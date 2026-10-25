# SX1280 TX Hardware Guide — Chain 1 Live Transmission

**Date:** 2026-10-25
**Status:** Hardware reference — no RF transmission performed
**Purpose:** Bill of materials, wiring, firmware/software setup, and integration
with `tools/elrs-tx-emulator.py` for Chain 1 live TX capability.

> **Scope:** This guide covers getting an SX1280 radio module connected to a
> host MCU and transmitting forged ELRS packets using the project's existing
> TX emulator tool. For the attack chain itself, see
> [`exploit_chains/chain1-dry-run.py`](../tools/exploit_chains/chain1-dry-run.py)
> and the [firmware inventory](firmware-inventory-and-chain.md).
>
> **For authorized CTF research and security testing only.**

---

## 1. Why a Dedicated SX1280 Module

The [PHY feasibility study](../research/sx1280-lora-phy-feasibility.md) concluded
that pure-SDR LoRa TX (Ettus B205mini/B210) is theoretically possible but faces
significant timing and modulation fidelity challenges — particularly the SX1280's
proprietary long-interleaving coding rate (register `0x07`). A dedicated SX1280
module handles the LoRa PHY natively, giving us:

- **Correct modulation:** SF5–SF8, BW 812.5/1625 kHz, CR 4/6–4/8 long
  interleaving — all handled by the SX1280 silicon.
- **Precise timing:** 6.667 ms packet intervals at 150 Hz, with hardware TX-done
  interrupts (DIO1) for tight hop scheduling.
- **Low cost:** $8–25 for a complete module vs. $1,300+ for an Ettus SDR.
- **Portable:** Fits in a pocket with a battery. The SDR path requires a laptop.

The SDR remains the capture/analysis tool. The SX1280 module is the TX tool.

---

## 2. Recommended Hardware

### Option A: LILYGO LoRa32 V2.1 (ESP32 + SX1280) — Recommended

The simplest path: an all-in-one board with ESP32, SX1280, antenna connector,
OLED display, and USB-C. No wiring required.

| Component | Detail |
|-----------|--------|
| MCU | ESP32-PICO-D4 (dual-core 240 MHz, WiFi/BLE) |
| Radio | SX1280 (2.4 GHz ISM, LoRa/FLRC/GFSK) |
| Antenna | U.FL/IPEX connector + 2.4 GHz antenna included |
| Interface | USB-C (serial + power), SPI internally wired |
| Display | 0.96" OLED (SSD1306) — optional, useful for status |
| Price | ~$18–22 USD |
| Where to buy | AliExpress (LILYGO official store), Amazon |

**SPI pin mapping (internal, fixed on the board):**

| SX1280 Pin | ESP32 GPIO | Function |
|------------|------------|----------|
| SCK | GPIO 5 | SPI clock |
| MISO | GPIO 19 | SPI data out (SX1280 → ESP32) |
| MOSI | GPIO 27 | SPI data in (ESP32 → SX1280) |
| NSS/CS | GPIO 18 | SPI chip select |
| RESET | GPIO 14 | Hardware reset |
| BUSY | GPIO 32 | SX1280 busy signal |
| DIO1 | GPIO 33 | TX/RX done interrupt |

**Pros:** Zero wiring, USB-powered, can run MicroPython or Arduino, built-in
WiFi for remote triggering. Cheapest all-in-one option.

**Cons:** Fixed pin mapping. ESP32 MicroPython SPI driver is slower than
bare-metal C/Arduino. Not a Raspberry Pi, so `spidev`/`RPi.GPIO` path from
`elrs-tx-emulator.py` doesn't directly apply (see Integration section).

---

### Option B: HappyModel ES24TX Pro (ELRS TX Module)

An actual ELRS transmitter module, normally used as a drone controller's
external TX. Runs the ELRS firmware natively — which means it already knows
how to do everything we need at the PHY level.

| Component | Detail |
|-----------|--------|
| MCU | ESP32 (ELRS firmware) |
| Radio | SX1280 |
| Antenna | Built-in PCB antenna + U.FL for external |
| Interface | USB-C (for flashing), CRSF/UART (to handset) |
| Power | 5V via USB or JR bay connector |
| Price | ~$20–28 USD |
| Where to buy | HappyModel store, RaceDayQuads, GetFPV, Amazon |

**Pros:** Native ELRS firmware — already implements the full LoRa config,
FHSS hopping, and packet framing. Could be flashed with custom ELRS firmware
that accepts forged packets over UART/WiFi. Hardware is proven for this exact
use case.

**Cons:** Requires custom ELRS firmware modifications or a serial bridge to
accept externally crafted packets. The stock firmware only takes CRSF input
from a handset. More work to integrate with our Python pipeline.

---

### Option C: Raspberry Pi + SX1280 Breakout — Best for `elrs-tx-emulator.py`

The most direct integration path. The TX emulator's SPI backend was written
for this exact setup. Any Raspberry Pi with 40-pin GPIO works.

| Component | Qty | Price (USD) | Notes |
|-----------|-----|-------------|-------|
| Raspberry Pi 4B (2GB+) or Pi 5 | 1 | $35–55 | Any 40-pin Pi works. Pi Zero 2 W ($15) also works. |
| SX1280 breakout module | 1 | $8–12 | Waveshare SX1280 LoRa HAT, NiceRF SX1280 module, or eByte E28-2G4M27S |
| 2.4 GHz antenna (SMA or U.FL) | 1 | $2–5 | Must be 2.4 GHz rated. An old WiFi antenna works. |
| Dupont jumper wires | 6+ | $2 | Female-to-female for module breakouts |
| MicroSD card (16GB+) | 1 | $5–8 | For Raspberry Pi OS |
| USB-C power supply (5V 3A) | 1 | $8–10 | Pi 4/5 PSU |

**Total: ~$60–90**

**Wiring (Pi 40-pin header → SX1280 module):**

```
Raspberry Pi              SX1280 Module
─────────────             ─────────────
Pin 1  (3.3V)  ────────── VCC
Pin 6  (GND)   ────────── GND
Pin 23 (SCLK)  ────────── SCK
Pin 21 (MISO)  ────────── MISO
Pin 19 (MOSI)  ────────── MOSI
Pin 24 (CE0)   ────────── NSS/CS
Pin 22 (GPIO25) ────────── RESET    (configurable: --spi-reset)
Pin 18 (GPIO24) ────────── BUSY     (configurable: --spi-busy)
Pin 15 (GPIO22) ────────── DIO1     (configurable: --spi-dio1)
```

These are the default GPIO pins in `elrs-tx-emulator.py`. Override with
`--spi-reset`, `--spi-busy`, `--spi-dio1` if your wiring differs.

**Pros:** Direct `spidev` + `RPi.GPIO` support — the TX emulator works
out of the box. Full Linux environment for running the Python toolchain.
Most flexible for development and debugging.

**Cons:** Bulkier than the all-in-one boards. Requires wiring. Pi is
overkill for just driving an SX1280, but useful for running the full
Python toolchain locally.

---

### Option D: Generic SX1280 Breakout + Any SPI Host

For maximum flexibility. Any MCU with SPI and 3.3V logic works.

**Recommended modules:**

| Module | Price | Notes |
|--------|-------|-------|
| Waveshare SX1280 LoRa HAT | $15 | Designed for Pi, also works standalone |
| NiceRF SX1280 module | $8–10 | Bare module, needs breakout soldering |
| eByte E28-2G4M27S | $10–12 | 27 dBm PA version (higher power, longer range) |
| CDEBYTE E28-2G4T12S | $8 | 12 dBm, smaller footprint |

**Pin connections are the same SPI + 3 GPIO pattern:**
SCK, MISO, MOSI, NSS, RESET, BUSY, DIO1, VCC (3.3V), GND.

---

## 3. Bill of Materials — Recommended Starter Kit

For the fastest path to live Chain 1 TX, the Raspberry Pi + breakout (Option C)
is recommended because it runs `elrs-tx-emulator.py` directly.

| # | Item | Source | Est. Price |
|---|------|--------|------------|
| 1 | Raspberry Pi 4B 2GB | Amazon / PiShop | $35 |
| 2 | Waveshare SX1280 LoRa HAT | Waveshare / Amazon | $15 |
| 3 | 2.4 GHz SMA antenna | Amazon | $3 |
| 4 | 32GB MicroSD card | Amazon | $6 |
| 5 | USB-C 5V/3A PSU | Amazon | $8 |
| 6 | Dupont jumpers (F-F, 10 pack) | Amazon | $2 |
| | **Total** | | **~$69** |

**Budget alternative (Pi Zero 2 W):**

| # | Item | Source | Est. Price |
|---|------|--------|------------|
| 1 | Raspberry Pi Zero 2 W | Amazon / PiShop | $15 |
| 2 | NiceRF SX1280 module | AliExpress | $9 |
| 3 | 2.4 GHz U.FL antenna + pigtail | Amazon | $4 |
| 4 | 32GB MicroSD card | Amazon | $6 |
| 5 | Micro-USB OTG + power splitter | Amazon | $5 |
| 6 | Dupont jumpers (F-F, 10 pack) | Amazon | $2 |
| | **Total** | | **~$41** |

**All-in-one alternative (LILYGO, no wiring):**

| # | Item | Source | Est. Price |
|---|------|--------|------------|
| 1 | LILYGO LoRa32 V2.1 (SX1280) | AliExpress / Amazon | $20 |
| 2 | 2.4 GHz antenna (if not included) | Amazon | $3 |
| 3 | USB-C cable | (likely already have) | $0 |
| | **Total** | | **~$23** |

---

## 4. Software Setup

### 4A. Raspberry Pi (Option C) — Direct SPI

This is the zero-friction path. The TX emulator's `--backend spi` flag drives
the SX1280 over `/dev/spidev0.0` using the `SPIBackend` class.

```bash
# 1. Enable SPI on the Pi
sudo raspi-config nonint do_spi 0
# (or: add "dtparam=spi=on" to /boot/config.txt and reboot)

# 2. Install Python dependencies
pip install spidev RPi.GPIO numpy

# 3. Verify SPI device exists
ls /dev/spidev*
# Expected: /dev/spidev0.0  /dev/spidev0.1

# 4. Clone the project
git clone https://github.com/oatmealphysics/research-project.git
cd research-project

# 5. Dry-run: verify crypto derivation (no hardware needed)
python3 tools/elrs-tx-emulator.py --phrase "iloveflying" info

# 6. Live TX: takeover with disarm
python3 tools/elrs-tx-emulator.py \
    --phrase "iloveflying" \
    --backend spi \
    --spi-device /dev/spidev0.0 \
    --arm takeover \
    --channels 1500,1500,1000,1500 \
    --disarm
```

**Troubleshooting:**

| Symptom | Fix |
|---------|-----|
| `SX1280 BUSY timeout` | Check BUSY pin wiring. Verify 3.3V power to module. |
| `spidev not installed` | `pip install spidev` |
| `/dev/spidev*` missing | Enable SPI: `sudo raspi-config nonint do_spi 0` + reboot |
| `Permission denied` on SPI | `sudo usermod -aG spi,gpio $USER` then re-login |
| `RPi.GPIO not installed` | `pip install RPi.GPIO` (Raspberry Pi) or `pip install gpiod` (other Linux) |

### 4B. ESP32 / LILYGO (Option A) — MicroPython SPI

For the LILYGO LoRa32 or any ESP32+SX1280 board. Two approaches:

**Approach 1: Serial bridge (recommended)**

Flash the ESP32 with a thin serial-to-SPI bridge that accepts packet bytes
over USB serial and transmits them via the SX1280. The Python toolchain
runs on your laptop and sends packets over the serial port.

```python
# Conceptual serial bridge protocol:
# Host → ESP32: [0xAA] [freq_3B] [len] [packet...] [0x55]
# ESP32 → Host: [0xAA] [status] [0x55]
#
# The ESP32 firmware sets the SX1280 frequency, writes the packet
# to the TX buffer, and triggers transmission. This keeps all the
# ELRS crypto/FHSS logic in Python on the host.
```

A serial bridge sketch for Arduino IDE or PlatformIO is straightforward —
the SX1280 SPI register commands are the same ones in `SPIBackend._configure_lora()`
and `SPIBackend.transmit()` in `elrs-tx-emulator.py` (lines 668–721).

**Approach 2: MicroPython direct**

Port the packet forger to MicroPython and run it entirely on the ESP32.
This is self-contained but harder to debug and update.

```python
# MicroPython SPI setup for LILYGO LoRa32 V2.1
from machine import SPI, Pin
spi = SPI(1, baudrate=10_000_000, polarity=0, phase=0,
          sck=Pin(5), mosi=Pin(27), miso=Pin(19))
cs = Pin(18, Pin.OUT, value=1)
reset = Pin(14, Pin.OUT, value=1)
busy = Pin(32, Pin.IN)
dio1 = Pin(33, Pin.IN)
```

### 4C. HappyModel ES24TX (Option B) — Custom ELRS Firmware

The most capable but most complex path. The ES24TX already runs ELRS
firmware, so the SX1280 is already configured correctly for ELRS LoRa.

**Integration strategy:**

1. Build custom ELRS firmware with a WiFi/serial backdoor that accepts
   externally crafted packet bytes and binding parameters.
2. Use the ELRS WiFi update mode (the same mode Chain 1 exploits for
   credential extraction) to push the custom firmware.
3. Send forged packets from the Python pipeline over WiFi/serial to the
   module, which handles RF timing and FHSS hopping natively.

```bash
# Build ELRS firmware (requires PlatformIO)
pip install platformio
git clone https://github.com/ExpressLRS/ExpressLRS.git
cd ExpressLRS/src
# Edit the target and add custom serial/WiFi packet input
pio run -e HappyModel_ES24TX_Pro_2400_TX
# Flash via WiFi update mode or USB
```

This approach is best when you want the ELRS firmware to handle all PHY
timing, but requires firmware development work. The Raspberry Pi + breakout
path is faster for initial testing.

---

## 5. Integration with the TX Emulator

The existing `tools/elrs-tx-emulator.py` supports two backends. Here's how
each hardware option maps to the tool:

| Hardware | Backend | Command |
|----------|---------|---------|
| Pi + SX1280 breakout | `--backend spi` | Works directly. See §4A. |
| Ettus B205mini/B210 | `--backend sdr` | SDR generates LoRa-like waveform (experimental). |
| LILYGO / ESP32 | Serial bridge needed | Write a thin ESP32 bridge (see §4B). |
| HappyModel ES24TX | Custom firmware needed | Modify ELRS firmware (see §4C). |

### Full Chain 1 live attack sequence

With hardware connected and tested:

```bash
# 1. Verify the full crypto chain (no RF)
python3 tools/exploit_chains/chain1-dry-run.py --phrase "TARGET_PHRASE"

# 2. Validate against captured frames (no RF)
python3 tools/elrs-capture-forge-validate.py \
    --phrase "TARGET_PHRASE" \
    --captures work/ranked-phy-candidates.jsonl

# 3. Live takeover — disarm the target
python3 tools/elrs-tx-emulator.py \
    --phrase "TARGET_PHRASE" \
    --backend spi \
    --arm takeover \
    --channels 1500,1500,1000,1500 \
    --disarm

# 4. Live MSP injection — reboot the flight controller
python3 tools/elrs-tx-emulator.py \
    --phrase "TARGET_PHRASE" \
    --backend spi \
    --arm inject \
    --msp-cmd 68
```

### Key parameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| `--backend` | (required) | `spi` or `sdr` |
| `--phrase` | (required) | Target's binding phrase |
| `--ota-version` | 3 | ELRS OTA version (3 for most current drones) |
| `--rate` | 3 (150 Hz) | Packet rate enum. Match the target's rate. |
| `--spi-device` | `/dev/spidev0.0` | SPI device path |
| `--spi-reset` | 25 | GPIO pin for SX1280 RESET |
| `--spi-busy` | 24 | GPIO pin for SX1280 BUSY |
| `--spi-dio1` | 22 | GPIO pin for SX1280 DIO1 |
| `--tx-power` | 13 | TX power in dBm (-18 to +13) |
| `--channels` | `1500,1500,1000,1500` | RC channel values (AETR order) |
| `--disarm` | off | Set throttle to minimum for disarm |
| `--msp-cmd` | none | MSP command ID to inject |

---

## 6. Safety and Legal

- **Transmission requires authorization.** Do not transmit on 2.4 GHz without
  explicit permission from the drone operator and compliance with local RF
  regulations.
- **ISM band limits:** 2.4 GHz ISM allows up to +20 dBm EIRP in most
  jurisdictions. The SX1280 module outputs up to +13 dBm (20 mW). The eByte
  E28-2G4M27S PA module outputs up to +27 dBm (500 mW) — this exceeds ISM
  limits and may require a license.
- **CTF/lab use:** For CTF competitions and authorized testing, operate in a
  shielded enclosure or at minimum power (`--tx-power -18`).
- **Kill switch:** Keep the target drone's props removed during bench testing.
  The emulator can arm the flight controller.

---

## 7. Next Steps

1. **Order hardware** from the BOM in §3.
2. **Bench test** with a known binding phrase and the dry-run tool.
3. **Validate** with live SDR capture — use the B205mini to confirm that the
   SX1280 module is transmitting valid LoRa frames on the correct frequencies.
4. **End-to-end test** against the TinyHawk test article with props removed.
