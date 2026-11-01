"""Concurrency and retention regressions for dashboard-owned histories."""

from __future__ import annotations

import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "tests"))

from helpers import get_web_app, make_target, needs_flask

web_app = get_web_app()


class TrackingRLock:
    """Small RLock proxy that makes lock participation deterministic."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self.enter_count = 0

    def __enter__(self):
        self._lock.acquire()
        self._local.depth = getattr(self._local, "depth", 0) + 1
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._local.depth -= 1
        self._lock.release()

    @property
    def held_by_current_thread(self) -> bool:
        return getattr(self._local, "depth", 0) > 0


class GuardedList(list):
    """List that rejects every mutation outside its owning test lock."""

    def __init__(self, lock: TrackingRLock, values=()) -> None:
        super().__init__(values)
        self._lock = lock

    def _require_lock(self) -> None:
        if not self._lock.held_by_current_thread:
            raise AssertionError("history mutation occurred outside state_lock")

    def append(self, value) -> None:
        self._require_lock()
        super().append(value)

    def extend(self, values) -> None:
        self._require_lock()
        super().extend(values)

    def clear(self) -> None:
        self._require_lock()
        super().clear()

    def __delitem__(self, key) -> None:
        self._require_lock()
        super().__delitem__(key)

    def __deepcopy__(self, memo):
        return [deepcopy(value, memo) for value in self]


@needs_flask
class TestDashboardHistoryOwnership(unittest.TestCase):
    def make_instance(self):
        state = web_app.create_dashboard_state(simulate=True, dry_run=True)
        flask_app = web_app.create_app(state)
        flask_app.testing = True
        return flask_app, state

    def test_history_mutations_use_the_unified_lock(self) -> None:
        flask_app, state = self.make_instance()
        self.assertIs(state.scan_lock, state.state_lock)
        self.assertIs(state.events_lock, state.state_lock)

        tracker = TrackingRLock()
        state.state_lock = tracker
        with state.state_lock:
            state.events = GuardedList(
                tracker,
                [
                    {"id": index, "type": "old", "data": {}}
                    for index in range(web_app.MAX_EVENTS)
                ],
            )
            state.next_event_id = web_app.MAX_EVENTS
            state.action_log = GuardedList(
                tracker,
                [{"id": f"old-{index}"} for index in range(web_app.MAX_LOG)],
            )
            state.chain2_state["injections"] = GuardedList(
                tracker,
                [
                    {"id": f"old-{index}"}
                    for index in range(web_app.MAX_CHAIN2_INJECTIONS)
                ],
            )

        def assert_locked(operation) -> None:
            before = tracker.enter_count
            operation()
            self.assertGreater(tracker.enter_count, before)

        assert_locked(
            lambda: web_app._push_event(state, "test", {"nested": [1]}),
        )
        assert_locked(
            lambda: web_app._record_action(
                state,
                {
                    "id": "action-1",
                    "action": "land",
                    "target_id": "target-1",
                },
            ),
        )
        assert_locked(
            lambda: web_app._record_chain2_history(
                state,
                [{"id": "chain-1", "command": "land"}],
                [{"id": "chain-1", "action": "chain2_land"}],
            ),
        )
        with flask_app.test_client() as client:
            assert_locked(lambda: client.get("/api/log"))
            assert_locked(lambda: client.get("/api/chain2/status"))
        assert_locked(state.reset_runtime)

    def test_route_snapshots_detach_before_serialization(self) -> None:
        flask_app, state = self.make_instance()
        tracker = TrackingRLock()
        state.state_lock = tracker
        with state.state_lock:
            state.action_log = [{"id": "a", "nested": {"value": "before"}}]
            injection = {"id": "i", "nested": {"value": "before"}}
            state.chain2_state["injections"] = [injection]
            state.chain2_state["last_injection"] = injection
            target = make_target(target_id="wifi:snapshot")
            target["details"] = {"value": "before"}
            state.targets[target["id"]] = target
            state.hw_status = {"nested": {"value": "before"}}

        original_jsonify = web_app.jsonify

        def assert_unlocked_then_mutate(mutator):
            def checked_jsonify(payload):
                self.assertFalse(tracker.held_by_current_thread)
                with state.state_lock:
                    mutator()
                return original_jsonify(payload)
            return checked_jsonify

        with flask_app.test_client() as client:
            with patch.object(
                web_app,
                "jsonify",
                side_effect=assert_unlocked_then_mutate(
                    lambda: state.action_log[0]["nested"].update(value="after"),
                ),
            ):
                action_snapshot = client.get("/api/log").get_json()

            with patch.object(
                web_app,
                "jsonify",
                side_effect=assert_unlocked_then_mutate(
                    lambda: state.chain2_state["injections"][0]["nested"].update(
                        value="after",
                    ),
                ),
            ):
                chain_snapshot = client.get("/api/chain2/status").get_json()

            def mutate_status_state() -> None:
                state.targets["wifi:snapshot"]["details"]["value"] = "after"
                state.hw_status["nested"]["value"] = "after"

            with patch.object(
                web_app,
                "jsonify",
                side_effect=assert_unlocked_then_mutate(mutate_status_state),
            ):
                status_snapshot = client.get("/api/status").get_json()

        self.assertEqual(action_snapshot[0]["nested"]["value"], "before")
        self.assertEqual(
            chain_snapshot["injections"][0]["nested"]["value"], "before",
        )
        self.assertEqual(
            chain_snapshot["last_injection"]["nested"]["value"], "before",
        )
        self.assertEqual(
            status_snapshot["targets"][0]["details"]["value"], "before",
        )
        self.assertEqual(status_snapshot["hardware"]["nested"]["value"], "before")

    def test_parallel_event_writers_keep_an_exact_bounded_tail(self) -> None:
        _, state = self.make_instance()
        workers = 8
        writes_per_worker = 80
        total = workers * writes_per_worker

        def write_events(worker: int) -> None:
            for sequence in range(writes_per_worker):
                payload = {"worker": worker, "nested": [sequence]}
                web_app._push_event(state, "parallel", payload)
                payload["nested"][0] = -1

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(write_events, range(workers)))

        with state.state_lock:
            retained = list(state.events)
            next_event_id = state.next_event_id
        ids = [event["id"] for event in retained]
        self.assertEqual(next_event_id, total)
        self.assertEqual(len(retained), web_app.MAX_EVENTS)
        self.assertEqual(ids, list(range(total - web_app.MAX_EVENTS, total)))
        self.assertTrue(
            all(event["data"]["nested"][0] >= 0 for event in retained),
        )

    def test_sse_replays_only_the_retained_tail_after_rollover(self) -> None:
        flask_app, state = self.make_instance()
        with patch.object(web_app, "MAX_EVENTS", 5):
            for index in range(8):
                web_app._push_event(state, "rollover", {"index": index})

            response = flask_app.test_client().get(
                "/api/events?last_id=-1", buffered=False,
            )
            try:
                stream = iter(response.response)
                self.assertEqual(next(stream).decode(), ": connected\n\n")
                chunks = [next(stream).decode() for _ in range(5)]
            finally:
                response.close()

        ids = [
            int(chunk.split("\n", 1)[0].removeprefix("id: "))
            for chunk in chunks
        ]
        self.assertEqual(ids, [3, 4, 5, 6, 7])
        self.assertTrue(all("event: rollover" in chunk for chunk in chunks))

    def test_open_sse_stream_continues_after_runtime_reset(self) -> None:
        flask_app, state = self.make_instance()
        web_app._push_event(state, "before_reset", {"phase": "before"})
        response = flask_app.test_client().get(
            "/api/events?last_id=-1", buffered=False,
        )
        try:
            stream = iter(response.response)
            self.assertEqual(next(stream).decode(), ": connected\n\n")
            before = next(stream).decode()

            state.reset_runtime()
            web_app._push_event(state, "after_reset", {"phase": "after"})
            after = next(stream).decode()
        finally:
            response.close()

        self.assertIn("id: 0", before)
        self.assertIn("event: before_reset", before)
        self.assertIn("id: 1", after)
        self.assertIn("event: after_reset", after)

    def test_regular_action_retention_and_api_window(self) -> None:
        flask_app, state = self.make_instance()
        target = make_target(target_id="wifi:bounded")
        with state.state_lock:
            state.targets[target["id"]] = target

        recorded_ids = []
        with flask_app.test_client() as client:
            for _ in range(web_app.MAX_LOG + 7):
                response = client.post(
                    "/api/targets/wifi:bounded/action",
                    json={"action": "land"},
                )
                self.assertEqual(response.status_code, 200)
                recorded_ids.append(response.get_json()["id"])
            api_ids = [entry["id"] for entry in client.get("/api/log").get_json()]

        with state.state_lock:
            retained_ids = [entry["id"] for entry in state.action_log]
        self.assertEqual(len(retained_ids), web_app.MAX_LOG)
        self.assertEqual(retained_ids, recorded_ids[-web_app.MAX_LOG:])
        self.assertEqual(api_ids, recorded_ids[-50:])

    def test_chain2_oversized_batch_bounds_both_histories(self) -> None:
        flask_app, state = self.make_instance()
        command_count = web_app.MAX_CHAIN2_INJECTIONS + 7
        response = flask_app.test_client().post(
            "/api/chain2/simulate",
            json={"target_id": "batch", "commands": ["land"] * command_count},
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [entry["id"] for entry in response.get_json()["results"]]

        with state.state_lock:
            injection_ids = [
                entry["id"] for entry in state.chain2_state["injections"]
            ]
            action_ids = [entry["id"] for entry in state.action_log]
            last_injection = state.chain2_state["last_injection"]
        self.assertEqual(len(injection_ids), web_app.MAX_CHAIN2_INJECTIONS)
        self.assertEqual(len(action_ids), web_app.MAX_LOG)
        self.assertEqual(
            injection_ids, result_ids[-web_app.MAX_CHAIN2_INJECTIONS:],
        )
        self.assertEqual(action_ids, result_ids[-web_app.MAX_LOG:])
        self.assertEqual(last_injection["id"], result_ids[-1])

    def test_empty_chain2_batch_preserves_the_retained_tail(self) -> None:
        flask_app, state = self.make_instance()
        with flask_app.test_client() as client:
            valid = client.post(
                "/api/chain2/simulate",
                json={"target_id": "first", "commands": ["land"]},
            )
            empty = client.post(
                "/api/chain2/simulate",
                json={"target_id": "second", "commands": []},
            )
            status = client.get("/api/chain2/status").get_json()

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.get_json()["results"], [])
        self.assertEqual(len(status["injections"]), 1)
        self.assertEqual(status["last_injection"], status["injections"][-1])
        with state.state_lock:
            self.assertEqual(
                state.chain2_state["last_injection"],
                state.chain2_state["injections"][-1],
            )

    def test_mixed_parallel_writers_never_publish_a_torn_snapshot(self) -> None:
        flask_app, state = self.make_instance()
        target = make_target(target_id="wifi:parallel")
        with state.state_lock:
            state.targets[target["id"]] = target

        writer_count = 8
        barrier = threading.Barrier(writer_count + 1)

        def regular_writer(_: int) -> list[int]:
            barrier.wait(timeout=10)
            with flask_app.test_client() as client:
                return [
                    client.post(
                        "/api/targets/wifi:parallel/action",
                        json={"action": "land"},
                    ).status_code
                    for _ in range(60)
                ]

        def chain_writer(worker: int) -> list[int]:
            barrier.wait(timeout=10)
            with flask_app.test_client() as client:
                return [
                    client.post(
                        "/api/chain2/simulate",
                        json={"target_id": str(worker), "commands": ["land"]},
                    ).status_code
                    for _ in range(60)
                ]

        def snapshot_reader() -> list[tuple[int, bool]]:
            barrier.wait(timeout=10)
            snapshots = []
            with flask_app.test_client() as client:
                for _ in range(300):
                    chain2 = client.get("/api/chain2/status").get_json()
                    injections = chain2["injections"]
                    snapshots.append((
                        len(injections),
                        not injections
                        or chain2["last_injection"] == injections[-1],
                    ))
                    self.assertLessEqual(
                        len(client.get("/api/log").get_json()), 50,
                    )
            return snapshots

        with ThreadPoolExecutor(max_workers=writer_count + 1) as pool:
            futures = [
                pool.submit(regular_writer, worker) for worker in range(4)
            ]
            futures.extend(
                pool.submit(chain_writer, worker) for worker in range(4, 8)
            )
            reader = pool.submit(snapshot_reader)
            statuses = [status for future in futures for status in future.result()]
            snapshots = reader.result()

        self.assertEqual(set(statuses), {200})
        self.assertTrue(all(valid for _, valid in snapshots))
        self.assertTrue(
            all(length <= web_app.MAX_CHAIN2_INJECTIONS for length, _ in snapshots),
        )
        with state.state_lock:
            self.assertEqual(len(state.action_log), web_app.MAX_LOG)
            self.assertEqual(
                len(state.chain2_state["injections"]),
                web_app.MAX_CHAIN2_INJECTIONS,
            )
            self.assertEqual(
                state.chain2_state["last_injection"],
                state.chain2_state["injections"][-1],
            )

    def test_history_helpers_detach_nested_caller_data(self) -> None:
        _, state = self.make_instance()
        action = {"id": "a", "nested": {"values": [1]}}
        injection = {"id": "i", "nested": {"values": [2]}}
        chain_action = {"id": "i", "nested": {"values": [3]}}
        web_app._record_action(state, action)
        web_app._record_chain2_history(state, [injection], [chain_action])

        action["nested"]["values"].append(10)
        injection["nested"]["values"].append(20)
        chain_action["nested"]["values"].append(30)

        with state.state_lock:
            self.assertEqual(state.action_log[0]["nested"]["values"], [1])
            self.assertEqual(state.action_log[1]["nested"]["values"], [3])
            self.assertEqual(
                state.chain2_state["injections"][0]["nested"]["values"], [2],
            )
            action_event = next(
                event for event in state.events
                if event["type"] == "action_executed"
            )
            self.assertEqual(action_event["data"]["nested"]["values"], [1])


if __name__ == "__main__":
    unittest.main()
