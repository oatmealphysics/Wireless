"""Synthetic tests for the read-only TinyHawk MSP observer."""

import importlib.util
import io
from pathlib import Path
import struct
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "tinyhawk-msp-rc-observe.py"
SPEC = importlib.util.spec_from_file_location("tinyhawk_msp_rc_observe", TOOL)
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


def reply(command, payload=b"", direction=b">"):
    check = len(payload) ^ command
    for value in payload:
        check ^= value
    return b"$M" + direction + bytes((len(payload), command)) + payload + bytes((check,))


class FakePort:
    def __init__(self, responses):
        self.stream = io.BytesIO(responses)
        self.writes = []

    def read(self, count):
        return self.stream.read(count)

    def write(self, data):
        self.writes.append(data)


class MspObserverTests(unittest.TestCase):
    def test_query_and_rc_reply(self):
        self.assertEqual(observer.msp_query(105), b"$M<\x00ii")
        channels = (1000, 1500, 2000, 1200)
        port = FakePort(b"noise" + reply(105, struct.pack("<4H", *channels)))
        payload = observer.read_reply(port, 105, observer.time.monotonic() + 0.1)
        self.assertEqual(observer.decode_rc(payload), list(channels))

    def test_bad_checksum_does_not_pass(self):
        broken = bytearray(reply(105, b"12345678"))
        broken[-1] ^= 1
        port = FakePort(bytes(broken) + reply(105, b"abcdefgh"))
        self.assertEqual(observer.read_reply(port, 105, observer.time.monotonic() + 0.1),
                         b"abcdefgh")

    def test_identity_mismatch_stops(self):
        responses = reply(1, b"\x00\x01\x2d") + reply(2, b"NOPE")
        with self.assertRaises(ValueError):
            observer.identify(FakePort(responses))

    def test_long_board_info_reply_keeps_alignment(self):
        responses = (reply(1, b"\x00\x01\x2d") + reply(2, b"BTFL")
                     + reply(3, b"\x04\x04\x02")
                     + reply(4, b"S411" + bytes(96))
                     + reply(10, b"TH III Plus"))
        port = FakePort(responses)
        observer.identify(port)
        self.assertEqual(len(port.writes), 5)

    def test_dry_run_never_imports_serial(self):
        err = io.StringIO()
        with patch.dict("sys.modules", {"serial": None}), redirect_stderr(err):
            self.assertEqual(observer.main(["--port", "COM5"]), 0)
        self.assertIn("Dry run", err.getvalue())

    def test_invalid_channel_count(self):
        for payload in (b"", b"123", b"a" * 66):
            with self.assertRaises(ValueError):
                observer.decode_rc(payload)


if __name__ == "__main__":
    unittest.main()
