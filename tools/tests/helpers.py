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


def make_logger(name: str = "test") -> "ChainLogger":
    """Create a ChainLogger for tests (no file output)."""
    from exploit_chains.common import ChainLogger
    return ChainLogger(name, log_dir=None)


def make_abort(name: str = "test") -> "AbortHandler":
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
    """Reset web_app state and return a Flask test client."""
    web_app.simulate_mode = simulate
    web_app.dry_run_mode = dry_run
    web_app.is_scanning = False
    web_app.targets = {}
    web_app.action_log = []
    web_app.scanner_status = {name: "idle" for name in web_app.DEFAULT_PROTOCOLS}
    web_app.app.testing = True
    return web_app.app.test_client()
