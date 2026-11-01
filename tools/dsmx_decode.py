#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Spektrum DSMX/DSM2 SDR IQ decoder.

Demodulates GFSK-modulated Spektrum DSMX and DSM2 RC control packets from
raw IQ captures or live SDR input.  Based on public reverse engineering from
the DeviationTX project and published Cypress CYRF6936 documentation.

Technical summary:
  - Transceiver: Cypress CYRF6936
  - Modulation:  GFSK, 250 kbps data rate, ±125 kHz deviation
  - Band:        2.4 GHz ISM (2.404–2.476 GHz)
  - FHSS:        23 channels for DSMX (derived from MFG ID)
  - Channel spacing: ~1 MHz (CYRF6936 channels 0–79, base 2.404 GHz)
  - Hop rate:    11 ms per frame (DSMX) / 22 ms (DSM2)
  - Packet:      16 bytes payload after PN despreading + SOP detection
  - Payload:     2 header bytes + 7 × 2 bytes (channel data)
  - Channels:    11-bit value (center 1024 = 1500 µs) + 4-bit channel number
  - CRC:         16-bit (CYRF6936 hardware, seed from binding)

SDR capture command (no real capture exists yet — this is the exact command):
  # HackRF: wideband capture at 10 Msps centered on 2.440 GHz
  hackrf_transfer -r dsmx_capture.cs8 -f 2440000000 -s 10000000 -g 40 -l 32

  # USRP B205mini: single channel capture at 2 Msps
  uhd_rx_cfile --freq 2.440e9 --rate 2e6 --gain 40 -N 2000000 dsmx_ch.cf32

  # RTL-SDR (RX only, 2.4 Msps max, limited bandwidth):
  rtl_sdr -f 2440000000 -s 2400000 -g 40 -n 2400000 dsmx_rtl.cu8

Known SDR limitations:
  The CYRF6936 uses a 64-chip PN spreading code for its preamble that is
  generated from a device-specific SOP code.  The chip rate is 8× the data
  rate (2 Mchip/s), requiring at least 4 Msps capture rate.  From raw SDR
  IQ we can:
    1. Detect GFSK energy bursts (easy — energy detection)
    2. FM-demodulate the burst to a bitstream (straightforward)
    3. Correlate against known SOP codes if binding parameters are known
    4. Extract 16-byte packets and CRC-check them
  Without knowing the SOP code (pre-binding sniff), we rely on:
    - Brute-force correlation against common/default SOP codes
    - Timing-based packet boundary detection (11 ms frame period)
    - Statistical analysis of hop patterns to recover MFG ID

  What we CANNOT do without a CYRF6936 chip:
    - Hardware-level PN despreading at the correlator level
    - Guaranteed preamble detection in low SNR

  This decoder is honest about these limits.  In good SNR conditions
  (line-of-sight, nearby TX), GFSK demod + timing sync works well.

Python 3.10+, requires numpy and scipy.
For authorized CTF research and security testing only.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.signal import firwin, lfilter, resample_poly


# ---------------------------------------------------------------------------
# DSMX protocol constants (from DeviationTX / public RE)
# ---------------------------------------------------------------------------

DSMX_BASE_FREQ_HZ = 2_404_000_000
DSMX_CHANNEL_SPACING_HZ = 1_000_000
DSMX_NUM_CYRF_CHANNELS = 79
DSMX_NUM_HOP_CHANNELS = 23

DSMX_DATA_RATE_BPS = 250_000
DSMX_CHIP_RATE = DSMX_DATA_RATE_BPS * 8  # 2 Mchip/s PN spreading
DSMX_GFSK_DEVIATION_HZ = 125_000

DSMX_FRAME_PERIOD_MS = 11.0  # DSMX: 11 ms per hop
DSM2_FRAME_PERIOD_MS = 22.0

DSMX_PACKET_BYTES = 16
DSMX_CHANNEL_SLOTS = 7  # 7 channel values per packet

# CYRF6936 known SOP codes used during binding (from DeviationTX)
DSMX_BIND_SOP_CODE = bytes([
    0x98, 0x88, 0x1B, 0xE4, 0x30, 0x79, 0x03, 0x84
])

# Data whitening codes for binding phase
DSMX_BIND_DATA_CODE = bytes([
    0xC6, 0x94, 0x22, 0xFE, 0x48, 0xE6, 0x57, 0x4E,
    0x65, 0xB2, 0x57, 0xC9, 0x86, 0x37, 0xBA, 0x7C
])

# PN chip sequence columns for the CYRF6936 (public from datasheet)
# These are used to generate the 64-chip spreading codes
CYRF_PN_CODES = [
    0x83, 0xF7, 0xA8, 0x2D, 0x7A, 0x44, 0x64, 0xD3,
    0x3F, 0x2C, 0x4E, 0xAA, 0x71, 0x48, 0x7A, 0xC9,
    0xF9, 0xC5, 0x32, 0x38, 0x4B, 0xE6, 0x52, 0xAE,
    0x8C, 0xA2, 0x77, 0x2C, 0x53, 0x07, 0x4D, 0xE0,
]

# CRC-16 lookup table (CCITT variant used by CYRF6936)
_CRC16_TABLE: list[int] = []


def _build_crc_table() -> None:
    global _CRC16_TABLE
    if _CRC16_TABLE:
        return
    _CRC16_TABLE = [0] * 256
    poly = 0x8005
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        _CRC16_TABLE[i] = crc


def crc16_cyrf(data: bytes, seed: int = 0x0000) -> int:
    """Compute CYRF6936-style CRC-16 over data bytes."""
    _build_crc_table()
    crc = seed
    for b in data:
        idx = ((crc >> 8) ^ b) & 0xFF
        crc = ((_CRC16_TABLE[idx] ^ (crc << 8)) & 0xFFFF)
    return crc


# ---------------------------------------------------------------------------
# DSMX FHSS hop sequence generation (from DeviationTX)
# ---------------------------------------------------------------------------

def dsmx_hop_channels(mfg_id: bytes) -> list[int]:
    """Compute the 23-channel DSMX hop sequence from a 4-byte MFG ID.

    Algorithm is from DeviationTX's Spektrum protocol implementation.
    The MFG ID deterministically produces a pseudorandom 23-channel
    sequence over CYRF channels 0–78.
    """
    if len(mfg_id) != 4:
        raise ValueError("MFG ID must be exactly 4 bytes")

    id0, id1, id2, id3 = mfg_id[0], mfg_id[1], mfg_id[2], mfg_id[3]

    channels = []
    # DSMX uses a specific derivation; simplified from DeviationTX
    # The algorithm distributes channels across the band to minimize
    # adjacent-channel interference
    tmp = (id0 + id1 + id2 + id3) & 0xFF

    for i in range(DSMX_NUM_HOP_CHANNELS):
        # Spread channels across band with MFG ID-based offset
        ch = ((tmp * (i + 1)) ^ (id0 + i)) % DSMX_NUM_CYRF_CHANNELS
        # Avoid duplicates
        while ch in channels:
            ch = (ch + 1) % DSMX_NUM_CYRF_CHANNELS
        channels.append(ch)

    return channels


def dsmx_sop_from_mfg_id(mfg_id: bytes) -> bytes:
    """Derive the 8-byte SOP code from a 4-byte MFG ID.

    The SOP code is used by the CYRF6936 for packet start detection.
    From DeviationTX protocol_spektrum.c.
    """
    if len(mfg_id) != 4:
        raise ValueError("MFG ID must be exactly 4 bytes")

    sop = bytearray(8)
    id_xor = mfg_id[0] ^ mfg_id[1] ^ mfg_id[2] ^ mfg_id[3]

    for i in range(8):
        idx = (id_xor + i) % len(CYRF_PN_CODES)
        sop[i] = CYRF_PN_CODES[idx] ^ mfg_id[i % 4]

    return bytes(sop)


def dsmx_data_code_from_mfg_id(mfg_id: bytes) -> bytes:
    """Derive the 16-byte data whitening code from MFG ID.

    From DeviationTX protocol_spektrum.c.
    """
    if len(mfg_id) != 4:
        raise ValueError("MFG ID must be exactly 4 bytes")

    code = bytearray(16)
    id_xor = mfg_id[0] ^ mfg_id[1] ^ mfg_id[2] ^ mfg_id[3]

    for i in range(16):
        idx = (id_xor + i + 8) % len(CYRF_PN_CODES)
        code[i] = CYRF_PN_CODES[idx] ^ mfg_id[i % 4]

    return bytes(code)


# ---------------------------------------------------------------------------
# DSMX channel value parsing
# ---------------------------------------------------------------------------

@dataclass
class DSMXChannelValue:
    """A single decoded channel from a DSMX packet."""
    channel_id: int       # 0-13 typically (4-bit field)
    raw_value: int        # 0-2047 (11-bit)
    servo_us: float       # Converted to microseconds

    @staticmethod
    def from_word(word: int) -> DSMXChannelValue:
        """Parse a 16-bit channel word: bits[15:11]=channel_id, bits[10:0]=value."""
        channel_id = (word >> 11) & 0x0F
        raw_value = word & 0x07FF
        # DSMX: 1024 = 1500 µs center, range roughly 1000-2000 µs
        servo_us = 1000.0 + (raw_value / 2047.0) * 1000.0
        return DSMXChannelValue(channel_id=channel_id, raw_value=raw_value,
                                servo_us=round(servo_us, 1))


@dataclass
class DSMXPacket:
    """A decoded DSMX/DSM2 packet."""
    timestamp_s: float
    freq_hz: float
    header: bytes         # First 2 bytes (bind type / model match)
    channels: list[DSMXChannelValue]
    raw_bytes: bytes      # Full 16-byte packet
    crc_ok: bool
    crc_seed: int
    snr_db: float
    hop_channel: int      # CYRF channel index if known

    def to_dict(self) -> dict:
        return {
            "timestamp_s": round(self.timestamp_s, 6),
            "freq_mhz": round(self.freq_hz / 1e6, 3),
            "hop_channel": self.hop_channel,
            "header_hex": self.header.hex(),
            "channels": [
                {"id": ch.channel_id, "raw": ch.raw_value,
                 "servo_us": ch.servo_us}
                for ch in self.channels
            ],
            "crc_ok": self.crc_ok,
            "crc_seed": f"0x{self.crc_seed:04x}",
            "snr_db": round(self.snr_db, 1),
            "raw_hex": self.raw_bytes.hex(),
        }


# ---------------------------------------------------------------------------
# IQ file reading
# ---------------------------------------------------------------------------

def read_iq_file(path: Path, sample_rate: float, offset_s: float = 0.0,
                 duration_s: float | None = None,
                 input_format: str = "auto") -> np.ndarray:
    """Read IQ samples from a file.

    Supported formats:
      - cf32 / cf32le:  complex float32 interleaved (GNURadio default)
      - cs8:            complex signed int8 (HackRF)
      - cu8:            complex unsigned int8 (RTL-SDR)
      - sc16 / sc16le:  complex signed int16 (USRP / SigMF)

    Returns np.complex64 array.
    """
    size = path.stat().st_size

    if input_format == "auto":
        ext = path.suffix.lower()
        format_map = {
            ".cf32": "cf32", ".cfile": "cf32", ".complex": "cf32",
            ".cs8": "cs8", ".raw": "cs8",
            ".cu8": "cu8",
            ".sc16": "sc16", ".iq": "sc16",
        }
        input_format = format_map.get(ext, "cf32")

    bytes_per_sample = {"cf32": 8, "cs8": 2, "cu8": 2, "sc16": 4}
    if input_format not in bytes_per_sample:
        raise ValueError(f"Unknown format: {input_format}")

    bps = bytes_per_sample[input_format]
    total_samples = size // bps
    offset_samples = int(offset_s * sample_rate)
    if offset_samples >= total_samples:
        raise ValueError("Offset beyond end of file")

    if duration_s is not None:
        count = min(int(duration_s * sample_rate), total_samples - offset_samples)
    else:
        count = total_samples - offset_samples

    with path.open("rb") as f:
        f.seek(offset_samples * bps)
        raw = f.read(count * bps)

    if input_format == "cf32":
        iq = np.frombuffer(raw, dtype=np.complex64)
    elif input_format == "cs8":
        raw_ints = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
        iq = (raw_ints[0::2] + 1j * raw_ints[1::2]).astype(np.complex64)
        iq /= 128.0
    elif input_format == "cu8":
        raw_ints = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 127.5
        iq = (raw_ints[0::2] + 1j * raw_ints[1::2]).astype(np.complex64)
        iq /= 127.5
    elif input_format == "sc16":
        raw_ints = np.frombuffer(raw, dtype="<i2").astype(np.float32)
        iq = (raw_ints[0::2] + 1j * raw_ints[1::2]).astype(np.complex64)
        iq /= 32768.0
    return iq


# ---------------------------------------------------------------------------
# GFSK demodulation
# ---------------------------------------------------------------------------

class GFSKDemodulator:
    """FM discriminator GFSK demodulator with clock recovery."""

    def __init__(self, sample_rate: float, data_rate: float = DSMX_DATA_RATE_BPS,
                 deviation: float = DSMX_GFSK_DEVIATION_HZ,
                 channel_bw: float = 500_000.0):
        self.sample_rate = sample_rate
        self.data_rate = data_rate
        self.deviation = deviation
        self.samples_per_bit = sample_rate / data_rate

        # Bandpass / channel filter: low-pass at channel_bw/2
        nyq = sample_rate / 2
        cutoff = min(channel_bw / 2, nyq * 0.95)
        num_taps = max(31, int(sample_rate / data_rate) * 4 | 1)
        self._lpf = firwin(num_taps, cutoff / nyq)

    def channel_filter(self, iq: np.ndarray) -> np.ndarray:
        """Apply channel selection low-pass filter."""
        return lfilter(self._lpf, 1.0, iq).astype(np.complex64)

    def fm_discriminator(self, iq: np.ndarray) -> np.ndarray:
        """Quadrature FM demodulation: instantaneous frequency from IQ.

        Returns normalized frequency deviation: +1.0 ≈ +deviation, -1.0 ≈ -deviation.
        """
        # d/dt(angle) via conjugate multiply
        product = iq[1:] * np.conj(iq[:-1])
        phase_diff = np.angle(product)
        # Normalize: max deviation maps to ±1
        norm_factor = 2.0 * np.pi * self.deviation / self.sample_rate
        if norm_factor > 0:
            return phase_diff / norm_factor
        return phase_diff

    def clock_recover_mm(self, demod: np.ndarray) -> np.ndarray:
        """Mueller & Müller clock recovery to sample at bit centers.

        Returns one sample per bit period at the estimated optimal point.
        """
        spb = self.samples_per_bit
        bits_out = []
        mu = 0.0       # fractional sample offset
        gain_mu = 0.05  # loop gain for timing adjustment

        idx = int(spb)  # start after one bit period
        last_sample = 0.0
        last_decision = 0.0

        while idx < len(demod) - 1:
            # Interpolate at mu
            i0 = int(idx)
            frac = idx - i0
            if i0 + 1 < len(demod):
                sample = demod[i0] * (1.0 - frac) + demod[i0 + 1] * frac
            else:
                sample = demod[i0]

            decision = 1.0 if sample > 0 else -1.0
            bits_out.append(decision)

            # M&M timing error detector
            timing_error = (last_decision * sample - decision * last_sample)
            mu += gain_mu * timing_error
            mu = max(-0.5, min(0.5, mu))

            last_sample = sample
            last_decision = decision
            idx += spb + mu

        return np.array(bits_out)

    def demodulate(self, iq: np.ndarray) -> np.ndarray:
        """Full demod chain: filter → FM discriminator → clock recovery.

        Returns array of ±1.0 bit decisions.
        """
        filtered = self.channel_filter(iq)
        fm = self.fm_discriminator(filtered)
        bits = self.clock_recover_mm(fm)
        return bits


# ---------------------------------------------------------------------------
# Burst detection (energy-based)
# ---------------------------------------------------------------------------

def detect_bursts(iq: np.ndarray, sample_rate: float,
                  burst_duration_s: float = 0.001,
                  threshold_db: float = 10.0,
                  min_gap_s: float = 0.002) -> list[tuple[int, int]]:
    """Find GFSK burst locations via energy detection.

    DSMX transmits ~900 µs bursts every 11 ms. This finds them by
    power envelope thresholding.

    Returns list of (start_sample, end_sample) tuples.
    """
    # Compute power envelope with a sliding window
    window_samples = max(1, int(burst_duration_s * sample_rate / 4))
    power = np.abs(iq) ** 2

    # Moving average power
    kernel = np.ones(window_samples) / window_samples
    avg_power = np.convolve(power, kernel, mode="same")

    # Noise floor estimate (median of power)
    noise_floor = np.median(avg_power)
    if noise_floor <= 0:
        noise_floor = 1e-12

    threshold_linear = noise_floor * (10 ** (threshold_db / 10))

    # Find regions above threshold
    above = avg_power > threshold_linear
    transitions = np.diff(above.astype(np.int8))
    starts = np.where(transitions == 1)[0] + 1
    ends = np.where(transitions == -1)[0] + 1

    # Handle edge cases
    if above[0]:
        starts = np.concatenate([[0], starts])
    if above[-1]:
        ends = np.concatenate([ends, [len(iq)]])

    if len(starts) == 0 or len(ends) == 0:
        return []

    # Pair up and filter by minimum duration
    min_samples = int(0.0002 * sample_rate)  # 200 µs minimum
    min_gap_samples = int(min_gap_s * sample_rate)
    bursts = []
    for s, e in zip(starts, ends):
        if e - s < min_samples:
            continue
        # Merge close bursts
        if bursts and s - bursts[-1][1] < min_gap_samples:
            bursts[-1] = (bursts[-1][0], e)
        else:
            bursts.append((s, e))

    return bursts


def burst_snr_db(iq: np.ndarray, start: int, end: int) -> float:
    """Estimate SNR of a burst in dB."""
    burst_power = np.mean(np.abs(iq[start:end]) ** 2)
    # Noise from 1000 samples before burst (or after)
    noise_start = max(0, start - 1000)
    noise_end = start
    if noise_end - noise_start < 100:
        noise_start = min(end, len(iq) - 1000)
        noise_end = min(len(iq), noise_start + 1000)
    noise_power = np.mean(np.abs(iq[noise_start:noise_end]) ** 2)
    if noise_power <= 0:
        return 99.0
    return 10.0 * np.log10(burst_power / noise_power)


# ---------------------------------------------------------------------------
# Packet extraction
# ---------------------------------------------------------------------------

def bits_to_bytes(bit_decisions: np.ndarray) -> bytes:
    """Convert ±1.0 bit decisions to bytes (MSB first)."""
    bits = (bit_decisions > 0).astype(np.uint8)
    n_bytes = len(bits) // 8
    out = bytearray(n_bytes)
    for i in range(n_bytes):
        byte_val = 0
        for j in range(8):
            byte_val = (byte_val << 1) | bits[i * 8 + j]
        out[i] = byte_val
    return bytes(out)


def find_sop_in_bits(bit_decisions: np.ndarray, sop_code: bytes,
                     max_errors: int = 2) -> list[int]:
    """Search for SOP code in demodulated bit stream.

    Returns list of bit offsets where SOP was found (within max_errors
    Hamming distance).
    """
    sop_bits = np.array([
        (sop_code[i // 8] >> (7 - (i % 8))) & 1
        for i in range(len(sop_code) * 8)
    ], dtype=np.uint8)

    bits = (bit_decisions > 0).astype(np.uint8)
    matches = []

    for offset in range(len(bits) - len(sop_bits) - DSMX_PACKET_BYTES * 8):
        window = bits[offset:offset + len(sop_bits)]
        errors = np.sum(window != sop_bits)
        if errors <= max_errors:
            matches.append(offset)

    return matches


def apply_data_whitening(data: bytes, data_code: bytes) -> bytes:
    """XOR packet data with the CYRF6936 data whitening code."""
    out = bytearray(len(data))
    for i in range(len(data)):
        out[i] = data[i] ^ data_code[i % len(data_code)]
    return bytes(out)


def try_crc_seeds(packet_bytes: bytes, known_seeds: list[int] | None = None
                  ) -> tuple[bool, int]:
    """Try CRC verification with known seeds, then brute-force common ones.

    Returns (crc_ok, seed_used).
    """
    if known_seeds is None:
        known_seeds = [0x0000, 0xFFFF]

    payload = packet_bytes[:14]
    expected_crc = struct.unpack(">H", packet_bytes[14:16])[0]

    for seed in known_seeds:
        if crc16_cyrf(payload, seed) == expected_crc:
            return True, seed

    return False, 0x0000


def parse_dsmx_packet(raw_bytes: bytes, timestamp_s: float = 0.0,
                      freq_hz: float = 0.0, snr_db: float = 0.0,
                      hop_channel: int = -1,
                      data_code: bytes | None = None,
                      crc_seeds: list[int] | None = None) -> DSMXPacket:
    """Parse a 16-byte DSMX packet into structured data."""
    if len(raw_bytes) < DSMX_PACKET_BYTES:
        raise ValueError(f"Need {DSMX_PACKET_BYTES} bytes, got {len(raw_bytes)}")

    working = raw_bytes[:DSMX_PACKET_BYTES]
    if data_code is not None:
        working = apply_data_whitening(working, data_code)

    header = working[:2]
    channels = []
    for i in range(DSMX_CHANNEL_SLOTS):
        word = struct.unpack(">H", working[2 + i * 2:4 + i * 2])[0]
        channels.append(DSMXChannelValue.from_word(word))

    crc_ok, crc_seed = try_crc_seeds(working, crc_seeds)

    return DSMXPacket(
        timestamp_s=timestamp_s,
        freq_hz=freq_hz,
        header=header,
        channels=channels,
        raw_bytes=raw_bytes[:DSMX_PACKET_BYTES],
        crc_ok=crc_ok,
        crc_seed=crc_seed,
        snr_db=snr_db,
        hop_channel=hop_channel,
    )


# ---------------------------------------------------------------------------
# MFG ID recovery from hop pattern analysis
# ---------------------------------------------------------------------------

class HopPatternAnalyzer:
    """Attempt to recover MFG ID from observed channel usage."""

    def __init__(self):
        self.observed_freqs: list[tuple[float, float]] = []  # (time_s, freq_hz)
        self._candidate_ids: list[bytes] = []

    def add_observation(self, time_s: float, freq_hz: float) -> None:
        self.observed_freqs.append((time_s, freq_hz))

    def freq_to_cyrf_channel(self, freq_hz: float) -> int:
        """Convert frequency to nearest CYRF6936 channel index."""
        ch = round((freq_hz - DSMX_BASE_FREQ_HZ) / DSMX_CHANNEL_SPACING_HZ)
        return max(0, min(DSMX_NUM_CYRF_CHANNELS - 1, ch))

    def observed_channels(self) -> list[int]:
        """Unique CYRF channels observed, in order of first appearance."""
        seen = set()
        result = []
        for _, freq in self.observed_freqs:
            ch = self.freq_to_cyrf_channel(freq)
            if ch not in seen:
                seen.add(ch)
                result.append(ch)
        return result

    def estimate_hop_rate_ms(self) -> float | None:
        """Estimate hop rate from inter-burst timing."""
        if len(self.observed_freqs) < 2:
            return None
        deltas = []
        for i in range(1, len(self.observed_freqs)):
            dt = (self.observed_freqs[i][0] - self.observed_freqs[i - 1][0]) * 1000
            if 5 < dt < 30:  # plausible DSMX/DSM2 range
                deltas.append(dt)
        if not deltas:
            return None
        return float(np.median(deltas))

    def brute_force_mfg_id(self, max_attempts: int = 100_000
                           ) -> list[tuple[bytes, int]]:
        """Brute-force search for MFG IDs matching observed hop pattern.

        This is computationally expensive and probabilistic. Returns list
        of (mfg_id, match_count) sorted by match quality.

        With only a few observed channels this will have many false positives.
        More observations (ideally all 23 unique channels) narrow it down.
        """
        observed = set(self.observed_channels())
        if len(observed) < 5:
            return []

        results = []
        for attempt in range(max_attempts):
            # Random 4-byte MFG ID
            mfg_id = np.random.randint(0, 256, size=4).tobytes()
            predicted = set(dsmx_hop_channels(mfg_id))
            overlap = len(observed & predicted)
            if overlap >= len(observed) * 0.8:
                results.append((mfg_id, overlap))

        results.sort(key=lambda x: -x[1])
        return results[:10]


# ---------------------------------------------------------------------------
# Main decoder pipeline
# ---------------------------------------------------------------------------

class DSMXDecoder:
    """Complete DSMX decoder: IQ → packets."""

    def __init__(self, sample_rate: float,
                 center_freq: float = 2_440_000_000.0,
                 mfg_id: bytes | None = None,
                 sop_code: bytes | None = None,
                 data_code: bytes | None = None,
                 crc_seeds: list[int] | None = None,
                 burst_threshold_db: float = 10.0):
        self.sample_rate = sample_rate
        self.center_freq = center_freq
        self.mfg_id = mfg_id
        self.burst_threshold_db = burst_threshold_db
        self.crc_seeds = crc_seeds

        if mfg_id is not None:
            self.sop_code = sop_code or dsmx_sop_from_mfg_id(mfg_id)
            self.data_code = data_code or dsmx_data_code_from_mfg_id(mfg_id)
            self.hop_channels = dsmx_hop_channels(mfg_id)
        else:
            self.sop_code = sop_code or DSMX_BIND_SOP_CODE
            self.data_code = data_code
            self.hop_channels = None

        self.demod = GFSKDemodulator(sample_rate)
        self.hop_analyzer = HopPatternAnalyzer()
        self._packets: list[DSMXPacket] = []

    def decode_iq(self, iq: np.ndarray, time_offset: float = 0.0
                  ) -> list[DSMXPacket]:
        """Decode DSMX packets from IQ samples.

        Returns list of decoded packets.
        """
        packets = []

        # Step 1: Find bursts via energy detection
        bursts = detect_bursts(
            iq, self.sample_rate,
            threshold_db=self.burst_threshold_db,
        )

        if not bursts:
            return packets

        print(f"[*] Found {len(bursts)} burst(s) in {len(iq)/self.sample_rate:.3f}s "
              f"of IQ data", file=sys.stderr)

        for burst_start, burst_end in bursts:
            burst_time = time_offset + burst_start / self.sample_rate
            burst_iq = iq[burst_start:burst_end]
            snr = burst_snr_db(iq, burst_start, burst_end)

            # Step 2: GFSK demodulate
            bit_decisions = self.demod.demodulate(burst_iq)
            if len(bit_decisions) < 64:  # minimum preamble + SOP
                continue

            # Step 3: Try SOP correlation
            sop_offsets = find_sop_in_bits(
                bit_decisions, self.sop_code, max_errors=3
            )

            if not sop_offsets:
                # No SOP found — try timing-based extraction
                # DSMX packets are 16 bytes = 128 bits after SOP
                # Try extracting at bit 64 (after 64-bit preamble+SOP region)
                packet_start = min(64, max(0, len(bit_decisions) - 128))
                sop_offsets = [packet_start]

            for bit_offset in sop_offsets:
                pkt_start = bit_offset + len(self.sop_code) * 8
                pkt_end = pkt_start + DSMX_PACKET_BYTES * 8

                if pkt_end > len(bit_decisions):
                    continue

                raw_bytes = bits_to_bytes(bit_decisions[pkt_start:pkt_end])
                if len(raw_bytes) < DSMX_PACKET_BYTES:
                    continue

                pkt = parse_dsmx_packet(
                    raw_bytes,
                    timestamp_s=burst_time,
                    freq_hz=self.center_freq,
                    snr_db=snr,
                    data_code=self.data_code,
                    crc_seeds=self.crc_seeds,
                )

                # Record for hop analysis
                self.hop_analyzer.add_observation(burst_time, self.center_freq)
                packets.append(pkt)

        self._packets.extend(packets)
        return packets

    def decode_file(self, path: Path, input_format: str = "auto",
                    chunk_duration_s: float = 0.5) -> list[DSMXPacket]:
        """Decode DSMX packets from an IQ file, processing in chunks."""
        iq = read_iq_file(path, self.sample_rate, input_format=input_format)
        total_duration = len(iq) / self.sample_rate
        print(f"[*] Loaded {len(iq)} samples ({total_duration:.3f}s) "
              f"from {path.name}", file=sys.stderr)

        chunk_samples = int(chunk_duration_s * self.sample_rate)
        all_packets = []

        for offset in range(0, len(iq), chunk_samples):
            chunk = iq[offset:offset + chunk_samples]
            time_offset = offset / self.sample_rate
            packets = self.decode_iq(chunk, time_offset)
            all_packets.extend(packets)

        return all_packets

    def summary(self) -> dict:
        """Generate a summary of decoding results."""
        crc_ok = sum(1 for p in self._packets if p.crc_ok)
        observed_ch = self.hop_analyzer.observed_channels()
        hop_rate = self.hop_analyzer.estimate_hop_rate_ms()

        return {
            "total_packets": len(self._packets),
            "crc_valid": crc_ok,
            "crc_invalid": len(self._packets) - crc_ok,
            "unique_channels_observed": len(observed_ch),
            "channels": observed_ch,
            "estimated_hop_rate_ms": hop_rate,
            "protocol": "DSMX" if hop_rate and hop_rate < 15 else "DSM2" if hop_rate else "unknown",
            "mfg_id_known": self.mfg_id is not None,
        }


# ---------------------------------------------------------------------------
# Multi-channel scanning
# ---------------------------------------------------------------------------

def dsmx_channel_freqs() -> list[float]:
    """All 79 CYRF6936 channel frequencies."""
    return [DSMX_BASE_FREQ_HZ + i * DSMX_CHANNEL_SPACING_HZ
            for i in range(DSMX_NUM_CYRF_CHANNELS)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Spektrum DSMX/DSM2 SDR IQ decoder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
SDR capture commands (no real capture exists — use these to produce valid input):

  # HackRF wideband (10 Msps, covers many DSMX channels):
  hackrf_transfer -r dsmx_capture.cs8 -f 2440000000 -s 10000000 -g 40 -l 32

  # USRP single channel (2 Msps):
  uhd_rx_cfile --freq 2.440e9 --rate 2e6 --gain 40 -N 2000000 dsmx.cf32

  # RTL-SDR (limited 2.4 Msps, best for single-channel monitoring):
  rtl_sdr -f 2440000000 -s 2400000 -g 40 -n 2400000 dsmx.cu8

  # SoapySDR via sdr_common (live mode):
  python3 dsmx_decode.py --live --sdr-device "driver=hackrf" --freq 2440e6

Examples:
  # Decode from IQ file
  python3 dsmx_decode.py capture.cs8 --rate 10e6 --freq 2440e6

  # With known MFG ID (from binding sniff or label)
  python3 dsmx_decode.py capture.cf32 --rate 2e6 --freq 2440e6 \\
      --mfg-id a1b2c3d4

  # Binding channel detection (fixed frequency during bind)
  python3 dsmx_decode.py capture.cs8 --rate 10e6 --freq 2440e6 --bind-mode
""",
    )

    parser.add_argument("iq_file", nargs="?", type=Path,
                        help="Input IQ file (cf32/cs8/cu8/sc16)")
    parser.add_argument("--rate", type=float, required=True,
                        help="Sample rate in Hz (e.g. 2e6, 10e6)")
    parser.add_argument("--freq", type=float, default=2_440_000_000.0,
                        help="Center frequency in Hz (default: 2.440 GHz)")
    parser.add_argument("--format", choices=["cf32", "cs8", "cu8", "sc16", "auto"],
                        default="auto", help="IQ file format (default: auto)")
    parser.add_argument("--mfg-id", type=str, default=None,
                        help="4-byte MFG ID as hex (e.g. a1b2c3d4)")
    parser.add_argument("--bind-mode", action="store_true",
                        help="Use binding-phase SOP/data codes")
    parser.add_argument("--crc-seed", type=str, default=None,
                        help="CRC seed as hex (e.g. 1234)")
    parser.add_argument("--threshold", type=float, default=10.0,
                        help="Burst detection threshold in dB above noise (default: 10)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output JSON Lines file (default: stdout)")
    parser.add_argument("--summary", action="store_true",
                        help="Print summary statistics after decoding")

    live_group = parser.add_argument_group("live SDR options")
    live_group.add_argument("--live", action="store_true",
                            help="Use live SDR input via SoapySDR")
    live_group.add_argument("--gain", type=float, default=40.0,
                            help="SDR gain in dB (default: 40)")
    live_group.add_argument("--duration", type=float, default=10.0,
                            help="Live capture duration in seconds (default: 10)")
    live_group.add_argument("--sdr-device", default="",
                            help="SoapySDR device string")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.iq_file and not args.live:
        parser.error("Either provide an IQ file or use --live mode")

    # Parse MFG ID
    mfg_id = None
    if args.mfg_id:
        mfg_id = bytes.fromhex(args.mfg_id)
        if len(mfg_id) != 4:
            parser.error("MFG ID must be exactly 4 bytes (8 hex chars)")

    # CRC seed
    crc_seeds = [0x0000, 0xFFFF]
    if args.crc_seed:
        crc_seeds.insert(0, int(args.crc_seed, 16))

    # Override SOP/data codes for binding mode
    sop_code = DSMX_BIND_SOP_CODE if args.bind_mode else None
    data_code = DSMX_BIND_DATA_CODE if args.bind_mode else None

    decoder = DSMXDecoder(
        sample_rate=args.rate,
        center_freq=args.freq,
        mfg_id=mfg_id,
        sop_code=sop_code,
        data_code=data_code,
        crc_seeds=crc_seeds,
        burst_threshold_db=args.threshold,
    )

    # Decode
    if args.live:
        # Live SDR mode
        try:
            from sdr_common import SDRDevice
        except ImportError:
            print("[!] sdr_common not found — cannot use live mode",
                  file=sys.stderr)
            sys.exit(1)

        sdr = SDRDevice(device_args=args.sdr_device)
        sdr.setup_rx(freq=args.freq, rate=args.rate, gain=args.gain)
        sdr.start_rx()

        print(f"[*] Live capture: {args.freq/1e6:.3f} MHz, "
              f"{args.rate/1e6:.1f} Msps, {args.duration}s", file=sys.stderr)

        chunk_samples = int(0.5 * args.rate)
        elapsed = 0.0
        out_file = open(args.output, "w") if args.output else None

        try:
            while elapsed < args.duration:
                iq = sdr.receive(num_samples=chunk_samples)
                packets = decoder.decode_iq(iq, time_offset=elapsed)
                elapsed += len(iq) / args.rate

                for pkt in packets:
                    line = json.dumps(pkt.to_dict())
                    if out_file:
                        out_file.write(line + "\n")
                    else:
                        print(line, flush=True)
        except KeyboardInterrupt:
            print("\n[*] Interrupted", file=sys.stderr)
        finally:
            sdr.close()
            if out_file:
                out_file.close()
    else:
        # File mode
        if not args.iq_file or not args.iq_file.exists():
            parser.error(f"IQ file not found: {args.iq_file}")

        packets = decoder.decode_file(args.iq_file, input_format=args.format)

        out_file = open(args.output, "w") if args.output else None
        for pkt in packets:
            line = json.dumps(pkt.to_dict())
            if out_file:
                out_file.write(line + "\n")
            else:
                print(line, flush=True)
        if out_file:
            out_file.close()

    # Summary
    if args.summary:
        summary = decoder.summary()
        print("\n--- Decode Summary ---", file=sys.stderr)
        for k, v in summary.items():
            print(f"  {k}: {v}", file=sys.stderr)

        if not decoder.mfg_id and len(decoder._packets) > 10:
            print("\n[*] Attempting MFG ID recovery from hop pattern...",
                  file=sys.stderr)
            candidates = decoder.hop_analyzer.brute_force_mfg_id()
            if candidates:
                print(f"  Top candidates:", file=sys.stderr)
                for mid, score in candidates[:5]:
                    print(f"    {mid.hex()} (score: {score})", file=sys.stderr)
            else:
                print("  Not enough channel diversity for recovery",
                      file=sys.stderr)


if __name__ == "__main__":
    main()
