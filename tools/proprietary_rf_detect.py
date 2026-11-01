#!/usr/bin/env python3
"""
Proprietary RF Protocol Detector — DragonLink & DJI OcuSync/Lightbridge

DETECTION AND FINGERPRINTING ONLY — these protocols are proprietary and
encrypted; full packet decode from SDR IQ is not feasible. This tool
identifies the presence of these signals, estimates key parameters, and
classifies the protocol variant from spectral and cyclostationary features.

Supported detections:
    DragonLink  — 433/900 MHz FSK long-range telemetry/control
    DJI         — 2.4/5.8 GHz OFDM video/control (OcuSync, Lightbridge)

Input: raw IQ file (complex64 or signed int16 interleaved) or live SDR
via SoapySDR (through the project's sdr_common abstraction layer).

Usage:
    # Detect DragonLink on 433 MHz from IQ file
    python3 proprietary_rf_detect.py dragonlink --iq-file capture_433mhz.raw \\
        --sample-rate 2e6 --center-freq 433e6

    # Detect DJI on 2.4 GHz from IQ file
    python3 proprietary_rf_detect.py dji --iq-file capture_2400mhz.raw \\
        --sample-rate 20e6 --center-freq 2.4e9

    # Broadband scan mode with live SDR
    python3 proprietary_rf_detect.py scan --band 2.4ghz

    # Scan from IQ file
    python3 proprietary_rf_detect.py scan --iq-file wideband.raw \\
        --sample-rate 20e6 --center-freq 2.44e9

Capture commands for generating valid input (HackRF example):
    # DragonLink 433 MHz (2 Msps, 8-bit signed)
    hackrf_transfer -r dragonlink_433.raw -f 433000000 -s 2000000 -g 40 -l 32 -n 4000000

    # DragonLink 900 MHz
    hackrf_transfer -r dragonlink_900.raw -f 915000000 -s 2000000 -g 40 -l 32 -n 4000000

    # DJI 2.4 GHz (20 Msps for full OFDM bandwidth)
    hackrf_transfer -r dji_2400.raw -f 2400000000 -s 20000000 -g 40 -l 32 -n 40000000

    # DJI 5.8 GHz
    hackrf_transfer -r dji_5800.raw -f 5800000000 -s 20000000 -g 40 -l 32 -n 40000000

    # SoapySDR (any device) — complex float32
    python3 -c "
    from sdr_common import SDRDevice
    import numpy as np
    sdr = SDRDevice()
    sdr.setup_rx(freq=2.4e9, rate=20e6, gain=40)
    samples = sdr.receive(num_samples=20_000_000)
    samples.tofile('dji_2400_cf32.raw')
    sdr.close()
    "

⚠ NO REAL CAPTURES EXIST YET for validation. All detection thresholds are
  literature-derived estimates. The tool flags detection confidence honestly.
  Validation requires IQ captures taken while the target protocol is active.

Requires: NumPy, SciPy. Python 3.10+.
For authorized security research and CTF only.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from scipy import signal as sp_signal

# Optional live SDR support
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sdr_common import SDRDevice, add_sdr_args
    HAS_SDR = True
except ImportError:
    HAS_SDR = False


# ---------------------------------------------------------------------------
# IQ file loading
# ---------------------------------------------------------------------------

def load_iq_file(path: str, sample_rate: float, fmt: str = "auto",
                 max_samples: int = 0) -> np.ndarray:
    """Load IQ samples from a raw binary file.

    Supported formats:
        cf32  — interleaved float32 (numpy complex64)
        cs16  — interleaved signed int16 (hackrf_transfer default)
        cu8   — interleaved unsigned int8 (rtl_sdr default)
        auto  — guess from file extension or try cf32
    """
    p = Path(path)
    if not p.exists():
        print(f"[!] File not found: {path}", file=sys.stderr)
        sys.exit(1)

    file_bytes = p.stat().st_size
    if fmt == "auto":
        suffix = p.suffix.lower()
        if suffix in (".cf32", ".fc32", ".cfile"):
            fmt = "cf32"
        elif suffix in (".cs16", ".sc16", ".iq"):
            fmt = "cs16"
        elif suffix in (".cu8", ".raw"):
            # HackRF .raw is cs8 (signed int8), rtl_sdr .raw is cu8
            # Default to cs16 for .raw (HackRF hackrf_transfer -r)
            # since that's the more common drone-research SDR
            fmt = "cs16"
        else:
            fmt = "cf32"

    if fmt == "cf32":
        bps = 8  # 2 × float32
        data = np.fromfile(path, dtype=np.complex64,
                           count=max_samples if max_samples else -1)
    elif fmt == "cs16":
        bps = 4  # 2 × int16
        count = max_samples * 2 if max_samples else -1
        raw = np.fromfile(path, dtype=np.int16, count=count)
        raw = raw[:len(raw) // 2 * 2]
        data = (raw[0::2] + 1j * raw[1::2]).astype(np.complex64) / 32768.0
    elif fmt == "cu8":
        bps = 2  # 2 × uint8
        count = max_samples * 2 if max_samples else -1
        raw = np.fromfile(path, dtype=np.uint8, count=count)
        raw = raw[:len(raw) // 2 * 2]
        data = ((raw[0::2].astype(np.float32) - 127.5) / 127.5 +
                1j * (raw[1::2].astype(np.float32) - 127.5) / 127.5
                ).astype(np.complex64)
    elif fmt == "cs8":
        bps = 2
        count = max_samples * 2 if max_samples else -1
        raw = np.fromfile(path, dtype=np.int8, count=count)
        raw = raw[:len(raw) // 2 * 2]
        data = (raw[0::2].astype(np.float32) / 128.0 +
                1j * raw[1::2].astype(np.float32) / 128.0
                ).astype(np.complex64)
    else:
        print(f"[!] Unknown IQ format: {fmt}", file=sys.stderr)
        sys.exit(1)

    total_samples = file_bytes // bps
    duration = total_samples / sample_rate
    print(f"[*] Loaded {len(data)} samples ({len(data)/sample_rate:.3f}s) "
          f"from {p.name} (format={fmt})", file=sys.stderr)
    return data


# ---------------------------------------------------------------------------
# Detection result types
# ---------------------------------------------------------------------------

@dataclass
class Detection:
    protocol: str
    confidence: float          # 0.0–1.0
    frequency_hz: float
    power_dbfs: float
    bandwidth_hz: float
    details: dict = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["frequency_mhz"] = round(self.frequency_hz / 1e6, 4)
        d["bandwidth_khz"] = round(self.bandwidth_hz / 1e3, 1)
        d["power_dbfs"] = round(self.power_dbfs, 1)
        d["confidence_pct"] = round(self.confidence * 100, 1)
        return d


# ---------------------------------------------------------------------------
# Spectral utilities
# ---------------------------------------------------------------------------

def compute_psd(samples: np.ndarray, sample_rate: float,
                nfft: int = 4096) -> tuple[np.ndarray, np.ndarray]:
    """Welch PSD estimate. Returns (frequencies_hz, power_db)."""
    freqs, psd = sp_signal.welch(
        samples, fs=sample_rate, nperseg=min(nfft, len(samples)),
        noverlap=nfft // 2, return_onesided=False, detrend="constant",
    )
    # fftshift so negative freqs come first
    freqs = np.fft.fftshift(freqs)
    psd = np.fft.fftshift(psd)
    psd_db = 10 * np.log10(np.maximum(psd, 1e-20))
    return freqs, psd_db


def estimate_noise_floor(psd_db: np.ndarray) -> float:
    """Robust noise floor estimate from PSD (median of lower quartile)."""
    sorted_psd = np.sort(psd_db)
    q25 = len(sorted_psd) // 4
    return float(np.median(sorted_psd[:max(q25, 1)]))


def find_spectral_peaks(freqs: np.ndarray, psd_db: np.ndarray,
                        noise_floor: float,
                        min_snr_db: float = 6.0,
                        min_distance_hz: float = 10e3,
                        sample_rate: float = 1.0) -> list[dict]:
    """Find peaks above the noise floor in PSD."""
    threshold = noise_floor + min_snr_db
    min_distance_bins = max(1, int(min_distance_hz / (sample_rate / len(freqs))))
    peak_indices, properties = sp_signal.find_peaks(
        psd_db, height=threshold, distance=min_distance_bins,
        prominence=min_snr_db / 2,
    )
    peaks = []
    for idx in peak_indices:
        peaks.append({
            "freq_hz": float(freqs[idx]),
            "power_db": float(psd_db[idx]),
            "snr_db": float(psd_db[idx] - noise_floor),
        })
    peaks.sort(key=lambda p: p["power_db"], reverse=True)
    return peaks


# ---------------------------------------------------------------------------
# DragonLink FSK Detector
# ---------------------------------------------------------------------------

# Known DragonLink parameters (from public docs / community reports)
DRAGONLINK_BANDS = {
    "433": {"center": 433.0e6, "span": 2.0e6},     # EU ISM 433 MHz
    "900": {"center": 915.0e6, "span": 10.0e6},     # US ISM 902–928 MHz
}

# SiK radio parameters for discrimination
SIK_TYPICAL = {
    "deviation_hz": 25_000,     # ±25 kHz typical
    "data_rate_bps": 64_000,    # 64 kbps air data rate
    "channel_bw_hz": 100_000,   # ~100 kHz occupied BW
}

DRAGONLINK_TYPICAL = {
    "deviation_hz_range": (10_000, 50_000),
    "data_rate_range": (9_600, 100_000),
    "channel_bw_hz_range": (30_000, 200_000),
}


def detect_fsk_signals(samples: np.ndarray, sample_rate: float,
                       center_freq: float) -> list[dict]:
    """Detect FSK modulated signals by instantaneous frequency analysis.

    FSK signals show up as clusters in the instantaneous frequency
    histogram — typically two peaks (binary FSK) or four (4-FSK).
    """
    # Instantaneous frequency via analytic signal phase derivative
    analytic = sp_signal.hilbert(np.real(samples[:min(len(samples), 500_000)]))
    inst_phase = np.unwrap(np.angle(analytic))
    inst_freq = np.diff(inst_phase) / (2 * np.pi) * sample_rate

    # Remove DC / very low frequency content
    inst_freq = inst_freq - np.median(inst_freq)

    # Look for bimodal distribution (binary FSK signature)
    hist_bins = min(500, len(inst_freq) // 100)
    if hist_bins < 10:
        return []
    counts, bin_edges = np.histogram(inst_freq, bins=hist_bins)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    # Smooth histogram and find peaks
    if len(counts) > 10:
        kernel_size = max(3, len(counts) // 50)
        if kernel_size % 2 == 0:
            kernel_size += 1
        smoothed = sp_signal.medfilt(counts.astype(float), kernel_size)
    else:
        smoothed = counts.astype(float)

    peak_idx, _ = sp_signal.find_peaks(smoothed, height=np.max(smoothed) * 0.1,
                                        distance=max(1, hist_bins // 20))

    fsk_signals = []
    if len(peak_idx) >= 2:
        # Sort by height, take top peaks
        sorted_peaks = sorted(peak_idx, key=lambda i: smoothed[i], reverse=True)

        for i in range(min(len(sorted_peaks), 4)):
            for j in range(i + 1, min(len(sorted_peaks), 4)):
                p1, p2 = sorted_peaks[i], sorted_peaks[j]
                freq1 = bin_centers[p1]
                freq2 = bin_centers[p2]
                deviation = abs(freq2 - freq1) / 2
                center_offset = (freq1 + freq2) / 2

                # Plausible FSK deviation range for drone telemetry
                if 5_000 < deviation < 100_000:
                    # Estimate data rate from deviation (Carson's rule inverse)
                    est_data_rate = deviation * 2  # rough for modulation index ~1

                    # Peak height ratio — balanced peaks suggest FSK
                    height_ratio = min(smoothed[p1], smoothed[p2]) / max(smoothed[p1], smoothed[p2])

                    fsk_signals.append({
                        "center_offset_hz": float(center_offset),
                        "deviation_hz": float(deviation),
                        "est_data_rate_bps": float(est_data_rate),
                        "peak_balance": float(height_ratio),
                        "est_freq_hz": center_freq + center_offset,
                    })

    return fsk_signals


def classify_fsk_source(fsk_info: dict) -> tuple[str, float, list[str]]:
    """Classify an FSK detection as DragonLink, SiK, or unknown.

    Returns (classification, confidence, caveats).
    """
    dev = fsk_info["deviation_hz"]
    rate = fsk_info["est_data_rate_bps"]
    balance = fsk_info["peak_balance"]
    caveats = []

    # SiK radios: ±25 kHz dev, ~64 kbps, very consistent
    sik_dev_match = abs(dev - SIK_TYPICAL["deviation_hz"]) / SIK_TYPICAL["deviation_hz"]

    # DragonLink: wider range of parameters
    dl_min_dev, dl_max_dev = DRAGONLINK_TYPICAL["deviation_hz_range"]
    dl_in_range = dl_min_dev <= dev <= dl_max_dev

    if sik_dev_match < 0.15 and balance > 0.6:
        return "SiK telemetry radio", 0.6, [
            "SiK and DragonLink both use FSK at similar frequencies",
            "Discrimination requires packet-level decode (not implemented)",
        ]

    if dl_in_range and balance > 0.4:
        confidence = 0.3  # Low baseline — can't confirm without packet decode
        if balance > 0.7:
            confidence += 0.1
        dl_rate_min, dl_rate_max = DRAGONLINK_TYPICAL["data_rate_range"]
        if dl_rate_min <= rate <= dl_rate_max:
            confidence += 0.1

        caveats.append("DragonLink identification is tentative — "
                       "FSK parameters overlap with SiK and other ISM devices")
        caveats.append("No packet-level validation (proprietary framing)")
        return "DragonLink (tentative)", confidence, caveats

    return "Unknown FSK", 0.2, [
        "FSK signal detected but does not match known DragonLink or SiK profiles"
    ]


def detect_dragonlink(samples: np.ndarray, sample_rate: float,
                      center_freq: float, band: str = "auto") -> list[Detection]:
    """Run DragonLink detection pipeline on IQ samples."""
    detections = []

    # 1. PSD analysis
    freqs, psd_db = compute_psd(samples, sample_rate)
    noise_floor = estimate_noise_floor(psd_db)
    abs_freqs = freqs + center_freq

    print(f"[*] DragonLink scan: center={center_freq/1e6:.3f} MHz, "
          f"BW={sample_rate/1e6:.1f} MHz, noise floor={noise_floor:.1f} dBFS",
          file=sys.stderr)

    # 2. Find narrowband emissions
    peaks = find_spectral_peaks(freqs, psd_db, noise_floor,
                                min_snr_db=8.0,
                                min_distance_hz=20e3,
                                sample_rate=sample_rate)

    if not peaks:
        print("[*] No signals above noise floor", file=sys.stderr)
        return detections

    print(f"[*] Found {len(peaks)} spectral peaks", file=sys.stderr)

    # 3. FSK analysis on the full band
    fsk_signals = detect_fsk_signals(samples, sample_rate, center_freq)

    if not fsk_signals:
        # Still report strong narrowband signals as potential candidates
        for peak in peaks[:5]:
            freq = center_freq + peak["freq_hz"]
            det = Detection(
                protocol="Unknown narrowband",
                confidence=0.15,
                frequency_hz=freq,
                power_dbfs=peak["power_db"],
                bandwidth_hz=0,
                details={"snr_db": peak["snr_db"]},
                caveats=["Narrowband signal detected but no FSK modulation confirmed",
                         "Could be CW, OOK, or other modulation"],
            )
            detections.append(det)
        return detections

    # 4. Classify each FSK detection
    for fsk in fsk_signals:
        classification, confidence, caveats = classify_fsk_source(fsk)

        # Estimate occupied bandwidth
        occupied_bw = fsk["deviation_hz"] * 2 + fsk["est_data_rate_bps"]

        det = Detection(
            protocol=classification,
            confidence=confidence,
            frequency_hz=fsk["est_freq_hz"],
            power_dbfs=float(peaks[0]["power_db"]) if peaks else -100.0,
            bandwidth_hz=occupied_bw,
            details={
                "deviation_hz": round(fsk["deviation_hz"], 0),
                "est_data_rate_bps": round(fsk["est_data_rate_bps"], 0),
                "peak_balance": round(fsk["peak_balance"], 3),
                "modulation": "FSK",
            },
            caveats=caveats,
        )
        detections.append(det)

    return detections


# ---------------------------------------------------------------------------
# DJI OcuSync / Lightbridge OFDM Detector
# ---------------------------------------------------------------------------

# Known DJI signal parameters
DJI_PROFILES = {
    "Lightbridge 2": {
        "bandwidths_mhz": [10, 20],
        "bands_ghz": [2.4, 5.8],
        "ofdm_fft_sizes": [512, 1024],
    },
    "OcuSync 2.0": {
        "bandwidths_mhz": [10, 20, 40],
        "bands_ghz": [2.4, 5.8],
        "ofdm_fft_sizes": [256, 512, 1024],
    },
    "OcuSync 3.0": {
        "bandwidths_mhz": [20, 40],
        "bands_ghz": [2.4, 5.8],
        "ofdm_fft_sizes": [512, 1024, 2048],
    },
}

# DJI DroneID OUI (first 3 bytes of MAC)
DJI_OUIS = [
    bytes([0x60, 0x60, 0x1F]),  # DJI primary
    bytes([0x34, 0xD2, 0x62]),  # DJI secondary
]


def detect_ofdm_cyclic_prefix(samples: np.ndarray, sample_rate: float,
                              candidate_fft_sizes: list[int] | None = None,
                              cp_fractions: list[float] | None = None
                              ) -> list[dict]:
    """Detect OFDM signals via cyclic prefix autocorrelation.

    OFDM signals have a cyclic prefix (CP) — a copy of the end of each symbol
    prepended to the beginning. This creates a strong autocorrelation peak at
    the OFDM symbol duration (FFT size + CP length in samples).

    Returns list of detected OFDM parameter sets.
    """
    if candidate_fft_sizes is None:
        candidate_fft_sizes = [64, 128, 256, 512, 1024, 2048]
    if cp_fractions is None:
        cp_fractions = [1/4, 1/8, 1/16, 1/32]

    # Work on a manageable chunk
    chunk = samples[:min(len(samples), 1_000_000)]
    chunk_len = len(chunk)

    results = []

    for fft_size in candidate_fft_sizes:
        for cp_frac in cp_fractions:
            cp_len = int(fft_size * cp_frac)
            symbol_len = fft_size + cp_len

            if symbol_len > chunk_len // 4:
                continue

            # Autocorrelation at lag = fft_size
            # The CP is a copy of the last cp_len samples of the OFDM symbol,
            # so correlating samples offset by fft_size should show peaks
            num_corr = chunk_len - symbol_len
            if num_corr < symbol_len:
                continue

            # Sliding window correlation
            seg_a = chunk[:num_corr]
            seg_b = chunk[fft_size:fft_size + num_corr]
            # Normalize
            corr = np.abs(np.sum(
                (seg_a * np.conj(seg_b)).reshape(-1, symbol_len), axis=0
            )) if num_corr >= symbol_len else np.array([0.0])

            if len(corr) == 0:
                continue

            # For a valid OFDM signal, the correlation should peak in the
            # CP region (first cp_len samples of each symbol)
            # Compute ratio of CP-region correlation to non-CP region
            n_symbols = num_corr // symbol_len
            if n_symbols < 2:
                continue

            # Block-average correlation
            block_corr = np.zeros(symbol_len)
            for s in range(n_symbols):
                start = s * symbol_len
                end = start + symbol_len
                if end + fft_size > chunk_len:
                    break
                seg_a_block = chunk[start:end]
                seg_b_block = chunk[start + fft_size:end + fft_size]
                block_corr += np.abs(seg_a_block * np.conj(seg_b_block))

            block_corr /= max(n_symbols, 1)

            # CP region: first cp_len samples should have high correlation
            cp_region_power = np.mean(block_corr[:cp_len])
            non_cp_power = np.mean(block_corr[cp_len:])

            if non_cp_power > 0:
                cp_ratio = cp_region_power / non_cp_power
            else:
                cp_ratio = 0.0

            # A clear OFDM signal has cp_ratio >> 1
            if cp_ratio > 1.5:
                bw_hz = sample_rate  # OFDM occupies roughly the sample rate
                subcarrier_spacing = sample_rate / fft_size
                symbol_duration_us = symbol_len / sample_rate * 1e6

                results.append({
                    "fft_size": fft_size,
                    "cp_length": cp_len,
                    "cp_fraction": cp_frac,
                    "cp_ratio": float(cp_ratio),
                    "symbol_length_samples": symbol_len,
                    "symbol_duration_us": float(symbol_duration_us),
                    "subcarrier_spacing_hz": float(subcarrier_spacing),
                    "n_symbols_analyzed": n_symbols,
                })

    # Sort by CP ratio (strongest OFDM evidence first)
    results.sort(key=lambda r: r["cp_ratio"], reverse=True)
    return results


def estimate_ofdm_bandwidth(samples: np.ndarray, sample_rate: float,
                            center_freq: float) -> dict:
    """Estimate the occupied bandwidth of an OFDM-like signal.

    Looks for the flat-top spectral shape characteristic of OFDM:
    relatively uniform power across occupied bandwidth with sharp rolloff.
    """
    freqs, psd_db = compute_psd(samples, sample_rate, nfft=4096)
    noise_floor = estimate_noise_floor(psd_db)

    # Signal region: where power exceeds noise + margin
    signal_mask = psd_db > (noise_floor + 3.0)

    if not np.any(signal_mask):
        return {"bandwidth_hz": 0, "flatness_db": 0, "noise_floor_db": noise_floor}

    signal_freqs = freqs[signal_mask]
    signal_psd = psd_db[signal_mask]

    # Occupied bandwidth
    if len(signal_freqs) > 0:
        bw = float(signal_freqs[-1] - signal_freqs[0])
    else:
        bw = 0.0

    # Flatness: std deviation of signal region (OFDM is very flat)
    flatness = float(np.std(signal_psd)) if len(signal_psd) > 1 else 999.0

    # Edge sharpness: derivative at bandwidth edges
    edge_sharpness = 0.0
    if len(psd_db) > 10:
        gradient = np.gradient(psd_db)
        # Look for steep negative gradient at upper edge, positive at lower
        edge_sharpness = float(np.max(np.abs(gradient)))

    return {
        "bandwidth_hz": bw,
        "flatness_db": flatness,
        "noise_floor_db": float(noise_floor),
        "edge_sharpness": edge_sharpness,
        "signal_mean_db": float(np.mean(signal_psd)) if len(signal_psd) > 0 else noise_floor,
    }


def classify_dji_variant(bw_info: dict, ofdm_params: list[dict],
                         center_freq: float) -> tuple[str, float, dict, list[str]]:
    """Classify a detected OFDM signal as a DJI protocol variant.

    Returns (variant_name, confidence, details, caveats).
    """
    bw_mhz = bw_info["bandwidth_hz"] / 1e6
    flatness = bw_info["flatness_db"]
    caveats = []
    details = {}

    # Determine frequency band
    band_ghz = center_freq / 1e9
    if 2.3 < band_ghz < 2.5:
        band = "2.4 GHz"
    elif 5.7 < band_ghz < 5.9:
        band = "5.8 GHz"
    else:
        band = f"{band_ghz:.2f} GHz"
        caveats.append(f"Unusual frequency band ({band}) — not standard DJI")

    details["band"] = band
    details["measured_bw_mhz"] = round(bw_mhz, 1)
    details["spectral_flatness_db"] = round(flatness, 2)

    if ofdm_params:
        best = ofdm_params[0]
        details["ofdm_fft_size"] = best["fft_size"]
        details["ofdm_cp_fraction"] = best["cp_fraction"]
        details["ofdm_cp_ratio"] = round(best["cp_ratio"], 2)
        details["subcarrier_spacing_hz"] = round(best["subcarrier_spacing_hz"], 1)

    # Classification by bandwidth
    confidence = 0.0
    variant = "Unknown OFDM"

    # Is the spectrum flat enough for OFDM?
    is_flat = flatness < 6.0  # OFDM typically < 4 dB variation
    has_cp = len(ofdm_params) > 0 and ofdm_params[0]["cp_ratio"] > 2.0

    if not is_flat and not has_cp:
        caveats.append("Signal lacks OFDM characteristics (not flat, no CP detected)")
        return "Non-OFDM signal", 0.1, details, caveats

    if 8 < bw_mhz < 12:
        variant = "Lightbridge 2 (10 MHz)"
        confidence = 0.45
    elif 18 < bw_mhz < 22:
        # Both Lightbridge 2 and OcuSync use 20 MHz
        if has_cp and ofdm_params[0]["fft_size"] >= 1024:
            variant = "OcuSync 2.0/3.0 (20 MHz)"
            confidence = 0.40
        else:
            variant = "DJI 20 MHz (Lightbridge 2 or OcuSync)"
            confidence = 0.35
    elif 35 < bw_mhz < 45:
        variant = "OcuSync 3.0 (40 MHz)"
        confidence = 0.50
    elif bw_mhz > 3:
        variant = f"Possible DJI ({bw_mhz:.0f} MHz)"
        confidence = 0.20

    # Boost confidence for strong OFDM CP evidence
    if has_cp:
        confidence = min(confidence + 0.15, 0.75)

    # Boost for spectral flatness
    if is_flat:
        confidence = min(confidence + 0.05, 0.75)

    caveats.append("DJI uses proprietary OFDM — variant identification "
                   "is by bandwidth/FFT size heuristics only")
    caveats.append("WiFi (802.11n/ac/ax) also uses OFDM at these frequencies "
                   "and CANNOT be distinguished without MAC-layer decode")
    caveats.append("No packet-level validation possible (encrypted, proprietary framing)")

    return variant, confidence, details, caveats


def detect_dji_droneid_beacon(samples: np.ndarray, sample_rate: float,
                              center_freq: float) -> Optional[dict]:
    """Attempt to detect DJI DroneID beacon frames.

    DJI drones broadcast DroneID as WiFi-like management frames with
    DJI's OUI. This requires WiFi-rate sampling (≥20 Msps) and is
    essentially a specialized WiFi frame detector.

    This is a VERY simplified detector — it looks for WiFi preamble
    energy patterns but cannot do full 802.11 demodulation.
    """
    if sample_rate < 15e6:
        return None  # Need at least ~15 Msps for WiFi-width signals

    # WiFi uses OFDM with 64 subcarriers, 20 MHz bandwidth
    # Look for short training field (STF) — repeated pattern at 0.8μs period
    stf_period_samples = int(0.8e-6 * sample_rate)
    if stf_period_samples < 4:
        return None

    # Autocorrelation at STF period
    chunk = samples[:min(len(samples), 500_000)]
    if len(chunk) < stf_period_samples * 20:
        return None

    corr_len = len(chunk) - stf_period_samples
    seg_a = chunk[:corr_len]
    seg_b = chunk[stf_period_samples:stf_period_samples + corr_len]

    # Sliding-window normalized correlation
    window = min(stf_period_samples * 10, corr_len // 2)
    if window < stf_period_samples:
        return None

    # Compute in blocks to manage memory
    n_blocks = corr_len // window
    max_corr = 0.0
    for i in range(n_blocks):
        s = i * window
        e = s + window
        if e > corr_len:
            break
        block_corr = np.abs(np.sum(seg_a[s:e] * np.conj(seg_b[s:e])))
        block_power = np.sqrt(np.sum(np.abs(seg_a[s:e])**2) *
                              np.sum(np.abs(seg_b[s:e])**2))
        if block_power > 0:
            norm_corr = block_corr / block_power
            if norm_corr > max_corr:
                max_corr = norm_corr

    if max_corr > 0.7:
        return {
            "wifi_preamble_detected": True,
            "correlation_strength": float(max_corr),
            "caveat": "WiFi preamble energy detected — could be DroneID, "
                      "normal WiFi, or any 802.11 transmission. "
                      "DJI OUI check requires full MAC decode (not implemented).",
        }

    return None


def detect_dji(samples: np.ndarray, sample_rate: float,
               center_freq: float) -> list[Detection]:
    """Run DJI OcuSync/Lightbridge detection pipeline."""
    detections = []

    print(f"[*] DJI scan: center={center_freq/1e6:.3f} MHz, "
          f"BW={sample_rate/1e6:.1f} MHz", file=sys.stderr)

    # 1. Bandwidth estimation
    bw_info = estimate_ofdm_bandwidth(samples, sample_rate, center_freq)
    print(f"[*] Occupied BW: {bw_info['bandwidth_hz']/1e6:.1f} MHz, "
          f"flatness: {bw_info['flatness_db']:.1f} dB", file=sys.stderr)

    # 2. OFDM cyclic prefix detection
    ofdm_results = detect_ofdm_cyclic_prefix(samples, sample_rate)
    if ofdm_results:
        print(f"[*] OFDM CP detected: FFT={ofdm_results[0]['fft_size']}, "
              f"CP ratio={ofdm_results[0]['cp_ratio']:.2f}", file=sys.stderr)
    else:
        print("[*] No OFDM cyclic prefix detected", file=sys.stderr)

    # 3. Classify
    variant, confidence, details, caveats = classify_dji_variant(
        bw_info, ofdm_results, center_freq
    )

    if bw_info["bandwidth_hz"] > 1e6:  # At least 1 MHz of signal present
        det = Detection(
            protocol=f"DJI {variant}",
            confidence=confidence,
            frequency_hz=center_freq,
            power_dbfs=bw_info["signal_mean_db"],
            bandwidth_hz=bw_info["bandwidth_hz"],
            details=details,
            caveats=caveats,
        )
        detections.append(det)

    # 4. DroneID beacon check (if bandwidth permits)
    droneid = detect_dji_droneid_beacon(samples, sample_rate, center_freq)
    if droneid:
        det = Detection(
            protocol="DJI DroneID (WiFi beacon)",
            confidence=0.25,
            frequency_hz=center_freq,
            power_dbfs=bw_info.get("signal_mean_db", -100),
            bandwidth_hz=20e6,
            details=droneid,
            caveats=[
                "WiFi preamble detected — MAY be DroneID or regular WiFi",
                "DJI OUI confirmation requires full 802.11 MAC decode",
                "DroneID uses standard WiFi framing with vendor-specific IEs",
            ],
        )
        detections.append(det)

    return detections


# ---------------------------------------------------------------------------
# Scan mode — sweep and detect everything
# ---------------------------------------------------------------------------

SCAN_BANDS = {
    "433": {
        "label": "433 MHz ISM (EU)",
        "center_hz": 433.0e6,
        "sample_rate": 2e6,
        "protocols": ["DragonLink", "SiK telemetry"],
    },
    "900": {
        "label": "900 MHz ISM (US)",
        "center_hz": 915.0e6,
        "sample_rate": 4e6,
        "protocols": ["DragonLink", "SiK telemetry", "LoRa telemetry"],
    },
    "2.4ghz": {
        "label": "2.4 GHz ISM",
        "center_hz": 2.44e9,
        "sample_rate": 20e6,
        "protocols": ["DJI OcuSync", "DJI Lightbridge", "WiFi", "ExpressLRS",
                       "FrSky", "Spektrum DSMX", "Flysky"],
    },
    "5.8ghz": {
        "label": "5.8 GHz ISM",
        "center_hz": 5.8e9,
        "sample_rate": 20e6,
        "protocols": ["DJI OcuSync", "DJI Lightbridge", "Analog FPV video",
                       "WiFi 5 GHz"],
    },
}


def scan_band(samples: np.ndarray, sample_rate: float,
              center_freq: float, band_key: str) -> list[Detection]:
    """Run all applicable detectors on a band."""
    all_detections = []
    band_info = SCAN_BANDS.get(band_key, {})

    # Determine which detectors to run
    if band_key in ("433", "900"):
        all_detections.extend(
            detect_dragonlink(samples, sample_rate, center_freq, band_key))
    elif band_key in ("2.4ghz", "5.8ghz"):
        all_detections.extend(
            detect_dji(samples, sample_rate, center_freq))

    # Always do a generic spectral survey
    freqs, psd_db = compute_psd(samples, sample_rate)
    noise_floor = estimate_noise_floor(psd_db)
    peaks = find_spectral_peaks(freqs, psd_db, noise_floor,
                                min_snr_db=10.0,
                                min_distance_hz=50e3,
                                sample_rate=sample_rate)

    if peaks and not all_detections:
        for peak in peaks[:3]:
            freq = center_freq + peak["freq_hz"]
            det = Detection(
                protocol="Unidentified signal",
                confidence=0.1,
                frequency_hz=freq,
                power_dbfs=peak["power_db"],
                bandwidth_hz=0,
                details={"snr_db": peak["snr_db"]},
                caveats=["Signal detected but not matched to a known drone protocol"],
            )
            all_detections.append(det)

    return all_detections


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_common_args(parser: argparse.ArgumentParser):
    """Add IQ file and common arguments."""
    input_group = parser.add_argument_group("Input")
    input_group.add_argument("--iq-file", type=str, default=None,
                             help="Path to raw IQ file")
    input_group.add_argument("--iq-format", type=str, default="auto",
                             choices=["auto", "cf32", "cs16", "cu8", "cs8"],
                             help="IQ sample format (default: auto-detect)")
    input_group.add_argument("--sample-rate", type=float, required=True,
                             help="Sample rate in Hz (e.g. 2e6)")
    input_group.add_argument("--center-freq", type=float, default=None,
                             help="Center frequency in Hz (e.g. 433e6)")
    input_group.add_argument("--max-samples", type=int, default=0,
                             help="Max samples to load (0 = all)")

    output_group = parser.add_argument_group("Output")
    output_group.add_argument("--json", action="store_true",
                              help="Output results as JSON")
    output_group.add_argument("--output", "-o", type=str, default=None,
                              help="Write JSON results to file")
    output_group.add_argument("--min-confidence", type=float, default=0.0,
                              help="Minimum confidence to report (0.0–1.0)")


def get_samples(args: argparse.Namespace) -> np.ndarray:
    """Load samples from IQ file or live SDR."""
    if args.iq_file:
        return load_iq_file(args.iq_file, args.sample_rate,
                            fmt=args.iq_format,
                            max_samples=args.max_samples)
    elif HAS_SDR:
        sdr = SDRDevice.from_args(args)
        freq = args.center_freq
        if freq is None:
            print("[!] --center-freq is required for live SDR", file=sys.stderr)
            sys.exit(1)
        sdr.setup_rx(freq=freq, rate=args.sample_rate,
                     gain=getattr(args, "sdr_gain", None) or 40.0)
        n = args.max_samples if args.max_samples > 0 else int(args.sample_rate * 2)
        print(f"[*] Capturing {n} samples ({n/args.sample_rate:.2f}s)...",
              file=sys.stderr)
        samples = sdr.receive(num_samples=n)
        sdr.close()
        return samples
    else:
        print("[!] No input specified. Use --iq-file or install SoapySDR "
              "for live capture.", file=sys.stderr)
        sys.exit(1)


def print_results(detections: list[Detection], as_json: bool = False,
                  output_path: str | None = None,
                  min_confidence: float = 0.0):
    """Display detection results."""
    filtered = [d for d in detections if d.confidence >= min_confidence]
    filtered.sort(key=lambda d: d.confidence, reverse=True)

    if as_json or output_path:
        results = {
            "detections": [d.to_dict() for d in filtered],
            "count": len(filtered),
            "note": "DETECTION ONLY — no packet decode. "
                    "Confidence values are heuristic estimates. "
                    "Validate against known-active transmissions.",
        }
        json_str = json.dumps(results, indent=2)
        if output_path:
            Path(output_path).write_text(json_str)
            print(f"[*] Results written to {output_path}", file=sys.stderr)
        if as_json:
            print(json_str)
        return

    # Human-readable output
    if not filtered:
        print("\n  No drone protocols detected above confidence threshold.\n")
        return

    print(f"\n{'='*70}")
    print(f"  DETECTION RESULTS — {len(filtered)} signal(s) found")
    print(f"{'='*70}\n")

    for i, det in enumerate(filtered, 1):
        conf_bar = '#' * int(det.confidence * 20) + '.' * (20 - int(det.confidence * 20))
        print(f"  [{i}] {det.protocol}")
        print(f"      Frequency:  {det.frequency_hz/1e6:.4f} MHz")
        print(f"      Power:      {det.power_dbfs:.1f} dBFS")
        print(f"      Bandwidth:  {det.bandwidth_hz/1e3:.1f} kHz")
        print(f"      Confidence: [{conf_bar}] {det.confidence*100:.0f}%")
        if det.details:
            for k, v in det.details.items():
                print(f"      {k}: {v}")
        if det.caveats:
            print(f"      ⚠ Caveats:")
            for c in det.caveats:
                print(f"        - {c}")
        print()

    print(f"{'='*70}")
    print("  ⚠ DETECTION ONLY — no packet-level decode.")
    print("  All identifications are heuristic; validate with known transmitters.")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Proprietary RF Protocol Detector — "
                    "DragonLink & DJI OcuSync/Lightbridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Detection mode")

    # DragonLink subcommand
    dl_parser = subparsers.add_parser(
        "dragonlink", help="Detect DragonLink FSK signals (433/900 MHz)")
    add_common_args(dl_parser)
    dl_parser.add_argument("--band", choices=["433", "900", "auto"],
                           default="auto",
                           help="DragonLink frequency band (default: auto)")
    if HAS_SDR:
        add_sdr_args(dl_parser)

    # DJI subcommand
    dji_parser = subparsers.add_parser(
        "dji", help="Detect DJI OcuSync/Lightbridge OFDM signals (2.4/5.8 GHz)")
    add_common_args(dji_parser)
    if HAS_SDR:
        add_sdr_args(dji_parser)

    # Scan subcommand
    scan_parser = subparsers.add_parser(
        "scan", help="Broadband scan for all drone protocols")
    add_common_args(scan_parser)
    scan_parser.add_argument(
        "--band", choices=list(SCAN_BANDS.keys()),
        default=None,
        help="Specific band to scan (default: infer from center-freq)")
    if HAS_SDR:
        add_sdr_args(scan_parser)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    samples = get_samples(args)
    center_freq = args.center_freq or 0.0

    if args.command == "dragonlink":
        band = args.band
        if band == "auto":
            if center_freq < 500e6:
                band = "433"
            else:
                band = "900"
        detections = detect_dragonlink(samples, args.sample_rate,
                                       center_freq, band)

    elif args.command == "dji":
        detections = detect_dji(samples, args.sample_rate, center_freq)

    elif args.command == "scan":
        band = args.band
        if band is None:
            # Infer from center frequency
            if center_freq < 500e6:
                band = "433"
            elif center_freq < 1e9:
                band = "900"
            elif center_freq < 3e9:
                band = "2.4ghz"
            else:
                band = "5.8ghz"
        detections = scan_band(samples, args.sample_rate, center_freq, band)

    else:
        parser.print_help()
        sys.exit(1)

    print_results(detections, as_json=args.json, output_path=args.output,
                  min_confidence=args.min_confidence)


if __name__ == "__main__":
    main()
