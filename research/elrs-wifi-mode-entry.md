# ELRS WiFi Mode: Entry Methods, Attack Surface, and E8 Considerations

**Date:** 2026-10-15
**Source:** ExpressLRS 4.1.0 source analysis (`src/lib/WIFI/devWIFI.cpp`,
`src/lib/OPTIONS/options.cpp`, `src/lib/BUTTON/devButton.cpp`, `src/src/tx_main.cpp`)
**Scope:** Documentation only. No flashing, settings writes, fuzzing, or RF transmission.

> **Constraint reminder:** The owner has explicitly prohibited flashing the E8
> controller. This document covers WiFi mode entry via normal operational
> methods (button presses, timeouts) and passive observation only.

---

## 1. WiFi Mode Entry Methods

ELRS devices (both TX and RX) enter WiFi mode through several paths. The
source-verified methods for ELRS 4.1.0 are:

### 1.1 Button Press (RX only — hardcoded default)

For receivers, the button actions are hardcoded in `devButton.cpp`:

| Action | Press Type | Duration | Effect |
|---|---|---|---|
| `ACTION_BIND` | Long press | 1.5s (count=2) | Enter binding mode |
| `ACTION_START_WIFI` | Long press | 5.0s (count=9) | Enter WiFi update mode |
| `ACTION_RESET_REBOOT` | Long press | 12.0s (count=23) | Factory reset + reboot |

For transmitters, button actions are **configurable** via the web UI or Lua script
(`config.GetButtonActions()`). The default mapping assigns `ACTION_START_WIFI`
to a button action, but the specific press pattern depends on the target build.

**Source:** `devButton.cpp:20-26`, `devWIFI.cpp:1027-1029`

### 1.2 Auto-WiFi Timeout (TX — No CRSF Detected)

This is the most attack-relevant entry method for transmitters.

**TX behavior** (`devWIFI.cpp:1409-1416`):
```
if (firmwareOptions.wifi_auto_on_interval != -1
    && webserverPreventAutoStart == false
    && connectionState < wifiUpdate
    && !wifiStarted)
{
    DBGLN("No CRSF ever detected, starting WiFi");
    setWifiUpdateMode();
}
```

The TX enters WiFi mode automatically when:
1. `wifi_auto_on_interval` is not `-1` (i.e., auto-WiFi is enabled)
2. `webserverPreventAutoStart` is `false` (no CRSF/UART has ever been detected)
3. The device is not already in WiFi update mode
4. WiFi hasn't already started

**Critical detail:** `webserverPreventAutoStart` is set to `true` the moment
a CRSF connection to the handset is detected (`tx_main.cpp:724`). This means:
- If the TX powers on **without** a handset connection (e.g., standalone module),
  it will enter WiFi mode after the configured interval
- If the TX powers on **with** a handset (EdgeTX radio), CRSF is detected
  immediately and auto-WiFi is permanently blocked for that boot cycle

**Default interval:** The `wifi_auto_on_interval` defaults to `-1` (disabled)
unless the firmware was built with `-DAUTO_WIFI_ON_INTERVAL=<seconds>`. When
set, common values are 20-60 seconds. The `start()` function returns this
interval as the initial timeout (`devWIFI.cpp:1363-1367`).

### 1.3 Auto-WiFi Timeout (RX — Disconnected)

**RX behavior** (`devWIFI.cpp:1417-1431`):
```
if (firmwareOptions.wifi_auto_on_interval != -1
    && !webserverPreventAutoStart
    && (connectionState == disconnected))
```

The RX enters WiFi mode when:
1. Auto-WiFi is enabled
2. The RX is in `disconnected` state (no TX link)
3. If in binding mode, a minimum 60-second delay is enforced regardless of the
   configured interval

**Attack relevance for TinyHawk III:** The TinyHawk III uses an **SPI ELRS
receiver compiled into Betaflight** (not a standalone ESP32 RX). This SPI RX
does **not have WiFi capability** — there is no ESP chip to run WiFi. The
auto-WiFi timeout path does not apply to the drone itself.

### 1.4 Lua Script / Handset Command

On TX modules connected to an EdgeTX/OpenTX radio, the Lua ELRS configuration
script can trigger WiFi mode through the handset UI. This sends a command over
CRSF that calls `setWifiUpdateMode()`.

### 1.5 Three-Power-Cycle Boot (some builds)

Some ELRS builds enter WiFi/bootloader mode after three rapid power cycles.
This is build-dependent and not a universal ELRS feature. Not confirmed for
EMAX targets specifically.

---

## 2. WiFi Access Point Characteristics

When WiFi mode activates, the ESP32 (or ESP8266) starts a web server.

### 2.1 AP Mode (Default — No Home WiFi Configured)

Source-verified defaults from `options.cpp:17-24`:

| Parameter | TX Value | RX Value |
|---|---|---|
| **SSID** | `ExpressLRS TX` | `ExpressLRS RX` |
| **Password** | `expresslrs` | `expresslrs` |
| **AP IP** | `10.0.0.1` | `10.0.0.1` |
| **Subnet** | `255.255.255.0` | `255.255.255.0` |
| **mDNS hostname** | `elrs_tx.local` | `elrs_rx.local` |
| **Web server port** | 80 (HTTP, no TLS) | 80 (HTTP, no TLS) |

**The password is a well-known default.** It is `expresslrs` for all stock
ELRS builds unless overridden at compile time. This is not a secret.

### 2.2 STA Mode (Home WiFi Configured)

If a "home WiFi" network was previously configured (via the web UI's
`/sethome` endpoint), the device first attempts to connect as a station to
that network. If connection fails after 30 seconds, it falls back to AP mode
(`devWIFI.cpp:1283-1287`).

**Attack note:** If an attacker knows or can guess the home WiFi SSID, they
could set up a rogue AP with that SSID. The ELRS device would connect to it,
placing both on the same network. The web UI has **no authentication** regardless
of WiFi mode.

### 2.3 mDNS Service Advertisement

The device advertises via mDNS (`devWIFI.cpp:1073-1143`):

| TXT Record | Value |
|---|---|
| `vendor` | `elrs` |
| `target` | Target name (e.g., `EMAX_2400_TX`) |
| `device` | Device name |
| `product` | Product name |
| `version` | Firmware version |
| `options` | Build options including `AUTO_WIFI_ON_INTERVAL` |
| `type` | `tx` or `rx` |

**mDNS instance name format:** `<hostname>_<MAC_ADDRESS>` (colons stripped)

This mDNS advertisement **leaks the firmware version, target name, and build
options** to anyone on the same network.

### 2.4 Captive Portal

The web server implements captive portal detection for Windows, Android, Apple,
Ubuntu, and Firefox (`devWIFI.cpp:1145-1179`). All captive portal probe URLs
redirect to the root page. A DNS server on port 53 responds to all queries
with the AP's IP address (`devWIFI.cpp:1245`).

---

## 3. Web Interface Endpoints

All endpoints are **unauthenticated**. CORS headers allow requests from any
origin (`Access-Control-Allow-Origin: *`). Source: `devWIFI.cpp:1181-1259`.

### 3.1 Read-Only / Information Endpoints

| Endpoint | Method | Returns |
|---|---|---|
| `/config` | GET | **Full device configuration as JSON** — includes UID, firmware options, button mappings, radio settings, product name, version, target name |
| `/config?export` | GET | Export-mode config — includes per-model settings (rate, telemetry, power, switch mode, antenna, backpack config) |
| `/options.json` | GET | Firmware build options |
| `/hardware.json` | GET | Hardware pin definitions |
| `/networks.json` | GET | Scanned WiFi networks list |
| `/firmware.bin` | GET | **Download the running firmware binary** |
| `/cw` | GET (no args) | Returns radio count and center frequency |

### 3.2 Write / Modification Endpoints

| Endpoint | Method | Effect |
|---|---|---|
| `/config` | POST (JSON) | **Update device configuration** — RX: serial protocol, failsafe, model ID, PWM config, UID. TX: button actions |
| `/import` | POST (JSON) | **Import full TX configuration** — rate, telemetry, power, fan, VTX, backpack settings (TX only, max 32KB) |
| `/options.json` | POST | Update firmware options (requires matching `flash-discriminator`) |
| `/update` | POST (multipart) | **Upload and flash new firmware** |
| `/forceupdate` | POST | Force-flash firmware (bypasses target name check) |
| `/sethome` | GET | Set home WiFi network + password (stored to flash) |
| `/forget` | GET | Clear stored home WiFi credentials |
| `/hardware.json` | POST | Upload hardware pin definitions |
| `/reboot` | GET | **Reboot the device** |
| `/reset` | GET | **Factory reset** (options, hardware, config, LR1121 cal) |
| `/access` | GET | Switch to AP mode |
| `/connect` | GET | Switch to STA mode |
| `/cw` | GET (with `radio` arg) | **Start continuous wave transmission** (RF transmission!) |
| `/udpcontrol` | POST | Start/stop WiFi joystick UDP service (TX+ESP32 only) |

### 3.3 Crown Jewel: `/config` Response

The `/config` GET response (`GetConfiguration()`, `devWIFI.cpp:370-533`) returns:

```json
{
  "options": { ... },
  "config": {
    "uid": [0x__, 0x__, 0x__, 0x__, 0x__, 0x__],
    "button-actions": [ ... ]
  },
  "settings": {
    "product_name": "...",
    "lua_name": "...",
    "uidtype": "Flashed|Overridden|Not set (using MAC address)",
    "ssid": "...",
    "mode": "STA|AP",
    "target": "...",
    "version": "...",
    "module-type": "TX|RX",
    "radio-type": "SX128X|SX127X|LR1121"
  }
}
```

**The `uid` array is the 6-byte binding UID** — the complete pairing secret
that seeds FHSS, CRC, and enables packet forgery. A single unauthenticated
`GET /config` request extracts it.

---

## 4. Attack-Relevant Behavior

### 4.1 Forced WiFi Mode via RF Disruption (TX Auto-WiFi)

**Chain concept:**
```
Jam/disrupt 2.4 GHz ELRS link
  → TX never detects CRSF from handset (standalone module scenario)
  → OR TX module is powered without handset
  → wifi_auto_on_interval expires
  → ESP32 starts open AP "ExpressLRS TX" / password "expresslrs"
  → Attacker connects to 10.0.0.1
  → GET /config → UID in plaintext
  → Derive FHSS + CRC → forge packets
```

**Limitations for TX modules integrated in handsets (like the E8):**
The E8 is a complete controller, not a standalone TX module. When the E8 powers
on, if its internal CRSF/serial bus is detected, `webserverPreventAutoStart`
is set immediately (`tx_main.cpp:724`). **RF disruption of the air link does
NOT trigger auto-WiFi on the TX** — the CRSF connection is between the handset
MCU and the ELRS module over a wired serial bus, not over the air.

The auto-WiFi TX path fires only when **no CRSF UART activity is ever
detected**, which would happen if:
- The ELRS module is powered outside its host handset
- The handset's CRSF output is disabled or broken
- The firmware is misconfigured for the wrong UART

### 4.2 Forced WiFi Mode via RF Disruption (RX Auto-WiFi)

For standalone ESP32-based ELRS receivers (not the TinyHawk III's SPI RX):
```
Jam/disrupt ELRS link
  → RX connectionState becomes "disconnected"
  → wifi_auto_on_interval expires
  → RX starts open AP "ExpressLRS RX"
  → Attacker connects, extracts UID, reconfigures
```

**Does NOT apply to TinyHawk III:** The SPI ELRS RX has no WiFi hardware.

### 4.3 WiFi Mode Duration

Once in WiFi mode, the device **stays in WiFi mode indefinitely** until:
- A reboot is triggered (via `/reboot` endpoint or power cycle)
- A firmware update completes (auto-reboots after 200ms)
- A factory reset completes (auto-reboots after 100ms)

There is **no automatic timeout** that exits WiFi mode. The web server runs
continuously with the hardware timer stopped and the radio powered down
(`devWIFI.cpp:1039-1052`).

### 4.4 What WiFi Mode Disables

When WiFi mode starts (`startWiFi()`, `devWIFI.cpp:1033-1071`):
1. The hardware timer is stopped (`hwTimer::stop()`)
2. The RF radio is powered to minimum, then stopped (`Radio.End()`)
3. VTX SPI is disabled (RX+ESP32)
4. The connection state is set to `wifiUpdate`

**The drone link is completely dead during WiFi mode.** If the TX enters WiFi
mode, it stops transmitting to the drone. If the RX enters WiFi mode, it stops
receiving from the TX.

### 4.5 Firmware Download and Analysis

The `/firmware.bin` endpoint serves the **currently running firmware image**
directly from flash. An attacker with WiFi access can download the entire
firmware for offline analysis — extracting binding phrases, keys, configuration,
and identifying the exact ELRS version and target.

### 4.6 Settings Modification via `/config` POST

The `/config` POST endpoint on an RX allows writing:
- **UID** (the binding secret)
- Serial protocol
- Failsafe mode
- Model ID
- PWM channel configuration

On a TX, `/config` POST allows writing button action mappings, and `/import`
allows writing the complete model configuration set.

---

## 5. E8-Specific Considerations

### 5.1 Hardware Identity Uncertainty

The E8's MCU and installed firmware version remain **unconfirmed** (see
`HARDWARE-IDENTITY.md` and `E8-USB-CHARACTERIZATION.md`). The closest official
ELRS targets are:

| Target | MCU | Description |
|---|---|---|
| `EMAX_2400_TX` | ESP32 + SX1280 | EMAX OLED TX (has screen) |
| `EMAX_NANO_2400_TX` | ESP32 + SX1280 | EMAX Nano TX |
| Base: `Unified_ESP32_2400_TX` | ESP32 | Generic ESP32 2.4 GHz TX |

The E8 is a screenless controller. If it runs standard ELRS firmware on an
ESP32, WiFi mode behavior should match the source analysis above. However:

### 5.2 EMAX Custom Firmware Possibility

The EMAX E8 ships with a **custom configurator** (`EMAX E8 ELRS Configurator.exe`,
branded "Serial Update Tool 2.1") that uses a **proprietary serial protocol**
(not standard CRSF or MSP). Key evidence from `e8-protocol-reconstruction.md`:

- 64-byte read command: `02 01` + 62 zero bytes
- 17-byte config response with UID at bytes [5..10]
- CRC-16/XMODEM framing
- Baud: 115200 8N1

This custom protocol suggests EMAX may run a **modified or custom ELRS build**
on the E8. This could mean:
- **Different WiFi SSID/password** (vendor-customized build options)
- **Different or disabled auto-WiFi interval**
- **Modified or absent web UI endpoints**
- **Additional proprietary endpoints or protections**

### 5.3 E8 WiFi Mode — What We Don't Know

| Question | Status |
|---|---|
| Does the E8 have WiFi capability? | **Unconfirmed.** ESP32 targets have WiFi; the E8's MCU is not positively identified. Related EMAX targets use ESP32. |
| What SSID does the E8 broadcast in WiFi mode? | **Unknown.** Could be standard `ExpressLRS TX` or an EMAX-customized name. |
| Is auto-WiFi enabled in the E8's firmware? | **Unknown.** Depends on build flags. |
| Does the E8's internal CRSF bus prevent auto-WiFi? | **Likely yes** if the handset MCU communicates over CRSF to the ELRS module at boot. |
| Can WiFi mode be triggered by button press? | **Unknown.** The E8 has at least one button; its action mapping is not confirmed. |
| Does the E8's web UI expose the standard ELRS endpoints? | **Unknown.** The custom configurator suggests possible modifications. |

### 5.4 What Would Confirm E8 WiFi

A safe, non-destructive test (requires battery power + power switch ON):
1. Power on the E8 without binding to any receiver
2. Wait 60-120 seconds
3. Scan for WiFi networks containing "ExpressLRS" or "EMAX" or "E8"
4. If an AP appears, connect with password `expresslrs` (or try open)
5. Browse to `http://10.0.0.1` or `http://elrs_tx.local`

This test does not write settings, flash firmware, or transmit RF.

---

## 6. Operator Checklist: E8 WiFi Mode Lab Procedure

**Prerequisites:**
- E8 controller with charged battery
- Laptop/phone with WiFi
- WiFi scanning tool (e.g., `nmcli device wifi list` on Linux, WiFi settings on phone)

**Procedure (read-only, no writes):**

### Step 1: Prepare Monitoring
```bash
# On the Ubuntu lab machine, start continuous WiFi scan
watch -n 2 'nmcli device wifi list --rescan yes 2>/dev/null | grep -i -E "express|elrs|emax|e8"'
```

### Step 2: Power On E8
1. Ensure battery is charged (red LED should be visible)
2. Turn power switch ON
3. Note the time — WiFi mode (if auto-WiFi is enabled) typically activates
   20-60 seconds after power-on if no CRSF link is detected

### Step 3: Wait and Watch
- Monitor WiFi scan output for 120 seconds minimum
- If the E8's internal CRSF bus activates at boot, auto-WiFi may be blocked
- Some ELRS builds require a button press (hold ~5 seconds) to enter WiFi mode

### Step 4: Try Button-Triggered WiFi (if auto-WiFi doesn't appear)
1. With E8 powered on, press and hold the button for ~5 seconds
2. Release and watch WiFi scan for a new AP
3. If no result, try different hold durations (3s, 5s, 8s, 10s)

### Step 5: Connect to WiFi AP (if found)
```bash
# Connect to the ELRS AP
nmcli device wifi connect "ExpressLRS TX" password "expresslrs"
# Or if a different SSID appears:
nmcli device wifi connect "<observed_ssid>" password "expresslrs"
```

### Step 6: Verify Web UI (read-only)
```bash
# Check if the web server is running
curl -s http://10.0.0.1/config | python3 -m json.tool

# If 10.0.0.1 doesn't work, try mDNS
curl -s http://elrs_tx.local/config | python3 -m json.tool

# Note the response — especially the "uid" array and "version"
```

### Step 7: Document Results
Record:
- Whether WiFi AP appeared (and how — auto or button)
- SSID name and whether password was required
- IP address of the AP
- `/config` response (redact UID to `work/` per project policy)
- mDNS TXT records: `avahi-browse -r _http._tcp`

### Step 8: Power Off
- Turn E8 power switch OFF
- Disconnect WiFi on the lab machine

**Do NOT:**
- Upload firmware via `/update`
- POST to `/config` or `/options.json`
- Call `/reset`, `/reboot`, or `/forceupdate`
- Call `/cw` (triggers RF continuous wave transmission)
- Modify any settings via `/sethome`

---

## 7. Summary: WiFi Mode in the Attack Kill Chain

| Stage | Action | Tool/Method |
|---|---|---|
| **Recon** | Scan for ELRS WiFi APs (`ExpressLRS TX/RX`, password `expresslrs`) | `nmcli`, `airodump-ng` |
| **Force entry** (if needed) | Jam 2.4 GHz to prevent ELRS link → auto-WiFi timeout on standalone RX/TX modules | Ettus B205/B210, or targeted ELRS band noise |
| **Connect** | Join AP with password `expresslrs`, IP `10.0.0.1` | `nmcli`, any WiFi client |
| **Extract UID** | `GET /config` → `config.uid` array | `curl http://10.0.0.1/config` |
| **Extract firmware** | `GET /firmware.bin` | `curl -o fw.bin http://10.0.0.1/firmware.bin` |
| **Identify version** | mDNS TXT records or `/config` response → version, target, build options | `avahi-browse` or `curl` |
| **Derive FHSS/CRC** | UID → MD5-based FHSS sequence + CRC initializer (per ELRS source) | `tools/elrs-uid-rainbow.py` (reverse), or direct derivation |
| **Forge packets** | With UID + FHSS + CRC, construct valid ELRS frames | Custom SDR tool (Ettus) or modified ELRS TX |

**Key limitation for E8 specifically:** The E8 is an integrated controller, not
a standalone module. Its internal CRSF bus likely activates at boot, blocking
the auto-WiFi timeout path. WiFi entry on the E8 probably requires either a
button press (physical access or the button press pattern) or an EMAX-specific
trigger. The E8's actual WiFi behavior has not been lab-verified.

---

## Appendix A: Source File Reference

| File | Key Content |
|---|---|
| `src/lib/WIFI/devWIFI.cpp` | WiFi mode state machine, web server endpoints, AP/STA logic |
| `src/lib/WIFI/devWIFI.h` | WIFI_device extern |
| `src/lib/OPTIONS/options.cpp` | Default SSID/password/hostname/IP, `wifi_auto_on_interval` handling |
| `src/lib/OPTIONS/options.h` | `firmware_options_t` struct with `wifi_auto_on_interval` |
| `src/lib/BUTTON/devButton.cpp` | RX hardcoded button actions (5s hold → WiFi) |
| `src/src/tx_main.cpp` | `webserverPreventAutoStart` set on CRSF detection |
| `src/src/rx_main.cpp` | RX connection state management |
| `src/include/common.h` | `ACTION_START_WIFI` enum definition |
| `src/html/src/pages/wifi-panel.js` | Web UI WiFi configuration panel |

## Appendix B: Related Project Documents

- `research/layer2-wireless-attack-research.md` — WiFi attack surface analysis (exploratory, paused)
- `HARDWARE-IDENTITY.md` — E8 hardware identity evidence
- `research/E8-USB-CHARACTERIZATION.md` — USB descriptor and serial probe results
- `research/e8-protocol-reconstruction.md` — EMAX custom serial protocol decode
- `SESSION-NOTES.md` §20 — Current ELRS pair benchmark direction
