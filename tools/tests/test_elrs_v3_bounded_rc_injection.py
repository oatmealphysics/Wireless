"""Tests for the offline bounded ELRS v3 RC injection canary."""

import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest


TOOL = Path(__file__).resolve().parents[1] / "elrs-v3-bounded-rc-injection.py"
SPEC = importlib.util.spec_from_file_location("elrs_v3_bounded_rc_injection", TOOL)
injection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(injection)

UID = bytes.fromhex("112233445566")


def aux_safety(blocked=()):
    now_ns = time.time_ns()
    modes = [{
        "permanent_id": 0, "aux_channel_index": 0,
        "start_step": 32, "end_step": 48,
        "mode_logic": 0, "linked_to": 0,
    }]
    modes.extend({
        "permanent_id": 1, "aux_channel_index": index,
        "start_step": 1, "end_step": 48,
        "mode_logic": 0, "linked_to": 0,
    } for index in blocked)
    return {
        "schema": "betaflight-aux-safety-v3",
        "board_identifier": "S411",
        "craft_name": "TH III Plus",
        "rc_map": [1, 2, 3, 0, 4, 5, 6, 7],
        "mode_range_slot_count": len(modes),
        "adjustment_range_slot_count": 0,
        "mode_ranges": modes,
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


class BoundedInjectionTests(unittest.TestCase):
    def test_source_derived_synthetic_packet_vector(self):
        packet, rc = injection.build_packet(UID, aux_safety(), "right")
        self.assertEqual(packet.hex(), "d40090092080005b")
        self.assertEqual(
            {name: rc[name] for name in ("roll", "pitch", "yaw", "throttle")},
            {"roll": 1600, "pitch": 1500, "yaw": 1500, "throttle": 988},
        )

    def test_left_and_right_are_exact_crc_valid_candidates(self):
        for direction, expected_roll in (("left", 1400), ("right", 1600)):
            evidence = aux_safety()
            plan = injection.build_plan(UID, evidence, direction)
            self.assertEqual(plan["schema"], injection.SCHEMA)
            self.assertFalse(plan["transmit_enabled"])
            self.assertEqual(plan["rc_channels"]["roll"], expected_roll)
            self.assertEqual(plan["rc_channels"]["throttle"], 988)
            self.assertEqual(plan["rc_channels"]["pitch"], 1500)
            self.assertEqual(plan["rc_channels"]["yaw"], 1500)
            injection.validate_injection_canary(plan, UID, evidence)

    def test_uses_first_unused_rotating_aux(self):
        evidence = aux_safety(blocked=(1, 2))
        plan = injection.build_plan(UID, evidence, "right")
        self.assertEqual(plan["safety"]["rotating_aux_channel_index"], 3)
        injection.validate_injection_canary(plan, UID, evidence)

    def test_preserves_exact_observed_rotating_aux_value(self):
        evidence = aux_safety()
        for sample in evidence["observed_rc_samples_us"]:
            sample[5] = 2000
        plan = injection.build_plan(UID, evidence, "right")
        self.assertEqual(plan["construction"]["preserved_switch_pwm"], 2000)
        self.assertEqual(plan["construction"]["switch_value_raw"], 5)
        injection.validate_injection_canary(plan, UID, evidence)

    def test_requires_stable_safe_live_rc_state(self):
        evidence = aux_safety()
        for channel in range(5, 8):
            evidence["observed_rc_samples_us"][0][channel] = 1275
        with self.assertRaisesRegex(ValueError, "no stable"):
            injection.build_plan(UID, evidence, "left")
        for mutate in (
                lambda rows: rows[0].__setitem__(4, 2000),
                lambda rows: rows[0].__setitem__(3, 1200),
                lambda rows: rows[0].__setitem__(3, 885)):
            evidence = aux_safety()
            mutate(evidence["observed_rc_samples_us"])
            with self.assertRaises(ValueError):
                injection.build_plan(UID, evidence, "left")
        evidence = aux_safety()
        evidence["schema"] = "betaflight-aux-safety-v2"
        with self.assertRaisesRegex(ValueError, "live RC-state"):
            injection.build_plan(UID, evidence, "left")
        evidence = aux_safety()
        evidence["craft_name"] = "other"
        with self.assertRaisesRegex(ValueError, "live RC-state"):
            injection.build_plan(UID, evidence, "left")

    def test_rejects_stale_reordered_and_overlong_live_evidence(self):
        evidence = aux_safety()
        now_ns = time.time_ns()
        evidence["observed_wall_time_ns"] = [
            now_ns - injection.MAX_RC_EVIDENCE_AGE_NS - 2,
            now_ns - injection.MAX_RC_EVIDENCE_AGE_NS - 1,
            now_ns - injection.MAX_RC_EVIDENCE_AGE_NS,
        ]
        with self.assertRaisesRegex(ValueError, "stale"):
            injection.build_plan(UID, evidence, "right")

        evidence = aux_safety()
        evidence["observed_wall_time_ns"][1] = evidence[
            "observed_wall_time_ns"][0]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            injection.build_plan(UID, evidence, "right")

        evidence = aux_safety()
        now_ns = time.time_ns()
        evidence["observed_wall_time_ns"] = [
            now_ns - injection.MAX_RC_EVIDENCE_SPAN_NS - 1,
            now_ns - 1,
            now_ns,
        ]
        with self.assertRaisesRegex(ValueError, "span"):
            injection.build_plan(UID, evidence, "right")

        evidence = aux_safety()
        future = (time.time_ns()
                  + injection.MAX_RC_EVIDENCE_FUTURE_SKEW_NS
                  + 1_000_000_000)
        evidence["observed_wall_time_ns"] = [future - 2, future - 1, future]
        with self.assertRaisesRegex(ValueError, "future"):
            injection.build_plan(UID, evidence, "right")

    def test_all_rotating_aux_channels_used_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no stable"):
            injection.build_plan(UID, aux_safety(blocked=range(1, 8)), "left")

    def test_tampered_direction_or_packet_is_rejected(self):
        evidence = aux_safety()
        plan = injection.build_plan(UID, evidence, "right")
        plan["construction"]["roll_direction"] = "left"
        with self.assertRaises(ValueError):
            injection.validate_injection_canary(plan, UID, evidence)
        evidence = aux_safety()
        plan = injection.build_plan(UID, evidence, "right")
        plan["frames"][0]["packet_hex"] = "00" * 8
        with self.assertRaises(ValueError):
            injection.validate_injection_canary(plan, UID, evidence)
        evidence = aux_safety()
        plan = injection.build_plan(UID, evidence, "right")
        plan["aux_safety_evidence"]["preserved_switch_pwm"] = 2000
        with self.assertRaisesRegex(ValueError, "AUX evidence"):
            injection.validate_injection_canary(plan, UID, evidence)

    def test_rejects_ack_bit_even_with_recomputed_crc_and_symbols(self):
        evidence = aux_safety()
        plan = injection.build_plan(UID, evidence, "right")
        packet = bytearray.fromhex(plan["frames"][0]["packet_hex"])
        packet[6] ^= 0x40
        kind = packet[0] & 0x03
        checksum = injection.REPLAY["crc14"](
            bytes((kind,)) + bytes(packet[1:7]),
            injection.REPLAY["uid_crc_init"](UID),
        )
        packet[0] = ((checksum >> 8) & 0x3F) << 2 | kind
        packet[7] = checksum & 0xFF
        plan["frames"][0]["packet_hex"] = bytes(packet).hex()
        plan["frames"][0]["symbols"] = injection.REPLAY[
            "encode_symbols"](bytes(packet))
        with self.assertRaisesRegex(ValueError, "bounded construction"):
            injection.validate_injection_canary(plan, UID, evidence)

    def test_wrong_map_fails_before_construction(self):
        evidence = aux_safety()
        evidence["rc_map"] = list(range(8))
        with self.assertRaisesRegex(ValueError, "RC map"):
            injection.build_plan(UID, evidence, "right")

    def test_cli_writes_only_private_plan_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            uid_path = work / "uid.hex"
            aux_path = work / "aux.json"
            output = work / "plan.json"
            uid_path.write_text(UID.hex(), encoding="utf-8")
            aux_path.write_text(json.dumps(aux_safety()), encoding="utf-8")
            path_globals = injection.REPLAY["require_private_path"].__globals__
            old_root = path_globals["ROOT"]
            path_globals["ROOT"] = root
            try:
                args = ["--uid-file", str(uid_path),
                        "--aux-safety-json", str(aux_path),
                        "--roll-direction", "left",
                        "--private-plan-out", str(output)]
                self.assertEqual(injection.main(args), 0)
                self.assertEqual(injection.main(args), 2)
            finally:
                path_globals["ROOT"] = old_root
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema"], injection.SCHEMA)


if __name__ == "__main__":
    unittest.main()
