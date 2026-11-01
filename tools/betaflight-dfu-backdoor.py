#!/usr/bin/env python3
"""
Betaflight DFU Firmware Modification Chain

Analysis tool for the firmware modification attack path on STM32F411-based
flight controllers. Demonstrates how extracted firmware can be patched
with persistent backdoors that survive power cycles and config resets.

Attack premise:
  Given physical access (or remote DFU trigger via MSP_REBOOT), an
  attacker can extract the full 512KB flash, modify the firmware, and
  reflash it. The modification persists across:
  - Power cycles
  - Betaflight configuration resets (CLI `defaults`)
  - EEPROM writes (only clear config sectors, not code)
  - OTA parameter changes

Backdoor categories:
  1. MSP command backdoor — add hidden MSP command that triggers on
     specific payload (kill switch, data exfil, mode change)
  2. Failsafe override — modify failsafe.c to ignore loss-of-signal,
     or redirect GPS rescue to attacker coordinates
  3. Telemetry exfiltration — modify telemetry to broadcast sensitive
     data (GPS coords, battery state, binding info) on known channel
  4. Trigger-based — backdoor activates only under specific conditions
     (GPS geofence, altitude threshold, time delay, specific stick combo)
  5. Supply chain — modify firmware before sale/delivery, affecting
     all future operations of the drone

DFU format:
  STM32 DFU files use a specific container format:
  - DFU prefix (11 bytes): signature, version, target count
  - Target prefix (274 bytes per target): name, alt setting, size
  - Image elements: address + data
  - DFU suffix (16 bytes): device/product/vendor IDs, CRC-32

This tool generates analysis of each backdoor category, the DFU
format for modification, and the detection difficulty for each approach.

Usage:
  python3 betaflight-dfu-backdoor.py --analyze
  python3 betaflight-dfu-backdoor.py --dfu-format
  python3 betaflight-dfu-backdoor.py --backdoor-catalog
  python3 betaflight-dfu-backdoor.py --detection

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
# DFU format constants
# ---------------------------------------------------------------------------

DFU_SIGNATURE = b"DfuSe"
DFU_VERSION = 0x01
DFU_SUFFIX_LEN = 16
DFU_PREFIX_LEN = 11
TARGET_PREFIX_LEN = 274

# STM32F411 specifics
STM32F411_FLASH_BASE = 0x08000000
STM32F411_FLASH_SIZE = 512 * 1024
STM32F411_VECTOR_TABLE_SIZE = 0x194  # Cortex-M4 vector table

# Betaflight memory layout (approximate, linker-dependent)
BF_CODE_START = 0x08000000
BF_CODE_END = 0x08060000   # ~384KB code
BF_EEPROM_START = 0x08060000  # sectors 6-7
BF_EEPROM_END = 0x08080000    # 128KB EEPROM emulation

# MSP handler locations (from msp.c source analysis)
MSP_HANDLER_ESTIMATES = {
    "mspFcProcessCommand": "~0x08010000-0x08020000 (sector 4-5, main dispatch)",
    "failsafe_update": "~0x08008000-0x0800C000 (sector 2-3)",
    "processRFPacket": "~0x08020000-0x08040000 (sector 5-6, ELRS driver)",
    "rxSpiFrameStatus": "~0x08020000-0x08040000 (sector 5-6, SPI RX)",
}


# ---------------------------------------------------------------------------
# Backdoor catalog
# ---------------------------------------------------------------------------

BACKDOOR_CATALOG = [
    {
        "id": "BD-001",
        "name": "MSP Kill Switch",
        "category": "Command backdoor",
        "description": "Add hidden MSP command (e.g., unused cmd 0xFE) that "
                      "calls disarm(DISARM_REASON_ARMING_DISABLED) on receipt. "
                      "Only an attacker who knows the command code can trigger it.",
        "persistence": "Survives power cycle, config reset, OTA changes",
        "trigger": "Specific MSP command via ELRS OTA",
        "modification_size": "~20 bytes (branch + disarm call)",
        "detection": "HARD — MSP dispatch table is a large switch statement; "
                    "one extra case is difficult to spot without full binary diff",
        "reversibility": "Reflash with known-good firmware",
    },
    {
        "id": "BD-002",
        "name": "Failsafe Redirect",
        "category": "Safety override",
        "description": "Patch failsafe GPS rescue to use hardcoded coordinates "
                      "instead of home point. Drone 'rescues' to attacker location.",
        "persistence": "Survives config reset (rescue coords are in code, not EEPROM)",
        "trigger": "Any failsafe event (signal loss, battery low)",
        "modification_size": "~32 bytes (replace coordinate load instructions)",
        "detection": "MEDIUM — GPS rescue path is testable; incorrect home-return "
                    "would be noticed during normal testing if tested outdoors",
        "reversibility": "Reflash with known-good firmware",
    },
    {
        "id": "BD-003",
        "name": "Telemetry Exfiltration",
        "category": "Data leak",
        "description": "Modify telemetry encoding to embed GPS coordinates, "
                      "battery state, or binding phrase UID in telemetry packets. "
                      "Any receiver in range captures the leaked data.",
        "persistence": "Survives power cycle",
        "trigger": "Always active (leaks on every telemetry frame)",
        "modification_size": "~64 bytes (telemetry encoding patch)",
        "detection": "MEDIUM — telemetry decoders would show unexpected values, "
                    "but most users don't monitor raw telemetry streams",
        "reversibility": "Reflash with known-good firmware",
    },
    {
        "id": "BD-004",
        "name": "Geofence Kill",
        "category": "Trigger-based",
        "description": "Add GPS geofence check: if drone crosses boundary, "
                      "force disarm or trigger failsafe. Enables area-denial "
                      "for specific geographic regions.",
        "persistence": "Survives power cycle, config reset",
        "trigger": "GPS position crossing hardcoded boundary",
        "modification_size": "~80 bytes (GPS check + disarm path)",
        "detection": "HARD — only activates in specific geography; normal testing "
                    "in a different location would never trigger it",
        "reversibility": "Reflash with known-good firmware",
    },
    {
        "id": "BD-005",
        "name": "Time-Delayed Motor Limit",
        "category": "Trigger-based",
        "description": "After N minutes of flight time (tracked via SysTick), "
                      "progressively reduce maximum motor output. Drone gradually "
                      "loses power, appears to be a battery/ESC issue.",
        "persistence": "Survives power cycle (counter resets, but triggers again)",
        "trigger": "Accumulated flight time",
        "modification_size": "~48 bytes (timer check + motor limit)",
        "detection": "VERY HARD — mimics hardware degradation; diagnosis would "
                    "check ESCs, motors, battery before suspecting firmware",
        "reversibility": "Reflash with known-good firmware",
    },
    {
        "id": "BD-006",
        "name": "Stick Combo Activation",
        "category": "Command backdoor",
        "description": "Specific stick combination (e.g., throttle-min + yaw-left + "
                      "roll-right + pitch-down held for 2s) activates hidden mode: "
                      "data exfil, config dump via telemetry, or kill switch.",
        "persistence": "Survives power cycle",
        "trigger": "Specific stick combination (known only to attacker)",
        "modification_size": "~96 bytes (stick check + mode activation)",
        "detection": "VERY HARD — specific combos are almost never accidentally hit; "
                    "normal flight testing would never trigger it",
        "reversibility": "Reflash with known-good firmware",
    },
    {
        "id": "BD-007",
        "name": "Configurator Supply Chain",
        "category": "Supply chain",
        "description": "Modify MSP response handler to return XSS payload in craft "
                      "name (MSP_NAME). When victim connects Configurator, XSS "
                      "executes → CVE-2026-70601 context-isolation bypass → host RCE.",
        "persistence": "Survives power cycle; XSS payload stored in code, not EEPROM",
        "trigger": "Victim connects Betaflight Configurator to the FC",
        "modification_size": "~48 bytes (MSP_NAME response patch)",
        "detection": "MEDIUM — craft name is visible in Configurator, but XSS may "
                    "execute before user notices; many users ignore craft name field",
        "reversibility": "Reflash with known-good firmware (but host may be compromised)",
    },
]


def analyze_dfu_format():
    """Analyze the STM32 DFU file format for firmware modification."""
    print(f"\n{'='*65}")
    print("STM32 DFU FILE FORMAT")
    print(f"{'='*65}")

    print(f"\n  DFU container structure:")
    print(f"    [DFU Prefix — {DFU_PREFIX_LEN} bytes]")
    print(f"      Signature: 'DfuSe' (5 bytes)")
    print(f"      Version: 0x{DFU_VERSION:02x}")
    print(f"      DFU image size: uint32_t (total file size)")
    print(f"      Number of targets: uint8_t")
    print(f"    [Target Prefix — {TARGET_PREFIX_LEN} bytes per target]")
    print(f"      Target signature: 'Target' (6 bytes)")
    print(f"      Alternate setting: uint8_t")
    print(f"      Named: uint32_t (0 or 1)")
    print(f"      Target name: char[255]")
    print(f"      Target size: uint32_t")
    print(f"      Number of elements: uint32_t")
    print(f"    [Image Elements]")
    print(f"      Element address: uint32_t (flash address)")
    print(f"      Element size: uint32_t")
    print(f"      Element data: uint8_t[size]")
    print(f"    [DFU Suffix — {DFU_SUFFIX_LEN} bytes]")
    print(f"      BCD device: uint16_t")
    print(f"      Product ID: uint16_t")
    print(f"      Vendor ID: uint16_t")
    print(f"      DFU spec: uint16_t (0x011A)")
    print(f"      Signature: 'UFD' (3 bytes, reversed)")
    print(f"      Suffix length: uint8_t ({DFU_SUFFIX_LEN})")
    print(f"      CRC-32: uint32_t (over entire file)")

    print(f"\n  Modification procedure:")
    print(f"    1. Parse DFU file → extract image element(s)")
    print(f"    2. Locate target code in the binary (using known offsets)")
    print(f"    3. Patch bytes at the target location")
    print(f"    4. Recompute DFU suffix CRC-32")
    print(f"    5. Flash modified DFU via STM32CubeProgrammer or dfu-util")

    print(f"\n  STM32F411 flash layout for TinyHawk III Plus:")
    print(f"    Code:   0x{BF_CODE_START:08X} — 0x{BF_CODE_END:08X} ({(BF_CODE_END-BF_CODE_START)//1024}KB)")
    print(f"    EEPROM: 0x{BF_EEPROM_START:08X} — 0x{BF_EEPROM_END:08X} ({(BF_EEPROM_END-BF_EEPROM_START)//1024}KB)")
    print(f"    Total:  {STM32F411_FLASH_SIZE//1024}KB")


def catalog_backdoors():
    """Display the full backdoor catalog."""
    print(f"\n{'='*65}")
    print("FIRMWARE BACKDOOR CATALOG")
    print(f"{'='*65}")

    for bd in BACKDOOR_CATALOG:
        print(f"\n  {bd['id']}: {bd['name']} [{bd['category']}]")
        print(f"    Description: {bd['description']}")
        print(f"    Trigger: {bd['trigger']}")
        print(f"    Persistence: {bd['persistence']}")
        print(f"    Patch size: {bd['modification_size']}")
        print(f"    Detection: {bd['detection']}")
        print(f"    Recovery: {bd['reversibility']}")


def analyze_detection():
    """Analyze detection difficulty for firmware backdoors."""
    print(f"\n{'='*65}")
    print("BACKDOOR DETECTION ANALYSIS")
    print(f"{'='*65}")

    print(f"\n  Detection methods and their limitations:")

    methods = [
        {
            "name": "Binary diff against known-good firmware",
            "effectiveness": "PERFECT — catches any modification",
            "limitation": "Requires known-good binary for exact HW/version combo. "
                        "Most users don't keep the original firmware binary.",
            "practical": "Low — few users perform firmware verification",
        },
        {
            "name": "Hash verification",
            "effectiveness": "PERFECT — if reference hash exists",
            "limitation": "EMAX doesn't publish firmware hashes. Betaflight releases "
                        "are generic; target-specific builds have different hashes.",
            "practical": "Very low — no reference hashes available",
        },
        {
            "name": "Behavioral testing",
            "effectiveness": "PARTIAL — catches some backdoors",
            "limitation": "Trigger-based backdoors (geofence, time-delay, stick combo) "
                        "won't activate during normal testing. MSP kill switch requires "
                        "knowing the hidden command code.",
            "practical": "Low — most users don't systematically test every flight mode",
        },
        {
            "name": "Code review / disassembly",
            "effectiveness": "HIGH — catches most modifications",
            "limitation": "Requires ARM Cortex-M4 disassembly expertise and intimate "
                        "knowledge of Betaflight internals. 384KB of stripped binary.",
            "practical": "Very low — requires specialist skills",
        },
        {
            "name": "Runtime monitoring (debug probe)",
            "effectiveness": "HIGH — can trace execution paths",
            "limitation": "Requires SWD/JTAG debug probe and real-time analysis. "
                        "Trigger-based backdoors still need activation conditions.",
            "practical": "Very low — requires hardware tools and expertise",
        },
    ]

    for method in methods:
        print(f"\n  {method['name']}:")
        print(f"    Effectiveness: {method['effectiveness']}")
        print(f"    Limitation: {method['limitation']}")
        print(f"    Practical adoption: {method['practical']}")

    print(f"\n  Summary: Firmware backdoors on consumer FCs are effectively")
    print(f"  undetectable in practice. No hash verification infrastructure")
    print(f"  exists, behavioral testing misses trigger-based backdoors, and")
    print(f"  binary analysis requires specialist skills most pilots lack.")


def main():
    parser = argparse.ArgumentParser(
        description="Betaflight DFU Firmware Modification Chain"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Overall analysis of the firmware modification attack",
    )
    parser.add_argument(
        "--dfu-format", action="store_true",
        help="Analyze STM32 DFU file format",
    )
    parser.add_argument(
        "--backdoor-catalog", action="store_true",
        help="Full catalog of firmware backdoor types",
    )
    parser.add_argument(
        "--detection", action="store_true",
        help="Analysis of backdoor detection difficulty",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("Betaflight DFU Firmware Modification Chain")
    print(f"Target: STM32F411CEU6 (EMAX TinyHawk III Plus)")
    print(f"Flash: {STM32F411_FLASH_SIZE//1024}KB at 0x{STM32F411_FLASH_BASE:08X}")
    print(f"Expected RDP: Level 0 (fully extractable)")
    print(f"Backdoor categories: {len(BACKDOOR_CATALOG)}")

    results = {
        "target_mcu": "STM32F411CEU6",
        "flash_size": STM32F411_FLASH_SIZE,
        "backdoor_count": len(BACKDOOR_CATALOG),
    }

    if args.analyze or args.dfu_format:
        analyze_dfu_format()
        results["mode"] = "dfu-format"

    if args.analyze or args.backdoor_catalog:
        catalog_backdoors()
        results["backdoors"] = BACKDOOR_CATALOG
        results["mode"] = "backdoor-catalog"

    if args.analyze or args.detection:
        analyze_detection()
        results["mode"] = "detection"

    if not any([args.analyze, args.dfu_format, args.backdoor_catalog, args.detection]):
        analyze_dfu_format()
        catalog_backdoors()
        analyze_detection()

    # Assessment
    print(f"\n{'='*65}")
    print("ASSESSMENT")
    print(f"{'='*65}")
    print("  Firmware modification on STM32F411 at RDP Level 0 is trivial")
    print("  given physical access. The DFU format is well-documented,")
    print("  binary patching requires only a hex editor and ARM knowledge,")
    print("  and the resulting backdoor is effectively undetectable.")
    print()
    print("  The 7 backdoor categories range from simple kill switches")
    print("  (~20 bytes) to sophisticated trigger-based attacks that mimic")
    print("  hardware failures. All survive power cycles and config resets.")
    print()
    print("  The supply chain variant (BD-007) is especially concerning:")
    print("  a modified FC sold to an unsuspecting buyer carries a permanent")
    print("  backdoor that also compromises the pilot's computer when they")
    print("  connect Betaflight Configurator (via XSS → CVE-2026-70601).")
    print()
    print("  No practical detection mechanism exists for consumer FCs.")
    print("  Firmware hash verification is not available, behavioral testing")
    print("  misses trigger-based attacks, and binary analysis requires")
    print("  specialist skills. The defense is: buy from trusted sources")
    print("  and flash known-good firmware yourself before first flight.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
