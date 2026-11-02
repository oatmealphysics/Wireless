"""Actual TLS transport and bounded persistent delivery; never opens a radio."""
from copy import deepcopy
import json
from pathlib import Path
import ssl
import threading
import urllib.error
import uuid

import pytest

from waveraider.node_cli import initialize, load_node
from waveraider.node_reporting import (CoordinatorStore, MAX_QUEUE, Outbox, ReportServer,
                                     encode, post_report, validate_report)


@pytest.fixture
def config(tmp_path):
    return {'node_id': 'sensor-a', 'stream_id': str(uuid.uuid4()), 'token': 'a' * 43,
            'coordinator': 'https://localhost:8943', 'ca_cert': str(tmp_path / 'ca.pem')}


def test_queue_bound_persistence_and_sequence_gaps(tmp_path, config):
    box = Outbox(tmp_path / 'outbox.db', config)
    for i in range(MAX_QUEUE + 3):
        report = box.enqueue('heartbeat', {})
    assert report['payload']['outbox_dropped_total'] == 3
    with box.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM queue').fetchone()[0] == MAX_QUEUE
    restarted = Outbox(box.path, config)
    new = restarted.enqueue('heartbeat', {})
    assert new['sequence'] == MAX_QUEUE + 4
    assert new['boot_id'] != report['boot_id']
    store = CoordinatorStore(tmp_path / 'server.db')
    assert store.accept(new)['status'] == 'accepted'
    before = store.status()[0]
    assert before['sequence_gaps'] == MAX_QUEUE + 3
    assert store.accept(report)['status'] == 'duplicate_or_old'
    store = CoordinatorStore(store.path)
    assert store.accept(new)['status'] == 'duplicate_or_old'
    assert store.status()[0]['latest'] == before['latest']
    assert store.status()[0]['localization_eligible'] is False


def test_store_bounds_history(tmp_path, config):
    box = Outbox(tmp_path / 'outbox.db', config)
    store = CoordinatorStore(tmp_path / 'server.db')
    for _ in range(135):
        store.accept(box.enqueue('heartbeat', {}))
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM recent').fetchone()[0] == 128


@pytest.mark.parametrize('field,value', [('schema', 2), ('sequence', True), ('sequence', -1),
    ('simulated', 'false'), ('created_at', '2026-01-01T00:00:00'), ('kind', 'drone_location'),
    ('payload', {'x': float('nan')}), ('payload', {'x': 'a' * 17000}),
    ('timing', {'sample_timing_validated': True, 'localization_eligible': True})])
def test_invalid_reports_rejected(tmp_path, config, field, value):
    report = Outbox(tmp_path / 'outbox.db', config).enqueue('heartbeat', {})
    report[field] = value
    with pytest.raises((ValueError, TypeError)):
        validate_report(report)


def test_wrong_enrollment_does_not_reuse_outbox(tmp_path, config):
    Outbox(tmp_path / 'outbox.db', config)
    with pytest.raises(ValueError):
        Outbox(tmp_path / 'outbox.db', {**config, 'stream_id': str(uuid.uuid4())})


@pytest.fixture
def tls_server(tmp_path):
    root = initialize(tmp_path / 'enrollment', 'localhost', 8943, ['sensor-a', 'sensor-b'])
    directory = root / 'coordinator'
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / 'server.crt', directory / 'server.key')
    config = json.loads((directory / 'coordinator.json').read_text())
    store = CoordinatorStore(directory / 'reports.sqlite3')
    server = ReportServer(('127.0.0.1', 0), store, config['nodes'], context)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = load_node(root / 'sensor-a' / 'node.json')
    client['coordinator'] = f'https://localhost:{server.server_port}'
    try:
        yield client, store, server, root
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)


def test_actual_https_delivery_retry_and_identity_checks(tls_server):
    config, store, server, root = tls_server
    box = Outbox(config['outbox'], config)
    report = box.enqueue('heartbeat', {'receiver_state': 'not_probed'})
    # A failed authentication leaves the durable outbox intact.
    bad = deepcopy(config)
    bad['token'] = 'wrong'
    with pytest.raises(urllib.error.HTTPError) as error:
        post_report(bad, encode(report))
    assert error.value.code == 401
    forged = {**report, 'node_id': 'sensor-b'}
    with pytest.raises(urllib.error.HTTPError) as error:
        post_report(config, encode(forged))
    assert error.value.code == 403
    assert box.flush() == 1
    assert box.flush() == 0
    assert store.status()[0]['latest']['sequence'] == 1
    assert post_report(config, encode(report))['status'] == 'duplicate_or_old'
    # Certificate hostname verification remains enabled.
    bad['token'] = config['token']
    bad['coordinator'] = f'https://127.0.0.1:{server.server_port}'
    with pytest.raises(urllib.error.URLError):
        post_report(bad, encode(report))
    # Explicit trust of a different deployment must fail.
    other = initialize(root.parent / 'other', 'localhost', 8943, ['sensor-a'])
    bad = {**config, 'ca_cert': str(other / 'sensor-a' / 'ca.pem')}
    with pytest.raises(urllib.error.URLError):
        post_report(bad, encode(report))


def test_oversized_request_and_timing_claim_are_rejected(tls_server):
    config, store, server, root = tls_server
    with pytest.raises(urllib.error.HTTPError) as error:
        post_report(config, b'x' * 17000)
    assert error.value.code == 413
    report = Outbox(config['outbox'], config).enqueue('heartbeat', {})
    report['timing']['localization_eligible'] = True
    with pytest.raises(urllib.error.HTTPError) as error:
        post_report(config, encode(report))
    assert error.value.code == 400
    assert store.status() == []


def test_failed_link_retains_recording_summary(tmp_path, config, monkeypatch):
    box = Outbox(tmp_path / 'outbox.db', config)
    box.enqueue('recording', {'capture_id': str(uuid.uuid4())}, simulated=True)
    def fail(*args):
        raise OSError('link down')
    monkeypatch.setattr('waveraider.node_reporting.post_report', fail)
    with pytest.raises(OSError):
        box.flush()
    with box.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM queue').fetchone()[0] == 1


def test_init_never_overwrites_existing_deployment(tmp_path):
    root = initialize(tmp_path / 'nodes', '127.0.0.1', 8943, ['a'])
    before = (root / 'a/node.json').read_bytes()
    with pytest.raises(FileExistsError):
        initialize(root, '127.0.0.1', 8943, ['a'])
    assert (root / 'a/node.json').read_bytes() == before


def test_real_link_outage_queue_recovery_and_coordinator_restart(tls_server):
    config, store, server, root = tls_server
    box = Outbox(config['outbox'], config)
    first = box.enqueue('heartbeat', {'receiver_state': 'not_probed'})
    assert box.flush() == 1
    port = server.server_port
    server.shutdown()
    server.server_close()
    for _ in range(3):
        box.enqueue('recording', {'capture_id': str(uuid.uuid4())}, simulated=True)
    with pytest.raises(urllib.error.URLError):
        box.flush()
    with box.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM queue').fetchone()[0] == 3
    directory = root / 'coordinator'
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / 'server.crt', directory / 'server.key')
    enrollment = json.loads((directory / 'coordinator.json').read_text())['nodes']
    restarted_store = CoordinatorStore(store.path)
    restarted = ReportServer(('127.0.0.1', port), restarted_store, enrollment, context)
    worker = threading.Thread(target=restarted.serve_forever, daemon=True)
    worker.start()
    try:
        box = Outbox(config['outbox'], config)  # client restart preserves queued data
        assert box.flush() == 3 and box.flush() == 0
        assert len(restarted_store.recordings()) == 3
        assert post_report(config, encode(first))['status'] == 'duplicate_or_old'
        assert restarted_store.status()[0]['sequence'] == 4
        assert restarted_store.status()[0]['sequence_gaps'] == 0
    finally:
        restarted.shutdown()
        restarted.server_close()
        worker.join(5)
