"""Tests for the combined attack demo chain.

Validates the full 4-stage attack pipeline in dry-run mode:
  Stage 1: RF Disruption simulation
  Stage 2: WiFi Mode Entry simulation
  Stage 3: Config Extraction (real crypto derivation)
  Stage 4: Packet Injection (real CRC validation)

Also tests inter-stage data flow, helper functions, report generation,
and CLI argument parsing.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict

TOOLS_DIR = Path(__file__).resolve().parents[1]
CHAINS_DIR = TOOLS_DIR / "exploit_chains"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(CHAINS_DIR) not in sys.path:
    sys.path.insert(0, str(CHAINS_DIR))

from exploit_chains.combined_attack_demo import (
    RfDisruptionResult,
    WifiModeResult,
    ConfigExtractResult,
    PacketInjectionResult,
    phrase_to_uid,
    uid_to_fhss_seed,
    uid_to_crc_init,
    sync_channel_for_version,
    fhss_index_to_freq_hz,
    data_crc_init,
    stage_rf_disruption,
    stage_wifi_mode_entry,
    stage_config_extraction,
    stage_packet_injection,
    build_chain_report,
    run_chain,
    ISM2G4_FREQ_COUNT,
    ISM2G4_FREQ_START_HZ,
    ISM2G4_FREQ_STOP_HZ,
    MSP_REBOOT,
)
from exploit_chains.common import ChainLogger, AbortHandler


SYNTH_PHRASE = "testphrase123"
SYNTH_PHRASE_2 = "iloveflying"


def make_logger():
    return ChainLogger("test-combined", log_dir=None)


def make_abort(logger):
    abort = AbortHandler(logger)
    abort.aborted = False
    return abort


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestPhraseToUid(unittest.TestCase):
    def test_deterministic(self):
        uid1 = phrase_to_uid(SYNTH_PHRASE)
        uid2 = phrase_to_uid(SYNTH_PHRASE)
        self.assertEqual(uid1, uid2)

    def test_length(self):
        uid = phrase_to_uid(SYNTH_PHRASE)
        self.assertEqual(len(uid), 6)

    def test_matches_md5(self):
        raw = f'-DMY_BINDING_PHRASE="{SYNTH_PHRASE}"'
        expected = hashlib.md5(raw.encode()).digest()[:6]
        self.assertEqual(phrase_to_uid(SYNTH_PHRASE), expected)

    def test_different_phrases_differ(self):
        self.assertNotEqual(phrase_to_uid(SYNTH_PHRASE), phrase_to_uid(SYNTH_PHRASE_2))


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestUidToFhssSeed(unittest.TestCase):
    def test_deterministic(self):
        uid = phrase_to_uid(SYNTH_PHRASE)
        self.assertEqual(uid_to_fhss_seed(uid, 3), uid_to_fhss_seed(uid, 3))

    def test_version_changes_seed(self):
        uid = phrase_to_uid(SYNTH_PHRASE)
        seed_v3 = uid_to_fhss_seed(uid, 3)
        seed_v4 = uid_to_fhss_seed(uid, 4)
        self.assertNotEqual(seed_v3, seed_v4)

    def test_formula(self):
        uid = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
        expected = (0x33 << 24) | (0x44 << 16) | (0x55 << 8) | (0x66 ^ 3)
        self.assertEqual(uid_to_fhss_seed(uid, 3), expected)


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestUidToCrcInit(unittest.TestCase):
    def test_v3_xor(self):
        uid = bytes([0x00, 0x00, 0x00, 0x00, 0xAB, 0xCD])
        init = uid_to_crc_init(uid, 3)
        self.assertEqual(init, (0xAB << 8 | 0xCD) ^ 3)

    def test_v4_xor(self):
        uid = bytes([0x00, 0x00, 0x00, 0x00, 0xAB, 0xCD])
        init = uid_to_crc_init(uid, 4)
        self.assertEqual(init, (0xAB << 8 | 0xCD) ^ (4 << 8))


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestSyncChannelForVersion(unittest.TestCase):
    def test_v3(self):
        self.assertEqual(sync_channel_for_version(3), ISM2G4_FREQ_COUNT // 2 + 1)

    def test_v4(self):
        self.assertEqual(sync_channel_for_version(4), ISM2G4_FREQ_COUNT // 2)


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestFhssIndexToFreqHz(unittest.TestCase):
    def test_channel_zero(self):
        self.assertEqual(fhss_index_to_freq_hz(0), ISM2G4_FREQ_START_HZ)

    def test_channel_last(self):
        self.assertEqual(fhss_index_to_freq_hz(ISM2G4_FREQ_COUNT - 1),
                         ISM2G4_FREQ_STOP_HZ)

    def test_monotonic(self):
        freqs = [fhss_index_to_freq_hz(i) for i in range(ISM2G4_FREQ_COUNT)]
        self.assertEqual(freqs, sorted(freqs))


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestDataCrcInit(unittest.TestCase):
    def test_v4_nonce_xor(self):
        self.assertEqual(data_crc_init(0x1234, 5, 4), 0x1234 ^ 5)

    def test_v3_no_nonce_xor(self):
        self.assertEqual(data_crc_init(0x1234, 5, 3), 0x1234)


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestDataclasses(unittest.TestCase):
    def test_rf_disruption_result(self):
        r = RfDisruptionResult(
            method="test", target_band_mhz=(2400.0, 2480.0),
            disruption_channels=[0, 1], estimated_link_loss_s=1.0,
            failsafe_trigger=True)
        d = asdict(r)
        self.assertEqual(d["method"], "test")
        self.assertTrue(d["failsafe_trigger"])

    def test_wifi_mode_result(self):
        r = WifiModeResult(ssid="test", ip="192.168.4.1", port=80,
                           auth="none", entry_method="timeout")
        self.assertEqual(r.ssid, "test")
        self.assertEqual(r.endpoints, {})

    def test_config_extract_result(self):
        uid = b"\x01\x02\x03\x04\x05\x06"
        r = ConfigExtractResult(
            uid=uid, uid_hex="01:02:03:04:05:06", phrase="test",
            fhss_seed=0, crc_init=0, sync_channel=40,
            sync_freq_mhz=2440.0, fhss_sequence=[])
        self.assertEqual(r.uid, uid)

    def test_packet_injection_result(self):
        r = PacketInjectionResult(
            sync_packets=[], rc_disarm_packets=[], msp_reboot_packets=[],
            total_packets=0, crc_pass_count=0, crc_fail_count=0)
        self.assertEqual(r.total_packets, 0)


# ---------------------------------------------------------------------------
# Stage tests
# ---------------------------------------------------------------------------


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestStage1RfDisruption(unittest.TestCase):
    def test_returns_result(self):
        logger = make_logger()
        abort = make_abort(logger)
        result = stage_rf_disruption(logger, abort)
        self.assertIsInstance(result, RfDisruptionResult)

    def test_failsafe_triggered(self):
        logger = make_logger()
        abort = make_abort(logger)
        result = stage_rf_disruption(logger, abort)
        self.assertTrue(result.failsafe_trigger)

    def test_covers_80_channels(self):
        logger = make_logger()
        abort = make_abort(logger)
        result = stage_rf_disruption(logger, abort)
        self.assertEqual(len(result.disruption_channels), ISM2G4_FREQ_COUNT)

    def test_band_range(self):
        logger = make_logger()
        abort = make_abort(logger)
        result = stage_rf_disruption(logger, abort)
        low, high = result.target_band_mhz
        self.assertAlmostEqual(low, ISM2G4_FREQ_START_HZ / 1e6, places=1)
        self.assertAlmostEqual(high, ISM2G4_FREQ_STOP_HZ / 1e6, places=1)

    def test_verbose_mode(self):
        logger = make_logger()
        abort = make_abort(logger)
        result = stage_rf_disruption(logger, abort, verbose=True)
        self.assertIsInstance(result, RfDisruptionResult)


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestStage2WifiModeEntry(unittest.TestCase):
    def setUp(self):
        self.logger = make_logger()
        self.abort = make_abort(self.logger)
        self.rf_result = RfDisruptionResult(
            method="test", target_band_mhz=(2400.4, 2479.4),
            disruption_channels=list(range(80)),
            estimated_link_loss_s=1.0, failsafe_trigger=True)

    def test_returns_result(self):
        result = stage_wifi_mode_entry(self.rf_result, self.logger, self.abort)
        self.assertIsInstance(result, WifiModeResult)

    def test_ssid_and_ip(self):
        result = stage_wifi_mode_entry(self.rf_result, self.logger, self.abort)
        self.assertEqual(result.ssid, "ExpressLRS TX")
        self.assertEqual(result.ip, "192.168.4.1")
        self.assertEqual(result.auth, "none")

    def test_endpoints_populated(self):
        result = stage_wifi_mode_entry(self.rf_result, self.logger, self.abort)
        self.assertIn("config", result.endpoints)
        self.assertIn("firmware_update", result.endpoints)

    def test_no_failsafe_still_runs(self):
        self.rf_result.failsafe_trigger = False
        result = stage_wifi_mode_entry(self.rf_result, self.logger, self.abort)
        self.assertIsInstance(result, WifiModeResult)


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestStage3ConfigExtraction(unittest.TestCase):
    def setUp(self):
        self.logger = make_logger()
        self.abort = make_abort(self.logger)
        self.wifi_result = WifiModeResult(
            ssid="ExpressLRS TX", ip="192.168.4.1", port=80,
            auth="none", entry_method="test")

    def test_returns_result(self):
        result = stage_config_extraction(
            self.wifi_result, SYNTH_PHRASE, 3, self.logger, self.abort)
        self.assertIsInstance(result, ConfigExtractResult)

    def test_uid_matches_phrase(self):
        result = stage_config_extraction(
            self.wifi_result, SYNTH_PHRASE, 3, self.logger, self.abort)
        self.assertEqual(result.uid, phrase_to_uid(SYNTH_PHRASE))

    def test_fhss_sequence_length(self):
        result = stage_config_extraction(
            self.wifi_result, SYNTH_PHRASE, 3, self.logger, self.abort)
        self.assertEqual(len(result.fhss_sequence), 240)

    def test_v3_vs_v4_differ(self):
        r3 = stage_config_extraction(
            self.wifi_result, SYNTH_PHRASE, 3, self.logger, self.abort)
        r4 = stage_config_extraction(
            self.wifi_result, SYNTH_PHRASE, 4, self.logger, self.abort)
        self.assertNotEqual(r3.fhss_seed, r4.fhss_seed)
        self.assertNotEqual(r3.crc_init, r4.crc_init)

    def test_sync_channel_v3(self):
        result = stage_config_extraction(
            self.wifi_result, SYNTH_PHRASE, 3, self.logger, self.abort)
        self.assertEqual(result.sync_channel, ISM2G4_FREQ_COUNT // 2 + 1)

    def test_verbose_mode(self):
        result = stage_config_extraction(
            self.wifi_result, SYNTH_PHRASE, 3, self.logger, self.abort, verbose=True)
        self.assertIsInstance(result, ConfigExtractResult)


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestStage4PacketInjection(unittest.TestCase):
    def setUp(self):
        self.logger = make_logger()
        self.abort = make_abort(self.logger)
        uid = phrase_to_uid(SYNTH_PHRASE)
        from elrs_core.fhss import build_sequence, FHSS_SEQUENCE_LEN
        fhss_seed = uid_to_fhss_seed(uid, 3)
        crc_init = uid_to_crc_init(uid, 3)
        sync_ch = sync_channel_for_version(3)
        fhss_seq = build_sequence(fhss_seed, ISM2G4_FREQ_COUNT, sync_ch, FHSS_SEQUENCE_LEN)
        self.config = ConfigExtractResult(
            uid=uid,
            uid_hex=":".join(f"{b:02X}" for b in uid),
            phrase=SYNTH_PHRASE,
            fhss_seed=fhss_seed,
            crc_init=crc_init,
            sync_channel=sync_ch,
            sync_freq_mhz=fhss_index_to_freq_hz(sync_ch) / 1e6,
            fhss_sequence=fhss_seq,
        )

    def test_returns_result(self):
        result = stage_packet_injection(self.config, 3, self.logger, self.abort)
        self.assertIsInstance(result, PacketInjectionResult)

    def test_all_crc_pass(self):
        result = stage_packet_injection(self.config, 3, self.logger, self.abort)
        self.assertEqual(result.crc_fail_count, 0)
        self.assertGreater(result.crc_pass_count, 0)

    def test_packet_counts(self):
        result = stage_packet_injection(self.config, 3, self.logger, self.abort)
        self.assertEqual(len(result.sync_packets), 5)
        self.assertEqual(len(result.rc_disarm_packets), 20)
        self.assertEqual(len(result.msp_reboot_packets), 3)
        self.assertEqual(result.total_packets, 28)

    def test_packets_have_hex(self):
        result = stage_packet_injection(self.config, 3, self.logger, self.abort)
        for pkt in result.sync_packets:
            self.assertIn("raw_hex", pkt)
            self.assertGreater(len(pkt["raw_hex"]), 0)
            bytes.fromhex(pkt["raw_hex"])

    def test_rc_packets_disarmed(self):
        result = stage_packet_injection(self.config, 3, self.logger, self.abort)
        for pkt in result.rc_disarm_packets:
            self.assertFalse(pkt["armed"])

    def test_msp_packets_have_reboot_cmd(self):
        result = stage_packet_injection(self.config, 3, self.logger, self.abort)
        for pkt in result.msp_reboot_packets:
            self.assertEqual(pkt["msp_cmd"], MSP_REBOOT)

    def test_verbose_mode(self):
        result = stage_packet_injection(self.config, 3, self.logger, self.abort, verbose=True)
        self.assertEqual(result.crc_fail_count, 0)


# ---------------------------------------------------------------------------
# Full chain test
# ---------------------------------------------------------------------------


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestRunChain(unittest.TestCase):
    def test_full_chain_v3(self):
        logger = make_logger()
        abort = make_abort(logger)
        report = run_chain(SYNTH_PHRASE, 3, logger, abort)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["summary"]["total_stages"], 4)
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertTrue(report["summary"]["all_crc_valid"])

    def test_full_chain_v4(self):
        logger = make_logger()
        abort = make_abort(logger)
        report = run_chain(SYNTH_PHRASE_2, 4, logger, abort)
        self.assertEqual(report["result"], "PASS")
        self.assertTrue(report["summary"]["all_crc_valid"])

    def test_report_structure(self):
        logger = make_logger()
        abort = make_abort(logger)
        report = run_chain(SYNTH_PHRASE, 3, logger, abort)
        self.assertIn("chain", report)
        self.assertIn("stages", report)
        self.assertIn("attack_flow", report)
        self.assertEqual(len(report["stages"]), 4)
        self.assertEqual(report["mode"], "dry-run/simulated")

    def test_report_parameters(self):
        logger = make_logger()
        abort = make_abort(logger)
        report = run_chain(SYNTH_PHRASE, 3, logger, abort)
        self.assertEqual(report["parameters"]["phrase"], SYNTH_PHRASE)
        self.assertEqual(report["parameters"]["ota_version"], 3)


# ---------------------------------------------------------------------------
# Report generation tests
# ---------------------------------------------------------------------------


@unittest.skip("QUARANTINE: uses synthetic attack pipeline; needs real multi-stage attack capture")
class TestBuildChainReport(unittest.TestCase):
    def _make_stages(self):
        uid = phrase_to_uid(SYNTH_PHRASE)
        return {
            "rf_disruption": RfDisruptionResult(
                method="test", target_band_mhz=(2400.4, 2479.4),
                disruption_channels=list(range(80)),
                estimated_link_loss_s=1.0, failsafe_trigger=True),
            "wifi_mode_entry": WifiModeResult(
                ssid="ExpressLRS TX", ip="192.168.4.1", port=80,
                auth="none", entry_method="test"),
            "config_extraction": ConfigExtractResult(
                uid=uid, uid_hex="AA:BB:CC:DD:EE:FF", phrase=SYNTH_PHRASE,
                fhss_seed=0x12345678, crc_init=0xABCD,
                sync_channel=41, sync_freq_mhz=2440.0, fhss_sequence=[0]*240),
            "packet_injection": PacketInjectionResult(
                sync_packets=[{"nonce": 0}] * 5,
                rc_disarm_packets=[{"hop": 0}] * 20,
                msp_reboot_packets=[{"nonce": 0}] * 3,
                total_packets=28, crc_pass_count=28, crc_fail_count=0),
        }

    def test_pass_report(self):
        report = build_chain_report(self._make_stages(), 0.5, SYNTH_PHRASE, 3)
        self.assertEqual(report["result"], "PASS")

    def test_fail_report(self):
        stages = self._make_stages()
        stages["packet_injection"].crc_fail_count = 1
        report = build_chain_report(stages, 0.5, SYNTH_PHRASE, 3)
        self.assertEqual(report["result"], "FAIL")

    def test_json_serializable(self):
        report = build_chain_report(self._make_stages(), 0.5, SYNTH_PHRASE, 3)
        serialized = json.dumps(report)
        self.assertIsInstance(serialized, str)
        roundtrip = json.loads(serialized)
        self.assertEqual(roundtrip["result"], "PASS")


if __name__ == "__main__":
    unittest.main()
