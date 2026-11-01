"""
MAVLink Reconnaissance — Passive Telemetry Interception and Decoding

Connects to a MAVLink stream and passively decodes all telemetry data:
vehicle state, GPS position, battery, flight mode, parameters, etc.

Usage:
    # Against SITL (localhost):
    python3 mavlink_recon.py --connection udp:127.0.0.1:14550

    # Against WiFi telemetry module:
    python3 mavlink_recon.py --connection udp:192.168.4.1:14550

    # Against serial telemetry:
    python3 mavlink_recon.py --connection /dev/ttyUSB0 --baud 57600

For authorized security research only.
"""

import argparse
import time
import json
import sys
from datetime import datetime
from pymavlink import mavutil


def decode_flight_mode(base_mode, custom_mode):
    """Decode ArduPilot flight mode from base_mode and custom_mode."""
    copter_modes = {
        0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO",
        4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE",
        9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP",
        15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW",
        19: "AVOID_ADSB", 20: "GUIDED_NOGPS", 21: "SMART_RTL",
        22: "FLOWHOLD", 23: "FOLLOW", 24: "ZIGZAG", 25: "SYSTEMID",
        26: "AUTOROTATE", 27: "AUTO_RTL",
    }
    return copter_modes.get(custom_mode, f"UNKNOWN({custom_mode})")


def format_gps_coord(coord_e7):
    """Convert MAVLink GPS coordinate (1e7 degrees) to decimal degrees."""
    return coord_e7 / 1e7


class MAVLinkRecon:
    def __init__(self, connection_string, baud=57600):
        self.conn_str = connection_string
        self.baud = baud
        self.conn = None
        self.vehicle_info = {}
        self.parameters = {}
        self.msg_counts = {}
        self.start_time = time.time()

    def connect(self):
        print(f"[*] Connecting to {self.conn_str}...")
        self.conn = mavutil.mavlink_connection(self.conn_str, baud=self.baud)
        print("[*] Waiting for heartbeat...")
        msg = self.conn.wait_heartbeat(timeout=30)
        if msg:
            self.vehicle_info['sysid'] = msg.get_srcSystem()
            self.vehicle_info['compid'] = msg.get_srcComponent()
            self.vehicle_info['type'] = msg.type
            self.vehicle_info['autopilot'] = msg.autopilot
            self.vehicle_info['base_mode'] = msg.base_mode
            self.vehicle_info['custom_mode'] = msg.custom_mode
            self.vehicle_info['mavlink_version'] = msg.mavlink_version

            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            mode = decode_flight_mode(msg.base_mode, msg.custom_mode)

            print(f"[+] Heartbeat received!")
            print(f"    System ID:    {self.vehicle_info['sysid']}")
            print(f"    Component ID: {self.vehicle_info['compid']}")
            print(f"    Vehicle type: {msg.type}")
            print(f"    Autopilot:    {msg.autopilot}")
            print(f"    Flight mode:  {mode}")
            print(f"    Armed:        {armed}")
            print(f"    MAVLink ver:  {msg.mavlink_version}")

            signing = hasattr(msg, '_header') and hasattr(msg._header, 'incompat_flags')
            if signing:
                signed = bool(getattr(msg._header, 'incompat_flags', 0) & 0x01)
                print(f"    Signing:      {'ENABLED' if signed else 'DISABLED'}")
            else:
                print(f"    Signing:      NOT AVAILABLE (MAVLink v1)")

            return True
        else:
            print("[-] No heartbeat received (timeout)")
            return False

    def passive_monitor(self, duration=60, verbose=True):
        """Passively monitor and decode all MAVLink messages."""
        print(f"\n[*] Passive monitoring for {duration}s...")
        print("=" * 70)

        end_time = time.time() + duration

        while time.time() < end_time:
            msg = self.conn.recv_match(blocking=True, timeout=1)
            if msg is None:
                continue

            msg_type = msg.get_type()
            self.msg_counts[msg_type] = self.msg_counts.get(msg_type, 0) + 1

            if not verbose:
                continue

            ts = f"[{time.time() - self.start_time:8.2f}]"

            if msg_type == 'HEARTBEAT':
                armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                mode = decode_flight_mode(msg.base_mode, msg.custom_mode)
                print(f"{ts} HEARTBEAT: mode={mode} armed={armed}")

            elif msg_type == 'GPS_RAW_INT':
                lat = format_gps_coord(msg.lat)
                lon = format_gps_coord(msg.lon)
                alt = msg.alt / 1000.0
                fix = msg.fix_type
                sats = msg.satellites_visible
                print(f"{ts} GPS: lat={lat:.7f} lon={lon:.7f} alt={alt:.1f}m fix={fix} sats={sats}")

            elif msg_type == 'GLOBAL_POSITION_INT':
                lat = format_gps_coord(msg.lat)
                lon = format_gps_coord(msg.lon)
                alt = msg.relative_alt / 1000.0
                hdg = msg.hdg / 100.0
                print(f"{ts} POS: lat={lat:.7f} lon={lon:.7f} alt={alt:.1f}m hdg={hdg:.0f}°")

            elif msg_type == 'ATTITUDE':
                roll = msg.roll * 57.2958
                pitch = msg.pitch * 57.2958
                yaw = msg.yaw * 57.2958
                print(f"{ts} ATT: roll={roll:.1f}° pitch={pitch:.1f}° yaw={yaw:.1f}°")

            elif msg_type == 'SYS_STATUS':
                vbat = msg.voltage_battery / 1000.0
                ibat = msg.current_battery / 100.0
                rem = msg.battery_remaining
                print(f"{ts} BAT: {vbat:.2f}V {ibat:.1f}A {rem}%")

            elif msg_type == 'VFR_HUD':
                print(f"{ts} HUD: aspd={msg.airspeed:.1f} gspd={msg.groundspeed:.1f} "
                      f"alt={msg.alt:.1f}m climb={msg.climb:.1f}m/s hdg={msg.heading}°")

            elif msg_type == 'STATUSTEXT':
                print(f"{ts} STATUS [{msg.severity}]: {msg.text}")

            elif msg_type == 'PARAM_VALUE':
                self.parameters[msg.param_id] = msg.param_value
                if verbose:
                    print(f"{ts} PARAM: {msg.param_id} = {msg.param_value} "
                          f"(type={msg.param_type}, idx={msg.param_index}/{msg.param_count})")

    def enumerate_parameters(self):
        """Request and enumerate all vehicle parameters."""
        print("\n[*] Requesting all parameters...")
        self.conn.mav.param_request_list_send(
            self.conn.target_system,
            self.conn.target_component
        )

        total = None
        received = 0
        timeout = time.time() + 60

        while time.time() < timeout:
            msg = self.conn.recv_match(type='PARAM_VALUE', blocking=True, timeout=2)
            if msg is None:
                if received > 0:
                    break
                continue

            self.parameters[msg.param_id] = msg.param_value
            received += 1

            if total is None:
                total = msg.param_count
                print(f"[+] Vehicle reports {total} parameters")

            if received >= total:
                break

        print(f"[+] Received {received} parameters")
        return self.parameters

    def find_security_relevant_params(self):
        """Identify security-relevant parameters in the vehicle config."""
        security_params = {
            'BRD_SAFETY_DEFLT': 'Board safety switch default',
            'ARMING_CHECK': 'Pre-arm checks bitmask',
            'ARMING_REQUIRE': 'Arming requirement level',
            'FS_THR_ENABLE': 'Throttle failsafe enable',
            'FS_THR_VALUE': 'Throttle failsafe PWM threshold',
            'FS_GCS_ENABLE': 'GCS failsafe enable',
            'FS_EKF_ACTION': 'EKF failsafe action',
            'FS_EKF_THRESH': 'EKF failsafe threshold',
            'FENCE_ENABLE': 'Geofence enable',
            'FENCE_ACTION': 'Geofence breach action',
            'FENCE_ALT_MAX': 'Maximum altitude fence',
            'FENCE_RADIUS': 'Radius fence',
            'RTL_ALT': 'RTL altitude',
            'RTL_ALT_FINAL': 'RTL final altitude',
            'ATC_RAT_RLL_P': 'Roll rate P gain',
            'ATC_RAT_PIT_P': 'Pitch rate P gain',
            'ATC_RAT_YAW_P': 'Yaw rate P gain',
            'EK2_GPS_TYPE': 'EKF2 GPS type',
            'EK3_GPS_TYPE': 'EKF3 GPS type',
            'EK2_POSNE_M_NSE': 'EKF2 position noise',
            'EK3_POSNE_M_NSE': 'EKF3 position noise',
        }

        print("\n[*] Security-relevant parameters:")
        print("=" * 70)
        for param, desc in security_params.items():
            if param in self.parameters:
                val = self.parameters[param]
                print(f"  {param:25s} = {val:10.2f}  ({desc})")
            else:
                print(f"  {param:25s} = {'NOT FOUND':>10s}  ({desc})")

    def print_summary(self):
        """Print summary of intercepted data."""
        print("\n" + "=" * 70)
        print("RECONNAISSANCE SUMMARY")
        print("=" * 70)
        print(f"Duration: {time.time() - self.start_time:.1f}s")
        print(f"Vehicle info: {json.dumps(self.vehicle_info, indent=2)}")
        print(f"Parameters collected: {len(self.parameters)}")
        print(f"Message types seen: {len(self.msg_counts)}")
        print("\nMessage type counts:")
        for msg_type, count in sorted(self.msg_counts.items(), key=lambda x: -x[1]):
            print(f"  {msg_type:30s}: {count}")


def main():
    parser = argparse.ArgumentParser(description='MAVLink Passive Reconnaissance')
    parser.add_argument('--connection', '-c', default='udp:127.0.0.1:14550',
                        help='MAVLink connection string')
    parser.add_argument('--baud', '-b', type=int, default=57600,
                        help='Serial baud rate')
    parser.add_argument('--duration', '-d', type=int, default=30,
                        help='Monitoring duration in seconds')
    parser.add_argument('--params', '-p', action='store_true',
                        help='Enumerate all parameters')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress per-message output')

    args = parser.parse_args()

    recon = MAVLinkRecon(args.connection, args.baud)

    if not recon.connect():
        sys.exit(1)

    if args.params:
        recon.enumerate_parameters()
        recon.find_security_relevant_params()

    recon.passive_monitor(duration=args.duration, verbose=not args.quiet)
    recon.print_summary()


if __name__ == '__main__':
    main()
