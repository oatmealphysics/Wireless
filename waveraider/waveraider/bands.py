"""Drone frequency bands for Inspect RF: the only ranges it tunes, and the names shown on
signal rows and as the plot overlay.

A band name says where a frequency sits, not what is transmitting there. These bands are
shared with Wi-Fi and other ISM devices, so a name isn't a decoded protocol or a drone
detection. Ranges follow the drone link profiles in uas_rf (FCC/ETSI region) and are
half-open: lo <= f < hi.
"""
from __future__ import annotations

import math

# (key, tab label, band name, lo MHz, hi MHz, default center MHz, default span MHz), lowest first.
# Together they cover every band in uas_rf.SIGNATURES (for example Crossfire from 850 MHz, and
# 5.15–5.25 GHz Autel SkyLink and DJI O4). The default view is what a band tab opens;
# inspect_rf.js carries the same table.
DRONE_BANDS = [
    ("868", "868 ctrl", "868 MHz control/telemetry", 850.0, 870.0, 866.5, 7.0),
    ("900", "900 ctrl", "900 MHz control/telemetry", 902.0, 928.0, 915.0, 26.0),
    ("1.3", "1.3 video", "1.3 GHz analog video", 1080.0, 1360.0, 1280.0, 40.0),
    ("2.4", "2.4 GHz", "2.4 GHz control/video", 2400.0, 2483.5, 2442.0, 60.0),
    ("5.2", "5.2 GHz", "5.2 GHz control/video", 5150.0, 5645.0, 5200.0, 60.0),
    ("5.8", "5.8 video", "5.8 GHz video/control", 5645.0, 5945.0, 5800.0, 60.0),
]
DEFAULT_BAND = "2.4"
SPAN_MIN_HZ = 10_000
SPAN_MAX_HZ = 60_000_000
OUTSIDE = "Outside the drone bands"

_BANDS = [(lo, hi, name) for _, _, name, lo, hi, _, _ in DRONE_BANDS]


def band_of(freq_mhz: float) -> str:
    for lo, hi, name in _BANDS:
        if lo <= freq_mhz < hi:
            return name
    return OUTSIDE


def _band(key: str):
    return next(b for b in DRONE_BANDS if b[0] == key)


def fit_window(center_hz: float, span_hz: float) -> tuple[float, float, str]:
    """Fit a requested view inside one drone band: the band holding the center (or the
    nearest one), with the span capped to that band and the center moved so the whole
    window stays inside it. Returns (center_hz, span_hz, band key)."""
    try:
        center, span = float(center_hz), float(span_hz)
    except (TypeError, ValueError):
        center = span = math.nan
    if not math.isfinite(center):
        _, _, _, _, _, c, s = _band(DEFAULT_BAND)
        center, span = c * 1e6, s * 1e6
    if not math.isfinite(span):
        span = SPAN_MAX_HZ

    def distance(band):
        return min(abs(center - band[3] * 1e6), abs(center - band[4] * 1e6))

    # The band containing the center wins, so an adjacent band's excluded upper edge never ties with it.
    containing = next((b for b in DRONE_BANDS if b[3] * 1e6 <= center < b[4] * 1e6), None)
    key, _, _, lo_mhz, hi_mhz, _, _ = containing or min(DRONE_BANDS, key=distance)
    lo, hi = lo_mhz * 1e6, hi_mhz * 1e6
    span = max(SPAN_MIN_HZ, min(span, SPAN_MAX_HZ, hi - lo))
    center = max(lo + span / 2, min(center, hi - span / 2))
    return center, span, key
