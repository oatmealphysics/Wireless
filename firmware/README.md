# Firmware Test Corpus — EMAX TinyHawk III & E8 Controller

Collected 2026-10-14 for offline analysis and security testing in preparation for a wireless drone CTF.

---

## Hardware → Chipset Mapping

| Device | MCU | RF Chip | Protocol Stack | Firmware Target |
|---|---|---|---|---|
| **TinyHawk III** (drone) | STM32F411CEU6 | SX1280 (SPI) | ELRS baked into Betaflight | `EMAX_TINYHAWKF4SX1280` (unified target on `CRAZYBEEF4SX1280` base) |
| **EMAX E8 TX** (controller) | ESP32 | SX1280 | ExpressLRS standalone | `Unified_ESP32_2400_TX` (closest: `EMAX_2400_TX` / `EMAX_NANO_2400_TX`) |
| **EMAX standalone 2.4GHz RX** | ESP8285 | SX1280 | ExpressLRS standalone | `Unified_ESP8285_2400_RX` |

**Note:** The official ExpressLRS target registry has no E8-specific entry. The EMAX OLED/Nano TX targets are the closest match (ESP32 + SX1280, same `Unified_ESP32_2400_TX` firmware base). The E8 likely uses one of these targets with a different pin layout JSON.

---

## Inventory

### Betaflight Firmware (TinyHawk III — drone side)

| File | Description |
|---|---|
| `betaflight/betaflight_2026.6.2_STM32F411_CRAZYBEEF4SX1280.hex` | Prebuilt firmware for STM32F411 + SPI SX1280 ELRS boards (Intel HEX) |
| `betaflight/betaflight_CRAZYBEEF4SX1280.bin` | Same firmware converted to raw binary for Ghidra/IDA analysis |
| `betaflight/betaflight_2026.6.2_STM32F411.hex` | Base STM32F411 unified firmware (Intel HEX) |
| `betaflight/betaflight_STM32F411.bin` | Same, raw binary |
| `betaflight/EMAX_TINYHAWKF4SX1280.config` | EMAX unified target config (pin mappings, SPI bus assignments, SX1280 GPIO) |

### ExpressLRS Source (E8 controller — TX side)

| File | Description |
|---|---|
| `src/expresslrs-4.1.0.tar.gz` | Official ExpressLRS 4.1.0 tagged release source |
| `src/ExpressLRS-4.1.0/` | Extracted source tree |
| `src/betaflight-2026.6.2.tar.gz` | Betaflight 2026.6.2 source (contains SPI ELRS RX driver) |
| `src/betaflight-2026.6.2/` | Extracted source tree |

### Reference (from prior collection)

| File | Description |
|---|---|
| `reference/ExpressLRS-master.tar.gz` | ELRS master branch snapshot (commit `dd96654`) |
| `reference/ExpressLRS-targets-master.tar.gz` | Target registry snapshot (commit `51a697b`) |
| `reference/targets.json` | Searchable target registry — confirms EMAX targets are ESP32 + SX1280 |
| `reference/elrs-4.1.0.lua` | Official Lua config script (not firmware) |

---

## Attack Surface Analysis — Key Source Files

### ELRS Binding Phrase → UID Derivation (CRITICAL)

**Location:** `src/ExpressLRS-4.1.0/src/python/build_flags.py:40,72`

The binding phrase is the **sole shared secret** between TX and RX. It is converted to a 6-byte UID via:
```python
UID = hashlib.md5(('-DMY_BINDING_PHRASE="' + phrase + '"').encode()).digest()[0:6]
```

This means:
- The UID is just the first 6 bytes of an MD5 hash of a known-format string
- MD5 is fast to brute-force — dictionary attacks against common binding phrases are trivial
- If you capture a SYNC packet containing UID[4] and UID[5], you only need to match 2 bytes initially
- The full UID search space is 2^48 but the binding phrase space is likely much smaller

### FHSS Hopping Sequence

**Location:** `src/ExpressLRS-4.1.0/src/lib/FHSS/FHSS.cpp`

- FHSS sequence is 256 entries, seeded by `OtaGetUidSeed()` which uses UID[2:6]
- For 2.4 GHz ISM: 80 channels from 2400.4 MHz to 2479.4 MHz
- Sync channel is always `freq_count / 2` = channel 40
- Sequence is deterministic from the seed — know the UID, know every hop

### OTA Packet CRC

**Location:** `src/ExpressLRS-4.1.0/src/lib/OTA/OTA.cpp:28-45`

- CRC initializer = `(UID[4] << 8) | UID[5]` XOR'd with `OTA_VERSION_ID << 8`
- SYNC packets use CRC init without nonce XOR
- Non-SYNC packets XOR the nonce into the CRC init
- CRC14 (poly 0x2E57) for standard 8-byte packets
- CRC16 (poly 0x3D65) for full-res 13-byte packets

### SYNC Packet Structure

**Location:** `src/ExpressLRS-4.1.0/src/lib/OTA/OTA.h:29-40`

SYNC packets are broadcast and contain:
```c
struct OTA_Sync_s {
    uint8_t fhssIndex;      // Current position in hop sequence
    uint8_t nonce;           // Packet counter
    uint8_t rfRateEnum;      // Air rate (50Hz, 150Hz, 250Hz, 500Hz)
    uint8_t switchEncMode:1, // Switch encoding
            newTlmRatio:3,   // Telemetry ratio
            geminiMode:1,    // Diversity mode
            otaProtocol:2,   // OTA version
            free:1;
    uint8_t UID4;            // UID byte 4 — SENT IN THE CLEAR
    uint8_t UID5;            // UID byte 5 — SENT IN THE CLEAR
};
```

**UID[4] and UID[5] are transmitted in plaintext in every SYNC packet.** This leaks 16 bits of the binding phrase hash to any passive listener.

### SX1280 Radio Driver

**Location:** `src/ExpressLRS-4.1.0/src/lib/SX1280Driver/`

- `SX1280.cpp` — Radio configuration, TX/RX, FHSS channel setting
- `SX1280_Regs.h` — Register map
- `SX1280_hal.cpp` — SPI hardware abstraction

### Betaflight SPI ELRS RX Driver

**Location:** `src/betaflight-2026.6.2/src/main/rx/expresslrs.c`

- SPI-connected ELRS receiver runs on the flight controller's STM32
- No separate ESP chip — the ELRS protocol stack is compiled directly into Betaflight
- Updates require reflashing the entire FC firmware

**Location:** `src/betaflight-2026.6.2/src/main/drivers/rx/rx_sx1280.c`

- Low-level SX1280 SPI driver for Betaflight
- Handles radio init, frequency setting, packet TX/RX

---

## ELRS Attack Chain Summary

```
1. Passive capture on 2.4 GHz (SDR or SX1280 sniffer)
   → Identify SYNC packets on the sync channel (channel 40 of 80)
   → Extract UID[4], UID[5] from SYNC payload

2. Brute-force binding phrase (offline)
   → MD5 hash of '-DMY_BINDING_PHRASE="<phrase>"'
   → Match first 6 bytes against captured UID[4:5]
   → Dictionary of common phrases + known defaults

3. Derive full attack state
   → UID[0:6] from MD5 hash
   → FHSS seed from OtaGetUidSeed(UID[2:6])
   → Full 256-entry hop sequence from FHSSrandomiseFHSSsequence()
   → CRC initializer from OtaUpdateCrcInitFromUid()

4. Inject packets
   → Craft valid OTA packets with correct CRC
   → Follow hop sequence in real-time
   → Inject RC data, SYNC, or DATA packets
   → Override legitimate TX (higher power wins on SX1280)
```

---

## SHA-256 Checksums

```
f06b053564ab37a18f5fd74c2e08cf9997434ef10a2b5d5785274a6cffeff5b2  betaflight/betaflight_2026.6.2_STM32F411_CRAZYBEEF4SX1280.hex
22463809f4172fcb7003b8fd9ea6528dfa14c9d1f98e530c6c5ffa3926a929d0  betaflight/betaflight_2026.6.2_STM32F411.hex
d98f2569e4fba71a3c3054fd798c6329c64b7ee4b686384a2a1abc9e28d9ad0e  betaflight/betaflight_CRAZYBEEF4SX1280.bin
0f9fc14eb368c4805a802a9bd65b4921ad5c420bf91e0446570c097e0569c387  betaflight/betaflight_STM32F411.bin
4b7d5acb364ff1f28265c5e84d1e63bec20a1a1a39f1e292250d7cae251db85a  src/betaflight-2026.6.2.tar.gz
d1d854d83017dfc09d1c78dfba5ce400952a6c61ae64fdd2a2188fcf16bb36c0  src/expresslrs-4.1.0.tar.gz
```

---

## Official References

- Betaflight releases: <https://github.com/betaflight/betaflight/releases>
- Betaflight unified targets: <https://github.com/betaflight/unified-targets>
- ExpressLRS source: <https://github.com/ExpressLRS/ExpressLRS>
- ExpressLRS targets: <https://github.com/ExpressLRS/targets>
- ExpressLRS 4.1.0 release: <https://github.com/ExpressLRS/ExpressLRS/releases/tag/4.1.0>
- EMAX 2.4 GHz TX update guide: <https://www.expresslrs.org/quick-start/transmitters/emax2400/>
- SPI ELRS receivers (Betaflight integration): <https://www.expresslrs.org/hardware/spi-receivers/>
