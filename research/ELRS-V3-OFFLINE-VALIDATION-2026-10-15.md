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
