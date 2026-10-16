"""Synthetic tests for the capture-relative ELRS v3 canary scheduler."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "elrs-v3-canary-schedule.py"
SPEC = importlib.util.spec_from_file_location("elrs_v3_canary_schedule", TOOL)
scheduler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduler)

UID = bytes.fromhex("12 34 56 78 9a bc")  # synthetic fixture only


def canary():
    return {
        "schema": "elrs-v3-msp-api-version-canary-v1",
        "transmit_enabled": False,
        "command": {"id": 1, "name": "MSP_API_VERSION",
                    "payload_bytes": 0, "read_only": True},
        "air": {"sf": 7, "bandwidth_hz": 812500,
                "implicit_payload_bytes": 8, "preamble_symbols": 12},
        "frames": [
            {"sequence": i, "package_index": package,
             "packet_hex": (bytes([i]) * 8).hex(), "symbols": [i] * 21}
            for i, package in enumerate((1, 2, 0))
        ],
    }


def sync(index=0, nonce=3, stamp=1.0, line=7, ratio=0):
    return {"line": line, "stamp": stamp,
            "fields": {"fhss_index": index, "nonce": nonce,
                       "switch_mode": "hybrid", "new_tlm_ratio": ratio,
                       "rate_index": 7}}


class CanaryScheduleTests(unittest.TestCase):
    def test_three_fragments_cross_hop_boundary(self):
        frames = scheduler.validate_canary(canary())
        plan = scheduler.build_schedule(UID, sync(), frames,
                                        guard_slots=2, rate_hz=150)
        self.assertFalse(plan["transmit_enabled"])
        self.assertTrue(plan["fresh_sync_required"])
        self.assertEqual([row["package_index"] for row in plan["slots"]],
                         [1, 2, 0])
        self.assertEqual([row["delta_slots"] for row in plan["slots"]],
                         [3, 4, 5])
        self.assertEqual([row["nonce"] for row in plan["slots"]], [6, 7, 8])
        self.assertEqual([row["fhss_index"] for row in plan["slots"]],
                         [1, 1, 2])
        sequence = scheduler.FHSS["build_v3_sequence"](UID)
        self.assertEqual([row["channel"] for row in plan["slots"]],
                         [sequence[1], sequence[1], sequence[2]])

    def test_nonce_and_sequence_rollover(self):
        frames = scheduler.validate_canary(canary())
        # Each 80-entry block begins on the fixed sync channel.
        plan = scheduler.build_schedule(UID, sync(index=160, nonce=255),
                                        frames, guard_slots=1, rate_hz=150)
        self.assertEqual(plan["slots"][0]["nonce"], 1)
        self.assertEqual(plan["slots"][0]["fhss_index"], 161)

    def test_latest_crc_valid_sync_is_selected(self):
        rows = [
            {"status": "crc_valid", "type": "SYNC",
             "preamble_start_seconds": 1.0, "sync": sync()["fields"]},
            {"status": "unresolved", "type": "SYNC",
             "preamble_start_seconds": 9.0, "sync": sync()["fields"]},
            {"status": "crc_valid", "type": "SYNC",
             "preamble_start_seconds": 2.0,
             "sync": sync(index=80, nonce=64)["fields"]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sync.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                            encoding="utf-8")
            chosen = scheduler.read_latest_sync(path)
        self.assertEqual(chosen["line"], 3)
        self.assertEqual(chosen["stamp"], 2.0)
        self.assertEqual(chosen["fields"]["fhss_index"], 80)

    def test_transition_evidence_matches_nonce_and_hop_clock(self):
        records = [sync(index=0, nonce=3, stamp=1.0, line=1),
                   sync(index=1, nonce=4, stamp=1.0 + 1 / 150, line=2),
                   sync(index=1, nonce=6, stamp=1.0 + 3 / 150, line=3)]
        for record in records:
            record["source"] = "synthetic-a"
        evidence = scheduler.validate_sync_transitions(records, 150)
        self.assertEqual(evidence["sources"], 1)
        self.assertEqual(evidence["transitions_checked"], 2)
        broken = [dict(record, fields=dict(record["fields"]))
                  for record in records]
        broken[-1]["fields"]["nonce"] = 7
        with self.assertRaisesRegex(ValueError, "transition"):
            scheduler.validate_sync_transitions(broken, 150)

    def test_reject_wrong_uid_sequence_or_canary_shape(self):
        frames = scheduler.validate_canary(canary())
        with self.assertRaisesRegex(ValueError, "UID-derived"):
            scheduler.build_schedule(UID, sync(index=1), frames)
        broken = canary()
        broken["frames"][2]["package_index"] = 3
        with self.assertRaisesRegex(ValueError, "fragment order"):
            scheduler.validate_canary(broken)

    def test_telemetry_ratio_mapping(self):
        self.assertIsNone(scheduler.telemetry_denominator(0))
        self.assertEqual([scheduler.telemetry_denominator(value)
                          for value in range(1, 8)],
                         [128, 64, 32, 16, 8, 4, 2])

    def test_private_paths_stay_under_work(self):
        allowed = Path(__file__).resolve().parents[2] / "work" / "plan.json"
        self.assertEqual(scheduler.require_private_path(allowed), allowed.resolve())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "work/"):
                scheduler.require_private_path(Path(directory) / "plan.json")


if __name__ == "__main__":
    unittest.main()
