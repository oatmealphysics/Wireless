# TinyHawk III Plus — Live Betaflight Identification and Configuration Summary

**Evidence class:** LIVE OBSERVATION (read-only Betaflight CLI transcript)

**Observed:** 2026-10-15 from the owner’s connected flight controller. This is
the current lab test article, not an assertion about an eventual CTF target.
The raw CLI transcript is intentionally not committed: it contains the ELRS UID,
which is pairing-sensitive configuration. Preserve any raw capture only in an
ignored private `work/` directory.

## Firmware identity

| Field | Observed value |
|---|---|
| Firmware | Betaflight 4.4.2 |
| Build timestamp | 2023-11-30 01:55:56 |
| Revision | `23d066d08` |
| MCU / target family | STM32F411 (`S411`) |
| MSP API | 1.45 |
| Board name | `EMAX_TINYHAWKF4SX1280` |
| Manufacturer | EMAX |
| Craft name | TH III Plus |

This is a material correction to the existing firmware corpus: the committed
2026.6.2 `CRAZYBEEF4SX1280` image remains a **related sample**, not the image
installed on this controller. Any finding derived from that corpus must retain
its version/build qualifier until checked against Betaflight 4.4.2.

## Observed hardware and receiver configuration

| Area | Observation | Interpretation |
|---|---|---|
| Receiver | `RX_SPI` enabled; `rx_spi_protocol = EXPRESSLRS` | The receiver is integrated SPI ExpressLRS, not an external serial RX. |
| Radio bus | `rx_spi_bus = 3` | Consistent with the target’s SX1280 bus mapping. |
| SX1280 signals | CS `PA15`, EXTI `PC14`, reset `PA8`, busy `PA13` | Matches the board-level resource map reported by the live controller. |
| ELRS domain | `ISM2400` | 2.4 GHz ELRS domain. |
| ELRS rate | `expresslrs_rate_index = 2` | Measure the actual PHY/version from a recorded signal; do not infer it from this field alone. |
| ELRS switch mode | `HYBRID` | Runtime configuration observed. |
| ELRS model ID | `255` | Runtime configuration observed. |
| ELRS UID | Present in the transcript | **Redacted; do not commit or log.** |
| Telemetry feature | `feature -TELEMETRY` | Telemetry is disabled in this live runtime configuration. This does not by itself prove whether a compile-time ELRS/MSP telemetry path exists in the installed binary. |
| Failsafe | `failsafe_procedure = DROP` | Bench testing requires a non-flight-safe setup; unexpected link loss is not benign. |

Other live resource observations include the MPU6000-class SPI gyro on bus 1,
OSD SPI on bus 2, and serial RX/TX resources consistent with the board target.

## CLI behavior observed

The CLI entered normally and produced a large configuration batch after `dump`.
It also printed these messages:

```text
###WARNING: NO CUSTOM DEFAULTS FOUND###
###ERROR IN dump: NO CONFIG FOUND###
###ERROR IN version: NO CONFIG FOUND###
```

These messages are recorded as observations, not as evidence that configuration
is absent: the same session returned board identity, resources, features, and
runtime settings. Do not attempt a reset, reflash, or configuration write to
resolve them. If they matter later, reproduce them read-only and compare the
Configurator/CLI version and connection mode.

## Implications for the research corpus

1. **Prioritize a 4.4.2 evidence lane.** Acquire the official Betaflight 4.4.2
   STM32F411 release artifact, matching 4.4.2 source, and the matching EMAX
   unified target configuration. Record provenance and SHA-256 before analysis.
2. **Do not call the official release image the exact installed binary.** The
   normal Betaflight configurator/CLI identifies the installed build but does
   not read out its raw flash image. An official artifact can be a version- and
   target-matched analysis sample; its hardware match remains qualified unless
   byte identity is independently established.
3. **Re-qualify earlier 2026.6.2 conclusions.** The target name and resource
   layout are corroborated, but build-dependent protocol code, compile flags,
   and vulnerability claims require comparison with 4.4.2 before being treated
   as applicable to this flight controller.
4. **Separate compile-time and runtime facts.** The committed target config
   enables `USE_RX_EXPRESSLRS_TELEMETRY`, while the observed live configuration
   disables the `TELEMETRY` feature. Neither fact alone establishes reachability
   of an MSP-over-air path in the live 4.4.2 binary.

## Version comparison: 4.4.2 vs 2026.6.2 ELRS differences (source-derived)

Diffed `src/main/rx/expresslrs_common.h` between 4.4.2 and 2026.6.2:

| Feature | 4.4.2 (installed) | 2026.6.2 (corpus) |
|---|---|---|
| OTA version | **v3 only** (`ELRS_OTA_VERSION_ID 3`, no `USE_ELRSV4`) | v3 or v4 (conditional `USE_ELRSV4`) |
| Rate max | 4 rates | 6 rates (+ FLRC/FSK) |
| Binding rate | Index 3 | Index 5 |
| FLRC/FSK modes | **Not present** | Present (RATE_FLRC_2G4_*, RATE_FSK_2G4_*) |
| Arm channel | Not v4-aware | v3 channel 4 / v4 channel 13 |

**Impact on the attack chain:**
- CRC init formula is definitively v3: `((UID[4] << 8) | UID[5]) XOR 3`.
  The `version << 8` form belongs to the newer v4 branch; the 4.4.2 source
  applies the v3 version value to the low byte.
- PHY is LoRa only — FLRC is not available on this firmware
- The `rate_index = 2` observed in CLI maps to the 4.4.2 rate table (LoRa)
- The `model_id = 255` (0xFF) means `modelXor = 0x00` — model-match is effectively always satisfied
- Our `elrs_core` vendored library must be parameterized for v3, not v4

## Completed acquisition steps (2026-10-15)

1. Raw CLI transcript saved privately under `work/tinyhawk-live-2026-10-15/`.
2. Betaflight 4.4.2 artifacts acquired and registered:
   - `firmware/betaflight/betaflight_4.4.2_STM32F411.hex` — official release (SHA256 verified)
   - `firmware/betaflight/betaflight_4.4.2_STM32F411.bin` — converted for Ghidra (0x08000000, 455 KB)
   - `firmware/src/betaflight-4.4.2.tar.gz` — version-matched source (18 MB)
3. Source diff confirms OTA v3 only, LoRa only, no FLRC.
4. Controller remains in read-only mode. No flash, reset, or settings write performed.

## Remaining actions

1. Ghidra analysis of the 4.4.2 binary to cross-check the source-derived findings.
2. Cross-check the OTA-v3 tool output against a live RF capture.
3. Validate the source-derived MSP reachability result on the owned bench target.

## 4.4.2 MSP-over-ELRS reachability review (source-derived, 2026-10-15)

The full ELRS RX source comparison requested above is complete. The result is
more precise than the earlier concern: runtime `feature -TELEMETRY` does **not**
gate receipt or dispatch of MSP uplink packets in Betaflight 4.4.2.

### Compile-time path

- `src/main/target/common_pre.h` defines `USE_MSP_OVER_TELEMETRY`.
- `src/main/target/common_post.h` keeps that define whenever at least one of
  SmartPort, CRSF, or GHST telemetry is compiled. For `USE_RX_EXPRESSLRS`, the
  same file explicitly defines `USE_TELEMETRY` and `USE_TELEMETRY_CRSF`.
- Consequently the ExpressLRS receiver contains `mspBuffer[65]`,
  `processRFMspPacket()`, and the CRSF/MSP parser for this source/target family.

### Runtime path with telemetry disabled

`expresslrs_telemetry.c:initTelemetry()` returns immediately when
`FEATURE_TELEMETRY` is disabled. That return skips sensor-list setup and
`mspReceiverResetState()`. It does not disable the RF packet switch or the MSP
receiver:

1. `expresslrs.c:expressLrsSpiInit()` calls
   `setMspDataToReceive(ELRS_MSP_BUFFER_SIZE, mspBuffer)` after
   `initTelemetry()`, even when `initTelemetry()` returned early.
2. `processRFPacket()` dispatches every CRC-valid `ELRS_MSP_DATA_PACKET` to
   `processRFMspPacket()` without checking `FEATURE_TELEMETRY`.
3. `processRFMspPacket()` requires `ELRS_CONNECTED`, assembles the bounded
   five-byte chunks, and calls `processMspPacket(mspBuffer)` when model match
   holds. The live `model_id = 255` makes the model XOR zero.
4. `processMspPacket()` feeds CRSF MSP request/write frames to
   `handleCrsfMspFrameBuffer()`, which reaches `mspFcProcessCommand()`.

The 65-byte buffer size and five-byte chunking are unchanged between 4.4.2 and
2026.6.2. Both versions bound each copy with the remaining buffer length. This
supports the earlier conclusion that the interesting surface is command
injection, not a packet-length memory corruption.

There is one runtime nuance. With `FEATURE_TELEMETRY` disabled, global
`telemetryInit()` does not call `initSharedMsp()`, so the shared MSP descriptor
retains its static initial value (`-1`). The 4.4.2 parser still invokes
`mspFcProcessCommand()` and the special telemetry-origin comparisons see the
same descriptor value. This is source evidence that commands remain reachable,
but only a bench observation can establish the installed binary's behavior and
whether acknowledgements are emitted reliably.

**Finding:** MSP-over-ELRS remains the primary 4.4.2 candidate.
Evidence class: **SOURCE-DERIVED / SELF-CONSISTENT**, pending live validation.
RC-data injection remains the fallback, not the primary path.
