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

from . import lora, ota, fhss, crc
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
        # a 20 MHz Wi-Fi channel needs a wider window than the band default;
        # default to 8 Msps (USB2-sustainable) unless overridden.
        if not srate_set:
            args.srate = 8e6
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

    r.tune(args.freq, args.srate, args.gain, args.antenna)
    bandnote = f" (band {args.band})" if getattr(args, "band", None) else ""
    chnote = f" (ch {args.channel})" if getattr(args, "channel", None) else ""
    print(f"capturing {args.samples:,} IQ samples @ {args.freq/1e6:.1f} MHz, "
          f"{args.srate/1e3:.0f} ksps{chnote}{bandnote} -> {args.out}", flush=True)
    t0 = time.time()
    r.capture(args.samples, args.out)
    print(f"done in {time.time()-t0:.1f}s", flush=True)
    print(f"\nto tune the detectors on this capture:", flush=True)
    print(f"  elrs-sdr validate {args.out} --srate {args.srate:g}", flush=True)


# ----------------------------------------------------------------------------
# survey — airodump-style channel survey (dwell N seconds on each channel)
# ----------------------------------------------------------------------------
def cmd_survey(args):
    _need_uhd()
    from .dronescan import (DroneScanner, wifi_scan_channels, BANDS)
    band = args.band
    # pick the sample rate for the band (8 Msps for 2.4/5 GHz Wi-Fi width)
    if band == "2.4":
        srate = 8e6
        channels = list(range(1, 12))   # ch 1-11 (the 2.4 GHz set)
    elif band == "5.8":
        srate = 8e6
        channels = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112,
                    116, 120, 124, 128, 132, 136, 140, 149, 153, 157, 161, 165]
    else:
        b = next((b for b in BANDS if b.name == band), None)
        srate = b.srate if b else 4e6
        channels = wifi_scan_channels(band) if band in ("2.4", "5.8") else []
    r = _open_radio(args)
    scanner = DroneScanner(r, dwell_s=0.05, gain=args.gain)
    total = len(channels) * args.dwell
    print(f"surveying {len(channels)} channels on {band} "
          f"({args.dwell}s dwell each, ~{total:.0f}s total)", flush=True)
    print(f"{'ch':>3}  {'freq(MHz)':>9}  {'peak(dB)':>8}  {'mean(dB)':>8}  "
          f"{'occupancy':>9}  signal", flush=True)
    print("-" * 60, flush=True)
    results = scanner.wifi_scan(band=band, srate=srate, channels=channels,
                                dwell_s=args.dwell, verbose=False)
    for res in results:
        # label: a steady AP has high occupancy; impulsive/DC artifact low
        if res["occupancy"] > 0.7 and res["peak_db"] > 15:
            sig = "** STEADY SIGNAL (likely a real AP/link) **"
        elif res["occupancy"] > 0.3:
            sig = "intermittent"
        elif res["peak_db"] > 15:
            sig = "impulsive (likely DC artifact / noise)"
        else:
            sig = "quiet"
        bar = "#" * int(res["occupancy"] * 25)
        print(f"{res['channel']:>3}  {res['freq_mhz']:>9.1f}  "
              f"{res['peak_db']:>8.1f}  {res['mean_db']:>8.1f}  "
              f"{res['occupancy']*100:>7.0f}%   {bar:<25} {sig}", flush=True)
    print("-" * 60, flush=True)
    # recommendation: the channel with the highest occupancy+peak is the target
    best = results[0]
    print(f"\nbest channel: {best['channel']} ({best['freq_mhz']:.1f} MHz) "
          f"occ={best['occupancy']*100:.0f}% peak={best['peak_db']:.1f} dB", flush=True)
    if best["occupancy"] > 0.3 and best["peak_db"] > 15:
        print(f"-> to capture it:  elrs-sdr capture --channel {best['channel']}",
              flush=True)
    else:
        print("-> no steady signal found on any channel. Make sure a 2.4 GHz", flush=True)
        print("   Wi-Fi AP / drone link is in range, then re-survey.", flush=True)


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
def cmd_webui(args):
    from . import webui
    webui.serve(args.host, args.port, block=True)


# ----------------------------------------------------------------------------
# validate — run the positive detectors on a captured IQ file (offline)
# ----------------------------------------------------------------------------
def cmd_validate(args):
    """Run the positive-identification detectors on a captured IQ file.

    This is the tuning loop for the UNCONFIRMED detectors: capture a real
    signal of a known protocol (with `capture --band`), then run this to see
    what each detector reports on the real data. A correct capture of protocol
    X should show X's detector high and the others low — that's how the
    synthetic-calibrated thresholds get confirmed.
    """
    from .radio import read_complex8
    from . import positivedet, dronescan as ds
    samples = read_complex8(args.file)
    srate = args.srate
    print(f"validating {len(samples):,} samples @ {srate/1e6:.3f} Msps "
          f"from {args.file}", flush=True)
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
    # --- L-SIG scan: try demod_lsig at many offsets to find a valid 802.11
    #     L-SIG anywhere in the capture. The front-end's Schmidl-Cox sync may
    #     miss the right offset on a real capture, so we brute-force offsets.
    #     This is the tuning tool for the POSITIVE 802.11 confirmation.
    from . import wifi
    lsig_hits = 0
    lsig_best = None
    step = max(1, int(srate * 0.1e-6))       # ~0.1 us offset granularity
    # scan a limited number of windows for speed (the L-SIG is 2 symbols)
    n_scan = min(2000, len(samples) // (2 * int(srate * 12.8e-6) + 64))
    for w in range(n_scan):
        off = w * step
        if off + 4 * 64 > len(samples):
            break
        ok, detail = wifi.demod_lsig(samples[off:off + 4 * 64 + 2048], srate)
        if ok:
            lsig_hits += 1
            if lsig_best is None:
                lsig_best = (off, detail)
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

    sp = sub.add_parser("survey",
                        help="airodump-style channel survey (dwell N s on each "
                             "channel, report occupancy/peak per channel)")
    sp.add_argument("--band", default="2.4",
                    choices=["2.4", "5.8"],
                    help="which band to survey (default 2.4 = ch 1-11)")
    sp.add_argument("--dwell", type=float, default=5.0,
                    help="seconds to dwell on each channel (default 5.0; "
                         "11 channels x 5s = 55s total)")
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
                    help="the sample rate the capture was taken at (default 4 Msps)")
    sp.set_defaults(fn=cmd_validate)

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
                        help="live multi-band drone scanner dashboard")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8090)
    sp.set_defaults(fn=cmd_webui)

    return p


def main(argv=None):
    parser = build_parser()
    # detect whether the user explicitly set --srate / --samples (so the
    # per-band sensible defaults don't clobber an explicit choice)
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    args = parser.parse_args(argv)
    args._srate_set = ("--srate" in argv)
    args._argv = argv
    args.fn(args)


if __name__ == "__main__":
    main()
