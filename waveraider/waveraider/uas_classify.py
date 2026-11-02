"""UAS Remote ID classifier — Open Drone ID / ASTM F3411.

Remote ID is broadcast in the clear over Wi-Fi and Bluetooth, so it rides on the
same 802.11 beacons and BLE advertisements Wave Raider already captures — no SDR.
Transport framing follows the Open Drone ID reference implementations
(opendroneid-core-c, receiver-android), which put a 1-byte message counter in
front of the payload:

  Wi-Fi beacon vendor IE    FA 0B BC | 0D | counter | message pack (or message)
  BLE service data 0xFFFA   FA FF    | 0D | counter | message (BT4) or pack

Decoded fields are what the transmitter *reports*: nothing here authenticates an
identity or a position, and the spec's "unknown" encodings are dropped instead of
being shown as real values. A report needs at least one decoded Basic ID,
Location, System or Operator ID message inside a complete envelope; anything else
is counted in `survey.diagnostics` and never creates or refreshes a report.
Entities are keyed by transport and transmitter address — one transmitter on one
transport, not necessarily one aircraft.

Not decoded yet: Wi-Fi NAN action frames, Bluetooth 5 Long Range captures, and
the contents of Authentication and Self-ID messages.
"""
from __future__ import annotations

import struct

from scapy.layers.dot11 import Dot11, Dot11Elt  # type: ignore
from scapy.layers.bluetooth4LE import BTLE_ADV_IND, BTLE_ADV_NONCONN_IND  # type: ignore

from .capture import iter_frames
from .model import Entity, Finding, Survey

ODID_WIFI_OUI = b"\xfa\x0b\xbc"   # ASTM F3411 vendor OUI in Wi-Fi beacon IEs
ODID_APP_CODE = 0x0D              # app code after OUI (Wi-Fi) / UUID (BLE)
ODID_BLE_UUID = b"\xfa\xff"       # service UUID 0xFFFA, little-endian

MESSAGE_SIZE = 25
PACK_MAX_MESSAGES = 9
# 0: ASTM F3411-19, 1: ASD-STAN prEN 4709-002 P1, 2: ASTM F3411-22. Any other
# version is counted as unsupported rather than guessed at, which also keeps a
# misframed payload from decoding into plausible-looking values. Unsupported
# data is not evidence that no drone is present.
SUPPORTED_VERSIONS = (0, 1, 2)
# Provisional default for "not heard recently", not an operational alert policy.
STALE_AFTER_S = 30.0

BASIC_ID, LOCATION, AUTH, SELF_ID, SYSTEM, OPERATOR_ID, PACK = 0, 1, 2, 3, 4, 5, 0xF
# Most messages of each type one pack may hold (opendroneid-core-c checkPackContent).
_PACK_LIMITS = {BASIC_ID: 2, LOCATION: 1, AUTH: 16, SELF_ID: 1, SYSTEM: 1, OPERATOR_ID: 1}
# Message types whose contents are decoded; only these can create or refresh a report.
DECODED_TYPES = ("basic_id", "location", "system", "operator_id")

UA_TYPES = {
    0: "Not declared", 1: "Aeroplane", 2: "Multirotor", 3: "Gyroplane", 4: "Hybrid Lift",
    5: "Ornithopter", 6: "Glider", 7: "Kite", 8: "Free Balloon", 9: "Captive Balloon",
    10: "Airship", 11: "Parachute", 12: "Rocket", 13: "Tethered", 14: "Ground Obstacle",
    15: "Other",
}
ID_TYPES = {0: "None", 1: "Serial (CTA-2063-A)", 2: "CAA Registration",
            3: "UTM (UUID)", 4: "Session ID"}
TEXT_ID_TYPES = (1, 2)            # ASCII; UTM UUIDs and session IDs are binary
OPERATOR_LOCATION_TYPES = {0: "takeoff", 1: "live GNSS", 2: "fixed"}
TRANSPORTS = {"wifi_beacon": "Wi-Fi beacon", "ble": "Bluetooth advertisement"}


def split_payload(data: bytes) -> tuple[list, str | None]:
    """Split a Remote ID payload into 25-byte messages -> (messages, problem).
    A pack that breaks the reference pack rules is rejected whole."""
    if not data:
        return [], "empty payload"
    if data[0] >> 4 != PACK:
        if len(data) < MESSAGE_SIZE:
            return [], "truncated message"
        return [data[:MESSAGE_SIZE]], None
    if data[0] & 0x0F not in SUPPORTED_VERSIONS:
        return [], "unsupported protocol version"
    if len(data) < 3 or data[1] != MESSAGE_SIZE or not 1 <= data[2] <= PACK_MAX_MESSAGES:
        return [], "malformed message pack"
    end = 3 + data[2] * MESSAGE_SIZE
    if len(data) < end:
        return [], "truncated message pack"
    messages = [data[i:i + MESSAGE_SIZE] for i in range(3, end, MESSAGE_SIZE)]
    counts: dict = {}
    for m in messages:
        t = m[0] >> 4
        counts[t] = counts.get(t, 0) + 1
        if counts[t] > _PACK_LIMITS.get(t, 0):
            return [], "invalid message pack content"
    return messages, None


def _text(raw: bytes) -> str | None:
    """An identifier exactly as sent: printable ASCII padded with NULs. None when
    empty, binary, or not NUL-padded — never trimmed or repaired into another string."""
    text, _, padding = raw.partition(b"\x00")
    if not text or padding.strip(b"\x00") or any(c < 0x20 or c > 0x7E for c in text):
        return None
    return text.decode("ascii")


def _position(lat_raw: int, lon_raw: int):
    """-> (position, invalid). Both-zero is the spec's "unknown", not invalid;
    out-of-range coordinates are invalid. Either way there is no position."""
    if lat_raw == 0 and lon_raw == 0:
        return None, False
    lat, lon = lat_raw / 1e7, lon_raw / 1e7
    if abs(lat) > 90 or abs(lon) > 180:
        return None, True
    return (lat, lon), False


def parse_message(m: bytes) -> dict | None:
    """Decode one 25-byte message. None for a short buffer, an unsupported
    protocol version, or a message type this decoder does not know. Fields that
    break their encoding rules are listed under "invalid"."""
    if len(m) < MESSAGE_SIZE or m[0] & 0x0F not in SUPPORTED_VERSIONS:
        return None
    t, version = m[0] >> 4, m[0] & 0x0F
    if t == BASIC_ID:
        id_type, raw = m[1] >> 4, m[2:22]
        return {"type": "basic_id", "version": version, "id_type": id_type,
                "ua_type": m[1] & 0x0F, "uas_id_raw": raw.hex(),
                "uas_id": _text(raw) if id_type in TEXT_ID_TYPES else None}
    if t == LOCATION:
        lat, lon = struct.unpack_from("<ii", m, 5)
        alt, = struct.unpack_from("<H", m, 15)      # geodetic, 0.5 m steps from -1000 m
        ts, = struct.unpack_from("<H", m, 21)       # tenths of a second after the hour
        position, bad_position = _position(lat, lon)
        bad_time = ts != 0xFFFF and ts > 36000
        return {"type": "location", "version": version, "position": position,
                "alt": None if alt == 0 else alt * 0.5 - 1000,
                "claimed_time_s": None if ts > 36000 else ts / 10,
                "invalid": [f for f, bad in (("drone position", bad_position),
                                             ("location timestamp", bad_time)) if bad]}
    if t == SYSTEM:
        lat, lon = struct.unpack_from("<ii", m, 2)
        position, bad_position = _position(lat, lon)
        return {"type": "system", "version": version, "op_location_type": m[1] & 0x03,
                "op_position": position, "invalid": ["operator position"] if bad_position else []}
    if t == OPERATOR_ID:
        raw = m[2:22]
        return {"type": "operator_id", "version": version, "operator_id_raw": raw.hex(),
                "operator_id": _text(raw)}
    if t in (AUTH, SELF_ID):                         # recognized; contents not decoded yet
        return {"type": "auth" if t == AUTH else "self_id", "version": version}
    return None


def _wifi_odid(pkt) -> tuple[bytes | None, str | None]:
    """(payload, problem) from the first Remote ID vendor IE; (None, None) if none."""
    e = pkt.getlayer(Dot11Elt)
    while isinstance(e, Dot11Elt):
        if e.ID == 221:
            # Read the element's own bytes (plus any that follow): scapy may dissect
            # it as Dot11EltVendorSpecific, which splits the OUI out of .info.
            raw = bytes(e)
            body = raw[2:2 + raw[1]] if len(raw) > 1 else b""
            if body[:3] == ODID_WIFI_OUI and (len(body) < 4 or body[3] == ODID_APP_CODE):
                if len(raw) < 2 + raw[1] or len(body) < 5:
                    return None, "truncated Remote ID envelope"
                return body[5:], None                # skip the 1-byte message counter
        e = e.payload.getlayer(Dot11Elt)
    return None, None


def _ble_adv(pkt):
    for cls in (BTLE_ADV_IND, BTLE_ADV_NONCONN_IND):
        if pkt.haslayer(cls):
            return pkt.getlayer(cls)
    return None


def _ble_odid(adv) -> tuple[bytes | None, str | None]:
    """(payload, problem) from the Remote ID service data AD; (None, None) if none."""
    raw = b"".join(bytes(x) for x in (adv.data or []))
    i = 0
    while i < len(raw):
        length = raw[i]
        if length == 0:
            break
        ad_type = raw[i + 1] if i + 1 < len(raw) else 0
        val = raw[i + 2:i + 1 + length]
        if ad_type == 0x16 and val[:2] == ODID_BLE_UUID and (len(val) < 3 or val[2] == ODID_APP_CODE):
            if i + 1 + length > len(raw) or len(val) < 4:
                return None, "truncated Remote ID envelope"
            return val[4:], None                     # skip the 1-byte message counter
        i += 1 + length
    return None, None


def _put(attrs: dict, key: str, value) -> None:
    """Set a reported value, or drop it when the newest message says unknown."""
    if value is None:
        attrs.pop(key, None)
    else:
        attrs[key] = value


def is_stale(entity: Entity, now: float, after_s: float = STALE_AFTER_S) -> bool:
    """True when the transmitter has not been received for `after_s` seconds."""
    return now - entity.attrs.get("last_seen", now) > after_s


class UasClassifier:
    def __init__(self, path: str = "<live>", gap_after=None) -> None:
        """`gap_after`: a callable giving, in seconds, the pause between two updates of one transmitter
        beyond which a gap is recorded. It is read when the later update is processed; earlier counts are
        never recalculated. Monitor passes its stale threshold and an elapsed-time stamp with each update
        (see `update`); recorded-file analysis passes nothing, because a capture's packet times say nothing
        about when we were listening. A gap is a fact about updates arriving here, not about an aircraft."""
        self.survey = Survey(path=path)
        self.gap_after = gap_after

    def _diagnose(self, problem: str) -> None:
        d = self.survey.diagnostics
        d[problem] = d.get(problem, 0) + 1

    def update(self, pkt, elapsed: float | None = None) -> None:
        """`elapsed`: a monotonic time stamp for this update, used only to measure gaps between updates.
        Wall-clock `pkt.time` still supplies first/last seen; it can be set backwards or forwards by the
        system, so it is never used to infer a gap."""
        self.survey.total_frames += 1
        if pkt.haslayer(Dot11):
            (payload, problem), transport = _wifi_odid(pkt), "wifi_beacon"
            addr = pkt[Dot11].addr2
        else:
            adv = _ble_adv(pkt)
            if adv is None:
                return
            (payload, problem), transport = _ble_odid(adv), "ble"
            addr = getattr(adv, "AdvA", "")
        if payload is None and problem is None:
            return                                   # no Remote ID in this frame

        messages, problem = ([], problem) if problem else split_payload(payload)
        infos = [info for info in map(parse_message, messages) if info]
        decoded = [info for info in infos if info["type"] in DECODED_TYPES]
        if not problem and not addr:
            problem = "no transmitter address"
        if not problem and not decoded:
            problem = "authentication/self-ID not decoded" if infos else "no supported Remote ID message"
        if problem:
            self._diagnose(problem)
            return
        if len(infos) < len(messages):
            self._diagnose("unsupported message skipped")

        addr = addr.lower()
        key = f"{transport}:{addr}"
        now = round(float(getattr(pkt, "time", 0) or 0), 6)
        e = self.survey.entities.get(key) or Entity(id=key, kind="uas")
        e.observations += 1
        a = e.attrs
        a.setdefault("first_seen", now)
        if self.gap_after is not None and elapsed is not None:
            previous = a.get("last_elapsed")
            if previous is not None and elapsed - previous > self.gap_after():
                a["update_gaps"] = a.get("update_gaps", 0) + 1
                a["last_gap"] = {"seconds": elapsed - previous, "at": now}
                a["sequence_started"] = now             # a new sequence of updates starts after a recorded gap
            a.setdefault("sequence_started", now)
            a["last_elapsed"] = elapsed
        a["last_seen"], a["addr"], a["transport"] = now, addr, transport
        for info in decoded:
            a["protocol_version"] = info["version"]
            for field in info.get("invalid", ()):
                self._diagnose(f"invalid {field}")
            kind = info["type"]
            if kind == "basic_id":
                ids = a.setdefault("basic_ids", {})
                ids[info["id_type"]] = {"text": info["uas_id"], "raw": info["uas_id_raw"]}
                a["ua_type"] = info["ua_type"]
                _put(a, "uas_id", next((ids[t]["text"] for t in TEXT_ID_TYPES
                                        if t in ids and ids[t]["text"]), None))
            elif kind == "location":
                pos = info["position"]
                _put(a, "lat", pos and pos[0])
                _put(a, "lon", pos and pos[1])
                _put(a, "alt", info["alt"])
                _put(a, "claimed_time_s", info["claimed_time_s"])
            elif kind == "system":
                pos = info["op_position"]
                a["op_location_type"] = info["op_location_type"]
                _put(a, "op_lat", pos and pos[0])
                _put(a, "op_lon", pos and pos[1])
            elif kind == "operator_id":
                a["operator_id_raw"] = info["operator_id_raw"]
                _put(a, "operator_id", info["operator_id"])
        e.label = a.get("uas_id", "")
        self.survey.entities[key] = e

    def rebuild_findings(self) -> None:
        out = []
        for e in self.survey.of_kind("uas"):
            a = e.attrs
            uid = a.get("uas_id")
            ua = UA_TYPES.get(a.get("ua_type"), "type not reported")
            source = f"{a.get('addr', e.id)} ({TRANSPORTS.get(a.get('transport'), 'unknown transport')})"
            out.append(Finding("uas_remote_id", "info",
                        f"Remote ID broadcast: {uid or 'identity not reported'} ({ua}) from {source}",
                        {"entity": e.id, "uas_id": uid or ""}))
            if "op_lat" in a:
                where = OPERATOR_LOCATION_TYPES.get(a.get("op_location_type"), "undeclared type")
                out.append(Finding("operator_location", "warning",
                            f"Operator location reported ({where}): {a['op_lat']}, {a['op_lon']}",
                            {"entity": e.id, "op_lat": a["op_lat"], "op_lon": a["op_lon"],
                             "op_location_type": where}))
        self.survey.findings = out


def survey(path) -> Survey:
    clf = UasClassifier(path=str(path))
    for pkt in iter_frames(path):
        clf.update(pkt)
    clf.rebuild_findings()
    return clf.survey
