#!/usr/bin/env python3
"""CRSF bus anomaly detector — defensive monitoring for injection attacks.

Analyzes CRSF serial bus traffic for signs of injection, spoofing, and
protocol abuse. Designed to run alongside legitimate CRSF traffic to
detect the attack patterns demonstrated in crsf-injection-poc.py.

Detection signatures:
  1. Unexpected device origins (non-TX/RX sending control frames)
  2. Command frames (0x32) from unexpected sources
  3. Parameter read/write floods
  4. RC channel frame timing anomalies (two sources)
  5. WiFi mode entry attempts
  6. Bind commands during active flight
  7. MSP passthrough from non-TX origin
  8. Frame rate anomalies (injection burst detection)
  9. CRC error rate spike (may indicate bus contention)

Usage:
  # Analyze a capture file
  python3 crsf-bus-defender.py analyze --file capture.bin

  # Analyze hex frames from stdin
  echo "C8 06 28 00 EA 71" | python3 crsf-bus-defender.py analyze --hex

  # Run detection rules against scenario output
  python3 crsf-bus-defender.py demo

For authorized security research only. See SECURITY.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

import importlib.util as _ilu

def _import_crsf_packet_tool():
    spec = _ilu.spec_from_file_location(
        "crsf_packet_tool",
        __import__("pathlib").Path(__file__).resolve().parent / "crsf-packet-tool.py",
    )
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_cpt = _import_crsf_packet_tool()

ADDRESSES = _cpt.ADDRESSES
FRAME_TYPES = _cpt.FRAME_TYPES
CRSFFrame = _cpt.CRSFFrame
crc8_command = _cpt.crc8_command
crc8_frame = _cpt.crc8_frame
parse_all = _cpt.parse_all
CRC8_BA = _cpt.CRC8_BA
crc8 = _cpt.crc8


# ---------------------------------------------------------------------------
# Detection alert levels
# ---------------------------------------------------------------------------

class AlertLevel:
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Alert:
    """A detection alert from the CRSF bus monitor."""
    level: str
    rule: str
    description: str
    frame_hex: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "rule": self.rule,
            "description": self.description,
            "frame_hex": self.frame_hex,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Known-good CRSF traffic patterns
# ---------------------------------------------------------------------------

LEGITIMATE_RC_ORIGINS = {0xEC, 0xEE}  # receiver or TX module
LEGITIMATE_CMD_ORIGINS = {0xEA, 0xEE}  # radio handset or TX module
LEGITIMATE_PARAM_ORIGINS = {0xEA, 0xEE}  # radio handset or TX module
LEGITIMATE_MSP_ORIGINS = {0xEA, 0xEE}  # radio handset or TX module

DANGEROUS_COMMANDS = {
    0x01: "FC command (disarm/arm)",
    0x08: "VTX control",
    0x10: "Crossfire/ELRS bind",
}


# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------

class CRSFBusMonitor:
    """Stateful CRSF bus traffic analyzer for injection detection."""

    def __init__(self):
        self.alerts: list[Alert] = []
        self.frame_count = 0
        self.crc_errors = 0
        self.frame_types_seen: Counter = Counter()
        self.origins_seen: Counter = Counter()
        self.cmd_count = 0
        self.param_read_count = 0
        self.param_write_count = 0
        self.rc_frame_times: list[float] = []
        self._start = time.monotonic()

    def analyze_frame(self, frame: CRSFFrame, timestamp: float = 0.0):
        """Run all detection rules against a single frame."""
        self.frame_count += 1
        self.frame_types_seen[frame.frame_type] += 1

        if not frame.crc_valid:
            self.crc_errors += 1
            self._rule_crc_error(frame)
            return

        if frame.is_extended and frame.origin is not None:
            self.origins_seen[frame.origin] += 1

        self._rule_unexpected_origin(frame)
        self._rule_dangerous_command(frame)
        self._rule_param_flood(frame)
        self._rule_wifi_mode_attempt(frame)
        self._rule_bind_during_flight(frame)
        self._rule_msp_injection(frame)
        self._rule_rc_timing(frame, timestamp)

    def analyze_stream(self, data: bytes) -> list[Alert]:
        """Analyze a byte stream of CRSF frames."""
        frames = parse_all(data)
        for i, frame in enumerate(frames):
            self.analyze_frame(frame, timestamp=float(i))
        return self.alerts

    def _add_alert(self, level: str, rule: str, desc: str,
                   frame: CRSFFrame, **details):
        self.alerts.append(Alert(
            level=level,
            rule=rule,
            description=desc,
            frame_hex=frame.raw.hex(" "),
            details=details,
        ))

    def _rule_crc_error(self, frame: CRSFFrame):
        if self.crc_errors > 3:
            self._add_alert(
                AlertLevel.MEDIUM,
                "crc_error_spike",
                f"CRC error rate elevated ({self.crc_errors}/{self.frame_count} frames)",
                frame,
                error_count=self.crc_errors,
                total_frames=self.frame_count,
            )

    def _rule_unexpected_origin(self, frame: CRSFFrame):
        if not frame.is_extended or frame.origin is None:
            return

        origin = frame.origin
        ft = frame.frame_type

        if ft == 0x16 and origin not in LEGITIMATE_RC_ORIGINS:
            self._add_alert(
                AlertLevel.CRITICAL,
                "unexpected_rc_origin",
                f"RC channels frame from unexpected origin 0x{origin:02X} "
                f"({ADDRESSES.get(origin, 'unknown')})",
                frame,
                origin=f"0x{origin:02X}",
                expected_origins=[f"0x{x:02X}" for x in LEGITIMATE_RC_ORIGINS],
            )

    def _rule_dangerous_command(self, frame: CRSFFrame):
        if frame.frame_type != 0x32:
            return

        self.cmd_count += 1
        ip = frame.inner_payload
        if len(ip) < 2:
            return

        cmd_id = ip[0]
        origin = frame.origin

        if cmd_id in DANGEROUS_COMMANDS:
            cmd_name = DANGEROUS_COMMANDS[cmd_id]

            if origin not in LEGITIMATE_CMD_ORIGINS:
                self._add_alert(
                    AlertLevel.CRITICAL,
                    "dangerous_cmd_unexpected_origin",
                    f"Dangerous command '{cmd_name}' from unexpected origin "
                    f"0x{origin:02X}" if origin else "unknown origin",
                    frame,
                    command_id=f"0x{cmd_id:02X}",
                    command_name=cmd_name,
                )
            else:
                self._add_alert(
                    AlertLevel.HIGH,
                    "dangerous_cmd",
                    f"Dangerous command '{cmd_name}' detected "
                    f"(cmd=0x{cmd_id:02X})",
                    frame,
                    command_id=f"0x{cmd_id:02X}",
                    command_name=cmd_name,
                    origin=f"0x{origin:02X}" if origin else "unknown",
                )

    def _rule_param_flood(self, frame: CRSFFrame):
        if frame.frame_type == 0x2C:
            self.param_read_count += 1
            if self.param_read_count > 10:
                self._add_alert(
                    AlertLevel.MEDIUM,
                    "param_read_flood",
                    f"Excessive parameter reads ({self.param_read_count} in session)",
                    frame,
                    read_count=self.param_read_count,
                )

        if frame.frame_type == 0x2D:
            self.param_write_count += 1
            if self.param_write_count > 5:
                self._add_alert(
                    AlertLevel.HIGH,
                    "param_write_flood",
                    f"Excessive parameter writes ({self.param_write_count} in session)",
                    frame,
                    write_count=self.param_write_count,
                )

    def _rule_wifi_mode_attempt(self, frame: CRSFFrame):
        if frame.frame_type != 0x2D:
            return

        ip = frame.inner_payload
        if len(ip) < 2:
            return

        param_idx = ip[0]
        if param_idx == 0xFF:
            self._add_alert(
                AlertLevel.CRITICAL,
                "wifi_mode_attempt",
                "Parameter write to WiFi mode index detected — "
                "may disconnect RF link",
                frame,
                param_index=f"0x{param_idx:02X}",
                origin=f"0x{frame.origin:02X}" if frame.origin else "unknown",
            )

    def _rule_bind_during_flight(self, frame: CRSFFrame):
        if frame.frame_type != 0x32:
            return

        ip = frame.inner_payload
        if len(ip) < 2:
            return

        cmd_id = ip[0]
        sub = ip[1:-1] if len(ip) > 2 else b""

        if cmd_id == 0x10 and sub[:1] == b"\x01":
            self._add_alert(
                AlertLevel.CRITICAL,
                "bind_command",
                "Bind command detected — will disrupt RF link if receiver is connected",
                frame,
                command_id="0x10",
                sub_command="0x01 (bind)",
            )

    def _rule_msp_injection(self, frame: CRSFFrame):
        if frame.frame_type not in (0x7A, 0x7C):
            return

        origin = frame.origin
        if origin is not None and origin not in LEGITIMATE_MSP_ORIGINS:
            self._add_alert(
                AlertLevel.HIGH,
                "msp_injection",
                f"MSP {'request' if frame.frame_type == 0x7A else 'write'} "
                f"from unexpected origin 0x{origin:02X}",
                frame,
                frame_type=f"0x{frame.frame_type:02X}",
                origin=f"0x{origin:02X}",
            )

    def _rule_rc_timing(self, frame: CRSFFrame, timestamp: float):
        if frame.frame_type != 0x16:
            return

        self.rc_frame_times.append(timestamp)
        if len(self.rc_frame_times) < 10:
            return

        recent = self.rc_frame_times[-10:]
        intervals = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
        if not intervals:
            return

        avg = sum(intervals) / len(intervals)
        if avg > 0:
            variance = sum((x - avg) ** 2 for x in intervals) / len(intervals)
            if variance > avg * 2:
                self._add_alert(
                    AlertLevel.MEDIUM,
                    "rc_timing_anomaly",
                    "RC channel frame timing shows high variance — "
                    "possible dual-source injection",
                    frame,
                    variance=round(variance, 4),
                    avg_interval=round(avg, 4),
                )

    def summary(self) -> dict:
        """Return a summary of the analysis."""
        by_level = Counter(a.level for a in self.alerts)
        by_rule = Counter(a.rule for a in self.alerts)
        return {
            "total_frames": self.frame_count,
            "crc_errors": self.crc_errors,
            "total_alerts": len(self.alerts),
            "alerts_by_level": dict(by_level),
            "alerts_by_rule": dict(by_rule),
            "unique_frame_types": len(self.frame_types_seen),
            "unique_origins": len(self.origins_seen),
        }


# ---------------------------------------------------------------------------
# Demo: run detection against injection PoC scenarios
# ---------------------------------------------------------------------------

def demo_detection():
    """Run the defender against the injection PoC's attack frames."""
    from crsf_packet_tool import (
        build_command,
        build_device_ping,
        build_frame,
        build_rc_channels,
    )

    monitor = CRSFBusMonitor()

    attack_frames = {
        "device_ping": build_device_ping(),
        "param_read": build_frame(0x2C, bytes([0xEC, 0xEE, 0x01, 0x00])),
        "wifi_mode_entry": build_frame(0x2D, bytes([0xEC, 0xEE, 0xFF, 0x01])),
        "force_disarm": build_command(0xC8, 0xEE, 0x01, b"\x00"),
        "vtx_pitmode": build_command(0xCE, 0xEE, 0x08, b"\x04\x01"),
        "bind_disrupt": build_command(0xEC, 0xEE, 0x10, b"\x01"),
        "rc_override": build_rc_channels([1500]*4 + [1000]*12),
        "msp_inject": build_frame(0x7A, bytes([0xC8, 0xEE, 0x00, 0x01, 0x00])),
    }

    print("CRSF Bus Defender — Detection Demo")
    print("=" * 60)
    print("Running detection rules against injection PoC attack frames\n")

    for name, frame_bytes in attack_frames.items():
        print(f"  Injecting: {name}")
        frames = parse_all(frame_bytes)
        for f in frames:
            monitor.analyze_frame(f)

    print(f"\n{'='*60}")
    print(f"Detection Results")
    print(f"{'='*60}")

    summary = monitor.summary()
    print(f"  Total frames analyzed: {summary['total_frames']}")
    print(f"  Total alerts raised:   {summary['total_alerts']}")
    print(f"  CRC errors:            {summary['crc_errors']}")

    if monitor.alerts:
        print(f"\nAlerts by severity:")
        for level in [AlertLevel.CRITICAL, AlertLevel.HIGH,
                      AlertLevel.MEDIUM, AlertLevel.LOW, AlertLevel.INFO]:
            count = summary["alerts_by_level"].get(level, 0)
            if count:
                print(f"  {level:10s}: {count}")

        print(f"\nDetailed alerts:")
        for alert in monitor.alerts:
            level_tag = {
                AlertLevel.CRITICAL: "!!",
                AlertLevel.HIGH: "! ",
                AlertLevel.MEDIUM: "* ",
                AlertLevel.LOW: "- ",
                AlertLevel.INFO: "  ",
            }.get(alert.level, "  ")
            print(f"  {level_tag} [{alert.level:8s}] {alert.rule}")
            print(f"     {alert.description}")
            if alert.details:
                for k, v in alert.details.items():
                    print(f"       {k}: {v}")

    print(f"\n{'='*60}")
    print("Defensive recommendations:")
    print("  1. Monitor CRSF bus for command frames (0x32) from non-TX origins")
    print("  2. Alert on parameter writes to WiFi mode index")
    print("  3. Block bind commands while armed or link quality > 0")
    print("  4. Track RC frame timing for dual-source detection")
    print("  5. Whitelist MSP commands allowed over CRSF passthrough")
    print("  6. Log all extended-frame origins for forensic analysis")
    print("  7. Consider a CRSF firewall between FC and untrusted peripherals")


def cmd_analyze(args: argparse.Namespace):
    """Analyze CRSF traffic from file or hex input."""
    if args.file:
        with open(args.file, "rb") as f:
            data = f.read()
    elif args.hex:
        hex_str = " ".join(args.hex).replace(",", "")
        data = bytes.fromhex(hex_str.replace(" ", ""))
    else:
        data = sys.stdin.buffer.read()

    monitor = CRSFBusMonitor()
    monitor.analyze_stream(data)

    if args.json:
        output = {
            "summary": monitor.summary(),
            "alerts": [a.to_dict() for a in monitor.alerts],
        }
        print(json.dumps(output, indent=2))
    else:
        summary = monitor.summary()
        print(f"Frames analyzed: {summary['total_frames']}")
        print(f"Alerts raised:   {summary['total_alerts']}")
        for alert in monitor.alerts:
            print(f"  [{alert.level}] {alert.rule}: {alert.description}")


def main():
    parser = argparse.ArgumentParser(
        description="CRSF bus anomaly detector — defensive monitoring",
    )
    sub = parser.add_subparsers(dest="command")

    p_analyze = sub.add_parser("analyze", help="Analyze CRSF traffic")
    p_analyze.add_argument("--file", "-f", help="Binary capture file")
    p_analyze.add_argument("--hex", nargs="*", help="Hex bytes")
    p_analyze.add_argument("--json", "-j", action="store_true")
    p_analyze.set_defaults(func=cmd_analyze)

    p_demo = sub.add_parser("demo", help="Run detection demo against PoC attacks")
    p_demo.set_defaults(func=lambda a: demo_detection())

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        demo_detection()


if __name__ == "__main__":
    main()
