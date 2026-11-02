"""Offline negative-corpus validation; never captures from or transmits to a radio.

Usage: python tools/validate_droneid_corpus.py /path/to/iq
Requires numpy and scipy. Emits aggregate results only, not decoded identifiers.
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from waveraider import droneid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'tests/fixtures/droneid/negatives.json').read_text())
    results = []
    for item in manifest:
        data = (args.directory / item['name']).read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('recording checksum mismatch: ' + item['name'])
        raw = np.frombuffer(data, dtype='<i2' if item['format'] == 'cs16' else 'i1').astype(np.float32)
        if raw.size % 2:
            raise ValueError('incomplete IQ sample')
        x = raw[0::2] + 1j * raw[1::2]
        factor = math.gcd(item['rate_sps'], droneid.SAMPLE_RATE)
        x = resample_poly(x, droneid.SAMPLE_RATE // factor, item['rate_sps'] // factor).astype(np.complex64)
        # Test the complete available recording, including the final partial window.
        # Overlap keeps a pair that straddles a scan boundary inside one window.
        window, overlap = 10752000, 4 * (droneid.FFT_SIZE + droneid.CP_LEN)
        windows = candidates = 0
        for start in range(0, len(x), window - overlap):
            chunk = x[start:start + window]
            if len(chunk) < overlap:
                break
            windows += 1
            candidates += int(droneid.detect(chunk)['droneid'])
        result = dict(name=item['name'], sha256=item['sha256'], source_seconds=round(len(raw)/2/item['rate_sps'], 6), windows=windows, candidates=candidates)
        results.append(result)
        print(json.dumps(result), flush=True)
    if not results or any(r['windows'] == 0 or r['candidates'] for r in results):
        raise SystemExit('negative-corpus validation failed')


if __name__ == '__main__':
    main()
