"""Verify a completed WaveRaider recording without opening a receiver."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def verify(folder):
    import numpy as np
    folder = Path(folder)
    if folder.name.endswith('.partial'):
        raise ValueError('incomplete .partial recording')
    metadata = json.loads((folder / 'iq.sigmf-meta').read_text())
    global_ = metadata['global']
    summary = global_['wr:summary']
    if global_['core:datatype'] != 'ci16_le' or global_['wr:q11_scale'] != 1 / 2048:
        raise ValueError('unsupported sample representation')
    count, clipped, energy, peak = 0, 0, 0.0, 0
    sha256, sha512 = hashlib.sha256(), hashlib.sha512()
    with (folder / 'iq.sigmf-data').open('rb') as stream:
        for raw in iter(lambda: stream.read(1024 * 1024), b''):
            if len(raw) % 4:
                raise ValueError('truncated I/Q sample')
            sha256.update(raw)
            sha512.update(raw)
            values = np.frombuffer(raw, dtype='<i2').astype(np.float64)
            count += len(values) // 2
            clipped += int(np.count_nonzero((values <= -2048) | (values >= 2047)))
            energy += float(np.dot(values, values))
            peak = max(peak, int(np.max(np.abs(values))))
    if not count or count != summary['sample_count'] or count * 4 != summary['bytes']:
        raise ValueError('sample count/size mismatch')
    if sha256.hexdigest() != summary['sha256'] or sha512.hexdigest() != global_['core:sha512']:
        raise ValueError('recording hash mismatch')
    captures = metadata['captures']
    gaps, overruns = 0, 0
    previous = None
    for block in captures:
        start, counter = block['core:sample_start'], block['wr:sample_counter']
        if type(start) is not int or not 0 <= start < count or (previous is None and start != 0):
            raise ValueError('invalid block offset')
        if type(counter) is not int or not 0 <= counter < 2**64:
            raise ValueError('invalid sample counter')
        gap = False
        if previous:
            if start <= previous['core:sample_start']:
                raise ValueError('unordered block offsets')
            expected = previous['wr:sample_counter'] + start - previous['core:sample_start']
            gap = counter != expected
        if block['wr:discontinuity'] != gap:
            raise ValueError('inconsistent discontinuity flag')
        gaps += int(gap)
        overruns += int(bool(block['wr:status_flags'] & 1))
        previous = block
    if not captures or gaps != summary['discontinuities'] or overruns != summary['overruns']:
        raise ValueError('block metadata disagrees with summary')
    rate, elapsed = summary['sample_rate_hz'], summary['elapsed_s']
    if rate <= 0 or rate != global_['core:sample_rate'] or elapsed <= 0:
        raise ValueError('invalid recording rate or elapsed time')
    return {'integrity': 'verified', 'simulated': summary['simulated'], 'samples': count,
            'duration_s': count / rate, 'capture_elapsed_s': elapsed,
            'write_mib_per_elapsed_s': count * 4 / elapsed / 2**20,
            'peak_component_q11': peak, 'rms_component': (energy / (2 * count)) ** .5 / 2048,
            'clipped_component_fraction': clipped / (2 * count),
            'overruns': overruns, 'discontinuities': gaps,
            'field_validated': False,
            'note': 'Integrity and sample statistics only. Known-signal, sustained throughput and field checks are still required.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify(args.directory), indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'integrity': 'failed', 'error': str(exc)}))
        return 1
