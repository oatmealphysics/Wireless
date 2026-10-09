#!/usr/bin/env python3
"""
fhss_scan.py — recover the FHSS hop fingerprint from real captures.

This is the identity path that does NOT need a payload decode, and therefore
is not blocked by the `CR_LI_4_8` long-interleaving wall (notes §30). It reads
the hop pattern straight out of the burst centre frequencies that
`aligned_scan.find_bursts` already measures.

Given two or more captures of the SAME link it also cross-checks them, which is
the test that decides whether the fingerprint is a real property of the link
(agreement near 1.0) or an artifact (agreement near the 1/N_visible chance
rate). Until that passes on real captures the fingerprint is SELF-CONSISTENT
only, per AGENTS.md §3.

Run:  python3 fhss_scan.py captures/a.complex8 [captures/b.complex8 ...]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aligned_scan as A                                  # noqa: E402
from elrs_sdr.core import fhss_observe as F               # noqa: E402


def run(path: str):
    mm, sr, fc, n = A.open_capture(path)
    thr = A.auto_snr_threshold(mm, sr, n)
    bursts = A.find_bursts(mm, sr, n, snr_min=thr)
    pat = F.observe(bursts, sr, fc)
    fold = pat.folded()
    print(f"\n=== {Path(path).name} ===")
    print(f"  {n/sr:.1f} s @ {sr/1e6:.5f} MS/s, centre {fc/1e9:.4f} GHz, "
          f"gate {thr:.1f} dB")
    # Derive the covered channel span from the frequency edges directly.
    # (Going through channel_of() here was wrong: it rejects anything off-grid
    # by design, so a band edge that lands mid-channel returned None and the
    # `or 0` / `or 79` fallbacks printed a bogus "100% of the band".)
    import math
    d, f0 = F.ISM.freq_spread, F.ISM.freq_start
    lo = max(0, math.ceil((fc - sr / 2 - f0) / d))
    hi = min(F.ISM.freq_count - 1, math.floor((fc + sr / 2 - f0) / d))
    print(f"  window covers channels {lo}-{hi} of {F.ISM.freq_count} "
          f"({(hi-lo+1)/F.ISM.freq_count*100:.0f}% of the band)")
    print(f"  {pat.n_bursts} bursts ({pat.n_offgrid} off-grid, rejected) -> "
          f"{len(pat.hops)} dwells")
    print(f"  channels actually seen: {pat.channels_seen}")
    print(f"  measured hop interval: {pat.hop_interval_s*1000:.3f} ms"
          f"{'' if pat.hop_interval_s else '   <-- NOT MEASURABLE'}")
    if pat.hop_interval_s:
        print(f"    => {1/pat.hop_interval_s:.1f} hops/s; at 150 Hz packets "
              f"that is {0.006666/pat.hop_interval_s*1:.2f} ... "
              f"{pat.hop_interval_s/0.006666:.2f} packets per hop")
    print(f"  fingerprint: {len(fold)}/{F.SEQ_LEN} sequence positions pinned")
    if fold:
        shown = sorted(fold.items())[:12]
        print(f"    first positions: "
              f"{', '.join(f'{p}->ch{c}' for p, c in shown)}")
    bp = F.block_phase(pat)
    print(f"  block structure: {bp}")
    if bp.get("phase") is not None:
        print(f"    => 80-hop block structure "
              f"{'DETECTED' if bp.get('structure_detected') else 'not detected'}"
              f"; zero-repeat-per-block (what build_sequence predicts) "
              f"{'holds' if bp.get('zero_repeat') else 'does NOT hold'}")
    c = F.seed_search_cost(len(fold))
    print(f"  seed search: {c['constraints_available']} constraints vs "
          f"{c['observations_to_pin_31_bits']} needed for the {c['seed_space']:,} "
          f"seed space -> {'sufficient' if c['sufficient'] else 'NOT sufficient'}")
    return pat


def main() -> int:
    paths = [a for a in sys.argv[1:] if a.endswith(".complex8")]
    if not paths:
        print(__doc__)
        return 2
    t0 = time.time()
    pats = [(p, run(p)) for p in paths]
    phases = {p: F.block_phase(pat).get("phase") for p, pat in pats}
    print(f"\n[{time.time()-t0:.0f}s]")

    if len(pats) < 2:
        print("\nOnly one capture given, so no cross-check. The fingerprint is "
              "SELF-CONSISTENT only until two captures of the same link agree.")
        return 0

    print("\n=== cross-check (the test that decides if this is real) ===")
    for i in range(len(pats)):
        for j in range(i + 1, len(pats)):
            (pa, a), (pb, b) = pats[i], pats[j]
            rot, agree, n = F.crosscheck(a, b)
            vis = max(1, len(set(a.channels_seen) | set(b.channels_seen)))
            chance = 1.0 / vis
            print(f"\n  {Path(pa).name}  vs  {Path(pb).name}")
            print(f"    best rotation {rot}, agreement {agree*100:.0f}% over "
                  f"{n} overlapping positions")
            print(f"    chance rate for {vis} visible channels: "
                  f"{chance*100:.0f}%")
            # Independent corroboration: block_phase() finds the 80-hop
            # boundary by minimising same-channel repeats, which knows nothing
            # about the other capture. If its phase difference equals the
            # rotation the fold agreement picked, two unrelated methods agree
            # on the same alignment -- a 1-in-80 coincidence otherwise.
            ka, kb = phases.get(pa), phases.get(pb)
            if ka is not None and kb is not None:
                print(f"    block phases: {ka} and {kb}, difference "
                      f"{(kb - ka) % 80}  vs  fold rotation mod 80 = "
                      f"{rot % 80}"
                      f"   {'<-- AGREE (independent corroboration)' if (kb - ka) % 80 == rot % 80 else '(differ)'}")
            if n < 10:
                print("    VERDICT: too little overlap to decide.")
            elif agree > 0.9 and agree > 4 * chance:
                print("    VERDICT: the hop fingerprint is REPRODUCIBLE -> a "
                      "real per-link identifier.")
            elif agree < 2 * chance:
                print("    VERDICT: no better than chance -> NOT a usable "
                      "fingerprint as measured. Do not claim an ID.")
            else:
                print("    VERDICT: inconclusive — above chance but not "
                      "reproducible. Needs more overlap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
