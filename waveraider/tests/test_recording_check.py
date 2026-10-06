import json

import pytest

from waveraider.recording import record
from waveraider.recording_check import verify


def capture(tmp_path):
    return record(tmp_path / 'iq', node_id='a', frequency=2440000000, rate=160000,
                  seconds=.01, label='synthetic verification', simulated=True)[0]


def test_verified_noise_is_not_field_validation(tmp_path):
    result = verify(capture(tmp_path))
    assert result['integrity'] == 'verified' and result['samples'] == 1600
    assert result['simulated'] and not result['field_validated']
    assert result['rms_component'] > 0 and result['overruns'] == 0


@pytest.mark.parametrize('problem', ['hash', 'count', 'counter', 'partial'])
def test_inconsistent_capture_rejected(tmp_path, problem):
    folder = capture(tmp_path)
    if problem == 'partial':
        new = folder.with_suffix('.partial')
        folder.rename(new)
        folder = new
    elif problem == 'hash':
        data = folder / 'iq.sigmf-data'
        data.write_bytes(b'\0' * data.stat().st_size)
    else:
        path = folder / 'iq.sigmf-meta'
        metadata = json.loads(path.read_text())
        if problem == 'count':
            metadata['global']['wr:summary']['sample_count'] += 1
        else:
            metadata['captures'][0]['wr:discontinuity'] = True
        path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        verify(folder)
