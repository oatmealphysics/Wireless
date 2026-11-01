#!/usr/bin/env python3
"""Read the EMAX E8 ELRS controller's configuration over USB serial.

Protocol: confirmed end-to-end on live hardware (elrs-sdr §49/§50).
  TX: 64 bytes — 02 01 + 62 zero bytes (CRC-16/XMODEM of zero body = 0x0000)
  RX: 17 bytes — 02 c5 <rate:1> <cfg1:1> <cfg2:1> <UID:6> <padding:6>

The E8 must be in serial mode: power-on holding RIGHT on the right d-pad,
center LED flashing green. USB descriptor in this mode: VID 0x8888 PID 0x2001.

Default is dry-run (prints what would happen). Pass --execute to send the
read command. Raw responses are saved privately under work/e8-session/.

Python 3.10+, pyserial==3.5. Read-only — no write, update, reset, bootloader,
or RF operations.

Usage:
  python tools/e8-read-config.py --port /dev/ttyACM0           # dry-run
  python tools/e8-read-config.py --port /dev/ttyACM0 --execute # send read
  python tools/e8-read-config.py --port /dev/ttyACM0 --execute --raw
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

READ_REQUEST = bytes([0x02, 0x01]) + bytes(62)

RATES = {0x00: "500Hz", 0x01: "250Hz", 0x02: "150Hz", 0x03: "50Hz"}
POWERS_GUESS = {0x00: "10mW?", 0x01: "25mW?", 0x02: "50mW?", 0x03: "100mW?"}
TLMS_GUESS = {0x00: "1:128?", 0x01: "1:64?", 0x02: "1:32?", 0x03: "1:16?"}


def validate_endpoint(port: str) -> bool:
    """Check that the port matches the E8 serial-mode USB descriptor."""
    try:
        from serial.tools import list_ports
        matches = [p for p in list_ports.comports()
                   if p.device == port and p.vid == 0x8888 and p.pid == 0x2001]
        return len(matches) == 1
    except ImportError:
        print("WARN: serial.tools.list_ports unavailable, skipping VID:PID check",
              file=sys.stderr)
        return True


def read_config(port: str, show_raw: bool = False) -> int:
    """Send the 64-byte Read command, parse the 17-byte response."""
    try:
        import serial
    except ImportError:
        print("FAIL: pyserial not installed. pip3 install pyserial", file=sys.stderr)
        return 2

    if not validate_endpoint(port):
        print(f"FAIL: {port} is not a VID:PID 8888:2001 device (E8 serial mode)",
              file=sys.stderr)
        print("Is the E8 in serial mode? (hold RIGHT on right d-pad at power-on, green LED)",
              file=sys.stderr)
        return 1

    try:
        ser = serial.Serial(port, 115200, timeout=2, bytesize=8, parity='N',
                            stopbits=1, xonxoff=False, rtscts=False, dsrdtr=False)
    except serial.SerialException as e:
        print(f"FAIL: cannot open {port}: {e}", file=sys.stderr)
        return 1

    capture_dir = Path(__file__).resolve().parents[1] / "work" / "e8-session"
    capture_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()

    try:
        time.sleep(0.2)
        ser.reset_input_buffer()
        initial = ser.read(4096)
        if initial:
            (capture_dir / f"vendor-initial-{stamp}.bin").write_bytes(initial)

        ser.write(READ_REQUEST)
        ser.flush()

        end = time.monotonic() + 3
        response = bytearray()
        while time.monotonic() < end:
            chunk = ser.read(min(ser.in_waiting or 0, 4096) or 1)
            if chunk:
                response.extend(chunk)
            if len(response) >= 17:
                break
    finally:
        ser.close()

    capture_path = capture_dir / f"vendor-read-{stamp}.bin"
    capture_path.write_bytes(response)

    if len(response) != 17 or response[0] != 0x02 or response[1] != 0xC5:
        print(f"FAIL: unexpected response ({len(response)} bytes)", file=sys.stderr)
        summary = {"port": port, "response_bytes": len(response),
                   "expected_length": False, "expected_header": False,
                   "raw_saved_privately": str(capture_path.relative_to(
                       capture_dir.parent.parent)),
                   "raw_sha256": hashlib.sha256(response).hexdigest()}
        print(json.dumps(summary), file=sys.stderr)
        return 1

    rate, cfg1, cfg2 = response[2], response[3], response[4]
    _uid = response[5:11]
    pad = response[11:17]

    print("EMAX E8 ELRS — Read Configuration")
    print(f"  Rate         : {RATES.get(rate, f'unknown=0x{rate:02x}')}  (byte 0x{rate:02x})")
    print(f"  cfg1 (Power?): {POWERS_GUESS.get(cfg1, f'0x{cfg1:02x}')}  "
          f"(byte 0x{cfg1:02x}; mapping TBD)")
    print(f"  cfg2 (tlm?)  : {TLMS_GUESS.get(cfg2, f'0x{cfg2:02x} (OFF?)')}  "
          f"(byte 0x{cfg2:02x}; mapping TBD)")
    print(f"  bind UID     : [redacted — see {capture_path.relative_to(capture_dir.parent.parent)}]")
    print(f"  padding      : {' '.join(f'{b:02x}' for b in pad)}")
    if show_raw:
        print(f"  raw response : [saved privately to {capture_path.relative_to(capture_dir.parent.parent)}]")

    summary = {"port": port, "response_bytes": len(response),
               "expected_length": True,
               "expected_header": True,
               "rate": RATES.get(rate, f"0x{rate:02x}"),
               "rate_byte": rate,
               "cfg1_byte": cfg1, "cfg2_byte": cfg2,
               "raw_saved_privately": str(capture_path.relative_to(
                   capture_dir.parent.parent)),
               "raw_sha256": hashlib.sha256(response).hexdigest(),
               "settings_modified": False}
    print(json.dumps(summary))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="/dev/ttyACM0",
                        help="Serial port (default: /dev/ttyACM0)")
    parser.add_argument("--execute", action="store_true",
                        help="Actually send the read command (default: dry-run)")
    parser.add_argument("--raw", action="store_true",
                        help="Save and reference raw response")
    args = parser.parse_args(argv)

    info = {"port": args.port, "baud": 115200, "request": "Read Configuration",
            "request_bytes": len(READ_REQUEST), "execute": args.execute}
    print(json.dumps(info))

    if not args.execute:
        print("Dry-run mode. Pass --execute to send the read command.",
              file=sys.stderr)
        return 0

    return read_config(args.port, args.raw)


if __name__ == "__main__":
    sys.exit(main())
