# Betaflight 4.4.2 MSP-over-ELRS Attack Surface Analysis

**Analyst:** Claude Code (thread), 2026-10-16
**Target:** EMAX TinyHawk III Plus (Betaflight 4.4.2, `CRAZYBEEF4SX1280`, STM32F411)
**Source:** `firmware/src/betaflight-4.4.2.tar.gz` (SHA-256: `195b0c6eb66741f6be64f30c63e6a41cc0cf2e990505508f1f7cc270de477c29`)
**Evidence class:** SOURCE-DERIVED. All findings are from the pinned 4.4.2 source. No live exploit was run. The installed binary has not been verified byte-identical to this archive.

---

## Executive Summary

The Betaflight 4.4.2 MSP command handler (`msp.c`) is reachable over the air via
MSP-over-ELRS with **no additional privilege separation** versus the serial/USB MSP
interface. Once an attacker establishes a CRC-valid, connected, model-matched ELRS
session, the full MSP command set is available. Several high-impact commands lack
arming-state checks, enabling attacks against both armed (flying) and disarmed drones.

**Pre-conditions for all attacks:** attacker knows the 6-byte UID (or target is in
bind mode using `{0,1,2,3,4,5}`), can generate CRC14-valid packets, and can achieve
`ELRS_CONNECTED` + `connectionHasModelMatch` state.

---

## 1. Attack Path: Air Packet to MSP Command Execution

```
SX1280 DMA buffer (8 bytes, fixed)
  → processRFPacket()              expresslrs.c:729    CRC14 validation
    → case ELRS_MSP_DATA_PACKET:
      → processRFMspPacket()       expresslrs.c:625    requires ELRS_CONNECTED
        → receiveMspData()         expresslrs_telemetry.c:231   accumulates into mspBuffer[65]
          → hasFinishedMspData()
            → processMspPacket()   expresslrs_telemetry.c:285
              → handleCrsfMspFrameBuffer()  crsf.c
                → handleMspFrame()          msp_shared.c:176    MSPv1/v2 parse
                  → mspFcProcessCommand()   msp.c               FULL MSP HANDLER
```

**Key observation:** `mspFcProcessCommand()` is the same function called by serial MSP
(USB/UART). There is no source-descriptor check, no "this came from radio" flag, and no
reduced command set for over-the-air MSP. Every MSP command available over USB is
available over the ELRS radio link.

### 1.1 Gating Analysis

| Gate | Location | Bypass |
|---|---|---|
| CRC14 | `validatePacketCrcStd()` expresslrs.c:715 | Requires UID-derived `crcInitializer = (UID[4]<<8\|UID[5]) ^ 3`. Bind-mode UID `{0,1,2,3,4,5}` → init = `0x0401 ^ 3 = 0x0402`. |
| `crcHigh` field | OTA packet header, 6 bits | For v3 non-WIDE non-RC packets: `crcHigh = 0` (expresslrs.c:715 — `ValidatePacketCrcStd` sets `OtaCrcHigh = 0` for these types). Not mixed with nonce. |
| Connection state | `connectionState == ELRS_CONNECTED` | Achieved by valid, version-matched SYNC packets. v3 carries UID3/UID4/UID5 fields, but generic model match may XOR UID5's lower six bits. The model-ID-255 lab pair has zero XOR. |
| Model match | `connectionHasModelMatch` | Default `modelId = 0xFF` → `modelXor = 0x00` after `(~modelId) & ELRS_MODELMATCH_MASK` = `0x00 & 0x3F = 0x00`. Only 6 bits validated in SYNC. |
| MSP buffer bounds | `receiveMspData()` | Bounded by `ELRS_MSP_BUFFER_SIZE=65` and `ELRS_MSP_BYTES_PER_CALL=5`. No overflow, but no replay/sequence protection beyond `packageIndex` matching. |

**No encryption at any layer.** The CRC14 is the sole cryptographic-equivalent gate,
and it depends entirely on the 6-byte UID which is partially observable from SYNC
packets and fully derivable from a known binding phrase.

---

## 2. Critical MSP Commands (No Arming Check)

These commands are accepted regardless of arming state. Source references are to
`src/main/msp/msp.c` in the 4.4.2 archive.

### 2.1 MSP_SET_MOTOR (command 214) — Motor Output While Disarmed

**Location:** msp.c, `case MSP_SET_MOTOR:` handler
**Effect:** Writes directly to `motor_disarmed[i]` array. The mixer
(`src/main/flight/mixer.c`) copies `motor_disarmed[i]` into `motor[i]` when the FC
is disarmed, producing physical motor output.
**Arming check:** None. No `ARMING_FLAG(ARMED)` test.
**CTF impact:** Spin motors on a grounded drone without ever arming it. Physical hazard.

### 2.2 MSP_SET_ARMING_DISABLED (command 99) — Mid-Flight Forced Disarm

**Location:** msp.c, `case MSP_SET_ARMING_DISABLED:` handler
**Effect:** When `command == 1` and `ARMING_FLAG(ARMED)`, calls
`disarm(DISARM_REASON_ARMING_DISABLED)`. This is an **immediate disarm** — the drone
falls.
**Arming check:** The command *checks* arming state to decide whether to disarm, but
does not *reject* the command based on arming state. It is designed to work while armed.
**CTF impact:** Drop a flying drone from the sky. Highest physical impact.

### 2.3 MSP_SET_RX_CONFIG (command 45) — Receiver UID Rewrite / Rebind

**Location:** msp.c, `case MSP_SET_RX_CONFIG:`, around line 3697
**Effect:** `sbufReadData(src, rxExpressLrsSpiConfigMutable()->UID, 6)` — writes a
new 6-byte UID directly from the MSP payload into the receiver's ELRS configuration.
This effectively **rebinds the receiver to an attacker-controlled UID**.
**Arming check:** None.
**CTF impact:** Permanent hijack. After UID rewrite + save, the legitimate TX can no
longer connect. The attacker's TX (using the new UID) has exclusive control.

### 2.4 MSP_SET_FAILSAFE_CONFIG (command 76) — Failsafe Behavior Manipulation

**Location:** msp.c, `case MSP_SET_FAILSAFE_CONFIG:` handler
**Effect:** Writes `failsafe_delay`, `failsafe_off_delay`, `failsafe_throttle`,
`failsafe_switch_mode`, `failsafe_throttle_low_delay`, `failsafe_procedure`.
**Arming check:** None.
**CTF impact:** Change failsafe from `DROP` (motors off) to `LAND` with high throttle,
or set extremely long failsafe delay so the drone flies uncontrolled for longer after
signal loss. Can be combined with jamming.

### 2.5 MSP_SET_RAW_GPS (command 201) — GPS Position Injection

**Location:** msp.c, `case MSP_SET_RAW_GPS:` handler
**Effect:** Writes directly to `gpsSol` struct: `fixType`, `numSat`, `coord.lat`,
`coord.lon`, `coord.alt`, `groundSpeed`, `groundCourse`, `hdop`.
**Arming check:** None.
**CTF impact:** GPS spoofing without SDR. If the drone uses GPS-based navigation or
return-to-home, injected coordinates redirect it. Lower priority for TinyHawk III
(primarily acro/manual) but relevant if GPS is fitted.

### 2.6 MSP_SET_FEATURE_CONFIG (command 37) — Feature Flag Manipulation

**Location:** msp.c, `case MSP_SET_FEATURE_CONFIG:` handler
**Effect:** `featureConfigReplace(sbufReadU32(src))` — replaces the entire feature
bitmask. Can disable telemetry, GPS, failsafe features, or enable unexpected features.
**Arming check:** None.
**CTF impact:** Disable safety features before a follow-up attack.

---

## 3. MSP Commands WITH Arming Checks (Bypassable via Chaining)

### 3.1 MSP Reboot Commands

**Location:** msp.c, `mspReboot()` at line 394
**Check:** Returns without action if `ARMING_FLAG(ARMED)`.
**Bypass:** Send `MSP_SET_ARMING_DISABLED` first (§2.2) to disarm, then send reboot.
**Available reboot targets:**
- `MSP_REBOOT_FIRMWARE` — normal reboot
- `MSP_REBOOT_BOOTLOADER_ROM` — reboot to ROM bootloader (DFU entry)
- `MSP_REBOOT_BOOTLOADER_FLASH` — reboot to flash bootloader
- `MSP_REBOOT_MSC` / `MSP_REBOOT_MSC_UTC` — mass storage mode

**CTF impact:** Force DFU entry enables firmware replacement (if physical access or
continued radio access exists). Reboot itself causes immediate loss of control.

### 3.2 MSP_EEPROM_WRITE (command 250)

**Check:** Rejects if `ARMING_FLAG(ARMED)`.
**Bypass:** Chain after disarm.
**CTF impact:** Persist configuration changes (UID rewrite, failsafe changes) across
power cycles.

---

## 4. Attack Scenarios for CTF

### Scenario A: "Drop the Drone" (Fastest, Highest Impact)

```
1. Obtain UID (bind-mode {0,1,2,3,4,5} or recovered from air)
2. Achieve ELRS_CONNECTED + model match
3. Send MSP_SET_ARMING_DISABLED (command=1) → immediate disarm
Result: drone falls from sky
```

### Scenario B: "Hijack the Drone" (Persistent Takeover)

```
1. Achieve connection (as above)
2. Send MSP_SET_ARMING_DISABLED → disarm if flying
3. Send MSP_SET_RX_CONFIG with attacker's UID
4. Send MSP_EEPROM_WRITE → persist new UID
5. Send MSP reboot → reconnect with attacker's TX
Result: legitimate TX permanently locked out, attacker has full control
```

### Scenario C: "Sabotage Failsafe" (Delayed Effect)

```
1. Achieve connection
2. Send MSP_SET_FAILSAFE_CONFIG: procedure=LAND, throttle=high, delay=maximum
3. Send MSP_EEPROM_WRITE → persist
4. Later: jam the legitimate TX signal
Result: drone climbs at high throttle instead of dropping safely
```

### Scenario D: "Ghost Motor" (Disarmed Motor Spin)

```
1. Achieve connection
2. Send MSP_SET_MOTOR with nonzero values
Result: motors spin on grounded, disarmed drone (physical hazard / score if CTF counts it)
```

---

## 5. Protocol-Level Weaknesses Enabling These Attacks

### 5.1 No Replay Protection

`receiveMspData()` tracks `packageIndex` for reassembly ordering, but there is no
nonce, timestamp, or sequence counter that prevents replaying a captured valid MSP
command. If an attacker captures a legitimate MSP-over-ELRS exchange, the same bytes
can be retransmitted.

### 5.2 CRC14 is Not Authentication

The CRC14 with a UID-derived initializer provides integrity (bit-error detection) and a
weak form of access control (must know UID to generate valid CRC). It is **not**
cryptographic authentication:
- 14-bit CRC space = 16,384 possible values. Brute-force at radio rate is feasible.
- The initializer depends only on the actual UID[4] and UID[5] (16 bits).
  UID4 is direct in v3 SYNC; UID5's lower six transmitted bits can be
  model-match-masked and require known model state or CRC resolution. The
  owned model-ID-255 pair has zero mask.
- No HMAC, no encryption, no session keys.

### 5.3 SYNC Packet Leaks UID Bytes

Every v3 SYNC packet contains `UID3`, `UID4`, and `UID5` fields
(`expresslrs_common.h`; processed at `expresslrs.c:662`). UID3, UID4, and the
upper two UID5 bits are direct; model match may XOR the lower six UID5 bits.
For this model-ID-255 pair the XOR is zero. Combined with FHSS sequence
observation (which can resolve the remaining seed byte), the air-link UID
material is recoverable from passive
observation.

### 5.4 Binding Mode Uses Known UID

`BindingUID[6] = {0, 1, 2, 3, 4, 5}` (expresslrs.c:74). Any drone in binding mode
accepts packets with this universally known UID. The FHSS sequence, CRC initializer,
and sync channel are all deterministic from it.

### 5.5 Model Match is 6 Bits

The model match uses only `(~modelId) & 0x3F` — 6 bits, 64 possible values. Default
`modelId = 0xFF` yields `modelXor = 0x00`. Even for non-default values, 64 attempts
covers the space.

---

## 6. Comparison: Serial MSP vs Over-the-Air MSP

| Property | Serial (USB/UART) | MSP-over-ELRS |
|---|---|---|
| Physical access required | Yes | No (wireless) |
| Authentication | None (physical = trust) | CRC14 + UID (weak) |
| Command set | Full | **Full (identical handler)** |
| Privilege separation | None | **None** |
| Buffer size | 192 bytes (MSP_PORT_INBUF_SIZE) | 65 bytes (ELRS_MSP_BUFFER_SIZE) |
| Multi-chunk support | Yes (MSPv1_Jumbo, MSPv2) | Yes (via CRSF fragmentation) |

The 65-byte ELRS MSP buffer is smaller than the 192-byte serial buffer, which limits
single-command payload size. However, all critical commands identified above fit within
65 bytes. The MSPv2 header is 9 bytes; leaving 56 bytes for payload is sufficient for
`MSP_SET_MOTOR` (16 bytes), `MSP_SET_ARMING_DISABLED` (2 bytes), `MSP_SET_RX_CONFIG`
(variable but UID write fits), and reboot commands (1 byte).

---

## 7. What This Analysis Did NOT Do

- **No live testing.** No MSP command was sent to any device. No motor was actuated.
  No RF was transmitted. This is source-level analysis only.
- **No binary verification.** The installed firmware has not been read back and compared
  to the source archive. Findings assume the source matches the running firmware.
- **No OTA packet construction.** The actual ELRS OTA framing, FHSS timing, and SX1280
  modulation needed to deliver these commands over the air is a separate PHY-layer
  problem (see `research/tinyhawk-paired-phy-analysis-2026-10-16.md`).
- **No buffer overflow search.** The MSP handler's bounds checking was not exhaustively
  audited for every command. The focus was on command-level semantics, not memory safety.

---

## 8. Relationship to Existing Analysis

- **`firmware/vuln-analysis-CRAZYBEEF4SX1280.md`**: Covers the 2026.6.2 binary/source.
  Confirmed MSP-over-ELRS as the viable injection surface and ruled out RC-data overflow.
  This document extends that to the **actual running 4.4.2 firmware** with specific
  dangerous-command enumeration.
- **`research/betaflight-442-elrs-msp-motor-path-2026-10-16.md`**: Covers
  `MSP_SET_MOTOR` specifically. This document adds the full dangerous-command set
  (§2.1–2.6, §3) and attack scenarios (§4).
- **`firmware/ghidra-analysis-CRAZYBEEF4SX1280.md`**: Binary-level Ghidra analysis of
  the 2026.6.2 build. Memory layout, vector table, binding UID location.

---

## 9. Recommended Next Steps

1. **Host-only unit test**: Mock `mspFcProcessCommand()` with crafted MSP payloads
   delivered through the `receiveMspData()` → `processMspPacket()` path. Verify the
   command dispatch and motor-array/config writes in a test harness without hardware.
2. **OTA packet construction**: Build valid ELRS v3 MSP data packets with correct CRC14
   for the bind-mode UID. Verify framing against the `elrsOtaPacket_t` struct.
3. **PHY-layer delivery**: Requires SX1280 LoRa modulation at the correct parameters
   (SF5/SF6, BW800/BW1600, long interleaver). This is the unsolved piece — see
   ongoing PHY analysis.
4. **Bench validation** (requires owner authorization): Props-off, isolated test with
   serial MSP monitoring to confirm command acceptance. The project safety gate
   prohibits RF transmission and motor actuation without explicit approval.
