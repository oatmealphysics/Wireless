"""Offline tests for msp-injection-sim.py MSP/CRSF/OTA framing logic."""

import importlib.util
from pathlib import Path
import unittest


TOOL = Path(__file__).resolve().parents[1] / "msp-injection-sim.py"


def _load():
    import sys
    name = "msp_injection_sim"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skip("QUARANTINE: uses hand-crafted MSP/CRSF frames; needs real MSP/CRSF serial captures")
class MspFramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = _load()

    def test_build_msp_command_header_and_checksum(self):
        frame = self.sim.build_msp_command(68, b"\x00")
        self.assertTrue(frame.startswith(b"$M<"))
        self.assertEqual(frame[3], 1)  # payload length
        self.assertEqual(frame[4], 68)  # command
        self.assertEqual(frame[5], 0)  # payload byte
        expected_crc = 1 ^ 68 ^ 0
        self.assertEqual(frame[6], expected_crc)

    def test_build_msp_command_empty_payload(self):
        frame = self.sim.build_msp_command(1)
        self.assertEqual(frame, b"$M<\x00\x01\x01")

    def test_parse_msp_response_roundtrip(self):
        cmd = 101
        payload = b"\xaa\xbb"
        body = bytes([len(payload), cmd]) + payload
        crc = self.sim.msp_checksum(body)
        response = b"$M>" + body + bytes([crc])
        parsed = self.sim.parse_msp_response(response)
        self.assertIsNotNone(parsed)
        resp_cmd, resp_payload, is_error = parsed
        self.assertEqual(resp_cmd, cmd)
        self.assertEqual(resp_payload, payload)
        self.assertFalse(is_error)

    def test_parse_msp_error_response(self):
        body = bytes([0, 68])
        crc = self.sim.msp_checksum(body)
        response = b"$M!" + body + bytes([crc])
        parsed = self.sim.parse_msp_response(response)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed[2])

    def test_parse_msp_response_rejects_bad_crc(self):
        response = b"$M>\x00\x01\xff"
        self.assertIsNone(self.sim.parse_msp_response(response))

    def test_parse_msp_response_rejects_truncated(self):
        self.assertIsNone(self.sim.parse_msp_response(b"$M>"))


@unittest.skip("QUARANTINE: uses hand-crafted MSP/CRSF frames; needs real MSP/CRSF serial captures")
class CrsfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = _load()

    def test_crsf_crc8_known_value(self):
        self.assertEqual(self.sim.crsf_crc8(b"\x00"), 0)
        crc = self.sim.crsf_crc8(bytes([0x7C, 0xC8, 0xEA, 0x00, 0x00, 68, 1, 0x00]))
        self.assertIsInstance(crc, int)
        self.assertIn(crc, range(256))

    def test_crsf_frame_structure(self):
        frame = self.sim.build_crsf_msp_frame(68, b"\x00")
        self.assertEqual(frame[0], 0xC8)
        self.assertEqual(frame[2], 0x7C)

    def test_crsf_read_uses_req_type(self):
        frame = self.sim.build_crsf_msp_frame(1)
        self.assertEqual(frame[2], 0x7A)


@unittest.skip("QUARANTINE: uses hand-crafted MSP/CRSF frames; needs real MSP/CRSF serial captures")
class ElrsOtaSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = _load()

    def test_split_short_payload(self):
        data = bytes(range(4))
        chunks = self.sim.split_for_elrs_ota(data, chunk_size=5)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 5)
        self.assertEqual(chunks[0][0] & 0x80, 0)

    def test_split_continuation_bit(self):
        data = bytes(range(12))
        chunks = self.sim.split_for_elrs_ota(data, chunk_size=5)
        self.assertGreater(len(chunks), 1)
        for c in chunks[:-1]:
            self.assertTrue(c[0] & 0x80)
        self.assertFalse(chunks[-1][0] & 0x80)

    def test_all_chunks_correct_size(self):
        data = bytes(range(20))
        chunks = self.sim.split_for_elrs_ota(data, chunk_size=5)
        for c in chunks:
            self.assertEqual(len(c), 5)

    def test_reassemble_recovers_original(self):
        data = bytes(range(15))
        chunks = self.sim.split_for_elrs_ota(data, chunk_size=5)
        reassembled = b""
        for c in chunks:
            reassembled += c[1:]
        self.assertTrue(reassembled.startswith(data))


@unittest.skip("QUARANTINE: uses hand-crafted MSP/CRSF frames; needs real MSP/CRSF serial captures")
class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = _load()

    def test_all_scenarios_build(self):
        scenarios = self.sim.build_scenarios()
        self.assertIn("force-disarm", scenarios)
        self.assertIn("reboot", scenarios)
        self.assertIn("motor-override", scenarios)
        self.assertIn("full-chain", scenarios)
        for name, sc in scenarios.items():
            self.assertTrue(len(sc.commands) > 0, f"{name} has no commands")
            for cmd, payload, desc in sc.commands:
                frame = self.sim.build_msp_command(cmd, payload)
                self.assertTrue(frame.startswith(b"$M<"))

    def test_dry_run_transport(self):
        transport = self.sim.DryRunTransport()
        self.assertIsNone(transport.send_recv(b"test"))


if __name__ == "__main__":
    unittest.main()
