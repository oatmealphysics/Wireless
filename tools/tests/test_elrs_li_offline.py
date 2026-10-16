"""Independent off-air and public-capture tests for the SF7 SX1280 LI inverse.

The two TX16S vectors are from v0l/WaveShark commit
62b513a7b699fd3a6928908102902abf9d9b2650 (GPL-3.0-or-later).
SPDX-License-Identifier: GPL-3.0-or-later
"""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "elrs-li-offline.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("elrs_li_offline", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LongInterleaverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_independent_tx16s_off_air_vectors(self):
        vectors = [
            ([117, 81, 73, 5, 117, 17, 29, 5, 121, 76, 111, 77,
              65, 21, 119, 42, 127, 75, 67, 15, 81, 83, 46, 80,
              37, 49, 1, 93, 22, 35, 106, 52],
             "00fab567c57c5658011f7c7873"),
            ([117, 81, 73, 5, 73, 17, 29, 5, 121, 72, 106, 77,
              64, 20, 120, 42, 34, 118, 67, 15, 0, 83, 46, 80,
              42, 52, 1, 93, 22, 35, 106, 116],
             "00fab967c57c5658011f7ce639"),
        ]
        for symbols, expected in vectors:
            with self.subTest(expected=expected):
                decoded = self.tool.decode_symbols(symbols, payload_bytes=13)
                self.assertIsNotNone(decoded)
                self.assertEqual(decoded[0], bytes.fromhex(expected))

    def test_real_captures_require_crc_not_only_fec(self):
        paths = [ROOT / "captures" / "oct15-ranked-2026-10-16.jsonl",
                 ROOT / "captures" / "tinyhawk-rc-retry-full-phy-2026-10-16.jsonl"]
        rows = self.tool.read_rows(paths)
        results, summary = self.tool.analyze(rows)
        self.assertEqual(summary["total"], 62)
        self.assertEqual(summary["crc_valid"], 54)
        self.assertEqual(summary["types"], {"RC": 38, "MSP": 0, "SYNC": 16, "TLM": 0})
        self.assertTrue(summary["sync_suffix_consistent"])
        self.assertTrue(all("packet_bytes_hex" not in item for item in results))
        for item in results:
            if item["status"] == "crc_valid" and item["type"] == "SYNC":
                self.assertEqual(item["sync"]["rate_index"], 7)
                self.assertEqual(item["sync"]["switch_mode"], "hybrid")

    def test_ota_analog_unpacking(self):
        fields = [512, 15, 1023, 0]
        packed = sum(value << (10 * index) for index, value in enumerate(fields))
        packet = b"\x00" + packed.to_bytes(5, "little") + b"\x00\x00"
        self.assertEqual(self.tool.rc_channels(packet),
                         [value + 988 for value in fields])

    def test_invalid_symbol_rejected(self):
        with self.assertRaisesRegex(ValueError, "0..127"):
            self.tool.symbol_bits([128] * 21)
        with self.assertRaisesRegex(ValueError, "need 21"):
            self.tool.symbol_bits([0] * 20)


if __name__ == "__main__":
    unittest.main()
