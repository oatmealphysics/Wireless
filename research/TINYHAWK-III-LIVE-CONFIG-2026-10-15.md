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

## Full ELRS driver diff: 4.4.2 vs 2026.6.2 (source-derived, 2026-10-15)

Complete comparison of all `src/main/rx/expresslrs*` files between the installed
Betaflight 4.4.2 and the corpus 2026.6.2. Changes totalling ~550 lines across
7 files. Only security-relevant and attack-chain-relevant differences are
recorded here; whitespace normalization and code style changes are omitted.

### 1. Rate table restructuring (expresslrs_common.c)

**4.4.2 (installed):** 4 entries for SX1280, all LoRa:
- Index 0: LoRa 500Hz (SF5, BW800)
- Index 1: LoRa 250Hz (SF6, BW800)
- Index 2: LoRa 150Hz (SF7, BW800)
- Index 3: LoRa 50Hz (SF8, BW800)

**2026.6.2 (corpus):** 6 entries — 2 FLRC + 4 LoRa:
- Index 0: FLRC 1000Hz
- Index 1: FLRC 500Hz
- Index 2–5: LoRa (same as 4.4.2 index 0–3)

**Attack impact:** The live `expresslrs_rate_index = 2` maps to **LoRa 150Hz
(SF7, BW800, CR_LI_4_7)** on 4.4.2. On 2026.6.2, index 2 is LoRa 500Hz.
The rate table index is not portable between versions — the FHSS hop interval
(6666 μs for 4.4.2 rate 2) is version-dependent. The SDR demod and TX emulator
must use the 4.4.2 rate table, not the 2026.6.2 one.

### 2. CRC initializer computation (expresslrs.c)

**4.4.2:** Inline in `expressLrsSpiInit()` and `unpackBindPacket()`:
```c
crcInitializer = (receiver.UID[4] << 8) | receiver.UID[5];
crcInitializer ^= ELRS_OTA_VERSION_ID;  // XOR to low byte (v3: XOR 3)
```

**2026.6.2:** Extracted to `OtaUpdateCrcInitFromUid()` with v3/v4 branching:
- v3: `crcInitializer ^= ELRS_OTA_VERSION_ID` (same as 4.4.2)
- v4: `crcInitializer ^= (uint16_t)ELRS_OTA_VERSION_ID << 8` (shifted to high byte)
- Binding: v3 uses `crcInitializer = 0`, v4 uses `crcInitializer = ELRS_OTA_VERSION_ID`

**Attack impact:** CRC computation is identical for v3. The `elrs_core` library's
v3 CRC init formula is confirmed: `((UID[4] << 8) | UID[5]) ^ 3`.

### 3. FHSS seed computation (expresslrs_common.c)

**4.4.2:** Inline in `fhssGenSequence()`:
```c
seed = (((long)UID[2] << 24) + ((long)UID[3] << 16) + ((long)UID[4] << 8) + UID[5]) ^ ELRS_OTA_VERSION_ID;
```

**2026.6.2:** Extracted to `elrsUidToSeed()`:
```c
return ((uint32_t)UID[2] << 24) + ((uint32_t)UID[3] << 16) +
    ((uint32_t)UID[4] << 8) + (UID[5] ^ ELRS_OTA_VERSION_ID);
```

**Subtle difference:** In 4.4.2, the XOR applies to the full 32-bit seed. In
2026.6.2, the XOR applies only to UID[5] (the low byte) before assembly. For
`ELRS_OTA_VERSION_ID = 3`, the result is identical because `3 ^ seed` and
`seed_with_uid5_xored` produce the same low byte, and the XOR of 3 with the
upper bytes is always zero. This equivalence holds only because the version ID
fits in one byte. **No attack impact for v3.**

### 4. Sync channel offset (expresslrs_common.c)

**4.4.2:** `syncChannel = (fhssConfig->freqCount / 2) + 1;`
**2026.6.2 (v3):** Same formula: `(freqCount / 2) + 1`
**2026.6.2 (v4):** `(freqCount / 2)` (no +1)

**Attack impact:** Sync channel for 2.4 GHz is channel 41 (80/2 + 1) on v3.
The v3 sync channel is index 41 (approximately 2441.4 MHz): the ISM2400
table starts at 2400.4 MHz and has 1 MHz steps. 2440.4 MHz is index 40,
the v4 sync channel. The earlier 2440.4 MHz value attached to v3 channel 41
was an error in our documentation; it should not be treated as a measured
center frequency for the live link.

### 5. CRC validation with nonce (expresslrs.c)

**4.4.2:** `calcCrc14((uint8_t *) otaPktPtr, 7, crcInitializer)` — nonce not
included in CRC init for standard packets.

**2026.6.2 (v3):** Same — `nonceValidator = 0`, so CRC init is unchanged.
**2026.6.2 (v4):** `crcInitializer ^ receiver.nonceRX` for non-SYNC packets.

**Attack impact:** v3 CRC does not include the nonce. Forged packets only need
the static CRC initializer derived from the UID. This simplifies the injection
attack — no need to track the nonce for CRC purposes (though it is still needed
for FHSS hop tracking).

### 6. SYNC packet UID verification (expresslrs.c)

**4.4.2:** `otaPktPtr->sync.UID3 != receiver.UID[3] || otaPktPtr->sync.UID4 != receiver.UID[4]`
**2026.6.2 (v3):** Same check.
**2026.6.2 (v4):** Only checks `UID4`, drops `UID3` check.

**Attack impact:** None for v3. The 4.4.2 SYNC packet exposes UID[4] and UID[5]
(the SYNC struct fields `UID4` and `UID5` in `OTA.h`), and the receiver validates
UID[3] and UID[4] against its stored UID. A passive listener gets 16 bits (UID[4:5])
from every SYNC packet.

### 7. MSP handling differences (expresslrs.c)

**4.4.2 `processRFMspPacket()`:** After `receiveMspData()`, checks if
`getCurrentMspConfirm()` value flipped and if so sets `nextTelemetryType =
ELRS_TELEMETRY_TYPE_LINK` to send the new confirm bit back.

**2026.6.2 (v3):** Same behavior.
**2026.6.2 (v4):** The confirm-bit-flipped telemetry logic is removed (v4 has a
different confirm mechanism).

**Attack impact:** None for v3. The MSP receive path is functionally identical.
The 65-byte buffer, 5-byte chunking, `processRFMspPacket()` → `processMspPacket()`
→ `handleCrsfMspFrameBuffer()` → `mspFcProcessCommand()` chain is unchanged
between versions. The MSP-over-ELRS attack surface is the same on both.

### 8. Switch mode handling (expresslrs.c)

**4.4.2:** Switch mode (`SM_WIDE`/`SM_HYBRID`) read from `rxExpressLrsSpiConfig()->switchMode`
(NVM-stored config). Updated from SYNC packets and persisted to config.

**2026.6.2:** Moved to `receiver.switchMode` (runtime state). No longer persisted
to config on every SYNC receive.

**Attack impact:** Minor. The switch mode determines how RC channel data is packed
in `ELRS_RC_DATA_PACKET` payloads. For injection, the attacker must match the
target's switch mode. The live config shows `HYBRID` mode (the default).

### 9. Telemetry response CRC (expresslrs.c)

**4.4.2:** `calcCrc14((uint8_t *) &otaPkt, 7, crcInitializer)` with no nonce XOR.
**2026.6.2 (v4):** `calcCrc14(..., crcInitializer ^ nonceValidator)` with
`nonceValidator = receiver.nonceRX + offset`.

**Attack impact:** On 4.4.2, both uplink and downlink CRC use the same static
`crcInitializer` with no nonce component. Downlink (telemetry) packets can be
decoded with the same CRC init as uplink — useful for passive interception of
telemetry data.

### Summary: 4.4.2 attack parameters (confirmed from source)

| Parameter | 4.4.2 value | Source |
|---|---|---|
| OTA version | 3 | `ELRS_OTA_VERSION_ID 3` in `expresslrs_common.h` |
| PHY | LoRa only (no FLRC) | Rate table in `expresslrs_common.c` |
| Rate index 2 | LoRa 150Hz, SF7, BW800, CR_LI_4_7, hop interval 6666 μs | Same |
| CRC init | `((UID[4] << 8) | UID[5]) ^ 3` | `expressLrsSpiInit()` |
| CRC nonce XOR | None (v3) | `validatePacketCrcStd()` — `nonceValidator = 0` |
| FHSS seed | `(UID[2]<<24 + UID[3]<<16 + UID[4]<<8 + UID[5]) ^ 3` | `fhssGenSequence()` |
| Sync channel | `(freqCount / 2) + 1` = channel 41 for 2.4 GHz | `fhssGenSequence()` |
| SYNC UID leak | UID[4] and UID[5] in every SYNC packet | `OTA_Sync_s` struct |
| MSP buffer | 65 bytes, 5-byte chunks | `ELRS_MSP_BUFFER_SIZE`, `processRFMspPacket()` |
| MSP reachable | Yes, even with `feature -TELEMETRY` | See MSP reachability review above |
| Model match | `model_id = 255` → XOR = 0x00 → always satisfied | Live CLI observation |
| Switch mode | HYBRID | Live CLI observation |
