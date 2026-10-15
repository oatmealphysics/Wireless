#!/usr/bin/env python3
"""
MAVLink Reconnaissance Tool

Passively sniffs MAVLink traffic on a UDP port or serial link and reports:
  - MAVLink version (v1 / v2)
  - System ID and component ID of each vehicle/GCS
  - Vehicle type and autopilot firmware
  - Whether message signing is enabled
  - Summary of message types observed
  - Link quality statistics

This is a receive-only tool — it sends nothing and cannot be detected.

Usage:
    # Listen on UDP (default: all interfaces, port 14550)
    python3 mavlink-recon.py

    # Listen on specific UDP endpoint
    python3 mavlink-recon.py --udp 0.0.0.0:14550

    # Listen on serial (SiK radio, telemetry UART)
    python3 mavlink-recon.py --serial /dev/ttyUSB0 --baud 57600

    # Run for 30 seconds then print summary
    python3 mavlink-recon.py --duration 30

    # Continuous mode with live updates
    python3 mavlink-recon.py --live

Dependencies:
    pip install pymavlink

For CTF research and authorized security testing only.
"""

import argparse
import sys
import time
import signal
import os
from collections import defaultdict
from datetime import datetime

try:
    from pymavlink import mavutil
    from pymavlink.dialects.v20 import common as mavlink2
except ImportError:
    print("[!] pymavlink not installed. Run: pip install pymavlink", file=sys.stderr)
    sys.exit(1)


# Vehicle type names (MAV_TYPE enum)
VEHICLE_TYPES = {
    0: "Generic", 1: "Fixed Wing", 2: "Quadrotor", 3: "Coaxial Heli",
    4: "Helicopter", 5: "Antenna Tracker", 6: "GCS", 7: "Airship",
    8: "Free Balloon", 9: "Rocket", 10: "Ground Rover", 11: "Surface Boat",
    12: "Submarine", 13: "Hexarotor", 14: "Octorotor", 15: "Tricopter",
    16: "Flapping Wing", 17: "Kite", 20: "VTOL Tailsitter Duo",
    21: "VTOL Tailsitter Quad", 22: "VTOL Tiltrotor",
    23: "VTOL Reserved2", 24: "VTOL Reserved3", 25: "VTOL Reserved4",
    26: "VTOL Reserved5", 27: "Gimbal", 28: "ADSB Peripheral",
    29: "Steerable Parachute", 30: "Defrief", 31: "Camera",
    32: "Charging Station", 33: "FLARM", 34: "Servo", 35: "ODID",
    36: "Decarotor",
}

# Autopilot firmware names (MAV_AUTOPILOT enum)
AUTOPILOT_TYPES = {
    0: "Generic", 1: "Reserved", 2: "SLUGS", 3: "ArduPilot",
    4: "OpenPilot", 5: "Generic WP Only", 6: "Generic Scale Only",
    7: "Generic All", 8: "PX4", 9: "SMACCMPILOT", 10: "AUTOQUAD",
    11: "ARMAZILA", 12: "AEROB", 13: "ASLUAV", 14: "SmartAP",
    15: "AirRails", 16: "Reflex",
}

# Flight mode names for ArduPilot (copter)
ARDUCOPTER_MODES = {
    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO",
    4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE",
    9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP",
    15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW",
    19: "AVOID_ADSB", 20: "GUIDED_NOGPS", 21: "SMART_RTL",
    22: "FLOWHOLD", 23: "FOLLOW", 24: "ZIGZAG", 25: "SYSTEMID",
    26: "AUTOROTATE", 27: "AUTO_RTL",
}


class MAVLinkSniffer:
    """Passive MAVLink traffic analyzer."""

    def __init__(self, connection_string, baud=57600):
        self.conn_str = connection_string
        self.baud = baud
        self.conn = None

        # Per-system tracking
        self.systems = {}        # sysid → {info dict}
        self.msg_counts = defaultdict(lambda: defaultdict(int))  # sysid → {msgtype → count}
        self.first_seen = {}     # sysid → timestamp
        self.last_seen = {}      # sysid → timestamp
        self.total_msgs = 0
        self.v1_count = 0
        self.v2_count = 0
        self.signed_count = 0
        self.start_time = None

    def connect(self):
        """Establish MAVLink connection (listen-only)."""
        try:
            # Use udpin for listening on UDP, or serial
            self.conn = mavutil.mavlink_connection(
                self.conn_str,
                baud=self.baud,
                source_system=254,      # Use GCS-like ID to not confuse anything
                source_component=0,     # Don't claim a component
                dialect="common",
            )
            return True
        except Exception as e:
            print(f"[!] Connection failed: {e}", file=sys.stderr)
            return False

    def process_message(self, msg):
        """Process a single MAVLink message."""
        self.total_msgs += 1
        now = time.time()

        sysid = msg.get_srcSystem()
        compid = msg.get_srcComponent()
        msgtype = msg.get_type()

        # Track MAVLink version
        # pymavlink sets _header.mlen for v2, v1 messages have different structure
        if hasattr(msg, '_header'):
            if msg._header.incompat_flags is not None:
                self.v2_count += 1
                # Check for signing flag (bit 0 of incompat_flags)
                if msg._header.incompat_flags & 0x01:
                    self.signed_count += 1
            else:
                self.v1_count += 1

        # Track system
        if sysid not in self.systems:
            self.systems[sysid] = {
                'components': set(),
                'vehicle_type': None,
                'autopilot': None,
                'armed': None,
                'mode': None,
                'version': None,
            }
            self.first_seen[sysid] = now

        self.systems[sysid]['components'].add(compid)
        self.last_seen[sysid] = now
        self.msg_counts[sysid][msgtype] += 1

        # Extract system info from HEARTBEAT
        if msgtype == 'HEARTBEAT':
            info = self.systems[sysid]
            info['vehicle_type'] = msg.type
            info['autopilot'] = msg.autopilot
            info['armed'] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            info['mode'] = msg.custom_mode

        # Extract version from AUTOPILOT_VERSION
        if msgtype == 'AUTOPILOT_VERSION':
            ver = msg.flight_sw_version
            major = (ver >> 24) & 0xFF
            minor = (ver >> 16) & 0xFF
            patch = (ver >> 8) & 0xFF
            self.systems[sysid]['version'] = f"{major}.{minor}.{patch}"

    def print_live_status(self):
        """Print current status to stderr for live mode."""
        elapsed = time.time() - self.start_time
        rate = self.total_msgs / elapsed if elapsed > 0 else 0
        signing = "YES ⚠" if self.signed_count > 0 else "NO (exploitable)"
        print(
            f"\r[{elapsed:.0f}s] {self.total_msgs} msgs | "
            f"{len(self.systems)} systems | "
            f"{rate:.1f} msg/s | "
            f"Signing: {signing}",
            end="", file=sys.stderr
        )

    def print_report(self):
        """Print full analysis report."""
        elapsed = time.time() - self.start_time if self.start_time else 0

        print("\n" + "=" * 70)
        print("  MAVLink Reconnaissance Report")
        print(f"  Source: {self.conn_str}")
        print(f"  Duration: {elapsed:.1f}s | Messages: {self.total_msgs}")
        print(f"  Captured: {datetime.now().isoformat()}")
        print("=" * 70)

        # Protocol version
        print("\n  [Protocol]")
        if self.v1_count > 0:
            print(f"    MAVLink v1 messages: {self.v1_count}")
        if self.v2_count > 0:
            print(f"    MAVLink v2 messages: {self.v2_count}")
        if self.v1_count == 0 and self.v2_count == 0 and self.total_msgs > 0:
            print(f"    Version detection inconclusive ({self.total_msgs} messages)")

        # Signing status — critical for attack feasibility
        print("\n  [Message Signing]")
        if self.signed_count > 0:
            pct = (self.signed_count / max(self.v2_count, 1)) * 100
            print(f"    Status:  ENABLED ({self.signed_count}/{self.v2_count} v2 msgs signed, {pct:.0f}%)")
            print(f"    Impact:  Command injection will be REJECTED by the vehicle.")
            print(f"             Need to recover the signing key or find unsigned endpoints.")
        else:
            print(f"    Status:  DISABLED — no signed messages observed")
            print(f"    Impact:  Vehicle accepts commands from ANY source.")
            print(f"             Full command injection possible (disarm, land, RC override, etc.)")

        # Per-system details
        for sysid, info in sorted(self.systems.items()):
            print(f"\n  [System {sysid}]")

            # Vehicle type
            vtype = VEHICLE_TYPES.get(info['vehicle_type'], f"Unknown ({info['vehicle_type']})")
            print(f"    Vehicle type:  {vtype}")

            # Autopilot
            ap = AUTOPILOT_TYPES.get(info['autopilot'], f"Unknown ({info['autopilot']})")
            print(f"    Autopilot:     {ap}")

            # Firmware version
            if info['version']:
                print(f"    FW Version:    {info['version']}")

            # Armed status
            if info['armed'] is not None:
                status = "ARMED ⚠" if info['armed'] else "DISARMED"
                print(f"    Armed:         {status}")

            # Flight mode
            if info['mode'] is not None:
                if info['autopilot'] == 3:  # ArduPilot
                    mode_name = ARDUCOPTER_MODES.get(info['mode'], f"Mode {info['mode']}")
                else:
                    mode_name = f"Mode {info['mode']}"
                print(f"    Flight mode:   {mode_name}")

            # Components
            comp_str = ", ".join(str(c) for c in sorted(info['components']))
            print(f"    Components:    [{comp_str}]")

            # Timing
            duration = self.last_seen[sysid] - self.first_seen[sysid]
            print(f"    Observed for:  {duration:.1f}s")

            # Top message types
            counts = self.msg_counts[sysid]
            top = sorted(counts.items(), key=lambda x: -x[1])[:15]
            print(f"    Message types ({len(counts)} unique):")
            for mtype, count in top:
                rate = count / max(duration, 0.1)
                print(f"      {mtype:30s}  {count:6d}  ({rate:.1f}/s)")

        # Attack recommendations
        print("\n  [Attack Surface Assessment]")
        for sysid, info in sorted(self.systems.items()):
            vtype = VEHICLE_TYPES.get(info['vehicle_type'], "Unknown")
            print(f"\n    System {sysid} ({vtype}):")

            if info['vehicle_type'] == 6:  # GCS
                print(f"      → This is a GCS, not a vehicle. Target for flag capture.")
                print(f"      → Check for MAVLink FTP access to extract files.")
                continue

            if self.signed_count == 0:
                print(f"      → UNSIGNED: Full command injection available")
                print(f"      → Use mavlink-injector.py --target-sysid {sysid}")
                if info['autopilot'] == 8:  # PX4
                    print(f"      → PX4 detected: MAVLink shell may be accessible")
            else:
                print(f"      → SIGNED: Direct injection blocked")
                print(f"      → Try: replay attacks, key recovery, or GPS spoofing")

        print("\n" + "=" * 70)

    def run(self, duration=None, live=False):
        """Main capture loop."""
        self.start_time = time.time()
        deadline = self.start_time + duration if duration else None

        print(f"[*] Listening on {self.conn_str}...", file=sys.stderr)
        print(f"[*] Press Ctrl+C to stop and print report.", file=sys.stderr)

        try:
            while True:
                if deadline and time.time() > deadline:
                    break

                msg = self.conn.recv_match(blocking=True, timeout=1.0)
                if msg is None:
                    continue

                # Skip bad/heartbeat-less data
                if msg.get_type() == 'BAD_DATA':
                    continue

                self.process_message(msg)

                if live and self.total_msgs % 50 == 0:
                    self.print_live_status()

        except KeyboardInterrupt:
            print("\n[*] Stopped by user.", file=sys.stderr)

        self.print_report()


def main():
    parser = argparse.ArgumentParser(
        description="MAVLink Reconnaissance — passive traffic analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              Listen on UDP :14550
  %(prog)s --udp 0.0.0.0:14550          Explicit UDP listen
  %(prog)s --serial /dev/ttyUSB0        Serial link (SiK radio)
  %(prog)s --duration 30                Capture for 30s then report
  %(prog)s --live                       Show live statistics

This tool is passive — it receives only and does not transmit.
        """
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--udp", metavar="HOST:PORT", default=None,
        help="UDP listen address (default: udpin:0.0.0.0:14550)"
    )
    group.add_argument(
        "--serial", metavar="DEVICE",
        help="Serial port (e.g. /dev/ttyUSB0)"
    )
    parser.add_argument(
        "--baud", type=int, default=57600,
        help="Serial baud rate (default: 57600)"
    )
    parser.add_argument(
        "--duration", type=float, default=None,
        help="Capture duration in seconds (default: until Ctrl+C)"
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Show live statistics on stderr"
    )

    args = parser.parse_args()

    # Build connection string
    if args.serial:
        conn_str = args.serial
    elif args.udp:
        conn_str = f"udpin:{args.udp}"
    else:
        conn_str = "udpin:0.0.0.0:14550"

    sniffer = MAVLinkSniffer(conn_str, baud=args.baud)

    if not sniffer.connect():
        sys.exit(1)

    sniffer.run(duration=args.duration, live=args.live)


if __name__ == "__main__":
    main()
