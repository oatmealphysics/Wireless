"""Authenticated HTTPS node telemetry; bounded persistent queues, no RF control API."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hmac
import json
import math
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import threading
import time
import urllib.request
from urllib.parse import urlsplit
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 16384
MAX_QUEUE = 128
NODE_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}\Z')


def encode(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':')).encode()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_report(report):
    required = {'schema', 'node_id', 'stream_id', 'boot_id', 'sequence', 'created_at',
                'kind', 'simulated', 'timing', 'position', 'payload'}
    if not isinstance(report, dict) or set(report) != required or len(encode(report)) > MAX_BODY:
        raise ValueError('invalid report envelope')
    if type(report['schema']) is not int or report['schema'] != 1:
        raise ValueError('unsupported schema')
    if not isinstance(report['node_id'], str) or not NODE_ID.fullmatch(report['node_id']):
        raise ValueError('invalid node ID')
    for key in ('stream_id', 'boot_id'):
        if not isinstance(report[key], str) or str(uuid.UUID(report[key])) != report[key]:
            raise ValueError('invalid stream/boot ID')
    if type(report['sequence']) is not int or not 1 <= report['sequence'] < 2**63:
        raise ValueError('invalid sequence')
    dt = datetime.fromisoformat(report['created_at'].replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    if report['kind'] not in ('heartbeat', 'recording') or type(report['simulated']) is not bool:
        raise ValueError('unsupported report kind')
    if report['timing'] != {'sample_timing_validated': False, 'localization_eligible': False}:
        raise ValueError('TDOA timing is not validated in this version')
    if not isinstance(report['position'], dict) or report['position'].get('state') not in (
            'valid', 'no_fix', 'stale', 'unavailable', 'disconnected', 'not_configured'):
        raise ValueError('invalid position')
    if report['position'].get('sample_timing_validated') is not False:
        raise ValueError('GPS is not a sample clock')
    position = report['position']
    if position['state'] == 'valid':
        for key, low, high in (('latitude', -90, 90), ('longitude', -180, 180), ('age_s', 0, 5)):
            value = position.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('invalid valid-position coordinates/age')
        if type(position.get('mode')) is not int or position['mode'] not in (2, 3):
            raise ValueError('invalid fix mode')
        epoch = datetime.fromisoformat(position['fix_time'].replace('Z', '+00:00'))
        if epoch.tzinfo is None:
            raise ValueError('fix epoch must include timezone')
    if not isinstance(report['payload'], dict):
        raise ValueError('payload must be an object')
    return report


class Database:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_EXCL creation protects permissions for newly created stores.
        if not path.exists():
            try:
                path.touch(mode=0o600, exist_ok=False)
            except FileExistsError:
                pass
        self.path = path
        self.lock = threading.Lock()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()


class Outbox(Database):
    """One persistent sequence stream per enrolled node. Retain this DB across restarts."""
    def __init__(self, path, config):
        super().__init__(path)
        self.config = config
        self.boot_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT, sequence INTEGER, dropped INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS queue (sequence INTEGER PRIMARY KEY, body BLOB)')
            identity = config['node_id'] + ':' + config['stream_id']
            db.execute('INSERT OR IGNORE INTO state VALUES (1, ?, 0, 0)', (identity,))
            if db.execute('SELECT identity FROM state').fetchone()[0] != identity:
                raise ValueError('outbox belongs to a different enrollment')

    def enqueue(self, kind, payload, simulated=False, position=None):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            seq, dropped = db.execute('SELECT sequence, dropped FROM state').fetchone()
            seq += 1
            overflow = db.execute('SELECT COUNT(*) FROM queue').fetchone()[0] >= MAX_QUEUE
            dropped += int(overflow)
            body = dict(schema=1, node_id=self.config['node_id'], stream_id=self.config['stream_id'],
                        boot_id=self.boot_id, sequence=seq, created_at=utc_now(), kind=kind,
                        simulated=simulated, timing={'sample_timing_validated': False, 'localization_eligible': False},
                        position=position or {'state': 'not_configured', 'sample_timing_validated': False},
                        payload={**payload, 'outbox_dropped_total': dropped})
            raw = encode(validate_report(body))
            if overflow:
                db.execute('DELETE FROM queue WHERE sequence=(SELECT MIN(sequence) FROM queue)')
            db.execute('INSERT INTO queue VALUES (?, ?)', (seq, raw))
            db.execute('UPDATE state SET sequence=?, dropped=?', (seq, dropped))
        return body

    def flush(self):
        """One bounded FIFO pass. Leave unsent reports queued on link/cert/auth failure."""
        sent = 0
        for _ in range(MAX_QUEUE):
            with self.connect() as db:
                row = db.execute('SELECT sequence, body FROM queue ORDER BY sequence LIMIT 1').fetchone()
            if row is None:
                break
            reply = post_report(self.config, row[1])
            if reply.get('sequence') != row[0] or reply.get('status') not in ('accepted', 'duplicate_or_old'):
                raise ValueError('unexpected coordinator acknowledgement')
            with self.connect() as db:
                db.execute('DELETE FROM queue WHERE sequence=?', (row[0],))
            sent += 1
        return sent


def post_report(config, raw):
    url = config['coordinator']
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('coordinator must be an HTTPS origin')
    context = ssl.create_default_context(cafile=config['ca_cert'])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # No proxy discovery or redirects: never forward enrollment credentials elsewhere.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=context))
    req = urllib.request.Request(url.rstrip('/') + '/v1/reports', data=raw,
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + config['token'],
                 'X-WR-Node': config['node_id']})
    with opener.open(req, timeout=5) as response:
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError('oversized acknowledgement')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError('invalid acknowledgement')
        return result


class CoordinatorStore(Database):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS nodes (node TEXT PRIMARY KEY, stream TEXT, sequence INTEGER, gaps INTEGER, received REAL, body BLOB)')
            db.execute('CREATE TABLE IF NOT EXISTS recent (node TEXT, sequence INTEGER, kind TEXT, body BLOB, PRIMARY KEY(node,sequence))')

    def accept(self, report):
        validate_report(report)
        node, seq = report['node_id'], report['sequence']
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT stream,sequence,gaps FROM nodes WHERE node=?', (node,)).fetchone()
            if old and old[0] != report['stream_id']:
                raise ValueError('enrollment stream changed; use a new node ID')
            if old and seq <= old[1]:
                return {'status': 'duplicate_or_old', 'sequence': seq}
            gaps = (old[2] if old else 0) + seq - (old[1] if old else 0) - 1
            raw = encode(report)
            db.execute('INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?)',
                       (node, report['stream_id'], seq, gaps, time.time(), raw))
            db.execute('INSERT INTO recent VALUES (?,?,?,?)', (node, seq, report['kind'], raw))
            db.execute('DELETE FROM recent WHERE node=? AND sequence NOT IN (SELECT sequence FROM recent WHERE node=? ORDER BY sequence DESC LIMIT 128)', (node, node))
        return {'status': 'accepted', 'sequence': seq}

    def status(self):
        with self.connect() as db:
            rows = db.execute('SELECT node,sequence,gaps,received,body FROM nodes ORDER BY node').fetchall()
        return [{'node_id': node, 'sequence': seq, 'sequence_gaps': gaps,
                 'seconds_since_receipt': max(0, time.time() - received),
                 'link_state': 'recent' if 0 <= time.time() - received <= 15 else 'stale',
                 'latest': json.loads(body), 'localization_eligible': False} for node, seq, gaps, received, body in rows]


    def recordings(self):
        with self.connect() as db:
            # History is bounded per node and returned as the newest 128 recording summaries.
            rows = db.execute("SELECT body FROM recent WHERE kind='recording' ORDER BY rowid DESC LIMIT 128").fetchall()
        return [json.loads(body) for (body,) in rows]


class ReportServer(ThreadingHTTPServer):
    daemon_threads = True
    # Bound concurrent workers, including TLS handshakes and slow clients.
    def __init__(self, address, store, enrollment, context):
        if not 1 <= len(enrollment) <= 32:
            raise ValueError('enroll 1–32 nodes')
        self.store, self.enrollment, self.context = store, enrollment, context
        self.slots = threading.BoundedSemaphore(16)
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, ReportHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(5)
            with self.context.wrap_socket(request, server_side=True) as secured:
                super().process_request_thread(secured, client_address)
        except (OSError, ssl.SSLError):
            request.close()
        finally:
            self.slots.release()


class ReportHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log bearer credentials or raw report bodies.

    def reply(self, code, value):
        raw = encode(value)
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path != '/v1/reports':
            return self.reply(404, {'error': 'not found'})
        node = self.headers.get('X-WR-Node', '')
        enrolled = self.server.enrollment.get(node)
        auth = self.headers.get('Authorization', '')
        if not enrolled or not hmac.compare_digest(auth.encode(), ('Bearer ' + enrolled['token']).encode()):
            return self.reply(401, {'error': 'unauthorized'})
        if self.headers.get('Origin') or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type') != 'application/json':
            return self.reply(400, {'error': 'invalid request headers'})
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_BODY:
                return self.reply(413, {'error': 'report size limit'})
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise ValueError('truncated body')
            report = validate_report(json.loads(raw))
            if report['node_id'] != node or report['stream_id'] != enrolled['stream_id']:
                return self.reply(403, {'error': 'enrollment mismatch'})
            return self.reply(200, self.server.store.accept(report))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            return self.reply(400, {'error': 'invalid report'})
