"""
ELRS over-the-air (OTA) packet model — RX and TX symmetric.

Ports of:
  src/lib/OTA/OTA.h   -> OTA_Packet4_s / OTA_Packet8_s / OTA_Sync_s / OTA_LinkStats_s
  src/lib/OTA/OTA.cpp -> CRC validate/generate, channel pack/unpack, sync fields

Design goal (per operator requirement): the same object model is used for
*decoding* a captured frame and for *building* a frame to transmit. RX calls
`validate_*` / `unpack_*`; TX calls `pack_*` / `generate_*`. Nothing here is
hardwired to a direction.

Two packet sizes, selected by air rate:
  * 13-byte "full"  (OTA_Packet8_s, CRC-16)  — 2.4 GHz high-rate / FullRes
  *  8-byte "std"   (OTA_Packet4_s, CRC-14)  — sub-GHz and 2.4 low-rate

CRC init is derived from the link UID (see ota_uid_crc_init). For a bound link:
    OtaCrcInitializer = (UID[4]<<8 | UID[5]) ^ (OTA_VERSION_ID << 8)
For *binding* mode the init collapses to OTA_VERSION_ID and nonce is 0, which
is why a bind-mode capture is the easiest on-ramp for recovering the UID.

Nonces: SYNC packets always use nonce=0 for the CRC; DATA/RC packets XOR the
current OtaNonce into the init. OtaNonce is 8-bit but is promoted to 16-bit
and XOR'd as-is in the C code.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .crc import Crc2Byte, ELRS_CRC14_POLY, ELRS_CRC16_POLY

# src/include/targets.h
OTA_VERSION_ID = 4
UID_LEN = 6

# Packet sizes (OTA.h)
OTA4_PACKET_SIZE = 8
OTA8_PACKET_SIZE = 13
# CRC is computed over everything before the trailing CRC field(s):
#   full: 13 - 2 (crc u16) = 11
#   std:  8  - 1 (crcLow)  = 7   (crcHigh bits are zeroed before calc)
OTA8_CRC_CALC_LEN = 11
OTA4_CRC_CALC_LEN = 7

# Payload sizes (src/include/telemetry_protocol.h)
ELRS4_DATA_DL_BYTES_PER_CALL = 5
ELRS4_DATA_UL_BYTES_PER_CALL = 5
ELRS8_DATA_DL_BYTES_PER_CALL = 10
ELRS8_DATA_UL_BYTES_PER_CALL = 10

# Packet types (low 2 bits of byte 0)
PACKET_TYPE_RCDATA = 0b00     # uplink
PACKET_TYPE_DATA   = 0b01     # bidirectional
PACKET_TYPE_SYNC   = 0b10     # uplink
PACKET_TYPE_LINKSTATS = 0b00  # downlink alias of RCDATA slot


# ----------------------------------------------------------------------------
# CRC init derivation
# ----------------------------------------------------------------------------
def ota_uid_crc_init(uid: bytes, ota_version: int = OTA_VERSION_ID) -> int:
    """Port of OtaUpdateCrcInitFromUid. uid must be 6 bytes."""
    if len(uid) != UID_LEN:
        raise ValueError(f"uid must be {UID_LEN} bytes, got {len(uid)}")
    init = (uid[4] << 8) | uid[5]
    init ^= ota_version << 8
    return init & 0xFFFF


def bind_crc_init(ota_version: int = OTA_VERSION_ID) -> int:
    """Binding-mode CRC init (tx_main.cpp::EnterBindingMode). Nonce is forced 0."""
    return ota_version & 0xFFFF


# ----------------------------------------------------------------------------
# Shared CRC instance (re-init per packet size, mirroring OtaUpdateSerializers)
# ----------------------------------------------------------------------------
_crc16 = Crc2Byte(16, ELRS_CRC16_POLY)
_crc14 = Crc2Byte(14, ELRS_CRC14_POLY)


# ----------------------------------------------------------------------------
# Bitfield helpers
# ----------------------------------------------------------------------------
def _get_bits(byte: int, start: int, n: int) -> int:
    """Extract n bits starting at bit `start` (LSB=bit 0)."""
    return (byte >> start) & ((1 << n) - 1)


def _set_bits(value: int, start: int, n: int, amount: int) -> int:
    mask = (1 << n) - 1
    return (value & ~(mask << start)) | ((amount & mask) << start)


# ----------------------------------------------------------------------------
# Channel packing (4x 10-bit in 5 bytes)
# ----------------------------------------------------------------------------
def pack_channels_4x10(src: list[int], decimate_div2: bool = False) -> bytes:
    """
    Port of PackUInt11ToChannels4x10. src = 4 channel values (11-bit CRSF).
    Produces 5 bytes. If decimate_div2, each channel is first shifted right 1
    (used by the 13-byte full-res path); otherwise values are used as-is after
    being constrained to the 10-bit CRSF range (hybrid/wide path).
    """
    out = bytearray(5)
    if decimate_div2:
        vals = [s >> 1 for s in src]
    else:
        # Hybrid/Wide: CRSF_to_UINT10 of a constrained 11-bit value. For decode
        # purposes we accept values already in 10-bit form; we simply mask.
        vals = [s & 0x3FF for s in src]

    dest_shift = 0
    pos = 0
    for ch in range(4):
        ch_val = vals[ch]
        out[pos] |= (ch_val << dest_shift) & 0xFF
        src_bits_left = 10 - 8 + dest_shift
        out[pos + 1] = ch_val >> (10 - src_bits_left)
        pos += 1
        dest_shift = src_bits_left
    return bytes(out)


def unpack_channels_4x10(payload: bytes) -> list[int]:
    """
    Port of UnpackChannels4x10ToUInt11: 5 bytes -> 4 x 11-bit values (left-shifted
    1, so value*2). Caller re-expands to CRSF 11-bit as appropriate.
    """
    if len(payload) < 5:
        raise ValueError("need 5 bytes")
    out = [0] * 4
    read = 0
    bits_merged = 0
    read_value = 0
    for n in range(4):
        while bits_merged < 10:
            b = payload[read]
            read += 1
            read_value |= b << bits_merged
            bits_merged += 8
        out[n] = (read_value & 0x3FF) << 1
        read_value >>= 10
        bits_merged -= 10
    return out


# CRSF 11-bit helpers (crsf_protocol.h)
CRSF_CHANNEL_VALUE_UNSET = 0x1FFF
CRSF_CHANNEL_VALUE_RANGEMIN = 171
CRSF_CHANNEL_VALUE_RANGEMAX = 1811


def crsf_to_uint10(ch11: int) -> int:
    """Map 11-bit CRSF channel to 10-bit (0..1023), clamped to std range."""
    c = min(max(ch11, CRSF_CHANNEL_VALUE_RANGEMIN), CRSF_CHANNEL_VALUE_RANGEMAX)
    return (c - CRSF_CHANNEL_VALUE_RANGEMIN) * 1023 // (CRSF_CHANNEL_VALUE_RANGEMAX - CRSF_CHANNEL_VALUE_RANGEMIN)


def uint10_to_crsf(v10: int) -> int:
    return v10 * (CRSF_CHANNEL_VALUE_RANGEMAX - CRSF_CHANNEL_VALUE_RANGEMIN) // 1023 + CRSF_CHANNEL_VALUE_RANGEMIN


def bit_to_crsf(bit: int) -> int:
    return CRSF_CHANNEL_VALUE_RANGEMAX if bit else CRSF_CHANNEL_VALUE_RANGEMIN


def crsf_to_bit(ch11: int) -> int:
    return 1 if ch11 >= (CRSF_CHANNEL_VALUE_RANGEMIN + CRSF_CHANNEL_VALUE_RANGEMAX) // 2 else 0


# ----------------------------------------------------------------------------
# Packet result containers (direction-agnostic)
# ----------------------------------------------------------------------------
@dataclass
class ElrsSync:
    """OTA_Sync_s (7 bytes)."""
    fhss_index: int
    nonce: int
    rf_rate_enum: int
    switch_enc_mode: int
    new_tlm_ratio: int
    gemini_mode: int
    ota_protocol: int
    free: int
    uid4: int
    uid5: int


@dataclass
class ElrsLinkStats:
    """OTA_LinkStats_s (4 bytes)."""
    uplink_rssi_1: int
    antenna: int
    uplink_rssi_2: int
    model_match: int
    lq: int
    true_diversity_available: int
    snr: int  # signed


@dataclass
class ElrsPacket:
    """
    A decoded/built ELRS OTA frame. All fields are optional depending on type;
    `raw` always holds the 8 or 13 on-air bytes.
    """
    raw: bytes = b""
    type: int = PACKET_TYPE_RCDATA
    is_full: bool = False          # True = 13-byte, False = 8-byte
    crc_valid: bool = False

    # sync
    sync: ElrsSync | None = None

    # rcdata
    channels: list[int] = field(default_factory=list)   # 11-bit CRSF, up to 16
    is_armed: bool = False
    stubborn_ack: bool = False
    uplink_power: int = 0
    is_high_aux: bool = False
    switch_index: int = 0
    switch_value: int = 0

    # linkstats (downlink)
    link_stats: ElrsLinkStats | None = None

    # data (bidirectional)
    data: bytes = b""

    # link context this packet was validated against
    uid: bytes | None = None
    ota_nonce: int = 0

    def to_dict(self) -> dict:
        d = {
            "type": {0: "RCDATA", 1: "DATA", 2: "SYNC", 3: "RESERVED"}.get(self.type, str(self.type)),
            "full_res": self.is_full,
            "crc_valid": self.crc_valid,
            "raw_hex": self.raw.hex(),
        }
        if self.sync:
            d["sync"] = {
                "fhss_index": self.sync.fhss_index,
                "nonce": self.sync.nonce,
                "rf_rate_enum": self.sync.rf_rate_enum,
                "switch_enc_mode": self.sync.switch_enc_mode,
                "new_tlm_ratio": self.sync.new_tlm_ratio,
                "gemini_mode": self.sync.gemini_mode,
                "ota_protocol": self.sync.ota_protocol,
                "uid4": self.sync.uid4,
                "uid5": self.sync.uid5,
            }
        if self.channels:
            d["channels"] = self.channels
        if self.link_stats:
            d["link_stats"] = {
                "uplink_rssi_1": self.link_stats.uplink_rssi_1,
                "uplink_rssi_2": self.link_stats.uplink_rssi_2,
                "lq": self.link_stats.lq,
                "snr": self.link_stats.snr,
            }
        if self.data:
            d["data_hex"] = self.data.hex()
        return d


# ----------------------------------------------------------------------------
# DECODE — 13-byte full packet
# ----------------------------------------------------------------------------
def _decode_sync(raw: bytes) -> ElrsSync:
    """
    Decode OTA_Sync_s from the bytes *following* the type byte.
    Field order (OTA.h): fhssIndex, nonce, rfRateEnum,
      [switchEncMode:1,newTlmRatio:3,geminiMode:1,otaProtocol:2,free:1], UID4, UID5
    => positions 0,1,2,3,4,5 (6 bytes). UID4=pos4, UID5=pos5.
    """
    b3 = raw[3]
    return ElrsSync(
        fhss_index=raw[0],
        nonce=raw[1],
        rf_rate_enum=raw[2],
        switch_enc_mode=_get_bits(b3, 0, 1),
        new_tlm_ratio=_get_bits(b3, 1, 3),
        gemini_mode=_get_bits(b3, 4, 1),
        ota_protocol=_get_bits(b3, 5, 2),
        free=_get_bits(b3, 7, 1),
        uid4=raw[4],
        uid5=raw[5],
    )


def decode_full(raw: bytes) -> ElrsPacket:
    """Parse a 13-byte OTA_Packet8_s (no CRC check — caller does that)."""
    if len(raw) != OTA8_PACKET_SIZE:
        raise ValueError(f"full packet must be {OTA8_PACKET_SIZE} bytes")
    pkt = ElrsPacket(raw=bytes(raw), is_full=True)
    b0 = raw[0]
    pkt.type = _get_bits(b0, 0, 2)

    if pkt.type == PACKET_TYPE_SYNC:
        pkt.sync = _decode_sync(raw[1:])
    elif pkt.type == PACKET_TYPE_RCDATA:
        pkt.stubborn_ack = bool(_get_bits(b0, 2, 1))
        pkt.uplink_power = _get_bits(b0, 3, 3)
        pkt.is_high_aux = bool(_get_bits(b0, 6, 1))
        pkt.is_armed = bool(_get_bits(b0, 7, 1))
        ch_low = unpack_channels_4x10(raw[1:6])
        ch_high = unpack_channels_4x10(raw[6:11])
        # Full-res: channels are full 11-bit CRSF range (div2 on pack)
        pkt.channels = ch_low + ch_high
    elif pkt.type == PACKET_TYPE_DATA:
        pkt.stubborn_ack = _get_bits(b0, 2, 1)
        pkt.data = raw[1:11]
    elif pkt.type == PACKET_TYPE_LINKSTATS:
        pkt.stubborn_ack = _get_bits(b0, 2, 1)
        ls = raw[1:5]
        pkt.link_stats = ElrsLinkStats(
            uplink_rssi_1=_get_bits(ls[0], 0, 7),
            antenna=_get_bits(ls[0], 7, 1),
            uplink_rssi_2=_get_bits(ls[1], 0, 7),
            model_match=_get_bits(ls[1], 7, 1),
            lq=_get_bits(ls[2], 0, 7),
            true_diversity_available=_get_bits(ls[2], 7, 1),
            snr=struct.unpack("<b", bytes([ls[3]]))[0],
        )
        pkt.data = raw[5:11]
    return pkt


# ----------------------------------------------------------------------------
# DECODE — 8-byte std packet
# ----------------------------------------------------------------------------
def decode_std(raw: bytes, ota_nonce: int = 0) -> ElrsPacket:
    """Parse an 8-byte OTA_Packet4_s (no CRC check)."""
    if len(raw) != OTA4_PACKET_SIZE:
        raise ValueError(f"std packet must be {OTA4_PACKET_SIZE} bytes")
    pkt = ElrsPacket(raw=bytes(raw), is_full=False, ota_nonce=ota_nonce)
    b0 = raw[0]
    pkt.type = _get_bits(b0, 0, 2)

    if pkt.type == PACKET_TYPE_SYNC:
        pkt.sync = _decode_sync(raw[1:])
    elif pkt.type == PACKET_TYPE_RCDATA:
        ch = unpack_channels_4x10(raw[1:6])
        pkt.is_armed = bool(_get_bits(raw[6], 7, 1))
        sw = raw[6]
        switch_index = _get_bits(sw, 3, 3)
        pkt.switch_index = switch_index
        pkt.stubborn_ack = bool(_get_bits(sw, 6, 1))
        pkt.channels = ch
        # switch value: low 3 bits (or 4 for index>=6 per hybrid8)
        pkt.switch_value = sw & 0b111
    elif pkt.type == PACKET_TYPE_DATA:
        pkt.stubborn_ack = _get_bits(b0, 2, 1)
        pkt.data = raw[1:7]
    elif pkt.type == PACKET_TYPE_LINKSTATS:
        pkt.stubborn_ack = _get_bits(b0, 2, 1)
        ls = raw[1:5]
        pkt.link_stats = ElrsLinkStats(
            uplink_rssi_1=_get_bits(ls[0], 0, 7),
            antenna=_get_bits(ls[0], 7, 1),
            uplink_rssi_2=_get_bits(ls[1], 0, 7),
            model_match=_get_bits(ls[1], 7, 1),
            lq=_get_bits(ls[2], 0, 7),
            true_diversity_available=_get_bits(ls[2], 7, 1),
            snr=struct.unpack("<b", bytes([ls[3]]))[0],
        )
        pkt.data = raw[5:7]
    return pkt


# ----------------------------------------------------------------------------
# CRC validate + generate — the RX/TX mirror
# ----------------------------------------------------------------------------
def validate_crc(raw: bytes, is_full: bool, crc_init: int,
                 ota_nonce: int, packet_type: int) -> bool:
    """
    Port of ValidatePacketCrcFull/Std. crc_init is the link's OtaCrcInitializer.
    ota_nonce is XOR'd in (as 16-bit) unless the packet is a SYNC (nonce 0).
    """
    if packet_type == PACKET_TYPE_SYNC:
        nonce_validator = 0
    else:
        nonce_validator = ota_nonce & 0xFFFF

    if is_full:
        calc = _crc16.calc(raw[:OTA8_CRC_CALC_LEN], crc_init ^ nonce_validator)
        stored = struct.unpack("<H", raw[11:13])[0]
    else:
        # Zero the crcHigh bits (top 6 of byte 0) before calc, mirroring C
        tmp = bytearray(raw)
        tmp[0] &= 0x03  # keep only low 2 type bits
        calc = _crc14.calc(bytes(tmp[:OTA4_CRC_CALC_LEN]), crc_init ^ nonce_validator)
        stored = ((_get_bits(raw[0], 2, 6) << 8) | raw[7]) & 0x3FFF
    return calc == stored


def generate_crc(raw: bytearray, is_full: bool, crc_init: int,
                 ota_nonce: int, packet_type: int) -> None:
    """
    Port of GeneratePacketCrcFull/Std. Writes the CRC back into `raw` in place.
    """
    if packet_type == PACKET_TYPE_SYNC:
        nonce_validator = 0
    else:
        nonce_validator = ota_nonce & 0xFFFF

    if is_full:
        calc = _crc16.calc(bytes(raw[:OTA8_CRC_CALC_LEN]), crc_init ^ nonce_validator)
        raw[11:13] = struct.pack("<H", calc)
    else:
        # Zero crcHigh first
        tmp = bytearray(raw)
        tmp[0] &= 0x03
        calc = _crc14.calc(bytes(tmp[:OTA4_CRC_CALC_LEN]), crc_init ^ nonce_validator)
        raw[0] = _set_bits(raw[0], 2, 6, calc >> 8)
        raw[7] = calc & 0xFF


# ----------------------------------------------------------------------------
# ENCODE (TX path) — build raw frames
# ----------------------------------------------------------------------------
def build_sync(is_full: bool, fhss_index: int, nonce: int, rf_rate_enum: int,
               switch_enc_mode: int = 0, new_tlm_ratio: int = 0,
               gemini_mode: int = 0, ota_protocol: int = 0,
               uid4: int = 0, uid5: int = 0,
               crc_init: int = 0, ota_nonce: int = 0) -> bytes:
    """Build a SYNC frame (nonce forced 0 in CRC, per ELRS)."""
    sync_byte = _set_bits(0, 0, 1, switch_enc_mode)
    sync_byte = _set_bits(sync_byte, 1, 3, new_tlm_ratio)
    sync_byte = _set_bits(sync_byte, 4, 1, gemini_mode)
    sync_byte = _set_bits(sync_byte, 5, 2, ota_protocol)
    # OTA_Sync_s is 6 bytes: fhssIndex, nonce, rfRateEnum, flags, UID4, UID5
    sync = bytes([fhss_index, nonce, rf_rate_enum, sync_byte, uid4, uid5])

    if is_full:
        # 13-byte: type(1) + sync(6) + free(6)
        raw = bytearray([PACKET_TYPE_SYNC, *sync])
        raw += b"\x00" * (OTA8_PACKET_SIZE - len(raw))
    else:
        # 8-byte: type(1) + sync(6) + crcLow(1)
        raw = bytearray([PACKET_TYPE_SYNC, *sync, 0x00])
        assert len(raw) == OTA4_PACKET_SIZE

    generate_crc(raw, is_full, crc_init, ota_nonce, PACKET_TYPE_SYNC)
    return bytes(raw)


def build_rcdata_std(channels: list[int], switch_index: int, switch_value: int,
                     is_armed: bool, stubborn_ack: bool = False,
                     crc_init: int = 0, ota_nonce: int = 0) -> bytes:
    """Build an 8-byte RCDATA frame (hybrid8 style)."""
    ch4 = channels[:4]
    ch_bytes = pack_channels_4x10([crsf_to_uint10(c) for c in ch4])
    sw = (stubborn_ack << 6) | (switch_index << 3) | (switch_value & 0b111)
    sw |= (1 if is_armed else 0) << 7
    raw = bytearray([PACKET_TYPE_RCDATA] + list(ch_bytes) + [sw, 0x00])
    generate_crc(raw, False, crc_init, ota_nonce, PACKET_TYPE_RCDATA)
    return bytes(raw)


def build_rcdata_full(channels: list[int], uplink_power: int, is_high_aux: bool,
                      is_armed: bool, stubborn_ack: bool = False,
                      crc_init: int = 0, ota_nonce: int = 0) -> bytes:
    """Build a 13-byte RCDATA frame (full-res, div2 packing)."""
    b0 = _set_bits(PACKET_TYPE_RCDATA, 2, 1, 1 if stubborn_ack else 0)
    b0 = _set_bits(b0, 3, 3, uplink_power & 0x7)
    b0 = _set_bits(b0, 6, 1, 1 if is_high_aux else 0)
    b0 = _set_bits(b0, 7, 1, 1 if is_armed else 0)
    ch_low = pack_channels_4x10([c & 0x7FF for c in channels[:4]], decimate_div2=True)
    ch_high = pack_channels_4x10([c & 0x7FF for c in channels[4:8]], decimate_div2=True)
    raw = bytearray([b0] + list(ch_low) + list(ch_high))
    raw += b"\x00" * (OTA8_PACKET_SIZE - len(raw))
    generate_crc(raw, True, crc_init, ota_nonce, PACKET_TYPE_RCDATA)
    return bytes(raw)


# ----------------------------------------------------------------------------
# High-level decode entry point (used by the RX pipeline)
# ----------------------------------------------------------------------------
def decode(raw: bytes, uid: bytes | None, crc_init: int, ota_nonce: int) -> ElrsPacket | None:
    """
    Validate + parse a raw frame of either size. Returns None if CRC fails.
    Caller supplies the link's crc_init (from UID) and current nonce.
    """
    if len(raw) == OTA8_PACKET_SIZE:
        pkt = decode_full(raw)
    elif len(raw) == OTA4_PACKET_SIZE:
        pkt = decode_std(raw, ota_nonce)
    else:
        return None

    ok = validate_crc(raw, pkt.is_full, crc_init, ota_nonce, pkt.type)
    pkt.crc_valid = ok
    pkt.uid = uid
    return pkt if ok else None
