#!/usr/bin/env python3
"""Read-only inventory and integrity check for private ELRS IQ captures.

The tool never opens a radio or interprets packet bytes. It streams hashes, checks
sample alignment, and reports whether a requested RF channel is inside the *digital*
Nyquist span. For MIDAS/BLUE .cdif it counts only the IQ data region. Analog
filtering can make a channel unusable despite that result.

Examples:
  python tools/elrs-capture-intake.py work/captures --recursive --json
  python tools/elrs-capture-intake.py capture.iq --format sc16le \
      --sample-rate 4000000 --center-hz 2441000000 --hash
  python tools/elrs-capture-intake.py incoming/ --recursive \
      --manifest incoming/SHA256SUMS.txt --json
  python tools/elrs-capture-intake.py recording.cdif --json

Python 3.10+, standard library only. Raw captures and output containing pairing
identifiers belong under ignored ``work/``; do not commit them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path

SAMPLE_BYTES = {"sc16le": 4, "cf32le": 8, "cu8": 2}
CAPTURE_SUFFIXES = {".iq", ".complex8", ".cdif", ".cfile", ".raw"}
HASH_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")
NUMBER = r"([+-]?\d+(?:\.\d+)?)"
FILENAME_RATE = re.compile(NUMBER + r"\s*(?:M|G|k)?sps", re.I)
FILENAME_CENTER = re.compile(NUMBER + r"\s*(?:M|G|k)?hz", re.I)


def scaled_number(text: str) -> float | None:
    match = re.search(NUMBER + r"\s*([GMk]?)(?:sps|hz)?", text, re.I)
    if not match:
        return None
    scale = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[match.group(2).lower()]
    return float(match.group(1)) * scale


def filename_metadata(path: Path) -> dict:
    """Parse only explicit unit-bearing filename hints, never assume a format."""
    result: dict = {}
    rate = FILENAME_RATE.search(path.name)
    center = FILENAME_CENTER.search(path.name)
    if rate:
        result["sample_rate_hz"] = scaled_number(rate.group(0))
    if center:
        result["center_hz"] = scaled_number(center.group(0))
    if re.search(r"(?:^|[_\-.])sc16(?:le)?(?:[_\-.]|$)", path.name, re.I):
        result["format"] = "sc16le"
    return result


def sidecar_metadata(path: Path) -> dict:
    candidates = (path.with_name(path.name + ".meta.txt"),
                  path.with_suffix(".meta.txt"))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        result: dict = {"sidecar": str(candidate)}
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            pair = re.match(r"\s*[-#]*\s*([\w .-]+?)\s*[:=]\s*(.*?)\s*$", line)
            if not pair:
                continue
            key = re.sub(r"[^a-z0-9]", "", pair.group(1).lower())
            value = pair.group(2)
            if key in {"samplerate", "sampleratehz", "rate", "ratehz", "sps"}:
                result["sample_rate_hz"] = scaled_number(value)
            elif key in {"center", "centerfreq", "centerfrequency", "centerhz", "frequency"}:
                result["center_hz"] = scaled_number(value)
            elif key in {"format", "sampleformat", "datatype"}:
                fmt = value.strip().lower()
                if fmt in SAMPLE_BYTES:
                    result["format"] = fmt
        return result
    return {}


def blue_metadata(path: Path) -> dict:
    """Read the small MIDAS/BLUE container headers, never the IQ payload."""
    size = path.stat().st_size
    if size < 512:
        raise ValueError(f"Truncated BLUE header: {path}")
    with path.open("rb") as stream:
        header = stream.read(512)
        if header[:4] != b"BLUE" or header[4:12] != b"EEEIEEEI":
            raise ValueError(f"Unsupported BLUE header or byte order: {path}")
        if struct.unpack_from("<i", header, 48)[0] not in (1000, 1001):
            raise ValueError(f"Unsupported BLUE record type: {path}")
        code = header[52:54]
        formats = {b"CF": "cf32le", b"CI": "sc16le"}
        if code not in formats:
            raise ValueError(f"Unsupported BLUE IQ format {code!r}: {path}")
        data_start, data_size = struct.unpack_from("<dd", header, 32)
        if (not math.isfinite(data_start) or not math.isfinite(data_size)
                or data_start < 512 or data_size < 0
                or not data_start.is_integer() or not data_size.is_integer()):
            raise ValueError(f"Invalid BLUE data boundaries: {path}")
        start, length = int(data_start), int(data_size)
        if start + length > size:
            raise ValueError(f"Truncated BLUE IQ payload: {path}")
        delta = struct.unpack_from("<d", header, 264)[0]
        if not math.isfinite(delta) or delta <= 0:
            raise ValueError(f"Invalid BLUE sample interval: {path}")
        result = {"container": "midas_blue", "format": formats[code],
                  "data_offset_bytes": start, "data_size_bytes": length,
                  "trailing_bytes": size - start - length,
                  "sample_rate_hz": 1 / delta}

        # The extended header's RF_FREQ tag is the authoritative center when
        # present. A filename frequency remains only a fallback hint.
        ext_block, ext_size = struct.unpack_from("<ii", header, 24)
        ext_start = ext_block * 512
        if ext_size < 0 or ext_size > 8 * 1024 * 1024 or (ext_size and
                (ext_start < start + length or ext_start + ext_size > size)):
            raise ValueError(f"Invalid BLUE extended-header boundaries: {path}")
        if ext_size:
            stream.seek(ext_start)
            extended = stream.read(ext_size)
            offset = 0
            while offset < ext_size:
                if offset + 8 > ext_size:
                    raise ValueError(f"Truncated BLUE extended record: {path}")
                record_size, extra_size, tag_size = struct.unpack_from(
                    "<ihb", extended, offset)
                if (record_size < 8 or extra_size < 0 or tag_size < 0
                        or offset + record_size > ext_size):
                    raise ValueError(f"Invalid BLUE extended record: {path}")
                value_size = record_size - extra_size
                if (value_size < 0 or
                        offset + 8 + value_size + tag_size > offset + record_size):
                    raise ValueError(f"Invalid BLUE extended value: {path}")
                tag_at = offset + 8 + value_size
                tag = extended[tag_at:tag_at + tag_size]
                if tag == b"RF_FREQ" and extended[offset + 7:offset + 8] == b"D" and value_size == 8:
                    center = struct.unpack_from("<d", extended, offset + 8)[0]
                    if math.isfinite(center) and center > 0:
                        result["center_hz"] = center
                offset += record_size
        return result


def load_manifest(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = HASH_LINE.match(line)
        if not match:
            raise ValueError(f"Unrecognized SHA-256 line {number}: {path}")
        name = match.group(2).replace("\\", "/").removeprefix("./")
        if name in hashes:
            raise ValueError(f"Duplicate manifest name: {name}")
        hashes[name] = match.group(1).lower()
    return hashes


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture_files(paths: list[Path], recursive: bool) -> list[Path]:
    found = set()
    for path in paths:
        if path.is_file():
            found.add(path.resolve())
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.iterdir()
            found.update(item.resolve() for item in iterator
                         if item.is_file() and item.suffix.lower() in CAPTURE_SUFFIXES)
        else:
            raise FileNotFoundError(path)
    return sorted(found)


def inspect(path: Path, args: argparse.Namespace, manifest: dict[str, str]) -> dict:
    size = path.stat().st_size
    metadata = filename_metadata(path)
    metadata.update(sidecar_metadata(path))
    if path.suffix.lower() == ".cdif":
        metadata.update(blue_metadata(path))
    for key, value in (("format", args.format), ("sample_rate_hz", args.sample_rate),
                       ("center_hz", args.center_hz)):
        if value is not None:
            metadata[key] = value
    result = {"path": str(path), "size_bytes": size, **metadata}
    payload_size = metadata.get("data_size_bytes", size)
    fmt = metadata.get("format")
    sample_rate = metadata.get("sample_rate_hz")
    center = metadata.get("center_hz")
    if fmt:
        width = SAMPLE_BYTES[fmt]
        result["sample_aligned"] = payload_size % width == 0
        if result["sample_aligned"]:
            result["complex_samples"] = payload_size // width
            if sample_rate and sample_rate > 0:
                result["duration_seconds"] = payload_size / width / sample_rate
    if sample_rate and sample_rate > 0 and center is not None:
        low, high = center - sample_rate / 2, center + sample_rate / 2
        result["digital_span_hz"] = [low, high]
        result["requested_channel_hz"] = args.channel_hz
        result["channel_inside_digital_span"] = low < args.channel_hz < high
    if args.hash or args.manifest is not None:
        print(f"Hashing {path.name} ({size} bytes)", file=sys.stderr)
        result["sha256"] = sha256_file(path)
    if args.manifest is not None:
        # Manifest paths may be relative to the manifest or just basenames.
        try:
            relative = path.relative_to(args.manifest.resolve().parent).as_posix()
        except ValueError:
            relative = ""
        expected = manifest.get(relative) or manifest.get(path.name)
        result["manifest_status"] = ("missing" if expected is None else
                                     "match" if expected == result["sha256"] else "mismatch")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path, help="IQ file(s) or directories")
    parser.add_argument("--recursive", action="store_true", help="Recurse into directories")
    parser.add_argument("--manifest", type=Path, help="SHA256SUMS.txt or sha256sum-style file")
    parser.add_argument("--hash", action="store_true", help="Calculate SHA-256 without a manifest")
    parser.add_argument("--format", choices=sorted(SAMPLE_BYTES), help="Override sample format")
    parser.add_argument("--sample-rate", type=float, help="Override complex samples per second")
    parser.add_argument("--center-hz", type=float, help="Override center frequency in Hz")
    parser.add_argument("--channel-hz", type=float, default=2441.3788e6,
                        help="Channel to check against Nyquist span (default: measured v3 sync)")
    parser.add_argument("--json", action="store_true", help="Emit JSON lines")
    args = parser.parse_args(argv)
    if args.sample_rate is not None and args.sample_rate <= 0:
        parser.error("--sample-rate must be positive")
    try:
        manifest = load_manifest(args.manifest) if args.manifest else {}
        files = capture_files(args.paths, args.recursive)
        if not files:
            raise ValueError("No capture files found")
        failures = 0
        for path in files:
            item = inspect(path, args, manifest)
            if item.get("sample_aligned") is False or item.get("manifest_status") in {"mismatch", "missing"}:
                failures += 1
            if args.json:
                print(json.dumps(item, sort_keys=True))
            else:
                print(f"{item['path']}  {item['size_bytes']} bytes")
                for key in ("container", "format", "data_offset_bytes", "data_size_bytes",
                            "sample_rate_hz", "center_hz", "duration_seconds",
                            "sample_aligned", "channel_inside_digital_span", "sha256",
                            "manifest_status"):
                    if key in item:
                        print(f"  {key}: {item[key]}")
        return 1 if failures else 0
    except (OSError, ValueError) as exc:
        print(f"Intake error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
