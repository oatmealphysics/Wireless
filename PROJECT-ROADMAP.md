# Project Roadmap

Last reviewed: 2026-11-04

This file is the sole source of truth for current project priorities, status,
ownership, acceptance criteria, and next-task selection. The capability-program
decisions were established by the completed roadmap grilling preserved in
`handoffs/2026-11-04-roadmap-grilling-handoff.md`.

`SESSION-NOTES.md` remains the canonical append-only evidence and handoff log;
it does not activate or reprioritize work. `codex-tasks.html`,
`session-notes-2026-10-25.md`, `attack-research-plan.md`,
`HOME-LAB-HANDOFF.md`, and topic-specific plans are historical context or
execution specifications. If any of them conflict with this roadmap, this file
wins.

## Operating rules

- **NOW** is the one active delivery outcome. Finish, explicitly block, or
  falsify it before promoting another outcome.
- **NEXT** contains at most two ordered outcomes. A planned assignment becomes
  active only when this roadmap promotes it to `NOW`.
- **PARKED** preserves useful work without competing with the active lane.
- **GATED** requires a new, explicit operator decision before the gated action.
- **DONE** requires the stated acceptance evidence and an eligible independent
  review; a primary never accepts their own work.
- Each active slice names one primary, one reviewer, exact dependencies, a safe
  scope, and executable completion criteria.
- Time boxes are review checkpoints, not delivery promises. Passing a checkpoint
  requires an evidence-based decision to continue rather than a silent slip.
- Ownership is dynamic at each handoff. It follows access, environment, current
  context, token availability, file boundaries, and review independence rather
  than permanent agent specialization.

Review eligibility is fixed even when implementation ownership changes:

| Writer | Eligible reviewer(s) |
| --- | --- |
| ANDEDS | Claude or Codex |
| Claude | Codex |
| Codex | Claude |

ANDEDS may write but never reviews. One active writer owns each implementation
slice. Another agent may perform a read-only audit in parallel when it uses no
active hardware and edits no overlapping files.

## Scope and evidence boundary

The reusable product is a **Capability Pack**: a target-scoped, human-operable
wireless chain that begins with Wireless Entry, produces and verifies a useful
effect against a Stock Target, retains evidence, and declares applicability,
prerequisites, reliability, stop behavior, and falsifiers. Tools, decoders,
dashboards, simulations, and research are components rather than completed
capabilities.

Default active work is offline application engineering: sanitized fixtures,
deterministic simulation, local files, and loopback-only tests. Hardware access,
serial access, RF transmission, state-changing network/device work, and
destructive execution each require their own explicit gate. No target may be
flashed, erased, restored, recovered, or firmware-updated under any gate.

Private identifiers, credentials, and raw captures stay under ignored `work/`
or a verified external archive. Track only sanitized fixtures, conclusions,
hashes, provenance, and retrieval instructions.

Capability evidence advances through:

```text
CANDIDATE -> OFFLINE-VALIDATED -> PASSIVE-VALIDATED
          -> BENCH-VALIDATED -> FIELD-READY
```

Only `BENCH-VALIDATED` and `FIELD-READY` count as completed offensive
capability. Execution reports a separate Result State:

```text
PREVIEWED -> ATTEMPTED -> TRANSMITTED
          -> TARGET_ACCEPTED -> EFFECT_CONFIRMED
```

Sending bytes never proves target acceptance or effect. Legacy evidence terms
map as follows:

- `SOURCE-DERIVED` is an evidence-basis tag, not a maturity level.
- `SYNTHETIC/OFFLINE-TESTED` maps to `OFFLINE-VALIDATED`.
- `PASSIVE-CAPTURE-VALIDATED` maps to `PASSIVE-VALIDATED`.
- `RECEIVER-ACCEPTED` maps to Result State `TARGET_ACCEPTED`; a bounded expected
  effect is still required for `BENCH-VALIDATED`.

The current 54-frame passive ELRS result is `PASSIVE-VALIDATED`. It does not
establish receiver acceptance, transmitter identity, coherent multi-channel
FHSS agreement, or operational takeover.

## Active capability-program plan

### NOW — G0: exact GitHub/GitLab repository migration

**Status:** IN PROGRESS — locally prepared, remote replacement not executed

**Primary:** Codex

**Independent reviewer:** Claude

**ANDEDS role:** writer freeze; after acceptance, re-clone GitLab and reproduce
the announced ref/tree checks as an operator, not as the reviewer

**Evidence target:** identical cleaned repositories and fresh-clone proof;
outside the Capability Pack maturity ladder

**Objective:** create one cleaned active history with GitHub as the sole write
authority and GitLab as its deterministic one-directional mirror.

Current facts:

- GitLab remains the temporary write authority and includes ANDEDS verification
  entry #83; GitHub `main` was last verified divergent at `40044e1`.
- GitHub CLI authentication has been restored.
- A complete 94-ref bundle plus LFS/capture archive was verified locally.
- A 252 MiB cleaned candidate was validated, but it predates `b64c9cf`,
  `b105d33`, `b9ff37b`, and this roadmap update. It is now a rehearsal artifact
  and must not be pushed as the final history.
- The proposed remote push was interrupted before either remote changed.

Execution steps:

1. Codex records the exact current refs, archive hashes, clean rules, intended
   surviving refs, rollback procedure, and fresh-clone verification commands in
   a reversible migration runbook.
2. Codex rebuilds the candidate from the writer-frozen current GitLab tip,
   removing raw capture history and restoring only approved metadata.
3. Claude independently verifies the archive, clean rules, current-tree
   preservation, object-size result, runbook, and rollback path without writing
   the candidate.
4. The operator approves the reviewed maintenance-window command set.
5. Codex performs the one-time remote replacement and configures the
   one-directional mirror.
6. Claude verifies fresh clones from GitHub and GitLab. ANDEDS then discards the
   old checkout and re-clones the announced GitLab mirror.
7. Codex publishes the GitHub issue-tracker setup, canonical label vocabulary,
   `GLOSSARY.md`, and qualifying ADRs on the cleaned history.

Completion criteria:

- the original history and external artifacts have verified recoverable
  archives;
- no raw capture blob is reachable from any intended active ref;
- GitHub and GitLab expose the same intended refs, commit graph, tip, and tree;
- fresh clones from both remotes pass `git fsck` and the maintained local gates;
- ordinary pushes target GitHub only and deterministic replication updates
  GitLab;
- GitHub Issues contains the canonical triage labels, including
  `ready-for-agent`;
- the domain glossary/ADRs and tracker-consumer instructions are present; and
- the one-time rewrite exception is closed, with later force-pushes prohibited.

### NEXT 1 — TR: bounded truth restoration

**Status:** BLOCKED on G0

**Evidence target:** `OFFLINE-VALIDATED`

**Review checkpoint:** two working days after activation; extension requires
recorded evidence that continuing is worth more than switching paths

This outcome is deliberately limited to five code defects and truth-in-evidence
corrections. It contains two serialized writer slices:

#### TR-A — code and focused regressions

**Planned primary:** ANDEDS

**Planned reviewer:** Codex

**Dependency:** G0 accepted and ANDEDS working from a fresh mirror clone

ANDEDS implements exactly the defect boundaries in `SESSION-NOTES.md` entries
#73 and #74:

1. robust MAVLink target parsing for IPv6 and omitted ports;
2. matching Wi-Fi/MAVLink constants imported from the existing MAVLink chain,
   with the broader six-file duplication left as tracked follow-up;
3. consistent ELRS v3/v4 SYNC and RC parameter derivation using the single
   `elrs_core`;
4. fix or removal of the unread `elrs_chain` `max_power_dbm` field without
   changing the correct bench-controller field; and
5. an explicit-safe simulation binding-phrase input instead of a real-looking
   default.

**TR-A complete when:** every defect has a failing regression before its fix,
focused tests pass, ANDEDS pushes one coherent handoff, and Codex independently
finds no scope, safety, or regression blocker.

#### TR-B — roadmap and evidence correction

**Planned primary:** Claude

**Planned reviewer:** Codex

**Dependency:** TR-A accepted

Claude reconciles maintained documentation with repository evidence: remove
stale active-lane language; correct the B3 description; describe RQ23 direct USB
entry as scaffolding rather than wireless compromise; remove unsupported E8
version-match language; report hosted CI as `NOT RUN` when it did not execute;
apply the public-fixture/private-identifier policy; and preserve the no-flashing
and reviewer rules.

**TR-B complete when:** the validator and full maintained offline suites pass,
every corrected claim cites repository evidence, hosted CI status is literal,
and Codex independently accepts the documentation diff.

**TR outcome complete when:** TR-A and TR-B are accepted, one truthful `NOW` and
at most two ordered `NEXT` outcomes remain, and no hardware, RF, serial, UI
framework expansion, or general cleanup entered the sprint.

### NEXT 2 — CP1: minimal Capability Pack/web-control contract

**Status:** BLOCKED on TR

**Evidence target:** `OFFLINE-VALIDATED`

The approved test seam is:

```text
registry -> applicability -> scoped authorization -> isolated executor
         -> structured Result State and evidence
```

The web UI remains a loopback-only human control plane and is tested against a
fake executor. RF/hardware acceptance remains a separate gated evidence seam.
Implementation is serialized into three verticals:

1. **CP1-A — contract and registry:** Claude primary; Codex reviewer. Define the
   repository-owned pack/action metadata, applicability, prerequisites,
   authorization scope, structured request/result, stop, and evidence contract.
2. **CP1-B — headless fake executor:** ANDEDS primary; Codex reviewer. Exercise
   the accepted contract end-to-end with deterministic fixtures, cancellation,
   timeout, and truthful Result States, without Flask or hardware.
3. **CP1-C — UI and isolated worker adapter:** Codex primary; Claude reviewer.
   Replace hard-coded in-process dispatch with the accepted registry/executor
   seam while preserving probe-free preview/simulation and loopback behavior.

Each vertical begins only after the previous writer's commit and independent
review. The next writer pulls that accepted tip before editing.

Completion criteria:

- one simulated Capability Pack traverses the full approved seam;
- applicability and action metadata have one source of truth;
- authorization is target/action-specific, bounded, expiring, and records stop
  conditions plus the evidence destination;
- live execution is isolated from Flask with structured input/output, timeout,
  cancellation, stop reason, and minimal privilege;
- destructive actions require exact Sacrificial Target designation;
- the UI never turns `ATTEMPTED` or `TRANSMITTED` into acceptance or effect;
- preview and simulation perform no hardware or external-network probe; and
- no arbitrary third-party plugin loading is introduced in v1.

### PARKED / GATED — ELRS Capability Pack 1

Promotion requires CP1 acceptance and a fresh roadmap assignment. The owned
E8/TinyHawk pair is a Proving Fixture, not a presumed competition target. The
candidate boundary is ELRS 2.4 GHz OTA v3 receiver-side injection; B205mini is
the primary portable SDR and B210 is the measured lab reference/fallback.

The first Pack 1 checkpoint occurs ten working days after activation. It must
answer whether realistic prerequisite acquisition, timed B2xx transport,
independently decodable IQ, Stock Target acceptance, a field-visible useful
effect, and adequate bounded-retry reliability form a plausible end-to-end
wireless route. If not, mark the path `LIMITED` or `BLOCKED`, preserve reusable
components, and move to the operator-supplied next Stock Target.

Initial assignment at promotion is chosen dynamically. Expected verticals are
Codex-led timed transport/ELRS integration with Claude review, an isolated
ANDEDS offline-fixture writer slice with Claude or Codex review, and a
Claude-led UI/evidence integration slice with Codex review. No live RF or Stock
Target step is activated by this roadmap text.

### Shared-checkout handoff protocol

1. The active primary pulls the canonical branch, confirms a clean tree, and
   records the exact slice and file boundary before editing.
2. Other agents remain read-only on that boundary. Parallel fact gathering may
   not touch the primary's files or active hardware.
3. The primary stages explicit paths only, runs focused and required full gates,
   commits a coherent slice, pushes it, and appends `SESSION-NOTES.md` evidence.
4. The independent reviewer pulls that commit and reviews without editing the
   primary's surface. Findings return to the primary; acceptance promotes the
   next serialized slice.
5. Ownership ends at handoff. Hardware access always requires a separate
   operator handoff and is never inferred from device availability.

## Historical completed cycle

R0 through R7 below are accepted historical foundations. Their former `NOW`,
`NEXT`, ownership, and CI wording do not select current work; the active plan
above does.

### R0 — Make simulation mode genuinely hardware-free

**Owner:** Codex implementation; Claude review
**Status:** DONE
**Evidence target:** SYNTHETIC/OFFLINE-TESTED

The dashboard advertises `--simulate` as needing no hardware, but page startup
currently calls `/api/hardware`, which invokes subprocesses, BLE/SDR discovery,
and UDP bind probes.

Acceptance criteria:

- `GET /api/hardware` in simulation returns deterministic `not_checked` data.
- `detect_hardware()` is never called in simulation, including browser startup
  and manual refresh.
- The widget says `Simulation / not checked`; it does not claim hardware is
  connected or unavailable.
- Live mode retains explicit hardware refresh behavior.
- Unit and browser tests fail if simulation reaches a real probe, non-loopback
  request, live scan, or live action.

Evidence: the route tests exercise both simulation and live delegation; the
loopback Playwright suite loads and manually refreshes the widget with
`detect_hardware()` replaced by a fail-on-call guard. The widget renders a
yellow `Not checked` state in simulation, and MAVLink live status now describes
UDP listener readiness rather than a connected peer.

### R1 — Isolate dashboard state behind an application/state factory

**Owner:** Codex implementation; three independent Codex read-only reviews;
Claude retrospective review deferred
**Depends on:** R0
**Status:** DONE — accepted by owner while Claude is unavailable
**Evidence target:** SYNTHETIC/OFFLINE-TESTED

Move targets, scan state, scanner status, events, action history, hardware
status, and Chain 2 state out of module globals. Preserve the current CLI and
routes. Two independently created app instances must not share mutable state.

Acceptance criteria:

- A documented state object owns every mutable dashboard collection, flag,
  counter, default, and lock.
- An application/state factory can create two independent test instances.
- Routes and scan workers resolve the state associated with their application;
  they do not fall back to shared module collections.
- The command-line entry point and existing import-based compatibility remain
  functional during migration.
- New tests mutate both instances concurrently and prove no target, event,
  action, scan, hardware, or Chain 2 state crosses the boundary.

Codex handoff evidence (2026-10-28): `DashboardState` now owns all runtime
collections, flags, counters, defaults, and locks; `create_app()` stores a fresh
state in each Flask application's extensions; request routes resolve
`current_app`, workers receive the originating state explicitly, and SSE closes
over that state. Eight factory regressions exercise parallel instance mutation,
worker admission/configuration, nested simulated targets, hardware/mode caches,
Chain 2 history, events, and import compatibility. The 38-route API suite,
52-test maintained dashboard suite, 11-test loopback browser suite, 648-test
full tools suite (23 optional SITL skips), and project validator pass locally.
The CI web matrix now covers Python 3.10/3.12 on Ubuntu and Windows. Three
independent read-only Codex reviews found no remaining architecture, race, or
test-evidence blocker. On 2026-10-28 the owner directed work to continue while
Claude is unavailable, so R1 is accepted as DONE and Claude's review is retained
as a later retrospective audit rather than a dependency for R2.

### R2 — Lock and bound every mutable history

**Owner:** Codex implementation; independent Codex adversarial reviews now;
Claude retrospective concurrency review deferred
**Depends on:** R1
**Status:** DONE
**Evidence target:** SYNTHETIC/OFFLINE-TESTED

Use one documented ownership/locking model. Bound events, action history,
Chain 2 injections, and any auxiliary integration histories owned by
`DashboardState`. Add concurrency and rollover tests. Standalone tool log
buffers remain separate components rather than sharing the web runtime lock.

Acceptance criteria:

- One per-application re-entrant lock guards every mutable dashboard field and
  nested object; writers finish mutations under it and readers detach snapshots
  under it before serialization or blocking work.
- Event, action, and Chain 2 injection histories have explicit retained-entry
  limits, and every production write goes through a bounded helper.
- Concurrent rollover preserves monotonic event IDs, exact newest-tail
  retention, and `last_injection == injections[-1]` whenever injections exist.
- Route-level tests cover ordinary actions, oversized Chain 2 batches, mixed
  parallel writers/readers, SSE replay after pruning, and snapshots detached
  before serialization. Deterministic guarded collections cover history and
  reset lock participation on the Ubuntu/Windows Python 3.10/3.12 matrix.

Evidence (2026-10-28): one per-application `RLock` now guards every mutable
dashboard field; the legacy scan/event lock names are aliases to that lock.
Events retain 500 entries, while action and Chain 2 injection histories retain
200 each through centralized helpers. Nine new tests cover exact concurrent
event tails, SSE replay after pruning, ordinary-action rollover, oversized and
empty Chain 2 batches, mixed concurrent writers/readers, guarded mutation and
reset paths, nested caller-data detachment, and route snapshots detached before
serialization. The review-discovered empty-batch invariant was fixed so a
retained injection can never coexist with a cleared `last_injection`. Both
independent read-only reviews report no remaining blocker; 107 maintained
backend/state tests, 11 loopback browser tests, 648 tool tests (23 optional SITL
skips), Python 3.10 grammar, and the 254-file validator pass locally.

### R3 — Add cross-instance and lifecycle regression coverage

**Owner:** Codex temporary implementation and independent sub-agent review;
Claude retrospective review deferred
**Depends on:** R1 and R2
**Status:** DONE — accepted on repository evidence and three independent reviews
**Evidence target:** SYNTHETIC/OFFLINE-TESTED

Prove that targets, logs, events, scans, and Chain 2 state cannot leak between
app instances or tests. Cover partial startup failure and cleanup.

Acceptance criteria:

- Every API-owned and direct compatibility scan has one tracked generation and
  worker owner; reset refuses active work, stale generations cannot mutate or
  release newer work, and all success/failure paths release their slot.
- Partial application registration, thread construction/start, worker
  bootstrap, malformed direct input, socket setup/bind, and SDR read failures
  leave the originating instance retryable without changing a peer instance.
- Runtime reset clears every mutable dashboard collection/status while
  preserving configuration and a monotonic event cursor for already-open SSE
  streams.
- Route fixtures use fresh application state. Background web scans do not
  install process signal handlers or accumulate logger handlers.

Evidence (2026-10-29): scan generations and worker identities are reserved
under the per-application lock and finalized by a route-owned wrapper. Direct
simulated/live entry points claim the same lifecycle. Quiescent reset rejects
active workers; event IDs remain monotonic across resets; application claims
roll back after partial factory failure; socket/SDR cleanup is exception-safe;
and web background abort handling avoids main-thread-only signals. Nineteen new
lifecycle regressions cover direct/API overlap, stale generations, partial
startup, cleanup/retry, fresh fixtures, concurrent logger creation, resource
failures, and independent SSE streams. The 127 maintained dashboard tests,
648 tool tests (23 optional skips), Python 3.10 grammar check, and 255-file
validator pass locally. Three independent final read-only reviews report no
remaining lifecycle, cleanup, CI, Windows, or evidence blocker. Browser E2E
remains an explicit Ubuntu/Windows CI gate and was not rerun locally in this
pass because the disposable WSL environment has no Playwright browser.

### R4 — Refresh maintained documentation

**Owner:** Codex implementation; three independent Codex read-only reviews;
Claude retrospective review available after the pushed handoff
**Depends on:** R0
**Status:** DONE
**Evidence target:** SOURCE-DERIVED plus SYNTHETIC/OFFLINE-TESTED commands

Bring `web/README.md` and `tools/README.md` in line with actual behavior. Add
the passive pipeline and decoder, fix test paths, distinguish active reads from
passive observation, and remove operational claims not supported by evidence.

Acceptance criteria:

- Documented setup, launch, validation, and focused-test commands match the
  maintained files and execute from the stated working directory.
- Passive capture/decoder components and evidence labels match tracked code and
  corpus results; source agreement and CRC validity are not described as
  receiver acceptance or operational capability.
- Hardware, serial, socket, RF, and network-active behavior is clearly
  separated from offline parsing, simulation, and loopback-only tests.
- Local links and named files pass the project validator; an independent review
  finds no stale path, mode, or evidence claim in the maintained READMEs.

Evidence (2026-10-29): the root, web, and tools guides now match the current
CLI modes, scanner sequencing, local side effects, action behavior, lifecycle,
CI matrix, and cross-platform interpreter convention. The maintained passive
path documents the 62-candidate/54-CRC-valid corpus result and its evidence
limits. The capture consistency checker now uses neutral CRC/observation JSON
keys, labels its output pairing-sensitive, rejects non-six-byte UID files, and
has an 11-test focused regression file. The exact passive command reproduced
54 byte matches, zero mismatches, and eight unresolved candidates. The
255-file validator, 127 dashboard tests, and 649 tool tests pass locally (23
optional integration skips). Three independent final reviews found no stale
path, mode, side-effect, privacy, or evidence blocker. Browser E2E remains the
four-cell CI gate and was not rerun locally because the WSL environment lacks
Playwright/Chrome.

### R5 — Extract dashboard assets and add a strict CSP

**Owner:** Codex implementation and local Chromium verification; Claude
retrospective browser review
**Depends on:** R1–R3
**Status:** DONE — hosted CI execution is administratively blocked, not failed
**Evidence target:** SYNTHETIC/OFFLINE-TESTED

Move inline CSS and JavaScript to versioned static files, remove inline event
and style dependencies, then enforce a self-only Content Security Policy. Keep
the full loopback browser suite green.

Acceptance criteria:

- The rendered template contains no inline script blocks, style blocks,
  `style=` attributes, or inline event handlers; behavior and layout come from
  same-origin versioned static assets.
- Dashboard responses carry an explicit CSP with no `unsafe-inline`,
  `unsafe-eval`, external script/style origin, or blanket wildcard.
- Route tests verify asset loading, cache/version references, and the exact CSP;
  the loopback browser suite covers the complete scan/action/log/clear flow and
  390 px layout under that policy.
- The validator, maintained dashboard suites, and full tool suite remain green
  locally; the four-cell Ubuntu/Windows Python 3.10/3.12 workflow remains the
  hosted gate when the account can start jobs.

Evidence (2026-10-29): `index.html` now references versioned same-origin CSS and
JavaScript, with no app-authored inline script/style blocks, style attributes,
event handlers, or JavaScript style mutation. HTML, API, and static responses
carry the exact strict CSP plus companion security headers; static assets have
a one-year cache policy. The 43-route API suite, 53 frontend contract tests,
8 factory tests, 19 lifecycle tests, 10 history tests, 650-tool full suite
(23 optional skips), and 257-file validator pass locally. A loopback Chromium
run loaded both assets with no console or CSP violations, completed the
scan/open/dry-run-action/clear flow, and proved the 390 px viewport plus
internally scrollable detail panel.

GitHub Actions run `40950236841` did not execute a single workflow step. All
eight check runs were rejected before startup with the account-level annotation
that recent payments failed or the spending limit must be increased. Those red
checks are an external billing/plan condition, not test failures; no roadmap
item may describe that run as green until GitHub actually executes it.

The companion research pass is recorded in
[`research/defensive-attack-surface-expansion-2026-10-29.md`](research/defensive-attack-surface-expansion-2026-10-29.md).
It adds evidence-gated pathways without activating any research implementation.

### R6 — Finish selected UX reliability gaps

**Owner:** Claude implementation after the R5 handoff; Codex browser/integration review
**Depends on:** R5 for theme work
**Status:** DONE

In order:

1. Hydrate the log viewer from `/api/log`, add Copy, resizing, and persisted
   height.
2. Add a persistent connection-state banner and `Retry now` control while
   retaining native `EventSource` reconnect and `Last-Event-ID` recovery. Do
   not add a competing manual reconnect loop without a failing test.
3. Add a dark/light theme after asset extraction.

Evidence (2026-11-01): Static assets bumped from v1 to v2. `dashboard.v2.js`
hydrates the log viewer from `/api/log` on init, adds a Copy button that writes
all log entries to clipboard, adds a drag-to-resize handle on the log viewer
body with height persisted in `localStorage`, shows a persistent SSE
connection-state banner with Retry button on disconnect (hidden on reconnect),
and adds a dark/light theme toggle persisting preference in `localStorage` with
`data-theme` attribute on `<html>`. `dashboard.v2.css` provides light-mode
defaults with dark mode via `prefers-color-scheme` media query (guarded by
`:root:not([data-theme="light"])`) and explicit `data-theme="dark"` selector.
`index.html` updated with SSE banner element, theme toggle button, resize
handle, and Copy button — no inline handlers or style attributes. All 43 API
route tests, 53 frontend contract tests, and project validator pass locally.

### R7 — Incremental quality gates

**Owner:** Claude implementation
**Depends on:** current Ubuntu/Windows Python 3.10/3.12 matrix green
**Status:** DONE

- Add Python 3.13 and fix compatibility issues; do not skip failures merely to
  turn CI green.
- Introduce pinned Ruff and mypy checks only for maintained, already-clean
  modules, then ratchet scope.
- Exclude vendored/generated/archive material. Do not blanket-format the
  repository or install optional hardware stacks in offline validator jobs.

Evidence (2026-11-01): Ruff and mypy quality gates added. `ruff.toml` added
with E402 per-file ignores (sys.path manipulation) and E501 ignored repo-wide.
Unused imports removed from `web/app.py` (os),
`tools/exploit_chains/common.py` (time), `web/tests/test_api_routes.py` (time),
and `web/app.py` (parse_mavlink_packet duplicate import). A `typecheck` job
runs pinned mypy 1.14.1 on `tools/exploit_chains/common.py` with
`--follow-imports=skip`. `web/app.py` mypy deferred due to an internal mypy
crash on complex Flask app serialization; to be ratcheted when the mypy
upstream fix lands. All local checks pass.

Correction (2026-11-01, andeds review): the R7 evidence above overstated the
CI matrix. Python 3.13 was added to the matrix in `e0a8ea2` (#65) but commit
`5849922` (#68) then reduced the hosted matrix back to Python 3.10/3.12 on
Ubuntu-only to fit the free-tier Actions quota, and the Windows cells were
dropped. The live workflow therefore runs a 2-cell matrix, not the 6-cell
Ubuntu/Windows x 3.10/3.12/3.13 the evidence text described. The Ruff 0.8.6
`lint` job and a full-tree Ruff 0.11.12 `lint-full` job both exist; the two
originally shared the `lint:` key, so the first was silently dropped by YAML
de-duplication — `lint-full` was renamed to restore it. Python 3.13 coverage
is not currently exercised in hosted CI; restoring it requires an owner
decision on Actions-minute budget.

## PARKED / GATED research pathway queue

Every RQ item below remains **GATED** with no active owner. The legacy ownership
cells are planning history, not activation. Promotion requires completion or
explicit blocking of the active capability-program outcome, a new operator
decision, and a roadmap edit that names the current primary, eligible reviewer,
evidence target, and falsifier. Merely appearing here does not authorize serial,
RF, hardware, or live-device work.

The queue was derived from the owner-provided one-screen summary, repository
evidence, and two primary-source research passes. The second pass is preserved
in
[`research/defensive-attack-surface-expansion-2026-10-29.md`](research/defensive-attack-surface-expansion-2026-10-29.md).
The linked Claude artifact required sign-in and was not treated as read
evidence.

Every promoted pathway must pin source tags or commits, hash its inputs, state
its privacy treatment, name its evidence class, and define the next promotion
test. Source agreement, parser success, or a valid CRC does not establish
receiver acceptance, RF causality, device identity, or field generality.

### Corrections to imported pathways

- **ESP-NOW replay / CVE-2024-42483:** rename this to an enhanced-component
  applicability and replay-semantics audit. Espressif's advisory applies to the
  higher-level `espressif/esp-now` component through 2.5.1, whether package
  managed or vendored, not every use of the base `esp_now_*` API. No E8 or
  ExpressLRS applicability finding under this CVE is valid until provenance
  proves that affected code and version are present.
- **ESP32 hidden HCI / CVE-2025-27840:** demote this to original-ESP32
  hosted-HCI reachability and patch attestation. Espressif states that the
  commands cannot be triggered over Bluetooth, radio, or the Internet and that
  ESP32-C/S/H parts are unaffected. A USB CDC port is not evidence of UART-HCI,
  and the E8 MCU remains unconfirmed.
- **CRSF injection:** begin with source-pinned stream conformance, differential
  parsing, and Betaflight SITL state tests. Any later owned-device acceptance
  check remains a separately approved, props-off bench stage.
- **STM32F411 voltage glitching:** begin with the F411 errata, source call-site
  mapping, and software fault-model mutation tests. Physical fault injection is
  restricted to a separately approved expendable equivalent board; never the
  no-flash/no-opening TinyHawk.
- **Wideband FHSS correlation:** retain it as a hardware-gated passive path.
  Promotion still requires one calibrated multi-channel timeline, an
  independently supplied complete UID, and enough distinct observations; a
  collection of unrelated single-channel captures is insufficient.

Primary correction sources: [ESP-NOW replay advisory](https://github.com/espressif/esp-now/security/advisories/GHSA-wf6q-c2xr-77xj),
[ESP-NOW component registry](https://components.espressif.com/components/espressif/esp-now/versions/2.5.2),
[Espressif hidden-HCI follow-up](https://www.espressif.com/sites/default/files/advisory_downloads/AR2025-004_Security_Advisory_Follow-Up_Updates_and_Fixes_Regarding_ESP32_Undocumented_Bluetooth_Commands_en.pdf),
and [STM32F411 errata](https://www.st.com/resource/en/errata_sheet/es0287-stm32f411xcxe-device-errata-stmicroelectronics.pdf).

### Ranked software-first pathways

| ID | Pathway | Planned ownership | Depends on | Safe deliverable | Evidence target and promotion gate |
|---|---|---|---|---|---|
| RQ1 | **Capture corpus and timebase standardization** | Codex primary; Claude review | Existing passive decoder and capture provenance | Adopt [SigMF](https://github.com/sigmf/SigMF) metadata for private IQ. Limit the first clock-aware event schema to the current ELRS IQ -> ranked-symbol -> frame pipeline. | `SYNTHETIC/OFFLINE-TESTED`: preserve the original file SHA-256 as provenance; record datatype, endianness, sample rate/count, and conversion; prove sample-value equivalence or a declared numeric tolerance on a sanitized fixture; validate on Windows/Linux; commit no private capture or identifier. |
| RQ2 | **ELRS v3/v4 OTA conformance and passive version classification** | Codex primary; Claude review | RQ1 for tracked-capture classification | Generate tag-pinned known-answer matrices for packet sizes, SYNC fields, CRC, packet-rate, and FHSS semantics from [ELRS 3.3.2 OTA](https://github.com/ExpressLRS/ExpressLRS/blob/3.3.2/src/lib/OTA/OTA.h), [3.3.2 FHSS](https://github.com/ExpressLRS/ExpressLRS/blob/3.3.2/src/lib/FHSS/FHSS.h), [4.0.0 OTA](https://github.com/ExpressLRS/ExpressLRS/blob/4.0.0/src/lib/OTA/OTA.h), and [4.0.0 FHSS](https://github.com/ExpressLRS/ExpressLRS/blob/4.0.0/src/lib/FHSS/FHSS.h); classify tracked IQ with confidence and provenance. | `SYNTHETIC/OFFLINE-TESTED`, then `PASSIVE-CAPTURE-VALIDATED` only after independent implementation agreement. Ambiguous captures stay unknown; no receiver-acceptance or transmitter-identity claim. |
| RQ3 | **CRSF streaming conformance and differential parser testing** | Claude primary; Codex review | Exact ExpressLRS/Betaflight/EdgeTX tags selected at promotion | Compare the commit-pinned [TBS CRSF specification](https://github.com/tbs-fpv/tbs-crsf-spec/blob/a73f7dc5386e5b8744aa4a23b3af5354ee0d4c92/crsf.md) and implementations for length bounds, truncation/noise resynchronization, optional fields, extended routing, CRC, and baud negotiation. Retain minimized offline regression corpora. | `SYNTHETIC/OFFLINE-TESTED`: deterministic sanitizer/property tests pass. A parser defect is not promoted to OTA reachability or aircraft effect. |
| RQ4 | **Betaflight 4.4.2 / MSP API 1.45 read-only contract and SITL oracle** | Claude primary; Codex review | Pinned 4.4.2 source; RQ3 for CRSF cases | Generate a direction- and length-aware schema from the pinned [4.4.2 MSP definitions](https://github.com/betaflight/betaflight/blob/4.4.2/src/main/msp/msp_protocol.h). The probe may encode only allowlisted read/query IDs with their exact required request payloads; it has no setter, CLI, reset, bootloader, passthrough, EEPROM, calibration, or motor-command encoder path. Use [SITL](https://betaflight.com/docs/development/SITL) for negotiation, malformed-frame, RXLOSS, and version-specific failsafe tests. | `SYNTHETIC/OFFLINE-TESTED` first. Any COM5 comparison requires explicit approval, props removed, allowlist enforcement, and no configuration write. |
| RQ5 | **Blackbox/RF/CRSF timeline fusion and health analytics** | Codex primary; Claude review | RQ1, RQ3, RQ4, and existing/synthetic logs | Align passive packet gaps and link statistics with [Blackbox](https://betaflight.com/docs/wiki/guides/current/Black-Box-logging-and-usage) state, RXLOSS/failsafe transitions, DShot telemetry errors, RPM imbalance, and reset markers. | `SYNTHETIC/OFFLINE-TESTED`, then `PASSIVE-CAPTURE-VALIDATED`: report clock offset, drift, dropout, and alignment residuals. Correlation is not called causation; fresh device-log retrieval remains gated. |
| RQ6 | **Firmware provenance, SBOM, feature reachability, and rebuild variance** | Claude primary; Codex review | Candidate source tree or firmware artifact | Produce parse-only manifests for chip/board, framework/Git identity, build options, artifact envelopes, hashes, dependencies, transports, build receipts, and dependency locks. Add Betaflight cloud-build, ArduPilot APJ/manifest, PX4 package, and ESP-IDF lock/SBOM adapters; attribute clean-build differences rather than silently normalizing them. | `SOURCE-DERIVED`: component presence, affected version, feature enablement, and reachability remain separate confirmed/absent/unknown fields. Equality is not source trust and inequality is not compromise. Never flash or sign. This gate controls every ESP CVE lane. |
| RQ7 | **Revision-aware MCU errata and memory-boundary conformance** | Claude primary; Codex review | Pinned Betaflight 4.4.2, exact MCU/revision evidence, and vendor documents | Start with F411 SPI/SX1280, USB OTG, DMA, timer, timeout, reset, and watchdog call sites mapped to [ES0287](https://www.st.com/resource/en/errata_sheet/es0287-stm32f411xcxe-device-errata-stmicroelectronics.pdf); add exact failure mocks. Later F7/H7 work requires its own revision-specific errata plus DMA/cache/MPU buffer ownership checks. | `SOURCE-DERIVED` and `SYNTHETIC/OFFLINE-TESTED`; board/MCU IDs are inventory, not attestation. Do not generalize across silicon families or infer a physical-glitch result. |
| RQ8 | **Software safety fault-model mutation** | Codex primary; Claude review | RQ4 state model | Simulate skipped checks, corrupted branch outcomes, timeout failures, reset causes, BOR policy, watchdog coverage, and actuator-output safe states without modeling a physical injection apparatus. | `SYNTHETIC/OFFLINE-TESTED` only. Results are not labeled silicon errata, device evidence, brownout behavior, or voltage-glitch susceptibility. |
| RQ9 | **OpenDroneID multi-transport/dialect parser corpus** | Claude primary; Codex review | Official sample pcaps and pinned reference source | Cross-check BLE/Wi-Fi transport framing, message fields, units, versions, duplicates, and redaction through the [OpenDroneID C reference](https://github.com/opendroneid/opendroneid-core-c) and [Wireshark dissector](https://github.com/opendroneid/wireshark-dissector). | `SYNTHETIC/OFFLINE-TESTED` against official fixtures. Parsing an Authentication message is not signature or identity trust; redact IDs/coordinates and gate live ambient capture separately. |
| RQ10 | **DRIP authentication and trust-state verifier** | Claude primary; Codex review | RQ9 plus pinned RFC test vectors, keys/trust roots, and registry/DNS snapshots | Verify syntax, signatures, freshness/replay, Manifest/Wrapper binding, and registry-chain state using [RFC 9374](https://www.rfc-editor.org/rfc/rfc9374.html) and [RFC 9575](https://www.rfc-editor.org/rfc/rfc9575.html). Report separate syntactically valid, signature-valid, identity-trusted, unsigned/unverifiable, stale, conflicting, and invalid states. | `SYNTHETIC/OFFLINE-TESTED`: every verdict identifies its test vector, key/trust-root snapshot, and unavailable dependency. Unsigned is not mislabeled spoofed; no live registry query by default. |
| RQ11 | **MAVLink generated-contract and state-machine gap audit** | Codex primary; Claude review | Existing repository MAVLink signing/replay tools; a maintained module scope approved at promotion | Generate message ID/length/field/`CRC_EXTRA` contracts from official XML, then test parameter encoding/cache invariants, packed parameters over FTP, session/retry/path bounds, command ACK/progress/cancel state, route identity/reboot behavior, signed-frame immutability, and bounded component metadata using synthetic fixtures. | `SYNTHETIC/OFFLINE-TESTED`: no real key material, `SETUP_SIGNING` payload, external metadata fetch, or actuator command. A protocol mismatch remains an offline software finding. Lower relevance to the current Betaflight target. |
| RQ12 | **CFO and waveform residuals as capture QA** | Codex primary; Claude review | RQ1 | Extract CFO, SFO/chirp-slope residuals, baseline bins, and decoder confidence from existing private IQ to detect capture drift and calibration problems. | `PASSIVE-CAPTURE-VALIDATED` only as capture QA. A device-fingerprinting claim requires a predeclared, held-out multi-device/multi-session design spanning relevant temperatures, locations, and calibrated receivers; studies show [CFO drift](https://arxiv.org/abs/2101.01668) and strong [channel/configuration/receiver sensitivity](https://ieeexplore.ieee.org/document/9580899). |
| RQ13 | **Conditional ESP patch-differential corpus** | Claude primary; Codex review | RQ6 confirms exact applicability | Reproduce fixed/affected behavior in offline source tests only for proven components and configurations: [CVE-2024-42484](https://github.com/espressif/esp-now/security/advisories/GHSA-q6f6-4qc5-vhx5), native ESP-IDF [CVE-2025-52471](https://github.com/espressif/esp-idf/security/advisories/GHSA-hqhh-cp47-fv5g), BLE provisioning [CVE-2026-25507](https://github.com/espressif/esp-idf/security/advisories/GHSA-h7r3-gmg9-xjmg) / [CVE-2026-25508](https://github.com/espressif/esp-idf/security/advisories/GHSA-9j5x-rf36-54x9), or SoftAP DHCP [CVE-2026-45160](https://github.com/espressif/esp-idf/security/advisories/GHSA-g764-gwc3-75m5). | `SYNTHETIC/OFFLINE-TESTED`: synthetic packets and local processes only; never present these as blanket ESP32 or E8 findings. |
| RQ14 | **DroneCAN-v0, Cyphal-v1, and adjacent topology health** | Claude primary; Codex review | A demonstrated owned-platform or fixture need | Keep [DroneCAN/UAVCAN v0](https://dronecan.github.io/Specification/6._Application_level_functions/) separate from [Cyphal v1](https://specification.opencyphal.org/Cyphal_Specification.pdf). Test versioned DSDL/extent/signatures, port manifests, transfer-ID ordering/deduplication, multi-frame/redundant-interface behavior, heartbeat/offline state, allocation/identity continuity, registers/commands, and file/update-service policy. | `SYNTHETIC/OFFLINE-TESTED`: CRCs, transfer IDs, node IDs, unique IDs, and image checksums are not authentication. Keep adapters dormant; no CAN traffic or external fetch. |
| RQ15 | **Betaflight preset, configuration, and build attestation** | Claude primary; Codex review | Exact firmware/target, artifact/build receipt, pinned preset index, and sanitized starting configuration | Bind cloud-build target/features/log/hash to the artifact; resolve and hash preset include graphs; interpret configuration under the exact version schema; emit a deterministic before/after semantic diff with no command or serial transport. | `SOURCE-DERIVED` and `SYNTHETIC/OFFLINE-TESTED`: reject wrong-version parameters, unknown commands, include cycles, hidden resource/protocol changes, and cross-version restores. Repository/build hashes are not signatures and the result is not a flight-safety claim. |
| RQ16 | **Verified boot, update target binding, and rollback-policy oracle** | Claude primary; Codex review | RQ6 plus exact MCU/board, boot path, image envelope, key identity, signed ranges, version policy, and recovery path | Parse PX4/ArduPilot/ESP/DFU profiles as separate finite-state machines; build a boot/update trust graph; test tampered, wrong-target, old-version, test-key, truncated, partially covered, and interrupted-update fixtures. Include peripheral update trust without equating APDescriptor checksums with signatures. | `SOURCE-DERIVED` and `SYNTHETIC/OFFLINE-TESTED`: report authorization, coverage, target compatibility, downgrade resistance, and recovery separately. No device enumeration, boot transition, flash/sign operation, eFuse/option-byte access, or debug attachment. |
| RQ17 | **DShot/ESC command-state and telemetry conformance** | Codex primary; Claude review | Proven DShot configuration and exact ESC firmware family/version | Pin FC/ESC sources; model checksums, pulse timing, command repetition, stopped-motor/cooldown rules, bidirectional GCR/EDT decoding, error rates, stale state, and telemetry plausibility with pure local frames. | `SOURCE-DERIVED` then `SYNTHETIC/OFFLINE-TESTED`: no GPIO, motor, serial, or RF output. Establishes an intra-aircraft contract, not remote reachability. |
| RQ18 | **PX4 uXRCE-DDS exposure and offboard-control lease oracle** | Claude primary; Codex review | Exact PX4/Agent/ROS versions, `dds_topics.yaml`, matching messages, and loopback SITL | Generate inbound/outbound topic/service manifests; test schema/QoS/namespace/session/fragment/reconnect state; separately test offboard proof-of-life, setpoint freshness, writer collisions, loss timeout, mode precedence, and fallback. | `SYNTHETIC/OFFLINE-TESTED`: PX4 documents DDS as unauthenticated and directly connected to uORB, so isolation is an acceptance criterion. An XRCE client key is not authentication; no physical output. |
| RQ19 | **GNSS receiver, correction-chain, and authentication-state contract** | Claude primary; Codex review | Exact receiver/protocol generation and pinned FC/correction stack | Generate receiver message contracts; test parse/resync/config ACK state, `GPS_RTCM_DATA` fragment/loss/source/freshness behavior, simulated stuck/wrong/off states, and Galileo OSNMA test vectors with navigation plausibility kept separate. | `SYNTHETIC/OFFLINE-TESTED`: checksums are not authentication; OSNMA navigation-data authentication is not range authentication or a safety guarantee. Synthetic/redacted coordinates only; no live GNSS, correction service, or RF. |
| RQ20 | **Diagnostic-artifact privacy and release sanitizer** | Codex primary; Claude review | Existing exported artifacts or synthetic fixtures only | Scan Blackbox, ULog, DataFlash, configs, tlogs, metadata, and core dumps; produce a redacted derivative with original/derivative hashes, transformation manifest, class counts, and explicit residual-unknowns. Track encryption separately from redaction. | `SYNTHETIC/OFFLINE-TESTED`: originals stay private; no new acquisition. A RAM scanner cannot prove secret absence, and an encrypted artifact is not automatically share-safe. |
| RQ21 | **ArduPilot Lua, ROMFS, defaults, and parameter-policy integrity** | Claude primary; Codex review | Exact build with scripting/ROMFS or a private existing script inventory | Hash and classify ROMFS/SD scripts; model load/run checksums; parse embedded defaults, read-only declarations, and parameter-lockdown allowlists; exercise deterministic fake-runtime load/error cases. | `SOURCE-DERIVED` and `SYNTHETIC/OFFLINE-TESTED`: checksums detect change, not author trust; static analysis is not runtime timing or safety evidence. |
| RQ22 | **Conditional peripheral serial and cooperative-traffic adapters** | Claude primary; Codex review | Exact SRXL2, SmartAudio, or ADS-B/DAA implementation is proven | Add decode-only/state-only adapters for SRXL2 handshake/failsafe, SmartAudio version/transaction/table policy, and synthetic cooperative-traffic freshness/collision/eviction/conflict state. | `SYNTHETIC/OFFLINE-TESTED`: no bind, programming, UART, VTX, RF, or live traffic action. These adapters remain dormant and do not apply to the current ELRS link by analogy. |
| RQ23 | **E8 TX command-surface PoC (issue commands to the controller)** | Codex primary; Claude review | `research/e8-tx-command-surface.md` (command-surface map, source-pinned ELRS 4.1.0); `tools/crsf-packet-tool.py`, `tools/elrs_core/` for frame construction; TBS CRSF spec (commit-pinned) for frame validation | Build offline CRSF/MSP frame constructors for the E8 TX command sinks (RC-channel override 0x16, command 0x32, parameter read/write 0x2C/0x2D, MSP_ELRS_FUNC power-cal, MSP_SET_VTX_CONFIG) and a USB-CDC reachability probe design. Validate frames offline against the pinned CRSF spec and `msptypes.h`. **Round 5 (integration against the live owned E8) is a separate gated stage** requiring explicit operator sign-off. | `SYNTHETIC/OFFLINE-TESTED` for rounds 1-4 (frame construction + offline validation). Round 5 is `GATED` on operator sign-off: bench-only, owned grounded E8, no RF TX, no flashing, no TinyHawk interaction. The PoC objective is T2 command injection (RC override, param write), not T1 RCE. The WiFi firmware-upload RCE lane is documented but not wirelessly reachable in the integrated E8 and requires a separate decision to enter WiFi mode. |

If the operator later promotes a research task, select it against the current
Capability Pack outcome and evidence gap rather than the former R6 lane. RQ15
remains the strongest current Betaflight attestation candidate and RQ1 the
passive-capture candidate; neither is active. RQ6 and RQ20 are reusable evidence
gates, while the remaining rows are conditional expansion paths rather than
reasons to delay current work.

#### RQ1 progress (andeds, 2026-11-01) — GATED, no active owner; groundwork only

The SigMF standardization groundwork is in place but the row **remains GATED**
(no owner promotion recorded):

- `tools/elrs-sigmf-standardize.py` writes a SigMF v1.0.0 `.sigmf-meta`
  sidecar for each tracked `captures/*.iq`, recording `core:datatype`
  (`sc16le`), `core:sample_rate` (4 Msps), `core:sample_size` (derived from
  the file size), `core:freq_center` (2441.000 MHz), the capture device, and
  the original file SHA-256 as `provenance:original_sha256`. The sidecar is
  flagged `provenance:private: true`. The Oct 16 capture carries the
  README's documented creation-time anchor, explicitly labelled an estimate of
  RF sample zero (not an instrument-synchronized timestamp).
- `tools/tests/test_elrs_sigmf_standardize.py` (15 tests, synthetic sc16le
  fixture, no private capture) verifies the record invariants, that the
  provenance SHA-256 matches an independent hash, that the IQ file is left
  byte-for-byte untouched, idempotency, force-overwrite, non-sc16le rejection,
  and the `--check` validator accepting a good sidecar and rejecting a
  tampered one.
- Both sidecars are generated and pass `--check`; the two `.iq` files still
  verify against `captures/SHA256SUMS.txt`.

Remaining before RQ1 can be called DONE (and it is not, pending promotion):
(1) wire the sidecar into `elrs-passive-pipeline.py` so its output stops
emitting `sample_rate: 0` / `capture_sha256: ""` and instead reads the
standardized timebase; (2) add the sample-value-equivalence / declared-tolerance
proof on a sanitized fixture; (3) the Windows/Linux validation is not exercised
here (no hosted CI in this environment). This groundwork does not activate the
row, change its evidence target, or authorize any device/RF/serial work.

#### RQ2 progress (andeds, 2026-11-01) — GATED, no active owner; groundwork only

The known-answer conformance matrix groundwork is in place but the row
**remains GATED** (no owner promotion recorded):

- `tools/tests/test_elrs_ota_kat_matrix.py` (19 tests, SYNTHETIC/OFFLINE-TESTED)
  is a systematic KAT matrix over packet type x size (std 8 / full 13) x
  version (v3 / v4): documented packet-size constants, v4 and v3
  build->decode round-trips (SYNC + RCDATA), CRC-rejection-on-corruption for
  both sizes, the v3 UID-seed (XOR with 3) and FHSS sequence length
  (rounds down to 240 for the 80-channel domain), the v3 sync channel
  (freq_count//2 + 1 = 41, distinct from v4's 40), and v3/v4 divergence
  (sync builds and CRC inits differ between versions).
- The matrix is derived from the vendored `elrs_core` (itself validated
  against the pinned ExpressLRS / Betaflight 4.4.2 source) and checked for
  internal consistency. It records a known build-path quirk: the
  `pack_channels_4x10`/`build_rcdata_std` channel expansion is not a strict
  CRSF round-trip, while the decode path the passive pipeline uses is
  validated (the 54 CRC-valid frame result still reproduces).

Remaining before RQ2 can be called DONE (and it is not, pending promotion):
(1) tag-pin the known answers to the four ELRS source refs named in the row
(3.3.2 / 4.0.0 OTA + FHSS) rather than the vendored re-implementation; (2)
classify the tracked captures with confidence + provenance
(`PASSIVE-CAPTURE-VALIDATED` only after independent implementation
agreement); (3) make the build path a strict channel inverse (separate, low
risk, does not touch the validated decode path). This groundwork does not
activate the row, change its evidence target, or authorize any
receiver-acceptance, transmitter-identity, or RF work.

### Full-review findings and fixes (andeds, 2026-11-01)

A full review of the GitHub update (113 commits, 267 files, +82,944 lines) was
run: three parallel deep reviewers (ELRS core + passive decoder, exploit-chain
orchestrators, web dashboard) plus independent self-verification. The headline
research result reproduced exactly (54 CRC-valid frames across both ranked
captures), all firmware/capture SHA-256 sums verify, and the CVE analyses are
accurate and properly hedged. Defects found and **fixed** on this branch:

**Safety (highest value):**
- `cross_chain_orchestrator.py`: `--arm` was decorative — a bare
  `... full` (no `--arm`) with no `--dry-run` reached live connect/probe/inject
  branches because they only tested `state.dry_run`. Now
  `state.dry_run = args.dry_run or not args.arm` (matches every other chain),
  so a bare run is always a preview. Regression-locked by
  `test_cross_chain_arm_gate.py` (5 tests).
- `wifi_mavlink_chain.py`: the live evil-twin path imported the module but
  called nothing, then reported `status:"started"` (false success). Now fails
  closed with `status:"unimplemented"`.
- `common.py require_arm`: the `getattr(args, "dry_run", True)` default was a
  foot-gun (a Namespace lacking `dry_run` would allow TX). Now defaults to
  `False` (fail closed).
- `cross_chain_orchestrator.py` live inject stages reported unconditional
  `SUCCESS` although the attack callees return `None` and can't signal
  per-command success. Now report `ATTEMPTED` (commands sent); `StageStatus`
  gained `ATTEMPTED`.

**ELRS core (evidence-independent — the 54-frame result is unaffected):**
- `elrs-passive-pipeline.py _rc_channels`: used `988 + raw10bit` (a wrong
  affine). Now uses `uint10_to_crsf` — the firmware's exact inverse of
  `CRSF_to_UINT10(constrain(ch,171,1811))`.
- `elrs_core/ota.py decode_std` (8-byte hybrid path): the `<<1` unpack was
  returned raw (doubled / not CRSF). Firmware
  `UnpackChannelDataHybridCommon` applies `UINT10_to_CRSF(raw >> 1)`. Added
  `expand_hybrid_channels` and used it in `decode_std`. The 13-byte full-res
  path is distinct (its 10-bit fields span the full CRSF range) and was already
  correct — left unchanged. Locked by `TestHybridVsFullChannelExpansion`.

**Web dashboard (no P1 security holes; CSP strict and enforced, all DOM sinks
escaped):**
- `app.py`: `import importlib` → `import importlib.util` (the latter is not
  guaranteed by the former); untyped `action` body field (list/dict → 500) now
  rejected with 400; SSE `event:` field now newline-sanitized (latent
  SSE-injection sink).

**Test/provenance/CI (found in first pass, already committed):** missing
`import tempfile`; scapy/wifi, pytest/playwright browser-test discovery
crashes now guarded; `PROVENANCE.md` SHA-256s corrected for the ruff-cleaned
`ota.py`/`fhss.py`; duplicate `lint:` CI job renamed to `lint-full`; R7
evidence corrected.

**Remaining (not fixed — larger scope, tracked):** orchestrator v3/v4 core
mismatch on the default `--ota-version 3` RF preview (uses v4 core for a v3
target); `mavlink_chain.sniff_mavlink` positional target parse (IndexError on
IPv6 / no-port); duplicated MAVLink constants in `wifi_mavlink_chain.py`;
elrs_chain `max_power_dbm` dead heuristic; default binding phrase
`"iloveflying"` foot-gun in sim-only tools.

### Hardware queue

- **SX1280 receive-only CAD/coexistence profiler:** model thresholds offline;
  later use a dev board strictly as a receiver. CAD occupancy is not transmitter
  attribution and is not a substitute for a wideband SDR.
- **Wideband FHSS/Gemini/DVDA correlation:** first use a calibrated SDR for
  UID-free occupancy, hop-timing, and coexistence measurements on one coherent
  multi-channel timeline. UID-dependent source-model agreement is a later gate
  requiring an independently supplied complete UID and enough distinct
  observations. No TX or injection is part of either stage.
- **Physical STM32F411 fault injection:** requires a sacrificial equivalent
  board, a bounded rig, and a new explicit decision. The TinyHawk is excluded.
- **Live Remote ID, DroneCAN, or proprietary DroneID validation:** requires the
  appropriate receiver/adapter and an owned transmitter/node, plus privacy
  handling. Official sample captures remain suitable for offline work now.

## Historical LATER list

- Persistent scan-session summaries and cancelable simulated/offline progress.
- Further replacement of `Any` with concrete target, event, and action types.
- Packaging of the web application only, if an installable product is actually
  desired after the app-factory layout stabilizes.
- Pytest support only when it provides fixture or reporting value beyond the
  existing `unittest` suite.
- Further task-board polish after the R6 reliability/theme work.

## Global gates / exclusions

- Receiver-acceptance tests, transmitter execution, RF, serial, live hardware
  discovery, and state-changing device or network actions.
- Any firmware write, update, recovery, or flash for the E8 or TinyHawk.
- Strong FHSS claims until one coherent multi-channel passive timeline, an
  independently supplied complete UID, and enough distinct observations exist.
- External disclosure, publication, or contact without explicit coordination.
- Live scan/action paths in CI.

The focused `research/receiver-acceptance-validation-plan.md` remains a design
reference only. Its active stages are not authorized by appearing in that file.

## Historical Claude task-list assessment

This table preserves Claude's original IDs and the decisions made during the
completed R0-R7 cycle. Its status column is historical and does not activate
current work.

| ID | Original task | Status | Roadmap decision |
|---|---|---|---|
| 1a | Theme toggle | NOW | Folded into R6 after the completed R5 asset/CSP handoff. |
| 1b | Responsive mobile UI | DONE | Definite viewport height, scroll reachability, focus recovery, and 390 px browser coverage landed. |
| 1c | Collapsible log viewer | PARTIAL | Drawer landed; hydration, copy, resize, and persistence move to R6. |
| 1d | SSE backoff | CHANGED | Native `EventSource` already reconnects and carries `Last-Event-ID`; add observable state/retry in R6, not a competing loop. |
| 1e | Hardware widget | DONE | Widget, refresh, tests, probe-free simulation semantics, and honest UDP-listener labeling landed. |
| 2a | Ruff CI | CHANGED | Fold into incremental R7; reject whole-tree lint/format churn. |
| 2b | Python 3.13 | NEXT | Part of R7 after the current matrix is green. |
| 2c | Mypy CI | CHANGED | Part of R7; make it meaningful before making it blocking. |
| 3a | Project packaging | DEFERRED | Mixed research scripts are not one clean package; revisit web-only packaging after R1. |
| 3b | Development requirements | CHANGED | Do not install BLE/SDR stacks in unrelated offline CI; pin narrow tooling where used. |
| 4a | Web UI guide | DONE | R4 now documents exact flags, modes, dependencies, side effects, architecture, API semantics, lifecycle, tests, and browser-isolation limits. |
| 4b | Main README setup/contributing | DONE | Practical setup, tests, structure, and safety checklist landed. |
| 4c | Tools catalog | DONE | R4 adds the maintained passive path, corrects test paths and evidence limits, and separates offline, receive-only, active-query, and state-changing behavior. |
| 5a | Type `web/app.py` | PARTIAL | Function hints mostly landed; state design and concrete types follow R1/R7. |
| 5b | Type `common.py` | DONE | Function annotations landed; strict checking joins R7. |
| 5c | Ruff autoformat | REJECTED AS WRITTEN | Use incremental maintained-module checks; no blanket format pass. |
| 6a | Shared test helpers | PARTIAL | Useful alternative helpers landed and are used; avoid adding unused abstractions. |
| 6b | Flask route tests | DONE | 38 route tests pass in `web/tests/test_api_routes.py`. |
| 6c | Pytest configuration | LATER | Existing `unittest` discovery is established; include both test roots if revisited. |
| 6d | `conftest.py` fixtures | DEFERRED | Do not entrench global resets; wait for R1 and a demonstrated pytest need. |

## Completed foundations

- Offline Ubuntu/Windows validation on Python 3.10 and 3.12.
- Flask route tests and loopback-only browser E2E coverage.
- Responsive dashboard behavior, focus recovery, log drawer, and hardware widget.
- Probe-free deterministic hardware status in simulation, with live detector delegation preserved.
- Root, web, and tool documentation aligned with current modes, tests, side
  effects, passive evidence, and CI behavior.
- Capture-proven passive SX1280 SF7/4-8-LI decoder and fail-closed pipeline.
- Private-output confinement, provenance, duplicate-evidence controls, and
  dependency-free passive decoder tests.

## Updating this roadmap

1. Change task status here in the same commit as its implementation or evidence.
2. Append the detailed evidence and handoff to `SESSION-NOTES.md`; do not copy a
   competing priority list there.
3. When a focused plan is needed, link it from the task instead of giving it an
   independent priority.
4. If a task becomes blocked, record the exact missing evidence or authorization
   and promote the next eligible item.
