"""DroneID detection on recorded IQ, when a fixture directory is available.

Set WAVERAIDER_IQ_FIXTURES to a directory holding the WaveShark recording
droneid_mini4k_2444.5M_15360k.cs8 (CC BY 4.0: "IQ recording by the WaveShark
project, https://github.com/v0l/waveshark, CC BY 4.0"). Recordings are never
committed here; without the directory these tests skip.
"""
import hashlib
import os
from pathlib import Path

import numpy as np
import pytest

from waveraider import droneid

NAME = "droneid_mini4k_2444.5M_15360k.cs8"
SHA256 = "ab9a98eae3549e104e4f9ddc12332916bd8db817d0a41e152f003b708e9b3cf5"


def _recording():
    root = os.environ.get("WAVERAIDER_IQ_FIXTURES")
    path = Path(root) / NAME if root else None
    if not root:
        pytest.skip("set WAVERAIDER_IQ_FIXTURES to run recorded validation")
    assert path.is_file(), "configured recorded DroneID fixture is missing"
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == SHA256, "fixture differs from the pinned recording"
    raw = np.frombuffer(data, dtype=np.int8).astype(np.float32)
    return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)


def _bursts(x):
    """The recording is cut to its bursts; find each one by its energy."""
    env = np.convolve(np.abs(x) ** 2, np.ones(1024) / 1024, "same")
    on = (env > np.median(env) * 20).astype(int)
    starts, ends = np.flatnonzero(np.diff(on) == 1), np.flatnonzero(np.diff(on) == -1)
    return [x[max(0, a - 2000):b + 2000] for a, b in zip(starts, ends[ends > starts[0]]) if b - a > 5000]


def _shift(x, hz):
    n = np.arange(x.size)
    return (x * np.exp(2j * np.pi * hz * n / droneid.SAMPLE_RATE)).astype(np.complex64)


def test_every_recorded_burst_is_a_candidate():
    bursts = _bursts(_recording())
    assert len(bursts) == 13
    assert all(droneid.detect(b)["droneid"] for b in bursts)


@pytest.mark.parametrize("offset_hz", [12_000.0, -18_000.0])
def test_recorded_bursts_survive_a_worse_crystal(offset_hz):
    bursts = _bursts(_shift(_recording(), offset_hz))
    assert len(bursts) == 13
    assert all(droneid.detect(b)["droneid"] for b in bursts)
