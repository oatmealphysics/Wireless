"""Offline tests for msp-vuln-scanner.py."""

import importlib.util
from pathlib import Path
import unittest

TOOL = Path(__file__).resolve().parents[1] / "msp-vuln-scanner.py"


def _load():
    import sys
    name = "msp_vuln_scanner"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class MspFramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_build_msp_command_header(self):
        frame = self.mod.build_msp_command(1)
        self.assertTrue(frame.startswith(b"$M<"))
        self.assertEqual(frame[4], 1)

    def test_parse_response_roundtrip(self):
        cmd = 101
        payload = b"\xaa\xbb"
        body = bytes([len(payload), cmd]) + payload
        crc = self.mod.msp_checksum(body)
        response = b"$M>" + body + bytes([crc])
        parsed = self.mod.parse_msp_response(response)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], cmd)
        self.assertEqual(parsed[1], payload)
        self.assertFalse(parsed[2])

    def test_parse_rejects_bad_crc(self):
        response = b"$M>\x00\x01\xff"
        self.assertIsNone(self.mod.parse_msp_response(response))

    def test_parse_rejects_truncated(self):
        self.assertIsNone(self.mod.parse_msp_response(b"$M>"))


class ElrsPacketSizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_small_command_fits_few_packets(self):
        pkts = self.mod.elrs_packets_for_msp(251, bytes([1, 0, 0, 0]))
        self.assertGreater(pkts, 0)
        self.assertLess(pkts, 10)

    def test_large_payload_needs_more_packets(self):
        small = self.mod.elrs_packets_for_msp(1, b"")
        large = self.mod.elrs_packets_for_msp(214, bytes(16))
        self.assertGreater(large, small)

    def test_empty_payload_needs_at_least_one(self):
        pkts = self.mod.elrs_packets_for_msp(1, b"")
        self.assertGreaterEqual(pkts, 1)


class CommandDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_database_not_empty(self):
        cmds = self.mod.build_command_database()
        self.assertGreater(len(cmds), 5)

    def test_critical_commands_present(self):
        cmds = self.mod.build_command_database()
        names = {c.name for c in cmds}
        self.assertIn("MSP_SET_ARMING_DISABLED", names)
        self.assertIn("MSP_REBOOT", names)
        self.assertIn("MSP_SET_MOTOR", names)

    def test_severity_assignments(self):
        cmds = self.mod.build_command_database()
        critical = [c for c in cmds if c.severity == self.mod.Severity.CRITICAL]
        self.assertGreaterEqual(len(critical), 3)

    def test_armed_check_annotation(self):
        cmds = self.mod.build_command_database()
        no_check = [c for c in cmds if not c.has_armed_check]
        self.assertGreater(len(no_check), 5)
        eeprom = [c for c in cmds if c.name == "MSP_EEPROM_WRITE"]
        self.assertTrue(eeprom[0].has_armed_check)

    def test_all_commands_have_payloads(self):
        cmds = self.mod.build_command_database()
        for cmd in cmds:
            self.assertIsInstance(cmd.payload_builder, bytes)
            frame = self.mod.build_msp_command(cmd.code, cmd.payload_builder)
            self.assertTrue(frame.startswith(b"$M<"))


class ScanLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_dry_run_transport(self):
        transport = self.mod.DryRunTransport()
        self.assertIsNone(transport.send_recv(b"test"))

    def test_identify_dry_run(self):
        transport = self.mod.DryRunTransport()
        info = self.mod.scan_identify(transport, execute=False)
        self.assertIsNotNone(info)
        self.assertIn("MSP_API_VERSION", info)
        self.assertEqual(info["MSP_API_VERSION"]["status"], "dry_run")

    def test_enumerate_dry_run(self):
        transport = self.mod.DryRunTransport()
        cmds = self.mod.build_command_database()
        results = self.mod.scan_enumerate(cmds, transport, execute=False, ota_version=3)
        self.assertEqual(len(results), len(cmds))
        for r in results:
            self.assertEqual(r["status"], "dry_run")
            self.assertIn("elrs_packets", r)

    def test_summary_builds(self):
        cmds = self.mod.build_command_database()
        transport = self.mod.DryRunTransport()
        results = self.mod.scan_enumerate(cmds, transport, False, 3)
        summary = self.mod.build_summary(results)
        self.assertGreater(summary["total_commands_scanned"], 0)
        self.assertGreater(summary["no_armed_check"], 0)
        self.assertIn("CRITICAL", summary["risk_assessment"])


if __name__ == "__main__":
    unittest.main()
