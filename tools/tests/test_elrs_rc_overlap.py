"""Read-only overlap checks against the published stick-motion USB log."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "elrs-rc-overlap.py"


class RcOverlapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("elrs_rc_overlap", TOOL)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_predeclared_timestamp_and_channel_mapping(self):
        decoded = self.tool.read_jsonl(ROOT / "captures" / "decoded-v3-summary.jsonl")
        usb = self.tool.read_jsonl(
            ROOT / "captures" / "tinyhawk_rc_pair_20261016_retry_msp_rc.jsonl")
        result = self.tool.compare(
            decoded, usb, "tinyhawk-rc-retry-full-phy-2026-10-16.jsonl",
            self.tool.parse_utc("2026-10-16T17:45:37.014895Z"))
        self.assertEqual(result["matched"], 5)
        self.assertEqual(result["mean_absolute_error"], [13.6, 0, 0, 0])
        self.assertEqual(result["pairs"][3]["predicted_msp"],
                         result["pairs"][3]["observed_msp"])

    def test_unanchored_time_is_not_fitted(self):
        result = self.tool.compare(
            [{"source": "x", "status": "crc_valid", "type": "RC",
              "preamble_start_seconds": 0.0, "ota_analog_channels": [988] * 4,
              "line": 1}],
            [{"utc": "2026-10-16T00:00:00Z", "channels": [988] * 4}],
            "x", self.tool.parse_utc("2026-10-16T00:01:00Z"))
        self.assertEqual(result["matched"], 0)

    def test_parse_utc_normalizes_submicrosecond_precision(self):
        expected = self.tool.parse_utc("2026-10-16T17:45:48.403259+00:00")
        self.assertEqual(
            self.tool.parse_utc("2026-10-16T17:45:48.4032590Z"), expected)
        self.assertEqual(
            self.tool.parse_utc("2026-10-16T13:45:48.4032599-04:00"), expected)

    def test_parse_utc_rejects_naive_timestamp(self):
        with self.assertRaisesRegex(ValueError, "include Z or an offset"):
            self.tool.parse_utc("2026-10-16T17:45:48.403259")


if __name__ == "__main__":
    unittest.main()
