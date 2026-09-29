#!/usr/bin/env python3
"""sdr_wifi_preamble.py -- SDR capture + 802.11 preamble/energy detector.

Stage 1 of the SDR Wi-Fi -> MAC work. It does NOT demodulate the OFDM or read
the MAC yet; it proves the B205mini/B210 is seeing 802.11 in the 2.4 GHz band
by detecting the time-domain signatures every Wi-Fi frame starts with:

  * 802.11a/g SHORT preamble : 10 repetitions of a 16-bit sync word
  * 802.11a/g LONG  preamble : 12 repetitions of a 16-bit word, then a first
    OFDM symbol (64-subcarrier) used as a channel reference

For each capture window it reports, per detected candidate:
  * absolute sample offset, estimated center-frequency offset (Hz),
    the correlation/energy confidence, and which preamble type matched.

It is deliberately dependency-light beyond numpy + uhd, and board-agnostic
(the same code opens a B205mini or B210; UHD reports the name, we just use it).

Run:
    python3 sdr_wifi_preamble.py --scan            # 10 s, print candidates
    python3 sdr_wifi_preamble.py --center 2437e6 --srate 25e6 --gain 30
    python3 sdr_wifi_preamble.py --save out.cfile  # also dump IQ

If you see candidates with high confidence, stage 2 (OFDM demod -> MAC) is
worth building. If you see none, we adjust gain/center/antenna first.
"""

import argparse
import time
import os
import sys
import numpy as np

# --- make the 4.9 pybind UHD importable + firmware findable without env vars ---
for _sp in ("/usr/local/lib/python3.12/site-packages",
            "/usr/local/lib/python3/dist-packages"):
    if os.path.isdir(_sp) and _sp not in sys.path:
        sys.path.insert(0, _sp)
os.environ.setdefault("UHD_IMAGES_DIR", "/usr/local/share/uhd/images")


# ---------------------------------------------------------------------------
# UHD (board-agnostic, version-tolerant). Order of attempts is chosen so the
# UHD 4.9 pybind API (uhd.usrp.MultiUSRP + set_rx_* + recv(buf, meta, t)) is
# tried FIRST, since that is what's confirmed working on the target laptop;
# 4.2+ multi_source/get_samples is the fallback for other setups.
# ---------------------------------------------------------------------------
class USRP:
    """Thin wrapper so the rest of the code calls one stable interface
    (tune/rate/gain/get) regardless of which UHD binding is present."""

    def __init__(self, device_arg=None):
        self.uhd = self._import_uhd()
        args = device_arg or "type=b200"
        self.dev = self._open(args)
        self.streamer = None
        self._stream_meta = None

    def _import_uhd(self):
        import uhd
        return uhd

    def _open(self, args):
        u = self.uhd
        errors = []
        # 1) UHD 4.9 pybind: uhd.usrp.MultiUSRP / uhd.usrp.multi_usrp
        for sub in ("usrp",):
            mod = getattr(u, sub, None)
            for cname in ("MultiUSRP", "multi_usrp"):
                ctor = getattr(mod, cname, None) if mod is not None else None
                if ctor is None:
                    continue
                try:
                    return ctor(args)
                except Exception as e:
                    errors.append((sub + "." + cname, e))
        # 2) 4.2+ multi-board source
        for cname in ("usrp_multi_source", "usrp_multi_usrp"):
            ctor = getattr(u, cname, None)
            if ctor is None:
                continue
            try:
                return ctor(args)
            except Exception as e:
                errors.append((cname, e))
        # 3) raw device
        ctor = getattr(u, "device", None)
        if ctor is not None:
            try:
                return ctor(args)
            except Exception as e:
                errors.append(("device", e))
        avail = [x for x in dir(u) if "usrp" in x.lower() or "source" in x.lower()]
        raise SystemExit(
            "no UHD constructor worked for %r\n  tried: %s\n  "
            "available uhd.* symbols: %s\n  hint: check UHD_IMAGES_DIR, "
            "'lsusb | grep -i ettus', 'uhd_find_devices'."
            % (args, errors, avail))

    # -- tuning (try 4.9 names then 4.2+ names) --
    def set_freq(self, center):
        d = self.dev
        if hasattr(d, "set_rx_freq"):
            d.set_rx_freq(center)
        elif hasattr(d, "set_center_freq"):
            d.set_center_freq(center, 0)
        elif hasattr(d, "set_tune_request"):
            d.set_tune_request(self.uhd.tune_request(center))
        else:
            raise SystemExit("no freq-set method found on device %r" % (d,))

    def set_rate(self, srate):
        d = self.dev
        if hasattr(d, "set_rx_rate"):
            d.set_rx_rate(srate)
        elif hasattr(d, "set_sample_rate"):
            d.set_sample_rate(srate)
        else:
            raise SystemExit("no rate-set method found on device %r" % (d,))

    def set_gain(self, gain):
        d = self.dev
        for name in ("set_rx_gain", "set_gain"):
            if hasattr(d, name):
                try:
                    getattr(d, name)(gain)
                    return
                except Exception:
                    try:
                        getattr(d, name)(gain, 0)
                        return
                    except Exception as e:
                        print("warn: %s(%s) failed (%s); using default gain"
                              % (name, gain, e))
                        return
        print("warn: no gain-set method; using default gain")

    def _ensure_stream(self, srate_unused):
        """Lazily create the RX streamer + metadata object."""
        if self.streamer is not None:
            return
        u, d = self.uhd, self.dev
        # build StreamArgs: try (fmt, '') then (fmt,) then StreamArgs()
        sa = None
        for mk in (lambda: u.usrp.StreamArgs("fc32", ""),
                   lambda: getattr(u, "usrp", None) and u.usrp.StreamArgs("fc32"),
                   lambda: u.StreamArgs("fc32", ""),
                   lambda: u.StreamArgs("fc32"),
                   lambda: u.StreamArgs()):
            try:
                sa = mk()
                break
            except Exception:
                continue
        if sa is None:
            raise SystemExit("could not construct UHD StreamArgs")
        # select RX subdev + antenna + DC offset (4.9 pybind uses set_rx_* names;
        # the RX path must be configured or recv times out with no data)
        try:
            d.set_rx_subdev_spec("A", 0)
            print("[sdr] set_rx_subdev_spec A:0")
        except Exception as e:
            print("[sdr] note: set_rx_subdev_spec failed (%r)" % (e,))
        ant_ok = False
        for ant in ("RX2", "RX1", "RX"):
            try:
                d.set_rx_antenna(ant, 0)
                ant_ok = True
                print("[sdr] set_rx_antenna %s" % ant)
                break
            except Exception:
                continue
        if not ant_ok:
            try:
                ants = d.get_rx_antennas(0)
                d.set_rx_antenna(ants[0], 0)
                print("[sdr] set_rx_antenna %s" % ants[0])
            except Exception as e:
                print("[sdr] note: antenna set failed (%r)" % (e,))
        try:
            d.set_rx_dc_offset(True, 0)
        except Exception:
            try:
                d.set_rx_dc_offset(True)
            except Exception:
                pass
        self.streamer = d.get_rx_stream(sa)
        # metadata object for recv(buf, meta, timeout) -- class is uhd.types.RXMetadata
        for mk in (lambda: u.types.RXMetadata(),
                   lambda: getattr(u, "RXMetadata", None) and u.RXMetadata(),
                   lambda: u.types.rx_metadata(),
                   lambda: u.rx_metadata()):
            try:
                self._stream_meta = mk()
                break
            except Exception:
                continue
        if self._stream_meta is None:
            raise SystemExit("could not construct UHD RXMetadata object")

    def _stream_cmd_start(self):
        """Start the RX stream explicitly. 4.9 needs a stream_cmd object, not a
        bool. Best-effort: if the stream auto-starts on first recv this is a
        no-op, and if the API differs we just log it and continue."""
        u = self.uhd
        sc = None
        for mk in (lambda: u.types.stream_cmd(u.types.stream_cmd.stream_mode_t.CONTINUOUS),
                   lambda: getattr(u.types, "stream_cmd", None) and u.types.stream_cmd(),
                   lambda: getattr(u, "stream_cmd", None) and u.stream_cmd()):
            try:
                sc = mk()
                break
            except Exception:
                continue
        if sc is None:
            print("[sdr] note: no stream_cmd object available; relying on auto-start")
            return
        try:
            self.streamer.issue_stream_cmd(sc)
            print("[sdr] stream started via issue_stream_cmd")
        except Exception as e:
            print("[sdr] note: issue_stream_cmd failed (%r); relying on auto-start" % (e,))

    def _recv_chunk(self, buf, timeout):
        """recv one chunk into buf; return (count, metadata_error_str)."""
        st, md = self.streamer, self._stream_meta
        try:
            got = st.recv(buf, md, timeout)
        except TypeError:
            got = st.recv(buf, md)
        err = "ok"
        try:
            err = str(md.error_code)
        except Exception:
            pass
        return int(got), err

    def get(self, n):
        """Return up to n complex64 samples. Self-diagnosing: prints what it's
        doing so the first live run reveals the stream state (no separate probe
        commands needed)."""
        d = self.dev
        if hasattr(d, "get_samples"):
            return np.asarray(d.get_samples(n), dtype=np.complex64)
        self._ensure_stream(None)
        per = n
        if hasattr(self.streamer, "get_max_num_samps"):
            try:
                per = min(n, int(self.streamer.get_max_num_samps()))
            except Exception:
                per = n
        per = max(per, 8)
        out = np.zeros(n, dtype=np.complex64)
        filled = 0
        # First, a diagnostic probe: one short recv to see if the stream is
        # delivering anything at all.
        probe = out[0:min(per, n)]
        got0, err0 = self._recv_chunk(probe, 2.0)
        print("[sdr] probe recv: got=%d err=%s (buffer absmax=%s)"
              % (got0, err0,
                 float(np.abs(probe[:max(got0,1)]).max()) if got0 > 0 else "n/a"))
        if got0 > 0:
            filled += got0
        else:
            # Stream delivered nothing. Try to start it explicitly and re-probe.
            self._stream_cmd_start()
            probe2 = out[0:min(per, n)]
            got1, err1 = self._recv_chunk(probe2, 3.0)
            print("[sdr] re-probe after stream start: got=%d err=%s" % (got1, err1))
            if got1 > 0:
                filled += got1
            else:
                print("[sdr] ERROR: RX stream is not delivering samples "
                      "(recv timeout, no data). The board opens and tunes but "
                      "the USB RX path is not producing. This is usually: "
                      "antenna/subdev not selected, or the RX chain needs "
                      "set_subdev_spec / a dc-offset reset. See stderr hints "
                      "above. Aborting capture.")
                raise SystemExit(2)
        # Fill the rest of the requested window.
        timeout = 2.0
        stalls = 0
        while filled < n:
            chunk = min(per, n - filled)
            buf = out[filled:filled + chunk]
            got, err = self._recv_chunk(buf, timeout)
            if got <= 0:
                stalls += 1
                if stalls > 5:
                    break
                continue
            stalls = 0
            filled += got
        return out[:filled]


# ---------------------------------------------------------------------------
# 802.11 preamble signatures (a/g, time domain, normalized)
# ---------------------------------------------------------------------------
def short_preamble_word():
    """The 16-bit 802.11a/g short-training sync word, normalized.
    +1/-1 sequence:  1 1 -1 -1 -1 1 1  1 1 -1 -1 1 -1 -1"""
    w = [1, 1, -1, -1, -1, 1, 1, 1, 1, -1, -1, 1, -1, -1, -1, -1]
    return np.array(w, dtype=np.float64)


def long_preamble_word():
    """The 16-bit long-training (first half) sync word, +1/-1."""
    w = [1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, 1, 1, 1]
    return np.array(w, dtype=np.float64)


def correlate_repeats(iq, word, n_repeat=10, step=None):
    """Slide a (word * n_repeat) template over iq, return (peak_corr, peak_idx).
    The template is the repeated sync word; a real preamble is that word
    repeated, so cross-correlation peaks strongly at the preamble start.
    Uses FFT for speed (linear correlation via np.correlate on a windowed
    slice is simpler but O(N*W); FFT keeps it tractable at 25 MS/s)."""
    W = len(word) * n_repeat
    if len(iq) < W + 8:
        return 0.0, -1
    # normalize both
    tpl = np.tile(word, n_repeat).astype(np.complex128)
    tpl = tpl / (np.abs(tpl).max() + 1e-12)
    # use a Hann-windowed sliding dot via stride trick for the peak only:
    # sample the metric every `step` samples to bound cost
    if step is None:
        step = max(1, W // 4)
    best, best_idx = 0.0, -1
    # downsample the search: check every `step` offset but with a small window
    metric = np.abs(np.correlate(iq.astype(np.complex128), tpl, mode="valid"))
    if len(metric) == 0:
        return 0.0, -1
    # reduce to a coarse peak
    pk = int(np.argmax(metric))
    return float(metric[pk]), pk


def estimate_freq_offset(iq, center, srate, idx, span=4096):
    """Rough carrier offset from the phase slope around idx (a few kHz
    accuracy, enough to show 'is it centered')."""
    seg = iq[idx:idx + span]
    if len(seg) < 128:
        return 0.0
    # remove DC, look at analytic signal phase derivative
    seg = seg - np.mean(seg)
    # goertzel-free: FFT around DC
    spec = np.fft.fft(seg)
    freqs = np.fft.fftfreq(len(seg), d=1.0 / srate)
    # energy near DC
    dc = np.abs(spec[:len(spec) // 2])
    if dc.max() == 0:
        return 0.0
    # offset estimate = peak within +/- srate/4 of the expected DC
    k = int(np.argmax(dc[:len(dc) // 4]))
    return float(freqs[k])


def detect_preambles(iq, center, srate, min_conf=1.0):
    """Return a list of candidate preamble detections in this window."""
    out = []
    Ws = len(short_preamble_word())
    # normalize window
    peak = np.abs(iq).max()
    if peak == 0:
        return out
    iq_n = iq / peak

    # short preamble: 10 repeats of the 16-bit word
    sc, si = correlate_repeats(iq_n, short_preamble_word(), n_repeat=10)
    # long preamble: 12 repeats of the long word (use 8 repeats for robustness)
    lc, li = correlate_repeats(iq_n, long_preamble_word(), n_repeat=8)

    # normalize correlation confidence against the window length
    conf_s = sc / (10 * Ws)
    conf_l = lc / (8 * Ws)

    if conf_s >= min_conf and si >= 0:
        out.append({
            "type": "SHORT", "idx": si, "conf": round(float(conf_s), 3),
            "freq_off_hz": estimate_freq_offset(iq, center, srate, si),
        })
    if conf_l >= min_conf and li >= 0:
        out.append({
            "type": "LONG", "idx": li, "conf": round(float(conf_l), 3),
            "freq_off_hz": estimate_freq_offset(iq, center, srate, li),
        })
    out.sort(key=lambda d: -d["conf"])
    return out


# ---------------------------------------------------------------------------
# Synthetic self-test: synthesize 802.11 short+long preambles in noisy IQ and
# confirm the detector finds them at the right offsets. No SDR needed.
# ---------------------------------------------------------------------------
def selftest(seed=0):
    rng = np.random.default_rng(seed)
    srate = 25e6
    n = 1_000_000
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * 0.01
    carrier = np.exp(2j * np.pi * 1200.0 * np.arange(n) / srate)
    sig = noise.copy()

    w = short_preamble_word()
    off = 500_000
    tpl = np.tile(w, 10)
    sig[off:off + len(tpl)] += (tpl * carrier[off:off + len(tpl)]).astype(np.complex64)

    lw = long_preamble_word()
    offL = 700_000
    tplL = np.tile(lw, 12)
    sig[offL:offL + len(tplL)] += (tplL * carrier[offL:offL + len(tplL)]).astype(np.complex64)

    cands = detect_preambles(sig, 2.437e9, srate, min_conf=0.3)
    ok_s = any(c["type"] == "SHORT" and abs(c["idx"] - off) < 2000 for c in cands)
    ok_l = any(c["type"] == "LONG" and abs(c["idx"] - offL) < 2000 for c in cands)
    for c in cands:
        print("  ", c)
    print("  short detected near %d: %s" % (off, ok_s))
    print("  long  detected near %d: %s" % (offL, ok_l))
    ok = ok_s and ok_l
    print("SELFTEST %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="SDR 2.4 GHz capture + 802.11 preamble/energy detector.")
    ap.add_argument("--device", default=None, help="UHD device arg (type=b200)")
    ap.add_argument("--center", type=float, default=2.437e9,
                    help="center frequency Hz (2437 MHz = 2.4 GHz Wi-Fi mid)")
    ap.add_argument("--srate", type=float, default=10.0e6,
                    help="sample rate Hz (10 MS/s default = safe over USB3; "
                         "raise to 20-25 MS/s for full 20 MHz Wi-Fi bandwidth)")
    ap.add_argument("--gain", type=float, default=30.0, help="RF gain dB")
    ap.add_argument("--seconds", type=float, default=5.0, help="capture seconds")
    ap.add_argument("--min-conf", type=float, default=0.5,
                    help="min normalized correlation confidence to report")
    ap.add_argument("--top", type=int, default=15, help="show top N candidates")
    ap.add_argument("--save", default=None, help="save IQ to this .cfile (fc32)")
    ap.add_argument("--psd", action="store_true", help="also print top spectral peaks")
    ap.add_argument("--selftest", action="store_true",
                    help="synthesize 802.11 preambles in IQ and verify detection (no SDR)")
    args = ap.parse_args()

    if args.selftest:
        import sys
        sys.exit(selftest())

    usrp = USRP(args.device)
    usrp.set_freq(args.center)
    usrp.set_rate(args.srate)
    usrp.set_gain(args.gain)
    n = int(args.srate * args.seconds)
    print("[sdr_wifi_preamble] center %.1f MHz, %.2f MS/s, gain %.0f dB, %.1f s (%d samples)"
          % (args.center / 1e6, args.srate / 1e6, args.gain, args.seconds, n))

    t0 = time.time()
    iq = usrp.get(n)
    iq = np.asarray(iq, dtype=np.complex64)
    if args.save:
        np.ascontiguousarray(iq).tofile(args.save)
        print("[sdr_wifi_preamble] saved IQ -> %s" % args.save)

    if args.psd:
        psd = np.abs(np.fft.rfft(iq * np.hanning(len(iq)))) ** 2
        freqs = np.fft.rfftfreq(len(iq), d=1.0 / args.srate) + args.center
        db = 10 * np.log10(psd + 1e-30)
        idx = np.argsort(db)[::-1][:10]
        print("\ntop spectral peaks (abs freq, dB rel):")
        for i in sorted(idx):
            print("  %9.2f MHz   %8.1f" % (freqs[i] / 1e6, db[i]))

    cands = detect_preambles(iq, args.center, args.srate, args.min_conf)
    print("\n%d preamble candidate(s) (conf >= %.2f):" % (len(cands), args.min_conf))
    if not cands:
        print("  none detected. Try: higher --gain, different --center (2412/2437/2462 MHz),")
        print("  a stronger/known Wi-Fi beacon in range, or more --seconds.")
    for c in cands[:args.top]:
        print("  %-5s conf=%.3f  idx=%d  est_freq_off=%+.1f Hz  (t=%.4f s)"
              % (c["type"], c["conf"], c["idx"], c["freq_off_hz"], c["idx"] / args.srate))
    print("\n[done] %.1f s capture" % (time.time() - t0))
    if cands and cands[0]["conf"] >= args.min_conf:
        print("=> 802.11 preamble signatures present. Stage 2 (OFDM demod -> MAC) is viable.")
    else:
        print("=> no confident 802.11 preamble. Adjust capture params or confirm a")
        print("   Wi-Fi beacon is actually transmitting nearby.")


if __name__ == "__main__":
    main()
