"""Fake-clock checks for display pacing, independent of capture/audio."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.fixture
def js():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for Spectrum display tests')
    source = (Path(__file__).parents[1] / 'waveraider/spectrum_display.js').read_text()
    setup = '''
const assert=require('node:assert/strict');
const data=(db=-30, center=100, device='radio')=>({device,source:'live',start_hz:center-1,
stop_hz:center+1,center_hz:center,points:[[center-1,-90],[center,db],[center+1,-90]],
peak:{freq_hz:center,db},floor_db:-90,signals:[{freq_hz:center+.1,db:-40},{freq_hz:center,db}]});
const display=new SpectrumDisplay();
'''
    def run(body):
        subprocess.run([node, '-e', source + setup + body], check=True, capture_output=True, text=True)
    return run


def test_fast_capture_has_slow_display_and_stable_list_order(js):
    js('''let plots=0,lists=0;
for(let t=0;t<10000;t+=100){display.accept(data(t%200?-60:-30),t);const r=display.present(t);
 if(r.plot)plots++;
 if(r.list){lists++;assert.ok(r.list.signals[0].freq_hz<r.list.signals[1].freq_hz);}}
assert.equal(plots,10);assert.equal(lists,2);assert.equal(display.latest.points[1][1],-60);
''')


def test_hold_freezes_display_but_keeps_receiving_and_resume_is_current(js):
    js('''display.accept(data(),0);const first=display.present(0).plot;
display.paused=true;display.accept(data(-60),6000);
assert.deepEqual(display.present(6000,true,true),{});assert.equal(display.frame,first);
assert.equal(display.latest.points[1][1],-60);
display.paused=false;const r=display.present(6000,false,true);
assert.notEqual(r.plot,first);assert.equal(r.list.signals[0].db,-60);
''')


def test_retune_or_device_switch_clears_old_hold_and_unpauses(js):
    js('''display.accept(data(),0);display.present(0);display.paused=true;
display.accept(data(-70,200),100);
assert.equal(display.paused,false);assert.equal(display.present(100).plot.peak.freq_hz,200);
assert.equal(display.hold[1],-70);
display.paused=true;display.accept(data(-80,200,'other'),200);
assert.equal(display.paused,false);assert.equal(display.present(200).plot.peak.db,-80);
''')


def test_transient_peak_holds_then_decays_and_raw_data_is_untouched(js):
    js('''const input=data(-30);display.accept(input,0);display.present(0);
display.accept(data(-80),4000);const r=display.present(4000);
assert.equal(r.plot.display_hold[1],-30);assert.ok(r.plot.points[1][1]>-80);
assert.equal(display.latest.points[1][1],-80);assert.equal(input.points[1][1],-30);
display.accept(data(-80),6000);assert.equal(display.hold[1],-36);
display.accept(data(-80),20000);assert.equal(display.hold[1],-80);
''')


def test_hover_or_keyboard_focus_prevents_row_replacement(js):
    js('''display.accept(data(),0);assert.ok(display.present(0,true).list);
display.accept(data(-60),6000);assert.equal(display.present(6000,true).list,undefined);
assert.ok(display.present(6100,false).list);
''')


def test_live_and_slow_paces_and_invalid_preference(js):
    js('''display.setPace('slow');display.accept(data(),0);display.present(0);
display.accept(data(-80),1000);assert.deepEqual(display.present(1000),{});
assert.ok(display.present(2000).plot);assert.equal(display.present(5000).list,undefined);
display.setPace('live');const r=display.present(5001);
assert.equal(r.plot.points[1][1],-80);assert.equal(r.plot.display_hold,null);assert.ok(r.list);
display.setPace('invalid');assert.equal(display.pace,'live');
''')
