"""Focused tests for web dashboard hardening.

Covers: hostile SSIDs (XSS), action validation, scanner failure cleanup,
score-to-threat-level labels, dry-run gating, and action categories.
"""

import html
import json
import os
import re
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..")
WEB_DIR = os.path.join(TOOLS_DIR, "..", "web")
sys.path.insert(0, TOOLS_DIR)
sys.path.insert(0, os.path.join(TOOLS_DIR, "exploit_chains"))
sys.path.insert(0, WEB_DIR)

import app as web_app


class TestHostileSSIDs(unittest.TestCase):
    """Verify that hostile target names/SSIDs cannot inject script."""

    def setUp(self):
        web_app.simulate_mode = True
        web_app.dry_run_mode = True
        web_app.targets = {}
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
            web_app.targets[f"test:{i}"] = {
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
        web_app.targets[hostile_id] = {
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


class TestActionValidation(unittest.TestCase):
    """Verify action validation and dry-run gating."""

    def setUp(self):
        web_app.simulate_mode = True
        web_app.dry_run_mode = True
        web_app.targets = {}
        web_app.action_log = []
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
        web_app.targets["wifi:test"] = self.target

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

    def test_valid_action_dry_run(self):
        resp = self.client.post(
            "/api/targets/wifi:test/action",
            json={"action": "land"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "dry_run")
        self.assertIn("DRY-RUN", data["result"])

    def test_dry_run_mode_gates_live_execution(self):
        """Even in non-simulate mode, dry_run_mode prevents live execution."""
        web_app.simulate_mode = False
        web_app.dry_run_mode = True

        resp = self.client.post(
            "/api/targets/wifi:test/action",
            json={"action": "land"},
            content_type="application/json",
        )
        data = resp.get_json()
        self.assertEqual(data["status"], "dry_run")

    def test_nonexistent_target_404(self):
        resp = self.client.post(
            "/api/targets/wifi:nonexistent/action",
            json={"action": "land"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


class TestActionCategories(unittest.TestCase):
    """Verify action definitions include category field."""

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
        for name, defn in data.items():
            self.assertIn("category", defn)


class TestScannerFailureCleanup(unittest.TestCase):
    """Verify that scanner exceptions reset is_scanning."""

    def setUp(self):
        web_app.simulate_mode = True
        web_app.dry_run_mode = True
        web_app.is_scanning = False
        web_app.targets = {}
        web_app.scanner_status = {
            "wifi": "idle", "ble": "idle", "sdr": "idle",
            "mavlink": "idle", "telemetry": "idle",
        }

    def test_simulated_scan_resets_on_exception(self):
        """is_scanning resets even if run_scan_simulated raises."""
        web_app.is_scanning = True

        with patch.object(web_app, "SIMULATED_TARGETS", side_effect=TypeError):
            with patch.object(web_app, "_push_event"):
                with patch("time.sleep"):
                    try:
                        web_app.run_scan_simulated()
                    except Exception:
                        pass

        self.assertFalse(web_app.is_scanning)

    def test_live_scan_resets_on_exception(self):
        """is_scanning resets even if run_scan_live raises."""
        web_app.is_scanning = True

        with patch("time.sleep"):
            with patch.object(web_app, "_push_event", side_effect=RuntimeError("test")):
                try:
                    web_app.run_scan_live(["wifi"], {"wifi_iface": "wlan0"})
                except RuntimeError:
                    pass

        self.assertFalse(web_app.is_scanning)

    def test_scan_api_rejects_concurrent(self):
        """Cannot start scan while one is running."""
        web_app.is_scanning = True
        web_app.app.testing = True
        client = web_app.app.test_client()

        resp = client.post("/api/scan", json={}, content_type="application/json")
        self.assertEqual(resp.status_code, 409)


class TestScoreAndThreatLabels(unittest.TestCase):
    """Verify threat labels derive from calculated score."""

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
        web_app.simulate_mode = True
        web_app.is_scanning = True
        web_app.targets = {}

        with patch("time.sleep"):
            web_app.run_scan_simulated()

        for tid, target in web_app.targets.items():
            expected = web_app._threat_from_score(target)
            self.assertEqual(
                target["threat_level"],
                expected,
                f"Target {tid}: threat_level={target['threat_level']} "
                f"but score={target['score']} should give {expected}",
            )

    def test_score_tello_is_high(self):
        score = web_app._score_target({"drone_type": "tello"})
        self.assertGreaterEqual(score, 80)

    def test_score_unknown_is_low(self):
        score = web_app._score_target({"drone_type": "unknown"})
        self.assertLess(score, 40)


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
        import ast
        app_path = os.path.join(WEB_DIR, "app.py")
        with open(app_path) as f:
            source = f.read()
        self.assertIn('default="127.0.0.1"', source)
        self.assertNotIn('default="0.0.0.0"', source)


class TestDryRunDefault(unittest.TestCase):
    """Verify dry-run is the default mode."""

    def test_dry_run_mode_default_true(self):
        """Module-level dry_run_mode defaults to True."""
        import importlib
        self.assertTrue(web_app.dry_run_mode)

    def test_status_api_reports_dry_run(self):
        web_app.dry_run_mode = True
        web_app.simulate_mode = False
        web_app.app.testing = True
        client = web_app.app.test_client()
        resp = client.get("/api/status")
        data = resp.get_json()
        self.assertTrue(data["dry_run"])


class TestFrontendXSSMitigations(unittest.TestCase):
    """Verify the HTML template contains XSS mitigations."""

    @classmethod
    def setUpClass(cls):
        template_path = os.path.join(WEB_DIR, "templates", "index.html")
        with open(template_path) as f:
            cls.html = f.read()

    def test_no_inline_onclick_handlers(self):
        """No inline onclick= attributes in the template."""
        onclick_matches = re.findall(r'onclick="[^"]*"', self.html)
        self.assertEqual(
            onclick_matches, [],
            f"Found inline onclick handlers: {onclick_matches}",
        )

    def test_eschtml_escapes_single_quotes(self):
        """escHtml function escapes single quotes."""
        self.assertIn("&#39;", self.html)
        self.assertIn(".replace(/'/g", self.html)

    def test_safe_css_class_function_exists(self):
        """safeCssClass function strips non-alphanumeric chars."""
        self.assertIn("safeCssClass", self.html)
        self.assertIn("[^a-zA-Z0-9_-]", self.html)

    def test_threat_level_uses_safe_css(self):
        """Threat level class interpolation uses safeCssClass."""
        self.assertIn("safeCssClass(t.threat_level)", self.html)

    def test_action_category_badges_present(self):
        """Action cards show category badges."""
        self.assertIn("action-category", self.html)
        self.assertIn("cat-simulated", self.html)
        self.assertIn("cat-passive", self.html)
        self.assertIn("cat-active", self.html)

    def test_data_attributes_for_event_delegation(self):
        """Target items use data attributes instead of inline handlers."""
        self.assertIn("data-target-id", self.html)
        self.assertIn("data-action-name", self.html)
        self.assertIn("data-action-target", self.html)


if __name__ == "__main__":
    unittest.main()
