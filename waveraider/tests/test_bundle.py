"""Offline launcher failures must stop before loading or starting an image."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which('sh') is None, reason='POSIX launcher')
@pytest.mark.parametrize('corrupt,architecture,message', [
    (True, 'amd64', 'FAILED'),
    (False, 'arm64', 'Download the matching bundle'),
])
def test_launcher_rejects_invalid_bundle_before_loading(tmp_path, corrupt, architecture, message):
    bundle = tmp_path / 'bundle with spaces'
    bundle.mkdir()
    shutil.copyfile(ROOT / 'packaging/start.sh', bundle / 'start.sh')
    (bundle / 'architecture.txt').write_text(architecture + '\n')
    image = bundle / 'waveraider.tar'
    image.write_bytes(b'test-image')
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    (bundle / 'SHA256SUMS').write_text(f'{digest}  waveraider.tar\n')
    if corrupt:
        image.write_bytes(b'truncated')
    commands = tmp_path / 'commands'
    commands.mkdir()
    docker = commands / 'docker'
    docker.write_text('''#!/bin/sh
case "$1" in
  info) echo x86_64;;
  compose) [ "$2" = version ] || exit 99;;
  *) echo "UNEXPECTED MUTATION"; exit 99;;
esac
''')
    docker.chmod(0o755)
    env = {**os.environ, 'PATH': str(commands) + os.pathsep + os.environ['PATH']}
    result = subprocess.run(['sh', str(bundle / 'start.sh')], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert 'UNEXPECTED MUTATION' not in result.stdout + result.stderr
