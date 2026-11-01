"""The unvalidated SYNC prototype must not claim packet recovery."""

import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "elrs-sync-capture.py"


class SyncCaptureQuarantineTests(unittest.TestCase):
    def test_every_legacy_mode_fails_before_opening_inputs(self):
        modes = (
            ["--iq-file", "nonexistent-private-capture.iq", "--sample-rate", "4000000",
             "--center-freq", "2441000000"],
            ["--sdr"],
            ["--stdin-raw"],
            ["--self-test"],
            ["--help"],
        )
        for arguments in modes:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, str(TOOL), *arguments],
                    input="02280504003ae2ff\n", text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False, timeout=15,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("Unsupported SYNC decoder prototype", result.stderr)
                self.assertNotIn("02280504003ae2ff", result.stderr)


if __name__ == "__main__":
    unittest.main()
