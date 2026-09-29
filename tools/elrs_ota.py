#!/usr/bin/env python3
"""elrs_ota.py -- ExpressLRS OTA packet decoder.

A faithful Python port of the on-wire packet format from the ExpressLRS
source (src/lib/OTA/OTA.h / OTA.cpp). Given a CRC-valid 8- or 13-byte OTA
packet it returns the decoded fields:

  * direction   -- uplink (TX->RX) or downlink (RX->TX)
  * type        -- RCDATA / DATA / SYNC / LINKSTATS
  * the type-specific payload:
      RCDATA      : 4 (or 8) analog channels in microseconds, switches, isArmed
      SYNC        : fhssIndex, nonce, rfRate, tlmRatio, otaProtocol, UID4/UID5
      DATA up     : packageIndex + payload (MSP/telemetry fragments)
      DATA dl     : packageIndex + LinkStats (RSSI1/2, LQ, SNR) + payload
      LINKSTATS   : uplink RSSI1/2, LQ, SNR
  * CRC info    -- the init value that validated the packet (encodes UID
                   bytes 4-5 ^ OTA_VERSION_ID), and the OTA version id.

This does NOT recover a hardware serial: ELRS does not transmit one. The
most identifying value in the air is the UID-derived CRC init (and, on
SYNC packets, the raw UID4/UID5 bytes). See elrs_live.py / drone_scan.py.

Ported constants (ExpressLRS/src):
  OTA4_PACKET_SIZE=8, OTA8_PACKET_SIZE=13
  ELRS_CRC14_POLY=0x2E57 (OTA4), ELRS_CRC16_POLY=0x3D65 (OTA8)
  ELRS4_DATA_DL/UL_BYTES_PER_CALL=5, ELRS8_DATA_DL/UL_BYTES_PER_CALL=10
  UID_LEN=6
  PACKET_TYPE_RCDATA=0b00, DATA=0b01, SYNC=0b10(uplink), LINKSTATS=0b00(dl)
  CRC init = (UID[4]<<8|UID[5]) ^ (OTA_VERSION_ID<<8); ^OtaNonce (0 for SYNC)
  channels: 4x10-bit, 0=998us .. 1023=2012us
"""

# packet type (low 2 bits of byte 0)
PKT_RCDATA = 0b00      # uplink: RC channels
PKT_DATA = 0b01        # up or down: data (MSP/telemetry)
PKT_SYNC = 0b10        # uplink only: link sync / rate
PKT_LINKSTATS = 0b00   # downlink: link stats (same code as RCDATA, dir distinguishes)

TYPE_NAMES_UL = {0b00: "RCDATA", 0b01: "DATA", 0b10: "SYNC"}
TYPE_NAMES_DL = {0b00: "LINKSTATS", 0b01: "DATA"}

OTA4_SIZE = 8
OTA8_SIZE = 13
CRC14_POLY = 0x2E57
CRC16_POLY = 0x3D65
OTA_VERSION_IDS = list(range(1, 16))
OTA4_DL_BYTES = 5
OTA8_DL_BYTES = 10

# CRSF channel value bounds (crsf_protocol.h)
CRSF_MIN = 172      # ~998us in 11-bit CRSF
CRSF_MAX = 1811     # ~2012us


# ---------------------------------------------------------------------------
# CRC (mirror of ExpressLRS/src/lib/CRC/crc.cpp Crc2Byte)
# ---------------------------------------------------------------------------
class Crc2Byte:
    def __init__(self, bits, poly):
        self.bits = bits
        self.poly = poly
        self.mask = (1 << bits) - 1
        self.highbit = 1 << (bits - 1)
        tab = []
        for i in range(256):
            crc = i << (bits - 8)
            for _ in range(8):
                crc = (crc << 1) ^ (self.poly if (crc & self.highbit) else 0)
            tab.append(crc & self.mask)
        self.tab = tab

    def calc(self, data, init=0):
        crc = init & self.mask
        for b in data:
            crc = ((crc << 8) ^ self.tab[((crc >> (self.bits - 8)) ^ b) & 0xFF]) & self.mask
        return crc


CRC14 = Crc2Byte(14, CRC14_POLY)
CRC16 = Crc2Byte(16, CRC16_POLY)


def crc_init_for_uid(uid, ota_version):
    """uid: 6 bytes. Returns the 16-bit CRC initializer."""
    return (((uid[4] << 8) | uid[5]) ^ (ota_version << 8)) & 0xFFFF


def uid_from_phrase(phrase):
    import hashlib
    return hashlib.md5(phrase.encode()).digest()[:6]


# ---------------------------------------------------------------------------
# Channel unpacking (port of UnpackChannels4x10ToUInt10 -> 10-bit values)
# ---------------------------------------------------------------------------
def unpack_4x10(raw5):
    """5 bytes -> four 10-bit channel values (0..1023).
    Port of UnpackChannels4x10ToUInt11, but we keep the 10-bit value (the
    extra <<1 to 11-bit is just a placeholder; the real range is 0..1023)."""
    out = []
    bits_merged = 0
    read_value = 0
    read_byte_index = 0
    for _ in range(4):
        while bits_merged < 10:
            read_value |= raw5[read_byte_index] << bits_merged
            read_byte_index += 1
            bits_merged += 8
        out.append(read_value & 0x3FF)
        read_value >>= 10
        bits_merged -= 10
    return out


def ch10_to_us(v10):
    """10-bit channel (0=998us .. 1023=2012us) -> microseconds."""
    return round(998 + v10 * (2012 - 998) / 1023)


def linkstats_from_bytes(b4):
    """OTA_LinkStats_s: 4 bytes."""
    rssi1 = b4[0] & 0x7F
    antenna = (b4[0] >> 7) & 1
    rssi2 = b4[1] & 0x7F
    lq = b4[2] & 0x7F
    snr = b4[3]
    snr = snr - 256 if snr & 0x80 else snr
    return {"uplink_rssi1": rssi1, "antenna": antenna, "uplink_rssi2": rssi2,
            "lq": lq, "snr_db": snr / 4.0}


# ---------------------------------------------------------------------------
# Decoders
# ---------------------------------------------------------------------------
def decode_ota8(pkt, direction="up"):
    """13-byte full-resolution packet. Returns a dict."""
    b0 = pkt[0]
    ptype = b0 & 0b11
    stubborn_ack = (b0 >> 2) & 1
    pkg_index = (b0 >> 3) & 0b11111
    out = {"size": OTA8_SIZE, "stubborn_ack": stubborn_ack,
           "package_index": pkg_index}
    if direction == "up":
        out["type"] = TYPE_NAMES_UL.get(ptype, "DATA")
        if ptype == PKT_RCDATA:
            uplink_power = (b0 >> 3) & 0b111   # note: overlaps pkg_index bits in RC
            is_high_aux = (b0 >> 6) & 1
            is_armed = (b0 >> 7) & 1
            ch_low = unpack_4x10(pkt[1:6])
            ch_high = unpack_4x10(pkt[6:11])
            out.update({
                "direction": "TX->RX",
                "is_armed": bool(is_armed),
                "is_high_aux": bool(is_high_aux),
                "channels_us": {
                    "CH0": ch10_to_us(ch_low[0]), "CH1": ch10_to_us(ch_low[1]),
                    "CH2": ch10_to_us(ch_low[2]), "CH3": ch10_to_us(ch_low[3]),
                    "CH4": ch10_to_us(ch_high[0]), "CH5": ch10_to_us(ch_high[1]),
                    "CH6": ch10_to_us(ch_high[2]), "CH7": ch10_to_us(ch_high[3]),
                },
            })
        elif ptype == PKT_SYNC:
            sync = pkt[1:7]
            out.update({
                "direction": "TX->RX",
                "fhss_index": sync[0],
                "nonce": sync[1],
                "rf_rate_enum": sync[2],
                "switch_enc_mode": sync[3] & 1,
                "new_tlm_ratio": (sync[3] >> 1) & 0b111,
                "gemini_mode": (sync[3] >> 4) & 1,
                "ota_protocol": (sync[3] >> 5) & 0b11,
                "uid4": sync[4],
                "uid5": sync[5],
            })
        else:  # DATA up
            out.update({"direction": "TX->RX", "payload": pkt[1:11].hex()})
    else:
        out["type"] = TYPE_NAMES_DL.get(ptype, "DATA")
        out["direction"] = "RX->TX"
        # downlink data: first 4 bytes = LinkStats
        out["link_stats"] = linkstats_from_bytes(pkt[1:5])
        out["payload"] = pkt[5:11].hex()
    return out


def decode_ota4(pkt, direction="up"):
    """8-byte standard packet. Returns a dict."""
    b0 = pkt[0]
    ptype = b0 & 0b11
    crc_high = b0 >> 2
    out = {"size": OTA4_SIZE}
    if direction == "up":
        out["type"] = TYPE_NAMES_UL.get(ptype, "DATA")
        out["direction"] = "TX->RX"
        if ptype == PKT_RCDATA:
            ch = unpack_4x10(pkt[1:6])
            switches = pkt[6] & 0x7F
            is_armed = (pkt[6] >> 7) & 1
            out.update({
                "is_armed": bool(is_armed),
                "switches_raw": switches,
                "channels_us": {"CH0": ch10_to_us(ch[0]), "CH1": ch10_to_us(ch[1]),
                                 "CH2": ch10_to_us(ch[2]), "CH3": ch10_to_us(ch[3])},
            })
        elif ptype == PKT_SYNC:
            sync = pkt[1:7]
            out.update({
                "fhss_index": sync[0], "nonce": sync[1], "rf_rate_enum": sync[2],
                "switch_enc_mode": sync[3] & 1, "new_tlm_ratio": (sync[3] >> 1) & 0b111,
                "gemini_mode": (sync[3] >> 4) & 1, "ota_protocol": (sync[3] >> 5) & 0b11,
                "uid4": sync[4], "uid5": sync[5],
            })
        else:  # DATA up
            out["payload"] = pkt[1:7].hex()
    else:
        out["type"] = TYPE_NAMES_DL.get(ptype, "LINKSTATS")
        out["direction"] = "RX->TX"
        if ptype == PKT_LINKSTATS:
            out["link_stats"] = linkstats_from_bytes(pkt[1:5])
            out["payload"] = pkt[5:7].hex()
        else:
            out["payload"] = pkt[1:7].hex()
    return out


def infer_direction(ptype, size):
    """Best-effort direction from type. RCDATA/SYNC are uplink-only; LINKSTATS
    (type 0b00 on the downlink) is downlink. type 0b00 is ambiguous without
    context -- ELRS resolves it by link direction; we default to uplink and
    the caller can override from the capture context (e.g. which side we are).
    """
    if ptype == PKT_SYNC:
        return "up"
    if ptype == PKT_DATA:
        return "up"  # ambiguous; DATA exists both ways
    # PKT_RCDATA / PKT_LINKSTATS share code 0b00
    return "up"


def decode_packet(pkt, direction=None, init=None, ota_version=None, sf=None):
    """Decode a single CRC-valid ELRS OTA packet.
    pkt: 8 or 13 bytes. Returns dict with 'direction','type',fields, plus
    'crc_init'/'ota_version'/'sf' when provided (for identification)."""
    size = len(pkt)
    if size not in (OTA4_SIZE, OTA8_SIZE):
        return None
    ptype = pkt[0] & 0b11
    if direction is None:
        direction = infer_direction(ptype, size)
    if size == OTA8_SIZE:
        out = decode_ota8(pkt, direction)
    else:
        out = decode_ota4(pkt, direction)
    if init is not None:
        out["crc_init"] = "0x%04x" % init
    if ota_version is not None:
        out["ota_version"] = ota_version
    if sf is not None:
        out["sf"] = sf
    # The UID low two bytes are recoverable from a known init & ota_version:
    # init = (uid4<<8|uid5) ^ (ov<<8)  ->  uid4uid5 = init ^ (ov<<8)
    if init is not None and ota_version is not None:
        uid_low = (init ^ (ota_version << 8)) & 0xFFFF
        out["uid_low2"] = "0x%02x%02x" % (uid_low >> 8, uid_low & 0xFF)
    return out


# ---------------------------------------------------------------------------
# Scan a demodulated byte buffer for CRC-valid packets (replaces elrs_live's
# try_decode, but returns rich decoded dicts).
# ---------------------------------------------------------------------------
def try_decode(buffer, uid=None, ota_candidates=OTA_VERSION_IDS, sf=None):
    """Scan buffer for CRC-valid OTA8/OTA4 packets. Returns list of decoded
    dicts. If uid given, only that uid's init is tried (fast + correct).
    Otherwise all 15 OTA_VERSION_ID inits are tried (bind/unknown mode) --
    each hit also records which init/ov validated it."""
    hits = []
    inits = []
    if uid is not None:
        for ov in ota_candidates:
            inits.append(crc_init_for_uid(uid, ov))
    else:
        inits = list(ota_candidates)  # bind mode: init == OTA_VERSION_ID

    def check(pkt, size, is8, init, ov, off):
        if is8:
            calc = CRC16.calc(pkt[:11], init)
            stored = pkt[11] | (pkt[12] << 8)
            ok = calc == stored
        else:
            p = bytearray(pkt)
            type_bits = p[0] & 0b11
            # stored 14-bit CRC = crcHigh (top 6 bits of byte0) << 8 | crcLow (byte7)
            stored = ((p[0] >> 2) << 8) | p[7]
            # CRC is computed over the packet with the crcHigh bits zeroed
            # (byte0 keeps only its 2 type bits), over bytes 0..6 (7 bytes)
            calc = CRC14.calc(bytes([type_bits]) + bytes(p[1:7]), init)
            ok = calc == stored
        if ok:
            d = decode_packet(bytes(pkt), init=init, ota_version=ov, sf=sf)
            if d:
                d["_offset"] = off
                hits.append(d)
            return True
        return False

    for size, is8 in ((OTA8_SIZE, True), (OTA4_SIZE, False)):
        for off in range(0, len(buffer) - size + 1, size):
            pkt = buffer[off:off + size]
            for i, init in enumerate(inits):
                ov = ota_candidates[i] if uid is not None else i
                if check(pkt, size, is8, init, ov, off):
                    break
    return hits


if __name__ == "__main__":
    # self-test: build a known RCDATA OTA4 with a proper 4x10 packer,
    # validate the CRC round-trips, decode it, check the channel values.
    def pack_4x10(vals):
        """Inverse of unpack_4x10: four 10-bit values -> 5 bytes (LSB-first)."""
        out = bytearray(5)
        bitpos = 0
        for v in vals:
            v &= 0x3FF
            # write 10 bits starting at bitpos
            lo = bitpos // 8
            shift = bitpos % 8
            out[lo] |= (v << shift) & 0xFF
            if shift + 10 > 8:
                out[lo + 1] |= (v >> (8 - shift)) & 0xFF
            bitpos += 10
        return bytes(out)

    def build_ota4_rc(ch10, switches, armed, init):
        # Build with the SAME split the validator uses, so the round-trip is
        # self-consistent (we are testing the decode path, not a layout guess).
        p = bytearray(8)
        p[0] = PKT_RCDATA & 0b11
        p[1:6] = pack_4x10(ch10)
        p[6] = (switches & 0x7F) | (armed << 7)
        calc_input = bytes([p[0] & 0b11]) + bytes(p[1:7])
        crc = CRC14.calc(calc_input, init)          # 14-bit value
        # store: byte0 = type(2) | (crc>>8 in top 6 bits); byte7 = crc & 0xFF
        p[0] |= ((crc >> 8) & 0b111111) << 2
        p[7] = crc & 0xFF
        return bytes(p)

    # bind mode: try_decode uses init == OTA_VERSION_ID (1..15); pick one
    init = 0x0A
    ch = [512, 512, 300, 800]
    pkt = build_ota4_rc(ch, 0b0000001, 1, init)
    # round-trip the channel packer
    got = unpack_4x10(pkt[1:6])
    assert got == ch, ("channel round-trip failed", got, ch)
    # verify the validator's CRC expression round-trips on this packet
    stored = ((pkt[0] >> 2) << 8) | pkt[7]
    calc = CRC14.calc(bytes([pkt[0] & 0b11]) + bytes(pkt[1:7]), init)
    assert calc == stored, ("crc round-trip failed", hex(calc), hex(stored))
    dec = try_decode(pkt, uid=None)
    print("built OTA4 RCDATA, CRC hit:", bool(dec))
    if dec:
        d = dec[0]
        print("  type=%s dir=%s armed=%s" % (d["type"], d["direction"], d["is_armed"]))
        print("  channels_us=", d["channels_us"])
        print("  crc_init=%s ota_version=%s uid_low2=%s"
              % (d.get("crc_init"), d.get("ota_version"), d.get("uid_low2")))
    assert dec, "try_decode should find the packet"
    print("SELFTEST OK")
