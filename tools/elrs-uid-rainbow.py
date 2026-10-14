#!/usr/bin/env python3
"""
ELRS Binding Phrase → UID Rainbow Table Generator

Generates a lookup table mapping binding phrases to their 6-byte UIDs,
FHSS seeds, and CRC initializers. For CTF research and authorized
security testing only.

The ELRS binding phrase is hashed as:
    MD5('-DMY_BINDING_PHRASE="<phrase>"')[0:6] → UID[0:6]

From the UID:
    FHSS seed = (UID[2] << 24) | (UID[3] << 16) | (UID[4] << 8) | (UID[5] ^ OTA_VERSION_ID)
    CRC init  = ((UID[4] << 8) | UID[5]) ^ (OTA_VERSION_ID << 8)

Fox-IT (2022) showed that UID[3], UID[4], UID[5] are sent in plaintext
in SYNC packets, so a passive observer can match against the table
using just those 3 bytes.

Usage:
    python3 elrs-uid-rainbow.py                    # generate table with built-in wordlist
    python3 elrs-uid-rainbow.py -w wordlist.txt    # use external wordlist
    python3 elrs-uid-rainbow.py -o table.csv       # output to CSV
    python3 elrs-uid-rainbow.py --lookup AA:BB:CC   # reverse lookup UID[3:6] from capture
"""

import hashlib
import argparse
import csv
import sys
import json
from pathlib import Path


OTA_VERSION_ID = 4  # ELRS v3.x/4.x OTA version


def phrase_to_uid(phrase: str) -> bytes:
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def uid_to_fhss_seed(uid: bytes) -> int:
    return (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ OTA_VERSION_ID)


def uid_to_crc_init(uid: bytes) -> int:
    return ((uid[4] << 8) | uid[5]) ^ (OTA_VERSION_ID << 8)


def uid_hex(uid: bytes) -> str:
    return ":".join(f"{b:02X}" for b in uid)


def sync_bytes_hex(uid: bytes) -> str:
    return ":".join(f"{b:02X}" for b in uid[3:6])


# --- Built-in wordlist ---
# Sources: ELRS docs defaults, tutorial examples, FPV community patterns,
# common password patterns adapted for drone pilots
BUILTIN_PHRASES = [
    # ELRS documentation defaults and examples
    "default ExpressLRS binding phrase",
    "expresslrs",
    "ExpressLRS",
    "EXPRESSLRS",
    "elrs",
    "ELRS",

    # Common tutorial/guide examples people copy verbatim
    "MyBindingPhrase",
    "mybindingphrase",
    "my binding phrase",
    "bindphrase",
    "BindPhrase",
    "binding",
    "testphrase",
    "test",
    "test1234",
    "example",
    "changeme",

    # Vendor/product specific
    "eyantra123",
    "betafpv",
    "BetaFPV",
    "BETAFPV",
    "happymodel",
    "HappyModel",
    "radiomaster",
    "RadioMaster",
    "jumper",
    "Jumper",
    "emax",
    "EMAX",
    "tinyhawk",
    "TinyHawk",
    "mobula",
    "Mobula",
    "meteor",
    "Meteor",
    "caddx",
    "walksnail",
    "iflight",
    "geprc",
    "diatone",
    "speedybee",
    "flywoo",
    "foxeer",

    # FPV community slang and common phrases
    "iloveflying",
    "fpv4life",
    "fpvlife",
    "fpvislife",
    "sendit",
    "send it",
    "rip it",
    "ripit",
    "fullsend",
    "letsfly",
    "lets fly",
    "flyfast",
    "fly fast",
    "gofast",
    "sendfpv",
    "fpvdrone",
    "fpvracing",
    "freestyle",
    "Freestyle",
    "longrange",
    "LongRange",
    "long range",
    "tinywhoop",
    "TinyWhoop",
    "whoop",
    "whooplife",
    "acro",
    "acromode",
    "runcam",
    "analog",
    "digital",
    "hdzero",
    "HDZero",
    "dji",
    "DJI",
    "o3",
    "O3",
    "gorilla",
    "bando",
    "bandos",
    "matty",
    "juicy",
    "steele",
    "bardwell",
    "joshuabardwell",

    # Callsigns patterns (placeholder examples)
    "pilot",
    "pilot1",
    "pilot123",
    "drone",
    "drone1",
    "drone123",
    "quad",
    "quad1",
    "quad123",
    "myquad",
    "mydrone",
    "myfpv",
    "fpv",
    "fpv1",
    "fpv123",
    "rc",
    "rc1",
    "rc123",

    # Common lazy passwords adapted
    "password",
    "password1",
    "password123",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty",
    "qwerty123",
    "letmein",
    "admin",
    "admin123",
    "welcome",
    "abc123",
    "monkey",
    "master",
    "dragon",
    "login",
    "princess",
    "football",
    "shadow",
    "sunshine",
    "trustno1",
    "iloveyou",
    "batman",
    "access",
    "hello",
    "charlie",
    "donald",
    "passw0rd",
    "asdf",
    "asdfgh",
    "asdfghjkl",
    "zxcvbnm",
    "qwertyuiop",

    # Number sequences
    "0000",
    "00000000",
    "1111",
    "11111111",
    "1234",
    "4321",
    "0123456789",
    "9876543210",
    "696969",
    "420420",
    "1337",

    # Empty / minimal
    "",
    " ",
    "a",
    "aa",
    "aaa",
    "aaaa",
    "aaaaaaaa",

    # Known CTF/hacker culture
    "hacktheplanet",
    "pwned",
    "r00t",
    "root",
    "toor",
    "deadbeef",
    "cafebabe",
    "31337",
    "h4x0r",

    # Drone model names people might use
    "tinyhawk3",
    "tinyhawkiii",
    "tinyhawk III",
    "babyhawk",
    "mobula6",
    "mobula7",
    "meteor65",
    "meteor75",
    "crazybee",
    "beta65",
    "beta75",
    "ur65",
    "us65",
    "moblite6",
    "moblite7",
    "nazgul",
    "chimera",
    "mark4",
    "source one",
    "sourceone",

    # Frequency/channel references
    "2.4ghz",
    "2.4GHz",
    "900mhz",
    "900MHz",
    "5.8ghz",
    "5.8GHz",
]


def load_wordlist(path: str) -> list[str]:
    phrases = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            phrase = line.rstrip("\n\r")
            phrases.append(phrase)
    return phrases


def generate_table(phrases: list[str]) -> list[dict]:
    table = []
    seen_uids = set()
    for phrase in phrases:
        uid = phrase_to_uid(phrase)
        uid_key = uid.hex()
        entry = {
            "phrase": phrase,
            "uid_hex": uid_hex(uid),
            "uid_bytes": list(uid),
            "sync_bytes": sync_bytes_hex(uid),
            "fhss_seed": uid_to_fhss_seed(uid),
            "crc_init": uid_to_crc_init(uid),
            "collision": uid_key in seen_uids,
        }
        seen_uids.add(uid_key)
        table.append(entry)
    return table


def lookup_sync(table: list[dict], sync_hex: str) -> list[dict]:
    target = sync_hex.upper().replace("-", ":").replace(" ", ":")
    return [e for e in table if e["sync_bytes"] == target]


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Binding Phrase → UID Rainbow Table Generator"
    )
    parser.add_argument(
        "-w", "--wordlist",
        help="External wordlist file (one phrase per line)",
    )
    parser.add_argument(
        "-a", "--append-builtin",
        action="store_true",
        help="Append built-in phrases to external wordlist",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output CSV file path",
    )
    parser.add_argument(
        "-j", "--json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--lookup",
        help="Reverse lookup: provide UID[3]:UID[4]:UID[5] from captured SYNC packet (hex, colon-separated)",
    )
    parser.add_argument(
        "--phrase",
        help="Compute UID for a single phrase",
    )
    args = parser.parse_args()

    if args.phrase:
        uid = phrase_to_uid(args.phrase)
        print(f"Phrase:     {args.phrase}")
        print(f"UID:        {uid_hex(uid)}")
        print(f"UID bytes:  {list(uid)}")
        print(f"SYNC bytes: {sync_bytes_hex(uid)}  (UID[3:6], visible in SYNC packets)")
        print(f"FHSS seed:  0x{uid_to_fhss_seed(uid):08X}")
        print(f"CRC init:   0x{uid_to_crc_init(uid):04X}")
        return

    # Build phrase list
    phrases = []
    if args.wordlist:
        phrases = load_wordlist(args.wordlist)
        print(f"Loaded {len(phrases)} phrases from {args.wordlist}", file=sys.stderr)
        if args.append_builtin:
            phrases.extend(BUILTIN_PHRASES)
            print(f"Appended {len(BUILTIN_PHRASES)} built-in phrases", file=sys.stderr)
    else:
        phrases = BUILTIN_PHRASES
        print(f"Using {len(BUILTIN_PHRASES)} built-in phrases", file=sys.stderr)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    phrases = unique
    print(f"Unique phrases: {len(phrases)}", file=sys.stderr)

    # Generate table
    table = generate_table(phrases)

    # Lookup mode
    if args.lookup:
        matches = lookup_sync(table, args.lookup)
        if matches:
            print(f"\nMatches for SYNC bytes {args.lookup}:\n")
            for m in matches:
                print(f"  Phrase:    \"{m['phrase']}\"")
                print(f"  Full UID:  {m['uid_hex']}")
                print(f"  FHSS seed: 0x{m['fhss_seed']:08X}")
                print(f"  CRC init:  0x{m['crc_init']:04X}")
                print()
        else:
            print(f"\nNo matches for SYNC bytes {args.lookup}")
            print("Try expanding the wordlist with -w <file>")
        return

    # Output CSV
    if args.output:
        with open(args.output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["phrase", "uid", "sync_bytes", "fhss_seed", "crc_init"])
            for e in table:
                writer.writerow([
                    e["phrase"],
                    e["uid_hex"],
                    e["sync_bytes"],
                    f"0x{e['fhss_seed']:08X}",
                    f"0x{e['crc_init']:04X}",
                ])
        print(f"Wrote {len(table)} entries to {args.output}", file=sys.stderr)

    # Output JSON
    if args.json:
        with open(args.json, "w") as f:
            json.dump(table, f, indent=2)
        print(f"Wrote {len(table)} entries to {args.json}", file=sys.stderr)

    # Default: print to stdout
    if not args.output and not args.json:
        print(f"\n{'PHRASE':<45} {'UID':<20} {'SYNC(3:6)':<12} {'FHSS SEED':<12} {'CRC INIT'}")
        print("-" * 100)
        for e in table:
            phrase_display = e["phrase"] if len(e["phrase"]) <= 42 else e["phrase"][:39] + "..."
            print(
                f"{phrase_display:<45} "
                f"{e['uid_hex']:<20} "
                f"{e['sync_bytes']:<12} "
                f"0x{e['fhss_seed']:08X}   "
                f"0x{e['crc_init']:04X}"
            )
        print(f"\nTotal entries: {len(table)}")


if __name__ == "__main__":
    main()
