"""Integration tests for the Flask web UI dashboard.

Verifies that the registry/executor dispatch is wired into the web
endpoints correctly, that SSE live updates carry Result State
transitions, and that scan tools are connected.

Runs entirely offline -- no hardware, no network targets.
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

@pytest.fixture
def app_state():
    """Create a fresh DashboardState with simulation mode enabled."""
    state = webapp.create_dashboard_state(simulate=True, dry_run=True)
    return state


@pytest.fixture
def client(app_state):
    """Test client backed by a fresh app instance with its own state."""
    flask_app = webapp.create_app(app_state)
    flask_app.testing = True
    with flask_app.test_client() as c:
        yield c


@pytest.fixture
def live_app_state():
    """Fresh DashboardState with simulation off, dry-run off."""
    state = webapp.create_dashboard_state(simulate=False, dry_run=False)
    return state


@pytest.fixture
def live_client(live_app_state):
    """Test client with live mode (dry_run=False, simulate=False)."""
    flask_app = webapp.create_app(live_app_state)
    flask_app.testing = True
    with flask_app.test_client() as c:
        yield c


def _inject_target(state, target_dict):
    """Helper: inject a target into the state's targets dict."""
    with state.state_lock:
        state.targets[target_dict["id"]] = dict(target_dict)


def _arm(client, target_id, chain, actions):
    """Arm a simulation pack scope so an action POST is authorized.

    Authorization is always enforced for execution, so every action test must
    first create a time-limited scope against a discovered target.
    """
    return client.post(
        f"/api/packs/sim-{chain}/0.1.0/arm",
        json={
            "target_identity": target_id,
            "permitted_actions": list(actions),
            "time_limit_seconds": 300,
            "stop_conditions": ["operator_abort"],
            "sacrificial_target": True,
            "evidence_destination": "test",
        },
    )


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

    def test_status_reflects_simulate_mode(self, client, app_state):
        resp = client.get("/api/status")
        assert resp.get_json()["simulate"] is True

    def test_status_shows_targets_sorted_by_score(self, client, app_state):
        _inject_target(app_state, {"id": "a", "name": "low", "score": 10})
        _inject_target(app_state, {"id": "b", "name": "high", "score": 90})
        resp = client.get("/api/status")
        tgts = resp.get_json()["targets"]
        assert tgts[0]["name"] == "high"
        assert tgts[1]["name"] == "low"


class TestScanEndpoint:
    def test_scan_rejects_when_already_scanning(self, client, app_state):
        with app_state.state_lock:
            app_state.is_scanning = True
        resp = client.post("/api/scan", json={})
        assert resp.status_code == 409
        assert "already" in resp.get_json()["error"].lower()

    def test_scan_starts_simulated(self, client, app_state):
        resp = client.post("/api/scan", json={"protocols": ["wifi"]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "scanning"
        assert data["simulate"] is True
        # Wait for the background thread to finish
        deadline = time.time() + 10
        while app_state.is_scanning and time.time() < deadline:
            time.sleep(0.2)
        assert not app_state.is_scanning
        assert len(app_state.targets) > 0

    def test_scan_populates_simulated_targets_with_expected_fields(self, client, app_state):
        client.post("/api/scan", json={})
        deadline = time.time() + 10
        while app_state.is_scanning and time.time() < deadline:
            time.sleep(0.2)
        for t in app_state.targets.values():
            assert "id" in t
            assert "protocol" in t
            assert "name" in t
            assert "score" in t
            assert "available_actions" in t
            assert "chain" in t

    def test_scan_live_calls_real_scanners(self, live_client, live_app_state):
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

        with patch("exploit_chains.multi_recon.scan_wifi", mock_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", mock_ble), \
             patch("exploit_chains.multi_recon.scan_sdr", mock_sdr), \
             patch("web.app.AbortHandler"):
            resp = live_client.post("/api/scan", json={
                "protocols": ["wifi", "ble", "sdr"],
                "wifi_iface": "wlan0",
            })
            assert resp.status_code == 200

            deadline = time.time() + 10
            while live_app_state.is_scanning and time.time() < deadline:
                time.sleep(0.2)

            mock_wifi.assert_called_once()
            mock_ble.assert_called_once()
            mock_sdr.assert_called_once()

            # The WiFi result should have been converted and stored
            assert len(live_app_state.targets) == 1
            t = list(live_app_state.targets.values())[0]
            assert t["protocol"] == "wifi"
            assert "land" in t["available_actions"]

    def test_scan_live_scanner_error_doesnt_crash(self, live_client, live_app_state):
        """A failing scanner should not prevent other scanners from running."""
        mock_wifi = MagicMock(side_effect=RuntimeError("No WiFi adapter"))
        mock_ble = MagicMock(return_value=[])
        mock_sdr = MagicMock(return_value=[])

        with patch("exploit_chains.multi_recon.scan_wifi", mock_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", mock_ble), \
             patch("exploit_chains.multi_recon.scan_sdr", mock_sdr), \
             patch("web.app.AbortHandler"):
            resp = live_client.post("/api/scan", json={"protocols": ["wifi", "ble", "sdr"]})
            assert resp.status_code == 200

            deadline = time.time() + 10
            while live_app_state.is_scanning and time.time() < deadline:
                time.sleep(0.2)

            # BLE and SDR should still have been called despite WiFi failure
            mock_ble.assert_called_once()
            mock_sdr.assert_called_once()

    def test_scan_config_forwarded_to_live_scanners(self, live_client, live_app_state):
        """Custom interface configs should be forwarded to the scan functions."""
        mock_wifi = MagicMock(return_value=[])
        mock_ble = MagicMock(return_value=[])
        mock_sdr = MagicMock(return_value=[])

        with patch("exploit_chains.multi_recon.scan_wifi", mock_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", mock_ble), \
             patch("exploit_chains.multi_recon.scan_sdr", mock_sdr), \
             patch("web.app.AbortHandler"):
            live_client.post("/api/scan", json={
                "protocols": ["wifi", "ble", "sdr"],
                "wifi_iface": "wlan1",
                "ble_iface": "hci1",
                "sdr_args": "driver=rtlsdr",
                "scan_time": 5.0,
            })
            deadline = time.time() + 10
            while live_app_state.is_scanning and time.time() < deadline:
                time.sleep(0.2)

            args_wifi = mock_wifi.call_args
            assert args_wifi[0][0] == "wlan1"  # interface
            assert args_wifi[0][1] == 5.0      # timeout

            args_ble = mock_ble.call_args
            assert args_ble[0][0] == "hci1"

            args_sdr = mock_sdr.call_args
            assert args_sdr[0][0] == "driver=rtlsdr"


class TestTargetDetailEndpoint:
    def test_target_found(self, client, app_state):
        _inject_target(app_state, {
            "id": "wifi:TEST", "name": "Test", "protocol": "wifi",
        })
        resp = client.get("/api/targets/wifi:TEST")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Test"

    def test_target_not_found(self, client):
        resp = client.get("/api/targets/nonexistent")
        assert resp.status_code == 404


class TestActionEndpoint:
    """Action dispatch now uses the registry/executor path."""

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

    def test_action_requires_action_name(self, client, app_state):
        _inject_target(app_state, self.TELLO_TARGET)
        resp = client.post(
            f"/api/targets/{self.TELLO_TARGET['id']}/action",
            json={},
        )
        assert resp.status_code == 400

    def test_action_rejects_unavailable_action(self, client, app_state):
        _inject_target(app_state, {
            "id": "wifi:TEST", "name": "Test", "available_actions": ["land"],
            "chain": "wifi_tello", "details": {},
        })
        resp = client.post("/api/targets/wifi:TEST/action", json={"action": "nuke"})
        assert resp.status_code == 400
        assert "not available" in resp.get_json()["error"]

    def test_action_returns_result_state(self, client, app_state):
        """All modes now return Result State response from the executor."""
        _inject_target(app_state, self.TELLO_TARGET)
        _arm(client, self.TELLO_TARGET["id"], "wifi_tello", ["land"])
        resp = client.post(
            f"/api/targets/{self.TELLO_TARGET['id']}/action",
            json={"action": "land"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["result_state"] == "attempted"
        assert data["stop_reason"] == "completed"
        assert "transition_log" in data
        assert len(data["transition_log"]) > 0
        assert data["transition_log"][0]["state"] == "previewed"

    def test_action_returns_result_state_for_each_chain(self, client, app_state):
        """Every chain type resolves through the registry and returns Result State."""
        chain_targets = {
            "wifi_tello": self.TELLO_TARGET,
            "mavlink": {
                "id": "mav:1:1", "name": "MAVLink Quad", "protocol": "mavlink",
                "chain": "mavlink",
                "available_actions": ["sniff", "disarm", "land", "rtl", "reboot", "replay"],
                "details": {"source": "192.168.1.100:14550"},
            },
            "elrs": {
                "id": "elrs:A1:B2:C3", "name": "ELRS TX", "protocol": "elrs",
                "chain": "elrs",
                "available_actions": ["forge_disarm", "preview_fhss", "scan_deeper"],
                "details": {"binding_phrase": "iloveflying"},
            },
            "wifi_parrot": {
                "id": "wifi:A0:14:3D:11:22:33", "name": "AR.Drone",
                "protocol": "wifi", "chain": "wifi_parrot",
                "available_actions": ["land", "emergency_watchdog", "kill_control"],
                "details": {"ip": "192.168.1.1"},
            },
            "dji_ble": {
                "id": "ble:48:1C:B9:DE:AD:01", "name": "DJI-BLE",
                "protocol": "ble", "chain": "dji_ble",
                "available_actions": ["ble_scan_services"],
                "details": {"address": "48:1C:B9:DE:AD:01"},
            },
        }
        for chain, target in chain_targets.items():
            _inject_target(app_state, target)
            action = target["available_actions"][0]
            _arm(client, target["id"], chain, [action])
            resp = client.post(
                f"/api/targets/{target['id']}/action",
                json={"action": action},
            )
            assert resp.status_code == 200, f"Chain {chain} returned {resp.status_code}"
            data = resp.get_json()
            assert "result_state" in data, f"Chain {chain} missing result_state"
            assert data["result_state"] == "attempted"

    def test_action_appends_to_log_with_result_state(self, client, app_state):
        _inject_target(app_state, self.TELLO_TARGET)
        _arm(client, self.TELLO_TARGET["id"], "wifi_tello", ["land"])
        client.post(
            f"/api/targets/{self.TELLO_TARGET['id']}/action",
            json={"action": "land"},
        )
        with app_state.state_lock:
            assert len(app_state.action_log) == 1
            entry = app_state.action_log[0]
        assert entry["action"] == "land"
        assert "result_state" in entry

    def test_preview_stops_at_previewed(self, client, app_state):
        """Preview requests produce no state beyond PREVIEWED."""
        _inject_target(app_state, self.TELLO_TARGET)
        resp = client.post(
            f"/api/targets/{self.TELLO_TARGET['id']}/action",
            json={"action": "land", "preview": True},
        )
        data = resp.get_json()
        assert data["result_state"] == "previewed"


class TestClearEndpoint:
    def test_clear_removes_targets(self, client, app_state):
        _inject_target(app_state, {"id": "a"})
        resp = client.post("/api/clear")
        assert resp.status_code == 200
        assert len(app_state.targets) == 0

    def test_clear_pushes_event(self, client, app_state):
        client.post("/api/clear")
        with app_state.state_lock:
            event_types = [e["type"] for e in app_state.events]
        assert "targets_cleared" in event_types


class TestActionDefsEndpoint:
    """``/api/actions`` returns pack metadata from the registry."""

    def test_returns_pack_list(self, client):
        resp = client.get("/api/actions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_each_pack_has_identity_and_actions(self, client):
        resp = client.get("/api/actions")
        data = resp.get_json()
        for pack_meta in data:
            assert "name" in pack_meta
            assert "version" in pack_meta
            assert "description" in pack_meta
            assert "actions" in pack_meta
            assert isinstance(pack_meta["actions"], list)

    def test_packs_cover_all_chains(self, client):
        resp = client.get("/api/actions")
        data = resp.get_json()
        chains = {p["chain"] for p in data}
        for expected in ("wifi_tello", "wifi_parrot", "mavlink", "elrs", "dji_ble"):
            assert expected in chains, f"Missing pack for chain {expected}"


class TestLogEndpoint:
    def test_log_returns_recent_entries(self, client, app_state):
        with app_state.state_lock:
            app_state.action_log.extend([
                {"id": "a", "action": "land", "timestamp": "2026-01-01T00:00:00Z"},
                {"id": "b", "action": "rtl", "timestamp": "2026-01-01T00:00:01Z"},
            ])
        resp = client.get("/api/log")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 2


class TestHardwareEndpoint:
    def test_hardware_returns_json(self, client, app_state):
        # Simulation mode is on, so detect_hardware won't be called
        resp = client.get("/api/hardware")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["mode"] == "simulation"


# ---------------------------------------------------------------------------
# SSE (Server-Sent Events) tests
# ---------------------------------------------------------------------------

class TestSSEEvents:
    def test_push_event_appends_to_state(self, app_state):
        webapp._push_event(app_state, "test_event", {"key": "value"})
        with app_state.state_lock:
            assert len(app_state.events) == 1
            assert app_state.events[0]["type"] == "test_event"
            assert app_state.events[0]["data"] == {"key": "value"}
            assert "timestamp" in app_state.events[0]
            assert app_state.events[0]["id"] == 0

    def test_push_event_increments_id(self, app_state):
        webapp._push_event(app_state, "ev1", {})
        webapp._push_event(app_state, "ev2", {})
        with app_state.state_lock:
            assert app_state.events[0]["id"] == 0
            assert app_state.events[1]["id"] == 1

    def test_push_event_caps_at_max(self, app_state):
        for i in range(webapp.MAX_EVENTS + 50):
            webapp._push_event(app_state, "ev", {"i": i})
        with app_state.state_lock:
            assert len(app_state.events) <= webapp.MAX_EVENTS

    def test_events_endpoint_returns_sse_content_type(self, client, app_state):
        webapp._push_event(app_state, "test_ev", {"hello": "world"})
        resp = client.get("/api/events")
        assert resp.content_type.startswith("text/event-stream")

    def test_events_endpoint_streams_existing_events(self, client, app_state):
        webapp._push_event(app_state, "target_found", {"name": "TELLO-ABC"})
        webapp._push_event(app_state, "scan_complete", {"target_count": 1})

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

    def test_events_endpoint_respects_last_id(self, client, app_state):
        webapp._push_event(app_state, "old_event", {"a": 1})
        webapp._push_event(app_state, "new_event", {"b": 2})

        resp = client.get("/api/events?last_id=0")
        data = b""
        for chunk in resp.response:
            data += chunk if isinstance(chunk, bytes) else chunk.encode()
            if b"new_event" in data:
                break
        text = data.decode()
        assert "old_event" not in text
        assert "event: new_event" in text

    def test_simulated_scan_emits_events(self, client, app_state):
        client.post("/api/scan", json={})
        deadline = time.time() + 10
        while app_state.is_scanning and time.time() < deadline:
            time.sleep(0.2)

        with app_state.state_lock:
            event_types = [e["type"] for e in app_state.events]
        assert "scan_start" in event_types
        assert "scanner_done" in event_types
        assert "target_found" in event_types
        assert "scan_complete" in event_types

    def test_action_emits_result_state_transition_event(self, client, app_state):
        """Action execution emits result_state_transition SSE events."""
        _inject_target(app_state, {
            "id": "wifi:TEST", "name": "Test", "chain": "wifi_tello",
            "available_actions": ["land"],
            "details": {},
        })
        _arm(client, "wifi:TEST", "wifi_tello", ["land"])
        client.post("/api/targets/wifi:TEST/action", json={"action": "land"})
        with app_state.state_lock:
            event_types = [e["type"] for e in app_state.events]
        # Both the new result_state_transition and the legacy action_executed
        assert "result_state_transition" in event_types
        assert "action_executed" in event_types

    def test_result_state_event_has_required_fields(self, client, app_state):
        _inject_target(app_state, {
            "id": "wifi:TEST", "name": "Test", "chain": "wifi_tello",
            "available_actions": ["land"],
            "details": {},
        })
        _arm(client, "wifi:TEST", "wifi_tello", ["land"])
        client.post("/api/targets/wifi:TEST/action", json={"action": "land"})
        with app_state.state_lock:
            rs_events = [
                e for e in app_state.events
                if e["type"] == "result_state_transition"
            ]
        assert len(rs_events) > 0
        ev = rs_events[0]["data"]
        assert "state" in ev
        assert "timestamp" in ev
        assert "target_id" in ev
        assert "action" in ev
        assert "pack_name" in ev


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
        state = webapp.create_dashboard_state(simulate=False, dry_run=False)
        calls = []
        def fake_wifi(iface, timeout, logger, abort):
            calls.append(("wifi", iface, timeout))
            return []

        with patch("exploit_chains.multi_recon.scan_wifi", fake_wifi), \
             patch("exploit_chains.multi_recon.scan_ble", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_sdr", MagicMock(return_value=[])):
            webapp.run_scan_live(
                state,
                ["wifi"],
                {"wifi_iface": "wlan0", "ble_iface": "hci0",
                 "sdr_args": "driver=uhd", "scan_time": 10.0},
            )
        assert len(calls) == 1
        assert calls[0] == ("wifi", "wlan0", 10.0)

    def test_run_scan_live_mavlink_scanner(self):
        """MAVLink UDP scanner should be invoked for the 'mavlink' protocol."""
        state = webapp.create_dashboard_state(simulate=False, dry_run=False)
        with patch("web.app._scan_mavlink_udp", return_value=[]) as mock_mav, \
             patch("exploit_chains.multi_recon.scan_wifi", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_ble", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_sdr", MagicMock(return_value=[])):
            webapp.run_scan_live(
                state,
                ["mavlink"],
                {"wifi_iface": "wlan0", "ble_iface": "hci0",
                 "sdr_args": "driver=uhd", "scan_time": 10.0},
            )
        mock_mav.assert_called_once()

    def test_run_scan_live_telemetry_scanner(self):
        """Passive telemetry scanner should be invoked for the 'telemetry' protocol."""
        state = webapp.create_dashboard_state(simulate=False, dry_run=False)
        with patch("web.app._scan_passive_telemetry", return_value=[]) as mock_telem, \
             patch("exploit_chains.multi_recon.scan_wifi", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_ble", MagicMock(return_value=[])), \
             patch("exploit_chains.multi_recon.scan_sdr", MagicMock(return_value=[])):
            webapp.run_scan_live(
                state,
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
# Registry/executor dispatch integration
# ---------------------------------------------------------------------------

class TestRegistryDispatchIntegration:
    """End-to-end: target -> registry -> executor -> Result State -> SSE."""

    def test_registry_has_packs_for_all_chains(self, app_state):
        """The registry built at app creation covers every chain."""
        registry = app_state.pack_registry
        assert registry is not None
        packs = registry.query({})
        chains = set()
        for p in packs:
            if isinstance(p, webapp.SimulationPack):
                chains.add(p.chain)
        for expected in ("wifi_tello", "wifi_parrot", "mavlink", "elrs", "dji_ble"):
            assert expected in chains

    def test_execute_via_registry_returns_result_state(self, app_state):
        """_execute_via_registry produces a Result State response once armed."""
        from datetime import timedelta

        target = {
            "id": "wifi:test", "name": "Test",
            "chain": "wifi_tello", "details": {},
        }
        # Authorization is always enforced; arm a scope against this target.
        app_state.scope_manager.arm(
            pack_name="sim-wifi_tello",
            pack_version="0.1.0",
            target=webapp.TargetDescriptor(
                identity="wifi:test", protocol_version="*",
                firmware_impl="*", firmware_version="*",
            ),
            permitted_actions=frozenset(["land"]),
            bounds={},
            time_limit=timedelta(seconds=300),
            stop_conditions=["operator_abort"],
            sacrificial_target=True,
            evidence_destination="test",
        )
        result = webapp._execute_via_registry(
            app_state, app_state.pack_registry, target, "land",
        )
        assert result["result_state"] == "attempted"
        assert result["stop_reason"] == "completed"
        assert len(result["transition_log"]) > 0

    def test_execute_via_registry_denies_unarmed(self, app_state):
        """Without an armed scope, execution is denied before any side effect."""
        target = {
            "id": "wifi:unarmed", "name": "Test",
            "chain": "wifi_tello", "details": {},
        }
        result = webapp._execute_via_registry(
            app_state, app_state.pack_registry, target, "land",
        )
        assert result["result_state"] is None
        assert result["stop_reason"] == "authorization_denied"
        assert result["transition_log"] == []

    def test_preview_flag_produces_previewed_only(self, app_state):
        target = {
            "id": "wifi:test", "name": "Test",
            "chain": "wifi_tello", "details": {},
        }
        result = webapp._execute_via_registry(
            app_state, app_state.pack_registry, target, "land",
            preview_only=True,
        )
        assert result["result_state"] == "previewed"

    def test_unknown_chain_returns_error(self, app_state):
        target = {
            "id": "unknown:x", "name": "Unknown",
            "chain": "nonexistent", "details": {},
        }
        result = webapp._execute_via_registry(
            app_state, app_state.pack_registry, target, "land",
        )
        assert result["result_state"] is None
        assert "no_pack" in result["stop_reason"]


# ---------------------------------------------------------------------------
# Chain 2 unified dispatch
# ---------------------------------------------------------------------------

class TestChain2UnifiedDispatch:
    """Chain 2 endpoints use the same registry/executor interface."""

    def test_chain2_pack_registered(self, app_state):
        """A pack for chain2_wifi_mavlink exists in the registry."""
        registry = app_state.pack_registry
        packs = registry.query({})
        chain2_packs = [
            p for p in packs
            if isinstance(p, webapp.SimulationPack) and p.chain == "chain2_wifi_mavlink"
        ]
        assert len(chain2_packs) == 1

    def test_chain2_simulate_still_works(self, client, app_state):
        """Chain 2 simulate endpoint remains functional."""
        resp = client.post(
            "/api/chain2/simulate",
            json={"commands": ["land"]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "simulated"

    def test_chain2_inject_still_works(self, client, app_state):
        """Chain 2 inject endpoint remains functional in dry-run mode."""
        resp = client.post(
            "/api/chain2/inject",
            json={"command": "land", "target_id": "t1"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "dry_run"
        # Dry-run builds the packet but transmits nothing: PREVIEWED only.
        assert data["result_state"] == "previewed"

    def test_chain2_inject_dry_run_records_evidence(self, client, app_state):
        """Every injection attempt produces an append-only evidence record."""
        client.post(
            "/api/chain2/inject",
            json={"command": "land", "target_id": "t1"},
        )
        records = client.get("/api/evidence").get_json()
        assert len(records) == 1
        assert records[0]["stop_reason"] == "dry_run"

    def test_chain2_live_inject_denied_without_scope(self, live_client, live_app_state):
        """A live (non-dry-run) injection requires an armed scope first."""
        resp = live_client.post(
            "/api/chain2/inject",
            json={
                "command": "land",
                "target_id": "mav:1:1",
                "endpoint": "192.0.2.1:14550",
            },
        )
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["status"] == "authorization_denied"
        # No transmission occurred; the packet was built but not sent.
        assert "packet_hex" in data

    @staticmethod
    def _arm_chain2(client, target_id, *, endpoint, max_packets):
        return client.post(
            "/api/packs/sim-chain2_wifi_mavlink/0.1.0/arm",
            json={
                "target_identity": target_id,
                "permitted_actions": ["land", "disarm"],
                "time_limit_seconds": 300,
                "stop_conditions": ["operator_abort"],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "bounds": {"endpoint": endpoint, "max_packets": max_packets},
            },
        )

    def test_chain2_live_rejects_out_of_bound_endpoint(self, live_client, live_app_state):
        """An armed scope bound to endpoint A must not transmit to endpoint B."""
        _inject_target(live_app_state, {
            "id": "mav:bound", "name": "MAV", "protocol": "mavlink",
            "chain": "chain2_wifi_mavlink", "available_actions": ["land"],
            "details": {},
        })
        armed = self._arm_chain2(
            live_client, "mav:bound", endpoint="127.0.0.1:14550", max_packets=1,
        )
        assert armed.status_code == 200
        with patch("socket.socket") as socket_cls:
            resp = live_client.post("/api/chain2/inject", json={
                "command": "land", "target_id": "mav:bound",
                "endpoint": "203.0.113.99:9999",
            })
        assert resp.status_code == 403
        assert resp.get_json()["status"] == "bounds_violation"
        socket_cls.assert_not_called()

    def test_chain2_live_within_bounds_caps_packet_count(self, live_client, live_app_state):
        """Within bounds, the burst is capped at the scope's max_packets."""
        _inject_target(live_app_state, {
            "id": "mav:bound", "name": "MAV", "protocol": "mavlink",
            "chain": "chain2_wifi_mavlink", "available_actions": ["land"],
            "details": {},
        })
        self._arm_chain2(
            live_client, "mav:bound", endpoint="127.0.0.1:14550", max_packets=1,
        )
        with patch("socket.socket") as socket_cls:
            sock = socket_cls.return_value
            resp = live_client.post("/api/chain2/inject", json={
                "command": "land", "target_id": "mav:bound",
                "endpoint": "127.0.0.1:14550",
            })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "sent"
        # max_packets=1 bound → exactly one datagram, not the default burst of 5.
        assert sock.sendto.call_count == 1
        assert data["packets_sent"] == 1


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------

class TestIndexPage:
    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"DRONE SCANNER" in resp.data
