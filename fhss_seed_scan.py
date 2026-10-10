#!/usr/bin/env python3
"""
fhss_seed_scan.py — try to recover the actual FHSS LCG seed (a true `net_id`)
from a capture's absolute hop fingerprint.

Requires a capture whose window contains the **pinned sync channel** (for the
2.4 GHz domain that is ~2440.4 MHz), because that is what gives absolute
sequence positions — see notes §32. A capture at 2405.4 MHz yields only a
rotation-relative fingerprint and cannot drive this search.

Honest framing: a confirmed unique seed here IS a per-link identity, because
`fhss.uid_seed()` derives the seed from the bind UID. A result of "no seed
found" means the observed sequence is not reproducible by our
`build_sequence` port — which would be a finding about the port, not about the
link, and must be reported that way rather than as a failed detection.

Run:  python3 fhss_seed_scan.py <capture.complex8> [seed_space_log2]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aligned_scan as A                              # noqa: E402
from elrs_sdr.core import fhss_observe as F           # noqa: E402
from elrs_sdr.core import fhss_seed as S              # noqa: E402
from elrs_sdr.core.fhss import uid_seed               # noqa: E402


def main() -> int:
    args = sys.argv[1:]
    paths = [a for a in args if a.endswith(".complex8")]
    if not paths:
        print(__doc__)
        return 2
    log2 = int(next((a for a in args if not a.endswith(".complex8")), 31))

    # Fold every capture given, then keep only the positions ALL of them agree
    # on. A 100-minute sweep is worthless if one constraint is wrong, and a
    # single capture can be wrong: the first 2^31 sweep was poisoned by a fold
    # that claimed positions 49 AND 50 were both channel 34, which is
    # impossible inside a block (slot misassignment). Cross-validating across
    # captures catches the drifted ones that per-block injectivity alone does
    # not, so pass two or more captures of the same link whenever possible.
    folds = []
    for p in paths:
        mm, sr, fc, n = A.open_capture(p)
        thr = A.auto_snr_threshold(mm, sr, n)
        pat = F.observe(A.find_bursts(mm, sr, n, snr_min=thr), sr, fc)
        sync = F.find_sync_channel(pat)
        if sync.get("channel") is None:
            print(f"{Path(p).name}: the pinned sync channel is not in this "
                  f"window ({sync.get('reason')}).")
            print("Re-capture centred on the sync channel "
                  f"(~{F.ISM.channel_freq(F.ISM.freq_count//2)/1e9:.4f} GHz).")
            return 1
        af = F.absolute_fold(pat)
        folds.append((p, sync, af))
        print(f"{Path(p).name}")
        print(f"  pinned sync channel ch{sync['channel']} "
              f"(the freq_count/2 formula says {sync['sync_channel_formula']}"
              f"{' — DISAGREES, measured wins' if sync['formula_disagrees'] else ''}), "
              f"{sync['n_sightings']} sightings, "
              f"spread {sync['spread_hops']} hops")
        print(f"  absolute fold: {len(af['positions'])}/{F.SEQ_LEN} positions "
              f"({af['n_dropped']} vote-conflicts, {af['n_collided']} dropped "
              f"for per-block channel collision {af['collided_positions']})")

    cons = dict(folds[0][2]["positions"])
    for _, _, af in folds[1:]:
        other = af["positions"]
        cons = {p: c for p, c in cons.items() if other.get(p) == c}
    if len(folds) > 1:
        print(f"\ncross-validated across {len(folds)} captures: {len(cons)} "
              f"positions all captures agree on")
    nb0 = sum(1 for p in cons if p < F.ISM.freq_count)
    print(f"  constraints in block 0: {nb0}  (>=4 needed per rotation)")
    if nb0 < 6:
        print(f"  WARNING: {nb0} block-0 constraints is thin. Position 0 is "
              f"always the sync channel and carries no information, so this is "
              f"effectively {max(0, nb0-1)} informative ones against a 2^31 "
              f"space (~5 needed).")
    print(f"  sweeping {2**log2:,} seeds across "
          f"{F.SEQ_LEN // F.ISM.freq_count} block rotations in ONE pass ...",
          flush=True)

    t0 = time.time()

    def prog(ci, nc, surv):
        el = time.time() - t0
        eta = el / ci * (nc - ci)
        print(f"    chunk {ci}/{nc}  {el/60:.1f} min elapsed, "
              f"~{eta/60:.1f} min left, survivors {surv}", flush=True)

    # Solve for the channel offset rather than assuming it. find_sync_channel()
    # calibrates by asserting the pinned channel IS freq_count//2 (giving -1 on
    # these captures). That is an unverified assumption, and a constant channel
    # shift preserves every structural invariant we confirmed while making seed
    # recovery impossible -- which is exactly what two chance-level sweeps look
    # like. So hand the search RAW channels and let it find the offset.
    # Channels are used verbatim, and the sync-channel index is the MEASURED
    # one, not freq_count/2 -- that is what made the seed recoverable (§35).
    sync_ch = folds[0][1]["channel"]
    print(f"  using measured sync_channel={sync_ch} and channels verbatim; "
          f"the offset is still SOLVED FOR as a cross-check")
    res = S.search_offset_free(cons, F.ISM.freq_count, sync_channel=sync_ch,
                               seed_space=2 ** log2, progress=prog)
    print(f"\n--- result  [{(time.time()-t0)/60:.1f} min] ---")
    for r, d in res.get("rotations", {}).items():
        print(f"  rotation {r}: {d['informative_constraints']} informative "
              f"constraints, {d['candidates']} candidates -> "
              f"{d['n_confirmed']} confirmed {d['confirmed']}; "
              f"best consistent {d.get('best_consistent')}")
    uniq = res.get("unique")
    if uniq is not None:
        rot, c = uniq
        sd = c["seed"]
        print(f"\n  ** UNIQUE SEED RECOVERED: {sd} (0x{sd:08x}) at rotation "
              f"{rot}, channel offset {c['channel_offset']:+d} **")
        print(f"  This is a per-link identity: fhss.uid_seed() derives it from "
              f"the bind UID.")
        print(f"  NOTE it does NOT reveal the full 6-byte UID — uid_seed() only "
              f"uses UID[2..5], and the low byte is XORed with OTA_VERSION_ID, "
              f"so UID[0], UID[1] are unrecoverable from the hop sequence and "
              f"UID[5] is known only up to that XOR.")
    elif res.get("error"):
        print(f"\n  {res['error']}")
    else:
        tot = sum(d["n_confirmed"] for d in res.get("rotations", {}).values())
        print(f"\n  {tot} confirmed seeds — not unique." if tot else
              "\n  No seed reproduces the observed sequence.")
        if not tot and log2 >= 31:
            print("  Since the sweep covered the whole 2^31 space, this is a "
                  "statement about our build_sequence port, NOT about the "
                  "link: the observed hops are real and reproducible (§32), so "
                  "some detail of the port still differs from the firmware.")
            bests = [d.get("best_consistent")
                     for d in res["rotations"].values()]
            print(f"  Best partial matches per rotation: {bests}. A best of "
                  f"N-1/N would point at one wrong constraint or a near-miss "
                  f"in the port; a best near half is just chance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
