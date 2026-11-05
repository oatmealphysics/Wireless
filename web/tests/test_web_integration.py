"""Integration tests for the Flask web UI dashboard.

Verifies that the real scan tools are wired into the web endpoints
correctly, that SSE live updates work, and that action execution
dispatches to the correct chain backends.

Runs entirely offline — no hardware, no network targets.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# This is a pytest-style test file (pytest fixtures + bare asserts). It is
# intended to run under pytest, not `unittest` discovery. Guard the optional
# imports so `unittest discover` (which also scans this directory) skips the
# module cleanly instead of crashing on a missing pytest / flask.
try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover - exercised on minimal installs
    # Minimal shim so the @pytest.fixture decorators below still bind when the
    # real pytest is absent. The decorated functions become plain no-ops; the
    # test classes carry no unittest-collectable methods (they use fixtures),
    # so `unittest discover` finds nothing to run.
    import types

    pytest = types.ModuleType("pytest_shim")

    def _noop_fixture(*a, **k):
        def deco(fn):
            return fn
        return deco

    pytest.fixture = _noop_fixture

# Ensure tools/ is importable (same path manipulation as app.py)
TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "exploit_chains"))

# Import the app module after path setup (needs flask).
try:
    import web.app as webapp
except ModuleNotFoundError:  # pragma: no cover - flask not installed
    webapp = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_app_state():
    """Reset all global mutable state between tests."""
    webapp.targets = {}
    webapp.action_log = []
    webapp.scanner_status = {
        "wifi": "idle", "ble": "idle", "sdr": "idle",
        "mavlink": "idle", "telemetry": "idle",
    }
    webapp.is_scanning = False
    webapp.hw_status = {}
    webapp.events = []
    webapp.simulate_mode = False
    yield
    webapp.simulate_mode = False


@pytest.fixture
def client():
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


@pytest.fixture
def sim_client():
    """Client with simulate mode enabled."""
    webapp.simulate_mode = True
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    def test_status_returns_json(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "scanning" in data
        assert "scanner_status" in data
        assert "targets" in data
        assert "target_count" in data
        assert "simulate" in data
        assert "hardware" in data

    def test_status_reflects_simulate_mode(self, sim_client):
        resp = sim_client.get("/api/status")
        assert resp.get_json()["simulate"] is True

    def test_status_shows_targets_sorted_by_score(self, client):
        webapp.targets = {
            "a": {"id": "a", "name": "low", "score": 10},
            "b": {"id": "b", "name": "high", "score": 90},
        }
        resp = client.get("/api/status")
        tgts = resp.get_json()["targets"]
        assert tgts[0]["name"] == "high"
        assert tgts[1]["name"] == "low"


class TestScanEndpoint:
    def test_scan_rejects_when_already_scanning(self, sim_client):
        webapp.is_scanning = True
        resp = sim_client.post("/api/scan", json={})
        assert resp.status_code == 409
        assert "already" in resp.get_json()["error"].lower()

    def test_scan_starts_simulated(self, sim_client):
        resp = sim_client.post("/api/scan", json={"protocols": ["wifi"]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "scanning"
        assert data["simulate"] is True
        # Wait for the background thread to finish
        deadline = time.time() + 10
        while webapp.is_scanning and time.time() < deadline:
            time.sleep(0.2)
        assert not webapp.is_scanning
        assert len(webapp.targets) > 0

    def test_scan_populates_simulated_targets_with_expected_fields(self, sim_client):
        sim_client.post("/api/scan", json={})
        deadline = time.time() + 10
        while webapp.is_scanning and time.time() < deadline:
            time.sleep(0.2)
        for t in webapp.targets.values():
            assert "id" in t
            assert "protocol" in t
            assert "name" in t
            assert "score" in t
            assert "available_actions" in t
            assert "chain" in t

    def test_scan_live_calls_real_scanners(self, client):
        """Verify live mode dispatches to multi_recon scan functions."""
        mock_wifi = MagicMock(return_value=[{
            "protocol": "wifi",
            "ssid": "TELLO-TEST",
            "drone_type": "tello",
            "description": "Ryze Tello",
            "encryption": "Open",
            "is_open": True,
        }])
        mock_ble = MagicMock(return_value=[])
        mock_sdr = MagicMock(return_value=[])

        with patch("web.app.simulate_mode", False), \
             patch("exploit_chains.multi_recon.scan_wifi", mock_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", mock_ble), \
             patch("exploit_chains.multi_recon.scan_sdr", mock_sdr), \
             patch("web.app.AbortHandler"):
            resp = client.post("/api/scan", json={
                "protocols": ["wifi", "ble", "sdr"],
                "wifi_iface": "wlan0",
            })
            assert resp.status_code == 200

            deadline = time.time() + 10
            while webapp.is_scanning and time.time() < deadline:
                time.sleep(0.2)

            mock_wifi.assert_called_once()
            mock_ble.assert_called_once()
            mock_sdr.assert_called_once()

            # The WiFi result should have been converted and stored
            assert len(webapp.targets) == 1
            t = list(webapp.targets.values())[0]
            assert t["protocol"] == "wifi"
            assert "land" in t["available_actions"]

    def test_scan_live_scanner_error_doesnt_crash(self, client):
        """A failing scanner should not prevent other scanners from running."""
        mock_wifi = MagicMock(side_effect=RuntimeError("No WiFi adapter"))
        mock_ble = MagicMock(return_value=[])
        mock_sdr = MagicMock(return_value=[])

        with patch("web.app.simulate_mode", False), \
             patch("exploit_chains.multi_recon.scan_wifi", mock_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", mock_ble), \
             patch("exploit_chains.multi_recon.scan_sdr", mock_sdr), \
             patch("web.app.AbortHandler"):
            resp = client.post("/api/scan", json={"protocols": ["wifi", "ble", "sdr"]})
            assert resp.status_code == 200

            deadline = time.time() + 10
            while webapp.is_scanning and time.time() < deadline:
                time.sleep(0.2)

            # BLE and SDR should still have been called despite WiFi failure
            mock_ble.assert_called_once()
            mock_sdr.assert_called_once()

    def test_scan_config_forwarded_to_live_scanners(self, client):
        """Custom interface configs should be forwarded to the scan functions."""
        mock_wifi = MagicMock(return_value=[])
        mock_ble = MagicMock(return_value=[])
        mock_sdr = MagicMock(return_value=[])

        with patch("web.app.simulate_mode", False), \
             patch("exploit_chains.multi_recon.scan_wifi", mock_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", mock_ble), \
             patch("exploit_chains.multi_recon.scan_sdr", mock_sdr), \
             patch("web.app.AbortHandler"):
            client.post("/api/scan", json={
                "protocols": ["wifi", "ble", "sdr"],
                "wifi_iface": "wlan1",
                "ble_iface": "hci1",
                "sdr_args": "driver=rtlsdr",
                "scan_time": 5.0,
            })
            deadline = time.time() + 10
            while webapp.is_scanning and time.time() < deadline:
                time.sleep(0.2)

            args_wifi = mock_wifi.call_args
            assert args_wifi[0][0] == "wlan1"  # interface
            assert args_wifi[0][1] == 5.0      # timeout

            args_ble = mock_ble.call_args
            assert args_ble[0][0] == "hci1"

            args_sdr = mock_sdr.call_args
            assert args_sdr[0][0] == "driver=rtlsdr"


class TestTargetDetailEndpoint:
    def test_target_found(self, client):
        webapp.targets["wifi:TEST"] = {
            "id": "wifi:TEST", "name": "Test", "protocol": "wifi",
        }
        resp = client.get("/api/targets/wifi:TEST")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Test"

    def test_target_not_found(self, client):
        resp = client.get("/api/targets/nonexistent")
        assert resp.status_code == 404


class TestActionEndpoint:
    TELLO_TARGET = {
        "id": "wifi:60:60:1F:AA:BB:CC",
        "name": "TELLO-TEST",
        "protocol": "wifi",
        "chain": "wifi_tello",
        "drone_type": "tello",
        "available_actions": ["land", "emergency", "takeoff", "streamon", "rc_zero"],
        "details": {"ip": "192.168.10.1", "sdk_port": 8889},
    }

    def test_action_requires_target(self, client):
        resp = client.post("/api/targets/nonexistent/action", json={"action": "land"})
        assert resp.status_code == 404

    def test_action_requires_action_name(self, client):
        webapp.targets[self.TELLO_TARGET["id"]] = {**self.TELLO_TARGET}
        resp = client.post(
            f"/api/targets/{self.TELLO_TARGET['id']}/action",
            json={},
        )
        assert resp.status_code == 400

    def test_action_rejects_unavailable_action(self, client):
        webapp.targets["wifi:TEST"] = {
            "id": "wifi:TEST", "name": "Test", "available_actions": ["land"],
        }
        resp = client.post("/api/targets/wifi:TEST/action", json={"action": "nuke"})
        assert resp.status_code == 400
        assert "not available" in resp.get_json()["error"]

    def test_simulate_action_returns_dry_run(self, sim_client):
        webapp.targets[self.TELLO_TARGET["id"]] = {**self.TELLO_TARGET}
        resp = sim_client.post(
            f"/api/targets/{self.TELLO_TARGET['id']}/action",
            json={"action": "land"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "dry_run"
        assert "DRY-RUN" in data["result"]

    def test_simulate_action_appends_to_log(self, sim_client):
        webapp.targets[self.TELLO_TARGET["id"]] = {**self.TELLO_TARGET}
        sim_client.post(
            f"/api/targets/{self.TELLO_TARGET['id']}/action",
            json={"action": "land"},
        )
        assert len(webapp.action_log) == 1
        assert webapp.action_log[0]["action"] == "land"

    def test_live_action_dispatches_to_tello_handler(self, client):
        webapp.targets[self.TELLO_TARGET["id"]] = {**self.TELLO_TARGET}
        with patch("web.app._exec_wifi_tello", return_value={
            "status": "executed", "message": "Landed",
        }) as mock_exec:
            resp = client.post(
                f"/api/targets/{self.TELLO_TARGET['id']}/action",
                json={"action": "land"},
            )
            assert resp.status_code == 200
            mock_exec.assert_called_once()
            data = resp.get_json()
            assert data["status"] == "executed"

    def test_live_action_dispatches_to_mavlink_handler(self, client):
        mav_target = {
            "id": "mav:1:1", "name": "MAVLink Quad", "protocol": "mavlink",
            "chain": "mavlink",
            "available_actions": ["sniff", "disarm", "land", "rtl", "reboot", "replay"],
            "details": {"source": "192.168.1.100:14550", "port": 14550, "sys_id": 1},
        }
        webapp.targets[mav_target["id"]] = mav_target
        with patch("web.app._exec_mavlink", return_value={
            "status": "executed", "message": "Disarmed",
        }) as mock_exec:
            resp = client.post(
                f"/api/targets/{mav_target['id']}/action",
                json={"action": "disarm"},
            )
            assert resp.status_code == 200
            mock_exec.assert_called_once()

    def test_live_action_dispatches_to_elrs_handler(self, client):
        elrs_target = {
            "id": "elrs:A1:B2:C3", "name": "ELRS TX", "protocol": "elrs",
            "chain": "elrs",
            "available_actions": ["forge_disarm", "preview_fhss", "scan_deeper"],
            "details": {"binding_phrase": "iloveflying", "ota_version": 3},
        }
        webapp.targets[elrs_target["id"]] = elrs_target
        with patch("web.app._exec_elrs", return_value={
            "status": "executed", "message": "FHSS preview computed",
        }) as mock_exec:
            resp = client.post(
                f"/api/targets/{elrs_target['id']}/action",
                json={"action": "preview_fhss"},
            )
            assert resp.status_code == 200
            mock_exec.assert_called_once()

    def test_live_action_dispatches_to_parrot_handler(self, client):
        parrot_target = {
            "id": "wifi:A0:14:3D:11:22:33", "name": "AR.Drone", "protocol": "wifi",
            "chain": "wifi_parrot",
            "available_actions": ["land", "emergency_watchdog", "kill_control"],
            "details": {"ip": "192.168.1.1"},
        }
        webapp.targets[parrot_target["id"]] = parrot_target
        with patch("web.app._exec_wifi_parrot", return_value={
            "status": "executed", "message": "Landing",
        }) as mock_exec:
            resp = client.post(
                f"/api/targets/{parrot_target['id']}/action",
                json={"action": "land"},
            )
            assert resp.status_code == 200
            mock_exec.assert_called_once()

    def test_live_action_dispatches_to_ble_handler(self, client):
        ble_target = {
            "id": "ble:48:1C:B9:DE:AD:01", "name": "DJI-BLE", "protocol": "ble",
            "chain": "dji_ble",
            "available_actions": ["ble_scan_services"],
            "details": {"address": "48:1C:B9:DE:AD:01"},
        }
        webapp.targets[ble_target["id"]] = ble_target
        with patch("web.app._exec_ble", return_value={
            "status": "executed", "message": "Found 3 services",
        }) as mock_exec:
            resp = client.post(
                f"/api/targets/{ble_target['id']}/action",
                json={"action": "ble_scan_services"},
            )
            assert resp.status_code == 200
            mock_exec.assert_called_once()

    def test_live_action_error_in_handler(self, client):
        webapp.targets[self.TELLO_TARGET["id"]] = {**self.TELLO_TARGET}
        with patch("web.app._exec_wifi_tello", side_effect=ConnectionError("No drone")):
            resp = client.post(
                f"/api/targets/{self.TELLO_TARGET['id']}/action",
                json={"action": "land"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "error"
            assert "Action failed" in data["result"]


class TestClearEndpoint:
    def test_clear_removes_targets(self, client):
        webapp.targets["a"] = {"id": "a"}
        resp = client.post("/api/clear")
        assert resp.status_code == 200
        assert len(webapp.targets) == 0

    def test_clear_pushes_event(self, client):
        client.post("/api/clear")
        assert any(e["type"] == "targets_cleared" for e in webapp.events)


class TestActionDefsEndpoint:
    def test_returns_all_action_definitions(self, client):
        resp = client.get("/api/actions")
        assert resp.status_code == 200
        defs = resp.get_json()
        assert "land" in defs
        assert "emergency" in defs
        assert "sniff" in defs
        assert "replay" in defs
        assert "forge_disarm" in defs
        for name, defn in defs.items():
            assert "label" in defn
            assert "description" in defn
            assert "severity" in defn


class TestLogEndpoint:
    def test_log_returns_recent_entries(self, client):
        webapp.action_log.extend([
            {"id": "a", "action": "land", "timestamp": "2026-01-01T00:00:00Z"},
            {"id": "b", "action": "rtl", "timestamp": "2026-01-01T00:00:01Z"},
        ])
        resp = client.get("/api/log")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 2


class TestHardwareEndpoint:
    def test_hardware_returns_json(self, client):
        with patch("web.app.detect_hardware", return_value={
            "wifi": {"available": False}, "ble": {"available": False},
            "sdr": {"available": False}, "mavlink": {"available": True},
        }):
            resp = client.get("/api/hardware")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "wifi" in data
            assert "mavlink" in data


# ---------------------------------------------------------------------------
# SSE (Server-Sent Events) tests
# ---------------------------------------------------------------------------

class TestSSEEvents:
    def test_push_event_appends_to_global_list(self):
        webapp._push_event("test_event", {"key": "value"})
        assert len(webapp.events) == 1
        assert webapp.events[0]["type"] == "test_event"
        assert webapp.events[0]["data"] == {"key": "value"}
        assert "timestamp" in webapp.events[0]
        assert webapp.events[0]["id"] == 0

    def test_push_event_increments_id(self):
        webapp._push_event("ev1", {})
        webapp._push_event("ev2", {})
        assert webapp.events[0]["id"] == 0
        assert webapp.events[1]["id"] == 1

    def test_push_event_caps_at_max(self):
        for i in range(webapp.MAX_EVENTS + 50):
            webapp._push_event("ev", {"i": i})
        assert len(webapp.events) <= webapp.MAX_EVENTS

    def test_events_endpoint_returns_sse_content_type(self, client):
        webapp._push_event("test_ev", {"hello": "world"})
        resp = client.get("/api/events")
        assert resp.content_type.startswith("text/event-stream")

    def test_events_endpoint_streams_existing_events(self, client):
        webapp._push_event("target_found", {"name": "TELLO-ABC"})
        webapp._push_event("scan_complete", {"target_count": 1})

        resp = client.get("/api/events?last_id=-1")
        # Read a chunk of the streaming response
        data = b""
        for chunk in resp.response:
            data += chunk if isinstance(chunk, bytes) else chunk.encode()
            if b"scan_complete" in data:
                break
        text = data.decode()
        assert "event: target_found" in text
        assert "event: scan_complete" in text
        assert "TELLO-ABC" in text

    def test_events_endpoint_respects_last_id(self, client):
        webapp._push_event("old_event", {"a": 1})
        webapp._push_event("new_event", {"b": 2})

        resp = client.get("/api/events?last_id=0")
        data = b""
        for chunk in resp.response:
            data += chunk if isinstance(chunk, bytes) else chunk.encode()
            if b"new_event" in data:
                break
        text = data.decode()
        assert "old_event" not in text
        assert "event: new_event" in text

    def test_simulated_scan_emits_events(self, sim_client):
        sim_client.post("/api/scan", json={})
        deadline = time.time() + 10
        while webapp.is_scanning and time.time() < deadline:
            time.sleep(0.2)

        event_types = [e["type"] for e in webapp.events]
        assert "scan_start" in event_types
        assert "scanner_done" in event_types
        assert "target_found" in event_types
        assert "scan_complete" in event_types

    def test_live_scan_emits_events_on_error(self, client):
        mock_wifi = MagicMock(side_effect=RuntimeError("No adapter"))
        mock_ble = MagicMock(return_value=[])
        mock_sdr = MagicMock(return_value=[])

        with patch("web.app.simulate_mode", False), \
             patch("exploit_chains.multi_recon.scan_wifi", mock_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", mock_ble), \
             patch("exploit_chains.multi_recon.scan_sdr", mock_sdr), \
             patch("web.app.AbortHandler"):
            client.post("/api/scan", json={"protocols": ["wifi", "ble", "sdr"]})
            deadline = time.time() + 10
            while webapp.is_scanning and time.time() < deadline:
                time.sleep(0.2)

        event_types = [e["type"] for e in webapp.events]
        assert "scan_start" in event_types
        assert "scanner_error" in event_types
        error_events = [e for e in webapp.events if e["type"] == "scanner_error"]
        assert any(e["data"]["scanner"] == "wifi" for e in error_events)

    def test_action_emits_event(self, sim_client):
        target = {
            "id": "wifi:TEST", "name": "Test", "chain": "wifi_tello",
            "available_actions": ["land"],
            "details": {},
        }
        webapp.targets["wifi:TEST"] = target
        sim_client.post("/api/targets/wifi:TEST/action", json={"action": "land"})
        event_types = [e["type"] for e in webapp.events]
        assert "action_executed" in event_types


# ---------------------------------------------------------------------------
# Scanner wiring verification
# ---------------------------------------------------------------------------

class TestScannerWiring:
    """Verify that run_scan_live actually imports and calls the real scanner functions."""

    def test_run_scan_live_imports_multi_recon(self):
        """The live scan path must import from exploit_chains.multi_recon."""
        import importlib
        spec = importlib.util.find_spec("exploit_chains.multi_recon")
        assert spec is not None, "exploit_chains.multi_recon must be importable"

    def test_run_scan_live_wifi_wiring(self):
        """WiFi scanner should be called with correct interface and timeout."""
        calls = []
        def fake_wifi(iface, timeout, logger, abort):
            calls.append(("wifi", iface, timeout))
            return []

        with patch("exploit_chains.multi_recon.scan_wifi", fake_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_sdr", MagicMock(return_value=[])):
            webapp.run_scan_live(
                ["wifi"],
                {"wifi_iface": "wlan0", "ble_iface": "hci0",
                 "sdr_args": "driver=uhd", "scan_time": 10.0},
            )
        assert len(calls) == 1
        assert calls[0] == ("wifi", "wlan0", 10.0)

    def test_run_scan_live_mavlink_scanner(self):
        """MAVLink UDP scanner should be invoked for the 'mavlink' protocol."""
        with patch("web.app._scan_mavlink_udp", return_value=[]) as mock_mav, \
             patch("exploit_chains.multi_recon.scan_wifi", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_ble", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_sdr", MagicMock(return_value=[])):
            webapp.run_scan_live(
                ["mavlink"],
                {"wifi_iface": "wlan0", "ble_iface": "hci0",
                 "sdr_args": "driver=uhd", "scan_time": 10.0},
            )
        mock_mav.assert_called_once()

    def test_run_scan_live_telemetry_scanner(self):
        """Passive telemetry scanner should be invoked for the 'telemetry' protocol."""
        with patch("web.app._scan_passive_telemetry", return_value=[]) as mock_telem, \
             patch("exploit_chains.multi_recon.scan_wifi", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_ble", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_sdr", MagicMock(return_value=[])):
            webapp.run_scan_live(
                ["telemetry"],
                {"wifi_iface": "wlan0", "ble_iface": "hci0",
                 "sdr_args": "driver=uhd", "scan_time": 10.0},
            )
        mock_telem.assert_called_once()

    def test_target_conversion_preserves_chain_and_actions(self):
        """Targets from scan_wifi should get correct chain and available_actions."""
        raw = {
            "protocol": "wifi",
            "ssid": "TELLO-ABCDEF",
            "drone_type": "tello",
            "description": "Ryze Tello",
            "encryption": "Open",
            "is_open": True,
        }
        tid = webapp._make_target_id(raw)
        converted = webapp._convert_recon_target(raw, tid, time.time())
        assert converted["chain"] == "wifi_tello"
        assert "land" in converted["available_actions"]
        assert "takeoff" in converted["available_actions"]
        assert converted["threat_level"] in ("HIGH", "CRITICAL", "MEDIUM")

    def test_target_conversion_mavlink(self):
        raw = {
            "protocol": "mavlink_udp",
            "description": "MAVLink on port 14550",
            "port": 14550,
            "signed": False,
            "drone_type": "mavlink",
        }
        tid = webapp._make_target_id(raw)
        converted = webapp._convert_recon_target(raw, tid, time.time())
        assert converted["chain"] == "mavlink"
        assert "disarm" in converted["available_actions"]
        assert "sniff" in converted["available_actions"]
        assert "NO_MSG_SIGNING" in converted["vulns"]

    def test_vuln_detection(self):
        assert "OPEN_WIFI" in webapp._detect_vulns({"is_open": True})
        assert "NO_MSG_SIGNING" in webapp._detect_vulns({"signed": False})
        assert "UDP_SDK_EXPOSED" in webapp._detect_vulns({"drone_type": "tello"})
        assert "ROOT_TELNET" in webapp._detect_vulns({"drone_type": "parrot_ar"})
        assert "FHSS_PREDICTABLE" in webapp._detect_vulns({"drone_type": "elrs"})
        assert "BLE_EXPOSED" in webapp._detect_vulns({"drone_type": "dji_ble"})


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class TestScoring:
    def test_tello_scores_high(self):
        t = {"drone_type": "tello", "is_open": True}
        assert webapp._score_target(t) >= 90

    def test_unknown_scores_low(self):
        t = {"drone_type": "unknown"}
        assert webapp._score_target(t) <= 40

    def test_unsigned_mavlink_gets_bonus(self):
        t = {"drone_type": "mavlink", "signed": False}
        assert webapp._score_target(t) >= 85

    def test_threat_levels(self):
        assert webapp._threat_from_score({"score": 90}) == "CRITICAL"
        assert webapp._threat_from_score({"score": 70}) == "HIGH"
        assert webapp._threat_from_score({"score": 50}) == "MEDIUM"
        assert webapp._threat_from_score({"score": 20}) == "LOW"


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------

class TestIndexPage:
    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"DRONE SCANNER" in resp.data
