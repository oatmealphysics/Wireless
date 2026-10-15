"""Offline tests; never open a physical serial device."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("serial_probe", ROOT / "tools/elrs-serial-probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeSerial:
    def __init__(self, incoming=b"", **kwargs):
        self.incoming = incoming
        self.writes = []
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, size):
        data, self.incoming = self.incoming[:size], self.incoming[size:]
        return data

    def write(self, data):
        self.writes.append(data)
        if data[2] == 0x28:
            self.incoming += device_frame()
        elif data[2] == 0x2C:
            index, chunk = data[5:7]
            payload = bytes([0xEA, 0xEE, index, 1 if chunk == 0 else 0])
            self.incoming += probe.build_crsf_frame(0x2B, payload + (b"\x00\x09Mode\0" if chunk == 0 else b"A;B\0\x00"))

    def flush(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


def device_frame():
    body = bytes([0xEA, 0xEE]) + b"Lab module\0" + struct.pack(">III", 123, 4, 5) + bytes([1, 0])
    return probe.build_crsf_frame(0x29, body, 0xEA)


class ProtocolTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(probe.crsf_crc8(b"123456789"), 0xBC)

    def test_constants_against_pinned_source(self):
        import re
        with tarfile.open(ROOT / "firmware/src/expresslrs-4.1.0.tar.gz") as archive:
            header = archive.extractfile("ExpressLRS-4.1.0/src/include/crsf_protocol.h").read().decode()
        for number, name in probe.CRSF_TYPES.items():
            source_name = "DEVICE_PING" if name == "PING_DEVICES" else name
            self.assertRegex(header, rf"CRSF_FRAMETYPE_{source_name}\s*=\s*0x{number:02X}\b")
        for number, name in probe.CRSF_ADDR.items():
            self.assertRegex(header, rf"CRSF_ADDRESS_{name}\s*=\s*0x{number:02X}\b")

    def test_request_headers(self):
        self.assertEqual(probe.build_ping()[:5], bytes([0xC8, 4, 0x28, 0, 0xEA]))
        self.assertEqual(probe.build_device_info_request()[3:5], bytes([0xEE, 0xEA]))
        self.assertEqual(probe.build_param_read(7, 2)[:7], bytes([0xC8, 6, 0x2C, 0xEE, 0xEA, 7, 2]))

    def test_total_length_limit(self):
        self.assertEqual(len(probe.build_crsf_frame(0x16, bytes(60))), 64)
        with self.assertRaises(ValueError):
            probe.build_crsf_frame(0x16, bytes(61))

    def test_invalid_lengths_and_sync(self):
        for data in (b"", b"\xc8", b"\xc8\x00\x29\x00", b"\xc8\x3f\x29\x00", b"\x01\x02\x29\x00"):
            self.assertIsNone(probe.parse_frame(data))

    def test_every_fragment_boundary(self):
        data = device_frame()
        for split in range(len(data) + 1):
            decoder = probe.FrameDecoder()
            frames = decoder.feed(data[:split]) + decoder.feed(data[split:])
            self.assertEqual([f["raw"] for f in frames], [data])

    def test_addressed_sync_variants(self):
        for address in probe.CRSF_START_BYTES:
            data = probe.build_crsf_frame(0x08, b"payload", address)
            self.assertEqual(probe.FrameDecoder().feed(data)[0]["sync"], address)

    def test_noise_and_bad_crc(self):
        bad = bytearray(device_frame())
        bad[-1] ^= 1
        frames = probe.FrameDecoder().feed(b"noise\xc8\x00" + bad + device_frame())
        self.assertEqual([f["raw"] for f in frames], [device_frame()])

    def test_corrupt_length_does_not_swallow_next_frame(self):
        frames = probe.FrameDecoder().feed(b"\xc8\x08\x29\x01" + device_frame())
        self.assertEqual([f["raw"] for f in frames], [device_frame()])

    def test_device_info_requires_complete_payload(self):
        payload = probe.parse_frame(device_frame())["payload"]
        for cut in range(len(payload)):
            self.assertEqual(probe.extract_device_info(payload[:cut]), {})
        info = probe.extract_device_info(payload)
        self.assertEqual((info["name"], info["hw_ver"], info["sw_ver"], info["param_count"]), ("Lab module", 4, 5, 1))

    def test_raw_capture_includes_noise(self):
        raw = b"noise" + device_frame() + b"\xc8"
        capture = io.BytesIO()
        decoder = probe.FrameDecoder()
        frames = probe.try_read_frames(FakeSerial(raw), 0.002, decoder, capture)
        self.assertEqual(capture.getvalue(), raw)
        self.assertEqual(len(frames), 1)
        self.assertEqual(decoder.buffer, b"\xc8")


class CliTests(unittest.TestCase):
    def run_cli(self, args, fake):
        serial = types.ModuleType("serial")
        serial.Serial = lambda *a, **kw: fake
        tools = types.ModuleType("serial.tools")
        tools.list_ports = types.SimpleNamespace(comports=lambda: [])
        modules = {"serial": serial, "serial.tools": tools}
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(sys.modules, modules), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = probe.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_default_is_passive_and_closes_port(self):
        fake = FakeSerial(device_frame())
        code, stdout, _ = self.run_cli(["-p", "fake", "-t", "0.002"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.writes, [])
        self.assertTrue(fake.closed)
        self.assertEqual(json.loads(stdout)["device_info"]["name"], "Lab module")

    def test_probe_reads_all_parameter_chunks(self):
        fake = FakeSerial()
        code, stdout, _ = self.run_cli(["-p", "fake", "-t", "0.002", "--probe", "--dump-params"], fake)
        self.assertEqual(code, 0)
        self.assertEqual([f[2] for f in fake.writes], [0x28, 0x2C, 0x2C])
        self.assertEqual([f[6] for f in fake.writes[1:]], [0, 1])
        entries = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual(entries[-1]["raw_data"], b"\x00\x09Mode\0A;B\0\x00".hex())
        self.assertTrue(fake.closed)

    def test_cli_errors_before_opening_port(self):
        cases = [["--dump-params"], ["-p", "fake", "-t", "nan"],
                 ["-p", "fake", "-t", "0"], ["-p", "fake", "-t", "inf"],
                 ["-p", "fake", "--probe", "--listen-only"], ["--decode", "file", "--probe"]]
        for args in cases:
            fake = FakeSerial()
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.run_cli(args, fake)
            self.assertEqual(fake.writes, [])

    def test_existing_capture_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.bin"
            path.write_bytes(b"original")
            fake = FakeSerial()
            code, _, _ = self.run_cli(["-p", "fake", "--capture", str(path)], fake)
            self.assertEqual(code, 1)
            self.assertEqual(path.read_bytes(), b"original")
            self.assertFalse(fake.closed)  # It was never opened.

    def test_offline_decode(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.bin"
            path.write_bytes(b"noise" + device_frame())
            code, stdout, _ = self.run_cli(["--decode", str(path)], FakeSerial())
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["type"], 0x29)

    def test_missing_discovery_is_a_failure(self):
        fake = FakeSerial()
        fake.write = lambda data: fake.writes.append(data)
        code, _, stderr = self.run_cli(["-p", "fake", "-t", "0.002", "--probe"], fake)
        self.assertEqual(code, 1)
        self.assertEqual(len(fake.writes), 2)
        self.assertIn("unconfirmed", stderr)
        self.assertTrue(fake.closed)

    def test_wrong_origin_is_not_module_identity(self):
        fake = FakeSerial()
        def respond(data):
            fake.writes.append(data)
            payload = bytearray(probe.parse_frame(device_frame())["payload"])
            payload[1] = 0xEC
            fake.incoming += probe.build_crsf_frame(0x29, payload)
        fake.write = respond
        code, _, _ = self.run_cli(["-p", "fake", "-t", "0.002", "--probe"], fake)
        self.assertEqual(code, 1)

    def test_incomplete_parameter_is_a_failure(self):
        fake = FakeSerial()
        write = fake.write
        fake.write = lambda data: write(data) if data[2] == 0x28 else fake.writes.append(data)
        code, stdout, stderr = self.run_cli(["-p", "fake", "-t", "0.002", "--probe", "--dump-params"], fake)
        self.assertEqual(code, 1)
        self.assertIn("incomplete", stderr)
        self.assertNotIn('"complete": true', stdout)

    def test_serial_error_closes_resources(self):
        fake = FakeSerial()
        def fail_read(size):
            raise OSError("disconnected")
        fake.read = fail_read
        code, _, stderr = self.run_cli(["-p", "fake", "-t", "0.002"], fake)
        self.assertEqual(code, 1)
        self.assertIn("disconnected", stderr)
        self.assertTrue(fake.closed)

    def test_capture_creates_parent_and_preserves_all_rx(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "new" / "capture.bin"
            raw = b"noise" + device_frame()
            code, _, _ = self.run_cli(["-p", "fake", "-t", "0.002", "--capture", str(path)], FakeSerial(raw))
            self.assertEqual(code, 0)
            self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
