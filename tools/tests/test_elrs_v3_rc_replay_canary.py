"""Tests for the offline ELRS v3 hybrid-RC replay canary tool."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CANARY = ROOT / "tools" / "elrs-v3-rc-replay-canary.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_rc_packet(roll=512, pitch=512, yaw=512, throttle=0,
                    armed=False, switch_index=0, switch_value=0):
    """Build a synthetic 8-byte hybrid-RC packet (type=0b00).

    Analog values are 10-bit raw (PWM = 988 + value).
    """
    # This pair's verified mapping is OTA [throttle, roll, pitch, yaw].
    packed = throttle | (roll << 10) | (pitch << 20) | (yaw << 30)
    payload = packed.to_bytes(5, "little")
    ch4_bit = 1 if armed else 0
    switch_byte = ((ch4_bit << 7) | (switch_index << 3)
                   | (switch_value & (0x0F if switch_index >= 6 else 0x07)))
    # type bits = 0b00, CRC high bits in [7:2] — set to zero for now
    header = 0x00
    crc_low = 0x00
    return bytes([header]) + payload + bytes([switch_byte, crc_low])


def stamp_crc(packet: bytes, uid: bytes, mod) -> bytes:
    """Stamp a valid CRC14 onto an 8-byte RC packet."""
    initial = mod.uid_crc_init(uid)
    kind = packet[0] & 0x03
    calc = mod.crc14(bytes([kind]) + packet[1:7], initial)
    high6 = (calc >> 8) & 0x3F
    low8 = calc & 0xFF
    header = (high6 << 2) | kind
    return bytes([header]) + packet[1:7] + bytes([low8])


def aux_safety(*, blocked_mode_aux=(), blocked_adjustment_aux=()):
    """Synthetic live-config evidence: AUX1-high arms; other AUX are unused."""
    modes = [{
        "permanent_id": 0,
        "aux_channel_index": 0,
        "start_step": 32,
        "end_step": 48,
        "mode_logic": 0,
        "linked_to": 0,
    }]
    modes.extend({
        "permanent_id": 1,
        "aux_channel_index": index,
        "start_step": 1,
        "end_step": 48,
        "mode_logic": 0,
        "linked_to": 0,
    } for index in blocked_mode_aux)
    adjustments = [{
        "aux_channel_index": index,
        "start_step": 1,
        "end_step": 48,
        "aux_switch_channel_index": index,
    } for index in blocked_adjustment_aux]
    return {
        "schema": "betaflight-aux-safety-v2",
        "board_identifier": "S411",
        "craft_name": "TH III Plus",
        "rc_map": [1, 2, 3, 0, 4, 5, 6, 7],
        "mode_range_slot_count": len(modes),
        "adjustment_range_slot_count": len(adjustments),
        "mode_ranges": modes,
        "adjustment_ranges": adjustments,
    }


class HybridRCDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def test_center_stick_decode(self):
        pkt = build_rc_packet(roll=512, pitch=512, yaw=512, throttle=0)
        rc = self.mod.decode_hybrid_rc(pkt)
        self.assertIsNotNone(rc)
        self.assertEqual(rc["roll"], 1500)
        self.assertEqual(rc["pitch"], 1500)
        self.assertEqual(rc["yaw"], 1500)
        self.assertEqual(rc["throttle"], 988)

    def test_extreme_values(self):
        pkt = build_rc_packet(roll=0, pitch=1023, yaw=0, throttle=1023)
        rc = self.mod.decode_hybrid_rc(pkt)
        self.assertEqual(rc["roll"], 988)
        self.assertEqual(rc["pitch"], 2011)
        self.assertEqual(rc["yaw"], 988)
        self.assertEqual(rc["throttle"], 2011)

    def test_capture_derived_channel_mapping_vector(self):
        # Public Oct 16 summary: OTA PWM [988, 1437, 1497, 1507] maps to
        # MSP [roll, pitch, yaw, throttle] = [1437, 1497, 1507, 988].
        pkt = build_rc_packet(roll=1437 - 988, pitch=1497 - 988,
                              yaw=1507 - 988, throttle=988 - 988)
        rc = self.mod.decode_hybrid_rc(pkt)
        self.assertEqual(
            [rc["roll"], rc["pitch"], rc["yaw"], rc["throttle"]],
            [1437, 1497, 1507, 988],
        )

    def test_armed_bit(self):
        armed_pkt = build_rc_packet(armed=True)
        disarmed_pkt = build_rc_packet(armed=False)
        self.assertEqual(self.mod.decode_hybrid_rc(armed_pkt)["ch4_bit"], 1)
        self.assertEqual(self.mod.decode_hybrid_rc(armed_pkt)["aux1"], 2000)
        self.assertEqual(self.mod.decode_hybrid_rc(disarmed_pkt)["ch4_bit"], 0)
        self.assertEqual(self.mod.decode_hybrid_rc(disarmed_pkt)["aux1"], 1000)

    def test_rejects_non_rc_type(self):
        pkt = build_rc_packet()
        # Set type bits to MSP (0b01)
        non_rc = bytes([pkt[0] | 0x01]) + pkt[1:]
        self.assertIsNone(self.mod.decode_hybrid_rc(non_rc))

    def test_rejects_wrong_length(self):
        self.assertIsNone(self.mod.decode_hybrid_rc(b"\x00" * 7))

    def test_switch_index_3bit(self):
        pkt = build_rc_packet(switch_index=3, switch_value=5)
        rc = self.mod.decode_hybrid_rc(pkt)
        self.assertEqual(rc["switch_index"], 3)
        self.assertEqual(rc["switch_value_raw"], 5)
        self.assertEqual(rc["switch_width"], 3)

    def test_switch_index_4bit(self):
        # index=6 (0b110) with value=5 (0b0101): bit 3 of value is 0,
        # so it doesn't overlap with the index field at bits [5:3]
        pkt = build_rc_packet(switch_index=6, switch_value=5)
        rc = self.mod.decode_hybrid_rc(pkt)
        self.assertEqual(rc["switch_index"], 6)
        self.assertEqual(rc["switch_value_raw"], 5)
        self.assertEqual(rc["switch_width"], 4)


class SafetyGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def _make_rc(self, **overrides):
        defaults = {
            "roll": 1600, "pitch": 1500, "yaw": 1500,
            "throttle": 988, "aux1": 1000, "ch4_bit": 0,
            "switch_index": 0, "switch_value_raw": 0,
            "switch_width": 3, "stubborn_ack": 0,
        }
        defaults.update(overrides)
        return defaults

    def test_all_safe(self):
        ok, reason = self.mod.safety_check(self._make_rc(), aux_safety())
        self.assertTrue(ok)
        self.assertEqual(reason, "pass")

    def test_armed_rejected(self):
        ok, reason = self.mod.safety_check(self._make_rc(ch4_bit=1), aux_safety())
        self.assertFalse(ok)
        self.assertIn("armed", reason)

    def test_wrong_rc_map_rejected_before_channel_interpretation(self):
        evidence = aux_safety()
        evidence["rc_map"] = list(range(8))
        ok, reason = self.mod.safety_check(
            self._make_rc(ch4_bit=1, throttle=1800), evidence)
        self.assertFalse(ok)
        self.assertEqual(reason, "rc_map_unverified")

    def test_high_throttle_rejected(self):
        ok, reason = self.mod.safety_check(self._make_rc(throttle=1200), aux_safety())
        self.assertFalse(ok)
        self.assertIn("throttle", reason)

    def test_throttle_boundary(self):
        ok_at, _ = self.mod.safety_check(self._make_rc(throttle=1050), aux_safety())
        ok_over, _ = self.mod.safety_check(self._make_rc(throttle=1051), aux_safety())
        self.assertTrue(ok_at)
        self.assertFalse(ok_over)

    def test_custom_throttle_max(self):
        ok, _ = self.mod.safety_check(
            self._make_rc(throttle=1000), aux_safety(), 1000)
        self.assertTrue(ok)
        fail, _ = self.mod.safety_check(
            self._make_rc(throttle=1001), aux_safety(), 1000)
        self.assertFalse(fail)
        with self.assertRaisesRegex(ValueError, "throttle maximum"):
            self.mod.safety_check(self._make_rc(), aux_safety(), 1051)

    def test_pitch_off_center_rejected(self):
        ok, reason = self.mod.safety_check(
            self._make_rc(pitch=1500 + 51), aux_safety())
        self.assertFalse(ok)
        self.assertIn("pitch_yaw", reason)

    def test_yaw_off_center_rejected(self):
        ok, reason = self.mod.safety_check(
            self._make_rc(yaw=1500 - 51), aux_safety())
        self.assertFalse(ok)
        self.assertIn("pitch_yaw", reason)

    def test_pitch_yaw_at_tolerance_boundary(self):
        ok, _ = self.mod.safety_check(
            self._make_rc(pitch=1550, yaw=1450), aux_safety())
        self.assertTrue(ok)
        fail, _ = self.mod.safety_check(
            self._make_rc(pitch=1551, yaw=1450), aux_safety())
        self.assertFalse(fail)

    def test_ambiguous_3bit_switch_rejected(self):
        ok, reason = self.mod.safety_check(
            self._make_rc(switch_index=2, switch_value_raw=3, switch_width=3),
            aux_safety())
        self.assertFalse(ok)
        self.assertIn("ambiguous_switch", reason)

    def test_safe_3bit_switch_values(self):
        for val in (0, 6, 7):
            with self.subTest(val=val):
                ok, _ = self.mod.safety_check(
                    self._make_rc(switch_value_raw=val), aux_safety())
                self.assertTrue(ok)

    def test_ambiguous_4bit_switch_rejected(self):
        ok, _ = self.mod.safety_check(
            self._make_rc(switch_index=6, switch_value_raw=8, switch_width=4),
            aux_safety())
        self.assertFalse(ok)

    def test_safe_4bit_switch_values(self):
        for val in (0, 15):
            with self.subTest(val=val):
                ok, _ = self.mod.safety_check(
                    self._make_rc(switch_index=6, switch_value_raw=val,
                                  switch_width=4), aux_safety())
                self.assertTrue(ok)

    def test_roll_window_is_mandatory(self):
        for roll in (1500, 1801):
            with self.subTest(roll=roll):
                ok, reason = self.mod.safety_check(
                    self._make_rc(roll=roll), aux_safety())
                self.assertFalse(ok)
                self.assertIn("roll", reason)

    def test_configured_rotating_aux_is_rejected(self):
        # switch_index 0 updates AUX2, whose zero-based mode index is 1.
        ok, reason = self.mod.safety_check(
            self._make_rc(), aux_safety(blocked_mode_aux=(1,)))
        self.assertFalse(ok)
        self.assertIn("configured", reason)

    def test_adjustment_rotating_aux_is_rejected(self):
        ok, reason = self.mod.safety_check(
            self._make_rc(), aux_safety(blocked_adjustment_aux=(1,)))
        self.assertFalse(ok)
        self.assertIn("configured", reason)


class RollWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def test_in_window(self):
        rc = {"roll": 1500 + 100}
        self.assertTrue(self.mod.is_roll_in_window(rc))

    def test_below_window(self):
        rc = {"roll": 1500 + 49}
        self.assertFalse(self.mod.is_roll_in_window(rc))

    def test_above_window(self):
        rc = {"roll": 1500 + 301}
        self.assertFalse(self.mod.is_roll_in_window(rc))

    def test_at_boundaries(self):
        self.assertTrue(self.mod.is_roll_in_window({"roll": 1500 + 50}))
        self.assertTrue(self.mod.is_roll_in_window({"roll": 1500 - 300}))
        self.assertFalse(self.mod.is_roll_in_window({"roll": 1500 + 301}))
        self.assertFalse(self.mod.is_roll_in_window({"roll": 1500 - 49}))


class AuxSafetyEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary_aux")

    def _read(self, snapshot):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aux.json"
            path.write_text(json.dumps(snapshot))
            return self.mod.read_aux_safety(path)

    def test_identity_gated_snapshot_passes(self):
        evidence = self._read(aux_safety())
        self.assertEqual(evidence["board_identifier"], "S411")

    def test_wrong_identity_rejected(self):
        snapshot = aux_safety()
        snapshot["craft_name"] = "other"
        with self.assertRaisesRegex(ValueError, "TinyHawk"):
            self._read(snapshot)

    def test_wrong_or_missing_rc_map_rejected(self):
        for mapping in (None, [0, 1, 2, 3, 4, 5, 6, 7]):
            snapshot = aux_safety()
            if mapping is None:
                del snapshot["rc_map"]
            else:
                snapshot["rc_map"] = mapping
            with self.assertRaisesRegex(ValueError, "RC map"):
                self._read(snapshot)

    def test_incomplete_snapshot_rejected(self):
        snapshot = aux_safety()
        snapshot["mode_range_slot_count"] += 1
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self._read(snapshot)

    def test_arm_range_must_prove_aux1_low_disarms(self):
        snapshot = aux_safety()
        snapshot["mode_ranges"][0]["start_step"] = 0
        with self.assertRaisesRegex(ValueError, "AUX1-low"):
            self._read(snapshot)

    def test_live_snapshot_samples_are_validated(self):
        snapshot = aux_safety()
        snapshot["schema"] = "betaflight-aux-safety-v3"
        snapshot["observed_rc_samples_us"] = [
            [1500, 1500, 1500, 988, 1000, 2000, 1000, 2000]
            for _ in range(3)
        ]
        snapshot["observed_wall_time_ns"] = [1, 2, 3]
        evidence = self._read(snapshot)
        self.assertEqual(evidence["schema"], "betaflight-aux-safety-v3")
        snapshot["observed_rc_samples_us"][0][0] = 500
        with self.assertRaisesRegex(ValueError, "RC-state"):
            self._read(snapshot)


class SelectCanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def _frame(self, roll, line=1, pitch=1500, yaw=1500,
               throttle=988, armed=False, switch_val=0):
        return {
            "line": line,
            "source": "test",
            "preamble_start_seconds": 0.0,
            "packet_hex": "00" * 8,
            "rc": {
                "roll": roll, "pitch": pitch, "yaw": yaw,
                "throttle": throttle, "aux1": 1000 if not armed else 2000,
                "ch4_bit": 1 if armed else 0,
                "switch_index": 0, "switch_value_raw": switch_val,
                "switch_width": 3, "stubborn_ack": 0,
            },
        }

    def test_prefers_windowed_roll(self):
        frames = [
            self._frame(1500 + 400, line=1),  # outside window
            self._frame(1500 + 100, line=2),   # in window
        ]
        sel = self.mod.select_canary(frames, aux_safety())
        self.assertEqual(sel["line"], 2)

    def test_prefers_higher_distinctiveness_in_window(self):
        frames = [
            self._frame(1500 + 60, line=1),
            self._frame(1500 + 200, line=2),
        ]
        sel = self.mod.select_canary(frames, aux_safety())
        self.assertEqual(sel["line"], 2)

    def test_rejects_non_windowed_frames(self):
        frames = [
            self._frame(1500 + 400, line=1),
            self._frame(1500 + 500, line=2),
        ]
        self.assertIsNone(self.mod.select_canary(frames, aux_safety()))

    def test_rejects_all_unsafe(self):
        frames = [self._frame(1500 + 100, armed=True)]
        self.assertIsNone(self.mod.select_canary(frames, aux_safety()))

    def test_rejection_counts_are_first_failure_only(self):
        frames = [
            self._frame(1500 + 100, armed=True, throttle=1800),
            self._frame(1500 + 100, throttle=1200),
            self._frame(1500 + 100, throttle=1200),
        ]
        self.assertEqual(self.mod.rejection_counts(frames, aux_safety()), {
            "armed": 1,
            "throttle_too_high": 2,
        })

    def test_empty_input(self):
        self.assertIsNone(self.mod.select_canary([], aux_safety()))


class SyncValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def test_valid_hybrid_no_telemetry(self):
        ok, reason = self.mod.validate_sync_for_rc_replay(
            {"switch_mode": "hybrid", "new_tlm_ratio": 0,
             "rate_index": 7})
        self.assertTrue(ok)
        self.assertEqual(reason, "pass")

    def test_rejects_wide_mode(self):
        ok, reason = self.mod.validate_sync_for_rc_replay(
            {"switch_mode": "wide", "new_tlm_ratio": 0,
             "rate_index": 7})
        self.assertFalse(ok)
        self.assertIn("hybrid", reason)

    def test_rejects_telemetry_enabled(self):
        ok, reason = self.mod.validate_sync_for_rc_replay(
            {"switch_mode": "hybrid", "new_tlm_ratio": 3,
             "rate_index": 7})
        self.assertFalse(ok)
        self.assertIn("telemetry", reason)

    def test_rejects_missing_fields(self):
        ok, _ = self.mod.validate_sync_for_rc_replay({})
        self.assertFalse(ok)

    def test_rejects_boolean_telemetry_ratio(self):
        ok, _ = self.mod.validate_sync_for_rc_replay(
            {"switch_mode": "hybrid", "new_tlm_ratio": False,
             "rate_index": 7})
        self.assertFalse(ok)

    def test_rejects_missing_boolean_or_mismatched_rate(self):
        for rate in (None, False, 0, 2, 15):
            fields = {"switch_mode": "hybrid", "new_tlm_ratio": 0}
            if rate is not None:
                fields["rate_index"] = rate
            ok, reason = self.mod.validate_sync_for_rc_replay(fields)
            self.assertFalse(ok)
            self.assertIn("rate_index", reason)


class PlanValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def _valid_plan(self):
        uid = bytes.fromhex("112233445566")
        packet = stamp_crc(build_rc_packet(roll=612), uid, self.mod)
        frame = {
            "line": 1,
            "source": "test.jsonl",
            "preamble_start_seconds": 0.0,
            "packet_hex": packet.hex(),
            "rc": self.mod.decode_hybrid_rc(packet),
        }
        interleaver = self.mod.verify_interleaver_round_trip(packet)
        return self.mod.build_plan(uid, frame, interleaver, aux_safety())

    @property
    def uid(self):
        return bytes.fromhex("112233445566")

    def test_valid_plan_passes(self):
        result = self.mod.validate_rc_canary(
            self._valid_plan(), self.uid, aux_safety())
        self.assertEqual(result["source_line"], 1)

    def test_rejects_wrong_schema(self):
        plan = self._valid_plan()
        plan["schema"] = "wrong"
        with self.assertRaises(ValueError):
            self.mod.validate_rc_canary(plan, self.uid, aux_safety())

    def test_rejects_transmit_enabled(self):
        plan = self._valid_plan()
        plan["transmit_enabled"] = True
        with self.assertRaises(ValueError):
            self.mod.validate_rc_canary(plan, self.uid, aux_safety())

    def test_rejects_forged_safety_flag(self):
        plan = self._valid_plan()
        plan["safety"]["disarmed"] = False
        with self.assertRaises(ValueError):
            self.mod.validate_rc_canary(plan, self.uid, aux_safety())

    def test_rejects_missing_sync_requirements(self):
        plan = self._valid_plan()
        del plan["sync_requirements"]
        with self.assertRaises(ValueError):
            self.mod.validate_rc_canary(plan, self.uid, aux_safety())

    def test_rejects_non_hybrid_sync(self):
        plan = self._valid_plan()
        plan["sync_requirements"]["switch_mode"] = "wide"
        with self.assertRaises(ValueError):
            self.mod.validate_rc_canary(plan, self.uid, aux_safety())

    def test_rejects_symbol_packet_mismatch(self):
        plan = self._valid_plan()
        plan["frames"][0]["symbols"][0] ^= 1
        with self.assertRaisesRegex(ValueError, "symbols"):
            self.mod.validate_rc_canary(plan, self.uid, aux_safety())

    def test_rejects_wrong_uid_crc(self):
        with self.assertRaisesRegex(ValueError, "CRC"):
            self.mod.validate_rc_canary(
                self._valid_plan(), bytes.fromhex("aabbccddeeff"), aux_safety())

    def test_rejects_configured_aux_evidence(self):
        with self.assertRaisesRegex(ValueError, "safety"):
            self.mod.validate_rc_canary(
                self._valid_plan(), self.uid,
                aux_safety(blocked_mode_aux=(1,)))

    def test_rejects_serialized_channel_tampering(self):
        plan = self._valid_plan()
        plan["rc_channels"]["roll"] += 1
        with self.assertRaisesRegex(ValueError, "RC channels"):
            self.mod.validate_rc_canary(plan, self.uid, aux_safety())

    def test_rejects_phy_contract_tampering(self):
        plan = self._valid_plan()
        plan["air"]["sf"] = 6
        with self.assertRaisesRegex(ValueError, "PHY"):
            self.mod.validate_rc_canary(plan, self.uid, aux_safety())


class InterleaverRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def test_round_trip_known_packet(self):
        result = self.mod.verify_interleaver_round_trip(
            bytes.fromhex("0123456789abcdef"))
        self.assertTrue(result["round_trip_pass"])
        self.assertEqual(result["symbol_count"], 21)

    def test_round_trip_zeros(self):
        result = self.mod.verify_interleaver_round_trip(bytes(8))
        self.assertTrue(result["round_trip_pass"])

    def test_round_trip_ones(self):
        result = self.mod.verify_interleaver_round_trip(bytes([0xFF] * 8))
        self.assertTrue(result["round_trip_pass"])

    def test_symbol_range(self):
        result = self.mod.verify_interleaver_round_trip(bytes(8))
        self.assertTrue(all(0 <= s < 128 for s in result["symbols"]))


class CRCVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def test_valid_crc_passes(self):
        uid = bytes.fromhex("112233445566")
        pkt = build_rc_packet(roll=200, throttle=0)
        stamped = stamp_crc(pkt, uid, self.mod)
        self.assertTrue(self.mod.verify_crc(stamped, uid))

    def test_wrong_uid_fails(self):
        uid = bytes.fromhex("112233445566")
        wrong_uid = bytes.fromhex("aabbccddeeff")
        pkt = build_rc_packet()
        stamped = stamp_crc(pkt, uid, self.mod)
        self.assertFalse(self.mod.verify_crc(stamped, wrong_uid))

    def test_v3_init_formula(self):
        uid = bytes.fromhex("112233445566")
        init = self.mod.uid_crc_init(uid)
        self.assertEqual(init, (0x55 << 8 | 0x66) ^ 3)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def test_integration_with_synthetic_jsonl(self):
        uid = bytes.fromhex("aabbccddeeff")
        # Roll=200 raw → PWM 1188 → deviation 312 (outside window)
        pkt_wide = build_rc_packet(roll=200, pitch=512, yaw=512, throttle=0)
        stamped_wide = stamp_crc(pkt_wide, uid, self.mod)
        # Roll=612 raw → PWM 1600 → deviation 100 (in window)
        pkt_window = build_rc_packet(roll=612, pitch=512, yaw=512, throttle=0)
        stamped_window = stamp_crc(pkt_window, uid, self.mod)

        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "work"
            work.mkdir()
            uid_file = work / "uid.hex"
            uid_file.write_text("aabbccddeeff")
            aux_file = work / "aux.json"
            aux_file.write_text(json.dumps(aux_safety()))
            jsonl_file = work / "frames.jsonl"
            lines = []
            for i, pkt_hex in enumerate([stamped_wide.hex(),
                                          stamped_window.hex()]):
                lines.append(json.dumps({
                    "status": "crc_valid", "type": "RC",
                    "packet_bytes_hex": pkt_hex,
                    "preamble_start_seconds": float(i),
                    "source": "synthetic",
                }))
            jsonl_file.write_text("\n".join(lines) + "\n")

            plan_out = work / "plan.json"
            # Monkey-patch ROOT so require_private_path accepts tmpdir
            original_root = self.mod.ROOT
            self.mod.ROOT = Path(tmpdir)
            try:
                rc = self.mod.main([
                    "--decoded-jsonl", str(jsonl_file),
                    "--uid-file", str(uid_file),
                    "--aux-safety-json", str(aux_file),
                    "--private-plan-out", str(plan_out),
                ])
            finally:
                self.mod.ROOT = original_root

            self.assertEqual(rc, 0)
            self.assertTrue(plan_out.exists())
            plan = json.loads(plan_out.read_text())
            self.assertEqual(plan["schema"], "elrs-v3-rc-replay-canary-v2")
            self.assertFalse(plan["transmit_enabled"])
            self.assertTrue(plan["safety"]["disarmed"])
            self.assertTrue(plan["safety"]["throttle_low"])
            self.assertTrue(plan["safety"]["pitch_yaw_centered"])
            # Should prefer the in-window frame (line 2)
            self.assertEqual(plan["frames"][0]["source_line"], 2)
            self.assertTrue(plan["safety"]["roll_in_window"])
            # Validate the plan
            self.mod.validate_rc_canary(plan, uid, aux_safety())

    def test_no_safe_frames_exits_nonzero(self):
        uid = bytes.fromhex("aabbccddeeff")
        pkt = build_rc_packet(roll=512, pitch=512, yaw=512,
                              throttle=0, armed=True)
        stamped = stamp_crc(pkt, uid, self.mod)

        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "work"
            work.mkdir()
            uid_file = work / "uid.hex"
            uid_file.write_text("aabbccddeeff")
            aux_file = work / "aux.json"
            aux_file.write_text(json.dumps(aux_safety()))
            jsonl_file = work / "frames.jsonl"
            jsonl_file.write_text(json.dumps({
                "status": "crc_valid", "type": "RC",
                "packet_bytes_hex": stamped.hex(),
                "preamble_start_seconds": 0.0,
            }) + "\n")

            original_root = self.mod.ROOT
            self.mod.ROOT = Path(tmpdir)
            try:
                rc = self.mod.main([
                    "--decoded-jsonl", str(jsonl_file),
                    "--uid-file", str(uid_file),
                    "--aux-safety-json", str(aux_file),
                ])
            finally:
                self.mod.ROOT = original_root

            self.assertEqual(rc, 1)


class PrivatePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def test_rejects_outside_work(self):
        with self.assertRaisesRegex(ValueError, "work"):
            self.mod.require_private_path(ROOT / "not-work" / "file.json")


class BuildPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load(CANARY, "elrs_v3_rc_replay_canary")

    def test_plan_structure(self):
        uid = bytes.fromhex("112233445566")
        packet = stamp_crc(build_rc_packet(roll=612), uid, self.mod)
        frame = {
            "line": 1, "source": "test",
            "preamble_start_seconds": 0.0,
            "packet_hex": packet.hex(),
            "rc": self.mod.decode_hybrid_rc(packet),
        }
        interleaver = self.mod.verify_interleaver_round_trip(packet)
        plan = self.mod.build_plan(uid, frame, interleaver, aux_safety())
        self.assertEqual(plan["schema"], "elrs-v3-rc-replay-canary-v2")
        self.assertFalse(plan["transmit_enabled"])
        self.assertTrue(plan["safety"]["disarmed"])
        self.assertTrue(plan["safety"]["pitch_yaw_centered"])
        self.assertTrue(plan["safety"]["roll_in_window"])
        self.assertTrue(plan["safety"]["rotating_aux_unused"])
        self.assertEqual(len(plan["frames"]), 1)
        self.assertTrue(plan["sync_requirements"]["fresh_sync_required"])
        self.assertEqual(plan["sync_requirements"]["switch_mode"], "hybrid")
        self.assertTrue(
            plan["sync_requirements"]["telemetry_ratio_must_be_zero"])


if __name__ == "__main__":
    unittest.main()
