# Tier 1.2: ArduPilot / MAVLink — Attack Surface Analysis

**Date:** 2026-10-31
**Status:** Research complete — PoC toolkit built with pymavlink, testable against SITL (no hardware needed)
**Context:** Authorized security research

---

## Executive Summary

MAVLink is the de facto standard protocol for drone/UAV communication, used by ArduPilot, PX4, and dozens of commercial drone platforms. **MAVLink v1 has zero authentication** — any device that can reach the communication link (serial, WiFi, telemetry radio) can inject arbitrary commands. MAVLink v2 adds optional HMAC-SHA256 signing, but it's **off by default and rarely enabled in practice**. Even when enabled, v2 signing can be defeated by a protocol downgrade attack. A recent critical CVE (CVE-2026-38971, CVSS 9.1) demonstrates the ongoing vulnerability of the ArduPilot stack. Published academic research has demonstrated six distinct attack classes against ArduPilot, validated on both SITL and real Pixhawk hardware.

---

## 1. MAVLink Protocol Architecture

### 1.1 Protocol Versions
| Version | Auth | Encryption | Signing | Status |
|---------|------|------------|---------|--------|
| MAVLink v1 | None | None | None | Still widely deployed |
| MAVLink v2 | Optional HMAC-SHA256 | None | Optional, off by default | Current standard |

### 1.2 MAVLink v1 Packet Structure (25 bytes max)
```
[STX=0xFE][Len][Seq][SysID][CompID][MsgID][Payload...][CRC16]
```
- **No authentication field** — CRC is for integrity, not authentication
- SysID/CompID are self-declared by the sender (trivially spoofable)
- Any device on the link can claim to be any system/component

### 1.3 MAVLink v2 Packet Structure
```
[STX=0xFD][Len][IncompatFlags][CompatFlags][Seq][SysID][CompID][MsgID(3bytes)][Payload...][CRC16][Signature(13bytes, optional)]
```
- **Signature format:** LinkID(1) + Timestamp(6) + SHA256(secret + header + payload + CRC + linkID + timestamp)(6)
- Signature is **optional** — `IncompatFlags & 0x01` indicates signed
- **Timestamp is 10μs resolution since 2015-01-01** — prevents replay
- **Secret key:** 32-byte shared secret, must be provisioned on both ends

### 1.4 Why Signing Fails in Practice
1. **Off by default** — requires explicit configuration on both GCS and vehicle
2. **Key distribution** — no standard mechanism; usually manual provisioning
3. **Backward compatibility** — mixed v1/v2 environments accept unsigned packets
4. **Downgrade attack** — attacker forces negotiation to v1, bypassing all signing
5. **USB bypass** — signing not supported on USB serial; `SERIAL0_PROTOCOL=0` needed to block
6. **Key extraction** — shared secret stored in flash; physical access → key theft
7. **Telemetry still cleartext** — signing authenticates but doesn't encrypt

---

## 2. Attack Surfaces

### 2.1 Communication Links (Attack Entry Points)
| Link | Range | Typical Security | Notes |
|------|-------|-----------------|-------|
| WiFi telemetry (ESP8266/ESP32) | 100m+ | None | Most common companion link |
| SiK telemetry radio (433/915 MHz) | 1km+ | None | Long-range, unauthenticated |
| 4G/LTE cellular link | Global | None (MAVLink level) | Growing in commercial drones |
| Companion computer (RPi) | Local | Varies | Network-accessible services |
| USB serial | Physical | None (signing unsupported) | Firmware upload + MAVLink |
| CAN bus | Physical | None | Peripherals, UAVCAN |

### 2.2 MAVLink Command Categories
| Category | Key Messages | Impact |
|----------|-------------|--------|
| **Flight control** | SET_MODE, MAV_CMD_NAV_*, SET_POSITION_TARGET_* | Redirect/crash the vehicle |
| **Arming** | MAV_CMD_COMPONENT_ARM_DISARM | Arm in unsafe conditions or disarm mid-flight |
| **Parameters** | PARAM_SET | Modify PID gains, failsafe thresholds, EKF settings |
| **Missions** | MISSION_ITEM, MISSION_SET_CURRENT | Replace or corrupt waypoint missions |
| **Geofence** | FENCE_POINT, MAV_CMD_DO_FENCE_ENABLE | Disable geofence or create forbidden zones |
| **Telemetry** | HEARTBEAT, GPS_RAW_INT, ATTITUDE | Spoof sensor data to GCS |
| **Firmware** | MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN | Reboot vehicle mid-flight |

---

## 3. Published Attack Research

### 3.1 Control-Aware Manipulation (arXiv 2606.22289, 2026)

Six attack classes validated on SITL + Pixhawk 2.4.8 hardware:

| Attack | Method | Effect |
|--------|--------|--------|
| **Oscillation injection** | Alternating position setpoints via SET_POSITION_TARGET_LOCAL_NED | Position controller instability |
| **PID gain manipulation** | PARAM_SET on rate controller gains | Rate loop destabilization |
| **EKF confusion** | PARAM_SET on estimator noise/gating params | Sensor fusion corruption (stealthy) |
| **Failsafe bypass** | Combine rate-loop corruption + setpoint excitation | Override safety mechanisms |
| **Yaw destabilization** | Corrupt yaw-rate and slew settings | Mixer priority exploitation |
| **Combined multi-layer** | EKF + rate + attitude corruption simultaneously | Complete loss of control → crash |

Key findings:
- ArduPilot accepted all out-of-range parameter values without validation
- EKF attack was **stealthy** — attitude monitoring panels showed no anomaly
- Combined attack persisted even after attacker stopped sending (self-destabilizing state)
- Hardware effects were **stronger** than simulation

### 3.2 CVE-2026-38971: ArduPlane Out-of-Bounds Read (CVSS 9.1)
- Critical vulnerability in ArduPlane
- Out-of-bounds read — potentially exploitable for information disclosure or DoS
- Added to OWASP Drone Security Cheat Sheet
- Demonstrates ongoing vulnerability of the ArduPilot stack

### 3.3 CVE-2020-10282: Missing Authentication (CVSS 9.6)
- CWE-306: Missing Authentication for Critical Function
- Covers the fundamental MAVLink v1 authentication absence
- Affects all MAVLink v1 implementations

### 3.4 ArduPilot Issue #32996: Arm/Disarm Magic Value Bypass
- `MAV_CMD_COMPONENT_ARM_DISARM` treats the disarm magic value (21196) as a force-arm bypass during arming
- Bug allows bypassing pre-arm safety checks

---

## 4. Attack Scenarios

### 4.1 Scenario A: WiFi Telemetry Injection
**Target:** ArduPilot drone with ESP8266/ESP32 WiFi telemetry module
**Range:** ~100m line of sight (more with directional antenna)
**Method:**
1. Scan for ArduPilot WiFi AP (default SSID patterns: "ArduPilot", "Pixhawk", etc.)
2. Connect to open WiFi (usually no password, or default password)
3. Connect pymavlink to UDP port 14550
4. Send MAVLink commands: mode change, waypoint override, parameter manipulation

### 4.2 Scenario B: Telemetry Radio Interception
**Target:** ArduPilot with SiK 433/915 MHz telemetry radio
**Range:** 1km+ (matched to telemetry range)
**Method:**
1. SDR reception of 433/915 MHz telemetry (SiK uses FHSS, but transparent serial)
2. Decode MAVLink stream (standard framing, no encryption)
3. Inject packets via same frequency or collocated transmitter

### 4.3 Scenario C: Parameter Manipulation (Stealthy)
**Target:** Any ArduPilot vehicle accessible via MAVLink
**Method:**
1. Connect to MAVLink stream
2. Use PARAM_SET to modify EKF parameters (stealthy — no visible attitude change)
3. Gradually corrupt sensor fusion until vehicle becomes uncontrollable
4. Effect persists even after attacker disconnects

### 4.4 Scenario D: Geofence Manipulation
**Target:** ArduPilot with geofence enabled
**Method:**
1. Send MAV_CMD_DO_FENCE_ENABLE with param1=0 (disable fence)
2. Or: overwrite FENCE_POINTs to create arbitrary restricted zones
3. Vehicle ignores geofence boundaries or enforces attacker-defined boundaries

### 4.5 Scenario E: Mission Hijack
**Target:** ArduPilot running autonomous waypoint mission
**Method:**
1. Send MISSION_COUNT + MISSION_ITEM_INT messages to upload new mission
2. Or: MISSION_SET_CURRENT to jump to specific waypoint
3. Vehicle redirects to attacker-controlled waypoints

---

## 5. PoC Toolkit Architecture

### 5.1 Components (built with pymavlink)
```
mavlink-toolkit/
├── mavlink_recon.py       # Passive telemetry interception and decoding
├── mavlink_inject.py      # Command injection (mode, arm, waypoint, params)
├── mavlink_param_attack.py # Parameter manipulation (PID, EKF, failsafe)
├── mavlink_mission.py     # Mission upload/hijack
├── mavlink_fence.py       # Geofence manipulation
└── README.md              # Usage and attack scenarios
```

### 5.2 Testing Against SITL
ArduPilot SITL (Software In The Loop) provides a complete simulation environment:
- Full ArduPilot firmware running on the host
- MAVLink interface on localhost UDP
- No hardware required
- All attacks testable in simulation first

---

## 6. Comparison to Current ELRS/Betaflight Target

| Dimension | ELRS/Betaflight | ArduPilot/MAVLink |
|-----------|----------------|-------------------|
| **Protocol** | CRSF (wire) + ELRS (RF) | MAVLink v1/v2 (serial/WiFi/radio) |
| **Authentication** | Binding phrase only | None (v1) / Optional signing (v2) |
| **Encryption** | None | None |
| **Attack complexity** | RF + protocol RE | Connect to WiFi/serial → inject |
| **Command richness** | ~16 RC channels | Hundreds of message types, thousands of params |
| **Autonomous flight** | Limited (GPS Rescue only) | Full waypoint missions, geofence, RTL |
| **Target population** | FPV racing (growing) | Commercial, industrial, military (massive) |
| **Prior CVEs** | NCC Group findings | CVE-2026-38971, CVE-2020-10282, many more |
| **SITL available** | No | Yes — full simulation |

**Key advantage:** MAVLink attacks require NO RF expertise. WiFi telemetry modules provide a standard TCP/IP attack surface. The command set is vastly richer than MSP, and SITL allows full validation without any hardware.

---

## 7. Hardware Requirements

### What's Testable Without Hardware (Offline/SITL)
1. ✅ All pymavlink command injection — runs against SITL on localhost
2. ✅ Parameter manipulation attacks — full PARAM_SET testing
3. ✅ Mission upload/hijack — complete waypoint mission manipulation
4. ✅ Geofence manipulation — fence enable/disable/rewrite
5. ✅ EKF confusion attack — parameter-based sensor fusion corruption
6. ✅ Arm/disarm bypass — MAV_CMD_COMPONENT_ARM_DISARM testing
7. ✅ Protocol downgrade analysis — v2→v1 negotiation behavior

### What Requires Hardware
- Real-world WiFi telemetry interception (range, signal strength)
- SiK radio telemetry injection (requires matched radio or SDR)
- Physical crash/destabilization validation
- 4G/LTE link injection testing

### Hardware Gap
**SITL covers the vast majority of attack validation.** Physical hardware (Pixhawk + telemetry module) is needed only for final real-world validation. SITL installation requires ~2GB disk and some build dependencies.

---

## 8. Risk Assessment

| Factor | Rating | Notes |
|--------|--------|-------|
| **Technical feasibility** | **VERY HIGH** | pip install pymavlink → inject commands |
| **Hardware accessibility** | **VERY HIGH** | SITL requires zero hardware |
| **Novelty** | **MEDIUM** | Well-known weakness, but fresh 2026 CVE + academic work |
| **Impact** | **CRITICAL** | Full vehicle control, mission hijack, crash induction |
| **Remediation** | **PARTIAL** | Signing exists but rarely enabled; no encryption |
| **Target population** | **MASSIVE** | Millions of ArduPilot/PX4 vehicles worldwide |
| **Transferability** | **HIGH** | Same attacks work against PX4, other MAVLink-based systems |

---

## Sources

- "Control-Aware Manipulation of ArduPilot via Legitimate MAVLink Commands" (arXiv 2606.22289)
- CVE-2026-38971 (ArduPlane OOB Read, CVSS 9.1)
- CVE-2020-10282 (MAVLink Missing Authentication, CVSS 9.6)
- ArduPilot Security Landing Page (ardupilot.org/dev/docs/security-landing-page.html)
- OWASP Drone Security Cheat Sheet
- "MAVLink: the protocol behind millions of drones" (dev.to/oliopti)
- "MAVLink Protocol: A Survey of Security Threats and Countermeasures" (ResearchGate 2024)
- ArduPilot Issue #32996 (arm/disarm magic value bypass)
- "Reverse Engineering and Control-Aware Security Analysis of ArduPilot" (arXiv 2512.01164)
- pymavlink documentation (ardusub.com/developers/pymavlink.html)
