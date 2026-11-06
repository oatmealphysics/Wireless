"""Focused tests for web dashboard hardening.

Covers: hostile SSIDs (XSS), action validation, scanner failure cleanup,
score-to-threat-level labels, dry-run gating, and action categories.
"""

import os
import re
import sys
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..")
WEB_DIR = os.path.join(TOOLS_DIR, "..", "web")
sys.path.insert(0, TOOLS_DIR)
sys.path.insert(0, os.path.join(TOOLS_DIR, "exploit_chains"))
sys.path.insert(0, WEB_DIR)

try:
    import flask  # noqa: F401 -- availability gate for the optional web suite
except ImportError:
    HAS_FLASK = False
    web_app = None
else:
    import app as web_app
    HAS_FLASK = True

needs_flask = unittest.skipUnless(HAS_FLASK, "Flask not installed")


def _state():
    return web_app.get_dashboard_state(web_app.app)


def _reset_default_state():
    """Restore the singleton only for tests that exercise import compatibility."""
    state = _state()
    state.reset_runtime()
    with state.state_lock:
        state.simulate_mode = True
        state.dry_run_mode = True
        state.scan_defaults = web_app._new_scan_defaults()
    return state


@needs_flask
class TestHostileSSIDs(unittest.TestCase):
    """Verify that hostile target names/SSIDs cannot inject script."""

    def setUp(self):
        _reset_default_state()
        web_app.app.testing = True
        self.client = web_app.app.test_client()

    HOSTILE_NAMES = [
        "<script>alert(1)</script>",
        "DRONE'><img src=x onerror=alert(1)>",
        "SSID'); alert('xss",
        'Name"onmouseover="alert(1)',
        "<svg/onload=alert(1)>",
        "DRONE-{{7*7}}",
        "${alert(1)}",
    ]

    def test_hostile_names_preserved_in_json_api(self):
        """API returns hostile names verbatim in JSON (XSS protection is frontend-side)."""
        now = time.time()
        for i, name in enumerate(self.HOSTILE_NAMES):
            _state().targets[f"test:{i}"] = {
                "id": f"test:{i}",
                "protocol": "wifi",
                "name": name,
                "type": "Test",
                "drone_type": "unknown",
                "signal": -50,
                "encryption": "Open",
                "threat_level": "LOW",
                "vulns": [],
                "details": {},
                "available_actions": [],
                "chain": "unknown",
                "first_seen": now,
                "last_seen": now,
                "score": 30,
            }

        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/json")
        data = resp.get_json()
        names = {t["name"] for t in data["targets"]}
        for name in self.HOSTILE_NAMES:
            self.assertIn(name, names)

    def test_hostile_id_in_action_endpoint(self):
        """Action endpoint handles hostile target IDs safely."""
        hostile_id = "wifi:<script>alert(1)</script>"
        resp = self.client.post(
            f"/api/targets/{hostile_id}/action",
            json={"action": "land"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_hostile_ssid_in_target_detail(self):
        """Target detail API handles hostile names."""
        hostile_id = "wifi:AA:BB:CC"
        _state().targets[hostile_id] = {
            "id": hostile_id,
            "name": "<img src=x onerror=alert(1)>",
            "protocol": "wifi",
            "type": "Test",
            "drone_type": "tello",
            "signal": -50,
            "encryption": "Open",
            "threat_level": "HIGH",
            "vulns": ["OPEN_WIFI"],
            "details": {"ssid": "<script>alert(1)</script>"},
            "available_actions": ["land"],
            "chain": "wifi_tello",
            "first_seen": time.time(),
            "last_seen": time.time(),
            "score": 95,
        }

        resp = self.client.get(f"/api/targets/{hostile_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["name"], "<img src=x onerror=alert(1)>")


@needs_flask
class TestActionValidation(unittest.TestCase):
    """Verify action validation and dry-run gating."""

    def setUp(self):
        _reset_default_state()
        web_app.app.testing = True
        self.client = web_app.app.test_client()

        self.target = {
            "id": "wifi:test",
            "name": "TestDrone",
            "protocol": "wifi",
            "type": "Test",
            "drone_type": "tello",
            "signal": -50,
            "encryption": "Open",
            "threat_level": "HIGH",
            "vulns": ["OPEN_WIFI"],
            "details": {},
            "available_actions": ["land", "streamon"],
            "chain": "wifi_tello",
            "first_seen": time.time(),
            "last_seen": time.time(),
            "score": 95,
        }
        _state().targets["wifi:test"] = self.target

    def test_missing_action_rejected(self):
        resp = self.client.post(
            "/api/targets/wifi:test/action",
            json={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_unavailable_action_rejected(self):
        resp = self.client.post(
            "/api/targets/wifi:test/action",
            json={"action": "reboot"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not available", resp.get_json()["error"])

    def _arm_land(self):
        """Arm the wifi_tello pack for wifi:test so 'land' is authorized."""
        return self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:test",
                "permitted_actions": ["land", "streamon"],
                "time_limit_seconds": 300,
                "stop_conditions": ["operator_abort"],
                "sacrificial_target": True,
                "evidence_destination": "test",
            },
            content_type="application/json",
        )

    def test_valid_action_armed_returns_result_state(self):
        self._arm_land()
        resp = self.client.post(
            "/api/targets/wifi:test/action",
            json={"action": "land"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        # New contract: the executor reports a truthful Result State, never a
        # flat "status"/"result" string.
        self.assertEqual(data["result_state"], "attempted")
        self.assertEqual(data["stop_reason"], "completed")

    def test_dry_run_mode_performs_no_live_execution(self):
        """In non-simulate dry-run mode an armed action still only simulates.

        Dry-run governs whether real I/O occurs; execution always runs through
        the fake executor here, so no real transmission happens and the Result
        State never exceeds ATTEMPTED.
        """
        _state().simulate_mode = False
        _state().dry_run_mode = True
        self._arm_land()

        resp = self.client.post(
            "/api/targets/wifi:test/action",
            json={"action": "land"},
            content_type="application/json",
        )
        data = resp.get_json()
        self.assertEqual(data["result_state"], "attempted")
        self.assertNotIn(
            data["result_state"], ("target_accepted", "effect_confirmed"),
        )

    def test_unarmed_action_denied(self):
        """Without an armed scope, execution is denied (403)."""
        resp = self.client.post(
            "/api/targets/wifi:test/action",
            json={"action": "land"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()["stop_reason"], "authorization_denied")

    def test_nonexistent_target_404(self):
        resp = self.client.post(
            "/api/targets/wifi:nonexistent/action",
            json={"action": "land"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


@needs_flask
class TestActionCategories(unittest.TestCase):
    """Verify action definitions include category field."""

    def setUp(self):
        _reset_default_state()

    def test_all_actions_have_category(self):
        for name, defn in web_app.ACTION_DEFS.items():
            self.assertIn("category", defn, f"Action '{name}' missing category")
            self.assertIn(
                defn["category"],
                ("simulated", "passive", "active"),
                f"Action '{name}' has invalid category '{defn['category']}'",
            )

    def test_passive_actions_are_info_severity(self):
        """Passive and simulated actions should not be danger severity."""
        for name, defn in web_app.ACTION_DEFS.items():
            if defn["category"] == "passive":
                self.assertIn(
                    defn["severity"],
                    ("info", "warning"),
                    f"Passive action '{name}' has unexpected severity '{defn['severity']}'",
                )

    def test_api_exposes_categories(self):
        web_app.app.testing = True
        client = web_app.app.test_client()
        resp = client.get("/api/actions")
        data = resp.get_json()
        # New contract: /api/actions returns a list of pack metadata, each with
        # an "actions" list whose entries carry a category.
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        for pack in data:
            for action in pack.get("actions", []):
                self.assertIn("category", action)


@needs_flask
class TestScannerFailureCleanup(unittest.TestCase):
    """Verify that scanner exceptions reset is_scanning."""

    def setUp(self):
        _reset_default_state()

    def test_simulated_scan_resets_on_exception(self):
        """is_scanning resets even if run_scan_simulated raises."""
        with patch.object(web_app, "_push_event"):
            with patch("time.sleep", side_effect=RuntimeError("test")):
                with self.assertRaises(RuntimeError):
                    web_app.run_scan_simulated(_state())

        self.assertFalse(_state().is_scanning)
        self.assertEqual(_state().scanner_status["wifi"], "error")
        self.assertEqual(_state().scanner_status["ble"], "error")
        self.assertEqual(_state().scanner_status["sdr"], "error")

    def test_live_scan_resets_on_exception(self):
        """is_scanning resets even if run_scan_live raises."""
        with patch("time.sleep"):
            with patch.object(web_app, "_push_event", side_effect=RuntimeError("test")):
                with self.assertRaises(RuntimeError):
                    web_app.run_scan_live(
                        _state(), ["wifi"], {"wifi_iface": "wlan0"},
                    )

        self.assertFalse(_state().is_scanning)

    def test_scan_api_rejects_concurrent(self):
        """Cannot start scan while one is running."""
        web_app.app.testing = True
        client = web_app.app.test_client()
        with patch.object(web_app.threading, "Thread"):
            first = client.post(
                "/api/scan", json={}, content_type="application/json",
            )
            second = client.post(
                "/api/scan", json={}, content_type="application/json",
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        web_app._finish_scan(_state(), _state().active_scan_generation)

    def test_worker_start_failure_releases_scan_slot(self):
        _state().is_scanning = False
        web_app.app.testing = True
        client = web_app.app.test_client()

        with patch.object(web_app.threading.Thread, "start", side_effect=RuntimeError("test")):
            resp = client.post("/api/scan", json={}, content_type="application/json")

        self.assertEqual(resp.status_code, 500)
        with _state().state_lock:
            self.assertFalse(_state().is_scanning)
            self.assertIsNone(_state().active_scan_generation)
            self.assertIsNone(_state().active_scan_thread)

        with patch.object(web_app.threading, "Thread") as thread_cls:
            retry = client.post(
                "/api/scan", json={}, content_type="application/json",
            )
        self.assertEqual(retry.status_code, 200)
        thread_cls.return_value.start.assert_called_once_with()
        web_app._finish_scan(_state(), _state().active_scan_generation)


@needs_flask
class TestScanRequestValidation(unittest.TestCase):
    def setUp(self):
        _reset_default_state()
        web_app.app.testing = True
        self.client = web_app.app.test_client()

    def test_rejects_non_object_json_without_reserving_slot(self):
        resp = self.client.post("/api/scan", json=["wifi"])
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(_state().is_scanning)

    def test_rejects_malformed_json_without_reserving_slot(self):
        resp = self.client.post(
            "/api/scan", data="{", content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(_state().is_scanning)

    def test_rejects_unknown_protocol(self):
        resp = self.client.post("/api/scan", json={"protocols": ["wifi", "invalid"]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("invalid", resp.get_json()["error"])

    def test_rejects_invalid_scan_durations(self):
        for value in (True, 0, -1, 301, "15", float("inf"), 10 ** 400):
            with self.subTest(value=value):
                resp = self.client.post("/api/scan", json={"scan_time": value})
                self.assertEqual(resp.status_code, 400)
                self.assertFalse(_state().is_scanning)

    def test_scan_admission_is_reserved_before_worker_start(self):
        with patch.object(web_app.threading, "Thread") as thread_cls:
            first = self.client.post("/api/scan", json={})
            second = self.client.post("/api/scan", json={})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        thread_cls.assert_called_once()
        web_app._finish_scan(_state(), _state().active_scan_generation)

    def test_live_worker_receives_requested_config(self):
        _state().simulate_mode = False
        try:
            with patch.object(web_app.threading, "Thread") as thread_cls:
                resp = self.client.post("/api/scan", json={
                    "protocols": ["wifi"],
                    "wifi_iface": "test-wifi",
                    "ble_iface": "test-ble",
                    "sdr_args": "driver=test",
                    "scan_time": 2,
                })

            self.assertEqual(resp.status_code, 200)
            worker_args = thread_cls.call_args.kwargs["args"]
            self.assertIs(worker_args[0], _state())
            self.assertIs(worker_args[2], web_app.run_scan_live)
            scan_args = worker_args[3]
            self.assertEqual(scan_args[0], ["wifi"])
            self.assertEqual(scan_args[1]["wifi_iface"], "test-wifi")
            self.assertEqual(scan_args[1]["ble_iface"], "test-ble")
            self.assertEqual(scan_args[1]["sdr_args"], "driver=test")
            self.assertEqual(scan_args[1]["scan_time"], 2.0)
        finally:
            web_app._finish_scan(_state(), _state().active_scan_generation)
            _state().simulate_mode = True

    def test_clear_rejected_during_scan(self):
        with patch.object(web_app.threading, "Thread"):
            started = self.client.post("/api/scan", json={})
            resp = self.client.post("/api/clear")
        self.assertEqual(started.status_code, 200)
        self.assertEqual(resp.status_code, 409)
        web_app._finish_scan(_state(), _state().active_scan_generation)


@needs_flask
class TestScoreAndThreatLabels(unittest.TestCase):
    """Verify threat labels derive from calculated score."""

    def setUp(self):
        _reset_default_state()

    def test_score_to_threat_mapping(self):
        cases = [
            (95, "CRITICAL"),
            (80, "CRITICAL"),
            (79, "HIGH"),
            (60, "HIGH"),
            (59, "MEDIUM"),
            (40, "MEDIUM"),
            (39, "LOW"),
            (0, "LOW"),
        ]
        for score, expected in cases:
            result = web_app._threat_from_score({"score": score})
            self.assertEqual(result, expected, f"score={score}")

    def test_simulated_targets_get_derived_threat(self):
        """After simulated scan, threat_level matches score, not hardcoded value."""
        _state().simulate_mode = True
        _state().targets = {}

        with patch("time.sleep"):
            web_app.run_scan_simulated(_state())

        for tid, target in _state().targets.items():
            expected = web_app._threat_from_score(target)
            self.assertEqual(
                target["threat_level"],
                expected,
                f"Target {tid}: threat_level={target['threat_level']} "
                f"but score={target['score']} should give {expected}",
            )

    def test_simulated_scan_respects_requested_protocols(self):
        _state().targets = {}
        with patch("time.sleep"):
            web_app.run_scan_simulated(_state(), ["wifi"])

        self.assertTrue(_state().targets)
        self.assertEqual(
            {target["protocol"] for target in _state().targets.values()},
            {"wifi"},
        )

    def test_score_tello_is_high(self):
        score = web_app._score_target({"drone_type": "tello"})
        self.assertGreaterEqual(score, 80)

    def test_score_unknown_is_low(self):
        score = web_app._score_target({"drone_type": "unknown"})
        self.assertLess(score, 40)

    def test_live_target_label_is_derived_from_computed_score(self):
        target = web_app._convert_recon_target(
            {"protocol": "wifi", "drone_type": "tello", "is_open": True},
            "wifi:test", time.time(),
        )
        self.assertEqual(target["score"], 100)
        self.assertEqual(target["threat_level"], "CRITICAL")

    def test_live_wifi_chain_matches_detected_family(self):
        parrot = web_app._convert_recon_target(
            {"protocol": "wifi", "drone_type": "parrot_ar"},
            "wifi:parrot", time.time(),
        )
        unknown = web_app._convert_recon_target(
            {"protocol": "wifi", "drone_type": "unknown"},
            "wifi:unknown", time.time(),
        )
        self.assertEqual(parrot["chain"], "wifi_parrot")
        self.assertIn("emergency_watchdog", parrot["available_actions"])
        self.assertEqual(unknown["chain"], "unknown")
        self.assertEqual(unknown["available_actions"], [])


@needs_flask
class TestEventRetention(unittest.TestCase):
    def setUp(self):
        _reset_default_state()
        web_app.app.testing = True
        self.client = web_app.app.test_client()
        self.first_event_id = _state().next_event_id

    def test_event_ids_remain_monotonic_after_pruning(self):
        for index in range(web_app.MAX_EVENTS + 3):
            web_app._push_event(_state(), "test", {"index": index})

        ids = [event["id"] for event in _state().events]
        self.assertEqual(len(ids), web_app.MAX_EVENTS)
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(
            ids[-1], self.first_event_id + web_app.MAX_EVENTS + 2,
        )

        previous = ids[-1]
        web_app._push_event(_state(), "test", {"index": "next"})
        self.assertEqual(_state().events[-1]["id"], previous + 1)

    def test_fresh_stream_starts_at_current_tail(self):
        web_app._push_event(_state(), "stale", {"value": "old"})
        with web_app.app.test_request_context("/api/events"):
            response = web_app.api_events()
            try:
                web_app._push_event(_state(), "fresh", {"value": "new"})
                stream = iter(response.response)
                self.assertEqual(next(stream), ": connected\n\n")
                chunk = next(stream)
            finally:
                response.close()

        self.assertIn("event: fresh", chunk)
        self.assertIn('"value": "new"', chunk)
        self.assertNotIn("event: stale", chunk)

    def test_reconnect_cursor_replays_only_newer_events(self):
        for index in range(3):
            web_app._push_event(_state(), "test", {"index": index})

        with web_app.app.test_request_context(
                "/api/events",
                headers={"Last-Event-ID": str(self.first_event_id)}):
            response = web_app.api_events()
            try:
                stream = iter(response.response)
                self.assertEqual(next(stream), ": connected\n\n")
                chunk = next(stream)
            finally:
                response.close()

        self.assertIn(f"id: {self.first_event_id + 1}", chunk)
        self.assertIn('"index": 1', chunk)
        self.assertNotIn('"index": 0', chunk)

    def test_cursor_ahead_after_restart_resets_to_tail(self):
        web_app._push_event(_state(), "before_restart", {})
        stale_cursor = _state().next_event_id + web_app.MAX_EVENTS
        with web_app.app.test_request_context(
                "/api/events", headers={"Last-Event-ID": str(stale_cursor)}):
            response = web_app.api_events()
            try:
                web_app._push_event(_state(), "after_restart", {"ok": True})
                stream = iter(response.response)
                self.assertEqual(next(stream), ": connected\n\n")
                chunk = next(stream)
            finally:
                response.close()

        self.assertIn("event: after_restart", chunk)
        self.assertIn('"ok": true', chunk)


class TestLocalhostBinding(unittest.TestCase):
    """Verify default host is localhost."""

    def test_default_host_is_localhost(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--host", default="127.0.0.1")
        args = parser.parse_args([])
        self.assertEqual(args.host, "127.0.0.1")

    def test_app_argparse_default(self):
        """The app.py main() default host is 127.0.0.1, not 0.0.0.0."""
        app_path = os.path.join(WEB_DIR, "app.py")
        with open(app_path) as f:
            source = f.read()
        self.assertIn('default="127.0.0.1"', source)
        self.assertNotIn('default="0.0.0.0"', source)


@needs_flask
class TestDryRunDefault(unittest.TestCase):
    """Verify dry-run is the default mode."""

    def setUp(self):
        _reset_default_state()

    def test_dry_run_mode_default_true(self):
        """The default application state starts in dry-run mode."""
        self.assertTrue(web_app.create_dashboard_state().dry_run_mode)

    def test_status_api_reports_dry_run(self):
        _state().dry_run_mode = True
        _state().simulate_mode = False
        web_app.app.testing = True
        client = web_app.app.test_client()
        resp = client.get("/api/status")
        data = resp.get_json()
        self.assertTrue(data["dry_run"])

    def test_live_actions_flag_is_removed(self):
        """The global --live-actions gate is gone; the flag must be rejected."""
        with patch.object(sys, "argv", ["app.py", "--simulate", "--live-actions"]):
            with self.assertRaises(SystemExit):
                web_app.main()

    def test_simulate_run_keeps_dry_run_and_sets_defaults(self):
        """--simulate wires scan defaults and keeps dry-run on (no live gate)."""
        original_simulate = _state().simulate_mode
        original_dry_run = _state().dry_run_mode
        original_defaults = dict(_state().scan_defaults)
        try:
            with patch.object(sys, "argv", [
                    "app.py", "--simulate",
                    "--wifi-iface", "test-wifi", "--ble-iface", "test-ble",
                    "--sdr-args", "driver=test"]), patch.object(web_app.app, "run") as run:
                web_app.main()

            # Authorization is per-pack scoping now; dry-run stays enabled.
            self.assertTrue(_state().dry_run_mode)
            self.assertEqual(_state().scan_defaults["wifi_iface"], "test-wifi")
            self.assertEqual(_state().scan_defaults["ble_iface"], "test-ble")
            self.assertEqual(_state().scan_defaults["sdr_args"], "driver=test")
            run.assert_called_once()
        finally:
            _state().simulate_mode = original_simulate
            _state().dry_run_mode = original_dry_run
            _state().scan_defaults.clear()
            _state().scan_defaults.update(original_defaults)

    def test_chain2_inject_respects_dry_run_mode(self):
        original_simulate = _state().simulate_mode
        original_dry_run = _state().dry_run_mode
        original_state = deepcopy(_state().chain2_state)
        original_log = list(_state().action_log)
        try:
            _state().simulate_mode = False
            _state().dry_run_mode = True
            web_app.app.testing = True
            client = web_app.app.test_client()

            with patch("socket.socket") as socket_cls:
                resp = client.post("/api/chain2/inject", json={
                    "target_id": "test",
                    "command": "land",
                    "endpoint": "127.0.0.1:14550",
                })

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()["status"], "dry_run")
            socket_cls.assert_not_called()
        finally:
            _state().simulate_mode = original_simulate
            _state().dry_run_mode = original_dry_run
            _state().chain2_state = original_state
            _state().action_log = original_log


class TestFrontendXSSMitigations(unittest.TestCase):
    """Verify the template and versioned assets retain frontend safeguards."""

    @classmethod
    def setUpClass(cls):
        template_path = os.path.join(WEB_DIR, "templates", "index.html")
        script_path = os.path.join(WEB_DIR, "static", "dashboard.v2.js")
        stylesheet_path = os.path.join(WEB_DIR, "static", "dashboard.v2.css")
        with open(template_path, encoding="utf-8") as f:
            cls.html = f.read()
        with open(script_path, encoding="utf-8") as f:
            cls.javascript = f.read()
        with open(stylesheet_path, encoding="utf-8") as f:
            cls.css = f.read()

    def test_no_inline_onclick_handlers(self):
        """No inline onclick= attributes in the template."""
        onclick_matches = re.findall(r'onclick="[^"]*"', self.html)
        self.assertEqual(
            onclick_matches, [],
            f"Found inline onclick handlers: {onclick_matches}",
        )

    def test_eschtml_escapes_single_quotes(self):
        """escHtml covers both text and quoted attribute delimiters."""
        self.assertIn("&#39;", self.javascript)
        self.assertIn("&quot;", self.javascript)
        self.assertIn("function escAttr", self.javascript)

    def test_safe_css_class_function_exists(self):
        """safeCssClass function strips non-alphanumeric chars."""
        self.assertIn("safeCssClass", self.javascript)
        self.assertIn("[^a-zA-Z0-9_-]", self.javascript)

    def test_threat_level_uses_safe_css(self):
        """Threat level class interpolation uses safeCssClass."""
        self.assertIn("safeCssClass(t.threat_level || 'LOW')", self.javascript)

    def test_action_category_badges_present(self):
        """Action cards show category badges."""
        self.assertIn("action-category", self.javascript)
        self.assertIn("cat-simulated", self.css)
        self.assertIn("cat-passive", self.css)
        self.assertIn("cat-active", self.css)

    def test_data_attributes_for_event_delegation(self):
        """Target items use data attributes instead of inline handlers."""
        self.assertIn("data-target-id", self.javascript)
        self.assertIn("data-action-name", self.javascript)
        self.assertIn("data-action-target", self.javascript)

    def test_interactive_cards_are_buttons(self):
        self.assertIn('<button type="button" class="target-item', self.javascript)
        self.assertIn('<button type="button" class="action-card', self.javascript)

    def test_scan_requests_use_checked_json_helper(self):
        self.assertIn("async function fetchJson", self.javascript)
        self.assertIn("if (!response.ok)", self.javascript)
        self.assertNotIn("setInterval(refreshStatus", self.javascript)
        self.assertIn("data === null || data.scanning", self.javascript)

    def test_dynamic_numbers_are_normalized(self):
        self.assertIn("Number.isFinite(rawScore)", self.javascript)
        self.assertIn("Number.isFinite(value)", self.javascript)
        self.assertIn("dbm == null || dbm === ''", self.javascript)
        self.assertIn("t.signal == null || t.signal === ''", self.javascript)

    def test_live_regions_and_reduced_motion_present(self):
        self.assertIn('role="log" aria-live="polite"', self.html)
        self.assertIn('aria-busy="true"', self.html)
        self.assertIn(
            'id="action-result" role="status" aria-live="polite"',
            self.javascript,
        )
        self.assertIn("prefers-reduced-motion", self.css)

    def test_cross_tab_events_are_coalesced_and_refresh_logs(self):
        self.assertIn("function queueStatusRefresh", self.javascript)
        self.assertIn("'scan_start'", self.javascript)
        self.assertIn("'targets_cleared'", self.javascript)
        self.assertIn("refreshActionLog(selectedTargetId)", self.javascript)

    def test_target_keyboard_navigation_and_focus_restoration(self):
        self.assertIn("handleTargetListKeydown", self.javascript)
        self.assertIn("'ArrowDown'", self.javascript)
        self.assertIn('aria-current="${selected ?', self.javascript)
        self.assertIn("focusFallback?.focus({preventScroll: true})", self.javascript)
        self.assertIn("document.getElementById('btn-scan')?.focus", self.javascript)

    def test_layout_uses_dynamic_viewport_and_wrapping(self):
        self.assertIn("height: 100dvh", self.css)
        self.assertIn("flex-wrap: wrap", self.css)
        self.assertIn("grid-template-columns: minmax(90px, 35%)", self.css)

    def test_versioned_assets_replace_inline_code_and_styles(self):
        self.assertIn("dashboard.v2.css", self.html)
        self.assertIn("dashboard.v2.js", self.html)
        self.assertNotIn("<style", self.html.lower())
        self.assertIsNone(re.search(r"<script(?![^>]+\bsrc=)", self.html))
        self.assertIsNone(re.search(r"\sstyle\s*=", self.html))
        self.assertIsNone(re.search(r"\sstyle\s*=", self.javascript))
        non_resize = [
            line for line in self.javascript.splitlines()
            if ".style" in line and "maxHeight" not in line
        ]
        for line in non_resize:
            self.assertIsNone(re.search(r"\.style(?:\.|\[)", line))


if __name__ == "__main__":
    unittest.main()
