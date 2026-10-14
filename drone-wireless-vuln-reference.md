# Drone Wireless Chipset Vulnerability Reference

> **Purpose:** CTF competition reference for wireless vulnerabilities affecting drone platforms.
> Covers WiFi, Bluetooth/BLE, and proprietary RF protocols — focused on DoS and RCE attack classes.
> **Last updated:** 2026-10-14

---

## Table of Contents

- [Quick-Reference Attack Table](#quick-reference-attack-table)
- [WiFi Chipset Vulnerabilities](#wifi-chipset-vulnerabilities)
  - [Broadcom / Cypress (BCM43xx)](#broadcom--cypress-bcm43xx)
  - [Qualcomm / Atheros](#qualcomm--atheros)
  - [Realtek (RTL88xx)](#realtek-rtl88xx)
  - [MediaTek (MT76xx)](#mediatek-mt76xx)
  - [Espressif (ESP32)](#espressif-esp32)
- [WiFi Protocol-Level Attacks](#wifi-protocol-level-attacks)
  - [KRACK (Key Reinstallation Attacks)](#krack-key-reinstallation-attacks)
  - [FragAttacks (Fragmentation & Aggregation)](#fragattacks-fragmentation--aggregation)
  - [PMKID Clientless Attack](#pmkid-clientless-attack)
  - [Deauthentication Attacks & PMF](#deauthentication-attacks--pmf)
  - [Kr00k (WPA2 Traffic Decryption)](#kr00k-wpa2-traffic-decryption)
- [Bluetooth / BLE Vulnerabilities](#bluetooth--ble-vulnerabilities)
  - [BlueBorne (Bluetooth Classic RCE)](#blueborne-bluetooth-classic-rce)
  - [BrakTooth (Bluetooth Classic)](#braktooth-bluetooth-classic)
  - [SweynTooth (BLE)](#sweyntooth-ble)
  - [KNOB + BIAS (Bluetooth Classic)](#knob--bias-bluetooth-classic)
  - [BLEEDINGBIT (TI CC2540/CC2640)](#bleedingbit-ti-cc2540cc2640)
  - [Nordic nRF52](#nordic-nrf52)
- [Drone-Specific Attack Surface](#drone-specific-attack-surface)
  - [DJI Bluetooth Vulnerabilities](#dji-bluetooth-vulnerabilities)
  - [DJI WiFi & DroneID](#dji-wifi--droneid)
  - [DJI OcuSync / Lightbridge](#dji-ocusync--lightbridge)
  - [MAVLink Protocol](#mavlink-protocol)
  - [ExpressLRS (ELRS)](#expresslrs-elrs)
  - [FrSky / Crossfire / Other RC Protocols](#frsky--crossfire--other-rc-protocols)
- [Tooling Reference](#tooling-reference)
  - [WiFi Tools](#wifi-tools)
  - [Bluetooth / BLE Tools](#bluetooth--ble-tools)
  - [SDR & RF Tools](#sdr--rf-tools)
  - [Packet Crafting & Analysis](#packet-crafting--analysis)
  - [Exploitation Frameworks](#exploitation-frameworks)
- [CVE Index](#cve-index)
- [Sources & Further Reading](#sources--further-reading)

---

## Quick-Reference Attack Table

| CVE / Name | Chipset / Protocol | Attack Type | Impact | Range | Auth Required | PoC Available |
|---|---|---|---|---|---|---|
| **CVE-2017-9417** (Broadpwn) | Broadcom BCM43xx | WiFi firmware overflow | **RCE** | WiFi range | No | Yes |
| **CVE-2019-9500** | Broadcom brcmfmac | WiFi driver heap overflow (WoWLAN) | **RCE** | WiFi range | No | Yes |
| **CVE-2019-9502** | Broadcom wl driver | WiFi driver heap overflow | **RCE** | WiFi range | No | Yes |
| **CVE-2019-15126** (Kr00k) | Broadcom/Cypress | WPA2 traffic decryption | Data leak | WiFi range | No | Yes |
| **CVE-2024-20017** | MediaTek MT7915/MT7622 | wappd stack overflow | **RCE** (CVSS 9.8) | WiFi range | No | Yes (public) |
| **CVE-2025-20685** | MediaTek MT7915/MT7916 | AP driver code exec | **RCE** | WiFi range | No | Unknown |
| **CVE-2026-68309** | MediaTek mt76 driver | NULL ptr deref | **DoS** (kernel panic) | WiFi range | No | Unknown |
| **CVE-2025-47401** | Qualcomm WLAN HAL | Buffer overrun | **DoS** | WiFi range | No | Unknown |
| **CVE-2025-47403** | Qualcomm WLAN firmware | Buffer overrun | **DoS** | WiFi range | No | Unknown |
| **CVE-2020-26652** | Realtek RTL8812AU | nl80211 DoS | **DoS** | WiFi range | No | Unknown |
| **CVE-2017-13077** (KRACK) | All WPA2 clients | Key reinstallation | Decrypt/Inject | WiFi range | No | Yes |
| **CVE-2020-24588** (FragAttacks) | All WiFi devices | A-MSDU injection | Inject/Leak | WiFi range | No | Yes |
| **CVE-2017-0781** (BlueBorne) | Android Bluetooth | BNEP RCE | **RCE** | ~10m BT | No | Yes |
| **CVE-2017-1000251** (BlueBorne) | Linux BlueZ | L2CAP stack overflow | **RCE** | ~10m BT | No | Yes |
| **CVE-2021-28139** (BrakTooth) | ESP32 BT Classic | LMP feature page overflow | **RCE** (CVSS 8.8) | BT range | No | Yes |
| **CVE-2019-9506** (KNOB) | All BT BR/EDR | Key entropy reduction | MitM | BT range | No | Yes |
| **CVE-2020-10135** (BIAS) | All BT BR/EDR ≤5.2 | Auth bypass / impersonation | MitM | BT range | No | Yes |
| **CVE-2018-16986** (BLEEDINGBIT) | TI CC2640/CC2650 | BLE heap overflow | **RCE** | BLE range | No | Yes |
| **CVE-2018-7080** | TI CC2540/CC2640R2 | OAD firmware overwrite | **RCE** | BLE range | No | Yes |
| **CVE-2019-16336** (SweynTooth) | Multiple BLE SoCs | LL length overflow | **DoS** (crash) | BLE range | No | Yes |
| **CVE-2025-27840** | ESP32 | Hidden HCI commands | Config tamper | Physical/HCI | Yes (wired) | PoC |
| **CVE-2026-45542** | ESP32 (ESP-IDF) | protocomm heap overflow | **RCE** | BLE range | No | Unknown |
| **CVE-2026-81507** | ESP32 (ESP-IDF) | L2CAP OOB read | **DoS** | BT range | No | Unknown |
| **CVE-2026-78306** | DJI (16 models) | BT command injection | **DoS** / config tamper | BLE range | No | PoC |
| **CVE-2023-6951** | DJI WiFi | Weak WPA2 credentials | Network access | WiFi range | User interaction | Unknown |
| N/A | MAVLink v1/v2 | Unencrypted telemetry | **Hijack** / inject | Radio range | No | Multiple |
| N/A | ExpressLRS 1.x/2.x | Binding phrase leak | **Hijack** | LoS RF range | No | Yes |

---

## WiFi Chipset Vulnerabilities

### Broadcom / Cypress (BCM43xx)

Broadcom's BCM43xx family is found in smartphones, IoT devices, and some drone companion computers. Cypress acquired Broadcom's IoT wireless division, so newer chips ship under the Cypress brand with the same firmware lineage.

#### CVE-2017-9417 — Broadpwn (CRITICAL)

- **CVSS:** 9.8
- **Impact:** Remote code execution on the WiFi SoC firmware
- **Root cause:** Heap buffer overflow in WiFi firmware, triggered by specially crafted WiFi frames
- **Auth required:** None — over-the-air, no association needed
- **Affected:** BCM4354, BCM4358, BCM4359, and other BCM43xx variants
- **Discovered by:** Nitay Artenstein (Exodus Intelligence), presented at Black Hat 2017
- **CTF relevance:** Classic firmware-level RCE. If a drone runs a BCM43xx chip with unpatched firmware, this is a direct path to code execution on the wireless SoC. From there, shared-memory escalation to the application processor may be possible.

#### CVE-2019-9500 — brcmfmac Wake-on-WLAN Heap Overflow

- **Impact:** RCE via heap overflow in the Wake-on-Wireless-LAN event processing path
- **Root cause:** Insufficient validation of event data lengths in the brcmfmac Linux driver
- **Affected:** Linux systems using Broadcom/Cypress WiFi with brcmfmac
- **Note:** CVE-2019-9503 is a related issue — firmware event frame validation bypass over USB

#### CVE-2019-9502 — wl Driver Heap Overflow

- **Impact:** RCE via heap buffer overflow
- **Root cause:** Vendor IE data length exceeding 164 bytes triggers overflow in `wlc_wpa_plumb_gtk`
- **Reported by:** Quarkslab, coordinated through CERT/CC (VU#166939)
- **Companion CVEs:** CVE-2019-9503 (firmware event bypass), CVE-2019-8564

#### CVE-2019-15126 — Kr00k

- **Impact:** WPA2 traffic decryption (data leak, not RCE)
- **Root cause:** After disassociation, Broadcom/Cypress chips encrypt frames with an all-zero temporal key (TK)
- **Affected:** Broadcom and Cypress WiFi chips (BCM4356, BCM4359, CYW4373, etc.)
- **CTF relevance:** Trigger disassociation, capture frames encrypted with zero-key, decrypt to recover data in transit

#### Shared-Memory Lateral Movement

Research from 2020 demonstrated that once code execution is achieved on a Broadcom WiFi chip, attackers can move laterally to the Bluetooth chip through shared memory interfaces. This is significant for drone targets: a WiFi exploit could pivot to compromise BLE functionality or vice versa.

---

### Qualcomm / Atheros

Qualcomm's WLAN chipsets (Atheros heritage) are found in high-end smartphones, some companion computers, and networking equipment that may serve as drone ground stations.

#### CVE-2025-47401 / CVE-2025-47403 — WLAN Buffer Overruns

- **Impact:** Transient denial of service
- **Severity:** High (per Qualcomm May 2026 bulletin)
- **Location:** WLAN HAL (47401) and WLAN firmware (47403)
- **Auth required:** None — remote
- **Note:** These are DoS, not confirmed RCE

#### CVE-2026-25254 — Qualcomm Software Center (Critical)

- **CVSS:** 9.8
- **Impact:** Unauthenticated RCE via SocketIO interface
- **Root cause:** Improper authorization in Qualcomm Software Center
- **Note:** Not a WiFi chipset bug per se, but relevant if the target uses Qualcomm management tools

#### Older Atheros Driver CVEs

| CVE | Chip | Impact |
|---|---|---|
| CVE-2009-0052 | AR9160 | Crash/possible RCE via truncated mgmt frame |
| CVE-2007-5474 | Atheros (Linksys WRT350N) | Malformed vendor IE in assoc request |

#### QuAlPwn (2019)

- Three Qualcomm bugs allowing over-the-air compromise of Android devices
- Exploits WiFi firmware to gain code execution, then escalates to the application processor kernel
- Relevant pattern: firmware RCE → kernel escalation (same as Broadpwn chain)

---

### Realtek (RTL88xx)

Realtek's RTL8812AU and RTL8811AU are popular USB WiFi adapters, often used in drone ground stations and pentesting rigs. The out-of-tree drivers have a history of issues.

#### CVE-2020-26652 — RTL8812AU DoS

- **CVSS:** 7.5 (High)
- **Impact:** Denial of service in `nl80211_send_chandef`
- **Affected:** aircrack-ng's out-of-tree rtl8812au driver v5.6.4.2
- **Auth required:** None — remote over WiFi
- **CTF relevance:** If the target uses RTL8812AU with the community driver, this can crash the WiFi stack

#### CVE-2025-8298 / CVE-2025-8301 — RTL8811AU Driver Bugs

- **CVE-2025-8298:** OOB read in rtwlanu.sys — local info disclosure
- **CVE-2025-8301:** Heap buffer overflow — local privilege escalation (rated HIGH)
- **Note:** These affect the Windows driver for RTL8811AU; not confirmed on RTL8812AU, but the chips share silicon

#### Community-Reported Vulnerabilities

A [GitHub issue on astsam/rtl8812au](https://github.com/astsam/rtl8812au/issues/89) reports multiple remotely exploitable vulnerabilities in the RTL8812AU and RTL8814AU out-of-tree drivers. No CVE was assigned.

---

### MediaTek (MT76xx)

MediaTek WiFi chips appear in routers, access points, and some embedded drone platforms (especially OpenWrt-based).

#### CVE-2024-20017 — wappd Stack Overflow (CRITICAL, public PoC)

- **CVSS:** 9.8
- **Impact:** Remote code execution on the access point
- **Root cause:** Stack buffer overflow in the wappd network daemon; attacker-supplied length goes unchecked, overflowing by up to 1433 bytes
- **Affected chipsets:** MT6890, MT7915, MT7916, MT7981, MT7986, MT7622
- **Auth required:** None — 0-click, over the air
- **PoC:** Publicly available
- **CTF relevance:** High — if the drone or ground station runs OpenWrt on a MediaTek chipset, this is a direct RCE path

#### CVE-2025-20685 — AP Driver Unauthenticated RCE

- **Impact:** Code execution on the access point without authentication
- **Affected:** MT6890, MT7915, MT7916, MT7981, MT7986
- **Patched:** MediaTek July 2025 security bulletin

#### CVE-2026-68309 — mt76 Kernel Driver NULL Pointer Deref

- **Impact:** Kernel panic / denial of service
- **Root cause:** NULL pointer dereference in `mt76_connac_mcu_uni_bss_he_tlv`
- **Affected:** mt76 driver (MT7915 and others)

#### CVE-2021-35055 — WPS Out-of-Bounds Write

- **CVSS:** 9.3
- **Impact:** Code execution via WPS
- **Note:** Primarily affects NETGEAR products using MediaTek WiFi

---

### Espressif (ESP32)

The ESP32 is extremely common in hobby drones, companion computers, and DIY drone projects. It integrates both WiFi and Bluetooth on a single SoC.

#### CVE-2025-27840 — Hidden HCI Commands ("Backdoor")

- **CVSS:** 6.8
- **Impact:** Memory read/write via 29 undocumented Bluetooth HCI commands
- **Reality check:** Requires wired HCI access, not remotely exploitable over Bluetooth. Better described as hidden debug commands than a backdoor.
- **CTF relevance:** If you have physical/UART access to the ESP32, these commands can read/write flash

#### CVE-2026-45542 — protocomm Security2 Heap Overflow

- **Impact:** RCE via heap overflow in Bluetooth provisioning (protocomm Security2)
- **Fixed in:** ESP-IDF 5.2.8, 5.3.6, 5.5.5, 6.0.3, 6.1-rc1
- **CTF relevance:** If the target drone uses BLE provisioning, this is exploitable over the air

#### CVE-2026-25508 / CVE-2026-25507 — BLE Provisioning Bugs

- **CVE-2026-25508:** Use-after-free in BLE provisioning
- **CVE-2026-25507:** Out-of-bounds read in BLE provisioning

#### CVE-2026-81507 — Bluetooth Classic L2CAP OOB Read

- **Impact:** Denial of service — nearby unauthenticated device can trigger
- **Fixed in:** ESP-IDF 5.2.8, 5.3.6, 5.5.5, 6.0.3, 6.1-rc1

#### CVE-2026-41429 — Arduino-ESP32 NBNS Memory Corruption

- **Impact:** Remote memory corruption via crafted NBNS packets
- **Fixed in:** Arduino-ESP32 3.3.8

#### BluFi Weaknesses (No CVE assigned)

NCC Group reported memory corruption and cryptographic weaknesses in the BluFi WiFi provisioning component. Espressif declined to issue CVEs.

---

## WiFi Protocol-Level Attacks

These affect all WiFi devices regardless of chipset vendor.

### KRACK (Key Reinstallation Attacks)

- **CVEs:** CVE-2017-13077 through CVE-2017-13088 (10 CVEs total)
- **Discovered by:** Mathy Vanhoef (2017)
- **Impact:** Decrypt WPA2 traffic, possibly inject forged packets
- **Mechanism:** Forces nonce reuse by replaying message 3 of the 4-way handshake, causing the pairwise key to be reinstalled with a reset nonce counter
- **Range:** WiFi proximity
- **Affected:** WPA and WPA2, personal and Enterprise; Linux and Android most severely affected (wpa_supplicant 2.4+ reinstalls an all-zero key)
- **CTF relevance:** If the drone's WiFi link uses WPA2 with an unpatched supplicant, KRACK allows traffic decryption. Most useful for capturing telemetry or credentials in transit.
- **Key CVEs:**
  - CVE-2017-13077 — PTK-TK reinstallation in 4-way handshake
  - CVE-2017-13078 — GTK reinstallation in 4-way handshake
  - CVE-2017-13080 — GTK reinstallation in group key handshake
  - CVE-2017-13082 — PTK-TK reinstallation in FT (fast BSS transition)

### FragAttacks (Fragmentation & Aggregation)

- **CVEs:** CVE-2020-24586, CVE-2020-24587, CVE-2020-24588 (design flaws); plus implementation-specific CVEs
- **Discovered by:** Mathy Vanhoef (2021)
- **Impact:** Frame injection, data exfiltration
- **Three design flaws:**
  - **CVE-2020-24586** — Fragment cache attack: fragments from different keys can be reassembled
  - **CVE-2020-24587** — Mixed key attack: fragments encrypted under different keys are reassembled
  - **CVE-2020-24588** — Aggregation attack: non-SPP A-MSDU frames are accepted, allowing injection
- **Scope:** Affects virtually all WiFi implementations (75+ devices tested)
- **CTF relevance:** Can inject arbitrary frames into an encrypted WiFi network; useful for injecting payloads or redirecting traffic

### PMKID Clientless Attack

- **No CVE** — design characteristic of WPA2
- **Discovered by:** Jens "atom" Steube (hashcat developer, 2018)
- **Impact:** Offline PSK cracking without needing a connected client
- **Mechanism:** Capture the PMKID from the first EAPOL frame (M1) sent by the AP in response to an association request. The PMKID = HMAC-SHA1-128(PMK, "PMK Name" || MAC_AP || MAC_STA). Brute-force offline with hashcat mode 16800.
- **Tools:** `hcxdumptool` (capture), `hcxpcaptool` (convert), `hashcat` (crack)
- **CTF relevance:** If the drone acts as a WiFi AP (common for control links), you can grab the PMKID from a single frame and crack the PSK offline. No deauth needed, no client needed.
- **Limitation:** Not all APs include PMKID in their frames

### Deauthentication Attacks & PMF

- **No CVE** — inherent to 802.11 management frames
- **Impact:** Denial of service — force disconnection of clients from an AP
- **Mechanism:** Forge deauthentication or disassociation management frames; these are unauthenticated by default
- **Mitigation:** 802.11w (Protected Management Frames / PMF) protects deauth/disassoc frames sent after key establishment
- **PMF limitations:** Pre-authentication frames (beacons, probes, auth, assoc) remain unprotected even with PMF enabled
- **CTF relevance:** Most consumer drones do NOT enable PMF. Deauth attacks can force the drone into failsafe mode (RTH, land, hover) or disrupt FPV video. Also useful as a precursor to KRACK or handshake capture.
- **Tools:** `aireplay-ng -0`, `mdk3`, `mdk4`, Scapy

### Kr00k (WPA2 Traffic Decryption)

- **CVE:** CVE-2019-15126
- **Impact:** Decrypt WPA2 unicast traffic (data leak)
- **Mechanism:** After disassociation, Broadcom/Cypress chips continue encrypting queued frames with an all-zero TK; attacker triggers disassoc, captures the zero-key-encrypted frames, and decrypts trivially
- **Affected:** Broadcom and Cypress WiFi chips specifically
- **CTF relevance:** Chain with deauth — force disassociation, capture and decrypt buffered frames

---

## Bluetooth / BLE Vulnerabilities

### BlueBorne (Bluetooth Classic RCE)

- **Discovered by:** Armis Labs (2017)
- **Impact:** Remote code execution over Bluetooth — no pairing, no discovery mode, no user interaction
- **Scope:** Affects Android, Linux (BlueZ), iOS (≤9.3.5), Windows
- **Protocol:** Bluetooth Classic (BR/EDR), not BLE
- **Key CVEs:**

| CVE | Platform | Impact |
|---|---|---|
| CVE-2017-0781 | Android (BNEP) | RCE — heap overflow in BNEP |
| CVE-2017-0782 | Android (PAN) | RCE — integer underflow in BNEP PAN |
| CVE-2017-1000251 | Linux (BlueZ) | RCE — L2CAP stack buffer overflow |
| CVE-2017-1000250 | Linux (BlueZ) | Info leak — SDP OOB read |
| CVE-2017-14315 | Apple (LEAP) | RCE — heap overflow in BT audio |

- **CTF relevance:** If the drone runs Android or Linux with an unpatched Bluetooth stack, BlueBorne gives RCE from ~10m away with zero interaction. Check `hciconfig` for discoverable Bluetooth interfaces.

### BrakTooth (Bluetooth Classic)

- **Discovered by:** ASSET Research Group, SUTD (2021) — same team as SweynTooth
- **Scope:** 16 vulnerabilities across Bluetooth Classic stacks from multiple SoC vendors
- **Key CVE:**

#### CVE-2021-28139 — ESP32 LMP Feature Page Overflow

- **CVSS:** 8.8 (v3) / 8.3 (v2)
- **Impact:** Arbitrary code execution on ESP32 via Bluetooth Classic
- **Root cause:** Missing bounds check in ESP32 BT library — a mutated LMP Extended Feature Response can inject 8 bytes outside the feature page table
- **Affected:** ESP-IDF 4.4 and earlier
- **Demonstrated impact:** Researchers erased ESP32 NVRAM, disabled both WiFi and Bluetooth
- **Patched:** esp-idf and esp32-bt-lib on GitHub
- **CTF relevance:** HIGH — ESP32 is common in drone projects. If the target uses BT Classic on ESP32 with old firmware, this gives RCE from Bluetooth range.

### SweynTooth (BLE)

- **Discovered by:** ASSET Research Group, SUTD (2020)
- **Scope:** BLE stack implementations across multiple chip vendors
- **Impact:** Primarily DoS (crashes, deadlocks); RCE possible in specific firmware configurations
- **Affected vendors:** TI, NXP, Cypress, Dialog, Microchip, Telink, STMicroelectronics
- **Key CVEs:**

| CVE | Name | Impact |
|---|---|---|
| CVE-2019-16336 | LL Length Overflow | Crash (DoS) |
| CVE-2019-17060/17061 | LLID Deadlock | Deadlock (DoS) |
| CVE-2019-19195 | Invalid L2CAP Fragment | Crash (DoS) |
| CVE-2019-19194 | Zero-LTK Security Bypass | Authentication bypass |

- **CTF relevance:** Useful for DoS against BLE peripherals. If the drone uses a SweynTooth-affected BLE SoC for control or telemetry, you can crash it with crafted BLE frames.
- **Note:** CISA ICS-ALERT-20-063-01 covers this family

### KNOB + BIAS (Bluetooth Classic)

These two attacks chain together to completely compromise a Bluetooth Classic link.

#### KNOB — Key Negotiation of Bluetooth (CVE-2019-9506)

- **Impact:** Force low-entropy encryption key, enabling brute-force
- **Mechanism:** Manipulate key length negotiation to force minimum entropy (as low as 1 byte). The Bluetooth spec allowed this — compliant chips were affected.
- **Scope:** All BR/EDR devices tested (17 chip models from Apple, Intel, Broadcom, Qualcomm)
- **Affected:** BT Core Spec ≤5.1

#### BIAS — Bluetooth Impersonation Attacks (CVE-2020-10135)

- **Impact:** Spoof a bonded device, bypass authentication
- **Mechanism:** Exploit lack of mandatory mutual authentication, permissive role switching, and auth procedure downgrade
- **Affected:** BT Core Spec ≤5.2

#### KNOB + BIAS Chain

1. BIAS bypasses authentication — attacker impersonates a bonded device without the link key
2. KNOB weakens the session key to brute-forceable entropy
3. Result: full MitM on the Bluetooth Classic link — decrypt, inject, eavesdrop

- **CTF relevance:** If the drone uses Bluetooth Classic (not BLE) for any control or data link, this chain gives full MitM. Requires proximity (~10m).

### BLEEDINGBIT (TI CC2540/CC2640)

Texas Instruments BLE chips are found in commercial IoT devices, some drone accessories, and enterprise access points.

#### CVE-2018-16986 — Heap Overflow via BLE Advertising

- **Impact:** Remote code execution / unauthenticated firmware installation
- **Root cause:** Heap overflow triggered by malformed BLE advertising packets
- **Affected:** TI CC2640, CC2650
- **Discovered by:** Armis

#### CVE-2018-7080 — Insecure OAD Firmware Update

- **Impact:** Remote firmware overwrite
- **Root cause:** Over-the-Air Download (OAD) feature enabled without proper security
- **Affected:** CC2540, CC2541, CC2640, CC2640R2
- **CTF relevance:** If OAD is enabled, an attacker can push malicious firmware to the BLE chip

#### CVE-2019-19193 — BLE DoS

- **CVSS:** 6.5
- **Impact:** Denial of service via crafted connection requests
- **Affected:** CC2640R2, CC2540/CC2541

### Nordic nRF52

Nordic's nRF52 series is widely used in BLE peripherals, drone RC receivers, and IoT devices.

#### CVE-2020-27211 — APPROTECT Bypass (Physical)

- **Impact:** Flash readout protection bypass via fault injection during boot
- **Scope:** nRF52840 revisions 1 and 2
- **Fixed:** nRF52840 revision 3 (hardware fix)
- **CTF relevance:** If you have physical access to an nRF52840 board, older revisions allow firmware extraction via voltage glitching

#### CVE-2021-29415 — ECDSA Timing Side-Channel

- **Impact:** Private key recovery via timing analysis of CryptoCell-310 ECC operations
- **Scope:** nRF52840 (not fixed in hardware)
- **Practical risk:** Nordic considers this a theoretical/lab-only attack

#### CVE-2020-15509 — Nordic Android BLE Library Encryption Downgrade

- **CVSS:** 6.5
- **Impact:** Unencrypted communication presented as encrypted during bond creation
- **Scope:** Nordic's Android BLE and DFU libraries (not the nRF52 firmware itself)

---

## Drone-Specific Attack Surface

### DJI Bluetooth Vulnerabilities

#### CVE-2026-78306 — Bluetooth Command Injection (2026)

- **Severity:** 8.5/10 (Cybernews rating)
- **Impact:** DoS, configuration tampering — change WiFi SSID/password, disable WiFi, restart WiFi, power off aircraft
- **Affected:** 16 DJI models including Mavic, Mini, Air, Neo, Flip, Avata lines
- **Root cause:** Only 3 of the Bluetooth commands check for a trusted UUID; the rest accept unauthorized instructions
- **Discovered by:** Abdelrahman Yousef and Dr. Jordan Samhi
- **Range:** Bluetooth range (~10-30m)
- **CTF relevance:** HIGH — if the CTF uses DJI drones, this is a direct path to DoS via Bluetooth. Change the WiFi password to lock out the operator, or power off the aircraft.

### DJI WiFi & DroneID

#### CVE-2023-6951 — Weak WiFi Credentials

- **CVSS:** 6.6 (Medium)
- **Impact:** Attacker can derive WPA2 key for drone's WiFi network
- **Condition:** Requires QuickTransfer Mode to be activated (user interaction)

#### DroneID Exposure (No CVE)

- DJI drones broadcast unencrypted DroneID packets that reveal:
  - Live drone position (GPS coordinates)
  - Live pilot/operator position
  - Serial number, flight path data
- Affects Lightbridge, OcuSync, and Enhanced WiFi era drones
- **Research:** NDSS 2023 paper by Schiller et al. — built a receiver/demodulator/decoder
- **CTF relevance:** Passive reconnaissance — identify drone position and pilot location without any active attack

#### DJI Enhanced WiFi MitM

- Researchers demonstrated man-in-the-middle attacks on DJI's Enhanced WiFi protocol (used by Mini SE and similar models)
- Commercial off-the-shelf WiFi routers are sufficient for the attack
- **Note:** OcuSync models are NOT vulnerable to this — OcuSync uses AES-256 encryption on both uplink and downlink

### DJI OcuSync / Lightbridge

- **OcuSync:** AES-256 encrypted uplink and downlink; researchers found it "robust" and "difficult to listen to and tamper with"
- **Lightbridge:** Older protocol; less research available. DroneID broadcasts are unencrypted but the control link uses proprietary encoding
- **NDSS 2023 fuzzing:** Black-box fuzzing uncovered "security-critical crashes that can be used to gain root access or crash drones in the air" — details were not fully published
- **CTF relevance:** OcuSync is hard to attack at the RF level. Focus on Bluetooth, WiFi, and application-layer attacks instead.

### MAVLink Protocol

MAVLink is the dominant telemetry/command protocol for open-source drone platforms (ArduPilot, PX4).

#### Core Weaknesses

| Weakness | Detail |
|---|---|
| **No encryption (v1)** | All messages sent in plaintext |
| **Weak auth (v2)** | Message signing exists but has weak key generation, vulnerable sequence numbers, incomplete timestamp verification |
| **No mutual auth** | GCS and drone don't mutually authenticate |
| **Replay vulnerable** | Captured packets can be replayed; timestamp checks are bypassable |

#### Attack Vectors

1. **Eavesdropping:** Sniff telemetry to get GPS, altitude, battery, sensor data
2. **Command injection:** Send unauthorized MAVLink commands (arm, disarm, change mode, set waypoints)
3. **Replay attacks:** Record and replay valid command sequences
4. **Man-in-the-middle:** Position between drone and GCS, modify commands/telemetry in transit
5. **Parameter manipulation:** Change flight parameters (geofence, failsafe settings, PID gains) to destabilize the aircraft
6. **Dictionary attack on v2 signing:** Recover the signing key, then inject authenticated commands

#### Transport Layers

MAVLink runs over multiple transports — each is an attack surface:
- **Serial radio (SiK 433/915 MHz):** No encryption, NetID can be sniffed
- **WiFi (UDP):** Standard WiFi attacks apply
- **Bluetooth:** Standard BT/BLE attacks apply
- **LTE/cellular:** Harder to attack but not immune

#### CTF Relevance

MAVLink is the most likely protocol in a drone CTF using open-source platforms. Key tools:
- `mavproxy` — GCS that can inject commands
- `pymavlink` — Python library for crafting MAVLink messages
- `Wireshark` with MAVLink dissector
- `Scapy` for raw packet crafting over the transport layer

### ExpressLRS (ELRS)

ExpressLRS is a popular open-source RC link protocol for FPV drones.

#### Binding Phrase Leak (2022)

- **Impact:** Full link hijack — attacker takes control of the drone
- **Root cause:**
  - Binding phrase is hashed with MD5 (broken algorithm)
  - Sync packet leaks the final 3 bytes of the UID
  - CRC initializer uses the final 2 bytes of the UID from the sync packet
  - With these values, an attacker can reconstruct the binding phrase hash and inject commands
- **Affected:** ELRS 1.x and 2.x firmware
- **Fixed:** ELRS 3.x (protocol-level redesign) — verify against official release notes
- **CTF relevance:** If the target runs ELRS 1.x/2.x, an attacker with line-of-sight and the right transmitter hardware can fully hijack the control link

### FrSky / Crossfire / Other RC Protocols

- **General assessment:** No encryption, no strong authentication. "Other hobby protocols like CRSF and FrSky aren't secure either" (AnyLeaf)
- **FrSky ACCESS:** Proprietary protocol with some binding security, but no public security audit
- **TBS Crossfire (CRSF):** No encryption over the air; binding phrase is the only security
- **nRF24L01-based links:** Toy-grade drones often use nRF24L01 2.4 GHz with no encryption or rolling codes — fully replayable
- **CTF relevance:** For toy/hobby drones, SDR capture + replay is often sufficient. For ELRS/Crossfire, the binding phrase is the target.

---

## Tooling Reference

### WiFi Tools

| Tool | Purpose | Key Usage |
|---|---|---|
| **aircrack-ng suite** | WiFi assessment (capture, deauth, crack) | `airodump-ng` (scan), `aireplay-ng -0` (deauth), `aircrack-ng` (crack WPA) |
| **hcxdumptool** | PMKID/handshake capture | Capture PMKID without client; feeds hashcat |
| **hcxpcaptool / hcxpcapngtool** | Convert captures to hashcat format | Process hcxdumptool output for cracking |
| **hashcat** | GPU-accelerated password cracking | Mode 16800 (PMKID), mode 22000 (WPA) |
| **bettercap** | Network attack framework | WiFi deauth, PMKID capture, ARP spoofing, proxying |
| **Wireshark** | Packet analysis | WiFi decryption with known key, MAVLink dissector |
| **hostapd-mana** | Rogue AP | Evil twin attacks against drone WiFi |
| **wpa_supplicant** | WPA client | Testing KRACK, connecting to drone APs |

### Bluetooth / BLE Tools

| Tool | Purpose | Key Usage |
|---|---|---|
| **hciconfig / hcitool** | Bluetooth interface management | Scan, configure BT interfaces |
| **bluetoothctl** | BlueZ interactive control | Scan, pair, connect to BLE devices |
| **gatttool / bluetoothctl** | GATT interaction | Enumerate services, read/write characteristics |
| **bettercap** | BLE scanning and enumeration | `ble.recon on`, enumerate nearby BLE devices |
| **btlejack** | BLE sniffing and jamming | Sniff existing BLE connections, inject packets |
| **Ubertooth One** | Bluetooth Classic sniffer | Hardware for capturing BT Classic (BR/EDR) frames |
| **InternalBlue** | Broadcom BT firmware patching | Binary patching of Broadcom BT firmware for research |
| **GATTacker** | BLE MitM | Proxy between BLE peripheral and central |
| **Sniffle** | BLE 5 sniffer | TI CC1352/CC26x2 based BLE sniffer |

### SDR & RF Tools

| Tool | Purpose | Key Usage |
|---|---|---|
| **HackRF One** | Wideband SDR transceiver (1 MHz–6 GHz) | Capture and replay drone RF signals |
| **RTL-SDR** | Receive-only SDR dongle | Passive monitoring of drone frequencies |
| **BladeRF** | Full-duplex SDR | Higher-fidelity capture and replay |
| **YARD Stick One** | Sub-GHz transceiver | 300-928 MHz attacks (SiK radios, 433/915 MHz telemetry) |
| **Universal Radio Hacker (URH)** | Signal analysis + demodulation | Full pipeline: capture → demodulate → analyze → replay |
| **GNU Radio** | SDR signal processing framework | Build custom demodulators and decoders |
| **Inspectrum** | Signal visualization | Analyze captured RF signals visually |
| **gr-dji** | DJI DroneID decoder | Decode DJI DroneID broadcasts |

### Packet Crafting & Analysis

| Tool | Purpose | Key Usage |
|---|---|---|
| **Scapy** | Python packet manipulation | Craft custom WiFi/BT/network frames |
| **pymavlink** | MAVLink Python library | Craft and inject MAVLink messages |
| **MAVProxy** | MAVLink ground station | Connect to and command MAVLink drones |
| **QGroundControl** | GCS for PX4/ArduPilot | Legitimate GCS — useful for understanding MAVLink |
| **tcpdump** | Command-line packet capture | Lightweight capture on embedded Linux |

### Exploitation Frameworks

| Tool | Purpose | Key Usage |
|---|---|---|
| **Metasploit** | General exploitation framework | Some WiFi/BT modules available |
| **RouterSploit** | Embedded device exploitation | Router/AP-focused exploits (relevant for ground stations) |
| **Pwnagotchi** | Automated WiFi auditing | AI-driven WPA handshake capture |

---

## CVE Index

Alphabetical index of all CVEs referenced in this document.

| CVE | Section | Short Description |
|---|---|---|
| CVE-2007-5474 | [Qualcomm/Atheros](#qualcomm--atheros) | Atheros vendor IE malformed assoc request |
| CVE-2009-0052 | [Qualcomm/Atheros](#qualcomm--atheros) | AR9160 truncated mgmt frame crash/RCE |
| CVE-2017-0781 | [BlueBorne](#blueborne-bluetooth-classic-rce) | Android BNEP RCE |
| CVE-2017-0782 | [BlueBorne](#blueborne-bluetooth-classic-rce) | Android PAN integer underflow RCE |
| CVE-2017-1000250 | [BlueBorne](#blueborne-bluetooth-classic-rce) | Linux BlueZ SDP info leak |
| CVE-2017-1000251 | [BlueBorne](#blueborne-bluetooth-classic-rce) | Linux BlueZ L2CAP stack overflow RCE |
| CVE-2017-13077 | [KRACK](#krack-key-reinstallation-attacks) | WPA2 PTK-TK reinstallation |
| CVE-2017-13078 | [KRACK](#krack-key-reinstallation-attacks) | WPA2 GTK reinstallation (4-way) |
| CVE-2017-13080 | [KRACK](#krack-key-reinstallation-attacks) | WPA2 GTK reinstallation (group key) |
| CVE-2017-13082 | [KRACK](#krack-key-reinstallation-attacks) | WPA2 FT PTK-TK reinstallation |
| CVE-2017-14315 | [BlueBorne](#blueborne-bluetooth-classic-rce) | Apple LEAP BT audio heap overflow |
| CVE-2017-9417 | [Broadcom/Cypress](#broadcom--cypress-bcm43xx) | Broadpwn — BCM43xx WiFi RCE |
| CVE-2018-16986 | [BLEEDINGBIT](#bleedingbit-ti-cc2540cc2640) | TI CC2640/CC2650 BLE heap overflow RCE |
| CVE-2018-19860 | [Broadcom/Cypress](#broadcom--cypress-bcm43xx) | Broadcom BT firmware RCE |
| CVE-2018-7080 | [BLEEDINGBIT](#bleedingbit-ti-cc2540cc2640) | TI OAD insecure firmware update |
| CVE-2019-15126 | [Kr00k](#kr00k-wpa2-traffic-decryption) | Broadcom/Cypress zero-TK decryption |
| CVE-2019-16336 | [SweynTooth](#sweyntooth-ble) | BLE LL length overflow crash |
| CVE-2019-17060 | [SweynTooth](#sweyntooth-ble) | BLE LLID deadlock (alt ID) |
| CVE-2019-17061 | [SweynTooth](#sweyntooth-ble) | BLE LLID deadlock |
| CVE-2019-19193 | [BLEEDINGBIT](#bleedingbit-ti-cc2540cc2640) | TI CC2640R2 BLE DoS |
| CVE-2019-19194 | [SweynTooth](#sweyntooth-ble) | BLE zero-LTK security bypass |
| CVE-2019-19195 | [SweynTooth](#sweyntooth-ble) | BLE invalid L2CAP fragment crash |
| CVE-2019-9500 | [Broadcom/Cypress](#broadcom--cypress-bcm43xx) | brcmfmac WoWLAN heap overflow |
| CVE-2019-9502 | [Broadcom/Cypress](#broadcom--cypress-bcm43xx) | wl driver heap overflow |
| CVE-2019-9503 | [Broadcom/Cypress](#broadcom--cypress-bcm43xx) | Firmware event frame validation bypass |
| CVE-2019-9506 | [KNOB](#knob--bias-bluetooth-classic) | BT key entropy negotiation downgrade |
| CVE-2020-10135 | [BIAS](#knob--bias-bluetooth-classic) | BT impersonation / auth bypass |
| CVE-2020-15509 | [Nordic nRF52](#nordic-nrf52) | Nordic Android BLE encryption downgrade |
| CVE-2020-24586 | [FragAttacks](#fragattacks-fragmentation--aggregation) | WiFi fragment cache attack |
| CVE-2020-24587 | [FragAttacks](#fragattacks-fragmentation--aggregation) | WiFi mixed key fragment attack |
| CVE-2020-24588 | [FragAttacks](#fragattacks-fragmentation--aggregation) | WiFi A-MSDU aggregation attack |
| CVE-2020-26652 | [Realtek](#realtek-rtl88xx) | RTL8812AU DoS |
| CVE-2020-27211 | [Nordic nRF52](#nordic-nrf52) | nRF52840 APPROTECT bypass |
| CVE-2021-28139 | [BrakTooth](#braktooth-bluetooth-classic) | ESP32 BT Classic LMP overflow RCE |
| CVE-2021-29415 | [Nordic nRF52](#nordic-nrf52) | nRF52840 ECDSA timing side-channel |
| CVE-2021-35055 | [MediaTek](#mediatek-mt76xx) | MediaTek WPS OOB write |
| CVE-2023-6951 | [DJI WiFi](#dji-wifi--droneid) | DJI weak WiFi credentials |
| CVE-2024-20017 | [MediaTek](#mediatek-mt76xx) | MediaTek wappd stack overflow RCE |
| CVE-2025-20685 | [MediaTek](#mediatek-mt76xx) | MediaTek AP driver unauth RCE |
| CVE-2025-27840 | [ESP32](#espressif-esp32) | ESP32 hidden HCI commands |
| CVE-2025-47401 | [Qualcomm/Atheros](#qualcomm--atheros) | Qualcomm WLAN HAL buffer overrun DoS |
| CVE-2025-47403 | [Qualcomm/Atheros](#qualcomm--atheros) | Qualcomm WLAN firmware buffer overrun DoS |
| CVE-2025-8298 | [Realtek](#realtek-rtl88xx) | RTL8811AU OOB read info disclosure |
| CVE-2025-8301 | [Realtek](#realtek-rtl88xx) | RTL8811AU heap overflow LPE |
| CVE-2026-25254 | [Qualcomm/Atheros](#qualcomm--atheros) | Qualcomm Software Center unauth RCE |
| CVE-2026-25507 | [ESP32](#espressif-esp32) | ESP32 BLE provisioning OOB read |
| CVE-2026-25508 | [ESP32](#espressif-esp32) | ESP32 BLE provisioning use-after-free |
| CVE-2026-41429 | [ESP32](#espressif-esp32) | Arduino-ESP32 NBNS memory corruption |
| CVE-2026-45542 | [ESP32](#espressif-esp32) | ESP32 protocomm heap overflow |
| CVE-2026-68309 | [MediaTek](#mediatek-mt76xx) | mt76 driver NULL ptr deref DoS |
| CVE-2026-78306 | [DJI Bluetooth](#dji-bluetooth-vulnerabilities) | DJI BT command injection |
| CVE-2026-81507 | [ESP32](#espressif-esp32) | ESP32 BT Classic L2CAP OOB read |

---

## Sources & Further Reading

### Academic Papers & Conference Talks

- [Broadpwn — Black Hat 2017 (Nitay Artenstein)](https://thehackernews.com/2017/07/android-ios-broadcom-hacking.html)
- [BlueBorne — Armis Labs (2017)](https://en.wikipedia.org/wiki/BlueBorne_(security_vulnerability))
- [SweynTooth — ASSET Group, SUTD (2020)](https://asset-group.github.io/disclosures/sweyntooth/sweyntooth.pdf)
- [BrakTooth — ASSET Group, SUTD (2021)](https://threatpost.com/bluetooth-bugs-dos-code-execution/169159)
- [KRACK — Mathy Vanhoef (2017)](https://www.csoonline.com/article/3233290/security/krack-researcher-discovers-flaws-in-wpa2-authentication.html)
- [FragAttacks — Mathy Vanhoef (2021)](https://securityaffairs.com/posts/wifi-fragattacks)
- [Kr00k — ESET Research (2020)](https://en.wikipedia.org/wiki/Kr00k)
- [DJI DroneID — NDSS 2023 (Schiller et al.)](https://www.ndss-symposium.org/wp-content/uploads/2023-217-paper.pdf)
- [DJI Enhanced WiFi Hijacking (2023)](https://arxiv.org/pdf/2309.05913)
- [MAVLink Protocol Survey](https://arxiv.org/pdf/1906.10641)
- [MAVLink Vulnerability Analysis](https://www.researchgate.net/publication/326860779_Empirical_Analysis_of_MAVLink_Protocol_Vulnerability_for_Attacking_Unmanned_Aerial_Vehicles)
- [MAVLink Hijacking via Legitimate Commands (2026)](https://arxiv.org/html/2606.22289v1)
- [InternalBlue — Bluetooth Binary Patching Framework](https://arxiv.org/pdf/1905.00631)
- [L2Fuzz — BLE L2CAP Fuzzing](https://arxiv.org/pdf/2208.00110)
- [MUVIDS — MAVLink IDS (NDSS AutoSec 2021)](https://www.ndss-symposium.org/wp-content/uploads/autosec2021_23036_paper.pdf)

### Vendor Advisories & Databases

- [Qualcomm Security Bulletins](https://docs.qualcomm.com/securitybulletin/may-2026-bulletin.html)
- [Espressif ESP-IDF Security Vulnerabilities](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/security/vulnerabilities.html)
- [CERT/CC VU#166939 — Broadcom WiFi Driver Vulns](https://www.kb.cert.org/vuls/id/166939/)
- [CERT/CC VU#317277 — BLEEDINGBIT](https://kb.cert.org/vuls/id/317277)
- [CERT/CC VU#647177 — BIAS](https://kb.cert.org/vuls/id/647177/)
- [CISA ICS-ALERT-20-063-01 — SweynTooth](https://www.cisa.gov/news-events/ics-alerts/ics-alert-20-063-01)
- [TI Security Advisory SWRZ164A](https://www.ti.com.cn/document-viewer/cn/lit/html/SWRZ164A/GUID-D067D4AC-3D0C-4D5E-B009-ADA6D7AB36FF)
- [NVD (National Vulnerability Database)](https://nvd.nist.gov/)
- [OWASP Drone Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Drone_Security_Cheat_Sheet.html)

### Tool Documentation & Resources

- [PMKID Attack — hashcat Forum (atom)](https://hashcat.net/forum/post-41646.html)
- [Bettercap PMKID Walkthrough](https://evilsocket.net:443/2019/02/13/Pwning-WiFi-networks-with-bettercap-and-the-PMKID-client-less-attack/)
- [awesome-drone-hacking (GitHub)](https://www.context-awesome.com/list/nicholasaleks/awesome-drone-hacking)
- [Bluetooth Security Vulnerabilities (GitHub)](https://github.com/Shadow7726/Bluetooth-Security-Vulnerabilities)
- [DJI Drone RF Security Research (GitHub)](https://github.com/Akshit0203/drone-rf-security-hackrf)
- [ExpressLRS Security Analysis](https://simonkoeck.com/blog/elrs-security-what-is-possible)
- [ELRS Takeover — Threatpost (2022)](https://threatpost.com/drone-hack-expresslrs-hijacked/180133/)

### News Coverage

- [DJI Bluetooth Flaw CVE-2026-78306 — Cybernews](https://cybernews.com/security/severe-bluetooth-flaw-hackers-dji-drones-midair-takeover/)
- [ESP32 Hidden Commands — BleepingComputer](https://www.bleepingcomputer.com/news/security/undocumented-commands-found-in-bluetooth-chip-used-by-a-billion-devices/)
- [MediaTek CVE-2024-20017 Public PoC — CyberInsider](https://cyberinsider.com/mediatek-chip-flaw-exposing-millions-of-devices-gets-public-exploit/)
- [Nozomi Networks DJI Mavic 3 Research](https://www.nozominetworks.com/blog/dji-mavic-3-drone-research-part-2-vulnerability-analysis)
- [WiFi/BT Chip Coexistence Bugs — BleepingComputer](https://www.bleepingcomputer.com/news/security/bugs-in-billions-of-wifi-bluetooth-chips-allow-password-data-theft/)
