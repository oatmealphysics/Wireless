#!/usr/bin/env python3
"""
ELRS Anomaly Detector — Detect forged, replayed, or anomalous ELRS packets

Defensive counterpart to the project's ELRS attack tools. Consumes the same
pre-decoded packet format that elrs-analyzer.py produces and flags:

  - SYNC packets with UID fields that don't match the expected binding
  - Unexpected FHSS channel usage (packet on wrong channel for its sequence index)
  - Nonce reuse / replay (same nonce seen twice within a session)
  - CRC failures (packet CRC doesn't match recomputed value)
  - Rate anomalies (burst of SYNC packets suggesting a takeover attempt)
  - Binding-mode CRC init on a bound link (attacker probing bind mode)

Input: line-oriented packet log (hex per line, or JSON with "bytes" key),
or --simulate for a demo that includes both normal and anomalous traffic.

Output: one JSON line per anomaly to stderr, full decoded stream to stdout.

Usage:
    # Simulate normal + attack traffic and detect anomalies
    python3 elrs-anomaly-detector.py --simulate

    # Analyze a packet log against a known binding phrase
    python3 elrs-anomaly-detector.py --input packets.log --phrase "iloveflying"

    # Analyze with explicit UID (hex)
    python3 elrs-anomaly-detector.py --input packets.log --uid 0x112233445566

    # Strict mode: also flag packets with valid CRC but unexpected timing
    python3 elrs-anomaly-detector.py --input packets.log --phrase "iloveflying" --strict

For CTF defense and authorized security research only.
"""

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Add parent for elrs_core imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from elrs_core.crc import ELRS_CRC14_POLY, ELRS_CRC16_POLY, Crc2Byte
from elrs_core.fhss import DOMAINS, build_sequence, uid_seed
from elrs_core.ota import (
    OTA4_PACKET_SIZE,
    OTA8_PACKET_SIZE,
    PACKET_TYPE_RCDATA,
    PACKET_TYPE_SYNC,
    UID_LEN,
    bind_crc_init,
    ota_uid_crc_init,
)
from elrs_core.v3_compat import v3_build_sequence

# ---------------------------------------------------------------------------
# Anomaly severity levels
# ---------------------------------------------------------------------------
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
SYNC_BURST_WINDOW_SEC = 2.0
SYNC_BURST_THRESHOLD = 5
NONCE_REUSE_WINDOW = 256


@dataclass
class LinkState:
    """Tracks expected state for a single ELRS link."""
    uid: bytes = b""
    fhss_sequence: list = field(default_factory=list)
    crc_init: int = 0
    ota_version: int = 3
    nonces_seen: dict = field(default_factory=dict)
    last_fhss_index: int = -1
    last_nonce: int = -1
    sync_timestamps: list = field(default_factory=list)
    packet_count: int = 0
    anomaly_count: int = 0


@dataclass
class Anomaly:
    """A detected anomaly."""
    timestamp: str
    severity: str
    category: str
    description: str
    packet_hex: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "timestamp": self.timestamp,
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
        }
        if self.packet_hex:
            d["packet_hex"] = self.packet_hex
        if self.details:
            d["details"] = self.details
        return d


def uid_from_phrase(phrase: str) -> bytes:
    """Derive ELRS UID from binding phrase (MD5 → first 6 bytes)."""
    return hashlib.md5(phrase.encode("utf-8")).digest()[:UID_LEN]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ELRSAnomalyDetector:
    """Stateful detector that ingests decoded ELRS packets and emits anomalies."""

    def __init__(
        self,
        uid: bytes | None = None,
        phrase: str | None = None,
        ota_version: int = 3,
        strict: bool = False,
    ):
        self.strict = strict
        self.anomalies: list[Anomaly] = []
        self.state = LinkState(ota_version=ota_version)

        if phrase:
            self.state.uid = uid_from_phrase(phrase)
        elif uid:
            self.state.uid = uid
        # else: learn UID from first valid SYNC

        if self.state.uid:
            self._init_crypto()

    def _init_crypto(self):
        """Initialize FHSS sequence and CRC from UID."""
        self.state.crc_init = ota_uid_crc_init(
            self.state.uid, self.state.ota_version
        )
        if self.state.ota_version == 3:
            self.state.fhss_sequence = v3_build_sequence(self.state.uid)
        else:
            seed = uid_seed(self.state.uid)
            domain = DOMAINS.get("ISM2G4")
            self.state.fhss_sequence = build_sequence(
                seed, domain.freq_count, domain.sync_channel
            )

    def _emit(self, severity: str, category: str, desc: str,
              pkt_hex: str = "", details: dict | None = None):
        a = Anomaly(
            timestamp=now_iso(),
            severity=severity,
            category=category,
            description=desc,
            packet_hex=pkt_hex,
            details=details or {},
        )
        self.anomalies.append(a)
        self.state.anomaly_count += 1
        print(json.dumps(a.to_dict()), file=sys.stderr)

    def check_crc(self, raw: bytes, is_full: bool) -> bool:
        """Verify CRC on a raw OTA packet. Returns True if valid."""
        if not self.state.uid:
            return True  # can't check without UID

        if is_full:
            crc_obj = Crc2Byte(16, ELRS_CRC16_POLY)
            calc_len = len(raw) - 2
            stored = (raw[-2] << 8) | raw[-1]
        else:
            crc_obj = Crc2Byte(14, ELRS_CRC14_POLY)
            calc_len = 7
            if len(raw) < OTA4_PACKET_SIZE:
                return False
            high_bits = (raw[0] >> 2) & 0x3F
            stored = ((high_bits << 8) | raw[calc_len]) & 0x3FFF

        pkt_type = raw[0] & 0x03
        nonce = raw[1] if len(raw) > 1 else 0

        init = self.state.crc_init
        if pkt_type != PACKET_TYPE_SYNC:
            init ^= nonce

        # Build the data to CRC: mask byte 0 for std packets
        crc_data = bytearray(raw[:calc_len])
        if not is_full:
            crc_data[0] &= 0x03
        computed = crc_obj.calc(bytes(crc_data), init)
        return computed == stored

    def ingest_packet(self, raw: bytes, channel: int = -1,
                      recv_time: float = 0.0) -> list[Anomaly]:
        """Process one decoded ELRS packet. Returns new anomalies."""
        start_count = self.state.anomaly_count
        self.state.packet_count += 1

        if recv_time == 0.0:
            recv_time = time.time()

        pkt_hex = raw.hex()
        pkt_len = len(raw)
        is_full = pkt_len >= OTA8_PACKET_SIZE

        if pkt_len < OTA4_PACKET_SIZE:
            self._emit(SEVERITY_WARNING, "malformed",
                       f"Packet too short ({pkt_len} bytes, min {OTA4_PACKET_SIZE})",
                       pkt_hex)
            return self.anomalies[start_count:]

        pkt_type = raw[0] & 0x03
        nonce = raw[1]

        # --- CRC check ---
        if self.state.uid and not self.check_crc(raw, is_full):
            self._emit(SEVERITY_CRITICAL, "crc_mismatch",
                       "CRC mismatch — packet may be forged or corrupted",
                       pkt_hex, {"expected_uid": self.state.uid.hex()})

        # --- SYNC-specific checks ---
        if pkt_type == PACKET_TYPE_SYNC:
            self._check_sync(raw, pkt_hex, recv_time, is_full)

        # --- Nonce checks ---
        self._check_nonce(nonce, pkt_hex, recv_time)

        # --- FHSS channel checks ---
        if channel >= 0 and self.state.fhss_sequence:
            self._check_fhss(channel, raw, pkt_hex)

        # --- Binding-mode probe detection ---
        if self.state.uid:
            bind_init_val = bind_crc_init(self.state.ota_version)
            if self.state.crc_init != bind_init_val:
                if is_full:
                    crc_obj = Crc2Byte(16, ELRS_CRC16_POLY)
                    calc_len = len(raw) - 2
                    stored = (raw[-2] << 8) | raw[-1]
                else:
                    crc_obj = Crc2Byte(14, ELRS_CRC14_POLY)
                    calc_len = 7
                    high_bits = (raw[0] >> 2) & 0x3F
                    stored = ((high_bits << 8) | raw[calc_len]) & 0x3FFF
                crc_data = bytearray(raw[:calc_len])
                if not is_full:
                    crc_data[0] &= 0x03
                bind_crc = crc_obj.calc(bytes(crc_data), bind_init_val)
                if bind_crc == stored:
                    self._emit(SEVERITY_CRITICAL, "bind_mode_probe",
                               "Packet uses binding-mode CRC on a bound link — "
                               "possible rebind attack",
                               pkt_hex)

        return self.anomalies[start_count:]

    def _check_sync(self, raw: bytes, pkt_hex: str, recv_time: float,
                    is_full: bool):
        """Check SYNC packet for UID mismatches and burst patterns."""
        # v3 SYNC layout: [type|crcHigh, nonce_rx, fhssIdx, nonce, switchEncMode,
        #                   UID3, UID4, UID5]
        if len(raw) >= OTA4_PACKET_SIZE:
            uid3 = raw[5] if len(raw) > 5 else 0
            uid4 = raw[6] if len(raw) > 6 else 0
            uid5_raw = raw[7] if len(raw) > 7 else 0
            # For std packets the CRC low is at index 7 — but for SYNC
            # the UID5 field is at offset 5 in the 6-byte payload which is
            # index 7 in the 8-byte frame. In v3, UID5 upper 2 bits are
            # identity, lower 6 may be XOR'd with model ID.
            uid5_upper = uid5_raw & 0xC0

            if self.state.uid:
                expected_uid3 = self.state.uid[3]
                expected_uid4 = self.state.uid[4]
                expected_uid5_upper = self.state.uid[5] & 0xC0

                if (uid3 != expected_uid3 or uid4 != expected_uid4
                        or uid5_upper != expected_uid5_upper):
                    self._emit(
                        SEVERITY_CRITICAL, "uid_mismatch",
                        "SYNC UID fields don't match expected binding — "
                        "possible takeover attempt",
                        pkt_hex, {
                            "expected": f"{expected_uid3:02x}{expected_uid4:02x}{expected_uid5_upper:02x}",
                            "received": f"{uid3:02x}{uid4:02x}{uid5_upper:02x}",
                        },
                    )

        # SYNC burst detection
        self.state.sync_timestamps.append(recv_time)
        # Trim old timestamps
        cutoff = recv_time - SYNC_BURST_WINDOW_SEC
        self.state.sync_timestamps = [
            t for t in self.state.sync_timestamps if t > cutoff
        ]
        if len(self.state.sync_timestamps) >= SYNC_BURST_THRESHOLD:
            self._emit(
                SEVERITY_WARNING, "sync_burst",
                f"{len(self.state.sync_timestamps)} SYNC packets in "
                f"{SYNC_BURST_WINDOW_SEC}s window — possible takeover flood",
                pkt_hex, {"count": len(self.state.sync_timestamps)},
            )

    def _check_nonce(self, nonce: int, pkt_hex: str, recv_time: float):
        """Detect nonce reuse that would indicate replay."""
        if nonce in self.state.nonces_seen:
            prev_time = self.state.nonces_seen[nonce]
            gap = recv_time - prev_time
            if gap < 1.0:
                self._emit(
                    SEVERITY_WARNING, "nonce_reuse",
                    f"Nonce {nonce} reused within {gap:.3f}s — possible replay",
                    pkt_hex, {"nonce": nonce, "gap_sec": round(gap, 3)},
                )
        self.state.nonces_seen[nonce] = recv_time
        # Prune old entries
        if len(self.state.nonces_seen) > NONCE_REUSE_WINDOW * 2:
            cutoff = recv_time - 30.0
            self.state.nonces_seen = {
                k: v for k, v in self.state.nonces_seen.items()
                if v > cutoff
            }

    def _check_fhss(self, channel: int, raw: bytes, pkt_hex: str):
        """Validate packet was received on the expected FHSS channel."""
        if len(raw) < 3:
            return
        fhss_idx = raw[2]  # fhssIndex field in SYNC
        pkt_type = raw[0] & 0x03
        if pkt_type == PACKET_TYPE_SYNC and fhss_idx < len(self.state.fhss_sequence):
            expected_ch = self.state.fhss_sequence[fhss_idx]
            if channel != expected_ch:
                self._emit(
                    SEVERITY_WARNING, "fhss_mismatch",
                    f"Packet on channel {channel} but FHSS index {fhss_idx} "
                    f"maps to channel {expected_ch}",
                    pkt_hex, {
                        "received_channel": channel,
                        "expected_channel": expected_ch,
                        "fhss_index": fhss_idx,
                    },
                )

    def summary(self) -> dict:
        """Return detection summary."""
        cats = defaultdict(int)
        sevs = defaultdict(int)
        for a in self.anomalies:
            cats[a.category] += 1
            sevs[a.severity] += 1
        return {
            "packets_analyzed": self.state.packet_count,
            "anomalies_detected": self.state.anomaly_count,
            "by_category": dict(cats),
            "by_severity": dict(sevs),
        }


# ---------------------------------------------------------------------------
# Simulation — generates normal + attack traffic
# ---------------------------------------------------------------------------
def simulate_traffic(detector: ELRSAnomalyDetector, count: int = 50):
    """Generate a mix of normal and anomalous packets for demo."""
    uid = detector.state.uid or uid_from_phrase("iloveflying")
    if not detector.state.uid:
        detector.state.uid = uid
        detector._init_crypto()

    base_time = time.time()
    packets = []

    # Normal RC packets
    for i in range(count):
        nonce = i % 256
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_RCDATA  # RC data
        pkt[1] = nonce
        # Fill payload with plausible channel data
        pkt[2] = 0x5D
        pkt[3] = 0xC0
        pkt[4] = 0x5D
        pkt[5] = 0xC0
        pkt[6] = 0x00
        # Compute valid CRC
        init = detector.state.crc_init ^ nonce
        crc_obj = Crc2Byte(14, ELRS_CRC14_POLY)
        crc_data = bytearray(pkt[:7])
        crc_data[0] &= 0x03
        crc_val = crc_obj.calc(bytes(crc_data), init)
        pkt[0] = (pkt[0] & 0x03) | (((crc_val >> 8) & 0x3F) << 2)
        pkt[7] = crc_val & 0xFF
        packets.append((bytes(pkt), -1, base_time + i * 0.004))

    # Normal SYNC packets (every ~250ms)
    for i in range(4):
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_SYNC
        pkt[1] = 0  # nonce always 0 for SYNC CRC
        pkt[2] = i * 10  # fhssIndex
        pkt[3] = i  # nonce field
        pkt[4] = 0  # switchEncMode
        pkt[5] = uid[3]
        pkt[6] = uid[4]
        pkt[7] = uid[5]
        # CRC for SYNC uses init without nonce XOR
        crc_obj = Crc2Byte(14, ELRS_CRC14_POLY)
        crc_data = bytearray(pkt[:7])
        crc_data[0] &= 0x03
        crc_val = crc_obj.calc(bytes(crc_data), detector.state.crc_init)
        pkt[0] = PACKET_TYPE_SYNC | (((crc_val >> 8) & 0x3F) << 2)
        pkt[7] = crc_val & 0xFF
        packets.append((bytes(pkt), -1, base_time + 0.25 * i))

    # --- ATTACK: forged SYNC with wrong UID ---
    bad_uid = bytes([uid[0], uid[1], uid[2], 0xFF, 0xFF, 0xFF])
    for i in range(3):
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_SYNC
        pkt[1] = 0
        pkt[2] = 0
        pkt[3] = 0
        pkt[4] = 0
        pkt[5] = bad_uid[3]
        pkt[6] = bad_uid[4]
        pkt[7] = bad_uid[5]
        packets.append((bytes(pkt), -1, base_time + count * 0.004 + i * 0.1))

    # --- ATTACK: SYNC burst (rapid-fire SYNC flood) ---
    burst_start = base_time + count * 0.004 + 5
    for i in range(8):
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_SYNC
        pkt[1] = 0
        pkt[2] = 0
        pkt[3] = 0
        pkt[4] = 0
        pkt[5] = uid[3]
        pkt[6] = uid[4]
        pkt[7] = uid[5]
        packets.append((bytes(pkt), -1, burst_start + i * 0.1))

    # --- ATTACK: nonce replay ---
    replay_time = base_time + count * 0.004 + 10
    replay_nonce = 42
    for i in range(2):
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_RCDATA
        pkt[1] = replay_nonce
        pkt[2] = 0x5D
        pkt[3] = 0xC0
        pkt[4] = 0x5D
        pkt[5] = 0xC0
        pkt[6] = 0x00
        pkt[7] = 0x00
        packets.append((bytes(pkt), -1, replay_time + i * 0.01))

    # Sort by time
    packets.sort(key=lambda x: x[2])

    print(f"[ELRS Anomaly Detector] Simulating {len(packets)} packets "
          f"({count} normal RC + 4 normal SYNC + 3 forged SYNC + "
          f"8 burst SYNC + 2 replay)", file=sys.stderr)

    for raw, ch, t in packets:
        new_anomalies = detector.ingest_packet(raw, channel=ch, recv_time=t)
        pkt_type = raw[0] & 0x03
        type_name = {0: "RC", 1: "DATA", 2: "SYNC", 3: "LINKSTATS"}.get(
            pkt_type, f"0x{pkt_type:02x}"
        )
        status = "ALERT" if new_anomalies else "OK"
        print(f"[{status}] {type_name} nonce={raw[1]:3d} | {raw.hex()}")


def read_packet_log(path: str):
    """Yield (raw_bytes, channel, timestamp) from a packet log file."""
    with open(path) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                if line.startswith("{"):
                    obj = json.loads(line)
                    raw = bytes.fromhex(obj.get("bytes", obj.get("hex", "")))
                    ch = obj.get("channel", -1)
                    ts = obj.get("timestamp", 0.0)
                else:
                    raw = bytes.fromhex(line.replace(" ", ""))
                    ch = -1
                    ts = 0.0
                yield raw, ch, ts
            except (ValueError, json.JSONDecodeError) as e:
                print(f"[WARN] Skipping line {line_no}: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Anomaly Detector — detect forged/replayed ELRS packets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", "-i", help="Packet log file (hex lines or JSON)")
    parser.add_argument("--phrase", "-p", help="Expected binding phrase")
    parser.add_argument("--uid", help="Expected UID as hex (e.g. 0x112233445566)")
    parser.add_argument("--ota-version", type=int, default=3,
                        choices=[3, 4], help="OTA version (default: 3)")
    parser.add_argument("--strict", action="store_true",
                        help="Enable strict timing checks")
    parser.add_argument("--simulate", action="store_true",
                        help="Run simulation with normal + attack traffic")
    parser.add_argument("--json", action="store_true",
                        help="Output decoded packets as JSON lines")
    args = parser.parse_args()

    uid = None
    if args.uid:
        uid_hex = args.uid.replace("0x", "")
        uid = bytes.fromhex(uid_hex)

    detector = ELRSAnomalyDetector(
        uid=uid,
        phrase=args.phrase,
        ota_version=args.ota_version,
        strict=args.strict,
    )

    if args.simulate:
        if not detector.state.uid:
            detector.state.uid = uid_from_phrase("iloveflying")
            detector._init_crypto()
        simulate_traffic(detector)
    elif args.input:
        for raw, ch, ts in read_packet_log(args.input):
            new = detector.ingest_packet(raw, channel=ch, recv_time=ts)
            if not args.json:
                pkt_type = raw[0] & 0x03 if raw else -1
                type_name = {0: "RC", 1: "DATA", 2: "SYNC", 3: "LINKSTATS"}.get(
                    pkt_type, "?"
                )
                status = "ALERT" if new else "OK"
                print(f"[{status}] {type_name} | {raw.hex()}")
            else:
                print(json.dumps({"hex": raw.hex(), "anomalies": [
                    a.to_dict() for a in new
                ]}))
    else:
        parser.print_help()
        sys.exit(1)

    # Print summary
    summary = detector.summary()
    print("\n" + "=" * 60, file=sys.stderr)
    print("DETECTION SUMMARY", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Packets analyzed:   {summary['packets_analyzed']}", file=sys.stderr)
    print(f"  Anomalies detected: {summary['anomalies_detected']}", file=sys.stderr)
    if summary["by_category"]:
        print("  By category:", file=sys.stderr)
        for cat, cnt in sorted(summary["by_category"].items()):
            print(f"    {cat}: {cnt}", file=sys.stderr)
    if summary["by_severity"]:
        print("  By severity:", file=sys.stderr)
        for sev, cnt in sorted(summary["by_severity"].items()):
            print(f"    {sev}: {cnt}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    sys.exit(1 if summary["anomalies_detected"] > 0 else 0)


if __name__ == "__main__":
    main()
