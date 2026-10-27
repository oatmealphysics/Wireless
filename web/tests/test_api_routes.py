"""Flask test client tests for the web API routes.

Exercises every public API endpoint through the Flask test client,
covering success paths, error responses, and edge cases.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "tests"))

from helpers import needs_flask, get_web_app, make_flask_client, make_target

web_app = get_web_app()


@needs_flask
class TestStatusEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)

    def test_status_returns_200(self) -> None:
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)

    def test_status_shape(self) -> None:
        resp = self.client.get("/api/status")
        data = resp.get_json()
        self.assertIn("scanning", data)
        self.assertIn("scanner_status", data)
        self.assertIn("target_count", data)
        self.assertIn("targets", data)
        self.assertIn("simulate", data)
        self.assertIn("dry_run", data)
        self.assertIn("hardware", data)

    def test_status_reflects_mode_flags(self) -> None:
        web_app.simulate_mode = True
        web_app.dry_run_mode = False
        data = self.client.get("/api/status").get_json()
        self.assertTrue(data["simulate"])
        self.assertFalse(data["dry_run"])

    def test_status_includes_injected_targets(self) -> None:
        web_app.targets["wifi:t1"] = make_target(target_id="wifi:t1")
        data = self.client.get("/api/status").get_json()
        self.assertEqual(data["target_count"], 1)
        self.assertEqual(len(data["targets"]), 1)
        self.assertEqual(data["targets"][0]["id"], "wifi:t1")

    def test_targets_sorted_by_score_descending(self) -> None:
        web_app.targets["a"] = make_target(target_id="a", score=30, name="low")
        web_app.targets["b"] = make_target(target_id="b", score=90, name="high")
        data = self.client.get("/api/status").get_json()
        self.assertEqual(data["targets"][0]["name"], "high")
        self.assertEqual(data["targets"][1]["name"], "low")


@needs_flask
class TestScanEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)

    def test_scan_starts_successfully(self) -> None:
        with patch.object(web_app.threading, "Thread") as thread_cls:
            resp = self.client.post("/api/scan", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "scanning")
        thread_cls.return_value.start.assert_called_once()
        web_app.is_scanning = False

    def test_scan_rejects_concurrent(self) -> None:
        web_app.is_scanning = True
        resp = self.client.post("/api/scan", json={})
        self.assertEqual(resp.status_code, 409)

    def test_scan_accepts_empty_body(self) -> None:
        with patch.object(web_app.threading, "Thread"):
            resp = self.client.post("/api/scan", content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        web_app.is_scanning = False

    def test_scan_rejects_non_object_body(self) -> None:
        resp = self.client.post("/api/scan", json=["wifi"])
        self.assertEqual(resp.status_code, 400)

    def test_scan_rejects_unknown_protocol(self) -> None:
        resp = self.client.post("/api/scan", json={"protocols": ["wifi", "invalid"]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("invalid", resp.get_json()["error"])

    def test_scan_rejects_empty_protocols(self) -> None:
        resp = self.client.post("/api/scan", json={"protocols": []})
        self.assertEqual(resp.status_code, 400)

    def test_scan_rejects_bad_scan_time(self) -> None:
        for bad in (0, -1, 301, True, "15"):
            resp = self.client.post("/api/scan", json={"scan_time": bad})
            self.assertEqual(resp.status_code, 400, f"Expected 400 for scan_time={bad!r}")

    def test_scan_deduplicates_protocols(self) -> None:
        with patch.object(web_app.threading, "Thread"):
            resp = self.client.post("/api/scan", json={"protocols": ["wifi", "wifi", "ble"]})
        data = resp.get_json()
        self.assertEqual(data["protocols"], ["wifi", "ble"])
        web_app.is_scanning = False


@needs_flask
class TestTargetDetailEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        web_app.targets["wifi:t1"] = make_target(target_id="wifi:t1")

    def test_existing_target_returns_200(self) -> None:
        resp = self.client.get("/api/targets/wifi:t1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["id"], "wifi:t1")

    def test_missing_target_returns_404(self) -> None:
        resp = self.client.get("/api/targets/wifi:nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_target_with_special_chars_in_id(self) -> None:
        resp = self.client.get("/api/targets/wifi:<script>alert(1)</script>")
        self.assertEqual(resp.status_code, 404)


@needs_flask
class TestTargetActionEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        web_app.targets["wifi:t1"] = make_target(
            target_id="wifi:t1",
            actions=["land", "streamon"],
        )

    def test_valid_action_returns_dry_run(self) -> None:
        resp = self.client.post(
            "/api/targets/wifi:t1/action",
            json={"action": "land"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "dry_run")
        self.assertIn("DRY-RUN", data["result"])

    def test_missing_action_returns_400(self) -> None:
        resp = self.client.post(
            "/api/targets/wifi:t1/action",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_unavailable_action_returns_400(self) -> None:
        resp = self.client.post(
            "/api/targets/wifi:t1/action",
            json={"action": "reboot"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not available", resp.get_json()["error"])

    def test_action_on_missing_target_returns_404(self) -> None:
        resp = self.client.post(
            "/api/targets/wifi:missing/action",
            json={"action": "land"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_action_logged(self) -> None:
        self.client.post(
            "/api/targets/wifi:t1/action",
            json={"action": "land"},
        )
        self.assertTrue(web_app.action_log)
        self.assertEqual(web_app.action_log[-1]["action"], "land")


@needs_flask
class TestActionDefsEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)

    def test_returns_all_action_defs(self) -> None:
        resp = self.client.get("/api/actions")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("land", data)
        self.assertIn("emergency", data)
        for name, defn in data.items():
            self.assertIn("label", defn)
            self.assertIn("description", defn)
            self.assertIn("severity", defn)
            self.assertIn("category", defn)


@needs_flask
class TestLogEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)

    def test_log_returns_empty_list(self) -> None:
        resp = self.client.get("/api/log")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_log_returns_recent_actions(self) -> None:
        web_app.targets["wifi:t1"] = make_target(target_id="wifi:t1")
        self.client.post("/api/targets/wifi:t1/action", json={"action": "land"})
        resp = self.client.get("/api/log")
        entries = resp.get_json()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "land")

    def test_log_capped_at_50(self) -> None:
        for i in range(60):
            web_app.action_log.append({"id": str(i), "action": f"a{i}"})
        resp = self.client.get("/api/log")
        self.assertEqual(len(resp.get_json()), 50)


@needs_flask
class TestClearEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)

    def test_clear_removes_targets(self) -> None:
        web_app.targets["wifi:t1"] = make_target(target_id="wifi:t1")
        resp = self.client.post("/api/clear")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "cleared")
        self.assertEqual(len(web_app.targets), 0)

    def test_clear_rejected_during_scan(self) -> None:
        web_app.is_scanning = True
        resp = self.client.post("/api/clear")
        self.assertEqual(resp.status_code, 409)


@needs_flask
class TestHardwareEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)

    def test_simulation_hardware_never_probes_host(self) -> None:
        with patch.object(
            web_app,
            "detect_hardware",
            side_effect=AssertionError("simulation attempted a hardware probe"),
        ) as detector:
            resp = self.client.get("/api/hardware")
        self.assertEqual(resp.status_code, 200)
        detector.assert_not_called()
        data = resp.get_json()
        self.assertEqual(data["mode"], "simulation")
        self.assertFalse(data["probe_performed"])
        for category in ("wifi", "ble", "sdr", "mavlink"):
            self.assertEqual(data[category]["status"], "not_checked")

    def test_live_hardware_route_uses_detector(self) -> None:
        web_app.simulate_mode = False
        self.addCleanup(setattr, web_app, "simulate_mode", True)
        detected = {
            "mode": "live",
            "probe_performed": True,
            "wifi": {"available": False},
        }
        with patch.object(web_app, "detect_hardware", return_value=detected) as detector:
            resp = self.client.get("/api/hardware")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), detected)
        detector.assert_called_once_with()


@needs_flask
class TestChain2Endpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        web_app.chain2_state = {
            "status": "idle",
            "proxy_running": False,
            "proxy_stats": {},
            "evil_twin": None,
            "last_injection": None,
            "injections": [],
        }

    def test_chain2_status(self) -> None:
        resp = self.client.get("/api/chain2/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "idle")

    def test_chain2_simulate(self) -> None:
        resp = self.client.post(
            "/api/chain2/simulate",
            json={"commands": ["land"]},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "simulated")
        self.assertEqual(len(data["results"]), 1)

    def test_chain2_simulate_unknown_command(self) -> None:
        resp = self.client.post(
            "/api/chain2/simulate",
            json={"commands": ["nonexistent"]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["results"][0]["status"], "unknown")

    def test_chain2_inject_requires_json(self) -> None:
        resp = self.client.post(
            "/api/chain2/inject",
            data="",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_chain2_inject_requires_command(self) -> None:
        resp = self.client.post("/api/chain2/inject", json={"target_id": "t1"})
        self.assertEqual(resp.status_code, 400)

    def test_chain2_inject_rejects_unknown_command(self) -> None:
        resp = self.client.post("/api/chain2/inject", json={"command": "invalid"})
        self.assertEqual(resp.status_code, 400)

    def test_chain2_inject_dry_run(self) -> None:
        resp = self.client.post(
            "/api/chain2/inject",
            json={"command": "land", "target_id": "t1"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "dry_run")
        self.assertIn("packet_hex", data)

    def test_chain2_commands_lists_attacks(self) -> None:
        resp = self.client.get("/api/chain2/commands")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("land", data)
        self.assertIn("disarm", data)


@needs_flask
class TestIndexRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_flask_client(web_app)

    def test_index_returns_html(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.content_type)


if __name__ == "__main__":
    unittest.main()
