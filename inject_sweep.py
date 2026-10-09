#!/usr/bin/env python3
"""
inject_sweep.py — sensitivity floor of the ELRS SYNC recovery path.

`inject_test.py` proved the path recovers a known SYNC packet at 30 dB SNR.
This answers the follow-on question that decides what the 0-hit result on real
air actually means:

    At what SNR does the path stop working, and are the real bursts above it?

If the floor is well below the real captures' measured burst SNR (~40-46 dB
frame-SNR at 10 MS/s, notes §25), then SNR is NOT why real air yields nothing,
and the remaining candidates are (a) no SYNC packets in the capture window or
(b) an SX1280-vs-SX127x PHY deviation in interleaving/whitening.

Run:  python3 inject_sweep.py [--frames 12]
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aligned_scan as A        # noqa: E402
import inject_test as T         # noqa: E402
import ref_scan                 # noqa: E402

SNRS = (45, 40, 35, 30, 25, 20, 15, 12, 10, 8, 6)


def trial(snr_db: float, n_frames: int, tmpdir: str) -> dict:
    cap = os.path.join(tmpdir, f"snr{snr_db:g}.complex8")
    truth = T.make_capture(cap, n_frames, snr_db)
    mm, sr, fc, n = A.open_capture(cap)
    thr = A.auto_snr_threshold(mm, sr, n)
    bursts = A.find_bursts(mm, sr, n, snr_min=thr)
    sets = A.aligned_payload_sets(mm, sr, bursts, n)
    hits, corr, rep = ref_scan.recover(sets) if sets else ([], [], {"trials": 0})
    good = [h for h in hits if h.uid4 == T.UID[4] and h.uid5 == T.UID[5]]
    frames_hit = len({(h.fhss_index, h.nonce) for h in good})
    for f in Path(tmpdir).glob(f"snr{snr_db:g}.*"):
        f.unlink()
    return {"snr_db": snr_db, "gate": thr, "bursts": len(bursts),
            "sets": len(sets), "trials": rep.get("trials", 0),
            "raw_hits": len(hits), "uid_hits": len(good),
            "frames_recovered": frames_hit, "n_frames": n_frames,
            "corroborated": len(corr)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=12)
    args = ap.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="sweep_", dir=os.environ.get(
        "SCRATCH_DIR", tempfile.gettempdir()))
    print(f"injecting {args.frames} known ELRS SYNC frames per SNR point "
          f"(uid 2ee2, SF7/BW812k, +1.2 MHz offset)\n")
    hdr = (f"{'SNR':>5} {'gate':>6} {'bursts':>7} {'sets':>6} {'trials':>7} "
           f"{'rawhit':>7} {'uidhit':>7} {'frames':>8} {'corrob':>7}")
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for snr in SNRS:
        r = trial(snr, args.frames, tmpdir)
        rows.append(r)
        print(f"{r['snr_db']:>5.0f} {r['gate']:>6.1f} {r['bursts']:>7} "
              f"{r['sets']:>6} {r['trials']:>7} {r['raw_hits']:>7} "
              f"{r['uid_hits']:>7} "
              f"{str(r['frames_recovered'])+'/'+str(r['n_frames']):>8} "
              f"{r['corroborated']:>7}", flush=True)

    os.rmdir(tmpdir)
    working = [r for r in rows if r["frames_recovered"] >= 2]
    print()
    if working:
        floor = min(r["snr_db"] for r in working)
        print(f"FLOOR: the path recovers >=2 independent SYNC frames down to "
              f"{floor:.0f} dB injected SNR.")
        print("Real captures' bursts measure ~40-46 dB frame-SNR at 10 MS/s "
              "(notes §25), i.e. well above this floor.")
        print("=> SNR is NOT the explanation for the 0-hit real-air result.")
    else:
        print("No SNR point recovered 2+ frames — re-check inject_test first.")
    detect_only = [r for r in rows if r["bursts"] == 0]
    if detect_only:
        print(f"(energy detector itself goes blind at "
              f"<={max(r['snr_db'] for r in detect_only):.0f} dB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
