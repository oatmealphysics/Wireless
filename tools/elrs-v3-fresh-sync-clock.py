#!/usr/bin/env python3
"""Turn a fresh decoded ELRS v3 SYNC into future SDR slot timestamps.

This is the receive-clock gate between the offline canary scheduler and any
future UHD transport.  It fits the actual packet period from CRC-valid SYNC
transitions, rejects stale observations, reserves processing lead time, and
maps either the three read-only MSP fragments or one fail-closed RC replay
frame to capture sample indices and, optionally, UHD device time.

There is deliberately no radio or transmit backend.  Output is marked
``transmit_enabled: false`` and is useful only as a private timing contract for
a separately reviewed transport.  Pairing-derived inputs and output must stay
under the repository's ignored ``work/`` directory.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import runpy
import sys


TOOLS = Path(__file__).resolve().parent
SCHEDULER = runpy.run_path(str(TOOLS / "elrs-v3-canary-schedule.py"))
RC_CANARY = runpy.run_path(str(TOOLS / "elrs-v3-rc-replay-canary.py"))
NOMINAL_RATE_HZ = 150.0
HOP_INTERVAL = SCHEDULER["HOP_INTERVAL"]
FIRST_HZ = SCHEDULER["FIRST_HZ"]
STEP_HZ = SCHEDULER["STEP_HZ"]


def fit_packet_clock(records: list[dict], nominal_rate_hz: float = NOMINAL_RATE_HZ,
                     tolerance_seconds: float = 0.0007) -> dict:
    """Fit one packet period while allowing each capture its own time origin."""
    if (not records or not math.isfinite(nominal_rate_hz)
            or not 25 <= nominal_rate_hz <= 500):
        raise ValueError("invalid packet-clock inputs")
    evidence = SCHEDULER["validate_sync_transitions"](
        records, nominal_rate_hz, tolerance_seconds)
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["source"], []).append(record)
    numerator = denominator = 0.0
    normalized: list[tuple[list[int], list[float]]] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda row: row["stamp"])
        first = ordered[0]["stamp"]
        slots = [round((row["stamp"] - first) * nominal_rate_hz)
                 for row in ordered]
        times = [row["stamp"] for row in ordered]
        normalized.append((slots, times))
        mean_slot = sum(slots) / len(slots)
        mean_time = sum(times) / len(times)
        numerator += sum((slot - mean_slot) * (stamp - mean_time)
                         for slot, stamp in zip(slots, times))
        denominator += sum((slot - mean_slot) ** 2 for slot in slots)
    if denominator == 0:
        period = 1.0 / nominal_rate_hz
        basis = "nominal_single_sync"
    else:
        period = numerator / denominator
        basis = "multi_sync_regression"
    fitted_rate = 1.0 / period
    if not period > 0 or abs(fitted_rate - nominal_rate_hz) > 0.5:
        raise ValueError("fitted packet rate disagrees with the source mode")
    maximum_fit_residual = 0.0
    for slots, times in normalized:
        intercept = sum(stamp - slot * period
                        for slot, stamp in zip(slots, times)) / len(slots)
        maximum_fit_residual = max(
            maximum_fit_residual,
            max(abs(stamp - (intercept + slot * period))
                for slot, stamp in zip(slots, times)),
        )
    if maximum_fit_residual > tolerance_seconds:
        raise ValueError("SYNC observations do not fit one packet period")
    return {
        **evidence,
        "fit_basis": basis,
        "packet_period_seconds": period,
        "fitted_rate_hz": fitted_rate,
        "maximum_fit_residual_seconds": maximum_fit_residual,
    }


def choose_anchor(records: list[dict], source: str | None) -> dict:
    """Choose the newest SYNC from exactly one capture source."""
    sources = sorted({record["source"] for record in records})
    if source is None:
        if len(sources) != 1:
            raise ValueError("--anchor-source is required when input has multiple captures")
        source = sources[0]
    selected = [record for record in records if record["source"] == source]
    if not selected:
        raise ValueError("anchor source has no CRC-valid SYNC records")
    return max(selected, key=lambda row: row["stamp"])


def read_receive_anchor(path: Path) -> dict:
    """Validate a manifest emitted by the bounded receive-only UHD helper."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "uhd-elrs-receive-anchor-v1"
            or manifest.get("receive_only") is not True
            or manifest.get("transmit_capable") is not False
            or manifest.get("sample_format")
            != "complex-float32-little-endian"):
        raise ValueError("invalid receive-only UHD anchor manifest")
    count = manifest.get("sample_count")
    rate = manifest.get("sample_rate_hz")
    start = manifest.get("first_sample_uhd_seconds")
    if (isinstance(count, bool) or not isinstance(count, int) or count <= 0
            or isinstance(rate, bool) or not isinstance(rate, (int, float))
            or not math.isfinite(float(rate)) or rate <= 0
            or isinstance(start, bool) or not isinstance(start, (int, float))
            or not math.isfinite(float(start)) or start < 0):
        raise ValueError("invalid receive-only UHD timing values")
    return {
        "schema": manifest["schema"],
        "sample_count": count,
        "sample_rate_hz": float(rate),
        "capture_duration_seconds": count / float(rate),
        "first_sample_uhd_seconds": float(start),
    }


def validate_canary_input(plan: object, uid: bytes,
                          aux_safety: dict | None = None) -> tuple[list[dict], dict]:
    """Normalize one of the two reviewed, transmit-disabled canary schemas."""
    if not isinstance(plan, dict):
        raise ValueError("canary plan must be an object")
    schema = plan.get("schema")
    if schema == "elrs-v3-msp-api-version-canary-v1":
        frames = SCHEDULER["validate_canary"](plan)
        return frames, {
            "kind": "msp_api_version",
            "name": "MSP_API_VERSION",
            "read_only": True,
            "frame_count": 3,
        }
    if schema == "elrs-v3-rc-replay-canary-v1":
        if aux_safety is None:
            raise ValueError("RC replay canary requires AUX safety evidence")
        frame = RC_CANARY["validate_rc_canary"](plan, uid, aux_safety)
        return [{
            "sequence": 0,
            "packet_hex": frame["packet_hex"],
            "symbols": frame["symbols"],
        }], {
            "kind": "hybrid_rc_replay",
            "name": "bounded_roll_observation",
            "read_only": False,
            "frame_count": 1,
            "safety_revalidated": True,
        }
    raise ValueError("unsupported canary schema")


def build_fresh_plan(uid: bytes, anchor: dict, frames: list[dict], *,
                     cursor_seconds: float, sample_rate_hz: float,
                     packet_period_seconds: float, lead_seconds: float = 0.100,
                     max_age_seconds: float = 0.500,
                     max_horizon_seconds: float = 1.200,
                     capture_start_uhd_seconds: float | None = None,
                     canary_metadata: dict | None = None) -> dict:
    """Schedule reviewed canary frames, rejecting stale or overlong plans."""
    numeric = (cursor_seconds, sample_rate_hz, packet_period_seconds,
               lead_seconds, max_age_seconds, max_horizon_seconds)
    if (len(uid) != 6 or not 1 <= len(frames) <= 3
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(float(value)) for value in numeric)
            or cursor_seconds < 0 or sample_rate_hz <= 0
            or packet_period_seconds <= 0 or lead_seconds < 0
            or max_age_seconds <= 0 or max_horizon_seconds <= 0):
        raise ValueError("invalid fresh-clock parameters")
    if (capture_start_uhd_seconds is not None
            and (isinstance(capture_start_uhd_seconds, bool)
                 or not isinstance(capture_start_uhd_seconds, (int, float))
                 or not math.isfinite(float(capture_start_uhd_seconds))
                 or capture_start_uhd_seconds < 0)):
        raise ValueError("invalid UHD time anchor")
    age = cursor_seconds - anchor["stamp"]
    if age < 0:
        raise ValueError("capture cursor precedes the SYNC anchor")
    if age > max_age_seconds:
        raise ValueError("latest SYNC anchor is stale")
    fields = anchor["fields"]
    sequence = SCHEDULER["FHSS"]["build_v3_sequence"](uid)
    if sequence[fields["fhss_index"]] != SCHEDULER["SYNC_CHANNEL"]:
        raise ValueError("SYNC FHSS index disagrees with the UID-derived sequence")

    earliest = cursor_seconds + lead_seconds
    first_delta = max(1, math.ceil(
        (earliest - anchor["stamp"]) / packet_period_seconds - 1e-12))
    if (first_delta + len(frames) - 1) * packet_period_seconds > max_horizon_seconds:
        raise ValueError("processing lead pushes the canary beyond the safe horizon")
    slots = []
    for offset, frame in enumerate(frames):
        delta = first_delta + offset
        stamp = anchor["stamp"] + delta * packet_period_seconds
        nonce = (fields["nonce"] + delta) & 0xFF
        hops = ((fields["nonce"] % HOP_INTERVAL) + delta) // HOP_INTERVAL
        fhss_index = (fields["fhss_index"] + hops) % len(sequence)
        channel = sequence[fhss_index]
        slot = {
            "fragment_sequence": frame["sequence"],
            "delta_slots": delta,
            "capture_preamble_seconds": stamp,
            "capture_sample_index": round(stamp * sample_rate_hz),
            "nonce": nonce,
            "fhss_index": fhss_index,
            "channel": channel,
            "frequency_hz": FIRST_HZ + channel * STEP_HZ,
            "packet_hex": frame["packet_hex"],
            "symbols": frame["symbols"],
        }
        if "package_index" in frame:
            slot["package_index"] = frame["package_index"]
        if capture_start_uhd_seconds is not None:
            slot["uhd_preamble_time_seconds"] = capture_start_uhd_seconds + stamp
        slots.append(slot)
    plan = {
        "schema": "elrs-v3-fresh-sync-clock-v1",
        "transmit_enabled": False,
        "fresh_sync_validated": True,
        "clock": {
            "sample_rate_hz": sample_rate_hz,
            "packet_period_seconds": packet_period_seconds,
            "cursor_seconds": cursor_seconds,
            "anchor_preamble_seconds": anchor["stamp"],
            "anchor_age_seconds": age,
            "lead_seconds": lead_seconds,
            "max_age_seconds": max_age_seconds,
            "max_horizon_seconds": max_horizon_seconds,
            "capture_start_uhd_seconds": capture_start_uhd_seconds,
        },
        "sync": {
            "fhss_index": fields["fhss_index"],
            "nonce": fields["nonce"],
            "switch_mode": fields["switch_mode"],
            "new_tlm_ratio": fields["new_tlm_ratio"],
        },
        "canary": canary_metadata or {
            "kind": "msp_api_version",
            "name": "MSP_API_VERSION",
            "read_only": True,
            "frame_count": len(frames),
        },
        "slots": slots,
        "transport_requirements": [
            "use a newly decoded CRC-valid SYNC from the same receive stream",
            "apply timed retunes before each listed preamble time",
            "abort rather than transmit after the clock age or horizon expires",
            "validate the SDR RF path independently before any live-UID frame",
        ],
    }
    # Preserve the original v1 field for existing MSP-plan consumers.  RC
    # replay plans use the generalized ``canary`` descriptor only.
    if plan["canary"]["kind"] == "msp_api_version":
        plan["command"] = {
            "name": "MSP_API_VERSION",
            "id": 1,
            "read_only": True,
            "fragment_count": len(frames),
        }
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uid-file", required=True, type=Path)
    parser.add_argument("--sync-jsonl", required=True, type=Path)
    parser.add_argument("--canary-plan", required=True, type=Path)
    parser.add_argument("--aux-safety-json", type=Path,
                        help="private TinyHawk AUX evidence required for RC plans")
    parser.add_argument("--private-clock-out", required=True, type=Path)
    parser.add_argument("--anchor-source")
    parser.add_argument("--cursor-seconds", type=float)
    parser.add_argument("--sample-rate", type=float)
    parser.add_argument("--capture-start-uhd-seconds", type=float)
    parser.add_argument("--receive-anchor-json", type=Path,
                        help="private manifest from uhd-elrs-sync-rx")
    parser.add_argument("--nominal-rate-hz", type=float, default=NOMINAL_RATE_HZ)
    parser.add_argument("--lead-ms", type=float, default=100.0)
    parser.add_argument("--max-age-ms", type=float, default=500.0)
    parser.add_argument("--max-horizon-ms", type=float, default=1200.0)
    args = parser.parse_args(argv)
    try:
        private = SCHEDULER["require_private_path"]
        uid_path = private(args.uid_file)
        sync_path = private(args.sync_jsonl)
        canary_path = private(args.canary_plan)
        aux_path = (private(args.aux_safety_json)
                    if args.aux_safety_json is not None else None)
        output_path = private(args.private_clock_out)
        uid = SCHEDULER["FHSS"]["frame_checker"].read_uid(uid_path)
        records = SCHEDULER["read_sync_records"](sync_path)
        clock = fit_packet_clock(records, args.nominal_rate_hz)
        anchor = choose_anchor(records, args.anchor_source)
        canary = json.loads(canary_path.read_text(encoding="utf-8"))
        aux_safety = (RC_CANARY["read_aux_safety"](aux_path)
                      if aux_path is not None else None)
        frames, canary_metadata = validate_canary_input(
            canary, uid, aux_safety)
        if canary_metadata["kind"] == "hybrid_rc_replay":
            sync_ok, sync_reason = RC_CANARY["validate_sync_for_rc_replay"](
                anchor["fields"])
            if not sync_ok:
                raise ValueError(f"fresh SYNC is incompatible with RC replay: "
                                 f"{sync_reason}")
        receive_anchor = None
        cursor_seconds = args.cursor_seconds
        sample_rate = args.sample_rate
        capture_start = args.capture_start_uhd_seconds
        if args.receive_anchor_json is not None:
            if capture_start is not None:
                raise ValueError("do not combine --receive-anchor-json with "
                                 "--capture-start-uhd-seconds")
            receive_anchor_path = private(args.receive_anchor_json)
            receive_anchor = read_receive_anchor(receive_anchor_path)
            if (sample_rate is not None
                    and not math.isclose(sample_rate,
                                         receive_anchor["sample_rate_hz"],
                                         rel_tol=0, abs_tol=0.01)):
                raise ValueError("sample rate disagrees with receive anchor")
            sample_rate = receive_anchor["sample_rate_hz"]
            capture_start = receive_anchor["first_sample_uhd_seconds"]
            if cursor_seconds is None:
                cursor_seconds = receive_anchor["capture_duration_seconds"]
            elif (cursor_seconds < 0
                  or cursor_seconds > receive_anchor["capture_duration_seconds"]):
                raise ValueError("capture cursor lies outside receive anchor")
        if cursor_seconds is None or sample_rate is None:
            raise ValueError("provide --cursor-seconds and --sample-rate, or a "
                             "receive anchor manifest")
        plan = build_fresh_plan(
            uid, anchor, frames,
            cursor_seconds=cursor_seconds,
            sample_rate_hz=sample_rate,
            packet_period_seconds=clock["packet_period_seconds"],
            lead_seconds=args.lead_ms / 1000,
            max_age_seconds=args.max_age_ms / 1000,
            max_horizon_seconds=args.max_horizon_ms / 1000,
            capture_start_uhd_seconds=capture_start,
            canary_metadata=canary_metadata,
        )
        if receive_anchor is not None:
            plan["receive_anchor"] = receive_anchor
        plan["offline_clock_evidence"] = clock
        with output_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(plan, stream, indent=2, sort_keys=True)
            stream.write("\n")
        print(json.dumps({
            "transmit_enabled": False,
            "fresh_sync_validated": True,
            "scheduled_fragments": len(plan["slots"]),
            "canary_kind": canary_metadata["kind"],
            "fitted_rate_hz": round(clock["fitted_rate_hz"], 6),
            "sync_transitions_checked": clock["transitions_checked"],
            "anchor_age_ms": round(plan["clock"]["anchor_age_seconds"] * 1000, 3),
            "has_uhd_time_anchor": capture_start is not None,
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Fresh SYNC clock error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
