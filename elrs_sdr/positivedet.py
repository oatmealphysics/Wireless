"""Positive-identification detectors.

Each protocol gets its OWN detector that fires only on that protocol's actual
RF signature. Nothing is classified by elimination (no default bin). Each
detector returns (score 0..1, metric str) where:
  * score  — higher = more positively identified by THIS detector
  * metric — the raw number the score is derived from, shown in the UI so a
             real capture can later tune the threshold

DESIGN PRINCIPLE (per operator): positively identify, don't default-bin.
A protocol is claimed only when its own detector clears the threshold; a
signal no detector positively claims is honestly "unidentified".

CONFIDENCE / VALIDATION STATE:
  * lora_chirp        — CAPTURE-VALIDATED (the only one; the chirp
                        autocorrelation premise is provable, self-tested).
  * ofdm_preamble     — SYNTHETIC-CALIBRATED. Correct structure (Schmidl-Cox),
                        threshold needs one real 802.11 beacon to confirm.
  * analog_video      — SYNTHETIC-CALIBRATED. Correct structure (line-rate
                        envelope ripple), threshold needs a real 5.8 video.
  * fhss_hop          — SYNTHETIC-CALIBRATED. Correct structure (discrete-freq
                        hopping at a regular rate), needs a real FrSky capture.

Every detector reports its raw metric so a capture can tune the threshold
without re-deriving the math.
"""
from __future__ import annotations
import numpy as np


# ============================================================================
# LoRa / ELRS — CAPTURE-VALIDATED (reference to the dronescan matched-filter)
# ============================================================================
def lora_chirp(samples: np.ndarray, srate: float, sf: int = 7) -> tuple[float, str]:
    """Positively detect a LoRa/ELRS chirp (chirp matched-filter).

    Returns (score 0..1, metric). score is a normalized dB measure: the
    matched-filter peak-to-noise ratio mapped so that >=12 dB -> 1.0.
    The raw dB is the metric (shown in the UI; this is the validated one).
    """
    from .dronescan import lora_chirp_score
    db = lora_chirp_score(samples, srate, sf=sf)
    if db < -90:
        return 0.0, "no chirp"
    score = min(1.0, max(0.0, (db - 6.0) / (12.0 - 6.0)))
    return score, f"chirp {db:.0f}dB"


# ============================================================================
# OFDM / Wi-Fi — SYNTHETIC-CALIBRATED (Schmidl-Cox preamble timing)
# ============================================================================
def _timing_autocorr_power(x: np.ndarray, max_tau: int | None = None) -> np.ndarray:
    """R[tau] = sum_n |x[n]|^2 * |x[n+tau]|^2 (Schmidl-Cox numerator)."""
    power = np.abs(x) ** 2
    N = len(power)
    max_tau = max_tau or N // 2
    n = 1
    while n < 2 * N:
        n <<= 1
    P = np.fft.fft(power, n)
    ac = np.fft.ifft(P * np.conj(P)).real[:N]
    return ac[:max_tau + 1]


def ofdm_preamble(iq: np.ndarray, srate: float) -> tuple[float, str]:
    """
    Positively detect an 802.11 a/g OFDM preamble.

    A real 802.11 preamble has an L-STF: a train of IDENTICAL short training
    symbols. The Schmidl-Cox timing metric M(tau) = R(tau)/sqrt(P1*P2) shows a
    strong, SHARP peak at the OFDM symbol period (0.8us=11a, 4us=11g) with a
    high normalized value (~1 for a periodic preamble; low/broad for noise or
    a LoRa chirp).

    We test both symbol periods, take the best, and score on:
      * M_peak at the symbol period (0..1)
      * sharpness = M_peak / M(1.25*symbol_period)
    metric reports the best M_peak and which period matched, for tuning.
    """
    n = len(iq)
    if n < 4096:
        return 0.0, "too_short"
    candidates = {
        "11a": int(round(srate * 0.8e-6)),
        "11g": int(round(srate * 4.0e-6)),
    }
    best = 0.0
    best_label = "none"
    best_m = 0.0
    power = np.abs(iq) ** 2
    csum = np.concatenate([[0.0], np.cumsum(power)])
    max_tau = min(int(1.6 * max(candidates.values())), n // 2)
    ac = _timing_autocorr_power(iq, max_tau)

    def rp(a, b):
        a = max(0, a); b = min(n, b)
        return float(csum[b] - csum[a]) if b > a else 0.0

    def m_at(t):
        if t < 1 or t >= len(ac):
            return 0.0
        r1 = rp(0, n - t); r2 = rp(t, n)
        return ac[t] / ((r1 * r2) ** 0.5 + 1e-12)

    for label, tau in candidates.items():
        if tau < 16 or tau >= len(ac):
            continue
        w = max(8, tau // 10)
        m_peak = max((m_at(t) for t in range(max(1, tau - w), min(len(ac), tau + w + 1))),
                     default=0.0)
        m_peak = min(1.0, float(m_peak))
        m_off = min(1.0, float(m_at(int(tau * 1.25))))
        sharp = m_peak / (m_off + 1e-9)
        score = 0.0
        if m_peak > 0.4:
            score += 0.6 * min(1.0, (m_peak - 0.4) / 0.4)
        if sharp > 1.5:
            score += 0.4 * min(1.0, (sharp - 1.5) / 3.0)
        if score > best:
            best = score
            best_label = label
            best_m = m_peak
    return min(1.0, best), f"M={best_m:.2f}@{best_label}"


# ============================================================================
# Analog video (5.8) — SYNTHETIC-CALIBRATED (line-rate envelope ripple)
# ============================================================================
def analog_video(iq: np.ndarray, srate: float) -> tuple[float, str]:
    """
    Positively detect 5.8 GHz analog FPV video (NTSC/PAL).

    The tell: analog video is AM; its ENVELOPE is modulated at the video line
    rate (NTSC ~60 Hz, PAL ~50 Hz). A "wide constant blob" (any wideband tx)
    has a FLAT envelope spectrum; real video shows a spectral peak at 50/60 Hz.

    We take the envelope |IQ|, its spectrum, and score on the prominence of a
    50/60 Hz peak above the 1 kHz baseline. metric reports the prominence.
    """
    n = len(iq)
    if n < 4096:
        return 0.0, "too_short"
    env = np.abs(iq) - np.mean(np.abs(iq))
    if env.std() < 1e-9:
        return 0.0, "flat_envelope"
    # 50/60 Hz line rate needs ~1/50 Hz freq resolution = >=0.1s of envelope.
    # At 4 Msps a 4096-sample block is only ~1 ms (500 Hz resolution), so we
    # can only resolve the line rate if the block is long. If the block is too
    # short, report "need_longer_window" (honest) rather than a false 0.
    dur_s = n / srate
    if dur_s < 0.05:
        return 0.0, f"need_longer_window({dur_s*1000:.0f}ms<50ms)"
    nfft = 1 << int(np.ceil(np.log2(n)))
    spec = np.abs(np.fft.rfft(env, nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / srate)
    # 50 Hz and 60 Hz line-rate peaks
    def peak_in(lo, hi):
        m = (freqs >= lo) & (freqs <= hi)
        return float(spec[m].max()) if m.any() else 0.0
    line_peak = max(peak_in(45, 65), peak_in(55, 75))
    # baseline: median of the 1 Hz..1 kHz envelope spectrum (excl. the peaks)
    base_mask = (freqs > 1) & (freqs < 1000) & ~((freqs >= 40) & (freqs <= 80))
    baseline = float(np.median(spec[base_mask])) if base_mask.any() else 0.0
    prominence = line_peak / (baseline + 1e-12)
    score = 0.0
    if prominence > 2.0:
        score += 0.6 * min(1.0, (prominence - 2.0) / 10.0)
    if prominence > 4.0:
        score += 0.4
    return min(1.0, score), f"line_prom={prominence:.1f}"


# ============================================================================
# FHSS — SYNTHETIC-CALIBRATED (discrete-freq hopping at a regular rate)
# ============================================================================
def fhss_hop(freq_track: list[float]) -> tuple[float, str]:
    """
    Positively detect FHSS from a time series of peak frequencies (Hz).

    The tell: FHSS moves the carrier among a SMALL SET of DISCRETE frequencies
    on a REGULAR cadence. A drifting carrier or a single freq does NOT snap to
    a few discrete freqs at a regular interval.

    Score on:
      * distinct-freq count: a few (2..16) discrete freqs (500 kHz quantized)
      * hop regularity: blocks-per-freq are roughly equal
    metric reports the distinct count and regularity, for tuning.
    """
    if len(freq_track) < 4:
        return 0.0, "too_short"
    f = np.array(freq_track, dtype=np.float64)
    q = np.round(f / 500_000.0).astype(int)
    distinct = int(len(np.unique(q)))
    if distinct < 2:
        return 0.0, "single_freq"
    if distinct > 16:
        return 0.0, f"{distinct} freqs (too many)"
    counts = np.bincount(q - q.min())
    mean_c = counts.mean()
    reg = max(0.0, 1.0 - (counts.std() / (mean_c + 1e-9)))
    score = 0.0
    score += 0.5 * min(1.0, distinct / 4.0)
    score += 0.5 * reg
    return min(1.0, score), f"{distinct}freqs reg={reg:.2f}"
