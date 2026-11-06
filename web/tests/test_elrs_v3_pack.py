"""Offline fixture tests for the ELRS v3 OTA Injection Capability Pack.

Exercises the pack's metadata, UID derivation, FHSS computation, CRC init,
frame construction, applicability rejection, and headless pipeline integration
using canned data.  No hardware probe, RF transmission, serial access, or
external network activity.

Known-good fixture values are independently computed:
  binding phrase : "test_phrase"
  UID            : f7:ac:a7:d7:75:0e  (MD5 of '-DMY_BINDING_PHRASE="test_phrase"')
  v3 FHSS seed  : 0xa7d7750d
  v3 CRC init   : 0x750d
  v3 sync channel: 41
  FHSS seq len  : 240
  FHSS seq[0]   : 41  (sync channel)
  FHSS seq[1]   : 79
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path

# Ensure web/ and tools/ are on sys.path for imports
WEB_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = WEB_DIR.parent
TOOLS_DIR = REPO_ROOT / "tools"
for p in (str(WEB_DIR), str(TOOLS_DIR), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Known-good fixture constants (independently computed, not from the pack)
# ---------------------------------------------------------------------------
FIXTURE_PHRASE = "test_phrase"
FIXTURE_UID = bytes.fromhex("f7aca7d7750e")
FIXTURE_V3_SEED = 0xA7D7750D
FIXTURE_V3_CRC_INIT = 0x750D
FIXTURE_V3_SYNC_CH = 41
FIXTURE_FHSS_LEN = 240
FIXTURE_FHSS_FIRST_20 = [41, 79, 7, 73, 0, 43, 18, 14, 50, 72,
                          45, 71, 51, 17, 53, 33, 39, 6, 69, 44]
FIXTURE_RC_FRAME_HEX = "c4000008208000d6"


# ===========================================================================
# Seam 1: Pack metadata
# ===========================================================================

class TestPackIdentityMetadata(unittest.TestCase):
    """Pack identity returns correct ELRS v3 injection metadata."""

    def test_pack_name(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        self.assertEqual(pack.identity.name, "ELRS v3 OTA Injection")

    def test_pack_version(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        self.assertEqual(pack.identity.version, "0.1.0")


class TestPackApplicabilityBoundary(unittest.TestCase):
    """Applicability boundary matches ELRS 2.4 GHz v3 on BF 4.4.2."""

    def test_protocol_version(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        self.assertEqual(pack.applicability.protocol_version, "ELRS v3 OTA")

    def test_phy_domain(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        self.assertEqual(pack.applicability.phy_domain, "2.4 GHz LoRa SX1280")

    def test_firmware(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        self.assertEqual(pack.applicability.firmware_impl, "Betaflight")
        self.assertEqual(pack.applicability.firmware_version, "4.4.2")


class TestPackPrerequisites(unittest.TestCase):
    """Prerequisites declare UID/binding phrase with acceptable modes."""

    def test_has_uid_prerequisite(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        names = {p.name for p in pack.prerequisites}
        self.assertIn("target_uid", names)

    def test_has_binding_phrase_prerequisite(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        names = {p.name for p in pack.prerequisites}
        self.assertIn("binding_phrase", names)

    def test_prerequisite_modes_are_valid(self) -> None:
        from capability_pack import PrerequisiteMode
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        for p in pack.prerequisites:
            self.assertIsInstance(p.mode, PrerequisiteMode)


class TestPackEffects(unittest.TestCase):
    """Effects include non-destructive RC override."""

    def test_rc_override_effect_exists(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        names = {e.name for e in pack.effects}
        self.assertIn("rc_override", names)

    def test_rc_override_is_non_destructive(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        rc = [e for e in pack.effects if e.name == "rc_override"][0]
        self.assertFalse(rc.destructive)

    def test_pack_has_no_destructive_effects(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        self.assertFalse(pack.has_destructive_effects)


class TestPackHardwareRequirements(unittest.TestCase):
    """B205mini and B210 declared as selectable SDR options."""

    def test_b205mini_declared(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        names = {h.name for h in pack.hardware_requirements}
        self.assertIn("Ettus B205mini", names)

    def test_b210_declared(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        names = {h.name for h in pack.hardware_requirements}
        self.assertIn("Ettus B210", names)


class TestPackFalsifiers(unittest.TestCase):
    """Pack declares meaningful falsifiers."""

    def test_has_falsifiers(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        self.assertGreater(len(pack.falsifiers), 0)

    def test_falsifiers_have_conditions(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        for f in pack.falsifiers:
            self.assertTrue(len(f.condition) > 0)


# ===========================================================================
# Seam 2: Registry integration + applicability rejection
# ===========================================================================

class TestRegistryIntegration(unittest.TestCase):
    """Pack registers and is queryable by matching targets."""

    def test_register_and_lookup(self) -> None:
        from capability_pack import PackRegistry
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        registry = PackRegistry()
        pack = ELRSv3OTAInjectionPack()
        registry.register(pack)
        found = registry.lookup("ELRS v3 OTA Injection", "0.1.0")
        self.assertIs(found, pack)

    def test_query_matching_elrs_v3_target(self) -> None:
        from capability_pack import PackRegistry
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        registry = PackRegistry()
        pack = ELRSv3OTAInjectionPack()
        registry.register(pack)
        results = registry.query({
            "protocol_version": "ELRS v3 OTA",
            "firmware_impl": "Betaflight",
            "firmware_version": "4.4.2",
        })
        self.assertEqual(len(results), 1)
        self.assertIs(results[0], pack)

    def test_query_rejects_non_elrs_target(self) -> None:
        from capability_pack import PackRegistry
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        registry = PackRegistry()
        registry.register(ELRSv3OTAInjectionPack())
        results = registry.query({
            "protocol_version": "MAVLink v2",
            "firmware_impl": "ArduPilot",
            "firmware_version": "4.7.2",
        })
        self.assertEqual(len(results), 0)

    def test_query_rejects_wrong_elrs_version(self) -> None:
        from capability_pack import PackRegistry
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        registry = PackRegistry()
        registry.register(ELRSv3OTAInjectionPack())
        results = registry.query({
            "protocol_version": "ELRS v4 OTA",
            "firmware_impl": "Betaflight",
            "firmware_version": "4.5.0",
        })
        self.assertEqual(len(results), 0)

    def test_query_rejects_wrong_firmware_version(self) -> None:
        from capability_pack import PackRegistry
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        registry = PackRegistry()
        registry.register(ELRSv3OTAInjectionPack())
        results = registry.query({
            "protocol_version": "ELRS v3 OTA",
            "firmware_impl": "Betaflight",
            "firmware_version": "2026.6.2",
        })
        self.assertEqual(len(results), 0)


# ===========================================================================
# Seam 3: UID derivation from binding phrase
# ===========================================================================

class TestUIDDerivation(unittest.TestCase):
    """Preview derives UID from binding phrase matching known-good value."""

    def test_preview_derives_correct_uid(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        self.assertEqual(bytes.fromhex(result["uid_hex"]), FIXTURE_UID)

    def test_preview_accepts_direct_uid(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"target_uid": FIXTURE_UID.hex()})
        self.assertEqual(bytes.fromhex(result["uid_hex"]), FIXTURE_UID)


# ===========================================================================
# Seam 4: FHSS sequence computation
# ===========================================================================

class TestFHSSComputation(unittest.TestCase):
    """FHSS sequence matches known-good v3 values for test_phrase UID."""

    def test_fhss_seed(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        self.assertEqual(result["fhss_seed"], FIXTURE_V3_SEED)

    def test_fhss_sequence_length(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        self.assertEqual(len(result["fhss_sequence"]), FIXTURE_FHSS_LEN)

    def test_fhss_sequence_first_entry_is_sync_channel(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        self.assertEqual(result["fhss_sequence"][0], FIXTURE_V3_SYNC_CH)

    def test_fhss_sequence_first_20_entries(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        self.assertEqual(result["fhss_sequence"][:20], FIXTURE_FHSS_FIRST_20)


# ===========================================================================
# Seam 5: CRC init derivation
# ===========================================================================

class TestCRCInitDerivation(unittest.TestCase):
    """CRC initializer matches known-good v3 value."""

    def test_crc_init(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        self.assertEqual(result["crc_init"], FIXTURE_V3_CRC_INIT)


# ===========================================================================
# Seam 6: Frame construction
# ===========================================================================

class TestFrameConstruction(unittest.TestCase):
    """Constructed RC frames have correct CRC and round-trip through decode."""

    def test_constructed_frame_hex_matches_known_good(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        # The first packet in the plan should match our fixture
        first_pkt = result["packets"][0]
        self.assertEqual(first_pkt["data_hex"], FIXTURE_RC_FRAME_HEX)

    def test_constructed_frame_crc_validates(self) -> None:
        """Frame round-trips through the OTA CRC validator."""
        from elrs_core.ota import PACKET_TYPE_RCDATA, validate_crc
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        for pkt_info in result["packets"][:5]:
            raw = bytes.fromhex(pkt_info["data_hex"])
            ok = validate_crc(raw, False, FIXTURE_V3_CRC_INIT, 0,
                              PACKET_TYPE_RCDATA)
            self.assertTrue(ok, f"CRC failed for hop {pkt_info['hop_index']}")

    def test_packets_span_fhss_hops(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        channels = [p["channel"] for p in result["packets"]]
        # First hop is the sync channel
        self.assertEqual(channels[0], FIXTURE_V3_SYNC_CH)
        # Multiple distinct channels (not all the same)
        self.assertGreater(len(set(channels)), 1)

    def test_nonce_model_v3_uses_zero(self) -> None:
        """v3 hybrid mode: nonce is not XOR'd into CRC init (unlike v4)."""
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.preview({"binding_phrase": FIXTURE_PHRASE})
        # All packets in v3 hybrid use ota_nonce=0 for CRC
        for pkt_info in result["packets"]:
            self.assertEqual(pkt_info.get("ota_nonce", 0), 0)


# ===========================================================================
# Seam 7: Headless pipeline integration
# ===========================================================================

class TestHeadlessPipelineIntegration(unittest.TestCase):
    """Pack flows through headless pipeline with correct state transitions."""

    def test_full_pipeline_with_operator_supplied_mode(self) -> None:
        from capability_pack import (
            EvidenceStore,
            FakeExecutor,
            PackRegistry,
            PrerequisiteMode,
            ResultState,
            ScopeManager,
            TargetDescriptor,
            run_headless_pipeline,
        )
        from elrs_v3_pack import ELRSv3OTAInjectionPack

        registry = PackRegistry()
        pack = ELRSv3OTAInjectionPack()
        registry.register(pack)

        target = TargetDescriptor(
            identity="tinyhawk-iii-bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )

        executor = FakeExecutor(
            canned_sequence=[
                ResultState.PREVIEWED,
                ResultState.ATTEMPTED,
            ],
            canned_evidence={
                "binding_phrase": FIXTURE_PHRASE,
                "uid_hex": FIXTURE_UID.hex(),
                "fhss_seed": FIXTURE_V3_SEED,
            },
        )

        evidence_store = EvidenceStore()

        record = run_headless_pipeline(
            registry=registry,
            scope_manager=ScopeManager(registry),
            executor=executor,
            target=target,
            pack_name="ELRS v3 OTA Injection",
            pack_version="0.1.0",
            parameters={
                "binding_phrase": FIXTURE_PHRASE,
                "target_uid": FIXTURE_UID.hex(),
            },
            prerequisite_modes={
                "binding_phrase": PrerequisiteMode.OPERATOR_SUPPLIED,
                "target_uid": PrerequisiteMode.OPERATOR_SUPPLIED,
            },
            hardware_identity="offline-fixture",
            time_limit=timedelta(minutes=5),
            evidence_store=evidence_store,
        )

        # Evidence record captures OPERATOR_SUPPLIED mode
        self.assertEqual(
            record.prerequisite_modes["binding_phrase"],
            PrerequisiteMode.OPERATOR_SUPPLIED,
        )
        self.assertEqual(
            record.prerequisite_modes["target_uid"],
            PrerequisiteMode.OPERATOR_SUPPLIED,
        )
        self.assertEqual(record.stop_reason, "completed")
        self.assertEqual(len(record.transition_log), 2)
        self.assertEqual(record.transition_log[0][0], ResultState.PREVIEWED)
        self.assertEqual(record.transition_log[1][0], ResultState.ATTEMPTED)
        self.assertEqual(len(evidence_store.records), 1)

    def test_preview_only_pipeline(self) -> None:
        from capability_pack import (
            EvidenceStore,
            FakeExecutor,
            PackRegistry,
            PrerequisiteMode,
            ResultState,
            ScopeManager,
            TargetDescriptor,
            run_headless_pipeline,
        )
        from elrs_v3_pack import ELRSv3OTAInjectionPack

        registry = PackRegistry()
        registry.register(ELRSv3OTAInjectionPack())

        target = TargetDescriptor(
            identity="tinyhawk-iii-bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )

        executor = FakeExecutor(
            canned_sequence=[ResultState.PREVIEWED],
            canned_evidence={"side_effects": False},
        )

        record = run_headless_pipeline(
            registry=registry,
            scope_manager=ScopeManager(registry),
            executor=executor,
            target=target,
            pack_name="ELRS v3 OTA Injection",
            pack_version="0.1.0",
            parameters={"binding_phrase": FIXTURE_PHRASE},
            prerequisite_modes={
                "binding_phrase": PrerequisiteMode.OPERATOR_SUPPLIED,
            },
            hardware_identity="offline-fixture",
            time_limit=timedelta(minutes=1),
            evidence_store=EvidenceStore(),
        )

        self.assertEqual(len(record.transition_log), 1)
        self.assertEqual(record.transition_log[0][0], ResultState.PREVIEWED)
        self.assertEqual(record.stop_reason, "completed")


# ===========================================================================
# Seam 8: Pack lifecycle methods (arm, execute, verify, stop)
# ===========================================================================

class TestPackLifecycleMethods(unittest.TestCase):
    """arm/execute/verify/stop return well-formed dicts without hardware."""

    def test_arm_returns_dict(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.arm({"binding_phrase": FIXTURE_PHRASE})
        self.assertIsInstance(result, dict)
        self.assertIn("armed", result)

    def test_execute_returns_dict(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.execute({"binding_phrase": FIXTURE_PHRASE})
        self.assertIsInstance(result, dict)

    def test_verify_returns_dict(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.verify({})
        self.assertIsInstance(result, dict)

    def test_stop_returns_dict(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.stop()
        self.assertIsInstance(result, dict)
        self.assertIn("stopped", result)


# ===========================================================================
# Seam 9: Honest offline evidence (no overclaiming)
# ===========================================================================

class TestOfflineEvidenceHonesty(unittest.TestCase):
    """execute()/verify() never claim more than offline source-derivation."""

    def test_execute_offline_does_not_claim_transmission(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.execute({"binding_phrase": FIXTURE_PHRASE})
        # Nothing is transmitted offline, so the honest state is PREVIEWED.
        self.assertEqual(result["result_state"], "previewed")
        self.assertFalse(result["transmitted"])

    def test_verify_validated_only_when_crc_checks_pass(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.verify({"binding_phrase": FIXTURE_PHRASE})
        self.assertTrue(result["independently_verified"])
        self.assertEqual(result["evidence_level"], "offline_validated")
        self.assertGreater(result["packets_checked"], 0)
        # Even when validated, it must not claim bench/field evidence.
        self.assertIn("bench-validated", result["scope"].lower())
        self.assertIn("not field-ready", result["scope"].lower())

    def test_verify_is_candidate_without_prerequisites(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        result = pack.verify({})
        self.assertEqual(result["evidence_level"], "candidate")
        self.assertFalse(result["independently_verified"])

    def test_detect_and_classify_stages(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        detection = pack.detect({"capture": {
            "elrs_detected": True, "sync_word": 0x42, "hop_count": 240,
            "ota_version": 3, "packet_rate_hz": 250,
        }})
        self.assertTrue(detection["detected"])
        classification = pack.classify(detection)
        self.assertEqual(classification["ota_version"], 3)
        self.assertTrue(classification["applies"])

    def test_classify_rejects_v4_observation(self) -> None:
        from elrs_v3_pack import ELRSv3OTAInjectionPack
        pack = ELRSv3OTAInjectionPack()
        classification = pack.classify({"ota_version": 4})
        self.assertFalse(classification["applies"])


if __name__ == "__main__":
    unittest.main()
