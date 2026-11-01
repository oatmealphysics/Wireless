# Tier 2.1: DJI DroneID + Enhanced WiFi + Bluetooth — Attack Surface Analysis

**Date:** 2026-10-31
**Status:** Research complete — DroneID receiver tools exist (open source), Enhanced WiFi hijack demonstrated, new 2026 Bluetooth CVEs affect 16 models
**Context:** Authorized security research

---

## Executive Summary

DJI drones present **three distinct, independently exploitable attack surfaces**: (1) **DroneID** broadcasts unencrypted telemetry including pilot GPS location, receivable by anyone with an SDR; (2) **Enhanced WiFi** control links use WEP encryption (crackable in seconds) and accept injected DUML commands, enabling full drone hijack demonstrated on a DJI Mini SE with ~$345 of equipment; (3) **Bluetooth DUML interface** (CVE-2026-78306, CVSS 8.5) exposes unauthenticated command injection across **16 DJI models** including Mini, Air, Mavic, and Avata series — enabling WiFi credential overwrite, flight control access, and denial of service from Bluetooth range. Additional 2026 CVEs expose hardcoded FTP credentials (CVE-2026-78251, CVSS 9.3) and unauthenticated media access (CVE-2026-78255, CVSS 8.7).

---

## 1. Attack Surface Overview

### 1.1 Three Independent Attack Vectors

```
                           DJI Drone
                          ┌─────────┐
    Bluetooth (BLE)  ───▶ │  DUML   │ ◀─── Enhanced WiFi (2.4/5.8 GHz)
    CVE-2026-78306        │ Command │      arXiv 2309.05913
    16 models, no auth    │  Layer  │      WEP → crack → hijack
                          └────┬────┘
                               │
                       DroneID Broadcast
                       (unencrypted OFDM)
                               │
                               ▼
                    Pilot GPS + drone telemetry
                    receivable by any SDR
```

### 1.2 DUML Protocol (Central Command Layer)

DUML (DJI Universal Markup Language) is DJI's internal command protocol, carried over multiple transports:

| Transport | Authentication | Range | CVE |
|-----------|---------------|-------|-----|
| Bluetooth | **None** | ~30m | CVE-2026-78306 |
| Enhanced WiFi | WEP (crackable) | ~100m+ | arXiv 2309.05913 |
| USB (RNDIS) | Hardcoded FTP creds | Physical | CVE-2026-78251 |
| OcuSync link | Proprietary | km range | Not yet broken |

DUML packet format (from Enhanced WiFi research):
```
[0x55][Length][Version][CRC8][Sender][Receiver][SeqNum][CmdType][CmdSet][CmdID][Payload][CRC16-Kermit]
```

---

## 2. DroneID — Passive Telemetry Interception

### 2.1 Protocol Details (NDSS 2023)

DJI DroneID is a proprietary Remote ID broadcast, transmitted continuously by all modern DJI drones. It is **not** standard WiFi or Bluetooth Remote ID — it uses a dedicated OFDM waveform.

| Property | Detail |
|----------|--------|
| **Frequency** | 2.4 GHz band (multiple channels: 2.3995–2.4595 GHz), some models also 5.8 GHz |
| **Modulation** | OFDM with QPSK subcarriers, LTE-like structure |
| **Bandwidth** | ~10 MHz occupied, 15.36 MHz actual |
| **Encryption** | **None** — all data transmitted in cleartext |
| **Authentication** | None |
| **Transmission** | Continuous during flight (cannot be disabled by pilot) |

### 2.2 Exposed Data Fields

Every DroneID packet broadcasts:

| Field | Privacy Impact |
|-------|---------------|
| `serial_number` | Unique drone identifier |
| `device_type` | Drone model identification |
| `uuid` | Additional unique ID |
| `latitude`, `longitude`, `altitude` | **Drone GPS position** |
| `v_north`, `v_east`, `v_up` | Drone velocity vector |
| `latitude_home`, `longitude_home` | **Home point (takeoff location)** |
| `app_lat`, `app_lon` | **Pilot/operator GPS position** (from phone) |
| `gps_time` | Timestamp |
| `sequence_number` | Packet sequence |

**Critical finding:** The pilot's smartphone GPS coordinates are broadcast in every packet. When the drone has no GPS lock, only the phone location is transmitted — meaning the pilot's location is always exposed regardless of drone GPS status.

### 2.3 Receiving DroneID

Two open-source receivers exist:

**RUB-SysSec/DroneSecurity** (NDSS 2023 authors):
- Python-based, uses Ettus USRP B205-mini (~$1500)
- Offline mode: `droneid_receiver_offline.py -i <capture_file>`
- Live mode: `droneid_receiver_live.py` (hops through frequency list)
- Pipeline: Zadoff-Chu detection → time/freq correction → OFDM symbol extraction → QPSK demod (brute-force orientation) → descramble → turbo decode → CRC check
- Sample data included (Mini 2, Mavic Air 2)
- 14/34 packets decoded with correct CRC in sample file
- AGPL-3.0 licensed

**proto17/dji_droneid** (community):
- GNU Radio based, incomplete but actively developed
- Wiki documents signal characteristics and capture methodology
- Supports 2.4 GHz band analysis
- Uses gr-osmosdr for SDR interface (compatible with HackRF, RTL-SDR, etc.)

### 2.4 DroneID Attack Scenarios

1. **Pilot geolocation** — Track the operator's physical location in real time from any position within SDR receive range (potentially km with directional antenna)
2. **Drone tracking** — Full position + velocity telemetry of the drone
3. **Identification** — Serial number and device type for targeting specific drones
4. **Counter-UAS** — Use DroneID data to aim directed countermeasures (jamming, capture)
5. **Intelligence** — Pattern-of-life analysis from repeated observation of serial numbers

---

## 3. Enhanced WiFi Hijack (arXiv 2309.05913)

### 3.1 Attack Overview

Researchers at Ruhr University Bochum demonstrated complete hijack of a DJI Mini SE by reverse-engineering the Enhanced WiFi protocol and injecting forged control packets.

| Property | Detail |
|----------|--------|
| **Target** | DJI Mini SE (firmware 01.02.0000) |
| **Cost** | ~$345 (HackRF $300 + MikroTik RBLDF-5nD $45) |
| **Range** | WiFi range (~100m+, more with directional antenna) |
| **Complexity** | Medium (requires WEP cracking + packet injection) |
| **Result** | Full flight control: arm, disarm, movement, rotation |

### 3.2 Enhanced WiFi Protocol

| Property | Detail |
|----------|--------|
| **Frequencies** | 2.4 GHz and 5.8 GHz (tested on channel 149 / 5.745 GHz) |
| **Channel width** | Unusual 5 MHz (not standard 20/40 MHz) |
| **Encryption** | **WEP with static key** (crackable in seconds with PTW attack) |
| **Link setup** | Drone broadcasts beacon (hidden SSID) → controller sends ARP + UDP → drone replies with connection initiator |
| **Command protocol** | DUML over UDP, 0x3C-byte control packets with 0x55 delimiter |
| **Authentication** | WEP only — no application-layer auth |

### 3.3 Attack Chain

```
1. Spectrum scan → locate Enhanced WiFi beacon (5 MHz channel)
2. Monitor mode capture → collect WEP IVs
3. aircrack-ng PTW attack → crack static WEP key (seconds)
4. Decrypt traffic → identify DUML control packet structure
5. Map commands → correlate bit patterns to drone movements
6. Inject forged DUML packets via WiFi → full drone control
```

**Key finding:** The drone accepts injected commands whether or not the legitimate controller is connected. No authentication beyond WEP exists.

### 3.4 Verified Commands

| Command | Verified |
|---------|----------|
| Idle | ✓ |
| Propeller on | ✓ |
| Rotation (yaw) | ✓ |
| Up/Down | ✓ |
| Forward/Backward | ✓ |
| Left/Right | ✓ |

### 3.5 Hardware Requirements

| Item | Cost | Purpose |
|------|------|---------|
| HackRF One | ~$300 | Initial spectrum analysis, capture |
| MikroTik RBLDF-5nD (LDF 5) | ~$45 | Monitor mode + packet injection (better than HackRF) |
| **OR** any monitor-mode WiFi adapter | ~$30–100 | Alternative to MikroTik |
| Software: Scapy, aircrack-ng, gr-ieee802-11 | Free | Packet crafting, WEP cracking, 802.11 processing |

### 3.6 Affected Models

Only the Mini SE was tested, but the Enhanced WiFi protocol is used by:
- DJI Mini series (Mini, Mini SE, Mini 2)
- DJI Spark
- DJI Mavic Mini
- Potentially other budget DJI drones without OcuSync

OcuSync-based drones (Mini 3 Pro, Mavic 3, Air 2S, etc.) use a different, more complex protocol that has **not** been broken.

---

## 4. Bluetooth DUML Injection (CVE-2026-78306)

### 4.1 Vulnerability Details

| Property | Detail |
|----------|--------|
| **CVE** | CVE-2026-78306 |
| **CWE** | CWE-306: Missing Authentication for Critical Function |
| **CVSS 4.0** | **8.5 (High)** |
| **Vector** | `AV:A/AC:L/AT:N/PR:N/UI:N/VC:N/VI:H/VA:H/SC:N/SI:H/SA:H` |
| **Published** | 2026-08-24 (CIRCL) |
| **Patch** | None available as of 2026-10-31 |

### 4.2 Affected Models (16 DJI Drones)

| Model | Vulnerable FW Version |
|-------|----------------------|
| DJI Neo | ≤ 01.00.0400 |
| DJI Neo 2 | ≤ 01.00.0500 |
| DJI Flip | ≤ 01.00.1200 |
| DJI Air 3 | ≤ 01.00.1600 |
| DJI Air 3S | ≤ 01.00.1400 |
| DJI Avata 2 | ≤ 01.00.0400 |
| DJI Avata 360 | ≤ 01.00.0300 |
| DJI Mavic 3 | ≤ 01.00.1400 |
| DJI Mavic 3 Classic | ≤ 01.00.0800 |
| DJI Mavic 3 Pro | ≤ 01.01.0700 |
| DJI Mavic 4 Pro | ≤ 01.00.0500 |
| DJI Mini 2 | ≤ 01.07.0200 |
| DJI Mini 3 | ≤ 01.00.0500 |
| DJI Mini 3 Pro | ≤ 01.00.0900 |
| DJI Mini 4 Pro | ≤ 01.00.1100 |
| DJI Mini 5 Pro | ≤ 01.00.0600 |

### 4.3 Attack Capabilities

From Bluetooth range (~30m), an unauthenticated attacker can:

1. **Overwrite WiFi PSK** — Set the drone's WiFi password to a known value, then join the internal WiFi network
2. **Access flight control interface** — Once on the internal WiFi, issue flight commands via DUML
3. **Change WiFi settings** — Modify SSID, MAC address, regulatory country code, channel
4. **Denial of service** — Disable/restart WiFi and Bluetooth, disconnect all WiFi clients, reset wireless configuration
5. **Interrupt mid-flight control** — Loss of control link, video, and telemetry during flight

### 4.4 Attack Chain

```
1. BLE scan → identify DJI drone (advertisement pattern)
2. Connect via Bluetooth (no pairing/auth required)
3. Send DUML commands to overwrite WiFi PSK
4. Connect to drone's WiFi with known PSK
5. Full DUML command access over WiFi → flight control
```

### 4.5 PoC Reference

Public PoC: GitHub repository `Wh02m1/CVE-2026-78306`

---

## 5. Additional DJI CVEs (2026)

### 5.1 CVE-2026-78251: Hardcoded FTP Credentials (CVSS 9.3)

| Property | Detail |
|----------|--------|
| **CWE** | CWE-798: Use of Hard-coded Credentials |
| **Impact** | Storage exhaustion, firmware update interference |
| **Access** | Internal WiFi network or USB (RNDIS) |
| **Persistence** | Uploaded files survive reboots and factory resets |
| **Models** | Neo, Mavic, Air, Avata, Mini series |

Attack: Authenticate to FTP with shared default credentials → upload files without limit to upgrade directory → fill storage → prevent flight recording and firmware updates.

### 5.2 CVE-2026-78255: Unauthenticated Media Server (CVSS 8.7)

| Property | Detail |
|----------|--------|
| **CWE** | CWE-306: Missing Authentication |
| **Impact** | Photos/videos exfiltrated (location data, people) |
| **Access** | Internal WiFi network |
| **Endpoint** | HTTP `/v2` serves stored media without auth |
| **Models** | 16+ models (same as CVE-2026-78306) |

Attack: Join drone WiFi → enumerate predictable filenames on `/v2` → download all stored photos and videos.

### 5.3 CVE-2026-1743: Authentication Bypass

| Property | Detail |
|----------|--------|
| **Impact** | Authentication bypass on DJI platform |
| **Details** | Limited public information as of analysis date |

### 5.4 CVE-2026-77812: Information Disclosure

| Property | Detail |
|----------|--------|
| **Impact** | Information disclosure from DJI products |
| **Details** | Limited public information as of analysis date |

---

## 6. DJI Goggle Exploitation (fpv.wtf / WTFOS)

### 6.1 Margerine Root Exploit

The fpv.wtf community has published root exploits for DJI FPV goggles:

| Property | Detail |
|----------|--------|
| **Tool** | fpv-wtf/margerine (GitHub) |
| **Target** | DJI FPV Goggles V1, V2, DJI FPV Air Unit |
| **Method** | Exploit chain achieving root shell |
| **Post-exploit** | WTFOS custom firmware platform |

WTFOS enables:
- Full Betaflight OSD overlay in digital video
- Custom DVR recording settings
- Root filesystem modifications
- Additional hardware capabilities unlocked

### 6.2 Security Implications

Rooted DJI goggles can:
- Intercept and record all video feeds
- Modify OSD overlays (inject false telemetry)
- Potentially act as rogue ground station
- Extract encryption keys and firmware secrets

---

## 7. Offline Toolkit Architecture

### 7.1 What's Buildable Without Hardware

```
dji-toolkit/
├── droneid_analyzer.py      # DroneID packet parser and analyzer
│   - Parse decoded DroneID packets (from RUB-SysSec tools)
│   - GPS coordinate extraction and mapping
│   - Serial number / UUID tracking
│   - Flight path reconstruction
│   - Pilot location tracking
│
├── duml_protocol.py         # DUML command protocol library
│   - Packet builder/parser (0x55 delimiter, CRC-Kermit)
│   - Command set definitions (WiFi, BT, flight control)
│   - Sender/receiver ID management
│   - Sequence number tracking
│
├── enhanced_wifi_analyzer.py # Enhanced WiFi capture analysis
│   - 5 MHz channel detection
│   - WEP key extraction helper
│   - DUML-over-UDP packet parser
│   - Control command mapping
│
└── cve_scanner.py           # DJI vulnerability scanner
    - Bluetooth DUML probe (CVE-2026-78306)
    - WiFi service enumeration
    - FTP default credential test (CVE-2026-78251)
    - Media server access test (CVE-2026-78255)
    - Model/firmware fingerprinting
```

### 7.2 What Requires Hardware

| Component | Hardware Needed | Cost |
|-----------|----------------|------|
| DroneID reception | SDR (USRP ~$1500 or HackRF ~$300) | $300–1500 |
| Enhanced WiFi hijack | Monitor-mode WiFi adapter (~$45) | $45 |
| Bluetooth DUML injection | BLE adapter (built into most laptops) | $0–15 |
| FTP/media exploit | WiFi connectivity to drone | $0 |

---

## 8. Comparison to Current ELRS/Betaflight Target

| Dimension | ELRS/Betaflight | DJI Ecosystem |
|-----------|----------------|---------------|
| **Protocol** | ELRS (SX1280) + CRSF + MSP | Enhanced WiFi + DUML + OcuSync + DroneID |
| **Authentication** | Binding phrase (MD5) | WEP (crackable) / None (BT) / OcuSync (proprietary) |
| **Known CVEs** | NCC Group findings (unfixed) | CVE-2026-78306, -78255, -78251, -1743, -77812 |
| **Attack complexity** | RF protocol knowledge | WiFi tools (aircrack-ng) / BLE scan |
| **Target population** | FPV racing (growing) | **Massive** — DJI holds ~70% consumer drone market |
| **Pilot location exposure** | None | DroneID broadcasts pilot GPS |
| **Hijack demonstrated** | Not yet (theoretical) | **Yes** — Mini SE full control (arXiv 2309.05913) |
| **Models affected by BT CVE** | N/A | 16 models (Mini, Air, Mavic, Avata, Neo, Flip) |
| **Goggle exploitation** | N/A | Root exploit + WTFOS community firmware |

---

## 9. Risk Assessment

| Factor | Rating | Notes |
|--------|--------|-------|
| **DroneID interception** | **VERY HIGH** | No mitigation possible — always broadcasts |
| **Enhanced WiFi hijack** | **HIGH** | WEP is trivially crackable; full control demonstrated |
| **Bluetooth DUML** | **CRITICAL** | 16 models, no auth, no patch, BLE range |
| **Hardcoded FTP** | **HIGH** | Persistent DoS, survives factory reset |
| **Media exfiltration** | **HIGH** | Privacy violation, no auth needed |
| **Target population** | **MASSIVE** | DJI dominates consumer/prosumer drone market |
| **Transferability** | **MEDIUM** | DUML is DJI-specific; DroneID affects all DJI |
| **Remediation** | **POOR** | No patches for 2026 CVEs as of analysis date |

---

## 10. Priority Next Steps

1. **DUML protocol library** (offline, no hardware) — Build packet builder/parser from published research and PoC code
2. **DroneID packet analyzer** (offline) — Parse decoded packets from RUB-SysSec sample data, build flight path reconstruction
3. **CVE-2026-78306 BLE probe** (needs BLE adapter) — Replicate unauthenticated DUML-over-Bluetooth connection
4. **Enhanced WiFi analysis** (needs monitor-mode adapter + DJI Mini SE) — Reproduce WEP crack and control injection
5. **DroneID live reception** (needs SDR) — Set up live receiver with USRP or HackRF

---

## Sources

- "Drone Security and the Mysterious Case of DJI's DroneID" (NDSS 2023) — RUB-SysSec
- "Behind The Wings: Reverse Engineering and Drone Hijacking in DJI Enhanced Wi-Fi Protocol" (arXiv 2309.05913)
- RUB-SysSec/DroneSecurity (GitHub) — DroneID receiver implementation
- proto17/dji_droneid (GitHub) — GNU Radio DroneID decoder (incomplete)
- CVE-2026-78306: DJI Bluetooth DUML (CVSS 8.5, CIRCL)
- CVE-2026-78255: DJI Unauthenticated Media Server (CVSS 8.7, CIRCL)
- CVE-2026-78251: DJI Hardcoded FTP Credentials (CVSS 9.3, CIRCL)
- CVE-2026-1743: DJI Authentication Bypass
- CVE-2026-77812: DJI Information Disclosure
- Wh02m1/CVE-2026-78306 (GitHub) — Bluetooth DUML PoC
- ibndias/dji-drone-hijacking (GitHub) — Enhanced WiFi hijack PoC
- fpv-wtf/margerine (GitHub) — DJI goggle root exploit
- fpv-wtf/wtfos (GitHub) — Custom firmware for DJI goggles
