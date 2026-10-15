"""Small offline tests; no radios or project captures are opened."""

import argparse
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]


def load_tool(filename):
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


intake = load_tool("elrs-capture-intake.py")


class IntakeTests(unittest.TestCase):
    def test_filename_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "sample_2441MHz_4Msps_sc16.iq"
            payload = bytes(range(16))
            capture.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            manifest = root / "SHA256SUMS.txt"
            manifest.write_text(f"{digest}  {capture.name}\n", encoding="utf-8")
            args = argparse.Namespace(format=None, sample_rate=None, center_hz=None,
                                      channel_hz=2441.3788e6, hash=False, manifest=manifest)
            item = intake.inspect(capture, args, intake.load_manifest(manifest))
            self.assertEqual(item["format"], "sc16le")
            self.assertEqual(item["complex_samples"], 4)
            self.assertEqual(item["manifest_status"], "match")
            self.assertTrue(item["channel_inside_digital_span"])

    def test_misaligned_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "bad_sc16.iq"
            capture.write_bytes(b"12345")
            args = argparse.Namespace(format="sc16le", sample_rate=4e6,
                                      center_hz=2441e6, channel_hz=2441.3788e6,
                                      hash=False, manifest=None)
            item = intake.inspect(capture, args, {})
            self.assertFalse(item["sample_aligned"])
            self.assertNotIn("complex_samples", item)


class SymbolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            import scipy  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("NumPy and SciPy are optional")
        cls.symbols = load_tool("elrs-iq-symbols.py")

    def test_preamble_sync_and_payload(self):
        import numpy as np

        modem = self.symbols
        n = np.arange(modem.N)
        up = modem.upchirp()

        def chirp(bin_number):
            return up * np.exp(2j * np.pi * bin_number * n / modem.N)

        start = 150
        down = up.conj()
        frame = np.concatenate(
            [chirp(17) for _ in range(12)] + [chirp(25), chirp(33),
            down, down, down[:modem.N // 4], chirp(49), chirp(52)]
        ).astype(np.complex64)
        iq = np.concatenate([np.zeros(start, np.complex64), frame,
                             np.zeros(200, np.complex64)])
        result = modem.find_preamble(iq, start - 10, start + 10)
        self.assertEqual(result["start_sample"], start)
        self.assertEqual(result["preamble_consistent"], 12)
        self.assertTrue(result["sync_consistent"])
        payload = modem.extract_payload_symbols(iq, start, result["baseline_bin"], 2)
        self.assertEqual([item["relative_bin"] for item in payload], [32, 35])


if __name__ == "__main__":
    unittest.main()
