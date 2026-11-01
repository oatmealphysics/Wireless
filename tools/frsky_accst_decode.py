#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
FrSky ACCST/ACCESS SDR IQ Decoder

Decodes FrSky D8 and D16 (ACCST v1) RC control packets from raw SDR IQ
captures.  The decoder implements the full 2-GFSK demodulation chain used
by the CC2500 transceiver: FM discriminator, clock recovery, bit slicing,
preamble/sync detection, CRC-16 verification, and channel-data extraction.

Protocol summary (from DeviationTX, OpenTX, multi-module reverse engineering):

  Transceiver:  Texas Instruments CC2500, 2.4 GHz ISM band
  Modulation:   2-GFSK, 250 kbps data rate, ≈540 kHz occupied bandwidth
  FHSS:         47 hop channels across 2.404 – 2.478 GHz (~1.6 MHz spacing)
  Hop rate:     ~9 ms per hop (D16), ~18 ms (D8)
  Preamble:     4 bytes 0xAA (alternating bits)
  Sync word:    0x2D, 0xD4 (CC2500 default)
  CRC:          CRC-16/CCITT (CC2500 hardware, poly 0x8005 bit-reversed)

  D8 packet (after sync):
    [len=0x11] [status] [txid_hi] [txid_lo] [counter] [ch0_lo] [ch0_hi]
    ... 8 channels, 11-bit each, packed ... [flags] [crc_hi] [crc_lo]

  D16 packet (after sync):
    [len=0x1D] [bind|0x00] [txid_hi] [txid_lo] [counter] [ch_data × 24]
    [flags] [crc_hi] [crc_lo]
    Channel data: 16 channels × 11-bit, packed little-endian into 22 bytes.

  ACCESS (v2) packets are encrypted after the header; this decoder marks
  them but cannot extract channel data.

Hop sequence (D16):  The 47-channel hop table is derived deterministically
from the TX ID.  This decoder reconstructs the full hop table once a TX ID
is observed, enabling prediction of the next hop frequency.

SDR capture commands (no real FrSky IQ capture validated yet — GAP):

  # HackRF — wideband capture covering the full hop range
  hackrf_transfer -r frsky_2440_20M.iq -f 2440000000 -s 20000000 \\
      -a 1 -l 32 -g 30 -n 400000000

  # USRP B210 — same band
  uhd_rx_cfile --freq 2.44e9 --rate 20e6 --gain 40 \\
      --type float32 frsky_2440_20M.iq

  # RTL-SDR (narrow — single channel only, 2.4 Msps max)
  rtl_sdr -f 2440000000 -s 2400000 -g 40 frsky_ch25_2.4M.iq

Supported IQ formats: complex64 (numpy cf32), complex int16 (sc16),
unsigned byte IQ (rtl_sdr cu8).

Requires: numpy, scipy.  Optional: sdr_common (for live SDR input).
Python 3.10+.  For authorized CTF research only.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.signal import firwin, lfilter, resample_poly


# ---------------------------------------------------------------------------
# CC2500 / FrSky protocol constants
# ---------------------------------------------------------------------------

CC2500_PREAMBLE = bytes([0xAA] * 4)
CC2500_SYNC_WORD = bytes([0x2D, 0xD4])
SYNC_BITS = []
for _b in CC2500_SYNC_WORD:
    for _i in range(8):
        SYNC_BITS.append((_b >> (7 - _i)) & 1)

FRSKY_BASE_FREQ_HZ = 2.404e9
FRSKY_CHANNEL_SPACING_HZ = 1.6e6
FRSKY_NUM_CHANNELS = 47
FRSKY_DATA_RATE = 250_000
FRSKY_D16_HOP_INTERVAL_S = 0.009
FRSKY_D8_HOP_INTERVAL_S = 0.018
FRSKY_D16_PKT_LEN = 0x1D
FRSKY_D8_PKT_LEN = 0x11

# CRC-16 table (CC2500 hardware CRC, polynomial 0x8005 bit-reversed = 0xA001)
_CRC16_TABLE = []
for _n in range(256):
    _crc = _n
    for _ in range(8):
        if _crc & 1:
            _crc = (_crc >> 1) ^ 0xA001
        else:
            _crc >>= 1
    _CRC16_TABLE.append(_crc)


def crc16_cc2500(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc = (_CRC16_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)) & 0xFFFF
    return crc


def frsky_channel_freq(channel_index: int) -> float:
    return FRSKY_BASE_FREQ_HZ + channel_index * FRSKY_CHANNEL_SPACING_HZ


# ---------------------------------------------------------------------------
# FHSS hop-sequence reconstruction
# ---------------------------------------------------------------------------

def compute_d16_hop_table(tx_id: int) -> list[int]:
    """Derive the 47-channel D16 hop table from a 16-bit TX ID.

    The algorithm is reconstructed from DeviationTX / OpenTX sources:
    the TX ID seeds a deterministic shuffle of 47 channels, ensuring
    no repeats and roughly uniform spacing.
    """
    id_hi = (tx_id >> 8) & 0xFF
    id_lo = tx_id & 0xFF

    table = []
    val = id_lo
    for i in range(FRSKY_NUM_CHANNELS):
        val = (val * 0xAA + id_hi) & 0xFF
        ch = val % FRSKY_NUM_CHANNELS
        while ch in table:
            ch = (ch + 1) % FRSKY_NUM_CHANNELS
        table.append(ch)
    return table


def compute_d8_hop_table(tx_id: int) -> list[int]:
    """Derive the D8 hop table (same 47 channels, different seed mixing)."""
    id_hi = (tx_id >> 8) & 0xFF
    id_lo = tx_id & 0xFF

    table = []
    val = id_hi
    for i in range(FRSKY_NUM_CHANNELS):
        val = (val * 0x97 + id_lo) & 0xFF
        ch = val % FRSKY_NUM_CHANNELS
        while ch in table:
            ch = (ch + 1) % FRSKY_NUM_CHANNELS
        table.append(ch)
    return table


# ---------------------------------------------------------------------------
# IQ file reading
# ---------------------------------------------------------------------------

def load_iq_file(path: str, fmt: str = "cf32",
                 offset_samples: int = 0,
                 max_samples: int = 0) -> np.ndarray:
    """Load an IQ file as np.complex64.

    Formats:
        cf32  — interleaved float32 I/Q (numpy complex64)
        sc16  — interleaved int16 I/Q (USRP / uhd_rx_cfile --type short)
        cu8   — interleaved uint8 I/Q (rtl_sdr raw output)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"IQ file not found: {path}")

    file_size = p.stat().st_size

    if fmt == "cf32":
        bytes_per_sample = 8
    elif fmt == "sc16":
        bytes_per_sample = 4
    elif fmt == "cu8":
        bytes_per_sample = 2
    else:
        raise ValueError(f"Unknown IQ format: {fmt}")

    total_samples = file_size // bytes_per_sample
    if offset_samples >= total_samples:
        return np.array([], dtype=np.complex64)

    read_count = total_samples - offset_samples
    if max_samples > 0:
        read_count = min(read_count, max_samples)

    if fmt == "cf32":
        raw = np.fromfile(str(p), dtype=np.complex64,
                          count=read_count, offset=offset_samples * bytes_per_sample)
        return raw
    elif fmt == "sc16":
        raw = np.fromfile(str(p), dtype=np.int16,
                          count=read_count * 2,
                          offset=offset_samples * bytes_per_sample)
        iq = raw.astype(np.float32).reshape(-1, 2)
        return (iq[:, 0] + 1j * iq[:, 1]).astype(np.complex64) / 32768.0
    elif fmt == "cu8":
        raw = np.fromfile(str(p), dtype=np.uint8,
                          count=read_count * 2,
                          offset=offset_samples * bytes_per_sample)
        iq = raw.astype(np.float32).reshape(-1, 2)
        return ((iq[:, 0] - 127.5) + 1j * (iq[:, 1] - 127.5)).astype(np.complex64) / 127.5


# ---------------------------------------------------------------------------
# 2-GFSK demodulation chain
# ---------------------------------------------------------------------------

class GFSKDemodulator:
    """Complete 2-GFSK demodulation: channelize → FM discriminator →
    clock recovery → bit decisions."""

    def __init__(self, sample_rate: float, data_rate: float = FRSKY_DATA_RATE,
                 channel_bw: float = 540e3):
        self.sample_rate = sample_rate
        self.data_rate = data_rate
        self.channel_bw = channel_bw
        self.samples_per_bit = sample_rate / data_rate

        # Design a low-pass filter for channel isolation
        cutoff = channel_bw / 2.0
        nyq = sample_rate / 2.0
        num_taps = max(int(sample_rate / channel_bw * 4) | 1, 31)
        if num_taps % 2 == 0:
            num_taps += 1
        if cutoff >= nyq:
            self._channel_filter = None
        else:
            self._channel_filter = firwin(num_taps, cutoff / nyq)

    def channelize(self, iq: np.ndarray, center_freq: float,
                   channel_freq: float) -> np.ndarray:
        """Shift a specific channel to baseband and filter."""
        offset = channel_freq - center_freq
        n = np.arange(len(iq))
        shifted = iq * np.exp(-2j * np.pi * offset / self.sample_rate * n)
        if self._channel_filter is not None:
            shifted = lfilter(self._channel_filter, 1.0, shifted)
        return shifted

    def fm_discriminator(self, iq: np.ndarray) -> np.ndarray:
        """Compute instantaneous frequency via arg(x[n] * conj(x[n-1]))."""
        product = iq[1:] * np.conj(iq[:-1])
        return np.angle(product)

    def clock_recover_mm(self, soft: np.ndarray) -> np.ndarray:
        """Mueller-Muller clock recovery for bit sampling.

        Returns one soft sample per bit period at the recovered sampling
        instants.  This is a standard digital-comm clock recovery loop
        adapted for the CC2500's 250 kbps GFSK.
        """
        spb = self.samples_per_bit
        mu = 0.0
        gain_mu = 0.05
        omega = spb
        gain_omega = 0.001 * gain_mu * gain_mu
        omega_limit = 0.005 * omega

        bits_out = []
        idx = int(spb)
        prev_sample = 0.0
        prev_decision = 0.0

        while idx < len(soft) - 1:
            # Interpolate at fractional position
            int_idx = int(idx)
            frac = idx - int_idx
            if int_idx + 1 < len(soft):
                sample = soft[int_idx] * (1 - frac) + soft[int_idx + 1] * frac
            else:
                sample = soft[int_idx]

            decision = 1.0 if sample > 0 else -1.0

            # Mueller-Muller timing error
            err = (prev_decision * sample - decision * prev_sample)

            prev_sample = sample
            prev_decision = decision

            # Update timing
            omega = omega + gain_omega * err
            omega = np.clip(omega, spb - omega_limit * spb,
                            spb + omega_limit * spb)
            mu = mu + omega + gain_mu * err
            idx += int(mu)
            mu -= int(mu)

            bits_out.append(sample)

        return np.array(bits_out)

    def demodulate(self, iq: np.ndarray, center_freq: float,
                   channel_freq: float) -> np.ndarray:
        """Full demodulation chain for one channel: channelize → FM disc →
        clock recovery.  Returns soft bit values (positive = 1)."""
        baseband = self.channelize(iq, center_freq, channel_freq)
        freq = self.fm_discriminator(baseband)
        bits = self.clock_recover_mm(freq)
        return bits


# ---------------------------------------------------------------------------
# Bit-level packet extraction
# ---------------------------------------------------------------------------

def soft_to_hard(soft_bits: np.ndarray) -> list[int]:
    """Convert soft bit values to hard 0/1 decisions."""
    return [1 if s > 0 else 0 for s in soft_bits]


def find_sync_word(bits: list[int], sync: list[int] = SYNC_BITS,
                   max_errors: int = 2) -> list[int]:
    """Find all positions where the sync word appears (with up to
    max_errors bit errors for robustness)."""
    positions = []
    sync_len = len(sync)
    for i in range(len(bits) - sync_len - 8):
        errors = sum(1 for j in range(sync_len) if bits[i + j] != sync[j])
        if errors <= max_errors:
            positions.append(i + sync_len)
    return positions


def bits_to_bytes(bits: list[int], start: int, num_bytes: int) -> bytes:
    """Extract bytes from a bit array (MSB first, CC2500 convention)."""
    result = bytearray()
    for b in range(num_bytes):
        byte_val = 0
        for bit in range(8):
            idx = start + b * 8 + bit
            if idx < len(bits):
                byte_val = (byte_val << 1) | bits[idx]
            else:
                byte_val <<= 1
        result.append(byte_val)
    return bytes(result)


# ---------------------------------------------------------------------------
# Packet parsing
# ---------------------------------------------------------------------------

@dataclass
class FrSkyPacket:
    """Decoded FrSky ACCST packet."""
    timestamp_s: float = 0.0
    channel_freq_hz: float = 0.0
    channel_index: int = -1
    mode: str = "unknown"   # "D8", "D16", "ACCESS", "bind"
    tx_id: int = 0
    frame_counter: int = 0
    channels: list[int] = field(default_factory=list)
    channels_us: list[float] = field(default_factory=list)
    flags: int = 0
    crc_ok: bool = False
    crc_received: int = 0
    crc_computed: int = 0
    raw_hex: str = ""
    hop_table: Optional[list[int]] = None
    next_hop_freq_hz: Optional[float] = None

    def to_dict(self) -> dict:
        d = {
            "timestamp_s": round(self.timestamp_s, 6),
            "channel_freq_hz": self.channel_freq_hz,
            "channel_index": self.channel_index,
            "mode": self.mode,
            "tx_id": f"0x{self.tx_id:04X}",
            "frame_counter": self.frame_counter,
            "channels": self.channels,
            "channels_us": [round(v, 1) for v in self.channels_us],
            "flags": f"0x{self.flags:02X}",
            "crc_ok": self.crc_ok,
            "raw_hex": self.raw_hex,
        }
        if self.next_hop_freq_hz is not None:
            d["next_hop_freq_hz"] = self.next_hop_freq_hz
        return d


def channel_raw_to_us(raw: int) -> float:
    """Convert 11-bit raw channel value to microseconds (988–2012 µs)."""
    return 988.0 + (raw / 2047.0) * 1024.0


def parse_d8_packet(raw: bytes) -> Optional[FrSkyPacket]:
    """Parse a D8-mode packet (length byte = 0x11)."""
    if len(raw) < FRSKY_D8_PKT_LEN + 1:
        return None

    pkt_len = raw[0]
    if pkt_len != FRSKY_D8_PKT_LEN:
        return None

    payload = raw[1:pkt_len - 1]
    crc_received = (raw[pkt_len - 1] << 8) | raw[pkt_len]
    crc_computed = crc16_cc2500(raw[:pkt_len - 1])

    status = payload[0]
    tx_id = (payload[1] << 8) | payload[2]
    counter = payload[3]

    # 8 channels × 11-bit, packed into bytes starting at payload[4]
    channels = []
    ch_data = payload[4:]
    bit_pos = 0
    for ch in range(8):
        byte_idx = bit_pos // 8
        bit_offset = bit_pos % 8
        if byte_idx + 1 < len(ch_data):
            val = (ch_data[byte_idx] | (ch_data[byte_idx + 1] << 8))
            val = (val >> bit_offset) & 0x7FF
        elif byte_idx < len(ch_data):
            val = ch_data[byte_idx] >> bit_offset
            val &= 0x7FF
        else:
            val = 0
        channels.append(val)
        bit_pos += 11

    pkt = FrSkyPacket(
        mode="D8",
        tx_id=tx_id,
        frame_counter=counter,
        channels=channels,
        channels_us=[channel_raw_to_us(c) for c in channels],
        flags=status,
        crc_ok=(crc_received == crc_computed),
        crc_received=crc_received,
        crc_computed=crc_computed,
        raw_hex=raw[:pkt_len + 1].hex(),
    )
    return pkt


def parse_d16_packet(raw: bytes) -> Optional[FrSkyPacket]:
    """Parse a D16-mode packet (length byte = 0x1D)."""
    if len(raw) < FRSKY_D16_PKT_LEN + 1:
        return None

    pkt_len = raw[0]
    if pkt_len != FRSKY_D16_PKT_LEN:
        return None

    payload = raw[1:pkt_len - 1]
    crc_received = (raw[pkt_len - 1] << 8) | raw[pkt_len]
    crc_computed = crc16_cc2500(raw[:pkt_len - 1])

    bind_status = payload[0]
    tx_id = (payload[1] << 8) | payload[2]
    counter = payload[3]

    is_bind = (bind_status & 0x80) != 0
    is_access = (bind_status & 0x40) != 0

    if is_access:
        pkt = FrSkyPacket(
            mode="ACCESS",
            tx_id=tx_id,
            frame_counter=counter,
            flags=bind_status,
            crc_ok=(crc_received == crc_computed),
            crc_received=crc_received,
            crc_computed=crc_computed,
            raw_hex=raw[:pkt_len + 1].hex(),
        )
        return pkt

    if is_bind:
        mode = "bind"
    else:
        mode = "D16"

    # 16 channels × 11-bit packed little-endian in 22 bytes at payload[4:26]
    channels = []
    ch_data = payload[4:26]
    bit_pos = 0
    for ch in range(16):
        byte_idx = bit_pos // 8
        bit_offset = bit_pos % 8
        if byte_idx + 1 < len(ch_data):
            val = (ch_data[byte_idx] | (ch_data[byte_idx + 1] << 8))
            val = (val >> bit_offset) & 0x7FF
        elif byte_idx < len(ch_data):
            val = ch_data[byte_idx] >> bit_offset
            val &= 0x7FF
        else:
            val = 0
        channels.append(val)
        bit_pos += 11

    flags = payload[26] if len(payload) > 26 else 0

    pkt = FrSkyPacket(
        mode=mode,
        tx_id=tx_id,
        frame_counter=counter,
        channels=channels,
        channels_us=[channel_raw_to_us(c) for c in channels],
        flags=flags,
        crc_ok=(crc_received == crc_computed),
        crc_received=crc_received,
        crc_computed=crc_computed,
        raw_hex=raw[:pkt_len + 1].hex(),
    )
    return pkt


def parse_frsky_packet(raw: bytes) -> Optional[FrSkyPacket]:
    """Auto-detect D8 vs D16 by length byte and parse accordingly."""
    if not raw:
        return None
    pkt_len = raw[0]
    if pkt_len == FRSKY_D16_PKT_LEN:
        return parse_d16_packet(raw)
    elif pkt_len == FRSKY_D8_PKT_LEN:
        return parse_d8_packet(raw)
    return None


# ---------------------------------------------------------------------------
# Multi-channel wideband scanning
# ---------------------------------------------------------------------------

def scan_all_channels(iq: np.ndarray, sample_rate: float,
                      center_freq: float, demod: GFSKDemodulator,
                      timestamp_offset: float = 0.0
                      ) -> list[FrSkyPacket]:
    """Scan all 47 FrSky channels within a wideband IQ capture and
    extract packets from each."""
    packets = []
    half_bw = sample_rate / 2.0

    for ch_idx in range(FRSKY_NUM_CHANNELS):
        ch_freq = frsky_channel_freq(ch_idx)
        offset = ch_freq - center_freq
        if abs(offset) > half_bw - 300e3:
            continue

        soft = demod.demodulate(iq, center_freq, ch_freq)
        hard = soft_to_hard(soft)

        sync_positions = find_sync_word(hard)
        for pos in sync_positions:
            # Maximum packet: 0x1D + 1 = 30 bytes = 240 bits
            max_bits = 240
            if pos + max_bits > len(hard):
                continue

            raw = bits_to_bytes(hard, pos, 30)
            pkt = parse_frsky_packet(raw)
            if pkt is None:
                continue

            pkt.channel_freq_hz = ch_freq
            pkt.channel_index = ch_idx
            pkt.timestamp_s = timestamp_offset + pos * (1.0 / FRSKY_DATA_RATE)

            # Compute hop table and next frequency
            if pkt.tx_id != 0:
                if pkt.mode == "D8":
                    hop_table = compute_d8_hop_table(pkt.tx_id)
                else:
                    hop_table = compute_d16_hop_table(pkt.tx_id)
                pkt.hop_table = hop_table
                hop_idx = hop_table.index(ch_idx) if ch_idx in hop_table else -1
                if hop_idx >= 0:
                    next_ch = hop_table[(hop_idx + 1) % len(hop_table)]
                    pkt.next_hop_freq_hz = frsky_channel_freq(next_ch)

            packets.append(pkt)

    # De-duplicate packets at the same timestamp/counter
    seen = set()
    unique = []
    for pkt in packets:
        key = (pkt.tx_id, pkt.frame_counter, pkt.channel_index)
        if key not in seen:
            seen.add(key)
            unique.append(pkt)

    return sorted(unique, key=lambda p: p.timestamp_s)


# ---------------------------------------------------------------------------
# Streaming / block-based processing
# ---------------------------------------------------------------------------

def process_iq_file(path: str, sample_rate: float, center_freq: float,
                    iq_format: str = "cf32",
                    block_duration_s: float = 0.05,
                    output_file=None) -> list[FrSkyPacket]:
    """Process an IQ file in blocks, extracting FrSky packets."""
    block_samples = int(sample_rate * block_duration_s)
    demod = GFSKDemodulator(sample_rate)

    all_packets = []
    offset = 0
    block_num = 0

    while True:
        iq = load_iq_file(path, fmt=iq_format,
                          offset_samples=offset,
                          max_samples=block_samples)
        if len(iq) == 0:
            break

        timestamp = offset / sample_rate
        packets = scan_all_channels(iq, sample_rate, center_freq,
                                    demod, timestamp)

        for pkt in packets:
            line = json.dumps(pkt.to_dict())
            if output_file:
                output_file.write(line + "\n")
                output_file.flush()
            else:
                print(line, flush=True)
            all_packets.append(pkt)

        offset += block_samples
        block_num += 1

        if block_num % 100 == 0:
            print(f"[*] Processed {block_num} blocks, "
                  f"{offset / sample_rate:.3f}s, "
                  f"{len(all_packets)} packets found",
                  file=sys.stderr)

    return all_packets


# ---------------------------------------------------------------------------
# Live SDR capture mode
# ---------------------------------------------------------------------------

def live_capture(center_freq: float, sample_rate: float, gain: float,
                 duration_s: float, sdr_args: str = "",
                 output_file=None) -> list[FrSkyPacket]:
    """Capture and decode FrSky packets from a live SDR."""
    try:
        from sdr_common import SDRDevice
    except ImportError:
        print("[!] sdr_common not found. Install SoapySDR or use --iq-file.",
              file=sys.stderr)
        sys.exit(1)

    sdr = SDRDevice(sdr_args)
    sdr.setup_rx(freq=center_freq, rate=sample_rate, gain=gain)

    demod = GFSKDemodulator(sample_rate)
    all_packets = []
    block_samples = int(sample_rate * 0.05)
    elapsed = 0.0

    print(f"[*] Live capture: center={center_freq / 1e6:.1f} MHz, "
          f"rate={sample_rate / 1e6:.1f} Msps, gain={gain:.0f} dB",
          file=sys.stderr)

    try:
        while elapsed < duration_s:
            iq = sdr.receive(num_samples=block_samples)
            if len(iq) == 0:
                continue

            packets = scan_all_channels(iq, sample_rate, center_freq,
                                        demod, elapsed)

            for pkt in packets:
                line = json.dumps(pkt.to_dict())
                if output_file:
                    output_file.write(line + "\n")
                    output_file.flush()
                else:
                    print(line, flush=True)
                all_packets.append(pkt)

            elapsed += len(iq) / sample_rate
    except KeyboardInterrupt:
        print("\n[*] Capture stopped by user.", file=sys.stderr)
    finally:
        sdr.close()

    return all_packets


# ---------------------------------------------------------------------------
# Summary / analysis output
# ---------------------------------------------------------------------------

def print_summary(packets: list[FrSkyPacket]):
    """Print a summary of decoded traffic to stderr."""
    if not packets:
        print("[*] No FrSky packets decoded.", file=sys.stderr)
        return

    tx_ids = set()
    modes = set()
    crc_ok = 0
    crc_fail = 0
    channels_seen = set()

    for pkt in packets:
        tx_ids.add(pkt.tx_id)
        modes.add(pkt.mode)
        if pkt.crc_ok:
            crc_ok += 1
        else:
            crc_fail += 1
        if pkt.channel_index >= 0:
            channels_seen.add(pkt.channel_index)

    duration = packets[-1].timestamp_s - packets[0].timestamp_s
    print(f"\n[*] === FrSky ACCST Decode Summary ===", file=sys.stderr)
    print(f"    Packets decoded:  {len(packets)} "
          f"({crc_ok} CRC OK, {crc_fail} CRC fail)", file=sys.stderr)
    print(f"    Duration:         {duration:.3f} s", file=sys.stderr)
    print(f"    TX IDs:           {', '.join(f'0x{t:04X}' for t in sorted(tx_ids))}",
          file=sys.stderr)
    print(f"    Modes:            {', '.join(sorted(modes))}", file=sys.stderr)
    print(f"    Hop channels:     {len(channels_seen)}/47 observed",
          file=sys.stderr)

    # Print hop table for each TX
    for tx_id in sorted(tx_ids):
        tx_packets = [p for p in packets if p.tx_id == tx_id and p.hop_table]
        if tx_packets:
            ht = tx_packets[0].hop_table
            print(f"    Hop table (0x{tx_id:04X}): "
                  f"{' '.join(str(c) for c in ht[:10])}...", file=sys.stderr)

    # Channel statistics for CRC-OK packets
    ok_packets = [p for p in packets if p.crc_ok and p.channels_us]
    if ok_packets:
        num_ch = len(ok_packets[0].channels_us)
        print(f"    Channel ranges (µs):", file=sys.stderr)
        for ch in range(min(num_ch, 8)):
            vals = [p.channels_us[ch] for p in ok_packets
                    if ch < len(p.channels_us)]
            if vals:
                print(f"      CH{ch}: {min(vals):.0f} – {max(vals):.0f} "
                      f"(avg {sum(vals)/len(vals):.0f})", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FrSky ACCST/ACCESS SDR IQ decoder. Extracts RC "
                    "control packets from raw IQ captures or live SDR.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Decode from a wideband IQ capture
  %(prog)s --iq-file frsky_2440_20M.iq --sample-rate 20e6 \\
      --center-freq 2.44e9 --format cf32

  # Live capture with HackRF
  %(prog)s --live --center-freq 2.44e9 --sample-rate 20e6 \\
      --gain 40 --duration 30

  # Narrowband single-channel capture (RTL-SDR)
  %(prog)s --iq-file frsky_ch25.iq --sample-rate 2.4e6 \\
      --center-freq 2.44e9 --format cu8

NOTE: No real FrSky IQ capture has been validated against this decoder
yet. The demodulation chain and packet parsing are built from public
protocol documentation (DeviationTX, OpenTX, CC2500 datasheet). Run
the capture commands in the module docstring to produce test data.
""",
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--iq-file", metavar="FILE",
        help="Path to raw IQ file for offline decoding",
    )
    input_group.add_argument(
        "--live", action="store_true",
        help="Live capture from SDR hardware",
    )

    parser.add_argument(
        "--sample-rate", type=float, required=True,
        help="Sample rate in Hz (e.g. 20e6)",
    )
    parser.add_argument(
        "--center-freq", type=float, required=True,
        help="Center frequency in Hz (e.g. 2.44e9)",
    )
    parser.add_argument(
        "--format", default="cf32", choices=["cf32", "sc16", "cu8"],
        help="IQ file format (default: cf32)",
    )
    parser.add_argument(
        "--gain", type=float, default=40.0,
        help="SDR gain in dB for live capture (default: 40)",
    )
    parser.add_argument(
        "--duration", type=float, default=60.0,
        help="Live capture duration in seconds (default: 60)",
    )
    parser.add_argument(
        "--sdr-device", default="",
        help="SoapySDR device string for live capture",
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Write JSON lines output to file",
    )
    parser.add_argument(
        "--summary", action="store_true", default=True,
        help="Print summary after decoding (default: on)",
    )
    parser.add_argument(
        "--no-summary", action="store_false", dest="summary",
        help="Suppress summary output",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    output_file = None
    if args.output:
        output_file = open(args.output, "w")

    try:
        if args.iq_file:
            print(f"[*] Decoding FrSky ACCST from: {args.iq_file}",
                  file=sys.stderr)
            print(f"    Sample rate: {args.sample_rate / 1e6:.1f} Msps, "
                  f"Center: {args.center_freq / 1e6:.1f} MHz, "
                  f"Format: {args.format}", file=sys.stderr)

            packets = process_iq_file(
                args.iq_file,
                sample_rate=args.sample_rate,
                center_freq=args.center_freq,
                iq_format=args.format,
                output_file=output_file,
            )
        else:
            packets = live_capture(
                center_freq=args.center_freq,
                sample_rate=args.sample_rate,
                gain=args.gain,
                duration_s=args.duration,
                sdr_args=args.sdr_device,
                output_file=output_file,
            )

        if args.summary:
            print_summary(packets)

    finally:
        if output_file:
            output_file.close()


if __name__ == "__main__":
    main()
