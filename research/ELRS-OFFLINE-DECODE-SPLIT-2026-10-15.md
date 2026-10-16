# Offline ELRS byte-decoding split — Claude handoff

Owner direction: split the next E8–TinyHawk benchmark step between Claude and
Codex. Work only from recorded IQ and version-pinned source. The EMAX E8 must
**never be flashed**. Neither track opens the controller, changes settings,
or transmits RF. Raw IQ, pairing UID/configuration, intermediate symbols, and
candidate decoded bytes stay under ignored `work/`, not in Git or issue text.

## Claude: SX1280 long-interleaving decoder

Own the physical-layer inverse from candidate LoRa payload symbols to byte
candidates for the live Betaflight 4.4.2 150 Hz link. The source selects SF7,
BW 812.5 kHz, implicit header, and SX1280 coding parameter **`0x07`**. Its
older enum name says `4/7 LI`; the corrected Semtech SX1280 Rev 3.3 datasheet
identifies `0x07` as **4/8 long interleaving**. Treat the numeric setting and
source-matched behavior as authoritative; do not reuse a legacy short-
interleaver decoder merely because its synthetic round-trip passes.

Deliver a pure offline decoder in a new, separately owned module under
`tools/`, with tests under `tools/tests/`. It should consume the existing
`tools/elrs-iq-symbols.py` JSON `payload_symbols`/`relative_bin` sequence
plus explicit PHY parameters, and return byte candidate(s) with bit-order,
symbol-alignment, and uncertainty information. Cover the SX1280 long
deinterleaving, dewhitening/scrambling, and FEC steps actually required by
`0x07`; document every source or measured assumption. Build independent
known-answer evidence (a reference implementation, published/chip-generated
vector, or another genuinely independent route). A decoder tested only by its
own encoder is not sufficient. Keep the decoder free of capture-specific UID,
timestamps, or hard-coded expected packet bytes.

Do not edit Codex-owned capture/intake and frame-verification files while both
tracks run. If the proposed JSON contract lacks a needed measurement (for
example soft symbol confidence), document that gap and coordinate before
changing `tools/elrs-iq-symbols.py`.

## Codex: capture evidence and v3 frame verifier

Codex owns repeatable extraction of multiple real burst candidates from the
private October 8 BLUE and October 15 sc16le captures, including timing,
frequency, preamble/sync quality, and symbol-confidence metadata. Codex also
owns a **separate** read-only Betaflight 4.4.2 v3 frame/CRC checker. It will
accept byte candidates from Claude's decoder later and report pass/fail with
version-specific packet fields and CRC logic; it must not synthesize a
successful decode or expose the private UID in tracked output. Tests should
use public or synthetic non-secret fixtures and include negative cases.

## Integration and evidence bar

The two modules meet at *candidate bytes*, not a claim of a valid packet.
First verify more than one real SYNC and data frame, including nonzero packet
counters, against the Betaflight 4.4.2 source. A claim of byte recovery needs
repeated CRC passes and consistent v3 fields/FHSS behavior, not merely matching
preamble timing or a single plausible UID fragment. Record disagreements and
uncertainty explicitly. No live acceptance or exploitability is established
by an offline decode.

Starting points: `research/e8-tinyhawk-passive-iq-2026-10-15.md`,
`research/2026-10-08-blue-capture-offline.md`, `tools/elrs-iq-symbols.py`,
and `firmware/src/betaflight-4.4.2.tar.gz`. The Semtech primary reference is
the [SX1280 Rev 3.3 datasheet, Table 14-49](https://shop.richardsonrfpd.com/docs/rfpd/DS_SX1280-1_V3_3.pdf).

Both agents: pull before editing, work in distinct files, commit and push
normally to `main`, and never rewrite history. If either track finds that the
interface or source assumptions are wrong, flag it before quietly adapting
the other track's implementation.
