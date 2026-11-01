#!/usr/bin/env python3
"""
ELRS v4.0 SPI RX Lockout Analysis

ELRS v4.0 (the next major version) drops support for SPI-integrated
receivers. This means the TinyHawk III Plus — which uses an SPI-connected
SX1280 on the CRAZYBEEF4SX1280 board — is PERMANENTLY locked to ELRS v3.x.

Implications:
  - Every v3 cryptographic weakness (no encryption, no nonce in CRC,
    predictable FHSS, MD5-based UID) can NEVER be patched on this hardware
  - NCC Group's rejected patches will never reach this hardware either
  - The hardware is permanently exploitable by anyone who can capture SYNC
  - No firmware update, no configuration change, no workaround exists

Why ELRS v4 drops SPI RX:
  - v4 requires more RAM/flash than STM32F411 SPI integration allows
  - SPI RX was a niche use case (most ELRS RX use UART CRSF)
  - The ELRS team decided the architecture cost wasn't justified
  - Betaflight's rx_spi driver binds tightly to v3 OTA format

Hardware affected:
  - All CRAZYBEEF4SX1280 boards (including TinyHawk III, III Plus)
  - Any FC with integrated SX1280 SPI ELRS receiver
  - Estimated thousands of units in the field

This tool analyzes the security implications and provides a
comprehensive comparison of v3 vs v4 protections.

Usage:
  python3 elrs-v4-lockout-analysis.py --compare
  python3 elrs-v4-lockout-analysis.py --impact
  python3 elrs-v4-lockout-analysis.py --timeline

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
# Protocol comparison data
# ---------------------------------------------------------------------------

V3_PROPERTIES = {
    "version": "ELRS v3.x (OTA v3)",
    "encryption": None,
    "crc": "CRC-14, static initializer = (UID[4]<<8|UID[5])^3",
    "nonce_in_crc": False,
    "replay_protection": None,
    "fhss_prng": "MINSTD LCG (deterministic, predictable)",
    "uid_derivation": "MD5('-DMY_BINDING_PHRASE=\"<phrase>\"')[:6]",
    "uid_leakage": "UID[3:5] in every SYNC packet (cleartext)",
    "model_match": "6-bit (64 values), brute-forceable in <0.5s",
    "msp_auth": None,
    "spi_rx_support": True,
    "ncc_patches": "REJECTED by ELRS maintainers",
}

V4_PROPERTIES = {
    "version": "ELRS v4.0 (OTA v4)",
    "encryption": None,  # Still no encryption in v4
    "crc": "CRC-14, nonce XOR: crcInitializer ^ nonce",
    "nonce_in_crc": True,
    "replay_protection": "Nonce-based (prevents exact replay)",
    "fhss_prng": "Same MINSTD LCG (still predictable)",
    "uid_derivation": "Same MD5 (still offline-crackable)",
    "uid_leakage": "Still leaks UID bytes in SYNC",
    "model_match": "Same 6-bit mechanism",
    "msp_auth": None,  # Still no MSP authentication
    "spi_rx_support": False,  # DROPPED
    "ncc_patches": "Partial (nonce-CRC only)",
}

AFFECTED_BOARDS = [
    {
        "name": "EMAX TinyHawk III Plus",
        "board": "CRAZYBEEF4SX1280",
        "mcu": "STM32F411CEU6",
        "radio": "SX1280 (SPI bus 3)",
        "betaflight": "4.4.2",
        "confirmed": True,
    },
    {
        "name": "EMAX TinyHawk III",
        "board": "CRAZYBEEF4SX1280",
        "mcu": "STM32F411CEU6",
        "radio": "SX1280 (SPI)",
        "betaflight": "4.4.x",
        "confirmed": False,
    },
    {
        "name": "Happymodel Mobula6/7 ELRS",
        "board": "CRAZYBEEF4SX1280 variant",
        "mcu": "STM32F411",
        "radio": "SX1280 (SPI)",
        "betaflight": "4.3-4.4",
        "confirmed": False,
    },
    {
        "name": "BetaFPV Meteor65/75 SPI",
        "board": "Various SX1280 SPI",
        "mcu": "STM32F411",
        "radio": "SX1280 (SPI)",
        "betaflight": "4.3-4.4",
        "confirmed": False,
    },
]

PERMANENT_VULNS = [
    {
        "id": "CRYPTO-001",
        "name": "No encryption",
        "severity": "CRITICAL",
        "v3": "Absent",
        "v4": "Still absent",
        "patchable": False,
        "detail": "All OTA data readable in cleartext forever",
    },
    {
        "id": "CRYPTO-002",
        "name": "Static CRC (no nonce)",
        "severity": "CRITICAL",
        "v3": "Static initializer, no per-packet freshness",
        "v4": "FIXED (nonce XOR in CRC)",
        "patchable": False,
        "detail": "Enables packet forgery and replay from passive capture",
    },
    {
        "id": "CRYPTO-003",
        "name": "Predictable FHSS",
        "severity": "HIGH",
        "v3": "MINSTD LCG, seed from UID",
        "v4": "Same LCG (not fixed)",
        "patchable": False,
        "detail": "Full hop sequence recoverable from partial UID",
    },
    {
        "id": "CRYPTO-004",
        "name": "MD5-based UID derivation",
        "severity": "HIGH",
        "v3": "MD5 of binding phrase flag",
        "v4": "Same (not fixed)",
        "patchable": False,
        "detail": "Binding phrase crackable via dictionary/brute-force",
    },
    {
        "id": "CRYPTO-005",
        "name": "UID leakage in SYNC",
        "severity": "HIGH",
        "v3": "UID[3:5] in cleartext",
        "v4": "Still present",
        "patchable": False,
        "detail": "3 of 6 UID bytes passive-capturable",
    },
    {
        "id": "CRYPTO-006",
        "name": "No MSP authentication",
        "severity": "CRITICAL",
        "v3": "Full MSP command table, no auth",
        "v4": "Still no MSP auth",
        "patchable": False,
        "detail": "Any connected attacker can inject any MSP command",
    },
    {
        "id": "FAILSAFE-001",
        "name": "Non-RC failsafe suppression",
        "severity": "CRITICAL",
        "v3": "lastValidPacketMs reset by all types",
        "v4": "Status unclear (Betaflight issue, not ELRS)",
        "patchable": False,
        "detail": "Non-RC packets suppress failsafe indefinitely",
    },
]


def compare_versions():
    """Compare v3 vs v4 security properties."""
    print(f"\n{'='*65}")
    print("ELRS v3 vs v4 SECURITY COMPARISON")
    print(f"{'='*65}")

    properties = [
        ("Encryption", "encryption"),
        ("CRC mechanism", "crc"),
        ("Nonce in CRC", "nonce_in_crc"),
        ("Replay protection", "replay_protection"),
        ("FHSS PRNG", "fhss_prng"),
        ("UID derivation", "uid_derivation"),
        ("UID leakage", "uid_leakage"),
        ("Model match", "model_match"),
        ("MSP authentication", "msp_auth"),
        ("SPI RX support", "spi_rx_support"),
    ]

    for label, key in properties:
        v3 = V3_PROPERTIES[key]
        v4 = V4_PROPERTIES[key]
        v3_str = str(v3) if v3 is not None else "NONE"
        v4_str = str(v4) if v4 is not None else "NONE"
        improved = v3 != v4 and v4 not in (None, False, "NONE")
        marker = " [IMPROVED]" if improved and key != "spi_rx_support" else ""
        if key == "spi_rx_support":
            marker = " [REMOVED]" if not v4 else ""

        print(f"\n  {label}:")
        print(f"    v3: {v3_str}")
        print(f"    v4: {v4_str}{marker}")

    print(f"\n  SUMMARY:")
    print(f"    v4 fixes: Nonce in CRC (replay protection)")
    print(f"    v4 drops: SPI RX support")
    print(f"    v4 keeps unfixed: No encryption, predictable FHSS,")
    print(f"      MD5 UID derivation, UID leakage, no MSP auth")
    print(f"    Net effect for SPI RX targets: ZERO improvements, permanent v3")


def analyze_impact():
    """Analyze the impact of permanent v3 lockout."""
    print(f"\n{'='*65}")
    print("PERMANENT VULNERABILITY IMPACT")
    print(f"{'='*65}")

    print(f"\n  Affected hardware:")
    for board in AFFECTED_BOARDS:
        status = "CONFIRMED" if board["confirmed"] else "Likely affected"
        print(f"    - {board['name']} ({board['board']}) — {status}")
        print(f"      MCU: {board['mcu']}, Radio: {board['radio']}, BF: {board['betaflight']}")

    print(f"\n  Permanently unpatched vulnerabilities:")
    for vuln in PERMANENT_VULNS:
        fix_in_v4 = vuln['v4'] != vuln['v3']
        v4_note = f" (v4 fixes this, but SPI can't get v4)" if fix_in_v4 and "FIXED" in vuln['v4'] else " (unfixed even in v4)"
        print(f"\n    [{vuln['severity']}] {vuln['id']}: {vuln['name']}")
        print(f"      {vuln['detail']}")
        print(f"      v3: {vuln['v3']}")
        print(f"      v4: {vuln['v4']}{v4_note}")
        print(f"      Patchable on SPI RX: {'Yes' if vuln['patchable'] else 'NO — permanent'}")

    crit_count = sum(1 for v in PERMANENT_VULNS if v['severity'] == 'CRITICAL')
    high_count = sum(1 for v in PERMANENT_VULNS if v['severity'] == 'HIGH')
    print(f"\n  Total permanent vulnerabilities: {len(PERMANENT_VULNS)}")
    print(f"    CRITICAL: {crit_count}")
    print(f"    HIGH:     {high_count}")
    print(f"    NONE of these can be patched on SPI-integrated hardware")


def analyze_timeline():
    """Timeline of the lockout."""
    print(f"\n{'='*65}")
    print("LOCKOUT TIMELINE")
    print(f"{'='*65}")

    events = [
        ("2021", "ELRS v1.0 — SPI RX support introduced for integrated boards"),
        ("2022", "NCC Group audit — identifies crypto weaknesses, proposes patches"),
        ("2022", "ELRS team rejects NCC patches — 'not needed for RC link'"),
        ("2023", "ELRS v3.0 — OTA v3 format, still no encryption, no nonce CRC"),
        ("2024", "EMAX ships TinyHawk III Plus with BF 4.4.2 + ELRS v3 SPI"),
        ("2024", "CVE-2024-42483 — ESP-NOW replay affects ELRS Backpack"),
        ("2025", "CVE-2025-52471 — ESP-NOW integer underflow RCE (CVSS 9.8)"),
        ("2025", "CVE-2025-53540 — Arduino-ESP32 CSRF firmware RCE"),
        ("2025-26", "ELRS v4.0 development — adds nonce CRC, DROPS SPI RX support"),
        ("2026", "Present — TinyHawk III Plus permanently on v3, all vulns permanent"),
    ]

    for date, event in events:
        print(f"  {date:>8s}  {event}")

    print(f"\n  The window for SPI RX security fixes closed when ELRS v4")
    print(f"  dropped SPI support. Every TinyHawk III Plus, Mobula, Meteor,")
    print(f"  and other SPI-integrated ELRS board sold is permanently")
    print(f"  running vulnerable protocol firmware with no upgrade path.")


def main():
    parser = argparse.ArgumentParser(
        description="ELRS v4.0 SPI RX Lockout Analysis"
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Compare v3 vs v4 security properties",
    )
    parser.add_argument(
        "--impact", action="store_true",
        help="Analyze permanent vulnerability impact on SPI hardware",
    )
    parser.add_argument(
        "--timeline", action="store_true",
        help="Timeline of the lockout",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("ELRS v4.0 SPI RX Lockout Analysis")
    print(f"Target: EMAX TinyHawk III Plus (CRAZYBEEF4SX1280)")
    print(f"Current protocol: ELRS v3.x (OTA v3)")
    print(f"Upgrade path to v4: NONE (SPI RX dropped)")
    print(f"Security fix path: NONE (permanent)")

    results = {
        "target": "CRAZYBEEF4SX1280",
        "current_version": "v3.x",
        "upgrade_path": None,
        "permanent_vulns": len(PERMANENT_VULNS),
    }

    if args.compare:
        compare_versions()
        results["mode"] = "compare"

    if args.impact:
        analyze_impact()
        results["mode"] = "impact"

    if args.timeline:
        analyze_timeline()
        results["mode"] = "timeline"

    if not any([args.compare, args.impact, args.timeline]):
        compare_versions()
        analyze_impact()
        analyze_timeline()

    # Assessment
    print(f"\n{'='*65}")
    print("ASSESSMENT")
    print(f"{'='*65}")
    print("  The TinyHawk III Plus is in a uniquely poor security position:")
    print("  it's running a protocol with ZERO cryptographic protections,")
    print("  and the upgrade path that would add partial protections (v4)")
    print("  has been architecturally removed for this hardware class.")
    print()
    print("  This is not a temporary gap — it's permanent by design.")
    print("  Every SPI-integrated ELRS board ever sold is in the same")
    print("  position: permanently running v3 with all NCC-identified")
    print("  weaknesses, all Betaflight MSP attack surfaces, and no")
    print("  path to any future security improvement.")
    print()
    print("  The only mitigation is physical: fly only in trusted RF")
    print("  environments where no adversary can capture SYNC packets.")
    print("  For a drone that flies outdoors in 2.4 GHz spectrum shared")
    print("  with billions of WiFi devices, this is no mitigation at all.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
