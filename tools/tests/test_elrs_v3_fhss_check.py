"""Synthetic, non-secret Betaflight 4.4.2 v3 FHSS and privacy checks."""

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout


TOOL = Path(__file__).resolve().parents[1] / "elrs-v3-fhss-check.py"
SPEC = importlib.util.spec_from_file_location("elrs_v3_fhss_check", TOOL)
fhss = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fhss)


def synthetic_sync(uid: bytes, index: int) -> bytes:
    """Build a fixture with a literal bitwise CRC, independent of the tool."""
    body = bytes([index, 9, 0x27, uid[3], uid[4], uid[5]])
    crc = (((uid[4] << 8) | uid[5]) ^ 3) & 0x3FFF
    for value in bytes([2]) + body:
        crc ^= value << 6
        for _ in range(8):
            crc = ((crc << 1) ^ (0x2E57 if crc & 0x2000 else 0)) & 0x3FFF
    return bytes([2 | ((crc >> 8) << 2)]) + body + bytes([crc & 0xFF])


class V3FhssTests(unittest.TestCase):
    def setUp(self):
        self.uid = bytes.fromhex("12 34 56 78 9a bc")  # synthetic only
        self.sequence = fhss.build_v3_sequence(self.uid)

    def test_version_pinned_sequence_vector_and_blocks(self):
        # Fixed vector from an independent direct port of the source's LCG
        # and swap loop, not from this Python function's output.
        self.assertEqual(self.sequence[:20], [
            41, 75, 70, 62, 46, 13, 29, 20, 17, 58,
            40, 8, 22, 38, 67, 57, 9, 25, 45, 24])
        self.assertEqual(len(self.sequence), 240)
        for start in (0, 80, 160):
            self.assertEqual(self.sequence[start], 41)
            self.assertEqual(set(self.sequence[start:start + 80]), set(range(80)))

    def test_measured_frequency_mapping_and_rejection(self):
        self.assertEqual(fhss.observed_channel(2_441_378_800, 100_000), 41)
        self.assertIsNone(fhss.observed_channel(2_441_250_000, 100_000))
        self.assertIsNone(fhss.observed_channel(2_500_000_000, 100_000))
        with self.assertRaises(ValueError):
            fhss.observed_channel("2441400000", 100_000)

    def test_crc_uid_then_channel_match(self):
        raw = synthetic_sync(self.uid, 0)
        item = {"packet_bytes_hex": raw.hex(), "channel_hz": 2_441_378_800}
        self.assertEqual(fhss.evaluate_record(item, self.uid, self.sequence, 100_000),
                         "channel_match")
        item["channel_hz"] = 2_442_400_000
        self.assertEqual(fhss.evaluate_record(item, self.uid, self.sequence, 100_000),
                         "channel_mismatch")
        del item["channel_hz"]
        self.assertEqual(fhss.evaluate_record(item, self.uid, self.sequence, 100_000),
                         "channel_missing")
        item["packet_bytes_hex"] = (raw[:-1] + bytes([raw[-1] ^ 1])).hex()
        self.assertEqual(fhss.evaluate_record(item, self.uid, self.sequence, 100_000),
                         "crc_fail")

    def test_uid_seed_byte_not_covered_by_crc_changes_hop(self):
        raw = synthetic_sync(self.uid, 1)
        item = {"packet_bytes_hex": raw.hex(), "channel_hz": 2_475_400_000}
        self.assertEqual(fhss.evaluate_record(item, self.uid, self.sequence, 100_000),
                         "channel_match")
        different_uid = bytes.fromhex("12 34 57 78 9a bc")
        different_sequence = fhss.build_v3_sequence(different_uid)
        # UID[2] changes the FHSS seed but not this frame's CRC initializer
        # or the SYNC-visible UID[3..5] identity check.
        self.assertEqual(fhss.evaluate_record(item, different_uid,
                                              different_sequence, 100_000),
                         "channel_mismatch")

    def test_invalid_index_and_private_aggregate_output(self):
        raw = synthetic_sync(self.uid, 240)
        item = {"packet_bytes_hex": raw.hex(), "channel_hz": 2_441_400_000}
        self.assertEqual(fhss.evaluate_record(item, self.uid, self.sequence, 100_000),
                         "index_out_of_range")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path, uid_path = root / "candidates.jsonl", root / "uid.hex"
            input_path.write_text(json.dumps(item) + "\n", encoding="utf-8")
            uid_path.write_text(self.uid.hex(), encoding="ascii")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = fhss.main(["--input", str(input_path),
                                    "--uid-file", str(uid_path)])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(out.getvalue())["index_out_of_range"], 1)
            self.assertNotIn(raw.hex(), out.getvalue() + err.getvalue())
            self.assertNotIn(self.uid.hex(), out.getvalue() + err.getvalue())
            self.assertNotIn(str(input_path), out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
