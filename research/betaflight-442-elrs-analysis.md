# Betaflight 4.4.2 ELRS Attack Surface Analysis

**Target:** EMAX TinyHawk III — CRAZYBEEF4SX1280 unified target  
**MCU:** STM32F411CEU6 (ARM Cortex-M4, 512KB flash, 128KB SRAM)  
**Radio:** SX1280 on SPI bus 3 (2.4 GHz LoRa)  
**Firmware:** Betaflight 4.4.2 with SPI-integrated ELRS (no separate ESP)  
**OTA version:** ELRS_OTA_VERSION_ID = 3  
**Analysis type:** Static source-level + binary cross-reference  
**Date:** 2026-10-15  

---

## Table of Contents

1. [Binary Overview](#1-binary-overview)
2. [Hardware Pinout and Peripheral Map](#2-hardware-pinout)
3. [Packet Reception Path (DMA → processRFPacket)](#3-packet-reception-path)
4. [CRC14 Validation](#4-crc14-validation)
5. [FHSS Hopping Sequence](#5-fhss-hopping-sequence)
6. [Connection State Machine](#6-connection-state-machine)
7. [Packet Type Dispatch](#7-packet-type-dispatch)
8. [MSP-over-ELRS Attack Surface](#8-msp-over-elrs)
9. [Failsafe State Machine](#9-failsafe-state-machine)
10. [Exploitable Sinks and Attack Vectors](#10-exploitable-sinks)
11. [Air Rate Configurations](#11-air-rate-configs)
12. [Key Constants and Structures](#12-key-constants)

---

## 1. Binary Overview

**File:** `firmware/betaflight/betaflight_CRAZYBEEF4SX1280.bin`  
**Size:** 426,910 bytes  
**Load address:** 0x08000000 (STM32F411 flash)

### Vector Table (first 16 words)

| Vector | Address | Function |
|--------|---------|----------|
| Initial SP | 0x2001FFF0 | Top of 128KB SRAM |
| Reset | 0x080556C5 | Entry point |
| EXTI15_10 | 0x0800B439 | SX1280 DIO1 interrupt (PC14) |
| TIM5 Global | 0x0800D1A9 | ELRS tick/tock timer ISR |
| DMA1 Stream0 | 0x0800B01D | SPI3 RX DMA complete |
| DMA1 Stream5 | 0x0800B081 | SPI3 TX DMA complete |

### Strings of Interest Found in Binary

- `expresslrs_uid`, `expresslrs_domain`, `expresslrs_rate_index`, `expresslrs_switch_mode`, `expresslrs_model_id` — ELRS configuration parameters
- `failsafe_delay`, `failsafe_off_delay`, `failsafe_throttle_low_delay`, `failsafe_procedure`, `failsafe_recovery_delay` — failsafe timing parameters
- `crsf_flight_mode`, `crsf_link_quality`, `crsf_rssi_dbm` — telemetry strings
- SPI3 peripheral (0x40003C00) referenced at binary offsets 0x0800C168, 0x08058E1C

---

## 2. Hardware Pinout

Source: `EMAX_TINYHAWKF4SX1280.config`

| Function | Pin | Notes |
|----------|-----|-------|
| SX1280 SPI bus | SPI3 | Peripheral at 0x40003C00 |
| SX1280 CS (NSS) | PA15 | Directly toggled by driver |
| SX1280 EXTI (DIO1) | PC14 | Rising edge interrupt → sx1280ISR() |
| SX1280 RESET | PA8 | Active low, used during init |
| SX1280 BUSY | PA13 | Polled before SPI transactions |
| Gyro (MPU6000) | SPI1 | Separate bus, not relevant |
| USB | PA11/PA12 | For Betaflight Configurator |

Key defines: `USE_RX_SPI`, `USE_RX_EXPRESSLRS`, `USE_RX_SX1280`  
Default protocol: `RX_SPI_DEFAULT_PROTOCOL = RX_SPI_EXPRESSLRS`  
Domain: `ISM2400` (80 channels, 2400.4–2479.4 MHz)

---

## 3. Packet Reception Path

The end-to-end path from RF to flight controller:

```
RF packet arrives at SX1280
  → DIO1 pin goes HIGH (packet received)
  → EXTI15_10 ISR fires (vector 0x0800B439)
  → sx1280ISR()                          [drivers/rx/rx_sx1280.c]
    → DMA chain: read IRQ status → clear IRQ → read FIFO
    → sx1280ProcessIrq()
      → processRFPacket(dmaBuffer)       [rx/expresslrs.c:729]
        → validatePacketCrcStd()         [rx/expresslrs.c:715]
        → dispatch by packet type
  → FHSS hop to next channel
  → re-enter RX mode or send telemetry
```

### DMA Chain Detail (rx_sx1280.c)

The SX1280 driver uses a multi-step DMA chain triggered by the EXTI interrupt.
Each step completes → DMA ISR → triggers next step. No CPU intervention between
steps except for the final callback.

```
Step 1: sx1280IrqGetStatus()      — SPI read IRQ register
Step 2: sx1280IrqStatusRead()     — check what happened
Step 3: sx1280IrqClearStatus()    — SPI write to clear IRQ
Step 4: sx1280ProcessIrq()        — if RX_DONE, read FIFO
Step 5: sx1280ReadPayloadToBuffer() — DMA read 8 bytes into dmaBuffer
Step 6: processRFPacket() called   — main protocol handler
```

### Critical: No Hardware CRC

The SX1280 is configured with **CRC OFF** at the radio level:

```c
// rx_sx1280.c, sx1280Config()
sx1280SetPacketParamsLoRa(preambleLen, SX1280_LORA_PACKET_IMPLICIT,
    ELRS_RX_TX_BUFF_SIZE,    // 8 bytes
    SX1280_LORA_CRC_OFF,     // No hardware CRC!
    SX1280_LORA_IQ_NORMAL | ((receiver.UID[5] & 0x01) ? 0x40 : 0x00));
```

All CRC validation is **software-only** in `processRFPacket()`. The radio will
accept any 8-byte LoRa packet that matches the configured SF/BW/CR.

### IQ Inversion

IQ polarity is set based on `UID[5] bit 0`. This is a per-binding-phrase
radio-level filter: packets from a transmitter with a different UID[5] LSB
will have inverted IQ and be undecodable. This provides a 1-bit radio-level
discrimination but is trivially enumerable (only two states).

---

## 4. CRC14 Validation

Source: `rx/expresslrs_common.c`, `rx/expresslrs.c`

### CRC Parameters

| Parameter | Value |
|-----------|-------|
| Polynomial | 0x2E57 (CRC14) |
| Initial value | `(UID[4] << 8) \| UID[5]) ^ ELRS_OTA_VERSION_ID` |
| Table size | 256 entries, precomputed at init |
| Input | 7 bytes of 8-byte packet (all except crcLow) |
| Stored | 6 bits in byte 0 (crcHigh) + 8 bits in byte 7 (crcLow) = 14 bits |

### CRC Computation

```c
// expresslrs_common.c
void generateCrc14Table(void) {
    uint16_t crc;
    for (uint16_t i = 0; i < ELRS_CRC_LEN; i++) {
        crc = i << (14 - 8);
        for (uint8_t j = 0; j < 8; j++) {
            crc = (crc << 1) ^ ((crc & 0x2000) ? ELRS_CRC14_POLY : 0);
        }
        crc14tab[i] = crc;
    }
}

uint16_t calcCrc14(uint8_t *data, uint8_t len, uint16_t crc) {
    while (len--) {
        crc = (crc << 8) ^ crc14tab[((crc >> 6) ^ (uint16_t) *data++) & 0x00FF];
    }
    return crc & 0x3FFF;
}
```

### Nonce-in-CRC Mechanism (SM_WIDE only)

For `PACKET_TYPE_RCDATA` with `SM_WIDE` switch mode, the `crcHigh` field is
**overwritten** before CRC calculation:

```c
// expresslrs.c:validatePacketCrcStd()
if (otaPktPtr->type == ELRS_RC_DATA_PACKET && switchModeCurr == SM_WIDE) {
    otaPktPtr->crcHigh = (receiver.nonceRX % receiver.modParams->fhssHopInterval) + 1;
}
```

This means the transmitter must embed the correct nonce modulo hop-interval
into the CRC high byte, creating a weak time-synchronization check. The nonce
increments each packet interval, so an attacker must track the nonce counter.

### Attack Relevance

- CRC initializer is derived from UID[4] and UID[5] XORed with OTA version (3)
- Knowing the full UID allows computing valid CRCs for any crafted packet
- The 14-bit CRC space (16384 values) is small enough for brute-force if the
  UID is unknown, but at ~250–500 packets/sec this is impractical in real-time
- The nonce-in-CRC for SM_WIDE adds a synchronization requirement

---

## 5. FHSS Hopping Sequence

Source: `rx/expresslrs_common.c`

### Sequence Generation

```c
void fhssGenSequence(const uint8_t UID[], const elrsFreqDomain_e dom) {
    long macSeed = ((long)UID[2] << 24) + ((long)UID[3] << 16) +
                   ((long)UID[4] << 8) + UID[5];
    macSeed ^= ELRS_OTA_VERSION_ID;  // XOR with 3
    rngSeed(macSeed);
    // Fisher-Yates shuffle of channel indices
    // For ISM2400: 80 channels, 240 sequence entries (3× channels)
}
```

### ISM2400 Configuration

| Parameter | Value |
|-----------|-------|
| Channels | 80 |
| Freq start | 2400.4 MHz |
| Freq stop | 2479.4 MHz |
| Freq step | ~1.0 MHz per channel |
| Sequence length | 240 entries (ELRS_NR_SEQUENCE_ENTRIES for 80ch) |
| PRNG | Linear Congruential Generator |

### PRNG (rngSeed/rng)

The sequence PRNG uses a simple 32-bit LCG. Knowing UID[2..5] (4 bytes)
fully determines the hopping sequence. UID[0..1] do not affect FHSS.

### Hop Interval

The `fhssHopInterval` varies by air rate:
- 500 Hz: hop every 4 packets
- 250 Hz: hop every 4 packets
- 150 Hz: hop every 4 packets
- 50 Hz: hop every 4 packets

An attacker who knows the UID can predict every frequency hop.

---

## 6. Connection State Machine

Source: `rx/expresslrs.c`

### States

```
ELRS_DISCONNECTED ──SYNC pkt──→ ELRS_TENTATIVE
                                    │
                         (timing lock criteria met)
                                    │
                                    ▼
                              ELRS_CONNECTED
                                    │
                         (disconnect timeout OR
                          model ID change)
                                    │
                    ┌───────────────┤
                    ▼               ▼
           ELRS_DISCONNECTED  ELRS_DISCONNECT_PENDING
                                    │
                              (drops to DISCONNECTED)
```

### Transition Criteria

**DISCONNECTED → TENTATIVE** (`tentativeConnection()`, triggered by SYNC packet):
- SYNC packet passes CRC14
- UID[3] and UID[4] match exactly
- Upper 2 bits of UID[5] match (18-bit UID check)
- Sets `connectionState = ELRS_TENTATIVE`
- Synchronizes nonce and FHSS index from SYNC packet

**TENTATIVE → CONNECTED** (`handleConnectionStateUpdate()`, line 954):
- `timerState == ELRS_TIM_LOCKED` (phase-lock loop has locked)
- Lock criteria: `abs(offsetDeltaUs) <= 10` AND `abs(offsetUs) < 100`
- Link quality: `lqGet() > minLqForChaos()` (LQ > dynamic threshold)
- Records `gotConnectionMs` timestamp
- If modelMatch: `connectionHasModelMatch = true`

**CONNECTED → DISCONNECTED** (`lostConnection()`, line 510):
- No valid packet received for `disconnectTimeoutMs` (2500–4000ms by rate)
- Resets all state: nonce, FHSS index, timer, RF mode
- Begins RF mode cycling through all air rates

**CONNECTED → DISCONNECT_PENDING** (model ID change via MSP):
- Received MSP_SET_RX_CONFIG with new model ID
- Drops connection to re-sync with new model match value

### Timer States

```
TIM_DISCONNECTED ──SYNC──→ TIM_TENTATIVE ──lock──→ TIM_LOCKED
       ▲                                               │
       └──────────────(lost connection)─────────────────┘
```

The tick/tock ISR alternates between TICK (packet window) and TOCK (frequency
hop + phase adjustment). Phase lock adjusts the timer period based on packet
arrival timing.

---

## 7. Packet Type Dispatch

Source: `rx/expresslrs.c:processRFPacket()` (line 729)

All packets are 8 bytes. The type field is the low 2 bits of byte 0.

### Dispatch Table

| Type | Value | Handler | Connection Required | Model Match Required |
|------|-------|---------|--------------------|--------------------|
| RC_DATA | 0x00 | inline in processRFPacket | CONNECTED | Yes |
| MSP_DATA | 0x01 | processRFMspPacket() | No (bind mode) / CONNECTED | Yes (for processMspPacket) |
| SYNC | 0x02 | processRFSyncPacket() | No | No (establishes connection) |
| TLM | 0x03 | (ignored on RX side) | — | — |

### RC_DATA Processing

```c
if (otaPktPtr->type == ELRS_RC_DATA_PACKET && connectionHasModelMatch) {
    // Only when CONNECTED + model matched
    memcpy((void *)rxSpiRcData, (void *)otaPktPtr, ELRS_RX_TX_BUFF_SIZE);
    // Raw packet copied to RC data buffer for later decoding
}
```

RC data is accepted only when fully connected with model match. This is the
primary channel control data — forging these controls the drone.

### SYNC Processing

SYNC packets are the gateway to establishing a connection. They are processed
in **any** connection state, including DISCONNECTED. The verification is:

1. CRC14 must match (requires knowing CRC initializer = f(UID[4], UID[5]))
2. UID[3] must match byte in packet
3. UID[4] must match byte in packet
4. Upper 2 bits of UID[5] must match

This means 18 bits of UID verification for connection establishment. The
remaining 30 bits (UID[0..2] + lower 6 bits of UID[5]) are not checked in
SYNC processing (lower 6 of UID[5] are model match).

---

## 8. MSP-over-ELRS Attack Surface

Source: `rx/expresslrs.c:processRFMspPacket()` (line 625)

### MSP Packet Flow

```
processRFMspPacket()
  │
  ├─ Bind mode? → unpackBindPacket() [writes new UID!]
  │
  └─ Connected? → receiveMspData()
                    → hasFinishedMspData()?
                      ├─ SET_RX_CONFIG + MODEL_ID → change model ID → DISCONNECT_PENDING
                      └─ connectionHasModelMatch → processMspPacket(mspBuffer)
```

### Bind Mode MSP (Critical)

When `receiver.inBindingMode == true`:
- Checks `packageIndex == 1` and `payload[0] == ELRS_MSP_BIND` (0x09)
- Calls `unpackBindPacket()` which writes a **new UID** from the MSP payload
- This effectively re-binds the receiver to a new transmitter

**Bind mode entry:** If the stored UID is all zeros, the receiver starts in
bind mode with `BindingUID = {0, 1, 2, 3, 4, 5}` and `crcInitializer = 0`.

### Model ID Change via MSP

When connected with model match:
- MSP command `ELRS_MSP_SET_RX_CONFIG` (45) with subcommand `ELRS_MSP_MODEL_ID` (0x0A)
- Writes new model ID to config
- Sets `connectionState = ELRS_DISCONNECT_PENDING`
- Forces reconnection with new model match value

### General MSP Dispatch

When `connectionHasModelMatch == true`, arbitrary MSP commands are passed to
`processMspPacket(mspBuffer)`. This is Betaflight's MSP handler — the same
one used over USB/UART. This is the broadest attack surface: any MSP command
that Betaflight supports can potentially be injected over ELRS.

MSP commands of interest:
- `MSP_SET_MOTOR` — direct motor control
- `MSP_SET_RAW_RC` — override RC channels
- `MSP_SET_FEATURE` / `MSP_SET_CF_SERIAL_CONFIG` — reconfigure features
- `MSP_EEPROM_WRITE` — persist configuration changes
- `MSP_SET_REBOOT` — reboot the flight controller
- CLI commands via MSP — full CLI access

**Constraint:** MSP data is sent in 5-byte chunks per ELRS packet
(`ELRS_MSP_BYTES_PER_CALL`), requiring multiple packets for larger MSP
commands. The MSP reassembly buffer must be filled correctly.

---

## 9. Failsafe State Machine

Source: `flight/failsafe.c`

### Default Configuration

| Parameter | Default | Unit |
|-----------|---------|------|
| failsafe_delay | 15 | 100ms units (1.5s) |
| failsafe_off_delay | 10 | 100ms units (1.0s) |
| failsafe_throttle | 1000 | PWM value (minimum) |
| failsafe_procedure | DROP_IT | (0=AUTO-LAND, 1=DROP, 2=GPS-RESCUE) |
| failsafe_recovery_delay | 10 | 100ms units (1.0s) |
| failsafe_stick_threshold | 30 | percent |
| failsafe_throttle_low_delay | 100 | 100ms units (10.0s) |

### State Machine

```
FAILSAFE_IDLE
  │
  ├─ (Rx data lost + throttle was low long enough) → FAILSAFE_LANDED
  │
  └─ (Rx data lost) → FAILSAFE_RX_LOSS_DETECTED
                          │
                          ├─ (data recovered) → FAILSAFE_RX_LOSS_RECOVERED → IDLE
                          │
                          ├─ procedure=AUTO_LAND → FAILSAFE_LANDING
                          │     └─ off_delay expires → FAILSAFE_LANDED
                          │
                          ├─ procedure=DROP_IT → FAILSAFE_LANDED (immediate)
                          │
                          └─ procedure=GPS_RESCUE → FAILSAFE_GPS_RESCUE

FAILSAFE_LANDED
  → disarm(DISARM_REASON_FAILSAFE)
  → setArmingDisabled(ARMING_DISABLED_FAILSAFE)
  → FAILSAFE_RX_LOSS_MONITORING

FAILSAFE_RX_LOSS_MONITORING
  → (Rx recovered + recovery period elapsed) → FAILSAFE_RX_LOSS_RECOVERED

FAILSAFE_RX_LOSS_RECOVERED
  → unsetArmingDisabled(ARMING_DISABLED_FAILSAFE)
  → FAILSAFE_IDLE
```

### Two-Stage Failsafe

**Stage 1 (rxDataFailurePeriod):**
- Period: `failsafe_delay × 100ms` (default 1.5s)
- Checked every 10ms by `failsafeCheckDataFailurePeriod()`
- Sets `rxLinkState = FAILSAFE_RXLINK_DOWN`
- Blocks arming: `ARMING_DISABLED_RX_FAILSAFE`

**Stage 2 (failsafe procedure):**
- Triggered when `failsafeUpdateState()` sees `receivingRxData == false`
- DROP_IT: immediate disarm
- AUTO_LAND: hold position for `failsafe_off_delay` then disarm
- GPS_RESCUE: autonomous return (requires GPS, not typical on TinyHawk III)

### Attack-Relevant Timing

The total time from last valid packet to disarm (DROP_IT):

```
ELRS disconnect timeout (2500-4000ms by rate)
  + failsafe_delay (1500ms default)
  = 4000-5500ms total
```

For AUTO_LAND, add `failsafe_off_delay` (1000ms default) = 5000-6500ms.

**Recovery:** After signal recovery, re-arming requires:
1. `failsafe_recovery_delay` (1000ms default) of continuous valid data
2. Manual re-arm by pilot (cannot auto-recover to armed state)

This means a brief jamming burst of >4 seconds triggers DROP_IT on default
settings, and the pilot must manually re-arm. A sustained jam keeps the
drone permanently disarmed.

---

## 10. Exploitable Sinks and Attack Vectors

### Vector 1: SYNC Flooding (Connection Hijack)

**Mechanism:** Send crafted SYNC packets with known UID to force the receiver
into TENTATIVE state, overriding the legitimate TX's timing.

**Requirements:**
- Know UID[3], UID[4], and upper 2 bits of UID[5] (18 bits)
- Know CRC initializer (derived from UID[4], UID[5])
- Match the current air rate (SF/BW/CR) and frequency

**Effect:**
- Disrupts phase-lock loop timing
- Can force DISCONNECTED state if timing doesn't re-lock
- Can inject false nonce and FHSS index to desynchronize hopping

**Source evidence:**
```c
// expresslrs.c:processRFSyncPacket()
if (receiver.connectionState == ELRS_DISCONNECTED ||
    receiver.nonceRX != otaPktPtr->sync.nonce ||
    fhssGetCurrIndex() != otaPktPtr->sync.fhssIndex || ...) {
    fhssSetCurrIndex(otaPktPtr->sync.fhssIndex);
    receiver.nonceRX = otaPktPtr->sync.nonce;
    tentativeConnection(timeStampMs);  // resets to TENTATIVE
}
```

Even when CONNECTED, a SYNC with a different nonce or FHSS index resets to
TENTATIVE. This is the most promising disruption vector.

### Vector 2: RC Data Injection (Commandeering)

**Mechanism:** Send crafted RC_DATA packets on the correct frequency at the
correct time with valid CRC.

**Requirements:**
- Full 6-byte UID (for CRC initializer and FHSS sequence)
- Synchronized nonce counter (for SM_WIDE CRC)
- Phase-locked timing (packet must arrive in the TICK window)
- Model match (for RC data to reach flight controller)

**Effect:**
- Direct control of all RC channels (roll, pitch, yaw, throttle, aux)
- Can override legitimate TX commands if timed correctly

**Difficulty:** High. Requires full UID, timing synchronization, and the
ability to transmit LoRa on the exact frequency during the receive window.
The legitimate TX is also transmitting, creating a race condition.

### Vector 3: MSP Injection (Configuration Attack)

**Mechanism:** Send MSP_DATA packets to execute arbitrary MSP commands on the
flight controller.

**Requirements:**
- Same as RC Data Injection (full connection + model match)
- Multiple packets to assemble MSP command (5 bytes per ELRS packet)

**Attack possibilities once connected:**
- `MSP_SET_MOTOR`: direct motor speed control
- `MSP_SET_REBOOT`: reboot flight controller mid-flight
- `MSP_EEPROM_WRITE`: persist malicious configuration
- Failsafe parameter modification via MSP SET commands
- Disable arming checks

### Vector 4: Bind Mode Exploitation

**Mechanism:** If the receiver enters bind mode (UID all zeros), connect and
send a bind MSP packet to re-bind to attacker's UID.

**Requirements:**
- Receiver must be in bind mode (UID = {0,0,0,0,0,0})
- Known bind parameters: BindingUID = {0,1,2,3,4,5}, crcInitializer = 0
- Binding air rate: ELRS_BINDING_RATE_24 = index 3 (50Hz on ISM2400)

**Effect:**
- Permanent takeover — receiver bound to attacker's UID
- Persisted to EEPROM, survives power cycles

**Difficulty:** Low if receiver is in bind mode. The bind UID and CRC are
hardcoded constants. However, getting the receiver into bind mode requires
either physical access (button press) or a previous MSP attack.

### Vector 5: Jamming / Denial of Service

**Mechanism:** Broadband or frequency-following jamming to prevent valid
packet reception.

**Requirements:**
- Sufficient transmit power on 2.4 GHz
- For targeted jamming: knowledge of FHSS sequence (from UID)
- For broadband: enough power to cover 2400.4–2479.4 MHz

**Effect:**
- ELRS disconnect after 2500–4000ms (rate-dependent)
- Failsafe Stage 1 after additional 1500ms
- DROP_IT disarm after 4000–5500ms total
- Pilot cannot re-arm until jamming stops + recovery delay

**Difficulty:** Low for broadband. The 80 MHz band is narrow. Even without
FHSS knowledge, a wideband 2.4 GHz jammer would be effective. ELRS has no
anti-jam features — it relies entirely on frequency hopping for interference
resistance.

### Vector 6: SX1280 Busy Timeout Exploitation

**Mechanism:** The SX1280 driver has a busy timeout watchdog in
`sx1280HandleFromTick()` (1000μs timeout). If the SX1280 BUSY pin is stuck
high, the driver force-clears the SPI state machine.

**Source evidence:**
```c
// rx_sx1280.c:sx1280HandleFromTick()
if (busyTimeoutUs != 0 && cmpTimeUs(micros(), busyTimeoutUs) > 0) {
    // Force end the current SPI operation
    sx1280IsBusy = false;
    // ... reset state machine
}
```

**Relevance:** This is a defensive mechanism, but if an attacker can induce
SPI bus contention or keep the SX1280 in a busy state (e.g., through
carefully timed transmissions during the TOCK window), it could disrupt
packet reception.

---

## 11. Air Rate Configurations

Source: `rx/expresslrs_common.c`

### SX1280 (ISM2400) Air Rate Table

| Rate | SF | BW | CR | Interval | Sensitivity | Disconnect Timeout | TOA |
|------|----|----|-----|----------|-------------|-------------------|-----|
| 500 Hz | SF5 | BW800 | CR_4_6 | 2000 μs | -105 dBm | 2500 ms | 216 μs |
| 250 Hz | SF6 | BW800 | CR_4_7 | 4000 μs | -108 dBm | 3000 ms | 432 μs |
| 150 Hz | SF7 | BW800 | CR_4_7 | 6666 μs | -112 dBm | 3500 ms | 864 μs |
| 50 Hz | SF8 | BW800 | CR_4_7 | 20000 μs | -115 dBm | 4000 ms | 1497 μs |

### Binding Rate

- ISM2400: ELRS_BINDING_RATE_24 = 3 → index 3 = 50 Hz (SF8)
- Uses special BindingUID = {0, 1, 2, 3, 4, 5}

### RF Mode Cycling

When disconnected, the receiver cycles through all 4 rates with period:
```
cycleIntervalMs = rfPerfParams->rxLockTimeoutMs * rfModeCycleMultiplier
```
With `rfModeCycleMultiplier = ELRS_MODE_CYCLE_MULTIPLIER_SLOW (10)` when locked,
or dynamically reduced. This means the receiver dwells on each rate for
several seconds when disconnected.

---

## 12. Key Constants and Structures

### Packet Structure (8 bytes, packed)

```
Byte 0: [type:2][crcHigh:6]
Bytes 1-6: payload (varies by type)
Byte 7: crcLow
```

### elrsOtaPacket_t Union Members

**RC_DATA (type=0x00):**
```
bytes 1-5: ch[5]     — channel data (4 channels packed)
byte 6:    [switches:7][ch4:1]
```

**MSP_DATA (type=0x01):**
```
byte 1:    packageIndex
bytes 2-6: payload[5]  (ELRS_MSP_BYTES_PER_CALL)
```

**SYNC (type=0x02):**
```
byte 1: fhssIndex
byte 2: nonce
byte 3: [switchEncMode:1][newTlmRatio:3][rateIndex:4]
byte 4: UID3
byte 5: UID4
byte 6: UID5
```

**TLM (type=0x03):**
```
byte 1: [type:SHIFT][packageIndex:8-SHIFT]
bytes 2-6: telemetry data or link stats
```

### Key Defines

```c
ELRS_OTA_VERSION_ID      = 3
ELRS_CRC14_POLY          = 0x2E57
ELRS_RX_TX_BUFF_SIZE     = 8
ELRS_MODELMATCH_MASK     = 0x3F
ELRS_MSP_BIND            = 0x09
ELRS_MSP_MODEL_ID        = 0x0A
ELRS_MSP_SET_RX_CONFIG   = 45
ELRS_MAX_CHANNELS        = 16
ELRS_BINDING_RATE_24     = 3
```

### UID → Derived Parameters

```
UID[0..1]:  Not used in any OTA verification
UID[2..5]:  Seeds FHSS PRNG (via macSeed XOR OTA_VERSION_ID)
UID[3]:     Verified in SYNC packet
UID[4]:     Verified in SYNC packet; CRC init high byte
UID[5]:     Partially verified in SYNC (upper 2 bits); CRC init low byte;
            IQ inversion (bit 0); model match (lower 6 bits XOR)
```

### CRC Initializer Derivation

```c
crcInitializer = (UID[4] << 8) | UID[5];
crcInitializer ^= ELRS_OTA_VERSION_ID;  // XOR with 3
```

For bind mode: `crcInitializer = 0` (UID is all zeros, XOR with 3 gives... 
actually `(0 << 8 | 0) ^ 3 = 3`, but the code sets it to 0 explicitly for
bind mode).

---

## Summary: Attack Priority Matrix

| Vector | Difficulty | Impact | UID Required | Notes |
|--------|-----------|--------|-------------|-------|
| Broadband Jam | Low | Deny (forced landing) | No | 2.4 GHz wideband; 4-5.5s to disarm |
| Targeted FHSS Jam | Medium | Deny (forced landing) | Partial (UID[2-5]) | Follow hops; less power needed |
| SYNC Flood | Medium | Disrupt (desync) | Partial (UID[3-5]) | Can force TENTATIVE/DISCONNECTED |
| Bind Mode Hijack | Low | Commandeer (permanent) | No (hardcoded) | Only if receiver in bind mode |
| RC Data Injection | High | Commandeer (transient) | Full + timing | Must win timing race with legit TX |
| MSP Injection | High | Commandeer / Persist | Full + timing | Multi-packet; can rewrite config |

**Most viable CTF attack path:**
1. Passive capture → extract UID[4,5] from SYNC packets (16 bits visible)
2. Rainbow table or brute-force remaining UID bytes
3. SYNC flooding to disrupt connection → force disconnect → failsafe
4. If full UID obtained: RC injection or MSP command injection

**Key constraint:** All source analysis here describes the 4.4.2 codebase as
compiled for CRAZYBEEF4SX1280. The binary at 0x08000000 matches this target.
No claims about runtime behavior on the live hardware have been validated —
that requires bench testing with a passive capture of the actual RF link.
