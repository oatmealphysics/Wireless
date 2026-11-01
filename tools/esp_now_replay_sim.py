#!/usr/bin/env python3
"""ESP-NOW Replay Attack Simulator (CVE-2024-42483).

Pure-software simulation of the ESP-NOW anti-replay cache vulnerability.
Demonstrates the cache-overflow replay attack without any RF transmission.

The ESP-NOW protocol uses a 32-slot round-robin cache of (type, magic) tuples
to detect duplicate frames.  CVE-2024-42483 exploits the fact that cleartext
broadcast frames share the same cache as encrypted unicast frames: flooding
32 cleartext entries evicts all encrypted entries, allowing verbatim replay
of previously captured encrypted traffic.

Usage:
    python3 esp_now_replay_sim.py [--verbose] [--demo]

This is an authorized security research tool for the drone-security CTF project.
It does NOT transmit any RF, construct real ESP-NOW frames, or interact with
hardware.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class MsgType(IntEnum):
    """ESP-NOW message types relevant to the cache."""
    BROADCAST_CLEARTEXT = 0
    UNICAST_CLEARTEXT = 1
    BROADCAST_ENCRYPTED = 2
    UNICAST_ENCRYPTED = 3


@dataclass
class EspNowFrame:
    """Simulated ESP-NOW frame with the fields relevant to anti-replay."""
    src_mac: str
    dst_mac: str
    msg_type: MsgType
    magic: int  # 16-bit anti-replay value
    payload: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.magic <= 0xFFFF:
            raise ValueError(f"magic must be 16-bit, got {self.magic}")


# ---------------------------------------------------------------------------
# Pre-patch cache: single shared cache (vulnerable)
# ---------------------------------------------------------------------------

DEFAULT_CACHE_SIZE = 32  # ESP_NOW_CACHE_MSG_MAGIC_NUM


class VulnerableCache:
    """Pre-patch ESP-NOW anti-replay cache (CVE-2024-42483 vulnerable).

    Single shared round-robin cache for all message types.  Cleartext
    broadcast frames can evict entries created by encrypted unicast frames.
    """

    def __init__(self, size: int = DEFAULT_CACHE_SIZE) -> None:
        self.size = size
        self._entries: list[Optional[tuple[int, int]]] = [None] * size
        self._write_idx = 0
        self.accepted = 0
        self.rejected = 0

    def _lookup(self, msg_type: int, magic: int) -> bool:
        """Return True if (msg_type, magic) is in the cache."""
        key = (msg_type, magic)
        return key in self._entries

    def _insert(self, msg_type: int, magic: int) -> None:
        """Insert (msg_type, magic) into the cache at the write pointer."""
        self._entries[self._write_idx] = (msg_type, magic)
        self._write_idx = (self._write_idx + 1) % self.size

    def receive(self, frame: EspNowFrame) -> bool:
        """Process a received frame.  Returns True if accepted, False if
        rejected as duplicate."""
        if self._lookup(frame.msg_type, frame.magic):
            self.rejected += 1
            return False
        self._insert(frame.msg_type, frame.magic)
        self.accepted += 1
        return True

    def contents(self) -> list[Optional[tuple[int, int]]]:
        """Return a copy of the cache contents for inspection."""
        return list(self._entries)


# ---------------------------------------------------------------------------
# Post-patch cache: split caches (mitigated)
# ---------------------------------------------------------------------------

class PatchedCache:
    """Post-patch ESP-NOW anti-replay cache (ESP-NOW >= 2.5.2).

    Separate caches for cleartext and ciphertext messages.  Cleartext
    frames cannot evict ciphertext entries.
    """

    def __init__(self, size: int = DEFAULT_CACHE_SIZE) -> None:
        self.cleartext = VulnerableCache(size)
        self.ciphertext = VulnerableCache(size)
        self.accepted = 0
        self.rejected = 0

    def _is_encrypted(self, msg_type: MsgType) -> bool:
        return msg_type in (MsgType.BROADCAST_ENCRYPTED,
                            MsgType.UNICAST_ENCRYPTED)

    def receive(self, frame: EspNowFrame) -> bool:
        cache = self.ciphertext if self._is_encrypted(frame.msg_type) \
            else self.cleartext
        result = cache.receive(frame)
        if result:
            self.accepted += 1
        else:
            self.rejected += 1
        return result


# ---------------------------------------------------------------------------
# Attack simulation
# ---------------------------------------------------------------------------

ATTACKER_MAC = "AA:BB:CC:DD:EE:FF"
VICTIM_TX_MAC = "11:22:33:44:55:66"
VICTIM_VRX_MAC = "77:88:99:AA:BB:CC"
BROADCAST_MAC = "FF:FF:FF:FF:FF:FF"


def make_legitimate_frame(magic: int,
                          encrypted: bool = False) -> EspNowFrame:
    """Create a simulated legitimate Backpack MSPv2 frame."""
    msg_type = MsgType.UNICAST_ENCRYPTED if encrypted \
        else MsgType.UNICAST_CLEARTEXT
    return EspNowFrame(
        src_mac=VICTIM_TX_MAC,
        dst_mac=VICTIM_VRX_MAC,
        msg_type=msg_type,
        magic=magic,
        payload=b"\x24\x58\x3C\x00\x00\xBC\x01",  # mock MSPv2 VTX set
    )


def make_flood_frame(magic: int) -> EspNowFrame:
    """Create a cleartext broadcast frame for cache flooding."""
    return EspNowFrame(
        src_mac=ATTACKER_MAC,
        dst_mac=BROADCAST_MAC,
        msg_type=MsgType.BROADCAST_CLEARTEXT,
        magic=magic,
    )


@dataclass
class AttackResult:
    """Results of a simulated replay attack."""
    legitimate_accepted: int = 0
    legitimate_rejected_pre_flush: int = 0
    flood_frames_sent: int = 0
    replay_accepted: int = 0
    replay_rejected: int = 0
    attack_succeeded: bool = False


def simulate_replay_attack(
    cache: VulnerableCache | PatchedCache,
    num_legitimate: int = 10,
    num_flood: int = 40,
    encrypted: bool = False,
    verbose: bool = False,
) -> AttackResult:
    """Simulate the full CVE-2024-42483 replay attack.

    Steps:
    1. Send legitimate frames (simulating normal Backpack traffic)
    2. Attempt replay — should be rejected (magic still in cache)
    3. Flood with cleartext broadcast frames to overflow the cache
    4. Attempt replay again — should succeed if cache is vulnerable

    Returns an AttackResult describing what happened.
    """
    result = AttackResult()
    rng = random.Random(42)  # deterministic for reproducibility

    # Step 1: legitimate traffic
    captured_frames: list[EspNowFrame] = []
    for i in range(num_legitimate):
        magic = rng.randint(0, 0xFFFF)
        frame = make_legitimate_frame(magic, encrypted=encrypted)
        accepted = cache.receive(frame)
        if accepted:
            result.legitimate_accepted += 1
            captured_frames.append(frame)
        if verbose:
            status = "ACCEPTED" if accepted else "REJECTED"
            print(f"  [legit {i}] magic=0x{magic:04X} -> {status}")

    if verbose:
        print(f"\n  Captured {len(captured_frames)} frames for replay")

    # Step 2: attempt replay before cache flush (should fail)
    pre_flush_replays = 0
    for frame in captured_frames:
        accepted = cache.receive(frame)
        if accepted:
            pre_flush_replays += 1
        else:
            result.legitimate_rejected_pre_flush += 1
    if verbose:
        print(f"  Pre-flush replay: {pre_flush_replays} accepted, "
              f"{result.legitimate_rejected_pre_flush} rejected")

    # Step 3: flood with cleartext broadcast frames
    for i in range(num_flood):
        magic = 0x8000 + i  # distinct from legitimate traffic
        flood = make_flood_frame(magic)
        cache.receive(flood)
        result.flood_frames_sent += 1
    if verbose:
        print(f"  Flood: sent {result.flood_frames_sent} cleartext "
              f"broadcast frames")

    # Step 4: replay captured frames
    for frame in captured_frames:
        accepted = cache.receive(frame)
        if accepted:
            result.replay_accepted += 1
        else:
            result.replay_rejected += 1
    if verbose:
        print(f"  Post-flush replay: {result.replay_accepted} accepted, "
              f"{result.replay_rejected} rejected")

    result.attack_succeeded = result.replay_accepted > 0
    return result


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def run_demo(verbose: bool = True) -> None:
    """Run the full demonstration comparing vulnerable vs patched caches."""
    print("=" * 70)
    print("ESP-NOW Replay Attack Simulator (CVE-2024-42483)")
    print("=" * 70)
    print()

    for encrypted in (False, True):
        mode = "encrypted" if encrypted else "cleartext"
        print(f"--- Scenario: {mode} legitimate traffic ---")
        print()

        # Vulnerable cache
        print(f"[1] Vulnerable cache (pre-patch, shared):")
        vuln_cache = VulnerableCache()
        vuln_result = simulate_replay_attack(
            vuln_cache, encrypted=encrypted, verbose=verbose)
        print()
        print(f"    Result: {'ATTACK SUCCEEDED' if vuln_result.attack_succeeded else 'Attack failed'}")
        print(f"    Replayed frames accepted: {vuln_result.replay_accepted}/{vuln_result.legitimate_accepted}")
        print()

        # Patched cache
        print(f"[2] Patched cache (post-patch, split):")
        patched_cache = PatchedCache()
        patched_result = simulate_replay_attack(
            patched_cache, encrypted=encrypted, verbose=verbose)
        print()
        print(f"    Result: {'ATTACK SUCCEEDED' if patched_result.attack_succeeded else 'Attack blocked'}")
        print(f"    Replayed frames accepted: {patched_result.replay_accepted}/{patched_result.legitimate_accepted}")
        print()

    # Summary
    print("=" * 70)
    print("Summary:")
    print("  - Vulnerable (shared) cache: cleartext flood evicts all entries,")
    print("    enabling replay of both cleartext and encrypted traffic")
    print("  - Patched (split) cache: cleartext flood cannot evict encrypted")
    print("    entries, blocking the cross-type replay attack")
    print("  - Note: same-type replay (ciphertext flooding ciphertext cache)")
    print("    remains theoretically possible but requires an encryption key")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESP-NOW replay attack simulator (CVE-2024-42483)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show per-frame accept/reject details")
    parser.add_argument("--demo", action="store_true", default=True,
                        help="Run the full demonstration (default)")
    args = parser.parse_args()
    run_demo(verbose=args.verbose)


if __name__ == "__main__":
    main()
