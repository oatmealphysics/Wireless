"""Synthetic tests for the stale-safe ELRS v3 fresh-SYNC clock."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "elrs-v3-fresh-sync-clock.py"
SPEC = importlib.util.spec_from_file_location("elrs_v3_fresh_sync_clock", TOOL)
clock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clock)

UID = bytes.fromhex("12 34 56 78 9a bc")


def record(slot: int, period: float, *, source="capture-a", line=1):
    sequence_index = (slot // 4) % 240
    # Use slot zero at a source-defined sync-channel sequence boundary.
    return {
        "source": source,
        "line": line,
        "stamp": 1.0 + slot * period,
        "fields": {"fhss_index": sequence_index, "nonce": slot & 0xFF,
                   "switch_mode": "hybrid", "new_tlm_ratio": 0,
                   "rate_index": 7},
    }


def frames():
    return [{"sequence": i, "package_index": package,
             "packet_hex": (bytes([i]) * 8).hex(), "symbols": [i] * 21}
            for i, package in enumerate((1, 2, 0))]


class FreshSyncClockTests(unittest.TestCase):
    def test_receive_anchor_manifest_is_independently_validated(self):
        manifest = {
            "schema": "uhd-elrs-receive-anchor-v1",
            "receive_only": True,
            "transmit_capable": False,
            "sample_format": "complex-float32-little-endian",
            "sample_count": 1_015_625,
            "sample_rate_hz": 4_062_500.0,
            "first_sample_uhd_seconds": 1.321458184615385,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "anchor.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            anchor = clock.read_receive_anchor(path)
            self.assertAlmostEqual(anchor["capture_duration_seconds"], 0.25)
            self.assertEqual(anchor["sample_rate_hz"], 4_062_500.0)
            manifest["transmit_capable"] = True
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "receive-only"):
                clock.read_receive_anchor(path)

    def test_clock_fit_recovers_rate_across_capture_origins(self):
        period = 1 / 150.02
        records = [record(slot, period, line=index)
                   for index, slot in enumerate((0, 2, 320, 322), 1)]
        second = [record(slot, period, source="capture-b", line=index)
                  for index, slot in enumerate((0, 2, 640), 1)]
        for item in second:
            item["stamp"] += 9.0
        fitted = clock.fit_packet_clock(records + second)
        self.assertEqual(fitted["transitions_checked"], 5)
        self.assertAlmostEqual(fitted["fitted_rate_hz"], 150.02, places=5)
        self.assertEqual(fitted["fit_basis"], "multi_sync_regression")

    def test_fresh_plan_has_future_samples_and_uhd_times(self):
        anchor = record(0, 1 / 150)
        plan = clock.build_fresh_plan(
            UID, anchor, frames(), cursor_seconds=1.020,
            sample_rate_hz=4_062_500, packet_period_seconds=1 / 150,
            lead_seconds=0.100, capture_start_uhd_seconds=50.0,
        )
        self.assertFalse(plan["transmit_enabled"])
        self.assertTrue(plan["fresh_sync_validated"])
        self.assertEqual([row["package_index"] for row in plan["slots"]],
                         [1, 2, 0])
        self.assertTrue(all(row["capture_preamble_seconds"] >= 1.120
                            for row in plan["slots"]))
        for row in plan["slots"]:
            self.assertEqual(row["capture_sample_index"],
                             round(row["capture_preamble_seconds"] * 4_062_500))
            self.assertAlmostEqual(row["uhd_preamble_time_seconds"],
                                   50 + row["capture_preamble_seconds"])

    def test_stale_future_and_horizon_fail_closed(self):
        anchor = record(0, 1 / 150)
        common = dict(uid=UID, anchor=anchor, frames=frames(),
                      sample_rate_hz=4_062_500,
                      packet_period_seconds=1 / 150)
        with self.assertRaisesRegex(ValueError, "stale"):
            clock.build_fresh_plan(**common, cursor_seconds=1.6)
        with self.assertRaisesRegex(ValueError, "precedes"):
            clock.build_fresh_plan(**common, cursor_seconds=0.9)
        with self.assertRaisesRegex(ValueError, "horizon"):
            clock.build_fresh_plan(**common, cursor_seconds=1.4,
                                   lead_seconds=0.2, max_age_seconds=0.5,
                                   max_horizon_seconds=0.5)

    def test_anchor_source_must_be_explicit_for_multiple_captures(self):
        records = [record(0, 1 / 150, source="a"),
                   record(0, 1 / 150, source="b")]
        with self.assertRaisesRegex(ValueError, "anchor-source"):
            clock.choose_anchor(records, None)
        self.assertEqual(clock.choose_anchor(records, "b")["source"], "b")

    def test_transition_mismatch_is_rejected(self):
        period = 1 / 150
        records = [record(0, period), record(2, period)]
        records[-1]["fields"]["nonce"] = 3
        with self.assertRaisesRegex(ValueError, "transition"):
            clock.fit_packet_clock(records)


if __name__ == "__main__":
    unittest.main()
