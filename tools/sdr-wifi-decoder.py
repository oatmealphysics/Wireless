#!/usr/bin/env python3
"""
SDR-based WiFi Decoder — 2.4 GHz 802.11 Detection and Analysis

Detects and fingerprints WiFi activity in the 2.4 GHz ISM band directly from
SDR IQ samples, removing the need for a dedicated WiFi adapter (Alfa card) in
monitor mode. Primary goal: identify an ELRS Backpack ESP co-processor by its
WiFi emissions during drone operation.

Architecture tiers (determined by available SDR bandwidth):
  - Tier 1 (any SDR, ≥2 MHz): Channel energy detection, activity timing,
    basic occupancy mapping. Enough to detect "something is transmitting on
    channel N" but cannot decode frames.
  - Tier 2 (≥20 MHz, e.g. HackRF): 802.11b/g/n preamble correlation,
    beacon interval measurement, rough frame timing. Preamble detection is
    reliable; full OFDM demodulation is best-effort.
  - Tier 3 (≥20 MHz with good flatness): Best-effort OFDM demodulation of
    beacon/probe frames to extract SSID, BSSID (MAC), and capabilities.
    Subject to SDR analog filter roll-off and DC offset artifacts.

Feasibility notes (be honest):
  802.11g/n uses 20 MHz OFDM with 64 subcarriers (52 data + 4 pilot). A
  HackRF One captures at exactly 20 Msps but its analog filter rolls off at
  the edges, degrading outer subcarriers. RTL-SDR tops out at ~2.4 Msps and
  cannot demodulate any 802.11 frame — energy detection only. A USRP B210
  (56 MHz BW) handles this comfortably. This tool automatically selects the
  highest tier supported by the configured sample rate.

  For ESP co-processor detection, Tier 1 (energy) + Tier 2 (preamble timing)
  is usually sufficient: an ESP32 beaconing on a known SSID pattern
  ("ExpressLRS RX", "ELRS_*", etc.) produces periodic, detectable bursts
  on a specific channel. Tier 3 (SSID extraction) is a bonus when bandwidth
  allows.

Input modes:
  - Live SDR via SoapySDR (--live with standard --sdr-* options)
  - IQ file (--capture FILE with --sample-rate, --center-hz)

Supported IQ formats: cf32le (complex float32), sc16le (signed complex int16),
  sc8 (signed complex int8), MIDAS/BLUE .cdif.

Capture command for valid input (HackRF example, channel 1 center 2412 MHz):
  hackrf_transfer -r capture_ch1.cs8 -f 2412000000 -s 20000000 -g 40 -l 32
  # Then: python tools/sdr-wifi-decoder.py --capture capture_ch1.cs8 \\
  #        --input-format sc8 --sample-rate 20e6 --center-hz 2412e6

  USRP example (wider BW for better subcarrier coverage):
  python tools/sdr-wifi-decoder.py --live --sdr-device "driver=uhd" \\
      --sdr-rate 25e6 --channel 1

Requires: Python 3.10+, NumPy, SciPy
Optional: SoapySDR (for live capture), matplotlib (for --plot)
For authorized CTF research and security testing only.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly, firwin, lfilter

# ---------------------------------------------------------------------------
# 802.11 PHY constants
# ---------------------------------------------------------------------------

WIFI_CHANNELS_24GHZ = {
    1: 2412e6, 2: 2417e6, 3: 2422e6, 4: 2427e6, 5: 2432e6,
    6: 2437e6, 7: 2442e6, 8: 2447e6, 9: 2452e6, 10: 2457e6,
    11: 2462e6, 12: 2467e6, 13: 2472e6, 14: 2484e6,
}

OFDM_BW = 20e6
OFDM_FFT_SIZE = 64
OFDM_CP_LONG = 32
OFDM_CP_SHORT = 16
OFDM_SYMBOL_LONG = OFDM_FFT_SIZE + OFDM_CP_LONG
OFDM_SYMBOL_SHORT = OFDM_FFT_SIZE + OFDM_CP_SHORT

STF_PERIOD_SAMPLES = 16
STF_REPETITIONS = 10
STF_TOTAL_SAMPLES = STF_PERIOD_SAMPLES * STF_REPETITIONS

LTF_TOTAL_SAMPLES = OFDM_FFT_SIZE * 2 + OFDM_CP_LONG

# 802.11 OFDM subcarrier mapping (20 MHz, 64-point FFT)
# Data subcarriers: -26 to -1, +1 to +26 (52 total)
# Pilot subcarriers: -21, -7, +7, +21
# DC (0) and guard bands are null
DATA_SUBCARRIERS = list(range(-26, 0)) + list(range(1, 27))
PILOT_SUBCARRIERS = [-21, -7, 7, 21]
DATA_ONLY = [s for s in DATA_SUBCARRIERS if s not in PILOT_SUBCARRIERS]

# Known LTF frequency-domain sequence (IEEE 802.11-2020, Table 17-7)
LTF_FREQ = np.array([
    0, 1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1,
    1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1,
    1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1,
], dtype=np.complex64)

# ESP32/ESP8266 OUI prefixes (Espressif)
ESPRESSIF_OUIS = [
    bytes.fromhex("24:0a:c4".replace(":", "")),
    bytes.fromhex("24:62:ab".replace(":", "")),
    bytes.fromhex("24:6f:28".replace(":", "")),
    bytes.fromhex("30:ae:a4".replace(":", "")),
    bytes.fromhex("3c:61:05".replace(":", "")),
    bytes.fromhex("3c:71:bf".replace(":", "")),
    bytes.fromhex("40:f5:20".replace(":", "")),
    bytes.fromhex("48:3f:da".replace(":", "")),
    bytes.fromhex("54:32:04".replace(":", "")),
    bytes.fromhex("58:cf:79".replace(":", "")),
    bytes.fromhex("5c:cf:7f".replace(":", "")),
    bytes.fromhex("68:c6:3a".replace(":", "")),
    bytes.fromhex("70:03:9f".replace(":", "")),
    bytes.fromhex("7c:df:a1".replace(":", "")),
    bytes.fromhex("80:7d:3a".replace(":", "")),
    bytes.fromhex("84:0d:8e".replace(":", "")),
    bytes.fromhex("84:cc:a8".replace(":", "")),
    bytes.fromhex("84:f3:eb".replace(":", "")),
    bytes.fromhex("8c:aa:b5".replace(":", "")),
    bytes.fromhex("90:38:0c".replace(":", "")),
    bytes.fromhex("94:b5:55".replace(":", "")),
    bytes.fromhex("94:b9:7e".replace(":", "")),
    bytes.fromhex("98:cd:ac".replace(":", "")),
    bytes.fromhex("a0:20:a6".replace(":", "")),
    bytes.fromhex("a4:cf:12".replace(":", "")),
    bytes.fromhex("ac:67:b2".replace(":", "")),
    bytes.fromhex("b4:e6:2d".replace(":", "")),
    bytes.fromhex("bc:dd:c2".replace(":", "")),
    bytes.fromhex("c4:4f:33".replace(":", "")),
    bytes.fromhex("c8:2b:96".replace(":", "")),
    bytes.fromhex("cc:50:e3".replace(":", "")),
    bytes.fromhex("d8:a0:1d".replace(":", "")),
    bytes.fromhex("dc:4f:22".replace(":", "")),
    bytes.fromhex("e0:98:06".replace(":", "")),
    bytes.fromhex("e8:db:84".replace(":", "")),
    bytes.fromhex("ec:94:cb".replace(":", "")),
    bytes.fromhex("f0:08:d1".replace(":", "")),
    bytes.fromhex("f4:cf:a2".replace(":", "")),
    bytes.fromhex("fc:f5:c4".replace(":", "")),
]

ELRS_SSID_PATTERNS = [
    "ExpressLRS TX", "ExpressLRS RX",
    "ELRS_", "elrs_",
    "ExpressLRS",
]

# Rate table: SIGNAL field rate bits → (modulation, coding rate, data rate Mbps)
RATE_TABLE = {
    0b1101: ("BPSK", "1/2", 6),
    0b1111: ("BPSK", "3/4", 9),
    0b0101: ("QPSK", "1/2", 12),
    0b0111: ("QPSK", "3/4", 18),
    0b1001: ("16-QAM", "1/2", 24),
    0b1011: ("16-QAM", "3/4", 36),
    0b0001: ("64-QAM", "2/3", 48),
    0b0011: ("64-QAM", "3/4", 54),
}


# ---------------------------------------------------------------------------
# IQ file reading (shared with other tools in the repo)
# ---------------------------------------------------------------------------

def read_iq_file(path: Path, sample_rate: float, start_s: float,
                 length_s: float, input_format: str = "auto") -> tuple[np.ndarray, float]:
    size = path.stat().st_size

    if input_format == "auto":
        suffix = path.suffix.lower()
        if suffix in (".cf32", ".fc32"):
            input_format = "cf32le"
        elif suffix in (".cs16", ".sc16"):
            input_format = "sc16le"
        elif suffix in (".cs8", ".sc8"):
            input_format = "sc8"
        elif suffix in (".cdif", ".blue"):
            input_format = "blue"
        else:
            input_format = "cf32le"

    if input_format == "blue":
        header_size = 512
        bytes_per_iq = 4  # 2x int16
        dtype = np.dtype("<i2")
        data_size = size - header_size
    elif input_format == "cf32le":
        header_size = 0
        bytes_per_iq = 8  # 2x float32
        dtype = np.dtype("<f4")
        data_size = size
    elif input_format == "sc16le":
        header_size = 0
        bytes_per_iq = 4  # 2x int16
        dtype = np.dtype("<i2")
        data_size = size
    elif input_format == "sc8":
        header_size = 0
        bytes_per_iq = 2  # 2x int8
        dtype = np.dtype("<i1")
        data_size = size
    else:
        raise ValueError(f"unknown format: {input_format}")

    total_samples = data_size // bytes_per_iq

    start_sample = max(0, int(start_s * sample_rate))
    num_samples = int(length_s * sample_rate)
    if start_sample >= total_samples:
        return np.array([], dtype=np.complex64), start_s
    num_samples = min(num_samples, total_samples - start_sample)

    actual_start = start_sample / sample_rate

    if input_format == "cf32le":
        offset = header_size + start_sample * bytes_per_iq
        raw = np.fromfile(str(path), dtype=dtype, count=num_samples * 2, offset=offset)
        iq = (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)
    elif input_format in ("sc16le", "blue"):
        offset = header_size + start_sample * bytes_per_iq
        raw = np.fromfile(str(path), dtype=dtype, count=num_samples * 2, offset=offset)
        iq = (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32))
        iq /= 32768.0
        iq = iq.astype(np.complex64)
    elif input_format == "sc8":
        offset = header_size + start_sample * bytes_per_iq
        raw = np.fromfile(str(path), dtype=dtype, count=num_samples * 2, offset=offset)
        iq = (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32))
        iq /= 128.0
        iq = iq.astype(np.complex64)
    else:
        raise ValueError(f"unknown format: {input_format}")

    return iq, actual_start


# ---------------------------------------------------------------------------
# Tier 1: Channel energy detection
# ---------------------------------------------------------------------------

@dataclass
class ChannelEnergy:
    channel: int
    center_hz: float
    power_dbm: float
    noise_floor_dbm: float
    snr_db: float
    active: bool
    burst_count: int = 0
    duty_cycle: float = 0.0


def measure_channel_energy(iq: np.ndarray, sample_rate: float,
                           center_hz: float, channel_hz: float,
                           channel_bw: float = 22e6) -> ChannelEnergy:
    offset = channel_hz - center_hz
    if abs(offset) >= sample_rate / 2:
        return ChannelEnergy(
            channel=0, center_hz=channel_hz,
            power_dbm=-120.0, noise_floor_dbm=-120.0,
            snr_db=0.0, active=False,
        )

    t = np.arange(len(iq), dtype=np.float64) / sample_rate
    shifted = iq * np.exp(-2j * np.pi * offset * t).astype(np.complex64)

    usable_bw = min(channel_bw, sample_rate)
    if usable_bw < sample_rate * 0.9:
        ntaps = min(63, len(shifted) // 4)
        if ntaps >= 3:
            cutoff = usable_bw / (2 * sample_rate)
            taps = firwin(ntaps | 1, cutoff)
            shifted = lfilter(taps, 1.0, shifted)

    power = np.abs(shifted) ** 2
    mean_power = float(np.mean(power))
    if mean_power <= 0:
        mean_power = 1e-20
    power_dbm = 10 * math.log10(mean_power)

    block_size = max(1, int(sample_rate * 0.001))
    n_blocks = len(power) // block_size
    if n_blocks > 1:
        block_power = np.mean(power[:n_blocks * block_size].reshape(n_blocks, block_size), axis=1)
        noise_floor = float(np.percentile(block_power, 10))
        if noise_floor <= 0:
            noise_floor = 1e-20
        noise_floor_dbm = 10 * math.log10(noise_floor)

        threshold = noise_floor * 6.0
        active_blocks = block_power > threshold
        burst_edges = np.diff(np.r_[False, active_blocks, False].astype(np.int8))
        burst_count = int(np.sum(burst_edges == 1))
        duty_cycle = float(np.mean(active_blocks))
    else:
        noise_floor_dbm = power_dbm - 3.0
        burst_count = 1 if mean_power > 1e-10 else 0
        duty_cycle = 1.0 if burst_count else 0.0

    snr_db = power_dbm - noise_floor_dbm

    ch_num = 0
    for num, freq in WIFI_CHANNELS_24GHZ.items():
        if abs(freq - channel_hz) < 1e6:
            ch_num = num
            break

    return ChannelEnergy(
        channel=ch_num,
        center_hz=channel_hz,
        power_dbm=power_dbm,
        noise_floor_dbm=noise_floor_dbm,
        snr_db=snr_db,
        active=snr_db > 10.0 and duty_cycle > 0.01,
        burst_count=burst_count,
        duty_cycle=duty_cycle,
    )


def scan_channel_energies(iq: np.ndarray, sample_rate: float,
                          center_hz: float) -> list[ChannelEnergy]:
    results = []
    for ch_num, ch_freq in sorted(WIFI_CHANNELS_24GHZ.items()):
        if abs(ch_freq - center_hz) < sample_rate / 2:
            energy = measure_channel_energy(iq, sample_rate, center_hz, ch_freq)
            results.append(energy)
    return results


# ---------------------------------------------------------------------------
# Tier 2: 802.11 preamble detection (STF autocorrelation)
# ---------------------------------------------------------------------------

@dataclass
class PreambleDetection:
    sample_offset: int
    time_offset_s: float
    correlation_peak: float
    normalized_correlation: float
    cfo_hz: float
    snr_estimate_db: float


def detect_stf_preambles(iq: np.ndarray, sample_rate: float,
                         threshold: float = 0.6,
                         min_gap_samples: int = 0) -> list[PreambleDetection]:
    if sample_rate < 18e6:
        return []

    ratio = sample_rate / OFDM_BW
    period = int(round(STF_PERIOD_SAMPLES * ratio))
    if period < 4:
        return []

    delay = period
    if len(iq) <= delay + period:
        return []

    window = period * 2
    conj_prod = iq[delay:] * np.conj(iq[:-delay])

    n_blocks = (len(conj_prod) - window) + 1
    if n_blocks <= 0:
        return []

    cumsum = np.cumsum(conj_prod)
    moving_sum = cumsum[window:] - cumsum[:-window]
    moving_sum = np.r_[cumsum[:1], moving_sum] if len(moving_sum) > 0 else cumsum[:n_blocks]
    if len(moving_sum) == 0:
        return []

    power_cumsum = np.cumsum(np.abs(iq[delay:]) ** 2)
    moving_power = power_cumsum[window:] - power_cumsum[:-window]
    moving_power = np.r_[power_cumsum[:1], moving_power] if len(moving_power) > 0 else power_cumsum[:n_blocks]

    min_len = min(len(moving_sum), len(moving_power))
    moving_sum = moving_sum[:min_len]
    moving_power = moving_power[:min_len]

    safe_power = np.where(moving_power > 0, moving_power, 1.0)
    metric = np.abs(moving_sum) / safe_power

    if min_gap_samples <= 0:
        min_gap_samples = int(sample_rate * 200e-6)

    detections = []
    peaks = np.where(metric > threshold)[0]
    if len(peaks) == 0:
        return []

    groups = np.split(peaks, np.where(np.diff(peaks) > min_gap_samples)[0] + 1)

    for group in groups:
        if len(group) == 0:
            continue
        best_idx = group[np.argmax(metric[group])]
        corr_val = complex(moving_sum[best_idx])
        cfo_rad = np.angle(corr_val)
        cfo_hz = cfo_rad * sample_rate / (2 * np.pi * delay)

        peak_power = float(np.mean(np.abs(iq[best_idx:best_idx + window]) ** 2))
        noise_est = float(np.percentile(
            np.abs(iq[max(0, best_idx - window * 4):best_idx]) ** 2, 20
        )) if best_idx > window else 1e-20
        if noise_est <= 0:
            noise_est = 1e-20
        snr_db = 10 * math.log10(max(peak_power / noise_est, 1e-10))

        detections.append(PreambleDetection(
            sample_offset=int(best_idx),
            time_offset_s=best_idx / sample_rate,
            correlation_peak=float(np.abs(corr_val)),
            normalized_correlation=float(metric[best_idx]),
            cfo_hz=float(cfo_hz),
            snr_estimate_db=snr_db,
        ))

    return detections


# ---------------------------------------------------------------------------
# Tier 3: Best-effort OFDM demodulation (beacon/probe frames)
# ---------------------------------------------------------------------------

@dataclass
class OFDMFrame:
    sample_offset: int
    time_offset_s: float
    rate_mbps: int
    length_bytes: int
    modulation: str
    coding_rate: str
    frame_type: str
    frame_subtype: str
    bssid: str
    src_addr: str
    dst_addr: str
    ssid: str
    beacon_interval_ms: float
    is_espressif: bool
    is_elrs_pattern: bool
    raw_signal_field: int
    demod_quality: str
    payload_hex: str


def _correct_cfo(iq: np.ndarray, cfo_hz: float, sample_rate: float) -> np.ndarray:
    t = np.arange(len(iq), dtype=np.float64) / sample_rate
    return iq * np.exp(-2j * np.pi * cfo_hz * t).astype(np.complex64)


def _extract_ofdm_symbol(iq: np.ndarray, start: int,
                         cp_len: int = OFDM_CP_SHORT) -> np.ndarray | None:
    symbol_start = start + cp_len
    symbol_end = symbol_start + OFDM_FFT_SIZE
    if symbol_end > len(iq):
        return None
    return np.fft.fftshift(np.fft.fft(iq[symbol_start:symbol_end]))


def _estimate_channel(ltf_symbols: np.ndarray) -> np.ndarray:
    channel = np.ones(OFDM_FFT_SIZE, dtype=np.complex64)
    for sc in range(-32, 32):
        shifted_idx = _sc_to_fftshift_idx(sc)
        orig_idx = (sc + OFDM_FFT_SIZE) % OFDM_FFT_SIZE
        if LTF_FREQ[orig_idx] != 0:
            channel[shifted_idx] = ltf_symbols[shifted_idx] / LTF_FREQ[orig_idx]
    return channel


def _equalize(symbol: np.ndarray, channel: np.ndarray) -> np.ndarray:
    safe_chan = np.where(np.abs(channel) > 1e-6, channel, 1.0)
    return symbol / safe_chan


def _sc_to_fftshift_idx(sc: int) -> int:
    return sc + OFDM_FFT_SIZE // 2


def _decode_signal_field(equalized: np.ndarray) -> tuple[int, int, int] | None:
    bits = []
    for sc_idx in DATA_ONLY[:24]:
        fft_idx = _sc_to_fftshift_idx(sc_idx)
        val = equalized[fft_idx]
        bits.append(0 if val.real > 0 else 1)

    if len(bits) < 24:
        return None

    rate_bits = (bits[0] << 3) | (bits[1] << 2) | (bits[2] << 1) | bits[3]
    length = 0
    for i in range(12):
        length |= bits[5 + i] << i
    parity_bit = bits[17]

    check = 0
    for i in range(17):
        check ^= bits[i]
    if check != parity_bit:
        return None

    return rate_bits, length, (rate_bits << 20) | (length << 8) | (parity_bit << 7)


def _demod_bpsk_symbols(iq: np.ndarray, start: int, channel: np.ndarray,
                        num_symbols: int) -> bytes:
    all_bits = []
    pos = start
    for sym_idx in range(num_symbols):
        cp = OFDM_CP_SHORT
        sym = _extract_ofdm_symbol(iq, pos, cp)
        if sym is None:
            break
        eq = _equalize(sym, channel)
        for sc_idx in DATA_ONLY:
            fft_idx = _sc_to_fftshift_idx(sc_idx)
            all_bits.append(0 if eq[fft_idx].real > 0 else 1)
        pos += OFDM_FFT_SIZE + cp

    octets = []
    for i in range(0, len(all_bits) - 7, 8):
        byte_val = 0
        for b in range(8):
            byte_val |= all_bits[i + b] << b
        octets.append(byte_val)
    return bytes(octets)


def _parse_mac(data: bytes, offset: int) -> str:
    if offset + 6 > len(data):
        return "00:00:00:00:00:00"
    return ":".join(f"{b:02x}" for b in data[offset:offset + 6])


def _is_espressif_mac(mac_str: str) -> bool:
    try:
        mac_bytes = bytes.fromhex(mac_str.replace(":", ""))
    except ValueError:
        return False
    oui = mac_bytes[:3]
    return oui in ESPRESSIF_OUIS


def _is_elrs_ssid(ssid: str) -> bool:
    for pattern in ELRS_SSID_PATTERNS:
        if ssid.startswith(pattern) or pattern in ssid:
            return True
    return False


def _parse_80211_header(data: bytes) -> dict:
    result = {
        "frame_type": "unknown",
        "frame_subtype": "unknown",
        "bssid": "00:00:00:00:00:00",
        "src_addr": "00:00:00:00:00:00",
        "dst_addr": "00:00:00:00:00:00",
        "ssid": "",
        "beacon_interval_ms": 0.0,
    }
    if len(data) < 24:
        return result

    fc = data[0] | (data[1] << 8)
    ftype = (fc >> 2) & 0x3
    subtype = (fc >> 4) & 0xF

    type_names = {0: "management", 1: "control", 2: "data"}
    result["frame_type"] = type_names.get(ftype, f"type_{ftype}")

    if ftype == 0:
        subtype_names = {
            0: "association_request", 1: "association_response",
            4: "probe_request", 5: "probe_response",
            8: "beacon", 10: "disassociation",
            11: "authentication", 12: "deauthentication",
        }
        result["frame_subtype"] = subtype_names.get(subtype, f"mgmt_{subtype}")
    else:
        result["frame_subtype"] = f"{result['frame_type']}_{subtype}"

    result["dst_addr"] = _parse_mac(data, 4)
    result["src_addr"] = _parse_mac(data, 10)
    result["bssid"] = _parse_mac(data, 16)

    if ftype == 0 and subtype in (8, 5):
        if len(data) >= 36:
            result["beacon_interval_ms"] = (data[32] | (data[33] << 8)) * 1.024

        ie_offset = 36
        while ie_offset + 2 <= len(data):
            ie_id = data[ie_offset]
            ie_len = data[ie_offset + 1]
            if ie_offset + 2 + ie_len > len(data):
                break
            if ie_id == 0:
                try:
                    result["ssid"] = data[ie_offset + 2:ie_offset + 2 + ie_len].decode("utf-8", errors="replace")
                except Exception:
                    pass
                break
            ie_offset += 2 + ie_len

    return result


def demodulate_frame(iq: np.ndarray, preamble_start: int,
                     sample_rate: float, cfo_hz: float = 0.0) -> OFDMFrame | None:
    if sample_rate < 18e6:
        return None

    ratio = sample_rate / OFDM_BW
    stf_len = int(round(STF_TOTAL_SAMPLES * ratio))
    ltf_cp = int(round(OFDM_CP_LONG * ratio))
    ltf_sym = int(round(OFDM_FFT_SIZE * ratio))

    working = iq
    if abs(cfo_hz) > 10:
        working = _correct_cfo(iq, cfo_hz, sample_rate)

    if abs(ratio - 1.0) > 0.01:
        p = int(round(OFDM_BW))
        q = int(round(sample_rate))
        from math import gcd
        g = gcd(p, q)
        p, q = p // g, q // g
        if p > 1000:
            p, q = int(round(OFDM_BW / 1e3)), int(round(sample_rate / 1e3))
            g2 = gcd(p, q)
            p, q = p // g2, q // g2
        needed = preamble_start + stf_len + ltf_cp + 2 * ltf_sym + 20 * (OFDM_FFT_SIZE + OFDM_CP_SHORT)
        needed = int(needed * 1.1)
        end = min(len(working), preamble_start + needed)
        segment = working[preamble_start:end]
        if len(segment) < 100:
            return None
        try:
            resampled = resample_poly(segment, p, q)
        except Exception:
            return None
        working = np.zeros(preamble_start + len(resampled), dtype=np.complex64)
        working[preamble_start:preamble_start + len(resampled)] = resampled
        stf_len = STF_TOTAL_SAMPLES
        ltf_cp = OFDM_CP_LONG
        ltf_sym = OFDM_FFT_SIZE

    ltf_start = preamble_start + stf_len
    ltf1_start = ltf_start + ltf_cp
    ltf2_start = ltf1_start + ltf_sym

    if ltf2_start + ltf_sym > len(working):
        return None

    ltf1 = np.fft.fftshift(np.fft.fft(working[ltf1_start:ltf1_start + OFDM_FFT_SIZE]))
    ltf2 = np.fft.fftshift(np.fft.fft(working[ltf2_start:ltf2_start + OFDM_FFT_SIZE]))
    ltf_avg = (ltf1 + ltf2) / 2.0
    channel = _estimate_channel(ltf_avg)

    sig_start = ltf2_start + OFDM_FFT_SIZE
    sig_sym = _extract_ofdm_symbol(working, sig_start, OFDM_CP_LONG)
    if sig_sym is None:
        return None
    sig_eq = _equalize(sig_sym, channel)

    sig_result = _decode_signal_field(sig_eq)
    if sig_result is None:
        return None

    rate_bits, length, raw_signal = sig_result
    rate_info = RATE_TABLE.get(rate_bits)
    if rate_info is None:
        return None

    modulation, coding, rate_mbps = rate_info

    if length > 4095 or length < 10:
        return None

    demod_quality = "good"
    if rate_mbps > 6:
        demod_quality = "signal_only"
    elif length > 200:
        demod_quality = "partial"

    data_start = sig_start + OFDM_SYMBOL_LONG

    header_data = b""
    parsed = {
        "frame_type": "unknown", "frame_subtype": "unknown",
        "bssid": "00:00:00:00:00:00", "src_addr": "00:00:00:00:00:00",
        "dst_addr": "00:00:00:00:00:00", "ssid": "", "beacon_interval_ms": 0.0,
    }

    if rate_mbps == 6:
        needed_symbols = min((length * 8) // 24 + 2, 40)
        raw_bytes = _demod_bpsk_symbols(working, data_start, channel, needed_symbols)
        if len(raw_bytes) >= 24:
            header_data = raw_bytes
            parsed = _parse_80211_header(raw_bytes)

    bssid = parsed["bssid"]
    is_esp = _is_espressif_mac(bssid) or _is_espressif_mac(parsed["src_addr"])
    is_elrs = _is_elrs_ssid(parsed["ssid"])

    return OFDMFrame(
        sample_offset=preamble_start,
        time_offset_s=preamble_start / sample_rate,
        rate_mbps=rate_mbps,
        length_bytes=length,
        modulation=modulation,
        coding_rate=coding,
        frame_type=parsed["frame_type"],
        frame_subtype=parsed["frame_subtype"],
        bssid=bssid,
        src_addr=parsed["src_addr"],
        dst_addr=parsed["dst_addr"],
        ssid=parsed["ssid"],
        beacon_interval_ms=parsed["beacon_interval_ms"],
        is_espressif=is_esp,
        is_elrs_pattern=is_elrs,
        raw_signal_field=raw_signal,
        demod_quality=demod_quality,
        payload_hex=header_data[:64].hex() if header_data else "",
    )


# ---------------------------------------------------------------------------
# ESP co-processor detection logic
# ---------------------------------------------------------------------------

@dataclass
class ESPDetectionResult:
    detected: bool
    confidence: str  # "high", "medium", "low", "none"
    evidence: list[str] = field(default_factory=list)
    channel: int = 0
    bssid: str = ""
    ssid: str = ""
    beacon_interval_ms: float = 0.0
    beacon_count: int = 0
    preamble_count: int = 0
    energy_snr_db: float = 0.0


def detect_esp_coprocessor(iq: np.ndarray, sample_rate: float,
                           center_hz: float,
                           target_channel: int = 0) -> ESPDetectionResult:
    result = ESPDetectionResult(detected=False, confidence="none")

    if target_channel:
        channels = {target_channel: WIFI_CHANNELS_24GHZ[target_channel]}
    else:
        channels = {ch: freq for ch, freq in WIFI_CHANNELS_24GHZ.items()
                    if abs(freq - center_hz) < sample_rate / 2}

    if not channels:
        result.evidence.append("no WiFi channels within capture bandwidth")
        return result

    best_channel = 0
    best_snr = 0.0

    for ch_num, ch_freq in channels.items():
        energy = measure_channel_energy(iq, sample_rate, center_hz, ch_freq)
        if energy.active and energy.snr_db > best_snr:
            best_snr = energy.snr_db
            best_channel = ch_num
            result.energy_snr_db = energy.snr_db
            result.channel = ch_num
            result.evidence.append(
                f"channel {ch_num} active: SNR={energy.snr_db:.1f}dB, "
                f"{energy.burst_count} bursts, {energy.duty_cycle:.1%} duty"
            )

    if not best_channel:
        result.evidence.append("no active WiFi channels detected")
        return result

    if sample_rate >= 18e6:
        ch_freq = WIFI_CHANNELS_24GHZ[best_channel]
        offset = ch_freq - center_hz
        t = np.arange(len(iq), dtype=np.float64) / sample_rate
        shifted = iq * np.exp(-2j * np.pi * offset * t).astype(np.complex64)

        preambles = detect_stf_preambles(shifted, sample_rate)
        result.preamble_count = len(preambles)
        if preambles:
            result.evidence.append(f"{len(preambles)} 802.11 preamble(s) detected")

            intervals = []
            for i in range(1, len(preambles)):
                dt = preambles[i].time_offset_s - preambles[i - 1].time_offset_s
                intervals.append(dt)

            if intervals:
                median_interval_ms = float(np.median(intervals)) * 1000
                result.evidence.append(
                    f"median inter-frame interval: {median_interval_ms:.1f}ms"
                )
                if 90 < median_interval_ms < 110:
                    result.evidence.append(
                        "~100ms beacon interval matches typical ESP default"
                    )
                    result.beacon_interval_ms = median_interval_ms

            for preamble in preambles[:10]:
                frame = demodulate_frame(
                    shifted, preamble.sample_offset,
                    sample_rate, preamble.cfo_hz,
                )
                if frame is None:
                    continue

                if frame.is_espressif:
                    result.is_espressif = True
                    result.bssid = frame.bssid
                    result.evidence.append(
                        f"Espressif OUI confirmed: BSSID={frame.bssid}"
                    )
                if frame.ssid:
                    result.ssid = frame.ssid
                    result.evidence.append(f"SSID extracted: {frame.ssid!r}")
                if frame.is_elrs_pattern:
                    result.evidence.append(
                        f"ELRS SSID pattern match: {frame.ssid!r}"
                    )
                if frame.frame_subtype == "beacon":
                    result.beacon_count += 1
                    result.beacon_interval_ms = frame.beacon_interval_ms

    score = 0
    if result.energy_snr_db > 10:
        score += 1
    if result.preamble_count > 0:
        score += 1
    if result.beacon_count > 0:
        score += 1
    if any("Espressif" in e for e in result.evidence):
        score += 2
    if any("ELRS" in e for e in result.evidence):
        score += 2
    if result.beacon_interval_ms and 90 < result.beacon_interval_ms < 110:
        score += 1

    if score >= 5:
        result.detected = True
        result.confidence = "high"
    elif score >= 3:
        result.detected = True
        result.confidence = "medium"
    elif score >= 1:
        result.confidence = "low"

    return result


# ---------------------------------------------------------------------------
# Live SDR scanning
# ---------------------------------------------------------------------------

def live_scan(device_args: str, channels: list[int], duration_s: float,
              gain: float, verbose: bool) -> dict:
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from sdr_common import SDRDevice, check_soapy_available
        check_soapy_available()
    except ImportError:
        print("[!] SoapySDR required for live scanning. "
              "Use --capture for file input.", file=sys.stderr)
        sys.exit(1)

    results = {"scan_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "channels": {}, "esp_detection": None}

    if not channels:
        channels = list(range(1, 14))

    sdr = SDRDevice(device_args)
    max_rate = 20e6
    try:
        _, hw_max = sdr.get_sample_rate_range()
        max_rate = min(hw_max, 25e6)
    except Exception:
        pass
    sample_rate = max(max_rate, 2e6)

    print(f"[*] Scanning {len(channels)} WiFi channels at "
          f"{sample_rate/1e6:.1f} Msps", file=sys.stderr)

    for ch_num in channels:
        ch_freq = WIFI_CHANNELS_24GHZ.get(ch_num)
        if ch_freq is None:
            continue

        print(f"[*] Channel {ch_num} ({ch_freq/1e6:.0f} MHz)...", file=sys.stderr)
        sdr.setup_rx(freq=ch_freq, rate=sample_rate, gain=gain)
        time.sleep(0.05)

        num_samples = int(sample_rate * duration_s)
        iq = sdr.receive(num_samples)

        energy = measure_channel_energy(iq, sample_rate, ch_freq, ch_freq)
        ch_result = {
            "power_dbm": round(energy.power_dbm, 1),
            "noise_floor_dbm": round(energy.noise_floor_dbm, 1),
            "snr_db": round(energy.snr_db, 1),
            "active": energy.active,
            "burst_count": energy.burst_count,
            "duty_cycle": round(energy.duty_cycle, 3),
        }

        if energy.active and sample_rate >= 18e6:
            preambles = detect_stf_preambles(iq, sample_rate)
            ch_result["preamble_count"] = len(preambles)
            ch_result["frames"] = []
            for p in preambles[:20]:
                frame = demodulate_frame(iq, p.sample_offset, sample_rate, p.cfo_hz)
                if frame:
                    ch_result["frames"].append({
                        "time_s": round(frame.time_offset_s, 6),
                        "type": frame.frame_subtype,
                        "bssid": frame.bssid,
                        "ssid": frame.ssid,
                        "rate_mbps": frame.rate_mbps,
                        "is_espressif": frame.is_espressif,
                        "is_elrs": frame.is_elrs_pattern,
                    })

        results["channels"][ch_num] = ch_result

        if verbose and energy.active:
            print(f"    SNR={energy.snr_db:.1f}dB, "
                  f"bursts={energy.burst_count}, "
                  f"duty={energy.duty_cycle:.1%}", file=sys.stderr)

    sdr.close()

    active_channels = [ch for ch, r in results["channels"].items() if r["active"]]
    if active_channels:
        best_ch = max(active_channels,
                      key=lambda c: results["channels"][c]["snr_db"])
        results["strongest_channel"] = best_ch
        esp_frames = []
        for ch, r in results["channels"].items():
            for f in r.get("frames", []):
                if f.get("is_espressif") or f.get("is_elrs"):
                    esp_frames.append(f)
        if esp_frames:
            results["esp_detection"] = {
                "detected": True,
                "frames": esp_frames,
            }

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SDR-based WiFi decoder for 2.4 GHz 802.11 detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Capture commands for valid input:

  HackRF (20 Msps, channel 1):
    hackrf_transfer -r ch1.cs8 -f 2412000000 -s 20000000 -g 40 -l 32

  USRP B210 (25 Msps, channel 1):
    uhd_rx_cfile ch1.cf32 --freq 2412e6 --rate 25e6 --gain 40 --duration 5

  RTL-SDR (2.4 Msps, channel 6 — energy detection only):
    rtl_sdr -f 2437000000 -s 2400000 -g 40 ch6.cs8

Then analyze:
    python tools/sdr-wifi-decoder.py --capture ch1.cs8 --input-format sc8 \\
        --sample-rate 20e6 --center-hz 2412e6

Feasibility note:
  RTL-SDR: energy/activity detection only (2.4 MHz BW << 20 MHz 802.11).
  HackRF:  preamble detection reliable; OFDM demod is best-effort (analog
           filter roll-off degrades outer subcarriers at exactly 20 Msps).
  USRP:    full capability at 25+ Msps.
""",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", type=Path,
                      help="IQ capture file to analyze")
    mode.add_argument("--live", action="store_true",
                      help="Live SDR capture via SoapySDR")

    parser.add_argument("--input-format",
                        choices=("auto", "cf32le", "sc16le", "sc8", "blue"),
                        default="auto")
    parser.add_argument("--sample-rate", type=float,
                        help="Sample rate in Hz (required for file input)")
    parser.add_argument("--center-hz", type=float,
                        help="Center frequency in Hz (required for file input)")
    parser.add_argument("--channel", type=int, action="append", default=[],
                        help="WiFi channel(s) to analyze (1-14, repeatable)")
    parser.add_argument("--start-seconds", type=float, default=0.0,
                        help="Start offset in capture file (seconds)")
    parser.add_argument("--duration-seconds", type=float, default=1.0,
                        help="Duration to analyze (seconds)")
    parser.add_argument("--preamble-threshold", type=float, default=0.6,
                        help="STF correlation threshold (0-1, default 0.6)")
    parser.add_argument("--detect-esp", action="store_true",
                        help="Run ESP co-processor detection analysis")

    parser.add_argument("--sdr-device", default="",
                        help="SoapySDR device string for live mode")
    parser.add_argument("--sdr-gain", type=float, default=40.0,
                        help="RF gain in dB for live mode")
    parser.add_argument("--dwell-time", type=float, default=0.5,
                        help="Per-channel dwell time in seconds (live mode)")

    parser.add_argument("-o", "--output", type=Path,
                        help="Output JSON file")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output to stdout")
    args = parser.parse_args(argv)

    if args.live:
        results = live_scan(args.sdr_device, args.channel,
                            args.dwell_time, args.sdr_gain, args.verbose)
        output_json = json.dumps(results, indent=2, default=str)
        if args.json:
            print(output_json)
        else:
            active = [ch for ch, r in results["channels"].items() if r["active"]]
            print(f"\n[*] Scan complete: {len(active)} active channel(s) "
                  f"of {len(results['channels'])} scanned", file=sys.stderr)
            for ch in active:
                r = results["channels"][ch]
                print(f"    Ch {ch}: SNR={r['snr_db']:.1f}dB, "
                      f"bursts={r['burst_count']}", file=sys.stderr)
                for f in r.get("frames", []):
                    ssid = f.get("ssid", "")
                    esp = " [ESP]" if f.get("is_espressif") else ""
                    elrs = " [ELRS]" if f.get("is_elrs") else ""
                    print(f"      {f['type']}: BSSID={f['bssid']} "
                          f"SSID={ssid!r}{esp}{elrs}", file=sys.stderr)
        if args.output:
            args.output.write_text(output_json + "\n")
            print(f"[*] Results saved to {args.output}", file=sys.stderr)
        return 0

    if not args.sample_rate or not args.center_hz:
        parser.error("--sample-rate and --center-hz required for file input")
    if not args.capture.exists():
        print(f"[!] Capture file not found: {args.capture}", file=sys.stderr)
        return 1

    print(f"[*] Reading {args.capture.name}", file=sys.stderr)
    print(f"    Sample rate: {args.sample_rate/1e6:.1f} Msps", file=sys.stderr)
    print(f"    Center freq: {args.center_hz/1e6:.1f} MHz", file=sys.stderr)

    tier = 1
    if args.sample_rate >= 18e6:
        tier = 3
    elif args.sample_rate >= 5e6:
        tier = 1
    print(f"    Capability tier: {tier} "
          f"({'energy+preamble+OFDM' if tier >= 3 else 'energy detection only'})",
          file=sys.stderr)

    iq, actual_start = read_iq_file(
        args.capture, args.sample_rate,
        args.start_seconds, args.duration_seconds,
        args.input_format,
    )
    if len(iq) == 0:
        print("[!] No samples read from capture", file=sys.stderr)
        return 1

    print(f"    Loaded {len(iq)} samples "
          f"({len(iq)/args.sample_rate*1000:.1f}ms)", file=sys.stderr)

    results: dict = {
        "capture": str(args.capture),
        "sample_rate_hz": args.sample_rate,
        "center_hz": args.center_hz,
        "tier": tier,
        "duration_s": len(iq) / args.sample_rate,
        "channels": {},
        "preambles": [],
        "frames": [],
        "esp_detection": None,
    }

    channel_list = args.channel if args.channel else [
        ch for ch, freq in WIFI_CHANNELS_24GHZ.items()
        if abs(freq - args.center_hz) < args.sample_rate / 2
    ]

    print(f"\n[*] Channel energy scan ({len(channel_list)} channels in BW):",
          file=sys.stderr)
    for ch_num in sorted(channel_list):
        ch_freq = WIFI_CHANNELS_24GHZ.get(ch_num)
        if ch_freq is None:
            continue
        energy = measure_channel_energy(iq, args.sample_rate, args.center_hz, ch_freq)
        status = "ACTIVE" if energy.active else "quiet"
        print(f"    Ch {ch_num:2d} ({ch_freq/1e6:.0f} MHz): "
              f"SNR={energy.snr_db:5.1f}dB  {status}"
              f"{'  bursts=' + str(energy.burst_count) if energy.active else ''}",
              file=sys.stderr)
        results["channels"][ch_num] = {
            "center_hz": ch_freq,
            "power_dbm": round(energy.power_dbm, 1),
            "noise_floor_dbm": round(energy.noise_floor_dbm, 1),
            "snr_db": round(energy.snr_db, 1),
            "active": energy.active,
            "burst_count": energy.burst_count,
            "duty_cycle": round(energy.duty_cycle, 3),
        }

    if tier >= 3:
        active_channels = [ch for ch in channel_list
                           if results["channels"].get(ch, {}).get("active")]
        if not active_channels:
            active_channels = sorted(channel_list)[:3]

        for ch_num in active_channels:
            ch_freq = WIFI_CHANNELS_24GHZ[ch_num]
            offset = ch_freq - args.center_hz
            t = np.arange(len(iq), dtype=np.float64) / args.sample_rate
            shifted = iq * np.exp(-2j * np.pi * offset * t).astype(np.complex64)

            preambles = detect_stf_preambles(
                shifted, args.sample_rate, args.preamble_threshold,
            )
            print(f"\n[*] Channel {ch_num}: {len(preambles)} preamble(s) detected",
                  file=sys.stderr)

            for p in preambles:
                results["preambles"].append({
                    "channel": ch_num,
                    "time_s": round(actual_start + p.time_offset_s, 6),
                    "correlation": round(p.normalized_correlation, 3),
                    "cfo_hz": round(p.cfo_hz, 1),
                    "snr_db": round(p.snr_estimate_db, 1),
                })

            for p in preambles[:50]:
                frame = demodulate_frame(
                    shifted, p.sample_offset, args.sample_rate, p.cfo_hz,
                )
                if frame is None:
                    continue

                frame_dict = {
                    "channel": ch_num,
                    "time_s": round(actual_start + frame.time_offset_s, 6),
                    "rate_mbps": frame.rate_mbps,
                    "length_bytes": frame.length_bytes,
                    "modulation": frame.modulation,
                    "frame_type": frame.frame_type,
                    "frame_subtype": frame.frame_subtype,
                    "bssid": frame.bssid,
                    "src_addr": frame.src_addr,
                    "ssid": frame.ssid,
                    "beacon_interval_ms": frame.beacon_interval_ms,
                    "is_espressif": frame.is_espressif,
                    "is_elrs_pattern": frame.is_elrs_pattern,
                    "demod_quality": frame.demod_quality,
                }
                results["frames"].append(frame_dict)

                esp = " [ESPRESSIF]" if frame.is_espressif else ""
                elrs = " [ELRS]" if frame.is_elrs_pattern else ""
                ssid_str = f" SSID={frame.ssid!r}" if frame.ssid else ""
                print(f"    {frame.frame_subtype}: BSSID={frame.bssid} "
                      f"{frame.rate_mbps}Mbps {frame.length_bytes}B"
                      f"{ssid_str}{esp}{elrs} [{frame.demod_quality}]",
                      file=sys.stderr)

    if args.detect_esp:
        print("\n[*] Running ESP co-processor detection...", file=sys.stderr)
        target_ch = args.channel[0] if len(args.channel) == 1 else 0
        esp = detect_esp_coprocessor(
            iq, args.sample_rate, args.center_hz, target_ch,
        )
        results["esp_detection"] = {
            "detected": esp.detected,
            "confidence": esp.confidence,
            "channel": esp.channel,
            "bssid": esp.bssid,
            "ssid": esp.ssid,
            "beacon_interval_ms": esp.beacon_interval_ms,
            "beacon_count": esp.beacon_count,
            "preamble_count": esp.preamble_count,
            "energy_snr_db": round(esp.energy_snr_db, 1),
            "evidence": esp.evidence,
        }
        print(f"    Result: {'DETECTED' if esp.detected else 'not detected'} "
              f"(confidence: {esp.confidence})", file=sys.stderr)
        for e in esp.evidence:
            print(f"      - {e}", file=sys.stderr)

    output_json = json.dumps(results, indent=2, default=str)
    if args.json:
        print(output_json)
    if args.output:
        args.output.write_text(output_json + "\n")
        print(f"\n[*] Results saved to {args.output}", file=sys.stderr)

    active = sum(1 for r in results["channels"].values() if r["active"])
    n_frames = len(results["frames"])
    n_preambles = len(results["preambles"])
    print(f"\n[*] Summary: {active} active channel(s), "
          f"{n_preambles} preamble(s), {n_frames} decoded frame(s)",
          file=sys.stderr)

    if not args.json:
        summary = {
            "active_channels": active,
            "preambles_detected": n_preambles,
            "frames_decoded": n_frames,
            "tier": tier,
            "esp_detected": results.get("esp_detection", {}).get("detected")
            if results.get("esp_detection") else None,
        }
        print(json.dumps(summary, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
