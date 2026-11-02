"""Target watchlist — identifiers the operator is hunting, matched everywhere.

A target is a typed identifier. Settings adds UAS IDs, operator IDs and transmitter
addresses; entries of older types from v0.5.1 (BD_ADDR, SSID, device name,
IMSI/IMEI/TMSI, radio id, cell id, …) are kept and still matched. A target is compared
against an entity's *identifier* fields — its id, its label, and a fixed allowlist of
identity-bearing attributes — so an older type only matches where a Remote ID
observation carries that field. Non-identity attributes (channel, power, frequency…)
are deliberately excluded so a numeric target can't false-match on them.

When an entity matches, `apply_to_survey` raises a critical `target` finding, so
the hit shows up in the findings list and the analysis, not just as a badge.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .model import Finding

VALID_TYPES = [
    "mac", "bd_addr", "ssid", "name",
    "imsi", "imei", "tmsi", "radio_id", "cid", "uas_id", "operator_id", "other",
]

# Attributes that carry an entity's *identity* (safe to match a target against).
# Freeform/measurement attrs (arfcn, freq, pwr, channel, cod, services…) are NOT
# here, so a target value like "3" can't collide with arfcn=3.
IDENTIFIER_ATTRS = (
    "ssid", "name", "bssid", "mac", "addr",
    "source", "radio_id", "ssi", "gssi", "tgid", "talkgroup",
    "cid", "imsi", "imei", "tmsi", "uas_id", "operator_id",
)

# Target types compared with MAC normalization (separators/case ignored).
_MAC_TYPES = ("mac", "bd_addr")


def _targets_file() -> Path:
    return Path(
        os.environ.get("WAVERAIDER_TARGETS", str(Path.home() / ".waveraider" / "targets.json"))
    )


def _norm_mac(v: str) -> str:
    return v.lower().replace(":", "").replace("-", "").replace(".", "")


def load() -> list:
    try:
        return json.loads(_targets_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def save(items: list) -> None:
    f = _targets_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(items, indent=2), encoding="utf-8")


def add(ttype: str, value: str, note: str = "", exact: bool = False) -> list:
    """Add a target. Typed-in values are trimmed; `exact` keeps a decoded identifier
    as sent (surrounding spaces included) so it still matches where it came from."""
    ttype = ttype if ttype in VALID_TYPES else "other"
    if not exact:
        value = value.strip()
    items = load()
    if value.strip() and not any(i["value"] == value and i["type"] == ttype for i in items):
        items.append({"type": ttype, "value": value, "note": note})
        save(items)
    return items


def remove(value: str, ttype: str | None = None) -> list:
    """Remove every target with this value, or only the one of type `ttype`."""
    items = [i for i in load() if not (i["value"] == value and (ttype is None or i["type"] == ttype))]
    save(items)
    return items


def _identifier_sets(entity) -> tuple[set, set]:
    """(exact-match strings, MAC-normalized strings) for one entity."""
    exact = {entity.id.lower()}
    if entity.label:
        exact.add(entity.label.lower())
    for k in IDENTIFIER_ATTRS:
        if k in entity.attrs:
            exact.add(str(entity.attrs[k]).lower())

    macs = {_norm_mac(entity.id)}
    for k in ("bssid", "mac", "addr"):
        if k in entity.attrs:
            macs.add(_norm_mac(str(entity.attrs[k])))
    macs.discard("")
    return exact, macs


def match(entity, watch: list | None = None) -> list:
    """Return the target values this entity matches (across any protocol).

    Pass `watch` (a preloaded watchlist) to avoid re-reading the file per entity.
    """
    if watch is None:
        watch = load()
    if not watch:
        return []
    exact, macs = _identifier_sets(entity)
    hits = []
    for t in watch:
        tv = t.get("value", "")
        if not tv:
            continue
        if t.get("type") in _MAC_TYPES:
            if _norm_mac(tv) in macs:
                hits.append(tv)
        elif tv.lower() in exact:
            hits.append(tv)
    return hits


def apply_to_survey(survey) -> int:
    """Inject a critical `target` finding for every entity that matches the
    watchlist. Target findings lead the findings list. Returns the hit count."""
    watch = load()
    if not watch:
        return 0
    found = []
    for e in survey.entities.values():
        hits = match(e, watch=watch)
        if hits:
            found.append(Finding(
                "target", "critical",
                f"TARGET MATCH — {e.label or e.id} ({e.kind}) matches watchlist: "
                + ", ".join(hits),
                {"entity": e.id, "targets": ", ".join(hits)},
            ))
    survey.findings = found + survey.findings
    return len(found)
