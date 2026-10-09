"""
LoRa payload chain — a faithful port of the gr-lora_sdr receive chain.

WHY A NEW MODULE INSTEAD OF PATCHING `lora_payload.py`
------------------------------------------------------
`lora_payload.py` implemented the chain from first principles and searched 1152
combinations of guessed parameters, hoping the ELRS CRC would pick the right one.
It never produced a corroborated hit, and §24 showed the danger of that approach:
a self-seeded CRC accepted a decode artifact (`2ee2`) as a recovered UID.

Rather than keep guessing, this module ports the algorithms from the reference
open-source implementation, **tapparelj/gr-lora_sdr** (GNU Radio LoRa SDR, the
most complete open LoRa PHY). Each step below cites the file it came from, and
the guessed-parameter search collapses from 1152 combinations to ~32.

WHAT THE REFERENCE CORRECTED IN OUR FIRST ATTEMPT
-------------------------------------------------
1. **De-interleaver formula.** Ours was `codeword[i] bit j = symbol[j] bit
   ((i + diag*j) % sf)`. The reference (`lib/deinterleaver_impl.cc`) is

       for i in range(cw_len):
           for j in range(sf_app):
               deinter_bin[(i - j - 1) mod sf_app][i] = inter_bin[i][j]

   — the i/j roles are swapped relative to ours, plus a `-1` offset.
2. **LDRO.** `sf_app = sf - 2` when low-data-rate-optimize (or header) is on,
   else `sf`. We never modelled this at all; it changes the block geometry.
   `cw_len = cr + 4` for payload (8 for an explicit header).
3. **Whitening is on NIBBLES, AFTER Hamming** (`lib/dewhitening_impl.cc`):

       low_nib  = in[2i]     ^ (whitening_seq[off] & 0x0F)
       high_nib = in[2i + 1] ^ (whitening_seq[off] & 0xF0) >> 4
       out_byte = high_nib << 4 | low_nib

   We whitened *bits* *before* Hamming — structurally wrong. CRC bytes are
   excluded from dewhitening.
4. **Hamming data bits** (`lib/hamming_dec_impl.cc`): the nibble is
   `{cw[3], cw[2], cw[1], cw[0]}` — the low four bits, **bit-reversed** — not a
   plain low/high nibble slice.
5. **Gray demap** is `s ^ (s >> 1)` with no offset (`lib/gray_mapping_impl.cc`).
   Our `xor_shift` mode was already right — which also confirms the `2ee2`
   "winner", which required `gray='none'`, was an artifact.

Pure numpy/stdlib — scipy is not a dependency of this package.

Reference: https://github.com/tapparelj/gr-lora_sdr
"""

from __future__ import annotations

from dataclasses import dataclass

# The reference's published whitening_seq begins with these bytes
# (lib/tables.h). We GENERATE the sequence and assert against this prefix in
# selftest(), so the generator is anchored to the reference rather than trusted.
_WHITENING_PREFIX = (
    0xFF, 0xFE, 0xFC, 0xF8, 0xF0, 0xE1, 0xC2, 0x85,
    0x0B, 0x17, 0x2F, 0x5E, 0xBC, 0x78, 0xF1, 0xE3,
    0xC6, 0x8D, 0x1A, 0x34, 0x68, 0xD0, 0xA0, 0x40,
)


def whitening_seq(n: int) -> list[int]:
    """The LoRa whitening sequence: 8-bit LFSR, seed 0xFF, taps b7^b5^b4^b3.

    Verified against the reference table's first 24 bytes (see selftest).
    """
    out, s = [], 0xFF
    for _ in range(n):
        out.append(s)
        fb = ((s >> 7) ^ (s >> 5) ^ (s >> 4) ^ (s >> 3)) & 1
        s = ((s << 1) | fb) & 0xFF
    return out


def gray_demap(sym: int) -> int:
    """`lib/gray_mapping_impl.cc`:  out = in ^ (in >> 1)."""
    return sym ^ (sym >> 1)


def _bits_of_symbol(sym: int, nbits: int, msb_first: bool) -> list[int]:
    if msb_first:
        return [(sym >> (nbits - 1 - b)) & 1 for b in range(nbits)]
    return [(sym >> b) & 1 for b in range(nbits)]


def deinterleave_block(symbols: list[int], sf_app: int, cw_len: int,
                       msb_first: bool = True) -> list[int]:
    """`lib/deinterleaver_impl.cc`:

        deinter_bin[(i - j - 1) mod sf_app][i] = inter_bin[i][j]

    Takes `cw_len` symbols of `sf_app` bits -> `sf_app` codewords of `cw_len`
    bits. `msb_first` is the one genuine ambiguity (how a symbol becomes a bit
    vector), so it stays a parameter.
    """
    if len(symbols) < cw_len:
        return []
    deinter = [[0] * cw_len for _ in range(sf_app)]
    for i in range(cw_len):
        bits = _bits_of_symbol(symbols[i], sf_app, msb_first)
        for j in range(sf_app):
            deinter[(i - j - 1) % sf_app][i] = bits[j]
    out = []
    for row in deinter:
        cw = 0
        for b in row:
            cw = (cw << 1) | b
        out.append(cw)
    return out


def _rev4(v: int) -> int:
    return ((v & 1) << 3) | ((v & 2) << 1) | ((v & 4) >> 1) | ((v & 8) >> 3)


def hamming_decode_nibble(cw: int, cr: int) -> int:
    """`lib/hamming_dec_impl.cc`: nibble = {cw[3], cw[2], cw[1], cw[0]}.

    i.e. the low four bits of the codeword, bit-reversed. We do not attempt
    single-error correction: the ELRS CRC is the arbiter and a frame needing
    correction would fail it anyway.
    """
    return _rev4(cw & 0x0F)


def dewhiten_nibbles(nibbles: list[int], payload_len: int | None = None
                     ) -> bytes:
    """`lib/dewhitening_impl.cc` — whitening acts on NIBBLE PAIRS, post-Hamming.

        low  = nib[2i]     ^ (wseq[off] & 0x0F)
        high = nib[2i + 1] ^ (wseq[off] & 0xF0) >> 4
        byte = high << 4 | low

    One sequence step per *output byte*. `payload_len` (bytes) bounds how far
    dewhitening is applied; anything beyond it (the LoRa CRC) is passed through,
    matching the reference's CRC exclusion.
    """
    nbytes = len(nibbles) // 2
    w = whitening_seq(nbytes + 2)
    out = bytearray()
    for i in range(nbytes):
        lo, hi = nibbles[2 * i] & 0xF, nibbles[2 * i + 1] & 0xF
        if payload_len is None or i < payload_len:
            lo ^= w[i] & 0x0F
            hi ^= (w[i] & 0xF0) >> 4
        out.append(((hi & 0xF) << 4) | (lo & 0xF))
    return bytes(out)


@dataclass
class RefOpts:
    """The only parameters still genuinely unknown for ELRS (32 combinations)."""
    cr: int = 1              # 1..4 => CR 4/5 .. 4/8 (ELRS does not expose it)
    ldro: bool = False       # sf_app = sf-2 when on
    msb_first: bool = True   # symbol -> bit-vector order
    whiten: bool = True      # SX1280 LoRa may not whiten at all

    def as_tuple(self) -> tuple:
        return (self.cr, self.ldro, self.msb_first, self.whiten)


def decode_payload(symbols: list[int], sf: int, opts: RefOpts,
                   max_bytes: int = 16) -> bytes:
    """Full reference receive chain: gray -> deinterleave -> Hamming -> dewhiten."""
    sf_app = (sf - 2) if opts.ldro else sf
    cw_len = opts.cr + 4
    if sf_app < 1 or len(symbols) < cw_len:
        return b""
    mapped = [gray_demap(int(s) & ((1 << sf) - 1)) for s in symbols]
    nibbles: list[int] = []
    for b in range(0, len(mapped) - cw_len + 1, cw_len):
        for cw in deinterleave_block(mapped[b:b + cw_len], sf_app, cw_len,
                                     opts.msb_first):
            nibbles.append(hamming_decode_nibble(cw, opts.cr))
        if len(nibbles) >= max_bytes * 2 + 4:
            break
    if len(nibbles) < 2:
        return b""
    if opts.whiten:
        return dewhiten_nibbles(nibbles)[:max_bytes]
    out = bytearray()
    for i in range(len(nibbles) // 2):
        out.append(((nibbles[2 * i + 1] & 0xF) << 4) | (nibbles[2 * i] & 0xF))
    return bytes(out[:max_bytes])


def all_opts() -> list[RefOpts]:
    """The full (small) search space: 4 CR x LDRO x bit-order x whiten = 32."""
    return [RefOpts(cr=cr, ldro=ldro, msb_first=mb, whiten=wh)
            for cr in (1, 2, 3, 4)
            for ldro in (False, True)
            for mb in (True, False)
            for wh in (True, False)]


# --------------------------------------------------------------------------- #
# selftest — anchors the generated whitening sequence to the reference table
# --------------------------------------------------------------------------- #
def selftest() -> None:
    w = whitening_seq(len(_WHITENING_PREFIX))
    assert tuple(w) == _WHITENING_PREFIX, (
        f"whitening generator does not match the reference table:\n"
        f"  got      {[hex(x) for x in w]}\n"
        f"  expected {[hex(x) for x in _WHITENING_PREFIX]}")
    print(f"  [PASS] whitening LFSR reproduces the reference table's first "
          f"{len(_WHITENING_PREFIX)} bytes exactly")

    assert gray_demap(0) == 0 and gray_demap(1) == 1
    assert gray_demap(0b1100) == 0b1010
    print("  [PASS] gray demap matches out = in ^ (in >> 1)")

    assert _rev4(0b0001) == 0b1000 and _rev4(0b1011) == 0b1101
    assert hamming_decode_nibble(0b11110001, 4) == 0b1000   # low nibble, reversed
    print("  [PASS] Hamming nibble = {cw[3],cw[2],cw[1],cw[0]} (low 4, reversed)")

    # de-interleaver is a bijection on a block: distinct symbol sets must not
    # collapse to the same codewords
    a = deinterleave_block([1, 2, 4, 8, 16], 7, 5)
    b = deinterleave_block([16, 8, 4, 2, 1], 7, 5)
    assert len(a) == 7 and len(b) == 7 and a != b
    print("  [PASS] de-interleaver returns sf_app codewords and is order-sensitive")

    # LDRO changes the block geometry
    assert len(deinterleave_block([0] * 5, 7, 5)) == 7
    assert len(deinterleave_block([0] * 5, 5, 5)) == 5
    print("  [PASS] LDRO geometry: sf_app=sf-2 yields sf-2 codewords")

    assert len(all_opts()) == 32
    print(f"  [PASS] search space is {len(all_opts())} combinations "
          f"(was 1152 in lora_payload)")
    print("\nlora_ref selftest PASS")


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "selftest":
        selftest()
    else:
        print(__doc__)
