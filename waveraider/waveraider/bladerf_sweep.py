"""Native bladeRF spectrum sweep via libbladeRF (ctypes) + numpy FFT.

The bladeRF has no rtl_power-style CLI and SoapySDR's bladeRF module isn't in
Homebrew, but libbladeRF drives it directly. We tile the requested band in
sample-rate-wide chunks, capture IQ at each, FFT to a power spectrum, drop the
DC spike and the filter roll-off at the chunk edges, then bin everything into
the requested number of points. Power is relative dB (uncalibrated), which the
view's auto-scaling axis handles.

Needs libbladeRF installed and its FPGA bitstream present (auto-loaded from
~/.config/Nuand/bladeRF/). The device handle is opened once and reused; the
caller (spectrum.sweep) already serialises access under the SDR device lock.
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
import os
import threading

_RATE = 3_840_000      # sample rate per chunk (Hz)
_N = 4096              # FFT size / samples per capture
_USABLE = 0.78         # fraction of each chunk kept (drop filter edges)
_GAIN_DB = 30          # manual gain: enough sensitivity, no front-end compression
_AVG = 4               # FFTs power-averaged per chunk (Welch) — lifts weak carriers

_LOCK = threading.Lock()
_LIB = None
_DEV = None

_CANDIDATE_LIBS = (
    "/opt/homebrew/lib/libbladeRF.dylib",
    "/usr/local/lib/libbladeRF.dylib",
    "/usr/lib/libbladeRF.so", "/usr/local/lib/libbladeRF.so",
)


def _libpath():
    p = ctypes.util.find_library("bladeRF")
    if p:
        return p
    return next((c for c in _CANDIDATE_LIBS if os.path.exists(c)), None)


def available() -> bool:
    try:
        import numpy  # noqa: F401
    except Exception:
        return False
    return _libpath() is not None


def _lib():
    global _LIB
    if _LIB is None:
        path = _libpath()
        if not path:
            raise OSError("libbladeRF not found")
        lib = C.CDLL(path)
        lib.bladerf_get_board_name.restype = C.c_char_p
        lib.bladerf_set_frequency.argtypes = [C.c_void_p, C.c_int, C.c_uint64]
        lib.bladerf_set_sample_rate.argtypes = [C.c_void_p, C.c_int, C.c_uint, C.POINTER(C.c_uint)]
        lib.bladerf_set_bandwidth.argtypes = [C.c_void_p, C.c_int, C.c_uint, C.POINTER(C.c_uint)]
        lib.bladerf_set_gain.argtypes = [C.c_void_p, C.c_int, C.c_int]
        lib.bladerf_sync_config.argtypes = [C.c_void_p, C.c_int, C.c_int,
                                            C.c_uint, C.c_uint, C.c_uint, C.c_uint]
        lib.bladerf_sync_rx.argtypes = [C.c_void_p, C.c_void_p, C.c_uint, C.c_void_p, C.c_uint]
        _LIB = lib
    return _LIB


def _open():
    global _DEV
    if _DEV is not None:
        return _DEV
    lib = _lib()
    dev = C.c_void_p()
    if lib.bladerf_open(C.byref(dev), b"") != 0:
        raise OSError("bladerf_open failed")
    if not lib.bladerf_is_fpga_configured(dev):
        lib.bladerf_close(dev)
        raise OSError("bladeRF FPGA not configured (missing bitstream)")
    ch = 0                                   # BLADERF_CHANNEL_RX(0)
    actual = C.c_uint()
    lib.bladerf_set_sample_rate(dev, ch, _RATE, C.byref(actual))
    lib.bladerf_set_bandwidth(dev, ch, int(_RATE * 0.9), C.byref(actual))
    lib.bladerf_set_gain(dev, ch, _GAIN_DB)
    # sync_config(layout=RX_X1, format=SC16_Q11, num_buffers, buffer_size, num_transfers, timeout)
    lib.bladerf_sync_config(dev, 0, 0, 16, 8192, 8, 3500)
    lib.bladerf_enable_module(dev, ch, True)
    _DEV = dev
    return _DEV


def _close():
    global _DEV
    if _DEV is not None:
        try:
            _lib().bladerf_enable_module(_DEV, 0, False)
            _lib().bladerf_close(_DEV)
        except Exception:
            pass
        _DEV = None


def close() -> None:
    """Release the bladeRF so other tools (or a re-open) can claim it."""
    with _LOCK:
        _close()


def capture_iq(center_hz: float, rate: float = 240_000, n: int = 12_000):
    """Capture `n` complex IQ samples at `center_hz`. Returns (iq, actual_rate)
    or (None, 0). Restores the sweep sample rate and bandwidth afterward."""
    import numpy as np
    with _LOCK:
        try:
            dev = _open()
            lib = _lib()
            a = C.c_uint()
            if lib.bladerf_set_sample_rate(dev, 0, int(rate), C.byref(a)) != 0 or not a.value:
                raise OSError("sample rate configuration failed")
            actual = float(a.value)
            if lib.bladerf_set_bandwidth(dev, 0, int(actual * 0.8), C.byref(a)) != 0:
                raise OSError("bandwidth configuration failed")
            if lib.bladerf_set_frequency(dev, 0, int(center_hz)) != 0:
                raise OSError("frequency configuration failed")
            buf = (C.c_int16 * (n * 2))()
            lib.bladerf_sync_rx(dev, buf, n, None, 5000)      # settle after retune
            if lib.bladerf_sync_rx(dev, buf, n, None, 5000) != 0:
                raise OSError("sync_rx failed")
            iq = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 2048.0
            if lib.bladerf_set_sample_rate(dev, 0, _RATE, C.byref(a)) != 0:
                raise OSError("sweep sample rate restore failed")
            if lib.bladerf_set_bandwidth(dev, 0, int(_RATE * 0.9), C.byref(a)) != 0:
                raise OSError("sweep bandwidth restore failed")
            return iq[0::2] + 1j * iq[1::2], actual
        except Exception:
            _close()
            return None, 0


def sweep(start_hz: float, stop_hz: float, bins: int, gain=None) -> list | None:
    """Return sorted [freq_hz, power_db] across [start, stop], or None on failure."""
    import numpy as np
    with _LOCK:
        try:
            dev = _open()
            lib = _lib()
            usable = _RATE * _USABLE
            n_chunks = min(80, max(1, int(np.ceil((stop_hz - start_hz) / usable))))
            win = np.hanning(_N).astype(np.float32)
            buf = (C.c_int16 * (_N * 2))()
            freqs, powers = [], []
            for k in range(n_chunks):
                center = start_hz + usable * (k + 0.5)
                if center <= 0:
                    continue
                if lib.bladerf_set_frequency(dev, 0, int(center)) != 0:
                    continue
                if lib.bladerf_sync_rx(dev, buf, _N, None, 5000) != 0:  # settle after retune
                    raise OSError("sync_rx failed")
                acc = np.zeros(_N)
                for _ in range(_AVG):            # power-average several FFTs (Welch)
                    lib.bladerf_sync_rx(dev, buf, _N, None, 5000)
                    iq = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 2048.0
                    x = iq[0::2] + 1j * iq[1::2]
                    sp = np.fft.fftshift(np.fft.fft(x * win))
                    acc += np.abs(sp) ** 2
                psd = 10.0 * np.log10(acc / _AVG + 1e-9)
                c0 = _N // 2                     # suppress the DC spike at chunk center
                psd[c0 - 2:c0 + 3] = float(np.median(psd))
                binfreq = center + (np.arange(_N) - _N / 2) * (_RATE / _N)
                half = usable / 2
                m = (binfreq >= center - half) & (binfreq <= center + half)
                freqs.append(binfreq[m])
                powers.append(psd[m])
            if not freqs:
                return None
            f = np.concatenate(freqs)
            p = np.concatenate(powers)
            edges = np.linspace(start_hz, stop_hz, bins + 1)
            idx = np.clip(np.digitize(f, edges) - 1, 0, bins - 1)
            # Peak-hold within each output bucket so narrow carriers aren't averaged
            # away (a spectrum survey wants the strongest energy per bin).
            out = np.full(bins, -np.inf)
            np.maximum.at(out, idx, p)
            centers = (edges[:-1] + edges[1:]) / 2.0
            floor = float(np.min(p))
            return [[int(centers[i]), round(float(out[i] if np.isfinite(out[i]) else floor), 1)]
                    for i in range(bins)]
        except Exception:
            _close()
            return None
