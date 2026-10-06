"""Settings: receivers, monitoring profile, UAS watchlist, data & export, about & activity.

Nothing here may present live Remote ID capture as available: listen options stay
off, Remote ID receivers never read ready, and exports keep the sample label. Every
test points the watchlist and monitoring profile at throwaway files."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import waveraider
from waveraider import logbuf, monitor, settings, targets
from waveraider.uas_classify import STALE_AFTER_S

DRONE_A, DRONE_B = "60:60:1f:00:00:01", "60:60:1f:00:00:02"   # transmitters in the bundled sample


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WAVERAIDER_TARGETS", str(tmp_path / "targets.json"))
    monkeypatch.setenv("WAVERAIDER_MONITORING", str(tmp_path / "monitoring.json"))
    return tmp_path


@pytest.fixture
def sample(home):
    clock = Clock()
    s = monitor.MonitorSession(clock=clock)
    s.start("sample", threaded=False)
    s.replay_pass()
    yield s, clock
    s.stop()


# ---- monitoring profile ----------------------------------------------------------

def test_default_threshold_is_provisional_and_live_listening_cannot_be_enabled(home):
    m = settings.monitoring()
    assert (m["stale_after_s"], m["default_s"], m["provisional"], m["error"]) == (STALE_AFTER_S, STALE_AFTER_S, True, None)
    assert (m["min_s"], m["max_s"]) == (5.0, 600.0)
    assert [o["id"] for o in m["listen"]] == ["remote-id-wifi", "remote-id-bluetooth", "rf-sweep"]
    assert all(o["available"] is False and o["enabled"] is False and o["reason"] for o in m["listen"])
    assert m["stored_in"] == str(home / "monitoring.json")


def test_threshold_is_validated_before_it_is_saved(home):
    assert settings.set_stale_after(45)["stale_after_s"] == 45.0
    assert json.loads((home / "monitoring.json").read_text()) == {"stale_after_s": 45.0}
    for bad in (4.9, 601, "30", True, None, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="between 5 and 600"):
            settings.set_stale_after(bad)
    assert settings.stale_after_s() == 45.0


def test_unreadable_profile_uses_the_default_and_says_why(home):
    (home / "monitoring.json").write_text("{not json")
    m = settings.monitoring()
    assert m["stale_after_s"] == STALE_AFTER_S and "Using the default" in m["error"]
    (home / "monitoring.json").write_text('{"stale_after_s": 9999}')
    m = settings.monitoring()
    assert m["stale_after_s"] == STALE_AFTER_S and "isn't valid" in m["error"]
    (home / "monitoring.json").write_text("[]")
    assert "isn't a settings object" in settings.monitoring()["error"]
    assert settings.set_stale_after(20)["error"] is None


def test_oversized_threshold_is_refused_and_a_saved_one_falls_back(sample, home):
    s, _ = sample
    settings.set_stale_after(40)
    before = (home / "monitoring.json").read_text()
    with pytest.raises(ValueError, match="between 5 and 600"):
        settings.set_stale_after(10**400)
    assert (home / "monitoring.json").read_text() == before
    (home / "monitoring.json").write_text('{"stale_after_s": 1' + "0" * 400 + "}")
    m = settings.monitoring()
    assert m["stale_after_s"] == STALE_AFTER_S and "isn't valid" in m["error"] and len(m["error"]) < 200
    assert s.status()["stale_after_s"] == STALE_AFTER_S
    data = s.observations()
    assert data["stale_after_s"] == STALE_AFTER_S and len(data["observations"]) == 2


def test_monitor_applies_a_changed_threshold_on_the_next_read(sample):
    s, clock = sample
    clock.t += 10
    assert not any(o["stale"] for o in s.observations()["observations"])
    settings.set_stale_after(5)
    data = s.observations()
    assert data["stale_after_s"] == 5.0 and all(o["stale"] for o in data["observations"])
    st = s.status()
    assert (st["stale_after_s"], st["stale_after_provisional"]) == (5.0, True)


# ---- watchlist ---------------------------------------------------------------------

def test_watchlist_accepts_only_uas_identifier_types(home):
    w = settings.watchlist()
    assert [(t["type"], t["label"]) for t in w["types"]] == \
        [("uas_id", "UAS ID"), ("operator_id", "Operator ID"), ("mac", "Transmitter address")]
    assert w["entries"] == [] and w["stored_in"] == str(home / "targets.json")
    for legacy in ("imsi", "ssid", "other", ""):
        with pytest.raises(ValueError, match="UAS ID, an operator ID or a transmitter address"):
            settings.add_watch(legacy, "001010123456789")
    assert targets.load() == []


def test_typed_values_are_trimmed_addresses_normalized_and_duplicates_reported(home):
    r = settings.add_watch("uas_id", "  DRONE-SN-001A ", " spotted at the field ")
    assert r["added"] is True and r["entry"] == {"type": "uas_id", "value": "DRONE-SN-001A"}
    assert settings.add_watch("uas_id", "DRONE-SN-001A")["added"] is False
    assert settings.add_watch("mac", "60-60-1F-00-00-01")["entry"]["value"] == DRONE_A
    assert settings.add_watch("mac", "6060.1f00.0001")["added"] is False
    for bad in ("60:60:1f", "zz:60:1f:00:00:01", "60:60:1f:00:00:01:02"):
        with pytest.raises(ValueError, match="six hex bytes"):
            settings.add_watch("mac", bad)
    with pytest.raises(ValueError, match="Enter the Operator ID"):
        settings.add_watch("operator_id", "   ")
    assert [(e["type"], e["value"], e["note"]) for e in settings.watchlist()["entries"]] == \
        [("uas_id", "DRONE-SN-001A", "spotted at the field"), ("mac", DRONE_A, "")]


def test_operator_id_and_address_entries_match_sample_observations(sample):
    s, _ = sample
    settings.add_watch("operator_id", "OP-FAA-12345")
    settings.add_watch("mac", "60:60:1F:00:00:02")
    obs = {o["transmitter"]: o for o in s.observations()["observations"]}
    assert obs[DRONE_A]["watchlist"] == ["OP-FAA-12345"]
    assert obs[DRONE_B]["watchlist"] == [DRONE_B]


def test_an_address_saved_in_another_format_is_not_added_again(sample):
    s, _ = sample
    targets.add("mac", "60-60-1F-00-00-02", "from the classic dashboard")
    r = settings.add_watch("mac", "60:60:1f:00:00:02", "new note")
    assert r["added"] is False and r["entry"] == {"type": "mac", "value": "60-60-1F-00-00-02"}
    assert targets.load() == [{"type": "mac", "value": "60-60-1F-00-00-02", "note": "from the classic dashboard"}]
    obs = {o["transmitter"]: o for o in s.observations()["observations"]}
    assert obs[DRONE_B]["watchlist"] == ["60-60-1F-00-00-02"]


def test_legacy_entries_stay_listed_and_removal_is_by_type_and_value(home):
    targets.add("imsi", "001010123456789")
    targets.add("uas_id", "SAME")
    targets.add("other", "SAME")
    by = {(e["type"], e["value"]): e for e in settings.watchlist()["entries"]}
    assert (by[("imsi", "001010123456789")]["legacy"], by[("imsi", "001010123456789")]["type_label"]) == (True, "IMSI")
    assert by[("uas_id", "SAME")]["legacy"] is False
    r = settings.remove_watch("other", "SAME")
    assert r["removed"] is True
    assert [(e["type"], e["value"]) for e in r["entries"]] == [("imsi", "001010123456789"), ("uas_id", "SAME")]
    assert settings.remove_watch("other", "SAME")["removed"] is False
    targets.remove("SAME")                                   # the classic dashboard still removes every type
    assert [e["type"] for e in settings.watchlist()["entries"]] == ["imsi"]


# ---- receivers -----------------------------------------------------------------------

RADIOS = {"radios": [{"device": "bladeRF 2.0 micro", "usb_id": "2cf0:5250", "live_capable": True,
                      "backend_label": "native libbladeRF"}]}
DETECTED = [
    {"kind": "sdr", "id": "2cf0:5250", "model": "bladeRF 2.0 micro", "note": "12-bit, 2x2 MIMO"},
    {"kind": "wifi", "id": "0e8d:7612", "model": "MT7612U (Alfa AWUS036ACM)", "note": "2.4/5 GHz"},
]


def test_receivers_add_detected_hardware_without_claiming_remote_id():
    r = settings.receivers(readiness=lambda: RADIOS, detect=lambda: DETECTED)
    states = {x["id"]: x["state"] for x in r["receivers"]}
    assert states == {"remote-id-wifi": "unavailable", "remote-id-bluetooth": "not_set_up", "radio:2cf0:5250": "ready"}
    assert [(h["id"], h["kind_label"]) for h in r["other_hardware"]] == [("0e8d:7612", "Wi-Fi adapter")]
    caveat = r["other_hardware"][0]["caveat"]
    assert "USB ID only" in caveat and "doesn't capture Remote ID" in caveat
    assert "network_interfaces" not in r and (r["error"], r["hardware_error"]) == (None, None)


def test_receiver_and_hardware_check_failures_are_reported():
    def boom():
        raise RuntimeError("usb gone")
    r = settings.receivers(readiness=boom, detect=boom)
    assert "usb gone" in r["error"] and "usb gone" in r["hardware_error"]
    assert r["other_hardware"] == [] and [x["id"] for x in r["receivers"]] == ["remote-id-wifi", "remote-id-bluetooth"]


# ---- data & export, about ------------------------------------------------------------

def test_data_page_labels_sample_exports_and_says_where_data_lives(home):
    s = monitor.MonitorSession(clock=Clock())
    idle = settings.data_export(s)
    assert (idle["state"], idle["source_label"], idle["observations"], idle["note"]) == ("idle", None, 0, None)
    s.start("sample", threaded=False)
    s.replay_pass()
    d = settings.data_export(s)
    assert (d["state"], d["source_label"], d["observations"], d["diagnostics"]) == ("running", "Sample data", 2, 0)
    assert "not live reception" in d["note"] and "not the original capture times" in d["note"]
    assert [f["href"] for f in d["formats"]] == ["/api/export?format=json", "/api/export?format=csv"]
    assert d["storage"]["watchlist"] == str(home / "targets.json")
    assert d["storage"]["monitoring_profile"] == str(home / "monitoring.json")
    assert "memory" in d["storage"]["session"] and "saved" in d["storage"]["session"]
    assert d["history"]["sessions"] == [] and d["history"]["keep"] == 30
    sid = s.status()["session_id"]
    s.stop()
    d = settings.data_export(s)
    assert d["storage"]["session_history"] == d["history"]["folder"]
    h = d["history"]["sessions"]
    assert [x["session_id"] for x in h] == [sid] and h[0]["observations"] == 2 and h[0]["source_label"] == "Sample data"
    assert h[0]["exports"] == [{"label": "JSON", "href": f"/api/sessions/{sid}/export?format=json"},
                               {"label": "CSV", "href": f"/api/sessions/{sid}/export?format=csv"}]
    assert d["history_error"] is None


def test_about_reports_version_session_and_unsupported_live_remote_id(home):
    targets.add("uas_id", "X")
    a = settings.about(session=monitor.MonitorSession(), started_at=None, ollama_ready=False)
    assert a["version"] == waveraider.__version__ and a["uptime_sec"] is None
    assert a["session"] == {"state": "idle", "source": None, "source_label": None}
    assert a["live_remote_id"] is False and "isn't supported yet" in a["live_note"]
    assert (a["watchlist_entries"], a["backends"]) == (1, {"rules": "ready", "ollama": "not running"})


# ---- HTTP routes -----------------------------------------------------------------------

@pytest.fixture
def server(monkeypatch, home):
    from waveraider import web
    s = monitor.MonitorSession(interval_s=0.001, pass_pause_s=0.01)
    monkeypatch.setattr(monitor, "SESSION", s)
    monkeypatch.setattr(settings, "receivers", lambda force=False: {
        "checked_at": 0, "receivers": [], "other_hardware": [], "error": None, "hardware_error": None, "force": force})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", s
    s.stop()
    httpd.shutdown()
    httpd.server_close()


def _call(url, data=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_settings_page_and_read_routes(server):
    base, _ = server
    code, headers, body = _call(base + "/settings")
    assert code == 200 and headers["Content-Type"].startswith("text/html") and b"<title>WaveRaider Settings</title>" in body
    assert json.loads(_call(base + "/api/settings/receivers?refresh=1")[2])["force"] is True
    assert json.loads(_call(base + "/api/settings/monitoring")[2])["provisional"] is True
    assert json.loads(_call(base + "/api/settings/watchlist")[2])["entries"] == []
    assert json.loads(_call(base + "/api/settings/data")[2])["state"] == "idle"
    about = json.loads(_call(base + "/api/settings/about")[2])
    assert about["version"] == waveraider.__version__ and about["uptime_sec"] >= 0 and about["live_remote_id"] is False


def test_settings_write_routes_validate_and_log(server, home):
    base, _ = server
    code, _, body = _call(base + "/api/settings/monitoring", {"stale_after_s": 12})
    assert code == 200 and json.loads(body)["stale_after_s"] == 12.0

    code, _, body = _call(base + "/api/settings/watchlist/add", {"type": "uas_id", "value": " SN-9 ", "note": ""})
    r = json.loads(body)
    assert code == 200 and r["added"] is True and [e["value"] for e in r["entries"]] == ["SN-9"]
    code, _, body = _call(base + "/api/settings/watchlist/remove", {"type": "uas_id", "value": "SN-9"})
    assert code == 200 and json.loads(body)["removed"] is True and targets.load() == []
    messages = [line["msg"] for line in logbuf.lines()]
    assert "stale threshold set to 12 s" in messages and "watchlist add (uas_id)" in messages
    assert "watchlist remove (uas_id)" in messages


@pytest.mark.parametrize("path, data, message", [
    ("/api/settings/monitoring", {"stale_after_s": 1}, "between 5 and 600"),
    ("/api/settings/monitoring", {}, "between 5 and 600"),
    ("/api/settings/watchlist/add", {"type": "imsi", "value": "001010123456789"}, "UAS ID, an operator ID"),
    ("/api/settings/watchlist/add", {"type": "mac", "value": "nope"}, "six hex bytes"),
])
def test_bad_settings_requests_explain_what_to_fix(server, path, data, message):
    base, _ = server
    code, _, body = _call(base + path, data)
    assert code == 400 and message in json.loads(body)["error"]


def test_oversized_threshold_post_is_refused_and_keeps_the_saved_value(server, home):
    base, _ = server
    assert _call(base + "/api/settings/monitoring", {"stale_after_s": 40})[0] == 200
    code, _, body = _call(base + "/api/settings/monitoring", {"stale_after_s": 10**400})
    assert code == 400 and "between 5 and 600" in json.loads(body)["error"]
    assert json.loads((home / "monitoring.json").read_text()) == {"stale_after_s": 40.0}
    assert json.loads(_call(base + "/api/session")[2])["stale_after_s"] == 40.0


# ---- page renderers (Node.js) ------------------------------------------------------------

PAGE = Path(settings.__file__).with_name("settings.html")


@pytest.fixture
def js():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the Settings page renderers")
    block = PAGE.read_text().split("/* ---- pure renderers ---- */", 1)[1].split("/* ---- end pure renderers ---- */", 1)[0]

    def run(expr):
        return subprocess.check_output([node, "-e", block + "\nprocess.stdout.write(String(" + expr + "));"], text=True)
    return run


def test_page_script_parses(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to syntax-check the Settings page script")
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(), re.S).group(1)
    path = tmp_path / "settings.js"
    path.write_text(script)
    subprocess.check_call([node, "--check", str(path)])


def test_monitor_and_settings_link_to_each_other():
    monitor_page = Path(monitor.__file__).with_name("monitor.html").read_text()
    assert '<a class="tab" href="/settings">Settings</a>' in monitor_page and "Settings · next" not in monitor_page
    assert '<a class="tab" href="/monitor">Monitor</a>' in PAGE.read_text()


def test_sections_route_from_the_hash(js):
    assert js('sectionFrom("#watchlist")') == "watchlist"
    assert js('sectionFrom("#nope")') == js('sectionFrom("")') == "receivers"
    side = js('renderSide("data")')
    assert side.count("<a ") == 5 and 'href="#data" class="on" aria-current="page"' in side and "Data &amp; export" in side


def test_receivers_page_never_shows_remote_id_ready_and_escapes(js):
    r = settings.receivers(readiness=lambda: {"radios": [dict(RADIOS["radios"][0], device="<b>bladeRF</b>")]},
                           detect=lambda: DETECTED)
    r["error"] = "Couldn't check attached radios: <boom>"
    html = js(f"renderReceivers({json.dumps(r)})")
    assert '<td class="crit">No receiver</td>' in html and '<td class="warn">Not set up</td>' in html
    assert '<td class="ok">Ready</td>' in html and html.count('class="ok">Ready') == 1
    assert "&lt;b&gt;bladeRF&lt;/b&gt;" in html and "&lt;boom&gt;" in html and "<b>bladeRF" not in html
    assert "MT7612U" in html and "USB ID only" in html and "Rescan hardware" in html
    failed = js('renderReceivers({receivers:[],other_hardware:[],hardware_error:"Couldn\'t list other capture hardware: x"})')
    assert "Couldn't list other capture hardware" in failed and "No other" not in failed


def test_monitoring_page_keeps_listening_disabled_and_threshold_provisional(js, home):
    html = js(f"renderMonitoring({json.dumps(settings.monitoring())})")
    assert html.count('type="checkbox" disabled') == 3 and " checked" not in html
    assert "isn't supported yet" in html and 'value="30"' in html and 'min="5"' in html and 'max="600"' in html
    assert "Provisional." in html and "hasn’t been tuned against live reception" in html
    broken = js('renderMonitoring({listen:[],error:"Couldn\'t read <file>. Using the default."})')
    assert 'class="fail"' in broken and "&lt;file&gt;" in broken


def test_watchlist_page_offers_three_types_marks_legacy_and_keeps_exact_values(js, home):
    targets.add("uas_id", " SN 1 ", exact=True)
    targets.add("imsi", '<i>"001"</i>')
    html = js(f"renderWatchlist({json.dumps(settings.watchlist())})")
    assert html.count("<option ") == 3 and ">Transmitter address</option>" in html and "imsi" not in html.split("</select>")[0]
    assert '<code class="val"> SN 1 </code>' in html and 'data-value=" SN 1 "' in html
    assert html.count("legacy type</span>") == 1 and "&lt;i&gt;&quot;001&quot;&lt;/i&gt;" in html and "<i>" not in html
    assert "isn’t a threat rating" in html and "aren’t authenticated" in html
    assert "The watchlist is empty" in js('renderWatchlist({types:[],entries:[]})')


def test_data_page_labels_sample_and_disables_empty_export(js, sample):
    s, _ = sample
    html = js(f"renderData({json.dumps(settings.data_export(s))})")
    assert '<span class="chip warn">Sample data</span>' in html and "2 observations · 0 diagnostic issues" in html
    assert "not live reception" in html and 'href="/api/export?format=csv" download' in html
    assert "in memory" in html and "No saved sessions yet" in html and "Anyone who can reach this server" in html
    sid = s.status()["session_id"]
    s.stop()
    saved = js(f"renderData({json.dumps(settings.data_export(s))})")
    assert "Session history" in saved and "Up to 30 readable records" in saved and "attempts to save" in saved
    assert f'href="/api/sessions/{sid}/export?format=csv" download' in saved
    assert f'data-act="delete-session" data-id="{sid}"' in saved and 'data-act="clear-sessions"' in saved
    assert saved.count('<span class="chip dim">Sample</span>') == 1 and "2 observations" in saved
    hostile = settings.data_export(s)
    hostile["history"]["sessions"][0].update(stop_reason="<i>x</i>", error="<b>boom</b>")
    hostile["history"]["unreadable"] = ["<script>.json"]
    hostile["history_error"] = "<u>disk full</u>"
    out = js(f"renderData({json.dumps(hostile)})")
    assert "<i>" not in out and "<b>" not in out and "<script>" not in out and "<u>" not in out
    assert "&lt;b&gt;boom&lt;/b&gt;" in out and "couldn’t be read" in out and "disk full" in out
    idle = js(f"renderData({json.dumps(settings.data_export(monitor.MonitorSession()))})")
    assert '<span class="chip dim">Idle</span>' in idle and "/api/export" not in idle
    assert idle.count("disabled>Export") == 2 and "Nothing to export yet" in idle


def test_about_page_shows_unsupported_live_remote_id_and_escapes_logs(js, home):
    a = settings.about(session=monitor.MonitorSession(), started_at=None)
    html = js(f"renderAbout({json.dumps(a)}, {json.dumps([{'t': '12:00:00', 'level': 'INFO', 'msg': '<script>x</script>'}])})")
    assert "Not supported yet." in html and "isn't supported yet" in html and ">Idle<" in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html and "<script>" not in html and "0 entries" in html
    assert js("uptime(3725)") == "1 h 2 min" and js("uptime(null)") == "—"
    assert "No activity yet." in js("renderLogs([])")


# ---- radio control -----------------------------------------------------------------------

FREE = {"held": False, "owner": None, "held_sec": None, "idle_sec": None,
        "center_hz": None, "span_hz": None, "lease_timeout_s": 8.0}


def test_radio_control_snapshot_states():
    held = settings.radio_control(lambda: dict(FREE, held=True, owner="spec-abc", held_sec=12.5, idle_sec=0.4,
                                                center_hz=2442000000, span_hz=60000000))
    assert (held["held"], held["owner"], held["error"]) == (True, "spec-abc", None) and held["checked_at"]
    free = settings.radio_control(lambda: FREE)
    assert (free["held"], free["error"]) == (False, None)

    def boom():
        raise RuntimeError("lock gone")
    failed = settings.radio_control(boom)
    assert "lock gone" in failed["error"] and "held" not in failed        # a failure never reads as free


def test_receivers_include_the_radio_control_snapshot():
    r = settings.receivers(readiness=lambda: RADIOS, detect=lambda: DETECTED, control=lambda: FREE)
    assert (r["radio_control"]["held"], r["radio_control"]["error"]) == (False, None)


def test_radio_control_renders_held_free_failure_and_missing(js):
    held = js("renderRadioControl({held:true,owner:'spec-<x>',held_sec:12.5,center_hz:2442000000,"
              "span_hz:60000000,lease_timeout_s:8,checked_at:1})")
    assert '<span class="warn">Held</span>' in held and "spec-&lt;x&gt;" in held
    assert "2442.000 MHz ± 30.000 MHz" in held and "not a per-receiver status" in held
    free = js("renderRadioControl({held:false,lease_timeout_s:8,checked_at:1})")
    assert "Free." in free and "Held" not in free and "Rescan hardware refreshes it" in free
    failed = js("renderRadioControl({error:'Couldn\\'t read radio control: <boom>'})")
    assert 'class="fail"' in failed and "&lt;boom&gt;" in failed and "Free" not in failed
    assert "wasn’t included" in js("renderRadioControl(null)")
    page = js("renderReceivers({receivers:[],other_hardware:[],radio_control:{held:false,lease_timeout_s:8}})")
    assert "Radio control" in page and "Free." in page


# ---- the second monitoring threshold: when an observation is listed as older ---------------------

def test_the_age_threshold_has_a_default_is_validated_and_never_sits_below_stale(home):
    m = settings.monitoring()
    assert (m["lapsed_after_s"], m["lapsed_default_s"], m["lapsed_max_s"]) == (300.0, 300.0, 86400.0)
    assert settings.set_monitoring(lapsed=600)["lapsed_after_s"] == 600.0 and settings.lapsed_after_s() == 600.0
    for bad in (True, "600", None, float("nan"), 10**400, -1, 86401):
        with pytest.raises(ValueError):
            settings.set_monitoring(lapsed=bad)
    with pytest.raises(ValueError, match="stale"):
        settings.set_monitoring(lapsed=20)                               # below the 30 s stale threshold
    settings.set_monitoring(lapsed=60)
    settings.set_stale_after(120)                                        # raising stale above it is allowed…
    assert settings.lapsed_after_s() == 120.0                            # …the effective value is the greater of the two…
    settings.set_stale_after(30)
    assert settings.lapsed_after_s() == 60.0                             # …and lowering stale restores the saved value
    note = settings.monitoring()["lapsed_note"]
    assert "greater of" in note and "never removed" in note


def test_a_rejected_request_with_both_thresholds_changes_neither(home, server):
    """OL-3. The reviewer's probe: stale 120 is fine on its own, lapsed 5 is not; nothing may be saved."""
    base, _ = server
    settings.set_monitoring(lapsed=60)
    code, _, body = _call(base + "/api/settings/monitoring", {"stale_after_s": 120, "lapsed_after_s": 5})
    assert code == 400 and "No settings were changed" in json.loads(body)["error"]
    assert (settings.stale_after_s(), settings.lapsed_after_s()) == (30.0, 60.0)
    code, _, body = _call(base + "/api/settings/monitoring", {"stale_after_s": 120, "lapsed_after_s": 100})
    assert code == 400 and (settings.stale_after_s(), settings.lapsed_after_s()) == (30.0, 60.0)   # lapsed below the new stale
    code, _, body = _call(base + "/api/settings/monitoring", {"stale_after_s": 40, "lapsed_after_s": 900})
    assert code == 200 and (settings.stale_after_s(), settings.lapsed_after_s()) == (40.0, 900.0)


def test_the_monitoring_route_saves_either_threshold(server):
    base, _ = server
    code, _, body = _call(base + "/api/settings/monitoring", {"lapsed_after_s": 900})
    assert code == 200 and json.loads(body)["lapsed_after_s"] == 900.0
    assert _call(base + "/api/settings/monitoring", {"lapsed_after_s": 5})[0] == 400
    assert _call(base + "/api/settings/monitoring", {})[0] == 400
    assert json.loads(_call(base + "/api/session")[2])["lapsed_after_s"] == 900.0


def test_the_monitoring_page_offers_both_thresholds_in_plain_terms(js, home):
    html = js(f"renderMonitoring({json.dumps(settings.monitoring())})")
    assert 'data-form="stale"' in html and 'data-form="lapsed"' in html and "Older observations" in html
    assert "never removed" in html and "do not show live reception" in html
    for banned in ("No longer heard", "heard again"):
        assert banned not in html
