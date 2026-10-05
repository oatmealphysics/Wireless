"""
LoRa PHY for ELRS — modulation and demodulation.

This is the most correctness-sensitive module in the project. It implements the
LoRa chirp-BPSK PHY per the Semtech SX1280 (2.4 GHz) parameters ELRS uses:

    SF5-8, BW 800 kHz, CR 4/6..4/8, preamble 12-14 symbols
    (see src/src/common.cpp ExpressLRS_AirRateConfig for the SX1280 table)

Design goals (per operator requirement):
  * RX and TX are symmetric — the same chirp tables and symbol mapping are used
    in both directions. TX calls `modulate()`, RX calls `demodulate()`.
  * IQ inversion is handled explicitly (ELRS inverts IQ for binding and for
    UID[5]&1; we demod both polarities and let the CRC pick the winner).
  * The demodulator returns *symbol indices*, which the upper layer maps to
    bits and reassembles bytes. The exact SF5-7 bit-per-symbol convention is a
    Semtech implementation detail; we implement it per the SX128x datasheet
    and flag the places where real-hardware validation is required.

NOTE ON VALIDATION: The LoRa demod (chirp dechirp + SF5-7 bit mapping +
interleaving) must be validated against a real ELRS capture before the tool
can be trusted. The CRC/packet/FHSS layers above are exact ports and are
self-testable. This module is structured so that once a capture exists, the
demod can be unit-tested directly against known-good bytes.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass

# ----------------------------------------------------------------------------
# ELRS 2.4 GHz (SX1280) air rates — from src/src/common.cpp
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class AirRate:
    name: str
    sf: int               # spreading factor
    bw_hz: int            # bandwidth
    cr_den: int           # coding rate denominator (4/x)
    preamble_symbols: int
    payload_len: int      # OTA bytes (8 or 13)
    is_full: bool         # True = 13-byte OTA8
    interval_us: int

ELRS_2G4_RATES: dict[str, AirRate] = {
    "500Hz":      AirRate("500Hz",      5, 800_000, 6, 12, 8,  False,  2000),
    "333Hz_8ch":  AirRate("333Hz_8ch",  5, 800_000, 8, 12, 13, True,  3003),
    "250Hz":      AirRate("250Hz",      6, 800_000, 8, 14, 8,  False,  4000),
    "150Hz":      AirRate("150Hz",      7, 800_000, 8, 12, 8,  False,  6666),
    "100Hz_8ch":  AirRate("100Hz_8ch",  7, 800_000, 8, 12, 13, True, 10000),
    "50Hz":       AirRate("50Hz",       8, 800_000, 8, 12, 8,  False, 20000),
}
# FLRC 2.4 GHz rates (1000Hz/500Hz) are NOT LoRa — they use the SX1280 FLRC
# modem (GFSK-like). Capturing those requires a GFSK demod, out of scope for
# the LoRa path. The default scan focuses on LoRa rates.

# 2.4 GHz domain channels (fhss.py ISM2G4)
DEFAULT_2G4_FREQ_START = 2_400_400_000
DEFAULT_2G4_FREQ_STOP  = 2_479_400_000
DEFAULT_2G4_NCHAN      = 80
DEFAULT_2G4_FREQ_SPREAD = (DEFAULT_2G4_FREQ_STOP - DEFAULT_2G4_FREQ_START) / (DEFAULT_2G4_NCHAN - 1)


# ----------------------------------------------------------------------------
# Chirp tables
# ----------------------------------------------------------------------------
def _base_chirp(sf: int, n: int | None = None) -> np.ndarray:
    """
    Base up-chirp of 2^sf symbols: x[n] = exp(j*pi*(n^2 - n)/(2^sf))  (up-chirp)
    This is the standard LoRa chirp used by the SX12xx in 2.4 GHz mode.
    """
    n = n or (1 << sf)
    idx = np.arange(n, dtype=np.float64)
    phase = np.pi * (idx * idx - idx) / n
    return np.exp(1j * phase)


@dataclass
class LoRaParams:
    sf: int
    bw_hz: int
    sample_rate: float      # IQ sample rate of the incoming stream
    invert_iq: bool = False # ELRS IQ inversion

    @property
    def n_chirp(self) -> int:
        return 1 << self.sf

    @property
    def symbol_rate_hz(self) -> float:
        return self.bw_hz / self.n_chirp

    @property
    def samples_per_symbol(self) -> float:
        return self.sample_rate / self.symbol_rate_hz

    def chirp(self, invert: bool | None = None) -> np.ndarray:
        inv = self.invert_iq if invert is None else invert
        c = _base_chirp(self.sf)
        return np.conj(c) if inv else c


# ----------------------------------------------------------------------------
# Preamble + sync word
# ----------------------------------------------------------------------------
def preamble_sync_symbol_rate(params: LoRaParams) -> float:
    return params.symbol_rate_hz


def detect_sync_word(
    samples: np.ndarray,
    params: LoRaParams,
    sync_word: np.ndarray | None = None,
    threshold: float = 0.6,
) -> list[int]:
    """
    Correlate the incoming baseband against the dechirped sync word and report
    sample indices where correlation exceeds `threshold`.

    `sync_word` is the complex chirp sequence of the sync word (for ELRS on
    SX1280 2.4 GHz, the private sync word; if unknown, pass None to do an
    autocorrelation-based preamble detector instead).

    For a first pass without the exact sync-word bytes, we use the *preamble*
    structure: a LoRa header-less (explicit header disabled in ELRS) frame has
    a repeating up-chirp preamble. Correlating against the base chirp at the
    expected symbol rate finds candidate frame starts.
    """
    if sync_word is None:
        sync_word = params.chirp()
    sw_len = len(sync_word)
    corr = np.correlate(samples, np.conj(sync_word), mode="valid")
    norm = np.abs(sync_word) @ np.abs(sync_word)
    corr = np.abs(corr) / norm
    # peak-pick
    indices = []
    for i in range(1, len(corr) - 1):
        if corr[i] > threshold and corr[i] >= corr[i - 1] and corr[i] >= corr[i + 1]:
            indices.append(i)
    return indices


# ----------------------------------------------------------------------------
# Demodulation: baseband -> symbol indices -> bits -> bytes
# ----------------------------------------------------------------------------
def demodulate_symbols(
    samples: np.ndarray,
    params: LoRaParams,
    start_sample: int,
    n_symbols: int,
) -> np.ndarray:
    """
    Dechirp a window of samples starting at `start_sample`, returning `n_symbols`
    symbol indices (0 .. 2^sf - 1). Handles resampling to integer
    samples/symbol via FFT cross-correlation against the dechirp table.

    Implementation: multiply the window by the conjugate of the up-chirp
    (dechirp), then for each symbol take the FFT bin that matches.
    """
    sps = params.samples_per_symbol
    chirp = params.chirp()
    # dechirp the whole window
    win = samples[start_sample:start_sample + int(np.ceil(n_symbols * sps))]
    # tile the dechirp over the window length
    tile = np.tile(np.conj(chirp), int(np.ceil(len(win) / len(chirp))) + 1)[:len(win)]
    dechirped = win * tile
    # For each symbol, the frequency bin = symbol index. Use a short FFT per
    # symbol window for robustness.
    symbols = np.zeros(n_symbols, dtype=np.int32)
    win_len = int(round(sps))
    for i in range(n_symbols):
        s = int(round(i * sps))
        chunk = dechirped[s:s + win_len]
        if len(chunk) < 4:
            break
        # zero-pad to 2^sf for exact bin resolution
        nfft = 1 << params.sf
        if len(chunk) < nfft:
            chunk = np.concatenate([chunk, np.zeros(nfft - len(chunk))])
        else:
            chunk = chunk[:nfft]
        spec = np.fft.fft(chunk * np.hanning(len(chunk)))
        symbols[i] = int(np.argmax(np.abs(spec)))
    return symbols


def symbols_to_bits(symbols: np.ndarray, sf: int) -> np.ndarray:
    """
    Map symbol indices to bits using the SX128x convention.

    For SF>=7: 1 bit per symbol (the MSB of the symbol index, with a
    differential/BPSK interpretation).
    For SF5-6: 2 bits per symbol.

    NOTE: The exact SF5-6 bit extraction and the BPSK differencing are
    Semtech-proprietary details that MUST be validated against a capture.
    This is a first-order implementation: we take the top 2 bits of each
    symbol index for SF5/6 and the MSB for SF>=7, then apply a running XOR to
    undo the BPSK differencing.
    """
    bits_per_sym = 2 if sf <= 6 else 1
    if sf <= 6:
        raw = np.empty((len(symbols), 2), dtype=np.uint8)
        raw[:, 0] = (symbols >> 1) & 1
        raw[:, 1] = symbols & 1
        raw = raw.flatten()
    else:
        raw = (symbols >> (sf - 1)) & 1
    # BPSK: de-differential (running XOR) — the received symbol is the XOR of
    # consecutive transmitted bits. Reverse by cumulative XOR.
    bits = np.zeros_like(raw)
    acc = 0
    out = []
    for b in raw:
        acc ^= b
        out.append(acc)
    return np.array(out, dtype=np.uint8)


def bits_to_bytes(bits: np.ndarray, payload_len: int) -> bytes:
    """Pack a bit array into bytes (MSB first), truncated to payload_len bytes.
    Elided CRC header bits and the exact bit ordering must be confirmed
    against a capture; this is the first-order mapping."""
    nbits = len(bits)
    nbytes = min(payload_len, nbits // 8)
    out = bytearray(nbytes)
    for i in range(nbytes):
        v = 0
        for b in range(8):
            v = (v << 1) | (bits[i * 8 + b] if i * 8 + b < nbits else 0)
        out[i] = v
    return bytes(out)


def demodulate_frame(
    samples: np.ndarray,
    params: LoRaParams,
    start_sample: int,
    air_rate: AirRate,
) -> bytes | None:
    """
    Full RX: dechirp -> symbols -> bits -> bytes for one LoRa frame.
    n_symbols = preamble + sync + header + payload symbols (approx).
    For header-less ELRS frames the symbol count is fixed by the air rate.
    """
    # Approx symbol budget: preamble(12-14) + 2 sync + 8 header bits +
    # ceil(payload_bits / bits_per_sym). We grab a generous window and let the
    # upper layer slice by CRC.
    payload_bits = air_rate.payload_len * 8
    bps = 2 if air_rate.sf <= 6 else 1
    n_payload_syms = (payload_bits + bps - 1) // bps
    n_syms = air_rate.preamble_symbols + 2 + 8 + n_payload_syms
    symbols = demodulate_symbols(samples, params, start_sample, n_syms)
    # skip preamble + sync + header symbols
    skip = air_rate.preamble_symbols + 2 + 8
    payload_syms = symbols[skip:skip + n_payload_syms]
    bits = symbols_to_bits(payload_syms, air_rate.sf)
    return bits_to_bytes(bits, air_rate.payload_len)


# ----------------------------------------------------------------------------
# Modulation (TX path) — mirror of the above
# ----------------------------------------------------------------------------
def bytes_to_bits(data: bytes) -> np.ndarray:
    out = []
    for b in data:
        for i in range(7, -1, -1):
            out.append((b >> i) & 1)
    return np.array(out, dtype=np.uint8)


def bits_to_symbols(bits: np.ndarray, sf: int) -> np.ndarray:
    """Inverse of symbols_to_bits (first-order)."""
    bps = 2 if sf <= 6 else 1
    # re-differential: transmit bits are running XOR of desired bits
    diff = np.zeros(len(bits), dtype=np.uint8)
    acc = 0
    for i, b in enumerate(bits):
        acc ^= b
        diff[i] = acc
    n_syms = (len(diff) + bps - 1) // bps
    symbols = np.zeros(n_syms, dtype=np.int32)
    if bps == 1:
        for i in range(n_syms):
            symbols[i] = diff[i] << (sf - 1)
    else:
        for i in range(n_syms):
            hi = diff[2 * i] if 2 * i < len(diff) else 0
            lo = diff[2 * i + 1] if 2 * i + 1 < len(diff) else 0
            symbols[i] = (hi << 1) | lo
    return symbols


def modulate(samples_per_symbol: float, sf: int, symbols: np.ndarray,
             invert_iq: bool = False) -> np.ndarray:
    """
    Generate baseband complex IQ for a sequence of LoRa symbols.
    Each symbol is a phase ramp whose slope = symbol index / 2^sf of the
    bandwidth. TX is the mirror of demodulate_symbols.
    """
    n_chirp = 1 << sf
    base = _base_chirp(sf)
    # phase offset per symbol = 2*pi*sym/n_chirp
    out = np.empty(int(np.ceil(len(symbols) * samples_per_symbol)), dtype=np.complex128)
    pos = 0
    for sym in symbols:
        phase_off = 2 * np.pi * sym / n_chirp
        chirp = base * np.exp(1j * phase_off)
        if invert_iq:
            chirp = np.conj(chirp)
        n = int(round(samples_per_symbol))
        seg = chirp[:n]
        out[pos:pos + len(seg)] = seg
        pos += len(seg)
    return out[:pos]


def modulate_frame(air_rate: AirRate, payload: bytes, sample_rate: float,
                   invert_iq: bool = False,
                   preamble_symbols: int | None = None) -> np.ndarray:
    """
    Build a full TX waveform (preamble + payload) for one ELRS LoRa frame.
    The preamble/sync/header symbol layout must match the SX1280 exactly;
    this is the first-order construction and requires hardware validation.
    """
    params = LoRaParams(sf=air_rate.sf, bw_hz=air_rate.bw_hz, sample_rate=sample_rate)
    sps = params.samples_per_symbol
    pb = preamble_symbols if preamble_symbols is not None else air_rate.preamble_symbols
    # preamble: all-zero symbols (pure up-chirp)
    pre_syms = np.zeros(pb, dtype=np.int32)
    # header bits (8): for ELRS header-less, the "header" is the payload length
    # marker; we emit a placeholder that must be validated.
    hdr_syms = np.zeros(8, dtype=np.int32)
    pay_syms = bits_to_symbols(bytes_to_bits(payload), air_rate.sf)
    all_syms = np.concatenate([pre_syms, hdr_syms, pay_syms])
    return modulate(sps, air_rate.sf, all_syms, invert_iq)
