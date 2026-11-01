#!/usr/bin/env python3
"""
Supply-Chain Firmware Verification & Provenance Analysis

Analyzes the build/distribution supply chain for ELRS and Betaflight
firmware to detect modification, substitution, or tampering.

Attack surfaces covered:
  - Hardware vendor firmware modifications (EMAX pre-flash)
  - PlatformIO / Arduino-ESP32 dependency confusion
  - Betaflight cloud build integrity
  - ELRS binary release verification
  - STM32 firmware comparison (DFU extract vs official release)

Supply chain threat model:
  1. VENDOR MODIFICATION: EMAX ships modified Betaflight; no mechanism
     to verify the pre-flashed binary matches official releases
  2. BUILD SYSTEM COMPROMISE: Betaflight cloud build or ELRS CI could
     produce backdoored binaries; reproducible builds are possible but
     not enforced
  3. DEPENDENCY CONFUSION: PlatformIO packages are community-maintained;
     typosquatting or malicious updates could inject code
  4. DISTRIBUTION TAMPERING: GitHub release binaries could be swapped
     after signing (no GPG signature on most drone firmware releases)
  5. UPDATE CHANNEL MitM: ELRS WiFi OTA has no TLS certificate pinning;
     Betaflight configurator checks GitHub API over HTTPS but does not
     verify binary signatures

Firmware formats analyzed:
  - Betaflight .hex (Intel HEX, STM32 flash image)
  - ELRS .bin (ESP8266/ESP32 firmware, OTA format)
  - STM32 DFU (USB DFU suffix with VID/PID/version)

Usage:
  python3 supply-chain-verify.py --analyze
  python3 supply-chain-verify.py --betaflight-chain
  python3 supply-chain-verify.py --elrs-chain
  python3 supply-chain-verify.py --hex-audit <file.hex>
  python3 supply-chain-verify.py --chain

For authorized security research and CTF use only.
"""

import argparse
import hashlib
import json
import os
import struct
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# Intel HEX parser (Betaflight .hex format)
# ---------------------------------------------------------------------------

IHEX_DATA = 0x00
IHEX_EOF = 0x01
IHEX_EXT_SEG_ADDR = 0x02
IHEX_START_SEG_ADDR = 0x03
IHEX_EXT_LINEAR_ADDR = 0x04
IHEX_START_LINEAR_ADDR = 0x05

IHEX_TYPE_NAMES = {
    IHEX_DATA: "Data",
    IHEX_EOF: "End of File",
    IHEX_EXT_SEG_ADDR: "Extended Segment Address",
    IHEX_START_SEG_ADDR: "Start Segment Address",
    IHEX_EXT_LINEAR_ADDR: "Extended Linear Address",
    IHEX_START_LINEAR_ADDR: "Start Linear Address",
}


def parse_ihex_line(line):
    """Parse one Intel HEX record."""
    line = line.strip()
    if not line.startswith(":"):
        return None
    raw = bytes.fromhex(line[1:])
    byte_count = raw[0]
    address = struct.unpack(">H", raw[1:3])[0]
    record_type = raw[3]
    data = raw[4:4 + byte_count]
    checksum = raw[4 + byte_count]
    # Verify checksum
    calc = (~sum(raw[:-1]) + 1) & 0xFF
    valid = calc == checksum
    return {
        "byte_count": byte_count,
        "address": address,
        "type": record_type,
        "type_name": IHEX_TYPE_NAMES.get(record_type, "Unknown"),
        "data": data,
        "checksum": checksum,
        "checksum_valid": valid,
    }


def analyze_hex_file(path):
    """Analyze an Intel HEX firmware file."""
    print(f"\n  Analyzing: {path}")

    if not os.path.exists(path):
        print(f"    ERROR: File not found")
        return None

    records = []
    total_data_bytes = 0
    base_address = 0
    entry_point = None
    regions = []
    current_region_start = None
    current_region_end = None
    bad_checksums = 0

    with open(path, "r") as f:
        for line_num, line in enumerate(f, 1):
            rec = parse_ihex_line(line)
            if rec is None:
                continue
            records.append(rec)

            if not rec["checksum_valid"]:
                bad_checksums += 1

            if rec["type"] == IHEX_EXT_LINEAR_ADDR:
                base_address = struct.unpack(">H", rec["data"])[0] << 16

            elif rec["type"] == IHEX_DATA:
                addr = base_address + rec["address"]
                total_data_bytes += rec["byte_count"]
                if current_region_start is None:
                    current_region_start = addr
                    current_region_end = addr + rec["byte_count"]
                elif addr == current_region_end:
                    current_region_end = addr + rec["byte_count"]
                else:
                    regions.append((current_region_start, current_region_end))
                    current_region_start = addr
                    current_region_end = addr + rec["byte_count"]

            elif rec["type"] == IHEX_START_LINEAR_ADDR:
                entry_point = struct.unpack(">I", rec["data"])[0]

    if current_region_start is not None:
        regions.append((current_region_start, current_region_end))

    # Compute SHA-256 of the raw file
    with open(path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()

    print(f"    File SHA-256: {file_hash}")
    print(f"    Total records: {len(records)}")
    print(f"    Data bytes: {total_data_bytes} ({total_data_bytes / 1024:.1f} KB)")
    print(f"    Bad checksums: {bad_checksums}")
    print(f"    Memory regions: {len(regions)}")
    for start, end in regions:
        print(f"      0x{start:08X} - 0x{end:08X} ({end - start} bytes)")
    if entry_point is not None:
        print(f"    Entry point: 0x{entry_point:08X}")

    return {
        "file": path,
        "sha256": file_hash,
        "records": len(records),
        "data_bytes": total_data_bytes,
        "bad_checksums": bad_checksums,
        "regions": [(hex(s), hex(e)) for s, e in regions],
        "entry_point": hex(entry_point) if entry_point else None,
    }


# ---------------------------------------------------------------------------
# STM32 DFU suffix parser
# ---------------------------------------------------------------------------

DFU_SUFFIX_SIZE = 16
DFU_SIGNATURE = b"UFD"  # reversed "DFU"


def parse_dfu_suffix(data):
    """Parse a STM32 DFU file suffix."""
    if len(data) < DFU_SUFFIX_SIZE:
        return None
    suffix = data[-DFU_SUFFIX_SIZE:]
    # DFU suffix format (last 16 bytes):
    # [0:2]  bcdDevice
    # [2:4]  idProduct
    # [4:6]  idVendor
    # [6:8]  bcdDFU (DFU spec version, typically 0x011A)
    # [8:11] ucDfuSignature ("UFD")
    # [11]   bLength (16)
    # [12:16] dwCRC
    bcd_device = struct.unpack_from("<H", suffix, 0)[0]
    id_product = struct.unpack_from("<H", suffix, 2)[0]
    id_vendor = struct.unpack_from("<H", suffix, 4)[0]
    bcd_dfu = struct.unpack_from("<H", suffix, 6)[0]
    signature = suffix[8:11]
    length = suffix[11]
    crc = struct.unpack_from("<I", suffix, 12)[0]

    return {
        "bcdDevice": f"0x{bcd_device:04X}",
        "idProduct": f"0x{id_product:04X}",
        "idVendor": f"0x{id_vendor:04X}",
        "bcdDFU": f"0x{bcd_dfu:04X}",
        "signature": signature.decode("ascii", errors="replace"),
        "signature_valid": signature == DFU_SIGNATURE,
        "length": length,
        "crc": f"0x{crc:08X}",
    }


# ---------------------------------------------------------------------------
# Betaflight supply chain analysis
# ---------------------------------------------------------------------------

BF_TARGETS = {
    "EMAX_TINYHAWKF4SX1280": {
        "mcu": "STM32F411CEU6",
        "flash_size": 512 * 1024,
        "ram_size": 128 * 1024,
        "clock": 100_000_000,
        "expected_version": "4.4.2",
        "cloud_build": True,
        "official_url": "https://github.com/betaflight/betaflight/releases/tag/4.4.2",
    },
}

BF_CLOUD_BUILD = {
    "endpoint": "https://build.betaflight.com/api/builds",
    "auth": "None (public API)",
    "signing": "None (no signature on output binary)",
    "reproducibility": "Possible but not enforced",
    "build_log": "Available via API, includes compile flags",
    "risks": [
        "Build server compromise would silently backdoor all cloud builds",
        "No binary signature — output .hex is unsigned",
        "Build log is self-reported by the build server",
        "No client-side verification of build reproducibility",
    ],
}


def analyze_betaflight_chain():
    """Analyze the Betaflight firmware supply chain."""
    print("\n" + "=" * 70)
    print("Betaflight Supply Chain Analysis")
    print("=" * 70)

    target = BF_TARGETS["EMAX_TINYHAWKF4SX1280"]
    print(f"\n  TARGET: EMAX_TINYHAWKF4SX1280")
    print(f"    MCU: {target['mcu']}")
    print(f"    Flash: {target['flash_size'] // 1024} KB")
    print(f"    RAM: {target['ram_size'] // 1024} KB")
    print(f"    Expected BF version: {target['expected_version']}")

    print(f"\n  BUILD PIPELINE:")
    print(f"    Source: github.com/betaflight/betaflight (tag 4.4.2)")
    print(f"    Toolchain: ARM GCC (arm-none-eabi-gcc)")
    print(f"    Build system: Make + STM32 HAL")
    print(f"    Output: .hex (Intel HEX) for STM32 targets")
    print(f"    Cloud build: {BF_CLOUD_BUILD['endpoint']}")
    print(f"    Build auth: {BF_CLOUD_BUILD['auth']}")
    print(f"    Binary signing: {BF_CLOUD_BUILD['signing']}")

    print(f"\n  SUPPLY CHAIN RISKS:")
    for i, risk in enumerate(BF_CLOUD_BUILD["risks"], 1):
        print(f"    {i}. {risk}")

    print(f"\n  VENDOR (EMAX) RISKS:")
    print(f"    1. EMAX pre-flashes firmware at factory")
    print(f"    2. No mechanism to verify factory flash matches official release")
    print(f"    3. No secure boot on STM32F411 (RDP likely Level 0)")
    print(f"    4. Modified builds could include:")
    print(f"       - Telemetry exfiltration (extra MSP responses)")
    print(f"       - Backdoor commands (custom MSP IDs)")
    print(f"       - Modified failsafe behavior")
    print(f"       - Disabled safety checks")
    print(f"    5. Only detection: DFU extract + binary diff against official")

    print(f"\n  VERIFICATION PROCEDURE (bench test):")
    print(f"    Step 1: Enter DFU mode (MSP_REBOOT to bootloader, or boot0 pin)")
    print(f"    Step 2: Extract flash via STM32 DFU (dfu-util or STM32CubeProgrammer)")
    print(f"    Step 3: Download official BF 4.4.2 .hex for EMAX_TINYHAWKF4SX1280")
    print(f"    Step 4: Convert both to flat binary, SHA-256 compare")
    print(f"    Step 5: If different, binary diff to identify modified regions")
    print(f"    Step 6: Cross-reference modified addresses with BF symbol map")

    print(f"\n  STM32F411 SECURITY STATE (expected):")
    print(f"    RDP Level: 0 (no read protection) — UNVERIFIED")
    print(f"    Secure boot: NOT AVAILABLE on F411")
    print(f"    Debug access: SWD likely unrestricted")
    print(f"    Option bytes: Writable (BOR level, WDG config)")
    print(f"    HARDWARE GATE: RDP check requires SWD probe or DFU entry")


# ---------------------------------------------------------------------------
# ELRS supply chain analysis
# ---------------------------------------------------------------------------

ELRS_BUILD = {
    "framework": "PlatformIO + Arduino-ESP32 (for ESP targets)",
    "platforms": ["ESP8285", "ESP32", "ESP32-C3", "STM32 (via native)"],
    "package_registry": "PlatformIO Registry (community packages)",
    "signing": "None (no firmware signature on OTA binaries)",
    "update_channels": [
        "WiFi OTA (HTTP, no TLS cert pinning)",
        "UART passthrough (Betaflight → ELRS)",
        "USB direct flash (STM32/ESP targets)",
    ],
    "risks": [
        "PlatformIO registry: dependency confusion / typosquatting",
        "Arduino-ESP32 core: CVE-2025-53540 (CSRF on OTA endpoint)",
        "ESP-IDF upstream: multiple CVEs (ESP-NOW, BLE provisioning)",
        "No firmware signing: OTA accepts ANY binary",
        "WiFi OTA: HTTP only, no certificate pinning",
        "ELRS Backpack: separate firmware, same OTA vulnerabilities",
        "Build reproducibility: possible but requires exact env match",
    ],
}

PLATFORMIO_DEPS = [
    {"name": "framework-arduinoespressif32", "risk": "HIGH",
     "note": "Arduino-ESP32 core — CVE-2025-53540 affects OTA"},
    {"name": "framework-arduinoespressif8266", "risk": "MEDIUM",
     "note": "ESP8266 Arduino core — smaller attack surface"},
    {"name": "toolchain-xtensa-esp32", "risk": "LOW",
     "note": "Espressif-maintained GCC toolchain"},
    {"name": "tool-esptoolpy", "risk": "MEDIUM",
     "note": "Flash tool — handles partition tables and images"},
    {"name": "tool-openocd-esp32", "risk": "LOW",
     "note": "Debug tool — not in runtime firmware"},
]


def analyze_elrs_chain():
    """Analyze the ELRS firmware supply chain."""
    print("\n" + "=" * 70)
    print("ELRS Supply Chain Analysis")
    print("=" * 70)

    print(f"\n  BUILD FRAMEWORK: {ELRS_BUILD['framework']}")
    print(f"  Target platforms: {', '.join(ELRS_BUILD['platforms'])}")
    print(f"  Package registry: {ELRS_BUILD['package_registry']}")
    print(f"  Binary signing: {ELRS_BUILD['signing']}")

    print(f"\n  UPDATE CHANNELS:")
    for ch in ELRS_BUILD["update_channels"]:
        print(f"    - {ch}")

    print(f"\n  SUPPLY CHAIN RISKS:")
    for i, risk in enumerate(ELRS_BUILD["risks"], 1):
        print(f"    {i}. {risk}")

    print(f"\n  PLATFORMIO DEPENDENCY ANALYSIS:")
    for dep in PLATFORMIO_DEPS:
        print(f"    {dep['name']}")
        print(f"      Risk: {dep['risk']} — {dep['note']}")

    print(f"\n  DEPENDENCY CONFUSION SCENARIOS:")
    print(f"    1. Typosquatting: register 'expresslrs' vs 'express-lrs' on PIO")
    print(f"    2. Version pinning gap: platformio.ini may use '^' ranges")
    print(f"    3. Transitive dependency: Arduino-ESP32 pulls in ESP-IDF components")
    print(f"    4. Build script injection: custom PIO build scripts run at build time")
    print(f"    5. SBOM gap: no dependency lock file in ELRS releases")

    print(f"\n  VERIFICATION PROCEDURE:")
    print(f"    Step 1: Clone ELRS at exact release tag")
    print(f"    Step 2: Record all PlatformIO dependency versions + hashes")
    print(f"    Step 3: Build locally in clean environment")
    print(f"    Step 4: Compare output .bin SHA-256 against official release")
    print(f"    Step 5: If different, compare ELF sections for code changes")
    print(f"    Step 6: Cross-reference with ESP-IDF CVE database")

    print(f"\n  ELRS-SPECIFIC RISKS:")
    print(f"    - Binding phrase stored in plaintext flash (extractable)")
    print(f"    - WiFi credentials stored in flash (NVS partition)")
    print(f"    - UID derived from MD5(phrase) — no hardware-backed secret")
    print(f"    - OTA endpoint trusts any uploaded binary (no signature)")
    print(f"    - Backpack firmware is separately updatable via same channel")


def analyze_chains():
    """Analyze supply chain attack chains."""
    print("\n" + "=" * 70)
    print("Supply Chain Attack Chains")
    print("=" * 70)

    chains = [
        {
            "id": "SC-KC-01",
            "name": "Vendor Firmware Modification",
            "severity": "HIGH",
            "stages": [
                "WEAPONIZE: Modify Betaflight source (add MSP backdoor command)",
                "DELIVER: Vendor (or compromised vendor) flashes modified FW at factory",
                "INSTALL: Drone ships with backdoored firmware",
                "C2: Attacker sends custom MSP command via ELRS OTA",
                "ACTIONS: Backdoor executes (motor control, config exfil, etc.)",
            ],
            "detection": "DFU extract + binary diff against official release",
            "hardware_gate": "Requires DFU entry (bench test)",
        },
        {
            "id": "SC-KC-02",
            "name": "PlatformIO Dependency Poisoning",
            "severity": "MEDIUM",
            "stages": [
                "RECON: Identify ELRS PlatformIO dependencies without lock files",
                "WEAPONIZE: Register typosquatted package or compromise existing",
                "DELIVER: Developer builds ELRS pulling poisoned dependency",
                "INSTALL: Backdoored firmware distributed via GitHub release",
                "ACTIONS: Persistent compromise of all flashed devices",
            ],
            "detection": "Dependency auditing, reproducible builds, SBOM comparison",
            "hardware_gate": "None (software supply chain)",
        },
        {
            "id": "SC-KC-03",
            "name": "Cloud Build Compromise",
            "severity": "CRITICAL",
            "stages": [
                "RECON: Identify Betaflight cloud build infrastructure",
                "WEAPONIZE: Compromise build server (credentials, CI injection)",
                "DELIVER: Modified compiler or build script produces backdoored .hex",
                "INSTALL: Pilots flash via Betaflight Configurator (trusts cloud build)",
                "ACTIONS: Mass compromise — all cloud-built firmware affected",
            ],
            "detection": "Reproducible builds, binary comparison, build log auditing",
            "hardware_gate": "None (infrastructure attack)",
        },
        {
            "id": "SC-KC-04",
            "name": "WiFi OTA MitM Firmware Replacement",
            "severity": "HIGH",
            "stages": [
                "RECON: Target enters WiFi mode (auto-timeout or forced via MSP)",
                "WEAPONIZE: Prepare malicious firmware binary",
                "DELIVER: ARP spoof or evil twin AP, intercept OTA HTTP request",
                "EXPLOIT: Replace firmware binary in transit (HTTP, no TLS pinning)",
                "INSTALL: Device flashes attacker firmware — persistent compromise",
            ],
            "detection": "Firmware signature verification (not implemented in ELRS)",
            "hardware_gate": "Requires WiFi proximity",
        },
    ]

    for c in chains:
        print(f"\n  CHAIN {c['id']}: {c['name']}")
        print(f"    Severity: {c['severity']}")
        for stage in c["stages"]:
            print(f"      {stage}")
        print(f"    Detection: {c['detection']}")
        print(f"    Hardware gate: {c['hardware_gate']}")

    print("\n  OVERALL ASSESSMENT:")
    print("    The drone firmware ecosystem lacks fundamental supply chain controls:")
    print("    - No binary signing on Betaflight or ELRS releases")
    print("    - No secure boot on STM32F4 targets")
    print("    - No dependency lock files in standard ELRS builds")
    print("    - No firmware attestation mechanism")
    print("    - HTTP-only OTA with no certificate pinning")
    print("    The verification gap is structural: even a careful operator cannot")
    print("    verify firmware provenance without extracting and comparing binaries.")


def main():
    parser = argparse.ArgumentParser(
        description="Supply-Chain Firmware Verification Analysis"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze overall supply chain risks",
    )
    parser.add_argument(
        "--betaflight-chain", action="store_true",
        help="Analyze Betaflight build/distribution chain",
    )
    parser.add_argument(
        "--elrs-chain", action="store_true",
        help="Analyze ELRS build/distribution chain",
    )
    parser.add_argument(
        "--hex-audit", metavar="FILE",
        help="Audit an Intel HEX firmware file",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze supply chain attack chains",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("Supply-Chain Firmware Verification & Provenance Analysis")
    print(f"Target: EMAX TinyHawk III Plus (EMAX_TINYHAWKF4SX1280)")
    print(f"Firmware: Betaflight 4.4.2 + ELRS (SX1280 SPI RX)")
    print(f"MCU: STM32F411CEU6 (no secure boot)")
    print(f"Impact: Unverifiable firmware provenance")

    results = {
        "target": "EMAX_TINYHAWKF4SX1280",
        "betaflight_version": "4.4.2",
        "secure_boot": False,
        "binary_signing": False,
        "supply_chain_chains": 4,
    }

    if args.hex_audit:
        results["hex_audit"] = analyze_hex_file(args.hex_audit)
    elif args.betaflight_chain:
        analyze_betaflight_chain()
    elif args.elrs_chain:
        analyze_elrs_chain()
    elif args.chain:
        analyze_chains()
    elif args.analyze:
        analyze_betaflight_chain()
        analyze_elrs_chain()
    else:
        analyze_betaflight_chain()
        analyze_elrs_chain()
        analyze_chains()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
