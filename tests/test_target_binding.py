from __future__ import annotations

import copy
import json
import unittest

from elrs_sdr import target_binding
from elrs_sdr.identity import ValidationError


def manifest() -> dict:
    return {
        "schema": target_binding.MANIFEST_SCHEMA,
        "poc": {
            "name": "unit-poc",
            "version": "1.0.0",
            "mode": "offline-non-transmitting",
        },
        "constraints": {
            "protocol": "elrs",
            "protocol_version": "ota-v3",
            "phy": "lora-sx1280",
            "model": "synthetic-model",
            "firmware": "synthetic-4.4.2",
            "identity_type": "uid",
        },
        "minimum_evidence_maturity": "identified",
    }


def track() -> dict:
    return {
        "entity_id": "did:v1:" + "a" * 64,
        "identity": {"kind": "uid", "value": "001122334455"},
        "protocol": "elrs",
        "compatibility": {
            "protocol_version": "ota-v3",
            "phy": "lora-sx1280",
            "model": "synthetic-model",
            "firmware": "synthetic-4.4.2",
        },
        "compatibility_conflicts": {},
        "max_evidence_maturity": "identified",
    }


class TargetBindingTests(unittest.TestCase):
    def test_exact_constraints_accept(self):
        result = target_binding.bind(manifest(), track())
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["binding_id"].startswith("binding:v1:"))

    def test_mismatch_rejects_without_binding_id(self):
        candidate = manifest()
        candidate["constraints"]["protocol_version"] = "ota-v4"
        result = target_binding.bind(candidate, track())
        self.assertEqual(result["status"], "rejected")
        self.assertNotIn("binding_id", result)

    def test_unknown_or_conflicted_declared_field_rejects(self):
        unknown = track()
        unknown["compatibility"]["firmware"] = None
        self.assertEqual(
            target_binding.bind(manifest(), unknown)["status"],
            "rejected",
        )
        conflicted = track()
        conflicted["compatibility_conflicts"] = {
            "phy": ["lora-sx1280", "flrc"]
        }
        self.assertEqual(
            target_binding.bind(manifest(), conflicted)["status"],
            "rejected",
        )

    def test_insufficient_maturity_rejects(self):
        candidate = track()
        candidate["max_evidence_maturity"] = "specific"
        self.assertEqual(
            target_binding.bind(manifest(), candidate)["status"],
            "rejected",
        )

    def test_receipt_is_deterministic(self):
        first = json.dumps(target_binding.bind(manifest(), track()), sort_keys=True)
        reordered = copy.deepcopy(manifest())
        reordered["constraints"] = dict(reversed(list(reordered["constraints"].items())))
        second = json.dumps(target_binding.bind(reordered, track()), sort_keys=True)
        self.assertEqual(first, second)

    def test_multiple_tracks_require_selection(self):
        target_set = {
            "schema": target_binding.TRACK_SET_SCHEMA,
            "tracks": [track(), track()],
        }
        with self.assertRaises(ValidationError):
            target_binding.bind(manifest(), target_set)

    def test_active_mode_is_invalid(self):
        candidate = manifest()
        candidate["poc"]["mode"] = "active"
        with self.assertRaises(ValidationError):
            target_binding.bind(candidate, track())


if __name__ == "__main__":
    unittest.main()
