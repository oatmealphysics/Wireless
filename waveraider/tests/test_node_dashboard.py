"""The GUI cannot turn replay, queued reports, or stale fixes into healthy sensors."""
from datetime import datetime, timezone
import json
import subprocess
import uuid

from waveraider.node_dashboard import snapshot
from waveraider.node_health import receiver_health
from waveraider.node_reporting import CoordinatorStore, Outbox


def setup(tmp_path):
    box = Outbox(tmp_path / 'outbox', {'node_id': 'sensor-a', 'stream_id': str(uuid.uuid4())})
    store = CoordinatorStore(tmp_path / 'reports.sqlite3')
    return box, store


def test_disabled_and_missing_database_do_not_create_files(tmp_path, monkeypatch):
    monkeypatch.delenv('WAVERAIDER_COORDINATOR', raising=False)
    assert snapshot()['state'] == 'not_configured'
    assert snapshot(tmp_path)['state'] == 'unavailable'
    assert list(tmp_path.iterdir()) == []


def test_heartbeat_age_and_fix_age_not_network_receipt(tmp_path):
    box, store = setup(tmp_path)
    now = datetime.now(timezone.utc).timestamp()
    report = box.enqueue('heartbeat', {'receiver': {'state': 'accessible', 'detail': 'query only'}},
        position={'state': 'valid', 'sample_timing_validated': False, 'mode': 3,
                  'latitude': 40, 'longitude': -74, 'fix_time': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'age_s': 0})
    store.accept(report)
    view = snapshot(tmp_path, now=now + 1)['nodes'][0]
    assert view['receiver_state'] == 'accessible' and view['position']['state'] == 'valid'
    assert not view['localization_eligible'] and not view['sample_timing_validated']
    assert snapshot(tmp_path, now=now + 6)['nodes'][0]['position']['state'] == 'stale'
    report['sequence'] += 1
    report['created_at'] = datetime.fromtimestamp(now - 100, timezone.utc).isoformat()
    store.accept(report)  # delayed delivery cannot refresh receiver health
    view = snapshot(tmp_path, now=now + 1)['nodes'][0]
    assert view['link_state'] == 'recent' and view['health_state'] == 'stale'
    assert view['receiver_state'] == 'unknown' and view['position']['state'] == 'stale'
    report['sequence'] += 1
    report['created_at'] = datetime.fromtimestamp(now + 100, timezone.utc).isoformat()
    store.accept(report)
    assert snapshot(tmp_path, now=now + 1)['nodes'][0]['health_state'] == 'stale'


def test_recording_does_not_replace_heartbeat_and_only_safe_fields_leave_database(tmp_path):
    box, store = setup(tmp_path)
    store.accept(box.enqueue('heartbeat', {'receiver': {'state': 'not_ready'}}))
    store.accept(box.enqueue('recording', {'capture_id': 'one', 'operator_label': '<script>',
                                         'secret': 'must-not-leak'}, simulated=True))
    view = snapshot(tmp_path)
    assert view['nodes'][0]['receiver_state'] == 'not_ready'
    assert view['recordings'][0]['simulated'] is True
    assert 'must-not-leak' not in json.dumps(view)


def test_probe_is_opt_in_and_timeout_is_not_healthy(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 3)
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert receiver_health()['state'] == 'not_probed'
    assert receiver_health('a' * 32)['state'] == 'unavailable'
