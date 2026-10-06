"""Fetch only the hash-pinned, CC BY 4.0 WaveShark Mini 4K recording.

IQ recording by the WaveShark project, https://github.com/v0l/waveshark, CC BY 4.0.
See tests/fixtures/droneid/PROVENANCE.md. No downloaded code is executed.
"""
import argparse
import hashlib
import lzma
import urllib.request
from pathlib import Path

NAME = 'droneid_mini4k_2444.5M_15360k.cs8'
PACKED_SHA = '626b82be90c7f1bc56c7f4dd8fba925c54e757a4d9571ab41a8fd023a569c109'
RAW_SHA = 'ab9a98eae3549e104e4f9ddc12332916bd8db817d0a41e152f003b708e9b3cf5'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    path = args.directory / NAME
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != RAW_SHA:
            raise ValueError('existing fixture checksum mismatch')
        return
    with urllib.request.urlopen('https://nostr.download/' + PACKED_SHA + '.xz', timeout=60) as response:
        packed = response.read()
    if hashlib.sha256(packed).hexdigest() != PACKED_SHA:
        raise ValueError('download checksum mismatch')
    raw = lzma.decompress(packed)
    if hashlib.sha256(raw).hexdigest() != RAW_SHA:
        raise ValueError('decompressed checksum mismatch')
    args.directory.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    print('Verified fixture:', NAME)


if __name__ == '__main__':
    main()
