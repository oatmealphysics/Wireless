#!/usr/bin/env python3
"""SigMF metadata standardization for the private ELRS IQ corpus (RQ1).

Writes a standard SigMF v1.0.0 ``.sigmf-meta`` sidecar for each tracked
``captures/*.iq`` file, recording the clock/timebase provenance that the
passive pipeline currently leaves as zeros (``sample_rate: 0``,
``capture_sha256: ""``). This is the first step of roadmap RQ1 —
"Capture corpus and timebase standardization".

Scope and safety (matches PROJECT-ROADMAP.md RQ1 evidence target):

* **SYNTHETIC/OFFLINE-TESTED.** The tool only reads the capture bytes to
  compute a SHA-256 (for provenance) and the sample count (from the file
  size); it never modifies the IQ file and never writes a private capture or
  identifier anywhere. The original file SHA-256 is preserved as provenance —
  the sidecar is a separate file and the ``.iq`` is byte-for-byte untouched.
* Records datatype (``sc16le``), endianness (little), sample rate/count, and
  the capture center frequency, exactly as documented in
  ``captures/README.md`` (Ettus B205mini, RX2, 2441.000 MHz, 4 Msps).
* The two tracked captures have a fixed, known timebase, so the annotation is
  a static clock statement plus the original-filesystem creation-time anchor
  from the README (declared as an *estimate* of RF sample zero, not an
  instrument-synchronized timestamp).
* Windows/Linux: pure standard library, no numpy/scipy.

The SigMF annotation set is deliberately minimal — it captures the clock and
provenance, nothing that would leak a pairing UID or raw private response.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

SIGMF_VERSION = "1.0.0"

# Data format constants for the tracked corpus (see captures/README.md).
DATATYPE_SC16LE = "sc16le"
BYTES_PER_SAMPLE = 4  # interleaved I,Q as two int16
SAMPLE_RATE_HZ = 4_000_000  # 4 Msps complex
CENTER_FREQ_HZ = 2_441_000_000.0  # 2441.000000 MHz


@dataclass(frozen=True)
class CaptureMeta:
    """Static, source-documented timebase for one tracked capture."""

    filename: str
    center_hz: float = CENTER_FREQ_HZ
    sample_rate_hz: int = SAMPLE_RATE_HZ
    # Original-filesystem creation-time anchor (estimate of RF sample zero).
    # From captures/README.md; declared approximate, not instrument-synced.
    creation_time_estimate: str | None = None
    notes: str = field(default="")


# The two tracked captures and their documented provenance. The creation-time
# anchor is only recorded for the Oct 16 file (the README documents it); the
# Oct 15 file has no documented anchor and is left as None.
TRACKED_CAPTURES: dict[str, CaptureMeta] = {
    "e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq": CaptureMeta(
        filename="e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq",
        notes=(
            "Ettus B205mini RX2, 2441.000 MHz center, 4 Msps complex, sc16le. "
            "20.000 s / 80,000,000 samples. Passive receive-only capture of the "
            "owned E8-TinyHawk ELRS pair; no RF transmitted, no flash."
        ),
    ),
    "tinyhawk_rc_pair_20261016_retry_2441MHz_4Msps_sc16.iq": CaptureMeta(
        filename="tinyhawk_rc_pair_20261016_retry_2441MHz_4Msps_sc16.iq",
        creation_time_estimate="2026-10-16T17:45:37.014895Z",
        notes=(
            "Ettus B205mini RX2, 2441.000 MHz center, 4 Msps complex, sc16le. "
            "15.0025 s / 60,010,000 samples. Passive receive-only capture while "
            "the owner moved the right stick left/right; no RF transmitted, no "
            "flash. creation_time is an estimate of RF sample zero, not an "
            "instrument-synchronized timestamp."
        ),
    ),
}


def sha256_of(path: Path, *, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file (large IQ files; no full read into RAM)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sample_count(path: Path) -> int:
    """Interleaved complex int16 sample count from the file size."""
    size = path.stat().st_size
    if size % BYTES_PER_SAMPLE != 0:
        raise ValueError(
            f"{path.name}: size {size} is not a multiple of "
            f"{BYTES_PER_SAMPLE} bytes (sc16le)"
        )
    return size // BYTES_PER_SAMPLE


def build_sigmf_meta(meta: CaptureMeta, iq_path: Path) -> dict:
    """Build the SigMF annotation dict for one capture (pure, testable).

    This is the deterministic core: given the static ``CaptureMeta`` and the
    measured file size/hash, produce the SigMF record. No I/O here beyond what
    the caller passes in, so it is trivially unit-testable on a fixture.
    """
    nsamples = sample_count(iq_path)
    record: dict = {
        "global": {
            "core:version": SIGMF_VERSION,
            "core:datatype": DATATYPE_SC16LE,
            "core:sample_rate": meta.sample_rate_hz,
            "core:sample_start": 0,
            "core:sample_size": nsamples,
            "core:freq_center": meta.center_hz,
            # Provenance: preserve the original file SHA-256. The SigMF spec
            # has no standard field for this, so it is carried in a custom
            # ``core:``-prefixed-free annotation key to avoid namespace clash.
            "authored": "andeds (poc-research RQ1)",
            "provenance:original_sha256": sha256_of(iq_path),
            "provenance:capture_device": "Ettus B205mini RX2",
            "provenance:private": True,
        },
        "annotations": [
            {
                "core:start": 0,
                "core:length": nsamples,
                "core:freq_center": meta.center_hz,
                "core:sample_rate": meta.sample_rate_hz,
                "core:extends": "global",
                "sigmf:label": "ELRS 2.4 GHz passive capture",
                "sigmf:description": meta.notes,
            }
        ],
    }
    if meta.creation_time_estimate is not None:
        record["annotations"][0]["sigmf:creation_time_estimate"] = (
            meta.creation_time_estimate
        )
        record["annotations"][0]["sigmf:timebase_note"] = (
            "creation_time is an estimate of RF sample zero, not an "
            "instrument-synchronized timestamp"
        )
    return record


def write_sigmf_meta(iq_path: Path, meta: CaptureMeta, *, force: bool = False) -> Path:
    """Write ``<name>.sigmf-meta`` next to the IQ file. Returns the path.

    The IQ file is never modified. If a sidecar already exists and ``force``
    is False, the existing file is left alone (idempotent, no clobber).
    """
    out = iq_path.with_suffix(iq_path.suffix + ".sigmf-meta")
    if out.exists() and not force:
        return out
    record = build_sigmf_meta(meta, iq_path)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out


def validate_sigmf_meta(path: Path) -> dict:
    """Read back a SigMF sidecar and check its invariants (test/CI helper).

    Returns a small report dict. Raises ValueError on a malformed file.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    g = data["global"]
    a = data["annotations"][0]
    report = {
        "version_ok": g["core:version"] == SIGMF_VERSION,
        "datatype_ok": g["core:datatype"] == DATATYPE_SC16LE,
        "sample_size_positive": g["core:sample_size"] > 0,
        "annotation_covers_full_capture": a["core:length"] == g["core:sample_size"],
        "has_provenance_sha": "provenance:original_sha256" in g,
        "private_flagged": g.get("provenance:private") is True,
    }
    if not all(report.values()):
        raise ValueError(f"{path.name}: SigMF invariant failure: {report}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write SigMF v1.0.0 sidecars for tracked ELRS IQ captures (RQ1)."
    )
    parser.add_argument(
        "--captures-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "captures",
        help="Directory containing the tracked .iq files (default: repo captures/).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing .sigmf-meta sidecar (default: leave it).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate existing sidecars against their invariants and exit.",
    )
    args = parser.parse_args(argv)

    exit_code = 0
    for filename, meta in TRACKED_CAPTURES.items():
        iq = args.captures_dir / filename
        if not iq.exists():
            print(f"skip (not present): {filename}", file=sys.stderr)
            continue
        if args.check:
            sidecar = iq.with_suffix(iq.suffix + ".sigmf-meta")
            if not sidecar.exists():
                print(f"MISSING sidecar: {sidecar.name}", file=sys.stderr)
                exit_code = 1
                continue
            report = validate_sigmf_meta(sidecar)
            print(f"OK {sidecar.name}: {report}")
            continue
        sidecar = write_sigmf_meta(iq, meta, force=args.force)
        nsamples = sample_count(iq)
        print(
            f"wrote {sidecar.name} "
            f"({nsamples} samples, {meta.sample_rate_hz} Hz, sha256 recorded)"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
