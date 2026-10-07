"""Spectrum source for Inspect RF — power vs frequency for a tuned
range, so the operator can see the RF as it's received and steer the radio.

Two sources, chosen automatically:

* **rtl_power** — a real sweep when an RTL-SDR-class device is connected and the
  `rtl_power` tool is installed. (Other SDRs — SignalHound, Ettus, HackRF — each
  need their own driver path; those show the simulated source for now.)
* **simulated** — a synthetic spectrum when there's no usable SDR, so the view
  and the tuning controls work with no hardware. Fixed emitters sit at real
  frequencies, so tuning to a band actually changes what you see.

A sweep returns points as [freq_hz, power_db] plus the noise floor and peak.
"""
from __future__ import annotations

import math
import random
import shutil
import subprocess
import threading
import time

from . import bands, interfaces
from .bands import _BANDS, band_of, fit_window

# --- SDR arbitration -------------------------------------------------------
# The SDR is one physical device: only one consumer may drive it at a time, and
# while Inspect RF is operating it owns the radio exclusively — the radio
# only ever does what the view commands (the current center/span), nothing else.
_DEVICE_LOCK = threading.Lock()     # serializes real device access (one sweep at a time)
_CONTROL_LOCK = threading.Lock()    # guards the ownership record below
_control = {"owner": None, "since": 0.0, "last": 0.0, "center": 0, "span": 0}
LEASE_TIMEOUT = 8.0                 # sec without activity -> control auto-released
                                    # (so a closed tab never locks the radio forever)


def _control_expired(now: float) -> bool:
    return _control["owner"] is not None and (now - _control["last"]) > LEASE_TIMEOUT


def lease_snapshot(now: float | None = None) -> dict:
    """The global radio lease as it stands, for a read-only status line. It doesn't
    touch a device, run readiness checks, or change the lease; an expired lease reads
    as free. The lease isn't per radio: it belongs to whichever view is driving one."""
    now = time.time() if now is None else now
    with _CONTROL_LOCK:
        held = _control["owner"] is not None and not _control_expired(now)
        return {"held": held,
                "owner": _control["owner"] if held else None,
                "held_sec": round(now - _control["since"], 1) if held else None,
                "idle_sec": round(now - _control["last"], 1) if held else None,
                "center_hz": _control["center"] if held else None,
                "span_hz": _control["span"] if held else None,
                "lease_timeout_s": LEASE_TIMEOUT}


def control_status(selected: str | None = None) -> dict:
    now = time.time()
    with _CONTROL_LOCK:
        if _control_expired(now):
            _control["owner"] = None
        owned = _control["owner"] is not None
        st = source_status(selected)
        return {
            "mode": st["mode"], "device": st["device"], "note": st["note"],
            "controlled": owned,
            "owner": _control["owner"] if owned else None,
            "center_hz": _control["center"] if owned else None,
            "span_hz": _control["span"] if owned else None,
            "held_sec": round(now - _control["since"], 1) if owned else None,
            "readiness": readiness(selected),
        }


def acquire(owner: str, center: float, span: float, device: str | None = None) -> dict:
    """Take exclusive control of the SDR for `owner`. Refuses if another live
    session already holds it (unexpired). Simulated mode has no device to hold."""
    if not owner:
        return {"ok": False, "reason": "no session id"}
    st = source_status(device)
    now = time.time()
    with _CONTROL_LOCK:
        held = None if _control_expired(now) else _control["owner"]
        if held and held != owner:
            return {"ok": False, "held_by": held,
                    "reason": "The SDR is under another session's control."}
        center, span, _ = fit_window(center, span)
        first = _control["owner"] != owner
        _control.update(owner=owner, last=now, center=int(center), span=int(span))
        if first:
            _control["since"] = now
    return {"ok": True, "owner": owner, "mode": st["mode"], "device": st["device"]}


def release(owner: str) -> dict:
    freed = False
    with _CONTROL_LOCK:
        if _control["owner"] == owner:
            _control.update(owner=None, since=0.0, last=0.0)
            freed = True
    if freed:
        # Free any radio a backend held open (e.g. the bladeRF or UHD handle) so the
        # device is available again once the operator stops the live view.
        for _mod in ("bladerf_sweep", "uhd_backend"):
            try:
                __import__(f"waveraider.{_mod}").close()
            except Exception:
                pass
    return {"ok": True}


_FLOOR_DB = -95.0
# Synthetic emitters for the simulated source, one or two per drone band:
# (freq_hz, peak_db, half_width_hz, label). The labels are for readers of this table only.
_EMITTERS = [
    (868_300_000, -66, 8_000, "868 MHz telemetry burst"),
    (915_000_000, -61, 200_000, "915 MHz ISM"),
    (1_280_000_000, -58, 9_000_000, "1.3 GHz analog video"),
    (2_437_000_000, -48, 9_000_000, "2.4 GHz Wi-Fi channel 6"),
    (2_470_500_000, -63, 400_000, "2.4 GHz narrowband control"),
    (5_200_000_000, -62, 9_000_000, "5.2 GHz digital link"),
    (5_806_000_000, -55, 11_000_000, "5.8 GHz analog video"),
]

# Sweep bounds (guardrails on user input). Views are also fitted inside one drone band.
SPAN_MIN_HZ = bands.SPAN_MIN_HZ
SPAN_MAX_HZ = bands.SPAN_MAX_HZ
FREQ_MIN_HZ = 100_000
FREQ_MAX_HZ = 6_000_000_000
BINS_MAX = 512


# --- capture backends ------------------------------------------------------
# Each SDR family needs its own capture tool/SDK. A backend matches a detected
# device by name, reports whether its tool is installed, and produces a sweep.
# Specific backends first; add one by dropping a descriptor in _BACKENDS.

def _rtl_power_ok() -> bool:
    return shutil.which("rtl_power") is not None


def _hackrf_ok() -> bool:
    return shutil.which("hackrf_sweep") is not None


def _signalhound_ok() -> bool:
    # The SignalHound SDK ships a Python API; the module name varies by series.
    for mod in ("signal_hound_sdk", "bbdevice", "smdevice", "sadevice"):
        try:
            __import__(mod)
            return True
        except Exception:
            continue
    return False


def _soapy_power_ok() -> bool:
    return shutil.which("soapy_power") is not None


def _bladerf_ok() -> bool:
    from . import bladerf_sweep
    return bladerf_sweep.available()


def _uhd_ok() -> bool:
    from . import uhd_backend
    return uhd_backend.available()


# (id, label, matches(model_lower)->bool, tool_ok()->bool, install_hint)
_BACKENDS = [
    ("rtl_power", "RTL-SDR via rtl_power",
     lambda m: "rtl" in m or "nesdr" in m or "nooelec" in m, _rtl_power_ok,
     "Install the rtl-sdr tools (they provide rtl_power)."),
    ("hackrf", "HackRF via hackrf_sweep",
     lambda m: "hackrf" in m, _hackrf_ok,
     "Install the hackrf tools (they provide hackrf_sweep)."),
    ("bladerf", "bladeRF via libbladeRF (native)",
     lambda m: "blade" in m, _bladerf_ok,
     "Install libbladeRF + numpy and the FPGA bitstream in ~/.config/Nuand/bladeRF/."),
    ("uhd", "Ettus USRP via UHD / SoapySDR",
     lambda m: any(k in m for k in ("ettus", "usrp", "b200", "b210")), _uhd_ok,
     "Install UHD python bindings (python3-uhd) or SoapySDR + soapy_power for the "
     "Ettus device module."),
    ("signalhound", "SignalHound via its SDK",
     lambda m: any(k in m for k in ("signal hound", "signalhound", "bb60", "bb200",
                                    "sm200", "sa44", "sa124", "spike")),
     _signalhound_ok,
     "Install the SignalHound SDK + its Python API (module for your series)."),
    ("soapy", "SoapySDR via soapy_power",
     lambda m: any(k in m for k in ("lime", "airspy", "usrp", "soapy")),
     _soapy_power_ok,
     "Install SoapySDR + soapy_power and the device module."),
]

# Map a device to the SoapySDR driver arg soapy_power wants.
_SOAPY_DRIVER = {"blade": "bladerf", "lime": "lime", "airspy": "airspy", "usrp": "uhd"}


def _soapy_arg(model: str) -> str | None:
    m = (model or "").lower()
    for key, drv in _SOAPY_DRIVER.items():
        if key in m:
            return f"driver={drv}"
    return None


def _detected_sdrs() -> list:
    det = interfaces.detect()
    return [d for d in det["capture_devices"] if d["kind"] == "sdr"]


def _backend_for(sdr):
    if not sdr:
        return None
    m = (sdr["model"] + " " + sdr.get("usb_name", "")).lower()
    for b in _BACKENDS:
        if b[2](m):
            return b
    return None


def _radio_info(sdr) -> dict:
    b = _backend_for(sdr)
    tool_ok = b[3]() if b else False
    # A specific, self-diagnosing hint for the UHD backend (the static one is a
    # generic "install X"; this says which path is actually missing *here*).
    install_hint = b[4] if (b and not tool_ok) else None
    if b and b[0] == "uhd" and not tool_ok:
        from . import uhd_backend
        diag = uhd_backend._native_diagnostic()
        if diag:
            install_hint = f"UHD path not usable — {diag}"
    return {
        "device": sdr["model"], "usb_id": sdr["id"],
        "backend": b[0] if b else None,
        "backend_label": b[1] if b else None,
        "tool_installed": tool_ok,
        "live_capable": bool(b and tool_ok),
        "install_hint": install_hint,
    }


def _choose(radios: list, selected: str | None):
    """Pick the radio to drive: the one whose usb_id matches `selected`, else the
    first live-capable one, else the first detected."""
    if selected:
        for r in radios:
            if r["usb_id"] == selected:
                return r
    return next((r for r in radios if r["live_capable"]), radios[0] if radios else None)


def readiness(selected: str | None = None) -> dict:
    """What the operator needs before going live: every radio plugged in, which
    backend drives each, whether its tool is installed, and how to fix it. The
    top-level fields describe the *chosen* radio. Powers the Spectrum readiness
    line, the device picker, and `wr sdr`."""
    det = interfaces.detect()
    sdrs = _detected_sdrs()
    radios = [_radio_info(s) for s in sdrs]
    chosen = _choose(radios, selected)
    base = {
        "sdr_detected": bool(sdrs),
        "radios": radios,
        "other_usb": det.get("other_usb_count", 0),
        # If nothing is recognized, surface raw USB so we can identify the radio.
        "unrecognized_usb": (interfaces.unrecognized_usb() if not sdrs else []),
    }
    if chosen:
        base.update(chosen, live_capable=chosen["live_capable"])
    else:
        base.update(device=None, usb_id=None, backend=None, backend_label=None,
                    tool_installed=False, live_capable=False, install_hint=None)
    return base


def source_status(selected: str | None = None) -> dict:
    """Which source will be used for the chosen radio, and why."""
    r = readiness(selected)
    if r["live_capable"]:
        return {"mode": "live", "device": r["device"], "backend": r["backend"],
                "note": f"Live from {r['device']} — {r['backend_label']}."}
    if r["sdr_detected"] and r["backend"] and not r["tool_installed"]:
        return {"mode": "simulated", "device": r["device"], "backend": None,
                "note": f"{r['device']} detected, but its capture tool isn't installed — "
                        f"showing simulated. {r['install_hint']}"}
    if r["sdr_detected"] and not r["backend"]:
        return {"mode": "simulated", "device": r["device"], "backend": None,
                "note": f"{r['device']} detected, but live capture for it isn't wired "
                        "yet — showing simulated."}
    return {"mode": "simulated", "device": None, "backend": None,
            "note": "No SDR detected — showing a simulated spectrum. "
                    "Connect an SDR to see live RF."}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _simulated_points(start: float, stop: float, bins: int) -> list:
    step = (stop - start) / bins
    pts = []
    for i in range(bins):
        f = start + (i + 0.5) * step
        p = _FLOOR_DB + random.uniform(-2.5, 2.5)
        for ef, epk, ehw, _ in _EMITTERS:      # wide emitters spread over bins
            if abs(f - ef) <= ehw:
                shape = 0.5 * (1 + math.cos(math.pi * (f - ef) / ehw))
                p = max(p, _FLOOR_DB + (epk - _FLOOR_DB) * shape)
        pts.append([int(f), round(p, 1)])
    # A carrier narrower than one bin still lands its energy in that bin — so at
    # wide spans it shows as a single spike instead of vanishing between bins.
    for ef, epk, ehw, _ in _EMITTERS:
        if start <= ef < stop:
            idx = min(bins - 1, max(0, int((ef - start) / step)))
            if epk > pts[idx][1]:
                pts[idx][1] = round(float(epk), 1)
    return pts


def _run_capture(cmd: list, timeout: float) -> str | None:
    """Run a capture CLI, returning stdout. On timeout, SIGKILL the whole process
    group so a hung radio tool can't linger and hold the USB device (which wedges
    the next sweep). Its own session keeps it clean across restarts."""
    import os
    import signal
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except Exception:
        return None
    try:
        out, _ = p.communicate(timeout=timeout)
        return out.decode("utf-8", "replace") if out else None
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            p.communicate(timeout=2)
        except Exception:
            pass
        return None
    except Exception:
        return None


def _rtl_power_points(start: float, stop: float, bins: int, device=None) -> list | None:
    """One rtl_power sweep, parsed to [freq_hz, db]. None if it can't run."""
    step = max(1, int((stop - start) / bins))
    out = _run_capture(
        ["rtl_power", "-f", f"{int(start)}:{int(stop)}:{step}", "-1", "-i", "1"], timeout=12)
    return _parse_power_csv(out) if out else None


def _parse_power_csv(out: str) -> list | None:
    """Parse rtl_power / hackrf_sweep CSV: date,time,Hz_low,Hz_high,Hz_step,
    samples,dB,dB,... into sorted [freq_hz, db] points."""
    pts = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            low = float(parts[2])
            st = float(parts[4])
            dbs = [float(x) for x in parts[6:]]
        except ValueError:
            continue
        for i, db in enumerate(dbs):
            pts.append([int(low + i * st), round(db, 1)])
    return sorted(pts) or None


def _hackrf_points(start: float, stop: float, bins: int, device=None) -> list | None:
    """HackRF sweep. hackrf_sweep emits rtl_power-style CSV."""
    step = max(1, int((stop - start) / bins))
    try:
        out = subprocess.run(
            ["hackrf_sweep", "-f", f"{int(start / 1e6)}:{int(stop / 1e6)}",
             "-w", str(step), "-1"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return None
    return _parse_power_csv(out)


def _soapy_power_points(start: float, stop: float, bins: int, device=None) -> list | None:
    """Sweep any SoapySDR device (e.g. bladeRF) via soapy_power, which emits
    rtl_power-style CSV. `device` is the radio's model → mapped to a driver arg
    so the right radio is used when several are attached. Exact flags confirmed
    on-device; returns None (falls back to simulated) if it can't run."""
    step = max(1, int((stop - start) / bins))
    cmd = ["soapy_power", "-f", f"{int(start)}:{int(stop)}", "-b", str(bins), "-T", "1", "-q"]
    drv = _soapy_arg(device or "")
    if drv:
        cmd += ["-d", drv]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    return _parse_power_csv(out)


def _bladerf_points(start: float, stop: float, bins: int, device=None) -> list | None:
    """Native bladeRF sweep via libbladeRF (see waveraider.bladerf_sweep)."""
    from . import bladerf_sweep
    return bladerf_sweep.sweep(start, stop, bins)


def _uhd_points(start: float, stop: float, bins: int, device=None) -> list | None:
    """Ettus USRP sweep via the UHD/SoapySDR backend (see waveraider.uhd_backend)."""
    from . import uhd_backend
    return uhd_backend.sweep(start, stop, bins)


def _signalhound_points(start: float, stop: float, bins: int, device=None) -> list | None:
    """SignalHound sweep via its SDK. SCAFFOLD — completed on-device: the Python
    module and sweep call differ by series (BB60 bbdevice / SM200 smdevice /
    SA44 sadevice). Returns None until wired, so the view falls back to simulated
    with a clear note rather than emitting fake data."""
    return None


_SWEEP_FNS = {
    "rtl_power": _rtl_power_points,
    "hackrf": _hackrf_points,
    "bladerf": _bladerf_points,
    "uhd": _uhd_points,
    "soapy": _soapy_power_points,
    "signalhound": _signalhound_points,
}


def _find_signals(points: list, floor: float, span: float, limit: int = 6) -> list:
    """The strongest carriers in view: local maxima well above the floor,
    de-duplicated by frequency, each tagged with its drone band."""
    thr = floor + 8.0
    n = len(points)
    peaks = []
    for i in range(n):
        f, db = points[i]
        if db < thr:
            continue
        if (i > 0 and points[i - 1][1] > db) or (i < n - 1 and points[i + 1][1] > db):
            continue
        peaks.append((f, db))
    peaks.sort(key=lambda p: -p[1])
    min_sep = max(50_000, span * 0.02)      # don't list two markers on one lump
    chosen = []
    for f, db in peaks:
        if all(abs(f - cf) > min_sep for cf, _ in chosen):
            chosen.append((f, db))
        if len(chosen) >= limit:
            break
    from . import uas_rf
    import bisect
    pf = [p[0] for p in points]
    out = []
    for f, db in chosen:
        mhz = f / 1e6
        # occupied bandwidth: the span around the peak that stays within 6 dB of it
        i = min(max(bisect.bisect_left(pf, f), 0), len(points) - 1)
        thr6 = db - 6
        lo = hi = i
        while lo > 0 and points[lo - 1][1] >= thr6:
            lo -= 1
        while hi < len(points) - 1 and points[hi + 1][1] >= thr6:
            hi += 1
        bw_khz = round((points[hi][0] - points[lo][0]) / 1e3, 1)
        cands = uas_rf.classify(mhz, bw_khz or None)
        # A power sweep has no packet, modulation or pilot-pair evidence.
        # Shared-band / rough-width matches are reference hints, not detections.
        # Keep the legacy badge field empty so older browser clients are safe too.
        # "protocol" stays null for older clients: there is no protocol analyzer to hand off to.
        out.append({"freq_hz": f, "db": db, "band": band_of(mhz),
                    "protocol": None, "bw_khz": bw_khz,
                    "drone": [],
                    "rf_candidates": [c["name"] for c in cands[:3]],
                    "identification": "unidentified",
                    "identification_note": (
                        "Frequency and approximate bandwidth only; no drone-specific evidence. "
                        "Wi-Fi and other shared-band signals can match these reference profiles."
                        if cands else "Frequency and approximate bandwidth only; protocol not verified."
                    )})
    return out


def _bands_in(start: float, stop: float) -> list:
    """Drone bands overlapping the window, clipped to it — for the plot overlay."""
    out = []
    for lo, hi, name in _BANDS:
        lo_hz, hi_hz = lo * 1e6, hi * 1e6
        if hi_hz > start and lo_hz < stop:
            out.append({"name": name,
                        "start_hz": int(max(lo_hz, start)),
                        "stop_hz": int(min(hi_hz, stop))})
    return out


def _rtl_capture_iq(center_hz: float, rate: float, n: int):
    """Capture IQ from an RTL-SDR via rtl_sdr (CU8 on stdout). (iq, rate) or (None, 0)."""
    import numpy as np
    try:
        out = subprocess.run(
            ["rtl_sdr", "-f", str(int(center_hz)), "-s", str(int(rate)),
             "-n", str(int(n)), "-"],
            capture_output=True, timeout=15,
        ).stdout
    except Exception:
        return None, 0
    if not out:
        return None, 0
    b = (np.frombuffer(out, dtype=np.uint8).astype(np.float32) - 127.5) / 127.5
    return b[0::2] + 1j * b[1::2], float(rate)


def analyze_signal(center_hz: float, device: str | None = None) -> dict:
    """Capture a slice of IQ at `center_hz` from the live SDR and demodulate it
    (FM today). Returns the analysis, or {available: False, note} when there's no
    live radio / the backend isn't wired for capture."""
    from . import demod
    center = _clamp(float(center_hz), FREQ_MIN_HZ, FREQ_MAX_HZ)
    status = source_status(device)
    if status["mode"] != "live":
        return {"available": False, "freq_hz": int(center),
                "note": "Connect an SDR to demodulate a live signal."}
    if not demod.available():
        return {"available": False, "freq_hz": int(center),
                "note": "numpy is required for demodulation."}
    backend = status.get("backend")
    mhz = center / 1e6
    is_video = (1080 <= mhz <= 1360) or (5645 <= mhz <= 5945)   # analog FPV video bands
    # Per-backend capture rate. The Ettus USRP (B200/B205mini/B210) and bladeRF
    # are wideband: 8 MSPS for analog video, 2 MSPS for control-link demod (well
    # inside the B205mini's ~56 MHz ceiling). The RTL-SDR is narrow: 240 kS/s.
    if is_video:
        rate = 8_000_000
    elif backend in ("bladerf", "uhd"):
        rate = 2_000_000
    else:
        rate = 240_000
    n = int(rate * 0.05)
    with _DEVICE_LOCK:
        if backend == "bladerf":
            from . import bladerf_sweep
            iq, rate = bladerf_sweep.capture_iq(center, rate, n)
        elif backend == "uhd":
            from . import uhd_backend
            iq, rate = uhd_backend.capture_iq(center, rate, n)
        elif backend == "rtl_power":
            if is_video:
                return {"available": False, "freq_hz": int(center),
                        "note": "Analog FPV video needs a wideband SDR (bladeRF); the RTL can't span it."}
            iq, rate = _rtl_capture_iq(center, 240_000, 12_000)
        else:
            return {"available": False, "freq_hz": int(center),
                    "note": f"Live demod isn't wired for the {backend} backend yet."}
    if iq is None or len(iq) < 64:
        return {"available": False, "freq_hz": int(center), "note": "capture failed."}

    if is_video:
        r = demod.analyze_video(iq, rate); r["kind"] = "video"
    else:
        r = demod.analyze_fm(iq, rate)
        if r.get("active"):
            r["kind"] = "fm"
        else:
            am = demod.analyze_am(iq, rate)     # try AM if it's not FM
            r, r["kind"] = (am, "am") if am.get("active") else (r, "fm")
    r.update(available=True, freq_hz=int(center),
             device=status.get("device"), sample_rate=int(rate))
    return r


def capture_audio(center_hz: float, device: str | None = None, secs: float = 2.5):
    """Capture a short window at `center_hz`, demodulate to audio (FM, or AM in the
    airband / AM-broadcast bands), and return WAV bytes — or a {'error': ...} dict."""
    from . import demod
    center = _clamp(float(center_hz), FREQ_MIN_HZ, FREQ_MAX_HZ)
    status = source_status(device)
    if status["mode"] != "live":
        return {"error": "Connect an SDR to listen to a live signal."}
    if not demod.available():
        return {"error": "numpy is required for audio."}
    backend = status.get("backend")
    capture_rate = 2_000_000 if backend in ("bladerf", "uhd") else 240_000
    n = int(capture_rate * max(0.5, min(6.0, secs)))
    with _DEVICE_LOCK:
        if backend == "bladerf":
            from . import bladerf_sweep
            iq, rate = bladerf_sweep.capture_iq(center, capture_rate, n)
        elif backend == "uhd":
            from . import uhd_backend
            iq, rate = uhd_backend.capture_iq(center, capture_rate, n)
        elif backend == "rtl_power":
            iq, rate = _rtl_capture_iq(center, 240_000, n)
        else:
            return {"error": f"Audio isn't wired for the {backend} backend."}
    if iq is None or len(iq) < 256:
        return {"error": "audio capture failed."}
    mhz = center / 1e6
    is_am = (118 <= mhz <= 137) or (0.5 <= mhz <= 1.71)     # airband / AM broadcast
    audio, arate = (demod.am_to_audio if is_am else demod.fm_to_audio)(iq, rate)
    if len(audio) < 64:
        return {"error": "no audio recovered."}
    return demod.to_wav(audio, arate)


DRONEID_BANDS_HZ = ((2_400_000_000, 2_483_500_000), (5_725_000_000, 5_850_000_000))
# 700 ms at 15.36 MSPS: longer than the ~600 ms DroneID burst interval, so one
# capture spans at least one burst period.
DRONEID_WINDOW_SAMPLES = 10_752_000


def validate_droneid_center(center_hz: float) -> float:
    """Accept scan centers in the supported DJI bands; never silently retune."""
    try:
        center = float(center_hz)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("DroneID scan center must be a finite number in Hz.") from None
    if not math.isfinite(center):
        raise ValueError("DroneID scan center must be a finite number in Hz.")
    if not any(low <= center <= high for low, high in DRONEID_BANDS_HZ):
        raise ValueError("DroneID scan refused: tune the center to 2400–2483.5 MHz "
                         "or 5725–5850 MHz (supported DJI bands). No capture performed.")
    return center


def scan_droneid(device: str | None = None, center_hz: float = 2_459_500_000) -> dict:
    """Capture a wideband window and scan for a DJI DroneID / OcuSync OFDM burst.
    Needs a wideband SDR (bladeRF) — the RTL can't span DroneID's ~10 MHz. Honest
    fallbacks when there's no live wideband radio."""
    center_hz = validate_droneid_center(center_hz)
    from . import droneid
    status = source_status(device)
    if status["mode"] != "live":
        return {"available": False, "note": "Connect a wideband SDR (bladeRF) to scan for DJI DroneID."}
    if not droneid.available():
        return {"available": False, "note": "numpy is required for DroneID detection."}
    if status.get("backend") not in ("bladerf", "uhd"):
        return {"available": False,
                "note": f"DroneID needs ~10 MHz / 15.36 MSPS — the {status.get('backend')} "
                        "backend can't reach that. Use a wideband SDR (bladeRF or Ettus USRP)."}
    be = status.get("backend")
    with _DEVICE_LOCK:
        if be == "bladerf":
            from . import bladerf_sweep
            iq, rate = bladerf_sweep.capture_iq(center_hz, droneid.SAMPLE_RATE, DRONEID_WINDOW_SAMPLES)
        else:
            from . import uhd_backend
            iq, rate = uhd_backend.capture_iq(center_hz, droneid.SAMPLE_RATE, DRONEID_WINDOW_SAMPLES)
    if iq is None or len(iq) < droneid.FFT_SIZE * 4:
        return {"available": False, "note": "wideband capture failed."}
    # We captured at the detector's reference rate; detect() strictly requires it,
    # and the device's reported rate can differ by a few Hz.
    try:
        r = droneid.detect(iq, droneid.SAMPLE_RATE)
    except ValueError as e:
        return {"available": False, "note": f"DroneID detection error: {e}"}
    r.update(available=True, center_hz=int(center_hz),
             start_hz=center_hz - rate / 2, stop_hz=center_hz + rate / 2,
             sample_rate=rate, device=status.get("device"),
             window_ms=round(len(iq) / (rate or droneid.SAMPLE_RATE) * 1000, 1))
    if not r["droneid"]:
        r["note"] += (f" (Listened {r['window_ms']:.0f} ms; DroneID bursts every ~600 ms. A quiet scan "
                      "does not mean no drone is present — it may be out of range, on another channel "
                      "or not broadcasting. Continuous monitoring is not implemented.)")
    return r


def sweep(center_hz: float, span_hz: float, bins: int = 256,
          owner: str | None = None, device: str | None = None,
          elrs_hint: bool = False) -> dict:
    center, span, band = fit_window(center_hz, span_hz)   # Inspect RF only views the drone bands
    bins = int(_clamp(int(bins or 256), 16, BINS_MAX))
    start, stop = center - span / 2, center + span / 2

    status = source_status(device)
    live = status["mode"] == "live"
    controlled = False

    if live:
        # Enforce exclusive control: a live sweep must come from the owner, and
        # no one else may hold the radio. Refresh the lease so control persists
        # only while the view keeps sweeping.
        now = time.time()
        with _CONTROL_LOCK:
            held = None if _control_expired(now) else _control["owner"]
            if held and owner and held != owner:
                return {"error": "busy", "held_by": held,
                        "center_hz": int(center), "span_hz": int(span),
                        "source": "live", "device": status["device"],
                        "note": "The SDR is under another session's control."}
            if owner:
                first = _control["owner"] != owner
                _control.update(owner=owner, last=now, center=int(center), span=int(span))
                if first:
                    _control["since"] = now
                controlled = True

    points = None
    if live:
        # One consumer touches the device at a time — no fighting over the radio.
        fn = _SWEEP_FNS.get(status.get("backend"))
        with _DEVICE_LOCK:
            points = fn(start, stop, bins, status.get("device")) if fn else None
        if points is None:                       # backend present but sweep failed
            status = {"mode": "simulated", "device": status["device"],
                      "note": f"{status['device']} didn't respond to the sweep — "
                              "unplug and replug the radio to reset it. Showing simulated."}
            live = False
    if points is None:
        points = _simulated_points(start, stop, bins)

    dbs = sorted(p[1] for p in points)
    floor = dbs[len(dbs) // 4] if dbs else _FLOOR_DB
    peak = max(points, key=lambda p: p[1]) if points else [int(center), _FLOOR_DB]
    signals = _find_signals(points, floor, span)
    # Optional ELRS presence hint: when enabled on a live wideband radio, probe the
    # strongest carriers with a short IQ grab + the chirp detector, and tag a likely
    # ELRS/LoRa carrier in rf_candidates. Off by default (keeps the sweep fast and
    # the existing behavior identical); a hint, never a detection.
    if elrs_hint and live and status.get("backend") in ("bladerf", "uhd") and signals:
        _annotate_elrs_hint(signals, status.get("backend"))
    return {
        "center_hz": int(center), "span_hz": int(span), "band": band,
        "start_hz": int(start), "stop_hz": int(stop),
        "points": points,
        "floor_db": round(floor, 1),
        "peak": {"freq_hz": peak[0], "db": peak[1]},
        "signals": signals,
        "bands": _bands_in(start, stop),
        "source": "live" if live else "simulated",
        "device": status.get("device"),
        "note": status["note"],
        "controlled": controlled,
    }


def _annotate_elrs_hint(signals: list, backend: str) -> None:
    """Probe each strong carrier with a short IQ window and tag a likely ELRS/LoRa
    signal in its rf_candidates. A hint only: a high chirp score adds
    'ExpressLRS (possible)'; it never sets protocol or identification. Fails soft —
    if the radio can't grab IQ (busy/unsupported), the sweep is returned unmodified."""
    if not signals:
        return
    try:
        from .protocols.elrs import elrs_present
        from . import uhd_backend, bladerf_sweep
    except Exception:
        return
    probe_rate = 2_400_000
    probe_n = int(probe_rate * 0.05)
    for sig in signals[:6]:                       # bound the work: strongest few only
        f = sig.get("freq_hz")
        if not f:
            continue
        try:
            with _DEVICE_LOCK:
                if backend == "uhd":
                    iq, rate = uhd_backend.capture_iq(f, probe_rate, probe_n)
                else:
                    iq, rate = bladerf_sweep.capture_iq(f, probe_rate, probe_n)
            if iq is None or len(iq) < 512:
                continue
            score = elrs_present(iq, rate or probe_rate)
            if score >= 0.5 and "ExpressLRS (possible)" not in sig.get("rf_candidates", []):
                sig.setdefault("rf_candidates", []).insert(0, "ExpressLRS (possible)")
                sig["elrs_hint"] = round(score, 2)
            # Broader protocol hint: run the shared multi-protocol classifier
            # (elrs_sdr.classify) on the SAME IQ window — OFDM/Wi-Fi-class, DJI
            # OcuSync-shape, analog FPV video — and attach the best positive
            # claim as a hint. Positive-ID only: nothing is attached when no
            # detector clears its threshold. A hint, never a detection — same
            # contract as the ELRS tag above (only the LoRa chirp detector is
            # capture-validated; the rest are synthetic-calibrated, confirmed=False).
            try:
                from elrs_sdr import classify as _wr_classify
                _c = _wr_classify.classify_iq(iq, rate or probe_rate)
                if _c.signature != "unknown":
                    sig["protocol_hint"] = _c.to_dict()
            except Exception:
                pass
        except Exception:
            continue
