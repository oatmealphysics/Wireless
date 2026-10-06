"""Exercise an installed package (run outside the source checkout), without a radio."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-bladerf', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='wr-install-') as folder:
        for key, name in [('TARGETS', 'targets.json'), ('MONITORING', 'monitoring.json'),
                          ('SESSIONS', 'sessions')]:
            os.environ['WAVERAIDER_' + key] = str(Path(folder) / name)
        import numpy as np
        import scapy.all  # noqa: F401
        import textual  # noqa: F401
        from waveraider import droneid, web, monitor
        from importlib.resources import files
        sample = files('waveraider.samples').joinpath('uas.pcap')
        output = subprocess.check_output([sys.executable, '-m', 'waveraider.cli', str(sample)], text=True)
        assert 'DRONE-SN-001A' in output, output
        assert droneid.detect(np.zeros(8192, dtype=np.complex64))['classification'] == 'none'
        if args.native_bladerf:
            from waveraider import bladerf_sweep
            bladerf_sweep._lib()  # load and resolve API symbols; do not open a receiver
            subprocess.run(['bladeRF-cli', '--version'], check=True)
            from waveraider.recording import metadata_format
            metadata_format(bladerf_sweep._lib())
        # Exercise the shipped node/recording path without a physical radio.
        from waveraider.node_cli import initialize, load_node
        from waveraider.node_reporting import CoordinatorStore, Outbox, ReportServer
        from waveraider.recording import record
        import ssl
        enrollment = initialize(Path(folder) / 'nodes', 'localhost', 8943, ['sensor-a'])
        coordinator = enrollment / 'coordinator'
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(coordinator / 'server.crt', coordinator / 'server.key')
        configs = json.loads((coordinator / 'coordinator.json').read_text())
        store = CoordinatorStore(coordinator / 'reports.sqlite3')
        node_server = ReportServer(('127.0.0.1', 0), store, configs['nodes'], context)
        node_worker = threading.Thread(target=node_server.serve_forever, daemon=True)
        node_worker.start()
        try:
            config = load_node(enrollment / 'sensor-a' / 'node.json')
            config['coordinator'] = f'https://localhost:{node_server.server_port}'
            box = Outbox(config['outbox'], config)
            box.enqueue('heartbeat', {'receiver_state': 'not_probed'})
            capture_folder, summary = record(Path(folder) / 'captures', node_id='sensor-a',
                frequency=2444500000, rate=160000, seconds=.1, label='smoke test', simulated=True)
            from waveraider.recording_check import verify
            assert verify(capture_folder)['integrity'] == 'verified'
            box.enqueue('recording', summary, simulated=True)
            assert box.flush() == 2
            assert store.recordings()[0]['payload']['capture_id'] == summary['capture_id']
            assert store.status()[0]['localization_eligible'] is False
        finally:
            node_server.shutdown()
            node_server.server_close()
            node_worker.join(5)
        os.environ['WAVERAIDER_COORDINATOR'] = str(coordinator)
        server = web.ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            for path in ('/', '/settings', '/inspect-rf.js', '/inspect-rf.css',
                         '/spectrum-display.js', '/brand/logo.png', '/brand/icon.png',
                         '/api/settings/about'):
                with urllib.request.urlopen(base + path, timeout=10) as response:
                    assert response.status == 200 and response.read(), path
            with urllib.request.urlopen(base + '/api/nodes', timeout=10) as response:
                view = json.load(response)
                assert view['state'] == 'available' and view['nodes'][0]['node_id'] == 'sensor-a'
                assert view['recordings'][0]['simulated'] is True
            request = urllib.request.Request(base + '/api/session',
                data=json.dumps({'action': 'start', 'source': 'sample'}).encode(),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200
        finally:
            monitor.SESSION.stop()
            server.shutdown()
            server.server_close()
            worker.join(5)
    print('Installed-package smoke test passed: dependencies, DSP, CLI sample, GUI assets and sample session.')


if __name__ == '__main__':
    main()
