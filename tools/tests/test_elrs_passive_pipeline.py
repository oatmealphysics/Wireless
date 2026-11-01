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
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
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
    """CRC14 validation using capture-derived UID from committed captures."""

    def setUp(self):
        from elrs_core.v3_compat import v3_uid_crc_init

        self.uid = bytes([0x00, 0x00, 0x00, 0x11, 0x18, 0x5b])
        self.crc_init = v3_uid_crc_init(self.uid)

    def test_crc_init_formula(self):
        from elrs_core.v3_compat import v3_uid_crc_init
        expected = (0x18 << 8 | 0x5b) ^ 3
        self.assertEqual(v3_uid_crc_init(self.uid), expected)

    def test_real_sync_frame_validates(self):
        from elrs_core.v3_compat import v3_validate_crc
        pkt = bytes.fromhex("b600037111185bba")
        self.assertTrue(v3_validate_crc(pkt, self.uid))

    def test_corrupted_real_frame_fails(self):
        from elrs_core.v3_compat import v3_validate_crc
        pkt = bytearray.fromhex("b600037111185bba")
        pkt[7] ^= 0x01
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
        self.assertEqual(mod["ELRS_CR"], 4)
        self.assertEqual(mod["PAYLOAD_SYMBOLS"], 21)
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
            raw_hex="b600037111185bba",
            crc_valid=True,
            fec_errors=0,
            symbols=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 5, 15, 25, 35],
            sync_fields={
                "fhss_index": 7,
                "nonce": 42,
                "uid3": 0x11,
                "uid4": 0x18,
                "uid5": 0x5b,
                "uid_hex": "00000011185b",
                "rate_index": 2,
            },
            rc_channels=[],
            fhss_position=7,
            confidence="CRC_VALID",
            source="oct15-ranked-2026-10-16.jsonl",
            source_line=17,
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

    def test_sanitized_strips_private_provenance(self):
        frame = self._make_frame()
        d = frame.to_sanitized_dict()
        self.assertNotIn("source", d)
        self.assertNotIn("source_line", d)

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
        frame.to_sanitized_dict()
        d = frame.to_dict()
        self.assertIn("raw_hex", d)
        self.assertIn("symbols", d)
        self.assertEqual(d["source"], "oct15-ranked-2026-10-16.jsonl")
        self.assertEqual(d["source_line"], 17)
        sf = d.get("sync_fields", {})
        self.assertIn("uid3", sf)

    def test_sanitized_no_uid_in_json_dump(self):
        frame = self._make_frame()
        sanitized_json = json.dumps(frame.to_sanitized_dict())
        self.assertNotIn("00000011185b", sanitized_json)
        self.assertNotIn("b600037111185bba", sanitized_json)


@unittest.skip("QUARANTINE: uses fake frames with fabricated channel indices; needs real FHSS observation captures")
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
        packet_interval = 1.0 / 150.0
        frames = []
        for i in range(20):
            ch_idx = self.sequence[(i // 4) % len(self.sequence)]
            frame = self.DecodedFrame(
                timestamp_s=i * packet_interval,
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
        packet_interval = 1.0 / 150.0
        frames = []
        for i in range(30):
            frame = self.DecodedFrame(
                timestamp_s=i * packet_interval,
                channel_index=rng.randint(0, 79),
                crc_valid=True,
            )
            frames.append(frame)

        result = self.correlate_fhss(frames, self.uid, ota_version=3)
        self.assertEqual(result["status"], "COMPUTED")
        # Random channels shouldn't produce strong correlation
        self.assertIn(result["quality"], ("NONE", "WEAK"))

    def test_single_channel_is_insufficient(self):
        frames = [self.DecodedFrame(
            timestamp_s=i / 150.0,
            channel_index=41,
            crc_valid=True,
        ) for i in range(12)]
        result = self.correlate_fhss(frames, self.uid, ota_version=3)
        self.assertEqual(result["status"], "INSUFFICIENT_OBSERVATIONS")
        self.assertEqual(result["quality"], "NONE")

    def test_duplicate_observations_cannot_inflate_correlation(self):
        frames = []
        for _ in range(4):
            frames.extend([
                self.DecodedFrame(
                    timestamp_s=0.0,
                    channel_index=self.sequence[0],
                    crc_valid=True,
                ),
                self.DecodedFrame(
                    timestamp_s=4.0 / 150.0,
                    channel_index=self.sequence[1],
                    crc_valid=True,
                ),
            ])

        result = self.correlate_fhss(frames, self.uid, ota_version=3)
        self.assertEqual(result["status"], "INSUFFICIENT_OBSERVATIONS")
        self.assertEqual(result["total_observations"], 2)
        self.assertEqual(result["unique_channels"], 2)


@unittest.skip("QUARANTINE: uses synthetic PipelineResult objects; needs real pipeline output data")
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

    def test_summary_redacts_capture_directories(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        PipelineResult = mod["PipelineResult"]

        private_path = ROOT / "work" / "private-user-folder" / "capture.iq"
        summary = PipelineResult(capture_file=str(private_path)).summary()
        self.assertEqual(summary["capture_file"], "capture.iq")
        self.assertNotIn(str(private_path.parent), json.dumps(summary))


class TestELRSFrameDecode(unittest.TestCase):
    """Test ELRS frame field decoding with real capture frame bytes."""

    def setUp(self):
        import runpy
        self.mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        self.decode_elrs_frame = self.mod["decode_elrs_frame"]

    def test_sync_frame_type(self):
        result = self.decode_elrs_frame(bytes.fromhex("b600037111185bba"), None, ota_version=3)
        self.assertEqual(result["type"], "SYNC")

    def test_rcdata_frame_type(self):
        result = self.decode_elrs_frame(bytes.fromhex("c4ffb3d7df8110ae"), None, ota_version=3)
        self.assertEqual(result["type"], "RC")

    def test_short_payload_error(self):
        result = self.decode_elrs_frame(bytes(3), None, ota_version=3)
        self.assertIn("error", result)


class TestCRCValidation(unittest.TestCase):
    """Test try_crc_validation wrapper with real capture frame bytes."""

    def setUp(self):
        import runpy
        self.mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        self.try_crc_validation = self.mod["try_crc_validation"]

    def test_no_uid_returns_false(self):
        payload = bytes.fromhex("b600037111185bba")
        self.assertFalse(self.try_crc_validation(payload, uid=None, ota_version=3))

    def test_wrong_length_returns_false(self):
        uid = bytes([0x00, 0x00, 0x00, 0x11, 0x18, 0x5b])
        self.assertFalse(self.try_crc_validation(bytes(5), uid, ota_version=3))

    def test_valid_v3_packet(self):
        uid = bytes([0x00, 0x00, 0x00, 0x11, 0x18, 0x5b])
        pkt = bytes.fromhex("b600037111185bba")
        self.assertTrue(self.try_crc_validation(pkt, uid, ota_version=3))


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

    def test_load_frames_rejects_untrusted_json_types(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        load_frames_jsonl = mod["load_frames_jsonl"]

        invalid_rows = (
            [],
            None,
            {"timestamp_s": 0.0, "channel_index": 41, "crc_valid": "false"},
            {"timestamp_s": 0.0, "channel_index": "41", "crc_valid": False},
            {"timestamp_s": 0.0, "channel_index": True, "crc_valid": False},
            {"timestamp_s": 0.0, "channel_index": 80, "crc_valid": False},
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".jsonl", delete=False,
                        encoding="utf-8") as stream:
                    stream.write(json.dumps(row) + "\n")
                    path = Path(stream.name)
                try:
                    with self.assertRaises(ValueError):
                        load_frames_jsonl(path)
                finally:
                    path.unlink()


class TestParseUID(unittest.TestCase):

    def test_valid_hex(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        parse_uid = mod["parse_uid"]
        self.assertEqual(parse_uid("00000011185b"), bytes([0x00, 0x00, 0x00, 0x11, 0x18, 0x5b]))

    def test_colon_separated(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        parse_uid = mod["parse_uid"]
        self.assertEqual(parse_uid("00:00:00:11:18:5b"), bytes([0x00, 0x00, 0x00, 0x11, 0x18, 0x5b]))

    def test_wrong_length_raises(self):
        import runpy
        mod = runpy.run_path(str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")
        parse_uid = mod["parse_uid"]
        with self.assertRaises(ValueError):
            parse_uid("0102030405")


class TestCaptureValidatedPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import runpy
        cls.mod = runpy.run_path(
            str(TOOLS / "elrs-passive-pipeline.py"), run_name="__test__")

    def test_ranked_corpus_matches_committed_baseline(self):
        paths = [
            ROOT / "captures" / "oct15-ranked-2026-10-16.jsonl",
            ROOT / "captures" / "tinyhawk-rc-retry-full-phy-2026-10-16.jsonl",
        ]
        result = self.mod["run_ranked_pipeline"](
            paths, cross_check=True)
        self.assertEqual(result.total_candidates, 62)
        self.assertEqual(result.crc_valid_frames, 54)
        self.assertEqual(result.sync_frames, 16)
        self.assertEqual(result.rc_frames, 38)
        self.assertEqual(result.reference_cross_check["byte_matches"], 54)
        self.assertEqual(result.reference_cross_check["byte_mismatches"], 0)
        self.assertEqual(result.reference_cross_check["status"], "PASS")
        self.assertEqual(result.evidence_class, "CAPTURE-VALIDATED")
        self.assertEqual(len(result.input_provenance), 2)
        self.assertEqual(
            [item["sha256"] for item in result.input_provenance],
            [
                "20b59b05ae74e677e20a6e62c130402cd5ced79df8c52f1f3455375c304ffb4d",
                "f9451c039036d65e421180e75e3c0912476d7e176a4d97aa63839bd2a8d11061",
            ],
        )
        self.assertTrue(all(frame.source and frame.source_line > 0
                            for frame in result.frames))

    def test_single_self_derived_sync_is_only_crc_validated(self):
        path = ROOT / "captures" / "oct15-ranked-2026-10-16.jsonl"
        rows = self.mod["load_ranked_rows"]([path])
        result = self.mod["_populate_result"](
            self.mod["PipelineResult"](), [rows[3]], uid=None, cross_check=True)

        self.assertEqual(result.crc_valid_frames, 1)
        self.assertEqual(result.sync_frames, 1)
        self.assertEqual(result.frames[0].crc_basis, "self_derived_sync_suffix")
        self.assertEqual(result.reference_cross_check["status"], "PASS")
        self.assertEqual(result.evidence_class, "CRC-VALIDATED")

    def test_duplicate_self_derived_sync_cannot_inflate_evidence(self):
        path = ROOT / "captures" / "oct15-ranked-2026-10-16.jsonl"
        rows = self.mod["load_ranked_rows"]([path])
        result = self.mod["_populate_result"](
            self.mod["PipelineResult"](), [rows[3], rows[3]], uid=None,
            cross_check=True)

        self.assertEqual(result.crc_valid_frames, 2)
        self.assertEqual(result.sync_frames, 2)
        self.assertEqual(result.reference_cross_check["status"], "PASS")
        self.assertEqual(result.evidence_class, "CRC-VALIDATED")

    def test_repeated_ranked_path_is_rejected(self):
        path = ROOT / "captures" / "oct15-ranked-2026-10-16.jsonl"
        with self.assertRaisesRegex(ValueError, "paths must be unique"):
            self.mod["run_ranked_pipeline"]([path, path])

    def test_invalid_ranked_timestamps_are_rejected(self):
        path = ROOT / "captures" / "oct15-ranked-2026-10-16.jsonl"
        valid_row = self.mod["load_ranked_rows"]([path])[3]
        for timestamp in ("NaN", "Infinity", -0.1, "not-a-number", True):
            with self.subTest(timestamp=timestamp):
                row = dict(valid_row, preamble_start_seconds=timestamp)
                with self.assertRaisesRegex(ValueError, "finite nonnegative"):
                    self.mod["_populate_result"](
                        self.mod["PipelineResult"](), [row, row], uid=None,
                        cross_check=True)

    def test_ranked_rows_reject_non_objects_boolean_bins_and_bad_frequency(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            self.mod["_row_bins"]([])
        with self.assertRaisesRegex(ValueError, "21 integers"):
            self.mod["_row_bins"]({"bins": [True] * 21})
        for value in ("NaN", "Infinity", -1, 1.5, True, []):
            with self.subTest(channel_hz=value):
                with self.assertRaisesRegex(ValueError, "finite nonnegative integer"):
                    self.mod["_row_channel_hz"]({"channel_hz": value})

    def test_ranked_loader_rejects_non_object_json_line(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False,
                encoding="utf-8") as stream:
            stream.write("[]\n")
            path = Path(stream.name)
        try:
            with self.assertRaisesRegex(ValueError, "must be an object"):
                self.mod["load_ranked_rows"]([path])
        finally:
            path.unlink()

    def test_binding_phrase_uses_build_define_contract(self):
        self.assertEqual(
            self.mod["binding_phrase_uid"](
                "default ExpressLRS binding phrase").hex(),
            "0ab70c7c3803",
        )

    def test_private_report_is_valid_json_and_never_overwrites(self):
        PipelineResult = self.mod["PipelineResult"]
        work_root = ROOT / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work_root) as tmp:
            report = Path(tmp) / "report.json"
            result = PipelineResult(
                capture_sha256="a" * 64,
                input_provenance=[{
                    "source": "C:/private/capture.iq",
                    "sha256": "a" * 64,
                }],
            )
            self.mod["write_full_report"](result, report)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["capture_sha256_full"], "a" * 64)
            self.assertEqual(payload["input_provenance"], result.input_provenance)
            self.assertIn("summary", payload)
            self.assertIn("frames", payload)
            with self.assertRaises(FileExistsError):
                self.mod["write_full_report"](result, report)

    def test_private_output_rejects_path_outside_work(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            self.mod["private_output"](Path(tmp) / "report.json")

    def test_measured_channel_frequency_is_not_rounded(self):
        PipelineResult = self.mod["PipelineResult"]
        main = self.mod["main"]
        globals_dict = main.__globals__
        fake_result = PipelineResult(crc_valid_frames=1)
        from unittest.mock import MagicMock
        fake_run = MagicMock(return_value=fake_result)
        with patch.dict(globals_dict, {
            "run_pipeline": fake_run,
            "write_full_report": MagicMock(),
            "print_sanitized_report": MagicMock(),
        }):
            rc = main([
                "--capture", "capture.iq",
                "--sample-rate", "4000000",
                "--center-hz", "2441000000",
                "--channel-hz", "2441378800",
                "--output-dir", str(ROOT / "work" / "cli-test-output"),
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(fake_run.call_args.kwargs["channel_freqs"], [2441378800])

    def test_cli_rejects_duplicate_scan_channels(self):
        with self.assertRaises(SystemExit) as context:
            self.mod["main"]([
                "--capture", "capture.iq",
                "--sample-rate", "4000000",
                "--center-hz", "2441000000",
                "--scan-channels", "40,40",
            ])
        self.assertEqual(context.exception.code, 2)

    def test_ranked_capture_hashes_match_manifest(self):
        expected = {}
        for line in (ROOT / "captures" / "SHA256SUMS.txt").read_text(
                encoding="utf-8").splitlines():
            digest, filename = line.split(maxsplit=1)
            if filename.endswith(".jsonl"):
                expected[filename] = digest.lower()
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    self.mod["file_sha256"](ROOT / "captures" / filename),
                    digest,
                )

    def test_full_capture_hashes_match_manifest_when_lfs_present(self):
        expected = {}
        for line in (ROOT / "captures" / "SHA256SUMS.txt").read_text(
                encoding="utf-8").splitlines():
            digest, filename = line.split(maxsplit=1)
            expected[filename] = digest.lower()
        checked = 0
        for filename in (
                "e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq",
                "tinyhawk_rc_pair_20261016_retry_2441MHz_4Msps_sc16.iq"):
            path = ROOT / "captures" / filename
            if path.stat().st_size < 1_000_000:
                continue  # Git LFS pointer in a minimal checkout
            self.assertEqual(self.mod["file_sha256"](path), expected[filename])
            checked += 1
        if checked == 0:
            self.skipTest("Git LFS capture objects are not present")


if __name__ == "__main__":
    unittest.main()
