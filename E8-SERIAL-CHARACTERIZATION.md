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

## 3. Diagnosis: Device Needs Battery Power

The E8 transmitter requires its internal battery to be charged and the power
switch to be engaged. Evidence:

1. **USB CDC without serial data** — The USB descriptor enumerates even in
   low-power/charging mode (the USB PHY on the MCU or bridge chip is powered by
   the USB 5V rail), but the application firmware is not running.
2. **Consistent with prior session** — The Ubuntu lab session recorded the same
   behavior: the device showed as `/dev/ttyACM0` but produced no CRSF responses.
   The SESSION-NOTES.md explicitly states: "The E8 almost certainly needs a
   battery installed and the power switch turned on."
3. **EMAX E8 form factor** — This is a handheld RC transmitter with a power
   switch and internal/removable battery. The USB port serves dual purpose:
   charging and serial communication, but only the charging function works
   without the main power switch.

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

## 5. Next Steps — When E8 Is Powered On

### Immediate (serial characterization)

```powershell
# 1. List ports to confirm COM assignment (may change)
python3 tools/elrs-serial-probe.py --list-ports

# 2. Passive CRSF capture at 460800 (ELRS default baud)
python3 tools/elrs-serial-probe.py -p COM4 -b 460800 --capture work/e8-capture.bin

# 3. Active probe with parameter dump
python3 tools/elrs-serial-probe.py -p COM4 -b 460800 --probe --dump-params

# 4. If CRSF fails, try MSP at 115200 (Betaflight passthrough)
# Use the msp_probe.py script from this session's scratchpad
```

### Extract and document

1. **Firmware version** — ELRS version, build date, hardware ID
2. **UID bytes** — Check if exposed via CRSF device info or parameters
3. **Binding phrase status** — Whether a custom phrase is set
4. **WiFi configuration** — Auto-WiFi-on timer, SSID, password
5. **TX power settings** — Maximum power, regulatory domain
6. **UART/serial config** — Which serial protocols are enabled on which ports
7. **Failsafe behavior** — What happens when the link is lost

### Security assessment

1. **Can MSP commands be sent without authentication?** — Expected: yes
2. **Can firmware be reflashed without a physical button press?** — Expected: yes (ESP32 UART boot)
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
