# CRSF Serial Bus Injection — Proof of Concept Analysis

**Date:** 2026-10-27
**Status:** Software-validated (virtual bus), no hardware transmission
**Classification:** Authorized CTF security research
**Related:** `research/tbs-crossfire-wireless-security-audit-2026-10-20.md`

## Executive Summary

The Crossfire Serial Protocol (CRSF) serial bus has **zero authentication** between devices. Any component on the shared half-duplex UART can forge frames from any origin, issue arbitrary commands, and override flight control inputs. This PoC demonstrates nine injection scenarios against a virtual CRSF bus modeled on a TinyHawk III Plus (Betaflight 4.4.2 / ELRS receiver) configuration, with a complementary defensive monitor that detects all demonstrated attack patterns.

All attacks succeed because the protocol provides no origin verification, message authentication, or replay protection on the serial bus.

## Attack Surface

### Bus Architecture

```
Radio Handset (0xEA) ─── TX Module (0xEE) ═══ RF ═══ Receiver (0xEC) ─┬─ FC (0xC8)
                                                                       ├─ VTX (0xCE)
                                                                       └─ [Injector]
```

The CRSF bus is a shared UART running at 420 kbaud (ELRS) or 400 kbaud (Crossfire). All devices share the same wire. An attacker with physical access to the bus (e.g., via an exposed UART pad, USB-serial bridge, or a compromised peripheral) can inject arbitrary frames.

### Frame Format

| Byte | Field | Notes |
|------|-------|-------|
| 0 | Sync (0xC8) | Fixed |
| 1 | Length | Payload + type + CRC |
| 2 | Type | 0x16=RC, 0x28=Ping, 0x32=Cmd, etc. |
| 3..N-1 | Payload | Extended frames: dest(1) + origin(1) + data |
| N | CRC-8 (poly 0xD5) | Over type + payload |

Command frames (type 0x32) carry a second CRC-8 (poly 0xBA) over the command sub-protocol, but **both CRCs are unauthenticated** — an attacker computes them trivially.

## Demonstrated Attack Scenarios

### 1. Device Reconnaissance (device_ping)
- **Type:** Reconnaissance
- **Frame:** Device ping (0x28) to broadcast
- **Impact:** Enumerates all devices on the bus — FC, RX, VTX, sensors
- **Severity:** Low (information only)

### 2. Parameter Exfiltration (param_read)
- **Type:** Information disclosure
- **Frame:** Parameter read request (0x2C) to receiver
- **Impact:** Reads configuration parameters including bind phrase hints, power settings, RF parameters
- **Severity:** Medium

### 3. WiFi Mode Forced Entry (wifi_mode_entry)
- **Type:** Denial of service
- **Frame:** Parameter write (0x2D) to receiver, param index 0xFF = WiFi mode
- **Impact:** Forces ELRS receiver into WiFi update mode, **severing the RF control link**
- **Severity:** Critical — loss of aircraft control during flight

### 4. Forced Disarm (force_disarm)
- **Type:** Denial of service
- **Frame:** Command (0x32) to FC, cmd 0x01 = arm/disarm
- **Impact:** Disarms motors mid-flight
- **Severity:** Critical — uncontrolled descent

### 5. VTX Pit Mode (vtx_pitmode)
- **Type:** Disruption
- **Frame:** Command (0x32) to VTX, cmd 0x08 = VTX control, pit mode enable
- **Impact:** Kills video transmission, pilot loses FPV feed
- **Severity:** High — loss of visual control

### 6. Bind Disruption (bind_disrupt)
- **Type:** Denial of service
- **Frame:** Command (0x32) to receiver, cmd 0x10 = bind, sub 0x01
- **Impact:** Forces receiver into bind mode, severing established link
- **Severity:** Critical — loss of control link

### 7. RC Channel Override (rc_override)
- **Type:** Commandeering
- **Frame:** RC channels packed (0x16) with attacker-controlled stick values
- **Impact:** Overrides pilot stick inputs with attacker values. Last-write-wins on the bus.
- **Severity:** Critical — full flight control takeover

### 8. MSP Passthrough (msp_passthrough)
- **Type:** Privilege escalation
- **Frame:** MSP request (0x7A) tunneled through CRSF to FC
- **Impact:** Access to Betaflight MSP commands: read/write configuration, trigger reboot, modify PID tuning
- **Severity:** High — arbitrary FC configuration changes

### 9. CRC Tamper Detection (crc_tamper)
- **Type:** Validation
- **Frame:** Deliberately corrupted CRC
- **Impact:** Confirms that CRC validation rejects malformed frames (the only existing defense)
- **Severity:** N/A — validates the single integrity check

## Defensive Analysis

### Detection Signatures

The companion tool (`crsf-bus-defender.py`) implements nine detection rules:

| Rule | Trigger | Alert Level |
|------|---------|-------------|
| `unexpected_rc_origin` | RC channel frames from non-RX/TX sources | Critical |
| `dangerous_cmd_unexpected_origin` | Arm/disarm/bind/VTX commands from unknown origin | Critical |
| `dangerous_cmd` | Any arm/disarm/bind/VTX command (even from legitimate origin) | High |
| `wifi_mode_attempt` | Parameter write to WiFi mode index (0xFF) | Critical |
| `bind_command` | Bind initiation command during operation | Critical |
| `msp_injection` | MSP request/write from non-TX origin | High |
| `param_read_flood` | >10 parameter reads in a session | Medium |
| `param_write_flood` | >5 parameter writes in a session | High |
| `rc_timing_anomaly` | High variance in RC frame intervals (dual-source) | Medium |
| `crc_error_spike` | >3 CRC errors (may indicate bus contention) | Medium |

### Recommended Mitigations

1. **Origin allowlisting:** FC firmware should maintain a whitelist of expected origins for safety-critical frame types (RC channels, arm/disarm commands). Reject frames with unexpected origin addresses.

2. **Command authentication:** Add HMAC or a shared-secret challenge-response to command frames (0x32). The existing CRC-8 provides integrity, not authentication.

3. **Bind lockout:** Reject bind commands (cmd 0x10) when the receiver has an active link with LQ > 0. Betaflight partially implements this but the receiver itself does not.

4. **WiFi mode protection:** Require a physical button press or multi-frame confirmation sequence before entering WiFi mode. ELRS 3.x partially addresses this.

5. **RC frame source tracking:** Monitor for RC channel frames from multiple origins within a timing window. Legitimate operation has exactly one source.

6. **MSP passthrough filtering:** Whitelist only safe MSP commands over CRSF passthrough. Block MSP_SET_*, MSP_REBOOT, and other write commands from the bus.

7. **Bus monitoring hardware:** A dedicated microcontroller on the CRSF bus running detection rules (as modeled by `crsf-bus-defender.py`) could alert or filter malicious frames in real-time.

## Validation

- **38 automated tests** validate CRC computation, frame construction, bus routing, all injection scenarios, and defensive detection
- All scenarios run against a virtual bus with four simulated devices (FC, RX, TX, VTX)
- No hardware was used or contacted — entirely software simulation
- The virtual bus models Betaflight/ELRS frame acceptance behavior including CRC validation and address filtering

## Files

| File | Purpose |
|------|---------|
| `tools/crsf-injection-poc.py` | Virtual CRSF bus + 9 attack scenarios |
| `tools/crsf-bus-defender.py` | Defensive anomaly detector (9 rules) |
| `tools/tests/test_crsf_injection_poc.py` | 38 automated tests |
| `tools/crsf-packet-tool.py` | CRSF frame library (pre-existing) |

## Relation to Prior Work

This PoC validates findings from the TBS Crossfire security audit (`research/tbs-crossfire-wireless-security-audit-2026-10-20.md`), specifically:
- The audit identified the serial bus as unauthenticated (Section 4)
- The audit noted ELRS WiFi mode as a DoS vector (Section 5.2)
- The audit flagged MSP passthrough as an escalation path (Section 6.1)
- This PoC provides concrete, testable demonstrations of those findings

## Next Steps

1. **Hardware validation (read-only):** Connect to TinyHawk III Plus FC over USB-serial and passively sniff CRSF traffic to confirm frame timing and format against the virtual model
2. **SITL integration:** Wire the PoC frames into the ArduPilot SITL harness (`tools/sitl_harness.py`) for end-to-end MAVLink + CRSF attack chain validation
3. **E8 USB injection path:** Investigate whether the EMAX E8 controller's USB-CDC interface exposes a CRSF bus injection point (the P2 pathway from the master plan)
