# Wireless Capability Program

Canonical language for designing, validating, and operating reusable wireless
offensive capabilities in authorized CTF and research environments.

## Capability

**Capability Pack**:
A target-scoped, human-operable wireless attack product with declared applicability, prerequisites, authorization bounds, verification, evidence, limitations, reliability, and falsification criteria.
_Avoid_: Tool, script, PoC, decoder, dashboard, research result

**Wireless Entry**:
The first adversarial delivery step into a target chain over RF or another wireless transport.
_Avoid_: Wired entry, direct-attached entry

**Downstream Effect Primitive**:
A command, protocol interaction, state transition, or exploit reached after Wireless Entry; a primitive requiring direct attacker cable attachment is fixture evidence only.
_Avoid_: Wireless Entry

**Applicability Boundary**:
The exact protocol, physical layer, domain, implementation, version, configuration, prerequisite knowledge, and access conditions for which evidence holds.
_Avoid_: Universal applicability, blanket protocol claim

**Operational Reliability**:
The measured success frequency under declared conditions, bounded retries, and time limits; it is independent of the Applicability Boundary.
_Avoid_: Applicability, theoretical feasibility

## Targets

**Proving Fixture**:
An owned target used to establish mechanics and evidence without implying that it represents the eventual competition population.
_Avoid_: Competition target, representative target

**Stock Target**:
A target in the software, firmware, and hardware configuration in which it is ordinarily deployed or presented.
_Avoid_: Simulator, debug build, instrumented target

**Sacrificial Target**:
A replaceable Stock Target explicitly designated for a destructive or potentially irreversible action.
_Avoid_: Proving Fixture, disposable by assumption

## Evidence and Outcomes

**Evidence Level**:
The maturity of a capability claim: `CANDIDATE`, `OFFLINE-VALIDATED`, `PASSIVE-VALIDATED`, `BENCH-VALIDATED`, or `FIELD-READY`; only the last two constitute a completed offensive capability.
_Avoid_: Result State, confidence score

**Result State**:
The observed execution outcome: `PREVIEWED`, `ATTEMPTED`, `TRANSMITTED`, `TARGET_ACCEPTED`, or `EFFECT_CONFIRMED`; transmitting input does not establish target acceptance or effect.
_Avoid_: Evidence Level, success after send

**Prerequisite Mode**:
How required knowledge was obtained: `WIRELESSLY_ACQUIRED`, `DERIVED`, or `OPERATOR_SUPPLIED`; operator-supplied knowledge proves downstream mechanics rather than a general field claim.
_Avoid_: Unstated fixture knowledge

## Work Selection

**Active Delivery Lane**:
The single implementation path responsible for producing the next accepted deliverable.
_Avoid_: Parallel active backlog

**Background Research Lane**:
An isolated exploration with a different owner and no overlap with active hardware or the Active Delivery Lane's files.
_Avoid_: Second active implementation lane
