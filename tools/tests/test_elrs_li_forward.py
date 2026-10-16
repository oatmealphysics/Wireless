"""Known-answer tests for the offline SX1280 LI forward path."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "tools" / "elrs-li-forward.py"
SYMBOLS = ROOT / "tools" / "elrs-iq-symbols.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LongInterleaverForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forward = load(FORWARD, "elrs_li_forward")

    def test_independent_tx16s_off_air_vectors_encode_exactly(self):
        vectors = [
            ("00fab567c57c5658011f7c7873",
             [117, 81, 73, 5, 117, 17, 29, 5, 121, 76, 111, 77,
              65, 21, 119, 42, 127, 75, 67, 15, 81, 83, 46, 80,
              37, 49, 1, 93, 22, 35, 106, 52]),
            ("00fab967c57c5658011f7ce639",
             [117, 81, 73, 5, 73, 17, 29, 5, 121, 72, 106, 77,
              64, 20, 120, 42, 34, 118, 67, 15, 0, 83, 46, 80,
              42, 52, 1, 93, 22, 35, 106, 116]),
        ]
        for packet_hex, expected in vectors:
            with self.subTest(packet_hex=packet_hex):
                self.assertEqual(
                    self.forward.encode_symbols(bytes.fromhex(packet_hex)), expected)

    def test_eight_byte_round_trip(self):
        packet = bytes.fromhex("0123456789abcdef")
        symbols = self.forward.encode_symbols(packet)
        self.assertEqual(len(symbols), 21)
        decoded = self.forward.decode_symbols(symbols)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded[0], packet)
        self.assertEqual(decoded[1], 0)

    def test_msp_request_matches_betaflight_fixture(self):
        request = self.forward.build_msp_v1_request(0x70)
        self.assertEqual(
            request[:13],
            bytes.fromhex("00 0d 7a c8 ea 30 00 70 70 00 00 00 00"))
        self.assertEqual(
            self.forward.stubborn_chunks(request),
            [(1, request[0:5]), (2, request[5:10]), (0, request[10:15])])

    def test_api_version_canary_is_crc_valid_and_decodable(self):
        uid = bytes.fromhex("12 34 56 78 9a bc")
        frames = self.forward.build_api_version_canary(uid)
        self.assertEqual([item["package_index"] for item in frames], [1, 2, 0])
        for item in frames:
            verdict = self.forward.CHECKER["check_frame"](item["packet"], uid=uid)
            self.assertEqual(verdict["type"], "MSP")
            self.assertEqual(verdict["crc"], "pass")
            decoded = self.forward.decode_symbols(item["symbols"])
            self.assertEqual(decoded[0], item["packet"])

    def test_waveform_recovers_encoded_symbols(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("NumPy unavailable")
        symbols_tool = load(SYMBOLS, "elrs_iq_symbols_forward_test")
        packet = bytes.fromhex("0123456789abcdef")
        encoded = self.forward.encode_symbols(packet)
        iq = self.forward.modulate_symbols(encoded)
        found = symbols_tool.find_preamble(iq, 0, 1)
        self.assertEqual(found["preamble_consistent"], 12)
        self.assertTrue(found["sync_consistent"])
        extracted = symbols_tool.extract_payload_symbols(
            iq, found["start_sample"], found["baseline_bin"], len(encoded))
        self.assertEqual([item["relative_bin"] for item in extracted], encoded)

    def test_rejects_bad_lengths_and_indices(self):
        with self.assertRaisesRegex(ValueError, "8 or 13"):
            self.forward.encode_symbols(b"short")
        with self.assertRaisesRegex(ValueError, "index"):
            self.forward.build_ota_msp_packet(3, bytes(5), bytes(6))
        with self.assertRaisesRegex(ValueError, "ignored work"):
            self.forward.require_ignored_work_path(ROOT / "private-plan.json")
        with self.assertRaisesRegex(ValueError, "samples_per_chip"):
            self.forward.modulate_symbols([1], 0)


if __name__ == "__main__":
    unittest.main()
