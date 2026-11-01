# New Targetable Attack Surfaces — Round 2

**Date:** 2026-10-29
**Scope:** Authorized defensive security research on owned hardware (TinyHawk III Plus + E8 controller)
**Status:** Research/analysis only — no live exploitation attempted
**Predecessor:** `new-attack-surfaces-deep-research-2026-10-29.md` (PR #66)

This document extends the first round of new attack surface research with additional areas not yet covered, focusing on protocol-level injection, sensor spoofing, ELRS ecosystem evolution, and supply-chain depth.

---

## Table of Contents

1. [MSP DisplayPort OSD Injection](#1-msp-displayport-osd-injection)
2. [ELRS Backpack: ESP-NOW Wireless Attack Surface](#2-elrs-backpack-esp-now-wireless-attack-surface)
3. [ExpressLRS v4.0 Security-Relevant Changes](#3-expresslrs-v40-security-relevant-changes)
4. [ELRS Binding Phrase: Entropy Analysis & Offline Cracking](#4-elrs-binding-phrase-entropy-analysis--offline-cracking)
5. [ESP-IDF / ESP-NOW Integer Underflow RCE (CVE-2025-52471)](#5-esp-idf--esp-now-integer-underflow-rce)
6. [ESP-IDF BLE Provisioning Vulnerabilities (CVE-2026-25507, CVE-2026-25508, CVE-2026-45542)](#6-esp-idf-ble-provisioning-vulnerabilities)
7. [GPS Spoofing & Sensor Injection Attacks](#7-gps-spoofing--sensor-injection-attacks)
8. [Betaflight Configurator Electron App Attack Surface](#8-betaflight-configurator-electron-app-attack-surface)
9. [Electron Framework CVE-2026-70601: Context Isolation Bypass](#9-electron-framework-cve-2026-70601)
10. [ELRS WiFi Firmware Update: Expanded Analysis](#10-elrs-wifi-firmware-update-expanded-analysis)
11. [MSP Command Surface: Dangerous Commands Audit](#11-msp-command-surface-dangerous-commands-audit)
12. [Applicability Matrix](#12-applicability-matrix)
13. [Combined Next Steps (Rounds 1 + 2)](#13-combined-next-steps)

---

## 1. MSP DisplayPort OSD Injection

### Protocol Overview

MSP DisplayPort is the protocol used by digital FPV systems (DJI, Walksnail, HDZero) to render OSD elements from the flight controller onto the pilot's goggles. It operates over a dedicated UART serial link using MSP framing.

**Key MSP commands:**

| Command | ID | Function |
|---------|-----|----------|
| MSP_DISPLAYPORT | 182 | Umbrella command with sub-commands |
| MSP_SET_OSD_CANVAS | 188 | VTX reports canvas size, enables HD mode |
| MSP_OSD_CANVAS | 189 | Request canvas dimensions |

**Sub-commands (within MSP_DISPLAYPORT):**

| Sub-command | ID | Function |
|-------------|-----|----------|
| HEARTBEAT | 0 | Keepalive — prevents "disconnected" state |
| RELEASE | 1 | Returns rendering to VTX local display |
| CLEAR_SCREEN | 2 | Clears display |
| WRITE_STRING | 3 | Write text at row/column with attributes |
| DRAW_SCREEN | 4 | Commit buffered frame to display |
| OPTIONS | 5 | Set resolution (SD/HD) — INAV/ArduPilot only |
| SYS | 6 | Position system elements |

### Security Analysis

**No authentication or integrity checking:** MSP DisplayPort has no authentication. Any device that can send MSP frames on the OSD UART can control what the pilot sees.

**Attack Scenario: OSD Spoofing via MSP Injection**

If an attacker achieves MSP command execution (via ELRS OTA → CRSF → MSP path):

1. **False warnings:** Write "LOW BATTERY" or "FAILSAFE" to the OSD, causing the pilot to land prematurely
2. **Attitude/altitude spoofing:** Display false altitude, speed, or heading values
3. **Social engineering:** Display "FIRMWARE UPDATE REQUIRED" with instructions to connect to attacker's WiFi AP
4. **Vision denial:** Repeatedly clear and redraw the screen to create flickering/unreadable display
5. **False GPS coordinates:** Display incorrect home arrow direction or GPS coordinates

**Input validation gaps (from Betaflight docs):**
- String writes are limited to 30 characters (NULL-terminated) — but behavior for overlong/unterminated strings is unspecified
- Row/column are uint8 — out-of-range coordinates have unspecified behavior
- Attribute byte has reserved bits that "must be 0" — behavior when set is unspecified
- System element IDs 0-10 are defined — behavior for invalid IDs is unspecified

These unspecified behaviors represent potential fuzzing targets for memory corruption on the VTX/goggles display processor.

**Source:** [Betaflight DisplayPort MSP Extensions](https://betaflight.com/docs/development/API/DisplayPort)

**Confirmed against D's hardware:** The TinyHawk III Plus uses an analog VTX (SmartAudio), not MSP DisplayPort. However, if the pilot uses digital goggles with an adapter or upgrades to a digital VTX, this becomes directly applicable. The MSP framing and sub-command structure is the same regardless of VTX type — the attack requires MSP command execution, which is achievable via the documented ELRS OTA path.

---

## 2. ELRS Backpack: ESP-NOW Wireless Attack Surface

### Architecture

The ELRS Backpack system creates a wireless bridge between the TX module and the video receiver (VRX) in the pilot's goggles using ESP-NOW:

```
ELRS TX Module ↔ TX Backpack (ESP8285) ──ESP-NOW──→ VRX Backpack (ESP8285/ESP-01F) ↔ Video RX
```

The backpack's purpose is automatic VTX channel synchronization: when the pilot changes the video channel via the Lua script's "VTX Administrator," the backpack relays this to the VRX so both stay on the same frequency.

### Security Weaknesses

**ESP-NOW has no encryption by default:** The ELRS Backpack documentation describes binding via a "Binding Phrase" (the same phrase used for the control link), but does not describe any ESP-NOW encryption. ESP-NOW supports optional CCMP encryption with a pre-shared key, but the ELRS Backpack implementation may not use it.

**WiFi AP mode with no authentication:** The backpack enters WiFi mode:
- On a single button press
- After 120 seconds in binding mode with no bind packets received
- The WiFi AP serves a firmware update interface (same Arduino-ESP32 WebServer vulnerability surface as described in Round 1, CVE-2025-53540)

**Attack scenarios:**

1. **ESP-NOW channel injection:** If ESP-NOW traffic is unencrypted, an attacker within WiFi range (~100m) can inject ESP-NOW frames to change the VRX frequency, causing the pilot to lose video
2. **Backpack firmware replacement:** Force the backpack into WiFi mode (wait for timeout or interfere with binding), then flash malicious firmware via the web update endpoint
3. **ESP-NOW replay:** Capture and replay channel-change commands (related to CVE-2024-42483 research already in the project, but now applied to the Backpack subsystem specifically)

### New CVE: ESP-NOW Integer Underflow (CVE-2025-52471)

This is covered in detail in §5 below, but note: the ELRS Backpack uses ESP-NOW over ESP-IDF, making it directly affected by CVE-2025-52471 if running vulnerable ESP-IDF versions. This is a network-reachable RCE with CVSS 9.8.

**Confirmed against D's hardware:** Unconfirmed — requires determining whether the E8 controller has a Backpack-capable ESP module. The TinyHawk III Plus receiver (SPI-integrated, no ESP chip) does not have a backpack. If the E8 has an ESP-based backpack, all three attack scenarios apply.

---

## 3. ExpressLRS v4.0 Security-Relevant Changes

ELRS v4.0 was released in 2026 with significant changes. Security-relevant items:

### Binding Phrase via Lua Script

**New in v4.0:** The binding phrase can now be changed directly from the radio's EdgeTX/OpenTX Lua script, without connecting over WiFi. This is a convenience feature, but it means:
- The binding phrase transits over the internal serial connection between the radio and the ELRS TX module
- If the radio is compromised (e.g., via a malicious EdgeTX model file or Lua script), the binding phrase can be extracted
- The binding phrase is stored in `hardware.json` on the ELRS module's filesystem — this file is downloadable via the WebUI and should be treated as sensitive

### Compatibility Breaking: STM32/SPI Not Supported

**Critical for D's hardware:** ELRS v4.0 drops support for STM32-based hardware and SPI receivers. The TinyHawk III Plus uses an SPI-integrated ELRS receiver (SX1280 on STM32F411), which means:
- The TinyHawk is **locked to ELRS 3.x** (via Betaflight's built-in ELRS stack)
- All ELRS 3.x security weaknesses (NCC Group findings, no encryption, UID leak) remain permanently applicable
- Betaflight 2026.06 reportedly adds SPI support for ELRS 4.0, but the TinyHawk III Plus running Betaflight 4.4.2 does not have this
- Any future security improvements in ELRS v4+ will **not reach** the TinyHawk's SPI receiver unless Betaflight is updated

### No Encryption Added

ELRS v4.0 still does not add encryption to the OTA protocol. The FHSS + CRC approach remains the only protection.

**Source:** [Oscar Liang: ExpressLRS 4.0 Released](https://oscarliang.com/expresslrs-4/)

**Confirmed against D's hardware:** The TinyHawk III Plus is confirmed running Betaflight 4.4.2 with ELRS v3.x SPI integration. It cannot upgrade to ELRS v4.0 without a Betaflight update. The E8 controller may be upgradeable to v4.0 if it uses a standalone ELRS TX module (not SPI-integrated).

---

## 4. ELRS Binding Phrase: Entropy Analysis & Offline Cracking

### How the Binding Phrase Becomes a UID

The ELRS binding phrase is processed as follows:
1. User enters a text string (the "binding phrase")
2. The string is hashed with **MD5** (128 bits)
3. The first 6 bytes of the MD5 hash become the **UID** (48 bits)
4. UID[2:5] seed the FHSS sequence
5. UID[4:5] compute the CRC14 initializer

### Entropy Analysis

**Typical binding phrases** used by hobbyists:
- Callsign-based: "KD9XYZ" (6 chars, ~30 bits)
- Simple words: "myquad", "racer1", "caddx" (5-8 chars, ~25-40 bits)
- Default/common: "expresslrs", "elrs", "test" (<20 bits — trivially guessable)
- Complex: "MyS3cur3Phr@se!" (16 chars, ~80+ bits)

**MD5 cracking speed (2026 hardware):**

| Hardware | MD5 Hashes/Second | Time for 2^48 (full UID space) |
|----------|-------------------|-------------------------------|
| Single RTX 4090 | ~70 billion/s | ~4,000 seconds (~1 hour) |
| 8x RTX 4090 rig | ~560 billion/s | ~500 seconds (~8 minutes) |
| Cloud GPU cluster | ~5 trillion/s | ~56 seconds |

However, the attacker doesn't need to crack the full 48-bit UID space. With UID[3:5] known from SYNC packets (confirmed for D's hardware as `11:18:5b`):

**Reduced search space: UID[0:2] only (24 bits = 16,777,216 candidates)**

| Hardware | Time for 2^24 | Notes |
|----------|---------------|-------|
| Single RTX 4090 | <1 millisecond | MD5 is trivially fast |
| Laptop CPU | ~200 milliseconds | Even without GPU |

**But this isn't a hash crack — it's a direct computation.** The attacker generates MD5(candidate_phrase), takes the first 6 bytes, and checks if bytes [3:5] match `11:18:5b`. With a dictionary of common binding phrases, a match can be found almost instantly.

### Practical Attack: Dictionary-Based Binding Phrase Recovery

1. Collect common binding phrases from forums, YouTube videos, default settings
2. Hash each with MD5, extract first 6 bytes
3. Check if bytes [3:5] match the target's known `11:18:5b`
4. On match: full UID is known → compute FHSS sequence → compute CRC14 → control link hijack

**Alternative: Pure brute-force of UID[0:2]**

Even without recovering the binding phrase, an attacker who knows UID[3:5] needs only to brute-force UID[0:2] (24 bits). With an SDR, they can test candidates by generating FHSS sequences and attempting SYNC. At 150 Hz packet rate, testing ~1000 candidates/second is feasible, giving full UID recovery in ~4.6 hours.

**Confirmed against D's hardware:** UID[3:5] = `11:18:5b` is triple-confirmed. Dictionary attack is purely computational — no hardware needed. SDR brute-force requires RF gear.

---

## 5. ESP-IDF / ESP-NOW Integer Underflow RCE

### CVE-2025-52471

**Severity:** CVSS 3.1: **9.8 CRITICAL** (Network/Low/None/None)
**Component:** ESP-NOW protocol implementation in ESP Wi-Fi component
**Type:** CWE-191 Integer Underflow → out-of-bounds memory access → arbitrary memory write
**Affected:** ESP-IDF 5.1.6, 5.2.5, 5.3.3, 5.4.1

**Description:** Insufficient validation of user-supplied data length in the ESP-NOW packet receive function. When a crafted ESP-NOW frame with a manipulated length field is received, an integer underflow occurs in the packet processing, leading to:
1. Out-of-bounds memory read/write
2. On systems without memory protection (e.g., ESP8285/ESP32 without MPU): **remote code execution**

**Attack requirements:**
- Attacker must be within ESP-NOW range (~100m WiFi range)
- Target device must be running ESP-NOW receive callback (`esp_now_register_recv_cb()`)
- No authentication required — ESP-NOW frames from any sender can trigger the vulnerability

### Relevance to ELRS Ecosystem

| Component | Uses ESP-NOW | Affected |
|-----------|-------------|----------|
| ELRS TX Module (ESP32-based) | Yes (Backpack comms) | Yes, if ESP-IDF version is vulnerable |
| ELRS RX Module (ESP-based) | Potentially (Backpack) | Yes, if present and ESP-IDF version is vulnerable |
| ELRS TX Backpack (ESP8285) | Yes (primary protocol) | **High risk** — always listening for ESP-NOW |
| ELRS VRX Backpack (ESP8285/ESP-01F) | Yes (primary protocol) | **High risk** — always listening for ESP-NOW |
| TinyHawk III Plus receiver | No (SPI-integrated, no ESP) | **Not affected** |

**Attack chain:**
1. ELRS Backpack device is powered on and listening for ESP-NOW frames
2. Attacker within WiFi range sends crafted ESP-NOW frame with manipulated length field
3. Integer underflow in receive callback causes out-of-bounds write
4. On ESP8285 (no memory protection): arbitrary code execution
5. Attacker now controls the Backpack ESP — can inject VTX channel changes, extract binding phrase from flash, or pivot to other attacks

**Source:** [NVD CVE-2025-52471](https://nvd.nist.gov/vuln/detail/CVE-2025-52471)

**Confirmed against D's hardware:** Requires confirming whether the E8 controller has an ESP-based Backpack module running ESP-NOW. Not applicable to the TinyHawk receiver (no ESP chip).

---

## 6. ESP-IDF BLE Provisioning Vulnerabilities

Three BLE provisioning vulnerabilities were disclosed in 2026, affecting ESP-IDF:

| CVE | Type | Description |
|-----|------|-------------|
| CVE-2026-25507 | Out-of-bounds read | BLE provisioning protocol allows OOB read via crafted provisioning data |
| CVE-2026-25508 | Use-after-free | BLE provisioning protocol has UAF in session handling |
| CVE-2026-45542 | Heap buffer overflow | protocomm Security2 over Bluetooth has heap overflow |

### Relevance

BLE provisioning is used in some ESP32-based devices for initial WiFi configuration. If the E8 controller's ESP32 uses BLE provisioning (e.g., for initial WiFi setup or device pairing), these vulnerabilities enable:
- Memory corruption via crafted BLE advertisements (within ~10m range)
- Potential RCE on the ESP32 without authentication
- Information disclosure via OOB read

**Additional ESP32 BLE vulnerability:**
- **CVE-2025-64342:** Bluetooth Controller accepts invalid access address — ESP32 SoC only. Could enable BLE traffic injection or tracking.

**Confirmed against D's hardware:** Requires confirming whether the E8 controller uses ESP32 with BLE provisioning. The E8's MCU/co-processor has not been identified — this is the same blocker as the passive BLE scan from Round 1 research.

---

## 7. GPS Spoofing & Sensor Injection Attacks

### GPS Spoofing Against Betaflight

Betaflight supports GPS for:
- **GPS Rescue mode** (Return to Home) — uses GPS to navigate back to launch point
- **Position Hold** (Betaflight 2025+) — maintains GPS position
- **OSD display** — shows coordinates, speed, altitude, distance to home

**GPS spoofing attack parameters:**
- Equipment: HackRF One (~$300) or similar SDR + GPS signal generator software (open source)
- Range: 10m to several km depending on power and antenna
- Time to lock: Spoofed signal must overpower genuine GPS signals (~10-20 dB above real signals)

**Attack scenarios against GPS-equipped Betaflight drones:**

1. **GPS Rescue hijack:** Spoof GPS coordinates to make the drone believe "home" is at an attacker-chosen location. When GPS Rescue activates (failsafe or pilot trigger), the drone flies to the attacker's coordinates instead of home.
2. **Position Hold drift:** Gradually shift spoofed coordinates to slowly move the drone in a chosen direction while the pilot believes it's holding position.
3. **Geofence bypass/trigger:** If geofencing is configured, spoofed coordinates can either bypass restricted areas or trigger a geofence landing in an unintended location.

**Research (2026):** A study published in Nature Scientific Reports demonstrated SDR-based GPS spoofing against UAV navigation with telemetry-based detection methods, confirming the practical feasibility and providing detection signatures.

**Barometer/compass injection:**
- Barometric altitude is computed from an on-board pressure sensor (typically BMP280 or similar on the FC). Physical attacks require proximity (pressure changes via airflow) — not practical for remote attack.
- Compass/magnetometer: susceptible to strong magnetic fields but requires close proximity (~1m). Not a practical remote attack vector.

**Source:** [Nature: Evaluation and telemetry-based detection of GPS spoofing effects on UAV navigation using SDR](https://www.nature.com/articles/s41598-026-53481-9)

**Confirmed against D's hardware:** The TinyHawk III Plus does **not** have a GPS module, so GPS spoofing is not applicable. If GPS is added (GPS Rescue is a popular upgrade), this becomes directly relevant. Barometer/compass attacks are not practical for this target.

---

## 8. Betaflight Configurator Electron App Attack Surface

### Architecture

Betaflight Configurator is an Electron application (Chromium + Node.js) that communicates with the flight controller via USB serial (MSP protocol). It runs with:
- Full Node.js access (filesystem, process spawning)
- USB serial port access via Web Serial API or native serial bindings
- WebSocket connections for some features
- No sandboxing in typical deployment

### Attack Vectors

**Vector A: Malicious FC → Configurator RCE**

If an attacker can flash modified firmware on a flight controller (via the DFU/bootloader path documented in Round 1), the modified firmware could send crafted MSP responses when the Configurator connects:
- MSP responses containing malicious payloads could exploit parsing vulnerabilities
- If the Configurator renders MSP data in HTML without sanitization, XSS → RCE via Node.js integration
- Craft names, OSD element text, or firmware version strings that contain JavaScript payloads

**Vector B: USB Serial Enumeration**

The Configurator enumerates and auto-connects to serial ports. A malicious USB device could:
- Impersonate a Betaflight FC on a USB serial port
- Send crafted MSP responses to trigger parsing bugs
- Exploit the Chromium renderer via embedded content

**Vector C: Supply-Chain via Extensions/Plugins**

The Configurator supports a plugin/extension system and loads firmware definitions from GitHub. A compromised firmware definition or plugin could:
- Execute arbitrary code in the Electron main process
- Access the filesystem and network
- Modify FC firmware during flashing

### Electron Framework Vulnerabilities

Electron applications inherit vulnerabilities from both Chromium and Node.js. The Betaflight Configurator's specific Electron version and security configuration (context isolation, sandbox settings) determine exposure.

**Confirmed against D's hardware:** This is a host-side attack surface. If D uses the Betaflight Configurator to connect to the TinyHawk III Plus, and the TinyHawk's firmware has been modified (via DFU or MSP-triggered bootloader), the modified firmware could attack the Configurator. This is a chained attack: ELRS OTA → MSP → DFU → modified firmware → Configurator exploitation → host computer compromise.

---

## 9. Electron Framework CVE-2026-70601: Context Isolation Bypass

### Vulnerability Details

**CVE:** CVE-2026-70601
**Affected:** Electron framework (multiple versions)
**Type:** Context isolation bypass leading to sandbox escape

Context isolation is Electron's primary defense against renderer-to-main process escalation. When bypassed:
- A renderer process XSS can access Node.js APIs
- File system access, process spawning, and network access become available
- Full RCE on the host machine

### Relevance to Betaflight Configurator

If the Betaflight Configurator uses a vulnerable Electron version and an attacker can inject content into the renderer (via crafted MSP responses, a malicious plugin, or a MITM on the cloud build API), CVE-2026-70601 provides the escalation from XSS to full host RCE.

**Source:** [NVD CVE-2026-70601](https://nvd.nist.gov/vuln/detail/CVE-2026-70601)

**Confirmed against D's hardware:** Requires checking the Electron version in D's Betaflight Configurator installation. This is a host-side vulnerability, not a drone-side one.

---

## 10. ELRS WiFi Firmware Update: Expanded Analysis

Building on Round 1's findings about ELRS WiFi update mode (no signing, CVE-2025-53540 CSRF):

### Auto-WiFi Timeout as Attack Enabler

ELRS devices enter WiFi mode automatically after a configurable timeout (default ~60 seconds) when no control link is established. This means:

1. **Jamming → WiFi mode:** An attacker jams the 2.4 GHz ELRS control frequency. After 60 seconds, the ELRS receiver/transmitter enters WiFi mode automatically.
2. **WiFi AP appears:** The device opens an unprotected WiFi AP (SSID pattern: `ExpressLRS RX` or `ExpressLRS TX`)
3. **Firmware replacement:** Attacker connects and flashes malicious firmware via the web endpoint

This converts a temporary RF disruption into a persistent firmware compromise. The ELRS Backpack has the same behavior: "devices without a button enter WiFi mode after 120 seconds in binding mode with no bind packets received."

### ESP-IDF WPS Vulnerability (CVE-2026-25532)

**Integer underflow in WPS Enrollee fragment handling** in ESP-IDF's Wi-Fi component. If ELRS devices have WPS enabled (unlikely but not confirmed disabled), this provides another WiFi-reachable memory corruption path.

**Confirmed against D's hardware:** Auto-WiFi timeout behavior applies to any ESP-based ELRS device. Requires confirming the E8 controller's ELRS module type.

---

## 11. MSP Command Surface: Dangerous Commands Audit

Building on the existing Betaflight 4.4.2 vulnerability audit, here is a focused audit of the most dangerous MSP commands reachable via the ELRS OTA path:

### Tier 1: Immediate Physical Effect

| Command | ID | Effect | OTA Reachable |
|---------|----|--------|---------------|
| MSP_SET_RAW_RC | 200 | Override all RC channel inputs — full flight control | Yes via MSP-over-ELRS |
| MSP_SET_MOTOR | 214 | Direct motor speed control — bypass all safety checks | Yes |
| MSP_REBOOT | 68 | Reboot FC or enter bootloader (arg 0=reboot, 1=bootloader ROM, 2=flash bootloader) | Yes |
| MSP_SET_ARMING_DISABLED | - | Force-disable arming | Yes |

### Tier 2: Persistent Configuration Damage

| Command | ID | Effect | OTA Reachable |
|---------|----|--------|---------------|
| MSP_EEPROM_WRITE | 250 | Write current settings to flash — persist any MSP_SET changes | Yes |
| MSP_SET_CF_SERIAL_CONFIG | 54 | Reconfigure UART assignments — could redirect MSP, disable SmartAudio | Yes |
| MSP_SET_MOTOR_CONFIG | 131 | Change motor protocol, direction, idle throttle | Yes |
| MSP_SET_PID_CONTROLLER | 59 | Change PID tuning — unstable flight | Yes |
| MSP_SET_RX_CONFIG | 45 | Change receiver configuration — could break control link | Yes |
| MSP_SET_FAILSAFE_CONFIG | 76 | Change failsafe behavior — disable failsafe entirely | Yes |
| MSP_SET_VTX_CONFIG | 89 | Change VTX frequency/power — lose video feed | Yes (→ SmartAudio on UART2) |

### Tier 3: Information Exfiltration

| Command | ID | Effect | OTA Reachable |
|---------|----|--------|---------------|
| MSP_UID | 160 | Read STM32 unique device ID (96 bits) | Yes |
| MSP_RAW_GPS | 106 | Read GPS position | Yes (if GPS equipped) |
| MSP_DATAFLASH_READ | 0x70 | Read blackbox flash data | Yes |
| MSP_RX_CONFIG | 44 | Read receiver config including binding UID | Yes |

**Key finding:** There is no MSP command whitelist, no rate limiting, and no authentication. Every MSP command is reachable from the ELRS OTA path with equal privilege. The MSP protocol design assumes a trusted serial connection, but the ELRS OTA path exposes it to the air interface.

**Confirmed against D's hardware:** MSP API 1.45 is confirmed. The full command surface is reachable via the documented OTA path (ELRS → CRSF → MSP). Specific commands like MSP_RX_CONFIG (which exposes binding UID) and MSP_UID (STM32 hardware ID) were confirmed readable during live MSP probing sessions.

---

## 12. Applicability Matrix

| Attack Surface | TinyHawk III Plus | E8 Controller | Hardware Needed | Status |
|---------------|-------------------|---------------|-----------------|--------|
| MSP DisplayPort OSD injection | Indirect (analog VTX) | N/A | None (MSP OTA path) | Confirmed MSP path |
| ELRS Backpack ESP-NOW | **No** (no ESP) | Unconfirmed (needs ESP check) | WiFi-range proximity | Unconfirmed for E8 |
| ELRS v4.0 SPI lockout | **Yes** — locked to v3.x | May upgrade to v4 | None | Confirmed |
| Binding phrase dictionary attack | Yes (UID[3:5] known) | Yes (same binding) | None (computation only) | Ready to execute |
| CVE-2025-52471 ESP-NOW RCE | **No** (no ESP) | If ESP-based Backpack | WiFi-range proximity | Unconfirmed for E8 |
| ESP-IDF BLE provisioning CVEs | **No** (no ESP/BLE) | If ESP32 with BLE | BLE range (~10m) | Unconfirmed for E8 |
| GPS spoofing | **No** (no GPS module) | N/A | SDR (~$300) | N/A current hardware |
| Configurator Electron RCE | Host-side | Host-side | USB connection | Requires chained attack |
| CVE-2026-70601 Electron bypass | Host-side | Host-side | XSS prerequisite | Requires version check |
| Auto-WiFi → firmware replacement | **No** (no ESP) | If ESP-based | WiFi jammer + proximity | Unconfirmed for E8 |
| MSP dangerous commands (full set) | Yes (all reachable OTA) | N/A | ELRS OTA access | **Confirmed** |

---

## 13. Combined Next Steps (Rounds 1 + 2)

### Priority 1: Zero-Hardware Computational Attacks

1. **Binding phrase dictionary attack:** Build a dictionary of common ELRS binding phrases from forums/YouTube/defaults, hash with MD5, check bytes [3:5] against `11:18:5b`. This requires zero hardware and could recover the full UID instantly.

2. **UID[0:2] exhaustive search:** Generate all 16M MD5 candidate UIDs, filter to those matching UID[3:5] = `11:18:5b`. Pre-compute FHSS sequences for each match. This is ready to run on any laptop.

### Priority 2: Bench Tests (Wired, No RF)

3. **MSP dangerous command validation:** Send MSP_FAILSAFE_CONFIG read, MSP_BLACKBOX_CONFIG read, MSP_UID read via serial to map the complete writable configuration surface.

4. **SmartAudio VTX probing:** Send Get Settings to UART2 to confirm VTX model and lock state.

5. **STM32F411 firmware dump:** Enter DFU mode, dump flash, compare against official Betaflight 4.4.2 release for supply-chain verification.

6. **SX1280 SPI register audit:** Logic analyzer on SPI bus 3 to map the register space.

### Priority 3: Passive RF (Requires WiFi/BLE Adapter)

7. **E8 WiFi/BLE scan:** Determine if E8 broadcasts WiFi AP or BLE advertisements — confirms ESP MCU and enables CVE-2025-52471/CSRF/BLE provisioning attacks.

8. **ELRS telemetry passive decode:** With known UID, decode the unencrypted telemetry downlink.

### Priority 4: Active RF (Requires SDR)

9. **UID brute-force validation:** Test pre-computed UID candidates via RF.

10. **GPS spoofing PoC:** If GPS is added to the drone, test GPS Rescue hijacking with SDR.

---

## References

- [Betaflight DisplayPort MSP Extensions](https://betaflight.com/docs/development/API/DisplayPort)
- [ELRS Backpack Info](https://expresslrs.org/hardware/backpack/esp-backpack/)
- [Oscar Liang: ExpressLRS 4.0 Released](https://oscarliang.com/expresslrs-4/)
- [NVD CVE-2025-52471: ESP-NOW Integer Underflow](https://nvd.nist.gov/vuln/detail/CVE-2025-52471)
- [Espressif ESP-IDF Security Vulnerabilities](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/security/vulnerabilities.html)
- [NVD CVE-2026-70601: Electron Context Isolation Bypass](https://nvd.nist.gov/vuln/detail/CVE-2026-70601)
- [Nature: GPS spoofing effects on UAV navigation using SDR (2026)](https://www.nature.com/articles/s41598-026-53481-9)
- [Betaflight MSP Extensions](https://betaflight.com/docs/development/API/MSP-Extensions)
- [Betaflight Issue #15752: MSP Command Whitelist](https://github.com/betaflight/betaflight/issues/15752)
- [NCC Group: ExpressLRS Vulnerabilities](https://www.nccgroup.com/research/technical-advisory-expresslrs-vulnerabilities-allow-for-hijack-of-control-link/)
- [CVE-2025-53540: Arduino-ESP32 CSRF Firmware Update](https://github.com/espressif/arduino-esp32/security/advisories/GHSA-9vfw-wx65-c872)
