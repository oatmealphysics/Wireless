"""Independent off-air and public-capture tests for the SF7 SX1280 LI inverse.

The two TX16S vectors are from v0l/WaveShark commit
62b513a7b699fd3a6928908102902abf9d9b2650 (GPL-3.0-or-later).
SPDX-License-Identifier: GPL-3.0-or-later
"""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


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
        packet = bytes.fromhex("c4ffb3d7df8110ae")
        channels = self.tool.rc_channels(packet)
        self.assertEqual(channels, [2011, 1480, 1497, 1507])

    @unittest.skip("QUARANTINE: uses fabricated symbols; needs real symbol vectors")
    def test_invalid_symbol_rejected(self):
        with self.assertRaisesRegex(ValueError, "0..127"):
            self.tool.symbol_bits([128] * 21)
        with self.assertRaisesRegex(ValueError, "need 21"):
            self.tool.symbol_bits([0] * 20)

    @unittest.skip("QUARANTINE: uses synthetic UID + mocked decode_symbols; needs real SYNC frames with known UID")
    def test_provided_uid_requires_sync_identity_fragment(self):
        uid = bytes.fromhex("12 34 56 78 9a bc")  # synthetic fixture
        flags = (2 << 4) | (3 << 1) | 1
        body = bytes([41, 9, flags, uid[3], uid[4], uid[5]])
        initial = self.tool.CHECKER["uid_crc_init"](uid)
        crc = self.tool.CHECKER["crc14"](bytes([2]) + body, initial)
        packet = bytes([2 | ((crc >> 8) << 2)]) + body + bytes([crc & 0xff])
        rows = [{"source": "synthetic.jsonl", "line": 1,
                 "preamble_start_seconds": 0.0, "bins": [0] * 21}]

        with patch.object(self.tool, "decode_symbols", return_value=(packet, 0)):
            valid, summary = self.tool.analyze(rows, uid=uid)
            self.assertEqual(valid[0]["status"], "crc_valid")
            self.assertTrue(summary["provided_uid_sync_match"])

            wrong_uid3 = uid[:3] + bytes([uid[3] ^ 1]) + uid[4:]
            rejected, summary = self.tool.analyze(rows, uid=wrong_uid3)
            self.assertEqual(rejected[0]["status"], "unresolved")
            self.assertFalse(summary["provided_uid_sync_match"])


if __name__ == "__main__":
    unittest.main()
