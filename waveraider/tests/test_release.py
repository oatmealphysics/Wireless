"""Promotion must reject incomplete, corrupted, or mixed-revision downloads."""
import json
from pathlib import Path
import zipfile

import pytest

from tools.prepare_release import REQUIRED, digest, prepare

SHA = 'a' * 40


def bundles(tmp_path):
    root = tmp_path / 'bundles'
    for arch in ('amd64', 'arm64'):
        folder = root / ('waveraider-' + arch)
        folder.mkdir(parents=True)
        for name in REQUIRED:
            (folder / name).write_text('fixture')
        (folder / 'bundle.json').write_text(json.dumps({'schema': 1, 'revision': SHA,
            'architecture': arch, 'image_id': 'sha256:abc'}))
        (folder / 'image.json').write_text(json.dumps({'Architecture': arch, 'Os': 'linux', 'Id': 'sha256:abc'}))
        (folder / 'architecture.txt').write_text(arch + '\n')
        checksums(folder)
    return root


def checksums(folder):
    (folder / 'SHA256SUMS').write_text(''.join(
        f'{digest(p)}  {p.name}\n' for p in sorted(folder.iterdir()) if p.name != 'SHA256SUMS'))


def test_release_contains_both_verified_architectures(tmp_path):
    prepare(bundles(tmp_path), tmp_path / 'release', SHA)
    with zipfile.ZipFile(tmp_path / 'release/waveraider-arm64.zip') as archive:
        assert set(archive.namelist()) == REQUIRED | {'SHA256SUMS'}
    assert json.loads((tmp_path / 'release/release.json').read_text())['revision'] == SHA


@pytest.mark.parametrize('problem', ['corrupt', 'missing', 'revision', 'architecture', 'extra', 'symlink'])
def test_bad_bundle_never_produces_release_assets(tmp_path, problem):
    root = bundles(tmp_path)
    folder = root / 'waveraider-arm64'
    if problem == 'corrupt':
        (folder / 'waveraider.tar').write_text('changed')
    elif problem == 'missing':
        (folder / 'start.cmd').unlink()
        checksums(folder)
    elif problem in ('revision', 'architecture'):
        meta = json.loads((folder / 'bundle.json').read_text())
        meta[problem] = 'b' * 40 if problem == 'revision' else 'amd64'
        (folder / 'bundle.json').write_text(json.dumps(meta))
        checksums(folder)
    elif problem == 'extra':
        (folder / 'unchecked').write_text('extra')
    else:
        (folder / 'start.cmd').unlink()
        (folder / 'start.cmd').symlink_to(folder / 'start.sh')
    with pytest.raises(ValueError):
        prepare(root, tmp_path / 'release', SHA)
    assert not (tmp_path / 'release').exists()


def test_publication_requires_both_checks_and_only_master():
    workflow = (Path(__file__).parents[1] / '.github/workflows/release.yml').read_text()
    assert 'needs: [tests, bundles]' in workflow
    assert "if: github.ref == 'refs/heads/master'" in workflow
    assert '"$current" != "$GITHUB_SHA"' in workflow
    assert '--target "$GITHUB_SHA" --latest' in workflow
    assert 'release delete' not in workflow and '--clobber' not in workflow
