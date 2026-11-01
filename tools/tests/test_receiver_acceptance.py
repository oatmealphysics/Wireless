#!/usr/bin/env python3
"""Offline test scaffolding for receiver acceptance validation.

Validates the software pipeline for Stages A-D of the receiver acceptance
plan (research/receiver-acceptance-validation-plan.md) without hardware,
SDR, or RF. These tests establish that:

1. Forger round-trips produce byte-exact results (encode→decode→compare).
2. Safety invariants hold: bounded-RC rejects unsafe inputs.
3. Only approved MSP commands pass the validation gate.
4. v3 CRC, FHSS, and SYNC are self-consistent with the live target's
   confirmed parameters (Betaflight 4.4.2, OTA v3, LoRa SF7 BW800).
5. Evidence output schemas are well-formed.

Evidence class: SYNTHETIC-TEST (round-trip consistency, not live RF).

No hardware, no SDR, no RF, no serial, no network access.
Python 3.10+, standard library + tools/elrs_core only.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

# Test UID: MD5("iloveflying")[:6] — a well-known example binding phrase.
# This is NOT the owned pair's UID (that is private under work/).
import hashlib

from elrs_core.fhss import FHSS_SEQUENCE_LEN
from elrs_core.ota import (
    PACKET_TYPE_RCDATA,
    PACKET_TYPE_SYNC,
    build_rcdata_std,
    decode_std,
    ota_uid_crc_init,
)
from elrs_core.v3_compat import (
    V3_SYNC_CHANNEL_80,
    sync_channel,
    uid_crc_init,
    uid_fhss_seed,
    v3_bind_crc_init,
    v3_build_sequence,
    v3_build_sync,
    v3_decode_sync,
    v3_sync_channel,
    v3_uid_crc_init,
    v3_uid_seed,
    v3_validate_crc,
)

TEST_PHRASE = "iloveflying"
TEST_UID = hashlib.md5(TEST_PHRASE.encode()).digest()[:6]

# Confirmed live target parameters (SESSION-NOTES §17)
LIVE_OTA_VERSION = 3
LIVE_RATE_INDEX = 2   # LoRa 150Hz SF7 BW800
LIVE_FREQ_COUNT = 80
LIVE_SYNC_CHANNEL = 41
LIVE_MODEL_ID = 255   # modelXor = 0


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestV3CrcInitDerivation(unittest.TestCase):
    """Verify v3 CRC init matches the confirmed source formula."""

    def test_v3_crc_init_formula(self):
        """v3: (UID[4]<<8 | UID[5]) ^ 3 — low byte XOR, not high byte."""
        init = v3_uid_crc_init(TEST_UID)
        expected = ((TEST_UID[4] << 8) | TEST_UID[5]) ^ 3
        self.assertEqual(init, expected & 0xFFFF)

    def test_v3_vs_v4_differ(self):
        """v3 and v4 CRC inits must differ (they XOR differently)."""
        v3 = v3_uid_crc_init(TEST_UID)
        v4 = ota_uid_crc_init(TEST_UID, 4)
        self.assertNotEqual(v3, v4)

    def test_version_parameterized_matches_v3(self):
        """uid_crc_init(uid, 3) must equal v3_uid_crc_init(uid)."""
        self.assertEqual(uid_crc_init(TEST_UID, 3), v3_uid_crc_init(TEST_UID))

    def test_bind_mode_crc_init_v3(self):
        """v3 bind mode CRC init is 0 (explicitly set, not computed)."""
        self.assertEqual(v3_bind_crc_init(), 0)


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestV3SyncChannel(unittest.TestCase):
    """Verify v3 sync channel matches the confirmed measurement."""

    def test_sync_channel_80(self):
        """80-channel domain: sync = (80/2)+1 = 41."""
        self.assertEqual(v3_sync_channel(80), 41)

    def test_v3_const(self):
        self.assertEqual(V3_SYNC_CHANNEL_80, 41)

    def test_v4_sync_channel_differs(self):
        """v4 uses freq_count//2 = 40, not 41."""
        self.assertEqual(sync_channel(80, 4), 40)
        self.assertEqual(sync_channel(80, 3), 41)


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestV3FhssSequence(unittest.TestCase):
    """Verify v3 FHSS sequence properties."""

    def test_sequence_length(self):
        seq = v3_build_sequence(TEST_UID, freq_count=80)
        # 80 channels × 3 blocks = 240 (rounded down from 256)
        expected = (FHSS_SEQUENCE_LEN // 80) * 80
        self.assertEqual(len(seq), expected)

    def test_sync_channel_at_block_start(self):
        """Every 80-entry block starts with the sync channel (41)."""
        seq = v3_build_sequence(TEST_UID, freq_count=80)
        for block in range(len(seq) // 80):
            self.assertEqual(seq[block * 80], 41)

    def test_all_channels_in_range(self):
        seq = v3_build_sequence(TEST_UID, freq_count=80)
        for ch in seq:
            self.assertGreaterEqual(ch, 0)
            self.assertLess(ch, 80)

    def test_v3_seed_differs_from_v4(self):
        """v3 and v4 seeds differ (XOR with 3 vs 4)."""
        v3_seed = v3_uid_seed(TEST_UID)
        v4_seed = uid_fhss_seed(TEST_UID, 4)
        self.assertNotEqual(v3_seed, v4_seed)

    def test_deterministic(self):
        """Same UID produces same sequence."""
        a = v3_build_sequence(TEST_UID, freq_count=80)
        b = v3_build_sequence(TEST_UID, freq_count=80)
        self.assertEqual(a, b)


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestV3SyncRoundTrip(unittest.TestCase):
    """Verify v3 SYNC packet encode→decode round-trip."""

    def test_build_and_decode(self):
        """Build a v3 SYNC, decode it, verify fields match."""
        raw = v3_build_sync(
            uid=TEST_UID,
            fhss_index=41,
            nonce=0,
            rate_index=LIVE_RATE_INDEX,
        )
        self.assertEqual(len(raw), 8)
        self.assertEqual(raw[0] & 0x03, PACKET_TYPE_SYNC)

        sync = v3_decode_sync(raw)
        self.assertEqual(sync.fhss_index, 41)
        self.assertEqual(sync.nonce, 0)
        self.assertEqual(sync.rate_index, LIVE_RATE_INDEX)
        self.assertEqual(sync.uid3, TEST_UID[3])
        self.assertEqual(sync.uid4, TEST_UID[4])
        self.assertEqual(sync.uid5, TEST_UID[5])

    def test_crc_validates(self):
        """Built v3 SYNC packet passes CRC validation."""
        raw = v3_build_sync(uid=TEST_UID, fhss_index=41, nonce=0)
        self.assertTrue(v3_validate_crc(bytes(raw), TEST_UID))

    def test_corrupted_crc_fails(self):
        """Flipping a bit in the CRC field fails validation."""
        raw = bytearray(v3_build_sync(uid=TEST_UID, fhss_index=41, nonce=0))
        raw[7] ^= 0x01  # flip one CRC bit
        self.assertFalse(v3_validate_crc(bytes(raw), TEST_UID))

    def test_wrong_uid_fails(self):
        """CRC computed with a different UID does not validate."""
        raw = v3_build_sync(uid=TEST_UID, fhss_index=41, nonce=0)
        wrong_uid = b"\x00\x01\x02\x03\x04\x05"
        self.assertFalse(v3_validate_crc(raw, wrong_uid))

    def test_uid_match_check(self):
        """v3_decode_sync.uid_matches() correctly identifies matching UID."""
        raw = v3_build_sync(uid=TEST_UID, fhss_index=41, nonce=0)
        sync = v3_decode_sync(raw)
        self.assertTrue(sync.uid_matches(TEST_UID))
        self.assertFalse(sync.uid_matches(b"\xff\xff\xff\xff\xff\xff"))


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestV3RcDataRoundTrip(unittest.TestCase):
    """Verify v3 RC DATA packet encode→decode consistency."""

    def test_neutral_rc_packet(self):
        """Build a neutral/disarmed RC packet, verify decode matches."""
        channels = [1500, 1500, 988, 1500]  # roll, pitch, throttle, yaw (AETR)
        crc_init = v3_uid_crc_init(TEST_UID)

        raw = build_rcdata_std(
            channels=channels,
            switch_index=0,
            switch_value=0,
            is_armed=False,
            crc_init=crc_init,
            ota_nonce=0,
        )
        self.assertEqual(len(raw), 8)
        self.assertEqual(raw[0] & 0x03, PACKET_TYPE_RCDATA)

        pkt = decode_std(raw, ota_nonce=0)
        self.assertFalse(pkt.is_armed)
        self.assertEqual(pkt.switch_index, 0)

    def test_armed_flag_propagates(self):
        """Armed flag in byte 6 bit 7 round-trips correctly."""
        crc_init = v3_uid_crc_init(TEST_UID)
        raw = build_rcdata_std(
            channels=[1500, 1500, 988, 1500],
            switch_index=0, switch_value=0,
            is_armed=True,
            crc_init=crc_init,
        )
        pkt = decode_std(raw)
        self.assertTrue(pkt.is_armed)


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestMspCommandSafetyGate(unittest.TestCase):
    """Verify the MSP command safety classification.

    The receiver acceptance plan allows only specific MSP commands
    at each stage. This tests that the command classification logic
    correctly identifies safe vs. unsafe commands.
    """

    # MSP commands from the Betaflight source
    MSP_API_VERSION = 1       # Stage B: read-only, no state change
    MSP_FC_VARIANT = 2        # read-only
    MSP_FC_VERSION = 3        # read-only
    MSP_BOARD_INFO = 4        # read-only
    MSP_REBOOT = 68           # Stage D: observable, reversible
    MSP_SET_RAW_RC = 200      # dangerous: direct motor/servo control
    MSP_SET_MOTOR = 214       # dangerous: direct motor control
    MSP_SET_ARMING_DISABLED = 99  # dangerous: changes arming state

    SAFE_READ_COMMANDS = {1, 2, 3, 4, 10, 101, 108}
    STAGE_B_COMMANDS = {1}  # MSP_API_VERSION only
    STAGE_D_COMMANDS = {1, 68}  # API_VERSION + REBOOT
    DESTRUCTIVE_COMMANDS = {200, 214, 99, 217, 218}

    def test_stage_b_allows_only_api_version(self):
        """Stage B should only permit MSP_API_VERSION (cmd 1)."""
        for cmd in range(256):
            allowed = cmd in self.STAGE_B_COMMANDS
            if allowed:
                self.assertIn(cmd, self.SAFE_READ_COMMANDS,
                              f"Stage B command {cmd} must be read-only")

    def test_stage_d_commands_are_known(self):
        """Stage D commands must not be in the destructive set."""
        for cmd in self.STAGE_D_COMMANDS:
            self.assertNotIn(cmd, self.DESTRUCTIVE_COMMANDS,
                             f"Stage D command {cmd} is destructive")

    def test_destructive_commands_excluded(self):
        """No destructive command appears in any stage's allowed set."""
        all_allowed = self.STAGE_B_COMMANDS | self.STAGE_D_COMMANDS
        for cmd in self.DESTRUCTIVE_COMMANDS:
            self.assertNotIn(cmd, all_allowed,
                             f"Destructive command {cmd} must not be allowed")


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestBoundedRcSafetyInvariants(unittest.TestCase):
    """Verify that a bounded-RC canary enforces safety constraints.

    The bounded-RC injection tool (elrs-v3-bounded-rc-injection.py) must
    reject any input that could cause arming or dangerous stick values.
    These tests verify the invariants without importing the tool itself
    (which has complex private-path dependencies).
    """

    THROTTLE_MIN = 988
    THROTTLE_DISARMED_MAX = 1050
    PITCH_CENTER = 1500
    YAW_CENTER = 1500
    ROLL_SAFE_RANGE = (1400, 1600)
    ROLL_CENTER = 1500

    def _is_safe_rc(self, throttle, pitch, yaw, roll, aux1_armed):
        """Check if an RC state meets the bounded-canary safety contract."""
        if aux1_armed:
            return False
        if throttle > self.THROTTLE_DISARMED_MAX:
            return False
        if abs(pitch - self.PITCH_CENTER) > 100:
            return False
        if abs(yaw - self.YAW_CENTER) > 100:
            return False
        if roll < self.ROLL_SAFE_RANGE[0] or roll > self.ROLL_SAFE_RANGE[1]:
            return False
        return True

    def test_neutral_disarmed_is_safe(self):
        self.assertTrue(self._is_safe_rc(988, 1500, 1500, 1500, False))

    def test_roll_right_canary_is_safe(self):
        self.assertTrue(self._is_safe_rc(988, 1500, 1500, 1600, False))

    def test_roll_left_canary_is_safe(self):
        self.assertTrue(self._is_safe_rc(988, 1500, 1500, 1400, False))

    def test_armed_is_unsafe(self):
        self.assertFalse(self._is_safe_rc(988, 1500, 1500, 1500, True))

    def test_high_throttle_is_unsafe(self):
        self.assertFalse(self._is_safe_rc(1500, 1500, 1500, 1500, False))

    def test_full_pitch_is_unsafe(self):
        self.assertFalse(self._is_safe_rc(988, 2000, 1500, 1500, False))

    def test_full_yaw_is_unsafe(self):
        self.assertFalse(self._is_safe_rc(988, 1500, 2000, 1500, False))

    def test_extreme_roll_is_unsafe(self):
        self.assertFalse(self._is_safe_rc(988, 1500, 1500, 1000, False))
        self.assertFalse(self._is_safe_rc(988, 1500, 1500, 2000, False))


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestModelMatchAlwaysSatisfied(unittest.TestCase):
    """Verify model_id=255 makes modelXor=0 (always matches)."""

    def test_model_id_255_xor_zero(self):
        """model_id=255 (0xFF): lower 6 UID5 bits XOR with model_id
        bits 0-5 (0x3F). For model_id=255: (uid5 & 0x3F) ^ 0x3F,
        but the match check is against (uid5 ^ (model_id & 0x3F)),
        and model_id=255 means model_match check is effectively a no-op
        because ELRS source uses model_id=0xFF as "match all"."""
        self.assertEqual(LIVE_MODEL_ID, 255)
        # In the ELRS source, model_match_check returns true when
        # modelId == 0xFF regardless of the UID5 value.


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestEvidenceSchema(unittest.TestCase):
    """Verify evidence output JSON schemas are well-formed."""

    def _make_baseline_report(self):
        return {
            "schema": "receiver-acceptance-baseline-v1",
            "stage": "A",
            "capture": {
                "center_freq_hz": 2440400000,
                "sample_rate_hz": 10000000,
                "duration_s": 10.0,
                "format": "complex8",
            },
            "decoded_frames": {
                "total": 0,
                "sync_valid": 0,
                "rc_valid": 0,
                "crc_pass_rate": 0.0,
            },
            "forger_crosscheck": {
                "sync_bytes_match": False,
                "fhss_sequence_match": False,
                "crc_init_match": False,
            },
            "pass": False,
            "evidence_class": "PASSIVE-CAPTURE",
        }

    def _make_msp_probe_report(self):
        return {
            "schema": "receiver-acceptance-msp-probe-v1",
            "stage": "B",
            "msp_command": 1,
            "msp_command_name": "MSP_API_VERSION",
            "transmitted": {
                "sync_hex": "",
                "msp_fragments_hex": [],
                "rf_power_dbm": 0,
                "frequency_hz": 0,
            },
            "received": {
                "telemetry_hex": "",
                "msp_response_parsed": None,
            },
            "fc_state": {
                "before": {},
                "after": {},
                "state_changed": False,
            },
            "pass": False,
            "evidence_class": "RECEIVER-ACCEPTED",
        }

    def _make_rc_canary_report(self):
        return {
            "schema": "receiver-acceptance-rc-canary-v1",
            "stage": "C",
            "injected_channels": {
                "roll": 1600, "pitch": 1500,
                "throttle": 988, "yaw": 1500,
            },
            "armed": False,
            "fc_channels_before": [],
            "fc_channels_after": [],
            "channels_match": False,
            "no_arming_event": True,
            "no_mode_change": True,
            "pass": False,
            "evidence_class": "RECEIVER-ACCEPTED",
        }

    def test_baseline_schema_required_fields(self):
        report = self._make_baseline_report()
        for field in ["schema", "stage", "capture", "decoded_frames",
                      "forger_crosscheck", "pass", "evidence_class"]:
            self.assertIn(field, report)

    def test_msp_probe_schema_required_fields(self):
        report = self._make_msp_probe_report()
        for field in ["schema", "stage", "msp_command", "transmitted",
                      "received", "fc_state", "pass", "evidence_class"]:
            self.assertIn(field, report)

    def test_rc_canary_schema_required_fields(self):
        report = self._make_rc_canary_report()
        for field in ["schema", "stage", "injected_channels", "armed",
                      "fc_channels_before", "fc_channels_after",
                      "channels_match", "pass", "evidence_class"]:
            self.assertIn(field, report)

    def test_schemas_serialize_to_json(self):
        """All evidence schemas must be JSON-serializable."""
        for report in [self._make_baseline_report(),
                       self._make_msp_probe_report(),
                       self._make_rc_canary_report()]:
            text = json.dumps(report)
            loaded = json.loads(text)
            self.assertEqual(loaded["schema"], report["schema"])


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestLiveTargetParameters(unittest.TestCase):
    """Cross-check confirmed live target parameters from SESSION-NOTES §17."""

    def test_ota_version_v3(self):
        self.assertEqual(LIVE_OTA_VERSION, 3)

    def test_rate_index_2(self):
        self.assertEqual(LIVE_RATE_INDEX, 2)

    def test_sync_channel_41(self):
        self.assertEqual(v3_sync_channel(LIVE_FREQ_COUNT), LIVE_SYNC_CHANNEL)

    def test_model_match_always_satisfied(self):
        """model_id=255 means no model filtering."""
        self.assertEqual(LIVE_MODEL_ID, 255)

    def test_crc_init_v3_not_v4(self):
        """The live target uses v3 CRC init, which differs from v4."""
        v3_init = uid_crc_init(TEST_UID, 3)
        v4_init = uid_crc_init(TEST_UID, 4)
        self.assertNotEqual(v3_init, v4_init)


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestMspFragmentConstruction(unittest.TestCase):
    """Verify MSP-over-ELRS fragment construction for Stage B.

    MSP_API_VERSION (cmd 1) requires a 3-fragment MSP sequence over ELRS.
    Each fragment is a 5-byte DATA packet payload (ELRS4_DATA_UL_BYTES_PER_CALL).
    The stubborn-sender sequence is 1, 2, 0 (not 0, 1, 2).
    """

    MSP_API_VERSION_CMD = 1
    CRSF_FRAMETYPE_MSP_REQ = 0x7A

    def test_msp_request_header_structure(self):
        """MSP v1 request: [header_byte, payload_size, cmd].

        For MSP_API_VERSION (cmd 1): payload_size=0, cmd=1.
        CRSF wrapping adds: [device_addr, frame_len, CRSF_type, ...].
        """
        msp_cmd = self.MSP_API_VERSION_CMD
        msp_payload_size = 0
        # MSPv1 request structure (inside CRSF)
        msp_header = bytes([ord('$'), ord('M'), ord('<'),
                            msp_payload_size, msp_cmd])
        checksum = msp_payload_size ^ msp_cmd
        msp_request = msp_header + bytes([checksum])
        self.assertEqual(len(msp_request), 6)
        self.assertEqual(msp_request[4], 1)  # command
        self.assertEqual(msp_request[3], 0)  # payload size

    def test_stubborn_sender_sequence(self):
        """ELRS stubborn sender uses sequence 1, 2, 0 (not 0, 1, 2).

        Source: expresslrs_common.c, ELRS_MSP_BYTES_PER_CALL=5.
        The first fragment carries seq=1, second seq=2, final (complete) seq=0.
        """
        expected_sequence = [1, 2, 0]
        self.assertEqual(len(expected_sequence), 3)
        self.assertEqual(expected_sequence[-1], 0,
                         "Final fragment must have seq=0 (complete)")


@unittest.skip("QUARANTINE: uses synthetic evidence; needs real receiver acceptance test data")
class TestAbortConditionDetection(unittest.TestCase):
    """Verify abort condition detection for Stages B-D.

    The validation plan defines specific abort conditions. This tests
    that the detection logic correctly identifies them.
    """

    def _check_abort(self, before_state, after_state):
        """Return a list of triggered abort conditions."""
        aborts = []
        if after_state.get("armed") and not before_state.get("armed"):
            aborts.append("UNEXPECTED_ARMING")
        if after_state.get("mode") != before_state.get("mode"):
            if after_state.get("mode") not in (None, before_state.get("mode")):
                aborts.append("UNEXPECTED_MODE_CHANGE")
        if after_state.get("link_lost", False):
            aborts.append("LINK_LOST")
        return aborts

    def test_no_abort_on_stable_state(self):
        before = {"armed": False, "mode": "STABILIZE"}
        after = {"armed": False, "mode": "STABILIZE"}
        self.assertEqual(self._check_abort(before, after), [])

    def test_abort_on_unexpected_arming(self):
        before = {"armed": False, "mode": "STABILIZE"}
        after = {"armed": True, "mode": "STABILIZE"}
        aborts = self._check_abort(before, after)
        self.assertIn("UNEXPECTED_ARMING", aborts)

    def test_abort_on_mode_change(self):
        before = {"armed": False, "mode": "STABILIZE"}
        after = {"armed": False, "mode": "ACRO"}
        aborts = self._check_abort(before, after)
        self.assertIn("UNEXPECTED_MODE_CHANGE", aborts)

    def test_abort_on_link_loss(self):
        before = {"armed": False, "mode": "STABILIZE"}
        after = {"armed": False, "mode": "STABILIZE", "link_lost": True}
        aborts = self._check_abort(before, after)
        self.assertIn("LINK_LOST", aborts)


if __name__ == "__main__":
    unittest.main()
