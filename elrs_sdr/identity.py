"""Deterministic, protocol-neutral identity tracking for passive observations.

The module is intentionally pure: callers provide JSON-compatible observation
records and receive a deterministic track set.  Measured frequency remains
evidence, but does not split a typed identity when a hopping link moves.  Raw
spectrum records remain separate and can never create or promote an identity.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


OBSERVATION_SCHEMA = "wireless-detection-observation/v1"
IDENTITY_SCHEMA = "wireless-detection-identity/v1"
TRACK_SET_SCHEMA = "wireless-detection-track-set/v1"
SPECTRUM_SCHEMA = "wireless-spectrum-evidence/v1"

MATURITY_RANK = {
    "presence": 0,
    "family": 1,
    "specific": 2,
    "identified": 3,
}
COMPATIBILITY_FIELDS = ("protocol_version", "phy", "model", "firmware")
HEX_IDENTITY_KINDS = {"uid", "mac", "bssid"}


class ValidationError(ValueError):
    """An input record cannot be used without guessing."""


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical JSON representation used by every identity hash."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_id(prefix: str, value: Any) -> str:
    return f"{prefix}{hashlib.sha256(canonical_bytes(value)).hexdigest()}"


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _normalized_label(value: Any, field: str) -> str:
    return _nonempty_text(value, field).casefold()


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValidationError(f"{field} must be a finite number")
    return result


def _integer_hz(value: Any, field: str) -> int:
    number = _finite_number(value, field)
    rounded = int(round(number))
    if rounded < 0 or abs(number - rounded) > 1e-6:
        raise ValidationError(f"{field} must be a non-negative integer number of hertz")
    return rounded


def _timestamp(value: Any) -> str:
    text = _nonempty_text(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValidationError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def normalize_identity(identity: Any) -> dict[str, str]:
    """Normalize a typed identity without changing its meaning."""
    if not isinstance(identity, dict):
        raise ValidationError("identity must be an object")
    kind = _normalized_label(identity.get("kind"), "identity.kind")
    value = _nonempty_text(identity.get("value"), "identity.value")
    if kind in HEX_IDENTITY_KINDS:
        normalized = "".join(ch for ch in value.casefold() if ch not in ":-.")
        if not normalized or len(normalized) % 2 or any(
            ch not in "0123456789abcdef" for ch in normalized
        ):
            raise ValidationError(f"identity.value must be even-length hex for {kind}")
        if kind in {"mac", "bssid"} and len(normalized) != 12:
            raise ValidationError(f"identity.value must contain 6 bytes for {kind}")
        value = normalized
    return {"kind": kind, "value": value}


def normalize_detection(record: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one identity-bearing passive observation."""
    protocol = _normalized_label(record.get("protocol"), "protocol")
    maturity = _normalized_label(record.get("evidence_maturity"), "evidence_maturity")
    if maturity not in MATURITY_RANK:
        raise ValidationError(
            "evidence_maturity must be one of " + ", ".join(MATURITY_RANK)
        )
    normalized: dict[str, Any] = {
        "schema": OBSERVATION_SCHEMA,
        "kind": "detection",
        "timestamp": _timestamp(record.get("timestamp")),
        "protocol": protocol,
        "frequency_hz": _integer_hz(record.get("frequency_hz"), "frequency_hz"),
        "evidence_maturity": maturity,
        "source": _nonempty_text(record.get("source"), "source"),
    }
    if "power_dbm" in record and record["power_dbm"] is not None:
        normalized["power_dbm"] = _finite_number(record["power_dbm"], "power_dbm")
    for field in COMPATIBILITY_FIELDS:
        if record.get(field) is not None:
            normalized[field] = _normalized_label(record[field], field)
    if record.get("identity") is not None:
        normalized["identity"] = normalize_identity(record["identity"])
    if record.get("unique_frequency_hz") is not None:
        normalized["unique_frequency_hz"] = _integer_hz(
            record["unique_frequency_hz"], "unique_frequency_hz"
        )
    if "identity" not in normalized and "unique_frequency_hz" not in normalized:
        raise ValidationError(
            "a detection needs a typed identity or explicit unique_frequency_hz"
        )
    return normalized


def normalize_spectrum(record: dict[str, Any]) -> dict[str, Any]:
    """Validate spectrum context while keeping it outside identity evidence."""
    powers = record.get("powers_dbm")
    if not isinstance(powers, list) or not powers:
        raise ValidationError("powers_dbm must be a non-empty array")
    normalized = {
        "schema": SPECTRUM_SCHEMA,
        "kind": "spectrum",
        "timestamp": _timestamp(record.get("timestamp")),
        "source": _nonempty_text(record.get("source"), "source"),
        "lower_frequency_hz": _integer_hz(
            record.get("lower_frequency_hz"), "lower_frequency_hz"
        ),
        "bin_resolution_hz": _integer_hz(
            record.get("bin_resolution_hz"), "bin_resolution_hz"
        ),
        "powers_dbm": [
            _finite_number(value, f"powers_dbm[{index}]")
            for index, value in enumerate(powers)
        ],
        "calibration": _normalized_label(record.get("calibration"), "calibration"),
    }
    if record.get("linked_entity_id") is not None:
        normalized["linked_entity_id"] = _nonempty_text(
            record["linked_entity_id"], "linked_entity_id"
        )
    normalized["evidence_id"] = _sha256_id("spec:v1:", normalized)
    return normalized


def stable_entity_id(detection: dict[str, Any]) -> str:
    """Hash only protocol and identity-bearing fields, never measured frequency."""
    key: dict[str, Any] = {
        "schema": IDENTITY_SCHEMA,
        "protocol": detection["protocol"],
    }
    if "identity" in detection:
        key["identity"] = detection["identity"]
    else:
        key["identity"] = {
            "kind": "unique_frequency",
            "value": detection["unique_frequency_hz"],
        }
    if "unique_frequency_hz" in detection and "identity" in detection:
        key["unique_frequency_hz"] = detection["unique_frequency_hz"]
    return _sha256_id("did:v1:", key)


def observation_id(detection: dict[str, Any]) -> str:
    return _sha256_id("obs:v1:", detection)


def detection_observation(
    detection: Any,
    *,
    timestamp: str,
    source: str,
    compatibility: dict[str, str | None] | None = None,
    unique_frequency_hz: int | None = None,
) -> dict[str, Any]:
    """Adapt an ``elrs_sdr.detect.Detection``-shaped object to the contract.

    An unidentified signal is rejected unless the caller explicitly states
    that frequency is identity-bearing via ``unique_frequency_hz``.
    """
    record: dict[str, Any] = {
        "kind": "detection",
        "timestamp": timestamp,
        "protocol": detection.protocol,
        "frequency_hz": detection.freq_hz,
        "evidence_maturity": detection.detection_level.label,
        "source": source,
    }
    net_id = getattr(detection, "net_id", None)
    if net_id is not None:
        record["identity"] = {
            "kind": net_id.kind,
            "value": net_id.net_id_str,
        }
    if unique_frequency_hz is not None:
        record["unique_frequency_hz"] = unique_frequency_hz
    for field, value in (compatibility or {}).items():
        if field not in COMPATIBILITY_FIELDS:
            raise ValidationError(f"unsupported compatibility field {field!r}")
        if value is not None:
            record[field] = value
    return normalize_detection(record)


def _new_track(entity_id: str, detection: dict[str, Any], obs_id: str) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "identity": detection.get("identity") or {
            "kind": "unique_frequency",
            "value": detection["unique_frequency_hz"],
        },
        "protocol": detection["protocol"],
        "compatibility": {
            field: detection.get(field) for field in COMPATIBILITY_FIELDS
        },
        "compatibility_conflicts": {},
        "first_seen": detection["timestamp"],
        "last_seen": detection["timestamp"],
        "observation_count": 1,
        "frequencies_hz": {detection["frequency_hz"]},
        "max_evidence_maturity": detection["evidence_maturity"],
        "evidence_counts": {detection["evidence_maturity"]: 1},
        "observation_ids": {obs_id},
    }


def _merge_track(track: dict[str, Any], detection: dict[str, Any], obs_id: str) -> None:
    track["first_seen"] = min(track["first_seen"], detection["timestamp"])
    track["last_seen"] = max(track["last_seen"], detection["timestamp"])
    track["observation_count"] += 1
    track["frequencies_hz"].add(detection["frequency_hz"])
    track["observation_ids"].add(obs_id)
    maturity = detection["evidence_maturity"]
    track["evidence_counts"][maturity] = track["evidence_counts"].get(maturity, 0) + 1
    if MATURITY_RANK[maturity] > MATURITY_RANK[track["max_evidence_maturity"]]:
        track["max_evidence_maturity"] = maturity
    for field in COMPATIBILITY_FIELDS:
        incoming = detection.get(field)
        current = track["compatibility"].get(field)
        if incoming is None:
            continue
        if current is None and field not in track["compatibility_conflicts"]:
            track["compatibility"][field] = incoming
        elif current is not None and current != incoming:
            values = track["compatibility_conflicts"].setdefault(field, set())
            values.update((current, incoming))
            track["compatibility"][field] = None
        elif field in track["compatibility_conflicts"]:
            track["compatibility_conflicts"][field].add(incoming)


def build_tracks(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic track set from detection and spectrum records."""
    tracks: dict[str, dict[str, Any]] = {}
    spectrum: list[dict[str, Any]] = []
    input_count = 0
    for index, record in enumerate(records, start=1):
        input_count += 1
        if not isinstance(record, dict):
            raise ValidationError(f"record {index} must be an object")
        kind = _normalized_label(record.get("kind", "detection"), f"record {index} kind")
        if kind == "spectrum":
            spectrum.append(normalize_spectrum(record))
            continue
        if kind != "detection":
            raise ValidationError(f"record {index} has unsupported kind {kind!r}")
        detection = normalize_detection(record)
        entity_id = stable_entity_id(detection)
        obs_id = observation_id(detection)
        if entity_id not in tracks:
            tracks[entity_id] = _new_track(entity_id, detection, obs_id)
        else:
            _merge_track(tracks[entity_id], detection, obs_id)

    serialized_tracks = []
    for entity_id in sorted(tracks):
        track = tracks[entity_id]
        track["frequencies_hz"] = sorted(track["frequencies_hz"])
        track["observation_ids"] = sorted(track["observation_ids"])
        track["evidence_counts"] = dict(sorted(track["evidence_counts"].items()))
        track["compatibility_conflicts"] = {
            field: sorted(values)
            for field, values in sorted(track["compatibility_conflicts"].items())
        }
        serialized_tracks.append(track)
    spectrum.sort(key=lambda item: item["evidence_id"])
    return {
        "schema": TRACK_SET_SCHEMA,
        "status": "ok",
        "input_record_count": input_count,
        "track_count": len(serialized_tracks),
        "spectrum_evidence_count": len(spectrum),
        "tracks": serialized_tracks,
        "spectrum_evidence": spectrum,
    }


def load_records(path: Path) -> list[dict[str, Any]]:
    """Load a JSON array, an observations object, or JSON Lines."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(str(exc)) from exc
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f"invalid JSON on line {line_number}: {exc.msg}"
                ) from exc
        return records
    if isinstance(decoded, list):
        return decoded
    if isinstance(decoded, dict) and isinstance(decoded.get("observations"), list):
        return decoded["observations"]
    raise ValidationError("JSON input must be an array or contain an observations array")
