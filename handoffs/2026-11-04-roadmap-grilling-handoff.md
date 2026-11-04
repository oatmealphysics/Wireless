# Handoff: wireless capability-program roadmap grilling

**Date:** 2026-11-04  
**Author:** Codex  
**Starting revision:** `c5ad259f7cd366152c0adfa84f38f6a36fbb061e`  
**Working tree at start:** clean  
**Canonical remote before the planned migration:** GitLab (`enclave`)  
**Purpose:** Preserve the complete outcome of the operator/Codex roadmap-grilling
session so another agent can continue without reopening settled decisions or
mistaking intermediate proposals for final policy.

This handoff records planning decisions only. No roadmap, attack tool, RF path,
device state, target configuration, or firmware was changed during the grilling
session. The only non-repository mutation was installation of the Matt Pocock
skills plugin into the user's Codex and Claude environments, described below.

## 1. Executive outcome

The operator rejected the repository's current research-catalogue shape as the
effective way forward and established a capability-delivery program instead.
The product is not a script, decoder, dashboard, packet builder, research note,
or synthetic round trip. The product is a **Capability Pack** that begins with
a wireless delivery path, produces a useful effect against a stock wireless
target, verifies that effect honestly, and is human-operable through the web
UI.

The agreed strategic chain is:

```text
detect -> identify -> acquire prerequisites -> deliver wirelessly
       -> execute -> verify effect -> retain evidence
```

A wireless entry may reach and use a wired, serial, USB, local-network, or
other downstream primitive. A pack that cannot begin with a wireless delivery
path is not an active capability for this repository. Its useful components
may be catalogued, but work moves to another wireless target.

The first proving fixture remains the owned E8/TinyHawk ELRS pair because it has
the deepest evidence. It is not assumed to represent the competition target.
The first planned pack is an ELRS 2.4 GHz OTA v3 receiver-side command/frame
injection chain. The pack is method-agnostic: the bounded RC canary is the
best-supported first hypothesis, not a mandatory technique. Pack v1 is complete
once **one** useful wireless effect reaches the agreed `FIELD-READY` evidence
level. Additional effects become backlog work while the operator supplies a
second stock wireless target.

The authoritative execution order agreed in the session is:

1. Gate 0: coordinated clean-history repository migration.
2. NOW: truth-restoration sprint.
3. NEXT 1: minimal Capability Pack/web-control contract.
4. NEXT 2: time-boxed ELRS Capability Pack 1.
5. Next target: supplied by the operator after Pack 1 v1 succeeds or is
   honestly falsified/blocked.

The two-day and ten-working-day durations discussed below are review
checkpoints and anti-rabbit-hole limits, not delivery promises. Work may finish
faster. Extending past a checkpoint requires recording what was learned, what
remains, and why extension has more value than changing paths.

## 2. Why the old roadmap was rejected as an execution system

The read-only roadmap audit found that the existing roadmap is internally
unable to select the next meaningful task:

- R0 through R7 are marked `DONE`.
- The research section still says R6 is the single active lane even though R6
  is `DONE`.
- Every RQ item is `GATED` with no active owner.
- The file therefore behaves as an archive/research catalogue rather than the
  promised source of truth for `NOW` and ordered `NEXT` work.
- The roadmap contains a superseded description of the orchestrator v3/v4
  defect. `SESSION-NOTES.md` entry #74 correctly reframes it as inconsistent
  version-specific parameters, not a v3/v4 core swap.
- Five known software defects are assigned in `SESSION-NOTES.md` entries #73
  and #74 but were never activated in the roadmap.
- R7 is marked `DONE` although the stated Windows/Python 3.13 hosted matrix is
  not active. The correction in the roadmap itself says hosted CI is presently
  Ubuntu Python 3.10/3.12 only.
- R5's hosted gate did not run because of an account/billing condition. Local
  evidence exists, but the hosted cells are not green.
- RQ23 is framed as a wireless controller-command objective, but its practical
  primitive is wired USB CDC command injection. That is useful scaffolding and
  a possible downstream primitive, not wireless compromise of the controller.
- The E8 MCU, RF chip, and installed ELRS version remain unconfirmed. Related
  EMAX/ELRS 4.1.0 source is not a proven version match for this unit.
- The current 54 CRC-valid passive frames establish
  `PASSIVE-CAPTURE-VALIDATED` parsing only. They do not establish receiver
  acceptance, transmitter identity, coherent multi-channel FHSS agreement, or
  an operational takeover.
- An older guiding document contains a tracked target UID despite the current
  rule that private identifiers and raw captures stay outside tracked Git.
- The current GitLab/GitHub history arrangement prevents literal mirroring and
  blocks ordinary GitHub updates.

The operator's conclusion was not to add another research row. The roadmap
must be reshaped around completed, effect-producing Capability Packs.

## 3. Canonical domain language established by the grilling

These terms should be written into a future `GLOSSARY.md` by
`/grill-with-docs`. No glossary existed at the time of this handoff.

### Capability Pack

A target-scoped, human-operable wireless attack product. It declares target
applicability, prerequisites, wireless entry, downstream execution stages,
authorization bounds, verification, recovery/stop behavior, evidence, known
limitations, reliability, and falsification criteria.

Avoid using **tool**, **script**, **PoC**, **decoder**, **dashboard**, or
**research result** as synonyms for Capability Pack. Those may be components.

### Wireless entry

The first adversarial delivery step into the target chain over RF or another
wireless transport. The target may subsequently expose or be driven through a
wired, serial, USB, local-network, or internal-bus primitive. The end-to-end
capability still qualifies because attacker delivery began wirelessly.

### Downstream effect primitive

A command, local protocol, state transition, or exploit reached after wireless
entry. A USB/serial primitive is valid inside a pack when the wireless chain
delivers or activates it. A primitive that requires the attacker to attach a
cable directly is fixture/scaffolding evidence only.

### Proving fixture

An owned target used to establish mechanics and evidence without implying that
the target represents the eventual competition population. The E8/TinyHawk
pair is Pack 1's proving fixture.

### Stock target

A target running the software and firmware configuration in which it would
actually be deployed or presented for the CTF. Modified firmware, debug builds,
simulators, and instrumentation may support development but cannot supply the
final Pack 1 acceptance evidence.

### Sacrificial target

A replaceable stock target explicitly designated by the operator for a
destructive or potentially irreversible action. The UI must mark the action as
destructive and require selection and confirmation of the exact sacrificial
target before execution.

### Applicability boundary

The exact protocol version, PHY/domain, firmware implementation/version,
configuration, prerequisite knowledge, and access conditions for which the
evidence holds. A result on one TinyHawk configuration is not automatically an
"ELRS attack" or an "SX1280 vulnerability."

### Operational reliability

The measured success frequency under declared conditions, including bounded
retry and time limits. Applicability and reliability are independent axes. A
technique may be broadly applicable but intermittent, or narrow and perfectly
reliable.

### Evidence level

The maturity of a capability claim:

1. `CANDIDATE`: source-derived or synthetic hypothesis.
2. `OFFLINE-VALIDATED`: deterministic fixtures/simulations and independent
   local checks pass.
3. `PASSIVE-VALIDATED`: recorded real traffic agrees with the model.
4. `BENCH-VALIDATED`: an owned stock target accepts the wireless input and
   exhibits the bounded expected effect.
5. `FIELD-READY`: a repeatable operator runbook, prerequisites, timing,
   failure handling, bounds, and verification exist.

Only levels 4 and 5 constitute a completed offensive capability. Level 3 does
not establish target acceptance.

### Result state

The outcome state emitted by the executor and displayed by the UI:

```text
PREVIEWED -> ATTEMPTED -> TRANSMITTED -> TARGET_ACCEPTED -> EFFECT_CONFIRMED
```

Sending bytes is not execution success. `ATTEMPTED` and `TRANSMITTED` must not
be collapsed into `TARGET_ACCEPTED` or `EFFECT_CONFIRMED`.

### Prerequisite mode

How a required UID, phrase, key, version, configuration, or other prerequisite
was obtained:

1. `WIRELESSLY_ACQUIRED`: recovered during the attack chain.
2. `DERIVED`: obtained through a realistic dictionary, protocol weakness, or
   other attack step.
3. `OPERATOR_SUPPLIED`: injected as private/fixture knowledge for bench work.

Only the first two modes may support a general field-ready claim. The third
proves downstream mechanics only.

### Active delivery lane

The single implementation path responsible for producing the next accepted
deliverable.

### Background research lane

An isolated exploration with a different owner, no use of active hardware, and
no overlap with the active writer's files. It may gather facts but may not
silently become a second implementation lane.

## 4. Complete decision register from the grilling

This section preserves each numbered decision and the final operator response.
Where the operator delegated a choice, the resulting adopted decision is
recorded. Where a later answer refined an earlier one, the later interpretation
wins.

### Round 1: mission and portfolio shape

**Q1 - Primary mission:** The operator agreed that the primary outcome is a
repeatable, rules-compliant, game-day wireless attack capability. They expanded
the goal beyond one project: the approach should repeat across multiple
wireless projects and stock targets used for CTFs and red-team practice.
Dashboards, research, decoders, and scripts support this outcome rather than
becoming ends in themselves.

**Q2 - Roadmap shape:** The operator delegated the structure. The adopted
structure is exactly one active `NOW` outcome, at most two ordered `NEXT`
outcomes, and a parked/gated backlog. The existing research catalogue must not
remain the effective task selector.

**Q3 - Target assumption:** The operator agreed that the E8/TinyHawk pair is a
laboratory fixture, not a presumed competition target. More practice targets
will be added later to expand applicability.

**Q4 - Kill criteria:** The operator agreed that every promoted pathway needs
an explicit falsifier, review checkpoint, and stop/switch condition.

### Round 2: reusable product and repository boundary

**Q5 - Capability unit:** The operator accepted Capability Pack as the reusable
product. Scripts, decoders, dashboards, and research documents are components.

**Q6 - Transport honesty:** The operator accepted that USB primitives are
useful for testing, attack discovery, and downstream PoCs but do not satisfy a
wireless objective by themselves. Wired findings should be retained and later
leveraged by wireless delivery chains.

**Q7 - Repository boundary:** The operator initially described the repository
as drones and wireless attacks, then clarified that the deciding boundary is
wireless delivery. Drones, access points, phones, controllers, and wireless IoT
may all belong here. Entirely wired/LAN-only targets belong in other
repositories.

**Q8 - Abstraction threshold:** The operator delegated the choice. Adopted
policy: build the first pack concretely, use a second materially different
stock target to reveal the real shared seam, and only then extract generalized
framework abstractions.

**Q9 - Effect hierarchy:** The operator delegated the choice. Adopted policy:
rank effects by actual CTF scoring when known; absent scoring facts, prefer flag
access or reversible command/control over denial, and denial over
reconnaissance-only results. Reconnaissance is not a completed capability.

### Round 3: immediate sequence and portability

**Q10 - Truth-restoration sprint:** The operator accepted a short,
non-expandable truth-restoration sprint before new attack development. Its
scope is the five known defects, stale roadmap/evidence language, RQ23
wireless-versus-USB framing, private-data/documentation corrections, and local
validation. The original two-day value is a review checkpoint, not a promise.

**Q11 - First Capability Pack:** The operator accepted the E8/TinyHawk ELRS
link as Pack 1's proving fixture because it has the deepest evidence. They
explicitly prefer protocol/chip/firmware-family weaknesses affecting broad
stock target populations over niche E8/TinyHawk-only behavior.

**Q12 - Evidence ladder:** The operator accepted the five evidence levels
listed above and confirmed that only `BENCH-VALIDATED` and `FIELD-READY` count
as completed offensive capabilities.

**Q13 - Human interface:** The operator stated that the completed system will
be controlled through a web UI and must be human-usable. The UI is the primary
operator control plane, while attack logic remains independently testable and
reusable outside Flask.

**Q14 - Wired findings:** The final policy is not to exclude wired/serial/USB
effect primitives when they belong to a target reached through a wireless
delivery chain. Wired-only attacker entry does not qualify; wireless entry
followed by a local primitive does.

**Q15 - Repository authority:** The operator ultimately chose literal one-for-
one GitHub/GitLab repositories, not merely tree-equivalent snapshots. Later
answers set GitHub as primary and GitLab as the deterministic mirror.

### Round 4: applicability, prerequisites, and mirroring

**Q16 - Applicability boundary:** After clarification, the operator agreed to
broaden claims only as evidence broadens. One TinyHawk success supports the
exact tested configuration. Source agreement plus another independent stock
implementation may support an ELRS implementation-family claim. An SX1280 chip
claim requires a flaw actually residing in and triggerable against the chip.

The operator noted that an attack working around 50% of the time may still be
useful and could be refined later. The final model does not call 50% "general
success"; it reports applicability and reliability separately. A technique may
be operationally useful if bounded retries deliver adequate mission-level
success within the CTF time budget.

**Q17 - Remote-first selection:** The operator agreed to prioritize protocol
and remotely reachable stock-firmware weaknesses. A broadly deployed chip flaw
is strategically irrelevant to this program if exploitation requires SPI,
debug attachment, opening the target, or modified firmware.

**Q18 - Prerequisite budget:** The operator agreed that missing prerequisites
should become explicit attack-chain stages. For example, the chain should try
to extract or derive an ELRS UID/binding phrase before launching the effect.
Private bench knowledge is only a fixture; field claims must either acquire the
prerequisite realistically or declare the limitation and continue trying to
defeat it.

**Q19 - Wired catalogue:** The operator rejected excluding local primitives.
The final rule is the wireless-entry/downstream-effect distinction described
above.

**Q20 - Literal mirroring:** The operator rejected legacy-history differences.
They want a clone from GitHub and a clone from GitLab to be the same repository.
Because GitHub rejects the current 5.43 GiB reachable history, the adopted
solution is a separately controlled, one-time clean-history migration:

1. Archive the original full history outside the active remotes.
2. Remove capture blobs from the active history.
3. Create one cleaned shared history.
4. Replace both remote branches in a coordinated maintenance window.
5. Re-clone every workstation.
6. Prohibit future history rewrites after the migration.

This requires a narrow, explicit exception to the existing "never rewrite
history/force-push" rule. The migration has been approved as a roadmap action
but has **not** been executed.

**Q21 - Mirror topology:** The operator accepted one-directional deterministic
replication rather than active-active writes. A later answer designated GitHub
as the primary/write repository and GitLab as its identical mirror.

### Round 5: UI architecture and truthful execution

**Q22 - UI control plane:** The operator agreed that the web UI controls all
attacks but Flask is not the sole implementation or testing location. The
agreed conceptual chain is:

```text
Web UI -> Capability Pack registry -> scoped executor -> attack module
       -> verifier -> structured evidence
```

**Q23 - Live-action authorization:** The operator agreed to replace the global
all-actions live switch and single-click semantics with per-target,
per-capability, time-limited arming bound to an approved scope record.

**Q24 - Truthful result states:** The operator accepted the shared result-state
progression. The UI must not report success merely because bytes were handed to
a socket or SDR.

**Q25 - Pack trust model:** The operator accepted repository-owned, reviewed
packs first. Arbitrary third-party plugin loading is deferred because it would
require dependency isolation, provenance/signature policy, permissions, and
containment.

### Round 6: reliability, stock targets, and Pack 1

**Q26 - Reliability semantics:** The operator accepted separate applicability
and reliability reporting. Record the numerator/denominator, conditions,
bounded retry/time behavior, and failure classes. An intermittent attack may
still be valuable, but its maturity must be explicit.

**Q27 - Stock-target requirement:** The operator agreed that levels 4 and 5
must succeed against unmodified stock target firmware/hardware.

**Q28 - First protocol capability:** The operator selected ELRS 2.4 GHz OTA v3
receiver-side command/frame injection as Pack 1. The desired high-level effects
include forcing a drone/controller to land, crash, turn off, or become
temporarily or permanently inoperable. Subsequent factual analysis found that
current source/tool evidence most strongly supports RC control displacement;
power-off and permanent disable are not presently supported by the ELRS path.
Pack 1 is not required to deliver every listed effect: one verified useful
effect completes v1.

**Q29 - UI implementation order:** The operator accepted a minimal Capability
Pack contract before Pack 1 and a broader framework only after another pack
tests the seam.

**Q30 - Migration sequence:** The operator accepted the repository migration
as Gate 0 before truth-restoration implementation. The later request to write
this handoff is a planning-preservation action; the migration itself remains
unperformed.

### Round 7: effect validation, destructive targets, and prerequisites

**Q31 - Grounded acceptance evidence:** The operator agreed that grounded,
props-removed state evidence can prove the causal exploit chain during
development.

**Q32 - Effect primitives:** The operator accepted bounded primitives such as
RC override, disarm, failsafe, or temporary link denial rather than a generic
hard-coded "crash" action. The operator chooses the rules-permitted effect.

**Q33 - Destructive effects:** The operator rejected a blanket prohibition on
physical damage because their practice targets are replaceable. Final policy:
destructive or irreversible actions are permitted only for an explicitly
designated sacrificial target and with the enhanced UI confirmation defined in
Q37. The assistant should not impose an extra preservation rule beyond the
operator's designation. Flashing remains prohibited globally.

**Q34 - Pack 1 kill gate:** The operator accepted a ten-working-day review
checkpoint after truth restoration. The checkpoint must establish an
applicability boundary, realistic prerequisite acquisition, a compatible RF
transport, a stock-target effect path, and concrete falsifiers. If the path has
no plausible end-to-end wireless route, mark it `LIMITED` or `BLOCKED`, preserve
components, and move to the next target.

**Q35 - Prerequisite modes:** The operator accepted the three prerequisite
modes. Only wireless acquisition or realistic derivation supports a general
field-ready claim.

### Round 8: no-flash and destructive-action control

**Q36 - Flashing:** The operator strengthened the existing device-specific
rule: **never flash any target**. Targets will be provisioned in advance with
the intended stock firmware/software. Attacks must act against that state.
No update, replacement, recovery flash, modified test build, or firmware write
is an attack-development step.

**Q37 - Destructive-action confirmation:** The operator agreed that a pack must
mark destructive/potentially irreversible variants and require exact target
selection plus affirmative sacrificial-target confirmation.

### Round 9: repository scope, UI deployment, execution, and evidence

**Q38 - Repository mission:** The operator confirmed that wireless is the
defining scope. If wireless delivery cannot reach a desired effect on a target,
retain useful local primitives but move to another target. Every active pack
must begin wirelessly.

**Q39 - Web deployment:** The operator directed the dashboard to remain
loopback-only on port `10101`. They do not want additional remote-deployment
architecture prioritized now.

**Q40 - Execution isolation:** The operator accepted live execution outside the
Flask process through a bounded worker with structured input/output,
cancellation, timeout, and minimal privileges.

**Q41 - Execution evidence:** The operator accepted an append-only record for
every armed run: capability version, target, prerequisites, bounds, timestamps,
result-state transitions, verification evidence, and stop reason.

**Q42 - Target data:** The operator initially stated that target "secrets" are
not secret and should not dominate the design. They later answered yes to the
refined policy: disposable target fixture values may be tracked intentionally,
while actual credentials/tokens and unnecessarily large/private raw captures
remain excluded.

### Round 10: fixture data and first ELRS acceptance hypothesis

**Q43 - Fixture values:** The operator accepted tracking target UIDs, phrases,
and identifiers when deliberately designated public CTF fixtures. Git/API
credentials, personal information, and large raw captures remain excluded.

**Q44 - First acceptance proof:** The operator is not attached to the bounded
RC canary. Use it if evidence says it is likely to work; abandon it if it does
not. The objective is a verified wireless effect, not preservation of a favored
method.

**Q45 - Pack 1 operational effect:** The operator stated that one working
effect is enough to move to the next target. Further ELRS effects may be
expanded later in downtime/background work. The source-backed leading effect
is sustained RC override/control displacement, but it is not a mandated final
method.

**Q46 - Field verification:** The operator agreed that USB/read-only telemetry
may verify bench causality, but a field-ready attack cannot depend on a wired
connection to the target.

**Q47 - RF escalation:** The operator delegated the choice. Adopted decision:
validate the RF transport with synthetic identifiers in a cabled, shielded, or
dummy-load setup before active receiver testing.

**Q48 - SDR choice:** The operator preferred the B205mini for mobility unless
the B210 offers a relevant capability advantage. The later factual comparison
found no decisive B210 advantage for this SISO ELRS path. Final recommendation:
B205mini primary implementation/field radio; B210 lab reference/fallback if
measurements expose B205mini underflow/timing limits or if a second coherent RF
chain/GPSDO becomes necessary.

### Round 11: goal orientation, pack completion, background work

**Q49 - Method flexibility:** The operator said to follow the most promising
route, not remain attached to a particular attack. They later retracted the
phrase "path of least resistance" as a formal scoring rule. Do not encode a
six-factor score or treat that phrase as policy. The surviving policy is
outcome-oriented hypothesis testing with explicit falsifiers.

**Q50 - Pack completion:** The operator agreed not to delay the second target
to accumulate multiple effects against the first. One field-ready effect makes
Pack 1 v1 complete.

**Q51 - Background work:** The operator accepted one active delivery lane plus
an isolated background-research lane with a separate owner, no active hardware,
and no overlapping files.

**Q52 - SDR roles:** The operator said they will use the B210 if it is materially
more capable/useful, otherwise the B205mini. The factual audit resolved this in
favor of B205mini primary and B210 validation/fallback for the present SISO
ELRS requirement.

**Q53 - Second target:** The operator will provide the second stock wireless
target after Pack 1 finishes. Do not select or purchase one autonomously.

**Q54 - Negative results:** The operator accepted retaining failed approaches
with prerequisites, evidence level, conditions, failure class, and the fact
that would justify reopening them.

### Round 12: artifacts, remotes, validation, and reviewer eligibility

**Q55 - Large artifacts:** The operator agreed to keep captures outside Git.
Commit hashes, provenance, metadata, sanitized fixtures, and retrieval
instructions instead.

**Q56 - Primary remote:** The operator designated **GitHub as primary** and
**GitLab as its literal one-for-one mirror**. This supersedes the earlier
intermediate recommendation to keep GitLab primary.

**Q57 - Validation authority:** The operator delegated the choice. Adopted
policy: the reproducible local validation suite is the release gate while
hosted CI is administratively unavailable. Hosted CI must be reported as
`NOT RUN`, never as passed or failed, when no job executes.

**Q58 - Candidate scoring:** The operator explicitly withdrew the phrase
"path of least resistance" and asked that it not become a formal scoring
system. Do not add the proposed six-factor score to the roadmap/spec.

**Q59 - Reviewer eligibility:** The operator established:

- Nobody may review their own work.
- ANDEDS may write but may not review.
- Claude may write and may review ANDEDS or Codex.
- Codex may write and may review ANDEDS or Claude.

### Round 13: review gate, ownership, and execution order

**Q60 - Review gate:** The operator agreed that a roadmap deliverable requires
at least one eligible independent review before it may be marked `DONE`.

**Q61 - Ownership:** The operator rejected permanent role pigeonholing. ANDEDS
is agentic but lacks Internet access. Assign each task dynamically to whichever
agent has the access, tools, context, and token budget. The review eligibility
matrix remains fixed even though implementation ownership is not.

**Q62 - Final roadmap order:** The operator accepted Gate 0 -> truth
restoration -> minimal UI contract -> ELRS Pack 1 -> next target. They are not
committed to the estimated durations; faster completion is welcome.

### Round 14: dynamic ownership and checkpoint meaning

**Q63 - Dynamic assignment:** The operator generally accepted assignment by
current ability and emphasized that token availability will often determine
who gets a task. Internet-dependent work must go to an Internet-capable agent;
hardware work requires an explicit operator/hardware handoff; no one reviews
their own work.

**Q64 - Time boxes:** The operator accepted review checkpoints rather than
deadlines. Finishing early is encouraged. Passing a checkpoint requires an
evidence-based extension decision.

## 5. Final authoritative roadmap sequence

### Gate 0: exact GitHub/GitLab repository migration

**Objective:** Produce identical cleaned repositories, with GitHub as the
single write authority and GitLab as a deterministic mirror.

Required outcomes:

- Coordinate a writer freeze/maintenance window.
- Archive the existing full GitLab history outside active remotes.
- Remove raw capture blobs from all reachable active history.
- Preserve provenance and checksums for external artifacts.
- Produce one cleaned shared history containing the latest intended tree.
- Replace both remote primary branches under the explicitly approved migration
  exception.
- Re-clone every workstation/agent checkout.
- Verify branch tips, commit graphs, refs intended to be mirrored, and tree
  hashes from both services.
- Remove or correct the current dual-push arrangement.
- Configure deterministic GitHub -> GitLab mirroring.
- Restore/re-authenticate GitHub CLI credentials before publishing issues.
- Prohibit subsequent force-push/history rewrite after the one-time migration.

Important current facts:

- GitHub currently rejects the roughly 5.43 GiB reachable history.
- The current `origin` has GitHub fetch/push plus a GitLab push URL.
- GitHub CLI authentication for `oatmealphysics` was invalid during this
  session.
- Do not retry the existing full-history push.
- No migration command has been run in this session.

### NOW: truth-restoration sprint

This is deliberately bounded. It is not a general cleanup.

Required scope:

1. Correct the roadmap's nonexistent/stale active lane.
2. Move from a research catalogue to one `NOW`, two ordered `NEXT` items, and a
   parked/gated backlog.
3. Fix the five defects assigned in `SESSION-NOTES.md` #73/#74:
   - robust MAVLink target parsing for IPv6/no-port cases;
   - remove matching constant redefinitions from the Wi-Fi/MAVLink chain
     without undertaking a six-file global refactor;
   - make ELRS v3/v4 SYNC and RC parameter branching consistent;
   - fix or remove the dead `elrs_chain` `max_power_dbm` field without touching
     the correct bench-controller field;
   - remove the misleading default real binding phrase from the simulation
     tool.
4. Correct the superseded B3 description in the roadmap.
5. Correct RQ23 so wired USB injection is scaffolding/downstream capability,
   not evidence of wireless compromise.
6. Remove the claim that the E8 source is version-matched until installed
   identity proves it.
7. Reconcile R5/R7 status and hosted-CI language with actual evidence.
8. Remove/sanitize obsolete tracked private-UID material while retaining the
   new public-fixture policy.
9. Record the global no-flashing rule.
10. Record the reviewer eligibility and dynamic-assignment policy.
11. Run the complete maintained local suite and report hosted CI as `NOT RUN`
    if it still does not execute.

No UI framework expansion, RF work, serial access, hardware access, or new
research pathway belongs in this sprint.

### NEXT 1: minimal Capability Pack/web-control contract

The UI remains loopback-only at `127.0.0.1:10101`.

Required behavior:

- Repository-owned Capability Pack registry.
- One source of truth for action metadata, target applicability,
  prerequisites, safety/destructive classification, preview, execution, and
  verification.
- Attack logic remains independently testable outside Flask.
- Live execution occurs in a bounded worker process, not in the Flask process.
- Structured input/output, timeout, cancellation, stop reason, and minimal
  privilege.
- Per-target, per-capability, time-limited arming.
- Scope record includes target, interface, action, bounds, expiry, stop
  conditions, explicit confirmation, and evidence destination.
- Destructive actions require exact sacrificial-target designation.
- Shared result-state progression.
- Append-only execution evidence.
- Preview and simulation remain probe-free and safe by default.
- No arbitrary third-party plugin loading in v1.
- Preserve a headless interface for tests and future automation even though the
  web UI is the primary human interface.

### NEXT 2: ELRS Capability Pack 1

**Proving fixture:** owned E8/TinyHawk pair.  
**Candidate applicability:** ELRS 2.4 GHz OTA v3 receiver-side
command/frame injection, narrowed to exact proven source/configuration.  
**Primary field SDR:** B205mini.  
**Lab reference/fallback:** B210.

Required chain:

1. Passive ELRS detection.
2. Version/rate/domain classification.
3. Prerequisite mode classification and UID/binding prerequisite acquisition.
4. FHSS/CRC/nonce derivation under the proven v3 model.
5. Device-neutral UHD B2xx timed SISO transport.
6. Cabled/shielded/dummy-load transport validation with synthetic identifiers.
7. Candidate receiver-acceptance hypothesis.
8. Stock receiver acceptance evidence.
9. At least one useful operational effect.
10. Field verification without target-side USB.
11. UI control, result/evidence output, reliability record, runbook, stop
    conditions, and limitations.

Pack 1 v1 is complete when one effect is `FIELD-READY`. It does not wait for
every possible landing, disarm, denial, crash, reboot, or persistence variant.

Review/kill checkpoint questions:

- Is a realistic wireless prerequisite acquisition path available?
- Does the B205mini meet timed-hop deadlines on a direct suitable USB path?
- Does the same transport behave coherently on the B210 reference setup?
- Is candidate IQ independently decodable before the stock target is involved?
- Did the stock receiver accept the candidate?
- Can a useful effect be verified without target-side wiring in the field?
- Is the measured mission-level reliability adequate under bounded retries?
- If not, which exact fact would reopen the method?

### Next target

The operator will supply it after Pack 1 v1. Do not autonomously purchase or
select the device. It must be a stock wireless target. The second pack should
exercise the agreed contract and reveal which abstractions are genuinely
shared. Only then should the platform be generalized beyond the minimum
contract.

## 6. Read-only factual audit: current UI architecture

The following facts were established from current source:

- There is no generic Capability Pack/action registry or plugin loader.
- `ACTION_DEFS` is a static dictionary.
- Target applicability is duplicated in synthetic targets and the live recon
  action map.
- The browser renders action buttons from target data and immediately POSTs an
  action on a single click.
- Live dispatch is a hard-coded chain `if/elif`.
- Chain 2 uses a separate API family and imports command builders directly.
- Flask handlers perform live UDP, Telnet, MAVLink, ELRS/deep-SDR, and BLE work
  in process.
- The web path bypasses the CLI tools' argparse, `--arm`, logger, and abort
  workflows.
- One global `--live-actions` flag enables all ordinary live action paths.
- Live scanning and hardware detection are distinct and may still occur without
  that flag.
- `DashboardState` contains no machine-readable approved scope/plan fields.
- Ordinary action requests contain only an action name; Chain 2 can add an
  endpoint.
- The bench controller contains the strongest machine-readable bounds/abort
  model, but no executor consumes its armed plan.
- Several web handlers report "executed" after local sends without receiver
  acknowledgement.
- The cross-chain orchestrator uses `ATTEMPTED` more honestly but still treats
  it as pass-like at the top level.
- Browser coverage deliberately tests simulation/dry-run, not live paths.
- The dashboard has CSP/browser hardening but no visible application
  authentication. The operator chose loopback-only `127.0.0.1:10101` rather
  than prioritizing remote access.

The selected existing seam is:

```text
action ID + metadata -> target applicability -> preview/live handler
                     -> structured result/evidence
```

The future `to-spec` test-seam proposal, not yet explicitly approved by the
operator after plugin installation, is:

> Exercise each Capability Pack end-to-end through registry -> authorization
> -> isolated executor -> result/evidence contract. Test the web UI against a
> fake executor; keep RF/hardware acceptance as a separate gated evidence seam.

## 7. Read-only factual audit: ELRS effect feasibility

### Strongest first acceptance hypothesis

The most strongly source-supported, observable, bounded receiver-acceptance
test is one hybrid-RC frame producing a distinctive transient `MSP_RC` channel
value on the grounded, visibly disarmed TinyHawk while:

- throttle remains minimum;
- ARM AUX remains low;
- pitch and yaw remain centered;
- every mode/adjustment AUX remains unchanged.

This is a hypothesis, not an operator-mandated method. The operator explicitly
wants it abandoned if it is not the promising route.

### Effect findings

- **Distinctive RC channel value:** directly source-supported; strongest first
  acceptance proof.
- **Unused AUX readback:** directly supported if the live map proves the AUX is
  unused.
- **Mode change:** source-supported but configuration-dependent and not needed
  for first proof.
- **RXLOSS/failsafe:** indirect and requires sustained/control-loss behavior.
- **Disarm:** source-supported but one RC frame is insufficient; the target is
  already disarmed during safe bench work.
- **Beeper/LED:** conditional and weak because the exact stock live mapping is
  not established.
- **Read-only OTA MSP reply:** not a valid present criterion because live
  telemetry is disabled/no-TLM.
- **Land:** indirect/speculative for the current stock configuration; it uses
  `DROP`, not auto-land.
- **Power off:** unsupported; reset/reboot is not electrical power-off.
- **Temporary denial:** source-plausible through synchronization displacement,
  reboot if reachable, or recognized-traffic behavior, but not
  receiver-validated.
- **Permanent disable:** unsupported in current evidence without prohibited
  firmware/hardware/configuration-damage paths.
- **Motor output:** source-reachable but inappropriate as an acceptance canary.

### Existing relevant tools

- `tools/elrs-v3-bounded-rc-injection.py`: constructs a transmit-disabled,
  fail-closed RC canary.
- `tools/elrs-li-forward.py`: builds v3 4/8-LI symbols and ideal offline
  waveform fixtures without scheduling or transmission.
- `tools/elrs-v3-fresh-sync-clock.py`: derives a transmit-disabled UHD schedule
  and timestamps.
- `tools/uhd-elrs-sync-rx.c`: timestamped receive-only capture.
- `tools/tinyhawk-msp-rc-observe.py`: identity-gated read-only `MSP_RC`
  observation with interlocks.

### Missing technical stages

1. Correct independently calibrated SF7/BW812.5/4-8-LI timed UHD TX.
2. Timed retunes, shared clock semantics, gain bounds, one-frame enforcement,
   and abort behavior.
3. Independent cabled/shielded IQ polarity, spectral-shape, timing, retune, and
   decode validation.
4. Tight trigger/correlation between TX and the `MSP_RC` observer.
5. One acceptance artifact tying candidate hash, SDR settings, UHD timestamp,
   readback, result transition, and stop reason together.
6. Field-visible effect verification not dependent on USB.

The existing TX emulator is not the missing transport. Its SDR waveform is
documented as not decodable by a real SX1280, and its SPI backend does not match
the live SF7/4-8-LI PHY.

### Authorization state

This planning session did not authorize fresh serial access, RF transmission,
receiver-acceptance testing, configuration writes, resets, or destructive live
work. Each remains behind its own explicit execution gate. The roadmap or this
handoff is not authorization.

## 8. Read-only factual audit: B205mini versus B210

Official Ettus/NI/UHD documentation and repository evidence support:

- Both cover 70 MHz to 6 GHz and therefore 2.4 GHz ELRS.
- Both provide up to 56 MHz instantaneous bandwidth in the relevant 1x1 mode,
  far above the 812.5 kHz PHY/expected 4 Msps need.
- B205mini is full-duplex SISO (one TX, one RX).
- B210 is coherent full-duplex 2x2, but its same-direction channels share an
  LO and do not independently park on different ELRS hops.
- Both support B2xx timed FPGA/UHD commands and external timing.
- B205mini is substantially smaller and USB-powered.
- B210's second RF chain and optional GPSDO are not currently required.
- Repository B205 receive use is already proven, although dock/USB scheduling
  drops warn that a direct suitable USB path and buffering must be measured.
- The current shared `tx_stream()` path is untimed and unsuitable for Pack 1.
- The ELRS schedule requires timed UHD commands, not host/USB sleep scheduling.

Final role decision:

1. Implement one device-neutral timed B2xx SISO transport.
2. Validate first on B205mini.
3. Run the same cabled/shielded test on B210.
4. Promote B210 only if B205mini measurements show missed hop deadlines,
   underflows, or a real need for B210-only hardware.
5. Keep both runtime-selectable and record which SDR produced each evidence
   artifact.

## 9. Repository migration and collaboration policy

### Final desired topology

- GitHub is the primary/write repository.
- GitLab is an identical one-for-one mirror.
- Operators should be able to clone/pull the same active history and tree from
  either service.
- Ordinary work pushes through one authoritative path; replication is
  deterministic and one-directional.

### Current mismatch

- Current GitLab history contains large historical capture objects.
- Current GitHub has a lean divergent history and is stale.
- The merge intended to join lineages makes the full large history reachable,
  so GitHub rejects the push.
- The local `origin` presently has a GitLab push URL in addition to GitHub,
  creating dual-push behavior.
- `gh auth status` reports the active GitHub token invalid.

### Required migration review

The migration is destructive to active remote history and therefore requires:

- exact target/ref inventory;
- offline archive/backup verification;
- cleaned-history size and content validation;
- current-tree equality proof;
- explicit maintenance window;
- coordinated force replacement under the one-time exception;
- fresh-clone validation from both remotes;
- an eligible independent reviewer who did not perform the migration.

ANDEDS lacks Internet access and cannot review. Do not permanently assign the
migration to ANDEDS. Assign it at execution time to whichever eligible agent
has remote access, authentication, tooling, context, and token capacity.

## 10. Review, ownership, and background-work policy

### Review matrix

| Writer | Eligible reviewer(s) |
|---|---|
| ANDEDS | Claude or Codex |
| Claude | Codex |
| Codex | Claude |

ANDEDS is never a reviewer. Nobody reviews their own work. At least one
eligible independent review is required before marking a roadmap deliverable
`DONE`.

### Dynamic task assignment

Do not permanently specialize or pigeonhole an agent. Assign each task based
on:

- access (Internet, authenticated remotes, hardware);
- tools and environment;
- fresh context and domain understanding;
- token availability;
- review independence.

Ownership ends at handoff. Hardware work requires an explicit operator/hardware
handoff; never infer it from a free port.

### Background work

One active delivery lane remains the rule. Background research is allowed only
when it has a separate owner, no use of active hardware, and no overlapping
implementation files. It should produce facts/evidence, not silently become a
second writer lane.

## 11. Data, credentials, and artifacts

Final policy after clarification:

- Raw RF captures and large binary evidence live outside active Git history.
- Commit hashes, metadata, provenance, sanitized fixtures, and retrieval
  instructions.
- A target UID, phrase, or identifier may be tracked when deliberately declared
  a public/disposable CTF fixture.
- Actual GitHub/GitLab tokens, PATs, API credentials, personal information, and
  unrelated real credentials remain excluded.
- UI logs and exports should distinguish public fixture values from redacted
  credential classes.
- Do not copy private raw device data into this handoff or later public issues.

## 12. Installed workflow skills and unfinished documentation flow

At the operator's request, the official Matt Pocock skills plugins were
installed from `mattpocock/skills`:

- Codex plugin `mattpocock-skills` version `1.3.2`.
- Claude plugin `mattpocock-skills` version `1.3.1`.
- Verified skills include `grill-with-docs`, `to-spec`, `to-tickets`,
  `grilling`, and `domain-modeling` in both environments.

The agents normally discover the new plugin after restart/new turn. Codex read
the installed instructions directly in this session.

The requested workflow is:

```text
grill-with-docs -> to-spec -> to-tickets
```

Status:

- The grilling interview is complete.
- This handoff preserves every decision.
- No `GLOSSARY.md` or ADR was created yet.
- No spec was published.
- No tickets were drafted/published.
- The issue-tracker setup skill has not been run for this repository.
- GitHub CLI authentication is invalid.
- The agreed repository migration has not occurred.
- The `to-spec` test seam was proposed but the operator moved to this handoff
  request before explicitly approving it.

Proposed test seam awaiting confirmation:

> Exercise each Capability Pack end-to-end through registry, authorization,
> isolated executor, and result/evidence contract. Exercise the web UI against
> a fake executor. Treat RF/hardware receiver acceptance as a separate, gated
> evidence seam.

Do not publish a spec or tickets to the stale pre-migration GitHub repository.
After Gate 0 and GitHub re-authentication:

1. Run `/setup-matt-pocock-skills` and choose GitHub as the tracker, the agreed
   documentation locations, and the `ready-for-agent` label vocabulary.
2. Run `/grill-with-docs` over this handoff to create a pure `GLOSSARY.md` and
   only the ADRs that pass the hard-to-reverse/surprising/real-trade-off test.
3. Ask the operator to approve the proposed test seam.
4. Run `/to-spec` and publish the complete spec with `ready-for-agent`.
5. Run `/to-tickets`, present the vertical-slice breakdown and blocking edges,
   obtain operator approval, then publish one issue per ticket.

Likely ADR candidates (create sparingly, not automatically):

- Wireless entry defines repository/capability scope; local primitives may be
  downstream effects.
- Web UI is a loopback control plane over isolated, independently testable
  Capability Packs rather than the execution implementation itself.
- One-time cleaned shared history with GitHub primary and GitLab exact mirror;
  raw captures live outside Git.
- Global no-flashing constraint for all targets.

## 13. Explicitly rejected or superseded interpretations

Future agents must not resurrect these without a new operator decision:

- **Rejected:** The dashboard, packet tools, passive decoder, or 54 valid frames
  are themselves completed capabilities.
- **Rejected:** R6 remains the active implementation lane.
- **Rejected:** Every RQ row is equally eligible current work.
- **Rejected:** USB -> E8 is wireless compromise of the E8.
- **Rejected:** Related EMAX ELRS 4.1.0 source proves the installed E8 version.
- **Rejected:** One target success justifies a blanket ELRS or SX1280 claim.
- **Rejected:** A 50% result and broad applicability are the same property.
- **Rejected:** Target-side USB may be required for a field-ready effect.
- **Rejected:** A pack must implement multiple effects before moving to the
  second target.
- **Rejected:** B210 is automatically primary because it is generally more
  capable.
- **Rejected:** The phrase "path of least resistance" is a formal candidate
  scoring model.
- **Rejected:** ANDEDS owns a permanent role or may review.
- **Rejected:** GitLab remains the future primary remote.
- **Rejected:** Tree equality with different active histories is sufficient;
  the operator wants literal mirror repositories after migration.
- **Rejected:** Active-active writes to both remotes.
- **Rejected:** Raw captures remain in active Git history.
- **Rejected:** Any target may be flashed for development, recovery, or
  validation.
- **Rejected:** Destructive testing is categorically forbidden. It is allowed
  on an explicitly designated sacrificial target, without flashing.
- **Rejected:** Hosted CI that did not execute may be reported as green or red.
- **Rejected:** Permanent ownership assignments based on agent identity rather
  than current access/context/token capacity.

## 14. Open gates and facts intentionally deferred

These are not forgotten decisions; they are deferred prerequisites:

1. Execute and independently verify Gate 0 repository migration.
2. Restore GitHub authentication.
3. Configure the Matt Pocock workflow for GitHub issue tracking.
4. Approve the proposed `to-spec` test seam.
5. Create glossary/ADRs/spec/tickets after migration.
6. Assign dynamic writers/reviewers for each actual task.
7. Complete truth restoration and local validation.
8. Implement the minimal pack/UI contract.
9. Determine whether a realistic UID/binding prerequisite can be acquired or
   derived in the wireless ELRS chain.
10. Implement and independently validate the timed UHD B2xx transport.
11. Obtain a separate explicit gate for any fresh serial read, RF transmission,
    receiver-acceptance test, reset/configuration change, or destructive run.
12. Measure B205mini timing/underflow behavior and cross-check B210.
13. Choose the first receiver-acceptance hypothesis at execution time based on
    evidence; the RC canary is leading but not mandatory.
14. Establish one field-visible operational effect and its reliability.
15. Receive the second target from the operator.
16. Obtain actual CTF scoring/rules/layout facts when they become available and
    use them to reprioritize effects.

## 15. Recommended immediate next actions

Do these in order unless the operator explicitly changes the sequence:

1. Read this handoff, `AGENTS.md`, current `PROJECT-ROADMAP.md`, and latest
   `SESSION-NOTES.md` fresh.
2. Confirm no other writer is active and coordinate the repository migration
   maintenance window.
3. Inventory all refs/large objects/remotes and produce a reversible migration
   runbook before executing it.
4. Archive and verify the original history.
5. Perform the one-time cleaned-history migration under explicit approval,
   with independent review.
6. Re-clone from both GitHub and GitLab and prove identical active histories
   and trees.
7. Re-authenticate GitHub CLI and run `/setup-matt-pocock-skills`.
8. Complete `grill-with-docs` artifacts from this handoff.
9. Obtain test-seam approval, then publish `/to-spec`.
10. Draft `/to-tickets`, obtain operator approval of granularity/edges, and
    publish the tickets.
11. Begin the truth-restoration sprint, not Pack 1 implementation.

## 16. Suggested skills for the next agent

Call the following installed skills as appropriate:

- **`grill-with-docs`**: Convert the settled vocabulary/architectural
  decisions into `GLOSSARY.md` and sparing ADRs after the migration.
- **`domain-modeling`**: Maintain the Capability Pack vocabulary and challenge
  any later term drift.
- **`to-spec`**: Synthesize and publish the implementation specification after
  the test seam is approved and GitHub is ready.
- **`to-tickets`**: Produce dependency-ordered tracer-bullet tickets after the
  spec exists; obtain operator approval before publishing.
- **`gh-cli`**: Use authenticated GitHub CLI workflows once credentials are
  restored.
- **`codex-coordinator`**: Use if multiple agents will work concurrently after
  ticket publication; preserve explicit file/ownership boundaries.
- **`post-patch-validation`**: Validate the five truth-restoration fixes against
  baseline behavior if the security-patch workflow fits.
- **`fp-check`**: Use for any suspected live/security finding before promoting
  it beyond candidate status.
- **`reverse-engineer-anything`**: Use only for later offline firmware analysis
  when the roadmap activates it; never as a reason to flash a target.

## 17. Handoff completion statement

The grilling frontier is empty for the strategic roadmap. The operator has
confirmed the mission, product, scope, evidence model, remote topology,
no-flashing rule, destructive-target policy, UI architecture, review matrix,
first target family, SDR roles, completion rule, time-box semantics, and
execution order.

Remaining items are implementation gates, external facts, or later target
inputs—not silently unresolved design branches. A future agent should not
restart the strategic interview. Continue from Gate 0, preserve the exact
decisions above, and ask only when a new fact forces a real branch.
