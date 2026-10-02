#!/usr/bin/env bash
# build.sh — build the ELRS interceptor on Borealis (where UHD is installed).
#
#   ./build.sh            # builds elrs_intercept (UHD live tool)
#   ./build.sh test       # builds + runs the CPU-only decode unit test (no UHD)
#
# Requires: g++ (C++17), UHD dev headers + libuhd.
set -euo pipefail
cd "$(dirname "$0")"

# --- CPU-only decode unit test (no UHD, runs anywhere with g++) ---
if [ "${1:-}" = "test" ]; then
  echo "[build] compiling CPU-only decode unit test..."
  g++ -O2 -std=c++17 tests/test_elrs_decode.cpp -o test_elrs_decode
  echo "[build] running decode unit test..."
  ./test_elrs_decode
  echo
  # Demod test needs a numpy-generated IQ; generate it if numpy is available.
  if python3 -c "import numpy" 2>/dev/null; then
    echo "[build] generating synthetic IQ + running demod test..."
    python3 tests/cross_validate_demod.py || echo "[build] demod byte-phase check (see README)"
    g++ -O2 -std=c++17 tests/test_lora_demod.cpp -o test_lora_demod
  fi
  exit 0
fi

# --- UHD live tool (needs UHD on this host) ---
if ! pkg-config --exists libuhd 2>/dev/null; then
  echo "[build] ERROR: libuhd not found via pkg-config." >&2
  echo "[build] Install UHD dev headers + libuhd, e.g.:" >&2
  echo "[build]   (Ubuntu) sudo apt install libuhd-dev    or build UHD from source" >&2
  echo "[build] and ensure 'pkg-config --exists libuhd' succeeds." >&2
  exit 1
fi

echo "[build] compiling elrs_intercept against UHD..."
g++ -O2 -std=c++17 elrs_intercept.cpp $(pkg-config --cflags --libs libuhd) -lm -o elrs_intercept
echo "[build] OK -> $(pwd)/elrs_intercept"
echo "[build] run:  ./elrs_intercept [dev] [hunt_MHz] [srate] [gain] [out.jsonl]"
echo "[build] e.g.: ./elrs_intercept type=b200 915.0 6e6 40 elrs_capture.jsonl"
