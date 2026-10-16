"""Offline tests for private batch symbol export; no captures or radios used."""

from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "elrs-batch-evidence.py"
SPEC = importlib.util.spec_from_file_location("elrs_batch_evidence", TOOL)
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


class BatchEvidenceTests(unittest.TestCase):
    def test_private_output_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "work"
            private.mkdir()
            with patch.object(batch, "WORK_ROOT", private):
                self.assertEqual(batch.private_output_path(private / "symbols.jsonl"),
                                 (private / "symbols.jsonl").resolve())
                with self.assertRaises(ValueError):
                    batch.private_output_path(root / "tracked.jsonl")
                existing = private / "existing.jsonl"
                existing.write_text("keep", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    batch.private_output_path(existing)
                self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_batch_writes_private_symbols_but_only_summarizes_terminal(self):
        class FakeSymbols:
            @staticmethod
            def analyze(_capture, _sample_rate, _center, _channel, guess,
                        _search_us, _max_symbols):
                return {"capture": "private-test.iq",
                        "preamble_start_seconds": guess,
                        "phy_candidate": True,
                        "preamble": {"preamble_consistent": 12,
                                     "sync_consistent": True},
                        "payload_symbols": [
                            {"relative_bin": 99, "peak_power_fraction": 0.9},
                            {"relative_bin": 42, "peak_power_fraction": 0.1},
                        ]}

        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "work"
            private.mkdir()
            output = private / "symbols.jsonl"
            log = io.StringIO()
            with patch.object(batch, "WORK_ROOT", private), \
                 patch.object(batch, "load_symbol_tool", return_value=FakeSymbols()), \
                 redirect_stderr(log):
                code = batch.main(["--capture", "unused.iq", "--sample-rate", "4000000",
                                   "--center-hz", "2441000000", "--channel-hz", "2441378800",
                                   "--packet-start", "1.0", "--packet-start", "1.01",
                                   "--output", str(output)])
            self.assertEqual(code, 0)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["payload_symbols"][0]["relative_bin"], 99)
            self.assertIn('"strong_symbols": 1', log.getvalue())
            self.assertNotIn("relative_bin", log.getvalue())
            self.assertNotIn('"capture"', log.getvalue())


if __name__ == "__main__":
    unittest.main()
