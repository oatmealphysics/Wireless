#!/usr/bin/env python3
"""
ELRS Kill Chain Orchestrator

Maps the complete end-to-end attack chains available against the
TinyHawk III Plus (CRAZYBEEF4SX1280) by combining all 20+ individual
PoC tools into coherent multi-stage attack paths.

This tool does NOT execute attacks. It analyzes and documents how the
individual vulnerabilities compose into full kill chains, from initial
reconnaissance through to persistent compromise.

Kill Chain Stages (adapted from Lockheed Martin Cyber Kill Chain):
  1. RECON       — Passive RF capture (SYNC, telemetry)
  2. WEAPONIZE   — Derive crypto material (UID, CRC, FHSS)
  3. DELIVER     — OTA packet injection or WiFi/physical access
  4. EXPLOIT     — Protocol weakness exploitation
  5. INSTALL     — Persistent firmware modification
  6. C2          — Command and control channel
  7. ACTIONS     — Objective execution (motor control, exfil, etc.)

Each chain maps to specific tools in the suite and identifies the
prerequisites, required hardware, and detection difficulty.

Usage:
  python3 elrs-kill-chain-orchestrator.py --chains
  python3 elrs-kill-chain-orchestrator.py --matrix
  python3 elrs-kill-chain-orchestrator.py --tools-map
  python3 elrs-kill-chain-orchestrator.py --chain-id KC-01

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
# Tool suite inventory
# ---------------------------------------------------------------------------

TOOL_SUITE = {
    "elrs-binding-phrase-crack": {
        "file": "elrs-binding-phrase-crack.py",
        "stage": "WEAPONIZE",
        "description": "Offline binding phrase dictionary/brute-force attack",
    },
    "elrs-fhss-seed-resolve": {
        "file": "elrs-fhss-seed-resolve.py",
        "stage": "WEAPONIZE",
        "description": "FHSS sequence prediction from partial UID",
    },
    "elrs-session-hijack": {
        "file": "elrs-session-hijack.py",
        "stage": "EXPLOIT",
        "description": "Full session hijack (UID + CRC + FHSS)",
    },
    "smartaudio-control": {
        "file": "smartaudio-control.py",
        "stage": "ACTIONS",
        "description": "VTX SmartAudio control (freq/power/pitmode)",
    },
    "msp-displayport-inject": {
        "file": "msp-displayport-inject.py",
        "stage": "ACTIONS",
        "description": "OSD injection (fake warnings)",
    },
    "elrs-wifi-persistence-chain": {
        "file": "elrs-wifi-persistence-chain.py",
        "stage": "INSTALL",
        "description": "WiFi timeout → firmware persistence",
    },
    "msp-command-arsenal": {
        "file": "msp-command-arsenal.py",
        "stage": "ACTIONS",
        "description": "Full MSP command surface (13 commands)",
    },
    "elrs-model-match-bruteforce": {
        "file": "elrs-model-match-bruteforce.py",
        "stage": "EXPLOIT",
        "description": "6-bit model match brute-force",
    },
    "bf-configurator-msp-xss": {
        "file": "bf-configurator-msp-xss.py",
        "stage": "EXPLOIT",
        "description": "Configurator MSP→XSS→RCE chain",
    },
    "elrs-uid-rainbow": {
        "file": "elrs-uid-rainbow.py",
        "stage": "WEAPONIZE",
        "description": "Rainbow table for UID[0:2] precomputation",
    },
    "elrs-failsafe-suppression": {
        "file": "elrs-failsafe-suppression.py",
        "stage": "EXPLOIT",
        "description": "Non-RC packet failsafe suppression",
    },
    "crsf-channel-oob-write": {
        "file": "crsf-channel-oob-write.py",
        "stage": "EXPLOIT",
        "description": "CRSF subset RC OOB write primitive",
    },
    "stm32-dfu-extract": {
        "file": "stm32-dfu-extract.py",
        "stage": "INSTALL",
        "description": "STM32F411 RDP check + DFU entry",
    },
    "espnow-backpack-overflow": {
        "file": "espnow-backpack-overflow.py",
        "stage": "EXPLOIT",
        "description": "ESP-NOW MSPv2 stack overflow (177B OOB)",
    },
    "elrs-crypto-audit": {
        "file": "elrs-crypto-audit.py",
        "stage": "RECON",
        "description": "ELRS v3 cryptographic weakness audit",
    },
    "elrs-v4-lockout-analysis": {
        "file": "elrs-v4-lockout-analysis.py",
        "stage": "RECON",
        "description": "ELRS v4 SPI RX permanent lockout",
    },
    "betaflight-dfu-backdoor": {
        "file": "betaflight-dfu-backdoor.py",
        "stage": "INSTALL",
        "description": "7 firmware backdoor categories",
    },
    "esp32-csrf-firmware-rce": {
        "file": "esp32-csrf-firmware-rce.py",
        "stage": "INSTALL",
        "description": "CVE-2025-53540 CSRF firmware replace",
    },
    "sx1280-spi-write-analysis": {
        "file": "sx1280-spi-write-analysis.py",
        "stage": "ACTIONS",
        "description": "SX1280 SPI register manipulation",
    },
    "crsf-parameter-indexing-oob": {
        "file": "crsf-parameter-indexing-oob.py",
        "stage": "EXPLOIT",
        "description": "CRSF parameter OOB read/write",
    },
    "elrs-telemetry-intercept": {
        "file": "elrs-telemetry-intercept.py",
        "stage": "RECON",
        "description": "Passive telemetry interception (GPS, battery, etc.)",
    },
}

# ---------------------------------------------------------------------------
# Kill chains
# ---------------------------------------------------------------------------

KILL_CHAINS = [
    {
        "id": "KC-01",
        "name": "Passive Recon → Session Hijack → Motor Override",
        "severity": "CRITICAL",
        "hardware": "SDR (HackRF/LimeSDR) + ELRS TX module",
        "range": "RF range (~1km line of sight)",
        "detection": "Very difficult — uses legitimate protocol",
        "stages": [
            {
                "stage": "RECON",
                "action": "Capture SYNC packets passively",
                "tool": "elrs-crypto-audit",
                "detail": "Extract UID[3:5] from cleartext SYNC. Derive CRC-14 init.",
            },
            {
                "stage": "WEAPONIZE",
                "action": "Resolve FHSS hop sequence",
                "tool": "elrs-fhss-seed-resolve",
                "detail": "256 UID[2] candidates → 256 FHSS sequences. Match against observed hops.",
            },
            {
                "stage": "WEAPONIZE",
                "action": "Brute-force model match (optional)",
                "tool": "elrs-model-match-bruteforce",
                "detail": "64 values, <0.5s to try all. Only needed if model match enabled.",
            },
            {
                "stage": "DELIVER",
                "action": "Transmit on target's FHSS channel",
                "tool": "elrs-session-hijack",
                "detail": "Inject RC packets with correct CRC on correct hop at correct time.",
            },
            {
                "stage": "EXPLOIT",
                "action": "Suppress failsafe via SYNC replay",
                "tool": "elrs-failsafe-suppression",
                "detail": "Send CRC-valid SYNC packets to keep lastValidPacketMs current.",
            },
            {
                "stage": "ACTIONS",
                "action": "Send MSP_SET_MOTOR commands",
                "tool": "msp-command-arsenal",
                "detail": "Direct motor control: cut motors, spin up, override RC input.",
            },
        ],
    },
    {
        "id": "KC-02",
        "name": "Binding Phrase Crack → Persistent Firmware Backdoor",
        "severity": "CRITICAL",
        "hardware": "SDR + WiFi adapter + ELRS TX module",
        "range": "RF range (initial), then persistent",
        "detection": "Extremely difficult — firmware-level persistence",
        "stages": [
            {
                "stage": "RECON",
                "action": "Capture SYNC → UID[3:5]",
                "tool": "elrs-crypto-audit",
                "detail": "Passive capture, no transmission needed.",
            },
            {
                "stage": "WEAPONIZE",
                "action": "Crack binding phrase offline",
                "tool": "elrs-binding-phrase-crack",
                "detail": "Dictionary attack on common phrases. MD5 derivation is instant.",
            },
            {
                "stage": "EXPLOIT",
                "action": "Hijack session, force WiFi mode",
                "tool": "elrs-session-hijack",
                "detail": "Send MSP_ELRS_SET_TX_WIFI_MODE via hijacked CRSF link.",
            },
            {
                "stage": "INSTALL",
                "action": "CSRF firmware replace on TX module",
                "tool": "esp32-csrf-firmware-rce",
                "detail": "POST malicious firmware to /update. No auth, no CSRF, no verify.",
            },
            {
                "stage": "INSTALL",
                "action": "Backdoored TX sends DFU reboot to FC",
                "tool": "stm32-dfu-extract",
                "detail": "MSP_REBOOT mode=1 → FC enters DFU. Flash backdoored BF firmware.",
            },
            {
                "stage": "C2",
                "action": "Backdoor activates on trigger",
                "tool": "betaflight-dfu-backdoor",
                "detail": "Stick combo, timer, or geofence triggers backdoor code.",
            },
            {
                "stage": "ACTIONS",
                "action": "Full drone control via backdoor",
                "tool": "msp-command-arsenal",
                "detail": "Motor override, failsafe redirect, telemetry exfil — permanent.",
            },
        ],
    },
    {
        "id": "KC-03",
        "name": "60s Jam → WiFi → CSRF → Permanent Compromise",
        "severity": "CRITICAL",
        "hardware": "2.4 GHz jammer + WiFi adapter",
        "range": "WiFi range (~50m)",
        "detection": "Moderate — jamming is detectable but brief",
        "stages": [
            {
                "stage": "DELIVER",
                "action": "Jam 2.4 GHz for 60 seconds",
                "tool": "(external — RF jammer)",
                "detail": "Broadband noise or WiFi deauth. Forces ELRS connection timeout.",
            },
            {
                "stage": "EXPLOIT",
                "action": "TX auto-enters WiFi mode",
                "tool": "elrs-wifi-persistence-chain",
                "detail": "connectionTimeoutMs default 60s. WiFi AP broadcasts open/default.",
            },
            {
                "stage": "INSTALL",
                "action": "CSRF firmware replace",
                "tool": "esp32-csrf-firmware-rce",
                "detail": "Connect to AP, POST to /update. Permanent firmware replace.",
            },
            {
                "stage": "INSTALL",
                "action": "Backdoored TX pivots to FC",
                "tool": "betaflight-dfu-backdoor",
                "detail": "Malicious TX firmware sends DFU commands, flashes FC.",
            },
            {
                "stage": "ACTIONS",
                "action": "Full persistent control",
                "tool": "sx1280-spi-write-analysis",
                "detail": "Backdoor uses SPI writes to retune radio, accept forged packets.",
            },
        ],
    },
    {
        "id": "KC-04",
        "name": "Passive Surveillance (No Transmission)",
        "severity": "HIGH",
        "hardware": "SDR only (receive-only)",
        "range": "RF range",
        "detection": "Impossible — purely passive",
        "stages": [
            {
                "stage": "RECON",
                "action": "Capture SYNC packets",
                "tool": "elrs-crypto-audit",
                "detail": "Extract UID[3:5], derive CRC init. Passive only.",
            },
            {
                "stage": "WEAPONIZE",
                "action": "Resolve FHSS sequence",
                "tool": "elrs-fhss-seed-resolve",
                "detail": "Compute hop sequence from partial UID.",
            },
            {
                "stage": "ACTIONS",
                "action": "Intercept telemetry",
                "tool": "elrs-telemetry-intercept",
                "detail": "Decode GPS, battery, attitude, flight mode — all cleartext.",
            },
            {
                "stage": "ACTIONS",
                "action": "Decode RC commands",
                "tool": "elrs-crypto-audit",
                "detail": "RC channel values readable (no encryption). Know stick inputs.",
            },
        ],
    },
    {
        "id": "KC-05",
        "name": "Configurator Supply Chain → Host RCE",
        "severity": "CRITICAL",
        "hardware": "Physical access (or DFU-modified firmware)",
        "range": "Local (USB) or remote (if firmware modified OTA)",
        "detection": "Very difficult — exploits trusted tool chain",
        "stages": [
            {
                "stage": "INSTALL",
                "action": "Flash modified FC firmware via DFU",
                "tool": "stm32-dfu-extract",
                "detail": "FC at RDP Level 0, DFU via MSP_REBOOT, no signature check.",
            },
            {
                "stage": "EXPLOIT",
                "action": "Crafted MSP responses in firmware",
                "tool": "bf-configurator-msp-xss",
                "detail": "Firmware returns XSS payloads in MSP_FC_VARIANT, MSP_BOARD_INFO.",
            },
            {
                "stage": "EXPLOIT",
                "action": "Pilot connects Configurator",
                "tool": "bf-configurator-msp-xss",
                "detail": "Electron app renders MSP strings without sanitization.",
            },
            {
                "stage": "ACTIONS",
                "action": "RCE on pilot's computer",
                "tool": "bf-configurator-msp-xss",
                "detail": "CVE-2026-70601 context-isolation bypass → Node.js require().",
            },
        ],
    },
    {
        "id": "KC-06",
        "name": "ESP-NOW Backpack → TX Compromise → Drone Pivot",
        "severity": "CRITICAL",
        "hardware": "ESP32 dev board (WiFi range)",
        "range": "WiFi range (~50m)",
        "detection": "Difficult — exploits backpack protocol",
        "stages": [
            {
                "stage": "DELIVER",
                "action": "Send crafted ESP-NOW frame",
                "tool": "espnow-backpack-overflow",
                "detail": "MSPv2 frame with 241-byte payload → 177 bytes stack overflow.",
            },
            {
                "stage": "EXPLOIT",
                "action": "RCE on Backpack ESP",
                "tool": "espnow-backpack-overflow",
                "detail": "Stack overflow → return address overwrite → shellcode.",
            },
            {
                "stage": "C2",
                "action": "Compromised Backpack → CRSF to TX",
                "tool": "espnow-backpack-overflow",
                "detail": "Backpack has CRSF serial to ELRS TX module.",
            },
            {
                "stage": "ACTIONS",
                "action": "Inject MSP commands via TX CRSF",
                "tool": "msp-command-arsenal",
                "detail": "Full MSP command injection through compromised backpack.",
            },
        ],
    },
    {
        "id": "KC-07",
        "name": "OOB Parameter Read → Full UID → Complete Takeover",
        "severity": "CRITICAL",
        "hardware": "ELRS TX module (partial session access)",
        "range": "RF range",
        "detection": "Very difficult — uses legitimate protocol frames",
        "stages": [
            {
                "stage": "RECON",
                "action": "Capture SYNC → partial UID",
                "tool": "elrs-crypto-audit",
                "detail": "UID[3:5] from SYNC, enough for partial session access.",
            },
            {
                "stage": "EXPLOIT",
                "action": "OOB parameter read → full UID",
                "tool": "crsf-parameter-indexing-oob",
                "detail": "Read beyond parameter array → extract all 6 UID bytes from config.",
            },
            {
                "stage": "WEAPONIZE",
                "action": "Derive all crypto material",
                "tool": "elrs-session-hijack",
                "detail": "Full UID → exact CRC init, exact FHSS sequence, no brute-force.",
            },
            {
                "stage": "ACTIONS",
                "action": "Complete session takeover",
                "tool": "msp-command-arsenal",
                "detail": "Full RC override + MSP command injection + failsafe manipulation.",
            },
        ],
    },
]


def show_chains(chain_id=None):
    """Display kill chains."""
    chains = KILL_CHAINS
    if chain_id:
        chains = [c for c in chains if c["id"] == chain_id.upper()]
        if not chains:
            print(f"  Unknown chain ID: {chain_id}")
            print(f"  Available: {', '.join(c['id'] for c in KILL_CHAINS)}")
            return

    for chain in chains:
        print(f"\n{'='*65}")
        print(f"[{chain['severity']}] {chain['id']}: {chain['name']}")
        print(f"{'='*65}")
        print(f"  Hardware required: {chain['hardware']}")
        print(f"  Range: {chain['range']}")
        print(f"  Detection difficulty: {chain['detection']}")

        print(f"\n  Stages:")
        for i, stage in enumerate(chain["stages"], 1):
            print(f"\n    {i}. [{stage['stage']}] {stage['action']}")
            print(f"       Tool: {stage['tool']}")
            print(f"       {stage['detail']}")


def show_matrix():
    """Show attack matrix: tools × kill chain stages."""
    print(f"\n{'='*65}")
    print("ATTACK MATRIX: TOOLS × KILL CHAIN STAGES")
    print(f"{'='*65}")

    stages = ["RECON", "WEAPONIZE", "DELIVER", "EXPLOIT", "INSTALL", "C2", "ACTIONS"]

    print(f"\n  {'Tool':<35s}", end="")
    for s in stages:
        print(f"  {s[:4]:4s}", end="")
    print()
    print(f"  {'-'*35}", end="")
    for _ in stages:
        print(f"  {'----'}", end="")
    print()

    for tool_id, tool in TOOL_SUITE.items():
        short_name = tool_id[:34]
        print(f"  {short_name:<35s}", end="")
        for s in stages:
            used = any(
                stage["tool"] == tool_id
                for chain in KILL_CHAINS
                for stage in chain["stages"]
                if stage["stage"] == s
            )
            primary = tool["stage"] == s
            marker = " [X]" if primary else "  x " if used else "  . "
            print(marker, end="")
        print()

    print(f"\n  Legend: [X] = primary stage, x = also used, . = not used")

    # Stage coverage
    print(f"\n  Stage coverage:")
    for s in stages:
        tools_in_stage = [t for t, info in TOOL_SUITE.items() if info["stage"] == s]
        print(f"    {s:12s}: {len(tools_in_stage)} tools "
              f"({', '.join(t[:25] for t in tools_in_stage)})")


def show_tools_map():
    """Map tools to their roles in kill chains."""
    print(f"\n{'='*65}")
    print("TOOL SUITE INVENTORY")
    print(f"{'='*65}")

    print(f"\n  Total tools: {len(TOOL_SUITE)}")
    print(f"  Total kill chains: {len(KILL_CHAINS)}")

    by_stage = {}
    for tool_id, info in TOOL_SUITE.items():
        stage = info["stage"]
        if stage not in by_stage:
            by_stage[stage] = []
        by_stage[stage].append((tool_id, info))

    for stage in ["RECON", "WEAPONIZE", "DELIVER", "EXPLOIT", "INSTALL", "C2", "ACTIONS"]:
        tools = by_stage.get(stage, [])
        print(f"\n  [{stage}] ({len(tools)} tools):")
        for tool_id, info in tools:
            chains_used = [
                c["id"] for c in KILL_CHAINS
                if any(s["tool"] == tool_id for s in c["stages"])
            ]
            chain_str = ", ".join(chains_used) if chains_used else "standalone"
            print(f"    {tool_id}")
            print(f"      {info['description']}")
            print(f"      Used in: {chain_str}")

    # Summary stats
    crit_chains = sum(1 for c in KILL_CHAINS if c["severity"] == "CRITICAL")
    high_chains = sum(1 for c in KILL_CHAINS if c["severity"] == "HIGH")
    print(f"\n  KILL CHAIN SUMMARY:")
    print(f"    CRITICAL: {crit_chains}")
    print(f"    HIGH: {high_chains}")
    print(f"    Total: {len(KILL_CHAINS)}")
    print(f"\n  HARDWARE REQUIREMENTS:")
    hw_counts = {}
    for c in KILL_CHAINS:
        hw = c["hardware"]
        hw_counts[hw] = hw_counts.get(hw, 0) + 1
    for hw, count in sorted(hw_counts.items(), key=lambda x: -x[1]):
        print(f"    {hw}: {count} chains")


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Kill Chain Orchestrator"
    )
    parser.add_argument(
        "--chains", action="store_true",
        help="Display all kill chains",
    )
    parser.add_argument(
        "--matrix", action="store_true",
        help="Show attack matrix (tools × stages)",
    )
    parser.add_argument(
        "--tools-map", action="store_true",
        help="Map all tools to their kill chain roles",
    )
    parser.add_argument(
        "--chain-id",
        help="Show a specific kill chain by ID (e.g., KC-01)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("ELRS Kill Chain Orchestrator")
    print(f"Target: TinyHawk III Plus (CRAZYBEEF4SX1280)")
    print(f"Tool suite: {len(TOOL_SUITE)} PoC tools")
    print(f"Kill chains: {len(KILL_CHAINS)} end-to-end paths")
    print(f"Protocol: ELRS v3.x (permanently locked, no upgrade path)")

    results = {
        "tools": len(TOOL_SUITE),
        "kill_chains": len(KILL_CHAINS),
        "critical_chains": sum(1 for c in KILL_CHAINS if c["severity"] == "CRITICAL"),
    }

    if args.chain_id:
        show_chains(args.chain_id)
    elif args.chains:
        show_chains()

    if args.matrix:
        show_matrix()

    if args.tools_map:
        show_tools_map()

    if not any([args.chains, args.matrix, args.tools_map, args.chain_id]):
        show_chains()
        show_matrix()
        show_tools_map()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
