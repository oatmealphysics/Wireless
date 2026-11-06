from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from elrs_sdr.detect import (
    Detection,
    EvidenceClass,
    NetID,
    _demod_burst,
    _promote_lora_detections,
)
from elrs_sdr.protocol_catalog import DetectionLevel


class DetectIntegrationTests(unittest.TestCase):
    def test_capture_corroboration_promotes_frozen_records_by_copy(self):
        original = Detection(
            protocol="ExpressLRS",
            detection_level=DetectionLevel.SPECIFIC,
            freq_hz=2_401_000_000,
            power_db=18.0,
            net_id=None,
            evidence=EvidenceClass.SELF_CONSISTENT,
            extra={"kept": True},
        )
        net_id = NetID(
            raw_net_id=bytes.fromhex("000000004455"),
            net_id_str="4455",
            kind="uid",
        )
        promoted = _promote_lora_detections(
            [original],
            net_id,
            corroborated_bursts=3,
            ota_version=3,
        )[0]
        self.assertIsNone(original.net_id)
        self.assertEqual(promoted.net_id, net_id)
        self.assertEqual(promoted.detection_level, DetectionLevel.IDENTIFIED)
        self.assertTrue(promoted.crc_valid)
        self.assertEqual(promoted.extra["corroborated_bursts"], 3)
        self.assertEqual(promoted.extra["ota_version"], 3)
        self.assertEqual(promoted.extra["kept"], True)

    def test_detection_uid_is_total_for_none_level(self):
        """detection_uid is a display helper and must not raise on any rung.

        DetectionLevel.NONE has no identity maturity in the shared contract;
        the id accessor floors it rather than raising, keeping the old
        total-function behavior for existing display/logging callers.
        """
        none_level = Detection(
            protocol="unknown",
            detection_level=DetectionLevel.NONE,
            freq_hz=2_400_000_000,
            power_db=5.0,
            net_id=None,
            evidence=EvidenceClass.PRESENCE,
        )
        uid = none_level.detection_uid
        self.assertIsInstance(uid, str)
        self.assertEqual(len(uid), 12)
        # Flooring maturity must not change the id of a real (>=PRESENCE)
        # detection: maturity is not part of the entity-id hash.
        presence_level = Detection(
            protocol="unknown",
            detection_level=DetectionLevel.PRESENCE,
            freq_hz=2_400_000_000,
            power_db=5.0,
            net_id=None,
            evidence=EvidenceClass.PRESENCE,
        )
        self.assertEqual(none_level.entity_id, presence_level.entity_id)

    def test_wifi_beacon_mac_reaches_identified_level(self):
        burst = np.ones(4096, dtype=np.complex128)
        mac = bytes.fromhex("001122334455")
        with (
            patch("elrs_sdr.positivedet.scan_lora_modes", return_value=(0.0, None, None, {})),
            patch("elrs_sdr.core.identify.classify_gfsk", side_effect=ValueError("not gfsk")),
            patch("elrs_sdr.positivedet.ofdm_preamble", return_value=(1.0, "ofdm")),
            patch("elrs_sdr.wifi.decode_beacon_mac", return_value=mac),
        ):
            protocol, level, net_id, evidence, _ = _demod_burst(burst, 20e6, None)
        self.assertEqual(protocol, "WiFi")
        self.assertEqual(level, DetectionLevel.IDENTIFIED)
        self.assertEqual(net_id.raw_net_id, mac)
        self.assertEqual(evidence, EvidenceClass.VALIDATED)


if __name__ == "__main__":
    unittest.main()
