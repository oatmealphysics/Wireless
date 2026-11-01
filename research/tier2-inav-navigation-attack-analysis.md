# Tier 2.3: iNav Navigation Attack Analysis

**Date:** 2026-11-01
**Status:** Research complete — attack tooling buildable offline, live testing requires target hardware
**Context:** Authorized security research

---

## Executive Summary

iNav is a **navigation-focused** fork of Betaflight, purpose-built for autonomous flight: waypoint missions, Return-to-Home (RTH), position hold, and GCS-directed navigation. This navigation emphasis creates a **significantly larger attack surface** than stock Betaflight, because iNav exposes dozens of MSP commands that directly control where the aircraft flies — waypoint injection, home position override, position estimator weight manipulation, failsafe parameter corruption, safehome redirection, and RTH configuration tampering — all **without any authentication**. The MSP protocol has zero access control: any device on any configured serial port (USB, UART, or telemetry link) can issue any command, including safety-critical navigation changes while armed and in flight.

**Key findings:**
- **8 distinct attack vectors** identified, all exploitable via unauthenticated MSP
- **MSP_SET_WP** (ID 209) can inject/overwrite waypoints including home (WP#0) and follow-me target (WP#255) while armed
- **MSP_SET_POSITION_ESTIMATION_CONFIG** (ID 17) can corrupt position estimator fusion weights, causing controlled navigation drift
- **MSP_SET_FAILSAFE_CONFIG** (ID 76) can change failsafe behavior (e.g., DROP instead of RTH)
- **MSP_SET_RTH_AND_LAND_CONFIG** (ID 22) can alter RTH altitude, abort thresholds, and landing behavior
- **MSP_SET_NAV_POSHOLD** (ID 13) can change maximum speeds and bank angles during autonomous flight
- Combined with ELRS/CRSF link compromise or GPS spoofing, these create multi-layer attacks
- Glitch protection was **removed** in iNav 5.0, reducing resilience to GPS spoofing
- Position estimator uses configurable fusion weights with no runtime integrity checks

---

## 1. iNav Architecture Overview

### 1.1 What Makes iNav Different from Betaflight

| Feature | Betaflight | iNav |
|---------|-----------|------|
| **Primary purpose** | FPV racing, acrobatic flight | Autonomous navigation |
| **GPS modes** | GPS Rescue only (emergency) | RTH, POSHOLD, WP missions, Follow-Me, GCS NAV |
| **Position estimator** | Minimal (GPS Rescue only) | Full 3-axis estimator with sensor fusion |
| **Waypoint missions** | None | Up to 60 waypoints, 8 waypoint types |
| **Failsafe** | Basic (land/drop) | Multi-stage with RTH, emergency landing, configurable procedures |
| **Safehomes** | None | Up to 8 alternative RTH points |
| **Navigation PID loops** | None | ALT, VEL, POS, POSR controllers |
| **MSP navigation commands** | ~2 (GPS Rescue config) | 20+ navigation-specific MSP messages |
| **Geofence** | None | Configurable max distance/altitude |
| **Attack surface** | **Small** (manual flight focus) | **Very large** (autonomous control) |

### 1.2 Supported Hardware

iNav runs on the same STM32-based flight controllers as Betaflight:
- **STM32F405/F411/F722/H743** — same silicon, different firmware
- **GPS modules** — uBlox M8N/M9N/M10 (most common), BeiDou/GLONASS capable
- **Barometer** — BMP280/BMP388/MS5611 (altitude fusion)
- **Magnetometer** — HMC5883L/QMC5883L/IST8310 (heading)
- **RC links** — CRSF/ELRS, SBUS, IBUS, PPM (all supported)
- **Telemetry** — LTM, MAVLink, CRSF, Smartport, IBUS

### 1.3 Position Estimator Architecture

iNav's position estimator is the core of its navigation — and a primary attack target.

**Method:** Accelerometer measurements are rotated from body frame to local NEU (North-East-Up) coordinates, integrated to velocity, then integrated to position. External references (GPS, barometer, sonar) correct drift.

**Fusion weights (all configurable via MSP/CLI):**

| Parameter | Source | Axis | Effect |
|-----------|--------|------|--------|
| `inav_w_z_baro_p` | Barometer | Z (altitude) | Position correction weight |
| `inav_w_z_baro_v` | Barometer | Z | Velocity correction weight |
| `inav_w_z_gps_p` | GPS | Z | Altitude position weight (experimental) |
| `inav_w_z_gps_v` | GPS | Z | Altitude velocity weight |
| `inav_w_xy_gps_p` | GPS | XY (horizontal) | Position correction weight |
| `inav_w_xy_gps_v` | GPS | XY | Velocity correction weight |
| `inav_w_z_sonar_p` | Sonar | Z | Near-ground altitude weight |
| `inav_w_z_sonar_v` | Sonar | Z | Near-ground velocity weight |
| `inav_w_xy_dr_p` | Dead reckoning | XY | DR position weight |
| `inav_w_xy_dr_v` | Dead reckoning | XY | DR velocity weight |
| `inav_w_z_res_v` | Decay | Z | Velocity decay rate |
| `inav_w_xy_res_v` | Decay | XY | Velocity decay rate |

**Critical parameters:**
| Parameter | Effect |
|-----------|--------|
| `inav_max_eph_epv` | Max position error before invalidation |
| `inav_gps_delay_ms` | GPS latency compensation (~200ms typical) |
| `inav_enable_dead_reckoning` | Whether to use DR during GPS loss |
| `inav_baro_epv` | Error assigned to barometer readings |

**Vulnerability:** All fusion weights are settable via `MSP_SET_POSITION_ESTIMATION_CONFIG` (ID 17) with no authentication. An attacker can:
- Zero out GPS weights → position estimator ignores GPS → drift on accelerometer alone
- Maximize GPS weights → GPS spoofing becomes immediately effective with no IMU cross-check
- Zero out barometer weights → altitude estimation relies solely on GPS (spoofable)
- Manipulate dead reckoning weights → controlled drift during GPS outages

---

## 2. MSP Attack Surface

### 2.1 MSP Protocol — Zero Authentication

MSP (MultiWii Serial Protocol) is iNav's command interface. It has **no authentication, no encryption, no access control**. Any device that can send bytes on a configured MSP port can issue any command.

**MSP frame format (v2):**
```
$X <dir> <flag> <cmd16> <size16> <payload> <crc8>
```
- `dir`: `<` (request to FC), `>` (response from FC), `!` (error)
- `flag`: 0 normally, bit 0 = "no reply" for latency-sensitive commands
- `cmd16`: 16-bit command ID
- `size16`: payload length
- `crc8`: CRC8/DVB-S2 over flag+cmd+size+payload

**MSPv1 frame format (legacy, still accepted):**
```
$M <dir> <size> <cmd> <payload> <checksum>
```
- `checksum`: XOR of size + cmd + payload bytes

### 2.2 Navigation-Critical MSP Commands

| ID | Command | Direction | Risk | Effect |
|----|---------|-----------|------|--------|
| **209** | `MSP_SET_WP` | → FC | **CRITICAL** | Write any waypoint (including home WP#0, follow-me WP#255) |
| **118** | `MSP_WP` | ← FC | RECON | Read waypoint data |
| **17** | `MSP_SET_POSITION_ESTIMATION_CONFIG` | → FC | **CRITICAL** | Change all position estimator fusion weights |
| **76** | `MSP_SET_FAILSAFE_CONFIG` | → FC | **CRITICAL** | Change failsafe procedure (RTH→DROP), delays, thresholds |
| **22** | `MSP_SET_RTH_AND_LAND_CONFIG` | → FC | **CRITICAL** | Change RTH altitude, abort threshold, landing speeds |
| **13** | `MSP_SET_NAV_POSHOLD` | → FC | **HIGH** | Change max speeds, bank angles, hover throttle |
| **24** | `MSP_SET_FW_CONFIG` | → FC | **HIGH** | Change fixed-wing cruise/min/max throttle, bank/climb angles |
| **215** | `MSP_SET_NAV_CONFIG` | → FC | **HIGH** | Change WP radius, safe distance, max altitude, max speed |
| **211** | `MSP_SET_HEAD` | → FC | **MEDIUM** | Override heading |
| **35** | `MSP_SET_MODE_RANGE` | → FC | **HIGH** | Remap flight mode activation channels |
| **37** | `MSP_SET_FEATURE` | → FC | **HIGH** | Enable/disable features (32-bit mask) |
| **15** | `MSP_SET_CALIBRATION_DATA` | → FC | **HIGH** | Corrupt accelerometer/magnetometer calibration |
| **68** | `MSP_REBOOT` | → FC | **MEDIUM** | Reboot FC (fails if armed) |
| **72** | `MSP_DATAFLASH_ERASE` | → FC | **LOW** | Erase blackbox data (evidence destruction) |
| **200** | `MSP_SET_RAW_RC` | → FC | **CRITICAL** | Override RC channels directly |

### 2.3 MSP_SET_WP Payload Format

21-byte little-endian payload for waypoint injection:

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | uint8 | wp_no | 0=home, 1-60=mission, 255=follow-me |
| 1 | uint8 | action | Waypoint type (see below) |
| 2 | int32 | lat | Degrees × 10^7 |
| 6 | int32 | lon | Degrees × 10^7 |
| 10 | int32 | altitude | Centimeters |
| 14 | int16 | p1 | Action-dependent (speed, time, etc.) |
| 16 | int16 | p2 | Action-dependent |
| 18 | int16 | p3 | Action-dependent |
| 20 | uint8 | flag | 0=normal, 0xA5=last WP, 0x48=FlyByHome |

**Waypoint actions:**
| Action | Value | Effect |
|--------|-------|--------|
| WAYPOINT | 1 | Fly to position (p1=speed cm/s) |
| POSHOLD_UNLIM | 2 | Hold position indefinitely |
| POSHOLD_TIME | 3 | Hold for p1 seconds |
| RTH | 4 | Return to home and land |
| SET_POI | 5 | Set point of interest for heading |
| JUMP | 6 | Jump to WP p1, repeat p2 times |
| SET_HEAD | 7 | Set heading to p1 degrees |
| LAND | 8 | Land at position |

**Special waypoints:**
- **WP#0 (Home):** Can be overwritten while armed + GPS fix + GCS NAV asserted. Redirects RTH destination.
- **WP#255 (Follow-Me):** Sets target for POSHOLD+GCS NAV mode. P1 (1-359) updates heading. Requires continuous 5Hz+ updates.

---

## 3. Attack Vectors

### 3.1 Attack 1: Home Position Hijack (WP#0 Override)

**Prerequisites:** MSP access + target armed with GPS fix + GCS NAV mode active
**MSP command:** `MSP_SET_WP` with `wp_no=0`
**Effect:** RTH destination changes to attacker-chosen location

```
Attack flow:
1. Gain MSP access (USB, UART, or telemetry link)
2. Wait for target to arm with GPS fix
3. Assert GCS NAV mode via MSP_SET_RAW_RC (set appropriate AUX channel)
4. Send MSP_SET_WP: wp_no=0, action=1, lat=ATTACKER_LAT, lon=ATTACKER_LON
5. Trigger RTH (via failsafe or MSP_SET_RAW_RC on RTH channel)
6. Drone flies to attacker's chosen location
```

**Severity:** CRITICAL — Direct flight path hijack
**Detection:** OSD may show safehome diversion message, but home override has no alert

### 3.2 Attack 2: Mission Injection (WP#1-60 Override)

**Prerequisites:** MSP access + target disarmed or with GCS NAV
**MSP command:** `MSP_SET_WP` for each waypoint
**Effect:** Entire mission replaced with attacker's waypoints

```
Attack flow:
1. Upload malicious mission via repeated MSP_SET_WP (up to 60 waypoints)
2. Set final waypoint flag (0xA5) on last WP
3. Mission now routes drone through attacker-chosen path
4. Can include LAND action at attacker's desired recovery point
```

**Severity:** CRITICAL — Complete route hijack
**Constraint:** `nav_wp_safe_distance` (default 10000cm/100m) limits first WP distance from current position, but this is itself changeable via `MSP_SET_NAV_CONFIG`

### 3.3 Attack 3: Position Estimator Corruption

**Prerequisites:** MSP access (no arming/mode requirements)
**MSP command:** `MSP_SET_POSITION_ESTIMATION_CONFIG` (ID 17)
**Payload:** 12 bytes — five fusion weights (uint16, value × 100) + min_sats (uint8) + legacy flag (uint8)

| Sub-attack | Weight change | Effect |
|-----------|--------------|--------|
| **GPS blindness** | `w_xy_gps_p=0, w_xy_gps_v=0` | Estimator ignores GPS; position drifts on IMU alone |
| **GPS maximized** | `w_xy_gps_p=1000, w_xy_gps_v=1000` | GPS fully trusted; spoofing immediately effective |
| **Baro blindness** | `w_z_baro_p=0, w_z_baro_v=0` | Altitude from GPS only (spoofable) |
| **Sat threshold raise** | `min_sats=10` | GPS rejected unless 10+ sats (rare indoors/urban) |
| **Sat threshold lower** | `min_sats=5` | GPS accepted with poor constellation (less accurate) |

**Severity:** CRITICAL — Undermines all navigation accuracy
**Combined with GPS spoofing:** Maximizing GPS weights while zeroing baro/IMU weights makes GPS spoofing trivially effective — no cross-check remains

### 3.4 Attack 4: Failsafe Parameter Corruption

**Prerequisites:** MSP access (no arming/mode requirements)
**MSP command:** `MSP_SET_FAILSAFE_CONFIG` (ID 76)
**Payload:** 20 bytes

| Parameter | Field | Default | Malicious value | Effect |
|-----------|-------|---------|----------------|--------|
| `failsafe_delay` | uint8 (×0.1s) | 15 (1.5s) | 0 | Instant failsafe on any signal glitch |
| `failsafe_delay` | uint8 | 15 | 255 (25.5s) | Failsafe delayed 25s → drone flies uncontrolled |
| `failsafe_off_delay` | uint8 (×0.1s) | 200 (20s) | 0 | Immediate disarm on failsafe → crash |
| `failsafe_throttle` | uint16 | 1000 | 1000 | Throttle at minimum during failsafe → DROP |
| `failsafe_procedure` | uint8 | RTH (2) | DROP (0) | On signal loss: motors off, drone falls |
| `failsafe_stick_threshold` | uint16 | 30 | 1000 | Recovery impossible (stick threshold too high) |
| `failsafe_min_distance` | uint16 | 0 | 65535 | Min RTH distance = 655m; close RTH becomes DROP |

**Severity:** CRITICAL — Converts any signal loss into a crash
**Stealth:** Changes take effect immediately but are not saved to EEPROM until `save` — a power cycle reverts them, making forensic analysis difficult

### 3.5 Attack 5: RTH Configuration Tampering

**Prerequisites:** MSP access
**MSP command:** `MSP_SET_RTH_AND_LAND_CONFIG` (ID 22)
**Payload:** 21 bytes

| Parameter | Effect of manipulation |
|-----------|----------------------|
| `nav_rth_altitude` | Set to 0 → RTH at ground level (crashes into obstacles); Set to 50000 (500m) → climbs dangerously high |
| `nav_rth_abort_threshold` | Set to 0 → RTH aborts immediately on any anomaly |
| `nav_rth_climb_first` | Disable → drone flies direct line to home (obstacle collision) |
| `nav_rth_allow_landing` | Disable → drone hovers at home until battery dies |
| `nav_emerg_landing_speed` | Set to maximum → hard landing / crash |
| `min_rth_distance` | Set very high → close RTH triggers emergency landing instead |

**Severity:** HIGH — RTH becomes dangerous or non-functional

### 3.6 Attack 6: Safehome Poisoning

**Prerequisites:** CLI/MSP access while disarmed (safehome MSP2 commands exist in newer iNav)
**Effect:** RTH diverts to attacker-chosen "safehome" location

```
Attack flow:
1. Set safehome within 200m (safehome_max_distance) of target's usual launch point
2. Set safehome_usage_mode to RTH (both manual and failsafe RTH)
3. On next RTH, drone diverts to attacker's safehome instead of actual home
```

**Constraint:** Safehome must be within `safehome_max_distance` (default 200m) of arming position
**Severity:** HIGH — Stealthy RTH redirect (OSD shows "DIVERTING TO SAFEHOME" but pilot may not notice)

### 3.7 Attack 7: Follow-Me Hijack (WP#255)

**Prerequisites:** MSP access + armed + GPS fix + POSHOLD + GCS NAV
**MSP command:** `MSP_SET_WP` with `wp_no=255`
**Effect:** Drone follows attacker's position updates instead of legitimate GCS

```
Attack flow:
1. Assert POSHOLD + GCS NAV modes via MSP_SET_RAW_RC
2. Send WP#255 updates at 5Hz+ with attacker's chosen coordinates
3. Drone tracks attacker's "follow-me" position
4. P1 field (1-359) controls heading → drone faces attacker's direction
```

**Severity:** HIGH — Real-time flight path control
**Advantage over mission injection:** Dynamic, real-time steering without pre-planned route

### 3.8 Attack 8: Calibration Data Corruption

**Prerequisites:** MSP access
**MSP command:** `MSP_SET_CALIBRATION_DATA` (ID 15)
**Payload:** 26 bytes — accelerometer zeros/gains, magnetometer zeros/gains, optical flow scale

| Sub-attack | Effect |
|-----------|--------|
| **Accel zero corruption** | Drone thinks "level" is tilted → drifts in POSHOLD/RTH |
| **Accel gain corruption** | Incorrect acceleration measurement → position estimator errors |
| **Mag zero corruption** | Incorrect heading → flies wrong direction during RTH/WP |
| **Mag gain corruption** | Heading errors vary with orientation → unpredictable navigation |
| **Optical flow scale** | Ground-referenced position estimation corrupted |

**Severity:** HIGH — Subtle navigation errors that are hard to diagnose

---

## 4. Multi-Layer Attack Combinations

### 4.1 ELRS Link Compromise + iNav Navigation Attack

From Tier 1.1 (DSMX/ELRS) research, if the ELRS binding phrase is cracked:

```
Layer 1: Crack ELRS binding phrase (offline, see Tier 1.1/PR #66 analysis)
Layer 2: Inject MSP commands via CRSF telemetry link
Layer 3: Corrupt iNav navigation parameters
Layer 4: Trigger failsafe → drone flies to attacker location
```

iNav supports MSP over serial links including telemetry UARTs. If CRSF/ELRS carries bidirectional MSP (as some configurations allow), OTA MSP injection is possible.

### 4.2 GPS Spoofing + Position Estimator Manipulation

```
Layer 1: MSP_SET_POSITION_ESTIMATION_CONFIG → maximize GPS weight, zero baro weight
Layer 2: GPS spoof via SDR (see Tier 2.2 analysis)
Layer 3: Position estimator fully trusts spoofed GPS
Layer 4: Drone follows spoofed position → flies to attacker location
```

This combination defeats iNav's IMU cross-check because the fusion weights have been manipulated to ignore non-GPS sources.

### 4.3 Failsafe Corruption + RF Jamming

```
Layer 1: MSP_SET_FAILSAFE_CONFIG → procedure=DROP, throttle=1000
Layer 2: Jam RC link (2.4 GHz broadband)
Layer 3: Failsafe triggers → DROP procedure → motors off → drone falls
```

A targeted crash attack using two layers.

### 4.4 Stealth Mission Replacement

```
Layer 1: Gain MSP access while disarmed (physical or RF)
Layer 2: MSP_SET_NAV_CONFIG → increase nav_wp_safe_distance to 50000 (500m)
Layer 3: MSP_SET_WP × N → upload malicious mission with final LAND at attacker's location
Layer 4: Pilot arms and starts WP mission → follows attacker's route
```

Pre-flight mission replacement that the pilot may not notice if they don't verify the mission.

---

## 5. iNav vs. ArduPilot Comparison

| Dimension | iNav | ArduPilot |
|-----------|------|-----------|
| **Protocol** | MSP (v1/v2) | MAVLink (v1/v2) |
| **Authentication** | None | Optional HMAC-SHA256 (v2, off by default) |
| **Position estimator** | Complementary filter, configurable weights | EKF2/EKF3, more parameters |
| **Navigation commands** | MSP_SET_WP (21 bytes) | MAV_CMD_NAV_WAYPOINT |
| **Remote parameter write** | MSP_SET_* (immediate) | PARAM_SET (immediate) |
| **Failsafe manipulation** | MSP_SET_FAILSAFE_CONFIG | MAVLink parameter write |
| **Glitch protection** | Removed in 5.0 | Active (EK3_GPS_CHECK) |
| **Home override** | WP#0 (requires GCS NAV) | MAV_CMD_DO_SET_HOME |
| **Follow-me** | WP#255 + POSHOLD + GCS NAV | GUIDED mode |
| **Safehomes** | Up to 8, configurable | Rally points |
| **Shared vulnerability** | GPS spoofing, parameter corruption | GPS spoofing, parameter corruption |

**Key iNav weakness:** Glitch protection removed in iNav 5.0 means GPS spoofing encounters no sanity check. ArduPilot's EKF still checks GPS consistency (though it can be disabled via parameters, per Tier 1.2 analysis).

---

## 6. Defenses and Limitations

### 6.1 What iNav Does Have

| Defense | Effectiveness | Bypass |
|---------|--------------|--------|
| `nav_wp_safe_distance` | Limits first WP distance (default 100m) | Changeable via MSP_SET_NAV_CONFIG |
| `nav_extra_arming_safety` | Pre-arm checks | Readable via MSP, not a security barrier |
| Safehome distance limit | 200m default | Only limits safehome selection |
| OSD warnings | Shows failsafe/safehome messages | Pilot may not see during FPV flight |
| MSP payload validation | From 5.0, length checked | Validates format, not authorization |
| Position invalidation | Drift beyond `inav_max_eph_epv` | Threshold itself is configurable |
| Emergency landing | On GPS loss during RTH/WP | Severity controllable via failsafe params |

### 6.2 What iNav Does NOT Have

- **No MSP authentication** — Any serial device can send any command
- **No command signing** — No HMAC, no tokens, no challenge-response
- **No parameter integrity** — No checksums on stored parameters
- **No anti-replay** — Duplicate commands accepted
- **No rate limiting** — Commands processed as fast as received
- **No audit log** — Blackbox logs flight data, not MSP commands received
- **No GPS authentication** — Civilian GPS has no signal authentication
- **No glitch protection** (since 5.0) — GPS jumps from spoofing are accepted
- **No sensor fusion integrity** — Fusion weights changeable at runtime

### 6.3 Fundamental Limitations of These Attacks

| Limitation | Detail |
|------------|--------|
| **Physical access** | USB/UART attacks require physical access to the FC |
| **Telemetry access** | OTA MSP requires compromised telemetry link first |
| **GCS NAV requirement** | WP#0 and WP#255 need GCS NAV mode asserted |
| **Armed state** | Some MSP writes blocked while armed (reboot, some config) |
| **EEPROM persistence** | Runtime changes revert on power cycle unless saved |
| **Pilot awareness** | Experienced pilot may notice abnormal behavior |
| **OSD indicators** | Safehome diversion, failsafe mode shown on OSD |

---

## 7. Comparison to Other Target Sets

| Dimension | iNav MSP | Betaflight MSP | ArduPilot MAVLink | DJI DUML |
|-----------|---------|----------------|-------------------|----------|
| **Auth** | None | None | Optional signing | None (BT/WiFi) |
| **Nav commands** | 20+ | ~2 (GPS Rescue) | 50+ | Full flight control |
| **Home override** | WP#0 | N/A | DO_SET_HOME | WiFi PSK → DUML |
| **Position estimator attack** | Fusion weight manipulation | N/A | EKF parameter manipulation | N/A (closed source) |
| **Failsafe corruption** | MSP_SET_FAILSAFE_CONFIG | Limited | Parameter write | Unknown |
| **Mission injection** | MSP_SET_WP (60 WPs) | N/A | MAV_CMD_NAV (hundreds) | N/A (closed mission) |
| **Hardware cost** | $0 (USB) | $0 (USB) | $0 (WiFi/serial) | $0-345 |
| **OTA feasibility** | Requires link compromise | Requires link compromise | WiFi range | BT/WiFi range |

---

## 8. Risk Assessment

| Factor | Rating | Notes |
|--------|--------|-------|
| **Technical feasibility** | **HIGH** | All attacks use documented MSP commands |
| **Authentication** | **NONE** | Zero access control on MSP |
| **Navigation impact** | **CRITICAL** | Full waypoint/RTH/position control |
| **Hardware cost** | **ZERO** (physical) | USB cable to FC |
| **OTA cost** | **LOW-MEDIUM** | Requires link compromise first |
| **Stealth** | **HIGH** | Runtime changes, no audit log, power-cycle reverts |
| **Combined attacks** | **DEVASTATING** | + GPS spoofing or + link compromise |
| **Remediation** | **VERY POOR** | MSP has no auth by design; would break all GCS tools |
| **Glitch protection** | **REMOVED** (5.0+) | GPS spoofing undetected |

---

## Sources

- [iNav MSP Navigation Messages](https://github.com/inavFlight/inav/wiki/MSP-Navigation-Messages)
- [iNav MSP Messages Reference](https://github.com/iNavFlight/inav/wiki/MSP-Messages-reference)
- [iNav GPS Failsafe and Glitch Protection](https://github.com/iNavFlight/inav/wiki/GPS-Failsafe-and-Glitch-Protection)
- [iNav Failsafe](https://github.com/iNavFlight/inav/wiki/Failsafe)
- [iNav Developer Info (Position Estimator)](https://github.com/inavFlight/inav/wiki/Developer-info)
- [iNav Remote Management, Control and Telemetry](https://github.com/inavFlight/inav/wiki/INAV-Remote-Management,-Control-and-Telemetry)
- [iNav Missions](https://github.com/iNavFlight/inav/wiki/iNavFlight-Missions)
- [iNav Safehomes](https://github.com/iNavFlight/inav/blob/master/docs/Safehomes.md)
- [iNav Navigation Modes](https://github.com/iNavFlight/inav/wiki/Navigation-modes)
- "Reverse Engineering and Control-Aware Security Analysis of the ArduPilot UAV Framework" (arXiv 2512.01164)
- "A systematic approach for threat and vulnerability analysis of unmanned aerial vehicles" (ScienceDirect 2024)
