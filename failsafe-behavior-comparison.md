# Failsafe Behavior Comparison — Betaflight, ArduPilot, iNav

Compiled 2026-10-14 for wireless drone CTF preparation. This document maps how each major flight controller firmware handles signal loss, what the configurable parameters are, and where the attack surface lies — particularly for smooth takeover scenarios where an attacker replaces the legitimate TX without triggering failsafe.

---

## Table of Contents

1. [Executive Summary for CTF](#executive-summary-for-ctf)
2. [Betaflight Failsafe](#betaflight-failsafe)
3. [ArduPilot Failsafe](#ardupilot-failsafe)
4. [iNav Failsafe](#inav-failsafe)
5. [Side-by-Side Comparison](#side-by-side-comparison)
6. [Smooth Takeover Analysis](#smooth-takeover-analysis)
7. [ELRS-Specific Failsafe Behavior](#elrs-specific-failsafe-behavior)
8. [Attack Implications](#attack-implications)

---

## Executive Summary for CTF

| Firmware | Default Failsafe | Takeover Difficulty | Key Weakness |
|---|---|---|---|
| **Betaflight** | Drop (motors off) | Low — no authentication beyond ELRS binding phrase | Stage 1 guard period gives a ~300ms window to re-establish link |
| **ArduPilot** | RTH or Land (configurable) | Medium — richer state machine, but same RF-layer weakness | Multiple failsafe layers (radio, GCS, battery) but RC override is straightforward once on the link |
| **iNav** | RTH (with GPS) / Drop (without) | Low–Medium — similar to Betaflight with GPS nav bolted on | RTH climb + return can be exploited by GPS spoofing (Tractor Beam) |

**The universal truth:** None of these firmware stacks authenticate the control link at the application layer. Authentication (such as it is) happens at the protocol layer (ELRS binding phrase, FrSky ID, Crossfire binding). If you can speak the protocol with valid credentials, the FC cannot distinguish your packets from the legitimate TX. Failsafe is a signal-loss mechanism, not an anti-hijack mechanism.

---

## Betaflight Failsafe

Betaflight uses a two-stage failsafe system. The stages run sequentially when the FC stops receiving valid RC data.

### Stage 1 — Guard Period (Channel Fallback)

- **Trigger:** No valid RC packets received for `failsafe_delay` × 0.1 seconds (default: 4 = 0.4s)
- **Behavior:** Each RC channel is set to its configured fallback value:
  - `AUTO` (default for most channels): holds last known good value
  - `HOLD`: explicitly holds last value
  - `SET`: moves to a configured value
  - `AUTO` on **throttle** specifically: drops to `rx_min_usec` (default 885µs, i.e., motors off)
- **Purpose:** Absorb brief signal dropouts without dramatic action
- **Duration:** Runs for `failsafe_delay` period, then transitions to Stage 2

**Key CLI parameters:**

| Parameter | Default | Description |
|---|---|---|
| `failsafe_delay` | 4 (0.4s) | Tenths of seconds before Stage 2 activates |
| `failsafe_throttle` | 1000 | Throttle value during Stage 2 "LAND" mode |
| `failsafe_off_delay` | 10 (1.0s) | Tenths of seconds of Stage 2 before motors off |
| `failsafe_switch_mode` | `STAGE1` | What the failsafe switch triggers (STAGE1, STAGE2, or KILL) |
| `failsafe_throttle_low_delay` | 100 (10s) | Tenths of seconds with low throttle before auto-disarm |
| `failsafe_procedure` | `DROP` | Stage 2 action: `DROP`, `LAND`, or `GPS_RESCUE` |
| `rx_min_usec` | 885 | Minimum valid pulse width — below this = invalid signal |
| `rx_max_usec` | 2115 | Maximum valid pulse width |

### Stage 2 — Failsafe Action

Activated after Stage 1 expires without signal recovery. Three modes:

| Mode | Behavior | When to Use |
|---|---|---|
| **DROP** (default) | Disarms immediately. Motors off. Drone falls. | Default for racing quads — minimizes runaway risk |
| **LAND** | Sets throttle to `failsafe_throttle` (default 1000µs), levels the quad, and descends. After `failsafe_off_delay` the motors cut. | Gentler than DROP but not GPS-aware |
| **GPS_RESCUE** | Engages GPS Rescue mode — climbs to set altitude, flies back toward home point, then lands. | Requires GPS fix and calibrated GPS Rescue settings |

### Recovery from Failsafe

- **Stage 1 recovery:** Immediate — as soon as valid packets resume, normal control restores. The guard period resets.
- **Stage 2 recovery:** Once Stage 2 activates, recovery requires:
  1. Valid signal restoration
  2. Throttle stick at or below `min_check` position (centered low)
  3. The pilot must actively move sticks to regain control
- **Exception:** If `DROP` mode is active and the drone has disarmed, re-arming is required (not possible mid-air without `ALLOW_ARMING_WHILE_FALLING` in the `failsafe_recovery_delay` logic).

### Betaflight Failsafe Timeline

```
Signal Loss     Stage 1 (Guard)         Stage 2 (Action)         Disarm
    |               |                        |                      |
    |-- 0.4s ------>|                        |                      |
    |  (holds last  |-- varies by mode ----->|                      |
    |   values,     |  DROP: immediate       |-- motor off -------->|
    |   throttle    |  LAND: failsafe_off_   |                      |
    |   drops to    |        delay (1.0s)    |                      |
    |   minimum)    |  GPS_RESCUE: flies home |                      |
```

---

## ArduPilot Failsafe

ArduPilot has a significantly more complex failsafe system with multiple independent failsafe triggers that can each invoke different actions. Copter, Plane, and Rover have slightly different behaviors; this section focuses on **Copter** as the most CTF-relevant.

### Failsafe Triggers

ArduPilot distinguishes several independent failsafe sources:

| Trigger | Parameter | Detection Method |
|---|---|---|
| **Radio (RC) Failsafe** | `FS_THR_ENABLE` | Throttle PWM drops below `FS_THR_VALUE` (default 975µs), or no RC input for >500ms |
| **GCS Failsafe** | `FS_GCS_ENABLE` | No heartbeat from ground control station for >5 seconds (configurable via `FS_GCS_TIMEOUT`) |
| **Battery Failsafe** | `FS_BATT_ENABLE` | Voltage below threshold or remaining capacity too low |
| **EKF Failsafe** | `FS_EKF_ACTION` | Position/velocity estimates diverge (IMU/GPS disagreement) |
| **Vibration Failsafe** | `FS_VIBE_ENABLE` | Excessive vibration corrupts IMU readings |
| **Dead Reckoning** | `FS_DR_ENABLE` | GPS lost while in GPS-dependent mode |

### Radio Failsafe Actions (`FS_THR_ENABLE`)

| Value | Action | Description |
|---|---|---|
| 0 | Disabled | No radio failsafe |
| 1 | RTL | Return to Launch — climbs, flies home, lands |
| 2 | Continue in Auto | Continues current mission if in Auto mode; RTL otherwise |
| 3 | Land | Lands immediately at current position |
| 4 | SmartRTL or RTL | Retraces path home if SmartRTL breadcrumbs available, else RTL |
| 5 | SmartRTL or Land | Retraces path or lands |
| 6 | Auto → DO_LAND_START or RTL | Follows DO_LAND_START mission item if available |

### Radio Failsafe Detection

Two detection methods, OR'd together:

1. **Throttle PWM:** Receiver outputs a low throttle value (below `FS_THR_VALUE`, default 975µs) when it loses its TX link. Most receivers do this natively.
2. **No-signal timeout:** If the FC receives zero RC packets for >500ms, failsafe triggers regardless of throttle value. This catches receivers that stop outputting entirely rather than sending a low-throttle signal.

### Key Timing Parameters

| Parameter | Default | Description |
|---|---|---|
| `FS_THR_VALUE` | 975 | Throttle PWM threshold — below this = failsafe |
| `FS_GCS_TIMEOUT` | 5.0s | Seconds without GCS heartbeat before GCS failsafe |
| `FS_OPTIONS` | 0 (bitmask) | Fine-tunes failsafe behavior (see below) |
| `RC_OVERRIDE_TIMEOUT` | 3.0s | Seconds before RC override messages expire |

### `FS_OPTIONS` Bitmask (Copter)

This bitmask provides granular control over failsafe behavior:

| Bit | Value | Effect |
|---|---|---|
| 0 | 1 | Continue in Auto/Guided if only RC is lost (keep GCS link as backup) |
| 1 | 2 | Continue in Auto/Guided if only GCS is lost (keep RC link as backup) |
| 2 | 4 | Continue in Guided if only RC is lost |
| 3 | 8 | Continue in Auto if RC+GCS both lost |
| 4 | 16 | If RC failsafe in Land mode, force Land even if in Auto with mission |
| 5 | 32 | If short failsafe, proceed to long failsafe action immediately |
| 6 | 64 | GCS failsafe in Guided uses pilot-specified action, not RTL |

### Short vs. Long Failsafe (ArduPilot)

ArduPilot distinguishes between brief dropouts and sustained loss:

- **Short failsafe** (~1.5s for radio): If signal returns within this window, control resumes with no mode change.
- **Long failsafe** (~5s for radio): If signal remains lost, the configured failsafe action activates.
- The short/long distinction allows for graceful handling of momentary RF interference.

### ArduPilot Recovery

- **During short failsafe:** Automatic — signal return restores control immediately.
- **During long failsafe (RTL/Land active):** Pilot must switch modes or the GCS must intervene. If RC signal returns, the pilot regains control by changing the flight mode switch.
- **RC Override:** MAVLink `RC_CHANNELS_OVERRIDE` messages can substitute for RC input. This is the GCS-as-backup path and is also an attack vector — any MAVLink sender on the telemetry link can send overrides.

---

## iNav Failsafe

iNav's failsafe is architecturally similar to Betaflight (shared heritage) but adds GPS-aware navigation actions, making it a hybrid between Betaflight's simplicity and ArduPilot's sophistication.

### Two-Stage System

Like Betaflight, iNav has Stage 1 (guard) and Stage 2 (action):

#### Stage 1 — Guard Period

- **Trigger:** No valid RC packets for `failsafe_delay` × 0.1 seconds (default: 10 = 1.0s)
- **Behavior:** Channels move to their fallback values (similar to Betaflight)
- **Duration:** Configurable; default is longer than Betaflight (1.0s vs 0.4s)

#### Stage 2 — Failsafe Procedure

| `failsafe_procedure` Value | Behavior | Requirements |
|---|---|---|
| **SET-THR** (0) | Sets throttle to `failsafe_throttle` and holds level. Disarms after `failsafe_off_delay`. | None |
| **DROP** (1) | Immediately disarms. Motors off. | None |
| **RTH** (2, default) | Engages Return to Home navigation. Climbs to `nav_rth_altitude`, flies home, lands. | GPS fix + compass + proper nav configuration |
| **NONE** (3) | Does nothing — channels stay at Stage 1 values indefinitely. | — |

### RTH Failsafe Sequence (iNav)

When `failsafe_procedure = RTH`:

1. **Climb phase:** Aircraft climbs to `nav_rth_altitude` (default 10m above home)
2. **Return phase:** Flies back to home position
3. **Landing phase:** Descends and auto-lands
4. **Disarm:** Motors off after touchdown detected

If GPS fix is insufficient at the time of failsafe, iNav falls back to `SET-THR` or `DROP` depending on configuration.

### Key CLI Parameters (iNav)

| Parameter | Default | Description |
|---|---|---|
| `failsafe_delay` | 10 (1.0s) | Tenths of seconds before Stage 2 |
| `failsafe_throttle` | 1000 | Throttle value for SET-THR mode |
| `failsafe_off_delay` | 200 (20s) | Tenths of seconds in Stage 2 before disarm |
| `failsafe_throttle_low_delay` | 0 (disabled) | Low-throttle auto-disarm timer |
| `failsafe_procedure` | RTH (2) | Stage 2 action |
| `failsafe_recovery_delay` | 5 (0.5s) | Tenths of seconds of valid signal needed to exit failsafe |
| `failsafe_stick_threshold` | 50 | Stick movement threshold to cancel failsafe RTH |
| `failsafe_min_distance` | 0 | Minimum distance from home to activate RTH (below this → LAND) |
| `failsafe_min_distance_procedure` | DROP (0) | Action when too close for RTH |
| `nav_rth_altitude` | 1000 (10m) | RTH climb altitude in cm |

### iNav Failsafe Recovery

- **Stage 1 recovery:** Immediate on valid signal return.
- **Stage 2 recovery:** Requires:
  1. Valid signal for `failsafe_recovery_delay` period (default 0.5s)
  2. Sticks moved beyond `failsafe_stick_threshold`
  3. If RTH is active, pilot must actively override navigation
- **NONE mode:** No Stage 2 action → pilot can re-take control anytime signal returns.

---

## Side-by-Side Comparison

### Signal Loss Detection

| Aspect | Betaflight | ArduPilot | iNav |
|---|---|---|---|
| **Detection method** | No valid RC packets (protocol-level) | Throttle PWM < threshold OR no packets >500ms | No valid RC packets (protocol-level) |
| **Default detection delay** | 0.4s (Stage 1 guard) | ~1.5s short / ~5s long | 1.0s (Stage 1 guard) |
| **Configurable timing** | Yes (`failsafe_delay`) | Partially (short/long distinction is hardcoded ranges) | Yes (`failsafe_delay`) |
| **Multiple failsafe sources** | No (RC only) | Yes (RC, GCS, battery, EKF, vibration) | Limited (RC + nav sanity) |

### Default Actions

| Aspect | Betaflight | ArduPilot | iNav |
|---|---|---|---|
| **Default failsafe action** | DROP (motors off) | Varies by param — commonly RTL | RTH (if GPS) / DROP (if no GPS) |
| **Landing option** | Yes (LAND mode) | Yes (Land mode) | Yes (SET-THR) |
| **Return-to-home** | Yes (GPS_RESCUE) | Yes (RTL, SmartRTL) | Yes (RTH, primary design goal) |
| **Continue mission** | No | Yes (`FS_OPTIONS` bit 0/1) | No |
| **Do nothing option** | No | No (but can disable FS) | Yes (`NONE` mode) |

### Recovery Behavior

| Aspect | Betaflight | ArduPilot | iNav |
|---|---|---|---|
| **Stage 1 recovery** | Immediate | Immediate (short failsafe) | Immediate |
| **Stage 2 recovery** | Requires low throttle + stick input | Mode switch or GCS intervention | Requires signal + stick input + recovery delay |
| **Re-arm required after DROP** | Yes (usually) | N/A (Land, not drop) | Depends on mode |
| **Throttle position check** | Yes — throttle must be low | Not explicitly for recovery | Yes |

### Configurability

| Aspect | Betaflight | ArduPilot | iNav |
|---|---|---|---|
| **Configuration interface** | CLI (`set failsafe_*`) + Configurator GUI | Parameter tree (QGC, Mission Planner, CLI) | CLI (`set failsafe_*`) + Configurator GUI |
| **Number of failsafe params** | ~10 | ~25+ (across all failsafe types) | ~15 |
| **Granularity** | Medium | High (bitmask options, per-mode behavior) | Medium-High |
| **Complexity** | Low | High | Medium |

---

## Smooth Takeover Analysis

A "smooth takeover" is when an attacker replaces the legitimate TX with their own signal, and the drone transitions to attacker control without ever entering failsafe. This is the ideal CTF attack — no RTH, no landing, no disarm. The drone simply obeys the attacker.

### Why Smooth Takeover Works

None of these firmware stacks perform application-layer authentication of control inputs. The FC trusts whatever the RX delivers over the serial/SPI bus. The authentication boundary is entirely at the RF protocol layer:

```
[TX] ---(RF protocol auth)---> [RX] ---(CRSF/SBUS/etc.)---> [FC]
          ^^^ THIS is the        ^^^ THIS is a dumb pipe
          only auth boundary     with zero authentication
```

If an attacker can satisfy the RF protocol's binding/credentials, their packets are indistinguishable from the legitimate TX's packets at the FC level.

### ELRS Smooth Takeover Conditions

For ELRS specifically (the protocol on our lab hardware), a smooth takeover requires:

1. **Know the binding phrase** (or the derived UID)
   - Derived UID: `MD5("'-DMY_BINDING_PHRASE=\"" + phrase + "\"'")[0:6]`
   - Betaflight 4.4.2 OTA v3 carries `UID3`, `UID4`, and `UID5` fields in
     every SYNC. `UID3`, `UID4`, and the upper two bits of `UID5` are
     unmasked; model match may XOR the lower six bits of the transmitted
     `UID5`. On this model-ID-255 pair the XOR is zero, so decoded SYNCs expose
     UID bytes 3--5 directly. OTA v4 removes the `UID3` field.
   - Common phrases are in our rainbow table (`tools/elrs-rainbow-table.csv`)

2. **Match the FHSS sequence**
   - Deterministic from UID — if you know the UID, you know every hop
   - 256-entry sequence, 80 channels for 2.4 GHz

3. **Transmit with stronger signal than the legitimate TX**
   - ELRS uses a "strongest signal wins" model at the SX1280 level
   - No sequence numbers or replay protection in the OTA packets
   - The RX locks to the strongest valid signal on the expected frequency at the expected time

4. **Maintain timing alignment**
   - Must hit the right frequency at the right time in the hop sequence
   - Phase tracking is needed — listen to SYNC packets to determine current position in the sequence

5. **No signal gap**
   - If there is a gap between the legitimate TX going silent and the attacker's TX being received, the FC will enter Stage 1 failsafe
   - The attacker must overlap or immediately replace the legitimate signal
   - The guard period (0.4s Betaflight, 1.0s iNav) provides a window: if the attacker starts transmitting within this period, the FC never reaches Stage 2

### Takeover Timing Windows

| Firmware | Stage 1 Guard | Recovery from Stage 1 | Stage 2 Action | Implication |
|---|---|---|---|---|
| **Betaflight** | 0.4s | Immediate | DROP / LAND / GPS_RESCUE | Must establish link within 400ms to avoid Stage 2. Stage 1 recovery is immediate and seamless. |
| **ArduPilot** | ~1.5s (short) | Immediate | Configurable (RTL default) | 1.5s window before any mode change. If signal returns within short-failsafe window, nothing observable happens. |
| **iNav** | 1.0s | Immediate | RTH / SET-THR / DROP | 1.0s window. Stage 1 recovery is seamless with no pilot-observable effects. |

### Per-Firmware Takeover Characteristics

#### Betaflight
- **Easiest takeover target** for racing quads
- No GPS on most racing builds → failsafe = DROP → if you miss the window, the drone falls
- Stage 1 guard is short (0.4s) but recovery is instant
- No telemetry-based anomaly detection
- SPI ELRS (as on TinyHawk III) means no separate RX processor to add latency

#### ArduPilot
- **Most complex state machine** but the RF-layer vulnerability is identical
- `FS_OPTIONS` can be configured to continue autonomous missions during RC loss — meaning the FC may not react to signal loss at all if GCS link is maintained
- RC Override via MAVLink is a second attack vector: send `RC_CHANNELS_OVERRIDE` on the telemetry link
- EKF failsafe could trigger if attacker sends erratic commands that cause sensor disagreements
- Multiple failsafe layers mean an attacker must plan for cascading failsafes (battery, GCS, EKF)

#### iNav
- **RTH default is exploitable via GPS spoofing** — if the drone enters RTH failsafe, a GPS spoofer can redirect the "home" position (Tractor Beam attack)
- Longer Stage 1 guard (1.0s) gives more room for the attacker to establish their link
- `failsafe_procedure = NONE` (if configured) means the FC does nothing on signal loss — easiest takeover, but unlikely in competition
- `failsafe_stick_threshold` means the attacker must move sticks to claim control after Stage 2 recovery

---

## ELRS-Specific Failsafe Behavior

ELRS has its own link-level failsafe handling that runs before the FC's failsafe ever sees anything:

### ELRS Connection States

| State | Description | FC Sees |
|---|---|---|
| **Connected** | TX and RX are exchanging packets | Normal RC channels via CRSF |
| **Tentative** | Some packets received but connection not fully established | RX may not output yet |
| **Disconnected** | No valid packets received for `RxExpireTimeout` | RX signals failsafe to FC |

### ELRS Failsafe Signaling to FC

When the ELRS RX loses its link, it signals failsafe to the FC in one of two ways (configurable in ELRS Lua or web UI):

| ELRS Failsafe Mode | Behavior | Effect on FC |
|---|---|---|
| **No Pulses** | RX stops sending CRSF frames entirely | FC detects signal loss via packet timeout → enters failsafe |
| **Last Position** | RX continues sending the last received channel values | FC sees valid-looking data → does NOT enter failsafe. Drone holds last stick position. |

**"Last Position" is critical for takeover defense:** If the ELRS RX is configured for "Last Position" failsafe, the FC never enters failsafe on signal loss — it just holds the last stick values. This means:
- The drone continues flying with whatever the last commands were
- An attacker has unlimited time to establish their own link
- There is no observable state change

### Model Match

ELRS has a `modelMatch` feature that ties RX binding to a specific model ID on the TX. This adds a check beyond the binding phrase:
- If `modelMatch` is enabled, the RX only accepts packets from a TX broadcasting the correct model number
- Default is **off** in most configurations
- When on, adds 8 bits of model ID to the SYNC validation — minimal additional security but one more thing to brute-force or capture

---

## Attack Implications

### Offensive Priorities for CTF

1. **Binding phrase recovery is the single most important objective.** If you know the binding phrase, you can compute the UID, derive the FHSS sequence, and forge valid ELRS packets. The FC's failsafe never enters the picture because the RX accepts your packets as legitimate.

2. **Overlap, don't gap.** Start transmitting before (or simultaneously with) the legitimate TX going silent. If the attacker's signal is stronger, the SX1280 demodulator will lock to it. The FC sees an uninterrupted stream of valid RC data.

3. **Failsafe as a fallback attack.** If smooth takeover isn't achievable, deliberately triggering failsafe can still deny the target:
   - Against Betaflight with DROP: the drone falls (DoS)
   - Against ArduPilot/iNav with RTH: GPS spoof the home position to redirect the drone
   - Against any firmware with LAND: the drone lands at its current position (denial of mission)

4. **ELRS "Last Position" failsafe helps the attacker.** If the target RX uses "Last Position" mode, jamming the legitimate TX doesn't cause the drone to fall or RTH — it just freezes in place, giving the attacker time to connect.

5. **ArduPilot MAVLink vector.** If the target uses ArduPilot with an open telemetry link (no signing), `RC_CHANNELS_OVERRIDE` messages bypass the RF layer entirely. This is a GCS-level takeover that doesn't touch ELRS at all.

### Defensive Awareness (Know What They Might Do)

Competition targets may be hardened with:
- Short failsafe timers to minimize the takeover window
- GPS Rescue / RTH as a recovery mechanism (exploitable via GPS spoofing)
- `failsafe_switch_mode = KILL` on a deadman switch (if the pilot's switch channel goes out of range, instant disarm — harder to fake because you must continuously maintain the correct channel value)
- ArduPilot `FS_OPTIONS` configured to continue mission without RC — makes RC-layer attacks irrelevant for autonomous missions
- MAVLink signing enabled — blocks unauthenticated GCS commands

### Quick Reference: CLI Queries per Firmware

To dump failsafe configuration on a captured or probed target:

**Betaflight:**
```
# Over MSP/CLI
get failsafe
get rx_min_usec
get rx_max_usec
```

**ArduPilot:**
```
# Over MAVLink (param request)
param show FS_*
param show RC_OVERRIDE_TIMEOUT
```

**iNav:**
```
# Over MSP/CLI
get failsafe
get nav_rth_altitude
```

---

## Sources

- [Betaflight Failsafe Documentation](https://betaflight.com/docs/wiki/guides/current/Failsafe)
- [Betaflight Failsafe Tab (Configurator)](https://betaflight.com/docs/wiki/app/failsafe-tab)
- [ArduPilot Radio Failsafe (Copter)](https://ardupilot.org/copter/docs/radio-failsafe.html)
- [ArduPilot GCS Failsafe](https://ardupilot.org/copter/docs/gcs-failsafe.html)
- [ArduPilot FS_OPTIONS Discussion](https://discuss.ardupilot.org/t/failsafe-updates-for-radio-gcs-and-battery-plus-new-fs-options-parameter/48822)
- [iNav Failsafe Wiki](https://github.com/iNavFlight/inav/wiki/Failsafe)
- [iNav Failsafe Source (docs/Failsafe.md)](https://github.com/iNavFlight/inav/blob/master/docs/Failsafe.md)
- [NCC Group/Fox-IT: ExpressLRS Vulnerabilities Allow Hijack of Control Link](https://www.fox-it.com/nl-en/technical-advisory-expresslrs-vulnerabilities-allow-for-hijack-of-control-link/)
- [Oscar Liang: Failsafe Setup Guide](https://oscarliang.com/failsafe-rx-fc-naze32-frsky-d4rii/)
- Existing project documentation: `drone-wireless-vuln-reference.md`, `firmware/README.md`
