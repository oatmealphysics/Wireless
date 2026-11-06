"""Contract tests for the Capability Pack type system.

Exercises every surface of the pack contract, registry, and scoped
authorization model.  Independent of Flask, hardware, and network.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1]
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))


class TestPrerequisiteMode(unittest.TestCase):
    def test_members_exist(self) -> None:
        from capability_pack import PrerequisiteMode

        self.assertEqual(PrerequisiteMode.WIRELESSLY_ACQUIRED.value, "wirelessly_acquired")
        self.assertEqual(PrerequisiteMode.DERIVED.value, "derived")
        self.assertEqual(PrerequisiteMode.OPERATOR_SUPPLIED.value, "operator_supplied")


class TestEvidenceLevel(unittest.TestCase):
    def test_members_exist(self) -> None:
        from capability_pack import EvidenceLevel

        self.assertEqual(EvidenceLevel.CANDIDATE.value, "candidate")
        self.assertEqual(EvidenceLevel.OFFLINE_VALIDATED.value, "offline_validated")
        self.assertEqual(EvidenceLevel.PASSIVE_VALIDATED.value, "passive_validated")
        self.assertEqual(EvidenceLevel.BENCH_VALIDATED.value, "bench_validated")
        self.assertEqual(EvidenceLevel.FIELD_READY.value, "field_ready")


class TestResultState(unittest.TestCase):
    def test_members_exist(self) -> None:
        from capability_pack import ResultState

        self.assertEqual(ResultState.PREVIEWED.value, "previewed")
        self.assertEqual(ResultState.ATTEMPTED.value, "attempted")
        self.assertEqual(ResultState.TRANSMITTED.value, "transmitted")
        self.assertEqual(ResultState.TARGET_ACCEPTED.value, "target_accepted")
        self.assertEqual(ResultState.EFFECT_CONFIRMED.value, "effect_confirmed")


class TestPackIdentity(unittest.TestCase):
    def test_construction(self) -> None:
        from capability_pack import PackIdentity

        ident = PackIdentity(
            name="elrs-desync",
            version="0.1.0",
            description="Force ELRS receiver desync via forged SYNC packets",
        )
        self.assertEqual(ident.name, "elrs-desync")
        self.assertEqual(ident.version, "0.1.0")
        self.assertEqual(ident.description, "Force ELRS receiver desync via forged SYNC packets")


class TestApplicabilityBoundary(unittest.TestCase):
    def test_construction(self) -> None:
        from capability_pack import ApplicabilityBoundary

        boundary = ApplicabilityBoundary(
            protocol_version="ELRS v3 OTA",
            phy_domain="2.4 GHz LoRa SX1280",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
            configuration={"packet_rate": "500Hz"},
            prerequisite_knowledge=["UID bytes 3-5 from passive SYNC capture"],
            access_conditions=["RF line-of-sight within 100m"],
        )
        self.assertEqual(boundary.protocol_version, "ELRS v3 OTA")
        self.assertEqual(boundary.firmware_impl, "Betaflight")


class TestPrerequisite(unittest.TestCase):
    def test_construction_with_mode(self) -> None:
        from capability_pack import Prerequisite, PrerequisiteMode

        prereq = Prerequisite(
            name="target_uid",
            mode=PrerequisiteMode.WIRELESSLY_ACQUIRED,
            description="Complete 6-byte UID from passive SYNC capture",
        )
        self.assertEqual(prereq.mode, PrerequisiteMode.WIRELESSLY_ACQUIRED)


class TestEffect(unittest.TestCase):
    def test_destructive_classification(self) -> None:
        from capability_pack import Effect

        non_destructive = Effect(
            name="desync",
            destructive=False,
            description="Temporary receiver desynchronization",
        )
        destructive = Effect(
            name="firmware_corrupt",
            destructive=True,
            description="Overwrites flash region",
        )
        self.assertFalse(non_destructive.destructive)
        self.assertTrue(destructive.destructive)


class TestHardwareRequirement(unittest.TestCase):
    def test_construction(self) -> None:
        from capability_pack import HardwareRequirement

        req = HardwareRequirement(
            name="sdr_transceiver",
            description="Ettus B210 or B205mini with UHD driver",
        )
        self.assertEqual(req.name, "sdr_transceiver")


class TestFalsifier(unittest.TestCase):
    def test_construction(self) -> None:
        from capability_pack import Falsifier

        f = Falsifier(
            description="Receiver rejects forged SYNC despite correct UID",
            condition="CRC mismatch on nonce > 0",
        )
        self.assertEqual(f.condition, "CRC mismatch on nonce > 0")


# ---------------------------------------------------------------------------
# Reference test pack — exercises the CapabilityPack protocol
# ---------------------------------------------------------------------------

def _make_reference_pack():
    """Build a reference test pack that implements every contract surface."""
    from capability_pack import (
        ApplicabilityBoundary,
        CapabilityPack,
        Effect,
        Falsifier,
        HardwareRequirement,
        PackIdentity,
        Prerequisite,
        PrerequisiteMode,
    )

    class ELRSDesyncPack(CapabilityPack):
        @property
        def identity(self) -> PackIdentity:
            return PackIdentity(
                name="elrs-desync",
                version="0.1.0",
                description="Force ELRS receiver desync via forged SYNC packets",
            )

        @property
        def applicability(self) -> ApplicabilityBoundary:
            return ApplicabilityBoundary(
                protocol_version="ELRS v3 OTA",
                phy_domain="2.4 GHz LoRa SX1280",
                firmware_impl="Betaflight",
                firmware_version="4.4.2",
                configuration={"packet_rate": "500Hz"},
                prerequisite_knowledge=["UID bytes 3-5 from passive SYNC capture"],
                access_conditions=["RF line-of-sight within 100m"],
            )

        @property
        def prerequisites(self) -> list[Prerequisite]:
            return [
                Prerequisite(
                    name="target_uid",
                    mode=PrerequisiteMode.WIRELESSLY_ACQUIRED,
                    description="Complete 6-byte UID from passive SYNC capture",
                ),
            ]

        @property
        def effects(self) -> list[Effect]:
            return [
                Effect(name="desync", destructive=False, description="Temporary receiver desynchronization"),
            ]

        @property
        def hardware_requirements(self) -> list[HardwareRequirement]:
            return [
                HardwareRequirement(name="sdr_transceiver", description="Ettus B210 or B205mini"),
            ]

        @property
        def falsifiers(self) -> list[Falsifier]:
            return [
                Falsifier(
                    description="Receiver rejects forged SYNC despite correct UID",
                    condition="CRC mismatch on nonce > 0",
                ),
            ]

        def preview(self, params: dict) -> dict:
            return {
                "description": "Will transmit forged SYNC on FHSS channel sequence",
                "target": params.get("target_uid"),
                "estimated_duration_s": 5,
            }

        def arm(self, params: dict) -> dict:
            return {"armed": True, "target_uid": params.get("target_uid")}

        def execute(self, params: dict) -> dict:
            return {"result_state": "transmitted", "packets_sent": 0}

        def verify(self, params: dict) -> dict:
            return {"result_state": "previewed", "evidence_level": "candidate"}

        def stop(self) -> dict:
            return {"stopped": True}

    return ELRSDesyncPack()


class TestCapabilityPackProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self.pack = _make_reference_pack()

    def test_identity_is_pack_identity(self) -> None:
        from capability_pack import PackIdentity

        self.assertIsInstance(self.pack.identity, PackIdentity)
        self.assertEqual(self.pack.identity.name, "elrs-desync")

    def test_applicability_is_boundary(self) -> None:
        from capability_pack import ApplicabilityBoundary

        self.assertIsInstance(self.pack.applicability, ApplicabilityBoundary)

    def test_prerequisites_have_modes(self) -> None:
        from capability_pack import PrerequisiteMode

        for p in self.pack.prerequisites:
            self.assertIsInstance(p.mode, PrerequisiteMode)

    def test_effects_have_destructive_flag(self) -> None:
        for e in self.pack.effects:
            self.assertIsInstance(e.destructive, bool)

    def test_preview_returns_dict(self) -> None:
        result = self.pack.preview({"target_uid": b"\x01\x02\x03\x04\x05\x06"})
        self.assertIn("description", result)

    def test_arm_returns_dict(self) -> None:
        result = self.pack.arm({"target_uid": b"\x01\x02\x03\x04\x05\x06"})
        self.assertIn("armed", result)

    def test_execute_returns_result_state(self) -> None:
        result = self.pack.execute({"target_uid": b"\x01\x02\x03\x04\x05\x06"})
        self.assertIn("result_state", result)

    def test_verify_returns_evidence_level(self) -> None:
        result = self.pack.verify({})
        self.assertIn("evidence_level", result)

    def test_stop_returns_dict(self) -> None:
        result = self.pack.stop()
        self.assertIn("stopped", result)

    def test_has_destructive_effects_false(self) -> None:
        self.assertFalse(self.pack.has_destructive_effects)

    def test_has_destructive_effects_true(self) -> None:
        from capability_pack import Effect

        pack = _make_reference_pack()
        original_effects = pack.effects

        class DestructivePack(pack.__class__):
            @property
            def effects(self):
                return original_effects + [
                    Effect(name="flash_corrupt", destructive=True, description="test"),
                ]

        destructive = DestructivePack()
        self.assertTrue(destructive.has_destructive_effects)


# ---------------------------------------------------------------------------
# Target descriptor for registry queries and authorization
# ---------------------------------------------------------------------------

def _make_matching_target() -> dict:
    """Target descriptor that matches the reference pack's applicability."""
    return {
        "protocol_version": "ELRS v3 OTA",
        "phy_domain": "2.4 GHz LoRa SX1280",
        "firmware_impl": "Betaflight",
        "firmware_version": "4.4.2",
    }


def _make_nonmatching_target() -> dict:
    """Target descriptor that does NOT match the reference pack."""
    return {
        "protocol_version": "ELRS v4 OTA",
        "phy_domain": "2.4 GHz LoRa SX1280",
        "firmware_impl": "Betaflight",
        "firmware_version": "4.5.0",
    }


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestPackRegistry(unittest.TestCase):
    def setUp(self) -> None:
        from capability_pack import PackRegistry

        self.registry = PackRegistry()
        self.pack = _make_reference_pack()

    def test_register_and_lookup_by_identity(self) -> None:
        self.registry.register(self.pack)
        found = self.registry.lookup("elrs-desync", "0.1.0")
        self.assertIs(found, self.pack)

    def test_lookup_missing_returns_none(self) -> None:
        self.assertIsNone(self.registry.lookup("nonexistent", "0.0.0"))

    def test_register_duplicate_raises(self) -> None:
        self.registry.register(self.pack)
        with self.assertRaises(ValueError):
            self.registry.register(self.pack)

    def test_query_matching_target(self) -> None:
        self.registry.register(self.pack)
        target = _make_matching_target()
        results = self.registry.query(target)
        self.assertEqual(len(results), 1)
        self.assertIs(results[0], self.pack)

    def test_query_nonmatching_target_returns_empty(self) -> None:
        self.registry.register(self.pack)
        target = _make_nonmatching_target()
        results = self.registry.query(target)
        self.assertEqual(len(results), 0)

    def test_query_partial_match_on_firmware_version_rejects(self) -> None:
        self.registry.register(self.pack)
        target = _make_matching_target()
        target["firmware_version"] = "2026.6.2"
        results = self.registry.query(target)
        self.assertEqual(len(results), 0)


# ---------------------------------------------------------------------------
# Authorization lifecycle tests
# ---------------------------------------------------------------------------

class TestAuthorizationScope(unittest.TestCase):
    def setUp(self) -> None:
        from capability_pack import AuthorizationScope, PackIdentity, TargetDescriptor

        self.target = TargetDescriptor(
            identity="tinyhawk-iii-bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )
        self.pack_identity = PackIdentity(
            name="elrs-desync",
            version="0.1.0",
            description="Force ELRS receiver desync",
        )
        self.scope = AuthorizationScope(
            target=self.target,
            pack_identity=self.pack_identity,
            permitted_actions=frozenset({"preview", "execute", "verify", "stop"}),
            bounds={"max_packets": 100},
            time_limit=timedelta(minutes=5),
            stop_conditions=["operator_abort", "target_failsafe"],
            sacrificial_target=False,
            evidence_destination="work/evidence/bench-01",
        )

    def test_scope_fields_accessible(self) -> None:
        self.assertEqual(self.scope.target.identity, "tinyhawk-iii-bench-01")
        self.assertEqual(self.scope.pack_identity.name, "elrs-desync")
        self.assertIn("preview", self.scope.permitted_actions)
        self.assertFalse(self.scope.sacrificial_target)

    def test_scope_not_expired_within_limit(self) -> None:
        self.assertFalse(self.scope.is_expired)

    def test_scope_expired_after_limit(self) -> None:
        from capability_pack import AuthorizationScope, PackIdentity, TargetDescriptor

        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        scope = AuthorizationScope(
            target=self.target,
            pack_identity=self.pack_identity,
            permitted_actions=frozenset({"preview"}),
            bounds={},
            time_limit=timedelta(minutes=5),
            stop_conditions=[],
            sacrificial_target=False,
            evidence_destination="work/evidence/bench-01",
            created_at=past,
        )
        self.assertTrue(scope.is_expired)

    def test_scope_disarm(self) -> None:
        self.assertFalse(self.scope.disarmed)
        self.scope.disarm()
        self.assertTrue(self.scope.disarmed)


class TestScopeManager(unittest.TestCase):
    def setUp(self) -> None:
        from capability_pack import PackRegistry, ScopeManager, TargetDescriptor

        self.registry = PackRegistry()
        self.pack = _make_reference_pack()
        self.registry.register(self.pack)
        self.manager = ScopeManager(self.registry)
        self.target = TargetDescriptor(
            identity="tinyhawk-iii-bench-01",
            protocol_version="ELRS v3 OTA",
            phy_domain="2.4 GHz LoRa SX1280",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )

    def test_arm_creates_scope(self) -> None:
        scope = self.manager.arm(
            pack_name="elrs-desync",
            pack_version="0.1.0",
            target=self.target,
            permitted_actions=frozenset({"preview", "execute", "verify", "stop"}),
            bounds={"max_packets": 100},
            time_limit=timedelta(minutes=5),
            stop_conditions=["operator_abort"],
            sacrificial_target=False,
            evidence_destination="work/evidence/bench-01",
        )
        self.assertIsNotNone(scope)
        self.assertEqual(scope.pack_identity.name, "elrs-desync")

    def test_arm_unknown_pack_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.manager.arm(
                pack_name="nonexistent",
                pack_version="0.0.0",
                target=self.target,
                permitted_actions=frozenset({"preview"}),
                bounds={},
                time_limit=timedelta(minutes=1),
                stop_conditions=[],
                sacrificial_target=False,
                evidence_destination="work/evidence",
            )

    def test_arm_rejects_ambiguous_target(self) -> None:
        """A target with an unknown ("*") attribute cannot arm a specific pack.

        The pack's boundary pins phy_domain; a target that does not actually
        know its PHY must not satisfy that requirement by wildcard, even though
        lenient discovery (query) would still list the pack.
        """
        from capability_pack import TargetDescriptor

        ambiguous = TargetDescriptor(
            identity="ambiguous-drone",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
            # phy_domain left unknown ("*") — the pack requires a specific PHY.
        )
        # Lenient discovery still lists the pack (a partial target "could" match).
        self.assertIn(self.pack, self.registry.query(ambiguous.as_query()))
        # Strict authorization refuses to arm it.
        with self.assertRaises(ValueError):
            self.manager.arm(
                pack_name="elrs-desync",
                pack_version="0.1.0",
                target=ambiguous,
                permitted_actions=frozenset({"execute"}),
                bounds={},
                time_limit=timedelta(minutes=1),
                stop_conditions=[],
                sacrificial_target=False,
                evidence_destination="work/evidence",
            )

    def test_arm_mismatched_target_raises(self) -> None:
        from capability_pack import TargetDescriptor

        wrong_target = TargetDescriptor(
            identity="wrong-drone",
            protocol_version="ELRS v4 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.5.0",
        )
        with self.assertRaises(ValueError):
            self.manager.arm(
                pack_name="elrs-desync",
                pack_version="0.1.0",
                target=wrong_target,
                permitted_actions=frozenset({"preview"}),
                bounds={},
                time_limit=timedelta(minutes=1),
                stop_conditions=[],
                sacrificial_target=False,
                evidence_destination="work/evidence",
            )

    def test_authorize_rejects_unarmed(self) -> None:
        from capability_pack import AuthorizationError

        with self.assertRaises(AuthorizationError) as ctx:
            self.manager.authorize(
                pack_name="elrs-desync",
                pack_version="0.1.0",
                target_identity="tinyhawk-iii-bench-01",
                action="execute",
            )
        self.assertIn("no active scope", str(ctx.exception).lower())

    def test_authorize_rejects_expired(self) -> None:
        from capability_pack import AuthorizationError

        scope = self.manager.arm(
            pack_name="elrs-desync",
            pack_version="0.1.0",
            target=self.target,
            permitted_actions=frozenset({"execute"}),
            bounds={},
            time_limit=timedelta(seconds=0),
            stop_conditions=[],
            sacrificial_target=False,
            evidence_destination="work/evidence",
        )
        # Force expiry by backdating created_at
        scope._created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        with self.assertRaises(AuthorizationError) as ctx:
            self.manager.authorize(
                pack_name="elrs-desync",
                pack_version="0.1.0",
                target_identity="tinyhawk-iii-bench-01",
                action="execute",
            )
        self.assertIn("expired", str(ctx.exception).lower())

    def test_authorize_rejects_wrong_target(self) -> None:
        from capability_pack import AuthorizationError

        self.manager.arm(
            pack_name="elrs-desync",
            pack_version="0.1.0",
            target=self.target,
            permitted_actions=frozenset({"execute"}),
            bounds={},
            time_limit=timedelta(minutes=5),
            stop_conditions=[],
            sacrificial_target=False,
            evidence_destination="work/evidence",
        )
        with self.assertRaises(AuthorizationError) as ctx:
            self.manager.authorize(
                pack_name="elrs-desync",
                pack_version="0.1.0",
                target_identity="wrong-drone",
                action="execute",
            )
        self.assertIn("target", str(ctx.exception).lower())

    def test_authorize_rejects_unpermitted_action(self) -> None:
        from capability_pack import AuthorizationError

        self.manager.arm(
            pack_name="elrs-desync",
            pack_version="0.1.0",
            target=self.target,
            permitted_actions=frozenset({"preview"}),
            bounds={},
            time_limit=timedelta(minutes=5),
            stop_conditions=[],
            sacrificial_target=False,
            evidence_destination="work/evidence",
        )
        with self.assertRaises(AuthorizationError) as ctx:
            self.manager.authorize(
                pack_name="elrs-desync",
                pack_version="0.1.0",
                target_identity="tinyhawk-iii-bench-01",
                action="execute",
            )
        self.assertIn("action", str(ctx.exception).lower())

    def test_authorize_succeeds_when_armed(self) -> None:
        self.manager.arm(
            pack_name="elrs-desync",
            pack_version="0.1.0",
            target=self.target,
            permitted_actions=frozenset({"preview", "execute"}),
            bounds={},
            time_limit=timedelta(minutes=5),
            stop_conditions=[],
            sacrificial_target=False,
            evidence_destination="work/evidence",
        )
        scope = self.manager.authorize(
            pack_name="elrs-desync",
            pack_version="0.1.0",
            target_identity="tinyhawk-iii-bench-01",
            action="execute",
        )
        self.assertIsNotNone(scope)

    def test_disarm_revokes_scope(self) -> None:
        from capability_pack import AuthorizationError

        self.manager.arm(
            pack_name="elrs-desync",
            pack_version="0.1.0",
            target=self.target,
            permitted_actions=frozenset({"execute"}),
            bounds={},
            time_limit=timedelta(minutes=5),
            stop_conditions=[],
            sacrificial_target=False,
            evidence_destination="work/evidence",
        )
        self.manager.disarm(
            pack_name="elrs-desync",
            pack_version="0.1.0",
            target_identity="tinyhawk-iii-bench-01",
        )
        with self.assertRaises(AuthorizationError):
            self.manager.authorize(
                pack_name="elrs-desync",
                pack_version="0.1.0",
                target_identity="tinyhawk-iii-bench-01",
                action="execute",
            )


class TestDestructiveEffectAuthorization(unittest.TestCase):
    """Destructive effects require Sacrificial Target designation."""

    def _make_destructive_pack(self):
        from capability_pack import (
            ApplicabilityBoundary,
            CapabilityPack,
            Effect,
            Falsifier,
            HardwareRequirement,
            PackIdentity,
            Prerequisite,
            PrerequisiteMode,
        )

        class FlashCorruptPack(CapabilityPack):
            @property
            def identity(self) -> PackIdentity:
                return PackIdentity(
                    name="flash-corrupt",
                    version="0.1.0",
                    description="Corrupt flash via OTA update exploit",
                )

            @property
            def applicability(self) -> ApplicabilityBoundary:
                return ApplicabilityBoundary(
                    protocol_version="ELRS v3 OTA",
                    phy_domain="2.4 GHz LoRa SX1280",
                    firmware_impl="Betaflight",
                    firmware_version="4.4.2",
                )

            @property
            def prerequisites(self) -> list[Prerequisite]:
                return [
                    Prerequisite(
                        name="target_uid",
                        mode=PrerequisiteMode.WIRELESSLY_ACQUIRED,
                        description="Full UID",
                    ),
                ]

            @property
            def effects(self) -> list[Effect]:
                return [
                    Effect(name="flash_corrupt", destructive=True, description="Overwrites flash"),
                ]

            @property
            def hardware_requirements(self) -> list[HardwareRequirement]:
                return []

            @property
            def falsifiers(self) -> list[Falsifier]:
                return []

            def preview(self, params):
                return {"description": "Will corrupt flash"}

            def arm(self, params):
                return {"armed": True}

            def execute(self, params):
                return {"result_state": "transmitted"}

            def verify(self, params):
                return {"result_state": "previewed", "evidence_level": "candidate"}

            def stop(self):
                return {"stopped": True}

        return FlashCorruptPack()

    def setUp(self) -> None:
        from capability_pack import PackRegistry, ScopeManager, TargetDescriptor

        self.registry = PackRegistry()
        self.destructive_pack = self._make_destructive_pack()
        self.registry.register(self.destructive_pack)
        self.manager = ScopeManager(self.registry)
        self.target = TargetDescriptor(
            identity="sacrificial-bench-02",
            protocol_version="ELRS v3 OTA",
            phy_domain="2.4 GHz LoRa SX1280",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )

    def test_destructive_pack_rejected_without_sacrificial_target(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.manager.arm(
                pack_name="flash-corrupt",
                pack_version="0.1.0",
                target=self.target,
                permitted_actions=frozenset({"execute"}),
                bounds={},
                time_limit=timedelta(minutes=5),
                stop_conditions=[],
                sacrificial_target=False,
                evidence_destination="work/evidence",
            )
        self.assertIn("sacrificial target", str(ctx.exception).lower())

    def test_destructive_pack_accepted_with_sacrificial_target(self) -> None:
        scope = self.manager.arm(
            pack_name="flash-corrupt",
            pack_version="0.1.0",
            target=self.target,
            permitted_actions=frozenset({"execute"}),
            bounds={},
            time_limit=timedelta(minutes=5),
            stop_conditions=[],
            sacrificial_target=True,
            evidence_destination="work/evidence",
        )
        self.assertTrue(scope.sacrificial_target)


if __name__ == "__main__":
    unittest.main()
