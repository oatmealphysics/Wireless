"""P1 — UHD/SoapySDR backend tests. No B205 needed: mock the UHD transport the
same way WaveRaider's own SDR tests mock bladeRF/SoapySDR, and verify the backend
contract (available / which_backend / sweep shape / capture_iq) plus that the
B205 is now routed to the UHD backend by spectrum's backend matcher."""
from __future__ import annotations

import sys
import numpy as np

sys.path.insert(0, "/home/andeds-agent/repos/elrs-sdr")           # elrs_sdr package
sys.path.insert(0, "/home/andeds-agent/repos/elrs-sdr/waveraider")  # waveraider package

from waveraider import uhd_backend, spectrum, bands  # noqa: E402

PASS, FAIL = 0, 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# --- a mock UHD transport -------------------------------------------------
# elrs_sdr.radio.uhd is the module the backend checks; mock a RadioDevice that
# emits a deterministic carrier so the sweep must show a peak near it.

class _FakeStream:
    def __init__(self, n):
        self.n = n


class _MockRadio:
    """Emits a narrow complex carrier at `freq` (relative to last tune) so an
    FFT-based sweep produces a real peak."""
    def __init__(self, args):
        self._freq = 2.4e9
        self._rate = 3.84e6
        self._closed = False

    def tune(self, freq_hz, rate, gain, antenna=None):
        self._freq = float(freq_hz)
        self._rate = float(rate)

    def recv_block(self, buf, timeout_s=0.2):
        if self._closed:
            return 0
        m = min(len(buf), self._n)
        t = np.arange(m) / self._rate
        # a carrier at +500 kHz offset from the tuned center, unit-ish amplitude
        buf[:m] = (0.8 + 0j) * np.exp(2j * np.pi * 500_000 * t)
        return m

    _n = 4096

    def close(self):
        self._closed = True


def _install_mock():
    import elrs_sdr.radio as radio
    radio.uhd = object()                 # pretend the bindings imported
    radio.RadioDevice = _MockRadio
    uhd_backend._DEV = None
    uhd_backend._BACKEND = None


def _reset():
    uhd_backend._DEV = None
    uhd_backend._BACKEND = None


# 1. availability + backend selection
print("== availability / backend selection ==")
_install_mock()
ok("available() True with mocked native uhd", uhd_backend.available() is True)
ok("which_backend() == 'uhd' (native preferred)", uhd_backend.which_backend() == "uhd")

# 2. capture_iq returns complex at the requested length
print("== capture_iq contract ==")
iq, rate = uhd_backend.capture_iq(2_400_000_000, 240_000, 12_000)
ok("capture_iq returns non-None iq", iq is not None)
ok("capture_iq iq is complex", iq is not None and np.iscomplexobj(iq))
ok("capture_iq iq has requested length", iq is not None and len(iq) == 12_000)
ok("capture_iq iq is finite", iq is not None and bool(np.all(np.isfinite(iq))))
ok("capture_iq reports a rate", rate and rate > 0)
_reset()

# 3. sweep returns the right number of points, in range, with a peak near the carrier
print("== sweep contract ==")
pts = uhd_backend.sweep(2_400_000_000, 2_420_000_000, 256)
ok("sweep returns non-None", pts is not None)
if pts:
    ok("sweep returns `bins` points", len(pts) == 256, f"got {len(pts)}")
    ok("sweep points are [freq_hz, db]", all(len(p) == 2 for p in pts))
    freqs = [p[0] for p in pts]
    ok("sweep freqs in [start, stop]", freqs[0] >= 2_400_000_000 and freqs[-1] <= 2_420_000_000)
    ok("sweep freqs sorted", all(a <= b for a, b in zip(freqs, freqs[1:])))
    # the mock emits a carrier at +500 kHz; a chunk whose center is near 2400.5 MHz
    # must show a power peak well above the floor.
    dbs = [p[1] for p in pts]
    floor = sorted(dbs)[len(dbs) // 4]
    ok("sweep has a peak > floor+8dB", max(dbs) > floor + 8, f"max={max(dbs):.1f} floor={floor:.1f}")
_reset()

# 4. backend matcher routes a B205 model string to the 'uhd' backend
print("== spectrum._backend_for routes B205 -> uhd ==")
fake_b205 = {"model": "Ettus USRP B200", "usb_name": "Ettus Research USRP B205mini", "id": "2500:0020"}
b = spectrum._backend_for(fake_b205)
ok("B205 matches a backend", b is not None)
ok("B205 matches the 'uhd' backend (not soapy)", b is not None and b[0] == "uhd",
   f"got {b[0] if b else None}")
# and the uhd sweep fn is registered
ok("'uhd' present in _SWEEP_FNS", "uhd" in spectrum._SWEEP_FNS)

# 5. SoapySDR-only path: no native uhd, soapy_power on PATH -> which_backend 'soapy'
print("== SoapySDR-only fallback ==")
import elrs_sdr.radio as radio
radio.uhd = None
uhd_backend._DEV = None
uhd_backend._BACKEND = None
# monkeypatch soapy detection to True (soapy_power not installed on enclave)
_orig_soapy = uhd_backend._soapy_ok
uhd_backend._soapy_ok = lambda: True
ok("which_backend() == 'soapy' when only soapy available", uhd_backend.which_backend() == "soapy")
ok("available() True via soapy", uhd_backend.available() is True)
uhd_backend._soapy_ok = _orig_soapy

# 6. neither available -> available() False, which_backend() None
print("== neither path available ==")
uhd_backend._BACKEND = None
ok("available() False when neither uhd nor soapy", uhd_backend.available() is False)
ok("which_backend() None when neither", uhd_backend.which_backend() is None)

print(f"\n== {PASS} passed, {FAIL} failed ==")
sys.exit(1 if FAIL else 0)
