"""Session history store: one JSON record per stopped Monitor session, kept locally,
looked up only by id, pruned to the newest KEEP."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from waveraider import sessions


def _obs(key: str) -> dict:
    return {"key": key, "evidence": "remote_id", "sample": True, "transmitter": key, "transport": "wifi_beacon",
            "identity": None, "basic_ids": [], "ua_type_label": None, "position": None, "alt_m": None,
            "operator": None, "operator_id": None, "first_seen": 1.0, "last_seen": 2.0, "claimed_time_s": None,
            "stale": False, "messages": 3, "protocol_version": None, "watchlist": []}


def _record(n: int, **extra) -> dict:
    return {"schema": 1, "session_id": f"{n:032x}", "source": "sample", "source_label": "Sample data",
            "note": "Sample data replayed from a bundled capture, not live reception.",
            "sample": {"name": "remote-id-wifi"}, "started_at": 1_800_000_000.0 + n,
            "stopped_at": 1_800_000_010.0 + n, "stop_reason": "stopped", "error": None, "frames": 12,
            "diagnostics": {"total": 0}, "observations": [_obs("a"), _obs("b")], **extra}


def test_the_history_folder_comes_from_the_environment_and_defaults_beside_the_watchlist(monkeypatch, tmp_path):
    assert sessions.folder() == tmp_path / "sessions"                    # set by the autouse fixture
    monkeypatch.delenv("WAVERAIDER_SESSIONS")
    assert sessions.folder() == Path.home() / ".waveraider" / "sessions"


def test_a_saved_record_can_be_listed_and_loaded_by_its_id():
    sessions.save(_record(1))
    listing = sessions.listing()
    assert [s["session_id"] for s in listing["sessions"]] == [f"{1:032x}"]
    s = listing["sessions"][0]
    assert s["observations"] == 2 and s["frames"] == 12 and s["diagnostics"] == 0 and s["stop_reason"] == "stopped"
    assert s["sample"] == "remote-id-wifi" and s["source_label"] == "Sample data"
    assert [o["key"] for o in sessions.load(f"{1:032x}")["observations"]] == ["a", "b"]
    assert sessions.load(f"{2:032x}") is None
    assert listing["keep"] == sessions.KEEP and listing["folder"] == str(sessions.folder())


def test_records_are_private_files_written_whole():
    sessions.save(_record(1))
    files = list(sessions.folder().iterdir())
    assert [f.name for f in files] == [f"{1:032x}.json"]                 # no temporary file left behind
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


def test_newest_first_and_only_the_newest_are_kept():
    for n in range(sessions.KEEP + 3):
        sessions.save(_record(n))
    ids = [s["session_id"] for s in sessions.listing()["sessions"]]
    assert len(ids) == sessions.KEEP and ids[0] == f"{sessions.KEEP + 2:032x}" and f"{0:032x}" not in ids
    assert len(list(sessions.folder().iterdir())) == sessions.KEEP


@pytest.mark.parametrize("bad", ["../../etc/passwd", "abc", "", "G" * 32, f"{1:032x}.json", f"{1:032x}/..", "A" * 32])
def test_an_id_is_thirty_two_lowercase_hex_digits_and_nothing_else(bad):
    for call in (sessions.load, sessions.delete):
        with pytest.raises(ValueError):
            call(bad)
    with pytest.raises(ValueError):
        sessions.save({**_record(1), "session_id": bad})


def test_delete_and_clear_remove_only_session_records():
    sessions.save(_record(1))
    sessions.save(_record(2))
    other = sessions.folder() / "notes.txt"
    other.write_text("not ours")
    assert sessions.delete(f"{1:032x}") is True and sessions.delete(f"{1:032x}") is False
    assert sessions.clear() == 1 and other.exists()
    assert sessions.listing()["sessions"] == []


def test_a_damaged_record_is_reported_and_does_not_hide_the_others():
    sessions.save(_record(1))
    (sessions.folder() / f"{2:032x}.json").write_text("{not json")
    (sessions.folder() / f"{3:032x}.json").write_text(json.dumps(["a", "list"]))
    listing = sessions.listing()
    assert [s["session_id"] for s in listing["sessions"]] == [f"{1:032x}"]
    assert sorted(listing["unreadable"]) == [f"{2:032x}.json", f"{3:032x}.json"]
    assert sessions.load(f"{2:032x}") is None


def test_a_record_that_is_a_symlink_is_neither_read_nor_followed_when_deleting(tmp_path):
    sessions.save(_record(1))
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps(_record(9)))
    os.symlink(secret, sessions.folder() / f"{9:032x}.json")
    assert sessions.load(f"{9:032x}") is None
    assert [s["session_id"] for s in sessions.listing()["sessions"]] == [f"{1:032x}"]
    sessions.clear()
    assert secret.exists()


def test_an_empty_or_missing_folder_is_an_empty_history():
    assert sessions.listing()["sessions"] == [] and sessions.clear() == 0 and sessions.delete(f"{1:032x}") is False


def test_the_history_folder_is_created_private():
    sessions.save(_record(1))
    assert stat.S_IMODE(sessions.folder().stat().st_mode) == 0o700


# ---- Review (Codex, 2026-09-19): SH-4…SH-7 ------------------------------------------

def _write(n: int, record) -> None:
    sessions.folder().mkdir(parents=True, exist_ok=True)
    (sessions.folder() / f"{n:032x}.json").write_text(json.dumps(record))


@pytest.mark.parametrize("change", [
    {"source": "live", "source_label": "Live", "note": None},            # the reviewer's probe: a sample record edited to read as live
    {"source_label": "Live"}, {"note": None}, {"note": ""}, {"schema": 2}, {"schema": None},
    {"observations": [{"key": "a"}]},                                    # would crash the CSV export
    {"observations": [{**_obs("a"), "sample": False}]},                  # a sample session whose rows don't say sample
    {"observations": "many"}, {"diagnostics": []}, {"sample": None},
    {"started_at": float("inf")}, {"stopped_at": "yesterday"}, {"started_at": True},
])
def test_a_record_that_is_not_a_consistent_sample_record_is_unreadable_not_trusted(change):
    """SH-4."""
    sessions.save(_record(1))
    _write(2, {**_record(2), **change})
    listing = sessions.listing()
    assert [s["session_id"] for s in listing["sessions"]] == [f"{1:032x}"] and listing["unreadable"] == [f"{2:032x}.json"]
    assert sessions.load(f"{2:032x}") is None
    json.loads(json.dumps(listing, allow_nan=False))                     # the listing is strict JSON whatever the files hold
    with pytest.raises(ValueError):
        sessions.save({**_record(3), **change})
    assert sessions.load(f"{3:032x}") is None


def test_every_field_the_exports_read_is_required_of_a_saved_observation():
    from waveraider import monitor
    record = _record(1)
    monitor.export_record(record, "csv")
    monitor.export_record(record, "json")
    for key in sessions.OBSERVATION_KEYS:
        broken = {**record, "observations": [{k: v for k, v in _obs("a").items() if k != key}]}
        with pytest.raises(ValueError):
            sessions.save(broken)


@pytest.mark.skipif(not sessions.NOFOLLOW_READS, reason="this platform has no no-follow open relative to a directory")
def test_a_record_swapped_for_a_link_after_the_scan_is_still_not_read(tmp_path, monkeypatch):
    """SH-5. The reviewer's probe: the scan sees an ordinary file; it is a link by the time it is opened."""
    sessions.save(_record(1))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({**_record(1), "probe_secret": "leaked"}))
    target = sessions.folder() / f"{1:032x}.json"
    real = sessions._records

    def scan_then_swap(kind):
        def scan():
            found = real()
            target.unlink()
            (os.symlink if kind == "symlink" else os.link)(outside, target)
            return found
        return scan
    for kind in ("symlink", "hardlink"):
        monkeypatch.setattr(sessions, "_records", scan_then_swap(kind))
        assert sessions.load(f"{1:032x}") is None
        monkeypatch.setattr(sessions, "_records", real)
        target.unlink()
        sessions.save(_record(1))


def test_the_record_just_saved_is_never_the_one_retention_removes():
    """SH-6. The reviewer's probe: a clock correction makes the newest session look like the oldest."""
    for n in range(sessions.KEEP):
        sessions.save(_record(1000 + n))
    early = _record(1, started_at=1_000.0, stopped_at=1_010.0)
    sessions.save(early)
    ids = [s["session_id"] for s in sessions.listing()["sessions"]]
    assert early["session_id"] in ids and len(ids) == sessions.KEEP


def test_deleting_something_already_gone_is_a_quiet_no(monkeypatch):
    """SH-7. The reviewer's probe: the file disappears between the scan and the unlink."""
    sessions.save(_record(1))
    target = sessions.folder() / f"{1:032x}.json"
    real = sessions._records

    def scan_then_remove():
        found = real()
        target.unlink()
        return found
    monkeypatch.setattr(sessions, "_records", scan_then_remove)
    assert sessions.delete(f"{1:032x}") is False


def test_a_record_too_large_to_be_read_back_is_refused_not_saved_and_hidden():
    """SI-2."""
    big = _record(1, observations=[{**_obs(f"k{i}"), "identity": "x" * 4000} for i in range(2200)])
    with pytest.raises(ValueError, match="too large"):
        sessions.save(big)
    assert sessions.listing() == {**sessions.listing(), "sessions": [], "unreadable": []}
