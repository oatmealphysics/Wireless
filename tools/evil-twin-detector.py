#!/usr/bin/env python3
"""
Evil Twin AP Detector — Detect rogue access points mimicking drone WiFi

Defensive counterpart to evil-twin-ap.py. Monitors WiFi scan results for
indicators of an evil twin attack targeting drone control links:

  - Duplicate SSIDs on different BSSIDs (two APs with the same name)
  - Known drone SSID patterns (TELLO-*, Bebop2-*, ArduPilot-*, etc.)
  - Signal strength anomalies (stronger-than-expected AP suddenly appears)
  - Channel changes (legitimate AP's channel shifts unexpectedly)
  - BSSID changes for a known SSID (MAC address changed)
  - Rapid AP appearance (new AP shows up during flight)

Input: JSON scan results (from iw/iwlist/nmcli), or --simulate for demo.

Output: one JSON anomaly per detection to stderr, scan analysis to stdout.

Usage:
    # Simulate normal + attack scenario
    python3 evil-twin-detector.py --simulate

    # Analyze a WiFi scan results file
    python3 evil-twin-detector.py --input scan_results.json

    # Analyze with a known-good BSSID for the drone
    python3 evil-twin-detector.py --input scan.json --known-bssid AA:BB:CC:DD:EE:FF

    # Monitor mode — re-read scan file periodically
    python3 evil-twin-detector.py --input scan.json --watch --interval 5

For CTF defense and authorized security research only.
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Known drone WiFi SSID patterns
DRONE_SSID_PATTERNS = [
    re.compile(r"^TELLO-[A-F0-9]{6}$", re.IGNORECASE),
    re.compile(r"^Bebop2?-[A-F0-9]{6}$", re.IGNORECASE),
    re.compile(r"^Disco-[A-F0-9]{6}$", re.IGNORECASE),
    re.compile(r"^Mambo-[A-F0-9]{6}$", re.IGNORECASE),
    re.compile(r"^Swing-[A-F0-9]{6}$", re.IGNORECASE),
    re.compile(r"^ANAFI-[A-F0-9]{6}$", re.IGNORECASE),
    re.compile(r"^ArduPilot", re.IGNORECASE),
    re.compile(r"^APM[_-]", re.IGNORECASE),
    re.compile(r"^PX4[_-]", re.IGNORECASE),
    re.compile(r"^MAVLINK[_-]?", re.IGNORECASE),
    re.compile(r"^DJI[_-]", re.IGNORECASE),
    re.compile(r"^Skydio", re.IGNORECASE),
    re.compile(r"^FPV[_-]", re.IGNORECASE),
    re.compile(r"^ELRS[_-]?", re.IGNORECASE),
    re.compile(r"^ExpressLRS", re.IGNORECASE),
]

SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

# Signal strength thresholds (dBm)
SIGNAL_JUMP_THRESHOLD = 15  # dB jump suggesting closer rogue AP
MIN_EXPECTED_SIGNAL = -85   # below this is too weak to be a real nearby drone


@dataclass
class APRecord:
    """Tracks a known access point over time."""
    ssid: str
    bssid: str
    channel: int = 0
    signal_dbm: int = -100
    first_seen: float = 0.0
    last_seen: float = 0.0
    scan_count: int = 0
    signal_history: list = field(default_factory=list)
    is_drone: bool = False


@dataclass
class Anomaly:
    timestamp: str
    severity: str
    category: str
    description: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "timestamp": self.timestamp,
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
        }
        if self.details:
            d["details"] = self.details
        return d


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_drone_ssid(ssid: str) -> bool:
    """Check if SSID matches known drone WiFi patterns."""
    return any(p.match(ssid) for p in DRONE_SSID_PATTERNS)


class EvilTwinDetector:
    """Stateful detector for rogue AP / evil twin attacks on drone WiFi."""

    def __init__(
        self,
        known_bssids: Optional[dict[str, str]] = None,
        drone_ssid: Optional[str] = None,
    ):
        # known_bssids maps SSID -> expected BSSID
        self.known_bssids = known_bssids or {}
        self.drone_ssid = drone_ssid
        self.ap_history: dict[str, APRecord] = {}  # bssid -> APRecord
        self.ssid_bssids: dict[str, set[str]] = defaultdict(set)
        self.anomalies: list[Anomaly] = []
        self.scan_count = 0

    def _emit(self, severity: str, category: str, desc: str,
              details: dict = None):
        a = Anomaly(
            timestamp=now_iso(),
            severity=severity,
            category=category,
            description=desc,
            details=details or {},
        )
        self.anomalies.append(a)
        print(json.dumps(a.to_dict()), file=sys.stderr)

    def ingest_scan(self, aps: list[dict],
                    scan_time: float = 0.0) -> list[Anomaly]:
        """Process one WiFi scan result. Each AP dict has ssid, bssid, channel, signal."""
        start_count = len(self.anomalies)
        self.scan_count += 1

        if scan_time == 0.0:
            scan_time = time.time()

        current_ssids: dict[str, list[dict]] = defaultdict(list)

        for ap in aps:
            ssid = ap.get("ssid", "")
            bssid = ap.get("bssid", "").upper()
            channel = ap.get("channel", 0)
            signal = ap.get("signal", ap.get("signal_dbm", -100))

            if not ssid or not bssid:
                continue

            current_ssids[ssid].append(ap)
            self.ssid_bssids[ssid].add(bssid)

            drone_ap = is_drone_ssid(ssid) or ssid == self.drone_ssid

            # Update or create AP record
            if bssid in self.ap_history:
                rec = self.ap_history[bssid]
                old_channel = rec.channel
                old_signal = rec.signal_dbm

                # Channel change detection
                if old_channel > 0 and channel != old_channel and drone_ap:
                    self._emit(
                        SEVERITY_WARNING, "channel_change",
                        f"Drone AP '{ssid}' ({bssid}) changed channel "
                        f"{old_channel} -> {channel}",
                        {"ssid": ssid, "bssid": bssid,
                         "old_channel": old_channel, "new_channel": channel},
                    )

                # Signal strength jump detection
                if (old_signal < -30 and
                        signal - old_signal > SIGNAL_JUMP_THRESHOLD and
                        drone_ap):
                    self._emit(
                        SEVERITY_WARNING, "signal_jump",
                        f"Drone AP '{ssid}' signal jumped by "
                        f"{signal - old_signal} dB ({old_signal} -> {signal} dBm)",
                        {"ssid": ssid, "bssid": bssid,
                         "old_signal": old_signal, "new_signal": signal},
                    )

                rec.channel = channel
                rec.signal_dbm = signal
                rec.last_seen = scan_time
                rec.scan_count += 1
                rec.signal_history.append(signal)
                if len(rec.signal_history) > 20:
                    rec.signal_history = rec.signal_history[-20:]
            else:
                rec = APRecord(
                    ssid=ssid, bssid=bssid, channel=channel,
                    signal_dbm=signal, first_seen=scan_time,
                    last_seen=scan_time, scan_count=1,
                    signal_history=[signal], is_drone=drone_ap,
                )
                self.ap_history[bssid] = rec

                # New drone AP appearing mid-session
                if drone_ap and self.scan_count > 1:
                    self._emit(
                        SEVERITY_WARNING, "new_drone_ap",
                        f"New drone AP appeared: '{ssid}' ({bssid}) "
                        f"signal={signal} dBm ch={channel}",
                        {"ssid": ssid, "bssid": bssid,
                         "channel": channel, "signal": signal},
                    )

            # Known BSSID mismatch
            if ssid in self.known_bssids:
                expected = self.known_bssids[ssid].upper()
                if bssid != expected:
                    self._emit(
                        SEVERITY_CRITICAL, "bssid_mismatch",
                        f"AP '{ssid}' has BSSID {bssid} but expected {expected} "
                        f"— possible evil twin",
                        {"ssid": ssid, "got_bssid": bssid,
                         "expected_bssid": expected},
                    )

        # Duplicate SSID detection (evil twin signature)
        for ssid, ap_list in current_ssids.items():
            if len(ap_list) > 1:
                bssids = [a.get("bssid", "").upper() for a in ap_list]
                unique_bssids = set(bssids)
                if len(unique_bssids) > 1:
                    is_drone = is_drone_ssid(ssid) or ssid == self.drone_ssid
                    severity = SEVERITY_CRITICAL if is_drone else SEVERITY_WARNING
                    self._emit(
                        severity, "duplicate_ssid",
                        f"Duplicate SSID '{ssid}' seen on {len(unique_bssids)} "
                        f"different BSSIDs — possible evil twin",
                        {"ssid": ssid, "bssids": sorted(unique_bssids),
                         "signals": [a.get("signal", -100) for a in ap_list],
                         "channels": [a.get("channel", 0) for a in ap_list],
                         "is_drone_pattern": is_drone},
                    )

        return self.anomalies[start_count:]

    def summary(self) -> dict:
        cats = defaultdict(int)
        sevs = defaultdict(int)
        for a in self.anomalies:
            cats[a.category] += 1
            sevs[a.severity] += 1
        drone_aps = [r for r in self.ap_history.values() if r.is_drone]
        return {
            "scans_analyzed": self.scan_count,
            "unique_aps_seen": len(self.ap_history),
            "drone_aps_seen": len(drone_aps),
            "anomalies_detected": len(self.anomalies),
            "by_category": dict(cats),
            "by_severity": dict(sevs),
        }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def simulate_traffic(detector: EvilTwinDetector):
    """Generate normal WiFi environment + evil twin attack scenario."""
    base_time = time.time()

    # Scan 1: normal environment with one drone AP
    scan1 = [
        {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
         "channel": 6, "signal": -45},
        {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
         "channel": 1, "signal": -55},
        {"ssid": "Neighbor5G", "bssid": "DD:EE:FF:44:55:66",
         "channel": 36, "signal": -70},
    ]

    print("[Scan 1] Normal environment — 3 APs, 1 drone", file=sys.stderr)
    new = detector.ingest_scan(scan1, scan_time=base_time)
    for ap in scan1:
        status = "ALERT" if new else "OK"
        print(f"  [{status}] {ap['ssid']:20s} {ap['bssid']} ch={ap['channel']:3d} "
              f"sig={ap['signal']} dBm")

    # Scan 2: still normal
    scan2 = [
        {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
         "channel": 6, "signal": -46},
        {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
         "channel": 1, "signal": -54},
        {"ssid": "Neighbor5G", "bssid": "DD:EE:FF:44:55:66",
         "channel": 36, "signal": -71},
    ]

    print("\n[Scan 2] Normal — minor signal variation", file=sys.stderr)
    new = detector.ingest_scan(scan2, scan_time=base_time + 5)
    for ap in scan2:
        status = "ALERT" if new else "OK"
        print(f"  [{status}] {ap['ssid']:20s} {ap['bssid']} ch={ap['channel']:3d} "
              f"sig={ap['signal']} dBm")

    # Scan 3: EVIL TWIN appears — same SSID, different BSSID, stronger signal
    scan3 = [
        {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
         "channel": 6, "signal": -46},
        {"ssid": "TELLO-AABB00", "bssid": "60:60:1F:AA:BB:00",
         "channel": 1, "signal": -56},
        {"ssid": "TELLO-AABB00", "bssid": "00:11:22:33:44:55",
         "channel": 1, "signal": -30},  # Evil twin — stronger
        {"ssid": "Neighbor5G", "bssid": "DD:EE:FF:44:55:66",
         "channel": 36, "signal": -71},
    ]

    print("\n[Scan 3] ATTACK — evil twin AP appears", file=sys.stderr)
    new = detector.ingest_scan(scan3, scan_time=base_time + 10)
    for ap in scan3:
        alerts = [a for a in new if ap['bssid'].upper() in json.dumps(a.to_dict())]
        status = "ALERT" if alerts else "OK"
        print(f"  [{status}] {ap['ssid']:20s} {ap['bssid']} ch={ap['channel']:3d} "
              f"sig={ap['signal']} dBm")

    # Scan 4: Legitimate AP disappears (deauth succeeded), evil twin remains
    scan4 = [
        {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
         "channel": 6, "signal": -46},
        {"ssid": "TELLO-AABB00", "bssid": "00:11:22:33:44:55",
         "channel": 1, "signal": -28},  # Evil twin only
        {"ssid": "Neighbor5G", "bssid": "DD:EE:FF:44:55:66",
         "channel": 36, "signal": -71},
    ]

    print("\n[Scan 4] Legitimate drone AP gone — only evil twin remains",
          file=sys.stderr)
    new = detector.ingest_scan(scan4, scan_time=base_time + 15)
    for ap in scan4:
        alerts = [a for a in new if ap['bssid'].upper() in json.dumps(a.to_dict())]
        status = "ALERT" if alerts else "OK"
        print(f"  [{status}] {ap['ssid']:20s} {ap['bssid']} ch={ap['channel']:3d} "
              f"sig={ap['signal']} dBm")

    # Scan 5: new unknown drone AP (different SSID pattern)
    scan5 = [
        {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:11:22:33",
         "channel": 6, "signal": -46},
        {"ssid": "TELLO-AABB00", "bssid": "00:11:22:33:44:55",
         "channel": 1, "signal": -28},
        {"ssid": "ArduPilot-Rogue", "bssid": "FF:EE:DD:CC:BB:AA",
         "channel": 11, "signal": -40},
        {"ssid": "Neighbor5G", "bssid": "DD:EE:FF:44:55:66",
         "channel": 36, "signal": -71},
    ]

    print("\n[Scan 5] New unknown drone AP appears", file=sys.stderr)
    new = detector.ingest_scan(scan5, scan_time=base_time + 20)
    for ap in scan5:
        alerts = [a for a in new if ap['bssid'].upper() in json.dumps(a.to_dict())]
        status = "ALERT" if alerts else "OK"
        print(f"  [{status}] {ap['ssid']:20s} {ap['bssid']} ch={ap['channel']:3d} "
              f"sig={ap['signal']} dBm")


def read_scan_file(path: str) -> list[list[dict]]:
    """Read WiFi scan results from JSON file. Supports single scan or array of scans."""
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        # Single scan with "aps" key
        return [data.get("aps", data.get("access_points", [data]))]
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and "ssid" in data[0]:
            return [data]  # single flat list of APs
        return data  # array of scans
    return []


def main():
    parser = argparse.ArgumentParser(
        description="Evil Twin AP Detector — detect rogue drone WiFi access points",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", "-i",
                        help="WiFi scan results JSON file")
    parser.add_argument("--known-bssid",
                        help="Known-good BSSID for drone SSID (format: SSID=BSSID)")
    parser.add_argument("--drone-ssid",
                        help="Specific drone SSID to watch")
    parser.add_argument("--simulate", action="store_true",
                        help="Run simulation with normal + evil twin attack")
    parser.add_argument("--watch", action="store_true",
                        help="Monitor mode — re-read scan file periodically")
    parser.add_argument("--interval", type=int, default=5,
                        help="Watch interval in seconds (default: 5)")
    args = parser.parse_args()

    known_bssids = {}
    if args.known_bssid:
        ssid, bssid = args.known_bssid.split("=", 1)
        known_bssids[ssid] = bssid

    detector = EvilTwinDetector(
        known_bssids=known_bssids,
        drone_ssid=args.drone_ssid,
    )

    if args.simulate:
        detector.known_bssids["TELLO-AABB00"] = "60:60:1F:AA:BB:00"
        simulate_traffic(detector)
    elif args.input:
        scans = read_scan_file(args.input)
        for i, scan in enumerate(scans):
            new = detector.ingest_scan(scan)
            print(f"[Scan {i + 1}] {len(scan)} APs — "
                  f"{len(new)} anomalies detected")
            for ap in scan:
                print(f"  {ap.get('ssid', '?'):20s} "
                      f"{ap.get('bssid', '?')} "
                      f"ch={ap.get('channel', '?'):>3} "
                      f"sig={ap.get('signal', '?')} dBm")
    else:
        parser.print_help()
        sys.exit(1)

    summary = detector.summary()
    print("\n" + "=" * 60, file=sys.stderr)
    print("EVIL TWIN DETECTION SUMMARY", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Scans analyzed:     {summary['scans_analyzed']}", file=sys.stderr)
    print(f"  Unique APs seen:    {summary['unique_aps_seen']}", file=sys.stderr)
    print(f"  Drone APs seen:     {summary['drone_aps_seen']}", file=sys.stderr)
    print(f"  Anomalies detected: {summary['anomalies_detected']}", file=sys.stderr)
    if summary["by_category"]:
        print("  By category:", file=sys.stderr)
        for cat, cnt in sorted(summary["by_category"].items()):
            print(f"    {cat}: {cnt}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    sys.exit(1 if summary["anomalies_detected"] > 0 else 0)


if __name__ == "__main__":
    main()
