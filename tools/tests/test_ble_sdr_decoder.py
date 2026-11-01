#!/usr/bin/env python3
"""
Tests for ble_sdr_decoder — internal math / logic verification.

These test the BLE spec math (CRC-24, whitening, PDU parsing, bit packing,
AD structure parsing, ESP32 fingerprinting) using known BLE spec vectors.

END-TO-END DECODING FROM REAL IQ CAPTURES IS NOT TESTED HERE.
That requires a real SDR capture of BLE advertising — flagged as a gap.
To fill it: capture BLE advertising with any SDR at 4 Msps on channel 37
(see ble_sdr_decoder.py --capture-help) and add the .raw/.cf32 file to
captures/ for a live integration test.
"""

import struct
import sys
import os
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from ble_sdr_decoder import (
    ble_crc24,
    ble_whitening_seq,
    ble_dewhiten,
    bits_to_bytes,
    parse_ad_structures,
    parse_advertising_pdu,
    format_mac,
    _fingerprint_esp32,
    BLEAdvPacket,
    BLE_CRC_INIT_ADV,
    AD_TYPE_COMPLETE_NAME,
    AD_TYPE_FLAGS,
    AD_TYPE_COMPLETE_16,
    AD_TYPE_MFG_SPECIFIC,
    AD_TYPE_TX_POWER,
)

if HAS_NUMPY:
    from ble_sdr_decoder import (
        fm_demodulate,
        find_access_addresses,
        recover_bits,
        load_iq_file,
    )


# ---------------------------------------------------------------------------
# CRC-24 tests
# ---------------------------------------------------------------------------

class TestCRC24(unittest.TestCase):
    """Verify BLE CRC-24 against known values from the spec."""

    def test_empty_input(self):
        crc = ble_crc24(b"", BLE_CRC_INIT_ADV)
        self.assertEqual(crc, BLE_CRC_INIT_ADV)

    def test_single_zero_byte(self):
        crc = ble_crc24(b"\x00")
        self.assertIsInstance(crc, int)
        self.assertGreaterEqual(crc, 0)
        self.assertLessEqual(crc, 0xFFFFFF)

    def test_deterministic(self):
        data = b"\x40\x06\xaa\xbb\xcc\xdd\xee\xff"
        crc1 = ble_crc24(data)
        crc2 = ble_crc24(data)
        self.assertEqual(crc1, crc2)

    def test_different_data_different_crc(self):
        crc1 = ble_crc24(b"\x00\x01\x02\x03")
        crc2 = ble_crc24(b"\x00\x01\x02\x04")
        self.assertNotEqual(crc1, crc2)

    def test_crc_range(self):
        for data in [b"\x00", b"\xff", b"\xaa\x55", b"\x01\x02\x03\x04\x05\x06"]:
            crc = ble_crc24(data)
            self.assertGreaterEqual(crc, 0)
            self.assertLessEqual(crc, 0xFFFFFF)


# ---------------------------------------------------------------------------
# Data whitening tests
# ---------------------------------------------------------------------------

class TestWhitening(unittest.TestCase):
    """Verify BLE data whitening / de-whitening."""

    def test_whitening_roundtrip(self):
        """Whitening applied twice should return original data."""
        for ch in [37, 38, 39, 0, 17]:
            original = bytes(range(42))
            whitened = ble_dewhiten(original, ch)
            restored = ble_dewhiten(whitened, ch)
            self.assertEqual(restored, original, f"Roundtrip failed for channel {ch}")

    def test_whitening_different_channels(self):
        """Different channels must produce different whitening sequences."""
        data = b"\x00" * 20
        w37 = ble_dewhiten(data, 37)
        w38 = ble_dewhiten(data, 38)
        w39 = ble_dewhiten(data, 39)
        self.assertNotEqual(w37, w38)
        self.assertNotEqual(w38, w39)
        self.assertNotEqual(w37, w39)

    def test_whitening_seq_length(self):
        for length in [0, 1, 10, 42, 100]:
            seq = ble_whitening_seq(37, length)
            self.assertEqual(len(seq), length)

    def test_whitening_seq_deterministic(self):
        s1 = ble_whitening_seq(37, 42)
        s2 = ble_whitening_seq(37, 42)
        self.assertEqual(s1, s2)

    def test_channel_37_lfsr_init(self):
        """Channel 37 = 0b100101, LFSR init should be (37 & 0x3F) << 1 | 1 = 0b1001011."""
        seq = ble_whitening_seq(37, 1)
        self.assertEqual(len(seq), 1)
        self.assertNotEqual(seq[0], 0)


# ---------------------------------------------------------------------------
# Bit packing tests
# ---------------------------------------------------------------------------

class TestBitPacking(unittest.TestCase):
    """Verify bit-to-byte conversion in BLE LSBit-first order."""

    def test_all_zeros(self):
        bits = [0] * 8
        self.assertEqual(bits_to_bytes(bits), b"\x00")

    def test_all_ones(self):
        bits = [1] * 8
        self.assertEqual(bits_to_bytes(bits), b"\xff")

    def test_lsb_first(self):
        bits = [1, 0, 0, 0, 0, 0, 0, 0]
        self.assertEqual(bits_to_bytes(bits), b"\x01")

    def test_alternating(self):
        bits = [0, 1, 0, 1, 0, 1, 0, 1]
        self.assertEqual(bits_to_bytes(bits), b"\xaa")

    def test_multi_byte(self):
        bits = [1, 0, 0, 0, 0, 0, 0, 0,  # 0x01
                0, 0, 0, 0, 0, 0, 0, 1]  # 0x80
        self.assertEqual(bits_to_bytes(bits), b"\x01\x80")


# ---------------------------------------------------------------------------
# MAC formatting
# ---------------------------------------------------------------------------

class TestFormatMac(unittest.TestCase):
    def test_format(self):
        addr = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        self.assertEqual(format_mac(addr), "06:05:04:03:02:01")

    def test_espressif_oui(self):
        addr = bytes([0x24, 0x0A, 0xC4, 0x11, 0x22, 0x33])
        self.assertEqual(format_mac(addr), "33:22:11:C4:0A:24")


# ---------------------------------------------------------------------------
# AD structure parsing
# ---------------------------------------------------------------------------

class TestADParsing(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_ad_structures(b""), [])

    def test_flags_only(self):
        data = bytes([0x02, 0x01, 0x06])
        result = parse_ad_structures(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], AD_TYPE_FLAGS)
        self.assertEqual(result[0]["data"], bytes([0x06]))

    def test_name(self):
        name = "TestDev"
        name_bytes = name.encode("utf-8")
        data = bytes([len(name_bytes) + 1, AD_TYPE_COMPLETE_NAME]) + name_bytes
        result = parse_ad_structures(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], AD_TYPE_COMPLETE_NAME)
        self.assertEqual(result[0]["data"], name_bytes)

    def test_multiple_structures(self):
        flags = bytes([0x02, 0x01, 0x06])
        name = b"ESP32"
        name_ad = bytes([len(name) + 1, AD_TYPE_COMPLETE_NAME]) + name
        data = flags + name_ad
        result = parse_ad_structures(data)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["type"], AD_TYPE_FLAGS)
        self.assertEqual(result[1]["type"], AD_TYPE_COMPLETE_NAME)

    def test_manufacturer_data(self):
        mfg_data = bytes([0x05, AD_TYPE_MFG_SPECIFIC, 0xE5, 0x02, 0xAA, 0xBB])
        result = parse_ad_structures(mfg_data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], AD_TYPE_MFG_SPECIFIC)
        self.assertEqual(result[0]["data"][0], 0xE5)
        self.assertEqual(result[0]["data"][1], 0x02)

    def test_truncated_gracefully(self):
        data = bytes([0x0A, 0x01, 0x06])
        self.assertEqual(parse_ad_structures(data), [])

    def test_zero_length_terminates(self):
        data = bytes([0x02, 0x01, 0x06, 0x00])
        self.assertEqual(len(parse_ad_structures(data)), 1)


# ---------------------------------------------------------------------------
# PDU parsing (using pre-constructed whitened bytes)
# ---------------------------------------------------------------------------

class TestPDUParsing(unittest.TestCase):
    def _make_raw_pdu(self, pdu_type, tx_add, length, payload, channel):
        """Build a raw PDU + CRC, then whiten it — simulates what the decoder receives."""
        header0 = (pdu_type & 0x0F) | ((tx_add & 1) << 6)
        header1 = length & 0x3F
        pdu_bytes = bytes([header0, header1]) + payload
        crc = ble_crc24(pdu_bytes)
        crc_bytes = bytes([crc & 0xFF, (crc >> 8) & 0xFF, (crc >> 16) & 0xFF])
        raw = pdu_bytes + crc_bytes
        whitened = ble_dewhiten(raw, channel)
        return whitened

    def test_adv_ind_parse(self):
        """Construct an ADV_IND and verify parsing."""
        channel = 37
        adv_a = bytes([0x24, 0x0A, 0xC4, 0x11, 0x22, 0x33])
        name = b"ESP32"
        ad_data = bytes([0x02, 0x01, 0x06,
                         len(name) + 1, AD_TYPE_COMPLETE_NAME]) + name
        payload = adv_a + ad_data
        length = len(payload)

        whitened = self._make_raw_pdu(0, 0, length, payload, channel)
        dewhitened = ble_dewhiten(whitened, channel)
        pkt = parse_advertising_pdu(dewhitened, channel)

        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.pdu_type, 0)
        self.assertEqual(pkt.pdu_type_name, "ADV_IND")
        self.assertEqual(pkt.length, length)
        self.assertTrue(pkt.crc_ok)
        self.assertEqual(pkt.adv_addr, format_mac(adv_a))
        self.assertEqual(pkt.device_name, "ESP32")
        self.assertTrue(pkt.is_espressif)
        self.assertTrue(any("Espressif OUI" in ind for ind in pkt.esp_indicators))

    def test_adv_nonconn_ind(self):
        channel = 38
        adv_a = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
        payload = adv_a
        whitened = self._make_raw_pdu(2, 1, len(payload), payload, channel)
        dewhitened = ble_dewhiten(whitened, channel)
        pkt = parse_advertising_pdu(dewhitened, channel)

        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.pdu_type, 2)
        self.assertEqual(pkt.pdu_type_name, "ADV_NONCONN_IND")
        self.assertEqual(pkt.tx_add, 1)
        self.assertTrue(pkt.crc_ok)

    def test_scan_rsp_with_mfg_data(self):
        channel = 39
        adv_a = bytes([0x30, 0xAE, 0xA4, 0x01, 0x02, 0x03])
        mfg = bytes([0x05, AD_TYPE_MFG_SPECIFIC, 0xE5, 0x02, 0xDE, 0xAD])
        payload = adv_a + mfg
        whitened = self._make_raw_pdu(4, 0, len(payload), payload, channel)
        dewhitened = ble_dewhiten(whitened, channel)
        pkt = parse_advertising_pdu(dewhitened, channel)

        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.pdu_type, 4)
        self.assertEqual(pkt.pdu_type_name, "SCAN_RSP")
        self.assertTrue(pkt.crc_ok)
        self.assertEqual(pkt.manufacturer_id, 0x02E5)
        self.assertTrue(pkt.is_espressif)

    def test_bad_crc_detected(self):
        channel = 37
        adv_a = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        payload = adv_a
        header0 = 0x00
        header1 = len(payload) & 0x3F
        pdu_bytes = bytes([header0, header1]) + payload
        bad_crc = bytes([0x00, 0x00, 0x00])
        raw = pdu_bytes + bad_crc
        pkt = parse_advertising_pdu(raw, channel)

        self.assertIsNotNone(pkt)
        self.assertFalse(pkt.crc_ok)

    def test_too_short_returns_none(self):
        self.assertIsNone(parse_advertising_pdu(b"\x00\x00", 37))
        self.assertIsNone(parse_advertising_pdu(b"", 37))

    def test_oversized_length_returns_none(self):
        raw = bytes([0x00, 50]) + b"\x00" * 56
        self.assertIsNone(parse_advertising_pdu(raw, 37))


# ---------------------------------------------------------------------------
# ESP32 fingerprinting
# ---------------------------------------------------------------------------

class TestESP32Fingerprint(unittest.TestCase):
    def test_espressif_oui_detected(self):
        pkt = BLEAdvPacket()
        pkt.adv_addr_bytes = bytes([0x24, 0x0A, 0xC4, 0x11, 0x22, 0x33])
        _fingerprint_esp32(pkt)
        self.assertTrue(pkt.is_espressif)
        self.assertTrue(any("OUI" in i for i in pkt.esp_indicators))

    def test_espressif_mfg_id_detected(self):
        pkt = BLEAdvPacket()
        pkt.adv_addr_bytes = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        pkt.manufacturer_id = 0x02E5
        pkt.manufacturer_data = b"\xDE\xAD"
        _fingerprint_esp32(pkt)
        self.assertTrue(pkt.is_espressif)

    def test_esp_name_patterns(self):
        for name in ["ESP32_BLE", "PROV_test", "BLE_SPP_server", "ELRS_Backpack"]:
            pkt = BLEAdvPacket()
            pkt.adv_addr_bytes = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
            pkt.device_name = name
            _fingerprint_esp32(pkt)
            self.assertTrue(pkt.is_espressif, f"Failed for name: {name}")

    def test_non_esp_not_flagged(self):
        pkt = BLEAdvPacket()
        pkt.adv_addr_bytes = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        pkt.device_name = "MyFitnessBand"
        pkt.manufacturer_id = 0x0000
        _fingerprint_esp32(pkt)
        self.assertFalse(pkt.is_espressif)


# ---------------------------------------------------------------------------
# FM demodulation / signal processing (math checks only — requires numpy)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_NUMPY, "numpy not available")
class TestSignalProcessing(unittest.TestCase):
    def test_fm_demod_constant_freq(self):
        """A constant-frequency signal should demodulate to a constant."""
        sr = 4e6
        t = np.arange(1000) / sr
        freq_offset = 250e3
        iq = np.exp(2j * np.pi * freq_offset * t).astype(np.complex64)
        fm = fm_demodulate(iq)
        self.assertLess(np.std(fm), 0.01)
        self.assertGreater(np.mean(fm), 0)

    def test_fm_demod_length(self):
        iq = np.ones(100, dtype=np.complex64)
        fm = fm_demodulate(iq)
        self.assertEqual(len(fm), 99)

    def test_recover_bits_length(self):
        """Bit recovery should produce roughly signal_length / samples_per_bit bits."""
        fm = np.random.randn(4000).astype(np.float32)
        spb = 4.0
        bits = recover_bits(fm, spb)
        expected = int(4000 / spb)
        self.assertLess(abs(len(bits) - expected), expected * 0.2)


# ---------------------------------------------------------------------------
# IQ file loading (format handling — requires numpy)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_NUMPY, "numpy not available")
class TestIQLoading(unittest.TestCase):
    def test_load_npy(self):
        samples = (np.random.randn(1000) + 1j * np.random.randn(1000)).astype(np.complex64)
        with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as f:
            path = f.name
        try:
            np.save(path, samples)
            loaded, sr = load_iq_file(path)
            self.assertEqual(len(loaded), len(samples))
            np.testing.assert_array_almost_equal(loaded, samples)
        finally:
            os.unlink(path)

    def test_load_cf32(self):
        samples = (np.random.randn(1000) + 1j * np.random.randn(1000)).astype(np.complex64)
        with tempfile.NamedTemporaryFile(suffix=".cf32", delete=False) as f:
            path = f.name
        try:
            samples.tofile(path)
            loaded, sr = load_iq_file(path)
            self.assertEqual(len(loaded), len(samples))
        finally:
            os.unlink(path)

    def test_load_int8(self):
        n = 500
        i_vals = np.random.randint(-128, 127, n, dtype=np.int8)
        q_vals = np.random.randint(-128, 127, n, dtype=np.int8)
        interleaved = np.empty(2 * n, dtype=np.int8)
        interleaved[0::2] = i_vals
        interleaved[1::2] = q_vals
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
            path = f.name
        try:
            interleaved.tofile(path)
            loaded, sr = load_iq_file(path)
            self.assertEqual(len(loaded), n)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Packet summary / serialization
# ---------------------------------------------------------------------------

class TestPacketOutput(unittest.TestCase):
    def test_summary_format(self):
        pkt = BLEAdvPacket(
            channel=37,
            pdu_type=0,
            pdu_type_name="ADV_IND",
            tx_add=0,
            length=12,
            adv_addr="33:22:11:C4:0A:24",
            device_name="ESP32_Test",
            is_espressif=True,
            esp_indicators=["Espressif OUI"],
            crc_ok=True,
        )
        s = pkt.summary()
        self.assertIn("ADV_IND", s)
        self.assertIn("ESP32", s)
        self.assertIn("33:22:11:C4:0A:24", s)

    def test_to_dict(self):
        pkt = BLEAdvPacket(channel=37, pdu_type=0, pdu_type_name="ADV_IND")
        d = pkt.to_dict()
        self.assertEqual(d["channel"], 37)
        self.assertEqual(d["pdu_type_name"], "ADV_IND")
        self.assertIn("is_espressif", d)


if __name__ == "__main__":
    unittest.main()
