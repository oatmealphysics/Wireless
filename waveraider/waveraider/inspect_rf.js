/* Inspect RF — the RF spectrum view for the drone bands: band tabs, plot, waterfall, tuning
   kept inside a band, trace modes, signal list, an analog video check in the video bands and
   the experimental DroneID scan, shown in the drawer on Monitor. It began as the retired
   protocol dashboard's Spectrum view.

   A host calls specUseHost() once, specMount() to render into its view, and specUnmount()
   when the view closes: that stops live capture, drops in-flight sweeps and releases the
   radio. Every opening gets its own number, and every close hands out a fresh lease owner,
   so a reply that arrives after close or reopen can tell it's stale and releases only a
   lease no open view uses. Re-rendering an open view (specMount without specUnmount)
   keeps its owner, so the view never contends with itself.
   Load /spectrum-display.js first. Functions stay global because the rendered markup
   uses inline handlers. */
let specHost={active:()=>false, view:()=>null};
function specUseHost(host){specHost=Object.assign({},specHost,host);}
function specEsc(t){return (t==null?"":""+t).replace(/&/g,"&amp;").replace(/</g,"&lt;");}
const specApi=(u,o)=>fetch(u,Object.assign({},o,{headers:Object.assign({"X-WaveRaider-Request":"1"},(o||{}).headers)})).then(r=>r.json());   // the server refuses radio requests without this marker
const specPost=(u,body)=>specApi(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});   // and changes not sent as JSON
let specMountGen=0;                                   // which opening of the view is current
function specNewOwner(){return "spec-"+Math.random().toString(36).slice(2)+Date.now().toString(36);}
function specCurrent(mount){return specHost.active()&&mount===specMountGen;}
function specReleaseRetired(owner){if(owner!==specSession)specRelease(owner);}   // the current owner is released by specUnmount
function specUnmount(){specMountGen++; specStop(); specGen++; const owner=specSession; specSession=specNewOwner(); specRelease(owner);}

const specDisplay=new SpectrumDisplay();
let specTimer=null, specCenter=2442000000, specSpan=60000000;   // opens on the 2.4 GHz band
let specMode="live", specHold=null, specAvg=null, specAvgN=0, specKey="", specLast=null, specDevice="";
let specRngLo=null, specRngHi=null, specRngKey="", specWfTall=false;
let specRunning=false, specGen=0;   // self-paced loop + a token to drop stale (old-radio) frames
let specSession=specNewOwner();   // lease owner for the current view; specUnmount retires it and issues a fresh one
// Drone RF bands, the only ranges Inspect RF tunes: [key, tab label, lo MHz, hi MHz, default center MHz, default span MHz].
// Same table as waveraider/bands.py. 5.8/1.3 GHz need an SDR that reaches them (e.g. bladeRF).
const DRONE_BANDS=[["868","868 ctrl",850,870,866.5,7],["900","900 ctrl",902,928,915,26],["1.3","1.3 video",1080,1360,1280,40],
  ["2.4","2.4 GHz",2400,2483.5,2442,60],["5.2","5.2 GHz",5150,5645,5200,60],["5.8","5.8 video",5645,5945,5800,60]];
const SPEC_SPAN_MIN=10000, SPEC_SPAN_MAX=60000000;
// Fit a view inside one drone band (the one holding the center, or the nearest), like bands.fit_window.
function specFit(centerHz,spanHz){
  let c=Number(centerHz), s=Number(spanHz);
  if(!Number.isFinite(c)){const b=DRONE_BANDS.find(x=>x[0]==="2.4"); c=b[4]*1e6; s=b[5]*1e6;}
  if(!Number.isFinite(s))s=SPEC_SPAN_MAX;
  const dist=b=>Math.min(Math.abs(c-b[2]*1e6),Math.abs(c-b[3]*1e6));
  // the band containing the center wins, so an adjacent band's excluded upper edge never ties with it
  let band=DRONE_BANDS.find(b=>b[2]*1e6<=c&&c<b[3]*1e6);
  if(!band){band=DRONE_BANDS[0]; for(const b of DRONE_BANDS){if(dist(b)<dist(band))band=b;}}
  const lo=band[2]*1e6, hi=band[3]*1e6;
  s=Math.max(SPEC_SPAN_MIN,Math.min(s,SPEC_SPAN_MAX,hi-lo));
  c=Math.max(lo+s/2,Math.min(c,hi-s/2));
  return {center:c, span:s, band:band[0]};
}
function specVideoBand(freqHz){const f=freqHz/1e6; return (f>=1080&&f<1360)||(f>=5645&&f<5945);}   // analog FPV video
function specSyncBands(){
  const key=specFit(specCenter,specSpan).band;
  DRONE_BANDS.forEach(b=>{const el=document.getElementById("spBand"+b[0].replace(".","_")); if(!el)return;
    const on=b[0]===key; el.classList.toggle("on",on); el.setAttribute("aria-pressed",String(on));});
}

async function specMount(){
  const mount=++specMountGen; specStop();   // a freshly rendered view is never live; re-entering an open view keeps its owner
  specLoadState();
  specHost.view().innerHTML='<div class="pad"><h2>Spectrum</h2>'+
    '<div id="specsrc" class="muted" style="margin-bottom:6px">tuning…</div>'+
    '<div id="specready" style="margin-bottom:12px"></div>'+
    '<div class="specctl">'+
      '<label>Center (MHz)<input id="spCenter" type="number" step="0.1" value="'+(specCenter/1e6)+'"></label>'+
      '<label>Span (MHz)<input id="spSpan" type="number" step="0.1" value="'+(specSpan/1e6)+'"></label>'+
      '<button class="btn" onclick="specApply()">Tune</button>'+
      '<button class="btn" onclick="specZoomOut()" title="widen the span 2x, up to the whole band">⊖ Zoom out</button>'+
      '<button class="btn" id="spRun" onclick="specToggle()">▶ Live</button>'+
    '</div>'+
    '<div class="specctl"><span class="preset">Drone bands:</span>'+
      DRONE_BANDS.map(b=>'<button class="chip" type="button" id="spBand'+b[0].replace(".","_")+'" aria-pressed="false" onclick="specPreset('+b[4]+','+b[5]+')" title="'+specEsc(b[1])+' — '+b[2]+'–'+b[3]+' MHz; tuning stays inside this band">'+specEsc(b[1])+'</button>').join("")+
      '<button class="chip spgo" onclick="specDroneID()" title="experimental pilot-pair candidate scan; no payload/CRC validation or GPS (needs bladeRF)">🛰 Experimental DroneID scan</button>'+
      '<button class="chip spgo" onclick="specElrsFhss()" title="dwells the ELRS sync channel (~2440.4 MHz) for 12 s and reads the frequency-hopping sequence. Enter a seed (if known) to confirm a per-link Net ID. Reports an RF link fingerprint, not an aircraft identity. First run of a session is slow (~87 s, one-time radio init); later runs ~18 s.">📡 ELRS hop fingerprint</button>'+
      '<input id="spElrsSeed" type="text" placeholder="seed (optional, e.g. 1120344)" title="the FHSS LCG seed to confirm against this dwell (from fhss_seed_scan.py). Leave blank for fingerprint only." style="width:11em;font-size:0.85em;padding:2px 6px"'+
      ' onchange="specSaveState()">'+
      '<input id="spElrsKeep" type="checkbox" title="save the dwell IQ to captures/ (default: keep, so you can re-analyse offline). Uncheck to discard after analysis." checked>'+
      '<label for="spElrsKeep" class="muted" style="font-size:0.8em">keep IQ</label>'+
      '</div>'+
    '<div id="spTuneNote" role="status"></div>'+
    '<div class="specctl"><span class="preset">Trace:</span>'+
      '<button class="chip on" id="tmlive" onclick="specSetMode(\'live\')">Live</button>'+
      '<button class="chip" id="tmmax" onclick="specSetMode(\'max\')">+ Max-hold</button>'+
      '<button class="chip" id="tmavg" onclick="specSetMode(\'avg\')">+ Average</button>'+
      '<button class="chip" onclick="specClearHold()">Clear</button>'+
      '<span class="preset">Save:</span>'+
      '<button class="chip" onclick="specSavePng()">PNG</button>'+
      '<button class="chip" onclick="specSaveCsv()" title="Latest raw sweep, without display smoothing">CSV (raw)</button>'+
      '<span class="muted" id="tmleg"></span></div>'+
    '<div class="specctl"><label>Display pace <select id="spPace" onchange="specSetPace(this.value)">'+
      '<option value="live">Live · every sweep</option><option value="steady">Steady · 1 s plot / 5 s list</option><option value="slow">Slow · 2 s plot / 10 s list</option></select></label>'+
      '<button class="btn" id="spHoldDisplay" onclick="specHoldDisplay()">Hold display</button>'+
      '<span class="muted" id="spDisplayStatus" role="status"></span></div>'+
    '<canvas id="spCanvas" class="speccv" onclick="specCanvasClick(event)" title="click a peak to focus/zoom on it"></canvas><div class="specax" id="spAxis"></div>'+
    '<div id="spStrength"></div>'+
    '<div id="spSignals" class="spsig"></div>'+
    '<div id="spAnalysis"></div>'+
    '<div class="wfhead"><b>Waterfall</b> <span class="muted">time flows downward — watch a signal hop or a band fill over time</span>'+
      '<button class="chip" id="wftoggle" onclick="specWfToggle()">Taller</button></div>'+
    '<canvas id="spWater" class="watercv"></canvas>'+
    '<div class="muted" style="margin-top:8px">Power vs frequency in the drone bands only. <b>Click a peak to focus/zoom on it</b>; ⊖ Zoom out or pick a band to widen. ▶ Live streams sweeps into the waterfall.</div>'+
    '</div>';
  specDisplay.reset(); specSyncDisplay(); specSyncBands();
  await specOnce();
  if(!specCurrent(mount))return;
  syncTrace();
  await specReadiness();
}
async function specReadiness(){
  const mount=specMountGen;
  const sdr=await specApi("/api/sdr?device="+encodeURIComponent(specDevice)); if(!specCurrent(mount))return;
  const r=sdr.readiness||{}, el=document.getElementById("specready"); if(!el)return;
  const radios=r.radios||[];
  // device picker when more than one radio is attached
  let picker="";
  if(radios.length>1){
    picker='<div class="specctl"><span class="preset">Radio:</span><select class="vsel" onchange="specSetDevice(this.value)">'+
      radios.map(rd=>'<option value="'+specEsc(rd.usb_id)+'"'+(rd.usb_id===(specDevice||r.usb_id)?" selected":"")+'>'+
        specEsc(rd.device)+(rd.live_capable?"":" (simulated)")+'</option>').join("")+'</select></div>';
  }
  let body;
  if(r.live_capable){body='<div class="rdy ok">● Ready for live spectrum capture via '+specEsc(r.backend_label||"SDR")+' — press ▶ Live.</div>';}
  else{
    let h='<div class="rdy"><b>Live capture not active.</b> ';
    if(r.sdr_detected){h+='Using '+specEsc(r.device||"SDR")+(r.usb_id?' ['+specEsc(r.usb_id)+']':'')+'. '+specEsc(r.install_hint||"Live capture for this device isn\\'t wired yet.");}
    else{h+='No SDR recognized.';
      if(r.unrecognized_usb&&r.unrecognized_usb.length)h+=' Unrecognized USB — is one of these your radio? '+r.unrecognized_usb.map(d=>'<code>'+specEsc(d.id)+'</code> '+specEsc(d.name)).join(", ");}
    body=h+'</div>';
  }
  el.innerHTML=picker+body;
}
function specSetDevice(id){specDevice=id; specClearNotes(); specResetRange(); specRefresh(); specReadiness();}
async function specOnce(){
  const gen=specGen, mount=specMountGen, owner=specSession;   // tie this sweep to this opening and the current radio/tuning
  let d;
  try{ d=await specApi("/api/spectrum?center="+Math.round(specCenter)+"&span="+Math.round(specSpan)+"&bins=256&owner="+encodeURIComponent(owner)+"&device="+encodeURIComponent(specDevice)); }
  catch(e){ if(!specCurrent(mount))specReleaseRetired(owner); return; }   // an unreadable reply may still have taken the lease
  if(!specCurrent(mount)){specReleaseRetired(owner);return;}   // closed or reopened mid-sweep: an owned sweep re-takes the lease, so give it back
  if(gen!==specGen)return;   // superseded by a radio/tune switch — drop it
  const b=document.getElementById("specsrc");
  if(d.error==="busy"){
    specStop();
    if(b)b.innerHTML='<span class="srcbusy">⛔ SDR BUSY</span> '+specEsc(d.note);
    return;
  }
  if(b){const live=d.source==="live";
    const tag=live?(d.controlled?'● LIVE · SDR under exclusive control':'● LIVE'):'◌ SIMULATED';
    b.innerHTML='<span class="'+(live?"srclive":"srcsim")+'">'+tag+'</span> '+specEsc(d.note);}
  specAccum(d); specDisplay.accept(d, performance.now()); specPresent();
  const ax=document.getElementById("spAxis");
  if(ax)ax.innerHTML='<span>'+(d.start_hz/1e6).toFixed(3)+'</span><span>'+(d.center_hz/1e6).toFixed(3)+' MHz</span><span>'+(d.stop_hz/1e6).toFixed(3)+'</span>';
}
function specSyncDisplay(){
  const select=document.getElementById("spPace"), button=document.getElementById("spHoldDisplay"), label=document.getElementById("spDisplayStatus");
  if(select)select.value=specDisplay.pace;
  if(button){button.textContent=specDisplay.paused?"Resume display":"Hold display";button.setAttribute("aria-pressed",String(specDisplay.paused));}
  if(label)label.textContent=specDisplay.paused?"Display held · capture/audio unaffected":
    (specDisplay.pace==="live"?"Every sweep · list stays still while hovered or focused":
      "Smoothed plot + "+(specDisplay.pace==="slow"?"8":"5")+" s peak hold · hover/focus rows to keep them still");
}
function specPresent(force=false){
  const list=document.getElementById("spSignals");
  const interacting=!!list&&(list.matches(":hover")||list.contains(document.activeElement));
  const result=specDisplay.present(performance.now(),interacting,force);
  if(result.plot){specLast=result.plot;drawSpectrum(result.plot);pushWaterfall(result.plot);renderStrength(result.plot);}
  if(result.list)renderSignals(result.list);
  specSyncDisplay();
}
function specSetPace(pace){specDisplay.setPace(pace);try{localStorage.setItem("wr.spec.pace",specDisplay.pace);}catch(e){} specPresent(true);specSyncDisplay();}
function specHoldDisplay(){specDisplay.paused=!specDisplay.paused;specPresent(true);specSyncDisplay();}
function renderStrength(d){
  const el=document.getElementById("spStrength"); if(!el)return;
  const snr=Math.max(0, d.peak.db - d.floor_db);
  const pct=Math.max(2, Math.min(100, snr/40*100));
  let label,cls;
  if(snr<6){label="No signal";cls="s0";}
  else if(snr<12){label="Weak";cls="s1";}
  else if(snr<25){label="Moderate";cls="s2";}
  else{label="Strong";cls="s3";}
  el.innerHTML='<div class="strhead">Signal strength <span class="muted">(strongest in view · '+(d.peak.freq_hz/1e6).toFixed(3)+' MHz)</span></div>'+
    '<div class="strbar"><div class="strfill '+cls+'" style="width:'+pct.toFixed(0)+'%"></div></div>'+
    '<div class="strval"><span class="'+cls+'">'+label+'</span> · '+snr.toFixed(1)+' dB over noise · peak '+d.peak.db+' dB</div>';
}
function renderSignals(d){
  const el=document.getElementById("spSignals"); if(!el)return;
  if(!d.signals||!d.signals.length){el.innerHTML='<span class="muted">No carriers above the noise floor in this range.</span>';return;}
  el.innerHTML='<div class="spsigh">Signals in view <span class="muted">— snapshot '+new Date().toLocaleTimeString()+' · click a row to tune</span></div>'+
    d.signals.map(s=>{
      const zoom=Math.max(0.5,(d.span_hz/1e6)/8);
      const attr=t=>specEsc(t).replace(/"/g,'&quot;');
      const profiles=Array.isArray(s.rf_candidates)?s.rf_candidates:[];
      const evidence=Array.isArray(s.drone)?s.drone:[];
      const note=s.identification_note||'Band overlap alone does not identify this signal.';
      const rf=profiles.length?'<span class="sprf" title="'+attr('Possible RF profiles on this band: '+profiles.join(', ')+'. '+note)+'">Possible RF profiles on this band: '+specEsc(profiles.join(', '))+'</span>':'';
      const drone=evidence.length?'<span class="spdrone" title="'+attr('Drone-specific evidence: '+evidence.join(', ')+'. '+note)+'">🛸 Drone evidence: '+specEsc(evidence.join(', '))+'</span>':'';
      const unidentified=s.identification==='unidentified'||!evidence.length;
      const identity=unidentified?'<span class="spidentity" title="'+attr(note)+'"><b>Unidentified</b> — '+(evidence.length?'identity not verified': 'no drone-specific evidence')+'</span>':'';
      return '<div class="spsigr" title="tune here" onclick="specPreset('+(s.freq_hz/1e6)+','+zoom+')">'+
        '<span class="spf">'+(s.freq_hz/1e6).toFixed(3)+' MHz</span>'+
        '<span class="spd">'+s.db+' dB</span>'+
        '<span class="spb">'+specEsc(s.band)+'</span>'+rf+drone+identity+'</div>';
    }).join("");
}
async function specAnalyze(freqHz){   // analog FPV video check, only offered in the 1.3 and 5.8 GHz video bands
  if(!specVideoBand(freqHz))return;
  const el=document.getElementById("spAnalysis"), mhz=(freqHz/1e6).toFixed(3), mount=specMountGen, gen=specGen, device=specDevice;
  if(el)el.innerHTML='<div class="rdy">📺 Checking '+mhz+' MHz for analog video…</div>';
  const r=await specApi("/api/analyze?freq="+Math.round(freqHz)+"&device="+encodeURIComponent(device));
  // a retune, radio change, close or re-render while it ran makes this result obsolete
  if(!el||!specCurrent(mount)||gen!==specGen||device!==specDevice||el!==document.getElementById("spAnalysis"))return;
  if(!r.available){el.innerHTML='<div class="rdy">📺 '+mhz+' MHz — '+specEsc(r.note||"unavailable")+'</div>';return;}
  const detail=r.video?('standard '+specEsc(r.standard)+' · line sync '+(r.line_rate_hz/1e3).toFixed(2)+' kHz'):'no video sync';
  el.innerHTML='<div class="rdy ok"><b>📺 '+(r.freq_hz/1e6).toFixed(3)+' MHz — '+specEsc(r.summary)+'</b><br>'+detail+'</div>';
}
async function specRelease(owner){try{await specPost("/api/spectrum/release",{owner:owner||specSession});}catch(e){}}
async function specDroneID(){
  const el=document.getElementById("spAnalysis"), center=specCenter, device=specDevice, mount=specMountGen;
  if(!el)return;
  if(!Number.isFinite(center)||!((center>=2400e6&&center<=2483.5e6)||(center>=5725e6&&center<=5850e6))){
    el.innerHTML='<div class="rdy">🛰 DroneID scan refused: tune the center to 2400–2483.5 MHz or 5725–5850 MHz (supported DJI bands). No capture performed.</div>';return;
  }
  el.innerHTML='<div class="rdy">🛰 Scanning '+((center-7.68e6)/1e6).toFixed(3)+'–'+((center+7.68e6)/1e6).toFixed(3)+' MHz (15.36 MSPS; independent of display span)…</div>';
  try{
    const r=await specApi("/api/droneid?device="+encodeURIComponent(device)+"&center="+encodeURIComponent(center));
    if(!specCurrent(mount)||center!==specCenter||device!==specDevice||el!==document.getElementById("spAnalysis"))return;
    if(!r.available){el.innerHTML='<div class="rdy">🛰 '+specEsc(r.note||r.error||"DroneID scan unavailable")+'</div>';return;}
    const range='Scanned '+(r.start_hz/1e6).toFixed(3)+'–'+(r.stop_hz/1e6).toFixed(3)+' MHz at '+(r.sample_rate/1e6).toFixed(2)+' MSPS; '+r.window_ms+' ms capture.<br>';
    if(r.droneid){el.innerHTML='<div class="rdy ok">'+range+'<b>🛰 DJI DroneID candidate</b> — ordered pilot pair found (ZC '+r.zc_peak+', OFDM '+r.metric+').<br>'+specEsc(r.note)+'</div>';}
    else if(r.detected){el.innerHTML='<div class="rdy">'+range+'<b>🛰 OFDM-like correlation</b> — no matching DroneID pilot pair. metric '+r.metric+'.<br>'+specEsc(r.note)+'</div>';}
    else{el.innerHTML='<div class="rdy">'+range+'🛰 No DroneID burst detected in this capture. '+specEsc(r.note)+'</div>';}
  }catch(e){if(specCurrent(mount)&&center===specCenter&&device===specDevice&&el===document.getElementById("spAnalysis"))el.innerHTML='<div class="rdy">🛰 DroneID scan failed. '+specEsc(e.message)+'</div>';}
}
// ELRS frequency-hopping fingerprint. Reads the hop SEQUENCE off the air and
// reports a per-link fingerprint; it never decodes a payload, so it is
// unaffected by the undocumented SX1280 long-interleaving payload coding.
//
// Wording: this identifies an RF LINK, not an aircraft. The text below says so
// and must keep saying so.
async function specElrsFhss(){
  const el=document.getElementById("spAnalysis"), device=specDevice, mount=specMountGen;
  if(!el)return;
  const sync=2440.4e6;   // the ELRS 2.4 GHz sync channel, pinned to every block start
  // Read the operator's seed + keep-IQ preferences (the inputs next to the button).
  const seedEl=document.getElementById("spElrsSeed");
  const seed=seedEl?seedEl.value.trim().replace(/^0x/i,""):'';
  const keepEl=document.getElementById("spElrsKeep");
  const keep=keepEl?keepEl.checked:false;
  if(!specCurrent(mount))return;
  // Timing measured on a B205mini: ~19 s once the radio is warm, but the FIRST
  // dwell of a session also pays one-time B200 initialisation and took ~87 s.
  // Saying "~20 s" there makes a working run look hung, so say both.
  el.innerHTML='<div class="rdy">📡 Dwelling '+(sync/1e6).toFixed(1)+' MHz for 12 s to read the hop sequence. '+
    (seed?('Confirming seed <code>'+specEsc(seed)+'</code>. '):'')+
    'About 20 s once warm; the first run of a session initialises the radio and can take up to 90 s. Leave it running.</div>';
  try{
    let url="/api/elrs/fhss?dwell_s=12&freq="+Math.round(sync)+"&device="+encodeURIComponent(device);
    if(seed)url+="&seed="+encodeURIComponent(seed);
    if(!keep)url+="&keep=0";
    const r=await specApi(url);
    // a retune, radio change, close or re-render while it ran makes this obsolete
    if(!specCurrent(mount)||device!==specDevice||el!==document.getElementById("spAnalysis"))return;
    if(r.error){el.innerHTML='<div class="rdy">📡 '+specEsc(r.error)+'</div>';return;}
    const head='Dwelled '+(r.duration_s||0)+' s at '+((r.center_freq_hz||0)/1e6).toFixed(3)+' MHz; '+
      (r.n_bursts||0)+' bursts, '+(r.n_dwells||0)+' channel dwells.<br>';
    if(!r.fingerprint_digest){
      el.innerHTML='<div class="rdy">'+head+'📡 No hop pattern recovered — no ELRS link in this dwell, '+
        'or the transmitter is off. This does not establish that nothing is present.</div>';return;}
    const sy=r.sync||{};
    // The Net ID is the headline — put it first if it's confirmed.
    let head2='';
    if(r.net_id){
      head2='<b>📡 Net ID <code style="font-size:1.1em">'+specEsc(r.net_id)+'</code></b> — the UID-seeded '+
        'hop-sequence seed, confirmed against this dwell. <b>This one IS stable across captures of the same link.</b><br>';
    }
    let body='📡 <b>ELRS 2.4 link — hop sequence read</b><br>'+head2+
      'Capture digest <code>'+specEsc(r.fingerprint_digest)+'</code> '+
      '<span class="muted">(this dwell only — not stable across captures; the seed above is the stable ID)</span><br>'+
      'Hop interval '+specEsc(r.hop_interval_ms)+' ms · '+specEsc(r.n_positions)+'/'+specEsc(r.seq_len)+
      ' sequence positions · '+(r.absolute?'absolute':'relative')+' phase<br>';
    if(sy.channel!=null){
      body+='Sync channel '+specEsc(sy.channel)+' (recurs every '+specEsc(sy.mean_gap_hops)+' hops) · '+
        specEsc(sy.elrs_generation||"generation unknown")+'<br>';}
    if(!r.net_id){
      body+='<span class="muted">No stable link ID: that needs the hop-sequence seed. '+
        'Recover it offline with <code>fhss_seed_scan.py</code> (two captures of the same link, centred near '+
        '2440.4 MHz), then enter it in the seed box above and dwell again to confirm it live.</span><br>';}
    if(r.seed_check&&r.seed_check.match===false&&seed){
      body+='<span style="color:#c66">⚠ Seed <code>'+specEsc(seed)+'</code> did NOT confirm: '+
        specEsc(r.seed_check.n_match)+'/'+specEsc(r.seed_check.n_checked)+' positions agree '+
        '(chance 10^'+specEsc(r.seed_check.log10_chance)+'). Wrong seed, wrong link, or a noisier dwell.</span><br>';}
    body+='<span class="muted">This is an identifier for a radio LINK, not an aircraft, and not a '+
      'hardware serial. Evidence: '+specEsc(r.evidence||"n/a")+'. '+(r.iq_kept===false?'IQ discarded (keep=0).':'')+'</span>';
    el.innerHTML='<div class="rdy '+(r.net_id?'ok':'')+'">'+head+body+'</div>';
  }catch(e){
    if(specCurrent(mount)&&device===specDevice&&el===document.getElementById("spAnalysis"))
      el.innerHTML='<div class="rdy">📡 ELRS hop fingerprint failed. '+specEsc(e.message)+'</div>';}
}
function specAccum(d){
  const key=d.start_hz+"-"+d.stop_hz+"-"+d.points.length;
  if(key!==specKey){specKey=key; specHold=d.points.map(p=>p[1]); specAvg=d.points.map(p=>p[1]); specAvgN=1; return;}
  for(let i=0;i<d.points.length;i++){const v=d.points[i][1];
    if(v>specHold[i])specHold[i]=v; specAvg[i]=(specAvg[i]*specAvgN+v)/(specAvgN+1);}
  specAvgN++;
}
function syncTrace(){["live","max","avg"].forEach(x=>{const b=document.getElementById("tm"+x);if(b)b.classList.toggle("on",x===specMode);});
  const leg=document.getElementById("tmleg");
  if(leg)leg.textContent=specMode==="max"?"gold = peak seen since Clear":(specMode==="avg"?"green = running average":"");}
function specSetMode(m){specMode=m; syncTrace(); specSaveState(); if(specLast)drawSpectrum(specLast);}
function specClearHold(){const latest=specDisplay.latest;specDisplay.reset();if(latest)specDisplay.accept(latest,performance.now());specKey=""; specHold=null; specAvg=null; specAvgN=0; specResetRange(); clearWater(); specPresent(true);}
/* persist the tuned range + trace mode across visits */
function specSaveState(){try{localStorage.setItem("wr.spec",JSON.stringify({c:specCenter,s:specSpan,m:specMode}));}catch(e){}}
function specLoadState(){try{specDisplay.setPace(localStorage.getItem("wr.spec.pace")||"steady");}catch(e){} try{const j=JSON.parse(localStorage.getItem("wr.spec")||"null");
  if(j&&j.c>0&&j.s>0){const v=specFit(j.c,j.s); specCenter=v.center; specSpan=v.span; if(["live","max","avg"].includes(j.m))specMode=j.m;}}catch(e){}}   // an older saved view outside the drone bands is moved into one
/* save the current view */
function specStamp(){const d=specLast,f=d?(d.center_hz/1e6).toFixed(3)+"MHz-":"",t=new Date(),z=n=>String(n).padStart(2,"0");
  return f+t.getFullYear()+z(t.getMonth()+1)+z(t.getDate())+"-"+z(t.getHours())+z(t.getMinutes())+z(t.getSeconds());}
function specDownload(url,name){const a=document.createElement("a");a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();}
function specSavePng(){const cv=document.getElementById("spCanvas"); if(!cv)return;
  specDownload(cv.toDataURL("image/png"),"waveraider-spectrum-"+specStamp()+".png");}
function csvq(s){s=""+s; return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}
function specSaveCsv(){const d=specDisplay.latest||specLast; if(!d)return;
  const L=["# Wave Raider spectrum snapshot",
    "# generated,"+new Date().toISOString(),
    "# center_mhz,"+(d.center_hz/1e6).toFixed(6),
    "# span_mhz,"+(d.span_hz/1e6).toFixed(6),
    "# source,"+d.source+(d.device?" ("+d.device+")":""),
    "# floor_db,"+d.floor_db,"","freq_mhz,power_db,band"];
  (d.signals||[]).forEach(s=>L.push((s.freq_hz/1e6).toFixed(6)+","+s.db+","+csvq(s.band)));
  L.push("","# full trace","freq_mhz,power_db");
  (d.points||[]).forEach(p=>L.push((p[0]/1e6).toFixed(6)+","+p[1]));
  const url=URL.createObjectURL(new Blob([L.join("\n")],{type:"text/csv"}));
  specDownload(url,"waveraider-spectrum-"+specStamp()+".csv"); setTimeout(()=>URL.revokeObjectURL(url),1500);}
function specRange(d){
  // Auto-scale the dB axis to the data (rtl_power ~0-20 dB, simulated ~-95..-8),
  // but LATCH it per tuned window and only ever widen it. A per-frame rescale made
  // the scrolling waterfall recolour old rows against a new scale — it flashed as
  // peaks moved. Expand-only + reset-on-retune keeps the scale stable.
  const key=d.start_hz+"-"+d.stop_hz;
  let lo=Infinity,hi=-Infinity;
  for(const p of d.points){if(p[1]<lo)lo=p[1]; if(p[1]>hi)hi=p[1];}
  const ov=specMode==="max"?specHold:(specMode==="avg"?specAvg:null);
  if(ov&&ov.length===d.points.length){for(const v of ov){if(v<lo)lo=v; if(v>hi)hi=v;}}
  if(d.display_hold){for(const v of d.display_hold){if(v<lo)lo=v;if(v>hi)hi=v;}}
  if(!isFinite(lo)){lo=-100;hi=-20;}
  const pad=Math.max(4,(hi-lo)*0.08);
  lo=Math.floor((lo-pad)/5)*5; hi=Math.ceil((hi+pad)/5)*5;   // round to 5 dB
  if(key!==specRngKey||specRngLo===null){specRngKey=key; specRngLo=lo; specRngHi=hi;}
  else{specRngLo=Math.min(specRngLo,lo); specRngHi=Math.max(specRngHi,hi);}  // widen only
  if(specRngHi-specRngLo<20){const m=(specRngHi+specRngLo)/2; specRngLo=m-10; specRngHi=m+10;}
  return [specRngLo,specRngHi];
}
function specResetRange(){specRngKey=""; specRngLo=null; specRngHi=null;}
// Canvas traces use the same semantic colors as the surrounding controls.
function spectrumColor(token,fallback){
  return typeof getComputedStyle==='function'?(getComputedStyle(document.documentElement).getPropertyValue('--'+token).trim()||fallback):fallback;
}
function drawSpectrum(d){
  const cv=document.getElementById("spCanvas"); if(!cv)return;
  const W=cv.clientWidth||600, H=180; cv.width=W; cv.height=H;
  const ctx=cv.getContext("2d"), n=d.points.length;
  const span=(d.stop_hz-d.start_hz)||1;
  const rng=specRange(d), dbMin=rng[0], dbMax=rng[1];
  const y=v=>H-((v-dbMin)/(dbMax-dbMin))*H, x=i=>i/(n-1)*W, xf=hz=>(hz-d.start_hz)/span*W;
  ctx.fillStyle=spectrumColor("bg","#111820"); ctx.fillRect(0,0,W,H);
  // band-plan overlay: alternating faint columns + labels
  (d.bands||[]).forEach((b,k)=>{const x0=xf(b.start_hz),x1=xf(b.stop_hz);
    ctx.fillStyle=k%2?"rgba(122,162,247,.05)":"rgba(122,162,247,.10)"; ctx.fillRect(x0,0,x1-x0,H);
    ctx.strokeStyle=spectrumColor("line","#364754"); ctx.beginPath();ctx.moveTo(x0,0);ctx.lineTo(x0,H);ctx.stroke();
    if(x1-x0>44){ctx.fillStyle=spectrumColor("accent","#8bcbff");ctx.font="10px ui-monospace,monospace";ctx.save();
      ctx.beginPath();ctx.rect(x0,0,x1-x0,H);ctx.clip();ctx.fillText(b.name,x0+4,12);ctx.restore();}});
  // horizontal dB grid + min/max labels (axis auto-scales to the data)
  ctx.strokeStyle=spectrumColor("line","#364754"); ctx.lineWidth=1;
  for(let k=0;k<=4;k++){const yy=H-k/4*H;ctx.beginPath();ctx.moveTo(0,yy);ctx.lineTo(W,yy);ctx.stroke();}
  ctx.fillStyle=spectrumColor("soft","#aab8c5");ctx.font="10px ui-monospace,monospace";
  ctx.fillText(dbMax.toFixed(0)+" dB",4,11); ctx.fillText(dbMin.toFixed(0)+" dB",4,H-4);
  const fy=y(d.floor_db); ctx.strokeStyle=spectrumColor("line2","#718599"); ctx.setLineDash([4,4]);
  ctx.beginPath();ctx.moveTo(0,fy);ctx.lineTo(W,fy);ctx.stroke(); ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(0,H);
  for(let i=0;i<n;i++)ctx.lineTo(x(i),y(d.points[i][1]));
  ctx.lineTo(W,H); ctx.closePath(); ctx.fillStyle="rgba(122,162,247,.22)"; ctx.fill();
  ctx.beginPath();
  for(let i=0;i<n;i++){const yy=y(d.points[i][1]); i?ctx.lineTo(x(i),yy):ctx.moveTo(x(i),yy);}
  ctx.strokeStyle=spectrumColor("accent","#8bcbff"); ctx.lineWidth=1.5; ctx.stroke();
  (d.signals||[]).forEach(s=>{const sx=xf(s.freq_hz),sy=y(s.db);
    ctx.fillStyle=spectrumColor("warn","#f2c66d");ctx.beginPath();ctx.moveTo(sx,sy-6);ctx.lineTo(sx-4,sy-13);ctx.lineTo(sx+4,sy-13);ctx.closePath();ctx.fill();});
  if(d.display_hold){ctx.beginPath();d.display_hold.forEach((v,i)=>{i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v));});
    ctx.strokeStyle=spectrumColor("warn","#f2c66d");ctx.lineWidth=1;ctx.stroke();}
  if(specMode!=="live"){const ov=specMode==="max"?specHold:specAvg;
    if(ov&&ov.length===n){ctx.beginPath();
      for(let i=0;i<n;i++){const yy=y(ov[i]); i?ctx.lineTo(x(i),yy):ctx.moveTo(x(i),yy);}
      ctx.strokeStyle=specMode==="max"?spectrumColor("warn","#f2c66d"):spectrumColor("ok","#8dd6b0"); ctx.lineWidth=1;
      ctx.setLineDash(specMode==="avg"?[3,3]:[]); ctx.stroke(); ctx.setLineDash([]);}}
  const pf=(d.peak.freq_hz-d.start_hz)/(d.stop_hz-d.start_hz)*W, py=y(d.peak.db);
  ctx.fillStyle=spectrumColor("crit","#ff9a9a"); ctx.beginPath(); ctx.arc(pf,py,3,0,7); ctx.fill();
  ctx.fillStyle=spectrumColor("ink","#e6edf3"); ctx.font="11px ui-monospace,monospace";
  ctx.fillText((d.peak.freq_hz/1e6).toFixed(3)+" MHz "+d.peak.db+" dB", Math.min(pf+6,W-150), Math.max(12,py-5));
}
function heat(t){const s=[[0,0,0],[0,0,140],[0,160,200],[0,190,60],[230,220,0],[230,60,40],[255,255,255]];
  t=Math.max(0,Math.min(1,t)); const p=t*(s.length-1), i=Math.floor(p), f=p-i, a=s[i], c=s[Math.min(i+1,s.length-1)];
  return 'rgb('+Math.round(a[0]+(c[0]-a[0])*f)+','+Math.round(a[1]+(c[1]-a[1])*f)+','+Math.round(a[2]+(c[2]-a[2])*f)+')';}
function specWfToggle(){specWfTall=!specWfTall;
  const b=document.getElementById("wftoggle"); if(b)b.textContent=specWfTall?"Shorter":"Taller";
  if(specLast)pushWaterfall(specLast);}
function pushWaterfall(d){
  const cv=document.getElementById("spWater"); if(!cv)return;
  const W=cv.clientWidth||600, H=specWfTall?360:160, ctx=cv.getContext("2d");
  if(cv.width!==W||cv.height!==H){cv.width=W;cv.height=H;cv.style.height=H+"px";ctx.fillStyle="#0b0d10";ctx.fillRect(0,0,W,H);}
  ctx.drawImage(cv,0,1);
  const n=d.points.length, rng=specRange(d), dbMin=rng[0], dbMax=rng[1];
  for(let px=0;px<W;px++){const i=Math.min(n-1,Math.round(px/(W-1)*(n-1)));
    ctx.fillStyle=heat((d.points[i][1]-dbMin)/(dbMax-dbMin)); ctx.fillRect(px,0,1,1);}
}
function clearWater(){const cv=document.getElementById("spWater"); if(cv){const c=cv.getContext("2d");c.fillStyle="#0b0d10";c.fillRect(0,0,cv.width,cv.height);}}
// A radio/tune change invalidates in-flight sweeps and re-sweeps the new range NOW
// (drop any pending timer/frame so the tune takes effect immediately, not next cycle).
function specRefresh(){specGen++; specKey="";specDisplay.reset();specLast=null;specSyncDisplay();
  if(specTimer){clearTimeout(specTimer);specTimer=null;}
  clearWater();
  if(specRunning)specLoop(specMountGen); else specOnce();}
// Every tune goes through specSetView, which keeps the view inside a drone band and shows what was applied.
function specSetView(centerHz,spanHz){
  const v=specFit(centerHz,spanHz); specCenter=v.center; specSpan=v.span;
  const ci=document.getElementById("spCenter"),si=document.getElementById("spSpan");
  if(ci)ci.value=+(specCenter/1e6).toFixed(3); if(si)si.value=+(specSpan/1e6).toFixed(3);
  specClearNotes(); specSyncBands(); specSaveState(); specRefresh();}
function specClearNotes(){for(const id of ["spTuneNote","spAnalysis"]){const el=document.getElementById(id); if(el)el.innerHTML="";}}   // results for the old view are obsolete
function specApply(){const c=parseFloat(document.getElementById("spCenter").value),s=parseFloat(document.getElementById("spSpan").value);
  const wantC=c>0?c*1e6:specCenter, wantS=s>0?s*1e6:specSpan;
  specSetView(wantC, wantS);
  const el=document.getElementById("spTuneNote"), band=DRONE_BANDS.find(b=>b[0]===specFit(specCenter,specSpan).band);
  if(el)el.innerHTML=(Math.abs(specCenter-wantC)>1000||Math.abs(specSpan-wantS)>1000)?   // say when the typed view was moved
    '<div class="rdy">'+(wantC/1e6).toFixed(3)+' MHz ± '+(wantS/2e6).toFixed(3)+' MHz doesn\'t fit in the drone bands, so this shows '+
    (specCenter/1e6).toFixed(3)+' MHz ± '+(specSpan/2e6).toFixed(3)+' MHz in the '+specEsc(band[1])+' band ('+band[2]+'–'+band[3]+' MHz).</div>':'';
  specAnalyze(specCenter);}   // Tune = focus the sweep here, and check for analog video in the video bands
function specPreset(c,s){specSetView(c*1e6, s*1e6);}
function specTune(centerHz,spanHz){specSetView(centerHz, Math.max(500000,Math.round(spanHz)));}
function specZoomOut(){const d=specLast; specTune(specCenter, (d?(d.stop_hz-d.start_hz):specSpan)*2);}
function specCanvasClick(evt){
  const cv=document.getElementById("spCanvas"), d=specLast; if(!cv||!d)return;
  const rect=cv.getBoundingClientRect();
  const frac=Math.max(0,Math.min(1,(evt.clientX-rect.left)/rect.width));
  let target=d.start_hz+frac*(d.stop_hz-d.start_hz);
  // snap to the nearest detected peak so it lands centred, not on the click jitter
  if(d.signals&&d.signals.length){
    let best=null,bd=Infinity;
    for(const s of d.signals){const dd=Math.abs(s.freq_hz-target); if(dd<bd){bd=dd;best=s;}}
    if(best&&bd<(d.stop_hz-d.start_hz)*0.06) target=best.freq_hz;
  }
  specTune(target, (d.stop_hz-d.start_hz)/4);   // centre + zoom ~4x on the clicked peak
  specAnalyze(target);                          // ...and check it for analog video in the video bands
}
function specStop(){specRunning=false; if(specTimer){clearTimeout(specTimer);specTimer=null;}
  const b=document.getElementById("spRun"); if(b)b.textContent="▶ Live";}
async function specLoop(mount){                  // self-paced: next sweep starts only after this one finishes
  if(!specRunning||!specCurrent(mount))return;
  await specOnce();
  if(specRunning&&specCurrent(mount)) specTimer=setTimeout(()=>specLoop(mount), 100);
}
async function specToggle(){const btn=document.getElementById("spRun"), mount=specMountGen, owner=specSession;
  if(specRunning){specStop(); await specRelease(owner); if(!specCurrent(mount))return; specGen++; specOnce();}
  else{
    let r;
    try{ r=await specPost("/api/spectrum/acquire",{owner:owner,center:Math.round(specCenter),span:Math.round(specSpan),device:specDevice}); }
    catch(e){ if(owner!==specSession||!specRunning)specRelease(owner); return; }   // unreadable reply: the server may still have granted it
    if(!specCurrent(mount)){if(r.ok)specReleaseRetired(owner);return;}   // closed or reopened while acquiring: don't keep the radio
    if(!r.ok){const b=document.getElementById("specsrc");if(b)b.innerHTML='<span class="srcbusy">⛔ SDR BUSY</span> '+specEsc(r.reason||"controlled by another session");return;}
    specRunning=true; if(btn)btn.textContent="■ Stop (SDR held)"; specLoop(mount);}}
