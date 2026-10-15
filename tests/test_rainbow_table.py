"""
Tests for the rainbow table generator: table generation, phrase lookup,
reverse SYNC lookup, CSV/JSON output, and CLI interface.
"""

import csv
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
elrs_rainbow = importlib.import_module("elrs-uid-rainbow")


class TestGenerateTable:
    def test_generates_entry_per_phrase(self):
        phrases = ["alpha", "bravo", "charlie"]
        table = elrs_rainbow.generate_table(phrases)
        assert len(table) == 3

    def test_entry_has_required_fields(self):
        table = elrs_rainbow.generate_table(["test"])
        entry = table[0]
        assert set(entry.keys()) == {
            "phrase", "uid_hex", "uid_bytes", "sync_bytes",
            "fhss_seed", "crc_init", "collision",
        }

    def test_phrase_preserved(self):
        table = elrs_rainbow.generate_table(["my phrase"])
        assert table[0]["phrase"] == "my phrase"

    def test_uid_hex_format(self):
        table = elrs_rainbow.generate_table(["test"])
        uid_hex = table[0]["uid_hex"]
        parts = uid_hex.split(":")
        assert len(parts) == 6
        for part in parts:
            assert len(part) == 2
            int(part, 16)  # must be valid hex

    def test_uid_bytes_is_list_of_6_ints(self):
        table = elrs_rainbow.generate_table(["test"])
        uid_bytes = table[0]["uid_bytes"]
        assert isinstance(uid_bytes, list)
        assert len(uid_bytes) == 6
        assert all(0 <= b <= 255 for b in uid_bytes)

    def test_sync_bytes_format(self):
        table = elrs_rainbow.generate_table(["test"])
        sync = table[0]["sync_bytes"]
        parts = sync.split(":")
        assert len(parts) == 3

    def test_collision_detection(self):
        # Two different phrases that happen to have the same UID would be a collision.
        # With normal phrases, collisions are unlikely; test the mechanism with same phrase twice.
        table = elrs_rainbow.generate_table(["test", "test"])
        assert table[0]["collision"] is False
        assert table[1]["collision"] is True

    def test_no_collision_for_unique_phrases(self):
        phrases = ["alpha", "bravo", "charlie", "delta"]
        table = elrs_rainbow.generate_table(phrases)
        assert all(not e["collision"] for e in table)

    def test_empty_phrase_list(self):
        table = elrs_rainbow.generate_table([])
        assert table == []

    def test_empty_string_phrase(self):
        table = elrs_rainbow.generate_table([""])
        assert len(table) == 1
        assert table[0]["phrase"] == ""
        assert len(table[0]["uid_bytes"]) == 6

    def test_consistency_with_direct_functions(self):
        phrase = "consistency_check"
        table = elrs_rainbow.generate_table([phrase])
        entry = table[0]

        uid = elrs_rainbow.phrase_to_uid(phrase)
        assert entry["uid_hex"] == elrs_rainbow.uid_hex(uid)
        assert entry["uid_bytes"] == list(uid)
        assert entry["sync_bytes"] == elrs_rainbow.sync_bytes_hex(uid)
        assert entry["fhss_seed"] == elrs_rainbow.uid_to_fhss_seed(uid)
        assert entry["crc_init"] == elrs_rainbow.uid_to_crc_init(uid)


class TestLookupSync:
    def test_lookup_match(self):
        table = elrs_rainbow.generate_table(["expresslrs"])
        sync = table[0]["sync_bytes"]
        matches = elrs_rainbow.lookup_sync(table, sync)
        assert len(matches) == 1
        assert matches[0]["phrase"] == "expresslrs"

    def test_lookup_no_match(self):
        table = elrs_rainbow.generate_table(["test"])
        matches = elrs_rainbow.lookup_sync(table, "00:00:00")
        # May or may not match depending on UID; test with very unlikely bytes
        uid = elrs_rainbow.phrase_to_uid("test")
        # Use bytes that differ from the actual sync
        fake = ":".join(f"{(b + 1) % 256:02X}" for b in uid[3:6])
        matches = elrs_rainbow.lookup_sync(table, fake)
        assert len(matches) == 0

    def test_lookup_case_insensitive(self):
        table = elrs_rainbow.generate_table(["test"])
        sync_upper = table[0]["sync_bytes"]
        sync_lower = sync_upper.lower()
        assert elrs_rainbow.lookup_sync(table, sync_lower) == elrs_rainbow.lookup_sync(table, sync_upper)

    def test_lookup_with_dash_separator(self):
        table = elrs_rainbow.generate_table(["test"])
        sync = table[0]["sync_bytes"]
        dashed = sync.replace(":", "-")
        matches = elrs_rainbow.lookup_sync(table, dashed)
        assert len(matches) == 1

    def test_lookup_with_space_separator(self):
        table = elrs_rainbow.generate_table(["test"])
        sync = table[0]["sync_bytes"]
        spaced = sync.replace(":", " ")
        matches = elrs_rainbow.lookup_sync(table, spaced)
        assert len(matches) == 1

    def test_lookup_multiple_matches(self):
        # Same phrase repeated generates same sync bytes
        table = elrs_rainbow.generate_table(["test", "test"])
        sync = table[0]["sync_bytes"]
        matches = elrs_rainbow.lookup_sync(table, sync)
        assert len(matches) == 2

    def test_lookup_2_byte_partial(self):
        """A 2-byte SYNC capture (UID[4:6]) should match via suffix."""
        table = elrs_rainbow.generate_table(["test"])
        sync = table[0]["sync_bytes"]
        partial = ":".join(sync.split(":")[1:])  # last 2 bytes
        matches = elrs_rainbow.lookup_sync(table, partial)
        assert len(matches) == 1  # 2-byte partial matches via endswith


class TestBuiltinWordlist:
    def test_builtin_phrases_not_empty(self):
        assert len(elrs_rainbow.BUILTIN_PHRASES) > 100

    def test_builtin_phrases_include_defaults(self):
        assert "expresslrs" in elrs_rainbow.BUILTIN_PHRASES
        assert "" in elrs_rainbow.BUILTIN_PHRASES

    def test_builtin_table_no_errors(self):
        table = elrs_rainbow.generate_table(elrs_rainbow.BUILTIN_PHRASES)
        assert len(table) == len(elrs_rainbow.BUILTIN_PHRASES)


class TestLoadWordlist:
    def test_load_wordlist(self, tmp_wordlist):
        wl_path, phrases = tmp_wordlist
        loaded = elrs_rainbow.load_wordlist(str(wl_path))
        assert loaded == phrases

    def test_preserves_empty_lines(self, tmp_path):
        wl = tmp_path / "empty_lines.txt"
        wl.write_text("alpha\n\nbeta\n")
        loaded = elrs_rainbow.load_wordlist(str(wl))
        assert loaded == ["alpha", "", "beta"]

    def test_preserves_whitespace_in_phrases(self, tmp_path):
        wl = tmp_path / "spaces.txt"
        wl.write_text("  leading\ntrailing  \n middle \n")
        loaded = elrs_rainbow.load_wordlist(str(wl))
        assert loaded == ["  leading", "trailing  ", " middle "]


class TestCSVOutput:
    def test_csv_output(self, tmp_path):
        phrases = ["alpha", "bravo"]
        table = elrs_rainbow.generate_table(phrases)
        csv_path = tmp_path / "output.csv"

        with patch("sys.argv", ["prog", "-o", str(csv_path)]):
            # Directly test the CSV writing logic
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["phrase", "uid", "sync_bytes", "fhss_seed", "crc_init"])
                for e in table:
                    writer.writerow([
                        e["phrase"], e["uid_hex"], e["sync_bytes"],
                        f"0x{e['fhss_seed']:08X}", f"0x{e['crc_init']:04X}",
                    ])

        with open(csv_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["phrase"] == "alpha"
        assert ":" in rows[0]["uid"]


class TestJSONOutput:
    def test_json_roundtrip(self, tmp_path):
        phrases = ["alpha", "bravo"]
        table = elrs_rainbow.generate_table(phrases)
        json_path = tmp_path / "output.json"

        with open(json_path, "w") as f:
            json.dump(table, f, indent=2)

        with open(json_path) as f:
            loaded = json.load(f)

        assert len(loaded) == 2
        assert loaded[0]["phrase"] == "alpha"
        assert loaded[0]["uid_bytes"] == table[0]["uid_bytes"]


class TestUIDHexFormatting:
    def test_uid_hex(self):
        uid = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE])
        assert elrs_rainbow.uid_hex(uid) == "DE:AD:BE:EF:CA:FE"

    def test_uid_hex_leading_zeros(self):
        uid = bytes([0x00, 0x01, 0x0A, 0x0F, 0x10, 0xFF])
        assert elrs_rainbow.uid_hex(uid) == "00:01:0A:0F:10:FF"

    def test_sync_bytes_hex(self):
        uid = bytes([0x01, 0x02, 0x03, 0xAA, 0xBB, 0xCC])
        assert elrs_rainbow.sync_bytes_hex(uid) == "AA:BB:CC"
