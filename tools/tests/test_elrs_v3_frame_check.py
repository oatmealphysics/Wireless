"""Synthetic, non-secret checks against Betaflight 4.4.2 CRC table semantics."""

import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr


TOOL = Path(__file__).resolve().parents[1] / "elrs-v3-frame-check.py"
SPEC = importlib.util.spec_from_file_location("elrs_v3_frame_check", TOOL)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def source_table_crc(data: bytes, initial: int) -> int:
    """Independent literal table calculation from expresslrs_common.c."""
    table = []
    for index in range(256):
        crc = index << 6
        for _ in range(8):
            crc = (crc << 1) ^ (0x2E57 if crc & 0x2000 else 0)
        table.append(crc & 0xFFFF)
    crc = initial
    for value in data:
        crc = (crc << 8) ^ table[((crc >> 6) ^ value) & 0xFF]
    return crc & 0x3FFF


def wire_frame(packet_type: int, body: bytes, crc_first_byte: int,
               initial: int) -> bytes:
    assert len(body) == 6
    computed = source_table_crc(bytes([crc_first_byte]) + body, initial)
    return bytes([packet_type | ((computed >> 8) << 2)]) + body + bytes([computed & 0xFF])


class V3FrameTests(unittest.TestCase):
    def setUp(self):
        self.uid = bytes.fromhex("12 34 56 78 9a bc")  # synthetic fixture only
        self.initial = checker.uid_crc_init(self.uid)

    def test_bitwise_crc_matches_source_table(self):
        for initial in (0, 3, 0x3FFF, 0x9ABF):
            for body in (bytes(7), bytes(range(7)), bytes.fromhex("02 ff 03 21 78 9a bc")):
                self.assertEqual(checker.crc14(body, initial),
                                 source_table_crc(body, initial))

    def test_sync_crc_and_identity_basis(self):
        flags = (2 << 4) | (3 << 1) | 1
        body = bytes([41, 9, flags, self.uid[3], self.uid[4], self.uid[5]])
        raw = wire_frame(2, body, 2, self.initial)
        known = checker.check_frame(raw, uid=self.uid)
        self.assertEqual(known["crc"], "pass")
        self.assertEqual(known["crc_basis"], "provided_uid")
        self.assertTrue(known["uid_18bit_match"])
        self.assertEqual(known["fields"]["rate_index"], 2)
        self.assertEqual(known["fields"]["switch_mode"], "hybrid")
        self.assertEqual(known["fields"]["fhss_index"], 41)
        self.assertEqual(checker.check_frame(raw)["crc_basis"],
                         "self_derived_sync_suffix")
        self.assertEqual(checker.check_frame(raw)["crc"], "pass")
        self.assertEqual(checker.check_frame(raw, uid=bytes.fromhex("12 34 56 78 9a bd"))["crc"],
                         "fail")
        altered = bytearray(raw)
        altered[1] ^= 1
        self.assertEqual(checker.check_frame(bytes(altered), uid=self.uid)["crc"], "fail")
        self.assertNotIn(self.uid.hex(), str(known))  # no UID value in report

    def test_wide_rc_requires_correct_nonce_slot(self):
        body = bytes.fromhex("10 20 30 40 50 60")
        nonce = 5
        crc_first = ((nonce % 4) + 1) << 2
        raw = wire_frame(0, body, crc_first, self.initial)
        self.assertEqual(checker.check_frame(raw, uid=self.uid,
                                             switch_mode="wide", nonce=nonce)["crc"], "pass")
        self.assertEqual(checker.check_frame(raw, uid=self.uid,
                                             switch_mode="wide", nonce=nonce + 1)["crc"], "fail")
        self.assertEqual(checker.check_frame(raw, uid=self.uid,
                                             switch_mode="wide")["crc_basis"], "missing_wide_nonce")
        self.assertEqual(checker.check_frame(raw, uid=self.uid,
                                             switch_mode="hybrid")["crc"], "fail")

    def test_hybrid_rc_and_missing_context(self):
        raw = wire_frame(0, bytes.fromhex("aa bb cc dd ee ff"), 0, self.initial)
        self.assertEqual(checker.check_frame(raw, uid=self.uid,
                                             switch_mode="hybrid", nonce=201)["crc"], "pass")
        self.assertEqual(checker.check_frame(raw, uid=self.uid)["crc"], "unverifiable")
        self.assertEqual(checker.check_frame(raw)["crc"], "unverifiable")
        with self.assertRaises(ValueError):
            checker.check_frame(raw[:-1], uid=self.uid)

    def test_cli_verdict_omits_private_input_values(self):
        body = bytes([41, 9, 0x27, self.uid[3], self.uid[4], self.uid[5]])
        raw = wire_frame(2, body, 2, self.initial)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.jsonl"
            secret = root / "uid.hex"
            candidates.write_text('{"packet_bytes_hex":"' + raw.hex() + '"}\n',
                                  encoding="utf-8")
            secret.write_text(self.uid.hex(), encoding="ascii")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = checker.main(["--input", str(candidates),
                                       "--uid-file", str(secret), "--json"])
            self.assertEqual(status, 0)
            self.assertIn('"crc": "pass"', out.getvalue())
            self.assertNotIn(raw.hex(), out.getvalue() + err.getvalue())
            self.assertNotIn(self.uid.hex(), out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
