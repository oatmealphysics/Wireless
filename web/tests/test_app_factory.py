"""Cross-instance regressions for the dashboard application/state factory."""

from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "tests"))

from helpers import get_web_app, make_target, needs_flask

web_app = get_web_app()


@needs_flask
class TestApplicationStateFactory(unittest.TestCase):
    def make_instance(self):
        state = web_app.create_dashboard_state(simulate=True, dry_run=True)
        flask_app = web_app.create_app(state)
        flask_app.testing = True
        return flask_app, state

    def test_factory_allocates_every_mutable_member(self) -> None:
        app_a, state_a = self.make_instance()
        app_b, state_b = self.make_instance()

        self.assertIsNot(app_a, app_b)
        self.assertIsNot(state_a, state_b)
        for name in (
            "scan_lock",
            "targets",
            "action_log",
            "scanner_status",
            "hw_status",
            "events",
            "events_lock",
            "scan_defaults",
            "chain2_state",
        ):
            self.assertIsNot(getattr(state_a, name), getattr(state_b, name), name)
        self.assertIsNot(
            state_a.chain2_state["proxy_stats"],
            state_b.chain2_state["proxy_stats"],
        )
        self.assertIsNot(
            state_a.chain2_state["injections"],
            state_b.chain2_state["injections"],
        )
        self.assertEqual(state_a.next_event_id, 0)
        self.assertEqual(state_b.next_event_id, 0)
        self.assertTrue(state_a.simulate_mode)
        self.assertTrue(state_b.dry_run_mode)

    def test_parallel_mutation_never_crosses_instance_boundary(self) -> None:
        app_a, state_a = self.make_instance()
        app_b, state_b = self.make_instance()

        def mutate(flask_app, state, label, scanner_status):
            target_id = f"wifi:{label}"
            target = make_target(target_id=target_id, name=f"Drone-{label}")
            with state.scan_lock:
                state.targets[target_id] = target
                state.is_scanning = True
                state.scanner_status["wifi"] = scanner_status
                state.scan_defaults["wifi_iface"] = f"wlan-{label}"
            web_app._push_event(state, "instance_event", {"instance": label})
            with flask_app.test_client() as client:
                action = client.post(
                    f"/api/targets/{target_id}/action", json={"action": "land"},
                )
                hardware = client.get("/api/hardware")
                chain2 = client.post(
                    "/api/chain2/simulate",
                    json={"target_id": label, "commands": ["land"]},
                )
            with state.state_lock:
                state.hw_status["instance"] = label
            return action.status_code, hardware.status_code, chain2.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(mutate, app_a, state_a, "a", "scanning")
            future_b = pool.submit(mutate, app_b, state_b, "b", "error")
            self.assertEqual(future_a.result(), (200, 200, 200))
            self.assertEqual(future_b.result(), (200, 200, 200))

        self.assertEqual(set(state_a.targets), {"wifi:a"})
        self.assertEqual(set(state_b.targets), {"wifi:b"})
        self.assertEqual(state_a.scanner_status["wifi"], "scanning")
        self.assertEqual(state_b.scanner_status["wifi"], "error")
        self.assertEqual(state_a.scan_defaults["wifi_iface"], "wlan-a")
        self.assertEqual(state_b.scan_defaults["wifi_iface"], "wlan-b")
        self.assertEqual(state_a.hw_status["instance"], "a")
        self.assertEqual(state_b.hw_status["instance"], "b")
        self.assertEqual(
            {event["data"].get("instance") for event in state_a.events},
            {None, "a"},
        )
        self.assertEqual(
            {event["data"].get("instance") for event in state_b.events},
            {None, "b"},
        )
        self.assertTrue(all(entry["target_id"] != "wifi:b" for entry in state_a.action_log))
        self.assertTrue(all(entry["target_id"] != "wifi:a" for entry in state_b.action_log))
        self.assertEqual(
            {entry["target_id"] for entry in state_a.chain2_state["injections"]},
            {"a"},
        )
        self.assertEqual(
            {entry["target_id"] for entry in state_b.chain2_state["injections"]},
            {"b"},
        )

        status_a = app_a.test_client().get("/api/status").get_json()
        status_b = app_b.test_client().get("/api/status").get_json()
        self.assertEqual({target["id"] for target in status_a["targets"]}, {"wifi:a"})
        self.assertEqual({target["id"] for target in status_b["targets"]}, {"wifi:b"})

    def test_scan_admission_passes_the_originating_state_to_each_worker(self) -> None:
        app_a, state_a = self.make_instance()
        app_b, state_b = self.make_instance()

        with patch.object(web_app.threading, "Thread") as thread_cls:
            response_a = app_a.test_client().post("/api/scan", json={"protocols": ["wifi"]})
            response_b = app_b.test_client().post("/api/scan", json={"protocols": ["ble"]})

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertTrue(state_a.is_scanning)
        self.assertTrue(state_b.is_scanning)
        calls = thread_cls.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0].kwargs["args"][0], state_a)
        self.assertIs(calls[1].kwargs["args"][0], state_b)
        self.assertIs(calls[0].kwargs["args"][2], web_app.run_scan_simulated)
        self.assertIs(calls[1].kwargs["args"][2], web_app.run_scan_simulated)
        self.assertEqual(calls[0].kwargs["args"][3], (["wifi"],))
        self.assertEqual(calls[1].kwargs["args"][3], (["ble"],))

    def test_live_scan_uses_only_its_instance_defaults(self) -> None:
        state_a = web_app.create_dashboard_state(
            simulate=False,
            scan_defaults={
                "wifi_iface": "wifi-a",
                "ble_iface": "ble-a",
                "sdr_args": "driver=a",
            },
        )
        state_b = web_app.create_dashboard_state(
            simulate=False,
            scan_defaults={
                "wifi_iface": "wifi-b",
                "ble_iface": "ble-b",
                "sdr_args": "driver=b",
            },
        )
        app_a = web_app.create_app(state_a)
        app_b = web_app.create_app(state_b)

        with patch.object(web_app.threading, "Thread") as thread_cls:
            response_a = app_a.test_client().post("/api/scan", json={})
            response_b = app_b.test_client().post("/api/scan", json={})

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        calls = thread_cls.call_args_list
        self.assertEqual(len(calls), 2)
        worker_a = calls[0].kwargs["args"]
        worker_b = calls[1].kwargs["args"]
        self.assertIs(worker_a[0], state_a)
        self.assertIs(worker_b[0], state_b)
        self.assertIs(worker_a[2], web_app.run_scan_live)
        self.assertIs(worker_b[2], web_app.run_scan_live)
        args_a = worker_a[3]
        args_b = worker_b[3]
        self.assertEqual(
            args_a[1],
            {
                "wifi_iface": "wifi-a",
                "ble_iface": "ble-a",
                "sdr_args": "driver=a",
                "scan_time": 15.0,
            },
        )
        self.assertEqual(
            args_b[1],
            {
                "wifi_iface": "wifi-b",
                "ble_iface": "ble-b",
                "sdr_args": "driver=b",
                "scan_time": 15.0,
            },
        )

    def test_hardware_cache_and_modes_are_instance_owned(self) -> None:
        app_a, state_a = self.make_instance()
        state_b = web_app.create_dashboard_state(simulate=False, dry_run=False)
        app_b = web_app.create_app(state_b)
        detected = {
            "mode": "live",
            "probe_performed": True,
            "wifi": {"available": False, "source": "instance-b"},
        }

        with patch.object(web_app, "detect_hardware", return_value=detected) as detector:
            response_a = app_a.test_client().get("/api/hardware")
            detector.assert_not_called()
            response_b = app_b.test_client().get("/api/hardware")

        detector.assert_called_once_with()
        self.assertEqual(response_a.get_json()["mode"], "simulation")
        self.assertEqual(response_b.get_json(), detected)
        self.assertEqual(state_a.hw_status["mode"], "simulation")
        self.assertEqual(state_b.hw_status, detected)
        self.assertNotIn("source", state_a.hw_status["wifi"])

        status_a = app_a.test_client().get("/api/status").get_json()
        status_b = app_b.test_client().get("/api/status").get_json()
        self.assertTrue(status_a["simulate"])
        self.assertTrue(status_a["dry_run"])
        self.assertFalse(status_b["simulate"])
        self.assertFalse(status_b["dry_run"])

    def test_parallel_workers_and_nested_targets_are_isolated(self) -> None:
        _, state_a = self.make_instance()
        _, state_b = self.make_instance()

        with patch.object(web_app.time, "sleep", return_value=None):
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(web_app.run_scan_simulated, state_a, ["wifi"])
                future_b = pool.submit(web_app.run_scan_simulated, state_b, ["wifi"])
                future_a.result()
                future_b.result()

        self.assertEqual(
            {target["protocol"] for target in state_a.targets.values()}, {"wifi"},
        )
        self.assertEqual(
            {target["protocol"] for target in state_b.targets.values()}, {"wifi"},
        )
        event_a = next(event for event in state_a.events if event["type"] == "scan_start")
        event_b = next(event for event in state_b.events if event["type"] == "scan_start")
        self.assertEqual(event_a["data"]["protocols"], ["wifi"])
        self.assertEqual(event_b["data"]["protocols"], ["wifi"])

        shared_id = next(iter(state_a.targets))
        target_a = state_a.targets[shared_id]
        target_b = state_b.targets[shared_id]
        target_a["details"]["instance_only"] = "a"
        target_a["vulns"].append("A_ONLY")
        self.assertNotIn("instance_only", target_b["details"])
        self.assertNotIn("A_ONLY", target_b["vulns"])

    def test_event_stream_closes_over_its_application_state(self) -> None:
        app_a, state_a = self.make_instance()
        app_b, state_b = self.make_instance()
        web_app._push_event(state_a, "only_a", {"instance": "a"})
        web_app._push_event(state_b, "only_b", {"instance": "b"})

        response_a = app_a.test_client().get("/api/events?last_id=-1", buffered=False)
        response_b = app_b.test_client().get("/api/events?last_id=-1", buffered=False)
        try:
            stream_a = iter(response_a.response)
            stream_b = iter(response_b.response)
            self.assertEqual(next(stream_a), b": connected\n\n")
            self.assertEqual(next(stream_b), b": connected\n\n")
            chunk_a = next(stream_a).decode()
            chunk_b = next(stream_b).decode()
        finally:
            response_a.close()
            response_b.close()

        self.assertIn("event: only_a", chunk_a)
        self.assertNotIn("only_b", chunk_a)
        self.assertIn("event: only_b", chunk_b)
        self.assertNotIn("only_a", chunk_b)

    def test_default_imported_app_remains_compatible(self) -> None:
        state = web_app.get_dashboard_state(web_app.app)
        state.reset_runtime()
        web_app.app.testing = True
        response = web_app.app.test_client().get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertIs(web_app.get_dashboard_state(web_app.app), state)


if __name__ == "__main__":
    unittest.main()
