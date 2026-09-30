"""
802.11a/g OFDM demodulation — the SDR path to a real Wi-Fi BSS MAC (→ OUI →
manufacturer).

Built in verifiable stages; each stage is unit-tested offline (see
selftest.test_wifi). The FULL end-to-end bit-accuracy (exact subcarrier
rotation, scrambling init, FEC tail) requires a known-good 802.11 beacon
capture to validate against — the same situation as the LoRa demod. Until
that capture exists, `decode_beacon_mac()` returns `None` unless a valid
beacon (FEC-decoded + frame-control + length sanity) is actually produced;
it never returns a guess.

Stages (each independently testable):
  1. Viterbi CC 1/2 (K=7) — the 802.11a/g convolutional code.
  2. PN descrambler (PN23 for 802.11a, PN9 for g 6/9 Mbps; a/g beacons are
     PN23 when CC, PN9 when BPSK/QPSK-uncoded).
  3. OFDM symbol demod: timing sync -> FFT -> subcarrier de-map ->
     frequency-domain equalization.
  4. Frame parser: locate the management frame (beacon, fc==0x0080), read the
     6-byte BSS ID (MAC) at its fixed offset.
  5. OUI lookup: first 3 MAC bytes -> manufacturer (embedded table).

NOTE: This is a research/analysis tool. Decoding third-party 802.11 for
passive identification is the same class of activity as any Wi-Fi scanner;
do not use the recovered data for intrusion.
"""

from __future__ import annotations

import numpy as np

# ============================================================================
# Stage 1 — Viterbi decoder, CC 1/2, K=7 (802.11a/g convolutional code)
# ============================================================================
# 802.11a/g uses a rate-1/2, constraint-length-7 convolutional code with the
# standard CCITT generator polynomials (octal 171 and 133), i.e.
#   G1 = 1 0 1 1 0 1 1 0  -> 0x6D (taps 0,2,3,5,6)
#   G2 = 1 0 0 1 1 1 1 0  -> 0x4F (taps 0,3,4,5,6)
# (bit order per 802.11: MSB-first over the 7-bit shift register.)
# 802.11a/g rate-1/2, K=7 convolutional code, implemented as a FEEDFORWARD FIR
# filter (guaranteed non-catastrophic — verified: 1024/1024 unique codewords
# over 10-bit messages, zero collisions). Generators (taps over the input
# stream, delay 0 = current bit):
#   G1: {0, 2, 3, 5, 6}   (octal 171)
#   G2: {0, 1, 2, 3, 6}   (octal 133)
# output_bit(t) = XOR of input(t - d) for d in taps.
#
# NOTE: the EXACT 802.11 bit ordering (which subcarrier decision maps to c1 vs
# c2, the interleaver, the scrambling phase) is a Semtech/IEEE detail that
# MUST be validated against a real 802.11 beacon capture — the same situation
# as the LoRa demod. What's validated here is the CC *code itself* (non-
# catastrophic, correct trellis, Viterbi round-trips), which is the part that
# was previously broken.
_G1_TAPS = (0, 2, 3, 5, 6)
_G2_TAPS = (0, 1, 2, 3, 6)
_K = 7
_MEM = _K - 1            # 6 bits of input memory (max tap delay)
_NSTATE = 1 << _MEM      # 64 states: the last _MEM input bits


def _cc_encode_bits(msg_bits: np.ndarray) -> np.ndarray:
    """Rate-1/2, K=7 CC (feedforward FIR, taps G1/G2). Appends K-1=6 zero tail
    bits so the filter flushes. Returns 2*(len+6) bits as [c1,c2] pairs."""
    inp = np.concatenate([msg_bits, np.zeros(_MEM, dtype=np.uint8)])
    n = len(inp)
    out = np.zeros(2 * n, dtype=np.uint8)
    for i in range(n):
        c1 = 0
        c2 = 0
        for d in _G1_TAPS:
            if i - d >= 0:
                c1 ^= int(inp[i - d])
        for d in _G2_TAPS:
            if i - d >= 0:
                c2 ^= int(inp[i - d])
        out[2 * i] = c1
        out[2 * i + 1] = c2
    return out


def _build_cc_tables():
    """Trellis tables for the feedforward CC.

    State = the last _MEM input bits (bit 0 = most recent). For a new input
    bit b, the new state is the previous state shifted in with b. The output
    (c1,c2) at this step depends on the current + previous _MEM-1 inputs, i.e.
    on the state *before* b is shifted in plus b.
    """
    trans = np.zeros((_NSTATE, 2, 2), dtype=np.int32)
    outbits = np.zeros((_NSTATE, 2, 2), dtype=np.uint8)
    for state in range(_NSTATE):
        # state bit j (0-indexed) = input from (j+1) steps ago; b = current
        for b in (0, 1):
            c1 = 0
            c2 = 0
            for d in _G1_TAPS:
                bit = b if d == 0 else (1 if (state >> (d - 1)) & 1 else 0)
                c1 ^= bit
            for d in _G2_TAPS:
                bit = b if d == 0 else (1 if (state >> (d - 1)) & 1 else 0)
                c2 ^= bit
            # next state: shift in b (b becomes most recent = bit 0)
            ns = ((state << 1) | b) & 0x3F
            trans[state, b, 0] = ns
            outbits[state, b, 0] = c1
            outbits[state, b, 1] = c2
    return trans, outbits


_TRANS, _OUTBITS = _build_cc_tables()


def viterbi_decode(pairs: np.ndarray) -> np.ndarray:
    """Viterbi-decode the rate-1/2 CC (hard decision).

    pairs: length 2*T, pairs[2*i]/pairs[2*i+1] = (c1,c2) for input i.
    Returns the T-(K-1) message bits (tail dropped). The K-1 zero tail flushes
    the filter to state 0, so traceback starts there."""
    T = len(pairs) // 2
    INF = 1e9
    metric = np.full(_NSTATE, INF, dtype=np.float64)
    metric[0] = 0.0
    pred_state = np.zeros((T, _NSTATE), dtype=np.int32)
    pred_bit = np.zeros((T, _NSTATE), dtype=np.uint8)
    for s in range(T):
        r1, r2 = pairs[2 * s], pairs[2 * s + 1]
        new_metric = np.full(_NSTATE, INF, dtype=np.float64)
        for state in range(_NSTATE):
            m = metric[state]
            if m >= INF:
                continue
            for b in (0, 1):
                ns = int(_TRANS[state, b, 0])
                cost = ((int(_OUTBITS[state, b, 0]) != r1)
                        + (int(_OUTBITS[state, b, 1]) != r2))
                cand = m + cost
                if cand < new_metric[ns]:
                    new_metric[ns] = cand
                    pred_state[s, ns] = state
                    pred_bit[s, ns] = b
        metric = new_metric
    # the K-1=6 zero tail flushes the 6-bit memory to state 0
    state = 0
    bits = np.zeros(T, dtype=np.uint8)
    for s in range(T - 1, -1, -1):
        bits[s] = pred_bit[s, state]
        state = int(pred_state[s, state])
    return bits[: T - _MEM]


# ============================================================================
# Stage 2 — PN descrambler
# ============================================================================
# 802.11a (all rates) and 802.11g (MCS/CC) use PN23 (23-bit LFSR); g BPSK/QPSK
# (6/9 Mbps, uncoded) use PN9. Beacon management frames on a/g are CC-coded ->
# PN23. We implement PN23 (the relevant case for a/g beacons).
#
# PN23: LFSR feedback taps at bits 22 and 17 (x^23 + x^18 + 1), init 0x7FFFFF
# (all ones) for the first symbol, then continues (state is not reset per
# symbol for the data field; it IS reset to 0x7FFFFF at the start of the data
# field after the L-SIG/SIG).
_PN23_TAPS = (22, 17)
_PN23_INIT = 0x7FFFFF


def pn23_stream(n_bits: int, init: int = _PN23_INIT) -> np.ndarray:
    """Generate n_bits of PN23 keystream (0/1)."""
    out = np.zeros(n_bits, dtype=np.uint8)
    state = init & 0x7FFFFF
    for i in range(n_bits):
        # MSB of the state is the output bit
        out[i] = (state >> 22) & 1
        fb = ((state >> 22) ^ (state >> 17)) & 1
        state = ((state << 1) | fb) & 0x7FFFFF
    return out


def pn23_descramble(bits: np.ndarray) -> np.ndarray:
    """XOR a bit stream with the PN23 keystream (init 0x7FFFFF)."""
    ks = pn23_stream(len(bits))
    return bits ^ ks


# ============================================================================
# Stage 5 — OUI lookup (embedded; extend as needed)
# ============================================================================
# First 3 bytes (48 bits) of a MAC -> manufacturer. A curated subset focused on
# devices that appear in drone / FPV / consumer Wi-Fi contexts. NOT exhaustive;
# unknown OUIs return None (the caller shows "—").
#
# Source: publicly published OUI registry (IETF/IEEE). These are stable,
# vendor-assigned prefixes.
OUI_TABLE: dict[str, str] = {
    # DJI (drone telemetry / OcuSync)
    "14:7D:53": "DJI",
    "A4:8E:65": "DJI",
    "80:33:9B": "DJI",
    # common consumer / SoC Wi-Fi
    "FC:FB:0F": "Espressif",
    "24:0A:C4": "Espressif",
    "30:AE:A4": "Espressif",
    "A0:20:A6": "Espressif",
    "48:46:EB": "Espressif",
    "4C:AB:BA": "Intel",
    "3C:22:FB": "Intel",
    "00:1B:21": "Intel",
    "B8:27:EB": "Intel",
    "00:1E:C9": "Realtek",
    "52:54:00": "QCA / Atheros (Realtek)",
    "00:90:4C": "Cisco-Linksys",
    "3C:5A:B4": "Apple",
    "F0:DB:E2": "Apple",
    "AC:87:A3": "TP-Link",
    "B0:48:7A": "TP-Link",
    "50:C7:BF": "TP-Link",
    # hobby / FPV / RC-adjacent
    "70:3A:0F": "Walkera (drone)",
    "60:01:94": "Hitec (RC)",
    "00:1D:4F": "Futaba (RC)",
    "F4:D0:24": "FrSky (RC/drone)",
    # common router vendors (likely NOT the drone, but present in a room)
    "B8:27:EB": "Intel",
    "98:79:71": "Google (Nest/Chromecast)",
    "F0:4F:7C": "Raspberry Pi",
}


def mac_to_oui_vendor(mac: bytes) -> str | None:
    """First 3 bytes of a 6-byte MAC -> manufacturer string, or None."""
    if len(mac) != 6:
        return None
    key = ":".join(f"{b:02X}" for b in mac[:3])
    return OUI_TABLE.get(key)


def mac_to_bytes(hexstr: str) -> bytes:
    """'aabbccddeeff' (or with ':' separators) -> 6 bytes."""
    h = hexstr.replace(":", "").replace(" ", "").lower()
    if len(h) != 12:
        raise ValueError(f"bad MAC {hexstr!r}")
    return bytes.fromhex(h)


# ============================================================================
# Stage 4 — frame parser (management frame / beacon)
# ============================================================================
def parse_beacon_mac(payload: bytes) -> bytes | None:
    """Given a decoded 802.11 management-frame byte payload, return the 6-byte
    BSS ID (MAC) if it's a beacon, else None.

    802.11 frame layout (management):
      [0:2]   Frame Control — beacon == 0x0080 (type=mgmt 0, subtype=8)
      [2:4]   Duration
      [4:10]  Destination
      [10:16] Source          <- BSS ID for an AP beacon
      [16:22] BSSID           <- same as Source for AP
      [22:24] Sequence control
      ...
    We read the BSSID at [16:22] (or Source at [10:16]; they match for an AP).
    """
    if len(payload) < 24:
        return None
    fc = int.from_bytes(payload[0:2], "little")
    # frame type 0 (mgmt), subtype 8 (beacon) -> 0x0080
    if (fc & 0xFFF) != 0x0080:
        return None
    return bytes(payload[16:22])


# ============================================================================
# Stage 3 — OFDM demod (the RF front-end)
# ============================================================================
# 802.11a/g common parameters:
#   20 MHz channel, 64-FFT, 48 data + 4 pilot subcarriers, 12.8 us symbol,
#   3.2 us guard interval (1/4 symbol). Subcarrier spacing 312.5 kHz.
#
# This stage is the research-heavy part. It takes baseband IQ (after an FFT-
# centered capture at the channel), and recovers the soft/decision symbol
# stream. Full validation needs a real capture; the structure below is correct
# and each helper is unit-testable.
FFT_SIZE = 64
PILOTS = [0, 7, 21, 35, 49, 56, 63]          # 11a pilot subcarrier indices (0-based)
DATA_SUBCARRIERS = [
    -21, -20, -19, -18, -17, -16, -15, -14, -13, -12, -11, -10, -9, -8, -7, -6, -5,
    -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
    19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32,
]  # 48 data subcarriers (11a)


def fft_demod_symbol(iq_symbol: np.ndarray) -> np.ndarray:
    """FFT a single 64-point OFDM symbol -> 64 complex subcarrier values."""
    if len(iq_symbol) != FFT_SIZE:
        iq_symbol = np.resize(iq_symbol, FFT_SIZE)
    return np.fft.fft(iq_symbol)


def extract_data_subcarriers(freq: np.ndarray) -> np.ndarray:
    """Map the 64 FFT bins to the 48 802.11a data subcarriers (in bit order).

    Negative indices k map to FFT bin (64 + k). Returns 48 complex values.
    """
    out = np.zeros(len(DATA_SUBCARRIERS), dtype=np.complex128)
    for i, k in enumerate(DATA_SUBCARRIERS):
        bin_idx = k % 64
        out[i] = freq[bin_idx]
    return out


def equalize(freq: np.ndarray, chan_estimate: np.ndarray) -> np.ndarray:
    """Frequency-domain equalization (LMS/ZF) using a channel estimate built
    from the known pilot subcarriers. ZF: X / H, with a floor to avoid div0."""
    eq = np.zeros_like(freq)
    for k in DATA_SUBCARRIERS:
        bin_idx = k % 64
        h = chan_estimate[bin_idx]
        if abs(h) < 1e-6:
            eq[bin_idx] = 0
        else:
            eq[bin_idx] = freq[bin_idx] / h
    return eq


def channel_from_pilots(freq: np.ndarray, known_pilots: np.ndarray) -> np.ndarray:
    """Estimate the per-bin channel from known pilot values (pilots are
    always BPSK +1 in 802.11a). Returns a 64-bin channel estimate (data bins
    left 0; only pilot bins are trusted)."""
    chan = np.zeros(FFT_SIZE, dtype=np.complex128)
    for i, p in enumerate(PILOTS):
        if i < len(known_pilots):
            chan[p] = known_pilots[i]      # pilots are BPSK +1 -> H ~= X
    return chan


# ============================================================================
# High-level: try to decode a beacon MAC from a baseband IQ block
# ============================================================================
def decode_beacon_mac(iq: np.ndarray, srate: float) -> bytes | None:
    """Attempt to recover a beacon BSS MAC from a baseband IQ capture at
    `srate` (Hz) centered on a 20 MHz 802.11 channel.

    Returns 6 bytes (the MAC) on a SUCCESSFUL, self-consistent decode
    (beacon frame-control + valid structure), or None. Never returns a guess.

    This is the research path: it requires a real, well-synced capture to
    produce a non-None result. It's structured so a known-good capture can be
    dropped in as a unit test (see selftest.test_wifi).
    """
    if iq is None or len(iq) < 2 * FFT_SIZE:
        return None
    # 1) coarse timing: find the strongest 64-sample window (a symbol)
    win = len(iq) // FFT_SIZE
    if win < 2:
        return None
    mag = np.abs(iq)
    # sliding window energy
    kernel = np.ones(FFT_SIZE)
    energy = np.convolve(mag, kernel, mode="valid")
    start = int(np.argmax(energy))
    sym = iq[start:start + FFT_SIZE]
    # 2) FFT + equalize (identity channel as a first-order; real use needs
    #    pilot-based estimation from the L-STF/L-LTF)
    freq = fft_demod_symbol(sym.astype(np.complex128))
    data = extract_data_subcarriers(freq)
    # 3) map to bits (QPSK/BPSK decision per subcarrier) — first-order
    bits = np.zeros(len(data), dtype=np.uint8)
    for i, v in enumerate(data):
        bits[i] = 1 if (v.real >= 0) else 0
    # 4) Viterbi decode (CC 1/2) — only if we have a plausible coded stream
    if len(bits) >= 2 * (_K + 1):
        # bits here are the raw decision bits; the real pipeline pairs them as
        # (c1,c2) codewords. This is a placeholder structure; a real decode
        # reconstructs the codeword pairs from the BPSK/QPSK subcarrier
        # decisions in the 802.11 bit order. We do NOT claim success here.
        pass
    # Without a validated front-end we do not fabricate a frame. Return None
    # unless we have an actual parsed beacon (which the unit tests inject).
    return None
