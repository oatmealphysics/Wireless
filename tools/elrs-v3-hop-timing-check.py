#!/usr/bin/env python3
"""Compare passive PHY timing with a private, source-derived ELRS v3 hop plan.

This is a *timing and channel* cross-check, not an IQ-to-byte decoder. It uses
the fixed sync-channel visits to fit the capture's packet clock, then tests
other measured channels against the Betaflight 4.4.2 FHSS sequence derived
from an independently obtained six-byte UID. It never prints the UID, a
packet, individual times, frequencies, or input paths.

Inputs must be private JSONL files produced by ``elrs-phy-scan.py``. Keep the
UID file and all candidate files under ignored ``work/``. Example::

    python tools/elrs-v3-hop-timing-check.py \
      --uid-file work/private-uid.hex \
      --sync-candidates work/auto-phy-ch41.jsonl \
      --channel 40=work/auto-phy-ch40.jsonl \
      --channel 42=work/auto-phy-ch42.jsonl \
      --duration-seconds 20

The result is evidence of consistency with the supplied UID and source model,
not proof of transmitter identity, SYNC bytes, CRC, or receiver acceptance.
It does not open a radio, transmit, or modify a capture. Python 3.10+.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from bisect import bisect_left
from pathlib import Path

SOURCE = Path(__file__).with_name("elrs-v3-fhss-check.py")
SPEC = importlib.util.spec_from_file_location("elrs_v3_fhss_check", SOURCE)
assert SPEC is not None and SPEC.loader is not None
fhss = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fhss)

SYNC_CHANNEL = fhss.SYNC_CHANNEL
HOP_PACKETS = 4
NOMINAL_RATE_HZ = 150.0
PHASES = (0, fhss.FREQ_COUNT, 2 * fhss.FREQ_COUNT)


def read_candidate_times(path: Path, channel: int) -> list[float]:
    """Load only timing metadata and reject candidates for another channel."""
    if not 0 <= channel < fhss.FREQ_COUNT:
        raise ValueError("channel index out of range")
    times = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if (not isinstance(item, dict)
                        or item.get("phy_candidate") is not True
                        or item.get("packet_bytes_decoded") is not False
                        or fhss.observed_channel(item["channel_hz"], 100_000) != channel):
                    raise ValueError("invalid PHY candidate")
                stamp = item["preamble_start_seconds"]
                if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                    raise ValueError("invalid timestamp")
                stamp = float(stamp)
                if not math.isfinite(stamp) or stamp < 0:
                    raise ValueError("invalid timestamp")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid candidate on line {number}") from exc
            times.append(stamp)
    if not times:
        raise ValueError("candidate file is empty")
    return sorted(times)


def fit_packet_clock(sync_times: list[float]) -> tuple[float, float, int]:
    """Fit packet zero and period from complete four-packet sync visits."""
    if not sync_times or any(not math.isfinite(t) or t < 0 for t in sync_times):
        raise ValueError("invalid sync candidate times")
    times = sorted(sync_times)
    nominal_visit = fhss.FREQ_COUNT * HOP_PACKETS / NOMINAL_RATE_HZ
    visits: list[list[float]] = []
    for stamp in times:
        if not visits or stamp - visits[-1][-1] > nominal_visit / 2:
            visits.append([stamp])
        else:
            visits[-1].append(stamp)
    complete = [(round((visit[0] - visits[0][0]) / nominal_visit), visit[0])
                for visit in visits if len(visit) == HOP_PACKETS]
    if len(complete) < 3 or len({index for index, _ in complete}) != len(complete):
        raise ValueError("need at least three distinct complete sync visits")
    xs = [index * fhss.FREQ_COUNT * HOP_PACKETS for index, _ in complete]
    ys = [stamp for _, stamp in complete]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        raise ValueError("sync visits do not span time")
    period = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    anchor = mean_y - period * mean_x
    if not period > 0 or abs(1 / period - NOMINAL_RATE_HZ) > 0.5:
        raise ValueError("sync timing disagrees with the 150 Hz source mode")
    if max(abs(y - (anchor + x * period)) for x, y in zip(xs, ys)) > 0.00065:
        raise ValueError("sync visits do not fit one packet clock")
    return anchor, period, len(visits)


def nearest_distance(times: list[float], target: float) -> float:
    index = bisect_left(times, target)
    return min((abs(times[j] - target) for j in (index - 1, index)
                if 0 <= j < len(times)), default=float("inf"))


def compare_phases(uid: bytes, observed: dict[int, list[float]],
                   anchor: float, period: float, duration: float,
                   tolerance: float) -> list[dict]:
    """Score held-out channels for each possible sync-slot sequence phase."""
    if (len(uid) != 6 or not math.isfinite(duration) or duration <= 0
            or not math.isfinite(period) or period <= 0
            or not math.isfinite(anchor)
            or not math.isfinite(tolerance) or not 0 < tolerance < period / 2
            or len(observed) < 2 or SYNC_CHANNEL in observed):
        raise ValueError("invalid timing comparison parameters")
    sequence = fhss.build_v3_sequence(uid)
    slot_period = HOP_PACKETS * period
    first_relative = math.floor(-anchor / slot_period) - 1
    last_relative = math.ceil((duration - anchor) / slot_period) + 1
    scores = []
    for phase in PHASES:
        by_channel = {}
        for channel, times in sorted(observed.items()):
            if not 0 <= channel < fhss.FREQ_COUNT or not times:
                raise ValueError("invalid held-out channel candidates")
            expected = hits = 0
            for relative in range(first_relative, last_relative + 1):
                if sequence[(phase + relative) % fhss.SEQUENCE_COUNT] != channel:
                    continue
                for packet in range(HOP_PACKETS):
                    stamp = anchor + relative * slot_period + packet * period
                    if 0 <= stamp < duration:
                        expected += 1
                        hits += nearest_distance(times, stamp) <= tolerance
            by_channel[str(channel)] = {
                "matched": hits, "expected": expected, "observed": len(times),
            }
        scores.append({
            "phase": phase,
            "matched": sum(value["matched"] for value in by_channel.values()),
            "expected": sum(value["expected"] for value in by_channel.values()),
            "channels": by_channel,
        })
    return scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uid-file", required=True, type=Path,
                        help="private six-byte UID hex file; never printed")
    parser.add_argument("--sync-candidates", required=True, type=Path,
                        help="private channel-41 PHY candidates")
    parser.add_argument("--channel", action="append", required=True,
                        help="held-out channel number=private JSONL path; use twice or more")
    parser.add_argument("--duration-seconds", required=True, type=float,
                        help="duration of the original capture")
    parser.add_argument("--tolerance-us", default=650.0, type=float,
                        help="maximum preamble-time error (default: 650 us)")
    args = parser.parse_args(argv)
    try:
        uid = fhss.frame_checker.read_uid(args.uid_file)
        sync_times = read_candidate_times(args.sync_candidates, SYNC_CHANNEL)
        anchor, period, visits = fit_packet_clock(sync_times)
        observed = {}
        for item in args.channel:
            number, separator, filename = item.partition("=")
            if not separator or not number.isdecimal() or not filename:
                raise ValueError("--channel requires number=path")
            channel = int(number)
            if channel in observed or channel == SYNC_CHANNEL:
                raise ValueError("held-out channels must be distinct and non-sync")
            observed[channel] = read_candidate_times(Path(filename), channel)
        scores = compare_phases(uid, observed, anchor, period,
                                args.duration_seconds, args.tolerance_us * 1e-6)
        best = max(scores, key=lambda row: row["matched"])
        print(json.dumps({
            "packet_bytes_decoded": False,
            "sync_visits": visits,
            "sync_candidates": len(sync_times),
            "fitted_rate_hz": round(1 / period, 6),
            "best_phase": best["phase"],
            "phase_scores": scores,
            "interpretation": "timing/channel consistency only; no packet bytes or CRC",
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        print(f"Hop timing check error: {exc if isinstance(exc, ValueError) else 'invalid input'}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
