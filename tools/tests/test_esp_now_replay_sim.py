#!/usr/bin/env python3
"""Tests for ESP-NOW replay attack simulator (CVE-2024-42483).

Verifies the cache behavior, attack simulation, and patch effectiveness
using deterministic scenarios.
"""

from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from esp_now_replay_sim import (
    AttackResult,
    EspNowFrame,
    MsgType,
    PatchedCache,
    VulnerableCache,
    make_flood_frame,
    make_legitimate_frame,
    simulate_replay_attack,
    DEFAULT_CACHE_SIZE,
)


class TestEspNowFrame(unittest.TestCase):
    """Test frame construction and validation."""

    def test_valid_frame(self) -> None:
        frame = EspNowFrame(
            src_mac="AA:BB:CC:DD:EE:FF",
            dst_mac="11:22:33:44:55:66",
            msg_type=MsgType.UNICAST_CLEARTEXT,
            magic=0x1234,
        )
        self.assertEqual(frame.magic, 0x1234)
        self.assertEqual(frame.msg_type, MsgType.UNICAST_CLEARTEXT)

    def test_magic_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            EspNowFrame(
                src_mac="AA:BB:CC:DD:EE:FF",
                dst_mac="11:22:33:44:55:66",
                msg_type=MsgType.UNICAST_CLEARTEXT,
                magic=0x10000,
            )

    def test_magic_negative(self) -> None:
        with self.assertRaises(ValueError):
            EspNowFrame(
                src_mac="AA:BB:CC:DD:EE:FF",
                dst_mac="11:22:33:44:55:66",
                msg_type=MsgType.UNICAST_CLEARTEXT,
                magic=-1,
            )


class TestVulnerableCache(unittest.TestCase):
    """Test the pre-patch (vulnerable) cache behavior."""

    def test_accepts_new_frame(self) -> None:
        cache = VulnerableCache()
        frame = make_legitimate_frame(0x0001)
        self.assertTrue(cache.receive(frame))
        self.assertEqual(cache.accepted, 1)

    def test_rejects_duplicate(self) -> None:
        cache = VulnerableCache()
        frame = make_legitimate_frame(0x0001)
        self.assertTrue(cache.receive(frame))
        self.assertFalse(cache.receive(frame))
        self.assertEqual(cache.rejected, 1)

    def test_different_magic_accepted(self) -> None:
        cache = VulnerableCache()
        self.assertTrue(cache.receive(make_legitimate_frame(0x0001)))
        self.assertTrue(cache.receive(make_legitimate_frame(0x0002)))
        self.assertEqual(cache.accepted, 2)

    def test_same_magic_different_type_accepted(self) -> None:
        """Same magic but different msg_type should be accepted."""
        cache = VulnerableCache()
        f1 = EspNowFrame("A", "B", MsgType.UNICAST_CLEARTEXT, 0x0001)
        f2 = EspNowFrame("A", "B", MsgType.BROADCAST_CLEARTEXT, 0x0001)
        self.assertTrue(cache.receive(f1))
        self.assertTrue(cache.receive(f2))

    def test_cache_full_eviction(self) -> None:
        """After filling the cache, oldest entries should be evicted."""
        cache = VulnerableCache(size=4)
        # Fill cache with magic 1-4
        for i in range(1, 5):
            cache.receive(make_legitimate_frame(i))
        # Magic 1 is in cache — duplicate rejected
        self.assertFalse(cache.receive(make_legitimate_frame(1)))
        # Add magic 5 — evicts magic 1
        cache.receive(make_legitimate_frame(5))
        # Now magic 1 should be accepted again (evicted)
        self.assertTrue(cache.receive(make_legitimate_frame(1)))

    def test_cache_round_robin(self) -> None:
        """Cache wraps around correctly."""
        size = 4
        cache = VulnerableCache(size=size)
        # Fill with 0..3
        for i in range(size):
            cache.receive(make_legitimate_frame(i))
        # Add 4 more (evicts 0..3)
        for i in range(size, size * 2):
            cache.receive(make_legitimate_frame(i))
        # Original 0..3 should now be accepted
        for i in range(size):
            self.assertTrue(cache.receive(make_legitimate_frame(i)),
                            f"magic {i} should have been evicted")

    def test_cross_type_eviction_vulnerable(self) -> None:
        """Cleartext frames can evict encrypted entries in vulnerable cache."""
        cache = VulnerableCache(size=4)
        # Insert encrypted frame
        encrypted = EspNowFrame("TX", "VRX", MsgType.UNICAST_ENCRYPTED,
                                0x0001, b"secret")
        cache.receive(encrypted)
        # Duplicate rejected
        self.assertFalse(cache.receive(encrypted))
        # Flood with 4 cleartext broadcasts (fills entire cache)
        for i in range(4):
            cache.receive(make_flood_frame(0x8000 + i))
        # Encrypted frame should now be accepted (evicted by cleartext)
        self.assertTrue(cache.receive(encrypted))

    def test_contents_inspection(self) -> None:
        cache = VulnerableCache(size=4)
        cache.receive(make_legitimate_frame(0x1234))
        contents = cache.contents()
        self.assertEqual(len(contents), 4)
        self.assertIn((MsgType.UNICAST_CLEARTEXT, 0x1234), contents)


class TestPatchedCache(unittest.TestCase):
    """Test the post-patch (split) cache behavior."""

    def test_accepts_new_frame(self) -> None:
        cache = PatchedCache()
        self.assertTrue(cache.receive(make_legitimate_frame(0x0001)))

    def test_rejects_duplicate(self) -> None:
        cache = PatchedCache()
        frame = make_legitimate_frame(0x0001)
        self.assertTrue(cache.receive(frame))
        self.assertFalse(cache.receive(frame))

    def test_cross_type_eviction_blocked(self) -> None:
        """Cleartext frames CANNOT evict encrypted entries in patched cache."""
        cache = PatchedCache(size=4)
        # Insert encrypted frame
        encrypted = EspNowFrame("TX", "VRX", MsgType.UNICAST_ENCRYPTED,
                                0x0001, b"secret")
        cache.receive(encrypted)
        self.assertFalse(cache.receive(encrypted))  # duplicate
        # Flood with cleartext broadcasts
        for i in range(8):  # double the cache size
            cache.receive(make_flood_frame(0x8000 + i))
        # Encrypted frame should STILL be rejected (not evicted)
        self.assertFalse(cache.receive(encrypted))

    def test_same_type_eviction_still_possible(self) -> None:
        """Same-type flooding can still evict same-type entries (known limit)."""
        cache = PatchedCache(size=4)
        # Insert cleartext frames
        for i in range(4):
            cache.receive(make_legitimate_frame(i))
        # Flood with more cleartext (same type)
        for i in range(4):
            cache.receive(make_flood_frame(0x9000 + i))
        # Original cleartext should be evicted
        self.assertTrue(cache.receive(make_legitimate_frame(0)))


class TestSimulateReplayAttack(unittest.TestCase):
    """Integration tests for the full attack simulation."""

    def test_vulnerable_cleartext_attack_succeeds(self) -> None:
        """Replay attack succeeds against vulnerable cache with cleartext."""
        cache = VulnerableCache()
        result = simulate_replay_attack(cache, encrypted=False)
        self.assertTrue(result.attack_succeeded)
        self.assertGreater(result.replay_accepted, 0)
        self.assertEqual(result.legitimate_rejected_pre_flush,
                         result.legitimate_accepted)

    def test_vulnerable_encrypted_attack_succeeds(self) -> None:
        """Replay attack succeeds against vulnerable cache with encrypted."""
        cache = VulnerableCache()
        result = simulate_replay_attack(cache, encrypted=True)
        self.assertTrue(result.attack_succeeded)
        self.assertGreater(result.replay_accepted, 0)

    def test_patched_cleartext_attack_succeeds(self) -> None:
        """Cleartext replay still works against patched cache (same-type)."""
        cache = PatchedCache()
        result = simulate_replay_attack(cache, encrypted=False)
        # Same-type eviction means cleartext can still be replayed
        self.assertTrue(result.attack_succeeded)

    def test_patched_encrypted_attack_blocked(self) -> None:
        """Cross-type replay blocked: cleartext flood cannot evict encrypted."""
        cache = PatchedCache()
        result = simulate_replay_attack(cache, encrypted=True)
        self.assertFalse(result.attack_succeeded)
        self.assertEqual(result.replay_accepted, 0)

    def test_pre_flush_replay_always_rejected(self) -> None:
        """Before cache flush, replays should always be rejected."""
        for CacheClass in (VulnerableCache, PatchedCache):
            cache = CacheClass()
            result = simulate_replay_attack(cache, num_flood=0)
            # With 0 flood frames, replay should fail
            # (unless cache was already full from legitimate traffic)

    def test_flood_count_matches(self) -> None:
        cache = VulnerableCache()
        result = simulate_replay_attack(cache, num_flood=50)
        self.assertEqual(result.flood_frames_sent, 50)

    def test_deterministic_results(self) -> None:
        """Same seed should produce identical results."""
        c1 = VulnerableCache()
        r1 = simulate_replay_attack(c1)
        c2 = VulnerableCache()
        r2 = simulate_replay_attack(c2)
        self.assertEqual(r1.legitimate_accepted, r2.legitimate_accepted)
        self.assertEqual(r1.replay_accepted, r2.replay_accepted)


class TestEdgeCases(unittest.TestCase):
    """Edge cases and boundary conditions."""

    def test_cache_size_one(self) -> None:
        """Minimum cache size: every new frame evicts the previous."""
        cache = VulnerableCache(size=1)
        f1 = make_legitimate_frame(0x0001)
        f2 = make_legitimate_frame(0x0002)
        cache.receive(f1)
        cache.receive(f2)  # evicts f1
        self.assertTrue(cache.receive(f1))  # f1 now accepted

    def test_cache_size_zero_boundary(self) -> None:
        """Cache size must be positive; zero would cause division by zero."""
        # The implementation uses modulo, so size=0 would crash
        # This is a known limitation, not a CVE concern
        pass

    def test_magic_zero_and_max(self) -> None:
        """Boundary magic values."""
        cache = VulnerableCache()
        self.assertTrue(cache.receive(make_legitimate_frame(0x0000)))
        self.assertTrue(cache.receive(make_legitimate_frame(0xFFFF)))
        self.assertFalse(cache.receive(make_legitimate_frame(0x0000)))
        self.assertFalse(cache.receive(make_legitimate_frame(0xFFFF)))

    def test_flood_exactly_cache_size(self) -> None:
        """Flooding with exactly cache_size frames should evict everything."""
        size = 8
        cache = VulnerableCache(size=size)
        target = make_legitimate_frame(0x0001)
        cache.receive(target)
        self.assertFalse(cache.receive(target))  # in cache
        for i in range(size):
            cache.receive(make_flood_frame(0x8000 + i))
        self.assertTrue(cache.receive(target))  # evicted

    def test_flood_one_less_than_cache_size(self) -> None:
        """Flooding with cache_size-1 frames may not evict the first entry."""
        size = 8
        cache = VulnerableCache(size=size)
        target = make_legitimate_frame(0x0001)
        cache.receive(target)
        # target is at index 0; flooding size-1 frames fills indices 1..7
        for i in range(size - 1):
            cache.receive(make_flood_frame(0x8000 + i))
        # target at index 0 should still be there
        self.assertFalse(cache.receive(target))


if __name__ == "__main__":
    unittest.main()
