#!/usr/bin/env python3
"""Offline SX1280 SF7/4-8-LI encoder and ELRS v3 MSP canary planner.

This tool performs no device or SDR I/O.  It inverts the independently
measured WaveShark long-interleaver mapping used by ``elrs-li-offline.py`` and
can prepare the three standard eight-byte ELRS packets needed to carry one
read-only MSPv1 ``MSP_API_VERSION`` request.  The output is an offline test
artifact, not evidence that a live receiver accepted a packet.

The UID file contains exactly six hexadecimal bytes and must remain private.
Default output omits packet bytes, symbols, and UID-derived values.  Use
``--private-plan-out`` only with an ignored path under ``work/``.

Examples::

  python tools/elrs-li-forward.py encode --packet-hex 00fab567c57c5658
  python tools/elrs-li-forward.py canary --uid-file work/private-uid.hex
  python tools/elrs-li-forward.py canary --uid-file work/private-uid.hex \
      --private-plan-out work/msp-api-version-canary.json

Algorithm and whitening sequence adapted from v0l/WaveShark's empirically
measured SX128x LI implementation, commit
62b513a7b699fd3a6928908102902abf9d9b2650, GPL-3.0-or-later:
https://github.com/v0l/waveshark/blob/62b513a7b699fd3a6928908102902abf9d9b2650/crates/decode/src/lora_li.rs
SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys


TOOLS = Path(__file__).resolve().parent
DECODER = runpy.run_path(str(TOOLS / "elrs-li-offline.py"))
CHECKER = runpy.run_path(str(TOOLS / "elrs-v3-frame-check.py"))

decode_symbols = DECODER["decode_symbols"]
parity = DECODER["parity"]
WHITENING = DECODER["WHITENING"]
crc14 = CHECKER["crc14"]
uid_crc_init = CHECKER["uid_crc_init"]
read_uid = CHECKER["read_uid"]

SF = 7
N = 1 << SF
ELRS_PACKET_BYTES = 8
ELRS_MSP_BYTES_PER_CALL = 5
ELRS_MSP_PACKET_TYPE = 1
CRSF_MSP_REQUEST = 0x7A
CRSF_ADDRESS_FLIGHT_CONTROLLER = 0xC8
CRSF_ADDRESS_RADIO_TRANSMITTER = 0xEA
MSP_API_VERSION = 1


def inverse_gray(gray: int) -> int:
    """Convert a reflected Gray-code integer back to binary."""
    value = 0
    while gray:
        value ^= gray
        gray >>= 1
    return value


def encode_symbols(packet: bytes) -> list[int]:
    """Encode an 8- or 13-byte payload into canonical SX1280 LI symbol bins."""
    if len(packet) not in (8, 13):
        raise ValueError("LI payload must contain exactly 8 or 13 bytes")
    nibbles = 2 * len(packet)
    coded = bytes(value ^ WHITENING[index % len(WHITENING)]
                  for index, value in enumerate(packet))
    # Each four-bit nibble becomes one systematic Hamming(8,4) codeword.
    bits = [0] * (8 * nibbles)
    for nibble_index in range(nibbles):
        value = (coded[nibble_index // 2] >>
                 (4 * (nibble_index % 2))) & 0x0F
        data = [(value >> bit) & 1 for bit in range(4)]
        checks = parity(data)
        for bit, state in enumerate(data):
            bits[nibble_index + nibbles * bit] = state
        for bit, state in enumerate(checks):
            bits[4 * nibbles + nibble_index + nibbles * bit] = state

    symbols: list[int] = []
    offset = 0
    symbol_count = 8 + max(0, (len(bits) - 40 + 6) // 7)
    for symbol_index in range(symbol_count):
        width = 5 if symbol_index < 8 else 7
        chunk = bits[offset:offset + width]
        chunk.extend([0] * (width - len(chunk)))
        gray = sum(state << bit for bit, state in enumerate(chunk))
        deoffset = inverse_gray(gray)
        symbols.append((1 + (deoffset << (SF - width))) & (N - 1))
        offset += width
    return symbols


def build_msp_v1_request(command: int) -> bytes:
    """Return the 15-byte stubborn-uplink buffer for one zero-payload request.

    The first 13 bytes mirror Betaflight 4.4.2's independent
    ``crsfPidRequest`` unit fixture (without its CRSF CRC); the final two bytes
    are ignored padding needed to fill three five-byte ELRS fragments.
    """
    if not 0 <= command <= 0xFF:
        raise ValueError("MSPv1 command must fit one byte")
    frame = bytes((0x00, 0x0D, CRSF_MSP_REQUEST,
                   CRSF_ADDRESS_FLIGHT_CONTROLLER,
                   CRSF_ADDRESS_RADIO_TRANSMITTER,
                   0x30, 0x00, command, command, 0x00, 0x00, 0x00, 0x00))
    return frame + bytes(2)


def stubborn_chunks(data: bytes) -> list[tuple[int, bytes]]:
    """Fragment a single 15-byte canary as receiver indices 1, 2, final 0."""
    if len(data) != 3 * ELRS_MSP_BYTES_PER_CALL:
        raise ValueError("canary buffer must contain exactly 15 bytes")
    return [(1, data[0:5]), (2, data[5:10]), (0, data[10:15])]


def build_ota_msp_packet(package_index: int, payload: bytes,
                         uid: bytes) -> bytes:
    """Build one CRC-valid Betaflight 4.4.2 standard MSP uplink packet."""
    if package_index not in (0, 1, 2):
        raise ValueError("canary package index must be 0, 1, or 2")
    if len(payload) != ELRS_MSP_BYTES_PER_CALL:
        raise ValueError("MSP uplink fragment must contain five bytes")
    if len(uid) != 6:
        raise ValueError("UID must contain six bytes")
    packet = bytearray((ELRS_MSP_PACKET_TYPE, package_index, *payload, 0))
    checksum = crc14(bytes(packet[:7]), uid_crc_init(uid))
    packet[0] |= ((checksum >> 8) & 0x3F) << 2
    packet[7] = checksum & 0xFF
    return bytes(packet)


def build_api_version_canary(uid: bytes) -> list[dict]:
    """Build three OTA packets and their canonical long-interleaved symbols."""
    request = build_msp_v1_request(MSP_API_VERSION)
    result = []
    for sequence, (package_index, payload) in enumerate(stubborn_chunks(request)):
        packet = build_ota_msp_packet(package_index, payload, uid)
        result.append({"sequence": sequence, "package_index": package_index,
                       "packet": packet, "symbols": encode_symbols(packet)})
    return result


def modulate_symbols(symbols: list[int], samples_per_chip: int = 1):
    """Return one ideal complex LoRa frame; imports NumPy only when requested.

    This is an offline waveform fixture.  It includes the source-matched
    12-symbol preamble, sync bins 8/16, 2.25 downchirps, and payload symbols.
    It does not perform FHSS scheduling, frequency tuning, or transmission.
    """
    if not symbols or any(not isinstance(value, int) or not 0 <= value < N
                          for value in symbols):
        raise ValueError("symbols must be integers in 0..127")
    if not isinstance(samples_per_chip, int) or not 1 <= samples_per_chip <= 32:
        raise ValueError("samples_per_chip must be an integer in 1..32")
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("NumPy is required for waveform generation") from exc
    x = np.arange(N * samples_per_chip, dtype=np.float64) / samples_per_chip
    up = np.exp(1j * np.pi * (x * x / N - x))

    def chirp(bin_number: int):
        return up * np.exp(2j * np.pi * bin_number * x / N)

    down = np.conj(up)
    pieces = ([chirp(0)] * 12 + [chirp(8), chirp(16), down, down,
              down[:N * samples_per_chip // 4]]
              + [chirp(value) for value in symbols])
    return np.concatenate(pieces).astype(np.complex64)


def private_plan(uid: bytes) -> dict:
    frames = build_api_version_canary(uid)
    return {
        "schema": "elrs-v3-msp-api-version-canary-v1",
        "transmit_enabled": False,
        "command": {"name": "MSP_API_VERSION", "id": MSP_API_VERSION,
                    "payload_bytes": 0, "read_only": True},
        "air": {"sf": 7, "bandwidth_hz": 812500,
                "coding": "SX1280 4/8 long interleaving",
                "implicit_payload_bytes": 8, "preamble_symbols": 12},
        "frames": [{"sequence": item["sequence"],
                    "package_index": item["package_index"],
                    "packet_hex": item["packet"].hex(),
                    "symbols": item["symbols"]}
                   for item in frames],
    }


def require_ignored_work_path(path: Path) -> Path:
    """Keep pairing-derived plans inside the repository's ignored work tree."""
    work = (TOOLS.parent / "work").resolve()
    target = path.resolve()
    try:
        target.relative_to(work)
    except ValueError as exc:
        raise ValueError("private plan output must be under the ignored work/ directory") from exc
    return target


def write_iq_bundle(plan: dict, directory: Path,
                    samples_per_chip: int = 5) -> dict:
    """Write unscheduled private fc32 frame fixtures for hardware loopback."""
    output = require_ignored_work_path(directory)
    output.mkdir(parents=True, exist_ok=False)
    sample_rate = 812_500 * samples_per_chip
    files = []
    for frame in plan["frames"]:
        iq = modulate_symbols(frame["symbols"], samples_per_chip) * 0.25
        raw = iq.astype("<c8", copy=False).tobytes()
        name = (f"frame-{frame['sequence']}-package-"
                f"{frame['package_index']}.cf32le")
        path = output / name
        path.write_bytes(raw)
        files.append({"file": name, "samples": len(iq),
                      "sha256": hashlib.sha256(raw).hexdigest(),
                      "package_index": frame["package_index"]})
    manifest = {
        "schema": "elrs-v3-unscheduled-iq-fixtures-v1",
        "transmit_enabled": False,
        "scheduled": False,
        "sample_format": "complex-float32-little-endian",
        "sample_rate": sample_rate,
        "samples_per_chip": samples_per_chip,
        "amplitude": 0.25,
        "frames": files,
    }
    with (output / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="operation", required=True)
    encode = commands.add_parser("encode", help="encode one known payload")
    encode.add_argument("--packet-hex", required=True,
                        help="8- or 13-byte payload in hexadecimal")
    canary = commands.add_parser("canary", help="prepare a read-only MSP canary")
    canary.add_argument("--uid-file", type=Path, required=True,
                        help="private six-byte UID hex file")
    canary.add_argument("--private-plan-out", type=Path,
                        help="new ignored JSON path; never overwritten")
    canary.add_argument("--private-iq-dir", type=Path,
                        help="new ignored directory for unscheduled fc32 fixtures")
    canary.add_argument("--samples-per-chip", type=int, default=5,
                        help="IQ oversampling factor, 1..32 (default: 5)")
    args = parser.parse_args(argv)
    try:
        if args.operation == "encode":
            packet = bytes.fromhex(args.packet_hex)
            symbols = encode_symbols(packet)
            decoded = decode_symbols(symbols, len(packet))
            print(json.dumps({"payload_bytes": len(packet),
                              "symbols": symbols,
                              "round_trip": decoded is not None
                              and decoded[0] == packet}, sort_keys=True))
            return 0

        uid = read_uid(args.uid_file)
        plan = private_plan(uid)
        if args.private_plan_out:
            output = require_ignored_work_path(args.private_plan_out)
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(plan, stream, indent=2, sort_keys=True)
                stream.write("\n")
        iq_manifest = (write_iq_bundle(plan, args.private_iq_dir,
                                       args.samples_per_chip)
                       if args.private_iq_dir else None)
        print(json.dumps({"command": "MSP_API_VERSION", "read_only": True,
                          "ota_packets": len(plan["frames"]),
                          "symbols_per_packet": len(plan["frames"][0]["symbols"]),
                          "private_plan_written": bool(args.private_plan_out),
                          "private_iq_written": iq_manifest is not None,
                          "iq_sample_rate": (iq_manifest["sample_rate"]
                                             if iq_manifest else None),
                          "scheduled": False,
                          "transmit_enabled": False}, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ELRS LI forward error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
