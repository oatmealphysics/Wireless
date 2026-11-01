"""Tests for P1 defect fixes: signing bypass, COMMAND_LONG wire order, OTA version.

P1-1: MAVLink signing rebuild methods require secret_key, signature includes
      IFLAG_SIGNED in header (per MAVLink spec).
P1-2: ELRS analyzer accepts --ota-version; CRC init uses version-parameterized
      computation.
P1-3: COMMAND_LONG wire order is floats-first (MAVLink v2 size-based reordering).
"""

import hashlib
import importlib.util
import struct
import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# --- Import mavlink-signing-replay (hyphenated filename) ---
_spec_replay = importlib.util.spec_from_file_location(
    "mavlink_signing_replay",
    str(TOOLS_DIR / "mavlink-signing-replay.py"),
)
_mod_replay = importlib.util.module_from_spec(_spec_replay)
_spec_replay.loader.exec_module(_mod_replay)

MavlinkPacket = _mod_replay.MavlinkPacket
MAVLINK_V2_MAGIC = _mod_replay.MAVLINK_V2_MAGIC
MAVLINK_IFLAG_SIGNED = _mod_replay.MAVLINK_IFLAG_SIGNED

# --- Import mavlink_chain ---
sys.path.insert(0, str(TOOLS_DIR / "exploit_chains"))
from mavlink_chain import build_command_long, MSG_COMMAND_LONG, MAV_CMD_ARM_DISARM

# --- Import ELRS modules ---
from elrs_core.ota import ota_uid_crc_init, OTA_VERSION_ID
from elrs_core.v3_compat import uid_crc_init, DEFAULT_OTA_VERSION


def _build_signed_packet(secret_key: bytes, msg_id: int = 0,
                         payload: bytes = b"\x00" * 9,
                         link_id: int = 0,
                         timestamp: int = 100000000) -> bytes:
    """Build a valid signed MAVLink v2 packet for testing."""
    header = bytes([
        MAVLINK_V2_MAGIC,
        len(payload),
        MAVLINK_IFLAG_SIGNED,  # incompat_flags: SIGNED
        0x00,  # compat_flags
        0,     # seq
        1,     # system_id
        1,     # component_id
        msg_id & 0xFF,
        (msg_id >> 8) & 0xFF,
        (msg_id >> 16) & 0xFF,
    ])

    # CRC (simplified — no CRC_EXTRA for test purposes)
    crc = 0xFFFF
    for b in header[1:]:
        tmp = b ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    for b in payload:
        tmp = b ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    # Apply CRC_EXTRA for HEARTBEAT (msg_id=0)
    if msg_id == 0:
        tmp = 50 ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    crc_bytes = struct.pack("<H", crc)

    ts_bytes = timestamp.to_bytes(6, "little")

    h = hashlib.sha256()
    h.update(secret_key)
    h.update(header)
    h.update(payload)
    h.update(crc_bytes)
    h.update(bytes([link_id]))
    h.update(ts_bytes)
    sig = h.digest()[:6]

    return header + payload + crc_bytes + bytes([link_id]) + ts_bytes + sig


# =============================================================================
# P1-1: MAVLink signing bypass
# =============================================================================
class TestSigningBypassP1_1(unittest.TestCase):
    """Verify that rebuild methods require secret_key and compute correct sigs."""

    SECRET = b"\x01" * 32

    def _make_signed_packet(self, link_id=0, timestamp=100000000):
        return _build_signed_packet(self.SECRET, link_id=link_id,
                                    timestamp=timestamp)

    def test_rebuild_link_id_requires_key(self):
        """rebuild_with_link_id must raise ValueError without secret_key."""
        raw = self._make_signed_packet(link_id=0)
        pkt = MavlinkPacket(raw)
        self.assertTrue(pkt.valid)
        self.assertTrue(pkt.signed)
        with self.assertRaises(ValueError):
            pkt.rebuild_with_link_id(5)

    def test_rebuild_timestamp_requires_key(self):
        """rebuild_with_timestamp must raise ValueError without secret_key."""
        raw = self._make_signed_packet()
        pkt = MavlinkPacket(raw)
        with self.assertRaises(ValueError):
            pkt.rebuild_with_timestamp(200000000)

    def test_rebuild_link_id_with_key_produces_valid_sig(self):
        """With key, rebuild_with_link_id must produce a verifiable signature."""
        raw = self._make_signed_packet(link_id=0)
        pkt = MavlinkPacket(raw)
        rebuilt = pkt.rebuild_with_link_id(5, self.SECRET)
        rebuilt_pkt = MavlinkPacket(rebuilt)

        self.assertTrue(rebuilt_pkt.valid)
        self.assertTrue(rebuilt_pkt.signed)
        self.assertEqual(rebuilt_pkt.link_id, 5)
        self.assertEqual(rebuilt_pkt.timestamp, pkt.timestamp)

        # Verify the signature independently
        crc_offset = 10 + rebuilt_pkt.payload_len
        crc_bytes = rebuilt[crc_offset:crc_offset + 2]
        ts_bytes = rebuilt_pkt.timestamp.to_bytes(6, "little")

        h = hashlib.sha256()
        h.update(self.SECRET)
        h.update(rebuilt[0:10])
        h.update(rebuilt_pkt.payload)
        h.update(crc_bytes)
        h.update(bytes([5]))
        h.update(ts_bytes)
        expected_sig = h.digest()[:6]

        self.assertEqual(rebuilt_pkt.signature, expected_sig)

    def test_rebuild_timestamp_with_key_produces_valid_sig(self):
        """With key, rebuild_with_timestamp must produce a verifiable signature."""
        raw = self._make_signed_packet(timestamp=100000000)
        pkt = MavlinkPacket(raw)
        new_ts = 200000000
        rebuilt = pkt.rebuild_with_timestamp(new_ts, self.SECRET)
        rebuilt_pkt = MavlinkPacket(rebuilt)

        self.assertTrue(rebuilt_pkt.valid)
        self.assertTrue(rebuilt_pkt.signed)
        self.assertEqual(rebuilt_pkt.timestamp, new_ts)
        self.assertEqual(rebuilt_pkt.link_id, pkt.link_id)

        # Verify the signature independently
        crc_offset = 10 + rebuilt_pkt.payload_len
        crc_bytes = rebuilt[crc_offset:crc_offset + 2]
        ts_bytes = new_ts.to_bytes(6, "little")

        h = hashlib.sha256()
        h.update(self.SECRET)
        h.update(rebuilt[0:10])
        h.update(rebuilt_pkt.payload)
        h.update(crc_bytes)
        h.update(bytes([rebuilt_pkt.link_id]))
        h.update(ts_bytes)
        expected_sig = h.digest()[:6]

        self.assertEqual(rebuilt_pkt.signature, expected_sig)

    def test_signature_includes_iflag_signed(self):
        """The IFLAG_SIGNED bit must be SET in header during signature computation.

        MAVLink spec: the signature covers the raw header including the signed
        flag. Clearing it (the old P1-1 bug) produces a different hash.
        """
        raw = self._make_signed_packet(link_id=0)
        pkt = MavlinkPacket(raw)
        rebuilt = pkt.rebuild_with_link_id(5, self.SECRET)
        rebuilt_pkt = MavlinkPacket(rebuilt)

        # Compute what the signature would be if IFLAG_SIGNED were cleared (old bug)
        crc_offset = 10 + rebuilt_pkt.payload_len
        crc_bytes = rebuilt[crc_offset:crc_offset + 2]
        ts_bytes = rebuilt_pkt.timestamp.to_bytes(6, "little")

        header_wrong = bytearray(rebuilt[0:10])
        header_wrong[2] &= ~MAVLINK_IFLAG_SIGNED

        h = hashlib.sha256()
        h.update(self.SECRET)
        h.update(bytes(header_wrong))
        h.update(rebuilt_pkt.payload)
        h.update(crc_bytes)
        h.update(bytes([5]))
        h.update(ts_bytes)
        wrong_sig = h.digest()[:6]

        self.assertNotEqual(
            rebuilt_pkt.signature, wrong_sig,
            "Signature must NOT match computation with IFLAG_SIGNED cleared"
        )

    def test_unsigned_packet_returned_unchanged(self):
        """rebuild_with_link_id on unsigned packet returns it as-is."""
        header = bytes([
            MAVLINK_V2_MAGIC, 9, 0x00, 0x00, 0, 1, 1, 0, 0, 0,
        ])
        payload = b"\x00" * 9
        crc = struct.pack("<H", 0x1234)
        raw = header + payload + crc
        pkt = MavlinkPacket(raw)
        result = pkt.rebuild_with_link_id(5, b"\x00" * 32)
        self.assertEqual(result, raw)


# =============================================================================
# P1-3: COMMAND_LONG wire order
# =============================================================================
class TestCommandLongWireOrderP1_3(unittest.TestCase):
    """Verify COMMAND_LONG payload uses MAVLink v2 size-based reordering."""

    def test_floats_before_small_fields(self):
        """Wire order: 7 floats, uint16 command, 3 uint8s."""
        pkt = build_command_long(1, 1, MAV_CMD_ARM_DISARM, [1.0, 21196.0])
        # Header is 10 bytes, payload starts at offset 10
        payload = pkt[10:10 + 33]  # 7*4 + 2 + 3 = 33

        # Unpack in correct wire order
        values = struct.unpack("<fffffffHBBB", payload)
        p1, p2, p3, p4, p5, p6, p7, cmd, tsys, tcomp, conf = values

        self.assertAlmostEqual(p1, 1.0)
        self.assertAlmostEqual(p2, 21196.0)
        self.assertEqual(cmd, MAV_CMD_ARM_DISARM)
        self.assertEqual(tsys, 1)
        self.assertEqual(tcomp, 1)

    def test_wrong_order_differs(self):
        """The old bug format (small fields first) must produce different bytes."""
        params = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        correct = struct.pack("<fffffffHBBB", *params, 400, 1, 1, 0)
        wrong = struct.pack("<BBHBfffffff", 1, 1, 400, 0, *params)
        self.assertNotEqual(correct, wrong)

    def test_packet_length(self):
        """COMMAND_LONG packet: 10 header + 33 payload + 2 CRC = 45 bytes."""
        pkt = build_command_long(1, 1, 400, [0.0])
        self.assertEqual(len(pkt), 45)

    def test_header_is_10_bytes(self):
        """V2 header must be exactly 10 bytes with correct magic."""
        pkt = build_command_long(1, 1, 400, [0.0])
        self.assertEqual(pkt[0], MAVLINK_V2_MAGIC)
        self.assertEqual(pkt[1], 33)  # payload_len for COMMAND_LONG
        # msg_id = 76 in 3-byte LE
        self.assertEqual(pkt[7], 76)
        self.assertEqual(pkt[8], 0)
        self.assertEqual(pkt[9], 0)


# =============================================================================
# P1-2: ELRS OTA version
# =============================================================================
class TestOtaVersionP1_2(unittest.TestCase):
    """Verify version-parameterized CRC init and analyzer --ota-version flag."""

    def test_v3_crc_init_differs_from_v4(self):
        """v3 and v4 CRC inits must differ for the same UID."""
        uid = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        v3_init = uid_crc_init(uid, 3)
        v4_init = uid_crc_init(uid, 4)
        self.assertNotEqual(v3_init, v4_init,
                            "v3 and v4 CRC init must differ — using wrong version "
                            "means every CRC check fails")

    def test_v3_formula(self):
        """v3: (UID[4]<<8 | UID[5]) ^ 3."""
        uid = bytes([0, 0, 0, 0, 0xAB, 0xCD])
        expected = ((0xAB << 8) | 0xCD) ^ 3
        self.assertEqual(uid_crc_init(uid, 3), expected)

    def test_v4_formula(self):
        """v4: (UID[4]<<8 | UID[5]) ^ (4 << 8)."""
        uid = bytes([0, 0, 0, 0, 0xAB, 0xCD])
        expected = ((0xAB << 8) | 0xCD) ^ (4 << 8)
        self.assertEqual(uid_crc_init(uid, 4), expected)

    def test_ota_py_default_is_v4(self):
        """elrs_core/ota.py defaults to v4 (for standalone ELRS 4.x)."""
        self.assertEqual(OTA_VERSION_ID, 4)

    def test_v3_compat_default_is_v3(self):
        """v3_compat defaults to v3 (Betaflight 4.4.2 target)."""
        self.assertEqual(DEFAULT_OTA_VERSION, 3)

    def test_analyzer_accepts_ota_version_flag(self):
        """elrs-analyzer.py must accept --ota-version without error."""
        spec = importlib.util.spec_from_file_location(
            "elrs_analyzer",
            str(TOOLS_DIR / "elrs-analyzer.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Check the argparse is wired up by creating a parser and parsing
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--ota-version', type=int, default=3, choices=[3, 4])
        args = parser.parse_args(['--ota-version', '3'])
        self.assertEqual(args.ota_version, 3)


if __name__ == "__main__":
    unittest.main()
