"""Capability Pack type system: contract, registry, and scoped authorization.

Pure-Python module with no Flask or third-party dependencies.  Domain
vocabulary follows GLOSSARY.md: Capability Pack, Applicability Boundary,
Sacrificial Target, Prerequisite Mode, Evidence Level, Result State.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Domain enums
# ---------------------------------------------------------------------------

class PrerequisiteMode(enum.Enum):
    WIRELESSLY_ACQUIRED = "wirelessly_acquired"
    DERIVED = "derived"
    OPERATOR_SUPPLIED = "operator_supplied"


class EvidenceLevel(enum.Enum):
    CANDIDATE = "candidate"
    OFFLINE_VALIDATED = "offline_validated"
    PASSIVE_VALIDATED = "passive_validated"
    BENCH_VALIDATED = "bench_validated"
    FIELD_READY = "field_ready"


class ResultState(enum.Enum):
    PREVIEWED = "previewed"
    ATTEMPTED = "attempted"
    TRANSMITTED = "transmitted"
    TARGET_ACCEPTED = "target_accepted"
    EFFECT_CONFIRMED = "effect_confirmed"


# Monotonic ordering for Result State machine validation.
_RESULT_STATE_ORDER: dict[ResultState, int] = {
    ResultState.PREVIEWED: 0,
    ResultState.ATTEMPTED: 1,
    ResultState.TRANSMITTED: 2,
    ResultState.TARGET_ACCEPTED: 3,
    ResultState.EFFECT_CONFIRMED: 4,
}


class InvalidTransitionError(Exception):
    """Raised when a Result State transition violates monotonic ordering."""


class ResultStateMachine:
    """Validates monotonic Result State progression.

    Each transition must advance strictly forward through the ordering:
    PREVIEWED -> ATTEMPTED -> TRANSMITTED -> TARGET_ACCEPTED -> EFFECT_CONFIRMED.
    Backward, repeated, or skipped states are rejected.
    """

    def __init__(self) -> None:
        self._current: ResultState | None = None
        self._log: list[tuple[ResultState, datetime]] = []

    @property
    def current(self) -> ResultState | None:
        return self._current

    @property
    def transition_log(self) -> list[tuple[ResultState, datetime]]:
        return list(self._log)

    def transition(self, state: ResultState) -> None:
        """Advance to *state*, raising on invalid or non-monotonic moves.

        Transitions must be strictly sequential: each new state's ordinal
        must be exactly one greater than the current state's ordinal (or
        zero when starting from no state).
        """
        new_order = _RESULT_STATE_ORDER[state]
        expected = 0 if self._current is None else _RESULT_STATE_ORDER[self._current] + 1

        if new_order != expected:
            raise InvalidTransitionError(
                f"Cannot transition from {self._current.value if self._current else 'None'!r} "
                f"to {state.value!r}: expected ordinal {expected}, got {new_order}"
            )

        self._current = state
        self._log.append((state, datetime.now(timezone.utc)))


# ---------------------------------------------------------------------------
# Metadata dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackIdentity:
    name: str
    version: str
    description: str


@dataclass(frozen=True)
class ApplicabilityBoundary:
    protocol_version: str
    phy_domain: str
    firmware_impl: str
    firmware_version: str
    configuration: dict[str, Any] = field(default_factory=dict)
    prerequisite_knowledge: list[str] = field(default_factory=list)
    access_conditions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Prerequisite:
    name: str
    mode: PrerequisiteMode
    description: str


@dataclass(frozen=True)
class Effect:
    name: str
    destructive: bool
    description: str


@dataclass(frozen=True)
class HardwareRequirement:
    name: str
    description: str


@dataclass(frozen=True)
class Falsifier:
    description: str
    condition: str


# ---------------------------------------------------------------------------
# Capability Pack protocol (ABC)
# ---------------------------------------------------------------------------

class CapabilityPack(ABC):
    """Target-scoped, human-operable wireless attack product.

    Subclasses declare their metadata via properties and implement the
    five-phase lifecycle: preview, arm, execute, verify, stop.
    """

    @property
    @abstractmethod
    def identity(self) -> PackIdentity: ...

    @property
    @abstractmethod
    def applicability(self) -> ApplicabilityBoundary: ...

    @property
    @abstractmethod
    def prerequisites(self) -> list[Prerequisite]: ...

    @property
    @abstractmethod
    def effects(self) -> list[Effect]: ...

    @property
    @abstractmethod
    def hardware_requirements(self) -> list[HardwareRequirement]: ...

    @property
    @abstractmethod
    def falsifiers(self) -> list[Falsifier]: ...

    @abstractmethod
    def preview(self, params: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def arm(self, params: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def execute(self, params: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def verify(self, params: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def stop(self) -> dict[str, Any]: ...

    @property
    def has_destructive_effects(self) -> bool:
        return any(e.destructive for e in self.effects)


# ---------------------------------------------------------------------------
# Pack Registry
# ---------------------------------------------------------------------------

_APPLICABILITY_MATCH_FIELDS = (
    "protocol_version",
    "phy_domain",
    "firmware_impl",
    "firmware_version",
)


class PackRegistry:
    """Dict-based store for registered Capability Packs.

    Supports lookup by identity (name + version) and query by target
    applicability.  A target descriptor is a dict whose keys are a
    subset of ApplicabilityBoundary field names; every supplied field
    must match exactly.
    """

    def __init__(self) -> None:
        self._packs: dict[tuple[str, str], CapabilityPack] = {}

    def register(self, pack: CapabilityPack) -> None:
        key = (pack.identity.name, pack.identity.version)
        if key in self._packs:
            raise ValueError(
                f"Pack already registered: {pack.identity.name} v{pack.identity.version}"
            )
        self._packs[key] = pack

    def lookup(self, name: str, version: str) -> CapabilityPack | None:
        return self._packs.get((name, version))

    def query(self, target: dict[str, Any]) -> list[CapabilityPack]:
        matches: list[CapabilityPack] = []
        for pack in self._packs.values():
            boundary = pack.applicability
            if self._target_matches(boundary, target):
                matches.append(pack)
        return matches

    @staticmethod
    def _target_matches(boundary: ApplicabilityBoundary, target: dict[str, Any]) -> bool:
        for field_name in _APPLICABILITY_MATCH_FIELDS:
            target_value = target.get(field_name)
            if target_value is None:
                continue
            boundary_value = getattr(boundary, field_name, None)
            if boundary_value == "*":
                continue
            if target_value != boundary_value:
                return False
        return True


# ---------------------------------------------------------------------------
# Target descriptor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TargetDescriptor:
    identity: str
    protocol_version: str
    firmware_impl: str
    firmware_version: str

    def as_query(self) -> dict[str, str]:
        return {
            "protocol_version": self.protocol_version,
            "firmware_impl": self.firmware_impl,
            "firmware_version": self.firmware_version,
        }


# ---------------------------------------------------------------------------
# Authorization scope
# ---------------------------------------------------------------------------

class AuthorizationError(Exception):
    pass


class AuthorizationScope:
    """Time-limited authorization for a specific pack against a specific target."""

    def __init__(
        self,
        *,
        target: TargetDescriptor,
        pack_identity: PackIdentity,
        permitted_actions: frozenset[str],
        bounds: dict[str, Any],
        time_limit: timedelta,
        stop_conditions: list[str],
        sacrificial_target: bool,
        evidence_destination: str,
        created_at: datetime | None = None,
    ) -> None:
        self.target = target
        self.pack_identity = pack_identity
        self.permitted_actions = permitted_actions
        self.bounds = bounds
        self.time_limit = time_limit
        self.stop_conditions = stop_conditions
        self.sacrificial_target = sacrificial_target
        self.evidence_destination = evidence_destination
        self._created_at = created_at or datetime.now(timezone.utc)
        self._disarmed = False

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def expiry(self) -> datetime:
        return self._created_at + self.time_limit

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expiry

    @property
    def disarmed(self) -> bool:
        return self._disarmed

    def disarm(self) -> None:
        self._disarmed = True


# ---------------------------------------------------------------------------
# Scope manager
# ---------------------------------------------------------------------------

class ScopeManager:
    """Creates, tracks, and enforces authorization scopes.

    Arming validates the pack exists, the target matches the pack's
    Applicability Boundary, and destructive packs require Sacrificial
    Target designation.
    """

    def __init__(self, registry: PackRegistry) -> None:
        self._registry = registry
        self._scopes: dict[tuple[str, str, str], AuthorizationScope] = {}

    def arm(
        self,
        *,
        pack_name: str,
        pack_version: str,
        target: TargetDescriptor,
        permitted_actions: frozenset[str],
        bounds: dict[str, Any],
        time_limit: timedelta,
        stop_conditions: list[str],
        sacrificial_target: bool,
        evidence_destination: str,
    ) -> AuthorizationScope:
        pack = self._registry.lookup(pack_name, pack_version)
        if pack is None:
            raise LookupError(
                f"Pack not found: {pack_name} v{pack_version}"
            )

        if not PackRegistry._target_matches(pack.applicability, target.as_query()):
            raise ValueError(
                f"Target {target.identity!r} does not match Applicability Boundary "
                f"of {pack_name} v{pack_version}"
            )

        if pack.has_destructive_effects and not sacrificial_target:
            raise ValueError(
                f"Pack {pack_name} has destructive effects; "
                f"Sacrificial Target designation is required"
            )

        scope = AuthorizationScope(
            target=target,
            pack_identity=pack.identity,
            permitted_actions=permitted_actions,
            bounds=bounds,
            time_limit=time_limit,
            stop_conditions=stop_conditions,
            sacrificial_target=sacrificial_target,
            evidence_destination=evidence_destination,
        )
        key = (pack_name, pack_version, target.identity)
        self._scopes[key] = scope
        return scope

    def disarm(
        self,
        *,
        pack_name: str,
        pack_version: str,
        target_identity: str,
    ) -> None:
        key = (pack_name, pack_version, target_identity)
        scope = self._scopes.get(key)
        if scope is not None:
            scope.disarm()

    def list_scopes(self) -> list[tuple[tuple[str, str, str], AuthorizationScope]]:
        """Return a snapshot of all (key, scope) pairs."""
        return list(self._scopes.items())

    def authorize(
        self,
        *,
        pack_name: str,
        pack_version: str,
        target_identity: str,
        action: str,
    ) -> AuthorizationScope:
        key = (pack_name, pack_version, target_identity)
        scope = self._scopes.get(key)

        if scope is None or scope.disarmed:
            raise AuthorizationError(
                f"No active scope for {pack_name} v{pack_version} "
                f"against target {target_identity!r}"
            )

        if scope.is_expired:
            raise AuthorizationError(
                f"Scope expired at {scope.expiry.isoformat()} for "
                f"{pack_name} v{pack_version}"
            )

        if scope.target.identity != target_identity:
            raise AuthorizationError(
                f"Target mismatch: scope authorizes {scope.target.identity!r}, "
                f"requested {target_identity!r}"
            )

        if action not in scope.permitted_actions:
            raise AuthorizationError(
                f"Action {action!r} not in permitted actions "
                f"{sorted(scope.permitted_actions)}"
            )

        return scope


# ---------------------------------------------------------------------------
# Executor contract: input, output, and evidence record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutorInput:
    """Structured input for a capability executor."""
    pack_identity: PackIdentity
    target: TargetDescriptor
    scope_reference: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ExecutorOutput:
    """Structured output from a capability executor."""
    transition_log: list[tuple[ResultState, datetime]]
    evidence: dict[str, Any]
    stop_reason: str
    final_state: ResultState


@dataclass(frozen=True)
class ExecutionRecord:
    """Append-only evidence record.  Frozen after creation -- never mutated."""
    capability_version: str
    target_identity: str
    prerequisite_values: dict[str, Any]
    prerequisite_modes: dict[str, PrerequisiteMode]
    authorization_scope_ref: str
    start_time: datetime
    end_time: datetime
    transition_log: list[tuple[ResultState, datetime]]
    verification_evidence: dict[str, Any]
    stop_reason: str
    hardware_identity: str


# ---------------------------------------------------------------------------
# Executor: timeout, cancellation, fake implementation
# ---------------------------------------------------------------------------

class ExecutorTimeoutError(Exception):
    """Raised when an executor exceeds its deadline."""


class FakeExecutor:
    """Deterministic executor for testing.

    Accepts a canned Result State sequence and produces a predictable
    ExecutorOutput.  Simulates timeout and cancellation via step limits.

    Parameters
    ----------
    canned_sequence : list[ResultState]
        The sequence of states to transition through.
    canned_evidence : dict[str, Any]
        Evidence dict returned in the output.
    timeout_after_step : int | None
        If set, simulate a timeout after this many steps (1-indexed).
    cancel_after_step : int | None
        If set, simulate cancellation after this many steps (1-indexed).
    """

    def __init__(
        self,
        *,
        canned_sequence: list[ResultState],
        canned_evidence: dict[str, Any],
        timeout_after_step: int | None = None,
        cancel_after_step: int | None = None,
    ) -> None:
        self._sequence = canned_sequence
        self._evidence = canned_evidence
        self._timeout_after = timeout_after_step
        self._cancel_after = cancel_after_step

    def run(self, inp: ExecutorInput) -> ExecutorOutput:
        if not self._sequence:
            raise ValueError(
                "FakeExecutor requires a non-empty canned_sequence"
            )

        sm = ResultStateMachine()
        stop_reason = "completed"

        for i, state in enumerate(self._sequence, start=1):
            # Check timeout before this step
            if self._timeout_after is not None and i > self._timeout_after:
                stop_reason = "timeout"
                break

            # Check cancellation before this step
            if self._cancel_after is not None and i > self._cancel_after:
                stop_reason = "cancelled"
                break

            sm.transition(state)

        return ExecutorOutput(
            transition_log=sm.transition_log,
            evidence=dict(self._evidence),
            stop_reason=stop_reason,
            final_state=sm.current if sm.current is not None else self._sequence[0],
        )


# ---------------------------------------------------------------------------
# SimulationPack — concrete pack for dashboard simulation / dry-run mode
# ---------------------------------------------------------------------------

class SimulationPack(CapabilityPack):
    """Concrete pack driven by declarative metadata dicts.

    Used by the web dashboard to represent each attack chain as a
    CapabilityPack without requiring real hardware executors.  The
    ``chain`` field links it to the target's ``chain`` value so the
    route handler can resolve an applicable pack from the registry.
    """

    def __init__(
        self,
        *,
        name: str,
        version: str,
        description: str,
        chain: str,
        actions: list[dict[str, Any]],
        protocol_version: str = "*",
        phy_domain: str = "*",
        firmware_impl: str = "*",
        firmware_version: str = "*",
        effects: list[dict[str, Any]] | None = None,
        prerequisites: list[dict[str, Any]] | None = None,
        hardware_requirements: list[dict[str, Any]] | None = None,
        falsifiers: list[dict[str, Any]] | None = None,
    ) -> None:
        self._identity = PackIdentity(name=name, version=version, description=description)
        self._chain = chain
        self._actions = actions
        self._applicability = ApplicabilityBoundary(
            protocol_version=protocol_version,
            phy_domain=phy_domain,
            firmware_impl=firmware_impl,
            firmware_version=firmware_version,
        )
        self._effects = [
            Effect(name=e["name"], destructive=e.get("destructive", False), description=e.get("description", ""))
            for e in (effects or [])
        ]
        self._prerequisites = [
            Prerequisite(name=p["name"], mode=p.get("mode", PrerequisiteMode.OPERATOR_SUPPLIED), description=p.get("description", ""))
            for p in (prerequisites or [])
        ]
        self._hardware_requirements = [
            HardwareRequirement(name=h["name"], description=h.get("description", ""))
            for h in (hardware_requirements or [])
        ]
        self._falsifiers = [
            Falsifier(description=f["description"], condition=f.get("condition", ""))
            for f in (falsifiers or [])
        ]

    @property
    def chain(self) -> str:
        return self._chain

    @property
    def identity(self) -> PackIdentity:
        return self._identity

    @property
    def applicability(self) -> ApplicabilityBoundary:
        return self._applicability

    @property
    def prerequisites(self) -> list[Prerequisite]:
        return list(self._prerequisites)

    @property
    def effects(self) -> list[Effect]:
        return list(self._effects)

    @property
    def hardware_requirements(self) -> list[HardwareRequirement]:
        return list(self._hardware_requirements)

    @property
    def falsifiers(self) -> list[Falsifier]:
        return list(self._falsifiers)

    @property
    def action_names(self) -> list[str]:
        return [a["name"] for a in self._actions]

    @property
    def action_defs(self) -> list[dict[str, Any]]:
        return list(self._actions)

    def preview(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "unknown")
        return {"description": f"Preview of {action}", "side_effects": False}

    def arm(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"armed": True}

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"result_state": "transmitted", "simulated": True}

    def verify(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"result_state": "previewed", "evidence_level": "candidate"}

    def stop(self) -> dict[str, Any]:
        return {"stopped": True}

    def to_metadata(self) -> dict[str, Any]:
        """Serialize pack metadata for the ``/api/actions`` response."""
        return {
            "name": self._identity.name,
            "version": self._identity.version,
            "description": self._identity.description,
            "chain": self._chain,
            "actions": list(self._actions),
            "effects": [
                {"name": e.name, "destructive": e.destructive, "description": e.description}
                for e in self._effects
            ],
            "prerequisites": [
                {"name": p.name, "mode": p.mode.value, "description": p.description}
                for p in self._prerequisites
            ],
            "hardware_requirements": [
                {"name": h.name, "description": h.description}
                for h in self._hardware_requirements
            ],
        }


# ---------------------------------------------------------------------------
# Evidence store (append-only)
# ---------------------------------------------------------------------------

class EvidenceStore:
    """Append-only collection of ExecutionRecords.

    Records are never retroactively edited.  The ``records`` property
    returns a shallow copy so callers cannot mutate the internal list.
    """

    def __init__(self) -> None:
        self._records: list[ExecutionRecord] = []

    def append(self, record: ExecutionRecord) -> None:
        self._records.append(record)

    @property
    def records(self) -> list[ExecutionRecord]:
        return list(self._records)


# ---------------------------------------------------------------------------
# Headless pipeline
# ---------------------------------------------------------------------------

def run_headless_pipeline(
    *,
    registry: PackRegistry,
    scope_manager: ScopeManager,
    executor: FakeExecutor,
    target: TargetDescriptor,
    pack_name: str,
    pack_version: str,
    parameters: dict[str, Any],
    prerequisite_modes: dict[str, PrerequisiteMode],
    hardware_identity: str,
    time_limit: timedelta,
    evidence_store: EvidenceStore,
) -> ExecutionRecord:
    """Wire registry -> scope_manager -> executor -> evidence in one call.

    Returns the completed ExecutionRecord and appends it to *evidence_store*.
    """
    # 1. Lookup pack
    pack = registry.lookup(pack_name, pack_version)
    if pack is None:
        raise LookupError(f"Pack not found: {pack_name} v{pack_version}")

    # 2. Arm scope
    scope = scope_manager.arm(
        pack_name=pack_name,
        pack_version=pack_version,
        target=target,
        permitted_actions=frozenset({"preview", "arm", "execute", "verify", "stop"}),
        bounds={},
        time_limit=time_limit,
        stop_conditions=["operator_abort"],
        sacrificial_target=pack.has_destructive_effects,
        evidence_destination="headless-pipeline",
    )

    # 3. Authorize execute action
    scope_manager.authorize(
        pack_name=pack_name,
        pack_version=pack_version,
        target_identity=target.identity,
        action="execute",
    )

    # 4. Build executor input
    inp = ExecutorInput(
        pack_identity=pack.identity,
        target=target,
        scope_reference=f"{pack_name}/{pack_version}/{target.identity}",
        parameters=parameters,
    )

    # 5. Run executor
    start_time = datetime.now(timezone.utc)
    output = executor.run(inp)
    end_time = datetime.now(timezone.utc)

    # 6. Build evidence record
    record = ExecutionRecord(
        capability_version=pack_version,
        target_identity=target.identity,
        prerequisite_values=dict(parameters),
        prerequisite_modes=dict(prerequisite_modes),
        authorization_scope_ref=inp.scope_reference,
        start_time=start_time,
        end_time=end_time,
        transition_log=output.transition_log,
        verification_evidence=output.evidence,
        stop_reason=output.stop_reason,
        hardware_identity=hardware_identity,
    )

    # 7. Append to evidence store (append-only, never mutate)
    evidence_store.append(record)

    return record
