#!/usr/bin/env python3
"""odr_web.py -- Open Drone ID capture with a live Web UI.

Starts an ODID capture (see odr_detect.py for transports) and a small
zero-dependency web server (Python stdlib http.server) that shows, in a
browser, every drone being seen right now:

  * identity   -- serial number / CAA registration / UUID, UA type
  * position   -- lat/lon, geo + baro altitude, height over ground/takeoff
  * kinematics -- direction, horizontal + vertical speed
  * operator   -- operator ID, operator location, area radius/ceiling/floor
  * status     -- ground/airborne/emergency, last message counter, RSSI
  * per-drone message history and a live event feed

No third-party packages. Run:

    sudo python3 odr_web.py --port 10000                 # BLE via btmon
    sudo python3 odr_web.py --port 10000 --source btmon -f cap.txt
    python3 odr_web.py --port 10000 --source pcap -f wifi.pcap
    python3 odr_web.py --port 10000 --source replay      # demo data, no radio

then open http://localhost:10000

Drone identity for the UI: a drone is keyed by its Basic ID (serial /
registration / UUID) when present, else by source MAC (Wi-Fi), else by a
hash of the last position. This matches how a human tells two drones apart.

The jsonl sink from odr_detect.py is reused, so every event is also
persisted to disk (default odr_web_<ts>.jsonl).
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import odr_detect as od  # noqa: E402

# A drone is considered "lost" after this many seconds without a packet.
DRONE_TIMEOUT_S = 20.0
# Keep this many recent events in the live feed + per-drone history.
MAX_FEED = 400
MAX_DRONE_EVENTS = 200


# ---------------------------------------------------------------------------
# Drone state
# ---------------------------------------------------------------------------

def _msg_list(decoded):
    """Return the list of single messages in a decoded event (unwraps PACK)."""
    if decoded is None:
        return []
    if "messages" in decoded:
        return decoded["messages"]
    return [decoded]


def drone_key(event):
    d = event["decoded"]
    for m in _msg_list(d):
        if m and m.get("type") == "BASIC_ID" and m.get("uas_id"):
            return "id:" + m["uas_id"]
    if event.get("addr"):
        return "mac:" + event["addr"]
    for m in _msg_list(d):
        if m and m.get("type") == "LOCATION" and \
                abs(m.get("latitude", 0)) > 1e-9:
            return "pos:" + hashlib.sha1(
                ("%f,%f" % (m["latitude"], m["longitude"])).encode()).hexdigest()[:12]
    return "anon:" + hashlib.sha1(event["raw_hex"].encode()).hexdigest()[:12]


class Drone:
    def __init__(self, key, event):
        self.key = key
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.frames = 0
        self.identity = None       # dict: id_type, uas_id, ua_type
        self.location = None       # dict
        self.system = None
        self.operator = None
        self.selfid = None
        self.auth_pages = 0
        self.transports = set()
        self.addr = None
        self.rssi = None
        self.events = []           # recent per-drone decoded messages

    def update(self, event):
        self.last_seen = time.time()
        self.frames += 1
        self.transports.add(event["transport"])
        if event.get("addr"):
            self.addr = event["addr"]
        if event.get("rssi") is not None:
            self.rssi = event["rssi"]
        for m in _msg_list(event["decoded"]):
            if not m:
                continue
            self.events.append({"ts": event["ts"], "m": m})
            t = m.get("type")
            if t == "BASIC_ID":
                self.identity = m
            elif t == "LOCATION":
                self.location = m
            elif t == "SYSTEM":
                self.system = m
            elif t == "OPERATOR_ID":
                self.operator = m
            elif t == "SELF_ID":
                self.selfid = m
            elif t == "AUTH":
                self.auth_pages = max(self.auth_pages, m.get("page", 0) + 1)
        self.events = self.events[-MAX_DRONE_EVENTS:]

    @property
    def age_s(self):
        return time.time() - self.last_seen

    def to_json(self):
        return {
            "key": self.key,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "age_s": round(self.age_s, 1),
            "frames": self.frames,
            "transports": sorted(self.transports),
            "addr": self.addr,
            "rssi": self.rssi,
            "identity": self.identity,
            "location": self.location,
            "system": self.system,
            "operator": self.operator,
            "selfid": self.selfid,
            "auth_pages": self.auth_pages,
        }


class ElrsLink:
    """A decoded ExpressLRS control/telemetry link, keyed by CRC init (which
    encodes the bind UID low byte ^ OTA version). Distinct from an ODID drone:
    it is an RF link, not a regulatory ID broadcast."""

    TIMEOUT_S = 15.0

    def __init__(self, key, pkt):
        self.key = key
        self.first_seen = pkt.get("t", time.time())
        self.last_seen = pkt.get("t", time.time())
        self.packets = 0
        self.uid = pkt.get("uid")
        self.ota_versions = set()
        self.sfs = set()
        self.types = set()
        self.inits = set()
        self.sizes = set()
        self.last_hex = None
        self._update(pkt)

    def _update(self, pkt):
        self.last_seen = pkt.get("t", self.last_seen)
        self.packets += 1
        if pkt.get("uid"):
            self.uid = pkt["uid"]
        if pkt.get("ov") is not None:
            self.ota_versions.add(pkt["ov"])
        if pkt.get("sf") is not None:
            self.sfs.add(pkt["sf"])
        if pkt.get("type") is not None:
            self.types.add(pkt["type"])
        if pkt.get("init"):
            self.inits.add(pkt["init"])
        if pkt.get("size") is not None:
            self.sizes.add(pkt["size"])
        if pkt.get("hex"):
            self.last_hex = pkt["hex"]

    def to_json(self):
        return {
            "key": self.key,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "age_s": round(time.time() - self.last_seen, 1),
            "packets": self.packets,
            "uid": self.uid,
            "ota_versions": sorted(self.ota_versions),
            "sfs": sorted(self.sfs),
            "pkt_types": sorted(self.types),
            "inits": sorted(self.inits),
            "sizes": sorted(self.sizes),
            "last_hex": self.last_hex,
        }


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.drones = {}
        self.links = {}
        self.feed = []
        self.started = time.time()
        self.total_events = 0
        self.total_link_pkts = 0

    def add_event(self, event):
        key = drone_key(event)
        with self.lock:
            self.total_events += 1
            d = self.drones.get(key)
            if d is None:
                d = Drone(key, event)
                self.drones[key] = d
            else:
                d.update(event)
            self.feed.append({
                "ts": event["ts"], "key": key, "transport": event["transport"],
                "addr": event["addr"], "counter": event["counter"],
                "types": [m.get("type") for m in _msg_list(event["decoded"]) if m],
            })
            self.feed = self.feed[-MAX_FEED:]

    def add_link(self, pkt):
        """pkt: one decoded ELRS packet dict (from elrs_live --pkts jsonl).
        Keyed by init (== UID-derived CRC init, stable per link)."""
        key = "elrs:" + str(pkt.get("init") or "unknown")
        with self.lock:
            self.total_link_pkts += 1
            l = self.links.get(key)
            if l is None:
                l = ElrsLink(key, pkt)
                self.links[key] = l
            else:
                l._update(pkt)
            self.feed.append({
                "ts": _dt.datetime.fromtimestamp(pkt.get("t", time.time())).isoformat(timespec="milliseconds"),
                "key": key, "transport": "ELRS", "addr": None,
                "counter": None,
                "types": ["ELRS sf=%s type=%s" % (pkt.get("sf"), pkt.get("type"))],
            })
            self.feed = self.feed[-MAX_FEED:]

    def snapshot(self):
        now = time.time()
        with self.lock:
            drones = [d.to_json() for d in self.drones.values()
                      if now - d.last_seen < DRONE_TIMEOUT_S]
            drones.sort(key=lambda x: -x["last_seen"])
            links = [l.to_json() for l in self.links.values()
                     if now - l.last_seen < ElrsLink.TIMEOUT_S]
            links.sort(key=lambda x: -x["last_seen"])
            return {
                "started": self.started,
                "now": now,
                "total_events": self.total_events,
                "total_link_pkts": self.total_link_pkts,
                "active_drones": len(drones),
                "known_drones": len(self.drones),
                "active_links": len(links),
                "drones": drones,
                "links": links,
                "feed": list(reversed(self.feed)),
            }


# ---------------------------------------------------------------------------
# Capture thread
# ---------------------------------------------------------------------------

def capture_loop(tracker, source, path, jsonl):
    try:
        for frame, meta in od.iter_source(source, path):
            ev = od.decoded_event(frame, meta)
            if ev is None:
                continue
            tracker.add_event(ev)
            if jsonl:
                with open(jsonl, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev, separators=(",", ":")) + "\n")
    except Exception as e:  # keep the web server alive even if capture dies
        print("capture stopped: %r" % (e,), file=sys.stderr)


def replay_loop(tracker, jsonl, interval=0.4):
    """Generate synthetic ODID traffic so the UI can be exercised without a
    radio. Builds a real BLE advertising frame per tick with a moving
    position -- the same code path as live capture."""
    import struct
    lat0, lon0 = 48.8566, 2.3522
    i = 0
    while True:
        i += 1
        lat = lat0 + (i * 0.00001)
        lon = lon0 + (i * 0.00001)
        alt = 120.0 + (i % 40)

        basic = bytearray(od.ODID_MESSAGE_SIZE)
        basic[0] = 0x02
        basic[1] = (1 << 4) | 2                      # serial, multicopter
        basic[2:22] = b"REPLAY-DRONE-01".ljust(20, b"\x00")

        loc = bytearray(od.ODID_MESSAGE_SIZE)
        loc[0] = 0x12
        loc[1] = 2 << 4                               # airborne
        loc[2] = i % 360
        loc[3] = min(255, int(10 / 0.25))
        loc[4] = 2                                    # +1.0 m/s
        loc[6:10] = struct.pack("<i", int(lat * od.LATLON_MULT))
        loc[10:14] = struct.pack("<i", int(lon * od.LATLON_MULT))
        loc[14:16] = struct.pack("<H", int((alt + 1000) / 0.5))
        loc[16:18] = struct.pack("<H", int((alt + 1000) / 0.5))
        loc[18:20] = struct.pack("<H", int((40 + 1000) / 0.5))  # height over takeoff: 40 m
        loc[19] = 0x2B
        loc[20] = 0x03
        loc[21:23] = struct.pack("<H", (i * 7) % 3600)
        loc[23] = 0x05
        sysm = bytearray(od.ODID_MESSAGE_SIZE)
        sysm[0] = 0x42
        sysm[1] = 0 << 2                              # op loc: takeoff
        sysm[2:6] = struct.pack("<i", int(lat0 * od.LATLON_MULT))
        sysm[6:10] = struct.pack("<i", int(lon0 * od.LATLON_MULT))
        sysm[10:12] = struct.pack("<H", 1)
        sysm[12] = 5                                  # 50 m radius
        sysm[13:15] = struct.pack("<H", int((150 + 1000) / 0.5))
        sysm[15:17] = struct.pack("<H", int((10 + 1000) / 0.5))
        sysm[18:20] = struct.pack("<H", int((5 + 1000) / 0.5))

        pack = bytes([0xF2, od.ODID_MESSAGE_SIZE, 3]) + bytes(basic) + \
            bytes(loc) + bytes(sysm)
        adv_val = od.BLE_SERVICE_UUID_LE + bytes([od.BLE_AD_CODE_ODID, i % 256]) + pack
        adv = bytes([len(adv_val) + 1, od.AD_TYPE_SERVICE_DATA_16]) + adv_val

        ev = od.decoded_event(adv, {"src": "replay", "rssi": -50 - (i % 20)})
        if ev:
            tracker.add_event(ev)
            if jsonl:
                with open(jsonl, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev, separators=(",", ":")) + "\n")
        time.sleep(interval)


def elrs_tail_loop(tracker, path, poll=0.4):
    """Follow-tail an ELRS --pkts jsonl file (written by elrs_live.py) and feed
    each decoded packet into the tracker as a link event. The file may not
    exist yet (ELRS child starts up slowly) — wait for it."""
    offset = 0
    fh = None
    while True:
        try:
            if fh is None:
                if not os.path.exists(path):
                    time.sleep(poll)
                    continue
                fh = open(path, "r", encoding="utf-8", errors="replace")
                offset = 0
            fh.seek(0, 2)  # EOF
            size = fh.tell()
            if size < offset:
                # truncated/rotated: re-open from start
                fh.close(); fh = None
                continue
            if size > offset:
                fh.seek(offset)
                chunk = fh.read(size - offset)
                offset = size
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        pkt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tracker.add_link(pkt)
            time.sleep(poll)
        except FileNotFoundError:
            fh = None
            time.sleep(poll)
        except Exception as e:
            print("elrs tail error: %r" % (e,), file=sys.stderr)
            fh = None
            time.sleep(poll)


# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open Drone ID</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3;
          --dim:#8b949e; --acc:#58a6ff; --ok:#3fb950; --warn:#d29922; --bad:#f85149; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { display:flex; align-items:baseline; gap:16px; padding:10px 16px;
           background:var(--panel); border-bottom:1px solid var(--line);
           position:sticky; top:0; z-index:5; }
  header h1 { font-size:16px; margin:0; }
  .stat { color:var(--dim); }
  .stat b { color:var(--fg); }
  .live { color:var(--ok); }
  main { display:grid; grid-template-columns: 340px 1fr; gap:0;
         height:calc(100vh - 46px); }
  #list { border-right:1px solid var(--line); overflow:auto; }
  #detail, #feedcol { overflow:auto; padding:12px 16px; }
  .drow { padding:9px 12px; border-bottom:1px solid var(--line); cursor:pointer; }
  .drow:hover { background:#1c2129; }
  .drow.sel { background:#1f2a3a; }
  .drow .t { font-size:13px; }
  .drow .s { color:var(--dim); font-size:12px; margin-top:2px; }
  .badge { display:inline-block; padding:0 6px; border-radius:3px; font-size:11px;
           border:1px solid var(--line); margin-right:4px; color:var(--dim); }
  .badge.air { color:var(--ok); border-color:var(--ok); }
  .badge.emg { color:var(--bad); border-color:var(--bad); }
  table { border-collapse:collapse; width:100%; }
  td,th { text-align:left; padding:4px 10px 4px 0; vertical-align:top;
          border-bottom:1px solid #21262d; }
  th { color:var(--dim); font-weight:normal; white-space:nowrap; width:170px; }
  h2 { font-size:14px; margin:0 0 8px; color:var(--acc); }
  .muted { color:var(--dim); }
  #feed { font-size:12px; }
  #feed .ev { padding:2px 0; border-bottom:1px solid #21262d; white-space:nowrap; }
  .kv .none { color:var(--dim); }
  .bar { height:4px; background:var(--line); border-radius:2px; margin-top:4px; }
  .bar i { display:block; height:100%; background:var(--acc); }
</style></head>
<body>
<header>
  <h1>Drone Scanner <span class="muted">ODID + ELRS</span></h1>
  <span class="stat">drones <b id="active">0</b></span>
  <span class="stat">links <b id="links">0</b></span>
  <span class="stat">events <b id="events">0</b></span>
  <span class="stat live" id="src">source: -</span>
</header>
<main>
  <div id="list"><div class="muted" style="padding:12px">No drones yet.</div></div>
  <div style="overflow:auto; display:flex; flex-direction:column;">
    <div id="detail" style="padding:12px 16px; overflow:auto;"><h2>Select a drone</h2>
      <div class="muted">Waiting for broadcasts…</div></div>
    <div id="linksPanel" style="padding:12px 16px; overflow:auto; border-top:1px solid var(--line);"></div>
  </div>
  <div id="feedcol" style="display:none"><h2>Live feed</h2><div id="feed"></div></div>
</main>
<script>
let sel = null;
function esc(s){ return (s==null?"":String(s)).replace(/[&<>]/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function fmtAge(s){ if(s==null) return "-";
  if(s<60) return s.toFixed(0)+"s";
  if(s<3600) return (s/60).toFixed(1)+"m";
  return (s/3600).toFixed(1)+"h"; }
function statusBadge(d){
  const st = d.location && d.location.status;
  if(st==="EMERGENCY"||st==="REMOTE_ID_SYSTEM_FAILURE")
    return '<span class="badge emg">'+esc(st)+'</span>';
  if(st==="AIRBORNE") return '<span class="badge air">AIRBORNE</span>';
  if(st) return '<span class="badge">'+esc(st)+'</span>';
  return ''; }
function identityOf(d){
  if(d.identity && d.identity.uas_id) return d.identity.uas_id;
  if(d.selfid && d.selfid.desc) return d.selfid.desc;
  if(d.addr) return d.addr;
  return d.key; }
function renderList(snap){
  const el = document.getElementById('list');
  if(!snap.drones.length){ el.innerHTML='<div class="muted" style="padding:12px">No drones yet.</div>'; return; }
  el.innerHTML = snap.drones.map(d=>{
    const id = identityOf(d);
    const loc = d.location;
    const pos = loc ? (loc.latitude.toFixed(4)+', '+loc.longitude.toFixed(4)) : 'no fix';
    const alt = loc && loc.altitude_geo_m!=null ? (loc.altitude_geo_m+'m') : '';
    const spd = loc && loc.speed_h_ms!=null ? (loc.speed_h_ms+'m/s') : '';
    return '<div class="drow'+(d.key===sel?' sel':'')+'" data-key="'+esc(d.key)+'">'
      + '<div class="t">'+statusBadge(d)+esc(id)+'</div>'
      + '<div class="s">'+esc(pos)+' '+alt+' '+spd
      + (d.rssi!=null?' rssi '+d.rssi:'')
      + ' · '+fmtAge(d.age_s)+'</div></div>';
  }).join('');
  el.querySelectorAll('.drow').forEach(r=>r.onclick=()=>{ sel=r.dataset.key; refresh(); });
}
function row(k,v){ return '<tr><th>'+k+'</th><td>'+(v==null?'<span class="none">-</span>':esc(v))+'</td></tr>'; }
function renderDetail(snap){
  const el = document.getElementById('detail');
  const d = snap.drones.find(x=>x.key===sel);
  if(!d){ el.innerHTML='<h2>Select a drone</h2><div class="muted">Waiting…</div>'; return; }
  const id=d.identity||{}, loc=d.location||{}, sys=d.system||{}, op=d.operator||{}, sf=d.selfid||{};
  let h='<h2>'+esc(identityOf(d))+' <span class="muted">('+esc(d.key)+')</span></h2>';
  h+='<table class="kv">';
  h+=row('First seen', new Date(d.first_seen*1000).toISOString().replace('T',' ').slice(0,19)+'Z');
  h+=row('Last seen', fmtAge(d.age_s)+' ago  ('+d.frames+' frames)');
  h+=row('Transport', d.transports.join(', '));
  h+=row('Source addr', d.addr);
  h+=row('RSSI', d.rssi);
  h+='</table><h2 style="margin-top:14px">Identity</h2><table>';
  h+=row('ID type', id.id_type);
  h+=row('UAS ID / serial', id.uas_id);
  h+=row('UA type', id.ua_type);
  h+=row('Self ID', sf.desc);
  h+=row('Operator ID', op&&op.operator_id);
  h+=row('Auth pages', d.auth_pages?d.auth_pages+' ('+( (d.events.find(e=>e.m&&e.m.type==="AUTH")||{}).m||{}).auth_type||''):'');
  h+='</table><h2 style="margin-top:14px">Position & motion</h2><table>';
  h+=row('Status', loc.status);
  h+=row('Latitude', loc.latitude!=null?loc.latitude:'-');
  h+=row('Longitude', loc.longitude!=null?loc.longitude:'-');
  h+=row('Altitude geo', loc.altitude_geo_m!=null?loc.altitude_geo_m+' m':'-');
  h+=row('Altitude baro', loc.altitude_baro_m!=null?loc.altitude_baro_m+' m':'-');
  h+=row('Height', loc.height_m!=null?loc.height_m+' m ('+loc.height_ref+')':'-');
  h+=row('Direction', loc.direction_deg!=null?loc.direction_deg.toFixed(0)+'°':'-');
  h+=row('Speed horiz', loc.speed_h_ms!=null?loc.speed_h_ms+' m/s':'-');
  h+=row('Speed vert', loc.speed_v_ms!=null?loc.speed_v_ms+' m/s':'-');
  h+=row('Timestamp', loc.timestamp_s_since_hour);
  h+='</table>';
  if(sys){ h+='<h2 style="margin-top:14px">System / operator</h2><table>';
    h+=row('Operator loc type', sys.operator_location_type);
    h+=row('Operator lat/lon', sys.operator_latitude!=null?sys.operator_latitude+', '+sys.operator_longitude:'-');
    h+=row('Operator alt', sys.operator_altitude_geo_m!=null?sys.operator_altitude_geo_m+' m':'-');
    h+=row('Area radius', sys.area_radius_m!=null?sys.area_radius_m+' m':'-');
    h+=row('Area floor/ceil', sys.area_floor_m+' m / '+sys.area_ceiling_m+' m');
    h+=row('EU class', sys.eu_class);
    h+='</table>'; }
  el.innerHTML=h;
}
function renderFeed(snap){
  const el=document.getElementById('feed');
  el.innerHTML = snap.feed.slice(0,60).map(e=>
    '<div class="ev"><span class="muted">'+e.ts.slice(11,19)+'</span> '
    +esc(e.transport)+' '+(e.addr||'')+' ['+e.types.join(',')+']</div>').join('');
}
function renderLinks(snap){
  const el=document.getElementById('linksPanel');
  if(!el) return;
  if(!snap.links.length){ el.innerHTML='<h2 style="margin-top:14px">ELRS links</h2><div class="muted">No CRC-valid ELRS packets yet (bind or --uid needed).</div>'; return; }
  let h='<h2 style="margin-top:14px">ELRS control/telemetry links</h2>';
  snap.links.forEach(l=>{
    h+='<table><tr><th>Key</th><td>'+esc(l.key)+'</td></tr>'
      +'<tr><th>Packets</th><td>'+l.packets+' · last '+fmtAge(l.age_s)+' ago</td></tr>'
      +'<tr><th>UID</th><td>'+(l.uid?esc(l.uid):'auto / brute (no UID)')+'</td></tr>'
      +'<tr><th>OTA versions</th><td>'+esc((l.ota_versions||[]).join(', '))+'</td></tr>'
      +'<tr><th>LoRa SF</th><td>'+esc((l.sfs||[]).join(', '))+'</td></tr>'
      +'<tr><th>Pkt types</th><td>'+esc((l.pkt_types||[]).join(', '))+'</td></tr>'
      +'<tr><th>Sizes</th><td>'+esc((l.sizes||[]).join(', '))+' bytes</td></tr>'
      +'<tr><th>Last pkt</th><td class="muted">'+(l.last_hex?esc(l.last_hex):'-')+'</td></tr>'
      +'</table>';
  });
  el.innerHTML=h;
}
async function refresh(){
  try{
    const r = await fetch('/api/state');
    const snap = await r.json();
    document.getElementById('active').textContent=snap.active_drones;
    const lk=document.getElementById('links'); if(lk) lk.textContent=snap.active_links||0;
    document.getElementById('events').textContent=snap.total_events;
    renderList(snap); renderDetail(snap); renderLinks(snap); renderFeed(snap);
  }catch(e){ /* transient */ }
}
refresh();
setInterval(refresh, 1500);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    tracker = None
    source_desc = ""

    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/api/state":
            snap = self.tracker.snapshot()
            snap["source"] = self.source_desc
            self._send(200, json.dumps(snap), "application/json")
        elif path == "/api/health":
            self._send(200, json.dumps({"ok": True,
                                        "drones": self.tracker.snapshot()["active_drones"]}),
                       "application/json")
        else:
            self._send(404, "not found", "text/plain")


def serve(tracker, port, host):
    Handler.tracker = tracker
    Handler.source_desc = getattr(serve, "source_desc", "")
    httpd = ThreadingHTTPServer((host, port), Handler)
    print("Web UI:  http://%s:%d   (source: %s)" % (host, port, Handler.source_desc))
    print("API:     http://%s:%d/api/state" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main():
    ap = argparse.ArgumentParser(
        description="ODID capture + live Web UI (stdlib only).")
    ap.add_argument("--port", type=int, default=10000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--source",
                    choices=["btmon-live", "btmon", "pcap", "stdin", "replay"],
                    default="btmon-live")
    ap.add_argument("-f", "--file")
    ap.add_argument("--jsonl", help="also tee events to this jsonl file")
    ap.add_argument("--elrs-pkts",
                    help="also tail this ELRS --pkts jsonl and show ELRS links "
                         "in the UI (start elrs_live.py --live --pkts <this>")
    args = ap.parse_args()

    if args.source == "btmon" and not args.file:
        ap.error("--source btmon needs -f <logfile>")
    if args.source == "pcap" and not args.file:
        ap.error("--source pcap needs -f <pcap>")

    jsonl = args.jsonl or (
        "odr_web_%s.jsonl" % _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.source in ("btmon-live", "replay") else None)

    tracker = Tracker()
    serve.source_desc = "%s%s" % (args.source,
                                  (" " + args.file) if args.file else "")

    if args.source == "replay":
        cap = threading.Thread(
            target=replay_loop, args=(tracker, jsonl), daemon=True)
        cap.start()
    else:
        cap = threading.Thread(
            target=capture_loop,
            args=(tracker, args.source, args.file, jsonl), daemon=True)
        cap.start()

    if args.elrs_pkts:
        elrs = threading.Thread(
            target=elrs_tail_loop, args=(tracker, args.elrs_pkts), daemon=True)
        elrs.start()
        print("ELRS links: tailing %s" % args.elrs_pkts)

    try:
        serve(tracker, args.port, args.host)
    except OSError as e:
        print("cannot bind %s:%d: %s" % (args.host, args.port, e),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
