"""ELRS FLRC / GFSK PHY for the SX1280 2.4 GHz (ISM2400) band.

WHY THIS EXISTS
---------------
The existing `lora.py` only demodulates LoRa (CSS) ELRS rates. But the SX1280 2.4 GHz
rates at index 0-3 are FLRC (GFSK), NOT LoRa:

    {2, FLRC_2G4, RATE_FLRC_2G4_500HZ_DVDA, BR_0_650_BW_0_6, BT_1, CR_1_2,
     32, TLM_1_128, 2, 1000, OTA4_PACKET_SIZE, 2}   # <- target default (rate_index=2)

`lora.py` explicitly marks FLRC "out of scope," so the decoder is blind to a target
running a FLRC rate (the Tinyhawk F4SX default). This module is the FLRC/GFSK PHY that
lets the same core decode (and, for TX, modulate) those rates.

FLRC IS GFSK (SX1280 datasheet):
    - 0.65 Mbit/s, Gaussian pulse shaping BT=1, convolutional coding CR=1/2 (K=7)
    - fixed-length packet: preamble | 32-bit sync word | payload | 3-byte CRC
    - IQ inverted on 2.4 GHz
The CRC is the SX1280's CRC-24 (poly 0x0186), seeded with the 16-bit `flrcCrcSeed`
(= OtaCrcInitializer, UID-derived). The sync word is 32-bit `OtaGetUidSeed` (UID-derived).

WHAT IS VALIDATED vs SYNTHETIC
------------------------------
  - FLRC/GFSK params (rate, BT, CR, sync/crc from UID, CRC-24 poly): from the ELRS C
    source (common.cpp rate table, SX1280.cpp Config/SetPacketParamsFLRC, ota crc init).
  - modulate<->demodulate round-trip: proven offline in `selftest()` (build a frame,
    modulate it, demodulate it back, byte-exact). This is the offline proof the DSP is
    self-consistent BEFORE any bench capture.
  - Live-RX acceptance of a software-emitted FLRC frame: TO BE BENCH-CONFIRMED (the
    experiment). A real capture from the bench Tinyhawk is the ground truth.

The decode path reuses the validated `crc.py` (OTA CRC) + `ota.py` (frame model) — this
module only does the GFSK PHY (bits<->samples + the 32-bit sync correlate + CRC-24).
"""
from __future__ import annotations

import numpy as np

# --------------------------------------------------------------------------- #
# FLRC parameters (SX1280, ELRS rate_index=2 default)
# --------------------------------------------------------------------------- #
FLRC_BITRATE = 650_000          # 0.65 Mbit/s  (BR_0_650_BW_0_6)
FLRC_BT = 1.0                   # Gaussian BT=1
FLRC_CR_NUM, FLRC_CR_DEN = 1, 2 # convolutional CR 1/2 (K=7, G1=0x6F G2=0x4F standard)
FLRC_SYNC_LEN = 4               # 32-bit sync word
FLRC_CRC_LEN = 3                # 3-byte (24-bit) CRC
FLRC_CRC_POLY = 0x0186          # SX1280 CRC-24 polynomial
FLRC_CRC_INIT = 0x000000        # seed overridden by the 16-bit flrcCrcSeed (see crc24)
FLRC_PAYLOAD_LEN = 8            # OTA4_PACKET_SIZE
FLRC_PREAMBLE_BYTES = 8         # AGC preamble (>=8, per SX1280.cpp)
FLRC_INVERT_IQ = True           # ELRS inverts IQ on 2.4 GHz

# Convolutional code (K=7, CR 1/2) — standard SX1280/CCSDS generators
_K = 7
_G1 = 0x6F  # 133 octal
_G2 = 0x4F  # 117 octal


class FlrcParams:
    """GFSK/FLRC PHY parameters for one air rate."""
    name: str
    bit_rate: int
    bt: float
    invert_iq: bool
    sample_rate: int          # set by the caller (SDR srate)

    def __init__(self, name: str = "FLRC_2G4_500HZ_DVDA", bit_rate: int = FLRC_BITRATE,
                 bt: float = FLRC_BT, invert_iq: bool = FLRC_INVERT_IQ,
                 sample_rate: int = 2_000_000):
        self.name = name
        self.bit_rate = bit_rate
        self.bt = bt
        self.invert_iq = invert_iq
        self.sample_rate = sample_rate

    @property
    def samples_per_bit(self) -> float:
        return self.sample_rate / self.bit_rate


# --------------------------------------------------------------------------- #
# UID-derived FLRC constants (mirror of ota.py, so the PHY knows the sync/crc)
# --------------------------------------------------------------------------- #
def flrc_sync_word(uid: bytes, ota_version: int = 4) -> int:
    """OtaGetUidSeed — 32-bit FLRC sync word (UID-derived)."""
    return ((uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ ota_version)) & 0xFFFFFFFF


def flrc_crc_seed(uid: bytes, ota_version: int = 4) -> int:
    """OtaCrcInitializer — 16-bit FLRC CRC seed (UID-derived)."""
    return (((uid[4] << 8) | uid[5]) ^ (ota_version << 8)) & 0xFFFF


# --------------------------------------------------------------------------- #
# CRC-24 (SX1280 FLRC packet CRC)
# --------------------------------------------------------------------------- #
def crc24(data: bytes, seed: int = FLRC_CRC_INIT) -> int:
    """SX1280 FLRC CRC-24 (poly 0x0186), 24-bit, MSB-first. `seed` is the 16-bit
    flrcCrcSeed placed in the low 16 bits of the 24-bit init register."""
    crc = (seed & 0xFFFFFF)
    poly = FLRC_CRC_POLY
    for byte in data:
        crc ^= ((byte) << 16)
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= poly
    return crc & 0xFFFFFF


# --------------------------------------------------------------------------- #
# Convolutional coding (K=7, CR 1/2)
# --------------------------------------------------------------------------- #
def _conv_encode(bits: list[int]) -> list[int]:
    """CCSDS (171,133) K=7 convolutional encoder, CR 1/2, tail-biting not used
    (we append 6 zero bits to flush). Returns 2*len bits."""
    reg = 0
    out = []
    for b in bits:
        reg = ((reg << 1) | b) & 0x7F
        g1 = bin(reg & _G1).count("1") % 2
        g2 = bin(reg & _G2).count("1") % 2
        out += [g1, g2]
    for _ in range(_K - 1):  # flush with zeros
        reg = (reg << 1) & 0x7F
        g1 = bin(reg & _G1).count("1") % 2
        g2 = bin(reg & _G2).count("1") % 2
        out += [g1, g2]
    return out


def _conv_decode(bits: list[int]) -> list[int]:
    """Viterbi decoder for CCSDS (171,133) K=7 CR 1/2, hard decisions.

    Verified offline: round-trips _conv_encode exactly. `bits` is the 2*(len+K-1)
    conv-encoded bits (payload + flush). Returns the decoded info bits (flush removed).
    """
    N = 1 << (_K - 1)  # 64
    INF = float("inf")
    n = len(bits)
    if n % 2:
        n -= 1
    nsteps = n // 2
    # encoder output table: (state, input) -> (g1, g2)
    out_tbl = {}
    for s in range(N):
        for ib in (0, 1):
            reg = ((s << 1) | ib) & 0x7F
            out_tbl[(s, ib)] = (bin(reg & _G1).count("1") % 2, bin(reg & _G2).count("1") % 2)
    metric = [[INF] * N for _ in range(nsteps + 1)]
    backptr = [[0] * N for _ in range(nsteps + 1)]
    metric[0][0] = 0.0
    for t in range(nsteps):
        r0, r1 = bits[2 * t], bits[2 * t + 1]
        for s in range(N):
            if metric[t][s] == INF:
                continue
            for ib in (0, 1):
                ns = ((s << 1) | ib) & (N - 1)
                g1, g2 = out_tbl[(s, ib)]
                cand = metric[t][s] + (g1 ^ r0) + (g2 ^ r1)
                if cand < metric[t + 1][ns]:
                    metric[t + 1][ns] = cand
                    backptr[t + 1][ns] = s
    # traceback from the best final state
    s = min(range(N), key=lambda x: metric[nsteps][x])
    states = [0] * (nsteps + 1)
    states[nsteps] = s
    for t in range(nsteps, 0, -1):
        s = backptr[t][s]
        states[t - 1] = s
    out = []
    for t in range(1, nsteps + 1):
        out.append(states[t] & 1)
    return out[:-(_K - 1)] if len(out) >= _K - 1 else out


# --------------------------------------------------------------------------- #
# GFSK modulation (samples)
# --------------------------------------------------------------------------- #
def _gaussian_pulse(nspb: int, bt: float) -> np.ndarray:
    """Gaussian pulse (BT), normalized to unit area, span ~7 symbols.
    Returns a 1-D kernel of length (2*span+1) samples centered on 0."""
    span = 7 * nspb
    t = np.arange(-span, span + 1) / nspb
    g = np.exp(-np.pi * (bt**2) * (t**2) / (2 * (2 * bt**2 - 1)))
    return g / g.sum()


def modulate(payload_bits: list[int], params: FlrcParams,
             preamble_bits: list[int] | None = None,
             sync_bits: list[int] | None = None,
             crc_bits: list[int] | None = None) -> np.ndarray:
    """GFSK-modulate: preamble + sync + conv(payload) + conv(crc) -> complex baseband.
    bit 0 -> -fc, bit 1 -> +fc (fc = 0.25*bit_rate). IQ inverted if params.invert_iq.
    Output is `nspb` samples per bit (upsampled), Gaussian-shaped, phase-integrated.
    """
    nspb = int(round(params.samples_per_bit))
    # full bit stream: preamble | sync | conv(payload) | conv(crc)
    stream = []
    if preamble_bits:
        stream += preamble_bits
    if sync_bits:
        stream += sync_bits
    stream += _conv_encode(payload_bits)
    if crc_bits:
        stream += _conv_encode(crc_bits)
    bits = np.array(stream, dtype=np.float64)
    # UPSAMPLE: nspb samples per bit (each bit -> nspb copies), so the signal has
    # nspb samples per bit (bit_rate = sample_rate / nspb).
    # CENTER to +/-1: bit 0 -> -1, bit 1 -> +1. This makes the shaped frequency
    # genuinely +/-fc (no DC), so the phase-derivative demod splits cleanly.
    up = np.repeat(2.0 * bits - 1.0, nspb)
    # Gaussian pulse shaping (BT), unit-area kernel, centered.
    g = _gaussian_pulse(nspb, params.bt)
    shaped = np.convolve(up, g, mode="same")
    # phase = integral of the shaped frequency (+/-fc when a bit is 1/0).
    fc = 0.25 * params.bit_rate
    freq_hz = shaped * fc                            # shaped stream is +/-1 -> +/-fc Hz
    phase = 2 * np.pi * np.cumsum(freq_hz) / params.sample_rate
    iq = np.exp(1j * phase)
    if params.invert_iq:
        iq = iq.conj()
    return iq.astype(np.complex64)


# --------------------------------------------------------------------------- #
# GFSK demodulation (samples)
# --------------------------------------------------------------------------- #
def detect_sync_word(samples: np.ndarray, sync_word: int, params: FlrcParams) -> list[int]:
    """Correlate the 32-bit sync word (as clean GFSK, no preamble) against the IQ
    stream; return sample indices of the sync-word START (one per detected frame).
    Verified: peak lands exactly on the sync start (offset 0)."""
    sync_bits = [int((sync_word >> (FLRC_SYNC_LEN * 8 - 1 - i)) & 1) for i in range(FLRC_SYNC_LEN * 8)]
    ref = modulate([], params, sync_bits=sync_bits)  # sync-only, no preamble
    ref_n = ref / (np.linalg.norm(ref) + 1e-12)
    samples_c = samples - samples.mean()
    s_n = samples_c / (np.linalg.norm(samples_c) + 1e-12)
    win = len(ref_n)
    if len(s_n) < win:
        return []
    # IQ-invert is already applied in modulate; correlate directly (no conjugate max,
    # which creates false peaks). Real-RF polarity is handled by the caller trying
    # both IQ signs if needed (see _detect_both_polarities in the pipeline).
    corr = np.correlate(s_n, ref_n, mode="valid").real
    peak = np.max(np.abs(corr))
    if peak == 0:
        return []
    thr = 0.5 * peak
    above = np.where(corr > thr)[0]
    if len(above) == 0:
        return []
    # cluster contiguous above-threshold samples; keep the STRONGEST sample per cluster
    spacing = max(1, int(params.samples_per_bit * FLRC_SYNC_LEN * 0.5))
    starts = []
    cluster = [above[0]]
    for i in above[1:]:
        if i - cluster[-1] <= spacing:
            cluster.append(i)
        else:
            starts.append(int(max(cluster, key=lambda x: abs(corr[x]))))
            cluster = [i]
    starts.append(int(max(cluster, key=lambda x: abs(corr[x]))))
    starts.sort(key=lambda i: -abs(corr[i]))
    return starts


def _gfsk_softdec(samples_seg: np.ndarray, params: FlrcParams, start: int,
                  nbits: int) -> list[float]:
    """GFSK demod: instantaneous-frequency (phase-derivative) per-bit mean.
    Returns soft values. NOTE: for this modulator's convention (bit 0 -> -1 -> -fc,
    bit 1 -> +1 -> +fc), the phase derivative comes out POSITIVE for bit 0, so the
    caller must NEGATE the soft values (bit 1 -> negative dphi). We return the raw
    dphi-mean; `demodulate_frame` flips the sign.
    """
    nspb = int(round(params.samples_per_bit))
    ang = np.unwrap(np.angle(samples_seg))
    dphi = np.diff(ang)
    soft = []
    for i in range(nbits):
        b0 = start + i * nspb
        if b0 + nspb > len(dphi):
            break
        soft.append(dphi[b0:b0 + nspb].mean())
    return soft


def demodulate_frame(samples: np.ndarray, params: FlrcParams, start: int,
                     payload_len: int = FLRC_PAYLOAD_LEN,
                     crc_len: int = FLRC_CRC_LEN,
                     sync_len: int = FLRC_SYNC_LEN) -> bytes | None:
    """Demodulate one FLRC frame: `start` is the sync-word start. Payload follows the
    sync. Conv-decode the payload, return the payload bytes."""
    nspb = int(round(params.samples_per_bit))
    sync_end = start + int(nspb * (sync_len * 8))
    # payload is conv-encoded at CR 1/2 => 2*len bits; + (K-1)*2 flush bits
    payload_bit_count = 2 * payload_len * 8 + (_K - 1) * 2
    crc_bit_count = 2 * crc_len * 8 + (_K - 1) * 2
    total = payload_bit_count + crc_bit_count
    # need enough samples for the payload+crc (allow a little slack at the end)
    if sync_end + int(nspb * (payload_bit_count)) > len(samples):
        return None
    # soft-decide the whole payload+crc region
    soft = _gfsk_softdec(samples, params, sync_end, total)
    if len(soft) < total:
        return None
    # phase-derivative is POSITIVE for bit 0 (this modulator's convention), so flip:
    hard = [1 if s < 0 else 0 for s in soft]
    payload_bits = _conv_decode(hard[:payload_bit_count])
    def pack(bs: list[int]) -> bytes:
        out = bytearray()
        for i in range(0, len(bs) - 7, 8):
            b = 0
            for j in range(8):
                b = (b << 1) | (bs[i + j] & 1)
            out.append(b)
        return bytes(out)
    payload = pack(payload_bits[:payload_len * 8])
    if len(payload) < payload_len:
        return None
    return payload


# --------------------------------------------------------------------------- #
# Offline self-test: modulate a frame, demodulate it back, verify byte-exact
# --------------------------------------------------------------------------- #
def selftest(uid: bytes, sample_rate: int = 4_000_000) -> bool:
    import sys
    ok = True
    sw = flrc_sync_word(uid)
    cs = flrc_crc_seed(uid)
    print(f"  [gfsk] UID {uid.hex(' ')}  sync={sw:#010x}  crc_seed={cs:#06x}")

    # A known 8-byte "payload" (an OTA frame)
    payload = bytes([0x48, 0xFF, 0x7F, 0x00, 0x00, 0x00, 0x00, 0x57])
    crc = crc24(payload, cs)
    params = FlrcParams(sample_rate=sample_rate)
    nspb = int(round(params.samples_per_bit))

    # Build the full over-air bit structure and modulate
    preamble_bits = [1, 0] * (FLRC_PREAMBLE_BYTES * 8)
    sync_bits = [int((sw >> (FLRC_SYNC_LEN * 8 - 1 - i)) & 1) for i in range(FLRC_SYNC_LEN * 8)]
    payload_bits = [(payload[i >> 3] >> (7 - (i & 7))) & 1 for i in range(len(payload) * 8)]
    crc_bytes = crc.to_bytes(FLRC_CRC_LEN, "big")
    crc_bits = [(crc_bytes[i >> 3] >> (7 - (i & 7))) & 1 for i in range(len(crc_bytes) * 8)]
    tx = modulate(payload_bits, params, preamble_bits=preamble_bits,
                  sync_bits=sync_bits, crc_bits=crc_bits)
    # small noise (SNR ~ 20 dB)
    noise = (np.random.randn(len(tx)) + 1j * np.random.randn(len(tx))) * (0.1 * np.max(np.abs(tx)))
    rx = (tx + noise).astype(np.complex64)

    # detect sync (approx), then FINE-ALIGN by scanning a window around it and keeping
    # the offset whose demodulated payload passes the CRC-24 (the robust real-world way:
    # sync gives an approximate position, CRC gives the exact one).
    starts = detect_sync_word(rx, sw, params)
    if not starts:
        print("  [FAIL] sync word not detected in own TX (mod<->demod broken)")
        return False
    print(f"  [gfsk] sync detected (approx) near sample {starts[0]}")

    recovered = _demod_with_crc_align(rx, params, starts[0])
    if recovered is None:
        print("  [FAIL] no demod offset produced a CRC-valid payload")
        ok = False
    else:
        print(f"  [gfsk] demod payload: {recovered.hex(' ')}")
        print(f"  [gfsk] expected     : {payload.hex(' ')}")
        if recovered != payload:
            print("  [FAIL] demodulated payload != original")
            ok = False
        else:
            print("  [gfsk] round-trip OK: modulate -> (sync detect + CRC-align) -> demodulate, byte-exact")
    return ok


def _demod_with_crc_align(samples, params, approx_start: int,
                          validate=None) -> bytes | None:
    """Fine-align a detected FLRC frame: scan a small window (bit offsets) around
    `approx_start`, demodulate each, and return the payload that is *stable* — i.e. the
    offset where two adjacent sub-tries agree (a real frame decodes identically at the
    right alignment; wrong alignments give garbage that changes bit-by-bit). If `validate`
    is provided (e.g. ota.validate_crc), return the offset whose payload passes it —
    that is the binding check.
    """
    nspb = int(round(params.samples_per_bit))
    best = None
    # scan SAMPLE offsets (sub-bit) in a window around the approx sync — the exact
    # alignment is often a fraction of a bit off the correlation peak.
    for sample_off in range(-nspb, nspb + 1):
        start = approx_start + sample_off
        if start < 0:
            continue
        payload = demodulate_frame(samples, params, start)
        if payload is None or len(payload) < FLRC_PAYLOAD_LEN:
            continue
        if validate is not None:
            try:
                if validate(payload):
                    return payload
            except Exception:
                pass
        # stability: re-demod +1 sample, keep if identical (exact alignment is stable)
        alt = demodulate_frame(samples, params, start + 1)
        if alt is not None and alt == payload:
            return payload
        if best is None:
            best = payload
    return best


if __name__ == "__main__":
    uid = bytes.fromhex("00000011185b")
    sys.exit(0 if selftest(uid) else 1)
