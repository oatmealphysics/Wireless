"""Validate rainbow table JSON schema and that all loader patterns index entries.

Catches P2-7-class regressions: a loader that reads wrong field names silently
indexes zero entries from a 723-entry table.  Each test class mimics the exact
field-access pattern of a specific loader so a schema drift is caught immediately.
"""

import json
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

TABLE_PATH = TOOLS / "elrs-rainbow-table.json"


class TestRainbowTableSchema(unittest.TestCase):
    """Every entry in the JSON has the required fields with correct types."""

    @classmethod
    def setUpClass(cls):
        if not TABLE_PATH.exists():
            raise unittest.SkipTest("rainbow table not present")
        cls.entries = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

    def test_is_nonempty_list(self):
        self.assertIsInstance(self.entries, list)
        self.assertGreater(len(self.entries), 0)

    def test_required_fields_present(self):
        required = {"phrase", "uid_hex", "uid_bytes", "sync_bytes",
                    "fhss_seed", "crc_init", "collision"}
        for i, entry in enumerate(self.entries):
            missing = required - set(entry.keys())
            self.assertFalse(missing, f"Entry {i} missing: {missing}")

    def test_uid_hex_format(self):
        for i, entry in enumerate(self.entries):
            uid_hex = entry["uid_hex"]
            parts = uid_hex.split(":")
            self.assertEqual(len(parts), 6, f"Entry {i}: uid_hex should have 6 colon-separated bytes")
            for part in parts:
                self.assertEqual(len(part), 2, f"Entry {i}: each byte should be 2 hex chars")
                int(part, 16)

    def test_uid_bytes_matches_uid_hex(self):
        for i, entry in enumerate(self.entries):
            uid_from_hex = bytes.fromhex(entry["uid_hex"].replace(":", ""))
            uid_from_bytes = bytes(entry["uid_bytes"])
            self.assertEqual(uid_from_hex, uid_from_bytes,
                             f"Entry {i}: uid_hex and uid_bytes disagree")

    def test_sync_bytes_matches_uid_tail(self):
        for i, entry in enumerate(self.entries):
            uid = bytes(entry["uid_bytes"])
            expected_sync = f"{uid[3]:02X}:{uid[4]:02X}:{uid[5]:02X}"
            self.assertEqual(entry["sync_bytes"].upper(), expected_sync,
                             f"Entry {i}: sync_bytes should be UID[3:6]")

    def test_fhss_seed_and_crc_init_are_ints(self):
        for i, entry in enumerate(self.entries):
            self.assertIsInstance(entry["fhss_seed"], int, f"Entry {i}")
            self.assertIsInstance(entry["crc_init"], int, f"Entry {i}")


class TestAnalyzerLoaderPattern(unittest.TestCase):
    """Mimics elrs-analyzer.py load_rainbow_table: keys on uid_hex → uid[3:6]."""

    @classmethod
    def setUpClass(cls):
        if not TABLE_PATH.exists():
            raise unittest.SkipTest("rainbow table not present")
        cls.entries = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

    def test_all_entries_indexed(self):
        table = {}
        for entry in self.entries:
            uid_key = entry.get("uid_hex") or entry.get("uid", "")
            if not uid_key:
                continue
            uid_bytes = bytes.fromhex(uid_key.replace(":", ""))
            key = uid_bytes[3:6].hex()
            table[key] = entry
        self.assertGreater(len(table), 0,
                           "Analyzer loader should index entries via uid_hex")
        # May be fewer than len(entries) due to uid[3:6] collisions
        self.assertGreaterEqual(len(table), len(self.entries) * 0.9)


class TestSyncCaptureLoaderPattern(unittest.TestCase):
    """Mimics elrs-sync-capture.py load_rainbow_table: keys on uid4:uid5."""

    @classmethod
    def setUpClass(cls):
        if not TABLE_PATH.exists():
            raise unittest.SkipTest("rainbow table not present")
        cls.entries = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

    def test_all_entries_indexed(self):
        table = {}
        for entry in self.entries:
            uid_hex = entry.get("uid_hex") or entry.get("uid", "")
            if not uid_hex:
                continue
            uid_bytes = bytes.fromhex(uid_hex.replace(":", ""))
            if len(uid_bytes) < 6:
                continue
            key = f"{uid_bytes[4]:02x}{uid_bytes[5]:02x}"
            table.setdefault(key, []).append(entry)
        total = sum(len(v) for v in table.values())
        self.assertEqual(total, len(self.entries),
                         "Sync capture loader should index every entry")


class TestPassiveTelemetryLoaderPattern(unittest.TestCase):
    """Mimics passive-telemetry-interceptor.py: keys on sync_bytes."""

    @classmethod
    def setUpClass(cls):
        if not TABLE_PATH.exists():
            raise unittest.SkipTest("rainbow table not present")
        cls.entries = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

    def test_all_entries_indexed(self):
        table = {}
        for entry in self.entries:
            key = entry.get("sync_bytes", "")
            if key:
                table[key.upper()] = entry
        self.assertEqual(len(table), len(self.entries),
                         "Passive telemetry loader should index every entry via sync_bytes")


class TestHijackPocLoaderPattern(unittest.TestCase):
    """Mimics msp-elrs-hijack-poc.py rainbow_lookup: reads uid_hex, strips colons."""

    @classmethod
    def setUpClass(cls):
        if not TABLE_PATH.exists():
            raise unittest.SkipTest("rainbow table not present")
        cls.entries = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

    def test_known_entry_matches(self):
        first = self.entries[0]
        target_uid = bytes(first["uid_bytes"])
        partial = {
            "uid3": target_uid[3],
            "uid4": target_uid[4],
            "uid5_upper2": (target_uid[5] >> 6) & 0x03,
        }
        found = None
        for entry in self.entries:
            uid_hex = entry.get("uid_hex", "") or entry.get("uid", "")
            uid_hex_clean = uid_hex.replace(":", "")
            if len(uid_hex_clean) != 12:
                continue
            uid = bytes.fromhex(uid_hex_clean)
            if (uid[3] == partial["uid3"] and
                uid[4] == partial["uid4"] and
                (uid[5] & 0xC0) == (partial["uid5_upper2"] << 6)):
                found = uid
                break
        self.assertIsNotNone(found, "Should find entry by partial UID match")
        self.assertEqual(found, target_uid)


class TestAttackProfileLoaderPattern(unittest.TestCase):
    """Mimics elrs-attack-profile.py uid_from_rainbow_lookup: reads uid_bytes."""

    @classmethod
    def setUpClass(cls):
        if not TABLE_PATH.exists():
            raise unittest.SkipTest("rainbow table not present")
        cls.entries = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

    def test_known_entry_matches(self):
        first = self.entries[0]
        target_uid = bytes(first["uid_bytes"])
        uid4, uid5 = target_uid[4], target_uid[5]
        matches = []
        for entry in self.entries:
            uid_bytes = entry.get("uid_bytes") or entry.get("uid", [])
            if len(uid_bytes) >= 6 and uid_bytes[4] == uid4 and uid_bytes[5] == uid5:
                matches.append(entry)
        self.assertGreater(len(matches), 0,
                           "Attack profile loader should find entries via uid_bytes")


class TestChainDryRunLoaderPattern(unittest.TestCase):
    """Mimics chain1-dry-run.py and combined_attack_demo.py: keys on sync_bytes."""

    @classmethod
    def setUpClass(cls):
        if not TABLE_PATH.exists():
            raise unittest.SkipTest("rainbow table not present")
        cls.entries = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

    def test_known_entry_matches(self):
        first = self.entries[0]
        uid = bytes(first["uid_bytes"])
        sync_hex = f"{uid[3]:02X}:{uid[4]:02X}:{uid[5]:02X}"
        found = None
        for entry in self.entries:
            if entry.get("sync_bytes", "").upper() == sync_hex.upper():
                found = entry
                break
        self.assertIsNotNone(found, "Chain dry-run loader should find entries via sync_bytes")


if __name__ == "__main__":
    unittest.main()
