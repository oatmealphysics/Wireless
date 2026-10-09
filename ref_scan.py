#!/usr/bin/env python3
"""
ref_scan.py — ELRS SYNC air-recovery using the gr-lora_sdr reference chain.

Replaces the guess-and-search approach of `aligned_scan.py` + `lora_payload.py`
with the ported reference algorithms in `elrs_sdr/core/lora_ref.py`. Everything
upstream (burst finding, preamble lock, de-rotation, SFD skip) is reused from
`aligned_scan`; only the payload chain changes.

Why this matters statistically, not just cosmetically:

    lora_payload (guessed)   1152 opts x 2 sizes = 2304 trials/burst
    lora_ref     (reference)   32 opts x 2 sizes =   64 trials/burst

On ~2250 payload sets that is ~144k trials instead of ~5.2M, so the expected
number of chance CRC false-accepts drops from ~7 (after the plausibility gate) to
~0.3. A single corroborated hit from this scan therefore means something, whereas
in the old search it was indistinguishable from noise (notes §24, §27).

Run:  python3 ref_scan.py <capture.complex8> [max_bursts] [snr_min_dB]
"""
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aligned_scan as A                                   # noqa: E402
from elrs_sdr.core import lora_payload as _lp              # noqa: E402
from elrs_sdr.core import lora_ref as _ref                 # noqa: E402

SF = A.SF


def recover(symbol_sets, sf=SF):
    """Reference chain + the §24-gated ELRS SYNC check (plausibility + independence)."""
    opts_list = _ref.all_opts()
    hits, trials = [], 0
    for syms in symbol_sets:
        for opts in opts_list:
            data = _ref.decode_payload(syms, sf, opts)
            if not data:
                continue
            for is_full in (True, False):
                trials += 1
                for h in _lp._try_sync(data, is_full):
                    h.opts = opts.as_tuple()
                    hits.append(h)
    # corroboration: same identity on >=2 bursts AND advancing (nonce, fhss_index)
    by_id: dict[tuple, list] = {}
    for h in hits:
        by_id.setdefault((h.uid4, h.uid5, h.ota_version, h.opts), []).append(h)
    corroborated, rejected = [], 0
    for ident, hs in by_id.items():
        states = {(h.nonce, h.fhss_index) for h in hs}
        if len(hs) >= 2 and len(states) >= 2:
            corroborated.extend(hs)
        elif len(hs) >= 2:
            rejected += len(hs)
    return hits, corroborated, {
        "trials": trials,
        "raw_hits": len(hits),
        "distinct": len(by_id),
        "rejected_identical_state": rejected,
        "expected_false_accepts_pre_gate": trials / 16384.0,
    }


def main():
    args = sys.argv[1:]
    paths = [a for a in args if a.endswith(".complex8")] or A.CAPTURES
    nums = [a for a in args if not a.endswith(".complex8")]
    cap_limit = int(nums[0]) if nums else 100_000
    snr_min = float(nums[1]) if len(nums) > 1 else None

    t0 = time.time()
    all_sets = []
    for path in paths:
        mm, sr, fc, n = A.open_capture(path)
        thr = snr_min if snr_min is not None else A.auto_snr_threshold(mm, sr, n)
        bursts = A.find_bursts(mm, sr, n, snr_min=thr)[:cap_limit]
        sets = A.aligned_payload_sets(mm, sr, bursts, n)
        print(f"{Path(path).name}: snr_gate={thr:.1f}dB  {len(bursts)} bursts -> "
              f"{len(sets)} aligned payload sets  [{time.time()-t0:.0f}s]", flush=True)
        all_sets.extend(sets)

    print(f"\nTOTAL aligned payload sets: {len(all_sets)}")
    print(f"reference chain: {len(_ref.all_opts())} option combos "
          f"(vs 1152 guessed) -> {len(all_sets)*len(_ref.all_opts())*2:,} trials")
    print("running reference-chain CRC-gated recovery ...", flush=True)

    hits, corroborated, rep = recover(all_sets)
    print(f"\n--- report  [{time.time()-t0:.0f}s] ---")
    for k, v in rep.items():
        print(f"  {k}: {v}")
    print(f"  expected false accepts AFTER plausibility gate (~3%): "
          f"{rep['expected_false_accepts_pre_gate']*0.03:.2f}")
    if hits:
        print("  identities seen:")
        for key, c in Counter((f"{h.uid4:02x}{h.uid5:02x}", h.ota_version)
                              for h in hits).most_common(8):
            print(f"    uid={key[0]} OTAv{key[1]}  x{c}")

    print(f"\nCORROBORATED (plausible fields AND advancing nonce/fhss_index): "
          f"{len(corroborated)}")
    for h in corroborated[:10]:
        print(f"   net_id={h.net_id} uid4=0x{h.uid4:02x} uid5=0x{h.uid5:02x} "
              f"OTAv{h.ota_version} full={h.is_full} fhss={h.fhss_index} "
              f"nonce={h.nonce} rate={h.rf_rate_enum} opts={h.opts}")
    if not corroborated:
        print("   none — reporting honestly, no per-link ID recovered.")


if __name__ == "__main__":
    main()
