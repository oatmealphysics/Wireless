"""Monitor session, observation feed, receivers, routes and page renderers.

The only source is a replayed bundled sample: these tests also check that it stays
labeled as sample data, never claims live Remote ID reception, and that start/stop
can't leave more than one replay worker feeding a session."""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from scapy.layers.dot11 import Dot11, Dot11Beacon, Dot11Elt, RadioTap  # type: ignore

from waveraider import monitor, targets

DRONE_A, DRONE_B = "60:60:1f:00:00:01", "60:60:1f:00:00:02"
MAC = "60:60:1f:00:00:09"
LAT, LON = 368529000, -759780000                              # 36.8529, -75.9780


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# ---- frame builders (ASTM F3411 layout, spec framing, wire round-trip) ------

def _msg(mtype, body=b""):
    m = bytearray(25)
    m[0] = (mtype << 4) | 2
    m[1:1 + len(body)] = body
    return bytes(m)


def _basic(text=b"SN-0001", id_type=1):
    return _msg(0, bytes([(id_type << 4) | 2]) + text.ljust(20, b"\x00")[:20])


def _location(lat_raw, lon_raw, alt_raw=2474):
    body = bytearray(24)
    body[4:12] = struct.pack("<ii", lat_raw, lon_raw)
    body[14:16] = struct.pack("<H", alt_raw)
    body[20:22] = struct.pack("<H", 0xFFFF)
    return _msg(1, bytes(body))


def _system(lat_raw, lon_raw):
    return _msg(4, b"\x01" + struct.pack("<ii", lat_raw, lon_raw))


def _pack(*messages):
    return bytes([0xF2, 25, len(messages)]) + b"".join(messages)


def _beacon(ie_body, mac=MAC):
    frame = (RadioTap() / Dot11(type=0, subtype=8, addr1="ff:ff:ff:ff:ff:ff", addr2=mac, addr3=mac)
             / Dot11Beacon() / Dot11Elt(ID=221, info=ie_body))
    return frame.__class__(bytes(frame))


def _remote_id(payload, mac=MAC):
    return _beacon(b"\xfa\x0b\xbc\x0d\x07" + payload, mac)


# ---- fixtures ----------------------------------------------------------------

@pytest.fixture
def watch(tmp_path, monkeypatch):
    """Point the watchlist and monitoring profile at throwaway files (never the user's real ones)."""
    monkeypatch.setenv("WAVERAIDER_TARGETS", str(tmp_path / "targets.json"))
    monkeypatch.setenv("WAVERAIDER_MONITORING", str(tmp_path / "monitoring.json"))
    return targets


@pytest.fixture
def session(watch):
    clock = Clock()
    s = monitor.MonitorSession(clock=clock)
    s.start("sample", threaded=False)
    yield s, clock
    s.stop()


def _by_transmitter(s):
    return {o["transmitter"]: o for o in s.observations()["observations"]}


def _workers(s):
    return [t for t in threading.enumerate() if t.name.startswith(f"monitor-replay-{id(s)}-") and t.is_alive()]


# ---- session and observations ---------------------------------------------

def test_idle_session_is_empty_and_covers_nothing(watch):
    st = monitor.MonitorSession().status()
    assert (st["state"], st["source"], st["source_label"]) == ("idle", None, None)
    cov = st["coverage"]
    assert (cov["active"], cov["covered"], cov["retained"]) == (False, [], [])
    assert "live Remote ID reception" in cov["not_covered"]
    assert monitor.MonitorSession().observations()["observations"] == []


def test_sample_replay_is_labeled_and_decodes_the_bundled_sample(session):
    s, clock = session
    assert s.replay_pass() == 18
    st = s.status()
    assert (st["state"], st["source"], st["source_label"], st["frames"]) == ("running", "sample", "Sample data", 18)

    obs = _by_transmitter(s)
    assert set(obs) == {DRONE_A, DRONE_B}
    a = obs[DRONE_A]
    assert (a["evidence"], a["sample"], a["transport"], a["transport_label"]) == \
        ("remote_id", True, "wifi_beacon", "Wi-Fi beacon")
    assert (a["identity"], a["ua_type_label"], a["operator_id"]) == ("DRONE-SN-001A", "Multirotor", "OP-FAA-12345")
    assert a["position"] == pytest.approx([36.8529, -75.978]) and a["alt_m"] == 120.0
    assert a["operator"]["type"] == "live GNSS"
    assert a["basic_ids"][0]["id_type_label"].startswith("Serial") and a["basic_ids"][0]["raw"]
    assert obs[DRONE_B]["operator"] is None
    assert a["first_seen"] == a["last_seen"] == clock.t      # received time = replay time
    assert a["messages"] == 10 and a["stale"] is False
    assert s.diagnostics() == {"total": 0, "rejected": 0, "partial": 0, "reasons": []}


def test_coverage_comes_from_the_selected_sample_and_distinguishes_stopped(session):
    s, _ = session
    s.replay_pass()
    transports = {o["transport"] for o in s.observations()["observations"]}
    running = s.status()["coverage"]
    assert transports == {"wifi_beacon"}
    assert running["active"] is True and running["covered"] == ["Wi-Fi beacon Remote ID (recorded sample)"]
    assert "Bluetooth legacy Remote ID" in running["not_covered"] and not running["retained"]
    assert "Remote ID in recorded Bluetooth legacy advertisements" in running["decoder_support"]

    stopped = s.stop()["coverage"]
    assert (stopped["active"], stopped["covered"]) == (False, [])
    assert stopped["retained"] == ["Wi-Fi beacon Remote ID (recorded sample)"]


def test_staleness_follows_elapsed_time_and_recovers_when_heard_again(session):
    s, clock = session
    s.replay_pass()
    clock.t += monitor.STALE_AFTER_S + 1
    assert all(o["stale"] for o in s.observations()["observations"])
    s.replay_pass()
    assert not any(o["stale"] for o in s.observations()["observations"])


def test_since_filters_by_last_heard(session):
    s, clock = session
    s.replay_pass()
    assert s.observations(since=clock.t)["observations"] == []
    assert len(s.observations(since=clock.t - 1)["observations"]) == 2


def test_watchlist_matches_are_reported_per_observation(session, watch):
    watch.add("uas_id", "DRONE-SN-001A")
    s, _ = session
    s.replay_pass()
    obs = _by_transmitter(s)
    assert obs[DRONE_A]["watchlist"] == ["DRONE-SN-001A"] and obs[DRONE_B]["watchlist"] == []


def test_stop_keeps_rows_for_review_and_restart_starts_clean(session):
    s, clock = session
    s.replay_pass()
    clock.t += 5
    st = s.stop()
    assert (st["state"], st["stopped_at"]) == ("stopped", clock.t)
    assert len(s.observations()["observations"]) == 2
    s.start("sample", threaded=False)
    assert s.status()["state"] == "running" and s.observations()["observations"] == []


@pytest.mark.parametrize("kwargs", [{"source": "live"}, {"source": "sample", "sample": "../../etc/passwd"}])
def test_only_bundled_samples_can_be_replayed(watch, kwargs):
    s = monitor.MonitorSession()
    with pytest.raises(ValueError):
        s.start(threaded=False, **kwargs)
    assert s.status()["state"] == "idle"


def test_replay_requires_a_running_session(watch):
    with pytest.raises(RuntimeError):
        monitor.MonitorSession().replay_pass()


def test_threaded_replay_runs_and_stops_cleanly(watch):
    s = monitor.MonitorSession(interval_s=0.001, pass_pause_s=0.01)
    s.start("sample")
    deadline = time.time() + 5
    while time.time() < deadline and len(s.observations()["observations"]) < 2:
        time.sleep(0.02)
    st = s.stop()
    assert st["state"] == "stopped" and st["error"] is None and st["passes"] >= 1
    assert len(s.observations()["observations"]) == 2 and s._run is None and not _workers(s)


# ---- lifecycle concurrency (M1) -----------------------------------------------

def _block_first_running_reset(s, monkeypatch):
    """Pause the first start() right after it resets the session, mid-transition."""
    entered, release = threading.Event(), threading.Event()
    real_reset = s._reset

    def slow_reset(state, source):
        real_reset(state, source)
        if state == "running" and not entered.is_set():
            entered.set()
            release.wait(timeout=5)
    monkeypatch.setattr(s, "_reset", slow_reset)
    return entered, release


def test_concurrent_starts_leave_exactly_one_replay_worker(watch, monkeypatch):
    s = monitor.MonitorSession(interval_s=0.005, pass_pause_s=0.01)
    entered, release = _block_first_running_reset(s, monkeypatch)
    second_done = threading.Event()
    first = threading.Thread(target=s.start)
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=lambda: (s.start(), second_done.set()))
    second.start()
    assert not second_done.wait(0.3)             # waits for the first transition to finish
    release.set()
    first.join(5)
    second.join(5)
    assert second_done.is_set()
    live = _workers(s)
    assert len(live) == 1 and live[0].name.endswith("-2") and s._run.gen == 2
    s.stop()
    assert not _workers(s)


def test_stop_during_start_waits_and_leaves_no_worker(watch, monkeypatch):
    s = monitor.MonitorSession(interval_s=0.005, pass_pause_s=0.01)
    entered, release = _block_first_running_reset(s, monkeypatch)
    stopped = threading.Event()
    starter = threading.Thread(target=s.start)
    starter.start()
    assert entered.wait(5)
    stopper = threading.Thread(target=lambda: (s.stop(), stopped.set()))
    stopper.start()
    assert not stopped.wait(0.3)
    release.set()
    starter.join(5)
    stopper.join(5)
    assert stopped.is_set() and s.status()["state"] == "stopped" and s._run is None
    assert not _workers(s)


def test_slow_old_worker_cannot_feed_a_restarted_session(watch, monkeypatch):
    s = monitor.MonitorSession(interval_s=0.001, pass_pause_s=0.01, join_timeout_s=0.05)
    in_feed, release = threading.Event(), threading.Event()
    real_feed = s._feed

    def slow_feed(pkt, run):
        if run is not None and run.gen == 1 and not in_feed.is_set():
            in_feed.set()
            release.wait(timeout=5)
        return real_feed(pkt, run)
    monkeypatch.setattr(s, "_feed", slow_feed)

    s.start()                                    # run 1, threaded
    assert in_feed.wait(5)                       # its worker is stuck before its first write
    old = _workers(s)[0]
    s.stop()                                     # join times out; the worker is still alive
    assert old.is_alive()
    s.start(threaded=False)                      # run 2
    release.set()
    old.join(5)
    assert not old.is_alive()
    st = s.status()
    assert (st["state"], st["frames"], st["passes"], st["error"]) == ("running", 0, 0, None)
    assert s.observations()["observations"] == []
    s.stop()


# ---- diagnostics (M3) -----------------------------------------------------------

def test_rejected_input_is_counted_never_listed(session):
    s, _ = session
    s.feed(_beacon(b"\xfa\x0b\xbc\x0d"))         # no message counter
    assert s.observations()["observations"] == []
    assert s.diagnostics() == {"total": 1, "rejected": 1, "partial": 0, "reasons": [
        {"reason": "truncated Remote ID envelope", "count": 1, "kind": "rejected"}]}


def test_partial_report_keeps_valid_fields_and_counts_each_invalid_field(session):
    s, _ = session
    s.feed(_remote_id(_pack(_location(910000000, LON), _system(LAT, 1810000000))))
    obs = s.observations()["observations"]
    assert len(obs) == 1 and obs[0]["alt_m"] == 237.0 and obs[0]["position"] is None and obs[0]["stale"] is False
    d = s.diagnostics()
    assert (d["total"], d["rejected"], d["partial"]) == (2, 0, 2)
    assert {r["reason"]: r["kind"] for r in d["reasons"]} == \
        {"invalid drone position": "partial", "invalid operator position": "partial"}
    assert monitor.diagnostic_kind("unsupported message skipped") == "partial"
    assert monitor.diagnostic_kind("unsupported protocol version") == "rejected"
    assert monitor.diagnostic_kind("invalid message pack content") == "rejected"


def test_whole_pack_rejection_is_not_reported_as_partial(session, js):
    s, _ = session
    s.feed(_remote_id(_pack(_location(LAT, LON), _location(LAT, LON))))   # two Location messages: not allowed
    assert s.observations()["observations"] == []
    d = s.diagnostics()
    assert d == {"total": 1, "rejected": 1, "partial": 0, "reasons": [
        {"reason": "invalid message pack content", "count": 1, "kind": "rejected"}]}
    html = js(f"renderDiagnostics({json.dumps(d)})")
    assert "Rejected inputs" in html and "Fields left out" not in html and "report was kept" not in html


def test_every_partial_reason_the_decoder_emits_keeps_a_report(session):
    s, _ = session
    bad_time = bytearray(_location(LAT, LON))
    bad_time[21:23] = struct.pack("<H", 36001)                          # beyond an hour, not the unknown marker
    future = bytearray(_basic())
    future[0] = 0x03                                                    # Basic ID, unsupported protocol version 3
    s.feed(_remote_id(_pack(_location(910000000, LON), _system(LAT, 1810000000)), mac="60:60:1f:00:00:0a"))
    s.feed(_remote_id(_pack(bytes(bad_time)), mac="60:60:1f:00:00:0b"))
    s.feed(_remote_id(_pack(bytes(future), _location(LAT, LON)), mac="60:60:1f:00:00:0c"))
    reasons = {r["reason"]: r["kind"] for r in s.diagnostics()["reasons"]}
    assert reasons == {reason: "partial" for reason in monitor.PARTIAL_REASONS}
    assert len(s.observations()["observations"]) == 3


# ---- watchlist action (M2) ------------------------------------------------------

def test_watch_keeps_a_decoded_identity_exactly_and_matches_it(session, watch):
    s, _ = session
    s.feed(_remote_id(_pack(_basic(b" SN-0001 "))))
    key = s.observations()["observations"][0]["key"]
    r = s.watch(key)
    assert r == {"type": "uas_id", "value": " SN-0001 ", "matches": [" SN-0001 "], "matched": True}
    assert [t["value"] for t in watch.load()] == [" SN-0001 "]


def test_watch_falls_back_to_the_transmitter_when_no_identity(session, watch):
    s, _ = session
    s.feed(_remote_id(_pack(_location(LAT, LON))))
    key = s.observations()["observations"][0]["key"]
    r = s.watch(key)
    assert (r["type"], r["value"], r["matched"]) == ("mac", MAC, True)
    with pytest.raises(KeyError):
        s.watch("wifi_beacon:00:00:00:00:00:00")


def test_typed_watchlist_values_are_still_trimmed(watch):
    watch.add("uas_id", "  typed-in  ")
    assert [t["value"] for t in watch.load()] == ["typed-in"]
    watch.add("uas_id", "   ", exact=True)
    assert [t["value"] for t in watch.load()] == ["typed-in"]


def test_exports_are_labeled_as_sample_and_explain_received_times(session):
    s, _ = session
    s.replay_pass()
    body, ctype, name = s.export("json")
    doc = json.loads(body)
    assert ctype == "application/json" and re.match(r"waveraider-sample-session-\d{8}-\d{6}\.json$", name)
    assert doc["source"] == "sample" and "not live reception" in doc["note"] and len(doc["observations"]) == 2
    assert "not the original capture times" in doc["note"]

    body, ctype, name = s.export("csv")
    assert ctype.startswith("text/csv") and name.endswith(".csv") and "-sample-" in name
    comment = body.splitlines()[1]
    assert "not live reception" in comment and "not the original capture times" in comment
    assert body.count("\nsample,") == 2 and "DRONE-SN-001A" in body

    key = doc["observations"][0]["key"]
    body, _, name = s.export("json", key=key)
    assert [o["key"] for o in json.loads(body)["observations"]] == [key] and "-observation-" in name
    with pytest.raises(KeyError):
        s.export("json", key="nope")
    with pytest.raises(ValueError):
        s.export("xml")


# ---- receivers --------------------------------------------------------------

def test_receivers_never_report_live_remote_id_as_ready():
    radios = {"radios": [
        {"device": "bladeRF 2.0 micro", "usb_id": "2cf0:5250", "live_capable": True, "backend_label": "native libbladeRF"},
        {"device": "RTL-SDR (RTL2838)", "usb_id": "0bda:2838", "live_capable": False, "install_hint": "install rtl-sdr"},
    ]}
    r = monitor.receivers(readiness=lambda: radios)
    by = {x["id"]: x for x in r["receivers"]}
    assert by["remote-id-wifi"]["state"] == "unavailable" and by["remote-id-bluetooth"]["state"] == "not_set_up"
    assert (by["radio:2cf0:5250"]["state"], by["radio:2cf0:5250"]["role"]) == ("ready", "Inspect RF")
    assert by["radio:0bda:2838"]["state"] == "not_ready" and "install rtl-sdr" in by["radio:0bda:2838"]["note"]
    assert r["error"] is None


def test_receiver_check_failure_is_reported_not_hidden():
    def boom():
        raise RuntimeError("usb scan failed")
    r = monitor.receivers(readiness=boom)
    assert "usb scan failed" in r["error"]
    assert [x["id"] for x in r["receivers"]] == ["remote-id-wifi", "remote-id-bluetooth"]


# ---- HTTP routes --------------------------------------------------------------

@pytest.fixture
def server(monkeypatch, watch):
    from waveraider import web
    s = monitor.MonitorSession(interval_s=0.001, pass_pause_s=0.01)
    monkeypatch.setattr(monitor, "SESSION", s)
    monkeypatch.setattr(monitor, "receivers", lambda force=False: {"checked_at": 0, "receivers": [], "error": None})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", s
    s.stop()
    httpd.shutdown()
    httpd.server_close()


def _call(url, data=None, headers=None, raw=None):
    body = raw if raw is not None else (None if data is None else json.dumps(data).encode())
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_monitor_page_and_session_routes(server):
    base, s = server
    code, headers, body = _call(base + "/monitor")
    assert code == 200 and headers["Content-Type"].startswith("text/html") and b"<title>WaveRaider Monitor</title>" in body

    code, _, body = _call(base + "/api/session")
    assert code == 200 and json.loads(body)["state"] == "idle"
    code, _, body = _call(base + "/api/session", {"action": "start", "source": "sample"})
    assert code == 200 and json.loads(body)["source_label"] == "Sample data"

    deadline = time.time() + 5
    while time.time() < deadline and len(s.observations()["observations"]) < 2:
        time.sleep(0.02)
    code, _, body = _call(base + "/api/observations")
    obs = json.loads(body)["observations"]
    assert code == 200 and len(obs) == 2 and all(o["sample"] for o in obs)

    code, _, body = _call(base + "/api/observations/" + urllib.request.quote(obs[0]["key"], safe=""))
    assert code == 200 and json.loads(body)["key"] == obs[0]["key"]
    assert _call(base + "/api/observations/wifi_beacon%3A00")[0] == 404
    assert json.loads(_call(base + "/api/diagnostics")[2])["total"] == 0
    assert json.loads(_call(base + "/api/receivers")[2])["receivers"] == []

    code, headers, body = _call(base + "/api/export?format=csv")
    assert code == 200 and "attachment" in headers["Content-Disposition"] and b"not live reception" in body

    code, _, body = _call(base + "/api/session", {"action": "stop"})
    assert code == 200 and json.loads(body)["state"] == "stopped"


def test_watch_route_saves_the_exact_decoded_identity(server, watch):
    base, s = server
    s.start("sample", threaded=False)
    s.feed(_remote_id(_pack(_basic(b" SN-0001 "))))
    key = s.observations()["observations"][0]["key"]
    code, _, body = _call(base + "/api/monitor/watch", {"key": key})
    r = json.loads(body)
    assert code == 200 and (r["value"], r["matched"]) == (" SN-0001 ", True)
    assert [t["value"] for t in watch.load()] == [" SN-0001 "]
    assert json.loads(_call(base + "/api/observations")[2])["observations"][0]["watchlist"] == [" SN-0001 "]
    assert _call(base + "/api/monitor/watch", {"key": "wifi_beacon:00"})[0] == 404


@pytest.mark.parametrize("path, data, message", [
    ("/api/session", {"action": "start", "source": "live"}, "isn't supported yet"),
    ("/api/session", {"action": "pause"}, "action must be"),
    ("/api/observations?since=soon", None, "since must be a number"),
    ("/api/export?format=xml", None, "format must be"),
])
def test_bad_requests_explain_what_to_fix(server, path, data, message):
    base, _ = server
    code, _, body = _call(base + path, data)
    assert code == 400 and message in json.loads(body)["error"]


# ---- page renderers (Node.js) ---------------------------------------------------

PAGE = Path(monitor.__file__).with_name("monitor.html")


@pytest.fixture
def js():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the Monitor page renderers")
    block = PAGE.read_text().split("/* ---- pure renderers ---- */", 1)[1].split("/* ---- end pure renderers ---- */", 1)[0]

    def run(expr):
        return subprocess.check_output([node, "-e", block + "\nprocess.stdout.write(String(" + expr + "));"], text=True)
    return run


def test_page_script_parses(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to syntax-check the Monitor page script")
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(), re.S).group(1)
    path = tmp_path / "monitor.js"
    path.write_text(script)
    subprocess.check_call([node, "--check", str(path)])


def test_node_health_renderers_escape_reports_and_label_simulation(js):
    data = {'state': 'available', 'nodes': [{'node_id': '<img onerror=evil>',
        'simulated': True, 'link_state': 'stale', 'health_state': 'stale',
        'seconds_since_receipt': 30, 'heartbeat_age_s': 40, 'receiver_state': 'unknown',
        'receiver_detail': '<script>evil</script>', 'position': {'state': 'stale', 'age_s': 50},
        'outbox_dropped_total': 2, 'sequence_gaps': 3}], 'recordings': [
        {'node_id': 'a', 'simulated': True, 'operator_label': '<script>evil</script>',
         'sample_count': 1600, 'overruns': 0, 'discontinuities': 0, 'capture_id': 'capture',
         'created_at': '2026-09-25'}]}
    html = js(f'renderNodes({json.dumps(data)})')
    assert '<img' not in html and '<script>' not in html
    assert 'SIMULATED' in html and 'Receiver status unknown' in html and 'GPS stale' in html
    html = js(f'renderNodeRecordings({json.dumps(data)})')
    assert '<script>' not in html and 'SIMULATED' in html and 'IQ remains on sensor' in html


def test_node_endpoint_is_opt_in_and_read_only(server, monkeypatch, tmp_path):
    base, _ = server
    monkeypatch.delenv('WAVERAIDER_COORDINATOR', raising=False)
    status, headers, body = _call(base + '/api/nodes')
    assert status == 200 and headers['Cache-Control'] == 'no-store'
    assert json.loads(body)['state'] == 'not_configured'
    monkeypatch.setenv('WAVERAIDER_COORDINATOR', str(tmp_path / 'missing'))
    assert json.loads(_call(base + '/api/nodes')[2])['state'] == 'unavailable'
    assert not (tmp_path / 'missing').exists()
    assert _call(base + '/api/nodes', {'action': 'record'})[0] == 404


ROW = {"key": "wifi_beacon:84:cc:a8:60:43:24", "evidence": "remote_id", "sample": True,
       "transmitter": "84:cc:a8:60:43:24", "transport_label": "Wi-Fi beacon", "identity": None,
       "ua_type_label": None, "position": [45.5470818, -122.9668346], "last_seen": 100.0,
       "stale": True, "watchlist": []}


def test_rows_show_unreported_identity_staleness_and_escape_text(js):
    other = dict(ROW, key="k2", identity='<b>SN "1"</b>', ua_type_label="Multirotor",
                 stale=False, last_seen=130.0, watchlist=['<b>SN "1"</b>'])
    html = js(f"renderRows({json.dumps([ROW, other])}, 131, {json.dumps(ROW['key'])})")
    assert 'class="sel stale"' in html and "Identity not reported" in html and "31 s ago · stale" in html
    assert "45.54708, −122.96683" in html and ">sample<" in html
    assert "&lt;b&gt;SN &quot;1&quot;&lt;/b&gt;" in html and "<b>SN" not in html and "Matches watchlist" in html


def test_detail_keeps_caveats_and_unknowns_visible(js):
    detail = dict(ROW, basic_ids=[{"id_type_label": "None", "text": None, "raw": "4d464731413031323334"}],
                  alt_m=None, operator={"lat": 45.5443876, "lon": -122.9726866, "type": "takeoff"},
                  operator_id="GBR-OP-123ABCD", first_seen=90.0, claimed_time_s=None, messages=21,
                  protocol_version=0)
    html = js(f"renderDetail({json.dumps(detail)}, 131)")
    assert "Identity not reported" in html and "no usable text" in html and "(takeoff location)" in html
    assert "not authenticated" in html and "not the original capture times" in html
    assert html.count("Not reported") >= 3                      # UA type, altitude, claimed time
    assert "key=wifi_beacon%3A84%3Acc%3Aa8%3A60%3A43%3A24" in html


def test_session_view_never_presents_sample_as_live(js):
    running = json.loads(js('JSON.stringify(sessionView({state:"running",source:"sample",started_at:100,'
                            'sample:{description:"Bundled Remote ID sample"}},3700))'))
    assert (running["chip"], running["action"], running["clock"]) == ("Sample data", "stop", "Session 01:00:00")
    assert "not live reception" in running["banner"] and running["chipClass"] == "warn"
    idle = json.loads(js('JSON.stringify(sessionView({state:"idle",source:null},0))'))
    assert (idle["chip"], idle["button"], idle["banner"]) == ("Idle", "Start sample session", None)
    failed = json.loads(js('JSON.stringify(sessionView({state:"stopped",source:"sample",stopped_at:0,error:"OSError: gone"},10))'))
    assert failed["bannerCrit"] is True and "stopped unexpectedly" in failed["banner"]


def test_coverage_line_separates_active_retained_and_idle(js):
    active = js('renderCoverage({active:true,covered:["Wi-Fi beacon Remote ID (recorded sample)"],retained:[],'
                'not_covered:["Bluetooth legacy Remote ID","live Remote ID reception"]})')
    assert "Covered now:" in active and "Bluetooth legacy Remote ID" in active.split("Not covered:")[1]
    stopped = js('renderCoverage({active:false,covered:[],retained:["Wi-Fi beacon Remote ID (recorded sample)"],not_covered:[]})')
    assert "Not monitoring." in stopped and "Rows retained from: Wi-Fi beacon" in stopped and "Covered now" not in stopped
    idle = js('renderCoverage({active:false,covered:[],retained:[],not_covered:["live Remote ID reception"]})')
    assert "Not monitoring." in idle and "retained" not in idle


def test_diagnostics_say_issues_and_explain_partial_reports(js):
    d = {"total": 3, "rejected": 1, "partial": 2, "reasons": [
        {"reason": "truncated Remote ID envelope", "count": 1, "kind": "rejected"},
        {"reason": "invalid drone position", "count": 1, "kind": "partial"},
        {"reason": "invalid operator position", "count": 1, "kind": "partial"}]}
    assert js(f"diagLabel({json.dumps(d)}, false)") == "3 diagnostic issues ›"
    assert js('diagLabel({total:1,reasons:[]}, true)') == "1 diagnostic issue ▾"
    html = js(f"renderDiagnostics({json.dumps(d)})")
    assert "Rejected inputs" in html and "never create or refresh" in html
    assert "Fields left out" in html and "rest of that report was kept" in html
    assert "not decoded" not in html


def test_watch_toast_shows_the_exact_value_and_admits_a_mismatch(js):
    ok = json.loads(js('JSON.stringify(watchToast({type:"uas_id",value:" SN-0001 ",matched:true}))'))
    assert ok == {"msg": 'Added " SN-0001 " to the watchlist.', "error": False}
    bad = json.loads(js('JSON.stringify(watchToast({type:"uas_id",value:"X",matched:false}))'))
    assert bad["error"] is True and "doesn’t match" in bad["msg"]


def test_receiver_strip_states_and_failures(js):
    data = {"receivers": [
        {"name": "Remote ID · Wi-Fi beacons", "role": "Remote ID", "state": "unavailable", "state_label": "No receiver",
         "note": "Live Wi-Fi Remote ID capture isn't supported yet."},
        {"name": "bladeRF 2.0 micro", "role": "Inspect RF", "state": "ready", "state_label": "Ready", "note": "<native>"},
    ], "error": "Couldn't check attached radios: boom"}
    html = js(f"renderReceivers({json.dumps(data)})")
    assert 'class="cell s-crit"' in html and "No receiver</span>" in html and "No receiver · Remote ID" not in html
    assert "Ready · Inspect RF" in html and "&lt;native&gt;" in html
    assert "Check failed" in html and "boom" in html


def test_empty_states_never_say_no_drones(js):
    idle = js('emptyState({state:"idle"})')
    assert "Not monitoring" in idle and "isn’t supported yet" in idle
    for state in ("idle", "running", "stopped"):
        html = js(f'emptyState({{state:"{state}"}})')
        assert "no drones" not in html.lower()


def test_observations_have_no_collection_filter_controls():
    html = PAGE.read_text()
    assert 'aria-label="Evidence filter"' not in html
    assert 'data-f=' not in html
    assert 'state.filter' not in html


# ---- session record and history ----------------------------------------------

def _history():
    from waveraider import sessions
    return sessions


def test_a_running_session_has_an_id_and_an_idle_one_does_not(session):
    s, _ = session
    sid = s.status()["session_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", sid)
    assert monitor.MonitorSession().status()["session_id"] is None
    s.start("sample", threaded=False)
    assert s.status()["session_id"] != sid                              # a new start is a new session


def test_stopping_a_session_saves_one_record_with_its_provenance(session):
    import hashlib
    import waveraider
    s, clock = session
    s.replay_pass()
    sid = s.status()["session_id"]
    clock.t += 7
    s.stop()
    s.stop()                                                            # a second stop saves nothing more
    listing = _history().listing()["sessions"]
    assert [x["session_id"] for x in listing] == [sid]
    r = _history().load(sid)
    assert (r["schema"], r["source"], r["source_label"], r["stop_reason"]) == (1, "sample", "Sample data", "stopped")
    assert r["app_version"] == waveraider.__version__
    assert r["sample"]["name"] == "remote-id-wifi" and r["sample"]["file"] == "uas.pcap"
    assert r["sample"]["sha256"] == hashlib.sha256((monitor.SAMPLES / "uas.pcap").read_bytes()).hexdigest()
    assert r["stopped_at"] - r["started_at"] == 7 and r["stopped_at_utc"].endswith("Z") and r["as_of"] == r["stopped_at"]
    assert r["frames"] == 18 and r["passes"] == 1 and r["stale_after_s"] == s.status()["stale_after_s"]
    assert "not live reception" in r["note"] and r["stale_after_provisional"] is True
    assert {o["transmitter"] for o in r["observations"]} == {DRONE_A, DRONE_B} and all(o["sample"] for o in r["observations"])
    assert r["diagnostics"]["total"] == 0 and r["coverage"]["retained"] and not r["coverage"]["active"]


def test_a_session_replaced_by_a_new_start_is_saved_as_replaced(session):
    s, _ = session
    s.replay_pass()
    first = s.status()["session_id"]
    s.start("sample", threaded=False)
    assert _history().load(first)["stop_reason"] == "replaced"
    assert _history().load(s.status()["session_id"]) is None             # the running one is not history yet


def test_a_replay_failure_is_saved_with_its_error(watch, monkeypatch):
    def boom(_path):
        raise OSError("sample unreadable")
        yield
    monkeypatch.setattr(monitor, "iter_frames", boom)
    s = monitor.MonitorSession(interval_s=0.001, pass_pause_s=0.01)
    sid = s.start("sample")["session_id"]
    deadline = time.time() + 5
    while time.time() < deadline and s.status()["state"] != "stopped":
        time.sleep(0.01)
    deadline = time.time() + 5
    while time.time() < deadline and _history().load(sid) is None:
        time.sleep(0.01)
    r = _history().load(sid)
    assert r["stop_reason"] == "error" and "sample unreadable" in r["error"]


def test_a_history_that_cannot_be_written_does_not_break_stop_and_is_reported(session, monkeypatch):
    s, _ = session
    s.replay_pass()

    def full(_record):
        raise OSError("disk full")
    monkeypatch.setattr(_history(), "save", full)
    st = s.stop()
    assert st["state"] == "stopped" and st["history"]["state"] == "failed" and "disk full" in st["history"]["error"]
    assert len(s.observations()["observations"]) == 2                   # the stopped session is still readable


def test_exports_carry_the_session_id(session):
    s, _ = session
    s.replay_pass()
    sid = s.status()["session_id"]
    assert json.loads(s.export("json")[0])["session"]["session_id"] == sid
    assert sid in s.export("csv")[0].splitlines()[0]


def test_a_saved_record_exports_like_the_session_it_came_from(session):
    s, _ = session
    s.replay_pass()
    sid = s.status()["session_id"]
    live_csv = s.export("csv")[0].splitlines()[2:]                       # header row and data rows
    s.stop()
    r = _history().load(sid)
    body, ctype, name = monitor.export_record(r, "json")
    doc = json.loads(body)
    assert ctype == "application/json" and name == f"waveraider-sample-session-{sid[:8]}.json"
    assert doc["session"]["session_id"] == sid and "not live reception" in doc["note"] and len(doc["observations"]) == 2
    assert doc["saved_record"] is True
    body, ctype, name = monitor.export_record(r, "csv")
    assert ctype.startswith("text/csv") and name.endswith(f"{sid[:8]}.csv")
    assert body.splitlines()[2:] == live_csv and "not live reception" in body.splitlines()[1]
    with pytest.raises(ValueError):
        monitor.export_record(r, "xml")


def test_session_history_routes(server):
    base, s = server
    assert json.loads(_call(base + "/api/sessions")[2])["sessions"] == []
    _call(base + "/api/session", {"action": "start", "source": "sample"})
    deadline = time.time() + 5
    while time.time() < deadline and len(s.observations()["observations"]) < 2:
        time.sleep(0.02)
    sid = json.loads(_call(base + "/api/session", {"action": "stop"})[2])["session_id"]

    code, _, body = _call(base + "/api/sessions")
    assert code == 200 and [x["session_id"] for x in json.loads(body)["sessions"]] == [sid]
    code, _, body = _call(base + "/api/sessions/" + sid)
    assert code == 200 and json.loads(body)["session_id"] == sid
    code, headers, body = _call(base + f"/api/sessions/{sid}/export?format=csv")
    assert code == 200 and "attachment" in headers["Content-Disposition"] and b"not live reception" in body
    assert _call(base + f"/api/sessions/{sid}/export?format=xml")[0] == 400

    assert _call(base + "/api/sessions/" + "0" * 32)[0] == 404
    for bad in ("..%2F..%2Fetc%2Fpasswd", "abc", "A" * 32):
        assert _call(base + "/api/sessions/" + bad)[0] == 400
        assert _call(base + "/api/sessions/delete", {"session_id": bad})[0] == 400

    code, _, body = _call(base + "/api/sessions/delete", {"session_id": sid})
    assert code == 200 and json.loads(body)["deleted"] is True
    assert json.loads(_call(base + "/api/sessions/delete", {"session_id": sid})[2])["deleted"] is False
    _call(base + "/api/session", {"action": "start", "source": "sample"})
    _call(base + "/api/session", {"action": "stop"})
    code, _, body = _call(base + "/api/sessions/clear", {})
    assert code == 200 and json.loads(body)["removed"] == 1
    assert json.loads(_call(base + "/api/sessions")[2])["sessions"] == []


# ---- Review (Codex, 2026-09-19): SH-1…SH-8 ------------------------------------------

def _saved_session(base, s):
    _call(base + "/api/session", {"action": "start", "source": "sample"})
    sid = json.loads(_call(base + "/api/session", {"action": "stop"})[2])["session_id"]
    assert _history().load(sid) is not None
    return sid


def test_history_cannot_be_deleted_by_a_form_post_or_from_another_origin(server):
    """SH-1. The reviewer's probe: a cross-origin form-shaped POST to /api/sessions/clear."""
    base, s = server
    sid = _saved_session(base, s)
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert _call(base + "/api/sessions/clear", raw=b"junk=1", headers={**form, "Origin": "https://untrusted.example"})[0] == 415
    assert _call(base + "/api/sessions/clear", raw=b"{}", headers={"Content-Type": "text/plain"})[0] == 415
    assert _call(base + "/api/sessions/clear", {}, headers={"Origin": "https://untrusted.example"})[0] == 403
    assert _call(base + "/api/sessions/delete", {"session_id": sid}, headers={"Origin": "https://untrusted.example"})[0] == 403
    assert _call(base + "/api/sessions/clear", {}, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert _call(base + "/api/sessions/clear", raw=b"{not json")[0] == 400
    assert _call(base + "/api/sessions/clear", raw=b"[1, 2]")[0] == 400
    assert _history().load(sid) is not None                              # still there after all of that
    same = {"Origin": base, "Sec-Fetch-Site": "same-origin"}
    assert json.loads(_call(base + "/api/sessions/delete", {"session_id": sid}, headers=same)[2])["deleted"] is True


def test_a_record_is_still_saved_when_the_sample_cannot_be_read_for_its_hash(watch, monkeypatch, tmp_path):
    """SH-2. Provenance is taken at start; an unreadable sample is recorded as that, not raised at stop."""
    monkeypatch.setattr(monitor, "SAMPLES", tmp_path / "no-such-folder")
    s = monitor.MonitorSession(clock=Clock())
    sid = s.start("sample", threaded=False)["session_id"]
    st = s.stop()
    assert st["state"] == "stopped" and st["history"]["state"] == "saved"
    r = _history().load(sid)
    assert r["sample"]["sha256"] is None and "no-such-folder" in r["sample"]["sha256_error"]


def test_a_record_that_cannot_be_built_is_reported_not_raised(session, monkeypatch):
    """SH-2. Anything that fails while the record is put together is a failed save, reported for this session."""
    s, _ = session
    s.replay_pass()

    def boom(*a, **kw):
        raise RuntimeError("watchlist unreadable")
    monkeypatch.setattr(monitor.targets, "load", boom)
    st = s.stop()
    assert st["state"] == "stopped" and st["history"]["state"] == "failed" and "watchlist unreadable" in st["history"]["error"]


def test_a_worker_failure_with_an_unreadable_sample_still_leaves_an_error_record(watch, monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SAMPLES", tmp_path / "no-such-folder")
    s = monitor.MonitorSession(interval_s=0.001, pass_pause_s=0.01)
    sid = s.start("sample")["session_id"]
    deadline = time.time() + 5
    while time.time() < deadline and s.status()["history"]["state"] not in ("saved", "failed"):
        time.sleep(0.01)
    r = _history().load(sid)
    assert r["stop_reason"] == "error" and r["error"] and r["sample"]["sha256"] is None


def test_the_sample_hash_is_taken_for_each_session_not_cached_for_the_process(watch, monkeypatch, tmp_path):
    """SH-8."""
    import hashlib
    shutil.copy(monitor.SAMPLES / "uas.pcap", tmp_path / "uas.pcap")
    monkeypatch.setattr(monitor, "SAMPLES", tmp_path)
    s = monitor.MonitorSession(clock=Clock())
    first = s.start("sample", threaded=False)["session_id"]
    s.stop()
    with open(tmp_path / "uas.pcap", "ab") as f:
        f.write(b"\x00" * 4)
    second = s.start("sample", threaded=False)["session_id"]
    s.stop()
    a, b = _history().load(first)["sample"]["sha256"], _history().load(second)["sample"]["sha256"]
    assert a != b and b == hashlib.sha256((tmp_path / "uas.pcap").read_bytes()).hexdigest()


def test_a_late_save_result_from_an_old_session_does_not_touch_the_current_one(watch, monkeypatch):
    """SH-3. The reviewer's interleaving: the first session's replay fails, its save (made from the worker) is
    slow and then fails, after a second session has already stopped and saved."""
    gate, entered, real_save, real_frames = threading.Event(), threading.Event(), _history().save, monitor.iter_frames
    calls = {"frames": 0, "saves": 0}

    def frames(path):
        calls["frames"] += 1
        if calls["frames"] == 1:
            raise OSError("sample unreadable")
        return real_frames(path)

    def slow_failing_save(record):
        calls["saves"] += 1
        if calls["saves"] == 1:                                          # the first session's, from its worker
            entered.set()
            gate.wait(5)
            raise OSError("old session disk failure")
        return real_save(record)
    monkeypatch.setattr(monitor, "iter_frames", frames)
    monkeypatch.setattr(_history(), "save", slow_failing_save)
    s = monitor.MonitorSession(interval_s=0.001, pass_pause_s=0.01, clock=Clock())
    sid = s.start("sample")["session_id"]
    assert entered.wait(5)                                               # the old save is now in flight, in the worker
    second = s.start("sample", threaded=False)["session_id"]
    st = s.stop()
    assert st["session_id"] == second and st["history"]["state"] == "saved"
    gate.set()
    deadline = time.time() + 5
    while time.time() < deadline and any(t.name.startswith(f"monitor-replay-{id(s)}-") for t in threading.enumerate()):
        time.sleep(0.01)
    assert s.status()["history"] == {"state": "saved", "error": None}     # the old failure did not land here
    assert _history().load(second) is not None and _history().load(sid) is None


def test_status_says_saving_is_pending_until_it_is_known(session, monkeypatch):
    """SH-3. Stopped is not saved: while the write is in flight the status says pending."""
    s, _ = session
    gate, entered, real_save = threading.Event(), threading.Event(), _history().save

    def slow_save(record):
        entered.set()
        gate.wait(5)
        return real_save(record)
    monkeypatch.setattr(_history(), "save", slow_save)
    stopper = threading.Thread(target=s.stop)
    stopper.start()
    assert entered.wait(5)
    st = s.status()
    assert st["state"] == "stopped" and st["history"] == {"state": "pending", "error": None}
    gate.set()
    stopper.join(5)
    assert s.status()["history"]["state"] == "saved"
    assert monitor.MonitorSession().status()["history"] == {"state": None, "error": None}


def test_the_stopped_banner_reports_pending_saved_and_failed_even_after_a_replay_error(js):
    def view(extra):
        return json.loads(js('JSON.stringify(sessionView(Object.assign({state:"stopped",source:"sample",stopped_at:0,'
                             'session_id:"' + "a" * 32 + '"},' + json.dumps(extra) + '),10))'))["banner"]
    assert "Saving its record to history" in view({"history": {"state": "pending", "error": None}})
    assert "saved to session history" in view({"history": {"state": "saved", "error": None}})
    failed = view({"history": {"state": "failed", "error": "<b>disk full</b>"}})
    assert "disk full" in failed and "<b>disk" not in failed and "saved to session history" not in failed
    assert "saved to session history" not in view({})                    # an id alone is not a saved record
    both = view({"error": "OSError: gone", "history": {"state": "failed", "error": "disk full"}})
    assert "stopped unexpectedly" in both and "disk full" in both
    ok = view({"error": "OSError: gone", "history": {"state": "saved", "error": None}})
    assert "stopped unexpectedly" in ok and "saved to session history" in ok


def test_reading_the_sample_for_its_hash_does_not_hold_up_a_stop(watch, monkeypatch):
    """SI-3. The reviewer's probe: a replacement start is slow reading the sample; stop() must not wait for it."""
    gate, entered, real = threading.Event(), threading.Event(), monitor._provenance
    s = monitor.MonitorSession(clock=Clock())
    s.start("sample", threaded=False)

    def slow(name):
        entered.set()
        gate.wait(5)
        return real(name)
    monkeypatch.setattr(monitor, "_provenance", slow)
    starter = threading.Thread(target=lambda: s.start("sample", threaded=False))
    starter.start()
    assert entered.wait(5)
    stopped = []
    stopper = threading.Thread(target=lambda: stopped.append(s.stop()["state"]))
    stopper.start()
    stopper.join(2)
    assert stopped == ["stopped"]                                        # it did not wait for the slow read
    gate.set()
    starter.join(5)
    assert s.status()["state"] == "running"
    s.stop()


# ---- every route that changes something refuses a cross-site browser request ---------------

CHANGING_ROUTES = [
    ("/api/session", {"action": "start", "source": "sample"}),
    ("/api/monitor/watch", {"key": "wifi_beacon:60:60:1f:00:00:01"}),
    ("/api/settings/monitoring", {"stale_after_s": 99}),
    ("/api/settings/watchlist/add", {"type": "uas_id", "value": "SN-X", "note": ""}),
    ("/api/settings/watchlist/remove", {"type": "uas_id", "value": "SN-X"}),
    ("/api/spectrum/acquire", {"owner": "spec-test", "center": 2_440_000_000, "span": 2_000_000}),
    ("/api/spectrum/release", {"owner": "spec-test"}),
    ("/api/sessions/clear", {}),
]


@pytest.fixture
def no_radio(monkeypatch):
    """The spectrum routes must never reach a radio from a test: record the calls instead."""
    from waveraider import spectrum
    calls = []
    monkeypatch.setattr(spectrum, "acquire", lambda *a, **kw: calls.append(("acquire", a)) or {"ok": False, "error": "test"})
    monkeypatch.setattr(spectrum, "release", lambda *a, **kw: calls.append(("release", a)))
    return calls


@pytest.mark.parametrize("path,data", CHANGING_ROUTES)
def test_no_changing_route_accepts_a_form_post_a_foreign_origin_or_a_non_object(server, watch, no_radio, path, data):
    from waveraider import settings
    base, s = server
    evil = {"Origin": "https://untrusted.example"}
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert _call(base + path, raw=b"junk=1", headers={**form, **evil})[0] == 415
    assert _call(base + path, raw=json.dumps(data).encode(), headers={"Content-Type": "text/plain"})[0] == 415
    assert _call(base + path, data, headers=evil)[0] == 403
    assert _call(base + path, data, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert _call(base + path, raw=b"{not json")[0] == 400
    assert _call(base + path, raw=b"[1]")[0] == 400
    assert no_radio == [] and s.status()["state"] == "idle"              # nothing was started, acquired or released
    assert watch.load() == [] and settings.monitoring()["stale_after_s"] != 99


def test_the_pages_own_requests_still_reach_the_changing_routes(server, watch, no_radio):
    base, s = server
    same = {"Origin": base, "Sec-Fetch-Site": "same-origin"}
    assert _call(base + "/api/spectrum/acquire", {"owner": "spec-test"}, headers=same)[0] == 200
    assert _call(base + "/api/spectrum/release", {"owner": "spec-test"}, headers=same)[0] == 200
    assert [c[0] for c in no_radio] == ["acquire", "release"]
    assert _call(base + "/api/settings/watchlist/add", {"type": "uas_id", "value": "SN-X", "note": ""}, headers=same)[0] == 200
    assert [e["value"] for e in watch.load()] == ["SN-X"]
    assert json.loads(_call(base + "/api/session", {"action": "start", "source": "sample"})[2])["state"] == "running"


def test_inspect_rf_sends_its_radio_requests_as_json():
    src = (Path(monitor.__file__).with_name("inspect_rf.js")).read_text()
    posts = [line for line in src.splitlines() if '"/api/spectrum/acquire"' in line or '"/api/spectrum/release"' in line]
    assert len(posts) == 2 and all("specPost(" in line for line in posts)
    helper = next(line for line in src.splitlines() if line.startswith("const specPost="))
    assert '"Content-Type":"application/json"' in helper and 'method:"POST"' in helper


def test_every_post_route_in_the_handler_is_a_guarded_route():
    """A route added later without being listed would be unreachable, not unguarded."""
    from waveraider import web
    src = Path(web.__file__).read_text()
    post = src[src.index("    def do_POST(self):"):src.index("    def log_message")]
    handled = set(re.findall(r'u\.path == "(/api/[^"]+)"', post))
    assert handled == set(web.CHANGING_ROUTES) and {p for p, _ in CHANGING_ROUTES} | {"/api/sessions/delete"} == handled


# ---- PR-1: the GET routes that start RF work or touch the radio lease -------------------------

RF_GETS = ["/api/spectrum?center=2440000000&span=2000000&bins=64&owner=spec-evil",
           "/api/analyze?freq=2440000000", "/api/audio?freq=100000000", "/api/droneid?center=5800000000"]


@pytest.fixture
def no_rf(monkeypatch):
    from waveraider import spectrum
    calls = []
    for name in ("sweep", "analyze_signal", "scan_droneid", "capture_audio"):
        assert hasattr(spectrum, name), name                             # a renamed entry point must not slip through unpatched
        monkeypatch.setattr(spectrum, name, lambda *a, _n=name, **kw: calls.append(_n) or {"ok": True, "available": True})
    for name in ("source_status", "readiness", "acquire", "release"):    # fail closed: nothing may probe hardware
        monkeypatch.setattr(spectrum, name, lambda *a, _n=name, **kw: pytest.fail(f"spectrum.{_n} reached from a test"))
    return calls


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.mark.parametrize("path", RF_GETS)
def test_rf_work_cannot_be_started_by_a_link_image_or_page_on_another_site(server, no_rf, path):
    """PR-1. A cross-site page can make a browser GET anything; it cannot add a custom header without a
    preflight, which this server never grants."""
    base, _ = server
    mine = {"X-WaveRaider-Request": "1"}
    assert _get(base + path) == 403                                       # an <img>, a link, a plain fetch
    assert _get(base + path, {"Sec-Fetch-Site": "cross-site"}) == 403
    assert _get(base + path, {**mine, "Sec-Fetch-Site": "cross-site"}) == 403
    assert _get(base + path, {**mine, "Origin": "https://untrusted.example"}) == 403
    assert no_rf == []                                                    # nothing swept, analyzed, scanned or leased
    assert _get(base + path, {**mine, "Sec-Fetch-Site": "same-origin"}) in (200, 400)
    assert len(no_rf) <= 1


def test_inspect_rf_marks_every_request_as_its_own():
    src = (Path(monitor.__file__).with_name("inspect_rf.js")).read_text()
    helper = next(line for line in src.splitlines() if line.startswith("const specApi="))
    assert '"X-WaveRaider-Request":"1"' in helper
    assert src.count("fetch(") == 1                                       # every request goes through specApi


def test_the_guarded_get_routes_are_exactly_the_ones_that_reach_the_radio_module():
    from waveraider import web
    src = Path(web.__file__).read_text()
    get = src[src.index("    def do_GET(self):"):src.index("    def do_POST(self):")]
    blocks = re.split(r'\n        if u\.path', get)
    # A GET route "reaches the radio module" if it calls a spectrum.* RF function
    # OR the ELRS plugin's sweep (elrs_plugin.sweep_phy, which opens a RadioDevice).
    rf = {re.match(r' == "(/api/[^"]+)"', b).group(1) for b in blocks
          if re.match(r' == "(/api/[^"]+)"', b)
          and re.search(r"spectrum\.(sweep|analyze_signal|scan_droneid|capture_audio)|elrs_plugin\.sweep_phy|RadioDevice\(", b)}
    assert rf == set(web.RF_GET_ROUTES)


# ---- observation lifecycle: fresh, stale, older; gaps between updates ---------------------------

def _lifecycles(s):
    return {o["transmitter"]: o["lifecycle"] for o in s.observations()["observations"]}


def test_an_observation_goes_from_fresh_to_stale_to_older_and_is_never_removed(session):
    s, clock = session
    s.replay_pass()
    assert set(_lifecycles(s).values()) == {"fresh"}
    clock.t += 31                                                        # past the 30 s stale default
    assert set(_lifecycles(s).values()) == {"stale"}
    clock.t += 300                                                       # past the 300 s default age threshold
    rows = s.observations()["observations"]
    assert {o["lifecycle"] for o in rows} == {"lapsed"} and len(rows) == 2   # still listed, still exportable
    assert all(o["stale"] for o in rows)                                 # the older flag stays true once not fresh
    assert s.status()["lifecycle_counts"] == {"fresh": 0, "stale": 0, "lapsed": 2}


def test_an_update_after_a_gap_longer_than_the_stale_threshold_is_recorded_as_a_gap(session):
    s, clock = session
    s.replay_pass()
    first = _by_transmitter(s)[DRONE_A]
    assert first["update_gaps"] == {"count": 0, "last_gap_s": None, "at": None} and first["sequence_started"] == first["first_seen"]
    clock.t += 45
    s.replay_pass()
    again = _by_transmitter(s)[DRONE_A]
    assert again["lifecycle"] == "fresh" and again["first_seen"] == first["first_seen"]
    assert again["update_gaps"]["count"] == 1 and again["update_gaps"]["last_gap_s"] == pytest.approx(45, abs=0.01)
    assert again["update_gaps"]["at"] == again["sequence_started"] == pytest.approx(clock.t, abs=0.01)
    clock.t += 400
    s.replay_pass()
    assert _by_transmitter(s)[DRONE_A]["update_gaps"]["count"] == 2


def test_a_pause_no_longer_than_the_stale_threshold_is_not_a_gap(session):
    s, clock = session
    s.replay_pass()
    clock.t += 20
    s.replay_pass()
    o = _by_transmitter(s)[DRONE_A]
    assert o["update_gaps"]["count"] == 0 and o["sequence_started"] == o["first_seen"]


def test_a_clock_set_backwards_and_then_corrected_does_not_invent_a_gap_or_an_age(watch):
    """OL-2. The reviewer's probe: wall clock 1000 → 900 → 1001 while three seconds really pass."""
    wall, mono = Clock(1000.0), Clock(50.0)
    s = monitor.MonitorSession(clock=wall, mono=mono)
    s.start("sample", threaded=False)
    s.replay_pass()
    wall.t, mono.t = 900.0, 51.0
    s.replay_pass()
    wall.t, mono.t = 1001.0, 52.0
    s.replay_pass()
    o = _by_transmitter(s)[DRONE_A]
    assert o["update_gaps"] == {"count": 0, "last_gap_s": None, "at": None} and o["lifecycle"] == "fresh"
    mono.t = 52.0 + 31                                                   # 31 real seconds with no update…
    wall.t = 1001.0                                                      # …while the wall clock hasn't moved
    assert _lifecycles(s)[DRONE_A] == "stale" and s.status()["lifecycle_counts"]["stale"] == 2
    wall.t, mono.t = 5000.0, 84.0                                        # a forward wall jump of an hour; one real second
    s.replay_pass()
    assert _by_transmitter(s)[DRONE_A]["update_gaps"]["count"] == 1      # the real 32 s gap, not the hour
    assert _by_transmitter(s)[DRONE_A]["update_gaps"]["last_gap_s"] == pytest.approx(32, abs=0.01)
    s.stop()


def test_recorded_captures_outside_monitor_do_not_track_gaps():
    """The decoder is also used on recorded files, where packet times are the capture's; gap tracking is Monitor's."""
    from waveraider.uas_classify import UasClassifier
    from waveraider.capture import iter_frames
    c = UasClassifier(path="file")
    for pkt in iter_frames(monitor.SAMPLES / "uas.pcap"):
        c.update(pkt)
    assert all("update_gaps" not in e.attrs and "sequence_started" not in e.attrs for e in c.survey.of_kind("uas"))


def test_exports_and_saved_records_carry_the_lifecycle_and_old_records_still_export(session):
    s, clock = session
    s.replay_pass()
    clock.t += 45
    s.replay_pass()
    header, *rows = s.export("csv")[0].splitlines()[2:]
    cols = header.split(",")
    assert cols[-3:] == ["lifecycle", "update_gaps", "last_gap_s"] and all(r.endswith(",fresh,1,45.0") for r in rows)
    sid = s.status()["session_id"]
    s.stop()
    r = _history().load(sid)
    assert {o["lifecycle"] for o in r["observations"]} == {"fresh"} and r["lapsed_after_s"] == 300.0
    old = {**r, "observations": [{k: v for k, v in o.items() if k not in ("lifecycle", "update_gaps", "sequence_started")}
                                 for o in r["observations"]]}
    body = monitor.export_record(old, "csv")[0]                          # a v0.6.3 record has none of the new fields
    assert all(line.endswith(",,,") for line in body.splitlines()[3:])


def test_monitor_groups_older_rows_without_claiming_anything_left_or_came_back(js):
    base = dict(ROW)
    fresh = {**base, "key": "k1", "lifecycle": "fresh", "stale": False,
             "update_gaps": {"count": 2, "last_gap_s": 95, "at": 100}}
    lapsed = {**base, "key": "k2", "transmitter": "60:60:1f:00:00:0a", "lifecycle": "lapsed", "stale": True,
              "update_gaps": {"count": 0, "last_gap_s": None, "at": None}}
    html = js(f"renderRows({json.dumps([fresh, lapsed])}, 131, null)")
    assert "Sample observation updated after a 1 min 35 s gap; 2 gaps recorded" in html
    head, _, tail = html.partition("Older sample observations (1)")
    assert "k1" in head and "k2" not in head and "k2" in tail and "past the age threshold" in tail
    assert html.count("<tbody") == 2                                     # OL-6: the heading labels its own row group
    live = [{**fresh, "sample": False}, {**lapsed, "sample": False}]
    out = js(f"renderRows({json.dumps(live)}, 131, null)")
    assert "Older observations (1)" in out and "Updated after a 1 min 35 s gap" in out and "Sample observation" not in out
    for banned in ("heard again", "No longer heard", "no longer heard", "without a gap", "came back", "reappear"):
        assert banned not in html and banned not in out
    assert "do not show live reception or establish that an aircraft left or returned" in html   # the only mention is the denial
    assert "Older" not in js(f"renderRows({json.dumps([fresh])}, 131, null)")
    old = {k: v for k, v in base.items() if k not in ("lifecycle", "update_gaps")}
    assert "undefined" not in js(f"renderRows({json.dumps([old])}, 131, null)")   # rows from an older server still render


def test_the_detail_panel_describes_the_latest_sequence_and_what_gaps_do_not_show(js):
    detail = {**ROW, "lifecycle": "lapsed", "stale": True, "sequence_started": 100,
              "update_gaps": {"count": 2, "last_gap_s": 95, "at": 100}}
    html = js(f"renderDetail({json.dumps(detail)}, 131)")
    assert "past the age threshold" in html and "Latest recorded sequence" in html and "2 gaps recorded" in html
    assert "do not show live reception" in html and "left or returned" in html and "not an alert or detection policy" in html
    for banned in ("Heard again", "heard again", "no longer heard", "without a gap"):
        assert banned not in html
    plain = {k: v for k, v in ROW.items() if k not in ("lifecycle", "update_gaps", "sequence_started")}
    out = js(f"renderDetail({json.dumps(plain)}, 131)")
    assert "Latest recorded sequence" not in out and "31 s ago · stale" in out   # an older row renders as before


# --------------------------------------------------------------------------- #
# "Drones nearby" table: the drones-only filter, the protocol cell, and the
# confirmed-link detail line. These are pure (node-executed); the DOM-coupled
# streaming loop is exercised in the browser.
#
# Regression context: protoCell previously read row.seed_set / row.elrs_net_id /
# row.droneid that the sweep loop NEVER set, so the identity headline could never
# fire — every row was "chirp hint" or "Unidentified". isDroneRow is the new
# drones-only filter the operator asked for ("just the drones nearby").
# --------------------------------------------------------------------------- #
def _drone_row(js, row, seed="null"):
    return js(f"isDroneRow({json.dumps(row)}, {seed})").strip()


def test_drones_only_filter_keeps_links_and_candidates_hides_wifi(js):
    # a confirmed ELRS link (seed-confirmed, net_id set)
    assert _drone_row(js, {"freq_hz": 2.4404e9, "elrs_net_id": "00111858"}) == "true"
    # a hopping link found by a seedless dwell (present, net_id unconfirmed)
    assert _drone_row(js, {"freq_hz": 2.4404e9, "elrs_link": True}) == "true"
    # a DroneID candidate — kept for the future DJI integration
    assert _drone_row(js, {"freq_hz": 2.4404e9, "droneid": True}) == "true"
    # a chirp-hint carrier
    assert _drone_row(js, {"freq_hz": 2.4414e9, "rf_candidates": ["ELRS/LoRa?"]}) == "true"
    # a sync-band carrier becomes a candidate once a seed is set
    assert _drone_row(js, {"freq_hz": 2.4404e9, "rf_candidates": []}, seed="true") == "true"
    # a plain Wi-Fi carrier with no drone evidence is hidden in drones-only
    assert _drone_row(js, {"freq_hz": 2.412e9, "rf_candidates": []}) == "false"
    # ...and does NOT become a drone just because a seed is set (it's out of band)
    assert _drone_row(js, {"freq_hz": 2.412e9, "rf_candidates": []}, seed="true") == "false"


def test_proto_cell_fires_the_identity_headline_and_stays_honest(js):
    def pc(row, seed="null"):
        return js(f"protoCell({json.dumps(row)}, {seed})")
    ident = pc({"freq_hz": 2.4404e9, "elrs_net_id": "00111858"})
    assert "Net ID 00111858" in ident
    assert "not an aircraft" in ident                       # the honesty tooltip is preserved
    present = pc({"freq_hz": 2.4404e9, "elrs_link": True})   # link found, net_id not confirmed
    assert "link" in present.lower() and "Net ID" not in present
    assert "DroneID" in pc({"freq_hz": 2.4404e9, "droneid": True})
    cand = pc({"freq_hz": 2.4404e9}, seed="true")
    assert "candidate" in cand.lower() and "unconfirmed" in cand.lower()
    assert "chirp hint" in pc({"freq_hz": 2.4414e9, "rf_candidates": ["lora"]}).lower()
    assert "Unidentified" in pc({"freq_hz": 2.45e9, "rf_candidates": []})


def test_confirmed_link_detail_shows_corroboration_or_admits_it_is_unconfirmed(js):
    confirmed = js("elrsLinkDetail(" + json.dumps({
        "net_id": "00111858", "elrs_generation": "elrs<=3.x",
        "hop_interval_ms": 26.6663, "evidence": "VALIDATED"}) + ")")
    assert "elrs&lt;=3.x" in confirmed and "26.7" in confirmed and "VALIDATED" in confirmed  # esc() escapes the <
    present = js("elrsLinkDetail(" + json.dumps({
        "net_id": None, "evidence": "SELF-CONSISTENT", "hop_interval_ms": 26.6}) + ")")
    assert "unconfirmed" in present.lower()
