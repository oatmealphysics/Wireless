#!/usr/bin/env python3
"""Extract candidate LoRa chirp symbols from a *local, receive-only* IQ file.

This is the synchronization/demodulation front end for the E8–TinyHawk
Betaflight 4.4.2 150 Hz benchmark. It intentionally does NOT call symbol bins
packet bytes: SX1280 long interleaving (modulation parameter 0x07) still needs
an independently validated deinterleaver, FEC and scrambling implementation.
No UID or packet field is emitted as a claimed decode.

Requires NumPy and SciPy (Python 3.10+). Reads at most one short packet window;
it does not load multi-gigabyte captures into memory.

Example (known channel-41 visit in our private 20-second capture):
  python tools/elrs-iq-symbols.py work/captures/e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq \
      --sample-rate 4000000 --center-hz 2441000000 \
      --channel-hz 2441378800 --packet-start 1.18793

  python tools/elrs-iq-symbols.py work/captures/2026-10-08_13-07-21_2450mhz_pred.cdif \
      --sample-rate 28000000 --center-hz 2450000000 \
      --channel-hz 2441378800 --packet-start 0.40729

Keep stdout and the raw capture under ignored ``work/`` if saving the result.
This script never transmits, opens a device, or modifies the input file.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np
from scipy.signal import resample_poly


BW_HZ = 812_500
SF = 7
N = 1 << SF
PREAMBLE_SYMBOLS = 12
SYNC_SYMBOLS = 2
DOWNCHIRP_SYMBOLS = 2.25


def upchirp() -> np.ndarray:
    n = np.arange(N, dtype=np.float64)
    return np.exp(1j * np.pi * (n * n / N - n)).astype(np.complex64)


def read_iq_window(path: Path, sample_rate: float, start_s: float,
                   length_s: float, input_format: str = "auto",
                   align_samples: int = 1) -> tuple[np.ndarray, float]:
    """Bounded IQ read; BLUE headers/trailers are never treated as samples."""
    size = path.stat().st_size
    if input_format == "auto":
        input_format = "blue" if path.suffix.lower() == ".cdif" else "sc16le"
    offset, payload_size = 0, size
    if input_format == "blue":
        if size < 512:
            raise ValueError("Truncated BLUE header")
        with path.open("rb") as stream:
            header = stream.read(512)
        if header[:12] != b"BLUEEEEIEEEI":
            raise ValueError("Unsupported BLUE header or byte order")
        if struct.unpack_from("<i", header, 48)[0] not in (1000, 1001):
            raise ValueError("Unsupported BLUE record type")
        input_format = {b"CF": "cf32le", b"CI": "sc16le"}.get(header[52:54], "")
        if not input_format:
            raise ValueError("Unsupported BLUE IQ datatype")
        start, length = struct.unpack_from("<dd", header, 32)
        if (not math.isfinite(start) or not math.isfinite(length)
                or start < 512 or length < 0 or not start.is_integer()
                or not length.is_integer() or start + length > size):
            raise ValueError("Invalid BLUE data boundaries")
        offset, payload_size = int(start), int(length)
        interval = struct.unpack_from("<d", header, 264)[0]
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Invalid BLUE sample interval")
        header_rate = 1 / interval
        if abs(header_rate / sample_rate - 1) > 1e-6:
            raise ValueError("--sample-rate disagrees with BLUE header")
    widths = {"sc16le": 4, "cf32le": 8}
    if input_format not in widths:
        raise ValueError("Unsupported IQ input format")
    width = widths[input_format]
    if payload_size % width:
        raise ValueError(f"{input_format} data length is not sample-aligned")
    if align_samples < 1:
        raise ValueError("sample-grid alignment must be positive")
    requested_first = max(0, round(start_s * sample_rate))
    first = (requested_first // align_samples) * align_samples
    count = min(payload_size // width - first,
                round(length_s * sample_rate) + requested_first - first)
    if count <= 0:
        raise ValueError("Requested window is outside the capture")
    with path.open("rb") as stream:
        stream.seek(offset + first * width)
        raw = stream.read(count * width)
    if len(raw) != count * width:
        raise ValueError("Short read: capture may still be copying")
    if input_format == "sc16le":
        iq = np.frombuffer(raw, dtype="<i2").astype(np.float32).view(np.complex64)
    else:
        iq = np.frombuffer(raw, dtype="<c8")
    return iq, first / sample_rate


def read_sc16_window(path: Path, sample_rate: float, start_s: float,
                     length_s: float) -> tuple[np.ndarray, float]:
    """Backward-compatible raw sc16le helper for offline callers."""
    return read_iq_window(path, sample_rate, start_s, length_s, "sc16le")


def shift_and_resample(iq: np.ndarray, sample_rate: float,
                       frequency_offset: float) -> tuple[np.ndarray, float]:
    """Mix channel to zero and resample to an integer 128 samples/symbol."""
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    ratio = Fraction(BW_HZ / sample_rate).limit_denominator(4096)
    actual_rate = sample_rate * ratio.numerator / ratio.denominator
    if abs(actual_rate - BW_HZ) > 1:
        raise ValueError("sample rate cannot be resampled accurately to 812.5 kS/s")
    n = np.arange(len(iq), dtype=np.float64)
    mixed = iq * np.exp(-2j * np.pi * frequency_offset * n / sample_rate)
    return resample_poly(mixed, ratio.numerator, ratio.denominator).astype(np.complex64), actual_rate


def symbol_spectrum(iq: np.ndarray, start: int, count: int,
                    template: np.ndarray) -> np.ndarray:
    end = start + count * N
    if start < 0 or end > len(iq):
        raise ValueError("Not enough samples for requested symbols")
    shaped = iq[start:end].reshape(count, N)
    power = np.abs(np.fft.fft(shaped * template.conj(), axis=1)) ** 2
    return power / np.maximum(power.sum(axis=1, keepdims=True), 1e-30)


def find_preamble(iq: np.ndarray, first: int, last: int) -> dict:
    """Seek 12 equal-bin upchirps followed by sync chirps offset 8 and 16.

    The ± sync orientation is searched because IQ spectrum conventions can
    reverse bin numbering. The winner is a PHY candidate, not a CRC check.
    """
    template = upchirp()
    best: dict | None = None
    for start in range(max(0, first), min(last, len(iq) - 14 * N) + 1):
        power = symbol_spectrum(iq, start, 14, template)
        preamble = power[:12].mean(axis=0)
        for direction in (-1, 1):
            sync_first = np.roll(power[12], -8 * direction)
            sync_second = np.roll(power[13], -16 * direction)
            # Both sync chirps must be present; a large preamble alone can
            # otherwise select an alignment shifted by a whole symbol.
            scores = 0.4 * preamble + 0.6 * np.minimum(sync_first, sync_second)
            baseline_bin = int(np.argmax(scores))
            score = float(scores[baseline_bin])
            if best is None or score > best["score"]:
                peaks = np.argmax(power, axis=1).astype(int)
                best = {"start_sample": start, "score": score,
                        "baseline_bin": baseline_bin, "sync_direction": direction,
                        "preamble_peak_bins": peaks[:12].tolist(),
                        "sync_peak_bins": peaks[12:14].tolist()}
    if best is None:
        raise ValueError("Search window is too short for preamble and sync")
    k = best["baseline_bin"]
    peaks = best["preamble_peak_bins"]
    best["preamble_consistent"] = sum(min((p - k) % N, (k - p) % N) <= 1 for p in peaks)
    expected = [(k + best["sync_direction"] * step) % N for step in (8, 16)]
    best["expected_sync_bins"] = expected
    best["sync_consistent"] = all(
        min((p - e) % N, (e - p) % N) <= 1
        for p, e in zip(best["sync_peak_bins"], expected)
    )
    return best


def extract_payload_symbols(iq: np.ndarray, preamble_start: int,
                            baseline_bin: int, max_symbols: int) -> list[dict]:
    # The quarter-downchirp is part of the LoRa frame delimiter.
    first = preamble_start + round((PREAMBLE_SYMBOLS + SYNC_SYMBOLS + DOWNCHIRP_SYMBOLS) * N)
    count = min(max_symbols, max(0, (len(iq) - first) // N))
    if not count:
        return []
    power = symbol_spectrum(iq, first, count, upchirp())
    peaks = np.argmax(power, axis=1)
    return [{"bin": int(k), "relative_bin": int((k - baseline_bin) % N),
             "peak_power_fraction": round(float(power[i, k]), 4)}
            for i, k in enumerate(peaks)]


def analyze(path: Path, sample_rate: float, center_hz: float, channel_hz: float,
            packet_start: float, search_us: float, max_symbols: int,
            input_format: str = "auto") -> dict:
    if sample_rate <= 0 or packet_start < 0 or search_us <= 0 or max_symbols < 0:
        raise ValueError("invalid rate, packet start, search span, or symbol count")
    if abs(channel_hz - center_hz) >= sample_rate / 2:
        raise ValueError("Channel lies outside the digital Nyquist span")
    margin = max(0.00015, search_us * 1e-6) + 0.0002
    window_start = max(0, packet_start - margin)
    window_length = 0.007 + 2 * margin
    print(f"Reading {window_length * 1000:.2f} ms from {path.name}", file=sys.stderr)
    # Anchoring input reads to the rational resampler's denominator keeps
    # output-chip timing on one global grid. Otherwise a 1-2 us change in the
    # guessed packet start can change polyphase alignment and symbol bins.
    resample_denominator = Fraction(BW_HZ / sample_rate).limit_denominator(4096).denominator
    iq, actual_start = read_iq_window(path, sample_rate, window_start,
                                      window_length, input_format,
                                      align_samples=resample_denominator)
    baseband, baseband_rate = shift_and_resample(iq, sample_rate, channel_hz - center_hz)
    first = round((packet_start - search_us * 1e-6 - actual_start) * baseband_rate)
    last = round((packet_start + search_us * 1e-6 - actual_start) * baseband_rate)
    preamble = find_preamble(baseband, first, last)
    payload = extract_payload_symbols(baseband, preamble["start_sample"],
                                      preamble["baseline_bin"], max_symbols)
    phy_candidate = (preamble["preamble_consistent"] >= 10 and
                     preamble["sync_consistent"] and preamble["score"] >= 0.2)
    return {"capture": str(path), "sample_rate_hz": sample_rate,
            "center_hz": center_hz, "channel_hz": channel_hz,
            "preamble_start_seconds": actual_start + preamble["start_sample"] / baseband_rate,
            "phy_mode": "SF7 BW812500 implicit header SX1280 LI 0x07 (source-matched candidate)",
            "preamble": preamble, "payload_symbols": payload,
            "phy_candidate": phy_candidate,
            "packet_bytes_decoded": False,
            "note": "Symbol bins are not packet bytes; no UID, CRC or control fields validated."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture", type=Path, help="Raw sc16le/cf32le IQ or MIDAS/BLUE .cdif")
    parser.add_argument("--input-format", choices=("auto", "sc16le", "cf32le", "blue"),
                        default="auto", help="IQ format (auto: BLUE for .cdif, otherwise sc16le)")
    parser.add_argument("--sample-rate", required=True, type=float, help="Complex samples per second")
    parser.add_argument("--center-hz", required=True, type=float, help="IQ center frequency")
    parser.add_argument("--channel-hz", required=True, type=float, help="Observed RF channel frequency")
    parser.add_argument("--packet-start", required=True, type=float, help="Approximate preamble start in seconds")
    parser.add_argument("--search-us", type=float, default=120,
                        help="Search ± this many microseconds around start (default 120)")
    parser.add_argument("--max-symbols", type=int, default=22, help="Maximum candidate payload symbols")
    args = parser.parse_args(argv)
    try:
        result = analyze(args.capture, args.sample_rate, args.center_hz,
                         args.channel_hz, args.packet_start, args.search_us,
                         args.max_symbols, args.input_format)
        print(json.dumps(result, indent=2))
        return 0 if result["phy_candidate"] else 1
    except (OSError, ValueError) as exc:
        print(f"Symbol extraction error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
