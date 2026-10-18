"""Synthetic tests for the stale-safe ELRS v3 fresh-SYNC clock."""

import importlib.util
import json
from pathlib import Path
import tempfile
import time
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


def rc_aux_safety():
    now_ns = time.time_ns()
    return {
        "schema": "betaflight-aux-safety-v3",
        "board_identifier": "S411",
        "craft_name": "TH III Plus",
        "rc_map": [1, 2, 3, 0, 4, 5, 6, 7],
        "mode_ranges": [{
            "permanent_id": 0, "aux_channel_index": 0,
            "start_step": 32, "end_step": 48,
            "mode_logic": 0, "linked_to": 0,
        }],
        "adjustment_ranges": [],
        "observed_rc_samples_us": [
            [1500, 1500, 1500, 988, 1000, 1000, 2000, 1000]
            for _ in range(3)
        ],
        "observed_wall_time_ns": [
            now_ns - 100_000_000,
            now_ns - 50_000_000,
            now_ns,
        ],
    }


def rc_canary():
    rc = clock.RC_CANARY
    # OTA slots are [throttle, roll, pitch, yaw] for this pair.
    packed = 0 | (612 << 10) | (512 << 20) | (512 << 30)
    packet = bytearray(bytes((0,)) + packed.to_bytes(5, "little")
                       + bytes((0, 0)))
    checksum = rc["crc14"](bytes((0,)) + packet[1:7],
                           rc["uid_crc_init"](UID))
    packet[0] = (checksum >> 8) << 2
    packet[7] = checksum & 0xFF
    packet = bytes(packet)
    frame = {
        "line": 1,
        "source": "synthetic",
        "preamble_start_seconds": 0.5,
        "packet_hex": packet.hex(),
        "rc": rc["decode_hybrid_rc"](packet),
    }
    interleaver = rc["verify_interleaver_round_trip"](packet)
    return rc["build_plan"](UID, frame, interleaver, rc_aux_safety())


class FreshSyncClockTests(unittest.TestCase):
    def test_bounded_injection_normalizes_to_one_fresh_slot(self):
        evidence = rc_aux_safety()
        candidate = clock.RC_INJECTION["build_plan"](
            UID, evidence, "right")
        frames_out, metadata = clock.validate_canary_input(
            candidate, UID, evidence)
        self.assertEqual(len(frames_out), 1)
        self.assertEqual(metadata["kind"], "bounded_rc_injection")
        plan = clock.build_fresh_plan(
            UID, record(0, 1 / 150), frames_out, cursor_seconds=1.020,
            sample_rate_hz=4_062_500, packet_period_seconds=1 / 150,
            lead_seconds=0.100, canary_metadata=metadata,
        )
        self.assertEqual(len(plan["slots"]), 1)
        self.assertEqual(plan["canary"]["kind"], "bounded_rc_injection")
        self.assertFalse(plan["transmit_enabled"])

    def test_rc_canary_normalizes_to_one_fresh_slot(self):
        frames_out, metadata = clock.validate_canary_input(
            rc_canary(), UID, rc_aux_safety())
        self.assertEqual(len(frames_out), 1)
        self.assertEqual(metadata["kind"], "hybrid_rc_replay")
        anchor = record(0, 1 / 150)
        plan = clock.build_fresh_plan(
            UID, anchor, frames_out, cursor_seconds=1.020,
            sample_rate_hz=4_062_500, packet_period_seconds=1 / 150,
            lead_seconds=0.100, canary_metadata=metadata,
        )
        self.assertEqual(len(plan["slots"]), 1)
        self.assertNotIn("package_index", plan["slots"][0])
        self.assertEqual(plan["canary"]["kind"], "hybrid_rc_replay")
        self.assertNotIn("command", plan)
        self.assertFalse(plan["transmit_enabled"])

    def test_rc_canary_requires_aux_evidence(self):
        with self.assertRaisesRegex(ValueError, "AUX"):
            clock.validate_canary_input(rc_canary(), UID, None)

    def test_rc_scheduling_rejects_stale_live_evidence(self):
        fresh = rc_aux_safety()
        candidate = clock.RC_INJECTION["build_plan"](
            UID, fresh, "right")
        stale = rc_aux_safety()
        cutoff = (time.time_ns()
                  - clock.RC_INJECTION["MAX_RC_EVIDENCE_AGE_NS"]
                  - 1_000_000_000)
        stale["observed_wall_time_ns"] = [cutoff - 2, cutoff - 1, cutoff]
        with self.assertRaisesRegex(ValueError, "stale"):
            clock.validate_canary_input(candidate, UID, stale)

    def test_rc_scheduling_rejects_unsafe_current_controller_state(self):
        unsafe = rc_aux_safety()
        for sample in unsafe["observed_rc_samples_us"]:
            sample[4] = 2000
        with self.assertRaisesRegex(ValueError, "safely disarmed"):
            clock.validate_canary_input(rc_canary(), UID, unsafe)

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
        self.assertEqual(plan["command"], {
            "name": "MSP_API_VERSION", "id": 1,
            "read_only": True, "fragment_count": 3,
        })
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
