"""
fhss_seed.py — recover the 31-bit FHSS LCG seed from observed hops.

This is the step from "hop fingerprint" (notes §31/§32, a reproducible set of
true (position, channel) pairs) to an actual **`net_id`**: the seed is derived
from the bind UID by `fhss.uid_seed()`, so recovering it is recovering a real
per-link identity rather than a pattern.

WHY IT IS NOW TRACTABLE
-----------------------
Three things had to be true, and §32 made all three true:

1. **The space is 2^31, not 2^48.** `fhss.uid_seed()` packs UID[2..5] into 32
   bits and `Lcg.__init__` masks to 31, so only 2.1e9 seeds are reachable --
   the other 2 UID bytes never touch the sequence.
2. **Each observation is worth ~6.3 bits** (log2 of 80 channels), so ~5
   observations pin the seed uniquely. We measure 31-34.
3. **The absolute sequence phase is known** (§32a), so there is no 240x
   rotation multiplier. Before that, every candidate had to be tested against
   every rotation, which is what made the search hopeless.

HOW THE SWEEP IS MADE CHEAP
---------------------------
Building a 240-entry sequence per candidate is ~600 operations, so a naive
sweep is ~1.3e12 ops. Two things cut that down hard:

* **Only block 0 is needed first.** Constraints at positions < 80 are decided
  by the first 80 entries, so most seeds die after an 80-entry build.
* **Progressive filtering.** The first constraint keeps ~1/80 of candidates and
  the second ~1/6400, so after two constraints almost nothing survives to be
  tested further. The full-sequence build only ever runs on the survivors.

`build_block0_vectorised()` does the per-chunk work in numpy over whole arrays
of seeds at once, and `selftest()` proves it reproduces `fhss.build_sequence`
exactly before any of it is trusted.
"""
from __future__ import annotations

import numpy as np

from .fhss import DOMAINS, FHSS_SEQUENCE_LEN, Lcg, build_sequence

_LCG_A, _LCG_C, _LCG_M = 214013, 2531011, 1 << 31
ISM = DOMAINS["ISM2G4"]


def build_block0_vectorised(seeds: np.ndarray, freq_count: int,
                            sync_channel: int) -> np.ndarray:
    """Block 0 of the hop sequence for every seed in `seeds`.

    Returns an (len(seeds), freq_count) uint8 array. This is an exact
    vectorisation of `fhss.build_sequence` restricted to the first block --
    the `selftest()` below asserts that elementwise against the scalar port,
    because a fast reimplementation that quietly disagrees would be worse than
    no search at all.
    """
    s = np.asarray(seeds, dtype=np.int64) & 0x7FFFFFFF
    n = len(s)
    # initial array, identical for every seed:
    #   pos 0 -> sync_channel, pos sync_channel -> 0, else pos -> pos
    base = np.arange(freq_count, dtype=np.uint8)
    base[0] = sync_channel
    if sync_channel < freq_count:
        base[sync_channel] = 0
    seq = np.repeat(base[None, :], n, axis=0)

    rows = np.arange(n)
    for i in range(1, freq_count):          # i % freq_count != 0 within block 0
        s = (_LCG_A * s + _LCG_C) % _LCG_M
        rand = (s >> 16) % (freq_count - 1) + 1       # 1 .. freq_count-1
        a = seq[rows, i].copy()
        b = seq[rows, rand]
        seq[rows, i] = b
        seq[rows, rand] = a
    return seq


def advance_lcg(seeds: np.ndarray, steps: int) -> np.ndarray:
    """Run the LCG forward `steps` draws (to skip past earlier blocks)."""
    s = np.asarray(seeds, dtype=np.int64) & 0x7FFFFFFF
    for _ in range(steps):
        s = (_LCG_A * s + _LCG_C) % _LCG_M
    return s


def search(constraints: dict[int, int], freq_count: int = 80,
           sync_channel: int | None = None, chunk: int = 1 << 21,
           seed_space: int = 1 << 31, progress=None,
           max_report: int = 16) -> dict:
    """Sweep the seed space for seeds consistent with `constraints`.

    `constraints` maps **absolute** sequence position -> channel, as
    `fhss_observe.absolute_fold()` returns. Only positions in block 0
    (``< freq_count``) are used for the sweep; the rest are applied to the
    survivors as confirmation, which is what keeps the sweep affordable.
    """
    if sync_channel is None:
        sync_channel = freq_count // 2
    seq_len = (FHSS_SEQUENCE_LEN // freq_count) * freq_count
    b0 = {p: c for p, c in constraints.items() if 0 <= p < freq_count}
    rest = {p: c for p, c in constraints.items() if freq_count <= p < seq_len}
    if not b0:
        return {"error": "no constraints land in block 0; cannot sweep",
                "n_block0": 0, "n_other": len(rest)}
    pos = np.array(sorted(b0), dtype=np.int64)
    want = np.array([b0[int(p)] for p in pos], dtype=np.uint8)

    survivors: list[int] = []
    n_chunks = (seed_space + chunk - 1) // chunk
    for ci in range(n_chunks):
        lo = ci * chunk
        seeds = np.arange(lo, min(lo + chunk, seed_space), dtype=np.int64)
        blk = build_block0_vectorised(seeds, freq_count, sync_channel)
        keep = np.all(blk[:, pos] == want[None, :], axis=1)
        if keep.any():
            survivors.extend(int(x) for x in seeds[keep])
        if progress and (ci % 16 == 0 or ci == n_chunks - 1):
            progress(ci + 1, n_chunks, len(survivors))
        if len(survivors) > 500_000:          # constraints too weak; bail early
            return {"error": "too many survivors; add constraints",
                    "survivors": len(survivors)}

    # confirm survivors on the FULL sequence, including positions outside block 0
    confirmed = []
    for sd in survivors:
        seq = build_sequence(sd, freq_count, sync_channel)
        if all(seq[p] == c for p, c in constraints.items() if p < len(seq)):
            confirmed.append(sd)
    return {
        "n_block0_constraints": len(b0),
        "n_other_constraints": len(rest),
        "survivors_block0": len(survivors),
        "confirmed": confirmed[:max_report],
        "n_confirmed": len(confirmed),
        "unique": len(confirmed) == 1,
    }


def search_multi(constraints: dict[int, int], freq_count: int = 80,
                 sync_channel: int | None = None, chunk: int = 1 << 21,
                 seed_space: int = 1 << 31, progress=None) -> dict:
    """Sweep once, testing every block-anchor rotation at the same time.

    `absolute_fold()` anchors position 0 on the first sighting of the pinned
    sync channel -- but ALL blocks start with the sync channel, so position 0 is
    one of 0, 80, 160 and we cannot tell which from one capture. That is a
    **3-fold** ambiguity, not the 240-fold one we had before §32.

    Rather than run three full sweeps, build block 0 once per seed chunk (the
    expensive part) and test all three rotations' constraint sets against it
    (cheap). Only the correct rotation should yield a surviving seed.
    """
    if sync_channel is None:
        sync_channel = freq_count // 2
    seq_len = (FHSS_SEQUENCE_LEN // freq_count) * freq_count
    n_blocks = seq_len // freq_count
    rots = [b * freq_count for b in range(n_blocks)]

    sets = []
    for r in rots:
        shifted = {(p + r) % seq_len: c for p, c in constraints.items()}
        b0 = {p: c for p, c in shifted.items() if p < freq_count}
        if len(b0) < 4:
            sets.append(None)
            continue
        pos = np.array(sorted(b0), dtype=np.int64)
        want = np.array([b0[int(p)] for p in pos], dtype=np.uint8)
        sets.append((r, pos, want, shifted))
    if not any(sets):
        return {"error": "no rotation puts >=4 constraints in block 0"}

    survivors: dict[int, list[int]] = {r: [] for r in rots}
    # Track the BEST partial match too. We have already found two bugs in this
    # port today (notes §32b, §32c), so "no seed matches all N constraints" is
    # ambiguous on its own: it could mean the port is still subtly wrong. If the
    # best seed in the whole space matches N-1 of N, that is a very different
    # (and much more actionable) story than a best of N/2, which is just noise.
    best: dict[int, tuple[int, int]] = {r: (-1, -1) for r in rots}
    n_chunks = (seed_space + chunk - 1) // chunk
    for ci in range(n_chunks):
        lo = ci * chunk
        seeds = np.arange(lo, min(lo + chunk, seed_space), dtype=np.int64)
        blk = build_block0_vectorised(seeds, freq_count, sync_channel)
        for item in sets:
            if item is None:
                continue
            r, pos, want, _ = item
            hits = (blk[:, pos] == want[None, :]).sum(axis=1)
            k = int(hits.max())
            if k > best[r][0]:
                best[r] = (k, int(seeds[int(np.argmax(hits))]))
            if k == len(pos):
                survivors[r].extend(int(x) for x in seeds[hits == k])
        if progress and (ci % 32 == 0 or ci == n_chunks - 1):
            progress(ci + 1, n_chunks,
                     {r: (len(survivors[r]), best[r][0]) for r in rots})

    out = {"rotations": {}, "n_constraints": len(constraints)}
    for item in sets:
        if item is None:
            continue
        r, pos, want, shifted = item
        conf = []
        for sd in survivors[r]:
            seq = build_sequence(sd, freq_count, sync_channel)
            if all(seq[p] == c for p, c in shifted.items() if p < len(seq)):
                conf.append(sd)
        out["rotations"][r] = {
            "block0_constraints": len(pos),
            "survivors_block0": len(survivors[r]),
            "confirmed": conf[:8],
            "n_confirmed": len(conf),
            "best_partial_match": f"{best[r][0]}/{len(pos)}",
            "best_partial_seed": best[r][1],
        }
    allc = [(r, s) for r, d in out["rotations"].items()
            for s in d["confirmed"]]
    out["unique_seed"] = allc[0][1] if len(allc) == 1 else None
    out["unique_rotation"] = allc[0][0] if len(allc) == 1 else None
    return out


def selftest(verbose: bool = True) -> None:
    """The vectorised builder must match the scalar port bit for bit."""
    fc, sc = ISM.freq_count, ISM.sync_channel
    seeds = np.array([0, 1, 2, 12345, 0x2bcdef, 0x7FFFFFFF, 0x12345678 & 0x7FFFFFFF,
                      987654321, 2 ** 31 - 2], dtype=np.int64)
    vec = build_block0_vectorised(seeds, fc, sc)
    for k, sd in enumerate(seeds):
        ref = build_sequence(int(sd), fc, sc)[:fc]
        assert list(vec[k]) == ref, (
            f"vectorised block 0 differs from build_sequence for seed {sd}:\n"
            f"  vec {list(vec[k])[:12]}\n  ref {ref[:12]}")
    if verbose:
        print(f"  [PASS] vectorised block 0 matches build_sequence exactly for "
              f"{len(seeds)} seeds")

    # every block is a permutation, and position 0 is the sync channel
    assert all(len(set(row)) == fc for row in vec)
    assert all(row[0] == sc for row in vec)
    if verbose:
        print(f"  [PASS] every vectorised block is a permutation of {fc} "
              f"channels starting on the sync channel")

    # a small end-to-end search: recover a KNOWN seed from its own sequence
    known = 0x2bcdef
    seq = build_sequence(known, fc, sc)
    cons = {p: seq[p] for p in (1, 5, 17, 33, 48, 61, 77)}
    # the slice must actually contain `known` (0x2bcdef = 2_870_767)
    res = search(cons, fc, sc, chunk=1 << 19, seed_space=1 << 22)
    assert known in res.get("confirmed", []), (known, res)
    if verbose:
        print(f"  [PASS] end-to-end search over a 2^22 slice recovered the "
              f"known seed {known:#x} from 7 constraints "
              f"({res['survivors_block0']} block-0 survivors -> "
              f"{res['n_confirmed']} confirmed)")
    print("fhss_seed selftest PASS")


if __name__ == "__main__":
    selftest()
