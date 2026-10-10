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
from elrs_sdr.core import lora_sync as _ls                    # noqa: E402
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
# Burst finding now lives in the package (elrs_sdr/core/bursts.py) so the
# WaveRaider surface can use the same validated path; AGENTS.md §5 makes core/
# the single source of truth. Re-exported here so every existing caller and
# every `A.find_bursts(...)` reference keeps working unchanged.
from elrs_sdr.core.bursts import (          # noqa: E402,F401
    DC_GUARD_HZ, open_capture, grab, auto_snr_threshold, find_bursts,
)


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


def aligned_payload_sets(mm, sr, bursts, n_complex, frac_delays=None):
    """For each burst: preamble-lock, de-rotate, skip sync+SFD, return payload symbols.

    `frac_delays` is the sub-chip timing grid from `core.lora_sync`. It exists
    because the alignment above is INTEGER-only: `find_bursts` quantises each
    start to its 0.4 ms FFT hop and the preamble lock resolves whole symbols, so
    the sub-chip remainder was never removed. `inject_timing.py` measured what
    that costs on a noise-free synthetic frame with a perfect symbol boundary:
    100% symbol accuracy up to 0.31 chips of offset, 35% at 0.5, and 0% beyond
    0.56 -- only 6 of 16 offsets decoded. That is exactly the SNR-independent
    ~5/12 plateau `inject_sweep.py` saw from 8 dB all the way to 45 dB.
    `lora_sync.selftest()` shows this 4-point grid takes that 8/16 -> 16/16.

    Pass `frac_delays=(0.0,)` to reproduce the old integer-only behaviour.
    """
    if frac_delays is None:
        frac_delays = _ls.FRAC_DELAYS
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
            # The preamble lock is integer-resolution, which is all IT needs;
            # the sub-chip remainder is what destroys the PAYLOAD. It is not
            # estimable from a model we could derive, so a small grid is
            # searched and the ELRS CRC picks the winner.
            #
            # Do NOT also de-rotate by the preamble's fractional bin here. That
            # was tried twice and measured worse both times (8/16 -> 6/16 in
            # isolation; 6/12 -> 2/12 frames recovered in this pipeline),
            # because a sub-chip delay on a chirp ALSO appears as a fractional
            # bin shift. The two are entangled, so removing one as "frequency"
            # breaks the premise of the timing grid. See core/lora_sync.py.
            delayed = [(d, _ls.frac_delay(rs, -d) if d else rs)
                       for d in frac_delays]
            for sfd in SFD_CANDIDATES:
                start = int(round((i1 + 1 + 2 + sfd) * N))
                if start + 6 * N > len(rs):
                    continue
                for _d, rsd in delayed:
                    psym = demodulate_symbols(rsd, p, start,
                                              min((len(rsd) - start) // N, 40))
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
