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
paths and never to stdout. ``--verbose`` adds sanitized per-frame metadata.

Requires: NumPy, SciPy (for IQ/DSP), plus tools/elrs_core (vendored).

Parameters for the live TinyHawk III (Betaflight 4.4.2, OTA v3):
  SF=7, BW=812500 Hz, SX1280 LI mode 0x07 (CR=4/8), implicit header,
  21 ranked payload symbols, no HW CRC, 8-byte standard OTA packet,
  CRC14, sync channel 41.

Usage:
  # Scan a single-channel capture for ELRS frames. Candidate extraction is
  # delegated to the capture-validated elrs-phy-scan implementation:
  python tools/elrs-passive-pipeline.py \\
      --capture work/captures/tinyhawk_2441MHz_4Msps.iq \\
      --sample-rate 4000000 --center-hz 2441000000 \\
      --channel-hz 2441378800

  # Decode and independently cross-check tracked ranked candidates:
  python tools/elrs-passive-pipeline.py \\
      --ranked-jsonl captures/oct15-ranked-2026-10-16.jsonl \\
      captures/tinyhawk-rc-retry-full-phy-2026-10-16.jsonl \\
      --cross-check-reference

  # Offline FHSS correlation from previously decoded frames:
  python tools/elrs-passive-pipeline.py \\
      --frames-jsonl work/decoded-frames.jsonl \\
      --uid-hex 001122334455 --ota-version 3 --correlate-fhss

Python 3.10+. For authorized CTF research only.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import importlib.util
import json
import math
import runpy
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from elrs_core.fhss import (
    DOMAINS, DEFAULT_2G4_DOMAIN, FhssDomain, build_sequence,
)
from elrs_core.ota import (
    PACKET_TYPE_SYNC, PACKET_TYPE_RCDATA, PACKET_TYPE_DATA,
    decode_std,
)
from elrs_core.v3_compat import (
    V3_SYNC_CHANNEL_80,
    v3_validate_crc, v3_decode_sync, v3_build_sequence, uid_crc_init,
)

try:
    import numpy as _numpy
    import scipy as _scipy
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
ELRS_PAYLOAD_BYTES = 8  # standard OTA4 packet
ELRS_CR = 4  # SX1280 long-interleaving mode 0x07 is CR 4/8
LONG_INTERLEAVE = True
PAYLOAD_SYMBOLS = 21
PACKETS_PER_HOP = 4
BIN_OFFSETS = (0, 1)

DOMAIN = DOMAINS[DEFAULT_2G4_DOMAIN]
WORK_ROOT = TOOLS.parent / "work"

_V3_CHECKER = runpy.run_path(str(TOOLS / "elrs-v3-frame-check.py"),
                             run_name="elrs_v3_frame_check")
check_v3_frame = _V3_CHECKER["check_frame"]


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
    bin_offset: int = -1
    crc_basis: str = ""
    confidence: str = "CANDIDATE"
    source: str = ""
    source_line: int = 0

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
        if self.bin_offset >= 0:
            d["bin_offset"] = self.bin_offset
        if self.crc_basis:
            d["crc_basis"] = self.crc_basis
        if self.sync_fields:
            d["sync_fields"] = copy.deepcopy(self.sync_fields)
        if self.rc_channels:
            d["rc_channels"] = list(self.rc_channels)
        if self.raw_hex:
            d["raw_hex"] = self.raw_hex
        if self.symbols:
            d["symbols"] = list(self.symbols)
        if self.source:
            d["source"] = self.source
        if self.source_line:
            d["source_line"] = self.source_line
        return d

    def to_sanitized_dict(self) -> dict:
        """Strip UID material for stdout reporting."""
        d = self.to_dict()
        d.pop("raw_hex", None)
        d.pop("symbols", None)
        d.pop("source", None)
        d.pop("source_line", None)
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
    msp_frames: int = 0
    tlm_frames: int = 0
    data_frames: int = 0
    fec_errors_total: int = 0
    fhss_correlation: dict = field(default_factory=dict)
    reference_cross_check: dict = field(default_factory=dict)
    frames: list[DecodedFrame] = field(default_factory=list)
    input_provenance: list[dict] = field(default_factory=list)
    ota_version: int = 3
    evidence_class: str = "SELF-CONSISTENT"
    duration_s: float = 0.0

    def summary(self) -> dict:
        return {
            "capture_file": Path(self.capture_file).name if self.capture_file else "",
            "capture_sha256": self.capture_sha256[:16] + "..." if self.capture_sha256 else "",
            "sample_rate": self.sample_rate,
            "center_freq_hz": self.center_freq_hz,
            "channels_scanned": len(self.channels_scanned),
            "total_candidates": self.total_candidates,
            "crc_valid_frames": self.crc_valid_frames,
            "sync_frames": self.sync_frames,
            "rc_frames": self.rc_frames,
            "msp_frames": self.msp_frames,
            "tlm_frames": self.tlm_frames,
            "data_frames": self.data_frames,
            "fec_errors_total": self.fec_errors_total,
            "fhss_correlation": self.fhss_correlation,
            "reference_cross_check": self.reference_cross_check,
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
}


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of the complete capture file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


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
    type_names = ({0: "RC", 1: "MSP", 2: "SYNC", 3: "TLM"}
                  if ota_version <= 3 else
                  {0: "RCDATA", 1: "DATA", 2: "SYNC", 3: "RESERVED"})
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
            if ota_version <= 3:
                result["channels"] = _rc_channels(payload)
                return result
            pkt = decode_std(payload)
            result["channels"] = pkt.channels
            result["is_armed"] = pkt.is_armed
            result["switch_index"] = pkt.switch_index
        except Exception:
            result["decode_error"] = "rc decode failed"
    elif pkt_type in (PACKET_TYPE_DATA, 3):
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
    observations_set: set[tuple[int, float]] = set()
    for f in frames:
        if f.channel_index >= 0 and f.crc_valid:
            if (f.channel_index >= domain.freq_count or
                    not math.isfinite(f.timestamp_s) or f.timestamp_s < 0):
                raise ValueError("FHSS observations require a valid channel and timestamp")
            observations_set.add((f.channel_index, f.timestamp_s))
    observations = sorted(observations_set, key=lambda item: item[1])

    if not observations:
        return {
            "status": "NO_OBSERVATIONS",
            "message": "No CRC-valid frames with channel indices",
        }
    if len(observations) < 8 or len({channel for channel, _ in observations}) < 2:
        return {
            "status": "INSUFFICIENT_OBSERVATIONS",
            "quality": "NONE",
            "total_observations": len(observations),
            "unique_channels": len({channel for channel, _ in observations}),
            "message": "Need at least 8 frames spanning 2 channels",
        }

    # Try to find a consistent FHSS position offset. The 150 Hz mode sends
    # four packets per frequency before advancing the FHSS pointer.
    hop_interval_s = PACKETS_PER_HOP / 150.0

    best_offset = -1
    best_packet_phase = -1
    best_matches = 0
    total_obs = len(observations)
    first_timestamp = min(timestamp for _, timestamp in observations)

    # Capture time zero need not align with the first packet of a four-packet
    # hop. Search the four possible packet phases as well as sequence offset.
    for packet_phase in range(PACKETS_PER_HOP):
        for offset in range(seq_len):
            matches = 0
            for ch_idx, ts in observations:
                packet_count = round((ts - first_timestamp) * 150.0)
                hop_count = (packet_count + packet_phase) // PACKETS_PER_HOP
                expected_pos = (offset + hop_count) % seq_len
                if sequence[expected_pos] == ch_idx:
                    matches += 1
            if matches > best_matches:
                best_matches = matches
                best_offset = offset
                best_packet_phase = packet_phase

    match_pct = (best_matches / total_obs * 100) if total_obs > 0 else 0

    # Assign FHSS positions to frames using the best offset
    if best_offset >= 0:
        for f in frames:
            if f.channel_index >= 0:
                packet_count = round((f.timestamp_s - first_timestamp) * 150.0)
                hop_count = (packet_count + best_packet_phase) // PACKETS_PER_HOP
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
    sync_observations = [item for item in observations if item[0] == sync_ch]

    return {
        "status": "COMPUTED",
        "quality": quality,
        "best_offset": best_offset,
        "packet_phase": best_packet_phase,
        "matches": best_matches,
        "total_observations": total_obs,
        "match_percent": round(match_pct, 1),
        "sequence_length": seq_len,
        "sync_channel": sync_ch,
        "sync_channel_observations": len(sync_observations),
        "hop_interval_s": hop_interval_s,
        "packets_per_hop": PACKETS_PER_HOP,
    }


# ---------------------------------------------------------------------------
# Capture-ranked candidate decoding
# ---------------------------------------------------------------------------
def _load_script_module(filename: str, module_name: str):
    path = TOOLS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _channel_index(channel_freq_hz: int) -> int:
    return min(range(DOMAIN.freq_count),
               key=lambda index: abs(DOMAIN.channel_freq(index) - channel_freq_hz))


def _row_bins(row: dict) -> list[int]:
    if not isinstance(row, dict):
        raise ValueError("ranked candidate must be a JSON object")
    if "bins" in row:
        bins = row["bins"]
    else:
        payload = row.get("payload_symbols")
        if not isinstance(payload, list) or len(payload) < PAYLOAD_SYMBOLS:
            raise ValueError("ranked candidate is missing 21 payload symbols")
        try:
            bins = [item["relative_bin"] for item in payload[:PAYLOAD_SYMBOLS]]
        except (KeyError, TypeError) as exc:
            raise ValueError("ranked candidate has invalid payload symbols") from exc
    if (not isinstance(bins, list) or len(bins) < PAYLOAD_SYMBOLS
            or any(type(value) is not int or not 0 <= value < 128
                   for value in bins[:PAYLOAD_SYMBOLS])):
        raise ValueError("payload bins must be 21 integers in 0..127")
    return bins[:PAYLOAD_SYMBOLS]


def _row_timestamp(row: dict) -> float:
    if not isinstance(row, dict):
        raise ValueError("capture row must be a JSON object")
    value = row.get("preamble_start_seconds", row.get("timestamp_s", 0.0))
    if value is None:
        value = 0.0
    if isinstance(value, bool):
        raise ValueError("capture timestamp must be a finite nonnegative number")
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "capture timestamp must be a finite nonnegative number"
        ) from exc
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("capture timestamp must be a finite nonnegative number")
    return timestamp


def _row_channel_hz(row: dict) -> int:
    if not isinstance(row, dict):
        raise ValueError("capture row must be a JSON object")
    value = row.get("channel_hz", 0)
    if value is None:
        value = 0
    if isinstance(value, bool):
        raise ValueError("channel_hz must be a finite nonnegative integer")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "channel_hz must be a finite nonnegative integer"
        ) from exc
    if (not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer()
            or numeric > 1_000_000_000_000):
        raise ValueError("channel_hz must be a finite nonnegative integer")
    return int(numeric)


def load_ranked_rows(paths: list[Path]) -> list[dict]:
    """Load canonical ranked-symbol JSONL without exposing packet bytes."""
    rows: list[dict] = []
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError(
                        f"ranked candidate {path.name}:{line_number} must be an object"
                    )
                bins = _row_bins(item)
                rows.append({
                    "source": str(path.resolve()),
                    "line": line_number,
                    "preamble_start_seconds": _row_timestamp(item),
                    "channel_hz": _row_channel_hz(item),
                    "bins": bins,
                })
    return rows


def _decode_possibilities(row: dict) -> list[tuple[int, bytes, int]]:
    if not HAS_LORA or not hasattr(lora_decode, "decode_sx1280_li"):
        raise RuntimeError("capture-validated SX1280 LI decoder is unavailable")
    possibilities = []
    bins = _row_bins(row)
    for offset in BIN_OFFSETS:
        adjusted = [(value + offset) & 127 for value in bins]
        decoded = lora_decode.decode_sx1280_li(adjusted, ELRS_PAYLOAD_BYTES)
        if decoded is not None:
            packet, corrected = decoded
            possibilities.append((offset, packet, corrected))
    return possibilities


def _rc_channels(packet: bytes) -> list[int]:
    packed = int.from_bytes(packet[1:6], "little")
    return [988 + ((packed >> (10 * channel)) & 1023) for channel in range(4)]


def decode_ranked_rows(rows: list[dict], uid: bytes | None = None,
                       channel_freq_hz: int = 0) -> list[DecodedFrame]:
    """Decode ranked v3 candidates and transfer CRC context across frames.

    A SYNC packet may self-validate its transmitted CRC suffix, but that does
    not authenticate device identity. Non-SYNC validation uses only a suffix
    that was consistently observed in different SYNC frames unless a complete
    UID was supplied independently.
    """
    candidates = [_decode_possibilities(row) for row in rows]
    suffixes: Counter[tuple[int, int]] = Counter()
    modes: Counter[str] = Counter()
    for possibilities in candidates:
        for _, packet, _ in possibilities:
            verdict = check_v3_frame(packet)
            if verdict["type"] == "SYNC" and verdict["crc"] == "pass":
                suffixes[(packet[5], packet[6])] += 1
                modes[verdict["fields"]["switch_mode"]] += 1

    if len(suffixes) > 1:
        raise ValueError("CRC-valid SYNC frames disagree on UID suffix")
    if len(modes) > 1:
        raise ValueError("CRC-valid SYNC frames disagree on switch mode")

    consensus = suffixes.most_common(1)[0][0] if suffixes else None
    switch_mode = modes.most_common(1)[0][0] if modes else None
    if uid is not None and consensus is not None and tuple(uid[4:6]) != consensus:
        raise ValueError("provided UID suffix conflicts with decoded SYNC frames")
    verifier_uid = uid if uid is not None else (
        bytes((0, 0, 0, 0, *consensus)) if consensus is not None else None)
    suffix_basis = ("provided_uid" if uid is not None else
                    "cross_frame_sync_suffix" if sum(suffixes.values()) >= 2 else
                    "single_sync_suffix" if consensus is not None else
                    "unavailable")

    frames: list[DecodedFrame] = []
    type_map = {"RC": "RC", "MSP": "MSP", "SYNC": "SYNC", "TLM": "TLM"}
    for row, possibilities in zip(rows, candidates):
        accepted = []
        for corrected_offset, packet, corrected in possibilities:
            kind = packet[0] & 3
            if kind == PACKET_TYPE_SYNC:
                verdict = check_v3_frame(packet, uid=uid)
                if verdict["crc"] != "pass":
                    continue
                if uid is not None and not verdict.get("uid_18bit_match", False):
                    continue
                if consensus is not None and (packet[5], packet[6]) != consensus:
                    continue
                basis = "self_derived_sync_suffix" if uid is None else "provided_uid"
            elif verifier_uid is not None:
                verdict = check_v3_frame(packet, uid=verifier_uid,
                                         switch_mode=switch_mode)
                if verdict["crc"] != "pass":
                    continue
                basis = suffix_basis
            else:
                continue
            accepted.append((corrected, corrected_offset, packet, verdict, basis))

        selected = min(accepted, default=None, key=lambda item: (item[0], item[1]))
        fallback = min(possibilities, default=None,
                       key=lambda item: (item[2], item[0]))
        packet = selected[2] if selected else (fallback[1] if fallback else b"")
        corrected = selected[0] if selected else (fallback[2] if fallback else 0)
        offset = selected[1] if selected else (fallback[0] if fallback else -1)
        verdict = selected[3] if selected else None
        frame_type = type_map.get(verdict["type"] if verdict else
                                  ("RC", "MSP", "SYNC", "TLM")[packet[0] & 3]
                                  if packet else "", "UNKNOWN")
        row_channel_hz = _row_channel_hz(row) or channel_freq_hz or 0
        frame = DecodedFrame(
            timestamp_s=_row_timestamp(row),
            channel_freq_hz=row_channel_hz,
            channel_index=_channel_index(row_channel_hz) if row_channel_hz else -1,
            frame_type=frame_type,
            raw_hex=packet.hex(),
            crc_valid=selected is not None,
            fec_errors=corrected,
            symbols=_row_bins(row),
            bin_offset=offset,
            crc_basis=selected[4] if selected else "unresolved",
            confidence="CRC_VALID" if selected else
                       "CRC_UNRESOLVED" if packet else "DECODE_ERROR",
            source=str(row.get("source", "")),
            source_line=int(row.get("line", 0)),
        )
        if selected and verdict is not None:
            if verdict["type"] == "SYNC":
                frame.sync_fields = dict(verdict.get("fields", {}))
            elif verdict["type"] == "RC":
                frame.rc_channels = _rc_channels(packet)
        frames.append(frame)
    return frames


def _reference_cross_check(rows: list[dict], frames: list[DecodedFrame],
                           uid: bytes | None = None) -> dict:
    reference = _load_script_module("elrs-li-offline.py", "elrs_li_reference")
    normalized = [{
        "source": row.get("source", "capture"),
        "line": row.get("line", index + 1),
        "preamble_start_seconds": _row_timestamp(row),
        "bins": _row_bins(row),
    } for index, row in enumerate(rows)]
    expected, summary = reference.analyze(normalized, uid=uid, include_bytes=True)
    matches = 0
    mismatches = 0
    false_positives = 0
    for expected_row, actual in zip(expected, frames):
        if expected_row["status"] == "crc_valid":
            if actual.crc_valid and actual.raw_hex == expected_row["packet_bytes_hex"]:
                matches += 1
            else:
                mismatches += 1
        elif actual.crc_valid:
            false_positives += 1
    return {
        "reference_crc_valid": summary["crc_valid"],
        "byte_matches": matches,
        "byte_mismatches": mismatches,
        "false_positives": false_positives,
        "unresolved": summary["unresolved"],
        "status": "PASS" if mismatches == 0 and false_positives == 0 else "FAIL",
    }


def _populate_result(result: PipelineResult, rows: list[dict], uid: bytes | None,
                     cross_check: bool = True,
                     correlate: bool = False) -> PipelineResult:
    decoded_frames = decode_ranked_rows(rows, uid)
    distinct_sync_observations = {
        (
            _row_timestamp(row),
            _row_channel_hz(row),
            tuple(_row_bins(row)),
        )
        for row, frame in zip(rows, decoded_frames)
        if frame.crc_valid and frame.frame_type == "SYNC"
    }
    result.frames = sorted(decoded_frames, key=lambda frame: frame.timestamp_s)
    result.total_candidates = len(result.frames)
    result.crc_valid_frames = sum(frame.crc_valid for frame in result.frames)
    result.sync_frames = sum(frame.crc_valid and frame.frame_type == "SYNC"
                             for frame in result.frames)
    result.rc_frames = sum(frame.crc_valid and frame.frame_type == "RC"
                           for frame in result.frames)
    result.msp_frames = sum(frame.crc_valid and frame.frame_type == "MSP"
                            for frame in result.frames)
    result.tlm_frames = sum(frame.crc_valid and frame.frame_type == "TLM"
                            for frame in result.frames)
    result.data_frames = result.msp_frames + result.tlm_frames
    result.fec_errors_total = sum(frame.fec_errors for frame in result.frames
                                  if frame.crc_valid)
    if cross_check:
        ordered_rows = sorted(rows, key=_row_timestamp)
        result.reference_cross_check = _reference_cross_check(
            ordered_rows, result.frames, uid)
    if correlate and uid is not None and result.crc_valid_frames:
        result.fhss_correlation = correlate_fhss(result.frames, uid, result.ota_version, DOMAIN)
    no_single_sync_dependents = not any(
        frame.crc_valid and frame.crc_basis == "single_sync_suffix"
        for frame in result.frames)
    independent_crc_context = uid is not None or len(distinct_sync_observations) >= 2
    if (result.crc_valid_frames and independent_crc_context and
            no_single_sync_dependents and
            result.reference_cross_check.get("status") == "PASS"):
        result.evidence_class = "CAPTURE-VALIDATED"
    elif result.crc_valid_frames:
        result.evidence_class = "CRC-VALIDATED"
    else:
        result.evidence_class = "INCONCLUSIVE"
    return result


def run_ranked_pipeline(paths: list[Path], uid: bytes | None = None,
                        cross_check: bool = True,
                        correlate: bool = False) -> PipelineResult:
    t0 = time.monotonic()
    resolved_paths = [path.resolve() for path in paths]
    if len(set(resolved_paths)) != len(resolved_paths):
        raise ValueError("ranked capture paths must be unique")
    rows = load_ranked_rows(paths)
    result = PipelineResult(capture_file=",".join(path.name for path in paths))
    result.input_provenance = [
        {"source": str(path.resolve()), "sha256": file_sha256(path)}
        for path in paths
    ]
    if correlate and len(paths) != 1:
        raise ValueError("FHSS correlation requires one capture timeline")
    _populate_result(result, rows, uid, cross_check, correlate)
    result.duration_s = time.monotonic() - t0
    return result


# ---------------------------------------------------------------------------
# Chunked raw-IQ pipeline
# ---------------------------------------------------------------------------
def run_pipeline(capture_path: Path, sample_rate: int, center_freq_hz: int,
                 fmt: str = "sc16le", uid: bytes | None = None,
                 ota_version: int = 3,
                 channels: list[int] | None = None,
                 channel_freqs: list[int] | None = None,
                 start_s: float = 0.0, duration_s: float | None = None,
                 verbose: bool = False,
                 correlate: bool = False) -> PipelineResult:
    """Scan raw IQ in bounded chunks, then decode and cross-check candidates."""
    if not HAS_DSP:
        raise RuntimeError("NumPy/SciPy required for IQ processing")
    t0 = time.monotonic()
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    if ota_version != 3:
        raise ValueError("capture-ranked decoding currently supports OTA v3 only")
    if fmt not in ("sc16le", "cf32le"):
        raise ValueError("capture-ranked scanning supports sc16le and cf32le")
    if not capture_path.is_file():
        raise ValueError(f"capture does not exist: {capture_path}")

    bytes_per_sample = SAMPLE_FORMATS[fmt][0]
    total_duration = capture_path.stat().st_size / bytes_per_sample / sample_rate
    scan_duration = total_duration - start_s if duration_s is None else duration_s
    if start_s < 0 or scan_duration <= 0 or start_s + scan_duration > total_duration + 1e-6:
        raise ValueError("requested capture window lies outside the IQ file")

    if channel_freqs is None:
        active_channels = channels or [V3_SYNC_CHANNEL_80]
        if len(set(active_channels)) != len(active_channels):
            raise ValueError("scan channel indices must be unique")
        channel_freqs = [int(DOMAIN.channel_freq(index)) for index in active_channels]
    elif len(set(channel_freqs)) != len(channel_freqs):
        raise ValueError("scan channel frequencies must be unique")
    scanner = _load_script_module("elrs-phy-scan.py", "elrs_phy_scan_pipeline")
    rows: list[dict] = []
    scanned_freqs: list[int] = []
    for channel_freq in channel_freqs:
        if abs(channel_freq - center_freq_hz) >= sample_rate / 2:
            if verbose:
                print(f"  Skipping {channel_freq / 1e6:.6f} MHz outside capture span",
                      file=sys.stderr)
            continue
        scanned_freqs.append(channel_freq)
        if verbose:
            print(f"  Scanning {channel_freq / 1e6:.6f} MHz in bounded chunks...",
                  file=sys.stderr)
        found = scanner.scan_file(
            capture_path, float(sample_rate), float(center_freq_hz),
            float(channel_freq), start_s, scan_duration, 0.20, 6.0,
            PAYLOAD_SYMBOLS, fmt,
        )
        for line_number, row in enumerate(found, 1):
            row["source"] = str(capture_path.resolve())
            row["line"] = line_number
        rows.extend(found)
        if verbose:
            print(f"    Found {len(found)} PHY candidates", file=sys.stderr)

    capture_hash = file_sha256(capture_path)
    result = PipelineResult(
        capture_file=capture_path.name,
        capture_sha256=capture_hash,
        sample_rate=sample_rate,
        center_freq_hz=center_freq_hz,
        channels_scanned=[_channel_index(freq) for freq in scanned_freqs],
        ota_version=ota_version,
        input_provenance=[{
            "source": str(capture_path.resolve()),
            "sha256": capture_hash,
        }],
    )
    _populate_result(result, rows, uid, cross_check=True, correlate=correlate)
    result.duration_s = time.monotonic() - t0
    return result


# ---------------------------------------------------------------------------
# Offline FHSS correlation from pre-decoded frames
# ---------------------------------------------------------------------------
def load_frames_jsonl(path: Path) -> list[DecodedFrame]:
    """Load previously decoded frames from JSONL."""
    frames = []
    with open(path, encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if not isinstance(d, dict):
                raise ValueError(f"frame {line_number} must be a JSON object")
            channel_index = d.get("channel_index", -1)
            if (type(channel_index) is not int or channel_index < -1 or
                    channel_index >= DOMAIN.freq_count):
                raise ValueError(
                    f"frame {line_number} channel_index must be -1..{DOMAIN.freq_count - 1}"
                )
            crc_valid = d.get("crc_valid", False)
            if type(crc_valid) is not bool:
                raise ValueError(f"frame {line_number} crc_valid must be boolean")
            frame = DecodedFrame(
                timestamp_s=_row_timestamp(d),
                channel_freq_hz=d.get("channel_freq_hz", 0),
                channel_index=channel_index,
                frame_type=d.get("frame_type", ""),
                raw_hex=d.get("raw_hex", ""),
                crc_valid=crc_valid,
                fec_errors=d.get("fec_errors", 0),
                symbols=d.get("symbols", []),
                sync_fields=d.get("sync_fields", {}),
                rc_channels=d.get("rc_channels", []),
                fhss_position=d.get("fhss_position", -1),
                confidence=d.get("confidence", "UNKNOWN"),
                source=d.get("source", ""),
                source_line=d.get("source_line", 0),
            )
            frames.append(frame)
    return frames


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def private_output(path: Path, *, allow_directory: bool = False) -> Path:
    """Resolve an output below ignored ``work/`` and refuse overwrites."""
    target = path.resolve()
    root = WORK_ROOT.resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("private output must be below the ignored work/ directory")
    if not allow_directory and target.exists():
        raise FileExistsError("output already exists; choose a new private filename")
    return target


def write_full_report(result: PipelineResult, output_path: Path) -> None:
    """Write one valid JSON report with packet bytes below ignored work/."""
    target = private_output(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": result.summary(),
        "capture_sha256_full": result.capture_sha256,
        "input_provenance": copy.deepcopy(result.input_provenance),
        "frames": [frame.to_dict() for frame in result.frames],
    }
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(f"Full report (with packet bytes): {target}", file=sys.stderr)


def write_frames_jsonl(frames: list[DecodedFrame], output_path: Path) -> None:
    """Write private frames to new JSONL below ignored work/."""
    target = private_output(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        for frame in frames:
            stream.write(json.dumps(frame.to_dict(), sort_keys=True) + "\n")


def print_sanitized_report(result: PipelineResult, verbose: bool = False) -> None:
    """Print the sanitized report to stdout (no UID material)."""
    print(json.dumps(result.summary(), indent=2))
    if verbose and result.frames:
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


def binding_phrase_uid(phrase: str) -> bytes:
    """Apply the ExpressLRS build-define UID derivation contract."""
    define = f'-DMY_BINDING_PHRASE="{phrase}"'.encode("utf-8")
    return hashlib.md5(define).digest()[:6]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evidence-grade passive ELRS capture pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Input modes
    input_mode = parser.add_mutually_exclusive_group(required=True)
    input_mode.add_argument("--capture", type=Path, help="IQ capture file path")
    input_mode.add_argument("--ranked-jsonl", type=Path, nargs="+",
                            help="ranked PHY candidate JSONL files")
    input_mode.add_argument("--frames-jsonl", type=Path,
                            help="pre-decoded frames JSONL for FHSS correlation")
    input_grp = parser.add_argument_group("Capture input")
    input_grp.add_argument("--format", default="sc16le",
                           choices=list(SAMPLE_FORMATS),
                           help="Sample format (default: sc16le)")
    input_grp.add_argument("--sample-rate", type=int, help="Sample rate (Hz)")
    input_grp.add_argument("--center-hz", type=int, help="Center frequency (Hz)")

    # Channel selection
    ch_grp = parser.add_mutually_exclusive_group()
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
    identity_grp = parser.add_mutually_exclusive_group()
    identity_grp.add_argument("--uid-hex", type=str,
                              help="UID as hex (e.g. 001122334455)")
    elrs_grp.add_argument("--ota-version", type=int, default=3,
                          help="OTA version (default: 3 for BF 4.4.2)")
    identity_grp.add_argument("--binding-phrase", type=str,
                              help="Binding phrase (derives UID via MD5)")

    # Correlation
    corr_grp = parser.add_argument_group("Correlation")
    corr_grp.add_argument("--correlate-fhss", action="store_true",
                          help="Correlate hop channels with FHSS sequence")
    corr_grp.add_argument("--cross-check-reference", action="store_true",
                          help="compare every accepted byte sequence with elrs-li-offline")

    # Output
    out_grp = parser.add_argument_group("Output")
    out_grp.add_argument("--verbose", action="store_true",
                         help="include sanitized per-frame metadata in stdout")
    out_grp.add_argument("--output-dir", type=Path, default=None,
                         help="Output directory for full report (default: work/pipeline/)")
    out_grp.add_argument("--jsonl-out", type=Path, default=None,
                         help="Write decoded frames to JSONL")

    args = parser.parse_args(argv)

    # Resolve UID
    uid = None
    try:
        if args.binding_phrase:
            uid = binding_phrase_uid(args.binding_phrase)
        elif args.uid_hex:
            uid = parse_uid(args.uid_hex)
    except ValueError as exc:
        parser.error(f"invalid UID: {exc}")
    if args.correlate_fhss and uid is None:
        parser.error("--correlate-fhss requires --uid-hex or --binding-phrase")
    if args.ranked_jsonl and args.ota_version != 3:
        parser.error("ranked capture decoding currently supports OTA v3 only")

    # Determine channels
    channels: list[int] | None = None
    channel_freqs: list[int] | None = None
    if args.scan_channels:
        try:
            channels = [int(c.strip()) for c in args.scan_channels.split(",")]
        except ValueError as exc:
            parser.error(f"invalid --scan-channels: {exc}")
        if any(index < 0 or index >= DOMAIN.freq_count for index in channels):
            parser.error(f"channel indices must be 0..{DOMAIN.freq_count - 1}")
        if len(set(channels)) != len(channels):
            parser.error("--scan-channels values must be unique")
    elif args.channel_hz:
        channel_freqs = [args.channel_hz]
    elif args.scan_all:
        channels = list(range(DOMAIN.freq_count))

    try:
        result: PipelineResult | None = None
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
                channel_freqs=channel_freqs,
                start_s=args.start_seconds,
                duration_s=args.duration_seconds,
                verbose=args.verbose,
                correlate=args.correlate_fhss,
            )
        elif args.ranked_jsonl:
            print(f"Decoding {len(args.ranked_jsonl)} ranked capture file(s)...",
                  file=sys.stderr)
            result = run_ranked_pipeline(
                args.ranked_jsonl, uid=uid,
                cross_check=args.cross_check_reference,
                correlate=args.correlate_fhss,
            )
        elif args.frames_jsonl:
            if uid is None:
                parser.error("--uid-hex or --binding-phrase required for FHSS correlation")
            print(f"Loading frames from {args.frames_jsonl}...", file=sys.stderr)
            frames = load_frames_jsonl(args.frames_jsonl)
            correlation = correlate_fhss(frames, uid, args.ota_version, DOMAIN)
            print(json.dumps(correlation, indent=2))
            return 0

        if result is None:
            return 1
        out_dir = private_output(args.output_dir or WORK_ROOT / "pipeline",
                                 allow_directory=True)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
        write_full_report(result, out_dir / f"pipeline-report-{stamp}.json")
        if args.jsonl_out:
            write_frames_jsonl(result.frames, args.jsonl_out)
        print_sanitized_report(result, verbose=args.verbose)
        return 0 if result.crc_valid_frames else 1
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Passive pipeline error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
