"""
LoRa payload chain + CRC-gated ELRS SYNC recovery (the UID air-recovery).

WHY THIS MODULE EXISTS
----------------------
`core/lora.demodulate_symbols` recovers LoRa symbols correctly — validated on the
operator's own B205 capture: dechirping at SF7 / BW 812 kHz gives a clean 10-13
symbol preamble run and a post-preamble pair of ~(8,16) relative to the preamble
bin, i.e. LoRa **sync word 0x12**, reproducible across 12 bursts (notes §19).

What was missing is everything *after* the symbols. `core/lora.symbols_to_bits`
is a self-described "first-order" stub that, for SF>=7, keeps **one bit per
symbol** (the MSB) — but an SF7 symbol carries **7** bits, so it discards 6 of 7
and can never reconstruct a packet. This module implements the real chain:

    symbols -> (offset) -> gray -> diagonal de-interleave -> Hamming -> nibbles
            -> bytes -> [optional de-whitening] -> ELRS packet

THE DISCRIMINATOR (AGENTS.md §3: CRC or it didn't happen)
---------------------------------------------------------
Several details of the chain are Semtech-proprietary and genuinely unknown to us
(gray direction, the symbol offset, the interleaver's diagonal sense, the coding
rate ELRS uses at SF7/812 kHz, which nibble half carries the data, whether the
SX1280 LoRa path whitens at all). AGENTS.md calls this "the single layer that
can't be proven on a desk" — so we do NOT assume any of it. We **search** the
combinations and let the **ELRS CRC decide**, which is legitimate because the
check is strong and self-contained:

  * An ELRS **SYNC** packet carries `uid4`,`uid5` in its own payload
    (`ota._decode_sync` -> raw[1:][4], raw[1:][5]).
  * `OtaCrcInitializer = (uid4<<8 | uid5) ^ (ota_version<<8)` (`ota.ota_uid_crc_init`).
  * For a SYNC packet `validate_crc` uses `nonce_validator = 0`.

So the CRC seed is derived *entirely from bytes the packet hands us*: there is no
chicken-and-egg and **no prior UID is required**. A passing CRC14/16 confirms the
recovered `uid4:uid5` AND the OTA version simultaneously. False-accept odds are
~1/2^14 per trial, so we report the trial count alongside any hit and require
agreement across multiple independent bursts before believing it (see
`recover_from_bursts`).

Pure-python/numpy only — scipy is not a dependency of this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

import numpy as np

from . import ota as _ota

# The unknowns we search over. Each is a real, documented ambiguity — not a knob
# invented to make something fit.
# Highest plausible ELRS `rfRateEnum`. ELRS 2.4 GHz defines six air rates
# (lora.ELRS_2G4_RATES); sub-GHz adds a few more. Anything beyond this is a
# mis-decode, not a packet — see the plausibility gate in `_try_sync`.
MAX_RF_RATE_ENUM = 7

GRAY_MODES = ("xor_shift", "inverse", "none")   # s^(s>>1) | true inverse-gray | identity
SYMBOL_OFFSETS = (0, -1, 1)                     # many LoRa impls apply a +-1 symbol offset
CODING_RATES = (1, 2, 3, 4)                     # 4/5, 4/6, 4/7, 4/8
NIBBLE_HALVES = ("low", "high")                 # which 4 bits of the codeword are data
DIAG_SENSES = (+1, -1)                          # the interleaver's diagonal direction
BIT_ORDERS = ("msb", "lsb")                     # bit order when packing nibbles -> bytes


# --------------------------------------------------------------------------- #
# Gray coding
# --------------------------------------------------------------------------- #
def gray_map(sym: int, mode: str) -> int:
    """Apply the gray stage. `xor_shift` is what several RX chains use as the
    de-map; `inverse` is the true gray->binary inverse; `none` is identity."""
    if mode == "xor_shift":
        return sym ^ (sym >> 1)
    if mode == "inverse":
        out = sym
        shift = 1
        while (sym >> shift) and shift < 32:
            out ^= sym >> shift
            shift += 1
        return out
    return sym


# --------------------------------------------------------------------------- #
# Diagonal de-interleaver
# --------------------------------------------------------------------------- #
def deinterleave_block(symbols: list[int], sf: int, cr: int,
                       diag: int = 1) -> list[int]:
    """One LoRa interleaver block: (4+cr) symbols of `sf` bits -> `sf` codewords
    of (4+cr) bits.

    TX arranges `sf` codewords diagonally across (4+cr) symbols; RX undoes it:
        codeword[i] bit j  =  symbol[j] bit ((i + diag*j) mod sf)
    `diag` flips the diagonal's sense, which differs between references.
    """
    cw_len = 4 + cr
    if len(symbols) < cw_len:
        return []
    out = []
    for i in range(sf):
        cw = 0
        for j in range(cw_len):
            row = (i + diag * j) % sf
            bit = (symbols[j] >> row) & 1
            cw = (cw << 1) | bit
        out.append(cw)
    return out


# --------------------------------------------------------------------------- #
# Hamming / parity stage
# --------------------------------------------------------------------------- #
def codeword_to_nibble(cw: int, cr: int, half: str) -> int:
    """Extract the 4 data bits from a (4+cr)-bit codeword.

    For CR 4/5 and 4/6 the extra bits are parity-only (detect, not correct); for
    4/7 and 4/8 they are Hamming parity. We do not attempt correction — the ELRS
    CRC is the arbiter, and a packet needing correction would fail it anyway.
    `half` selects which 4 bits carry the data, which differs between references.
    """
    cw_len = 4 + cr
    if half == "low":
        return cw & 0x0F
    return (cw >> (cw_len - 4)) & 0x0F


# --------------------------------------------------------------------------- #
# Packing + whitening
# --------------------------------------------------------------------------- #
def nibbles_to_bytes(nibbles: list[int], order: str) -> bytes:
    """Pack 4-bit nibbles into bytes, low- or high-nibble-first."""
    out = bytearray()
    for i in range(0, len(nibbles) - 1, 2):
        a, b = nibbles[i] & 0xF, nibbles[i + 1] & 0xF
        out.append((a << 4) | b if order == "msb" else (b << 4) | a)
    return bytes(out)


def lfsr_whitening(n: int, seed: int = 0xFF, poly: int = 0xB8) -> bytes:
    """A generic 8-bit LFSR whitening stream.

    NOTE: the SX1280's LoRa packet type has no whitening field (whitening is a
    GFSK/FLRC feature on that part), so ELRS LoRa most likely does NOT whiten.
    We therefore default whitening OFF and only offer this as a search option.
    """
    out = bytearray()
    s = seed & 0xFF
    for _ in range(n):
        out.append(s)
        for _ in range(8):
            fb = s & 1
            s >>= 1
            if fb:
                s ^= poly
    return bytes(out)


# --------------------------------------------------------------------------- #
# The full chain
# --------------------------------------------------------------------------- #
@dataclass
class ChainOpts:
    gray: str = "xor_shift"
    offset: int = 0
    cr: int = 1
    half: str = "low"
    diag: int = 1
    order: str = "msb"
    whiten: bool = False
    whiten_flip: bool = False      # the reference's `unwhiten_seeded(..., flip)`
    whiten_seed: int = 0xFF

    def as_tuple(self) -> tuple:
        return (self.gray, self.offset, self.cr, self.half, self.diag,
                self.order, self.whiten, self.whiten_flip, self.whiten_seed)


def _bits_of(codewords: list[int], cw_len: int) -> list[int]:
    out: list[int] = []
    for cw in codewords:
        for b in range(cw_len - 1, -1, -1):
            out.append((cw >> b) & 1)
    return out


def _regroup(bits: list[int], cw_len: int) -> list[int]:
    out: list[int] = []
    for i in range(0, len(bits) - cw_len + 1, cw_len):
        cw = 0
        for b in bits[i:i + cw_len]:
            cw = (cw << 1) | b
        out.append(cw)
    return out


def dewhiten_bits(bits: list[int], seed: int = 0xFF, flip: bool = False) -> list[int]:
    """De-whiten a BIT stream — the reference's `unwhiten_seeded(bits, seed, flip)`.

    Per `docs/ELRS_LORA_SYNC_SPEC.md` §3 the reference whitens at the **bit**
    level, on the de-interleaved stream and BEFORE the Hamming decode, and it
    tries both the normal and the `flip`ped sequence. (My first implementation
    whitened *bytes* *after* Hamming — structurally wrong, which is one reason
    the first on-air search returned 0/21888.)
    """
    out: list[int] = []
    s = seed & 0xFF
    for i, b in enumerate(bits):
        k = (s >> 7) & 1 if flip else (s & 1)
        out.append(b ^ k)
        fb = ((s >> 7) ^ (s >> 5) ^ (s >> 4) ^ (s >> 3)) & 1   # x^8+x^6+x^5+x^4+1
        s = ((s << 1) | fb) & 0xFF
    return out


def decode_payload(symbols: list[int], sf: int, opts: ChainOpts,
                   max_bytes: int = 16) -> bytes:
    """Run symbols through the payload chain, in the REFERENCE's stage order.

    Per `docs/ELRS_LORA_SYNC_SPEC.md` §3 (the detection daemon's C++ symbols):
        dechirp -> gray -> de-interleave -> de-whiten(bits) -> Hamming -> CRC
    Note de-whitening sits BETWEEN de-interleave and Hamming, and acts on bits.
    """
    n = 1 << sf
    mapped = [gray_map((int(s) + opts.offset) % n, opts.gray) for s in symbols]
    cw_len = 4 + opts.cr

    # 3. de-interleave every full block into codewords
    codewords: list[int] = []
    for b in range(0, len(mapped) - cw_len + 1, cw_len):
        codewords.extend(deinterleave_block(mapped[b:b + cw_len], sf,
                                            opts.cr, opts.diag))
        if len(codewords) >= max_bytes * 2 + 8:
            break
    if not codewords:
        return b""

    # 4. de-whiten on the BIT stream (reference: unwhiten_seeded), then regroup
    if opts.whiten:
        bits = dewhiten_bits(_bits_of(codewords, cw_len),
                             opts.whiten_seed, opts.whiten_flip)
        codewords = _regroup(bits, cw_len)

    # 5. Hamming/parity -> nibbles, 6. pack
    nibbles = [codeword_to_nibble(cw, opts.cr, opts.half) for cw in codewords]
    return nibbles_to_bytes(nibbles, opts.order)[:max_bytes]


# --------------------------------------------------------------------------- #
# CRC-gated ELRS SYNC recovery — the actual air-recovery
# --------------------------------------------------------------------------- #
@dataclass
class SyncHit:
    """A CRC-VALID ELRS SYNC packet recovered from the air (no prior UID)."""
    uid4: int
    uid5: int
    ota_version: int
    is_full: bool                  # True = OTA8 (13 B, CRC16); False = OTA4 (8 B, CRC14)
    raw: bytes
    opts: tuple                    # the chain options that produced it
    fhss_index: int = 0
    nonce: int = 0
    rf_rate_enum: int = 0

    @property
    def net_id(self) -> str:
        """The per-link identifier (the reference device's `raw_net_id`)."""
        return f"{self.uid4:02x}{self.uid5:02x}"


def _try_sync(raw: bytes, is_full: bool) -> list[SyncHit]:
    """Is `raw` a CRC-valid ELRS SYNC packet? Try every OTA version.

    The seed comes from the packet's own uid4/uid5, so no prior UID is needed.
    """
    need = _ota.OTA8_PACKET_SIZE if is_full else _ota.OTA4_PACKET_SIZE
    if len(raw) < need:
        return []
    raw = raw[:need]
    if (raw[0] & 0x03) != _ota.PACKET_TYPE_SYNC:
        return []
    body = raw[1:]
    if len(body) < 6:
        return []

    # PLAUSIBILITY GATE (added 2026-10-08 after the `2ee2` false positive).
    #
    # The CRC alone is NOT sufficient here, and it is important to understand why:
    # the seed is derived from `uid4`/`uid5` *in the same decoded bytes* we are
    # checking. A self-seeded CRC is strictly weaker than a true 14/16-bit check,
    # because a degenerate or structured mis-decode can be self-consistent. My
    # original "~1/16384 per trial" claim overstated the gate's strength.
    #
    # The `2ee2` case: a fixed-option search produced 2 CRC-valid hits from 164
    # trials (0.01 expected), so it was systematic rather than random — but the
    # decoded fields were impossible: `rf_rate_enum = 204`, when ELRS 2.4 GHz has
    # only six rates (see lora.ELRS_2G4_RATES) and this link was independently
    # measured as SF7/812 kHz = 150 Hz (enum ~3). So: sanity-check the fields a
    # real ELRS SYNC packet must satisfy, not just the checksum.
    rf_rate_enum = body[2]
    if rf_rate_enum > MAX_RF_RATE_ENUM:
        return []

    uid4, uid5 = body[4], body[5]
    hits = []
    for v in (2, 3, 4, 5, 6):
        crc_init = ((uid4 << 8) | uid5) ^ (v << 8)
        try:
            ok = _ota.validate_crc(raw, is_full, crc_init & 0xFFFF, 0,
                                   _ota.PACKET_TYPE_SYNC)
        except Exception:
            ok = False
        if ok:
            hits.append(SyncHit(uid4=uid4, uid5=uid5, ota_version=v,
                                is_full=is_full, raw=raw, opts=(),
                                fhss_index=body[0], nonce=body[1],
                                rf_rate_enum=body[2]))
    return hits


def recover_sync(symbols: list[int], sf: int,
                 crs: tuple[int, ...] = CODING_RATES,
                 ) -> tuple[list[SyncHit], int]:
    """Search the chain unknowns for a CRC-valid ELRS SYNC packet.

    Returns (hits, trials). `trials` matters for honesty: a CRC14 has a ~1/16384
    false-accept rate per trial, so a single hit out of thousands of trials is
    NOT proof on its own — require agreement across bursts (`recover_from_bursts`).
    """
    hits: list[SyncHit] = []
    trials = 0
    # whitening: off, or on with either sequence sense (the reference's `flip`)
    whiten_modes = ((False, False), (True, False), (True, True))
    for gray, off, cr, half, diag, order, (wh, wflip) in product(
            GRAY_MODES, SYMBOL_OFFSETS, crs, NIBBLE_HALVES,
            DIAG_SENSES, BIT_ORDERS, whiten_modes):
        opts = ChainOpts(gray=gray, offset=off, cr=cr, half=half,
                         diag=diag, order=order, whiten=wh, whiten_flip=wflip)
        data = decode_payload(symbols, sf, opts)
        for is_full in (True, False):
            trials += 1
            for h in _try_sync(data, is_full):
                h.opts = opts.as_tuple()
                hits.append(h)
    return hits, trials


def recover_from_bursts(symbol_sets: list[list[int]], sf: int
                        ) -> tuple[list[SyncHit], dict]:
    """Run `recover_sync` over many bursts and keep only CORROBORATED results.

    The honest bar: a real link produces the SAME (uid4,uid5,ota_version) under
    the SAME chain options on INDEPENDENT bursts. A lone CRC pass is noise.
    Returns (hits, report) where report counts agreement.
    """
    from collections import Counter
    all_hits: list[SyncHit] = []
    total_trials = 0
    for syms in symbol_sets:
        h, t = recover_sync(syms, sf)
        all_hits.extend(h)
        total_trials += t
    key = Counter((h.uid4, h.uid5, h.ota_version, h.opts) for h in all_hits)

    # INDEPENDENCE GATE (added 2026-10-08 after the `2ee2` false positive).
    #
    # Counting >= 2 hits on the same (uid, version, options) is NOT enough. The
    # `2ee2` case passed that bar with two hits that were **byte-identical**:
    # both decoded `fhss_index=202, nonce=37, rf_rate_enum=204`. Two genuinely
    # independent SYNC packets CANNOT look like that — `nonce` increments every
    # packet and `fhss_index` advances through the hop sequence, so two SYNC
    # frames captured at different times must differ in both. Identical values
    # mean we saw ONE systematic decode artifact twice, not two confirmations.
    #
    # So corroboration now requires >= 2 hits that agree on the identity
    # (uid4, uid5, ota_version) while DIFFERING in (nonce, fhss_index) — i.e.
    # evidence of a live, advancing link rather than a repeated artifact.
    by_identity: dict[tuple, list[SyncHit]] = {}
    for h in all_hits:
        by_identity.setdefault((h.uid4, h.uid5, h.ota_version, h.opts), []).append(h)

    corroborated: list[SyncHit] = []
    rejected_identical = 0
    for ident, hs in by_identity.items():
        states = {(h.nonce, h.fhss_index) for h in hs}
        if len(hs) >= 2 and len(states) >= 2:
            corroborated.extend(hs)
        elif len(hs) >= 2:
            rejected_identical += len(hs)

    report = {
        "bursts": len(symbol_sets),
        "trials": total_trials,
        "raw_hits": len(all_hits),
        "distinct": len(key),
        "agreement": key.most_common(5),
        "expected_false_accepts": total_trials / 16384.0,
        "rejected_identical_state": rejected_identical,
        "corroboration_rule":
            "requires >=2 hits agreeing on identity AND differing in "
            "(nonce, fhss_index); identical state = one artifact seen twice",
    }
    return corroborated, report
