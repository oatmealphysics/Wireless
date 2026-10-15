#!/usr/bin/env python3
"""
Read-only CRSF serial diagnostics (Python 3.10+, live dependency pyserial==3.5).

Examples:
  python tools/elrs-serial-probe.py --list-ports
  python tools/elrs-serial-probe.py -p /dev/ttyACM0 --capture capture.bin
  python tools/elrs-serial-probe.py --decode capture.bin
  python tools/elrs-serial-probe.py -p COM3 --probe --dump-params

Default: receive only. --probe explicitly enables discovery/configuration reads.
No settings writes or firmware updates are sent. Opening a port can still change
device control lines. USB CRSF support on the E8 remains unconfirmed.
Capture stores all received bytes, including corrupt packets/noise. JSON lines
go to stdout; actions and errors go to stderr. Existing captures are preserved.
"""

import struct
import time
import sys
import argparse
from contextlib import ExitStack
import json
import math
from pathlib import Path

# Live serial access requires pyserial==3.5; offline parsing uses only stdlib.


# Constants from pinned ExpressLRS 4.1.0 src/include/crsf_protocol.h.
CRSF_SYNC_TX = 0xC8
CRSF_SYNC_RX = 0xC8
CRSF_SYNC_MODULE = 0xEE
CRSF_START_BYTES = (0xC8, 0xEA, 0xEC, 0xEE)
CRSF_TYPES = {
    0x02: "GPS", 0x03: "GPS_TIME", 0x07: "VARIO",
    0x08: "BATTERY_SENSOR", 0x09: "BARO_ALTITUDE", 0x0A: "AIRSPEED",
    0x0B: "HEARTBEAT", 0x0C: "RPM", 0x0D: "TEMP", 0x0E: "CELLS",
    0x14: "LINK_STATISTICS", 0x16: "RC_CHANNELS_PACKED",
    0x1E: "ATTITUDE", 0x21: "FLIGHT_MODE", 0x28: "PING_DEVICES",
    0x29: "DEVICE_INFO", 0x2B: "PARAMETER_SETTINGS_ENTRY",
    0x2C: "PARAMETER_READ", 0x2D: "PARAMETER_WRITE", 0x2E: "ELRS_STATUS",
    0x32: "COMMAND", 0x3A: "HANDSET", 0x78: "KISS_REQ",
    0x79: "KISS_RESP", 0x7A: "MSP_REQ", 0x7B: "MSP_RESP",
    0x7C: "MSP_WRITE", 0x80: "ARDUPILOT_RESP",
}
CRSF_ADDR = {
    0x00: "BROADCAST", 0x10: "USB", 0x12: "BLUETOOTH_WIFI",
    0x80: "TBS_CORE_PNP_PRO", 0x8A: "RESERVED1",
    0xC0: "CURRENT_SENSOR", 0xC2: "GPS", 0xC4: "TBS_BLACKBOX",
    0xC8: "FLIGHT_CONTROLLER", 0xCA: "RESERVED2", 0xCC: "RACE_TAG",
    0xEA: "RADIO_TRANSMITTER", 0xEC: "CRSF_RECEIVER",
    0xEE: "CRSF_TRANSMITTER", 0xEF: "ELRS_LUA",
}


def crsf_crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0xD5
            else:
                crc = crc << 1
            crc &= 0xFF
    return crc


def build_crsf_frame(frame_type: int, payload: bytes, sync: int = CRSF_SYNC_RX) -> bytes:
    if len(payload) > 60:
        raise ValueError("CRSF frame exceeds 64 total bytes")
    length = len(payload) + 2  # type + payload + crc
    frame = bytes([sync, length, frame_type]) + payload
    crc = crsf_crc8(frame[2:])  # CRC over type + payload
    return frame + bytes([crc])


def build_ping() -> bytes:
    # CRSF ping: addressed to broadcast
    # Extended frame: [sync][len][type][dest][origin][crc]
    dest = 0x00      # broadcast
    origin = 0xEA    # radio transmitter
    payload = bytes([dest, origin])
    return build_crsf_frame(0x28, payload)


def build_device_info_request() -> bytes:
    dest = 0xEE      # CRSF transmitter module
    origin = 0xEA    # radio transmitter
    payload = bytes([dest, origin])
    return build_crsf_frame(0x28, payload)


def build_param_read(param_index: int, chunk: int = 0) -> bytes:
    dest = 0xEE
    origin = 0xEA
    payload = bytes([dest, origin, param_index, chunk])
    return build_crsf_frame(0x2C, payload)


def parse_frame(data: bytes) -> dict | None:
    if len(data) < 4 or data[0] not in CRSF_START_BYTES:
        return None
    sync = data[0]
    length = data[1]
    if not 2 <= length <= 62 or len(data) < length + 2:
        return None
    frame_type = data[2]
    payload = data[3:2 + length - 1]
    crc_received = data[2 + length - 1]
    crc_calc = crsf_crc8(data[2:2 + length - 1])

    return {
        "sync": sync,
        "length": length,
        "type": frame_type,
        "type_name": CRSF_TYPES.get(frame_type, f"UNKNOWN(0x{frame_type:02X})"),
        "payload": payload,
        "crc_ok": crc_received == crc_calc,
        "raw": data[:2 + length],
    }


class FrameDecoder:
    """Keep partial frames across reads; resynchronize one byte at a time."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        frames = []
        while self.buffer:
            if self.buffer[0] not in CRSF_START_BYTES:
                del self.buffer[0]
                continue
            if len(self.buffer) < 2:
                break
            length = self.buffer[1]
            if not 2 <= length <= 62:
                del self.buffer[0]
                continue
            if len(self.buffer) < length + 2:
                break
            frame = parse_frame(bytes(self.buffer[:length + 2]))
            if frame and frame["crc_ok"]:
                frames.append(frame)
                del self.buffer[:length + 2]
            else:
                del self.buffer[0]
        return frames


def try_read_frames(ser, timeout=2.0, decoder=None, capture=None) -> list[dict]:
    decoder = decoder if decoder is not None else FrameDecoder()
    frames = []
    end_time = time.monotonic() + timeout
    while time.monotonic() < end_time:
        data = ser.read(min(ser.in_waiting, 4096) or 1)
        if data:
            if capture is not None:
                capture.write(data)
            frames.extend(decoder.feed(data))
    if capture is not None:
        capture.flush()
    return frames


def extract_device_info(payload: bytes) -> dict:
    name_end = payload.find(b"\0", 2)
    if name_end < 2 or len(payload) < name_end + 15:
        return {}
    rest = payload[name_end + 1:]
    return {
        "dest": CRSF_ADDR.get(payload[0], f"0x{payload[0]:02X}"),
        "origin": CRSF_ADDR.get(payload[1], f"0x{payload[1]:02X}"),
        "name": payload[2:name_end].decode("ascii", errors="replace"),
        "serial_no": rest[:4].hex(),
        "hw_ver": struct.unpack(">I", rest[4:8])[0],
        "sw_ver": struct.unpack(">I", rest[8:12])[0],
        "param_count": rest[12], "protocol_ver": rest[13],
    }


def log(message):
    print(message, file=sys.stderr)


def emit_frames(frames):
    for frame in frames:
        output = {key: value.hex() if isinstance(value, bytes) else value
                  for key, value in frame.items()}
        if frame["type"] == 0x29:
            output["device_info"] = extract_device_info(frame["payload"])
        print(json.dumps(output))


def positive_timeout(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--port", help="Explicit serial endpoint; use --list-ports first")
    parser.add_argument("-b", "--baud", type=int, default=460800)
    parser.add_argument("-t", "--timeout", type=positive_timeout, default=3.0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--probe", action="store_true", help="Send device-info and parameter-read requests")
    mode.add_argument("--listen-only", action="store_true", help="Receive only (default)")
    parser.add_argument("--dump-params", action="store_true", help="Requires --probe")
    parser.add_argument("--capture", type=Path, help="New raw RX capture; never overwrite")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--decode", type=Path, help="Decode a raw capture offline")
    source.add_argument("--list-ports", action="store_true", help="List serial descriptors")
    args = parser.parse_args(argv)
    if args.dump_params and not args.probe:
        parser.error("--dump-params requires --probe")
    if (args.decode or args.list_ports) and (args.port or args.capture or args.probe or args.listen_only):
        parser.error("offline decoding/port listing cannot be combined with live options")
    if not args.decode and not args.list_ports and not args.port:
        parser.error("specify --port or use --list-ports / --decode")
    if args.baud <= 0:
        parser.error("baud must be positive")
    try:
        if args.decode:
            decoder = FrameDecoder()
            with args.decode.open("rb") as stream:
                while data := stream.read(4096):
                    emit_frames(decoder.feed(data))
            log(f"Trailing undecoded bytes: {len(decoder.buffer)}")
            return 0
        try:
            import serial
            from serial.tools import list_ports
        except ImportError:
            log("Install live dependency: python -m pip install pyserial==3.5")
            return 1
        if args.list_ports:
            for port in list_ports.comports():
                print(json.dumps({"port": port.device, "description": port.description,
                                  "hwid": port.hwid, "vid": port.vid, "pid": port.pid,
                                  "serial_number": port.serial_number}))
            return 0
        with ExitStack() as stack:
            if args.capture:
                args.capture.parent.mkdir(parents=True, exist_ok=True)
            capture = stack.enter_context(args.capture.open("xb")) if args.capture else None
            log(f"Opening {args.port} at {args.baud}; mode={'probe' if args.probe else 'receive only'}")
            # Opening a serial device does not prove correct baud or protocol.
            ser = stack.enter_context(serial.Serial(args.port, args.baud, timeout=0.05))
            decoder = FrameDecoder()

            def receive(timeout=args.timeout):
                frames = try_read_frames(ser, timeout, decoder, capture)
                emit_frames(frames)
                return frames

            def request(frame):
                log(f"TX {frame.hex()}")
                ser.write(frame)
                ser.flush()
                return receive()

            frames = receive()
            log(f"Passive capture: {len(frames)} valid frames")
            if not args.probe:
                return 0

            def module_info(items):
                for item in items:
                    payload = item["payload"]
                    if item["type"] == 0x29 and payload[:2] == bytes([0xEA, 0xEE]):
                        info = extract_device_info(payload)
                        if info:
                            return info
                return None

            info = module_info(request(build_ping()))
            if info is None:
                info = module_info(request(build_device_info_request()))
            if info is None:
                log("No valid module device-info response; USB CRSF support remains unconfirmed")
                return 1
            incomplete = False
            if args.dump_params:
                for index in range(1, info["param_count"] + 1):
                    data = bytearray()
                    previous_remaining = None
                    for chunk in range(256):
                        replies = request(build_param_read(index, chunk))
                        matches = [f["payload"] for f in replies if f["type"] == 0x2B
                                   and len(f["payload"]) >= 4
                                   and f["payload"][:3] == bytes([0xEA, 0xEE, index])]
                        if len(matches) != 1:
                            log(f"Parameter {index}: expected one response, received {len(matches)}; incomplete")
                            incomplete = True
                            break
                        payload = matches[0]
                        remaining = payload[3]
                        if previous_remaining is not None and remaining != previous_remaining - 1:
                            log(f"Parameter {index}: inconsistent chunk sequence; incomplete")
                            incomplete = True
                            break
                        data.extend(payload[4:])
                        if remaining == 0:
                            print(json.dumps({"parameter": index, "raw_data": data.hex(), "complete": True}))
                            break
                        previous_remaining = remaining
                    else:
                        log(f"Parameter {index}: chunk limit reached; incomplete")
                        incomplete = True
            return 1 if incomplete else 0
    except (OSError, ValueError) as exc:
        log(f"Diagnostic failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
