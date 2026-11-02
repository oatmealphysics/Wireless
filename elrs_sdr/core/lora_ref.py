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


def _cw_bits(cw: int, cw_len: int) -> list[int]:
    """Codeword integer -> the reference's `codeword[]` bool array.

    `deinterleave_block` packs each codeword MSB-first, so `codeword[0]` is bit
    `cw_len-1` of the integer, NOT bit 0.
    """
    return [(cw >> (cw_len - 1 - k)) & 1 for k in range(cw_len)]


#: syndrome -> which `codeword[]` index to flip. `lib/hamming_dec_impl.cc`
#: flips `data_nibble[3,2,1,0]` for syndromes 5,7,3,6, and `data_nibble[k]` is
#: `codeword[3-k]`, so the indices map through as below.
_SYNDROME_FLIP = {5: 0, 7: 1, 3: 2, 6: 3}


def hamming_decode_nibble(cw: int, cr: int, correct: bool = True) -> int:
    """`lib/hamming_dec_impl.cc`: nibble = {cw[3], cw[2], cw[1], cw[0]}.

    *** CORRECTED 2026-10-09 — the previous version was self-consistently wrong.

    It returned `_rev4(cw & 0x0F)`, i.e. the integer's LOW four bits reversed.
    But `deinterleave_block` packs codewords MSB-first, so `codeword[0..3]` --
    the data bits -- live in the integer's HIGH four bits. The low four bits are
    the PARITY bits (p0..p3) for every cr >= 2. So the old decoder fed parity
    into the payload and discarded the data.

    It survived `selftest_roundtrip()` because `hamming_encode_nibble` made the
    mirror-image mistake, so the pair round-tripped perfectly while matching
    neither the reference nor the air. That is exactly the failure mode the
    AGENTS.md §3 "SELF-CONSISTENT is not VALIDATED" rule warns about, and it is
    a prime suspect for the 0-hit real-air results.

    With the data bits in the right place the parity bits become usable, so this
    now also does the reference's single-error correction (cr >= 3 only; cr 1
    and 2 carry too few parity bits to locate an error).
    """
    cw_len = cr + 4
    c = _cw_bits(cw, cw_len)
    if correct and cr >= 3:
        s0 = c[0] ^ c[1] ^ c[2] ^ c[4]
        s1 = c[1] ^ c[2] ^ c[3] ^ c[5]
        s2 = c[0] ^ c[1] ^ c[3] ^ c[6]
        idx = _SYNDROME_FLIP.get(s0 | (s1 << 1) | (s2 << 2))
        if idx is not None:
            c[idx] ^= 1
    return (c[3] << 3) | (c[2] << 2) | (c[1] << 1) | c[0]


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
    # Data bits are codeword[0..3] = the HIGH four bits (deinterleave_block
    # packs MSB-first), and the nibble reads out as {cw[3],cw[2],cw[1],cw[0]}.
    # 0b1111_0001 at cr=4: codeword[0..3] = 1,1,1,1 -> nibble 0b1111.
    assert hamming_decode_nibble(0b11110001, 4, correct=False) == 0b1111
    # 0b1010_0000: codeword[0..3] = 1,0,1,0 -> {cw3,cw2,cw1,cw0} = 0,1,0,1
    assert hamming_decode_nibble(0b10100000, 4, correct=False) == 0b0101
    print("  [PASS] Hamming data = codeword[0..3] (HIGH 4 bits), read out as "
          "{cw[3],cw[2],cw[1],cw[0]}")

    # encoder/decoder agree AND the parity bits are real, so a single bit error
    # anywhere in a cr=4/7 or 4/8 codeword is corrected rather than fatal.
    for cr in (1, 2, 3, 4):
        for nib in range(16):
            cw = hamming_encode_nibble(nib, cr)
            assert hamming_decode_nibble(cw, cr) == nib, (cr, nib)
    print("  [PASS] encode -> decode is exact for all 16 nibbles x 4 rates")
    fixed = total = 0
    for cr in (3, 4):
        for nib in range(16):
            cw = hamming_encode_nibble(nib, cr)
            for b in range(cr + 4):
                total += 1
                fixed += hamming_decode_nibble(cw ^ (1 << b), cr) == nib
    assert fixed > total * 0.5, f"only {fixed}/{total} single-bit errors fixed"
    print(f"  [PASS] single-error correction repairs {fixed}/{total} one-bit "
          f"errors at cr 4/7 and 4/8 (was 0 — parity used to be discarded)")

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


# (the __main__ selftest entry is at the very bottom of the module — it must come
# after the TX-inverse functions (hamming_encode_nibble etc.) that selftest() calls.)


# --------------------------------------------------------------------------- #
# The TX side — the exact inverse of the chain above.
#
# Purpose is diagnostic, not transmission (AGENTS.md §2: the TX path stays
# gated; this only produces SYMBOL NUMBERS in memory, never IQ and never RF).
# It lets us round-trip a KNOWN ELRS SYNC packet through the receive chain:
#   * if the round-trip FAILS, the bug is in our implementation and we can fix
#     it offline against a known-correct answer;
#   * if it PASSES, the chain is internally coherent, so a failure on real air
#     data points at symbol errors or an SX1280-vs-SX127x PHY deviation.
# A passing round-trip is SELF-CONSISTENT only (AGENTS.md §3) — it does not
# prove our chain matches Semtech, since both directions are ours.
# --------------------------------------------------------------------------- #
def whiten_bytes_to_nibbles(data: bytes, whiten: bool = True) -> list[int]:
    """Inverse of `dewhiten_nibbles`: bytes -> whitened nibble stream."""
    w = whitening_seq(len(data) + 2)
    nibbles: list[int] = []
    for i, byte in enumerate(data):
        lo, hi = byte & 0x0F, (byte >> 4) & 0x0F
        if whiten:
            lo ^= w[i] & 0x0F
            hi ^= (w[i] & 0xF0) >> 4
        nibbles.append(lo & 0xF)
        nibbles.append(hi & 0xF)
    return nibbles


def hamming_encode_nibble(nib: int, cr: int) -> int:
    """`lib/hamming_enc_impl.cc`, exactly — including REAL parity bits.

    *** CORRECTED 2026-10-09 alongside `hamming_decode_nibble`. The previous
    version returned `_rev4(nib)` with the parity bits left zero, which mirrored
    the decoder's bug and left the chain with no error correction at all (one
    wrong symbol killed a whole frame).

        data_bin = int2bool(nib, 4)        # data_bin[0] is the nibble's MSB
        p0 = data_bin[3]^data_bin[2]^data_bin[1]
        p1 = data_bin[2]^data_bin[1]^data_bin[0]
        p2 = data_bin[3]^data_bin[2]^data_bin[0]
        p3 = data_bin[3]^data_bin[1]^data_bin[0]
        cr>=2: (d3<<7|d2<<6|d1<<5|d0<<4|p0<<3|p1<<2|p2<<1|p3) >> (4 - cr)
        cr==1: p4 = d0^d1^d2^d3;  d3<<4|d2<<3|d1<<2|d0<<1|p4
    """
    d = [(nib >> (3 - k)) & 1 for k in range(4)]     # MSB-first, as int2bool
    if cr == 1:
        p4 = d[0] ^ d[1] ^ d[2] ^ d[3]
        return (d[3] << 4) | (d[2] << 3) | (d[1] << 2) | (d[0] << 1) | p4
    p0 = d[3] ^ d[2] ^ d[1]
    p1 = d[2] ^ d[1] ^ d[0]
    p2 = d[3] ^ d[2] ^ d[0]
    p3 = d[3] ^ d[1] ^ d[0]
    full = ((d[3] << 7) | (d[2] << 6) | (d[1] << 5) | (d[0] << 4)
            | (p0 << 3) | (p1 << 2) | (p2 << 1) | p3)
    return full >> (4 - cr)


def interleave_block(codewords: list[int], sf_app: int, cw_len: int,
                     msb_first: bool = True) -> list[int]:
    """Inverse of `deinterleave_block`.

    Decoder:  deinter[(i - j - 1) mod sf_app][i] = bit j of symbol i
    so:       bit j of symbol i = bit i of codeword[(i - j - 1) mod sf_app]
    """
    syms = []
    for i in range(cw_len):
        bits = []
        for j in range(sf_app):
            cw = codewords[(i - j - 1) % sf_app]
            bits.append((cw >> (cw_len - 1 - i)) & 1)
        v = 0
        if msb_first:
            for b in bits:
                v = (v << 1) | b
        else:
            for k, b in enumerate(bits):
                v |= b << k
        syms.append(v)
    return syms


def gray_map(sym: int, sf: int) -> int:
    """Inverse of `gray_demap` (out = in ^ (in>>1)): the true gray->binary."""
    out, sh = sym, 1
    while sh < sf:
        out ^= sym >> sh
        sh += 1
    return out & ((1 << sf) - 1)


def encode_payload(data: bytes, sf: int, opts: RefOpts) -> list[int]:
    """bytes -> LoRa symbol numbers, the inverse of `decode_payload`."""
    sf_app = (sf - 2) if opts.ldro else sf
    cw_len = opts.cr + 4
    nibbles = whiten_bytes_to_nibbles(data, opts.whiten)
    while len(nibbles) % sf_app:
        nibbles.append(0)
    symbols: list[int] = []
    for b in range(0, len(nibbles), sf_app):
        block = nibbles[b:b + sf_app]
        cws = [hamming_encode_nibble(nb, opts.cr) for nb in block]
        for s in interleave_block(cws, sf_app, cw_len, opts.msb_first):
            symbols.append(gray_map(s, sf))
    return symbols


def selftest_roundtrip() -> None:
    """Encode a real ELRS SYNC packet, decode it back, and CRC-validate it."""
    from . import ota as _o
    from . import lora_payload as _lp

    uid = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0x2E, 0xE2])
    ok = 0
    for is_full in (True, False):
        for ver in (3, 4):
            raw = _o.build_sync(is_full=is_full, fhss_index=42, nonce=7,
                                rf_rate_enum=3, uid4=uid[4], uid5=uid[5],
                                crc_init=_o.ota_uid_crc_init(uid, ver),
                                ota_nonce=0)
            for opts in all_opts():
                syms = encode_payload(raw, 7, opts)
                back = decode_payload(syms, 7, opts, max_bytes=len(raw))
                if back[:len(raw)] != raw:
                    continue
                hits = _lp._try_sync(back, is_full)
                if any(h.uid4 == 0x2E and h.uid5 == 0xE2 and h.ota_version == ver
                       for h in hits):
                    ok += 1
                    break
            else:
                raise AssertionError(
                    f"round-trip FAILED for full={is_full} v{ver}: no option set "
                    f"recovered the packet -> the chain has an internal bug")
    print(f"  [PASS] encode->decode->CRC round-trip recovered the ELRS SYNC "
          f"packet in all {ok} cases (uid 2ee2, OTA v3+v4, both sizes)")
    print("         (SELF-CONSISTENT only — both directions are ours)")



if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "selftest":
        selftest()
    else:
        print(__doc__)
