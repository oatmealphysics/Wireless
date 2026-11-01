#!/usr/bin/env python3
"""Offline ELRS v3 hybrid-RC replay canary: select a safe captured frame.

Selects an existing CRC-valid RC frame from private captures and validates
it for safe replay (low throttle, disarmed, centered pitch/yaw, unambiguous
AUX).  The selected frame is re-encoded through the validated SX1280 SF7 4/8
long interleaver and the byte-to-symbol-to-byte round trip is verified.

This tool never synthesizes arbitrary control inputs.  It fails closed:
any frame that does not prove low throttle, disarmed, centered pitch/yaw,
bounded roll, and an unused rotating AUX channel is rejected.  AUX evidence
comes from a private, identity-gated Betaflight mode/adjustment snapshot.

The output schema ``elrs-v3-rc-replay-canary-v2`` is a self-contained
single-frame plan.  It is not accepted by the older capture-relative
``elrs-v3-canary-schedule.py``; ``elrs-v3-fresh-sync-clock.py`` is the reviewed
bridge for both this schema and the three-fragment MSP schema.  Use
``validate_rc_canary()`` to validate a saved plan.

All pairing-derived inputs and output belong under the ignored ``work/``
directory.  Default stdout contains only a privacy-safe summary.

Examples::

  python tools/elrs-v3-rc-replay-canary.py \\
      --decoded-jsonl work/decoded-frames.jsonl \\
      --uid-file work/private-uid.hex \\
      --aux-safety-json work/tinyhawk-aux-safety.json

  python tools/elrs-v3-rc-replay-canary.py \\
      --decoded-jsonl work/decoded-frames.jsonl \\
      --uid-file work/private-uid.hex \\
      --aux-safety-json work/tinyhawk-aux-safety.json \\
      --private-plan-out work/rc-replay-canary.json

No serial access, SDR access, RF transmission, configuration changes,
opening, or flashing.  Python 3.10+, standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent

CHECKER = runpy.run_path(str(TOOLS / "elrs-v3-frame-check.py"))
FORWARD = runpy.run_path(str(TOOLS / "elrs-li-forward.py"))

crc14 = CHECKER["crc14"]
uid_crc_init = CHECKER["uid_crc_init"]
read_uid = CHECKER["read_uid"]
encode_symbols = FORWARD["encode_symbols"]
decode_symbols = FORWARD["decode_symbols"]

# --- Betaflight 4.4.2 hybrid-RC constants (source-traced) ---

# The four analog values are unpacked in OTA slot order.  Simultaneous RF and
# MSP_RC evidence for this E8/TinyHawk pair establishes the target's mapping
# as OTA [1, 2, 3, 0] -> MSP [roll, pitch, yaw, throttle].
CH_ROLL = 1
CH_PITCH = 2
CH_YAW = 3
CH_THROTTLE = 0
CH_AUX1 = 4

ANALOG_MIN = 988
ANALOG_MAX = 2011
ANALOG_MID = 1500

THROTTLE_LOW_MAX = 1050

DISARMED_VALUE = 1000
ARMED_VALUE = 2000

PITCH_YAW_CENTER_TOLERANCE = 50

ROLL_MIN_DEVIATION = 50
ROLL_MAX_DEVIATION = 300

ROLL_DISTINCTIVE_THRESHOLD = 50
EXPECTED_RATE_INDEX = 7

# Hybrid switch byte layout (byte 6 of 8-byte std packet):
#   bits [2:0] = switch value (3-bit for index < 6, 4-bit for index >= 6)
#   bits [5:3] = switch index (0..7; index-1 in the round-robin)
#   bit  [6]   = telemetry status / stubborn ack
#   bit  [7]   = ch4 (AUX1 low-latency arm switch)

# 3-bit switch safe values (Betaflight defaults — validate against
# TinyHawk's configured mode ranges before flight-critical decisions):
#   0 = low (1000 PWM), 6/7 = center/default (1500 PWM)
SWITCH_SAFE_VALUES_3B = {0, 6, 7}
# 4-bit switch (index >= 6): only extremes are unambiguous
SWITCH_SAFE_VALUES_4B_LOW = 0
SWITCH_SAFE_VALUES_4B_HIGH = 15
AUX_SAFETY_SCHEMA = "betaflight-aux-safety-v2"
AUX_SAFETY_SCHEMA_LIVE = "betaflight-aux-safety-v3"
EXPECTED_BOARD_IDENTIFIER = "S411"
EXPECTED_CRAFT_NAME = "TH III Plus"
EXPECTED_RC_MAP = (1, 2, 3, 0, 4, 5, 6, 7)


def decode_hybrid_rc(packet: bytes) -> dict | None:
    """Decode Betaflight 4.4.2 hybrid-8 RC fields from an 8-byte OTA packet.

    Returns None if the packet is not an RC type (type bits != 0b00).
    """
    if len(packet) != 8:
        return None
    kind = packet[0] & 0x03
    if kind != 0:
        return None

    packed = int.from_bytes(packet[1:6], "little")
    ota_channels = [988 + ((packed >> (10 * ch)) & 0x3FF)
                    for ch in range(4)]

    switch_byte = packet[6]
    ch4_bit = (switch_byte >> 7) & 1
    aux1 = ARMED_VALUE if ch4_bit else DISARMED_VALUE
    stubborn_ack = (switch_byte >> 6) & 1
    switch_index = (switch_byte >> 3) & 0x07

    if switch_index >= 6:
        switch_value_raw = switch_byte & 0x0F
        switch_width = 4
    else:
        switch_value_raw = switch_byte & 0x07
        switch_width = 3

    return {
        "roll": ota_channels[CH_ROLL],
        "pitch": ota_channels[CH_PITCH],
        "yaw": ota_channels[CH_YAW],
        "throttle": ota_channels[CH_THROTTLE],
        "aux1": aux1,
        "ch4_bit": ch4_bit,
        "switch_index": switch_index,
        "switch_value_raw": switch_value_raw,
        "switch_width": switch_width,
        "stubborn_ack": stubborn_ack,
    }


def is_disarmed(rc: dict) -> bool:
    return rc["ch4_bit"] == 0


def is_low_throttle(rc: dict, threshold: int | None = None) -> bool:
    return rc["throttle"] <= (threshold if threshold is not None
                              else THROTTLE_LOW_MAX)


def is_pitch_yaw_centered(rc: dict,
                           tolerance: int = PITCH_YAW_CENTER_TOLERANCE) -> bool:
    return (abs(rc["pitch"] - ANALOG_MID) <= tolerance and
            abs(rc["yaw"] - ANALOG_MID) <= tolerance)


def _decoded_switch_pwm(rc: dict) -> int:
    """Mirror the source-defined hybrid switch conversion exactly."""
    idx = rc["switch_index"]
    val = rc["switch_value_raw"]
    if idx >= 6:
        return val * 1000 // 15 + 1000
    return (1000, 1275, 1425, 1575, 1725, 2000, 1500, 1500)[val]


def _switch_matches_live_observation(rc: dict, aux_safety: dict | None) -> bool:
    """Allow a non-default switch only when it preserves repeated live MSP_RC."""
    if (not isinstance(aux_safety, dict)
            or aux_safety.get("schema") != AUX_SAFETY_SCHEMA_LIVE):
        return False
    samples = aux_safety.get("observed_rc_samples_us")
    channel_index = rotating_aux_channel_index(rc) + 4
    return (isinstance(samples, list) and len(samples) >= 3
            and all(isinstance(sample, list) and channel_index < len(sample)
                    and sample[channel_index] == _decoded_switch_pwm(rc)
                    for sample in samples))


def is_switch_unambiguous(rc: dict, aux_safety: dict | None = None) -> bool:
    """Accept conservative defaults or a value proven unchanged by live reads."""
    idx = rc["switch_index"]
    val = rc["switch_value_raw"]
    if idx >= 6:
        conservative = val in {
            SWITCH_SAFE_VALUES_4B_LOW, SWITCH_SAFE_VALUES_4B_HIGH}
    else:
        conservative = val in SWITCH_SAFE_VALUES_3B
    return conservative or _switch_matches_live_observation(rc, aux_safety)


def is_roll_in_window(rc: dict) -> bool:
    """Roll deviation must be in the distinctive but not extreme window."""
    dev = abs(rc["roll"] - ANALOG_MID)
    return ROLL_MIN_DEVIATION <= dev <= ROLL_MAX_DEVIATION


def _bounded_int(value: object, name: str, low: int, high: int) -> int:
    if (isinstance(value, bool) or not isinstance(value, int)
            or not low <= value <= high):
        raise ValueError(f"invalid {name}")
    return value


def read_aux_safety(path: Path) -> dict:
    """Validate an identity-gated MSP mode/adjustment range snapshot."""
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(snapshot, dict)
            or snapshot.get("schema") not in {
                AUX_SAFETY_SCHEMA, AUX_SAFETY_SCHEMA_LIVE}
            or snapshot.get("board_identifier") != EXPECTED_BOARD_IDENTIFIER
            or snapshot.get("craft_name") != EXPECTED_CRAFT_NAME):
        raise ValueError("not a TinyHawk AUX safety snapshot")
    modes = snapshot.get("mode_ranges")
    adjustments = snapshot.get("adjustment_ranges")
    if not isinstance(modes, list) or not isinstance(adjustments, list):
        raise ValueError("AUX safety snapshot is missing range arrays")
    rc_map = snapshot.get("rc_map")
    if (not isinstance(rc_map, list) or len(rc_map) != len(EXPECTED_RC_MAP)
            or any(isinstance(value, bool) or not isinstance(value, int)
                   for value in rc_map)
            or tuple(rc_map) != EXPECTED_RC_MAP):
        raise ValueError("AUX safety snapshot does not prove the expected RC map")
    mode_count = snapshot.get("mode_range_slot_count")
    adjustment_count = snapshot.get("adjustment_range_slot_count")
    if (isinstance(mode_count, bool) or not isinstance(mode_count, int)
            or isinstance(adjustment_count, bool)
            or not isinstance(adjustment_count, int)
            or mode_count != len(modes)
            or adjustment_count != len(adjustments)):
        raise ValueError("AUX safety snapshot is incomplete")
    normalized_modes = []
    for entry in modes:
        if not isinstance(entry, dict):
            raise ValueError("invalid mode range entry")
        normalized_modes.append({
            "permanent_id": _bounded_int(entry.get("permanent_id"),
                                          "mode permanent ID", 0, 255),
            "aux_channel_index": _bounded_int(entry.get("aux_channel_index"),
                                               "mode AUX index", 0, 15),
            "start_step": _bounded_int(entry.get("start_step"),
                                       "mode start step", 0, 48),
            "end_step": _bounded_int(entry.get("end_step"),
                                     "mode end step", 0, 48),
            "mode_logic": _bounded_int(entry.get("mode_logic"),
                                       "mode logic", 0, 1),
            "linked_to": _bounded_int(entry.get("linked_to"),
                                      "linked mode ID", 0, 255),
        })
    normalized_adjustments = []
    for entry in adjustments:
        if not isinstance(entry, dict):
            raise ValueError("invalid adjustment range entry")
        normalized_adjustments.append({
            "aux_channel_index": _bounded_int(entry.get("aux_channel_index"),
                                               "adjustment AUX index", 0, 15),
            "start_step": _bounded_int(entry.get("start_step"),
                                       "adjustment start step", 0, 48),
            "end_step": _bounded_int(entry.get("end_step"),
                                     "adjustment end step", 0, 48),
            "aux_switch_channel_index": _bounded_int(
                entry.get("aux_switch_channel_index"),
                "adjustment switch AUX index", 0, 15),
        })
    normalized = {
        "schema": snapshot["schema"],
        "board_identifier": EXPECTED_BOARD_IDENTIFIER,
        "craft_name": EXPECTED_CRAFT_NAME,
        "rc_map": list(EXPECTED_RC_MAP),
        "mode_ranges": normalized_modes,
        "adjustment_ranges": normalized_adjustments,
    }
    if snapshot["schema"] == AUX_SAFETY_SCHEMA_LIVE:
        samples = snapshot.get("observed_rc_samples_us")
        stamps = snapshot.get("observed_wall_time_ns")
        if (not isinstance(samples, list) or len(samples) < 3
                or not isinstance(stamps, list) or len(stamps) != len(samples)
                or any(not isinstance(sample, list) or not 8 <= len(sample) <= 32
                       or any(isinstance(value, bool) or not isinstance(value, int)
                              or not 750 <= value <= 2250 for value in sample)
                       for sample in samples)
                or len({len(sample) for sample in samples}) != 1
                or any(isinstance(stamp, bool) or not isinstance(stamp, int)
                       or stamp <= 0 for stamp in stamps)
                or stamps != sorted(stamps)):
            raise ValueError("live AUX snapshot has invalid RC-state evidence")
        normalized["observed_rc_samples_us"] = samples
        normalized["observed_wall_time_ns"] = stamps
    validate_arm_range(normalized)
    return normalized


def _range_usable(entry: dict) -> bool:
    return entry["start_step"] < entry["end_step"]


def _range_active(entry: dict, channel_value: int) -> bool:
    return (_range_usable(entry)
            and channel_value >= 900 + entry["start_step"] * 25
            and channel_value < 900 + entry["end_step"] * 25)


def validate_arm_range(aux_safety: dict) -> None:
    """Require the live config to prove AUX1-low disarms this TinyHawk."""
    arm = [entry for entry in aux_safety["mode_ranges"]
           if entry["permanent_id"] == 0 and _range_usable(entry)]
    if not arm:
        raise ValueError("AUX snapshot does not contain a usable ARM range")
    if (any(entry["aux_channel_index"] != 0
            or entry["mode_logic"] != 0 or entry["linked_to"] != 0
            or _range_active(entry, DISARMED_VALUE) for entry in arm)
            or not any(_range_active(entry, ARMED_VALUE) for entry in arm)):
        raise ValueError("AUX snapshot does not prove AUX1-low disarms")


def rotating_aux_channel_index(rc: dict) -> int:
    """Map the hybrid switch selector to Betaflight's zero-based AUX index."""
    return 7 if rc["switch_index"] >= 6 else rc["switch_index"] + 1


def rotating_aux_is_unused(rc: dict, aux_safety: dict) -> bool:
    """Require the replayed rotating AUX channel to control no mode/adjustment."""
    aux_index = rotating_aux_channel_index(rc)
    for entry in aux_safety["mode_ranges"]:
        if _range_usable(entry) and entry["aux_channel_index"] == aux_index:
            return False
    for entry in aux_safety["adjustment_ranges"]:
        if (_range_usable(entry)
                and aux_index in (entry["aux_channel_index"],
                                  entry["aux_switch_channel_index"])):
            return False
    return True


def roll_distinctiveness(rc: dict) -> int:
    return abs(rc["roll"] - ANALOG_MID)


def rc_map_is_expected(aux_safety: dict) -> bool:
    mapping = aux_safety.get("rc_map") if isinstance(aux_safety, dict) else None
    return (isinstance(mapping, list)
            and not any(isinstance(value, bool) for value in mapping)
            and tuple(mapping) == EXPECTED_RC_MAP)


def safety_check(rc: dict, aux_safety: dict,
                 throttle_max: int | None = None) -> tuple[bool, str]:
    """Fail-closed safety gate.  Returns (safe, reason)."""
    threshold = THROTTLE_LOW_MAX if throttle_max is None else throttle_max
    if (isinstance(threshold, bool) or not isinstance(threshold, int)
            or not ANALOG_MIN <= threshold <= THROTTLE_LOW_MAX):
        raise ValueError(f"throttle maximum must be in "
                         f"[{ANALOG_MIN}, {THROTTLE_LOW_MAX}]")
    if not rc_map_is_expected(aux_safety):
        return False, "rc_map_unverified"
    if not is_disarmed(rc):
        return False, "armed"
    if not is_low_throttle(rc, threshold):
        return False, f"throttle_too_high ({rc['throttle']})"
    if not is_pitch_yaw_centered(rc):
        return False, (f"pitch_yaw_not_centered "
                       f"(pitch={rc['pitch']}, yaw={rc['yaw']})")
    if not is_roll_in_window(rc):
        return False, f"roll_outside_window ({rc['roll']})"
    if not is_switch_unambiguous(rc, aux_safety):
        return False, (f"ambiguous_switch (index={rc['switch_index']}, "
                       f"value={rc['switch_value_raw']})")
    if not rotating_aux_is_unused(rc, aux_safety):
        return False, (f"rotating_aux_is_configured "
                       f"(aux={rotating_aux_channel_index(rc) + 1})")
    return True, "pass"


def read_decoded_jsonl(path: Path) -> list[dict]:
    """Read CRC-valid RC frames from elrs-li-offline.py JSONL output."""
    frames: list[dict] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("status") != "crc_valid":
                continue
            if item.get("type") != "RC":
                continue
            hex_bytes = item.get("packet_bytes_hex")
            if not isinstance(hex_bytes, str):
                continue
            try:
                packet = bytes.fromhex(hex_bytes)
            except ValueError:
                continue
            if len(packet) != 8:
                continue
            rc = decode_hybrid_rc(packet)
            if rc is None:
                continue
            stamp = item.get("preamble_start_seconds")
            if (isinstance(stamp, bool) or
                    not isinstance(stamp, (int, float)) or
                    not math.isfinite(float(stamp))):
                stamp = None
            frames.append({
                "line": line_number,
                "source": item.get("source", "unknown"),
                "preamble_start_seconds": stamp,
                "packet_hex": hex_bytes,
                "rc": rc,
            })
    return frames


def select_canary(frames: list[dict], aux_safety: dict,
                  throttle_max: int | None = None) -> dict | None:
    """Select the best safe frame for replay canary.

    Priority:
      1. Must pass all safety checks (fail closed)
      2. Require roll in the distinctive window [50, 300]
      3. Prefer higher roll distinctiveness then earliest line
    """
    safe_frames = []
    for frame in frames:
        ok, _ = safety_check(frame["rc"], aux_safety, throttle_max)
        if ok:
            safe_frames.append(frame)

    if not safe_frames:
        return None

    safe_frames.sort(key=lambda f: (-roll_distinctiveness(f["rc"]), f["line"]))
    return safe_frames[0]


def rejection_counts(frames: list[dict], aux_safety: dict,
                     throttle_max: int | None = None) -> dict[str, int]:
    """Return privacy-safe first-failure counts for rejected candidates."""
    counts: dict[str, int] = {}
    for frame in frames:
        ok, reason = safety_check(frame["rc"], aux_safety, throttle_max)
        if ok:
            continue
        label = reason.split(" (", 1)[0]
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def verify_crc(packet: bytes, uid: bytes, switch_mode: str = "hybrid") -> bool:
    """Verify CRC14 of an 8-byte v3 RC frame using the shared implementation."""
    if len(packet) != 8 or len(uid) != 6:
        return False
    kind = packet[0] & 0x03
    if kind != 0:
        return False
    initial = uid_crc_init(uid)
    calc_data = bytes([kind]) + packet[1:7]
    calc = crc14(calc_data, initial)
    stored = (((packet[0] >> 2) & 0x3F) << 8) | packet[7]
    return calc == stored


def verify_interleaver_round_trip(packet: bytes) -> dict:
    """Encode and decode through the long interleaver, verify recovery."""
    symbols = encode_symbols(packet)
    recovered = decode_symbols(symbols)
    passed = (recovered is not None
              and recovered[0] == packet
              and recovered[1] == 0)
    return {
        "symbols": symbols,
        "round_trip_pass": passed,
        "symbol_count": len(symbols),
    }


def validate_sync_for_rc_replay(sync_fields: dict) -> tuple[bool, str]:
    """Validate that a SYNC record is suitable for RC replay scheduling.

    Requirements:
      - switch_mode must be "hybrid" (not "wide")
      - new_tlm_ratio must be 0 (no telemetry expected)
    """
    if sync_fields.get("switch_mode") != "hybrid":
        return False, (f"switch_mode must be hybrid, "
                       f"got {sync_fields.get('switch_mode')!r}")
    ratio = sync_fields.get("new_tlm_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, int) or ratio != 0:
        return False, (f"telemetry ratio must be 0 (disabled), "
                       f"got {ratio!r}")
    rate_index = sync_fields.get("rate_index")
    if (isinstance(rate_index, bool) or not isinstance(rate_index, int)
            or rate_index != EXPECTED_RATE_INDEX):
        return False, (f"rate_index must be {EXPECTED_RATE_INDEX} "
                       f"(LoRa 150 Hz), got {rate_index!r}")
    return True, "pass"


def validate_rc_canary(plan: object, uid: bytes, aux_safety: dict) -> dict:
    """Validate a saved RC replay canary plan.

    Returns the normalized frame dict on success; raises ValueError on
    any structural or content violation.
    """
    if not isinstance(plan, dict):
        raise ValueError("canary plan must be an object")
    if plan.get("schema") != "elrs-v3-rc-replay-canary-v2":
        raise ValueError("not an RC replay canary plan")
    if plan.get("transmit_enabled") is not False:
        raise ValueError("plan must have transmit_enabled=false")
    frames = plan.get("frames")
    if not isinstance(frames, list) or len(frames) != 1:
        raise ValueError("plan must contain exactly one frame")
    frame = frames[0]
    if not isinstance(frame, dict):
        raise ValueError("frame must be an object")
    required = ("packet_hex", "symbols", "source_line", "source_file")
    for key in required:
        if key not in frame:
            raise ValueError(f"frame missing required key: {key}")
    try:
        packet = bytes.fromhex(frame["packet_hex"])
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid packet hex") from exc
    symbols = frame["symbols"]
    if (len(packet) != 8 or not isinstance(symbols, list)
            or len(symbols) != 21
            or any(isinstance(v, bool) or not isinstance(v, int)
                   or not 0 <= v < 128 for v in symbols)):
        raise ValueError("invalid packet or symbol dimensions")
    if not verify_crc(packet, uid):
        raise ValueError("plan frame CRC does not match the supplied UID")
    rc = decode_hybrid_rc(packet)
    if rc is None:
        raise ValueError("plan frame is not hybrid RC data")
    expected_symbols = encode_symbols(packet)
    if symbols != expected_symbols:
        raise ValueError("plan symbols do not encode the plan packet")
    safety = plan.get("safety")
    if not isinstance(safety, dict):
        raise ValueError("plan missing safety block")
    throttle_max = safety.get("throttle_low_max")
    ok, reason = safety_check(rc, aux_safety, throttle_max)
    if not ok:
        raise ValueError(f"plan frame failed recomputed safety: {reason}")
    expected_safety = {
        "throttle": rc["throttle"],
        "throttle_low": True,
        "throttle_low_max": throttle_max,
        "disarmed": True,
        "pitch_yaw_centered": True,
        "pitch_yaw_tolerance": PITCH_YAW_CENTER_TOLERANCE,
        "switch_unambiguous": True,
        "switch_index": rc["switch_index"],
        "switch_value_raw": rc["switch_value_raw"],
        "switch_width": rc["switch_width"],
        "roll_in_window": True,
        "roll_window": [ROLL_MIN_DEVIATION, ROLL_MAX_DEVIATION],
        "rotating_aux_channel_index": rotating_aux_channel_index(rc),
        "rotating_aux_unused": True,
        "rc_map_verified": True,
    }
    if safety != expected_safety:
        raise ValueError("serialized safety evidence disagrees with the packet")
    aux_evidence = plan.get("aux_safety_evidence")
    if aux_evidence != {
            "schema": AUX_SAFETY_SCHEMA,
            "board_identifier": EXPECTED_BOARD_IDENTIFIER,
            "craft_name": EXPECTED_CRAFT_NAME,
            "arm_range_proven": True,
            "rc_map": list(EXPECTED_RC_MAP),
            "rotating_aux_channel_index": rotating_aux_channel_index(rc),
            "rotating_aux_unused": True}:
        raise ValueError("plan AUX evidence is missing or inconsistent")
    expected_channels = {
        "roll": rc["roll"],
        "pitch": rc["pitch"],
        "yaw": rc["yaw"],
        "throttle": rc["throttle"],
        "aux1_armed": False,
        "roll_distinctiveness": roll_distinctiveness(rc),
    }
    if plan.get("rc_channels") != expected_channels:
        raise ValueError("serialized RC channels disagree with the packet")
    if plan.get("air") != {
            "sf": 7,
            "bandwidth_hz": 812_500,
            "coding": "SX1280 4/8 long interleaving",
            "implicit_payload_bytes": 8,
            "preamble_symbols": 12}:
        raise ValueError("plan PHY contract is invalid")
    if plan.get("interleaver_verification") != {
            "round_trip_pass": True,
            "symbol_count": 21}:
        raise ValueError("plan interleaver evidence is invalid")
    sync_req = plan.get("sync_requirements")
    if not isinstance(sync_req, dict):
        raise ValueError("plan missing sync_requirements")
    if not sync_req.get("fresh_sync_required"):
        raise ValueError("plan must require fresh SYNC")
    if sync_req.get("switch_mode") != "hybrid":
        raise ValueError("plan must require hybrid switch mode")
    if not sync_req.get("telemetry_ratio_must_be_zero"):
        raise ValueError("plan must require zero telemetry ratio")
    return {
        "packet_hex": frame["packet_hex"],
        "symbols": symbols,
        "source_line": frame["source_line"],
        "source_file": frame["source_file"],
    }


def require_private_path(path: Path) -> Path:
    """Reject pairing-sensitive files outside the repository's work tree."""
    work = (ROOT / "work").resolve()
    target = path.resolve()
    try:
        target.relative_to(work)
    except ValueError as exc:
        raise ValueError(
            "private paths must be under the ignored work/ directory"
        ) from exc
    return target


def build_plan(uid: bytes, frame: dict, interleaver: dict,
               aux_safety: dict, throttle_max: int = THROTTLE_LOW_MAX) -> dict:
    """Build the canary plan with a single-frame ``frames`` list."""
    rc = frame["rc"]
    packet = bytes.fromhex(frame["packet_hex"])
    ok, reason = safety_check(rc, aux_safety, throttle_max)
    if not ok:
        raise ValueError(f"refusing unsafe RC canary: {reason}")
    if not verify_crc(packet, uid):
        raise ValueError("refusing RC canary with invalid UID-derived CRC")
    if (not interleaver.get("round_trip_pass")
            or interleaver.get("symbols") != encode_symbols(packet)):
        raise ValueError("refusing RC canary with inconsistent symbols")
    return {
        "schema": "elrs-v3-rc-replay-canary-v2",
        "transmit_enabled": False,
        "safety": {
            "throttle": rc["throttle"],
            "throttle_low": is_low_throttle(rc, throttle_max),
            "throttle_low_max": throttle_max,
            "disarmed": is_disarmed(rc),
            "pitch_yaw_centered": is_pitch_yaw_centered(rc),
            "pitch_yaw_tolerance": PITCH_YAW_CENTER_TOLERANCE,
            "switch_unambiguous": is_switch_unambiguous(rc, aux_safety),
            "switch_index": rc["switch_index"],
            "switch_value_raw": rc["switch_value_raw"],
            "switch_width": rc["switch_width"],
            "roll_in_window": is_roll_in_window(rc),
            "roll_window": [ROLL_MIN_DEVIATION, ROLL_MAX_DEVIATION],
            "rotating_aux_channel_index": rotating_aux_channel_index(rc),
            "rotating_aux_unused": rotating_aux_is_unused(rc, aux_safety),
            "rc_map_verified": rc_map_is_expected(aux_safety),
        },
        "rc_channels": {
            "roll": rc["roll"],
            "pitch": rc["pitch"],
            "yaw": rc["yaw"],
            "throttle": rc["throttle"],
            "aux1_armed": not is_disarmed(rc),
            "roll_distinctiveness": roll_distinctiveness(rc),
        },
        "frames": [{
            "source_line": frame["line"],
            "source_file": frame["source"],
            "preamble_start_seconds": frame["preamble_start_seconds"],
            "packet_hex": frame["packet_hex"],
            "symbols": interleaver["symbols"],
        }],
        "air": {
            "sf": 7,
            "bandwidth_hz": 812_500,
            "coding": "SX1280 4/8 long interleaving",
            "implicit_payload_bytes": 8,
            "preamble_symbols": 12,
        },
        "interleaver_verification": {
            "round_trip_pass": interleaver["round_trip_pass"],
            "symbol_count": interleaver["symbol_count"],
        },
        "aux_safety_evidence": {
            "schema": AUX_SAFETY_SCHEMA,
            "board_identifier": EXPECTED_BOARD_IDENTIFIER,
            "craft_name": EXPECTED_CRAFT_NAME,
            "arm_range_proven": True,
            "rc_map": list(EXPECTED_RC_MAP),
            "rotating_aux_channel_index": rotating_aux_channel_index(rc),
            "rotating_aux_unused": True,
        },
        "sync_requirements": {
            "fresh_sync_required": True,
            "switch_mode": "hybrid",
            "telemetry_ratio_must_be_zero": True,
            "rationale": (
                "A replay is only safe when the link is in hybrid switch "
                "mode (no wide mode), the latest SYNC confirms no telemetry "
                "(ratio=0), and the SYNC is fresh (not stale from a prior "
                "session)."
            ),
        },
        "limitations": [
            "single-frame replay; not a sustained control takeover",
            "capture-relative timestamp only; not a live hardware clock",
            "receiver acceptance of a replayed frame is not established",
            "SDR latency, oscillator error, and transmit polarity are not calibrated",
            "no serial access, SDR access, RF transmission, or device changes",
            "the selected rotating AUX channel was unused in the supplied live mode/adjustment snapshot",
        ],
    }


def privacy_safe_summary(frame: dict, interleaver: dict,
                         total_rc: int, safe_count: int) -> dict:
    """Build a summary that omits UID, packet bytes, and symbols."""
    rc = frame["rc"]
    return {
        "schema": "elrs-v3-rc-replay-canary-v2",
        "transmit_enabled": False,
        "total_rc_frames": total_rc,
        "safe_frames": safe_count,
        "selected_source_line": frame["line"],
        "safety_pass": True,
        "throttle": rc["throttle"],
        "throttle_low": True,
        "disarmed": True,
        "pitch_yaw_centered": True,
        "rc_map_verified": True,
        "switch_unambiguous": True,
        "rotating_aux_unused": True,
        "roll": rc["roll"],
        "roll_distinctiveness": roll_distinctiveness(rc),
        "roll_in_window": is_roll_in_window(rc),
        "interleaver_round_trip": interleaver["round_trip_pass"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--decoded-jsonl", required=True, type=Path,
        help="JSONL from elrs-li-offline.py with --include-bytes (private)",
    )
    parser.add_argument(
        "--uid-file", required=True, type=Path,
        help="private six-byte UID hex file",
    )
    parser.add_argument(
        "--aux-safety-json", required=True, type=Path,
        help="private identity-gated mode/adjustment range snapshot",
    )
    parser.add_argument(
        "--private-plan-out", type=Path,
        help="new ignored JSON path for the full plan; never overwritten",
    )
    parser.add_argument(
        "--throttle-max", type=int, default=THROTTLE_LOW_MAX,
        help=f"maximum throttle PWM to consider safe (default: {THROTTLE_LOW_MAX})",
    )
    args = parser.parse_args(argv)

    try:
        uid_path = require_private_path(args.uid_file)
        jsonl_path = require_private_path(args.decoded_jsonl)
        aux_path = require_private_path(args.aux_safety_json)
        uid = read_uid(uid_path)
        aux_safety = read_aux_safety(aux_path)

        throttle_max = args.throttle_max
        if (isinstance(throttle_max, bool)
                or not ANALOG_MIN <= throttle_max <= THROTTLE_LOW_MAX):
            raise ValueError(
                f"--throttle-max must be in [{ANALOG_MIN}, {THROTTLE_LOW_MAX}]"
            )

        print(f"Reading decoded RC frames from {jsonl_path.name}",
              file=sys.stderr)
        frames = read_decoded_jsonl(jsonl_path)
        if not frames:
            print("No CRC-valid RC frames with packet bytes found",
                  file=sys.stderr)
            return 1

        verified = []
        for frame in frames:
            packet = bytes.fromhex(frame["packet_hex"])
            if verify_crc(packet, uid):
                verified.append(frame)
        if not verified:
            print("No RC frames pass CRC with the provided UID",
                  file=sys.stderr)
            return 1

        safe_count = sum(1 for f in verified
                         if safety_check(f["rc"], aux_safety, throttle_max)[0])

        selected = select_canary(verified, aux_safety, throttle_max)
        if selected is None:
            counts = rejection_counts(verified, aux_safety, throttle_max)
            print("No RC frames pass all safety checks (fail closed):\n"
                  "  - All frames are either armed, throttle too high, "
                  "pitch/yaw off center, roll outside the bounded window, "
                  "or use a configured rotating AUX channel",
                  file=sys.stderr)
            for reason, count in counts.items():
                print(f"  - {reason}: {count}", file=sys.stderr)
            return 1

        packet = bytes.fromhex(selected["packet_hex"])
        interleaver = verify_interleaver_round_trip(packet)
        if not interleaver["round_trip_pass"]:
            print("FATAL: interleaver round trip failed", file=sys.stderr)
            return 1

        summary = privacy_safe_summary(
            selected, interleaver, len(verified), safe_count
        )
        print(json.dumps(summary, sort_keys=True))

        if args.private_plan_out:
            plan_path = require_private_path(args.private_plan_out)
            plan = build_plan(uid, selected, interleaver,
                              aux_safety, throttle_max)
            validate_rc_canary(plan, uid, aux_safety)
            with plan_path.open("x", encoding="utf-8", newline="\n") as f:
                json.dump(plan, f, indent=2, sort_keys=True)
                f.write("\n")
            print(f"Private plan written to {plan_path.name}",
                  file=sys.stderr)

        return 0

    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as exc:
        print(f"RC replay canary error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
