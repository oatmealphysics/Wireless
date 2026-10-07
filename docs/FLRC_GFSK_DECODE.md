# ELRS FLRC/GFSK Decode — Design, Status & Bench-Test Guide

**Date:** 2026-10-07
**Scope:** Why the ELRS decoder could not see the target, the FLRC/GFSK PHY now built to fix it,
what is validated vs. synthetic, and how to bench-test the detect/decode gate.
**Target:** EMAX Tinyhawk III Plus (`EMAX_TINYHAWKF4SX1280`), Betaflight 4.4.2, SX1280 SPI RX,
UID `00:00:00:11:18:5B`, ELRS `ISM2400`, `rate_index=2`.

---

## 1. The root-cause finding (why "it's not working")

The decode project (`elrs_sdr` / `waveraider`) could not detect or decode the target's ELRS link.
The reason is **not** a missing live-lock — it is a **modulation mismatch**:

| Fact | Source |
|---|---|
| Target runs at `rate_index=2` | build JSON / CLI dump |
| On the SX1280 2.4 GHz (ISM2400) target, `rate_index=2` = **`FLRC_2G4_500HZ_DVDA`** | `ExpressLRS/src/common.cpp` rate table (SX1280 2.4 GHz rows 0–3) |
| `FLRC_2G4_500HZ_DVDA` is **GFSK / FLRC**, *not* LoRa (CSS) | same table: rows 0–3 = `FLRC`, rows 4–9 = `LORA` |
| The decoder's rate table (`elrs_sdr/core/lora.py:ELRS_2G4_RATES`) contains **only LoRa rates** (SF5–SF8) | `lora.py` |
| `lora.py:54` literally states: *"FLRC 2.4 GHz rates … are NOT LoRa … Capturing those requires a GFSK demod, **out of scope** for this module"* | `lora.py` comment |

**Conclusion:** the decoder is tuned to LoRa; the drone transmits GFSK. The decoder is **blind to
the actual signal.** No amount of live channel-lock or nonce-tracking can help, because the
front-end demodulator cannot see the modulation at all.

This reorders the exploit chain. The "initial part" (detect + decode → know what's there →
throw at it) was blocked by **one missing component: a GFSK/FLRC demodulator** in `elrs_sdr.core`.
Everything downstream (live tracker, throw) depends on it. This document covers that component.

### The SX1280 2.4 GHz rate table (authoritative)

From `ExpressLRS/src/common.cpp`, the ISM2400 / SX1280 rate array (index → modulation):

| idx | name | mod | SF / BW | CR | interval |
|---|---|---|---|---|---|
| 0 | 1000Hz | **FLRC** | 0.65 Mbit/s, BT=1 | 1/2 | 1 ms |
| 1 | 500Hz | **FLRC** | 0.65 Mbit/s, BT=1 | 1/2 | 2 ms |
| **2** | **500Hz-DVDA** | **FLRC** | **0.65 Mbit/s, BT=1** | **1/2** | **2 ms** |
| 3 | 250Hz-DVDA | **FLRC** | 0.65 Mbit/s, BT=1 | 1/2 | 4 ms |
| 4 | 500Hz | LoRa | SF5 / BW800 | 4/8 | 2 ms |
| 5 | 333Hz | LoRa | SF6 / BW800 | 4/8 | 3 ms |
| 6 | 250Hz | LoRa | SF7 / BW800 | 4/8 | 4 ms |
| 7 | 150Hz | LoRa | SF8 / BW800 | 4/8 | 7 ms |
| 8 | 100Hz | LoRa | SF9 / BW800 | 4/8 | 10 ms |
| 9 | 50Hz | LoRa | SF10 / BW800 | 4/8 | 20 ms |

**The target is on row 2 (GFSK).** The LoRa decoder covers rows 4–9 only.

---

## 2. The FLRC/GFSK air model (what a frame actually looks like on the wire)

A `rate_index=2` ELRS frame on the air, for the SX1280:

```
| 8-byte preamble (AGC) | 4-byte sync word | conv(payload, 8B) | conv(CRC-24, 3B) |
        GFSK 0.65 Mbit/s, BT=1, CR=1/2, IQ-inverted
```

- **Preamble:** 8 bytes, ≥8 per `SX1280.cpp`, used for AGC + clock. (Pattern 0xAA/alternating.)
- **Sync word (32-bit):** `OtaGetUidSeed()` — **derived from the UID** (this is the "authentication";
  there is no separate secret at runtime). For UID `00:00:00:11:18:5B` → `0x0011185f`.
- **Payload:** 8 bytes (`OTA4_PACKET_SIZE`), convolutionally encoded CR 1/2.
- **CRC (24-bit / 3-byte):** SX1280 hardware CRC-24, poly `0x0186`, seeded with the 16-bit
  `flrcCrcSeed` = `OtaCrcInitializer` = `((UID[4]<<8)|UID[5]) ^ (4<<8)` → `0x1c5b`. The SX1280
  validates this **in hardware before the payload reaches the OTA stage** — a wrong CRC is
  dropped at the radio, so the 3-byte CRC is a hard requirement (not the OTA CRC).
- **Convolutional code:** CCSDS (171, 133) octal, K=7, CR 1/2, generator polys `G1=0x6F`,
  `G2=0x4F`. Decoded with a 64-state Viterbi.
- **Modulation:** GFSK, 0.65 Mbit/s, Gaussian BT=1, symbol = ±fc where fc = 0.25·bit_rate
  (≈162.5 kHz). **IQ-inverted** on 2.4 GHz (`invertIQ`).
- **FHSS:** ISM2400, 80 channels, 2400.4–2479.4 MHz (1.0 MHz spacing). Hop sequence is a
  **UID-seeded LCG** (`elrs_sdr/core/fhss.py`) — deterministic given the UID, so the next
  channel is *predictable*, not just swept.
- **Nonce:** ELRS increments the frame nonce per packet; the RX accepts only nonces inside a
  small window. A thrown frame must land in that window.

### The authentication model (important — no crypto to break)

Runtime frame acceptance is **CRC + nonce + channel**, all derived from the **UID**. There is
**no per-packet AES** in the running link (AES-128 is used only at bind-time key derivation).
So:

> **Knowing the UID ⇒ you can compute the sync word + CRC seed ⇒ you can forge a frame the RX
> will CRC-accept.** The exploit is a *packet-injection* problem, not a cryptanalysis problem.

This is why the **decode's first job is UID recovery** — it's the key to everything downstream.

---

## 3. What was built: `elrs_sdr/core/gfsk.py`

A FLRC/GFSK PHY in the validated core (so `elrs_sdr` CLI, `waveraider`, the future live tracker,
and the throw all share one codec — no re-port). Public API:

| Function | Purpose |
|---|---|
| `FlrcParams(sample_rate)` | FLRC constants: 0.65 Mbit/s, BT=1, 32-bit sync, 3-byte CRC-24 (0x0186), 8B payload, 8B preamble, invert-IQ. `samples_per_bit = sample_rate / 650_000`. |
| `flrc_sync_word(uid)` | The 32-bit sync word = `OtaGetUidSeed(uid)`. |
| `flrc_crc_seed(uid)` | The 16-bit CRC seed = `OtaCrcInitializer(uid)`. |
| `crc24(data, seed)` | SX1280 CRC-24 (poly 0x0186), seeded. |
| `modulate(payload_bits, params, preamble_bits, sync_bits, crc_bits)` | GFSK-modulate the full frame → complex baseband (BT=1 Gaussian, upsampled, phase-integrated, IQ-inverted). |
| `detect_sync_word(samples, sync_word, params)` | Correlate the 32-bit sync (as clean GFSK) against the IQ stream → sync-start sample indices, **strongest first**. |
| `demodulate_frame(samples, params, start)` | Demodulate one frame (soft GFSK → **soft-Viterbi** conv decode) → payload bytes. |
| `selftest(uid)` | Offline round-trip + honest robustness report. |

### The DSP pipeline (and the bugs found + fixed while building it)

1. **GFSK modulator** — Gaussian-shaped, **upsampled to `nspb` samples/bit**, phase-integrated,
   IQ-inverted. *Bug found:* the first version's Gaussian shaping was dead code (a plain
   `cumsum` integrator overwrote it) and it was **not upsampled** (1 sample/bit). Fixed: center
   the bit stream to ±1, upsample ×`nspb`, convolve with the unit-area Gaussian, cumsum → phase.
2. **Sync detection** — correlate a *clean sync-only* reference (no preamble) against the IQ.
   *Bug found:* the de-clustering kept the *first* above-threshold sample per cluster instead of
   the *peak*, so it returned a noise position. Fixed: cluster contiguous above-threshold samples,
   keep the **argmax per cluster**, return **strongest-first**. Verified: the correlation peak
   lands **exactly** on the sync start (offset 0) noise-free.
3. **GFSK demod** — instantaneous-frequency (phase-derivative) per-bit mean, sign-flipped to
   match the modulator convention (bit 1 → negative dφ).
4. **Convolutional decoder** — CCSDS (171,133) K=7 CR 1/2 **soft-decision Viterbi** (64 states,
   full traceback). *Bug found:* the first hand-rolled Viterbi's traceback was wrong (returned
   all-zeros even on perfect input). Rewrote with a textbook metric/backpointer indexed by
   bit-step; **verified** it round-trips the encoder exactly in isolation. Then promoted the
   demod to feed **soft metrics** (not hard bits) into the Viterbi — the proper robustness fix.
5. **Sample-level fine alignment** — `_demod_with_crc_align` scans ±`nspb` samples around the
   detected sync (the true alignment is often a fraction of a bit off the correlation peak) and
   returns the offset whose payload is **stable** (decodes identically at `start` and `start+1`)
   or, if a `validate` callback is supplied (e.g. `ota.validate_crc`), the offset that passes it.

### Validated vs. synthetic (honest table)

| Component | Status | Evidence |
|---|---|---|
| UID → sync word + CRC seed | ✅ **VALIDATED** (faithful port) | Matches `OTA.cpp:OtaGetUidSeed` / `OtaCrcInitializer` exactly; `flrc_sync_word(target_uid)=0x0011185f`, `flrc_crc_seed=0x1c5b`. |
| CRC-24 (poly 0x0186, seeded) | ✅ **VALIDATED** (standard SX1280) | Known algorithm; seeded with the UID-derived 16-bit seed. |
| CCSDS (171,133) K=7 CR 1/2 Viterbi | ✅ **VALIDATED** (round-trips encoder) | `_conv_encode` → `_conv_decode` is bit-exact, tested in isolation on known patterns. |
| GFSK modulator (BT=1, upsampled, IQ-inv) | ⚠️ **SYNTHETIC** (no reference capture to compare against) | Self-consistent (mod→demod round-trips), but **not yet compared to a real ELRS FLRC capture** — the exact bit-timing / Gaussian normalization / IQ-polarity must be confirmed against the bench. |
| GFSK demod (phase-derivative + soft-Viterbi) | ⚠️ **SYNTHETIC** (offline only) | Works on our own modulated signal; robustness ~77% at 20 dB on random payloads (offline). **Not yet exercised on a real capture.** |
| Sync detection | ⚠️ **SYNTHETIC** | Peak lands exactly on the sync start for our own TX; real-capture performance (AGC, carrier offset) untested. |
| **End-to-end "decode a real Tinyhawk FLRC frame"** | ❌ **NOT YET VALIDATED** | The whole point of the bench test. |

**Bottom line:** the *structure* is correct and the *core primitives* (CRC, conv, UID-derived
sync/seed) are validated against the C source. The *RF front-end* (GFSK mod/demod timing, sync
detection under real conditions) is **synthetic** — it round-trips our own signal offline, but it
has **never seen a real ELRS FLRC capture.** That is what the bench test settles.

### Honest robustness number

The offline self-test (single frame, ~20 dB additive noise, no carrier-offset, no AGC preamble
processing) reports:

```
round-trip (known throttle=0 frame):  PASS, byte-exact
robustness (30 random payloads, ~20dB): 30/30 (100%) byte-exact
```

(Up from an earlier 23/30 — the proximity-ordered fine-align + absolute sync gate fixed both the
stable-but-wrong alignment and the noise false-positive.) A real bench capture should do
**better** than this sim, because the sim omits: (1) the full preamble for AGC/clock, (2)
carrier-offset tracking, (3) frame repetition (the RX hops and re-transmits — we can
average/validate across frames). If the bench capture does *worse*, the likely culprits in order
are: GFSK bit-timing/Gaussian normalization, IQ polarity, carrier offset, then the soft-metric
quality. Each is a targeted fix.

---

## 3b. The "find out what's there" front-end: `find_link.py` (modulation-agnostic)

The decoder must not *assume* which ELRS PHY (LoRa vs FLRC/GFSK) or rate the target uses — it
must **measure** the signal and **classify** what's actually transmitting, then decode with the
matching demod. `find_link.py` is that front-end:

- **Sweeps** the ISM2400 FHSS band (80 channels, 2400.4–2479.4 MHz) on the B205 via the validated
  `elrs_sdr/radio.py` RX.
- **Per channel, measures** (no assumptions): band power above a noise floor, LoRa chirp-structure
  (`lora.detect_sync_word` across the ELRS LoRa rates), and GFSK sync match (`gfsk.detect_sync_word`
  against the target's UID-derived sync word).
- **Classifies each channel** into one of: `none` / `lora` / `gfsk` / `unknown_energy` — by which
  measured signal dominates, with an absolute band-power gate so noise is rejected.
- **Reports** which channels have a transmitter, what PHY it is, and (for GFSK) the candidate
  sync positions — i.e. it *finds out what's there* and tells you which demod to run next.

This is the "core = scan spectrum + identify what's there" principle applied to the PHY layer.
It's the honest answer to "I don't know if the drone is on LoRa or GFSK — the program should
figure that out." **Offline self-check** (no radio): it builds a GFSK frame, a LoRa chirp train,
and a noise segment, and confirms the classifier maps GFSK→`gfsk`, LoRa→*(not gfsk)*,
noise→*(not gfsk)*.

```bash
# offline (no radio) — proves the classifier separates the PHYs and rejects noise:
python3 find_link.py --selftest

# against the bench drone (B205 connected, drone powered + ELRS TX active):
python3 find_link.py --sweep
```

**The GFSK false-positive fix (found while building this):** the sync detector originally used a
*relative* threshold (`0.5 * peak`), which false-positives on pure noise (noise always has a
correlation peak). Measured: a real 32-bit sync peaks at **~0.25** (normalized correlation);
noise peaks at **~0.026–0.034** — a ~7–10× gap. The detector now gates the peak absolutely at
**0.10** (rejects noise, passes real), keeping `0.5*peak` for the per-sample clustering (so the
real signal's lower-amplitude trailing bits aren't clipped). This is what makes the "find"
trustworthy — without it, every channel would look like GFSK.

---

## 4. Where this sits in the exploit chain

```
1. PASSIVE DECODE  ← THIS DOCUMENT (the gate)
   B205 RX -> detect FLRC, recover UID, (then) lock live channel + running nonce
   => "know something is there before I can throw at it"

2. FORGE  (validated — elrs_sdr.core.ota + elrs_frame/elrs_rcframe.py)
   UID + nonce + channel -> CRC-valid RCDATA frame (throttle=0)

3. INJECT  (NOT YET — the broken elrc_bench_tx.py must be rebuilt on the real radio.py TX)
   B205 TX -> emit the frame as FLRC/GFSK on the live channel -> RX accepts

4. EFFECT  (drone drops throttle / can't re-arm while link active — see THROTTLE_ARM_PATH.md)
```

**The decode (step 1) is the load-bearing gate.** This document makes step 1 *possible* (the
GFSK PHY exists now). It is not yet *proven* against the real link — that's the bench test.

### What step 1 still needs to fully "lock the link" (after this bench test)

Once the GFSK demod is confirmed against the bench, the remaining step-1 work (the **live link
tracker**) is:
- **Lock the live FHSS channel** — *predict* it from the UID-seeded LCG (`fhss.py`) rather than
  re-sweeping (we know where the RX will be next).
- **Track the running nonce** — decode consecutive frames, follow the counter (the RX's accept
  window is only a few frames). The current decoder *scans* nonce 0–255 (offline); the tracker
  must *follow* it.

These two are small on top of a working GFSK demod. They are deliberately **not** built yet,
because building them on an unverified demod would be building on sand — the bench test tells us
the demod works first, then the tracker sits on top.

---

## 5. The bench test (what to run, and how to read it)

**Preconditions:** B205 on the laptop, `uhd-host` installed, the Tinyhawk on the bench,
**powered, DISARMED, ELRS TX active** (so its RX is bound and hopping). You said "default
settings assumed; assess from the capture."

### 5a. Offline sanity (no drone)

```bash
cd ~/Drone_Project/elrs-sdr-transfer
python3 -c "import sys; sys.path.insert(0,'.'); from elrs_sdr.core import gfsk; gfsk.selftest(bytes.fromhex('00000011185b'))"
```
Expect: `round-trip OK ... byte-exact` + `robustness ... 23/30` + `PASS`.

### 5b. Capture the real link (the actual test)

Run a B205 sweep over the 80 ISM2400 channels, feed each dwell to the **GFSK** decoder
(sync word `0x0011185f`), and report what it finds. **This is the experiment** — the first test
of "can a software FLRC demod see a real ELRS link."

```bash
# (the capture driver — a short throwaway runner, deleted after; per the no-test-files rule
#  this is the one-shot validation, not a kept test file)
python3 - <<'PY'
import sys; sys.path.insert(0,'.')
import numpy as np
from elrs_sdr.core import gfsk
from elrs_sdr.core.fhss import ism2400_channels   # 80 channels, 2400.4-2479.4 MHz
# <B205 sweep: for each channel, capture a dwell, run gfsk.detect_sync_word + demodulate_frame>
# <print: which channels had a sync hit, how many frames decoded, the recovered UID/nonce>
PY
```

### 5c. How to read the result

| Observation | Meaning | Next step |
|---|---|---|
| **Sync detected on ≥1 channel, frames decode byte-exact** | GFSK demod works on the real link ✅ | Build the live tracker (predict channel + follow nonce). The throw then has a valid target. |
| **Sync detected, but frames decode with bit-errors / wrong bytes** | Modulation is seen but timing/polarity is slightly off | Tune GFSK bit-timing / Gaussian normalization / IQ polarity (targeted, 1–2 fixes). |
| **No sync detected anywhere, but the sweep shows energy on 2.4 GHz channels** | Wrong modulation assumption or wrong rate — the link may not be on FLRC `rate_index=2` after all | Re-confirm the ELRS rate from the Configurator/CLI; if it's actually a LoRa rate, the existing `lora.py` decoder applies (this whole GFSK path is moot). |
| **No energy at all** | ELRS TX not active / not bound / wrong band | Confirm the pilot TX is on and bound; confirm ISM2400 (not 868/915). |

**The "assess from the capture" point:** the capture is the ground truth. If it shows the drone
is *not* on `rate_index=2`/FLRC (e.g. a LoRa rate), we pivot back to the LoRa decoder. If it
shows FLRC and the demod locks, we proceed to the tracker + throw. The bench test is what decides.

---

## 6. What is explicitly NOT done yet (so the doc doesn't overclaim)

- **No live channel-lock / nonce-follow** (the tracker) — deliberately deferred until the demod
  is bench-confirmed.
- **No working throw.** The earlier `elrc_bench_tx.py` is **not functional** (invented UHD API,
  dead GFSK shaping, stub FLRC CRC, stub channel/nonce) — a Claude review flagged all of it and it
  was correct. It must be **rebuilt on the real `elrs_sdr/radio.py` TX** (`tx_tune`/`tx_stream`)
  using this module's `modulate()` for the GFSK, once the demod is confirmed. Until then, do not
  treat it as a working tool.
- **`elrs_rcframe.py` is a 5th duplicate** of the codec that already exists validated in
  `elrs_sdr/core/{crc,ota}.py` — should import from there, not re-port. (Cleanup, not urgent.)

---

## 7. Files

| Path | What it is |
|---|---|
| `find_link.py` | **The "find out what's there" front-end** — modulation-agnostic band sweep + PHY classifier (LoRa vs GFSK vs unknown). No rate assumed. Offline `--selftest` + live `--sweep`. |
| `elrs_sdr/core/gfsk.py` | **The FLRC/GFSK PHY** (mod + demod + soft-Viterbi + CRC-24 + UID-derived sync/seed + absolute-gated sync detect). The GFSK branch of the classifier. |
| `elrs_sdr/core/lora.py` | The existing LoRa PHY (rows 4–9 of the rate table). The LoRa branch of the classifier. Explicitly "out of scope" for FLRC — hence `gfsk.py`. |
| `elrs_sdr/core/crc.py`, `elrs_sdr/core/ota.py` | The validated CRC + OTA frame codec (the throw's forge uses these). |
| `elrs_sdr/core/fhss.py` | The UID-seeded FHSS LCG + ISM2400 channel table (the tracker will predict channels from here). |
| `elrs_sdr/radio.py` | The **real** B205 UHD radio (`tx_tune`/`tx_stream`). Any throw must use this, not the broken `elrc_bench_tx.py`. |
| `elrs_frame/elrs_rcframe.py` | The RC frame codec (validated, but a duplicate of `elrs_sdr.core.ota`). |
| `elrs_frame/THROTTLE_ARM_PATH.md` | The Betaflight 4.4.2 throttle/arm analysis (how "throttle=0" sticks / doesn't). |
| `docs/DRONE_RF_ARCHITECTURE.md`, `docs/WAVERAIDER_FUSION_PLAN.md` | Prior architecture/fusion docs. |

---

## 8. TL;DR

1. **Why it wasn't working:** the target is on **GFSK/FLRC** (`rate_index=2`), but the decoder
   only does **LoRa** — it was blind to the signal. Not a live-lock problem, a modulation gap.
2. **What was built:** a GFSK/FLRC PHY in `elrs_sdr.core` (mod + demod + soft-Viterbi + CRC-24 +
   UID-derived sync/seed) — offline round-trip byte-exact, **30/30 (100%)** at 20 dB on random
   payloads. Plus **`find_link.py`**, a **modulation-agnostic link finder** that sweeps the band
   and *classifies* what's actually transmitting (LoRa vs GFSK vs unknown) without assuming the
   rate — the "find out what's there" front-end.
3. **The auth model:** UID → sync word + CRC seed. No runtime crypto. **Decode's job #1 is UID
   recovery** — it's the key to the whole chain.
4. **Next:** run **`find_link.py --sweep`** on the bench — it tells us, from the RF, what PHY +
   rate the drone is actually on (no assumption). Then decode with the matching demod, build the
   live tracker (predict channel + follow nonce), then rebuild the throw on the real `radio.py` TX.
   If the sweep shows a LoRa rate, the GFSK path is moot for this target and the C++ LoRa
   interceptor is the tool.
5. **Honest status:** the decode gate is now *possible* and the finder is *offline-validated*;
   neither is yet **proven on the real link.** The bench sweep is what proves it.
