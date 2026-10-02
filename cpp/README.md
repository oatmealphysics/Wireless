# cpp/ — ELRS interceptor (C++, UHD)

Passive ELRS interceptor for the B205mini, built against the **proven 4-phase
model** from `LoRa-ELRS-Interceptor-Decoder` (hunt SYNC → 256-guess UID byte →
hop-lock → dewhiten/decode), adapted from an ESP32+LR1121 radio to a UHD
software capture.

## What's proven here vs. what isn't

| Piece | Status |
|---|---|
| CRC-8/CCITT, OTA detect, UID extract | ✅ unit-tested (`tests/test_elrs_decode.cpp`, 18 checks) |
| Fisher-Yates hop table + `freq_at` | ✅ unit-tested (deterministic, permutation, 256 distinct tables) |
| dewhiten (LFSR 0x69) + channel unpack + frame map | ✅ unit-tested (round-trips 16×11-bit channels) |
| 256-guess uniqueness (full table) | ✅ unit-tested |
| **Software LoRa demod** (`lora_demod_cpp.h`) | ✅ **CPU-verified at the symbol level** — recovers payload symbols from numpy-generated IQ. ⚠️ byte-phase alignment + real-RF behavior NOT yet proven (the CRC stage is the arbiter on live data). |
| UHD capture loop + 4-phase state machine | ⚠️ written, **needs Borealis to build/run** |

**Honest state of the demod:** it is a faithful port of the proven numpy
reference (`tools/lora_ref.py`) — downchirp → FFT → argmax per symbol, LSB-first
symbol→bit. Verified to recover the correct payload *symbols* from a synthetic
frame. What is **not** proven: (a) the exact byte-phase (which symbol the first
byte starts on) and (b) behavior on real RF (CFO, AGC, noise). Both are
resolved live: a misaligned window fails CRC-8 and is dropped, the aligned one
passes — that is the built-in gate. So the first live run may need a small
preamble-offset tweak in `lora_demod_cpp.h` (`best_off + 11*N`); the IQ dump
makes that tunable without another round-trip.

**Critical caveat (from the decode tests):** a single hop frequency is
*ambiguous* (~28 of 256 UID-byte guesses map to the same freq at one index).
The reference confirms the seed by observing a **real CRC-valid RC packet** on
the predicted freq — freq-match alone is not sufficient. The LOCK phase must do
the same (currently the HUNT→EXPLOIT→LOCK transitions are wired but the
real-time hop-timing loop in LOCK is a stub to complete).

## Build & test (on Borealis, where UHD lives)

```bash
# CPU-only decode test — no UHD, runs on any g++ host (including this enclave):
./build.sh test

# Full UHD tool — needs UHD dev headers + libuhd:
./build.sh            # -> ./elrs_intercept
./elrs_intercept type=b200 915.0 6e6 40 elrs_capture.jsonl
```

If UHD isn't visible to `pkg-config`, install `libuhd-dev` (or build UHD from
source) first.

## Files
- `elrs_decode.h` — UHD-independent decode logic (constants, CRC, hop table,
  dewhiten, unpack, frame map). Shared by the tool and the unit test.
- `lora_demod_cpp.h` — UHD-independent software LoRa demod (downchirp → FFT →
  argmax → LSB-first symbol→bit). CPU-verified at the symbol level.
- `elrs_intercept.cpp` — UHD capture + 4-phase state machine + JSONL output.
- `tests/test_elrs_decode.cpp` — CPU-only decode unit test (18 checks).
- `tests/test_lora_demod.cpp` — CPU-only demod test (numpy IQ → C++ demod).
- `tests/demod_cli.cpp` — reads a .iq file, runs the demod, dumps JSON.
- `tests/cross_validate_demod.py` — generates numpy IQ, runs the C++ demod.
- `build.sh` — builds the tool (UHD) or runs the CPU-only tests (`test`).

## First live-test plan (cheap, no decode risk)
1. `./build.sh test` — confirm the decode logic passes on the build host.
2. `./elrs_intercept ...` with the demod stub → it **dumps raw IQ** to
   `elrs_capture.jsonl.iq`. Confirm you *see* LoRa energy (spectrum) at the
   hunt frequency. This proves the radio path without depending on the demod.
3. Drop in the validated software LoRa demod, re-run, and watch for
   `event:"sync_hunt"` then `event:"rc"` lines in the JSONL.
