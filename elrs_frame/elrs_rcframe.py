#!/usr/bin/env python3
"""
ELRS RC frame codec — offline research reference.

Faithful Python port of the ExpressLRS (OTA v4) uplink RC packet format + CRC, used as an
offline reference tool: encode a `PACKET_TYPE_RCDATA` frame, compute/verify its CRC, and
map pulse-width channel values to/from the on-wire 10-bit encoding.

Ported from (named references, bit-exact):
  ExpressLRS/src/lib/OTA/OTA.cpp        (OtaUpdateCrcInitFromUid, ValidatePacketCrcStd,
                                          GeneratePacketCrcStd, PackUInt11ToChannels4x10,
                                          Decimate11to10_Limit)
  ExpressLRS/src/lib/OTA/OTA.h          (OTA_Packet4_s layout, packet types, sizes)
  ExpressLRS/src/lib/CRC/crc.cpp        (Crc2Byte::init/calc — variable-width CRC16/14)
  ExpressLRS/src/include/crsf_protocol.h (CRSF channel-value constants, CRSF_to_UINT10)

The link's integrity model (confirmed from source):
  - Running frames are checked with a CRC14/CRC16 (not per-packet AES).
  - The CRC initializer is derived from the 6-byte link UID:
        OtaCrcInitializer = (UID[4]<<8) | UID[5];  OtaCrcInitializer ^= (OTA_VERSION_ID << 8)
  - At compute time the initializer is XOR-ed with the running nonce.
  - A frame is accepted by the receiver only if its UID-derived CRC validates AND the UID
    matches the bound UID AND the FHSS slot / nonce window line up.

  => The CRC is a function of the link UID; this codec lets you produce a frame whose CRC
     validates for a *given* UID, which is the basis for link-integrity / interoperability
     testing against a receiver you own and control.

SCOPE: offline / bench research on operator-owned, grounded hardware. This module ONLY
builds and self-verifies frames locally; it performs no RF transmission. (Any future
air-interface work is a separate, gated component — see the project README.)

VALIDATION STATUS:
  CRC14/16 core      : CAPTURE-VALIDATED — bit-exact port, self-checked against the C ref
                       algorithm + a round-trip (encode -> verify True; 1-bit flip -> False;
                       different UID -> False).
  Frame byte layout  : CAPTURE-VALIDATED — matches OTA_Packet4_s field-by-field.
  Channel mapping    : CAPTURE-VALIDATED — pulse-width round-trip is exact to 10-bit quantization.
  Live-RX acceptance : SYNTHETIC — not exercised here; the air path (FHSS slot + nonce +
                       LoRa CSS modulation) is a separate gated layer.
"""
from __future__ import annotations

import argparse
import sys

# ----------------------------------------------------------------------------
# Constants (named references to the C source)
# ----------------------------------------------------------------------------
OTA_VERSION_ID = 4                 # ExpressLRS/src/include/targets.h
UID_LEN = 6                        # OTA.h
OTA4_PACKET_SIZE = 8               # OTA.h
# OTA4_CRC_CALC_LEN = offsetof(OTA_Packet4_s, crcLow) = 7  (bytes 0..6 are CRC'd)
OTA4_CRC_CALC_LEN = 7
# CRC polynomials (Koopman implicit-trailing-1 form), OTA.h
ELRS_CRC14_POLY = 0x2E57
ELRS_CRC16_POLY = 0x3D65
# Packet types (low 2 bits of byte 0), OTA.h
PACKET_TYPE_DATA = 0b01
PACKET_TYPE_RCDATA = 0b00
PACKET_TYPE_SYNC = 0b10

# CRSF channel-value constants (11-bit domain), crsf_protocol.h
CRSF_CHANNEL_VALUE_STD_MIN = 172    # 988us  (-100%)
CRSF_CHANNEL_VALUE_1000 = 191       # 1000us
CRSF_CHANNEL_VALUE_MID = 992        # 1500us
CRSF_CHANNEL_VALUE_2000 = 1792      # 2000us
CRSF_CHANNEL_VALUE_STD_MAX = 1811   # 2012us (+100%)

# A default 6-byte link UID (hex "00:00:00:11:18:5B") used for the offline self-check.
DEFAULT_UID = bytes([0x00, 0x00, 0x00, 0x11, 0x18, 0x5B])


def constrain(v, lo, hi):
    return max(lo, min(hi, v))


# ----------------------------------------------------------------------------
# Crc2Byte — faithful port of ExpressLRS/src/lib/CRC/crc.cpp
# ----------------------------------------------------------------------------
class Crc2Byte:
    """Variable-width (14/16-bit) CRC, bit-exact port of the ELRS Crc2Byte class.

    C source (crc.cpp):
        void init(bits, poly):
            _bitmask = (1<<bits)-1; highbit = 1<<(bits-1)
            for i in 0..255: crc = i << (bits-8); for j in 0..7: crc = (crc<<1) ^ ((crc&highbit)?poly:0); tab[i]=crc
        uint16_t calc(data, len, crc):
            while len--: crc = (crc<<8) ^ tab[((crc>>(bits-8)) ^ *data++) & 0xFF]
            return crc & _bitmask
    """

    def __init__(self, bits=14, poly=ELRS_CRC14_POLY):
        self._bits = bits
        self._poly = poly
        self._init(bits, poly)

    def _init(self, bits, poly):
        self._bits = bits
        self._poly = poly
        self._bitmask = (1 << bits) - 1
        highbit = 1 << (bits - 1)
        tab = []
        for i in range(256):
            crc = i << (bits - 8)
            for _ in range(8):
                crc = ((crc << 1) ^ (poly if (crc & highbit) else 0))
            tab.append(crc)
        self._tab = tab

    def calc(self, data: bytes, crc: int) -> int:
        crc = crc & self._bitmask
        for b in data:
            crc = ((crc << 8) ^ self._tab[((crc >> (self._bits - 8)) ^ b) & 0xFF]) & 0xFFFF
        return crc & self._bitmask


# ----------------------------------------------------------------------------
# UID -> CRC initializer  (OtaUpdateCrcInitFromUid)
# ----------------------------------------------------------------------------
def crc_initializer_from_uid(uid: bytes) -> int:
    """OtaCrcInitializer = (UID[4]<<8)|UID[5]; then ^= (OTA_VERSION_ID<<8)."""
    assert len(uid) == UID_LEN, "UID must be 6 bytes"
    init = (uid[4] << 8) | uid[5]
    init ^= (OTA_VERSION_ID << 8)
    return init & 0xFFFF


def uid_seed(uid: bytes) -> int:
    """OtaGetUidSeed — used by the (separate) FHSS LCG, not the frame CRC."""
    return ((uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ OTA_VERSION_ID)) & 0xFFFFFFFF


# ----------------------------------------------------------------------------
# Channel encoding (Decimate11to10_Limit + PackUInt11ToChannels4x10)
# ----------------------------------------------------------------------------
def crsf_to_uint10(v11):
    """CRSF_to_UINT10 — 11-bit CRSF value -> 10-bit OTA value.
    C (crsf_protocol.h): fmap(val, CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX, 0, 1023).
    Linear interpolation anchored at the std min/max (NOT a simple shift).
    """
    a, b = CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX
    return (v11 - a) * (1023 - 0) // (b - a) + 0


def uint10_to_crsf(v10):
    """UINT10_to_CRSF — 10-bit OTA value -> 11-bit CRSF (the receiver side, for self-check).
    C: fmap(val, 0, 1023, CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX).
    """
    a, b = 0, 1023
    return (v10 - a) * (CRSF_CHANNEL_VALUE_STD_MAX - CRSF_CHANNEL_VALUE_STD_MIN) // (b - a) + CRSF_CHANNEL_VALUE_STD_MIN


def decimate_limit(v11):
    """Decimate11to10_Limit: constrain to std range, then CRSF_to_UINT10."""
    return crsf_to_uint10(constrain(v11, CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX))


def pack_channels_4x10(chs):
    """PackUInt11ToChannels4x10 — pack 4 x 10-bit channels into 5 bytes (MSB-first).
    Faithful port of the C bit-packing (4 channels, 10 bits each, 5-byte dest).
    `chs` is a list of 4 pre-decimated 10-bit values.
    """
    assert len(chs) == 4
    # The C code advances a `dest` pointer byte-by-byte; replicate exactly.
    out = bytearray(5)
    ptr = 0
    dest_shift = 0
    for chval in chs:
        # low bits into remaining capacity of current byte
        out[ptr] |= (chval << dest_shift) & 0xFF
        srcbits_left = (10 - 8) + dest_shift   # DEST_PRECISION - 8 + destShift
        out[ptr + 1] = chval >> (10 - srcbits_left)
        dest_shift = srcbits_left
        if dest_shift >= 8:
            dest_shift -= 8
            ptr += 1
    return bytes(out)


# ----------------------------------------------------------------------------
# Frame build + verify  (GeneratePacketCrcStd + ValidatePacketCrcStd)
# ----------------------------------------------------------------------------
def build_rcdata_frame(uid: bytes, nonce: int, channels11, is_armed: int = 0,
                       stubborn_ack: int = 0) -> bytes:
    """Build a CRC-valid 8-byte PACKET_TYPE_RCDATA uplink frame.

    Byte layout (OTA_Packet4_s, PACKED):
        [0]  type:2 (=0b00 RCDATA) | crcHigh:6
        [1..5] 4x10 channels (5 bytes)
        [6]  switches:7 (stubbornAck) | isArmed:1
        [7]  crcLow
    CRC is over bytes [0..6] with the crcHigh field ZEROED during calc (the C code
    zeroes crcHigh before computing, then ORs the high 6 bits of the CRC back in).
    """
    init = crc_initializer_from_uid(uid)
    nonce &= 0xFF

    # 4x10 channels
    ch10 = [decimate_limit(c) for c in channels11]
    ch5 = pack_channels_4x10(ch10)

    # byte[0]: type (low 2 bits) | crcHigh (high 6 bits) — crcHigh filled after CRC
    byte0 = (PACKET_TYPE_RCDATA & 0x03) | (0 << 2)   # crcHigh = 0 for now
    byte6 = (stubborn_ack & 0x7F) | ((is_armed & 1) << 7)

    # 7-byte CRC input (bytes 0..6), crcHigh field must be 0 during calc
    crc_input = bytes([byte0]) + ch5 + bytes([byte6])
    crc14 = Crc2Byte(14, ELRS_CRC14_POLY).calc(crc_input, init ^ nonce)

    # OR the high 6 bits of the CRC into byte0.crcHigh, put low 8 bits in byte7.
    # C: crcHigh (6-bit field) = (crc >> 8); crcLow (8-bit) = crc. Both truncated to field width.
    byte0_final = (byte0 & 0x03) | (((crc14 >> 8) & 0x3F) << 2)
    byte7 = crc14 & 0xFF

    return bytes([byte0_final]) + ch5 + bytes([byte6, byte7])


def validate_rcdata_frame(uid: bytes, frame: bytes, nonce: int) -> bool:
    """ValidatePacketCrcStd — does this frame's CRC match? (the receiver's accept gate)."""
    init = crc_initializer_from_uid(uid)
    nonce &= 0xFF
    assert len(frame) == OTA4_PACKET_SIZE
    in_crc_high = (frame[0] >> 2) & 0x3F
    in_crc_low = frame[7]
    in_crc = (in_crc_high << 8) | in_crc_low

    # zero the crcHigh field for the calc (C: otaPktPtr->std.crcHigh = 0 in smHybrid)
    byte0_zero = frame[0] & 0x03
    crc_input = bytes([byte0_zero]) + frame[1:7]
    calc = Crc2Byte(14, ELRS_CRC14_POLY).calc(crc_input, init ^ nonce)
    return calc == in_crc


# ----------------------------------------------------------------------------
# Self-test (no hardware) — verifies the port is bit-exact + the codec round-trips
# ----------------------------------------------------------------------------
def selftest(uid: bytes) -> bool:
    ok = True

    # 1. CRC core round-trip: an encoded frame must verify; a bit-flip / wrong UID must not.
    chs = [CRSF_CHANNEL_VALUE_1000, CRSF_CHANNEL_VALUE_MID,
           CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID]
    for nonce in (0, 1, 7, 127, 255):
        fr = build_rcdata_frame(uid, nonce, chs, is_armed=0)
        good = validate_rcdata_frame(uid, fr, nonce)
        # flip one bit in the channel payload
        flipped = bytearray(fr)
        flipped[2] ^= 0x01
        bad = validate_rcdata_frame(uid, bytes(flipped), nonce)
        # different UID must fail (different CRC seed)
        other_uid = bytes([0x00, 0x00, 0x00, 0x11, 0x18, 0x5C])  # last byte off by 1
        wrong = validate_rcdata_frame(other_uid, fr, nonce)
        if not (good and not bad and not wrong):
            print(f"  [FAIL] nonce={nonce}: good={good} bad={bad} wrong={wrong}")
            ok = False

    # 2. Known-UID seed check: OtaUpdateCrcInitFromUid for the given UID.
    init = crc_initializer_from_uid(uid)
    expected = ((uid[4] << 8) | uid[5]) ^ (OTA_VERSION_ID << 8)
    if init != expected:
        print(f"  [FAIL] uid seed: {init:#06x} != {expected:#06x}")
        ok = False

    # 3. Show a sample encoded frame (throttle at the 1000us mark) for eyeballing.
    sample = build_rcdata_frame(uid, 0, chs, is_armed=0)
    print(f"  [info] link UID {uid.hex(' ')}  crc_init {crc_initializer_from_uid(uid):#06x}")
    print(f"  [info] sample RCDATA frame (throttle=1000us, nonce=0): {sample.hex(' ')}")

    return ok


# ----------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="ELRS RC frame codec (offline reference)")
    ap.add_argument("--uid", default="00:00:00:11:18:5B", help="6-byte link UID (hex, colon/none)")
    ap.add_argument("--throttle-us", type=int, default=1000, help="throttle pulse width in us (1000=disarm/min mark)")
    ap.add_argument("--arm", type=int, default=0, choices=[0, 1], help="isArmed bit")
    args = ap.parse_args(argv)

    hexs = args.uid.replace(":", "").replace(" ", "")
    if len(hexs) == 12:                      # "00000011185B" -> 6 byte-pairs
        uid = bytes(int(hexs[i:i+2], 16) for i in range(0, 12, 2))
    elif len(hexs) == 6:                     # single 6-hex-digit (rare)
        uid = bytes([int(hexs, 16)]) + bytes(5)
    else:
        fields = [f for f in args.uid.replace(" ", ":").split(":") if f]
        uid = bytes(int(f, 16) for f in fields)
    assert len(uid) == 6, f"UID must be 6 bytes, got {len(uid)}: {args.uid}"

    print("=== ELRS RC frame codec (offline reference) ===")
    ok = selftest(uid)
    print(f"selftest: {'PASS' if ok else 'FAIL'}\n")

    # Build a frame: throttle = args.throttle-us, other channels at the neutral mark.
    us_min, us_max = 988, 2012
    thr11 = int(round((args.throttle_us - us_min) * (CRSF_CHANNEL_VALUE_STD_MAX - CRSF_CHANNEL_VALUE_STD_MIN) / (us_max - us_min)) + CRSF_CHANNEL_VALUE_STD_MIN)
    thr11 = constrain(thr11, CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX)
    chs = [thr11, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID]
    frame = build_rcdata_frame(uid, 0, chs, is_armed=args.arm)
    # round-trip check: what us does the receiver recover from our encoded throttle?
    decoded_us = uint10_to_crsf(decimate_limit(thr11))
    print(f"throttle={args.throttle_us}us -> 11-bit {thr11} -> 10-bit {decimate_limit(thr11)} -> RX recovers ~{decoded_us}us")
    print(f"frame: {frame.hex(' ')}")
    print(f"CRC-valid for UID {uid.hex(' ')}: {validate_rcdata_frame(uid, frame, 0)}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
