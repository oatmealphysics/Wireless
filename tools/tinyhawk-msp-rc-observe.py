#!/usr/bin/env python3
"""Bounded, read-only Betaflight 4.4.2 MSP RC-channel observer.

Default behavior prints the planned MSP queries and opens no port. Live reads
require explicit flags, exclusive port access, removed propellers, and the
previously observed TinyHawk identity (BTFL 4.4.2, S411, TH III Plus).
It sends only MSP_API_VERSION, MSP_FC_VARIANT, MSP_FC_VERSION,
MSP_BOARD_INFO, MSP_NAME, and MSP_RC queries. No CLI, setting, reset,
bootloader, or motor command is implemented. It does not access radio FIFO.

Example after the operator has coordinated the port and removed propellers:
  python tools/tinyhawk-msp-rc-observe.py --port COM5 --live-read \
      --props-removed --exclusive-port --duration 10 --interval 0.2 \
      > work/tinyhawk-rc-observation.jsonl

Requires pyserial for --live-read only. Output is private bench evidence;
keep it under ignored work/. Opening a serial port can still alter DTR/RTS
transiently or disrupt an existing configurator session.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time


QUERIES = ((1, "API_VERSION"), (2, "FC_VARIANT"), (3, "FC_VERSION"),
           (4, "BOARD_INFO"), (10, "NAME"), (105, "RC"))


def msp_query(command: int) -> bytes:
    if not 0 <= command <= 255:
        raise ValueError("MSP v1 command must fit in one byte")
    return b"$M<" + bytes((0, command, command))


def read_reply(port, command: int, deadline: float) -> bytes:
    """Read one matching MSP v1 reply, rejecting errors and bad checksums."""
    while time.monotonic() < deadline:
        if port.read(1) != b"$":
            continue
        if port.read(1) != b"M":
            continue
        direction = port.read(1)
        if direction not in (b">", b"!"):
            continue
        header = port.read(2)
        if len(header) != 2:
            continue
        length, reply_command = header
        # MSP v1 permits 255 payload bytes. This board's BOARD_INFO is 87;
        # truncating it to 64 would lose frame alignment.
        payload = port.read(length)
        check = port.read(1)
        if len(payload) != length or len(check) != 1:
            continue
        expected = length ^ reply_command
        for value in payload:
            expected ^= value
        if check[0] != expected:
            continue
        if reply_command != command:
            continue
        if direction == b"!":
            raise ValueError(f"FC rejected MSP command {command}")
        return payload
    raise TimeoutError(f"Timed out waiting for MSP command {command}")


def identify(port) -> None:
    expected = {1: b"\x00\x01\x2d", 2: b"BTFL", 3: b"\x04\x04\x02"}
    for command in (1, 2, 3, 4, 10):
        port.write(msp_query(command))
        payload = read_reply(port, command, time.monotonic() + 1.5)
        if command in expected and payload != expected[command]:
            raise ValueError(f"Unexpected MSP identity reply for command {command}")
        if command == 4 and (len(payload) < 4 or payload[:4] != b"S411"):
            raise ValueError("Board identifier does not match observed S411")
        if command == 10 and payload != b"TH III Plus":
            raise ValueError("Craft name does not match observed TH III Plus")


def decode_rc(payload: bytes) -> list[int]:
    if len(payload) < 8 or len(payload) > 64 or len(payload) % 2:
        raise ValueError("MSP_RC length is not a bounded channel array")
    return list(struct.unpack("<" + "H" * (len(payload) // 2), payload))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="Exact TinyHawk USB serial port")
    parser.add_argument("--duration", type=float, default=10.0, help="Seconds, maximum 30")
    parser.add_argument("--interval", type=float, default=0.2, help="Query spacing, minimum 0.2 s")
    parser.add_argument("--live-read", action="store_true", help="Open port and send read-only queries")
    parser.add_argument("--props-removed", action="store_true", help="Operator confirms props removed")
    parser.add_argument("--exclusive-port", action="store_true", help="Operator confirms no other process owns port")
    args = parser.parse_args(argv)
    if not 0 < args.duration <= 30 or not 0.2 <= args.interval <= 2:
        parser.error("duration must be (0,30] seconds; interval must be [0.2,2] seconds")
    print(f"TinyHawk read-only MSP plan: {args.port}, {args.duration}s, "
          f"{args.interval}s interval; queries: {', '.join(n for _, n in QUERIES)}",
          file=sys.stderr)
    if not args.live_read:
        print("Dry run: no serial port opened", file=sys.stderr)
        return 0
    if not (args.props_removed and args.exclusive_port):
        parser.error("--live-read requires --props-removed and --exclusive-port")
    try:
        import serial  # type: ignore[import-not-found]
        port = serial.Serial(port=None, baudrate=115200, timeout=0.1,
                             write_timeout=0.5, rtscts=False, dsrdtr=False)
        port.dtr = False
        port.rts = False
        port.port = args.port
        port.open()
        try:
            identify(port)
            print("Betaflight 4.4.2 TinyHawk identity verified", file=sys.stderr)
            end = time.monotonic() + args.duration
            while time.monotonic() < end:
                started = time.monotonic()
                port.write(msp_query(105))
                channels = decode_rc(read_reply(port, 105, min(end, started + 1.0)))
                print(json.dumps({"wall_time_ns": time.time_ns(),
                                  "channels_us": channels}), flush=True)
                time.sleep(max(0, args.interval - (time.monotonic() - started)))
        finally:
            port.close()
        return 0
    except (OSError, ValueError, TimeoutError, ImportError) as exc:
        print(f"Stopped without retry or state-changing command: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
