#!/usr/bin/env python3
"""
MAVLink Passive Recon Sniffer

Listens for MAVLink v1/v2 packets on a network interface or UDP port and
extracts system information, GPS coordinates, and telemetry. Identifies
MAVLink-speaking drones and ground stations for attack surface mapping.

Supports:
  - UDP multicast/broadcast listening (default MAVLink ports)
  - Raw packet capture via scapy (requires root)
  - Serial port listening (for wired lab testing)

Output is line-oriented for piping into the dashboard. Each line is:
  MAVLINK|<timestamp>|<type>|<json-fields>

Usage:
    mavlink-recon.py                           # listen on UDP 14550/14555
    mavlink-recon.py --udp 0.0.0.0:14550       # specific UDP endpoint
    mavlink-recon.py --serial /dev/ttyUSB0     # serial port
    mavlink-recon.py --pcap capture.pcap       # read from pcap file
    mavlink-recon.py --json                    # JSON lines output

Requires: pymavlink (pip install pymavlink)
Optional: scapy (for raw capture)

For CTF research and authorized security testing only.
"""

import argparse
import json
import socket
import struct
import sys
import time
from datetime import datetime, timezone

# MAVLink constants
MAVLINK_V1_STX = 0xFE
MAVLINK_V2_STX = 0xFD

# MAVLink message IDs we care about
MSG_HEARTBEAT = 0
MSG_SYS_STATUS = 1
MSG_GPS_RAW_INT = 24
MSG_GLOBAL_POSITION_INT = 33
MSG_ATTITUDE = 30
MSG_VFR_HUD = 74
MSG_STATUSTEXT = 253
MSG_COMMAND_LONG = 76
MSG_PARAM_VALUE = 22
MSG_AUTOPILOT_VERSION = 148
MSG_BATTERY_STATUS = 147

# MAVLink type enums
MAV_TYPE = {
    0: "GENERIC", 1: "FIXED_WING", 2: "QUADROTOR", 3: "COAXIAL",
    4: "HELICOPTER", 5: "ANTENNA_TRACKER", 6: "GCS", 7: "AIRSHIP",
    10: "GROUND_ROVER", 11: "SURFACE_BOAT", 12: "SUBMARINE",
    13: "HEXAROTOR", 14: "OCTOROTOR", 15: "TRICOPTER",
    20: "KITE", 22: "VTOL", 26: "GIMBAL", 29: "DECAROTOR",
}

MAV_AUTOPILOT = {
    0: "GENERIC", 1: "RESERVED", 2: "SLUGS", 3: "ARDUPILOTMEGA",
    4: "OPENPILOT", 5: "GENERIC_WP_ONLY", 6: "GENERIC_WP_ANGLES",
    7: "NONE", 8: "INVALID", 9: "PPZ", 10: "UDB", 11: "FP",
    12: "PX4", 13: "SMACCMPILOT", 14: "AUTOQUAD", 15: "ARMAZILA",
    16: "AEROB", 17: "ASLUAV", 18: "SMARTAP", 19: "AIRRAILS",
    20: "REFLEX",
}

MAV_STATE = {
    0: "UNINIT", 1: "BOOT", 2: "CALIBRATING", 3: "STANDBY",
    4: "ACTIVE", 5: "CRITICAL", 6: "EMERGENCY", 7: "POWEROFF",
    8: "FLIGHT_TERMINATION",
}

MAV_MODE_FLAGS = {
    0x01: "CUSTOM", 0x02: "TEST", 0x04: "AUTO", 0x08: "GUIDED",
    0x10: "STABILIZE", 0x20: "HIL", 0x40: "MANUAL", 0x80: "SAFETY_ARMED",
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def emit(msg_type, fields, json_mode=False):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if json_mode:
        out = {"source": "mavlink", "timestamp": ts, "type": msg_type}
        out.update(fields)
        print(json.dumps(out), flush=True)
    else:
        flat = json.dumps(fields)
        print(f"MAVLINK|{ts}|{msg_type}|{flat}", flush=True)


def decode_mode_flags(base_mode):
    flags = []
    for bit, name in MAV_MODE_FLAGS.items():
        if base_mode & bit:
            flags.append(name)
    return flags


class MavlinkParser:
    """Minimal MAVLink v1/v2 frame parser without pymavlink dependency."""

    def __init__(self):
        self.buffer = bytearray()

    def parse(self, data):
        """Feed raw bytes, yield parsed frames."""
        self.buffer.extend(data)
        while len(self.buffer) > 0:
            # Find start byte
            v1_pos = -1
            v2_pos = -1
            try:
                v1_pos = self.buffer.index(MAVLINK_V1_STX)
            except ValueError:
                pass
            try:
                v2_pos = self.buffer.index(MAVLINK_V2_STX)
            except ValueError:
                pass

            if v1_pos == -1 and v2_pos == -1:
                self.buffer.clear()
                return

            # Pick the earlier one
            if v1_pos == -1:
                start = v2_pos
            elif v2_pos == -1:
                start = v1_pos
            else:
                start = min(v1_pos, v2_pos)

            if start > 0:
                self.buffer = self.buffer[start:]

            stx = self.buffer[0]

            if stx == MAVLINK_V1_STX:
                # MAVLink v1: STX LEN SEQ SYS COMP MSGID PAYLOAD CRC16
                if len(self.buffer) < 6:
                    return
                payload_len = self.buffer[1]
                frame_len = 6 + payload_len + 2
                if len(self.buffer) < frame_len:
                    return
                frame = bytes(self.buffer[:frame_len])
                self.buffer = self.buffer[frame_len:]
                yield {
                    "version": 1,
                    "seq": frame[2],
                    "sysid": frame[3],
                    "compid": frame[4],
                    "msgid": frame[5],
                    "payload": frame[6:6 + payload_len],
                }

            elif stx == MAVLINK_V2_STX:
                # MAVLink v2: STX LEN INCOMPAT COMPAT SEQ SYS COMP MSGID(3) PAYLOAD CRC16 [SIG]
                if len(self.buffer) < 10:
                    return
                payload_len = self.buffer[1]
                incompat = self.buffer[2]
                has_sig = bool(incompat & 0x01)
                frame_len = 10 + payload_len + 2 + (13 if has_sig else 0)
                if len(self.buffer) < frame_len:
                    return
                frame = bytes(self.buffer[:frame_len])
                self.buffer = self.buffer[frame_len:]
                msgid = frame[7] | (frame[8] << 8) | (frame[9] << 16)
                yield {
                    "version": 2,
                    "seq": frame[4],
                    "sysid": frame[5],
                    "compid": frame[6],
                    "msgid": msgid,
                    "payload": frame[10:10 + payload_len],
                    "signed": has_sig,
                }

            else:
                self.buffer = self.buffer[1:]


def decode_heartbeat(payload):
    if len(payload) < 9:
        return None
    custom_mode, mav_type, autopilot, base_mode, system_status = struct.unpack_from(
        '<IBBBBB', payload[:9]
    )[:5]
    # Unpack properly
    custom_mode = struct.unpack_from('<I', payload, 0)[0]
    mav_type = payload[4]
    autopilot = payload[5]
    base_mode = payload[6]
    system_status = payload[7]
    return {
        "mav_type": MAV_TYPE.get(mav_type, f"UNKNOWN({mav_type})"),
        "autopilot": MAV_AUTOPILOT.get(autopilot, f"UNKNOWN({autopilot})"),
        "base_mode": base_mode,
        "mode_flags": decode_mode_flags(base_mode),
        "armed": bool(base_mode & 0x80),
        "custom_mode": custom_mode,
        "state": MAV_STATE.get(system_status, f"UNKNOWN({system_status})"),
    }


def decode_gps(payload):
    if len(payload) < 30:
        return None
    time_usec, fix_type, lat, lon, alt = struct.unpack_from('<QBiiI', payload[:21])
    return {
        "fix_type": fix_type,
        "lat": lat / 1e7,
        "lon": lon / 1e7,
        "alt_m": alt / 1000.0,
    }


def decode_global_position(payload):
    if len(payload) < 28:
        return None
    time_boot_ms, lat, lon, alt, relative_alt = struct.unpack_from('<Iiiii', payload[:20])
    return {
        "lat": lat / 1e7,
        "lon": lon / 1e7,
        "alt_m": alt / 1000.0,
        "relative_alt_m": relative_alt / 1000.0,
    }


def decode_attitude(payload):
    if len(payload) < 28:
        return None
    time_boot_ms, roll, pitch, yaw = struct.unpack_from('<Ifff', payload[:16])
    return {
        "roll_deg": round(roll * 57.2958, 1),
        "pitch_deg": round(pitch * 57.2958, 1),
        "yaw_deg": round(yaw * 57.2958, 1),
    }


def decode_statustext(payload):
    if len(payload) < 2:
        return None
    severity = payload[0]
    text = payload[1:51].decode('ascii', errors='replace').rstrip('\x00')
    sev_names = {0: "EMERGENCY", 1: "ALERT", 2: "CRITICAL", 3: "ERROR",
                 4: "WARNING", 5: "NOTICE", 6: "INFO", 7: "DEBUG"}
    return {
        "severity": sev_names.get(severity, str(severity)),
        "text": text,
    }


def listen_udp(host, port, parser, json_mode, systems):
    """Listen for MAVLink on UDP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(1.0)
    log(f"Listening on UDP {host}:{port}")

    while True:
        try:
            data, addr = sock.recvfrom(4096)
        except TimeoutError:
            continue
        except KeyboardInterrupt:
            break

        for frame in parser.parse(data):
            process_frame(frame, json_mode, systems, source=f"{addr[0]}:{addr[1]}")


def listen_serial(port, baud, parser, json_mode, systems):
    """Listen for MAVLink on serial port."""
    try:
        import serial as ser_mod
    except ImportError:
        log("ERROR: pyserial required for serial mode (pip install pyserial)")
        sys.exit(1)

    s = ser_mod.Serial(port, baud, timeout=1)
    log(f"Listening on serial {port} @ {baud}")

    while True:
        try:
            data = s.read(256)
        except KeyboardInterrupt:
            break
        if not data:
            continue
        for frame in parser.parse(data):
            process_frame(frame, json_mode, systems, source=port)


def read_pcap(path, parser, json_mode, systems):
    """Read MAVLink frames from a pcap file."""
    try:
        from scapy.all import UDP as ScapyUDP
        from scapy.all import rdpcap
    except ImportError:
        log("ERROR: scapy required for pcap mode (pip install scapy)")
        sys.exit(1)

    log(f"Reading pcap: {path}")
    packets = rdpcap(path)
    for pkt in packets:
        if pkt.haslayer(ScapyUDP):
            data = bytes(pkt[ScapyUDP].payload)
            for frame in parser.parse(data):
                process_frame(frame, json_mode, systems, source=path)
    log(f"Processed {len(packets)} packets from pcap")


def process_frame(frame, json_mode, systems, source=""):
    """Process a single MAVLink frame."""
    sysid = frame["sysid"]
    compid = frame["compid"]
    msgid = frame["msgid"]
    key = f"{sysid}:{compid}"

    if msgid == MSG_HEARTBEAT:
        info = decode_heartbeat(frame["payload"])
        if info:
            is_new = key not in systems
            systems[key] = {**systems.get(key, {}), **info}
            systems[key]["sysid"] = sysid
            systems[key]["compid"] = compid
            systems[key]["source"] = source
            systems[key]["version"] = frame["version"]
            systems[key]["last_seen"] = time.time()
            if is_new or info.get("armed") != systems[key].get("_prev_armed"):
                emit("HEARTBEAT", {
                    "sysid": sysid, "compid": compid,
                    "source": source, "version": frame["version"],
                    **info, "new_system": is_new,
                }, json_mode)
                systems[key]["_prev_armed"] = info.get("armed")

    elif msgid == MSG_GPS_RAW_INT:
        info = decode_gps(frame["payload"])
        if info and info["fix_type"] >= 2:
            emit("GPS", {"sysid": sysid, "compid": compid, **info}, json_mode)

    elif msgid == MSG_GLOBAL_POSITION_INT:
        info = decode_global_position(frame["payload"])
        if info and (info["lat"] != 0 or info["lon"] != 0):
            emit("POSITION", {"sysid": sysid, "compid": compid, **info}, json_mode)

    elif msgid == MSG_ATTITUDE:
        info = decode_attitude(frame["payload"])
        if info:
            emit("ATTITUDE", {"sysid": sysid, "compid": compid, **info}, json_mode)

    elif msgid == MSG_STATUSTEXT:
        info = decode_statustext(frame["payload"])
        if info:
            emit("STATUSTEXT", {"sysid": sysid, "compid": compid, **info}, json_mode)


def main():
    parser = argparse.ArgumentParser(
        description="MAVLink passive recon sniffer for drone CTF research"
    )
    parser.add_argument('--udp', metavar='HOST:PORT',
                        help='UDP endpoint (default: 0.0.0.0:14550)')
    parser.add_argument('--serial', metavar='PORT',
                        help='Serial port (e.g., /dev/ttyUSB0)')
    parser.add_argument('--baud', type=int, default=57600,
                        help='Serial baud rate (default: 57600)')
    parser.add_argument('--pcap', metavar='FILE',
                        help='Read from pcap file')
    parser.add_argument('--json', action='store_true',
                        help='Output JSON lines')
    parser.add_argument('--multi-port', action='store_true',
                        help='Listen on both 14550 and 14555')
    args = parser.parse_args()

    mav_parser = MavlinkParser()
    systems = {}

    if args.pcap:
        read_pcap(args.pcap, mav_parser, args.json, systems)
    elif args.serial:
        listen_serial(args.serial, args.baud, mav_parser, args.json, systems)
    else:
        host, port = '0.0.0.0', 14550
        if args.udp:
            parts = args.udp.rsplit(':', 1)
            host = parts[0]
            port = int(parts[1]) if len(parts) > 1 else 14550
        listen_udp(host, port, mav_parser, args.json, systems)


if __name__ == '__main__':
    main()
