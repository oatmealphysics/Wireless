"""
Unified drone-watch web dashboard.

One page, fed by the fusion TrackTable, showing every drone around you merged
across all sources (Remote ID / SDR protocol scan / DJI DroneID) in a single
identity-keyed table. Pure stdlib http.server — no third-party packages, no
build step.

Endpoints:
    GET  /              -> dashboard HTML
    GET  /api/state     -> {summary, drones:[...], window_s}
    POST /api/window    -> {"seconds": 120}   (0 = all time)

Launched by `dronewatch webui` (see cli.py), which wires up the sources the
operator selected and hands this server the shared TrackTable.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .log import get_logger

log = get_logger(__name__)


DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DroneWatch</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0d1117; color: #c9d1d9;
         font: 14px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { padding: 14px 18px; border-bottom: 1px solid #21262d;
           display: flex; align-items: baseline; gap: 18px; flex-wrap: wrap; }
  h1 { font-size: 18px; margin: 0; color: #58a6ff; letter-spacing: .5px; }
  .sub { color: #8b949e; font-size: 12px; }
  .pill { background: #161b22; border: 1px solid #30363d; border-radius: 999px;
          padding: 2px 10px; font-size: 12px; }
  .pill b { color: #e6edf3; }
  main { padding: 14px 18px; }
  .controls { margin-bottom: 12px; display: flex; gap: 10px; align-items: center;
              flex-wrap: wrap; }
  select, button { background: #161b22; color: #c9d1d9; border: 1px solid #30363d;
                   border-radius: 6px; padding: 5px 10px; font: inherit; }
  button { cursor: pointer; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid #21262d;
           white-space: nowrap; }
  th { color: #8b949e; font-weight: 600; font-size: 12px; cursor: pointer;
       position: sticky; top: 0; background: #0d1117; }
  tr:hover td { background: #11151c; }
  .ok { color: #3fb950; } .warn { color: #d29922; } .dim { color: #6e7681; }
  .tag { border-radius: 4px; padding: 1px 7px; font-size: 11px; border: 1px solid; }
  .tag.remote-id { color: #3fb950; border-color: #238636; }
  .tag.dji-droneid { color: #58a6ff; border-color: #1f6feb; }
  .tag.control-link { color: #d29922; border-color: #9e6a03; }
  .tag.video { color: #db61a2; border-color: #ab296a; }
  .mono { font-variant-numeric: tabular-nums; }
  .empty { color: #6e7681; padding: 30px 0; text-align: center; }
  code { color: #e6edf3; }
</style>
</head>
<body>
<header>
  <h1>DroneWatch</h1>
  <span class="sub">unified drone presence &amp; identity &mdash; all sources</span>
  <span class="pill">tracks <b id="n-total">0</b></span>
  <span class="pill">confirmed ID <b id="n-conf" class="ok">0</b></span>
  <span class="pill" id="n-kinds"></span>
</header>
<main>
  <div class="controls">
    <label>window
      <select id="window" onchange="setWindow()">
        <option value="0">all time</option>
        <option value="30">30 s</option>
        <option value="120" selected>2 min</option>
        <option value="600">10 min</option>
      </select>
    </label>
    <button onclick="refresh()">refresh now</button>
    <span class="sub" id="status"></span>
  </div>
  <table>
    <thead><tr>
      <th>status</th><th>kind</th><th>protocol</th><th>identity / serial</th>
      <th>operator</th><th>position</th><th>alt m</th><th>spd m/s</th>
      <th>freq</th><th>rssi</th><th>metric</th><th>age</th><th>last</th><th>n</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="empty" id="empty">no signals yet &mdash; waiting for sources&hellip;</div>
</main>
<script>
let WINDOW = 120;
function setWindow() {
  WINDOW = parseInt(document.getElementById('window').value, 10);
  fetch('/api/window', {method:'POST', body: JSON.stringify({seconds: WINDOW})})
    .then(refresh);
}
function fmtPos(r){ return (r.lat==null||r.lon==null) ? '—'
  : r.lat.toFixed(5)+', '+r.lon.toFixed(5); }
function num(x, d){ return (x==null) ? '—' : (+x).toFixed(d==null?0:d); }
function row(r){
  const st = r.confirmed
    ? '<span class="ok">● CONFIRMED</span>'
    : '<span class="warn">○ unconfirmed</span>';
  return `<tr>
    <td>${st}</td>
    <td><span class="tag ${r.kind}">${r.kind}</span></td>
    <td>${r.protocol}</td>
    <td><code>${r.identity}</code></td>
    <td>${r.operator}</td>
    <td class="mono">${fmtPos(r)}</td>
    <td class="mono">${num(r.altitude_m)}</td>
    <td class="mono">${num(r.speed_ms,1)}</td>
    <td class="mono">${r.freq_mhz==null?'—':r.freq_mhz+' MHz'}</td>
    <td class="mono">${r.rssi_db==null?'—':num(r.rssi_db)+' dB'}</td>
    <td class="dim">${r.metric||''}</td>
    <td class="mono">${num(r.age_s)}s</td>
    <td class="mono dim">${num(r.last_seen_s)}s</td>
    <td class="mono">${r.count}</td>
  </tr>`;
}
function refresh(){
  fetch('/api/state?window='+WINDOW).then(r=>r.json()).then(s=>{
    document.getElementById('n-total').textContent = s.summary.total;
    document.getElementById('n-conf').textContent = s.summary.confirmed;
    const kinds = Object.entries(s.summary.by_kind||{})
      .map(([k,v])=>k+': '+v).join('  ');
    document.getElementById('n-kinds').textContent = kinds;
    document.getElementById('rows').innerHTML = s.drones.map(row).join('');
    document.getElementById('empty').style.display = s.drones.length?'none':'block';
    document.getElementById('status').textContent =
      'updated ' + new Date().toLocaleTimeString();
  }).catch(e=>{
    document.getElementById('status').textContent = 'fetch error: '+e;
  });
}
setInterval(refresh, 1000);
refresh();
</script>
</body>
</html>"""


def make_handler(table, state_ref):
    """Build a request handler bound to the shared TrackTable."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence default access logging
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, DASHBOARD, "text/html; charset=utf-8")
                return
            if path == "/api/state":
                window = state_ref.get("window_s", 0.0)
                q = self.path.split("?", 1)
                if len(q) == 2:
                    for part in q[1].split("&"):
                        if part.startswith("window="):
                            try:
                                window = float(part.split("=", 1)[1])
                            except ValueError:
                                pass
                body = json.dumps({
                    "summary": table.summary(),
                    "drones": table.rows(window_s=window),
                    "window_s": window,
                })
                self._send(200, body)
                return
            self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if self.path != "/api/window":
                self._send(404, json.dumps({"error": "not found"}))
                return
            n = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
                state_ref["window_s"] = max(0.0, float(payload.get("seconds", 0)))
            except (ValueError, TypeError):
                pass
            self._send(200, json.dumps({"window_s": state_ref["window_s"]}))

    return Handler


def serve(table, host: str = "127.0.0.1", port: int = 8090,
          window_s: float = 120.0) -> ThreadingHTTPServer:
    """Start the dashboard server (non-blocking). Returns the server."""
    state_ref = {"window_s": window_s}
    handler = make_handler(table, state_ref)
    httpd = ThreadingHTTPServer((host, port), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    log.info("dashboard at http://%s:%d", host, port)
    return httpd
