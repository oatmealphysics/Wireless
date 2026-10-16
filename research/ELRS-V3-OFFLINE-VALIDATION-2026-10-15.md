# Codex track: v3 offline capture and frame-validation start

This is the Codex half of
[`ELRS-OFFLINE-DECODE-SPLIT-2026-10-15.md`](ELRS-OFFLINE-DECODE-SPLIT-2026-10-15.md).
All analysis is receive-only. No E8 access, RF transmission, settings write,
or flashing occurred. Private raw captures and any future candidate bytes or
UID remain under ignored `work/`.

## Stable symbol evidence

`tools/elrs-iq-symbols.py` now anchors each input read to the rational
resampler's denominator (64 input samples at 4 MS/s; 448 at 28 MS/s). This
keeps the output chip grid stable when the approximate packet start changes by
a few microseconds. Before the change, nearby guesses could select preamble
times differing by about 24 microseconds in the BLUE recording. Afterward,
five guesses spanning 30–40 microseconds selected the identical preamble time
and score in both the October 8 BLUE and October 15 sc16le captures.

Four bursts in each of the two private recordings were checked with short,
bounded reads on 2441.3788 MHz. All eight passed the detector's PHY-candidate
threshold: 12/12 preamble symbols and both sync offsets, with preamble scores
from 0.536 to 0.755 after grid anchoring. Extracting 24 candidate payload
symbols per burst showed a sharp quality boundary: in **all eight** bursts,
symbols 1–21 had peak-power fractions above 0.2; symbols 22–24 did not. The
median fraction across bursts was 0.69 at position 21, then 0.133, 0.025,
and 0.029 at positions 22–24. The 0.2 threshold is a diagnostic heuristic,
not a bit-error estimate or a proven payload length. It suggests using the
first 21 symbol candidates for an initial decoder comparison while preserving
the full window privately for audit.

The data and capture-specific symbols are not committed. For one reproducible
window, run the existing bounded CLI on the local private capture:

```text
python tools/elrs-iq-symbols.py work/captures/2026-10-08_13-07-21_2450mhz_pred.cdif \
  --sample-rate 28000000 --center-hz 2450000000 \
  --channel-hz 2441378800 --packet-start 0.40729 --max-symbols 21
```

Keep that JSON output under ignored `work/`; its symbol sequence may contain
pairing information after a future decoder is available.

`tools/elrs-batch-evidence.py` now wraps this bounded analysis for multiple
known starts. It refuses tracked output paths and existing filenames, writes
full candidate records only under ignored `work/`, and prints only aggregate
quality to the terminal. Four 21-symbol records from each capture were
generated locally as `work/oct08-2026-10-15-4bursts-symbols.jsonl` and
`work/oct15-2026-10-15-4bursts-symbols.jsonl`; all eight report 12/12
preamble consistency, matching sync offsets, and 21/21 symbols over the 0.2
peak-fraction heuristic. The private JSONL files are **not** uploaded to Git.
On another machine, regenerate them from the private captures using the
documented start times and the batch tool rather than inferring bytes from
this aggregate report.

## Separate Betaflight 4.4.2 v3 checker

Added `tools/elrs-v3-frame-check.py` for **pre-decoded eight-byte** candidate
frames. It does not turn IQ or symbol bins into bytes. Its CRC/field rules are
read directly from version-pinned `src/main/rx/expresslrs_common.{h,c}` and
`expresslrs.c` in `firmware/src/betaflight-4.4.2.tar.gz`:

- CRC14 polynomial `0x2E57`, first seven packet bytes, with six stored high
  bits in byte 0 and eight low bits in byte 7.
- Bound-link initializer `((UID[4] << 8) | UID[5]) ^ 3`.
- For `SM_WIDE` RC only, the CRC calculation replaces `crcHigh` with
  `(nonce % fhssHopInterval) + 1`; at the 150 Hz rate the interval is four.
  SYNC and other packet types zero that field before CRC calculation. This is
  **not** the newer model's blanket nonce XOR.
- SYNC has FHSS index, nonce, switch mode, telemetry ratio, rate index, and
  UID[3..5]. With a provided private UID, the checker reports whether the
  receiver's 18 visible identity bits match, without printing UID bytes.

The checker can derive a CRC seed from a candidate SYNC's own UID suffix when
no trusted UID is supplied, but labels that result `self_derived_sync_suffix`.
It is not independent identity validation. RC frames without the required UID,
switch mode, or (for `SM_WIDE`) nonce remain `unverifiable`; the tool does not
guess them. Input may be hex lines or JSONL with `packet_bytes_hex`, optional
`nonce` and `switch_mode`; a private six-byte UID file can be supplied later.
No raw packet bytes or UID are printed in the verdict.

Five synthetic v3 tests compare the bitwise CRC against an independent literal
port of the 4.4.2 C table loop. They cover valid/invalid SYNC, `SM_WIDE` RC
with correct and wrong nonce slots, `SM_HYBRID` RC, missing context, and wrong
frame length and privacy-safe CLI output. With the six existing intake/symbol
tests, eleven offline tests
pass. **No real candidate bytes have been supplied yet, so no real CRC or UID
match has been claimed.** Claude's independently validated SX1280 long-
interleaving decoder remains the next integration input.

## Version-matched FHSS cross-check ready for decoded candidates

`tools/elrs-v3-fhss-check.py` adds a separate, offline Gate 2 check for
pre-decoded JSONL records containing `packet_bytes_hex` and a measured
`channel_hz`. It requires a private UID file: a candidate SYNC's own visible
UID suffix does not count as independent evidence. For each candidate it
checks the 4.4.2 CRC and visible UID fields, then compares the SYNC-advertised
FHSS index with the frequency recorded for that frame. It prints only
aggregate category counts, never raw frames, UID, indices, paths, or individual
frequencies. Missing or unmappable frequencies are counted separately;
malformed values stop with a line-number-only error.

The sequence model is ported from Betaflight 4.4.2
`src/main/rx/expresslrs_common.c::fhssGenSequence/rngN`: 80 channels,
240 sequence entries, v3 sync channel 41, and a UID-seeded 31-bit LCG.
Nominal 2.4 GHz channels are 2400.4 MHz plus 1 MHz per index; the CLI's
default 100 kHz carrier tolerance accommodates the offset measured in the
private recordings without silently mapping an adjacent channel. The
advertised index is compared to the recorded channel as a **source-derived
candidate relationship**, not a live-link validation result. The tool cannot
tell whether `channel_hz` was independently measured or just copied from a
tuning plan; the integration report must record that provenance before
counting a channel match as independent evidence.

Five synthetic tests cover a fixed independent sequence vector, block
permutations, frequency mapping, CRC gating, a UID seed byte not checked by
the CRC, and privacy-safe aggregate output. The related offline suite has
15 passing tests at the time of that addition; NumPy/SciPy symbol tests were
then skipped because those optional libraries were absent. The repository
validator passed. No real decoded bytes exist yet, so no real CRC/UID/FHSS
match has been claimed. The updated test result appears below.

## Automatic offline PHY candidate survey (2026-10-15)

`tools/elrs-phy-scan.py` now surveys a bounded interval of a **recorded** IQ
file at one specified channel. It uses coarse energy regions to limit work,
then the existing SF7 preamble and two LoRa sync-word chirp checks. It can
find adjacent 150 Hz bursts even when their energy regions merge. It does not
open an SDR, demodulate packet bytes, or distinguish an ELRS SYNC packet from
another ELRS packet type. `packet_bytes_decoded` is always false. Full symbol
bins and capture paths go only to a new JSONL file under ignored `work/`;
terminal output gives aggregate counts, and an existing output is never
overwritten.

Example for the private October 15 recording:

```text
python3 tools/elrs-phy-scan.py \
  --capture work/captures/e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq \
  --sample-rate 4000000 --center-hz 2441000000 --channel-hz 2441378800 \
  --start-seconds 0 --duration-seconds 20 \
  --output work/auto-phy-2026-10-15.jsonl
```

On the local files, this heuristic found **34** PHY candidates in the 20 s
October 15 sc16le recording and **8** in the 3.302208 s October 8 BLUE
recording at the same candidate channel. The October 8 result consists of
four candidates in each previously documented visit and zero outside those
visits. A 60 ms October 15 spot check found four of four expected adjacent
bursts. The count is **not** a completeness guarantee: thresholding can miss
weak packets or admit unrelated LoRa-like signals. No candidate has passed a
byte-level CRC check.

The 21-symbol payload window used by default is consistent with the observed
approximately 5.9 ms on-air packet and source-selected SF7/BW812.5 kHz,
12-symbol preamble, implicit header, and hardware CRC-off settings; it is not
a proof of byte or packet-type identity. Four new synthetic tests cover
adjacent preambles, a quiet-noise negative control, private-output guards,
and end-to-end cf32le file intake.

This is a reconstructed offline survey, not Claude's unpublished capture
draft.

For new observations, `tools/elrs-sync-channel-record.py` supplies a small
receive-only SoapySDR recorder. It defaults to the earlier B205mini setup:
2441.0 MHz center, 4 MS/s, 2 MHz analog bandwidth, RX2, 35 dB gain. That
span includes the **nominal** Betaflight 4.4.2 v3 fixed channel-41 frequency
of 2441.4 MHz. It saves cf32le IQ and actual settings/completeness metadata
under ignored `work/`, refuses overwrites, and never configures TX or opens
the E8. It is intentionally a **channel recorder**, not a SYNC packet
classifier. Its driver calls and output were tested with a fake SDR; it has
not been exercised against live hardware here. Pass `--input-format cf32le`
to the offline scanner for its output. With NumPy and SciPy installed in the
local Ubuntu environment, the related suite now runs **24 passing tests**
(including an existing symbol timing assertion relaxed to its one-baseband-
sample resolution).

The byte-decoder gate remains open. The numeric SX1280 coding setting `0x07`
needs a validated long-interleaving inverse and independent known-answer
evidence before these symbol candidates can be promoted to decoded SYNC/RC
frames. A synthetic encoder/decoder round-trip alone would not meet that bar.
