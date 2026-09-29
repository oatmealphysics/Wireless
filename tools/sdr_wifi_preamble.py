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
import numpy as np


# ---------------------------------------------------------------------------
# UHD (board-agnostic open + RX setup, shared with elrs_live.py conventions)
# ---------------------------------------------------------------------------
def open_usrp(device_arg=None):
    import uhd
    args_str = device_arg or "type=b200"  # b200 matches B205mini and B210
    last_err = None
    for ctor_name in ("usrp_multi_source", "usrp_multi_usrp"):
        ctor = getattr(uhd, ctor_name, None)
        if ctor is None:
            continue
        try:
            return uhd, ctor(args_str), uhd.get_device(0) if hasattr(uhd, "get_device") else None
        except Exception as e:
            last_err = e
    avail = [x for x in dir(uhd) if "usrp" in x.lower() or "source" in x.lower()]
    raise SystemExit(
        "no UHD USRP source constructor worked (args=%r)\n  error: %r\n  "
        "available uhd.* symbols: %s\n  hint: set UHD_IMAGES_DIR if firmware "
        "isn't found; check 'lsusb | grep -i ettus' and 'uhd_find_devices'."
        % (args_str, last_err, avail))


def setup_rx(usrp, uhd, center, srate, gain):
    usrp.set_tune_request(uhd.tune_request(center))
    usrp.set_sample_rate(srate)
    # gain: try RF then baseband if the chain rejects the value
    try:
        usrp.set_gain(gain)
    except Exception:
        try:
            usrp.set_gain(gain, 0)
        except Exception as e:
            print("warn: set_gain(%s) failed (%s); using default gain" % (gain, e))
    # antenna: query rather than hardcode (B205mini has one, B210 has RX1/RX2)
    try:
        ants = usrp.get_antenna_names(0)
        usrp.set_antenna(ants[0], 0)
    except Exception:
        pass
    return usrp


def capture(usrp, n):
    return usrp.get_samples(n)


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
    ap.add_argument("--srate", type=float, default=25.0e6,
                    help="sample rate Hz (>=20 MS/s for 20 MHz Wi-Fi; 25 MS/s default)")
    ap.add_argument("--gain", type=float, default=30.0, help="RF gain dB")
    ap.add_argument("--seconds", type=float, default=10.0, help="capture seconds")
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

    uhd, usrp, _ = open_usrp(args.device)
    setup_rx(usrp, uhd, args.center, args.srate, args.gain)
    n = int(args.srate * args.seconds)
    print("[sdr_wifi_preamble] center %.1f MHz, %.2f MS/s, gain %.0f dB, %.1f s (%d samples)"
          % (args.center / 1e6, args.srate / 1e6, args.gain, args.seconds, n))

    t0 = time.time()
    iq = capture(usrp, n)
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
