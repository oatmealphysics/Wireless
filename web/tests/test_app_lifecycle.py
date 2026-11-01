"""Lifecycle and cleanup regressions for isolated dashboard applications."""

from __future__ import annotations

import logging
import sys
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "tests"))

from helpers import get_web_app, make_flask_client, make_target, needs_flask

web_app = get_web_app()


@needs_flask
class TestApplicationLifecycle(unittest.TestCase):
    def make_instance(self, *, simulate: bool = True):
        state = web_app.create_dashboard_state(
            simulate=simulate,
            dry_run=True,
        )
        flask_app = web_app.create_app(state)
        flask_app.testing = True
        return flask_app, state

    def snapshot(self, state):
        with state.state_lock:
            return {
                "targets": deepcopy(state.targets),
                "action_log": deepcopy(state.action_log),
                "scanner_status": dict(state.scanner_status),
                "is_scanning": state.is_scanning,
                "simulate_mode": state.simulate_mode,
                "dry_run_mode": state.dry_run_mode,
                "hw_status": deepcopy(state.hw_status),
                "events": deepcopy(state.events),
                "next_event_id": state.next_event_id,
                "scan_defaults": dict(state.scan_defaults),
                "chain2_state": deepcopy(state.chain2_state),
                "scan_generation": state.scan_generation,
                "active_scan_generation": state.active_scan_generation,
                "active_scan_thread": state.active_scan_thread,
            }

    def seed_peer(self, state, label: str) -> None:
        with state.state_lock:
            state.targets[label] = make_target(target_id=label, name=label)
            state.action_log.append({"id": label, "action": "peer"})
            state.hw_status = {"peer": label}
            state.chain2_state["proxy_stats"] = {"peer": label}
        web_app._push_event(state, "peer", {"peer": label})

    def test_factory_rejects_invalid_or_reused_state(self) -> None:
        with self.assertRaisesRegex(TypeError, "DashboardState"):
            web_app.create_app({})

        flask_app, state = self.make_instance()
        with self.assertRaisesRegex(RuntimeError, "already installed"):
            web_app.create_app(state)

        response = flask_app.test_client().get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertIs(web_app.get_dashboard_state(flask_app), state)

    def test_blueprint_failure_releases_state_claim(self) -> None:
        live_app, live_state = self.make_instance()
        self.seed_peer(live_state, "live")
        before = self.snapshot(live_state)
        candidate = web_app.create_dashboard_state(simulate=True)

        with patch.object(
                web_app.Flask,
                "register_blueprint",
                autospec=True,
                side_effect=RuntimeError("registration failed")):
            with self.assertRaisesRegex(RuntimeError, "registration failed"):
                web_app.create_app(candidate)

        recovered = web_app.create_app(candidate)
        recovered.testing = True
        self.assertEqual(recovered.test_client().get("/api/status").status_code, 200)
        self.assertEqual(self.snapshot(live_state), before)
        self.assertEqual(live_app.test_client().get("/api/status").status_code, 200)

    def test_factory_state_creation_failure_cannot_mutate_existing_app(self) -> None:
        flask_app, state = self.make_instance()
        self.seed_peer(state, "existing")
        before = self.snapshot(state)

        with patch.object(
                web_app,
                "create_dashboard_state",
                side_effect=RuntimeError("state creation failed")):
            with self.assertRaisesRegex(RuntimeError, "state creation failed"):
                web_app.create_app()

        self.assertEqual(self.snapshot(state), before)
        self.assertEqual(flask_app.test_client().get("/api/status").status_code, 200)

    def test_route_test_clients_receive_fresh_state(self) -> None:
        client_a = make_flask_client(web_app, simulate=True, dry_run=False)
        client_b = make_flask_client(web_app, simulate=False, dry_run=True)
        state_a = web_app.get_dashboard_state(client_a.application)
        state_b = web_app.get_dashboard_state(client_b.application)

        with state_a.state_lock:
            state_a.targets["only-a"] = make_target(target_id="only-a")
            state_a.scan_defaults["wifi_iface"] = "test-a"
        web_app._push_event(state_a, "only_a", {})

        self.assertIsNot(state_a, state_b)
        self.assertEqual(state_b.targets, {})
        self.assertEqual(state_b.events, [])
        self.assertEqual(state_b.scan_defaults["wifi_iface"], "wlan0")
        self.assertFalse(state_a.dry_run_mode)
        self.assertTrue(state_b.dry_run_mode)
        self.assertTrue(state_a.simulate_mode)
        self.assertFalse(state_b.simulate_mode)

    def test_runtime_reset_is_complete_and_instance_local(self) -> None:
        flask_app, state = self.make_instance()
        _, peer = self.make_instance()
        self.seed_peer(peer, "peer")
        peer_before = self.snapshot(peer)

        with state.state_lock:
            state.simulate_mode = False
            state.dry_run_mode = False
            state.scan_defaults["wifi_iface"] = "owned-wifi"
            state.targets["owned"] = make_target(target_id="owned")
            state.action_log.append({"id": "action", "nested": {"owned": True}})
            state.scanner_status["wifi"] = "error"
            state.hw_status = {"nested": {"owned": True}}
            state.chain2_state = {
                "status": "running",
                "proxy_running": True,
                "proxy_stats": {"packets": 9},
                "evil_twin": {"ssid": "owned"},
                "last_injection": {"id": "inject"},
                "injections": [{"id": "inject"}],
            }
        web_app._push_event(state, "owned", {"nested": {"owned": True}})
        generation_before = state.scan_generation
        next_event_id_before = state.next_event_id

        state.reset_runtime()

        with state.state_lock:
            self.assertEqual(state.targets, {})
            self.assertEqual(state.action_log, [])
            self.assertEqual(state.scanner_status, web_app._new_scanner_status())
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_generation)
            self.assertIsNone(state.active_scan_thread)
            self.assertEqual(state.hw_status, {})
            self.assertEqual(state.events, [])
            self.assertEqual(state.next_event_id, next_event_id_before)
            self.assertEqual(state.chain2_state, web_app._new_chain2_state())
            self.assertFalse(state.simulate_mode)
            self.assertFalse(state.dry_run_mode)
            self.assertEqual(state.scan_defaults["wifi_iface"], "owned-wifi")
            self.assertEqual(state.scan_generation, generation_before + 1)
        self.assertEqual(self.snapshot(peer), peer_before)
        self.assertEqual(flask_app.test_client().get("/api/status").status_code, 200)

    def test_reset_refuses_an_active_worker_then_succeeds_after_join(self) -> None:
        flask_app, state = self.make_instance()
        entered = threading.Event()
        release = threading.Event()

        def blocked_worker(
                worker_state, protocols, *, scan_generation=None):
            self.assertIs(worker_state, state)
            self.assertEqual(protocols, ["wifi"])
            self.assertIsNotNone(scan_generation)
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test worker release timed out")

        with patch.object(web_app, "run_scan_simulated", new=blocked_worker):
            response = flask_app.test_client().post(
                "/api/scan", json={"protocols": ["wifi"]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(entered.wait(5), "scan worker did not start")
        with state.state_lock:
            worker_thread = state.active_scan_thread
        self.assertIsNotNone(worker_thread)
        with self.assertRaisesRegex(RuntimeError, "scan worker is active"):
            state.reset_runtime()

        release.set()
        worker_thread.join(timeout=10)
        self.assertFalse(worker_thread.is_alive())
        state.reset_runtime()
        self.assertEqual(state.targets, {})
        self.assertEqual(state.events, [])
        self.assertFalse(state.is_scanning)

    def test_direct_simulated_scan_blocks_reset_until_completion(self) -> None:
        _, state = self.make_instance()
        entered = threading.Event()
        release = threading.Event()
        errors = []

        def blocking_sleep(_delay):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test worker release timed out")

        def run_directly() -> None:
            try:
                web_app.run_scan_simulated(state, ["wifi"])
            except BaseException as exc:
                errors.append(exc)

        with patch.object(web_app.time, "sleep", side_effect=blocking_sleep):
            worker = threading.Thread(target=run_directly)
            worker.start()
            self.assertTrue(entered.wait(5), "direct scan did not start")
            with self.assertRaisesRegex(RuntimeError, "scan worker is active"):
                state.reset_runtime()
            release.set()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        with state.state_lock:
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_generation)
            self.assertIsNone(state.active_scan_thread)
        state.reset_runtime()
        self.assertEqual(state.targets, {})
        self.assertEqual(state.events, [])

    def test_direct_live_scan_blocks_reset_until_completion(self) -> None:
        _, state = self.make_instance(simulate=False)
        entered = threading.Event()
        release = threading.Event()
        errors = []

        def blocking_scan(*_args, **_kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test worker release timed out")
            return []

        def run_directly() -> None:
            try:
                web_app.run_scan_live(
                    state,
                    ["wifi"],
                    {"wifi_iface": "test", "scan_time": 0.1},
                )
            except BaseException as exc:
                errors.append(exc)

        with patch(
                "exploit_chains.multi_recon.scan_wifi",
                side_effect=blocking_scan):
            with patch.object(web_app.time, "sleep", return_value=None):
                worker = threading.Thread(target=run_directly)
                worker.start()
                self.assertTrue(entered.wait(5), "direct live scan did not start")
                with self.assertRaisesRegex(RuntimeError, "scan worker is active"):
                    state.reset_runtime()
                release.set()
                worker.join(timeout=10)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        with state.state_lock:
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_generation)
            self.assertIsNone(state.active_scan_thread)
        state.reset_runtime()
        self.assertEqual(state.targets, {})
        self.assertEqual(state.events, [])

    def test_invalid_direct_scan_input_releases_slot_and_allows_retry(self) -> None:
        _, state = self.make_instance()

        with self.assertRaises(TypeError):
            web_app.run_scan_simulated(state, [["unhashable"]])

        with state.state_lock:
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_generation)
            self.assertIsNone(state.active_scan_thread)

        with patch.object(web_app.time, "sleep", return_value=None):
            web_app.run_scan_simulated(state, ["wifi"])

        with state.state_lock:
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_generation)
            self.assertIsNone(state.active_scan_thread)
            self.assertEqual(
                {target["protocol"] for target in state.targets.values()},
                {"wifi"},
            )

    def test_bootstrap_exception_is_recorded_and_releases_only_origin(self) -> None:
        flask_app, state = self.make_instance()
        _, peer = self.make_instance()
        self.seed_peer(peer, "peer")
        peer_before = self.snapshot(peer)
        entered = threading.Event()
        real_thread = threading.Thread
        created_threads = []

        def thread_factory(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            created_threads.append(thread)
            return thread

        def fail_before_bootstrap(*args, **kwargs):
            entered.set()
            raise RuntimeError("bootstrap failed")

        with patch.object(web_app.threading, "Thread", side_effect=thread_factory):
            with patch.object(
                    web_app, "run_scan_simulated", new=fail_before_bootstrap):
                response = flask_app.test_client().post(
                    "/api/scan", json={"protocols": ["wifi"]},
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(entered.wait(5), "failing worker did not start")
        self.assertEqual(len(created_threads), 1)
        created_threads[0].join(timeout=10)
        self.assertFalse(created_threads[0].is_alive())
        with state.state_lock:
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_generation)
            self.assertIsNone(state.active_scan_thread)
            self.assertFalse(any(
                status == "scanning" for status in state.scanner_status.values()
            ))
            self.assertEqual([event["type"] for event in state.events], ["scan_failed"])
        self.assertEqual(self.snapshot(peer), peer_before)

        fake_thread = MagicMock()
        with patch.object(web_app.threading, "Thread", return_value=fake_thread):
            retry = flask_app.test_client().post(
                "/api/scan", json={"protocols": ["wifi"]},
            )
        self.assertEqual(retry.status_code, 200)
        fake_thread.start.assert_called_once_with()
        web_app._finish_scan(state, state.active_scan_generation)

    def test_thread_constructor_failure_is_instance_local_and_retryable(self) -> None:
        flask_app, state = self.make_instance()
        _, peer = self.make_instance()
        self.seed_peer(state, "origin")
        self.seed_peer(peer, "peer")
        origin_before = self.snapshot(state)
        peer_before = self.snapshot(peer)

        with patch.object(
                web_app.threading,
                "Thread",
                side_effect=RuntimeError("constructor failed")):
            response = flask_app.test_client().post(
                "/api/scan", json={"protocols": ["wifi"]},
            )

        self.assertEqual(response.status_code, 500)
        with state.state_lock:
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_generation)
            self.assertIsNone(state.active_scan_thread)
            self.assertFalse(any(
                status == "scanning" for status in state.scanner_status.values()
            ))
        self.assertEqual(state.targets, origin_before["targets"])
        self.assertEqual(state.action_log, origin_before["action_log"])
        self.assertEqual(state.events, origin_before["events"])
        self.assertEqual(state.chain2_state, origin_before["chain2_state"])
        self.assertEqual(self.snapshot(peer), peer_before)

        fake_thread = MagicMock()
        with patch.object(web_app.threading, "Thread", return_value=fake_thread):
            retry = flask_app.test_client().post(
                "/api/scan", json={"protocols": ["wifi"]},
            )
        self.assertEqual(retry.status_code, 200)
        web_app._finish_scan(state, state.active_scan_generation)

    def test_stale_generation_cannot_release_a_new_scan(self) -> None:
        flask_app, state = self.make_instance()
        first_thread = MagicMock()
        with patch.object(web_app.threading, "Thread", return_value=first_thread):
            first = flask_app.test_client().post(
                "/api/scan", json={"protocols": ["wifi"]},
            )
        self.assertEqual(first.status_code, 200)
        first_generation = state.active_scan_generation
        self.assertTrue(web_app._finish_scan(state, first_generation))

        second_thread = MagicMock()
        with patch.object(web_app.threading, "Thread", return_value=second_thread):
            second = flask_app.test_client().post(
                "/api/scan", json={"protocols": ["ble"]},
            )
        self.assertEqual(second.status_code, 200)
        second_generation = state.active_scan_generation
        self.assertNotEqual(first_generation, second_generation)

        self.assertFalse(web_app._finish_scan(state, first_generation))
        with state.state_lock:
            self.assertTrue(state.is_scanning)
            self.assertEqual(state.active_scan_generation, second_generation)
            self.assertIs(state.active_scan_thread, second_thread)
        web_app._finish_scan(state, second_generation)

    def test_live_worker_bootstraps_safely_in_background_thread(self) -> None:
        flask_app, state = self.make_instance(simulate=False)
        real_thread = threading.Thread
        created_threads = []

        def thread_factory(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            created_threads.append(thread)
            return thread

        with patch.object(web_app.threading, "Thread", side_effect=thread_factory):
            with patch(
                    "exploit_chains.multi_recon.scan_wifi",
                    return_value=[]):
                with patch.object(web_app.time, "sleep", return_value=None):
                    response = flask_app.test_client().post(
                        "/api/scan",
                        json={"protocols": ["wifi"], "scan_time": 0.1},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(created_threads), 1)
                    created_threads[0].join(timeout=10)

        self.assertFalse(created_threads[0].is_alive())
        with state.state_lock:
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_thread)
            event_types = [event["type"] for event in state.events]
        self.assertIn("scan_start", event_types)
        self.assertIn("scanner_done", event_types)
        self.assertIn("scan_complete", event_types)
        self.assertNotIn("scan_failed", event_types)

    def test_simulated_worker_completes_through_owned_wrapper(self) -> None:
        flask_app, state = self.make_instance(simulate=True)
        real_thread = threading.Thread
        created_threads = []

        def thread_factory(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            created_threads.append(thread)
            return thread

        with patch.object(web_app.threading, "Thread", side_effect=thread_factory):
            with patch.object(web_app.time, "sleep", return_value=None):
                response = flask_app.test_client().post(
                    "/api/scan", json={"protocols": ["wifi"]},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(created_threads), 1)
                created_threads[0].join(timeout=10)

        self.assertFalse(created_threads[0].is_alive())
        with state.state_lock:
            self.assertFalse(state.is_scanning)
            self.assertIsNone(state.active_scan_generation)
            self.assertIsNone(state.active_scan_thread)
            self.assertEqual(
                {target["protocol"] for target in state.targets.values()},
                {"wifi"},
            )
            event_types = [event["type"] for event in state.events]
        self.assertIn("scan_start", event_types)
        self.assertIn("scanner_done", event_types)
        self.assertIn("target_found", event_types)
        self.assertIn("scan_complete", event_types)
        self.assertNotIn("scan_failed", event_types)

    def test_repeated_scan_loggers_do_not_accumulate_handlers(self) -> None:
        logger_name = f"web-scan-lifecycle-{id(self)}"
        logger = logging.getLogger(logger_name)
        workers = 8
        barrier = threading.Barrier(workers)
        errors = []

        def create_logger() -> None:
            try:
                barrier.wait(timeout=5)
                web_app.ChainLogger(logger_name)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create_logger) for _ in range(workers)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            marked_handlers = [
                handler for handler in logger.handlers
                if getattr(handler, "_drone_chain_handler", False)
            ]
            self.assertEqual(len(marked_handlers), 1)
        finally:
            for handler in list(logger.handlers):
                if getattr(handler, "_drone_chain_handler", False):
                    logger.removeHandler(handler)
                    handler.close()

    def test_passive_sdr_stream_closes_after_read_failure(self) -> None:
        calls = []

        class FakeDevice:
            def setSampleRate(self, *args):
                pass

            def setGain(self, *args):
                pass

            def setupStream(self, *args):
                calls.append("setup")
                return "stream"

            def activateStream(self, stream):
                calls.append(("activate", stream))

            def setFrequency(self, *args):
                pass

            def readStream(self, *args, **kwargs):
                raise RuntimeError("read failed")

            def deactivateStream(self, stream):
                calls.append(("deactivate", stream))

            def closeStream(self, stream):
                calls.append(("close", stream))

        device = FakeDevice()
        fake_soapy = SimpleNamespace(
            Device=lambda _args: device,
            SOAPY_SDR_RX=1,
            SOAPY_SDR_CF32=2,
        )
        fake_numpy = SimpleNamespace(
            complex64=object(),
            zeros=lambda size, dtype=None: [0j] * size,
        )

        with patch.dict(
                sys.modules,
                {"SoapySDR": fake_soapy, "numpy": fake_numpy}):
            with patch.object(web_app.time, "sleep", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "read failed"):
                    web_app._scan_passive_telemetry(
                        "driver=fake", 0.1, MagicMock(), MagicMock(),
                    )

        self.assertEqual(calls.count(("deactivate", "stream")), 1)
        self.assertEqual(calls.count(("close", "stream")), 1)

    def test_mavlink_socket_closes_when_configuration_fails(self) -> None:
        sockets = [MagicMock() for _ in range(6)]
        for sock in sockets:
            sock.setsockopt.side_effect = OSError("configuration failed")

        with patch.object(web_app.socket, "socket", side_effect=sockets):
            results = web_app._scan_mavlink_udp(
                0.1, MagicMock(), MagicMock(),
            )

        self.assertEqual(results, [])
        for sock in sockets:
            sock.close.assert_called_once_with()

    def test_hardware_probe_socket_closes_when_bind_fails(self) -> None:
        sockets = [MagicMock() for _ in range(3)]
        for sock in sockets:
            sock.bind.side_effect = OSError("port unavailable")

        with patch.object(
                web_app.subprocess,
                "run",
                side_effect=FileNotFoundError("tool unavailable")):
            with patch.dict(sys.modules, {"SoapySDR": None}):
                with patch.object(
                        web_app.socket, "socket", side_effect=sockets):
                    snapshot = web_app.detect_hardware()

        self.assertEqual(snapshot["mavlink"]["bindable_ports"], [])
        for sock in sockets:
            sock.close.assert_called_once_with()

    def test_closing_one_event_stream_does_not_close_another(self) -> None:
        app_a, state_a = self.make_instance()
        app_b, state_b = self.make_instance()
        response_a = app_a.test_client().get(
            "/api/events?last_id=-1", buffered=False,
        )
        response_b = app_b.test_client().get(
            "/api/events?last_id=-1", buffered=False,
        )
        stream_a = iter(response_a.response)
        stream_b = iter(response_b.response)
        try:
            self.assertEqual(next(stream_a), b": connected\n\n")
            self.assertEqual(next(stream_b), b": connected\n\n")
            response_a.close()
            with self.assertRaises(StopIteration):
                next(stream_a)

            web_app._push_event(state_b, "still_open", {"instance": "b"})
            chunk_b = next(stream_b).decode()
            self.assertIn("event: still_open", chunk_b)
            self.assertNotIn("still_open", [
                event["type"] for event in state_a.events
            ])
        finally:
            response_a.close()
            response_b.close()


if __name__ == "__main__":
    unittest.main()
