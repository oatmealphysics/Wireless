#!/usr/bin/env python3
"""
ELRS Telemetry Interception Analysis

ELRS v3 telemetry is transmitted in CLEARTEXT with no encryption,
no authentication, and no integrity protection. Any passive receiver
on the correct FHSS channel can capture telemetry data including:
  - GPS coordinates (latitude, longitude, altitude, speed, heading)
  - Battery voltage, current, fuel level
  - Attitude (pitch, roll, yaw)
  - Flight mode and arm status
  - RSSI, LQ, SNR (link quality metrics)
  - Variometer (climb/descent rate)

The telemetry path:
  FC → CRSF (type 0x02/0x07/0x08/etc.) → ELRS TX → OTA (cleartext)

Telemetry frames use the same CRC14 as RC data, which is derived from
the UID. Since the CRC init is static (no nonce in v3), an attacker
who knows UID[4:5] (from SYNC capture) can validate telemetry frames
and decode all sensor data.

CRSF telemetry frame types:
  0x02: GPS
  0x07: Vario (vertical speed)
  0x08: Battery sensor
  0x09: Baro altitude
  0x0B: Heartbeat
  0x14: Link statistics
  0x1E: Attitude (pitch/roll/yaw)
  0x21: Flight mode (string)
  0x29: Device info

Privacy implications:
  - Real-time drone GPS tracking from passive RF capture
  - Battery monitoring reveals flight time remaining
  - Attitude data reveals maneuvers and flight pattern
  - Flight mode reveals armed/disarmed state
  - Link stats reveal pilot distance and orientation

Usage:
  python3 elrs-telemetry-intercept.py --analyze
  python3 elrs-telemetry-intercept.py --decode-formats
  python3 elrs-telemetry-intercept.py --privacy-impact
  python3 elrs-telemetry-intercept.py --chain

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
# CRSF telemetry frame definitions
# ---------------------------------------------------------------------------

CRSF_TELEMETRY_TYPES = {
    0x02: {
        "name": "GPS",
        "fields": [
            {"name": "latitude", "type": "int32", "unit": "degrees * 1e7",
             "privacy": "CRITICAL — real-time location tracking"},
            {"name": "longitude", "type": "int32", "unit": "degrees * 1e7",
             "privacy": "CRITICAL — real-time location tracking"},
            {"name": "groundspeed", "type": "uint16", "unit": "km/h * 10",
             "privacy": "MEDIUM — movement speed"},
            {"name": "heading", "type": "uint16", "unit": "degrees * 100",
             "privacy": "MEDIUM — travel direction"},
            {"name": "altitude", "type": "uint16", "unit": "meters + 1000",
             "privacy": "HIGH — altitude reveals location context"},
            {"name": "satellites", "type": "uint8", "unit": "count",
             "privacy": "LOW — GPS fix quality"},
        ],
        "size": 15,
        "privacy_rating": "CRITICAL",
    },
    0x07: {
        "name": "Vario",
        "fields": [
            {"name": "vertical_speed", "type": "int16", "unit": "cm/s",
             "privacy": "LOW — climb/descent rate"},
        ],
        "size": 2,
        "privacy_rating": "LOW",
    },
    0x08: {
        "name": "Battery",
        "fields": [
            {"name": "voltage", "type": "uint16", "unit": "V * 10",
             "privacy": "MEDIUM — reveals battery state"},
            {"name": "current", "type": "uint16", "unit": "A * 10",
             "privacy": "LOW — current draw"},
            {"name": "capacity", "type": "uint24", "unit": "mAh",
             "privacy": "MEDIUM — fuel remaining → flight time estimate"},
            {"name": "remaining", "type": "uint8", "unit": "percent",
             "privacy": "HIGH — directly reveals when drone must land"},
        ],
        "size": 8,
        "privacy_rating": "MEDIUM",
    },
    0x09: {
        "name": "Baro Altitude",
        "fields": [
            {"name": "altitude", "type": "uint16", "unit": "dm + 10000",
             "privacy": "MEDIUM — altitude without GPS"},
        ],
        "size": 2,
        "privacy_rating": "MEDIUM",
    },
    0x14: {
        "name": "Link Statistics",
        "fields": [
            {"name": "uplink_rssi_ant1", "type": "uint8", "unit": "dBm (negative)",
             "privacy": "MEDIUM — pilot distance estimate"},
            {"name": "uplink_rssi_ant2", "type": "uint8", "unit": "dBm (negative)",
             "privacy": "MEDIUM — pilot distance estimate"},
            {"name": "uplink_lq", "type": "uint8", "unit": "percent",
             "privacy": "LOW — link quality"},
            {"name": "uplink_snr", "type": "int8", "unit": "dB",
             "privacy": "LOW — signal quality"},
            {"name": "active_antenna", "type": "uint8", "unit": "index",
             "privacy": "LOW"},
            {"name": "rf_mode", "type": "uint8", "unit": "enum",
             "privacy": "LOW — reveals ELRS rate mode"},
            {"name": "uplink_tx_power", "type": "uint8", "unit": "enum",
             "privacy": "MEDIUM — TX power → range estimate"},
            {"name": "downlink_rssi", "type": "uint8", "unit": "dBm (negative)",
             "privacy": "MEDIUM — drone distance from pilot"},
            {"name": "downlink_lq", "type": "uint8", "unit": "percent",
             "privacy": "LOW"},
            {"name": "downlink_snr", "type": "int8", "unit": "dB",
             "privacy": "LOW"},
        ],
        "size": 10,
        "privacy_rating": "MEDIUM",
    },
    0x1E: {
        "name": "Attitude",
        "fields": [
            {"name": "pitch", "type": "int16", "unit": "radians * 10000",
             "privacy": "LOW — drone orientation"},
            {"name": "roll", "type": "int16", "unit": "radians * 10000",
             "privacy": "LOW — drone orientation"},
            {"name": "yaw", "type": "int16", "unit": "radians * 10000",
             "privacy": "MEDIUM — heading without GPS"},
        ],
        "size": 6,
        "privacy_rating": "LOW",
    },
    0x21: {
        "name": "Flight Mode",
        "fields": [
            {"name": "mode_string", "type": "string", "unit": "text",
             "privacy": "HIGH — reveals armed state, flight mode"},
        ],
        "size": "variable (null-terminated string)",
        "privacy_rating": "HIGH",
    },
    0x29: {
        "name": "Device Info",
        "fields": [
            {"name": "device_name", "type": "string", "unit": "text",
             "privacy": "MEDIUM — reveals hardware model"},
            {"name": "serial_number", "type": "uint32", "unit": "serial",
             "privacy": "HIGH — unique device identifier"},
            {"name": "hardware_version", "type": "uint32", "unit": "version",
             "privacy": "LOW"},
            {"name": "software_version", "type": "uint32", "unit": "version",
             "privacy": "LOW — but reveals vulnerability surface"},
        ],
        "size": "variable",
        "privacy_rating": "MEDIUM",
    },
}

# Telemetry ratio options in ELRS
TELEM_RATIOS = {
    "Off": {"ratio": 0, "description": "No telemetry sent"},
    "1:128": {"ratio": 128, "description": "1 telemetry frame per 128 RC frames"},
    "1:64": {"ratio": 64, "description": "1 per 64"},
    "1:32": {"ratio": 32, "description": "1 per 32"},
    "1:16": {"ratio": 16, "description": "1 per 16"},
    "1:8": {"ratio": 8, "description": "1 per 8"},
    "1:4": {"ratio": 4, "description": "1 per 4"},
    "1:2": {"ratio": 2, "description": "1 per 2 — highest telemetry rate"},
}


def crc14_calc(data: bytes, crc_init: int) -> int:
    """Calculate CRC-14 used by ELRS."""
    crc = crc_init & 0x3FFF
    for byte in data:
        crc ^= byte << 6
        for _ in range(8):
            if crc & 0x2000:
                crc = ((crc << 1) ^ 0x2E57) & 0x3FFF
            else:
                crc = (crc << 1) & 0x3FFF
    return crc


def decode_gps_frame(payload: bytes) -> dict:
    """Decode a CRSF GPS telemetry frame payload."""
    if len(payload) < 15:
        return {"error": f"GPS frame too short: {len(payload)} < 15"}

    lat = struct.unpack(">i", payload[0:4])[0]
    lon = struct.unpack(">i", payload[4:8])[0]
    speed = struct.unpack(">H", payload[8:10])[0]
    heading = struct.unpack(">H", payload[10:12])[0]
    alt = struct.unpack(">H", payload[12:14])[0]
    sats = payload[14]

    return {
        "latitude": lat / 1e7,
        "longitude": lon / 1e7,
        "groundspeed_kmh": speed / 10.0,
        "heading_deg": heading / 100.0,
        "altitude_m": alt - 1000,
        "satellites": sats,
    }


def decode_battery_frame(payload: bytes) -> dict:
    """Decode a CRSF battery telemetry frame payload."""
    if len(payload) < 8:
        return {"error": f"Battery frame too short: {len(payload)} < 8"}

    voltage = struct.unpack(">H", payload[0:2])[0]
    current = struct.unpack(">H", payload[2:4])[0]
    capacity = (payload[4] << 16) | (payload[5] << 8) | payload[6]
    remaining = payload[7]

    return {
        "voltage_v": voltage / 10.0,
        "current_a": current / 10.0,
        "capacity_mah": capacity,
        "remaining_pct": remaining,
    }


def decode_attitude_frame(payload: bytes) -> dict:
    """Decode a CRSF attitude telemetry frame payload."""
    if len(payload) < 6:
        return {"error": f"Attitude frame too short: {len(payload)} < 6"}

    pitch = struct.unpack(">h", payload[0:2])[0]
    roll = struct.unpack(">h", payload[2:4])[0]
    yaw = struct.unpack(">h", payload[4:6])[0]

    import math
    return {
        "pitch_deg": pitch / 10000.0 * (180.0 / math.pi),
        "roll_deg": roll / 10000.0 * (180.0 / math.pi),
        "yaw_deg": yaw / 10000.0 * (180.0 / math.pi),
    }


def decode_link_stats_frame(payload: bytes) -> dict:
    """Decode a CRSF link statistics frame payload."""
    if len(payload) < 10:
        return {"error": f"Link stats frame too short: {len(payload)} < 10"}

    return {
        "uplink_rssi_1_dbm": -payload[0],
        "uplink_rssi_2_dbm": -payload[1],
        "uplink_lq_pct": payload[2],
        "uplink_snr_db": struct.unpack("b", bytes([payload[3]]))[0],
        "active_antenna": payload[4],
        "rf_mode": payload[5],
        "uplink_tx_power_idx": payload[6],
        "downlink_rssi_dbm": -payload[7],
        "downlink_lq_pct": payload[8],
        "downlink_snr_db": struct.unpack("b", bytes([payload[9]]))[0],
    }


DECODERS = {
    0x02: decode_gps_frame,
    0x08: decode_battery_frame,
    0x1E: decode_attitude_frame,
    0x14: decode_link_stats_frame,
}


def analyze_vulnerability():
    """Analyze telemetry interception vulnerability."""
    print(f"\n{'='*65}")
    print("ELRS v3 TELEMETRY INTERCEPTION ANALYSIS")
    print(f"{'='*65}")

    print(f"\n  Protocol properties (telemetry path):")
    print(f"    Encryption: NONE — all telemetry in cleartext")
    print(f"    Authentication: CRC-14 only (static init, not a MAC)")
    print(f"    Integrity: NONE — CRC is not integrity protection")
    print(f"    Replay protection: NONE — no nonce in v3")
    print(f"    FHSS: Predictable (MINSTD LCG from UID)")

    print(f"\n  Telemetry direction: FC → ELRS RX → OTA → ELRS TX → Radio")
    print(f"  A passive receiver on the correct channel captures ALL telemetry")

    print(f"\n  Telemetry frame types ({len(CRSF_TELEMETRY_TYPES)} types):")
    for ftype, info in CRSF_TELEMETRY_TYPES.items():
        print(f"    0x{ftype:02X}: {info['name']:20s}  Privacy: {info['privacy_rating']}")

    print(f"\n  Interception requirements:")
    print(f"    1. Know the FHSS sequence (from UID[2:5] + OTA version)")
    print(f"    2. Tune to the correct hop frequency at the correct time")
    print(f"    3. Receive during telemetry slot (based on telem ratio)")
    print(f"    4. CRC-14 validation optional (cleartext regardless)")
    print(f"    5. Decode CRSF frame format (documented, open-source)")


def decode_formats():
    """Document all telemetry frame decode formats."""
    print(f"\n{'='*65}")
    print("CRSF TELEMETRY FRAME FORMATS")
    print(f"{'='*65}")

    for ftype, info in CRSF_TELEMETRY_TYPES.items():
        print(f"\n  0x{ftype:02X}: {info['name']} (size: {info['size']})")
        print(f"  Privacy rating: {info['privacy_rating']}")
        for field in info["fields"]:
            print(f"    {field['name']:25s}  {field['type']:8s}  "
                  f"({field['unit']})")
            print(f"      Privacy: {field['privacy']}")

    # Show example decode
    print(f"\n  EXAMPLE GPS DECODE:")
    example_gps = bytes([
        0x15, 0xCD, 0x5B, 0x07,  # lat: 364870407 → 36.4870407
        0xD6, 0xE7, 0xD0, 0xB4,  # lon: -690000716 → -69.0000716 (example)
        0x00, 0xC8,              # speed: 200 → 20.0 km/h
        0x0E, 0x10,              # heading: 3600 → 36.0 degrees
        0x04, 0x1A,              # alt: 1050 → 50m
        0x0C,                    # sats: 12
    ])
    decoded = decode_gps_frame(example_gps)
    print(f"    Raw: {example_gps.hex()}")
    for k, v in decoded.items():
        print(f"    {k}: {v}")

    print(f"\n  EXAMPLE BATTERY DECODE:")
    example_batt = bytes([
        0x00, 0xA6,  # voltage: 166 → 16.6V (4S)
        0x00, 0x32,  # current: 50 → 5.0A
        0x00, 0x02, 0x58,  # capacity: 600 mAh used
        0x4B,        # remaining: 75%
    ])
    decoded = decode_battery_frame(example_batt)
    print(f"    Raw: {example_batt.hex()}")
    for k, v in decoded.items():
        print(f"    {k}: {v}")


def analyze_privacy():
    """Analyze privacy implications of telemetry interception."""
    print(f"\n{'='*65}")
    print("PRIVACY IMPACT ANALYSIS")
    print(f"{'='*65}")

    print(f"\n  REAL-TIME TRACKING CAPABILITY:")
    print(f"    GPS telemetry (type 0x02) provides:")
    print(f"      - Latitude/longitude to ~1m precision")
    print(f"      - Altitude")
    print(f"      - Groundspeed and heading")
    print(f"      - Update rate depends on telem ratio")
    print(f"    At 150 Hz with 1:8 ratio → GPS update every 53ms")
    print(f"    At 150 Hz with 1:32 ratio → GPS update every 213ms")

    print(f"\n  OPERATIONAL INTELLIGENCE:")
    print(f"    Battery telemetry reveals:")
    print(f"      - Current voltage → state of charge")
    print(f"      - mAh consumed → flight time remaining")
    print(f"      - Current draw → flight intensity (acro vs cruise)")
    print(f"    Flight mode string reveals:")
    print(f"      - Armed/disarmed state")
    print(f"      - Current flight mode (ACRO, ANGLE, HORIZON, etc.)")
    print(f"      - Failsafe status")

    print(f"\n  DEVICE FINGERPRINTING:")
    print(f"    Device info (type 0x29) reveals:")
    print(f"      - Hardware model name")
    print(f"      - Serial number (unique identifier)")
    print(f"      - Firmware versions")
    print(f"    Combined with UID from SYNC → persistent device tracking")

    print(f"\n  LINK QUALITY INTELLIGENCE:")
    print(f"    Link stats (type 0x14) reveal:")
    print(f"      - RSSI → distance estimation")
    print(f"      - TX power level → range capability")
    print(f"      - Active antenna → orientation")
    print(f"      - RF mode → data rate configuration")

    print(f"\n  TELEMETRY RATIO TABLE:")
    for name, info in TELEM_RATIOS.items():
        if info["ratio"] > 0:
            interval_ms = info["ratio"] / 150.0 * 1000.0  # at 150Hz
            print(f"    {name:8s}: 1 telem frame per {info['ratio']:3d} RC frames "
                  f"(~{interval_ms:.0f}ms at 150Hz)")
        else:
            print(f"    {name:8s}: No telemetry")

    print(f"\n  PASSIVE COLLECTION SCENARIO:")
    print(f"    An attacker with an SDR (HackRF, LimeSDR, etc.) at a")
    print(f"    flying field can passively collect telemetry from all")
    print(f"    ELRS-equipped drones. With the FHSS sequence known")
    print(f"    (from SYNC capture → UID → seed), the attacker tunes")
    print(f"    to each target's hop frequency and records telemetry")
    print(f"    frames. No transmission needed — purely passive.")
    print(f"    All data is cleartext. CRC validation confirms the")
    print(f"    frame belongs to the target. Over a flying session,")
    print(f"    the attacker builds a complete GPS track, battery")
    print(f"    profile, and operational timeline of every drone.")


def analyze_chains():
    """Analyze attack chains using telemetry interception."""
    print(f"\n{'='*65}")
    print("TELEMETRY INTERCEPTION ATTACK CHAINS")
    print(f"{'='*65}")

    chains = [
        {
            "id": "TELEM-CHAIN-1",
            "name": "Passive GPS tracking",
            "severity": "HIGH",
            "steps": [
                "1. Capture SYNC packet → extract UID[3:5]",
                "2. Derive FHSS sequence from UID (see elrs-fhss-seed-resolve.py)",
                "3. Tune SDR to each hop frequency during telemetry slots",
                "4. Decode GPS frames (type 0x02) → lat/lon/alt/speed",
                "5. Build real-time GPS track of target drone",
                "6. Continuous tracking until drone lands",
            ],
            "impact": "Real-time location tracking, flight path recording",
        },
        {
            "id": "TELEM-CHAIN-2",
            "name": "Battery depletion monitoring → timed attack",
            "severity": "MEDIUM",
            "steps": [
                "1. Passively intercept battery telemetry (type 0x08)",
                "2. Monitor voltage curve and remaining percentage",
                "3. Predict time-to-landing from capacity consumed",
                "4. When battery is low (pilot must land soon):",
                "   - Initiate session hijack (see elrs-session-hijack.py)",
                "   - Or: jam return-to-home frequency",
                "5. Pilot under pressure, drone running out of power",
            ],
            "impact": "Timed attack when pilot is most vulnerable",
        },
        {
            "id": "TELEM-CHAIN-3",
            "name": "Telemetry forgery (inject false telemetry)",
            "severity": "HIGH",
            "steps": [
                "1. Know UID → derive CRC-14 initializer",
                "2. Know FHSS sequence → transmit on correct channel",
                "3. During telemetry slot, transmit forged GPS frame",
                "4. Pilot's radio displays false GPS position",
                "5. Or: forge battery frame showing 0% → pilot panic-lands",
                "6. Or: forge flight mode showing FAILSAFE → pilot confusion",
            ],
            "impact": "Pilot misinformation, forced landing via false telemetry",
        },
        {
            "id": "TELEM-CHAIN-4",
            "name": "Multi-target passive surveillance",
            "severity": "HIGH",
            "steps": [
                "1. Deploy SDR at flying field / drone race venue",
                "2. Capture SYNC from each active ELRS drone",
                "3. Derive FHSS sequence for each target",
                "4. Time-multiplex between targets' telemetry slots",
                "5. Build simultaneous GPS tracks for all drones",
                "6. Correlate with pilot locations (from TX RSSI patterns)",
                "7. Persistent surveillance database",
            ],
            "impact": "Mass drone surveillance at events, zero transmission needed",
        },
    ]

    for chain in chains:
        print(f"\n  [{chain['severity']}] {chain['id']}: {chain['name']}")
        for step in chain["steps"]:
            print(f"    {step}")
        print(f"    Impact: {chain['impact']}")

    print(f"\n  KEY FINDING:")
    print(f"    ELRS telemetry is a zero-cost intelligence source.")
    print(f"    No encryption means EVERY telemetry frame is readable.")
    print(f"    No nonce means forged telemetry is indistinguishable")
    print(f"    from legitimate telemetry. The only 'protection' is")
    print(f"    FHSS hop timing, which is fully predictable from the UID.")


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Telemetry Interception Analysis"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze telemetry interception vulnerability",
    )
    parser.add_argument(
        "--decode-formats", action="store_true",
        help="Document all CRSF telemetry frame decode formats",
    )
    parser.add_argument(
        "--privacy-impact", action="store_true",
        help="Analyze privacy implications",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze attack chains using telemetry interception",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("ELRS Telemetry Interception Analysis")
    print(f"Protocol: ELRS v3.x (no encryption, no nonce)")
    print(f"Target: All ELRS v3 links (including TinyHawk III Plus)")
    print(f"Data exposed: GPS, battery, attitude, flight mode, link stats")

    results = {
        "telemetry_types": len(CRSF_TELEMETRY_TYPES),
        "encryption": None,
        "authentication": "CRC-14 (static, not a MAC)",
        "critical_privacy_types": sum(
            1 for t in CRSF_TELEMETRY_TYPES.values()
            if t["privacy_rating"] in ("CRITICAL", "HIGH")
        ),
    }

    if args.analyze:
        analyze_vulnerability()

    if args.decode_formats:
        decode_formats()

    if args.privacy_impact:
        analyze_privacy()

    if args.chain:
        analyze_chains()

    if not any([args.analyze, args.decode_formats, args.privacy_impact, args.chain]):
        analyze_vulnerability()
        decode_formats()
        analyze_privacy()
        analyze_chains()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
