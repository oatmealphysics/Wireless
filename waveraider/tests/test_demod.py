"""FM demodulation + analysis, verified against synthetic IQ (no radio needed)."""
import numpy as np
import pytest

from waveraider import demod


def _fm(fs, dur, fmod, fdev_hz, pilot=False, noise=0.0, seed=0):
    """Synthetic FM: a tone at fmod deviating the carrier by fdev_hz."""
    rng = np.random.default_rng(seed)
    n = int(fs * dur)
    t = np.arange(n)
    m = np.sin(2 * np.pi * fmod * t / fs)
    if pilot:
        m = 0.9 * m + 0.1 * np.sin(2 * np.pi * 19_000 * t / fs)   # 19 kHz stereo pilot
    phase = 2 * np.pi * fdev_hz / fs * np.cumsum(m)
    iq = np.exp(1j * phase)
    if noise:
        iq = iq + noise * (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    return iq.astype(np.complex64)


def test_discriminator_recovers_frequency_offset():
    fs = 240_000
    t = np.arange(fs // 4)
    iq = np.exp(1j * 2 * np.pi * 40_000 / fs * t)      # pure +40 kHz tone
    inst = demod.fm_demodulate(iq, fs)
    assert abs(np.median(inst) - 40_000) < 500          # recovered ~40 kHz


def test_active_fm_with_audio():
    iq = _fm(240_000, 0.05, fmod=3_000, fdev_hz=50_000)
    r = demod.analyze_fm(iq, 240_000)
    assert r["active"] and r["carrier"] and r["audio_activity"]
    assert 35 <= r["deviation_khz"] <= 65               # ~50 kHz peak deviation


def test_pure_noise_is_not_active():
    rng = np.random.default_rng(1)
    n = 12_000
    iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
    r = demod.analyze_fm(iq, 240_000)
    assert not r["active"] and not r["carrier"] and not r["audio_activity"]
    assert "noise" in r["summary"]


def test_idle_carrier_has_no_audio():
    fs = 240_000
    t = np.arange(int(fs * 0.05))
    iq = np.exp(1j * 2 * np.pi * 1_000 / fs * t).astype(np.complex64)   # steady carrier
    r = demod.analyze_fm(iq, fs)
    assert r["carrier"] and not r["audio_activity"] and not r["active"]


def test_broadcast_stereo_pilot_detected():
    iq = _fm(240_000, 0.1, fmod=1_000, fdev_hz=60_000, pilot=True)
    r = demod.analyze_fm(iq, 240_000)
    assert r["stereo_pilot"] and "stereo" in r["summary"]


def test_too_few_samples_is_graceful():
    r = demod.analyze_fm(np.zeros(10, dtype=np.complex64), 240_000)
    assert r["active"] is False


def _am(fs, dur, fmod=1_000, depth=0.5, seed=0):
    n = int(fs * dur)
    t = np.arange(n)
    env = 1.0 + depth * np.sin(2 * np.pi * fmod * t / fs)
    return (env * np.exp(1j * 2 * np.pi * 2_000 / fs * t)).astype(np.complex64)


def test_am_detected():
    r = demod.analyze_am(_am(48_000, 0.1), 48_000)
    assert r["active"] and r["depth"] > 0.05


def test_am_rejects_steady_carrier():
    fs = 48_000
    t = np.arange(int(fs * 0.1))
    iq = np.exp(1j * 2 * np.pi * 2_000 / fs * t).astype(np.complex64)
    assert demod.analyze_am(iq, fs)["active"] is False


def _fpv_video(fs, dur, line_hz=15_734, fdev=250_000):
    n = int(fs * dur)
    t = np.arange(n)
    m = 2.0 * ((t * line_hz / fs) % 1.0) - 1.0          # sawtooth horizontal sweep
    phase = 2 * np.pi * fdev / fs * np.cumsum(m)
    return np.exp(1j * phase).astype(np.complex64)


def test_analog_video_line_sync_detected():
    r = demod.analyze_video(_fpv_video(1_000_000, 0.05, line_hz=15_734), 1_000_000)
    assert r["video"] and r["standard"] == "NTSC"
    assert abs(r["line_rate_hz"] - 15_734) < 200


def test_video_pal_vs_ntsc():
    r = demod.analyze_video(_fpv_video(1_000_000, 0.05, line_hz=15_625), 1_000_000)
    assert r["video"] and r["standard"] == "PAL"


def test_fm_to_audio_decimates_and_recovers_tone():
    iq = _fm(240_000, 0.2, fmod=1_000, fdev_hz=50_000)
    audio, arate = demod.fm_to_audio(iq, 240_000, audio_rate=48_000)
    assert arate == 48_000 and len(audio) > 0
    assert float(np.max(np.abs(audio))) <= 1.0 + 1e-6      # normalized
    # the 1 kHz modulating tone dominates the recovered audio spectrum
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / arate)
    assert abs(freqs[int(np.argmax(spec))] - 1_000) < 60


def test_to_wav_is_valid_mono_16bit():
    wav = demod.to_wav(np.zeros(4800, dtype=np.float32), 48_000)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert wav[22:24] == (1).to_bytes(2, "little")          # channels = 1
    assert wav[24:28] == (48_000).to_bytes(4, "little")     # sample rate
    assert wav[34:36] == (16).to_bytes(2, "little")         # bits/sample


def test_no_video_in_noise():
    rng = np.random.default_rng(3)
    iq = (rng.standard_normal(50_000) + 1j * rng.standard_normal(50_000)).astype(np.complex64)
    assert demod.analyze_video(iq, 1_000_000)["video"] is False


def _composite_fm(fs, dur, line_hz=15_734.264, fdev=2_500_000, snr_db=20.0, seed=0):
    """FM-modulated composite video: per line a 4.7 us sync tip (-0.3), blanking (0) and
    random picture content (0..0.7), with complex noise at `snr_db`."""
    rng = np.random.default_rng(seed)
    n = int(fs * dur)
    t = np.arange(n) / fs
    pos = (t * line_hz) % 1.0
    line = np.floor(t * line_hz).astype(int)
    content = rng.uniform(0.0, 0.7, line.max() + 1)[line]
    m = np.where(pos < 4.7e-6 * line_hz, -0.3, np.where(pos < 10.9e-6 * line_hz, 0.0, content))
    phase = 2 * np.pi * fdev / fs * np.cumsum(m)
    x = np.exp(1j * phase)
    sigma = 10 ** (-snr_db / 20) / np.sqrt(2)
    x = x + sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return x.astype(np.complex64)


def test_composite_video_detected_on_short_buffers():
    for std, rate in (("NTSC", 15_734.264), ("PAL", 15_625.0)):
        r = demod.analyze_video(_composite_fm(20_000_000, 0.0131072, line_hz=rate), 20_000_000)
        assert r["video"] and r["standard"] == std, r
        assert abs(r["line_rate_hz"] - rate) < 40
        assert sum(p >= demod.VIDEO_MIN_PROMINENCE for p in r["harmonic_prominence"]) >= 2


def test_noise_never_reported_as_video():
    rng = np.random.default_rng(20260916)
    flagged = 0
    for _ in range(300):
        iq = (rng.standard_normal(50_000) + 1j * rng.standard_normal(50_000)).astype(np.complex64)
        flagged += demod.analyze_video(iq, 1_000_000)["video"]
    assert flagged == 0


def test_off_standard_line_rate_rejected():
    # 16.02 kHz: the off-band artefact the old 15.0-16.2 kHz window accepted
    r = demod.analyze_video(_fpv_video(1_000_000, 0.05, line_hz=16_022), 1_000_000)
    assert r["video"] is False and r["line_rate_hz"] is None
    r = demod.analyze_video(_fpv_video(1_000_000, 0.05, line_hz=15_400), 1_000_000)
    assert r["video"] is False


def test_small_line_rate_error_within_tolerance_accepted():
    for rate in (15_734.264 * 1.001, 15_625.0 * 0.999):
        assert demod.analyze_video(_fpv_video(1_000_000, 0.05, line_hz=rate), 1_000_000)["video"]


def test_lone_tone_at_line_rate_is_not_video():
    fs, n = 1_000_000, 50_000
    t = np.arange(n)
    phase = 2 * np.pi * 250_000 / fs * np.cumsum(np.sin(2 * np.pi * 15_734.264 * t / fs))   # sinusoid: no harmonics
    r = demod.analyze_video(np.exp(1j * phase).astype(np.complex64), fs)
    assert r["video"] is False and r["harmonic_prominence"][0] >= demod.VIDEO_MIN_PROMINENCE


def test_video_needs_enough_bandwidth_for_harmonics():
    r = demod.analyze_video(_fpv_video(80_000, 0.05, line_hz=15_734), 80_000)
    assert r["video"] is False and r["summary"] == "capture rate too low"
