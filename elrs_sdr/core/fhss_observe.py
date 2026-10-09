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
#: 80 channels at 1 MHz spacing -> the sequence is 4 blocks of 80
SEQ_LEN = ((FHSS_SEQUENCE_LEN + ISM.freq_count - 1) // ISM.freq_count) * ISM.freq_count


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
    print("fhss_observe selftest PASS")


if __name__ == "__main__":
    selftest()
