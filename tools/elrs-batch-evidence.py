#!/usr/bin/env python3
"""Batch bounded, receive-only ELRS symbol checks into private JSON Lines.

This wraps ``elrs-iq-symbols.py`` for known burst start estimates. It does not
decode bytes, access hardware, or transmit. The output includes raw candidate
symbol bins, which may disclose pairing information after future decoding;
therefore output is restricted to this repository's ignored ``work/`` tree.
An existing output file is never overwritten.

Requires NumPy and SciPy (Python 3.10+). Example:

  python tools/elrs-batch-evidence.py \
      --capture work/captures/2026-10-08_13-07-21_2450mhz_pred.cdif \
      --sample-rate 28000000 --center-hz 2450000000 \
      --channel-hz 2441378800 \
      --packet-start 0.40729 --packet-start 0.41395 \
      --max-symbols 21 --output work/oct08-symbols.jsonl

Only aggregate quality and the output basename are printed to the terminal.
Ranked symbol hypotheses stay in the private JSONL, never terminal summaries.
No capture or device settings are changed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


WORK_ROOT = Path(__file__).resolve().parents[1] / "work"


def private_output_path(path: Path) -> Path:
    target = path.resolve()
    if not target.is_relative_to(WORK_ROOT.resolve()) or target == WORK_ROOT.resolve():
        raise ValueError("Full symbol output must be inside this repository's ignored work/ tree")
    if target.exists():
        raise FileExistsError("Output already exists; choose a new private filename")
    return target


def load_symbol_tool():
    script = Path(__file__).with_name("elrs-iq-symbols.py")
    spec = importlib.util.spec_from_file_location("elrs_iq_symbols", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summarize(result: dict, threshold: float = 0.2) -> dict:
    payload = result["payload_symbols"]
    return {"preamble_start_seconds": round(result["preamble_start_seconds"], 8),
            "phy_candidate": result["phy_candidate"],
            "preamble_consistent": result["preamble"]["preamble_consistent"],
            "sync_consistent": result["preamble"]["sync_consistent"],
            "strong_symbols": sum(item["peak_power_fraction"] > threshold
                                  for item in payload),
            "low_margin_symbols_lt3db": sum(item.get("margin_db") is not None
                                               and item["margin_db"] < 3
                                               for item in payload),
            "reported_symbols": len(payload)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--capture", type=Path, required=True, help="Local passive IQ capture")
    parser.add_argument("--sample-rate", type=float, required=True)
    parser.add_argument("--center-hz", type=float, required=True)
    parser.add_argument("--channel-hz", type=float, required=True)
    parser.add_argument("--packet-start", type=float, action="append", required=True,
                        help="Approximate preamble time; repeat for each burst")
    parser.add_argument("--search-us", type=float, default=120)
    parser.add_argument("--max-symbols", type=int, default=21)
    parser.add_argument("--top-k", type=int, default=3,
                        help="Ranked bin hypotheses per payload symbol (default 3)")
    parser.add_argument("--output", type=Path, required=True,
                        help="New JSONL filename under ignored work/")
    args = parser.parse_args(argv)
    try:
        output = private_output_path(args.output)
        if not 1 <= len(args.packet_start) <= 100:
            raise ValueError("Provide 1..100 packet starts per batch")
        symbols = load_symbol_tool()
        results = []
        for guess in args.packet_start:
            item = symbols.analyze(args.capture, args.sample_rate,
                                   args.center_hz, args.channel_hz, guess,
                                   args.search_us, args.max_symbols,
                                   top_k=args.top_k)
            item["packet_start_guess_seconds"] = guess
            results.append(item)
            print(json.dumps(summarize(item), sort_keys=True), file=sys.stderr)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            for item in results:
                stream.write(json.dumps(item, sort_keys=True) + "\n")
        print(f"Saved {len(results)} private candidate records to {output.name}",
              file=sys.stderr)
        return 0 if all(item["phy_candidate"] for item in results) else 1
    except (OSError, ValueError) as exc:
        print(f"Batch evidence error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
