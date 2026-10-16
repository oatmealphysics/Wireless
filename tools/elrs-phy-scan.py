#!/usr/bin/env python3
"""Automatically locate LoRa PHY candidates in a local, receive-only IQ file.

This scans a bounded time interval on one RF channel, using energy only to
propose windows and ``elrs-iq-symbols.py`` to verify the 12-chirp preamble
and two LoRa sync-word chirps. Those chirps do NOT identify an ELRS SYNC
packet type; byte decoding is still required for that claim. The tool never
opens a radio or transmits.

Full candidate bins stay in a new JSONL file under the repository's ignored
``work/`` directory. Terminal output contains only aggregate counts. Example::

    python tools/elrs-phy-scan.py \\
      --capture work/captures/e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq \\
      --sample-rate 4000000 --center-hz 2441000000 \\
      --channel-hz 2441378800 --start-seconds 1.17 --duration-seconds 0.06 \\
      --output work/auto-phy-candidates.jsonl

Requires Python 3.10+, NumPy, and SciPy. Supports sc16le, cf32le, and
MIDAS/BLUE .cdif via the existing bounded IQ reader. No file is overwritten.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import importlib.util
import json
import math
from pathlib import Path
import sys

import numpy as np


WORK_ROOT = Path(__file__).resolve().parents[1] / "work"
BLOCK = 32  # 39.4 us at the 812.5 kS/s baseband rate
OVERLAP_SECONDS = 0.010


def load_symbols():
    script = Path(__file__).with_name("elrs-iq-symbols.py")
    spec = importlib.util.spec_from_file_location("elrs_iq_symbols", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_output(path: Path) -> Path:
    target = path.resolve()
    if not target.is_relative_to(WORK_ROOT.resolve()) or target == WORK_ROOT.resolve():
        raise ValueError("candidate output must be under the ignored work/ directory")
    if target.exists():
        raise FileExistsError("output already exists; choose a new private filename")
    return target


def energy_regions(baseband: np.ndarray, rate: float,
                   ratio: float = 6.0, min_ms: float = 1.4) -> list[tuple[int, int]]:
    """Return coarse above-noise intervals in baseband sample coordinates."""
    if len(baseband) < BLOCK or ratio <= 1 or min_ms <= 0:
        return []
    blocks = len(baseband) // BLOCK
    power = np.mean(np.abs(baseband[:blocks * BLOCK].reshape(blocks, BLOCK)) ** 2,
                    axis=1)
    baseline = float(np.median(power))
    if baseline <= 0:
        positive = power[power > 0]
        baseline = float(np.median(positive)) if len(positive) else 0.0
    if baseline <= 0:
        return []
    active = power > baseline * ratio
    edges = np.diff(np.r_[False, active, False].astype(np.int8))
    starts = np.flatnonzero(edges == 1).tolist()
    ends = np.flatnonzero(edges == -1).tolist()
    max_gap = max(1, round(0.00024 * rate / BLOCK))
    merged: list[list[int]] = []
    for first, last in zip(starts, ends):
        if merged and first - merged[-1][1] <= max_gap:
            merged[-1][1] = last
        else:
            merged.append([first, last])
    min_blocks = math.ceil(min_ms * 1e-3 * rate / BLOCK)
    return [(first * BLOCK, last * BLOCK) for first, last in merged
            if last - first >= min_blocks]


def scan_chunk(baseband: np.ndarray, actual_start: float, rate: float,
               ratio: float, max_symbols: int, symbols) -> list[dict]:
    results = []
    for onset, region_end in energy_regions(baseband, rate, ratio):
        first = max(0, onset - round(0.00055 * rate))
        last = min(len(baseband) - 14 * symbols.N,
                   onset + round(0.00020 * rate))
        for _ in range(32):  # bounded even if interference stays above threshold
            if first > last:
                break
            try:
                preamble = symbols.find_preamble(baseband, first, last)
                quality = (preamble["preamble_consistent"] >= 10 and
                           preamble["sync_consistent"] and preamble["score"] >= 0.2)
                if not quality:
                    break
                payload = symbols.extract_payload_symbols(
                    baseband, preamble["start_sample"], preamble["baseline_bin"],
                    max_symbols)
            except ValueError:
                break
            results.append({
                "preamble_start_seconds": actual_start + preamble["start_sample"] / rate,
                "preamble": preamble,
                "payload_symbols": payload,
                "phy_candidate": True,
                "packet_bytes_decoded": False,
                "note": "LoRa PHY sync word only; ELRS packet type and CRC unverified.",
            })
            # Adjacent 150 Hz packets can form one energy region. Search for
            # another preamble several milliseconds later without claiming
            # that every such region contains an ELRS frame.
            previous = preamble["start_sample"]
            if region_end - previous < round(0.005 * rate):
                break
            first = previous + round(0.004 * rate)
            last = min(previous + round(0.008 * rate),
                       len(baseband) - 14 * symbols.N)
    return results


def scan_file(capture: Path, sample_rate: float, center_hz: float,
              channel_hz: float, start: float, duration: float,
              chunk_seconds: float, ratio: float, max_symbols: int,
              input_format: str = "auto") -> list[dict]:
    if (not all(math.isfinite(value) for value in
                (sample_rate, center_hz, channel_hz, start, duration, chunk_seconds, ratio))
            or sample_rate <= 0 or start < 0 or duration <= 0
            or not 0.03 <= chunk_seconds <= 1.0 or ratio <= 1
            or not 0 <= max_symbols <= 64):
        raise ValueError("invalid scan parameters")
    if abs(channel_hz - center_hz) >= sample_rate / 2:
        raise ValueError("channel lies outside the digital Nyquist span")
    symbols = load_symbols()
    denominator = Fraction(symbols.BW_HZ / sample_rate).limit_denominator(4096).denominator
    results: list[dict] = []
    cursor = start
    end = start + duration
    while cursor < end - 1e-9:
        scan_end = min(cursor + chunk_seconds, end)
        read_start = max(0.0, cursor - OVERLAP_SECONDS)
        read_length = scan_end - read_start + OVERLAP_SECONDS
        iq, actual_start = symbols.read_iq_window(
            capture, sample_rate, read_start, read_length, input_format,
            align_samples=denominator)
        baseband, rate = symbols.shift_and_resample(
            iq, sample_rate, channel_hz - center_hz)
        for item in scan_chunk(baseband, actual_start, rate, ratio, max_symbols, symbols):
            t = item["preamble_start_seconds"]
            if not cursor <= t < scan_end:
                continue
            item.update({"capture": str(capture), "sample_rate_hz": sample_rate,
                         "center_hz": center_hz, "channel_hz": channel_hz,
                         "phy_mode": "SF7 BW812500 candidate; no byte decode"})
            if not results or t - results[-1]["preamble_start_seconds"] > 0.001:
                results.append(item)
        cursor = scan_end
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--input-format", choices=("auto", "sc16le", "cf32le", "blue"),
                        default="auto")
    parser.add_argument("--sample-rate", type=float, required=True)
    parser.add_argument("--center-hz", type=float, required=True)
    parser.add_argument("--channel-hz", type=float, required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--chunk-seconds", type=float, default=0.20)
    parser.add_argument("--energy-ratio", type=float, default=6.0)
    parser.add_argument("--max-symbols", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True,
                        help="new private JSONL file under ignored work/")
    args = parser.parse_args(argv)
    try:
        output = private_output(args.output)
        print("Scanning recorded IQ for LoRa PHY candidates only", file=sys.stderr)
        results = scan_file(args.capture, args.sample_rate, args.center_hz,
                            args.channel_hz, args.start_seconds,
                            args.duration_seconds, args.chunk_seconds,
                            args.energy_ratio, args.max_symbols,
                            args.input_format)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            for item in results:
                stream.write(json.dumps(item, sort_keys=True) + "\n")
        print(f"Saved {len(results)} private PHY candidates to {output.name}",
              file=sys.stderr)
        print(json.dumps({"phy_candidates": len(results),
                          "packet_bytes_decoded": False}, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"PHY scan error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
