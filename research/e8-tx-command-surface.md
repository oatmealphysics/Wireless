# EMAX E8 (ELRS) TX — Command Surface for "Issue Commands to the Controller"

**Date:** 2026-11-03
**Source:** ExpressLRS 4.1.0 version-matched source (`firmware/src/ExpressLRS-4.1.0/`)
**Scope:** Offline source analysis only. No device interaction, no RF transmission,
no flashing. Documents the input paths an attacker can use to issue commands to
the E8 controller (TX) and the sinks each path reaches.
**Relationship:** complements `crsf-injection-poc-analysis.md` (RX/drone-side bus
injection) and `msp-elrs-hijack-poc.py` / `elrs-tx-emulator.py` (RX-side frame
forging). This is the **reverse direction**: commands *to* the controller, not *to*
the drone.

---

## 0. Authorization frame

Authorized CTF security research on the operator's **own, physically-held,
grounded E8 controller** (bench, isolated network, responsible disclosure).
"Issue commands to the controller" = achieve command execution on a device the
operator owns sitting on the bench. This is in scope per the exploit-throw
pipeline scope boundary (2026-10-13). The no-flashing rule for the E8 is absolute
and is not relaxed by this document.

---

## 1. The three input paths to the E8 TX

The E8 runs ELRS `TARGET_TX` firmware on an ESP32-class MCU (per the related
EMAX OLED/Nano target registry; the E8's own silicon is still unconfirmed — see
`HARDWARE-IDENTITY.md`). There are exactly three input paths the firmware reads
commands from, plus one out-of-band path:

| # | Path | Transport | Auth | Sinks reached |
|---|---|---|---|---|
| A | **USB CDC** (`/dev/ttyACM0`, 115200 8N1) | E8's own USB-C | **None** (firmware reads `TxUSB->available()` and feeds `crsfParser` / MSP directly — `tx_main.cpp:1131 HandleUARTin`) | CRSF frames, MSP packets, MAVLink auto-switch |
| B | **WiFi webserver** (ESP32 softAP, when in WiFi mode) | 2.4 GHz Wi-Fi | **None** (no auth on the AsyncWebServer; `Access-Control-Allow-Origin: *`) | Reboot, factory reset, config import, firmware upload, CW TX, button config |
| C | **ELRS over-the-air** (RX→TX uplink, telemetry) | 2.4 GHz ISM FHSS | **UID-seeded CRC only** (no per-packet crypto; "encryption" is the bind UID) | Telemetry, power-calibration read/set (MSP_ELRS_FUNC), VTX config, backpack ptr |
| D | **EMAX vendor USB tool** | Same USB CDC as A | None | EMAX-proprietary config read/write (17-byte read confirmed in `research/e8-protocol-reconstruction.md`) |

**Key fact (A):** the E8's USB CDC port is *not* a dumb serial console. In
`tx_main.cpp:1131 HandleUARTin()`, USB serial input is fed to
`crsfParser.processBytes(&usbConnector, buf, size)` (line 1174) **or** to
MAVLink auto-switch (line 1160-1163) **or** to the airport buffer if the build
is `is_airport` (line 1133-1144). This means any host that can open the E8's USB
port can issue CRSF frames and MSP packets to the TX firmware with zero
authentication. The vendor tool we already reverse-engineered is just one user of
this path; a generic CRSF/MSP sender is another.

**Key fact (C):** the over-the-air uplink (RX→TX) is a *telemetry* channel in
normal operation, not a command channel. The TX firmware reads MSP from it via
`ParseMSPData` → `ProcessMSPPacket` (`tx_main.cpp:1103,1056`), but the **TX-side
MSP handler is narrow** — see §2.

---

## 2. The TX-side MSP handler is narrow (this is the constraint)

`ProcessMSPPacket` at `tx_main.cpp:1056` is the function that acts on MSP
packets received by the TX. The full switch, from source:

```c
void ProcessMSPPacket(uint32_t now, mspPacket_t *packet) {
#if defined(PLATFORM_ESP32)
  if (packet->function == MSP_ELRS_FUNC) {              // 0x4578
    uint8_t opcode = packet->readByte();
    switch (opcode) {
    case MSP_ELRS_POWER_CALI_GET:   OnPowerGetCalibration(packet); break;  // 0x20
    case MSP_ELRS_POWER_CALI_SET:   OnPowerSetCalibration(packet);  break;  // 0x21
    default: break;
    }
  }
  else if (packet->function == MSP_SET_VTX_CONFIG) {    // 89
    if (packet->payload[0] < 48) {
      config.SetVtxBand(packet->payload[0] / 8 + 1);
      config.SetVtxChannel(packet->payload[0] % 8);
    } else { return; }
    VtxTriggerSend();
  }
  else if (packet->function == MSP_ELRS_BACKPACK_SET_PTR) {  // 0x0383
    processPanTiltRollPacket(now, packet);
  }
  if (packet->function == MSP_ELRS_GET_BACKPACK_VERSION) {   // 0x10
    memcpy(backpackVersion, packet->payload, ...);
  }
#endif
}
```

**What the TX MSP handler does NOT do** (compared to the RX handler in
`rx_main.cpp`):
- It does **not** call `processMspPacket` (Betaflight's general MSP dispatch).
  The RX side routes unknown MSP to the flight controller; the TX side has no
  flight controller and no general dispatcher.
- It does **not** handle `MSP_ELRS_BIND` (0x09) — bind-UID re-write is RX-side
  only (`rx_main.cpp:964`).
- It does **not** handle `MSP_ELRS_SET_RX_WIFI_MODE` (0x0E) — the TX *sends*
  this opcode to the RX (`tx_main.cpp:950 SendRxWiFiOverMSP`), it does not
  act on it.
- It does **not** handle `MSP_ELRS_RXTX_CONFIG` (0x2D) subcommands
  (UID/BIND_PHRASE/MODEL_ID) — those are RX-side.

**So the MSP-over-air path to the TX (path C) reaches only four sinks:**
1. `OnPowerGetCalibration` — read-only, returns power calibration values.
2. `OnPowerSetCalibration` — **state-changing**: calls `hwTimer::stop()`,
   `delay(20)`, writes to `POWERMGNT` calibration. This is the most interesting
   TX-side MSP sink — an attacker who can inject MSP over the ELRS uplink can
   stop the TX's hardware timer and alter power calibration. Reaching it
   requires the attacker to be the bound RX (or a forged frame accepted by the
   TX's uplink parser).
3. `SetVtxBand`/`SetVtxChannel` + `VtxTriggerSend` — reconfigure the VTX band/
   channel. State-changing but bounded (48-channel table).
4. `processPanTiltRollPacket` — backpack pan/tilt/roll pointer. Bounded.

**The RX-side "broad MSP → flight controller" attack surface does not apply to
the TX.** The TX is not a flight controller. This is the critical asymmetry
between "command the drone" (broad) and "command the controller" (narrow).

---

## 3. The WiFi webserver is the broadest command surface on the TX

When the E8 enters WiFi mode (button hold, or auto-WiFi timeout when no CRSF is
detected — see `research/elrs-wifi-mode-entry.md`), it runs an AsyncWebServer
(`src/lib/WIFI/devWIFI.cpp:1175 startServices`). The registered endpoints, from
source:

| Endpoint | Method | Sink | Impact |
|---|---|---|---|
| `/` + captive-portal URIs | GET | `WebUpdateHandleRoot` | Web UI (read) |
| `/config` | GET | `GetConfiguration` | **Full config exfil** (rate, power, UID, button mapping, WiFi settings) |
| `/config` | POST | `UpdateConfiguration` (JSON) | **Full config write** (rate, power, UID, button mapping, etc.) |
| `/options.json` | GET/POST | `getFile` / `UpdateSettings` | Options read/write |
| `/hardware.json` | GET/POST | `getFile` / `putFile` | Hardware config read/write |
| `/firmware.bin` | GET | `WebUpdateGetFirmware` | Firmware download |
| `/update` | POST | `WebUploadResponseHandler` / `WebUploadDataHandler` | **Firmware upload** (unsigned) |
| `/forceupdate` | POST | `WebUploadForceUpdateHandler` | **Forced firmware upload** |
| `/reboot` | GET | `HandleReboot` | **Reboot the controller** |
| `/reset` | GET | `HandleReset` | **Factory reset** (wipes UID, rebind required) |
| `/cw` | POST | `HandleContinuousWave` | **Continuous-wave RF transmission** |
| `/udpcontrol` | POST | `WebUdpControl` | UDP control (TX-only) |
| `/buttons` | POST | `WebUpdateButtonColors` | Button action remap |
| `/import` | POST | `ImportConfiguration` (32 KB) | **Import arbitrary config JSON** |
| `/connect`, `/sethome`, `/forget`, `/access`, `/networks.json` | POST/GET | WiFi client-mode controls | Reconfigure the controller's own WiFi client |
| `/options.json` (JSON) | POST | `UpdateSettings` | Settings write |

**No authentication on any endpoint.** `Access-Control-Allow-Origin: *` is set
globally. The firmware update path (`/update`, `/forceupdate`) is **unsigned** —
this is the documented CSRF RCE lane from `new-attack-surfaces-deep-research-
2026-10-29.md` §4.

**Reachability question (the gate):** the E8 is an *integrated* TX — its internal
handset bus always has CRSF, so auto-WiFi (which triggers on *absence* of CRSF)
does **not** fire in normal operation (see `research/rf-disruption-feasibility.md`
conclusion: "RF disruption Viable? No — internal bus always active"). The
remaining entry paths to WiFi mode on the E8 are:
1. **Button hold** (the E8's physical buttons, wired to the ELRS button driver).
   Requires physical access.
2. **CRSF parameter write** from the handset UI (Lua script / handset command —
   `research/elrs-wifi-mode-entry.md` §1.4). Requires the handset to be in
   control.
3. **Vendor tool** (the EMAX Windows tool). Requires USB + the vendor binary.

None of these is wirelessly reachable in the normal operational state. So the
WiFi webserver, while the broadest command surface, is **not wirelessly
reachable** against the E8 in its integrated form. It is reachable if the E8 is
already in WiFi mode (e.g., the user left it in update mode, or a standalone
module scenario).

---

## 4. The USB CDC path is the most practically reachable command path

The E8's USB CDC port is:
- **Always present** (the E8's only external interface when not in WiFi mode).
- **Unauthenticated** (the firmware reads bytes and feeds them to the CRSF/MSP
  parser with no handshake, token, or pairing check).
- **Already characterized** (`E8-SERIAL-CHARACTERIZATION.md`,
  `research/e8-protocol-reconstruction.md`): 115200 8N1, the EMAX vendor read
  command (`02 01` + 62 zeros → 17-byte response) works, the write path is not
  yet fully decoded (needs a vendor-tool strace of a Write/Update click).

**The command primitive here is: send a CRSF frame or MSP packet over USB CDC
and have the TX firmware act on it.** The firmware path is:
```
USB CDC bytes → HandleUARTin (tx_main.cpp:1131)
  → crsfParser.processBytes(&usbConnector, buf, size)   [CRSF path]
  → ParseMSPData → ProcessMSPPacket                       [MSP path]
  → isThisAMavPacket → MAVLink mode switch                [MAVLink path]
```

**What this reaches:**
- **CRSF path:** `crsfParser` parses CRSF frames. RC channel frames (0x16) set
  the TX's internal stick state (which is then transmitted over the ELRS link to
  the RX — i.e., USB CRSF injection can **override the pilot's stick inputs on
  the air link**). Command frames (0x32) and parameter frames (0x2C/0x2D) route
  through the CRSF router to the appropriate endpoint.
- **MSP path:** the narrow TX-side MSP handler from §2 (power calibration, VTX
  config, backpack ptr).
- **MAVLink path:** if the first packet is a valid MAVLink frame, the TX
  switches to `TX_MAVLINK_MODE` and the link mode changes — this is a
  state-changing link-mode transition.

**The highest-value USB-CDC command primitive is CRSF RC-channel injection:**
an attacker with USB access to the E8 can send `CRSF_FRAMETYPE_RC_CHANNELS_PACKED`
(0x16) frames with attacker-controlled stick values. The TX firmware accepts
them (no origin check — `usbConnector` is a valid CRSF source) and transmits
them over the ELRS link to the bound RX, **overriding the pilot's sticks on the
air link**. This is the same "rc_override" class as
`crsf-injection-poc-analysis.md` §7, but delivered over the controller's USB
port instead of the drone's CRSF bus.

---

## 5. What "issue commands to the controller" means, ranked by reachability

| Rank | Command | Path | Reachability | Sink | Value |
|---|---|---|---|---|---|
| 1 | **RC channel override** (pilot stick takeover on air link) | A (USB CDC) | **Always reachable** (USB plugged in, no auth) | `crsfParser` → ELRS TX → RX | Critical: full flight control via the controller |
| 2 | **CRSF command/parameter frames** (device ping, param read/write, bind disrupt) | A (USB CDC) | Always reachable | `crsfParser` → CRSF router | High: recon, config exfil, bind disruption |
| 3 | **MSP power-calibration set** (stop hwTimer, alter power) | A (USB CDC) or C (over-air, if bound RX) | USB: always; over-air: requires bound-RX position | `OnPowerSetCalibration` → `hwTimer::stop()` | Medium: state-changing, bounded |
| 4 | **MAVLink mode switch** (change link mode) | A (USB CDC) | Always reachable (first packet) | `config.SetLinkMode(TX_MAVLINK_MODE)` | Medium: link-mode state change |
| 5 | **WiFi webserver commands** (reboot, reset, config write, firmware upload, CW TX) | B (WiFi) | **Not wirelessly reachable** in integrated E8 (auto-WiFi needs no-CRSF, which never happens); reachable if already in WiFi mode | `HandleReboot`, `HandleReset`, `UpdateConfiguration`, `WebUploadDataHandler`, `HandleContinuousWave` | Critical *if* WiFi mode is entered; the firmware-upload path is the RCE lane |
| 6 | **Over-air MSP injection** (power cal, VTX config) | C (ELRS uplink) | Requires the attacker to be the bound RX or forge frames accepted by the TX's uplink parser (UID + nonce + channel) | `ProcessMSPPacket` | Medium: requires RX-position or frame-forging |

**The practical "issue commands to the controller" PoC is rank 1-2 over USB
CDC.** It does not require RF, does not require the E8 to be in WiFi mode, does
not require flashing, and does not require the drone. It requires only USB
access to the E8 (which the operator has — the device is on the bench, plugged
in).

---

## 6. The PoC round decomposition (GATED — no device interaction yet)

Per the `poc-development` skill, the PoC is decomposed into independently
reviewable rounds. Each round produces one artifact; the composition is the
capability. **None of these rounds transmit RF or flash the E8.**

| Round | Scope | Artifact | Authorization check |
|---|---|---|---|
| 1 | **USB-CDC CRSF reachability probe** | `e8_crsf_probe.py` — open `/dev/ttyACM0`, send a single `CRSF_FRAMETYPE_DEVICE_PING` (0x28) broadcast, observe whether the TX responds on the same port (CRSF extended header response) or whether the TX's ELRS link state changes (observable via passive SDR capture of the air link, if available). Question: "Does a CRSF frame sent over USB reach the CRSF parser?" | Offline: the probe only opens the port the vendor tool already opens. No write to EMAX config. No RF. |
| 2 | **CRSF RC-channel frame construction** | `e8_crsf_rc_frame.py` — build a valid `CRSF_FRAMETYPE_RC_CHANNELS_PACKED` (0x16) frame with 16 channels, attacker-controlled values, valid CRC-8 (poly 0xD5). Validate the frame offline against the CRSF spec (TBS CRSF, commit-pinned). | Offline: frame construction only. No transmission. |
| 3 | **CRSF command/parameter frame construction** | `e8_crsf_cmd_frames.py` — build `CRSF_FRAMETYPE_COMMAND` (0x32) and `CRSF_FRAMETYPE_PARAMETER_READ` (0x2C) frames for the specific command sub-ids of interest (device ping, param read, bind disrupt). | Offline: frame construction only. |
| 4 | **MSP-over-USB frame construction** | `e8_msp_frame.py` — build MSP packets for the TX-side sinks (`MSP_ELRS_FUNC` + `MSP_ELRS_POWER_CALI_GET`/`SET`, `MSP_SET_VTX_CONFIG`). Validate against `msptypes.h`. | Offline: frame construction only. |
| 5 | **Integration test against the owned E8** | `e8_crsf_integration.py` — send the rank-1-2 frames from rounds 2-3 over USB CDC to the operator's owned E8 and observe the effect (CRSF response on USB, or a change in the air-link telemetry captured passively). **This round requires explicit operator sign-off** because it sends CRSF frames to a live (but owned, grounded) controller. | **GATED:** operator sign-off required. No RF TX. No flashing. Props N/A (controller, not drone). The observable is the CRSF response or a passive air-link change, not a drone behavior change. |

**Round 5 is the gate.** Rounds 1-4 are offline and can proceed immediately
under the current scope. Round 5 requires a separate operator decision because
it sends command frames to the live E8 (even though the E8 is owned and
grounded, the no-flashing rule and the "bench-only + human sign-off" control
apply).

**The RCE gate (`rce-gate-joern` T1/T2):** the TX-side MSP handler (§2) is
narrow — there is no general `processMspPacket` dispatch, no `system()`, no
shell, no arbitrary write primitive in the MSP path. The WiFi webserver's
firmware-upload path (§3) is the RCE-relevant sink (unsigned firmware upload →
code execution on the next boot), but it is not wirelessly reachable in the
integrated E8. **T1/T2 verdict for "issue commands to the controller":** the
command primitive is T2 (a stable, bounded command injection over USB CDC —
RC override, param write, VTX config), not T1 (RCE). The RCE path is the WiFi
firmware upload, which is T3 at best (reachable only if WiFi mode is entered,
and not wirelessly reachable in the integrated form). The PoC objective is
**T2 command injection**, not T1 RCE, unless the operator separately authorizes
entering WiFi mode on the E8 (button hold or vendor tool) to reach the
firmware-upload sink.

---

## 7. Gating summary and what is NOT authorized by this document

- **NOT authorized:** RF transmission from or to the E8, flashing the E8,
  entering WiFi mode on the E8 (button hold, vendor tool, or otherwise),
  sending CRSF/MSP frames to the live E8 (round 5), any interaction with the
  TinyHawk.
- **Authorized (offline, current scope):** rounds 1-4 (probe design, frame
  construction, offline validation against pinned spec/source).
- **The no-flashing rule is absolute** and is not relaxed by any finding in
  this document. The WiFi firmware-upload path is documented as an analysis
  target, not as an authorized operation.

## 8. Evidence and next steps

- Source: `firmware/src/ExpressLRS-4.1.0/src/src/tx_main.cpp` (HandleUARTin
  :1131, ProcessMSPPacket :1056, ParseMSPData :1103, SendRxWiFiOverMSP :948,
  OnPowerSetCalibration :975), `src/lib/WIFI/devWIFI.cpp` (startServices
  :1175), `src/lib/MSP/msptypes.h`, `src/include/crsf_protocol.h`,
  `src/src/rx_main.cpp:964` (RX-side bind handling, for contrast).
- Existing tools that can be extended: `tools/e8_read.py` (vendor read),
  `tools/crsf-packet-tool.py` (CRSF frame builder), `tools/elrs_core/` (codec).
- Next (offline): build the round-2-3-4 frame constructors against the pinned
  CRSF spec and `msptypes.h`, validate offline, and present the round-5
  integration test to the operator for the sign-off decision.
- Roadmap: register as RQ23 (GATED) in `PROJECT-ROADMAP.md`.
