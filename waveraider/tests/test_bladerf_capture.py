"""Hardware error propagation and rate selection without opening a physical radio."""
import ctypes as C
import io
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from waveraider import bladerf_sweep, demod, spectrum


@pytest.mark.parametrize('failure', ['rate', 'bandwidth', 'frequency', 'restore'])
def test_rejected_hardware_configuration_does_not_return_iq(monkeypatch, failure):
    closed = []
    calls = []
    def rate(dev, ch, value, actual):
        calls.append(value)
        C.cast(actual, C.POINTER(C.c_uint))[0] = value
        return -1 if failure == 'rate' or (failure == 'restore' and len(calls) == 2) else 0
    lib = SimpleNamespace(
        bladerf_set_sample_rate=rate,
        bladerf_set_bandwidth=lambda *a: -1 if failure == 'bandwidth' else 0,
        bladerf_set_frequency=lambda *a: -1 if failure == 'frequency' else 0,
        bladerf_sync_rx=lambda *a: 0)
    monkeypatch.setattr(bladerf_sweep, '_open', lambda: object())
    monkeypatch.setattr(bladerf_sweep, '_lib', lambda: lib)
    monkeypatch.setattr(bladerf_sweep, '_close', lambda: closed.append(True))
    assert bladerf_sweep.capture_iq(94.9e6, 960000, 64) == (None, 0)
    assert closed == [True]


@pytest.mark.parametrize('backend,rate', [('bladerf', 960000), ('rtl_power', 240000)])
def test_audio_capture_rate_and_duration(monkeypatch, backend, rate):
    monkeypatch.setattr(spectrum, 'source_status', lambda device: {
        'mode': 'live', 'backend': backend, 'device': device})
    calls = []
    def capture(center, requested_rate, n):
        calls.append((center, requested_rate, n))
        return np.ones(n, dtype=complex), requested_rate
    monkeypatch.setattr(bladerf_sweep, 'capture_iq', capture)
    monkeypatch.setattr(spectrum, '_rtl_capture_iq', capture)
    result = spectrum.capture_audio(94.9e6, backend, secs=0.5)
    assert calls == [(94.9e6, rate, int(rate * 0.5))]
    with wave.open(io.BytesIO(result)) as wav:
        assert abs(wav.getnframes()/wav.getframerate() - 0.5) < .001


def test_bladerf_analysis_uses_supported_rate(monkeypatch):
    monkeypatch.setattr(spectrum, 'source_status', lambda device: {
        'mode': 'live', 'backend': 'bladerf', 'device': device})
    calls = []
    def capture(center, rate, count):
        calls.append((rate, count))
        return np.ones(count, dtype=complex), rate
    monkeypatch.setattr(bladerf_sweep, 'capture_iq', capture)
    result = spectrum.analyze_signal(94.9e6, 'blade')
    assert calls == [(960000, 48000)]
    assert result['sample_rate'] == 960000


def test_capture_restores_sweep_rate_and_bandwidth(monkeypatch):
    settings = []
    def setting(name):
        def apply(dev, ch, value, actual):
            settings.append((name, value))
            C.cast(actual, C.POINTER(C.c_uint))[0] = value
            return 0
        return apply
    lib = SimpleNamespace(
        bladerf_set_sample_rate=setting('rate'),
        bladerf_set_bandwidth=setting('bandwidth'),
        bladerf_set_frequency=lambda *a: 0,
        bladerf_sync_rx=lambda *a: 0)
    monkeypatch.setattr(bladerf_sweep, '_open', lambda: object())
    monkeypatch.setattr(bladerf_sweep, '_lib', lambda: lib)
    iq, rate = bladerf_sweep.capture_iq(94.9e6, 960000, 64)
    assert len(iq) == 64 and rate == 960000
    assert settings == [('rate', 960000), ('bandwidth', 768000),
                        ('rate', bladerf_sweep._RATE),
                        ('bandwidth', int(bladerf_sweep._RATE * .9))]
