#!/usr/bin/env python3
"""Offline CRC/field checker for *pre-decoded* Betaflight 4.4.2 ELRS v3 bytes.

This is NOT an IQ decoder, packet forger, or live-device tool. It never opens
a radio, transmits, or changes a capture. A CRC match using UID bytes copied
from an untrusted SYNC candidate is explicitly labeled self-derived; it is
not proof of device identity. Repeated real frames and independent UID/FHSS
agreement are required before claiming byte recovery.

Input: one 8-byte hex frame per line, or JSON lines with ``packet_bytes_hex``
and optional ``nonce`` / ``switch_mode`` ("wide" or "hybrid"). Keep this
input under ignored ``work/`` if it came from private captures.

Examples:
  python tools/elrs-v3-frame-check.py --input work/candidates.jsonl --json
  python tools/elrs-v3-frame-check.py --input work/candidates.jsonl \
      --uid-file work/private-uid.hex --switch-mode wide --json

``--uid-file`` contains exactly six UID bytes as 12 hex digits (whitespace
allowed). The UID and raw frame bytes are never printed. The file must remain
private and untracked. Python 3.10+, standard library only.

Source: Betaflight 4.4.2, src/main/rx/expresslrs_common.{h,c} and
src/main/rx/expresslrs.c::validatePacketCrcStd/processRFSyncPacket.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CRC14_POLY = 0x2E57
CRC14_MASK = 0x3FFF
OTA_VERSION_ID = 3
FRAME_BYTES = 8
TYPE_NAMES = ("RC", "MSP", "SYNC", "TLM")
SWITCH_MODES = {"wide": 0, "hybrid": 1}
MODEL_MATCH_MASK = 0x3F


def crc14(data: bytes, initial: int) -> int:
    """MSB-first equivalent of 4.4.2 calcCrc14's 256-entry table."""
    if not 0 <= initial <= 0xFFFF:
        raise ValueError("CRC initializer must fit 16 bits")
    crc = initial & CRC14_MASK
    for value in data:
        crc ^= value << 6
        for _ in range(8):
            crc = ((crc << 1) ^ (CRC14_POLY if crc & 0x2000 else 0)) & CRC14_MASK
    return crc


def uid_crc_init(uid: bytes) -> int:
    if len(uid) != 6:
        raise ValueError("UID must contain six bytes")
    return ((uid[4] << 8) | uid[5]) ^ OTA_VERSION_ID


def check_frame(raw: bytes, *, uid: bytes | None = None,
                switch_mode: str | None = None, nonce: int | None = None,
                hop_interval: int = 4) -> dict:
    """Return a privacy-safe verdict; do not include raw bytes or UID fields."""
    if len(raw) != FRAME_BYTES:
        raise ValueError("v3 standard frame must contain exactly eight bytes")
    if uid is not None and len(uid) != 6:
        raise ValueError("UID must contain six bytes")
    if switch_mode is not None and switch_mode not in SWITCH_MODES:
        raise ValueError("switch mode must be wide or hybrid")
    if nonce is not None and not 0 <= nonce <= 255:
        raise ValueError("nonce must be a byte")
    if not 1 <= hop_interval <= 16:
        raise ValueError("hop interval must be 1..16")

    kind = raw[0] & 0x03
    result: dict = {"type": TYPE_NAMES[kind], "crc": "unverifiable",
                    "crc_basis": "missing_context"}
    if kind == 2:
        result["fields"] = {
            "fhss_index": raw[1], "nonce": raw[2],
            "switch_mode": "hybrid" if raw[3] & 1 else "wide",
            "new_tlm_ratio": (raw[3] >> 1) & 7,
            "rate_index": (raw[3] >> 4) & 15,
        }
        if uid is None:
            # The candidate frame itself supplies UID4/5; a passing CRC here
            # cannot independently authenticate those bytes or the device.
            initial = ((raw[5] << 8) | raw[6]) ^ OTA_VERSION_ID
            result["crc_basis"] = "self_derived_sync_suffix"
        else:
            initial = uid_crc_init(uid)
            result["crc_basis"] = "provided_uid"
            result["uid_18bit_match"] = (raw[4] == uid[3] and raw[5] == uid[4]
                                         and (raw[6] & ~MODEL_MATCH_MASK)
                                         == (uid[5] & ~MODEL_MATCH_MASK))
    elif uid is None:
        return result
    else:
        initial = uid_crc_init(uid)
        result["crc_basis"] = "provided_uid"

    if kind == 0 and switch_mode == "wide":
        if nonce is None:
            result["crc_basis"] = "missing_wide_nonce"
            return result
        first = ((nonce % hop_interval) + 1) << 2 | kind
        result["wide_slot"] = nonce % hop_interval
    elif kind == 0 and switch_mode is None:
        result["crc_basis"] = "missing_switch_mode"
        return result
    else:
        # SYNC, MSP, TLM, and SM_HYBRID RC zero crcHigh before calculation.
        first = kind

    transmitted = ((raw[0] >> 2) << 8) | raw[7]
    calculated = crc14(bytes([first]) + raw[1:7], initial)
    result["crc"] = "pass" if transmitted == calculated else "fail"
    return result


def read_uid(path: Path) -> bytes:
    try:
        uid = bytes.fromhex(path.read_text(encoding="ascii"))
    except (OSError, ValueError) as exc:
        raise ValueError("UID file must contain exactly 12 hex digits") from exc
    if len(uid) != 6:
        raise ValueError("UID file must contain exactly 12 hex digits")
    return uid


def parse_candidate(line: str) -> tuple[bytes, dict]:
    text = line.strip()
    if text.startswith("{"):
        item = json.loads(text)
        if not isinstance(item, dict) or "packet_bytes_hex" not in item:
            raise ValueError("JSON candidate requires packet_bytes_hex")
        raw = bytes.fromhex(item["packet_bytes_hex"])
        return raw, {key: item[key] for key in ("nonce", "switch_mode") if key in item}
    return bytes.fromhex(text), {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="Hex lines or JSONL of pre-decoded frames")
    parser.add_argument("--uid-file", type=Path, help="Private six-byte UID hex file; never printed")
    parser.add_argument("--switch-mode", choices=sorted(SWITCH_MODES), help="Known RC switch mode")
    parser.add_argument("--nonce", type=int, help="Known RC nonce, overridden by per-frame JSON nonce")
    parser.add_argument("--hop-interval", type=int, default=4, help="Packets per hop (150 Hz source: 4)")
    parser.add_argument("--json", action="store_true", help="One privacy-safe JSON verdict per input frame")
    args = parser.parse_args(argv)
    try:
        uid = read_uid(args.uid_file) if args.uid_file else None
        print(f"Checking offline candidates from {args.input.name}", file=sys.stderr)
        with args.input.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                raw, overrides = parse_candidate(line)
                verdict = check_frame(raw, uid=uid,
                                      switch_mode=overrides.get("switch_mode", args.switch_mode),
                                      nonce=overrides.get("nonce", args.nonce),
                                      hop_interval=args.hop_interval)
                verdict["line"] = number
                if args.json:
                    print(json.dumps(verdict, sort_keys=True))
                else:
                    print(f"line {number}: {verdict['type']} CRC {verdict['crc']} "
                          f"({verdict['crc_basis']})")
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Frame check error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
