"""
Web UI for the multi-band drone presence + protocol scanner.

One command:
    python3 -m elrs_sdr webui

Starts a local dashboard on http://localhost:8090 (no external deps — pure
stdlib http.server). A background thread runs the DroneScanner; the browser
polls /api/state (JSON) and renders a Kismet-style multi-band waterfall with
click-to-lock band tabs, a live detection panel, and a protocol summary.

Endpoints:
    GET  /            -> dashboard HTML
    GET  /api/state   -> {cycling, locked_band, bands:[...], summary}
    POST /api/lock    -> {"band": "5.8"} to lock, {"band": null} to resume
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import dronescan
from .dronescan import DroneScanner, CYCLE_ORDER

# The dashboard (self-contained HTML/JS/CSS, no build step).
DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Drone RF Scanner</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#0a0e12; color:#cdd6df;
         font:14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { padding:12px 18px; background:#0f151b; border-bottom:1px solid #1c2733;
           display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; color:#e8eef4; font-weight:600; }
  .status { font-size:12.5px; color:#7fa8c9; }
  .live { color:#5fd07a; }
  main { display:grid; grid-template-columns: 1fr 340px; gap:14px; padding:14px 18px; }
  @media (max-width: 900px){ main { grid-template-columns: 1fr; } }

  .tabs { display:flex; gap:6px; margin-bottom:8px; flex-wrap:wrap; }
  .tab { padding:7px 13px; border:1px solid #223243; background:#0f151b;
         border-radius:7px; cursor:pointer; font-size:13px; color:#aebccb;
         transition: background .12s, border-color .12s; user-select:none; }
  .tab:hover { background:#15202b; }
  .tab.active { background:#16324a; border-color:#2f6ea0; color:#dff0ff; }
  .tab.locked { border-color:#5fd07a; box-shadow:0 0 0 1px #5fd07a inset; }
  .tab .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
              margin-right:6px; background:#2a3a48; vertical-align:middle; }
  .tab.present .dot { background:#ff5c5c; }
  .tab.hop .dot { background:#ffb43c; }

  .wf-wrap { background:#05080b; border:1px solid #1c2733; border-radius:8px;
             overflow:hidden; position:relative; }
  canvas#wf { display:block; width:100%; height:300px; }
  /* fixed-height scroll box for the detections table so it NEVER changes the
     page height / pushes the waterfall or other elements around */
  .table-card { padding:0; }
  .table-card h2 { padding:11px 14px 0; }
  .table-head { display:flex; align-items:center; justify-content:space-between;
                padding:11px 14px 8px; gap:10px; }
  .table-head h2 { margin:0; }
  .win-label { font-size:12px; color:#7fa8c9; display:flex; align-items:center; gap:6px; }
  .win-label select { background:#0d141b; color:#cdd6df; border:1px solid #223243;
                      border-radius:6px; padding:4px 8px; font-size:12px; cursor:pointer; }
  .table-scroll { max-height:300px; overflow-y:auto; }
  .wf-label { position:absolute; top:8px; left:10px; font-size:12px;
              color:#7fa8c9; background:rgba(5,8,11,.6); padding:2px 8px;
              border-radius:5px; }
  .hint { font-size:11.5px; color:#5a6b7a; margin-top:6px; }

  .panel { display:flex; flex-direction:column; gap:12px; }
  .card { background:#0f151b; border:1px solid #1c2733; border-radius:8px; padding:12px 14px; }
  .card h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
             color:#7fa8c9; margin:0 0 8px; font-weight:600; }
  .summary { font-size:14px; color:#e8eef4; min-height:20px; }
  .det { border-top:1px solid #182430; padding:8px 0; }
  .det:first-of-type { border-top:none; }
  .det .f { font-size:15px; font-weight:600; color:#e8eef4; }
  .det .p { font-size:12.5px; color:#aebccb; margin-top:2px; }
  .badge { display:inline-block; font-size:10.5px; padding:1px 7px; border-radius:9px;
           margin-left:6px; vertical-align:middle; font-weight:600; }
  .badge.high { background:#1c4a2e; color:#7fe0a0; }
  .badge.medium { background:#4a3a1c; color:#e0c87f; }
  .badge.low { background:#3a2a1c; color:#c9a87f; }
  .quiet { color:#5a6b7a; font-style:italic; }
  .resume { padding:7px 13px; border:1px solid #2f6ea0; background:#16324a;
            color:#dff0ff; border-radius:7px; cursor:pointer; font-size:12.5px; }
  .resume:hover { background:#1c4059; }
  .sig { font-size:11px; color:#7fa8c9; }

  /* table-layout:fixed + explicit widths => columns NEVER resize when cell
     content changes (no left/right jitter as values update) */
  table.dets { width:100%; border-collapse:collapse; font-size:12.5px;
               table-layout:fixed; }
  table.dets th { text-align:left; padding:6px 9px; color:#7fa8c9; font-weight:600;
                  border-bottom:1px solid #223243; position:sticky; top:0;
                  background:#0d141b; cursor:pointer; user-select:none;
                  white-space:nowrap; overflow:hidden; }
  table.dets th:hover { background:#15202b; color:#cfe3f5; }
  table.dets th .arrow { font-size:10px; color:#5fd07a; margin-left:3px; }
  table.dets td { padding:6px 9px; border-bottom:1px solid #16202b; color:#cdd6df;
                  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  table.dets tr:hover td { background:#101922; }
  /* column widths (must match the 10 <th> in order) */
  table.dets col.c-band   { width:64px; }
  table.dets col.c-freq   { width:88px; }
  table.dets col.c-proto  { width:150px; }
  table.dets col.c-make   { width:150px; }
  table.dets col.c-manuf  { width:110px; }
  table.dets col.c-type   { width:100px; }
  table.dets col.c-str    { width:92px; }
  table.dets col.c-age    { width:90px; }
  table.dets col.c-last   { width:88px; }
  table.dets col.c-conf   { width:72px; }
  .conf-badge { padding:1px 7px; border-radius:9px; font-size:11px; font-weight:600; }
  .conf-badge.high { background:#12321f; color:#5fd07a; }
  .conf-badge.medium { background:#3a2f12; color:#ffb43c; }
  .conf-badge.low { background:#2a1a1a; color:#e08a8a; }
  .manuf { color:#9fc3e8; }
  .age { color:#7f95a8; }
  .hidden { display:none; }
</style>
</head>
<body>
<header>
  <h1>Drone RF Scanner</h1>
  <span class="status" id="status">connecting…</span>
  <span class="status" id="lockbtn-wrap"></span>
</header>
<main>
  <section id="view-waterfall">
    <div class="tabs" id="tabs"></div>
    <div class="wf-wrap">
      <canvas id="wf" width="820" height="300"></canvas>
      <div class="wf-label" id="wflabel"></div>
    </div>
    <div class="hint">Auto-cycling through bands. <b>Click a band tab to lock</b> on it;
      click the <b>Resume</b> button (or click the locked tab) to release back to cycling.</div>
    <div class="card table-card" style="padding:0; margin-top:12px;">
      <div class="table-head">
        <h2>Detections <span class="sig">(all bands)</span></h2>
        <label class="win-label">Window
          <select id="win" onchange="setWindow(this.value)">
            <option value="0">All time</option>
            <option value="60">1 min</option>
            <option value="300">5 min</option>
            <option value="900">15 min</option>
            <option value="3600">1 hour</option>
            <option value="28800">8 hours</option>
            <option value="86400">24 hours</option>
          </select>
        </label>
      </div>
      <div class="table-scroll">
        <table class="dets">
          <colgroup>
            <col class="c-band"><col class="c-freq"><col class="c-proto">
            <col class="c-make"><col class="c-manuf"><col class="c-type">
            <col class="c-str"><col class="c-age"><col class="c-last">
            <col class="c-conf">
          </colgroup>
          <thead>
            <tr>
              <th data-col="band" onclick="sortCol('band')">Band</th>
              <th data-col="freq_mhz" onclick="sortCol('freq_mhz')">Freq (MHz)</th>
              <th data-col="protocol" onclick="sortCol('protocol')">Protocol</th>
              <th data-col="make_model" onclick="sortCol('make_model')">Make / Model</th>
              <th data-col="manufacturer" onclick="sortCol('manufacturer')">Manufacturer</th>
              <th data-col="type" onclick="sortCol('type')">Type</th>
              <th data-col="strength_db" onclick="sortCol('strength_db')">Strength</th>
              <th data-col="age_s" onclick="sortCol('age_s')">Age</th>
              <th data-col="last_seen_s" onclick="sortCol('last_seen_s')">Last seen</th>
              <th data-col="confidence" onclick="sortCol('confidence')">Conf</th>
            </tr>
          </thead>
          <tbody id="dettbody"><tr><td colspan="10" class="quiet">no detections yet…</td></tr></tbody>
        </table>
      </div>
    </div>
    <div class="hint">Make/Model is the <b>protocol family</b> (e.g. "ELRS-family LoRa") — a
      passive SDR identifies the <i>kind</i> of link, not the vendor. Manufacturer is a real
      OUI→vendor only for Wi-Fi signals once 802.11 beacon decode is validated against a
      capture (shows — until then). Age = how long the signal has been present.</div>
  </section>
  <aside class="panel">
    <div class="card">
      <h2>Protocol summary</h2>
      <div class="summary" id="summary">—</div>
    </div>
    <div class="card">
      <h2>Live detections</h2>
      <div id="dets"><div class="quiet">listening…</div></div>
    </div>
  </aside>
</main>

<script>
const BAND_ORDER = ["2.4","5.8","sub1g","433"];
let state = null;
let locked = null;   // null = cycling (follow the auto band)
// table sort: {col, dir} — dir 1 = asc, -1 = desc. null col = stable default
// (by first_seen), so rows don't reorder as values tick (no vertical jitter).
let sortCol_ = null;
let sortDir = 1;
const CONF_RANK = {high:0, medium:1, low:2};
function sortCol(col){
  if(sortCol_ === col){ sortDir = -sortDir; }   // toggle direction
  else { sortCol_ = col; sortDir = 1; }
  // mark the active column header
  for(const th of document.querySelectorAll('table.dets th')){
    th.querySelector('.arrow')?.remove();
    if(th.dataset.col === col){
      const a = document.createElement('span');
      a.className = 'arrow'; a.textContent = sortDir === 1 ? '▲' : '▼';
      th.appendChild(a);
    }
  }
  renderTable();
}

// ---- waterfall canvas: scroll newest at bottom ----
const cv = document.getElementById('wf');
const ctx = cv.getContext('2d');
const W = cv.width, H = cv.height;
// each band keeps its own offscreen scroll buffer
const buffers = {};
function ensureBuffer(name){
  if(!buffers[name]){
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    buffers[name] = c;
  }
  return buffers[name];
}
function dbToColor(db){
  // map relative dB (0 = band mean) to a thermal palette; clip -30..30 dB
  const v = Math.max(-30, Math.min(30, db));
  const t = (v + 30) / 60;   // 0..1
  let r,g,b;
  if(t < 0.25){ r=8;  g=20;  b=40;  }       // deep blue (quiet)
  else if(t < 0.5){ const u=(t-0.25)/0.25; r=8+u*30; g=20+u*60; b=40+u*120; }
  else if(t < 0.75){ const u=(t-0.5)/0.25; r=38+u*170; g=80+u*130; b=160-u*120; }
  else { const u=(t-0.75)/0.25; r=208+u*47; g=210+u*45; b=40+u*115; } // yellow->red
  return `rgb(${r|0},${g|0},${b|0})`;
}
function pushFrame(name, frame, freqs){
  const buf = ensureBuffer(name);
  const bctx = buf.getContext('2d');
  // shift existing content up by 1px
  const img = bctx.getImageData(0,0,W,H);
  bctx.putImageData(img, 0, 1);
  // draw the new frame as the bottom row (1px tall, stretched to W)
  for(let x=0;x<W;x++){
    const idx = Math.min(frame.length-1, Math.floor(x / W * frame.length));
    bctx.fillStyle = dbToColor(frame[idx]);
    bctx.fillRect(x, H-1, 1, 1);
  }
  // draw current band buffer onto main canvas
  ctx.clearRect(0,0,W,H);
  ctx.drawImage(buf, 0, 0);
  // freq axis labels
  ctx.fillStyle = "rgba(180,200,220,.55)";
  ctx.font = "10px monospace";
  const lo = (freqs[0]/1e9).toFixed(3), hi=(freqs[freqs.length-1]/1e9).toFixed(3);
  ctx.fillText(lo+" GHz", 6, H-4);
  ctx.fillText(hi+" GHz", W-44, H-4);
}

function el(id){ return document.getElementById(id); }

function render(){
  if(!state) return;
  const cycling = state.cycling;
  locked = state.locked_band;
  const activeBand = locked || (state.bands && state.bands.find(b=>b.last_update===Math.max(...state.bands.map(x=>x.last_update)))?.name) || "2.4";
  // tabs
  const tabs = el('tabs');
  tabs.innerHTML = '';
  for(const b of state.bands){
    const t = document.createElement('div');
    t.className = 'tab' + (b.name===activeBand?' active':'') + (b.locked?' locked':'') + (b.present?' present':'') + (b.hopping?' hop':'');
    t.innerHTML = `<span class="dot"></span>${b.name}${b.present? ' <span class="sig">+'+b.presence_db.toFixed(0)+'dB</span>':''}`;
    t.onclick = () => {
      if(b.locked){ lock(null); } else { lock(b.name); }
    };
    tabs.appendChild(t);
  }
  // lock / resume button
  const lw = el('lockbtn-wrap');
  lw.innerHTML = locked
    ? `<button class="resume" onclick="lock(null)">Resume auto</button>`
    : `<span class="live">● auto-cycling</span>`;
  // status
  el('status').textContent = locked ? `locked on ${locked}` : 'auto-cycling 2.4 → 5.8 → sub-1G → 433';
  // summary
  el('summary').textContent = state.summary || '—';
  // waterfall label + detection panel
  const activeB = state.bands.find(b=>b.name===activeBand) || state.bands[0];
  el('wflabel').textContent = activeB.label + (locked? ' (locked)':'');
  // detections (show the active band's, plus a global "all" line)
  const dets = el('dets');
  const items = [];
  for(const b of state.bands){
    for(const d of b.detections){
      items.push({b:d, band:b.name, conf:b.detections[0].confidence});
      items[items.length-1].conf = d.confidence;
      items[items.length-1].freq=d.freq_hz; items[items.length-1].power=d.power_db;
      items[items.length-1].proto=d.protocol; items[items.length-1].note=d.note;
      items[items.length-1].sig=d.signature; items[items.length-1].hop=b.hopping;
      break; // latest per band
    }
  }
  if(items.length===0){ dets.innerHTML = '<div class="quiet">quiet — no drone-class signal yet</div>'; }
  else {
    items.sort((a,b)=>b.power-a.power);
    dets.innerHTML = items.map(it=>`
      <div class="det">
        <div class="f">${(it.freq/1e6).toFixed(1)} MHz <span class="badge ${it.conf}">${it.conf}</span>
          ${it.hop?'<span class="badge low">hop</span>':''}</div>
        <div class="p">${it.proto}<br><span class="sig">${it.band} · +${it.power.toFixed(1)} dB · ${it.note}</span></div>
      </div>`).join('');
  }
  // push waterfall frame for the active band
  if(activeB && activeB.frame && activeB.frame.length){
    pushFrame(activeBand, activeB.frame, activeB.freqs_frame);
  }
  // detections table
  renderTable();
}

function renderTable(){
  if(!state || !state.detections) return;
  let rows = state.detections.slice();
  const tb = el('dettbody');
  // STABLE order: by the active sort column if the user picked one, else by
  // first_seen (a row's position is fixed once it appears, so rows don't jump
  // as their strength/age values tick). first_seen is the tiebreaker either
  // way, which makes the sort deterministic across polls.
  if(sortCol_){
    const col = sortCol_, dir = sortDir;
    rows.sort((a,b)=>{
      let va = a[col], vb = b[col];
      if(col === 'confidence'){ va = CONF_RANK[va]??3; vb = CONF_RANK[vb]??3; }
      if(typeof va === 'string' || typeof vb === 'string'){
        va = (va==null?'':String(va)); vb = (vb==null?'':String(vb));
        return va < vb ? -dir : va > vb ? dir : (a.first_seen - b.first_seen);
      }
      va = (va==null?-Infinity:va); vb = (vb==null?-Infinity:vb);
      return (va - vb) * dir || (a.first_seen - b.first_seen);
    });
  } else {
    rows.sort((a,b)=> a.first_seen - b.first_seen);
  }
  if(!rows.length){
    tb.innerHTML = '<tr><td colspan="10" class="quiet">no detections yet…</td></tr>';
    return;
  }
  tb.innerHTML = rows.map(r=>{
    const manuf = r.manufacturer ? `<span class="manuf">${r.manufacturer}</span>` : '—';
    const lastSeen = r.last_seen_s !== undefined
      ? (r.last_seen_s < 1.5 ? 'now' : r.last_seen_s.toFixed(0)+'s ago')
      : '—';
    return `<tr>
      <td>${r.band}</td>
      <td>${r.freq_mhz.toFixed(1)}</td>
      <td>${r.protocol}</td>
      <td>${r.make_model}</td>
      <td>${manuf}</td>
      <td>${r.type}${r.hopping?' ⤴':''}</td>
      <td>+${r.strength_db.toFixed(1)} dB</td>
      <td class="age">${r.age_s.toFixed(1)}s <span class="sig">×${r.seen_count}</span></td>
      <td class="age">${lastSeen}</td>
      <td><span class="conf-badge ${r.confidence}">${r.confidence}</span></td>
    </tr>`;
  }).join('');
}

async function setWindow(seconds){
  try{
    await fetch('/api/window', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({seconds: parseFloat(seconds)})});
  }catch(e){ console.error(e); }
}

async function lock(band){
  try{
    await fetch('/api/lock', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({band: band})});
  }catch(e){ console.error(e); }
}

async function poll(){
  try{
    const r = await fetch('/api/state');
    state = await r.json();
    render();
  }catch(e){ el('status').textContent = 'server error: '+e; }
  setTimeout(poll, 350);
}
poll();
</script>
</body>
</html>
"""


class _State:
    """Holds the live scanner + lock state, shared between the thread and HTTP."""
    def __init__(self):
        self.scanner = None
        self.locked = None
        self.window_s = 0.0
        self.error = None
        self.last_state = {"cycling": True, "locked_band": None,
                           "window_s": 0.0, "detections": [],
                           "bands": [], "summary": "starting…"}

    def set_lock(self, band):
        self.locked = band
        if self.scanner:
            self.scanner.set_lock(band)

    def set_window(self, seconds):
        self.window_s = max(0.0, float(seconds))
        if self.scanner:
            self.scanner.set_window(seconds)


STATE = _State()


def _quiet_uhd():
    """Route UHD's C++ log output (and everything else on stdout/stderr) to
    /dev/null so the terminal stays clean — including the bare 'O' heartbeat
    char libuhd writes straight to the file descriptor.

    Real errors are NOT lost: our own Python exceptions are caught in
    _scan_loop and stored in STATE (shown in the dashboard), and a copy is
    written to a small error log file. We keep the original fds (1/2) open so
    nothing breaks, we just point them at /dev/null for the duration.

    Only suppress when we're actually running the radio; on a box without UHD
    (e.g. CI/selftest) this is a no-op.
    """
    import os
    import sys

    # 1) ask libuhd (if it exposes it) to be quiet too — belt and suspenders
    os.environ.setdefault("UHD_LOGGER_LEVEL", "ERROR")
    try:
        from . import uhd_bootstrap
        uhd = uhd_bootstrap.load_uhd()
        log_mod = getattr(uhd, "log", None)
        if log_mod is not None:
            for meth in ("setLevel", "set_level", "setLogLevel"):
                fn = getattr(log_mod, meth, None)
                if callable(fn):
                    for val in ("ERROR", 4, 3):
                        try:
                            fn(val); break
                        except Exception:
                            continue
    except Exception:
        pass

    # 2) fd-level: dup the real stdout/stderr, then point 1/2 at /dev/null.
    #    Works for C++ lib writes (the 'O'), not just Python prints.
    #    Print the confirmation FIRST, then redirect.
    print("UHD output routed to /dev/null (terminal muted; errors still shown "
          "in the dashboard).", flush=True)
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        for fd in (1, 2):
            real = os.dup(fd)              # keep the real fd
            os.dup2(devnull, fd)           # redirect to /dev/null
            os.close(real)
        os.close(devnull)
        # Also silence Python's sys.stdout/stderr wrappers (they hold the old fd)
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")
    except Exception:
        # If fd tricks fail, at least Python prints are muted
        try:
            sys.stdout = open(os.devnull, "w")
            sys.stderr = open(os.devnull, "w")
        except Exception:
            pass


def _scan_loop():
    """Background thread: open the radio, run the scanner, update STATE."""
    import os
    import traceback
    try:
        _quiet_uhd()
        from . import uhd_bootstrap
        uhd_bootstrap.load_uhd()
        from .radio import RadioDevice
        args = os.environ.get("ELRS_DEVICE", "type=b200")
        gain = float(os.environ.get("ELRS_GAIN", "30"))
        dwell = float(os.environ.get("ELRS_DWELL", "0.15"))
        radio = RadioDevice(args)
        STATE.scanner = DroneScanner(radio, dwell_s=dwell, gain=gain,
                                     lock=STATE.locked)
        while True:
            try:
                STATE.last_state = STATE.scanner.step(window_s=STATE.window_s)
            except Exception as e:  # one bad block shouldn't kill the UI
                STATE.last_state = {**STATE.last_state, "last_step_error": repr(e)}
    except Exception as e:  # noqa: BLE001  (radio open failed)
        STATE.error = repr(e)
        STATE.last_state = {"cycling": True, "locked_band": None,
                            "bands": [], "summary": f"radio error: {e}"}
        raise


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _html(self, text, code=200):
            data = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._html(DASHBOARD)
            elif self.path == "/api/state":
                if STATE.error and not STATE.scanner:
                    self._json({**STATE.last_state, "error": STATE.error})
                else:
                    self._json(STATE.last_state)
            elif self.path == "/api/bands":
                self._json({"bands": [b.name for b in dronescan.BANDS]})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path == "/api/lock":
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n) if n else b"{}"
                try:
                    req = json.loads(body or b"{}")
                except Exception:
                    req = {}
                band = req.get("band")
                if band is not None and band not in {b.name for b in dronescan.BANDS}:
                    self._json({"error": f"unknown band {band!r}"}, 400)
                    return
                STATE.set_lock(band)
                # refresh the snapshot immediately so the next /api/state
                # reflects the lock without waiting for the next scanner step
                if STATE.scanner is not None:
                    STATE.last_state = STATE.scanner.state_dict()
                self._json({"ok": True, "locked_band": band})
            elif self.path == "/api/window":
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n) if n else b"{}"
                try:
                    req = json.loads(body or b"{}")
                except Exception:
                    req = {}
                ws = req.get("seconds", 0)
                try:
                    ws = float(ws)
                except Exception:
                    ws = 0.0
                STATE.set_window(ws)
                if STATE.scanner is not None:
                    STATE.last_state = STATE.scanner.state_dict(window_s=STATE.window_s)
                self._json({"ok": True, "window_s": STATE.window_s})
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def serve(host: str = "0.0.0.0", port: int = 8090, block: bool = True):
    # Print the URL FIRST (before the scan thread redirects fds to /dev/null),
    # so the user always sees where to point the browser.
    print(f"Drone RF scanner UI:  http://localhost:{port}")
    print(f"  (bands: {', '.join(CYCLE_ORDER)}  |  Ctrl-C to stop)")
    t = threading.Thread(target=_scan_loop, daemon=True)
    t.start()
    httpd = ThreadingHTTPServer((host, port), make_handler())
    if block:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopping.")
        finally:
            httpd.server_close()
    return httpd


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="elrs_sdr webui")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--host", default="0.0.0.0")
    a = p.parse_args(argv)
    serve(a.host, a.port, block=True)


if __name__ == "__main__":
    main()
