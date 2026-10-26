# Receiver Acceptance Validation Plan

**Status:** PLAN — no RF transmission, no hardware, no live testing  
**Evidence class:** SOURCE-DERIVED + SYNTHETIC-TEST scaffolding  
**Last updated:** 2026-10-26  
**Author:** Claude (thread: Plan receiver acceptance validation)

## 1. Purpose

This plan defines the methodology for validating that a forged ELRS packet is
**accepted by the live TinyHawk III receiver** and produces an observable state
change on the flight controller. Until receiver acceptance is demonstrated, all
upstream work (UID recovery, packet forging, CRC/FHSS derivation) establishes
only that our bytes are *self-consistent with the source*, not that the target
acts on them.

The plan follows the project's evidence hierarchy (SESSION-NOTES §11.6):

| Milestone | Evidence |
|---|---|
| M1: Source-derived behavior | Done (§17, §15) |
| M2: Observed packets with defined version/format | Requires passive capture |
| M3: Candidate identity match with documented ambiguity | Requires UID recovery |
| M4: Validated receiver state + timing in controlled lab | **This plan** |
| M5: Demonstrated receiver behavior | **This plan's end state** |

## 2. Safety constraints (non-negotiable)

1. **No flashing** of the E8 or TinyHawk under any circumstances.
2. **Props removed** before any powered bench test with RF transmission.
3. **Constrained RF:** minimal TX power, directional coupling or close
   proximity (<1 m), no transmission outside the 2.4 GHz ISM band.
4. **Dry-run default:** every tool defaults to simulation. RF transmission
   requires `--arm` or equivalent explicit flag.
5. **Documented stop condition:** each test step defines what constitutes
   abort (unexpected arming, unexpected mode change, loss of telemetry).
6. **No "commandeering" claim** until M5 is reached with documented evidence.
7. **localhost-only network** for all SITL and offline testing.
8. **UID/binding-phrase material stays under ignored `work/`.**

## 3. Prerequisites

### 3.1 Hardware (owner's bench)

| Item | Role | Status |
|---|---|---|
| TinyHawk III | Target receiver (Betaflight 4.4.2, ELRS v3, LoRa SF7 BW800 150 Hz) | Available |
| EMAX E8 | Reference TX (for binding, normal-operation baseline) | Available, no-flash |
| Ettus B205mini or B210 | SDR TX/RX | Available |
| USB serial | Betaflight CLI for state readback | Available (read-only) |

### 3.2 Software (already in repo or installable)

| Component | Location | Purpose |
|---|---|---|
| `tools/elrs-packet-forger.py` | Repo | Forge SYNC/RC/MSP packets (v3, uses `elrs_core`) |
| `tools/elrs_core/` | Repo | Vendored CRC/FHSS/OTA (v2-v6 parameterized) |
| `tools/elrs-v3-bounded-rc-injection.py` | Repo | Safe bounded-RC canary builder |
| `tools/elrs-v3-rc-replay-canary.py` | Repo | Safe captured-frame replay selector |
| `tools/elrs-v3-fresh-sync-clock.py` | Repo | SYNC-relative timing bridge |
| `tools/sitl_harness.py` | Repo | SITL lifecycle (Chain 2 MAVLink, reference) |
| ArduPilot SITL 4.7.2 | `firmware/ardupilot/` | Offline MAVLink command validation |
| pymavlink | pip | MAVLink message construction |

### 3.3 Information required before bench testing

| # | Item | Source | Status |
|---|---|---|---|
| I1 | Complete 6-byte UID | Air recovery (`fhss_seed.py`) or E8 read or bind-mode `{0,1,2,3,4,5}` | Known for owned pair (private, under `work/`) |
| I2 | OTA version | Confirmed v3 (SESSION-NOTES §17) | Done |
| I3 | Air rate | Confirmed LoRa SF7 BW800 150 Hz, rate_index 2 (§15, §17) | Done |
| I4 | Model ID | Confirmed 255 → modelXor=0 (§15) | Done |
| I5 | FHSS sequence | Derivable from UID via `elrs_core.fhss` | Ready |
| I6 | CRC14 init | Derivable from UID via `elrs_core.crc` (v3 low-byte XOR) | Ready |
| I7 | Sync channel | Confirmed channel 41 (v3 formula) | Done |
| I8 | Betaflight AUX/mode map | Read-only CLI dump from live TinyHawk | Needed before RC injection |

## 4. Validation stages

### Stage A: Passive baseline capture (M2)

**Goal:** Record the normal E8→TinyHawk link as ground truth.

**Procedure:**
1. Power TinyHawk, bind to E8 normally.
2. Record IQ on B205/B210 at 2440.4 MHz center, 10 MS/s, 10 seconds.
3. Decode with `elrs-analyzer.py` (or `elrs_core.ota` directly).
4. Extract: SYNC packets, FHSS hop sequence, CRC validity, nonce progression.
5. Cross-check forger output against decoded real frames byte-for-byte.

**Pass criteria:**
- Forger's SYNC bytes match decoded real SYNC bytes (same UID, same CRC init).
- FHSS sequence from `elrs_core.fhss` matches observed hop pattern.
- CRC14 validates on ≥95% of decoded frames.

**Abort:** If forger bytes diverge from real frames, return to protocol debugging.
Do not proceed to Stage B until Stage A passes.

**Evidence output:** `work/baseline-capture-report.json` (sanitized summary
to `research/`, raw IQ under `work/`).

### Stage B: Read-only MSP probe (M4, canary)

**Goal:** Demonstrate that the receiver processes a **harmless read-only MSP
request** (`MSP_API_VERSION`, function 1) sent over the forged ELRS link.

This is the first test where forged packets are transmitted. It is chosen
because:
- `MSP_API_VERSION` (cmd 1) is a pure read — it returns the API version
  string and changes no state on the FC.
- Success is observable: the TinyHawk's ELRS telemetry uplink carries the
  MSP response back to the E8 (or a second SDR receiver).
- Failure is safe: a rejected packet produces no state change.

**Procedure:**
1. **Props removed.** Verify visually and photograph.
2. Power TinyHawk, bind to E8. Confirm normal link via E8 display.
3. Read Betaflight state via USB serial CLI: `status`, `get rc_channels`,
   `get mode_ranges`. Record as baseline.
4. Configure B205 for TX at **minimum power** (0 dBm or lowest UHD setting),
   close proximity (<0.5 m), LoRa SF7 BW800 at the sync channel frequency.
5. Build the MSP_API_VERSION request:
   ```
   python tools/elrs-packet-forger.py --phrase <phrase> --ota-version 3 \
       msp --msp-cmd 1 --arm
   ```
6. Transmit a single SYNC packet followed by the three MSP fragments
   (the `elrs-v3-canary-schedule.py` timing plan).
7. Monitor:
   - SDR RX on a second channel (or the B210's second port): look for
     telemetry response carrying MSP reply.
   - Betaflight CLI: `status` — verify no mode change, no arming, no reboot.
   - E8 display: confirm link is still active (not interrupted).

**Pass criteria (M4):**
- The TinyHawk's telemetry uplink contains an MSP_API_VERSION response
  (3 bytes: protocol version, API major, API minor).
- Betaflight `status` shows no state change from baseline.
- No arming event, no mode change, no failsafe triggered.

**Abort conditions:**
- Any arming event → power off TinyHawk immediately.
- Any unexpected mode change → power off.
- Loss of E8 link for >5 seconds → power off, investigate.
- Any MSP command other than the intended one observed in telemetry → stop.

**Evidence output:** `work/msp-probe-results.json` with:
- Transmitted packet hex (SYNC + 3 MSP fragments)
- Received telemetry hex
- Betaflight CLI before/after
- Timestamps and RF parameters
- Sanitized summary → `research/msp-probe-summary.md`

### Stage C: Bounded neutral RC canary (M4, expanded)

**Goal:** Demonstrate that the receiver accepts a forged RC packet containing
**neutral/disarmed stick values** and that the FC's channel readout reflects
the injected values.

This uses the existing `elrs-v3-bounded-rc-injection.py` tool, which is
designed for exactly this purpose: it synthesizes a single hybrid-RC packet
with throttle at minimum (988), pitch/yaw centered (1500), AUX1 low
(disarmed), and roll at a distinctive but safe value (1400 or 1600).

**Procedure:**
1. **Props removed.** Verify visually.
2. Power TinyHawk, bind to E8. Record baseline `status` and channel values.
3. Read the AUX/mode map from Betaflight CLI and save to
   `work/tinyhawk-aux-safety-v3.json` in the required schema.
4. Build the bounded RC canary:
   ```
   python tools/elrs-v3-bounded-rc-injection.py \
       --uid-file work/private-uid.hex \
       --aux-safety-json work/tinyhawk-aux-safety-v3.json \
       --roll-direction right \
       --private-plan-out work/bounded-rc-injection.json
   ```
5. Transmit: SYNC → RC packet at the correct FHSS channel/timing.
6. **Within 500 ms of transmission**, read Betaflight CLI `rc_channels`:
   - Expect roll ≈ 1600 (right), throttle ≈ 988, pitch/yaw ≈ 1500.
   - Expect AUX1 low (disarmed).
7. Record before/after channel values.

**Pass criteria (M4+):**
- `rc_channels` readout shows the injected values (within ±10 counts).
- No arming event.
- No mode change.
- FC remains in STABILIZE (or its pre-test mode).

**Abort:** Same as Stage B.

**Evidence output:** `work/rc-canary-results.json`.

### Stage D: Demonstrated receiver behavior (M5)

**Goal:** Show that a forged MSP command produces an **observable, controlled
state change** on the FC — specifically, a mode change to a safe mode.

**Candidate commands (in order of preference):**

1. **`MSP_SET_RAW_RC` with mode-switch channel** — set a safe mode
   (e.g., ANGLE mode) by changing the AUX channel that controls modes.
   Observable via Betaflight CLI `status` showing the mode change.

2. **`MSP_REBOOT` (cmd 68)** — reboot the FC. Observable via USB serial
   disconnect/reconnect and boot banner. Completely reversible. Demonstrates
   code execution without any flight-related state change.

3. **RC-only mode change** — forge an RC packet with AUX values that trigger
   a configured mode switch (e.g., HORIZON mode). Observable via CLI.

**Procedure (using MSP_REBOOT as the safest demonstration):**
1. Props removed. USB serial connected. Betaflight CLI open.
2. Power TinyHawk, bind to E8. Record `status`.
3. Build MSP_REBOOT:
   ```
   python tools/elrs-packet-forger.py --phrase <phrase> --ota-version 3 \
       msp --msp-cmd 68 --arm
   ```
4. Transmit SYNC → MSP fragments.
5. Observe: USB serial disconnects, FC reboots, boot banner appears.
6. After reboot: `status` shows default post-boot state.

**Pass criteria (M5):**
- FC rebooted (USB disconnect/reconnect observed).
- Boot banner confirms clean restart.
- No unexpected behavior (no arming, no motor activity).
- The reboot was caused by the forged MSP command, not by link loss or
  failsafe (distinguish by timing: reboot within 100 ms of MSP TX, vs.
  failsafe timeout of several seconds).

**Do NOT claim "commandeering" from a reboot alone.** M5 with MSP_REBOOT
demonstrates *code execution* (the FC acted on a forged MSP command). Full
commandeering (sustained control) requires sustained RC injection with
demonstrated stick-to-motor response, which is a separate future milestone
requiring props-on testing with appropriate safety measures.

**Evidence output:** `work/m5-reboot-results.json`, sanitized summary →
`research/m5-receiver-acceptance-summary.md`.

## 5. Evidence grading

Each stage's results are graded per the project's evidence hierarchy:

| Grade | Meaning | Example |
|---|---|---|
| SOURCE-DERIVED | Conclusion follows from reading source code | "MSP handler is reachable" (§17) |
| SYNTHETIC-TEST | Round-trip passes in simulation/offline | Forger encode→decode (§10.1) |
| PASSIVE-CAPTURE | Observed from real RF without injection | Stage A baseline |
| RECEIVER-ACCEPTED | Target FC acted on injected input | Stages B-D |

No stage's output is promoted to a higher grade until its pass criteria are
met with documented evidence.

## 6. What this plan does NOT cover

- **Sustained commandeering** (continuous RC override with motor response).
  Requires props-on testing, separate safety plan, separate authorization.
- **UID air-recovery** from an unknown target. Assumes UID is already known
  (owned pair) or recovered via `fhss_seed.py` / E8 read / bind-mode.
- **Game-day operational use.** This is bench validation, not a field-ready
  procedure.
- **FLRC modulation.** The live TinyHawk (4.4.2) is confirmed LoRa-only (§15).
  FLRC validation is a separate work item if a different target uses it.
- **The E8 WiFi AP exploit** (Chain 1, Layer 2). That is a separate attack
  surface, though it could simplify UID recovery for Stage A.

## 7. Dependencies on other work

| Dependency | Owner | Status | Blocks |
|---|---|---|---|
| `elrs_core` rewire complete | Done (§17) | ✓ Done | — |
| OTA v3 CRC init correction | Done (§17) | ✓ Done | — |
| Sync channel = 41 (v3) | Done (§17) | ✓ Done | — |
| MSP reachability confirmed | Done (§16, §17) | ✓ Done | — |
| LoRa TX modulation (Phase 2 PHY) | Codex | **Blocked** | Stages B-D |
| Passive capture + decode | Owner + bench | Not started | Stage A |
| Betaflight AUX/mode dump | Owner (read-only CLI) | Not started | Stage C |

**Critical path:** LoRa TX modulation (Phase 2 PHY, §9.4 steps 9-11) is the
single blocker for Stages B-D. Stage A (passive capture) can proceed
independently. The offline test scaffolding (Section 8) validates everything
except the actual RF transmission.

## 8. Offline test scaffolding

The `tools/tests/test_receiver_acceptance.py` file provides offline validation
of every step that does not require hardware:

1. **Forger round-trip:** encode a SYNC/RC/MSP packet with `elrs_core`, decode
   it back, assert byte equality.
2. **Safety invariant checks:** verify that the bounded-RC canary builder
   rejects unsafe inputs (high throttle, armed AUX, unconstrained roll).
3. **MSP command validation:** verify that only the intended MSP commands
   (API_VERSION=1, REBOOT=68) are accepted by the test scaffolding, and that
   destructive commands (SET_MOTOR, SET_ARMING_DISABLED) are rejected.
4. **Evidence schema validation:** verify that output files conform to the
   expected JSON schemas.
5. **SITL cross-reference:** validate MSP command construction against
   ArduPilot SITL (reuses the existing `sitl_harness.py`).

These tests run without hardware, SDR, or RF. They establish that the
*software pipeline* is correct before any bench testing begins.
