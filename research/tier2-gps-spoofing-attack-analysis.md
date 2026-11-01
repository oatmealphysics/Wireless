# Tier 2.2: GPS Spoofing — Cross-Platform Drone Attack Analysis

**Date:** 2026-11-01
**Status:** Research complete — signal generation tooling exists (open source), requires SDR hardware for live testing
**Context:** Authorized security research

---

## Executive Summary

GPS spoofing is a **cross-platform attack** that affects virtually every consumer and commercial drone regardless of manufacturer or flight controller. Civilian GPS (L1 C/A) signals are unencrypted and unauthenticated by design, allowing anyone with an SDR (~$200–320) and open-source software (GPS-SDR-SIM) to generate fake GPS signals. Published research demonstrates **safe hijacking** ("Tractor Beam") that can steer drones to attacker-chosen locations without crashing them, validated on DJI Phantom 3/4, Parrot Bebop 2, and 3DR Solo. The attack is effective against all platforms tested in this project: ArduPilot, Betaflight (GPS Rescue), DJI, iNav, and any other GPS-dependent autopilot.

---

## 1. GPS/GNSS Fundamentals

### 1.1 Civilian GPS Signal Structure

| Property | Detail |
|----------|--------|
| **Frequency** | L1: 1575.42 MHz (primary civilian), L2: 1227.60 MHz, L5: 1176.45 MHz |
| **Signal type** | CDMA spread spectrum, C/A code (coarse acquisition) |
| **Code rate** | 1.023 Mchips/s (L1 C/A) |
| **Data rate** | 50 bps navigation message |
| **Signal power** | -130 dBm at ground level (extremely weak) |
| **Authentication** | **None** (civilian L1 C/A) |
| **Encryption** | **None** (civilian signals) — military P(Y) code is encrypted |

### 1.2 Why GPS Is Vulnerable

1. **No authentication** — Any signal matching the L1 C/A code structure is accepted
2. **Extremely weak** — Satellite signals arrive at -130 dBm; a few milliwatts of spoofed signal easily overwhelms them
3. **Predictable** — Satellite positions, PRN codes, and navigation data are public (broadcast ephemeris)
4. **Single frequency** — Most consumer GPS uses L1 only, no cross-frequency consistency check
5. **No integrity** — Receiver has no way to verify signal origin (unlike military signals)

### 1.3 GNSS Constellations Affected

| Constellation | Civilian Freq | Auth | Status |
|---------------|--------------|------|--------|
| **GPS** (US) | L1 1575.42 MHz | None | Vulnerable |
| **GLONASS** (Russia) | L1 1602 MHz (FDMA) | None | Vulnerable (harder to spoof due to FDMA) |
| **Galileo** (EU) | E1 1575.42 MHz | OSNMA (optional) | Partially defended (OSNMA adoption growing) |
| **BeiDou** (China) | B1 1561.098 MHz | None | Vulnerable |

Most consumer drones use GPS only, or GPS+GLONASS. Multi-constellation receivers are more resilient but can be defeated with concurrent spoofing + jamming.

---

## 2. Attack Methods

### 2.1 Meaconing (Signal Replay)

| Property | Detail |
|----------|--------|
| **Complexity** | Low |
| **Hardware** | SDR (receive) + amplifier + antenna (retransmit) |
| **Effect** | Delays real GPS signals, causing position/time offset |
| **Limitations** | Cannot control target position precisely |
| **Detection** | Relatively easy (time anomalies) |

Record real GPS signals, amplify, retransmit with delay. Simple but imprecise.

### 2.2 Non-Coherent Spoofing (Brute Force)

| Property | Detail |
|----------|--------|
| **Complexity** | Medium |
| **Hardware** | SDR (HackRF ~$320) + GPS-SDR-SIM |
| **Effect** | Overpower real signals with fake position/time |
| **Limitations** | Causes receiver jump (detectable), may trigger failsafe |
| **Range** | 100m+ (depends on power) |

Generate synthetic GPS signals for a fake location and transmit at higher power than real satellites. The receiver locks onto the stronger fake signal. This causes an abrupt position jump that sophisticated receivers or flight controllers may detect.

### 2.3 Coherent Spoofing (Seamless Takeover)

| Property | Detail |
|----------|--------|
| **Complexity** | High |
| **Hardware** | SDR + GPS receiver (reference) + precise clock |
| **Effect** | Seamless transition from real to fake signals |
| **Limitations** | Requires tracking real signal first |
| **Cost** | ~$1,500 DIY, ~$50K commercial |

Match the real GPS signal's time, code phase, and Doppler, then gradually increase spoofed signal power while shifting the reported position. The receiver transitions without detecting the switch. This is the most sophisticated and hardest to detect form of spoofing.

### 2.4 Tractor Beam (Safe Hijacking)

| Property | Detail |
|----------|--------|
| **Paper** | "Tractor Beam" (ACM TOPS 2019, KAIST) |
| **Complexity** | High |
| **Effect** | Steer drone to attacker-chosen destination without crash |
| **Tested drones** | DJI Phantom 3/4, Parrot Bebop 2, 3DR Solo |
| **Accuracy** | <3° angular error (Phantom 4), <20° (Phantom 3) |

**Method:**
1. Detect and identify the target drone (model determines strategy)
2. Classify the drone's GPS failsafe behavior (4 types identified)
3. Lock the drone's GPS receiver onto spoofed signal
4. Gradually shift reported position to steer drone along desired heading
5. Exploit failsafe/path-following logic to maintain controlled flight
6. Continuously update spoofed position to guide to destination

**Drone failsafe types:**
| Type | Behavior | Example |
|------|----------|---------|
| I | Returns to pre-loss GPS position on recovery | DJI Phantom 3/4 |
| II | Continues from current position on recovery | Parrot Bebop 2 |
| III | Follows mission waypoints on recovery | 3DR Solo (ArduPilot) |
| IV | Enters non-GPS hover permanently | Some custom builds |

---

## 3. Hardware & Software

### 3.1 SDR Hardware Options

| Device | Cost | TX Capable | Bandwidth | Notes |
|--------|------|-----------|-----------|-------|
| **HackRF One** | ~$320 | Yes | 20 MHz | Most common for GPS spoofing |
| **ADALM-Pluto** | ~$200 | Yes | 20 MHz | Lower cost alternative |
| **LimeSDR** | ~$315 | Yes | 61.44 MHz | Higher bandwidth |
| **bladeRF** | ~$480 | Yes | 40 MHz | Higher quality |
| **USRP B200/B210** | ~$1500 | Yes | 56 MHz | Research grade |

**Additional equipment for extended range:**
| Item | Cost | Purpose |
|------|------|---------|
| RF amplifier (10W) | ~$100 | Extend spoofing range |
| Directional antenna | ~$50–200 | Focus signal on target |
| GNSS jammer | ~$150 | Suppress non-GPS constellations |

### 3.2 Software Tools

**GPS-SDR-SIM (osqzss/gps-sdr-sim, GitHub):**
- Generates GPS L1 C/A baseband signals as IQ samples
- Input: RINEX navigation file + target location (lat/lon/alt) or trajectory
- Output: IQ file playable on any TX-capable SDR
- Supports static position or dynamic trajectory
- 680+ forks on GitHub (extensive community)

**Usage pattern:**
```bash
# Download current GPS ephemeris (broadcast navigation data)
wget ftp://cddis.gsfc.nasa.gov/gnss/data/daily/YYYY/DDD/YYn/brdcDDD0.YYn.Z

# Generate spoofed signal for target location
./gps-sdr-sim -e brdc.nav -l 40.7128,-74.0060,30 -o gpssim.bin

# Transmit via HackRF
hackrf_transfer -t gpssim.bin -f 1575420000 -s 2600000 -a 1 -x 47
```

**Multi-GNSS spoofing tools:**
- GNSS-SDR (open source GNSS receiver, also used for signal analysis)
- Spirent / Rohde & Schwarz (commercial GNSS simulators, $50K+)

---

## 4. Drone-Specific Attack Scenarios

### 4.1 Against ArduPilot (Tier 1.2 target)

| Aspect | Detail |
|--------|--------|
| **GPS dependency** | Primary navigation sensor for Auto, Guided, RTL, Loiter modes |
| **EKF behavior** | EKF3 fuses GPS with IMU; gradual spoofing may bypass consistency checks |
| **Failsafe** | GPS glitch failsafe triggers land/RTL; coherent spoof avoids triggering |
| **Combined attack** | GPS spoof + EKF parameter manipulation (Tier 1.2) = devastating |

ArduPilot's EKF has GPS consistency checking (`EK3_GPS_CHECK`), but this can be disabled via parameter manipulation (see Tier 1.2 analysis). Combined GPS spoofing + EKF confusion eliminates both the primary navigation source and the consistency check.

### 4.2 Against Betaflight/ELRS (Current project target)

| Aspect | Detail |
|--------|--------|
| **GPS dependency** | GPS Rescue mode only (manual flight unaffected) |
| **Failsafe** | GPS Rescue triggered on RC link loss → spoofable |
| **Effect** | Drone flies to spoofed "home" location instead of actual home |
| **Combined attack** | ELRS link disruption + GPS spoof = redirect to attacker's location |

GPS Rescue is the one autonomous mode in Betaflight. If the ELRS control link is disrupted (per Tier 1.1 DSMX analysis or ELRS binding phrase attack), the drone activates GPS Rescue and flies toward the spoofed home point.

### 4.3 Against DJI (Tier 2.1 target)

| Aspect | Detail |
|--------|--------|
| **GPS dependency** | Primary navigation for all autonomous functions |
| **Multi-GNSS** | Many DJI drones use GPS+GLONASS (harder but not immune) |
| **Failsafe** | RTH on signal loss → GPS spoof controls RTH destination |
| **Visual positioning** | Downward cameras provide altitude/position assist (partial defense) |
| **Tractor Beam** | Validated on DJI Phantom 3/4 with <3°–20° angular error |

DJI's visual positioning system provides some defense at low altitude, but GPS remains the primary navigation sensor above ~10m.

### 4.4 Against iNav (Tier 2.3 target)

| Aspect | Detail |
|--------|--------|
| **GPS dependency** | Primary sensor for Navigation, RTH, Position Hold |
| **EKF behavior** | Similar to ArduPilot (GPS+IMU fusion) |
| **Failsafe** | GPS-dependent RTH on link loss |
| **Defense** | Some GPS sanity checking, but no anti-spoofing |

---

## 5. Defenses and Limitations

### 5.1 Current Drone Defenses

| Defense | Effectiveness | Notes |
|---------|--------------|-------|
| **Multi-GNSS** | Partial | Spoofing multiple constellations is harder but not impossible |
| **IMU consistency** | Partial | EKF checks GPS vs IMU; coherent spoof fools this |
| **Visual positioning** | Low altitude only | DJI uses downward cameras; not available on most FPV drones |
| **GPS glitch detection** | Basic | Detects sudden jumps; gradual spoofing bypasses |
| **Galileo OSNMA** | Growing | Authentication for Galileo signals; limited adoption so far |
| **Antenna arrays** | Effective | Direction-finding detects spoof angle; expensive, not on drones |

### 5.2 Fundamental Limitations of GPS Spoofing

| Limitation | Detail |
|------------|--------|
| **Legal** | GPS spoofing is illegal in most jurisdictions (FCC, ITU) |
| **Collateral** | All GPS receivers in range are affected, not just target |
| **Line of sight** | RF propagation; buildings/terrain attenuate signal |
| **Moving target** | Harder to maintain lock on fast-moving drone |
| **Multi-sensor** | Drones with optical flow, lidar, or barometer partially resist |

---

## 6. Offline Toolkit Architecture

### 6.1 What's Buildable Without Hardware

```
gps-spoof-toolkit/
├── ephemeris_fetch.py       # Download current GPS/GNSS ephemeris data
│   - Fetch RINEX navigation files from CDDIS/IGS
│   - Parse satellite positions and clock corrections
│   - Generate satellite visibility predictions for target location
│
├── trajectory_gen.py        # Generate spoofing trajectories
│   - Static position (hold at fake location)
│   - Linear path (move from A to B)
│   - Circular orbit (loiter pattern)
│   - RTH redirect (guide drone to attacker-chosen "home")
│   - Gradual drift (slow, undetectable position shift)
│   - Output: lat/lon/alt/time series for GPS-SDR-SIM input
│
├── signal_analyzer.py       # Analyze GPS signal characteristics
│   - Calculate expected signal power levels
│   - Compute Doppler shifts for target scenario
│   - Estimate required spoofing power budget
│   - Model multi-constellation interference
│
└── defense_analyzer.py      # Evaluate target drone defenses
    - Flight controller identification (ArduPilot/Betaflight/iNav/DJI)
    - GPS configuration analysis (constellations, SBAS, consistency checks)
    - Failsafe behavior prediction
    - Vulnerability scoring per drone type
```

### 6.2 What Requires Hardware

| Component | Hardware | Cost |
|-----------|----------|------|
| Signal generation + TX | HackRF One + antenna | ~$350 |
| Extended range | + RF amplifier + directional antenna | ~$500 |
| Multi-GNSS spoofing | + GNSS jammer (suppress GLONASS/BeiDou) | ~$650 |
| Coherent spoofing | + GPS reference receiver + precise clock | ~$1,500 |
| Target drone | Any GPS-equipped drone (all vulnerable) | Varies |

---

## 7. Comparison to Other Attack Surfaces

| Dimension | GPS Spoofing | ELRS/DSMX (RF) | MAVLink (protocol) | DJI BT/WiFi |
|-----------|-------------|----------------|--------------------|--------------|
| **Cross-platform** | **Yes — all drones** | Protocol-specific | ArduPilot/PX4 | DJI only |
| **Hardware cost** | $200–1,500 | $0–300 | $0 (WiFi) | $0–15 |
| **Range** | 100m–30km | 30m–1km | WiFi range | 30–100m |
| **Stealth** | High (coherent) | Low | Medium | Low |
| **Collateral** | **All GPS receivers in area** | Target only | Target only | Target only |
| **Legal risk** | **Very high** | Varies | Low (WiFi) | Low |
| **Defense maturity** | Low (civilian) | None | Optional signing | None |

**Key advantage of GPS spoofing:** It's the only attack surface that works against every drone platform universally, regardless of protocol or manufacturer. Combined with protocol-specific attacks (EKF parameter corruption, link disruption), it becomes a devastating multi-layer attack.

---

## 8. Risk Assessment

| Factor | Rating | Notes |
|--------|--------|-------|
| **Technical feasibility** | **HIGH** | Open-source tools, commodity SDR |
| **Cross-platform** | **UNIVERSAL** | Affects all GPS-equipped drones |
| **Hardware cost** | **LOW–MEDIUM** | $200–1,500 depending on sophistication |
| **Novelty** | **LOW** | Well-studied since 2012; still effective |
| **Impact** | **CRITICAL** | Position hijack, RTH redirect, geofence bypass |
| **Remediation** | **VERY POOR** | Fundamental GPS design flaw; OSNMA decades away |
| **Legal risk** | **VERY HIGH** | Illegal in most jurisdictions |
| **Collateral damage** | **HIGH** | Affects all GPS receivers in area |

---

## Sources

- "Tractor Beam: Safe-Hijacking of Consumer Drones with Adaptive GPS Spoofing" (ACM TOPS 2019, KAIST)
- "On GPS Spoofing of Aerial Platforms: A Review" (PMC 2021)
- osqzss/gps-sdr-sim (GitHub) — GPS L1 C/A signal generator
- "Is Your Timespace Safe? Time and Position Spoofing Opensourcely" (Black Hat EU 2015)
- "GNSS Spoofing Scenarios with SDRs" (GPSPatron)
- "SemperFi: A Spoofer Eliminating GPS Receiver for UAVs" (arXiv 2105.01860)
- "Protecting UAV Navigation from GNSS Jamming and Spoofing" (EverythingRF)
