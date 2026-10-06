"""Settings — the setup side of the Monitor/Settings skeleton.

Five pages: Receivers, Monitoring, Watchlist, Data & export, and About & activity.
They describe what WaveRaider can do today. Live Remote ID capture isn't supported
yet, so no Remote ID receiver reads ready and no listen option can be turned on.
The stale threshold is a provisional setting, not a tuned value.

Settings are stored locally next to the watchlist. The running session stays in memory;
WaveRaider attempts to save each stopped session to the local session history (sessions.py), up to 30 kept.
"""
from __future__ import annotations

import json
import os
import platform
import re
import threading
import time
from pathlib import Path

import waveraider

from . import interfaces, logbuf, sessions, targets
from .uas_classify import STALE_AFTER_S

STALE_MIN_S, STALE_MAX_S = 5.0, 600.0
LAPSED_AFTER_S, LAPSED_MAX_S = 300.0, 86400.0   # listed as older after this: provisional, like the stale threshold
LAPSED_NOTE = ("After this long without an update an observation is listed under Older observations. It is "
               "never removed from the session, its exports or its saved record. The effective age threshold "
               "is the greater of its saved value and the stale threshold; lowering stale can restore the "
               "saved value. Provisional, like the stale threshold.")
LIFECYCLE_NOTE = ("These ages and gaps describe sample replay updates on this computer. Replay pauses can create "
                  "gaps. They do not show live reception or establish that an aircraft left or returned, and "
                  "are not an alert or detection policy. Each gap is compared with the stale threshold in "
                  "effect when the next update is processed; changing it does not recalculate earlier counts.")
HARDWARE_CACHE_S = 10.0   # USB detection shells out; don't repeat it on every page load

LIVE_NOTE = "Live Remote ID capture isn't supported yet. Monitor replays a bundled sample."
LISTEN_OPTIONS = (
    ("remote-id-wifi", "Listen for Remote ID over Wi-Fi beacons",
     "Live Wi-Fi Remote ID capture isn't supported yet."),
    ("remote-id-bluetooth", "Listen for Remote ID over Bluetooth",
     "Live Bluetooth Remote ID capture isn't supported yet."),
    ("rf-sweep", "Sweep drone RF bands for activity (experimental)",
     "RF activity isn't part of monitoring yet. Inspect RF, opened from an observation or a radio under Receivers, can inspect bands."),
)

# The only identifier types the UAS watchlist accepts. Entries of older types from v0.5.1
# stay listed and removable; they match only identifier fields Remote ID observations carry.
WATCH_TYPES = {"uas_id": "UAS ID", "operator_id": "Operator ID", "mac": "Transmitter address"}
WATCH_HINTS = {"uas_id": "serial number or registration ID as broadcast",
               "operator_id": "operator ID as broadcast",
               "mac": "Wi-Fi or Bluetooth address, like 60:60:1f:00:00:01"}
LEGACY_LABELS = {"bd_addr": "BD_ADDR", "ssid": "SSID", "name": "Device name", "imsi": "IMSI",
                 "imei": "IMEI", "tmsi": "TMSI", "radio_id": "Radio ID", "cid": "Cell ID", "other": "Other"}
_MAC_HEX = re.compile(r"[0-9a-f]{12}")

KIND_LABELS = {"sdr": "SDR", "wifi": "Wi-Fi adapter", "bluetooth": "Bluetooth sniffer"}
HARDWARE_CAVEAT = ("Detected by USB ID only. Its driver, monitor mode and permissions aren't verified, "
                   "and WaveRaider doesn't capture Remote ID from it yet.")
SESSION_STORAGE = ("The running session is kept in memory. When it stops, WaveRaider attempts to save a record of "
                   "it on this computer; a session still running when the server exits is not saved. Anyone who "
                   "can reach this server can view, export or delete saved sessions.")


# ---- monitoring profile ----------------------------------------------------------

def _profile_file() -> Path:
    return Path(os.environ.get("WAVERAIDER_MONITORING", str(Path.home() / ".waveraider" / "monitoring.json")))


def _valid_stale(value) -> float:
    # Range-check before converting: comparing a huge int with a float is exact, while
    # float(10**400) overflows. NaN and infinity fail the range check too.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not STALE_MIN_S <= value <= STALE_MAX_S:
        raise ValueError(f"The stale threshold must be a number of seconds between {STALE_MIN_S:g} and {STALE_MAX_S:g}.")
    return float(value)


def _read_profile() -> tuple[dict, str | None]:
    """(saved profile, why it couldn't be read). A missing file is just the defaults."""
    f = _profile_file()
    if not f.exists():
        return {}, None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"Couldn't read {f}: {exc}. Using the default."
    if not isinstance(data, dict):
        return {}, f"Couldn't read {f}: it isn't a settings object. Using the default."
    return data, None


def _stale_setting() -> tuple[float, str | None]:
    data, error = _read_profile()
    if "stale_after_s" not in data:
        return STALE_AFTER_S, error
    try:
        return _valid_stale(data["stale_after_s"]), None
    except ValueError:
        return STALE_AFTER_S, (f"The saved stale threshold isn't valid. It must be a number of seconds between "
                               f"{STALE_MIN_S:g} and {STALE_MAX_S:g}. Using the default.")


def stale_after_s() -> float:
    """Seconds without reception before an observation is stale: the saved value, else the default."""
    return _stale_setting()[0]


def _valid_lapsed(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not STALE_MIN_S <= value <= LAPSED_MAX_S:
        raise ValueError(f"The age threshold for older observations must be a number of seconds between "
                         f"{STALE_MIN_S:g} and {LAPSED_MAX_S:g}.")
    return float(value)


def lapsed_after_s() -> float:
    """Seconds without an update before an observation is listed as older: the greater of the saved value
    (else the default) and the stale threshold. Lowering the stale threshold can restore the saved value."""
    data, _ = _read_profile()
    try:
        saved = _valid_lapsed(data["lapsed_after_s"]) if "lapsed_after_s" in data else LAPSED_AFTER_S
    except ValueError:
        saved = LAPSED_AFTER_S
    return max(saved, stale_after_s())


def set_monitoring(stale=None, lapsed=None) -> dict:
    """Save one or both thresholds. Everything is validated first and written once: a request that is
    refused changes nothing."""
    if stale is None and lapsed is None:
        raise ValueError("Send stale_after_s or lapsed_after_s. No settings were changed.")
    try:
        new_stale = stale_after_s() if stale is None else _valid_stale(stale)
        new_lapsed = None if lapsed is None else _valid_lapsed(lapsed)
        if new_lapsed is not None and new_lapsed < new_stale:
            raise ValueError(f"The age threshold for older observations can't be shorter than the stale "
                             f"threshold ({new_stale:g} s).")
    except ValueError as exc:
        raise ValueError(f"{exc} No settings were changed.") from None
    data, _ = _read_profile()           # an unreadable file is replaced
    if stale is not None:
        data["stale_after_s"] = new_stale
    if new_lapsed is not None:
        data["lapsed_after_s"] = new_lapsed
    f = _profile_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return monitoring()


def monitoring() -> dict:
    value, error = _stale_setting()
    return {
        "stale_after_s": value,
        "default_s": STALE_AFTER_S,
        "min_s": STALE_MIN_S,
        "max_s": STALE_MAX_S,
        "lapsed_after_s": lapsed_after_s(),
        "lapsed_default_s": LAPSED_AFTER_S,
        "lapsed_max_s": LAPSED_MAX_S,
        "lapsed_note": LAPSED_NOTE,
        "lifecycle_note": LIFECYCLE_NOTE,
        "provisional": True,
        "error": error,
        "listen": [{"id": i, "label": label, "available": False, "enabled": False, "reason": reason}
                   for i, label, reason in LISTEN_OPTIONS],
        "stored_in": str(_profile_file()),
    }


def set_stale_after(value) -> dict:
    v = _valid_stale(value)
    data, _ = _read_profile()           # an unreadable file is replaced
    data["stale_after_s"] = v
    f = _profile_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return monitoring()


# ---- watchlist ---------------------------------------------------------------------

def _entry(t: dict) -> dict:
    ttype = t.get("type", "other")
    return {"type": ttype, "type_label": WATCH_TYPES.get(ttype) or LEGACY_LABELS.get(ttype, ttype),
            "value": t.get("value", ""), "note": t.get("note", ""), "legacy": ttype not in WATCH_TYPES}


def watchlist() -> dict:
    return {"types": [{"type": k, "label": v, "hint": WATCH_HINTS[k]} for k, v in WATCH_TYPES.items()],
            "entries": [_entry(t) for t in targets.load()],
            "stored_in": str(targets._targets_file())}


def add_watch(ttype: str, value, note="") -> dict:
    """Add a typed-in UAS identifier. Values are trimmed; addresses are stored as
    lowercase colon-separated hex so the same address isn't added twice."""
    if ttype not in WATCH_TYPES:
        raise ValueError("Watchlist entries here are a UAS ID, an operator ID or a transmitter address.")
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"Enter the {WATCH_TYPES[ttype]} to watch.")
    if ttype == "mac":
        hexdigits = targets._norm_mac(value)
        if not _MAC_HEX.fullmatch(hexdigits):
            raise ValueError("A transmitter address is six hex bytes, like 60:60:1f:00:00:01.")
        value = ":".join(hexdigits[i:i + 2] for i in range(0, 12, 2))
        # Older releases stored addresses as typed, so compare normalized forms.
        existing = next((t for t in targets.load() if t.get("type") == "mac"
                         and targets._norm_mac(str(t.get("value", ""))) == hexdigits), None)
        if existing is not None:
            return dict(watchlist(), added=False, entry={"type": ttype, "value": existing["value"]})
    before = len(targets.load())
    added = len(targets.add(ttype, value, str(note or "").strip())) > before
    return dict(watchlist(), added=added, entry={"type": ttype, "value": value})


def remove_watch(ttype: str, value: str) -> dict:
    """Remove one entry by type and exact value. Legacy entries can be removed too."""
    before = len(targets.load())
    removed = len(targets.remove(value, ttype=ttype)) < before
    return dict(watchlist(), removed=removed)


# ---- receivers -----------------------------------------------------------------------

_hardware_lock = threading.Lock()
_hardware_cache: dict = {"at": 0.0, "value": None}


def _capture_hardware(force: bool, detect) -> list:
    if detect is not None:
        return detect()
    now = time.time()
    with _hardware_lock:
        if _hardware_cache["value"] is not None and not force and now - _hardware_cache["at"] < HARDWARE_CACHE_S:
            return _hardware_cache["value"]
    value = interfaces.detect()["capture_devices"]
    with _hardware_lock:
        _hardware_cache.update(at=now, value=value)
    return value


def radio_control(snapshot=None) -> dict:
    """The global radio lease Inspect RF takes while it drives a radio: held or free, by
    which view opening, for how long, and at what tuning. It isn't per receiver, and a
    failure to read it is reported rather than shown as free."""
    now = time.time()
    try:
        if snapshot is None:
            from . import spectrum
            snap = spectrum.lease_snapshot(now)
        else:
            snap = snapshot()
    except Exception as exc:
        return {"checked_at": now, "error": f"Couldn't read radio control: {exc}"}
    return dict(snap, checked_at=now, error=None)


def receivers(force: bool = False, readiness=None, detect=None, control=None) -> dict:
    """Monitor's receivers (Remote ID paths and radios), other capture hardware recognized
    by USB ID, and the radio control snapshot. The host network interface list is
    deliberately left out. `readiness`, `detect` and `control` are injectable for tests."""
    from . import monitor
    base = monitor.receivers(force=force) if readiness is None else monitor.receivers(readiness=readiness)
    listed = {r["usb_id"] for r in base["receivers"] if r.get("usb_id")}
    try:
        found, hardware_error = _capture_hardware(force, detect), None
    except Exception as exc:
        found, hardware_error = [], f"Couldn't list other capture hardware: {exc}"
    other = [{"id": d.get("id"), "kind": d.get("kind"), "kind_label": KIND_LABELS.get(d.get("kind"), "Other"),
              "model": d.get("model") or "USB device", "note": d.get("note", ""), "caveat": HARDWARE_CAVEAT}
             for d in found if d.get("id") not in listed]
    return {"checked_at": base["checked_at"], "receivers": base["receivers"], "other_hardware": other,
            "error": base["error"], "hardware_error": hardware_error, "radio_control": radio_control(control)}


# ---- data & export, about ------------------------------------------------------------

def data_export(session=None) -> dict:
    from . import monitor
    s = session if session is not None else monitor.SESSION
    st, diag = s.status(), s.diagnostics()
    history = sessions.listing()
    for h in history["sessions"]:
        h["exports"] = [{"label": fmt.upper(), "href": f"/api/sessions/{h['session_id']}/export?format={fmt}"}
                        for fmt in ("json", "csv")]
    return {
        "state": st["state"],
        "source": st["source"],
        "source_label": st["source_label"],
        "sample": st["sample"],
        "observations": len(s.observations()["observations"]),
        "diagnostics": diag["total"],
        "note": monitor.SAMPLE_NOTE if st["source"] == "sample" else None,
        "formats": [
            {"format": "json", "label": "JSON", "href": "/api/export?format=json",
             "detail": "session details, diagnostics and every observation"},
            {"format": "csv", "label": "CSV", "href": "/api/export?format=csv",
             "detail": "one row per observation"},
        ],
        "history": history,
        "history_error": (st.get("history") or {}).get("error"),
        "storage": {"watchlist": str(targets._targets_file()), "monitoring_profile": str(_profile_file()),
                    "session": SESSION_STORAGE, "session_history": history["folder"]},
    }


def about(session=None, started_at: float | None = None, ollama_ready: bool = False) -> dict:
    from . import monitor
    st = (session if session is not None else monitor.SESSION).status()
    return {
        "version": waveraider.__version__,
        "uptime_sec": int(time.time() - started_at) if started_at is not None else None,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "session": {k: st[k] for k in ("state", "source", "source_label")},
        "live_remote_id": False,
        "live_note": LIVE_NOTE,
        "watchlist_entries": len(targets.load()),
        "backends": {"rules": "ready", "ollama": "ready" if ollama_ready else "not running"},
        "log_lines": len(logbuf.lines()),
    }
