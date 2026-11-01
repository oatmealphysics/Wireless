#!/usr/bin/env python3
"""
SDR-Based BLE Advertising Decoder

Decodes BLE advertising packets from raw SDR IQ captures — no dedicated
BLE sniffer hardware required. Targets advertising channels 37/38/39
(2402 / 2426 / 2480 MHz) using GFSK demodulation at 1 Mbps.

Designed for ESP32 fingerprinting: identifies Espressif OUI MACs,
device names, service UUIDs, and manufacturer-specific advertising data
that indicate an ESP32 (or ESP32-C3/S3) MCU.

Capture requirements (documented in detail with --help):
    hackrf_transfer -f 2402000000 -s 4000000 -l 32 -g 30 -n 16000000 -r ch37.raw
    (produces 8-bit signed IQ at 4 Msps — the decoder handles the format)

Supported IQ formats:
    - complex64 (numpy .npy): np.complex64 array
    - raw 8-bit signed IQ (HackRF / rtl_sdr): interleaved int8 I,Q pairs
    - raw 16-bit signed IQ (USRP / BladeRF): interleaved int16 I,Q pairs
    - WAV (SDR# / GQRX): 16-bit stereo WAV with I/Q channels

For authorized security research and CTF use only.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

# ---------------------------------------------------------------------------
# BLE PHY constants
# ---------------------------------------------------------------------------

BLE_PREAMBLE = 0xAA  # advertising preamble byte (LSBit-first: 10101010)
BLE_ADV_ACCESS_ADDR = 0x8E89BED6  # advertising channel access address
BLE_ADV_AA_BYTES = struct.pack("<I", BLE_ADV_ACCESS_ADDR)  # little-endian

# Advertising channel frequencies
BLE_ADV_CHANNELS = {
    37: 2402e6,
    38: 2426e6,
    39: 2480e6,
}

# CRC-24 polynomial and initial value for advertising
BLE_CRC_POLY = 0x100065B
BLE_CRC_INIT_ADV = 0x555555  # advertising channel CRC init

# Data whitening LFSR taps (x^7 + x^4 + 1)
WHITENING_POLY = 0x11  # taps at bit 4 and bit 0 (x^7 + x^4 + 1)

# Espressif OUI prefixes (first 3 bytes of MAC)
ESPRESSIF_OUIS = [
    bytes([0x24, 0x0A, 0xC4]),
    bytes([0x24, 0x6F, 0x28]),
    bytes([0x24, 0x62, 0xAB]),
    bytes([0x30, 0xAE, 0xA4]),
    bytes([0x3C, 0x61, 0x05]),
    bytes([0x3C, 0x71, 0xBF]),
    bytes([0x40, 0x22, 0xD8]),
    bytes([0x40, 0x91, 0x51]),
    bytes([0x48, 0x3F, 0xDA]),
    bytes([0x54, 0x32, 0x04]),
    bytes([0x58, 0xBF, 0x25]),
    bytes([0x5C, 0xCF, 0x7F]),
    bytes([0x60, 0x01, 0x94]),
    bytes([0x68, 0x67, 0x25]),
    bytes([0x70, 0x04, 0x1D]),
    bytes([0x7C, 0xDF, 0xA1]),
    bytes([0x7C, 0x9E, 0xBD]),
    bytes([0x80, 0x7D, 0x3A]),
    bytes([0x84, 0x0D, 0x8E]),
    bytes([0x84, 0xCC, 0xA8]),
    bytes([0x84, 0xF3, 0xEB]),
    bytes([0x8C, 0xAA, 0xB5]),
    bytes([0x94, 0xB5, 0x55]),
    bytes([0x94, 0xB9, 0x7E]),
    bytes([0x98, 0xCD, 0xAC]),
    bytes([0xA0, 0x20, 0xA6]),
    bytes([0xA4, 0xCF, 0x12]),
    bytes([0xAC, 0x67, 0xB2]),
    bytes([0xB4, 0xE6, 0x2D]),
    bytes([0xBC, 0xDD, 0xC2]),
    bytes([0xC4, 0x4F, 0x33]),
    bytes([0xC8, 0xF0, 0x9E]),
    bytes([0xCC, 0x50, 0xE3]),
    bytes([0xD8, 0xA0, 0x1D]),
    bytes([0xD8, 0xBC, 0x38]),
    bytes([0xDC, 0x54, 0x75]),
    bytes([0xE0, 0x98, 0x06]),
    bytes([0xE8, 0x68, 0xE7]),
    bytes([0xEC, 0x94, 0xCB]),
    bytes([0xF0, 0x08, 0xD1]),
    bytes([0xF4, 0xCF, 0xA2]),
    bytes([0xFC, 0xF5, 0xC4]),
]

# BLE AD type codes we care about
AD_TYPE_FLAGS = 0x01
AD_TYPE_INCOMPLETE_16 = 0x02
AD_TYPE_COMPLETE_16 = 0x03
AD_TYPE_INCOMPLETE_32 = 0x04
AD_TYPE_COMPLETE_32 = 0x05
AD_TYPE_INCOMPLETE_128 = 0x06
AD_TYPE_COMPLETE_128 = 0x07
AD_TYPE_SHORT_NAME = 0x08
AD_TYPE_COMPLETE_NAME = 0x09
AD_TYPE_TX_POWER = 0x0A
AD_TYPE_MFG_SPECIFIC = 0xFF

AD_TYPE_NAMES = {
    0x01: "Flags",
    0x02: "Incomplete 16-bit UUIDs",
    0x03: "Complete 16-bit UUIDs",
    0x04: "Incomplete 32-bit UUIDs",
    0x05: "Complete 32-bit UUIDs",
    0x06: "Incomplete 128-bit UUIDs",
    0x07: "Complete 128-bit UUIDs",
    0x08: "Shortened Local Name",
    0x09: "Complete Local Name",
    0x0A: "TX Power Level",
    0x16: "Service Data (16-bit UUID)",
    0x19: "Appearance",
    0x1B: "LE Bluetooth Device Address",
    0xFF: "Manufacturer Specific Data",
}

# PDU type names (BLE 4.x advertising)
PDU_TYPE_NAMES = {
    0: "ADV_IND",
    1: "ADV_DIRECT_IND",
    2: "ADV_NONCONN_IND",
    3: "SCAN_REQ",
    4: "SCAN_RSP",
    5: "CONNECT_IND",
    6: "ADV_SCAN_IND",
}


# ---------------------------------------------------------------------------
# Data whitening (BLE Core Spec Vol 6, Part B, Section 3.2)
# ---------------------------------------------------------------------------

def ble_whitening_seq(channel: int, length_bytes: int) -> bytes:
    """Generate the BLE data-whitening XOR sequence for a given channel."""
    # LFSR init: position 0 = 1, positions 1-6 = channel bits (LSB first)
    lfsr = (channel & 0x3F) << 1 | 1  # 7-bit LFSR

    out = bytearray(length_bytes)
    for i in range(length_bytes):
        byte_val = 0
        for bit in range(8):
            # output bit is LFSR bit 0
            out_bit = lfsr & 1
            byte_val |= (out_bit << bit)
            # feedback: XOR of bit 0 and bit 4
            feedback = ((lfsr >> 0) ^ (lfsr >> 4)) & 1
            lfsr = (lfsr >> 1) | (feedback << 6)
        out[i] = byte_val
    return bytes(out)


def ble_dewhiten(data: bytes, channel: int) -> bytes:
    """Remove data whitening from raw BLE payload bytes."""
    seq = ble_whitening_seq(channel, len(data))
    return bytes(a ^ b for a, b in zip(data, seq))


# ---------------------------------------------------------------------------
# CRC-24 (BLE Core Spec Vol 6, Part B, Section 3.1.1)
# ---------------------------------------------------------------------------

def ble_crc24(data: bytes, init: int = BLE_CRC_INIT_ADV) -> int:
    """Compute the BLE 24-bit CRC over data bytes (LSBit-first convention)."""
    crc = init
    for byte in data:
        for bit_idx in range(8):
            # process LSBit first
            data_bit = (byte >> bit_idx) & 1
            # MSBit of current CRC
            crc_msb = (crc >> 23) & 1
            crc = (crc << 1) & 0xFFFFFF
            if data_bit ^ crc_msb:
                crc ^= 0x00065B  # lower 24 bits of polynomial
    return crc & 0xFFFFFF


# ---------------------------------------------------------------------------
# IQ loading
# ---------------------------------------------------------------------------

def load_iq_file(path: str, fmt: str = "auto", sample_rate: float = 0) -> tuple:
    """
    Load IQ samples from a file.

    Returns (samples: np.ndarray[complex64], sample_rate: float).
    sample_rate is 0 if unknown (caller must supply it via --rate).
    """
    p = Path(path)
    ext = p.suffix.lower()

    if fmt == "auto":
        if ext == ".npy":
            fmt = "npy"
        elif ext == ".wav":
            fmt = "wav"
        elif ext in (".raw", ".bin", ".iq8s", ".cs8"):
            fmt = "int8"
        elif ext in (".iq16s", ".cs16", ".sc16"):
            fmt = "int16"
        elif ext in (".cf32", ".fc32", ".iq"):
            fmt = "cf32"
        elif ext == ".cfile":
            fmt = "cf32"
        else:
            # try to guess from file size vs expected sample counts
            fmt = "int8"

    data = p.read_bytes()

    if fmt == "npy":
        samples = np.load(str(p))
        if np.iscomplexobj(samples):
            return samples.astype(np.complex64), sample_rate
        # real-valued pairs?
        if samples.ndim == 2 and samples.shape[1] == 2:
            return (samples[:, 0] + 1j * samples[:, 1]).astype(np.complex64), sample_rate
        raise ValueError(f"Unexpected .npy shape: {samples.shape}")

    if fmt == "wav":
        import wave
        with wave.open(str(p), "rb") as wf:
            sr = wf.getframerate()
            nch = wf.getnchannels()
            sw = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        if nch != 2:
            raise ValueError(f"Expected stereo WAV (I/Q), got {nch} channels")
        if sw == 2:
            arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
            samples = (arr[:, 0] + 1j * arr[:, 1]).astype(np.complex64) / 32768.0
        elif sw == 1:
            arr = np.frombuffer(raw, dtype=np.int8).reshape(-1, 2)
            samples = (arr[:, 0] + 1j * arr[:, 1]).astype(np.complex64) / 128.0
        else:
            raise ValueError(f"Unsupported WAV sample width: {sw}")
        return samples, float(sr)

    if fmt == "int8":
        raw = np.frombuffer(data, dtype=np.int8)
        i_samples = raw[0::2].astype(np.float32) / 128.0
        q_samples = raw[1::2].astype(np.float32) / 128.0
        return (i_samples + 1j * q_samples).astype(np.complex64), sample_rate

    if fmt == "int16":
        raw = np.frombuffer(data, dtype=np.int16)
        i_samples = raw[0::2].astype(np.float32) / 32768.0
        q_samples = raw[1::2].astype(np.float32) / 32768.0
        return (i_samples + 1j * q_samples).astype(np.complex64), sample_rate

    if fmt == "cf32":
        samples = np.frombuffer(data, dtype=np.complex64)
        return samples, sample_rate

    raise ValueError(f"Unknown IQ format: {fmt}")


# ---------------------------------------------------------------------------
# GFSK Demodulation
# ---------------------------------------------------------------------------

def fm_demodulate(iq: np.ndarray) -> np.ndarray:
    """FM-discriminator demodulation: instantaneous frequency from IQ."""
    # conjugate-multiply consecutive samples → phase difference
    product = iq[1:] * np.conj(iq[:-1])
    return np.angle(product)


def lowpass_filter(signal: np.ndarray, cutoff_ratio: float = 0.4) -> np.ndarray:
    """Simple moving-average low-pass filter."""
    # kernel width from cutoff ratio (approx)
    width = max(1, int(1.0 / cutoff_ratio))
    kernel = np.ones(width) / width
    return np.convolve(signal, kernel, mode="same")


# ---------------------------------------------------------------------------
# Bit recovery with clock/data recovery
# ---------------------------------------------------------------------------

def recover_bits(fm_signal: np.ndarray, samples_per_bit: float,
                 threshold: float = 0.0) -> list:
    """
    Recover bits from FM-demodulated signal using a simple
    zero-crossing clock recovery.

    Returns list of (bit_value, sample_index) tuples.
    """
    bits = []
    # start at half a bit period in
    pos = samples_per_bit / 2.0
    n = len(fm_signal)

    while pos < n:
        idx = int(pos)
        if idx >= n:
            break
        # sample the signal at the bit center
        val = fm_signal[idx]
        bit = 1 if val > threshold else 0
        bits.append((bit, idx))

        # simple early-late gate for clock tracking
        early = max(0, idx - max(1, int(samples_per_bit * 0.25)))
        late = min(n - 1, idx + max(1, int(samples_per_bit * 0.25)))
        if early < n and late < n:
            e_val = abs(fm_signal[early])
            l_val = abs(fm_signal[late])
            # nudge timing based on energy balance
            adjust = (l_val - e_val) / (l_val + e_val + 1e-10) * 0.3
            pos += samples_per_bit * (1.0 + adjust)
        else:
            pos += samples_per_bit

    return bits


def bits_to_bytes(bits: list, msb_first: bool = False) -> bytes:
    """Pack a list of bit values (0/1) into bytes. BLE is LSBit-first."""
    result = bytearray()
    for i in range(0, len(bits) - 7, 8):
        byte_bits = bits[i:i+8]
        val = 0
        for j, b in enumerate(byte_bits):
            if msb_first:
                val = (val << 1) | b
            else:
                val |= (b << j)
        result.append(val)
    return bytes(result)


# ---------------------------------------------------------------------------
# Access Address detection (preamble + 0x8E89BED6)
# ---------------------------------------------------------------------------

def find_access_addresses(bits_with_pos: list, samples_per_bit: float) -> list:
    """
    Scan recovered bit stream for the BLE advertising access address.

    The preamble is 0xAA (10101010 LSBit-first) followed by the 32-bit
    access address 0x8E89BED6 (LSBit-first).

    Returns list of (bit_index, sample_position) where the PDU starts
    (first bit after the access address).
    """
    if len(bits_with_pos) < 40:
        return []

    # build the expected preamble + AA bit pattern (LSBit-first)
    preamble_bits = []
    for bit_idx in range(8):
        preamble_bits.append((BLE_PREAMBLE >> bit_idx) & 1)

    aa_bits = []
    for bit_idx in range(32):
        aa_bits.append((BLE_ADV_ACCESS_ADDR >> bit_idx) & 1)

    target = preamble_bits + aa_bits  # 40 bits total

    bits_only = [b[0] for b in bits_with_pos]

    hits = []
    for i in range(len(bits_only) - 39):
        match = True
        mismatches = 0
        for j in range(40):
            if bits_only[i + j] != target[j]:
                mismatches += 1
                if mismatches > 2:  # allow up to 2 bit errors in preamble+AA
                    match = False
                    break
        if match:
            # PDU starts after preamble(8) + AA(32) = 40 bits from match start
            pdu_bit_start = i + 40
            if pdu_bit_start < len(bits_with_pos):
                hits.append((pdu_bit_start, bits_with_pos[pdu_bit_start][1]))
    return hits


# ---------------------------------------------------------------------------
# PDU Parsing
# ---------------------------------------------------------------------------

@dataclass
class BLEAdvPacket:
    """Decoded BLE advertising packet."""
    channel: int = 0
    pdu_type: int = 0
    pdu_type_name: str = ""
    tx_add: int = 0  # 1 = random address, 0 = public
    rx_add: int = 0
    length: int = 0
    adv_addr: str = ""
    adv_addr_bytes: bytes = b""
    ad_structures: list = field(default_factory=list)
    device_name: str = ""
    service_uuids: list = field(default_factory=list)
    manufacturer_data: bytes = b""
    manufacturer_id: int = 0
    tx_power: Optional[int] = None
    is_espressif: bool = False
    esp_indicators: list = field(default_factory=list)
    raw_pdu: bytes = b""
    crc_ok: bool = False
    crc_received: int = 0
    crc_computed: int = 0
    sample_offset: int = 0  # sample index in IQ file where packet was found
    bit_errors_in_aa: int = 0

    def summary(self) -> str:
        lines = []
        crc_str = "OK" if self.crc_ok else "FAIL"
        lines.append(f"[Ch {self.channel}] {self.pdu_type_name} "
                      f"from {self.adv_addr} "
                      f"({'random' if self.tx_add else 'public'}) "
                      f"len={self.length} CRC={crc_str}")
        if self.device_name:
            lines.append(f"  Name: {self.device_name}")
        if self.service_uuids:
            lines.append(f"  UUIDs: {', '.join(self.service_uuids)}")
        if self.manufacturer_data:
            lines.append(f"  Mfg ID: 0x{self.manufacturer_id:04X} "
                         f"Data: {self.manufacturer_data.hex()}")
        if self.tx_power is not None:
            lines.append(f"  TX Power: {self.tx_power} dBm")
        if self.is_espressif:
            lines.append(f"  ** ESP32 DETECTED ** indicators: "
                         f"{', '.join(self.esp_indicators)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "pdu_type": self.pdu_type,
            "pdu_type_name": self.pdu_type_name,
            "tx_add": self.tx_add,
            "rx_add": self.rx_add,
            "length": self.length,
            "adv_addr": self.adv_addr,
            "device_name": self.device_name,
            "service_uuids": self.service_uuids,
            "manufacturer_id": f"0x{self.manufacturer_id:04X}" if self.manufacturer_data else None,
            "manufacturer_data": self.manufacturer_data.hex() if self.manufacturer_data else None,
            "tx_power": self.tx_power,
            "is_espressif": self.is_espressif,
            "esp_indicators": self.esp_indicators,
            "crc_ok": self.crc_ok,
            "sample_offset": self.sample_offset,
        }


def parse_ad_structures(data: bytes) -> list:
    """Parse BLE Advertising Data (AD) structures (type-length-value)."""
    structures = []
    i = 0
    while i < len(data):
        if i >= len(data):
            break
        ad_len = data[i]
        i += 1
        if ad_len == 0 or i + ad_len > len(data):
            break
        ad_type = data[i]
        ad_data = data[i+1:i+ad_len]
        structures.append({
            "type": ad_type,
            "type_name": AD_TYPE_NAMES.get(ad_type, f"0x{ad_type:02X}"),
            "data": ad_data,
        })
        i += ad_len
    return structures


def format_mac(addr_bytes: bytes) -> str:
    """Format 6-byte address as colon-separated hex (BLE is little-endian)."""
    # BLE transmits address LSB first, so reverse for display
    return ":".join(f"{b:02X}" for b in reversed(addr_bytes))


def parse_advertising_pdu(raw_bytes: bytes, channel: int) -> Optional[BLEAdvPacket]:
    """
    Parse a de-whitened BLE advertising channel PDU.

    raw_bytes should start at the PDU header (after access address),
    and include at least header(2) + payload(length) + CRC(3).
    """
    if len(raw_bytes) < 5:  # minimum: 2 header + 0 payload + 3 CRC
        return None

    pkt = BLEAdvPacket()
    pkt.channel = channel

    # PDU header (2 bytes, BLE 4.x advertising)
    header0 = raw_bytes[0]
    header1 = raw_bytes[1]

    pkt.pdu_type = header0 & 0x0F
    pkt.tx_add = (header0 >> 6) & 1
    pkt.rx_add = (header0 >> 7) & 1
    pkt.pdu_type_name = PDU_TYPE_NAMES.get(pkt.pdu_type, f"UNKNOWN(0x{pkt.pdu_type:X})")
    pkt.length = header1 & 0x3F  # 6 bits

    if pkt.length > 37:  # max advertising PDU payload
        return None
    if len(raw_bytes) < 2 + pkt.length + 3:
        return None

    payload = raw_bytes[2:2 + pkt.length]
    crc_bytes = raw_bytes[2 + pkt.length:2 + pkt.length + 3]

    pkt.raw_pdu = raw_bytes[:2 + pkt.length]

    # CRC check (computed over header + payload)
    pkt.crc_computed = ble_crc24(raw_bytes[:2 + pkt.length])
    pkt.crc_received = crc_bytes[0] | (crc_bytes[1] << 8) | (crc_bytes[2] << 16)
    pkt.crc_ok = (pkt.crc_computed == pkt.crc_received)

    # Extract advertiser address (first 6 bytes of payload for most ad PDU types)
    if pkt.pdu_type in (0, 1, 2, 4, 6) and pkt.length >= 6:
        pkt.adv_addr_bytes = payload[:6]
        pkt.adv_addr = format_mac(payload[:6])

        # Parse AD structures from remaining payload
        ad_data = payload[6:]
        if ad_data:
            pkt.ad_structures = parse_ad_structures(ad_data)
            _extract_ad_fields(pkt)

        # ESP32 fingerprinting
        _fingerprint_esp32(pkt)

    elif pkt.pdu_type == 3 and pkt.length >= 12:
        # SCAN_REQ: ScanA (6) + AdvA (6)
        pkt.adv_addr_bytes = payload[:6]
        pkt.adv_addr = format_mac(payload[:6])

    return pkt


def _extract_ad_fields(pkt: BLEAdvPacket):
    """Extract common fields from parsed AD structures."""
    for ad in pkt.ad_structures:
        ad_type = ad["type"]
        ad_data = ad["data"]

        if ad_type in (AD_TYPE_SHORT_NAME, AD_TYPE_COMPLETE_NAME):
            try:
                pkt.device_name = ad_data.decode("utf-8", errors="replace")
            except Exception:
                pass

        elif ad_type in (AD_TYPE_COMPLETE_16, AD_TYPE_INCOMPLETE_16):
            for i in range(0, len(ad_data) - 1, 2):
                uuid16 = ad_data[i] | (ad_data[i+1] << 8)
                pkt.service_uuids.append(f"0x{uuid16:04X}")

        elif ad_type in (AD_TYPE_COMPLETE_128, AD_TYPE_INCOMPLETE_128):
            for i in range(0, len(ad_data) - 15, 16):
                uuid_bytes = ad_data[i:i+16]
                uuid_hex = uuid_bytes[::-1].hex()
                formatted = (f"{uuid_hex[:8]}-{uuid_hex[8:12]}-"
                             f"{uuid_hex[12:16]}-{uuid_hex[16:20]}-"
                             f"{uuid_hex[20:]}")
                pkt.service_uuids.append(formatted)

        elif ad_type == AD_TYPE_MFG_SPECIFIC and len(ad_data) >= 2:
            pkt.manufacturer_id = ad_data[0] | (ad_data[1] << 8)
            pkt.manufacturer_data = ad_data[2:]

        elif ad_type == AD_TYPE_TX_POWER and len(ad_data) >= 1:
            pkt.tx_power = struct.unpack("b", ad_data[:1])[0]


def _fingerprint_esp32(pkt: BLEAdvPacket):
    """Check indicators that suggest an ESP32 MCU."""
    indicators = []

    # Check OUI
    if len(pkt.adv_addr_bytes) >= 3:
        oui = bytes(pkt.adv_addr_bytes[:3])
        if oui in ESPRESSIF_OUIS:
            indicators.append(f"Espressif OUI ({oui.hex()})")

    # Espressif company ID in manufacturer-specific data (0x02E5)
    if pkt.manufacturer_id == 0x02E5:
        indicators.append("Espressif manufacturer ID (0x02E5)")

    # Known ESP32 BLE device name patterns
    esp_name_patterns = [
        "ESP", "PROV_", "BLE_SPP", "nimble", "BluFi",
        "ELRS", "ExpressLRS", "Backpack",
    ]
    if pkt.device_name:
        name_upper = pkt.device_name.upper()
        for pattern in esp_name_patterns:
            if pattern.upper() in name_upper:
                indicators.append(f"ESP name pattern: '{pattern}'")
                break

    # ESP32 BluFi service UUID (0xFFFF is common default, but not conclusive alone)
    # The custom 128-bit UUID used by ESP-IDF BLE provisioning
    esp_prov_uuid = "0000ffff-0000-1000-8000-00805f9b34fb"
    for uuid in pkt.service_uuids:
        if uuid.lower() == esp_prov_uuid:
            indicators.append("ESP-IDF provisioning UUID")

    if indicators:
        pkt.is_espressif = True
        pkt.esp_indicators = indicators


# ---------------------------------------------------------------------------
# Main decoder pipeline
# ---------------------------------------------------------------------------

class BLEDecoder:
    """
    SDR-based BLE advertising channel decoder.

    Pipeline: IQ → FM demod → bit recovery → AA search → dewhiten → parse PDU
    """

    def __init__(self, sample_rate: float, channel: int = 37,
                 verbose: bool = False):
        self.sample_rate = sample_rate
        self.channel = channel
        self.verbose = verbose
        self.samples_per_bit = sample_rate / 1e6  # BLE = 1 Mbps
        self.packets: list[BLEAdvPacket] = []

    def decode(self, iq: np.ndarray) -> list[BLEAdvPacket]:
        """
        Decode BLE advertising packets from IQ samples.

        Returns list of decoded BLEAdvPacket objects.
        """
        if len(iq) < 100:
            return []

        if self.verbose:
            duration_ms = len(iq) / self.sample_rate * 1000
            print(f"[*] Decoding {len(iq)} samples "
                  f"({duration_ms:.1f} ms) at {self.sample_rate/1e6:.1f} Msps, "
                  f"channel {self.channel}")

        # Step 1: FM demodulation
        fm = fm_demodulate(iq)

        # Step 2: Low-pass filter to clean up GFSK signal
        # BLE symbol rate is 1 MHz, so filter at ~1.2 MHz relative to sample rate
        cutoff = 1.2e6 / self.sample_rate
        if cutoff < 0.5:
            fm = lowpass_filter(fm, cutoff)

        # Step 3: Compute adaptive threshold (DC offset in FM signal)
        # Use a block-based median for robustness
        block_size = max(100, int(self.samples_per_bit * 100))
        threshold = np.median(fm[:min(block_size, len(fm))])

        # Step 4: Recover bits
        bits_with_pos = recover_bits(fm, self.samples_per_bit, threshold)
        if self.verbose:
            print(f"[*] Recovered {len(bits_with_pos)} bits")

        # Step 5: Find access addresses
        aa_hits = find_access_addresses(bits_with_pos, self.samples_per_bit)
        if self.verbose:
            print(f"[*] Found {len(aa_hits)} potential access address matches")

        # Step 6: For each hit, extract and parse the PDU
        packets = []
        for pdu_bit_start, sample_pos in aa_hits:
            # extract enough bits for max PDU (2 header + 37 payload + 3 CRC = 42 bytes = 336 bits)
            max_pdu_bits = 42 * 8
            end_bit = min(pdu_bit_start + max_pdu_bits, len(bits_with_pos))
            pdu_bits = [bits_with_pos[i][0] for i in range(pdu_bit_start, end_bit)]

            if len(pdu_bits) < 40:  # need at least header + minimal payload + CRC
                continue

            raw_bytes = bits_to_bytes(pdu_bits)

            # De-whiten
            dewhitened = ble_dewhiten(raw_bytes, self.channel)

            # Parse
            pkt = parse_advertising_pdu(dewhitened, self.channel)
            if pkt is not None:
                pkt.sample_offset = int(sample_pos)
                packets.append(pkt)

        # Deduplicate by (address, pdu_type, ad content)
        seen = set()
        unique = []
        for pkt in packets:
            key = (pkt.adv_addr, pkt.pdu_type, pkt.raw_pdu.hex())
            if key not in seen:
                seen.add(key)
                unique.append(pkt)

        self.packets = unique
        return unique


def decode_file(path: str, channel: int = 37, sample_rate: float = 0,
                iq_format: str = "auto", verbose: bool = False) -> list[BLEAdvPacket]:
    """Decode BLE packets from an IQ capture file."""
    iq, detected_rate = load_iq_file(path, iq_format, sample_rate)

    rate = sample_rate if sample_rate > 0 else detected_rate
    if rate <= 0:
        rate = 4e6  # default assumption for HackRF captures
        if verbose:
            print(f"[!] No sample rate specified or detected, assuming {rate/1e6:.0f} Msps")

    decoder = BLEDecoder(rate, channel, verbose)
    return decoder.decode(iq)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

CAPTURE_HELP = """
=== SDR Capture Instructions ===

To capture BLE advertising packets, tune your SDR to one of the three
BLE advertising channels and record IQ samples at >= 2 Msps (4 Msps
recommended for clean demodulation).

HackRF One:
  Channel 37 (2402 MHz):
    hackrf_transfer -f 2402000000 -s 4000000 -l 32 -g 30 -n 16000000 -r ch37.raw
  Channel 38 (2426 MHz):
    hackrf_transfer -f 2426000000 -s 4000000 -l 32 -g 30 -n 16000000 -r ch38.raw
  Channel 39 (2480 MHz):
    hackrf_transfer -f 2480000000 -s 4000000 -l 32 -g 30 -n 16000000 -r ch39.raw

  Output: 8-bit signed IQ pairs (.raw), 4 Msps, ~4 seconds per capture.
  Use --format int8 --rate 4e6.

RTL-SDR (RX only, limited bandwidth):
  rtl_sdr -f 2402000000 -s 2400000 -n 9600000 ch37.raw
  Output: 8-bit unsigned IQ pairs. Note: RTL-SDR BW may be marginal for BLE.
  Use --format int8 --rate 2.4e6.

USRP (UHD):
  uhd_rx_cfile --freq=2402e6 --rate=4e6 --gain=40 --nsamples=16000000 ch37.cf32
  Output: complex64 float IQ (.cf32 / .cfile), 4 Msps.
  Use --format cf32 --rate 4e6.

BladeRF:
  bladeRF-cli -e "set frequency rx 2402000000; set samplerate rx 4000000; \\
    set bandwidth rx 3000000; set gain rx 40; rx config file=ch37.sc16 n=16000000; \\
    rx start; rx wait"
  Output: 16-bit signed IQ pairs (.sc16), 4 Msps.
  Use --format int16 --rate 4e6.

SDR# / GQRX:
  Record to WAV at 2402/2426/2480 MHz, IQ mode, >= 2 Msps.
  Use --format wav (sample rate auto-detected from WAV header).

GNU Radio:
  Use a File Sink block saving complex64 samples.
  Use --format cf32 --rate <your_rate>.

Tips:
  - BLE advertising is bursty — capture several seconds to catch packets.
  - Higher sample rate (4+ Msps) gives better demodulation margin.
  - BLE advertising happens on ALL THREE channels in rotation, so capturing
    just one channel will catch about 1/3 of advertisements.
  - To scan all three, run three captures (or use SoapySDR with the
    project's sdr_common.py for programmatic channel hopping).
  - For ESP32 detection, one channel is usually sufficient — ESP32 devices
    advertise frequently (every 20-100ms by default).
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SDR-Based BLE Advertising Decoder — "
                    "decode BLE packets from raw IQ captures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CAPTURE_HELP,
    )
    parser.add_argument("input", nargs="?",
                        help="IQ capture file to decode")
    parser.add_argument("-c", "--channel", type=int, default=37,
                        choices=[37, 38, 39],
                        help="BLE advertising channel (default: 37)")
    parser.add_argument("-r", "--rate", type=float, default=0,
                        help="Sample rate in Hz (e.g., 4e6 for 4 Msps). "
                             "Auto-detected for WAV files.")
    parser.add_argument("-f", "--format", dest="iq_format", default="auto",
                        choices=["auto", "int8", "int16", "cf32", "npy", "wav"],
                        help="IQ sample format (default: auto-detect from extension)")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Write decoded packets to JSON file")
    parser.add_argument("--all-channels", action="store_true",
                        help="Try decoding with all three advertising channel "
                             "whitening seeds (useful if channel is uncertain)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    parser.add_argument("--capture-help", action="store_true",
                        help="Show detailed SDR capture instructions and exit")
    parser.add_argument("--esp-only", action="store_true",
                        help="Show only packets with ESP32 indicators")
    parser.add_argument("--crc-ok-only", action="store_true",
                        help="Show only packets with valid CRC")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.capture_help:
        print(CAPTURE_HELP)
        return

    if not args.input:
        parser.print_help()
        print("\n[!] No input file specified. Use --capture-help for SDR capture instructions.")
        sys.exit(1)

    if not os.path.isfile(args.input):
        print(f"[!] File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    channels = [37, 38, 39] if args.all_channels else [args.channel]
    all_packets = []

    for ch in channels:
        if args.verbose and args.all_channels:
            print(f"\n{'='*60}")
            print(f"Trying channel {ch} whitening...")
            print(f"{'='*60}")

        packets = decode_file(
            args.input,
            channel=ch,
            sample_rate=args.rate,
            iq_format=args.iq_format,
            verbose=args.verbose,
        )
        all_packets.extend(packets)

    # Apply filters
    if args.crc_ok_only:
        all_packets = [p for p in all_packets if p.crc_ok]
    if args.esp_only:
        all_packets = [p for p in all_packets if p.is_espressif]

    # Output
    if not all_packets:
        print("[*] No BLE advertising packets decoded.")
        print("[*] This could mean:")
        print("    - No BLE devices were advertising during capture")
        print("    - Wrong channel specified (try --all-channels)")
        print("    - Sample rate mismatch (verify with --rate)")
        print("    - Signal too weak (increase SDR gain)")
        print("    - Use --capture-help for SDR setup instructions")
        sys.exit(0)

    print(f"\n[+] Decoded {len(all_packets)} BLE advertising packet(s):\n")
    for pkt in all_packets:
        print(pkt.summary())
        print()

    # ESP32 summary
    esp_packets = [p for p in all_packets if p.is_espressif]
    if esp_packets:
        print(f"\n{'='*60}")
        print(f"ESP32 DETECTION SUMMARY: {len(esp_packets)} packet(s) with ESP32 indicators")
        print(f"{'='*60}")
        seen_addrs = set()
        for pkt in esp_packets:
            if pkt.adv_addr not in seen_addrs:
                seen_addrs.add(pkt.adv_addr)
                print(f"  {pkt.adv_addr} — {', '.join(pkt.esp_indicators)}")
                if pkt.device_name:
                    print(f"    Name: {pkt.device_name}")

    # JSON output
    if args.output:
        out_data = {
            "decoder": "ble_sdr_decoder",
            "channel": args.channel,
            "sample_rate": args.rate,
            "total_packets": len(all_packets),
            "esp32_detected": len(esp_packets) if esp_packets else 0,
            "packets": [p.to_dict() for p in all_packets],
        }
        with open(args.output, "w") as f:
            json.dump(out_data, f, indent=2)
        print(f"\n[+] Results written to {args.output}")


if __name__ == "__main__":
    main()
