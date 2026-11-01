# FPV Drone Target Sets — Attack Surface Survey

**Date:** 2026-10-31
**Context:** Authorized security research expanding beyond the current ELRS/Betaflight/TinyHawk III Plus target set.

---

## 1. DJI Digital Link Ecosystem (O3 / O4 / OcuSync)

### Hardware/Firmware
- **Air Units:** DJI O3 Air Unit, O4 Air Unit, Vista (legacy), original DJI FPV Air Unit
- **Goggles:** DJI Goggles V2, Goggles 2 (O3-compatible), Goggles 3 (O4-compatible), Goggles Integra
- **Protocol:** OcuSync (proprietary OFDM digital link, 2.4 GHz / 5.8 GHz dual-band)
- **FC integration:** DJI FPV Drone (proprietary FC), or third-party Betaflight FCs via MSP DisplayPort over the DJI HD system

### Attack Surfaces

**a) DroneID Broadcast — Unencrypted Remote ID (NDSS 2023)**
- DJI drones broadcast DroneID frames embedded in OcuSync/WiFi-adjacent PHY transmissions
- **Confirmed unencrypted:** GPS coordinates, serial number, pilot location, altitude, speed are all in cleartext
- Receivable with commodity SDR (HackRF/USRP) — CISPA/Bochum researchers built open-source decoders
- **Impact:** Real-time tracking, pilot geolocation, and de-anonymization of any DJI drone from the ground
- **Published research:** Schiller et al., "Drone Security and the Mysterious Case of DJI's DroneID" (NDSS 2023); Bender, "DJI drone IDs are not encrypted" (arXiv 2207.10795)
- GitHub repos exist for OcuSync O2/O4 DroneID PHY analysis

**b) Enhanced WiFi Protocol Hijacking ($400 attack)**
- Nico Schiller et al. (arXiv 2309.05913, Politecnico di Milano thesis) reverse-engineered DJI's "Enhanced WiFi" protocol used on consumer DJI drones (Mini series, some FPV configurations)
- Full drone hijack demonstrated with ~$400 of equipment (SDR + laptop)
- Attack exploits protocol-level authentication weaknesses
- **Limitation:** Applies to Enhanced WiFi mode, NOT to OcuSync/O3/O4 proprietary link (separate attack surface)

**c) 2026 CVEs**
- **CVE-2026-1743:** Authentication bypass in DJI drone firmware — allows unauthorized command execution
- **CVE-2026-78255:** Information disclosure vulnerability — leaks flight data/configuration
- **CVE-2026-77812:** Information disclosure flaw — similar class, affects DJI consumer drones
- Specific affected firmware versions tracked on NVD

**d) Goggle Rooting (fpv.wtf / WTFOS)**
- The fpv-wtf project achieved root on DJI FPV Goggles V1/V2 and Air Unit/Vista via the "Margerine" exploit
- WTFOS community firmware package manager runs on rooted goggles
- Underlying platform is Android/Linux — once rooted, full shell access
- DJI attempted to patch in firmware v01.00.06.07 (partially successful)
- **Relevance:** Proves the DJI embedded platform is rootable; similar techniques may apply to O3/O4 hardware

**e) OcuSync Protocol Itself**
- Proprietary OFDM, no published spec
- Active reverse engineering on GitHub (dji-drone-ocusync-research, dji-ocusync-droneid-research)
- KTH thesis work on DJI drone security architecture
- Far less studied than ELRS due to proprietary nature — large unexplored attack surface

### Comparison to Current ELRS Target
DJI is a **much harder** but **much higher-value** target. The proprietary stack means less community tooling, but the install base is enormous (DJI dominates FPV market share). The DroneID disclosure is already exploitable with commodity SDR. The Enhanced WiFi hijack is proven but limited to specific modes. OcuSync core protocol remains largely unexplored — a breakthrough there would affect millions of drones.

---

## 2. HDZero Digital VTX Ecosystem

### Hardware/Firmware
- **VTX modules:** HDZero Race V3, HDZero Freestyle V2, HDZero Whoop Lite, etc.
- **Goggles:** HDZero Goggle (Digiview-based), Fatshark HDO3 (integrated HDZero RX)
- **Protocol:** Custom low-latency digital link (H.265 encoding, ultra-low latency ~4ms)
- **Open source:** VTX firmware is open source on GitHub (hd-zero/hdzero-vtx)

### Attack Surfaces

**a) Open-Source Firmware — Full Source Audit Available**
- Unlike DJI/Walksnail, HDZero's VTX firmware is publicly auditable
- GitHub security tab shows no reported vulnerabilities — but this likely means **nobody has looked**, not that it's secure
- Source audit could reveal buffer overflows in OSD rendering, firmware update paths, serial protocol handling
- MCU targets are documented (different "VTX targets" for different hardware)

**b) One-Way Video Link (VTX → Goggles)**
- HDZero is primarily a one-way video link (VTX transmits, goggles receive)
- Limited bidirectional capability for configuration
- **Attack surface is narrower** than full-duplex systems — primarily video injection/jamming
- VTX configuration over serial/UART from FC — same MSP-over-serial attack path as current target

**c) No Authentication/Encryption on Video**
- Digital video stream has no authentication — anyone with an HDZero receiver can view the feed
- Video injection possible if the encoding format is reverse-engineered
- Similar privacy concern as analog but with digital clarity

### Comparison to Current ELRS Target
**Lower priority.** HDZero is video-only (no control link), limiting the impact of attacks. Open source is a double-edged sword — easier to audit but also easier to harden. Smaller install base than DJI. The one-way nature means you can't inject commands through it (though the serial connection to the FC is still an MSP path).

---

## 3. Walksnail / Avatar Digital VTX Ecosystem

### Hardware/Firmware
- **VTX:** Walksnail Avatar HD (multiple revisions), Avatar HD Pro, Avatar HD Mini
- **Goggles:** Walksnail Avatar HD Goggles X, Avatar Goggles X4
- **Company:** Caddx (backed by Runcam)
- **SoC:** Sigmastar-based (SSC338Q or similar ARM SoC, runs Linux)

### Attack Surfaces

**a) ROOT EXPLOIT — Confirmed and Published**
- **Walksnail Avatar HD Goggles X root exploit is public** (intofpv.com, GitHub aj22r/walksnail-root)
- Rooting utility available — gives full Linux shell access on the goggles
- Underlying OS is Linux on ARM SoC — full rootfs access once exploited
- **This is an active, proven entry point**

**b) Firmware Update Mechanism**
- Firmware updates distributed via USB (goggle) and potentially OTA (VTX)
- D3VL/Avatar-Firmware-Updates GitHub repo tracks firmware releases — suggests the update format has been reverse-engineered
- If firmware signing is weak or absent, malicious firmware flashing is possible

**c) FCC Unlock / Range Hack**
- Community "range hack" and FCC unlock exist — modify TX power limits
- First Quadcopter documented the process — indicates firmware-level modifications are tractable
- Implies the firmware protection/signing is bypassable

**d) Bidirectional Link**
- Unlike HDZero, Walksnail Avatar has a bidirectional link (video down, telemetry/config up)
- MSP passthrough for Betaflight OSD
- Bidirectional = more attack surface than one-way HDZero

### Comparison to Current ELRS Target
**HIGH PRIORITY.** Walksnail runs Linux on an ARM SoC with a confirmed root exploit. This is far richer than the bare-metal SX1280/STM32 of ELRS. The Sigmastar SoC likely has WiFi/networking capabilities. The bidirectional link plus Linux OS means remote code execution may be achievable from the RF path. Growing market share as DJI alternative. Much less security research published than DJI.

---

## 4. Radio Control Links

### 4a. TBS Crossfire (900 MHz)

**Protocol:** Proprietary, uses CRSF serial protocol to FC (same protocol ELRS adopted)
**Encryption:** Proprietary — partially reverse-engineered by g3gg0.de
**Key findings from g3gg0 reverse engineering:**
- Air link protocol analyzed and partially documented
- Uses frequency hopping on 868/915 MHz ISM band
- Binding process and session key exchange examined
- CRSF protocol itself has no encryption (wire protocol between TX module and FC)
- **Since ELRS forked from Crossfire's CRSF protocol, many wire-level attacks transfer directly**

**Attack surfaces:**
- RF link interception/replay (if session key derivation is weak)
- CRSF serial injection (identical to our existing CRSF attack path from PR #64)
- TBS Agent/Lua script update mechanism
- Crossfire TX/RX firmware update over CRSF

**Comparison:** The CRSF wire protocol attack from PR #64 applies directly. The RF-level security is proprietary and less studied than ELRS — could be stronger or weaker.

### 4b. TBS Tracer (2.4 GHz)

**Protocol:** Proprietary, also uses CRSF serial protocol
**Key details:**
- 2.4 GHz version of Crossfire concept
- Same CRSF protocol stack for FC communication
- Shares firmware update infrastructure with Crossfire
- TBS manual documents the CRSF protocol structure

**Attack surfaces:** Same as Crossfire — our CRSF injection work transfers directly.

### 4c. FrSky ACCST / ACCESS

**ACCST (Advanced Continuous Channel Shifting Technology):**
- 2.4 GHz protocol, frequency-hopping spread spectrum
- Used in older FrSky gear (X-series, XM, etc.)
- **No published CVEs** but protocol has been analyzed in academic papers
- Binding phrase mechanism — similar concept to ELRS but proprietary implementation

**ACCESS (Advanced Communication Control, Elevated Spread Spectrum):**
- Newer FrSky protocol, mandatory firmware lock-in
- Community backlash due to forced obsolescence (cannot mix ACCST/ACCESS gear)
- Proprietary and locked down — **less community research** but also less community auditing
- FrSky's proprietary approach means fewer eyes on security

**Attack surfaces:**
- ACCST frequency-hopping predictability
- ACCESS binding/pairing mechanism
- S.BUS/S.PORT serial protocol (FC wire protocol — similar attack surface to CRSF)
- FrSky firmware update mechanism

**Comparison:** FrSky is a **declining ecosystem** in competitive FPV (ELRS/Crossfire took market share). Lower priority unless targeting legacy fleets.

### 4d. Spektrum DSM / DSM2 / DSMX

**The "Icarus" Attack — Full Hijack Demonstrated (2016)**

This is the **most completely broken** consumer RC protocol:
- Jonathan Andersson (Trend Micro) demonstrated "Icarus" at PacSec 2016
- SDR-based tool that can **commandeer any DSMX drone mid-flight**
- Exploits weaknesses in the DSMX frequency-hopping spread spectrum:
  - Predictable hopping sequence derivable from intercepted frames
  - Weak authentication — transmitter identity can be spoofed
  - Once hopping pattern is learned, attacker can inject commands as the legitimate transmitter
- **Works against DSM2 and DSMX** (DSM2 is trivially broken; DSMX is harder but still feasible)
- Demonstrated live at conference — full mid-flight takeover

**Current status:**
- Spektrum has not fundamentally redesigned the protocol since 2016
- DSMX remains in wide use (Spektrum radios are popular in North America, especially with BNF/RTF models)
- Academic paper: "Security Analysis of Drone Communication Protocols" (Springer, 2018) covers DSMx analysis in depth

**Attack surfaces:**
- RF-level hijack (proven with Icarus)
- Binding/pairing theft
- Telemetry interception (DSMX bidirectional telemetry)

**Comparison:** DSMX is the **easiest proven RF takeover** of any major protocol. If the goal is demonstrating drone hijack capability against widely-deployed hardware, DSMX is the lowest-hanging fruit. However, Spektrum is less common in custom FPV builds (more common in BNF/RTF).

---

## 5. Flight Controller Firmware

### 5a. ArduPilot / ArduPlane / ArduCopter

**The richest FC attack surface by far:**

**CVE-2026-38971: Out-of-Bounds Read (CVSS 9.1)**
- Critical vulnerability in ArduPlane
- Out-of-bounds read — potentially exploitable for information disclosure or crash
- Added to OWASP Drone Security Cheat Sheet

**MAVLink Protocol — Unauthenticated by Default:**
- ArduPilot uses MAVLink for all communication (GCS, telemetry, companion computers)
- **MAVLink v1 has no authentication whatsoever** — any device on the link can send commands
- MAVLink v2 added message signing, but **it's optional and rarely enabled in practice**
- Attack demonstrated: "Control-Aware Manipulation of ArduPilot via Legitimate MAVLink Commands" (arXiv 2606.22289) — validated on real hardware
- Commands include: change waypoints, arm/disarm, trigger RTL/land, modify flight mode, geofence manipulation

**ArduPilot Security Page (official):**
- ArduPilot has a formal security landing page (ardupilot.org/dev/docs/security-landing-page.html)
- Acknowledges the attack surface and documents security model
- This is **unique** among open-source FC firmware — they take it seriously enough to have a security page

**Reverse Engineering Research:**
- "Reverse Engineering and Control-Aware Security Analysis of the ArduPilot UAV Framework" (arXiv 2512.01164)
- Systematic analysis of the entire ArduPilot attack surface
- Identified multiple control-flow manipulation paths through legitimate protocol features

**Voltage Fault Injection:**
- "Glitch in the Sky: Exploiting Voltage Fault Injection in UAV Flight Controllers" (arXiv 2604.16699)
- Hardware-level attack — glitch the MCU to bypass firmware protections
- Applies to Pixhawk and other ArduPilot hardware platforms

**Attack surfaces:**
- MAVLink command injection (WiFi telemetry, serial, USB)
- Companion computer exploitation (Raspberry Pi running MAVProxy/DroneKit)
- GCS software chain (Mission Planner, QGroundControl)
- Firmware update (DFU over USB, sometimes over MAVLink)
- Parameter manipulation (thousands of tunable parameters, many safety-critical)
- Geofence/rally point manipulation
- Lua scripting engine (ArduPilot supports Lua — potential for persistent implants)

**Comparison to Betaflight:** ArduPilot is **dramatically more complex and capable** than Betaflight, with a correspondingly larger attack surface. It runs on more powerful hardware (Pixhawk/STM32F7/H7 series), supports companion computers, has networking stacks, and uses the MAVLink protocol which is far richer than MSP. If Betaflight is a knife, ArduPilot is a Swiss Army factory.

### 5b. iNav

**Firmware:** Open-source, Betaflight fork optimized for GPS navigation
**Protocol:** MSP (same as Betaflight) + GPS/compass integration
**Key differences from Betaflight:**
- Built-in GPS navigation, waypoint missions, RTH (Return to Home)
- Position hold, altitude hold, autonomous flight modes
- LTM (Lightweight Telemetry) protocol for long-range telemetry

**Attack surfaces:**
- Everything from Betaflight MSP attacks applies directly
- **GPS spoofing** — iNav relies heavily on GPS for navigation. Spoofed GPS coordinates can redirect the aircraft
- Waypoint mission manipulation via MSP
- RTH position manipulation (change "home" location)
- LTM telemetry interception (less common, but another data stream)

**Comparison:** iNav adds GPS-dependent attack surfaces on top of the Betaflight base we already understand. If the target is a long-range fixed-wing or GPS-equipped quad running iNav, GPS spoofing becomes a primary vector.

### 5c. KISS FC / FlightOne FalcoX

**KISS (Keep It Super Simple):**
- Closed-source, proprietary firmware
- Simpler protocol than MSP (KISS serial protocol)
- Smaller install base, declining
- Minimal telemetry, minimal configurability
- **Very small attack surface** — there's just not much to attack

**FlightOne FalcoX:**
- Closed-source, proprietary
- FalcoX uses its own configuration protocol (BOLT protocol)
- STM32-based (similar hardware to Betaflight targets)
- Small community, minimal security research
- WiFi-based configurator on some models — potential attack surface

**Comparison:** Both are niche, declining ecosystems with small attack surfaces. Low priority.

---

## 6. Analog VTX Systems (5.8 GHz)

### Hardware
- Commodity 5.8 GHz video transmitters (TBS Unify, Rush Tank, Foxeer Reaper, etc.)
- Analog goggles (Fatshark HDO2/HDO3 analog mode, Skyzone, etc.)

### Attack Surfaces
- **Zero encryption, zero authentication** — analog FM video on known channels
- Anyone with a 5.8 GHz receiver can view the pilot's video feed
- Video jamming is trivial (overpower with nearby transmitter)
- No digital command path — video only, no control injection possible through VTX
- SmartAudio / IRC Tramp serial protocols for VTX configuration (same attack vector as current TinyHawk SmartAudio finding)

### Comparison
Analog VTX is **trivially interceptable** for surveillance but offers no command injection path. SmartAudio/Tramp attacks transfer from existing research. Low priority for new research — we already covered SmartAudio.

---

## 7. GPS / Compass Spoofing

### State of Research
This is a **mature, well-funded research area** with significant recent publications:

- **"Tractor Beam: Safe-hijacking of Consumer Drones with Adaptive GPS Spoofing"** (ACM TOPS, Vol 22, No 2) — demonstrated safe, gradual GPS spoofing that redirects drones without triggering failsafes
- **2026 SDR-based GPS spoofing detection research** (Nature Scientific Reports, 2026) — shows both the attack and detection methods using SDR
- **Ukraine/Russia conflict data** — extensive real-world GPS spoofing of drones in combat (TechRxiv 2026)
- **AI-aware GPS spoofing** (arXiv 2506.08445) — attacks that defeat AI-based obstacle avoidance alongside GPS

### Attack Surfaces
- GPS L1/L2 signal spoofing with SDR (HackRF + GPS-SDR-SIM)
- Compass/magnetometer spoofing with electromagnetic interference
- GNSS jamming (simpler, just denial-of-service)
- Targeting: any drone running iNav, ArduPilot, DJI, or Betaflight with GPS rescue enabled

### Relevance to FPV
- FPV drones increasingly use GPS (GPS Rescue in Betaflight, full nav in iNav/ArduPilot)
- DJI FPV drones rely heavily on GPS for geo-fencing, RTH, and position hold
- GPS spoofing is **cross-platform** — works against any GPS-equipped drone regardless of FC firmware
- SDR tools (HackRF) are commodity hardware (~$300)

### Comparison
GPS spoofing is a **platform-agnostic attack** that works against the widest range of targets. Well-researched with proven tools, but requires SDR hardware. High impact against DJI/ArduPilot/iNav targets that depend on GPS for safety features.

---

## 8. Goggle Firmware Attack Surfaces

### DJI Goggles (V1/V2/Integra/2/3)
- **Root access proven** (fpv.wtf/Margerine exploit on V1/V2)
- Android/Linux-based — full OS once rooted
- WTFOS package manager for community modifications
- USB, WiFi, and HDMI input interfaces
- O3/O4 goggles (Goggles 2/3) — less community root work published but likely similar architecture

### Walksnail Avatar Goggles X
- **Root exploit published** (aj22r/walksnail-root on GitHub)
- Linux on ARM SoC
- USB and WiFi interfaces
- Growing target as market share increases

### HDZero Goggles
- Digiview-based, less powerful platform
- No published root exploits
- Smaller attack surface than DJI/Walksnail

### Fatshark (Analog/HDZero)
- Simpler firmware (not Linux-based for analog)
- HDO3 with HDZero module — module is the attack surface, not the goggle

---

## PRIORITIZED TARGET SHORTLIST

### Tier 1 — Highest Priority (pursue next)

| # | Target | Why |
|---|--------|-----|
| **1** | **Spektrum DSMX RF Hijack** | Only major protocol with a **fully proven mid-flight takeover** (Icarus, 2016). Spektrum never fundamentally fixed it. SDR-based attack, commodity hardware. Demonstrates the gold standard of drone hijack. |
| **2** | **ArduPilot / MAVLink Command Injection** | Unauthenticated MAVLink v1 is the norm. CVE-2026-38971 (CVSS 9.1) is fresh. Extensive published research base to build on. Huge target population (commercial/industrial drones, autonomous vehicles). |
| **3** | **Walksnail Avatar (Linux SoC exploitation)** | Confirmed root exploit, Linux on ARM, bidirectional link, growing market share, minimal security research. Richest embedded Linux target in FPV outside DJI. |

### Tier 2 — High Value, Higher Difficulty

| # | Target | Why |
|---|--------|-----|
| **4** | **DJI DroneID Sniffing + Enhanced WiFi Hijack** | DroneID interception is proven and impactful (tracking/de-anonymization). Enhanced WiFi hijack proven on specific models. 2026 CVEs add fresh material. But: DJI's proprietary stack is hard to crack deeper. |
| **5** | **GPS Spoofing (cross-platform)** | Platform-agnostic, proven tools, high impact on GPS-dependent drones. Mature research area means tooling exists. Best paired with an iNav or ArduPilot target. |
| **6** | **iNav GPS Navigation Attacks** | Betaflight MSP attacks transfer directly, PLUS GPS spoofing/waypoint manipulation. Natural extension of existing work for the GPS-equipped drone segment. |

### Tier 3 — Lower Priority

| # | Target | Why |
|---|--------|-----|
| **7** | **TBS Crossfire/Tracer RF Link** | CRSF wire-protocol attacks already done (PR #64). RF-layer is proprietary, partially reverse-engineered. Worthwhile but incremental over existing ELRS/CRSF work. |
| **8** | **HDZero Open-Source VTX Audit** | Source is public — audit could yield findings. But it's video-only with minimal command surface. |
| **9** | **FrSky ACCST/ACCESS** | Declining ecosystem, proprietary, less community tooling. Only worthwhile if targeting a specific legacy fleet. |
| **10** | **DJI OcuSync Core Protocol RE** | Massive payoff if cracked, but likely requires substantial RE resources. Long-term moonshot. |

---

## RECOMMENDED NEXT STEPS

1. **DSMX Icarus reproduction:** Acquire a Spektrum DSMX transmitter/receiver pair and SDR. Reproduce the Icarus-class attack against a modern DSMX setup. This is the fastest path to a demonstrated RF takeover because the vulnerability is proven and public.

2. **ArduPilot MAVLink injection toolkit:** Build on the published research (arXiv 2512.01164, 2606.22289) to create a MAVLink injection suite. Can start with simulation (SITL) requiring zero hardware. Huge target population.

3. **Walksnail Avatar firmware RE:** Use the existing root exploit to extract and analyze the Walksnail firmware. Map the attack surface of the Linux environment, network services, and firmware update mechanism.

4. **DJI DroneID receiver:** Build an SDR-based DroneID receiver using published research. Demonstrate real-time DJI drone tracking. Commodity hardware only.

5. **GPS spoofing PoC:** Build on GPS-SDR-SIM with HackRF to demonstrate GPS spoofing against Betaflight GPS Rescue and iNav RTH. Cross-platform applicability makes this high-value.

---

## Sources

- Schiller et al., "Drone Security and the Mysterious Case of DJI's DroneID" (NDSS 2023)
- Bender, "DJI drone IDs are not encrypted" (arXiv 2207.10795)
- "Behind The Wings: The Case of Reverse Engineering and Drone Hijacking in DJI Enhanced Wi-Fi Protocol" (arXiv 2309.05913)
- CVE-2026-1743, CVE-2026-78255, CVE-2026-77812 (DJI, NVD)
- CVE-2026-38971 (ArduPilot/ArduPlane, CVSS 9.1, NVD)
- fpv-wtf/margerine, fpv-wtf/wtfos (DJI goggle root)
- aj22r/walksnail-root (Walksnail goggle root)
- hd-zero/hdzero-vtx (HDZero open-source VTX firmware)
- g3gg0.de Crossfire reverse engineering
- Andersson, "Icarus: SDR-based hijack of DSMX drones" (PacSec 2016, DSIAC)
- "Reverse Engineering and Control-Aware Security Analysis of ArduPilot" (arXiv 2512.01164)
- "Control-Aware Manipulation of ArduPilot via Legitimate MAVLink Commands" (arXiv 2606.22289)
- "Glitch in the Sky: Exploiting Voltage Fault Injection in UAV Flight Controllers" (arXiv 2604.16699)
- "Tractor Beam: Safe-hijacking of Consumer Drones with Adaptive GPS Spoofing" (ACM TOPS)
- OWASP Drone Security Cheat Sheet
- "Security Analysis of Drone Communication Protocols" (Springer, 2018)
