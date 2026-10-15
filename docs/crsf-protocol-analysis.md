# TBS Crossfire (CRSF) Protocol — Attack Surface Analysis

> **Purpose:** CTF competition reference for attacking TBS Crossfire RC links.
> Covers serial protocol, over-the-air RF link, binding, encryption gaps, and practical attack vectors.
> **Last updated:** 2026-10-15

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Protocol Overview](#protocol-overview)
  - [What Is CRSF](#what-is-crsf)
  - [Serial vs Over-the-Air](#serial-vs-over-the-air)
- [Serial Protocol Specification](#serial-protocol-specification)
  - [Frame Format](#frame-format)
  - [CRC Algorithm](#crc-algorithm)
  - [Device Addresses](#device-addresses)
  - [Channel Encoding](#channel-encoding)
  - [Frame Types Reference](#frame-types-reference)
  - [Command Frames](#command-frames)
- [Over-the-Air RF Protocol](#over-the-air-rf-protocol)
  - [Frequency Bands and Channels](#frequency-bands-and-channels)
  - [Modulation](#modulation)
  - [Frequency Hopping](#frequency-hopping)
  - [OTA Packet Structure](#ota-packet-structure)
  - [OTA CRC](#ota-crc)
  - [Timing](#timing)
- [Binding and Authentication](#binding-and-authentication)
  - [Binding Process](#binding-process)
  - [Model Match](#model-match)
  - [Multi-Bind and TBS Cloud](#multi-bind-and-tbs-cloud)
- [Encryption and Security Analysis](#encryption-and-security-analysis)
  - [Encryption Feature](#encryption-feature)
  - [What Is NOT Protected](#what-is-not-protected)
  - [CRC as Security — It Isn't](#crc-as-security--it-isnt)
- [Known Vulnerabilities](#known-vulnerabilities)
  - [WiFi Module Attack Surface](#wifi-module-attack-surface)
  - [Firmware Update Attack Surface](#firmware-update-attack-surface)
  - [Serial Bus Injection](#serial-bus-injection)
  - [Published CVEs](#published-cves)
- [Attack Vectors](#attack-vectors)
  - [1. RF Sniffing and Reconnaissance](#1-rf-sniffing-and-reconnaissance)
  - [2. Frequency Hop Recovery](#2-frequency-hop-recovery)
  - [3. Replay Attack](#3-replay-attack)
  - [4. Packet Injection / TX Impersonation](#4-packet-injection--tx-impersonation)
  - [5. Jamming and Denial of Service](#5-jamming-and-denial-of-service)
  - [6. WiFi Module Exploitation](#6-wifi-module-exploitation)
  - [7. Firmware OTA Hijack](#7-firmware-ota-hijack)
  - [8. Serial CRSF Injection](#8-serial-crsf-injection)
- [CRSF vs ELRS — Attacker's Comparison](#crsf-vs-elrs--attackers-comparison)
- [Detection Signatures](#detection-signatures)
  - [RF Fingerprinting](#rf-fingerprinting)
  - [SDR-Based Identification](#sdr-based-identification)
- [Lab Equipment and Tools](#lab-equipment-and-tools)
- [References](#references)

---

## Executive Summary

TBS Crossfire is a long-range RC link operating at 868/915 MHz using LoRa-derived modulation on Semtech SX1272/SX1276 chips. It is widely used in FPV racing drones as an alternative to ExpressLRS.

**Bottom line for CTF:** Crossfire has slightly better security properties than ELRS — it offers an optional encryption mode and uses a proprietary (closed-source) OTA protocol — but the default configuration has **no encryption, no authentication, and a recoverable frequency hopping sequence**. The binding process ties a receiver to a TX serial number, but the OTA protocol relies on a "salted CRC and some hopping" that an attacker can reconstruct from passive observation. An attacker with an SDR and patience can sniff the hop pattern, reconstruct CRC seeds, and forge packets to commandeer the link.

The WiFi module on the TX is a secondary attack surface — it exposes an AP with a web interface, potentially with weak or no default credentials.

---

## Protocol Overview

### What Is CRSF

CRSF (Crossfire) is two things:

1. **A serial protocol** — a half-duplex or full-duplex UART bus connecting the radio handset, TX module, RX module, flight controller, and peripherals (GPS, OSD, VTX). This is the wired protocol. It is **open-source** and documented by TBS at [tbs-fpv/tbs-crsf-spec](https://github.com/tbs-fpv/tbs-crsf-spec).

2. **An over-the-air RF protocol** — the wireless link between TX and RX. This is **proprietary and closed-source**. Reverse engineering by g3gg0 and others has partially documented it.

ExpressLRS adopted the serial CRSF frame format for its wired side but uses a completely different OTA protocol. When this document says "CRSF OTA," it means the TBS Crossfire air link, not the ELRS air link.

### Serial vs Over-the-Air

| Property | Serial CRSF | OTA Crossfire |
|---|---|---|
| Specification | Open (GitHub) | Closed / proprietary |
| CRC | CRC-8 poly 0xD5 | CRC-16 poly 0x1021 (uplink), CRC-8 poly 0x07 (downlink) |
| Stick resolution | 11-bit (0–1984) | 10-bit |
| Channel count | 16 | 8 or 12 |
| Encryption | None | Optional ("military-strength" — unspecified cipher) |
| Max frame | 64 bytes | 23 bytes (uplink), 13 bytes (downlink) |

---

## Serial Protocol Specification

### Frame Format

```
┌──────┬────────┬──────┬─────────┬─────┐
│ Sync │ Length │ Type │ Payload │ CRC │
│ 1B   │ 1B     │ 1B   │ 0–58B   │ 1B  │
└──────┴────────┴──────┴─────────┴─────┘
```

- **Sync byte:** `0xC8` (serial), `0x00` (broadcast), or a device address
- **Length:** Bytes remaining after sync and length fields (= total - 2). Valid range: 2–62
- **Type:** Frame type ID (see table below)
- **Payload:** Type-specific. For extended frames (type ≥ 0x28), payload starts with destination address + origin address
- **CRC:** CRC-8 over type + payload (not sync or length)
- **Max frame size:** 64 bytes total
- **Byte order:** Big-endian

### CRC Algorithm

**Frame CRC (all frames):**
- CRC-8, polynomial `0xD5` (x^7 + x^6 + x^4 + x^2 + x^0)
- Initial value: `0x00`
- No input/output reflection
- Covers: type byte + payload (excludes sync, length, and the CRC byte itself)

**Command CRC (type 0x32 only):**
- CRC-8, polynomial `0xBA` (x^7 + x^5 + x^4 + x^3 + x^1)
- Appended inside the command payload before the outer frame CRC
- Covers: type (0x32), destination, origin, command ID, and command payload

**Python implementation:**

```python
CRC8_TABLE_D5 = []
for i in range(256):
    crc = i
    for _ in range(8):
        crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    CRC8_TABLE_D5.append(crc)

def crsf_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC8_TABLE_D5[crc ^ b]
    return crc
```

### Device Addresses

| Address | Device | CTF Relevance |
|---|---|---|
| `0x00` | Broadcast | Target for injection — all devices process |
| `0x10` | USB device | Agent desktop connection |
| `0x12` | Bluetooth/WiFi module | WiFi attack surface |
| `0x80` | OSD / TBS Core PNP Pro | Telemetry display |
| `0xC0` | Voltage/current sensor | — |
| `0xC2` | GPS | GPS data exfiltration |
| `0xC4` | TBS Blackbox | Flight logs |
| `0xC8` | Flight controller | **Primary target** — receives RC channels |
| `0xCC` | Race tag | Race timing |
| `0xCE` | VTX | Video transmitter control |
| `0xEA` | Radio transmitter (handset) | Source of RC input |
| `0xEC` | RC receiver (Crossfire RX) | **On the drone** |
| `0xEE` | RC transmitter module (Crossfire TX) | RF link endpoint |

### Channel Encoding

Frame type `0x16` — RC Channels Packed:

- 16 channels × 11 bits = 22 bytes payload
- Center (1500 µs) = raw value 992
- Conversion: `µs = (raw - 992) * 5/8 + 1500`
- Reverse: `raw = (µs - 1500) * 8/5 + 992`
- Range: 172 (988 µs) to 1811 (2012 µs)

**Stick channel mapping (typical):**

| Channel | Function | Arm-critical |
|---|---|---|
| 0 | Roll | No |
| 1 | Pitch | No |
| 2 | Throttle | **Yes** |
| 3 | Yaw | No |
| 4 | Arm switch | **Yes** — toggling disarms |
| 5–15 | AUX 1–11 | Mode switches, buzzer, etc. |

### Frame Types Reference

**Broadcast types (< 0x28) — no extended header:**

| Type | Name | Payload Size | Content |
|---|---|---|---|
| `0x02` | GPS | 15 B | lat/lon (int32 × 10^7), speed, heading, alt, sats |
| `0x03` | GPS Time | 9 B | Y/M/D/H/M/S/ms |
| `0x07` | Variometer | 2 B | Vertical speed (cm/s) |
| `0x08` | Battery Sensor | 8 B | Voltage, current, capacity, remaining % |
| `0x09` | Baro Altitude | 3 B | Altitude + vario |
| `0x0B` | Heartbeat | 2 B | Origin address |
| `0x14` | Link Statistics | 10 B | RSSI, LQ, SNR, antenna, RF mode, power |
| `0x16` | RC Channels Packed | 22 B | 16 × 11-bit channels |
| `0x1C` | Link Stats RX | — | RSSI, LQ, SNR from RX side |
| `0x1D` | Link Stats TX | — | Same + FPS from TX side |
| `0x1E` | Attitude | 6 B | Pitch, roll, yaw (100 µrad) |
| `0x21` | Flight Mode | Variable | Null-terminated string |

**Extended types (≥ 0x28) — include dest + origin addresses:**

| Type | Name | Purpose |
|---|---|---|
| `0x28` | Device Ping | Discovery — empty payload |
| `0x29` | Device Info | Name, serial, HW/FW IDs, param count |
| `0x2B` | Parameter Entry | Settings menu item (chunked) |
| `0x2C` | Parameter Read | Request a parameter |
| `0x2D` | Parameter Write | Write a setting |
| `0x32` | Command | Direct commands (see below) |
| `0x7A` | MSP Request | Betaflight MSP over CRSF |
| `0x7B` | MSP Response | MSP reply |
| `0xAA` | MAVLink Envelope | MAVLink 1/2 frames, chunked |

### Command Frames

Command frame `0x32` carries a sub-protocol with its own CRC (poly `0xBA`). Attack-relevant commands:

| Command ID | Sub-command | Action | CTF Use |
|---|---|---|---|
| `0x01` | FC commands | **Force disarm** | Instant drone kill via serial injection |
| `0x01` | FC commands | Scale channel | Override a specific channel value |
| `0x05` | OSD | Send button states | Manipulate OSD navigation |
| `0x08` | VTX | Set frequency | Move video to attacker's frequency |
| `0x08` | VTX | Set power / dynamic power | Reduce VTX power → lose video |
| `0x08` | VTX | Pit mode | Kill video output |
| `0x10` | Crossfire | **Bind / cancel bind** | Trigger rebind → disrupt link |
| `0x10` | Crossfire | Model select | Switch to wrong model config |
| `0x0A.0x70` | General | Speed proposal | Negotiate baud rate |

**Force disarm (0x32.0x01) is accepted based only on address and CRC. There is no authentication.** Any device on the serial bus that can send valid CRSF frames can disarm the drone.

---

## Over-the-Air RF Protocol

This section is based on reverse engineering by [g3gg0](https://www.g3gg0.de/wordpress/fpv/fpv-analysis-of-tbs-crossfire/) and other community research. The OTA protocol is proprietary and not documented by TBS.

### Frequency Bands and Channels

| Region | Range (MHz) | Channels | Spacing | Notes |
|---|---|---|---|---|
| 868 | 860.165 – 885.905 | 50 up + 50 down | 260 kHz | EU region |
| 915 FCC | 902.165 – 927.905 | 50 up + 50 down | 260 kHz | US region |
| 868 RACE | 860.165 – 885.905 | 100 shared | 260 kHz | Up/down share channels |
| 915 RACE | 902.165 – 927.905 | 100 shared | 260 kHz | Up/down share channels |
| 868 CE | 863.093 – 868.581 | 50 | 112 kHz | Narrow band, LBT |
| 915 AU | 915.165 – 927.905 | 50 | 260 kHz | Australia |

**Non-race mode:** Uplink uses channels 0–49, downlink uses channels 50–99 (offset by exactly 50 channels). This separation means the uplink and downlink never share a frequency.

**Race mode:** Uplink and downlink share all 100 channels.

**Hardware variation:** The base frequency can vary by ~15 kHz between different TX/RX hardware units. An SDR sniffer may need frequency offset calibration.

### Modulation

- **150 Hz mode:** FSK (not LoRa), 42.48 kHz frequency shift, 85.1 kBaud bitrate
- **50 Hz mode:** LoRa modulation (spreading factor, bandwidth, coding rate not publicly documented for this mode)
- **Binding and firmware OTA:** LoRa mode with CRC-8, init value 0
- **Chip:** Semtech SX1272 (receivers), SX1276 (some modules). Both support LoRa and FSK.

The 150 Hz mode uses FSK rather than LoRa, which is significant: FSK is much easier to receive and decode with an SDR than LoRa. Standard GNU Radio blocks can demodulate FSK directly.

### Frequency Hopping

- Hop sequence is **deterministic** and appears to depend on the bind key
- Each TX/RX pair follows its own hop sequence
- The sequence repeats after 150 hops before restarting
- In non-race mode, the downlink channel is always the uplink channel + 50
- The hop sequence can be recovered by passive RF observation (listening and timing)

**Attack implication:** Once the hop sequence is captured (requires ~1 second of observation at 150 Hz), an attacker knows every future frequency and can follow the link or selectively jam specific hops.

### OTA Packet Structure

| Direction | Size | Stick Resolution | CRC |
|---|---|---|---|
| Uplink (TX → RX) | 23 bytes | 10-bit | CRC-16 |
| Downlink (RX → TX) | 13 bytes | — | CRC-8 |

- **8-channel mode:** Sticks 0–7 sent in every packet
- **12-channel mode:** Sticks 0–3 in every packet; sticks 4–7 and 8–11 alternate. Bit 5 of the first uplink byte selects the alternating half.

The payload appears to be encoded or obfuscated. g3gg0 describes it as "obviously coded" but did not fully decode the encoding scheme. It does not appear to be strong encryption — likely XOR or simple substitution.

### OTA CRC

| CRC | Polynomial | Reflection | XOR-out | Used for |
|---|---|---|---|---|
| CRC-8 | `0x07` | None | `0x00` | Downlink, binding, firmware |
| CRC-16 | `0x1021` (CCITT) | Input + output reflected | `0x0000` | Uplink |

**The CRC init value changes with the hop number.** Identical payloads produce different CRCs on different hops. This serves as a weak form of sequence authentication — but since the hop number is observable, the init value can be derived.

### Timing

- **150 Hz mode:** One packet every 6.666 ms
- **50 Hz mode:** One packet every 20 ms
- **Downlink:** Arrives ≤ 2.6 ms after the uplink packet completes (smaller packet, shorter transmission)
- **Failsafe:** If no packets received for ~1 second, the RX enters failsafe

---

## Binding and Authentication

### Binding Process

1. Put the RX into bind mode (press bind button within 1 minute of power-on)
2. Put the TX into bind mode (via Lua script, OLED menu, or button)
3. The RX stores the TX's serial number
4. One TX can bind to many receivers
5. A firmware update on the TX may require rebinding

**No shared secret is exchanged during binding** (unlike ELRS, where the binding phrase generates a UID). The RX learns the TX serial number, which seeds the hop sequence and CRC initialization. The serial number is not a secret — it's transmitted during binding and may be recoverable from the OTA link.

### Model Match

- TX sends a model ID; the RX checks it against its stored value
- Model ID `0x00` is a wildcard (matches anything)
- If model IDs mismatch (and neither is 0x00), the RX does not respond
- Model match prevents accidental connection to the wrong drone but is not a security feature

### Multi-Bind and TBS Cloud

- Since FW 3.71, TBS supports "multi-bind" where multiple TXs share a user ID from the TBS Cloud
- Requires the WiFi module and a TBS account
- The user ID serves as the binding identity
- **Attack implication:** If the TBS Cloud user ID is compromised, all bound receivers are compromised

---

## Encryption and Security Analysis

### Encryption Feature

TBS Crossfire offers an optional encryption mode described as "military-strength" in the manual.

**What is known:**
- Encryption is a menu option on the TX
- Enabling it makes binding exclusive to one receiver
- Multi-bind is disabled when encryption is on
- The cipher, key length, key exchange, and standard are **not documented**

**What is not known:**
- Whether this encrypts the OTA payload, the CRC, or both
- Key derivation and rotation
- Whether it provides authentication or only confidentiality
- Whether replay protection is included

**Assessment:** The encryption feature exists but is:
1. **Off by default** — most Crossfire users fly unencrypted
2. **Undocumented** — no public audit, no named cipher, no key management details
3. **Proprietary** — security through obscurity
4. **Rare in practice** — FPV pilots disable it because it restricts multi-bind and adds latency

**For CTF:** Assume the target is running with encryption **off** unless proven otherwise. If encryption is on, the attack surface shifts to the WiFi module and serial bus.

### What Is NOT Protected

Even with encryption enabled, the following remain unprotected:

| Component | Protection | Risk |
|---|---|---|
| Serial CRSF bus | **None** | Any device on the bus can send any command |
| WiFi module AP | Password optional | Network-based attacks on TX |
| Firmware update OTA | Encrypted (proprietary) | Firmware is encrypted but integrity checking is unclear |
| Telemetry downlink | Unknown | GPS, battery, attitude data may leak |
| Command frames (0x32) | CRC only | Force disarm, VTX control, bind commands accepted by CRC alone |
| Link statistics | Broadcast | RSSI, LQ, SNR visible to all |

### CRC as Security — It Isn't

The CRSF serial protocol uses CRC-8 (poly 0xD5) for error detection. The OTA protocol uses CRC-16 (poly 0x1021) for uplink and CRC-8 (poly 0x07) for downlink.

CRCs are **not cryptographic**:
- CRC values can be computed by anyone who knows the polynomial
- The hop-dependent init value is observable, not secret
- CRCs detect accidental bit errors, not deliberate tampering
- An attacker who knows the polynomial and init value can forge packets with valid CRCs

As g3gg0 summarized: "No authentication, just a salted CRC and some hopping — both of which can be calculated back."

---

## Known Vulnerabilities

### WiFi Module Attack Surface

The Crossfire TX exposes a WiFi access point:

- **SSID:** `TBS_XF_AP_xxxxxx` (predictable format, last 6 chars likely from serial)
- **Default credentials:** Not clearly documented — may be open or use a weak default password
- **Web UI:** HTTP at `192.168.4.1` — handles firmware uploads, network config, status
- **Services:** MAVLink stream over UDP/TCP (typically port 8888)
- **AP Lock / Web UI Lock:** Optional passwords, not enforced by default

**Attack scenarios:**
1. Connect to the AP → access the web UI → upload malicious firmware
2. Connect to the AP → inject MAVLink commands via UDP/TCP
3. Deauth the pilot's phone/tablet from the AP → disrupt telemetry
4. Scan for `TBS_XF_AP_*` SSIDs to enumerate Crossfire users at an event

### Firmware Update Attack Surface

- TX firmware updated via USB (AGENT desktop) or WiFi
- RX firmware updated OTA from the TX
- Firmware files are encrypted (suspected 8-byte block cipher or XOR, per g3gg0)
- A "golden" recovery image is stored on Micro/Nano RX (FW 2.24+) — can be triggered by button sequence
- **TANGO II** validates firmware before OTA update

**Attack scenarios:**
1. If the firmware encryption is weak (XOR), a modified firmware could be crafted
2. The OTA firmware update uses LoRa mode — could be jammed to prevent updates
3. Triggering the golden image recovery via the button sequence resets the RX to factory defaults, potentially losing model match settings

### Serial Bus Injection

If an attacker gains physical or wireless access to the CRSF serial bus (e.g., through a compromised peripheral, rogue OSD, or BLE-UART bridge):

- **Force disarm:** Command `0x32.0x01` — accepted with CRC only, no authentication
- **VTX control:** Change video frequency, reduce power, enable pit mode
- **Bind command:** Trigger a rebind, disrupting the RF link
- **Channel override:** Scale or replace stick channels
- **MSP passthrough:** Send arbitrary Betaflight MSP commands via frame type `0x7A`

### Published CVEs

No CVEs have been published specifically for TBS Crossfire's RF protocol or serial CRSF.

**Related:**
- **CVE-2023-47625** — References CRSF in the context of Betaflight's handling of serial protocols, but this is a Betaflight issue, not a Crossfire-specific vulnerability.

The absence of CVEs reflects the niche nature of RC link protocols and the hobbyist community's preference for disclosure through blog posts and forum threads rather than formal advisories. It does **not** indicate security.

---

## Attack Vectors

### 1. RF Sniffing and Reconnaissance

**Goal:** Identify Crossfire links, capture packets, map frequency usage.

**Equipment:**
- Ettus B205mini or B210 (covers 868/915 MHz with sufficient bandwidth)
- Or: HackRF One, RTL-SDR (receive only)

**Method:**
1. Tune to the 868 or 915 MHz band
2. Look for the characteristic pattern: 150 Hz packet rate, 260 kHz channel spacing, ~23-byte uplink / ~13-byte downlink
3. FSK mode (150 Hz) is straightforward to demodulate with GNU Radio
4. LoRa mode (50 Hz) requires a LoRa-capable SDR decoder (gr-lora_sdr or SX1276 in receive mode)

**Tools:**
- `gr-lora_sdr` — GNU Radio LoRa transceiver implementation ([GitHub](https://github.com/tapparelj/gr-lora_sdr))
- SoapySDR + Ettus UHD for SDR hardware abstraction
- Custom GNU Radio flowgraph for FSK demodulation at 85.1 kBaud

**Difficulty:** Low for detection, medium for full demodulation.

### 2. Frequency Hop Recovery

**Goal:** Determine the full hop sequence of a target TX/RX pair.

**Method:**
1. Use a wideband SDR (B210 = 56 MHz instantaneous BW) to capture the entire 868 or 915 band
2. At 150 Hz, all 50 uplink channels are visited in ~150 × 6.666 ms = ~1 second
3. Detect packet energy on each channel and record the sequence
4. The downlink channel is always uplink + 50 (non-race mode)
5. After ~1 second of capture, the full 150-hop sequence is known

**Alternatively:** Use multiple narrowband receivers (RTL-SDR dongles) on different channels and triangulate the sequence.

**Difficulty:** Low with wideband SDR, medium with narrowband.

### 3. Replay Attack

**Goal:** Re-transmit captured packets to confuse the RX.

**Limitations:**
- The CRC init value changes with hop number, so a replayed packet must be sent on the correct hop
- The hop sequence advances at 150 Hz, so timing must be precise (±6.666 ms)
- A simple replay on the wrong hop will fail CRC

**Method:**
1. Capture a full hop cycle (~1 second)
2. For each captured packet, record: frequency, timestamp, raw bytes
3. Wait for the hop sequence to repeat (every 150 hops = 1 second)
4. Retransmit each packet on its original frequency at the correct time

**Difficulty:** Medium. Requires precise timing and frequency synchronization.

### 4. Packet Injection / TX Impersonation

**Goal:** Send forged RC channel packets that the RX accepts, overriding the legitimate TX.

**Method:**
1. Recover the hop sequence (see above)
2. Determine the CRC init values for each hop (by capturing packets and back-calculating)
3. Construct a forged packet with desired stick values
4. Encode the payload using the obfuscation scheme (if reversible) or replay a captured packet with modified stick bytes (if the encoding is simple XOR, flipping stick bits may work)
5. Transmit with higher power than the legitimate TX to win the RF capture

**Requirements:**
- Transmit-capable SDR (Ettus B205mini/B210) or a modified SX1276 module
- Knowledge of the OTA payload encoding (partially documented)
- Power advantage over the legitimate TX

**What g3gg0 says:** "Faking a TX to control a victim's quad is not done yet, but easily™ possible."

**Difficulty:** Medium-high. The payload encoding is the main unknown. If it's simple XOR with a known key, it's straightforward. If it's a block cipher, it's harder.

### 5. Jamming and Denial of Service

**Goal:** Prevent the RX from receiving valid packets → trigger failsafe.

**Methods:**

| Method | Equipment | Difficulty | Selectivity |
|---|---|---|---|
| **Broadband noise** | Any TX at 868/915 MHz | Trivial | None — jams all devices in the band |
| **Follow-the-hop jam** | SDR + hop sequence | Medium | Targets one link |
| **Reactive jam** | SDR with fast turnaround | High | Jams on energy detection |
| **Deauth-style** | Not applicable (no deauth frame in CRSF) | N/A | — |

**Crossfire failsafe timing:** ~1 second without valid packets → failsafe. In "cut" mode, the FC takes over (RTH if configured). In "position" mode, the RX outputs pre-taught stick values.

**Follow-the-hop is the most practical targeted attack.** Once the hop sequence is known, the attacker transmits noise or a bogus packet on each channel just before the legitimate packet arrives. The RX sees corrupted data and drops it.

### 6. WiFi Module Exploitation

**Goal:** Gain access to the TX's internal systems via WiFi.

**Prerequisites:** Be within WiFi range of the TX module (~50 m typical for small AP).

**Attack chain:**
1. Scan for `TBS_XF_AP_*` SSIDs
2. Attempt connection (check for open AP or weak password)
3. Access web UI at `192.168.4.1`
4. From the web UI:
   - Upload malicious firmware
   - Modify TX settings
   - Access MAVLink stream (port 8888)
5. Via MAVLink:
   - Send `MAV_CMD_COMPONENT_ARM_DISARM` to disarm
   - Inject waypoints
   - Change flight mode

**Difficulty:** Low if the AP is open, medium if password-protected.

### 7. Firmware OTA Hijack

**Goal:** Intercept or inject firmware during an OTA update.

**When it happens:** Both TX and RX must be in bind mode for OTA firmware update.

**Attack chain:**
1. Detect when a bind mode is initiated (LoRa mode, known frequency)
2. Jam the legitimate firmware transfer
3. Transmit a modified firmware image

**Limitations:**
- Firmware files are encrypted (suspected XOR or simple block cipher)
- The encryption has not been publicly broken
- TANGO II validates firmware before applying

**Difficulty:** High. Requires breaking the firmware encryption first.

### 8. Serial CRSF Injection

**Goal:** Send CRSF command frames on the serial bus to control the drone.

**Prerequisites:** Physical or wireless access to the UART bus.

**Access methods:**
- Rogue peripheral (GPS, OSD, BLE module) connected to an open CRSF port
- BLE-UART bridge on an exposed CRSF pad
- If the drone has an unused CRSF-capable UART, solder a transmitter to it

**Injection commands:**
```python
# Force disarm via CRSF command 0x32
import struct

def forge_disarm_frame():
    """Build a CRSF force-disarm command frame."""
    frame_type = 0x32
    dest = 0xC8       # Flight controller
    origin = 0xEE     # Pretend to be TX module
    cmd_id = 0x01     # FC command
    sub_cmd = 0x00    # Force disarm (hypothetical sub-command)

    # Command CRC (poly 0xBA)
    cmd_payload = bytes([frame_type, dest, origin, cmd_id, sub_cmd])
    cmd_crc = crsf_crc8_ba(cmd_payload)

    # Full payload: dest + origin + cmd_id + sub_cmd + cmd_crc
    payload = bytes([dest, origin, cmd_id, sub_cmd, cmd_crc])

    # Frame CRC (poly 0xD5)
    frame_data = bytes([frame_type]) + payload
    frame_crc = crsf_crc8_d5(frame_data)

    # Complete frame
    length = len(payload) + 2  # +1 type +1 CRC
    return bytes([0xC8, length, frame_type]) + payload + bytes([frame_crc])
```

**Difficulty:** Low if physical access exists. The serial bus has zero authentication.

---

## CRSF vs ELRS — Attacker's Comparison

| Property | TBS Crossfire | ExpressLRS | Advantage |
|---|---|---|---|
| **Frequency** | 868/915 MHz (sub-GHz) | 2.4 GHz (or 900 MHz) | CRSF harder to jam (lower freq, better penetration) |
| **Source code** | Closed (OTA), open (serial) | Fully open | ELRS easier to analyze |
| **OTA encryption** | Optional (undocumented cipher) | **None** | CRSF slightly better (if enabled) |
| **OTA authentication** | None (CRC only) | None (CRC only) | Tie — both weak |
| **Binding secret** | TX serial number | Binding phrase → MD5 → UID | ELRS easier to brute-force (phrase dictionary) |
| **SYNC packet leakage** | Unknown | UID[3:5] sent in plaintext | ELRS worse (leaks UID bytes) |
| **FHSS recovery** | Passive observation (~1s) | Compute from UID (instant if known) | ELRS worse if UID known |
| **Hop channels** | 50 up + 50 down (868/915 MHz) | 80 channels (2.4 GHz) | Similar complexity |
| **Failsafe time** | ~1 second | ~1 second (configurable) | Similar |
| **WiFi attack surface** | TX has WiFi AP | TX has WiFi AP (for config) | Both exposed |
| **Serial bus security** | CRC only (no auth) | CRC only (no auth) | Tie — both zero auth |
| **Stick resolution OTA** | 10-bit | 10-bit (wide switch) or 12-bit | Similar |
| **Community analysis** | g3gg0 partial RE | Fully open, well-documented | ELRS fully understood |

**Summary for CTF:**
- If the target runs **ELRS**: attack the binding phrase (rainbow table, SYNC capture → UID → full takeover)
- If the target runs **Crossfire with encryption off**: recover hop sequence via SDR, then jam or inject
- If the target runs **Crossfire with encryption on**: pivot to WiFi module or serial bus
- **Both protocols** have zero authentication on the serial CRSF bus — physical access = full control

---

## Detection Signatures

### RF Fingerprinting

How to identify TBS Crossfire in the RF spectrum:

| Feature | Value | How to detect |
|---|---|---|
| **Band** | 868 or 915 MHz | SDR waterfall — look below 1 GHz |
| **Packet rate** | 150 Hz (6.666 ms) or 50 Hz (20 ms) | Time-domain analysis |
| **Modulation** | FSK (150 Hz) or LoRa (50 Hz) | Spectral shape — FSK shows two peaks, LoRa shows chirp spread |
| **Bandwidth** | ~85 kHz (FSK) or wider (LoRa) | Channel width measurement |
| **Channel spacing** | 260 kHz (or 112 kHz for CE) | Hop pattern analysis |
| **Band occupancy** | 50 channels (non-race) or 100 (race) | Count active frequencies |
| **Uplink/downlink split** | Uplink = channels 0–49, downlink = channels 50–99 | Distinguish by packet size (23 vs 13 bytes) |

### SDR-Based Identification

**Using the Ettus B205mini/B210:**

1. Set center frequency to 893 MHz (center of 868 band) or 915 MHz
2. Set sample rate to ≥ 30 MHz to capture the full band
3. Run an energy detector looking for periodic bursts at 150 Hz or 50 Hz intervals
4. When a burst is found, measure:
   - Duration (determines FSK vs LoRa)
   - Bandwidth (confirms Crossfire vs other ISM traffic)
   - Packet size after demodulation (23 = uplink, 13 = downlink)

**Distinguishing Crossfire from other 868/915 MHz traffic:**

| Protocol | Packet rate | Modulation | Channel count |
|---|---|---|---|
| TBS Crossfire | 150 or 50 Hz | FSK / LoRa | 50–100 |
| LoRaWAN | < 1 Hz typical | LoRa | 8 (EU) or 64 (US) |
| Sigfox | ~1 per hour | DBPSK | 3 |
| Z-Wave | Event-driven | FSK | 1 |
| ELRS 900 MHz | 25–200 Hz | LoRa | 60+ |

---

## Lab Equipment and Tools

### SDR Hardware for 868/915 MHz

| Device | Role | Notes |
|---|---|---|
| **Ettus B205mini** | Sniffing + transmit | 12-bit ADC, 56 MHz BW, half-duplex. Good for wideband capture. |
| **Ettus B210** | Full-duplex sniff + inject | Two channels, full-duplex. Can sniff uplink and downlink simultaneously. |
| **HackRF One** | Sniffing + transmit | 8-bit ADC, 20 MHz BW. Cheaper alternative. |
| **RTL-SDR v3/v4** | Receive only | 2 MHz BW. Can monitor one channel at a time. |
| **SX1276 module** | LoRa sniffing + inject | Native LoRa support. Best for LoRa mode decoding. |

### Software Tools

| Tool | Purpose | Notes |
|---|---|---|
| **GNU Radio** | SDR signal processing | FSK demod for 150 Hz mode |
| **gr-lora_sdr** | LoRa demodulation | For 50 Hz mode and binding packets |
| **SoapySDR** | Hardware abstraction | Unifies Ettus, HackRF, RTL-SDR |
| **Inspectrum** | Spectrogram analysis | Visual signal identification |
| **URH (Universal Radio Hacker)** | Protocol analysis | Auto-detect modulation, decode packets |
| **tools/crsf-packet-tool.py** | CRSF frame codec | Encode/decode serial CRSF frames (this repo) |

---

## References

- [TBS CRSF Protocol Specification (official)](https://github.com/tbs-fpv/tbs-crsf-spec/blob/main/crsf.md) — Serial protocol only
- [g3gg0: Analysis of TBS Crossfire, reverse engineering the air link](https://www.g3gg0.de/wordpress/fpv/fpv-analysis-of-tbs-crossfire/) — OTA reverse engineering
- [TBS Crossfire Manual (PDF)](https://www.team-blacksheep.com/media/files/tbs-crossfire-manual.pdf) — Official user manual
- [Betaflight CRSF protocol header](https://github.com/betaflight/betaflight/blob/master/src/main/rx/crsf_protocol.h) — Implementation reference
- [CRSF Working Group Wiki](https://github.com/crsf-wg/crsf/wiki/) — Community protocol documentation
- [gr-lora_sdr](https://github.com/tapparelj/gr-lora_sdr) — GNU Radio LoRa transceiver
- [Deepwiki: CRSF Protocol Fundamentals](https://deepwiki.com/tbs-fpv/tbs-crsf-spec/2-crsf-protocol-fundamentals) — Protocol summary
- [ExpressLRS Wiki](https://en.wikipedia.org/wiki/ExpressLRS) — ELRS comparison context
