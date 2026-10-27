# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture regressions for the public SX1280 SF7/4-8-LI decoder.

The reference implementation is ``tools/elrs-li-offline.py``, whose measured
mapping is adapted from v0l/WaveShark commit
62b513a7b699fd3a6928908102902abf9d9b2650 (GPL-3.0-or-later).
"""

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import lora_decode


def load_reference():
    """Load the maintained standalone decoder under a non-package filename."""
    path = TOOLS / "elrs-li-offline.py"
    spec = importlib.util.spec_from_file_location("elrs_li_capture_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_ranked_rows(path: Path) -> list[dict]:
    """Read only the fields required to decode and CRC-check ranked rows."""
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            payload = raw["payload_symbols"]
            bins = [item["relative_bin"] for item in payload[:21]]
            rows.append({
                "source": path.name,
                "line": line_number,
                "preamble_start_seconds": raw.get("preamble_start_seconds"),
                "bins": bins,
            })
    return rows


class Sx1280CaptureDecodeTests(unittest.TestCase):
    DATASETS = (
        ("oct15-ranked-2026-10-16.jsonl", 34, 32),
        ("tinyhawk-rc-retry-full-phy-2026-10-16.jsonl", 28, 22),
    )

    @classmethod
    def setUpClass(cls):
        cls.reference = load_reference()

    def test_every_capture_candidate_and_offset_matches_reference(self):
        comparisons = 0
        for filename, expected_rows, _ in self.DATASETS:
            rows = load_ranked_rows(ROOT / "captures" / filename)
            self.assertEqual(len(rows), expected_rows)
            for row in rows:
                for offset in (0, 1):
                    bins = [(value + offset) & 0x7F for value in row["bins"]]
                    with self.subTest(
                            source=filename, line=row["line"], offset=offset):
                        expected = self.reference.decode_symbols(bins)
                        actual = lora_decode.decode_sx1280_li(bins)
                        self.assertEqual(actual, expected)
                        if expected is not None:
                            self.assertEqual(actual[0], expected[0])
                    comparisons += 1
        self.assertEqual(comparisons, 2 * (34 + 28))

    def test_capture_crc_acceptance_matches_reference(self):
        accepted_total = 0
        for filename, expected_rows, expected_accepted in self.DATASETS:
            rows = load_ranked_rows(ROOT / "captures" / filename)
            with patch.object(
                    self.reference,
                    "decode_symbols",
                    side_effect=lora_decode.decode_sx1280_li):
                actual_results, actual_summary = self.reference.analyze(rows)
            reference_results, reference_summary = self.reference.analyze(rows)

            with self.subTest(source=filename):
                self.assertEqual(actual_summary, reference_summary)
                self.assertEqual(actual_results, reference_results)
                self.assertEqual(actual_summary["total"], expected_rows)
                self.assertEqual(actual_summary["crc_valid"], expected_accepted)
            accepted_total += actual_summary["crc_valid"]

        self.assertEqual(accepted_total, 54)


if __name__ == "__main__":
    unittest.main()
