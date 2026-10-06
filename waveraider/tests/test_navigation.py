"""Navigation after retirement: WaveRaider opens on Monitor with two tabs, the old
protocol dashboard and its APIs are gone, and /classic redirects to Monitor."""
from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from waveraider import cli, web

PKG = Path(web.__file__).parent
ROOT = PKG.parent
MONITOR = (PKG / "monitor.html").read_text()
SETTINGS = (PKG / "settings.html").read_text()


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("WAVERAIDER_TARGETS", str(tmp_path / "targets.json"))
    monkeypatch.setenv("WAVERAIDER_MONITORING", str(tmp_path / "monitoring.json"))
    monkeypatch.setenv("WAVERAIDER_INTERFACES", str(tmp_path / "interfaces.json"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], tmp_path
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=None if body is None else json.dumps(body))
    r = conn.getresponse()
    status, headers, data = r.status, dict(r.getheaders()), r.read()
    conn.close()
    return status, headers, data


def _tabs(page):
    nav = re.search(r'<nav class="tabs" aria-label="Main">(.*?)</nav>', page, re.S).group(1)
    return re.findall(r'<a class="tab[^"]*" href="([^"]+)"[^>]*>([^<]+)</a>', nav)


def test_home_is_monitor(server):
    port, _ = server
    status, headers, home = _request(port, "GET", "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b"<title>WaveRaider Monitor</title>" in home
    assert _request(port, "GET", "/monitor")[2] == home
    assert b"<title>WaveRaider Settings</title>" in _request(port, "GET", "/settings")[2]


def test_classic_redirects_once_to_monitor(server):
    port, _ = server
    status, headers, body = _request(port, "GET", "/classic")
    assert (status, headers["Location"], body) == (302, "/", b"")
    assert not hasattr(web, "INDEX_HTML")


@pytest.mark.parametrize("path", ["/api/survey?protocol=wifi", "/api/protocols", "/api/target-sweep",
                                  "/api/targets", "/api/interfaces", "/api/status"])
def test_retired_get_apis_are_gone(server, path):
    port, _ = server
    status, _, body = _request(port, "GET", path)
    assert status == 404 and json.loads(body) == {"error": "not found"}


@pytest.mark.parametrize("path, body", [
    ("/api/targets/add", {"type": "ssid", "value": "HomeNet"}), ("/api/targets/remove", {"value": "HomeNet"}),
    ("/api/interfaces/add", {"name": "alfa-1", "kind": "wifi"}), ("/api/interfaces/remove", {"name": "alfa-1"}),
])
def test_retired_post_apis_are_gone_and_write_nothing(server, path, body):
    port, home = server
    status, _, data = _request(port, "POST", path, body)
    assert status == 404 and json.loads(data) == {"error": "not found"}
    assert not (home / "targets.json").exists() and not (home / "interfaces.json").exists()


@pytest.mark.parametrize("path, ctype", [
    ("/api/logs", "application/json"), ("/api/uas-signatures", "application/json"),
    ("/api/settings/watchlist", "application/json"), ("/inspect-rf.js", "application/javascript"),
    ("/inspect-rf.css", "text/css"), ("/spectrum-display.js", "application/javascript"),
])
def test_kept_routes_still_work(server, path, ctype):
    port, _ = server
    status, headers, _ = _request(port, "GET", path)
    assert status == 200 and headers["Content-Type"].startswith(ctype)


def test_monitor_and_settings_show_two_tabs_and_no_old_dashboard():
    for page in (MONITOR, SETTINGS):
        assert _tabs(page) == [("/monitor", "Monitor"), ("/settings", "Settings")]
        assert "/classic" not in page and "Classic dashboard" not in page
        assert "legacy dashboard" not in page.lower() and "protocol dashboard" not in page.lower()


def test_settings_about_no_longer_points_to_a_legacy_dashboard():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to run the Settings renderers")
    block = SETTINGS.split("/* ---- pure renderers ---- */", 1)[1].split("/* ---- end pure renderers ---- */", 1)[0]
    html = subprocess.check_output([node, "-e", block + "\nprocess.stdout.write(renderAbout("
                                    "{version:'x',session:{state:'idle'},backends:{rules:'ready',ollama:'not running'}},[]));"],
                                   text=True)
    assert "/classic" not in html and "Legacy" not in html and "legacy" not in html
    assert "wr --explain" in html


def test_cli_help_and_docs_describe_the_entry_point():
    source = Path(cli.__file__).read_text()
    assert "web UI on Monitor + Settings (sample data)" in source
    assert "legacy dashboard" not in source and "/classic" not in source
    readme, usage = (ROOT / "README.md").read_text(), (ROOT / "docs" / "usage.md").read_text()
    assert "Start sample session" in readme and "Start sample session" in usage
    for text in (readme, usage):
        assert "Survey → UAS" not in text and "Monitor + Settings is planned" not in text
        assert "localhost:8899/classic" not in text
