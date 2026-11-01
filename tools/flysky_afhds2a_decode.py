#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Flysky AFHDS 2A SDR Decoder — IQ-to-packet decoder for the A7105-based protocol.

Decodes Flysky AFHDS 2A (Automatic Frequency Hopping Digital System 2A) RC
control link packets from raw IQ captures or live SDR input.  The protocol
uses the AMICCOM A7105 transceiver chip at 2.4 GHz with 2-GFSK modulation
at 500 kbps.

Technical reference:
  - AMICCOM A7105 datasheet (public)
  - DeviationTX project (GPL, protocol analysis)
  - Pascal Langer's multi-module project (GPL, AFHDS2A implementation)

Protocol summary:
  Band:        2.408–2.475 GHz
  Modulation:  2-GFSK, 500 kbps symbol rate, ±186 kHz deviation
  FHSS:        16 hop channels, ~3.85 ms dwell per hop
  Preamble:    A7105 hardware preamble — 4 bytes of 0x55 (alternating bits)
  Sync word:   4-byte A7105 ID code (TX-specific, set during binding)
  Packet:      37 bytes payload
    Byte 0:    Packet type (0x58=sticks, 0xAA=bind, 0x56=telemetry req, etc.)
    Bytes 1-4: TX ID
    Bytes 5-8: RX ID
    Bytes 9+:  14 channels × 16-bit big-endian (1000–2000 µs range)
    Last 2:    CRC-16/CCITT

NO REAL FLYSKY IQ CAPTURE EXISTS IN THIS PROJECT.  To produce a valid
capture for testing this decoder:

    # HackRF, center on a known AFHDS 2A channel, 2 Msps is sufficient
    hackrf_transfer -r flysky_capture.cs8 -f 2440000000 -s 2000000 -g 40 -l 32

    # Then convert to complex64:
    python3 -c "
    import numpy as np
    raw = np.fromfile('flysky_capture.cs8', dtype=np.int8)
    iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)
    iq /= 128.0
    iq.tofile('flysky_capture.cf32')
    "

    # Or with USRP B205mini via sdr_common:
    python3 tools/flysky_afhds2a_decode.py --live --freq 2.44e9 --sdr-rate 2e6

For authorized security research and CTF use only.
Requires: NumPy, SciPy.  Optional: SoapySDR (live capture via sdr_common).
Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np
from scipy.signal import firwin, lfilter, resample_poly

# ---------------------------------------------------------------------------
# A7105 / AFHDS 2A constants
# ---------------------------------------------------------------------------

A7105_PREAMBLE = bytes([0x55, 0x55, 0x55, 0x55])
A7105_PREAMBLE_BITS = np.array([int(b) for byte in A7105_PREAMBLE for b in f"{byte:08b}"], dtype=np.int8)

AFHDS2A_PAYLOAD_LEN = 37
AFHDS2A_NUM_CHANNELS = 14
AFHDS2A_HOP_CHANNELS = 16
AFHDS2A_HOP_DWELL_MS = 3.85

AFHDS2A_DATA_RATE = 500_000
AFHDS2A_DEVIATION = 186_000
AFHDS2A_CHANNEL_BW = 500_000

# Packet type identifiers
PKT_TYPE_STICKS      = 0x58
PKT_TYPE_BIND        = 0xAA
PKT_TYPE_TELEM_REQ   = 0x56
PKT_TYPE_TELEM_RESP  = 0xAA  # context-dependent: bind vs telemetry
PKT_TYPE_FAILSAFE    = 0x56

PKT_TYPES = {
    0x58: "sticks",
    0xAA: "bind",
    0x56: "telemetry_req",
    0x01: "settings",
    0x02: "failsafe_set",
}

# AFHDS 2A frequency table base: 2408 MHz + channel_offset
# The A7105 uses a frequency register where freq = 2400 + reg_val * 0.5 MHz
# AFHDS 2A selects from a pool of channels across the 2.408–2.475 GHz range.
# Channel numbers map to: 2408 + channel_num * 0.5 MHz
AFHDS2A_FREQ_BASE_HZ = 2_408_000_000
AFHDS2A_FREQ_STEP_HZ = 500_000  # 0.5 MHz per channel number
AFHDS2A_MAX_CHANNEL = 134  # up to 2475 MHz

# Binding frequency: fixed channel during bind phase
AFHDS2A_BIND_CHANNEL = 0  # 2408 MHz
AFHDS2A_BIND_FREQ_HZ = AFHDS2A_FREQ_BASE_HZ

# CRC-16/CCITT (poly 0x1021, init 0xFFFF) — used by A7105 hardware CRC
CRC16_POLY = 0x1021
CRC16_INIT = 0xFFFF

# ---------------------------------------------------------------------------
# CRC-16/CCITT
# ---------------------------------------------------------------------------

_crc16_table: list[int] | None = None


def _build_crc16_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC16_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table.append(crc)
    return table


def crc16_ccitt(data: bytes, init: int = CRC16_INIT) -> int:
    global _crc16_table
    if _crc16_table is None:
        _crc16_table = _build_crc16_table()
    crc = init
    for b in data:
        crc = ((_crc16_table[((crc >> 8) ^ b) & 0xFF] ^ (crc << 8))) & 0xFFFF
    return crc


# ---------------------------------------------------------------------------
# AFHDS 2A hop sequence computation (from DeviationTX / multi-module)
# ---------------------------------------------------------------------------

def compute_hop_sequence(tx_id: bytes) -> list[int]:
    """Compute the 16-channel FHSS hop sequence from a 4-byte TX ID.

    Algorithm derived from the DeviationTX AFHDS 2A protocol module
    and the multi-module project source.  The hop table is deterministic
    given the TX ID.
    """
    if len(tx_id) != 4:
        raise ValueError("TX ID must be exactly 4 bytes")

    id0, id1, id2, id3 = tx_id

    # Seed the hop list from the TX ID bytes
    channels = []
    idx = 0
    for i in range(AFHDS2A_HOP_CHANNELS):
        # Generate candidate channel from ID, wrapping within valid range
        candidate = (id0 + id1 * (i + 1) + id2 * (i + 1) * (i + 1)) & 0xFF
        candidate = (candidate % (AFHDS2A_MAX_CHANNEL + 1))

        # Ensure odd/even alternation for spectral spread
        if i & 1:
            candidate |= 1  # odd channels for odd hop slots
        else:
            candidate &= ~1  # even channels for even hop slots

        # Avoid duplicates — shift forward if collision
        attempts = 0
        while candidate in channels and attempts < AFHDS2A_MAX_CHANNEL:
            candidate = (candidate + 2) % (AFHDS2A_MAX_CHANNEL + 1)
            if i & 1:
                candidate |= 1
            else:
                candidate &= ~1
            attempts += 1

        channels.append(candidate)

    return channels


def channel_to_freq_hz(channel: int) -> float:
    """Convert A7105 channel number to frequency in Hz."""
    return AFHDS2A_FREQ_BASE_HZ + channel * AFHDS2A_FREQ_STEP_HZ


# ---------------------------------------------------------------------------
# IQ file loading
# ---------------------------------------------------------------------------

def load_iq_file(path: str, fmt: str = "cf32", offset: int = 0,
                 count: int = -1) -> np.ndarray:
    """Load IQ samples from a file.

    Supported formats:
      cf32 / complex64 — interleaved float32 I/Q (GNU Radio default)
      cs16 / sc16      — interleaved int16 I/Q (USRP / UHD)
      cs8  / sc8       — interleaved int8 I/Q (HackRF / RTL-SDR)
      cu8              — interleaved uint8 I/Q (RTL-SDR raw)
    """
    fmt = fmt.lower().strip()

    if fmt in ("cf32", "complex64", "fc32"):
        raw = np.fromfile(path, dtype=np.float32, offset=offset * 8,
                          count=count * 2 if count > 0 else -1)
        return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)

    elif fmt in ("cs16", "sc16", "int16"):
        raw = np.fromfile(path, dtype=np.int16, offset=offset * 4,
                          count=count * 2 if count > 0 else -1)
        iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)
        return (iq / 32768.0).astype(np.complex64)

    elif fmt in ("cs8", "sc8", "int8"):
        raw = np.fromfile(path, dtype=np.int8, offset=offset * 2,
                          count=count * 2 if count > 0 else -1)
        iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)
        return (iq / 128.0).astype(np.complex64)

    elif fmt in ("cu8", "uint8", "u8"):
        raw = np.fromfile(path, dtype=np.uint8, offset=offset * 2,
                          count=count * 2 if count > 0 else -1)
        iq = (raw[0::2].astype(np.float32) - 127.5) + 1j * (raw[1::2].astype(np.float32) - 127.5)
        return (iq / 128.0).astype(np.complex64)

    else:
        raise ValueError(f"Unknown IQ format: {fmt!r}")


# ---------------------------------------------------------------------------
# 2-GFSK demodulation chain
# ---------------------------------------------------------------------------

class GFSKDemodulator:
    """2-GFSK demodulator: bandpass → FM discriminator → clock recovery → bits."""

    def __init__(self, sample_rate: float, data_rate: float = AFHDS2A_DATA_RATE,
                 deviation: float = AFHDS2A_DEVIATION):
        self.sample_rate = sample_rate
        self.data_rate = data_rate
        self.deviation = deviation
        self.samples_per_symbol = sample_rate / data_rate

        # Design a low-pass filter for the GFSK signal bandwidth
        bw = data_rate + 2 * deviation
        cutoff = min(bw / sample_rate, 0.45)
        numtaps = max(int(self.samples_per_symbol * 4) | 1, 31)
        if numtaps % 2 == 0:
            numtaps += 1
        self.channel_filter = firwin(numtaps, cutoff)

    def fm_discriminator(self, iq: np.ndarray) -> np.ndarray:
        """Compute instantaneous frequency via conjugate-multiply differentiator."""
        # d/dt(phase) ≈ arg(x[n] * conj(x[n-1]))
        product = iq[1:] * np.conj(iq[:-1])
        return np.angle(product)

    def clock_recover_mm(self, soft_symbols: np.ndarray) -> np.ndarray:
        """Mueller-Muller clock recovery for symbol sampling.

        Returns hard-decision bits (0/1) at symbol boundaries.
        """
        sps = self.samples_per_symbol
        bits = []
        mu = 0.0  # fractional symbol timing offset
        gain_mu = 0.01  # loop gain
        idx = int(sps)  # start one symbol in

        prev_sample = 0.0
        prev_decision = 0

        while idx < len(soft_symbols) - 1:
            # Interpolate at the fractional sample point
            frac = mu
            i0 = int(idx)
            if i0 + 1 < len(soft_symbols):
                sample = soft_symbols[i0] * (1.0 - frac) + soft_symbols[i0 + 1] * frac
            else:
                sample = soft_symbols[i0]

            # Hard decision
            decision = 1 if sample > 0 else 0
            decision_val = 1.0 if sample > 0 else -1.0

            # Mueller-Muller timing error
            timing_error = (prev_decision * sample - decision_val * prev_sample)
            mu += gain_mu * timing_error

            # Clamp mu to prevent runaway
            mu = max(-0.5, min(0.5, mu))

            prev_sample = sample
            prev_decision = decision_val
            bits.append(decision)

            idx += sps + mu
            mu = idx - int(idx)
            idx = int(idx)

        return np.array(bits, dtype=np.uint8)

    def demodulate(self, iq: np.ndarray) -> np.ndarray:
        """Full demodulation chain: filter → FM demod → clock recovery → bits."""
        # Channel filter
        filtered = lfilter(self.channel_filter, 1.0, iq)

        # FM discriminator — instantaneous frequency
        freq = self.fm_discriminator(filtered)

        # Normalize: positive deviation = 1, negative = -1
        # Scale so that ±deviation maps to ±1
        if np.std(freq) > 0:
            freq_norm = freq / (np.std(freq) * 2)
        else:
            return np.array([], dtype=np.uint8)

        # Clock recovery and bit slicing
        bits = self.clock_recover_mm(freq_norm)
        return bits


# ---------------------------------------------------------------------------
# Preamble / sync detection
# ---------------------------------------------------------------------------

def find_preamble(bits: np.ndarray, min_run: int = 24) -> list[int]:
    """Find A7105 preamble positions (runs of alternating 0/1).

    Returns indices of the first bit of each detected preamble.
    The A7105 preamble is 0x55555555 = 32 bits of alternating 0/1.
    We look for runs of at least `min_run` alternating bits.
    """
    if len(bits) < min_run:
        return []

    # Detect alternating pattern: XOR adjacent bits should be 1
    alternating = bits[:-1] ^ bits[1:]
    positions = []
    run_start = -1
    run_len = 0

    for i in range(len(alternating)):
        if alternating[i] == 1:
            if run_start < 0:
                run_start = i
                run_len = 2
            else:
                run_len += 1
        else:
            if run_len >= min_run:
                positions.append(run_start)
            run_start = -1
            run_len = 0

    if run_len >= min_run:
        positions.append(run_start)

    return positions


def extract_sync_and_payload(bits: np.ndarray, preamble_pos: int,
                             preamble_bits: int = 32) -> tuple[bytes, bytes] | None:
    """Extract the 4-byte sync word and 37-byte payload after a preamble.

    Returns (sync_word_bytes, payload_bytes) or None if not enough bits.
    """
    sync_start = preamble_pos + preamble_bits
    sync_bits_needed = 4 * 8  # 4-byte sync word
    payload_bits_needed = AFHDS2A_PAYLOAD_LEN * 8

    total_needed = sync_start + sync_bits_needed + payload_bits_needed
    if total_needed > len(bits):
        return None

    sync_bits = bits[sync_start:sync_start + sync_bits_needed]
    payload_start = sync_start + sync_bits_needed
    payload_bits = bits[payload_start:payload_start + payload_bits_needed]

    sync_bytes = bits_to_bytes(sync_bits)
    payload_bytes = bits_to_bytes(payload_bits)

    return sync_bytes, payload_bytes


def bits_to_bytes(bits: np.ndarray) -> bytes:
    """Convert a bit array (MSB first per byte) to bytes."""
    n = len(bits) // 8
    result = bytearray(n)
    for i in range(n):
        byte_val = 0
        for j in range(8):
            byte_val = (byte_val << 1) | int(bits[i * 8 + j])
        result[i] = byte_val
    return bytes(result)


# ---------------------------------------------------------------------------
# Packet parser
# ---------------------------------------------------------------------------

class AFHDS2APacket:
    """Parsed AFHDS 2A packet."""

    def __init__(self, sync_word: bytes, payload: bytes, freq_hz: float = 0.0,
                 timestamp: float = 0.0, crc_ok: bool = False):
        self.sync_word = sync_word
        self.raw_payload = payload
        self.freq_hz = freq_hz
        self.timestamp = timestamp
        self.crc_ok = crc_ok

        self.pkt_type = payload[0] if payload else 0
        self.pkt_type_name = PKT_TYPES.get(self.pkt_type, f"unknown_0x{self.pkt_type:02x}")
        self.tx_id = payload[1:5] if len(payload) >= 5 else b""
        self.rx_id = payload[5:9] if len(payload) >= 9 else b""
        self.channels: list[int] = []

        if self.pkt_type == PKT_TYPE_STICKS and len(payload) >= 9 + AFHDS2A_NUM_CHANNELS * 2:
            for ch in range(AFHDS2A_NUM_CHANNELS):
                offset = 9 + ch * 2
                val = struct.unpack(">H", payload[offset:offset + 2])[0]
                self.channels.append(val)

    def to_dict(self) -> dict:
        d: dict = {
            "timestamp": round(self.timestamp, 6),
            "sync_word": self.sync_word.hex(),
            "type": self.pkt_type_name,
            "type_code": f"0x{self.pkt_type:02x}",
            "tx_id": self.tx_id.hex(),
            "rx_id": self.rx_id.hex(),
            "crc_ok": self.crc_ok,
        }
        if self.freq_hz > 0:
            d["freq_mhz"] = round(self.freq_hz / 1e6, 3)
        if self.channels:
            d["channels"] = self.channels
        d["raw_hex"] = self.raw_payload.hex()
        return d


def parse_packet(sync_word: bytes, payload: bytes, freq_hz: float = 0.0,
                 timestamp: float = 0.0) -> AFHDS2APacket | None:
    """Parse and CRC-verify a 37-byte AFHDS 2A payload."""
    if len(payload) < AFHDS2A_PAYLOAD_LEN:
        return None

    # CRC is over the first 35 bytes; last 2 are the CRC
    data = payload[:AFHDS2A_PAYLOAD_LEN - 2]
    crc_received = struct.unpack(">H", payload[AFHDS2A_PAYLOAD_LEN - 2:AFHDS2A_PAYLOAD_LEN])[0]
    crc_computed = crc16_ccitt(data)
    crc_ok = crc_received == crc_computed

    return AFHDS2APacket(sync_word, payload, freq_hz=freq_hz,
                         timestamp=timestamp, crc_ok=crc_ok)


# ---------------------------------------------------------------------------
# Hop tracker
# ---------------------------------------------------------------------------

class HopTracker:
    """Track observed hop frequencies to reconstruct the FHSS sequence."""

    def __init__(self):
        self.observations: list[tuple[float, float]] = []  # (timestamp, freq_hz)
        self.tx_ids_seen: dict[str, int] = {}
        self.reconstructed_sequence: list[int] | None = None

    def observe(self, pkt: AFHDS2APacket):
        if pkt.freq_hz > 0:
            self.observations.append((pkt.timestamp, pkt.freq_hz))
        tx_key = pkt.tx_id.hex()
        self.tx_ids_seen[tx_key] = self.tx_ids_seen.get(tx_key, 0) + 1

    def try_reconstruct(self) -> list[int] | None:
        """Try to reconstruct the hop sequence from observations + TX ID."""
        if not self.tx_ids_seen:
            return None

        # Use the most frequently seen TX ID
        best_tx = max(self.tx_ids_seen, key=self.tx_ids_seen.get)
        tx_id = bytes.fromhex(best_tx)
        self.reconstructed_sequence = compute_hop_sequence(tx_id)
        return self.reconstructed_sequence

    def summary(self) -> dict:
        unique_freqs = sorted(set(f for _, f in self.observations))
        result: dict = {
            "total_hops_observed": len(self.observations),
            "unique_frequencies": len(unique_freqs),
            "tx_ids_seen": dict(self.tx_ids_seen),
        }
        if unique_freqs:
            result["freq_range_mhz"] = [
                round(unique_freqs[0] / 1e6, 3),
                round(unique_freqs[-1] / 1e6, 3),
            ]
        if self.reconstructed_sequence:
            result["reconstructed_hop_channels"] = self.reconstructed_sequence
            result["reconstructed_hop_freqs_mhz"] = [
                round(channel_to_freq_hz(ch) / 1e6, 3)
                for ch in self.reconstructed_sequence
            ]
        return result


# ---------------------------------------------------------------------------
# Main decoder pipeline
# ---------------------------------------------------------------------------

class AFHDS2ADecoder:
    """End-to-end AFHDS 2A decoder: IQ → packets."""

    def __init__(self, sample_rate: float, center_freq: float = 0.0,
                 known_sync: bytes | None = None, verbose: bool = False):
        self.sample_rate = sample_rate
        self.center_freq = center_freq
        self.known_sync = known_sync
        self.verbose = verbose
        self.demod = GFSKDemodulator(sample_rate)
        self.hop_tracker = HopTracker()
        self.packets: list[AFHDS2APacket] = []

    def decode_block(self, iq: np.ndarray, block_time: float = 0.0) -> list[AFHDS2APacket]:
        """Decode all packets in an IQ block."""
        bits = self.demod.demodulate(iq)
        if len(bits) == 0:
            return []

        preamble_positions = find_preamble(bits)
        found = []

        for pos in preamble_positions:
            result = extract_sync_and_payload(bits, pos)
            if result is None:
                continue

            sync_word, payload = result

            # Filter by known sync word if specified
            if self.known_sync and sync_word != self.known_sync:
                continue

            sample_offset = pos * self.demod.samples_per_symbol
            timestamp = block_time + sample_offset / self.sample_rate

            pkt = parse_packet(sync_word, payload, freq_hz=self.center_freq,
                               timestamp=timestamp)
            if pkt is None:
                continue

            self.hop_tracker.observe(pkt)
            self.packets.append(pkt)
            found.append(pkt)

            if self.verbose:
                status = "CRC OK" if pkt.crc_ok else "CRC FAIL"
                print(f"  [{status}] t={timestamp:.6f}s type={pkt.pkt_type_name} "
                      f"tx={pkt.tx_id.hex()} rx={pkt.rx_id.hex()}", file=sys.stderr)

        return found

    def decode_file(self, path: str, fmt: str = "cf32",
                    block_size: int = 0) -> list[AFHDS2APacket]:
        """Decode all packets in an IQ file, processing in blocks."""
        if block_size <= 0:
            # ~50 ms blocks by default
            block_size = int(self.sample_rate * 0.05)

        total_samples = Path(path).stat().st_size
        fmt_lower = fmt.lower()
        if fmt_lower in ("cf32", "complex64", "fc32"):
            total_samples //= 8
        elif fmt_lower in ("cs16", "sc16", "int16"):
            total_samples //= 4
        else:
            total_samples //= 2

        offset = 0
        all_packets: list[AFHDS2APacket] = []
        blocks_done = 0

        while offset < total_samples:
            count = min(block_size, total_samples - offset)
            if count < 1000:
                break

            iq = load_iq_file(path, fmt=fmt, offset=offset, count=count)
            block_time = offset / self.sample_rate

            found = self.decode_block(iq, block_time=block_time)
            all_packets.extend(found)

            offset += count
            blocks_done += 1

            if self.verbose and blocks_done % 100 == 0:
                elapsed = offset / self.sample_rate
                print(f"  Processed {elapsed:.3f}s, {len(all_packets)} packets found",
                      file=sys.stderr)

        return all_packets

    def decode_live(self, freq: float, duration: float = 0.0,
                    sdr_args: str = "", gain: float = 40.0,
                    rate: float | None = None):
        """Decode from a live SDR source using sdr_common."""
        try:
            from sdr_common import SDRDevice
        except ImportError:
            print("[!] Live capture requires sdr_common.py in the Python path.",
                  file=sys.stderr)
            print("    Ensure tools/ is on sys.path or run from the repo root.",
                  file=sys.stderr)
            sys.exit(1)

        if rate is None:
            rate = max(2_000_000, self.sample_rate)

        self.sample_rate = rate
        self.center_freq = freq
        self.demod = GFSKDemodulator(rate)

        sdr = SDRDevice(device_args=sdr_args)
        sdr.setup_rx(freq=freq, rate=rate, gain=gain)
        sdr.start_rx()

        print(f"[*] Live capture: {freq/1e6:.3f} MHz, {rate/1e6:.1f} Msps",
              file=sys.stderr)
        print(f"[*] Listening for AFHDS 2A packets...", file=sys.stderr)

        block_samples = int(rate * 0.05)  # 50 ms blocks
        start = time.time()

        try:
            while True:
                elapsed = time.time() - start
                if duration > 0 and elapsed >= duration:
                    break

                iq = sdr.receive(num_samples=block_samples)
                if len(iq) == 0:
                    continue

                found = self.decode_block(iq, block_time=elapsed)
                for pkt in found:
                    print(json.dumps(pkt.to_dict()), flush=True)

        except KeyboardInterrupt:
            print("\n[*] Stopped by user.", file=sys.stderr)
        finally:
            sdr.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Flysky AFHDS 2A SDR Decoder — IQ-to-packet decoder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Decode from a complex64 IQ file
  %(prog)s capture.cf32 --sample-rate 2e6 --center-freq 2.44e9

  # Decode from an int8 HackRF capture
  %(prog)s capture.cs8 --format cs8 --sample-rate 2e6 --center-freq 2.44e9

  # Live capture (requires SoapySDR + sdr_common)
  %(prog)s --live --freq 2.44e9 --sdr-rate 2e6

  # Filter by known TX sync word
  %(prog)s capture.cf32 --sample-rate 2e6 --sync-word a1b2c3d4

  # Show only CRC-valid packets
  %(prog)s capture.cf32 --sample-rate 2e6 --crc-only
""",
    )

    p.add_argument("iq_file", nargs="?", help="IQ capture file (omit for --live mode)")
    p.add_argument("--sample-rate", "-r", type=float, default=2e6,
                   help="Sample rate in Hz (default: 2e6)")
    p.add_argument("--center-freq", "-f", type=float, default=0.0,
                   help="Center frequency of capture in Hz (for metadata)")
    p.add_argument("--format", "-F", default="cf32",
                   choices=["cf32", "complex64", "cs16", "sc16", "cs8", "sc8", "cu8"],
                   help="IQ sample format (default: cf32)")
    p.add_argument("--sync-word", type=str, default=None,
                   help="Known 4-byte sync word as hex (e.g. a1b2c3d4) to filter packets")
    p.add_argument("--crc-only", action="store_true",
                   help="Output only CRC-valid packets")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Print progress and per-packet status to stderr")
    p.add_argument("--summary", action="store_true",
                   help="Print hop tracker summary at the end")

    live_group = p.add_argument_group("Live capture")
    live_group.add_argument("--live", action="store_true",
                            help="Capture from SDR instead of file")
    live_group.add_argument("--freq", type=float, default=2.44e9,
                            help="SDR center frequency in Hz (default: 2.44e9)")
    live_group.add_argument("--duration", type=float, default=0,
                            help="Capture duration in seconds (0 = indefinite)")
    live_group.add_argument("--sdr-device", default="",
                            help="SoapySDR device string")
    live_group.add_argument("--sdr-rate", type=float, default=2e6,
                            help="SDR sample rate in Hz (default: 2e6)")
    live_group.add_argument("--sdr-gain", type=float, default=40.0,
                            help="SDR gain in dB (default: 40)")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    sync_word = None
    if args.sync_word:
        try:
            sync_word = bytes.fromhex(args.sync_word)
            if len(sync_word) != 4:
                parser.error("--sync-word must be exactly 4 bytes (8 hex chars)")
        except ValueError:
            parser.error("--sync-word must be valid hex")

    if args.live:
        decoder = AFHDS2ADecoder(
            sample_rate=args.sdr_rate,
            center_freq=args.freq,
            known_sync=sync_word,
            verbose=args.verbose,
        )
        decoder.decode_live(
            freq=args.freq,
            duration=args.duration,
            sdr_args=args.sdr_device,
            gain=args.sdr_gain,
            rate=args.sdr_rate,
        )
        packets = decoder.packets
    elif args.iq_file:
        if not Path(args.iq_file).exists():
            parser.error(f"File not found: {args.iq_file}")

        decoder = AFHDS2ADecoder(
            sample_rate=args.sample_rate,
            center_freq=args.center_freq,
            known_sync=sync_word,
            verbose=args.verbose,
        )

        print(f"[*] Decoding {args.iq_file} ({args.format}, "
              f"{args.sample_rate/1e6:.1f} Msps)", file=sys.stderr)

        packets = decoder.decode_file(args.iq_file, fmt=args.format)

        print(f"[*] Found {len(packets)} packets "
              f"({sum(1 for p in packets if p.crc_ok)} CRC-valid)",
              file=sys.stderr)
    else:
        parser.error("Provide an IQ file or use --live mode")
        return

    # Output packets as JSON lines
    for pkt in packets:
        if args.crc_only and not pkt.crc_ok:
            continue
        print(json.dumps(pkt.to_dict()))

    # Hop tracker summary
    if args.summary and packets:
        decoder.hop_tracker.try_reconstruct()
        summary = decoder.hop_tracker.summary()
        print(json.dumps({"hop_summary": summary}), file=sys.stderr)


if __name__ == "__main__":
    main()
