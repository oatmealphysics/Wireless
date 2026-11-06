"""Executor contract, Result State machine, evidence record, and headless pipeline.

Exercises the execution layer: state machine validation, evidence capture,
fake executor, and the full headless pipeline seam.  Independent of Flask,
hardware, and external network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1]
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))


# ---------------------------------------------------------------------------
# Seam 1: ResultStateMachine
# ---------------------------------------------------------------------------

class TestResultStateMachineForwardProgression(unittest.TestCase):
    """Valid monotonic progression through the Result State sequence."""

    def test_advance_through_all_states(self) -> None:
        from capability_pack import ResultState, ResultStateMachine

        sm = ResultStateMachine()
        self.assertIsNone(sm.current)

        sm.transition(ResultState.PREVIEWED)
        self.assertEqual(sm.current, ResultState.PREVIEWED)

        sm.transition(ResultState.ATTEMPTED)
        self.assertEqual(sm.current, ResultState.ATTEMPTED)

        sm.transition(ResultState.TRANSMITTED)
        self.assertEqual(sm.current, ResultState.TRANSMITTED)

        sm.transition(ResultState.TARGET_ACCEPTED)
        self.assertEqual(sm.current, ResultState.TARGET_ACCEPTED)

        sm.transition(ResultState.EFFECT_CONFIRMED)
        self.assertEqual(sm.current, ResultState.EFFECT_CONFIRMED)

    def test_transition_log_records_all_steps(self) -> None:
        from capability_pack import ResultState, ResultStateMachine

        sm = ResultStateMachine()
        sm.transition(ResultState.PREVIEWED)
        sm.transition(ResultState.ATTEMPTED)

        log = sm.transition_log
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0][0], ResultState.PREVIEWED)
        self.assertEqual(log[1][0], ResultState.ATTEMPTED)
        # Each entry is (ResultState, datetime)
        self.assertIsNotNone(log[0][1])
        self.assertIsNotNone(log[1][1])


class TestResultStateMachineFirstTransition(unittest.TestCase):
    """First transition must be PREVIEWED."""

    def test_first_transition_must_be_previewed(self) -> None:
        from capability_pack import InvalidTransitionError, ResultState, ResultStateMachine

        sm = ResultStateMachine()
        with self.assertRaises(InvalidTransitionError):
            sm.transition(ResultState.ATTEMPTED)


class TestResultStateMachineRejections(unittest.TestCase):
    """Invalid or non-monotonic transitions must be rejected."""

    def test_backward_transition_rejected(self) -> None:
        from capability_pack import InvalidTransitionError, ResultState, ResultStateMachine

        sm = ResultStateMachine()
        sm.transition(ResultState.PREVIEWED)
        sm.transition(ResultState.ATTEMPTED)

        with self.assertRaises(InvalidTransitionError):
            sm.transition(ResultState.PREVIEWED)

    def test_repeated_state_rejected(self) -> None:
        from capability_pack import InvalidTransitionError, ResultState, ResultStateMachine

        sm = ResultStateMachine()
        sm.transition(ResultState.PREVIEWED)

        with self.assertRaises(InvalidTransitionError):
            sm.transition(ResultState.PREVIEWED)

    def test_skip_transition_rejected(self) -> None:
        """Must not skip states: PREVIEWED -> TRANSMITTED is invalid."""
        from capability_pack import InvalidTransitionError, ResultState, ResultStateMachine

        sm = ResultStateMachine()
        sm.transition(ResultState.PREVIEWED)

        with self.assertRaises(InvalidTransitionError):
            sm.transition(ResultState.TRANSMITTED)

    def test_current_unchanged_after_rejected_transition(self) -> None:
        from capability_pack import InvalidTransitionError, ResultState, ResultStateMachine

        sm = ResultStateMachine()
        sm.transition(ResultState.PREVIEWED)
        sm.transition(ResultState.ATTEMPTED)

        with self.assertRaises(InvalidTransitionError):
            sm.transition(ResultState.PREVIEWED)

        self.assertEqual(sm.current, ResultState.ATTEMPTED)

    def test_log_unchanged_after_rejected_transition(self) -> None:
        from capability_pack import InvalidTransitionError, ResultState, ResultStateMachine

        sm = ResultStateMachine()
        sm.transition(ResultState.PREVIEWED)

        with self.assertRaises(InvalidTransitionError):
            sm.transition(ResultState.PREVIEWED)

        self.assertEqual(len(sm.transition_log), 1)


# ---------------------------------------------------------------------------
# Seam 2: ExecutorInput, ExecutorOutput, ExecutionRecord
# ---------------------------------------------------------------------------

class TestExecutorInput(unittest.TestCase):
    """ExecutorInput bundles structured input for the executor."""

    def test_construction(self) -> None:
        from capability_pack import (
            ExecutorInput,
            PackIdentity,
            TargetDescriptor,
        )

        pack_id = PackIdentity(name="elrs-desync", version="0.1.0", description="test")
        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )
        inp = ExecutorInput(
            pack_identity=pack_id,
            target=target,
            scope_reference="scope-key-123",
            parameters={"target_uid": "aabbccddeeff"},
        )
        self.assertEqual(inp.pack_identity.name, "elrs-desync")
        self.assertEqual(inp.target.identity, "bench-01")
        self.assertEqual(inp.scope_reference, "scope-key-123")
        self.assertEqual(inp.parameters["target_uid"], "aabbccddeeff")


class TestExecutorOutput(unittest.TestCase):
    """ExecutorOutput carries the result of an execution."""

    def test_construction(self) -> None:
        from datetime import datetime, timezone

        from capability_pack import ExecutorOutput, ResultState

        now = datetime.now(timezone.utc)
        log = [(ResultState.PREVIEWED, now)]
        out = ExecutorOutput(
            transition_log=log,
            evidence={"packets_sent": 0},
            stop_reason="completed",
            final_state=ResultState.PREVIEWED,
        )
        self.assertEqual(out.final_state, ResultState.PREVIEWED)
        self.assertEqual(out.stop_reason, "completed")
        self.assertEqual(len(out.transition_log), 1)


class TestExecutionRecord(unittest.TestCase):
    """ExecutionRecord is the append-only evidence record."""

    def test_construction_and_all_required_fields(self) -> None:
        from datetime import datetime, timedelta, timezone

        from capability_pack import (
            ExecutionRecord,
            PackIdentity,
            PrerequisiteMode,
            ResultState,
            TargetDescriptor,
        )

        now = datetime.now(timezone.utc)
        record = ExecutionRecord(
            capability_version="0.1.0",
            target_identity="bench-01",
            prerequisite_values={"target_uid": "aabbccddeeff"},
            prerequisite_modes={"target_uid": PrerequisiteMode.WIRELESSLY_ACQUIRED},
            authorization_scope_ref="scope-key-123",
            start_time=now,
            end_time=now + timedelta(seconds=5),
            transition_log=[(ResultState.PREVIEWED, now)],
            verification_evidence={"signal_detected": False},
            stop_reason="completed",
            hardware_identity="ettus-b210-serial-001",
        )
        self.assertEqual(record.capability_version, "0.1.0")
        self.assertEqual(record.target_identity, "bench-01")
        self.assertEqual(record.prerequisite_modes["target_uid"], PrerequisiteMode.WIRELESSLY_ACQUIRED)
        self.assertEqual(record.authorization_scope_ref, "scope-key-123")
        self.assertEqual(record.stop_reason, "completed")
        self.assertEqual(record.hardware_identity, "ettus-b210-serial-001")

    def test_record_is_frozen(self) -> None:
        from datetime import datetime, timezone

        from capability_pack import ExecutionRecord, ResultState

        now = datetime.now(timezone.utc)
        record = ExecutionRecord(
            capability_version="0.1.0",
            target_identity="bench-01",
            prerequisite_values={},
            prerequisite_modes={},
            authorization_scope_ref="ref",
            start_time=now,
            end_time=now,
            transition_log=[(ResultState.PREVIEWED, now)],
            verification_evidence={},
            stop_reason="completed",
            hardware_identity="sdr-001",
        )
        with self.assertRaises(AttributeError):
            record.capability_version = "0.2.0"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Seam 3: FakeExecutor
# ---------------------------------------------------------------------------

class TestFakeExecutorDeterministicSequence(unittest.TestCase):
    """FakeExecutor produces deterministic output from canned sequences."""

    def test_full_sequence_through_effect_confirmed(self) -> None:
        from capability_pack import (
            ExecutorInput,
            ExecutorOutput,
            FakeExecutor,
            PackIdentity,
            ResultState,
            TargetDescriptor,
        )

        pack_id = PackIdentity(name="elrs-desync", version="0.1.0", description="test")
        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )
        inp = ExecutorInput(
            pack_identity=pack_id,
            target=target,
            scope_reference="scope-ref",
            parameters={"target_uid": "aabbccddeeff"},
        )

        canned_sequence = [
            ResultState.PREVIEWED,
            ResultState.ATTEMPTED,
            ResultState.TRANSMITTED,
            ResultState.TARGET_ACCEPTED,
            ResultState.EFFECT_CONFIRMED,
        ]
        executor = FakeExecutor(
            canned_sequence=canned_sequence,
            canned_evidence={"packets_sent": 42},
        )

        output = executor.run(inp)

        self.assertIsInstance(output, ExecutorOutput)
        self.assertEqual(output.final_state, ResultState.EFFECT_CONFIRMED)
        self.assertEqual(len(output.transition_log), 5)
        self.assertEqual(output.evidence["packets_sent"], 42)
        self.assertEqual(output.stop_reason, "completed")

    def test_partial_sequence(self) -> None:
        from capability_pack import (
            ExecutorInput,
            FakeExecutor,
            PackIdentity,
            ResultState,
            TargetDescriptor,
        )

        pack_id = PackIdentity(name="elrs-desync", version="0.1.0", description="test")
        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )
        inp = ExecutorInput(
            pack_identity=pack_id,
            target=target,
            scope_reference="ref",
            parameters={},
        )

        executor = FakeExecutor(
            canned_sequence=[ResultState.PREVIEWED, ResultState.ATTEMPTED],
            canned_evidence={},
        )
        output = executor.run(inp)
        self.assertEqual(output.final_state, ResultState.ATTEMPTED)
        self.assertEqual(len(output.transition_log), 2)
        self.assertEqual(output.stop_reason, "completed")


class TestFakeExecutorTimeout(unittest.TestCase):
    """FakeExecutor reports timeout correctly."""

    def test_timeout_produces_correct_stop_reason(self) -> None:
        from capability_pack import (
            ExecutorInput,
            ExecutorTimeoutError,
            FakeExecutor,
            PackIdentity,
            ResultState,
            TargetDescriptor,
        )

        pack_id = PackIdentity(name="elrs-desync", version="0.1.0", description="test")
        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )
        inp = ExecutorInput(
            pack_identity=pack_id,
            target=target,
            scope_reference="ref",
            parameters={},
        )

        executor = FakeExecutor(
            canned_sequence=[ResultState.PREVIEWED, ResultState.ATTEMPTED],
            canned_evidence={},
            timeout_after_step=1,  # timeout after PREVIEWED
        )

        output = executor.run(inp)
        self.assertEqual(output.stop_reason, "timeout")
        self.assertEqual(output.final_state, ResultState.PREVIEWED)
        self.assertEqual(len(output.transition_log), 1)


class TestFakeExecutorCancellation(unittest.TestCase):
    """FakeExecutor checks cancellation flag between transitions."""

    def test_cancellation_produces_correct_stop_reason_and_partial_evidence(self) -> None:
        from capability_pack import (
            ExecutorInput,
            FakeExecutor,
            PackIdentity,
            ResultState,
            TargetDescriptor,
        )

        pack_id = PackIdentity(name="elrs-desync", version="0.1.0", description="test")
        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )
        inp = ExecutorInput(
            pack_identity=pack_id,
            target=target,
            scope_reference="ref",
            parameters={},
        )

        executor = FakeExecutor(
            canned_sequence=[
                ResultState.PREVIEWED,
                ResultState.ATTEMPTED,
                ResultState.TRANSMITTED,
            ],
            canned_evidence={"partial": True},
            cancel_after_step=2,  # cancel after ATTEMPTED
        )

        output = executor.run(inp)
        self.assertEqual(output.stop_reason, "cancelled")
        self.assertEqual(output.final_state, ResultState.ATTEMPTED)
        self.assertEqual(len(output.transition_log), 2)
        self.assertEqual(output.evidence["partial"], True)


# ---------------------------------------------------------------------------
# Seam 3b: EvidenceStore (append-only)
# ---------------------------------------------------------------------------

class TestEvidenceStore(unittest.TestCase):
    """EvidenceStore is append-only; records are never retroactively edited."""

    def test_append_and_retrieve(self) -> None:
        from datetime import datetime, timezone

        from capability_pack import EvidenceStore, ExecutionRecord, ResultState

        store = EvidenceStore()
        now = datetime.now(timezone.utc)
        record = ExecutionRecord(
            capability_version="0.1.0",
            target_identity="bench-01",
            prerequisite_values={},
            prerequisite_modes={},
            authorization_scope_ref="ref",
            start_time=now,
            end_time=now,
            transition_log=[(ResultState.PREVIEWED, now)],
            verification_evidence={},
            stop_reason="completed",
            hardware_identity="sdr-001",
        )
        store.append(record)
        self.assertEqual(len(store.records), 1)
        self.assertIs(store.records[0], record)

    def test_records_returns_copy(self) -> None:
        """Callers cannot mutate the internal list."""
        from capability_pack import EvidenceStore

        store = EvidenceStore()
        records = store.records
        records.append("bogus")  # type: ignore[arg-type]
        self.assertEqual(len(store.records), 0)

    def test_multiple_appends(self) -> None:
        from datetime import datetime, timezone

        from capability_pack import EvidenceStore, ExecutionRecord, ResultState

        store = EvidenceStore()
        now = datetime.now(timezone.utc)
        for i in range(3):
            record = ExecutionRecord(
                capability_version=f"0.{i}.0",
                target_identity="bench-01",
                prerequisite_values={},
                prerequisite_modes={},
                authorization_scope_ref="ref",
                start_time=now,
                end_time=now,
                transition_log=[(ResultState.PREVIEWED, now)],
                verification_evidence={},
                stop_reason="completed",
                hardware_identity="sdr-001",
            )
            store.append(record)
        self.assertEqual(len(store.records), 3)

    def test_stored_record_cannot_be_mutated_in_place(self) -> None:
        """A stored record's container fields are deeply immutable."""
        from datetime import datetime, timezone

        from capability_pack import EvidenceStore, ExecutionRecord, ResultState

        store = EvidenceStore()
        now = datetime.now(timezone.utc)
        evidence = {"packets": [{"channel": 1}], "ok": True}
        record = ExecutionRecord(
            capability_version="0.1.0",
            target_identity="bench-01",
            prerequisite_values={"uid": "aabbcc"},
            prerequisite_modes={},
            authorization_scope_ref="ref",
            start_time=now,
            end_time=now,
            transition_log=[(ResultState.PREVIEWED, now)],
            verification_evidence=evidence,
            stop_reason="completed",
            hardware_identity="sdr-001",
        )
        store.append(record)

        # Mutating the original input dict must not change the stored record.
        evidence["ok"] = False
        evidence["packets"].append({"channel": 99})

        stored = store.records[0]
        self.assertTrue(stored.verification_evidence["ok"])
        self.assertEqual(len(stored.verification_evidence["packets"]), 1)

        # Direct in-place mutation of the frozen field must raise.
        with self.assertRaises(TypeError):
            stored.verification_evidence["ok"] = False  # type: ignore[index]


# ---------------------------------------------------------------------------
# Seam 4: Headless pipeline integration
# ---------------------------------------------------------------------------

def _build_reference_pack():
    """Return the ELRSDesyncPack used across test_capability_pack.py."""
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
        def prerequisites(self) -> list:
            return [
                Prerequisite(
                    name="target_uid",
                    mode=PrerequisiteMode.WIRELESSLY_ACQUIRED,
                    description="Complete 6-byte UID from passive SYNC capture",
                ),
            ]

        @property
        def effects(self) -> list:
            return [Effect(name="desync", destructive=False, description="Temporary receiver desynchronization")]

        @property
        def hardware_requirements(self) -> list:
            return [HardwareRequirement(name="sdr_transceiver", description="Ettus B210 or B205mini")]

        @property
        def falsifiers(self) -> list:
            return [Falsifier(description="Receiver rejects forged SYNC", condition="CRC mismatch")]

        def preview(self, params):
            return {"description": "Will transmit forged SYNC", "side_effects": False}

        def arm(self, params):
            return {"armed": True}

        def execute(self, params):
            return {"result_state": "transmitted"}

        def verify(self, params):
            return {"evidence_level": "candidate"}

        def stop(self):
            return {"stopped": True}

    return ELRSDesyncPack()


class TestHeadlessPipelineIntegration(unittest.TestCase):
    """Full headless pipeline: register -> match -> arm -> execute -> evidence."""

    def test_full_pipeline_previewed_through_effect_confirmed(self) -> None:
        from datetime import timedelta

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

        # 1. Register
        registry = PackRegistry()
        pack = _build_reference_pack()
        registry.register(pack)

        # 2. Match target
        target = TargetDescriptor(
            identity="tinyhawk-iii-bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )
        matches = registry.query(target.as_query())
        self.assertEqual(len(matches), 1)

        # 3. Arm scope
        scope_mgr = ScopeManager(registry)

        # 4. Execute via fake executor with full canned sequence
        executor = FakeExecutor(
            canned_sequence=[
                ResultState.PREVIEWED,
                ResultState.ATTEMPTED,
                ResultState.TRANSMITTED,
                ResultState.TARGET_ACCEPTED,
                ResultState.EFFECT_CONFIRMED,
            ],
            canned_evidence={"packets_sent": 42, "desync_observed": True},
        )

        evidence_store = EvidenceStore()

        record = run_headless_pipeline(
            registry=registry,
            scope_manager=scope_mgr,
            executor=executor,
            target=target,
            pack_name="elrs-desync",
            pack_version="0.1.0",
            parameters={"target_uid": "aabbccddeeff"},
            prerequisite_modes={"target_uid": PrerequisiteMode.WIRELESSLY_ACQUIRED},
            hardware_identity="ettus-b210-serial-001",
            time_limit=timedelta(minutes=5),
            evidence_store=evidence_store,
        )

        # 5. Inspect evidence record
        self.assertEqual(record.capability_version, "0.1.0")
        self.assertEqual(record.target_identity, "tinyhawk-iii-bench-01")
        self.assertEqual(record.stop_reason, "completed")
        self.assertEqual(record.hardware_identity, "ettus-b210-serial-001")
        self.assertEqual(
            record.prerequisite_modes["target_uid"],
            PrerequisiteMode.WIRELESSLY_ACQUIRED,
        )
        self.assertEqual(len(record.transition_log), 5)
        self.assertEqual(
            record.transition_log[-1][0], ResultState.EFFECT_CONFIRMED
        )
        self.assertIsNotNone(record.start_time)
        self.assertIsNotNone(record.end_time)
        self.assertGreaterEqual(record.end_time, record.start_time)

        # Evidence store has the record
        self.assertEqual(len(evidence_store.records), 1)
        self.assertIs(evidence_store.records[0], record)

    def test_preview_produces_no_side_effects(self) -> None:
        """Preview step is read-only: the pack.preview() call must not
        produce any state transition beyond PREVIEWED."""
        from datetime import timedelta

        from capability_pack import (
            EvidenceStore,
            FakeExecutor,
            PackRegistry,
            ResultState,
            ScopeManager,
            TargetDescriptor,
            run_headless_pipeline,
        )

        registry = PackRegistry()
        pack = _build_reference_pack()
        registry.register(pack)

        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )

        # Only PREVIEWED -- verify preview has no side effects
        executor = FakeExecutor(
            canned_sequence=[ResultState.PREVIEWED],
            canned_evidence={"side_effects": False},
        )

        record = run_headless_pipeline(
            registry=registry,
            scope_manager=ScopeManager(registry),
            executor=executor,
            target=target,
            pack_name="elrs-desync",
            pack_version="0.1.0",
            parameters={},
            prerequisite_modes={},
            hardware_identity="sdr-001",
            time_limit=timedelta(minutes=1),
            evidence_store=EvidenceStore(),
        )

        self.assertEqual(len(record.transition_log), 1)
        self.assertEqual(record.transition_log[0][0], ResultState.PREVIEWED)
        self.assertEqual(record.stop_reason, "completed")

    def test_timeout_produces_correct_stop_reason(self) -> None:
        from datetime import timedelta

        from capability_pack import (
            EvidenceStore,
            FakeExecutor,
            PackRegistry,
            ResultState,
            ScopeManager,
            TargetDescriptor,
            run_headless_pipeline,
        )

        registry = PackRegistry()
        registry.register(_build_reference_pack())
        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )

        executor = FakeExecutor(
            canned_sequence=[
                ResultState.PREVIEWED,
                ResultState.ATTEMPTED,
                ResultState.TRANSMITTED,
            ],
            canned_evidence={},
            timeout_after_step=2,
        )

        record = run_headless_pipeline(
            registry=registry,
            scope_manager=ScopeManager(registry),
            executor=executor,
            target=target,
            pack_name="elrs-desync",
            pack_version="0.1.0",
            parameters={},
            prerequisite_modes={},
            hardware_identity="sdr-001",
            time_limit=timedelta(minutes=1),
            evidence_store=EvidenceStore(),
        )

        self.assertEqual(record.stop_reason, "timeout")
        self.assertEqual(len(record.transition_log), 2)

    def test_cancellation_produces_correct_stop_and_partial_evidence(self) -> None:
        from datetime import timedelta

        from capability_pack import (
            EvidenceStore,
            FakeExecutor,
            PackRegistry,
            ResultState,
            ScopeManager,
            TargetDescriptor,
            run_headless_pipeline,
        )

        registry = PackRegistry()
        registry.register(_build_reference_pack())
        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )

        executor = FakeExecutor(
            canned_sequence=[
                ResultState.PREVIEWED,
                ResultState.ATTEMPTED,
                ResultState.TRANSMITTED,
            ],
            canned_evidence={"partial": True},
            cancel_after_step=1,
        )

        evidence_store = EvidenceStore()
        record = run_headless_pipeline(
            registry=registry,
            scope_manager=ScopeManager(registry),
            executor=executor,
            target=target,
            pack_name="elrs-desync",
            pack_version="0.1.0",
            parameters={},
            prerequisite_modes={},
            hardware_identity="sdr-001",
            time_limit=timedelta(minutes=1),
            evidence_store=evidence_store,
        )

        self.assertEqual(record.stop_reason, "cancelled")
        self.assertEqual(len(record.transition_log), 1)
        # Partial evidence is still captured
        self.assertTrue(record.verification_evidence.get("partial"))
        # Record still lands in the store
        self.assertEqual(len(evidence_store.records), 1)

    def test_prerequisite_mode_recorded_in_evidence(self) -> None:
        from datetime import timedelta

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

        registry = PackRegistry()
        registry.register(_build_reference_pack())
        target = TargetDescriptor(
            identity="bench-01",
            protocol_version="ELRS v3 OTA",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
        )

        executor = FakeExecutor(
            canned_sequence=[ResultState.PREVIEWED],
            canned_evidence={},
        )

        modes = {
            "target_uid": PrerequisiteMode.OPERATOR_SUPPLIED,
            "fhss_seed": PrerequisiteMode.DERIVED,
        }

        record = run_headless_pipeline(
            registry=registry,
            scope_manager=ScopeManager(registry),
            executor=executor,
            target=target,
            pack_name="elrs-desync",
            pack_version="0.1.0",
            parameters={"target_uid": "aabb", "fhss_seed": "1234"},
            prerequisite_modes=modes,
            hardware_identity="sdr-001",
            time_limit=timedelta(minutes=1),
            evidence_store=EvidenceStore(),
        )

        self.assertEqual(
            record.prerequisite_modes["target_uid"],
            PrerequisiteMode.OPERATOR_SUPPLIED,
        )
        self.assertEqual(
            record.prerequisite_modes["fhss_seed"],
            PrerequisiteMode.DERIVED,
        )


if __name__ == "__main__":
    unittest.main()
