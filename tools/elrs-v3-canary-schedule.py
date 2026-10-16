#!/usr/bin/env python3
"""Build a private, capture-relative ELRS v3 canary slot plan.

This tool joins two already validated offline artifacts: a CRC-valid decoded
SYNC observation from ``elrs-li-offline.py`` and the three-fragment read-only
``MSP_API_VERSION`` plan from ``elrs-li-forward.py``.  It derives the future
FHSS index, RF channel, frequency, and nonce for each fragment.

It intentionally has no SDR backend and cannot transmit.  Times are relative
to a recorded SYNC preamble and are not a live clock.  A transmitter must use
a newly received SYNC observation and independently validate its hardware
latency before this plan can become a delivery mechanism.

All pairing-derived inputs and output belong under the ignored ``work/``
directory.  Default stdout contains only a privacy-safe summary.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import runpy
import sys


TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
FHSS = runpy.run_path(str(TOOLS / "elrs-v3-fhss-check.py"))

FREQ_COUNT = FHSS["FREQ_COUNT"]
SEQUENCE_COUNT = FHSS["SEQUENCE_COUNT"]
SYNC_CHANNEL = FHSS["SYNC_CHANNEL"]
FIRST_HZ = FHSS["FIRST_HZ"]
STEP_HZ = FHSS["STEP_HZ"]
HOP_INTERVAL = 4
DEFAULT_RATE_HZ = 150.0


def require_private_path(path: Path) -> Path:
    """Reject pairing-sensitive files outside the repository's work tree."""
    work = (ROOT / "work").resolve()
    target = path.resolve()
    try:
        target.relative_to(work)
    except ValueError as exc:
        raise ValueError("private paths must be under the ignored work/ directory") from exc
    return target


def read_sync_records(path: Path) -> list[dict]:
    """Return valid decoded SYNC rows from a private JSONL file."""
    candidates: list[dict] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("status") != "crc_valid" or item.get("type") != "SYNC":
                continue
            stamp = item.get("preamble_start_seconds")
            fields = item.get("sync")
            if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                    or not math.isfinite(float(stamp)) or float(stamp) < 0
                    or not isinstance(fields, dict)):
                raise ValueError(f"invalid SYNC record on line {line_number}")
            required = ("fhss_index", "nonce", "switch_mode",
                        "new_tlm_ratio", "rate_index")
            if any(name not in fields for name in required):
                raise ValueError(f"incomplete SYNC record on line {line_number}")
            if (isinstance(fields["fhss_index"], bool)
                    or not isinstance(fields["fhss_index"], int)
                    or not 0 <= fields["fhss_index"] < SEQUENCE_COUNT
                    or isinstance(fields["nonce"], bool)
                    or not isinstance(fields["nonce"], int)
                    or not 0 <= fields["nonce"] <= 255
                    or fields["switch_mode"] != "hybrid"
                    or isinstance(fields["new_tlm_ratio"], bool)
                    or not isinstance(fields["new_tlm_ratio"], int)
                    or not 0 <= fields["new_tlm_ratio"] <= 7
                    or isinstance(fields["rate_index"], bool)
                    or not isinstance(fields["rate_index"], int)
                    or not 0 <= fields["rate_index"] <= 15):
                raise ValueError(f"unsupported SYNC fields on line {line_number}")
            source = item.get("source", "single-source")
            if not isinstance(source, str):
                raise ValueError(f"invalid SYNC source on line {line_number}")
            candidates.append({"line": line_number, "stamp": float(stamp),
                               "source": source, "fields": fields})
    if not candidates:
        raise ValueError("no CRC-valid SYNC record found")
    return candidates


def read_latest_sync(path: Path) -> dict:
    """Return the latest valid decoded SYNC row from a private JSONL file."""
    return max(read_sync_records(path), key=lambda row: row["stamp"])


def validate_sync_transitions(records: list[dict], rate_hz: float,
                              tolerance_seconds: float = 0.0007) -> dict:
    """Cross-check nonce/FHSS evolution against independent frame timestamps."""
    if (not records or not math.isfinite(float(rate_hz)) or rate_hz <= 0
            or not math.isfinite(tolerance_seconds)
            or tolerance_seconds <= 0):
        raise ValueError("invalid SYNC transition inputs")
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["source"], []).append(record)
    checked = 0
    maximum_residual = 0.0
    for group in groups.values():
        ordered = sorted(group, key=lambda row: row["stamp"])
        for before, after in zip(ordered, ordered[1:]):
            delta_seconds = after["stamp"] - before["stamp"]
            delta_slots = round(delta_seconds * rate_hz)
            residual = abs(delta_seconds - delta_slots / rate_hz)
            if delta_slots <= 0 or residual > tolerance_seconds:
                raise ValueError("SYNC timestamps do not fit the requested packet clock")
            fields = before["fields"]
            expected_nonce = (fields["nonce"] + delta_slots) & 0xFF
            hops = ((fields["nonce"] % HOP_INTERVAL) + delta_slots) // HOP_INTERVAL
            expected_index = (fields["fhss_index"] + hops) % SEQUENCE_COUNT
            if (expected_nonce != after["fields"]["nonce"]
                    or expected_index != after["fields"]["fhss_index"]):
                raise ValueError("SYNC nonce/FHSS transition disagrees with source timing")
            checked += 1
            maximum_residual = max(maximum_residual, residual)
    return {"sources": len(groups), "transitions_checked": checked,
            "maximum_clock_residual_seconds": maximum_residual}


def validate_canary(plan: object) -> list[dict]:
    """Validate the read-only three-fragment canary without regenerating it."""
    if not isinstance(plan, dict):
        raise ValueError("canary plan must be an object")
    command = plan.get("command")
    air = plan.get("air")
    frames = plan.get("frames")
    if (plan.get("schema") != "elrs-v3-msp-api-version-canary-v1"
            or plan.get("transmit_enabled") is not False
            or not isinstance(command, dict)
            or command.get("id") != 1
            or command.get("name") != "MSP_API_VERSION"
            or command.get("read_only") is not True
            or command.get("payload_bytes") != 0
            or not isinstance(air, dict)
            or air.get("sf") != 7
            or air.get("bandwidth_hz") != 812_500
            or air.get("implicit_payload_bytes") != 8
            or air.get("preamble_symbols") != 12
            or not isinstance(frames, list) or len(frames) != 3):
        raise ValueError("not the validated read-only API-version canary")
    expected_packages = (1, 2, 0)
    normalized = []
    for sequence, (frame, package_index) in enumerate(zip(frames, expected_packages)):
        if (not isinstance(frame, dict)
                or frame.get("sequence") != sequence
                or frame.get("package_index") != package_index
                or not isinstance(frame.get("packet_hex"), str)
                or not isinstance(frame.get("symbols"), list)):
            raise ValueError("invalid canary fragment order")
        try:
            packet = bytes.fromhex(frame["packet_hex"])
        except ValueError as exc:
            raise ValueError("invalid canary packet hex") from exc
        symbols = frame["symbols"]
        if (len(packet) != 8 or len(symbols) != 21
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or not 0 <= value < 128 for value in symbols)):
            raise ValueError("invalid canary packet or symbol count")
        normalized.append({"sequence": sequence, "package_index": package_index,
                           "packet_hex": frame["packet_hex"], "symbols": symbols})
    return normalized


def telemetry_denominator(new_tlm_ratio: int) -> int | None:
    """Port the v3 SYNC ratio field: zero is no telemetry, 1..7 is 1/128..1/2."""
    if not 0 <= new_tlm_ratio <= 7:
        raise ValueError("telemetry ratio field must be 0..7")
    return None if new_tlm_ratio == 0 else 1 << (8 - new_tlm_ratio)


def build_schedule(uid: bytes, sync: dict, frames: list[dict],
                   guard_slots: int = 16,
                   rate_hz: float = DEFAULT_RATE_HZ) -> dict:
    """Assign the three fragments to future packet slots after one SYNC frame."""
    if len(uid) != 6:
        raise ValueError("UID must contain six bytes")
    if (isinstance(guard_slots, bool) or not isinstance(guard_slots, int)
            or not 1 <= guard_slots <= 128):
        raise ValueError("guard slots must be an integer in 1..128")
    if (isinstance(rate_hz, bool) or not isinstance(rate_hz, (int, float))
            or not math.isfinite(float(rate_hz)) or not 25 <= rate_hz <= 500):
        raise ValueError("rate must be finite and in 25..500 Hz")
    if len(frames) != 3:
        raise ValueError("exactly three canary fragments are required")
    fields = sync["fields"]
    sequence = FHSS["build_v3_sequence"](uid)
    current_index = fields["fhss_index"]
    if sequence[current_index] != SYNC_CHANNEL:
        raise ValueError("SYNC FHSS index disagrees with the UID-derived sequence")
    period = 1.0 / float(rate_hz)
    slots = []
    for fragment_number, frame in enumerate(frames):
        delta = guard_slots + 1 + fragment_number
        nonce = (fields["nonce"] + delta) & 0xFF
        hops = ((fields["nonce"] % HOP_INTERVAL) + delta) // HOP_INTERVAL
        fhss_index = (current_index + hops) % len(sequence)
        channel = sequence[fhss_index]
        slots.append({
            "fragment_sequence": frame["sequence"],
            "package_index": frame["package_index"],
            "delta_slots": delta,
            "preamble_start_seconds": sync["stamp"] + delta * period,
            "nonce": nonce,
            "fhss_index": fhss_index,
            "channel": channel,
            "frequency_hz": FIRST_HZ + channel * STEP_HZ,
            "packet_hex": frame["packet_hex"],
            "symbols": frame["symbols"],
        })
    denominator = telemetry_denominator(fields["new_tlm_ratio"])
    return {
        "schema": "elrs-v3-capture-relative-canary-schedule-v1",
        "transmit_enabled": False,
        "fresh_sync_required": True,
        "clock": {
            "source": "recorded SYNC preamble; not a live hardware clock",
            "sync_line": sync["line"],
            "sync_preamble_start_seconds": sync["stamp"],
            "rate_hz": float(rate_hz),
            "packet_period_seconds": period,
            "guard_slots": guard_slots,
            "hop_interval_slots": HOP_INTERVAL,
        },
        "sync": {
            "fhss_index": current_index,
            "nonce": fields["nonce"],
            "rate_index": fields["rate_index"],
            "switch_mode": fields["switch_mode"],
            "new_tlm_ratio": fields["new_tlm_ratio"],
            "telemetry_denominator": denominator,
        },
        "command": {"name": "MSP_API_VERSION", "id": 1,
                    "read_only": True, "fragment_count": 3},
        "slots": slots,
        "limitations": [
            "capture-relative timestamps cannot be replayed as live timestamps",
            "SDR latency, oscillator error, and transmit polarity are not calibrated",
            "receiver acceptance and any telemetry response are not established",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uid-file", required=True, type=Path)
    parser.add_argument("--sync-jsonl", required=True, type=Path)
    parser.add_argument("--canary-plan", required=True, type=Path)
    parser.add_argument("--private-plan-out", required=True, type=Path)
    parser.add_argument("--guard-slots", type=int, default=16)
    parser.add_argument("--rate-hz", type=float, default=DEFAULT_RATE_HZ)
    args = parser.parse_args(argv)
    try:
        uid_path = require_private_path(args.uid_file)
        sync_path = require_private_path(args.sync_jsonl)
        canary_path = require_private_path(args.canary_plan)
        output_path = require_private_path(args.private_plan_out)
        uid = FHSS["frame_checker"].read_uid(uid_path)
        records = read_sync_records(sync_path)
        sync = max(records, key=lambda row: row["stamp"])
        transition_evidence = validate_sync_transitions(records, args.rate_hz)
        canary = json.loads(canary_path.read_text(encoding="utf-8"))
        frames = validate_canary(canary)
        plan = build_schedule(uid, sync, frames, args.guard_slots, args.rate_hz)
        plan["offline_transition_evidence"] = transition_evidence
        with output_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(plan, stream, indent=2, sort_keys=True)
            stream.write("\n")
        print(json.dumps({
            "transmit_enabled": False,
            "fresh_sync_required": True,
            "scheduled_fragments": len(plan["slots"]),
            "guard_slots": args.guard_slots,
            "sync_transitions_checked": transition_evidence["transitions_checked"],
            "telemetry_enabled": plan["sync"]["telemetry_denominator"] is not None,
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Canary schedule error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
