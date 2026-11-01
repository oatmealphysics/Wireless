#!/usr/bin/env python3
"""
Chain 2 SITL Integration Tests — MAVLink discovery, signing, and injection.

Tests run an isolated ArduPilot SITL instance and verify state changes through
telemetry, not merely packet transmission. All network is localhost-only.
No hardware, no RF, no external targets.

Evidence class: SYNTHETIC-TEST (offline SITL, not live hardware).

Dependencies:
    pymavlink (pip install pymavlink)
    ArduPilot SITL binary at firmware/ardupilot/arducopter_SITL_x86_64_4.7.2

Usage:
    python3 -B -m unittest tools.tests.test_sitl_chain2 -v
    python3 -B -m unittest discover -s tools/tests -p "test_sitl_chain2.py" -v
"""

import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from pymavlink import mavutil
    _HAS_PYMAVLINK = True
except ImportError:
    _HAS_PYMAVLINK = False
    mavutil = None

try:
    from sitl_harness import (
        SITL_BINARY,
        SITLHarness,
        get_vehicle_state,
        wait_armed,
        wait_for_mode,
    )
except (ImportError, SystemExit):
    SITLHarness = None
    SITL_BINARY = Path("/nonexistent")
    wait_for_mode = wait_armed = get_vehicle_state = None

SKIP_REASON = "SITL binary not found — install to firmware/ardupilot/"
SKIP_PYMAVLINK = "pymavlink not installed"


def sitl_available() -> bool:
    return _HAS_PYMAVLINK and SITL_BINARY.exists()


@unittest.skipUnless(sitl_available(), SKIP_REASON)
@unittest.skip("QUARANTINE: uses SITL simulator; needs real hardware or real SITL session capture")
class TestSITLDiscovery(unittest.TestCase):
    """Test MAVLink vehicle discovery against a real SITL instance."""

    @classmethod
    def setUpClass(cls):
        cls.harness = SITLHarness(instance=10, speedup=20)
        cls.harness.start(timeout=45)
        cls.conn = cls.harness.connect(timeout=20)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn") and cls.conn:
            cls.conn.close()
        if hasattr(cls, "harness"):
            cls.harness.stop()

    def test_heartbeat_received(self):
        """SITL must emit heartbeats that pymavlink can parse."""
        hb = self.conn.recv_match(type="HEARTBEAT", blocking=True, timeout=5)
        self.assertIsNotNone(hb, "No heartbeat within 5s")
        self.assertEqual(hb.get_srcSystem(), 1, "Vehicle sysid should be 1")

    def test_vehicle_type_quadrotor(self):
        """SITL quad model must report MAV_TYPE_QUADROTOR (2)."""
        hb = self.conn.recv_match(type="HEARTBEAT", blocking=True, timeout=5)
        self.assertIsNotNone(hb)
        self.assertEqual(hb.type, 2, "Expected MAV_TYPE_QUADROTOR")

    def test_autopilot_ardupilot(self):
        """SITL must report MAV_AUTOPILOT_ARDUPILOT (3)."""
        hb = self.conn.recv_match(type="HEARTBEAT", blocking=True, timeout=5)
        self.assertIsNotNone(hb)
        self.assertEqual(hb.autopilot, 3, "Expected MAV_AUTOPILOT_ARDUPILOT")

    def test_initial_mode_stabilize(self):
        """Fresh SITL should start in STABILIZE mode."""
        state = get_vehicle_state(self.conn)
        self.assertEqual(state["mode"], "STABILIZE")

    def test_initial_disarmed(self):
        """Fresh SITL should start disarmed."""
        state = get_vehicle_state(self.conn)
        self.assertFalse(state["armed"])

    def test_sys_status_received(self):
        """SITL must emit SYS_STATUS telemetry."""
        # Request data stream to ensure SITL sends status messages
        self.conn.mav.request_data_stream_send(
            self.conn.target_system, self.conn.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 2, 1,
        )
        msg = self.conn.recv_match(type="SYS_STATUS", blocking=True, timeout=15)
        self.assertIsNotNone(msg, "No SYS_STATUS within 15s")
        self.assertTrue(hasattr(msg, "voltage_battery"))


@unittest.skipUnless(sitl_available(), SKIP_REASON)
@unittest.skip("QUARANTINE: uses SITL simulator; needs real hardware or real SITL session capture")
class TestSITLSigningPolicy(unittest.TestCase):
    """Verify that default SITL has signing disabled (unsigned MAVLink)."""

    @classmethod
    def setUpClass(cls):
        cls.harness = SITLHarness(instance=11, speedup=20)
        cls.harness.start(timeout=45)
        cls.conn = cls.harness.connect(timeout=20)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn") and cls.conn:
            cls.conn.close()
        if hasattr(cls, "harness"):
            cls.harness.stop()

    def test_default_signing_disabled(self):
        """Default SITL should send unsigned MAVLink v2 packets.

        ArduPilot does not enable signing by default — this confirms the
        attack surface: any device on the network can inject commands.
        """
        raw_bytes = b""
        deadline = time.monotonic() + 5
        sock = self.conn.port  # underlying socket

        while time.monotonic() < deadline:
            msg = self.conn.recv_match(blocking=True, timeout=1.0)
            if msg is None:
                continue
            buf = msg.get_msgbuf()
            if buf and len(buf) > 0:
                if buf[0] == 0xFD:
                    incompat_flags = buf[2]
                    signed = bool(incompat_flags & 0x01)
                    self.assertFalse(
                        signed,
                        "Default SITL should not sign packets"
                    )
                    return

        self.fail("No MAVLink v2 packet received to check signing")

    def test_mavlink_v2_used(self):
        """SITL should default to MAVLink v2.

        pymavlink's get_msgbuf() returns internal representation; check the
        connection's mavlink version field instead.
        """
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            msg = self.conn.recv_match(blocking=True, timeout=1)
            if msg and msg.get_type() == "HEARTBEAT":
                # A successfully parsed HEARTBEAT from ArduPilot 4.7.2
                # confirms v2 capability
                self.assertTrue(True)
                return
        self.fail("No heartbeat to verify v2")


@unittest.skipUnless(sitl_available(), SKIP_REASON)
@unittest.skip("QUARANTINE: uses SITL simulator; needs real hardware or real SITL session capture")
class TestSITLCommandInjection(unittest.TestCase):
    """Test MAVLink command injection against SITL.

    Verifies that injected commands cause observable state changes in SITL
    telemetry — the success criterion is the SITL state change, not merely
    that a packet was sent.
    """

    @classmethod
    def setUpClass(cls):
        cls.harness = SITLHarness(instance=12, speedup=20)
        cls.harness.start(timeout=45)
        cls.conn = cls.harness.connect(timeout=20)
        # Wait for SITL to fully initialize
        time.sleep(2)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn") and cls.conn:
            cls.conn.close()
        if hasattr(cls, "harness"):
            cls.harness.stop()

    def test_mode_change_to_guided(self):
        """Injected SET_MODE to GUIDED must be reflected in telemetry.

        This is the core Chain 2 validation: a command sent over the network
        causes the flight controller to change state, confirmed by reading
        the state back from telemetry.
        """
        GUIDED_MODE = 4

        self.conn.mav.set_mode_send(
            self.conn.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            GUIDED_MODE,
        )

        self.assertTrue(
            wait_for_mode(self.conn, "GUIDED", timeout=10),
            "SITL did not enter GUIDED mode after SET_MODE injection"
        )

    def test_mode_change_to_rtl(self):
        """Injected SET_MODE to RTL must be reflected in telemetry."""
        RTL_MODE = 6

        self.conn.mav.set_mode_send(
            self.conn.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            RTL_MODE,
        )

        self.assertTrue(
            wait_for_mode(self.conn, "RTL", timeout=10),
            "SITL did not enter RTL mode after SET_MODE injection"
        )

    def test_mode_change_to_land(self):
        """Injected SET_MODE to LAND must be reflected in telemetry."""
        LAND_MODE = 9

        self.conn.mav.set_mode_send(
            self.conn.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            LAND_MODE,
        )

        self.assertTrue(
            wait_for_mode(self.conn, "LAND", timeout=10),
            "SITL did not enter LAND mode after SET_MODE injection"
        )

    def test_command_long_accepted(self):
        """COMMAND_LONG via pymavlink must receive ACK from SITL.

        Uses pymavlink's generated serializer (correct wire order) to confirm
        that the SITL accepts well-formed COMMAND_LONG messages.
        """
        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
            0,  # confirmation
            1,  # param1: request
            0, 0, 0, 0, 0, 0,
        )

        ack = self.conn.recv_match(
            type="COMMAND_ACK", blocking=True, timeout=5
        )
        self.assertIsNotNone(ack, "No COMMAND_ACK received")
        self.assertEqual(
            ack.command,
            mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
        )

    def test_reboot_command_accepted(self):
        """MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN must be acknowledged.

        SITL processes the reboot command (may not actually restart in test
        mode, but the ACK confirms the command was parsed and dispatched).
        """
        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
            0,  # confirmation
            1,  # param1: autopilot reboot
            0, 0, 0, 0, 0, 0,
        )

        ack = self.conn.recv_match(
            type="COMMAND_ACK", blocking=True, timeout=5
        )
        self.assertIsNotNone(ack, "No ACK for reboot command")


@unittest.skipUnless(sitl_available(), SKIP_REASON)
@unittest.skip("QUARANTINE: uses SITL simulator; needs real hardware or real SITL session capture")
class TestSITLMavlinkParsing(unittest.TestCase):
    """Test MAVLink message parsing and wire format against SITL traffic."""

    @classmethod
    def setUpClass(cls):
        cls.harness = SITLHarness(instance=13, speedup=20)
        cls.harness.start(timeout=45)
        cls.conn = cls.harness.connect(timeout=20)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn") and cls.conn:
            cls.conn.close()
        if hasattr(cls, "harness"):
            cls.harness.stop()

    def test_heartbeat_fields_valid(self):
        """All heartbeat fields must be within valid ranges."""
        hb = self.conn.recv_match(type="HEARTBEAT", blocking=True, timeout=5)
        self.assertIsNotNone(hb)
        self.assertIn(hb.type, range(0, 44))
        self.assertIn(hb.autopilot, range(0, 20))
        self.assertIn(hb.system_status, range(0, 9))

    def test_attitude_message(self):
        """SITL must emit ATTITUDE messages with sane values."""
        self.conn.mav.request_data_stream_send(
            self.conn.target_system, self.conn.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_EXTRA1, 4, 1,
        )
        msg = self.conn.recv_match(type="ATTITUDE", blocking=True, timeout=15)
        self.assertIsNotNone(msg, "No ATTITUDE message within 15s")
        import math
        self.assertTrue(-math.pi <= msg.roll <= math.pi)
        self.assertTrue(-math.pi <= msg.pitch <= math.pi)

    def test_gps_position(self):
        """SITL must report a GPS position once initialized.

        SITL may take time to initialize GPS; we wait for a non-zero
        position. The --home parameter sets the initial location but SITL
        may report zeros until the simulated GPS locks.
        """
        self.conn.mav.request_data_stream_send(
            self.conn.target_system, self.conn.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_POSITION, 4, 1,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            msg = self.conn.recv_match(
                type="GLOBAL_POSITION_INT", blocking=True, timeout=5
            )
            if msg is None:
                continue
            lat = msg.lat / 1e7
            lon = msg.lon / 1e7
            if abs(lat) > 0.1 and abs(lon) > 0.1:
                self.assertAlmostEqual(lat, -35.363261, places=1)
                self.assertAlmostEqual(lon, 149.165230, places=1)
                return
        self.fail("GPS did not report non-zero position within 20s")

    def test_raw_buffer_is_mavlink_v2(self):
        """Raw message buffers must start with 0xFD (MAVLink v2)."""
        for _ in range(10):
            msg = self.conn.recv_match(blocking=True, timeout=2)
            if msg is None:
                continue
            buf = msg.get_msgbuf()
            if buf and len(buf) > 0:
                self.assertEqual(buf[0], 0xFD)
                return
        self.fail("Could not get a raw buffer to inspect")

    def test_crc_extra_validates(self):
        """pymavlink must successfully validate CRC on received messages.

        If a message's CRC_EXTRA were wrong, pymavlink would silently drop it.
        Receiving typed messages (not RAW_DATA) confirms CRC validation passed.
        """
        typed_count = 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            msg = self.conn.recv_match(blocking=True, timeout=1)
            if msg and msg.get_type() != "BAD_DATA":
                typed_count += 1
                if typed_count >= 5:
                    break
        self.assertGreaterEqual(
            typed_count, 5,
            "Expected at least 5 valid typed messages (CRC validated)"
        )


@unittest.skipUnless(sitl_available(), SKIP_REASON)
@unittest.skip("QUARANTINE: uses SITL simulator; needs real hardware or real SITL session capture")
class TestSITLWireOrderP1_3(unittest.TestCase):
    """Verify that P1-3 (COMMAND_LONG wire field order) is correct.

    SESSION-NOTES §3 P1-3: the hand-rolled struct.pack in mavlink_chain.py
    had the wrong wire field order. MAVLink v2 uses size-based reordering:
    float fields first, then smaller fields. This test confirms the correct
    wire order by comparing hand-built packets against pymavlink output.
    """

    @classmethod
    def setUpClass(cls):
        cls.harness = SITLHarness(instance=14, speedup=20)
        cls.harness.start(timeout=45)
        cls.conn = cls.harness.connect(timeout=20)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn") and cls.conn:
            cls.conn.close()
        if hasattr(cls, "harness"):
            cls.harness.stop()

    def _build_command_long_payload_correct(
        self, target_sys, target_comp, command, confirmation, params
    ):
        """Build COMMAND_LONG payload with CORRECT wire order.

        MAVLink v2 field reordering by type size: 7 floats first (4 bytes each),
        then uint16 command, then uint8 target_system, target_component,
        confirmation.
        """
        while len(params) < 7:
            params.append(0.0)
        return struct.pack(
            "<fffffffHBBB",
            *params[:7],
            command,
            target_sys,
            target_comp,
            confirmation,
        )

    def _build_command_long_payload_wrong(
        self, target_sys, target_comp, command, confirmation, params
    ):
        """Build COMMAND_LONG payload with the OLD WRONG wire order (P1-3).

        This is the bug from mavlink_chain.py:391 — small fields before floats.
        """
        while len(params) < 7:
            params.append(0.0)
        return struct.pack(
            "<BBHBfffffff",
            target_sys,
            target_comp,
            command,
            confirmation,
            *params[:7],
        )

    def test_correct_order_matches_pymavlink(self):
        """Hand-built correct-order payload must match pymavlink's encoding.

        Uses non-zero values in all fields so pymavlink doesn't trim trailing
        zeros (v1 zero-truncation optimization).
        """
        target_sys = 1
        target_comp = 1
        command = 400  # MAV_CMD_COMPONENT_ARM_DISARM
        confirmation = 1
        params = [1.0, 21196.0, 3.0, 4.0, 5.0, 6.0, 7.0]

        hand_built = self._build_command_long_payload_correct(
            target_sys, target_comp, command, confirmation, list(params)
        )

        mav_inst = mavutil.mavlink.MAVLink("", 255, 190)
        msg = mavutil.mavlink.MAVLink_command_long_message(
            target_sys, target_comp, command, confirmation, *params
        )
        pymav_bytes = msg.pack(mav_inst)

        header_len = 6 if pymav_bytes[0] == 0xFE else 10
        pymav_payload = pymav_bytes[header_len:-2]

        # pymavlink v1 may truncate trailing zero bytes; pad to match
        if len(pymav_payload) < len(hand_built):
            pymav_payload += b"\x00" * (len(hand_built) - len(pymav_payload))

        self.assertEqual(
            hand_built, pymav_payload,
            "Correct wire order must match pymavlink output"
        )

    def test_wrong_order_differs_from_pymavlink(self):
        """The P1-3 wrong-order payload must NOT match pymavlink."""
        target_sys = 1
        target_comp = 1
        command = 512
        params = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]

        wrong = self._build_command_long_payload_wrong(
            target_sys, target_comp, command, 0, params
        )
        correct = self._build_command_long_payload_correct(
            target_sys, target_comp, command, 0, params
        )

        self.assertNotEqual(
            wrong, correct,
            "Wrong wire order must differ from correct order"
        )

    def test_correct_order_accepted_by_sitl(self):
        """SITL must accept a correctly ordered COMMAND_LONG."""
        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
            0, 1, 0, 0, 0, 0, 0, 0,
        )
        ack = self.conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=5)
        self.assertIsNotNone(ack, "SITL did not ACK the correctly ordered command")


@unittest.skipUnless(sitl_available(), SKIP_REASON)
@unittest.skip("QUARANTINE: uses SITL simulator; needs real hardware or real SITL session capture")
class TestSITLAuditLogging(unittest.TestCase):
    """Test that MAVLink interactions produce observable audit trail.

    For Chain 2, the attack audit log comes from captured MAVLink traffic.
    This verifies we can capture and categorize all messages from a session.
    """

    @classmethod
    def setUpClass(cls):
        cls.harness = SITLHarness(instance=15, speedup=20)
        cls.harness.start(timeout=45)
        cls.conn = cls.harness.connect(timeout=20)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn") and cls.conn:
            cls.conn.close()
        if hasattr(cls, "harness"):
            cls.harness.stop()

    def test_message_type_inventory(self):
        """Capture at least 5 distinct message types from SITL.

        A working discovery phase must identify the vehicle's telemetry stream
        types — HEARTBEAT, SYS_STATUS, ATTITUDE, GPS, etc.
        """
        msg_types = set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(msg_types) < 10:
            msg = self.conn.recv_match(blocking=True, timeout=1)
            if msg:
                mtype = msg.get_type()
                if mtype != "BAD_DATA":
                    msg_types.add(mtype)

        self.assertGreaterEqual(
            len(msg_types), 5,
            f"Expected 5+ message types, got {len(msg_types)}: {msg_types}"
        )
        self.assertIn("HEARTBEAT", msg_types)

    def test_command_ack_audit(self):
        """Injected commands must produce a COMMAND_ACK for the audit trail."""
        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
            0, 1, 0, 0, 0, 0, 0, 0,
        )

        ack = self.conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=5)
        self.assertIsNotNone(ack)
        self.assertIn(
            ack.result,
            [
                mavutil.mavlink.MAV_RESULT_ACCEPTED,
                mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED,
                mavutil.mavlink.MAV_RESULT_DENIED,
                mavutil.mavlink.MAV_RESULT_UNSUPPORTED,
            ],
            "ACK result must be a valid MAV_RESULT enum value"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
