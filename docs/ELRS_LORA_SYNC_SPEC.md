# Reference C-UAS SDR: ELRS LoRa Sync Template + Payload Chain Spec

> **Source:** the reference counter-UAS passive SDR's firmware (v4.0.6 `.swu`), extracted +
> analyzed 2026-10-09. Every claim below is **VALIDATED** (read directly from the firmware
> binary + config files). The device's internal names are scrubbed per the standing no-names
> rule (the vendor's code name, the paired-ECM device name, etc. — see the naming directive in
> AGENTS.md §1).
>
> **Purpose:** this is the spec for the **ELRS LoRa sync-word air-recovery** — the exact
> parameters Claude needs to implement the LoRa demod → sync word → UID recovery in
> `core/lora.py`. It answers the specific asks in Claude's notes §16/§17/§19:
> - "the reference's `parse_sync_0` + the `.fc64` sync templates" (§16)
> - "the ELRS **LoRa** sync structure (length in symbols, where it sits relative to the
>   preamble, and what `parse_sync_0` actually correlates)" (§18)
> - "the reference's ELRS **LoRa** core, specifically the **coding rate** it uses for SF7 @
>   812 kHz and its de-interleave/whitening constants" (§19)

---

## 1. The LoRa sync template (the `.fc64` files)

The firmware ships **two** `.fc64` sync templates (both **complex128** / float64,
interleaved I,Q):

| File | Samples | Mag range | Mean mag | What it is |
|---|---|---|---|---|
| `symbPSSfft12M.fc64` | 4096 (2^12) | [0.002, 111.8] | 56.5 | The **PSS FFT** (Primary Synchronization Signal FFT) at 12 MHz — the **magnitude spectrum** of the sync symbol. Used for **spectrum-based sync detection** (correlate the signal's FFT against this template). The magnitude profile has a **dip in the middle** (segments 5-6 drop to ~2.9, the rest are ~57-74) — a characteristic of the PSS pattern. |
| `syncSymbSSSCAFFDC.fc64` | 101,268 | [0, 1.65] | 0.57 | The **sync symbol in the time domain** (the "SSS C-FFDC" pattern). **Constant-envelope** (flat magnitude ~0.56-0.59 across all 10 segments). The phase derivative **varies** (0.17, -0.12, 0.02 rad/samp in the first 3 SF7 symbols) — this is **NOT a linear chirp** (a LoRa chirp would have a constant positive derivative). It's a **FSK-like** reference waveform (the phase changes direction). 101,268 samples at 812.5 kHz = **124.6 ms** — a **long reference sync waveform**. |

**How they're used (inferred from the `lora_sync_peak` function, §2):**
- The `symbPSSfft12M.fc64` is the **f-domain template** (`f_domain_template` in the
  `lora_sync_peak` locals) — the signal's FFT is correlated against this to find the sync
  symbol.
- The `syncSymbSSSCAFFDC.fc64` is the **time-domain reference** — used for the
  `f_domain` (the FFT of the time-domain reference, which is the f-domain template for the
  FFT-based sync detection).

**Note:** these are the **generic LoRa** sync templates (the "SSS C-FFDC" pattern). For **ELRS
LoRa specifically**, the sync word is the **stock SX12xx 0x12** (the "private network" value) —
Claude confirmed this in §19b (the two symbols after the preamble, relative to the preamble bin,
across 12 bursts: (8,16) rock-solid → sync word = 0x12). So the ELRS LoRa sync word is NOT
UID-seeded (that's a FLRC concept) — the per-link ID is in the **payload** (the ELRS SYNC
packet), not the LoRa sync word.

---

## 2. The `lora_sync_peak` function (the sync detector)

The firmware's LoRa sync detector is a **templated C++ function** `lora_sync_peak` with
template parameters `<BW, sync_len, preamble_len, window_len, template_len, invert_iq>`. The
**exact instantiations** for the **812.5 kHz** bandwidth (the ELRS 2.4 GHz 150 Hz rate, which
is the link Claude validated in §17d):

| BW (Hz) | sync_len (symbols) | preamble_len (symbols) | window_len (samples) | template_len (samples) | invert_iq |
|---|---|---|---|---|---|
| 812,500 | **12** | **96** | 1,536 | 3,072 | 0 / 1 |
| 812,500 | **12** | **96** | 384 | 768 | 0 / 1 |
| 812,500 | **12** | **96** | 768 | 1,536 | 0 / 1 |
| 812,500 | **12** | **96** | 96 | 192 | 0 / 1 |
| 812,500 | **14** | **96** | 192 | 384 | 0 / 1 |

**Key parameters for SF7 @ 812.5 kHz:**
- **sync_len = 12 symbols** (the LoRa sync word length — 12 symbols = 6 bits × 2 symbols/bit
  for the standard LoRa sync encoding `((sw>>4)<<3, (sw&0xF)<<3)`)
- **preamble_len = 96 symbols** (the LoRa preamble length)
- **invert_iq** = 0 or 1 (the IQ inversion flag — the reference tries BOTH, like the
  `invert_iq` field in the `LORADetect` table)
- The **multiple window/template sizes** (1536/3072, 384/768, 768/1536, 96/192, 192/384) =
  the reference tries **multiple FFT window sizes** (a multi-resolution sync search — larger
  windows for better frequency resolution, smaller for better time resolution)

**The `lora_sync_peak` locals** (from the demangled C++ symbols):
- `sync_window_buf` — the sync window buffer (the signal segment being analyzed)
- `f_domain_template` — the f-domain template (the FFT of the sync template — the
  `symbPSSfft12M.fc64` or the FFT of `syncSymbSSSCAFFDC.fc64`)
- `corr` — the correlation result (the sync peak)
- `f_domain` — the f-domain signal (the FFT of the signal segment)

**So the reference's LoRa sync detection = FFT the signal segment, correlate against the
f-domain template (the sync template's FFT), find the peak = the sync position.** This is the
"measure, don't assume" approach — it **discovers** the sync position from the signal, not
from a configured offset.

---

## 3. The LoRa payload chain (the demod → bits → bytes)

The firmware's LoRa payload chain (from the C++ symbols in the detection daemon):

| Stage | Function | C++ symbol | Parameters |
|---|---|---|---|
| 1. Dechirp → symbols | (the FFT per 2^SF sample, argmax) | (the `DSP_LORA_812p5` FPGA core) | BW, SF |
| 2. Gray decode | (the standard LoRa Gray code) | (implied — the standard LoRa) | SF |
| 3. **De-interleave** | `deinterleave` | `_Z12deinterleaveSt6vectorIfSaIfEEss` | `vector<float>` (the symbols), `short` (the SF), `short` (the CR block size) |
| 4. **De-whiten** | `unwhiten_seeded` | `_Z15unwhiten_seededRSt6vectorIhSaIhEES2_hb` | `vector<unsigned char>` (the bits), `char` (the seed), `bool` (the flip flag) |
| 5. **Hamming FEC decode** | `hamming74_decode` / `hamming1511_decode` / `hamming3126_decode` | `_Z15hamming74_decodeRh` / `_Z18hamming1511_decodeRhS_` / `_Z18hamming3126_decodeRhS_S_S_` | (the bits) |
| 6. **CRC check** | `crc16` / `crc24A_check` / `crc32` | `_Z5crc16hPh` / `_Z12crc24A_checkPh` / `_Z8runCRC32St6vectorIcSaIcEES1_` | (the bytes) |

**The `LORADetect` table** (the per-detection parameters, from the flatbuffers schema):
```
table LORADetect {
    center_freq_hz:double;
    is_power_valid:bool;
    power_dbm:float;
    det_time_us:long;
    antenna:Antenna;
    sf:int;              // the spreading factor (5-9)
    bandwidth:int;       // the bandwidth (250, 500, or 812.5 kHz)
    nzeros:int;          // the LoRa CRC length (0 or 1 byte)
    invert_iq:bool;      // the IQ inversion flag
    n_upchirp_dets:int;  // the up-chirp detection count
    n_downchirp_dets:int; // the down-chirp detection count
    channel_freqs_hz:[double]; // the channel frequencies
}
```

**The `DetectionNetID` table** (the per-link ID):
```
table DetectionNetID {
    device_network_id:int;
    baudrate_enum:int;
    baudrate:int;
    net_id:string;
    raw_net_id:[ubyte];  // THE per-link ID (the byte array)
}
```

**The ELRS LoRa-specific parameters** (for SF7 @ 812.5 kHz):
- **sync_len = 12 symbols** (the sync word length)
- **preamble_len = 96 symbols** (the preamble length)
- **nzeros** = 0 or 1 (the LoRa CRC length — the reference tries both)
- **invert_iq** = 0 or 1 (the reference tries both)
- **coding rate (CR)** = **NOT explicitly named in the firmware** (the `ScanTimeFSK` enum is
  just a scan-duration lookup, not the CR). The reference's `LORADetect` table does NOT have a
  `cr` field. **The CR is an unknown** — Claude's §19d correctly identifies this as the biggest
  unknown in the search. The ELRS firmware uses **CR 4/5** (LDRO) or **CR 4/8** (LONG)
  depending on the rate, but the reference device does NOT name it in the config. Claude will
  need to **iterate the CR** (4/5, 4/6, 4/7, 4/8) + let the CRC decide (the "measure, don't
  assume" approach applied to the decoder itself).
- **de-interleave block size** = `SF × (4+CR)` (the standard LoRa diagonal interleaver — the
  `deinterleave` function takes the SF + the CR block size as the two `short` params)
- **whitening seed** = the LFSR seed (the `unwhiten_seeded` function takes the seed + the flip
  flag — the reference tries both the normal + the flipped whitening)

---

## 4. The ELRS LoRa sync word (the per-link ID is NOT in it)

Claude confirmed in §19b: the ELRS LoRa sync word is **0x12** (the stock SX12xx "private
network" value). **The per-link ID is NOT in the LoRa sync word** — it's in the **payload**
(specifically an ELRS **SYNC packet**, which carries `uid4` + `uid5` in its own payload).

The **recovery chain** (Claude's §19c, which I confirm is correct):
1. Demod a burst to payload bytes (the LoRa payload chain in §3)
2. Read candidate `uid4`, `uid5` from payload positions 4, 5
3. For `v` in OTA v2..v6: `crc_init = (uid4<<8|uid5) ^ (v<<8)`
4. `ota.validate_crc(...)`
5. A passing CRC confirms `uid4:uid5` AND the OTA version simultaneously, with no prior UID

**The CRC-gated discriminator** (AGENTS.md §3): a genuine ELRS frame passes the CRC in a way a
raw correlation peak does not. The ELRS CRC14/16 over a recovered uid4/uid5 is a strong check
(~1/16384 false-accept per trial), so Claude can legitimately **search** the unknowns (Gray
direction, CR, whitening variant, bit order) + let the CRC pick the right combination.

---

## 5. What this means for the implementation (Claude's §19 next step)

**The knowns (from the firmware):**
- The sync word is 0x12 (12 symbols) — NOT UID-seeded (that's FLRC)
- The preamble is 96 symbols
- The per-link ID is in the payload (the ELRS SYNC packet, positions 4, 5)
- The LoRa payload chain is: Gray decode → de-interleave (SF × (4+CR) block) → de-whiten
  (LFSR, seeded + flip) → Hamming FEC (7,4 / 15,11 / 31,26) → CRC (16/24/32)
- The `LORADetect` table has: SF, BW, nzeros (0/1), invert_iq (0/1)

**The unknowns (Claude must search + CRC-gate):**
- The **coding rate** (CR 4/5, 4/6, 4/7, 4/8) — NOT named in the firmware
- The **Gray decode direction** (normal vs inverse)
- The **whitening variant** (normal vs flipped, the seed)
- The **bit order** (MSB vs LSB)
- The **CRC length** (nzeros 0 vs 1)
- The **invert_iq** (0 vs 1)

**The search space** is small (6 CRs × 2 Gray × 2 whitening × 2 bit-order × 2 nzeros × 2
invert_iq = 96 combinations), and the CRC gate (~1/16384 false-accept) makes it tractable.
Claude's "measure, don't assume" approach applied to the decoder itself is the right call.

---

## 6. The `parse_sync_0` function (the sync parser)

The firmware's `parse_sync_0` function (string: `parse_sync_0: pos (%zu) ret (%d)`) reports:
- **`pos`** (the sync position, in samples) — where the sync word was found
- **`ret`** (the return code, 0 = success, non-zero = failure)

**What it correlates:** the signal's FFT against the f-domain template (the sync template's
FFT — the `symbPSSfft12M.fc64` or the FFT of `syncSymbSSSCAFFDC.fc64`). The peak = the sync
position. This is the "read the sync out of the air" — the same air-recovery Claude is
building.

**Note:** `parse_sync_0` is the **generic LoRa** sync parser (the "SSS C-FFDC" pattern). For
**ELRS LoRa specifically**, the sync word is the stock 0x12 (not the "SSS C-FFDC" pattern), so
the reference's `parse_sync_0` may NOT be the one used for ELRS LoRa (the ELRS LoRa path uses
the `DSP_LORA_812p5` FPGA core + the `ELRSDetect` handler, which has its own sync detection).
The `syncSymbSSSCAFFDC.fc64` template is the **generic LoRa** reference (for the `LORADetect`
path), not the ELRS-specific one.

---

## 7. Summary (the one-paragraph spec for Claude)

The reference C-UAS SDR's ELRS LoRa sync detection + payload chain: **FFT the signal segment,
correlate against the f-domain template (the sync template's FFT), find the peak = the sync
position (the `lora_sync_peak` function, with sync_len=12 symbols, preamble_len=96 symbols,
multiple window/template sizes, invert_iq 0/1). The LoRa payload chain: Gray decode →
de-interleave (SF × (4+CR) block, the `deinterleave(vector<float>, short sf, short cr_block)`
function) → de-whiten (LFSR, seeded + flip, the `unwhiten_seeded(vector<unsigned char>, char
seed, bool flip)` function) → Hamming FEC decode (7,4 / 15,11 / 31,26, the
`hamming74_decode` / `hamming1511_decode` / `hamming3126_decode` functions) → CRC check
(16/24/32, the `crc16` / `crc24A_check` / `crc32` functions). The ELRS LoRa sync word is 0x12
(stock SX12xx, NOT UID-seeded — the per-link ID is in the payload, the ELRS SYNC packet
positions 4, 5). The coding rate (CR 4/5, 4/6, 4/7, 4/8) is NOT named in the firmware — Claude
must iterate it + let the CRC decide. The `LORADetect` table has: SF, BW, nzeros (0/1),
invert_iq (0/1). The search space is small (96 combinations) + the CRC gate (~1/16384
false-accept) makes it tractable.**

---

*Firmware deleted after analysis (the operator's rule: no device material on disk; notes
only). The `.xz` is still in git history at commit `4a4ce8a` if a re-fetch is needed.*
