"""Signal demodulation + analysis from IQ samples.

Inspect RF finds *where* energy is; this turns a captured slice of IQ at
a tuned frequency into *what it is*. First real mode: analog FM — a frequency
discriminator recovers the baseband, from which we measure carrier presence,
peak deviation, and whether there's actual audio (voice/music) vs. an idle
carrier or just noise. Digital modes (DMR/P25/…) are a later, heavier lift.

Pure DSP (numpy): testable with synthetic IQ, no radio required. Capturing the
IQ from a real SDR is a separate, hardware-gated step (see the SDR backends).
"""
from __future__ import annotations

import math


def available() -> bool:
    try:
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def fm_demodulate(iq, fs: float):
    """Frequency discriminator: instantaneous frequency (Hz) per sample.

    inst_freq = fs/(2π) · d/dt(phase), computed as the angle of successive
    conjugate products — the standard, robust FM demodulator.
    """
    import numpy as np
    x = np.asarray(iq)
    if x.size < 2:
        return np.zeros(0)
    d = np.angle(x[1:] * np.conj(x[:-1]))
    return d * (fs / (2.0 * math.pi))


def fm_to_audio(iq, fs: float, audio_rate: int = 48_000):
    """FM-discriminate to baseband audio and decimate to `audio_rate`. Returns
    (float32 audio in [-1,1], actual_rate)."""
    import numpy as np
    disc = fm_demodulate(iq, fs)                    # instantaneous frequency = FM audio
    if disc.size < 4:
        return np.zeros(0, dtype=np.float32), audio_rate
    q = max(1, int(round(fs / audio_rate)))
    disc = np.convolve(disc, np.ones(q) / q, "same")   # anti-alias before downsample
    audio = disc[::q].astype(np.float32)
    audio -= audio.mean()
    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak).astype(np.float32), fs / q


def am_to_audio(iq, fs: float, audio_rate: int = 48_000):
    """AM envelope to audio, decimated. Returns (float32 audio, actual_rate)."""
    import numpy as np
    env = am_demodulate(iq).astype(np.float32)
    if env.size < 4:
        return np.zeros(0, dtype=np.float32), audio_rate
    env -= env.mean()
    q = max(1, int(round(fs / audio_rate)))
    env = np.convolve(env, np.ones(q) / q, "same")
    audio = env[::q].astype(np.float32)
    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak).astype(np.float32), fs / q


def to_wav(samples, rate: float) -> bytes:
    """Mono 16-bit PCM WAV bytes from float samples in [-1,1]."""
    import io
    import struct
    import numpy as np
    pcm = (np.clip(np.asarray(samples), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    r = int(rate)
    buf = io.BytesIO()
    buf.write(b"RIFF"); buf.write(struct.pack("<I", 36 + len(pcm))); buf.write(b"WAVE")
    buf.write(b"fmt "); buf.write(struct.pack("<IHHIIHH", 16, 1, 1, r, r * 2, 2, 16))
    buf.write(b"data"); buf.write(struct.pack("<I", len(pcm))); buf.write(pcm)
    return buf.getvalue()


def am_demodulate(iq):
    """AM: the message rides on the envelope."""
    import numpy as np
    return np.abs(np.asarray(iq))


def analyze_am(iq, fs: float) -> dict:
    """Detect AM by envelope modulation depth + audio in the envelope."""
    import numpy as np
    env = am_demodulate(iq)
    if env.size < 64:
        return {"modulation": "AM", "active": False, "summary": "too few samples"}
    mean = float(env.mean()) or 1e-9
    depth = float(env.std() / mean)                    # 0 = unmodulated carrier
    ac = env - env.mean()
    win = np.hanning(ac.size)
    spec = np.abs(np.fft.rfft(ac * win)) ** 2
    freqs = np.fft.rfftfreq(ac.size, 1.0 / fs)
    total = float(spec.sum()) + 1e-12
    audio = float(spec[(freqs >= 300) & (freqs <= 8_000)].sum() / total)
    active = depth > 0.05 and audio > 0.3
    return {"modulation": "AM", "active": bool(active),
            "depth": round(depth, 3), "audio_ratio": round(audio, 2),
            "summary": "AM voice/audio present" if active else "no clear AM audio"}


# Analog video line-sync check. Only the broadcast line rates are accepted (tight tolerance), and
# composite sync must show harmonics too: a random noise peak or a single stray tone near
# 15-16 kHz is not video sync.
VIDEO_LINE_RATES = (("NTSC", 15_734.264), ("PAL", 15_625.0))
VIDEO_LINE_TOLERANCE = 0.002          # +/-0.2 % (about +/-31 Hz)
VIDEO_HARMONICS = (1, 2, 3)
VIDEO_MIN_PROMINENCE = 8.0            # peak / local median spectrum, per harmonic
VIDEO_MIN_HARMONICS = 2               # fundamental plus at least one more harmonic
VIDEO_NOISE_HALF_WIDTH_HZ = 2_500.0   # local noise window each side of a harmonic
VIDEO_MIN_HARMONIC_RATIO = 1e-4       # further harmonics must be within 40 dB of the fundamental


def _line_sync_scores(spec, freqs, line_hz, duration_s):
    """(prominence, frequency, power) of each harmonic of `line_hz`: max within the tolerance over
    the local median (the peak's own main lobe excluded from the noise estimate). A candidate that
    is not the strongest point within its main-lobe neighbourhood is leakage and scores 0."""
    import numpy as np
    out = []
    lobe = max(300.0, 4.0 / duration_s)
    for k in VIDEO_HARMONICS:
        f0 = k * line_hz
        tol = f0 * VIDEO_LINE_TOLERANCE
        sel = (freqs >= f0 - tol) & (freqs <= f0 + tol)
        near = (np.abs(freqs - f0) <= VIDEO_NOISE_HALF_WIDTH_HZ) & (np.abs(freqs - f0) > lobe)
        if not sel.any() or near.sum() < 8:
            out.append((0.0, None, 0.0))
            continue
        i = int(np.argmax(np.where(sel, spec, -1.0)))
        around = np.abs(freqs - f0) <= lobe
        if spec[i] < spec[around].max():                  # a stronger neighbour: leakage, not a line-rate peak
            out.append((0.0, None, 0.0))
            continue
        out.append((float(spec[i] / (np.median(spec[near]) + 1e-30)), float(freqs[i]), float(spec[i])))
    return out


def analyze_video(iq, fs: float) -> dict:
    """Detect analog FPV video: FM-demodulate the composite baseband and test the horizontal
    line-sync structure at the NTSC (15734.264 Hz) and PAL (15625 Hz) line rates.

    Video is reported only when the fundamental AND at least one further harmonic (2x, 3x)
    each stand at least VIDEO_MIN_PROMINENCE above the local spectrum, within +/-0.2 % of a
    standard rate. Off-standard rates and lone tones are rejected."""
    import numpy as np
    base = fm_demodulate(iq, fs)
    top = max(VIDEO_HARMONICS) * max(r for _, r in VIDEO_LINE_RATES) * (1 + VIDEO_LINE_TOLERANCE)
    if base.size < 256 or fs / 2 < top + VIDEO_NOISE_HALF_WIDTH_HZ:
        return {"modulation": "video", "video": False, "summary": "capture rate too low"}
    base = base - base.mean()
    duration = base.size / fs
    nfft = 1 << int(np.ceil(np.log2(base.size * 4)))     # zero-padded: fine grid for the tolerance search
    spec = np.abs(np.fft.rfft(base * np.hanning(base.size), nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    best = None
    for std, rate in VIDEO_LINE_RATES:
        scores = _line_sync_scores(spec, freqs, rate, duration)
        fund_power = scores[0][2]
        passed = sum(1 for p, _, pw in scores
                     if p >= VIDEO_MIN_PROMINENCE and pw >= VIDEO_MIN_HARMONIC_RATIO * fund_power)
        ok = scores[0][0] >= VIDEO_MIN_PROMINENCE and passed >= VIDEO_MIN_HARMONICS
        key = (ok, passed, scores[0][0])
        if best is None or key > best[0]:
            best = (key, std, scores)
    (video, _, fundamental), std, scores = best
    line_hz = scores[0][1] or 0.0
    return {
        "modulation": "video", "video": bool(video),
        "line_rate_hz": round(line_hz) if video else None,
        "standard": std if video else None,
        "prominence": round(float(fundamental), 1),
        "harmonic_prominence": [round(p, 1) for p, _, _ in scores],
        "summary": (f"analog FPV video — {std} line sync {line_hz/1e3:.2f} kHz"
                    if video else "no analog-video line sync found"),
    }


def analyze_fm(iq, fs: float) -> dict:
    """Characterise an IQ slice as an FM signal. Returns carrier presence, peak
    deviation, audio activity, and (for wideband captures) a stereo-pilot flag."""
    import numpy as np
    x = np.asarray(iq)
    n = x.size
    if n < 64:
        return {"modulation": "FM", "active": False, "summary": "too few samples"}

    mag = np.abs(x)
    mean_mag = float(mag.mean()) or 1e-9
    power_db = round(float(10 * np.log10(np.mean(mag ** 2) + 1e-12)), 1)
    # Envelope stability: a real carrier has a steadier envelope than noise
    # (Rayleigh noise gives cv ~ 0.52). Low cv => a carrier is present.
    cv = float(mag.std() / mean_mag)

    inst = fm_demodulate(x, fs)
    dev_khz = round(float(np.percentile(np.abs(inst), 99)) / 1e3, 1)

    # Audio activity: fraction of demod-spectrum energy in the 0.3–15 kHz voice/
    # program band. FM audio concentrates there; noise spreads flat.
    win = np.hanning(inst.size)
    spec = np.abs(np.fft.rfft(inst * win)) ** 2
    freqs = np.fft.rfftfreq(inst.size, 1.0 / fs)
    total = float(spec.sum()) + 1e-12
    audio_band = (freqs >= 300) & (freqs <= 15_000)
    audio_ratio = float(spec[audio_band].sum() / total)
    audio_activity = audio_ratio > 0.45

    stereo_pilot = False
    if fs > 40_000:                       # need >38 kHz to see the 19 kHz pilot
        pilot = (freqs >= 18_500) & (freqs <= 19_500)
        near = (freqs >= 15_000) & (freqs <= 23_000)
        if near.any() and pilot.any():
            stereo_pilot = spec[pilot].max() > 6 * (spec[near].mean() + 1e-12)

    carrier = cv < 0.45
    active = carrier and (audio_activity or dev_khz > 1.0)
    if active and audio_activity:
        what = "broadcast FM (stereo)" if stereo_pilot else "active FM — audio present"
    elif carrier:
        what = "carrier present, no audio (idle/dead air)"
    else:
        what = "no carrier — just noise"
    return {
        "modulation": "FM",
        "active": bool(active),
        "carrier": bool(carrier),
        "power_db": power_db,
        "deviation_khz": dev_khz,
        "audio_activity": bool(audio_activity),
        "audio_ratio": round(audio_ratio, 2),
        "stereo_pilot": bool(stereo_pilot),
        "summary": what,
    }
