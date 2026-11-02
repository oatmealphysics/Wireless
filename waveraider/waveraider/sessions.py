"""Session history — one JSON record per stopped Monitor session, kept locally.

Records live next to the watchlist (~/.waveraider/sessions, or WAVERAIDER_SESSIONS). A
record is looked up by a validated session id; no function here takes a file name or a
path. Up to KEEP readable records are kept: when one is saved, the readable records with
the earliest recorded start times beyond that count are removed, never the one just saved.
Start times come from the computer's clock, so a clock change can affect which are kept.

What is trusted, and what isn't. The folder is ordinary local storage that belongs to this
user: newly written files use mode 0600 and a newly created folder 0700, where the system
enforces them. That restricts filesystem access only. The web server (web.py) serves these
records to anyone who can reach it. Where the platform allows it, a record is opened
relative to the opened folder without following a symlink, and a file with more than one
hard link is refused; elsewhere the scan only skips entries that are already symlinks.
Neither is protection against another local process that can write to the folder.

A record is only listed, loaded or saved if it is a consistent sample-session record of
the schema this version writes (today the only source is the bundled sample): anything
else is reported as unreadable rather than shown as history.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
import threading
from pathlib import Path

KEEP = 30
SCHEMA = 1
# Every field the exports and pages read from a saved observation (monitor.observation writes them).
OBSERVATION_KEYS = ("key", "evidence", "sample", "transmitter", "transport", "identity", "basic_ids",
                    "ua_type_label", "position", "alt_m", "operator", "operator_id", "first_seen",
                    "last_seen", "claimed_time_s", "stale", "messages", "protocol_version", "watchlist")
NOFOLLOW_READS = hasattr(os, "O_NOFOLLOW") and os.open in getattr(os, "supports_dir_fd", set())
_ID = re.compile(r"[0-9a-f]{32}")
_MAX_BYTES = 8 << 20
_mutating = threading.Lock()          # save, delete and clear in this process run one at a time


def folder() -> Path:
    return Path(os.environ.get("WAVERAIDER_SESSIONS", str(Path.home() / ".waveraider" / "sessions")))


def _file(session_id) -> Path:
    if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
        raise ValueError("A session id is 32 lowercase hexadecimal digits.")
    return folder() / f"{session_id}.json"


def _time(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check(record) -> dict:
    """The record, if it is a consistent sample-session record of this schema; ValueError if not."""
    def need(ok: bool, what: str) -> None:
        if not ok:
            raise ValueError(f"Not a session record this version can use: {what}.")
    need(isinstance(record, dict), "it isn't an object")
    need(record.get("schema") == SCHEMA and not isinstance(record.get("schema"), bool), "unknown schema")
    need(isinstance(record.get("session_id"), str) and bool(_ID.fullmatch(record["session_id"])), "bad session id")
    need(record.get("source") == "sample" and record.get("source_label") == "Sample data",
         "its source isn't the bundled sample")
    need(isinstance(record.get("note"), str) and bool(record["note"].strip()), "the sample note is missing")
    need(isinstance(record.get("sample"), dict) and isinstance(record["sample"].get("name"), str), "no sample provenance")
    need(_time(record.get("started_at")) and _time(record.get("stopped_at")), "start or stop time isn't a finite number")
    need(isinstance(record.get("diagnostics"), dict), "diagnostics aren't an object")
    rows = record.get("observations")
    need(isinstance(rows, list), "observations aren't a list")
    for o in rows:
        need(isinstance(o, dict) and all(k in o for k in OBSERVATION_KEYS), "an observation is missing fields")
        need(o["sample"] is True, "an observation isn't labeled as sample data")
        need(isinstance(o["basic_ids"], list) and isinstance(o["watchlist"], list), "an observation has malformed lists")
    return record


def _records() -> list[Path]:
    """Plain record files only: named by an id, regular, not a symlink."""
    try:
        entries = list(folder().iterdir())
    except OSError:
        return []
    return [f for f in entries
            if f.suffix == ".json" and _ID.fullmatch(f.stem) and not f.is_symlink() and f.is_file()]


def _bytes(f: Path) -> bytes | None:
    """The file's bytes. Where possible, opened relative to the opened folder without following a link,
    and only if what was opened is one regular, singly linked file."""
    try:
        if not NOFOLLOW_READS:
            return f.read_bytes() if f.stat().st_size <= _MAX_BYTES else None
        dir_fd = os.open(f.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            fd = os.open(f.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > _MAX_BYTES:
                return None
            return fh.read(_MAX_BYTES + 1)
    except OSError:
        return None


def _read(f: Path) -> dict | None:
    raw = _bytes(f)
    if raw is None:
        return None
    try:
        data = check(json.loads(raw.decode("utf-8")))
    except ValueError:                                   # bad JSON, bad UTF-8 or not a usable record
        return None
    return data if data["session_id"] == f.stem else None


def _summary(r: dict) -> dict:
    return {
        "session_id": r["session_id"],
        "source": r["source"],
        "source_label": r["source_label"],
        "sample": r["sample"]["name"],
        "started_at": r["started_at"],
        "stopped_at": r["stopped_at"],
        "stop_reason": r.get("stop_reason"),
        "error": r.get("error"),
        "frames": r.get("frames"),
        "observations": len(r["observations"]),
        "diagnostics": r["diagnostics"].get("total", 0),
    }


def save(record: dict) -> None:
    """Write one checked record whole (temporary file, then rename), then remove readable records
    beyond KEEP — earliest recorded start first, and never the record just saved."""
    check(record)
    target = _file(record["session_id"])
    body = json.dumps(record, indent=1, allow_nan=False)
    if len(body.encode("utf-8")) > _MAX_BYTES:             # what can't be read back must not be reported as saved
        raise ValueError(f"This session's record is too large to keep ({len(body):,} bytes; the limit is {_MAX_BYTES:,}).")
    with _mutating:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)    # a new folder is this user's only
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".saving-", suffix=".tmp")   # created 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(body)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        others = sorted(((r["started_at"], f) for f in _records()
                         if f != target and (r := _read(f)) is not None),
                        key=lambda x: (x[0], x[1].name), reverse=True)
        for _t, f in others[KEEP - 1:]:
            try:
                f.unlink()
            except OSError:
                pass


def listing() -> dict:
    """Summaries, latest recorded start first, plus the names of record files that can't be used."""
    good, unreadable = [], []
    for f in _records():
        r = _read(f)
        if r is None:
            unreadable.append(f.name)
        else:
            good.append(r)
    good.sort(key=lambda r: (r["started_at"], r["session_id"]), reverse=True)
    return {"sessions": [_summary(r) for r in good], "unreadable": sorted(unreadable),
            "keep": KEEP, "folder": str(folder())}


def load(session_id: str) -> dict | None:
    f = _file(session_id)
    return _read(f) if f in _records() else None


def delete(session_id: str) -> bool:
    """False if there is no such record, including one that was removed a moment ago by something else."""
    f = _file(session_id)
    with _mutating:
        if f not in _records():
            return False
        try:
            f.unlink()
        except FileNotFoundError:
            return False
        return True


def clear() -> int:
    """Remove the records present now. A session still running, or still being saved, can add one afterwards."""
    removed = 0
    with _mutating:
        for f in _records():
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed
