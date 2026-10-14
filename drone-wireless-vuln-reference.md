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
- [Autopilot & Flight Controller Vulnerabilities](#autopilot--flight-controller-vulnerabilities)
  - [PX4 Autopilot](#px4-autopilot)
  - [ArduPilot](#ardupilot)
- [GPS Spoofing](#gps-spoofing)
- [Coexistence & Cross-Chip Attacks](#coexistence--cross-chip-attacks)
  - [Spectra (WiFi ↔ Bluetooth Lateral Movement)](#spectra-wifi--bluetooth-lateral-movement)
- [Hardware Attack Surface](#hardware-attack-surface)
  - [Debug Interfaces (JTAG / SWD / UART)](#debug-interfaces-jtag--swd--uart)
  - [Firmware Extraction Techniques](#firmware-extraction-techniques)
- [Known Drone Hardware Profiles](#known-drone-hardware-profiles)
- [Consumer Drone Default Security Posture](#consumer-drone-default-security-posture)
  - [Parrot (Anafi / Bebop / AR.Drone)](#parrot-anafi--bebop--ardrone)
  - [Toy-Grade Drones (Tello, Syma, Hubsan)](#toy-grade-drones-tello-syma-hubsan)
- [Drone Attack Methodology](#drone-attack-methodology)
- [Advanced Chipset Exploitation Techniques](#advanced-chipset-exploitation-techniques)
  - [Why Wireless Chipsets Are Soft Targets](#why-wireless-chipsets-are-soft-targets)
  - [Broadcom BCM43xx Firmware Exploitation](#broadcom-bcm43xx-firmware-exploitation)
  - [Marvell Avastar ThreadX Exploitation](#marvell-avastar-threadx-exploitation)
  - [ESP32 Bluetooth Firmware Exploitation (BrakTooth)](#esp32-bluetooth-firmware-exploitation-braktooth)
  - [Qualcomm WLAN Firmware & Driver Exploitation](#qualcomm-wlan-firmware--driver-exploitation)
  - [Linux WiFi Driver Kernel Exploitation](#linux-wifi-driver-kernel-exploitation)
  - [Firmware-to-Host Escalation Patterns](#firmware-to-host-escalation-patterns)
  - [Firmware Reversing & Exploit Dev Toolchain](#firmware-reversing--exploit-dev-toolchain)
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
| **CVE-2026-1579** | PX4 Autopilot | MAVLink shell cmd exec | **RCE** (CVSS 9.8) | Radio range | No (no signing) | Unknown |
| **CVE-2026-86097** | PX4 Autopilot | param file NULL ptr deref | **DoS** (crash) | Shell access | Yes | Unknown |
| **CVE-2026-102808** | PX4 Autopilot | sd_stress malloc overflow | **DoS** (crash) | Shell access | Yes | Unknown |
| **CVE-2026-77812** | DJI (multiple) | BLE WiFi credential leak | Credential theft | BLE range | No | Unknown |
| **CVE-2019-3944** | Parrot Anafi | WiFi deauth susceptibility | **DoS** | WiFi range | No | Yes |
| **CVE-2019-3945** | Parrot Anafi | SDK date field crash | **DoS** (web server) | WiFi range | No | Yes |
| N/A | Parrot Bebop/AR.Drone | Open WiFi / Telnet root | **Full compromise** | WiFi range | No | Yes |
| N/A | Ryze Tello | No default WiFi password | Network access | WiFi range | No | N/A |
| N/A | GPS (civilian L1) | GPS spoofing | **Diversion/Hijack** | SDR range | No | Yes |
| N/A | SiK Radio (433/915 MHz) | NetID sniff + injection | **Hijack** | Radio range | No | Yes |
| **CVE-2020-12313** | Intel WiFi (AX200/AX210) | Adjacent-access escalation | **Priv Esc** | WiFi range | No | Unknown |
| **CVE-2022-21212** | Intel WiFi (AX200/AX210) | Input validation DoS | **DoS** | WiFi range | No | Unknown |

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

### Intel WiFi (AX200 / AX210)

Intel WiFi adapters appear in ground station laptops, companion computers, and some embedded platforms.

#### CVE-2020-12313 — Privilege Escalation (INTEL-SA-00402)

- **Impact:** Privilege escalation via adjacent access
- **Affected:** Intel PROSet/Wireless WiFi before v21.110
- **Auth required:** No — unauthenticated, adjacent range
- **Note:** Part of a broader Intel WiFi advisory covering multiple CVEs

#### CVE-2022-21212 — Input Validation DoS

- **Impact:** Denial of service via improper input validation
- **Affected:** AX200 and AX210 firmware
- **Auth required:** No — adjacent access

#### CVE-2021-0071 — UEFI Privilege Escalation

- **Impact:** Privilege escalation via adjacent access in PROSet/Wireless WiFi UEFI
- **Affected:** AX200 and AX210 firmware before v22.40

#### Linux iwlwifi Driver Issues (2025)

Recent kernel CVEs in the iwlwifi driver are mostly stability/DoS issues, not RCE:

| CVE | Impact | Detail |
|---|---|---|
| CVE-2025-38656 | Use-after-free (DoS) | Lost error code during DVM startup |
| CVE-2025-21930 | Instability | Commands sent to unresponsive firmware |
| CVE-2025-71226 | PTP failure | Missing callback in PTP clock registration |

---

### Synaptics (SYN43xxx)

Synaptics WiFi/BT combo chips are used in newer DJI drones (Mavic 3 Pro, Mini 3).

- **SYN43752A2XKUBG:** WiFi 802.11ax + Bluetooth 5.1 + FM receiver (found in Mavic 3 Pro per iFixit teardown)
- **SYN43456PXKUBG:** WiFi 802.11ac + Bluetooth 5 + FM receiver (found in Mini 3 per iFixit teardown)
- **Security research:** No public CVEs specific to these Synaptics parts as of this writing. However, Synaptics acquired Broadcom's wireless IoT business, so the firmware lineage may share code with the BCM43xx family — Broadcom-era vulnerabilities may be worth investigating on these chips.

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

#### CVE-2026-77812 — BLE WiFi Credential Leak (2026)

- **Impact:** WiFi credentials sent in cleartext over Bluetooth Low Energy
- **Mechanism:** DJI drones transmit WiFi SSID and password over BLE during certain operations; an attacker within BLE range can passively sniff and recover credentials
- **Affected:** Mini, Mavic, and Air series (specific models TBD)
- **CTF relevance:** Passive attack — just sniff BLE traffic near the drone to get WiFi credentials, then connect to the drone's WiFi network

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
- **Serial radio (SiK 433/915 MHz):** No encryption on standard SiK firmware (Si1000 CPU cannot support it). NetID can be sniffed from traffic and used to inject packets. RFD900+ models have a stronger CPU with beta AES encryption firmware — verify current status. Anyone with a radio using the same NetID can disarm a quadcopter remotely.
- **WiFi (UDP):** Standard WiFi attacks apply; MAVLink typically runs on UDP ports 14550/14555
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

## Autopilot & Flight Controller Vulnerabilities

### PX4 Autopilot

PX4 is one of the two dominant open-source autopilot stacks (the other being ArduPilot). It runs on flight controllers like Pixhawk and is used in commercial and research drones.

#### CVE-2026-1579 — MAVLink Remote Command Execution (CRITICAL)

- **CVSS:** 9.8
- **Impact:** Arbitrary shell command execution without authentication
- **Root cause:** When MAVLink message signing is not enabled (the default), anyone with access to the MAVLink interface can execute shell commands on the flight controller
- **Affected:** PX4 v1.16.0 and likely earlier
- **CISA advisory:** ICSA-26-090-02
- **CWE:** CWE-306 (Missing Authentication for Critical Function)
- **CTF relevance:** CRITICAL — this is the single most impactful finding for an open-source drone CTF. If the drone runs PX4 with default settings (no MAVLink signing), you can get a shell on the flight controller over any MAVLink transport (WiFi, serial radio, Bluetooth). From there: arm/disarm motors, change waypoints, modify parameters, exfiltrate data.

#### CVE-2026-86097 — Parameter File NULL Pointer Dereference

- **Impact:** Flight controller crash (DoS)
- **Root cause:** `param select` and `param select-backup` commands accept empty path arguments, causing NULL pointer dereference
- **Requires:** Shell access (via MAVLink or serial console)

#### CVE-2026-102808 — sd_stress Malloc Overflow

- **Impact:** Flight controller crash (DoS)
- **Root cause:** The `-b` byte count parameter is parsed without validation before being passed to `malloc()` and `memset()`
- **Requires:** Shell access

#### CVE-2026-102809 — file2 Stack Overflow

- **Impact:** Flight controller crash (DoS)
- **Root cause:** Excessively large value to `-c` option triggers stack overflow
- **CVSS:** 6.5
- **Requires:** Shell access

### ArduPilot

ArduPilot is the other major open-source autopilot. No specific CVEs were found in this research, but ArduPilot inherits all MAVLink protocol weaknesses documented in the [MAVLink Protocol](#mavlink-protocol) section. ArduPilot's default configuration does not enable MAVLink signing.

---

## GPS Spoofing

GPS spoofing is adjacent to wireless chipset attacks but highly relevant in drone CTFs — civilian GPS (L1 C/A) has no authentication.

### Attack Mechanism

1. **Signal generation:** Attacker generates counterfeit GPS signals using an SDR (HackRF, BladeRF, USRP)
2. **Signal overpowering:** Fake signals are transmitted at higher power than genuine satellites
3. **Receiver lock:** Drone's GPS receiver locks onto the spoofed signals
4. **Position manipulation:** Attacker controls the drone's perceived position, velocity, and time

### Research Findings

| Study | Year | Key Finding |
|---|---|---|
| Sathaye et al. (USENIX Security) | 2022 | Full takeover requires real-time signal manipulation; simple diversion is easier |
| "Tractor Beam" (ACM TOPS) | 2019 | Exploits drone failsafe mechanisms (RTH) to redirect aircraft by spoofing the "home" position |
| Scientific Reports (Nature) | 2026 | Real-time spoofing demonstrated with HackRF in outdoor trials; also developed telemetry-based detection |
| TechRxiv (Russia-Ukraine analysis) | 2025 | Battlefield GPS spoofing documented at scale; single-constellation reliance is a key weakness |

### Attack Modes

- **Diversion:** Shift the drone's perceived position to make it fly off course
- **Geofence manipulation:** Spoof position to trigger or bypass geofence boundaries
- **RTH hijack:** Spoof the "home" coordinates so Return-to-Home flies to attacker's location
- **Time spoofing:** Corrupt GPS time to break time-dependent protocols (MAVLink timestamps, log integrity)

### Tools

- **gps-sdr-sim:** Open-source GPS signal simulator for HackRF/BladeRF
- **HackRF One:** Transmit spoofed GPS signals (requires appropriate licensing/authorization)
- **GNSS-SDR:** Software-defined GPS receiver for analysis and testing

### CTF Relevance

GPS spoofing is often a CTF category of its own. Look for:
- Drones using single-constellation GPS (no GLONASS/Galileo cross-check)
- No IMU-based spoofing detection
- RTH failsafe that can be exploited by position spoofing

---

## Coexistence & Cross-Chip Attacks

### Spectra (WiFi ↔ Bluetooth Lateral Movement)

- **Discovered by:** Jiska Classen and Francesco Gringoli (2020)
- **Presented at:** Black Hat USA / DEF CON
- **Impact:** Privilege escalation between WiFi and Bluetooth chips on combo hardware

#### How It Works

Modern combo chips (Broadcom, Cypress, etc.) share spectrum via "coexistence" mechanisms. Spectra exploits this:

1. **Shared RAM:** Bluetooth and WiFi firmware share a RAM region. Writes from one side are immediately visible to the other.
2. **Escalation path:** Attacker gains code execution on the Bluetooth chip (e.g., via BrakTooth or BlueBorne), then writes to shared RAM to achieve code execution on the WiFi chip.
3. **OS escalation:** From the WiFi chip, existing WiFi firmware → kernel escalation techniques apply (e.g., Broadpwn-style).

#### Attack Chain Example

```
BLE exploit (BrakTooth/BlueBorne)
  → Code execution on BT firmware
    → Write to shared coexistence RAM
      → Code execution on WiFi firmware
        → Kernel exploit via WiFi driver
          → Full device compromise
```

#### Affected Hardware

- Broadcom and Cypress combo WiFi/BT chips (confirmed)
- Other vendors' combo chips may have similar shared-memory architectures
- **Relevance to drones:** Any drone using a Broadcom/Cypress/Synaptics combo chip (including newer DJI models) may be vulnerable to this cross-chip escalation

#### Additional Coexistence Effects

- Denial of service on spectrum access (jam WiFi from BT side or vice versa)
- Information disclosure across chip boundaries
- Kernel panics on Android/iOS during WiFi firmware code execution

---

## Hardware Attack Surface

### Debug Interfaces (JTAG / SWD / UART)

Physical access to debug interfaces can bypass all wireless security. These are highly relevant in CTFs where you may have hands-on access to hardware.

#### Interface Comparison

| Interface | Pins | Speed | Capability | Common On |
|---|---|---|---|---|
| **JTAG** | 4-20 | Medium | Full debug, flash R/W, boundary scan | Larger SoCs, FPGAs |
| **SWD** | 2 (SWDIO + SWCLK) | Fast | Full debug, flash R/W (ARM only) | ARM Cortex-M (STM32, nRF52, etc.) |
| **UART** | 2 (TX + RX) | Variable | Serial console, bootloader access, sometimes shell | Almost everything |

#### Common Obstacles

- **Unlabeled pads:** Most consumer drones have debug pads with no silkscreen labels
- **Locked debug ports:** Readout protection (RDP), APPROTECT, secure boot
- **Epoxy potting:** PCBs covered in epoxy to prevent probing
- **Removed test points:** Production boards may have debug pads depopulated
- **Multiplexed signals:** Debug pins shared with other functions

#### Techniques

1. **Visual inspection:** Look for unpopulated headers, test points, labeled pads
2. **Continuity testing:** Trace PCB connections from known chip pins to test points
3. **JTAGulator:** Automated pin identification for JTAG/SWD
4. **Voltage glitching:** Bypass readout protection (see nRF52 APPROTECT, CVE-2020-27211)
5. **UART discovery:** Common baud rates (115200, 921600); look for boot messages

#### Tools

| Tool | Purpose |
|---|---|
| **JTAGulator** | Automated JTAG/SWD pin discovery |
| **OpenOCD** | Open-source debug server for JTAG/SWD |
| **Flipper Zero** | JTAG/SWD interface with DAP support |
| **Bus Pirate** | Multi-protocol debug interface |
| **Logic analyzer** | Signal identification and protocol decode |
| **ChipWhisperer** | Voltage glitching for readout protection bypass |

### Firmware Extraction Techniques

#### Methods (in order of increasing difficulty)

1. **UART bootloader:** Connect to UART, interrupt boot, dump flash via bootloader commands
2. **SWD/JTAG read:** Connect debugger, read flash contents directly (if not read-protected)
3. **SPI flash clip:** Attach test clip directly to SPI flash chip, read with flashrom or Bus Pirate
4. **Firmware update capture:** Intercept OTA update traffic, extract firmware image
5. **Chip-off:** Desolder flash chip, read in external programmer
6. **Fault injection:** Glitch readout protection during boot (CVE-2020-27211 technique for nRF52)

#### What to Do With Extracted Firmware

- **binwalk:** Extract filesystem, identify compression, find embedded keys
- **Ghidra / IDA Pro:** Disassemble and reverse-engineer firmware
- **strings:** Find hardcoded credentials, URLs, API keys
- **entropy analysis:** Identify encrypted/compressed sections
- **emulation (QEMU/Unicorn):** Run firmware in emulator for dynamic analysis

#### Recent Research

A 2026 arXiv paper evaluated firmware extraction on three Holy Stone drone platforms using SPI flash reading, SWD/JTAG, UART boot messages, and clip-based methods. Key finding: consumer drones typically have unlabeled debug pads and undocumented memory layouts, but rarely enable readout protection.

---

## Known Drone Hardware Profiles

Mapping specific drone models to their wireless chipsets helps target the right exploits.

| Drone Model | WiFi Chip | BT Chip | Control Protocol | Source |
|---|---|---|---|---|
| **DJI Mavic 3 Pro** | Synaptics SYN43752 (802.11ax) | Integrated (BT 5.1) | OcuSync 3+ | iFixit teardown |
| **DJI Mini 3** | Synaptics SYN43456 (802.11ac) | Integrated (BT 5) | OcuSync 3 | iFixit teardown |
| **DJI Spark** | Qualcomm/Atheros AR1021X | Unknown | Enhanced WiFi | EDN teardown |
| **DJI Mini SE** | Unknown (WiFi-based) | Unknown | Enhanced WiFi | Security research |
| **Parrot Anafi** | Unknown (WiFi AP mode) | Unknown | WiFi direct | Tenable advisory |
| **Parrot Bebop 2** | Unknown (WiFi AP mode) | N/A | WiFi direct | Community research |
| **Parrot AR.Drone** | Atheros AR9271 (likely) | N/A | WiFi direct (open) | Community teardowns |
| **Ryze Tello** | Unknown (WiFi AP mode) | N/A | WiFi direct | Which? testing |
| **Generic ArduPilot** | Varies (ESP32/WiFi module) | Varies | MAVLink over WiFi/serial | Platform-dependent |
| **Generic PX4** | Varies | Varies | MAVLink over WiFi/serial | Platform-dependent |
| **FPV (ELRS)** | N/A | N/A | ExpressLRS 2.4GHz/900MHz | Community |
| **FPV (Crossfire)** | N/A | N/A | TBS CRSF 868/915MHz | Community |
| **Toy drones (nRF24)** | N/A | N/A | nRF24L01 2.4GHz | Community |

**Note:** Chipset identification is tentative for many models — iFixit and EDN teardowns are the most reliable sources. Confirm by visual inspection or FCC filings when possible.

---

## Consumer Drone Default Security Posture

### Parrot (Anafi / Bebop / AR.Drone)

Parrot drones historically have some of the weakest wireless security of any major drone brand.

#### AR.Drone (Legacy)

- **WiFi:** Open network (no password by default)
- **Telnet:** Root shell accessible over WiFi with no authentication
- **FTP:** Open FTP server for media access
- **Impact:** Complete takeover — connect to WiFi, telnet in as root
- **Status:** End of life, but commonly seen in CTFs and security labs

#### Bebop / Bebop 2

- **WiFi:** WPA2 with weak/predictable default passwords
- **No PMF:** Cannot enable 802.11w Protected Management Frames
- **Vulnerable to:** Deauthentication, ARP cache poisoning, buffer overflow, replay attacks
- **FTP:** Media accessible via FTP

#### Anafi

- **CVE-2019-3944:** Deauthentication vulnerability — forged deauth frames disconnect controller (fixed in firmware 1.5.0)
- **CVE-2019-3945:** Malformed date in SDK command crashes the web server
- **WiFi password:** Can be set, but default configuration may use weak/predictable passwords
- **Testing results:** KTH thesis found 7/10 DoS attempts succeeded against the Anafi

### Toy-Grade Drones (Tello, Syma, Hubsan)

#### Ryze Tello (DJI-powered)

- **WiFi:** Supports password but setup process does not prompt user to set one
- **Default state:** Open WiFi network
- **Camera:** Unencrypted video stream accessible to anyone on the network
- **DJI response:** "Tello supports communication encryption, and users can set up passwords" — but the default is open

#### Syma / Hubsan / Generic Toy Drones

- **WiFi:** No password protection on tested models (Which? lab 2021)
- **No encryption:** Camera feeds transmitted in cleartext
- **Control link:** Many use nRF24L01 with no encryption or rolling codes — fully replayable with SDR
- **App security:** Controller apps often lack TLS; some transmit telemetry in cleartext

---

## Drone Attack Methodology

A structured approach for assessing drone wireless security in a CTF context. Adapted from the Lockheed Martin cyber kill chain for cyber-physical drone systems.

### Phase 1: Reconnaissance

| Action | Tools | Output |
|---|---|---|
| Spectrum scan (2.4 GHz, 5 GHz, 433/915 MHz) | RTL-SDR, HackRF, `hackrf_sweep` | Active frequencies, modulation types |
| WiFi AP enumeration | `airodump-ng`, `kismet` | SSIDs, BSSIDs, channels, encryption type |
| BLE device scan | `bluetoothctl`, `bettercap`, `hcitool lescan` | BLE addresses, service UUIDs, device names |
| DroneID capture (DJI) | `gr-dji`, RTL-SDR | Drone position, pilot position, serial number |
| MAVLink traffic capture | Wireshark (MAVLink dissector), `tcpdump` | System ID, component ID, message types |
| Identify drone model | Visual + RF signature | Narrow chipset and protocol possibilities |

### Phase 2: Vulnerability Identification

| Check | Method | What You're Looking For |
|---|---|---|
| WiFi encryption type | `airodump-ng` output | Open/WEP/WPA2; presence of PMF |
| Default credentials | Known defaults for model | Tello (open), Bebop (weak), Parrot AR.Drone (none) |
| MAVLink signing | Capture MAVLink traffic | Unsigned packets = full injection capability |
| BLE services | GATT enumeration | Writable characteristics, provisioning services |
| Firmware version | MAVLink AUTOPILOT_VERSION, BLE device info | Map to known CVEs |
| Protocol version | ELRS sync packets, MAVLink header | ELRS 1.x/2.x vs 3.x, MAVLink v1 vs v2 |

### Phase 3: Exploitation

| Target | Primary Attack | Fallback |
|---|---|---|
| **WiFi-controlled drone (open)** | Connect directly, access telnet/FTP/API | Deauth + reconnect |
| **WiFi-controlled drone (WPA2)** | PMKID capture + crack, or deauth + handshake | Default password, Kr00k if Broadcom/Cypress |
| **DJI (Enhanced WiFi)** | CVE-2026-78306 (BT command injection) | WiFi MitM on Enhanced WiFi models |
| **DJI (OcuSync)** | CVE-2026-78306 (BT), CVE-2026-77812 (BLE cred leak) | DroneID passive recon |
| **MAVLink drone (PX4)** | CVE-2026-1579 (shell via MAVLink) | MAVLink command injection (unsigned) |
| **MAVLink drone (ArduPilot)** | MAVLink injection (unsigned default) | Parameter tampering, waypoint override |
| **ELRS 1.x/2.x FPV drone** | Binding phrase recovery + link hijack | Jamming |
| **Toy drone (nRF24)** | SDR capture + replay | Jamming |
| **Any drone (GPS)** | GPS spoofing (gps-sdr-sim + HackRF) | — |

### Phase 4: Post-Exploitation

Once you have access:

- **Data exfiltration:** Flight logs, GPS tracks, camera feeds, stored media
- **Persistent access:** Modify firmware, install backdoor, change credentials
- **Lateral movement:** WiFi → BT via Spectra coexistence, MAVLink → companion computer
- **Physical impact:** Change waypoints, disable geofence, modify failsafe behavior, alter PID gains

---

## Advanced Chipset Exploitation Techniques

This section covers the low-level exploitation mechanics of wireless chipset attacks — heap overflows, function pointer overwrites, ROP chains, firmware-to-host escalation, and the properties of embedded targets that make these techniques practical.

### Why Wireless Chipsets Are Soft Targets

Wireless chipset firmware runs on a separate processor (typically ARM Cortex-R4 or Cortex-M) from the main application processor. These embedded environments lack the security mitigations found on modern desktop/mobile CPUs:

| Property | Desktop/Mobile OS | Wireless Chipset Firmware |
|---|---|---|
| **ASLR** | Yes — randomized memory layout | **No** — firmware loaded to fixed addresses on every boot |
| **DEP/NX** | Yes — non-executable data pages | **No** — all RAM is typically RWX (read-write-execute) |
| **Stack canaries** | Yes (compiler-inserted) | **Rarely** — most firmware compiled without |
| **Heap hardening** | Safe unlinking, heap cookies | **No** — simple freelists, no integrity checks |
| **Code signing** | Kernel enforces signed code | **Varies** — some vendors sign firmware, many don't |
| **Secure boot** | Chain of trust from bootloader | **Rare** on WiFi/BT SoCs |

**Implications for exploitation:**
- No ASLR means addresses are **deterministic per firmware version** — build a dictionary of gadget addresses, retry until the firmware version matches
- No DEP means **shellcode runs directly in the heap** — no need for ROP chains in many cases, though ROP is still useful for pivoting
- No heap hardening means **heap metadata overwrites are trivially exploitable** — no safe unlinking to bypass
- Firmware is loaded by the host OS on every reset and is often unencrypted, making **static analysis straightforward**

### Broadcom BCM43xx Firmware Exploitation

The BCM43xx family is the most thoroughly researched wireless chipset from an exploitation perspective, thanks to Google Project Zero and Exodus Intelligence.

#### Architecture

- **Processor:** ARM Cortex-R4 (runs all frame processing logic)
- **OS:** HNDRTE (Broadcom proprietary lightweight RTOS)
- **Memory:** All RAM is RWX — no DEP, no ASLR
- **Heap:** Simple freelist allocator with **no safe unlinking and no heap header cookies**
- **Firmware loading:** Unencrypted, loaded by the host driver on every chip reset

#### Broadpwn Exploitation Mechanics (CVE-2017-9417)

**The bug:** A WMM information element buffer is allocated at 0x2c (44) bytes, but IEs can be up to 0xff (255) bytes. This gives ~211 bytes of controlled overflow.

**Why it's reliable:**
1. The overflow always lands on the **same heap structure** — no heap feng-shui needed
2. Addresses are deterministic per firmware build — attacker builds a **per-firmware address dictionary** and retries until the version matches
3. The WiFi chip scans for APs every few seconds, even in power-saving mode — the **ring buffer fills with attacker-controlled data** automatically
4. The attacker broadcasts malicious beacons — the victim's chip parses them during background scanning with **zero user interaction**

**Exploitation flow:**
1. Broadcast crafted WiFi beacons containing oversized WMM IEs
2. Victim chip processes beacons during background scan
3. Heap overflow corrupts adjacent heap metadata / structures
4. Overwritten pointer redirects execution into the ring buffer (which contains attacker's payload)
5. Payload executes as shellcode directly in RAM (RWX, no DEP)
6. **Result:** Code execution on the WiFi SoC — from there, escalate to host

**Key insight:** Because the ring buffer continuously fills with beacon data the attacker controls, it serves as a **reliable payload staging area** without needing traditional heap spraying.

#### Project Zero "Over The Air" Exploitation (CVE-2017-0561)

Gal Beniamini's research documented two distinct exploitation paths:

**Part 1 — Firmware heap freelist corruption:**
- TDLS teardown frame triggers a heap overflow (~25 bytes past buffer boundary)
- Attacker reverse-engineers the heap allocator to locate the freelist head pointer in RAM
- Overflow corrupts the freelist — next allocation returns an attacker-controlled address
- This is a **write-what-where primitive** achieved through heap metadata corruption
- **Constraint:** Requires attacker and victim on the same WPA2 network

**Part 2 — Host kernel heap overflow via WiFi events:**
- A separate bug in the host driver's event handler fails to validate the relationship between `total_count` and `pkt_count`
- Attacker (with WiFi firmware code execution) crafts event frames that trigger a **kernel SLUB heap overflow**
- The researcher shaped the SLUB heap layout using controllable allocations so the overflow target was predictable
- **Result:** WiFi firmware code execution → host kernel compromise

**Key takeaway for exploitation:** Broadcom's heap allocator performs no safe unlinking and has no heap cookies — once you have a heap overflow, converting it to code execution is straightforward compared to modern userspace heap exploitation.

### Marvell Avastar ThreadX Exploitation

#### CVE-2019-6496 — Block Pool Overflow (Zero-Click)

**The bug:** Block pool overflow in the WiFi frame parser, triggered during background network scanning.

**Architecture:**
- **RTOS:** ThreadX (real-time OS — common in embedded WiFi/BT chips)
- **Heap:** ThreadX block pools — fixed-size allocations with metadata headers
- **Affected chips:** 88W8787, 88W8797, 88W8801, 88W8897, 88W8997
- **Found in:** Microsoft Surface, Xbox One, PlayStation 4, Samsung Chromebooks, Galaxy J1, Steam Link

**Why it's zero-click:** The scan parser runs on a **5-minute timer** regardless of whether the device is connected to a network. The victim device scans for networks automatically, parses attacker-controlled probe responses, and hits the overflow — "with nobody touching it."

**Exploitation approach (Denis Selianin / Embedi):**
1. Reverse-engineer ThreadX memory management wrappers and metadata headers
2. Overflow corrupts block pool metadata — specifically the **next-free-block pointer**
3. Attacker controls the location of the next allocation
4. Next allocation writes attacker data to an arbitrary address → **write-what-where primitive**
5. Overwrite a function pointer or return address → code execution
6. Escalation to host is possible via DMA if the host OS permits it

**Key technique:** The ThreadX block pool allocator uses linked lists for free blocks. Overwriting the next-free pointer gives an arbitrary-write primitive — the same class of attack as glibc unlink exploitation, but without any of the mitigations.

### ESP32 Bluetooth Firmware Exploitation (BrakTooth)

#### CVE-2021-28139 — LMP Feature Page Overflow → Function Pointer Hijack

**The bug:** Missing bounds check in the ESP32 Bluetooth library when processing `LMP_feature_response_ext` packets. An attacker can write **8 bytes of arbitrary data** past the Extended Feature Page Table.

**Exploitation mechanics:**
1. The BT library stores **callback function pointers** within the out-of-bounds offset reachable from the Feature Page Table
2. Attacker sends a crafted LMP Extended Feature Response with malicious data in the feature bits
3. The 8-byte overflow overwrites a **stored callback pointer**
4. The callback is eventually invoked during the normal Bluetooth connection lifecycle
5. Execution redirects to attacker-controlled address → **arbitrary code execution**

**What makes this practical:**
- No pairing or authentication required — works against any ESP32 with BT Classic enabled in radio range
- Firmware addresses are fixed (no ASLR) — callback target can be a known function or shellcode in a controlled buffer
- Researchers demonstrated **NVRAM erasure** and **disabling both WiFi and Bluetooth** on the target

**Tooling:** The BrakTooth PoC toolkit is [publicly available on GitHub](https://github.com/Matheus-Garbelini/braktooth_esp32_bluetooth_classic_attacks). It requires an ESP32 dev board with custom LMP firmware as the attack platform.

**Attack hardware requirements:**
- ESP32 development kit (attack transmitter)
- Custom Link Manager Protocol (LMP) firmware (provided in PoC repo)
- Computer running the PoC tool
- Target ESP32 device within Bluetooth range

### Qualcomm WLAN Firmware & Driver Exploitation

Qualcomm's WLAN stack has had multiple memory corruption bugs, though fewer have public exploitation details compared to Broadcom.

#### Key Vulnerability Classes

| CVE | Type | Location | Impact |
|---|---|---|---|
| CVE-2019-14114 | Buffer overflow | WLAN firmware GTK IE parsing | RCE — GTK key larger than buffer (CVSS 9.8) |
| CVE-2019-10546 | Heap overflow | WLAN firmware | RCE — remote, no auth |
| CVE-2021-1972 | Integer overflow | WLAN firmware (extscan hostlist) | Code exec with elevated privileges |
| CVE-2026-21385 | Integer overflow → heap corruption | WLAN alignment math | RCE — in CISA KEV catalog (actively exploited) |
| CVE-2016-5342 | Heap overflow | wcnss_wlan Linux driver | Kernel compromise via /dev/wcnss_wlan |

#### CVE-2026-21385 — Actively Exploited in the Wild

- **In the CISA Known Exploited Vulnerabilities catalog** — confirmed real-world exploitation
- Integer overflow in alignment calculation during memory allocation
- Affects QCA WiFi/Ethernet chips
- Patched in Qualcomm's March 2026 bulletin
- **CTF relevance:** If the target uses Qualcomm WiFi and hasn't applied the March 2026 patch, this is a live RCE vector

#### QuAlPwn Escalation Pattern (2019)

QuAlPwn demonstrated the full chain on Qualcomm WiFi:
1. Over-the-air WiFi frame triggers firmware vulnerability
2. Gain code execution on the WiFi firmware (separate ARM core)
3. Exploit shared memory / DMA interface to write into host kernel memory
4. Achieve kernel code execution on the application processor
5. **Full device compromise from a WiFi frame**

### Linux WiFi Driver Kernel Exploitation

WiFi driver bugs in the Linux kernel are valuable because they can give **kernel-level code execution** on the host processor, bypassing all firmware-level boundaries.

#### Relevant Driver Vulnerability Classes

| Driver | Chipset Family | Example CVEs | Typical Bug Class |
|---|---|---|---|
| **brcmfmac** | Broadcom/Cypress | CVE-2019-9500, CVE-2019-9502 | Heap overflow in event/IE processing |
| **ath9k/ath10k/ath11k** | Qualcomm/Atheros | CVE-2025-39746 | Use-after-free, shutdown races |
| **iwlwifi** | Intel | CVE-2025-38656, CVE-2023-53524 | Use-after-free, integer overflow |
| **mt76** | MediaTek | CVE-2026-68309 | NULL ptr deref, HE capability parsing |
| **mwlwifi** | Marvell | (unnumbered) | Heap overflow in auth path |
| **rtl8812au** (OOT) | Realtek | CVE-2020-26652 | DoS in nl80211 |

#### Exploitation Pattern: WiFi Frame → Kernel Heap Overflow

1. Attacker sends crafted WiFi management or data frames over the air
2. The kernel WiFi driver processes the frame in **kernel context**
3. A length/bounds check is missing — data overflows a **kernel heap buffer (SLUB/SLAB)**
4. Attacker shapes the heap (spray objects of known size) so the overflow corrupts a **target object**
5. Target object contains a function pointer (e.g., `sk_buff` operations, file operations, timer callbacks)
6. Overwritten function pointer redirects execution to attacker-controlled data
7. **Result:** Kernel code execution from a WiFi frame

#### Key Technique: SLUB Heap Shaping

The Linux SLUB allocator groups same-sized allocations into slabs. To exploit a heap overflow:
1. Spray objects of the same size as the vulnerable buffer to fill the slab
2. Free one object to create a "hole" adjacent to the vulnerable buffer
3. Allocate the target object (with function pointers) into the hole
4. Trigger the overflow — it corrupts the adjacent target object
5. Trigger the function pointer call — execution redirected

This technique is well-documented for netfilter (CVE-2024-1086, 99.4% success rate) and applies identically to WiFi driver heap overflows.

### Firmware-to-Host Escalation Patterns

Once you have code execution on a wireless chipset's firmware, the next goal is escalating to the host application processor. There are several documented patterns:

#### Pattern 1: DMA (Direct Memory Access)

- WiFi/BT chips often have DMA access to host memory for transferring frames
- With firmware code execution, the attacker can **read/write arbitrary host memory via DMA**
- This bypasses all software-level protections on the host
- **Mitigation:** IOMMU (if configured) restricts DMA to specific memory regions
- **Research:** Marvell Avastar exploitation noted that "exploitation of the Wi-Fi device can lead to exploitation of the host application processor in some cases, but this depends on host OS hardening and the availability of DMA"

#### Pattern 2: Malicious Event Injection

- The WiFi chip sends events to the host driver (association results, scan data, firmware status)
- With firmware control, the attacker crafts **malicious events** that trigger driver bugs
- Project Zero's Broadcom Part 2: injected event frames trigger a kernel heap overflow in the host driver
- **This is the most common escalation path** — it doesn't require DMA and works even with IOMMU

#### Pattern 3: Shared Memory / Coexistence (Spectra)

- Combo WiFi/BT chips share RAM between the two radios
- Compromise one radio → write to shared memory → compromise the other
- From the WiFi side, the host driver interface may be more permissive
- **Chain:** BT exploit → shared memory write → WiFi firmware control → host driver exploit

#### Pattern 4: Firmware Update Poisoning

- If the host loads firmware on every chip reset (common for Broadcom), a persistent attacker can:
  1. Modify the firmware image on the host filesystem
  2. Chip resets → host loads the backdoored firmware
  3. Persistent code execution on the wireless chip across reboots

#### Complete Exploitation Chain Example

```
Attacker broadcasts malicious WiFi beacons
  → Victim chip processes beacons during background scan
    → Heap overflow in firmware (no ASLR, no DEP)
      → Shellcode executes on WiFi SoC (ARM Cortex-R4)
        → Craft malicious event frames to host driver
          → Kernel heap overflow in brcmfmac/wl driver
            → SLUB heap shaping → function pointer overwrite
              → Kernel code execution on application processor
                → Full device compromise
```

**Total interaction required from victim:** Zero. The entire chain is triggered by the victim's chip performing routine background WiFi scans.

### Firmware Reversing & Exploit Dev Toolchain

#### Static Analysis

| Tool | Purpose | Key Workflow |
|---|---|---|
| **Ghidra** | Free disassembler/decompiler (NSA) | Load firmware binary, set ARM Cortex-R/M architecture, identify RTOS entry points and heap management functions |
| **IDA Pro** | Commercial disassembler | Better ARM support for some targets; FLIRT signatures for RTOS libraries |
| **binwalk** | Firmware extraction | Extract filesystems, identify compression, find embedded binaries |
| **radare2 / rizin** | Scriptable reverse engineering | Quick firmware triage, string search, cross-references |

#### Dynamic Analysis & Emulation

| Tool | Purpose | Key Workflow |
|---|---|---|
| **Frankenstein** | Broadcom/Cypress firmware emulation | Emulate firmware in QEMU user-mode, fuzz with AFL/libFuzzer, find crashes, develop exploits without hardware |
| **InternalBlue** | Broadcom BT firmware patching & debugging | Inject patches into running firmware via HCI, set breakpoints, read/write firmware memory, live experimentation |
| **QEMU** | ARM system/user emulation | Emulate firmware for crash analysis; pair with Frankenstein for wireless-specific targets |
| **Unicorn Engine** | Lightweight CPU emulator | Emulate specific firmware functions for targeted fuzzing |

#### Fuzzing

| Tool | Purpose | Key Workflow |
|---|---|---|
| **Frankenstein + AFL** | Coverage-guided firmware fuzzing | Emulate WiFi/BT frame parsers, fuzz with AFL, find heap overflows and crashes |
| **BrakTooth PoC toolkit** | BT Classic LMP fuzzing | ESP32-based fuzzer for LMP protocol — finds BrakTooth-class bugs |
| **Bluetooth Fuzzing Framework** | L2CAP/GATT/SMP fuzzing | Protocol-aware fuzzing of BLE stack layers |
| **wifuzzit / owfuzz** | WiFi frame fuzzing | Send malformed 802.11 frames, monitor for crashes |

#### Exploit Development

| Technique | When to Use | Tool Support |
|---|---|---|
| **Direct shellcode** | Firmware target with RWX memory (most chipsets) | Write ARM shellcode, place in ring buffer / heap spray, jump to it |
| **Function pointer overwrite** | Heap overflow near stored callbacks (BrakTooth pattern) | Identify callbacks in Ghidra, calculate overflow distance |
| **Heap metadata corruption** | Heap overflow with no safe unlinking (Broadcom, ThreadX) | Overwrite freelist pointer → arbitrary write → code execution |
| **ROP chain** | When DEP is enabled (rare on chipsets, common on host kernel) | Use ropper/ROPgadget on kernel or firmware binary to find gadgets |
| **SLUB heap shaping** | Linux kernel driver exploitation | Spray same-size objects, create holes, place target objects adjacent to overflow |
| **DMA abuse** | Firmware-to-host escalation | From firmware, use DMA engine to read/write host kernel memory |

#### Key References for Exploit Development

| Resource | What It Covers |
|---|---|
| [Project Zero: Over The Air Part 1](https://projectzero.google/2017/04/over-air-exploiting-broadcoms-wi-fi_4.html) | Broadcom firmware heap analysis, freelist corruption, TDLS overflow |
| [Project Zero: Over The Air Part 2](https://projectzero.google/2017/04/over-air-exploiting-broadcoms-wi-fi_11.html) | Host kernel exploitation via malicious WiFi events, SLUB shaping |
| [Exodus Intelligence: Broadpwn](https://blog.exodusintel.com/2017/07/26/broadpwn/) | WMM IE overflow, ring buffer payload staging, per-firmware address dictionary |
| [Black Hat: Broadpwn whitepaper](https://blackhat.com/docs/us-17/thursday/us-17-Artenstein-Broadpwn-Remotely-Compromising-Android-And-iOS-Via-A-Bug-In-Broadcoms-Wifi-Chipsets-wp.pdf) | Full technical detail on Broadpwn exploitation chain |
| [Frankenstein: Advanced Wireless Fuzzing (USENIX)](https://www.usenix.org/system/files/sec20-ruge.pdf) | Firmware emulation, fuzzing, BT escalation targets |
| [InternalBlue (GitHub)](https://github.com/seemoo-lab/internalblue) | Broadcom BT firmware experimentation framework |
| [BrakTooth PoC (GitHub)](https://github.com/Matheus-Garbelini/braktooth_esp32_bluetooth_classic_attacks) | ESP32 BT Classic attack toolkit |
| [Quarkslab: Reversing Broadcom WiFi](https://blog.quarkslab.com/reverse-engineering-broadcom-wireless-chipsets.html) | Firmware analysis, allocator hooking, emulation |
| [Embedi: Marvell Avastar ThreadX](https://www.bleepingcomputer.com/news/security/vulnerabilities-found-in-highly-popular-firmware-for-wifi-chips/) | ThreadX block pool exploitation, zero-click scan overflow |
| [HackerNotes Ep. 157: MediaTek WiFi Heap Overflow](https://blog.criticalthinkingpodcast.io/p/hackernotes-ep-157-kernel-driver-exploitation-mediatek-wi-fi-heap-overflow-to-root-shell-with-hypr) | MediaTek mt76 driver kernel exploitation walkthrough |
| [Coexistence Attacks (arXiv)](https://arxiv.org/pdf/2112.05719) | Cross-chip escalation via shared memory on combo WiFi/BT SoCs |
| [Awesome Embedded Vuln Research (GitHub)](https://github.com/IamAlch3mist/Awesome-Embedded-Systems-Vulnerability-Research) | Curated links for embedded firmware exploit development |

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
| CVE-2026-86097 | [PX4 Autopilot](#px4-autopilot) | PX4 param file NULL ptr deref DoS |
| CVE-2026-102808 | [PX4 Autopilot](#px4-autopilot) | PX4 sd_stress malloc overflow DoS |
| CVE-2026-102809 | [PX4 Autopilot](#px4-autopilot) | PX4 file2 stack overflow DoS |
| CVE-2026-1579 | [PX4 Autopilot](#px4-autopilot) | PX4 MAVLink shell command RCE |
| CVE-2026-77812 | [DJI WiFi](#dji-wifi--droneid) | DJI BLE WiFi credential leak |
| CVE-2019-3944 | [Parrot](#parrot-anafi--bebop--ardrone) | Parrot Anafi deauth DoS |
| CVE-2019-3945 | [Parrot](#parrot-anafi--bebop--ardrone) | Parrot Anafi web server crash |
| CVE-2020-12313 | [Intel WiFi](#intel-wifi-ax200--ax210) | Intel WiFi privilege escalation |
| CVE-2021-0071 | [Intel WiFi](#intel-wifi-ax200--ax210) | Intel WiFi UEFI priv esc |
| CVE-2022-21212 | [Intel WiFi](#intel-wifi-ax200--ax210) | Intel WiFi input validation DoS |
| CVE-2025-38656 | [Intel WiFi](#intel-wifi-ax200--ax210) | iwlwifi use-after-free DoS |
| CVE-2019-6496 | [Marvell Avastar](#marvell-avastar-threadx-exploitation) | ThreadX block pool overflow zero-click RCE |
| CVE-2019-10546 | [Qualcomm WLAN](#qualcomm-wlan-firmware--driver-exploitation) | Qualcomm WLAN firmware heap overflow RCE |
| CVE-2019-14114 | [Qualcomm WLAN](#qualcomm-wlan-firmware--driver-exploitation) | Qualcomm WLAN GTK IE buffer overflow RCE |
| CVE-2021-1972 | [Qualcomm WLAN](#qualcomm-wlan-firmware--driver-exploitation) | Qualcomm WLAN extscan integer overflow |
| CVE-2026-21385 | [Qualcomm WLAN](#qualcomm-wlan-firmware--driver-exploitation) | Qualcomm WLAN integer overflow RCE (CISA KEV) |
| CVE-2016-5342 | [Qualcomm WLAN](#qualcomm-wlan-firmware--driver-exploitation) | wcnss_wlan driver heap overflow |
| CVE-2017-0561 | [Broadcom Exploitation](#broadcom-bcm43xx-firmware-exploitation) | Broadcom TDLS heap overflow RCE |
| CVE-2023-53524 | [Linux WiFi Drivers](#linux-wifi-driver-kernel-exploitation) | iwlwifi debugfs integer overflow |

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
- [Spectra — Coexistence Attacks on WiFi/BT Combo Chips (2020)](https://arxiv.org/pdf/2112.05719)
- [Frankenstein — BT Firmware Fuzzing for Escalation (2020)](https://arxiv.org/pdf/2006.09809)
- [DJI DroneID — NDSS 2023 (Schiller et al.)](https://www.ndss-symposium.org/wp-content/uploads/2023-217-paper.pdf)
- [DJI Enhanced WiFi Hijacking (2023)](https://arxiv.org/pdf/2309.05913)
- [MAVLink Protocol Survey](https://arxiv.org/pdf/1906.10641)
- [MAVLink Vulnerability Analysis](https://www.researchgate.net/publication/326860779_Empirical_Analysis_of_MAVLink_Protocol_Vulnerability_for_Attacking_Unmanned_Aerial_Vehicles)
- [MAVLink Hijacking via Legitimate Commands (2026)](https://arxiv.org/html/2606.22289v1)
- [GPS Spoofing and Takeover of UAVs — USENIX Security 2022](https://www.usenix.org/system/files/sec22-sathaye.pdf)
- [Tractor Beam — Safe-Hijacking of Consumer Drones (ACM TOPS 2019)](https://dl.acm.org/doi/10.1145/3309735)
- [GPS Spoofing Detection via Telemetry — Scientific Reports 2026](https://www.nature.com/articles/s41598-026-53481-9)
- [GPS Spoofing in Russia-Ukraine War — TechRxiv 2025](https://www.techrxiv.org/doi/full/10.36227/techrxiv.175203757.71749390/v1)
- [Drone Firmware Extraction Methodology — arXiv 2026](https://arxiv.org/html/2605.11040v1)
- [Drone Hacking Part 1: Firmware Dumping — Neodyme](https://neodyme.io/en/blog/drone_hacking_part_1/)
- [Electronic Warfare and UAV Avionics Survey (2025)](https://arxiv.org/pdf/2504.07358)
- [SoK: Security and Privacy in the Age of Drones](https://arxiv.org/pdf/1903.05155)
- [RAND: How to Analyze the Cyber Threat from Drones](https://www.rand.org/content/dam/rand/pubs/research_reports/RR2900/RR2972/RAND_RR2972.pdf)
- [InternalBlue — Bluetooth Binary Patching Framework](https://arxiv.org/pdf/1905.00631)
- [L2Fuzz — BLE L2CAP Fuzzing](https://arxiv.org/pdf/2208.00110)
- [MUVIDS — MAVLink IDS (NDSS AutoSec 2021)](https://www.ndss-symposium.org/wp-content/uploads/autosec2021_23036_paper.pdf)
- [Project Zero: Over The Air Part 1 — Broadcom WiFi Firmware](https://projectzero.google/2017/04/over-air-exploiting-broadcoms-wi-fi_4.html)
- [Project Zero: Over The Air Part 2 — Host Kernel Exploitation](https://projectzero.google/2017/04/over-air-exploiting-broadcoms-wi-fi_11.html)
- [Exodus Intelligence: Broadpwn Technical Analysis](https://blog.exodusintel.com/2017/07/26/broadpwn/)
- [Black Hat 2017: Broadpwn Whitepaper](https://blackhat.com/docs/us-17/thursday/us-17-Artenstein-Broadpwn-Remotely-Compromising-Android-And-iOS-Via-A-Bug-In-Broadcoms-Wifi-Chipsets-wp.pdf)
- [BrakTooth: Causing Havoc on Bluetooth Link Manager (ASSET Group)](https://asset-group.github.io/disclosures/braktooth/disclosure.html)
- [BrakTooth Paper (PDF)](https://asset-group.github.io/papers/BrakTooth.pdf)
- [Quarkslab: Reverse Engineering Broadcom Wireless Chipsets](https://blog.quarkslab.com/reverse-engineering-broadcom-wireless-chipsets.html)
- [HackerNotes Ep. 157: MediaTek WiFi Heap Overflow to Root Shell](https://blog.criticalthinkingpodcast.io/p/hackernotes-ep-157-kernel-driver-exploitation-mediatek-wi-fi-heap-overflow-to-root-shell-with-hypr)
- [CVE-2024-1086: Universal Linux LPE (netfilter/SLUB technique reference)](https://github.com/Notselwyn/CVE-2024-1086)
- [Awesome Embedded Systems Vulnerability Research](https://github.com/IamAlch3mist/Awesome-Embedded-Systems-Vulnerability-Research)

### Vendor Advisories & Databases

- [Qualcomm Security Bulletins](https://docs.qualcomm.com/securitybulletin/may-2026-bulletin.html)
- [Espressif ESP-IDF Security Vulnerabilities](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/security/vulnerabilities.html)
- [CERT/CC VU#166939 — Broadcom WiFi Driver Vulns](https://www.kb.cert.org/vuls/id/166939/)
- [CERT/CC VU#317277 — BLEEDINGBIT](https://kb.cert.org/vuls/id/317277)
- [CERT/CC VU#647177 — BIAS](https://kb.cert.org/vuls/id/647177/)
- [CISA ICS-ALERT-20-063-01 — SweynTooth](https://www.cisa.gov/news-events/ics-alerts/ics-alert-20-063-01)
- [CISA ICSA-26-090-02 — PX4 Autopilot](https://www.cisa.gov/news-events/ics-advisories/icsa-26-090-02)
- [Intel WiFi Advisory INTEL-SA-00402](https://www.intel.com/content/www/us/en/security-center/advisory/intel-sa-00402.html)
- [TI Security Advisory SWRZ164A](https://www.ti.com.cn/document-viewer/cn/lit/html/SWRZ164A/GUID-D067D4AC-3D0C-4D5E-B009-ADA6D7AB36FF)
- [Tenable TRA-2019-22 — Parrot Anafi DoS](https://www.tenable.com/security/research/tra-2019-22)
- [NVD (National Vulnerability Database)](https://nvd.nist.gov/)
- [OWASP Drone Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Drone_Security_Cheat_Sheet.html)

### Teardowns & Hardware Analysis

- [DJI Mavic 3 Pro Chip ID — iFixit](https://www.ifixit.com/Guide/DJI+Mavic+3+Pro+Chip+ID/161708)
- [DJI Mini 3 Chip ID — iFixit](https://www.ifixit.com/Teardown/DJI+Mini+3+Chip+ID/160247)
- [DJI Spark Teardown — EDN](https://www.edn.com/teardown-dji-spark-drone/)
- [Parrot Anafi Reverse Engineering — hardbreak.wiki](https://www.hardbreak.wiki/network-analysis/protocols/application-layer/proprietary-protocols/parrot-anafi-drone-reverse-engineering)

### Tool Documentation & Resources

- [PMKID Attack — hashcat Forum (atom)](https://hashcat.net/forum/post-41646.html)
- [Bettercap PMKID Walkthrough](https://evilsocket.net:443/2019/02/13/Pwning-WiFi-networks-with-bettercap-and-the-PMKID-client-less-attack/)
- [awesome-drone-hacking (GitHub)](https://www.context-awesome.com/list/nicholasaleks/awesome-drone-hacking)
- [Damn Vulnerable Drone — Security Research Platform (GitHub)](https://github.com/bgoldmann/Drone-hijack)
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
- [PX4 Autopilot RCE — VPNCentral](https://vpncentral.com/critical-px4-autopilot-flaw-could-let-attackers-send-shell-commands-to-drones/)
- [Consumer Drone Security Testing — Which? (2021)](https://www.which.co.uk/news/2021/12/which-lab-tests-uncover-security-issues-with-toy-drones)
- [Spectra Coexistence Attack — HotHardware](https://hothardware.com/news/spectra-security-exploit-side-channel-attack-wi-fi-bluetooth)
- [Coexistence Attacks on WiFi/BT — The Hacker News](https://thehackernews.com/2021/12/researchers-uncover-new-coexistence.html)
- [KTH Thesis: Parrot Anafi Penetration Testing](https://kth.diva-portal.org/smash/get/diva2:1484619/FULLTEXT01.pdf)
- [KTH: DoS Attacks Against Parrot Anafi](https://kth.diva-portal.org/smash/get/diva2:1601435/FULLTEXT01.pdf)
- [IoActive: Drone Security and Fault Injection](https://www.ioactive.com/wp-content/uploads/2025/05/DroneSec-GGonzalez.pdf)
