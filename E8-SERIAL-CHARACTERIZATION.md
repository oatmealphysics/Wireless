# EMAX E8 Controller — USB Serial Characterization

**Date:** 2026-10-15  
**Platform:** Windows 11 Pro (LENOVO), Python 3.12.10, pyserial 3.5  
**Connection:** Direct USB to PC

---

## 1. USB Device Identity

| Property | Value |
|---|---|
| **Port** | COM4 |
| **VID** | 0x8888 (34952) — non-standard/custom vendor ID |
| **PID** | 0x2001 (8193) |
| **Serial Number** | 00000001 |
| **Bus-Reported Description** | "USB Serial" |
| **USB Class** | 02 (Communications / CDC) |
| **USB SubClass** | 02 (Abstract Control Model) |
| **USB Protocol** | 01 |
| **Windows Driver** | usbser.inf (Microsoft built-in CDC driver) |
| **First Install Date** | 2026-10-14 21:22:09 |
| **USB Location** | Port 9, Root Hub (XHCI HS09) |
| **Device Status** | OK — "This device is working properly" |
| **Hardware Revision** | REV_0100 |

### Other COM port present

COM3 is a Prolific PL2303GC USB-to-serial adapter (VID 067B, PID 23A3, SN BDEID126810) — this is a separate device, not the E8.

### VID 0x8888 analysis

VID 0x8888 is not a registered USB Vendor ID with the USB-IF. It appears in some
Chinese-manufactured ESP32-based devices and custom STM32 CDC implementations.
The previous Linux session recorded this same VID:PID showing as `/dev/ttyACM0`
described as "Your Company USB Serial" — consistent with a generic or placeholder
USB descriptor, typical of development/prototype firmware or low-cost consumer
electronics that did not purchase a USB-IF vendor ID.

---

## 2. Serial Communication Test Results

### Protocols tested

| Protocol | Baud Rates Tested | Result |
|---|---|---|
| **MSP v1** (Betaflight) | 115200, 420000, 921600, 57600 | No response |
| **MSP v2** (Betaflight) | 115200 | No response |
| **CRSF** (ExpressLRS) | 115200, 420000, 460800, 921600, 57600, 9600, 256000 | No response |
| **MAVLink v1** heartbeat | 115200, 420000, 460800, 921600, 57600, 9600, 256000 | No response |
| **Betaflight CLI** (`#`, `version`, `status`) | 115200, 420000, 921600, 57600 | No response |
| **AT commands** | All above | No response |
| **Raw byte echo test** | All above | No response |

### Signal line tests

| DTR | RTS | Result |
|---|---|---|
| Default | Default | No data |
| High | Low | No data |
| Low | High | No data |
| High | High | No data |

### Boot/reset tests

| Method | Result |
|---|---|
| DTR toggle (low→high, 2s wait) | No data |
| Port open/close/reopen cycle | No data |
| 1200 bps touch (bootloader trigger) | No data |
| ESP32 boot detection at 74880 baud | No data |
| Long passive listen (10s at 115200) | No data |
| Long passive listen (10s at 460800) | No data |

### Summary

**Zero bytes received across all tests.** The device enumerates as a valid USB CDC
ACM endpoint but produces no serial data and does not respond to any protocol
tested. The port opens and closes without error.

---

## 3. Powered-On Testing (2026-10-15, controller powered on and connected)

With the E8 powered on, the device remains on COM4 with identical USB identity.
**The serial interface is still completely unresponsive.**

### Additional tests performed with device powered on

| Test Category | Tests Run | Result |
|---|---|---|
| **Serial configs** | 13 combinations (8N1, 8E1, 8O1, 7N1, 7E1, 8N2, etc.) | No response |
| **Flow control** | XON/XOFF, RTS/CTS, DSR/DTR | No response |
| **Baud rates** | 9600, 19200, 38400, 57600, 74880, 115200, 230400, 256000, 420000, 460800, 500000, 921600, 1000000, 2000000 | No response |
| **Handshake sequences** | 30+ patterns including 0x55, 0xAA, 0x7F (STM32 bootloader), ESP32 sync, CRSF ping variants, text commands, control bytes | No response |
| **BREAK signal** | Sent at 115200 | No response |
| **Boot triggers** | DTR reset cycle, 1200bps touch, ESP32 GPIO0 strapping | No response |
| **Control lines** | CTS, DSR, RI, CD all checked with DTR/RTS on/off | All FALSE at all times |
| **Windows mode command** | Configured port with DSR handshaking off, DTR=ON | No change |
| **Extended passive listen** | 5–15 seconds at each baud rate | Zero bytes received |

### Modem status line analysis

```
CTS  = False (even with RTS asserted)
DSR  = False (even with DTR asserted)
RI   = False
CD   = False
```

All modem status lines are permanently low. This indicates the USB CDC ACM
implementation either does not implement flow control, or the internal UART
bridge is not connected to an active MCU UART in normal operating mode.

### Diagnosis: Custom EMAX Configurator Protocol Required

The E8 does NOT use standard MSP, CRSF, MAVLink, or any standard CLI
protocol over USB serial. EMAX provides a **custom configurator application**
that communicates using a proprietary protocol:

- Download: Available from [EMAX Support](https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264)
  (hosted on Google Drive and Mega.nz)
- Workflow: "Connect USB-C → Click Connect → Click Read" (no special boot mode)
- The configurator can Read/Write device settings through dropdown menus

The configurator likely uses one of:
1. **USB CDC ACM class-specific control transfers** (SET_LINE_CODING,
   SEND_ENCAPSULATED_COMMAND) rather than bulk data endpoints
2. **A proprietary framing protocol** with a specific initialization handshake
   that none of our 30+ tested sequences matched
3. **Direct USB control transfers** that bypass the serial port abstraction

### What this means for CTF exploitation

The E8's serial interface is effectively **obscured by a proprietary protocol**.
This is security-by-obscurity, not security-by-design:
- The protocol can be reverse-engineered from the configurator application
- The USB interface has no authentication mechanism
- Once the protocol is known, arbitrary configuration changes are possible

---

## 4. Security-Relevant Observations (Pre-Power-On)

Even without serial communication, several attack-relevant facts are established:

### 4.1 USB Attack Surface

| Finding | Security Implication |
|---|---|
| **Generic VID 0x8888** — not a registered USB-IF vendor | The device cannot be uniquely fingerprinted by VID/PID alone; an attacker could spoof this identity with any USB device |
| **USB CDC ACM with no authentication** | When powered on, MSP/CRSF commands are expected to work without any handshake, token, or authentication |
| **Serial number "00000001"** | All units likely share this serial number — no per-device USB identity |
| **Windows generic driver (usbser.inf)** | No proprietary driver needed; any Windows/Linux/macOS system can open the port immediately |

### 4.2 Expected Attack Vectors (to confirm when powered on)

Based on the EMAX E8's architecture (ESP32 + SX1280, ELRS firmware) and the
existing research in this repository:

| Vector | Protocol | Authentication Required? | Expected |
|---|---|---|---|
| **MSP command injection** | MSP v1/v2 over USB | None expected | Full FC config read/write |
| **Firmware reflash via USB** | UART bootloader or ELRS Configurator | None expected (no physical button needed for ESP32 UART flash) |
| **WiFi firmware update** | ELRS WiFi mode | WPA2 with default password "expresslrs" | Full firmware replacement |
| **Binding phrase extraction** | CRSF parameter read | None expected | Reveals UID → enables OTA hijack |
| **Configuration dump** | MSP/CLI | None expected | Failsafe settings, UART config, feature flags |
| **Debug interface** | ESP32 JTAG (if exposed on pads) | Physical access | Full memory read/write, firmware extraction |

### 4.3 What the existing config tells us (TinyHawk III drone side)

From `EMAX_TINYHAWKF4SX1280.config`:

- **SPI ELRS receiver** — ELRS stack is compiled into Betaflight, not a separate chip
- **MSP API version 1.44** (Betaflight 4.3.1)
- **Features enabled:** RX_SPI, OSD, TELEMETRY
- **UART1 and UART2** are configured (TX/RX pins on PA09/PA10 and PA02/PA03)
- **No debug pins exposed** in the config (but hardware pads may exist)
- **expresslrs_domain = ISM2400** — confirms 2.4 GHz operation

---

## 5. Next Steps

### Priority 1: Reverse-engineer the EMAX configurator protocol

1. **Download the EMAX configurator** from the support page
2. **Run it with a USB sniffer** (USBPcap, Wireshark) to capture the exact
   USB transactions between the configurator and the E8
3. **Analyze the configurator binary** — likely an Electron/NW.js or .NET app;
   look for serial port initialization code, baud rate, and frame format
4. **Replicate the protocol** in a Python script for automated exploitation

### Priority 2: Alternative access vectors

1. **WiFi mode** — The ELRS TX module has a WiFi AP mode (default SSID
   "ExpressLRS TX", default password "expresslrs"). Try triggering it:
   - Via the Lua script on the radio (if paired)
   - By timeout (some ELRS builds auto-enable WiFi after 60s without connection)
   - WiFi AP at 10.0.0.1 provides web UI for firmware upload and config
2. **Firmware update mode** — Hold R4+ button + power button to enter serial
   flash mode (per EMAX manual). This might use a different USB protocol.
3. **Bootloader mode** — Try esptool.py with GPIO0 strapping if the ESP32
   boot pins are accessible

### Priority 3: Security assessment (once access is gained)

1. **Can MSP commands be sent without authentication?** — Expected: yes
2. **Can firmware be reflashed without a physical button press?** — Expected: yes
3. **Are debug interfaces exposed?** — Check for JTAG test pads on the PCB
4. **What failsafe behavior is configured?** — Critical for understanding DoS impact
5. **Is the WiFi password default?** — Check if "expresslrs" is still the AP password
6. **Can the binding phrase/UID be read over serial?** — Would enable OTA hijack

---

## 6. Files Produced This Session

| File | Location | Purpose |
|---|---|---|
| `E8-SERIAL-CHARACTERIZATION.md` | Repo root | This document |
| `msp_probe.py` | Session scratchpad | MSP v1/v2 + CLI probe script |
| `passive_listen.py` | Session scratchpad | Multi-protocol passive listener |
| `deep_probe.py` | Session scratchpad | DTR reset, boot detection, long listen |

The scratchpad scripts are session-local. The MSP probe script should be moved to
`tools/` and committed once validated against the powered-on device.
