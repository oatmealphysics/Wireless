from datetime import datetime, timezone
import pytest
from waveraider.node_position import position_from_tpv, gpsd_position

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
BASE = {'class': 'TPV', 'device': '/dev/gps', 'mode': 3, 'lat': 40.0, 'lon': -75.0,
        'time': '2026-09-25T11:59:59Z', 'altHAE': 130.0, 'epx': 3.0}


def test_position_is_not_a_sample_clock():
    fix = position_from_tpv(BASE, NOW)
    assert fix['state'] == 'valid' and fix['age_s'] == 1
    assert fix['sample_timing_validated'] is False
    assert fix['altHAE'] == 130
    assert 'altMSL' not in fix


@pytest.mark.parametrize('changes,state', [({'mode': 1}, 'no_fix'),
    ({'time': '2026-09-25T11:00:00Z'}, 'stale'), ({'lat': float('nan')}, 'unavailable'),
    ({'lon': 200}, 'unavailable'), ({'time': None}, 'unavailable')])
def test_invalid_position_not_reported(changes, state):
    assert position_from_tpv({**BASE, **changes}, NOW)['state'] == state


def test_partial_and_2d_fixes_do_not_invent_coordinates_or_altitude():
    partial = {k: v for k, v in BASE.items() if k != 'lon'}
    assert position_from_tpv(partial, NOW)['state'] == 'unavailable'
    assert 'altHAE' not in position_from_tpv({**BASE, 'mode': 2}, NOW)
    assert gpsd_position(None)['state'] == 'not_configured'


def test_gpsd_watch_is_local_device_selected_and_bounded(monkeypatch):
    import io
    import json
    import socket
    from datetime import timedelta
    calls = []
    timestamp = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    reports = [dict(BASE, device='/dev/other', time=timestamp),
               {'class': 'TPV', 'device': '/dev/gps', 'mode': 3, 'time': timestamp},
               dict(BASE, time=timestamp)]
    class Sock:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def sendall(self, data):
            calls.append(data)
        def makefile(self, *args):
            return io.BytesIO(b''.join(json.dumps(r).encode() + b'\n' for r in reports))
        def settimeout(self, value):
            assert 0 < value <= 2
    def connect(address, timeout):
        assert address == ('127.0.0.1', 2947)
        return Sock()
    monkeypatch.setattr(socket, 'create_connection', connect)
    fix = gpsd_position('/dev/gps')
    assert fix['state'] == 'valid' and fix['device'] == '/dev/gps'
    assert b'"device": "/dev/gps"' in calls[0]


def test_gpsd_disconnect_never_reuses_a_prior_fix(monkeypatch):
    def disconnected(*args, **kwargs):
        raise OSError('disconnected')
    monkeypatch.setattr('socket.create_connection', disconnected)
    assert gpsd_position('/dev/gps')['state'] == 'disconnected'
