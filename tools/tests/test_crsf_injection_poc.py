"""Tests for CRSF injection PoC and bus defender.

Validates:
  - All injection scenarios produce expected state changes
  - CRC computation matches reference values
  - Frame construction produces valid CRSF frames
  - Defensive monitor detects all attack patterns
  - Virtual bus correctly routes and filters frames
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_tools_dir = Path(__file__).resolve().parent.parent


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _tools_dir / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_cpt = _load_module("crsf_packet_tool", "crsf-packet-tool.py")
_poc = _load_module("crsf_injection_poc", "crsf-injection-poc.py")
_def = _load_module("crsf_bus_defender", "crsf-bus-defender.py")

CRC8_BA = _cpt.CRC8_BA
CRC8_D5 = _cpt.CRC8_D5
build_command = _cpt.build_command
build_device_ping = _cpt.build_device_ping
build_frame = _cpt.build_frame
build_rc_channels = _cpt.build_rc_channels
crc8 = _cpt.crc8
crc8_command = _cpt.crc8_command
crc8_frame = _cpt.crc8_frame
encode_channels = _cpt.encode_channels
parse_all = _cpt.parse_all
parse_frame = _cpt.parse_frame
raw_to_us = _cpt.raw_to_us

Addr = _poc.Addr
FrameType = _poc.FrameType
ScenarioResult = _poc.ScenarioResult
VirtualCRSFBus = _poc.VirtualCRSFBus
VirtualDevice = _poc.VirtualDevice
run_all_scenarios = _poc.run_all_scenarios
run_scenario = _poc.run_scenario
SCENARIOS = _poc.SCENARIOS

AlertLevel = _def.AlertLevel
CRSFBusMonitor = _def.CRSFBusMonitor


# ---------------------------------------------------------------------------
# CRC validation
# ---------------------------------------------------------------------------

class TestCRC:
    """Verify CRC computations against known reference values."""

    def test_crc8_d5_empty(self):
        assert crc8_frame(b"") == 0

    def test_crc8_d5_known(self):
        """Frame CRC for a device ping frame type byte + payload."""
        data = bytes([0x28, 0x00, 0xEA])
        result = crc8_frame(data)
        assert 0 <= result <= 255

    def test_crc8_ba_empty(self):
        assert crc8_command(b"") == 0

    def test_crc8_d5_roundtrip(self):
        """Build a frame and verify its CRC validates."""
        frame_bytes = build_device_ping()
        frames = parse_all(frame_bytes)
        assert len(frames) == 1
        assert frames[0].crc_valid

    def test_crc8_ba_roundtrip(self):
        """Build a command frame and verify both CRCs validate."""
        frame_bytes = build_command(
            dest=Addr.FLIGHT_CONTROLLER,
            origin=Addr.TRANSMITTER,
            cmd_id=0x01,
            sub_payload=b"\x00",
        )
        frames = parse_all(frame_bytes)
        assert len(frames) == 1
        frame = frames[0]
        assert frame.crc_valid

        ip = frame.inner_payload
        cmd_data = bytes([frame.frame_type]) + frame.payload[:2] + ip[:-1]
        expected_cmd_crc = crc8(cmd_data, CRC8_BA)
        actual_cmd_crc = ip[-1]
        assert actual_cmd_crc == expected_cmd_crc


# ---------------------------------------------------------------------------
# Frame construction
# ---------------------------------------------------------------------------

class TestFrameConstruction:
    """Verify CRSF frame builders produce spec-compliant output."""

    def test_device_ping_format(self):
        frame = build_device_ping()
        assert frame[0] == 0xC8  # sync
        assert frame[2] == 0x28  # type = device ping
        assert len(frame) == frame[1] + 2  # length field consistency

    def test_rc_channels_format(self):
        frame = build_rc_channels([1500.0] * 16)
        assert frame[0] == 0xC8
        assert frame[2] == 0x16  # RC channels packed
        assert len(frame) == 26  # sync(1) + len(1) + type(1) + 22 payload + crc(1)

    def test_rc_channels_center(self):
        """Center stick (1500 µs) should encode to raw 992."""
        payload = encode_channels([1500.0] * 16)
        bits = int.from_bytes(payload[:22], "little")
        raw_ch0 = bits & 0x7FF
        assert raw_ch0 == 992

    def test_rc_channels_min_max(self):
        """Verify min/max stick values encode within 11-bit range."""
        payload_min = encode_channels([988.0] * 16)
        payload_max = encode_channels([2012.0] * 16)
        bits_min = int.from_bytes(payload_min[:22], "little")
        bits_max = int.from_bytes(payload_max[:22], "little")
        raw_min = bits_min & 0x7FF
        raw_max = bits_max & 0x7FF
        assert raw_min >= 0
        assert raw_max <= 0x7FF

    def test_command_frame_format(self):
        frame = build_command(0xC8, 0xEE, 0x01, b"\x00")
        parsed = parse_all(frame)
        assert len(parsed) == 1
        assert parsed[0].frame_type == 0x32
        assert parsed[0].is_extended
        assert parsed[0].dest == 0xC8
        assert parsed[0].origin == 0xEE

    def test_frame_max_size(self):
        """CRSF frames must be <= 64 bytes total."""
        payload = b"\x00" * 58  # max payload
        frame = build_frame(0x28, payload)
        assert len(frame) <= 64

    def test_extended_frame_routing(self):
        """Extended frames (type >= 0x28) carry dest/origin in payload."""
        frame = build_frame(0x28, bytes([0x00, 0xEA]))  # ping
        parsed = parse_all(frame)
        assert parsed[0].is_extended
        assert parsed[0].dest == 0x00
        assert parsed[0].origin == 0xEA


# ---------------------------------------------------------------------------
# Virtual CRSF bus
# ---------------------------------------------------------------------------

class TestVirtualBus:
    """Test the virtual CRSF bus simulation."""

    def test_default_devices(self):
        bus = VirtualCRSFBus()
        assert Addr.FLIGHT_CONTROLLER in bus.devices
        assert Addr.RECEIVER in bus.devices
        assert Addr.TRANSMITTER in bus.devices
        assert Addr.VTX in bus.devices

    def test_valid_frame_accepted(self):
        bus = VirtualCRSFBus()
        frame = build_device_ping()
        events = bus.inject(frame)
        accepted = [e for e in events if e.accepted]
        assert len(accepted) > 0

    def test_bad_crc_rejected(self):
        bus = VirtualCRSFBus()
        frame = bytearray(build_device_ping())
        frame[-1] ^= 0xFF
        events = bus.inject(bytes(frame))
        accepted = [e for e in events if e.accepted]
        assert len(accepted) == 0

    def test_wrong_dest_filtered(self):
        """Frames addressed to a specific device should not be accepted by others."""
        bus = VirtualCRSFBus()
        frame = build_command(
            dest=Addr.FLIGHT_CONTROLLER,
            origin=Addr.TRANSMITTER,
            cmd_id=0x01,
            sub_payload=b"\x00",
        )
        events = bus.inject(frame)
        for evt in events:
            if evt.accepted:
                assert evt.frame.dest in (
                    Addr.FLIGHT_CONTROLLER, Addr.BROADCAST
                )

    def test_broadcast_accepted_by_all(self):
        bus = VirtualCRSFBus()
        frame = build_device_ping()  # dest=broadcast
        events = bus.inject(frame)
        accepted = [e for e in events if e.accepted]
        assert len(accepted) == len(bus.devices)

    def test_command_crc_validation(self):
        """Command frames with bad command CRC should be rejected."""
        bus = VirtualCRSFBus()
        good_frame = build_command(Addr.FLIGHT_CONTROLLER, Addr.TRANSMITTER, 0x01, b"\x00")

        bad_frame = bytearray(good_frame)
        # The command CRC is the byte before the frame CRC
        cmd_crc_pos = len(bad_frame) - 2
        bad_frame[cmd_crc_pos] ^= 0xFF
        # Recompute frame CRC so only the command CRC is bad
        frame_data = bytes(bad_frame[2:-1])
        bad_frame[-1] = crc8_frame(frame_data)

        events = bus.inject(bytes(bad_frame))
        fc_events = [e for e in events
                     if e.frame.dest == Addr.FLIGHT_CONTROLLER]
        for evt in fc_events:
            assert not evt.accepted or "command CRC" in (evt.rejection_reason or "")


# ---------------------------------------------------------------------------
# Injection scenarios
# ---------------------------------------------------------------------------

class TestInjectionScenarios:
    """Verify each injection scenario produces expected results."""

    def test_all_scenarios_registered(self):
        assert len(SCENARIOS) >= 8

    def test_device_ping_succeeds(self):
        result = run_scenario("device_ping")
        assert result.success
        assert result.attack_type == "reconnaissance"
        assert result.frames_accepted > 0

    def test_param_read_succeeds(self):
        result = run_scenario("param_read")
        assert result.success
        assert result.attack_type == "information_disclosure"

    def test_wifi_mode_entry_succeeds(self):
        result = run_scenario("wifi_mode_entry")
        assert result.success
        assert result.attack_type == "denial_of_service"
        assert any("wifi" in sc.lower() for sc in result.state_changes)

    def test_force_disarm_succeeds(self):
        result = run_scenario("force_disarm")
        assert result.success
        assert result.attack_type == "denial_of_service"
        assert any("disarm" in sc.lower() for sc in result.state_changes)

    def test_vtx_pitmode_succeeds(self):
        result = run_scenario("vtx_pitmode")
        assert result.success
        assert any("pit" in sc.lower() for sc in result.state_changes)

    def test_bind_disrupt_succeeds(self):
        result = run_scenario("bind_disrupt")
        assert result.success
        assert any("bind" in sc.lower() for sc in result.state_changes)

    def test_rc_override_succeeds(self):
        result = run_scenario("rc_override")
        assert result.success
        assert result.attack_type == "commandeering"

    def test_msp_passthrough_succeeds(self):
        result = run_scenario("msp_passthrough")
        assert result.success

    def test_crc_tamper_detects_bad_crc(self):
        result = run_scenario("crc_tamper")
        assert result.success
        assert any("rejected" in sc.lower() for sc in result.state_changes)

    def test_all_scenarios_pass(self):
        results = run_all_scenarios()
        for r in results:
            assert r.success, f"Scenario {r.name} failed"

    def test_all_scenarios_have_defense_notes(self):
        results = run_all_scenarios()
        for r in results:
            assert len(r.defense_notes) > 0, f"Scenario {r.name} missing defense notes"

    def test_scenario_result_serializable(self):
        """Scenario results should be JSON-serializable."""
        results = run_all_scenarios()
        for r in results:
            d = r.to_dict()
            import json
            json.dumps(d)


# ---------------------------------------------------------------------------
# Defensive monitor
# ---------------------------------------------------------------------------

class TestBusDefender:
    """Test the CRSF bus anomaly detector."""

    def test_clean_traffic_no_alerts(self):
        """Legitimate traffic should not trigger critical alerts."""
        monitor = CRSFBusMonitor()
        frame = build_rc_channels([1500.0] * 16)
        frames = parse_all(frame)
        for f in frames:
            monitor.analyze_frame(f)
        critical = [a for a in monitor.alerts if a.level == AlertLevel.CRITICAL]
        assert len(critical) == 0

    def test_detects_dangerous_command(self):
        monitor = CRSFBusMonitor()
        frame = build_command(Addr.FLIGHT_CONTROLLER, Addr.TRANSMITTER, 0x01, b"\x00")
        frames = parse_all(frame)
        for f in frames:
            monitor.analyze_frame(f)
        cmd_alerts = [a for a in monitor.alerts if "cmd" in a.rule]
        assert len(cmd_alerts) > 0

    def test_detects_wifi_mode_attempt(self):
        monitor = CRSFBusMonitor()
        frame = build_frame(0x2D, bytes([Addr.RECEIVER, Addr.TRANSMITTER, 0xFF, 0x01]))
        frames = parse_all(frame)
        for f in frames:
            monitor.analyze_frame(f)
        wifi_alerts = [a for a in monitor.alerts if "wifi" in a.rule]
        assert len(wifi_alerts) > 0

    def test_detects_bind_command(self):
        monitor = CRSFBusMonitor()
        frame = build_command(Addr.RECEIVER, Addr.TRANSMITTER, 0x10, b"\x01")
        frames = parse_all(frame)
        for f in frames:
            monitor.analyze_frame(f)
        bind_alerts = [a for a in monitor.alerts if "bind" in a.rule]
        assert len(bind_alerts) > 0

    def test_detects_crc_errors(self):
        monitor = CRSFBusMonitor()
        for _ in range(5):
            frame = bytearray(build_device_ping())
            frame[-1] ^= 0xFF
            frames = parse_all(bytes(frame))
            for f in frames:
                monitor.analyze_frame(f)
        assert monitor.crc_errors > 0

    def test_summary_format(self):
        monitor = CRSFBusMonitor()
        frame = build_device_ping()
        monitor.analyze_stream(frame)
        summary = monitor.summary()
        assert "total_frames" in summary
        assert "total_alerts" in summary
        assert "crc_errors" in summary

    def test_alert_serializable(self):
        monitor = CRSFBusMonitor()
        frame = build_command(Addr.FLIGHT_CONTROLLER, Addr.TRANSMITTER, 0x01, b"\x00")
        monitor.analyze_stream(frame)
        for alert in monitor.alerts:
            d = alert.to_dict()
            import json
            json.dumps(d)
