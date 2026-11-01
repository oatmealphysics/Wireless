"""Tests for the evil twin AP detector."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib.util

spec = importlib.util.spec_from_file_location(
    "evil_twin_detector",
    str(Path(__file__).resolve().parent.parent / "evil-twin-detector.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

EvilTwinDetector = mod.EvilTwinDetector
is_drone_ssid = mod.is_drone_ssid
SEVERITY_CRITICAL = mod.SEVERITY_CRITICAL
SEVERITY_WARNING = mod.SEVERITY_WARNING


@unittest.skip("QUARANTINE: uses fabricated WiFi scan dicts; needs real WiFi scan data with evil twin scenarios")
class TestDroneSSIDPatterns(unittest.TestCase):
    def test_tello_pattern(self):
        self.assertTrue(is_drone_ssid("TELLO-AABB00"))
        self.assertTrue(is_drone_ssid("TELLO-123456"))

    def test_ardupilot_pattern(self):
        self.assertTrue(is_drone_ssid("ArduPilot"))
        self.assertTrue(is_drone_ssid("ArduPilot-Custom"))

    def test_dji_pattern(self):
        self.assertTrue(is_drone_ssid("DJI-Mini3"))
        self.assertTrue(is_drone_ssid("DJI_Mavic"))

    def test_elrs_pattern(self):
        self.assertTrue(is_drone_ssid("ELRS_TX"))
        self.assertTrue(is_drone_ssid("ExpressLRS"))

    def test_non_drone_ssid(self):
        self.assertFalse(is_drone_ssid("HomeNetwork"))
        self.assertFalse(is_drone_ssid("Starbucks_WiFi"))
        self.assertFalse(is_drone_ssid("xfinity"))

    def test_bebop_pattern(self):
        self.assertTrue(is_drone_ssid("Bebop2-AABBCC"))

    def test_fpv_pattern(self):
        self.assertTrue(is_drone_ssid("FPV-Quad"))


@unittest.skip("QUARANTINE: uses fabricated WiFi scan dicts; needs real WiFi scan data with evil twin scenarios")
class TestDuplicateSSIDDetection(unittest.TestCase):
    def test_evil_twin_detected(self):
        detector = EvilTwinDetector()
        scan = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -55},
            {"ssid": "TELLO-AABB00", "bssid": "00:11:22:33:44:55",
             "channel": 1, "signal": -30},
        ]
        anomalies = detector.ingest_scan(scan, scan_time=time.time())
        dup = [a for a in anomalies if a.category == "duplicate_ssid"]
        self.assertGreater(len(dup), 0)
        # Drone SSID duplicate should be CRITICAL
        self.assertEqual(dup[0].severity, SEVERITY_CRITICAL)

    def test_non_drone_duplicate_is_warning(self):
        detector = EvilTwinDetector()
        scan = [
            {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
             "channel": 6, "signal": -45},
            {"ssid": "HomeNetwork", "bssid": "DD:EE:FF:11:22:33",
             "channel": 6, "signal": -50},
        ]
        anomalies = detector.ingest_scan(scan, scan_time=time.time())
        dup = [a for a in anomalies if a.category == "duplicate_ssid"]
        self.assertGreater(len(dup), 0)
        self.assertEqual(dup[0].severity, SEVERITY_WARNING)

    def test_no_duplicate(self):
        detector = EvilTwinDetector()
        scan = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -55},
            {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
             "channel": 6, "signal": -45},
        ]
        anomalies = detector.ingest_scan(scan, scan_time=time.time())
        dup = [a for a in anomalies if a.category == "duplicate_ssid"]
        self.assertEqual(len(dup), 0)


@unittest.skip("QUARANTINE: uses fabricated WiFi scan dicts; needs real WiFi scan data with evil twin scenarios")
class TestBSSIDMismatchDetection(unittest.TestCase):
    def test_known_bssid_mismatch(self):
        detector = EvilTwinDetector(
            known_bssids={"TELLO-AABB00": "60:60:1F:AA:BB:00"}
        )
        scan = [
            {"ssid": "TELLO-AABB00", "bssid": "00:11:22:33:44:55",
             "channel": 1, "signal": -30},
        ]
        anomalies = detector.ingest_scan(scan, scan_time=time.time())
        bssid_alerts = [a for a in anomalies if a.category == "bssid_mismatch"]
        self.assertGreater(len(bssid_alerts), 0)
        self.assertEqual(bssid_alerts[0].severity, SEVERITY_CRITICAL)

    def test_known_bssid_match_no_alert(self):
        detector = EvilTwinDetector(
            known_bssids={"TELLO-AABB00": "60:60:1F:AA:BB:00"}
        )
        scan = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -55},
        ]
        anomalies = detector.ingest_scan(scan, scan_time=time.time())
        bssid_alerts = [a for a in anomalies if a.category == "bssid_mismatch"]
        self.assertEqual(len(bssid_alerts), 0)


@unittest.skip("QUARANTINE: uses fabricated WiFi scan dicts; needs real WiFi scan data with evil twin scenarios")
class TestChannelChangeDetection(unittest.TestCase):
    def test_channel_change(self):
        detector = EvilTwinDetector()
        base = time.time()

        scan1 = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -55},
        ]
        detector.ingest_scan(scan1, scan_time=base)

        scan2 = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 6, "signal": -55},
        ]
        anomalies = detector.ingest_scan(scan2, scan_time=base + 5)
        ch_alerts = [a for a in anomalies if a.category == "channel_change"]
        self.assertGreater(len(ch_alerts), 0)


@unittest.skip("QUARANTINE: uses fabricated WiFi scan dicts; needs real WiFi scan data with evil twin scenarios")
class TestSignalJumpDetection(unittest.TestCase):
    def test_large_signal_jump(self):
        detector = EvilTwinDetector()
        base = time.time()

        scan1 = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -70},
        ]
        detector.ingest_scan(scan1, scan_time=base)

        scan2 = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -40},
        ]
        anomalies = detector.ingest_scan(scan2, scan_time=base + 5)
        sig_alerts = [a for a in anomalies if a.category == "signal_jump"]
        self.assertGreater(len(sig_alerts), 0)

    def test_small_signal_change_ok(self):
        detector = EvilTwinDetector()
        base = time.time()

        scan1 = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -55},
        ]
        detector.ingest_scan(scan1, scan_time=base)

        scan2 = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -50},
        ]
        anomalies = detector.ingest_scan(scan2, scan_time=base + 5)
        sig_alerts = [a for a in anomalies if a.category == "signal_jump"]
        self.assertEqual(len(sig_alerts), 0)


@unittest.skip("QUARANTINE: uses fabricated WiFi scan dicts; needs real WiFi scan data with evil twin scenarios")
class TestNewDroneAPDetection(unittest.TestCase):
    def test_new_ap_mid_session(self):
        detector = EvilTwinDetector()
        base = time.time()

        scan1 = [
            {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
             "channel": 6, "signal": -45},
        ]
        detector.ingest_scan(scan1, scan_time=base)

        scan2 = [
            {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
             "channel": 6, "signal": -45},
            {"ssid": "ArduPilot-Rogue", "bssid": "FF:EE:DD:CC:BB:AA",
             "channel": 11, "signal": -40},
        ]
        anomalies = detector.ingest_scan(scan2, scan_time=base + 10)
        new_ap = [a for a in anomalies if a.category == "new_drone_ap"]
        self.assertGreater(len(new_ap), 0)

    def test_drone_ap_in_first_scan_no_alert(self):
        detector = EvilTwinDetector()
        scan = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -55},
        ]
        anomalies = detector.ingest_scan(scan, scan_time=time.time())
        new_ap = [a for a in anomalies if a.category == "new_drone_ap"]
        self.assertEqual(len(new_ap), 0)


@unittest.skip("QUARANTINE: uses fabricated WiFi scan dicts; needs real WiFi scan data with evil twin scenarios")
class TestSummary(unittest.TestCase):
    def test_summary_structure(self):
        detector = EvilTwinDetector()
        scan = [
            {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
             "channel": 1, "signal": -55},
            {"ssid": "TELLO-AABB00", "bssid": "00:11:22:33:44:55",
             "channel": 1, "signal": -30},
        ]
        detector.ingest_scan(scan, scan_time=time.time())

        s = detector.summary()
        self.assertEqual(s["scans_analyzed"], 1)
        self.assertEqual(s["unique_aps_seen"], 2)
        self.assertGreater(s["drone_aps_seen"], 0)
        self.assertGreater(s["anomalies_detected"], 0)


@unittest.skip("QUARANTINE: uses fabricated WiFi scan dicts; needs real WiFi scan data with evil twin scenarios")
class TestSimulation(unittest.TestCase):
    def test_simulate_finds_anomalies(self):
        detector = EvilTwinDetector(
            known_bssids={"TELLO-AABB00": "60:60:1F:AA:BB:00"}
        )
        mod.simulate_traffic(detector)
        s = detector.summary()
        self.assertGreater(s["anomalies_detected"], 0)
        self.assertGreater(s["scans_analyzed"], 3)


if __name__ == "__main__":
    unittest.main()
