"""
Open Drone ID (Remote ID) message decoder — ASTM F3411 / ASD-STAN prEN
4709-002, as implemented in opendroneid/opendroneid-core-c.

This is the single highest-value identity source for the project's goal: a
standards-compliant drone broadcasts its serial number, operator ID and
operator location in the clear over Bluetooth LE advertising and/or Wi-Fi
(beacon + NAN). This module decodes the 25-byte message format and the
message pack, and collapses a pack into one normalised "drone" dict that the
fusion layer keys by identity.

Ported from tools/odr_detect.py (itself a port of libopendroneid/opendroneid.c
and odid_wifi.h). The transport extraction (btmon / pcap / NAN) lives with the
source adapters; here we decode bytes -> fields, which is pure and testable.

A matching `build_pack()` synthesiser is included so the decoder can be
round-trip tested offline and so the SDR-only operator (no BLE/Wi-Fi monitor
hardware yet) can drive the dashboard in replay mode with realistic data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Protocol constants (opendroneid-core-c)
# ---------------------------------------------------------------------------
ODID_MESSAGE_SIZE = 25
ODID_ID_SIZE = 20
ODID_STR_SIZE = 23
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

SPEED_DIV_LOW = 0.25
SPEED_DIV_HIGH = 0.75
VSPEED_DIV = 0.5
LATLON_MULT = 10000000
ALT_DIV = 0.5
ALT_ADDER = 1000
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


# ---------------------------------------------------------------------------
# Little-endian helpers (ports of opendroneid.c decode* helpers)
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
    proto = m[0] & 0x0F
    uatype, idtype = m[1] & 0x0F, m[1] >> 4
    raw = bytes(m[2:22])
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


def decode_self_id(m):
    desctype = m[1]
    desc = bytes(m[2:25]).split(b"\x00", 1)[0].decode("ascii", "replace")
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
    raw = bytes(m[2:22])
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
    MSG_SELF_ID: decode_self_id,
    MSG_SYSTEM: decode_system,
    MSG_OPERATOR_ID: decode_operator_id,
}


def check_pack_content(msgs) -> bool:
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
    """Decode a Message Pack at b[off]. Returns (dict, consumed) or (None, 0)."""
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
# Normalisation: a decoded pack -> one drone-identity dict for the fusion layer
# ---------------------------------------------------------------------------
@dataclass
class DroneReport:
    """Normalised Remote ID observation, flattened from a message pack."""
    uas_id: str | None = None
    id_type: str | None = None
    ua_type: str | None = None
    operator_id: str | None = None
    status: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_geo_m: float | None = None
    height_m: float | None = None
    speed_h_ms: float | None = None
    speed_v_ms: float | None = None
    direction_deg: float | None = None
    operator_latitude: float | None = None
    operator_longitude: float | None = None
    self_id: str | None = None
    extra: dict = field(default_factory=dict)

    def identity(self) -> str | None:
        """Best stable identity key (serial/registration/UUID, else operator)."""
        return self.uas_id or self.operator_id


def summarize(messages: list[dict]) -> DroneReport:
    """Collapse the messages of one pack (or a list of singles) into a report."""
    r = DroneReport()
    for m in messages:
        t = m.get("type")
        if t == "BASIC_ID":
            # Prefer a serial/registration id over a session UUID.
            if r.uas_id is None or m["id_type"] in ("SERIAL_NUMBER",
                                                    "CAA_REGISTRATION_ID"):
                r.uas_id = m["uas_id"]
                r.id_type = m["id_type"]
                r.ua_type = m["ua_type"]
        elif t == "LOCATION":
            r.status = m["status"]
            r.latitude = m["latitude"]
            r.longitude = m["longitude"]
            r.altitude_geo_m = m["altitude_geo_m"]
            r.height_m = m["height_m"]
            r.speed_h_ms = m["speed_h_ms"]
            r.speed_v_ms = m["speed_v_ms"]
            r.direction_deg = m["direction_deg"]
        elif t == "SYSTEM":
            r.operator_latitude = m["operator_latitude"]
            r.operator_longitude = m["operator_longitude"]
        elif t == "OPERATOR_ID":
            r.operator_id = m["operator_id"]
        elif t == "SELF_ID":
            r.self_id = m["desc"]
    return r


# ---------------------------------------------------------------------------
# Synthesiser — the inverse, for round-trip tests and replay-mode demo data.
# Encodes the same fields the decoders read, so decode(build(x)) == x.
# ---------------------------------------------------------------------------
def _enc_latlon(deg: float) -> int:
    v = int(round(deg * LATLON_MULT))
    return v & 0xFFFFFFFF


def _enc_alt(m: float) -> int:
    return int(round((m + ALT_ADDER) / ALT_DIV)) & 0xFFFF


def _pad(s: str, n: int) -> bytes:
    raw = s.encode("ascii", "replace")[:n]
    return raw + b"\x00" * (n - len(raw))


def build_basic_id(uas_id: str, id_type: int = 1, ua_type: int = 2) -> bytes:
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_BASIC_ID << 4) | 0x02           # type | proto v2
    m[1] = (id_type << 4) | (ua_type & 0x0F)
    m[2:22] = _pad(uas_id, ODID_ID_SIZE)
    return bytes(m)


def build_location(lat: float, lon: float, alt_geo_m: float = 120.0,
                   height_m: float = 100.0, speed_h_ms: float = 12.0,
                   status: int = 2) -> bytes:
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_LOCATION << 4) | 0x02
    m[1] = (status << 4)                         # speedMult=0, ew=0, ground=0
    m[2] = 0                                      # direction (0 deg)
    m[3] = min(255, int(round(speed_h_ms / SPEED_DIV_LOW)))
    m[4] = 0                                      # vertical speed
    lat_e = _enc_latlon(lat)
    lon_e = _enc_latlon(lon)
    m[6:10] = lat_e.to_bytes(4, "little")
    m[10:14] = lon_e.to_bytes(4, "little")
    m[14:16] = _enc_alt(alt_geo_m).to_bytes(2, "little")   # baro
    m[16:18] = _enc_alt(alt_geo_m).to_bytes(2, "little")   # geo
    m[18:20] = _enc_alt(height_m).to_bytes(2, "little")
    return bytes(m)


def build_system(op_lat: float, op_lon: float) -> bytes:
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_SYSTEM << 4) | 0x02
    m[1] = 0x01                                   # op location type = LIVE_GNSS
    m[2:6] = _enc_latlon(op_lat).to_bytes(4, "little")
    m[6:10] = _enc_latlon(op_lon).to_bytes(4, "little")
    m[18:20] = _enc_alt(0.0).to_bytes(2, "little")
    return bytes(m)


def build_operator_id(operator_id: str) -> bytes:
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_OPERATOR_ID << 4) | 0x02
    m[1] = 0
    m[2:22] = _pad(operator_id, ODID_ID_SIZE)
    return bytes(m)


def build_pack(messages: list[bytes]) -> bytes:
    """Wrap single messages into a Message Pack (what BLE/Wi-Fi NAN carry)."""
    n = len(messages)
    if not (1 <= n <= ODID_PACK_MAX_MESSAGES):
        raise ValueError(f"pack holds 1..{ODID_PACK_MAX_MESSAGES} messages")
    hdr = bytes([(MSG_PACKED << 4) | 0x02, ODID_MESSAGE_SIZE, n])
    return hdr + b"".join(messages)
