# Layer 2 / Chipset Attack Research & Implementation Plan

> **Purpose:** Structured plan for researching and implementing wireless attack capabilities against drone platforms for CTF competition.
> **Role:** Attack team — deny, disrupt, commandeer target drones and capture flags from ground control stations.
> **Constraint:** All attacks must be deliverable wirelessly (RF). Serial/JTAG access available for lab development and debugging only — not available on game day.
> **Last updated:** 2026-10-14

---

## Table of Contents

- [Shared Understanding](#shared-understanding)
- [Phase 1: WiFi-Based Drone Attacks](#phase-1-wifi-based-drone-attacks)
- [Phase 2: MAVLink / Telemetry Attacks](#phase-2-mavlink--telemetry-attacks)
- [Phase 3: ELRS / RC Protocol Attacks](#phase-3-elrs--rc-protocol-attacks)
- [Phase 4: Bluetooth / BLE Attacks](#phase-4-bluetooth--ble-attacks)
- [Phase 5: GPS Spoofing](#phase-5-gps-spoofing)
- [Phase 6: GCS Flag Capture (Wireless)](#phase-6-gcs-flag-capture-wireless)
- [Phase 7: Attack Chaining & Game-Day Playbook](#phase-7-attack-chaining--game-day-playbook)
- [Lab Exercises Checklist](#lab-exercises-checklist)
- [Open Questions](#open-questions)

---

## Shared Understanding

### Objectives

On game day, the attack team needs to:

1. **Deny / disrupt drones** — stop rotors, force landing, trigger RTH, power off, or otherwise remove a drone from the opponent's control
2. **Commandeer drones** — take over control link, redirect flight, change waypoints
3. **Capture flags from the GCS** — extract flag data from the opponent's ground control station, delivered entirely over wireless (WiFi, BT, RF — no physical access to the GCS)

### Constraints

- **All payloads delivered wirelessly.** No physical/serial/USB access on game day.
- **Unknown target platforms.** Could be WiFi-controlled (Tello, Parrot), MAVLink (ArduPilot/PX4 on Pixhawk), ELRS FPV, DJI, or something custom. Must prepare for multiple protocol families.
- **Serial access in lab only.** Used for firmware inspection, debugging attack scripts, validating effects — but the attack itself must work over the air.
- **Defense handled by a teammate.** This plan focuses exclusively on offensive capabilities.

### Success Criteria

- Can reliably execute at least one DoS and one takeover attack against each protocol family (WiFi, MAVLink, ELRS, BLE)
- Can wirelessly exfiltrate data from a GCS running common drone software
- Have a game-day playbook: see an unknown drone, identify its protocol in <60 seconds, select and launch the right attack
- All attack tooling is scripted/automated — no fumbling with manual commands under time pressure

---

## Phase 1: WiFi-Based Drone Attacks

**Target platforms:** Tello, Parrot Bebop/Anafi, any WiFi AP-mode drone
**Lab target:** Ryze Tello (primary), Parrot Bebop 2 (secondary)

### 1.1 Reconnaissance

**Goal:** Identify and fingerprint a WiFi-controlled drone on the network.

| Exercise | Tools | Expected Output |
|---|---|---|
| Scan for drone APs | `airodump-ng` on ALFA | SSID pattern (TELLO-XXXXXX, Bebop2-XXXXXX), channel, BSSID |
| Identify encryption | `airodump-ng` | Open / WPA2-PSK / WPA2 with PMF |
| Probe for services | `nmap -sV` after connecting | Open ports — Telnet (23), FTP (21), HTTP (80), UDP control ports |
| Capture management frames | Wireshark in monitor mode | Beacon interval, capabilities, vendor OUI |

**Script to build:** `recon-wifi-drone.sh` — automated scan that identifies drone SSIDs by pattern matching and reports encryption type, channel, and BSSID.

### 1.2 Denial of Service

| Attack | Method | Tool | Effect on Drone |
|---|---|---|---|
| **Deauthentication flood** | Forge deauth frames targeting drone↔controller | `aireplay-ng -0 0 -a <BSSID>` | Disconnect controller; drone enters failsafe (land/hover/RTH) |
| **Deauth + channel hop** | Sweep all channels with deauth | `mdk4 <iface> d` | Hit drone regardless of channel |
| **Beacon flood** | Saturate 2.4/5 GHz with fake APs | `mdk4 <iface> b` | Interference; may disrupt reconnection |
| **Authentication flood** | Send auth requests to drone AP | `mdk4 <iface> a -a <BSSID>` | Exhaust AP connection table |

**Lab exercise:**
1. Power on Tello, connect controller phone
2. Launch deauth from ALFA → verify controller loses connection
3. Observe Tello behavior (does it hover? land? RTH?)
4. Time how fast reconnection happens after deauth stops
5. Test whether continuous deauth prevents reconnection entirely

**Script to build:** `wifi-dos.py` (Scapy) — targeted deauth with BSSID auto-detection, supports continuous and burst modes.

### 1.3 Network Access & Takeover

| Attack | Method | Tools | Prerequisite |
|---|---|---|---|
| **Connect to open AP** | Just connect (Tello default) | `nmcli` / `wpa_supplicant` | Drone AP is open |
| **PMKID capture + crack** | Grab PMKID, crack offline | `hcxdumptool` → `hashcat -m 16800` | Drone AP uses WPA2 |
| **Handshake capture** | Deauth → capture 4-way handshake → crack | `airodump-ng` + `aireplay-ng` → `hashcat -m 22000` | Need connected client |
| **Default password** | Try known defaults for model | Manual / script | Drone uses factory password |
| **Evil twin** | Clone drone's AP, deauth real one, catch controller reconnect | `hostapd-mana` | Controller auto-reconnects |

**Post-access actions (once on drone's WiFi):**
- Tello: Send SDK commands via UDP to port 8889 (`command`, `takeoff`, `land`, `emergency`)
- Parrot: Telnet to root shell (AR.Drone), or FTP access to media
- Any: ARP spoof between drone and controller to MitM command traffic

**Scripts to build:**
- `tello-takeover.py` — connect to Tello WiFi, send SDK commands (land, emergency stop, flip to crash)
- `wifi-crack-pipeline.sh` — automated PMKID/handshake capture → hashcat pipeline
- `evil-twin-drone.sh` — clone a drone AP, deauth original, capture connecting clients

### 1.4 Data Exfiltration (Flag Capture)

Once on the drone's network:
- Scan for the GCS (controller) on the same subnet
- Probe GCS for open services (HTTP dashboards, debug ports, exposed APIs)
- Capture traffic between drone and GCS — flags may be in telemetry, command payloads, or file transfers
- Check for unencrypted FTP/HTTP serving flag files

---

## Phase 2: MAVLink / Telemetry Attacks

**Target platforms:** ArduPilot, PX4 on Pixhawk with SiK radio or WiFi telemetry
**Lab target:** Pixhawk flight controller + SiK 915 MHz radio pair

### 2.1 Reconnaissance

| Exercise | Tools | Output |
|---|---|---|
| Scan 433/915 MHz for SiK signals | B205mini/B210 + GNU Radio | Detect FHSS telemetry signals |
| Capture MAVLink over WiFi | Wireshark (MAVLink dissector), `tcpdump` on UDP 14550 | System ID, message types, signing status |
| Identify MAVLink version | Inspect packet header (magic byte: 0xFD = v2, 0xFE = v1) | v1 = no signing possible; v2 = check if signing enabled |
| Extract NetID from SiK | SDR analysis of SiK FHSS pattern | NetID needed for injection |

**Script to build:** `mavlink-recon.py` — sniff MAVLink traffic, report system ID, component ID, message types, whether signing is enabled, and vehicle type.

### 2.2 Command Injection (Unsigned MAVLink)

If MAVLink signing is not enabled (the default on both ArduPilot and PX4):

| Attack | MAVLink Command | Effect |
|---|---|---|
| **Disarm motors** | `MAV_CMD_COMPONENT_ARM_DISARM` (param1=0) | Immediate motor stop — drone falls |
| **Force land** | `MAV_CMD_NAV_LAND` | Controlled landing at current position |
| **Force RTH** | `MAV_CMD_NAV_RETURN_TO_LAUNCH` | Returns to home point |
| **Set mode LAND** | `SET_MODE` (mode=LAND) | Same as force land, different message type |
| **Override RC** | `RC_CHANNELS_OVERRIDE` | Take over RC inputs — full manual control |
| **Change home point** | `MAV_CMD_DO_SET_HOME` | Change RTH destination |
| **Set waypoint** | `MISSION_ITEM` sequence | Redirect autonomous flight |
| **Reboot** | `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` | Reboot flight controller |

**Lab exercise (serial for validation, injection over WiFi/radio):**
1. Set up Pixhawk running ArduPilot with WiFi telemetry (ESP32 or SiK + WiFi bridge)
2. Connect legitimate GCS (QGroundControl) via one link
3. From attack machine, use `pymavlink` to inject `COMPONENT_ARM_DISARM(disarm)` over UDP
4. Verify motors disarm via serial console
5. Repeat with each command type above
6. Test `RC_CHANNELS_OVERRIDE` to take manual control away from the legitimate controller

**Script to build:** `mavlink-injector.py` — unified tool that takes a target IP/port (or serial-over-radio), discovers system ID by sniffing, and offers a menu of attack commands. Must work over both UDP (WiFi) and serial (SiK radio via UART adapter).

### 2.3 PX4 Shell Exploitation (CVE-2026-1579)

If the target runs PX4 with unsigned MAVLink:

1. Establish MAVLink connection (UDP or serial)
2. Send `MAV_CMD_DO_SEND_BANNER` or use the MAVLink shell protocol to open a shell session
3. Execute arbitrary commands on the flight controller
4. Exfiltrate parameters, logs, or flag files from the SD card

**Script to build:** `px4-shell.py` — MAVLink shell client that automates CVE-2026-1579 exploitation over wireless.

### 2.4 SiK Radio Injection (433/915 MHz)

For SiK telemetry radios (no encryption on standard firmware):

1. **Identify NetID:** Analyze FHSS pattern with B210 to extract the NetID
2. **Configure attack radio:** Set a SiK radio or SDR to the same NetID and frequency
3. **Inject MAVLink:** Send commands on the same channel — no authentication needed
4. **Alternative:** Use B210 as a raw transmitter to replay or inject MAVLink frames directly via SDR

**Lab exercise:**
1. Set up Pixhawk with SiK 915 MHz pair
2. Use B210 to capture and decode SiK traffic
3. Identify NetID from the captured FHSS pattern
4. Inject MAVLink DISARM command via a second SiK radio (or directly from B210)
5. Validate disarm via serial console

**Script to build:** `sik-analyzer.py` — GNU Radio flowgraph + Python for SiK FHSS dehopping and NetID extraction.

---

## Phase 3: ELRS / RC Protocol Attacks

**Target platform:** TinyHawk III with ELRS receiver, EMAX E8 TX
**Lab target:** Your own existing hardware

### 3.1 ELRS Version Fingerprinting

Determine the exact OTA format before interpreting a SYNC. Do not infer field
layout from the marketing major version alone:

- **1.x / 2.x:** the 2022 Fox-IT binding-phrase recovery applies to the
  affected historical formats.
- **Betaflight 4.4.2 / OTA v3 (the owned TinyHawk):** the six-byte SYNC payload
  is `fhssIndex, nonce, flags, UID3, UID4, UID5`. Model match can XOR only the
  lower six bits of the transmitted `UID5`; this pair has model ID 255, so the
  XOR is zero. The receiver compares 18 UID bits and checks CRC14 with the full
  `UID[4:5]`-derived initializer.
- **OTA v4:** `UID3` is removed and replaced by the separate rate/flags layout;
  v3 tooling must not parse it as v3.

| Method | Tool | Detail |
|---|---|---|
| Check firmware via Betaflight | Serial (lab only) | Betaflight CLI → `get expresslrs` or check via ELRS web UI |
| Capture sync packets OTA | B210 + GNU Radio | Analyze 2.4 GHz ISM band for ELRS FHSS pattern |
| Identify protocol version from sync format | Custom decoder | Sync packet structure differs between 2.x and 3.x |

### 3.2 Historical ELRS 1.x/2.x Binding Phrase Recovery

**Attack path (from our reference doc):**
1. Capture sync packets with B210 — extract final 3 bytes of UID
2. Extract CRC initializer (uses final 2 bytes of UID from sync packet)
3. Reconstruct binding phrase hash (MD5 — broken, fast to compute)
4. With recovered binding parameters, configure an attack TX to match
5. Inject control packets — full link hijack

This historical exercise is not applicable to the owned TinyHawk. Its firmware
must not be downgraded, reflashed, or otherwise changed. Validate the literature
against archived artifacts or a separate expendable target whose owner permits
that exact test.

**Script to build:** `elrs-analyzer.py` — capture ELRS sync packets via B210, extract UID bytes, compute binding parameters.

### 3.3 ELRS 3.x Considerations

For the pinned Betaflight 4.4.2 implementation, it is incorrect to say that v3
removes UID fields from SYNC. Source inspection and passive decoding established
the v3 structure above. Sixteen decoded SYNC frames agreed on the two-byte CRC
suffix, and that suffix independently validated 38 decoded RC frames. On this
model-match-disabled pair, a decoded SYNC also directly provides UID bytes
3--5; UID byte 2 is the remaining unknown FHSS-seed byte (256 candidates).
That target-specific air-link recovery is distinct from claiming that the old
Fox-IT phrase-recovery procedure applies unchanged to every v3 implementation.

Remaining work is version-matched timing and receiver-acceptance validation.
Jamming is an availability fallback, not a consequence of UID fields being
absent from v3 SYNC.

### 3.4 nRF24L01 / Toy Drone Replay

For toy-grade drones using nRF24L01 (Syma, Hubsan, cheap quads):
1. Capture control traffic with B210 on 2.4 GHz
2. Demodulate GFSK signal (nRF24 standard modulation)
3. No rolling codes or encryption — replay captured packets directly
4. Replay "throttle zero" or "disarm" commands

**Script to build:** `nrf24-replay.py` — GNU Radio flowgraph for nRF24L01 capture and replay via B210.

---

## Phase 4: Bluetooth / BLE Attacks

**Target platforms:** DJI drones (BLE control commands), ESP32-based drones (BLE provisioning), any BLE-enabled flight controller
**Lab target:** ESP32 dev board (primary), DJI drone if acquired

### 4.1 BLE Reconnaissance

| Exercise | Tool | Output |
|---|---|---|
| Scan for BLE devices | `bluetoothctl`, `bettercap ble.recon` | Device names, addresses, service UUIDs |
| Enumerate GATT services | `gatttool`, `bluetoothctl` | Writable characteristics, provisioning endpoints |
| Identify DJI drones | BLE scan for DJI-pattern names | Model identification for CVE selection |
| Passive BLE sniffing | nRF52840 dongle + Wireshark | Capture BLE traffic without connecting |

### 4.2 DJI BLE Command Injection (CVE-2026-78306)

If the target is a DJI drone (16 affected models):
1. Scan for DJI BLE advertisements
2. Connect without trusted UUID — most commands don't check
3. Send commands: change WiFi SSID/password, disable WiFi, restart WiFi, power off
4. **DoS:** Change WiFi password → operator loses connection to drone
5. **Power off:** Send power-off command → drone stops

**Script to build:** `dji-ble-attack.py` — scan for DJI BLE, connect, and send unauthenticated commands.

### 4.3 DJI BLE Credential Sniffing (CVE-2026-77812)

Passive attack — no connection needed:
1. Run BLE sniffer near target DJI drone
2. Capture WiFi credentials sent in cleartext over BLE
3. Use recovered credentials to connect to drone's WiFi network
4. Pivot to GCS flag capture (Phase 6)

### 4.4 ESP32 BLE Exploitation

Against ESP32-based drone controllers or companion computers:

| CVE | Attack | Lab Exercise |
|---|---|---|
| CVE-2021-28139 (BrakTooth) | LMP feature page overflow → RCE | Flash ESP32 with vulnerable ESP-IDF ≤4.4, trigger from attack device |
| CVE-2026-45542 | protocomm heap overflow via BLE provisioning | Set up ESP32 with Security2 provisioning, send crafted packets |
| CVE-2026-81507 | L2CAP OOB read → crash | Send malformed L2CAP from nearby device |

**Script to build:** `esp32-ble-fuzz.py` — targeted BLE fuzzer for ESP32 provisioning and L2CAP interfaces.

---

## Phase 5: GPS Spoofing

**Tool:** Ettus B210 (preferred — full duplex, higher power) or B205mini
**Software:** gps-sdr-sim + GNU Radio

### 5.1 Static Position Spoof

1. Generate fake GPS signal for a fixed position using `gps-sdr-sim`
2. Transmit via B210
3. Verify target drone's GPS locks onto spoofed signal (check via serial telemetry in lab)
4. Observe behavior — does the drone drift? trigger geofence? panic?

### 5.2 RTH Redirect

1. Allow drone to acquire real GPS fix and record home position
2. Begin spoofing a gradually shifted position
3. Trigger failsafe (via WiFi deauth, ELRS jam, or MAVLink command)
4. Drone initiates RTH — but "home" is now the spoofed position
5. Drone flies to attacker's chosen location

### 5.3 Geofence Escape/Trigger

1. Spoof GPS to place the drone inside a geofence exclusion zone
2. Drone may auto-land or refuse to fly
3. Alternatively: spoof position to make the drone think it's outside its allowed zone

**Script to build:** `gps-spoof.sh` — wrapper around gps-sdr-sim + B210 TX, with presets for static spoof, gradual drift, and RTH redirect.

**Safety note:** GPS spoofing transmits RF. Only do this in a shielded environment or with appropriate authorization. Even low-power spoofing can affect nearby receivers.

---

## Phase 6: GCS Flag Capture (Wireless)

The GCS is the ground control station — laptop or phone running QGroundControl, Mission Planner, DJI Fly, FreePilot, etc. Flags are likely stored on or accessible from the GCS. All access must be achieved wirelessly.

### 6.1 WiFi-Based GCS Access

**Scenario:** GCS is on the same WiFi network as the drone (or the drone IS the WiFi AP).

| Step | Method | Tools |
|---|---|---|
| Get on the network | Crack drone AP password, or connect to open AP | `hashcat`, `hcxdumptool`, manual connect |
| Discover GCS on subnet | ARP scan, mDNS/SSDP discovery | `nmap -sn`, `arp-scan`, `avahi-browse` |
| Port scan GCS | Identify open services | `nmap -sV -p-` |
| Exploit exposed services | Web dashboards, debug APIs, MAVLink forwarding ports | Custom scripts, `curl`, `pymavlink` |
| Intercept drone↔GCS traffic | ARP spoof + packet capture | `bettercap`, `ettercap`, Wireshark |
| Extract flags from traffic | Search captured traffic for flag patterns | `grep`, `strings`, Wireshark filters |

### 6.2 MAVLink-Based GCS Data Exfiltration

If the GCS is running MAVLink:
1. Inject `MAV_CMD_REQUEST_MESSAGE` to request specific telemetry
2. Request file list from GCS via MAVLink FTP protocol
3. Download log files, parameter files, or flag files via MAVLink FTP
4. Intercept GCS→drone parameter writes for credential/flag data

### 6.3 Rogue AP + Captive Portal

1. Clone the drone's WiFi AP (evil twin)
2. Deauth the real drone AP
3. GCS auto-reconnects to evil twin
4. MitM all GCS traffic — capture flags in transit
5. Optionally serve a captive portal to capture credentials

### 6.4 BLE-Based GCS Access

If the GCS phone has BLE exposed:
1. Scan for BLE services on GCS device
2. DJI Fly app may expose data over BLE
3. Sniff BLE traffic between GCS phone and drone for flag data

---

## Phase 7: Attack Chaining & Game-Day Playbook

### Rapid Protocol Identification (<60 seconds)

```
1. airodump-ng scan (2s)     → WiFi AP with drone-pattern SSID?
   ├── YES → Phase 1 (WiFi attack)
   └── NO ↓

2. BLE scan (3s)             → DJI/ESP32 BLE advertisements?
   ├── YES → Phase 4 (BLE attack)
   └── NO ↓

3. B210 spectrum sweep       → 915/433 MHz SiK telemetry? 2.4 GHz FHSS?
   ├── SiK signal → Phase 2 (MAVLink injection)
   ├── ELRS signal → Phase 3 (ELRS attack)
   ├── nRF24 signal → Phase 3.4 (replay)
   └── Unknown → deeper analysis
```

**Script to build:** `identify-target.sh` — runs WiFi scan, BLE scan, and SDR sweep in parallel, reports what it finds in a summary table.

### Attack Selection Matrix

| Identified Protocol | Fastest DoS | Fastest Takeover | Flag Capture Path |
|---|---|---|---|
| **WiFi (open)** | Deauth flood | Connect + send commands | Join network → scan GCS |
| **WiFi (WPA2)** | Deauth flood | PMKID crack → connect | Crack → join → scan GCS |
| **MAVLink (unsigned)** | DISARM command | RC_CHANNELS_OVERRIDE | MAVLink FTP to GCS |
| **MAVLink (signed)** | GPS spoof → failsafe | Dictionary attack on key | Intercept MAVLink traffic |
| **ELRS 2.x** | 2.4 GHz jam | Binding phrase recovery | N/A (no IP network) |
| **ELRS 3.x** | 2.4 GHz jam | GPS spoof + failsafe exploit | N/A |
| **DJI (BLE exposed)** | CVE-2026-78306 power off | Change WiFi password → connect | CVE-2026-77812 cred sniff → WiFi |
| **Toy (nRF24)** | Jam 2.4 GHz | Replay disarm | N/A |

### Recommended Attack Order on Game Day

1. **Passive recon first** — never transmit before you know what you're looking at
2. **DoS is fast, takeover is valuable** — start with DoS to score quick points, then work on takeover
3. **Layer attacks** — deauth + GPS spoof simultaneously for maximum disruption
4. **GCS flags last** — these require network access and take longer, but may be worth more points

---

## Lab Exercises Checklist

Track completion as you work through each exercise. Mark with `[x]` when done.

### WiFi Attacks
- [ ] Identify a drone AP with airodump-ng
- [ ] Execute deauth attack against Tello, observe failsafe behavior
- [ ] Capture PMKID from a WPA2 drone AP
- [ ] Crack captured PMKID/handshake with hashcat
- [ ] Connect to Tello, send land/emergency commands via SDK
- [ ] Execute evil twin against drone AP, capture reconnecting client
- [ ] ARP spoof between drone and GCS, capture traffic

### MAVLink Attacks
- [ ] Set up Pixhawk + ArduPilot + SiK radio
- [ ] Capture MAVLink traffic in Wireshark, identify message types
- [ ] Inject DISARM command via pymavlink over WiFi UDP
- [ ] Inject RC_CHANNELS_OVERRIDE, take manual control
- [ ] Inject SET_MODE (LAND, RTL) commands
- [ ] Change home point via MAV_CMD_DO_SET_HOME
- [ ] Extract files via MAVLink FTP protocol
- [ ] (If PX4) Execute CVE-2026-1579 shell exploitation

### SiK Radio Attacks
- [ ] Capture SiK 915 MHz traffic with B210
- [ ] Identify NetID from FHSS pattern
- [ ] Inject MAVLink commands via second SiK radio or B210

### ELRS Attacks
- [ ] Capture ELRS 2.4 GHz traffic with B210
- [ ] Extract UID bytes from sync packets
- [ ] Recover binding phrase parameters
- [ ] Inject control packets from attack TX
- [ ] Test jamming → observe failsafe behavior
- [ ] Verify attack fails on ELRS 3.x firmware

### Bluetooth/BLE Attacks
- [ ] BLE scan and GATT enumeration of ESP32
- [ ] Trigger BrakTooth (CVE-2021-28139) crash on ESP32
- [ ] Trigger protocomm overflow (CVE-2026-45542) on ESP32
- [ ] (If DJI available) Execute CVE-2026-78306 command injection
- [ ] (If DJI available) Sniff BLE for WiFi credentials (CVE-2026-77812)

### GPS Spoofing
- [ ] Generate static spoofed GPS signal with gps-sdr-sim
- [ ] Transmit via B210, verify drone GPS lock (serial monitor)
- [ ] Execute gradual position drift
- [ ] Execute RTH redirect (trigger failsafe + GPS spoof)

### GCS Flag Capture
- [ ] Discover GCS on drone network via ARP scan
- [ ] Port scan GCS, identify exposed services
- [ ] ARP spoof GCS↔drone, search traffic for flags
- [ ] Extract data via MAVLink FTP
- [ ] Evil twin AP → capture GCS traffic

### Integration
- [ ] Run full attack chain: identify → DoS → takeover → exfiltrate in <5 minutes
- [ ] Run full attack chain against a second protocol family
- [ ] Practice rapid protocol identification (<60 seconds)

---

## Open Questions

Things we don't know yet that will affect the plan:

1. **CTF rules on jamming:** Is broadband jamming allowed, or only targeted protocol-level attacks?
2. **CTF rules on GPS spoofing:** Is GPS spoofing in scope, or is it out of bounds?
3. **Scoring model:** Are DoS, takeover, and flag capture scored differently? Which is worth most?
4. **Team coordination:** How does the defense teammate communicate with the attack team? Shared comms channel?
5. **Time format:** Is it a timed round-robin, continuous, or bracket?
6. **Physical layout:** How far apart are teams? This affects RF range constraints.
7. **Legal/authorization:** Is there a signed authorization for the CTF that covers RF transmission?

---

## Scripts To Build (Summary)

All scripts should be in a `tools/` directory in this repo.

| Script | Phase | Purpose |
|---|---|---|
| `recon-wifi-drone.sh` | 1.1 | Auto-scan for drone WiFi APs |
| `wifi-dos.py` | 1.2 | Targeted deauth with auto-BSSID detection |
| `tello-takeover.py` | 1.3 | Tello SDK command injection |
| `wifi-crack-pipeline.sh` | 1.3 | PMKID/handshake capture → hashcat pipeline |
| `evil-twin-drone.sh` | 1.3 | Clone drone AP + deauth original |
| `mavlink-recon.py` | 2.1 | MAVLink sniffer + analyzer |
| `mavlink-injector.py` | 2.2 | MAVLink command injection tool |
| `px4-shell.py` | 2.3 | PX4 CVE-2026-1579 exploitation |
| `sik-analyzer.py` | 2.4 | SiK FHSS dehopping + NetID extraction |
| `elrs-analyzer.py` | 3.2 | ELRS sync capture + binding phrase recovery |
| `nrf24-replay.py` | 3.4 | nRF24L01 capture and replay |
| `dji-ble-attack.py` | 4.2 | DJI BLE command injection |
| `esp32-ble-fuzz.py` | 4.4 | ESP32 BLE provisioning fuzzer |
| `gps-spoof.sh` | 5 | GPS spoofing wrapper (gps-sdr-sim + B210) |
| `identify-target.sh` | 7 | Rapid protocol identification |
