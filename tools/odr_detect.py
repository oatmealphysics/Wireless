#!/usr/bin/env python3
"""odr_detect.py -- capture and decode Open Drone ID (Remote ID) broadcasts.

Part of the ELRS-SDR research project. This tool is for the Remote ID
protocol class (ASTM F3411 / ASD-STAN prEN 4709-002), which is NOT the
ExpressLRS/LoRa link: ODID rides Bluetooth LE advertising (1 MHz LE
GFSK, 2.402-2.480 GHz) and/or Wi-Fi (802.11 beacons and Wi-Fi NAN on the
same 2.4 GHz ISM band). See odr_detect.md in this directory for the
protocol summary and capture-method tradeoffs.

Message parsing is a Python port of the reference implementation in
opendroneid/opendroneid-core-c (libopendroneid/opendroneid.c,
odid_wifi.h/wifi.c). The on-wire 25-byte message format is:

    byte 0:  [MessageType:4][ProtoVersion:4]   (LSB-first bitfields)
    ...per-type fields...

Message types:
    0 Basic ID   -- UAS identity: serial number / CAA registration / UUID
    1 Location   -- lat/lon, altitude (geo + baro), height, direction,
                    horizontal + vertical speed, status, timestamp
    2 Auth       -- authentication data pages (UAS ID signature, etc.)
    3 Self ID    -- free-text identifier
    4 System     -- operator location, area radius/ceiling/floor, EU class
    5 Operator ID-- operator identifier string
    F Pack       -- up to 9 of the above messages in one 227-byte blob
                    (what BLE FRDID and Wi-Fi NAN/beacon carry)

Transport encodings handled:
  * BLE: BT 4 legacy or BT 5 extended advertising payload containing an
         AD structure "Service Data - 16-bit UUID", UUID 0xFFFA (ASTM
         International), AD code 0x0D ("Open Drone ID"), followed by the
         FRDID: 1-byte message counter + ODID Message Pack.
         The full advertising payload is taken directly from btmon output
         (it carries LE Coded / extended payloads that hci2sco-style text
         decoders truncate to the legacy 31 bytes).
  * Wi-Fi NAN: 802.11 management Action frame, DA 51-6F-9A-01-00-00,
         NAN Service Discovery (cat 0x04, action 0x09, OUI 50-6F-9A,
         type 0x13), service descriptor attribute (id 0x03, service id
         88-69-19-9D-92-09, instance 0x01, control 0x10), then
         ODID service info (1-byte counter) + Message Pack.
  * Wi-Fi Beacon: 802.11 Beacon with vendor-specific IE (id 0xDD,
         OUI FA-0B-BC, type 0x0D) containing the same service info +
         Message Pack.

Usage:
    python3 odr_detect.py --source btmon -f /tmp/btmon.pcap   (or --source btmon-live)
    python3 odr_detect.py --source pcap -f /tmp/wifi-24ghz.pcap
    python3 odr_detect.py --source stdin                      (one frame per line, hex)
    python3 odr_detect.py --selftest

Captured frames are printed in real time and tee'd as JSON Lines to
--out (default odr_<timestamp>.jsonl).

Dependencies: Python 3.8+ stdlib only (scapy used opportunistically for
pcap input if installed; otherwise a built-in minimal pcap reader).
"""

import argparse
import bisect
import datetime as _dt
import json
import os
import re
import select
import struct
import subprocess
import sys

# ---------------------------------------------------------------------------
# Protocol constants (ASTM F3411 / ASD-STAN prEN 4709-002, as implemented
# in opendroneid-core-c)
# ---------------------------------------------------------------------------

ODID_MESSAGE_SIZE = 25          # bytes per single message
ODID_ID_SIZE = 20               # UAS ID / operator ID field size
ODID_STR_SIZE = 23              # Self ID description size
ODID_AUTH_MAX_PAGES = 16
ODID_AUTH_PAGE_ZERO_DATA_SIZE = 17
ODID_AUTH_PAGE_NONZERO_DATA_SIZE = 23
ODID_BASIC_ID_MAX_MESSAGES = 2
ODID_PACK_MAX_MESSAGES = 9

MSG_BASIC_ID = 0
MSG_LOCATION = 1
MSG_AUTH = 2
MSG_SELF_ID = 3
MSG_SYSTEM = 4
MSG_OPERATOR_ID = 5
MSG_PACKED = 0xF
MSG_INVALID = 0xFF

MSG_NAMES = {
    0: "BASIC_ID", 1: "LOCATION", 2: "AUTH", 3: "SELF_ID",
    4: "SYSTEM", 5: "OPERATOR_ID", 0xF: "PACK",
}

SPEED_DIV_LOW = 0.25            # m/s per LSB, speedMult == 0
SPEED_DIV_HIGH = 0.75           # m/s per LSB, speedMult == 1
VSPEED_DIV = 0.5                # m/s per LSB, vertical speed
LATLON_MULT = 10000000          # 1e-7 degree per LSB
ALT_DIV = 0.5                   # m per LSB
ALT_ADDER = 1000                # encode offset: -1000 m
INV_DIR = 361.0
INV_SPEED_H = 255.0
INV_SPEED_V = 63
INV_ALT = -1000.0
INV_TIMESTAMP = 0xFFFF

IDTYPE_NAMES = {
    0: "NONE", 1: "SERIAL_NUMBER", 2: "CAA_REGISTRATION_ID",
    3: "UTM_ASSIGNED_UUID", 4: "SPECIFIC_SESSION_ID",
}
UATYPE_NAMES = {
    0: "NONE", 1: "AEROPLANE", 2: "HELICOPTER_OR_MULTIROTOR", 3: "GYROPLANE",
    4: "HYBRID_LIFT", 5: "ORNITHOPTER", 6: "GLIDER", 7: "KITE",
    8: "FREE_BALLOON", 9: "CAPTIVE_BALLOON", 10: "AIRSHIP",
    11: "FREE_FALL_PARACHUTE", 12: "ROCKET", 13: "TETHERED_POWERED_AIRCRAFT",
    14: "GROUND_OBSTACLE", 15: "OTHER",
}
STATUS_NAMES = {
    0: "UNDECLARED", 1: "GROUND", 2: "AIRBORNE", 3: "EMERGENCY",
    4: "REMOTE_ID_SYSTEM_FAILURE",
}
AUTHTYPE_NAMES = {
    0: "NONE", 1: "UAS_ID_SIGNATURE", 2: "OPERATOR_ID_SIGNATURE",
    3: "MESSAGE_SET_SIGNATURE", 4: "NETWORK_REMOTE_ID",
    5: "SPECIFIC_AUTHENTICATION",
}
DESCTYPE_NAMES = {0: "TEXT", 1: "EMERGENCY", 2: "EXTENDED_STATUS"}
OP_LOC_NAMES = {0: "TAKEOFF", 1: "LIVE_GNSS", 2: "FIXED"}
CLASSIF_NAMES = {0: "UNDECLARED", 1: "EU"}
EU_CAT_NAMES = {0: "UNDECLARED", 1: "OPEN", 2: "SPECIFIC", 3: "CERTIFIED"}
EU_CLASS_NAMES = {
    0: "UNDECLARED", 1: "C0", 2: "C1", 3: "C2", 4: "C3", 5: "C4", 6: "C5", 7: "C6",
}

# BLE AD structure type for "Service Data - 16-bit UUID"
AD_TYPE_SERVICE_DATA_16 = 0x16
BLE_SERVICE_UUID_LE = b"\xfa\xff"      # 0xFFFA little-endian
BLE_AD_CODE_ODID = 0x0D

# Wi-Fi NAN constants (from opendroneid-core-c wifi.c)
NAN_DA = bytes([0x51, 0x6F, 0x9A, 0x01, 0x00, 0x00])
NAN_OUI = bytes([0x50, 0x6F, 0x9A])    # Wi-Fi Alliance
NAN_OUI_TYPE = 0x13
NAN_CATEGORY = 0x04
NAN_ACTION_CODE = 0x09
NAN_SERVICE_ID = bytes([0x88, 0x69, 0x19, 0x9D, 0x92, 0x09])
NAN_DESCRIBER_ATTR_ID = 0x03
BEACON_VENDOR_IE_ID = 0xDD
BEACON_OUI = bytes([0xFA, 0x0B, 0xBC])  # ASD-STAN
BEACON_OUI_TYPE = 0x0D

IEEE80211_FTYPE_MGMT = 0x00
IEEE80211_STYPE_BEACON = 0x0080
IEEE80211_STYPE_ACTION = 0x00D0


# ---------------------------------------------------------------------------
# Little-endian decoders (ports of opendroneid.c decode* helpers)
# ---------------------------------------------------------------------------

def _le_u16(b, o):
    return b[o] | (b[o + 1] << 8)


def _le_u32(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


def _le_i32(b, o):
    v = _le_u32(b, o)
    return v - 0x100000000 if v & 0x80000000 else v


def _le_i8(b, o):
    v = b[o]
    return v - 256 if v & 0x80 else v


def decode_direction(direction_enc, ew):
    return float(direction_enc) + 180.0 if ew else float(direction_enc)


def decode_speed_h(speed_enc, mult):
    if mult:
        return speed_enc * SPEED_DIV_HIGH + 0xFF * SPEED_DIV_LOW
    return speed_enc * SPEED_DIV_LOW


def decode_speed_v(enc):
    return enc * VSPEED_DIV


def decode_latlon(enc):
    return enc / LATLON_MULT


def decode_altitude(enc):
    return enc * ALT_DIV - ALT_ADDER


def decode_timestamp(enc):
    return INV_TIMESTAMP if enc == INV_TIMESTAMP else enc / 10.0


def decode_area_radius(enc):
    return enc * 10


def message_type(byte0):
    mt = byte0 >> 4
    return mt if mt in MSG_NAMES else MSG_INVALID


# ---------------------------------------------------------------------------
# Single-message decoders (ports of decode*Message from opendroneid.c)
# ---------------------------------------------------------------------------

def decode_basic_id(m):
    """m: 25-byte message. Returns dict or None."""
    proto = m[0] & 0x0F
    uatype, idtype = m[1] & 0x0F, m[1] >> 4
    raw = m[2:22]
    if idtype in (1, 2):
        uid = raw.split(b"\x00", 1)[0].decode("ascii", "replace")
    else:
        uid = raw.hex()
    return {
        "type": "BASIC_ID",
        "proto_version": proto,
        "id_type": IDTYPE_NAMES.get(idtype, "UNKNOWN(%d)" % idtype),
        "ua_type": UATYPE_NAMES.get(uatype, "UNKNOWN(%d)" % uatype),
        "uas_id": uid,
        "uas_id_raw": raw.hex(),
    }


def decode_location(m):
    b1 = m[1]
    speed_mult = b1 & 0x1
    ew = (b1 >> 1) & 0x1
    height_type = (b1 >> 2) & 0x1
    status = (b1 >> 4) & 0x0F
    horiz_acc = m[19] & 0x0F
    vert_acc = m[19] >> 4
    speed_acc = m[20] & 0x0F
    baro_acc = m[20] >> 4
    ts = _le_u16(m, 21)
    ts_acc = m[23] & 0x0F
    lat = decode_latlon(_le_i32(m, 6))
    lon = decode_latlon(_le_i32(m, 10))
    return {
        "type": "LOCATION",
        "proto_version": m[0] & 0x0F,
        "status": STATUS_NAMES.get(status, "UNKNOWN(%d)" % status),
        "direction_deg": decode_direction(m[2], ew),
        "speed_h_ms": round(decode_speed_h(m[3], speed_mult), 2),
        "speed_v_ms": decode_speed_v(_le_i8(m, 4)),
        "latitude": round(lat, 7),
        "longitude": round(lon, 7),
        "altitude_baro_m": round(decode_altitude(_le_u16(m, 14)), 1),
        "altitude_geo_m": round(decode_altitude(_le_u16(m, 16)), 1),
        "height_ref": "OVER_GROUND" if height_type else "OVER_TAKEOFF",
        "height_m": round(decode_altitude(_le_u16(m, 18)), 1),
        "horiz_accuracy": horiz_acc,
        "vert_accuracy": vert_acc,
        "baro_accuracy": baro_acc,
        "speed_accuracy": speed_acc,
        "timestamp_s_since_hour": decode_timestamp(ts),
        "timestamp_accuracy": ts_acc,
    }


def decode_auth(m):
    page = m[1] & 0x0F
    authtype = m[1] >> 4
    out = {
        "type": "AUTH",
        "proto_version": m[0] & 0x0F,
        "auth_type": AUTHTYPE_NAMES.get(authtype, "UNKNOWN(%d)" % authtype),
        "page": page,
    }
    if page == 0:
        last_page = m[2]
        length = m[3]
        if last_page >= ODID_AUTH_MAX_PAGES:
            return None
        cap = ODID_AUTH_PAGE_ZERO_DATA_SIZE + last_page * ODID_AUTH_PAGE_NONZERO_DATA_SIZE
        if length > cap:
            return None
        out["last_page"] = last_page
        out["length"] = length
        out["timestamp_s_2019"] = _le_u32(m, 4)
        out["auth_data"] = m[8:25].hex()
    else:
        if page >= ODID_AUTH_MAX_PAGES:
            return None
        out["auth_data"] = m[2:25].hex()
    return out


def decode_self_id(m):
    desctype = m[1]
    desc = m[2:25].split(b"\x00", 1)[0].decode("ascii", "replace")
    return {
        "type": "SELF_ID",
        "proto_version": m[0] & 0x0F,
        "desc_type": DESCTYPE_NAMES.get(desctype, "UNKNOWN(%d)" % desctype),
        "desc": desc,
    }


def decode_system(m):
    b1 = m[1]
    op_loc = b1 & 0x03
    classif = (b1 >> 2) & 0x07
    class_eu = m[17] & 0x0F
    cat_eu = m[17] >> 4
    out = {
        "type": "SYSTEM",
        "proto_version": m[0] & 0x0F,
        "operator_location_type": OP_LOC_NAMES.get(op_loc, "UNKNOWN(%d)" % op_loc),
        "classification": CLASSIF_NAMES.get(classif, "UNKNOWN(%d)" % classif),
        "operator_latitude": round(decode_latlon(_le_i32(m, 2)), 7),
        "operator_longitude": round(decode_latlon(_le_i32(m, 6)), 7),
        "area_count": _le_u16(m, 10),
        "area_radius_m": decode_area_radius(m[12]),
        "area_ceiling_m": round(decode_altitude(_le_u16(m, 13)), 1),
        "area_floor_m": round(decode_altitude(_le_u16(m, 15)), 1),
        "operator_altitude_geo_m": round(decode_altitude(_le_u16(m, 18)), 1),
        "timestamp_s_2019": _le_u32(m, 20),
    }
    if classif == 1:
        out["eu_category"] = EU_CAT_NAMES.get(cat_eu, "UNKNOWN(%d)" % cat_eu)
        out["eu_class"] = EU_CLASS_NAMES.get(class_eu, "UNKNOWN(%d)" % class_eu)
    return out


def decode_operator_id(m):
    raw = m[2:22]
    oid = raw.split(b"\x00", 1)[0].decode("ascii", "replace")
    return {
        "type": "OPERATOR_ID",
        "proto_version": m[0] & 0x0F,
        "operator_id_type": m[1],
        "operator_id": oid,
        "operator_id_raw": raw.hex(),
    }


_SINGLE_DECODERS = {
    MSG_BASIC_ID: decode_basic_id,
    MSG_LOCATION: decode_location,
    MSG_AUTH: decode_auth,
    MSG_SELF_ID: decode_self_id,
    MSG_SYSTEM: decode_system,
    MSG_OPERATOR_ID: decode_operator_id,
}


def check_pack_content(msgs):
    """Port of checkPackContent(). msgs: list of 25-byte messages."""
    counts = [0] * 6
    for m in msgs:
        mt = message_type(m[0])
        if mt > MSG_OPERATOR_ID:
            return False
        counts[mt] += 1
    if (counts[MSG_BASIC_ID] > ODID_BASIC_ID_MAX_MESSAGES or
            counts[MSG_LOCATION] > 1 or
            counts[MSG_AUTH] > ODID_AUTH_MAX_PAGES or
            counts[MSG_SELF_ID] > 1 or
            counts[MSG_SYSTEM] > 1 or
            counts[MSG_OPERATOR_ID] > 1):
        return False
    return True


def decode_pack(b, off=0):
    """Decode a Message Pack starting at b[off]. Returns (dict, consumed_bytes)
    or (None, 0). The pack header is 3 bytes: [type|proto][msgSize][nMsgs]."""
    if len(b) < off + 3:
        return None, 0
    hdr0 = b[off]
    if (hdr0 >> 4) != MSG_PACKED:
        return None, 0
    single_size = b[off + 1]
    n = b[off + 2]
    if single_size != ODID_MESSAGE_SIZE:
        return None, 0
    if not (1 <= n <= ODID_PACK_MAX_MESSAGES):
        return None, 0
    total = 3 + n * ODID_MESSAGE_SIZE
    # The pack self-describes its size; tolerate trailing bytes (padding,
    # next IE, radiotap residue) but require the full declared content.
    if len(b) < off + total:
        return None, 0
    msgs = [b[off + 3 + i * ODID_MESSAGE_SIZE:
              off + 3 + (i + 1) * ODID_MESSAGE_SIZE] for i in range(n)]
    if not check_pack_content(msgs):
        return None, 0
    decoded = []
    for m in msgs:
        mt = message_type(m[0])
        dec = _SINGLE_DECODERS.get(mt)
        if dec:
            d = dec(m)
            if d is not None:
                decoded.append(d)
    return {"proto_version": hdr0 & 0x0F, "n_messages": n,
            "messages": decoded}, total


def decode_message(b, off=0):
    """Decode a single 25-byte message or a pack at b[off]."""
    if len(b) < off + 3:
        return None, 0
    mt = message_type(b[off])
    if mt == MSG_PACKED:
        return decode_pack(b, off)
    dec = _SINGLE_DECODERS.get(mt)
    if dec is None or len(b) < off + ODID_MESSAGE_SIZE:
        return None, 0
    d = dec(b[off:off + ODID_MESSAGE_SIZE])
    if d is None:
        return None, 0
    return d, ODID_MESSAGE_SIZE


# ---------------------------------------------------------------------------
# Transport extraction
# ---------------------------------------------------------------------------

def _ad_structure_iter(payload):
    """Yield (ad_type, value) from a GAP AD payload (len|type|value triples)."""
    i = 0
    while i + 1 < len(payload):
        ln = payload[i]
        if ln == 0:
            i += 1
            continue
        if i + ln >= len(payload) + 1:
            break
        ad_type = payload[i + 1]
        val = payload[i + 2:i + 1 + ln]
        yield ad_type, val
        i += 1 + ln


def extract_odid_from_ble(payload):
    """Find the ODID service-data AD in a BT LE advertising payload.

    Accepts either the raw advertising payload (AD structures only, as
    printed by btmon) or a full LE scan report. Returns (counter, pack_bytes)
    or None.
    """
    # If the first byte is not a plausible AD length (1..39), assume the
    # caller handed us a scan report with a 3-byte prefix (len, evt, subevt).
    start = 0
    if payload and payload[0] < 3:
        if len(payload) >= 3:
            start = 3
    for ad_type, val in _ad_structure_iter(payload[start:]):
        if ad_type != AD_TYPE_SERVICE_DATA_16 or len(val) < 3:
            continue
        if val[0:2] != BLE_SERVICE_UUID_LE:
            continue
        rest = val[2:]
        if not rest:
            continue
        # AD code 0x0D ("Open Drone ID"), then 1-byte counter, then pack
        if rest[0] == BLE_AD_CODE_ODID:
            if len(rest) < 2:
                continue
            return rest[1], rest[2:]
        # Some implementations omit the AD application code
        if rest and len(rest) > 3:
            return rest[0], rest[1:]
    return None


def extract_odid_from_nan(buf):
    """Wi-Fi NAN action frame. Returns (sa_mac, counter, pack_bytes) or None.

    Layout (from odid_wifi.h / wifi.c receive path):
      mgmt hdr (24) | NSD (7: cat,action,oui[3],type)
      | descr attribute: attr_id(1) len(2 LE) service_id(6) instance(1)
        req_instance(1) control(1) info_len(1) | service info: counter(1)
        + pack
    """
    if len(buf) < 24 + 6 + 14:
        return None
    fc = buf[0] | (buf[1] << 8)
    if (fc & 0xFFF0) != (IEEE80211_FTYPE_MGMT | IEEE80211_STYPE_ACTION):
        return None
    if buf[4:10] != NAN_DA:
        return None
    sa = buf[10:16]
    o = 24
    # NAN Service Discovery header: category, action, OUI[3], type = 6 bytes
    if buf[o] != NAN_CATEGORY or buf[o + 1] != NAN_ACTION_CODE:
        return None
    if buf[o + 2:o + 5] != NAN_OUI or buf[o + 5] != NAN_OUI_TYPE:
        return None
    o += 6
    if buf[o] != NAN_DESCRIBER_ATTR_ID:
        return None
    attr_len = buf[o + 1] | (buf[o + 2] << 8)
    if buf[o + 3:o + 9] != NAN_SERVICE_ID:
        return None
    instance_id = buf[o + 9]
    service_control = buf[o + 11]
    info_len = buf[o + 12]
    if instance_id != 0x01 or service_control != 0x10:
        return None
    if info_len < 1:
        return None
    if attr_len != (13 + info_len):
        return None
    o += 13
    if len(buf) < o + info_len:
        return None
    si = buf[o:o + info_len]
    return sa, si[0], si[1:]


def extract_odid_from_beacon(buf):
    """Wi-Fi Beacon with ASD-STAN vendor IE. Returns (bssid, counter, pack)."""
    if len(buf) < 24 + 12 + 16:
        return None
    fc = buf[0] | (buf[1] << 8)
    if (fc & 0xFFF0) != (IEEE80211_FTYPE_MGMT | IEEE80211_STYPE_BEACON):
        return None
    bssid = buf[16:22]
    # walk information elements starting after mgmt(24)+timestamp(8)+intval(2)+caps(2)
    o = 36
    while o + 2 <= len(buf):
        eid = buf[o]
        elen = buf[o + 1]
        if o + 2 + elen > len(buf):
            break
        if eid == BEACON_VENDOR_IE_ID and elen >= 4:
            body = buf[o + 2:o + 2 + elen]
            if body[0:3] == BEACON_OUI and body[3] == BEACON_OUI_TYPE and len(body) >= 5:
                si = body[4:]
                if len(si) >= 1:
                    return bssid, si[0], si[1:]
        o += 2 + elen
    return None


def extract_odid(raw):
    """Try all three transports on a raw frame. Returns dict or None:
    {transport, addr, counter, pack}"""
    r = extract_odid_from_nan(raw)
    if r:
        sa, counter, pack = r
        return {"transport": "WiFi-NAN",
                "addr": ":".join("%02x" % x for x in sa),
                "counter": counter, "pack": pack}
    r = extract_odid_from_beacon(raw)
    if r:
        bssid, counter, pack = r
        return {"transport": "WiFi-Beacon",
                "addr": ":".join("%02x" % x for x in bssid),
                "counter": counter, "pack": pack}
    r = extract_odid_from_ble(raw)
    if r:
        counter, pack = r
        return {"transport": "BLE", "addr": None,
                "counter": counter, "pack": pack}
    return None


# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------

BTMON_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]*)\]\s+\S+:\s+ACL data.*?\|(?P<payload>[0-9a-fA-F ]*)\|")


def btmon_line_to_frames(line):
    """Parse one btmon CLI line into candidate payloads.

    btmon prints each HCI packet as:
        [timestamp] type: ... | hex hex hex ... | [rssi]
    For LE Advertising Report the payload contains the adv data after the
    event header; the AD structures begin at offset 14 (event hdr 3 +
    addr 6 + event-specific 5). We try several offsets and let
    extract_odid() pick the one that parses.
    """
    m = BTMON_LINE_RE.match(line.strip())
    if not m:
        return []
    hexs = m.group("payload").split()
    if len(hexs) < 15:
        return []
    raw = bytes(int(h, 16) for h in hexs)
    # ACL data line: HCI pkt header (4) + HCI evt header (3) + adv evt (14)
    # -> AD payload at 4+3+14 = 21 when the line is an ACL data frame;
    # when btmon already stripped to the HCI event, offset is 3+14 = 17.
    candidates = []
    for off in (21, 17, 0):
        if len(raw) > off:
            c = raw[off:]
            if extract_odid_from_ble(c) is not None:
                return [c]
    # fall through: hand the whole line's payload and trimmed variants on
    # to the generic extractor (it tolerates a 3-byte scan-report prefix)
    return [raw[21:]] if len(raw) > 21 else [raw]


def iter_btmon_live(adapter=None):
    """Yield (timestamp_str, frame_bytes) from a live `btmon` process.

    btmon prints one line per HCI packet to stdout in its CLI text format.
    Requires root (it opens the Bluetooth monitor interface).
    """
    cmd = ["btmon"]
    if adapter:
        cmd += ["-w"]  # not needed; btmon follows all by default
    cmd = ["btmon"]  # keep simple: follow all adapters, filter in code
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True,
                            bufsize=1)
    try:
        for line in proc.stdout:
            for frame in btmon_line_to_frames(line):
                yield line[:22].strip(), frame
    finally:
        proc.terminate()


def read_pcap_frames(path):
    """Yield frame byte strings from a libpcap file. Uses scapy if
    available, otherwise a built-in minimal reader. DLT types handled:
    113 (BT LE link layer, used by btmon), 127 (IEEE 802.11), 1 (Ethernet,
    for Wireshark-exported 802.11 with the radiotap stripped)."""
    try:
        from scapy.utils import PcapReader
        from scapy.all import Raw
        with PcapReader(path) as pr:
            for pkt in pr:
                raw = bytes(pkt.getlayer(Raw) or pkt)
                # for 802.11 captured with radiotap header, the frame
                # itself is at pkt; extract_odid() looks for the mgmt
                # header anywhere only at offset 0, so also try common
                # radiotap header lengths
                yield raw
                if raw and raw[0] in (0x00, 0x01, 0x04):
                    # possible radiotap: parse header length
                    try:
                        itf = raw[0]
                        if itf & 0x01:
                            hdr_len = 4
                        elif itf & 0x02:
                            hdr_len = 8
                        elif itf & 0x04:
                            hdr_len = 12
                        else:
                            hdr_len = 4
                        hdr_len = ((hdr_len + 3) // 4) * 4
                        if hdr_len < len(raw):
                            yield raw[hdr_len:]
                    except Exception:
                        pass
        return
    except ImportError:
        pass
    # minimal libpcap reader
    with open(path, "rb") as f:
        ghdr = f.read(24)
        if len(ghdr) < 24:
            raise ValueError("not a pcap file: %s" % path)
        magic = struct.unpack("<I", ghdr[:4])[0]
        if magic == 0xA1B2C3D4:
            endian = "<"
        elif magic == 0xD4C3B2A1:
            endian = ">"
        else:
            raise ValueError("not a pcap file: %s" % path)
        dlt = struct.unpack(endian + "I", ghdr[20:24])[0]
        while True:
            phdr = f.read(16)
            if len(phdr) < 16:
                break
            inclen = struct.unpack(endian + "I", phdr[8:12])[0]
            frame = f.read(inclen)
            if len(frame) < inclen:
                break
            if dlt == 113:  # BT LE linklayer: [len(2)][type(1)][data...]
                if len(frame) >= 3:
                    yield frame[3:]
                continue
            yield frame


# ---------------------------------------------------------------------------
# Real-time reporting + jsonl tee
# ---------------------------------------------------------------------------

class Reporter:
    def __init__(self, out_path, quiet=False):
        self.out = open(out_path, "a", encoding="utf-8") if out_path else None
        self.quiet = quiet
        self.count = 0

    def report(self, frame_bytes, meta, decoded):
        self.count += 1
        ts = _dt.datetime.now().isoformat(timespec="milliseconds")
        rec = {
            "ts": ts,
            "meta": meta,
            "decoded": decoded,
            "raw_hex": frame_bytes.hex(),
        }
        if self.out:
            self.out.write(json.dumps(rec, separators=(",", ":")) + "\n")
            self.out.flush()
        if not self.quiet:
            self._print(meta, decoded, ts)

    def _print(self, meta, decoded, ts):
        t = meta.get("transport", "?")
        addr = meta.get("addr") or ""
        ctr = meta.get("counter", "?")
        n = decoded.get("n_messages", 1) if decoded else 0
        line = "[%s] %s %s counter=%s" % (ts[11:], t, addr, ctr)
        if decoded is None:
            print(line + "  UNDECODED")
            return
        if decoded.get("type") == "PACK" or "messages" in decoded:
            print(line + "  PACK (%d msgs):" % n)
            for m in decoded["messages"]:
                print("    - " + self._describe(m))
        else:
            print(line + "  " + self._describe(decoded))

    @staticmethod
    def _describe(m):
        t = m["type"]
        if t == "BASIC_ID":
            return "BASIC_ID  %s  %s  id=%s" % (
                m["ua_type"], m["id_type"], m["uas_id"])
        if t == "LOCATION":
            return "LOCATION  %s  lat=%.6f lon=%.6f alt_geo=%.1fm alt_baro=%.1fm h=%.1fm(%s) " \
                   "dir=%.0fdeg vh=%.2fm/s vv=%.1fm/s ts=%ss" % (
                m["status"], m["latitude"], m["longitude"],
                m["altitude_geo_m"], m["altitude_baro_m"], m["height_m"],
                m["height_ref"], m["direction_deg"], m["speed_h_ms"],
                m["speed_v_ms"], m["timestamp_s_since_hour"])
        if t == "AUTH":
            extra = ""
            if "last_page" in m:
                extra = " pages=0..%d len=%d" % (m["last_page"], m["length"])
            return "AUTH  %s  page=%d%s data=%s" % (
                m["auth_type"], m["page"], extra,
                (m["auth_data"][:16] + "…") if len(m["auth_data"]) > 16 else m["auth_data"])
        if t == "SELF_ID":
            return "SELF_ID  %s  %r" % (m["desc_type"], m["desc"])
        if t == "SYSTEM":
            s = "SYSTEM  oploc=%s  oplat=%.6f oplon=%.6f opalt=%.1fm " \
                "area=r%dm/%.1fm..%.1fm n=%d" % (
                m["operator_location_type"], m["operator_latitude"],
                m["operator_longitude"], m["operator_altitude_geo_m"],
                m["area_radius_m"], m["area_floor_m"], m["area_ceiling_m"],
                m["area_count"])
            if "eu_class" in m:
                s += "  EU:%s/%s" % (m["eu_category"], m["eu_class"])
            return s
        if t == "OPERATOR_ID":
            return "OPERATOR_ID  type=%d  id=%s" % (m["operator_id_type"], m["operator_id"])
        return json.dumps(m)

    def close(self):
        if self.out:
            self.out.close()
            print("\n[%d frames] jsonl -> %s" % (self.count, self.out.name))


# ---------------------------------------------------------------------------
# Self-test: build known-good frames, extract, decode, assert
# ---------------------------------------------------------------------------

def _test_ble_frame():
    pack_hdr = bytes([0xF2, ODID_MESSAGE_SIZE, 2])
    # Basic ID: serial "TEST-123", UA multrotor(2), id type serial(1)
    basic = bytearray(ODID_MESSAGE_SIZE)
    basic[0] = 0x02
    basic[1] = (1 << 4) | 2
    basic[2:22] = b"TEST-123".ljust(20, b"\x00")
    # Location: airborne, lat 48.8566, lon 2.3522, geo alt 120m, v speed 1.5
    loc = bytearray(ODID_MESSAGE_SIZE)
    loc[0] = 0x12
    loc[1] = (2 << 4) | 0  # status airborne, no flags
    loc[2] = 90            # 90 deg
    loc[3] = 40            # 40 * 0.25 = 10 m/s
    loc[4] = 3             # 3 * 0.5 = 1.5 m/s up
    loc[6:10] = struct.pack("<i", int(48.8566 * LATLON_MULT))
    loc[10:14] = struct.pack("<i", int(2.3522 * LATLON_MULT))
    loc[14:16] = struct.pack("<H", int((120 + 1000) / 0.5))
    loc[16:18] = struct.pack("<H", int((120 + 1000) / 0.5))
    loc[18:20] = struct.pack("<H", int((50 + 1000) / 0.5))
    loc[19] = 0x2B          # vert acc 2, horiz acc 11
    loc[20] = 0x03          # baro acc 3, speed acc 3
    loc[21:23] = struct.pack("<H", 1234)  # 123.4s
    loc[23] = 0x05          # ts acc 5
    pack = pack_hdr + bytes(basic) + bytes(loc)
    counter = 0x2A
    adv_val = BLE_SERVICE_UUID_LE + bytes([BLE_AD_CODE_ODID, counter]) + pack
    adv = bytes([len(adv_val) + 1, AD_TYPE_SERVICE_DATA_16]) + adv_val
    # trailing generic-access AD (real adv payloads carry more structures)
    adv += bytes([4, 0x09, 0xC0, 0x00, 0x01])
    return adv


def _test_nan_frame():
    pack_hdr = bytes([0xF2, ODID_MESSAGE_SIZE, 1])
    op = bytearray(ODID_MESSAGE_SIZE)
    op[0] = 0x52
    op[1] = 0
    op[2:22] = b"PIL0001".ljust(20, b"\x00")
    pack = pack_hdr + bytes(op)
    sa = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
    mgmt = bytearray(24)
    mgmt[0:2] = struct.pack("<H", IEEE80211_FTYPE_MGMT | IEEE80211_STYPE_ACTION)
    mgmt[4:10] = NAN_DA
    mgmt[10:16] = sa
    nsd = bytes([NAN_CATEGORY, NAN_ACTION_CODE]) + NAN_OUI + bytes([NAN_OUI_TYPE])
    si = bytes([0x77]) + pack
    # service descriptor attribute: id(1) len(2) service_id(6) instance(1)
    # req_instance(1) control(1) info_len(1)  -> fixed part = 13 bytes
    descr = bytes([NAN_DESCRIBER_ATTR_ID]) + struct.pack(
        "<H", 13 + len(si)) + NAN_SERVICE_ID + bytes([0x01, 0x00, 0x10, len(si)])
    return bytes(mgmt) + nsd + descr + si


def _test_beacon_frame():
    pack_hdr = bytes([0xF2, ODID_MESSAGE_SIZE, 1])
    selfid = bytearray(ODID_MESSAGE_SIZE)
    selfid[0] = 0x32
    selfid[1] = 0
    desc = b"my-drone-042"
    assert len(desc) <= ODID_STR_SIZE
    selfid[2:2 + len(desc)] = desc
    pack = pack_hdr + bytes(selfid)
    bssid = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
    mgmt = bytearray(24)
    mgmt[0:2] = struct.pack("<H", IEEE80211_FTYPE_MGMT | IEEE80211_STYPE_BEACON)
    mgmt[4:10] = b"\xff" * 6
    mgmt[10:16] = bssid
    mgmt[16:22] = bssid
    body = bytearray()
    body += struct.pack("<QHH", 0, 100, 0x0431)          # ts, interval, caps
    body += bytes([0, 8]) + b"DroneID\x00"               # SSID IE (8 bytes)
    body += bytes([1, 1, 0x8C])                           # rates
    # service info = counter(1) + pack; the pack self-describes its size, so
    # pass the whole remainder and let decode_pack trim it.
    vendor_body = BEACON_OUI + bytes([BEACON_OUI_TYPE]) + bytes([0x3C]) + pack
    # vendor IE length = everything after the 2-byte (id, len) header
    body += bytes([BEACON_VENDOR_IE_ID, len(vendor_body)]) + vendor_body
    return bytes(mgmt) + bytes(body)


def selftest():
    failures = 0

    def check(name, cond, detail=""):
        nonlocal failures
        if cond:
            print("PASS  %s" % name)
        else:
            failures += 1
            print("FAIL  %s  %s" % (name, detail))

    # BLE
    adv = _test_ble_frame()
    ext = extract_odid(adv)
    check("ble.extract", ext is not None and ext["transport"] == "BLE")
    d, consumed = decode_message(ext["pack"]) if ext else (None, 0)
    check("ble.pack.n=2", d is not None and d.get("n_messages") == 2, repr(d))
    if d and d.get("messages"):
        basic = [m for m in d["messages"] if m["type"] == "BASIC_ID"]
        loc = [m for m in d["messages"] if m["type"] == "LOCATION"]
        check("ble.basic.id", basic and basic[0]["uas_id"] == "TEST-123", repr(basic))
        check("ble.loc.lat", loc and abs(loc[0]["latitude"] - 48.8566) < 1e-4, repr(loc))
        check("ble.loc.alt", loc and abs(loc[0]["altitude_geo_m"] - 120.0) < 0.5, repr(loc))
        check("ble.loc.vspeed", loc and abs(loc[0]["speed_v_ms"] - 1.5) < 0.01, repr(loc))

    # NAN
    nan = _test_nan_frame()
    ext = extract_odid(nan)
    check("nan.extract", ext is not None and ext["transport"] == "WiFi-NAN",
          "extract failed on %d-byte frame" % len(nan))
    if ext:
        check("nan.addr", ext["addr"] == "aa:bb:cc:dd:ee:ff", ext["addr"])
        d, _ = decode_message(ext["pack"])
        ops = [m for m in (d["messages"] if d else []) if m["type"] == "OPERATOR_ID"]
        check("nan.op.id", ops and ops[0]["operator_id"] == "PIL0001", repr(d))

    # Beacon
    bc = _test_beacon_frame()
    ext = extract_odid(bc)
    check("beacon.extract", ext is not None and ext["transport"] == "WiFi-Beacon")
    if ext:
        d, _ = decode_message(ext["pack"])
        sids = [m for m in (d["messages"] if d else []) if m["type"] == "SELF_ID"]
        check("beacon.self.desc", sids and sids[0]["desc"] == "my-drone-042", repr(d))

    # round-trip of a raw 25-byte single message (no transport)
    single = _test_nan_frame()  # reuse; but test direct single decode:
    pack_hdr = bytes([0xF2, ODID_MESSAGE_SIZE, 1])
    op = bytearray(ODID_MESSAGE_SIZE)
    op[0] = 0x52
    op[2:22] = b"PIL0001".ljust(20, b"\x00")
    d, consumed = decode_message(pack_hdr + bytes(op))
    check("direct.single", d is not None and consumed == 28, "consumed=%d" % consumed)

    print("\n%d checks, %d failures" % (
        12, failures))
    return failures


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def decoded_event(frame, meta):
    """Extract + decode one raw frame. Returns a JSON-ready event dict or None.

    Event shape: {ts, transport, addr, counter, src, decoded, raw_hex}
    where decoded is the message (or {'type':'PACK','messages':[...]}).
    """
    ext = extract_odid(frame)
    if ext is None:
        return None
    decoded, _ = decode_message(ext["pack"])
    return {
        "ts": _dt.datetime.now().isoformat(timespec="milliseconds"),
        "transport": ext["transport"],
        "addr": ext["addr"],
        "counter": ext["counter"],
        "src": meta.get("src"),
        "rssi": meta.get("rssi"),
        "decoded": decoded,
        "raw_hex": frame.hex(),
    }


def iter_source(source, path=None):
    """Yield (frame_bytes, meta) from the named source.

    source: btmon-live | btmon | pcap | stdin
    Used by both the CLI main() and odr_web.py.
    """
    if source == "stdin":
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if re.fullmatch(r"[0-9a-fA-F ]+", line):
                yield bytes(int(x, 16) for x in line.split()), {"src": "stdin"}
            else:
                for f in btmon_line_to_frames(line):
                    yield f, {"src": "stdin-btmon-line"}
    elif source == "btmon":
        if not path:
            raise ValueError("--source btmon needs -f <logfile>")
        with open(path) as fh:
            for line in fh:
                for f in btmon_line_to_frames(line):
                    yield f, {"src": "btmon-log"}
    elif source == "btmon-live":
        for _ts, frame in iter_btmon_live():
            yield frame, {"src": "btmon-live"}
    elif source == "pcap":
        if not path:
            raise ValueError("--source pcap needs -f <pcap>")
        for i, frame in enumerate(read_pcap_frames(path)):
            yield frame, {"src": "pcap", "idx": i}
    else:
        raise ValueError("unknown source: %s" % source)


def main():
    ap = argparse.ArgumentParser(
        description="Capture and decode Open Drone ID broadcasts "
                    "(BLE via btmon, Wi-Fi NAN/Beacon via pcap).")
    ap.add_argument("--source", choices=["btmon-live", "btmon", "pcap", "stdin"],
                    default="btmon-live")
    ap.add_argument("-f", "--file", help="input file (pcap for --source pcap, "
                    "btmon text log for --source btmon)")
    ap.add_argument("--out", help="jsonl output path (default odr_<ts>.jsonl)")
    ap.add_argument("--quiet", action="store_true",
                    help="do not print decoded lines (jsonl only)")
    ap.add_argument("--selftest", action="store_true",
                    help="run built-in frame round-trip tests and exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(1 if selftest() else 0)

    out_path = args.out or ("odr_%s.jsonl" % _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    rep = Reporter(out_path, quiet=args.quiet)
    print("ODID capture: source=%s out=%s  (Ctrl-C to stop)" % (args.source, out_path))

    try:
        for frame, meta in iter_source(args.source, args.file):
            ev = decoded_event(frame, meta)
            if ev is None:
                continue
            rep.report(frame, {k: ev[k] for k in
                                ("transport", "addr", "counter") if ev[k] is not None},
                       ev["decoded"])
    except KeyboardInterrupt:
        pass
    finally:
        rep.close()


if __name__ == "__main__":
    main()
