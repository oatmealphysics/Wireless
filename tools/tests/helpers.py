"""Shared test helpers for exploit chain and scanner tests.

Provides path setup, factory functions for common test fixtures,
and Flask test client utilities.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parents[1]
CHAINS_DIR = TOOLS_DIR / "exploit_chains"
WEB_DIR = TOOLS_DIR.parent / "web"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(CHAINS_DIR) not in sys.path:
    sys.path.insert(0, str(CHAINS_DIR))
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))


def make_logger(name: str = "test") -> ChainLogger:
    """Create a ChainLogger for tests (no file output)."""
    from exploit_chains.common import ChainLogger
    return ChainLogger(name, log_dir=None)


def make_abort(name: str = "test") -> AbortHandler:
    """Create an AbortHandler backed by a test logger."""
    from exploit_chains.common import AbortHandler
    return AbortHandler(make_logger(name))


def make_target(
    target_id: str = "wifi:test",
    protocol: str = "wifi",
    name: str = "TestDrone",
    drone_type: str = "tello",
    chain: str = "wifi_tello",
    actions: list[str] | None = None,
    score: int = 95,
    threat_level: str = "CRITICAL",
    vulns: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a target dict suitable for injection into web_app.targets."""
    now = time.time()
    return {
        "id": target_id,
        "protocol": protocol,
        "name": name,
        "type": "Test",
        "drone_type": drone_type,
        "signal": -50,
        "encryption": "Open",
        "threat_level": threat_level,
        "vulns": vulns if vulns is not None else ["OPEN_WIFI"],
        "details": details if details is not None else {},
        "available_actions": actions if actions is not None else ["land", "streamon"],
        "chain": chain,
        "first_seen": now,
        "last_seen": now,
        "score": score,
    }


try:
    import flask as _flask  # noqa: F401
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

needs_flask = unittest.skipUnless(HAS_FLASK, "Flask not installed")


def get_web_app() -> Any:
    """Import and return the web app module, or None if Flask is missing."""
    if not HAS_FLASK:
        return None
    import app as web_app
    return web_app


def make_flask_client(
    web_app: Any,
    simulate: bool = True,
    dry_run: bool = True,
) -> Any:
    """Return a test client backed by a fresh application and state."""
    state = web_app.create_dashboard_state(
        simulate=simulate,
        dry_run=dry_run,
    )
    flask_app = web_app.create_app(state)
    flask_app.testing = True
    return flask_app.test_client()


def arm_pack(
    client: Any,
    target_id: str,
    *,
    chain: str = "wifi_tello",
    actions: list[str] | None = None,
    version: str = "0.1.0",
    sacrificial: bool = True,
    time_limit: int = 300,
) -> Any:
    """Arm a simulation pack's scope so a subsequent action POST is authorized.

    Authorization is always enforced for execution, so tests that drive an
    action through the registry must first create a time-limited scope. The
    target must already be present in ``state.targets`` before calling this.
    """
    pack_name = f"sim-{chain}"
    if actions is None:
        actions = ["land", "streamon", "emergency", "takeoff", "rc_zero"]
    return client.post(
        f"/api/packs/{pack_name}/{version}/arm",
        json={
            "target_identity": target_id,
            "permitted_actions": list(actions),
            "time_limit_seconds": time_limit,
            "stop_conditions": ["operator_abort"],
            "sacrificial_target": sacrificial,
            "evidence_destination": "test",
        },
    )
