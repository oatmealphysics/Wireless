"""Tests for Ticket 04: Authorization and evidence dashboard UI.

Exercises the backend API endpoints for pack arming/disarming,
scope listing, evidence listing/detail, DashboardState wiring,
frontend DOM structure, and CLI flag deprecation.

Seams under test:
1. Flask test client against /api/packs/.../arm, disarm, /api/scopes,
   /api/evidence, /api/evidence/<id>
2. DashboardState wiring: ScopeManager and EvidenceStore accessible
3. Frontend template: DOM elements for pack/arming/evidence UI
4. CLI: --live-actions flag removed from argparse
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "tests"))

from helpers import arm_pack, get_web_app, make_flask_client, make_target, needs_flask

web_app = get_web_app()


# ---------------------------------------------------------------------------
# Seam 1: DashboardState wiring
# ---------------------------------------------------------------------------

@needs_flask
class TestDashboardStateWiring(unittest.TestCase):
    """DashboardState exposes ScopeManager and EvidenceStore."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        self.state = web_app.get_dashboard_state(self.client.application)

    def test_state_has_scope_manager(self) -> None:
        self.assertIsNotNone(self.state.scope_manager)
        self.assertIsInstance(self.state.scope_manager, web_app.ScopeManager)

    def test_state_has_evidence_store(self) -> None:
        self.assertIsNotNone(self.state.evidence_store)
        self.assertIsInstance(self.state.evidence_store, web_app.EvidenceStore)

    def test_scope_manager_uses_pack_registry(self) -> None:
        """ScopeManager should be wired to the same registry as the state."""
        self.assertIs(
            self.state.scope_manager._registry,
            self.state.pack_registry,
        )


# ---------------------------------------------------------------------------
# Seam 2: Pack arm endpoint
# ---------------------------------------------------------------------------

@needs_flask
class TestArmEndpoint(unittest.TestCase):
    """POST /api/packs/<name>/<version>/arm creates a scoped authorization."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        self.state = web_app.get_dashboard_state(self.client.application)
        # Seed a target so the arm endpoint has something to authorize against
        self.state.targets["wifi:t1"] = make_target(
            target_id="wifi:t1",
            chain="wifi_tello",
        )

    def test_arm_creates_scope(self) -> None:
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land", "streamon"],
                "time_limit_seconds": 300,
                "stop_conditions": ["operator_abort"],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "armed")
        self.assertIn("expiry", data)
        self.assertIn("scope_key", data)

    def test_arm_returns_400_for_missing_fields(self) -> None:
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={"target_identity": "wifi:t1"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_arm_returns_404_for_unknown_pack(self) -> None:
        resp = self.client.post(
            "/api/packs/nonexistent/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_arm_destructive_pack_without_sacrificial_returns_400(self) -> None:
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": False,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("destructive", resp.get_json()["error"].lower())

    def test_arm_browser_payload_without_applicability_fields(self) -> None:
        """The browser arm request omits protocol/firmware fields; it must work.

        Regression for the broken-arming finding: the server previously
        required client-asserted applicability fields, so the real browser
        request returned HTTP 400 and arming could never complete.
        """
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land", "streamon"],
                "time_limit_seconds": 300,
                "stop_conditions": ["operator_abort"],
                "sacrificial_target": True,
                "evidence_destination": "default",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "armed")

    def test_arm_rejects_stringified_sacrificial_target(self) -> None:
        """A truthy string such as "false" must not satisfy the bool check."""
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": "false",
                "evidence_destination": "test",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("sacrificial_target", resp.get_json()["error"])

    def test_arm_rejects_non_numeric_time_limit(self) -> None:
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": "300",
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("time_limit_seconds", resp.get_json()["error"])

    def test_arm_rejects_time_limit_over_max(self) -> None:
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": web_app.MAX_ARM_SECONDS + 1,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_arm_rejects_unknown_target(self) -> None:
        """You can only arm against a target the scanner actually discovered."""
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:never-scanned",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Target not found", resp.get_json()["error"])


# ---------------------------------------------------------------------------
# Seam 3: Pack disarm endpoint
# ---------------------------------------------------------------------------

@needs_flask
class TestDisarmEndpoint(unittest.TestCase):
    """POST /api/packs/<name>/<version>/disarm revokes authorization."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        self.state = web_app.get_dashboard_state(self.client.application)
        self.state.targets["wifi:t1"] = make_target(
            target_id="wifi:t1",
            chain="wifi_tello",
        )

    def _arm(self) -> dict:
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        return resp.get_json()

    def test_disarm_revokes_scope(self) -> None:
        self._arm()
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/disarm",
            json={"target_identity": "wifi:t1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "disarmed")

    def test_disarm_nonexistent_returns_200(self) -> None:
        """Disarming a non-existent scope is idempotent."""
        resp = self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/disarm",
            json={"target_identity": "wifi:nonexistent"},
        )
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Seam 4: Scopes listing endpoint
# ---------------------------------------------------------------------------

@needs_flask
class TestScopesEndpoint(unittest.TestCase):
    """GET /api/scopes lists active authorization scopes."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        self.state = web_app.get_dashboard_state(self.client.application)
        self.state.targets["wifi:t1"] = make_target(
            target_id="wifi:t1",
            chain="wifi_tello",
        )

    def test_scopes_empty_initially(self) -> None:
        resp = self.client.get("/api/scopes")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_scopes_lists_armed_scope(self) -> None:
        self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        resp = self.client.get("/api/scopes")
        data = resp.get_json()
        self.assertEqual(len(data), 1)
        scope = data[0]
        self.assertEqual(scope["pack_name"], "sim-wifi_tello")
        self.assertEqual(scope["target_identity"], "wifi:t1")
        self.assertIn("expiry", scope)
        self.assertIn("time_remaining_seconds", scope)
        self.assertIn("is_expired", scope)
        self.assertFalse(scope["is_expired"])
        self.assertFalse(scope["disarmed"])

    def test_scopes_shows_disarmed(self) -> None:
        self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/disarm",
            json={"target_identity": "wifi:t1"},
        )
        resp = self.client.get("/api/scopes")
        data = resp.get_json()
        self.assertEqual(len(data), 1)
        self.assertTrue(data[0]["disarmed"])


# ---------------------------------------------------------------------------
# Seam 5: Evidence endpoints
# ---------------------------------------------------------------------------

@needs_flask
class TestEvidenceEndpoints(unittest.TestCase):
    """GET /api/evidence and GET /api/evidence/<id>."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        self.state = web_app.get_dashboard_state(self.client.application)

    def test_evidence_empty_initially(self) -> None:
        resp = self.client.get("/api/evidence")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_evidence_lists_records_after_execution(self) -> None:
        """After an action executes through the pack system, evidence is recorded."""
        self.state.targets["wifi:t1"] = make_target(
            target_id="wifi:t1",
            chain="wifi_tello",
        )
        # Arm the pack
        self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        # Execute an action (this should create an evidence record)
        self.client.post(
            "/api/targets/wifi:t1/action",
            json={"action": "land"},
        )
        resp = self.client.get("/api/evidence")
        data = resp.get_json()
        self.assertGreaterEqual(len(data), 1)
        record = data[0]
        self.assertIn("id", record)
        self.assertIn("capability_version", record)
        self.assertIn("target_identity", record)
        self.assertIn("transition_log", record)

    def test_evidence_detail_returns_record(self) -> None:
        self.state.targets["wifi:t1"] = make_target(
            target_id="wifi:t1",
            chain="wifi_tello",
        )
        self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        self.client.post(
            "/api/targets/wifi:t1/action",
            json={"action": "land"},
        )
        evidence_list = self.client.get("/api/evidence").get_json()
        if evidence_list:
            record_id = evidence_list[0]["id"]
            resp = self.client.get(f"/api/evidence/{record_id}")
            self.assertEqual(resp.status_code, 200)
            detail = resp.get_json()
            self.assertEqual(detail["id"], record_id)
            self.assertIn("verification_evidence", detail)
            self.assertIn("stop_reason", detail)

    def test_evidence_detail_404_for_unknown(self) -> None:
        resp = self.client.get("/api/evidence/nonexistent")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Seam 6: Result State display constraints
# ---------------------------------------------------------------------------

@needs_flask
class TestResultStateDisplay(unittest.TestCase):
    """The UI never collapses ATTEMPTED or TRANSMITTED into higher states."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        self.state = web_app.get_dashboard_state(self.client.application)
        self.state.targets["wifi:t1"] = make_target(
            target_id="wifi:t1",
            chain="wifi_tello",
        )

    def test_simulation_never_claims_target_accepted(self) -> None:
        """Simulation executor must not report TARGET_ACCEPTED."""
        resp = self.client.post(
            "/api/targets/wifi:t1/action",
            json={"action": "land"},
        )
        data = resp.get_json()
        self.assertNotEqual(data["result_state"], "target_accepted")
        self.assertNotEqual(data["result_state"], "effect_confirmed")
        for entry in data.get("transition_log", []):
            self.assertNotIn(entry["state"], ("target_accepted", "effect_confirmed"))

    def test_evidence_record_never_has_inflated_states(self) -> None:
        """Evidence records from simulation must not contain inflated states."""
        self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/arm",
            json={
                "target_identity": "wifi:t1",
                "permitted_actions": ["land"],
                "time_limit_seconds": 300,
                "stop_conditions": [],
                "sacrificial_target": True,
                "evidence_destination": "test",
                "protocol_version": "*",
                "firmware_impl": "*",
                "firmware_version": "*",
            },
        )
        self.client.post(
            "/api/targets/wifi:t1/action",
            json={"action": "land"},
        )
        evidence = self.client.get("/api/evidence").get_json()
        for record in evidence:
            for entry in record.get("transition_log", []):
                state = entry["state"] if isinstance(entry, dict) else entry[0]
                self.assertNotIn(state, ("target_accepted", "effect_confirmed"))


# ---------------------------------------------------------------------------
# Seam 7: CLI flag deprecation
# ---------------------------------------------------------------------------

@needs_flask
class TestCLIFlagDeprecation(unittest.TestCase):
    """The --live-actions CLI flag should be removed or deprecated."""

    def test_live_actions_flag_removed_from_argparse(self) -> None:
        """The argparse section should not have --live-actions."""
        import inspect
        source = inspect.getsource(web_app.main)
        self.assertNotIn("--live-actions", source)


# ---------------------------------------------------------------------------
# Seam 8: Frontend DOM structure
# ---------------------------------------------------------------------------

@needs_flask
class TestFrontendDOMStructure(unittest.TestCase):
    """The HTML template contains elements for the authorization UI."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)

    def test_html_has_pack_listing_panel(self) -> None:
        resp = self.client.get("/")
        html = resp.get_data(as_text=True)
        self.assertIn("pack-list", html)

    def test_html_has_arming_dialog(self) -> None:
        resp = self.client.get("/")
        html = resp.get_data(as_text=True)
        self.assertIn("arm-dialog", html)

    def test_html_has_scope_indicator(self) -> None:
        resp = self.client.get("/")
        html = resp.get_data(as_text=True)
        self.assertIn("scope-indicator", html)

    def test_html_has_evidence_viewer(self) -> None:
        resp = self.client.get("/")
        html = resp.get_data(as_text=True)
        self.assertIn("evidence-viewer", html)

    def test_html_has_destructive_confirmation(self) -> None:
        resp = self.client.get("/")
        html = resp.get_data(as_text=True)
        self.assertIn("destructive-confirm", html)

    def test_html_has_result_state_display(self) -> None:
        resp = self.client.get("/")
        html = resp.get_data(as_text=True)
        self.assertIn("result-state-display", html)


# ---------------------------------------------------------------------------
# Seam 9: Authorization is always enforced for execution
# ---------------------------------------------------------------------------

@needs_flask
class TestAuthorizationEnforcement(unittest.TestCase):
    """Execution requires an armed scope even in simulate/dry-run mode."""

    def setUp(self) -> None:
        # Explicitly simulate + dry-run: the modes that previously waived auth.
        self.client = make_flask_client(web_app, simulate=True, dry_run=True)
        self.state = web_app.get_dashboard_state(self.client.application)
        self.state.targets["wifi:t1"] = make_target(
            target_id="wifi:t1", chain="wifi_tello", actions=["land", "streamon"],
        )

    def test_unarmed_action_denied_with_403(self) -> None:
        resp = self.client.post(
            "/api/targets/wifi:t1/action", json={"action": "land"},
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertIsNone(data["result_state"])
        self.assertEqual(data["stop_reason"], "authorization_denied")

    def test_unarmed_action_creates_no_evidence(self) -> None:
        self.client.post("/api/targets/wifi:t1/action", json={"action": "land"})
        self.assertEqual(self.client.get("/api/evidence").get_json(), [])

    def test_unarmed_action_creates_no_log_entry(self) -> None:
        self.client.post("/api/targets/wifi:t1/action", json={"action": "land"})
        with self.state.state_lock:
            self.assertEqual(self.state.action_log, [])

    def test_preview_is_exempt_from_arming(self) -> None:
        """Preview is probe-free and side-effect-free, so it needs no scope."""
        resp = self.client.post(
            "/api/targets/wifi:t1/action",
            json={"action": "land", "preview": True},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["result_state"], "previewed")

    def test_armed_action_succeeds(self) -> None:
        arm_pack(self.client, "wifi:t1", actions=["land"])
        resp = self.client.post(
            "/api/targets/wifi:t1/action", json={"action": "land"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["result_state"], "attempted")

    def test_disarmed_scope_denies_action(self) -> None:
        arm_pack(self.client, "wifi:t1", actions=["land"])
        self.client.post(
            "/api/packs/sim-wifi_tello/0.1.0/disarm",
            json={"target_identity": "wifi:t1"},
        )
        resp = self.client.post(
            "/api/targets/wifi:t1/action", json={"action": "land"},
        )
        self.assertEqual(resp.status_code, 403)


@needs_flask
class TestExecutionCancel(unittest.TestCase):
    """POST /api/executions/<id>/cancel controls the bounded-worker token."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        self.state = web_app.get_dashboard_state(self.client.application)

    def test_cancel_unknown_execution_returns_404(self) -> None:
        resp = self.client.post("/api/executions/deadbeef/cancel")
        self.assertEqual(resp.status_code, 404)

    def test_cancel_sets_inflight_token(self) -> None:
        import threading
        event = threading.Event()
        with self.state.state_lock:
            self.state.executions["exec-1"] = event
        resp = self.client.post("/api/executions/exec-1/cancel")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "cancel_requested")
        self.assertTrue(event.is_set())


@needs_flask
class TestPack1Registration(unittest.TestCase):
    """Pack 1 (ELRS v3 OTA Injection) is registered in the app registry."""

    def setUp(self) -> None:
        self.client = make_flask_client(web_app)
        self.state = web_app.get_dashboard_state(self.client.application)

    def test_elrs_v3_pack_present_in_registry(self) -> None:
        pack = self.state.pack_registry.lookup("ELRS v3 OTA Injection", "0.1.0")
        self.assertIsNotNone(pack)

    def test_elrs_v3_pack_matches_betaflight_v3_target(self) -> None:
        results = self.state.pack_registry.query({
            "protocol_version": "ELRS v3 OTA",
            "firmware_impl": "Betaflight",
            "firmware_version": "4.4.2",
        })
        names = {p.identity.name for p in results}
        self.assertIn("ELRS v3 OTA Injection", names)


if __name__ == "__main__":
    unittest.main()
