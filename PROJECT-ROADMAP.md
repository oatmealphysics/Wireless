# Project Roadmap

Last reviewed: 2026-10-27

This file is the sole source of truth for current project priorities, status,
ownership, acceptance criteria, and next-task selection. It consolidates and
updates Claude's `codex-tasks.html` task inventory after comparing it with the
code, tests, merged PRs, and the evidence log.

`SESSION-NOTES.md` remains the canonical append-only evidence and handoff log;
it does not activate or reprioritize work. `codex-tasks.html`,
`session-notes-2026-10-25.md`, `attack-research-plan.md`,
`HOME-LAB-HANDOFF.md`, and topic-specific plans are historical context or
execution specifications. If any of them conflict with this roadmap, this file
wins.

## Operating rules

- **NOW** is the single active implementation lane. Finish or explicitly block
  it before promoting another task.
- **NEXT** is ordered. Pick the first task whose dependencies and acceptance
  criteria are satisfied.
- **LATER** is useful but not yet worth active complexity or CI cost.
- **GATED** requires a new, explicit owner decision before work begins.
- **DONE** means the stated acceptance evidence exists in the repository.
- Every NOW task must have a safe scope, an executable acceptance test, a named
  evidence class, and no unmet authorization or hardware gate.
- Codex normally implements the NOW task and Claude independently reviews it.
  Ownership is a coordination aid, not permission to bypass these gates.

## Scope and evidence boundary

Default active work is offline application engineering: tracked fixtures and
passive captures, deterministic simulation, local files, and loopback-only
tests. It excludes hardware access, serial access, RF transmission, firmware
changes, external targets, and state-changing network or device actions.

The E8 and TinyHawk no-flashing rule is absolute. A plan or tool is never
authorization. Private identifiers, UIDs, and raw captures remain under ignored
`work/`; only sanitized aggregates and hashes are tracked.

Evidence labels are not interchangeable:

- **SOURCE-DERIVED:** established from pinned source or protocol definitions.
- **SYNTHETIC/OFFLINE-TESTED:** deterministic local fixtures and simulations pass.
- **PASSIVE-CAPTURE-VALIDATED:** independently reproduced from recorded traffic.
- **RECEIVER-ACCEPTED:** an owned receiver accepted a bounded candidate under a
  separately approved bench plan.

The current passive ELRS result is `PASSIVE-CAPTURE-VALIDATED`: 54 CRC-valid
frames from two tracked captures agree byte-for-byte across two decoders. It is
not evidence of receiver acceptance, transmitter identity, measured
multi-channel FHSS agreement, or an operational takeover capability.

## Completed this cycle

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

## NOW

### R1 — Isolate dashboard state behind an application/state factory

**Owner:** Codex implementation; Claude review
**Depends on:** R0
**Status:** NOW
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

## NEXT

### R2 — Lock and bound every mutable history

**Owner:** Codex implementation; Claude adversarial concurrency review
**Depends on:** R1

Use one documented ownership/locking model. Bound events, action history,
Chain 2 injections, and any auxiliary integration histories. Add concurrency
and rollover tests.

### R3 — Add cross-instance and lifecycle regression coverage

**Owner:** Claude tests; Codex integration review
**Depends on:** R1 and R2

Prove that targets, logs, events, scans, and Chain 2 state cannot leak between
app instances or tests. Cover partial startup failure and cleanup.

### R4 — Refresh maintained documentation

**Owner:** Claude documentation; Codex evidence review
**Depends on:** R0

Bring `web/README.md` and `tools/README.md` in line with actual behavior. Add
the passive pipeline and decoder, fix test paths, distinguish active reads from
passive observation, and remove operational claims not supported by evidence.

### R5 — Extract dashboard assets and add a strict CSP

**Owner:** Codex implementation; Claude browser review
**Depends on:** R1–R3

Move inline CSS and JavaScript to versioned static files, remove inline event
and style dependencies, then enforce a self-only Content Security Policy. Keep
the full loopback browser suite green.

### R6 — Finish selected UX reliability gaps

**Owner:** split by subtask
**Depends on:** R5 for theme work

In order:

1. Hydrate the log viewer from `/api/log`, add Copy, resizing, and persisted
   height.
2. Add a persistent connection-state banner and `Retry now` control while
   retaining native `EventSource` reconnect and `Last-Event-ID` recovery. Do
   not add a competing manual reconnect loop without a failing test.
3. Add a dark/light theme after asset extraction.

### R7 — Incremental quality gates

**Owner:** Codex configuration; Claude cleanup/review
**Depends on:** current Ubuntu/Windows Python 3.10/3.12 matrix green

- Add Python 3.13 and fix compatibility issues; do not skip failures merely to
  turn CI green.
- Introduce pinned Ruff and mypy checks only for maintained, already-clean
  modules, then ratchet scope.
- Exclude vendored/generated/archive material. Do not blanket-format the
  repository or install optional hardware stacks in offline validator jobs.

## LATER

- Persistent scan-session summaries and cancelable simulated/offline progress.
- Further replacement of `Any` with concrete target, event, and action types.
- Packaging of the web application only, if an installable product is actually
  desired after the app-factory layout stabilizes.
- Pytest support only when it provides fixture or reporting value beyond the
  existing `unittest` suite.
- Cosmetic theme and task-board polish after reliability work.

## GATED / excluded from the active backlog

- Receiver-acceptance tests, transmitter execution, RF, serial, live hardware
  discovery, and state-changing device or network actions.
- Any firmware write, update, recovery, or flash for the E8 or TinyHawk.
- Strong FHSS claims until one coherent multi-channel passive timeline, an
  independently supplied complete UID, and enough distinct observations exist.
- External disclosure, publication, or contact without explicit coordination.
- Live scan/action paths in CI.

The focused `research/receiver-acceptance-validation-plan.md` remains a design
reference only. Its active stages are not authorized by appearing in that file.

## Claude task-list assessment

This table preserves Claude's original IDs while correcting stale premises and
making dependencies explicit.

| ID | Original task | Status | Roadmap decision |
|---|---|---|---|
| 1a | Theme toggle | LATER | Safe cosmetic work; follow R5. |
| 1b | Responsive mobile UI | DONE | Definite viewport height, scroll reachability, focus recovery, and 390 px browser coverage landed. |
| 1c | Collapsible log viewer | PARTIAL | Drawer landed; hydration, copy, resize, and persistence move to R6. |
| 1d | SSE backoff | CHANGED | Native `EventSource` already reconnects and carries `Last-Event-ID`; add observable state/retry in R6, not a competing loop. |
| 1e | Hardware widget | DONE | Widget, refresh, tests, probe-free simulation semantics, and honest UDP-listener labeling landed. |
| 2a | Ruff CI | CHANGED | Fold into incremental R7; reject whole-tree lint/format churn. |
| 2b | Python 3.13 | NEXT | Part of R7 after the current matrix is green. |
| 2c | Mypy CI | CHANGED | Part of R7; make it meaningful before making it blocking. |
| 3a | Project packaging | DEFERRED | Mixed research scripts are not one clean package; revisit web-only packaging after R1. |
| 3b | Development requirements | CHANGED | Do not install BLE/SDR stacks in unrelated offline CI; pin narrow tooling where used. |
| 4a | Web UI guide | PARTIAL | Substantial guide landed; refresh exact flags, architecture, simulation semantics, and examples in R4. |
| 4b | Main README setup/contributing | DONE | Practical setup, tests, structure, and safety checklist landed. |
| 4c | Tools catalog | PARTIAL | Catalog landed but misses recent passive tools and contains stale evidence claims; R4. |
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
- Root, web, and tool documentation foundations.
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
