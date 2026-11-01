#!/usr/bin/env python3
"""
Analog FPV Video Signal Detector & Channel Scanner

Detects and characterizes analog FPV video transmitters (VTX) in the 5.8 GHz
band by scanning known FPV channel frequencies for wideband FM carriers.

Capabilities:
  - Power-spectral-density sweep across all 48 standard 5.8 GHz FPV channels
  - Active-channel detection against an adaptive noise floor
  - Band/channel identification (A, B, E, F, Raceband, Low)
  - Relative signal-strength estimation per detected VTX
  - Single-channel FM demodulation to extract raw baseband (validates signal
    is analog FPV video by spectral shape — not full NTSC/PAL frame decode)
  - JSON output for dashboard integration

Input:
  - Live SDR via sdr_common (SoapySDR — any supported hardware)
  - Raw IQ file (complex64 or signed-int16 interleaved)

SDR hardware notes:
  Most project SDRs cover 5.8 GHz but with caveats:
    HackRF One   — tunes to 6 GHz, 20 MHz BW, but reduced sensitivity above
                    5.5 GHz (~10-15 dB NF increase); usable for nearby VTX
    USRP B210    — covers to 6 GHz natively, best option
    RTL-SDR v3   — tops out at ~1.766 GHz; CANNOT reach 5.8 GHz
    RTL-SDR v4   — uses R828D, tunes ~24 MHz-1.766 GHz; also NO 5.8 GHz
    LimeSDR      — covers to 3.8 GHz; NO 5.8 GHz
    BladeRF 2.0  — covers to 6 GHz; usable

  TL;DR: HackRF, USRP B2x0, and BladeRF 2.0 can scan 5.8 GHz; others cannot.
  Sensitivity at 5.8 GHz is lower than at 2.4 GHz — detection range is
  reduced, especially for 25 mW VTX.  Expect reliable detection within
  ~10-50 m for HackRF, further for USRP.

REAL-CAPTURE GAP:
  No 5.8 GHz IQ capture currently exists in this project. To record one:

    # HackRF — sweep entire 5.8 GHz FPV range, 20 MHz BW per step:
    hackrf_transfer -r fpv_5800mhz.iq -f 5800000000 -s 20000000 \\
        -a 1 -l 40 -g 40 -n 40000000

    # SoapySDR via sdr_common (any supported device):
    python3 -c "
    from sdr_common import SDRDevice
    import numpy as np
    sdr = SDRDevice()
    sdr.setup_rx(freq=5.8e9, rate=20e6, gain=40)
    iq = sdr.receive(num_samples=20_000_000)  # 1 second
    iq.tofile('fpv_5800mhz_cf32.iq')
    sdr.close()
    "

  Validate with: ls -la fpv_5800mhz*.iq  (expect ~80-160 MB for 1 sec)

Usage:
    # Sweep all 5.8 GHz FPV channels from a live SDR
    python3 analog_fpv_scanner.py --mode sweep

    # Sweep from an IQ file captured at 5.8 GHz center, 20 Msps
    python3 analog_fpv_scanner.py --mode sweep \\
        --iq-file fpv_5800mhz_cf32.iq --sample-rate 20e6 --center-freq 5.8e9

    # Monitor a single channel (e.g., Raceband 1)
    python3 analog_fpv_scanner.py --mode monitor --channel R1

    # FM-demodulate a detected channel and dump baseband
    python3 analog_fpv_scanner.py --mode demod --channel F4 \\
        --iq-file fpv_5800mhz_cf32.iq --sample-rate 20e6 --center-freq 5.8e9 \\
        --output-baseband demod_F4.raw

    # JSON output for dashboard
    python3 analog_fpv_scanner.py --mode sweep --json

Requires: numpy, scipy. SoapySDR for live capture (via sdr_common).
For authorized CTF research only.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.signal import welch, butter, sosfilt, resample_poly
from scipy.fft import fft, fftfreq


# ---------------------------------------------------------------------------
# 5.8 GHz FPV channel table — all standard bands
# ---------------------------------------------------------------------------

# fmt: off
FPV_CHANNELS: dict[str, list[tuple[str, int]]] = {
    "A": [  # Boscam A
        ("A1", 5865_000_000), ("A2", 5845_000_000), ("A3", 5825_000_000),
        ("A4", 5805_000_000), ("A5", 5785_000_000), ("A6", 5765_000_000),
        ("A7", 5745_000_000), ("A8", 5725_000_000),
    ],
    "B": [  # Boscam B
        ("B1", 5733_000_000), ("B2", 5752_000_000), ("B3", 5771_000_000),
        ("B4", 5790_000_000), ("B5", 5809_000_000), ("B6", 5828_000_000),
        ("B7", 5847_000_000), ("B8", 5866_000_000),
    ],
    "E": [  # DJI / Lumenier
        ("E1", 5705_000_000), ("E2", 5685_000_000), ("E3", 5665_000_000),
        ("E4", 5645_000_000), ("E5", 5885_000_000), ("E6", 5905_000_000),
        ("E7", 5925_000_000), ("E8", 5945_000_000),
    ],
    "F": [  # FatShark / NexWave / ImmersionRC
        ("F1", 5740_000_000), ("F2", 5760_000_000), ("F3", 5780_000_000),
        ("F4", 5800_000_000), ("F5", 5820_000_000), ("F6", 5840_000_000),
        ("F7", 5860_000_000), ("F8", 5880_000_000),
    ],
    "R": [  # Raceband
        ("R1", 5658_000_000), ("R2", 5695_000_000), ("R3", 5732_000_000),
        ("R4", 5769_000_000), ("R5", 5806_000_000), ("R6", 5843_000_000),
        ("R7", 5880_000_000), ("R8", 5917_000_000),
    ],
    "L": [  # Low band
        ("L1", 5362_000_000), ("L2", 5399_000_000), ("L3", 5436_000_000),
        ("L4", 5473_000_000), ("L5", 5510_000_000), ("L6", 5547_000_000),
        ("L7", 5584_000_000), ("L8", 5621_000_000),
    ],
}
# fmt: on

# Flatten for lookup
ALL_CHANNELS: dict[str, int] = {}
for _band_channels in FPV_CHANNELS.values():
    for _name, _freq in _band_channels:
        ALL_CHANNELS[_name] = _freq

AUDIO_SUBCARRIER_OFFSET_HZ = 6_500_000  # 6.5 MHz above video carrier
FPV_CHANNEL_BW_HZ = 18_000_000  # ~18 MHz occupied bandwidth per channel


# ---------------------------------------------------------------------------
# IQ file reader
# ---------------------------------------------------------------------------

def load_iq_file(
    path: str,
    sample_rate: float,
    center_freq: float,
    fmt: str = "cf32",
    max_samples: int = 0,
) -> tuple[np.ndarray, float, float]:
    """Load IQ samples from a raw file.

    Supported formats:
      cf32 — interleaved float32 (numpy complex64)
      cs16 — interleaved signed int16 (hackrf_transfer default)

    Returns (samples, sample_rate, center_freq).
    """
    p = Path(path)
    if not p.exists():
        print(f"[!] IQ file not found: {path}", file=sys.stderr)
        sys.exit(1)

    file_size = p.stat().st_size

    if fmt == "cf32":
        bytes_per_sample = 8  # 2 × float32
        dtype = np.complex64
        raw = np.fromfile(str(p), dtype=dtype)
    elif fmt == "cs16":
        bytes_per_sample = 4  # 2 × int16
        raw_int = np.fromfile(str(p), dtype=np.int16)
        raw = (raw_int[0::2] + 1j * raw_int[1::2]).astype(np.complex64)
        raw /= 32768.0  # normalize to [-1, 1]
    else:
        print(f"[!] Unknown IQ format: {fmt}", file=sys.stderr)
        sys.exit(1)

    total_samples = len(raw)
    if max_samples > 0 and total_samples > max_samples:
        raw = raw[:max_samples]

    duration = len(raw) / sample_rate
    print(
        f"[*] Loaded {len(raw):,} samples from {p.name} "
        f"({duration:.3f}s at {sample_rate/1e6:.1f} Msps, "
        f"center={center_freq/1e6:.1f} MHz, fmt={fmt})",
        file=sys.stderr,
    )
    return raw, sample_rate, center_freq


# ---------------------------------------------------------------------------
# Core DSP — power spectral density and channel power measurement
# ---------------------------------------------------------------------------

def measure_channel_power(
    iq: np.ndarray,
    sample_rate: float,
    center_freq: float,
    channel_freq: float,
    channel_bw: float = FPV_CHANNEL_BW_HZ,
    nfft: int = 4096,
) -> tuple[float, float]:
    """Measure power in a frequency band around channel_freq.

    Returns (power_dBFS, peak_freq_Hz).
    power_dBFS is the average power in the channel bandwidth relative to
    full-scale, in dB.  peak_freq_Hz is the frequency of the spectral peak
    within the channel — useful for precise carrier measurement.

    If the channel is outside the captured bandwidth, returns (-inf, 0).
    """
    half_bw = sample_rate / 2.0
    freq_offset = channel_freq - center_freq

    # Check if channel falls within our captured bandwidth
    if abs(freq_offset) > half_bw - channel_bw / 2:
        return (float("-inf"), 0.0)

    # Compute PSD via Welch's method
    freqs, psd = welch(
        iq, fs=sample_rate, nperseg=min(nfft, len(iq)),
        noverlap=nfft // 2, return_onesided=False, detrend=False,
    )

    # Shift so freqs are relative to center_freq
    # welch returns freqs in [-fs/2, fs/2] order after fftshift
    # but scipy.signal.welch with return_onesided=False gives [0..fs/2, -fs/2..0)
    freqs = np.fft.fftshift(freqs)
    psd = np.fft.fftshift(psd)

    # Convert to absolute frequency
    abs_freqs = freqs + center_freq

    # Select bins within channel bandwidth
    mask = np.abs(abs_freqs - channel_freq) <= channel_bw / 2
    if not np.any(mask):
        return (float("-inf"), 0.0)

    channel_psd = psd[mask]
    channel_freqs = abs_freqs[mask]

    # Average power in channel (integrate PSD × bin width)
    freq_resolution = sample_rate / len(freqs)
    avg_power = np.mean(channel_psd)

    # Convert to dBFS (reference: full-scale = 1.0 for normalized IQ)
    if avg_power > 0:
        power_dbfs = 10.0 * np.log10(avg_power + 1e-30)
    else:
        power_dbfs = -120.0

    # Peak frequency within channel
    peak_idx = np.argmax(channel_psd)
    peak_freq = channel_freqs[peak_idx]

    return (power_dbfs, peak_freq)


def estimate_noise_floor(
    iq: np.ndarray,
    sample_rate: float,
    nfft: int = 4096,
) -> float:
    """Estimate noise floor as the median PSD value (dBFS).

    The median is robust against narrowband signals — it converges to the
    thermal noise level even when several channels are occupied.
    """
    _, psd = welch(
        iq, fs=sample_rate, nperseg=min(nfft, len(iq)),
        noverlap=nfft // 2, return_onesided=False, detrend=False,
    )
    median_power = np.median(psd)
    if median_power > 0:
        return 10.0 * np.log10(median_power + 1e-30)
    return -120.0


# ---------------------------------------------------------------------------
# Sweep scanner — measures all FPV channels in one pass
# ---------------------------------------------------------------------------

def scan_channels(
    iq: np.ndarray,
    sample_rate: float,
    center_freq: float,
    detection_threshold_db: float = 10.0,
    nfft: int = 4096,
) -> dict:
    """Scan all FPV channels and return detection results.

    Parameters
    ----------
    iq : complex64 array of IQ samples
    sample_rate : sample rate in Hz
    center_freq : center frequency of the capture in Hz
    detection_threshold_db : dB above noise floor to declare a detection
    nfft : FFT size for PSD estimation

    Returns
    -------
    dict with keys:
      noise_floor_dbfs : float — estimated noise floor
      channels_scanned : int — how many channels fell within capture bandwidth
      channels_detected : int — how many exceeded the detection threshold
      detections : list of dicts, each with:
        channel, band, freq_mhz, power_dbfs, snr_db, peak_freq_mhz
      out_of_band : list of channel names outside capture bandwidth
    """
    noise_floor = estimate_noise_floor(iq, sample_rate, nfft)

    detections = []
    out_of_band = []
    channels_scanned = 0

    for band_name, band_channels in FPV_CHANNELS.items():
        for ch_name, ch_freq in band_channels:
            power_dbfs, peak_freq = measure_channel_power(
                iq, sample_rate, center_freq, ch_freq, nfft=nfft,
            )
            if power_dbfs == float("-inf"):
                out_of_band.append(ch_name)
                continue

            channels_scanned += 1
            snr = power_dbfs - noise_floor

            if snr >= detection_threshold_db:
                detections.append({
                    "channel": ch_name,
                    "band": band_name,
                    "freq_mhz": ch_freq / 1e6,
                    "power_dbfs": round(power_dbfs, 1),
                    "snr_db": round(snr, 1),
                    "peak_freq_mhz": round(peak_freq / 1e6, 3),
                })

    # Sort by power (strongest first)
    detections.sort(key=lambda d: d["power_dbfs"], reverse=True)

    return {
        "noise_floor_dbfs": round(noise_floor, 1),
        "channels_scanned": channels_scanned,
        "channels_detected": len(detections),
        "detections": detections,
        "out_of_band": out_of_band,
    }


# ---------------------------------------------------------------------------
# FM demodulator — extract raw baseband from a single FPV channel
# ---------------------------------------------------------------------------

def extract_channel(
    iq: np.ndarray,
    sample_rate: float,
    center_freq: float,
    channel_freq: float,
    channel_bw: float = FPV_CHANNEL_BW_HZ,
) -> tuple[np.ndarray, float]:
    """Frequency-shift and filter to isolate one FPV channel.

    Returns (channel_iq, channel_sample_rate).
    The output is decimated to ~2× the channel bandwidth.
    """
    freq_offset = channel_freq - center_freq

    # Frequency shift to baseband
    n = np.arange(len(iq))
    shift = np.exp(-1j * 2 * np.pi * freq_offset / sample_rate * n).astype(
        np.complex64
    )
    shifted = iq * shift

    # Low-pass filter at channel_bw/2
    # Target output rate: 2× channel bandwidth
    target_rate = channel_bw * 2
    decimation = max(1, int(sample_rate / target_rate))
    output_rate = sample_rate / decimation

    # Butterworth LPF
    nyq = sample_rate / 2.0
    cutoff = channel_bw / 2.0
    if cutoff / nyq >= 1.0:
        cutoff = nyq * 0.95
    sos = butter(5, cutoff / nyq, btype="low", output="sos")
    filtered = sosfilt(sos, shifted)

    # Decimate
    if decimation > 1:
        filtered = filtered[::decimation]

    return filtered, output_rate


def fm_demodulate(iq: np.ndarray) -> np.ndarray:
    """Wideband FM demodulation via instantaneous frequency.

    Computes the phase difference between consecutive samples, which for
    an FM signal is proportional to the instantaneous frequency deviation
    and thus the modulating signal (composite video + audio subcarrier).

    Returns a real-valued array of the demodulated baseband.
    """
    # Differential phase: angle(x[n] * conj(x[n-1]))
    product = iq[1:] * np.conj(iq[:-1])
    demod = np.angle(product)
    return demod


def characterize_baseband(
    baseband: np.ndarray,
    sample_rate: float,
) -> dict:
    """Analyze demodulated baseband for analog FPV characteristics.

    Looks for:
    - 15.734 kHz (NTSC) or 15.625 kHz (PAL) horizontal sync
    - 6.5 MHz audio subcarrier
    - Overall spectral shape consistent with composite video
    """
    nfft = min(65536, len(baseband))
    freqs, psd = welch(
        baseband, fs=sample_rate, nperseg=nfft,
        noverlap=nfft // 2, return_onesided=True,
    )

    # Look for horizontal sync lines
    ntsc_sync_hz = 15_734.0
    pal_sync_hz = 15_625.0

    def find_peak_near(target_hz: float, tolerance_hz: float = 200.0) -> float:
        mask = np.abs(freqs - target_hz) <= tolerance_hz
        if not np.any(mask):
            return -120.0
        return float(10.0 * np.log10(np.max(psd[mask]) + 1e-30))

    # Noise reference: median PSD
    noise_ref = 10.0 * np.log10(np.median(psd) + 1e-30)

    ntsc_peak = find_peak_near(ntsc_sync_hz) - noise_ref
    pal_peak = find_peak_near(pal_sync_hz) - noise_ref

    # Audio subcarrier at 6.5 MHz
    audio_peak = find_peak_near(AUDIO_SUBCARRIER_OFFSET_HZ, 200_000.0) - noise_ref

    # Determine video standard
    video_standard = "unknown"
    if ntsc_peak > 10.0 and ntsc_peak > pal_peak:
        video_standard = "NTSC"
    elif pal_peak > 10.0:
        video_standard = "PAL"

    return {
        "video_standard": video_standard,
        "ntsc_sync_snr_db": round(float(ntsc_peak), 1),
        "pal_sync_snr_db": round(float(pal_peak), 1),
        "audio_subcarrier_snr_db": round(float(audio_peak), 1),
        "has_audio_subcarrier": bool(audio_peak > 6.0),
        "baseband_sample_rate": float(sample_rate),
        "baseband_duration_s": round(len(baseband) / sample_rate, 3),
    }


# ---------------------------------------------------------------------------
# Live SDR sweep — tunes across the 5.8 GHz band
# ---------------------------------------------------------------------------

def live_sweep(
    sdr_device_args: str = "",
    gain: float = 40.0,
    dwell_time_s: float = 0.1,
    step_bw: float = 20e6,
    detection_threshold_db: float = 10.0,
) -> dict:
    """Sweep the 5.8 GHz FPV band using a live SDR.

    Tunes across the band in steps of step_bw, collecting dwell_time_s
    of samples at each step, then scans all channels that fall within
    each step's bandwidth.

    Returns the same dict format as scan_channels().
    """
    try:
        from sdr_common import SDRDevice
    except ImportError:
        print(
            "[!] sdr_common not found. Run from the tools/ directory or "
            "add it to PYTHONPATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Frequency plan: cover 5.3 GHz to 6.0 GHz in steps
    freq_start = 5_300_000_000
    freq_stop = 6_000_000_000
    step_centers = []
    f = freq_start + step_bw / 2
    while f < freq_stop:
        step_centers.append(f)
        f += step_bw * 0.8  # 20% overlap between steps

    all_detections = []
    all_out_of_band = set(ALL_CHANNELS.keys())
    overall_noise_floors = []
    channels_scanned_total = set()

    sdr = SDRDevice(device_args=sdr_device_args)

    print(
        f"[*] Sweeping {freq_start/1e6:.0f}–{freq_stop/1e6:.0f} MHz "
        f"in {len(step_centers)} steps ({step_bw/1e6:.0f} MHz each, "
        f"{dwell_time_s*1000:.0f} ms dwell)",
        file=sys.stderr,
    )

    try:
        for i, fc in enumerate(step_centers):
            sdr.setup_rx(freq=fc, rate=step_bw, gain=gain)
            num_samples = int(dwell_time_s * step_bw)
            iq = sdr.receive(num_samples=num_samples)

            if len(iq) < 1024:
                print(
                    f"  [{i+1}/{len(step_centers)}] {fc/1e6:.0f} MHz — "
                    f"insufficient samples ({len(iq)}), skipping",
                    file=sys.stderr,
                )
                continue

            result = scan_channels(
                iq, step_bw, fc,
                detection_threshold_db=detection_threshold_db,
            )
            overall_noise_floors.append(result["noise_floor_dbfs"])

            for det in result["detections"]:
                ch = det["channel"]
                # Avoid duplicates from overlapping steps — keep strongest
                existing = [d for d in all_detections if d["channel"] == ch]
                if existing:
                    if det["power_dbfs"] > existing[0]["power_dbfs"]:
                        all_detections.remove(existing[0])
                        all_detections.append(det)
                else:
                    all_detections.append(det)

            for ch in list(all_out_of_band):
                if ch not in [c for c in result["out_of_band"]]:
                    all_out_of_band.discard(ch)
                    channels_scanned_total.add(ch)

            n_det = len(result["detections"])
            status = f"{n_det} detected" if n_det else "clear"
            print(
                f"  [{i+1}/{len(step_centers)}] {fc/1e6:.0f} MHz — "
                f"noise={result['noise_floor_dbfs']:.1f} dBFS, {status}",
                file=sys.stderr,
            )
    finally:
        sdr.close()

    all_detections.sort(key=lambda d: d["power_dbfs"], reverse=True)
    avg_noise = (
        np.mean(overall_noise_floors) if overall_noise_floors else -120.0
    )

    return {
        "noise_floor_dbfs": round(float(avg_noise), 1),
        "channels_scanned": len(channels_scanned_total),
        "channels_detected": len(all_detections),
        "detections": all_detections,
        "out_of_band": sorted(all_out_of_band),
    }


# ---------------------------------------------------------------------------
# Monitor mode — continuously watch a single channel
# ---------------------------------------------------------------------------

def monitor_channel(
    channel_name: str,
    sdr_device_args: str = "",
    gain: float = 40.0,
    update_interval_s: float = 0.5,
    duration_s: float = 0.0,
    json_output: bool = False,
) -> None:
    """Continuously monitor a single FPV channel.

    Prints power and SNR at each update interval. Press Ctrl+C to stop.
    """
    try:
        from sdr_common import SDRDevice
    except ImportError:
        print("[!] sdr_common not found.", file=sys.stderr)
        sys.exit(1)

    if channel_name not in ALL_CHANNELS:
        print(f"[!] Unknown channel: {channel_name}", file=sys.stderr)
        print(f"    Valid channels: {', '.join(sorted(ALL_CHANNELS.keys()))}", file=sys.stderr)
        sys.exit(1)

    ch_freq = ALL_CHANNELS[channel_name]
    capture_bw = 20e6  # 20 MHz capture centered on channel

    sdr = SDRDevice(device_args=sdr_device_args)
    sdr.setup_rx(freq=ch_freq, rate=capture_bw, gain=gain)

    print(
        f"[*] Monitoring {channel_name} ({ch_freq/1e6:.1f} MHz), "
        f"update every {update_interval_s:.1f}s. Ctrl+C to stop.",
        file=sys.stderr,
    )

    start = time.time()
    try:
        while True:
            num_samples = int(update_interval_s * capture_bw)
            iq = sdr.receive(num_samples=num_samples)
            if len(iq) < 1024:
                continue

            power, peak_freq = measure_channel_power(
                iq, capture_bw, ch_freq, ch_freq,
            )
            noise = estimate_noise_floor(iq, capture_bw)
            snr = power - noise
            elapsed = time.time() - start

            entry = {
                "t": round(elapsed, 1),
                "channel": channel_name,
                "power_dbfs": round(power, 1),
                "noise_dbfs": round(noise, 1),
                "snr_db": round(snr, 1),
                "peak_mhz": round(peak_freq / 1e6, 3),
                "detected": snr > 10.0,
            }

            if json_output:
                print(json.dumps(entry), flush=True)
            else:
                bar = "#" * max(0, int((snr + 10) / 2))
                det = " *** ACTIVE ***" if entry["detected"] else ""
                print(
                    f"  [{elapsed:7.1f}s] {channel_name} "
                    f"pwr={power:6.1f} dBFS  "
                    f"snr={snr:5.1f} dB  "
                    f"|{bar:<30s}|{det}",
                    flush=True,
                )

            if 0 < duration_s <= elapsed:
                break
    except KeyboardInterrupt:
        print("\n[*] Monitoring stopped.", file=sys.stderr)
    finally:
        sdr.close()


# ---------------------------------------------------------------------------
# Demod mode — FM-demodulate one channel from an IQ file
# ---------------------------------------------------------------------------

def demod_channel_from_file(
    iq: np.ndarray,
    sample_rate: float,
    center_freq: float,
    channel_name: str,
    output_path: Optional[str] = None,
) -> dict:
    """Extract and FM-demodulate a single FPV channel.

    Returns characterization results.  Optionally writes raw baseband
    to output_path as float32.
    """
    if channel_name not in ALL_CHANNELS:
        print(f"[!] Unknown channel: {channel_name}", file=sys.stderr)
        sys.exit(1)

    ch_freq = ALL_CHANNELS[channel_name]

    # Check channel is within capture bandwidth
    half_bw = sample_rate / 2.0
    offset = ch_freq - center_freq
    if abs(offset) > half_bw - FPV_CHANNEL_BW_HZ / 2:
        print(
            f"[!] Channel {channel_name} ({ch_freq/1e6:.1f} MHz) is outside "
            f"capture bandwidth ({center_freq/1e6:.1f} ± {half_bw/1e6:.1f} MHz)",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"[*] Extracting channel {channel_name} ({ch_freq/1e6:.1f} MHz) "
        f"from capture centered at {center_freq/1e6:.1f} MHz",
        file=sys.stderr,
    )

    # Isolate channel
    ch_iq, ch_rate = extract_channel(iq, sample_rate, center_freq, ch_freq)
    print(
        f"[*] Channel isolated: {len(ch_iq):,} samples at {ch_rate/1e6:.1f} Msps",
        file=sys.stderr,
    )

    # FM demodulate
    baseband = fm_demodulate(ch_iq)
    print(
        f"[*] FM demodulated: {len(baseband):,} samples "
        f"({len(baseband)/ch_rate:.3f}s)",
        file=sys.stderr,
    )

    # Characterize
    char_result = characterize_baseband(baseband, ch_rate)
    char_result["channel"] = channel_name
    char_result["channel_freq_mhz"] = ch_freq / 1e6

    # Save baseband if requested
    if output_path:
        baseband.astype(np.float32).tofile(output_path)
        print(f"[*] Baseband written to {output_path}", file=sys.stderr)
        char_result["output_file"] = output_path

    return char_result


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def print_scan_results(results: dict) -> None:
    """Print scan results in a human-readable table."""
    print()
    print("=" * 72)
    print("  Analog FPV 5.8 GHz Channel Scan Results")
    print("=" * 72)
    print(
        f"  Noise floor:      {results['noise_floor_dbfs']:.1f} dBFS"
    )
    print(
        f"  Channels scanned: {results['channels_scanned']} / "
        f"{len(ALL_CHANNELS)}"
    )
    print(
        f"  Active VTX:       {results['channels_detected']}"
    )
    if results["out_of_band"]:
        print(
            f"  Out of band:      {len(results['out_of_band'])} channels "
            f"(outside capture BW)"
        )
    print("-" * 72)

    if results["detections"]:
        print(
            f"  {'Chan':>5s}  {'Band':>5s}  {'Freq (MHz)':>10s}  "
            f"{'Power':>8s}  {'SNR':>6s}  {'Peak (MHz)':>11s}"
        )
        print("-" * 72)
        for det in results["detections"]:
            print(
                f"  {det['channel']:>5s}  {det['band']:>5s}  "
                f"{det['freq_mhz']:10.1f}  "
                f"{det['power_dbfs']:7.1f}  "
                f"{det['snr_db']:5.1f}  "
                f"{det['peak_freq_mhz']:11.3f}"
            )
    else:
        print("  No active VTX detected.")
    print("=" * 72)
    print()


def print_demod_results(results: dict) -> None:
    """Print demodulation characterization results."""
    print()
    print("=" * 60)
    print(f"  FM Demod: {results['channel']} ({results['channel_freq_mhz']:.1f} MHz)")
    print("=" * 60)
    print(f"  Video standard:      {results['video_standard']}")
    print(f"  NTSC sync SNR:       {results['ntsc_sync_snr_db']:.1f} dB")
    print(f"  PAL sync SNR:        {results['pal_sync_snr_db']:.1f} dB")
    print(f"  Audio subcarrier:    {'YES' if results['has_audio_subcarrier'] else 'no'} "
          f"({results['audio_subcarrier_snr_db']:.1f} dB)")
    print(f"  Baseband duration:   {results['baseband_duration_s']:.3f} s")
    print(f"  Baseband rate:       {results['baseband_sample_rate']/1e6:.1f} Msps")
    if results.get("output_file"):
        print(f"  Output:              {results['output_file']}")
    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analog FPV 5.8 GHz Video Signal Detector & Channel Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Modes:
  sweep   — scan all FPV channels, report active VTX (default)
  monitor — continuously watch a single channel (live SDR only)
  demod   — FM-demodulate a channel and characterize the baseband

Examples:
  %(prog)s --mode sweep --iq-file fpv.iq --sample-rate 20e6 --center-freq 5.8e9
  %(prog)s --mode monitor --channel R1
  %(prog)s --mode demod --channel F4 --iq-file fpv.iq --sample-rate 20e6 \\
      --center-freq 5.8e9 --output-baseband demod.raw
""",
    )
    parser.add_argument(
        "--mode", choices=["sweep", "monitor", "demod"], default="sweep",
        help="Operating mode (default: sweep)",
    )
    parser.add_argument(
        "--channel", default=None,
        help="Channel name for monitor/demod mode (e.g. R1, F4, A3)",
    )

    # IQ file input
    iq_group = parser.add_argument_group("IQ file input")
    iq_group.add_argument("--iq-file", help="Path to raw IQ file")
    iq_group.add_argument(
        "--sample-rate", type=float, default=20e6,
        help="Sample rate in Hz (default: 20e6)",
    )
    iq_group.add_argument(
        "--center-freq", type=float, default=5.8e9,
        help="Center frequency of capture in Hz (default: 5.8e9)",
    )
    iq_group.add_argument(
        "--iq-format", choices=["cf32", "cs16"], default="cf32",
        help="IQ sample format (default: cf32)",
    )

    # Live SDR
    sdr_group = parser.add_argument_group("Live SDR options")
    sdr_group.add_argument(
        "--sdr-device", default="",
        help="SoapySDR device string (auto-detect if empty)",
    )
    sdr_group.add_argument(
        "--gain", type=float, default=40.0,
        help="RF gain in dB (default: 40)",
    )
    sdr_group.add_argument(
        "--dwell-time", type=float, default=0.1,
        help="Dwell time per step in seconds (sweep mode, default: 0.1)",
    )

    # Detection
    det_group = parser.add_argument_group("Detection options")
    det_group.add_argument(
        "--threshold", type=float, default=10.0,
        help="Detection threshold in dB above noise floor (default: 10)",
    )

    # Monitor mode
    mon_group = parser.add_argument_group("Monitor options")
    mon_group.add_argument(
        "--update-interval", type=float, default=0.5,
        help="Update interval in seconds (monitor mode, default: 0.5)",
    )
    mon_group.add_argument(
        "--duration", type=float, default=0.0,
        help="Monitor duration in seconds (0=indefinite, default: 0)",
    )

    # Demod mode
    demod_group = parser.add_argument_group("Demod options")
    demod_group.add_argument(
        "--output-baseband", default=None,
        help="Path to write demodulated baseband (float32)",
    )

    # Output
    out_group = parser.add_argument_group("Output options")
    out_group.add_argument(
        "--json", action="store_true",
        help="Output results as JSON",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.mode == "monitor":
        if not args.channel:
            print("[!] --channel required for monitor mode", file=sys.stderr)
            sys.exit(1)
        if args.iq_file:
            print("[!] Monitor mode requires live SDR (no --iq-file)", file=sys.stderr)
            sys.exit(1)
        monitor_channel(
            channel_name=args.channel,
            sdr_device_args=args.sdr_device,
            gain=args.gain,
            update_interval_s=args.update_interval,
            duration_s=args.duration,
            json_output=args.json,
        )
        return

    if args.mode == "demod":
        if not args.channel:
            print("[!] --channel required for demod mode", file=sys.stderr)
            sys.exit(1)
        if not args.iq_file:
            print("[!] --iq-file required for demod mode", file=sys.stderr)
            sys.exit(1)
        iq, sr, cf = load_iq_file(
            args.iq_file, args.sample_rate, args.center_freq, args.iq_format,
        )
        results = demod_channel_from_file(
            iq, sr, cf, args.channel, args.output_baseband,
        )
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print_demod_results(results)
        return

    # Sweep mode
    if args.iq_file:
        iq, sr, cf = load_iq_file(
            args.iq_file, args.sample_rate, args.center_freq, args.iq_format,
        )
        results = scan_channels(
            iq, sr, cf, detection_threshold_db=args.threshold,
        )
    else:
        results = live_sweep(
            sdr_device_args=args.sdr_device,
            gain=args.gain,
            dwell_time_s=args.dwell_time,
            detection_threshold_db=args.threshold,
        )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_scan_results(results)


if __name__ == "__main__":
    main()
