#!/usr/bin/env python3
"""Compare decoded OTA RC channels with a simultaneous read-only MSP_RC log.

Use a measured/estimated IQ sample-zero UTC timestamp, not a fitted time
offset. The nearest USB sample may be stale relative to a fast stick motion;
this is supporting evidence, not proof of packet-to-stick causality.

  python tools/elrs-rc-overlap.py captures/decoded-v3-summary.jsonl \
      captures/tinyhawk_rc_pair_20261016_retry_msp_rc.jsonl \
      --source tinyhawk-rc-retry-full-phy-2026-10-16.jsonl \
      --iq-start-utc 2026-10-16T17:45:37.014895Z

Source-derived OTA analog values are 988-based. The expected mapping to this
device's MSP order is OTA [1,2,3,0] -> MSP [0,1,2,3], based on the four
observed channel ranges; the tool does not search permutations or clock offset.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
import sys


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamps must include Z or an offset")
    return parsed.astimezone(timezone.utc)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def compare(decoded: list[dict], usb: list[dict], source: str,
            iq_start: datetime, max_gap_s: float = 0.12) -> dict:
    usb_samples = sorted((parse_utc(row["utc"]), row["channels"])
                         for row in usb)
    times = [item[0] for item in usb_samples]
    pairs = []
    for row in decoded:
        if (row.get("source") != source or row.get("status") != "crc_valid"
                or row.get("type") != "RC"):
            continue
        rf_time = iq_start + timedelta(seconds=row["preamble_start_seconds"])
        insertion = bisect_left(times, rf_time)
        neighbors = usb_samples[max(0, insertion - 1):insertion + 1]
        if not neighbors:
            continue
        usb_time, msp = min(neighbors, key=lambda item: abs((item[0] - rf_time).total_seconds()))
        gap = abs((usb_time - rf_time).total_seconds())
        if gap > max_gap_s:
            continue
        ota = row["ota_analog_channels"]
        predicted_msp = [ota[1], ota[2], ota[3], ota[0]]
        pairs.append({"line": row["line"], "rf_utc": rf_time.isoformat(),
                      "usb_gap_ms": round(gap * 1000, 2),
                      "predicted_msp": predicted_msp,
                      "observed_msp": msp[:4]})
    errors = [[abs(pair["predicted_msp"][index] - pair["observed_msp"][index])
               for pair in pairs] for index in range(4)]
    return {"pairs": pairs, "matched": len(pairs),
            "mean_absolute_error": [round(statistics.mean(e), 1) if e else None
                                    for e in errors],
            "timing_note": "IQ start is an external estimate; USB nearest-sample alignment is not exact"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("decoded", type=Path)
    parser.add_argument("usb", type=Path)
    parser.add_argument("--source", required=True, help="source filename in decoded JSONL")
    parser.add_argument("--iq-start-utc", required=True,
                        help="external sample-zero estimate, ISO 8601 with Z")
    parser.add_argument("--max-gap-ms", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        result = compare(read_jsonl(args.decoded), read_jsonl(args.usb),
                         args.source, parse_utc(args.iq_start_utc),
                         args.max_gap_ms / 1000)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["matched"] else 1
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"RC overlap error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
