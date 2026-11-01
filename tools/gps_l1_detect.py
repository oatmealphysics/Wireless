#!/usr/bin/env python3
"""GPS L1 C/A Signal Detector and Acquisition Engine

Detects and acquires GPS L1 C/A signals from raw SDR IQ captures. This is
NOT a full GPS receiver (no tracking loops, no navigation solution). It
performs signal acquisition: detecting which GPS satellites are visible and
estimating their Doppler shift and code phase.

GPS L1 C/A signal characteristics:
    Frequency:   1575.42 MHz (L1 band)
    Modulation:  BPSK with CDMA (Code Division Multiple Access)
    Chip rate:   1.023 Mchips/s (C/A code)
    Code length: 1023 chips per period (1 ms)
    Data rate:   50 bps navigation message
    Bandwidth:   ~2 MHz (main lobe), ~20 MHz (full null-to-null)
    Power:       ~-130 dBm at antenna (below thermal noise floor)

Each satellite broadcasts a unique Gold code (PRN 1–32). Acquisition
searches for these codes across Doppler frequency bins to identify
which satellites are in view.

SDR requirements:
    - Center frequency: 1575.42 MHz
    - Sample rate: >= 2 Msps (4 Msps recommended)
    - HackRF covers this band; RTL-SDR also works (with appropriate antenna)
    - An active GPS antenna with LNA significantly improves results

Capture command (HackRF):
    hackrf_transfer -r gps_l1_capture.raw -f 1575420000 -s 4000000 \\
        -g 40 -l 32 -a 1 -n 40000000

    This captures 10 seconds at 4 Msps (int8 IQ). An active GPS antenna
    with clear sky view is essential — indoor captures will fail.

Note: No real GPS L1 IQ capture exists in this project yet. The capture
command above documents how to obtain one.

For authorized security research and CTF purposes only.
Requires: numpy, scipy
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np
from scipy.fft import fft, ifft

# GPS L1 C/A Gold code generator polynomials
# G1: x^10 + x^3 + 1
# G2: x^10 + x^9 + x^8 + x^6 + x^3 + x^2 + 1
# Each PRN uses a specific pair of G2 taps for the code phase assignment

# PRN -> G2 tap pairs (from IS-GPS-200)
_PRN_TAPS = {
    1: (2, 6), 2: (3, 7), 3: (4, 8), 4: (5, 9), 5: (1, 9),
    6: (2, 10), 7: (1, 8), 8: (2, 9), 9: (3, 10), 10: (2, 3),
    11: (3, 4), 12: (5, 6), 13: (6, 7), 14: (7, 8), 15: (8, 9),
    16: (9, 10), 17: (1, 4), 18: (2, 5), 19: (3, 6), 20: (4, 7),
    21: (5, 8), 22: (6, 9), 23: (1, 3), 24: (4, 6), 25: (5, 7),
    26: (6, 8), 27: (7, 9), 28: (8, 10), 29: (1, 6), 30: (2, 7),
    31: (3, 8), 32: (4, 9),
}

CA_CODE_LEN = 1023
L1_FREQ = 1575.42e6
CA_CHIP_RATE = 1.023e6


def _lfsr_step(reg: int, taps: list[int], nbits: int = 10) -> tuple[int, int]:
    """Advance a Fibonacci LFSR by one step. Return (output_bit, new_reg)."""
    feedback = 0
    for t in taps:
        feedback ^= (reg >> (nbits - t)) & 1
    output = reg & 1
    reg = ((reg >> 1) | (feedback << (nbits - 1))) & ((1 << nbits) - 1)
    return output, reg


def generate_ca_code(prn: int) -> np.ndarray:
    """Generate the 1023-chip C/A Gold code for a given PRN (1-32).

    Returns an array of +1/-1 values (BPSK mapping: 0->+1, 1->-1).
    """
    if prn not in _PRN_TAPS:
        raise ValueError(f"PRN {prn} not in range 1-32")

    tap1, tap2 = _PRN_TAPS[prn]
    g1_taps = [3, 10]  # G1 feedback: x^10 + x^3 + 1
    g2_taps = [2, 3, 6, 8, 9, 10]  # G2 feedback polynomial

    g1_reg = 0x3FF  # all ones
    g2_reg = 0x3FF

    code = np.zeros(CA_CODE_LEN, dtype=np.int8)

    for i in range(CA_CODE_LEN):
        g1_out = g1_reg & 1
        g2_phase = ((g2_reg >> (10 - tap1)) & 1) ^ ((g2_reg >> (10 - tap2)) & 1)
        code[i] = g1_out ^ g2_phase

        # Advance G1
        fb1 = 0
        for t in g1_taps:
            fb1 ^= (g1_reg >> (10 - t)) & 1
        g1_reg = ((g1_reg >> 1) | (fb1 << 9)) & 0x3FF

        # Advance G2
        fb2 = 0
        for t in g2_taps:
            fb2 ^= (g2_reg >> (10 - t)) & 1
        g2_reg = ((g2_reg >> 1) | (fb2 << 9)) & 0x3FF

    # BPSK mapping: 0 -> +1, 1 -> -1
    return 1 - 2 * code.astype(np.float32)


def _resample_code(code: np.ndarray, sample_rate: float) -> np.ndarray:
    """Resample 1023-chip C/A code to the given sample rate (samples per ms)."""
    samples_per_ms = int(sample_rate / 1000)
    indices = np.arange(samples_per_ms) * CA_CHIP_RATE / sample_rate
    indices = indices.astype(int) % CA_CODE_LEN
    return code[indices]


def acquire_satellite(
    iq_data: np.ndarray,
    prn: int,
    sample_rate: float,
    doppler_range: float = 10000.0,
    doppler_step: float = 500.0,
    coherent_ms: int = 1,
    threshold: float = 2.5,
) -> dict | None:
    """Search for a GPS satellite signal using parallel code phase search.

    Uses the FFT-based circular correlation method: for each Doppler bin,
    multiply signal by carrier replica, correlate with local code via FFT,
    and look for a correlation peak above the noise floor.

    Args:
        iq_data: Complex IQ samples (at least coherent_ms milliseconds)
        prn: PRN number (1-32)
        sample_rate: Sample rate in Hz
        doppler_range: Search range in Hz (±)
        doppler_step: Doppler bin spacing in Hz
        coherent_ms: Coherent integration time in ms (1 typical for acquisition)
        threshold: Detection threshold (peak / second-peak ratio)

    Returns:
        Dict with PRN, Doppler, code_phase, SNR if detected; None otherwise.
    """
    samples_per_ms = int(sample_rate / 1000)
    n_samples = samples_per_ms * coherent_ms

    if len(iq_data) < n_samples:
        return None

    signal = iq_data[:n_samples]

    # Generate and resample local C/A code
    ca_code = generate_ca_code(prn)
    local_code = _resample_code(ca_code, sample_rate)

    if coherent_ms > 1:
        local_code = np.tile(local_code, coherent_ms)

    # Pre-compute FFT of conjugate code (for correlation)
    code_fft_conj = np.conj(fft(local_code))

    # Search across Doppler bins
    doppler_bins = np.arange(-doppler_range, doppler_range + doppler_step, doppler_step)
    t = np.arange(n_samples) / sample_rate

    best_peak = 0.0
    best_doppler = 0.0
    best_code_phase = 0
    best_corr_mag = None

    for doppler in doppler_bins:
        # Generate carrier replica with this Doppler offset
        carrier = np.exp(-1j * 2 * np.pi * doppler * t)
        # Mix signal with carrier
        mixed = signal * carrier
        # Circular correlation via FFT
        sig_fft = fft(mixed)
        corr = ifft(sig_fft * code_fft_conj)
        corr_mag = np.abs(corr)

        peak_idx = np.argmax(corr_mag)
        peak_val = corr_mag[peak_idx]

        if peak_val > best_peak:
            best_peak = peak_val
            best_doppler = doppler
            best_code_phase = peak_idx
            best_corr_mag = corr_mag.copy()

    if best_corr_mag is None:
        return None

    # Compute SNR: ratio of peak to mean of correlation (excluding peak region)
    mask = np.ones(len(best_corr_mag), dtype=bool)
    peak_region = 20  # samples around peak to exclude
    start = max(0, best_code_phase - peak_region)
    end = min(len(mask), best_code_phase + peak_region)
    mask[start:end] = False
    noise_mean = np.mean(best_corr_mag[mask])
    noise_std = np.std(best_corr_mag[mask])

    if noise_std == 0:
        return None

    snr = (best_peak - noise_mean) / noise_std

    if snr < threshold:
        return None

    # Convert code phase from samples to chips
    code_phase_chips = (best_code_phase / samples_per_ms) * CA_CHIP_RATE / 1000

    return {
        "prn": prn,
        "doppler_hz": float(best_doppler),
        "code_phase_samples": int(best_code_phase),
        "code_phase_chips": float(code_phase_chips),
        "snr": float(snr),
        "peak_magnitude": float(best_peak),
    }


def load_iq_file(
    path: str, sample_rate: float, fmt: str = "complex64", max_ms: int = 20
) -> np.ndarray:
    """Load IQ samples from a file.

    Supported formats:
        complex64  — interleaved float32 I/Q (numpy default)
        int8       — interleaved int8 I/Q (HackRF raw)
        int16      — interleaved int16 I/Q (sc16)
    """
    p = Path(path)
    max_samples = int(sample_rate / 1000 * max_ms)

    if fmt == "complex64":
        data = np.fromfile(p, dtype=np.complex64, count=max_samples)
    elif fmt == "int8":
        raw = np.fromfile(p, dtype=np.int8, count=max_samples * 2)
        data = (raw[0::2] + 1j * raw[1::2]).astype(np.complex64) / 128.0
    elif fmt == "int16":
        raw = np.fromfile(p, dtype=np.int16, count=max_samples * 2)
        data = (raw[0::2] + 1j * raw[1::2]).astype(np.complex64) / 32768.0
    else:
        raise ValueError(f"Unknown format: {fmt}")

    return data


def scan_all_prns(
    iq_data: np.ndarray,
    sample_rate: float,
    doppler_range: float = 10000.0,
    doppler_step: float = 500.0,
    threshold: float = 2.5,
    prn_range: tuple[int, int] = (1, 32),
) -> list[dict]:
    """Scan for all GPS satellites (PRN 1-32).

    Returns list of acquired satellites sorted by SNR (strongest first).
    """
    results = []
    for prn in range(prn_range[0], prn_range[1] + 1):
        result = acquire_satellite(
            iq_data, prn, sample_rate,
            doppler_range=doppler_range,
            doppler_step=doppler_step,
            threshold=threshold,
        )
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r["snr"], reverse=True)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="GPS L1 C/A signal detector — acquires visible satellites from SDR IQ data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Capture command (HackRF):
    hackrf_transfer -r gps_l1_capture.raw -f 1575420000 -s 4000000 \\
        -g 40 -l 32 -a 1 -n 40000000

    (10 seconds at 4 Msps, int8 IQ. Requires active GPS antenna with sky view.)

NOTE: No real GPS L1 IQ capture exists in this project. The tool is
validated against the C/A code generation (known Gold codes) but has not
been tested against a real off-air capture. Flag this as a gap to fill.
""",
    )
    parser.add_argument("iq_file", help="Path to IQ capture file")
    parser.add_argument(
        "--sample-rate", type=float, default=4e6,
        help="Sample rate in Hz (default: 4e6)",
    )
    parser.add_argument(
        "--format", choices=["complex64", "int8", "int16"], default="complex64",
        help="IQ file format (default: complex64)",
    )
    parser.add_argument(
        "--doppler-range", type=float, default=10000,
        help="Doppler search range in Hz (default: ±10000)",
    )
    parser.add_argument(
        "--doppler-step", type=float, default=500,
        help="Doppler bin spacing in Hz (default: 500)",
    )
    parser.add_argument(
        "--threshold", type=float, default=2.5,
        help="Detection threshold as SNR (default: 2.5)",
    )
    parser.add_argument(
        "--prn", type=int, nargs="*", default=None,
        help="Specific PRN(s) to search for (default: all 1-32)",
    )
    parser.add_argument(
        "--max-ms", type=int, default=20,
        help="Maximum milliseconds of data to load (default: 20)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON lines",
    )

    # Optional live SDR support
    try:
        from sdr_common import add_sdr_args
        add_sdr_args(parser)
        has_sdr = True
    except ImportError:
        has_sdr = False

    args = parser.parse_args()

    print(f"[*] Loading IQ data from {args.iq_file}", file=sys.stderr)
    iq_data = load_iq_file(
        args.iq_file, args.sample_rate, args.format, args.max_ms
    )
    duration_ms = len(iq_data) / args.sample_rate * 1000
    print(
        f"[*] Loaded {len(iq_data)} samples ({duration_ms:.1f} ms) "
        f"at {args.sample_rate/1e6:.1f} Msps",
        file=sys.stderr,
    )

    if args.prn:
        print(f"[*] Searching for PRN(s): {args.prn}", file=sys.stderr)
        results = []
        for prn in args.prn:
            r = acquire_satellite(
                iq_data, prn, args.sample_rate,
                doppler_range=args.doppler_range,
                doppler_step=args.doppler_step,
                threshold=args.threshold,
            )
            if r:
                results.append(r)
    else:
        print("[*] Scanning all PRNs (1-32)...", file=sys.stderr)
        results = scan_all_prns(
            iq_data, args.sample_rate,
            doppler_range=args.doppler_range,
            doppler_step=args.doppler_step,
            threshold=args.threshold,
        )

    if not results:
        print("[!] No GPS satellites acquired.", file=sys.stderr)
        print(
            "    Ensure the capture was taken outdoors with an active GPS antenna.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n[+] Acquired {len(results)} satellite(s):\n", file=sys.stderr)

    for r in results:
        if args.json:
            print(json.dumps(r))
        else:
            print(
                f"  PRN {r['prn']:2d}  |  Doppler: {r['doppler_hz']:+8.1f} Hz  |  "
                f"Code phase: {r['code_phase_chips']:7.1f} chips  |  "
                f"SNR: {r['snr']:5.1f}",
                file=sys.stderr,
            )

    if not args.json:
        print(file=sys.stderr)


if __name__ == "__main__":
    main()
