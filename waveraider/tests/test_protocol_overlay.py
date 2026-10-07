"""Protocol-hint overlay: spectrum._annotate_elrs_hint attaches a multi-protocol
hint from the shared elrs_sdr.classify brain, using the same per-carrier IQ grab.

Hardware-free: the SDR capture is monkeypatched to return a synthetic LoRa
preamble, so the test exercises the wiring (capture -> classify -> annotate)
without a radio.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

# elrs_sdr lives at the repo root (two levels up from waveraider/waveraider).
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from waveraider import spectrum  # noqa: E402
from waveraider import uhd_backend  # noqa: E402


def _synthetic_lora_iq(sf: int = 7, srate: int = 2_000_000) -> np.ndarray:
    """A train of SF7 up-chirps — what the capture-validated chirp detector fires on."""
    from elrs_sdr.core.lora import _base_chirp
    chirp = _base_chirp(sf)
    reps = int(np.ceil(srate * 0.02 / len(chirp)))
    return np.tile(chirp, reps).astype(np.complex64)


def test_annotate_attaches_protocol_hint_for_lora(monkeypatch):
    iq = _synthetic_lora_iq()
    monkeypatch.setattr(uhd_backend, "capture_iq",
                        lambda f, rate, n: (iq, 2_000_000))
    signals = [{"freq_hz": 2_440_000_000, "db": -6.0, "rf_candidates": []}]

    spectrum._annotate_elrs_hint(signals, "uhd")

    hint = signals[0].get("protocol_hint")
    assert hint is not None, "a positive LoRa window should attach a protocol_hint"
    assert hint["signature"] == "lora"
    assert hint["confirmed"] is True            # LoRa chirp is the capture-validated detector
    assert "ExpressLRS (possible)" in signals[0]["rf_candidates"]   # existing behavior preserved


def test_annotate_leaves_noise_unhinted(monkeypatch):
    rng = np.random.default_rng(0)
    noise = (rng.normal(size=40_000) + 1j * rng.normal(size=40_000)).astype(np.complex64)
    monkeypatch.setattr(uhd_backend, "capture_iq",
                        lambda f, rate, n: (noise, 2_000_000))
    signals = [{"freq_hz": 2_440_000_000, "db": -6.0, "rf_candidates": []}]

    spectrum._annotate_elrs_hint(signals, "uhd")

    # Positive-ID only: pure noise clears no detector, so no hint is attached.
    assert "protocol_hint" not in signals[0]


def test_annotate_is_soft_on_capture_failure(monkeypatch):
    monkeypatch.setattr(uhd_backend, "capture_iq",
                        lambda f, rate, n: (None, 0))
    signals = [{"freq_hz": 2_440_000_000, "db": -6.0, "rf_candidates": []}]
    # Must not raise, and must leave the signal unmodified when IQ can't be grabbed.
    spectrum._annotate_elrs_hint(signals, "uhd")
    assert "protocol_hint" not in signals[0]
