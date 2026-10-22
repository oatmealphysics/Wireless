# SX1280 LoRa PHY Transmission Feasibility for ELRS v3 Injection

**Date:** 2026-10-17
**Status:** Research — no RF transmission performed
**Target:** EMAX TinyHawk III Plus (Betaflight 4.4.2, SPI ELRS v3, SX1280)
**Lab SDRs:** Ettus B205mini, B210

---

## Executive Summary

Transmitting valid ELRS v3 LoRa frames requires generating SX1280-compatible
chirp-spread spectrum modulation with Semtech's proprietary long-interleaver
coding. The Ettus B205mini/B210 SDRs can generate arbitrary baseband waveforms
at the required 2.4 GHz center frequency and 812.5 kHz bandwidth, but the
LoRa PHY must be implemented entirely in software. This document evaluates
three transmission paths and recommends a hybrid approach.

**Bottom line:** A dedicated SX1280 dev board (e.g., an ELRS TX module) is the
lowest-risk path to valid LoRa transmission. Pure-SDR LoRa TX is theoretically
possible but has significant timing and modulation fidelity challenges. The
recommended approach uses the SDR for passive capture/analysis and an SX1280
module for actual packet injection.

---

## 1. ELRS v3 PHY Parameters (Source-Confirmed)

These parameters are confirmed against the Betaflight 4.4.2 source and
validated by passive SDR capture of the E8–TinyHawk pair.

| Parameter | Value | Source |
|-----------|-------|--------|
| Modulation | LoRa (chirp spread spectrum) | SX1280 register config |
| Center band | ISM 2.4 GHz (2400.4–2479.4 MHz) | `expresslrs_common.h` |
| Channel spacing | 1.0 MHz, 80 channels | FHSS domain config |
| Spreading factor | SF7 | Rate table, 150 Hz entry |
| Bandwidth | 812.5 kHz | `BW_0800` register value |
| Coding rate | CR 4/8 long interleaving | SX1280 register `0x07` |
| Preamble | 12 symbols | `SX1280_LORA_PREAMBLE_LEN_12` |
| Sync word | 2 symbols (standard LoRa) | SX1280 sync word register |
| Packet mode | Implicit header | SX1280 config |
| Payload | 8 bytes (SYNC/RC) or 13 bytes (TLM/MSP) | OTA v3 frame sizes |
| Packet rate | 150 Hz (6.667 ms interval) | Rate entry |
| FHSS hop interval | Every 4 packets (26.67 ms) | Source-confirmed |
| Sync channel | 41 (2441.4 MHz) | v3: `freq_count/2 + 1` |

### What makes SX1280 LoRa different from SX127x LoRa

The SX1280 operates at 2.4 GHz (vs sub-GHz for SX127x) and supports features
not available on the older chipset:

1. **Long interleaving (LI):** Register value `0x07` selects a proprietary
   interleaving scheme not documented in the public datasheet. The bit
   permutation was empirically measured by the WaveShark project using
   known-plaintext hardware tests. This is the single most challenging
   parameter for SDR-based generation.

2. **Higher bandwidth:** 812.5 kHz BW at 2.4 GHz produces shorter chirp
   symbols (~163 µs at SF7) than typical sub-GHz LoRa.

3. **Faster rates:** 150 Hz packet rate with 6.667 ms inter-packet interval
   requires tight timing.

---

## 2. Transmission Path Analysis

### Path A: Pure SDR (B205mini/B210)

**Concept:** Generate LoRa chirp waveforms entirely in software (GnuRadio or
Python/NumPy) and transmit via the Ettus SDR's DAC.

#### What the SDR can do

- **Frequency range:** B205mini covers 70 MHz–6 GHz; B210 covers 70 MHz–6 GHz.
  Both comfortably cover the 2.4 GHz ISM band.
- **TX bandwidth:** B205mini: up to 56 MHz instantaneous BW. B210: up to 56 MHz
  with 2 TX channels. 812.5 kHz LoRa BW is well within range.
- **Sample rate:** Up to 61.44 MS/s (B205mini) or 56 MS/s (B210). At 4 MS/s
  (sufficient for 812.5 kHz BW), USB throughput is manageable.
- **Duplex:** B210 supports full duplex (simultaneous TX/RX on separate chains).
  B205mini is half-duplex but can switch rapidly.
- **TX power:** B205mini: up to ~10 dBm at 2.4 GHz. B210: similar. Sufficient
  for bench testing at 1–3 meters.

#### LoRa chirp generation

A LoRa symbol is a frequency-chirp spanning the bandwidth:

```
f(t) = f_base + BW * ((symbol_value/2^SF + t/T_sym) mod 1)
```

Where:
- `BW` = 812500 Hz
- `SF` = 7 (128 possible symbol values, 0–127)
- `T_sym` = 2^SF / BW = 128 / 812500 = 157.538 µs
- Each symbol encodes SF=7 bits

Generating the baseband IQ:
```python
def lora_chirp(symbol, sf=7, bw=812500, sample_rate=4000000):
    n_sym = 2**sf  # 128
    t_sym = n_sym / bw  # ~157.5 µs
    n_samples = int(t_sym * sample_rate)
    t = np.arange(n_samples) / sample_rate
    # Instantaneous frequency offset from center
    f_inst = bw * (((symbol / n_sym) + t / t_sym) % 1.0 - 0.5)
    phase = 2 * np.pi * np.cumsum(f_inst) / sample_rate
    return np.exp(1j * phase)
```

Preamble chirps are symbol 0 (unmodulated up-chirp). The sync word uses two
specific symbol values. Data chirps carry the encoded payload.

#### Open-source LoRa SDR implementations

Several projects implement LoRa modulation/demodulation in software:

1. **gr-lora_sdr** (EPFL): GnuRadio OOT module for LoRa TX/RX. Implements
   standard chirp generation, whitening, Hamming coding, and interleaving.
   Targets SX127x (sub-GHz, standard interleaving). Does NOT implement
   SX1280 long interleaving.
   - GitHub: `tapparelj/gr-lora_sdr`
   - Validated against SX1276 hardware at sub-GHz
   - Would need modification for SX1280 LI coding

2. **WaveShark** (v0l): SX1280-specific LoRa decoder in Rust. Implements
   the empirically measured long-interleaver bit permutation for SF7/CR4-8-LI.
   **Decode only** — no TX/modulator. But the measured interleaver maps are
   the critical missing piece for generating valid coded symbols.
   - GitHub: `v0l/waveshark`
   - GPL-3.0-or-later
   - We successfully used its LI maps to decode 54/62 captured frames

3. **liquid-dsp**: Low-level DSP library with chirp generation primitives.
   No LoRa-specific framing.

4. **SX1280 datasheet (Rev 3.3)**: Documents register values but NOT the
   long-interleaver bit permutation. States `0x07` is "4/8 long interleaving"
   without specifying the algorithm.

#### Feasibility assessment for pure SDR TX

| Requirement | Feasible? | Notes |
|-------------|-----------|-------|
| Chirp generation | **Yes** | Standard DSP, well-understood |
| Preamble/sync word | **Yes** | Fixed symbol patterns |
| Standard FEC (Hamming 4/8) | **Yes** | gr-lora_sdr implements this |
| **Long interleaving** | **Possible** | WaveShark's measured maps can be inverted (decode → encode). We have the decode direction validated on real captures. |
| Whitening | **Yes** | Standard LoRa LFSR, documented |
| Gray coding | **Yes** | Standard, documented |
| Frequency accuracy | **Challenging** | SDR oscillator drift at 2.4 GHz may exceed SX1280 tolerance. B205mini TCXO is ±2 ppm = ±4.88 kHz at 2.4 GHz. SX1280 RX has ~±20% BW tolerance for frequency error. |
| Timing accuracy | **Challenging** | USB latency adds jitter. FHSS synchronization at 150 Hz (6.667 ms slots) requires <1 ms TX timing precision. |
| Phase continuity | **Moderate** | LoRa demodulation is frequency-based, somewhat tolerant of phase noise |

**Key risk: Long interleaver inversion.** We have WaveShark's measured
decode-direction LI maps. To transmit, we need the inverse (encode direction).
For a permutation, this is straightforward — if `decode[i] = j`, then
`encode[j] = i`. The FEC encoding (Hamming 4/8) is standard. The question is
whether the measured maps are complete and whether any undocumented scrambling
or state exists in the SX1280's encoder that the decode path doesn't reveal.

**Key risk: Timing.** ELRS FHSS hops every 26.67 ms. To inject a packet on the
correct channel at the correct time, the SDR must:
1. Know the current position in the 240-entry hop sequence
2. Tune to the correct frequency
3. Transmit within the ~6.667 ms packet window
4. Account for USB bus latency (typically 1–5 ms with UHD)

The B210's full-duplex capability helps: one chain monitors while the other
transmits. The B205mini would need rapid RX→TX switching.

**Verdict:** Pure SDR TX is the highest-effort, highest-risk path. Possible in
principle but requires:
- Inverting the WaveShark LI maps (moderate effort)
- Building a complete LoRa modulator (moderate effort, gr-lora_sdr as base)
- Solving USB timing jitter (hard for FHSS sync)
- Validating against real hardware (requires another SX1280 receiver)

### Path B: Dedicated SX1280 Dev Board / ELRS TX Module

**Concept:** Use an off-the-shelf SX1280-based board (standalone ELRS TX module,
SX1280 evaluation board, or LoRa dev board) controlled via SPI/UART from the
Ubuntu lab machine.

#### Available hardware options

1. **ELRS TX module (e.g., BetaFPV Nano TX, HappyModel ES24TX):**
   - ESP32 + SX1280, designed for ELRS
   - Can run custom firmware (ExpressLRS is open source)
   - SPI interface to SX1280 is accessible
   - ~$15–30, widely available
   - Can be flashed with modified ELRS that transmits attacker-controlled packets
   - **Risk:** The project's no-flash rule applies only to the OWNED E8 and
     TinyHawk. A separately purchased ELRS TX module can be flashed freely.

2. **Semtech SX1280 evaluation board (SX1280DVK1ZHP):**
   - Direct SX1280 access via SPI
   - Full register control, no firmware abstraction
   - ~$50–80 from distributors
   - Best for raw packet-level control

3. **Generic SX1280 breakout boards (e.g., EBYTE E28 modules):**
   - SX1280 + PA/LNA on a small module
   - SPI interface, needs an MCU or SPI adapter
   - ~$5–15
   - Requires writing bare-metal SX1280 driver code

4. **Raspberry Pi + SX1280 module (via SPI GPIO):**
   - Pi's SPI bus drives SX1280 directly
   - Low latency, no USB bus jitter
   - Precise timing via hardware interrupts
   - Ideal for FHSS synchronization

#### What the SX1280 handles natively

The SX1280 implements ALL of the following in hardware:
- LoRa chirp modulation with correct symbol timing
- Long interleaving (`0x07` coding rate)
- FEC encoding (Hamming family)
- Whitening
- CRC (though ELRS computes its own CRC14 in software)
- Precise frequency synthesis (±2 ppm with TCXO)
- Configurable TX power (-18 to +12.5 dBm)
- Sub-microsecond TX timing via DIO interrupt

The host only needs to:
1. Configure SX1280 registers (modulation params, frequency, power)
2. Write the 8 or 13 raw payload bytes to the TX FIFO
3. Issue the TX command
4. Handle FHSS frequency hopping in software

**This is exactly what ELRS TX firmware does.** The `elrs-tx-emulator.py` tool
in this repo already implements the packet construction, CRC, and FHSS logic —
it just lacks a real SX1280 backend.

#### Feasibility assessment

| Requirement | Feasible? | Notes |
|-------------|-----------|-------|
| LoRa modulation | **Native** | SX1280 hardware |
| Long interleaving | **Native** | SX1280 hardware (register `0x07`) |
| Frequency accuracy | **Native** | SX1280 TCXO, sub-ppm |
| Timing precision | **Excellent** | SPI latency ~µs, DIO interrupt |
| FHSS synchronization | **Good** | Software hop + SX1280 fast freq change (~100 µs) |
| TX power control | **Native** | -18 to +12.5 dBm |
| Packet construction | **Done** | `elrs-packet-forger.py`, `elrs-tx-emulator.py` |

**Verdict:** Lowest risk, fastest path to valid LoRa injection. Requires
purchasing a ~$15–30 ELRS TX module or ~$5–15 SX1280 breakout. The existing
`elrs-tx-emulator.py` needs only a real SPI backend (replacing the current
simulated/file backends).

### Path C: Hybrid SDR + SX1280

**Concept:** Use the B205mini/B210 for passive monitoring and FHSS
synchronization, and an SX1280 module for actual packet transmission.

#### Architecture

```
B205mini (RX only)          SX1280 module (TX)
    │                            │
    ├─ Passive capture           ├─ SPI from Raspberry Pi / USB adapter
    ├─ FHSS timing recovery      ├─ Receives timing sync from SDR pipeline
    ├─ Nonce tracking             ├─ Transmits forged packets
    └─ Link quality monitoring    └─ Hops frequency per FHSS schedule
```

The SDR provides real-time intelligence:
1. **FHSS synchronization:** Detect the target's current hop position by
   observing burst timing on known channels
2. **Nonce recovery:** Track the nonce counter from decoded SYNC packets
3. **Timing window:** Measure exact packet arrival times to find injection
   windows between legitimate packets
4. **Link quality:** Monitor whether injection is succeeding (link quality
   changes, telemetry anomalies)

The SX1280 handles the hard part — generating correct LoRa modulation with
precise timing.

**Verdict:** This is the recommended approach for CTF. It combines the
SDR's monitoring capability with the SX1280's native LoRa generation.

---

## 3. SX1280 Long Interleaver: Encode Direction

For Path A (pure SDR), we need the encode-direction LI transform. Here's the
analysis:

### Decode direction (known, validated)

WaveShark provides empirically measured decode maps for SF7/CR4-8-LI:
- 8 symbols × 5 coded bits + 13 symbols × 7 coded bits = 131 bit slots
- 128 coded bits carry 64 data bits (16 nibbles × 4 data bits, each with
  4 parity bits via Hamming(8,4))
- The remaining 3 slots are fixed/padding

The decode pipeline:
```
RF symbols → Gray decode → de-interleave (LI map) → de-whiten → FEC decode → payload bytes
```

### Encode direction (derived by inversion)

```
payload bytes → FEC encode → whiten → interleave (inverse LI map) → Gray encode → RF symbols
```

Each step is invertible:
1. **FEC encode:** Standard Hamming(8,4) — each 4-bit nibble → 8 coded bits
2. **Whiten:** XOR with LFSR sequence (same key, self-inverse)
3. **Interleave:** Apply the inverse permutation of the LI decode map
4. **Gray encode:** Standard Gray code mapping

The LI decode permutation maps coded-bit positions to symbol positions. The
inverse maps symbol positions back to coded-bit positions. For a permutation
`P` where `decode: symbol_bits[P[i]] = coded_bits[i]`, the encode direction
is `P_inv` where `P_inv[P[i]] = i`.

### Validation strategy

1. Generate a known payload (e.g., the WaveShark TX16S test vector)
2. Encode through the full pipeline
3. Decode the result with our validated decoder
4. Verify round-trip produces the original payload

If this round-trip succeeds with the TX16S known-answer vector AND with our
captured frames (encode the decoded bytes, compare against captured symbols),
the encoder is validated.

---

## 4. Open-Source LoRa SDR Resources

### Transmit-capable implementations

| Project | Language | Sub-GHz | 2.4 GHz / SX1280 | Long Interleaver | Status |
|---------|----------|---------|-------------------|------------------|--------|
| gr-lora_sdr (EPFL) | C++/GnuRadio | TX+RX | No | No | Active, validated |
| lora-sdr (jkadbear) | Python | TX | No | No | Research code |
| pysdr LoRa examples | Python | TX concept | No | No | Educational |
| liquid-dsp | C | Chirp only | Freq range ok | No | Library |

### Decode-only (SX1280-relevant)

| Project | Language | LI support | Status |
|---------|----------|------------|--------|
| WaveShark | Rust | **Yes (SF7)** | Active, measured maps |
| Our tools | Python | **Yes (via WaveShark maps)** | Validated on 54 frames |

### Key gap

No open-source project implements SX1280 long-interleaver **encoding** for SDR
transmission. This is the novel work required for Path A. The building blocks
exist (gr-lora_sdr for standard LoRa TX, WaveShark for LI decode maps), but
assembling and validating the full encode pipeline is new.

---

## 5. SDR Chirp Parameter Details

For anyone implementing Path A, here are the precise SX1280 LoRa chirp
parameters for the ELRS v3 150 Hz mode:

### Symbol timing

```
SF = 7
BW = 812500 Hz
T_symbol = 2^SF / BW = 128 / 812500 = 157.538 µs
Symbols per packet:
  Preamble:  12 upchirps
  Sync word:  2 symbols (values from SX1280 sync word register)
  SFD:        2.25 downchirps
  Payload:   21 symbols (8-byte packet) or ~35 symbols (13-byte packet)
  Total air time: ~5.8 ms (8-byte) or ~8.4 ms (13-byte)
```

### Chirp baseband formula

An SF7 upchirp (preamble) sweeps from -BW/2 to +BW/2:
```
f(t) = -BW/2 + BW * (t / T_sym),  0 ≤ t < T_sym
```

A data symbol with value `s` (0 ≤ s < 128) starts at offset `s/128` of the
bandwidth and wraps:
```
f(t) = -BW/2 + BW * ((s/128 + t/T_sym) mod 1)
```

The SFD (start-of-frame delimiter) uses downchirps:
```
f(t) = +BW/2 - BW * (t / T_sym)
```

### IQ sample generation

At sample rate `fs`:
```python
n_sym = 2**sf  # 128 for SF7
samples_per_sym = int(n_sym * fs / bw)  # e.g., 631 at 4 MS/s
t = np.arange(samples_per_sym) / fs

# Phase for symbol value s
freq = bw * (((s / n_sym) + t * bw / n_sym) % 1.0 - 0.5)
phase = 2 * np.pi * np.cumsum(freq) / fs
iq = np.exp(1j * phase).astype(np.complex64)
```

### Frequency plan

```
Channel k frequency = 2400400000 + k * 1000000  Hz
k = 0..79
Sync channel (v3) = 41 → 2441400000 Hz
```

---

## 6. Timing Constraints for FHSS Injection

### ELRS v3 timing at 150 Hz

```
Packet interval:     6.667 ms (150 Hz)
Packets per channel: 4
Hop interval:        26.667 ms (every 4 packets)
Sync revisit:        2.133 s (80 channels × 4 × 6.667 ms)
Sequence length:     240 entries (3 full passes through 80 channels)
Full sequence:       240 × 4 × 6.667 ms = 6.4 s
```

### Injection timing requirements

To inject a packet that the receiver will accept:

1. **Frequency:** Must be on the correct FHSS channel (±BW/2 tolerance,
   so ±406 kHz). The FHSS sequence is UID-derived and fully predictable.

2. **Timing:** The SX1280 receiver expects packets at the 150 Hz rate.
   The RX window is governed by `expresslrs.c` timeout logic:
   - After a valid packet, the receiver listens for the next one
   - `rfPerfParams->RXtimeoutUs` defines the listen window
   - The receiver re-syncs FHSS position from SYNC packets

3. **Nonce:** v3 doesn't mix nonce into CRC for most packet types (only
   SM_WIDE RC_DATA). But the nonce must be plausible for the connection
   state machine.

4. **Power:** Must arrive at the receiver above its sensitivity threshold
   (approximately -105 dBm for SF7/BW812.5kHz). In a bench scenario at
   1–3 meters, even 0 dBm TX power is sufficient.

### SDR timing challenges

| Operation | Typical latency | Impact |
|-----------|----------------|--------|
| UHD TX command → DAC output | 50–200 µs | Acceptable |
| USB bus scheduling | 1–5 ms | **Problematic** for 6.667 ms slots |
| Frequency retune (UHD) | ~1 ms | Acceptable for 26.67 ms hops |
| Python processing | Variable | Needs real-time thread or pre-computed buffers |

**Mitigation for SDR path:**
- Pre-compute all chirp waveforms for each possible symbol
- Pre-compute the full FHSS frequency plan
- Use UHD's timed TX commands (`send()` with `tx_md.time_spec`)
- Use the B210's GPSDO or external reference for timing
- Buffer multiple packets ahead of time

---

## 7. Recommendation

### For the E8–TinyHawk ELRS pair benchmark

**Recommended: Path B (SX1280 module) or Path C (hybrid).**

1. **Immediate next step:** Acquire an inexpensive SX1280-based board:
   - Best option: An ELRS 2.4 GHz TX module ($15–30) that can be flashed
     with custom firmware. This is NOT the owned E8 — flashing a separately
     purchased module is allowed.
   - Alternative: EBYTE E28-2G4M27S SX1280 module ($8–12) + SPI adapter

2. **Integration with existing tools:** The `elrs-tx-emulator.py` already
   has the full packet construction pipeline. It needs:
   - A real SX1280 SPI backend (replacing `DryRunBackend`/`FileBackend`)
   - FHSS timing from the SDR passive capture pipeline
   - This is a well-scoped integration task

3. **Validation path:**
   - All passive bench-validation gates (G1–G8) should be completed first
   - Gate G9 (owner authorization) is required before any RF transmission
   - First active test: single SYNC injection per `docs/bench-validation-plan.md`

### For CTF game day

**Path C (hybrid) is the competition architecture:**
- B205mini/B210 monitors the target's ELRS link passively
- ELRS TX module (or SX1280 dev board) injects forged packets
- The existing Python tool suite handles all protocol logic
- Physical proximity at 2.4 GHz: effective range is 10–50 meters indoors

### For research (if no SX1280 board is available)

**Path A (pure SDR) as a fallback:**
- Invert WaveShark LI maps to get encode direction
- Port gr-lora_sdr chirp generation to 2.4 GHz / SX1280 parameters
- Build a Python LoRa TX pipeline with UHD timed commands
- Validate against captured frames before any live test
- Higher effort, but uses only existing lab equipment

---

## 8. Bill of Materials for Active Testing

| Item | Purpose | Est. Cost | Priority |
|------|---------|-----------|----------|
| ELRS 2.4 GHz TX module (e.g., BetaFPV Nano TX) | SX1280-based packet TX | $15–30 | **High** |
| SPI adapter (USB-SPI, e.g., Bus Pirate, FT232H) | Host→SX1280 comms | $15–25 | High (if not using ESP32) |
| Raspberry Pi 4/5 | Low-latency SPI host | $35–75 | Medium (alternative to USB-SPI) |
| SMA cables + attenuators | Controlled bench testing | $10–20 | Medium |
| EBYTE E28 SX1280 module (backup) | Bare SX1280 access | $8–12 | Low |

**Total estimated cost:** $40–80 for minimum viable active test setup.

The ESP32 on an ELRS TX module can run custom firmware that:
1. Accepts packet bytes over UART/WiFi from the Python pipeline
2. Configures the SX1280 via SPI
3. Handles FHSS frequency hopping with microsecond precision
4. Transmits on command with configurable power and timing

This is substantially simpler than building a LoRa modulator from scratch.

---

## 9. References

- Semtech SX1280/SX1281/SX1282 datasheet Rev 3.3 (register-level PHY config)
- WaveShark: `github.com/v0l/waveshark` (SX1280 LI decode maps, GPL-3.0)
- gr-lora_sdr: `github.com/tapparelj/gr-lora_sdr` (SX127x LoRa TX/RX)
- ExpressLRS source: `github.com/ExpressLRS/ExpressLRS` (SX1280 driver code)
- Betaflight 4.4.2 source: SPI ELRS receiver implementation
- Project captures: 54/62 decoded frames validating WaveShark LI maps
- `tools/elrs-tx-emulator.py`: Existing FHSS + packet construction pipeline
- `tools/elrs_core/v3_compat.py`: v3-correct protocol functions
