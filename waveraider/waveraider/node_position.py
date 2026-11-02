"""Optional, bounded GPSD position snapshot. Never a sample-clock reference."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import socket
import time


def position_from_tpv(tpv, now=None):
    result = {'state': 'unavailable', 'sample_timing_validated': False}
    if not isinstance(tpv, dict) or tpv.get('class') != 'TPV':
        return result
    result['device'] = tpv.get('device')
    if type(tpv.get('mode')) is not int or tpv['mode'] not in (2, 3):
        result['state'] = 'no_fix'
        return result
    try:
        lat, lon = tpv['lat'], tpv['lon']
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (lat, lon)):
            return result
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return result
        epoch = datetime.fromisoformat(tpv['time'].replace('Z', '+00:00'))
        if epoch.tzinfo is None:
            return result
        age = (datetime.now(timezone.utc) if now is None else now).timestamp() - epoch.timestamp()
        if not 0 <= age <= 5:
            result['state'] = 'stale'
            return result
        result.update(state='valid', latitude=lat, longitude=lon, mode=tpv['mode'],
                      fix_time=tpv['time'], age_s=age, receipt_monotonic_s=time.monotonic())
        # Preserve the receiver's field names, not an invented confidence interval.
        for key in ('epx', 'epy', 'epv', 'speed', 'track', 'altHAE', 'altMSL'):
            value = tpv.get(key)
            if key.startswith('alt') and tpv['mode'] != 3:
                continue
            if type(value) in (int, float) and math.isfinite(value):
                result[key] = value
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError):
        pass
    return result


def gpsd_position(device):
    """Read a complete TPV from one explicitly selected local GPSD device.

    Partial reports are not merged across epochs. No network-host GPSD is exposed.
    A position here describes report time, not a past IQ capture.
    """
    if not device:
        return {'state': 'not_configured', 'sample_timing_validated': False}
    deadline = time.monotonic() + 2
    result = {'state': 'unavailable', 'sample_timing_validated': False}
    try:
        with socket.create_connection(('127.0.0.1', 2947), timeout=2) as sock:
            sock.sendall(('?WATCH=' + json.dumps({'enable': True, 'json': True, 'device': device}) + ';\n').encode())
            with sock.makefile('rb') as stream:
                for _ in range(32):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    sock.settimeout(remaining)
                    line = stream.readline(8193)
                    if not line or len(line) > 8192:
                        break
                    report = json.loads(line)
                    if isinstance(report, dict) and report.get('device') == device:
                        result = position_from_tpv(report)
                        if result['state'] == 'valid':
                            return result
    except (OSError, ValueError):
        result = {'state': 'disconnected', 'sample_timing_validated': False}
    return result
