#!/usr/bin/env python3
"""Tests for esp32-hci-probe.py — CVE-2025-27840 detection tool."""

import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from importlib import import_module

# Import the module with hyphens in the name
import importlib.util
spec = importlib.util.spec_from_file_location(
    "esp32_hci_probe",
    os.path.join(os.path.dirname(__file__), "..", "esp32-hci-probe.py"),
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class TestHciConstants:
    def test_vendor_ogf(self):
        assert probe.VENDOR_OGF == 0x3F

    def test_opcode_construction(self):
        opcode = probe.make_opcode(0x3F, 0x01)
        assert opcode == 0xFC01

    def test_opcode_read_memory(self):
        opcode = probe.make_opcode(probe.VENDOR_OGF, probe.OCF_READ_MEMORY)
        assert opcode == 0xFC01

    def test_opcode_write_memory(self):
        opcode = probe.make_opcode(probe.VENDOR_OGF, probe.OCF_WRITE_MEMORY)
        assert opcode == 0xFC02

    def test_opcode_read_flash(self):
        opcode = probe.make_opcode(probe.VENDOR_OGF, probe.OCF_READ_FLASH)
        assert opcode == 0xFC08

    def test_all_29_commands_listed(self):
        assert len(probe.COMMAND_NAMES) == 29


class TestHciCommandBuilding:
    def test_basic_command_structure(self):
        cmd = probe.build_hci_command(probe.OCF_READ_MEMORY_INFO)
        assert cmd[0] == probe.HCI_COMMAND_PKT
        opcode = struct.unpack_from("<H", cmd, 1)[0]
        assert opcode == 0xFC12
        assert cmd[3] == 0  # no params

    def test_read_memory_command(self):
        cmd = probe.build_read_memory_cmd(0x3FFB0000, access_size=32, count=4)
        assert cmd[0] == probe.HCI_COMMAND_PKT
        opcode = struct.unpack_from("<H", cmd, 1)[0]
        assert opcode == 0xFC01
        param_len = cmd[3]
        assert param_len == 6  # 4 (addr) + 1 (size) + 1 (count)
        addr = struct.unpack_from("<I", cmd, 4)[0]
        assert addr == 0x3FFB0000
        assert cmd[8] == 32  # access size
        assert cmd[9] == 4   # count

    def test_read_flash_command(self):
        cmd = probe.build_read_flash_cmd(0x9000, 256)
        assert cmd[0] == probe.HCI_COMMAND_PKT
        opcode = struct.unpack_from("<H", cmd, 1)[0]
        assert opcode == 0xFC08
        addr, length = struct.unpack_from("<II", cmd, 4)
        assert addr == 0x9000
        assert length == 256


class TestHciEvent:
    def test_command_complete_parsing(self):
        data = struct.pack("<BHB", 1, 0xFC12, 0x00) + b"\xAA\xBB"
        evt = probe.HciEvent(probe.HCI_COMMAND_COMPLETE_EVT, len(data), data)
        assert evt.is_command_complete
        assert evt.status == 0x00
        assert evt.return_opcode == 0xFC12
        assert evt.return_data == b"\xAA\xBB"

    def test_unknown_command_status(self):
        data = struct.pack("<BHB", 1, 0xFC12, probe.HCI_UNKNOWN_COMMAND)
        evt = probe.HciEvent(probe.HCI_COMMAND_COMPLETE_EVT, len(data), data)
        assert evt.status == probe.HCI_UNKNOWN_COMMAND

    def test_non_command_complete(self):
        evt = probe.HciEvent(0x0F, 2, b"\x01\x02")
        assert not evt.is_command_complete
        assert evt.status is None


class TestSimulatedTransport:
    def test_vulnerable_device(self):
        transport = probe.SimulatedTransport(vulnerable=True)
        cmd = probe.build_hci_command(probe.OCF_READ_MEMORY_INFO)
        transport.send(cmd)
        evt = transport.recv_event()
        assert evt is not None
        assert evt.status == probe.HCI_SUCCESS

    def test_patched_device(self):
        transport = probe.SimulatedTransport(vulnerable=False)
        cmd = probe.build_hci_command(probe.OCF_READ_MEMORY_INFO)
        transport.send(cmd)
        evt = transport.recv_event()
        assert evt is not None
        assert evt.status == probe.HCI_UNKNOWN_COMMAND


class TestProbeVulnerableDevice:
    def test_vulnerable_detection(self):
        transport = probe.SimulatedTransport(vulnerable=True)
        results = probe.probe_vendor_commands(transport, detect_only=True)
        assert results["vulnerable"] is True
        assert len(results["commands_found"]) > 0

    def test_patched_detection(self):
        transport = probe.SimulatedTransport(vulnerable=False)
        results = probe.probe_vendor_commands(transport, detect_only=True)
        assert results["vulnerable"] is False
        assert len(results["commands_rejected"]) > 0
        assert len(results["commands_found"]) == 0


class TestSafeProbeSelection:
    def test_only_safe_commands_probed(self):
        """Verify we never probe write/erase/reset commands."""
        dangerous_ocfs = {
            probe.OCF_WRITE_MEMORY,
            probe.OCF_ERASE_FLASH,
            probe.OCF_WRITE_FLASH,
            probe.OCF_WRITE_NVDS,
            probe.OCF_DELETE_NVDS,
            probe.OCF_PLATFORM_RESET,
            probe.OCF_REGISTER_WRITE,
            probe.OCF_SET_MAC_ADDR,
            probe.OCF_SEND_LMP,
            probe.OCF_SEND_LLCP,
        }
        for ocf in probe.SAFE_PROBE_OCFS:
            assert ocf not in dangerous_ocfs, (
                f"OCF 0x{ocf:02X} ({probe.COMMAND_NAMES.get(ocf)}) is in SAFE_PROBE_OCFS "
                f"but is a dangerous command"
            )
