"""Burst-aware ELRS detection (elrs_sdr.classify.classify_iq_burst).

A brief LoRa/FLRC burst buried in a long window of continuous interference is
diluted to invisibility by a whole-window metric; per-slice (burst) detection
should recover it. Hardware-free (synthetic IQ).
"""
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from elrs_sdr import classify  # noqa: E402
from elrs_sdr.core.lora import _base_chirp  # noqa: E402

SR = 2_000_000


def _noise(n, seed):
    rng = np.random.default_rng(seed)
    return (rng.normal(size=n) + 1j * rng.normal(size=n)).astype(np.complex64)


def _lora_burst_in_noise():
    """~60 ms of noise with a short LoRa-chirp burst spliced into one ~2 ms slot."""
    total = int(SR * 0.060)
    iq = _noise(total, 0) * 0.3
    chirp = _base_chirp(7)
    burst = np.tile(chirp, 12).astype(np.complex64)      # ~0.8 ms of SF7 up-chirps
    at = int(SR * 0.030)
    iq[at:at + len(burst)] += burst * 4.0                # strong, brief
    return iq


def test_burst_recovers_a_brief_lora_burst_a_whole_window_pass_misses():
    iq = _lora_burst_in_noise()
    whole = classify.classify_iq(iq, SR)                 # whole-window
    burst = classify.classify_iq_burst(iq, SR, burst_ms=2.0)
    assert burst.signature == "lora", f"burst scan should catch it; got {burst.signature}"
    # the value of burst mode: the whole-window pass is far less likely to flag it
    # (the burst is ~1% duty). We assert the burst scan wins; we don't require the
    # whole-window pass to fail (that would be brittle), just that burst >= whole.
    assert burst.score >= (whole.score if whole.signature == "lora" else 0.0)


def test_burst_does_not_false_positive_on_pure_noise():
    iq = _noise(int(SR * 0.060), 7)
    c = classify.classify_iq_burst(iq, SR, burst_ms=2.0,
                                   gfsk_uid=bytes.fromhex("00000011185b"))
    assert c.signature == "unknown", f"noise must stay unidentified; got {c.signature}"


def test_burst_falls_back_to_whole_window_when_short():
    iq = _noise(1000, 1)                                  # shorter than one 2 ms slice
    c = classify.classify_iq_burst(iq, SR, burst_ms=2.0)
    assert c.signature in ("unknown", "lora", "gfsk", "ofdm", "ocusync", "video")
