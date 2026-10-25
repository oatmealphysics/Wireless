"""Synthetic, receive-only tests for automatic LoRa PHY candidate finding."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ImportError:
    np = None


TOOL = Path(__file__).resolve().parents[1] / "elrs-phy-scan.py"


@unittest.skipIf(np is None, "NumPy is optional")
class PhyScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import scipy  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("SciPy is optional")
        spec = importlib.util.spec_from_file_location("elrs_phy_scan", TOOL)
        cls.scan = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.scan)
        cls.scan.WORK_ROOT.mkdir(parents=True, exist_ok=True)
        cls.symbols = cls.scan.load_symbols()

    def make_frame(self):
        modem = self.symbols
        n = np.arange(modem.N)
        up = modem.upchirp()

        def chirp(bin_number):
            return up * np.exp(2j * np.pi * bin_number * n / modem.N)

        return np.concatenate(
            [chirp(17) for _ in range(12)] +
            [chirp(25), chirp(33), up.conj(), up.conj(),
             up.conj()[:modem.N // 4]] +
            [chirp(49) for _ in range(21)]).astype(np.complex64)

    def test_energy_regions_and_adjacent_preambles(self):
        rate = self.symbols.BW_HZ
        frame = self.make_frame()
        first = 350
        second = first + round(rate / 150)
        iq = np.zeros(round(rate * 0.05), dtype=np.complex64)
        iq[first:first + len(frame)] = frame
        iq[second:second + len(frame)] = frame
        rng = np.random.default_rng(1234)
        iq += (rng.normal(0, 0.01, len(iq)) +
               1j * rng.normal(0, 0.01, len(iq))).astype(np.complex64)
        regions = self.scan.energy_regions(iq, rate)
        self.assertGreaterEqual(len(regions), 1)
        # Force a merged energy region to exercise the second-preamble search.
        with patch.object(self.scan, "energy_regions",
                          return_value=[(first - 32, second + len(frame))]):
            results = self.scan.scan_chunk(iq, 0.0, rate, 6.0, 21, self.symbols)
        self.assertEqual(len(results), 2)
        self.assertLess(abs(results[0]["preamble_start_seconds"] - first / rate), 2 / rate)
        self.assertLess(abs(results[1]["preamble_start_seconds"] - second / rate), 2 / rate)
        self.assertTrue(all(not item["packet_bytes_decoded"] for item in results))

    def test_private_output_rejects_tracked_and_existing_paths(self):
        with self.assertRaises(ValueError):
            self.scan.private_output(TOOL.parent / "candidate.jsonl")
        with tempfile.TemporaryDirectory(dir=self.scan.WORK_ROOT) as directory:
            path = Path(directory) / "candidate.jsonl"
            self.assertEqual(self.scan.private_output(path), path.resolve())
            path.write_text("private", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                self.scan.private_output(path)

    def test_quiet_noise_has_no_candidate(self):
        rng = np.random.default_rng(6789)
        noise = (rng.normal(0, 0.01, 50_000) +
                 1j * rng.normal(0, 0.01, 50_000)).astype(np.complex64)
        results = self.scan.scan_chunk(
            noise, 0.0, self.symbols.BW_HZ, 6.0, 21, self.symbols)
        self.assertEqual(results, [])

    def test_cf32_file_end_to_end(self):
        rate = self.symbols.BW_HZ
        frame = self.make_frame()
        iq = np.zeros(round(rate * 0.05), dtype=np.complex64)
        first = 350
        second = first + round(rate / 150)
        iq[first:first + len(frame)] = frame
        iq[second:second + len(frame)] = frame
        rng = np.random.default_rng(8765)
        iq += (rng.normal(0, 0.01, len(iq)) +
               1j * rng.normal(0, 0.01, len(iq))).astype(np.complex64)
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "synthetic_cf32.iq"
            capture.write_bytes(iq.astype("<c8").tobytes())
            found = self.scan.scan_file(
                capture, rate, 2_441_400_000, 2_441_400_000,
                0, 0.05, 0.05, 6.0, 21, "cf32le")
        self.assertEqual(len(found), 2)
        self.assertTrue(all(not item["packet_bytes_decoded"] for item in found))


if __name__ == "__main__":
    unittest.main()
