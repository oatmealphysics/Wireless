#!/usr/bin/env python3
"""
OpenDroneID Remote ID Spoofing — Protocol Analysis & Frame Generation

The OpenDroneID protocol (ASTM F3411-22a / ASD-STAN prEN 4709-002)
broadcasts drone identity and location via WiFi NAN or Bluetooth 4/5
Legacy Advertising. The protocol has NO authentication or integrity
protection — any device can broadcast fabricated Remote ID messages.

Known vulnerabilities:
  - CVE-2024-22519: OpenDroneID OSM 3.5.1 allows crafted RID injection
  - droneRemoteIDSpoofer (Swiss Cyber Defence Campus): public ESP32 tool
    creates ghost drones, impersonates real drones, floods receivers

OpenDroneID message types (ASTM F3411-22a §2):
  Type 0: Basic ID       — UA type, ID type, UA ID (serial/session/assigned)
  Type 1: Location/Vector — lat, lon, alt, speed, heading, timestamp
  Type 2: Authentication  — auth type, page number, auth data (OPTIONAL)
  Type 3: Self-ID         — text description (free-form, 23 chars)
  Type 4: System          — operator location, area count, timestamps
  Type 5: Operator ID     — operator registration ID
  Type F: Message Pack    — multiple messages in one frame

Attack scenarios:
  1. Ghost drones — create phantom entries on RID receivers
  2. Identity cloning — impersonate real drones by copying their broadcast ID
  3. Position falsification — broadcast false location for a real drone
  4. Flooding — overwhelm receivers with hundreds of phantom entries
  5. Tracking evasion — broadcast contradictory IDs from same physical drone

Hardware cost: ~$20 (ESP32 DevKit or ESP32-C3 module)

Transport:
  - Bluetooth 4 Legacy Advertising (ADV_NONCONN_IND, max 31 bytes)
  - Bluetooth 5 Extended Advertising (up to 255 bytes, enables Message Pack)
  - WiFi NAN (Neighbor Awareness Networking) Action frames

Relevance to TinyHawk III Plus / E8:
  - TinyHawk does NOT have RID hardware (sub-250g exempt in many jurisdictions)
  - If E8 controller has ESP32 with WiFi/BLE, it could serve as a spoofer
  - Defensive context: understanding RID weaknesses informs counter-drone work

Usage:
  python3 opendroneid-spoof-analysis.py --analyze
  python3 opendroneid-spoof-analysis.py --generate-frames
  python3 opendroneid-spoof-analysis.py --attack-scenarios
  python3 opendroneid-spoof-analysis.py --chain

For authorized security research and CTF use only.
"""

import argparse
import hashlib
import json
import os
import struct
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# OpenDroneID constants (ASTM F3411-22a)
# ---------------------------------------------------------------------------

MSG_TYPE_BASIC_ID = 0x0
MSG_TYPE_LOCATION = 0x1
MSG_TYPE_AUTH = 0x2
MSG_TYPE_SELF_ID = 0x3
MSG_TYPE_SYSTEM = 0x4
MSG_TYPE_OPERATOR_ID = 0x5
MSG_TYPE_MESSAGE_PACK = 0xF

MSG_TYPE_NAMES = {
    MSG_TYPE_BASIC_ID: "Basic ID",
    MSG_TYPE_LOCATION: "Location/Vector",
    MSG_TYPE_AUTH: "Authentication",
    MSG_TYPE_SELF_ID: "Self-ID",
    MSG_TYPE_SYSTEM: "System",
    MSG_TYPE_OPERATOR_ID: "Operator ID",
    MSG_TYPE_MESSAGE_PACK: "Message Pack",
}

# UA (Unmanned Aircraft) types
UA_TYPE_NONE = 0
UA_TYPE_AEROPLANE = 1
UA_TYPE_HELICOPTER = 2  # includes multirotor
UA_TYPE_GYROPLANE = 3
UA_TYPE_VTOL = 4  # hybrid fixed-wing
UA_TYPE_ORNITHOPTER = 5
UA_TYPE_GLIDER = 6
UA_TYPE_KITE = 7
UA_TYPE_FREE_BALLOON = 8
UA_TYPE_CAPTIVE_BALLOON = 9
UA_TYPE_AIRSHIP = 10
UA_TYPE_PARACHUTE = 11
UA_TYPE_ROCKET = 12
UA_TYPE_TETHERED = 13
UA_TYPE_GND_OBSTACLE = 14
UA_TYPE_OTHER = 15

# ID types
ID_TYPE_NONE = 0
ID_TYPE_SERIAL = 1  # ANSI/CTA-2063-A
ID_TYPE_CAA_REG = 2  # CAA registration ID
ID_TYPE_UTM_ASSIGNED = 3  # UTM assigned UUID
ID_TYPE_SPECIFIC_SESSION = 4  # specific session ID

# Altitude types
ALT_TYPE_BARO = 0
ALT_TYPE_WGS84 = 1

# Auth types
AUTH_TYPE_NONE = 0
AUTH_TYPE_UAS_ID_SIGNATURE = 1
AUTH_TYPE_OPERATOR_ID_SIGNATURE = 2
AUTH_TYPE_MESSAGE_SET_SIGNATURE = 3
AUTH_TYPE_NETWORK_REMOTE_ID = 4
AUTH_TYPE_SPECIFIC_AUTH = 5

# Status
STATUS_UNDECLARED = 0
STATUS_GROUND = 1
STATUS_AIRBORNE = 2
STATUS_EMERGENCY = 3
STATUS_RID_SYSTEM_FAILURE = 4

# Operator classification (EU)
OPERATOR_CLASS_UNDECLARED = 0
OPERATOR_CLASS_EU_OPEN = 1
OPERATOR_CLASS_EU_SPECIFIC = 2
OPERATOR_CLASS_EU_CERTIFIED = 3

# BLE advertising constants
BLE_ADV_TYPE_NONCONN_IND = 0x03
BLE_AD_TYPE_SERVICE_DATA_16 = 0x16
ODID_BLE_SERVICE_UUID = 0xFFFA  # OpenDroneID BLE service UUID

# WiFi NAN constants
WIFI_NAN_SERVICE_NAME = "org.opendroneid.remoteid"
WIFI_OUI_TYPE_ODID = 0x13  # WiFi Alliance ODID OUI type

# Frame sizes
ODID_MSG_SIZE = 25  # each message is exactly 25 bytes
ODID_MSG_PACK_MAX = 9  # max 9 messages per pack
BLE_LEGACY_ADV_MAX = 31  # BLE 4.x legacy advertising
BLE_EXT_ADV_MAX = 255  # BLE 5.x extended advertising


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------

def build_basic_id_msg(ua_type=UA_TYPE_HELICOPTER, id_type=ID_TYPE_SERIAL,
                       ua_id="FAKE0000000000000000"):
    """Build a Basic ID message (Type 0)."""
    msg = bytearray(ODID_MSG_SIZE)
    msg[0] = (MSG_TYPE_BASIC_ID << 4) | (ua_type & 0x0F)
    msg[1] = id_type & 0x0F
    ua_id_bytes = ua_id.encode("ascii")[:20]
    msg[2:2 + len(ua_id_bytes)] = ua_id_bytes
    return bytes(msg)


def _encode_lat_lon(deg):
    """Encode lat/lon as int32 (1e-7 degrees)."""
    return int(deg * 1e7) & 0xFFFFFFFF


def _encode_altitude(alt_m):
    """Encode altitude: 0.5m resolution, offset by -1000m."""
    raw = int((alt_m + 1000.0) / 0.5)
    return max(0, min(0xFFFF, raw))


def _encode_speed(speed_ms):
    """Encode speed in 0.25 m/s units (multiplier 0) or 0.75 m/s (multiplier 1)."""
    if speed_ms <= 63.75:
        return int(speed_ms / 0.25) & 0xFF, 0
    raw = int((speed_ms - 63.75) / 0.75)
    return min(0xFF, raw), 1


def _encode_direction(heading_deg):
    """Encode heading: 0-359 in 1-degree steps, 0xFF = unknown."""
    if heading_deg is None:
        return 0xFF
    return int(heading_deg) % 360


def build_location_msg(lat=47.3977, lon=8.5456, alt_baro=120.0,
                       alt_geo=130.0, speed=5.0, heading=90.0,
                       status=STATUS_AIRBORNE):
    """Build a Location/Vector message (Type 1)."""
    msg = bytearray(ODID_MSG_SIZE)
    msg[0] = (MSG_TYPE_LOCATION << 4) | (status & 0x0F)
    direction = _encode_direction(heading)
    msg[1] = direction & 0xFF
    speed_enc, speed_mult = _encode_speed(speed)
    msg[2] = speed_enc
    # vertical speed: int8, 0.5 m/s resolution
    msg[3] = 0  # 0 = level flight
    lat_enc = _encode_lat_lon(lat)
    struct.pack_into("<I", msg, 4, lat_enc)
    lon_enc = _encode_lat_lon(lon)
    struct.pack_into("<I", msg, 8, lon_enc)
    alt_baro_enc = _encode_altitude(alt_baro)
    struct.pack_into("<H", msg, 12, alt_baro_enc)
    alt_geo_enc = _encode_altitude(alt_geo)
    struct.pack_into("<H", msg, 14, alt_geo_enc)
    # height above takeoff: int16, 0.5m resolution (0 = at takeoff)
    struct.pack_into("<H", msg, 16, 0)
    # horizontal/vertical accuracy: nibble-packed
    msg[18] = 0x0A  # ~10m horizontal, ~10m vertical
    # baro alt accuracy, speed accuracy
    msg[19] = 0x0A
    # timestamp: 0.1s since the current hour
    ts = (time.time() % 3600) * 10
    struct.pack_into("<H", msg, 20, int(ts) & 0xFFFF)
    # reserved + timestamp accuracy
    msg[22] = 0x01  # 1 = known timestamp accuracy
    msg[23] = speed_mult  # speed multiplier in bit 0
    return bytes(msg)


def build_self_id_msg(description="Research drone"):
    """Build a Self-ID message (Type 3)."""
    msg = bytearray(ODID_MSG_SIZE)
    msg[0] = (MSG_TYPE_SELF_ID << 4) | 0x00  # description type = text
    desc_bytes = description.encode("ascii")[:23]
    msg[1:1 + len(desc_bytes)] = desc_bytes
    return bytes(msg)


def build_system_msg(operator_lat=47.3969, operator_lon=8.5432,
                     area_count=1, operator_alt=400.0):
    """Build a System message (Type 4)."""
    msg = bytearray(ODID_MSG_SIZE)
    msg[0] = (MSG_TYPE_SYSTEM << 4) | (OPERATOR_CLASS_UNDECLARED & 0x0F)
    lat_enc = _encode_lat_lon(operator_lat)
    struct.pack_into("<I", msg, 1, lat_enc)
    lon_enc = _encode_lat_lon(operator_lon)
    struct.pack_into("<I", msg, 5, lon_enc)
    struct.pack_into("<H", msg, 9, area_count)
    # area radius: 0 = point, units of 10m
    struct.pack_into("<H", msg, 11, 0)
    # area ceiling: 0.5m resolution, offset -1000m
    struct.pack_into("<H", msg, 13, _encode_altitude(200.0))
    # area floor
    struct.pack_into("<H", msg, 15, _encode_altitude(0.0))
    # operator altitude
    struct.pack_into("<H", msg, 17, _encode_altitude(operator_alt))
    # timestamp (seconds since 2019-01-01 00:00:00)
    epoch_2019 = 1546300800
    ts = int(time.time()) - epoch_2019
    struct.pack_into("<I", msg, 19, ts & 0xFFFFFFFF)
    return bytes(msg)


def build_operator_id_msg(operator_id="CHE-OP-TEST000001"):
    """Build an Operator ID message (Type 5)."""
    msg = bytearray(ODID_MSG_SIZE)
    msg[0] = (MSG_TYPE_OPERATOR_ID << 4) | 0x00  # operator ID type = 0
    op_bytes = operator_id.encode("ascii")[:20]
    msg[1:1 + len(op_bytes)] = op_bytes
    return bytes(msg)


def build_message_pack(messages):
    """Build a Message Pack (Type F) containing multiple messages."""
    count = min(len(messages), ODID_MSG_PACK_MAX)
    # Message Pack header: type nibble + count, then 25-byte messages
    header = bytearray(2)
    header[0] = (MSG_TYPE_MESSAGE_PACK << 4)
    header[1] = count
    payload = header + b"".join(messages[:count])
    return bytes(payload)


def build_ble_adv_frame(odid_msg):
    """Wrap an ODID message in a BLE Legacy Advertising frame."""
    # AD structure: length, type (0x16 = Service Data), UUID (0xFFFA), counter, msg
    service_data = struct.pack("<H", ODID_BLE_SERVICE_UUID)
    counter = int(time.time()) & 0xFF  # message counter
    ad_data = service_data + bytes([counter]) + odid_msg
    ad_length = len(ad_data) + 1  # +1 for AD type byte
    frame = bytes([ad_length, BLE_AD_TYPE_SERVICE_DATA_16]) + ad_data
    return frame


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_protocol():
    """Analyze OpenDroneID protocol security weaknesses."""
    print("\n" + "=" * 70)
    print("OpenDroneID Protocol Security Analysis")
    print("=" * 70)

    print("\n  PROTOCOL OVERVIEW (ASTM F3411-22a):")
    print(f"    Message size: {ODID_MSG_SIZE} bytes (fixed)")
    print(f"    Message types: {len(MSG_TYPE_NAMES)}")
    for k, v in MSG_TYPE_NAMES.items():
        print(f"      Type 0x{k:X}: {v}")

    print("\n  SECURITY MODEL: NONE")
    print("    - No authentication on any message type")
    print("    - No integrity protection (no MAC/signature)")
    print("    - No encryption (all fields plaintext)")
    print("    - No replay protection (no sequence numbers/nonces)")
    print("    - No sender verification (any device can broadcast)")
    print("    - Authentication message (Type 2) is OPTIONAL")
    print("      and no receiver is required to validate it")

    print("\n  TRANSPORT SECURITY:")
    print("    BLE Legacy Advertising (4.x):")
    print(f"      - Max payload: {BLE_LEGACY_ADV_MAX} bytes")
    print(f"      - ADV_NONCONN_IND: connectionless, broadcast only")
    print(f"      - No link-layer authentication")
    print(f"      - Single ODID message per advertisement")
    print("    BLE Extended Advertising (5.x):")
    print(f"      - Max payload: {BLE_EXT_ADV_MAX} bytes")
    print(f"      - Enables Message Pack (up to {ODID_MSG_PACK_MAX} messages)")
    print(f"      - Still no authentication")
    print("    WiFi NAN:")
    print(f"      - Service: {WIFI_NAN_SERVICE_NAME}")
    print(f"      - Action frames, no association required")
    print(f"      - Larger payload allows full message packs")
    print(f"      - No WiFi-level authentication for NAN")

    print("\n  KNOWN CVES:")
    print("    CVE-2024-22519: OpenDroneID OSM 3.5.1")
    print("      - Allows injection of crafted Remote ID messages")
    print("      - Module accepts and processes forged broadcasts")
    print("      - No validation of message origin or integrity")

    print("\n  PUBLIC TOOLS:")
    print("    droneRemoteIDSpoofer (Swiss Cyber Defence Campus):")
    print("      - GitHub: cyber-defence-campus/droneRemoteIDSpoofer")
    print("      - Hardware: ESP32 DevKit (~$20)")
    print("      - Capabilities: ghost creation, ID cloning, flooding")
    print("      - Both BLE and WiFi NAN spoofing modes")


def analyze_attack_scenarios():
    """Analyze specific attack scenarios."""
    print("\n" + "=" * 70)
    print("Attack Scenarios")
    print("=" * 70)

    scenarios = [
        {
            "name": "Ghost Drone Creation",
            "severity": "HIGH",
            "description": (
                "Broadcast fabricated Basic ID + Location messages to create "
                "phantom drone entries on any RID receiver within range."
            ),
            "hardware": "ESP32 ($20)",
            "range": "BLE: ~100m, WiFi NAN: ~200m",
            "detection": "Difficult — messages are protocol-valid",
            "impact": [
                "False positives overwhelm law enforcement/UTM",
                "Mask real drone operations behind ghost swarm",
                "Create no-fly-zone violations at arbitrary locations",
            ],
            "mitigations": [
                "RF fingerprinting (requires specialized equipment)",
                "Consistency checks (speed vs position changes)",
                "Multi-sensor correlation (radar/visual + RID)",
            ],
        },
        {
            "name": "Identity Cloning / Impersonation",
            "severity": "HIGH",
            "description": (
                "Copy a real drone's serial number or session ID and "
                "broadcast it from a different location."
            ),
            "hardware": "ESP32 + RID receiver to capture real IDs",
            "range": "Capture: ~100m, Replay: unlimited (store-and-forward)",
            "detection": "Very difficult — messages match real drone format",
            "impact": [
                "Frame a legitimate operator for violations",
                "Create false track divergences in UTM",
                "Undermine legal accountability of RID system",
            ],
            "mitigations": [
                "Authentication (Type 2 msg) — but currently optional",
                "Network Remote ID cross-referencing",
                "Anomaly detection on duplicate IDs at different locations",
            ],
        },
        {
            "name": "Position Falsification",
            "severity": "MEDIUM",
            "description": (
                "Broadcast correct drone ID but false GPS coordinates, "
                "making the drone appear in a different location."
            ),
            "hardware": "ESP32 + knowledge of target drone ID",
            "range": "Must overpower real drone's broadcasts at receiver",
            "detection": "Moderate — speed/position consistency checks possible",
            "impact": [
                "Evade geofence enforcement",
                "Appear to comply with airspace restrictions",
                "Misdirect response to drone sightings",
            ],
            "mitigations": [
                "Position/speed consistency checks over time",
                "RSSI-based proximity validation",
                "Multi-receiver triangulation",
            ],
        },
        {
            "name": "Receiver Flooding / DoS",
            "severity": "MEDIUM",
            "description": (
                "Generate hundreds of unique phantom drone IDs, "
                "overwhelming receiver tracking capacity and UI."
            ),
            "hardware": "ESP32 with rapid ID cycling",
            "range": "BLE: ~100m, WiFi NAN: ~200m",
            "detection": "Easy to detect, hard to filter",
            "impact": [
                "Receiver memory/CPU exhaustion",
                "Real drones lost in noise",
                "UTM system degradation",
            ],
            "mitigations": [
                "Rate limiting per source MAC",
                "RSSI-based filtering",
                "Anomaly detection on ID creation rate",
            ],
        },
        {
            "name": "Tracking Evasion",
            "severity": "MEDIUM",
            "description": (
                "Broadcast contradictory IDs and locations from the "
                "same physical drone, fragmenting the tracking record."
            ),
            "hardware": "Onboard ESP32 or modified RID module",
            "range": "N/A — operates on own drone",
            "detection": "Difficult without RF fingerprinting",
            "impact": [
                "Fragment tracking history across multiple fake IDs",
                "Prevent continuous track association",
                "Undermine post-incident forensics",
            ],
            "mitigations": [
                "RF fingerprinting (PHY-layer identification)",
                "Visual/radar correlation",
                "Mandatory authentication (future regulations)",
            ],
        },
    ]

    for i, s in enumerate(scenarios, 1):
        print(f"\n  SCENARIO {i}: {s['name']}")
        print(f"    Severity: {s['severity']}")
        print(f"    {s['description']}")
        print(f"    Hardware: {s['hardware']}")
        print(f"    Range: {s['range']}")
        print(f"    Detection difficulty: {s['detection']}")
        print(f"    Impact:")
        for imp in s["impact"]:
            print(f"      - {imp}")
        print(f"    Mitigations:")
        for mit in s["mitigations"]:
            print(f"      - {mit}")


def generate_sample_frames():
    """Generate sample OpenDroneID frames for analysis."""
    print("\n" + "=" * 70)
    print("Sample Frame Generation (offline analysis only)")
    print("=" * 70)

    # Ghost drone at Zurich coordinates
    basic_id = build_basic_id_msg(
        ua_type=UA_TYPE_HELICOPTER,
        id_type=ID_TYPE_SERIAL,
        ua_id="GHOST000000000000001",
    )
    print(f"\n  Basic ID message ({len(basic_id)} bytes):")
    print(f"    Hex: {basic_id.hex()}")
    print(f"    Type: 0x{basic_id[0] >> 4:X} ({MSG_TYPE_NAMES[basic_id[0] >> 4]})")
    print(f"    UA type: {basic_id[0] & 0x0F} (Helicopter/Multirotor)")
    print(f"    ID type: {basic_id[1]} (Serial Number)")
    print(f"    UA ID: {basic_id[2:22].decode('ascii', errors='replace').rstrip(chr(0))}")

    location = build_location_msg(
        lat=47.3977, lon=8.5456, alt_baro=120.0,
        alt_geo=130.0, speed=5.0, heading=90.0,
    )
    lat_dec = struct.unpack_from("<I", location, 4)[0]
    lon_dec = struct.unpack_from("<I", location, 8)[0]
    print(f"\n  Location message ({len(location)} bytes):")
    print(f"    Hex: {location.hex()}")
    print(f"    Status: Airborne")
    print(f"    Lat: {lat_dec / 1e7:.7f} deg")
    print(f"    Lon: {lon_dec / 1e7:.7f} deg")
    print(f"    Baro alt: {struct.unpack_from('<H', location, 12)[0] * 0.5 - 1000:.1f} m")
    print(f"    Geo alt: {struct.unpack_from('<H', location, 14)[0] * 0.5 - 1000:.1f} m")
    print(f"    Speed: {location[2] * 0.25:.1f} m/s")

    self_id = build_self_id_msg("Security research drone")
    print(f"\n  Self-ID message ({len(self_id)} bytes):")
    print(f"    Hex: {self_id.hex()}")
    print(f"    Description: {self_id[1:24].decode('ascii', errors='replace').rstrip(chr(0))}")

    system = build_system_msg(
        operator_lat=47.3969, operator_lon=8.5432,
        area_count=1, operator_alt=400.0,
    )
    print(f"\n  System message ({len(system)} bytes):")
    print(f"    Hex: {system.hex()}")

    operator = build_operator_id_msg("CHE-OP-RESEARCH001")
    print(f"\n  Operator ID message ({len(operator)} bytes):")
    print(f"    Hex: {operator.hex()}")
    op_text = operator[1:21].decode("ascii", errors="replace").rstrip(chr(0))
    print(f"    Operator ID: {op_text}")

    # BLE advertisement frame
    ble_frame = build_ble_adv_frame(basic_id)
    print(f"\n  BLE Legacy Advertisement frame ({len(ble_frame)} bytes):")
    print(f"    Hex: {ble_frame.hex()}")
    print(f"    Service UUID: 0x{ODID_BLE_SERVICE_UUID:04X} (OpenDroneID)")
    fits_legacy = len(ble_frame) <= BLE_LEGACY_ADV_MAX
    print(f"    Fits BLE 4.x legacy ({BLE_LEGACY_ADV_MAX} byte limit): {'YES' if fits_legacy else 'NO'}")

    # Message pack
    pack = build_message_pack([basic_id, location, self_id, system, operator])
    print(f"\n  Message Pack ({len(pack)} bytes):")
    print(f"    Messages: {pack[1]}")
    print(f"    Total payload: {len(pack)} bytes")
    fits_ext = len(pack) <= BLE_EXT_ADV_MAX
    print(f"    Fits BLE 5.x extended ({BLE_EXT_ADV_MAX} byte limit): {'YES' if fits_ext else 'NO'}")

    # Frame validation checks
    print("\n  FRAME VALIDATION:")
    print(f"    All messages are {ODID_MSG_SIZE} bytes (fixed): YES")
    for name, msg in [("Basic ID", basic_id), ("Location", location),
                      ("Self-ID", self_id), ("System", system),
                      ("Operator", operator)]:
        print(f"    {name}: {len(msg)} bytes, type=0x{msg[0] >> 4:X} — "
              f"{'VALID' if len(msg) == ODID_MSG_SIZE else 'INVALID'}")


def analyze_chains():
    """Analyze complete attack chains."""
    print("\n" + "=" * 70)
    print("Attack Chain Analysis")
    print("=" * 70)

    chains = [
        {
            "id": "RID-KC-01",
            "name": "Ghost Swarm",
            "severity": "HIGH",
            "stages": [
                "WEAPONIZE: Generate N unique serial numbers + trajectories",
                "DELIVER: ESP32 cycles through N drone identities at BLE adv rate",
                "EXPLOIT: Receivers track each ID as a separate drone",
                "ACTIONS: Law enforcement / UTM overwhelmed with false positives",
            ],
            "cost": "~$20 (ESP32)",
            "detection": "Rate-based anomaly detection, RSSI clustering",
        },
        {
            "id": "RID-KC-02",
            "name": "Identity Theft + Position Spoof",
            "severity": "CRITICAL",
            "stages": [
                "RECON: Capture target drone's Basic ID from BLE advertisements",
                "WEAPONIZE: Clone serial number, prepare false Location messages",
                "DELIVER: Broadcast cloned ID at different GPS coordinates",
                "EXPLOIT: Receiver shows target drone in wrong location",
                "ACTIONS: False incident report, wrong-location enforcement response",
            ],
            "cost": "~$40 (ESP32 + BLE sniffer)",
            "detection": "Multi-receiver TDOA, RF fingerprinting",
        },
        {
            "id": "RID-KC-03",
            "name": "Regulatory Evasion via RID Jamming",
            "severity": "MEDIUM",
            "stages": [
                "RECON: Identify target's RID broadcast frequency (BLE/WiFi)",
                "DELIVER: Targeted BLE/WiFi jamming suppresses RID broadcasts",
                "EXPLOIT: Drone continues flying but invisible to RID receivers",
                "ACTIONS: Untracked operation in controlled airspace",
            ],
            "cost": "~$50 (directional 2.4 GHz)",
            "detection": "RF monitoring for jamming, visual/radar correlation",
        },
    ]

    for c in chains:
        print(f"\n  CHAIN {c['id']}: {c['name']}")
        print(f"    Severity: {c['severity']}")
        print(f"    Cost: {c['cost']}")
        for stage in c["stages"]:
            print(f"      {stage}")
        print(f"    Detection: {c['detection']}")

    print("\n  REGULATORY CONTEXT:")
    print("    EU: Delegated Regulation (EU) 2019/945 requires RID for most classes")
    print("    US: FAA 14 CFR Part 89 requires Standard or Broadcast RID")
    print("    Both regulations ASSUME broadcast integrity without authentication")
    print("    Neither mandates receiver-side validation beyond message parsing")

    print("\n  DEFENSIVE RECOMMENDATIONS:")
    print("    1. Deploy multi-receiver correlation (detect single-source spoofs)")
    print("    2. Implement RF fingerprinting alongside RID (PHY-layer ID)")
    print("    3. Cross-reference with Network Remote ID (server-validated)")
    print("    4. Push for mandatory Authentication messages (Type 2)")
    print("    5. Monitor for rate anomalies (too many new IDs from one location)")


def main():
    parser = argparse.ArgumentParser(
        description="OpenDroneID Remote ID Spoofing Analysis"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze protocol security weaknesses",
    )
    parser.add_argument(
        "--generate-frames", action="store_true",
        help="Generate sample ODID frames for analysis",
    )
    parser.add_argument(
        "--attack-scenarios", action="store_true",
        help="Analyze attack scenarios",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze full attack chains",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("OpenDroneID Remote ID Spoofing Analysis")
    print(f"Standard: ASTM F3411-22a / ASD-STAN prEN 4709-002")
    print(f"BLE Service UUID: 0x{ODID_BLE_SERVICE_UUID:04X}")
    print(f"Message size: {ODID_MSG_SIZE} bytes (fixed)")
    print(f"Message types: {len(MSG_TYPE_NAMES)}")
    print(f"Target hardware: N/A (TinyHawk sub-250g, no RID)")
    print(f"Relevance: Defensive research / counter-drone context")

    results = {
        "standard": "ASTM F3411-22a",
        "message_types": len(MSG_TYPE_NAMES),
        "authentication": "NONE (Type 2 optional, no receiver enforcement)",
        "attack_scenarios": 5,
        "chains": 3,
        "hardware_cost": "$20 (ESP32)",
    }

    if args.analyze:
        analyze_protocol()

    if args.generate_frames:
        generate_sample_frames()

    if args.attack_scenarios:
        analyze_attack_scenarios()

    if args.chain:
        analyze_chains()

    if not any([args.analyze, args.generate_frames, args.attack_scenarios, args.chain]):
        analyze_protocol()
        generate_sample_frames()
        analyze_attack_scenarios()
        analyze_chains()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
