# Session Notes — October 25, 2026

> **Historical snapshot:** This standalone note was incorporated with
> corrections into `SESSION-NOTES.md` §53. Current priorities and status live
> only in `PROJECT-ROADMAP.md`; nothing in this file is an active assignment.

## Overview

Project status review and forward planning session. The research project has matured significantly since the October 14–15 sprint: Chain 1 (ELRS injection) is software-validated against real captures, 20+ PRs have been merged covering the full tool suite, and three PRs are staged for merge. This document captures what's proven, what's open, and the prioritized path forward.

---

## 1. Current State — What's on Main (Proven)

Chain 1 (ELRS packet injection) software has been **validated against real EMAX E8 / TinyHawk captures**:

- **54 PHY frames** pass v3 CRC and FHSS sequence verification
- Capture-to-forge validation merged in [PR #39](https://github.com/oatmealphysics/research-project/pull/39)
- End-to-end chain: binding phrase → MD5 → UID → FHSS seed → CRC init → packet forge → CRC verify

### Merged PRs (20+)

The following capabilities are merged to main and tested:

| Category | PRs | Tools / Docs |
|---|---|---|
| ELRS TX emulator | #18 | `elrs-tx-emulator.py` — live TX via SPI to SX1280 |
| MAVLink signing bypass | #19 | `mavlink-signing-bypass.py` — v2 signing weakness PoC |
| CRSF analysis | #20 | `crsf-tool.py` — Crossfire packet parsing and crafting |
| Exploit chains | #21 | End-to-end automated attack sequences |
| WiFi tools | #12 | WiFi drone attack surface reference |
| Betaflight analysis | #34 | MSP-over-ELRS attack surface mapping |
| Replay canary | #36 | Fail-closed ELRS v3 hybrid-RC replay canary |
| Web UI | #38 | Web-based drone scanner dashboard |
| Capture validation | #39 | Chain 1 PHY frame validation against real captures |
| Rainbow tables | #5 | 723 binding phrases, v3/v4 OTA support |
| Recon tools | #9, #13, #16 | BLE scanner, curses dashboard, passive telemetry |
| Protocol research | #6, #7, #8 | Failsafe mapping, GPS spoofing, ELRS v3/v4 diff |
| Testing | #10, #37 | 90+ integration tests, MSP injection simulator |
| SDR compatibility | #14 | SoapySDR abstraction layer for all RF tools |

## 2. Open Pull Requests (#40–#42)

Three PRs are ready to merge:

| PR | Title | Purpose |
|---|---|---|
| [#40](https://github.com/oatmealphysics/research-project/pull/40) | Add SX1280 TX hardware guide | Hardware acquisition guide for Chain 1 live testing ($23–$69 dev boards) |
| [#41](https://github.com/oatmealphysics/research-project/pull/41) | Wire real scanners into web UI | Connect Flask dashboard to real scanner/exploit tools with SSE updates |
| [#42](https://github.com/oatmealphysics/research-project/pull/42) | Chain 2 WiFi → MAVLink injection tools | Evil twin AP, MitM proxy, and MAVLink injection for WiFi-based attack chain |

## 3. Ways Forward

### Immediate (No Hardware Required)

1. **Merge PRs #40–#42** — all three are reviewed and ready
2. **Chain 2 SITL end-to-end test** — run evil twin → MitM → MAVLink injection against ArduPilot SITL to validate the full WiFi attack chain without hardware
3. **Web UI integration testing** — verify the Flask dashboard drives real tools with SSE status updates
4. **Cross-chain orchestrator** — build a unified launcher that coordinates Chain 1 (ELRS injection) and Chain 2 (WiFi → MAVLink) from a single interface

### Hardware-Dependent

5. **Acquire SX1280 dev board** ($23–$69, hardware guide in PR #40) — SDR cannot do native LoRa modulation, so a dedicated SX1280 module is required for Chain 1 live RF
6. **Chain 1 live injection test** — validate packet forge → SPI TX → RF against the real E8/TinyHawk pair
7. **Replay canary validation** — test the fail-closed replay canary against live RF to confirm detection thresholds

### Research Extensions

8. **MSP-over-ELRS exploitation PoCs** — the attack surface was mapped in PR #34; build concrete exploit tools for MSP command injection over the ELRS link
9. **Combined attack demo** — full multi-vector sequence: RF disruption → WiFi mode entry → config extraction → forged packet injection
10. **Defense and detection tools** — ELRS anomaly detection, MAVLink signing enforcement, and monitoring tools to round out the project with defensive capabilities

---

## Summary Statistics

| Metric | Value |
|---|---|
| PRs merged to main | 20+ |
| PRs open (ready to merge) | 3 (#40, #41, #42) |
| Chain 1 validation | 54 PHY frames, v3 CRC/FHSS verified |
| Integration tests | 90+ |
| Rainbow table phrases | 723 |
| Tools built | ~20 |
| Research docs | 10+ |

## Key Risk

**SX1280 hardware is the critical-path blocker for live RF testing.** Everything in Chain 1 is software-validated, but the jump from "CRC checks pass on captures" to "forged packets accepted by a real receiver" requires transmitting via an SX1280 module. The dev board guide in PR #40 covers sourcing options.
