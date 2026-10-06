"""UAS Remote ID protocol plugin (Open Drone ID / ASTM F3411)."""
from __future__ import annotations

import time

from ..model import Entity, Survey
from ..uas_classify import ID_TYPES, OPERATOR_LOCATION_TYPES, TRANSPORTS, UA_TYPES, UasClassifier
from .base import ProtocolPlugin, _SEV

NOT_REPORTED = "not reported"

UAS_KNOWLEDGE = {
    "uas_remote_id": {
        "what": "a decoded UAS Remote ID broadcast (ASTM F3411 / Open Drone ID)",
        "means": (
            "a transmitter is broadcasting Remote ID messages in the clear over "
            "Wi-Fi/Bluetooth. The identity and positions are what it reports — decoded, "
            "not authenticated — and one transmitter is not proof of one aircraft."
        ),
        "next": "record the reported UAS ID and follow the reported position over time.",
    },
    "operator_location": {
        "what": "an operator location reported in the Remote ID System message",
        "means": (
            "the broadcaster declares it as the takeoff point, a live GNSS position, or a "
            "fixed location, so it is not necessarily where the pilot is right now."
        ),
        "next": "check the declared location type, then correlate it with the drone's reported path.",
    },
}


def _loc(lat, lon):
    return f"{lat}, {lon}" if lat is not None else NOT_REPORTED


def _op_loc(a: dict) -> str:
    if a.get("op_lat") is None:
        return NOT_REPORTED
    where = OPERATOR_LOCATION_TYPES.get(a.get("op_location_type"), "undeclared type")
    return f"{a['op_lat']}, {a['op_lon']} ({where})"


def _transmitter(e: Entity) -> str:
    a = e.attrs
    return f"{a.get('addr', e.id)} ({TRANSPORTS.get(a.get('transport'), '-')})"


def _when(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


class UasPlugin(ProtocolPlugin):
    name = "uas"
    category = "UAS"
    label = "Remote ID"
    knowledge = UAS_KNOWLEDGE
    primary_kinds = ("uas",)
    sample = "uas.pcap"

    def new_classifier(self) -> UasClassifier:
        return UasClassifier()

    def columns(self) -> list[str]:
        return ["Transmitter", "UAS ID", "Type", "Drone location", "Operator location", "Alerts"]

    def row(self, e: Entity, alerts: int) -> list[str]:
        a = e.attrs
        return [
            _transmitter(e),
            a.get("uas_id") or NOT_REPORTED,
            UA_TYPES.get(a.get("ua_type"), NOT_REPORTED),
            _loc(a.get("lat"), a.get("lon")),
            _op_loc(a),
            str(alerts) if alerts else "-",
        ]

    def describe(self, e: Entity, survey: Survey) -> str:
        a = e.attrs
        uid = a.get("uas_id") or "identity not reported"
        parts = [f"Transmitter {_transmitter(e)} is broadcasting Remote ID ({uid}; "
                 f"{UA_TYPES.get(a.get('ua_type'), 'type not reported')})."]
        if a.get("lat") is not None:
            alt = f", alt {a['alt']} m" if a.get("alt") is not None else ""
            parts.append(f"Reported drone position {a['lat']}, {a['lon']}{alt}.")
        if a.get("op_lat") is not None:
            parts.append(f"Reported operator location {_op_loc(a)}.")
        parts.append("Values are as broadcast, not authenticated.")
        return " ".join(parts)

    def detail(self, survey: Survey, e: Entity) -> str:
        a = e.attrs
        alt = f"{a['alt']} m" if a.get("alt") is not None else NOT_REPORTED
        lines = [
            f"Transmitter  {_transmitter(e)}",
            f"UAS ID       {a.get('uas_id') or NOT_REPORTED}",
        ]
        for id_type, v in sorted(a.get("basic_ids", {}).items()):
            kind = ID_TYPES.get(id_type, f"reserved ({id_type})")
            lines.append(f"  Basic ID   {kind}: {v['text'] if v['text'] is not None else 'no usable text'}"
                         f"  raw {v['raw']}")
        lines += [
            f"UA type      {UA_TYPES.get(a.get('ua_type'), NOT_REPORTED)}",
            f"Drone loc    {_loc(a.get('lat'), a.get('lon'))}   alt {alt}",
            f"Operator loc {_op_loc(a)}",
            f"Operator ID  {a.get('operator_id') or NOT_REPORTED}",
            f"Heard        first {_when(a.get('first_seen'))} · last {_when(a.get('last_seen'))}",
            f"Version      {a.get('protocol_version', '-')}",
            f"Messages     {e.observations}",
        ]
        fs = [f for f in survey.findings if f.evidence.get("entity") == e.id]
        lines += ["", f"FINDINGS ({len(fs)})"]
        lines += [f"  {_SEV.get(f.severity, '[ ?]')} {f.summary}" for f in fs] or ["  none"]
        return "\n".join(lines)
