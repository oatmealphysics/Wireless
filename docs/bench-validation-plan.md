# ELRS Bench-Validation Plan: E8 Controller ↔ TinyHawk III

**Pair under test:** EMAX E8 controller (TX) → EMAX TinyHawk III (RX)
**RX firmware:** Betaflight 4.4.2, SPI-integrated ELRS, OTA version 3
**Radio:** SX1280, ISM2400 domain (2400.4–2479.4 MHz, 80 channels)
**Air rate:** 150 Hz (SF7 / BW 812.5 kHz LoRa)
**Date:** 2026-10-16

This plan follows SESSION-NOTES §20 evidence gates 0–4 and AGENTS.md
constraints. No firmware flashing, settings writes, reset/bootloader
sequences, live fuzzing, or RF transmission may occur without separate
owner authorization at each gate.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Stage 1 — Passive Capture Setup](#2-stage-1--passive-capture-setup)
3. [Stage 2 — Capture Validation (PHY Confirmation)](#3-stage-2--capture-validation-phy-confirmation)
4. [Stage 3 — Frame Decode Validation](#4-stage-3--frame-decode-validation)
5. [Stage 4 — FHSS Sequence Validation](#5-stage-4--fhss-sequence-validation)
6. [Stage 5 — Nonce and Timing Validation](#6-stage-5--nonce-and-timing-validation)
7. [Stage 6 — Safety Gates Before Active RF](#7-stage-6--safety-gates-before-active-rf)
8. [Stage 7 — Active Test Protocol (Future Authorization Required)](#8-stage-7--active-test-protocol-future-authorization-required)
9. [Tool Reference](#9-tool-reference)
10. [Evidence Record Template](#10-evidence-record-template)

---

## 1. Prerequisites

### Hardware setup

| Item | Role | Notes |
|------|------|-------|
| EMAX E8 controller | TX — bound to TinyHawk III | Do **not** flash, reset, or write settings |
| EMAX TinyHawk III | RX — propellers removed, disarmed | Battery powered, sitting on bench |
| Ettus B205mini or B210 | SDR receiver (RX only) | UHD driver, RX2 antenna port |
| Ubuntu lab machine | Runs all capture/analysis tooling | Python 3.10+, NumPy, SciPy, SoapySDR |

### Software dependencies

```
python3 -m pip install numpy scipy soapysdr
```

UHD driver and `soapysdr-module-uhd` must be installed for live SDR
capture. All offline analysis tools require only NumPy.

### Private data policy

All raw IQ captures, decoded frames, UID files, and per-frame evidence go
under the repository's ignored `work/` directory. Never commit pairing UIDs,
raw packet bytes, or capture files to Git. Only aggregate counts and
pass/fail verdicts appear in tracked documents.

### Ground truth (Gate 0)

Before any RF work, confirm:

- [ ] TinyHawk III firmware version (Betaflight 4.4.2) via USB Configurator
- [ ] ELRS rate index, switch mode, and telemetry ratio from Configurator
- [ ] Private UID recorded under `work/private-uid.hex` (6 bytes, from
      binding phrase MD5 or read-only device config)
- [ ] E8 installed firmware version recorded (if identifiable from read-only
      serial probe; do not guess from related EMAX products)
- [ ] Each claim tagged: `device-observed`, `source-derived`, or `unknown`

---

## 2. Stage 1 — Passive Capture Setup

**Objective:** Record real ELRS traffic between the E8 and TinyHawk III
using the B205mini/B210 in receive-only mode.

### SDR configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Center frequency | 2441.0 MHz | Covers ELRS v3 sync channel 41 (2441.4 MHz nominal) with margin |
| Sample rate | 4.0 MS/s | Sufficient for 812.5 kHz BW LoRa; manageable file size |
| Bandwidth | 2.0 MHz | Covers the sync channel and immediate neighbors |
| Gain | 40 dB (adjust to avoid clipping) | Start at 40, reduce if samples saturate |
| Antenna port | RX2 | Receive only — never connect to TX/RX |
| Duration | 20 seconds minimum | Captures ~9 sync-channel revisits at the 2.133 s period |
| Output format | cf32le or sc16le | cf32le preferred for direct analysis |

### Capture procedure

1. Power on the TinyHawk III (battery) and E8 controller. Confirm the pair
   links (solid LED, telemetry visible on E8 screen if available).

2. Position the SDR antenna 1–3 meters from the pair. No need for close
   proximity at 2.4 GHz indoors.

3. Record using `elrs-sync-channel-record.py`:

   ```bash
   python3 tools/elrs-sync-channel-record.py \
     --sdr-device driver=uhd --antenna RX2 \
     --duration-seconds 30 \
     --output work/captures/e8_tinyhawk_bench_YYYYMMDD_ch41_cf32.iq
   ```

4. Verify the `.meta.txt` sidecar records actual center frequency, sample
   rate, gain, antenna, duration, and sample count.

5. Compute and record SHA-256 of the capture file:

   ```bash
   sha256sum work/captures/e8_tinyhawk_bench_*.iq >> work/captures/SHA256SUMS
   ```

### Wideband survey (optional, recommended)

For broader FHSS validation in Stage 4, capture at a higher sample rate
across more of the band:

| Parameter | Value |
|-----------|-------|
| Center frequency | 2440.0 MHz |
| Sample rate | 28.0 MS/s |
| Bandwidth | 28.0 MHz |
| Duration | 5–10 seconds |

This covers approximately channels 20–60 of the 80-channel FHSS sequence.

### Control capture

To confirm the captured signal comes from the E8–TinyHawk pair:

1. Record a "pair-off" capture: power down the TinyHawk III, record 10
   seconds with identical SDR settings.
2. The pair-off capture should show no LoRa bursts on the sync channel.
   If bursts persist, another 2.4 GHz LoRa device is nearby.

---

## 3. Stage 2 — Capture Validation (PHY Confirmation)

**Objective:** Verify captured frames are genuine ELRS v3 LoRa packets
with the expected physical-layer parameters.

### Source-predicted PHY for Betaflight 4.4.2 at 150 Hz

| Parameter | Expected value | Source reference |
|-----------|---------------|------------------|
| Modulation | LoRa (not FLRC) | `expresslrs_common.h` rate table, index for 150 Hz |
| Spreading factor | SF7 | Rate entry `SF7` |
| Bandwidth | 812.5 kHz | Rate entry `BW_0800` (SX1280 register = 812.5 kHz) |
| Coding rate | CR 4/8 long interleaving | SX1280 register value `0x07` |
| Preamble length | 12 symbols | `SX1280_LORA_PREAMBLE_LEN_12` |
| Sync word | 2 symbols (LoRa standard) | SX1280 sync word register |
| Packet length | 8 bytes (SYNC, RC), 13 bytes (TLM, MSP) | `ELRS_OTA_VERSION_ID == 3` frame sizes |
| Packet rate | 150 Hz (6.667 ms interval) | Rate entry `interval = 6667 μs` |

### Validation steps

1. Run the PHY scanner on the narrowband capture:

   ```bash
   python3 tools/elrs-phy-scan.py \
     --capture work/captures/e8_tinyhawk_bench_*.iq \
     --sample-rate 4000000 --center-hz 2441000000 \
     --channel-hz 2441378800 \
     --start-seconds 0 --duration-seconds 30 \
     --output work/phy-scan-bench.jsonl
   ```

2. Check the terminal summary for:
   - [ ] **Preamble consistency:** 12/12 chirps detected per burst
   - [ ] **Sync word offsets:** Both LoRa sync chirps at expected positions
   - [ ] **Burst spacing:** 4 packets per channel visit at ~6.67 ms intervals
   - [ ] **Revisit period:** Sync channel visits every ~2.133 seconds
         (80 channels × 4 packets × 6.667 ms = 2133 ms)

3. Compare against the pair-off control capture. The control should show
   0/12 preamble consistency on the same channel.

### PHY red flags (any of these blocks Stage 3)

- FLRC modulation instead of LoRa → wrong firmware version assumption
- Different spreading factor or bandwidth → wrong rate index
- No detectable bursts → SDR configuration issue or pair not linked
- Persistent bursts in pair-off capture → interference from another device

---

## 4. Stage 3 — Frame Decode Validation

**Objective:** Decode real SYNC packets and verify field structure, UID
fragment, and CRC against the v3 source model.

### Decoder prerequisite

The project currently extracts LoRa **symbols** but does not have a
validated SX1280 coding-rate `0x07` (4/8 long-interleaving) byte decoder.
Until this is resolved:

- Option A: Obtain a known-plaintext hardware test vector (e.g., SX1280
  eval board transmitting known bytes) and validate the LI deinterleaver.
- Option B: Use the companion `elrs-sdr` repo's decoder if it implements
  the `0x07` path, and cross-validate against captured symbols.
- Option C: Brute-force short frames — with only 8 payload bytes and a
  known CRC polynomial, try candidate deinterleavings against CRC14.

**Gate:** At least one captured frame must pass CRC14 validation with the
source-derived seed before any claims about decoded content.

### Once bytes are available

1. Run the v3 frame checker on decoded candidates:

   ```bash
   python3 tools/elrs-v3-frame-check.py \
     --input work/decoded-candidates.jsonl \
     --uid-file work/private-uid.hex
   ```

2. For each SYNC packet, verify:
   - [ ] **Packet type field** = SYNC (type bits in header)
   - [ ] **UID[3..5] fragment** matches the private UID's bytes 3–5
   - [ ] **FHSS index** is consistent with channel 41 position in sequence
   - [ ] **Rate index** matches 150 Hz configuration
   - [ ] **Switch mode** matches Configurator-observed setting
   - [ ] **Telemetry ratio** matches Configurator-observed setting
   - [ ] **CRC14 passes** with seed `((UID[4] << 8) | UID[5]) ^ 3`

3. For RC data packets (if decodable):
   - [ ] **CRC14 passes** with the bound-link initializer
   - [ ] **Nonce field** present and incrementing (see Stage 5)
   - [ ] Channel values in plausible range for stick positions

### Evidence standard

- A frame that passes CRC14 with the independently obtained UID seed is
  strong evidence of correct decoding and pair attribution.
- A frame that passes CRC14 with a self-derived seed (from its own UID
  suffix) is weaker — it confirms internal consistency but not independent
  identity. Label it `self_derived_sync_suffix` in the evidence record.
- Multiple frames from different channel visits passing CRC with the same
  UID seed constitutes a validated decode.

---

## 5. Stage 4 — FHSS Sequence Validation

**Objective:** Observe the hopping sequence across multiple packets and
compare against the source-predicted 240-entry hop table.

### Source-derived FHSS model (Betaflight 4.4.2)

- FHSS sequence: 240 entries for the 80-channel ISM2400 domain
- Generator: `fhssGenSequence()` in `expresslrs_common.c`, seeded by UID
- Frequency map: 2400.4 MHz + (channel × 1.0 MHz)
- Sync channel: Index 41 (2441.4 MHz nominal), visited every 80 hops
  (every 4 × 80 = 320 packets at 150 Hz = 2.133 seconds)
- Hop interval at 150 Hz: every 4 packets (26.67 ms per hop)

### Validation steps

1. From the wideband capture, extract PHY candidates on multiple channels
   using `elrs-phy-scan.py` with different `--channel-hz` values.

2. Run the FHSS cross-checker with the private UID:

   ```bash
   python3 tools/elrs-v3-fhss-check.py \
     --input work/decoded-candidates.jsonl \
     --uid-file work/private-uid.hex
   ```

3. For timing-based FHSS validation (does not require decoded bytes):

   ```bash
   python3 tools/elrs-v3-hop-timing-check.py \
     --uid-file work/private-uid.hex \
     --sync-candidates work/auto-phy-ch41.jsonl \
     --channel 40=work/auto-phy-ch40.jsonl \
     --channel 42=work/auto-phy-ch42.jsonl \
     --duration-seconds 30
   ```

4. Verify:
   - [ ] **Sync channel visits** occur at the predicted FHSS positions
   - [ ] **Channel frequencies** of observed bursts match the UID-derived
         hop table within ±100 kHz tolerance
   - [ ] **Hop order** across consecutive visits matches the source-predicted
         240-entry sequence
   - [ ] **Revisit period** (2.133 s for sync, 26.67 ms per hop) matches
         within timing measurement precision

### What this proves

FHSS match confirms the pair is using the expected UID-derived hop sequence.
Combined with CRC validation from Stage 3, this establishes that the captured
traffic is from the E8–TinyHawk pair operating with the known binding phrase.

---

## 6. Stage 5 — Nonce and Timing Validation

**Objective:** Verify nonce increment behavior, packet rate, and telemetry
ratio against source expectations.

### Source-predicted behavior (150 Hz, v3)

| Parameter | Expected | Source |
|-----------|----------|--------|
| Nonce width | 8 bits (wraps at 256) | `expresslrs.c` nonce handling |
| Nonce increment | +1 per packet | Sequential, no skip |
| Packet interval | 6.667 ms ± jitter | 150 Hz rate entry |
| Telemetry ratio | 1:n (Configurator-observed) | TLM ratio from SYNC packet |
| TLM packet timing | Every n-th packet slot | RX sends TLM instead of TX sending RC |

### Validation steps

1. From a sequence of decoded frames on a single channel visit (4 packets):
   - [ ] **Nonce values** increment by 1 across consecutive packets
   - [ ] **Packet timestamps** are spaced ~6.667 ms apart (±0.5 ms)

2. Across channel visits on the sync channel:
   - [ ] **Nonce progression** is consistent with 150 Hz rate between visits
         (expect ~320 nonce increments per 2.133 s sync revisit)
   - [ ] **Nonce wraps** cleanly at 256 with no resets or jumps

3. If telemetry frames are captured:
   - [ ] **TLM ratio** matches the SYNC-reported value
   - [ ] **TLM packets** are 13 bytes (vs 8 for RC/SYNC)

### CRC with nonce (SM_WIDE RC frames only)

For `SM_WIDE` switch mode at 150 Hz, the CRC calculation replaces `crcHigh`
with `(nonce % 4) + 1`. Verify that RC frames pass CRC only when this
nonce substitution is applied:

- [ ] RC frame fails CRC14 with standard calculation
- [ ] RC frame passes CRC14 with `crcHigh = (nonce % fhssHopInterval) + 1`
- [ ] This behavior is consistent across multiple RC frames with different nonces

---

## 7. Stage 6 — Safety Gates Before Active RF

**No RF transmission may occur until ALL of the following are demonstrated
from passive captures of the real E8–TinyHawk pair.**

### Mandatory evidence checklist

| # | Gate | Evidence required | Status |
|---|------|-------------------|--------|
| G1 | PHY match | SF7/BW812.5kHz LoRa confirmed in ≥2 independent captures | ☐ |
| G2 | Frame decode | ≥5 SYNC packets pass CRC14 with independent UID seed | ☐ |
| G3 | UID confirmed | UID[3..5] from decoded SYNCs matches private UID file | ☐ |
| G4 | FHSS match | Observed hop sequence matches UID-derived table on ≥10 channels | ☐ |
| G5 | Nonce model | Nonce increments and CRC-nonce interaction match source | ☐ |
| G6 | Timing match | Packet rate, hop timing, and sync revisit within 5% of predicted | ☐ |
| G7 | Control capture | Pair-off recording confirms bursts are from this pair | ☐ |
| G8 | Byte decoder | A validated LI deinterleaver produces bytes that pass CRC | ☐ |
| G9 | Owner approval | Written authorization from owner for active RF testing | ☐ |

### What blocks active RF

- **Any gate failed or incomplete** → no transmission
- **Synthetic-only evidence** (self-tests, round-trip of own packets) → does
  not satisfy any gate
- **Source-derived predictions alone** (without confirming captures) → does
  not satisfy any gate
- **Decoded frames from only one capture session** → G1 requires two
  independent sessions to rule out capture artifacts

### Review process

Before requesting owner authorization (G9):

1. Write a summary document listing each gate with its evidence file paths
   (under `work/`), pass/fail result, and any caveats.
2. Have the summary reviewed by a second agent or operator.
3. Present to the owner with a clear statement of what active testing will
   do and what its risks are.

---

## 8. Stage 7 — Active Test Protocol (Future Authorization Required)

**This section describes a protocol for future use. It is NOT authorization
to transmit. Owner must separately approve before any step here is executed.**

### Physical setup

- TinyHawk III on bench, **propellers removed**, battery connected, disarmed
- E8 controller powered on and linked to the TinyHawk
- SDR configured for TX on a second device (or TX/RX on B210)
- Monitoring: Betaflight Configurator USB connected to TinyHawk for
  real-time link quality / RSSI / failsafe status observation
- **Kill switch:** Physical disconnect (battery pull) within arm's reach;
  alternatively, E8 controller can be powered off to verify the TinyHawk
  enters failsafe

### Test 1: Single SYNC injection

**Goal:** Determine if the TinyHawk receiver accepts a forged SYNC packet.

1. Generate a forged SYNC packet using `elrs-packet-forger.py` with:
   - The verified UID
   - Correct v3 CRC14 seed and calculation
   - FHSS index pointing to the current sync channel
   - Rate, switch mode, and TLM ratio matching the live link

2. Transmit a **single** SYNC packet on channel 41 (2441.4 MHz) at low
   power, timed to arrive during a sync-channel visit window.

3. Observe on Configurator:
   - **RSSI/LQ change:** A sudden RSSI spike or LQ drop may indicate the
     receiver processed the packet
   - **No change:** The receiver may have rejected it (wrong timing, CRC
     fail, or anti-replay protection)

4. Record: TX power, timing, observed RSSI/LQ before and after, failsafe
   state.

### Test 2: SYNC flood on sync channel

**Goal:** Determine if repeated forged SYNCs cause link disruption.

1. Transmit forged SYNC packets continuously on channel 41 at the 150 Hz
   rate (every 6.667 ms) for 5 seconds.

2. Observe: Does the TinyHawk enter failsafe? Does LQ degrade? Does it
   re-acquire the legitimate E8 link after the flood stops?

3. **Abort condition:** If the TinyHawk arms or behaves unexpectedly,
   pull the battery immediately.

### Test 3: FHSS-following injection

**Goal:** Test whether following the hop sequence enables sustained link
capture.

1. Using the verified FHSS table, transmit forged packets that follow the
   hopping sequence, including SYNC on channel 41 and RC data on the
   predicted next channels.

2. Observe whether the TinyHawk locks to the forged signal (LQ rises on
   Configurator while E8 shows link loss).

3. **This is the most aggressive test.** It must not be attempted until
   Tests 1 and 2 are understood and the owner has reviewed results.

### Safety controls for all active tests

- [ ] Propellers physically removed before any RF test
- [ ] TinyHawk disarmed and confirmed disarmed via Configurator
- [ ] Battery pull accessible within 2 seconds
- [ ] SDR TX power set to minimum effective level
- [ ] Each test requires separate, logged operator confirmation (`--arm` flag)
- [ ] All transmit tools default to dry-run mode
- [ ] Test duration limited (5 s max for flood, single-shot for injection)
- [ ] Results recorded immediately; do not rely on memory

---

## 9. Tool Reference

| Tool | Stage | Purpose | Mode |
|------|-------|---------|------|
| `elrs-sync-channel-record.py` | 1 | Live SDR receive-only IQ recording on sync channel | RX only |
| `elrs-phy-scan.py` | 2 | Offline LoRa PHY candidate detection in IQ files | Offline |
| `elrs-iq-symbols.py` | 2 | Bounded LoRa symbol extraction and preamble verification | Offline |
| `elrs-capture-intake.py` | 1–2 | Capture metadata, alignment, and SHA-256 verification | Offline |
| `elrs-batch-evidence.py` | 2 | Multi-burst symbol quality assessment | Offline |
| `elrs-v3-frame-check.py` | 3 | CRC14 and field validation for pre-decoded v3 frames | Offline |
| `elrs-v3-fhss-check.py` | 4 | FHSS sequence cross-check against UID-derived hop table | Offline |
| `elrs-v3-hop-timing-check.py` | 4–5 | Timing and channel cross-check against hop plan | Offline |
| `elrs-uid-rainbow.py` | 0 | Binding phrase → UID rainbow table generator | Offline |
| `elrs-packet-forger.py` | 7 | Forged ELRS packet generation (**requires `--arm`)** | TX (gated) |
| `elrs-tx-emulator.py` | 7 | SDR-based ELRS TX emulation (**requires `--arm`)** | TX (gated) |

---

## 10. Evidence Record Template

For each validation stage, record the following under `work/bench-evidence/`:

```
Stage: [1-7]
Date: YYYY-MM-DD
Operator: [name]
Capture file: work/captures/[filename]
Capture SHA-256: [hash]
SDR settings: [device, freq, rate, gain, antenna, duration]
Pair state: [on/linked, off, one-side-off]

Results:
  - [metric]: [observed value] vs [predicted value] — [PASS/FAIL]
  - ...

Caveats:
  - [any qualifications on the evidence]

Files produced:
  - work/[path]: [description]
```

Each stage's evidence file should be self-contained: a future reviewer must
be able to reproduce the analysis from the capture file and the commands
listed.

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-10-16 | Claude | Initial plan, following SESSION-NOTES §20 gates |
