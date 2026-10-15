#!/usr/bin/env python3
"""Read-only inventory and integrity check for private ELRS IQ captures.

The tool never opens a radio or interprets packet bytes. It streams hashes, checks
sample alignment, and reports whether a requested RF channel is inside the *digital*
Nyquist span. Analog filtering can make a channel unusable despite that result.

Examples:
  python tools/elrs-capture-intake.py work/captures --recursive --json
  python tools/elrs-capture-intake.py capture.iq --format sc16le \
      --sample-rate 4000000 --center-hz 2441000000 --hash
  python tools/elrs-capture-intake.py incoming/ --recursive \
      --manifest incoming/SHA256SUMS.txt --json

Python 3.10+, standard library only. Raw captures and output containing pairing
identifiers belong under ignored ``work/``; do not commit them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
    for key, value in (("format", args.format), ("sample_rate_hz", args.sample_rate),
                       ("center_hz", args.center_hz)):
        if value is not None:
            metadata[key] = value
    result = {"path": str(path), "size_bytes": size, **metadata}
    fmt = metadata.get("format")
    sample_rate = metadata.get("sample_rate_hz")
    center = metadata.get("center_hz")
    if fmt:
        width = SAMPLE_BYTES[fmt]
        result["sample_aligned"] = size % width == 0
        if result["sample_aligned"]:
            result["complex_samples"] = size // width
            if sample_rate and sample_rate > 0:
                result["duration_seconds"] = size / width / sample_rate
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
                for key in ("format", "sample_rate_hz", "center_hz", "duration_seconds",
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
