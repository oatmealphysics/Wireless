"""Scan boundaries, HTTP validation and the actual browser scan function."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from waveraider import bladerf_sweep, droneid, spectrum
from waveraider.web import Handler

MODULE = Path(spectrum.__file__).with_name('inspect_rf.js').read_text()


@pytest.mark.parametrize('center', [None, '', 'oops', float('nan'), float('inf'),
                                   -1, 101.1e6, 2399999999, 2483500001,
                                   5724999999, 5850000001, 1e20])
def test_invalid_center_never_touches_radio(monkeypatch, center):
    monkeypatch.setattr(spectrum, 'source_status', lambda device: pytest.fail('radio queried'))
    with pytest.raises(ValueError):
        spectrum.scan_droneid('blade', center)


@pytest.mark.parametrize('center', [2400e6, 2437e6, 2483.5e6, 5725e6, 5800e6, 5850e6])
def test_capture_uses_tuner_and_reports_full_range(monkeypatch, center):
    monkeypatch.setattr(spectrum, 'source_status', lambda device: {
        'mode': 'live', 'backend': 'bladerf', 'device': device})
    calls = []
    def capture(freq, rate, count):
        calls.append((freq, rate, count))
        return np.zeros(8192, dtype=complex), rate
    monkeypatch.setattr(bladerf_sweep, 'capture_iq', capture)
    result = spectrum.scan_droneid('blade', center)
    assert calls == [(center, 15360000, 10752000)]      # 700 ms: longer than the ~600 ms burst period
    assert result['center_hz'] == center
    assert result['start_hz'] == center - 7680000
    assert result['stop_hz'] == center + 7680000
    assert result['sample_rate'] == 15360000
    assert result['available'] and not result['droneid']


@pytest.mark.parametrize('query', ['', '?center=', '?center=abc', '?center=NaN',
                                   '?center=Infinity', '?center=-1', '?center=101100000'])
def test_http_rejects_invalid_center_without_capture(monkeypatch, query):
    monkeypatch.setattr(spectrum, 'scan_droneid', lambda *args: pytest.fail('capture attempted'))
    responses = []
    handler = SimpleNamespace(path='/api/droneid' + query, headers={'X-WaveRaider-Request': '1'},
                              _send=lambda code, body: responses.append((code, json.loads(body))))
    Handler.do_GET(handler)
    code, body = responses[0]
    assert code == 400 and body['available'] is False and body['error']


def test_http_passes_selected_device_and_center(monkeypatch):
    calls = []
    monkeypatch.setattr(spectrum, 'scan_droneid',
                        lambda *args: calls.append(args) or {'available': True})
    handler = SimpleNamespace(path='/api/droneid?device=2cf0%3A5250&center=5800000000', headers={'X-WaveRaider-Request': '1'},
                              _send=lambda code, body: None)
    Handler.do_GET(handler)
    assert calls == [('2cf0:5250', 5800000000)]


@pytest.mark.parametrize('center, available, detected, candidate', [
    (101.1e6, True, False, False), (2437e6, True, False, False),
    (5800e6, True, True, True), (2437e6, True, True, False),
    (2437e6, False, False, False)])
def test_browser_tuning_refusal_range_and_rtl_message(center, available, detected, candidate):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for browser scan regression')
    fn = MODULE.split('async function specDroneID(){', 1)[1].split('function specAccum', 1)[0]
    esc = re.search(r'function specEsc\(t\).*', MODULE).group(0)
    esc += '\nlet specMountGen=0;\n' + re.search(r'function specCurrent\(mount\).*', MODULE).group(0)
    response = dict(available=available, detected=detected, droneid=candidate,
                    start_hz=center-7680000, stop_hz=center+7680000,
                    sample_rate=15360000, window_ms=260.4, zc_peak=0.9, metric=0.9,
                    note='Use the bladeRF.' if not available else 'Experimental scan.')
    js = ('let specCenter=' + str(center) + ';let specDevice="RTL";const specHost={active:()=>true};'
          'const el={innerHTML:""};const document={getElementById:()=>el};let calls=[];'
          'const specApi=async u=>{calls.push(u);return ' + json.dumps(response) + ';};\n' + esc +
          '\nasync function specDroneID(){' + fn +
          '\nspecDroneID().then(()=>process.stdout.write(JSON.stringify({calls,html:el.innerHTML})));')
    result = json.loads(subprocess.check_output([node, '-e', js], text=True))
    if center == 101.1e6:
        assert result['calls'] == [] and 'refused' in result['html']
        assert 'No DroneID' not in result['html']
    else:
        assert float(result['calls'][0].split('&center=')[1]) == center
        if not available:
            assert 'Use the bladeRF' in result['html']
        else:
            assert f'{(center-7680000)/1e6:.3f}–{(center+7680000)/1e6:.3f} MHz' in result['html']
            assert '15.36 MSPS' in result['html']
            if candidate:
                assert 'DJI DroneID candidate' in result['html']
            elif detected:
                assert 'OFDM-like correlation' in result['html']
            else:
                assert 'No DroneID burst detected in this capture' in result['html']


def test_quiet_scan_says_quiet_is_not_absence(monkeypatch):
    monkeypatch.setattr(spectrum, 'source_status', lambda device: {
        'mode': 'live', 'backend': 'bladerf', 'device': device})
    monkeypatch.setattr(bladerf_sweep, 'capture_iq',
                        lambda *args: (np.zeros(8192, dtype=complex), 15360000))
    monkeypatch.setattr(droneid, 'detect', lambda *args: {
        'detected': True, 'droneid': False, 'note': 'OFDM-like correlation.'})
    result = spectrum.scan_droneid('blade', 2437e6)
    assert 'does not mean no drone is present' in result['note']
    assert 'Continuous monitoring is not implemented' in result['note']
