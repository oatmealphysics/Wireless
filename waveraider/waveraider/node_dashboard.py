"""Read-only, explicitly enabled local coordinator view. Never reads credentials."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import time


def age(epoch, now):
    try:
        value = datetime.fromisoformat(epoch.replace('Z', '+00:00'))
        seconds = now - value.timestamp() if value.tzinfo else -1
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def snapshot(directory=None, now=None):
    directory = directory or os.environ.get('WAVERAIDER_COORDINATOR')
    result = {'state': 'not_configured', 'nodes': [], 'recordings': [],
              'note': 'Node health is separate from sample observations. No live detection or localization.'}
    if not directory:
        return result
    now = time.time() if now is None else now
    path = Path(directory).resolve() / 'reports.sqlite3'
    try:
        # mode=ro must not create a missing database or initialize/migrate tables.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            nodes = db.execute('SELECT node,gaps,received,body FROM nodes ORDER BY node LIMIT 32').fetchall()
            recent = db.execute('SELECT node,kind,body FROM recent ORDER BY rowid DESC LIMIT 4096').fetchall()
        heartbeats = {}
        recordings = []
        for node, kind, raw in recent:
            report = json.loads(raw)
            if kind == 'heartbeat':
                heartbeats.setdefault(node, report)
            elif kind == 'recording' and len(recordings) < 128:
                # Explicitly project only operator-facing fields, no arbitrary payloads.
                p = report['payload']
                recordings.append({key: p.get(key) for key in ('capture_id', 'operator_label',
                    'frequency_hz', 'sample_rate_hz', 'sample_count', 'bytes', 'sha256',
                    'overruns', 'discontinuities')} | {'node_id': node,
                    'simulated': report['simulated'], 'created_at': report['created_at']})
        for node, gaps, received, raw in nodes:
            latest = json.loads(raw)
            heartbeat = heartbeats.get(node)
            report_age = age(heartbeat['created_at'], now) if heartbeat else None
            fresh = report_age is not None and report_age <= 15
            receipt_age = now - received
            payload = heartbeat['payload'] if heartbeat else {}
            receiver = payload.get('receiver', {})
            position = dict(heartbeat['position']) if heartbeat else {'state': 'unavailable'}
            fix_age = age(position.get('fix_time'), now)
            if position.get('state') == 'valid' and (not fresh or fix_age is None or fix_age > 5):
                position['state'] = 'stale'
            position['age_s'] = fix_age
            # Never retain a green receiver state from an old or future-dated report.
            result['nodes'].append({'node_id': node,
                'link_state': 'recent' if 0 <= receipt_age <= 15 else 'stale',
                'seconds_since_receipt': max(0, receipt_age), 'heartbeat_age_s': report_age,
                'health_state': 'recent' if fresh else 'stale',
                'receiver_state': receiver.get('state', 'not_probed') if fresh else 'unknown',
                'receiver_detail': receiver.get('detail', 'Receiver not probed') if fresh else 'No recent heartbeat',
                'position': {k: position.get(k) for k in ('state', 'latitude', 'longitude', 'age_s')},
                'sequence_gaps': gaps, 'outbox_dropped_total': latest['payload'].get('outbox_dropped_total'),
                'simulated': heartbeat['simulated'] if heartbeat else latest['simulated'],
                'sample_timing_validated': False, 'localization_eligible': False})
        result.update(state='available', recordings=recordings)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
        result.update(state='unavailable', nodes=[], recordings=[],
                      error='Coordinator data unavailable. Check the configured local database and service.')
    return result
