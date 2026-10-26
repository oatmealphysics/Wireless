"""Offline tests for msp-elrs-hijack-poc.py."""

import importlib.util
import hashlib
from pathlib import Path
import unittest

TOOL = Path(__file__).resolve().parents[1] / "msp-elrs-hijack-poc.py"


def _load():
    import sys
    name = "msp_elrs_hijack_poc"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class UidRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_uid_from_phrase(self):
        uid = self.mod.uid_from_phrase("iloveflying")
        self.assertEqual(len(uid), 6)
        expected = hashlib.md5(b"iloveflying").digest()[:6]
        self.assertEqual(uid, expected)

    def test_extract_uid_from_sync(self):
        from elrs_core.v3_compat import v3_build_sync, v3_decode_sync
        uid = self.mod.uid_from_phrase("testphrase")
        sync = v3_build_sync(uid, 0, 42)
        decoded = v3_decode_sync(sync)
        partial = self.mod.extract_uid_from_sync(decoded)
        self.assertEqual(partial["uid3"], uid[3])
        self.assertEqual(partial["uid4"], uid[4])
        self.assertEqual(partial["bits_recovered"], 18)
        self.assertEqual(partial["bits_remaining"], 30)

    def test_brute_force_estimate(self):
        partial = {"bits_remaining": 30}
        result = self.mod.brute_force_uid_sim(partial)
        self.assertEqual(result["search_space"], 2**30)
        self.assertTrue(result["feasible"])

    def test_brute_force_large(self):
        partial = {"bits_remaining": 48}
        result = self.mod.brute_force_uid_sim(partial)
        self.assertFalse(result["feasible"])


class ConnectionSimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_initial_state_disconnected(self):
        conn = self.mod.ElrsConnectionSim()
        self.assertEqual(conn.state, self.mod.ConnectionState.DISCONNECTED)
        self.assertFalse(conn.can_inject_msp)

    def test_sync_transitions(self):
        from elrs_core.v3_compat import v3_build_sync, v3_decode_sync
        uid = self.mod.uid_from_phrase("test")
        conn = self.mod.ElrsConnectionSim()

        sync1 = v3_decode_sync(v3_build_sync(uid, 0, 10))
        conn.process_sync(sync1, uid, 0.0)
        self.assertEqual(conn.state, self.mod.ConnectionState.TENTATIVE)

        sync2 = v3_decode_sync(v3_build_sync(uid, 0, 20))
        conn.process_sync(sync2, uid, 100.0)
        self.assertEqual(conn.state, self.mod.ConnectionState.CONNECTED)
        self.assertTrue(conn.can_inject_msp)

    def test_sync_uid_mismatch_rejected(self):
        from elrs_core.v3_compat import v3_build_sync, v3_decode_sync
        uid1 = self.mod.uid_from_phrase("phrase1")
        uid2 = self.mod.uid_from_phrase("phrase2")
        conn = self.mod.ElrsConnectionSim()

        sync = v3_decode_sync(v3_build_sync(uid1, 0, 10))
        msg = conn.process_sync(sync, uid2, 0.0)
        self.assertIn("mismatch", msg)
        self.assertEqual(conn.state, self.mod.ConnectionState.DISCONNECTED)

    def test_tentative_timeout(self):
        from elrs_core.v3_compat import v3_build_sync, v3_decode_sync
        uid = self.mod.uid_from_phrase("test")
        conn = self.mod.ElrsConnectionSim()

        sync = v3_decode_sync(v3_build_sync(uid, 0, 10))
        conn.process_sync(sync, uid, 0.0)
        self.assertEqual(conn.state, self.mod.ConnectionState.TENTATIVE)

        msg = conn.check_timeout(300.0)
        self.assertIsNotNone(msg)
        self.assertEqual(conn.state, self.mod.ConnectionState.DISCONNECTED)

    def test_connected_timeout(self):
        from elrs_core.v3_compat import v3_build_sync, v3_decode_sync
        uid = self.mod.uid_from_phrase("test")
        conn = self.mod.ElrsConnectionSim()

        s1 = v3_decode_sync(v3_build_sync(uid, 0, 10))
        conn.process_sync(s1, uid, 0.0)
        s2 = v3_decode_sync(v3_build_sync(uid, 0, 20))
        conn.process_sync(s2, uid, 100.0)
        self.assertEqual(conn.state, self.mod.ConnectionState.CONNECTED)

        msg = conn.check_timeout(2000.0)
        self.assertIsNotNone(msg)
        self.assertEqual(conn.state, self.mod.ConnectionState.DISCONNECTED)


class MspFramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_build_msp_command(self):
        frame = self.mod.build_msp_command(68, b"\x00")
        self.assertTrue(frame.startswith(b"$M<"))
        self.assertEqual(frame[3], 1)
        self.assertEqual(frame[4], 68)

    def test_crsf_frame_structure(self):
        frame = self.mod.build_crsf_msp_frame(68, b"\x00")
        self.assertEqual(frame[0], 0xC8)
        self.assertEqual(frame[2], 0x7C)

    def test_elrs_split_roundtrip(self):
        crsf = self.mod.build_crsf_msp_frame(214, bytes(16))
        chunks = self.mod.split_crsf_to_elrs_packets(crsf)
        self.assertTrue(len(chunks) > 1)
        reassembled = b""
        for c in chunks:
            reassembled += c[1:]
        self.assertTrue(reassembled.startswith(crsf))

    def test_elrs_data_frame_size(self):
        uid = self.mod.uid_from_phrase("test")
        chunk = bytes(5)
        frame = self.mod.build_elrs_data_frame(chunk, uid)
        self.assertEqual(len(frame), 8)
        self.assertEqual(frame[0] & 0x03, 0x01)


class AttackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_all_attacks_build(self):
        attacks = self.mod.get_attacks()
        self.assertIn("force-disarm", attacks)
        self.assertIn("reboot", attacks)
        self.assertIn("canary", attacks)
        for name, atk in attacks.items():
            self.assertGreater(atk.elrs_packets_needed, 0,
                               f"{name} has zero packets")

    def test_canary_is_smallest(self):
        attacks = self.mod.get_attacks()
        canary = attacks["canary"]
        for name, atk in attacks.items():
            if name != "canary":
                self.assertLessEqual(canary.elrs_packets_needed,
                                     atk.elrs_packets_needed)


class HijackSimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_simulation_completes(self):
        uid = self.mod.uid_from_phrase("iloveflying")
        result = self.mod.run_hijack_simulation(uid, "canary", show_timing=True)
        self.assertEqual(result.uid, uid)
        self.assertIsNotNone(result.attack_plan)
        self.assertIsNotNone(result.timing)
        self.assertGreater(len(result.connection_log), 0)

    def test_simulation_force_disarm(self):
        uid = self.mod.uid_from_phrase("test")
        result = self.mod.run_hijack_simulation(uid, "force-disarm")
        self.assertEqual(result.attack_plan["attack"], "force-disarm")
        self.assertGreater(result.attack_plan["elrs_packets_needed"], 0)

    def test_timing_analysis(self):
        uid = self.mod.uid_from_phrase("test")
        attacks = self.mod.get_attacks()
        timing = self.mod.compute_timing_analysis(uid, attacks)
        self.assertEqual(timing["packet_rate_hz"], 150)
        self.assertIn("canary", timing["attacks"])
        for name, info in timing["attacks"].items():
            self.assertGreater(info["packets"], 0)
            self.assertGreater(info["total_from_first_sync_ms"], 0)


if __name__ == "__main__":
    unittest.main()
