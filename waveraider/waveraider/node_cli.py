"""CLI for enrolling nodes, HTTPS reporting and explicit IQ recording."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import uuid

from .node_reporting import NODE_ID, CoordinatorStore, Outbox, ReportServer


def write_private(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
        file.write(json.dumps(value, indent=2) + '\n')


def load_node(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    if config.get('schema') != 1 or not NODE_ID.fullmatch(config.get('node_id', '')):
        raise ValueError('invalid node configuration')
    if not isinstance(config.get('token'), str) or len(config['token']) < 32:
        raise ValueError('invalid node credential')
    uuid.UUID(config['stream_id'])
    config['ca_cert'] = str(path.parent / config['ca_cert'])
    config['outbox'] = str(path.parent / 'outbox.sqlite3')
    return config


def initialize(directory, host, port, node_ids):
    """Generate enrollment files locally; no credentials printed and no network calls."""
    if not shutil.which('openssl'):
        raise OSError('openssl is required for enrollment (included in the WR Docker image)')
    if not 1 <= port <= 65535 or not 1 <= len(node_ids) <= 32 or len(set(node_ids)) != len(node_ids):
        raise ValueError('use a valid port and 1–32 unique node IDs')
    if any(not NODE_ID.fullmatch(node) for node in node_ids):
        raise ValueError('node IDs use letters, digits, underscores and hyphens; max 48 characters')
    try:
        address = ipaddress.ip_address(host)
        san = 'IP:' + str(address)
        urlhost = '[' + host + ']' if address.version == 6 else host
    except ValueError:
        import re
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host):
            raise ValueError('host must be a coordinator IP or DNS name') from None
        san, urlhost = 'DNS:' + host, host
    root = Path(directory).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    server = root / 'coordinator'
    server.mkdir(mode=0o700)
    key, cert = server / 'server.key', server / 'server.crt'
    # Precreate the private key with restrictive permissions before OpenSSL writes it.
    key.touch(mode=0o600)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-sha256', '-nodes',
                    '-days', '365', '-subj', '/CN=WaveRaider coordinator',
                    '-addext', 'subjectAltName=' + san, '-keyout', str(key), '-out', str(cert)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    enrollment = {}
    for node in node_ids:
        credentials = {'token': secrets.token_urlsafe(32), 'stream_id': str(uuid.uuid4())}
        enrollment[node] = credentials
        node_dir = root / node
        node_dir.mkdir(mode=0o700)
        shutil.copyfile(cert, node_dir / 'ca.pem')
        write_private(node_dir / 'node.json', {'schema': 1, 'node_id': node, **credentials,
            'coordinator': f'https://{urlhost}:{port}', 'ca_cert': 'ca.pem'})
    write_private(server / 'coordinator.json', {'schema': 1, 'port': port, 'nodes': enrollment})
    return root


def serve(config_file, bind):
    config_file = Path(config_file).resolve()
    config = json.loads(config_file.read_text())
    if config.get('schema') != 1:
        raise ValueError('unsupported coordinator config')
    root = config_file.parent
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(root / 'server.crt', root / 'server.key')
    server = ReportServer((bind, config['port']), CoordinatorStore(root / 'reports.sqlite3'), config['nodes'], context)
    print(f'Node reporting listening on {bind}:{config["port"]} (HTTPS). TDOA unavailable.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv):
    parser = argparse.ArgumentParser(prog='wr node', description='Experimental node telemetry over your IP network; not TDOA.')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='create coordinator keys and per-node enrollment folders')
    init.add_argument('--directory', required=True)
    init.add_argument('--host', required=True, help='coordinator IP address or DNS name reachable from the sensors')
    init.add_argument('--port', type=int, default=8943)
    init.add_argument('--nodes', nargs='+', required=True)
    run = sub.add_parser('serve', help='run the separate authenticated reporting service')
    run.add_argument('--config', required=True)
    run.add_argument('--bind', default='0.0.0.0')
    beat = sub.add_parser('report', help='send node heartbeats, retaining up to 128 unsent reports')
    beat.add_argument('--config', required=True)
    beat.add_argument('--once', action='store_true')
    beat.add_argument('--serial', help='optional exact bladeRF micro serial for a bounded read-only health query')
    beat.add_argument('--gps-device', help='explicit GPSD device path, e.g. /dev/ttyACM0')
    status = sub.add_parser('status', help='read coordinator status locally; does not expose an unauthenticated API')
    status.add_argument('--directory', required=True, help='coordinator folder')
    status.add_argument('--recordings', action='store_true', help='show retained recording summaries')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            root = initialize(args.directory, args.host, args.port, args.nodes)
            print(f'Created {root}. Transfer only each node’s own folder. Keep coordinator keys private.')
        elif args.command == 'serve':
            serve(args.config, args.bind)
        elif args.command == 'status':
            path = Path(args.directory) / 'reports.sqlite3'
            if not path.exists():
                raise ValueError('no coordinator database yet; start wr node serve first')
            store = CoordinatorStore(path)
            print(json.dumps(store.recordings() if args.recordings else store.status(), indent=2))
        else:
            from .node_position import gpsd_position
            from .node_health import receiver_health
            config = load_node(args.config)
            box = Outbox(config['outbox'], config)
            while True:
                receiver = receiver_health(args.serial)
                box.enqueue('heartbeat', {'receiver_state': receiver['state'], 'receiver': receiver, 'transport': 'IP',
                    'clock_status': 'unvalidated'}, position=gpsd_position(args.gps_device))
                try:
                    sent = box.flush()
                    print(f'Delivered {sent} report(s). TDOA unavailable.', flush=True)
                except (OSError, ValueError) as exc:
                    print(f'Reports remain queued: {exc}', file=sys.stderr, flush=True)
                    if args.once:
                        return 1
                if args.once:
                    break
                time.sleep(5)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'wr node: {exc}', file=sys.stderr)
        return 1


def record_main(argv):
    parser = argparse.ArgumentParser(prog='wr record', description='Save one bounded IQ recording. Explicit opt-in; no automatic recording.')
    parser.add_argument('--output', required=True, help='local directory for recordings')
    parser.add_argument('--node-id', required=True)
    parser.add_argument('--frequency', type=int, required=True, help='center frequency in Hz')
    parser.add_argument('--rate', type=int, default=15_360_000, help='sample rate in Hz')
    parser.add_argument('--seconds', type=float, default=1)
    parser.add_argument('--label', required=True, help='operator description; not a verified training label')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--simulate', action='store_true', help='synthetic noise, without opening hardware')
    mode.add_argument('--serial', help='exact 32-character bladeRF micro serial; enables physical RX')
    parser.add_argument('--gain', type=int, default=30)
    parser.add_argument('--storage-mib', type=int, default=2048, help='output folder quota; never automatically deletes captures')
    parser.add_argument('--node-config', help='optionally queue/send recording summary; IQ stays local')
    args = parser.parse_args(argv)
    try:
        from .recording import record
        config = load_node(args.node_config) if args.node_config else None
        if config and config['node_id'] != args.node_id:
            raise ValueError('node ID does not match enrollment')
        folder, summary = record(args.output, node_id=args.node_id, frequency=args.frequency,
            rate=args.rate, seconds=args.seconds, label=args.label, simulated=args.simulate,
            serial=args.serial, gain=args.gain, storage_mib=args.storage_mib)
        print(f'Saved {folder}\nSimulated: {args.simulate}. Sample timing unvalidated; not TDOA-ready.', flush=True)
        if config:
            box = Outbox(config['outbox'], config)
            box.enqueue('recording', summary, simulated=args.simulate)
            try:
                box.flush()
                print('Recording summary delivered; IQ remains on this node.')
            except (OSError, ValueError) as exc:
                print(f'Recording saved; summary remains queued: {exc}', file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print('Recording interrupted; incomplete data stays in a .partial folder.', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError) as exc:
        print(f'wr record: {exc}', file=sys.stderr)
        return 1
