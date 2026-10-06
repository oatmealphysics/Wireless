"""DJI DroneID / OFDM burst detection from IQ.

DroneID is the highest-value UAS target: an *unencrypted* OFDM management burst
DJI drones broadcast ~every 600 ms, carrying the drone's and the OPERATOR's GPS.
Structure (public reverse-engineering): ~10 MHz wide, 1024-point OFDM at
15.36 MSPS, 9 symbols/burst, Zadoff-Chu pilots at symbols 4 & 6.

This module finds candidate bursts, not decoded or authenticated identities.
Both ZC pilots must occur in order, two OFDM symbols apart; they are searched
over a grid of carrier frequency offsets. Payload deframing and error checking
remain outstanding.
Format reference: https://github.com/proto17/dji_droneid (README, Detect ZC
Sequence / OFDM Structure). Pilot positions 4 and 6 are ONE-based; the middle
of the 601-element sequence is nulled before the IFFT.

Pure numpy DSP; capturing wideband IQ from an SDR (≈15.36 MSPS) is the separate,
hardware-gated step.
"""
from __future__ import annotations

# DroneID / LTE-like OFDM at 15.36 MSPS.
FFT_SIZE = 1024
CP_LEN = 72
SAMPLE_RATE = 15_360_000
DETECT_THRESHOLD = 0.5      # CP-autocorrelation metric above this = OFDM present
ZC_LEN = 601                # Zadoff-Chu pilot length (occupied subcarriers)
ZC_ROOTS = (600, 147)       # DroneID ZC roots (symbols 4 & 6, one-based)
ZC_THRESHOLD = 0.25         # matched-filter correlation above this = ZC pilot present
# Carrier frequency offset search. A 15 kHz subcarrier spacing means an uncorrected
# offset beyond ~7.5 kHz breaks the pilot correlation, so the pilots are searched
# on a 7.5 kHz grid; ±22.5 kHz covers about ±26 kHz of combined drone/receiver
# crystal error (≈ 4.5 ppm at 5.8 GHz).
CFO_STEP_HZ = 7_500
CFO_SPAN_HZ = 22_500
_BLOCK = 1 << 18            # FFT block length for the pilot correlation


def available() -> bool:
    try:
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def cp_metric_array(iq, fft_size: int = FFT_SIZE, cp_len: int = CP_LEN):
    """Sliding cyclic-prefix autocorrelation metric (0..~1). Peaks ≈1 where an
    OFDM cyclic prefix aligns; stays low (~1/√cp_len) on noise."""
    import numpy as np
    x = np.asarray(iq)
    n = fft_size
    if x.size < 3 * (n + cp_len):
        return np.zeros(0)
    # Bound temporary arrays while retaining the same valid sliding metric.
    size = x.size - n - cp_len + 1
    result = np.empty(size, dtype=np.float32)
    ones = np.ones(cp_len)
    for start in range(0, size, _BLOCK):
        count = min(_BLOCK, size - start)
        left = x[start:start + count + cp_len - 1]
        right = x[start + n:start + n + count + cp_len - 1]
        prod = left * np.conj(right)
        num = np.abs(np.convolve(prod, ones, "valid"))
        energy = np.abs(left) ** 2 + np.abs(right) ** 2
        den = 0.5 * np.convolve(energy, ones, "valid") + 1e-12
        result[start:start + count] = num / den
    return result


def ofdm_metric(iq, fft_size: int = FFT_SIZE, cp_len: int = CP_LEN) -> float:
    m = cp_metric_array(iq, fft_size, cp_len)
    import numpy as np
    return float(np.max(m)) if m.size else 0.0


def scan_for_bursts(iq, fs: float, fft_size: int = FFT_SIZE, cp_len: int = CP_LEN,
                    threshold: float = DETECT_THRESHOLD) -> list:
    """Find OFDM burst regions in a longer IQ buffer. Returns a list of
    {start_sec, peak_metric} — candidate DroneID/OcuSync bursts."""
    import numpy as np
    m = cp_metric_array(iq, fft_size, cp_len)
    if m.size == 0:
        return []
    step = fft_size + cp_len
    idx = np.where(m > threshold)[0]        # per-symbol CP peaks (~cp_len wide each)
    if idx.size == 0:
        return []
    # Group peaks into bursts: consecutive symbol peaks sit ~step apart, so merge
    # any above-threshold indices closer than 2 symbols; require ≥2 symbols span.
    bursts = []
    start = prev = idx[0]
    for k in idx[1:]:
        if k - prev <= 2 * step:
            prev = k
        else:
            if prev - start >= step:
                bursts.append({"start_sec": round(start / fs, 4),
                               "peak_metric": round(float(m[start:prev + 1].max()), 3)})
            start = prev = k
    if prev - start >= step:
        bursts.append({"start_sec": round(start / fs, 4),
                       "peak_metric": round(float(m[start:prev + 1].max()), 3)})
    return bursts


def zc_sequence(root: int, n_zc: int = ZC_LEN):
    """Frequency-domain Zadoff-Chu sequence (odd length)."""
    import numpy as np
    n = np.arange(n_zc)
    return np.exp(-1j * np.pi * root * n * (n + 1) / n_zc)


def zc_symbol_time(root: int, n_zc: int = ZC_LEN, fft_size: int = FFT_SIZE, cp_len: int = CP_LEN):
    """Time-domain OFDM symbol carrying a ZC sequence on its centre subcarriers,
    with nulled DC and cyclic prefix — the matched-filter reference."""
    import numpy as np
    grid = np.zeros(fft_size, dtype=complex)
    half = n_zc // 2
    idx = (np.arange(-half, half + 1)) % fft_size        # centre subcarriers (incl. DC)
    grid[idx] = zc_sequence(root, n_zc)
    grid[0] = 0.0  # DC is not a transmitted ZC carrier.
    t = np.fft.ifft(grid)
    return np.concatenate([t[-cp_len:], t])


def _correlate_abs(x, ref):
    """|np.correlate(x, ref, "valid")| computed blockwise with FFTs (overlap-save),
    so long captures stay fast."""
    import numpy as np
    m = ref.size
    out = np.empty(x.size - m + 1, dtype=np.float32)
    step = _BLOCK - m + 1
    kernel = np.conj(np.fft.fft(ref, _BLOCK))
    for start in range(0, out.size, step):
        block = x[start:start + _BLOCK]
        full = np.fft.ifft(np.fft.fft(block, _BLOCK) * kernel)
        n = min(step, out.size - start)
        out[start:start + n] = np.abs(full[:n])
    return out


def _pilot_norm(x, ref):
    """Energy normalisation for the pilot correlation at every reference start."""
    import numpy as np
    # Sliding energy without a second long convolution; accumulate in float64.
    energy = np.concatenate(([0.0], np.cumsum(np.abs(x) ** 2, dtype=np.float64)))
    window = np.maximum(energy[ref.size:] - energy[:-ref.size], 0.0)
    return np.sqrt(window * np.sum(np.abs(ref) ** 2)) + 1e-12


def _zc_metric(iq, root: int, norm=None):
    """Normalized pilot correlation at every possible reference start."""
    import numpy as np
    x = np.asarray(iq)
    ref = zc_symbol_time(root)
    if x.size < ref.size:
        return np.zeros(0)
    if norm is None:
        norm = _pilot_norm(x, ref)
    return np.clip(_correlate_abs(x, ref) / norm, 0.0, 1.0)


def _best_pair(first, second):
    """Strongest ordered pilot pair (symbol 4 then symbol 6) near the expected spacing."""
    import numpy as np
    # Symbols 4 and 6 have short prefixes, with one short-prefix symbol between.
    # Allow a few samples of peak drift, but never pair unrelated bursts.
    spacing = 2 * (FFT_SIZE + CP_LEN)
    best_score, pair = 0.0, None
    for gap in range(spacing - 4, spacing + 5):
        if first.size <= gap:
            continue
        scores = np.minimum(first[:-gap], second[gap:])
        i = int(np.argmax(scores))
        score = float(scores[i])
        if score > best_score:
            best_score = score
            pair = {"first_sample": i, "second_sample": i + gap,
                    "spacing_samples": gap,
                    "first_metric": round(float(first[i]), 3),
                    "second_metric": round(float(second[i + gap]), 3)}
    return best_score, pair


def _pilot_search_block(x, fs):
    """Search the pilot pair over the carrier-offset grid.
    Returns (best_score, pair, offset_hz, zc_peak)."""
    import numpy as np
    ref = zc_symbol_time(ZC_ROOTS[0])
    if x.size < ref.size:
        return 0.0, None, 0.0, 0.0
    norm = _pilot_norm(x, ref)      # both references have equal energy; |x| is offset-invariant
    m = ref.size
    size = x.size - m + 1
    step = _BLOCK - m + 1
    starts = range(0, size, step)
    # Each block is transformed once. An offset that is a whole number of FFT bins
    # is then a rotation of that spectrum (exact for the circular block); the phase
    # it leaves between blocks does not change |correlation|.
    spectra = [np.fft.fft(x[s:s + _BLOCK], _BLOCK).astype(np.complex64) for s in starts]
    kernels = [np.conj(np.fft.fft(zc_symbol_time(r), _BLOCK)) for r in ZC_ROOTS]
    best = (0.0, None, 0.0)
    zc_peak = 0.0
    for offset in range(-CFO_SPAN_HZ, CFO_SPAN_HZ + 1, CFO_STEP_HZ):
        shift = offset * _BLOCK / fs
        if abs(shift - round(shift)) > 1e-9:
            raise ValueError("carrier offset grid must fall on whole FFT bins")
        first = np.empty(size, dtype=np.float32)
        second = np.empty(size, dtype=np.float32)
        for s, spec in zip(starts, spectra):
            rolled = np.roll(spec, -int(round(shift)))
            n = min(step, size - s)
            for out, kernel in ((first, kernels[0]), (second, kernels[1])):
                out[s:s + n] = np.abs(np.fft.ifft(rolled * kernel)[:n])
        first = np.clip(first / norm, 0.0, 1.0)
        second = np.clip(second / norm, 0.0, 1.0)
        zc_peak = max(zc_peak, float(first.max()), float(second.max()))
        score, pair = _best_pair(first, second)
        if score > best[0]:
            best = (score, pair, float(offset))
    return best[0], best[1], best[2], zc_peak


def _pilot_search(x, fs):
    """Bound working memory, overlapping blocks to keep every possible pilot pair."""
    ref_size = FFT_SIZE + CP_LEN
    max_gap = 2 * ref_size + 4
    step = _BLOCK - ref_size - max_gap + 1
    best = (0.0, None, 0.0)
    peak = 0.0
    for start in range(0, max(0, x.size - ref_size + 1), step):
        score, pair, offset, block_peak = _pilot_search_block(x[start:start + _BLOCK], fs)
        peak = max(peak, block_peak)
        if pair:
            pair = dict(pair, first_sample=pair["first_sample"] + start,
                        second_sample=pair["second_sample"] + start)
        # Preserve frequency-grid ordering on equal scores, then earliest position.
        better_tie = (pair is not None and best[1] is not None and score == best[0]
                      and (offset, pair["first_sample"]) < (best[2], best[1]["first_sample"]))
        if score > best[0] or better_tie:
            best = (score, pair, offset)
    return best[0], best[1], best[2], peak


def zc_correlate(iq, root: int) -> float:
    """Peak single-pilot correlation; by itself this does not identify DroneID."""
    m = _zc_metric(iq, root)
    return float(m.max()) if m.size else 0.0


def detect(iq, fs: float = SAMPLE_RATE) -> dict:
    """Find an ordered pilot pair at the expected spacing (candidate only).

    References assume 15.36 MSPS. Resample other recordings before calling;
    silently accepting another rate would produce misleading results.
    """
    import numpy as np
    if not np.isfinite(fs) or fs != SAMPLE_RATE:
        raise ValueError(f"DroneID detection requires {SAMPLE_RATE} samples/second")
    x = np.asarray(iq)
    if x.ndim != 1 or not np.all(np.isfinite(x)):
        raise ValueError("IQ must be a finite one-dimensional sample array")
    x = x.astype(np.complex64, copy=False)
    metric = ofdm_metric(x)
    ofdm = metric >= DETECT_THRESHOLD
    bursts = scan_for_bursts(x, fs)
    # The ordered pilot pair is the evidence; the cyclic-prefix metric is reported
    # but no longer gates it, because the pilots survive noise the metric cannot.
    best_score, pair, offset_hz, zc_peak = _pilot_search(x, fs)
    candidate = best_score >= ZC_THRESHOLD
    if candidate:
        note = ("DJI DroneID candidate — both Zadoff-Chu pilots match in order "
                f"at the expected spacing (carrier offset ≈ {offset_hz / 1000:+.1f} kHz "
                "corrected). Payload not decoded or CRC-validated; "
                "drone and operator locations are unavailable.")
    elif ofdm:
        note = ("OFDM-like correlation detected; no matching DroneID pilot pair. "
                "Wi-Fi or a periodic carrier can also trigger this metric.")
    else:
        note = "No DroneID pilot pair detected in this capture."
    return {
        "detected": bool(ofdm or candidate),
        "droneid": bool(candidate),  # compatibility: candidate, NOT confirmation
        "classification": "droneid_candidate" if candidate else "ofdm_like" if ofdm else "none",
        "payload_validated": False,
        "metric": round(metric, 3),
        "zc_peak": round(zc_peak, 3),
        "pilot_pair": pair if candidate else None,
        "cfo_hz": offset_hz if candidate else None,
        "bursts": bursts,
        "fft_size": FFT_SIZE,
        "note": note,
    }
