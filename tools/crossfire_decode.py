#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""TBS Crossfire SDR IQ decoder — sub-GHz LoRa (SX1272/SX1276).

Decodes TBS Crossfire RC control and telemetry frames from raw IQ captures
or live SDR input.  Crossfire uses LoRa (CSS) modulation on sub-GHz ISM
bands (868/915/433 MHz) via Semtech SX1272/SX1276 transceivers, in contrast
to ELRS 2.4 GHz which uses the SX1280.

Decode chain:
  1. Chirp demodulation (dechirp + FFT peak extraction)
  2. Preamble / sync-word detection
  3. Gray decode
  4. Standard diagonal de-interleave (SX127x, NOT the SX1280 long-interleaver)
  5. Hamming FEC decode (CR-dependent)
  6. De-whitening (SX127x 9-bit LFSR)
  7. CRC verification
  8. CRSF payload frame parsing

Crossfire modes (approximate):
  150 Hz : SF6,  BW 500 kHz, CR 4/5  — lowest latency, shortest range
   50 Hz : SF8,  BW 500 kHz, CR 4/5  — balanced
    4 Hz : SF12, BW 500 kHz, CR 4/8  — maximum range

VALIDATION STATUS: The LoRa PHY decode chain (de-interleave, FEC, whitening)
reuses the generic helpers from lora_decode.py whose encode→decode round trips
are self-consistent.  The chirp demodulation DSP is implemented from the
public LoRa PHY specification.  However, no real Crossfire IQ capture has been
validated against this decoder yet.  See --help for the exact SDR capture
commands needed to produce valid test input.

Requires: numpy, scipy.  Optional: SoapySDR (for live SDR capture).
For authorized CTF research only.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

# ---------------------------------------------------------------------------
# Reuse generic LoRa primitives from lora_decode.py
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lora_decode import (
    gray_decode,
    gray_decode_symbols,
    deinterleave_standard,
    fec_decode,
    dewhiten_nibbles,
    nibbles_to_bytes,
)

# ---------------------------------------------------------------------------
# Crossfire known parameters
# ---------------------------------------------------------------------------
CROSSFIRE_MODES = {
    "150hz": {"sf": 6,  "bw": 500_000, "cr": 1, "label": "150 Hz (SF6 / CR4/5)"},
    "50hz":  {"sf": 8,  "bw": 500_000, "cr": 1, "label": "50 Hz (SF8 / CR4/5)"},
    "4hz":   {"sf": 12, "bw": 500_000, "cr": 4, "label": "4 Hz (SF12 / CR4/8)"},
}

CROSSFIRE_BANDS = {
    "868": 868_000_000,
    "915": 915_000_000,
    "433": 433_000_000,
}

# LoRa sync words — Crossfire uses the private-network sync word 0x12
SYNC_WORD_PRIVATE = 0x12
SYNC_WORD_PUBLIC = 0x34

# ---------------------------------------------------------------------------
# CRSF frame constants (from crsf-packet-tool.py, duplicated minimally)
# ---------------------------------------------------------------------------
CRSF_SYNC = 0xC8

CRSF_FRAME_TYPES = {
    0x02: "GPS",
    0x08: "Battery",
    0x09: "Baro Altitude",
    0x0B: "Heartbeat",
    0x14: "Link Statistics",
    0x16: "RC Channels Packed",
    0x1E: "Attitude",
    0x21: "Flight Mode",
    0x28: "Device Ping",
    0x29: "Device Info",
    0x2A: "Parameter Settings Entry",
    0x2B: "Parameter Read",
    0x2C: "Parameter Write",
    0x32: "Command",
}


def _crsf_crc8(data: bytes, poly: int = 0xD5) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def parse_crsf_frame(data: bytes) -> dict | None:
    """Parse a single CRSF frame from raw bytes.

    Returns a dict with frame fields, or None if invalid.
    """
    if len(data) < 4:
        return None

    addr = data[0]
    length = data[1]
    if length < 2 or len(data) < length + 2:
        return None

    frame_type = data[2]
    payload = data[3:1 + length]  # length includes type + payload + crc
    crc_byte = data[1 + length]

    # CRC covers type + payload (bytes from index 2 to 1+length-1)
    crc_data = data[2:1 + length]
    expected_crc = _crsf_crc8(crc_data)
    crc_ok = crc_byte == expected_crc

    result = {
        "address": addr,
        "length": length,
        "type": frame_type,
        "type_name": CRSF_FRAME_TYPES.get(frame_type, f"Unknown(0x{frame_type:02X})"),
        "payload_hex": payload.hex(),
        "crc": crc_byte,
        "crc_ok": crc_ok,
    }

    # Decode RC channels if type 0x16
    if frame_type == 0x16 and len(payload) >= 22:
        result["channels"] = _decode_rc_channels(payload)

    # Decode link statistics if type 0x14
    if frame_type == 0x14 and len(payload) >= 10:
        result["link_stats"] = _decode_link_stats(payload)

    return result


def _decode_rc_channels(payload: bytes) -> list[int]:
    """Decode 16 x 11-bit RC channels from CRSF packed format."""
    if len(payload) < 22:
        return []
    bits = int.from_bytes(payload[:22], "little")
    channels = []
    for i in range(16):
        channels.append((bits >> (11 * i)) & 0x7FF)
    return channels


def _decode_link_stats(payload: bytes) -> dict:
    """Decode CRSF link statistics frame."""
    return {
        "uplink_rssi_ant1": -(payload[0]) if payload[0] else 0,
        "uplink_rssi_ant2": -(payload[1]) if payload[1] else 0,
        "uplink_link_quality": payload[2],
        "uplink_snr": struct.unpack_from("b", payload, 3)[0] if len(payload) > 3 else 0,
        "active_antenna": payload[4] if len(payload) > 4 else 0,
        "rf_mode": payload[5] if len(payload) > 5 else 0,
        "uplink_tx_power": payload[6] if len(payload) > 6 else 0,
        "downlink_rssi": -(payload[7]) if len(payload) > 7 and payload[7] else 0,
        "downlink_link_quality": payload[8] if len(payload) > 8 else 0,
        "downlink_snr": struct.unpack_from("b", payload, 9)[0] if len(payload) > 9 else 0,
    }


def extract_crsf_frames(data: bytes) -> list[dict]:
    """Scan a byte stream for CRSF frames (sync byte 0xC8)."""
    frames = []
    i = 0
    while i < len(data) - 3:
        if data[i] == CRSF_SYNC:
            length = data[i + 1]
            if 2 <= length <= 64 and i + length + 2 <= len(data):
                frame = parse_crsf_frame(data[i:i + length + 2])
                if frame is not None:
                    frame["offset"] = i
                    frames.append(frame)
                    i += length + 2
                    continue
        i += 1
    return frames


# ---------------------------------------------------------------------------
# LoRa chirp demodulation (SX127x sub-GHz)
# ---------------------------------------------------------------------------
def generate_chirp(sf: int, bw: float, fs: float, up: bool = True) -> np.ndarray:
    """Generate one LoRa chirp symbol (up or down) at the given sample rate.

    A LoRa up-chirp sweeps from -BW/2 to +BW/2 over one symbol period
    T_sym = 2^SF / BW.  The instantaneous frequency is:
        f(t) = -BW/2 + BW * t / T_sym     (for an up-chirp)

    The phase is the integral:
        phi(t) = 2*pi * (-BW/2 * t + BW * t^2 / (2 * T_sym))
    """
    n_chips = 1 << sf
    t_sym = n_chips / bw
    n_samples = int(round(fs * t_sym))
    t = np.arange(n_samples, dtype=np.float64) / fs
    sign = 1.0 if up else -1.0
    phase = 2.0 * np.pi * sign * (-bw / 2.0 * t + bw * t ** 2 / (2.0 * t_sym))
    return np.exp(1j * phase).astype(np.complex64)


def dechirp_symbol(samples: np.ndarray, sf: int, bw: float, fs: float) -> int:
    """Dechirp one symbol's worth of samples and return the FFT bin index.

    Multiply the received signal by a conjugate down-chirp (equivalent to
    mixing with the reference), then take the FFT.  The peak bin is the
    symbol value.
    """
    n_chips = 1 << sf
    t_sym = n_chips / bw
    n_sym_samples = int(round(fs * t_sym))

    if len(samples) < n_sym_samples:
        pad = np.zeros(n_sym_samples - len(samples), dtype=np.complex64)
        samples = np.concatenate([samples, pad])
    else:
        samples = samples[:n_sym_samples]

    # Generate conjugate down-chirp for dechirping
    down_chirp = generate_chirp(sf, bw, fs, up=False)

    # Dechirp
    dechirped = samples * down_chirp

    # Resample to exactly n_chips samples for clean FFT bin mapping
    if n_sym_samples != n_chips:
        # Use FFT-based resampling
        dechirped_fft = np.fft.fft(dechirped)
        resampled = np.zeros(n_chips, dtype=np.complex128)
        half = min(n_sym_samples, n_chips) // 2
        resampled[:half] = dechirped_fft[:half]
        resampled[-half:] = dechirped_fft[-half:]
        dechirped = np.fft.ifft(resampled).astype(np.complex64)

    # FFT and find peak
    spectrum = np.abs(np.fft.fft(dechirped))
    return int(np.argmax(spectrum))


def detect_preamble(
    samples: np.ndarray, sf: int, bw: float, fs: float,
    min_preamble: int = 6, threshold_db: float = 6.0,
) -> list[dict]:
    """Scan IQ samples for LoRa preamble sequences.

    A LoRa preamble is a sequence of identical up-chirps (all symbol value 0).
    We slide through the IQ data one symbol at a time, dechirping each, and
    look for runs where the peak bin is near zero (within ±1 bin tolerance)
    and the peak is significantly above the noise floor.

    Returns a list of candidate preamble detections with their sample offsets.
    """
    n_chips = 1 << sf
    t_sym = n_chips / bw
    n_sym_samples = int(round(fs * t_sym))
    step = n_sym_samples  # non-overlapping for speed, refine later

    detections = []
    run_start = None
    run_length = 0
    prev_bin = -1

    i = 0
    while i + n_sym_samples <= len(samples):
        sym_samples = samples[i:i + n_sym_samples]

        # Generate conjugate down-chirp
        down_chirp = generate_chirp(sf, bw, fs, up=False)
        if len(down_chirp) > len(sym_samples):
            down_chirp = down_chirp[:len(sym_samples)]
        elif len(sym_samples) > len(down_chirp):
            sym_samples = sym_samples[:len(down_chirp)]

        dechirped = sym_samples * down_chirp

        # Quick FFT — use n_chips bins for clean mapping
        if len(dechirped) != n_chips:
            dechirped_fft = np.fft.fft(dechirped)
            resampled = np.zeros(n_chips, dtype=np.complex128)
            half = min(len(dechirped), n_chips) // 2
            resampled[:half] = dechirped_fft[:half]
            resampled[-half:] = dechirped_fft[-half:]
            spectrum = np.abs(np.fft.ifft(resampled))
            spectrum = np.abs(np.fft.fft(spectrum))
        else:
            spectrum = np.abs(np.fft.fft(dechirped))

        peak_bin = int(np.argmax(spectrum))
        peak_power = spectrum[peak_bin]

        # Noise estimate: mean of non-peak bins
        mask = np.ones(n_chips, dtype=bool)
        mask[peak_bin] = False
        if peak_bin > 0:
            mask[peak_bin - 1] = False
        if peak_bin < n_chips - 1:
            mask[peak_bin + 1] = False
        noise_floor = np.mean(spectrum[mask]) if np.any(mask) else 1e-12

        snr_db = 10 * np.log10(max(peak_power, 1e-12) / max(noise_floor, 1e-12))

        # Preamble chirps dechirp to bin 0 (or n_chips-1 due to wrapping)
        is_preamble_bin = peak_bin <= 1 or peak_bin >= n_chips - 1
        is_strong = snr_db >= threshold_db

        if is_preamble_bin and is_strong:
            if run_start is None:
                run_start = i
            run_length += 1
        else:
            if run_length >= min_preamble:
                detections.append({
                    "sample_offset": run_start,
                    "time_offset": run_start / fs,
                    "preamble_symbols": run_length,
                    "data_start_sample": run_start + run_length * n_sym_samples,
                })
            run_start = None
            run_length = 0

        i += step

    # Check trailing run
    if run_length >= min_preamble and run_start is not None:
        detections.append({
            "sample_offset": run_start,
            "time_offset": run_start / fs,
            "preamble_symbols": run_length,
            "data_start_sample": run_start + run_length * n_sym_samples,
        })

    return detections


def demodulate_symbols(
    samples: np.ndarray, sf: int, bw: float, fs: float,
    n_symbols: int, start_sample: int = 0,
) -> list[dict]:
    """Demodulate a sequence of LoRa symbols from IQ samples.

    Returns a list of {bin, power_db, noise_db} for each symbol.
    """
    n_chips = 1 << sf
    t_sym = n_chips / bw
    n_sym_samples = int(round(fs * t_sym))
    symbols = []

    for idx in range(n_symbols):
        offset = start_sample + idx * n_sym_samples
        if offset + n_sym_samples > len(samples):
            break

        sym_samples = samples[offset:offset + n_sym_samples]
        sym_bin = dechirp_symbol(sym_samples, sf, bw, fs)

        # Power estimate
        down_chirp = generate_chirp(sf, bw, fs, up=False)
        if len(down_chirp) != len(sym_samples):
            min_len = min(len(down_chirp), len(sym_samples))
            dechirped = sym_samples[:min_len] * down_chirp[:min_len]
        else:
            dechirped = sym_samples * down_chirp

        spectrum = np.abs(np.fft.fft(dechirped, n=n_chips))
        peak_power = float(spectrum[sym_bin])
        mask = np.ones(n_chips, dtype=bool)
        mask[sym_bin] = False
        noise_est = float(np.mean(spectrum[mask])) if n_chips > 1 else 1e-12

        symbols.append({
            "bin": sym_bin,
            "power_db": round(20 * np.log10(max(peak_power, 1e-12)), 1),
            "noise_db": round(20 * np.log10(max(noise_est, 1e-12)), 1),
        })

    return symbols


def detect_sync_word(
    samples: np.ndarray, sf: int, bw: float, fs: float,
    preamble_end_sample: int, expected_sync: int = SYNC_WORD_PRIVATE,
) -> dict | None:
    """Detect the LoRa sync word after the preamble.

    The sync word is encoded as two symbols whose values are derived from
    the 8-bit sync word.  For SX127x:
      symbol_1 = (sync_word >> 4) * 8  (upper nibble × 8)
      symbol_2 = (sync_word & 0xF) * 8 (lower nibble × 8)

    After the sync word come 2.25 down-chirps (SFD), then the payload.
    """
    n_chips = 1 << sf
    t_sym = n_chips / bw
    n_sym_samples = int(round(fs * t_sym))

    # Demodulate two candidate sync symbols
    syms = demodulate_symbols(samples, sf, bw, fs, 2, preamble_end_sample)
    if len(syms) < 2:
        return None

    bin1 = syms[0]["bin"]
    bin2 = syms[1]["bin"]

    # Expected sync word bins
    exp_bin1 = ((expected_sync >> 4) & 0xF) * 8
    exp_bin2 = (expected_sync & 0xF) * 8

    # Allow ±1 bin tolerance
    match1 = abs(bin1 - exp_bin1) <= 1 or abs(bin1 - exp_bin1 + n_chips) <= 1
    match2 = abs(bin2 - exp_bin2) <= 1 or abs(bin2 - exp_bin2 + n_chips) <= 1

    if match1 and match2:
        # SFD is 2.25 down-chirps after the sync word
        sfd_samples = int(2.25 * n_sym_samples)
        payload_start = preamble_end_sample + 2 * n_sym_samples + sfd_samples
        return {
            "sync_word": expected_sync,
            "sync_bins": [bin1, bin2],
            "expected_bins": [exp_bin1, exp_bin2],
            "payload_start_sample": payload_start,
        }

    return None


# ---------------------------------------------------------------------------
# Full packet decode pipeline
# ---------------------------------------------------------------------------
def decode_packet(
    symbols: list[int], sf: int, cr: int, payload_len: int | None = None,
) -> dict:
    """Decode LoRa symbol bins → payload bytes via the standard SX127x chain.

    Uses the generic helpers from lora_decode.py (standard diagonal
    de-interleave, not the SX1280 long-interleaver).
    """
    # Gray decode
    decoded = gray_decode_symbols(symbols, sf)

    # Standard de-interleave
    codewords = deinterleave_standard(decoded, sf, cr)

    if not codewords:
        return {"error": "no codewords from de-interleave", "symbols": symbols}

    # FEC decode
    nibbles, fec_errors = fec_decode(codewords, cr)

    # De-whiten
    nibbles = dewhiten_nibbles(nibbles)

    # Nibbles → bytes
    payload = nibbles_to_bytes(nibbles)

    if payload_len is not None:
        payload = payload[:payload_len]

    result = {
        "payload_hex": payload.hex(),
        "payload_len": len(payload),
        "fec_errors": fec_errors,
        "n_symbols": len(symbols),
    }

    # Try CRSF frame extraction
    crsf_frames = extract_crsf_frames(payload)
    if crsf_frames:
        result["crsf_frames"] = crsf_frames

    return result


# ---------------------------------------------------------------------------
# IQ file loading
# ---------------------------------------------------------------------------
def load_iq_file(
    path: str, sample_rate: float, fmt: str = "auto",
    max_seconds: float | None = None,
) -> np.ndarray:
    """Load IQ samples from a file.

    Supported formats:
      complex64 — interleaved float32 I/Q (GNURadio .cfile, .raw, .complex64)
      int16     — interleaved int16 I/Q (HackRF, .cs16, .sc16)
      uint8     — interleaved uint8 I/Q offset-binary (RTL-SDR .cu8)
    """
    p = Path(path)
    size = p.stat().st_size

    if fmt == "auto":
        ext = p.suffix.lower()
        if ext in (".cfile", ".raw", ".complex64", ".cf32"):
            fmt = "complex64"
        elif ext in (".cs16", ".sc16", ".iq"):
            fmt = "int16"
        elif ext in (".cu8", ".raw8"):
            fmt = "uint8"
        else:
            # Guess from file size: int16 has 4 bytes/sample, complex64 has 8
            fmt = "complex64"

    if fmt == "complex64":
        bps = 8
    elif fmt == "int16":
        bps = 4
    elif fmt == "uint8":
        bps = 2
    else:
        raise ValueError(f"unknown format: {fmt}")

    n_samples = size // bps
    if max_seconds is not None:
        n_samples = min(n_samples, int(sample_rate * max_seconds))

    if fmt == "complex64":
        raw = np.fromfile(path, dtype=np.complex64, count=n_samples)
    elif fmt == "int16":
        raw = np.fromfile(path, dtype=np.int16, count=n_samples * 2)
        raw = raw.astype(np.float32) / 32768.0
        raw = raw[0::2] + 1j * raw[1::2]
        raw = raw.astype(np.complex64)
    elif fmt == "uint8":
        raw = np.fromfile(path, dtype=np.uint8, count=n_samples * 2)
        raw = (raw.astype(np.float32) - 127.5) / 127.5
        raw = raw[0::2] + 1j * raw[1::2]
        raw = raw.astype(np.complex64)

    return raw


# ---------------------------------------------------------------------------
# Full capture decode pipeline
# ---------------------------------------------------------------------------
def decode_capture(
    samples: np.ndarray,
    sf: int,
    bw: float,
    fs: float,
    cr: int,
    sync_word: int = SYNC_WORD_PRIVATE,
    max_payload_symbols: int = 64,
    min_preamble: int = 6,
    threshold_db: float = 6.0,
) -> list[dict]:
    """Full pipeline: IQ samples → decoded Crossfire packets.

    1. Detect preambles
    2. Verify sync word
    3. Demodulate payload symbols
    4. Decode through LoRa PHY chain
    5. Parse CRSF frames
    """
    packets = []

    preambles = detect_preamble(
        samples, sf, bw, fs,
        min_preamble=min_preamble,
        threshold_db=threshold_db,
    )

    for pdet in preambles:
        # Check sync word
        sync = detect_sync_word(
            samples, sf, bw, fs,
            pdet["data_start_sample"],
            expected_sync=sync_word,
        )

        if sync is None:
            # Try without sync word verification — might be a different word
            n_chips = 1 << sf
            t_sym = n_chips / bw
            n_sym_samples = int(round(fs * t_sym))
            # Skip sync (2 symbols) + SFD (2.25 symbols) = 4.25 symbols
            sfd_skip = int(4.25 * n_sym_samples)
            payload_start = pdet["data_start_sample"] + sfd_skip
            sync_info = {"sync_word": "unknown", "sync_verified": False}
        else:
            payload_start = sync["payload_start_sample"]
            sync_info = {
                "sync_word": f"0x{sync['sync_word']:02X}",
                "sync_bins": sync["sync_bins"],
                "sync_verified": True,
            }

        # Demodulate payload symbols
        sym_data = demodulate_symbols(
            samples, sf, bw, fs, max_payload_symbols, payload_start,
        )

        if not sym_data:
            continue

        symbol_bins = [s["bin"] for s in sym_data]

        # Decode through LoRa PHY
        decoded = decode_packet(symbol_bins, sf, cr)

        packet = {
            "time_offset": pdet["time_offset"],
            "sample_offset": pdet["sample_offset"],
            "preamble_symbols": pdet["preamble_symbols"],
            **sync_info,
            **decoded,
            "symbol_details": sym_data[:8],  # first 8 for diagnostics
        }

        packets.append(packet)

    return packets


# ---------------------------------------------------------------------------
# Live SDR capture (optional, requires SoapySDR)
# ---------------------------------------------------------------------------
def live_capture(args: argparse.Namespace) -> None:
    """Capture and decode Crossfire packets from a live SDR."""
    try:
        from sdr_common import SDRDevice, add_sdr_args
    except ImportError:
        print("[!] sdr_common.py not found — live SDR capture unavailable.",
              file=sys.stderr)
        sys.exit(1)

    mode = CROSSFIRE_MODES[args.mode]
    sf = mode["sf"]
    bw = mode["bw"]
    cr = mode["cr"]
    freq = CROSSFIRE_BANDS.get(args.band, int(args.band))

    # Sample rate: at least 2× bandwidth for Nyquist, prefer higher for margin
    sample_rate = args.sdr_rate or max(bw * 2, 1_000_000)

    sdr = SDRDevice(device_args=args.sdr_device, channel=args.sdr_channel)
    sdr.setup_rx(freq=freq, rate=sample_rate, gain=args.sdr_gain or 40)

    n_chips = 1 << sf
    t_sym = n_chips / bw
    # Capture enough for several packets
    chunk_duration = max(t_sym * 256, 0.5)  # at least 256 symbols or 0.5s
    chunk_samples = int(sample_rate * chunk_duration)

    print(f"[*] Crossfire live decode: {mode['label']}", file=sys.stderr)
    print(f"    Band: {freq / 1e6:.3f} MHz, BW: {bw / 1e3:.0f} kHz, "
          f"Rate: {sample_rate / 1e6:.3f} Msps", file=sys.stderr)
    print(f"    Capturing {chunk_duration:.3f}s chunks ({chunk_samples} samples)",
          file=sys.stderr)

    frame_count = 0
    try:
        while True:
            samples = sdr.receive(chunk_samples)
            if len(samples) == 0:
                time.sleep(0.01)
                continue

            packets = decode_capture(
                samples, sf, bw, sample_rate, cr,
                sync_word=args.sync_word,
                threshold_db=args.threshold,
            )

            for pkt in packets:
                frame_count += 1
                pkt["frame_number"] = frame_count
                print(json.dumps(pkt))
                sys.stdout.flush()

    except KeyboardInterrupt:
        print(f"\n[*] Stopped. Decoded {frame_count} packets.", file=sys.stderr)
    finally:
        sdr.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="TBS Crossfire SDR IQ decoder (sub-GHz LoRa)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
CAPTURE COMMANDS (no real Crossfire IQ capture validated yet):

  HackRF (915 MHz, 2 Msps, 30 dB gain):
    hackrf_transfer -r crossfire_915.cs16 -f 915000000 -s 2000000 -l 30 -g 30

  RTL-SDR (868 MHz, 2 Msps — RX only, limited BW):
    rtl_sdr -f 868000000 -s 2000000 -g 40 crossfire_868.cu8

  USRP B205mini (915 MHz, 2 Msps):
    uhd_rx_cfile --freq=915e6 --rate=2e6 --gain=40 crossfire_915.cfile

  SoapySDR (any device, 915 MHz):
    python3 crossfire_decode.py --live --band 915 --mode 50hz

Each capture should be made while a TBS Crossfire TX module is actively
transmitting (bound to an RX, stick inputs moving) within range of the
SDR antenna. A directional antenna or close proximity helps at sub-GHz.

EXAMPLES:

  Decode from IQ file:
    python3 crossfire_decode.py --iq crossfire_915.cs16 --sample-rate 2e6 \\
        --mode 50hz --band 915

  Decode with explicit parameters:
    python3 crossfire_decode.py --iq capture.cfile --sample-rate 2e6 \\
        --sf 8 --cr 1 --bw 500000

  Live capture and decode:
    python3 crossfire_decode.py --live --band 915 --mode 50hz
""",
    )

    # Input source
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--iq", metavar="FILE", help="Input IQ file path")
    source.add_argument("--live", action="store_true",
                        help="Live SDR capture (requires SoapySDR)")

    # Crossfire mode presets
    parser.add_argument("--mode", choices=list(CROSSFIRE_MODES.keys()),
                        default="50hz",
                        help="Crossfire rate mode preset (default: 50hz)")
    parser.add_argument("--band", default="915",
                        help="Frequency band: 868, 915, 433, or Hz value "
                             "(default: 915)")

    # Manual PHY overrides
    parser.add_argument("--sf", type=int, help="Override spreading factor")
    parser.add_argument("--cr", type=int, choices=[1, 2, 3, 4],
                        help="Override coding rate (1=4/5 .. 4=4/8)")
    parser.add_argument("--bw", type=float, help="Override bandwidth (Hz)")

    # IQ file options
    parser.add_argument("--sample-rate", type=float, default=2e6,
                        help="IQ file sample rate in Hz (default: 2e6)")
    parser.add_argument("--format", choices=["auto", "complex64", "int16", "uint8"],
                        default="auto",
                        help="IQ file format (default: auto-detect)")
    parser.add_argument("--max-seconds", type=float,
                        help="Maximum seconds of IQ data to process")
    parser.add_argument("--center-freq", type=float,
                        help="Center frequency of IQ capture (Hz). "
                             "Default: band center frequency.")

    # Detection parameters
    parser.add_argument("--sync-word", type=lambda x: int(x, 0),
                        default=SYNC_WORD_PRIVATE,
                        help="Expected LoRa sync word (default: 0x12)")
    parser.add_argument("--threshold", type=float, default=6.0,
                        help="Preamble detection threshold in dB (default: 6.0)")
    parser.add_argument("--min-preamble", type=int, default=6,
                        help="Minimum preamble symbols to detect (default: 6)")

    # SDR options (for --live mode)
    parser.add_argument("--sdr-device", default="",
                        help="SoapySDR device string")
    parser.add_argument("--sdr-rate", type=float,
                        help="SDR sample rate (Hz)")
    parser.add_argument("--sdr-gain", type=float, default=40,
                        help="SDR gain (dB)")
    parser.add_argument("--sdr-channel", type=int, default=0,
                        help="SDR channel index")

    # Output options
    parser.add_argument("--raw-symbols", action="store_true",
                        help="Also output raw symbol bins")
    parser.add_argument("--max-packets", type=int,
                        help="Stop after decoding N packets")

    args = parser.parse_args()

    # Resolve mode parameters
    mode = CROSSFIRE_MODES[args.mode]
    sf = args.sf or mode["sf"]
    cr = args.cr or mode["cr"]
    bw = args.bw or mode["bw"]

    if args.live:
        # Patch args for live_capture
        args.sf = sf
        args.cr = cr
        args.bw = bw
        live_capture(args)
        return

    # File-based decode
    if not args.iq:
        parser.error("--iq FILE required for offline decode")

    freq = CROSSFIRE_BANDS.get(args.band, int(float(args.band)))
    fs = args.sample_rate

    print(f"[*] Loading IQ: {args.iq}", file=sys.stderr)
    print(f"    SF={sf}, CR=4/{cr + 4}, BW={bw / 1e3:.0f} kHz, "
          f"Rate={fs / 1e6:.3f} Msps", file=sys.stderr)

    samples = load_iq_file(args.iq, fs, fmt=args.format,
                           max_seconds=args.max_seconds)
    print(f"    Loaded {len(samples)} samples "
          f"({len(samples) / fs:.3f} seconds)", file=sys.stderr)

    packets = decode_capture(
        samples, sf, bw, fs, cr,
        sync_word=args.sync_word,
        threshold_db=args.threshold,
        min_preamble=args.min_preamble,
    )

    print(f"[*] Found {len(packets)} candidate packet(s)", file=sys.stderr)

    for i, pkt in enumerate(packets):
        pkt["frame_number"] = i + 1
        if not args.raw_symbols:
            pkt.pop("symbol_details", None)
        print(json.dumps(pkt))

    if not packets:
        print("[*] No packets decoded. Tips:", file=sys.stderr)
        print("    - Verify the Crossfire TX was transmitting during capture",
              file=sys.stderr)
        print("    - Try --threshold 3.0 for weaker signals", file=sys.stderr)
        print("    - Ensure sample rate ≥ 2× bandwidth "
              f"(need ≥ {bw * 2 / 1e6:.1f} Msps)", file=sys.stderr)
        print("    - Check center frequency matches the Crossfire band",
              file=sys.stderr)


if __name__ == "__main__":
    main()
