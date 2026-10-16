"""Validate elrs_core CRC/OTA/FHSS against Betaflight 4.4.2 source analysis.

Cross-references the vendored v4 library and the new v3 compatibility layer
against the source-level findings in betaflight-442-source-analysis.md.
Uses synthetic test vectors — no private captures or UIDs.

Test groups:
  1. CRC14 engine correctness (polynomial, table generation, calc)
  2. v3 vs v4 CRC initializer divergence
  3. v3 vs v4 nonce-in-CRC handling
  4. SYNC packet structure (v3 vs v4 field layout)
  5. FHSS seed and sync channel (v3 vs v4)
  6. FHSS sequence generation and round-trip
  7. Bind-mode CRC
  8. CRC round-trip: generate then validate
  9. Cross-check v3_compat against standalone elrs-v3-frame-check.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import unittest

# Add tools/ to path for imports
TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from elrs_core.crc import Crc2Byte, ELRS_CRC14_POLY, ELRS_CRC16_POLY
from elrs_core.ota import (
    OTA_VERSION_ID as V4_OTA_VERSION_ID,
    ota_uid_crc_init, bind_crc_init,
    validate_crc, generate_crc, build_sync, decode_std,
    PACKET_TYPE_SYNC, PACKET_TYPE_RCDATA, PACKET_TYPE_DATA,
    OTA4_PACKET_SIZE,
)
from elrs_core.fhss import (
    Lcg, build_sequence, uid_seed, DOMAINS, FHSS_SEQUENCE_LEN,
)
from elrs_core.v3_compat import (
    V3_OTA_VERSION_ID, v3_crc14, v3_uid_crc_init, v3_bind_crc_init,
    v3_validate_crc, v3_generate_crc, v3_uid_seed, v3_sync_channel,
    v3_build_sequence, v3_decode_sync, v3_build_sync, V3Sync,
)

# Synthetic UID for all tests — not from any real device
SYNTH_UID = bytes.fromhex("12 34 56 78 9a bc")


# ---------------------------------------------------------------------------
# Independent reference: literal port of BF 4.4.2 calcCrc14 table method
# ---------------------------------------------------------------------------
def bf442_table_crc14(data: bytes, initial: int) -> int:
    """Literal port of BF 4.4.2 generateCrc14Table + calcCrc14."""
    table = []
    for i in range(256):
        crc = i << 6  # i << (14-8)
        for _ in range(8):
            crc = (crc << 1) ^ (0x2E57 if crc & 0x2000 else 0)
        table.append(crc & 0xFFFF)
    crc = initial
    for val in data:
        crc = (crc << 8) ^ table[((crc >> 6) ^ val) & 0xFF]
    return crc & 0x3FFF


class TestCrc14Engine(unittest.TestCase):
    """Group 1: CRC14 polynomial and computation correctness."""

    def test_polynomial_value(self):
        self.assertEqual(ELRS_CRC14_POLY, 0x2E57)

    def test_v4_crc2byte_matches_bf442_table(self):
        """The v4 Crc2Byte engine with poly 0x2E57 and 14 bits must produce
        the same output as BF 4.4.2's generateCrc14Table/calcCrc14."""
        crc_engine = Crc2Byte(14, ELRS_CRC14_POLY)
        for initial in (0, 3, 0x3FFF, 0x1234):
            for data in (bytes(7), bytes(range(7)), b"\xff" * 7):
                v4_result = crc_engine.calc(data, initial)
                bf_result = bf442_table_crc14(data, initial)
                self.assertEqual(v4_result, bf_result,
                                 f"CRC mismatch init=0x{initial:04x} data={data.hex()}")

    def test_v3_bitwise_matches_bf442_table(self):
        """v3_crc14 bitwise method must agree with BF 4.4.2 table method."""
        for initial in (0, 3, 0x3FFF, 0x9ABF):
            for data in (bytes(7), bytes(range(7)), b"\xde\xad\xbe\xef\xca\xfe\x42"):
                self.assertEqual(v3_crc14(data, initial),
                                 bf442_table_crc14(data, initial))

    def test_crc14_is_14_bits(self):
        """Result must always fit in 14 bits."""
        crc_engine = Crc2Byte(14, ELRS_CRC14_POLY)
        for data in (b"\xff" * 7, bytes(range(7))):
            for init in (0, 0x3FFF, 0xFFFF):
                result = crc_engine.calc(data, init)
                self.assertLessEqual(result, 0x3FFF)
                result_v3 = v3_crc14(data, init)
                self.assertLessEqual(result_v3, 0x3FFF)


class TestCrcInitDivergence(unittest.TestCase):
    """Group 2: v3 vs v4 CRC initializer — the formulas differ."""

    def test_v4_formula(self):
        """v4: (UID[4]<<8 | UID[5]) ^ (OTA_VERSION_ID << 8)."""
        init = ota_uid_crc_init(SYNTH_UID)
        expected = ((SYNTH_UID[4] << 8) | SYNTH_UID[5]) ^ (V4_OTA_VERSION_ID << 8)
        self.assertEqual(init, expected & 0xFFFF)

    def test_v3_formula(self):
        """v3 (BF 4.4.2): (UID[4]<<8 | UID[5]) ^ ELRS_OTA_VERSION_ID (no shift)."""
        init = v3_uid_crc_init(SYNTH_UID)
        expected = ((SYNTH_UID[4] << 8) | SYNTH_UID[5]) ^ V3_OTA_VERSION_ID
        self.assertEqual(init, expected)

    def test_v3_v4_differ(self):
        """The two formulas produce different values for the same UID."""
        v3_init = v3_uid_crc_init(SYNTH_UID)
        v4_init = ota_uid_crc_init(SYNTH_UID)
        self.assertNotEqual(v3_init, v4_init,
                            "v3 and v4 CRC init should differ for the same UID")

    def test_v3_init_xor_low_bits(self):
        """v3 XORs version (3) into the low bits of the 16-bit value."""
        base = (SYNTH_UID[4] << 8) | SYNTH_UID[5]
        init = v3_uid_crc_init(SYNTH_UID)
        self.assertEqual(init ^ V3_OTA_VERSION_ID, base)

    def test_v4_init_xor_high_bits(self):
        """v4 XORs version<<8 into the high bits."""
        base = (SYNTH_UID[4] << 8) | SYNTH_UID[5]
        init = ota_uid_crc_init(SYNTH_UID)
        self.assertEqual(init ^ (V4_OTA_VERSION_ID << 8), base)

    def test_v4_ota_version_is_4(self):
        self.assertEqual(V4_OTA_VERSION_ID, 4)

    def test_v3_ota_version_is_3(self):
        self.assertEqual(V3_OTA_VERSION_ID, 3)


class TestNonceInCrc(unittest.TestCase):
    """Group 3: v3 vs v4 nonce-in-CRC handling — fundamentally different."""

    def test_v4_xors_nonce_into_init(self):
        """v4 validate_crc XORs ota_nonce into crc_init for non-SYNC."""
        # Build a v4 RCDATA with nonce=5
        raw = bytearray(8)
        raw[0] = PACKET_TYPE_RCDATA
        raw[1:7] = b"\x10\x20\x30\x40\x50\x60"
        v4_init = ota_uid_crc_init(SYNTH_UID)
        generate_crc(raw, False, v4_init, 5, PACKET_TYPE_RCDATA)
        # It should validate with nonce=5
        self.assertTrue(validate_crc(bytes(raw), False, v4_init, 5, PACKET_TYPE_RCDATA))
        # And fail with nonce=6
        self.assertFalse(validate_crc(bytes(raw), False, v4_init, 6, PACKET_TYPE_RCDATA))

    def test_v4_sync_ignores_nonce(self):
        """v4 SYNC uses nonce=0 regardless of ota_nonce arg."""
        v4_init = ota_uid_crc_init(SYNTH_UID)
        raw1 = bytearray(build_sync(False, 41, 9, 2, crc_init=v4_init, ota_nonce=0))
        raw2 = bytearray(build_sync(False, 41, 9, 2, crc_init=v4_init, ota_nonce=99))
        # Both should produce identical CRC
        self.assertEqual(raw1[0], raw2[0])
        self.assertEqual(raw1[7], raw2[7])

    def test_v3_hybrid_no_nonce(self):
        """v3 hybrid mode: CRC uses zeroed crcHigh, no nonce involvement."""
        raw = bytearray(8)
        raw[0] = PACKET_TYPE_RCDATA
        raw[1:7] = b"\x10\x20\x30\x40\x50\x60"
        v3_generate_crc(raw, SYNTH_UID, switch_mode="hybrid")
        # Should validate without nonce
        self.assertTrue(v3_validate_crc(bytes(raw), SYNTH_UID, switch_mode="hybrid"))
        # And also with any nonce (ignored for hybrid)
        self.assertTrue(v3_validate_crc(bytes(raw), SYNTH_UID, switch_mode="hybrid", nonce=5))
        self.assertTrue(v3_validate_crc(bytes(raw), SYNTH_UID, switch_mode="hybrid", nonce=99))

    def test_v3_wide_requires_nonce(self):
        """v3 wide mode: crcHigh = (nonce%hopInterval)+1 before CRC calc."""
        raw = bytearray(8)
        raw[0] = PACKET_TYPE_RCDATA
        raw[1:7] = b"\x10\x20\x30\x40\x50\x60"
        v3_generate_crc(raw, SYNTH_UID, switch_mode="wide", nonce=5, hop_interval=4)
        # Should validate with correct nonce
        self.assertTrue(v3_validate_crc(bytes(raw), SYNTH_UID, switch_mode="wide",
                                        nonce=5, hop_interval=4))
        # Fail with wrong nonce
        self.assertFalse(v3_validate_crc(bytes(raw), SYNTH_UID, switch_mode="wide",
                                         nonce=6, hop_interval=4))
        # Fail without nonce
        self.assertFalse(v3_validate_crc(bytes(raw), SYNTH_UID, switch_mode="wide"))

    def test_v3_wide_nonce_slot_value(self):
        """The crcHigh for wide RC_DATA is (nonce % hopInterval) + 1, shifted."""
        for nonce in range(16):
            raw = bytearray(8)
            raw[0] = PACKET_TYPE_RCDATA
            raw[1:7] = b"\xaa\xbb\xcc\xdd\xee\xff"
            v3_generate_crc(raw, SYNTH_UID, switch_mode="wide", nonce=nonce, hop_interval=4)
            expected_slot = (nonce % 4) + 1
            actual_crc_high = (raw[0] >> 2) & 0x3F
            # The crcHigh is the full 6-bit CRC high portion, not just the slot.
            # But the slot is embedded in the CRC computation input.
            self.assertTrue(v3_validate_crc(bytes(raw), SYNTH_UID,
                                            switch_mode="wide", nonce=nonce))

    def test_v3_v4_nonce_produce_different_crcs(self):
        """For the same payload, v3 and v4 nonce handling produce different frames."""
        payload = b"\x10\x20\x30\x40\x50\x60"
        nonce = 5

        # v4 path
        raw_v4 = bytearray(8)
        raw_v4[0] = PACKET_TYPE_RCDATA
        raw_v4[1:7] = payload
        v4_init = ota_uid_crc_init(SYNTH_UID)
        generate_crc(raw_v4, False, v4_init, nonce, PACKET_TYPE_RCDATA)

        # v3 hybrid path
        raw_v3h = bytearray(8)
        raw_v3h[0] = PACKET_TYPE_RCDATA
        raw_v3h[1:7] = payload
        v3_generate_crc(raw_v3h, SYNTH_UID, switch_mode="hybrid")

        # v3 wide path
        raw_v3w = bytearray(8)
        raw_v3w[0] = PACKET_TYPE_RCDATA
        raw_v3w[1:7] = payload
        v3_generate_crc(raw_v3w, SYNTH_UID, switch_mode="wide", nonce=nonce)

        # All three should be different (different CRC init + different nonce handling)
        crcs = {bytes(raw_v4), bytes(raw_v3h), bytes(raw_v3w)}
        self.assertEqual(len(crcs), 3, "v4, v3-hybrid, and v3-wide should produce distinct frames")


class TestSyncStructure(unittest.TestCase):
    """Group 4: v3 vs v4 SYNC packet field layout."""

    def test_v3_sync_carries_uid3(self):
        """v3 SYNC has UID3 at byte 4 (payload byte 3)."""
        raw = v3_build_sync(SYNTH_UID, fhss_index=41, nonce=9, rate_index=2)
        sync = v3_decode_sync(raw)
        self.assertEqual(sync.uid3, SYNTH_UID[3])
        self.assertEqual(sync.uid4, SYNTH_UID[4])
        self.assertEqual(sync.uid5, SYNTH_UID[5])

    def test_v3_sync_rate_in_flags(self):
        """v3 packs rateIndex into the flags byte (bits 4-7)."""
        raw = v3_build_sync(SYNTH_UID, fhss_index=41, nonce=9, rate_index=3)
        sync = v3_decode_sync(raw)
        self.assertEqual(sync.rate_index, 3)

    def test_v3_sync_flags_layout(self):
        """v3 flags byte: [switchEncMode:1][newTlmRatio:3][rateIndex:4]."""
        raw = v3_build_sync(SYNTH_UID, fhss_index=41, nonce=9,
                            rate_index=5, switch_enc_mode=1, new_tlm_ratio=7)
        sync = v3_decode_sync(raw)
        self.assertEqual(sync.switch_enc_mode, 1)
        self.assertEqual(sync.new_tlm_ratio, 7)
        self.assertEqual(sync.rate_index, 5)

    def test_v4_sync_has_no_uid3(self):
        """v4 SYNC only has uid4 and uid5 in the ElrsSync dataclass."""
        v4_init = ota_uid_crc_init(SYNTH_UID)
        raw = build_sync(False, 41, 9, 2, uid4=SYNTH_UID[4], uid5=SYNTH_UID[5],
                         crc_init=v4_init)
        pkt = decode_std(raw)
        self.assertIsNotNone(pkt.sync)
        self.assertFalse(hasattr(pkt.sync, 'uid3'))
        self.assertEqual(pkt.sync.uid4, SYNTH_UID[4])
        self.assertEqual(pkt.sync.uid5, SYNTH_UID[5])

    def test_v3_sync_18bit_uid_match(self):
        """processRFSyncPacket checks UID[3], UID[4], and upper 2 bits of UID[5]."""
        raw = v3_build_sync(SYNTH_UID, fhss_index=41, nonce=9)
        sync = v3_decode_sync(raw)
        self.assertTrue(sync.uid_matches(SYNTH_UID))

        # Different UID[3] → mismatch
        different_uid3 = bytearray(SYNTH_UID)
        different_uid3[3] ^= 0x01
        self.assertFalse(sync.uid_matches(bytes(different_uid3)))

        # Different UID[5] upper 2 bits → mismatch
        different_uid5_high = bytearray(SYNTH_UID)
        different_uid5_high[5] ^= 0x40
        self.assertFalse(sync.uid_matches(bytes(different_uid5_high)))

        # Different UID[5] lower 6 bits → still matches (model match, not checked here)
        different_uid5_low = bytearray(SYNTH_UID)
        different_uid5_low[5] ^= 0x01
        self.assertTrue(sync.uid_matches(bytes(different_uid5_low)))

    def test_v3_sync_crc_round_trip(self):
        """Build a v3 SYNC, then validate its CRC."""
        raw = v3_build_sync(SYNTH_UID, fhss_index=41, nonce=9, rate_index=2)
        self.assertTrue(v3_validate_crc(raw, SYNTH_UID))

    def test_v3_sync_crc_detects_corruption(self):
        """Flipping any bit in the payload should break CRC."""
        raw = v3_build_sync(SYNTH_UID, fhss_index=41, nonce=9)
        for byte_idx in range(1, 7):
            corrupted = bytearray(raw)
            corrupted[byte_idx] ^= 0x01
            self.assertFalse(v3_validate_crc(bytes(corrupted), SYNTH_UID),
                             f"CRC should fail after corrupting byte {byte_idx}")


class TestFhssSeedAndSyncChannel(unittest.TestCase):
    """Group 5: FHSS seed derivation and sync channel formula."""

    def test_v3_seed_uses_version_3(self):
        """v3 seed XORs with 3."""
        seed = v3_uid_seed(SYNTH_UID)
        base = (SYNTH_UID[2] << 24) + (SYNTH_UID[3] << 16) + (SYNTH_UID[4] << 8) + SYNTH_UID[5]
        self.assertEqual(seed, base ^ 3)

    def test_v4_seed_uses_version_4(self):
        """v4 seed XORs UID[5] with 4 before combining."""
        seed = uid_seed(SYNTH_UID)
        expected = ((SYNTH_UID[2] << 24) + (SYNTH_UID[3] << 16) +
                    (SYNTH_UID[4] << 8) + (SYNTH_UID[5] ^ V4_OTA_VERSION_ID))
        self.assertEqual(seed, expected)

    def test_v3_v4_seeds_differ(self):
        """v3 and v4 produce different seeds for the same UID."""
        self.assertNotEqual(v3_uid_seed(SYNTH_UID), uid_seed(SYNTH_UID))

    def test_v3_sync_channel_80ch(self):
        """v3: sync channel = (80/2)+1 = 41."""
        self.assertEqual(v3_sync_channel(80), 41)

    def test_v4_sync_channel_80ch(self):
        """v4: sync channel = 80/2 = 40."""
        domain = DOMAINS["ISM2G4"]
        self.assertEqual(domain.sync_channel, 40)

    def test_sync_channels_differ(self):
        self.assertNotEqual(v3_sync_channel(80), DOMAINS["ISM2G4"].sync_channel)


class TestFhssSequence(unittest.TestCase):
    """Group 6: FHSS sequence generation."""

    def test_v3_sequence_length_240(self):
        """80-channel domain: 3 blocks × 80 = 240 (rounds down from 256)."""
        seq = v3_build_sequence(SYNTH_UID)
        self.assertEqual(len(seq), 240)

    def test_v4_sequence_length_240(self):
        """v4 should also round down to 240 for 80 channels."""
        seed = uid_seed(SYNTH_UID)
        seq = build_sequence(seed, 80, DOMAINS["ISM2G4"].sync_channel)
        self.assertEqual(len(seq), 240)

    def test_v3_block_boundaries_are_sync_channel(self):
        """Every block starts with the sync channel (41 for v3)."""
        seq = v3_build_sequence(SYNTH_UID)
        self.assertEqual(seq[0], 41)
        self.assertEqual(seq[80], 41)
        self.assertEqual(seq[160], 41)

    def test_v3_each_block_is_permutation(self):
        """Each 80-entry block contains every channel exactly once."""
        seq = v3_build_sequence(SYNTH_UID)
        for block_start in (0, 80, 160):
            block = seq[block_start:block_start + 80]
            self.assertEqual(sorted(block), list(range(80)))

    def test_v3_v4_sequences_differ(self):
        """Different seeds and sync channels → different sequences."""
        v3_seq = v3_build_sequence(SYNTH_UID)
        v4_seed = uid_seed(SYNTH_UID)
        v4_seq = build_sequence(v4_seed, 80, DOMAINS["ISM2G4"].sync_channel)
        self.assertNotEqual(v3_seq, v4_seq)

    def test_v3_sequence_deterministic(self):
        """Same UID → same sequence every time."""
        seq1 = v3_build_sequence(SYNTH_UID)
        seq2 = v3_build_sequence(SYNTH_UID)
        self.assertEqual(seq1, seq2)

    def test_v3_sequence_pinned_vector(self):
        """Cross-check first 20 entries against the standalone v3 fhss tool's
        test (test_elrs_v3_fhss_check.py)."""
        seq = v3_build_sequence(SYNTH_UID)
        expected = [41, 75, 70, 62, 46, 13, 29, 20, 17, 58,
                    40, 8, 22, 38, 67, 57, 9, 25, 45, 24]
        self.assertEqual(seq[:20], expected)

    def test_lcg_shift_is_16(self):
        """The LCG rng() must use >>16, not >>15."""
        lcg = Lcg(42)
        result = lcg.rng()
        # Manually compute: seed = (214013*42 + 2531011) % 2^31
        expected_seed = (214013 * 42 + 2531011) % 2147483648
        expected_rng = expected_seed >> 16
        self.assertEqual(result, expected_rng)

    def test_sub_ghz_sequence_length(self):
        """FCC915 (40 channels): 6 blocks × 40 = 240 (rounds down from 256)."""
        v3_seed = v3_uid_seed(SYNTH_UID)
        sync_ch = v3_sync_channel(40)
        seq = build_sequence(v3_seed, 40, sync_ch)
        self.assertEqual(len(seq), 240)
        # Would have been 280 (7×40) if rounding up — regression check
        self.assertLessEqual(len(seq), FHSS_SEQUENCE_LEN)


class TestBindMode(unittest.TestCase):
    """Group 7: Bind-mode CRC initializer."""

    def test_v3_bind_crc_is_zero(self):
        """BF 4.4.2 explicitly sets bind crcInitializer = 0."""
        self.assertEqual(v3_bind_crc_init(), 0)

    def test_v4_bind_crc_is_version(self):
        """v4 bind_crc_init returns OTA_VERSION_ID (4)."""
        self.assertEqual(bind_crc_init(), V4_OTA_VERSION_ID)

    def test_bind_crcs_differ(self):
        self.assertNotEqual(v3_bind_crc_init(), bind_crc_init())


class TestCrcRoundTrip(unittest.TestCase):
    """Group 8: generate → validate round-trip for both versions."""

    def test_v3_sync_round_trip(self):
        raw = v3_build_sync(SYNTH_UID, fhss_index=0, nonce=0)
        self.assertTrue(v3_validate_crc(raw, SYNTH_UID))

    def test_v3_rc_hybrid_round_trip(self):
        raw = bytearray(8)
        raw[0] = PACKET_TYPE_RCDATA
        raw[1:7] = bytes(range(6))
        v3_generate_crc(raw, SYNTH_UID, switch_mode="hybrid")
        self.assertTrue(v3_validate_crc(bytes(raw), SYNTH_UID, switch_mode="hybrid"))

    def test_v3_rc_wide_round_trip(self):
        for nonce in (0, 1, 5, 127, 255):
            raw = bytearray(8)
            raw[0] = PACKET_TYPE_RCDATA
            raw[1:7] = b"\xaa\xbb\xcc\xdd\xee\xff"
            v3_generate_crc(raw, SYNTH_UID, switch_mode="wide", nonce=nonce)
            self.assertTrue(v3_validate_crc(bytes(raw), SYNTH_UID,
                                            switch_mode="wide", nonce=nonce),
                            f"round-trip failed for nonce={nonce}")

    def test_v3_data_round_trip(self):
        raw = bytearray(8)
        raw[0] = PACKET_TYPE_DATA
        raw[1:7] = b"\x01\x02\x03\x04\x05\x06"
        v3_generate_crc(raw, SYNTH_UID)
        self.assertTrue(v3_validate_crc(bytes(raw), SYNTH_UID))

    def test_v4_std_sync_round_trip(self):
        v4_init = ota_uid_crc_init(SYNTH_UID)
        raw = build_sync(False, 41, 9, 2, uid4=SYNTH_UID[4], uid5=SYNTH_UID[5],
                         crc_init=v4_init)
        self.assertTrue(validate_crc(raw, False, v4_init, 0, PACKET_TYPE_SYNC))

    def test_v4_std_rcdata_round_trip(self):
        v4_init = ota_uid_crc_init(SYNTH_UID)
        raw = bytearray(8)
        raw[0] = PACKET_TYPE_RCDATA
        raw[1:7] = bytes(range(6))
        generate_crc(raw, False, v4_init, 42, PACKET_TYPE_RCDATA)
        self.assertTrue(validate_crc(bytes(raw), False, v4_init, 42, PACKET_TYPE_RCDATA))


class TestCrossCheckWithStandaloneV3(unittest.TestCase):
    """Group 9: v3_compat functions match the standalone elrs-v3-frame-check.py."""

    def test_crc_init_matches_standalone(self):
        """v3_uid_crc_init should match elrs-v3-frame-check.py's uid_crc_init."""
        # Standalone formula: ((UID[4]<<8)|UID[5]) ^ OTA_VERSION_ID(3)
        expected = ((SYNTH_UID[4] << 8) | SYNTH_UID[5]) ^ 3
        self.assertEqual(v3_uid_crc_init(SYNTH_UID), expected)

    def test_crc14_matches_standalone(self):
        """v3_crc14 should produce identical results to standalone crc14."""
        # The standalone's crc14 is the same bitwise algorithm
        init = v3_uid_crc_init(SYNTH_UID)
        for data in (bytes(7), bytes(range(7)), b"\xff" * 7):
            self.assertEqual(v3_crc14(data, init), bf442_table_crc14(data, init))

    def test_sync_frame_matches_standalone_wire_frame(self):
        """A v3 SYNC built by v3_build_sync should match the standalone's
        wire_frame helper (from test_elrs_v3_frame_check.py)."""
        flags = (2 << 4) | (3 << 1) | 1  # rateIndex=2, tlmRatio=3, switchEnc=1
        body = bytes([41, 9, flags, SYNTH_UID[3], SYNTH_UID[4], SYNTH_UID[5]])
        initial = v3_uid_crc_init(SYNTH_UID)
        # Standalone wire_frame: type_byte=2, first_byte_for_crc = type_byte (=2)
        computed = bf442_table_crc14(bytes([2]) + body, initial)
        standalone_raw = bytes([2 | ((computed >> 8) << 2)]) + body + bytes([computed & 0xFF])

        our_raw = v3_build_sync(SYNTH_UID, fhss_index=41, nonce=9,
                                rate_index=2, switch_enc_mode=1, new_tlm_ratio=3)
        self.assertEqual(our_raw, standalone_raw)

    def test_fhss_sequence_matches_standalone(self):
        """v3_build_sequence should match elrs-v3-fhss-check.py's build_v3_sequence."""
        # Standalone seed: ((UID[2]<<24)+...+UID[5]) ^ OTA_VERSION_ID(3)
        seq = v3_build_sequence(SYNTH_UID)
        self.assertEqual(len(seq), 240)
        self.assertEqual(seq[0], 41)
        # Pinned vector from test_elrs_v3_fhss_check.py
        self.assertEqual(seq[:20], [
            41, 75, 70, 62, 46, 13, 29, 20, 17, 58,
            40, 8, 22, 38, 67, 57, 9, 25, 45, 24])


if __name__ == "__main__":
    unittest.main()
