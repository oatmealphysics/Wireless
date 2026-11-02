"""The RF referrer gate and its localhost exemption (waveraider.web).

The RF GET routes (/api/elrs, /api/droneid, ...) are gated so a random web page the user
has open cannot drive the radio through their browser (CSRF). The gate passes browser
requests that carry the app's own marker header, and — via `_local_cli_request` — also
passes a bare local CLI (curl/script on the loopback interface with no browser fetch
metadata), so the operator's terminal works without knowing the marker header. These tests
pin both halves: the exemption lets local curl through, and it does NOT reopen the CSRF hole.
"""
import json
from types import SimpleNamespace

from waveraider import web
from waveraider.web import Handler, _foreign_rf_request, _local_cli_request


# --- the two gate helpers, directly -------------------------------------------------

def test_marker_header_alone_passes_the_foreign_check():
    # the gate keys on X-WaveRaider-Request, not on Referer/Origin
    assert _foreign_rf_request({"X-WaveRaider-Request": "1"}, "localhost:8899") is False


def test_spoofing_only_referer_and_origin_is_still_foreign():
    # no marker header -> still refused, even with Referer/Origin set (andeds's spoof example)
    hdrs = {"Referer": "http://localhost:8899/monitor", "Origin": "http://localhost:8899"}
    assert _foreign_rf_request(hdrs, "localhost:8899") is True


def test_exemption_is_off_by_default():
    # fail-closed by default: without the opt-in env var, even a bare loopback curl is not exempt
    assert _local_cli_request({}, "127.0.0.1") is False


def test_bare_loopback_curl_is_exempt_when_opted_in(monkeypatch):
    # curl from the terminal, operator opted in: loopback peer, no browser fetch-metadata, no Origin
    monkeypatch.setenv("WAVERAIDER_LOCAL_CLI", "1")
    assert _local_cli_request({}, "127.0.0.1") is True
    assert _local_cli_request({}, "::1") is True


def test_browser_fetch_from_loopback_is_not_exempt_even_when_opted_in(monkeypatch):
    # even with the exemption on, a browser still sends Sec-Fetch-* (and Origin on cross-site) -> refused
    monkeypatch.setenv("WAVERAIDER_LOCAL_CLI", "1")
    assert _local_cli_request({"Sec-Fetch-Site": "cross-site"}, "127.0.0.1") is False
    assert _local_cli_request({"Sec-Fetch-Mode": "cors"}, "127.0.0.1") is False
    assert _local_cli_request({"Origin": "http://evil.example"}, "127.0.0.1") is False


def test_remote_client_is_never_exempt_even_when_opted_in(monkeypatch):
    # the exemption is loopback-only; a LAN/remote client still needs the marker header
    monkeypatch.setenv("WAVERAIDER_LOCAL_CLI", "1")
    assert _local_cli_request({}, "192.168.1.50") is False
    assert _local_cli_request({}, None) is False


# --- end-to-end through Handler.do_GET ----------------------------------------------

def _run_get(path, headers, client_ip, monkeypatch):
    """Drive Handler.do_GET for an RF route and capture _send(code, body). The /api/elrs
    handler is stubbed so a *passed* gate doesn't touch real hardware."""
    import waveraider.protocols.elrs as elrs
    monkeypatch.setattr(elrs, "sweep_phy", lambda *a, **k: {"channels": [], "available": True},
                        raising=False)
    responses = []
    handler = SimpleNamespace(
        path=path, headers=headers, client_address=(client_ip, 12345),
        _send=lambda code, body, *a, **k: responses.append((code, json.loads(body))),
    )
    Handler.do_GET(handler)
    return responses


def test_do_get_refuses_cross_site_browser(monkeypatch):
    # browser cross-site fetch (marker header cannot be set cross-site) -> 403
    resp = _run_get("/api/elrs", {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example",
                                  "Host": "localhost:8899"}, "127.0.0.1", monkeypatch)
    assert resp and resp[0][0] == 403
    assert resp[0][1]["available"] is False


def test_do_get_refuses_bare_local_curl_by_default(monkeypatch):
    # default fail-closed: without opt-in, a bare loopback curl is still refused
    monkeypatch.delenv("WAVERAIDER_LOCAL_CLI", raising=False)
    resp = _run_get("/api/elrs", {"Host": "localhost:8899"}, "127.0.0.1", monkeypatch)
    assert resp and resp[0][0] == 403


def test_do_get_allows_bare_local_curl_when_opted_in(monkeypatch):
    # operator opted in -> gate passes (no 403 refusal is sent first)
    monkeypatch.setenv("WAVERAIDER_LOCAL_CLI", "1")
    resp = _run_get("/api/elrs", {"Host": "localhost:8899"}, "127.0.0.1", monkeypatch)
    assert not (resp and resp[0][0] == 403), f"opted-in local curl should pass the gate; got {resp[:1]}"
