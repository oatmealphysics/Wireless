# PR #66 Zero-RF Bench Steps — Offline Static Analysis

- **Date:** 2026-10-29
- **Scope:** Authorized security research on owned EMAX TinyHawk III Plus / E8 hardware
- **Method:** Offline/static analysis only — no device contact, no RF, no bench hardware
- **Target:** EMAX TinyHawk III Plus (STM32F411CEU6, Betaflight 4.4.2, SPI SX1280)
- **Source:** PR #66 deep research findings (12 new attack surfaces)

This document completes the offline-feasible portions of the four zero-RF bench
steps identified in PR #66. Each section documents the audit procedure, static
analysis against existing project data, expected findings, and what a future
on-bench run would require.

---

## 1. SX1280 SPI Audit — Static Analysis vs Semtech LR11xx CVEs

### Background

PR #66 identified three Semtech LR11xx CVEs published in 2025:

| CVE | Description | Affected IC |
|---|---|---|
| CVE-2025-14857 | Buffer overflow in SPI command processing | LR1110, LR1120, LR1121 |
| CVE-2025-14858 | Arbitrary code execution via SPI write primitives | LR1110, LR1120, LR1121 |
| CVE-2025-14859 | Firmware update validation bypass | LR1110, LR1120, LR1121 |

The question: do analogous SPI write primitives exist on the SX1280 (same
vendor, different product line)?

### SX1280 vs LR11xx Architecture Comparison

| Property | SX1280 | LR1110/LR1120/LR1121 |
|---|---|---|
| Vendor | Semtech | Semtech |
| Frequency band | 2.4 GHz ISM | Sub-GHz + 2.4 GHz (LR1120/21) |
| Processor architecture | **ROM-based state machine** | **ARM Cortex-M0+ with flash** |
| Firmware model | Fixed silicon ROM; no field-updatable firmware | Field-updatable firmware via SPI |
| SPI command set | Register read/write + FIFO buffer operations | Register + firmware upload + crypto + GNSS |
| DMA capability | TX/RX FIFO buffer only (256 bytes) | Full DMA to internal SRAM and peripherals |
| Code execution surface | **None** — no general-purpose CPU | ARM Cortex-M0+ executes uploaded firmware |

### Why the LR11xx CVEs Do Not Directly Transfer

The LR11xx CVEs exploit its **ARM Cortex-M0+ microcontroller** — a general-purpose
CPU that runs updatable firmware loaded via SPI. The attack surface is:

1. **CVE-2025-14857:** The LR11xx SPI command parser runs on the Cortex-M0+ and
   has a buffer overflow when processing oversized command payloads. The SX1280
   has no equivalent command parser running on a CPU — its SPI interface is a
   hardware register/FIFO interface implemented in silicon.

2. **CVE-2025-14858:** Arbitrary code execution via SPI writes to the LR11xx's
   internal SRAM, which the Cortex-M0+ then executes. The SX1280 has **no
   internal SRAM accessible for code execution** — its 256-byte FIFO buffer is
   a hardware TX/RX data path, not executable memory.

3. **CVE-2025-14859:** Firmware update validation bypass on the LR11xx's flash
   programming interface. The SX1280 has **no firmware update mechanism** — its
   behavior is fixed in ROM at manufacturing.

**Conclusion:** The LR11xx CVEs are architectural — they exploit a software
execution environment that the SX1280 does not have. The SX1280 is a
hardware-defined radio transceiver with register-level SPI control, not a
programmable microcontroller. There is no firmware to overwrite, no command
parser to overflow, and no executable memory to target via SPI.

### What SPI Primitives the SX1280 Does Expose

The SX1280 SPI interface (per Semtech DS_SX1280-1_V3.2 datasheet) provides:

| SPI Command | Function | Security Relevance |
|---|---|---|
| `WriteRegister` | Write to configuration registers | Can reconfigure modulation, frequency, power, packet format |
| `ReadRegister` | Read configuration registers | Information disclosure of current radio state |
| `WriteBuffer` | Write to 256-byte TX FIFO | Loads arbitrary packet data for transmission |
| `ReadBuffer` | Read from 256-byte RX FIFO | Reads received packet data |
| `SetStandby` / `SetTx` / `SetRx` | Mode control | Can force radio state transitions |
| `SetPacketType` | LoRa / FLRC / GFSK / BLE selection | Changes modulation type |
| `SetRfFrequency` | Set carrier frequency | Retune to arbitrary ISM2400 frequency |

**Attack surface from the host MCU side:** An attacker who controls the SPI bus
(i.e., has code execution on the STM32F411 host) can fully reconfigure the
SX1280: change frequency, modulation, power, packet contents, and force
transmission. This is **expected behavior** — the SX1280 is a peripheral
controlled by its host. The security boundary is the host MCU, not the radio IC.

### Relevance to the TinyHawk III Plus

On the TinyHawk III Plus:
- The SX1280 is on **SPI bus 3** (CS PA15, EXTI PC14, reset PA8, busy PA13)
- The STM32F411 is the host MCU running Betaflight 4.4.2
- ELRS receiver code runs on the STM32F411, not on the SX1280
- The attack path is: **OTA → SX1280 RX FIFO → STM32F411 SPI read → ELRS stack → CRSF → MSP**

The SX1280 is a passive component in this chain. Compromise of the radio link
targets the STM32F411's ELRS parser, not the SX1280 itself.

### What a Bench SPI Audit Would Add

A physical SPI bus audit with a logic analyzer (Saleae, sigrok) would:

1. **Confirm SPI bus 3 pinout and clock speed** against the Betaflight target config
2. **Capture the SX1280 initialization sequence** at boot — register writes that
   configure modulation (FLRC at rate_index=2), frequency plan, packet params
3. **Verify no unexpected SPI commands** — confirm Betaflight only uses the
   documented SX1280 command set, ruling out undocumented vendor commands
4. **Capture live packet RX flow** — observe timing from SX1280 DIO1 interrupt
   through STM32F411 SPI read to ELRS packet processing

**Equipment needed:** Logic analyzer with ≥4 channels, ≥25 MHz sampling (SPI
clock is typically 10 MHz for SX1280). Probe leads to SPI bus 3 test points or
direct wire to CS/SCK/MOSI/MISO pins. No firmware modification required — this
is passive bus observation.

---

## 2. STM32F411 Readout Protection (RDP) Level Assessment

### Background

PR #66 noted that the STM32F411 "almost certainly ships RDP Level 0," meaning
firmware extraction and DFU persistence would be trivial with bench access. This
section documents the RDP mechanism, how to determine the level, and
implications.

### STM32F411 RDP Levels (per ST RM0383 Reference Manual)

| RDP Level | Protection State | SWD Access | Flash Read | Implications |
|---|---|---|---|---|
| **Level 0** (0xAA) | No protection | Full SWD debug | Full flash read/write | Firmware extraction trivial via SWD |
| **Level 1** (any value except 0xAA, 0xCC) | Read protection | SWD connects but flash read blocked; debug halts at reset | Flash read returns bus error | Firmware not readable via SWD; bypass requires voltage glitching |
| **Level 2** (0xCC) | Permanent protection | SWD permanently disabled | No access | **Irreversible.** No debug, no DFU. Chip must be replaced. |

The RDP level is stored in the **Option Bytes** at address `0x1FFFC000`,
specifically in the `RDP` field of `FLASH_OPTCR` (bit 8). The value is:
- `0xAA` = Level 0 (unprotected)
- `0xCC` = Level 2 (permanently locked)
- Any other value = Level 1

### Why Level 0 Is Likely

Evidence from the project and broader ecosystem:

1. **Consumer drone industry practice:** The 2026 arXiv paper on Holy Stone
   drone firmware extraction found that consumer drones "typically have unlabeled
   debug pads and undocumented memory layouts, but rarely enable readout
   protection" (cited in `research/drone-wireless-vuln-reference.md`).

2. **Betaflight ecosystem:** Betaflight's DFU bootloader and Configurator flash
   workflow assume SWD/DFU access. Setting RDP Level 1+ would break the standard
   firmware update path that EMAX documents in their product support.

3. **EMAX product category:** The TinyHawk III Plus is a consumer FPV drone, not
   a defense/enterprise product. RDP Level 1+ adds manufacturing complexity
   (must be set post-flash) with no revenue benefit for a $90 consumer product.

4. **STM32F411 defaults:** ST ships the STM32F411 with Option Bytes at factory
   defaults, which is RDP Level 0. A manufacturer must explicitly program the
   Option Bytes to enable protection.

5. **Betaflight 4.4.2 source:** The firmware source does not contain Option Byte
   programming code. Protection would need to be set during EMAX's manufacturing
   flashing process as a separate step.

**Assessment: RDP Level 0 with >95% confidence.** This is not confirmed until
a physical SWD connection is attempted.

### How to Determine the RDP Level (On-Bench Procedure)

**Equipment needed:**
- SWD adapter: ST-Link V2 ($5–15), J-Link EDU ($20), or any CMSIS-DAP probe
- 2 wires: SWDIO and SWCLK (plus GND)
- OpenOCD or STM32CubeProgrammer software

**Procedure (read-only, does not modify firmware):**

```bash
# 1. Connect SWD adapter to TinyHawk III Plus SWD pads
#    (SWDIO, SWCLK, GND — typically exposed as test pads on the PCB)

# 2. Attempt connection with OpenOCD
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg

# Expected outcomes:
# - Level 0: Full connection. "stm32f4x.cpu: hardware has 6 breakpoints"
# - Level 1: Connection succeeds but flash reads return bus error.
#            "Error: flash read failed" when attempting memory read.
# - Level 2: SWD connection refused entirely.
#            "Error: init mode failed (unable to connect to target)"

# 3. If Level 0, read Option Bytes to confirm:
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "init; flash info 0; shutdown"
# Look for "Option bytes: RDP Level 0"

# 4. If Level 0, firmware dump (read-only):
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "init; flash read_image firmware_dump.bin 0x08000000 0x80000; shutdown"
# Reads 512 KiB flash (full STM32F411CEU6 flash)
```

**Alternative via STM32CubeProgrammer (GUI):**
1. Connect ST-Link, launch CubeProgrammer
2. Click "Connect" — if it connects, RDP is Level 0 or Level 1
3. Go to Option Bytes tab — read RDP level directly
4. If Level 0: "Read Out Protection" dropdown shows "AA" (Level 0)

### Implications of RDP Level 0

If confirmed Level 0:

| Capability | Method | Impact |
|---|---|---|
| **Full firmware dump** | `flash read_image` via SWD | Extract the exact installed binary for offline analysis |
| **Live memory inspection** | `mdw` / `mdd` via SWD | Read SRAM at runtime — includes ELRS UID, session keys, nonces |
| **Breakpoint debugging** | GDB via OpenOCD | Set breakpoints in ELRS/MSP/CRSF handlers for live protocol analysis |
| **DFU boot** | STM32 system bootloader (BOOT0 pin) | Load custom firmware via UART/USB DFU — **blocked by no-flash constraint** |
| **Option Byte read** | Via SWD | Confirms exact RDP state, watchdog config, BOR level |

**Critical constraint:** Even with RDP Level 0 confirmed, the project's **no-flash
rule** remains absolute. SWD read access and live debugging are permitted
(read-only operations); firmware writes, Option Byte modification, and DFU boot
are prohibited.

### What the Firmware Dump Would Contain

A 512 KiB dump of the STM32F411 flash would yield:

1. **The exact installed Betaflight 4.4.2 binary** — not a release artifact, but
   the actual bytes running on this specific flight controller. Enables:
   - Exact linker map reconstruction (symbol resolution via signature matching)
   - Precise vulnerability assessment against the installed build
   - Closing the `hardware_match` gap in the firmware inventory

2. **Stored configuration** — Betaflight stores config in the last flash sectors.
   Contains binding UID, PID tuning, serial port config, mode ranges, failsafe
   settings. (Most already known from MSP probing, but flash gives the complete
   picture including any non-MSP-accessible fields.)

3. **Build metadata** — Compiler flags, optimization level, stack protection
   status. Critical for assessing whether stack canaries or other mitigations
   are present in the installed binary.

### Voltage Glitching (If RDP Level 1)

If RDP Level 1 is found (unlikely but possible):

Published attacks by Anvil Secure (2023) and eShard (2024) demonstrate RDP
Level 1 bypass on STM32F4xx via voltage fault injection:

1. **Method:** ~5 ns voltage glitch on the VDD rail during the boot sequence,
   between RDP check and debug port lockout
2. **Equipment:** ChipWhisperer Lite ($250) or custom MOSFET glitcher + Raspberry Pi
3. **Success rate:** Published reports show 1–10% success rate per glitch attempt,
   typically succeeding within 100–1000 attempts (minutes to hours)
4. **Risk:** Overpowered glitch can cause latch-up → permanent damage. Low risk
   with proper voltage limiting but non-zero.

**This would require explicit owner sign-off** due to the physical risk, and is
outside the current scope.

---

## 3. Firmware Extraction Procedure Documentation

### Overview

This section documents the complete firmware extraction procedure for the
TinyHawk III Plus, assuming RDP Level 0 is confirmed. The procedure is
non-destructive and read-only.

### Prerequisites

| Item | Purpose | Approximate Cost |
|---|---|---|
| ST-Link V2 (or clone) | SWD adapter | $5–15 |
| 3× Dupont jumper wires | SWDIO, SWCLK, GND | $1 |
| Soldering iron + fine tip (optional) | If SWD pads need wire attachment | Available |
| Multimeter | Continuity testing for pad identification | Available |
| OpenOCD or STM32CubeProgrammer | Debug software | Free |
| Host computer (Linux preferred) | Run extraction tools | Available |

### Step 1: Identify SWD Pads

The STM32F411CEU6 exposes SWD on:
- **SWDIO:** PA13 (pin 34 on UFQFPN48 package)
- **SWCLK:** PA14 (pin 37 on UFQFPN48 package)

On the TinyHawk III Plus PCB, these are **shared with the SX1280 SPI signals**:
- PA13 is mapped to **SX1280 BUSY** in the Betaflight target config
- PA14 is **not listed** in the target resource map (available for SWD)

**Important:** PA13 (SWDIO) shares a pin with the SX1280 BUSY signal. During
SWD debug, the SX1280 BUSY function will be unavailable. This is acceptable
for read-only extraction (the radio link will be disrupted during extraction,
but no firmware is modified). Power cycle the flight controller after extraction
to restore normal SX1280 operation.

Typical EMAX TinyHawk board layouts expose SWD as:
- Unpopulated 0.1" header pads near the STM32
- Sometimes labeled "SWD" or "DBG" on the silkscreen
- Sometimes unlabeled test pads requiring continuity testing to PA13/PA14

**Procedure:** With the FC powered off, use a multimeter in continuity mode to
trace from the STM32F411 pins (PA13, PA14) to accessible test pads or header
holes on the PCB. Do not probe while powered.

### Step 2: Connect and Verify

```bash
# Power the TinyHawk via USB (not battery — more controlled)
# Connect ST-Link: SWDIO → PA13 pad, SWCLK → PA14 pad, GND → GND pad

# Verify connection:
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "init; targets; shutdown"

# Expected output (Level 0):
# stm32f4x.cpu: target state: halted
# target halted due to debug-request, current mode: Thread
# xPSR: 0x01000000 pc: 0x08000xxx msp: 0x2001ffxx
```

### Step 3: Read Option Bytes

```bash
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "init; stm32f4x option_read 0 0x1FFFC000; shutdown"

# Decode the 32-bit value:
# Bits [15:8] = RDP level byte
# 0xAA = Level 0, 0xCC = Level 2, other = Level 1
```

### Step 4: Extract Firmware

```bash
# Full flash dump (512 KiB for STM32F411CEU6):
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "init; flash read_image work/tinyhawk-identity/firmware_dump_raw.bin 0x08000000 0x80000; shutdown"

# Verify dump:
sha256sum work/tinyhawk-identity/firmware_dump_raw.bin > work/tinyhawk-identity/firmware_dump_raw.sha256

# Compare against the official 4.4.2 release artifact:
sha256sum firmware/betaflight/betaflight_4.4.2_STM32F411.bin
# If hashes differ (expected — EMAX applies unified target config),
# compare vector tables and code sections for structural similarity.
```

### Step 5: Extract SRAM Snapshot (Optional)

```bash
# Read full SRAM (128 KiB for STM32F411):
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "init; dump_image work/tinyhawk-identity/sram_snapshot.bin 0x20000000 0x20000; shutdown"

# This captures runtime state including:
# - ELRS binding UID (in ELRS RX state struct)
# - Current FHSS position and nonce
# - CRSF/MSP parser state
# - Stack contents
```

### Step 6: Disconnect and Restore

```bash
# Disconnect SWD adapter
# Power cycle the TinyHawk (remove USB, reconnect)
# Verify normal operation: LED pattern, MSP response on COM5
```

### Expected Outcomes

| Outcome | Next Step |
|---|---|
| RDP Level 0, full dump obtained | Compare against 4.4.2 release; extract config sector; build exact ELF/map for vulnerability analysis |
| RDP Level 1, connection blocked | Document; consider voltage glitching (requires owner sign-off) |
| RDP Level 2, no SWD | Document; firmware extraction requires chip-off (destructive) — outside project scope |
| SWD pads not found | Requires PCB photographs and continuity mapping; may need microscope |

### Post-Extraction Analysis Plan

With a full firmware dump:

1. **Binary diffing** against the official 4.4.2 release using `bindiff` or
   `diaphora` — identifies EMAX-specific customizations
2. **Symbol recovery** via Betaflight 4.4.2 source and linker script — maps
   function addresses to source symbols
3. **Stack protection audit** — check for `__stack_chk_guard` references in the
   binary; if absent, stack buffer overflows are directly exploitable
4. **ELRS parser binary analysis** — exact code path from `SX1280_GetPayload()`
   through CRSF routing to MSP dispatch
5. **Configuration sector decode** — extract all stored parameters including any
   not accessible via MSP

---

## 4. SmartAudio / UART2 Probing — Unauthenticated VTX Control Analysis

### Background

The extended MSP probe (Oct 29) confirmed:

> **UART2 runs TBS SmartAudio (function ID `0x0800`), not CRSF.** The CRSF link
> uses SPI bus 3 directly (`rx_spi_protocol=19`).

SmartAudio is TBS's serial protocol for Video Transmitter (VTX) control. On the
TinyHawk III Plus, it runs on UART2 between the STM32F411 flight controller and
the onboard VTX.

### SmartAudio Protocol Analysis

SmartAudio is documented by TBS and implemented in Betaflight source. Key
properties:

| Property | Value | Security Implication |
|---|---|---|
| Authentication | **None** | Any device on the UART bus can send commands |
| Encryption | **None** | Commands are plaintext |
| Integrity | **CRC8 only** | Trivially computable; no HMAC or signature |
| Direction | Bidirectional (FC ↔ VTX) | Both query and command operations |
| Baud rate | 4800 (SmartAudio v1/v2) or 115200 (v2.1) | Low-speed serial |
| Frame format | `0xAA 0x55 [cmd] [length] [data...] [crc8]` | Simple framing with sync bytes |

### SmartAudio Command Set

| Command | ID | Function | Impact if Abused |
|---|---|---|---|
| `GET_SETTINGS` | `0x03` | Read VTX frequency, power, mode | Information disclosure |
| `SET_FREQUENCY` | `0x09` | Set VTX output frequency | **Move video to wrong channel → pilot loses FPV feed** |
| `SET_POWER` | `0x05` | Set VTX output power | Reduce to minimum → range loss; increase to max → regulatory violation |
| `SET_MODE` | `0x07` | Set VTX mode (race/pit/etc.) | **Force pit mode → video off → blind pilot** |
| `SET_POWER_LEGACY` | `0x02` | Set power (v1 compat) | Same as SET_POWER |

### Attack Path Analysis: OTA → SmartAudio

The confirmed attack topology on the TinyHawk III Plus is:

```
OTA (2.4 GHz ELRS)
  → SX1280 SPI bus 3 (RX FIFO)
  → STM32F411 ELRS receiver stack
  → CRSF router
  → MSP handler
  → Betaflight VTX control (vtx_smartaudio.c)
  → UART2 TX → VTX SmartAudio
```

**Critical finding:** MSP commands can control the VTX through Betaflight's VTX
abstraction layer. The relevant MSP commands are:

| MSP Command | ID | Function | SmartAudio Effect |
|---|---|---|---|
| `MSP_VTX_CONFIG` | 88 | Read VTX config | Maps to SmartAudio `GET_SETTINGS` |
| `MSP_SET_VTX_CONFIG` | 89 | Write VTX config | Maps to SmartAudio `SET_FREQUENCY`, `SET_POWER`, `SET_MODE` |

**The MSP-over-ELRS path established in the project's research can therefore
reach SmartAudio commands without direct UART2 access.** An attacker who can
inject MSP commands over the ELRS link (requires UID + packet injection
capability) can:

1. **Read VTX configuration** — determine current video channel and power
2. **Change VTX frequency** — move the pilot's video feed to an unmonitored channel
3. **Force pit mode** — disable video transmission entirely
4. **Set minimum power** — reduce video range, potentially causing video loss

### Betaflight 4.4.2 SmartAudio Implementation

From Betaflight 4.4.2 source analysis:

```c
// src/main/io/vtx_smartaudio.c
// SmartAudio frame builder — no authentication, CRC8 only
static void saFrameInit(smartAudioFrame_t *frame, uint8_t cmd)
{
    frame->header = SA_HEADER;     // 0xAA 0x55
    frame->command = cmd;
}

// CRC8 (DVB-S2 polynomial 0xD5) — trivially computable
static uint8_t saCrc8(const uint8_t *data, int len)
```

The CRC8 uses the DVB-S2 polynomial (`x^8 + x^7 + x^6 + x^4 + x^2 + 1`,
init 0x00). This is a data-integrity check, not a security mechanism — an
attacker who knows the protocol can compute valid CRCs for arbitrary commands.

### MSP_SET_VTX_CONFIG Routing in Betaflight

```c
// src/main/msp/msp.c (Betaflight 4.4.2)
case MSP_SET_VTX_CONFIG:
{
    vtxSettingsConfig_t settings;
    settings.freq = sbufReadU16(src);       // Attacker-controlled frequency
    settings.band = sbufReadU8(src);
    settings.channel = sbufReadU8(src);
    settings.power = sbufReadU8(src);       // Attacker-controlled power level
    settings.pitMode = sbufReadU8(src);     // Attacker-controlled pit mode
    // ... applies to VTX via vtxCommonProcess()
}
```

The MSP handler parses the fields directly from the incoming MSP payload and
applies them to the VTX. There is **no additional authentication or
authorization check** between the MSP handler and the SmartAudio command
emission — once an MSP command reaches the handler, it executes.

### ELRS MSP-over-Air Transport

For ELRS v3 (the version running on the TinyHawk III Plus), MSP is transported
using the 13-byte extended payload packet type. Key constraints:

- MSP commands are fragmented across multiple ELRS packets (13 bytes per packet,
  minus CRSF/MSP framing overhead)
- The ELRS receiver reassembles MSP fragments before passing to Betaflight
- `MSP_SET_VTX_CONFIG` requires 6 bytes of payload — fits in a single ELRS MSP
  packet after framing

**Delivery timeline at 150 Hz:**
- Single MSP packet: 1 ELRS frame = 6.67 ms
- VTX frequency change command: ~1 frame (6.67 ms) for delivery + SmartAudio
  UART latency (~2 ms at 4800 baud) = **under 10 ms total**

### What Direct UART2 Probing Would Add

A bench-level UART2 probe (logic analyzer or USB-UART adapter on UART2 TX/RX
pins) would:

1. **Confirm SmartAudio version** — v1 (4800 baud, limited commands) vs v2/v2.1
   (more features, possibly 115200 baud)
2. **Capture idle protocol traffic** — SmartAudio v2+ supports automatic status
   polling by the FC; observe the query/response cadence
3. **Verify VTX presence and model** — the `GET_SETTINGS` response identifies the
   VTX hardware, frequency table, and power levels
4. **Test command injection** — send a benign SmartAudio command (e.g.,
   `GET_SETTINGS`) from an external UART adapter and verify VTX response

**Equipment needed:**
- USB-UART adapter (3.3V logic, e.g., CP2102 or FTDI)
- 2 wires to UART2 TX/RX pads
- Logic analyzer (optional, for protocol capture)
- Terminal software (minicom, PuTTY, or custom Python serial script)

### Combined Attack Surface Assessment

The SmartAudio UART2 surface intersects with the existing ELRS attack chain:

| Attack Vector | Access Required | Impact | Complexity |
|---|---|---|---|
| MSP_SET_VTX_CONFIG over ELRS | UID + packet injection | VTX frequency/power/pit control | High (requires full ELRS chain) |
| Direct UART2 SmartAudio | Physical access to UART2 pads | Same VTX control | Low (trivial protocol) |
| CRSF → MSP → SmartAudio via USB | USB access to E8 (if CRSF bridge exists) | Same VTX control | Medium (depends on E8 USB bridge) |

The OTA path (MSP over ELRS) is the highest-value attack vector because it
requires no physical access. The SmartAudio protocol's complete lack of
authentication means the only defense is the ELRS link itself — once MSP
commands can be injected over the air, VTX control follows immediately.

### Defensive Implications

For the bench defense tool (PR #64's bus anomaly detector):
- Monitor UART2 for unexpected SmartAudio commands that don't originate from
  Betaflight's normal VTX management cycle
- Alert on `SET_FREQUENCY` or `SET_MODE` commands outside the operator's
  configured band/channel
- Alert on rapid pit mode toggling (potential disruption attack signature)

---

## Summary: Bench-Step Readiness Matrix

| Step | Offline Analysis | Key Finding | Needs Hardware? | Equipment for Bench |
|---|---|---|---|---|
| **SX1280 SPI audit** | **Complete** | LR11xx CVEs do not transfer — SX1280 is ROM-based, no code execution surface | Yes (for init sequence capture) | Logic analyzer, 4 probes |
| **STM32F411 RDP check** | **Complete** | >95% confidence RDP Level 0; procedure documented | Yes (to confirm) | ST-Link V2 ($5), 3 wires |
| **Firmware extraction** | **Complete** | Full procedure documented; post-extraction analysis plan ready | Yes (to execute) | ST-Link V2, OpenOCD |
| **SmartAudio/UART2** | **Complete** | Unauthenticated CRC8-only protocol; reachable from OTA via MSP-over-ELRS chain | Yes (for direct VTX probing) | USB-UART adapter, 2 wires |

### Priority Ordering for Future Bench Work

1. **STM32F411 RDP check** (5 minutes, $5 equipment) — unlocks or blocks
   everything else. If Level 0, firmware dump follows immediately.
2. **Firmware extraction** (10 minutes if Level 0) — the single highest-value
   bench step. Closes the `hardware_match` gap and enables exact binary analysis.
3. **SmartAudio UART2 capture** (15 minutes) — confirms VTX model and protocol
   version. Lower priority since MSP-over-ELRS already provides the OTA attack
   path to VTX control.
4. **SX1280 SPI bus capture** (30+ minutes) — useful for protocol timing analysis
   but lowest priority since the LR11xx CVEs don't transfer.

All four steps are non-destructive, read-only, and require no firmware
modification. Total bench time estimate: 1–2 hours including setup.
