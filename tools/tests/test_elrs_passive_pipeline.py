"""Tests for the passive ELRS capture pipeline and LoRa PHY decode module.

Covers:
  - LoRa encode→decode round-trip (Gray, interleave, FEC, whitening)
  - Hamming FEC error correction
  - ELRS CRC validation with v3 test vectors
  - FHSS correlation logic with synthetic data
  - Report sanitization (no UID leakage in sanitized output)
  - Edge cases and hostile inputs

Python 3.10+, standard library only (no NumPy/SciPy needed for unit tests).
For authorized CTF research only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import lora_decode


class TestGrayCoding(unittest.TestCase):
    """Gray encode/decode round-trip and known values."""

    def test_roundtrip_all_7bit(self):
        for n in range(128):
            g = lora_decode.gray_encode(n)
            self.assertEqual(lora_decode.gray_decode(g), n,
                             f"Gray round-trip failed for {n}")

    def test_known_values(self):
        self.assertEqual(lora_decode.gray_encode(0), 0)
        self.assertEqual(lora_decode.gray_encode(1), 1)
        self.assertEqual(lora_decode.gray_encode(2), 3)
        self.assertEqual(lora_decode.gray_encode(3), 2)
        self.assertEqual(lora_decode.gray_encode(7), 4)

    def test_gray_decode_symbols(self):
        syms = [lora_decode.gray_encode(i) for i in range(10)]
        decoded = lora_decode.gray_decode_symbols(syms, sf=7)
        self.assertEqual(decoded, list(range(10)))

    def test_gray_decode_symbols_mask(self):
        syms = [lora_decode.gray_encode(5) | 0x80]  # set bit above SF=7
        decoded = lora_decode.gray_decode_symbols(syms, sf=7)
        self.assertEqual(decoded[0], 5)


class TestBitHelpers(unittest.TestCase):

    def test_symbol_to_bits(self):
        bits = lora_decode.symbol_to_bits(0b1010101, 7)
        self.assertEqual(bits, [1, 0, 1, 0, 1, 0, 1])

    def test_bits_to_nibble(self):
        self.assertEqual(lora_decode.bits_to_nibble([1, 0, 1, 0]), 0xA)
        self.assertEqual(lora_decode.bits_to_nibble([0, 0, 0, 0]), 0)
        self.assertEqual(lora_decode.bits_to_nibble([1, 1, 1, 1]), 0xF)


class TestHammingFEC(unittest.TestCase):
    """Hamming(7,4) and Hamming(8,4) encode/decode."""

    def test_hamming_7_4_roundtrip(self):
        for nib in range(16):
            cw = lora_decode.hamming_encode_7_4(nib)
            decoded, errors = lora_decode.hamming_decode_7_4(cw)
            self.assertEqual(decoded, nib, f"Hamming(7,4) round-trip failed for {nib}")
            self.assertEqual(errors, 0)

    def test_hamming_8_4_roundtrip(self):
        for nib in range(16):
            cw = lora_decode.hamming_encode_8_4(nib)
            decoded, errors = lora_decode.hamming_decode_8_4(cw)
            self.assertEqual(decoded, nib, f"Hamming(8,4) round-trip failed for {nib}")
            self.assertEqual(errors, 0)

    def test_hamming_7_4_single_error_correction(self):
        for nib in range(16):
            cw = lora_decode.hamming_encode_7_4(nib)
            for bit_pos in range(7):
                corrupted = list(cw)
                corrupted[bit_pos] ^= 1
                decoded, errors = lora_decode.hamming_decode_7_4(corrupted)
                self.assertEqual(decoded, nib,
                                 f"Hamming(7,4) failed to correct bit {bit_pos} for nibble {nib}")
                self.assertEqual(errors, 1)

    def test_hamming_8_4_single_error_correction(self):
        for nib in range(16):
            cw = lora_decode.hamming_encode_8_4(nib)
            for bit_pos in range(8):
                corrupted = list(cw)
                corrupted[bit_pos] ^= 1
                decoded, errors = lora_decode.hamming_decode_8_4(corrupted)
                self.assertEqual(decoded, nib,
                                 f"Hamming(8,4) failed to correct bit {bit_pos} for nibble {nib}")
                self.assertEqual(errors, 1)

    def test_hamming_8_4_double_error_detection(self):
        cw = lora_decode.hamming_encode_8_4(0b1010)
        corrupted = list(cw)
        corrupted[0] ^= 1
        corrupted[1] ^= 1
        _, errors = lora_decode.hamming_decode_8_4(corrupted)
        self.assertEqual(errors, 2)


class TestFECDecode(unittest.TestCase):

    def test_cr3_roundtrip(self):
        for nib in range(16):
            cw = lora_decode.hamming_encode_7_4(nib)
            nibbles, errs = lora_decode.fec_decode([cw], cr=3)
            self.assertEqual(nibbles[0], nib)
            self.assertEqual(errs, 0)

    def test_cr4_roundtrip(self):
        for nib in range(16):
            cw = lora_decode.hamming_encode_8_4(nib)
            nibbles, errs = lora_decode.fec_decode([cw], cr=4)
            self.assertEqual(nibbles[0], nib)
            self.assertEqual(errs, 0)


class TestWhitening(unittest.TestCase):

    def test_dewhiten_roundtrip(self):
        original = [0xA, 0x5, 0x3, 0xC, 0x0, 0xF, 0x7, 0x1]
        whitened = lora_decode.dewhiten_nibbles(original)
        recovered = lora_decode.dewhiten_nibbles(whitened)
        self.assertEqual(recovered, original)

    def test_dewhiten_deterministic(self):
        nibs = [0] * 8
        result1 = lora_decode.dewhiten_nibbles(nibs)
        result2 = lora_decode.dewhiten_nibbles(nibs)
        self.assertEqual(result1, result2)


class TestNibblePacking(unittest.TestCase):

    def test_nibbles_to_bytes(self):
        nibs = [0xA, 0x5, 0x3, 0xC]
        self.assertEqual(lora_decode.nibbles_to_bytes(nibs), bytes([0xA5, 0x3C]))

    def test_nibbles_to_bytes_odd(self):
        nibs = [0xA, 0x5, 0x3]
        self.assertEqual(lora_decode.nibbles_to_bytes(nibs), bytes([0xA5]))


class TestFullEncodeDecodeRoundTrip(unittest.TestCase):
    """End-to-end encode→decode round-trip for the LoRa PHY."""

    def test_roundtrip_long_interleave_cr3(self):
        payload = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0])
        symbols = lora_decode.encode_payload(payload, sf=7, cr=3, long_interleave=True)
        recovered, fec_errors = lora_decode.decode_payload(
            symbols, sf=7, cr=3, payload_len=8, long_interleave=True)
        self.assertEqual(recovered, payload,
                         f"Round-trip failed: {payload.hex()} → {recovered.hex()}")
        self.assertEqual(fec_errors, 0)

    def test_roundtrip_long_interleave_cr4(self):
        payload = bytes([0xAA, 0x55, 0xFF, 0x00, 0x11, 0x22, 0x33, 0x44])
        symbols = lora_decode.encode_payload(payload, sf=7, cr=4, long_interleave=True)
        recovered, fec_errors = lora_decode.decode_payload(
            symbols, sf=7, cr=4, payload_len=8, long_interleave=True)
        self.assertEqual(recovered, payload)
        self.assertEqual(fec_errors, 0)

    def test_roundtrip_various_payloads(self):
        payloads = [
            bytes(8),                        # all zeros
            bytes([0xFF] * 8),               # all ones
            bytes(range(8)),                  # sequential
            bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0xBA, 0xBE]),
        ]
        for payload in payloads:
            with self.subTest(payload=payload.hex()):
                symbols = lora_decode.encode_payload(payload, sf=7, cr=3, long_interleave=True)
                recovered, _ = lora_decode.decode_payload(
                    symbols, sf=7, cr=3, payload_len=8, long_interleave=True)
                self.assertEqual(recovered, payload)

    def test_roundtrip_short_payload(self):
        payload = bytes([0x42, 0x00])
        symbols = lora_decode.encode_payload(payload, sf=7, cr=3, long_interleave=True)
        recovered, _ = lora_decode.decode_payload(
            symbols, sf=7, cr=3, payload_len=2, long_interleave=True)
        self.assertEqual(recovered, payload)


class TestELRSCRCValidation(unittest.TestCase):
    """CRC14 validation using v3 test vectors from elrs_core."""

    def setUp(self):
        from elrs_core.v3_compat import v3_uid_crc_init, v3_generate_crc

        self.uid = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        self.crc_init = v3_uid_crc_init(self.uid)

    def test_crc_init_formula(self):
        from elrs_core.v3_compat import v3_uid_crc_init
        uid = bytes([0x00, 0x00, 0x00, 0x00, 0xAB, 0xCD])
        expected = (0xAB << 8 | 0xCD) ^ 3
        self.assertEqual(v3_uid_crc_init(uid), expected)

    def test_generate_then_validate(self):
        from elrs_core.v3_compat import v3_generate_crc, v3_validate_crc
        pkt = bytearray([0x02, 0x10, 0x20, 0x30, 0x04, 0x05, 0x06, 0x00])
        v3_generate_crc(pkt, self.uid)
        self.assertTrue(v3_validate_crc(pkt, self.uid))

    def test_corrupted_crc_fails(self):
        from elrs_core.v3_compat import v3_generate_crc, v3_validate_crc
        pkt = bytearray([0x02, 0x10, 0x20, 0x30, 0x04, 0x05, 0x06, 0x00])
        v3_generate_crc(pkt, self.uid)
        pkt[7] ^= 0x01  # flip a CRC bit
        self.assertFalse(v3_validate_crc(bytes(pkt), self.uid))


class TestPipelineImports(unittest.TestCase):
    """Verify pipeline module imports and key structures."""

    def test_pipeline_module_loads(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")

        self.assertIn("DecodedFrame", mod)
        self.assertIn("PipelineResult", mod)
        self.assertIn("correlate_fhss", mod)

    def test_pipeline_constants(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        self.assertEqual(mod["SF"], 7)
        self.assertEqual(mod["BW_HZ"], 812_500)
        self.assertEqual(mod["ELRS_PAYLOAD_BYTES"], 8)
        self.assertEqual(mod["ELRS_CR"], 3)
        self.assertTrue(mod["LONG_INTERLEAVE"])


class TestDecodedFrameSanitization(unittest.TestCase):
    """Ensure sanitized output strips all UID material."""

    def _make_frame(self) -> dict:
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        DecodedFrame = mod["DecodedFrame"]

        frame = DecodedFrame(
            timestamp_s=1.234,
            channel_freq_hz=2441378800,
            channel_index=41,
            frame_type="SYNC",
            raw_hex="aabbccdd11223344",
            crc_valid=True,
            fec_errors=0,
            symbols=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 5, 15, 25, 35],
            sync_fields={
                "fhss_index": 7,
                "nonce": 42,
                "uid3": 0x04,
                "uid4": 0x05,
                "uid5": 0x06,
                "uid_hex": "010203040506",
                "rate_index": 2,
            },
            rc_channels=[],
            fhss_position=7,
            confidence="CRC_VALID",
        )
        return frame

    def test_sanitized_strips_raw_hex(self):
        frame = self._make_frame()
        d = frame.to_sanitized_dict()
        self.assertNotIn("raw_hex", d)

    def test_sanitized_strips_symbols(self):
        frame = self._make_frame()
        d = frame.to_sanitized_dict()
        self.assertNotIn("symbols", d)

    def test_sanitized_strips_uid_fields(self):
        frame = self._make_frame()
        d = frame.to_sanitized_dict()
        sf = d.get("sync_fields", {})
        self.assertNotIn("uid3", sf)
        self.assertNotIn("uid4", sf)
        self.assertNotIn("uid5", sf)
        self.assertNotIn("uid_hex", sf)

    def test_sanitized_preserves_non_uid_fields(self):
        frame = self._make_frame()
        d = frame.to_sanitized_dict()
        self.assertEqual(d["timestamp_s"], 1.234)
        self.assertEqual(d["channel_freq_hz"], 2441378800)
        self.assertEqual(d["frame_type"], "SYNC")
        self.assertTrue(d["crc_valid"])
        sf = d.get("sync_fields", {})
        self.assertEqual(sf.get("fhss_index"), 7)
        self.assertEqual(sf.get("nonce"), 42)

    def test_full_dict_includes_uid(self):
        frame = self._make_frame()
        d = frame.to_dict()
        self.assertIn("raw_hex", d)
        self.assertIn("symbols", d)
        sf = d.get("sync_fields", {})
        self.assertIn("uid3", sf)

    def test_sanitized_no_uid_in_json_dump(self):
        frame = self._make_frame()
        sanitized_json = json.dumps(frame.to_sanitized_dict())
        self.assertNotIn("010203040506", sanitized_json)
        self.assertNotIn("aabbccdd11223344", sanitized_json)


class TestFHSSCorrelation(unittest.TestCase):
    """Test FHSS correlation logic with synthetic frames."""

    def setUp(self):
        import runpy
        self.mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        self.DecodedFrame = self.mod["DecodedFrame"]
        self.correlate_fhss = self.mod["correlate_fhss"]

        from elrs_core.v3_compat import v3_build_sequence
        self.uid = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        self.sequence = v3_build_sequence(self.uid, 80)

    def test_perfect_correlation(self):
        hop_interval = 1.0 / 150.0
        frames = []
        for i in range(20):
            ch_idx = self.sequence[i % len(self.sequence)]
            frame = self.DecodedFrame(
                timestamp_s=i * hop_interval,
                channel_freq_hz=2400_400_000 + ch_idx * 1_000_000,
                channel_index=ch_idx,
                frame_type="RCDATA",
                crc_valid=True,
            )
            frames.append(frame)

        result = self.correlate_fhss(frames, self.uid, ota_version=3)
        self.assertEqual(result["status"], "COMPUTED")
        self.assertIn(result["quality"], ("STRONG", "MODERATE"))
        self.assertEqual(result["matches"], 20)

    def test_no_observations(self):
        frames = []
        result = self.correlate_fhss(frames, self.uid, ota_version=3)
        self.assertEqual(result["status"], "NO_OBSERVATIONS")

    def test_no_crc_valid_frames(self):
        frame = self.DecodedFrame(
            timestamp_s=0.0,
            channel_index=41,
            crc_valid=False,
        )
        result = self.correlate_fhss([frame], self.uid, ota_version=3)
        self.assertEqual(result["status"], "NO_OBSERVATIONS")

    def test_random_channels_weak_correlation(self):
        import random
        rng = random.Random(42)
        hop_interval = 1.0 / 150.0
        frames = []
        for i in range(30):
            frame = self.DecodedFrame(
                timestamp_s=i * hop_interval,
                channel_index=rng.randint(0, 79),
                crc_valid=True,
            )
            frames.append(frame)

        result = self.correlate_fhss(frames, self.uid, ota_version=3)
        self.assertEqual(result["status"], "COMPUTED")
        # Random channels shouldn't produce strong correlation
        self.assertIn(result["quality"], ("NONE", "WEAK"))


class TestPipelineResult(unittest.TestCase):
    """Test PipelineResult summary generation."""

    def test_summary_no_uid(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        PipelineResult = mod["PipelineResult"]

        result = PipelineResult(
            capture_file="test.iq",
            capture_sha256="abcdef" * 10,
            sample_rate=4_000_000,
            center_freq_hz=2441_000_000,
        )
        summary = result.summary()
        summary_json = json.dumps(summary)
        self.assertNotIn("uid", summary_json.lower())
        self.assertEqual(summary["capture_file"], "test.iq")
        self.assertEqual(summary["sample_rate"], 4_000_000)

    def test_sha256_truncated_in_summary(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        PipelineResult = mod["PipelineResult"]

        full_hash = "a" * 64
        result = PipelineResult(capture_sha256=full_hash)
        summary = result.summary()
        self.assertTrue(summary["capture_sha256"].endswith("..."))
        self.assertNotEqual(summary["capture_sha256"], full_hash)


class TestELRSFrameDecode(unittest.TestCase):
    """Test ELRS frame field decoding."""

    def setUp(self):
        import runpy
        self.mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        self.decode_elrs_frame = self.mod["decode_elrs_frame"]

    def test_sync_frame_type(self):
        payload = bytearray(8)
        payload[0] = 0x02  # SYNC type
        result = self.decode_elrs_frame(bytes(payload), None, ota_version=3)
        self.assertEqual(result["type"], "SYNC")

    def test_rcdata_frame_type(self):
        payload = bytearray(8)
        payload[0] = 0x00  # RCDATA type
        result = self.decode_elrs_frame(bytes(payload), None, ota_version=3)
        self.assertEqual(result["type"], "RCDATA")

    def test_data_frame_type(self):
        payload = bytearray(8)
        payload[0] = 0x01  # DATA type
        result = self.decode_elrs_frame(bytes(payload), None, ota_version=3)
        self.assertEqual(result["type"], "DATA")

    def test_short_payload_error(self):
        result = self.decode_elrs_frame(bytes(3), None, ota_version=3)
        self.assertIn("error", result)


class TestCRCValidation(unittest.TestCase):
    """Test try_crc_validation wrapper."""

    def setUp(self):
        import runpy
        self.mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        self.try_crc_validation = self.mod["try_crc_validation"]

    def test_no_uid_returns_false(self):
        payload = bytes(8)
        self.assertFalse(self.try_crc_validation(payload, uid=None, ota_version=3))

    def test_wrong_length_returns_false(self):
        uid = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        self.assertFalse(self.try_crc_validation(bytes(5), uid, ota_version=3))

    def test_valid_v3_packet(self):
        from elrs_core.v3_compat import v3_generate_crc
        uid = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        pkt = bytearray([0x02, 0x10, 0x20, 0x30, 0x04, 0x05, 0x06, 0x00])
        v3_generate_crc(pkt, uid)
        self.assertTrue(self.try_crc_validation(bytes(pkt), uid, ota_version=3))


class TestEdgeCases(unittest.TestCase):
    """Edge cases and hostile inputs."""

    def test_decode_empty_symbols(self):
        payload, errors = lora_decode.decode_payload([], sf=7, cr=3, payload_len=8)
        self.assertEqual(len(payload), 0)

    def test_decode_insufficient_symbols(self):
        payload, errors = lora_decode.decode_payload([0, 1, 2], sf=7, cr=3, payload_len=8)
        self.assertLessEqual(len(payload), 8)

    def test_gray_decode_zero(self):
        self.assertEqual(lora_decode.gray_decode(0), 0)

    def test_gray_encode_large(self):
        g = lora_decode.gray_encode(255)
        self.assertEqual(lora_decode.gray_decode(g), 255)

    def test_dewhiten_empty(self):
        self.assertEqual(lora_decode.dewhiten_nibbles([]), [])

    def test_nibbles_to_bytes_empty(self):
        self.assertEqual(lora_decode.nibbles_to_bytes([]), b"")

    def test_fec_decode_empty(self):
        nibbles, errors = lora_decode.fec_decode([], cr=3)
        self.assertEqual(nibbles, [])
        self.assertEqual(errors, 0)


class TestLoadFramesJSONL(unittest.TestCase):
    """Test JSONL loading for offline correlation."""

    def test_load_valid_jsonl(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        load_frames_jsonl = mod["load_frames_jsonl"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({
                "timestamp_s": 1.0,
                "channel_freq_hz": 2441378800,
                "channel_index": 41,
                "frame_type": "SYNC",
                "crc_valid": True,
            }) + "\n")
            f.write(json.dumps({
                "timestamp_s": 2.0,
                "channel_index": 42,
                "crc_valid": False,
            }) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            frames = load_frames_jsonl(path)
            self.assertEqual(len(frames), 2)
            self.assertEqual(frames[0].timestamp_s, 1.0)
            self.assertEqual(frames[0].channel_index, 41)
            self.assertTrue(frames[0].crc_valid)
            self.assertFalse(frames[1].crc_valid)
        finally:
            path.unlink()

    def test_load_empty_jsonl(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        load_frames_jsonl = mod["load_frames_jsonl"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n\n")
            f.flush()
            path = Path(f.name)

        try:
            frames = load_frames_jsonl(path)
            self.assertEqual(len(frames), 0)
        finally:
            path.unlink()


class TestParseUID(unittest.TestCase):

    def test_valid_hex(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        parse_uid = mod["parse_uid"]
        self.assertEqual(parse_uid("010203040506"), bytes([1, 2, 3, 4, 5, 6]))

    def test_colon_separated(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        parse_uid = mod["parse_uid"]
        self.assertEqual(parse_uid("01:02:03:04:05:06"), bytes([1, 2, 3, 4, 5, 6]))

    def test_wrong_length_raises(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        parse_uid = mod["parse_uid"]
        with self.assertRaises(ValueError):
            parse_uid("0102030405")


if __name__ == "__main__":
    unittest.main()
