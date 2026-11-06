"""Fail-closed compatibility binding for offline identification PoCs.

The module evaluates a versioned manifest against exactly one identity track
and emits a deterministic receipt.  It never imports, invokes, or transmits
the PoC named by the manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .identity import MATURITY_RANK, TRACK_SET_SCHEMA, ValidationError, canonical_bytes


MANIFEST_SCHEMA = "wireless-poc-compatibility/v1"
RECEIPT_SCHEMA = "wireless-poc-target-binding/v1"
CORE_CONSTRAINTS = ("protocol", "protocol_version", "phy", "identity_type")
OPTIONAL_CONSTRAINTS = ("model", "firmware", "entity_id")
SUPPORTED_CONSTRAINTS = CORE_CONSTRAINTS + OPTIONAL_CONSTRAINTS


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _label(value: Any, field: str) -> str:
    return _text(value, field).casefold()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: invalid JSON: {exc.msg}") from exc


def normalize_manifest(raw: Any) -> dict[str, Any]:
    """Validate the complete PoC compatibility contract."""
    if not isinstance(raw, dict):
        raise ValidationError("manifest must be an object")
    if raw.get("schema") != MANIFEST_SCHEMA:
        raise ValidationError(f"manifest.schema must be {MANIFEST_SCHEMA}")
    poc = raw.get("poc")
    if not isinstance(poc, dict):
        raise ValidationError("manifest.poc must be an object")
    normalized_poc = {
        "name": _text(poc.get("name"), "poc.name"),
        "version": _text(poc.get("version"), "poc.version"),
        "mode": _label(poc.get("mode"), "poc.mode"),
    }
    if normalized_poc["mode"] != "offline-non-transmitting":
        raise ValidationError("poc.mode must be offline-non-transmitting")
    constraints = raw.get("constraints")
    if not isinstance(constraints, dict):
        raise ValidationError("manifest.constraints must be an object")
    unsupported = sorted(set(constraints) - set(SUPPORTED_CONSTRAINTS))
    if unsupported:
        raise ValidationError("unsupported constraints: " + ", ".join(unsupported))
    missing = [field for field in CORE_CONSTRAINTS if field not in constraints]
    if missing:
        raise ValidationError("missing core constraints: " + ", ".join(missing))
    normalized_constraints = {
        field: _label(value, f"constraints.{field}")
        for field, value in constraints.items()
    }
    maturity = _label(raw.get("minimum_evidence_maturity"), "minimum_evidence_maturity")
    if maturity not in MATURITY_RANK:
        raise ValidationError("minimum_evidence_maturity is unsupported")
    return {
        "schema": MANIFEST_SCHEMA,
        "poc": normalized_poc,
        "constraints": dict(sorted(normalized_constraints.items())),
        "minimum_evidence_maturity": maturity,
    }


def select_track(raw: Any, entity_id: str | None) -> dict[str, Any]:
    """Select exactly one track or reject ambiguous evidence."""
    if not isinstance(raw, dict):
        raise ValidationError("target must be an object")
    if raw.get("schema") == TRACK_SET_SCHEMA:
        tracks = raw.get("tracks")
        if not isinstance(tracks, list):
            raise ValidationError("target track set needs a tracks array")
        if entity_id is None:
            if len(tracks) != 1:
                raise ValidationError(
                    "--entity-id is required for a track set with other than one track"
                )
            track = tracks[0]
        else:
            matches = [
                item for item in tracks
                if isinstance(item, dict) and item.get("entity_id") == entity_id
            ]
            if len(matches) != 1:
                raise ValidationError("--entity-id did not select exactly one track")
            track = matches[0]
    else:
        track = raw
        if entity_id is not None and track.get("entity_id") != entity_id:
            raise ValidationError("--entity-id does not match the target track")
    if not isinstance(track, dict):
        raise ValidationError("selected target track must be an object")
    _text(track.get("entity_id"), "target.entity_id")
    return track


def _target_value(track: dict[str, Any], field: str) -> tuple[str | None, str | None]:
    conflicts = track.get("compatibility_conflicts")
    if field in {"protocol_version", "phy", "model", "firmware"}:
        if isinstance(conflicts, dict) and conflicts.get(field):
            return None, "conflicting target evidence"
        compatibility = track.get("compatibility")
        if not isinstance(compatibility, dict) or compatibility.get(field) is None:
            return None, "unknown target field"
        return _label(compatibility[field], f"target.compatibility.{field}"), None
    if field == "protocol":
        if track.get("protocol") is None:
            return None, "unknown target field"
        return _label(track["protocol"], "target.protocol"), None
    if field == "identity_type":
        identity = track.get("identity")
        if not isinstance(identity, dict) or identity.get("kind") is None:
            return None, "unknown target field"
        return _label(identity["kind"], "target.identity.kind"), None
    if field == "entity_id":
        return _label(track["entity_id"], "target.entity_id"), None
    raise AssertionError(field)


def bind(manifest_raw: Any, target_raw: Any, entity_id: str | None = None) -> dict[str, Any]:
    """Return an accepted or rejected deterministic target-binding receipt."""
    manifest = normalize_manifest(manifest_raw)
    track = select_track(target_raw, entity_id)
    checks: list[dict[str, Any]] = []
    for field, expected in manifest["constraints"].items():
        observed, reason = _target_value(track, field)
        passed = reason is None and observed == expected
        if reason is None and not passed:
            reason = "exact constraint mismatch"
        checks.append({
            "constraint": field,
            "expected": expected,
            "observed": observed,
            "passed": passed,
            "reason": reason or "exact match",
        })

    minimum = manifest["minimum_evidence_maturity"]
    observed_maturity = track.get("max_evidence_maturity")
    if isinstance(observed_maturity, str):
        observed_maturity = observed_maturity.casefold()
    maturity_passed = (
        observed_maturity in MATURITY_RANK
        and MATURITY_RANK[observed_maturity] >= MATURITY_RANK[minimum]
    )
    checks.append({
        "constraint": "minimum_evidence_maturity",
        "expected": minimum,
        "observed": observed_maturity,
        "passed": maturity_passed,
        "reason": (
            "threshold met"
            if maturity_passed
            else "unknown or insufficient evidence maturity"
        ),
    })
    checks.sort(key=lambda item: item["constraint"])
    accepted = all(item["passed"] for item in checks)
    manifest_sha256 = _digest(manifest)
    target_sha256 = _digest(track)
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "accepted" if accepted else "rejected",
        "entity_id": track["entity_id"],
        "poc": manifest["poc"],
        "manifest_sha256": manifest_sha256,
        "target_sha256": target_sha256,
        "checks": checks,
    }
    if accepted:
        receipt["binding_id"] = "binding:v1:" + _digest({
            "entity_id": track["entity_id"],
            "manifest_sha256": manifest_sha256,
            "target_sha256": target_sha256,
            "poc": manifest["poc"],
        })
    return receipt
