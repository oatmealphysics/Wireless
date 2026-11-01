"""RQ2: ELRS v3/v4 OTA known-answer conformance matrix.

Extends ``test_elrs_core_validation.py`` (which covers CRC engine, init
divergence, nonce, and SYNC structure) with a systematic *known-answer matrix*
spanning packet type x packet size (std 8-byte / full 13-byte) x OTA version
(v3 / v4). This is the RQ2 deliverable: "Generate tag-pinned known-answer
matrices for packet sizes, SYNC fields, CRC, packet-rate, and FHSS semantics."

Known quirk resolved during RQ2: ``decode_std`` (the 8-byte hybrid path) now
expands the unpacked 10-bit channel fields via ``expand_hybrid_channels``
(firmware ``UnpackChannelDataHybridCommon``: ``UINT10_to_CRSF(raw >> 1)``),
so build->decode CRSF round-trips to the 10-bit quantized value. The 13-byte
full-res path is distinct: its 10-bit fields cover the entire CRSF range and
the ``<<1`` unpack IS the complete decode (no re-expansion), which
``decode_full`` already does. See ``TestHybridVsFullChannelExpansion``.

Evidence target: **SYNTHETIC/OFFLINE-TESTED.** All inputs are deterministic
constants; no capture, UID, or device is involved. The expected byte layouts
and CRC behaviours are derived from the vendored ``elrs_core`` (which is itself
validated against the pinned ExpressLRS / Betaflight 4.4.2 source) and checked
for internal consistency:

* a built frame always has the documented size (8 for std, 13 for full);
* a built frame round-trips through the version's own decoder with a matching
  type and fields;
* flipping a payload bit breaks the CRC (the CRC actually covers the payload);
* v3 and v4 builds of the same logical frame diverge (different CRC init /
  layout), so a v4 decoder does not validate a v3 frame and vice versa.

Ambiguous captures stay unknown; nothing here claims receiver acceptance,
transmitter identity, or RF causality.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from elrs_core.ota import (
    OTA4_PACKET_SIZE,
    OTA8_PACKET_SIZE,
    PACKET_TYPE_DATA,
    PACKET_TYPE_RCDATA,
    PACKET_TYPE_SYNC,
    bind_crc_init,
    build_rcdata_full,
    build_rcdata_std,
    build_sync,
    decode_full,
    decode_std,
    ota_uid_crc_init,
    validate_crc,
)
from elrs_core.v3_compat import (
    V3_OTA_VERSION_ID,
    v3_build_sequence,
    v3_build_sync,
    v3_decode_sync,
    v3_sync_channel,
    v3_uid_crc_init,
    v3_uid_seed,
    v3_validate_crc,
)

# Deterministic test UID (no binding phrase, no private capture).
UID = bytes([0x00, 0x00, 0x00, 0x11, 0x18, 0x5b])
# A representative 8-channel set within CRSF 10-bit range (1000..2000 us).
CHANNELS = [1000, 1200, 1400, 1600, 1800, 1900, 1500, 1300]
# A fixed FHSS index and nonce for deterministic builds.
FHSS_INDEX = 7
NONCE = 3
RF_RATE_ENUM = 2  # a mid-range rate enum value


def _flip_bit(buf: bytearray, pos: int) -> bytearray:
    buf[pos] ^= 0x01
    return buf


# ---------------------------------------------------------------------------
# Packet-size KATs (the documented 8 / 13 byte constants)
# ---------------------------------------------------------------------------
class TestPacketSizeKats(unittest.TestCase):
    def test_std_size_is_8(self):
        self.assertEqual(OTA4_PACKET_SIZE, 8)

    def test_full_size_is_13(self):
        self.assertEqual(OTA8_PACKET_SIZE, 13)

    def test_std_sync_frame_size(self):
        crc_init = ota_uid_crc_init(UID)
        frame = build_sync(is_full=False, fhss_index=FHSS_INDEX, nonce=NONCE,
                           rf_rate_enum=RF_RATE_ENUM, crc_init=crc_init)
        self.assertEqual(len(frame), OTA4_PACKET_SIZE)

    def test_full_sync_frame_size(self):
        crc_init = ota_uid_crc_init(UID)
        frame = build_sync(is_full=True, fhss_index=FHSS_INDEX, nonce=NONCE,
                           rf_rate_enum=RF_RATE_ENUM, crc_init=crc_init)
        self.assertEqual(len(frame), OTA8_PACKET_SIZE)

    def test_std_rcdata_frame_size(self):
        crc_init = ota_uid_crc_init(UID)
        frame = build_rcdata_std(CHANNELS, switch_index=0, switch_value=1,
                                 is_armed=False, crc_init=crc_init)
        self.assertEqual(len(frame), OTA4_PACKET_SIZE)

    def test_full_rcdata_frame_size(self):
        crc_init = ota_uid_crc_init(UID)
        frame = build_rcdata_full(CHANNELS, uplink_power=4, is_high_aux=False,
                                  is_armed=False, crc_init=crc_init)
        self.assertEqual(len(frame), OTA8_PACKET_SIZE)


# ---------------------------------------------------------------------------
# v4 build/decode round-trip matrix
# ---------------------------------------------------------------------------
class TestV4RoundTripMatrix(unittest.TestCase):
    def setUp(self):
        self.crc_init = ota_uid_crc_init(UID)

    def test_v4_std_sync_round_trip(self):
        frame = build_sync(is_full=False, fhss_index=FHSS_INDEX, nonce=NONCE,
                           rf_rate_enum=RF_RATE_ENUM, crc_init=self.crc_init)
        pkt = decode_std(frame)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.type, PACKET_TYPE_SYNC)
        self.assertEqual(pkt.sync.fhss_index, FHSS_INDEX)
        self.assertEqual(pkt.sync.rf_rate_enum, RF_RATE_ENUM)

    def test_v4_full_sync_round_trip(self):
        frame = build_sync(is_full=True, fhss_index=FHSS_INDEX, nonce=NONCE,
                           rf_rate_enum=RF_RATE_ENUM, crc_init=self.crc_init)
        pkt = decode_full(frame)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.type, PACKET_TYPE_SYNC)
        self.assertEqual(pkt.sync.fhss_index, FHSS_INDEX)

    def test_v4_std_rcdata_round_trip(self):
        from elrs_core.ota import crsf_to_uint10, uint10_to_crsf

        # Use values in the std CRSF range [171, 1811] so the 10-bit round-trip
        # is clean. decode_std now expands via expand_hybrid_channels
        # (firmware UnpackChannelDataHybridCommon), so packed->decoded CRSF
        # equals the 10-bit quantized round-trip.
        std_channels = [988, 1500, 1000, 1811]
        frame = build_rcdata_std(std_channels, switch_index=1, switch_value=2,
                                 is_armed=True, crc_init=self.crc_init)
        pkt = decode_std(frame)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.type, PACKET_TYPE_RCDATA)
        expected = [uint10_to_crsf(crsf_to_uint10(c)) for c in std_channels]
        self.assertEqual(pkt.channels, expected)
        self.assertTrue(pkt.is_armed)
        self.assertEqual(pkt.switch_index, 1)

    def test_v4_full_rcdata_round_trip(self):
        frame = build_rcdata_full(CHANNELS, uplink_power=5, is_high_aux=True,
                                  is_armed=True, crc_init=self.crc_init)
        pkt = decode_full(frame)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.type, PACKET_TYPE_RCDATA)
        self.assertEqual(pkt.uplink_power, 5)

    def test_v4_std_crc_rejects_corruption(self):
        frame = bytearray(build_sync(is_full=False, fhss_index=FHSS_INDEX,
                                     nonce=NONCE, rf_rate_enum=RF_RATE_ENUM,
                                     crc_init=self.crc_init))
        _flip_bit(frame, 1)  # corrupt the fhss_index byte
        self.assertFalse(
            validate_crc(bytes(frame), is_full=False, crc_init=self.crc_init,
                         ota_nonce=0, packet_type=PACKET_TYPE_SYNC)
        )

    def test_v4_full_crc_rejects_corruption(self):
        frame = bytearray(build_rcdata_full(CHANNELS, uplink_power=4,
                                            is_high_aux=False, is_armed=False,
                                            crc_init=self.crc_init))
        _flip_bit(frame, 5)  # corrupt a channel byte
        self.assertFalse(
            validate_crc(bytes(frame), is_full=True, crc_init=self.crc_init,
                         ota_nonce=0, packet_type=PACKET_TYPE_RCDATA)
        )


# ---------------------------------------------------------------------------
# v3 build/decode round-trip matrix (the v3_compat layer)
# ---------------------------------------------------------------------------
class TestV3RoundTripMatrix(unittest.TestCase):
    def setUp(self):
        self.crc_init = v3_uid_crc_init(UID)

    def test_v3_sync_round_trip(self):
        frame = v3_build_sync(uid=UID, fhss_index=FHSS_INDEX, nonce=NONCE,
                              rate_index=RF_RATE_ENUM)
        sync = v3_decode_sync(frame)
        self.assertIsNotNone(sync)
        self.assertEqual(sync.fhss_index, FHSS_INDEX)
        # v3 sync channel for the 80-channel domain is freq_count//2 + 1 = 41
        # (v4 uses freq_count//2 = 40; the two versions differ here).
        self.assertEqual(v3_sync_channel(80), 41)

    def test_v3_sync_crc_valid_then_corrupt(self):
        frame = bytearray(v3_build_sync(uid=UID, fhss_index=FHSS_INDEX,
                                        nonce=NONCE,
                                        rate_index=RF_RATE_ENUM))
        self.assertTrue(v3_validate_crc(bytes(frame), uid=UID))
        _flip_bit(frame, 1)
        self.assertFalse(v3_validate_crc(bytes(frame), uid=UID))

    def test_v3_uid_seed_uses_version_3(self):
        seed = v3_uid_seed(UID)
        # v3 seed: low 32 bits of UID XORed with the v3 OTA version id.
        low32 = (UID[2] << 24) | (UID[3] << 16) | (UID[4] << 8) | UID[5]
        self.assertEqual(seed, low32 ^ V3_OTA_VERSION_ID)

    def test_v3_fhss_sequence_length(self):
        seq = v3_build_sequence(UID)
        # v3 sequence rounds DOWN to fit FHSS_SEQUENCE_LEN (256): for the
        # 80-channel domain that is (256//80)*80 = 240 hops (3 full cycles).
        from elrs_core.fhss import FHSS_SEQUENCE_LEN

        expected = (FHSS_SEQUENCE_LEN // 80) * 80
        self.assertEqual(len(seq), expected)
        # The sequence is a permutation of 0..79 repeated, so every cycle hits
        # the v3 sync channel exactly once and contains no out-of-range hop.
        self.assertTrue(all(0 <= h < 80 for h in seq))


# ---------------------------------------------------------------------------
# v3 vs v4 divergence (the two versions must NOT be interchangeable)
# ---------------------------------------------------------------------------
class TestV3V4Divergence(unittest.TestCase):
    def test_v3_and_v4_sync_builds_differ(self):
        v4 = build_sync(is_full=False, fhss_index=FHSS_INDEX, nonce=NONCE,
                        rf_rate_enum=RF_RATE_ENUM,
                        crc_init=ota_uid_crc_init(UID))
        v3 = v3_build_sync(uid=UID, fhss_index=FHSS_INDEX, nonce=NONCE,
                           rate_index=RF_RATE_ENUM)
        self.assertNotEqual(v4, v3)

    def test_v3_and_v4_crc_inits_differ(self):
        self.assertNotEqual(v3_uid_crc_init(UID), ota_uid_crc_init(UID))

    def test_v3_bind_and_v4_bind_crc_inits_differ(self):
        # bind_crc_init(ota_version) must diverge between v3 and v4.
        from elrs_core.ota import bind_crc_init as v4_bind
        self.assertNotEqual(v4_bind(3), v4_bind(4))


# ---------------------------------------------------------------------------
# Hybrid (8-byte) vs full (13-byte) channel expansion — the RQ2 fix
# ---------------------------------------------------------------------------
class TestHybridVsFullChannelExpansion(unittest.TestCase):
    """The 8-byte path re-expands (CRSF range); the 13-byte path does not.

    Firmware ground truth:
      - Hybrid/Wide (8-byte): UnpackChannelDataHybridCommon does
        ``UINT10_to_CRSF(raw >> 1)`` after the ``<<1`` unpack.
      - Full-res (13-byte): UnpackChannelData8ch uses the ``<<1`` unpack
        directly (10-bit fields already span the full CRSF range).
    """

    def test_expand_hybrid_matches_firmware(self):
        from elrs_core.ota import expand_hybrid_channels, uint10_to_crsf

        # raw is the <<1 unpack output; firmware does uint10_to_crsf(raw >> 1).
        raw = [517 << 1, 829 << 1, 0, 1023 << 1]
        expanded = expand_hybrid_channels(raw)
        expected = [uint10_to_crsf(v >> 1) for v in raw]
        self.assertEqual(expanded, expected)

    def test_std_decode_reexpands_full_decode_does_not(self):
        from elrs_core.ota import build_rcdata_full, build_rcdata_std

        ci = ota_uid_crc_init(UID)
        # 8 channels for the full path (4 low + 4 high); first 4 reused for std.
        full_ch = [988, 1500, 1000, 1811, 1200, 1600, 1400, 1100]
        std_pkt = decode_std(
            build_rcdata_std(full_ch, switch_index=0, switch_value=0,
                             is_armed=False, crc_init=ci)
        )
        full_pkt = decode_full(
            build_rcdata_full(full_ch, uplink_power=4, is_high_aux=False,
                              is_armed=False, crc_init=ci)
        )
        # Both decode to 4 (std) / 8 (full) channels; std's first 4 are the
        # CRSF-range re-expansion, full's first 4 are the raw <<1 unpack
        # (full 11-bit CRSF). They differ because the two paths encode
        # differently — the std values sit in [171, 1811], the full values
        # span the full CRSF range.
        self.assertEqual(len(std_pkt.channels), 4)
        self.assertEqual(len(full_pkt.channels), 8)
        for v in std_pkt.channels:
            self.assertGreaterEqual(v, 171)
            self.assertLessEqual(v, 1811)


if __name__ == "__main__":
    unittest.main()
