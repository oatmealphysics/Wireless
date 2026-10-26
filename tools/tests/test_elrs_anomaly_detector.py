"""Tests for the ELRS anomaly detector."""

import hashlib
import struct
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elrs_core.crc import Crc2Byte, ELRS_CRC14_POLY
from elrs_core.ota import ota_uid_crc_init, PACKET_TYPE_SYNC, PACKET_TYPE_RCDATA

# Import detector components
import importlib.util
spec = importlib.util.spec_from_file_location(
    "elrs_anomaly_detector",
    str(Path(__file__).resolve().parent.parent / "elrs-anomaly-detector.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ELRSAnomalyDetector = mod.ELRSAnomalyDetector
uid_from_phrase = mod.uid_from_phrase
OTA4_PACKET_SIZE = mod.OTA4_PACKET_SIZE
SEVERITY_CRITICAL = mod.SEVERITY_CRITICAL
SEVERITY_WARNING = mod.SEVERITY_WARNING


def _make_rc_packet(uid: bytes, nonce: int, ota_version: int = 3) -> bytes:
    """Build a minimal valid RC packet with correct CRC."""
    pkt = bytearray(OTA4_PACKET_SIZE)
    pkt[0] = PACKET_TYPE_RCDATA
    pkt[1] = nonce
    pkt[2] = 0x5D
    pkt[3] = 0xC0
    pkt[4] = 0x5D
    pkt[5] = 0xC0
    pkt[6] = 0x00

    init = ota_uid_crc_init(uid, ota_version) ^ nonce
    crc_obj = Crc2Byte(14, ELRS_CRC14_POLY)
    crc_data = bytearray(pkt[:7])
    crc_data[0] &= 0x03
    crc_val = crc_obj.calc(bytes(crc_data), init)
    pkt[0] = (pkt[0] & 0x03) | (((crc_val >> 8) & 0x3F) << 2)
    pkt[7] = crc_val & 0xFF
    return bytes(pkt)


def _make_sync_packet(uid: bytes, fhss_idx: int = 0,
                      nonce_field: int = 0,
                      ota_version: int = 3) -> bytes:
    """Build a minimal SYNC packet with the given UID fields."""
    pkt = bytearray(OTA4_PACKET_SIZE)
    pkt[0] = PACKET_TYPE_SYNC
    pkt[1] = 0  # nonce for CRC is 0 for SYNC
    pkt[2] = fhss_idx
    pkt[3] = nonce_field
    pkt[4] = 0  # switchEncMode
    pkt[5] = uid[3]
    pkt[6] = uid[4]
    pkt[7] = uid[5]
    return bytes(pkt)


class TestUIDFromPhrase(unittest.TestCase):
    def test_known_phrase(self):
        uid = uid_from_phrase("iloveflying")
        expected = hashlib.md5(b"iloveflying").digest()[:6]
        self.assertEqual(uid, expected)
        self.assertEqual(len(uid), 6)

    def test_different_phrases_different_uids(self):
        uid1 = uid_from_phrase("phrase1")
        uid2 = uid_from_phrase("phrase2")
        self.assertNotEqual(uid1, uid2)


class TestDetectorInit(unittest.TestCase):
    def test_init_with_phrase(self):
        d = ELRSAnomalyDetector(phrase="iloveflying")
        self.assertEqual(len(d.state.uid), 6)
        self.assertGreater(len(d.state.fhss_sequence), 0)
        self.assertNotEqual(d.state.crc_init, 0)

    def test_init_with_uid(self):
        uid = b"\x01\x02\x03\x04\x05\x06"
        d = ELRSAnomalyDetector(uid=uid)
        self.assertEqual(d.state.uid, uid)

    def test_init_no_uid(self):
        d = ELRSAnomalyDetector()
        self.assertEqual(d.state.uid, b"")


class TestNormalTraffic(unittest.TestCase):
    def test_valid_rc_no_anomalies(self):
        uid = uid_from_phrase("iloveflying")
        d = ELRSAnomalyDetector(phrase="iloveflying")

        base = time.time()
        for i in range(10):
            pkt = _make_rc_packet(uid, nonce=i)
            anomalies = d.ingest_packet(pkt, recv_time=base + i * 0.004)

        # No nonce reuse, each nonce is unique and spaced apart
        crc_anomalies = [a for a in d.anomalies if a.category == "crc_mismatch"]
        self.assertEqual(len(crc_anomalies), 0)

    def test_packet_count(self):
        d = ELRSAnomalyDetector(phrase="iloveflying")
        uid = uid_from_phrase("iloveflying")
        base = time.time()
        for i in range(5):
            pkt = _make_rc_packet(uid, nonce=i)
            d.ingest_packet(pkt, recv_time=base + i)
        self.assertEqual(d.state.packet_count, 5)


class TestUIDMismatchDetection(unittest.TestCase):
    def test_wrong_uid_in_sync(self):
        uid = uid_from_phrase("iloveflying")
        bad_uid = bytes([uid[0], uid[1], uid[2], 0xFF, 0xFF, 0xFF])

        d = ELRSAnomalyDetector(phrase="iloveflying")
        pkt = _make_sync_packet(bad_uid)
        anomalies = d.ingest_packet(pkt, recv_time=time.time())

        uid_anomalies = [a for a in anomalies if a.category == "uid_mismatch"]
        self.assertGreater(len(uid_anomalies), 0)
        self.assertEqual(uid_anomalies[0].severity, SEVERITY_CRITICAL)

    def test_correct_uid_no_alert(self):
        uid = uid_from_phrase("iloveflying")
        d = ELRSAnomalyDetector(phrase="iloveflying")
        pkt = _make_sync_packet(uid)
        anomalies = d.ingest_packet(pkt, recv_time=time.time())

        uid_anomalies = [a for a in anomalies if a.category == "uid_mismatch"]
        self.assertEqual(len(uid_anomalies), 0)


class TestSyncBurstDetection(unittest.TestCase):
    def test_sync_burst_triggers_alert(self):
        uid = uid_from_phrase("test")
        d = ELRSAnomalyDetector(phrase="test")
        base = time.time()

        # Send 6 SYNC packets within 2 seconds
        burst_anomalies = []
        for i in range(6):
            pkt = _make_sync_packet(uid)
            new = d.ingest_packet(pkt, recv_time=base + i * 0.1)
            burst_anomalies.extend(
                a for a in new if a.category == "sync_burst"
            )

        self.assertGreater(len(burst_anomalies), 0)

    def test_slow_syncs_no_burst(self):
        uid = uid_from_phrase("test")
        d = ELRSAnomalyDetector(phrase="test")
        base = time.time()

        # Send 4 SYNC packets spaced 1 second apart (below threshold)
        for i in range(4):
            pkt = _make_sync_packet(uid)
            d.ingest_packet(pkt, recv_time=base + i * 1.0)

        burst_anomalies = [a for a in d.anomalies if a.category == "sync_burst"]
        self.assertEqual(len(burst_anomalies), 0)


class TestNonceReplay(unittest.TestCase):
    def test_nonce_reuse_detected(self):
        d = ELRSAnomalyDetector(phrase="test")
        uid = uid_from_phrase("test")
        base = time.time()

        pkt1 = _make_rc_packet(uid, nonce=42)
        d.ingest_packet(pkt1, recv_time=base)

        pkt2 = _make_rc_packet(uid, nonce=42)
        anomalies = d.ingest_packet(pkt2, recv_time=base + 0.01)

        nonce_anomalies = [a for a in anomalies if a.category == "nonce_reuse"]
        self.assertGreater(len(nonce_anomalies), 0)

    def test_nonce_reuse_after_long_gap_ok(self):
        d = ELRSAnomalyDetector(phrase="test")
        uid = uid_from_phrase("test")
        base = time.time()

        pkt1 = _make_rc_packet(uid, nonce=42)
        d.ingest_packet(pkt1, recv_time=base)

        pkt2 = _make_rc_packet(uid, nonce=42)
        anomalies = d.ingest_packet(pkt2, recv_time=base + 5.0)

        nonce_anomalies = [a for a in anomalies if a.category == "nonce_reuse"]
        self.assertEqual(len(nonce_anomalies), 0)


class TestMalformedPackets(unittest.TestCase):
    def test_short_packet(self):
        d = ELRSAnomalyDetector(phrase="test")
        anomalies = d.ingest_packet(b"\x00\x01\x02", recv_time=time.time())
        mal = [a for a in anomalies if a.category == "malformed"]
        self.assertGreater(len(mal), 0)


class TestSummary(unittest.TestCase):
    def test_summary_counts(self):
        d = ELRSAnomalyDetector(phrase="test")
        uid = uid_from_phrase("test")
        bad_uid = bytes([0xFF] * 6)
        base = time.time()

        d.ingest_packet(_make_rc_packet(uid, 0), recv_time=base)
        d.ingest_packet(_make_sync_packet(bad_uid), recv_time=base + 1)

        s = d.summary()
        self.assertEqual(s["packets_analyzed"], 2)
        self.assertGreater(s["anomalies_detected"], 0)
        self.assertIn("uid_mismatch", s["by_category"])


class TestSimulation(unittest.TestCase):
    def test_simulate_finds_anomalies(self):
        d = ELRSAnomalyDetector(phrase="iloveflying")
        mod.simulate_traffic(d, count=20)
        s = d.summary()
        self.assertGreater(s["anomalies_detected"], 0)
        self.assertGreater(s["packets_analyzed"], 20)


if __name__ == "__main__":
    unittest.main()
