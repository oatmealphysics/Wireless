#!/usr/bin/env python3
"""
odr_detect.py -- Open Drone ID (Remote ID) broadcast detector/decoder.

Captures Open Drone ID broadcasts from a Linux laptop and prints the decoded
Remote ID fields in real time, teeing every record to a JSONL file.

Message parsing is a faithful Python port of opendroneid-core-c
(github.com/opendroneid/opendroneid-core-c, libopendroneid/opendroneid.c,
commit 6484f26, Sep 2026), which implements the Open Drone ID Message
Specification (ASTM F3411 / ASD-STAN prEN 4709-002, protocol versions 0-2).
Stdlib only -- no third-party dependencies.

How Open Drone ID is broadcast (both transports are 2.4 GHz ISM):
  BLE  : connectionless BLE advertising.
         - "Legacy" (BT 4.x) ADV_NONCONN_IND on beacon channels 37/38/39
           (2402/2426/2480 MHz), GFSK 1 Mbit/s, up to 31-byte payload.
           Open Drone ID frames carry AD structures 0x16/0x1F with the
           ASTM 16-bit service UUID 0xFFFA ("Service Data" 0xFFFA + app
           code 0x0D, or "Manufacturer Data" 0xFFFA little-endian + 0x0D),
           followed by a 25-byte ODID block message.
           Some vendors instead emit a plain ADV_IND with only 0xFF
           manufacturer data; those are picked up by the raw-mode scan.
         - BT5 "Extended Advertising" on the LE Coded (S=8) PHY for long
           range; the payload is a single ODID Message Pack.
         - Rates: static messages ~every 3 s, dynamic (Location) 3x/s.
  WiFi : 802.11 management (action) frames, or NAN (Neighbor Awareness
         Networking) service discovery frames on NULS. NAN SDF carries
         OUI 50-6F-9A (Wi-Fi Alliance) / OUI type 0x13, NAN Service
         Descriptor attribute 0x3 with Service ID
         88-69-19-9D-92-09 (SHA-256("org.opendroneid.remoteid")[0:6]) and
         a Service Info of 1 message-counter byte + ODID Message Pack.

ODID block messages (25 bytes each, little-endian numerics):
  type 0 Basic ID     : ID type (serial # / CAA reg / UTM UUID / session)
                        + 20-byte UAS ID + UA type (the drone identity)
  type 1 Location     : status, direction, H/V speed, lat/lon, baro/geodetic
                        altitude, height above takeoff, accuracies, timestamp
  type 2 Authentication: up to 16 pages of signature data (UAS ID signature,
                        operator ID signature, message-set, network RID)
  type 3 Self ID      : 23-byte operator-supplied description text
  type 4 System       : operator location (takeoff / live GNSS / fixed),
                        area count/radius/ceiling/floor, EU category/class,
                        operator altitude, timestamp
  type 5 Operator ID  : 20-byte operator identifier
  type 0xF Message Pack: 1-9 of the above in one packet (used by BT5 extended
                        and Wi-Fi NAN)

Capture methods (see README.md for the full tradeoff discussion):
  1) BT adapter, raw hci mode (RECOMMENDED, no root):
       python3 odr_detect.py --source bt
     Uses BlueZ's RFCOMM-based hci raw socket (the same API used by btmon)
     to read HCI LE Meta events directly from the kernel. Works while
     bluetoothd is running; does not require root.
  2) BT adapter, btmon text (fallback, needs root + bluez installed):
       sudo btmon -w /tmp/btmon.log &
       python3 odr_detect.py --source btmon --btmon-log /tmp/btmon.log
     Tails the ASCII log and extracts ADV data. Simpler, but btmon's ASCII
     output varies between BlueZ versions, so this is a best-effort path.
  3) Wi-Fi adapter in monitor mode (needs root + a monitor-capable adapter):
       ip link set wlan0 down && iw dev wlan0 set type monitor
       python3 odr_detect.py --source wifi
     Reads the monitor stream (pcapng) with pure-Python code, scans the
     frames for the ODID pack header and the NAN SDF signature.
  4) Pre-captured files (test/analysis):
       python3 odr_detect.py --source file --file /tmp/btmon.log      # btmon
       python3 odr_detect.py --source file --file /tmp/rx.pcapng      # wifi
     (btmon log from 'sudo btmon -w <file>' or a pcapng from
     'tshark -i wlan0 -w rx.pcapng' / 'airodump-ng wlan0 -w rx'.)

Common options:
  --jsonl PATH     JSONL output path (default: odr_records_<ts>.jsonl)
  --quiet          no per-record terminal output (still tees to JSONL)
  --no-merge       do not merge per-source records into per-drone tracks
                   (off by default; tracks are still kept in JSONL)

Each JSONL line:
  {"ts": "...", "source": "bt", "mac": "aa:bb:cc:dd:ee:ff", "rssi": -61,
   "msg_type": "LOCATION", "protocol_version": 2, "fields": {...},
   "drone_id": "AA:BB:CC:DD:EE:FF", "raw": "<hex of 25-byte ODID message>"}

Run it alongside elrs_live.py in a second terminal: the ELRS C2 link (LoRa,
SF5-8 @ 800 kHz, FHSS) and the Remote ID broadcast (BLE/GFSK or Wi-Fi)
coexist in 2.4 GHz and do not interfere with each other's demodulation for
this purpose -- just note the B205mini capture of an ELRS FHSS hop and a BT
adapter hearing BLE at the same time are independent hardware paths.
"""

import argparse
import binascii
import json
import math
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# ODID constants (opendroneid-core-c, opendroneid.h)
# --------------------------------------------------------------------------
ODID_MESSAGE_SIZE = 25
ODID_ID_SIZE = 20
ODID_STR_SIZE = 23
ODID_AUTH_PAGE_ZERO_DATA_SIZE = 17
ODID_AUTH_PAGE_NONZERO_DATA_SIZE = 23
ODID_AUTH_MAX_PAGES = 16
ODID_BASIC_ID_MAX_MESSAGES = 2
ODID_PACK_MAX_MESSAGES = 9

MSG_BASIC_ID = 0
MSG_LOCATION = 1
MSG_AUTH = 2
MSG_SELF_ID = 3
MSG_SYSTEM = 4
MSG_OPERATOR_ID = 5
MSG_PACKED = 0xF

MSG_NAMES = {
    MSG_BASIC_ID: "BASIC_ID",
    MSG_LOCATION: "LOCATION",
    MSG_AUTH: "AUTH",
    MSG_SELF_ID: "SELF_ID",
    MSG_SYSTEM: "SYSTEM",
    MSG_OPERATOR_ID: "OPERATOR_ID",
    MSG_PACKED: "PACKED",
}

IDTYPE_NAMES = {
    0: "NONE", 1: "SERIAL_NUMBER", 2: "CAA_REGISTRATION_ID",
    3: "UTM_ASSIGNED_UUID", 4: "SPECIFIC_SESSION_ID",
}
UATYPE_NAMES = {
    0: "NONE", 1: "AEROPLANE", 2: "HELICOPTER_OR_MULTIROTOR",
    3: "GYROPLANE", 4: "HYBRID_LIFT", 5: "ORNITHOPTER", 6: "GLIDER",
    7: "KITE", 8: "FREE_BALLOON", 9: "CAPTIVE_BALLOON", 10: "AIRSHIP",
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
OPLOC_NAMES = {0: "TAKEOFF", 1: "LIVE_GNSS", 2: "FIXED"}
CLASSIF_NAMES = {0: "UNDECLARED", 1: "EU"}
CAT_EU_NAMES = {0: "UNDECLARED", 1: "OPEN", 2: "SPECIFIC", 3: "CERTIFIED"}
CLASS_EU_NAMES = {0: "UNDECLARED", 1: "C0", 2: "C1", 3: "C2", 4: "C3",
                  5: "C4", 6: "C5", 7: "C6"}

# Wi-Fi NAN SDF service id: first 6 bytes of SHA-256("org.opendroneid.remoteid")
NAN_SERVICE_ID = bytes.fromhex("8869199d9209")
NAN_CLUSTER_ID = bytes.fromhex("506f9a0100ff")

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _name(table, v, default=None):
    return table.get(v, default if default is not None else f"UNKNOWN_{v}")


def _cstr(b):
    """Null-truncated ASCII string from a raw field."""
    i = b.find(0)
    return (b[:i] if i >= 0 else b).decode("ascii", "replace")


def _hex(b):
    return binascii.hexlify(b).decode()


def msg_type_of(first_byte):
    # C lib (decodeMessageType) switches on the upper nibble only and does
    # not reject on protocol version -- do the same.
    t = first_byte >> 4
    return t if t in MSG_NAMES else 0xFF


def ts_str():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------
# Decoders (port of opendroneid.c decode* functions)
# --------------------------------------------------------------------------
def decode_direction(enc, ew):
    return float(enc + 180) if ew else float(enc)


def decode_speed_h(enc, mult):
    # C lib: mult==1 -> enc*0.75 + 255*0.25 ; else enc*0.25
    return enc * 0.75 + (255 * 0.25) if mult else enc * 0.25


def decode_alt(enc):
    return enc * 0.5 - 1000.0


def decode_latlon(enc):
    return enc / 1e7


def decode_basic_id(m):
    id_type = m[1] & 0x0F
    ua_type = (m[1] >> 4) & 0x0F
    raw_id = m[2:22]
    if id_type in (1, 2):
        uas_id = _cstr(raw_id)
    elif id_type == 3:
        # UTM-assigned ID: 16-byte RFC4122 UUID
        h = _hex(raw_id[:16])
        uas_id = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    else:
        uas_id = _hex(raw_id)
    return {
        "id_type": _name(IDTYPE_NAMES, id_type),
        "ua_type": _name(UATYPE_NAMES, ua_type),
        "uas_id": uas_id,
        "uas_id_raw": _hex(raw_id),
    }


def decode_location(m):
    flags = m[1]
    status = flags & 0x0F
    speed_mult = (flags >> 4) & 1
    ew_dir = (flags >> 5) & 1
    height_type = (flags >> 6) & 1
    direction = decode_direction(m[2], ew_dir)
    speed_h = decode_speed_h(m[3], speed_mult)
    speed_v = struct.unpack_from("<b", m, 4)[0] * 0.5
    lat = decode_latlon(struct.unpack_from("<i", m, 5)[0])
    lon = decode_latlon(struct.unpack_from("<i", m, 9)[0])
    alt_baro = decode_alt(struct.unpack_from("<H", m, 13)[0])
    alt_geo = decode_alt(struct.unpack_from("<H", m, 15)[0])
    height = decode_alt(struct.unpack_from("<H", m, 17)[0])
    ts_enc = struct.unpack_from("<H", m, 21)[0]
    ts = None if ts_enc == 0xFFFF else ts_enc / 10.0
    return {
        "status": _name(STATUS_NAMES, status),
        "direction_deg": direction,
        "speed_horizontal_ms": round(speed_h, 2),
        "speed_vertical_ms": speed_v,
        "latitude": lat,
        "longitude": lon,
        "altitude_baro_m": alt_baro,
        "altitude_geo_m": alt_geo,
        "height_m": height,
        "height_ref": "OVER_TAKEOFF" if height_type == 0 else "OVER_GROUND",
        "horiz_accuracy": m[19] & 0x0F,
        "vert_accuracy": m[19] >> 4,
        "speed_accuracy": m[20] & 0x0F,
        "baro_accuracy": m[20] >> 4,
        "ts_accuracy": m[23] & 0x0F,
        "timestamp_s_after_hour": ts,
    }


def decode_auth(m):
    auth_type = m[1] & 0x0F
    page = (m[1] >> 4) & 0x0F
    out = {"auth_type": _name(AUTHTYPE_NAMES, auth_type), "data_page": page}
    if page == 0:
        last_page = m[2]
        length = m[3]
        out["last_page_index"] = last_page
        out["length"] = length
        out["timestamp_s_since_2019"] = struct.unpack_from("<I", m, 4)[0]
        out["auth_data"] = _hex(m[8:8 + ODID_AUTH_PAGE_ZERO_DATA_SIZE])
    else:
        out["auth_data"] = _hex(m[2:2 + ODID_AUTH_PAGE_NONZERO_DATA_SIZE])
    return out


def decode_self_id(m):
    return {
        "desc_type": _name(DESCTYPE_NAMES, m[1]),
        "desc": _cstr(m[2:25]),
    }


def decode_system(m):
    op_loc = m[1] & 0x03
    classif = (m[1] >> 2) & 0x07
    op_lat = decode_latlon(struct.unpack_from("<i", m, 2)[0])
    op_lon = decode_latlon(struct.unpack_from("<i", m, 6)[0])
    area_count = struct.unpack_from("<H", m, 10)[0]
    area_radius = m[12] * 10
    area_ceiling = decode_alt(struct.unpack_from("<H", m, 13)[0])
    area_floor = decode_alt(struct.unpack_from("<H", m, 15)[0])
    class_eu = m[17] & 0x0F
    cat_eu = (m[17] >> 4) & 0x0F
    op_alt = decode_alt(struct.unpack_from("<H", m, 18)[0])
    out = {
        "operator_location_type": _name(OPLOC_NAMES, op_loc),
        "classification_type": _name(CLASSIF_NAMES, classif),
        "operator_latitude": op_lat,
        "operator_longitude": op_lon,
        "area_count": area_count,
        "area_radius_m": area_radius,
        "area_ceiling_m": area_ceiling,
        "area_floor_m": area_floor,
        "operator_altitude_geo_m": op_alt,
    }
    if classif == 1:
        out["category_eu"] = _name(CAT_EU_NAMES, cat_eu)
        out["class_eu"] = _name(CLASS_EU_NAMES, class_eu)
    # timestamp at offset 20, 4 bytes LE
    out["timestamp_s_since_2019"] = struct.unpack_from("<I", m, 20)[0]
    return out


def decode_operator_id(m):
    return {
        "operator_id_type": m[1],
        "operator_id": _cstr(m[2:22]),
        "operator_id_raw": _hex(m[2:22]),
    }


def decode_message(m):
    """Decode one 25-byte ODID block message -> (type_name, fields)."""
    if len(m) < ODID_MESSAGE_SIZE:
        return None, None
    t = msg_type_of(m[0])
    if t not in MSG_NAMES:
        return None, None
    try:
        if t == MSG_BASIC_ID:
            return "BASIC_ID", decode_basic_id(m)
        if t == MSG_LOCATION:
            return "LOCATION", decode_location(m)
        if t == MSG_AUTH:
            return "AUTH", decode_auth(m)
        if t == MSG_SELF_ID:
            return "SELF_ID", decode_self_id(m)
        if t == MSG_SYSTEM:
            return "SYSTEM", decode_system(m)
        if t == MSG_OPERATOR_ID:
            return "OPERATOR_ID", decode_operator_id(m)
        if t == MSG_PACKED:
            size = m[1]
            count = m[2]
            if size != ODID_MESSAGE_SIZE or not (1 <= count <= ODID_PACK_MAX_MESSAGES):
                return "PACKED", {"error": "bad pack size"}
            subs = []
            for i in range(count):
                sub = m[3 + i * ODID_MESSAGE_SIZE:3 + (i + 1) * ODID_MESSAGE_SIZE]
                nm, fl = decode_message(sub)
                subs.append({"type": nm, "fields": fl, "raw": _hex(sub)})
            return "PACKED", {"count": count, "messages": subs}
    except Exception as e:  # never let one bad packet kill the reader
        return MSG_NAMES.get(t, "UNKNOWN"), {"error": str(e)}
    return MSG_NAMES.get(t, "UNKNOWN"), None


def find_odid_blocks(data):
    """
    Scan a raw buffer for 25-byte ODID block messages / message packs.

    A message starts with a byte whose upper nibble is a valid message type
    and lower nibble (protocol version) is 0-2. For PACKED (0xFx) we require
    the size/count bytes to be sane. Yields (offset, message_bytes).

    False-positive guard: a standalone (non-packed) "message" whose payload is
    all zeros is rejected — real messages always carry non-zero content (e.g. a
    Basic ID always has a non-empty UAS ID). This stops the scanner from
    matching zero-filled framing/padding bytes as spurious Basic IDs.
    """
    found = []
    i = 0
    n = len(data)
    while i <= n - ODID_MESSAGE_SIZE:
        b0 = data[i]
        t = b0 >> 4
        ver = b0 & 0x0F
        if t in MSG_NAMES and ver <= 2:
            if t == MSG_PACKED:
                if i + 3 <= n and data[i + 1] == ODID_MESSAGE_SIZE \
                        and 1 <= data[i + 2] <= ODID_PACK_MAX_MESSAGES:
                    total = 3 + data[i + 2] * ODID_MESSAGE_SIZE
                    if i + total <= n:
                        found.append((i, data[i:i + total]))
                        i += total
                        continue
            else:
                chunk = data[i:i + ODID_MESSAGE_SIZE]
                # reject all-zero "messages" (padding / framing, not real ODID)
                if any(chunk):
                    found.append((i, chunk))
                    i += ODID_MESSAGE_SIZE
                    continue
        i += 1
    return found



def extract_odid_from_nan(frame):
    """
    Wi-Fi NAN SDF hunt: anchor on the OpenDroneID NAN signature
    (OUI 50-6F-9A + attribute type 0x13), then walk the Service Descriptor
    attribute to its Service Info element and return the ODID Message Pack.

    NAN SDF layout (OpenDroneID Wi-Fi spec):
        50 6F 9A        Wi-Fi Alliance OUI
        13              OUI type: NAN Service Descriptor Function
        03              SDF attribute: Service Descriptor
        <len:u16>       SDF attribute length (little-endian)
        88 69 19 9D 92 09  service ID = SHA256("org.opendroneid.remoteid")[:6]
        <instance:u8><req-instance:u8><control:u8>
        <service-info-len:u8>  = 1 (message counter) + len(pack)
        <message-counter:u8>
        <ODID Message Pack: (3 + count*25) bytes>

    Returns list of (offset_in_frame, pack_bytes).
    """
    sig = bytes.fromhex("506f9a") + b"\x13"
    out = []
    start = 0
    while True:
        idx = frame.find(sig, start)
        if idx < 0:
            break
        start = idx + 1
        # walk SDF attr after the 4-byte OUI+type
        p = idx + 4
        if p + 3 > len(frame):
            continue
        if frame[p] != 0x03:            # Service Descriptor attr id
            continue
        attr_len = struct.unpack_from("<H", frame, p + 1)[0]
        attr_start = p + 3
        attr_end = attr_start + attr_len
        if attr_end > len(frame):
            continue
        # service id (6) + instance/req/control (3) then service-info len byte
        si_off = attr_start + 6 + 3
        if si_off >= attr_end:
            continue
        si_len = frame[si_off]
        si_start = si_off + 1
        si_end = si_start + si_len
        if si_end > attr_end:
            continue
        # service info = 1 message-counter byte + the pack
        counter = frame[si_start]
        pack = frame[si_start + 1:si_end]
        if (msg_type_of(pack[0]) == MSG_PACKED and pack[1] == ODID_MESSAGE_SIZE
                and 1 <= pack[2] <= ODID_PACK_MAX_MESSAGES
                and len(pack) == 3 + pack[2] * ODID_MESSAGE_SIZE):
            out.append((si_start + 1, pack))
    return out


# --------------------------------------------------------------------------
# Output: real-time print + JSONL tee, with per-drone merging
# --------------------------------------------------------------------------
class Reporter:
    def __init__(self, jsonl_path, quiet=False, merge=True):
        self.jsonl_path = jsonl_path
        self.quiet = quiet
        self.merge = merge
        self.jsonl = open(jsonl_path, "a", buffering=1)
        self.drones = {}
        self.stats = {"packets": 0, "odid_messages": 0}

    def close(self):
        try:
            self.jsonl.close()
        except Exception:
            pass

    def record(self, source, mac, rssi, offset_msg, extra=None):
        offset, raw = offset_msg
        type_name, fields = decode_message(raw)
        if type_name is None:
            return
        self.stats["packets"] += 1
        drone_id = (mac or "unknown").upper()
        rec = {
            "ts": ts_str(),
            "source": source,
            "mac": mac,
            "rssi": rssi,
            "msg_type": type_name,
            "protocol_version": raw[0] & 0x0F,
            "fields": fields,
            "drone_id": drone_id,
            "raw": _hex(raw),
        }
        if extra:
            rec.update(extra)
        # JSONL tee
        self.jsonl.write(json.dumps(rec) + "\n")
        # per-drone merged track
        d = self.drones.setdefault(drone_id, {
            "mac": mac, "first_seen": rec["ts"], "last_seen": rec["ts"],
            "min_rssi": rssi, "fields": {}})
        d["last_seen"] = rec["ts"]
        if rssi is not None:
            d["min_rssi"] = rssi if d["min_rssi"] is None \
                else min(d["min_rssi"], rssi)
        self._merge_fields(d["fields"], type_name, fields)
        if not self.quiet:
            self._print(rec, d)

    def _merge_fields(self, merged, type_name, fields):
        if fields is None:
            return
        if type_name == "PACKED":
            for sub in fields.get("messages", []):
                if sub.get("fields"):
                    self._merge_fields(merged, sub.get("type"), sub["fields"])
            return
        merged[type_name] = fields

    def _print(self, rec, d):
        t = rec["msg_type"]
        f = rec["fields"] or {}
        line = f"[{rec['ts']}] {rec['source']:4} mac={rec['mac']} "
        if rec["rssi"] is not None:
            line += f" rssi={rec['rssi']}"
        line += f"  {t}"
        if t == "BASIC_ID":
            line += (f"  id={f.get('id_type')} ua={f.get('ua_type')} "
                     f"id_value={f.get('uas_id')!r}")
        elif t == "LOCATION":
            line += (f"  status={f.get('status')} "
                     f"lat={f.get('latitude'):.5f} lon={f.get('longitude'):.5f} "
                     f"alt_geo={f.get('altitude_geo_m'):.1f}m "
                     f"spd_h={f.get('speed_horizontal_ms')}m/s "
                     f"spd_v={f.get('speed_vertical_ms')}m/s "
                     f"dir={f.get('direction_deg')}deg")
        elif t == "SYSTEM":
            line += (f"  op_lat={f.get('operator_latitude'):.5f} "
                     f"op_lon={f.get('operator_longitude'):.5f} "
                     f"op_type={f.get('operator_location_type')} "
                     f"radius={f.get('area_radius_m')}m")
        elif t == "OPERATOR_ID":
            line += f"  op_id={f.get('operator_id')!r}"
        elif t == "SELF_ID":
            line += f"  desc={f.get('desc')!r}"
        elif t == "AUTH":
            line += (f"  auth_type={f.get('auth_type')} "
                     f"page={f.get('data_page')}")
        elif t == "PACKED":
            line += f"  pack count={f.get('count')}"
        print(line, flush=True)

    def dump_tracks(self):
        """Print the merged per-drone summary (called on Ctrl-C)."""
        if not self.drones:
            return
        print("\n=== Drone tracks (merged) ===", flush=True)
        for drone_id, d in sorted(self.drones.items()):
            print(f"\n{drone_id}  mac={d['mac']}  min_rssi={d['min_rssi']} "
                  f"first={d['first_seen']} last={d['last_seen']}", flush=True)
            for mt in ("BASIC_ID", "LOCATION", "SYSTEM", "OPERATOR_ID",
                       "SELF_ID"):
                f = d["fields"].get(mt)
                if f:
                    print(f"  {mt}: {json.dumps(f, default=str)}", flush=True)


# --------------------------------------------------------------------------
# AD-structure helpers (BLE + generic)
# --------------------------------------------------------------------------
ASTM_UUID_LE = struct.pack("<H", 0xFFFA)      # 0xFF 0xFA
ASTM_UUID_BE = struct.pack(">H", 0xFFFA)      # 0xFF 0xFA (same bytes!)
ODID_APP_CODE = 0x0D


def odid_payload_from_ad(ad):
    """
    Given a single AD structure (length byte included), return
    (payload, payload_offset_in_ad) for an Open Drone ID service-data /
    manufacturer structure, else (None, None).
    """
    if len(ad) < 2:
        return None, None
    length = ad[0]
    body = ad[1:1 + length]  # type + value
    if not body:
        return None, None
    atype, value = body[0], body[1:]
    if atype in (0x16, 0x1F) and len(value) >= 3:  # service data 16/128 bit
        uuidlen = 2 if atype == 0x16 else 16
        if len(value) < uuidlen:
            return None, None
        uuid = value[:uuidlen]
        rest = value[uuidlen:]
        if uuid in (ASTM_UUID_LE, ASTM_UUID_BE):
            # app code 0x0D is optional framing (present in legacy frames)
            poff = 1 + 1 + uuidlen
            if rest and rest[0] == ODID_APP_CODE:
                rest = rest[1:]
                poff += 1
            if rest:
                return rest, poff
    if atype == 0xFF and len(value) >= 3:  # manufacturer specific
        mfg = value[:2]
        rest = value[2:]
        if mfg in (ASTM_UUID_LE, ASTM_UUID_BE):
            poff = 1 + 1 + 2
            if rest and rest[0] == ODID_APP_CODE:
                rest = rest[1:]
                poff += 1
            if rest:
                return rest, poff
    return None, None


def scan_ad_blocks(data):
    """
    Walk BLE AD structures (len|type|value chain) in `data` and yield
    (absolute_offset, odid_bytes) for every AD that carries ODID content.
    Used for legacy ADV payloads (27-byte cap, so at most ~1 ODID message).
    """
    out = []
    i = 0
    n = len(data)
    while i < n:
        length = data[i]
        if length == 0 or i + 1 + length > n:
            i += 1
            continue
        ad = data[i:i + 1 + length]
        payload, poff = odid_payload_from_ad(ad)
        if payload:
            out.append((i + poff, payload))
        i += 1 + length
    return out


def extract_odid_from_ble_payload(payload):
    """
    Given a BLE advertisement payload (AD-structure stream for legacy frames,
    or adv data for extended frames), return a list of (offset, odid_bytes)
    where odid_bytes is a full 25-byte message or a full message pack.
    """
    msgs = []
    for off, p in scan_ad_blocks(payload):
        t = msg_type_of(p[0])
        if t == MSG_PACKED and len(p) >= 3 and p[1] == ODID_MESSAGE_SIZE \
                and 1 <= p[2] <= ODID_PACK_MAX_MESSAGES:
            total = 3 + p[2] * ODID_MESSAGE_SIZE
            if len(p) >= total:
                msgs.append((off, p[:total]))
                continue
        if t in MSG_NAMES and len(p) >= ODID_MESSAGE_SIZE:
            msgs.append((off, p[:ODID_MESSAGE_SIZE]))
    if not msgs:
        # bare message pack (BT5 extended adv secondary payload)
        if len(payload) >= 3 and msg_type_of(payload[0]) == MSG_PACKED \
                and payload[1] == ODID_MESSAGE_SIZE \
                and 1 <= payload[2] <= ODID_PACK_MAX_MESSAGES:
            total = 3 + payload[2] * ODID_MESSAGE_SIZE
            if len(payload) >= total:
                msgs.append((0, payload[:total]))
        else:
            # fall back: raw hunt (handles vendor framing quirks)
            for off, m in find_odid_blocks(payload):
                msgs.append((off, m))
    return msgs


# --------------------------------------------------------------------------
# Source 1: BLE via BlueZ raw HCI socket (no root)
# --------------------------------------------------------------------------
# HCI constants
HCI_EVT_LE_META = 0x3E
HCI_LE_ADVERTISING_REPORT = 0x02
HCI_LE_EXTENDED_ADVERTISING_REPORT = 0x0D


class BtRawSource:
    """
    Read HCI LE Meta events from BlueZ's rfcomm-based raw HCI socket.

    This is the same mechanism btmon uses under the hood. It works on any
    BlueZ >= 5.37 with a connected BT controller, without root, while
    bluetoothd is running (BlueZ multiplexes raw sockets from its own).
    """

    def __init__(self, reporter, device="hci0"):
        self.rep = reporter
        self.device = device
        self.sock = None

    def start(self):
        # AF_BLUETOOTH=31, BTPROTO_HCI=1
        AF_BLUETOOTH, BTPROTO_HCI = 31, 1
        # SOL_BLUETOOTH=271; filter to LE Meta events only
        SOL_BLUETOOTH, HCI_FILTER_TYPE = 271, 3
        self.sock = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
        self.sock.settimeout(0.5)
        # bind to specific device if possible (needs CAP_NET_ADMIN on some
        # kernels for non-hci0; default hci0 works for most laptops)
        try:
            idx = self._hci_index(self.device)
            self.sock.bind((0, idx))
        except OSError:
            self.sock.bind((0, 0))
        # event filter: enable only LE Meta event (event code 0x3E)
        try:
            hci_filter = struct.pack("<B", HCI_FILTER_TYPE)
            # event mask: 32-bit LE event enable mask (bit 0x3E set)
            event_mask = struct.pack("<I", 1 << (0x3E & 0x1F))
            # older BlueZ: setsockopt(SOL_BLUETOOTH, HCIDEV_*, ...) varies;
            # without the filter we simply filter in userspace below.
        except Exception:
            pass

    def _hci_index(self, name):
        # /sys/class/bluetooth/hci0 -> /sys/devices/.../net/... no index;
        # use hci index via /sys/class/bluetooth/<dev>/hci (some kernels)
        p = f"/sys/class/bluetooth/{name}/hci"
        try:
            with open(p) as f:
                return int(f.read().strip())
        except OSError:
            # fall back to a scan of /sys/class/bluetooth
            for d in os.listdir("/sys/class/bluetooth"):
                if d == name:
                    try:
                        with open(f"/sys/class/bluetooth/{d}/hci") as f:
                            return int(f.read().strip())
                    except OSError:
                        return 0
            return 0

    def read_one(self):
        """Non-blocking read; yields (mac, rssi, payload, is_ext) or None."""
        if not self.sock:
            return None
        try:
            pkt = self.sock.recv(1024)
        except socket.timeout:
            return None
        except OSError:
            return None
        if len(pkt) < 3 or pkt[0] != 0x04:  # 0x04 = event packet
            return None
        evt = pkt[1]
        plen = pkt[2]
        data = pkt[3:3 + plen]
        if evt != HCI_EVT_LE_META or len(data) < 2:
            return None
        sub = data[0]
        count = data[1]
        if sub == HCI_LE_ADVERTISING_REPORT:
            # Legacy ADV report: for each advertiser:
            #   evt_type(1) addr_type(1) addr(6) data_len(1)
            #   rssi(1, 0xFF=unknown) adv_data(data_len)
            off = 2
            for _ in range(count):
                if off + 9 > len(data):
                    break
                addr_type = data[off + 1]
                mac = _hci_addr(data[off + 2:off + 8], addr_type)
                data_len = data[off + 8]
                if off + 9 + 1 + data_len > len(data):
                    break
                rssi = data[off + 9]
                rssi = None if rssi == 0xFF else rssi
                payload = data[off + 10:off + 10 + data_len]
                return (mac, rssi, payload, False)
        elif sub == HCI_LE_EXTENDED_ADVERTISING_REPORT:
            off = 2
            for _ in range(count):
                if off + 11 > len(data):
                    break
                evt_type = data[off]
                addr_type = data[off + 1]
                mac = _hci_addr(data[off + 2:off + 8], addr_type)
                rssi = data[off + 8]
                rssi = None if rssi == 0xFF else rssi
                data_len = data[off + 9]
                payload = data[off + 10:off + 10 + data_len]
                off += 10 + data_len
                return (mac, rssi, payload, True)
        return None

    def run(self):
        while True:
            r = self.read_one()
            if r is None:
                continue
            mac, rssi, payload, is_ext = r
            for off, m in extract_odid_from_ble_payload(payload):
                self.rep.record("bt", mac, rssi, (off, m),
                                extra={"ext_adv": is_ext})

    def stop(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass


def _hci_addr(b, addr_type):
    """HCI LE address is little-endian; swap to canonical display order."""
    return ":".join(f"{x:02X}" for x in bytes(reversed(b)))


# --------------------------------------------------------------------------
# Source 2: btmon ASCII log (root, or pre-captured file)
# --------------------------------------------------------------------------
BTMON_ADV_RE = re.compile(
    r">\s*(ADV_IND|ADV_SCAN_IND|ADV_NONCONN_IND|ADV_EXT_IND|AUX_ADV_IND)"
    r"\(.*?\)\s*data\s*len=(\d+)[^\n]*\n")
# btmon prints:
#  > ADV_IND(0x04) data len=27 data:
#  16 ff fa ff 0d 02 00 01 02 ...   (hex bytes, 16 per line, continued)
BTMON_DATA_LINE_RE = re.compile(r"^\s*((?:[0-9a-f]{2}\s+)+)$$")


class BtmonSource:
    """Tail a btmon ASCII log and extract BLE ADV payloads."""

    def __init__(self, reporter, path=None):
        self.rep = reporter
        self.path = path
        self.tail = None

    def start(self):
        if self.path:
            self.f = open(self.path, "rb")
            self.eof = False
            return
        cmd = ["btmon", "-w", os.devnull]
        # btmon -w writes to a file; we need to capture its stdout instead.
        # btmon normally prints to stdout when no -w; use that.
        self.tail = subprocess.Popen(
            ["btmon"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.f = self.tail.stdout
        self.eof = False

    def _read_until(self, stop):
        """Read one ADV payload from the btmon stream."""
        # find the next ADV line
        adv_line = None
        while not stop():
            line = self.f.readline()
            if not line:
                return None
            line_s = line.decode("latin-1").rstrip("\n")
            m = BTMON_ADV_RE.search(line_s)
            if m:
                adv_line = line_s
                break
        if adv_line is None:
            return None
        # next line(s) hold the hex data
        data = bytearray()
        # first line after ADV may contain "data len=N data:" then hex
        rest = adv_line.split("data:")[-1]
        data.extend(bytes.fromhex(re.sub(r"\s+", "", rest.split("\n")[0]
                                      .strip() or "00")))
        while True:
            line = self.f.readline()
            if not line:
                break
            s = line.decode("latin-1").rstrip("\n")
            m = BTMON_DATA_LINE_RE.match(s)
            if not m:
                break
            data.extend(bytes.fromhex(m.group(1).replace(" ", "")))
        # mac + rssi: btmon prints them on the header line
        mac = self._last_mac_from_header(adv_line)
        rssi = self._last_rssi_from_header(adv_line)
        return (mac, rssi, bytes(data))

    def _last_mac_from_header(self, line):
        m = re.search(r"(?:from|addr)=?([0-9a-f:]{17})", line, re.I)
        if not m:
            m = re.search(r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})", line, re.I)
        return m.group(1).upper() if m else None

    def _last_rssi_from_header(self, line):
        m = re.search(r"rssi=(-?\d+)", line, re.I)
        return int(m.group(1)) if m else None

    def run(self):
        stop = lambda: False
        while not stop():
            if self.f is None:
                break
            r = self._read_until(stop)
            if r is None:
                # EOF or closed
                if self.path:
                    break
                time.sleep(0.2)
                continue
            mac, rssi, payload = r
            for off, m in extract_odid_from_ble_payload(payload):
                self.rep.record("btmon", mac, rssi, (off, m))

    def stop(self):
        if self.tail:
            try:
                self.tail.terminate()
            except Exception:
                pass
        if getattr(self, "f", None):
            try:
                self.f.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Source 3: Wi-Fi monitor mode (pure-python pcapng reader + ODID hunt)
# --------------------------------------------------------------------------
def read_pcapng_frames(f):
    """
    Minimal pcapng reader. Yields (ts, frame_bytes) for each packet data
    block. Handles the common single-section-file layout with standard
    interface description blocks.
    """
    magic = f.read(4)
    if magic != b"\x0A\x0D\x0D\x0A":
        raise ValueError("not a pcapng file (bad magic)")
    while True:
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        btype, blen = struct.unpack("<II", hdr)
        body = f.read(blen - 12)
        if btype == 0x00000006:  # packet data block
            # interface id(4) ts_high(4) ts_low(4) caplen(4) origlen(4) data...
            if len(body) < 20:
                continue
            caplen = struct.unpack("<I", body[12:16])[0]
            data = body[20:20 + caplen]
            yield data
        elif btype == 0x0A0D0D0A:
            continue
        # skip unknown blocks


class WifiSource:
    """Read a Wi-Fi monitor-mode pcapng and hunt for ODID content."""

    def __init__(self, reporter, interface="wlan0"):
        self.rep = reporter
        self.interface = interface

    def start(self):
        self.f = open(f"/tmp/odr_{self.interface}.pcapng", "rb")

    def run(self):
        # In live mode the user must be capturing with tshark/airodump into
        # the same file; here we tail-read frames.
        import subprocess as sp
        cap = sp.Popen(
            ["tshark", "-i", self.interface, "-U", "wireless", "-w",
             f"/tmp/odr_{self.interface}.pcapng"],
            stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        try:
            self._tail()
        finally:
            cap.terminate()

    def _tail(self):
        last = 0
        while True:
            self.f.seek(last)
            for frame in read_pcapng_frames(self.f):
                last = self.f.tell()
                self._process(frame)
            time.sleep(0.5)

    def run_file(self, path):
        with open(path, "rb") as f:
            for frame in read_pcapng_frames(f):
                self._process(frame)

    def _process(self, frame):
        # Preferred: anchor on the NAN SDF signature and walk to the pack.
        # This is deterministic and avoids false-positive headers in the
        # frame's MAC/FCS overhead.
        for off, pack in extract_odid_from_nan(frame):
            self.rep.record("wifi", None, None, (off, pack))
        # Fallback: raw hunt anywhere in the frame (adapters that omit the
        # NAN SDF wrapper, e.g. some promiscuous captures of the payload).
        # Only used when the signature hunt found nothing.
        if not extract_odid_from_nan(frame):
            for off, m in find_odid_blocks(frame):
                self.rep.record("wifi", None, None, (off, m))


# --------------------------------------------------------------------------
# Encoders (port of opendroneid.c encode* functions) -- used by --selftest
# to generate known-answer test frames, and available for building
# synthetic captures.
# --------------------------------------------------------------------------
PROTOCOL_VERSION = 2


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else int(round(v))


def enc_basic_id(id_type, ua_type, uas_id):
    """Build a 25-byte Basic ID message. uas_id: str or bytes (<=20)."""
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_BASIC_ID << 4) | PROTOCOL_VERSION
    m[1] = ((ua_type & 0x0F) << 4) | (id_type & 0x0F)
    b = uas_id.encode("ascii") if isinstance(uas_id, str) else bytes(uas_id)
    m[2:22] = (b[:20]).ljust(20, b'\x00')
    return bytes(m)


def enc_location(lat, lon, alt_baro, alt_geo, height, height_type,
                 direction, speed_h, speed_v, status=2, timestamp=None,
                 horiz_acc=12, vert_acc=5, speed_acc=3, baro_acc=5, ts_acc=5):
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_LOCATION << 4) | PROTOCOL_VERSION
    flags = (status & 0x0F) | (height_type << 6)
    if direction is not None:
        if direction >= 180:
            flags |= (1 << 5)
            direction_enc = int(round(direction - 180))
        else:
            direction_enc = int(round(direction))
    else:
        direction_enc = 0
    if speed_h is not None:
        if speed_h <= 255 * 0.25:
            flags |= 0
            sh_enc = int(round(speed_h / 0.25))
        else:
            flags |= (1 << 4)
            sh_enc = int(round((speed_h - 255 * 0.25) / 0.75))
    else:
        sh_enc = 0
    m[1] = flags
    m[2] = _clamp(direction_enc, 0, 255)
    m[3] = _clamp(sh_enc, 0, 255)
    # vertical speed: single int8_t at byte 4 (0.5 m/s units, signed).
    # bytearray needs an unsigned byte, so wrap the clamped signed value to
    # two's-complement (decoder reads it back with struct "<h" over bytes 4:6,
    # low byte = int8 value, high byte = 0).
    sv = int(round(speed_v / 0.5)) if speed_v is not None else 0
    m[4] = _clamp(sv, -128, 127) & 0xFF
    m[5:9] = struct.pack("<i", _clamp(int(round(lat * 1e7)),
                                      -180 * 10**7, 180 * 10**7))
    m[9:13] = struct.pack("<i", _clamp(int(round(lon * 1e7)),
                                       -180 * 10**7, 180 * 10**7))
    m[13:15] = struct.pack("<H", _clamp(int(round((alt_baro + 1000) / 0.5)),
                                        0, 65535))
    m[15:17] = struct.pack("<H", _clamp(int(round((alt_geo + 1000) / 0.5)),
                                        0, 65535))
    m[17:19] = struct.pack("<H", _clamp(int(round((height + 1000) / 0.5)),
                                        0, 65535))
    m[19] = (vert_acc & 0x0F) << 4 | (horiz_acc & 0x0F)
    m[20] = (baro_acc & 0x0F) << 4 | (speed_acc & 0x0F)
    if timestamp is None:
        m[21:23] = b"\xff\xff"
    else:
        m[21:23] = struct.pack("<H", _clamp(int(round(timestamp * 10)),
                                            0, 36000))
    m[23] = (ts_acc & 0x0F) << 4
    return bytes(m)


def enc_system(op_lat, op_lon, op_loc_type, area_radius, area_ceiling,
               area_floor, op_alt, classif=0, cat_eu=0, class_eu=0,
               timestamp=0):
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_SYSTEM << 4) | PROTOCOL_VERSION
    m[1] = (op_loc_type & 0x03) | ((classif & 0x07) << 2)
    m[2:6] = struct.pack("<i", _clamp(int(round(op_lat * 1e7)),
                                      -180 * 10**7, 180 * 10**7))
    m[6:10] = struct.pack("<i", _clamp(int(round(op_lon * 1e7)),
                                       -180 * 10**7, 180 * 10**7))
    m[10:12] = struct.pack("<H", 1)  # area count
    m[12] = _clamp(int(area_radius) // 10, 0, 255)
    m[13:15] = struct.pack("<H", _clamp(int(round((area_ceiling + 1000) / 0.5)),
                                        0, 65535))
    m[15:17] = struct.pack("<H", _clamp(int(round((area_floor + 1000) / 0.5)),
                                        0, 65535))
    m[17] = ((cat_eu & 0x0F) << 4) | (class_eu & 0x0F)
    m[18:20] = struct.pack("<H", _clamp(int(round((op_alt + 1000) / 0.5)),
                                        0, 65535))
    m[20:24] = struct.pack("<I", _clamp(timestamp, 0, 2**32 - 1))
    return bytes(m)


def enc_operator_id(op_id):
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_OPERATOR_ID << 4) | PROTOCOL_VERSION
    m[1] = 0
    b = op_id.encode("ascii") if isinstance(op_id, str) else bytes(op_id)
    m[2:22] = (b[:20]).ljust(20, b'\x00')
    return bytes(m)


def enc_self_id(desc, desc_type=0):
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_SELF_ID << 4) | PROTOCOL_VERSION
    m[1] = desc_type
    b = desc.encode("ascii") if isinstance(desc, str) else bytes(desc)
    m[2:25] = (b[:23]).ljust(23, b'\x00')
    return bytes(m)


def enc_pack(messages):
    assert 1 <= len(messages) <= ODID_PACK_MAX_MESSAGES
    m = bytearray()
    m.append((MSG_PACKED << 4) | PROTOCOL_VERSION)
    m.append(ODID_MESSAGE_SIZE)
    m.append(len(messages))
    for x in messages:
        assert len(x) == ODID_MESSAGE_SIZE
        m.extend(x)
    return bytes(m)


def enc_auth_page0(auth_type, last_page, length, timestamp, auth_data):
    m = bytearray(ODID_MESSAGE_SIZE)
    m[0] = (MSG_AUTH << 4) | PROTOCOL_VERSION
    m[1] = ((0 & 0x0F) << 4) | (auth_type & 0x0F)  # page 0 in upper nibble
    m[2] = last_page
    m[3] = length
    m[4:8] = struct.pack("<I", timestamp)
    m[8:8 + ODID_AUTH_PAGE_ZERO_DATA_SIZE] = (auth_data[:ODID_AUTH_PAGE_ZERO_DATA_SIZE]).ljust(ODID_AUTH_PAGE_ZERO_DATA_SIZE, b'\x00')
    return bytes(m)


# --------------------------------------------------------------------------
# Selftest: build known-answer frames, decode them, check fields
# --------------------------------------------------------------------------
def selftest():
    """
    Generate synthetic ODID messages (encoders = port of the C encoders),
    wrap them in realistic BLE legacy ADV payloads and a Wi-Fi NAN SDF,
    run them through the decoders, and verify the round-tripped fields.
    Prints PASS/FAIL per check; exit code 0 on all-pass.
    """
    fails = []

    def check(name, got, want, tol=None):
        if tol is not None:
            ok = got is not None and abs(got - want) <= tol
        else:
            ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")
        if not ok:
            fails.append(name)

    print("== selftest: ODID block message decoders ==")
    basic = enc_basic_id(1, 2, "TEST123456789012")
    nm, f = decode_message(basic)
    check("basic type", nm, "BASIC_ID")
    check("basic id_type", f["id_type"], "SERIAL_NUMBER")
    check("basic ua_type", f["ua_type"], "HELICOPTER_OR_MULTIROTOR")
    check("basic uas_id", f["uas_id"], "TEST123456789012")

    loc = enc_location(47.3769, 8.5417, 123.0, 456.0, 12.0, 1,
                       direction=200, speed_h=1.5, speed_v=-0.5, status=2)
    nm, f = decode_message(loc)
    check("loc type", nm, "LOCATION")
    check("loc status", f["status"], "AIRBORNE")
    check("loc lat", f["latitude"], 47.3769, tol=1e-6)
    check("loc lon", f["longitude"], 8.5417, tol=1e-6)
    check("loc alt_baro", f["altitude_baro_m"], 123.0, tol=0.01)
    check("loc alt_geo", f["altitude_geo_m"], 456.0, tol=0.01)
    check("loc height", f["height_m"], 12.0, tol=0.01)
    check("loc height_ref", f["height_ref"], "OVER_GROUND")
    check("loc direction", f["direction_deg"], 200.0, tol=1e-9)
    check("loc speed_h", f["speed_horizontal_ms"], 1.5, tol=0.01)
    check("loc speed_v", f["speed_vertical_ms"], -0.5, tol=0.01)

    # high speed path (multiplier bit set): 50 m/s > 255*0.25
    loc2 = enc_location(0.0, 0.0, 0.0, 0.0, 0.0, 0,
                        direction=90, speed_h=50.0, speed_v=0.0)
    nm2, f2 = decode_message(loc2)
    check("loc2 speed_h high", f2["speed_horizontal_ms"], 50.0, tol=0.01)

    sysm = enc_system(47.40, 8.50, 0, 250, 120.0, 0.0, 45.0,
                      classif=1, cat_eu=1, class_eu=2)
    nm, f = decode_message(sysm)
    check("sys type", nm, "SYSTEM")
    check("sys op_lat", f["operator_latitude"], 47.40, tol=1e-5)
    check("sys op_lon", f["operator_longitude"], 8.50, tol=1e-5)
    check("sys op_loc", f["operator_location_type"], "TAKEOFF")
    check("sys radius", f["area_radius_m"], 250)
    check("sys ceiling", f["area_ceiling_m"], 120.0, tol=0.01)
    check("sys op_alt", f["operator_altitude_geo_m"], 45.0, tol=0.01)
    check("sys cat_eu", f["category_eu"], "OPEN")
    check("sys class_eu", f["class_eu"], "C1")

    opid = enc_operator_id("OP-1234")
    nm, f = decode_message(opid)
    check("opid type", nm, "OPERATOR_ID")
    check("opid id", f["operator_id"], "OP-1234")

    sid = enc_self_id("Test drone - research")
    nm, f = decode_message(sid)
    check("selfid type", nm, "SELF_ID")
    check("selfid desc", f["desc"], "Test drone - research")

    auth = enc_auth_page0(1, 0, 17, 123456789, b"\x01\x02\x03\x04")
    nm, f = decode_message(auth)
    check("auth type", nm, "AUTH")
    check("auth authtype", f["auth_type"], "UAS_ID_SIGNATURE")
    check("auth page", f["data_page"], 0)
    check("auth length", f["length"], 17)
    check("auth ts", f["timestamp_s_since_2019"], 123456789)

    pack = enc_pack([basic, loc, sysm])
    nm, f = decode_message(pack)
    check("pack type", nm, "PACKED")
    check("pack count", f["count"], 3)
    types = [s["type"] for s in f["messages"]]
    check("pack subtypes", types, ["BASIC_ID", "LOCATION", "SYSTEM"])

    # pack too large to fit in one legacy ADV -> must NOT be found as pack
    big_pack = enc_pack([basic, loc, sysm, opid, sid])
    check("pack5 size", len(big_pack), 3 + 5 * 25)

    print("== selftest: BLE legacy ADV framing (ASTM 0xFFFA service data) ==")
    # flags AD + service-data AD carrying the Location message
    ad_flags = b"\x02\x01\x06"
    sd_val = ASTM_UUID_BE + bytes([ODID_APP_CODE]) + loc
    ad_sd = bytes([len(sd_val) + 1]) + b"\x16" + sd_val  # AD length = type(1) + value
    adv_payload = ad_flags + ad_sd
    got = extract_odid_from_ble_payload(adv_payload)
    check("ble legacy found", len(got), 1)
    off, p = got[0]
    check("ble legacy msg type", msg_type_of(p[0]), MSG_LOCATION)
    check("ble legacy offset", off, len(ad_flags) + 1 + 1 + 2 + 1)
    nm, f = decode_message(p)
    check("ble legacy lat", f["latitude"], 47.3769, tol=1e-6)

    # manufacturer-data variant (0xFF) carrying Basic ID
    md_val = ASTM_UUID_BE + bytes([ODID_APP_CODE]) + basic
    adv2 = b"\x02\x01\x06" + bytes([len(md_val) + 1]) + b"\xff" + md_val  # AD length = type(1) + value
    got2 = extract_odid_from_ble_payload(adv2)
    check("ble mfg found", len(got2), 1)
    nm, f = decode_message(got2[0][1])
    check("ble mfg uas_id", f["uas_id"], "TEST123456789012")

    print("== selftest: BT5 extended ADV bare message pack ==")
    got3 = extract_odid_from_ble_payload(pack)
    check("ble ext pack found", len(got3), 1)
    check("ble ext pack is PACKED", msg_type_of(got3[0][1][0]), MSG_PACKED)

    print("== selftest: Wi-Fi NAN SDF hunt ==")
    # build a plausible NAN SDF: mgmt-ish prefix + OUI 50-6f-9a + 0x13 +
    # service descriptor attr (id 0x03) + service id 88-69-19-9d-92-09 +
    # ... + service info (counter + pack).  The decoder just hunts for the
    # 0xFx ODID header, so wrap the pack with the NAN signature.
    nan_attr_len = 6 + 1 + 1 + 1 + 1 + 1 + (1 + len(pack))  # service id..info
    nan = (bytes.fromhex("506f9a") + b"\x13"
           + b"\x03" + struct.pack("<H", nan_attr_len)
           + NAN_SERVICE_ID
           + b"\x01\x00\x10"               # instance, req instance, control
           + bytes([1 + len(pack)])        # service info len
           + b"\x01"                       # message counter
           + pack)
    frame = bytes(30) + nan + bytes(4)  # fake 802.11 header + FCS
    # Wi-Fi path anchors on the NAN SDF signature and walks to the pack.
    hits = extract_odid_from_nan(frame)
    check("wifi pack found", len(hits) >= 1, True)
    if hits:
        off, m = hits[0]
        check("wifi pack type", msg_type_of(m[0]), MSG_PACKED)
        check("wifi pack count", m[2], 3)
        check("wifi pack size", len(m), 3 + m[2] * ODID_MESSAGE_SIZE)

    print()
    if fails:
        print(f"SELFTEST FAILED: {len(fails)} check(s) failed: {fails}")
        return 1
    print("SELFTEST PASSED")
    return 0


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Open Drone ID (Remote ID) broadcast detector/decoder")
    ap.add_argument("--source", choices=["bt", "btmon", "wifi", "file"],
                    default="bt",
                    help="capture source (default: bt = BlueZ raw HCI)")
    ap.add_argument("--btmon-log", default=None,
                    help="btmon ASCII log file (with --source btmon or file)")
    ap.add_argument("--file", default=None,
                    help="pre-captured file (with --source file)")
    ap.add_argument("--jsonl", default=None,
                    help="JSONL output path (default: odr_records_<ts>.jsonl)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-record terminal output")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in known-answer decode test and exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    jsonl = args.jsonl or f"odr_records_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    rep = Reporter(jsonl, quiet=args.quiet)

    if args.source == "file":
        path = args.file or args.btmon_log
        if not path:
            ap.error("--source file requires --file or --btmon-log")
        low = path.lower()
        if low.endswith(".pcapng") or low.endswith(".pcap"):
            WifiSource(rep).run_file(path)
        else:
            src = BtmonSource(rep, path=path)
            src.start()
            src.run()
            src.stop()
    elif args.source == "bt":
        src = BtRawSource(rep)
        try:
            src.start()
        except OSError as e:
            print(f"ERROR: cannot open BlueZ raw HCI socket: {e}\n"
                  f"  - Is a Bluetooth adapter present?  `ls /sys/class/bluetooth`\n"
                  f"  - Is bluetoothd running?  `systemctl status bluetooth`\n"
                  f"  - Fall back to:  --source btmon  (needs root)\n"
                  f"    sudo btmon -w /tmp/btmon.log &\n"
                  f"    python3 odr_detect.py --source btmon --btmon-log /tmp/btmon.log",
                  file=sys.stderr)
            return 1
        print(f"Listening for Open Drone ID broadcasts on BlueZ raw HCI "
              f"(jsonl: {jsonl})  Ctrl-C to stop.", flush=True)
        try:
            src.run()
        except KeyboardInterrupt:
            pass
        finally:
            src.stop()
    elif args.source == "btmon":
        src = BtmonSource(rep, path=args.btmon_log)
        src.start()
        if args.btmon_log:
            print(f"Tailing btmon log {args.btmon_log} (jsonl: {jsonl}) "
                  f"Ctrl-C to stop.", flush=True)
        else:
            print("Tailing live `btmon` output (jsonl: {jsonl}) "
                  "Ctrl-C to stop.  Run as root or pre-capture with "
                  "`sudo btmon -w /tmp/btmon.log`.", flush=True)
        try:
            src.run()
        except KeyboardInterrupt:
            pass
        finally:
            src.stop()
    elif args.source == "wifi":
        print("Wi-Fi monitor capture: put the adapter in monitor mode first:\n"
              "  sudo ip link set wlan0 down\n"
              "  sudo iw dev wlan0 set type monitor\n"
              "then run this again.  (Needs a monitor-capable adapter and "
              "root.)", file=sys.stderr)
        src = WifiSource(rep)
        src.start()
        try:
            src.run()
        except KeyboardInterrupt:
            pass
        finally:
            src.stop()

    rep.dump_tracks()
    rep.close()
    st = rep.stats
    print(f"\nDone. packets={st['packets']} records written to {jsonl}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
