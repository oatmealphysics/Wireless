"""Monitor session — the observation feed behind the Monitor screen.

One session per process. Today the only source is a bundled Remote ID sample,
replayed through the same decoder a live receiver will use and always labeled
"sample". Replayed frames are stamped with the time this session feeds them, so
"last heard" and staleness behave the way they will for live reception; nothing
here may be presented as live. Live receivers become another source kind once a
receive path is proven.

Every start is a new session with its own id. When a session stops — stopped by the
user, replaced by a new start, or ended by a replay failure — WaveRaider attempts to
save one record of it to the local session history (sessions.py): where its frames came
from (the sample's name and file, and the file's SHA-256 as read when the session
started), when it ran, the stale threshold in force, and its observations and
diagnostics as of the stop. The status says whether that save is pending, succeeded or
failed, for that session only. A session still running when the server exits is not saved.

The HTTP API only ever names bundled samples; it never accepts a file path.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import time
import uuid
from pathlib import Path

import waveraider

from . import sessions, settings, targets
from .capture import iter_frames
from .uas_classify import (
    ID_TYPES, OPERATOR_LOCATION_TYPES, STALE_AFTER_S, TRANSPORTS, UA_TYPES,
    UasClassifier,
)

SAMPLES = Path(__file__).resolve().parent / "samples"
# Replayable samples. `transports` is what the capture actually contains, which is
# what a session replaying it covers — not everything the decoder can read.
SAMPLE_SOURCES = {
    "remote-id-wifi": {"file": "uas.pcap",
                       "description": "Bundled Remote ID sample (two transmitters, Wi-Fi beacons)",
                       "transports": ["wifi_beacon"]},
}
DEFAULT_SAMPLE = "remote-id-wifi"
REPLAY_INTERVAL_S = 0.25          # gap between replayed frames
REPLAY_PASS_PAUSE_S = 5.0         # pause before the sample loops again
STOP_JOIN_TIMEOUT_S = 2.0         # how long stop() waits for the replay worker
RECEIVER_CACHE_S = 10.0           # hardware detection shells out; don't repeat it every poll

SAMPLE_NOTE = ("Sample data replayed from a bundled capture, not live reception. First and last "
               "received times are when this session replayed each frame, not the original capture "
               "times. Values are as broadcast, not authenticated.")
TRANSPORT_COVERAGE = {"wifi_beacon": "Wi-Fi beacon Remote ID", "ble": "Bluetooth legacy Remote ID"}
DECODER_SUPPORT = ["Remote ID in recorded Wi-Fi beacons",
                   "Remote ID in recorded Bluetooth legacy advertisements"]
ALWAYS_NOT_COVERED = ["Wi-Fi NAN", "Bluetooth 5 Long Range", "live Remote ID reception"]
# Diagnostic reasons where the decoder kept the rest of the report (uas_classify.update).
# Every other reason, including "invalid message pack content", rejected the whole input.
PARTIAL_REASONS = ("invalid drone position", "invalid operator position",
                   "invalid location timestamp", "unsupported message skipped")


def coverage(state: str, source: str | None, sample: str | None) -> dict:
    """What the session covers while running, what stopped rows were retained from,
    and what isn't covered. Sample coverage comes from the selected capture."""
    meta = SAMPLE_SOURCES.get(sample) if source == "sample" else None
    have = meta["transports"] if meta else []
    labels = [f"{TRANSPORT_COVERAGE[t]} (recorded sample)" for t in have]
    return {
        "active": state == "running" and bool(have),
        "covered": labels if state == "running" else [],
        "retained": labels if state == "stopped" else [],
        "not_covered": [TRANSPORT_COVERAGE[t] for t in TRANSPORT_COVERAGE if t not in have] + ALWAYS_NOT_COVERED,
        "decoder_support": list(DECODER_SUPPORT),
    }


def diagnostic_kind(reason: str) -> str:
    """"partial": an invalid field or unsupported message was left out and the rest of the
    report kept (only PARTIAL_REASONS); "rejected": nothing from the input was used."""
    return "partial" if reason in PARTIAL_REASONS else "rejected"


def lifecycle(age_s: float, stale_after_s: float, lapsed_after_s: float) -> str:
    """Where an observation stands by how long ago it was last updated: "fresh", then "stale", then
    "lapsed" (past the age threshold; listed as older). Both thresholds are provisional settings, not
    tuned values, and a lapsed observation is still kept, listed and exported: nothing is removed from
    a session. It describes updates arriving on this computer, not whether an aircraft is present."""
    if age_s <= stale_after_s:
        return "fresh"
    return "stale" if age_s <= max(lapsed_after_s, stale_after_s) else "lapsed"


def _age(a: dict, now: float, elapsed_now: float | None) -> float:
    """Seconds since the last update: by elapsed (monotonic) time when the session stamped one, so a
    system clock set backwards or forwards can't make an observation look younger or older."""
    if elapsed_now is not None and "last_elapsed" in a:
        return elapsed_now - a["last_elapsed"]
    return now - a.get("last_seen", now)


def observation(e, now: float, watch: list, source: str | None, stale_after_s: float = STALE_AFTER_S,
                lapsed_after_s: float | None = None, elapsed_now: float | None = None) -> dict:
    """One transmitter-on-one-transport observation, as the Monitor screen shows it."""
    a = e.attrs
    gap = a.get("last_gap")
    lapsed_after_s = settings.LAPSED_AFTER_S if lapsed_after_s is None else lapsed_after_s
    state = lifecycle(_age(a, now, elapsed_now), stale_after_s, lapsed_after_s)
    return {
        "key": e.id,
        "evidence": "remote_id",
        "sample": source == "sample",
        "transmitter": a.get("addr", e.id),
        "transport": a.get("transport"),
        "transport_label": TRANSPORTS.get(a.get("transport"), "Unknown transport"),
        "identity": a.get("uas_id"),                       # None: identity not reported
        "basic_ids": [{"id_type": t, "id_type_label": ID_TYPES.get(t, f"Reserved ({t})"),
                       "text": v["text"], "raw": v["raw"]}
                      for t, v in sorted(a.get("basic_ids", {}).items())],
        "ua_type": a.get("ua_type"),
        "ua_type_label": UA_TYPES.get(a.get("ua_type")),
        "position": [a["lat"], a["lon"]] if "lat" in a else None,
        "alt_m": a.get("alt"),
        "operator": ({"lat": a["op_lat"], "lon": a["op_lon"],
                      "type": OPERATOR_LOCATION_TYPES.get(a.get("op_location_type"), "undeclared type")}
                     if "op_lat" in a else None),
        "operator_id": a.get("operator_id"),
        "claimed_time_s": a.get("claimed_time_s"),
        "first_seen": a.get("first_seen"),
        "last_seen": a.get("last_seen"),
        "stale": state != "fresh",
        "lifecycle": state,
        "sequence_started": a.get("sequence_started", a.get("first_seen")),
        "update_gaps": {"count": a.get("update_gaps", 0),
                        "last_gap_s": round(gap["seconds"], 3) if gap else None,
                        "at": gap["at"] if gap else None},
        "messages": e.observations,
        "protocol_version": a.get("protocol_version"),
        "watchlist": targets.match(e, watch=watch),
    }


def _utc(ts) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""


RECORD_SCHEMA = sessions.SCHEMA


def _provenance(name: str) -> dict:
    """Where a sample session's frames come from, taken when the session starts. The SHA-256 is of
    the sample file as read then; each replay pass reads the file again, so it identifies the
    replayed bytes only if the file doesn't change while the session runs. A file that can't be
    read is recorded as that: a session whose sample is unreadable still gets a record."""
    meta = SAMPLE_SOURCES[name]
    out = {"name": name, "description": meta["description"], "file": meta["file"], "sha256": None, "sha256_error": None}
    try:
        out["sha256"] = hashlib.sha256((SAMPLES / meta["file"]).read_bytes()).hexdigest()
    except OSError as exc:
        out["sha256_error"] = f"{type(exc).__name__}: {exc}"
    return out


CSV_COLUMNS = ["source", "key", "evidence", "transmitter", "transport", "identity", "basic_ids",
               "ua_type", "lat", "lon", "alt_m", "operator_lat", "operator_lon",
               "operator_location_type", "operator_id", "first_seen_utc", "last_seen_utc",
               "claimed_time_s", "stale", "messages", "protocol_version", "watchlist",
               "lifecycle", "update_gaps", "last_gap_s"]


def _csv_row(source: str, o: dict) -> list:
    pos, op = o["position"] or ["", ""], o["operator"] or {}
    ids = ";".join(f"{b['id_type_label']}:{b['raw']}" for b in o["basic_ids"])
    return [source, o["key"], o["evidence"], o["transmitter"], o["transport"] or "",
            o["identity"] or "", ids, o["ua_type_label"] or "", pos[0], pos[1],
            "" if o["alt_m"] is None else o["alt_m"], op.get("lat", ""), op.get("lon", ""),
            op.get("type", ""), o["operator_id"] or "", _utc(o["first_seen"]), _utc(o["last_seen"]),
            "" if o["claimed_time_s"] is None else o["claimed_time_s"], str(o["stale"]).lower(),
            o["messages"], "" if o["protocol_version"] is None else o["protocol_version"],
            ";".join(o["watchlist"])] + _lifecycle_cells(o)


def _lifecycle_cells(o: dict) -> list:
    """Blank for a record saved before the lifecycle existed."""
    gaps = o.get("update_gaps") or {}
    gap = gaps.get("last_gap_s")
    return [o.get("lifecycle") or "", "" if "update_gaps" not in o else gaps.get("count", 0),
            "" if gap is None else gap]


def _csv(header: str, source: str | None, label: str, rows: list) -> str:
    buf = io.StringIO()
    buf.write(f"# {header}\n")
    if source == "sample":
        buf.write(f"# {SAMPLE_NOTE}\n")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows(_csv_row(label, o) for o in rows)
    return buf.getvalue()


def export_record(record: dict, fmt: str = "json") -> tuple[str, str, str]:
    """A saved session record -> (body, content type, filename), shaped like a session export."""
    if fmt not in ("json", "csv"):
        raise ValueError("format must be 'json' or 'csv'")
    source, sid = record.get("source"), record["session_id"]
    label = source or "idle"
    name = f"waveraider-{label}-session-{sid[:8]}.{fmt}"
    rows = record.get("observations") or []
    if fmt == "json":
        doc = {"exported_at_utc": _utc(time.time()), "saved_record": True, "source": source,
               "note": record.get("note"),
               "session": {k: v for k, v in record.items() if k not in ("observations", "diagnostics", "note")},
               "diagnostics": record.get("diagnostics"), "observations": rows}
        return json.dumps(doc, indent=2), "application/json", name
    header = (f"WaveRaider Monitor saved session {sid}, stopped {record.get('stopped_at_utc') or 'unknown'}; "
              f"observations as of the stop")
    return _csv(header, source, label, rows), "text/csv; charset=utf-8", name


class _Run:
    """One start of the session. Its own stop signal and generation stop a worker that
    outlives stop() from touching a later run."""

    def __init__(self, gen: int) -> None:
        self.gen = gen
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None


class MonitorSession:
    def __init__(self, interval_s: float = REPLAY_INTERVAL_S, pass_pause_s: float = REPLAY_PASS_PAUSE_S,
                 join_timeout_s: float = STOP_JOIN_TIMEOUT_S, clock=time.time, stale_after=None, mono=None) -> None:
        self.interval_s, self.pass_pause_s, self.join_timeout_s, self.clock = \
            interval_s, pass_pause_s, join_timeout_s, clock
        # Ages and gaps are measured on elapsed time, which the system can't set backwards; `clock` (wall
        # time) only stamps what is shown and exported. A test that injects one clock gets it for both.
        self.mono = mono or (time.monotonic if clock is time.time else clock)
        self.stale_after = stale_after or settings.stale_after_s   # read on every poll, so a change applies at once
        self.lapsed_after = settings.lapsed_after_s
        self._lifecycle = threading.Lock()   # serializes whole start/stop transitions
        self._lock = threading.RLock()       # guards decoder state; never held while joining a worker
        self._run: _Run | None = None
        self._generation = 0
        self.sample: str | None = None
        self._reset("idle", None)

    def _reset(self, state: str, source: str | None) -> None:
        self.state, self.source = state, source
        self.session_id = uuid.uuid4().hex if state == "running" else None
        self.provenance: dict | None = None
        self.history = {"state": None, "error": None}      # this session's save: None, pending, saved or failed
        self.started_at = self.stopped_at = None
        self._stopped_elapsed: float | None = None
        self.passes = 0
        self.error: str | None = None
        label = SAMPLE_SOURCES[self.sample]["file"] if self.sample else "none"
        self.classifier = UasClassifier(path=f"{source or 'idle'}:{label}", gap_after=self.stale_after)

    # ---- control -----------------------------------------------------------

    def start(self, source: str = "sample", sample: str = DEFAULT_SAMPLE, threaded: bool = True) -> dict:
        if source != "sample":
            raise ValueError("Only sample replay is available. Live Remote ID capture isn't supported yet.")
        if sample not in SAMPLE_SOURCES:
            raise ValueError(f"Unknown sample {sample!r}. Available: {', '.join(SAMPLE_SOURCES)}.")
        provenance = _provenance(sample)                   # reads the sample file: before, not under, either lock
        with self._lifecycle:
            self._stop_current("replaced")
            with self._lock:
                self.sample = sample
                self._reset("running", "sample")
                self.provenance = provenance
                self.started_at = self.clock()
                self._generation += 1
                run = self._run = _Run(self._generation)
            if threaded:
                run.thread = threading.Thread(target=self._worker, args=(run,), daemon=True,
                                              name=f"monitor-replay-{id(self)}-{run.gen}")
                run.thread.start()
        return self.status()

    def stop(self) -> dict:
        with self._lifecycle:
            self._stop_current("stopped")
        return self.status()

    def _stop_current(self, reason: str) -> None:
        """Caller holds the lifecycle lock."""
        run = self._run
        if run is None:
            return
        run.stop.set()
        if run.thread is not None and run.thread is not threading.current_thread():
            run.thread.join(timeout=self.join_timeout_s)   # one that outlives this can no longer write
        pending = None
        with self._lock:
            if self._run is run:
                self._run = None
                if self.state == "running":
                    self.state, self.stopped_at, self._stopped_elapsed = "stopped", self.clock(), self.mono()
                    pending = self._snapshot(reason)
        self._save(pending)

    def _snapshot(self, reason: str):
        """(session id, record or None, why not). Caller holds the state lock, after the stop. Building
        the record can fail (it reads the watchlist and the stale setting); that is a failed save for
        this session, never an exception out of stop() or the worker."""
        self.history = {"state": "pending", "error": None}
        try:
            return self.session_id, self._record(reason), None
        except Exception as exc:
            return self.session_id, None, f"{type(exc).__name__}: {exc}"

    def _record(self, reason: str) -> dict:
        """The session as it stands at its stop. Caller holds the state lock, after the stop."""
        after, lapsed = self.stale_after(), self.lapsed_after()
        watch = targets.load()
        rows = [observation(e, self.stopped_at, watch, self.source, after, lapsed, self._stopped_elapsed)
                for e in self.classifier.survey.of_kind("uas")]
        rows.sort(key=lambda r: (-(r["last_seen"] or 0), r["transmitter"]))
        return {
            "schema": RECORD_SCHEMA,
            "session_id": self.session_id,
            "app_version": waveraider.__version__,
            "source": self.source,
            "source_label": "Sample data" if self.source == "sample" else None,
            "sample": dict(self.provenance) if self.provenance else None,
            "note": SAMPLE_NOTE if self.source == "sample" else None,
            "started_at": self.started_at, "stopped_at": self.stopped_at,
            "started_at_utc": _utc(self.started_at), "stopped_at_utc": _utc(self.stopped_at),
            "stop_reason": reason, "error": self.error,
            "frames": self.classifier.survey.total_frames, "passes": self.passes,
            "stale_after_s": after, "stale_after_provisional": True, "lapsed_after_s": lapsed,
            "coverage": coverage("stopped", self.source, self.sample),
            "diagnostics": self.diagnostics(),
            "as_of": self.stopped_at,
            "observations": rows,
        }

    def _save(self, pending) -> None:
        """Never under the state lock: this writes a file. The result belongs to the session it came
        from: if a newer session has started meanwhile, it is not written into that one's status."""
        if pending is None:
            return
        session_id, record, problem = pending
        if record is not None:
            try:
                sessions.save(record)
            except Exception as exc:
                problem = f"{type(exc).__name__}: {exc}"
        with self._lock:
            if self.session_id == session_id:
                self.history = ({"state": "saved", "error": None} if problem is None else
                                {"state": "failed", "error": f"This session wasn't saved to history: {problem}"})

    def _worker(self, run: _Run) -> None:
        try:
            while not run.stop.is_set():
                self._replay(run, pace=True)
                if run.stop.wait(self.pass_pause_s):
                    break
        except Exception as exc:   # surface replay failures instead of silently going quiet
            pending = None
            with self._lock:
                if self._run is run:
                    self.error = f"{type(exc).__name__}: {exc}"
                    self.state, self.stopped_at, self._stopped_elapsed, self._run = "stopped", self.clock(), self.mono(), None
                    pending = self._snapshot("error")
            self._save(pending)

    def _feed(self, pkt, run: _Run | None) -> bool:
        pkt.time = self.clock()
        with self._lock:
            if run is not None and self._run is not run:
                return False                                # that run was stopped or replaced
            self.classifier.update(pkt, elapsed=self.mono())
        return True

    def feed(self, pkt) -> None:
        """Decode one frame into the current session, stamped with the time it was received."""
        self._feed(pkt, None)

    def _replay(self, run: _Run, pace: bool) -> int:
        fed = 0
        for pkt in iter_frames(SAMPLES / SAMPLE_SOURCES[self.sample]["file"]):
            if run.stop.is_set() or not self._feed(pkt, run):
                break
            fed += 1
            if pace and run.stop.wait(self.interval_s):
                break
        with self._lock:
            if self._run is run:
                self.passes += 1
        return fed

    def replay_pass(self, pace: bool = False) -> int:
        """Feed the selected sample once into the running session; returns frames fed."""
        run = self._run
        if run is None:
            raise RuntimeError("Start a session before replaying.")
        return self._replay(run, pace)

    # ---- reads -------------------------------------------------------------

    def status(self) -> dict:
        stale_after_s, lapsed_after_s = self.stale_after(), self.lapsed_after()
        with self._lock:
            meta = SAMPLE_SOURCES.get(self.sample) if self.source == "sample" else None
            now, elapsed, counts = self.clock(), self.mono(), {"fresh": 0, "stale": 0, "lapsed": 0}
            for e in self.classifier.survey.of_kind("uas"):
                counts[lifecycle(_age(e.attrs, now, elapsed), stale_after_s, lapsed_after_s)] += 1
            return {
                "state": self.state,
                "session_id": self.session_id,
                "history": dict(self.history),
                "source": self.source,
                "source_label": "Sample data" if self.source == "sample" else None,
                "sample": {"name": self.sample, "description": meta["description"]} if meta else None,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "now": now,
                "frames": self.classifier.survey.total_frames,
                "passes": self.passes,
                "stale_after_s": stale_after_s,
                "stale_after_provisional": True,
                "lapsed_after_s": max(lapsed_after_s, stale_after_s),
                "lifecycle_counts": counts,
                "error": self.error,
                "coverage": coverage(self.state, self.source, self.sample),
            }

    def observations(self, since: float | None = None) -> dict:
        watch, after, lapsed = targets.load(), self.stale_after(), self.lapsed_after()
        with self._lock:
            now, elapsed = self.clock(), self.mono()
            rows = [observation(e, now, watch, self.source, after, lapsed, elapsed)
                    for e in self.classifier.survey.of_kind("uas")]
        if since is not None:
            rows = [r for r in rows if (r["last_seen"] or 0) > since]
        rows.sort(key=lambda r: (-(r["last_seen"] or 0), r["transmitter"]))
        return {"now": now, "source": self.source, "stale_after_s": after, "observations": rows}

    def observation(self, key: str) -> dict | None:
        return next((o for o in self.observations()["observations"] if o["key"] == key), None)

    def diagnostics(self) -> dict:
        """Diagnostic issues by reason. One input can raise several (e.g. two invalid fields)."""
        with self._lock:
            counts = dict(self.classifier.survey.diagnostics)
        reasons = [{"reason": r, "count": n, "kind": diagnostic_kind(r)}
                   for r, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        return {"total": sum(counts.values()),
                "rejected": sum(r["count"] for r in reasons if r["kind"] == "rejected"),
                "partial": sum(r["count"] for r in reasons if r["kind"] == "partial"),
                "reasons": reasons}

    # ---- actions -----------------------------------------------------------

    def watch(self, key: str) -> dict:
        """Watch an observation's reported identity exactly as decoded, or its transmitter
        address when no identity was reported. KeyError for an unknown key."""
        o = self.observation(key)
        if o is None:
            raise KeyError(key)
        ttype, value = ("uas_id", o["identity"]) if o["identity"] else ("mac", o["transmitter"])
        if not value.strip():
            raise ValueError("This identifier is only spaces, so it can't be watched. Watch the transmitter address instead.")
        targets.add(ttype, value, note="Added from Monitor", exact=True)
        matches = (self.observation(key) or {}).get("watchlist", [])
        return {"type": ttype, "value": value, "matches": matches, "matched": value in matches}

    def export(self, fmt: str = "json", key: str | None = None) -> tuple[str, str, str]:
        """-> (body, content type, filename). KeyError for an unknown key, ValueError for a bad format."""
        if fmt not in ("json", "csv"):
            raise ValueError("format must be 'json' or 'csv'")
        data = self.observations()
        rows = data["observations"]
        if key is not None:
            rows = [r for r in rows if r["key"] == key]
            if not rows:
                raise KeyError(key)
        label = self.source or "idle"
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(data["now"]))
        name = f"waveraider-{label}-{'observation' if key else 'session'}-{stamp}.{fmt}"
        if fmt == "json":
            doc = {"exported_at_utc": _utc(data["now"]), "source": self.source,
                   "note": SAMPLE_NOTE if self.source == "sample" else None,
                   "session": {k: v for k, v in self.status().items() if k != "now"},
                   "diagnostics": self.diagnostics(), "observations": rows}
            return json.dumps(doc, indent=2), "application/json", name
        header = f"WaveRaider Monitor export, {_utc(data['now'])}, session {self.session_id or 'none'}"
        return _csv(header, self.source, label, rows), "text/csv; charset=utf-8", name


_receivers_lock = threading.Lock()
_receivers_cache: dict = {"at": 0.0, "value": None}


def receivers(force: bool = False, readiness=None) -> dict:
    """Remote ID receive paths plus attached radios, for the receiver strip.

    Live Remote ID capture isn't supported yet, so those paths never report ready.
    `readiness` is injectable for tests and bypasses the cache."""
    now = time.time()
    cacheable = readiness is None
    if cacheable:
        with _receivers_lock:
            cached = _receivers_cache["value"]
            if cached and not force and now - _receivers_cache["at"] < RECEIVER_CACHE_S:
                return cached
        from . import spectrum
        readiness = spectrum.readiness
    try:
        detected, error = readiness(), None
    except Exception as exc:
        detected, error = {"radios": []}, f"Couldn't check attached radios: {exc}"
    items = [
        {"id": "remote-id-wifi", "name": "Remote ID · Wi-Fi beacons", "role": "Remote ID",
         "state": "unavailable", "state_label": "No receiver",
         "note": "Live Wi-Fi Remote ID capture isn't supported yet."},
        {"id": "remote-id-bluetooth", "name": "Remote ID · Bluetooth", "role": "Remote ID",
         "state": "not_set_up", "state_label": "Not set up",
         "note": "Live Bluetooth Remote ID capture isn't supported yet."},
    ]
    for rd in detected.get("radios", []):
        ready = bool(rd.get("live_capable"))
        items.append({
            "id": f"radio:{rd.get('usb_id')}", "name": rd.get("device") or "SDR", "role": "Inspect RF",
            "usb_id": rd.get("usb_id"), "state": "ready" if ready else "not_ready",
            "state_label": "Ready" if ready else "Not ready",
            "note": (rd.get("backend_label") or "Live capture available") if ready
                    else (rd.get("install_hint") or "Live capture for this radio isn't available."),
        })
    value = {"checked_at": now, "receivers": items, "error": error}
    if cacheable:
        with _receivers_lock:
            _receivers_cache.update(at=now, value=value)
    return value


SESSION = MonitorSession()
