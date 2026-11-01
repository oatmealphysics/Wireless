#!/usr/bin/env python3
"""Offline FHSS cross-check for pre-decoded Betaflight 4.4.2 ELRS v3 frames.

Consumes JSONL records containing ``packet_bytes_hex`` and the RF
``channel_hz`` measured for that frame. A private six-byte UID file is
required: a SYNC candidate's own UID suffix is not independent evidence.
Only aggregate counts are printed; frame bytes, UID, FHSS indices, paths,
and individual frequencies are never emitted.

Example (keep the input and UID under ignored ``work/``)::

    python tools/elrs-v3-fhss-check.py \\
      --input work/decoded-candidates.jsonl --uid-file work/private-uid.hex

The checker does not decode IQ, open a radio, transmit, or change files.
The tool cannot establish whether ``channel_hz`` was independently measured
or merely copied from a tuning plan; record that provenance separately.
Even matching CRC/UID/FHSS candidates do not prove live receiver acceptance.
Python 3.10+, standard library only.

Version-pinned source: Betaflight 4.4.2
``src/main/rx/expresslrs_common.c`` (fhssGenSequence/rngN) and
``src/main/rx/expresslrs.c`` (processRFSyncPacket). The 2.4 GHz frequency
map is nominal: 2400.4 MHz plus 1 MHz per channel. A measured RF carrier
may differ from nominal by tens of kHz; the default tolerance is 100 kHz.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

SOURCE = Path(__file__).with_name("elrs-v3-frame-check.py")
SPEC = importlib.util.spec_from_file_location("elrs_v3_frame_check", SOURCE)
assert SPEC is not None and SPEC.loader is not None
frame_checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(frame_checker)

FREQ_COUNT = 80
SEQUENCE_COUNT = 240  # (256 / 80) * 80 in the 4.4.2 C source
SYNC_CHANNEL = 41  # (80 / 2) + 1 in the 4.4.2 C source
FIRST_HZ = 2_400_400_000
STEP_HZ = 1_000_000


def build_v3_sequence(uid: bytes) -> list[int]:
    """Port of 4.4.2 fhssGenSequence for the SX1280 80-channel domain."""
    if len(uid) != 6:
        raise ValueError("UID must contain six bytes")
    seed = (((uid[2] << 24) + (uid[3] << 16) + (uid[4] << 8) + uid[5])
            ^ frame_checker.OTA_VERSION_ID)
    sequence = [SYNC_CHANNEL if i % FREQ_COUNT == 0 else
                0 if i % FREQ_COUNT == SYNC_CHANNEL else i % FREQ_COUNT
                for i in range(SEQUENCE_COUNT)]
    for i in range(SEQUENCE_COUNT):
        if i % FREQ_COUNT == 0:
            continue
        seed = (214013 * seed + 2531011) % 2147483648
        offset = (i // FREQ_COUNT) * FREQ_COUNT
        other = offset + ((seed >> 16) % (FREQ_COUNT - 1)) + 1
        sequence[i], sequence[other] = sequence[other], sequence[i]
    return sequence


def observed_channel(value: object, tolerance_hz: float) -> int | None:
    """Map a measured carrier to a nominal channel, or reject ambiguity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("channel_hz must be a finite number")
    frequency = float(value)
    if not math.isfinite(frequency):
        raise ValueError("channel_hz must be a finite number")
    channel = round((frequency - FIRST_HZ) / STEP_HZ)
    if not 0 <= channel < FREQ_COUNT:
        return None
    if abs(frequency - (FIRST_HZ + channel * STEP_HZ)) > tolerance_hz:
        return None
    return channel


def evaluate_record(item: object, uid: bytes, sequence: list[int],
                    tolerance_hz: float) -> str:
    """Classify one candidate without returning identifying field values."""
    if not isinstance(item, dict) or not isinstance(item.get("packet_bytes_hex"), str):
        raise ValueError("JSON record requires packet_bytes_hex text")
    try:
        raw = bytes.fromhex(item["packet_bytes_hex"])
    except ValueError as exc:
        raise ValueError("packet_bytes_hex is not hexadecimal") from exc
    verdict = frame_checker.check_frame(raw, uid=uid)
    if verdict["type"] != "SYNC":
        return "not_sync"
    if verdict["crc"] != "pass":
        return "crc_fail"
    if not verdict["uid_18bit_match"]:
        return "uid_mismatch"
    index = verdict["fields"]["fhss_index"]
    if index >= SEQUENCE_COUNT:
        return "index_out_of_range"
    if "channel_hz" not in item:
        return "channel_missing"
    channel = observed_channel(item["channel_hz"], tolerance_hz)
    if channel is None:
        return "channel_unmapped"
    return "channel_match" if channel == sequence[index] else "channel_mismatch"


def summarize(path: Path, uid: bytes, tolerance_hz: float) -> dict[str, int]:
    if not math.isfinite(tolerance_hz) or not 0 <= tolerance_hz < STEP_HZ / 2:
        raise ValueError("tolerance must be finite and below 500 kHz")
    sequence = build_v3_sequence(uid)
    counts = {key: 0 for key in (
        "records", "not_sync", "crc_fail", "uid_mismatch",
        "index_out_of_range", "channel_missing", "channel_unmapped",
        "channel_match", "channel_mismatch")}
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                item = json.loads(line)
                category = evaluate_record(item, uid, sequence, tolerance_hz)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid candidate on line {number}") from exc
            counts["records"] += 1
            counts[category] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path,
                        help="Private JSONL with packet_bytes_hex and channel_hz")
    parser.add_argument("--uid-file", required=True, type=Path,
                        help="Private six-byte UID hex file; never printed")
    parser.add_argument("--tolerance-hz", type=float, default=100_000,
                        help="Maximum deviation from nominal channel (default 100000 Hz)")
    args = parser.parse_args(argv)
    try:
        uid = frame_checker.read_uid(args.uid_file)
        print("Checking offline v3 SYNC candidates against measured channels", file=sys.stderr)
        counts = summarize(args.input, uid, args.tolerance_hz)
        print(json.dumps(counts, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"FHSS check error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
