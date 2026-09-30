"""
UHD device wrapper — symmetric RX/TX for B210 / B205mini.

One class serves both directions so the TX path (future) reuses the exact same
device handling as RX. UHD's Python API is direction-symmetric
(set_tx_freq/set_tx_rate mirror set_center_freq/set_sample_rate), so nothing
here is hardcoded to receive.

UHD-BINDING TOLERANCE (the whole reason this file exists)
---------------------------------------------------------
Four different Python `uhd` bindings have shipped and the constructor +
method names differ across all of them. We open the device trying each
known constructor in order, and we select the RX-chain / stream methods by
probing what the object actually exposes. This is what makes the same
source run on:

  * UHD 4.2+ source build        -> uhd.usrp_multi_source(args)
  * UHD 4.9/4.11 source/apt      -> uhd.usrp.MultiUSRP(args)   [the 4.9+
                                   pybind; `uhd.usrp` is a MODULE, not a
                                   callable, so the 4.2 name fails here]
  * UHD 4.x (apt on Ubuntu noble)-> uhd.usrp.MultiUSRP(args)   [4.11, the
                                   one confirmed on the operator laptop]

After the device is open we probe for the method family that exists and use
it:
  RX chain: set_rx_freq/set_rx_rate/set_rx_gain   (4.9+/4.11)
            OR set_tune_request/set_sample_rate/set_gain   (4.2+)
  stream  : get_rx_stream(StreamArgs)              (always; 4.9+/4.11 need
            the StreamArgs, 4.2 tolerates it)
  recv    : stream.recv(buf, meta, timeout) -> fills buf, returns int

THE ONE GATE THAT BIT US LIVE (B205mini, 2026-09-29):
  On the 4.9/4.11 binding, RX delivers NOTHING until the subdev/antenna is
  selected. The board opens, every set_* succeeds, but recv() returns 0
  forever (timeout, all-zero buffer) because the RX chain was never turned
  on. The fix is `_ensure_rx_chain()`: set_subdev_spec + set_antenna +
  set_dc_offset_mode, all guarded by hasattr so they no-op on bindings that
  don't have them. `_open_rx_stream()` runs it exactly once before the first
  get_rx_stream.

Usage (RX):
    radio = RadioDevice("type=b200")        # auto-detect B205/B210
    radio.tune(2.44e9, sample_rate=4e6, gain=40)
    for chunk in radio.rx_stream(1024):
        process(chunk)                      # np.complex64

Usage (TX, future):
    radio.tx_tune(2.44e9, sample_rate=4e6, gain=10)
    radio.tx_stream(waveform)               # np.complex64

IQ capture to file uses the standard .complex8 (int16 I,Q interleaved) SDR
format so files open in GQRtx / SDR# / our own decoder.
"""

from __future__ import annotations

import time
import numpy as np

# Import UHD through the bootstrap so the sys.path scan is actually used
# (see uhd_bootstrap.load_uhd). This is the single source of the `uhd` module
# for the whole radio path.
try:
    from .uhd_bootstrap import load_uhd, UHDNotInstalled
    uhd = load_uhd()
    _HAVE_UHD = True
    _UHD_ERR: Exception | None = None
except UHDNotInstalled as e:
    uhd = None
    _HAVE_UHD = False
    _UHD_ERR = e
except ImportError as e:  # pragma: no cover - uhd_bootstrap itself missing
    uhd = None
    _HAVE_UHD = False
    _UHD_ERR = e


def _open_usrp(args: str):
    """Open the device, tolerating the constructor name across bindings.

    Tries the known constructors in order and returns the first that works.
    On total failure, prints the candidate symbols that actually exist so the
    real name is self-diagnosing (never a blind AttributeError).
    """
    attempts = []
    # 1) 4.9/4.11 pybind: uhd.usrp is a module with MultiUSRP (or multi_usrp)
    mod = getattr(uhd, "usrp", None)
    if isinstance(mod, type(None)) is False and not callable(mod):
        for name in ("MultiUSRP", "multi_usrp"):
            ctor = getattr(mod, name, None)
            if ctor is not None:
                attempts.append((f"uhd.usrp.{name}", lambda c=ctor: c(args)))
    # 2) 4.2+ source build: uhd.usrp_multi_source
    for name in ("usrp_multi_source", "usrp_multi_usrp", "device"):
        ctor = getattr(uhd, name, None)
        if callable(ctor):
            attempts.append((f"uhd.{name}", lambda c=ctor: c(args)))
    # 3) gnuradio 4.6 block: uhd.usrp_source  (degraded: no raw device)
    ctor = getattr(getattr(uhd, "usrp", None), "usrp_source", None)
    if callable(ctor):
        attempts.append(("gnuradio uhd.usrp.usrp_source",
                         lambda: uhd.usrp.usrp_source(args, uhd.stream_args())))

    last_err = None
    for label, fn in attempts:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - try the next constructor
            last_err = e
    # nothing worked: dump what's actually available
    syms = [s for s in dir(uhd) if "usrp" in s.lower()]
    raise UHDNotInstalled(
        "Could not open the UHD device with any known constructor "
        f"(args={args!r}). Last error: {last_err!r}\n"
        f"Available 'usrp*' symbols in this binding: {syms}\n"
        "This means the installed Python binding differs from every known "
        "variant. Rebuild UHD with -DLIBUHD_PYTHON_BINDINGS=ON or adjust "
        "_open_usrp() to the real constructor name above."
    )


def _make_stream_args(ctype: str = "fc32"):
    """Build a stream_args object across bindings (fc32 = complex float32).

    The factory name/case differs by binding:
      * 4.11 (apt):      uhd.usrp.StreamArgs(fmt, metadata)   <- a CLASS, capital S
      * 4.2 / older:     uhd.stream_args(fmt, metadata)        <- lowercase function
      * some:            uhd.usrp.stream_args(...)
    Search all known names so we actually FIND it on 4.11 (a missing find is
    exactly the bug that made get_rx_stream() be called with no args).
    """
    candidates = []
    for holder in (uhd, getattr(uhd, "usrp", None), getattr(uhd, "types", None)):
        if holder is None:
            continue
        for name in ("StreamArgs", "stream_args", "StreamArgs_t"):
            f = getattr(holder, name, None)
            if callable(f) and f not in candidates:
                candidates.append(f)
    for factory in candidates:
        for call in ((ctype, ""), (ctype,)):
            try:
                return factory(*call)
            except TypeError:
                continue
            except Exception:
                break
    return None


def _make_rx_metadata():
    """RXMetadata object (capital in 4.9+, lowercase in 4.2+)."""
    for name in ("RXMetadata", "rx_metadata"):
        f = getattr(getattr(uhd, "types", None), name, None)
        if callable(f):
            try:
                return f()
            except Exception:
                continue
        f = getattr(uhd, name, None)
        if callable(f):
            try:
                return f()
            except Exception:
                continue
    return None


def _make_timeout(seconds: float):
    for name in ("timeout", "Timeout"):
        f = getattr(uhd, name, None)
        if callable(f):
            try:
                return f(seconds)
            except Exception:
                continue
        f = getattr(getattr(uhd, "types", None), name, None)
        if callable(f):
            try:
                return f(seconds)
            except Exception:
                continue
    return None


class RadioDevice:
    """Direction-agnostic UHD USRP wrapper (binding-tolerant)."""

    def __init__(self, args: str = "type=b200", rx_channel: int = 0,
                 tx_channel: int = 0):
        if not _HAVE_UHD:
            detail = f"\n{ _UHD_ERR}" if _UHD_ERR else ""
            raise UHDNotInstalled(
                "The 'uhd' python module is not importable. Install UHD with "
                "python bindings on the machine where the radio is attached "
                "(e.g. `sudo apt install uhd-host python3-uhd` or a source "
                "build with -DLIBUHD_PYTHON_BINDINGS=ON)." + detail
            )
        self.args = args
        self.rx_channel = rx_channel
        self.tx_channel = tx_channel
        self._usrp = _open_usrp(args)
        self._rx_chain_ready = False
        self._sample_rate = None
        self._freq = None
        self._gain = None

    # ------------------------------------------------------------------ #
    # device info
    # ------------------------------------------------------------------ #
    @property
    def device(self):
        return self._usrp

    def get_gain_range(self, chan: int | None = None) -> list:
        c = chan if chan is not None else self.rx_channel
        for m in ("get_rx_gain_range", "get_gain_range"):
            if hasattr(self._usrp, m):
                return getattr(self._usrp, m)(c)
        return []

    def get_antennas(self, direction: str = "rx", chan: int | None = None) -> list:
        c = chan if chan is not None else (self.rx_channel if direction == "rx"
                                           else self.tx_channel)
        m = "get_rx_antennas" if direction == "rx" else "get_tx_antennas"
        if hasattr(self._usrp, m):
            return list(getattr(self._usrp, m)(c))
        m2 = "get_antenna_names"
        if hasattr(self._usrp, m2):
            return list(getattr(self._usrp, m2)(c))
        return []

    # ------------------------------------------------------------------ #
    # RX chain activation (the 4.9/4.11 gate)
    # ------------------------------------------------------------------ #
    def _ensure_rx_chain(self):
        """Turn on the RX chain. No-op on bindings that don't expose these.

        Without set_subdev_spec + set_antenna, the 4.9/4.11 binding opens the
        board but recv() returns 0 forever. All calls are hasattr-guarded so
        this is safe across every binding.
        """
        if self._rx_chain_ready:
            return
        d = self._usrp
        # The antenna/subdev method NAMES differ by binding:
        #   4.9 pybind / 4.2 : set_subdev_spec / set_antenna / set_dc_offset_mode
        #   4.11 apt         : the set_rx_* family (set_rx_antenna, etc.) — the
        #                      NON-rx variants do NOT exist (diag confirmed
        #                      "'MultiUSRP' object has no attribute 'set_antenna'").
        # Try both families, hasattr-guarded, so we hit whichever the binding
        # has. B205mini is single-chain: its real RF antenna is "TX/RX".

        # --- antenna (both families) ---
        ant_set = False
        for meth in ("set_rx_antenna", "set_antenna"):
            if not hasattr(d, meth):
                continue
            # arg forms: (name, chan), (name,), (chan,)
            for ant in ("TX/RX", "RX2", "RX1"):
                for args in ((ant, self.rx_channel), (ant,)):
                    try:
                        getattr(d, meth)(*args)
                        ant_set = True
                        break
                    except Exception:
                        continue
                if ant_set:
                    break
            if ant_set:
                break
        if not ant_set:
            # last resort: pick the board's first reported RX antenna
            try:
                ants = d.get_antenna_names(self.rx_channel)
                if ants:
                    for meth in ("set_rx_antenna", "set_antenna"):
                        if hasattr(d, meth):
                            try:
                                getattr(d, meth)(ants[0], self.rx_channel)
                                ant_set = True
                                break
                            except Exception:
                                try:
                                    getattr(d, meth)(ants[0])
                                    ant_set = True
                                except Exception:
                                    pass
            except Exception:
                pass

        # NOTE: no set_rx_subdev_spec on the 4.11 B205mini (only the getters
        # get_rx_subdev_spec / get_rx_subdev_name exist, per the live method
        # dump). The single-chain RF path is routed by set_rx_antenna above,
        # which is sufficient — do NOT call a subdev-spec setter here (it
        # doesn't exist, and earlier attempts to probe it crashed).

        # --- DC-offset / IQ imbalance (both families) ---
        for meth in ("set_rx_dc_offset", "set_rx_dc_offset_mode", "set_dc_offset_mode"):
            if hasattr(d, meth):
                # set_rx_dc_offset on 4.11 takes (channel, mode) or (mode); try a
                # couple of forms, all guarded — a no-op if the signature differs.
                for call in ((self.rx_channel, True), (self.rx_channel, 1), (True,), (1,)):
                    try:
                        d.__getattribute__(meth)(*call)
                        break
                    except Exception:
                        continue
                break
        self._rx_chain_ready = True

    # ------------------------------------------------------------------ #
    # RX setup
    # ------------------------------------------------------------------ #
    def tune(self, freq_hz: float, sample_rate: float, gain: float,
             antenna: str | None = None):
        """Configure the RX chain. freq_hz is the RF center."""
        d = self._usrp
        if hasattr(d, "set_rx_freq"):           # 4.9+/4.11
            d.set_rx_freq(freq_hz)
            d.set_rx_rate(sample_rate)
            d.set_rx_gain(gain)
        else:                                    # 4.2+
            d.set_tune_request(uhd.tune_request(freq_hz), self.rx_channel)
            d.set_sample_rate(sample_rate, self.rx_channel)
            d.set_gain(gain, self.rx_channel)
        if antenna and hasattr(d, "set_antenna"):
            try:
                d.set_antenna(antenna, self.rx_channel)
            except Exception:
                pass
        self._freq, self._sample_rate, self._gain = freq_hz, sample_rate, gain

    def set_freq(self, freq_hz: float):
        """Set the RX center frequency, binding-tolerant (4.9/4.11 vs 4.2+)."""
        d = self._usrp
        if hasattr(d, "set_rx_freq"):
            d.set_rx_freq(freq_hz)
        else:
            d.set_center_freq(uhd.tune_request(freq_hz), self.rx_channel)
        self._freq = freq_hz

    def rx_stream_args(self, ctype: str = "fc32"):
        return _make_stream_args(ctype)

    def _make_rx_metadata(self):
        """RXMetadata for recv / inline error-code inspection (cmd_scan)."""
        return _make_rx_metadata()

    def _open_rx_stream(self):
        """Activate the RX chain (once), then open a stream.

        4.9+/4.11 REQUIRE a stream_args object: get_rx_stream(StreamArgs).
        Only the old 4.2 form allowed get_rx_stream() with no arg. So: build
        StreamArgs (across all factory spellings) and pass it. If we genuinely
        can't build StreamArgs, try the no-arg form once for 4.2, but surface
        the real error rather than silently mis-calling.
        """
        self._ensure_rx_chain()
        sa = self.rx_stream_args("fc32")
        if sa is not None:
            try:
                stream = self._usrp.get_rx_stream(sa)
            except Exception as e:
                raise RuntimeError(
                    f"get_rx_stream(StreamArgs) failed: {e!r}. "
                    f"StreamArgs type: {type(sa).__name__}"
                ) from e
        else:
            # No StreamArgs factory found — only viable on the old 4.2 binding.
            stream = self._usrp.get_rx_stream()
        # Some bindings hand back a STOPPED streamer that must be started with
        # issue_stream_cmd(StreamCMD(StreamMode.START)) before recv delivers.
        # Do it defensively: if the method/objects don't exist, or it's already
        # streaming, this is a harmless no-op.
        self._start_stream(stream)
        return stream

    def _start_stream(self, stream):
        """Issue a START stream command if the binding/streamer needs it.

        CONFIRMED 4.11 API (from the operator board's own introspection):
            uhd.types.StreamCMD(stream_mode)         # ctor takes a stream_mode
            uhd.types.StreamMode.start_cont()        # -> a 'start' stream_mode
            (also: StreamMode() + .start() on some builds)
        All calls are hasattr/try-guarded so this is a safe no-op where the
        streamer is already running or the API differs (4.2/4.9).
        """
        if not hasattr(stream, "issue_stream_cmd"):
            return
        import sys
        types = getattr(uhd, "types", None)
        cmd_cls = getattr(types, "StreamCMD", None) or getattr(uhd, "StreamCMD", None)
        mode_cls = getattr(types, "StreamMode", None) or getattr(uhd, "StreamMode", None)
        if cmd_cls is None or mode_cls is None:
            return
        mode = None
        # LIVE-CONFIRMED 4.11 (from the operator board's own errors):
        #   * StreamMode.__init__ takes an INT (value) — StreamMode() w/o arg fails.
        #   * mode_cls.start_cont is NOT callable — it is ALREADY a ready-made
        #     stream_mode instance (a class attribute holding the start mode).
        # So the correct usage is:  mode = mode_cls.start_cont   (no parentheses).
        # We still probe the instance-method / enum-constant shapes as fallbacks
        # for other bindings (4.2/4.9), and report which one worked.

        # 1) Attribute already holds a stream_mode instance (the 4.11 case).
        #    Do NOT call it — calling a stream_mode instance raises TypeError.
        for attr in ("start_cont", "STREAM_MODE_START_CONTINUOUS", "start"):
            a = getattr(mode_cls, attr, None)
            if a is not None and not callable(a):
                mode = a
                print(f"  [ok] START via class attr {attr} -> {type(a).__name__}",
                      file=sys.stderr)
                break
        # 2) Attribute is a zero-arg callable (some bindings).
        if mode is None:
            for attr in ("start_cont", "STREAM_MODE_START_CONTINUOUS", "start"):
                a = getattr(mode_cls, attr, None)
                if callable(a):
                    try:
                        m = a()
                        if m is not None:
                            mode = m
                            print(f"  [ok] START via {attr}() -> {type(m).__name__}",
                                  file=sys.stderr)
                            break
                    except Exception as e:
                        print(f"  [dbg] {attr}() failed: {e!r}", file=sys.stderr)
                        continue
        # 3) Instance-method fallback (4.2: StreamMode().start()).
        if mode is None:
            try:
                inst = mode_cls()
                for attr in ("start_cont", "start"):
                    m = getattr(inst, attr, None)
                    if m is not None:
                        mode = m() if callable(m) else m
                        print(f"  [ok] START via instance .{attr} "
                              f"-> {type(mode).__name__}", file=sys.stderr)
                        break
            except Exception as e:
                print(f"  [dbg] StreamMode() instance fallback failed: {e!r}",
                      file=sys.stderr)
        if mode is None:
            if getattr(self, "_start_stream_reported", False):
                return
            self._start_stream_reported = True
            print("  [warn] _start_stream: could not build StreamMode.START "
                  "(see [dbg] lines)", file=sys.stderr)
            return
        try:
            cmd = cmd_cls(mode)
            print(f"  [ok] built StreamCMD -> {type(cmd).__name__}", file=sys.stderr)
        except Exception as e:
            print(f"  [dbg] StreamCMD(mode) failed: {e!r}", file=sys.stderr)
            try:
                cmd = cmd_cls()
                if hasattr(cmd, "set_stream_mode"):
                    cmd.set_stream_mode(mode)
                else:
                    return
            except Exception as e2:
                print(f"  [dbg] StreamCMD() fallback failed: {e2!r}", file=sys.stderr)
                return
        try:
            stream.issue_stream_cmd(cmd)
            if not getattr(self, "_start_stream_reported", False):
                self._start_stream_reported = True
                print("  [ok] issue_stream_cmd(START) issued", file=sys.stderr)
        except Exception as e:
            if not getattr(self, "_start_stream_reported", False):
                self._start_stream_reported = True
                print(f"  [warn] issue_stream_cmd(START) raised: {e!r} "
                      "(stream may already be running)", file=sys.stderr)

    # Back-compat name used by cli.py / older callers.
    def get_rx_stream(self):
        return self._open_rx_stream()

    def _recv(self, stream, buf, timeout_s: float, meta=None) -> int:
        """Recv that fills `buf` (complex64), returns sample count.

        The 4.11 apt binding signature (printed by its own TypeError) is:
            recv(np_array, metadata, timeout: float = 0.1) -> int
        i.e. (buf, meta, timeout) with timeout a PLAIN FLOAT (default 0.1).
        The 4.9 pybind is the same. The 4.2 form is recv(n, timeout, meta)
        returning an array. Try the buffer-filling forms first with a plain
        float timeout (never a wrapped object — pybind11 rejects wrong types),
        then the 2-arg form, then the 4.2 array form.

        `meta` may be passed in so the CALLER can inspect its error_code
        afterwards (cmd_scan does this to self-diagnose a 0-sample recv).
        """
        if meta is None:
            meta = _make_rx_metadata()
        # 4.9/4.11: recv(buf, meta, timeout_float) -> int, fills buf.
        if meta is not None:
            for args in ((buf, meta, timeout_s), (buf, meta)):
                try:
                    return int(stream.recv(*args) or 0)
                except TypeError:
                    continue
                except Exception:
                    break
        # 4.9/4.11 with no metadata available: recv(buf, timeout)
        try:
            return int(stream.recv(buf, timeout_s) or 0)
        except TypeError:
            pass
        # 4.2: recv(num_samps, timeout, meta) -> np.array
        try:
            out = stream.recv(len(buf), timeout_s, meta)
            n = len(out)
            buf[:n] = out
            return n
        except Exception:
            pass
        # last resort: 2-arg, let the binding's default timeout apply
        return int(stream.recv(buf, meta) or 0)

    def rx_stream(self, chunk: int = 1024, timeout_s: float = 0.25,
                  ctype: str = "fc32"):
        """Generator yielding np.complex64 chunks of up to `chunk` samples."""
        stream = self._open_rx_stream()
        buf = np.zeros(chunk, dtype=np.complex64)
        while True:
            count = self._recv(stream, buf, timeout_s)
            if count > 0:
                yield buf[:count].copy()

    def capture(self, n_samples: int, path: str | None = None,
                ctype: str = "fc32", log_every: int = 1 << 20):
        """Capture a fixed number of IQ samples. Returns np.complex64.

        If `path` is given, also writes a .complex8 file (SDR#, GQRtx format).
        """
        stream = self._open_rx_stream()
        out = np.empty(n_samples, dtype=np.complex64)
        got = 0
        buf = np.zeros(min(4096, n_samples), dtype=np.complex64)
        t0 = time.time()
        while got < n_samples:
            n = self._recv(stream, buf, 0.5)
            if n == 0:
                continue
            take = min(n, n_samples - got)
            out[got:got + take] = buf[:take]
            got += take
            if (got // log_every) != ((got - take) // log_every):
                rate = got / max(time.time() - t0, 1e-9)
                print(f"  captured {got}/{n_samples} @ {rate/1e6:.2f} MS/s",
                      flush=True)
        if path:
            _write_complex8(out, path)
            print(f"  wrote {path}", flush=True)
        return out

    # ------------------------------------------------------------------ #
    # TX setup (future) — mirrors the RX API
    # ------------------------------------------------------------------ #
    def tx_tune(self, freq_hz: float, sample_rate: float, gain: float,
                antenna: str | None = None):
        d = self._usrp
        if hasattr(d, "set_tx_freq"):
            d.set_tx_freq(freq_hz)
            d.set_tx_rate(sample_rate)
            d.set_tx_gain(gain)
        else:
            d.set_tx_freq(uhd.tune_request(freq_hz), self.tx_channel)
            d.set_tx_rate(sample_rate, self.tx_channel)
            d.set_tx_gain(gain, self.tx_channel)
        if antenna and hasattr(d, "set_tx_antenna"):
            try:
                d.set_tx_antenna(antenna, self.tx_channel)
            except Exception:
                pass
        self._freq, self._sample_rate, self._gain = freq_hz, sample_rate, gain

    def tx_stream(self, samples: np.ndarray, chunk: int = 4096):
        """Transmit a complex64 waveform (future path)."""
        sa = _make_stream_args("fc32")
        stream = self._usrp.get_tx_stream(sa) if sa is not None \
            else self._usrp.get_tx_stream()
        sent = 0
        n = len(samples)
        while sent < n:
            take = min(chunk, n - sent)
            meta = _make_rx_metadata()  # tx_metadata analog
            for nm in ("TXMetadata", "tx_metadata"):
                f = getattr(getattr(uhd, "types", None), nm, None)
                if callable(f):
                    meta = f()
                    break
            if meta is not None and hasattr(meta, "set_end_of_burst"):
                meta.set_end_of_burst(sent + take == n)
            timeout = _make_timeout(1.0)
            c = 0
            while c < take:
                r = stream.send(samples[sent + c:sent + c + chunk],
                                timeout, meta)
                c += int(r or 0)
            sent += c
        meta = _make_rx_metadata()
        if meta is not None and hasattr(meta, "has_tx_done"):
            while not meta.has_tx_done():
                stream.wait_for_tx_complete(_make_timeout(1.0), meta)


# ----------------------------------------------------------------------------
# .complex8 file I/O (SDR standard: int16 I,Q interleaved)
# ----------------------------------------------------------------------------
def _write_complex8(samples: np.complex64, path: str):
    i = np.clip(samples.real, -1, 1)
    q = np.clip(samples.imag, -1, 1)
    inter = np.empty(len(samples) * 2, dtype=np.int16)
    inter[0::2] = (i * 32767).astype(np.int16)
    inter[1::2] = (q * 32767).astype(np.int16)
    inter.tofile(path)


def read_complex8(path: str) -> np.complex64:
    raw = np.fromfile(path, dtype=np.int16)
    raw = raw[: (len(raw) // 2) * 2]
    i = raw[0::2].astype(np.float32) / 32767.0
    q = raw[1::2].astype(np.float32) / 32767.0
    return (i + 1j * q).astype(np.complex64)
