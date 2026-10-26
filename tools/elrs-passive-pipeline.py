#!/usr/bin/env python3
"""Evidence-grade passive ELRS capture pipeline.

End-to-end decode chain for ELRS signals captured by SDR:
  IQ capture → preamble detection → symbol extraction → FEC decode →
  CRC validation → SYNC decode → FHSS hop correlation → validation report.

Each decoded frame records:
  - Actual center frequency (Hz) of the hop channel
  - Timestamp (seconds from capture start)
  - Frame type, decoded fields, CRC status
  - FHSS sequence position (if correlation succeeds)

The sanitized report (stdout) contains aggregate statistics and correlation
results. Packet bytes and UID material are written only to ignored work/
paths, never to tracked files or stdout (unless --verbose is given).

Requires: NumPy, SciPy (for IQ/DSP), plus tools/elrs_core (vendored).

Parameters for the live TinyHawk III (Betaflight 4.4.2, OTA v3):
  SF=7, BW=812500 Hz, CR=4/7, long interleaving, implicit header,
  no HW CRC, 8-byte standard OTA packet, CRC14, sync channel 41.

Usage:
  # Scan a single-channel capture for ELRS frames:
  python tools/elrs-passive-pipeline.py \\
      --capture work/captures/tinyhawk_2441MHz_4Msps.iq \\
      --sample-rate 4000000 --center-hz 2441000000 \\
      --channel-hz 2441378800

  # Scan multiple channels with UID for CRC validation:
  python tools/elrs-passive-pipeline.py \\
      --capture work/captures/wideband.iq \\
      --sample-rate 28000000 --center-hz 2440000000 \\
      --uid-hex 001122334455 --ota-version 3 \\
      --scan-channels 38,39,40,41,42,43

  # Offline FHSS correlation from previously decoded frames:
  python tools/elrs-passive-pipeline.py \\
      --frames-jsonl work/decoded-frames.jsonl \\
      --uid-hex 001122334455 --ota-version 3 --correlate-fhss

Python 3.10+. For authorized CTF research only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from elrs_core.crc import Crc2Byte, ELRS_CRC14_POLY
from elrs_core.fhss import (
    DOMAINS, DEFAULT_2G4_DOMAIN, FhssDomain, build_sequence,
    sync_channel_candidates,
)
from elrs_core.ota import (
    OTA4_PACKET_SIZE, OTA8_PACKET_SIZE,
    PACKET_TYPE_SYNC, PACKET_TYPE_RCDATA, PACKET_TYPE_DATA,
    decode_std, decode_full,
)
from elrs_core.v3_compat import (
    V3_OTA_VERSION_ID, V3_SYNC_CHANNEL_80,
    v3_uid_crc_init, v3_validate_crc, v3_decode_sync, v3_build_sequence,
    uid_crc_init, uid_fhss_seed, sync_channel as v_sync_channel,
)

try:
    import numpy as np
    from scipy.signal import resample_poly
    HAS_DSP = True
except ImportError:
    HAS_DSP = False

try:
    import lora_decode
    HAS_LORA = True
except ImportError:
    HAS_LORA = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BW_HZ = 812_500
SF = 7
N_CHIPS = 1 << SF  # 128
PREAMBLE_SYMBOLS = 12
SYNC_WORD_SYMBOLS = 2
SFD_DOWNCHIRPS = 2.25
ELRS_PAYLOAD_BYTES = 8  # standard OTA4 packet
ELRS_CR = 3  # CR 4/7 for 150Hz rate
LONG_INTERLEAVE = True

DOMAIN = DOMAINS[DEFAULT_2G4_DOMAIN]
WORK_ROOT = TOOLS.parent / "work"


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class DecodedFrame:
    """A single decoded ELRS frame with metadata."""
    timestamp_s: float = 0.0
    channel_freq_hz: int = 0
    channel_index: int = -1
    frame_type: str = ""
    raw_hex: str = ""
    crc_valid: bool = False
    fec_errors: int = 0
    symbols: list[int] = field(default_factory=list)
    sync_fields: dict = field(default_factory=dict)
    rc_channels: list[int] = field(default_factory=list)
    fhss_position: int = -1
    confidence: str = "CANDIDATE"

    def to_dict(self) -> dict:
        d = {
            "timestamp_s": round(self.timestamp_s, 9),
            "channel_freq_hz": self.channel_freq_hz,
            "channel_index": self.channel_index,
            "frame_type": self.frame_type,
            "crc_valid": self.crc_valid,
            "fec_errors": self.fec_errors,
            "confidence": self.confidence,
        }
        if self.fhss_position >= 0:
            d["fhss_position"] = self.fhss_position
        if self.sync_fields:
            d["sync_fields"] = self.sync_fields
        if self.rc_channels:
            d["rc_channels"] = self.rc_channels
        if self.raw_hex:
            d["raw_hex"] = self.raw_hex
        if self.symbols:
            d["symbols"] = self.symbols
        return d

    def to_sanitized_dict(self) -> dict:
        """Strip UID material for stdout reporting."""
        d = self.to_dict()
        d.pop("raw_hex", None)
        d.pop("symbols", None)
        sf = d.get("sync_fields", {})
        for k in ("uid3", "uid4", "uid5", "uid_hex"):
            sf.pop(k, None)
        return d


@dataclass
class PipelineResult:
    """Aggregate result of the pipeline run."""
    capture_file: str = ""
    capture_sha256: str = ""
    sample_rate: int = 0
    center_freq_hz: int = 0
    channels_scanned: list[int] = field(default_factory=list)
    total_candidates: int = 0
    crc_valid_frames: int = 0
    sync_frames: int = 0
    rc_frames: int = 0
    data_frames: int = 0
    fec_errors_total: int = 0
    fhss_correlation: dict = field(default_factory=dict)
    frames: list[DecodedFrame] = field(default_factory=list)
    ota_version: int = 3
    evidence_class: str = "SELF-CONSISTENT"
    duration_s: float = 0.0

    def summary(self) -> dict:
        return {
            "capture_file": self.capture_file,
            "capture_sha256": self.capture_sha256[:16] + "..." if self.capture_sha256 else "",
            "sample_rate": self.sample_rate,
            "center_freq_hz": self.center_freq_hz,
            "channels_scanned": len(self.channels_scanned),
            "total_candidates": self.total_candidates,
            "crc_valid_frames": self.crc_valid_frames,
            "sync_frames": self.sync_frames,
            "rc_frames": self.rc_frames,
            "data_frames": self.data_frames,
            "fec_errors_total": self.fec_errors_total,
            "fhss_correlation": self.fhss_correlation,
            "ota_version": self.ota_version,
            "evidence_class": self.evidence_class,
            "duration_s": round(self.duration_s, 3),
        }


# ---------------------------------------------------------------------------
# IQ file reading
# ---------------------------------------------------------------------------
SAMPLE_FORMATS = {
    "sc16le": (4, "<hh"),
    "cf32le": (8, "<ff"),
    "cu8": (2, "BB"),
}


def read_iq_block(path: Path, fmt: str, offset_samples: int, count: int,
                  sample_rate: int) -> "np.ndarray":
    """Read a block of IQ samples from a raw capture file."""
    if not HAS_DSP:
        raise RuntimeError("NumPy/SciPy required for IQ processing")

    bps, struct_fmt = SAMPLE_FORMATS[fmt]
    file_offset = offset_samples * bps

    with open(path, "rb") as f:
        f.seek(file_offset)
        raw = f.read(count * bps)

    n_read = len(raw) // bps
    if n_read == 0:
        return np.array([], dtype=np.complex64)

    if fmt == "sc16le":
        pairs = np.frombuffer(raw[:n_read * bps], dtype=np.int16).reshape(-1, 2)
        iq = (pairs[:, 0] + 1j * pairs[:, 1]).astype(np.complex64) / 32768.0
    elif fmt == "cf32le":
        iq = np.frombuffer(raw[:n_read * bps], dtype=np.complex64)
    elif fmt == "cu8":
        pairs = np.frombuffer(raw[:n_read * bps], dtype=np.uint8).reshape(-1, 2)
        iq = ((pairs[:, 0] - 127.5) + 1j * (pairs[:, 1] - 127.5)).astype(np.complex64) / 128.0
    else:
        raise ValueError(f"Unknown format: {fmt}")

    return iq


def file_sha256(path: Path, max_bytes: int = 64 * 1024 * 1024) -> str:
    """SHA-256 of the first max_bytes of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        remaining = max_bytes
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# LoRa chirp generation and symbol extraction
# ---------------------------------------------------------------------------
def _base_upchirp(n: int = N_CHIPS) -> "np.ndarray":
    """Generate a base upchirp of N chips at baseband rate = BW."""
    t = np.arange(n, dtype=np.float64) / n
    phase = 2.0 * np.pi * (0.5 * n * t * t - 0.5 * n * t)
    return np.exp(1j * phase).astype(np.complex64)


def _base_downchirp(n: int = N_CHIPS) -> "np.ndarray":
    return np.conj(_base_upchirp(n))


def frequency_shift(iq: "np.ndarray", shift_hz: float,
                    sample_rate: float) -> "np.ndarray":
    """Shift IQ by shift_hz."""
    t = np.arange(len(iq), dtype=np.float64) / sample_rate
    return iq * np.exp(-2j * np.pi * shift_hz * t).astype(np.complex64)


def resample_to_bw(iq: "np.ndarray", sample_rate: int,
                   target_rate: int = BW_HZ) -> "np.ndarray":
    """Resample IQ to the LoRa baseband rate (= BW)."""
    from fractions import Fraction
    ratio = Fraction(target_rate, sample_rate)
    if ratio == 1:
        return iq
    return resample_poly(iq, ratio.numerator, ratio.denominator).astype(np.complex64)


def detect_preamble(baseband: "np.ndarray", threshold: float = 0.6,
                    min_peaks: int = 6) -> list[int]:
    """Find preamble start positions via upchirp correlation.

    Returns sample offsets (at baseband rate) where preambles are detected.
    """
    ref = _base_upchirp()
    n = len(ref)
    if len(baseband) < n * min_peaks:
        return []

    # Sliding correlation with the upchirp
    positions = []
    stride = n // 4
    peak_threshold = threshold * n

    for start in range(0, len(baseband) - n * PREAMBLE_SYMBOLS, stride):
        # Correlate first few upchirps
        corr_sum = 0.0
        for k in range(min_peaks):
            seg = baseband[start + k * n:start + (k + 1) * n]
            if len(seg) < n:
                break
            corr = abs(np.dot(seg, np.conj(ref)))
            corr_sum += corr

        avg_corr = corr_sum / min_peaks
        if avg_corr > peak_threshold:
            positions.append(start)

    # Deduplicate nearby detections
    if not positions:
        return []

    deduped = [positions[0]]
    for p in positions[1:]:
        if p - deduped[-1] > n * 2:
            deduped.append(p)

    return deduped


def extract_symbols(baseband: "np.ndarray", preamble_start: int,
                    n_payload_symbols: int = 16) -> list[int]:
    """Extract payload symbols after the preamble/SFD.

    Symbol values are raw FFT peak bins (Gray-coded).
    """
    n = N_CHIPS
    # Skip: preamble (12) + sync word (2) + SFD downchirps (2.25)
    payload_start = preamble_start + int((PREAMBLE_SYMBOLS + SYNC_WORD_SYMBOLS + SFD_DOWNCHIRPS) * n)

    downchirp = _base_downchirp()
    symbols = []

    for i in range(n_payload_symbols):
        seg_start = payload_start + i * n
        seg_end = seg_start + n
        if seg_end > len(baseband):
            break

        seg = baseband[seg_start:seg_end]
        # Dechirp: multiply by downchirp, then FFT
        dechirped = seg * downchirp
        spectrum = np.abs(np.fft.fft(dechirped))
        peak_bin = int(np.argmax(spectrum))
        symbols.append(peak_bin)

    return symbols


def extract_preamble_sync_word(baseband: "np.ndarray",
                               preamble_start: int) -> tuple[int, int]:
    """Extract the two LoRa sync-word symbols after the preamble.

    Returns (sw0, sw1) as bin indices.
    """
    n = N_CHIPS
    downchirp = _base_downchirp()
    sw_start = preamble_start + PREAMBLE_SYMBOLS * n

    syms = []
    for i in range(2):
        seg = baseband[sw_start + i * n:sw_start + (i + 1) * n]
        if len(seg) < n:
            return (-1, -1)
        dechirped = seg * downchirp
        spectrum = np.abs(np.fft.fft(dechirped))
        syms.append(int(np.argmax(spectrum)))

    return (syms[0], syms[1])


# ---------------------------------------------------------------------------
# ELRS CRC validation
# ---------------------------------------------------------------------------
def try_crc_validation(payload: bytes, uid: bytes | None,
                       ota_version: int = 3) -> bool:
    """Attempt ELRS CRC validation on decoded payload bytes."""
    if len(payload) != ELRS_PAYLOAD_BYTES:
        return False

    if uid is None:
        return False

    if ota_version <= 3:
        return v3_validate_crc(payload, uid)
    else:
        crc_init = uid_crc_init(uid, ota_version)
        pkt_type = payload[0] & 0x03
        from elrs_core.ota import validate_crc
        return validate_crc(payload, False, crc_init, 0, pkt_type)


def decode_elrs_frame(payload: bytes, uid: bytes | None,
                      ota_version: int = 3) -> dict:
    """Decode an ELRS packet payload and return field dict."""
    if len(payload) < ELRS_PAYLOAD_BYTES:
        return {"error": "too short"}

    pkt_type = payload[0] & 0x03
    type_names = {0: "RCDATA", 1: "DATA", 2: "SYNC", 3: "RESERVED"}
    result = {"type": type_names.get(pkt_type, str(pkt_type))}

    if pkt_type == PACKET_TYPE_SYNC and ota_version <= 3:
        try:
            sync = v3_decode_sync(payload)
            result["sync"] = {
                "fhss_index": sync.fhss_index,
                "nonce": sync.nonce,
                "rate_index": sync.rate_index,
                "switch_enc_mode": sync.switch_enc_mode,
                "new_tlm_ratio": sync.new_tlm_ratio,
                "uid3": sync.uid3,
                "uid4": sync.uid4,
                "uid5": sync.uid5,
            }
        except Exception:
            result["decode_error"] = "sync decode failed"
    elif pkt_type == PACKET_TYPE_SYNC:
        try:
            pkt = decode_std(payload)
            if pkt.sync:
                result["sync"] = {
                    "fhss_index": pkt.sync.fhss_index,
                    "nonce": pkt.sync.nonce,
                    "rf_rate_enum": pkt.sync.rf_rate_enum,
                    "uid4": pkt.sync.uid4,
                    "uid5": pkt.sync.uid5,
                }
        except Exception:
            result["decode_error"] = "sync decode failed"
    elif pkt_type == PACKET_TYPE_RCDATA:
        try:
            pkt = decode_std(payload)
            result["channels"] = pkt.channels
            result["is_armed"] = pkt.is_armed
            result["switch_index"] = pkt.switch_index
        except Exception:
            result["decode_error"] = "rc decode failed"
    elif pkt_type == PACKET_TYPE_DATA:
        result["data_hex"] = payload[1:7].hex()

    return result


# ---------------------------------------------------------------------------
# FHSS hop correlation
# ---------------------------------------------------------------------------
def correlate_fhss(frames: list[DecodedFrame], uid: bytes,
                   ota_version: int = 3,
                   domain: FhssDomain | None = None) -> dict:
    """Correlate observed hop channels against the FHSS sequence.

    For each frame with a known channel_index, check if it appears at a
    consistent position in the candidate FHSS sequence.
    """
    if domain is None:
        domain = DOMAIN

    if ota_version <= 3:
        sequence = v3_build_sequence(uid, domain.freq_count)
        sync_ch = V3_SYNC_CHANNEL_80
    else:
        from elrs_core.fhss import uid_seed as fhss_uid_seed
        seed = fhss_uid_seed(uid)
        sync_ch = domain.freq_count // 2
        sequence = build_sequence(seed, domain.freq_count, sync_ch)

    seq_len = len(sequence)

    # Collect (channel_index, timestamp) pairs
    observations = []
    for f in frames:
        if f.channel_index >= 0 and f.crc_valid:
            observations.append((f.channel_index, f.timestamp_s))

    if not observations:
        return {
            "status": "NO_OBSERVATIONS",
            "message": "No CRC-valid frames with channel indices",
        }

    # Try to find a consistent FHSS position offset
    # The FHSS pointer advances by 1 per hop interval (~6.67ms for 150Hz)
    hop_interval_s = 1.0 / 150.0  # 150Hz rate

    best_offset = -1
    best_matches = 0
    total_obs = len(observations)

    # For each possible sequence offset, count how many observations match
    for offset in range(seq_len):
        matches = 0
        for ch_idx, ts in observations:
            # Expected sequence position at this timestamp
            hop_count = int(ts / hop_interval_s)
            expected_pos = (offset + hop_count) % seq_len
            expected_ch = sequence[expected_pos]
            if expected_ch == ch_idx:
                matches += 1
        if matches > best_matches:
            best_matches = matches
            best_offset = offset

    match_pct = (best_matches / total_obs * 100) if total_obs > 0 else 0

    # Assign FHSS positions to frames using the best offset
    if best_offset >= 0:
        for f in frames:
            if f.channel_index >= 0:
                hop_count = int(f.timestamp_s / hop_interval_s)
                f.fhss_position = (best_offset + hop_count) % seq_len

    # Classify correlation quality
    if match_pct >= 90:
        quality = "STRONG"
    elif match_pct >= 70:
        quality = "MODERATE"
    elif match_pct >= 40:
        quality = "WEAK"
    else:
        quality = "NONE"

    # Check sync channel consistency
    sync_observations = [f for f in frames if f.channel_index == sync_ch and f.crc_valid]

    return {
        "status": "COMPUTED",
        "quality": quality,
        "best_offset": best_offset,
        "matches": best_matches,
        "total_observations": total_obs,
        "match_percent": round(match_pct, 1),
        "sequence_length": seq_len,
        "sync_channel": sync_ch,
        "sync_channel_observations": len(sync_observations),
        "hop_interval_s": hop_interval_s,
    }


# ---------------------------------------------------------------------------
# Single-channel decode pass
# ---------------------------------------------------------------------------
def decode_channel(baseband: "np.ndarray", channel_freq_hz: int,
                   channel_index: int, capture_offset_s: float,
                   uid: bytes | None, ota_version: int = 3,
                   verbose: bool = False) -> list[DecodedFrame]:
    """Run the full decode chain on one channel's baseband IQ."""
    frames = []

    # Detect preambles
    preamble_positions = detect_preamble(baseband)

    for pos in preamble_positions:
        timestamp = capture_offset_s + pos / BW_HZ

        # Extract LoRa sync word
        sw0, sw1 = extract_preamble_sync_word(baseband, pos)

        # Extract payload symbols
        symbols = extract_symbols(baseband, pos, n_payload_symbols=16)

        frame = DecodedFrame(
            timestamp_s=timestamp,
            channel_freq_hz=channel_freq_hz,
            channel_index=channel_index,
            symbols=symbols,
        )

        if len(symbols) < 16:
            frame.confidence = "PARTIAL"
            frame.frame_type = "INCOMPLETE"
            frames.append(frame)
            continue

        # Attempt LoRa decode (symbols → bytes)
        if HAS_LORA:
            try:
                payload, fec_errors = lora_decode.decode_payload(
                    symbols, sf=SF, cr=ELRS_CR,
                    payload_len=ELRS_PAYLOAD_BYTES,
                    long_interleave=LONG_INTERLEAVE,
                )
                frame.fec_errors = fec_errors
                frame.raw_hex = payload.hex()

                # ELRS CRC validation
                if uid is not None:
                    frame.crc_valid = try_crc_validation(payload, uid, ota_version)

                # Decode frame fields
                if len(payload) == ELRS_PAYLOAD_BYTES:
                    decoded = decode_elrs_frame(payload, uid, ota_version)
                    frame.frame_type = decoded.get("type", "UNKNOWN")
                    if "sync" in decoded:
                        frame.sync_fields = decoded["sync"]
                    if "channels" in decoded:
                        frame.rc_channels = decoded["channels"]

                if frame.crc_valid:
                    frame.confidence = "CRC_VALID"
                elif fec_errors == 0:
                    frame.confidence = "FEC_CLEAN"
                else:
                    frame.confidence = "CANDIDATE"

            except Exception as e:
                frame.confidence = "DECODE_ERROR"
                frame.frame_type = f"ERROR:{e}"
        else:
            frame.confidence = "SYMBOLS_ONLY"
            frame.frame_type = "UNDECODED"

        frames.append(frame)

    return frames


# ---------------------------------------------------------------------------
# Multi-channel pipeline
# ---------------------------------------------------------------------------
def run_pipeline(capture_path: Path, sample_rate: int, center_freq_hz: int,
                 fmt: str = "sc16le", uid: bytes | None = None,
                 ota_version: int = 3,
                 channels: list[int] | None = None,
                 start_s: float = 0.0, duration_s: float | None = None,
                 verbose: bool = False) -> PipelineResult:
    """Run the full pipeline on a capture file."""
    if not HAS_DSP:
        raise RuntimeError("NumPy/SciPy required for IQ processing")

    result = PipelineResult(
        capture_file=str(capture_path),
        sample_rate=sample_rate,
        center_freq_hz=center_freq_hz,
        ota_version=ota_version,
    )

    t0 = time.monotonic()

    # Hash the capture
    if capture_path.exists():
        result.capture_sha256 = file_sha256(capture_path)

    # Determine which channels to scan
    if channels is None:
        channels = [V3_SYNC_CHANNEL_80] if ota_version <= 3 else [DOMAIN.freq_count // 2]

    result.channels_scanned = channels

    # Calculate file parameters
    bps = SAMPLE_FORMATS[fmt][0]
    file_size = capture_path.stat().st_size
    total_samples = file_size // bps
    start_sample = int(start_s * sample_rate)
    if duration_s is not None:
        block_samples = min(int(duration_s * sample_rate), total_samples - start_sample)
    else:
        block_samples = total_samples - start_sample

    # Process each channel
    all_frames = []

    for ch_idx in channels:
        channel_freq = DOMAIN.channel_freq(ch_idx)
        shift_hz = channel_freq - center_freq_hz

        # Check if channel is within the capture bandwidth
        nyquist = sample_rate / 2
        if abs(shift_hz) > nyquist:
            if verbose:
                print(f"  Channel {ch_idx} ({channel_freq/1e6:.1f} MHz) "
                      f"outside capture bandwidth, skipping", file=sys.stderr)
            continue

        if verbose:
            print(f"  Scanning channel {ch_idx} at {channel_freq/1e6:.4f} MHz "
                  f"(shift {shift_hz/1e3:.1f} kHz)...", file=sys.stderr)

        # Read IQ block
        iq = read_iq_block(capture_path, fmt, start_sample, block_samples, sample_rate)
        if len(iq) == 0:
            continue

        # Frequency shift to channel
        shifted = frequency_shift(iq, shift_hz, sample_rate)

        # Resample to baseband rate (= BW)
        baseband = resample_to_bw(shifted, sample_rate)

        # Decode
        capture_offset_s = start_s
        frames = decode_channel(
            baseband, channel_freq, ch_idx, capture_offset_s,
            uid, ota_version, verbose,
        )
        all_frames.extend(frames)

        if verbose:
            print(f"    Found {len(frames)} candidate frames", file=sys.stderr)

    # Aggregate results
    result.frames = sorted(all_frames, key=lambda f: f.timestamp_s)
    result.total_candidates = len(result.frames)
    result.crc_valid_frames = sum(1 for f in result.frames if f.crc_valid)
    result.sync_frames = sum(1 for f in result.frames if f.frame_type == "SYNC")
    result.rc_frames = sum(1 for f in result.frames if f.frame_type == "RCDATA")
    result.data_frames = sum(1 for f in result.frames if f.frame_type == "DATA")
    result.fec_errors_total = sum(f.fec_errors for f in result.frames)

    # FHSS correlation
    if uid is not None and result.crc_valid_frames > 0:
        result.fhss_correlation = correlate_fhss(
            result.frames, uid, ota_version, DOMAIN,
        )

    # Evidence class
    if result.crc_valid_frames > 0 and result.fhss_correlation.get("quality") in ("STRONG", "MODERATE"):
        result.evidence_class = "PASSIVE-CAPTURE-VALIDATED"
    elif result.crc_valid_frames > 0:
        result.evidence_class = "CRC-VALIDATED"
    else:
        result.evidence_class = "SELF-CONSISTENT"

    result.duration_s = time.monotonic() - t0
    return result


# ---------------------------------------------------------------------------
# Offline FHSS correlation from pre-decoded frames
# ---------------------------------------------------------------------------
def load_frames_jsonl(path: Path) -> list[DecodedFrame]:
    """Load previously decoded frames from JSONL."""
    frames = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            frame = DecodedFrame(
                timestamp_s=d.get("timestamp_s", 0.0),
                channel_freq_hz=d.get("channel_freq_hz", 0),
                channel_index=d.get("channel_index", -1),
                frame_type=d.get("frame_type", ""),
                raw_hex=d.get("raw_hex", ""),
                crc_valid=d.get("crc_valid", False),
                fec_errors=d.get("fec_errors", 0),
                symbols=d.get("symbols", []),
                sync_fields=d.get("sync_fields", {}),
                rc_channels=d.get("rc_channels", []),
                fhss_position=d.get("fhss_position", -1),
                confidence=d.get("confidence", "UNKNOWN"),
            )
            frames.append(frame)
    return frames


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def write_full_report(result: PipelineResult, output_path: Path) -> None:
    """Write the full report with packet bytes to work/."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(json.dumps(result.summary(), indent=2))
        f.write("\n\n--- FRAMES ---\n")
        for frame in result.frames:
            f.write(json.dumps(frame.to_dict()) + "\n")
    print(f"Full report (with packet bytes): {output_path}", file=sys.stderr)


def write_frames_jsonl(frames: list[DecodedFrame], output_path: Path) -> None:
    """Write frames to JSONL for further processing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for frame in frames:
            f.write(json.dumps(frame.to_dict()) + "\n")


def print_sanitized_report(result: PipelineResult) -> None:
    """Print the sanitized report to stdout (no UID material)."""
    print(json.dumps(result.summary(), indent=2))
    if result.frames:
        print(f"\n--- Decoded frames ({len(result.frames)}) ---")
        for frame in result.frames:
            print(json.dumps(frame.to_sanitized_dict()))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_uid(uid_str: str) -> bytes:
    uid_str = uid_str.replace(":", "").replace(" ", "")
    uid = bytes.fromhex(uid_str)
    if len(uid) != 6:
        raise ValueError(f"UID must be 6 bytes, got {len(uid)}")
    return uid


def main():
    parser = argparse.ArgumentParser(
        description="Evidence-grade passive ELRS capture pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Input modes
    input_grp = parser.add_argument_group("Input")
    input_grp.add_argument("--capture", type=Path, help="IQ capture file path")
    input_grp.add_argument("--format", default="sc16le",
                           choices=list(SAMPLE_FORMATS),
                           help="Sample format (default: sc16le)")
    input_grp.add_argument("--sample-rate", type=int, help="Sample rate (Hz)")
    input_grp.add_argument("--center-hz", type=int, help="Center frequency (Hz)")
    input_grp.add_argument("--frames-jsonl", type=Path,
                           help="Pre-decoded frames JSONL (for offline correlation)")

    # Channel selection
    ch_grp = parser.add_argument_group("Channels")
    ch_grp.add_argument("--channel-hz", type=int,
                        help="Single channel frequency (Hz)")
    ch_grp.add_argument("--scan-channels", type=str,
                        help="Comma-separated channel indices to scan")
    ch_grp.add_argument("--scan-all", action="store_true",
                        help="Scan all 80 ISM2G4 channels")

    # Time window
    time_grp = parser.add_argument_group("Time window")
    time_grp.add_argument("--start-seconds", type=float, default=0.0,
                          help="Start offset in seconds")
    time_grp.add_argument("--duration-seconds", type=float, default=None,
                          help="Duration to process (seconds)")

    # ELRS parameters
    elrs_grp = parser.add_argument_group("ELRS parameters")
    elrs_grp.add_argument("--uid-hex", type=str,
                          help="UID as hex (e.g. 001122334455)")
    elrs_grp.add_argument("--ota-version", type=int, default=3,
                          help="OTA version (default: 3 for BF 4.4.2)")
    elrs_grp.add_argument("--binding-phrase", type=str,
                          help="Binding phrase (derives UID via MD5)")

    # Correlation
    corr_grp = parser.add_argument_group("Correlation")
    corr_grp.add_argument("--correlate-fhss", action="store_true",
                          help="Correlate hop channels with FHSS sequence")

    # Output
    out_grp = parser.add_argument_group("Output")
    out_grp.add_argument("--verbose", action="store_true",
                         help="Include packet bytes in stdout")
    out_grp.add_argument("--output-dir", type=Path, default=None,
                         help="Output directory for full report (default: work/pipeline/)")
    out_grp.add_argument("--jsonl-out", type=Path, default=None,
                         help="Write decoded frames to JSONL")

    args = parser.parse_args()

    # Resolve UID
    uid = None
    if args.binding_phrase:
        uid = hashlib.md5(args.binding_phrase.encode()).digest()[:6]
    elif args.uid_hex:
        uid = parse_uid(args.uid_hex)

    # Determine channels
    channels = None
    if args.scan_channels:
        channels = [int(c.strip()) for c in args.scan_channels.split(",")]
    elif args.channel_hz and args.center_hz:
        # Find closest channel index
        for i in range(DOMAIN.freq_count):
            if abs(DOMAIN.channel_freq(i) - args.channel_hz) < 500_000:
                channels = [i]
                break
    elif args.scan_all:
        channels = list(range(DOMAIN.freq_count))

    # Mode 1: IQ capture processing
    if args.capture:
        if not args.sample_rate or not args.center_hz:
            parser.error("--sample-rate and --center-hz required with --capture")

        print(f"Processing {args.capture}...", file=sys.stderr)
        result = run_pipeline(
            capture_path=args.capture,
            sample_rate=args.sample_rate,
            center_freq_hz=args.center_hz,
            fmt=args.format,
            uid=uid,
            ota_version=args.ota_version,
            channels=channels,
            start_s=args.start_seconds,
            duration_s=args.duration_seconds,
            verbose=args.verbose,
        )

        # Write full report to work/
        out_dir = args.output_dir or WORK_ROOT / "pipeline"
        ts = time.strftime("%Y%m%d-%H%M%S")
        write_full_report(result, out_dir / f"pipeline-report-{ts}.json")

        if args.jsonl_out:
            write_frames_jsonl(result.frames, args.jsonl_out)

        # Sanitized stdout report
        print_sanitized_report(result)

    # Mode 2: Offline FHSS correlation from pre-decoded frames
    elif args.frames_jsonl:
        if uid is None:
            parser.error("--uid-hex or --binding-phrase required for FHSS correlation")

        print(f"Loading frames from {args.frames_jsonl}...", file=sys.stderr)
        frames = load_frames_jsonl(args.frames_jsonl)
        print(f"Loaded {len(frames)} frames", file=sys.stderr)

        correlation = correlate_fhss(frames, uid, args.ota_version, DOMAIN)
        print(json.dumps(correlation, indent=2))

    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
