# Layer 2 / Chipset-Level Wireless Attack Research

**Status:** Brainstorming — attack surface analysis and target landscape mapping.
Not yet a committed design; pending timeline and approach selection.

**Date:** 2026-10-15

> **Status update (2026-10-15): exploratory background, not the active plan.**
> The owner selected the E8–TinyHawk ELRS control link as the first benchmark
> and paused WiFi entry, WiFi-mode forcing, and RF disruption. The E8's WiFi
> capability and the assumptions in the controller-WiFi chain below were not
> validated on this unit. `SESSION-NOTES.md` §20 is historical; follow
> `PROJECT-ROADMAP.md` for current work.

**Context:** Architectural brainstorming for a hands-on research and implementation
program covering WiFi and Bluetooth/BLE attacks against drone controllers and
ground stations, delivered wirelessly, for CTF preparation.

---

## 1. Attack Philosophy: Wireless → Controller → Drone

The core insight driving this program: **attack the controller, not (only) the
drone.** The RC transmitter or ground control station holds the authentication
secrets, has the richer attack surface (often a full SoC with WiFi + BLE), and
maintains an already-authenticated link to the drone. Compromising the controller
gives us the drone for free.

### Two-stage chain model

```
Stage 1: Wireless entry (WiFi / BLE / RF disruption)
    ↓
  Exploit a vulnerability in the controller's wireless stack
  OR force the controller into a vulnerable mode (e.g., WiFi update mode)
    ↓
Stage 2: Pivot to drone control
    ↓
  Extract binding phrase / UID / authentication secrets
  OR inject commands through the controller's existing authenticated link
  OR forge our own packets using extracted secrets
    ↓
  Drone obeys: land, disarm, RTL, waypoint change, motor shutoff
```

### Why the controller, not the drone directly?

| Factor | Drone (bare-metal FC) | Controller (ESP32 / Android / Linux) |
|---|---|---|
| Operating system | None (Betaflight on STM32) | RTOS, Linux, or Android |
| WiFi/BLE radios | Usually none | Usually both (ESP32, Qualcomm SoC) |
| Known CVEs | Very few for bare-metal | ESP32 BLE, Android BT, Linux kernel |
| Authentication secrets | Receives, doesn't store phrase | **Stores the binding phrase** |
| Network stack | None | Full TCP/IP, BLE GATT, WiFi AP mode |
| Post-compromise value | Control one drone | Control drone + extract secrets for offline forgery |

For bare-metal drones (TinyHawk III, most racing quads), the ELRS/Crossfire
packet IS the deepest layer — there's no kernel to pivot through. But the
controller that sends those packets has a full SoC with WiFi, BLE, and known
vulnerabilities. Attacking the controller is the richer path.

**Exception:** WiFi-based toy drones (Tello, some DJI models) where the drone
itself runs an AP and accepts unauthenticated commands. For these, the drone IS
the direct target — no controller pivot needed.

---

## 2. Target Landscape: Controllers and Ground Stations

A drone CTF could use any of these controller types. We need capabilities against
the most common categories, not just our lab E8.

### 2.1 ELRS TX Controllers (ESP32-based)

**Examples:** EMAX E8, RadioMaster Zorro/Boxer/Pocket/TX16S (ELRS module),
Jumper T-Pro/T-Lite, BetaFPV LiteRadio, HappyModel ES24TX, ImmersionRC Ghost TX

**MCU:** ESP32 (WiFi + BLE + dual-core Xtensa LX6)
**RF:** SX1280 (2.4 GHz LoRa/FLRC) or SX127x (900 MHz LoRa)
**Protocol:** ExpressLRS (open source)

#### WiFi attack surface (PRIMARY)

ELRS TX modules have a built-in WiFi update mode. When active:
- The ESP32 spins up an **open WiFi AP** (typically "ExpressLRS TX" or similar)
- A web server runs on port 80 with **no authentication**
- The web UI exposes: firmware upload, configuration read/write, binding phrase
- The binding phrase (or UID) is the crown jewel — it unlocks the entire ELRS
  attack chain (FHSS sequence, CRC init, packet forgery)

**How WiFi mode is triggered:**
1. Button hold during power-on (manual)
2. **Auto WiFi mode:** TX enters WiFi after failing to connect to its RX for a
   configurable timeout (default ~60s in many builds). This is exploitable:
   **jam or disrupt the ELRS link → TX loses connection → TX auto-enters WiFi
   mode → we connect to its AP → extract secrets**
3. Lua script command from the handset (EdgeTX/OpenTX)
4. Three sequential power cycles (some builds)

**The auto-WiFi-mode forcing chain:**
```
RF jamming (Ettus B205/B210, or just high-power 2.4 GHz noise)
    → ELRS link drops
    → TX auto-WiFi timeout expires
    → ESP32 starts open AP
    → Attacker connects to AP
    → GET /config → binding phrase / UID in plaintext
    → Derive FHSS sequence + CRC init
    → Forge ELRS packets from our own hardware
    → Drone takeover
```

#### BLE attack surface

- **ESP32 BLE provisioning (CVE-2026-45542):** protocomm Security2 heap overflow.
  If the ELRS firmware or a companion feature uses ESP-IDF's BLE provisioning
  (PROV_* advertisements), this is a direct heap corruption path.
- **BrakTooth (CVE-2021-28139):** LMP feature response overflow on ESP32.
  Causes crash or potential code execution on the ESP32's Bluetooth Classic stack.
  Requires BT Classic to be active (not just BLE).
- **ESP32 BLE stack crashes:** Various assertion failures and heap corruptions
  documented in the BrakTooth and SweynTooth research against ESP-IDF versions.

#### ELRS-specific attack surface

- **Binding phrase/UID recovery from RF:** field layout is OTA-version-specific.
  Betaflight 4.4.2 v3 carries UID3, UID4, and UID5, but model match may XOR the
  lower six UID5 bits. On this model-ID-255 pair the XOR is zero; other links
  require mask or CRC resolution before using the 723-phrase rainbow table.
- **ELRS WiFi update page CSRF/injection:** The web server is minimal (ESPAsyncWebServer).
  No CSRF tokens, no auth. If the TX is in WiFi mode and we're on the same network,
  any HTTP request is accepted.

### 2.2 DJI Controllers

**Examples:** DJI RC-N1, RC-N2, RC Pro, RC 2, DJI FPV Remote Controller,
DJI Goggles 2/3

**MCU:** Qualcomm or proprietary SoC (Android-based on RC Pro/RC 2)
**RF:** OcuSync 2/3/4 (proprietary, 2.4/5.8 GHz, encrypted, frequency hopping)
**Protocol:** DJI proprietary (OcuSync + DUML internal command protocol)

#### BLE attack surface (PRIMARY for DJI)

- **CVE-2026-78306 — BLE command injection (16 DJI models):** Unauthenticated
  BLE commands can DoS the drone, change WiFi password (locks out operator),
  power off the aircraft. Affects Mavic, Mini, Air, Neo, Flip, Avata lines.
  This is the highest-value DJI attack for a CTF.
- **CVE-2026-77812 — BLE WiFi credential leak:** DJI drones/controllers transmit
  WiFi SSID and password over BLE during certain operations. Passive BLE sniffing
  within range recovers credentials.
- **CVE-2023-6951 — Weak WPA2 credentials:** Some DJI WiFi links use predictable
  or weak WPA2 passwords derivable from the drone's serial number.

#### WiFi attack surface

- **DJI Enhanced WiFi (Mini SE, older models):** MitM demonstrated by researchers.
  WiFi-based control link without OcuSync. Deauth + evil twin is viable.
- **DroneID broadcast:** DJI drones broadcast unencrypted DroneID packets
  (drone position, pilot position, serial number). Useful for recon, not direct
  control, but reveals the pilot's location for physical proximity attacks.
- **DJI Assistant/app WiFi:** When configuring via DJI Fly app, WiFi is active.
  If we can intercept this connection, we may be able to inject configuration.

#### What we CAN'T easily attack

- **OcuSync 2/3/4:** Proprietary, encrypted, frequency-hopping radio link.
  No public security research has broken the RF link itself. The BLE/WiFi
  side-channels are the realistic attack path, not the primary radio.

### 2.3 TBS Crossfire / Tracer Controllers

**Examples:** TBS Tango 2, TBS Mambo, Crossfire TX module (on any EdgeTX radio)

**MCU:** STM32 (no WiFi, no BLE on the radio module itself)
**RF:** 868/915 MHz (Crossfire) or 2.4 GHz (Tracer)
**Protocol:** CRSF (Crossfire protocol) — no encryption, binding phrase only

#### Attack surface

- **No WiFi or BLE on the RF module.** The Crossfire TX module is bare-metal
  STM32 + RF, similar to the drone side. No network stack to exploit.
- **The EdgeTX handset** (Radiomaster, Jumper, etc.) running the Crossfire
  module may have its own WiFi/BLE for firmware updates — but that's the
  handset, not the Crossfire protocol.
- **CRSF over the air:** No encryption. Binding phrase → UID derivation similar
  to ELRS but TBS-proprietary. SDR capture + replay may work.
- **TBS Agent X (configuration software):** Runs on PC, connects via USB.
  Not a wireless attack surface.

**Assessment:** Crossfire is harder to attack wirelessly than ELRS because there's
no WiFi/BLE side-channel on the RF module. The attack is RF-only: capture CRSF
packets, reverse the binding, forge packets. More like the ELRS direct-injection
path than the controller-exploitation path.

### 2.4 FrSky Controllers

**Examples:** FrSky X-Lite, QX7, X9D+, Horus X10/X12, Tandem X20

**MCU:** STM32 (older) or Linux-based (Tandem X20, Horus series with EdgeTX)
**RF:** Proprietary FrSky D8/D16/ACCESS, 2.4 GHz
**Protocol:** FrSky proprietary — D8 is trivially replayable, D16 has binding,
ACCESS is encrypted (no public break)

#### Attack surface

- **D8 (legacy):** No security. SDR capture and replay works.
- **D16:** Binding-based, similar security model to ELRS but proprietary.
  Less public research than ELRS.
- **ACCESS:** FrSky's encrypted protocol. No public audit, no known breaks.
  If the CTF uses ACCESS, we likely can't attack the radio link.
- **EdgeTX/OpenTX WiFi (on compatible handsets):** Some FrSky radios running
  EdgeTX have ESP32 WiFi backpacks for firmware updates. Same attack surface
  as ELRS WiFi mode if present.

### 2.5 MAVLink Ground Control Stations (Software)

**Examples:** QGroundControl (Linux/Windows/Mac/Android), Mission Planner
(Windows/.NET), MAVProxy (Python CLI), APM Planner 2

**Runs on:** Laptop, tablet, or Herelink (Android GCS with integrated radio)

#### WiFi/network attack surface

- **If the GCS is on a WiFi network:** Evil twin, deauth, PMKID capture, WPA
  cracking. Once on the same network, MAVLink is typically unencrypted UDP
  (port 14550/14551). Inject commands directly.
- **MAVLink v2 signing:** Optional and rarely enabled. When enabled, requires
  a shared 32-byte key. Without signing, anyone on the network can send
  COMMAND_LONG packets.
- **Herelink:** Android-based, runs QGC, has its own WiFi AP for configuration.
  Android attack surface (BLE, WiFi, app vulnerabilities) plus MAVLink.

#### Application-level attack surface

- **QGroundControl parses telemetry from the drone.** Malicious MAVLink telemetry
  could potentially trigger parsing bugs. QGC is C++/Qt — memory corruption is
  plausible but unexplored.
- **Mission Planner is .NET (C#).** Less likely to have memory corruption, but
  logic bugs in parameter handling are possible.
- **MAVProxy is Python.** Logic bugs, not memory corruption.

### 2.6 WiFi Toy Drones (Phone as Controller)

**Examples:** Ryze Tello, DJI Mini SE (Enhanced WiFi mode), Hubsan X4 WiFi,
Cheerson CX-10W, Syma X5UW, numerous cheap camera drones

**Protocol:** Plaintext UDP or proprietary WiFi, drone runs as open AP

#### Attack surface

- **Completely unauthenticated.** Join the drone's WiFi AP → send commands.
  No exploit needed — just network access.
- **Deauth + rogue AP:** Kick the legitimate controller off, present a fake AP
  with the same SSID, the phone reconnects to us, we MitM and inject commands.
- **Tello specifics:** UDP port 8889, plaintext SDK commands, no auth, no
  encryption. Our `tello-takeover.py` already handles this.

---

## 3. Attack Surfaces Summary: What to Build Capabilities For

Ranked by CTF likelihood × exploitability:

| Priority | Target | Wireless entry | Pivot to control | Lab-testable? | Status |
|---|---|---|---|---|---|
| **P1 — NOW** | ELRS TX (ESP32) | WiFi mode exploitation (force + connect) | Extract binding phrase → forge ELRS packets | Yes — our E8 (read-only) | **Active — prove end-to-end first** |
| **P1 — NOW** | MAVLink GCS on WiFi | Evil twin, WPA crack, network injection | MAVLink command injection over UDP | Yes — SITL + laptop + ALFA | Can run in parallel (no hw needed) |
| **P2 — after ELRS proven** | DJI controller | BLE exploitation (CVE-2026-78306) | BLE command injection → DoS / WiFi cred theft | After DJI hw acquisition | Sequenced after Chain 1 proven |
| **P2 — after ELRS proven** | DJI Enhanced WiFi drone | WiFi MitM (deauth + evil twin) | Command injection over WiFi link | After DJI hw acquisition | Sequenced after Chain 1 proven |
| **P2** | WiFi toy drone (Tello etc.) | Deauth + rogue AP, or direct join | Send UDP commands directly | Yes — if we acquire a Tello | Tools mostly built |
| **P3** | ESP32 BLE (BrakTooth) | BLE LMP overflow (CVE-2021-28139) | Crash / potential code exec on ESP32 | Yes — our E8 if BT active | After Chain 1 WiFi path |
| **P3** | Crossfire TX | RF capture + reverse binding | CRSF packet forgery | Need TBS hardware + SDR | Deferred |
| **P3** | Linux GCS kernel | WiFi driver exploit or BleedingTooth | Kernel RCE → full GCS control | Partially — our own machine | Deferred |
| **P4** | FrSky ACCESS | Unknown — no public research | Unknown | Need FrSky hardware | Deferred |

### What we already have vs. what's missing

**Already built (in this repo):**
- ELRS packet analysis, forgery, TX emulation, UID rainbow table (723 phrases)
- ELRS binding phrase → UID derivation (tools/elrs-uid-rainbow.py)
- BLE recon scanner with drone signature database (tools/ble-recon-scanner.py)
- Tello takeover + video capture (tools/tello-takeover.py, tello-video-capture.py)
- MAVLink fuzzer, injector, recon, signing replay (tools/mavlink-*.py)
- Multi-protocol recon chain (tools/exploit_chains/multi_recon.py)
- WiFi/Tello exploit chain (tools/exploit_chains/wifi_tello_chain.py)
- Full vulnerability reference with WiFi/BLE CVE coverage (research/drone-wireless-vuln-reference.md)
- Firmware corpus: Betaflight 4.4.2, ArduPilot 4.7.2, PX4 1.18.0-rc1, ELRS 4.1.0, Tello SDK

**Gaps — no tools or lab exercises yet:**
1. **WiFi evil twin / rogue AP automation** — no tool to automate hostapd + dnsmasq +
   deauth + captive portal for drone WiFi takeover
2. **ELRS WiFi mode exploitation** — no tool to detect ELRS WiFi APs, connect,
   extract config/binding phrase, or force WiFi mode via RF disruption
3. **MAVLink MitM proxy** — no transparent proxy to sit between GCS and drone,
   selectively modify/inject MAVLink in transit
4. **DJI BLE exploitation** — CVE-2026-78306 is documented but no PoC tool
5. **ESP32 BLE exploitation** — BrakTooth/protocomm attacks documented but no
   lab exercise or PoC
6. **WPA cracking pipeline** — no automated PMKID capture → hashcat workflow
7. **WiFi-based MAVLink injection** — no tool that combines WiFi network access
   with MAVLink command injection in one chain
8. **RF disruption / jamming for WiFi mode forcing** — documented concept but
   no implementation or lab exercise

---

## 4. The Three Attack Chains to Build

### Chain 1: ELRS Controller WiFi Exploitation

```
[RF Jamming / Link Disruption]
         ↓
    ELRS TX loses connection to RX
         ↓
    Auto-WiFi timeout expires (~60s)
         ↓
    ESP32 starts open WiFi AP
         ↓
    [ALFA card] Connect to "ExpressLRS TX" AP
         ↓
    HTTP GET /config → binding phrase + UID
         ↓
    Derive FHSS sequence + CRC init (elrs-uid-rainbow.py or elrs_core)
         ↓
    [Ettus B205/B210 or ESP32+SX1280] Forge ELRS packets
         ↓
    Inject RC commands: disarm, land, full-throttle-yaw
    OR inject MSP: reboot FC, change settings, trigger failsafe
```

**Hardware needed:** ALFA card (have), Ettus SDR (have), target ELRS TX
**Software needed:** WiFi AP scanner/connector, HTTP config extractor,
existing ELRS forgery tools (need P1-2 fix for OTA v3)
**Lab test:** E8 controller — read-only (NO flash, NO settings write per
project constraints). Verify WiFi mode entry and config readability.

### Chain 2: WiFi Network → MAVLink Command Injection

```
[ALFA card] Scan for GCS/drone WiFi networks
         ↓
    Option A: WPA network → PMKID capture → hashcat → join
    Option B: Open network → direct join
    Option C: Deauth + evil twin → MitM
         ↓
    On the network: scan for MAVLink endpoints (UDP 14550/14551)
         ↓
    Option A: Direct injection (if no signing)
    Option B: MitM proxy (modify packets in transit)
         ↓
    COMMAND_LONG: land (MAV_CMD 21), RTL (MAV_CMD 20),
                  disarm (MAV_CMD 400 param1=0),
                  set mode LAND (MAV_CMD 176)
```

**Hardware needed:** ALFA card (have), laptop (have)
**Software needed:** Evil twin automation, MAVLink proxy, MAVLink injector
(partially exists in tools/mavlink-injector.py — needs network transport)
**Lab test:** ArduPilot SITL (have the binary) + laptop WiFi network

### Chain 3: DJI BLE Exploitation

```
[BLE adapter] Scan for DJI devices (ble-recon-scanner.py)
         ↓
    Identify target model + BLE characteristics
         ↓
    CVE-2026-78306: Send unauthenticated BLE commands
         ↓
    Option A: Power off aircraft → DoS
    Option B: Change WiFi password → lock out operator
    Option C: Trigger config changes → disrupt flight
         ↓
    If CVE-2026-77812 applicable: Passively sniff WiFi credentials
         ↓
    Join DJI WiFi with recovered credentials
         ↓
    Further exploitation through WiFi interface
```

**Hardware needed:** BLE adapter (built into most laptops, or USB dongle),
DJI drone (NOT in lab — would need to acquire)
**Software needed:** DJI BLE command PoC tool, WiFi credential extractor
**Lab test:** Requires DJI hardware acquisition

---

## 5. Hardware in Lab vs. Hardware Needed

### Currently available

| Hardware | Relevant capability |
|---|---|
| EMAX E8 controller | ELRS TX — test WiFi mode, BLE scanning (read-only, NO flash) |
| EMAX TinyHawk III | ELRS RX — target drone for injection testing |
| Multiple ALFA cards | WiFi monitor mode, injection, deauth, rogue AP |
| Ettus B205mini | SDR — RF jamming, 2.4 GHz disruption |
| Ettus B210 | SDR — full-duplex, higher bandwidth |
| Ubuntu lab machine | Attack platform |
| FPV headset | Monitor analog FPV feed |

### Acquisition candidates (for expanding target coverage)

| Hardware | Purpose | Estimated cost | Priority |
|---|---|---|---|
| Ryze Tello | WiFi toy drone — live Chain 2 target, trivial takeover | ~$80 | P1 |
| ESP32 dev board (ESP32-WROOM-32) | BrakTooth / BLE exploit development without risking E8 | ~$10 | P1 |
| DJI Mini SE or Mini 2 SE | DJI BLE/WiFi target — Chain 3 development | ~$250–350 | P2 |
| TBS Crossfire TX Lite | Crossfire protocol research, CRSF capture/replay | ~$50 | P3 |
| Pixhawk flight controller (clone) | MAVLink hardware target, complement to SITL | ~$40 | P3 |
| Herelink controller | Android-based MAVLink GCS target | ~$400 | P4 |
| USB BLE adapter (CSR8510-based) | Dedicated BLE attack adapter | ~$10 | P1 |

---

## 6. Constraints and Safety

- **EMAX E8: absolute no-flash, no-write.** Read configuration and observe WiFi
  mode only. Do not write settings, upload firmware, or fuzz the live device.
- **EMAX TinyHawk III: read-only characterization.** Same constraints as E8 for
  the lab drone.
- **RF transmission:** Jamming on 2.4 GHz is illegal outside of shielded
  enclosures or authorized test environments. Lab exercises involving RF
  disruption must use appropriate isolation or be simulation-only until
  game day authorization is confirmed.
- **All WiFi attacks against our own equipment only.** Do not target networks
  or devices we don't own.
- **No flash, no firmware modification** on any lab hardware without explicit
  owner authorization per device.
- **Game day is different:** On competition day, all techniques are authorized
  against competition targets per the CTF rules. The tools must be ready but
  the constraints above apply to lab development only.

---

## 7. Owner Decisions (2026-10-15)

1. **Timeline:** ~1 month target (mid-November 2026). Build depth on the
   primary target first, prove it works, then expand.
2. **Hardware strategy:** Prove capability on the TinyHawk III + EMAX E8
   (ELRS, Chain 1) first. DJI hardware available once ELRS chain is proven
   end-to-end. This means Chain 1 is the priority; Chain 3 (DJI BLE) is
   sequenced after.
3. **Phasing:** ELRS/E8 first → prove end-to-end → then DJI acquisition →
   Chain 3. Chain 2 (MAVLink/WiFi) can run in parallel using SITL (no
   hardware acquisition needed).

### Remaining open questions

1. **RF test environment:** Do we have or can we get a shielded enclosure for
   RF jamming tests? Or do we develop jamming tools in simulation only?
2. **CTF facts (SESSION-NOTES §4, Q1–Q9):** Still inform priority, but we
   proceed with ELRS as the default path regardless.
3. **E8 WiFi mode testing:** Has the E8 been observed in WiFi mode? Can we
   safely trigger it (power cycle without RX in range) without violating
   the no-write constraint? Entering WiFi mode is a read-only operation
   on the controller side, but this needs owner confirmation.

---

## 8. Relationship to Existing Work

This Layer 2 program **complements, not replaces**, the existing attack paths:

- **ELRS path (SESSION-NOTES §5, Phase B/C):** The ELRS packet forgery tools
  are the *output* of Chain 1. This program adds the *wireless entry* stage
  that forces the controller into WiFi mode and extracts the binding phrase.
- **MAVLink path:** The MAVLink injector/fuzzer tools are the *payload* of
  Chain 2. This program adds the *network access* stage via WiFi attacks.
- **Tello/WiFi path:** Already partially built. This program adds evil twin
  automation and rogue AP capabilities.
- **BLE recon:** The scanner exists. This program adds *active exploitation*
  (DJI BLE command injection, ESP32 BLE attacks).

The existing tools handle "once we have access, what do we send." This program
handles "how do we get access wirelessly in the first place."
