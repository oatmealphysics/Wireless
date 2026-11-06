from __future__ import annotations

import json
import unittest

from elrs_sdr import identity
from elrs_sdr.detect import Detection, EvidenceClass, NetID
from elrs_sdr.fusion import TrackTable, detection_event
from elrs_sdr.protocol_catalog import DetectionLevel


def observation(uid: str, frequency_hz: int, maturity: str = "specific") -> dict:
    return {
        "kind": "detection",
        "timestamp": "2026-01-01T00:00:00Z",
        "protocol": "elrs",
        "protocol_version": "ota-v3",
        "phy": "lora-sx1280",
        "model": "synthetic-sx1280-fixture",
        "firmware": "synthetic-4.4.2",
        "frequency_hz": frequency_hz,
        "evidence_maturity": maturity,
        "identity": {"kind": "uid", "value": uid},
        "source": "unit-fixture",
    }


def detected(frequency_hz: int) -> Detection:
    return Detection(
        protocol="ExpressLRS",
        detection_level=DetectionLevel.IDENTIFIED,
        freq_hz=frequency_hz,
        power_db=20.0,
        net_id=NetID(
            raw_net_id=bytes.fromhex("001122334455"),
            net_id_str="001122334455",
            kind="uid",
            evidence=EvidenceClass.SELF_CONSISTENT,
        ),
        evidence=EvidenceClass.SELF_CONSISTENT,
    )


class IdentityTests(unittest.TestCase):
    def test_same_typed_identity_merges_across_frequencies(self):
        result = identity.build_tracks([
            observation("001122334455", 2_401_000_000),
            observation("00:11:22:33:44:55", 2_479_000_000, "identified"),
        ])
        self.assertEqual(result["track_count"], 1)
        track = result["tracks"][0]
        self.assertEqual(track["observation_count"], 2)
        self.assertEqual(track["frequencies_hz"], [2_401_000_000, 2_479_000_000])
        self.assertEqual(track["max_evidence_maturity"], "identified")

    def test_distinct_typed_identities_stay_distinct(self):
        result = identity.build_tracks([
            observation("001122334455", 2_401_000_000),
            observation("102132435465", 2_401_000_000),
        ])
        self.assertEqual(result["track_count"], 2)

    def test_spectrum_never_creates_identity(self):
        result = identity.build_tracks([{
            "kind": "spectrum",
            "timestamp": "2026-01-01T00:00:00Z",
            "source": "unit-spectrum",
            "lower_frequency_hz": 2_400_000_000,
            "bin_resolution_hz": 1_000_000,
            "powers_dbm": [-90.0, -50.0],
            "calibration": "uncalibrated",
        }])
        self.assertEqual(result["track_count"], 0)
        self.assertEqual(result["spectrum_evidence_count"], 1)

    def test_output_is_deterministic(self):
        records = [
            observation("001122334455", 2_479_000_000),
            observation("001122334455", 2_401_000_000),
        ]
        first = json.dumps(identity.build_tracks(records), sort_keys=True)
        second = json.dumps(identity.build_tracks(reversed(records)), sort_keys=True)
        self.assertEqual(first, second)

    def test_conflicting_compatibility_becomes_unknown(self):
        first = observation("001122334455", 2_401_000_000)
        second = observation("001122334455", 2_479_000_000)
        second["protocol_version"] = "ota-v4"
        track = identity.build_tracks([first, second])["tracks"][0]
        self.assertIsNone(track["compatibility"]["protocol_version"])
        self.assertEqual(
            track["compatibility_conflicts"]["protocol_version"],
            ["ota-v3", "ota-v4"],
        )

    def test_unidentified_observation_fails_closed_without_unique_frequency(self):
        detection = detected(2_401_000_000)
        detection = Detection(
            protocol=detection.protocol,
            detection_level=DetectionLevel.SPECIFIC,
            freq_hz=detection.freq_hz,
            power_db=detection.power_db,
            net_id=None,
            evidence=detection.evidence,
        )
        with self.assertRaises(identity.ValidationError):
            detection.to_observation(timestamp="2026-01-01T00:00:00Z")

    def test_detection_uid_is_stable_across_hops(self):
        first = detected(2_401_000_000)
        second = detected(2_479_000_000)
        self.assertEqual(first.entity_id, second.entity_id)
        self.assertEqual(first.detection_uid, second.detection_uid)

    def test_fusion_adapter_uses_canonical_entity_id(self):
        table = TrackTable(forget_s=1_000_000_000)
        for detection in (detected(2_401_000_000), detected(2_479_000_000)):
            table.ingest(detection_event(
                detection,
                observed_at="2026-01-01T00:00:00Z",
                source="unit-detector",
                compatibility={"protocol_version": "ota-v3", "phy": "lora-sx1280"},
            ))
        rows = table.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["key"], detected(2_401_000_000).entity_id)
        self.assertEqual(rows[0]["entity_id"], rows[0]["key"])


if __name__ == "__main__":
    unittest.main()
