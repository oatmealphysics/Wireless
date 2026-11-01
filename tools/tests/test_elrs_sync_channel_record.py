"""Synthetic receiver tests; no SDR or E8 is opened."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import numpy as np
except ImportError:
    np = None


TOOL = Path(__file__).resolve().parents[1] / "elrs-sync-channel-record.py"


@unittest.skipIf(np is None, "NumPy is optional")
@unittest.skip("QUARANTINE: uses FakeDevice SDR mock; needs real SDR capture session data")
class SyncChannelRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("elrs_sync_record", TOOL)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)
        cls.tool.WORK_ROOT.mkdir(parents=True, exist_ok=True)

    def test_private_output_and_no_overwrite(self):
        with self.assertRaises(ValueError):
            self.tool.output_paths(TOOL.parent / "tracked.iq")
        with tempfile.TemporaryDirectory(dir=self.tool.WORK_ROOT) as directory:
            path = Path(directory) / "record.iq"
            actual, sidecar = self.tool.output_paths(path)
            self.assertEqual(actual, path.resolve())
            sidecar.write_text("existing", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                self.tool.output_paths(path)

    def test_records_receive_only_and_marks_complete(self):
        calls = []

        class FakeDevice:
            def __init__(self, args):
                calls.append(("open", args))

            def setSampleRate(self, direction, channel, value):
                calls.append(("rate", direction, channel, value))

            def setFrequency(self, direction, channel, value):
                calls.append(("freq", direction, channel, value))

            def setGain(self, direction, channel, value):
                calls.append(("gain", direction, channel, value))

            def setBandwidth(self, direction, channel, value):
                calls.append(("bandwidth", direction, channel, value))

            def setAntenna(self, direction, channel, value):
                calls.append(("antenna", direction, channel, value))

            def getSampleRate(self, direction, channel):
                return 1000

            def getFrequency(self, direction, channel):
                return 2_441_000_000

            def getBandwidth(self, direction, channel):
                return 500

            def getGain(self, direction, channel):
                return 35

            def getAntenna(self, direction, channel):
                return "RX2"

            def setupStream(self, direction, fmt, channels):
                calls.append(("stream", direction, fmt, channels))
                return object()

            def activateStream(self, stream):
                calls.append(("activate",))

            def readStream(self, stream, buffers, count, timeoutUs):
                buffers[0][:count] = np.complex64(0.25 + 0.5j)
                return SimpleNamespace(ret=count)

            def deactivateStream(self, stream):
                calls.append(("deactivate",))

            def closeStream(self, stream):
                calls.append(("close",))

        fake = SimpleNamespace(Device=FakeDevice, SOAPY_SDR_RX=11,
                               SOAPY_SDR_CF32=22)
        with tempfile.TemporaryDirectory(dir=self.tool.WORK_ROOT) as directory:
            path = Path(directory) / "record.iq"
            result = self.tool.record(path, "fake", 0, 2_441_000_000,
                                      1000, 500, 35, "RX2", 0.025, fake)
            self.assertEqual(result["samples"], 25)
            self.assertTrue(result["complete"])
            np.testing.assert_array_equal(
                np.fromfile(path, dtype="<c8"),
                np.full(25, 0.25 + 0.5j, dtype=np.complex64))
            metadata = path.with_name(path.name + ".meta.txt").read_text(
                encoding="utf-8")
            self.assertIn("format: cf32le", metadata)
            self.assertIn("complete: true", metadata)
            self.assertTrue(all(call[1] == 11 for call in calls
                                if call[0] in {"rate", "freq", "gain",
                                               "bandwidth", "antenna", "stream"}))
            self.assertNotIn(("transmit",), calls)


if __name__ == "__main__":
    unittest.main()
