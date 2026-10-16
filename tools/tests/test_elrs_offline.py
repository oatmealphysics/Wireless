"""Small offline tests; no radios or project captures are opened."""

import argparse
import hashlib
import importlib.util
from pathlib import Path
import struct
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

    def test_blue_container_counts_payload_not_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.cdif"
            header = bytearray(512)
            header[:12] = b"BLUEEEEIEEEI"
            struct.pack_into("<i", header, 48, 1001)
            header[52:54] = b"CF"
            struct.pack_into("<ii", header, 24, 2, 24)
            struct.pack_into("<dd", header, 32, 512.0, 16.0)
            struct.pack_into("<d", header, 264, 1 / 28e6)
            extended = (struct.pack("<ihb", 24, 16, 7) + b"D"
                        + struct.pack("<d", 2450e6) + b"RF_FREQ" + b"\0")
            capture.write_bytes(header + bytes(16) + bytes(496) + extended)
            args = argparse.Namespace(format=None, sample_rate=None, center_hz=None,
                                      channel_hz=2441.3788e6, hash=False, manifest=None)
            item = intake.inspect(capture, args, {})
            self.assertEqual(item["format"], "cf32le")
            self.assertEqual(item["complex_samples"], 2)
            self.assertEqual(item["data_offset_bytes"], 512)
            self.assertEqual(item["trailing_bytes"], 520)
            self.assertEqual(item["center_hz"], 2450e6)
            self.assertAlmostEqual(item["duration_seconds"], 2 / 28e6)
            self.assertTrue(item["channel_inside_digital_span"])


class SymbolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            import scipy  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("NumPy and SciPy are optional")
        cls.symbols = load_tool("elrs-iq-symbols.py")

    def test_blue_window_skips_header_and_trailer(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "tiny.cdif"
            expected = np.array([1 + 2j, 3 + 4j, 5 + 6j], dtype="<c8")
            header = bytearray(512)
            header[:12] = b"BLUEEEEIEEEI"
            struct.pack_into("<i", header, 48, 1001)
            header[52:54] = b"CF"
            struct.pack_into("<dd", header, 32, 512.0, float(expected.nbytes))
            struct.pack_into("<d", header, 264, 1 / 812500)
            capture.write_bytes(header + expected.tobytes() + bytes(16))
            actual, t0 = self.symbols.read_iq_window(
                capture, 812500, 0, 3 / 812500)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(t0, 0)
            with self.assertRaisesRegex(ValueError, "disagrees"):
                self.symbols.read_iq_window(capture, 800000, 0, 3 / 800000)

    def test_read_window_anchors_resampler_grid(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "grid_sc16.iq"
            values = np.arange(2048, dtype="<i2")
            iq = np.column_stack((values, -values)).astype("<i2")
            capture.write_bytes(iq.tobytes())
            first, t1 = self.symbols.read_iq_window(
                capture, 4e6, 1000 / 4e6, 100 / 4e6,
                align_samples=64)
            second, t2 = self.symbols.read_iq_window(
                capture, 4e6, 1004 / 4e6, 100 / 4e6,
                align_samples=64)
            self.assertEqual(t1, 960 / 4e6)
            self.assertEqual(t1, t2)
            np.testing.assert_array_equal(first[:100], second[:100])

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
        # FFT peak scoring can favor the adjacent sample at a chirp boundary;
        # this is below one baseband sample of timing uncertainty.
        self.assertLessEqual(abs(result["start_sample"] - start), 1)
        self.assertEqual(result["preamble_consistent"], 12)
        self.assertTrue(result["sync_consistent"])
        payload = modem.extract_payload_symbols(
            iq, result["start_sample"], result["baseline_bin"], 2)
        self.assertEqual([item["relative_bin"] for item in payload], [32, 35])
        self.assertEqual([item["alternatives"][0]["relative_bin"]
                          for item in payload], [32, 35])
        self.assertTrue(all(item["margin_db"] is not None for item in payload))
        self.assertTrue(all(len(item["alternatives"]) == 3 for item in payload))

    def test_ranked_symbol_hypotheses(self):
        import numpy as np

        modem = self.symbols
        n = np.arange(modem.N)
        up = modem.upchirp()
        strong = up * np.exp(2j * np.pi * 9 * n / modem.N)
        weak = up * np.exp(2j * np.pi * 21 * n / modem.N)
        payload_start = round((modem.PREAMBLE_SYMBOLS + modem.SYNC_SYMBOLS
                               + modem.DOWNCHIRP_SYMBOLS) * modem.N)
        iq = np.zeros(payload_start + modem.N, dtype=np.complex64)
        iq[payload_start:] = strong + 0.5 * weak
        item = modem.extract_payload_symbols(iq, 0, 0, 1, top_k=2)[0]
        self.assertEqual([candidate["relative_bin"]
                          for candidate in item["alternatives"]], [9, 21])
        self.assertAlmostEqual(item["margin_db"], 6.02, places=1)
        with self.assertRaisesRegex(ValueError, "top_k"):
            modem.extract_payload_symbols(iq, 0, 0, 1, top_k=0)


if __name__ == "__main__":
    unittest.main()
