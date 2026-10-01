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

    Two gates (both required for a positive call):
      1. PEAK: the matched-filter peak-to-noise ratio is high. A real LoRa
         preamble scores ~35 dB; an OFDM short-symbol train or pure noise
         scores only ~15-17 dB (their repetition is at the WRONG period).
      2. PERIODICITY: the matched-filter peaks must repeat at the LoRa SYMBOL
         PERIOD (2^sf samples = one base chirp). A real preamble has peaks
         spaced exactly one symbol apart; an OFDM signal's matched-filter
         response is a broad hump (peaks NOT at the symbol period).

    This is the CAPTURE-VALIDATED detector: the chirp autocorrelation premise
    is provable, and the symbol-periodic peak spacing is the positive tell
    that separates a genuine LoRa preamble from a repetitive OFDM/noise
    signal (which is what was causing the all-Wi-Fi-labels-as-LoRa bug).

    Returns (score 0..1, metric).
    """
    from .dronescan import lora_chirp_score, _fft_correlate
    from .lora import _base_chirp
    db = lora_chirp_score(samples, srate, sf=sf)
    if db < -90:
        return 0.0, "no chirp"
    # gate 1: peak (a real preamble is ~35 dB; OFDM/noise ~15-17 dB)
    # score ramps from 18 dB (0.0) to 34 dB (1.0)
    peak_score = min(1.0, max(0.0, (db - 18.0) / (34.0 - 18.0)))
    if peak_score <= 0.0:
        return 0.0, f"chirp {db:.0f}dB (too weak)"
    # gate 2: symbol-periodic peaks
    nsym = 1 << sf
    if len(samples) < 4 * nsym:
        # can't resolve periodicity on such a short window
        return peak_score * 0.5, f"chirp {db:.0f}dB (short win)"
    ref = _base_chirp(sf).astype(np.complex128)
    corr = np.abs(_fft_correlate(samples, ref))
    if corr.size < 16:
        return peak_score * 0.5, f"chirp {db:.0f}dB (short win)"
    thr = corr.max() * 0.5
    idx = np.where(corr > thr)[0]
    if len(idx) < 3:
        return peak_score * 0.5, f"chirp {db:.0f}dB (sparse peaks)"
    d = np.diff(idx)
    d = d[d > 0]
    if d.size == 0:
        return peak_score * 0.5, f"chirp {db:.0f}dB (no spacing)"
    spacing = float(np.median(d))
    # periodicity: median peak spacing should be ~1 symbol (nsym), within a
    # factor of ~2 (allow for a few missing/merged peaks)
    ratio = spacing / nsym
    if 0.5 <= ratio <= 2.0:
        period_score = 1.0
    elif 0.25 <= ratio < 0.5 or 2.0 < ratio <= 4.0:
        period_score = 0.5          # half-spacing (every-other peak) or 2x
    else:
        period_score = 0.0          # wrong period (OFDM hump / noise)
    score = peak_score * (0.5 + 0.5 * period_score)
    if period_score == 0.0:
        # not symbol-periodic -> do NOT claim LoRa, even if the peak is high
        return 0.0, f"chirp {db:.0f}dB spacing={spacing:.0f}≠{nsym}"
    return min(1.0, score), f"chirp {db:.0f}dB spacing={spacing:.0f}≈{nsym}"


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
        # A real 802.11 L-STF is 10 IDENTICAL short symbols -> M near 1.0 at
        # the symbol period AND very sharp (the metric drops off a cliff just
        # past the period). Broadband noise / a wideband blob gives M ~ 0.3-0.7
        # with low sharpness and it FLICKERS block-to-block, which was creating
        # spurious "Wi-Fi/OFDM" rows on the 2026-10-01 laptop capture. So
        # require a strong M AND a sharp peak before scoring.
        score = 0.0
        if m_peak > 0.6:
            score += 0.6 * min(1.0, (m_peak - 0.6) / 0.4)
        if sharp > 2.0:
            score += 0.4 * min(1.0, (sharp - 2.0) / 3.0)
        if score > best:
            best = score
            best_label = label
            best_m = m_peak
    # gate 1 result: OFDM symbol-timing present (Schmidl-Cox). This is the
    # fast per-block detector. The POSITIVE 802.11 confirmation (gate 2:
    # decode + validate the L-SIG) is done by the classifier on the rolling IQ
    # buffer (one block is too short for the full preamble+L-SIG) — see
    # DroneScanner._classify_positive / _lsig_scan. So this returns the
    # timing score; a high score means "OFDM-shaped, worth an L-SIG check".
    timing_score = min(1.0, best)
    if timing_score < 0.3:
        return 0.0, f"no OFDM timing (M={best_m:.2f})"
    return 0.5 + 0.5 * timing_score, f"OFDM timing M={best_m:.2f}@{best_label}"


# ============================================================================
# Analog video (5.8) — SYNTHETIC-CALIBRATED (line-rate envelope ripple)
# ============================================================================
def analog_video(iq: np.ndarray, srate: float) -> tuple[float, str]:
    """
    Positively detect 5.8 GHz analog FPV video (NTSC/PAL).

    The tell: analog video is AM; its ENVELOPE is modulated at the video line
    rate (NTSC ~60 Hz, PAL ~50 Hz) AROUND A STEADY CARRIER. Two gates:
      1) CONTINUITY: real video is a continuous AM carrier — the signal is
         present (above the noise floor) for a HIGH fraction of the window.
         Impulsive noise (radar/spikes/clicks) is present only a few % of the
         time, so this rejects the false positives the 2026-10-01 capture
         exposed (17 sub-ms pulses tripped the line-rate check).
      2) LINE-RATE: a narrow 50/60 Hz peak in the envelope spectrum, well
         above a flat baseline (a real video line rate is a stable line, not
         broadband impulse energy).
    """
    n = len(iq)
    if n < 4096:
        return 0.0, "too_short"
    env = np.abs(iq) - np.mean(np.abs(iq))
    if env.std() < 1e-9:
        return 0.0, "flat_envelope"
    dur_s = n / srate
    if dur_s < 0.05:
        return 0.0, f"need_longer_window({dur_s*1000:.0f}ms<50ms)"

    # --- GATE 1: continuity (real video is a steady carrier, not impulses) ---
    # the signal must be present (above a robust noise floor) for most of the
    # window. Use a running window so a few gaps don't kill a real carrier,
    # but a mostly-quiet capture (impulsive noise) fails this. The floor is
    # peak-relative (not percentile) so a sparse impulsive signal — where the
    # 30th percentile is near zero and would mark everything "present" — is
    # correctly seen as low-presence.
    mag = np.abs(iq)
    peak = float(mag.max()) + 1e-12
    floor = 0.1 * peak          # present = at least 10% of the peak envelope
    win = max(64, int(srate * 1e-3))          # ~1 ms sub-windows
    nb = n // win
    if nb < 10:
        return 0.0, f"need_more_blocks({nb})"
    sub_rms = np.array([float(np.sqrt(np.mean(mag[i*win:(i+1)*win]**2)))
                        for i in range(nb)])
    present_frac = float((sub_rms > floor).mean())
    if present_frac < 0.5:
        # mostly quiet -> impulsive, not a steady AM video carrier
        return 0.0, f"not_continuous(present={present_frac*100:.0f}%<50%)"

    # --- GATE 2: narrow 50/60 Hz line-rate peak ---
    nfft = 1 << int(np.ceil(np.log2(n)))
    spec = np.abs(np.fft.rfft(env, nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / srate)
    def peak_in(lo, hi):
        m = (freqs >= lo) & (freqs <= hi)
        return float(spec[m].max()) if m.any() else 0.0
    line_peak = max(peak_in(45, 65), peak_in(55, 75))
    # baseline: median of the 1 Hz..1 kHz envelope spectrum (excl. the peaks)
    base_mask = (freqs > 1) & (freqs < 1000) & ~((freqs >= 40) & (freqs <= 80))
    baseline = float(np.median(spec[base_mask])) if base_mask.any() else 0.0
    prominence = line_peak / (baseline + 1e-12)
    # the line-rate peak must be a narrow LINE: measure how many adjacent bins
    # are also high (a real line is 1-2 bins; broadband impulse energy is many)
    def peak_width(lo, hi):
        m = (freqs >= lo) & (freqs <= hi)
        seg = spec[m]
        if seg.max() <= 0:
            return 0
        thr = seg.max() * 0.5
        return int((seg > thr).sum())
    lw = max(peak_width(45, 65), peak_width(55, 75))
    score = 0.0
    if prominence > 2.0:
        score += 0.6 * min(1.0, (prominence - 2.0) / 10.0)
    if prominence > 4.0:
        score += 0.4
    # a narrow line (<=4 bins at half-max) is the real deal; wide = impulse
    if lw > 6:
        score *= 0.3
    return min(1.0, score), (f"line_prom={prominence:.1f} "
                             f"present={present_frac*100:.0f}% linew={lw}")


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
    # A real FHSS link hops among SEVERAL discrete frequencies (FrSky, ELRS
    # non-LoRa, etc. use 5-10+). Just 2-3 frequencies is a drifting/bipolar
    # carrier or broadband interference, NOT hopping — the 2026-10-01 laptop
    # capture showed "2freqs reg=0.5" firing on noise, so require >=4.
    if distinct < 4:
        return 0.0, f"{distinct}freqs (need >=4 for real FHSS)"
    if distinct > 16:
        return 0.0, f"{distinct} freqs (too many)"
    counts = np.bincount(q - q.min())
    # regularity over the OCCUPIED bins only (the distinct freqs that appear).
    # bincount pads with zeros across the full frequency span, and including
    # those empty bins would drown the regularity (mean/std dominated by
    # zeros) — a real 8-freq hop spread over 480 500-kHz bins would read
    # reg=0 otherwise.
    occ = counts[counts > 0]
    mean_c = occ.mean()
    reg = max(0.0, 1.0 - (occ.std() / (mean_c + 1e-9)))
    # Both gates must hold: enough distinct freqs AND a regular cadence. A
    # broadband blob wanders among a few freqs with LOW regularity (reg~0.5);
    # a real hop pattern is regular (reg > 0.6).
    score = 0.0
    score += 0.5 * min(1.0, (distinct - 3) / 5.0)      # 4->0, 9+->full
    if reg > 0.6:
        score += 0.5 * min(1.0, (reg - 0.6) / 0.4)
    # if the cadence isn't regular, do NOT claim FHSS even if freqs are distinct
    if reg <= 0.6:
        score = 0.0
    return min(1.0, score), f"{distinct}freqs reg={reg:.2f}"


# ============================================================================
# DJI OcuSync (O3/O4) / Lightbridge — PROPRIETARY OFDM, distinct symbol period
# ============================================================================
def ocusync_phy(iq: np.ndarray, srate: float) -> tuple[float, str]:
    """Positively detect a DJI OcuSync / Lightbridge link (proprietary OFDM).

    The PHYSICAL-LAYER tell (distinct from 802.11): OcuSync O3/O4 is a
    256-FFT OFDM with a ~2 us symbol period, whereas 802.11a is 64-FFT with a
    4 us data symbol (and 12.8 us total). So the Schmidl-Cox timing metric
    M(tau) peaks at the OFDM symbol period — for OcuSync that's ~2 us, NOT at
    the 802.11 symbol periods (0.8/4/12.8 us). We score on the prominence of
    a M-peak near 2 us that is NOT one of the 802.11 periods.

    HONESTY: this is a PHY-shape detector, NOT a validated OcuSync decoder.
    It is reported UNCONFIRMED until validated against a real OcuSync capture
    (the exact symbol period + preamble must be confirmed). It keys on a
    distinct OFDM symbol period so it can separate OcuSync from 802.11 at the
    RF layer — but it is a hypothesis, not a proof, until capture-validated.
    """
    n = len(iq)
    if n < 4096:
        return 0.0, "too_short"
    power = np.abs(iq) ** 2
    csum = np.concatenate([[0.0], np.cumsum(power)])

    def rp(a, b):
        a = max(0, a); b = min(n, b)
        return float(csum[b] - csum[a]) if b > a else 0.0

    # Schmidl-Cox M(tau) at candidate OFDM symbol periods. OcuSync ~2 us;
    # 802.11a ~4 us (data) / 12.8 us (total) / 0.8 us (short). We want a peak
    # near 2 us that is distinctly higher than the 802.11 lags.
    def m_at(tau):
        # R(tau) = sum |x[n]|^2 |x[n+tau]|^2 over the overlap
        tau = int(tau)
        if tau <= 0 or tau >= n:
            return 0.0
        # use the first ~2*tau samples for R, and the next 2*tau for P
        # (Schmidl-Cox normalization)
        r = 0.0
        p1 = 0.0
        p2 = 0.0
        # vectorized over a window
        win = min(n - tau, 2048)
        x = power[:win]
        r = float(np.sum(x * power[tau:tau + win]))
        p1 = float(np.sum(power[:win]))
        p2 = float(np.sum(power[tau:tau + win]))
        if p1 <= 0 or p2 <= 0:
            return 0.0
        return r / np.sqrt(p1 * p2)

    # candidate lags in samples
    tau_ocusync = int(srate * 2.0e-6)    # ~2 us (OcuSync symbol)
    tau_80211a = int(srate * 4.0e-6)     # 4 us (802.11a data symbol)
    tau_80211s = int(srate * 0.8e-6)     # 0.8 us (802.11 short symbol)
    tau_total = int(srate * 12.8e-6)     # 12.8 us (802.11 total symbol)
    if tau_ocusync < 8:
        return 0.0, "srate_too_low"

    m_ocusync = m_at(tau_ocusync)
    m_80211 = max(m_at(tau_80211a), m_at(tau_80211s), m_at(tau_total))
    # a flat noise M is ~1.0; a real OFDM timing peak is >> 1.0 at the symbol
    # period. Score: OcuSync peak high AND clearly above the 802.11 lags.
    if m_ocusync < 1.5:
        return 0.0, f"no OFDM peak@2us (M={m_ocusync:.2f})"
    # prominence over the 802.11 lags: OcuSync should be the dominant period
    prom = m_ocusync / (m_80211 + 1e-9)
    score = 0.0
    score += 0.6 * min(1.0, (m_ocusync - 1.5) / 2.0)
    if prom > 1.2:
        score += 0.4 * min(1.0, (prom - 1.2) / 2.0)
    return min(1.0, score), (f"OcuSync? M@2us={m_ocusync:.2f} "
                             f"vs 802.11={m_80211:.2f} (prom={prom:.2f})")
