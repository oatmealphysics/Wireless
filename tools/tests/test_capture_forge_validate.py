"""Tests for the offline ELRS capture frame/CRC consistency checker."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "exploit_chains"))

import runpy

_VALIDATE = runpy.run_path(str(TOOLS / "elrs-capture-forge-validate.py"))
phrase_to_uid = _VALIDATE["phrase_to_uid"]
extract_sync_uid_suffix = _VALIDATE["extract_sync_uid_suffix"]
rainbow_lookup = _VALIDATE["rainbow_lookup"]
validate_forge_pipeline = _VALIDATE["validate_forge_pipeline"]
decode_all_with_bytes = _VALIDATE["decode_all_with_bytes"]
main = _VALIDATE["main"]

_LI = runpy.run_path(str(TOOLS / "elrs-li-offline.py"))
read_rows = _LI["read_rows"]

from elrs_core.v3_compat import (
    v3_build_sync, v3_generate_crc, v3_uid_crc_init, v3_validate_crc,
)


class PhraseToUidTests(unittest.TestCase):
    def test_known_phrase(self):
        uid = phrase_to_uid("default ExpressLRS binding phrase")
        self.assertEqual(len(uid), 6)
        self.assertEqual(uid.hex(), "0ab70c7c3803")

    def test_different_phrases_differ(self):
        self.assertNotEqual(phrase_to_uid("abc"), phrase_to_uid("def"))


class ForgeValidationTests(unittest.TestCase):
    """Test that forged packets match the v3 CRC algorithm."""

    def _make_sync_frame(self, uid, fhss_index=0, nonce=3, rate_index=7):
        return v3_build_sync(uid, fhss_index, nonce, rate_index)

    def _make_rc_frame(self, uid):
        raw = bytearray([0x00, 0x00, 0x01, 0x80, 0x00, 0x00, 0x00, 0x00])
        v3_generate_crc(raw, uid, switch_mode="hybrid")
        return bytes(raw)

    def test_forge_sync_roundtrip(self):
        uid = phrase_to_uid("expresslrs")
        frame = self._make_sync_frame(uid)
        self.assertTrue(v3_validate_crc(frame, uid))

    def test_forge_rc_roundtrip(self):
        uid = phrase_to_uid("expresslrs")
        frame = self._make_rc_frame(uid)
        self.assertTrue(v3_validate_crc(frame, uid, switch_mode="hybrid"))

    def test_wrong_uid_fails_validation(self):
        uid1 = phrase_to_uid("expresslrs")
        uid2 = phrase_to_uid("different")
        frame = self._make_sync_frame(uid1)
        self.assertFalse(v3_validate_crc(frame, uid2))

    def test_validate_pipeline_with_synthetic_frames(self):
        uid = phrase_to_uid("testphrase")
        frames = []

        for nonce in range(5):
            sync = self._make_sync_frame(uid, fhss_index=0, nonce=nonce)
            frames.append({
                "source": "test", "line": nonce + 1,
                "time_s": nonce * 0.007,
                "type": "SYNC", "packet_bytes": sync,
                "corrected": 0, "offset": 0,
            })

        for i in range(10):
            rc = self._make_rc_frame(uid)
            frames.append({
                "source": "test", "line": 100 + i,
                "time_s": 1.0 + i * 0.007,
                "type": "RC", "packet_bytes": rc,
                "corrected": 0, "offset": 0,
            })

        report = validate_forge_pipeline(uid, frames)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["sync_crc_recompute_match"], 5)
        self.assertEqual(report["sync_crc_recompute_mismatch"], 0)
        self.assertEqual(report["rc_crc_recompute_match"], 10)
        self.assertEqual(report["rc_crc_recompute_mismatch"], 0)
        self.assertEqual(report["fhss_indices_observed"], [0])
        self.assertEqual(report["derived_channel_predictions"][0]["fhss_index"], 0)
        self.assertNotIn("fhss_index_validated", report)
        self.assertNotIn("sync_forge_match", report)
        self.assertIn("no observed RF-channel comparison",
                      report["evidence_limitations"])


class RainbowLookupTests(unittest.TestCase):
    def test_known_entry(self):
        table_path = TOOLS / "elrs-rainbow-table.json"
        if not table_path.exists():
            self.skipTest("rainbow table not present")
        matches = rainbow_lookup(0x7C, 0x38, 0x03, table_path)
        self.assertTrue(len(matches) >= 1)
        self.assertEqual(matches[0]["phrase"], "default ExpressLRS binding phrase")

    def test_no_match(self):
        table_path = TOOLS / "elrs-rainbow-table.json"
        if not table_path.exists():
            self.skipTest("rainbow table not present")
        matches = rainbow_lookup(0xFF, 0xFF, 0xFF, table_path)
        self.assertEqual(len(matches), 0)


class IntegrationWithCapturesTests(unittest.TestCase):
    """Test against real committed capture files if present."""

    RANKED_FILES = [
        Path("captures/oct15-ranked-2026-10-16.jsonl"),
        Path("captures/tinyhawk-rc-retry-full-phy-2026-10-16.jsonl"),
    ]

    @classmethod
    def setUpClass(cls):
        cls.repo = TOOLS.parent
        for f in cls.RANKED_FILES:
            if not (cls.repo / f).exists():
                raise unittest.SkipTest("ranked JSONL capture files not present")

    def test_uid_suffix_extraction(self):
        paths = [self.repo / f for f in self.RANKED_FILES]
        rows = read_rows(paths)
        suffix = extract_sync_uid_suffix(rows)
        self.assertIsNotNone(suffix)
        self.assertEqual(len(suffix), 3)

    def test_full_validation_passes(self):
        paths = [self.repo / f for f in self.RANKED_FILES]
        rows = read_rows(paths)
        suffix = extract_sync_uid_suffix(rows)
        self.assertIsNotNone(suffix)
        uid = bytes((0, 0, 0, *suffix))
        decoded = decode_all_with_bytes(rows, uid)
        self.assertGreater(len(decoded), 0)
        report = validate_forge_pipeline(uid, decoded)
        self.assertEqual(report["verdict"], "PASS")
        self.assertGreater(report["sync_crc_recompute_match"], 0)
        self.assertGreater(report["rc_crc_recompute_match"], 0)
        self.assertEqual(report["sync_crc_recompute_mismatch"], 0)
        self.assertEqual(report["rc_crc_recompute_mismatch"], 0)

    def test_cli_rejects_non_six_byte_uid_file(self):
        ranked = self.repo / self.RANKED_FILES[0]
        with tempfile.TemporaryDirectory() as tmp:
            uid_file = Path(tmp) / "uid.hex"
            uid_file.write_text("0011223344", encoding="ascii")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = main([str(ranked), "--uid-file", str(uid_file)])
        self.assertEqual(rc, 2)
        self.assertIn("exactly six UID bytes", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
