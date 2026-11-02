"""Execute the served signal renderer against backend data (Node.js required)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from waveraider import spectrum

MODULE = Path(spectrum.__file__).with_name('inspect_rf.js').read_text()


@pytest.fixture
def render():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute the served JavaScript renderer')
    esc = re.search(r'function specEsc\(t\).*', MODULE).group(0)
    renderer = MODULE.split('function renderSignals(d){', 1)[1].split('async function specAnalyze', 1)[0]

    def run(signals):
        js = ('const el={innerHTML:""}; const document={getElementById:()=>el};'
              'const specHost={active:()=>true};\n' + esc + '\nfunction renderSignals(d){' + renderer +
              '\nrenderSignals(' + json.dumps({'span_hz': 20e6, 'signals': signals}) + ');'
              '\nprocess.stdout.write(el.innerHTML);')
        return subprocess.check_output([node, '-e', js], text=True)
    return run


@pytest.mark.parametrize('center', [915e6, 2437e6, 5805e6])
def test_backend_profiles_render_as_unidentified_hints(render, center):
    points = [[center-1e6, -95], [center, -35], [center+1e6, -95]]
    signals = spectrum._find_signals(points, -95, 20e6)
    assert signals[0]['rf_candidates'] and signals[0]['drone'] == []
    html = render(signals)
    assert 'Possible RF profiles on this band:' in html
    assert '<b>Unidentified</b> — no drone-specific evidence' in html
    assert signals[0]['identification_note'] in html
    assert 'class="spdrone"' not in html and '🛸' not in html
    assert 'onclick="specPreset(' in html  # row tuning preserved


def test_explicit_drone_evidence_is_separate_from_reference_hints(render):
    html = render([{'freq_hz': 2437e6, 'db': -35, 'band': 'shared',
                    'rf_candidates': ['reference only'], 'drone': ['pilot-pair candidate'],
                    'identification': 'unidentified', 'identification_note': 'No decoded identity.'}])
    assert 'class="sprf"' in html and 'class="spdrone"' in html
    assert 'Drone evidence: pilot-pair candidate' in html
    assert '<b>Unidentified</b> — identity not verified' in html
    assert 'no drone-specific evidence' not in html
    assert 'confirmed' not in html.lower()


def test_missing_metadata_stays_unidentified_and_tooltips_escape_quotes(render):
    base = {'freq_hz': 915e6, 'db': -35, 'band': 'shared'}
    assert '<b>Unidentified</b>' in render([base])
    html = render([dict(base, rf_candidates=['<profile>'],
                        identification_note='Not "verified" <evidence>')])
    assert '&quot;verified&quot; &lt;evidence>' in html
    assert '<profile>' not in html


def test_signals_never_offer_a_protocol_hand_off(render):
    html = render([{'freq_hz': 433.92e6, 'db': -40, 'band': 'ISM', 'protocol': 'ook'}])   # an older backend's hint
    assert '⇢' not in html and 'protocol survey' not in html and 'specToProtocol' not in html
    assert 'click a row to tune' in html and 'onclick="specPreset(' in html


def test_only_drone_band_presets_remain():
    for general in ('FM 88–108', 'Airband', 'VHF 144–174', '433 ISM', '915 ISM', 'PRESETS', 'Bands:'):
        assert general not in MODULE
    for drone in ('868 ctrl', '900 ctrl', '1.3 video', '2.4 GHz', '5.8 video', 'Experimental DroneID scan'):
        assert drone in MODULE
    assert 'openProtocol' not in MODULE and 'protocolLabel' not in MODULE


def test_drawer_has_no_audio_listen():
    for gone in ('specListen', '/api/audio', '🔊', '🎧', 'Listen', 'specAudio'):
        assert gone not in MODULE


def _drawer_tuning(node, script):
    head = MODULE.split('const DRONE_BANDS=', 1)[1].split('\nasync function specMount', 1)[0]
    tuning = MODULE.split('function specSetView(', 1)[1].split('\nfunction specZoomOut', 1)[0]
    analyze = MODULE.split('async function specAnalyze(', 1)[1].split('\nasync function specRelease', 1)[0]
    load = 'function specLoadState(' + MODULE.split('function specLoadState(', 1)[1].split('\n/* save the current view */', 1)[0]
    js = ('const els={}; const el=id=>els[id]||(els[id]={id,value:"",innerHTML:"",attrs:{},cls:new Set(),'
          'classList:{toggle(c,on){on?this._o.cls.add(c):this._o.cls.delete(c);}},setAttribute(k,v){this.attrs[k]=v;}});'
          'const document={getElementById:id=>{const e=el(id); e.classList._o=e; return e;}};'
          'let saved=null; const localStorage={getItem:k=>k==="wr.spec"?saved:null,setItem(){}};'
          'let specCenter=2442000000, specSpan=60000000, specMode="live", specMountGen=1, specGen=0, specDevice="", refreshes=0; const apiCalls=[];'
          'const specDisplay={setPace(){}}; function specSaveState(){} function specRefresh(){refreshes++; specGen++;}'
          'function specCurrent(){return true;} function specEsc(t){return String(t);}'
          'let apiReply=async()=>({available:false,note:"no SDR"}); async function specApi(url){apiCalls.push(url); return apiReply(url);}\n'
          'const DRONE_BANDS=' + head + '\nfunction specSetView(' + tuning + '\nasync function specAnalyze(' + analyze +
          '\n' + load + '\nconst on=()=>DRONE_BANDS.filter(b=>el("spBand"+b[0].replace(".","_")).cls.has("on")).map(b=>b[0]);\n'
          '(async()=>{' + script + '})();')
    return json.loads(subprocess.check_output([node, '-e', js], text=True))


@pytest.fixture
def node():
    path = shutil.which('node')
    if not path:
        pytest.skip('Node.js is required to execute the drawer tuning')
    return path


def test_band_tabs_follow_tuning_and_views_stay_in_the_band(node):
    out = _drawer_tuning(node, '''
      const r={};
      specPreset(5800,60); r.preset=[on(), specCenter, specSpan, el("spBand5_8").attrs["aria-pressed"], el("spBand2_4").attrs["aria-pressed"]];
      specTune(101.1e6, 2e6); r.fm=[on(), el("spCenter").value, el("spSpan").value];
      el("spCenter").value="3000"; el("spSpan").value="100"; specApply(); r.apply=[on(), specCenter, specSpan];
      r.refreshes=refreshes;
      process.stdout.write(JSON.stringify(r));''')
    assert out['preset'] == [['5.8'], 5800e6, 60e6, 'true', 'false']
    assert out['fm'] == [['868'], 851, 2]
    assert out['apply'] == [['2.4'], 2453.5e6, 60e6]
    assert out['refreshes'] == 3


def test_tune_says_when_a_typed_view_is_moved_into_a_band(node):
    out = _drawer_tuning(node, '''
      const r={};
      el("spCenter").value="101.1"; el("spSpan").value="2"; specApply(); r.moved=el("spTuneNote").innerHTML;
      el("spCenter").value="2437"; el("spSpan").value="20"; specApply(); r.inside=el("spTuneNote").innerHTML;
      process.stdout.write(JSON.stringify(r));''')
    assert "101.100 MHz ± 1.000 MHz doesn't fit in the drone bands" in out['moved']
    assert "shows 851.000 MHz ± 1.000 MHz in the 868 ctrl band (850–870 MHz)" in out['moved']
    assert out['inside'] == ''


def test_moved_note_survives_the_video_check_in_a_video_band(node):
    # 5930 MHz ± 30 runs past the top of the 5.8 GHz video band, so it's moved down inside it, and the
    # video check that follows must not replace the note saying so.
    out = _drawer_tuning(node, '''
      el("spCenter").value="5930"; el("spSpan").value="60"; specApply();
      await new Promise(r=>setTimeout(r,0));
      process.stdout.write(JSON.stringify({note:el("spTuneNote").innerHTML, analysis:el("spAnalysis").innerHTML, center:specCenter}));''')
    assert out['center'] == 5915e6
    assert "5930.000 MHz ± 30.000 MHz doesn't fit in the drone bands" in out['note'] and "5.8 video band" in out['note']
    assert "5915.000 MHz — no SDR" in out['analysis']


def test_a_video_check_reply_for_an_old_view_is_dropped(node):
    out = _drawer_tuning(node, '''
      let release; apiReply=()=>new Promise(r=>{release=r;});
      specPreset(5800,60); const pending=specAnalyze(5806e6);
      const checking=el("spAnalysis").innerHTML;
      specPreset(2442,60);                                  // retune while the check is still running
      release({available:true,freq_hz:5806e6,summary:"analog video",video:true,standard:"PAL",line_rate_hz:15625});
      await pending;
      process.stdout.write(JSON.stringify({checking, after:el("spAnalysis").innerHTML}));''')
    assert 'Checking 5806.000 MHz' in out['checking'] and out['after'] == ''


def test_a_saved_view_outside_the_drone_bands_is_moved_into_one(node):
    out = _drawer_tuning(node, '''
      saved=JSON.stringify({c:101100000,s:2000000,m:"max"}); specLoadState();
      process.stdout.write(JSON.stringify([specCenter, specSpan, specMode]));''')
    assert out == [851e6, 2e6, 'max']


def test_video_check_only_runs_in_the_analog_video_bands(node):
    out = _drawer_tuning(node, '''
      for (const f of [2437e6, 915e6, 866e6, 1280e6, 5806e6]) await specAnalyze(f);
      process.stdout.write(JSON.stringify(apiCalls));''')
    assert [c.split('freq=')[1].split('&')[0] for c in out] == ['1280000000', '5806000000']
