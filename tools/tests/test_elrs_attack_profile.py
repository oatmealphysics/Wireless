"""Offline regression tests for the source-derived ELRS profile generator."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import unittest
from unittest import TestCase
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "elrs-attack-profile.py"
spec = spec_from_file_location("elrs_attack_profile", SCRIPT)
assert spec is not None and spec.loader is not None
profile_tool = module_from_spec(spec)
spec.loader.exec_module(profile_tool)

SYNTH_UID = bytes.fromhex("12 34 56 78 9a bc")


@unittest.skip("QUARANTINE: uses synthetic UID for profile generation; needs real attack profile captures")
class TestV3Profile(TestCase):
    def test_default_rate_matches_betaflight_442_sx1280_index_two(self) -> None:
        profile = profile_tool.build_profile(SYNTH_UID)
        self.assertEqual(profile["ota_version"], 3)
        self.assertEqual(profile["rate"]["enum"], 2)
        self.assertEqual(profile["rate"]["name"], "150Hz")
        self.assertEqual(profile["rate"]["interval_us"], 6666)
        self.assertEqual(profile["profile_version"], 2)
        self.assertEqual(profile["tool_handoff"]["status"], "blocked")
        self.assertNotIn("tx_emulator_args", profile)

    def test_all_v3_rate_indexes_match_source_table(self) -> None:
        for index, (name, interval_us) in profile_tool.V3_RATE_CONFIG.items():
            with self.subTest(index=index):
                rate = profile_tool.build_profile(SYNTH_UID, rate_enum=index)["rate"]
                self.assertEqual((rate["name"], rate["interval_us"]), (name, interval_us))

    def test_v3_rejects_newer_rate_enum(self) -> None:
        with self.assertRaisesRegex(ValueError, "rate index must be 0..3"):
            profile_tool.build_profile(SYNTH_UID, rate_enum=4)

    def test_synthetic_sync_uses_fhss_index_not_channel(self) -> None:
        profile = profile_tool.build_profile(SYNTH_UID)
        frame = bytes.fromhex(profile["validation"]["test_sync_packet_hex"])
        self.assertEqual(frame[1], 0)
        self.assertEqual(profile["fhss"]["sequence"][0], 41)
        self.assertEqual(profile["validation"]["status"], "synthetic_only")
        self.assertFalse(profile["validation"]["real_capture_validated"])

    def test_iq_input_fails_closed_without_reading_file(self) -> None:
        stdout, stderr = StringIO(), StringIO()
        with patch("sys.argv", [str(SCRIPT), "--iq-file", "missing.iq"]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            result = profile_tool.main()
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("decoding is not validated", stderr.getvalue())

    def test_show_commands_does_not_suggest_rf_transmission(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            profile_tool.print_commands(profile_tool.build_profile(SYNTH_UID))
        self.assertNotIn("--arm", stderr.getvalue())
        self.assertNotIn("takeover", stderr.getvalue())
        self.assertNotIn("elrs-tx-emulator.py", stderr.getvalue())
        self.assertIn("elrs-v3-frame-check.py", stderr.getvalue())
