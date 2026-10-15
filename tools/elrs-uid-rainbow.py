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

OTA_VERSION_ID depends on the firmware build:
    - ELRS 4.x / Betaflight with USE_ELRSV4: OTA_VERSION_ID = 4 (default)
    - ELRS 3.x / Betaflight without USE_ELRSV4: OTA_VERSION_ID = 3
    The FHSS seed and CRC init differ between versions for the same UID.

SYNC packet UID exposure by version:
    - ELRS 4.x OTA_Sync_s struct has explicit UID4, UID5 fields → 2 bytes
      in the clear per SYNC packet (confirmed in OTA.h:29-40).
    - Fox-IT (2022) described 3 bytes (UID[3:5]) visible in SYNC packets,
      based on earlier ELRS versions. Some older firmware may expose 3 bytes.
    - The --lookup flag accepts either format: 2 bytes (AA:BB) matches
      UID[4]:UID[5]; 3 bytes (AA:BB:CC) matches UID[3]:UID[4]:UID[5].
    - The table always stores all 3 sync bytes for maximum coverage.

Usage:
    python3 elrs-uid-rainbow.py                       # generate table (OTA v4 default)
    python3 elrs-uid-rainbow.py --ota-version 3       # generate table for OTA v3 targets
    python3 elrs-uid-rainbow.py -w wordlist.txt       # use external wordlist
    python3 elrs-uid-rainbow.py -o table.csv          # output to CSV
    python3 elrs-uid-rainbow.py --lookup AA:BB        # reverse lookup UID[4:5] from SYNC capture
    python3 elrs-uid-rainbow.py --lookup AA:BB:CC     # reverse lookup UID[3:5] (legacy 3-byte)
"""

import hashlib
import argparse
import csv
import sys
import json
from pathlib import Path


DEFAULT_OTA_VERSION = 4


def phrase_to_uid(phrase: str) -> bytes:
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def uid_to_fhss_seed(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> int:
    return (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ ota_ver)


def uid_to_crc_init(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> int:
    return ((uid[4] << 8) | uid[5]) ^ (ota_ver << 8)


def uid_hex(uid: bytes) -> str:
    return ":".join(f"{b:02X}" for b in uid)


def sync_bytes_hex(uid: bytes) -> str:
    return ":".join(f"{b:02X}" for b in uid[3:6])


# --- Built-in wordlist ---
# Sources: ELRS docs defaults, tutorial/guide examples, FPV forum posts
# (RCGroups, IntoFPV, Oscar Liang, Joshua Bardwell), GitHub ELRS configs
# and issues, manufacturer defaults (HappyModel, BetaFPV, EMAX, iFlight,
# Radiomaster, Jumper, GEPRC, Flywoo, Diatone, SpeedyBee, Foxeer, Matek,
# TBS), common user patterns, hacker/CTF culture, and password lists.
BUILTIN_PHRASES = [
    # ===== ELRS documentation defaults and examples =====
    "default ExpressLRS binding phrase",
    "expresslrs",
    "ExpressLRS",
    "EXPRESSLRS",
    "elrs",
    "ELRS",
    "express lrs",
    "Express LRS",
    "expressLRS",

    # ===== Tutorial/guide examples people copy verbatim =====
    "MyBindingPhrase",
    "mybindingphrase",
    "my binding phrase",
    "My Binding Phrase",
    "bindphrase",
    "BindPhrase",
    "bind phrase",
    "binding",
    "binding phrase",
    "bindingphrase",
    "BindingPhrase",
    "testphrase",
    "test",
    "test1234",
    "test123",
    "example",
    "changeme",
    "change me",
    "enter binding phrase",
    "your binding phrase",
    "put your binding phrase here",
    "mypassword",
    "mysecret",
    "default",
    "demo",
    "setup",

    # ===== e-Yantra / educational defaults =====
    "eyantra123",
    "eyantra",
    "e-yantra",

    # ===== Manufacturer / vendor names =====
    # BetaFPV
    "betafpv",
    "BetaFPV",
    "BETAFPV",
    "betafpv123",
    "betafpvelrs",
    "beta fpv",
    # HappyModel
    "happymodel",
    "HappyModel",
    "HAPPYMODEL",
    "happymodel123",
    "happymodelelrs",
    "happy model",
    # Radiomaster
    "radiomaster",
    "RadioMaster",
    "RADIOMASTER",
    "radiomaster123",
    "radio master",
    "radiomasterelrs",
    "zorro",
    "Zorro",
    "pocket",
    "Pocket",
    "tx16s",
    "TX16S",
    "tx12",
    "TX12",
    "boxer",
    "Boxer",
    "mt12",
    "MT12",
    "ranger",
    "Ranger",
    # Jumper
    "jumper",
    "Jumper",
    "JUMPER",
    "jumpert-pro",
    "t-pro",
    "T-Pro",
    "tlite",
    "T-Lite",
    "jumperelrs",
    # EMAX
    "emax",
    "EMAX",
    "emax123",
    "emaxelrs",
    # iFlight
    "iflight",
    "iFlight",
    "IFLIGHT",
    "iflight123",
    "iflightelrs",
    # GEPRC
    "geprc",
    "GEPRC",
    "geprc123",
    "geprcelrs",
    # Diatone
    "diatone",
    "Diatone",
    "DIATONE",
    "diatone123",
    # SpeedyBee
    "speedybee",
    "SpeedyBee",
    "SPEEDYBEE",
    "speedybee123",
    "speedybeef405",
    # Flywoo
    "flywoo",
    "Flywoo",
    "FLYWOO",
    "flywoo123",
    # Foxeer
    "foxeer",
    "Foxeer",
    "FOXEER",
    "foxeer123",
    # Matek
    "matek",
    "Matek",
    "MATEK",
    "matek123",
    # TBS / Crossfire (people switching to ELRS keep old habits)
    "tbs",
    "TBS",
    "crossfire",
    "Crossfire",
    "CROSSFIRE",
    "teamblacksheep",
    # Caddx / Walksnail / DJI (video system names used as phrases)
    "caddx",
    "Caddx",
    "walksnail",
    "Walksnail",
    "WALKSNAIL",
    "walksnailavatar",
    "avatar",
    # Other manufacturers
    "axisflying",
    "darwinfpv",
    "DarwinFPV",
    "namimnorc",
    "NamimnoRC",
    "siyi",
    "hglrc",
    "HGLRC",
    "rushfpv",
    "RushFPV",
    "frsky",
    "FrSky",
    "FRSKY",

    # ===== Flight controller firmware / software =====
    "betaflight",
    "Betaflight",
    "BETAFLIGHT",
    "inav",
    "INAV",
    "iNav",
    "ardupilot",
    "ArduPilot",
    "edgetx",
    "EdgeTX",
    "opentx",
    "OpenTX",
    "rotorflight",
    "emuflight",

    # ===== FPV community slang and common phrases =====
    "iloveflying",
    "i love flying",
    "ilovefpv",
    "i love fpv",
    "fpv4life",
    "fpvlife",
    "fpvislife",
    "fpv is life",
    "sendit",
    "send it",
    "rip it",
    "ripit",
    "fullsend",
    "full send",
    "letsfly",
    "lets fly",
    "let's fly",
    "letsgo",
    "lets go",
    "flyfast",
    "fly fast",
    "gofast",
    "go fast",
    "sendfpv",
    "fpvdrone",
    "fpvracing",
    "fpvfreestyle",
    "fpvpilot",
    "freestyle",
    "Freestyle",
    "FREESTYLE",
    "longrange",
    "LongRange",
    "long range",
    "Long Range",
    "tinywhoop",
    "TinyWhoop",
    "tiny whoop",
    "whoop",
    "whooplife",
    "whoop life",
    "whoopgang",
    "acro",
    "acromode",
    "acro mode",
    "runcam",
    "RunCam",
    "analog",
    "digital",
    "hdzero",
    "HDZero",
    "HDZERO",
    "dji",
    "DJI",
    "djifpv",
    "o3",
    "O3",
    "gorilla",
    "bando",
    "bandos",
    "bando life",
    "matty",
    "juicy",
    "steele",
    "bardwell",
    "joshuabardwell",
    "joshua bardwell",
    "oscarliang",
    "oscar liang",
    "rotorriot",
    "rotor riot",
    "ledrib",
    "le drib",
    "mrsteele",
    "mr steele",
    "skitzo",
    "botgrinder",
    "bot grinder",
    "ummagawd",
    "vanover",
    "nurk",
    "kabab",
    "painless360",
    "andyrc",
    "nick burns",
    "project blue falcon",
    "kwad",
    "kwadbanger",
    "rippah",
    "flynoceros",
    "flyingbear",
    "droneracingleague",
    "drl",
    "DRL",
    "multigp",
    "MultiGP",
    "fai",

    # ===== Piloting terms and flight modes =====
    "throttle",
    "yaw",
    "pitch",
    "roll",
    "arm",
    "disarm",
    "airmode",
    "air mode",
    "angle",
    "horizon",
    "failsafe",
    "rssi",
    "snr",
    "lq",
    "linkquality",
    "headfree",
    "gps rescue",
    "gpsrescue",
    "turtle",
    "turtlemode",
    "flip over after crash",
    "rates",
    "pids",
    "vtx",
    "osd",

    # ===== Callsign / pilot name patterns =====
    "pilot",
    "pilot1",
    "pilot123",
    "pilotfpv",
    "drone",
    "drone1",
    "drone123",
    "dronepilot",
    "quad",
    "quad1",
    "quad123",
    "myquad",
    "mydrone",
    "myfpv",
    "myelrs",
    "fpv",
    "fpv1",
    "fpv123",
    "rc",
    "rc1",
    "rc123",
    "flyboy",
    "flyguy",
    "flygirl",
    "skyking",
    "skypilot",
    "droneking",
    "flyingace",
    "maverick",
    "goose",
    "iceman",
    "topgun",
    "top gun",
    "viper",
    "ghost",
    "phantom",
    "raptor",
    "eagle",
    "falcon",
    "hawk",
    "osprey",
    "raven",
    "sparrow",
    "phoenix",
    "thunderbird",
    "mustang",
    "bandit",
    "rebel",
    "ace",
    "bravo",
    "alpha",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
    "sierra",
    "tango",
    "uniform",
    "victor",
    "whiskey",
    "xray",
    "yankee",
    "zulu",

    # ===== Drone model names =====
    # TinyHawk variants
    "tinyhawk3",
    "tinyhawkiii",
    "tinyhawk III",
    "tinyhawk2",
    "tinyhawkii",
    "tinyhawk s",
    "tinyhawks",
    "tinyhawkfreestyle",
    # BabyHawk
    "babyhawk",
    "babyhawk2",
    "babyhawkii",
    # Mobula variants
    "mobula6",
    "mobula7",
    "mobula6elrs",
    "mobula7elrs",
    "mobula6hd",
    "mobula7hd",
    # Meteor variants
    "meteor65",
    "meteor75",
    "meteor65elrs",
    "meteor75elrs",
    "meteor65pro",
    "meteor75pro",
    # CrazyBee
    "crazybee",
    "crazybeef4",
    "crazybeesx1280",
    # BetaFPV models
    "beta65",
    "beta75",
    "beta65pro",
    "beta75pro",
    "beta85",
    "beta95",
    "cetus",
    "cetusx",
    "cetuslite",
    "cetuspro",
    "aquila16",
    "pavo",
    "pavo25",
    "pavo pico",
    "pavopico",
    # UR65/US65
    "ur65",
    "us65",
    # MobLite
    "moblite6",
    "moblite7",
    # Larger quads / frames
    "nazgul",
    "nazgul5",
    "nazgulv2",
    "nazgulevoque",
    "chimera",
    "chimera7",
    "mark4",
    "mark5",
    "source one",
    "sourceone",
    "source two",
    "sourcetwo",
    "cidora",
    "titan",
    "xl5",
    "xl7",
    "xl10",
    "rekon",
    "rekon3",
    "rekon4",
    "rekon5",
    "bumblebee",
    "explorer",
    "cinelog",
    "cinelog25",
    "cinelog30",
    "cinelog35",
    "cinebot",
    "cinebot25",
    "cinebot30",
    "cinerace",
    "protek",
    "protek25",
    "protek35",
    "crux3",
    "crux35",
    "firefly",

    # ===== Common lazy passwords =====
    "password",
    "password1",
    "password123",
    "password1234",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty",
    "qwerty123",
    "letmein",
    "admin",
    "admin123",
    "welcome",
    "welcome1",
    "abc123",
    "abc1234",
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
    "hello123",
    "charlie",
    "donald",
    "passw0rd",
    "p@ssw0rd",
    "p@ssword",
    "asdf",
    "asdfgh",
    "asdfghjkl",
    "zxcvbnm",
    "qwertyuiop",
    "whatever",
    "nothing",
    "secret",
    "secret123",
    "letmein123",
    "baseball",
    "soccer",
    "hockey",
    "hunter",
    "hunter2",
    "killer",
    "pepper",
    "george",
    "robert",
    "thomas",
    "michael",
    "jordan",
    "mustang1",
    "ginger",
    "buster",
    "tigger",
    "jessica",
    "joshua",
    "andrew",
    "matrix",
    "starwars",
    "star wars",

    # ===== Number sequences =====
    "0000",
    "00000000",
    "1111",
    "11111111",
    "1234",
    "4321",
    "12345",
    "123456",
    "1234567",
    "0123456789",
    "9876543210",
    "696969",
    "420420",
    "420",
    "1337",
    "7777",
    "8888",
    "9999",
    "1212",
    "2468",
    "1357",
    "54321",
    "111111",
    "222222",
    "333333",
    "444444",
    "555555",
    "666666",
    "777777",
    "888888",
    "999999",
    "000000",

    # ===== Empty / minimal =====
    "",
    " ",
    "a",
    "aa",
    "aaa",
    "aaaa",
    "aaaaaaaa",
    "x",
    "xx",
    "xxx",
    "z",
    "zz",
    "zzz",

    # ===== Hacker / CTF culture =====
    "hacktheplanet",
    "hack the planet",
    "pwned",
    "r00t",
    "root",
    "toor",
    "deadbeef",
    "cafebabe",
    "31337",
    "h4x0r",
    "hacker",
    "hax",
    "leet",
    "1337h4x0r",
    "ctf",
    "CTF",
    "pwnage",
    "exploit",
    "shellcode",
    "overflow",
    "0day",
    "zeroday",
    "pentest",
    "kali",
    "metasploit",

    # ===== Frequency / channel / protocol references =====
    "2.4ghz",
    "2.4GHz",
    "900mhz",
    "900MHz",
    "5.8ghz",
    "5.8GHz",
    "868mhz",
    "868MHz",
    "915mhz",
    "915MHz",
    "ism",
    "ISM",
    "lora",
    "LoRa",
    "LORA",
    "sx1280",
    "SX1280",
    "sx1276",
    "SX1276",
    "esp32",
    "ESP32",
    "esp8266",
    "ESP8266",
    "stm32",
    "STM32",
    "stm32f4",
    "nrf24",

    # ===== ELRS-specific technical terms =====
    "crsf",
    "CRSF",
    "crossfire protocol",
    "fhss",
    "FHSS",
    "50hz",
    "150hz",
    "250hz",
    "333hz",
    "500hz",
    "1000hz",
    "f500",
    "f1000",
    "d250",
    "d500",
    "flrc",
    "FLRC",
    "switch8",
    "hybrid8",
    "wide8",
    "fullres",

    # ===== Common WiFi passwords (ELRS uses WiFi for config) =====
    "expresslrs",
    "wifipassword",
    "wifi password",
    "wifipass",
    "12345678",

    # ===== Pop culture / memes =====
    "yolo",
    "swag",
    "thug",
    "doge",
    "hodl",
    "stonks",
    "gg",
    "noob",
    "n00b",
    "bruh",
    "sus",
    "deez",
    "ligma",
    "rekt",
    "based",
    "chad",
    "sigma",
    "poggers",
    "pepe",
    "kek",
    "rickroll",
    "nevergunnagiveyouup",

    # ===== Animals (common password themes) =====
    "falcon1",
    "eagle1",
    "dragon1",
    "wolf",
    "tiger",
    "lion",
    "bear",
    "shark",
    "snake",
    "spider",

    # ===== Seasons / dates people use =====
    "summer",
    "winter",
    "spring",
    "fall",
    "summer2024",
    "summer2025",
    "winter2024",
    "winter2025",
    "2024",
    "2025",
    "2026",
    "2023",
    "2022",

    # ===== Ham radio / aviation crossover =====
    "cq",
    "cqdx",
    "73",
    "ham",
    "hamradio",
    "adsb",
    "metar",
    "notam",
    "atis",
    "vfr",
    "ifr",
    "clearance",
    "cleared for takeoff",

    # ===== Gaming references (FPV sim crossover) =====
    "velocidrone",
    "liftoff",
    "Liftoff",
    "drl sim",
    "drlsim",
    "tryp",
    "uncrashed",
    "orqa",
    "ORQA",

    # ===== Additional brand + ELRS combos =====
    "emaxelrs",
    "emaxelrs123",
    "betafpvelrs123",
    "happymodelelrs123",
    "radiomasterelrs123",
    "jumperelrs123",
    "geprcelrs123",
    "iflightelrs123",
    "matekr24",

    # ===== Common forum-posted phrases and typos =====
    "elrs bind",
    "elrsbind",
    "my elrs",
    "myelrs",
    "firstflight",
    "first flight",
    "maidenflght",
    "maiden",
    "maiden flight",
    "letsgoflying",
    "readytofly",
    "ready to fly",
    "rtf",
    "bnf",
    "pnp",
    "arfquad",
    "buildandfly",

    # ===== Simple word + number combos =====
    "drone1234",
    "fpv1234",
    "elrs1234",
    "quad1234",
    "pilot1234",
    "fly1234",
    "race1234",
    "drone2024",
    "drone2025",
    "fpv2024",
    "fpv2025",
    "elrs2024",
    "elrs2025",
]


def load_wordlist(path: str) -> list[str]:
    phrases = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            phrase = line.rstrip("\n\r")
            phrases.append(phrase)
    return phrases


def generate_table(phrases: list[str], ota_ver: int = DEFAULT_OTA_VERSION) -> list[dict]:
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
            "fhss_seed": uid_to_fhss_seed(uid, ota_ver),
            "crc_init": uid_to_crc_init(uid, ota_ver),
            "collision": uid_key in seen_uids,
        }
        seen_uids.add(uid_key)
        table.append(entry)
    return table


def lookup_sync(table: list[dict], sync_hex: str) -> list[dict]:
    target = sync_hex.upper().replace("-", ":").replace(" ", ":")
    parts = target.split(":")
    if len(parts) == 2:
        return [e for e in table if e["sync_bytes"].endswith(target)]
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
        help="Reverse lookup from captured SYNC packet (hex, colon-separated). "
             "Accepts 2 bytes (UID[4]:UID[5], ELRS 4.x SYNC struct) or "
             "3 bytes (UID[3]:UID[4]:UID[5], legacy/Fox-IT convention)",
    )
    parser.add_argument(
        "--phrase",
        help="Compute UID for a single phrase",
    )
    parser.add_argument(
        "--ota-version", type=int, default=DEFAULT_OTA_VERSION, choices=[3, 4],
        help="OTA version ID (default: %(default)s). "
             "ELRS 4.x / Betaflight+USE_ELRSV4 = 4; ELRS 3.x = 3. "
             "Affects FHSS seed and CRC init calculations.",
    )
    args = parser.parse_args()

    ota_ver = args.ota_version

    if args.phrase:
        uid = phrase_to_uid(args.phrase)
        print(f"Phrase:      {args.phrase}")
        print(f"OTA version: {ota_ver}")
        print(f"UID:         {uid_hex(uid)}")
        print(f"UID bytes:   {list(uid)}")
        print(f"SYNC bytes:  {sync_bytes_hex(uid)}  (UID[3:5], visible in SYNC packets)")
        print(f"FHSS seed:   0x{uid_to_fhss_seed(uid, ota_ver):08X}")
        print(f"CRC init:    0x{uid_to_crc_init(uid, ota_ver):04X}")
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
    print(f"OTA version: {ota_ver}", file=sys.stderr)

    # Generate table
    table = generate_table(phrases, ota_ver)

    # Lookup mode
    if args.lookup:
        matches = lookup_sync(table, args.lookup)
        if matches:
            print(f"\nMatches for SYNC bytes {args.lookup} (OTA version {ota_ver}):\n")
            for m in matches:
                print(f"  Phrase:    \"{m['phrase']}\"")
                print(f"  Full UID:  {m['uid_hex']}")
                print(f"  FHSS seed: 0x{m['fhss_seed']:08X}")
                print(f"  CRC init:  0x{m['crc_init']:04X}")
                print()
        else:
            print(f"\nNo matches for SYNC bytes {args.lookup} (OTA version {ota_ver})")
            print("Try expanding the wordlist with -w <file>")
            if ota_ver == DEFAULT_OTA_VERSION:
                print(f"Also try --ota-version 3 if the target runs ELRS 3.x")
        return

    # Output CSV
    if args.output:
        with open(args.output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["phrase", "uid", "sync_bytes", "fhss_seed", "crc_init", "ota_version"])
            for e in table:
                writer.writerow([
                    e["phrase"],
                    e["uid_hex"],
                    e["sync_bytes"],
                    f"0x{e['fhss_seed']:08X}",
                    f"0x{e['crc_init']:04X}",
                    ota_ver,
                ])
        print(f"Wrote {len(table)} entries to {args.output}", file=sys.stderr)

    # Output JSON
    if args.json:
        output = {"ota_version": ota_ver, "entries": table}
        with open(args.json, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Wrote {len(table)} entries to {args.json}", file=sys.stderr)

    # Default: print to stdout
    if not args.output and not args.json:
        print(f"\nOTA version: {ota_ver}")
        print(f"\n{'PHRASE':<45} {'UID':<20} {'SYNC(3:5)':<12} {'FHSS SEED':<12} {'CRC INIT'}")
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
