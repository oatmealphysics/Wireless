#!/usr/bin/env python3
"""
SiK Telemetry Radio — GFSK SDR Decoder

Decodes SiK-protocol telemetry radio transmissions (RFD900, HopeRF HM-TRP,
3DR radios) from raw IQ captures or live SDR input.  These radios carry
MAVLink telemetry between drone and ground station on 433 / 915 MHz ISM.

PHY layer:
    Modulation ......  GFSK (Gaussian FSK), BT = 0.5
    Data rates ......  1–250 kbps air rate (64 kbps typical for MAVLink)
    Deviation .......  ±25 kHz (at 64 kbps)
    Channel BW ......  ~25 kHz per hop channel
    Bands ...........  433 MHz (EU), 915 MHz (US/AU)
    FHSS ............  50 channels, ~4 hops/sec at lower data rates
    Channel spacing .  ~25 kHz (915: across ~1 MHz; 433: across ~1.7 MHz)
    Chip: Si1000/Si1060 (Silicon Labs EZRadioPRO)

Frame format:
    Preamble ........  0xAA 0xAA 0xAA 0xAA  (alternating bits, ≥32 bits)
    Sync word .......  0x36 0x5C  (default; varies by netid)
    Header ..........  1 byte length | 1 byte flags
    Payload .........  0–252 bytes (typically MAVLink v1 or v2 frames)
    CRC .............  2 bytes CRC-16/CCITT (x^16+x^12+x^5+1, init=0xFFFF)

Pipeline:
    IQ → bandpass filter → FM discriminator → clock recovery (M&M) →
    bit slicer → preamble correlator → sync detect → frame extract →
    CRC check → MAVLink parse → JSON output

NO REAL SiK IQ CAPTURE EXISTS YET.  Decoder is validated against synthetic
round-trip encode→decode only.  To produce a valid capture:

    # 915 MHz, 2 Msps, 30s capture with HackRF:
    hackrf_transfer -r sik_915mhz_2msps.raw -f 915000000 -s 2000000 \\
        -l 32 -g 40 -n 60000000

    # Or with RTL-SDR (433 MHz):
    rtl_sdr -f 433000000 -s 2000000 -g 40 -n 60000000 sik_433mhz_2msps.raw

    # Or via sdr_common (any SoapySDR device):
    python3 sik_telemetry_decode.py --live --freq 915e6 --sdr-rate 2e6

    Convert raw int16 to complex64 if needed:
    python3 -c "import numpy as np; d=np.fromfile('capture.raw',dtype=np.int16); \\
      c=(d[0::2]+1j*d[1::2]).astype(np.complex64); c.tofile('capture.cf32')"

Requires: NumPy, SciPy.  SoapySDR for live capture (optional).
For authorized security research only.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np
from scipy.signal import firwin, lfilter


# ---------------------------------------------------------------------------
# CRC-16/CCITT (0x1021, init 0xFFFF, no final XOR)
# ---------------------------------------------------------------------------
def _build_crc16_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
        table.append(crc)
    return table


_CRC16_TABLE = _build_crc16_table()


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc = (_CRC16_TABLE[((crc >> 8) ^ b) & 0xFF] ^ (crc << 8)) & 0xFFFF
    return crc


# ---------------------------------------------------------------------------
# MAVLink v1/v2 minimal parser (no external libraries)
# ---------------------------------------------------------------------------
MAVLINK_V1_STX = 0xFE
MAVLINK_V2_STX = 0xFD


def parse_mavlink(payload: bytes) -> dict | None:
    """Try to parse a MAVLink v1 or v2 frame from the SiK payload."""
    if len(payload) < 8:
        return None

    stx = payload[0]

    if stx == MAVLINK_V1_STX:
        if len(payload) < 8:
            return None
        plen = payload[1]
        if len(payload) < 8 + plen:
            return None
        return {
            "version": 1,
            "payload_len": plen,
            "seq": payload[2],
            "sysid": payload[3],
            "compid": payload[4],
            "msgid": payload[5],
            "payload_hex": payload[6:6 + plen].hex(),
            "checksum": struct.unpack_from("<H", payload, 6 + plen)[0],
        }

    if stx == MAVLINK_V2_STX:
        if len(payload) < 12:
            return None
        plen = payload[1]
        if len(payload) < 12 + plen:
            return None
        msgid = payload[7] | (payload[8] << 8) | (payload[9] << 16)
        return {
            "version": 2,
            "payload_len": plen,
            "incompat_flags": payload[2],
            "compat_flags": payload[3],
            "seq": payload[4],
            "sysid": payload[5],
            "compid": payload[6],
            "msgid": msgid,
            "payload_hex": payload[10:10 + plen].hex(),
            "checksum": struct.unpack_from("<H", payload, 10 + plen)[0],
        }

    return None


# ---------------------------------------------------------------------------
# SiK channel plan
# ---------------------------------------------------------------------------
SIK_BANDS = {
    "915": {"base_freq": 915.0e6, "num_channels": 50, "channel_spacing": 25e3,
            "band_start": 902.0e6, "band_end": 928.0e6},
    "433": {"base_freq": 433.0e6, "num_channels": 50, "channel_spacing": 25e3,
            "band_start": 433.05e6, "band_end": 434.79e6},
}


def sik_channel_freq(band: str, channel: int) -> float:
    """Compute the center frequency for a SiK hop channel."""
    b = SIK_BANDS[band]
    return b["base_freq"] + channel * b["channel_spacing"]


# ---------------------------------------------------------------------------
# IQ file reader
# ---------------------------------------------------------------------------
def load_iq(path: str, fmt: str = "cf32", offset: int = 0,
            count: int = -1) -> np.ndarray:
    """Load IQ samples from a file.

    Formats:
        cf32  — interleaved float32 (numpy complex64)
        cs16  — interleaved int16 (scaled to ±1.0)
        cu8   — interleaved uint8 (RTL-SDR raw, centered at 127.5)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"IQ file not found: {path}")

    if fmt == "cf32":
        raw = np.fromfile(path, dtype=np.complex64, offset=offset * 8,
                          count=count)
        return raw
    elif fmt == "cs16":
        raw = np.fromfile(path, dtype=np.int16,
                          offset=offset * 4,
                          count=count * 2 if count > 0 else -1)
        floats = raw.astype(np.float32) / 32768.0
        return floats[0::2] + 1j * floats[1::2]
    elif fmt == "cu8":
        raw = np.fromfile(path, dtype=np.uint8,
                          offset=offset * 2,
                          count=count * 2 if count > 0 else -1)
        floats = (raw.astype(np.float32) - 127.5) / 127.5
        return floats[0::2] + 1j * floats[1::2]
    else:
        raise ValueError(f"Unknown IQ format: {fmt}")


# ---------------------------------------------------------------------------
# GFSK demodulation pipeline
# ---------------------------------------------------------------------------
class GFSKDemodulator:
    """GFSK demodulation: bandpass → FM discriminator → clock recovery."""

    def __init__(self, sample_rate: float, data_rate: float = 64000.0,
                 deviation: float = 25000.0, channel_bw: float = 80000.0):
        self.sample_rate = sample_rate
        self.data_rate = data_rate
        self.deviation = deviation
        self.samples_per_symbol = sample_rate / data_rate

        nyq = sample_rate / 2.0
        cutoff = min(channel_bw, nyq * 0.95)
        numtaps = max(31, int(sample_rate / data_rate) * 4 | 1)
        if numtaps % 2 == 0:
            numtaps += 1
        self._lpf = firwin(numtaps, cutoff / nyq)

    def bandpass_filter(self, iq: np.ndarray) -> np.ndarray:
        """Low-pass filter the baseband IQ signal."""
        return lfilter(self._lpf, 1.0, iq)

    @staticmethod
    def fm_discriminator(iq: np.ndarray) -> np.ndarray:
        """Polar FM discriminator: instantaneous frequency from IQ phase diff."""
        delayed = np.concatenate(([iq[0]], iq[:-1]))
        product = iq * np.conj(delayed)
        return np.angle(product)

    def clock_recovery(self, freq_samples: np.ndarray) -> np.ndarray:
        """Recover symbol values by averaging the center of each symbol period.

        Segments the FM-discriminator output into symbol-length windows and
        averages the center half of each window (avoids transition edges).
        This is robust for typical SiK SNR levels and avoids the fragility of
        a full M&M loop for offline decoding.
        """
        sps = int(round(self.samples_per_symbol))
        if sps < 2:
            sps = 2
        n_syms = len(freq_samples) // sps
        if n_syms == 0:
            return np.array([])

        reshaped = freq_samples[:n_syms * sps].reshape(n_syms, sps)
        center_start = sps // 4
        center_end = max(center_start + 1, 3 * sps // 4)
        sym_vals = reshaped[:, center_start:center_end].mean(axis=1)
        return sym_vals

    def demodulate(self, iq: np.ndarray) -> np.ndarray:
        """Full demod pipeline: filter → FM discrim → clock recovery."""
        filtered = self.bandpass_filter(iq)
        freq = self.fm_discriminator(filtered)

        # normalize to ±1 range based on expected deviation
        max_dev = 2.0 * np.pi * self.deviation / self.sample_rate
        if max_dev > 0:
            freq = freq / max_dev
        freq = np.clip(freq, -3.0, 3.0)

        recovered = self.clock_recovery(freq)
        return recovered


# ---------------------------------------------------------------------------
# Bit-level frame extraction
# ---------------------------------------------------------------------------
DEFAULT_SYNC_WORD = 0x365C
DEFAULT_PREAMBLE = 0xAAAAAAAA

MAX_FRAME_LEN = 256


def soft_to_bits(soft: np.ndarray) -> np.ndarray:
    """Hard-decision bit slicer."""
    return (soft > 0).astype(np.uint8)


def bits_to_bytes(bits: np.ndarray) -> bytes:
    """Pack bit array (MSB first) into bytes."""
    n = (len(bits) // 8) * 8
    bits = bits[:n]
    packed = np.packbits(bits)
    return bytes(packed)


def find_sync_word(bits: np.ndarray, sync: int = DEFAULT_SYNC_WORD,
                   sync_bits: int = 16, max_errors: int = 1) -> list[int]:
    """Find all positions where the sync word appears (with up to max_errors
    bit errors allowed)."""
    sync_pattern = np.array(
        [(sync >> (sync_bits - 1 - i)) & 1 for i in range(sync_bits)],
        dtype=np.uint8,
    )
    positions = []
    for i in range(len(bits) - sync_bits):
        errors = np.sum(bits[i:i + sync_bits] != sync_pattern)
        if errors <= max_errors:
            positions.append(i)
    return positions


def extract_frames(bits: np.ndarray, sync_word: int = DEFAULT_SYNC_WORD,
                   ) -> list[dict]:
    """Extract SiK frames from a bit stream.

    After sync word: 1 byte length, 1 byte flags, <length> payload, 2 byte CRC.
    """
    sync_positions = find_sync_word(bits, sync_word)
    frames = []

    for pos in sync_positions:
        data_start = pos + 16  # skip 16-bit sync word
        # need at least 4 bytes (len + flags + 2 CRC) after sync
        if data_start + 32 > len(bits):
            continue

        header_bits = bits[data_start:data_start + 16]
        header_bytes = bits_to_bytes(header_bits)
        if len(header_bytes) < 2:
            continue

        frame_len = header_bytes[0]
        flags = header_bytes[1]

        if frame_len > MAX_FRAME_LEN or frame_len == 0:
            continue

        total_bits = 16 + frame_len * 8 + 16  # header + payload + CRC
        if data_start + total_bits > len(bits):
            continue

        frame_bits = bits[data_start:data_start + total_bits]
        frame_bytes = bits_to_bytes(frame_bits)

        payload = frame_bytes[2:2 + frame_len]
        crc_received = struct.unpack_from(">H", frame_bytes, 2 + frame_len)[0]
        crc_computed = crc16_ccitt(frame_bytes[:2 + frame_len])

        frame = {
            "sync_bit_offset": pos,
            "length": frame_len,
            "flags": flags,
            "payload_hex": payload.hex(),
            "crc_received": f"0x{crc_received:04X}",
            "crc_computed": f"0x{crc_computed:04X}",
            "crc_ok": crc_received == crc_computed,
        }

        mavlink = parse_mavlink(payload)
        if mavlink:
            frame["mavlink"] = mavlink

        frames.append(frame)

    return frames


# ---------------------------------------------------------------------------
# Channel scanner (live SDR mode)
# ---------------------------------------------------------------------------
def scan_channels(band: str, dwell_ms: float = 250.0,
                  sdr_args: str = "", gain: float = 40.0,
                  data_rate: float = 64000.0, sample_rate: float = 2e6,
                  sync_word: int = DEFAULT_SYNC_WORD,
                  output=sys.stdout):
    """Scan across SiK hop channels looking for transmissions."""
    try:
        from sdr_common import SDRDevice
    except ImportError:
        print("[!] sdr_common not found; live scanning requires SoapySDR.",
              file=sys.stderr)
        return

    band_info = SIK_BANDS[band]
    demod = GFSKDemodulator(sample_rate, data_rate)
    num_samples = int(sample_rate * dwell_ms / 1000.0)

    with SDRDevice(sdr_args) as sdr:
        sdr.setup_rx(freq=band_info["base_freq"], rate=sample_rate, gain=gain)

        channel = 0
        while True:
            freq = sik_channel_freq(band, channel)
            sdr.dev.setFrequency(0x0, sdr.channel, freq)  # SOAPY_SDR_RX=0
            time.sleep(0.01)

            iq = sdr.receive(num_samples)
            if len(iq) < num_samples // 2:
                channel = (channel + 1) % band_info["num_channels"]
                continue

            soft = demod.demodulate(iq)
            bits = soft_to_bits(soft)
            frames = extract_frames(bits, sync_word)

            for frame in frames:
                frame["channel"] = channel
                frame["freq_mhz"] = freq / 1e6
                frame["timestamp"] = time.time()
                output.write(json.dumps(frame) + "\n")
                output.flush()

            channel = (channel + 1) % band_info["num_channels"]


# ---------------------------------------------------------------------------
# Offline IQ file decode
# ---------------------------------------------------------------------------
def decode_file(path: str, sample_rate: float, data_rate: float = 64000.0,
                deviation: float = 25000.0, iq_format: str = "cf32",
                sync_word: int = DEFAULT_SYNC_WORD,
                channel_bw: float = 80000.0,
                output=sys.stdout) -> list[dict]:
    """Decode SiK frames from an IQ capture file."""
    print(f"[*] Loading IQ file: {path} (format={iq_format})", file=sys.stderr)
    iq = load_iq(path, fmt=iq_format)
    print(f"[*] Loaded {len(iq)} samples ({len(iq)/sample_rate:.3f}s)",
          file=sys.stderr)

    demod = GFSKDemodulator(sample_rate, data_rate, deviation, channel_bw)
    print(f"[*] Demodulating: rate={data_rate/1000:.1f} kbps, "
          f"dev=±{deviation/1000:.1f} kHz, sps={demod.samples_per_symbol:.1f}",
          file=sys.stderr)

    soft = demod.demodulate(iq)
    print(f"[*] Recovered {len(soft)} symbols", file=sys.stderr)

    bits = soft_to_bits(soft)
    frames = extract_frames(bits, sync_word)

    print(f"[*] Found {len(frames)} frame(s) "
          f"({sum(1 for f in frames if f['crc_ok'])} CRC-valid)",
          file=sys.stderr)

    for frame in frames:
        frame["source_file"] = path
        output.write(json.dumps(frame) + "\n")
        output.flush()

    return frames


# ---------------------------------------------------------------------------
# Self-test: synthetic encode → decode round-trip
# ---------------------------------------------------------------------------
def _gfsk_encode_bits(bits: np.ndarray, sample_rate: float,
                      data_rate: float, deviation: float) -> np.ndarray:
    """Synthesize a GFSK-modulated IQ signal from a bit stream (for testing)."""
    sps = int(sample_rate / data_rate)
    # NRZ: 1 → +1, 0 → -1
    nrz = 2.0 * bits.astype(np.float64) - 1.0
    # upsample
    nrz_up = np.repeat(nrz, sps)
    # Gaussian filter (BT=0.5)
    bt = 0.5
    ntaps = 4 * sps
    t = np.arange(-ntaps // 2, ntaps // 2 + 1) / sample_rate
    alpha = np.sqrt(np.log(2) / 2.0) / (bt / data_rate)
    gauss = np.sqrt(2 * np.pi) * alpha * np.exp(-2 * (np.pi * alpha * t) ** 2)
    gauss /= gauss.sum()

    freq_shaped = np.convolve(nrz_up, gauss, mode="same")
    freq_shaped *= 2.0 * np.pi * deviation / sample_rate

    phase = np.cumsum(freq_shaped)
    iq = np.exp(1j * phase).astype(np.complex64)
    return iq


def self_test():
    """Encode a known SiK frame, modulate, demodulate, verify CRC match."""
    print("[*] SiK decoder self-test: synthetic round-trip", file=sys.stderr)

    # build a frame: preamble + sync + header + payload + CRC
    payload = bytes([
        MAVLINK_V1_STX, 9, 0, 1, 1, 0,  # MAVLink v1 heartbeat header
        0, 0, 0, 0,  # custom_mode
        6,            # type: GCS
        8,            # autopilot: invalid
        0,            # base_mode
        0, 0,         # system_status + mavlink_version placeholder
    ])
    frame_len = len(payload)
    flags = 0x00
    header = bytes([frame_len, flags])
    crc_data = header + payload
    crc_val = crc16_ccitt(crc_data)

    preamble_bytes = b'\xAA\xAA\xAA\xAA'
    sync_bytes = struct.pack(">H", DEFAULT_SYNC_WORD)
    crc_bytes = struct.pack(">H", crc_val)

    full_frame = preamble_bytes + sync_bytes + header + payload + crc_bytes

    # convert to bits
    frame_bits = np.unpackbits(np.frombuffer(full_frame, dtype=np.uint8))

    # pad with some silence bits
    padding = np.zeros(200, dtype=np.uint8)
    all_bits = np.concatenate([padding, frame_bits, padding])

    # modulate (2 Msps matches typical SDR capture rate)
    sample_rate = 2000000.0
    data_rate = 64000.0
    deviation = 25000.0

    iq = _gfsk_encode_bits(all_bits, sample_rate, data_rate, deviation)

    # add some noise
    rng = np.random.default_rng(42)
    noise = (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq))) * 0.05
    iq = (iq + noise).astype(np.complex64)

    # demodulate
    demod = GFSKDemodulator(sample_rate, data_rate, deviation)
    soft = demod.demodulate(iq)
    bits = soft_to_bits(soft)

    frames = extract_frames(bits, DEFAULT_SYNC_WORD)

    crc_valid = [f for f in frames if f["crc_ok"]]
    if crc_valid:
        print(f"[+] PASS: {len(crc_valid)} CRC-valid frame(s) recovered",
              file=sys.stderr)
        for f in crc_valid:
            if f.get("mavlink"):
                print(f"    MAVLink v{f['mavlink']['version']} "
                      f"msgid={f['mavlink']['msgid']} "
                      f"sysid={f['mavlink']['sysid']}", file=sys.stderr)
        return True
    else:
        # relaxed check: did we at least find the sync word?
        syncs = find_sync_word(bits, DEFAULT_SYNC_WORD, max_errors=2)
        if syncs:
            print(f"[~] PARTIAL: sync word found at {len(syncs)} position(s) "
                  f"but no CRC-valid frames (clock recovery drift likely)",
                  file=sys.stderr)
        else:
            print("[!] FAIL: no sync words found in demodulated bitstream",
                  file=sys.stderr)
        print(f"    Total bits: {len(bits)}, frames found: {len(frames)}",
              file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="SiK Telemetry Radio GFSK Decoder — SDR IQ → frames",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Decode from IQ file (complex64)
    python3 sik_telemetry_decode.py --iq-file capture.cf32 --sample-rate 2e6

    # Decode from int16 IQ file (HackRF raw)
    python3 sik_telemetry_decode.py --iq-file capture.raw --sample-rate 2e6 --iq-format cs16

    # Decode from RTL-SDR uint8 format
    python3 sik_telemetry_decode.py --iq-file capture.bin --sample-rate 2e6 --iq-format cu8

    # Live scan on 915 MHz band
    python3 sik_telemetry_decode.py --live --band 915 --sdr-rate 2e6

    # Self-test (synthetic encode → decode round-trip)
    python3 sik_telemetry_decode.py --self-test
""",
    )

    parser.add_argument("--iq-file", help="Path to IQ capture file")
    parser.add_argument("--iq-format", default="cf32",
                        choices=["cf32", "cs16", "cu8"],
                        help="IQ sample format (default: cf32)")
    parser.add_argument("--sample-rate", type=float, default=2e6,
                        help="Sample rate in Hz (default: 2e6)")
    parser.add_argument("--data-rate", type=float, default=64000.0,
                        help="SiK air data rate in bps (default: 64000)")
    parser.add_argument("--deviation", type=float, default=25000.0,
                        help="FSK frequency deviation in Hz (default: 25000)")
    parser.add_argument("--sync-word", type=lambda x: int(x, 0),
                        default=DEFAULT_SYNC_WORD,
                        help="16-bit sync word (default: 0x365C)")
    parser.add_argument("--live", action="store_true",
                        help="Live SDR capture and decode (requires SoapySDR)")
    parser.add_argument("--band", default="915", choices=["915", "433"],
                        help="Frequency band for live scan (default: 915)")
    parser.add_argument("--dwell-ms", type=float, default=250.0,
                        help="Per-channel dwell time in ms (default: 250)")
    parser.add_argument("--self-test", action="store_true",
                        help="Run synthetic encode→decode self-test")
    parser.add_argument("--output", "-o", default="-",
                        help="Output file (default: stdout)")

    # import sdr_common args if available
    try:
        from sdr_common import add_sdr_args
        add_sdr_args(parser)
    except ImportError:
        pass

    args = parser.parse_args()

    if args.self_test:
        ok = self_test()
        sys.exit(0 if ok else 1)

    out = sys.stdout
    if args.output != "-":
        out = open(args.output, "w")

    try:
        if args.live:
            sdr_device = getattr(args, "sdr_device", "")
            sdr_gain = getattr(args, "sdr_gain", None) or 40.0
            scan_channels(
                band=args.band,
                dwell_ms=args.dwell_ms,
                sdr_args=sdr_device,
                gain=sdr_gain,
                data_rate=args.data_rate,
                sample_rate=args.sample_rate,
                sync_word=args.sync_word,
                output=out,
            )
        elif args.iq_file:
            decode_file(
                path=args.iq_file,
                sample_rate=args.sample_rate,
                data_rate=args.data_rate,
                deviation=args.deviation,
                iq_format=args.iq_format,
                sync_word=args.sync_word,
                output=out,
            )
        else:
            parser.print_help()
            sys.exit(1)
    finally:
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    main()
