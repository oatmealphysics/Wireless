"""
fhss_observe.py — recover a per-link identifier from the HOP PATTERN, not the payload.

WHY THIS EXISTS
---------------
Notes §30: decoding the ELRS 2.4 GHz payload to read the UID out of a SYNC
packet is blocked. The 150 Hz rate uses `SX1280_LORA_CR_LI_4_8` -- Semtech
"Long Interleaving", a convolutional code over a packet-spanning interleaver
whose geometry and polynomials Semtech does not publish. Our `lora_ref` chain is
a port of gr-lora_sdr, which models the SX127x per-block Hamming code. Those are
different codes, so no parameter search inside that family can ever decode this
link, and 401,920 trials on a confirmed-live link returned exactly zero.

But AGENTS.md §1 (Identity goal) already names the alternative:

    "The identifier we can actually recover passively is the sync word /
     FHSS sequence (both UID-seeded)."

The FHSS sequence needs **no payload decode at all**. It is carried in *which
channels the link visits, in what order* -- something `aligned_scan.find_bursts`
already measures as a by-product of finding bursts. This module turns that into
a fingerprint.

WHAT THE SEQUENCE LOOKS LIKE (from `fhss.build_sequence`, a port of
FHSSrandomiseFHSSsequenceBuild)
---------------------------------------------------------------------------
For the 2.4 GHz ISM2G4 domain: 80 channels at 2400.4 + n MHz, and
`sequence_count = ceil(256/80)*80 = 320`. The sequence is **4 blocks of 80**,
each block a permutation of all 80 channels, and **every block starts on the
sync channel** (`freq_count // 2 = 40`). So the link visits every channel
exactly once per 80 hops.

WHAT ONE CAPTURE CAN AND CANNOT SEE
-----------------------------------
A 10 MS/s capture at 2405.4 MHz spans 2400.4-2410.4 MHz = channels **0-10**,
i.e. 11 of 80. So a single capture observes ~14% of hops and, importantly,
**never sees the sync channel 40**, so block boundaries are not directly
visible. That is a property of the capture, not of the method -- a capture
centred near 2440.4 MHz would see them.

HONESTY (AGENTS.md §3)
----------------------
What this module produces is a **fingerprint**, not a decoded UID. It is
SELF-CONSISTENT until the same link fingerprints the same way on two separate
captures; `crosscheck()` is the test for that. Recovering the actual 31-bit LCG
seed (and hence a true `net_id`) is a separate, costed job -- see
`seed_search_cost()`, which reports the real numbers rather than implying it is
cheap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .fhss import DOMAINS, FHSS_SEQUENCE_LEN, build_sequence

ISM = DOMAINS["ISM2G4"]
#: Sequence period: the whole multiple of `freq_count` that FITS in the
#: firmware's 256-entry buffer, i.e. 3 blocks of 80 = **240**, not 4 of 320.
#: This used to round UP and was wrong; the period was then measured off the
#: air at 240 (90% fold agreement over 101 corroborated positions, vs 39% at
#: 320 and 0% at 256) and confirmed against FHSS.cpp, whose own expression is
#: `(FHSS_SEQUENCE_LEN / freqCount) * freqCount` -- integer division. See
#: `fhss.build_sequence` and notes §32.
SEQ_LEN = (FHSS_SEQUENCE_LEN // ISM.freq_count) * ISM.freq_count


def channel_of(freq_hz: float, domain=ISM) -> int | None:
    """Channel index for an absolute frequency, or None if off-grid.

    Rejects anything more than half a channel spacing from a grid point, so a
    Wi-Fi hump or a DC spur does not get silently rounded onto a channel.
    """
    spread = domain.freq_spread
    idx = (freq_hz - domain.freq_start) / spread
    n = int(round(idx))
    if n < 0 or n >= domain.freq_count:
        return None
    if abs(idx - n) > 0.4:
        return None
    return n


@dataclass
class HopObservation:
    """One dwell on one channel: when it started, which channel, how many bursts."""
    t_s: float
    channel: int
    n_bursts: int


@dataclass
class HopPattern:
    """What one capture saw of the hop sequence."""
    hops: list[HopObservation] = field(default_factory=list)
    hop_interval_s: float = 0.0
    channels_seen: tuple[int, ...] = ()
    n_bursts: int = 0
    n_offgrid: int = 0

    @property
    def span_s(self) -> float:
        return (self.hops[-1].t_s - self.hops[0].t_s) if len(self.hops) > 1 else 0.0

    def slots(self) -> list[tuple[int, int]]:
        """(slot, channel) with slot = hop index from the first observed hop.

        Quantises each dwell's start time by the measured hop interval, so hops
        we could not see (out-of-window channels) still consume their slot.
        """
        if not self.hops or self.hop_interval_s <= 0:
            return []
        t0 = self.hops[0].t_s
        return [(int(round((h.t_s - t0) / self.hop_interval_s)), h.channel)
                for h in self.hops]

    def folded(self, seq_len: int = SEQ_LEN) -> dict[int, int]:
        """slot mod seq_len -> channel, keeping only positions that agree.

        The sequence repeats every `seq_len` hops, so observations fold onto it.
        A position where two passes disagree is dropped rather than guessed --
        a disagreement means the slot quantisation drifted, and a wrong entry is
        worse than a missing one.
        """
        votes: dict[int, list[int]] = {}
        for slot, ch in self.slots():
            votes.setdefault(slot % seq_len, []).append(ch)
        out = {}
        for pos, chans in votes.items():
            if len(set(chans)) == 1:
                out[pos] = chans[0]
        return out


def estimate_hop_interval(times: np.ndarray, channels: np.ndarray,
                          lo: float = 0.002, hi: float = 0.100) -> float:
    """MEASURE the hop interval from channel-change times (never assume it).

    ELRS hops every `FHSShopInterval` packets, and that field is not something
    we can read off the air, so it is derived here: take the elapsed time
    between consecutive channel CHANGES, and find the largest value `T` such
    that those gaps are near-integer multiples of `T`. Gaps are multiples of the
    hop interval because unseen (out-of-window) channels consume whole hops.
    """
    changes = times[np.r_[True, channels[1:] != channels[:-1]]]
    if len(changes) < 4:
        return 0.0
    gaps = np.diff(changes)
    gaps = gaps[(gaps > lo) & (gaps < hi * 40)]
    if len(gaps) < 3:
        return 0.0
    # Score every candidate T, then take the LARGEST good one. Any divisor of a
    # set of multiples fits it just as well, so a plain argmax latches onto a
    # sub-harmonic -- measured: it returned 2.66 ms for a true 26.6 ms interval,
    # exactly T/10. Two guards: require that T itself occurs as a gap (some
    # multiple must be 1, i.e. two consecutive hops both landed in our window),
    # and among equally good candidates prefer the largest.
    scored = []
    for T in np.arange(lo, hi, 0.00002):
        r = gaps / T
        mult = np.round(r)
        ok = mult >= 1
        if ok.sum() < 3 or not np.any((mult == 1) & (np.abs(r - mult) < 0.06)):
            continue
        scored.append((float(np.mean(np.abs(r[ok] - mult[ok]) < 0.06)), float(T)))
    if not scored:
        return 0.0
    top = max(s for s, _ in scored)
    if top <= 0.5:
        return 0.0
    return max(T for s, T in scored if s >= top - 1e-9)


def refine_interval(times: np.ndarray, T0: float, span_frac: float = 0.25
                    ) -> float:
    """Sharpen a hop-interval estimate by regressing dwell time on slot index.

    The grid-search estimate is only as fine as its step, and the residual error
    ACCUMULATES: a 0.15% error (26.64 vs 26.60 ms, measured) is 1.35 slots of
    drift over 900 hops, which silently corrupts the fold -- every late slot
    lands one position off and the whole fingerprint disagrees with itself.

    So: assign slots over a short leading window where the drift cannot yet
    exceed half a slot, least-squares fit T there, then extend to the full span
    and refit with the now-accurate T. `t = t0 + T * slot` through the origin,
    so T = sum(slot * dt) / sum(slot^2).
    """
    if len(times) < 4 or T0 <= 0:
        return T0
    t = times - times[0]
    T = T0
    for frac in (span_frac, 1.0, 1.0):
        sel = t <= max(t[-1] * frac, T * 8)
        if sel.sum() < 4:
            continue
        slots = np.round(t[sel] / T)
        denom = float(np.sum(slots * slots))
        if denom <= 0:
            continue
        cand = float(np.sum(slots * t[sel]) / denom)
        if cand > 0:
            T = cand
    return T


def observe(bursts, sample_rate: float, center_hz: float,
            domain=ISM, min_bursts_per_hop: int = 1) -> HopPattern:
    """Turn `aligned_scan.find_bursts()` output into a `HopPattern`.

    `bursts` is the list of `(sample_index, centre_offset_hz)` that
    `find_bursts` returns -- the centre offset is the energy-weighted centroid
    it already computes, so no extra signal processing is needed here.
    """
    pat = HopPattern()
    rows = []
    for a, cen in bursts:
        pat.n_bursts += 1
        ch = channel_of(center_hz + cen, domain)
        if ch is None:
            pat.n_offgrid += 1
            continue
        rows.append((a / sample_rate, ch))
    if not rows:
        return pat
    rows.sort()
    times = np.array([r[0] for r in rows])
    chans = np.array([r[1] for r in rows])
    pat.hop_interval_s = estimate_hop_interval(times, chans)

    # group consecutive same-channel bursts into one dwell
    start = 0
    for i in range(1, len(rows) + 1):
        if i == len(rows) or chans[i] != chans[start]:
            n = i - start
            if n >= min_bursts_per_hop:
                pat.hops.append(HopObservation(float(times[start]),
                                               int(chans[start]), n))
            start = i
    pat.channels_seen = tuple(sorted(set(int(c) for c in chans)))
    if pat.hop_interval_s > 0 and len(pat.hops) >= 4:
        pat.hop_interval_s = refine_interval(
            np.array([h.t_s for h in pat.hops]), pat.hop_interval_s)
        pat.hops = _merge_split_dwells(pat.hops, pat.hop_interval_s)
    return pat


def _merge_split_dwells(hops: list[HopObservation], hop_s: float,
                        tol: float = 1.5) -> list[HopObservation]:
    """Re-join one dwell that the burst detector reported as two.

    A single dwell holds ~4 packets at 150 Hz, and one mis-measured centroid in
    the middle splits it into two dwells on the same channel. That inflates the
    dwell count (117 observed where ~105 were expected) and, worse, creates a
    spurious same-channel repeat inside an 80-hop block, which is exactly what
    the block-structure test is looking for. Measured on real captures, this is
    the dominant contributor to the residual repeat rate.

    Two dwells merge when they are on the SAME channel and start less than
    `tol` hop intervals apart -- i.e. they cannot be two genuine visits, since
    the sequence visits each channel once per 80 hops.
    """
    if not hops or hop_s <= 0:
        return hops
    out = [hops[0]]
    for h in hops[1:]:
        prev = out[-1]
        if h.channel == prev.channel and (h.t_s - prev.t_s) < tol * hop_s:
            out[-1] = HopObservation(prev.t_s, prev.channel,
                                     prev.n_bursts + h.n_bursts)
        else:
            out.append(h)
    return out


def crosscheck(a: HopPattern, b: HopPattern, seq_len: int = SEQ_LEN
               ) -> tuple[int, float, int]:
    """Do two captures of the SAME link fingerprint the same way?

    The absolute phase of each capture into the 320-hop sequence is unknown, so
    every rotation is tried and the best is reported.

    Returns (best_rotation, agreement_fraction, n_overlapping_positions).
    An agreement near 1.0 on a decent overlap means the fingerprint is a real
    property of the link. Near 1/11 (the chance rate for 11 visible channels)
    means it is not.
    """
    fa, fb = a.folded(seq_len), b.folded(seq_len)
    if not fa or not fb:
        return 0, 0.0, 0
    # Two captures whose windows see DISJOINT channel sets can never share a
    # sequence position: each position holds exactly one channel, so a position
    # one capture can record is one the other is blind to. Comparing them is
    # meaningless, and the rotation search will happily align unrelated
    # positions and report 0% over a handful of them. Say "not applicable"
    # instead of implying a failed test.
    if not (set(a.channels_seen) & set(b.channels_seen)):
        return 0, float("nan"), 0
    best = (0, 0.0, 0)
    for rot in range(seq_len):
        common = [(fa[p], fb[(p + rot) % seq_len])
                  for p in fa if (p + rot) % seq_len in fb]
        if len(common) < 3:
            continue
        agree = sum(1 for x, y in common if x == y) / len(common)
        if (agree, len(common)) > (best[1], best[2]):
            best = (rot, agree, len(common))
    return best


def observe_capture(path: str, snr_min: float | None = None,
                    domain=ISM) -> dict:
    """Whole pipeline for one capture file: bursts -> hops -> fingerprint.

    The packaged entry point, so a caller (the WaveRaider surface, the CLI, a
    notebook) gets the fingerprint from a path without re-deriving any of the
    burst finding or slot alignment. Streams from a memmap, so a 20 s 10 MS/s
    capture (1.6 GB as complex64) is never held in memory.
    """
    from .bursts import auto_snr_threshold, find_bursts, open_capture

    mm, sr, fc, n = open_capture(path)
    thr = snr_min if snr_min is not None else auto_snr_threshold(mm, sr, n)
    bursts = find_bursts(mm, sr, n, snr_min=thr)
    pat = observe(bursts, sr, fc, domain)
    sync = find_sync_channel(pat, domain.freq_count)
    out = {
        "path": path,
        "duration_s": round(n / sr, 2),
        "sample_rate_hz": sr,
        "center_freq_hz": fc,
        "snr_gate_db": round(float(thr), 1),
        "n_bursts": pat.n_bursts,
        "n_offgrid": pat.n_offgrid,
        "n_dwells": len(pat.hops),
        "channels_seen": list(pat.channels_seen),
        "hop_interval_ms": round(pat.hop_interval_s * 1000, 4),
        "hops_per_s": round(1 / pat.hop_interval_s, 2) if pat.hop_interval_s else None,
        "sync": sync,
        "seq_len": SEQ_LEN,
    }
    if sync.get("channel") is not None:
        af = absolute_fold(pat, domain.freq_count)
        out["absolute"] = True
        out["fingerprint"] = {str(p): c for p, c in sorted(af["positions"].items())}
        out["n_positions"] = len(af["positions"])
        out["n_collided"] = af.get("n_collided", 0)
        out["hop_interval_refined_ms"] = round(
            sync["hop_interval_refined_s"] * 1000, 5)
    else:
        fold = pat.folded()
        out["absolute"] = False
        out["fingerprint"] = {str(p): c for p, c in sorted(fold.items())}
        out["n_positions"] = len(fold)
        out["note"] = (
            "The pinned sync channel is not in this capture's window, so these "
            "positions are relative to the first observed hop, not absolute. "
            f"Re-capture centred near "
            f"{domain.channel_freq(domain.freq_count // 2) / 1e9:.4f} GHz to "
            "get absolute positions.")
    return out


def find_sync_channel(pat: HopPattern, freq_count: int = 80,
                      tol: float = 3.0) -> dict:
    """Find the PINNED sync channel, which calibrates both axes at once.

    `FHSSrandomiseFHSSsequenceBuild` puts `syncChannel` at every index where
    `i % freqCount == 0`, and the swap loop touches neither those indices (never
    a source, since it skips `i % freqCount == 0`) nor them as targets (the
    target is `offset + rand` with `rand` in 1..freqCount-1). So the sync
    channel is the ONE channel that recurs exactly every `freq_count` hops;
    every other channel is shuffled within its block and recurs irregularly.

    Measured on `sync_ch40_20261009_161112` (20 s at 2440.4 MHz, 10 MS/s), that
    signature is unmistakable -- gaps in hops, per channel:

        ch 41:  80.0, 80.0, 80.0, 80.1, 79.9, ...   spread 0.3   <- pinned
        ch 40:  94.7, 72.0, 73.0, 95.0, 72.0, ...   spread 23.0
        ch 45: 147.0, 79.9, 13.0, 146.9, 80.0, ...  spread 134.2

    Two things come out of it, neither of which has to be assumed:

    1. **The link's actual sync-channel index.** ELRS's documented formula is
       `sync_channel = freq_count / 2` = 40 (2440.4 MHz), but the pinned channel
       measures one index higher, at 2441.4 MHz = index **41**, on every capture.

       This was first read as a ~1 MHz tuning offset and "corrected" by shifting
       every channel index by -1. **That reading was wrong, and recovering the
       LCG seed proved it** (notes §35): the seed only reproduces the observed
       hops when the measured channels are used VERBATIM (offset 0) and
       `sync_channel` is taken as the measured **41**, not the formula's 40.
       With 40 it fails on exactly the constraints involving channels 40/41 --
       necessarily, because `build_sequence` pins `sync_channel` at position 0
       where it can never move, and swaps channel 0 into its place.

       So this returns the MEASURED index and does not adjust the channels.
       `sync_channel_formula` is reported alongside so the disagreement with
       `freq_count / 2` stays visible rather than being silently absorbed.

    2. **Absolute sequence phase.** The sync channel's slots ARE the block
       starts, so sequence position 0 is pinned instead of being anchored on
       whichever hop we happened to see first. That removes the rotation
       ambiguity that would otherwise multiply a seed search by `SEQ_LEN`.
    """
    T = pat.hop_interval_s
    if T <= 0 or not pat.hops:
        return {"channel": None, "reason": "no hop interval"}
    cands = []
    for ch in sorted(set(h.channel for h in pat.hops)):
        t = [h.t_s for h in pat.hops if h.channel == ch]
        if len(t) < 3:
            continue
        g = np.diff(np.array(t)) / T
        spread = float(g.max() - g.min())
        if spread <= tol and abs(float(g.mean()) - freq_count) <= tol:
            cands.append((spread, ch, float(g.mean()), t))
    if not cands:
        return {"channel": None,
                "reason": f"no channel recurs every ~{freq_count} hops"}
    cands.sort()
    spread, ch, mean_gap, times = cands[0]
    # The sync-channel sightings are, by construction, EXACTLY `freq_count`
    # hops apart -- a far cleaner timing reference than the grid search over
    # channel-change gaps. Use the first-to-last span to refine the hop
    # interval, which removes the accumulated rounding that otherwise shifts
    # late block starts by a slot (seen as a 79-hop gap among 80s).
    t_ref = float(times[-1] - times[0])
    n_span = freq_count * (len(times) - 1)
    hop_refined = t_ref / n_span if n_span > 0 else T
    t0 = pat.hops[0].t_s
    block_slots = [int(round((x - t0) / hop_refined)) for x in times]
    return {
        "channel": ch,
        "mean_gap_hops": round(mean_gap, 3),
        "spread_hops": round(spread, 3),
        "n_sightings": len(times),
        # The measured index IS the sequence value -- see note 1 above. No
        # channel adjustment is applied; `index_offset` is kept at 0 only so
        # existing readers do not silently re-apply the refuted -1 shift.
        "index_offset": 0,
        "sync_channel_formula": freq_count // 2,
        "formula_disagrees": ch != freq_count // 2,
        "block_start_slots": block_slots,
        "hop_interval_refined_s": hop_refined,
        "unique": len(cands) == 1,
    }


def absolute_fold(pat: HopPattern, freq_count: int = 80,
                  seq_len: int = SEQ_LEN) -> dict:
    """Fold onto the sequence with position 0 pinned by the sync channel.

    Unlike `folded()`, whose position 0 is just the first hop we happened to
    see, this returns true sequence positions, so two captures of the same link
    fold onto the SAME positions with no rotation search. Channel values are
    reported exactly as measured -- see `find_sync_channel()` note 1 for why no
    index shift is applied.
    """
    sync = find_sync_channel(pat, freq_count)
    if sync.get("channel") is None:
        return {"positions": {}, "sync": sync}
    off = sync["index_offset"]
    base = sync["block_start_slots"][0]
    t0 = pat.hops[0].t_s
    # use the sync-channel-refined interval, not the grid estimate
    T = sync.get("hop_interval_refined_s") or pat.hop_interval_s
    votes: dict[int, list[int]] = {}
    for h in pat.hops:
        slot = int(round((h.t_s - t0) / T))
        votes.setdefault((slot - base) % seq_len, []).append(h.channel + off)
    pos = {p: v[0] for p, v in votes.items() if len(set(v)) == 1}

    # Enforce the per-block INJECTIVITY the sequence guarantees. Each block is a
    # permutation of all channels, so a channel occurs exactly once per block --
    # two positions in the same block cannot hold the same channel.
    #
    # This check was missing, and it mattered: the fold claimed both position 49
    # AND position 50 were channel 34. Each pair was individually reproducible
    # by ~1.25% of seeds, so nothing looked wrong, but together they are
    # impossible, which silently poisoned a full 2^31 seed sweep (it could only
    # ever reach 7/12). The cause is slot misassignment -- one dwell quantised
    # into a neighbouring slot -- and adjacent duplicates are its signature.
    #
    # Both members of a colliding pair are suspect, so drop both rather than
    # guess which one drifted.
    by_block: dict[int, dict[int, list[int]]] = {}
    for p, c in pos.items():
        by_block.setdefault(p // freq_count, {}).setdefault(c, []).append(p)
    collided = set()
    for chans in by_block.values():
        for c, ps in chans.items():
            if len(ps) > 1:
                collided.update(ps)
    clean = {p: c for p, c in pos.items() if p not in collided}
    return {"positions": clean, "sync": sync,
            "n_dropped": len(votes) - len(pos),
            "n_collided": len(collided),
            "collided_positions": sorted(collided)}


def block_phase(pat: HopPattern, freq_count: int = 80) -> dict:
    """Locate the 80-hop BLOCK BOUNDARY, and in doing so test the block model.

    `fhss.build_sequence` makes each block of `freq_count` consecutive hops a
    permutation of ALL channels, so **every channel appears exactly once per
    block** -- including each of the handful our capture window can see. That is
    a strong, falsifiable structural claim about real air, and it is testable
    without knowing the seed or the UID.

    For each candidate phase, the observed dwells are partitioned into blocks
    and repeats of the same channel inside one block are counted. The true phase
    should show ~zero repeats; a wrong phase straddles a boundary and repeats.

    Returns the best phase, its repeat rate, the mean repeat rate over all
    phases (the null), and `model_holds` -- the honest verdict on whether real
    air behaves the way the ELRS sequence builder says it should.
    """
    slots = pat.slots()
    if len(slots) < freq_count // 4:
        return {"phase": None, "reason": "too few dwells"}
    rates = []
    for phase in range(freq_count):
        blocks: dict[int, list[int]] = {}
        for slot, ch in slots:
            blocks.setdefault((slot - phase) // freq_count, []).append(ch)
        tot = rep = 0
        for chans in blocks.values():
            if len(chans) < 2:
                continue
            tot += len(chans)
            rep += len(chans) - len(set(chans))
        rates.append((rep / tot if tot else 1.0, phase))
    best_rate, best_phase = min(rates)
    mean_rate = float(np.mean([r for r, _ in rates]))
    # Two separate questions, kept separate because the honest answer differs:
    #   structure_detected -- is there ANY 80-hop block structure? (best phase
    #       clearly beats the all-phase null)
    #   zero_repeat -- does real air show the ZERO repeats per block that
    #       `build_sequence` predicts?
    # Measured on both real captures: structure_detected True (repeat rate
    # 0.07-0.13 vs a 0.25-0.29 null, so 2-4x better at one specific phase) but
    # zero_repeat False. The residual is not dwell-splitting -- merging split
    # dwells changed neither the dwell count nor the rate -- so it is most
    # likely slot misassignment from timing jitter near a block boundary.
    # Do not report a confirmed block model on this evidence.
    return {
        "phase": best_phase,
        "repeat_rate": round(best_rate, 4),
        "null_mean_repeat_rate": round(mean_rate, 4),
        "structure_detected": bool(mean_rate > best_rate * 1.8),
        "zero_repeat": bool(best_rate < 0.05),
    }


def seed_search_cost(n_constraints: int, freq_count: int = 80) -> dict:
    """Honest cost of recovering the actual 31-bit LCG seed (a true `net_id`).

    `fhss.uid_seed()` is a 32-bit value the LCG masks to 31 bits, so the space
    is 2^31 ~ 2.1e9 -- NOT the 2^48 of the full 6-byte UID. Each observed
    (position, channel) pair constrains ~log2(freq_count) bits, so only a
    handful of hops are needed to pin it uniquely; the cost is the sweep itself,
    not the information.
    """
    bits_per = np.log2(freq_count)
    return {
        "seed_space": 2 ** 31,
        "bits_per_observation": round(float(bits_per), 2),
        "observations_to_pin_31_bits": int(np.ceil(31 / bits_per)),
        "constraints_available": n_constraints,
        "sufficient": n_constraints >= int(np.ceil(31 / bits_per)),
        "note": ("Information-wise easy; the sweep is the cost. Each candidate "
                 "needs a 320-entry sequence build (~600 ops), so a full sweep "
                 "is ~1.3e12 ops -- a C/parallel job, not a Python one. "
                 "Progressive filtering helps: the first constraint keeps ~1/80 "
                 "of seeds, so later constraints are nearly free."),
    }


def selftest(verbose: bool = True) -> None:
    """Validate against a sequence built from a KNOWN seed.

    Synthesises the hops a capture would see for a known UID seed, pushes them
    through `observe()`, and checks the recovered fold matches the truth.
    """
    seed = 0x12345678 & 0x7FFFFFFF
    seq = build_sequence(seed, ISM.freq_count, ISM.sync_channel)
    assert len(seq) == SEQ_LEN, (len(seq), SEQ_LEN)
    assert len(set(seq[:ISM.freq_count])) == ISM.freq_count, \
        "each block must be a permutation of all channels"
    assert all(seq[i] == ISM.sync_channel
               for i in range(0, SEQ_LEN, ISM.freq_count)), \
        "every block must start on the sync channel"
    if verbose:
        print(f"  [PASS] sequence is {SEQ_LEN} entries = "
              f"{SEQ_LEN // ISM.freq_count} blocks of {ISM.freq_count}, each a "
              f"permutation starting on sync channel {ISM.sync_channel}")

    # synthesise what a 10 MS/s capture at 2405.4 MHz would see: channels 0-10
    srate, center, hop_s = 1e7, 2.4054e9, 0.0266
    visible = set(range(11))
    bursts, truth = [], {}
    for slot in range(900):                      # ~24 s of hops
        ch = seq[slot % SEQ_LEN]
        if ch not in visible:
            continue
        truth[slot % SEQ_LEN] = ch
        f_off = ISM.channel_freq(ch) - center
        for k in range(4):                       # ~4 packets per dwell
            bursts.append((int((slot * hop_s + k * 0.0066) * srate), f_off))

    pat = observe(bursts, srate, center)
    if verbose:
        print(f"  measured hop interval {pat.hop_interval_s*1000:.2f} ms "
              f"(true {hop_s*1000:.2f} ms)")
        print(f"  {pat.n_bursts} bursts -> {len(pat.hops)} dwells on channels "
              f"{pat.channels_seen}")
    assert abs(pat.hop_interval_s - hop_s) < 0.0008, pat.hop_interval_s

    fold = pat.folded()
    assert len(fold) >= 30, f"only {len(fold)} positions recovered"
    # `folded()` anchors position 0 on the FIRST OBSERVED hop, and the first
    # visible channel is not slot 0 of the sequence (slot 0 is always the sync
    # channel, which this window cannot see). So the fold is the true sequence
    # under an unknown rotation -- find it, exactly as `crosscheck` does.
    best = (0, 0.0, 0)
    for rot in range(SEQ_LEN):
        common = [(fold[p], truth[(p + rot) % SEQ_LEN])
                  for p in fold if (p + rot) % SEQ_LEN in truth]
        if len(common) < 10:
            continue
        agree = sum(1 for x, y in common if x == y) / len(common)
        if (agree, len(common)) > (best[1], best[2]):
            best = (rot, agree, len(common))
    rot, agree, n = best
    if verbose:
        print(f"  recovered {len(fold)}/{SEQ_LEN} sequence positions; vs the "
              f"known seed: rotation {rot}, agreement {agree*100:.0f}% "
              f"over {n} positions")
    assert agree == 1.0, (
        f"fold disagrees with the known sequence: {agree*100:.0f}% at the best "
        f"rotation {rot} over {n} positions")
    if verbose:
        print(f"  [PASS] the recovered fold matches the known-seed sequence "
              f"exactly, at one consistent rotation")

    # a capture of the SAME link, started at a different phase, must agree
    pat2 = observe([(a - int(137 * hop_s * srate), c)
                    for a, c in bursts if a >= int(137 * hop_s * srate)],
                   srate, center)
    rot, agree, n = crosscheck(pat, pat2)
    if verbose:
        print(f"  [PASS] crosscheck of two phases of the same link: "
              f"rotation {rot}, agreement {agree*100:.0f}% over {n} positions")
    assert agree > 0.95 and n >= 10, (rot, agree, n)

    # --- the sync-channel calibration, on a window that CAN see channel 40 --- #
    center40 = ISM.channel_freq(ISM.sync_channel)
    vis40 = set(range(ISM.sync_channel - 5, ISM.sync_channel + 6))
    b40 = []
    for slot in range(900):
        ch = seq[slot % SEQ_LEN]
        if ch not in vis40:
            continue
        f_off = ISM.channel_freq(ch) - center40
        for k in range(4):
            b40.append((int((slot * hop_s + k * 0.0066) * srate), f_off))
    p40 = observe(b40, srate, center40)
    sync = find_sync_channel(p40)
    if verbose:
        print(f"  sync channel found: ch={sync['channel']} "
              f"mean_gap={sync['mean_gap_hops']} hops "
              f"spread={sync['spread_hops']} offset={sync['index_offset']} "
              f"unique={sync['unique']}")
    assert sync["channel"] == ISM.sync_channel, sync
    assert abs(sync["mean_gap_hops"] - ISM.freq_count) < 1.0, sync
    assert sync["index_offset"] == 0, sync
    assert not sync["formula_disagrees"], (
        "the synthetic frame is built with sync_channel = freq_count//2, so the "
        "measured index must agree with the formula here")
    assert sync["unique"], "the pinned channel must be unambiguous"
    starts = sync["block_start_slots"]
    # allow one slot of quantisation slack; real captures measured a spread of
    # only 0.255 hops over 10 sightings, well inside this.
    assert all(abs((b - a) - ISM.freq_count) <= 1
               for a, b in zip(starts, starts[1:])), starts
    if verbose:
        print(f"  [PASS] the pinned sync channel is identifiable and its "
              f"sightings are exactly {ISM.freq_count} hops apart")

    af = absolute_fold(p40)
    wrong = [(p, c) for p, c in af["positions"].items() if seq[p] != c]
    assert not wrong, f"absolute fold disagrees with the sequence at {wrong[:5]}"
    if verbose:
        print(f"  [PASS] absolute fold pinned {len(af['positions'])}/{SEQ_LEN} "
              f"TRUE sequence positions with 0 errors and NO rotation search")
    print("fhss_observe selftest PASS")


if __name__ == "__main__":
    selftest()
