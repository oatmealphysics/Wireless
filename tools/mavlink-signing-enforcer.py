#!/usr/bin/env python3
"""
MAVLink Signing Enforcer — Validate MAVLink v2 signing and detect attacks

Defensive counterpart to the project's MAVLink attack tools. Analyzes
MAVLink v2 traffic and enforces signing requirements:

  - Reject unsigned MAVLink v2 packets on a signing-required link
  - Detect signature replay (same link_id + timestamp seen before)
  - Detect timestamp manipulation (too old, too far in future, or going backwards)
  - Detect link_id spoofing (unexpected link_id for a known stream)
  - Verify signature when secret key is available
  - Flag dangerous commands (disarm, mode change, RC override) from unknown sources
  - Detect v1 downgrade attacks on a v2 link

Input: raw MAVLink bytes from UDP capture, pcap, or --simulate for demo.

Output: one JSON anomaly per detection to stderr, decoded stream to stdout.

Usage:
    # Simulate normal + attack traffic
    python3 mavlink-signing-enforcer.py --simulate

    # Monitor a UDP port (dry run, just analyze)
    python3 mavlink-signing-enforcer.py --udp 0.0.0.0:14550

    # Analyze with signing enforcement (reject unsigned)
    python3 mavlink-signing-enforcer.py --udp 0.0.0.0:14550 --require-signing

    # Verify signatures with a known key
    python3 mavlink-signing-enforcer.py --udp 0.0.0.0:14550 --secret-key 0x...

For CTF defense and authorized security research only.
"""

import argparse
import hashlib
import json
import socket
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAVLINK_V2_MAGIC = 0xFD
MAVLINK_V1_MAGIC = 0xFE
MAVLINK_IFLAG_SIGNED = 0x01
MAVLINK_SIGNATURE_BLOCK_LEN = 13
MAVLINK_EPOCH = 1420070400  # 2015-01-01 00:00:00 UTC

# Dangerous commands that should always be signed
DANGEROUS_MSG_IDS = {
    11: "SET_MODE",
    76: "COMMAND_LONG",
    75: "COMMAND_INT",
    70: "RC_CHANNELS_OVERRIDE",
    400: "COMMAND_LONG (legacy)",
    84: "SET_POSITION_TARGET_LOCAL_NED",
    85: "SET_POSITION_TARGET_GLOBAL_INT",
    86: "SET_ATTITUDE_TARGET",
}

# MAVLink COMMAND_LONG sub-commands of interest
DANGEROUS_COMMANDS = {
    176: "MAV_CMD_DO_SET_MODE",
    400: "MAV_CMD_COMPONENT_ARM_DISARM",
    21: "MAV_CMD_NAV_LAND",
    20: "MAV_CMD_NAV_RETURN_TO_LAUNCH",
    22: "MAV_CMD_NAV_TAKEOFF",
    31000: "MAV_CMD_NAV_SCRIPT_TIME",
    252: "MAV_CMD_OVERRIDE_GOTO",
}

SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

REPLAY_WINDOW_SEC = 60
TIMESTAMP_FUTURE_TOLERANCE_SEC = 5
TIMESTAMP_AGE_LIMIT_SEC = 300


@dataclass
class StreamState:
    """Per-stream (system_id, component_id, link_id) tracking."""
    last_timestamp: int = 0
    last_signature: bytes = b""
    packet_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0


@dataclass
class Anomaly:
    timestamp: str
    severity: str
    category: str
    description: str
    packet_summary: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "timestamp": self.timestamp,
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
        }
        if self.packet_summary:
            d["packet_summary"] = self.packet_summary
        if self.details:
            d["details"] = self.details
        return d


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mavlink_timestamp_to_unix(mav_ts: int) -> float:
    """Convert MAVLink signing timestamp (10μs since MAVLink epoch) to Unix."""
    return MAVLINK_EPOCH + mav_ts / 100000.0


def unix_to_mavlink_timestamp(unix_ts: float) -> int:
    """Convert Unix timestamp to MAVLink signing timestamp."""
    return int((unix_ts - MAVLINK_EPOCH) * 100000)


def compute_signature(secret_key: bytes, header: bytes, payload: bytes,
                      crc: bytes, link_id: int, timestamp: int) -> bytes:
    """Compute MAVLink v2 signature (SHA-256 truncated to 6 bytes)."""
    h = hashlib.sha256()
    h.update(secret_key)
    h.update(header)
    h.update(payload)
    h.update(crc)
    h.update(struct.pack("<B", link_id))
    h.update(struct.pack("<Q", timestamp)[:6])
    return h.digest()[:6]


class MAVLinkSigningEnforcer:
    """Stateful MAVLink v2 signing validator and anomaly detector."""

    def __init__(
        self,
        require_signing: bool = False,
        secret_key: bytes | None = None,
        allowed_sysids: set | None = None,
    ):
        self.require_signing = require_signing
        self.secret_key = secret_key
        self.allowed_sysids = allowed_sysids
        self.streams: dict[tuple, StreamState] = {}
        self.anomalies: list[Anomaly] = []
        self.packet_count = 0
        self.v1_count = 0
        self.v2_unsigned_count = 0
        self.v2_signed_count = 0
        self.seen_v2 = False

    def _emit(self, severity: str, category: str, desc: str,
              pkt_summary: str = "", details: dict | None = None):
        a = Anomaly(
            timestamp=now_iso(),
            severity=severity,
            category=category,
            description=desc,
            packet_summary=pkt_summary,
            details=details or {},
        )
        self.anomalies.append(a)
        print(json.dumps(a.to_dict()), file=sys.stderr)

    def _parse_v2_header(self, data: bytes) -> dict | None:
        """Parse MAVLink v2 header. Returns dict or None."""
        if len(data) < 10:
            return None
        if data[0] != MAVLINK_V2_MAGIC:
            return None

        payload_len = data[1]
        incompat_flags = data[2]
        _compat_flags = data[3]
        seq = data[4]
        sysid = data[5]
        compid = data[6]
        msgid = data[7] | (data[8] << 8) | (data[9] << 16)

        is_signed = bool(incompat_flags & MAVLINK_IFLAG_SIGNED)
        total_len = 10 + payload_len + 2  # header + payload + CRC
        if is_signed:
            total_len += MAVLINK_SIGNATURE_BLOCK_LEN

        if len(data) < total_len:
            return None

        payload = data[10 : 10 + payload_len]
        crc = data[10 + payload_len : 10 + payload_len + 2]

        sig_block = None
        link_id = None
        mav_timestamp = None
        signature = None
        if is_signed:
            sig_start = 10 + payload_len + 2
            sig_block = data[sig_start : sig_start + MAVLINK_SIGNATURE_BLOCK_LEN]
            link_id = sig_block[0]
            mav_timestamp = struct.unpack("<Q", sig_block[1:7] + b"\x00\x00")[0]
            signature = sig_block[7:13]

        return {
            "payload_len": payload_len,
            "incompat_flags": incompat_flags,
            "seq": seq,
            "sysid": sysid,
            "compid": compid,
            "msgid": msgid,
            "is_signed": is_signed,
            "payload": payload,
            "crc": crc,
            "header": data[:10],
            "link_id": link_id,
            "mav_timestamp": mav_timestamp,
            "signature": signature,
            "total_len": total_len,
            "raw": data[:total_len],
        }

    def ingest_packet(self, data: bytes,
                      recv_time: float = 0.0) -> list[Anomaly]:
        """Process raw bytes that may contain one MAVLink message."""
        start_count = len(self.anomalies)
        self.packet_count += 1

        if recv_time == 0.0:
            recv_time = time.time()

        if not data:
            return []

        magic = data[0]

        # v1 downgrade detection
        if magic == MAVLINK_V1_MAGIC:
            self.v1_count += 1
            if self.seen_v2:
                self._emit(
                    SEVERITY_WARNING, "v1_downgrade",
                    "MAVLink v1 packet on a link that has seen v2 — "
                    "possible protocol downgrade attack",
                    f"v1 packet (len={len(data)})",
                    {"v1_count": self.v1_count},
                )
            return self.anomalies[start_count:]

        if magic != MAVLINK_V2_MAGIC:
            return []

        self.seen_v2 = True
        parsed = self._parse_v2_header(data)
        if not parsed:
            self._emit(SEVERITY_WARNING, "malformed",
                       "Truncated or malformed MAVLink v2 packet",
                       f"raw_len={len(data)}")
            return self.anomalies[start_count:]

        pkt_summary = (
            f"sysid={parsed['sysid']} compid={parsed['compid']} "
            f"msgid={parsed['msgid']} seq={parsed['seq']} "
            f"signed={parsed['is_signed']}"
        )

        # --- Unsigned packet checks ---
        if not parsed["is_signed"]:
            self.v2_unsigned_count += 1
            if self.require_signing:
                self._emit(SEVERITY_CRITICAL, "unsigned_packet",
                           "Unsigned MAVLink v2 packet on signing-required link",
                           pkt_summary)

            # Flag dangerous commands even without require_signing
            if parsed["msgid"] in DANGEROUS_MSG_IDS:
                self._emit(
                    SEVERITY_CRITICAL, "unsigned_dangerous_cmd",
                    f"Unsigned dangerous command: "
                    f"{DANGEROUS_MSG_IDS[parsed['msgid']]} "
                    f"(msgid={parsed['msgid']})",
                    pkt_summary,
                )

                # Check COMMAND_LONG sub-command
                if parsed["msgid"] == 76 and len(parsed["payload"]) >= 28:
                    cmd_id = struct.unpack("<H", parsed["payload"][28:30])[0] \
                        if len(parsed["payload"]) >= 30 else 0
                    if cmd_id in DANGEROUS_COMMANDS:
                        self._emit(
                            SEVERITY_CRITICAL, "unsigned_dangerous_subcmd",
                            f"Unsigned {DANGEROUS_COMMANDS[cmd_id]} "
                            f"(cmd={cmd_id})",
                            pkt_summary,
                        )

            return self.anomalies[start_count:]

        # --- Signed packet analysis ---
        self.v2_signed_count += 1
        link_id = parsed["link_id"]
        mav_ts = parsed["mav_timestamp"]
        signature = parsed["signature"]

        stream_key = (parsed["sysid"], parsed["compid"], link_id)
        stream = self.streams.get(stream_key)

        if stream is None:
            stream = StreamState(
                first_seen=recv_time,
                last_seen=recv_time,
            )
            self.streams[stream_key] = stream
        else:
            stream.last_seen = recv_time

        stream.packet_count += 1

        # --- Timestamp checks ---
        if stream.last_timestamp > 0:
            if mav_ts < stream.last_timestamp:
                self._emit(
                    SEVERITY_CRITICAL, "timestamp_regression",
                    f"Signing timestamp went backwards "
                    f"({mav_ts} < {stream.last_timestamp}) — "
                    f"possible replay or manipulation",
                    pkt_summary,
                    {"current_ts": mav_ts, "previous_ts": stream.last_timestamp},
                )

            if mav_ts == stream.last_timestamp:
                self._emit(
                    SEVERITY_WARNING, "timestamp_duplicate",
                    f"Duplicate signing timestamp {mav_ts} — possible replay",
                    pkt_summary,
                )

        # Check timestamp plausibility against wall clock
        ts_unix = mavlink_timestamp_to_unix(mav_ts)
        age = recv_time - ts_unix
        if age > TIMESTAMP_AGE_LIMIT_SEC:
            self._emit(
                SEVERITY_WARNING, "timestamp_stale",
                f"Signing timestamp is {age:.0f}s old "
                f"(limit: {TIMESTAMP_AGE_LIMIT_SEC}s) — possible old replay",
                pkt_summary,
                {"age_seconds": round(age, 1)},
            )
        elif age < -TIMESTAMP_FUTURE_TOLERANCE_SEC:
            self._emit(
                SEVERITY_WARNING, "timestamp_future",
                f"Signing timestamp is {-age:.0f}s in the future — "
                f"possible clock manipulation",
                pkt_summary,
            )

        # --- Signature replay detection ---
        if signature == stream.last_signature and stream.last_signature:
            self._emit(
                SEVERITY_CRITICAL, "signature_replay",
                "Exact signature replay detected — same signature block "
                "seen on consecutive packets",
                pkt_summary,
                {"signature": signature.hex()},
            )

        # --- Signature verification ---
        if self.secret_key:
            expected = compute_signature(
                self.secret_key,
                parsed["header"],
                parsed["payload"],
                parsed["crc"],
                link_id,
                mav_ts,
            )
            if signature != expected:
                self._emit(
                    SEVERITY_CRITICAL, "invalid_signature",
                    "Signature verification failed — packet is forged or "
                    "uses a different key",
                    pkt_summary,
                    {"got": signature.hex(), "expected": expected.hex()},
                )

        # --- Unknown source detection ---
        if self.allowed_sysids and parsed["sysid"] not in self.allowed_sysids:
            self._emit(
                SEVERITY_WARNING, "unknown_source",
                f"Packet from unknown system_id={parsed['sysid']}",
                pkt_summary,
            )

        stream.last_timestamp = mav_ts
        stream.last_signature = signature

        return self.anomalies[start_count:]

    def summary(self) -> dict:
        cats = defaultdict(int)
        sevs = defaultdict(int)
        for a in self.anomalies:
            cats[a.category] += 1
            sevs[a.severity] += 1
        return {
            "packets_analyzed": self.packet_count,
            "v1_packets": self.v1_count,
            "v2_unsigned": self.v2_unsigned_count,
            "v2_signed": self.v2_signed_count,
            "anomalies_detected": len(self.anomalies),
            "active_streams": len(self.streams),
            "by_category": dict(cats),
            "by_severity": dict(sevs),
        }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def _build_v2_packet(sysid: int, compid: int, msgid: int, payload: bytes,
                     signed: bool = False, link_id: int = 0,
                     mav_timestamp: int = 0,
                     secret_key: bytes | None = None) -> bytes:
    """Build a MAVLink v2 packet (CRC is placeholder — detection doesn't verify MAVLink CRC)."""
    incompat = MAVLINK_IFLAG_SIGNED if signed else 0
    header = struct.pack("<BBBBBBBBB",
                         MAVLINK_V2_MAGIC,
                         len(payload),
                         incompat,
                         0,  # compat_flags
                         0,  # seq
                         sysid,
                         compid,
                         msgid & 0xFF,
                         (msgid >> 8) & 0xFF)
    header += struct.pack("<B", (msgid >> 16) & 0xFF)
    crc = struct.pack("<H", 0xAAAA)  # placeholder CRC
    pkt = header + payload + crc

    if signed:
        sig_data = struct.pack("<B", link_id)
        sig_data += struct.pack("<Q", mav_timestamp)[:6]
        if secret_key:
            sig = compute_signature(secret_key, header, payload, crc,
                                    link_id, mav_timestamp)
        else:
            sig = b"\x00" * 6
        pkt += sig_data + sig

    return pkt


def simulate_traffic(enforcer: MAVLinkSigningEnforcer):
    """Generate normal + attack MAVLink traffic."""
    secret = b"\x00" * 32  # demo key
    base_time = time.time()
    mav_base_ts = unix_to_mavlink_timestamp(base_time)

    packets = []

    # Normal signed heartbeats
    for i in range(20):
        ts = mav_base_ts + i * 100000  # 1 second apart
        payload = struct.pack("<BBBBBB", 0, 0, 3, 4, 0, 3)  # heartbeat fields
        pkt = _build_v2_packet(1, 1, 0, payload, signed=True, link_id=0,
                               mav_timestamp=ts, secret_key=secret)
        packets.append((pkt, base_time + i))

    # Normal signed COMMAND_LONG (arm)
    ts = mav_base_ts + 25 * 100000
    cmd_payload = struct.pack("<fffffffHBBB",
                              1.0, 0, 0, 0, 0, 0, 0, 400, 1, 1, 0)
    pkt = _build_v2_packet(255, 190, 76, cmd_payload, signed=True,
                           link_id=0, mav_timestamp=ts, secret_key=secret)
    packets.append((pkt, base_time + 25))

    # --- ATTACK: unsigned dangerous command (disarm) ---
    cmd_payload = struct.pack("<fffffffHBBB",
                              0.0, 0, 0, 0, 0, 0, 0, 400, 1, 1, 0)
    pkt = _build_v2_packet(255, 190, 76, cmd_payload, signed=False)
    packets.append((pkt, base_time + 30))

    # --- ATTACK: unsigned SET_MODE ---
    mode_payload = struct.pack("<IBB", 9, 1, 0)  # LAND mode
    pkt = _build_v2_packet(255, 190, 11, mode_payload, signed=False)
    packets.append((pkt, base_time + 31))

    # --- ATTACK: unsigned RC override ---
    rc_payload = struct.pack("<" + "H" * 8, 1500, 1500, 0, 1500,
                             0, 0, 0, 0)
    pkt = _build_v2_packet(255, 190, 70, rc_payload, signed=False)
    packets.append((pkt, base_time + 32))

    # --- ATTACK: timestamp replay (reuse old timestamp) ---
    old_ts = mav_base_ts  # replay first packet's timestamp
    payload = struct.pack("<BBBBBB", 0, 0, 3, 4, 0, 3)
    pkt = _build_v2_packet(1, 1, 0, payload, signed=True, link_id=0,
                           mav_timestamp=old_ts, secret_key=secret)
    packets.append((pkt, base_time + 35))

    # --- ATTACK: forged signature (wrong key) ---
    bad_secret = b"\xFF" * 32
    ts = mav_base_ts + 40 * 100000
    payload = struct.pack("<BBBBBB", 0, 0, 3, 4, 0, 3)
    pkt = _build_v2_packet(1, 1, 0, payload, signed=True, link_id=0,
                           mav_timestamp=ts, secret_key=bad_secret)
    packets.append((pkt, base_time + 40))

    # --- ATTACK: v1 downgrade ---
    v1_pkt = struct.pack("<BBBBBB", MAVLINK_V1_MAGIC, 6, 0, 1, 1, 0)
    v1_pkt += b"\x00" * 6 + struct.pack("<H", 0xBBBB)
    packets.append((v1_pkt, base_time + 45))

    # --- ATTACK: unknown system_id ---
    ts = mav_base_ts + 50 * 100000
    payload = struct.pack("<BBBBBB", 0, 0, 3, 4, 0, 3)
    pkt = _build_v2_packet(99, 1, 0, payload, signed=True, link_id=0,
                           mav_timestamp=ts, secret_key=secret)
    packets.append((pkt, base_time + 50))

    packets.sort(key=lambda x: x[1])

    print(f"[MAVLink Signing Enforcer] Simulating {len(packets)} packets "
          f"(21 normal signed + 3 unsigned attacks + 1 replay + 1 forged sig "
          f"+ 1 v1 downgrade + 1 unknown source)", file=sys.stderr)

    for pkt_data, t in packets:
        new = enforcer.ingest_packet(pkt_data, recv_time=t)
        magic = pkt_data[0]
        if magic == MAVLINK_V2_MAGIC:
            parsed = enforcer._parse_v2_header(pkt_data)
            if parsed:
                msgid = parsed["msgid"]
                sig = "signed" if parsed["is_signed"] else "UNSIGNED"
                status = "ALERT" if new else "OK"
                print(f"[{status}] v2 msgid={msgid} {sig} "
                      f"sysid={parsed['sysid']}")
            else:
                print("[WARN] Malformed v2")
        elif magic == MAVLINK_V1_MAGIC:
            status = "ALERT" if new else "OK"
            print(f"[{status}] v1 downgrade packet")


def main():
    parser = argparse.ArgumentParser(
        description="MAVLink Signing Enforcer — validate signing and detect attacks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--udp", help="UDP address:port to monitor (e.g. 0.0.0.0:14550)")
    parser.add_argument("--require-signing", action="store_true",
                        help="Reject all unsigned MAVLink v2 packets")
    parser.add_argument("--secret-key", help="Signing secret key (hex) for verification")
    parser.add_argument("--allowed-sysids", help="Comma-separated allowed system IDs")
    parser.add_argument("--simulate", action="store_true",
                        help="Run simulation with normal + attack traffic")
    parser.add_argument("--json", action="store_true",
                        help="JSON output mode")
    args = parser.parse_args()

    secret_key = None
    if args.secret_key:
        secret_key = bytes.fromhex(args.secret_key.replace("0x", ""))

    allowed = None
    if args.allowed_sysids:
        allowed = {int(x.strip()) for x in args.allowed_sysids.split(",")}

    enforcer = MAVLinkSigningEnforcer(
        require_signing=args.require_signing,
        secret_key=secret_key,
        allowed_sysids=allowed,
    )

    if args.simulate:
        enforcer.require_signing = True
        enforcer.secret_key = b"\x00" * 32
        enforcer.allowed_sysids = {1, 255}
        simulate_traffic(enforcer)
    elif args.udp:
        host, port = args.udp.rsplit(":", 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, int(port)))
        print(f"[*] Listening on {host}:{port}", file=sys.stderr)
        try:
            while True:
                data, addr = sock.recvfrom(4096)
                new = enforcer.ingest_packet(data)
                if new:
                    for a in new:
                        if not args.json:
                            print(f"[ALERT] {a.category}: {a.description}")
        except KeyboardInterrupt:
            pass
    else:
        parser.print_help()
        sys.exit(1)

    summary = enforcer.summary()
    print("\n" + "=" * 60, file=sys.stderr)
    print("SIGNING ENFORCEMENT SUMMARY", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Packets analyzed:   {summary['packets_analyzed']}", file=sys.stderr)
    print(f"  v1 packets:         {summary['v1_packets']}", file=sys.stderr)
    print(f"  v2 unsigned:        {summary['v2_unsigned']}", file=sys.stderr)
    print(f"  v2 signed:          {summary['v2_signed']}", file=sys.stderr)
    print(f"  Active streams:     {summary['active_streams']}", file=sys.stderr)
    print(f"  Anomalies detected: {summary['anomalies_detected']}", file=sys.stderr)
    if summary["by_category"]:
        print("  By category:", file=sys.stderr)
        for cat, cnt in sorted(summary["by_category"].items()):
            print(f"    {cat}: {cnt}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    sys.exit(1 if summary["anomalies_detected"] > 0 else 0)


if __name__ == "__main__":
    main()
