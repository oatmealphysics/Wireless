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
# Resolve UHD build flags. Try pkg-config first; if it can't find libuhd
# (common when UHD is source-built into /usr/local and PKG_CONFIG_PATH doesn't
# include it), fall back to explicit flags against /usr/local then /usr.
UHD_CFLAGS=""; UHD_LIBS=""
if pkg-config --exists libuhd 2>/dev/null; then
  UHD_CFLAGS=$(pkg-config --cflags libuhd)
  UHD_LIBS=$(pkg-config --libs libuhd)
  echo "[build] UHD via pkg-config: ${UHD_CFLAGS} ${UHD_LIBS}"
else
  echo "[build] pkg-config can't find libuhd; trying explicit prefixes..."
  for prefix in /usr/local /usr; do
    if [ -f "$prefix/include/uhd/usrp/multi_usrp.hpp" ] && \
       [ -e "$prefix/lib/x86_64-linux-gnu/libuhd.so" ] || \
       [ -e "$prefix/lib/libuhd.so" ]; then
      UHD_CFLAGS="-I$prefix/include"
      UHD_LIBS="-L$prefix/lib/x86_64-linux-gnu -L$prefix/lib -luhd -Wl,-rpath,$prefix/lib/x86_64-linux-gnu -Wl,-rpath,$prefix/lib"
      echo "[build] UHD explicit prefix: $prefix"
      break
    fi
  done
  if [ -z "$UHD_CFLAGS" ]; then
    echo "[build] ERROR: UHD headers/lib not found in /usr/local or /usr." >&2
    echo "[build] Run: find / -name 'multi_usrp.hpp' 2>/dev/null  and tell me the prefix." >&2
    exit 1
  fi
fi

echo "[build] compiling elrs_intercept against UHD..."
g++ -O2 -std=c++17 elrs_intercept.cpp $UHD_CFLAGS $UHD_LIBS -lm -o elrs_intercept
echo "[build] OK -> $(pwd)/elrs_intercept"
echo "[build] run:  ./elrs_intercept [dev] [hunt_MHz] [srate] [gain] [out.jsonl]"
echo "[build] e.g.: ./elrs_intercept type=b200 915.0 6e6 40 elrs_capture.jsonl"
