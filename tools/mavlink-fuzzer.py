#!/usr/bin/env python3
"""
MAVLink Protocol Fuzzer — CTF Reconnaissance & Boundary Testing

Systematically probes a MAVLink target to discover accepted commands,
test parameter boundaries, and detect whether signing is enforced.
Designed for authorized CTF competition and security research against
equipment you own.

Capabilities:
  1. Signing detection — determines if the target requires MAVLink v2 signing
  2. Command enumeration — sends each MAV_CMD and records ACK/NAK/timeout
  3. Boundary testing — pushes parameter min/max/NaN/negative on accepted commands
  4. State-change identification — flags commands that alter vehicle state
     (arm/disarm, mode changes, waypoint injection, reboot)

Modes:
  --passive (default)  Listen only; detect signing from observed traffic
  --active             Transmit probes (command enum, boundary tests)

Requires: pymavlink (pip install pymavlink)

Targets:
  UDP:     udp:TARGET_IP:14550   (most common for WiFi telemetry)
  TCP:     tcp:TARGET_IP:5760
  Serial:  /dev/ttyUSB0          (lab only — SiK radio or USB)

Usage:
    # Passive — sniff traffic and detect signing status
    python3 mavlink-fuzzer.py udp:192.168.1.1:14550

    # Active — enumerate accepted commands
    python3 mavlink-fuzzer.py udp:192.168.1.1:14550 --active

    # Active — enumerate + boundary fuzz accepted commands
    python3 mavlink-fuzzer.py udp:192.168.1.1:14550 --active --fuzz-params

    # Target specific commands only
    python3 mavlink-fuzzer.py udp:192.168.1.1:14550 --active --commands 400,176,20

    # Output results to JSON
    python3 mavlink-fuzzer.py udp:192.168.1.1:14550 --active -o results.json

    # Serial (lab, needs battery-powered target)
    python3 mavlink-fuzzer.py /dev/ttyUSB0 --baud 57600 --active
"""

import argparse
import json
import math
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    from pymavlink import mavutil
    from pymavlink.dialects.v20 import common as mavlink2
except ImportError:
    print("ERROR: pymavlink not installed. Run: pip install pymavlink", file=sys.stderr)
    sys.exit(1)


# --- MAV_CMD catalog ---
# Build {id: name} from the dialect module.
MAV_CMD_NAMES = {}
MAV_CMD_IDS = {}
for _attr in dir(mavlink2):
    if _attr.startswith("MAV_CMD_") and isinstance(getattr(mavlink2, _attr), int):
        _val = getattr(mavlink2, _attr)
        MAV_CMD_NAMES[_val] = _attr
        MAV_CMD_IDS[_attr] = _val

# Commands known to change vehicle state — these get special flagging
STATE_CHANGE_CMDS = {
    mavlink2.MAV_CMD_COMPONENT_ARM_DISARM,      # 400 — arm/disarm motors
    mavlink2.MAV_CMD_NAV_LAND,                   # 21  — land
    mavlink2.MAV_CMD_NAV_RETURN_TO_LAUNCH,       # 20  — RTL
    mavlink2.MAV_CMD_NAV_TAKEOFF,                # 22  — takeoff
    mavlink2.MAV_CMD_DO_SET_MODE,                # 176 — flight mode change
    mavlink2.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,  # 246 — reboot / shutdown
    mavlink2.MAV_CMD_DO_FLIGHTTERMINATION,       # 185 — kill switch
    mavlink2.MAV_CMD_DO_SET_HOME,                # 179 — change home position
    mavlink2.MAV_CMD_MISSION_START,              # 300 — start mission
    mavlink2.MAV_CMD_DO_PAUSE_CONTINUE,          # 193 — pause/resume
    mavlink2.MAV_CMD_NAV_WAYPOINT,               # 16  — waypoint
    mavlink2.MAV_CMD_DO_SET_SERVO,               # 183 — direct servo
    mavlink2.MAV_CMD_DO_SET_RELAY,               # 181 — relay on/off
    mavlink2.MAV_CMD_OVERRIDE_GOTO,              # 252 — override goto
}

# MAV_RESULT codes
MAV_RESULT_NAMES = {
    0: "ACCEPTED",
    1: "TEMPORARILY_REJECTED",
    2: "DENIED",
    3: "UNSUPPORTED",
    4: "FAILED",
    5: "IN_PROGRESS",
    6: "CANCELLED",
    7: "COMMAND_LONG_ONLY",
    8: "COMMAND_INT_ONLY",
    9: "COMMAND_UNSUPPORTED_MAV_FRAME",
}

# Boundary test values for parameter fuzzing
BOUNDARY_VALUES = [
    0.0,
    1.0,
    -1.0,
    255.0,
    256.0,
    65535.0,
    65536.0,
    2147483647.0,   # INT32_MAX
    -2147483648.0,  # INT32_MIN
    4294967295.0,   # UINT32_MAX
    float("nan"),
    float("inf"),
    float("-inf"),
    3.4028235e+38,  # FLT_MAX
    -3.4028235e+38,
    1.17549435e-38, # FLT_MIN (positive)
]


@dataclass
class CommandResult:
    cmd_id: int
    cmd_name: str
    result_code: int = -1
    result_name: str = "TIMEOUT"
    response_time_ms: float = 0.0
    is_state_change: bool = False
    boundary_results: list = field(default_factory=list)


@dataclass
class FuzzReport:
    target: str
    timestamp: str = ""
    mavlink_version: str = "unknown"
    signing_detected: bool = False
    signing_required: str = "unknown"
    target_system_id: int = -1
    target_component_id: int = -1
    vehicle_type: str = "unknown"
    autopilot: str = "unknown"
    firmware_version: str = ""
    total_commands_tested: int = 0
    accepted_commands: list = field(default_factory=list)
    rejected_commands: list = field(default_factory=list)
    unsupported_commands: list = field(default_factory=list)
    timed_out_commands: list = field(default_factory=list)
    state_change_commands: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def log(msg, level="INFO"):
    print(f"[{level}] {msg}", file=sys.stderr)


def connect(target, baud, source_system, source_component, timeout=10):
    log(f"Connecting to {target}...")
    try:
        conn = mavutil.mavlink_connection(
            target,
            baud=baud,
            source_system=source_system,
            source_component=source_component,
            autoreconnect=True,
        )
        conn.wait_heartbeat(timeout=timeout)
        log(f"Heartbeat received — target sysid={conn.target_system} compid={conn.target_component}")
        return conn
    except Exception as e:
        log(f"Connection failed: {e}", "ERROR")
        sys.exit(1)


def detect_signing(conn, listen_seconds=5):
    """Listen for packets and check if any carry a signature (13-byte trailer)."""
    log(f"Listening {listen_seconds}s for signed packets...")
    signed_count = 0
    unsigned_count = 0
    end = time.time() + listen_seconds

    while time.time() < end:
        msg = conn.recv_match(blocking=True, timeout=1)
        if msg is None:
            continue
        # pymavlink exposes _header on received messages
        raw = msg.get_msgbuf()
        if raw and len(raw) > 0:
            if raw[0] == 0xFD:  # MAVLink v2
                # v2 incompat_flags bit 0 = signed
                incompat = raw[2]
                if incompat & 0x01:
                    signed_count += 1
                else:
                    unsigned_count += 1
            elif raw[0] == 0xFE:  # MAVLink v1 — never signed
                unsigned_count += 1

    log(f"Signing detection: {signed_count} signed, {unsigned_count} unsigned packets")
    return signed_count, unsigned_count


def identify_target(conn):
    """Extract vehicle type and autopilot from heartbeat."""
    msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=5)
    if msg is None:
        return "unknown", "unknown"

    vehicle_types = {
        0: "GENERIC", 1: "FIXED_WING", 2: "QUADROTOR", 3: "COAXIAL",
        4: "HELICOPTER", 5: "ANTENNA_TRACKER", 6: "GCS", 10: "GROUND_ROVER",
        11: "SURFACE_BOAT", 12: "SUBMARINE", 13: "HEXAROTOR", 14: "OCTOROTOR",
        15: "TRICOPTER", 20: "VTOL",
    }
    autopilots = {
        0: "GENERIC", 3: "ARDUPILOT", 4: "OPENPILOT", 8: "INVALID",
        12: "PX4", 14: "PAPARAZZI",
    }

    vtype = vehicle_types.get(msg.type, f"TYPE_{msg.type}")
    apilot = autopilots.get(msg.autopilot, f"AUTOPILOT_{msg.autopilot}")
    return vtype, apilot


def request_version(conn, timeout=3):
    """Try to get AUTOPILOT_VERSION for firmware info."""
    conn.mav.command_long_send(
        conn.target_system, conn.target_component,
        mavlink2.MAV_CMD_REQUEST_MESSAGE,
        0,  # confirmation
        mavlink2.MAVLINK_MSG_ID_AUTOPILOT_VERSION,  # param1: msg id
        0, 0, 0, 0, 0, 0,
    )
    msg = conn.recv_match(type="AUTOPILOT_VERSION", blocking=True, timeout=timeout)
    if msg:
        ver = msg.flight_sw_version
        major = (ver >> 24) & 0xFF
        minor = (ver >> 16) & 0xFF
        patch = (ver >> 8) & 0xFF
        return f"{major}.{minor}.{patch}"
    return ""


def send_command_long(conn, cmd_id, params=None, timeout=3):
    """Send COMMAND_LONG and wait for COMMAND_ACK. Returns (result_code, response_time_ms)."""
    if params is None:
        params = [0.0] * 7
    while len(params) < 7:
        params.append(0.0)

    # Replace Python-level NaN/Inf with their float encoding
    safe_params = []
    for p in params:
        if math.isnan(p) or math.isinf(p):
            safe_params.append(p)
        else:
            safe_params.append(float(p))

    t0 = time.time()
    conn.mav.command_long_send(
        conn.target_system, conn.target_component,
        cmd_id,
        0,  # confirmation
        *safe_params[:7],
    )

    # Wait for ACK
    while True:
        ack = conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=timeout)
        if ack is None:
            return -1, (time.time() - t0) * 1000
        if ack.command == cmd_id:
            return ack.result, (time.time() - t0) * 1000
        # ACK for a different command — keep waiting


def enumerate_commands(conn, cmd_ids, throttle_ms=100):
    """Send each command with zero params and record the result."""
    results = []
    total = len(cmd_ids)

    for i, cmd_id in enumerate(cmd_ids):
        cmd_name = MAV_CMD_NAMES.get(cmd_id, f"CMD_{cmd_id}")
        is_sc = cmd_id in STATE_CHANGE_CMDS

        result_code, resp_ms = send_command_long(conn, cmd_id, timeout=2)
        result_name = MAV_RESULT_NAMES.get(result_code, f"UNKNOWN_{result_code}")

        r = CommandResult(
            cmd_id=cmd_id,
            cmd_name=cmd_name,
            result_code=result_code,
            result_name=result_name,
            response_time_ms=round(resp_ms, 1),
            is_state_change=is_sc,
        )
        results.append(r)

        marker = ""
        if is_sc:
            marker = " [STATE-CHANGE]"
        if result_code == 0:
            marker += " *** ACCEPTED ***"

        progress = f"[{i+1}/{total}]"
        log(f"{progress} {cmd_name} ({cmd_id}): {result_name} ({resp_ms:.0f}ms){marker}")

        if throttle_ms > 0:
            time.sleep(throttle_ms / 1000.0)

    return results


def fuzz_parameters(conn, cmd_id, throttle_ms=100):
    """Test boundary values on each of the 7 parameters for a given command."""
    cmd_name = MAV_CMD_NAMES.get(cmd_id, f"CMD_{cmd_id}")
    boundary_results = []

    for param_idx in range(7):
        for val in BOUNDARY_VALUES:
            params = [0.0] * 7
            params[param_idx] = val

            result_code, resp_ms = send_command_long(conn, cmd_id, params=params, timeout=2)
            result_name = MAV_RESULT_NAMES.get(result_code, f"UNKNOWN_{result_code}")

            val_str = repr(val)
            entry = {
                "param_index": param_idx + 1,
                "value": val_str,
                "result_code": result_code,
                "result_name": result_name,
                "response_time_ms": round(resp_ms, 1),
            }
            boundary_results.append(entry)

            if result_code == 0:
                log(f"  ACCEPTED: {cmd_name} param{param_idx+1}={val_str}")
            elif result_code == -1:
                pass  # timeout, don't spam

            if throttle_ms > 0:
                time.sleep(throttle_ms / 1000.0)

    return boundary_results


def print_summary(report):
    """Print a human-readable summary to stderr."""
    print("\n" + "=" * 70, file=sys.stderr)
    print("  MAVLink Fuzzer Report", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  Target:          {report.target}", file=sys.stderr)
    print(f"  Timestamp:       {report.timestamp}", file=sys.stderr)
    print(f"  MAVLink version: {report.mavlink_version}", file=sys.stderr)
    print(f"  System ID:       {report.target_system_id}", file=sys.stderr)
    print(f"  Vehicle type:    {report.vehicle_type}", file=sys.stderr)
    print(f"  Autopilot:       {report.autopilot}", file=sys.stderr)
    if report.firmware_version:
        print(f"  Firmware:        {report.firmware_version}", file=sys.stderr)
    print(f"  Signing seen:    {report.signing_detected}", file=sys.stderr)
    print(f"  Signing status:  {report.signing_required}", file=sys.stderr)
    print(f"  Commands tested: {report.total_commands_tested}", file=sys.stderr)
    print(f"  Accepted:        {len(report.accepted_commands)}", file=sys.stderr)
    print(f"  Rejected/Denied: {len(report.rejected_commands)}", file=sys.stderr)
    print(f"  Unsupported:     {len(report.unsupported_commands)}", file=sys.stderr)
    print(f"  Timed out:       {len(report.timed_out_commands)}", file=sys.stderr)
    print(f"  State-change:    {len(report.state_change_commands)}", file=sys.stderr)

    if report.accepted_commands:
        print("\n--- ACCEPTED COMMANDS ---", file=sys.stderr)
        for c in report.accepted_commands:
            sc = " [STATE-CHANGE]" if c["is_state_change"] else ""
            print(f"  {c['cmd_name']} ({c['cmd_id']}){sc}", file=sys.stderr)

    if report.state_change_commands:
        print("\n--- STATE-CHANGE COMMANDS (accepted) ---", file=sys.stderr)
        for c in report.state_change_commands:
            print(f"  {c['cmd_name']} ({c['cmd_id']}) — {c['result_name']}", file=sys.stderr)

    print("=" * 70 + "\n", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="MAVLink Protocol Fuzzer — enumerate commands, test boundaries, detect signing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "target",
        help="MAVLink connection string (udp:IP:PORT, tcp:IP:PORT, /dev/ttyUSBx)",
    )
    parser.add_argument(
        "--active", action="store_true",
        help="Enable active probing (transmit commands). Without this, only passive listening.",
    )
    parser.add_argument(
        "--fuzz-params", action="store_true",
        help="Run boundary-value parameter fuzzing on accepted commands (requires --active).",
    )
    parser.add_argument(
        "--commands", type=str, default=None,
        help="Comma-separated list of MAV_CMD IDs to test (default: all known commands).",
    )
    parser.add_argument(
        "--baud", type=int, default=57600,
        help="Serial baud rate (default: 57600).",
    )
    parser.add_argument(
        "--source-system", type=int, default=255,
        help="Source system ID for outgoing messages (default: 255 = GCS).",
    )
    parser.add_argument(
        "--source-component", type=int, default=0,
        help="Source component ID (default: 0).",
    )
    parser.add_argument(
        "--throttle", type=int, default=100,
        help="Delay between probes in ms (default: 100).",
    )
    parser.add_argument(
        "--listen-time", type=int, default=5,
        help="Seconds to listen for signing detection (default: 5).",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Write JSON report to file.",
    )

    args = parser.parse_args()

    if args.fuzz_params and not args.active:
        log("--fuzz-params requires --active", "ERROR")
        sys.exit(1)

    report = FuzzReport(target=args.target)
    report.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Connect
    conn = connect(args.target, args.baud, args.source_system, args.source_component)
    report.target_system_id = conn.target_system
    report.target_component_id = conn.target_component

    # Identify target
    vtype, apilot = identify_target(conn)
    report.vehicle_type = vtype
    report.autopilot = apilot
    log(f"Vehicle: {vtype}, Autopilot: {apilot}")

    # Detect MAVLink version from first heartbeat we already got
    hb = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=3)
    raw = hb.get_msgbuf() if hb else None
    if raw and raw[0] == 0xFD:
        report.mavlink_version = "v2"
    elif raw and raw[0] == 0xFE:
        report.mavlink_version = "v1"

    # Signing detection (passive — always runs)
    signed, unsigned = detect_signing(conn, args.listen_time)
    report.signing_detected = signed > 0
    if signed > 0 and unsigned == 0:
        report.signing_required = "enforced"
        log("Signing appears ENFORCED — all packets signed", "WARN")
    elif signed > 0 and unsigned > 0:
        report.signing_required = "optional"
        log("Signing OPTIONAL — mixed signed/unsigned traffic", "WARN")
    elif unsigned > 0:
        report.signing_required = "not_enabled"
        log("No signing detected — target is UNSIGNED")
    else:
        report.signing_required = "no_traffic"
        log("No traffic captured for signing analysis", "WARN")

    if not args.active:
        log("Passive mode — skipping active probing. Use --active to enumerate commands.")
        print_summary(report)
        if args.output:
            Path(args.output).write_text(json.dumps(report.to_dict(), indent=2))
            log(f"Report written to {args.output}")
        return

    # Active probing — request firmware version first
    report.firmware_version = request_version(conn)
    if report.firmware_version:
        log(f"Firmware version: {report.firmware_version}")

    # Build command list
    if args.commands:
        cmd_ids = [int(c.strip()) for c in args.commands.split(",")]
    else:
        cmd_ids = sorted(MAV_CMD_NAMES.keys())

    log(f"Enumerating {len(cmd_ids)} commands...")
    results = enumerate_commands(conn, cmd_ids, throttle_ms=args.throttle)
    report.total_commands_tested = len(results)

    # Classify results
    for r in results:
        entry = asdict(r)
        if r.result_code == 0:
            report.accepted_commands.append(entry)
            if r.is_state_change:
                report.state_change_commands.append(entry)
        elif r.result_code in (2, 3):  # DENIED or UNSUPPORTED
            if r.result_code == 3:
                report.unsupported_commands.append(entry)
            else:
                report.rejected_commands.append(entry)
        elif r.result_code == -1:
            report.timed_out_commands.append(entry)
        else:
            report.rejected_commands.append(entry)

    # Parameter boundary fuzzing on accepted commands
    if args.fuzz_params and report.accepted_commands:
        log(f"Boundary-testing {len(report.accepted_commands)} accepted commands...")
        for entry in report.accepted_commands:
            cmd_id = entry["cmd_id"]
            cmd_name = entry["cmd_name"]
            log(f"Fuzzing parameters for {cmd_name} ({cmd_id})...")
            boundary = fuzz_parameters(conn, cmd_id, throttle_ms=args.throttle)
            entry["boundary_results"] = boundary

    print_summary(report)

    # Output
    if args.output:
        Path(args.output).write_text(json.dumps(report.to_dict(), indent=2))
        log(f"Report written to {args.output}")
    else:
        print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
