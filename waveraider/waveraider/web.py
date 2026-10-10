"""Wave Raider local web console, served from stdlib http.server. No web framework,
no build step, offline.

Monitor (/, also /monitor) is the home screen: sample-labeled Remote ID observations
with the Inspect RF drawer. Settings (/settings) holds receivers, monitoring, the
watchlist, data & export, and about & activity. The protocol dashboard was retired
after v0.5.1; /classic redirects to Monitor, and its APIs are gone.
"""
from __future__ import annotations

import errno
import json
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import logbuf

_START = time.time()


def _ollama_ok() -> bool:
    try:
        with socket.create_connection(("localhost", 11434), timeout=0.3):
            return True
    except Exception:
        return False


# Every POST route. Each changes something, so each goes through Handler._refuse_untrusted_change.
# (State is also changed by some GETs: see RF_GET_ROUTES below.)
CHANGING_ROUTES = frozenset({
    "/api/spectrum/acquire", "/api/spectrum/release", "/api/session", "/api/sessions/delete",
    "/api/sessions/clear", "/api/monitor/watch", "/api/settings/monitoring",
    "/api/settings/watchlist/add", "/api/settings/watchlist/remove",
})


# GET routes that start RF work or touch the radio lease (a sweep with an owner takes or renews it).
# They are reads in form only, so they get their own check: Handler._refuse_foreign_rf_request.
RF_GET_ROUTES = frozenset({"/api/spectrum", "/api/analyze", "/api/audio", "/api/droneid", "/api/elrs", "/api/elrs/progress", "/api/elrs/fhss"})
OWN_REQUEST_HEADER = "X-WaveRaider-Request"


def _foreign_rf_request(headers, host) -> bool:
    """True unless the request carries this app's own marker header and no sign of another site. A page
    on another site can make a browser GET any URL (an image, a link, a plain fetch) but cannot add a
    custom header without a preflight, which this server never grants. Not authentication: a script
    that sets the header is accepted like any other client of this unauthenticated server.

    Note the gate keys on OWN_REQUEST_HEADER (`X-WaveRaider-Request: 1`), NOT on the Referer/Origin:
    a `curl` that just sets that one header passes, while spoofing only Referer/Origin does not.
    A bare local CLI with no headers at all can also be let through by `_local_cli_request` (the
    loopback exemption), but only when the operator opts in with WAVERAIDER_LOCAL_CLI=1; by default
    the gate stays fail-closed and a terminal `curl` must send the marker header."""
    get = headers.get if headers is not None else (lambda _k, _d=None: None)
    if get(OWN_REQUEST_HEADER) != "1":
        return True
    if (get("Sec-Fetch-Site") or "same-origin").lower() not in ("same-origin", "none"):
        return True
    origin = get("Origin")
    return origin is not None and origin not in (f"http://{host}", f"https://{host}")


# Loopback interface addresses: a client here is a process on this same machine.
_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def _local_cli_enabled() -> bool:
    """The localhost-CLI exemption is OPT-IN, default OFF. Set WAVERAIDER_LOCAL_CLI=1 to enable it.

    Off by default on purpose: the RF gate is fail-closed (no positive proof of same-origin ->
    refuse), and that default is what `test_rf_work_cannot_be_started_...` pins. An operator who
    wants a terminal `curl`/script to drive the radio without the marker header opts in explicitly
    and accepts the narrow tradeoff documented on `_local_cli_request`."""
    import os
    return (os.environ.get("WAVERAIDER_LOCAL_CLI") or "").strip().lower() in ("1", "true", "yes", "on")


def _local_cli_request(headers, client_ip) -> bool:
    """True for a non-browser client on the loopback interface — the operator's own terminal
    (`curl`, a script) hitting 127.0.0.1, with none of the fetch-metadata a browser attaches —
    AND only when the operator has opted in with WAVERAIDER_LOCAL_CLI=1 (see `_local_cli_enabled`).

    This is the localhost exemption to the RF referrer gate. The gate exists only to stop a web
    page the user happens to have open from driving the radio through their browser (CSRF); a
    local CLI is not that threat. The distinction is reliable for modern browsers (Chrome 76+,
    Firefox 90+): they attach `Sec-Fetch-*` headers to EVERY request — same-origin, cross-origin
    and to localhost alike — and a page cannot suppress them, while `curl`/scripts send none. So a
    loopback peer that sends no `Sec-Fetch-*` and no `Origin` is a local operator at a terminal. The
    residual, operator-accepted risk (why this is opt-in, not the default): an OLD browser that omits
    `Sec-Fetch-*` could have a cross-site page drive the radio via a no-cors GET to localhost. A
    browser that does send `Sec-Fetch-*` (or any `Origin`) is refused regardless of the flag, and a
    non-loopback client is never exempted — remote access still needs the marker header."""
    if not _local_cli_enabled():
        return False
    if client_ip not in _LOOPBACK_PEERS:
        return False
    get = headers.get if headers is not None else (lambda _k, _d=None: None)
    if get("Origin") is not None:
        return False
    # Any Sec-Fetch-* header means a browser made this request — do not exempt it.
    return not any(get(h) is not None for h in ("Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-Dest"))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", headers=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        self._raw = self.rfile.read(n)
        try:
            return json.loads(self._raw or b"{}")
        except Exception:
            return {}

    def _refuse_untrusted_change(self):
        """A browser cross-site-request measure for every route that changes something (the watchlist,
        the monitoring profile, the session, saved sessions, the radio lease) — not authentication, and
        not a gate on every client. A browser can send a form or text/plain POST
        to another site's server without asking, but not application/json without a preflight, which
        this server never grants; so the body must be application/json, and origin metadata, when a
        browser supplies it, must be this server's own (compared with the Host header, so a reverse
        proxy has to preserve Host). A client that sends JSON and no origin metadata — curl, a script —
        is accepted like any other request to this unauthenticated server. True if a refusal was sent."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._send(415, json.dumps({"error": "Send this request as application/json."}))
            return True
        origin, host = self.headers.get("Origin"), self.headers.get("Host")
        if (self.headers.get("Sec-Fetch-Site") or "same-origin").lower() not in ("same-origin", "none") or \
                (origin is not None and origin not in (f"http://{host}", f"https://{host}")):
            self._send(403, json.dumps({"error": "This change can only be made from WaveRaider's own pages."}))
            return True
        try:
            ok = isinstance(json.loads(self._raw or b""), dict)
        except ValueError:
            ok = False
        if not ok:
            self._send(400, json.dumps({"error": "The request body must be a JSON object."}))
            return True
        return False

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/monitor"):
            return self._send(200, Path(__file__).with_name("monitor.html").read_text(), "text/html; charset=utf-8")
        brand_assets = {"/brand/logo.png": "logo.png", "/brand/icon.png": "icon.png", "/favicon.ico": "icon.png"}
        if u.path in brand_assets:
            asset = Path(__file__).parent / "brand" / brand_assets[u.path]
            return self._send(200, asset.read_bytes(), "image/png")
        if u.path == "/classic":                  # retired protocol dashboard: old bookmarks land on Monitor
            return self._redirect("/")
        if u.path == "/spectrum-display.js":
            return self._send(200, Path(__file__).with_name("spectrum_display.js").read_text(), "application/javascript; charset=utf-8")
        if u.path == "/inspect-rf.js":
            return self._send(200, Path(__file__).with_name("inspect_rf.js").read_text(), "application/javascript; charset=utf-8")
        if u.path == "/inspect-rf.css":
            return self._send(200, Path(__file__).with_name("inspect_rf.css").read_text(), "text/css; charset=utf-8")
        if u.path in RF_GET_ROUTES:
            headers = getattr(self, "headers", None)
            host = headers.get("Host") if headers is not None else None
            client = getattr(self, "client_address", None)
            client_ip = client[0] if client else None
            if _foreign_rf_request(headers, host) and not _local_cli_request(headers, client_ip):
                return self._send(403, json.dumps({"error": "Radio requests can only be made from WaveRaider's own pages.",
                                                   "available": False}))
        if u.path == "/api/spectrum":
            from . import spectrum
            q = parse_qs(u.query)
            try:
                center = float(q.get("center", ["100000000"])[0])
                span = float(q.get("span", ["2000000"])[0])
                bins = int(q.get("bins", ["256"])[0])
                owner = q.get("owner", [""])[0] or None
                device = q.get("device", [""])[0] or None
                elrs_hint = q.get("elrs_hint", ["0"])[0] in ("1", "true", "True", "yes")
                return self._send(200, json.dumps(
                    spectrum.sweep(center, span, bins, owner, device, elrs_hint=elrs_hint)))
            except Exception as e:
                return self._send(400, json.dumps({"error": str(e)}))
        if u.path == "/api/sdr":
            from . import spectrum
            device = parse_qs(u.query).get("device", [""])[0] or None
            return self._send(200, json.dumps(spectrum.control_status(device)))
        if u.path == "/api/analyze":
            from . import spectrum
            q = parse_qs(u.query)
            try:
                freq = float(q.get("freq", ["100000000"])[0])
                device = q.get("device", [""])[0] or None
                logbuf.log("info", f"analyze {freq/1e6:.3f} MHz")
                return self._send(200, json.dumps(spectrum.analyze_signal(freq, device)))
            except Exception as e:
                return self._send(400, json.dumps({"error": str(e)}))
        if u.path == "/api/uas-signatures":
            from . import uas_rf
            return self._send(200, json.dumps(uas_rf.catalog()))
        if u.path == "/api/audio":
            from . import spectrum
            q = parse_qs(u.query)
            freq = float(q.get("freq", ["100000000"])[0])
            device = q.get("device", [""])[0] or None
            r = spectrum.capture_audio(freq, device)
            if isinstance(r, (bytes, bytearray)):
                logbuf.log("info", f"audio {freq/1e6:.3f} MHz")
                return self._send(200, bytes(r), "audio/wav")
            return self._send(200, json.dumps(r))
        if u.path == "/api/droneid":
            from . import spectrum
            q = parse_qs(u.query, keep_blank_values=True)
            device = q.get("device", [""])[0] or None
            try:
                center = spectrum.validate_droneid_center(q.get("center", [None])[0])
            except ValueError as e:
                return self._send(400, json.dumps({"available": False, "error": str(e),
                                                  "note": str(e)}))
            logbuf.log("info", f"DroneID scan centered at {center / 1e6:.3f} MHz")
            return self._send(200, json.dumps(spectrum.scan_droneid(device, center)))
        if u.path == "/api/elrs/progress":
            # SSE stream of the ELRS Find sweep: a `data: {json}` line after each
            # channel (live % + freq + per-channel PHY), then a final
            # `data: {"result": {...}}` line with the full result. The UI reads this
            # stream to update the "sweeping…" box with a live percentage + bar.
            from .protocols import elrs as elrs_plugin
            if not elrs_plugin.core_available():
                return self._send(400, json.dumps({"error": elrs_plugin.core_error()}))
            q = parse_qs(u.query, keep_blank_values=True)
            try:
                srate = float(q.get("srate", ["10000000"])[0])
                gain = float(q.get("gain", ["40"])[0])
                dwell_ms = int(q.get("dwell_ms", ["50"])[0])
                channels = int(q.get("channels", ["80"])[0])
                freq0 = float(q.get("freq0", ["2400400000"])[0])
                spacing = float(q.get("spacing", ["1000000"])[0])
                uid_hex = q.get("uid", [""])[0]
                device = q.get("device", [""])[0] or None
            except ValueError as e:
                return self._send(400, json.dumps({"error": f"bad param: {e}"}))
            uid = bytes.fromhex(uid_hex) if uid_hex else None
            import time as _time
            cap_root = Path(__file__).resolve().parents[2] / "captures"
            out_dir = str(cap_root / f"elrs_find_{_time.strftime('%Y%m%d_%H%M%S')}")
            # SSE headers (no Content-Length; stream until the connection closes).
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def _sse(obj):
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
                self.wfile.flush()

            def _progress(ev):
                _sse({"type": "progress", **ev})

            try:
                from elrs_sdr.radio import RadioDevice
                from . import spectrum as _spectrum, uhd_backend as _uhdb
                # Serialize access to the single, non-thread-safe B205 with Inspect
                # RF (which holds the device open via uhd_backend._DEV). Without this,
                # a concurrent spectrum sweep or a double-clicked ELRS Find collides
                # on the radio. Release any held device before we claim it.
                with _spectrum._DEVICE_LOCK:
                    _uhdb.close()
                    radius = RadioDevice(device or "type=b200")
                    try:
                        out = elrs_plugin.sweep_phy(radius, srate=srate, gain=gain,
                                                    dwell_ms=dwell_ms, channels=channels,
                                                    freq0=freq0, spacing=spacing, uid=uid,
                                                    output_dir=out_dir, progress=_progress)
                    finally:
                        del radius   # release the UHD device even on the error path
                results = out["channels"]
                g = [r for r in results if r["phy"] == "gfsk"]
                l = [r for r in results if r["phy"] == "lora"]
                unk = [r for r in results if r["phy"] == "unknown_energy"]
                interp = []
                if g:
                    interp.append(f"GFSK/FLRC link on channel(s) {[r['channel'] for r in g]} — decode with the GFSK branch (rate_index 0-3).")
                if l:
                    interp.append(f"LoRa link on channel(s) {[r['channel'] for r in l]} (rate {l[0].get('lora_rate')}) — decode with the LoRa branch.")
                if unk:
                    interp.append(f"Unidentified RF on channel(s) {[r['channel'] for r in unk]} — energy present, neither LoRa nor known-UID GFSK.")
                if not results:
                    interp.append("Nothing found — is the drone's ELRS TX on? On ISM2400?")
                interp.append(f"Saved {out.get('saved_files', 0)} raw-IQ file(s) + manifest to: {out.get('saved_dir') or '(not saved)'}")
                _sse({"type": "result", "channels": results, "interpretation": interp,
                      "srate": srate, "uid": (uid.hex() if uid else None),
                      "saved_dir": out.get("saved_dir"), "saved_files": out.get("saved_files", 0)})
            except Exception as e:
                _sse({"type": "error", "error": str(e)})
            return None
        if u.path == "/api/elrs/fhss":
            # Per-link IDENTIFIER from the FHSS hop pattern — no payload decode,
            # so it is unaffected by the SX1280 CR_LI_4_8 long-interleaving wall
            # that blocks reading the UID out of a SYNC packet (notes §30).
            # Long-dwells the sync channel (~2440.4 MHz), where the pinned
            # block-start channel yields ABSOLUTE sequence positions.
            # Query: srate, gain, dwell_s, freq, snr_min, device.
            from .protocols import elrs as elrs_plugin
            if not elrs_plugin.core_available():
                return self._send(400, json.dumps({"error": elrs_plugin.core_error()}))
            q = parse_qs(u.query, keep_blank_values=True)
            try:
                srate = float(q.get("srate", ["10000000"])[0])
                gain = float(q.get("gain", ["40"])[0])
                dwell_s = float(q.get("dwell_s", ["12"])[0])
                freq = float(q.get("freq", [str(elrs_plugin.FHSS_SYNC_FREQ)])[0])
                snr_raw = q.get("snr_min", [""])[0]
                snr_min = float(snr_raw) if snr_raw else None
                device = q.get("device", [""])[0] or None
            except ValueError as e:
                return self._send(400, json.dumps({"error": f"bad param: {e}"}))
            if not (1.0 <= dwell_s <= 60.0):
                return self._send(400, json.dumps(
                    {"error": "dwell_s must be 1-60; the 240-hop sequence needs "
                              "~6.4 s to cover one period at 26.66 ms/hop"}))
            import time as _time
            cap_root = Path(__file__).resolve().parents[2] / "captures"
            out_dir = str(cap_root / f"fhss_{_time.strftime('%Y%m%d_%H%M%S')}")
            try:
                from elrs_sdr.radio import RadioDevice
                from . import spectrum as _spectrum, uhd_backend as _uhdb
                # Same single-device serialization as ELRS Find: the B205 is not
                # thread-safe and Inspect RF holds it open via uhd_backend._DEV.
                with _spectrum._DEVICE_LOCK:
                    _uhdb.close()
                    radius = RadioDevice(device or "type=b200")
                    try:
                        out = elrs_plugin.fhss_fingerprint(
                            radius, srate=srate, gain=gain, dwell_s=dwell_s,
                            freq_hz=freq, output_dir=out_dir, snr_min=snr_min)
                    finally:
                        del radius   # release the UHD device on every path
            except Exception as e:
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
            return self._send(200, json.dumps(out, default=str))

        if u.path == "/api/elrs":
            # Modulation-agnostic ELRS "what's there" sweep: classifies each ISM2400
            # channel's PHY (LoRa vs GFSK/FLRC vs unknown) WITHOUT assuming the rate.
            # Query: srate, gain, dwell_ms, channels, freq0, spacing, uid (hex), device.
            from .protocols import elrs as elrs_plugin
            if not elrs_plugin.core_available():
                return self._send(400, json.dumps({"error": elrs_plugin.core_error()}))
            q = parse_qs(u.query, keep_blank_values=True)
            try:
                srate = float(q.get("srate", ["10000000"])[0])
                gain = float(q.get("gain", ["40"])[0])
                dwell_ms = int(q.get("dwell_ms", ["50"])[0])
                channels = int(q.get("channels", ["80"])[0])
                freq0 = float(q.get("freq0", ["2400400000"])[0])
                spacing = float(q.get("spacing", ["1000000"])[0])
                uid_hex = q.get("uid", [""])[0]
                device = q.get("device", [""])[0] or None
                detect = q.get("detect", ["window"])[0]        # window | burst
                strategy = q.get("strategy", ["sweep"])[0]     # sweep | sync | matrix
                sync_dwell_s = float(q.get("dwell_s", ["1.0"])[0])
                sync_srate = float(q.get("sync_srate", ["5000000"])[0])
                max_slices = int(q.get("max_slices", ["200"])[0])
                early_exit = q.get("early_exit", ["1"])[0] not in ("0", "false", "no")
            except ValueError as e:
                return self._send(400, json.dumps({"error": f"bad param: {e}"}))
            uid = bytes.fromhex(uid_hex) if uid_hex else None
            # Save the capture to a timestamped dir so the user has a concrete artifact
            # to upload for offline re-analysis (raw .complex8 per channel + manifest.json).
            import time as _time
            cap_root = Path(__file__).resolve().parents[2] / "captures"
            out_dir = str(cap_root / f"elrs_find_{_time.strftime('%Y%m%d_%H%M%S')}")
            logbuf.log("info", f"ELRS PHY sweep: {channels} ch @ {freq0/1e6:.1f} MHz "
                               f"(spacing {spacing/1e6:.1f} MHz), srate {srate:.3g} -> {out_dir}")
            try:
                from elrs_sdr.radio import RadioDevice
                from . import spectrum as _spectrum, uhd_backend as _uhdb
                # Serialize B205 access with Inspect RF (single, non-thread-safe
                # radio); release any device uhd_backend holds open before claiming it.
                with _spectrum._DEVICE_LOCK:
                    _uhdb.close()
                    radius = RadioDevice(device or "type=b200")
                    try:
                        if strategy == "sync":
                            # secondary scan: long-dwell the ELRS sync channel (ch40,
                            # 2440.4 MHz) + burst-detect — catches FHSS hops a sweep misses
                            out = elrs_plugin.sync_dwell_phy(radius, srate=srate, gain=gain,
                                                             dwell_s=sync_dwell_s, uid=uid,
                                                             output_dir=out_dir,
                                                             sync_srate=sync_srate,
                                                             max_slices=max_slices)
                        elif strategy == "matrix":
                            # redundancy: sweep/window -> sweep/burst -> sync_dwell/burst,
                            # early-exiting on the first GFSK/LoRa hit (fast)
                            out = elrs_plugin.run_scan_matrix(
                                radius, srate=srate, gain=gain, uid=uid,
                                output_root=out_dir, channels=channels,
                                sweep_dwell_ms=dwell_ms, sync_dwell_s=sync_dwell_s,
                                sync_srate=sync_srate, max_slices=max_slices,
                                early_exit=early_exit, progress=_progress)
                        else:
                            out = elrs_plugin.sweep_phy(radius, srate=srate, gain=gain,
                                                        dwell_ms=dwell_ms, channels=channels,
                                                        freq0=freq0, spacing=spacing, uid=uid,
                                                        output_dir=out_dir, detect=detect)
                    finally:
                        del radius   # release the UHD device even on the error path
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))
            # two result shapes: the sweep/sync ({channels:[...], ...}) and the
            # matrix ({strategies:[{name,result}], hit, hit_strategy}). Normalize
            # the matrix to a combined channels list (union across passes, by
            # channel number) + report which pass hit, so the UI table + the
            # "Saved N files" line work the same either way.
            matrix_info = None
            if strategy == "matrix":
                seen = {}
                for s in out["strategies"]:
                    for c in s["result"].get("all_channels", []):
                        # prefer the hit (gfsk/lora) entry for a channel over a none/unknown one
                        cur = seen.get(c.get("channel"))
                        if cur is None or (c.get("phy") in ("gfsk", "lora")
                                           and cur.get("phy") not in ("gfsk", "lora")):
                            seen[c.get("channel")] = c
                results = sorted(seen.values(), key=lambda c: c.get("channel", 0))
                results = [c for c in results if c.get("phy") != "none"]
                matrix_info = {"hit": out.get("hit"),
                               "hit_strategy": out.get("hit_strategy"),
                               "strategies": [s["name"] for s in out["strategies"]]}
            else:
                results = out["channels"]   # channels with energy (the UI table)
            # interpret for the UI
            g = [r for r in results if r["phy"] == "gfsk"]
            l = [r for r in results if r["phy"] == "lora"]
            unk = [r for r in results if r["phy"] == "unknown_energy"]
            interp = []
            if matrix_info and matrix_info["hit"]:
                interp.append(f"Matrix HIT via strategy: {matrix_info['hit_strategy']} "
                              f"(passes run: {', '.join(matrix_info['strategies'])}).")
            if g:
                interp.append(f"GFSK/FLRC link on channel(s) {[r['channel'] for r in g]} "
                              f"— decode with the GFSK branch (rate_index 0-3).")
            if l:
                interp.append(f"LoRa link on channel(s) {[r['channel'] for r in l]} "
                              f"(rate {l[0].get('lora_rate')}) — decode with the LoRa branch.")
            if unk:
                interp.append(f"Unidentified RF on channel(s) {[r['channel'] for r in unk]} "
                              f"— energy present, neither LoRa nor known-UID GFSK.")
            if not results:
                interp.append("Nothing found — is the drone's ELRS TX on? On ISM2400?")
            # tell the UI where the capture was saved (so the user knows what to upload)
            interp.append(f"Saved {out.get('saved_files', 0)} raw-IQ file(s) + manifest to: "
                          f"{out.get('saved_dir') or '(not saved)'}")
            resp = {"channels": results, "interpretation": interp,
                    "srate": srate, "uid": (uid.hex() if uid else None),
                    "saved_dir": out.get("saved_dir"),
                    "saved_files": out.get("saved_files", 0)}
            if matrix_info:
                resp["matrix"] = matrix_info
            return self._send(200, json.dumps(resp))
        if u.path == "/settings":
            return self._send(200, Path(__file__).with_name("settings.html").read_text(), "text/html; charset=utf-8")
        if u.path == "/api/settings/receivers":
            from . import settings
            refresh = parse_qs(u.query).get("refresh", [""])[0] == "1"
            return self._send(200, json.dumps(settings.receivers(force=refresh)))
        if u.path == "/api/settings/monitoring":
            from . import settings
            return self._send(200, json.dumps(settings.monitoring()))
        if u.path == "/api/settings/watchlist":
            from . import settings
            return self._send(200, json.dumps(settings.watchlist()))
        if u.path == "/api/settings/data":
            from . import settings
            return self._send(200, json.dumps(settings.data_export()))
        if u.path == "/api/settings/about":
            from . import settings
            return self._send(200, json.dumps(settings.about(started_at=_START, ollama_ready=_ollama_ok())))
        if u.path == "/api/session":
            from . import monitor
            return self._send(200, json.dumps(monitor.SESSION.status()))
        if u.path == "/api/nodes":
            from .node_dashboard import snapshot
            return self._send(200, json.dumps(snapshot()), headers={"Cache-Control": "no-store"})
        if u.path == "/api/receivers":
            from . import monitor
            refresh = parse_qs(u.query).get("refresh", [""])[0] == "1"
            return self._send(200, json.dumps(monitor.receivers(force=refresh)))
        if u.path == "/api/observations":
            from . import monitor
            since = parse_qs(u.query).get("since", [""])[0]
            try:
                since_s = float(since) if since else None
            except ValueError:
                return self._send(400, json.dumps({"error": "since must be a number of Unix seconds."}))
            return self._send(200, json.dumps(monitor.SESSION.observations(since_s)))
        if u.path.startswith("/api/observations/"):
            from . import monitor
            found = monitor.SESSION.observation(unquote(u.path[len("/api/observations/"):]))
            if found is None:
                return self._send(404, json.dumps({"error": "No observation with that key in this session."}))
            return self._send(200, json.dumps(found))
        if u.path == "/api/diagnostics":
            from . import monitor
            return self._send(200, json.dumps(monitor.SESSION.diagnostics()))
        if u.path == "/api/export":
            from . import monitor
            q = parse_qs(u.query)
            try:
                body, ctype, name = monitor.SESSION.export(q.get("format", ["json"])[0], q.get("key", [None])[0])
            except KeyError:
                return self._send(404, json.dumps({"error": "No observation with that key in this session."}))
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
            return self._send(200, body, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})
        if u.path == "/api/sessions":
            from . import sessions
            return self._send(200, json.dumps(sessions.listing()))
        if u.path.startswith("/api/sessions/"):
            from . import monitor, sessions
            sid, _, tail = u.path[len("/api/sessions/"):].partition("/")
            sid = unquote(sid)                          # split first: an encoded slash stays inside the id and fails it
            if tail not in ("", "export"):
                return self._send(404, json.dumps({"error": "not found"}))
            try:
                record = sessions.load(sid)             # by id only; a saved session is never named by a path
                if record is None:
                    return self._send(404, json.dumps({"error": "No saved session with that id."}))
                if not tail:
                    return self._send(200, json.dumps(record))
                body, ctype, name = monitor.export_record(record, parse_qs(u.query).get("format", ["json"])[0])
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
            return self._send(200, body, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})
        if u.path == "/api/logs":
            return self._send(200, json.dumps({"lines": logbuf.lines()}))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        u = urlparse(self.path)
        data = self._body()
        if u.path not in CHANGING_ROUTES:               # retired and unknown paths stay a plain 404
            return self._send(404, json.dumps({"error": "not found"}))
        if self._refuse_untrusted_change():             # every POST here changes something: settings, a session, a radio lease
            return
        if u.path == "/api/spectrum/acquire":
            from . import spectrum
            r = spectrum.acquire(data.get("owner", ""),
                                 data.get("center", 100_000_000), data.get("span", 2_000_000),
                                 data.get("device"))
            if r.get("ok"):
                logbuf.log("info", f"SDR control acquired by spectrum ({r.get('mode')})")
            return self._send(200, json.dumps(r))
        if u.path == "/api/spectrum/release":
            from . import spectrum
            spectrum.release(data.get("owner", ""))
            logbuf.log("info", "SDR control released")
            return self._send(200, json.dumps({"ok": True}))
        if u.path == "/api/session":
            from . import monitor
            action = data.get("action")
            try:
                if action == "start":
                    st = monitor.SESSION.start(data.get("source", "sample"), data.get("sample", monitor.DEFAULT_SAMPLE))
                    logbuf.log("info", f"monitor session started ({st['source']})")
                elif action == "stop":
                    st = monitor.SESSION.stop()
                    logbuf.log("info", "monitor session stopped")
                else:
                    return self._send(400, json.dumps({"error": "action must be 'start' or 'stop'."}))
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
            return self._send(200, json.dumps(st))
        if u.path == "/api/sessions/delete":
            from . import sessions
            try:
                deleted = sessions.delete(data.get("session_id"))
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
            if deleted:
                logbuf.log("info", "saved session deleted")
            return self._send(200, json.dumps({"deleted": deleted}))
        if u.path == "/api/sessions/clear":
            from . import sessions
            removed = sessions.clear()
            logbuf.log("info", f"session history cleared ({removed})")
            return self._send(200, json.dumps({"removed": removed}))
        if u.path == "/api/monitor/watch":
            from . import monitor
            try:
                r = monitor.SESSION.watch(str(data.get("key", "")))
            except KeyError:
                return self._send(404, json.dumps({"error": "That observation is no longer in this session."}))
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
            logbuf.log("info", f"target add from monitor ({r['type']})")
            return self._send(200, json.dumps(r))
        if u.path == "/api/settings/monitoring":
            from . import settings
            try:
                if "stale_after_s" in data and data["stale_after_s"] is None:
                    raise ValueError("The stale threshold must be a number. No settings were changed.")
                if "lapsed_after_s" in data:            # either or both; validated together, written once
                    r = settings.set_monitoring(stale=data.get("stale_after_s"), lapsed=data["lapsed_after_s"])
                    logbuf.log("info", f"monitoring thresholds set: stale {r['stale_after_s']:g} s, older {r['lapsed_after_s']:g} s")
                else:
                    r = settings.set_stale_after(data.get("stale_after_s"))
                    logbuf.log("info", f"stale threshold set to {r['stale_after_s']:g} s")
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
            return self._send(200, json.dumps(r))
        if u.path == "/api/settings/watchlist/add":
            from . import settings
            try:
                r = settings.add_watch(str(data.get("type", "")), data.get("value", ""), data.get("note", ""))
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
            if r["added"]:
                logbuf.log("info", f"watchlist add ({r['entry']['type']})")
            return self._send(200, json.dumps(r))
        if u.path == "/api/settings/watchlist/remove":
            from . import settings
            r = settings.remove_watch(str(data.get("type", "")), str(data.get("value", "")))
            if r["removed"]:
                logbuf.log("info", f"watchlist remove ({data.get('type')})")
            return self._send(200, json.dumps(r))
        return self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *a):
        pass


def serve(port: int = 8899, host: str = "") -> None:
    logbuf.log("info", f"server start on :{port}")
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        raise SystemExit(f"Port {port} is already in use. Wave Raider may already be running: open "
                         f"http://localhost:{port}/, or add --port 8900 to the serve command to use another port.")
    # Flush so the address shows up right away under Docker, where output isn't a terminal.
    print(f"Wave Raider web UI → http://localhost:{port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Wave Raider stopped.", flush=True)
    finally:
        httpd.server_close()
