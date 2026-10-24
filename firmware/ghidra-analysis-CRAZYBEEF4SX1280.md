# Static Firmware Analysis — betaflight_CRAZYBEEF4SX1280.bin

**Target:** EMAX TinyHawk III flight controller firmware
**File:** `firmware/betaflight/betaflight_CRAZYBEEF4SX1280.bin`
**SHA-256:** `d98f2569e4fba71a3c3054fd798c6329c64b7ee4b686384a2a1abc9e28d9ad0e`
**Build version:** Betaflight 2026.6.2 (`CRAZYBEEF4SX1280` unified target)
**Analysis date:** 2026-10-14
**Method:** ARM Cortex-M4 static binary analysis + source cross-reference from `betaflight-2026.6.2` and `ExpressLRS-4.1.0`

---

## 1. Ghidra Load Settings

| Parameter | Value |
|---|---|
| **Processor** | ARM Cortex (little endian) |
| **Variant** | v7 (Cortex-M4F) |
| **Base address** | `0x08000000` (STM32F411 flash start) |
| **Address space** | ROM |
| **File format** | Raw Binary |

After loading, apply the STM32F411 SVD or memory map:

| Region | Start | End | Size | Description |
|---|---|---|---|---|
| **Flash** | `0x08000000` | `0x0806839E` | 416.9 KiB | Firmware code + rodata (512 KiB chip max) |
| **SRAM** | `0x20000000` | `0x2001FFF0` | 128 KiB | Stack/heap/globals (128 KiB total on chip) |
| **Peripherals** | `0x40000000` | `0x50060BFF` | — | APB1/APB2/AHB1/AHB2 |

---

## 2. Vector Table and Entry Points

The STM32F411 vector table at flash base provides the CPU entry points. Key vectors:

| Vector | Address | Function |
|---|---|---|
| **Initial SP** | `0x2001FFF0` | Top of 128 KiB SRAM |
| **Reset** | `0x080556C5` | Firmware entry point (Thumb) |
| **NMI** | `0x0804EC35` | Non-maskable interrupt |
| **HardFault** | `0x0800D6FD` | Hard fault handler |
| **SysTick** | `0x08009D25` | System tick — drives scheduler |
| **SVC** | `0x0804EC39` | Supervisor call |
| **PendSV** | `0x0804EC41` | Context switch support |

Notable IRQ handlers (mapped to STM32F411 peripherals):

| IRQ | Vector Addr | Likely Peripheral |
|---|---|---|
| EXTI9_5 (IRQ 23) | `0x0800B431` | SX1280 DIO1 interrupt (EXTI on PC14) |
| DMA1_Stream0-7 | `0x0800B01D`–`0x0800B0F9` | SPI DMA for sensor/radio/OSD |
| TIM5 (IRQ 50) | `0x0800D1A9` | ELRS timer (packet timing/phase lock) |
| USART1/2 (IRQ 37-38) | `0x08049645`/`0x08049651` | Serial ports |
| SPI3 (IRQ 51) | — | SX1280 radio SPI bus |

---

## 3. Memory Layout — Code Sections

Estimated from string table locations and function density:

| Region | Flash Range | Content |
|---|---|---|
| `0x08000000–0x0800B500` | Core startup, interrupts, DMA handlers |
| `0x0800B500–0x08040000` | Main Betaflight code (PID, mixer, scheduler, sensors) |
| `0x08040000–0x08056000` | ELRS receiver stack + SX1280 driver (~223 functions) |
| `0x08056000–0x0805C000` | CLI, config parameter tables |
| `0x0805C000–0x08060000` | Debug strings (debug mode names, OSD strings) |
| `0x08060000–0x08068000` | Rodata: config parameter names, CLI strings, format strings |
| `0x08068000–0x0806839E` | MSP protocol strings, build info |

---

## 4. ELRS Integration — Key Findings

### 4.1. ELRS Protocol Version: V3 (Not V4)

The build does **not** define `USE_ELRSV4`. This means:

- **OTA_VERSION_ID = 3** (source: `expresslrs_common.h:37`)
- CRC initializer = `(UID[4] << 8) | UID[5]` XOR `0x03` (low byte)
- SYNC packet validation checks `UID[3]` AND `UID[4]` (V4 drops the UID[3] check)
- FHSS sync channel = `(freqCount / 2) + 1` = channel 41 (V4 uses channel 40)

**Air-link implication:** OTA v3 adds a `UID3` field that v4 removes. `UID3`,
`UID4`, and the upper two bits of the transmitted `UID5` field are direct UID
material; model match may XOR the lower six `UID5` bits. The owned pair has
model ID 255, so its XOR is zero and its decoded SYNC exposes UID bytes 3--5
directly. Do not generalize that last property to every v3 link.

### 4.2. Hardcoded Binding UID

**Location in binary:** `0x0805EF1C`
**Value:** `{0x00, 0x01, 0x02, 0x03, 0x04, 0x05}`

This is the well-known binding mode UID used when no binding phrase is configured. When the receiver enters binding mode (no UID set, or bind button pressed):

```c
// expresslrs.c:1017
receiver.UID = BindingUID;  // {0, 1, 2, 3, 4, 5}
```

**Attack implication:** Any drone in binding mode uses this universally known UID. The FHSS sequence, CRC initializer, and sync channel are all derivable from it. A TX broadcasting with this UID can immediately bind to any unbound ELRS receiver.

### 4.3. UID Storage and CRC Initialization

The configured UID is stored in the Betaflight config EEPROM, referenced through the config parameter table:

| Parameter | String Address | Config Table Ref |
|---|---|---|
| `expresslrs_uid` | `0x08062D50` | `0x0805BBD0` |
| `expresslrs_domain` | `0x08062D60` | `0x0805BBDE` |
| `expresslrs_model_id` | `0x08062D74` | `0x0805BBEC` |

CRC initialization (source-confirmed, `expresslrs.c:656–673`):

```
ELRS V3 (this build):
  crcInitializer = (UID[4] << 8) | UID[5]
  crcInitializer ^= 3           // OTA_VERSION_ID = 3
  
  For binding mode:
  crcInitializer = 0            // No UID XOR in binding mode
```

### 4.4. FHSS Configuration (Binary-Confirmed)

Two identical FHSS domain entries found at `0x0805EF00` (ISM2400 and CE2400):

| Field | Address | Value | Meaning |
|---|---|---|---|
| `freqStart` | `0x0805EF00` | `0x00B8A56A` | SX1280 reg for 2400.4 MHz |
| `freqStop` | `0x0805EF04` | `0x00BEB91B` | SX1280 reg for 2479.4 MHz |
| `freqCount` | `0x0805EF08` | `80` | 80 channels across the band |
| Default UID | `0x0805EF1C` | `{0,1,2,3,4,5}` | Binding mode UID |

FHSS sequence derivation (source-confirmed, `expresslrs_common.c:193–230`):

```
Seed = (UID[2] << 24) | (UID[3] << 16) | (UID[4] << 8) | (UID[5] ^ 3)
Sequence = 240 entries for the 80-channel domain (three complete blocks), shuffled with the seed-derived PRNG
Sync channel = channel 41 (V3) at every 80th entry
Channel freq = freqStart + (channel * spread / scale)
```

### 4.5. CRC14 Implementation

The CRC14 polynomial `0x2E57` was not found as a literal constant in the binary. Instead, a 256-entry lookup table is generated at runtime by `generateCrc14Table()` (called from `expressLrsSpiInit`). The CRC16 polynomial `0x3D65` appears at `0x08040C36` in code and extensively in the rodata section (likely for MSP/config checksums, not OTA).

CRC validation occurs in `validatePacketCrcStd()`:

```c
// V3 path:
if (packet type == RC_DATA && switchMode == WIDE):
    crcHigh = (nonceRX % fhssHopInterval) + 1
else:
    crcHigh = 0
nonceValidator = 0  // V3: nonce NOT mixed into CRC for non-sync
calculatedCRC = calcCrc14(packet, 7, crcInitializer ^ nonceValidator)
```

**Attack implication:** In ELRS V3, the CRC nonce validation is **not applied** — `nonceValidator` is always 0. The CRC depends only on the static `crcInitializer` derived from UID[4:5]. Once you know the UID, every packet's CRC is predictable without tracking the nonce counter.

---

## 5. Packet Handler Analysis

### 5.1. processRFPacket() — Main Entry Point

**Estimated binary location:** Near `0x0805D7CA` (references "CRCERR" debug string)

The packet processing flow:

```
1. validatePacketCrcStd() — CRC14 check using crcInitializer
   └─ FAIL → return RX_SPI_RECEIVED_NONE (no state change)
   
2. Switch on packet type:
   ├─ ELRS_RC_DATA_PACKET (0):
   │   └─ Only processed if CONNECTED + modelMatch
   │   └─ memcpy payload → RC data buffer (no further validation)
   │
   ├─ ELRS_MSP_DATA_PACKET (1):
   │   └─ processRFMspPacket() — MSP-over-ELRS injection point
   │
   ├─ ELRS_SYNC_PACKET (3):
   │   └─ processRFSyncPacket() — UID match, rate/FHSS sync
   │
   └─ default: return NONE

3. Update LQ/RSSI stats
4. Set rfModeCycleMultiplier = SLOW (extend sync window)
5. If sync → start timer
```

### 5.2. processRFSyncPacket() — Sync/Binding Validation

**Source:** `expresslrs.c:739–789`

Sync packet validation sequence:

```
1. Check UID[3] == packet.UID3  ← V3 only, V4 skips this
2. Check UID[4] == packet.UID4  ← plaintext in SYNC packet
3. Check UID[5] (masked) == packet.UID5 (masked)
   └─ Mask = ~ELRS_MODELMATCH_MASK = ~0x3F = 0xC0
   └─ Only top 2 bits of UID[5] checked for initial connection
   └─ Bottom 6 bits carry model ID XOR

4. On match → tentativeConnection()
   └─ Sets ELRS_TENTATIVE state
   └─ Syncs fhssIndex and nonce from packet
```

**Attack implication:** The model match mask `0x3F` means only 2 bits of UID[5] are validated for initial sync. The remaining 6 bits carry `(~modelId) & 0x3F`. Default `modelId = 0xFF`, so `modelXor = 0x00` — no model match protection unless explicitly configured.

### 5.3. MSP-over-ELRS — Command Injection Surface

`processRFMspPacket()` at source line 697 processes MSP commands received over the air. Once connected and CRC-validated, MSP packets are deserialized and forwarded to the Betaflight MSP handler. This enables:

- Configuration reads/writes
- Motor test commands (requires arming)
- Reboot commands
- Firmware update initiation (DFU mode)
- Binding phrase modification via `expresslrs_uid` parameter

**Attack implication:** An attacker who can forge valid ELRS packets can send arbitrary MSP commands to the flight controller. This includes writing a new UID (rebinding to attacker's phrase) and triggering motor commands.

---

## 6. Failsafe Behavior

### 6.1. Default Configuration (Binary-Confirmed)

From `failsafe.c:80–90` (defaults compiled into the binary):

| Parameter | Default | Description |
|---|---|---|
| `failsafe_throttle` | 1000 | Throttle value during failsafe (minimum/off) |
| `failsafe_delay` | 15 | 1.5 sec before Stage 2 activates |
| `failsafe_landing_time` | 60 | 60 sec max landing phase |
| `failsafe_procedure` | `DROP_IT` (1) | Default = cut motors immediately |
| `failsafe_recovery_delay` | 5 | 500ms of valid data to recover |
| `failsafe_switch_mode` | `STAGE1` | Failsafe switch = same as signal loss |
| `failsafe_stick_threshold` | 30 | 30% stick to exit GPS rescue |

### 6.2. Failsafe State Machine

Strings at `0x08065054` ("-- FAILSAFE --") and `0x08064828` ("failsafePhase") confirm the failsafe module is compiled in. The state machine:

```
Signal received continuously
         │
    Signal lost
         │
         ▼
  ┌─────────────┐
  │ STAGE 1      │  Duration: failsafe_delay (1.5s default)
  │ Hold last    │  RX channels hold last good values
  │ good values  │  Pilot can recover if signal returns
  └──────┬───────┘
         │ Timer expires
         ▼
  ┌─────────────┐
  │ STAGE 2      │  Procedure depends on failsafe_procedure:
  │              │  0: AUTO-LAND — gradual throttle reduction
  │  DROP_IT ←── │  1: DROP — motors off immediately (DEFAULT)
  │              │  2: GPS-RESCUE — fly home (requires GPS)
  └──────┬───────┘
         │ Recovery: 500ms valid data
         ▼
  ┌─────────────┐
  │ RECOVERED    │  Normal operation resumes
  └─────────────┘
```

### 6.3. ELRS Connection Loss Detection

From `handleConnectionStateUpdate()` (source line 1040–1090):

```
TENTATIVE state:
  └─ Timeout after rfPerfParams->rxLockTimeoutMs → lostConnection()

CONNECTED state:
  └─ No valid packet for rfPerfParams->disconnectTimeoutMs → lostConnection()

CONNECTED + high offset + low LQ:
  └─ "Chaos territory" → lostConnection() (resync needed)
```

`lostConnection()` resets all ELRS state, stops the timer, and triggers RF mode cycling (scanning all rates for a new sync). This feeds into the Betaflight failsafe chain above.

**Attack implication:** The failsafe delay of 1.5 seconds is the window between "TX signal lost" and "motors cut." An attacker performing a takeover has 1.5 seconds where the drone holds position before failsafe kicks in if the legitimate TX is jammed. During a smooth takeover (higher power, same UID), the RX simply locks to the attacker's TX without triggering failsafe at all.

---

## 7. SX1280 Radio Configuration

### 7.1. Pin Mapping (from config + binary)

| Function | Pin | SPI Bus |
|---|---|---|
| SX1280 CS | PA15 | SPI3 |
| SX1280 EXTI (DIO1) | PC14 | — |
| SX1280 RESET | PA8 | — |
| SX1280 BUSY | PA13 | — |
| SPI3_SCK | PB03 | — |
| SPI3_MISO | PB04 | — |
| SPI3_MOSI | PB05 | — |

### 7.2. Radio Parameters

- **Domain:** ISM2400 (2.4 GHz ISM band)
- **Modulation:** LoRa or FLRC depending on air rate
- **Rates supported:** 50 Hz, 100 Hz, 150 Hz, 200 Hz, 250 Hz, 500 Hz (LoRa), 500 Hz, 1000 Hz (FLRC)
- **Rate interval constants confirmed in binary:** 2000µs (500Hz), 4000µs (250Hz), 20000µs (50Hz)
- **TX power levels** (from source): 0, 10, 25, 50, 100, 250, 500, 1000, 2000 mW

---

## 8. No Encryption — Confirmed

The string search for crypto-related terms (`md5`, `hash`, `key`, `encrypt`, `secret`, `auth`, `crypt`) returned **zero matches** in the binary. This confirms:

- No encryption on OTA packets
- No authentication beyond UID-based CRC
- No key derivation or key exchange in the firmware
- The binding phrase hash (MD5) is computed at build time, not at runtime on the receiver

The only shared identity material is the six-byte UID derived from the binding
phrase. A v3 SYNC carries fields corresponding to UID[3], UID[4], and UID[5],
with the lower six bits of the last field subject to model-match XOR. For this
model-ID-255 pair that XOR is zero.

---

## 9. Attack-Relevant Findings Summary

### Critical

| Finding | Impact | Binary Evidence |
|---|---|---|
| **No encryption on OTA** | All packets forgeable with known UID | No crypto strings in binary |
| **Version-dependent UID fields in SYNC** | v3 carries UID3/UID4/UID5; UID5's lower six bits may be model-match-masked. This pair's model ID 255 makes all UID[3:5] direct | SYNC struct, v3 source, live config, and passive decode |
| **CRC14 depends only on UID** | V3 doesn't mix nonce; static CRC init | `nonceValidator = 0` in V3 path |
| **Hardcoded binding UID** | `{0,1,2,3,4,5}` for all binding mode | At `0x0805EF1C` |
| **Failsafe = DROP (motors off)** | Signal loss → crash in 1.5s | Default config at `failsafe_procedure = 1` |

### Notable

| Finding | Detail |
|---|---|
| **MSP over ELRS** | Full MSP command injection once connected |
| **Model match disabled by default** | `modelId = 0xFF` → `modelXor = 0x00` |
| **No firmware signing** | Config + UID writable via MSP |
| **FHSS sequence fully deterministic** | Seed from UID[2:5], Fisher-Yates shuffle with known PRNG |
| **V3 sync channel = 41** | Fixed listen point for passive SYNC capture |

### Recommended Next Steps

1. **Completed: passively decode v3 SYNC** — 16 CRC-valid SYNC frames from the owned pair confirmed the source layout on channel 41
2. **Use the rainbow table only with unmasked bytes** — this pair's model ID 255 makes UID[3:5] suitable; generic v3 captures require model-match or CRC resolution first
3. **Build ELRS TX emulator** — Forge RC_DATA packets with known UID/CRC to inject stick commands
4. **Test failsafe trigger** — Jam the legitimate TX and measure the 1.5s failsafe window
5. **MSP injection PoC** — Send MSP_SET_MOTOR or MSP_SET_RAW_RC over forged ELRS packets
6. **Ghidra deep dive** — Load the binary with the settings above and trace the exact packet handler at `~0x0805D7CA` for any runtime-only validation not visible in source

---

## 10. Ghidra Quick-Start Bookmarks

When loading in Ghidra, create bookmarks at these addresses:

| Address | Label | Notes |
|---|---|---|
| `0x080556C5` | `Reset_Handler` | Entry point |
| `0x08009D25` | `SysTick_Handler` | Scheduler tick |
| `0x0805EF00` | `fhssConfigs_ISM2400` | FHSS freq table |
| `0x0805EF1C` | `BindingUID` | `{0,1,2,3,4,5}` |
| `0x0805BBD0` | `elrs_config_params` | UID/domain/modelId config |
| `0x0805D7CA` | `near_processRFPacket` | Packet handler (find via CRCERR xref) |
| `0x08062D50` | `str_expresslrs_uid` | Config param string |
| `0x08064590` | `str_DeviceUID` | Device UID display string |
| `0x0806496C` | `str_ELRS` | Protocol identifier |
| `0x08065054` | `str_FAILSAFE` | Failsafe debug string |
| `0x0806601A` | `str_Binding` | "Binding..." OSD string |
