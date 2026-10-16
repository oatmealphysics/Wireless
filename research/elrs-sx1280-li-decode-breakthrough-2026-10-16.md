# SX1280 long-interleaver decode: externally measured map validated on our pair

Status (2026-10-16): **54 of 62 existing passive PHY candidates yielded CRC-valid Betaflight v3 packet bytes.** This supersedes the undecoded status in `elrs-packet-decode-progress-2026-10-16.md` and `tinyhawk-paired-phy-analysis-2026-10-16.md`. The eight remaining candidates are unresolved, not negative packets. No live acceptance, forgery, or transmitter behavior is established.

## Source and independence

[WaveShark's SX128x long-interleaver implementation](https://github.com/v0l/waveshark/blob/62b513a7b699fd3a6928908102902abf9d9b2650/crates/decode/src/lora_li.rs) reports an empirical bit-walk on a RadioMaster RP2 and independent TX16S off-air confirmation. The project includes measured [SF7/4-8-LI maps](https://github.com/v0l/waveshark/tree/62b513a7b699fd3a6928908102902abf9d9b2650/crates/decode/src/lora_li) and its [measurement driver](https://github.com/v0l/waveshark/blob/62b513a7b699fd3a6928908102902abf9d9b2650/tools/bitwalk.py). This is the independently measured source missing from our earlier synthetic round trips. The upstream code is GPL-3.0-or-later; it has **not** been vendored into our tracked repository. An attributed, private exploratory adaptation is at ignored `work/try_li.py`.

The map is specific to SF7, SX1280 `0x07` (4/8 long interleaving), implicit-header, eight-byte packets. It predicts **21 payload symbols**, exactly the length already extracted independently from our recordings. Its first eight symbols carry five coded bits each; the remaining 13 carry seven, for 131 slots holding 128 coded bits. Sixteen systematic four-bit nibbles and their parity bits are spread over the entire packet; whitening and symbol Gray mapping are also specified by the measured implementation. The published 13-byte TX16S off-air known-answer vector passed the private decoder before our captures were evaluated. That external fixture checks the Python translation, while our own CRCs are a separate, held-out test.

## Offline result

Only ignored `work/oct15-ranked-2026-10-16.jsonl` (34 frames) and `work/tinyhawk-rc-retry-full-phy-2026-10-16.jsonl` (28 frames) were read. There was no device access or transmission. The previously measured relative FFT-bin convention was tested with a **global positive orientation** and one of two adjacent offsets (0 or +1 bin); no arbitrary per-bit map was fitted. Strict FEC rejects a nibble it cannot repair. The Betaflight 4.4.2 source-derived `tools/elrs-v3-frame-check.py` then checks CRC and fields.

| Existing capture | PHY candidates | FEC-decodable, offset 0 | FEC-decodable, offset +1 | CRC-valid SYNC | CRC-valid RC |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-10-15, 20 s | 34 | 23 | 9 | 10 | 22 |
| 2026-10-16, 15 s | 28 | 14 | 8 | 6 | 16 |
| **Total** | **62** | **37** | **17** | **16** | **38** |

The 54 CRC-valid outcomes are from 54 distinct candidates. All 16 SYNC frames passed the v3 CRC, yielded the **same two-byte UID suffix**, reported hybrid switch encoding, and carried air-rate code **7**. Betaflight 4.4.2 `airRateIndexToIndex24` maps over-air code 7 to local index 2, the independently established LoRa 150 Hz SF7 mode. This resolves the apparent 7-versus-2 discrepancy. The SYNC CRC's initializer is derived from bytes within that same SYNC frame, so it is not by itself an independent identity proof. However, the suffix is consistent across both recordings and, when transferred to *different* frames, all 38 FEC-decodable RC frames pass the hybrid RC CRC. This cross-frame agreement is the strongest check here. No UID or packet bytes are published in this note.

The one-bin alternative is consistent with imperfect baseline/frequency estimation. It is selected by FEC/CRC, not a free packet-specific permutation. The 8 candidates still failing may involve low-margin symbol decisions, timing or interference; the currently saved top-three alternatives can be used for error-aware recovery. Do not call those frames decoded yet.

## Gemini proposals assessed

- A known-byte/SX1280-IQ oracle is sound and still valuable, but WaveShark's published measured map and independent off-air fixture make buying or flashing another node unnecessary for this SF7/4-8-LI setting. No change to the owner's absolute no-flashing rule is implied.
- The Hamming constraint is useful **after** fixing the map and symbol convention. A random eight-bit block is one of 16 exact codewords with probability 1/16, but a one-error-correcting decoder also accepts nearby non-codewords. Searching a large guessed matrix and then using the same packets to select it risks overfitting. We did not do that: the map came from an external measurement, and real ELRS CRCs cross-check the result.
- The ranked FFT alternatives are the next offline way to try the eight unresolved candidates, with CRC used only as a final check. Recovering more frames is useful but not required to establish byte-level decoding of this capture set.

## Remaining gates

1. Promote the private adaptation into a maintained offline tool with tests against the upstream off-air fixture and these ignored held-out captures. Respect and document WaveShark's GPL-3.0-or-later terms before vendoring or deriving distributable code.
2. Compare the 38 decoded RC channel values against the 2026-10-16 USB `MSP_RC` observation over the limited timestamp overlap. That would tie over-air control data to measured stick movement, not merely CRC-valid frames.
3. Recover the eight low-confidence frames, if practical, by constrained ranked-symbol search. Keep raw bytes, UID and captures under ignored `work/`.
4. Treat any live crafted-input or RF-TX test as a **separate** authorization and safety plan. No-flash and no-opening constraints remain absolute for the owned E8 and TinyHawk.

## Publication and stick-motion follow-up (2026-10-16)

The owner subsequently authorized publishing the relevant captures. The two
IQ files, ranked-symbol JSONL files, October 16 USB/MSP stick log, hashes,
and privacy-safe decoded summary are now under `captures/`. The large IQ files
are Git LFS objects; see `captures/README.md` for format, timing, reproduction,
and disclosure caveats. The separate wired E8 configuration response remains
unpublished. An attributed GPL-3.0-or-later offline decoder and independent
tests are now in `tools/elrs-li-offline.py` and `tools/tests/`; the experimental
ignored script is no longer needed to reproduce the result. This is a
correction to remaining gate 1 above, not a change to its original evidence.

The USB comparison used the October 16 IQ file's *predeclared* original local
creation time as sample-zero estimate, with no clock shift fitted. Five of
the CRC-valid RC packets fall within 120 ms of a USB sample in the overlap;
the nearest-sample gaps are 10–36 ms. Source-derived OTA analog fields map to
the observed MSP order as `[1,2,3,0]`. Three stationary channels match exactly
in all five comparisons; the moving right-stick channel has mean absolute
difference 13.6 channel units, including one exact match. This supports the
decoded channel interpretation, but the timestamp anchor and USB polling rate
do not prove exact packet-to-stick timing. `tools/elrs-rc-overlap.py` performs
this fixed-anchor comparison reproducibly.

Both published IQ copies were rescanned from sample zero with the existing
receive-only PHY scanner: 34 and 28 candidates. The completed LI decoder
again produced 32 and 22 CRC-valid frames respectively, for the same 54/62
total. The full 99-test offline suite passed in the writable publication
checkout. No hardware was accessed during this follow-up.
