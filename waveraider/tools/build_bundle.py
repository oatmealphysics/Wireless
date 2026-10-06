"""Package an already-built Docker image for offline startup. No external Python deps."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision', help='full source commit SHA for release provenance')
    args = parser.parse_args()
    if args.revision and not re.fullmatch(r'[0-9a-f]{40}', args.revision):
        parser.error('revision must be a full commit SHA')
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image]))[0]
    arch = info['Architecture']
    if info['Os'] != 'linux' or arch not in ('amd64', 'arm64'):
        parser.error('expected a linux/amd64 or linux/arm64 image')
    # A content-derived tag prevents one download silently replacing another revision.
    tag = f"waveraider:bundle-{arch}-{info['Id'].split(':')[1][:16]}"
    args.output.mkdir(parents=True, exist_ok=False)
    subprocess.run(['docker', 'tag', args.image, tag], check=True)
    subprocess.run(['docker', 'save', '-o', str(args.output / 'waveraider.tar'), tag], check=True)
    for name in ('start.sh', 'start.ps1', 'start.cmd'):
        shutil.copyfile(ROOT / 'packaging' / name, args.output / name)
    shutil.copyfile(ROOT / 'docs/node-quickstart.md', args.output / 'NODE-SETUP.md')
    shutil.copyfile(ROOT / 'docs/node-data-format.md', args.output / 'NODE-DATA-FORMAT.md')
    (args.output / 'bundle.json').write_text(json.dumps({'schema': 1, 'revision': args.revision,
        'architecture': arch, 'image_id': info['Id']}, indent=2) + '\n')
    (args.output / 'architecture.txt').write_text(arch + '\n')
    (args.output / 'image.json').write_text(json.dumps(info, indent=2) + '\n')
    (args.output / 'compose.yaml').write_text(f'''name: waveraider
services:
  waveraider:
    image: {tag}
    pull_policy: never
    ports:
      - "${{WR_BIND:-127.0.0.1}}:${{WR_PORT:-8899}}:8899"
    volumes:
      - wr_targets:/root/.waveraider
volumes:
  wr_targets:
''')
    (args.output / 'README.txt').write_text('''WaveRaider offline runtime bundle

Install Docker Desktop on Windows/macOS, or Docker Engine with the Compose plugin
on Linux, BEFORE taking this bundle offline: https://docs.docker.com/engine/install/
Extract ALL files into one folder. No Git or host Python installation is needed.

Windows: double-click start.cmd (Docker Desktop must use Linux containers).
macOS: open Terminal in this folder, run: sh start.sh
Linux: open a terminal in this folder, run: sudo sh start.sh

Open http://localhost:8899 once startup completes. Start sample session exercises
recorded Remote ID data, without a receiver. Stop with Ctrl+C; run the launcher again
to restart. Settings and saved sessions survive in the waveraider_wr_targets volume.
Do not use docker compose down -v unless you intend to delete that data.
Updating: stop the old bundle and launch a newer extracted bundle; it reuses the volume.
For a busy port set WR_PORT=8900 in .env; open http://localhost:8900 instead.

Includes Python, all core Python dependencies (including NumPy), the web assets,
sample data, libbladeRF, bladeRF-cli and OpenSSL for node enrollment. Startup does not build or download images.
Other receiver backends, model-specific FPGA images/firmware, USB passthrough and
host drivers are separate hardware setup; see the repository README.
Experimental node reporting, optional GPSD snapshots and opt-in recording tools
are included; see NODE-SETUP.md. They do not add TDOA or a trained RF classifier.

By default only this computer can connect. To connect your phone over a trusted
private Wi-Fi network, create .env containing WR_BIND=0.0.0.0 and restart. Open
http://<NUC-IP>:8899 on the phone. Restrict the host firewall to your private subnet:
this UI has no login and connected clients can change settings/export/delete data.
Never forward this port to the internet.

SHA256SUMS checks for incomplete/corrupted extraction; obtain bundles only from the
trusted repository release. image.json records the exact image and dependency
versions are frozen inside it. CPU compatibility is not a hardware performance test.
''')
    checksums = []
    for file in sorted(args.output.iterdir()):
        digest = hashlib.sha256()
        with file.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        checksums.append(f'{digest.hexdigest()}  {file.name}\n')
    (args.output / 'SHA256SUMS').write_text(''.join(checksums))
    print(args.output)


if __name__ == '__main__':
    main()
