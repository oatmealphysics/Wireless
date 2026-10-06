"""UHD / SoapySDR capture backend for the Ettus USRP family (B205/B210/...).

WaveRaider's other backends are CLI tools (rtl_power, hackrf_sweep) or native
libbladeRF. UHD radios have no such standard CLI, so this backend drives them
directly, reusing the project's own validated UHD transport (elrs_sdr.radio)
for the native path. It exposes the same contract as bladerf_sweep:

    available()      -> bool          (some UHD path is usable)
    close()          -> None          (release the device)
    capture_iq(center_hz, rate, n)    -> (iq, actual_rate) | (None, 0)
    sweep(start_hz, stop_hz, bins)    -> [[freq_hz, power_db], ...] | None

Two transport paths, auto-picked at first use:
  1. **native uhd** — the project's elrs_sdr.radio.RadioDevice (handles the
     four UHD pybind variants, issues the stream-start command that UHD requires
     before recv() delivers anything, and arms the RX chain). Preferred, because
     it gives us a continuous IQ stream for the ELRS decode.
  2. **SoapySDR** — soapy_power (subprocess) for the power sweep, so an
     UHD-family radio still works in Inspect RF when only SoapySDR is installed.
     (No IQ path via SoapySDR here; capture_iq returns None for that backend.)

If neither is available, available() is False and spectrum.py falls back to the
simulated source with a clear note — exactly how it treats every other missing
backend.
"""
from __future__ import annotations

import shutil
import subprocess
import threading

_RATE = 3_840_000      # sweep sample rate per chunk (Hz) — matches bladerf_sweep
_N = 4096              # FFT size / samples per sweep chunk
_USABLE = 0.78         # fraction of each chunk kept (drop filter roll-off edges)
_GAIN_DB = 30          # manual RX gain (sensitivity without front-end compression)
_AVG = 4               # FFTs power-averaged per chunk (Welch) — lifts weak carriers

_LOCK = threading.Lock()
_DEV = None            # native RadioDevice, opened once, reused
_BACKEND: str | None = None   # "uhd" | "soapy" — which path actually works here


# --- availability ----------------------------------------------------------

def _soapy_ok() -> bool:
    return shutil.which("soapy_power") is not None


def _native_available() -> bool:
    """True if the project's UHD transport can open a device. Importing
    elrs_sdr.radio is cheap (it defers the uhd import until device open)."""
    try:
        from elrs_sdr import radio  # noqa: F401
        return radio.uhd is not None
    except Exception:
        return False


def available() -> bool:
    try:
        import numpy  # noqa: F401
    except Exception:
        return False
    return _native_available() or _soapy_ok()


def which_backend() -> str | None:
    """'uhd' | 'soapy' | None — native preferred, SoapySDR as the fallback."""
    return "uhd" if _native_available() else ("soapy" if _soapy_ok() else None)


# --- native UHD path (via elrs_sdr.radio) ---------------------------------

def _open_native():
    global _DEV, _BACKEND
    if _DEV is not None:
        return _DEV
    from elrs_sdr import radio
    if radio.uhd is None:
        raise OSError("UHD python bindings not importable")
    dev = radio.RadioDevice("type=b200")   # auto-detect B205/B210
    _DEV = dev
    _BACKEND = "uhd"
    return dev


def _close_native() -> None:
    global _DEV
    if _DEV is not None:
        try:
            _DEV.close()
        except Exception:
            pass
        _DEV = None


def close() -> None:
    """Release the UHD device so other tools (or a re-open) can claim it."""
    with _LOCK:
        _close_native()


def _native_capture_iq(center_hz: float, rate: float, n: int):
    import numpy as np
    dev = _open_native()
    dev.tune(float(center_hz), float(rate), _GAIN_DB)
    iq = np.empty(n, dtype=np.complex64)
    got = 0
    while got < n:
        chunk = dev.recv_block(iq[got:got + 65536] if n - got >= 65536 else iq[got:])
        if not chunk:
            break
        got += chunk
    if got == 0:
        _close_native()
        return None, 0
    # Report the rate we actually asked for (RadioDevice tracks the device rate).
    return iq[:got], float(rate)


# --- SoapySDR path (soapy_power, power sweep only) -------------------------

def _soapy_sweep_points(start: float, stop: float, bins: int) -> list | None:
    step = max(1, int((stop - start) / bins))
    try:
        out = subprocess.run(
            ["soapy_power", "-f", f"{int(start)}:{int(stop)}", "-b", str(bins),
             "-T", "1", "-q", "-d", "uhd"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return None
    # soapy_power emits rtl_power-style CSV; reuse the existing parser.
    from . import spectrum
    return spectrum._parse_power_csv(out)


# --- public API ------------------------------------------------------------

def capture_iq(center_hz: float, rate: float = 240_000, n: int = 12_000):
    """Capture `n` complex IQ samples at `center_hz`. Returns (iq, actual_rate)
    or (None, 0). The native UHD path streams; SoapySDR-only has no IQ capture."""
    global _BACKEND
    import numpy as np  # noqa: F401
    with _LOCK:
        if _BACKEND is None:
            _BACKEND = which_backend()
        if _BACKEND == "uhd":
            try:
                return _native_capture_iq(center_hz, rate, n)
            except Exception:
                _close_native()
                _BACKEND = "soapy" if _soapy_ok() else None
        # SoapySDR-only (or fallback after a native failure): no IQ path.
        return None, 0


def sweep(start_hz: float, stop_hz: float, bins: int, gain=None) -> list | None:
    """Return sorted [freq_hz, power_db] across [start, stop], or None on failure.

    Native UHD: chunked FFT (same algorithm as bladerf_sweep, but our transport).
    SoapySDR-only: delegate to soapy_power (rtl_power-style CSV)."""
    global _BACKEND
    import numpy as np
    with _LOCK:
        if _BACKEND is None:
            _BACKEND = which_backend()
        if _BACKEND == "soapy":
            return _soapy_sweep_points(start_hz, stop_hz, bins)

        try:
            dev = _open_native()
            win = np.hanning(_N).astype(np.float32)
            usable = _RATE * _USABLE
            n_chunks = min(80, max(1, int(np.ceil((stop_hz - start_hz) / usable))))
            freqs, powers = [], []
            for k in range(n_chunks):
                center = start_hz + usable * (k + 0.5)
                if center <= 0:
                    continue
                dev.tune(float(center), _RATE, _GAIN_DB)
                acc = np.zeros(_N)
                for _ in range(_AVG):          # power-average several FFTs (Welch)
                    buf = np.empty(_N, dtype=np.complex64)
                    got = 0
                    while got < _N:
                        c = dev.recv_block(buf[got:] if got + 4096 < _N else buf[got:])
                        if not c:
                            break
                        got += c
                    if got < _N:
                        buf = np.resize(buf, _N)
                    x = buf * win
                    sp = np.fft.fftshift(np.fft.fft(x))
                    acc += np.abs(sp) ** 2
                psd = 10.0 * np.log10(acc / _AVG + 1e-9)
                c0 = _N // 2                     # suppress the DC spike at chunk center
                psd[c0 - 2:c0 + 3] = float(np.median(psd))
                binfreq = center + (np.arange(_N) - _N / 2) * (_RATE / _N)
                half = usable / 2
                m = (binfreq >= center - half) & (binfreq <= center + half)
                freqs.append(binfreq[m]); powers.append(psd[m])
            if not freqs:
                return None
            f = np.concatenate(freqs); p = np.concatenate(powers)
            edges = np.linspace(start_hz, stop_hz, bins + 1)
            idx = np.clip(np.digitize(f, edges) - 1, 0, bins - 1)
            out = np.full(bins, -np.inf)         # peak-hold per bucket
            np.maximum.at(out, idx, p)
            centers = (edges[:-1] + edges[1:]) / 2.0
            floor = float(np.min(p))
            return [[int(centers[i]), round(float(out[i] if np.isfinite(out[i]) else floor), 1)]
                    for i in range(bins)]
        except Exception:
            _close_native()
            return None
