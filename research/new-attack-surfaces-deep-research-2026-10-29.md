# New Targetable Attack Surfaces — Deep Research

**Date:** 2026-10-29
**Scope:** Authorized defensive security research on owned hardware (TinyHawk III Plus + E8 controller)
**Status:** Research/analysis only — no live exploitation attempted
**Target:** EMAX TinyHawk III Plus (EMAX_TINYHAWKF4SX1280, STM32F411CEU6, Betaflight 4.4.2, MSP API 1.45) + EMAX E8 controller (ELRS/SX1280)

This document covers attack surfaces **not previously analyzed in depth** by prior research docs in this project. Prior work already covers: ELRS WiFi-mode entry, ESP-NOW replay (CVE-2024-42483), ESP32 hidden HCI (CVE-2025-27840), CRSF injection, RF disruption feasibility, Betaflight 4.4.2 MSP command surface, and TBS Crossfire wireless audit.

---

## Table of Contents

1. [Semtech LoRa Transceiver Firmware Vulnerabilities (LR11xx → SX1280 Applicability)](#1-semtech-lora-transceiver-firmware-vulnerabilities)
2. [SX1280 SPI Command Interface Attack Surface](#2-sx1280-spi-command-interface-attack-surface)
3. [ELRS Binding Phrase & OTA Update Security — Residual Weaknesses in v3](#3-elrs-binding-phrase--ota-update-security)
4. [ELRS WiFi Firmware Update: No Signing, CSRF RCE](#4-elrs-wifi-firmware-update-no-signing-csrf-rce)
5. [STM32F411 Bootloader/DFU Attack Surface](#5-stm32f411-bootloaderdfu-attack-surface)
6. [VTX SmartAudio Protocol — Unauthenticated UART Command Injection](#6-vtx-smartaudio-protocol)
7. [Telemetry Downlink Data Leakage & Blackbox Exfiltration](#7-telemetry-downlink-data-leakage--blackbox-exfiltration)
8. [Multi-Modem FHSS Drone Takeover (Kang et al. 2025)](#8-multi-modem-fhss-drone-takeover)
9. [Remote ID Spoofing & OpenDroneID Vulnerabilities](#9-remote-id-spoofing--opendroneid-vulnerabilities)
10. [Supply-Chain Security: ELRS/Betaflight Toolchain](#10-supply-chain-security)
11. [Arduino-ESP32 CSRF Firmware Update RCE (CVE-2025-53540)](#11-arduino-esp32-csrf-firmware-update-rce)
12. [MSP Command Whitelist Gap (Betaflight Issue #15752)](#12-msp-command-whitelist-gap)
13. [Applicability Matrix for TinyHawk III Plus / E8](#13-applicability-matrix)
14. [Recommended Next Steps](#14-recommended-next-steps)

---

## 1. Semtech LoRa Transceiver Firmware Vulnerabilities

### Background: LR11xx Code Execution Chain (CVE-2025-14857, CVE-2025-14858, CVE-2025-14859)

In early 2026, researcher re146 demonstrated full code execution on Semtech's LR11xx LoRa transceiver family (LR1110, LR1120, LR1121) — the same vendor that makes the SX1280 used in our target. Three CVEs were assigned:

| CVE | Severity | Type | Description |
|-----|----------|------|-------------|
| CVE-2025-14858 | Medium (5.4) | Decrypted firmware left in RAM | `CryptoCheckEncryptedFirmwareImage` SPI command leaves decrypted firmware blocks in RAM, usable as a decryption oracle |
| CVE-2025-14857 | Medium | Unrestricted memory write | `WriteRegMemMask32` SPI command allows writes to arbitrary memory locations, bypassing address restrictions on `SystemWriteRegMem32` |
| CVE-2025-14859 | High (7.0) | AES-CBC preimage weakness | AES-CBC preimages can be calculated, allowing custom firmware signing |

**Exploit chain (LR11xx):**
1. Use CVE-2025-14858 to decrypt firmware via the SPI decryption oracle
2. Reverse-engineer the ARC-core firmware (base address `0x84000`)
3. Use CVE-2025-14857 to overwrite a function pointer via `WriteRegMemMask32`
4. Craft ciphertext blocks (using CBC properties + decryption oracle) that decrypt to chosen instructions in flash
5. Redirect execution to attacker-controlled code at `0x94510`

The bootloader commands used: erase flash (`0x8000`), write 256-byte chunk (`0x8003`), reset (`0x8005`). The researcher also discovered "magic bytes" that disable signature verification of the secondary bootloader.

Semtech issued advisory SEM-PSA-2026-001 covering all LR11xx chips.

**Source:** [LoRa under attack: running our own code in secure LoRaWAN Semtech LR11xx chips](https://b.re146.dev/lora-under-attack/), [Semtech Advisory SEM-PSA-2026-001](https://www.semtech.com/uploads/bulletins/SEM-PSA-2026-001.pdf)

### Applicability to SX1280

The SX1280 is a **different architecture** from the LR11xx family:

| Feature | LR11xx | SX1280 |
|---------|--------|--------|
| Core | ARC (ARCompact) | Unknown (likely simpler) |
| Frequency | Sub-GHz + 2.4 GHz | 2.4 GHz only |
| Firmware | Encrypted/signed, updatable via SPI | ROM-based, no user-updateable firmware |
| LoRaWAN stack | On-chip | None (raw LoRa/FLRC/GFSK modem) |
| SPI command set | Rich (crypto, firmware update, memory R/W) | Simpler (radio config, buffer R/W, status) |

**Key difference:** The SX1280 does NOT have user-updateable firmware — it is a dedicated radio modem with a fixed ROM. This means:
- The firmware update/decryption oracle attack (CVE-2025-14858) is **not directly applicable**
- The AES-CBC signing bypass (CVE-2025-14859) is **not applicable** (no firmware signing)
- The arbitrary memory write (CVE-2025-14857) **may have analogues** if the SX1280 SPI command set includes register write commands with insufficient address restrictions

**Assessment:** The LR11xx vulnerabilities demonstrate that Semtech's transceiver SPI interfaces can harbor exploitable weaknesses. The SX1280's simpler architecture provides a smaller attack surface, but its SPI register space has not been audited for similar unrestricted write primitives.

**Confirmed against D's hardware:** No — requires SPI bus analysis of the SX1280 on the TinyHawk III Plus (SPI bus 3: CS=PA15, EXTI=PC14, RESET=PA8, BUSY=PA13). This is a wired/bench test, not requiring RF gear.

---

## 2. SX1280 SPI Command Interface Attack Surface

The SX1280 communicates exclusively via SPI with the host MCU (STM32F411). Per the Semtech SX1280 datasheet, the SPI command set includes:

### Commands of Security Interest

| Command | Opcode | Security Relevance |
|---------|--------|-------------------|
| `WriteRegister` | `0x18` | Write arbitrary register values — could alter radio behavior |
| `ReadRegister` | `0x19` | Read register values — information disclosure |
| `WriteBuffer` | `0x1A` | Write to TX buffer — payload injection |
| `ReadBuffer` | `0x1B` | Read RX buffer — data extraction |
| `SetStandby` | `0x80` | Mode control |
| `SetTx` / `SetRx` | `0x83` / `0x82` | Transmit/receive mode control |
| `SetRegulatorMode` | `0x96` | Hardware register control |
| `SetRangingRole` | `0x9A` | Ranging master/slave role — position tracking implications |

### Attack Scenarios

**Scenario A: SPI Bus Injection (Physical Access)**
If an attacker gains physical access to the SPI bus (e.g., via a debug header or exposed trace), they can:
- Inject arbitrary packets by writing to the TX buffer and triggering transmission
- Read all received packets from the RX buffer
- Reconfigure the radio to different frequencies, power levels, or modulation modes
- Enable ranging mode to extract distance measurements

**Scenario B: Host MCU Compromise → SPI Pivoting**
If the STM32F411 is compromised (e.g., via MSP injection), the attacker inherits full SPI control over the SX1280. Since the ELRS stack runs on the STM32, not on a separate ESP chip, there is no isolation boundary — MSP command execution leads directly to SPI register access.

**Scenario C: Register Space Audit Needed**
The SX1280 register map includes undocumented regions. Without a full register audit, it is unknown whether:
- Test/debug modes exist that could be activated via specific register writes
- The SX1280 has factory test registers accessible via SPI
- There are writable regions that affect radio calibration in ways that could be weaponized (e.g., forcing max TX power beyond rated limits)

**Confirmed against D's hardware:** Partially — the SPI bus topology (bus 3, known pins) is confirmed from live MSP probing. Full register audit requires SPI logic analyzer access on the bench.

---

## 3. ELRS Binding Phrase & OTA Update Security

### NCC Group Findings (2022) — What Remains in ELRS v3

The NCC Group 2022 advisory identified three fundamental weaknesses in ExpressLRS:

1. **Sync packet leaks 75% of UID:** The final 3 bytes of the 6-byte UID are transmitted in every SYNC packet. An attacker observing one SYNC packet knows bytes UID[3], UID[4], UID[5].

2. **CRC initializer derived from leaked bytes:** The CRC14 initializer is `(UID[4] << 8 | UID[5]) ^ 3`. Both UID[4] and UID[5] are in the SYNC packet, so the CRC is trivially computable by an eavesdropper.

3. **Weak FHSS RNG:** The FHSS sequence is seeded by UID[2:5]. With UID[3:5] known from SYNC, only UID[2] is unknown — 256 candidates. The RNG weakness further reduces this to 128 distinct sequences. Timing correlation against observed packets identifies the correct one.

**Status of mitigations in ELRS v3:**

The NCC Group submitted a PR (#1411) in February 2022 with fixes. The ELRS maintainers **rejected it** (March 5, 2022), citing differing design philosophies — ELRS considers the binding phrase an anti-collision mechanism, not a security feature.

Subsequent ELRS v3 changes relevant to these vulnerabilities:

| Issue | ELRS v3 Status | Assessment |
|-------|---------------|------------|
| UID bytes in SYNC | **Still present** — SYNC still carries UID[3:5] for receiver validation | Unmitigated |
| CRC from leaked bytes | **Still present** — CRC14 init still derived from UID[4:5] | Unmitigated |
| Weak FHSS RNG | **Partially addressed** — FHSS generation was reworked but fundamental approach (deterministic from UID) unchanged | Partially mitigated |
| No encryption | **Still no encryption** — OTA packets are CRC-protected only | Unmitigated |
| Model match bypass | **Model match is only 6 bits** (64 values), and `modelId=0xFF` disables it entirely | Unmitigated |

**The binding phrase remains the only barrier to control link hijack.** For D's hardware, the confirmed UID[3:5] = `11:18:5b` (triple-confirmed via E8 vendor read, passive SYNC extraction, and RX config MSP bytes 35-37). This leaves only UID[0:2] unknown — a 24-bit brute-force space, which is computationally trivial (~16M candidates).

**Source:** [NCC Group Technical Advisory](https://www.nccgroup.com/research/technical-advisory-expresslrs-vulnerabilities-allow-for-hijack-of-control-link/)

**Confirmed against D's hardware:** Yes — UID[3:5] already extracted. Full UID recovery requires passive RF observation or brute-force of UID[0:2] (no RF gear needed for computational brute-force, but validation requires RF).

---

## 4. ELRS WiFi Firmware Update: No Signing, CSRF RCE

### WiFi Update Mode Architecture

ELRS receivers and transmitters with ESP8285/ESP32 co-processors enter WiFi update mode when:
- No connection is established for ~60 seconds (auto WiFi mode)
- A specific MSP command forces WiFi mode entry
- The user triggers it via button sequence

In WiFi mode, the device opens an HTTP web server (typically on `10.0.0.1`) that accepts firmware uploads via a simple POST endpoint — `/update` or `/upload`.

### Security Weaknesses

**No firmware signing or hash verification:** ELRS WiFi updates accept any valid ESP binary. There is no cryptographic signature, no hash check against a known-good value, and no certificate pinning. The update endpoint accepts whatever binary is POSTed.

**No authentication on the update endpoint:** The web server has no login, no API key, and no session token. Anyone on the WiFi network can POST a firmware image.

**CSRF vulnerability (CVE-2025-53540):** The Arduino-ESP32 `WebServer` library (used by ELRS and many other ESP32 projects) has a CSRF vulnerability in its firmware update endpoints. An attacker who can lure a user on the same network to visit a malicious page can trigger a firmware flash via a cross-origin POST request, achieving remote code execution.

From the advisory (GHSA-9vfw-wx65-c872):
> The `/update` endpoint in the `WebServer` library accepts firmware uploads via HTTP POST without any CSRF protection. A crafted page served from a different origin can submit a firmware image to the update endpoint via JavaScript, replacing the device firmware with attacker-controlled code.

### Attack Chain for ELRS Receivers

1. Force the ELRS receiver into WiFi mode (via MSP command `0x000E` over the air, or wait for auto-WiFi timeout)
2. Connect to the receiver's WiFi AP (default SSID pattern: `ExpressLRS RX`)
3. POST a malicious firmware binary to the update endpoint
4. The receiver flashes the attacker's firmware — full code execution on the ESP

**Relevance to TinyHawk III Plus:** The TinyHawk's SPI-integrated ELRS (SX1280 on STM32F411) does NOT have a separate ESP chip, so the WiFi update path **does not apply to the receiver**. However, the E8 controller likely has an ESP32-based ELRS module — if the E8's ELRS enters WiFi mode, this attack applies to the controller side.

**Confirmed against D's hardware:** Unconfirmed for E8 — requires determining whether the E8's ELRS module has WiFi capability (suspected but not confirmed). Not applicable to the TinyHawk receiver (no ESP chip).

---

## 5. STM32F411 Bootloader/DFU Attack Surface

The TinyHawk III Plus FC uses an STM32F411CEU6. This chip has a built-in system bootloader in ROM that supports DFU (Device Firmware Update) via USB and UART.

### Bootloader Entry Methods

| Method | How | Physical Access Required |
|--------|-----|------------------------|
| BOOT0 pin high + reset | Hold BOOT0 high, trigger NRST | Yes — requires access to boot pads |
| Option bytes | Set `nBOOT0` option byte via SWD/JTAG | Yes — requires debug access |
| Application jump | Firmware jumps to system bootloader address (`0x1FFF0000`) | No — if MSP command triggers it |
| BFB (Boot From Bootloader) | Betaflight CLI `bl` command enters bootloader | No — via MSP/CLI |

### Security Analysis

**Read-Out Protection (RDP):**
- **Level 0 (default):** No protection — firmware can be read via debug/bootloader. Most hobby FC boards ship at RDP Level 0.
- **Level 1:** Blocks debug access and bootloader read commands. Firmware read is blocked, but flash erase + reprogram is still possible.
- **Level 2:** Permanent — disables SWD/JTAG and system bootloader entirely. Irreversible.

**The TinyHawk III Plus almost certainly ships at RDP Level 0.** Hobby FC manufacturers do not enable RDP because users need to flash firmware updates. This means:

1. **Full firmware extraction** is possible via SWD (SWDIO/SWCLK pins on the FC) or via the bootloader UART/USB interface
2. **Firmware replacement** is trivial — DFU mode accepts any valid binary
3. **No secure boot chain** — the STM32F411 has no hardware root of trust or secure boot (unlike STM32L4/U5/H5 families)

### Voltage Glitch Attack (RDP Level 1 Bypass)

Even if RDP Level 1 were enabled, research demonstrates that STM32F4 family chips are vulnerable to voltage fault injection:

- Anvil Secure demonstrated glitching the STM32F401CC (same F4 family) to bypass RDP Level 1 read protection
- Equipment: ChipWhisperer-Lite (~$250), wire to VCAP_1 pin, bypass capacitor removal
- The attack targets the bootloader's RDP check during UART read commands
- Success requires physical access and ~6 hours of glitching, but is repeatable
- After bypass, full firmware dump is possible

**Source:** [Anvil Secure: Glitching STM32 Read Out Protection](https://www.anvilsecure.com/blog/glitching-stm32-read-out-protection-with-voltage-fault-injection.html)

### DFU as Persistence Mechanism

If an attacker achieves MSP command execution (e.g., via ELRS OTA), they can potentially:
1. Send `MSP_REBOOT` with argument to enter bootloader mode
2. Flash modified firmware that includes a backdoor
3. The drone now runs attacker-controlled code persistently

The Betaflight CLI `bl` command and `MSP_REBOOT` (command 68, with `MSP_REBOOT_BOOTLOADER_ROM` argument 1) can trigger bootloader entry. This is reachable via the MSP-over-ELRS OTA path documented in the existing Betaflight 4.4.2 vulnerability audit.

**Confirmed against D's hardware:** Partially — MSP API 1.45 is confirmed, `MSP_REBOOT` is in the command surface. RDP level is unknown but almost certainly Level 0. Full confirmation requires SWD probe or bootloader UART access (bench test).

---

## 6. VTX SmartAudio Protocol

### Protocol Summary

The TinyHawk III Plus has a TBS-protocol VTX connected via UART2 using SmartAudio. The protocol specification (TBS SmartAudio Rev 09) reveals a completely unauthenticated command interface:

**Physical layer:** Software UART at 4800 bps, half-duplex, single-wire

**Frame format:** `[0xAA 0x55] [Command] [Length] [Payload] [CRC8]`
- CRC8 polynomial: 0xD5
- No authentication, no encryption, no session tokens

**Available commands (all unauthenticated):**

| Command | Code | Effect |
|---------|------|--------|
| Get Settings | 0x03 | Read current frequency, power, mode — information disclosure |
| Set Power | 0x05 | Change TX power (0 dBm = Pit Mode, up to max rated power) |
| Set Channel | 0x07 | Change video frequency (5.8 GHz band, 40 channels) |
| Set Frequency | 0x09 | Set arbitrary frequency 5000-6000 MHz |
| Set Mode | 0x0B | Enable/disable Pit Mode, lock/unlock VTX |

The only access control is a "lock/unlock" command (Set Mode with value 0x08), but the spec does not describe any credential requirement — it appears to be a simple flag.

### Attack Scenarios

**Scenario A: VTX Disruption via MSP-to-SmartAudio Pivot**

If an attacker achieves MSP command execution via the ELRS OTA path:
1. Use `MSP_SET_VTX_CONFIG` (or direct UART write via MSP) to send SmartAudio commands
2. Set VTX to Pit Mode (0 dBm) — pilot loses video feed
3. Change frequency to an unexpected channel — pilot loses video
4. Set max power — potential regulatory violation, interference with other pilots

Betaflight routes VTX control through its vtxSmartAudio driver, which accepts MSP commands and translates them to SmartAudio frames on UART2. The entire SmartAudio command set is reachable from MSP.

**Scenario B: Physical UART Injection**

With physical access to the VTX UART line, an attacker can directly inject SmartAudio frames without needing to compromise the FC first. At 4800 bps with no authentication, this is trivial.

**Scenario C: Cross-Protocol Pivot**

The SmartAudio bus is bidirectional — the VTX responds to queries. If an attacker can read SmartAudio responses (via MSP telemetry or physical tap), they gain:
- Current VTX frequency and power settings
- VTX firmware version
- Lock/unlock status

**Source:** [TBS SmartAudio Protocol Specification Rev 09](https://www.team-blacksheep.com/media/files/tbs_smartaudio_rev09.pdf)

**Confirmed against D's hardware:** Partially — UART2 = TBS SmartAudio is confirmed from live serial topology probing. Full exploitation requires MSP command execution (achievable via the documented ELRS OTA path) or physical UART access.

---

## 7. Telemetry Downlink Data Leakage & Blackbox Exfiltration

### CRSF Telemetry Leakage

The CRSF protocol supports bidirectional telemetry. The drone sends the following data back to the controller over the ELRS link:

| Telemetry Frame | Data Leaked | Privacy Impact |
|----------------|-------------|----------------|
| GPS | Latitude, longitude, altitude, speed, heading, satellite count | Real-time position tracking |
| Battery | Voltage, current, mAh consumed, remaining percentage | Flight endurance estimation |
| Attitude | Pitch, roll, yaw | Flight behavior analysis |
| Flight Mode | Current mode (ACRO, ANGLE, HORIZON, etc.) | Operational state |
| Barometric Altitude | Pressure altitude | Altitude tracking |
| Variometer | Climb/descent rate | Flight pattern analysis |

**Passive interception:** Because ELRS v3 has no encryption, an eavesdropper who knows (or has brute-forced) the UID can decode all telemetry. Given that UID[3:5] is broadcast in every SYNC packet, telemetry interception requires the same UID recovery as control link hijack.

**Active exfiltration via MSP:** If an attacker achieves MSP command execution, they can use telemetry MSP commands to exfiltrate data that is not normally part of the telemetry stream:
- `MSP_RAW_GPS` (106): GPS coordinates, altitude, speed
- `MSP_COMP_GPS` (107): Distance/direction to home
- `MSP_WP` (118): Waypoint data
- `MSP_BOXIDS` (119): Active mode flags
- `MSP_BLACKBOX_CONFIG` (80): Blackbox logging configuration

### Blackbox Data

Betaflight's blackbox logger stores high-rate flight data to onboard flash or SD card. This includes:
- All RC channel inputs (stick positions, switch states)
- PID controller outputs
- GPS position/velocity (if GPS equipped)
- Motor outputs
- Battery telemetry
- Timestamps

Blackbox data is readable via `MSP_DATAFLASH_READ` (command 0x0070). An attacker with MSP access could exfiltrate blackbox logs containing historical flight data, GPS tracks, and controller input patterns.

**Confirmed against D's hardware:** GPS telemetry leakage is confirmed as a theoretical path. The TinyHawk III Plus does not have a GPS module, so GPS-specific exfiltration is not applicable to this target. Other telemetry (battery, attitude, flight mode) is confirmed active. Blackbox configuration unknown — requires `MSP_BLACKBOX_CONFIG` query.

---

## 8. Multi-Modem FHSS Drone Takeover (Kang et al. 2025)

### Research Summary

A 2025 paper by Kang et al. in the ETRI Journal describes a novel FHSS drone takeover technique using multiple synchronized SDR modems:

**Approach:** Instead of predicting or brute-forcing the FHSS sequence, the researchers deployed multiple SDR receivers, each monitoring a subset of the hopping channels simultaneously. By correlating received signal timing across modems, they achieved:
- Real-time FHSS sequence reconstruction
- Precision spoofing that injects packets at exactly the right time/frequency
- Takeover of the control link without prior knowledge of the hopping sequence

**Key innovation:** The multi-modem approach trades hardware (multiple SDRs) for computational complexity. Rather than cracking the FHSS algorithm, it directly observes and follows the hop pattern in real time.

**Relevance to ELRS/SX1280:** ELRS uses 2.4 GHz FHSS with 80 channels (per existing research: 240 hops / 1.6s, sync channel every 0.533s). The multi-modem approach is applicable in principle, though ELRS's 150 Hz packet rate and narrow timing windows make it more challenging than the protocols tested in the paper.

**Source:** [Kang et al., "Multi-modem-based FHSS-drone takeover with precision spoofing," ETRI Journal, 2025](https://onlinelibrary.wiley.com/doi/10.4218/etrij.2024-0369)

**Confirmed against D's hardware:** Not confirmed — requires multiple SDR receivers and real-time processing capability. This is an advanced attack requiring significant hardware investment, but validates the theoretical feasibility of FHSS takeover without prior key knowledge.

---

## 9. Remote ID Spoofing & OpenDroneID Vulnerabilities

### Background

Remote ID (RID) regulations require drones to broadcast identification and location data. The OpenDroneID standard specifies broadcast via WiFi Neighbor Awareness Networking (NAN) or Bluetooth 4/5 Legacy Advertising.

### Vulnerabilities

**CVE-2024-22519 (OpenDroneID OSM 3.5.1):** A vulnerability in the OpenDroneID Open Source Module allows attackers to inject crafted Remote ID messages. The protocol has no authentication — any device can broadcast RID messages claiming to be any drone.

**droneRemoteIDSpoofer (Swiss Cyber Defence Campus):** A public tool demonstrates Bluetooth and WiFi-based Drone Remote ID spoofing, showing:
- Creation of "ghost" drones at arbitrary GPS coordinates
- Impersonation of real drones by cloning their broadcast IDs
- Flooding attacks creating hundreds of phantom drones
- Costs as little as $20 (ESP32 dev board)

**Source:** [droneRemoteIDSpoofer on GitHub](https://github.com/cyber-defence-campus/droneRemoteIDSpoofer)

### Relevance

While the TinyHawk III Plus does not have Remote ID (it's a sub-250g FPV quad), this attack surface is relevant for:
- Defensive awareness: understanding how an adversary could spoof D's drone's identity if RID is added
- Counter-drone research: RID spoofing undermines the primary identification mechanism
- If the E8 controller has WiFi/BLE (suspected ESP32), it could potentially be used as a RID spoofer

**Confirmed against D's hardware:** Not applicable to current hardware (no RID module). Relevant as defensive research context.

---

## 10. Supply-Chain Security

### ELRS Build Toolchain

ELRS firmware is built using PlatformIO on top of the Arduino-ESP32 framework. The supply chain includes:

| Component | Risk | Assessment |
|-----------|------|-----------|
| PlatformIO package registry | Dependency confusion, typosquatting | Medium — packages are community-maintained |
| Arduino-ESP32 core | Known CVEs (see §11), large codebase | High — CVE-2025-53540 affects firmware update endpoints |
| ESP-IDF (underlying framework) | Espressif-maintained, better audited | Low-Medium |
| ELRS GitHub releases | Binary releases, source available | Low — reproducible builds possible |
| Hardware vendor firmware | EMAX may ship modified ELRS builds | Unknown — no way to verify without extraction |

### Betaflight Build Toolchain

| Component | Risk | Assessment |
|-----------|------|-----------|
| ARM GCC toolchain | Well-audited, widely used | Low |
| Betaflight configurator (Electron app) | Web-based with USB serial access | Medium — Electron supply chain risks |
| Cloud build service | Betaflight offers cloud builds for custom targets | Medium — build server compromise would affect many users |
| STM32 HAL libraries | ST-maintained | Low |

### Specific Concern: Hardware Vendor Firmware Modifications

EMAX ships the TinyHawk III Plus with pre-flashed Betaflight. There is no mechanism to verify that the flashed firmware matches the official Betaflight 4.4.2 release:
- No secure boot chain on STM32F411
- No firmware attestation mechanism
- Binary diff against official build would detect modifications, but requires firmware extraction first

**Confirmed against D's hardware:** Supply chain analysis is based on public information. Firmware verification requires extraction via SWD or DFU (bench test).

---

## 11. Arduino-ESP32 CSRF Firmware Update RCE (CVE-2025-53540)

### Vulnerability Details

**CVE:** CVE-2025-53540
**Advisory:** GHSA-9vfw-wx65-c872
**Affected:** espressif/arduino-esp32 WebServer library
**CVSS:** High (RCE via CSRF)

The Arduino-ESP32 `WebServer` library's firmware update endpoint (`/update`) accepts HTTP POST requests with firmware binaries **without any CSRF protection**. An attacker can:

1. Host a malicious web page
2. Use JavaScript to POST a firmware binary to the target's update endpoint
3. If the victim's browser can reach the ESP device (same network), the firmware is replaced

This affects any ESP32/ESP8285 device using the Arduino-ESP32 WebServer for OTA updates — which includes **ELRS receivers and transmitters with WiFi update capability**.

### Attack Chain

For an ELRS device in WiFi update mode:
1. Device enters WiFi mode (auto-timeout, MSP command, or button)
2. Attacker is on the same WiFi network (or forces the device to connect to attacker-controlled AP)
3. Attacker serves page that triggers CSRF POST with malicious firmware
4. Device flashes attacker's firmware — full code execution

**Confirmed against D's hardware:** Applies if the E8 controller's ELRS module has WiFi update capability (suspected but unconfirmed). Does not apply to the TinyHawk receiver (no ESP chip).

---

## 12. MSP Command Whitelist Gap (Betaflight Issue #15752)

### The Problem

Betaflight issue #15752 documents a **missing MSP command whitelist** for VTX serial ports. When a serial port is configured for MSP (e.g., for DisplayPort OSD), it accepts the **full MSP command set** — not just the OSD-related commands.

This means a device connected to a VTX serial port (e.g., a Walksnail/DJI/HDZero digital VTX with MSP) can send **any MSP command**, including:
- `MSP_SET_MOTOR` — direct motor control
- `MSP_SET_RAW_RC` — override RC channel inputs
- `MSP_REBOOT` — reboot or enter bootloader
- `MSP_SET_CF_SERIAL_CONFIG` — reconfigure serial ports
- `MSP_EEPROM_WRITE` — persist configuration changes

### Security Implication

If an attacker can compromise a digital VTX (which often runs Linux or RTOS with WiFi), they can pivot through the MSP serial connection to gain full control of the flight controller. The VTX serial port was intended only for OSD rendering, but exposes the complete MSP attack surface.

**Source:** [Betaflight Issue #15752](https://github.com/betaflight/betaflight/issues/15752)

**Confirmed against D's hardware:** The TinyHawk III Plus uses an analog VTX with SmartAudio (not MSP-based digital VTX), so this specific issue is less directly applicable. However, it demonstrates a systemic MSP trust boundary failure that affects the MSP-over-ELRS path too — there is no command whitelist on the ELRS MSP path either.

---

## 13. Applicability Matrix

| Attack Surface | Applies to TinyHawk III Plus | Applies to E8 Controller | Requires RF Gear | Requires Physical Access | Status |
|---------------|------------------------------|--------------------------|-------------------|-------------------------|--------|
| Semtech LR11xx CVEs (SX1280 analogue) | Possible (needs SPI audit) | Possible (needs SPI audit) | No | Yes (SPI probe) | Unconfirmed |
| SX1280 SPI register abuse | Yes (SPI bus 3 confirmed) | Unknown | No | Yes (SPI probe) | Unconfirmed |
| ELRS UID recovery (NCC Group) | Yes (UID[3:5] confirmed) | Yes (same binding) | Yes (for validation) | No | Partially confirmed |
| ELRS WiFi firmware update | **No** (no ESP chip) | Likely yes (suspected ESP) | No | No | Unconfirmed for E8 |
| STM32F411 DFU/bootloader | Yes (STM32F411 confirmed) | N/A (different MCU) | No | Yes (boot pads/SWD) | Confirmed architecture |
| VTX SmartAudio injection | Yes (UART2 confirmed) | N/A (controller side) | No | Via MSP OTA or physical | Confirmed topology |
| Telemetry data leakage | Yes (CRSF telemetry active) | Yes (receives telemetry) | Yes (passive sniff) | No | Confirmed |
| Multi-modem FHSS takeover | Yes (ELRS FHSS target) | N/A (controller is TX) | Yes (multiple SDRs) | No | Theoretical |
| Remote ID spoofing | No (no RID module) | Possible (if ESP-based) | No | No | N/A for current hardware |
| Supply chain verification | Yes | Yes | No | Yes (firmware extract) | Unconfirmed |
| Arduino-ESP32 CSRF (CVE-2025-53540) | **No** (no ESP) | Likely yes | No | No | Unconfirmed for E8 |
| MSP command whitelist gap | Indirect (ELRS MSP path) | N/A | No | No | Confirmed (systemic) |

---

## 14. Recommended Next Steps

### Zero-RF-Gear Steps (Bench/Wired Only)

1. **SX1280 SPI register audit:** Use a logic analyzer on SPI bus 3 to enumerate the SX1280's register space and identify any unrestricted write regions analogous to the LR11xx `WriteRegMemMask32`
2. **STM32F411 RDP level check:** Connect via SWD to determine the current RDP level (expected: Level 0)
3. **Firmware extraction via DFU:** Enter bootloader mode and dump the flash — compare against official Betaflight 4.4.2 release for supply-chain verification
4. **Blackbox configuration query:** Send `MSP_BLACKBOX_CONFIG` to determine logging configuration and available storage
5. **SmartAudio VTX probing:** Send SmartAudio Get Settings command via UART2 to confirm VTX model, firmware version, and current configuration

### Requires Passive RF Scan (WiFi + BLE)

6. **E8 controller WiFi scan:** Determine if the E8 broadcasts any WiFi AP during normal operation or timeout (would confirm ESP-based ELRS module and enable WiFi attack path)
7. **E8 BLE scan:** Check for BLE advertisements from the E8 (would confirm ESP32 MCU and enable CVE-2025-27840 testing)
8. **ELRS telemetry interception:** With known UID, passively decode telemetry downlink to confirm data leakage scope

### Advanced (Multiple SDRs Required)

9. **FHSS sequence real-time tracking:** Validate multi-modem approach against ELRS 150 Hz / 80-channel hopping
10. **Full UID brute-force:** With UID[3:5] known, computationally generate all 16M UID[0:2] candidates and validate via RF

---

## References

- [LoRa under attack: running our own code in secure LoRaWAN Semtech LR11xx chips](https://b.re146.dev/lora-under-attack/) — CVE-2025-14857, CVE-2025-14858
- [Semtech Product Security Advisory SEM-PSA-2026-001](https://www.semtech.com/uploads/bulletins/SEM-PSA-2026-001.pdf)
- [NCC Group: ExpressLRS Vulnerabilities Allow for Hijack of Control Link](https://www.nccgroup.com/research/technical-advisory-expresslrs-vulnerabilities-allow-for-hijack-of-control-link/)
- [TBS SmartAudio Protocol Specification Rev 09](https://www.team-blacksheep.com/media/files/tbs_smartaudio_rev09.pdf)
- [Anvil Secure: Glitching STM32 Read Out Protection](https://www.anvilsecure.com/blog/glitching-stm32-read-out-protection-with-voltage-fault-injection.html)
- [Kang et al., "Multi-modem-based FHSS-drone takeover with precision spoofing," ETRI Journal, 2025](https://onlinelibrary.wiley.com/doi/10.4218/etrij.2024-0369)
- [droneRemoteIDSpoofer — Swiss Cyber Defence Campus](https://github.com/cyber-defence-campus/droneRemoteIDSpoofer)
- [CVE-2025-53540 / GHSA-9vfw-wx65-c872: Arduino-ESP32 CSRF Firmware Update RCE](https://github.com/espressif/arduino-esp32/security/advisories/GHSA-9vfw-wx65-c872)
- [Betaflight Issue #15752: MSP Command Whitelist for VTX Ports](https://github.com/betaflight/betaflight/issues/15752)
- [CVE-2025-14859: Semtech LR11xx AES-CBC Preimage](https://app.opencve.io/cve/CVE-2025-14859)
- [STM32 Security Introduction — ST Application Note](https://www.st.com/resource/en/application_note/dm00493651-introduction-to-stm32-microcontrollers-security-stmicroelectronics.pdf)
- [Securing the Low-Altitude Economy: A Survey (2026)](http://scis.scichina.com/en/2026/141202.pdf)
