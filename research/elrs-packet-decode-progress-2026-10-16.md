# ELRS packet decoding: October 15 passive capture

Status: **payload symbols recovered; packet bytes not decoded**

## Scope

This is offline analysis of the existing 20-second, receive-only B205mini recording of the owned E8/TinyHawk pair. The raw IQ and derived ranked-symbol file remain ignored under `work/`. Neither device was accessed, configured, transmitted to, or flashed.

The owner clarified that no binding phrase is presently applied in Betaflight and may be applied later for a controlled comparison. This setting is **not** equivalent to an all-zero UID. In the pinned Betaflight 4.4.2 `src/main/rx/expresslrs.c`, a nonzero configured six-byte UID selects the normal paired receiver state; an all-zero stored UID selects the special binding UID and binding mode. The earlier wired E8 read provides an independent private UID for later CRC checks, but it is not a PHY byte-decoding oracle and is not published here.

## What was done

- Rechecked that `firmware/src/betaflight-4.4.2.tar.gz` is the version-matched source already in the repository. The live installed binary is not proven byte-identical, and no firmware readout was attempted.
- Checked Semtech's SX1280 Rev 3.3 datasheet: modulation parameter `0x07` is **4/8 long interleaving**, not the older header's mislabeled 4/7. The datasheet says this mode uses a different interleaving scheme and scrambling; it does not provide the symbol-to-byte inverse. A conventional short-interleaver decoder is therefore not a validated substitute. [Semtech datasheet, Table 14-49](https://shop.richardsonrfpd.com/docs/rfpd/DS_SX1280-1_V3_3.pdf).
- Extended `tools/elrs-iq-symbols.py` and `tools/elrs-batch-evidence.py` to retain the three strongest relative FFT bins and their normalized power for each payload symbol, plus a best-versus-runner-up power margin. Existing best-bin fields remain for backward compatibility. These are **ranked bin hypotheses**, not calibrated probabilities, FEC bits, or decoded bytes.
- Reprocessed 34 previously identified bursts from the October 15 capture. All 34 passed the existing 12/12 preamble and two-sync-chirp candidate gates and yielded 21 analyzed payload-symbol positions: 714 positions total. The mean top-two margin was 5.86 dB; 157/714 positions had a margin below 3 dB. The private results are `work/oct15-ranked-2026-10-16.jsonl` and must not be committed or shared publicly.
- Tested a private ±3-baseband-sample payload timing refinement. It improved mean dominant-bin power fraction by only 0.0114 and mean top-two margin by 0.177 dB across the 34 frames. This does not resolve the long-interleaver gap; no timing-refined packet bytes are claimed.
- The 87-test offline suite passed after updating synthetic ranked-symbol and batch fixtures.

## Decoding gate

The captured packet's approximate 21 payload-symbol positions and normal preamble/sync do **not** tell us the eight OTA bytes. The remaining inverse must cover the SX1280 `0x07` long interleaver, bit/symbol mapping, FEC, and scrambling, with an independent known-answer test. The existing v3 frame checker accepts **already decoded** eight-byte frames and can then compare CRC against the private paired UID; it cannot fill this PHY gap.

The minimum convincing result is exact byte recovery on multiple held-out real frames, followed by repeated v3 CRC passes and plausible SYNC/FHSS/nonce fields. A match found by searching many transforms or deriving the CRC seed from the same candidate bytes is not independent validation. A separately obtained SX1280 4/8-LI IQ-plus-FIFO-byte pair would be a strong oracle. The repository's earlier source audit found no stock read-only USB/MSP path to the TinyHawk SX1280 FIFO. Acquiring a passive hardware tap or changing pairing for test vectors would require its own scoped approval; neither was done here.

## Next offline work

1. Find or generate a legally shareable, independently labeled SX1280 4/8-LI known-answer vector from an external test receiver or authoritative reference. Record exact SF, BW, header, CRC, IQ polarity, payload length, and chip revision.
2. Implement the long-interleaver/FEC/scrambling inverse in a separate offline module and test against that oracle, not only self-generated symbols.
3. Feed the private 34-frame ranked-symbol output to that module, carry alternative bins through FEC, and use `tools/elrs-v3-frame-check.py` only on final byte candidates. Keep raw bytes/UID under ignored `work/`.

No RF transmission or device firmware operation is needed for the current offline stage, and the absolute no-flashing rule for both devices remains in force.
