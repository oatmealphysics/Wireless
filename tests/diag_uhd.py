#!/usr/bin/env python3
"""
One-shot UHD 4.11 diagnostic — run this AFTER `git pull` + re-extract.
It opens the real board and prints the EXACT API surface for the two things
that have been failing (meta_range_t accessors, StreamArgs), so the fix can be
confirmed against reality instead of guesswork. Safe: read-only, no TX.

    python3 diag_uhd.py
"""
import sys, traceback
try:
    import uhd
except Exception as e:
    print("import uhd failed:", e); sys.exit(1)

print("== UHD module ==")
print("  version :", uhd.get_version_string() if hasattr(uhd, "get_version_string") else "?")
print("  dir(uhd) has StreamArgs? ", hasattr(uhd, "stream_args"),
      "| uhd.usrp.StreamArgs?", hasattr(getattr(uhd, "usrp", None), "StreamArgs"))

# open the board
try:
    d = uhd.usrp.MultiUSRP("type=b200")
except Exception:
    try:
        d = uhd.usrp.multi_usrp("type=b200")
    except Exception as e:
        print("could not open board:", e); sys.exit(1)
print("  opened board OK")

print("\n== meta_range_t accessors (get_rx_rates) ==")
try:
    r = d.get_rx_rates(0)
except Exception as e:
    print("  get_rx_rates(0) failed:", e)
else:
    print("  type:", type(r).__name__)
    # EXHAUSTIVE: dump every attribute, and call any callable that takes no args
    print("  --- dir() (non-dunder) ---")
    for attr in sorted(dir(r)):
        if attr.startswith("__") and attr.endswith("__"):
            continue
        print("   ", attr)
    print("  --- calling no-arg methods ---")
    for attr in sorted(dir(r)):
        if attr.startswith("__"):
            continue
        c = getattr(r, attr, None)
        if callable(c):
            try:
                v = c()
                print(f"   {attr}() = {v!r}  ({type(v).__name__})")
            except Exception as ee:
                print(f"   {attr}() -> raised {type(ee).__name__}: {ee}")
    # is it iterable / indexable / has len?
    for probe in ("len", "iter", "getitem"):
        try:
            if probe == "len":
                print("   len(r) =", len(r))
            elif probe == "iter":
                it = iter(r); print("   iter works; first few:", [next(it) for _ in range(3)])
            elif probe == "getitem":
                print("   r[0] =", r[0])
        except Exception as ee:
            print(f"   {probe}: {type(ee).__name__}: {ee}")

print("\n== StreamArgs factory (get_rx_stream requires it) ==")
for name in ("stream_args", "StreamArgs", "StreamArgs_t"):
    for holder in (uhd, getattr(uhd, "usrp", None)):
        f = getattr(holder, name, None)
        if callable(f):
            print(f"  found {holder.__name__ if hasattr(holder,'__name__') else 'uhd'}.{name}")
            for args in (("fc32", ""), ("fc32",)):
                try:
                    sa = f(*args)
                    print(f"    {name}{args} -> {type(sa).__name__}")
                    break
                except Exception as e:
                    print(f"    {name}{args} failed: {e}")
            break

print("\n== RX stream: WITH and WITHOUT chain activation ==")
import numpy as np
import time

def _make_stream_cmd(mode_name):
    """Construct a stream_cmd on 4.11. CONFIRMED names: uhd.types.StreamCMD and
    uhd.types.StreamMode (both capital). StreamCMD() builds a cmd; set its mode
    to StreamMode.START via set_stream_mode or by construction.
    """
    types = getattr(uhd, "types", None)
    cmd_cls = getattr(types, "StreamCMD", None) or getattr(uhd, "StreamCMD", None)
    mode_cls = getattr(types, "StreamMode", None) or getattr(uhd, "StreamMode", None)
    if cmd_cls is None:
        print("   (no StreamCMD found)"); return None
    print(f"   StreamCMD={cmd_cls.__name__}, StreamMode={getattr(mode_cls,'__name__',mode_cls)}")
    # get the START mode
    start_mode = None
    if mode_cls is not None:
        for n in ("START", "start"):
            m = getattr(mode_cls, n, None)
            if m is not None:
                start_mode = m; break
    if start_mode is None:
        print("   (no StreamMode.START attr — mode enum:",
              [a for a in dir(mode_cls) if not a.startswith("__")], ")")
    # build the cmd, try the plausible ways to set the mode
    cmd = None
    for shape in (
        lambda: cmd_cls(start_mode) if start_mode is not None else None,
        lambda: cmd_cls(),
    ):
        try:
            c = shape()
            if c is not None:
                cmd = c; break
        except Exception as e:
            print(f"   cmd ctor failed: {e}")
    if cmd is None:
        return None
    # ensure the mode is START (construction may not have applied it)
    if start_mode is not None and hasattr(cmd, "set_stream_mode"):
        try:
            cmd.set_stream_mode(start_mode)
        except Exception as e:
            print(f"   set_stream_mode failed: {e}")
    print(f"   built {type(cmd).__name__}")
    return cmd


# DUMP every set_rx_*/get_rx_* method on the device so we see the EXACT 4.11
# names for antenna / subdev / dc-offset / rate / gain / freq — no more guessing.
print("  --- device RX methods (set_rx_* / get_rx_*) ---")
for attr in sorted(dir(d)):
    if attr.startswith("set_rx_") or attr.startswith("get_rx_") or attr in ("set_antenna","get_antenna_names","set_subdev_spec","set_dc_offset_mode"):
        print("   ", attr)
def _recv_test(st, label, seconds=0.3):
    meta = uhd.types.RXMetadata()
    buf = np.zeros(2048, dtype=np.complex64)
    total = 0
    import time
    t0 = time.time()
    peak = 0.0
    while time.time() - t0 < seconds:
        n = st.recv(buf, meta, 0.1)
        if n:
            total += n
            peak = max(peak, float(np.abs(buf[:n]).max()))
    print(f"  {label}: {total} samples over {seconds:.1f}s (peak abs={peak:.4f})")
    return total

try:
    sa = uhd.usrp.StreamArgs("fc32", "")
    # Set the RF up the way the WORKING methods do (set_rx_* family).
    try:
        d.set_rx_rate(4e6); print("  set_rx_rate(4e6) ok")
    except Exception as e: print("  set_rx_rate failed:", e)
    try:
        d.set_rx_freq(2.44e9); print("  set_rx_freq(2.44e9) ok")
    except Exception as e: print("  set_rx_freq failed:", e)
    try:
        d.set_rx_gain(30); print("  set_rx_gain(30) ok")
    except Exception as e: print("  set_rx_gain failed:", e)
    # Antenna via the set_rx_* family (the non-rx set_antenna does NOT exist on 4.11)
    ant_ok = False
    for m in ("set_rx_antenna", "set_antenna", "set_rx_subdev_spec"):
        if hasattr(d, m):
            for arg in (("TX/RX", 0), ("RX2", 0), ("TX/RX",), (0,)):
                try:
                    getattr(d, m)(*arg); print(f"  {m}{arg} ok"); ant_ok=True; break
                except Exception:
                    continue
            if ant_ok: break
    print("  (antenna set ok)" if ant_ok else "  (no antenna method worked — see dump above)")
    st = d.get_rx_stream(sa)
    print("  (after set_rx_rate/freq/gain/antenna):")
    b = _recv_test(st, "B rx-configured")
    if b == 0:
        print("  === still 0 samples — probing likely causes ===")
        # 1) Does the streamer need issue_stream_cmd(stream_cmd_t(START))?
        #    The 4.11 binding needs a uhd.types.stream_cmd object with a StreamMode.
        print("  -- trying issue_stream_cmd with a constructed stream_cmd --")
        started = False
        for cmd_ctor in (
            lambda: _make_stream_cmd("START"),
        ):
            cmd = cmd_ctor()
            if cmd is not None:
                try:
                    st.issue_stream_cmd(cmd)
                    print("   issue_stream_cmd(START) ok — re-testing recv:")
                    print("   ", _recv_test(st, "post-start"))
                    started = True
                    break
                except Exception as e:
                    print(f"   issue_stream_cmd failed: {e}")
        if not started:
            print("   (could not construct/issue a stream_cmd — see dump above)")
        # 2) max samples per recv?
        if hasattr(d, "get_max_num_samps"):
            print("  get_max_num_samps(0) =", d.get_max_num_samps(0))
        # 3) KNOWN-PRESENT signal test: sweep a few freqs at HIGH gain and look
        #    for ANY energy. 2.4 GHz Wi-Fi is almost always active indoors, so if
        #    the RX chain + stream are truly working, we should see non-zero
        #    somewhere. This separates "stream not delivering" from "no signal".
        print("  -- known-signal probe: high-gain sniff across 2.4 GHz --")
        meta = uhd.types.RXMetadata()
        buf = np.zeros(2048, dtype=np.complex64)
        any_energy = 0.0
        for fghz in (2.412, 2.437, 2.462, 2.422, 2.442):
            try:
                d.set_rx_freq(fghz*1e9); d.set_rx_gain(60)   # high gain
            except Exception as e:
                print("   tune failed:", e); break
            peak = 0.0; tot = 0
            t0 = time.time()
            while time.time() - t0 < 0.2:
                n = st.recv(buf, meta, 0.1)
                if n:
                    tot += n
                    peak = max(peak, float(np.abs(buf[:n]).max()))
            any_energy = max(any_energy, peak)
            print(f"   {fghz:.3f} GHz: {tot} samps, peak abs={peak:.5f}")
        print(f"   -> max peak across band = {any_energy:.5f}")
        if any_energy > 1e-4:
            print("   >>> RX IS DELIVERING (energy present) — the earlier 0 was a")
            print("       stream-start / freq issue, NOT a dead antenna.")
        else:
            print("   >>> STILL ~0 everywhere at high gain. Strongly suggests NO")
            print("       antenna on the RF port (open circuit) OR a hardware fault.")
            print("       CHECK: is a 2.4 GHz antenna physically screwed into the")
            print("       B205mini RF SMA connector?")
        # 4) re-read config
        try:
            print("  current gain:", d.get_rx_gain(0), " freq:", d.get_rx_freq(0), " rate:", d.get_rx_rate(0))
        except Exception as e:
            print("  could not re-read gain/freq/rate:", e)
except Exception as e:
    print("  failed:", repr(e))

print("\nDONE")
