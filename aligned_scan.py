#!/usr/bin/env python3
"""
aligned_scan.py — the scaled ELRS SYNC air-recovery, with CORRECT frame alignment.

WHY THIS EXISTS (vs `scale_bursts.py`)
--------------------------------------
`scale_bursts.py` scaled the burst count (good) but demodulated with **no frame
alignment**: it sliced symbols on a fixed 2^SF grid starting at the energy
detector's `burst.start`:

    for i in range(0, len(dechirped) - n_sym + 1, n_sym):   # arbitrary phase

That leaves (a) an arbitrary sub-symbol timing phase, (b) an unknown constant bin
offset (STO/CFO), and (c) no skip of the LoRa **SFD** (the 2.25 down-chirps between
the sync word and the payload). Those corrupt every payload symbol, and they are
why the `2ee2` "recovery" was a decode artifact rather than a packet (notes §24).

This script does the alignment that notes §19/§21 established on real IQ:
  1. find bursts, resample to the LoRa BW (1 sample/chip),
  2. demod symbols and find the **preamble** (longest constant-symbol run),
  3. **de-rotate** by the preamble bin (removes the STO/CFO offset),
  4. skip 2 sync-word symbols + the **2.25-symbol SFD**, then re-demod the payload
     from that exact sample offset,
  5. run the CRC-gated recovery with the §24 plausibility + independence gates.

Sizing (andeds §22a): `ELRS_SYNC_PERIOD = 32`, so only ~1 burst in 32 is a SYNC
packet (~3%). ~471 bursts => ~15 SYNC candidates, which is why scale matters.

Run:  python3 aligned_scan.py [capture.complex8 ...] [max_bursts] [snr_min_dB]
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from elrs_sdr import positivedet as _pd                      # noqa: E402
from elrs_sdr.core import lora_payload as _lp                 # noqa: E402
from elrs_sdr.core.lora import LoRaParams, demodulate_symbols  # noqa: E402
from elrs_sdr.radio import read_capture_meta                   # noqa: E402

CAPTURES = [
    "captures/live_ch5_20261008_204039.complex8",
    "captures/live_ch5_20261008_204103.complex8",
    "captures/live_ch5_20261008_204114.complex8",
]
SF, BW, N = 7, 812_000, 128
SFD_CANDIDATES = (2.0, 2.25, 2.5)   # the LoRa SFD is nominally 2.25 symbols
MIN_PREAMBLE = 8                    # symbols; real locks run 10-13 (notes §19a)


def open_capture(path):
    """Memmap the int16 I/Q file. A 30 s @ 10 MS/s capture is 1.2 GB on disk and
    ~2.4 GB as complex64, so we never materialise the whole thing — bursts are
    found chunk-by-chunk and only short windows around each burst are converted."""
    m = read_capture_meta(path)
    sr = float(m.get("sample_rate_hz", 5e6))
    fc = float(m.get("center_freq_hz", 2.4054e9))
    mm = np.memmap(path, dtype=np.int16, mode="r")
    return mm, sr, fc, len(mm) // 2


def grab(mm, a, n):
    """Complex64 slice [a, a+n) of the memmapped int16 I/Q capture."""
    a = max(0, int(a)); n = int(n)
    seg = np.asarray(mm[2 * a: 2 * (a + n)], dtype=np.float32)
    if len(seg) < 2:
        return np.zeros(0, dtype=np.complex64)
    seg = seg[: (len(seg) // 2) * 2]
    return ((seg[0::2] + 1j * seg[1::2]) / 32767.0).astype(np.complex64)


def _bw10(S, pk, sr, n):
    thr = S[pk] / 10.0
    i = pk
    while i > 0 and S[i] > thr:
        i -= 1
    j = pk
    while j < len(S) - 1 and S[j] > thr:
        j += 1
    return (j - i) * sr / n, i, j


def auto_snr_threshold(mm, sr, n_complex, sample_s=2.0, pct=88.0, floor_db=25.0):
    """Pick the burst SNR gate from the capture itself.

    A FIXED dB gate is rate-dependent and silently drops bursts: with nfft fixed,
    doubling the sample rate doubles the FFT bin width, so the same burst scores
    several dB lower. A 45 dB gate tuned on the 5 MS/s captures (bursts 48-58 dB)
    kept only 3 of 573 real bursts at 10 MS/s, where the same link lands at
    ~40-45 dB — an ~85% loss that made the SYNC-packet yield look hopeless.
    So derive the gate from the frame-SNR distribution instead: bursts are the
    upper tail, the floor is the noise bulk (median ~14 dB in both captures).
    """
    nfft, step = 2048, int(sr * 0.4e-3)
    iq = grab(mm, 0, min(int(sr * sample_s), n_complex))
    snrs = []
    for k in range(max(0, (len(iq) - nfft) // step)):
        seg = iq[k * step:k * step + nfft]
        S = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(nfft)))) ** 2
        snrs.append(10 * np.log10(S.max() / (np.median(S) + 1e-30)))
    if not snrs:
        return floor_db
    return float(max(floor_db, np.percentile(snrs, pct)))


def find_bursts(mm, sr, n_complex, snr_min=45.0):
    """Narrowband (ELRS-width) bursts with their energy-weighted centre frequency.
    Streams the memmap in ~2 s chunks so a 30 s capture never loads at once."""
    nfft, step = 2048, int(sr * 0.4e-3)
    out, last = [], -1
    chunk = int(sr * 2.0)
    for base in range(0, n_complex, chunk):
        iq = grab(mm, base, min(chunk + nfft, n_complex - base))
        for k in range(max(0, (len(iq) - nfft) // step)):
            seg = iq[k * step:k * step + nfft]
            S = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(nfft)))) ** 2
            med = np.median(S) + 1e-30
            pk = int(np.argmax(S))
            if 10 * np.log10(S[pk] / med) < snr_min:
                continue
            bw, i0, j0 = _bw10(S, pk, sr, nfft)
            if not (300e3 < bw < 2.0e6):       # reject CW spurs and wideband Wi-Fi
                continue
            a = base + k * step
            if a - last < int(sr * 0.003):
                continue
            fax = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / sr))
            sl = slice(i0, j0 + 1)
            out.append((a, float(np.sum(fax[sl] * S[sl]) / np.sum(S[sl]))))
            last = a
    return out


def longest_run(sym, tol=1):
    best, i = (0, 0, 0), 0
    while i < len(sym):
        j = i
        while j + 1 < len(sym) and abs(int(sym[j + 1]) - int(sym[i])) <= tol:
            j += 1
        if j - i + 1 > best[0]:
            best = (j - i + 1, i, j)
        i = j + 1
    return best


def aligned_payload_sets(mm, sr, bursts, n_complex):
    """For each burst: preamble-lock, de-rotate, skip sync+SFD, return payload symbols."""
    sets = []
    for a, cen in bursts:
        s0 = max(0, a - int(sr * 0.0012))
        seg = grab(mm, s0, int(sr * 0.0082))
        if len(seg) < 12 * N:
            continue
        t = np.arange(len(seg)) / sr
        rs = _pd.fft_resample(seg * np.exp(-2j * np.pi * cen * t),
                              int(round(len(seg) * BW / sr)))
        if len(rs) < 12 * N:
            continue
        for inv in (False, True):             # LORADetect.invert_iq is 0 or 1
            p = LoRaParams(sf=SF, bw_hz=BW, sample_rate=float(BW), invert_iq=inv)
            sym = demodulate_symbols(rs, p, 0, min(len(rs) // N, 52))
            L, i0, i1 = longest_run(sym)
            if L < MIN_PREAMBLE:
                continue                      # no preamble lock on this polarity
            pre = int(np.median(sym[i0:i1 + 1]))
            for sfd in SFD_CANDIDATES:
                start = int(round((i1 + 1 + 2 + sfd) * N))
                if start + 6 * N > len(rs):
                    continue
                psym = demodulate_symbols(rs, p, start,
                                          min((len(rs) - start) // N, 40))
                if len(psym) >= 10:
                    sets.append([(int(v) - pre) % (1 << SF) for v in psym])
            break                             # one polarity is enough once locked
    return sets


def main():
    args = [a for a in sys.argv[1:]]
    paths = [a for a in args if a.endswith(".complex8")] or CAPTURES
    nums = [a for a in args if not a.endswith(".complex8")]
    cap_limit = int(nums[0]) if len(nums) > 0 else 10_000
    snr_min = float(nums[1]) if len(nums) > 1 else None   # None => auto per capture
    t0 = time.time()
    all_sets = []
    for path in paths:
        mm, sr, fc, n_complex = open_capture(path)
        thr = snr_min if snr_min is not None else auto_snr_threshold(mm, sr, n_complex)
        bursts = find_bursts(mm, sr, n_complex, snr_min=thr)[:cap_limit]
        sets = aligned_payload_sets(mm, sr, bursts, n_complex)
        print(f"{Path(path).name}: snr_gate={thr:.1f}dB  {len(bursts)} bursts -> "
              f"{len(sets)} aligned payload sets  [{time.time()-t0:.0f}s]", flush=True)
        all_sets.extend(sets)

    print(f"\nTOTAL aligned payload sets: {len(all_sets)}", flush=True)
    print(f"expected SYNC candidates (~1 in 32 bursts): "
          f"~{len(all_sets)/len(SFD_CANDIDATES)/32:.1f}", flush=True)
    print("running CRC-gated recovery (plausibility + independence gates on) ...",
          flush=True)

    hits, rep = _lp.recover_from_bursts(all_sets, SF)
    print(f"\n--- report  [{time.time()-t0:.0f}s] ---")
    for k, v in rep.items():
        if k != "agreement":
            print(f"  {k}: {v}")
    print("  top agreement:")
    for key, c in rep["agreement"]:
        print(f"    uid={key[0]:02x}{key[1]:02x} OTAv{key[2]}  x{c}")

    print(f"\nCORROBORATED (plausible fields AND advancing nonce/fhss_index): {len(hits)}")
    for h in hits[:10]:
        print(f"   net_id={h.net_id} uid4=0x{h.uid4:02x} uid5=0x{h.uid5:02x} "
              f"OTAv{h.ota_version} full={h.is_full} fhss={h.fhss_index} "
              f"nonce={h.nonce} rate={h.rf_rate_enum} opts={h.opts}")
    if not hits:
        print("   none — reporting honestly, no per-link ID recovered.")


if __name__ == "__main__":
    main()
