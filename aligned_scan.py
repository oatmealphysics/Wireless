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

Run:  python3 aligned_scan.py [max_bursts_per_capture] [snr_min_dB]
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


def load(path):
    m = read_capture_meta(path)
    sr = float(m.get("sample_rate_hz", 5e6))
    fc = float(m.get("center_freq_hz", 2.4054e9))
    raw = np.fromfile(path, dtype=np.int16)
    iq = (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) / 32767.0
    return iq.astype(np.complex64), sr, fc


def _bw10(S, pk, sr, n):
    thr = S[pk] / 10.0
    i = pk
    while i > 0 and S[i] > thr:
        i -= 1
    j = pk
    while j < len(S) - 1 and S[j] > thr:
        j += 1
    return (j - i) * sr / n, i, j


def find_bursts(iq, sr, snr_min=45.0):
    """Narrowband (ELRS-width) bursts with their energy-weighted centre frequency."""
    nfft, step = 2048, int(sr * 0.4e-3)
    out, last = [], -1
    for k in range((len(iq) - nfft) // step):
        seg = iq[k * step:k * step + nfft]
        S = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(nfft)))) ** 2
        med = np.median(S) + 1e-30
        pk = int(np.argmax(S))
        if 10 * np.log10(S[pk] / med) < snr_min:
            continue
        bw, i0, j0 = _bw10(S, pk, sr, nfft)
        if not (300e3 < bw < 2.0e6):          # reject CW spurs and wideband Wi-Fi
            continue
        a = k * step
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


def aligned_payload_sets(iq, sr, bursts):
    """For each burst: preamble-lock, de-rotate, skip sync+SFD, return payload symbols."""
    sets = []
    for a, cen in bursts:
        s0 = max(0, a - int(sr * 0.0012))
        s1 = min(len(iq), a + int(sr * 0.007))
        seg = iq[s0:s1]
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
    cap_limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
    snr_min = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
    t0 = time.time()
    all_sets = []
    for path in CAPTURES:
        iq, sr, fc = load(path)
        bursts = find_bursts(iq, sr, snr_min=snr_min)[:cap_limit]
        sets = aligned_payload_sets(iq, sr, bursts)
        print(f"{Path(path).name}: {len(bursts)} bursts -> {len(sets)} aligned "
              f"payload sets  [{time.time()-t0:.0f}s]", flush=True)
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
