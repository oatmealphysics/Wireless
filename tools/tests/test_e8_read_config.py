"""Tests for the immutable vendor read request; no hardware required."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("e8_reader", ROOT / "tools/e8-read-config.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class VendorReaderTests(unittest.TestCase):
    def test_fixed_read_request(self):
        self.assertEqual(reader.READ_REQUEST, b"\x02\x01" + bytes(62))
        self.assertEqual(len(reader.READ_REQUEST), 64)

    def test_default_does_not_import_or_open_serial(self):
        with patch.dict(sys.modules, {"serial": None}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(reader.main(["--port", "COM4"]), 0)
        self.assertFalse(json.loads(output.getvalue())["execute"])

    def test_wrong_device_rejected_before_open(self):
        serial = types.ModuleType("serial")
        tools = types.ModuleType("serial.tools")
        tools.list_ports = types.SimpleNamespace(comports=lambda: [types.SimpleNamespace(
            device="COM4", vid=0x067B, pid=0x23A3)])
        serial.Serial = lambda: self.fail("Must not open an unrecognized device")
        with patch.dict(sys.modules, {"serial": serial, "serial.tools": tools}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(reader.main(["--port", "COM4", "--execute"]), 1)


if __name__ == "__main__":
    unittest.main()
