"""
Unit tests for the ELRS crypto derivation chain.

Tests the full chain: binding phrase → MD5 → UID → FHSS seed → CRC init,
for both the current OTA version and historical versions.
"""

import hashlib
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
elrs_rainbow = importlib.import_module("elrs-uid-rainbow")


class TestPhraseToUID:
    """Binding phrase → MD5 hash → first 6 bytes = UID."""

    def test_known_phrase_matches_manual_md5(self):
        phrase = "expresslrs"
        raw = f'-DMY_BINDING_PHRASE="{phrase}"'
        expected = hashlib.md5(raw.encode()).digest()[:6]
        assert elrs_rainbow.phrase_to_uid(phrase) == expected

    def test_empty_phrase(self):
        uid = elrs_rainbow.phrase_to_uid("")
        raw = '-DMY_BINDING_PHRASE=""'
        expected = hashlib.md5(raw.encode()).digest()[:6]
        assert uid == expected
        assert len(uid) == 6

    def test_uid_is_6_bytes(self):
        for phrase in ["test", "", "a very long binding phrase with spaces"]:
            assert len(elrs_rainbow.phrase_to_uid(phrase)) == 6

    def test_different_phrases_produce_different_uids(self):
        uid1 = elrs_rainbow.phrase_to_uid("alpha")
        uid2 = elrs_rainbow.phrase_to_uid("bravo")
        assert uid1 != uid2

    def test_case_sensitive(self):
        assert elrs_rainbow.phrase_to_uid("ELRS") != elrs_rainbow.phrase_to_uid("elrs")

    def test_whitespace_significant(self):
        assert elrs_rainbow.phrase_to_uid("test") != elrs_rainbow.phrase_to_uid("test ")
        assert elrs_rainbow.phrase_to_uid("test") != elrs_rainbow.phrase_to_uid(" test")

    def test_deterministic(self):
        uid1 = elrs_rainbow.phrase_to_uid("deterministic")
        uid2 = elrs_rainbow.phrase_to_uid("deterministic")
        assert uid1 == uid2

    def test_special_characters(self):
        uid = elrs_rainbow.phrase_to_uid('quotes"and\\backslash')
        assert len(uid) == 6

    def test_unicode_phrase(self):
        uid = elrs_rainbow.phrase_to_uid("café☕")
        assert len(uid) == 6


class TestUIDToFHSSSeed:
    """UID → FHSS seed: (UID[2]<<24) | (UID[3]<<16) | (UID[4]<<8) | (UID[5] ^ OTA_VERSION_ID)."""

    def test_manual_computation(self):
        uid = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        expected = (0x03 << 24) | (0x04 << 16) | (0x05 << 8) | (0x06 ^ 4)
        assert elrs_rainbow.uid_to_fhss_seed(uid) == expected

    def test_zero_uid(self):
        uid = bytes([0, 0, 0, 0, 0, 0])
        assert elrs_rainbow.uid_to_fhss_seed(uid) == (0 ^ elrs_rainbow.DEFAULT_OTA_VERSION)

    def test_all_ff(self):
        uid = bytes([0xFF] * 6)
        expected = (0xFF << 24) | (0xFF << 16) | (0xFF << 8) | (0xFF ^ 4)
        assert elrs_rainbow.uid_to_fhss_seed(uid) == expected

    def test_ota_version_xor(self):
        uid = bytes([0, 0, 0, 0, 0, 0x04])
        seed = elrs_rainbow.uid_to_fhss_seed(uid)
        assert seed == 0  # 0x04 ^ 0x04 = 0

    def test_seed_is_32_bit(self):
        uid = bytes([0xFF] * 6)
        seed = elrs_rainbow.uid_to_fhss_seed(uid)
        assert 0 <= seed <= 0xFFFFFFFF

    def test_only_uid_2_through_5_matter(self):
        uid_a = bytes([0xAA, 0xBB, 0x03, 0x04, 0x05, 0x06])
        uid_b = bytes([0xCC, 0xDD, 0x03, 0x04, 0x05, 0x06])
        assert elrs_rainbow.uid_to_fhss_seed(uid_a) == elrs_rainbow.uid_to_fhss_seed(uid_b)


class TestUIDToCRCInit:
    """UID → CRC init: ((UID[4]<<8) | UID[5]) ^ (OTA_VERSION_ID << 8)."""

    def test_manual_computation(self):
        uid = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        expected = ((0x05 << 8) | 0x06) ^ (4 << 8)
        assert elrs_rainbow.uid_to_crc_init(uid) == expected

    def test_zero_uid(self):
        uid = bytes([0, 0, 0, 0, 0, 0])
        expected = (4 << 8)  # 0 ^ 0x0400
        assert elrs_rainbow.uid_to_crc_init(uid) == expected

    def test_crc_init_is_16_bit(self):
        uid = bytes([0xFF] * 6)
        crc = elrs_rainbow.uid_to_crc_init(uid)
        assert 0 <= crc <= 0xFFFF

    def test_only_uid_4_and_5_matter(self):
        uid_a = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0x05, 0x06])
        uid_b = bytes([0x11, 0x22, 0x33, 0x44, 0x05, 0x06])
        assert elrs_rainbow.uid_to_crc_init(uid_a) == elrs_rainbow.uid_to_crc_init(uid_b)


class TestEndToEndDerivation:
    """Full chain: phrase → UID → FHSS seed + CRC init."""

    def test_known_phrase_roundtrip(self):
        phrase = "default ExpressLRS binding phrase"
        uid = elrs_rainbow.phrase_to_uid(phrase)
        seed = elrs_rainbow.uid_to_fhss_seed(uid)
        crc = elrs_rainbow.uid_to_crc_init(uid)

        raw = f'-DMY_BINDING_PHRASE="{phrase}"'
        md5_bytes = hashlib.md5(raw.encode()).digest()[:6]

        assert uid == md5_bytes
        expected_seed = (md5_bytes[2] << 24) | (md5_bytes[3] << 16) | (md5_bytes[4] << 8) | (md5_bytes[5] ^ 4)
        assert seed == expected_seed
        expected_crc = ((md5_bytes[4] << 8) | md5_bytes[5]) ^ (4 << 8)
        assert crc == expected_crc

    def test_sync_bytes_are_uid_3_to_5(self):
        uid = elrs_rainbow.phrase_to_uid("test")
        sync = elrs_rainbow.sync_bytes_hex(uid)
        parts = sync.split(":")
        assert len(parts) == 3
        assert int(parts[0], 16) == uid[3]
        assert int(parts[1], 16) == uid[4]
        assert int(parts[2], 16) == uid[5]


class TestOTAVersionVariations:
    """Verify seed/CRC change with different OTA version IDs."""

    def test_v3_vs_v4_fhss_seed_differs(self):
        uid = elrs_rainbow.phrase_to_uid("test")
        seed_v4 = elrs_rainbow.uid_to_fhss_seed(uid)
        # Manually compute with OTA version 3
        seed_v3 = (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ 3)
        assert seed_v4 != seed_v3 or uid[5] in (3 ^ 4,)  # would only match on specific UID[5] values

    def test_v3_vs_v4_crc_init_differs(self):
        uid = elrs_rainbow.phrase_to_uid("test")
        crc_v4 = elrs_rainbow.uid_to_crc_init(uid)
        crc_v3 = ((uid[4] << 8) | uid[5]) ^ (3 << 8)
        assert crc_v4 != crc_v3  # different OTA versions always differ (bits 8-9 change)
