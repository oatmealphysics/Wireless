"""LoRa PHY decode: symbols → payload bytes for SX1280 ELRS captures.

Implements the standard LoRa decode chain:
  1. Gray decode (reverse Gray coding on each symbol)
  2. De-interleave (diagonal bit-shuffle across SF symbols)
  3. Hamming FEC decode (CR-dependent error correction)
  4. De-whiten (XOR with SX12xx LFSR sequence)

Parameters are configurable for SF 5-12, CR 4/5 through 4/8, and both
standard and long interleaving modes (SX1280-specific).

Evidence class: SELF-CONSISTENT (encode→decode round-trips pass; not yet
validated against a real SX1280 capture — that is the pipeline's job).

For the live TinyHawk III (BF 4.4.2, rate_index=2, 150 Hz):
  SF=7, BW=812500 Hz, CR=4/7, long interleaving, implicit header, no HW CRC,
  payload = 8 bytes (OTA_Packet4_s).

Python 3.10+, no external dependencies.
For authorized CTF research only.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Gray coding
# ---------------------------------------------------------------------------
def gray_encode(n: int) -> int:
    return n ^ (n >> 1)


def gray_decode(g: int) -> int:
    n = g
    shift = 1
    while (n >> shift) > 0:
        n ^= (n >> shift)
        shift <<= 1
    return n


def gray_decode_symbols(symbols: list[int], sf: int = 7) -> list[int]:
    mask = (1 << sf) - 1
    return [gray_decode(s & mask) for s in symbols]


# ---------------------------------------------------------------------------
# Bit manipulation helpers
# ---------------------------------------------------------------------------
def symbol_to_bits(symbol: int, sf: int) -> list[int]:
    """MSB-first bit extraction from a symbol value."""
    return [(symbol >> (sf - 1 - i)) & 1 for i in range(sf)]


def bits_to_nibble(bits: list[int]) -> int:
    """4-bit list (MSB first) → nibble."""
    v = 0
    for b in bits:
        v = (v << 1) | (b & 1)
    return v


# ---------------------------------------------------------------------------
# De-interleaving
# ---------------------------------------------------------------------------
def deinterleave_standard(symbols: list[int], sf: int, cr: int) -> list[list[int]]:
    """Standard LoRa diagonal de-interleave.

    Takes blocks of SF symbols, each SF bits wide. Returns lists of
    (cr+4)-bit codewords (one per data nibble). The interleave is a
    diagonal read of the SF × SF bit matrix formed by the symbols.

    cr: coding rate index (1=4/5, 2=4/6, 3=4/7, 4=4/8)
    """
    ppg = cr + 4  # parity bits per group
    codewords = []
    block_size = sf

    for blk_start in range(0, len(symbols), block_size):
        block = symbols[blk_start:blk_start + block_size]
        if len(block) < block_size:
            break

        # Build bit matrix: block_size rows × sf columns
        matrix = [symbol_to_bits(s, sf) for s in block]

        # Read diagonally: for each row r, for each column c,
        # the bit at (r, (r+c) % sf) contributes to codeword c
        for cw_idx in range(sf):
            cw = []
            for r in range(min(ppg, block_size)):
                col = (cw_idx + r) % sf
                cw.append(matrix[r][col])
            if len(cw) == ppg:
                codewords.append(cw)

    return codewords


def deinterleave_long(symbols: list[int], sf: int, cr: int,
                      payload_nibbles: int) -> list[list[int]]:
    """SX1280 long interleaving de-interleave.

    In long interleaving mode, all codeword bits are transposed through
    a (n_symbols × sf) matrix. Bits were written column-major during
    encoding; here we read the matrix row-major (from symbols) and
    extract them column-major to recover the bit stream, then split
    into ppg-bit codewords.

    This is the mode ELRS uses for 150Hz (CR_LI_4_7).
    """
    ppg = cr + 4
    n_symbols = len(symbols)

    if n_symbols == 0:
        return []

    # Build the full bit matrix: n_symbols rows × sf columns
    matrix = [symbol_to_bits(s, sf) for s in symbols]

    # Read column-major to recover the flat bit stream
    bits = []
    for col in range(sf):
        for row in range(n_symbols):
            bits.append(matrix[row][col])

    # Split into codewords of ppg bits each
    total_cw = min(payload_nibbles, len(bits) // ppg)
    codewords = []
    for i in range(total_cw):
        codewords.append(bits[i * ppg:(i + 1) * ppg])

    return codewords


# ---------------------------------------------------------------------------
# Hamming FEC decode
# ---------------------------------------------------------------------------
# Hamming parity check matrices for each coding rate
# CR 4/5: single parity bit (P0) — detect 1 error
# CR 4/6: P0 + P1 — detect 1 error
# CR 4/7: Hamming(7,4) — correct 1 error
# CR 4/8: Hamming(8,4) + overall parity — correct 1, detect 2 errors

def hamming_decode_7_4(codeword: list[int]) -> tuple[int, int]:
    """Decode Hamming(7,4): 7 bits → 4 data bits + error count.

    Bit order (MSB first): [d3, d2, d1, p2, d0, p1, p0]
    Syndrome checks standard Hamming positions.
    Returns (nibble, errors_corrected).
    """
    if len(codeword) < 7:
        codeword = codeword + [0] * (7 - len(codeword))

    d3, d2, d1, p2, d0, p1, p0 = codeword[:7]

    # Syndrome
    s0 = p0 ^ d0 ^ d1 ^ d3
    s1 = p1 ^ d0 ^ d2 ^ d3
    s2 = p2 ^ d1 ^ d2 ^ d3

    syndrome = (s2 << 2) | (s1 << 1) | s0
    errors = 0

    if syndrome != 0:
        errors = 1
        # Syndrome value (1-7) is the Hamming position.
        # Map to our array index: [d3=pos7, d2=pos6, d1=pos5, p2=pos4, d0=pos3, p1=pos2, p0=pos1]
        _HAM_POS_TO_IDX = {1: 6, 2: 5, 3: 4, 4: 3, 5: 2, 6: 1, 7: 0}
        bits = [d3, d2, d1, p2, d0, p1, p0]
        idx = _HAM_POS_TO_IDX.get(syndrome)
        if idx is not None:
            bits[idx] ^= 1
        d3, d2, d1, p2, d0, p1, p0 = bits

    nibble = (d3 << 3) | (d2 << 2) | (d1 << 1) | d0
    return nibble, errors


def hamming_decode_8_4(codeword: list[int]) -> tuple[int, int]:
    """Decode Hamming(8,4): 8 bits → 4 data bits + error count.

    Extended Hamming with overall parity bit P3.
    Can correct 1 error and detect 2 errors.
    """
    if len(codeword) < 8:
        codeword = codeword + [0] * (8 - len(codeword))

    d3, d2, d1, p2, d0, p1, p0, p3 = codeword[:8]

    # Syndrome
    s0 = p0 ^ d0 ^ d1 ^ d3
    s1 = p1 ^ d0 ^ d2 ^ d3
    s2 = p2 ^ d1 ^ d2 ^ d3

    syndrome = (s2 << 2) | (s1 << 1) | s0
    overall_parity = d3 ^ d2 ^ d1 ^ p2 ^ d0 ^ p1 ^ p0 ^ p3
    errors = 0

    if syndrome == 0 and overall_parity:
        # Only p3 is wrong
        errors = 1
    elif syndrome != 0 and overall_parity:
        # Single error in the (7,4) part — correct it
        errors = 1
        _HAM_POS_TO_IDX = {1: 6, 2: 5, 3: 4, 4: 3, 5: 2, 6: 1, 7: 0}
        bits = [d3, d2, d1, p2, d0, p1, p0, p3]
        idx = _HAM_POS_TO_IDX.get(syndrome)
        if idx is not None:
            bits[idx] ^= 1
        d3, d2, d1, p2, d0, p1, p0, p3 = bits
    elif syndrome != 0 and not overall_parity:
        # Double error detected, uncorrectable
        errors = 2

    nibble = (d3 << 3) | (d2 << 2) | (d1 << 1) | d0
    return nibble, errors


def fec_decode(codewords: list[list[int]], cr: int) -> tuple[list[int], int]:
    """Decode FEC codewords to nibbles.

    cr: 1=4/5, 2=4/6, 3=4/7, 4=4/8
    Returns (nibbles, total_errors).
    """
    nibbles = []
    total_errors = 0

    for cw in codewords:
        if cr == 4:
            nib, errs = hamming_decode_8_4(cw)
        elif cr == 3:
            nib, errs = hamming_decode_7_4(cw)
        elif cr == 2:
            # CR 4/6: parity check only, extract data bits
            # Bit order: [d3, d2, d1, d0, p1, p0]
            nib = bits_to_nibble(cw[:4])
            p0_check = cw[0] ^ cw[1] ^ cw[2] ^ cw[3] ^ (cw[5] if len(cw) > 5 else 0)
            errs = 1 if p0_check else 0
        elif cr == 1:
            # CR 4/5: single parity, extract data bits
            nib = bits_to_nibble(cw[:4])
            parity = 0
            for b in cw[:5]:
                parity ^= b
            errs = 1 if parity else 0
        else:
            nib = bits_to_nibble(cw[:4]) if len(cw) >= 4 else 0
            errs = 0

        nibbles.append(nib)
        total_errors += errs

    return nibbles, total_errors


# ---------------------------------------------------------------------------
# Whitening (SX12xx LFSR)
# ---------------------------------------------------------------------------
# The SX1280 uses a whitening LFSR with polynomial x^8 + x^6 + x^5 + x^4 + 1
# (0x171, taps at bits 4,5,6). Seed is 0xFF.
_WHITEN_POLY = 0x171
_WHITEN_SEED = 0xFF


def _whiten_sequence(n_bits: int, seed: int = _WHITEN_SEED) -> list[int]:
    """Generate n_bits of the SX12xx whitening sequence."""
    state = seed & 0x1FF  # 9-bit LFSR
    seq = []
    for _ in range(n_bits):
        seq.append(state & 1)
        feedback = 0
        temp = state & _WHITEN_POLY
        while temp:
            feedback ^= (temp & 1)
            temp >>= 1
        state = ((state >> 1) | (feedback << 8)) & 0x1FF
    return seq


def dewhiten_nibbles(nibbles: list[int], seed: int = _WHITEN_SEED) -> list[int]:
    """XOR nibble stream with the whitening sequence."""
    n_bits = len(nibbles) * 4
    seq = _whiten_sequence(n_bits, seed)
    result = []
    for i, nib in enumerate(nibbles):
        whiten_nib = 0
        for b in range(4):
            whiten_nib = (whiten_nib << 1) | seq[i * 4 + b]
        result.append(nib ^ whiten_nib)
    return result


def nibbles_to_bytes(nibbles: list[int]) -> bytes:
    """Pack pairs of nibbles into bytes (high nibble first)."""
    out = bytearray()
    for i in range(0, len(nibbles) - 1, 2):
        out.append(((nibbles[i] & 0xF) << 4) | (nibbles[i + 1] & 0xF))
    return bytes(out)


# ---------------------------------------------------------------------------
# Hamming FEC encode (for round-trip testing)
# ---------------------------------------------------------------------------
def hamming_encode_7_4(nibble: int) -> list[int]:
    """Encode 4 data bits → Hamming(7,4) codeword."""
    d3 = (nibble >> 3) & 1
    d2 = (nibble >> 2) & 1
    d1 = (nibble >> 1) & 1
    d0 = nibble & 1

    p0 = d0 ^ d1 ^ d3
    p1 = d0 ^ d2 ^ d3
    p2 = d1 ^ d2 ^ d3

    return [d3, d2, d1, p2, d0, p1, p0]


def hamming_encode_8_4(nibble: int) -> list[int]:
    """Encode 4 data bits → Hamming(8,4) codeword."""
    cw = hamming_encode_7_4(nibble)
    p3 = 0
    for b in cw:
        p3 ^= b
    cw.append(p3)
    return cw


# ---------------------------------------------------------------------------
# Interleaving (encode direction, for round-trip testing)
# ---------------------------------------------------------------------------
def interleave_standard(codewords: list[list[int]], sf: int) -> list[int]:
    """Standard LoRa interleave: codewords → symbols."""
    cr = len(codewords[0]) - 4 if codewords else 0
    ppg = cr + 4
    symbols = []
    block_size = sf

    for blk_start in range(0, len(codewords), sf):
        block = codewords[blk_start:blk_start + sf]
        if len(block) < sf:
            block = block + [[0] * ppg] * (sf - len(block))

        # Build SF symbols from the SF codewords by writing diagonally
        for sym_idx in range(block_size):
            bits = [0] * sf
            for r in range(min(ppg, block_size)):
                col = (sym_idx + r) % sf
                if r < len(block[sym_idx]):
                    pass  # placeholder
                bits[col] = block[(sym_idx - 0) % sf][r] if r < len(block[sym_idx % sf]) else 0

            # Diagonal write: codeword c contributes bit r at row r, col (c+r)%sf
            bits = [0] * sf
            for r in range(min(ppg, block_size)):
                cw_idx = sym_idx  # which codeword
                # We want: matrix[r][(cw_idx + r) % sf] = codeword[cw_idx][r]
                # But for encoding we build matrix then read rows as symbols
                pass

        # Simpler approach: build the full matrix, then read rows
        matrix = [[0] * sf for _ in range(block_size)]
        for cw_idx in range(min(sf, len(block))):
            cw = block[cw_idx]
            for r in range(min(ppg, block_size)):
                col = (cw_idx + r) % sf
                matrix[r][col] = cw[r] if r < len(cw) else 0

        for row in matrix:
            val = 0
            for bit in row:
                val = (val << 1) | (bit & 1)
            symbols.append(val)

    return symbols


# ---------------------------------------------------------------------------
# Full encode chain (for testing)
# ---------------------------------------------------------------------------
def encode_payload(payload: bytes, sf: int = 7, cr: int = 3,
                   long_interleave: bool = True) -> list[int]:
    """Encode payload bytes → LoRa symbol values (for round-trip testing).

    Returns: list of Gray-coded symbol values ready for chirp modulation.
    """
    # Bytes → nibbles (high nibble first)
    nibbles = []
    for b in payload:
        nibbles.append((b >> 4) & 0xF)
        nibbles.append(b & 0xF)

    # Whiten
    whitened = dewhiten_nibbles(nibbles)

    # FEC encode
    codewords = []
    for nib in whitened:
        if cr == 4:
            codewords.append(hamming_encode_8_4(nib))
        elif cr == 3:
            codewords.append(hamming_encode_7_4(nib))
        elif cr == 2:
            bits = [(nib >> (3 - i)) & 1 for i in range(4)]
            p0 = bits[0] ^ bits[1] ^ bits[2] ^ bits[3]
            p1 = bits[0] ^ bits[1]
            codewords.append(bits + [p1, p0])
        else:
            bits = [(nib >> (3 - i)) & 1 for i in range(4)]
            p0 = bits[0] ^ bits[1] ^ bits[2] ^ bits[3]
            codewords.append(bits + [p0])

    # Interleave
    if long_interleave:
        symbols = interleave_long(codewords, sf)
    else:
        symbols = interleave_standard(codewords, sf)

    # Gray encode
    symbols = [gray_encode(s) for s in symbols]

    return symbols


def interleave_long(codewords: list[list[int]], sf: int) -> list[int]:
    """Long interleave encode: codewords → symbols.

    Flatten all codeword bits, write them column-major into a
    (n_symbols × sf) matrix, then read rows as symbols.
    """
    ppg = len(codewords[0]) if codewords else 0
    n_cw = len(codewords)
    total_bits = n_cw * ppg
    n_symbols = (total_bits + sf - 1) // sf

    # Flatten codewords into a bit stream
    bits = []
    for cw in codewords:
        bits.extend(cw)
    # Pad to fill the matrix
    bits.extend([0] * (n_symbols * sf - len(bits)))

    # Write column-major into matrix
    matrix = [[0] * sf for _ in range(n_symbols)]
    idx = 0
    for col in range(sf):
        for row in range(n_symbols):
            matrix[row][col] = bits[idx]
            idx += 1

    # Read rows as symbols
    symbols = []
    for row in matrix:
        val = 0
        for bit in row:
            val = (val << 1) | (bit & 1)
        symbols.append(val)

    return symbols


# ---------------------------------------------------------------------------
# Full decode chain
# ---------------------------------------------------------------------------
def decode_payload(symbols: list[int], sf: int = 7, cr: int = 3,
                   payload_len: int = 8,
                   long_interleave: bool = True) -> tuple[bytes, int]:
    """Decode LoRa symbol values → payload bytes.

    Returns: (payload_bytes, fec_errors)
    """
    # Gray decode
    decoded_syms = gray_decode_symbols(symbols, sf)

    # De-interleave
    payload_nibbles = payload_len * 2
    if long_interleave:
        codewords = deinterleave_long(decoded_syms, sf, cr, payload_nibbles)
    else:
        codewords = deinterleave_standard(decoded_syms, sf, cr)

    # FEC decode
    nibbles, fec_errors = fec_decode(codewords[:payload_nibbles], cr)

    # De-whiten
    nibbles = dewhiten_nibbles(nibbles[:payload_nibbles])

    # Nibbles → bytes
    payload = nibbles_to_bytes(nibbles)

    return payload[:payload_len], fec_errors
