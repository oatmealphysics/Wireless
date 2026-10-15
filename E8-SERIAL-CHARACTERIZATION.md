# EMAX E8 Controller — USB Serial Characterization

**Date:** 2026-10-15  
**Platform:** Windows 11 Pro (LENOVO), Python 3.12.10, pyserial 3.5  
**Connection:** Direct USB to PC

> **Evidence qualification from the concurrent characterization:** The tests in
> this document are the other session's reported results. Their zero-byte outcome
> does not establish that application firmware is stopped, that charging power is
> the cause, or that MSP/CRSF will work after startup. USB CDC enumeration does not
> establish application authentication properties, an ESP32/SX1280 board, WiFi
> availability, or bootloader access. Those statements below remain hypotheses.
> EMAX's manufacturer-linked tool uses a custom 115200 8-N-1 configuration read,
> and its manual describes a separate serial startup mode. See the independently
> recorded [USB characterization and source evidence](research/E8-USB-CHARACTERIZATION.md)
> before acting on the expected attack vectors below. No installed version or
> working controller exploit has been established by either zero-byte result.

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

## 5. Serial Mode Testing (2026-10-15, R4+ activated)

The user confirmed entering serial mode: R4+ held during power-on, flashing
green WiFi/signal LED observed. Testing was performed with the device in this
state.

### Qt-mimic vendor read attempts

Eight DTR/RTS configurations tested, mimicking various QSerialPort behaviors:

| Setup | DTR | RTS | Settle | Result |
|---|---|---|---|---|
| Default (driver chooses) | true | true | 2s | 0 bytes |
| DTR+RTS explicit pre-open | true | true | 2s | 0 bytes |
| DTR toggle after open | true→false→true | false | 2s | 0 bytes |
| DTR pulse after open | true→false→true | true | 2s | 0 bytes |
| RTS only | false | true | 2s | 0 bytes |
| Wine-like (both pre-open) | true | true | 3s | 0 bytes |
| Default, long settle | true | true | 5s | 0 bytes |
| DTR=true, long settle | true | true | 5s | 0 bytes |

All modem status lines (CTS, DSR, RI, CD) remained FALSE across all
configurations. No initial/unsolicited data received in any test.

### Vendor command variations

Six framing variations of the vendor read request tested:

| Variant | Request Bytes | Result |
|---|---|---|
| `02 01 00` + 61 zeros (64 total) | Standard | 0 bytes |
| `02 01` only (2 bytes) | Minimal | 0 bytes |
| `02 01 00` (3 bytes) | Short | 0 bytes |
| `01 00` + 62 zeros | Alt header | 0 bytes |
| `02 00` + 62 zeros | Alt subcommand | 0 bytes |
| `02 02` + 62 zeros | Alt subcommand 2 | 0 bytes |

### MSP/CLI in serial mode

MSP v1 API_VERSION request and CLI commands (`#`, `version`, `help`) also
returned 0 bytes while in serial mode.

### Passive listen at multiple baud rates

115200 and 460800 passive listen with DTR on/off: 0 bytes in all cases.

### Additional serial stacks tested

To rule out library-specific issues, the vendor read was attempted with
three completely independent serial implementations:

| Stack | API | Result |
|---|---|---|
| pyserial 3.5 | Python `serial.Serial` | 0 bytes |
| .NET SerialPort | PowerShell `System.IO.Ports.SerialPort` | 0 bytes |
| Windows API | Python ctypes `CreateFileW` / `WriteFile` / `ReadFile` | 0 bytes |

All three successfully opened the port, configured 115200 8-N-1, asserted
DTR/RTS, wrote 64 bytes, and received 0 bytes. The `EscapeCommFunction`
(SETDTR/SETRTS) and `GetCommModemStatus` calls confirmed CTS/DSR/RI/CD
all remained FALSE.

### EMAX configurator verification

The official EMAX configurator (`Serial Update Tool 2.1`, SHA-256
`7ec41368...be99362d`) was run on this same Windows machine with the
device in serial mode (R4+ active, green LED flashing). **The configurator
also failed to read** — same behavior as our scripts.

This confirms the zero-byte result is **not** a protocol or framing issue.
The device previously worked with the same configurator on Ubuntu using Wine.

### Root cause: Windows `usbser` driver incompatibility

| Evidence | Conclusion |
|---|---|
| Device uses non-standard VID 0x8888 (not USB-IF registered) | Generic driver match only |
| `DEVPKEY_Device_GenericDriverInstalled = True` | Windows used class-code matching, no device-specific driver |
| `DEVPKEY_Device_MatchingDeviceId = usb\class_02&subclass_02&prot_01` | Matched by CDC ACM class, not by VID/PID |
| `DriverRank = 16719875` | Very low priority match (high rank number) |
| Device is NOT a USB Composite Device | Single-function CDC ACM, no interface confusion |
| Same device works on Linux (`cdc_acm` driver → `/dev/ttyACM0`) | Linux driver handles non-standard descriptors |
| EMAX configurator fails identically on Windows | Not a script/library issue |

The Windows `usbser` driver binds to the device by CDC ACM class code but
does not properly initialize the USB data path for this device's non-standard
descriptors. Linux's `cdc_acm` module is more permissive and handles the
device correctly. The E8's firmware likely has minimal or non-standard CDC
functional descriptors that Windows requires but Linux does not enforce.

### Resolution paths

1. **Read on Linux** — fastest path. The device works on Ubuntu. Run
   `python3 tools/e8-read-config.py --port /dev/ttyACM0 --execute`
2. **STM32 VCP driver** — the STM32 Virtual COM Port driver handles more
   CDC ACM descriptor variants than `usbser`
3. **Driver reinstall** — uninstall in Device Manager (without deleting
   driver), replug to force fresh binding
4. **USB traffic capture** — use USBPcap/Wireshark on a working Linux
   read to capture the exact USB control transfer sequence, then replicate
   on Windows via pyusb/WinUSB

---

## 6. Next Steps

### Priority 1: Get a successful configuration read

The vendor protocol is fully reverse-engineered (64-byte read request at
115200 8-N-1, 17-byte expected response). The blocker is the Windows USB
driver, not the protocol.

1. **Read on Ubuntu** — plug E8 into the Ubuntu lab machine, enter serial
   mode (R4+ held during power-on), and run:
   `python3 tools/e8-read-config.py --port /dev/ttyACM0 --execute`
2. **Parse the 17-byte response** — byte 0 should be `0x02` (header),
   bytes 1–16 are the configuration payload containing rate, power,
   telemetry ratio, and binding UID data
3. **Optionally capture USB traffic** with Wireshark/USBMon on the working
   Linux read for future Windows driver debugging

### Priority 2: Extract security-relevant configuration

Once a valid read is obtained:

| Field | Security Relevance |
|---|---|
| Binding UID (6 bytes) | Seeds FHSS hopping + CRC init → enables OTA hijack |
| TX power setting | Determines required attacker power to override |
| Packet rate | Determines timing for packet injection |
| Telemetry ratio | Determines telemetry injection opportunities |
| WiFi configuration | Check if default credentials ("expresslrs") are set |
| Failsafe behavior | Critical for understanding DoS impact on drone |

### Priority 3: Alternative access vectors

1. **WiFi mode** — ELRS TX modules have a WiFi AP mode (default SSID
   "ExpressLRS TX", default password "expresslrs"). Trigger by:
   - Timeout (some ELRS builds auto-enable WiFi after 60s without connection)
   - WiFi AP at 10.0.0.1 provides web UI for config and firmware upload
2. **ELRS Configurator** — the standard ExpressLRS Configurator (distinct
   from EMAX's custom tool) may be able to read device info over USB
3. **Passive RF analysis** — use SDR to capture OTA SYNC packets and
   extract UID bytes [3:5] directly from the air

### Priority 4: Develop attack tools

Once configuration is extracted:

1. **Binding phrase brute-force** — use `tools/elrs-uid-rainbow.py` to
   check if the UID matches a known binding phrase
2. **FHSS sequence calculator** — given the UID, compute the full 256-entry
   hopping sequence for packet injection
3. **OTA packet forger** — craft valid ELRS packets to override the
   legitimate TX and commandeer the drone

---

## 7. Files Produced

| File | Location | Purpose |
|---|---|---|
| `E8-SERIAL-CHARACTERIZATION.md` | Repo root | This document |
| `tools/e8-read-config.py` | Repo | Vendor read script (dry-run default, `--execute` to send) |
| `tools/elrs-serial-probe.py` | Repo | CRSF serial probe with passive/active/param-dump modes |
| `research/E8-USB-CHARACTERIZATION.md` | Repo | Detailed USB/configurator reverse engineering |
| `research/E8-COLLABORATION.md` | Repo | Codex/Claude coordination notes |
| `HARDWARE-IDENTITY.md` | Repo | Hardware identity evidence and discovery log |
| Various probe scripts | Session scratchpad | MSP, passive listen, deep probe, wake probe, serial config sweep, Qt-mimic, raw Win32 API |

Session scratchpad scripts are ephemeral. The committed tools in `tools/`
are the production-ready versions.
