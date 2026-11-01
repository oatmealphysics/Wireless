#!/usr/bin/env python3
"""
SX1280 SPI Write Primitive Analysis

Analyzes whether Semtech LR11xx CVEs (CVE-2025-14857, CVE-2025-14858,
CVE-2025-14859) have analogues on the SX1280 radio used in ELRS.

The LR11xx vulnerabilities allow:
  - CVE-2025-14857: RCE via crafted OTA packets processed by LR11xx firmware
  - CVE-2025-14858: Memory corruption via malformed radio commands
  - CVE-2025-14859: Bypass of radio security features

Key architectural difference:
  The LR11xx has UPDATABLE FIRMWARE (flash-based), while the SX1280 uses
  a MASK ROM with fixed firmware. This means:
  - LR11xx firmware update attacks do NOT directly transfer to SX1280
  - BUT: SX1280 SPI register writes are still a viable attack surface
  - The FC (STM32F411) sends SPI commands to configure the SX1280
  - A compromised ELRS stack can write arbitrary SX1280 registers

SX1280 SPI interface on CRAZYBEEF4SX1280:
  - SPI Bus 3 (PB3=SCK, PB4=MISO, PB5=MOSI)
  - CS: PA15, RST: PC15, BUSY: PC14, DIO1: PA1
  - ELRS rx_spi driver has full register access
  - No SPI command filtering or validation

This tool maps the SX1280 SPI attack surface and analyzes what a
compromised FC or malicious firmware can achieve via SPI writes.

Usage:
  python3 sx1280-spi-write-analysis.py --analyze
  python3 sx1280-spi-write-analysis.py --registers
  python3 sx1280-spi-write-analysis.py --attack-surface
  python3 sx1280-spi-write-analysis.py --chain

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
# SX1280 SPI command set (from Semtech SX1280 datasheet Rev 3.2)
# ---------------------------------------------------------------------------

SX1280_COMMANDS = {
    "GetStatus": {
        "opcode": 0xC0,
        "direction": "read",
        "params": 0,
        "description": "Get radio status byte",
        "abuse_potential": "LOW — read only",
    },
    "WriteRegister": {
        "opcode": 0x18,
        "direction": "write",
        "params": "variable",
        "description": "Write to register address (16-bit addr + data)",
        "abuse_potential": "CRITICAL — arbitrary register write",
    },
    "ReadRegister": {
        "opcode": 0x19,
        "direction": "read",
        "params": "variable",
        "description": "Read from register address",
        "abuse_potential": "LOW — read only, but info leak",
    },
    "WriteBuffer": {
        "opcode": 0x1A,
        "direction": "write",
        "params": "variable",
        "description": "Write to TX FIFO buffer",
        "abuse_potential": "HIGH — controls transmitted data",
    },
    "ReadBuffer": {
        "opcode": 0x1B,
        "direction": "read",
        "params": "variable",
        "description": "Read from RX FIFO buffer",
        "abuse_potential": "LOW — read only",
    },
    "SetSleep": {
        "opcode": 0x84,
        "direction": "write",
        "params": 1,
        "description": "Enter sleep mode",
        "abuse_potential": "MEDIUM — can disable radio",
    },
    "SetStandby": {
        "opcode": 0x80,
        "direction": "write",
        "params": 1,
        "description": "Enter standby mode",
        "abuse_potential": "MEDIUM — can disable radio",
    },
    "SetTx": {
        "opcode": 0x83,
        "direction": "write",
        "params": 3,
        "description": "Enter TX mode with timeout",
        "abuse_potential": "HIGH — can force transmission",
    },
    "SetRx": {
        "opcode": 0x82,
        "direction": "write",
        "params": 3,
        "description": "Enter RX mode with timeout",
        "abuse_potential": "MEDIUM — controls receive window",
    },
    "SetRfFrequency": {
        "opcode": 0x86,
        "direction": "write",
        "params": 3,
        "description": "Set RF frequency (2400-2500 MHz)",
        "abuse_potential": "HIGH — can detune or set out-of-band",
    },
    "SetTxParams": {
        "opcode": 0x8E,
        "direction": "write",
        "params": 2,
        "description": "Set TX power and ramp time",
        "abuse_potential": "HIGH — can set max power, damage PA",
    },
    "SetPacketType": {
        "opcode": 0x8A,
        "direction": "write",
        "params": 1,
        "description": "Set modulation (GFSK, LoRa, FLRC, BLE)",
        "abuse_potential": "HIGH — can switch to BLE/FLRC",
    },
    "SetModulationParams": {
        "opcode": 0x8B,
        "direction": "write",
        "params": 3,
        "description": "Set modulation parameters (SF, BW, CR)",
        "abuse_potential": "HIGH — can make link incompatible",
    },
    "SetPacketParams": {
        "opcode": 0x8C,
        "direction": "write",
        "params": 7,
        "description": "Set packet format parameters",
        "abuse_potential": "HIGH — controls packet structure",
    },
    "SetDioIrqParams": {
        "opcode": 0x8D,
        "direction": "write",
        "params": 8,
        "description": "Configure DIO interrupt mapping",
        "abuse_potential": "MEDIUM — can suppress/redirect IRQs",
    },
    "SetAutoFs": {
        "opcode": 0x9E,
        "direction": "write",
        "params": 1,
        "description": "Auto frequency synthesis after TX/RX",
        "abuse_potential": "LOW",
    },
    "SetRegulatorMode": {
        "opcode": 0x96,
        "direction": "write",
        "params": 1,
        "description": "Set voltage regulator mode (LDO/DC-DC)",
        "abuse_potential": "MEDIUM — wrong mode can damage hardware",
    },
    "SetLongPreamble": {
        "opcode": 0x9B,
        "direction": "write",
        "params": 1,
        "description": "Enable long preamble for ranging",
        "abuse_potential": "LOW",
    },
    "SetSaveContext": {
        "opcode": 0xD5,
        "direction": "write",
        "params": 0,
        "description": "Save current radio context",
        "abuse_potential": "MEDIUM — persists malicious config",
    },
}

# Security-relevant SX1280 registers (from datasheet)
SX1280_REGISTERS = [
    {
        "addr": 0x0891,
        "name": "SyncWord (LoRa)",
        "size": 2,
        "impact": "CRITICAL — controls sync word, can impersonate or jam",
    },
    {
        "addr": 0x09C0,
        "name": "SyncWord (FLRC/GFSK) [0]",
        "size": 5,
        "impact": "HIGH — controls GFSK sync word bytes",
    },
    {
        "addr": 0x09C8,
        "name": "CRC Init (FLRC/GFSK)",
        "size": 2,
        "impact": "CRITICAL — controls CRC initializer (ELRS auth bypass)",
    },
    {
        "addr": 0x09C6,
        "name": "CRC Polynomial (FLRC/GFSK)",
        "size": 2,
        "impact": "HIGH — modifying polynomial breaks all CRC validation",
    },
    {
        "addr": 0x0944,
        "name": "WhiteningInit (GFSK)",
        "size": 1,
        "impact": "MEDIUM — controls data whitening seed",
    },
    {
        "addr": 0x0846,
        "name": "TxClampConfig",
        "size": 1,
        "impact": "MEDIUM — PA clamping, incorrect value can damage PA",
    },
    {
        "addr": 0x0889,
        "name": "FrequencyErrorIndicator",
        "size": 3,
        "impact": "LOW — read-only frequency error",
    },
]

# CRAZYBEEF4SX1280 SPI pinout
SPI_PINOUT = {
    "bus": "SPI3",
    "sck": "PB3",
    "miso": "PB4",
    "mosi": "PB5",
    "cs": "PA15",
    "rst": "PC15",
    "busy": "PC14",
    "dio1": "PA1",
    "mcu": "STM32F411CEU6",
    "radio": "SX1280",
}

LR11XX_CVES = [
    {
        "cve": "CVE-2025-14857",
        "title": "LR11xx RCE via crafted OTA packets",
        "severity": "CRITICAL",
        "mechanism": "Malformed radio packets trigger code execution in LR11xx firmware",
        "sx1280_analogue": (
            "NOT DIRECTLY APPLICABLE — SX1280 uses mask ROM, not updatable "
            "firmware. The SX1280's packet processing is hardware-fixed. However, "
            "the FC-side ELRS stack that processes SX1280's output IS vulnerable "
            "to crafted packet content (see CRSF OOB, MSP injection)."
        ),
    },
    {
        "cve": "CVE-2025-14858",
        "title": "LR11xx memory corruption via SPI commands",
        "severity": "HIGH",
        "mechanism": "Malformed SPI commands corrupt LR11xx internal state",
        "sx1280_analogue": (
            "PARTIALLY APPLICABLE — SX1280 SPI command parsing is ROM-based "
            "and likely more robust, but unexpected register write sequences "
            "can still put the radio in undefined states. The SX1280 datasheet "
            "documents several 'reserved' register ranges where writes have "
            "undefined behavior."
        ),
    },
    {
        "cve": "CVE-2025-14859",
        "title": "LR11xx security feature bypass",
        "severity": "HIGH",
        "mechanism": "Radio security features (encryption, auth) can be bypassed",
        "sx1280_analogue": (
            "NOT APPLICABLE — SX1280 has NO security features to bypass. "
            "It provides raw modulation/demodulation only. All 'security' "
            "(CRC, FHSS, etc.) is implemented in the FC-side ELRS firmware, "
            "not in the radio hardware. This is actually WORSE — there's "
            "nothing to bypass because there's nothing there."
        ),
    },
]

ATTACK_SURFACE_ITEMS = [
    {
        "surface": "Arbitrary RF frequency",
        "command": "SetRfFrequency (0x86)",
        "impact": "CRITICAL",
        "detail": (
            "Compromised firmware can tune the SX1280 to any frequency in "
            "2400-2500 MHz. Can detune from FHSS sequence (DoS), or set to "
            "fixed frequency for reliable packet injection. Can also be used "
            "as a 2.4 GHz jammer by transmitting continuously."
        ),
    },
    {
        "surface": "TX power manipulation",
        "command": "SetTxParams (0x8E)",
        "impact": "HIGH",
        "detail": (
            "Set TX power to maximum (+12.5 dBm) continuously. Combined with "
            "continuous TX mode, effectively creates a targeted jammer. Or set "
            "power to minimum to stealthily degrade link quality."
        ),
    },
    {
        "surface": "Packet type switching",
        "command": "SetPacketType (0x8A)",
        "impact": "HIGH",
        "detail": (
            "SX1280 supports GFSK, LoRa, FLRC, and BLE packet types. A "
            "compromised FC can switch to BLE mode and use the drone's radio "
            "as a BLE beacon or scanner — the hardware supports it natively. "
            "ELRS uses FLRC (500 kbps) or LoRa; switching type causes DoS."
        ),
    },
    {
        "surface": "CRC initializer manipulation",
        "command": "WriteRegister (0x18) → 0x09C8",
        "impact": "CRITICAL",
        "detail": (
            "The CRC init register (0x09C8) controls the GFSK/FLRC CRC "
            "initializer — this IS the ELRS 'authentication'. Writing 0x0000 "
            "or a known value allows crafted packets to pass CRC. Combined "
            "with known FHSS sequence, enables reliable packet injection."
        ),
    },
    {
        "surface": "Sync word manipulation",
        "command": "WriteRegister (0x18) → 0x09C0",
        "impact": "HIGH",
        "detail": (
            "Changing the sync word causes the radio to accept packets from "
            "a different source or reject legitimate packets. Can be used to "
            "redirect the receiver to an attacker's transmitter."
        ),
    },
    {
        "surface": "IRQ suppression",
        "command": "SetDioIrqParams (0x8D)",
        "impact": "MEDIUM",
        "detail": (
            "Suppress DIO1 interrupts so the FC never knows packets arrived. "
            "Causes silent link loss — the FC doesn't trigger failsafe because "
            "it doesn't know the radio received anything. Combined with "
            "failsafe suppression (SYNC replay), this creates a persistent "
            "silent hijack state."
        ),
    },
    {
        "surface": "Continuous TX (jammer mode)",
        "command": "SetTx (0x83) with max timeout + SetTxParams max power",
        "impact": "HIGH",
        "detail": (
            "Force the SX1280 into continuous transmit mode at max power. "
            "The drone's own radio becomes a 2.4 GHz jammer, denying service "
            "to itself and all nearby ELRS/WiFi devices. Combined with "
            "frequency set to a specific WiFi channel, can target specific "
            "infrastructure."
        ),
    },
    {
        "surface": "Context save persistence",
        "command": "SetSaveContext (0xD5)",
        "impact": "MEDIUM",
        "detail": (
            "Save a malicious radio configuration so it persists across "
            "SX1280 sleep/wake cycles. Normal ELRS init should overwrite "
            "this, but if the save includes modified interrupt routing or "
            "calibration data, effects may persist."
        ),
    },
]


def analyze_architecture():
    """Compare LR11xx vs SX1280 architecture."""
    print(f"\n{'='*65}")
    print("SX1280 vs LR11xx ARCHITECTURE COMPARISON")
    print(f"{'='*65}")

    print(f"\n  SX1280 (TinyHawk III Plus):")
    print(f"    Firmware: MASK ROM (not updatable)")
    print(f"    Packet processing: Hardware-fixed")
    print(f"    SPI interface: Full register access from FC")
    print(f"    Security features: NONE (raw modulation only)")
    print(f"    Packet types: GFSK, LoRa, FLRC, BLE")

    print(f"\n  LR11xx (newer Semtech):")
    print(f"    Firmware: Flash-based (UPDATABLE)")
    print(f"    Packet processing: Firmware-defined")
    print(f"    SPI interface: Command-based with firmware abstraction")
    print(f"    Security features: AES encryption, whitening, integrity")

    print(f"\n  CVE APPLICABILITY:")
    for cve_info in LR11XX_CVES:
        print(f"\n  {cve_info['cve']}: {cve_info['title']}")
        print(f"    Severity: {cve_info['severity']}")
        print(f"    LR11xx: {cve_info['mechanism']}")
        print(f"    SX1280: {cve_info['sx1280_analogue']}")

    print(f"\n  KEY INSIGHT:")
    print(f"    The SX1280's mask ROM actually makes it MORE secure against")
    print(f"    firmware-level attacks (can't be reflashed), but the LACK of")
    print(f"    any security features means the SPI register interface is the")
    print(f"    entire attack surface — and it's wide open to any code running")
    print(f"    on the FC (malicious firmware, exploited ELRS stack, etc.).")


def analyze_registers():
    """Map security-relevant SX1280 registers."""
    print(f"\n{'='*65}")
    print("SECURITY-RELEVANT SX1280 REGISTERS")
    print(f"{'='*65}")

    print(f"\n  SPI pinout on CRAZYBEEF4SX1280:")
    for pin, val in SPI_PINOUT.items():
        print(f"    {pin}: {val}")

    print(f"\n  SPI command set ({len(SX1280_COMMANDS)} commands):")
    for name, cmd in SX1280_COMMANDS.items():
        abuse = cmd["abuse_potential"]
        marker = " ***" if "CRITICAL" in abuse or "HIGH" in abuse else ""
        print(f"    0x{cmd['opcode']:02X}  {name:25s}  [{abuse}]{marker}")

    print(f"\n  Security-relevant registers:")
    for reg in SX1280_REGISTERS:
        print(f"    0x{reg['addr']:04X}  {reg['name']:30s}  {reg['size']}B  [{reg['impact']}]")

    write_cmds = sum(1 for c in SX1280_COMMANDS.values() if c["direction"] == "write")
    crit_cmds = sum(1 for c in SX1280_COMMANDS.values()
                    if "CRITICAL" in c["abuse_potential"] or "HIGH" in c["abuse_potential"])
    print(f"\n  Total commands: {len(SX1280_COMMANDS)}")
    print(f"  Write commands: {write_cmds}")
    print(f"  HIGH/CRITICAL abuse potential: {crit_cmds}")


def analyze_attack_surface():
    """Analyze the SPI write attack surface."""
    print(f"\n{'='*65}")
    print("SX1280 SPI ATTACK SURFACE")
    print(f"{'='*65}")

    for item in ATTACK_SURFACE_ITEMS:
        print(f"\n  [{item['impact']}] {item['surface']}")
        print(f"    Command: {item['command']}")
        print(f"    {item['detail']}")

    crit = sum(1 for i in ATTACK_SURFACE_ITEMS if i["impact"] == "CRITICAL")
    high = sum(1 for i in ATTACK_SURFACE_ITEMS if i["impact"] == "HIGH")
    print(f"\n  ATTACK SURFACE SUMMARY:")
    print(f"    Total surfaces: {len(ATTACK_SURFACE_ITEMS)}")
    print(f"    CRITICAL: {crit}")
    print(f"    HIGH: {high}")
    print(f"    All require code execution on the FC (compromised firmware,")
    print(f"    exploited ELRS stack, or DFU-injected backdoor)")


def analyze_chains():
    """Analyze attack chains using SPI write primitives."""
    print(f"\n{'='*65}")
    print("ATTACK CHAINS VIA SPI WRITE PRIMITIVES")
    print(f"{'='*65}")

    chains = [
        {
            "id": "SPI-CHAIN-1",
            "name": "DFU backdoor → SPI radio manipulation",
            "severity": "CRITICAL",
            "steps": [
                "1. Flash backdoored firmware via DFU (see stm32-dfu-extract.py)",
                "2. Backdoor includes modified ELRS rx_spi driver",
                "3. On trigger (stick combo, timer, geofence):",
                "   a. WriteRegister 0x09C8 ← attacker's CRC init (accept forged packets)",
                "   b. SetRfFrequency ← fixed frequency (leave FHSS sequence)",
                "   c. SetRx ← continuous listen on attacker's frequency",
                "4. Drone now listens on attacker's channel with attacker's CRC",
                "5. Full RC override from attacker's TX",
            ],
            "impact": "Silent takeover — drone appears normal until attacker activates",
        },
        {
            "id": "SPI-CHAIN-2",
            "name": "ELRS exploit → jammer mode",
            "severity": "HIGH",
            "steps": [
                "1. Exploit ELRS vulnerability (CRSF OOB, MSP injection)",
                "2. Gain code execution on STM32F411",
                "3. SetTxParams ← max power (+12.5 dBm)",
                "4. SetTx ← continuous transmit mode",
                "5. Drone's own SX1280 becomes a 2.4 GHz jammer",
                "6. Denies service to all nearby ELRS and WiFi devices",
            ],
            "impact": "Self-jamming DoS, plus collateral WiFi interference",
        },
        {
            "id": "SPI-CHAIN-3",
            "name": "BLE beacon pivot",
            "severity": "MEDIUM",
            "steps": [
                "1. Compromised firmware on FC",
                "2. SetPacketType ← BLE (SX1280 natively supports BLE PHY)",
                "3. WriteBuffer ← BLE advertising payload",
                "4. SetTx ← transmit BLE advertisement",
                "5. Drone becomes a BLE beacon broadcasting attacker data",
                "6. Can be used for tracking, data exfiltration, or BLE attacks",
            ],
            "impact": "Radio repurposed for BLE — tracking, exfil, or pivoting",
        },
    ]

    for chain in chains:
        print(f"\n  [{chain['severity']}] {chain['id']}: {chain['name']}")
        for step in chain["steps"]:
            print(f"    {step}")
        print(f"    Impact: {chain['impact']}")

    print(f"\n  KEY FINDING:")
    print(f"    The SX1280 SPI interface provides a powerful abuse surface for")
    print(f"    post-exploitation. Once an attacker has code execution on the FC")
    print(f"    (via DFU backdoor, CRSF OOB, or MSP exploitation), the SX1280")
    print(f"    becomes a general-purpose 2.4 GHz radio under full attacker control.")
    print(f"    It can jam, beacon, retune, and transmit arbitrary packets.")
    print(f"    The mask ROM is irrelevant — the attack surface is the SPI bus.")


def main():
    parser = argparse.ArgumentParser(
        description="SX1280 SPI Write Primitive Analysis"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Compare LR11xx vs SX1280 architecture and CVE applicability",
    )
    parser.add_argument(
        "--registers", action="store_true",
        help="Map security-relevant SX1280 registers and SPI commands",
    )
    parser.add_argument(
        "--attack-surface", action="store_true",
        help="Analyze SPI write attack surface",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze attack chains via SPI write primitives",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("SX1280 SPI Write Primitive Analysis")
    print(f"Target: CRAZYBEEF4SX1280 (TinyHawk III Plus)")
    print(f"Radio: SX1280 on SPI Bus 3")
    print(f"Architecture: Mask ROM (not updatable)")
    print(f"LR11xx CVEs: Partially applicable (SPI surface, not firmware)")

    results = {
        "target": "SX1280 on CRAZYBEEF4SX1280",
        "spi_commands": len(SX1280_COMMANDS),
        "security_registers": len(SX1280_REGISTERS),
        "attack_surfaces": len(ATTACK_SURFACE_ITEMS),
    }

    if args.analyze:
        analyze_architecture()

    if args.registers:
        analyze_registers()

    if args.attack_surface:
        analyze_attack_surface()

    if args.chain:
        analyze_chains()

    if not any([args.analyze, args.registers, args.attack_surface, args.chain]):
        analyze_architecture()
        analyze_registers()
        analyze_attack_surface()
        analyze_chains()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
