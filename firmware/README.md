# Firmware Test Corpus

Collected for offline analysis and security testing in preparation for a wireless drone CTF.

**Handoff evidence note (2026-10-14):** See [the detailed home lab notes](../HOME-LAB-HANDOFF.md) for acquisition status, source-version distinctions, and tool limitations. The particular E8's MCU, installed firmware version, and upstream target mapping remain unconfirmed; ESP32/OLED/Nano mapping below is a hypothesis based on related modules. No E8-specific binary has been positively identified in this corpus. Source checks established two explicit UID fields in the 4.1.0 SYNC struct, while the existing lookup helper assumes three bytes. The complete corpus hash manifest is [SHA256SUMS](SHA256SUMS).

---

## Hardware → Chipset Mapping

| Device | MCU | RF Chip | Protocol Stack | Firmware Target |
|---|---|---|---|---|
| **TinyHawk III** (drone) | STM32F411CEU6 | SX1280 (SPI) | ELRS baked into Betaflight | `EMAX_TINYHAWKF4SX1280` (unified target on `CRAZYBEEF4SX1280` base) |
| **EMAX E8 TX** (controller) | Unconfirmed for this unit | Unconfirmed for this unit | ELRS variant reported; version unconfirmed | No confirmed target; OLED/Nano entries are related-module references only |
| **EMAX standalone 2.4GHz RX** | ESP8285 | SX1280 | ExpressLRS standalone | `Unified_ESP8285_2400_RX` |

**Note:** The official ExpressLRS target registry has no E8-specific entry. The EMAX OLED/Nano TX targets are the closest match (ESP32 + SX1280, same `Unified_ESP32_2400_TX` firmware base). The E8 likely uses one of these targets with a different pin layout JSON.

---

## Inventory

The machine-readable [artifact register](artifact-register.json) covers every
manifest entry with size, hash, declared version/target, source project, artifact
kind, and hardware-match confidence. Missing original download URLs and exact
commit attribution are explicitly unresolved; a matching hash does not repair
those evidence gaps. None of the eleven samples is confirmed E8-specific.

### Betaflight Firmware (TinyHawk III — drone side)

**Live device runs 4.4.2** (confirmed via CLI `version` on 2026-10-15). The 2026.6.2 corpus remains as a related sample; 4.4.2 is the version-matched analysis target. See `research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md`.

| File | Description |
|---|---|
| `betaflight/betaflight_4.4.2_STM32F411.hex` | **Version-matched** official release (commit `23d066d08`, Nov 30 2023). OTA v3 only. |
| `betaflight/betaflight_4.4.2_STM32F411.bin` | Same, raw binary for Ghidra (0x08000000, 455 KB, ARM Cortex-M4F). |
| `betaflight/betaflight_2026.6.2_STM32F411_CRAZYBEEF4SX1280.hex` | 2026.6.2 related sample (Intel HEX). Not installed on the live drone. |
| `betaflight/betaflight_CRAZYBEEF4SX1280.bin` | Same, raw binary. |
| `betaflight/betaflight_2026.6.2_STM32F411.hex` | Base STM32F411 unified firmware, 2026.6.2 (Intel HEX). |
| `betaflight/betaflight_STM32F411.bin` | Same, raw binary. |
| `betaflight/EMAX_TINYHAWKF4SX1280.config` | EMAX unified target config (pin mappings, SPI bus assignments, SX1280 GPIO). |

### ExpressLRS Source (E8 controller — TX side)

| File | Description |
|---|---|
| `src/expresslrs-4.1.0.tar.gz` | Official ExpressLRS 4.1.0 tagged release source |
| `src/ExpressLRS-4.1.0/` | Extracted source tree |
| `src/betaflight-2026.6.2.tar.gz` | Betaflight 2026.6.2 source (contains SPI ELRS RX driver) |
| `src/betaflight-2026.6.2/` | Extracted source tree |

### ArduPilot / MAVLink (autopilot — MAVLink attack path)

| File | Description |
|---|---|
| `ardupilot/arducopter_CubeOrange_4.7.2.apj` | ArduCopter 4.7.2 for CubeOrange (STM32H743, board_id 140). APJ = JSON metadata + base64 firmware image. |
| `ardupilot/arducopter_CubeOrange_4.7.2.elf` | Same build, ELF with symbols — primary RE target for MAVLink handler analysis. |
| `ardupilot/arducopter_Pixhawk1_4.7.2.apj` | ArduCopter 4.7.2 for Pixhawk1 (STM32F4). Common CTF autopilot board. |
| `ardupilot/arducopter_Pixhawk1_4.7.2.elf` | Same build, ELF with symbols. |
| `ardupilot/arducopter_SITL_x86_64_4.7.2` | Software-in-the-loop binary for x86_64 Linux. Runs locally for MAVLink protocol testing, P1-1/P1-3 validation, and fuzzing — no hardware needed. |
| `src/ardupilot-Copter-4.7.2.tar.gz` | Full ArduPilot source (308 MB, **not committed** — re-download from tagged release). |

All ArduPilot artifacts: version 4.7.2, commit `fb773567`, released 2026-10-14.

### PX4 / MAVLink (autopilot — cross-stack MAVLink comparison)

| File | Description |
|---|---|
| `px4/px4_fmu-v5_default_1.18.0-rc1.px4` | PX4 v1.18.0-rc1 for FMU-v5/Pixhawk4 (STM32F765, board_id 50). PX4 `.px4` = JSON+base64 container. |
| `px4/cubepilot_cubeorange_default_1.18.0-rc1.px4` | PX4 v1.18.0-rc1 for CubeOrange (STM32H743, board_id 140 — same board as ArduPilot CubeOrange). |

**PX4 `.px4` format:** JSON envelope with `board_id`, `description`, `git_identity`, and a base64-encoded firmware image. Extract the raw binary for Ghidra with: `python3 -c "import json,base64,sys; d=json.load(open(sys.argv[1])); open(sys.argv[2],'wb').write(base64.b64decode(d['image']))" input.px4 output.bin`

All PX4 artifacts: version 1.18.0-rc1, git identity `fca3df86`.

### Tello / Wi-Fi drone (Wi-Fi attack path)

| File | Description |
|---|---|
| `tello/djitellopy.tar.gz` | DJITelloPy community SDK (21 KB). Clean Python API for Tello control. |
| `tello/dji-sdk-protocol/` | Protocol-relevant files extracted from the official DJI Tello-Python SDK: `tello.py` (UDP client), `command.txt` (command set), `tello_state.py` (state parser). |

**Tello protocol:** plaintext UDP on port 8889, drone runs as Wi-Fi AP at 192.168.10.1. No authentication, no encryption. Attack: join AP (or deauth + rogue AP), send UDP commands.

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

The complete manifest is `firmware/SHA256SUMS`; its paths are relative to the
repository root. Verify it from that root on Ubuntu with:

```sh
sha256sum --check firmware/SHA256SUMS
```

The repository's `.gitattributes` preserves the original bytes of corpus
artifacts on Windows and Ubuntu. Do not normalize their line endings: even a
text reference file's checksum covers the exact downloaded bytes. The Intel HEX
files retain their original CRLF endings; the config, Lua, and JSON references
retain LF. Existing checkouts made before this rule may still contain converted
text files; compare with a fresh checkout before treating a mismatch as corruption.

```
bed2ed630acf8301b966ed6ac9fadea53ad0337535856ff8d05e45f852ea9729  betaflight/betaflight_4.4.2_STM32F411.hex
33830bda10262ebc2b3a8c337de611a6f5c9f4b3560bf1b0997f1d1081ffbf3d  betaflight/betaflight_4.4.2_STM32F411.bin
f06b053564ab37a18f5fd74c2e08cf9997434ef10a2b5d5785274a6cffeff5b2  betaflight/betaflight_2026.6.2_STM32F411_CRAZYBEEF4SX1280.hex
22463809f4172fcb7003b8fd9ea6528dfa14c9d1f98e530c6c5ffa3926a929d0  betaflight/betaflight_2026.6.2_STM32F411.hex
d98f2569e4fba71a3c3054fd798c6329c64b7ee4b686384a2a1abc9e28d9ad0e  betaflight/betaflight_CRAZYBEEF4SX1280.bin
0f9fc14eb368c4805a802a9bd65b4921ad5c420bf91e0446570c097e0569c387  betaflight/betaflight_STM32F411.bin
7b21b072fede3e23fdc43b4d4722d47276f789f6517573e956ec228b1f6bea94  betaflight/EMAX_TINYHAWKF4SX1280.config
4b7d5acb364ff1f28265c5e84d1e63bec20a1a1a39f1e292250d7cae251db85a  src/betaflight-2026.6.2.tar.gz
d1d854d83017dfc09d1c78dfba5ce400952a6c61ae64fdd2a2188fcf16bb36c0  src/expresslrs-4.1.0.tar.gz
95ab5dee46134dd1c1eb3d245d11d97875f2abcdd26a2a13d95e103cfe7a54c7  ardupilot/arducopter_CubeOrange_4.7.2.apj
d8cd98b9e2ed256a03253f5abc818de6f023f45df70768f4cdee43f49851bc86  ardupilot/arducopter_CubeOrange_4.7.2.elf
61a30eab1e09b23024b89a3ad873deb729273a39d0e336a46a19e51da2bcd907  ardupilot/arducopter_Pixhawk1_4.7.2.apj
c74d088b28c21dad6447d9c7ee1147d234c2d64db59da7e8cb5b7c142d5af2b9  ardupilot/arducopter_Pixhawk1_4.7.2.elf
caed35e4cb796ec06cac40b09566a18251771486b1cbac5e2bf16b7a5ed5045d  ardupilot/arducopter_SITL_x86_64_4.7.2
bba1e43f89a435d8e8e5949795155119e5a0812a8353de99cef89cc4259f265d  tello/djitellopy.tar.gz
d561181122481d735a14dac248a0e51fa18f4455f842fb5ae89de31bc1e63bf3  px4/px4_fmu-v5_default_1.18.0-rc1.px4
efa4f2f7cabd81bfd2eb2881c2bcddf58ba71fff97e5f35a38b9c7e5d942f419  px4/cubepilot_cubeorange_default_1.18.0-rc1.px4
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
- ArduPilot Copter firmware: <https://firmware.ardupilot.org/Copter/stable/>
- ArduPilot source: <https://github.com/ArduPilot/ardupilot>
- PX4 Autopilot releases: <https://github.com/PX4/PX4-Autopilot/releases>
- PX4 Autopilot source: <https://github.com/PX4/PX4-Autopilot>
- DJI Tello SDK (official): <https://github.com/dji-sdk/Tello-Python>
- DJITelloPy (community): <https://github.com/damiafuentes/DJITelloPy>
