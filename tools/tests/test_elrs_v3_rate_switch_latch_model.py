"""Tests for the offline Betaflight 4.4.2 ELRS rate-switch model."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import unittest


TOOL = Path(__file__).resolve().parents[1] / "elrs-v3-rate-switch-latch-model.py"
SPEC = importlib.util.spec_from_file_location("elrs_v3_rate_switch_latch_model", TOOL)
model = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model)


class RateSwitchLatchModelTests(unittest.TestCase):
    def test_source_rate_table_and_integer_cycle_intervals(self):
        self.assertEqual(
            {code: model.cycle_interval_ms(code) for code in (4, 6, 7, 9)},
            {4: 704, 6: 1408, 7: 2346, 9: 3520},
        )

    def test_different_rate_forces_fixed_channel_disconnect(self):
        state = model.process_sync(model.connected_state(), rate_code=9)
        self.assertTrue(state["sync_accepted"])
        self.assertTrue(state["forced_rate_change"])
        self.assertEqual(state["connection"], "disconnected")
        self.assertFalse(state["timer_running"])
        self.assertEqual(state["current_rate_code"], 9)
        self.assertEqual(state["radio_channel"], model.INITIAL_CHANNEL)
        self.assertTrue(state["lock_rf_mode"])
        self.assertFalse(model.can_cycle_rate(state))
        self.assertFalse(state["config_changed"])
        self.assertEqual(state["rf_mode_cycle_multiplier"], 1)

    def test_same_rate_matching_sync_state_does_not_force_disconnect(self):
        state = model.process_sync(model.connected_state(), rate_code=7)
        self.assertFalse(state["forced_rate_change"])
        self.assertEqual(state["connection"], "connected")
        self.assertTrue(state["timer_running"])

    def test_same_rate_sync_state_mismatch_becomes_tentative(self):
        state = model.process_sync(
            model.connected_state(), rate_code=7, sync_state_matches=False)
        self.assertTrue(state["sync_accepted"])
        self.assertFalse(state["forced_rate_change"])
        self.assertEqual(state["connection"], "tentative")
        self.assertTrue(state["timer_running"])

    def test_invalid_crc_is_ignored(self):
        state = model.process_sync(model.connected_state(), rate_code=9,
                                   crc_valid=False)
        self.assertFalse(state["sync_accepted"])
        self.assertEqual(state["connection"], "connected")
        self.assertEqual(state["current_rate_code"], 7)
        self.assertFalse(state["generic_frame_complete"])

    def test_crc_valid_identity_rejected_sync_still_replays_rc(self):
        forced = model.process_sync(model.connected_state(), rate_code=9)
        state = model.process_sync(forced, rate_code=9,
                                   identity_match=False)
        self.assertFalse(state["sync_accepted"])
        self.assertTrue(state["identity_rejected_after_crc"])
        self.assertEqual(state["connection"], "disconnected")
        self.assertEqual(state["current_rate_code"], 9)
        self.assertTrue(state["generic_frame_complete"])
        self.assertTrue(state["retained_rc_replayed"])
        self.assertFalse(state["forced_rate_change"])

    def test_valid_sync_after_latch_models_tentative_and_forced_loss(self):
        latched = model.process_sync(model.connected_state(), rate_code=9)
        same_rate = model.process_sync(latched, rate_code=9)
        self.assertEqual(same_rate["connection"], "tentative")
        self.assertTrue(same_rate["timer_running"])
        self.assertFalse(same_rate["forced_rate_change"])
        other_rate = model.process_sync(latched, rate_code=4)
        self.assertEqual(other_rate["connection"], "disconnected")
        self.assertFalse(other_rate["timer_running"])
        self.assertTrue(other_rate["forced_rate_change"])
        self.assertEqual(other_rate["current_rate_code"], 4)
        self.assertEqual(other_rate["rf_mode_cycle_multiplier"], 1)

    def test_reserved_rate_code_uses_initial_scan_rate_fallback(self):
        state = model.process_sync(model.connected_state(), rate_code=5)
        self.assertTrue(state["sync_accepted"])
        self.assertEqual(state["current_rate_code"], 7)
        self.assertEqual(state["next_rate_code"], 7)
        self.assertFalse(state["forced_rate_change"])
        rejected = model.process_sync(model.connected_state(), rate_code=15,
                                      identity_match=False)
        self.assertFalse(rejected["sync_accepted"])
        self.assertTrue(rejected["generic_frame_complete"])
        self.assertEqual(rejected["current_rate_code"], 7)
        with self.assertRaises(ValueError):
            model.process_sync(model.connected_state(), rate_code=16)

    def test_reserved_rate_after_latch_falls_back_to_scan_rate(self):
        latched = model.process_sync(model.connected_state(), rate_code=9)
        self.assertEqual(latched["current_rate_code"], 9)
        self.assertEqual(latched["scan_rate_code"], 7)
        state = model.process_sync(latched, rate_code=5)
        self.assertTrue(state["sync_accepted"])
        self.assertTrue(state["forced_rate_change"])
        self.assertEqual(state["connection"], "disconnected")
        self.assertEqual(state["current_rate_code"], 7)
        self.assertEqual(state["next_rate_code"], 7)
        self.assertEqual(state["scan_rate_code"], 7)

    def test_switch_mode_change_is_explicitly_unsafe(self):
        state = model.process_sync(model.connected_state(), rate_code=9,
                                   switch_mode="wide")
        self.assertTrue(state["config_changed"])
        self.assertEqual(state["switch_mode"], "wide")

    def test_tlm_replays_retained_rc_while_disconnected(self):
        state = model.process_sync(model.connected_state(), rate_code=9)
        state = model.process_tlm(state)
        self.assertEqual(state["connection"], "disconnected")
        self.assertTrue(state["tlm_accepted"])
        self.assertTrue(state["generic_frame_complete"])
        self.assertTrue(state["retained_rc_replayed"])
        self.assertEqual(state["generic_signal_deadline_ms"], 100)
        self.assertEqual(state["radio_channel"], model.INITIAL_CHANNEL)
        self.assertEqual(state["rf_mode_cycle_multiplier"], 10)

    def test_tlm_needs_crc_recognized_type_and_prior_payload(self):
        for kwargs in ({"crc_valid": False}, {"recognized_type": False}):
            state = model.process_tlm(model.connected_state(), **kwargs)
            self.assertFalse(state["tlm_accepted"])
            self.assertFalse(state["generic_frame_complete"])
        state = model.process_tlm(model.connected_state(retained_rc=False))
        self.assertTrue(state["generic_frame_complete"])
        self.assertFalse(state["retained_rc_replayed"])

    def test_cli_report_is_non_operational_and_privacy_safe(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(model.main(["--json"]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["schema"], model.SCHEMA)
        self.assertFalse(report["transmit_enabled"])
        self.assertFalse(report["live_validated"])
        self.assertEqual(len(report["paths"]), 3)
        self.assertTrue(all(path["rejected_sync_reports_complete_frame"]
                            for path in report["paths"]))
        self.assertTrue(all(path["post_forced_loss_cycle_multiplier"] == 1
                            for path in report["paths"]))
        self.assertTrue(all(path["post_keepalive_cycle_multiplier"] == 10
                            for path in report["paths"]))


if __name__ == "__main__":
    unittest.main()
