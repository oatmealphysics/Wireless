"""Verify complete same-revision bundles and assemble permanent release assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

REQUIRED = {'waveraider.tar', 'image.json', 'bundle.json', 'architecture.txt',
            'compose.yaml', 'start.sh', 'start.cmd', 'start.ps1', 'README.txt',
            'NODE-SETUP.md', 'NODE-DATA-FORMAT.md'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify(folder, arch, revision):
    entries = {}
    for line in (folder / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_.-]+)', line)
        if not match or match[2] in entries or match[2] in ('.', '..'):
            raise ValueError('invalid or duplicate checksum entry')
        entries[match[2]] = match[1]
    actual = {p.name for p in folder.iterdir()}
    if not REQUIRED <= entries.keys() or actual != entries.keys() | {'SHA256SUMS'}:
        raise ValueError('incomplete bundle or unchecked files')
    for name, expected in entries.items():
        path = folder / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError('bundle integrity check failed: ' + name)
    metadata = json.loads((folder / 'bundle.json').read_text())
    image = json.loads((folder / 'image.json').read_text())
    if (metadata.get('schema') != 1 or metadata.get('revision') != revision
            or metadata.get('architecture') != arch or image.get('Architecture') != arch
            or image.get('Os') != 'linux' or metadata.get('image_id') != image.get('Id')
            or (folder / 'architecture.txt').read_text().strip() != arch):
        raise ValueError('bundle revision, image or architecture mismatch')
    return metadata


def prepare(bundles, output, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('expected full source revision')
    verified = {arch: verify(bundles / ('waveraider-' + arch), arch, revision)
                for arch in ('amd64', 'arm64')}
    output.mkdir(parents=True, exist_ok=False)
    for arch in verified:
        folder = bundles / ('waveraider-' + arch)
        with zipfile.ZipFile(output / (folder.name + '.zip'), 'x', zipfile.ZIP_DEFLATED,
                             compresslevel=1) as archive:
            for path in sorted(folder.iterdir()):
                archive.write(path, path.name)
    (output / 'release.json').write_text(json.dumps({'schema': 1, 'revision': revision,
        'bundles': verified}, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(
        f'{digest(path)}  {path.name}\n' for path in sorted(output.iterdir())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    prepare(args.bundles, args.output, args.revision)


if __name__ == '__main__':
    main()
