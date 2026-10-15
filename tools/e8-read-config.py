#!/usr/bin/env python3
"""Reproduce only the EMAX ELRS configurator's Read Configuration request.

Python 3.10+, pyserial==3.5. Default is dry-run; --execute sends one immutable
read request at 115200 8-N-1 to a selected VID:PID 8888:2001 endpoint. No write,
update, reset, bootloader, malformed-input or RF operations are supported.
Opening a serial endpoint can affect device control lines despite DTR/RTS false.

Usage: python tools/e8-read-config.py --port COM4 --execute
Raw response can contain binding data and remains under ignored work/. Summary
output does not print that data. Firmware version is not inferred from config.
Request attribution: manufacturer-linked configurator SHA256
7ec41368c1d7fe9cbf7e25ba053b05519f7e1aa8542c698e70764162be99362d,
Qt method 10 -> 0x407fb0 -> 0x404620; frame construction 0x404708–0x404807.
The handler zeroes the 59-byte payload and its CRC16 (zero for zero payload),
then prefixes 02 01 00 and writes 64 bytes. Reply reader: 0x406c60, 17 bytes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

READ_REQUEST = bytes([2, 1]) + bytes(62)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps({"port": args.port, "baud": 115200, "request": "Read Configuration",
                      "request_bytes": len(READ_REQUEST), "execute": args.execute}))
    if not args.execute:
        return 0
    try:
        import serial
        from serial.tools import list_ports
        matches = [port for port in list_ports.comports() if port.device.lower() == args.port.lower()
                   and port.vid == 0x8888 and port.pid == 0x2001]
        if len(matches) != 1:
            raise ValueError("Selected endpoint is not uniquely identified as VID:PID 8888:2001")
        directory = Path(__file__).resolve().parents[1] / "work/e8-session"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.time_ns()
        capture_path = directory / f"vendor-read-{stamp}.bin"
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout, ser.write_timeout = args.port, 115200, 0.1, 1
        ser.dtr = ser.rts = False
        with capture_path.open("xb") as capture:
            ser.open()
            try:
                # Retain unsolicited bytes separately from the response.
                initial = ser.read(4096)
                if initial:
                    (directory / f"vendor-initial-{stamp}.bin").write_bytes(initial)
                sent = ser.write(READ_REQUEST)
                if sent != len(READ_REQUEST):
                    raise OSError("Short serial write")
                ser.flush()
                end = time.monotonic() + 3
                response = bytearray()
                while time.monotonic() < end:
                    chunk = ser.read(min(ser.in_waiting, 4096) or 1)
                    response.extend(chunk)
                    capture.write(chunk)
                summary = {"port": args.port, "response_bytes": len(response),
                           "expected_length": len(response) == 17,
                           "expected_header": bool(response) and response[0] == 2,
                           "raw_saved_privately": str(capture_path.relative_to(directory.parent.parent)),
                           "raw_sha256": hashlib.sha256(response).hexdigest(),
                           "firmware_version": None, "settings_modified": False}
                print(json.dumps(summary))
                return 0 if summary["expected_length"] and summary["expected_header"] else 1
            finally:
                ser.close()
    except (OSError, ValueError, ImportError) as exc:
        print(f"Read diagnostic failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
