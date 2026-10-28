# Project Roadmap

Last reviewed: 2026-10-28

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
- Each task names a primary and an independent reviewer. The primary owns its
  implementation and acceptance evidence; the reviewer stays read-only on that
  implementation surface until the primary pushes a handoff, unless this
  roadmap explicitly transfers ownership. A planned assignment does not
  activate work, and ownership is never permission to bypass a gate.
- Keep the maintained backlog serialized in this order unless an explicit
  owner decision changes it: R1 Codex -> R2 Codex -> R3 Claude -> R4 Claude ->
  R5 Codex -> R6 Claude -> R7 Codex. This keeps related file families under one
  writer and preserves independent review.

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
**Status:** NOW — Codex implementation complete; Claude review pending
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
The CI web matrix now covers Python 3.10/3.12 on Ubuntu and Windows. R1 remains
NOW until Claude completes the planned independent review; do not promote R2
from this implementation handoff alone.

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

**Owner:** Claude implementation after the R5 handoff; Codex browser/integration review
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

## GATED research pathway queue

R1 remains the single active implementation lane. Every RQ item below has
status **GATED** and no active owner. Each row records a dormant planned primary
and reviewer so a future handoff is unambiguous. Promotion requires an explicit
owner decision and a roadmap change that moves R1 out of NOW, activates the
planned assignment (or records a replacement), and records its evidence
target. Merely appearing here does not authorize serial, RF, hardware, or
live-device work.

The queue was derived from the owner-provided one-screen summary, repository
evidence, and a primary-source research pass. The linked Claude artifact
required sign-in and was not treated as read evidence.

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
| RQ6 | **Firmware provenance, SBOM, and feature reachability** | Claude primary; Codex review | Candidate source tree or firmware artifact | Produce parse-only manifests for chip/board, firmware/framework version, Git identity, build options, hashes, dependencies, and transports. Record component presence, affected-version match, feature enablement, and reachability as separate confirmed/absent/unknown fields. | `SOURCE-DERIVED`: no advisory is marked applicable without all required evidence; missing reachability evidence is `unknown`, not `not applicable`. Never flash or sign. This gate controls every ESP CVE lane. |
| RQ7 | **STM32F411 errata-to-source conformance** | Claude primary; Codex review | Pinned Betaflight 4.4.2 and ST documents | Map SPI/SX1280, USB OTG, DMA, timer, timeout, reset, and watchdog call sites to [ES0287](https://www.st.com/resource/en/errata_sheet/es0287-stm32f411xcxe-device-errata-stmicroelectronics.pdf); add mocks for the exact documented failure conditions. | `SOURCE-DERIVED` and `SYNTHETIC/OFFLINE-TESTED`; do not infer a device observation, general fault susceptibility, or physical-glitch result. |
| RQ8 | **Software safety fault-model mutation** | Codex primary; Claude review | RQ4 state model | Simulate skipped checks, corrupted branch outcomes, timeout failures, resets, and watchdog events around safety-critical state transitions without modeling a physical injection apparatus. | `SYNTHETIC/OFFLINE-TESTED` only. Results are not labeled silicon errata, device evidence, or voltage-glitch susceptibility. |
| RQ9 | **OpenDroneID multi-transport/dialect parser corpus** | Claude primary; Codex review | Official sample pcaps and pinned reference source | Cross-check BLE/Wi-Fi transport framing, message fields, units, versions, duplicates, and redaction through the [OpenDroneID C reference](https://github.com/opendroneid/opendroneid-core-c) and [Wireshark dissector](https://github.com/opendroneid/wireshark-dissector). | `SYNTHETIC/OFFLINE-TESTED` against official fixtures. Parsing an Authentication message is not signature or identity trust; redact IDs/coordinates and gate live ambient capture separately. |
| RQ10 | **DRIP authentication and trust-state verifier** | Claude primary; Codex review | RQ9 plus pinned RFC test vectors, keys/trust roots, and registry/DNS snapshots | Verify syntax, signatures, freshness/replay, Manifest/Wrapper binding, and registry-chain state using [RFC 9374](https://www.rfc-editor.org/rfc/rfc9374.html) and [RFC 9575](https://www.rfc-editor.org/rfc/rfc9575.html). Report separate syntactically valid, signature-valid, identity-trusted, unsigned/unverifiable, stale, conflicting, and invalid states. | `SYNTHETIC/OFFLINE-TESTED`: every verdict identifies its test vector, key/trust-root snapshot, and unavailable dependency. Unsigned is not mislabeled spoofed; no live registry query by default. |
| RQ11 | **MAVLink generated-contract and gap audit** | Codex primary; Claude review | Existing repository MAVLink signing/replay tools; a maintained module scope approved at promotion | Generate missing message ID/length/field/`CRC_EXTRA` contracts from official dialect XML, then close uncovered routing, ACK-state, and log-redaction gaps using synthetic fixtures. Follow the official [signing](https://mavlink.io/en/guide/message_signing.html) and [routing](https://mavlink.io/en/guide/routing.html) rules. | `SYNTHETIC/OFFLINE-TESTED`: do not duplicate already-covered signing/replay tests; use no real key material, `SETUP_SIGNING` payload, or external link. Lower relevance to the current Betaflight target. |
| RQ12 | **CFO and waveform residuals as capture QA** | Codex primary; Claude review | RQ1 | Extract CFO, SFO/chirp-slope residuals, baseline bins, and decoder confidence from existing private IQ to detect capture drift and calibration problems. | `PASSIVE-CAPTURE-VALIDATED` only as capture QA. A device-fingerprinting claim requires a predeclared, held-out multi-device/multi-session design spanning relevant temperatures, locations, and calibrated receivers; studies show [CFO drift](https://arxiv.org/abs/2101.01668) and strong [channel/configuration/receiver sensitivity](https://ieeexplore.ieee.org/document/9580899). |
| RQ13 | **Conditional ESP patch-differential corpus** | Claude primary; Codex review | RQ6 confirms exact applicability | Reproduce fixed/affected behavior in offline source tests only for proven components and configurations: [CVE-2024-42484](https://github.com/espressif/esp-now/security/advisories/GHSA-q6f6-4qc5-vhx5), native ESP-IDF [CVE-2025-52471](https://github.com/espressif/esp-idf/security/advisories/GHSA-hqhh-cp47-fv5g), BLE provisioning [CVE-2026-25507](https://github.com/espressif/esp-idf/security/advisories/GHSA-h7r3-gmg9-xjmg) / [CVE-2026-25508](https://github.com/espressif/esp-idf/security/advisories/GHSA-9j5x-rf36-54x9), or SoftAP DHCP [CVE-2026-45160](https://github.com/espressif/esp-idf/security/advisories/GHSA-g764-gwc3-75m5). | `SYNTHETIC/OFFLINE-TESTED`: synthetic packets and local processes only; never present these as blanket ESP32 or E8 findings. |
| RQ14 | **Adjacent-stack schema and topology health** | Claude primary; Codex review | A demonstrated owned-platform or fixture need | Add offline DSDL/transfer-ID checks for [DroneCAN](https://dronecan.github.io/Specification/6._Application_level_functions/), bounded component-metadata parsing, and firmware-envelope provenance only for ecosystems actually added to the project. | `SYNTHETIC/OFFLINE-TESTED`: keep adapters dormant until needed; no CAN or external metadata fetch by default. |

If the owner explicitly promotes a research task and moves R1 out of NOW, start
with RQ1, then RQ2-RQ4. RQ5-RQ8 are the next target-specific evidence paths.
RQ9-RQ14 are useful expansion paths, not reasons to delay current work.

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
