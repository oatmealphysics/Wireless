"""
Mock-UHD harness: proves the binding-tolerant radio.py works end-to-end WITHOUT
a real radio. Simulates the UHD 4.11 (apt) pybind API surface — the binding the
operator laptop actually has — and drives RadioDevice through the full RX setup
+ a recv. If this passes, the code path that previously failed (recv=0 because
the RX chain was never activated) is wired correctly.
"""
import sys, types, numpy as np

# ---- build a fake `uhd` module matching the 4.11 API ----------------------
uhd = types.ModuleType("uhd")

class _SampleRange:
    def get_min_rate(self): return 1e6
    def get_max_rate(self): return 61.44e6
    def get_step_rate(self): return 1e3

class _MetaRange411:
    # LIVE-CONFIRMED 4.11 apt shape for get_rx_rates(): a meta_range_t exposing
    # start() / stop() / step() (a "range" triple), NO get_*, NO __len__, NOT
    # iterable or subscriptable. Operator board: start=31250.0, stop=16000000.0,
    # step=246.06.
    def start(self): return 31250.0
    def stop(self): return 16000000.0
    def step(self): return 246.06299212598606
    def __len__(self):
        raise TypeError("object of type 'meta_range_t' has no len()")

class _RXStream:
    def __init__(self, n=4096): self._n = n; self._calls = 0; self._started = False
    def issue_stream_cmd(self, cmd):
        # 4.11: the streamer must be started before recv delivers
        self._started = True
    def recv(self, *a):
        # EXACT 4.11 signature: recv(np_array, metadata, timeout: float=0.1) -> int
        buf = a[0] if isinstance(a[0], np.ndarray) else None
        if buf is None:
            raise TypeError("recv(): first arg must be a numpy array")
        self._calls += 1
        # only deliver once BOTH antenna selected AND stream started
        if getattr(self, "_ready", False) and self._started:
            n = min(self._n, len(buf))
            buf[:n] = np.exp(2j*np.pi*np.linspace(0,3,n)).astype(np.complex64)
            return n
        return 0  # not ready / not started -> 0

class _Device:
    _ANTENNAS = {"TX/RX"}  # B205mini is single-chain; RX2 is NOT a real antenna
    def __init__(self, args):
        self.args = args
        self._subdev = None; self._antenna = None; self._dc = None
        self._freq = self._rate = self._gain = None
        self._stream = _RXStream()
        self._stream._ready = False
    # 4.11 apt exposes the set_rx_* family; the NON-rx variants do NOT exist
    # (diag: "'MultiUSRP' object has no attribute 'set_antenna'"). So we only
    # model set_rx_antenna here, NOT set_antenna/set_subdev_spec.
    def set_rx_antenna(self, name, ch=0):
        # like the real board: an invalid antenna name raises
        if name not in self._ANTENNAS:
            raise RuntimeError(f"Antenna {name!r} not found (valid: {sorted(self._ANTENNAS)})")
        self._antenna = (name, ch)
    def get_antenna_names(self, ch): return list(self._ANTENNAS)
    def get_firmware_revision(self): return "4.11.0.0-mock"
    def set_rx_freq(self, f): self._freq = f
    def set_rx_rate(self, r): self._rate = r
    def set_rx_gain(self, g): self._gain = g
    def set_center_freq(self, tr, ch):
        raise AttributeError("set_center_freq does not exist on 4.11 (correct)")
    def get_rx_stream(self, sa=None):
        # 4.11 REQUIRES a stream_args object; no-arg form is rejected.
        if sa is None:
            raise TypeError(
                "get_rx_stream(): incompatible function arguments. "
                "Supported: (self, arg0: uhd::stream_args_t) -> rx_streamer "
                "(this is the exact error the operator laptop hit)")
        self._stream_args = sa
        # mark the stream ready only if the antenna was selected via set_rx_antenna
        self._stream._ready = (self._antenna is not None)
        return self._stream
    # 4.11 apt: get_rx_sample_rates() is absent, get_rx_rates() -> meta_range_t
    def get_rx_rates(self, ch): return _MetaRange411()

class _MultiUSRP:
    def __init__(self, args): self._d = _Device(args)
    def __getattr__(self, name): return getattr(self._d, name)

# ---- the REAL 4.11 surface -------------------------------------------------
# uhd.usrp is a MODULE with: MultiUSRP (device) AND StreamArgs (a CLASS).
# This is the exact spelling that the operator laptop has — a lowercase
# uhd.stream_args does NOT exist on apt 4.11, so the mock must NOT provide it.
class StreamArgs:
    def __init__(self, fmt="fc32", metadata=""):
        self.fmt, self.metadata = fmt, metadata
    def __repr__(self):
        return f"StreamArgs({self.fmt!r},{self.metadata!r})"

usrp_mod = types.ModuleType("uhd.usrp")
usrp_mod.MultiUSRP = _MultiUSRP
usrp_mod.StreamArgs = StreamArgs      # capital, a class — the real 4.11 name
uhd.usrp = usrp_mod

types_mod = types.ModuleType("uhd.types")
class RXMetadata:
    # LIVE-CONFIRMED 4.11 attrs: error_code, strerror (no get_ prefix)
    def error_code(self): return "ERROR_CODE_TIMEOUT"
    def strerror(self): return "No error"
    def get_error_code(self): return "ERROR_CODE_TIMEOUT"
    def get_error_code_warn(self): return "WARN_NONE"
types_mod.RXMetadata = RXMetadata
# LIVE-CONFIRMED 4.11 StreamMode (stream_mode_t):
#   * __init__(value: int)  -> StreamMode() with no arg FAILS
#   * start_cont is a CLASS ATTRIBUTE already holding a stream_mode instance
#     (NOT callable — calling it raises 'stream_mode object is not callable')
#   * the correct START usage: mode = StreamMode.start_cont  (no parens)
class StreamMode:
    def __init__(self, value=0):
        self._value = value
        self._is_start = (value == 1)   # 1 == START_CONTINUOUS
    @property
    def value(self): return self._value
    @property
    def name(self): return f"stream_mode({self._value})"
    # instance .start() for the 4.2 fallback path
    def start(self):
        return StreamMode(1)
# class-level ready-made modes (the confirmed 4.11 API) — set AFTER the class
# body so StreamMode is defined. start_cont is an INSTANCE, not callable.
StreamMode.start_cont = StreamMode(1)
StreamMode.stop_cont = StreamMode(2)
class StreamCMD:
    def __init__(self, mode=None): self.mode = mode
    def set_stream_mode(self, m): self.mode = m
types_mod.StreamMode = StreamMode
types_mod.StreamCMD = StreamCMD
uhd.types = types_mod
# NOTE: apt 4.11 has NO top-level uhd.stream_args and NO uhd.usrp_multi_usrp.
# The timeout object:
class _Timeout:
    def __init__(self, s): self.s = s
uhd.timeout = _Timeout
# sys.modules registration
sys.modules["uhd"] = uhd
sys.modules["uhd.usrp"] = usrp_mod
sys.modules["uhd.types"] = types_mod

# ---- now drive the real code through the fake ----------------------------
import os
# Make the project root (parent of this tests/ dir) importable so it runs
# from anywhere in the tarball:  python tests/test_mock_uhd.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from elrs_sdr.radio import RadioDevice, UHDNotInstalled

print("== 1. constructor resolution (must find uhd.usrp.MultiUSRP) ==")
r = RadioDevice("type=b200")
print("   opened OK, device:", type(r.device).__name__)

print("== 2. tune() must use set_rx_* (NOT set_center_freq) ==")
r.tune(2.44e9, 4e6, 40, None)
print("   freq/rate/gain:", r.device._freq, r.device._rate, r.device._gain)
assert r.device._freq == 2.44e9, "tune did not set freq"

print("== 3. set_freq() (scan path) must use set_rx_freq ==")
r.set_freq(2.45e9)
assert r.device._freq == 2.45e9
print("   set_freq OK:", r.device._freq)

print("== 4. RX chain activation (antenna via the set_rx_* family on 4.11) ==")
stream = r._open_rx_stream()
print("   antenna:", r.device._antenna)
assert r.device._antenna is not None, "set_rx_antenna was never called (BUG — chain not activated)"
assert r.device._antenna[0] == "TX/RX", \
    f"antenna is {r.device._antenna[0]!r}, expected 'TX/RX' (B205mini single-chain RF path)"
# CRITICAL: get_rx_stream must have been called WITH a StreamArgs (4.11 requires it).
assert hasattr(r.device, "_stream_args"), \
    "get_rx_stream() was called with NO StreamArgs (the 4.11 TypeError bug)"
assert isinstance(r.device._stream_args, StreamArgs), \
    f"stream args is {type(r.device._stream_args).__name__}, expected StreamArgs"
print("   antenna -> TX/RX (B205mini RF path); stream opened WITH StreamArgs (4.11 OK)")

print("== 5. recv must now DELIVER samples (antenna + stream-start) ==")
buf = np.zeros(8192, dtype=np.complex64)
assert stream._started, "issue_stream_cmd(START) was never called (BUG — stream not started)"
n = r._recv(stream, buf, 0.2)
print("   recv returned", n, "samples; absmax =", float(np.abs(buf[:n]).max()))
assert n > 0, "recv returned 0 — chain/stream not activated (BUG)"
assert np.abs(buf[:n]).max() > 0, "buffer all-zero after recv (BUG)"

print("== 6. info-path: _describe_rx_rates parses the 4.11 meta_range_t ==")
from elrs_sdr.cli import _describe_rx_rates
desc = _describe_rx_rates(r.device, 0)
print("   ", desc)
assert "n/a" not in desc and "unrecognized" not in desc, \
    f"still got n/a/unrecognized on 4.11 meta_range_t (BUG): {desc}"
# The confirmed 4.11 range is start=31250 Hz (0.03125 Msps) .. stop=16e6 Hz (16 Msps)
assert "0.031" in desc and "16.000" in desc, f"didn't parse start/stop: {desc}"
print("   meta_range_t (start/stop/step) parsed correctly")

print("\nALL MOCK-UHD 4.11 CHECKS PASSED")
