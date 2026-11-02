"""
elrs_sdr command-line interface.

Subcommands:
  info       — probe the attached UHD device (gains, antennas, sample rates)
  scan       — sweep the 2.4 GHz band, report where energy is (find ELRS hops)
  capture    — capture IQ to a .complex8 file for later decoding
  monitor    — live decode loop: capture -> LoRa demod -> CRC -> JSON lines
  decode     — decode a previously captured .complex8 file offline
  bind-capture — capture a TX in bind mode to recover the link UID (on-ramp)
  tx         — (FUTURE, gated) transmit a constructed ELRS frame

TX is behind an explicit --i-am-authorized flag because transmitting on drone
control bands is the legally/ethically sensitive half of this tool.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np

from .core import lora, ota, fhss, crc
from .radio import RadioDevice, UHDNotInstalled, read_complex8


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
_uhd = None


def _need_uhd():
    """Lazy-load UHD through the shared bootstrap (path scan + self-diagnosing
    error). Exits with code 2 and a useful message if UHD is genuinely absent."""
    global _uhd
    if _uhd is not None:
        return
    from .uhd_bootstrap import load_uhd, UHDNotInstalled
    try:
        _uhd = load_uhd()
    except UHDNotInstalled as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)


def _open_radio(args) -> RadioDevice:
    return RadioDevice(args.device, rx_channel=args.rx_chan, tx_channel=args.tx_chan)


def _resolve_gain(args, r: RadioDevice, freq_hz: float, srate: float) -> float:
    """Return the gain to use: the user's --gain if explicitly set, otherwise
    auto-pick a sensible one (so the user never has to type --gain)."""
    if getattr(args, "_gain_set", False):
        return args.gain
    g = r.auto_gain(freq_hz, srate, getattr(args, "antenna", None))
    print(f"  (auto-gain: {g:.0f} dB)", flush=True)
    return g


def _describe_rx_rates(d, chan) -> str:
    """Describe the RX sample-rate range across bindings, without crashing.

    The shape of the returned object differs a LOT by binding:
      * 4.2+ `get_rx_sample_rates()` -> range object with get_min_rate()/get_max_rate()
      * 4.9/4.11 `get_rx_rates()`    -> a `meta_range_t` whose accessor family is
        EITHER get_min_num()/get_max_num()  OR  get_low()/get_high() (and
        get_step()/get_step_num()). Try every known family — guessing one and
        missing the others is exactly the bug that produced the first "n/a".
    Never call len()/min()/max() on the object: a meta_range_t has no __len__.
    """
    # Preferred: get_rx_sample_rates() -> range object with *_rate() methods
    if hasattr(d, "get_rx_sample_rates"):
        try:
            sr = d.get_rx_sample_rates(chan)
            return (f"{sr.get_min_rate()/1e6:.3f} - {sr.get_max_rate()/1e6:.3f} "
                    f"Msps, step {sr.get_step_rate()/1e6:.4f} Msps")
        except Exception:
            pass

    if hasattr(d, "get_rx_rates"):
        try:
            r = d.get_rx_rates(chan)
        except Exception as e:
            return f"(n/a: {e})"
        # list/tuple of supported rates
        if isinstance(r, (list, tuple)):
            if r:
                return (f"{len(r)} supported; min {min(r)/1e6:.3f} - "
                        f"max {max(r)/1e6:.3f} Msps")
            return "(none)"
        # meta_range_t: robust, name-agnostic. Call every no-arg get_*() method
        # that returns a number. The range endpoints are the min/max — but the
        # *step* also comes back as a (tiny) number and must be EXCLUDED from the
        # min/max, otherwise min() wrongly picks the step as the low end.
        #
        # LIVE-CONFIRMED on the operator's 4.11 board: the meta_range_t exposes
        # start() / stop() / step() (NOT get_*). So also probe start/stop/step
        # explicitly — that is the actual API here.
        vals, step_val = [], None
        for attr in dir(r):
            if not (attr.startswith("get_") or attr in ("start", "stop", "step")):
                continue
            c = getattr(r, attr, None)
            if not callable(c):
                continue
            try:
                v = c()
            except Exception:
                continue
            if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
                continue
            if attr.lower().startswith("get_step") or attr.lower().startswith("get_inc") \
               or attr in ("step",):
                step_val = float(v)
                continue
            vals.append(float(v))
        if len(vals) >= 2:
            lo, hi = min(vals), max(vals)
            if step_val is not None:
                return f"{lo/1e6:.3f} - {hi/1e6:.3f} Msps, step {step_val/1e6:.4f} Msps"
            return f"{lo/1e6:.3f} - {hi/1e6:.3f} Msps"
        if len(vals) == 1:
            return f"{vals[0]/1e6:.3f} Msps (single reported rate)"
        # Fallback: the object may be ITERABLE (yields rates) or INDEXABLE,
        # even with no numeric get_* methods. Try to collect numbers from it.
        try:
            collected = []
            for item in r:                       # iterable
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    collected.append(float(item))
            if collected:
                return (f"{len(collected)} supported; "
                        f"min {min(collected)/1e6:.3f} - max {max(collected)/1e6:.3f} Msps")
        except TypeError:
            pass
        # indexable probe: r[0], r[1], ... until it stops
        try:
            collected = []
            i = 0
            while i < 4096:
                try:
                    item = r[i]
                except Exception:
                    break
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    collected.append(float(item))
                i += 1
            if collected:
                return (f"{len(collected)} supported; "
                        f"min {min(collected)/1e6:.3f} - max {max(collected)/1e6:.3f} Msps")
        except Exception:
            pass
        # last resort: its repr (still better than a bare "n/a")
        try:
            return f"(unrecognized range object: {r!r})"
        except Exception:
            return "(n/a)"


def _print_json(obj):
    print(json.dumps(obj, separators=(",", ":")), flush=True)


# ----------------------------------------------------------------------------
# info
# ----------------------------------------------------------------------------
def cmd_info(args):
    _need_uhd()
    r = _open_radio(args)
    d = r.device
    print(f"device args : {args.device}")
    # firmware version: 4.2+ get_firmware_version(); 4.9/4.11 get_firmware_revision()
    for m in ("get_firmware_version", "get_firmware_revision"):
        if hasattr(d, m):
            try:
                print(f"firmware    : {getattr(d, m)()}")
                break
            except Exception as e:
                print(f"firmware    : (n/a: {e})")
    try:
        print(f"hw revision : {d.get_mboard_firmware_versions()}")
    except Exception:
        pass
    print(f"RX gain range : {r.get_gain_range()}")
    print(f"RX antennas   : {r.get_antennas('rx')}")
    try:
        print(f"TX antennas   : {r.get_antennas('tx')}")
    except Exception as e:
        print(f"TX antennas   : (n/a: {e})")
    # sample-rate range: 4.2+ get_rx_sample_rates(); 4.9/4.11 may return a
    # meta_range_t (use its own min/max methods) or a list — handle both.
    print("RX sample rate:", _describe_rx_rates(d, args.rx_chan))


# ----------------------------------------------------------------------------
# scan
# ----------------------------------------------------------------------------
def cmd_scan(args):
    _need_uhd()
    r = _open_radio(args)
    # coarse sweep across the 2.4 GHz ELRS band
    n = args.sweep
    f0 = 2.400e9
    f1 = 2.480e9
    step = (f1 - f0) / n
    r.tune(f0, args.srate, args.gain, args.antenna)
    print(f"sweeping {f0/1e9:.3f}-{f1/1e9:.3f} GHz in {n} steps "
          f"@ {args.srate/1e3:.0f} ksps, {args.dwell_ms} ms dwell", flush=True)
    results = []
    stream = r._open_rx_stream()
    buf = np.zeros(8192, dtype=np.complex64)
    meta = r._make_rx_metadata()
    first = True
    for i in range(n):
        f = f0 + step * i
        r.set_freq(f)
        time.sleep(args.dwell_ms / 1000.0)
        # grab a burst and measure power
        cnt = r._recv(stream, buf, 0.2, meta)
        if first:
            first = False
            if cnt == 0:
                # Self-diagnose a dead first recv. LIVE-CONFIRMED 4.11 metadata
                # attrs (from dir()): 'error_code' and 'strerror'. Probe those
                # first, then the 4.9 spellings.
                code = warn = "?"
                for m in ("error_code", "strerror", "get_error_code",
                          "get_error_string", "get_code"):
                    try:
                        v = getattr(meta, m)()
                        if v is not None and str(v) != "ERROR_CODE_TIMEOUT":
                            code = str(v); break
                    except Exception:
                        try:
                            v = getattr(meta, m)   # may be a property
                            if v is not None:
                                code = str(v); break
                        except Exception:
                            pass
                        continue
                for m in ("strerror", "get_error_code_warn", "get_warn", "warn"):
                    try:
                        v = getattr(meta, m)()
                        if v is not None:
                            warn = str(v); break
                    except Exception:
                        continue
                print(f"  !! first recv returned 0 samples "
                      f"(error_code={code}, strerror={warn})")
                print(f"     -> RX chain is not delivering. Check: (a) 2.4 GHz")
                print(f"        antenna on the RF port, (b) stream started (see")
                print(f"        the [ok]/[warn] _start_stream line above).")
                print(f"        Continuing sweep, but expect flat -999 until fixed.")
        if cnt:
            pwr = float(np.mean(np.abs(buf[:cnt]) ** 2))
            dbmish = 10 * np.log10(pwr + 1e-12)
        else:
            dbmish = -999.0
        results.append((f, dbmish))
        print(f"  {f/1e9:.4f} GHz  {dbmish:7.1f} dBref", flush=True)
    results.sort(key=lambda x: -x[1])
    print("\ntop 5 peaks:")
    for f, p in results[:5]:
        print(f"  {f/1e9:.4f} GHz  {p:7.1f} dBref")


# ----------------------------------------------------------------------------
# _identify_carrier — run the positive detectors on a captured buffer and
# return the strongest positive ID (or None). Used by `survey`.
# ----------------------------------------------------------------------------
def _identify_carrier(buf, srate) -> str | None:
    """Run the positive detectors on a captured buffer; return the strongest
    positive ID (or None if nothing claims it)."""
    from . import positivedet
    from .dronescan import DroneScanner as DS
    buf = np.asarray(buf)
    if len(buf) < 4096:
        return None
    candidates = []
    lora_s, lora_m = positivedet.lora_chirp(buf, srate)
    if lora_s >= DS._LORA_CLAIM:
        candidates.append((lora_s, "LoRa (ELRS/Meshtastic/sub-1G)"))
    ofdm_s, ofdm_m = positivedet.ofdm_preamble(buf, srate)
    if ofdm_s >= DS._OFDM_CLAIM:
        candidates.append((ofdm_s, "OFDM / 802.11 (Wi-Fi)"))
    video_s, video_m = positivedet.analog_video(buf, srate)
    if video_s >= DS._VIDEO_CLAIM:
        candidates.append((video_s, "Analog FPV video"))
    ocus_s, ocus_m = positivedet.ocusync_phy(buf, srate)
    if ocus_s >= DS._OCUSYNC_CLAIM:
        candidates.append((ocus_s, "OcuSync (proprietary OFDM)"))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


# ----------------------------------------------------------------------------
# capture
# ----------------------------------------------------------------------------
def cmd_capture(args):
    _need_uhd()
    from .dronescan import (band_by_name, wifi_channel_freq, wifi_scan_channels,
                            DroneScanner, BANDS)
    # --- resolve band, freq, srate, samples with SENSIBLE PER-BAND DEFAULTS ---
    # The user should NOT have to type --srate/--samples. The program picks
    # them from the band (or channel -> band). Explicit flags still override.
    scan_first = getattr(args, "scan", False)
    srate_set = getattr(args, "_srate_set", False)
    samples_set = ("--samples" in (getattr(args, "_argv", []) or []))

    # determine the target band (channel 1-14 -> 2.4, 36+ -> 5.8/5ghz, or --band)
    band_name = getattr(args, "band", None)
    if getattr(args, "channel", None):
        ch = int(args.channel)
        args.freq = wifi_channel_freq(ch)
        band_name = "2.4" if 1 <= ch <= 14 else "5.8"
        # A 20 MHz Wi-Fi channel needs a window that covers the WHOLE channel
        # (center +/-10 MHz) — 8 Msps only sees 8 MHz and misses the outer
        # half of the beacon's bandwidth. Default to 20 Msps (sustainable over
        # USB 2.0 with 32-bit I/Q) unless overridden.
        if not srate_set:
            args.srate = 20e6
    elif band_name:
        b = band_by_name(band_name)
        args.freq = (b.lo_hz + b.hi_hz) / 2
        if not srate_set:
            args.srate = b.srate
    else:
        # raw --freq: pick the band that contains it (for a sensible default rate)
        for b in BANDS:
            if b.lo_hz - 1e6 <= args.freq <= b.hi_hz + 1e6:
                if not srate_set:
                    args.srate = b.srate
                break
        else:
            if not srate_set:
                args.srate = 4e6   # safe default outside any known band
    band_name = getattr(args, "band", None) or (
        "2.4" if (1 <= args.freq < 2.5e9) else
        "5.8" if (5.1e9 <= args.freq < 6.0e9) else None)

    # sensible default sample count: ~3 seconds at the chosen rate (enough to
    # catch a periodic beacon, short enough to be quick). Override with --samples.
    if not samples_set:
        args.samples = int(round(args.srate * 3.0))

    r = _open_radio(args)

    # --- optional: scan first, find the strongest channel, capture on it ---
    if scan_first:
        band = "2.4" if (getattr(args, "channel", None) and
                         1 <= int(args.channel) <= 14) else (
                 "5.8" if (getattr(args, "channel", None) and
                           int(args.channel) >= 36) else "2.4")
        scanner = DroneScanner(r, dwell_s=0.05, gain=args.gain)
        chans = (wifi_scan_channels(band) if not getattr(args, "channel", None)
                 else [int(args.channel)])
        print(f"scanning {len(chans)} channel(s) on {band}...", flush=True)
        results = scanner.wifi_scan(band=band, srate=args.srate, channels=chans)
        print("\nchannel  freq(MHz)  peak(dB)  occupancy", flush=True)
        for res in results:
            marker = "  <-- strongest" if res is results[0] else ""
            print(f"  {res['channel']:>4}   {res['freq_mhz']:>8.1f}  "
                  f"{res['peak_db']:>7.1f}  {res['occupancy']*100:>5.0f}%{marker}",
                  flush=True)
        # capture on the strongest channel
        best = results[0]
        args.freq = best["freq_hz"]
        r.tune(args.freq, args.srate, args.gain, args.antenna)
        print(f"\ncapturing on strongest channel {best['channel']} "
              f"({best['freq_mhz']:.1f} MHz)", flush=True)

    # resolve gain (auto if the user didn't set --gain)
    gain = _resolve_gain(args, r, args.freq, args.srate)
    r.tune(args.freq, args.srate, gain, args.antenna)
    bandnote = f" (band {args.band})" if getattr(args, "band", None) else ""
    chnote = f" (ch {args.channel})" if getattr(args, "channel", None) else ""
    print(f"capturing {args.samples:,} IQ samples @ {args.freq/1e6:.1f} MHz, "
          f"{args.srate/1e3:.0f} ksps, gain={gain:.0f} dB{chnote}{bandnote} -> {args.out}",
          flush=True)
    t0 = time.time()
    r.capture(args.samples, args.out)
    print(f"done in {time.time()-t0:.1f}s", flush=True)
    print(f"\nto run the detectors on this capture:", flush=True)
    print(f"  elrs-sdr validate {args.out}", flush=True)
    print(f"  (the measured sample rate is saved in {args.out}.meta.txt and", flush=True)
    print(f"   auto-read by validate — no need to type --srate)", flush=True)


# ----------------------------------------------------------------------------
# spectrum — quick RF diagnostic: dump the real power spectrum on a channel,
# check DC offset + occupancy. Tells us if the B205 is even receiving RF.
# ----------------------------------------------------------------------------
def cmd_spectrum(args):
    _need_uhd()
    import numpy as np
    from .dronescan import power_spectrum, wifi_channel_freq
    from .radio import RadioDevice
    ch = getattr(args, "channel", None)
    if ch is not None:
        freq = wifi_channel_freq(ch)
        chnote = f" (ch {ch})"
    else:
        freq = args.freq
        chnote = ""
        if freq is None:
            print("give a --channel (e.g. 6 or 149) or --freq in Hz")
            return
    srate = args.srate
    r = _open_radio(args)
    gain = _resolve_gain(args, r, freq, srate)
    r.tune(freq, srate, gain, args.antenna)
    r.recv_block  # settle
    # capture a 0.5s snippet
    n = int(srate * 0.5)
    buf = np.zeros(8192, dtype=np.complex64)
    chunks = []
    import time as _t
    t0 = _t.time()
    while _t.time() - t0 < 0.5:
        if r.recv_block(buf, 0.02):
            chunks.append(buf.copy())
    iq = np.concatenate(chunks) if chunks else buf
    iq = iq.astype(np.complex128) - iq.mean()   # DC remove (B205 LO image)
    print(f"\nspectrum @ {freq/1e6:.1f} MHz{chnote}, {srate/1e6:.1f} Msps "
          f"({len(iq):,} samples = {len(iq)/srate*1000:.0f} ms), gain={gain:.0f} dB "
          f"[DC-removed]")
    # DC offset (a large DC offset = receiver problem / saturated)
    dc = float(np.mean(np.abs(iq)))
    dc_db = 20 * np.log10(abs(dc) + 1e-12)
    ac_db = 20 * np.log10(float(np.std(iq)) + 1e-12)
    print(f"  DC offset (post-removal): {dc_db:.1f} dB  "
          f"[should be < -40 dB after removal]")
    print(f"  AC (signal+noise) std: {ac_db:.1f} dB")
    # power spectrum (average over several blocks for stability)
    f, db = power_spectrum(iq[:min(len(iq), 8192*64)], srate)
    floor = float(np.percentile(db, 20))
    peak_idx = int(np.argmax(db))
    peak = float(db[peak_idx] - floor)
    # AP-band mean power (the +/-8 MHz channel window around center)
    inband = (f >= -8e6) & (f <= 8e6)
    band_db = float(db[inband].mean() - floor)
    pres = db > (floor + 8)
    occ = float(pres.mean())
    print(f"  noise floor (20th pct): {floor:.1f} dB")
    print(f"  AP-band mean (±8 MHz): {band_db:.1f} dB above floor  "
          f"[a real 802.11 AP is typically +8..+20 dB here]")
    print(f"  peak: {peak:.1f} dB above floor at {f[peak_idx]/1e6:+.2f} MHz offset")
    print(f"  occupancy (above floor+8 dB): {occ*100:.1f}%")
    # top 8 peaks
    order = np.argsort(db)[::-1][:8]
    print("  top spectral peaks (offset from center, dB above floor):")
    for k in sorted(order):
        print(f"    {f[k]/1e6:+7.2f} MHz   {db[k]-floor:6.1f} dB")
    if band_db > 12:
        print(f"\n  -> AP-BAND SIGNAL PRESENT (band mean {band_db:.0f} dB above floor).")
        print(f"     This is a real carrier on this channel — survey/capture it")
        print(f"     and the 802.11 detector will try to decode the beacon MAC.")
    elif peak < 8:
        print("\n  -> NO clear carrier. Either no signal in range, or the B205")
        print("     isn't receiving well (try lower gain, check antenna).")
    else:
        print(f"\n  -> Peak present ({peak:.0f} dB) but AP-band is quiet "
              f"({band_db:.1f} dB) — likely an impulsive artifact (DC spike /")
        print("     narrow interferer), not a steady AP.")


# ----------------------------------------------------------------------------
# survey — airodump-style: scan a single channel, a comma-separated list of
# channels, or a whole band. Shows the spectrum + IDENTIFIES each carrier.
# This is the core "scan the spectrum and identify what's there" workflow.
# ----------------------------------------------------------------------------
def _band_channels(band: str) -> list[int]:
    """Channel list for a band name."""
    if band == "2.4":
        return list(range(1, 12))
    if band == "5.8":
        return [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112,
                116, 120, 124, 128, 132, 136, 140,
                149, 153, 157, 161, 165, 169, 173, 177]
    return [1]   # sub1g/433/1.2: not Wi-Fi-channeled; single slot


def _parse_channel_spec(spec: str) -> list[int]:
    """Parse a channel spec: a single number, a comma list, a range (a-b),
    or a band name (2.4/5.8/sub1g/433/1.2)."""
    spec = spec.strip()
    if spec in ("2.4", "5.8", "sub1g", "433", "1.2"):
        return _band_channels(spec)
    out = []
    for p in (x.strip() for x in spec.split(",")):
        if not p:
            continue
        if "-" in p:
            a, b = p.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(p))
    return out


def cmd_survey(args):
    _need_uhd()
    import numpy as np
    import time as _t
    from .dronescan import (DroneScanner, power_spectrum, wifi_channel_freq, BANDS)
    from . import positivedet

    # channel spec: --channel (single/comma/range/band) or --band (fallback)
    spec = getattr(args, "channel", None) or getattr(args, "band", "2.4")
    channels = _parse_channel_spec(spec)
    if not channels:
        print(f"no channels to survey (spec {spec!r})"); return
    # band name (for the scanner) + sample rate
    is_wifi = any((1 <= c <= 14) or (36 <= c <= 177) for c in channels)
    band_name = ("2.4" if any(1 <= c <= 14 for c in channels)
                 else "5.8") if is_wifi else getattr(args, "band", "2.4")
    srate = 8e6 if is_wifi else 4e6
    if getattr(args, "_srate_set", False):
        srate = args.srate

    def ch_freq(ch):
        try:
            return wifi_channel_freq(ch)
        except ValueError:
            b = next((b for b in BANDS if b.name == band_name), None)
            return (b.lo_hz + b.hi_hz) / 2.0 if b else 2.44e9

    freqs = [ch_freq(c) for c in channels]
    r = _open_radio(args)
    gain = _resolve_gain(args, r, freqs[0], srate)
    scanner = DroneScanner(r, dwell_s=0.05, gain=gain)
    total = len(channels) * args.dwell
    print(f"\nsurveying {len(channels)} channel(s) on {band_name} "
          f"({args.dwell}s dwell each, ~{total:.0f}s total, gain={gain:.0f} dB, "
          f"{srate/1e6:.1f} Msps)\n", flush=True)

    # per-channel dwell scan (occupancy/peak) via the proven scanner
    try:
        results = scanner.wifi_scan(band=band_name, srate=srate, channels=channels,
                                    dwell_s=args.dwell, verbose=False)
    except Exception:
        results = []
    res_by_ch = {r_["channel"]: r_ for r_ in results}

    print(f"{'ch':>4}  {'freq(MHz)':>9}  {'APocc':>6}  {'peak':>6}  signal / identification")
    print("-" * 78, flush=True)
    stream = r._open_rx_stream()
    buf = np.zeros(8192, dtype=np.complex64)
    meta = r._make_rx_metadata()
    for ch, f in zip(channels, freqs):
        r.tune(f, srate, gain, args.antenna)
        _t.sleep(0.05)
        # ---- accumulate blocks over the full dwell: an AP beacon is
        # PERSISTENT (every ~100 ms) and spread across the 20 MHz channel;
        # the B205 DC artifact is a few narrow bins and bursty. Per-block
        # AP-band occupancy over the dwell is the discriminator. ----
        t_end = _t.monotonic() + args.dwell
        blocks = 0
        ap_active_blocks = 0
        peak_db = 0.0
        id_blocks = []
        while _t.monotonic() < t_end:
            if not r._recv(stream, buf, 0.02, meta):
                continue
            n = int(buf.size)
            if n == 0:
                continue
            # DC removal (B205 has no IF filter; the DC/LO image at ~0.5 MHz
            # from center would otherwise dominate any single-block metric)
            iq = buf[:n].astype(np.complex128)
            iq = iq - iq.mean()
            f_arr, db = power_spectrum(iq, srate)
            floor = float(np.percentile(db, 20))
            inband = (f_arr >= -10e6) & (f_arr <= 10e6)
            band_db = float(db[inband].mean() - floor)
            peak_db = max(peak_db, float(db.max() - floor))
            blocks += 1
            if band_db > 6.0:
                ap_active_blocks += 1
                if len(id_blocks) < 4:
                    id_blocks.append(iq.astype(np.complex64))
        ap_occ = (ap_active_blocks / blocks) if blocks else 0.0
        # identification on the strongest DC-removed block (if any)
        ident = None
        if id_blocks:
            best_blk = max(id_blocks, key=lambda b: float(np.mean(np.abs(b) ** 2)))
            ident = _identify_carrier(best_blk, srate)
        if ap_occ > 0.5:
            sig = "** AP-BAND SIGNAL ** " + (ident or "(802.11 not yet decoded)")
        elif ap_occ > 0.15:
            sig = "intermittent AP-band carrier " + (ident or "")
        elif peak_db > 15:
            sig = "impulsive (DC artifact/noise)"
        else:
            sig = "quiet"
        bar = "#" * int(ap_occ * 20)
        print(f"{ch:>4}  {f/1e6:>9.1f}  {ap_occ*100:>5.0f}%  {peak_db:>5.1f}  "
              f"{bar:<20} {sig}", flush=True)
    print("-" * 78, flush=True)
    best = max(channels, key=lambda c: (res_by_ch.get(c, {}).get("occupancy", 0),
                                        res_by_ch.get(c, {}).get("peak_db", 0)))
    bres = res_by_ch.get(best, {})
    print(f"\nbest channel: {best} ({ch_freq(best)/1e6:.1f} MHz) "
          f"occ={bres.get('occupancy',0)*100:.0f}% peak={bres.get('peak_db',0):.1f} dB",
          flush=True)
    if bres.get("occupancy", 0) > 0.3 and bres.get("peak_db", 0) > 15:
        print(f"-> to capture it:  elrs-sdr capture --channel {best}", flush=True)
    else:
        print("-> no steady signal found. Make sure a transmitter is in range.",
              flush=True)


# ----------------------------------------------------------------------------
# decode (offline, from a .complex8 file)
# ----------------------------------------------------------------------------
def cmd_decode(args):
    samples = read_complex8(args.file)
    print(f"loaded {len(samples):,} samples from {args.file}", flush=True)
    _decode_samples(samples, args)


def _decode_samples(samples, args, label=""):
    """Run the RX pipeline over an IQ array and emit JSON lines."""
    rate = lora.ELRS_2G4_RATES[args.rate]
    params = lora.LoRaParams(sf=rate.sf, bw_hz=rate.bw_hz,
                             sample_rate=args.srate)
    # detect candidate frame starts (both IQ polarities)
    starts = []
    for inv in (False, True):
        p = lora.LoRaParams(sf=rate.sf, bw_hz=rate.bw_hz,
                            sample_rate=args.srate, invert_iq=inv)
        starts += [(s, inv) for s in lora.detect_sync_word(samples, p)]
    # dedupe close starts
    starts.sort()
    deduped = []
    for s, inv in starts:
        if not deduped or s - deduped[-1][0] > params.samples_per_symbol:
            deduped.append((s, inv))
    print(f"{label}{len(deduped)} candidate frame starts "
          f"(rate={args.rate}, SF{rate.sf})", flush=True)

    decoded = 0
    for s, inv in deduped:
        p = lora.LoRaParams(sf=rate.sf, bw_hz=rate.bw_hz,
                            sample_rate=args.srate, invert_iq=inv)
        raw = lora.demodulate_frame(samples, p, s, rate)
        if raw is None:
            continue
        pkt = _try_crc(raw, args)
        if pkt is not None:
            decoded += 1
            rec = pkt.to_dict()
            rec["sample"] = s
            rec["invert_iq"] = inv
            _print_json(rec)
    print(f"{label}decoded {decoded} CRC-valid packets", flush=True)


def _try_crc(raw: bytes, args) -> "ota.ElrsPacket | None":
    """
    Validate a raw frame against the link. Without a known UID we try:
      1. the UID supplied on the CLI (--uid), if given
      2. binding-mode init (OTA_VERSION_ID) — works for bind-capture files
    The nonce is scanned over 0..255 for non-sync packets since an offline
    decoder doesn't track the live nonce counter.
    """
    if args.uid:
        uid = bytes.fromhex(args.uid)
        init = ota.ota_uid_crc_init(uid)
    else:
        uid = None
        init = ota.bind_crc_init()

    is_full = len(raw) == ota.OTA8_PACKET_SIZE
    ptype = raw[0] & 0x3
    # scan nonce space; sync packets use nonce 0
    for nonce in ([0] if ptype == ota.PACKET_TYPE_SYNC else range(256)):
        pkt = ota.decode(raw, uid, init, nonce)
        if pkt is not None:
            return pkt
    return None


# ----------------------------------------------------------------------------
# monitor (live)
# ----------------------------------------------------------------------------
def cmd_monitor(args):
    _need_uhd()
    r = _open_radio(args)
    r.tune(args.freq, args.srate, args.gain, args.antenna)
    print(f"monitoring {args.freq/1e6:.1f} MHz @ {args.srate/1e3:.0f} ksps "
          f"(rate={args.rate}); Ctrl-C to stop", flush=True)
    rate = lora.ELRS_2G4_RATES[args.rate]
    chunk = 65536
    ring = bytearray()
    RING = 1 << 18
    try:
        for buf in r.rx_stream(chunk):
            ring.extend(buf.tobytes())
            if len(ring) > RING:
                del ring[:len(ring) - RING]
            arr = np.frombuffer(bytes(ring), dtype=np.complex64)
            # reuse the offline decoder over the rolling window
            _decode_samples(arr, args, label="[live] ")
    except KeyboardInterrupt:
        print("\nstopped", flush=True)


# ----------------------------------------------------------------------------
# bind-capture
# ----------------------------------------------------------------------------
def cmd_bind_capture(args):
    _need_uhd()
    r = _open_radio(args)
    # bind mode: 50 Hz rate, sync channel (ISM2G4 chan 40 = 2440 MHz),
    # InvertIQ. Tune to the sync channel and capture.
    dom = fhss.DOMAINS[fhss.DEFAULT_2G4_DOMAIN]
    f = dom.channel_freq(dom.sync_channel)
    r.tune(f, args.srate, args.gain, args.antenna)
    print(f"bind-capture @ {f/1e6:.1f} MHz (sync ch {dom.sync_channel}), "
          f"{args.samples:,} samples -> {args.out}", flush=True)
    r.capture(args.samples, args.out)
    print("\nNow decode with binding-mode init (default) to extract the UID:",
          flush=True)
    print(f"  elrs-sdr decode {args.out} --rate 50Hz", flush=True)


# ----------------------------------------------------------------------------
# tx (future, gated)
# ----------------------------------------------------------------------------
def cmd_tx(args):
    if not getattr(args, "i_am_authorized_to_transmit", False):
        print("refusing: TX is disabled by default. Re-run with "
              "--i-am-authorized-to-transmit only if you own the target link "
              "and are in a regulated/authorized context.", file=sys.stderr)
        sys.exit(3)
    _need_uhd()
    r = _open_radio(args)
    rate = lora.ELRS_2G4_RATES[args.rate]
    # build a frame (placeholder: sync packet)
    raw = ota.build_sync(is_full=rate.is_full, fhss_index=0, nonce=0,
                         rf_rate_enum=rate.sf, uid4=0, uid5=0)
    dom = fhss.DOMAINS[fhss.DEFAULT_2G4_DOMAIN]
    f = args.freq or dom.channel_freq(0)
    r.tx_tune(f, args.srate, args.gain, args.antenna)
    wf = lora.modulate_frame(rate, raw, args.srate)
    print(f"TX {len(wf)} samples @ {f/1e6:.1f} MHz (rate={rate.name})", flush=True)
    r.tx_stream(wf)
    print("transmit complete", flush=True)


# ----------------------------------------------------------------------------
# dronescan (CLI one-shot multi-band presence + protocol ID)
# ----------------------------------------------------------------------------
def cmd_dronescan(args):
    """One-shot multi-band drone presence + protocol scan (no web UI).

    Sweeps every band (or the --bands subset) for a few cycles and prints a
    presence + classification report. For the live version use `webui`.
    """
    _need_uhd()
    from . import dronescan as ds
    from . import webui
    webui._quiet_uhd()
    r = _open_radio(args)
    # which bands to scan
    wanted = None
    if getattr(args, "bands", None):
        wanted = [x.strip() for x in args.bands.split(",") if x.strip()]
    scanner = ds.DroneScanner(r, dwell_s=args.dwell_s, gain=args.gain)
    if wanted:
        # scan only the chosen bands by cycling through just them
        ds.CYCLE_ORDER[:] = [b for b in ds.CYCLE_ORDER if b in wanted]
    cycles = args.cycles
    band_names = ds.CYCLE_ORDER
    print(f"scanning {len(band_names)} band(s): {', '.join(band_names)} "
          f"for {cycles} cycles each (dwell {args.dwell_s*1000:.0f} ms)…",
          flush=True)
    try:
        for i in range(cycles):
            st = scanner.step()
            # light progress: per-cycle presence
            pres = [b["name"] for b in st["bands"] if b["present"]]
            print(f"  cycle {i+1:2d}: present -> {', '.join(pres) or '(none)'}",
                  flush=True)
    except KeyboardInterrupt:
        print("\nstopped early.", flush=True)
    # final report
    st = scanner.state_dict()
    print("\n" + "=" * 60)
    print("DRONE RF SCAN REPORT")
    print("=" * 60)
    print("SUMMARY:", st["summary"])
    print()
    for b in st["bands"]:
        flag = "ACTIVE" if b["present"] else "quiet "
        hop = " (hopping)" if b["hopping"] else ""
        print(f"[{flag}] {b['name']:6s} {b['lo_hz']/1e9:.3f}-{b['hi_hz']/1e9:.3f} GHz"
              f"  peak +{b['presence_db']:.1f} dB{hop}")
        for d in b["detections"][:3]:
            print(f"          {d['freq_hz']/1e6:8.1f} MHz  {d['protocol']}"
                  f"  [{d['confidence']}]  {d['note']}")
    print()
    print("confidence: high = strong signature match; medium = likely, verify; "
          "low = best-effort/weak.")


# ----------------------------------------------------------------------------
# webui (live dashboard)
# ----------------------------------------------------------------------------
def _build_sdr_source(args):
    """Open the radio + band scanner; return an SdrScanSource or None on failure.
    The SDR source is the presence+protocol layer (drones that don't broadcast
    Remote ID). Tolerant: a missing radio just means no live SDR rows."""
    from .fusion import SdrScanSource, get_logger
    log = get_logger(__name__)
    try:
        from .radio import RadioDevice
        from .dronescan import DroneScanner
    except Exception as e:
        log.warning("SDR source unavailable (import failed): %r", e)
        return None
    try:
        radio = RadioDevice(args.device)
    except Exception as e:
        log.warning("could not open radio %r: %r — skipping SDR source",
                    args.device, e)
        return None
    scanner = DroneScanner(radio, gain=args.gain)
    return SdrScanSource(scanner)


def cmd_webui(args):
    """Serve the unified drone-watch dashboard (all sources fused)."""
    import time
    from . import fusion, webui
    from .log import get_logger
    log = get_logger(__name__)

    table = fusion.TrackTable()
    sources = []

    if args.sdr:
        src = _build_sdr_source(args)
        if src is not None:
            sources.append(src)
    if args.dji:
        sources.append(fusion.DjiDroneIdSource(args.dji.split()))
    if args.replay or not sources:
        if not sources and not args.replay:
            log.warning("no live source available — falling back to --replay "
                        "(synthetic Remote ID). Use --sdr with a radio, or "
                        "--dji CMD, for live data.")
        sources.append(fusion.ReplaySource())

    fusion.start_sources(sources, table)
    httpd = webui.serve(table, host=args.host, port=args.port,
                        window_s=args.window)
    print(f"drone dashboard: http://{args.host}:{args.port}  "
          f"(sources: {', '.join(s.name for s in sources)})", flush=True)
    print("Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nshutting down.", flush=True)
        httpd.shutdown()


# ----------------------------------------------------------------------------
# validate — run the positive detectors on a captured IQ file (offline)
# ----------------------------------------------------------------------------
def cmd_spectra(args):
    """Averaged power spectrum of a whole capture. The single most useful
    diagnostic: does a REAL beacon exist in this file? A steady 802.11 AP
    shows as a broad, flat-ish hump across the ~20 MHz channel; the B205's
    DC/LO artifact shows as a few narrow spikes near center. Averages over
    the whole capture so a single beacon (every 100 ms) is clearly visible
    above the noise floor.
    """
    import numpy as np
    from .radio import read_complex8, read_capture_meta
    from .dronescan import power_spectrum
    samples = read_complex8(args.file)
    srate = args.srate
    if srate is None:
        meta = read_capture_meta(args.file)
        srate = float(meta.get("sample_rate_hz", 4e6))
    print(f"spectra: {len(samples):,} samples @ {srate/1e6:.3f} Msps "
          f"from {args.file}", flush=True)
    # ---- DC removal (B205 has no IF filter) ----
    # Remove the block-mean DC, then the long-term (slow) drift, so the
    # averaged spectrum isn't dominated by the DC/LO image.
    samples = samples.astype(np.complex128)
    samples = samples - samples.mean()
    # ---- averaged PSD: split into ~1 s chunks, FFT each, average the
    #     POWER (not the complex) to get a stable, low-noise spectrum.
    chunk = int(srate * 1.0)          # ~1 s per chunk
    chunk = max(1024, min(chunk, len(samples)))
    n_chunks = max(1, len(samples) // chunk)
    # raw-power averaging (correct, no dB round-trip)
    w = np.hanning(chunk)
    acc = None
    f = None
    for i in range(n_chunks):
        seg = samples[i*chunk:(i+1)*chunk] * w
        if len(seg) < 256:
            break
        spec = np.fft.fft(seg)
        half = len(spec)//2 + 1
        p = (np.abs(spec[:half])**2) / (n_chunks)
        if acc is None:
            acc = p
            f = np.fft.rfftfreq(len(seg), d=1.0/srate)
        else:
            acc += p
    ref = float(np.mean(acc) + 1e-12)
    db = 10.0*np.log10(acc + 1e-12) - 10.0*np.log10(ref + 1e-12)
    # ---- report ----
    floor = float(np.percentile(db, 20))
    # AP-band mean (the whole 20 MHz channel = full sample band at 20 Msps,
    # or +/-10 MHz if the srate is higher)
    band = 10e6
    inband = (f >= -band) & (f <= band)
    band_db = float(db[inband].mean() - floor) if inband.any() else 0.0
    peak_idx = int(np.argmax(db))
    peak_db = float(db[peak_idx] - floor)
    print("="*62)
    print(f"  noise floor (20th pct):   {floor:6.1f} dB")
    print(f"  AP-band mean (±10 MHz):   {band_db:6.1f} dB above floor")
    print(f"  overall peak:             {peak_db:6.1f} dB at {f[peak_idx]/1e6:+.2f} MHz")
    print(f"  -> a real 802.11 beacon is typically +8..+20 dB AP-band mean;")
    print(f"     a B205 DC artifact is a few narrow spikes, AP-band stays <5 dB")
    print("="*62)
    print("top spectral peaks (offset from center, dB above floor):")
    order = np.argsort(db)[::-1][:args.top]
    for k in sorted(order):
        print(f"    {f[k]/1e6:+7.2f} MHz   {db[k]-floor:6.1f} dB")
    if band_db > 8:
        print(f"\n  ** AP-BAND SIGNAL PRESENT (mean {band_db:.0f} dB above floor).")
        print(f"     A real carrier is in this capture — run `validate` to")
        print(f"     attempt the 802.11 L-SIG/MAC decode.")
    elif peak_db > 15:
        print(f"\n  -> Peak ({peak_db:.0f} dB) but AP-band quiet ({band_db:.1f} dB):")
        print(f"     likely the B205 DC/LO artifact or a narrow interferer,")
        print(f"     NOT a steady AP beacon.")
    else:
        print(f"\n  -> No clear signal in this capture (peak {peak_db:.0f} dB).")
        print(f"     The AP is too weak / out of range / not on this channel.")

def cmd_validate(args):
    """Run the positive-identification detectors on a captured IQ file.

    This is the tuning loop for the UNCONFIRMED detectors: capture a real
    signal of a known protocol (with `capture --band`), then run this to see
    what each detector reports on the real data. A correct capture of protocol
    X should show X's detector high and the others low — that's how the
    synthetic-calibrated thresholds get confirmed.
    """
    from .radio import read_complex8, read_capture_meta
    from . import positivedet, dronescan as ds
    samples = read_complex8(args.file)
    srate = args.srate
    # auto-read the sample rate from the .meta.txt sidecar (written by
    # `capture`) when the user didn't pass --srate explicitly — the B205 mini
    # delivers ~8 MS/s even when 20 Msps is configured, so the configured
    # rate is wrong and the measured one (in the meta) is authoritative.
    srate_explicit = "--srate" in (getattr(args, "_argv", []) or [])
    if not srate_explicit:
        meta = read_capture_meta(args.file)
        if meta.get("sample_rate_hz"):
            try:
                srate = float(meta["sample_rate_hz"])
            except ValueError:
                pass
    print(f"validating {len(samples):,} samples @ {srate/1e6:.3f} Msps "
          f"from {args.file}"
          f"{'  [srate from .meta.txt]' if not srate_explicit else ''}",
          flush=True)
    print("=" * 62)
    print("POSITIVE-IDENTIFICATION DETECTOR METRICS (real capture)")
    print("=" * 62)
    # run each detector on the full capture (and a few sub-windows for robustness)
    def run(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            return (0.0, f"error: {e}")
    lora_s, lora_m = run(positivedet.lora_chirp, samples, srate)
    ofdm_s, ofdm_m = run(positivedet.ofdm_preamble, samples, srate)
    video_s, video_m = run(positivedet.analog_video, samples, srate)
    ocus_s, ocus_m = run(positivedet.ocusync_phy, samples, srate)
    # FHSS needs a freq track: approximate from a few sub-windows' peaks
    from .dronescan import power_spectrum
    track = []
    nwin = min(16, len(samples) // 4096)
    for i in range(nwin):
        seg = samples[i * 4096:(i + 1) * 4096]
        if len(seg) < 4096:
            break
        freqs, db = power_spectrum(seg, srate)
        if len(freqs):
            floor = float(np.percentile(db, 20))
            pres = db > floor + 8.0
            if pres.any():
                idx = np.where(pres)[0]
                track.append(float(freqs[idx[int(np.argmax(db[idx]))]]))
    fhss_s, fhss_m = run(positivedet.fhss_hop, track)
    # --- L-SIG scan: find a valid 802.11 L-SIG ANYWHERE in the capture.
    #     Two-pass: (1) coarse scan the whole capture in 1-OFDM-symbol
    #     (12.8 us) steps to find candidate regions, (2) a fine 1-sample
    #     sweep around each candidate to nail the exact alignment.
    #     demod_lsig does its own L-STF matched-filter sync, so the window
    #     just needs to contain the full preamble+L-LTF+2xL-SIG (~5 symbols).
    from . import wifi
    sym = max(64, int(round(srate * 12.8e-6)))        # one OFDM symbol
    win = 8 * sym + 256                              # preamble margin + L-SIG
    lsig_hits = 0
    lsig_best = None
    # pass 1: coarse (1-symbol step) across the whole capture, capped for speed
    step1 = sym
    n_scan1 = min(4000, max(1, (len(samples) - win) // step1 + 1))
    candidates = []
    for w in range(n_scan1):
        off = w * step1
        if off + win > len(samples):
            break
        try:
            ok, detail = wifi.demod_lsig(samples[off:off + win], srate)
        except Exception:
            ok = False
        if ok:
            lsig_hits += 1
            if lsig_best is None:
                lsig_best = (off, detail)
            candidates.append(off)
            if len(candidates) >= 8:        # plenty; stop the coarse pass
                break
    # pass 2: fine 1-sample sweep around each candidate (if the coarse pass
    #     found a hit but the exact offset matters, refine).
    for c in candidates[:8]:
        lo = max(0, c - 4 * sym)
        hi = min(len(samples) - win, c + 4 * sym)
        for off in range(lo, hi + 1, 1):
            try:
                ok, detail = wifi.demod_lsig(samples[off:off + win], srate)
            except Exception:
                continue
            if ok:
                lsig_hits += 1
                if lsig_best is None:
                    lsig_best = (off, detail)
                break
    rows = [
        ("LoRa / ELRS  (CONFIRMED)",  lora_s,  lora_m,  ds.DroneScanner._LORA_CLAIM),
        ("OFDM / Wi-Fi (UNCONFIRMED)",ofdm_s,  ofdm_m,  ds.DroneScanner._OFDM_CLAIM),
        ("OcuSync      (UNCONFIRMED)",ocus_s,  ocus_m,  ds.DroneScanner._OCUSYNC_CLAIM),
        ("Analog video (UNCONFIRMED)",video_s, video_m, ds.DroneScanner._VIDEO_CLAIM),
        ("FHSS         (UNCONFIRMED)",fhss_s,  fhss_m,  ds.DroneScanner._FHSS_CLAIM),
    ]
    for name, score, metric, claim in rows:
        verdict = "FIRE" if score >= claim else "no"
        print(f"  {name:28s} score={score:.2f}  claim={claim:.2f}  "
              f"-> {verdict:4s}  ({metric})")
    print()
    print("802.11 L-SIG SCAN (the POSITIVE 802.11 confirmation):")
    if lsig_hits > 0:
        print(f"  ** {lsig_hits} VALID L-SIG decoded (first at offset {lsig_best[0]}: {lsig_best[1]}) **")
        print("  -> this capture IS 802.11. The OFDM detector can be flipped to")
        print("     confirmed once the front-end sync is tuned to land on the L-SIG.")
    else:
        print("  no valid L-SIG found. Either (a) not 802.11, (b) the front-end")
        print("  sync/channel-est needs tuning, or (c) the L-SIG interleaver/bit-")
        print("  order needs the real-capture calibration. Tune the front-end in")
        print("  wifi.demod_lsig against this capture until a valid L-SIG appears.")
    print()
    print("TUNING: for a known-protocol capture, that protocol's score should")
    print("be the highest and >= its claim. The 802.11 path is confirmed by a")
    print("VALID L-SIG (Rate/Length/Parity/Tail consistent), not just OFDM timing.")


# ----------------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="elrs-sdr",
                                description="Capture/decode (and later TX) "
                                            "ExpressLRS drone signals.")
    p.add_argument("--device", default="type=b200",
                   help="UHD device args (default type=b200 = auto B205/B210)")
    p.add_argument("--rx-chan", type=int, default=0)
    p.add_argument("--tx-chan", type=int, default=0)
    p.add_argument("--antenna", default=None, help="antenna name")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="debug logging (UHD binding-probe trail, etc.)")
    p.add_argument("--quiet", "-q", action="store_true", help="errors only")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp, need_freq=True):
        if need_freq:
            sp.add_argument("--freq", type=float, default=2.44e9,
                            help="center frequency Hz (default 2.44 GHz)")
        sp.add_argument("--srate", type=float, default=4e6,
                        help="IQ sample rate (default 4 Msps; >=4x LoRa BW)")
        sp.add_argument("--gain", type=float, default=40, help="RF gain dB")
        sp.add_argument("--rate", default="250Hz",
                        choices=sorted(lora.ELRS_2G4_RATES),
                        help="ELRS air rate (default 250Hz)")

    sp = sub.add_parser("info", help="probe the device"); sp.set_defaults(fn=cmd_info)

    sp = sub.add_parser("scan", help="sweep the 2.4 GHz band")
    sp.add_argument("--sweep", type=int, default=80, help="number of sweep steps")
    sp.add_argument("--dwell-ms", type=int, default=25, help="ms per step")
    sp.add_argument("--srate", type=float, default=2e6)
    sp.add_argument("--gain", type=float, default=30)
    sp.add_argument("--antenna", default=None)
    sp.set_defaults(fn=cmd_scan)

    sp = sub.add_parser("spectrum",
                        help="quick RF diagnostic: dump the power spectrum on a "
                             "channel (DC offset, peak, occupancy) — tells us if "
                             "the B205 is receiving RF")
    sp.add_argument("--channel", type=int, default=None,
                    help="Wi-Fi channel number (e.g. 6 or 149) — mapped to its "
                         "center frequency automatically. Preferred over --freq.")
    sp.add_argument("--freq", type=float, default=None,
                    help="center frequency Hz (only if --channel is not given)")
    sp.add_argument("--srate", type=float, default=20e6,
                    help="sample rate Hz (default 20 Msps — covers the full "
                         "20 MHz Wi-Fi channel; the B205 mini delivers ~8 "
                         "MS/s over USB 2.0, which is enough to see the "
                         "center of the channel)")
    sp.add_argument("--gain", type=float, default=40)
    sp.add_argument("--antenna", default=None)
    sp.set_defaults(fn=cmd_spectrum)

    sp = sub.add_parser("survey",
                        help="airodump-style: scan a channel, comma list, or "
                             "band; show the spectrum + IDENTIFY each carrier")
    sp.add_argument("--channel", default=None,
                    help="channel(s): a single number (48), a comma list "
                         "(48,149,157), a range (149-165), or a band name "
                         "(2.4 / 5.8). Overrides --band.")
    sp.add_argument("--band", default="2.4",
                    choices=["2.4", "5.8", "sub1g", "433", "1.2"],
                    help="band to survey (default 2.4 = ch 1-11)")
    sp.add_argument("--dwell", type=float, default=2.0,
                    help="seconds to dwell on each channel (default 2.0)")
    sp.add_argument("--srate", type=float, default=8e6)
    sp.add_argument("--gain", type=float, default=40)
    sp.add_argument("--antenna", default=None)
    sp.set_defaults(fn=cmd_survey)

    sp = sub.add_parser("capture", help="capture IQ to a .complex8 file")
    add_common(sp)
    sp.add_argument("--channel", type=int, default=None,
                    help="Wi-Fi channel NUMBER (airodump-style): 1-14 for "
                         "2.4 GHz, 36-177 for 5 GHz. Overrides --freq. e.g. "
                         "--channel 6 -> 2437 MHz")
    sp.add_argument("--scan", action="store_true",
                    help="sweep the band's channels first, report the strongest, "
                         "then capture on it (airodump-style 'find it first')")
    sp.add_argument("--band", default=None,
                    choices=["2.4", "5.8", "5ghz", "sub1g", "1.2", "433"],
                    help="capture at this band's center with its sample rate "
                         "(overrides --freq/--srate; --channel takes priority)")
    sp.add_argument("--samples", type=int, default=1 << 20,
                    help="number of IQ samples (default 1M)")
    sp.add_argument("--out", default="capture.complex8")
    sp.set_defaults(fn=cmd_capture)

    sp = sub.add_parser("validate",
                        help="run the positive detectors on a capture file "
                             "(tune the UNCONFIRMED detectors)")
    sp.add_argument("file", help="a .complex8 capture file")
    sp.add_argument("--srate", type=float, default=4e6,
                    help="the sample rate the capture was taken at (default 4 Msps; "
                         "auto-read from the .meta.txt sidecar if present)")
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("spectra",
                        help="AVERAGED power spectrum of a whole capture — shows "
                             "if a real beacon is present (a steady 20 MHz hump "
                             "vs. the B205 DC spike). The key diagnostic for "
                             "'is there a signal in this capture?'")
    sp.add_argument("file", help="a .complex8 capture file")
    sp.add_argument("--srate", type=float, default=None,
                    help="sample rate Hz (auto-read from .meta.txt if omitted)")
    sp.add_argument("--top", type=int, default=15,
                    help="number of top spectral peaks to print (default 15)")
    sp.set_defaults(fn=cmd_spectra)

    sp = sub.add_parser("decode", help="decode a .complex8 file offline")
    sp.add_argument("file")
    add_common(sp, need_freq=False)
    sp.add_argument("--uid", default=None,
                    help="link UID hex (12 chars) for CRC init; omit for "
                         "binding-mode init")
    sp.set_defaults(fn=cmd_decode)

    sp = sub.add_parser("monitor", help="live decode loop")
    add_common(sp)
    sp.set_defaults(fn=cmd_monitor)

    sp = sub.add_parser("bind-capture", help="capture a TX in bind mode")
    add_common(sp, need_freq=False)
    sp.add_argument("--samples", type=int, default=1 << 20)
    sp.add_argument("--out", default="bind.complex8")
    sp.set_defaults(fn=cmd_bind_capture)

    sp = sub.add_parser("tx", help="(future) transmit an ELRS frame [gated]")
    add_common(sp)
    sp.add_argument("--i-am-authorized-to-transmit",
                    action="store_true",
                    help="required to enable TX; confirm authorization")
    sp.set_defaults(fn=cmd_tx)

    sp = sub.add_parser("dronescan",
                        help="multi-band drone presence + protocol ID (one-shot)")
    sp.add_argument("--bands", default=None,
                    help="comma list of bands to scan: 2.4,5.8,sub1g,433 "
                         "(default: all)")
    sp.add_argument("--cycles", type=int, default=8,
                    help="observation cycles per band (default 8)")
    sp.add_argument("--dwell-s", type=float, default=0.15,
                    help="seconds per observation (default 0.15)")
    sp.add_argument("--gain", type=float, default=30, help="RF gain dB")
    sp.set_defaults(fn=cmd_dronescan)

    sp = sub.add_parser("webui",
                        help="unified drone dashboard (Remote ID + SDR scan + DJI, fused)")
    sp.add_argument("--replay", action="store_true",
                    help="show synthetic Remote ID drones (no hardware needed)")
    sp.add_argument("--sdr", action="store_true",
                    help="add the live SDR band/protocol-scan source")
    sp.add_argument("--dji", metavar="CMD", default=None,
                    help="add a DJI DroneID source: command emitting one JSON line/frame")
    sp.add_argument("--gain", type=float, default=30.0, help="RX gain (dB, for --sdr)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8090)
    sp.add_argument("--window", type=float, default=120.0,
                    help="initial display window in seconds (0 = all time)")
    sp.set_defaults(fn=cmd_webui)

    return p


def main(argv=None):
    parser = build_parser()
    # detect whether the user explicitly set --srate / --samples (so the
    # per-band sensible defaults don't clobber an explicit choice)
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    args = parser.parse_args(argv)
    # configure logging once (verbosity from the global -v/-q flags)
    from .log import setup as _log_setup
    _log_setup(verbose=args.verbose, quiet=args.quiet)
    args._srate_set = ("--srate" in argv)
    args._argv = argv
    args._gain_set = ("--gain" in argv)
    args.fn(args)


if __name__ == "__main__":
    main()
