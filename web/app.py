#!/usr/bin/env python3
"""
Drone Scanner Web UI

Flask application that wraps the existing recon engine and exploit chain
modules into a unified browser-based dashboard.

Usage:
    python3 web/app.py                        # default on port 5000
    python3 web/app.py --port 8080            # custom port
    python3 web/app.py --simulate             # demo mode, no hardware

For authorized CTF research only.
"""

import argparse
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "exploit_chains"))

from exploit_chains.common import ChainLogger, AbortHandler

app = Flask(__name__)

# Global state
scan_engine = None
scan_lock = threading.Lock()
targets = {}
action_log = []
scanner_status = {"wifi": "idle", "ble": "idle", "sdr": "idle"}
is_scanning = False
simulate_mode = False


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
        "icon": "arrow-down",
    },
    "emergency": {
        "label": "Emergency Stop",
        "description": "Kill all motors immediately (drone falls)",
        "severity": "danger",
        "icon": "x-octagon",
    },
    "takeoff": {
        "label": "Takeoff",
        "description": "Command drone to take off",
        "severity": "warning",
        "icon": "arrow-up",
    },
    "streamon": {
        "label": "Capture Video",
        "description": "Enable video stream from drone camera",
        "severity": "info",
        "icon": "video",
    },
    "rc_zero": {
        "label": "Zero RC",
        "description": "Set all RC inputs to zero (hover/stop)",
        "severity": "warning",
        "icon": "pause",
    },
    "disarm": {
        "label": "Disarm Motors",
        "description": "Disarm the flight controller motors",
        "severity": "danger",
        "icon": "shield-off",
    },
    "rtl": {
        "label": "Return to Launch",
        "description": "Command drone to return to its launch position",
        "severity": "warning",
        "icon": "home",
    },
    "reboot": {
        "label": "Reboot FC",
        "description": "Reboot the flight controller",
        "severity": "danger",
        "icon": "refresh-cw",
    },
    "emergency_watchdog": {
        "label": "Watchdog Reset",
        "description": "Send AT*COMWDG watchdog reset (Parrot)",
        "severity": "danger",
        "icon": "alert-triangle",
    },
    "kill_control": {
        "label": "Kill Control Process",
        "description": "Kill the drone's main control process via Telnet",
        "severity": "danger",
        "icon": "x-circle",
    },
    "forge_disarm": {
        "label": "Forge Disarm Packet",
        "description": "Build forged RC disarm packet using recovered binding params",
        "severity": "danger",
        "icon": "zap",
    },
    "preview_fhss": {
        "label": "Preview FHSS Sequence",
        "description": "Show the computed FHSS hop sequence",
        "severity": "info",
        "icon": "radio",
    },
    "scan_deeper": {
        "label": "Deep ELRS Scan",
        "description": "Extended scan for SYNC packet capture",
        "severity": "info",
        "icon": "search",
    },
    "ble_scan_services": {
        "label": "Scan BLE Services",
        "description": "Enumerate BLE GATT services and characteristics",
        "severity": "info",
        "icon": "bluetooth",
    },
}


def run_scan_simulated():
    """Run a simulated scan for demo mode."""
    global targets, is_scanning, scanner_status
    import random

    scanner_status = {"wifi": "scanning", "ble": "scanning", "sdr": "scanning"}

    time.sleep(1.5)
    scanner_status["wifi"] = "done"
    time.sleep(1.0)
    scanner_status["ble"] = "done"
    time.sleep(1.5)
    scanner_status["sdr"] = "done"

    with scan_lock:
        now = time.time()
        for t in SIMULATED_TARGETS:
            target = dict(t)
            target["first_seen"] = now - random.uniform(0, 10)
            target["last_seen"] = now
            target["score"] = _score_target(target)
            targets[target["id"]] = target

    is_scanning = False
    scanner_status = {"wifi": "idle", "ble": "idle", "sdr": "idle"}


def run_scan_live(protocols):
    """Run live scanning using the existing multi_recon scanners."""
    global targets, is_scanning, scanner_status

    from exploit_chains.multi_recon import scan_wifi, scan_ble, scan_sdr

    logger = ChainLogger("web-scan")
    abort = AbortHandler(logger)

    all_targets = []

    if "wifi" in protocols:
        scanner_status["wifi"] = "scanning"
        try:
            wifi_results = scan_wifi("wlan0", 15.0, logger, abort)
            all_targets.extend(wifi_results)
        except Exception:
            pass
        scanner_status["wifi"] = "done"

    if "ble" in protocols:
        scanner_status["ble"] = "scanning"
        try:
            ble_results = scan_ble("hci0", 10.0, logger, abort)
            all_targets.extend(ble_results)
        except Exception:
            pass
        scanner_status["ble"] = "done"

    if "sdr" in protocols:
        scanner_status["sdr"] = "scanning"
        try:
            sdr_results = scan_sdr("driver=uhd", 15.0, logger, abort)
            all_targets.extend(sdr_results)
        except Exception:
            pass
        scanner_status["sdr"] = "done"

    with scan_lock:
        now = time.time()
        for t in all_targets:
            tid = _make_target_id(t)
            target = _convert_recon_target(t, tid, now)
            targets[tid] = target

    is_scanning = False
    scanner_status = {"wifi": "idle", "ble": "idle", "sdr": "idle"}


def _make_target_id(t):
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


def _convert_recon_target(t, tid, now):
    proto = t.get("protocol", "unknown")
    chain_map = {
        "wifi": "wifi_tello",
        "ble": "dji_ble",
        "elrs": "elrs",
        "mavlink_udp": "mavlink",
        "sik_915": "mavlink",
        "sik_433": "mavlink",
    }
    action_map = {
        "wifi_tello": ["land", "emergency", "takeoff", "streamon"],
        "mavlink": ["disarm", "land", "rtl", "reboot"],
        "elrs": ["forge_disarm", "preview_fhss", "scan_deeper"],
        "dji_ble": ["ble_scan_services"],
    }
    chain = chain_map.get(proto, "unknown")
    return {
        "id": tid,
        "protocol": proto,
        "name": t.get("ssid") or t.get("name") or t.get("description", "Unknown"),
        "type": t.get("description", "Unknown"),
        "drone_type": t.get("drone_type", "unknown"),
        "signal": None,
        "encryption": t.get("encryption", "Unknown"),
        "threat_level": _threat_from_score(t),
        "vulns": [],
        "details": t,
        "available_actions": action_map.get(chain, []),
        "chain": chain,
        "first_seen": now,
        "last_seen": now,
        "score": _score_target(t),
    }


def _threat_from_score(t):
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


def _score_target(t):
    dtype = t.get("drone_type", "unknown")
    base = EXPLOITABILITY_SCORES.get(dtype, 30)
    if t.get("is_open"):
        base = min(100, base + 10)
    if t.get("signed") is False:
        base = min(100, base + 15)
    return base


def execute_action_simulated(target, action_name):
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
    action_log.append(entry)
    return entry


# --- Flask routes ---

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    with scan_lock:
        target_list = sorted(
            targets.values(),
            key=lambda t: t.get("score", 0),
            reverse=True,
        )
    return jsonify({
        "scanning": is_scanning,
        "scanner_status": scanner_status,
        "target_count": len(target_list),
        "targets": target_list,
        "simulate": simulate_mode,
    })


@app.route("/api/scan", methods=["POST"])
def api_scan():
    global is_scanning
    if is_scanning:
        return jsonify({"error": "Scan already in progress"}), 409

    is_scanning = True
    protocols = request.json.get("protocols", ["wifi", "ble", "sdr"]) if request.json else ["wifi", "ble", "sdr"]

    if simulate_mode:
        t = threading.Thread(target=run_scan_simulated, daemon=True)
    else:
        t = threading.Thread(target=run_scan_live, args=(protocols,), daemon=True)
    t.start()

    return jsonify({"status": "scanning", "protocols": protocols})


@app.route("/api/targets/<path:target_id>")
def api_target_detail(target_id):
    with scan_lock:
        target = targets.get(target_id)
    if not target:
        return jsonify({"error": "Target not found"}), 404
    return jsonify(target)


@app.route("/api/targets/<path:target_id>/action", methods=["POST"])
def api_target_action(target_id):
    with scan_lock:
        target = targets.get(target_id)
    if not target:
        return jsonify({"error": "Target not found"}), 404

    action_name = request.json.get("action") if request.json else None
    if not action_name:
        return jsonify({"error": "No action specified"}), 400

    if action_name not in target.get("available_actions", []):
        return jsonify({"error": f"Action '{action_name}' not available for this target"}), 400

    result = execute_action_simulated(target, action_name)
    return jsonify(result)


@app.route("/api/actions")
def api_action_defs():
    return jsonify(ACTION_DEFS)


@app.route("/api/log")
def api_log():
    return jsonify(action_log[-50:])


@app.route("/api/clear", methods=["POST"])
def api_clear():
    global targets
    with scan_lock:
        targets = {}
    return jsonify({"status": "cleared"})


def main():
    global simulate_mode

    parser = argparse.ArgumentParser(description="Drone Scanner Web UI")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--simulate", action="store_true",
                        help="Demo mode with simulated targets")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    simulate_mode = args.simulate
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
