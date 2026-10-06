"""Inspect RF: the shared spectrum view, its release-on-close contract, and the Monitor drawer.

The view scripts run in Node against stubbed HTTP calls and a minimal fake DOM, so
nothing here opens a radio."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from waveraider import web

PKG = Path(web.__file__).parent
MODULE = (PKG / "inspect_rf.js").read_text()
DISPLAY = (PKG / "spectrum_display.js").read_text()
MONITOR = (PKG / "monitor.html").read_text()
SETTINGS = (PKG / "settings.html").read_text()

ROW = {"key": "wifi_beacon:60:60:1f:00:00:01", "evidence": "remote_id", "sample": True,
       "transmitter": "60:60:1f:00:00:01", "transport_label": "Wi-Fi beacon", "identity": "DRONE-SN-001A",
       "ua_type_label": "Multirotor", "position": [36.8529, -75.978], "last_seen": 130.0, "stale": False,
       "watchlist": [], "basic_ids": [], "alt_m": 120.0, "operator": None, "operator_id": None,
       "first_seen": 100.0, "claimed_time_s": None, "messages": 10, "protocol_version": 2}
FRAME = {"source": "simulated", "note": "simulated", "controlled": False, "device": None,
         "start_hz": 99e6, "stop_hz": 101e6, "center_hz": 100e6, "span_hz": 2e6,
         "points": [[99e6, -90], [100e6, -40], [101e6, -90]], "peak": {"freq_hz": 100e6, "db": -40},
         "floor_db": -90, "signals": [], "bands": []}

FETCH = """
const calls=[]; let sweepGate=null, acquireGate=null, releaseGate=null, failNext=null;
const FRAME=%s;
const reply=o=>({ok:true,status:200,json:async()=>o,headers:{get:()=>"application/json"}});
const broken={ok:true,status:200,json:async()=>{throw new Error("truncated response");},headers:{get:()=>""}};
const fails=kind=>{if(failNext===kind){failNext=null;return true;}return false;};
// The backend's lease: applied when a request arrives (gates only delay the reply). With ownerAware,
// a sweep or acquire from a different owner is refused the way spectrum.sweep/acquire refuse it.
const lease={owner:null}; let ownerAware=false;
const BUSY="The SDR is under another session's control.";
globalThis.fetch=async (url,opts)=>{
  calls.push({url,method:(opts&&opts.method)||"GET",body:opts&&opts.body});
  // The server's rule, enforced by the stub so every executed request is checked: radio requests carry the
  // app's marker header, and changes are sent as JSON.
  const hdr=(opts&&opts.headers)||{};
  const radio=["/api/spectrum","/api/analyze","/api/audio","/api/droneid"].some(p=>url.startsWith(p));
  if(radio&&hdr["X-WaveRaider-Request"]!=="1")throw new Error("radio request without the marker header: "+url);
  if(opts&&opts.method==="POST"&&hdr["Content-Type"]!=="application/json")throw new Error("POST not sent as JSON: "+url);
  const body=opts&&opts.body?JSON.parse(opts.body):{};
  if(url.startsWith("/api/spectrum/acquire")){
    const busy=ownerAware&&lease.owner&&lease.owner!==body.owner; if(!busy)lease.owner=body.owner;
    if(acquireGate){const g=acquireGate;acquireGate=null;await g;}
    return fails("acquire")?broken:reply(busy?{ok:false,reason:BUSY}:{ok:true});}
  if(url.startsWith("/api/spectrum/release")){
    if(lease.owner===body.owner)lease.owner=null;
    if(releaseGate){const g=releaseGate;releaseGate=null;await g;}
    return reply({ok:true});}
  if(url.startsWith("/api/spectrum?")){
    const o=new URL(url,"http://stub").searchParams.get("owner"), busy=ownerAware&&lease.owner&&o&&lease.owner!==o;
    if(!busy&&o)lease.owner=o;
    if(sweepGate){const g=sweepGate;sweepGate=null;await g;}
    return fails("sweep")?broken:reply(busy?{error:"busy",note:BUSY}:FRAME);}
  if(url.startsWith("/api/sdr"))return reply({readiness:{radios:[],live_capable:false,sdr_detected:false}});
  return reply(ROUTES[url.split("?")[0]]||{});
};
const releases=from=>calls.slice(from).filter(c=>c.url==="/api/spectrum/release");
const sweeps=from=>calls.slice(from).filter(c=>c.url.startsWith("/api/spectrum?"));
const ownerOf=c=>JSON.parse(c.body).owner;
const settle=async(n=1)=>{for(let i=0;i<n;i++)await new Promise(r=>setImmediate(r));};
""" % json.dumps(FRAME)

# Just the elements the view writes to; canvases stay absent so drawing is skipped.
VIEW_DOM = """
const assert=require('node:assert/strict');
const ROUTES={}, els={};
const LIVE_IDS=new Set(["view","spRun","specsrc","specready","spSignals","spPace","spHoldDisplay","spDisplayStatus","spAnalysis","spAxis"]);
const document={activeElement:null,getElementById(id){if(!LIVE_IDS.has(id))return null;
  return els[id]||(els[id]={id,innerHTML:"",textContent:"",value:"",setAttribute(){},matches:()=>false,contains:()=>false});}};
let open=true;
"""
VIEW_HOST = 'specUseHost({active:()=>open, view:()=>document.getElementById("view")});\n'


@pytest.fixture
def node():
    path = shutil.which("node")
    if not path:
        pytest.skip("Node.js is required to run the Inspect RF scripts")
    return path


def _node(node, script):
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout


def _view(node, body):
    wrapped = "(async()=>{\n" + body + '\nprocess.stdout.write("ok");\n})().catch(e=>{console.error(e);process.exit(1);});'
    return _node(node, VIEW_DOM + FETCH + DISPLAY + "\n" + MODULE + "\n" + VIEW_HOST + wrapped)


def _block(page):
    return page.split("/* ---- pure renderers ---- */", 1)[1].split("/* ---- end pure renderers ---- */", 1)[0]


# ---- the shared view's release contract ------------------------------------------------

def test_closing_the_view_stops_live_capture_and_releases_the_radio(node):
    assert _view(node, """
  await specMount();
  assert.ok(document.getElementById("view").innerHTML.includes('id="spCanvas"'));
  assert.equal(sweeps(0).length,1);
  assert.ok(sweeps(0)[0].url.includes("owner="+encodeURIComponent(specSession)));
  await specToggle();                                   // ▶ Live: take the radio, then sweep
  assert.equal(specRunning,true);
  assert.equal(calls.filter(c=>c.url==="/api/spectrum/acquire").length,1);
  await settle(5);
  const mark=calls.length, owner=specSession;
  open=false; specUnmount();
  assert.equal(specRunning,false); assert.equal(specTimer,null);
  assert.equal(ownerOf(releases(mark)[0]),owner);
  assert.notEqual(specSession,owner);                   // the next opening gets a fresh owner
  await new Promise(r=>setTimeout(r,300));
  assert.equal(sweeps(mark).length,0);                  // nothing sweeps after close
""") == "ok"


def test_a_sweep_that_returns_after_close_hands_the_lease_back(node):
    assert _view(node, """
  let finish; sweepGate=new Promise(r=>finish=r);
  const late=specOnce();                                // an owned sweep is on its way to the server
  open=false; specUnmount();
  const mark=calls.length;
  finish(); await late;
  assert.equal(releases(mark).length,1);
  assert.equal(document.getElementById("specsrc").innerHTML,"");   // and it isn't drawn
  open=true; sweepGate=new Promise(r=>finish=r);
  const retuned=specOnce(); specGen++;                  // superseded by a retune while still open
  const mark2=calls.length; finish(); await retuned;
  assert.equal(releases(mark2).length,0);
""") == "ok"


def test_closing_while_live_is_being_acquired_does_not_keep_the_radio(node):
    assert _view(node, """
  let grant; acquireGate=new Promise(r=>grant=r);
  const toggling=specToggle();                          // ▶ Live pressed; acquire still in flight
  open=false; specUnmount();
  const mark=calls.length;
  grant(); await toggling;
  assert.equal(specRunning,false);
  assert.equal(releases(mark).length,1);
  assert.equal(sweeps(mark).length,0);
""") == "ok"


def test_a_reopened_view_is_never_driven_by_the_previous_opening(node):
    assert _view(node, """
  await specMount(); const first=specSession;
  let grant; acquireGate=new Promise(r=>grant=r);
  const toggling=specToggle();                          // ▶ Live pressed in the first opening
  open=false; specUnmount();
  open=true; await specMount();                         // closed, then reopened
  assert.notEqual(specSession,first);                   // each opening owns its own lease
  const mark=calls.length;
  grant(); await toggling;                              // the first opening's acquire finally answers
  assert.equal(specRunning,false);                      // it must not start capture in this opening
  assert.equal(sweeps(mark).length,0);
  assert.ok(releases(mark).length>=1);
  assert.ok(releases(mark).every(c=>ownerOf(c)===first));   // and it releases only its own lease
""") == "ok"


def test_reentering_an_open_view_keeps_its_lease_and_leaving_frees_the_radio(node):
    assert _view(node, """
  ownerAware=true;                                      // the stub now refuses a different owner, like the backend
  const busyShown=()=>document.getElementById("specsrc").innerHTML.includes("SDR BUSY");
  await specMount();                                    // open the view
  const owner=specSession; assert.equal(lease.owner,owner);
  await specMount();                                    // re-render while open: specMount without specUnmount
  assert.equal(specSession,owner); assert.equal(busyShown(),false); assert.equal(lease.owner,owner);
  let finish; sweepGate=new Promise(r=>finish=r);
  const old=specOnce();                                 // a sweep from before the next re-entry is still in flight
  await specMount();
  finish(); await old;
  assert.equal(lease.owner,owner); assert.equal(busyShown(),false);   // the stale reply doesn't give away the open view's lease
  await specToggle(); assert.equal(specRunning,true);   // ▶ Live still works after re-entry
  await specMount();                                    // re-entered while live: the fresh view isn't live
  assert.equal(specRunning,false); assert.equal(specTimer,null);
  open=false; specUnmount();                            // close the view
  await settle(10);
  assert.equal(lease.owner,null);                       // nothing left holding the radio
""") == "ok"


def test_stop_followed_by_close_starts_no_new_sweep(node):
    assert _view(node, """
  await specMount(); await specToggle(); await settle(3);
  assert.equal(specRunning,true);
  let finish; releaseGate=new Promise(r=>finish=r);
  const stopping=specToggle();                          // ■ Stop pressed; its release is slow to answer
  open=false; specUnmount();
  const mark=calls.length;
  finish(); await stopping;
  assert.equal(specRunning,false);
  assert.equal(sweeps(mark).length,0);                  // the stop path must not re-sweep a closed view
""") == "ok"


def test_a_response_that_cannot_be_read_still_hands_the_lease_back(node):
    assert _view(node, """
  await specMount(); const owner=specSession;
  let finish; sweepGate=new Promise(r=>finish=r); failNext="sweep";
  const late=specOnce();                                // this sweep's reply will be unreadable
  open=false; specUnmount();
  const mark=calls.length;
  finish(); await late;
  assert.ok(releases(mark).some(c=>ownerOf(c)===owner));
  open=true; await specMount(); const owner2=specSession;
  const mark2=calls.length; failNext="acquire";
  await specToggle();                                   // ▶ Live, but the acquire reply is unreadable
  assert.equal(specRunning,false);
  assert.ok(releases(mark2).some(c=>ownerOf(c)===owner2));
""") == "ok"


def test_shared_view_does_not_redefine_page_globals():
    assert not re.search(r"^(function esc\(|const api\b|let cur\b|const \$|const state\b)", MODULE, re.M)
    assert "catalog" not in MODULE and 'go("' not in MODULE


# ---- Monitor drawer ------------------------------------------------------------------------

def _page(node, body, hash_=""):
    routes = {
        "/api/session": {"state": "running", "source": "sample", "source_label": "Sample data",
                         "sample": {"description": "Bundled sample"}, "started_at": 100, "stopped_at": None,
                         "coverage": {"active": True, "covered": ["Wi-Fi beacon Remote ID (recorded sample)"],
                                      "retained": [], "not_covered": []}, "error": None},
        "/api/observations": {"now": 131, "observations": [ROW]},
        "/api/diagnostics": {"total": 0, "rejected": 0, "partial": 0, "reasons": []},
        "/api/receivers": {"receivers": [], "error": None},
    }
    dom = """
const assert=require('node:assert/strict');
const ROUTES=%s;
const NULL_IDS=new Set(["spCanvas","spWater","spStrength"]);
function mk(key){return {key,innerHTML:"",textContent:"",hidden:false,inert:false,value:"",disabled:false,dataset:{},style:{},
  className:"",classList:{toggle(){},add(){},remove(){},contains:()=>false},_l:{},
  addEventListener(t,f){(this._l[t]=this._l[t]||[]).push(f);},setAttribute(){},getAttribute:()=>null,
  focus(o){document.activeElement=this;this.focusOpts=o||null;},contains:()=>false,matches:()=>false,closest:()=>null,
  querySelector:()=>null,querySelectorAll:()=>[]};}
const byId={}, bySel={}, NO_SEL=new Set();
const document={activeElement:null,_l:{},addEventListener(t,f){(this._l[t]=this._l[t]||[]).push(f);},
  getElementById:id=>NULL_IDS.has(id)?null:(byId[id]||(byId[id]=mk("#"+id))),
  querySelector:s=>NO_SEL.has(s)?null:(bySel[s]||(bySel[s]=mk(s))),querySelectorAll:()=>[]};
globalThis.window=globalThis; const winL={}; globalThis.addEventListener=(t,f)=>{winL[t]=f;};
globalThis.location={hash:%s,pathname:"/monitor",search:""};
const replaced=[]; globalThis.history={replaceState:(s,t,url)=>{replaced.push(url);location.hash="";}};
globalThis.setTimeout=()=>0; globalThis.setInterval=()=>0;
""" % (json.dumps(routes), json.dumps(hash_))
    inline = re.search(r"<script>(.*?)</script>", MONITOR, re.S).group(1)
    wrapped = "(async()=>{\n" + body + '\nprocess.stdout.write("ok");\n})().catch(e=>{console.error(e);process.exit(1);});'
    return _node(node, dom + FETCH + DISPLAY + "\n" + MODULE + "\n" + inline + "\n" + wrapped)


def test_monitor_loads_the_view_scripts_before_its_own():
    assert '<link rel="stylesheet" href="/inspect-rf.css">' in MONITOR
    assert '<script src="/spectrum-display.js"></script>\n<script src="/inspect-rf.js"></script>\n<script>' in MONITOR
    assert 'role="dialog" aria-modal="true" aria-labelledby="irfTitle" hidden' in MONITOR


def test_evidence_disclosure_and_focus_survive_polling(node):
    assert _page(node, """
  await settle(30);
  const detail=document.getElementById('detail');
  const disclosure={dataset:{disclosure:'evidence'},open:true};
  const summary=mk('summary'); summary.dataset.act='evidence-notes';
  detail.querySelectorAll=s=>s.includes('[open]')?(disclosure.open?[disclosure]:[]):[disclosure];
  detail.contains=el=>el===summary;
  detail.querySelector=()=>summary;
  document.activeElement=summary;
  paint();
  assert.equal(disclosure.open,true);
  assert.equal(document.activeElement,summary);
  assert.deepEqual(summary.focusOpts,{preventScroll:true});
  disclosure.open=false;
  paint();
  assert.equal(disclosure.open,false);
  assert.ok(detail.innerHTML.includes('not authenticated'));
  assert.ok(detail.innerHTML.includes('not the original capture times'));
  assert.ok(!document.getElementById('obsList').innerHTML.includes('Not part of monitoring yet'));
  state.obs.push({...state.obs[0],key:'second-observation',lifecycle:'lapsed'});
  paint();
  const list=document.getElementById('obsList').innerHTML;
  for(const observation of state.obs)assert.ok(list.includes('data-key=\"'+observation.key+'\"'));
  assert.equal(document.getElementById('obsCount').textContent,'Observations ('+state.obs.length+')');
""") == "ok"


def test_stacked_selection_focuses_evidence_without_moving_desktop(node):
    assert _page(node, """
  await settle(30);
  const detail=document.getElementById('detail');
  let scrolls=0;
  detail.scrollIntoView=()=>{scrolls++;};
  window.matchMedia=()=>({matches:true});
  selectObservation(state.obs[0].key);
  assert.equal(scrolls,1);
  assert.equal(document.activeElement,detail);
  window.matchMedia=()=>({matches:false});
  selectObservation(state.obs[0].key);
  assert.equal(scrolls,1);
""") == "ok"


def test_drawer_from_a_radio_link_closes_with_escape_and_releases(node):
    assert _page(node, """
  await settle(30);
  const drawer=document.getElementById("irfDrawer"), view=document.getElementById("irfView");
  assert.equal(drawer.hidden,false); assert.equal(document.getElementById("irfBackdrop").hidden,false);
  assert.equal(specDevice,"2cf0:5250");
  assert.ok(document.getElementById("irfContext").innerHTML.includes("Opened from Settings › Receivers for radio 2cf0:5250"));
  assert.ok(view.innerHTML.includes('id="spCanvas"'));
  assert.ok(sweeps(0).some(c=>c.url.includes("device=2cf0%3A5250")));
  assert.equal(document.querySelector(".main").inert,true);
  assert.equal(document.activeElement,document.getElementById("irfClose"));
  const mark=calls.length;
  document._l.keydown.forEach(f=>f({key:"Escape",preventDefault(){}}));
  assert.equal(drawer.hidden,true); assert.equal(view.innerHTML,""); assert.equal(state.inspect.open,false);
  assert.equal(document.querySelector(".main").inert,false);
  assert.equal(releases(mark).length,1); assert.deepEqual(replaced,["/monitor"]);
  assert.equal(esc('"<'),"&quot;&lt;");                 // Monitor's own escaping is untouched by the shared script
""", hash_="#inspect=2cf0%3A5250") == "ok"


def test_drawer_from_an_observation_releases_a_live_hold_on_close(node):
    assert _page(node, """
  await settle(30);
  assert.equal(state.selected,%s);
  assert.equal(state.inspect.open,false); assert.equal(sweeps(0).length,0);   // closed, and no sweep, until asked
  document.getElementById("detail")._l.click.forEach(f=>f({target:{closest:s=>s.includes('"inspect"')?{}:null}}));
  await settle(10);
  const ctx=document.getElementById("irfContext").innerHTML;
  assert.ok(ctx.includes("Opened from DRONE-SN-001A · transmitter 60:60:1f:00:00:01 · Wi-Fi beacon"));
  assert.ok(ctx.includes("replayed sample data") && ctx.includes("releases the radio"));
  await specToggle(); assert.equal(specRunning,true);
  const mark=calls.length;
  document.getElementById("irfClose")._l.click.forEach(f=>f({}));
  assert.equal(specRunning,false); assert.equal(releases(mark).length,1);
  assert.equal(document.getElementById("irfDrawer").hidden,true); assert.deepEqual(replaced,[]);
""" % json.dumps(ROW["key"])) == "ok"


def test_link_navigation_switches_radios_and_leaving_the_link_closes(node):
    assert _page(node, """
  await settle(30);
  assert.equal(specDevice,"2cf0:5250");
  ownerAware=true;                                              // switching must not contend with its own lease
  const first=specSession, mark=calls.length;
  location.hash="#inspect=0bda%3A2838"; winL.hashchange();      // the link now names another radio
  await settle(20);
  assert.equal(state.inspect.open,true); assert.equal(specDevice,"0bda:2838");
  assert.ok(releases(mark).some(c=>ownerOf(c)===first));
  assert.ok(sweeps(mark).some(c=>c.url.includes("device=0bda%3A2838")));
  assert.deepEqual(replaced,[]);                                // switching keeps the link in the address bar
  assert.equal(lease.owner,specSession);
  assert.ok(!document.getElementById("specsrc").innerHTML.includes("SDR BUSY"));
  const second=specSession, mark2=calls.length;
  location.hash=""; winL.hashchange();                          // back button / link removed
  await settle(10);
  assert.equal(state.inspect.open,false); assert.equal(specRunning,false);
  assert.ok(releases(mark2).some(c=>ownerOf(c)===second));
  assert.equal(lease.owner,null);
""", hash_="#inspect=2cf0%3A5250") == "ok"


def test_an_observation_drawer_ignores_other_hashes_and_focus_falls_back(node):
    assert _page(node, """
  await settle(30);
  assert.equal(state.inspect.open,false);
  document.getElementById("detail")._l.click.forEach(f=>f({target:{closest:s=>s.includes('"inspect"')?{}:null}}));
  await settle(10);
  assert.equal(state.inspect.open,true);
  location.hash="#watchlist"; winL.hashchange(); await settle(5);
  assert.equal(state.inspect.open,true);                        // it wasn't opened from a link, so leave it alone
  NO_SEL.add('#detail [data-act="inspect"]');                   // nothing selected: no button to return focus to
  document.getElementById("irfClose")._l.click.forEach(f=>f({}));
  await settle(5);
  assert.equal(state.inspect.open,false);
  assert.equal(document.activeElement,document.getElementById("sessionBtn"));
""") == "ok"


def test_monitor_refresh_keeps_focus_without_scrolling(node):
    # A plain focus() after each one-second redraw scrolled the page back to the focused
    # row or button, so the page couldn't be scrolled up once one had focus.
    key = json.dumps(ROW["key"])
    assert _page(node, """
  await settle(30);
  const row=mk("row"); row.dataset.key=%s;
  document.activeElement={dataset:{key:%s}};
  document.querySelectorAll=s=>s==="#obsList tr[data-key]"?[row]:[];
  paint();
  assert.equal(document.activeElement,row); assert.deepEqual(row.focusOpts,{preventScroll:true});
  const detail=document.getElementById("detail"), btn=mk("btn");
  document.activeElement={dataset:{act:"inspect"}};
  detail.contains=el=>!!(el&&el.dataset&&el.dataset.act==="inspect");
  detail.querySelector=s=>s==='[data-act="inspect"]'?btn:null;
  paint();
  assert.equal(document.activeElement,btn); assert.deepEqual(btn.focusOpts,{preventScroll:true});
  detail._l.click.forEach(f=>f({target:{closest:s=>s.includes('"inspect"')?{}:null}}));
  await settle(10);
  assert.deepEqual(document.getElementById("irfClose").focusOpts,{preventScroll:true});
  const back=mk("back"); bySel['#detail [data-act="inspect"]']=back;
  document.getElementById("irfClose")._l.click.forEach(f=>f({}));
  assert.equal(document.activeElement,back); assert.deepEqual(back.focusOpts,{preventScroll:true});
""" % (key, key)) == "ok"


def test_inspect_links_context_and_button(node):
    run = lambda expr: _node(node, _block(MONITOR) + "\nprocess.stdout.write(String(" + expr + "));")
    targets = json.loads(run('JSON.stringify([inspectTarget("#inspect=2cf0%3A5250"),inspectTarget("#inspect"),'
                             'inspectTarget("#inspect=%E0%A4%A"),inspectTarget("#watchlist"),inspectTarget("")])'))
    assert targets == [{"device": "2cf0:5250"}, {"device": ""}, {"device": ""}, None, None]
    ctx = run("inspectContext(" + json.dumps(dict(ROW, identity='<b>SN "1"</b>')) + ', "")')
    assert "&lt;b&gt;SN &quot;1&quot;&lt;/b&gt;" in ctx and "<b>SN" not in ctx
    assert "doesn’t show that this transmitter is on any frequency" in ctx and "isn’t a detection" in ctx
    assert "replayed sample data" in ctx and "releases the radio" in ctx
    radio = run('inspectContext(null, "2cf0:5250")')
    assert "Opened from Settings › Receivers for radio 2cf0:5250." in radio
    assert "this transmitter" not in radio and "sample" not in radio
    assert '<button class="btn" type="button" data-act="inspect">Inspect RF…</button>' in run(
        "renderDetail(" + json.dumps(ROW) + ", 131)")


def test_settings_radios_link_to_the_drawer(node):
    data = {"receivers": [
        {"id": "remote-id-wifi", "name": "Remote ID · Wi-Fi beacons", "role": "Remote ID", "state": "unavailable",
         "state_label": "No receiver", "note": "Live Wi-Fi Remote ID capture isn't supported yet."},
        {"id": "radio:2cf0:5250", "name": "bladeRF 2.0 micro", "role": "Inspect RF", "usb_id": "2cf0:5250",
         "state": "ready", "state_label": "Ready", "note": "native libbladeRF"}],
        "other_hardware": [], "error": None, "hardware_error": None}
    html = _node(node, _block(SETTINGS) + "\nprocess.stdout.write(renderReceivers(" + json.dumps(data) + "));")
    assert html.count("Inspect RF…") == 1 and 'href="/monitor#inspect=2cf0%3A5250"' in html


# ---- styles, files and packaging ---------------------------------------------------------------

def test_drawer_css_is_scoped_to_the_view():
    css = (PKG / "inspect_rf.css").read_text()
    rules = [line for line in css.splitlines() if "{" in line]
    assert rules and all(line.startswith(".irf ") for line in rules)


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_view_files_are_served(server):
    with urllib.request.urlopen(server + "/inspect-rf.js", timeout=5) as r:
        assert r.headers["Content-Type"].startswith("application/javascript") and b"function specMount" in r.read()
    with urllib.request.urlopen(server + "/inspect-rf.css", timeout=5) as r:
        assert r.headers["Content-Type"].startswith("text/css") and b".irf .specctl{" in r.read()


def test_view_files_are_packaged():
    text = (PKG.parent / "pyproject.toml").read_text()
    assert '"inspect_rf.js"' in text and '"inspect_rf.css"' in text
