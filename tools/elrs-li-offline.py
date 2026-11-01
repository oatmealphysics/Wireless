#!/usr/bin/env python3
"""Read-only SX1280 SF7, 4/8-LI decoder for ranked ExpressLRS symbol JSONL.

Input is produced by ``elrs-phy-scan.py`` or ``elrs-batch-evidence.py``; raw
IQ is never modified. For the two published captures, for example::

  python tools/elrs-li-offline.py captures/*ranked*.jsonl \
      captures/*full-phy*.jsonl --jsonl-out decoded.jsonl

The default JSONL omits raw packet bytes and UID. ``--include-bytes`` is an
explicit opt-in for private analysis; do not share that output unless its
pairing-sensitive contents have been reviewed and deliberately approved. A
decoded frame is reported as verified only after the source-matched Betaflight
v3 CRC check.
The SYNC initializer is self-derived; RC verification transfers its UID suffix
from *different* SYNC frames. Neither proves a full six-byte UID or live
acceptance of a crafted packet.

Algorithm and whitening sequence adapted from v0l/WaveShark's empirically
measured SX128x LI implementation, commit
62b513a7b699fd3a6928908102902abf9d9b2650, GPL-3.0-or-later:
https://github.com/v0l/waveshark/blob/62b513a7b699fd3a6928908102902abf9d9b2650/crates/decode/src/lora_li.rs
SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from collections import Counter
from pathlib import Path

CHECKER = runpy.run_path(str(Path(__file__).with_name("elrs-v3-frame-check.py")))
check_frame = CHECKER["check_frame"]
read_uid = CHECKER["read_uid"]

SF = 7
PAYLOAD_BYTES = 8
SYMBOLS = 21
WHITENING = bytes.fromhex("fffefcf8f0e1c2850b172f5ebc")
OFFSETS = (0, 1)  # adjacent FFT-bin uncertainty, not a fitted bit permutation


def parity(data: list[int]) -> tuple[int, int, int, int]:
    a, b, c, d = data
    return a ^ b ^ c, b ^ c ^ d, a ^ b ^ d, a ^ c ^ d


def symbol_bits(symbols: list[int], payload_bytes: int = PAYLOAD_BYTES) -> list[int]:
    """Undo the measured reduced-width opening and Gray/bin mapping."""
    needed = 8 + max(0, (16 * payload_bytes - 40 + 6) // 7)
    if len(symbols) < needed:
        raise ValueError(f"need {needed} symbols for {payload_bytes} bytes")
    bits: list[int] = []
    for index, symbol in enumerate(symbols[:needed]):
        if not isinstance(symbol, int) or not 0 <= symbol < 128:
            raise ValueError("symbol bins must be integers in 0..127")
        width = 5 if index < 8 else 7
        deoffset = ((symbol - 1) & 127) >> (SF - width)
        gray = deoffset ^ (deoffset >> 1)
        bits.extend((gray >> bit) & 1 for bit in range(width))
    return bits


def decode_symbols(symbols: list[int], payload_bytes: int = PAYLOAD_BYTES
                   ) -> tuple[bytes, int] | None:
    """Return (packet bytes, corrected nibbles), or None for uncorrectable FEC."""
    bits = symbol_bits(symbols, payload_bytes)
    nibbles = 2 * payload_bytes
    output = bytearray(payload_bytes)
    corrected = 0
    for nibble_index in range(nibbles):
        data = [bits[nibble_index + nibbles * bit] for bit in range(4)]
        check = [bits[4 * nibbles + nibble_index + nibbles * bit]
                 for bit in range(4)]
        if parity(data) != tuple(check):
            repaired = False
            for bit in range(4):
                trial = data.copy()
                trial[bit] ^= 1
                if parity(trial) == tuple(check):
                    data = trial
                    repaired = True
                    break
            if not repaired and sum(a != b for a, b in zip(parity(data), check)) == 1:
                repaired = True  # one parity bit, no data-bit change
            if not repaired:
                return None
            corrected += 1
        value = sum(bit << index for index, bit in enumerate(data))
        output[nibble_index // 2] |= value << (4 * (nibble_index % 2))
    for index in range(payload_bytes):
        output[index] ^= WHITENING[index % len(WHITENING)]
    return bytes(output), corrected


def rc_channels(packet: bytes) -> list[int]:
    """Four Betaflight 4.4.2 OTA analog fields (988-based, not MSP order)."""
    packed = int.from_bytes(packet[1:6], "little")
    return [988 + ((packed >> (10 * channel)) & 1023) for channel in range(4)]


def read_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                payload = row.get("payload_symbols")
                if not isinstance(payload, list) or len(payload) < SYMBOLS:
                    raise ValueError(f"{path}:{line_number}: missing 21 payload symbols")
                bins = [item["relative_bin"] for item in payload[:SYMBOLS]]
                if any(not isinstance(value, int) or not 0 <= value < 128
                       for value in bins):
                    raise ValueError(f"{path}:{line_number}: invalid relative bin")
                rows.append({"source": path.name, "line": line_number,
                             "preamble_start_seconds": row.get("preamble_start_seconds"),
                             "bins": bins})
    return rows


def analyze(rows: list[dict], uid: bytes | None = None,
            include_bytes: bool = False,
            switch_mode: str | None = None) -> tuple[list[dict], dict]:
    """Decode with fixed published map; use cross-frame UID suffix for RC CRC."""
    candidates: list[list[tuple[int, bytes, int]]] = []
    suffixes: Counter[tuple[int, int]] = Counter()
    modes: Counter[str] = Counter()
    for row in rows:
        possibilities = []
        for offset in OFFSETS:
            bins = [(value + offset) & 127 for value in row["bins"]]
            decoded = decode_symbols(bins)
            if decoded is None:
                continue
            packet, corrected = decoded
            possibilities.append((offset, packet, corrected))
            preliminary = check_frame(packet)
            if preliminary["type"] == "SYNC" and preliminary["crc"] == "pass":
                suffixes[(packet[5], packet[6])] += 1
                modes[preliminary["fields"]["switch_mode"]] += 1
        candidates.append(possibilities)

    if len(suffixes) > 1:
        raise ValueError("CRC-valid SYNC frames disagree on UID suffix")
    if len(modes) > 1:
        raise ValueError("CRC-valid SYNC frames disagree on switch mode")
    consensus = suffixes.most_common(1)[0][0] if suffixes else None
    observed_mode = modes.most_common(1)[0][0] if modes else None
    if switch_mode is not None and observed_mode is not None and switch_mode != observed_mode:
        raise ValueError("requested switch mode conflicts with decoded SYNC frames")
    active_mode = observed_mode or switch_mode
    if active_mode == "wide":
        raise ValueError("wide RC CRC needs an independently tracked nonce; unsupported")
    if uid is not None:
        if len(uid) != 6:
            raise ValueError("UID must be six bytes")
        if consensus is not None and tuple(uid[4:6]) != consensus:
            raise ValueError("provided UID suffix conflicts with decoded SYNC frames")
        suffix = tuple(uid[4:6])
        basis = "provided_uid"
    elif consensus is not None:
        suffix = consensus
        basis = ("cross_frame_sync_suffix" if sum(suffixes.values()) >= 2
                 else "single_sync_suffix")
    else:
        suffix = None
        basis = "unavailable"
    verifier_uid = uid if uid is not None else (
        bytes((0, 0, 0, 0, *suffix)) if suffix is not None else None)

    results: list[dict] = []
    counts: Counter[str] = Counter()
    for row, possibilities in zip(rows, candidates):
        accepted = []
        for offset, packet, corrected in possibilities:
            kind = packet[0] & 3
            if kind == 2:
                verdict = check_frame(packet, uid=verifier_uid)
                if verdict["crc"] != "pass":
                    continue
                # A SYNC CRC authenticates only the UID-derived CRC suffix.
                # When a full UID was supplied independently, require the
                # source-defined 18-bit identity/model fragment as well.
                if uid is not None and not verdict.get("uid_18bit_match", False):
                    continue
                if consensus is not None and (packet[5], packet[6]) != consensus:
                    continue
                mode = "self_derived_sync" if uid is None else basis
            elif verifier_uid is not None:
                verdict = check_frame(packet, uid=verifier_uid,
                                      switch_mode=active_mode)
                if verdict["crc"] != "pass":
                    continue
                mode = basis
            else:
                continue
            accepted.append((corrected, offset, packet, verdict, mode))
        result = {"source": row["source"], "line": row["line"],
                  "preamble_start_seconds": row["preamble_start_seconds"]}
        if not accepted:
            result["status"] = "unresolved"
            counts["unresolved"] += 1
        else:
            accepted.sort(key=lambda item: (item[0], item[1]))
            corrected, offset, packet, verdict, mode = accepted[0]
            result.update({"status": "crc_valid", "type": verdict["type"],
                           "crc_basis": mode, "bin_offset": offset,
                           "corrected_nibbles": corrected})
            if verdict["type"] == "RC":
                result["ota_analog_channels"] = rc_channels(packet)
            elif verdict["type"] == "SYNC":
                result["sync"] = verdict["fields"]
            if include_bytes:
                result["packet_bytes_hex"] = packet.hex()
            counts[verdict["type"]] += 1
        results.append(result)
    summary = {"total": len(rows), "crc_valid": len(rows) - counts["unresolved"],
               "unresolved": counts["unresolved"],
               "types": {kind: counts[kind] for kind in ("RC", "MSP", "SYNC", "TLM")},
               "sync_suffix_consistent": len(suffixes) <= 1,
               "provided_uid_sync_match": (uid is not None and counts["SYNC"] > 0),
               "switch_mode": active_mode,
               "crc_basis_for_non_sync": basis}
    return results, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path, help="ranked PHY JSONL files")
    parser.add_argument("--uid-file", type=Path, help="optional private six-byte UID file")
    parser.add_argument("--switch-mode", choices=("hybrid", "wide"),
                        help="needed for RC if no SYNC frame is present; wide is not supported")
    parser.add_argument("--jsonl-out", type=Path, help="new output path; never overwritten")
    parser.add_argument("--include-bytes", action="store_true",
                        help="include pairing-sensitive raw packet bytes for private analysis")
    args = parser.parse_args(argv)
    try:
        uid = read_uid(args.uid_file) if args.uid_file else None
        rows = read_rows(args.inputs)
        results, summary = analyze(rows, uid, args.include_bytes, args.switch_mode)
        if args.jsonl_out:
            with args.jsonl_out.open("x", encoding="utf-8", newline="\n") as stream:
                for row in results:
                    stream.write(json.dumps(row, sort_keys=True) + "\n")
        print(json.dumps(summary, sort_keys=True))
        return 0 if summary["crc_valid"] else 1
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"ELRS LI decode error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
