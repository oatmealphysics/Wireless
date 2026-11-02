"""Drone band table for Inspect RF: the only ranges it tunes, shared by the backend and the drawer."""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from waveraider import bands, spectrum, uas_rf

EXPECTED = [
    ("868", "868 ctrl", "868 MHz control/telemetry", 850.0, 870.0, 866.5, 7.0),
    ("900", "900 ctrl", "900 MHz control/telemetry", 902.0, 928.0, 915.0, 26.0),
    ("1.3", "1.3 video", "1.3 GHz analog video", 1080.0, 1360.0, 1280.0, 40.0),
    ("2.4", "2.4 GHz", "2.4 GHz control/video", 2400.0, 2483.5, 2442.0, 60.0),
    ("5.2", "5.2 GHz", "5.2 GHz control/video", 5150.0, 5645.0, 5200.0, 60.0),
    ("5.8", "5.8 video", "5.8 GHz video/control", 5645.0, 5945.0, 5800.0, 60.0),
]
JS = Path(bands.__file__).with_name("inspect_rf.js").read_text()


def test_drone_band_table_in_order_with_defaults_inside_each_band():
    assert bands.DRONE_BANDS == EXPECTED and bands.DEFAULT_BAND == "2.4"
    assert [b[3] for b in EXPECTED] == sorted(b[3] for b in EXPECTED)
    for _, _, _, lo, hi, center, span in EXPECTED:
        assert lo <= center - span / 2 and center + span / 2 <= hi
    assert bands._BANDS == [(lo, hi, name) for _, _, name, lo, hi, _, _ in EXPECTED]


def test_bands_cover_every_drone_link_profile():
    # Every frequency range in the RF profile table must be tunable, so no supported link is unreachable.
    for name, _, ranges, *_ in uas_rf.SIGNATURES:
        for lo, hi in ranges:
            covered, f = True, lo
            while f < hi and covered:
                band = next((b for b in bands.DRONE_BANDS if b[3] <= f < b[4]), None)
                covered, f = band is not None, band[4] if band else f
            assert covered, f"{name} {lo}-{hi} MHz is outside the drone bands at {f} MHz"
    c, s, key = bands.fit_window(5200e6, 20e6)                  # Autel SkyLink / DJI O4 5.2 GHz stays put
    assert (c, s, key) == (5200e6, 20e6, "5.2")


def test_spectrum_uses_the_shared_table():
    assert spectrum._BANDS is bands._BANDS and spectrum.band_of is bands.band_of
    assert spectrum.fit_window is bands.fit_window


def test_drawer_carries_the_same_table():
    table = re.search(r"const DRONE_BANDS=(\[.*?\]\]);", JS, re.S).group(1)
    assert json.loads(table) == [[k, label, lo, hi, c, s] for k, label, _, lo, hi, c, s in EXPECTED]
    assert "specCenter=2442000000, specSpan=60000000" in JS


def test_band_names_are_half_open_and_outside_is_named():
    b = bands.band_of
    assert (b(849.999), b(850.0), b(869.999), b(870.0)) == (
        bands.OUTSIDE, "868 MHz control/telemetry", "868 MHz control/telemetry", bands.OUTSIDE)
    assert (b(5644.999), b(5645.0)) == ("5.2 GHz control/video", "5.8 GHz video/control")   # adjacent bands
    assert (b(2400.0), b(2483.499), b(2483.5)) == ("2.4 GHz control/video", "2.4 GHz control/video", bands.OUTSIDE)
    assert (b(101.1), b(433.92), b(1575.42)) == (bands.OUTSIDE,) * 3        # FM, 433 ISM, GPS L1 aren't drone bands


@pytest.mark.parametrize("center, span, expected", [
    (2442e6, 60e6, (2442e6, 60e6, "2.4")),              # a default view is unchanged
    (2482e6, 60e6, (2453.5e6, 60e6, "2.4")),            # moved so the window ends at the band edge
    (5800e6, 1e12, (5800e6, 60e6, "5.8")),              # span capped to the sweep limit
    (866e6, 60e6, (860e6, 20e6, "868")),                # span capped to a narrow band
    (101.1e6, 2e6, (851e6, 2e6, "868")),                # FM moves to the nearest drone band
    (5200e6, 60e6, (5200e6, 60e6, "5.2")),
    (5640e6, 60e6, (5615e6, 60e6, "5.2")),              # adjacent bands don't merge into one view
    (5645e6, 60e6, (5675e6, 60e6, "5.8")),              # exactly 5645 MHz is the first frequency of 5.8
    (5644.999e6, 60e6, (5615e6, 60e6, "5.2")),          # just below it is still 5.2
    (5645.001e6, 60e6, (5675e6, 60e6, "5.8")),
    (5645e6, 2e6, (5646e6, 2e6, "5.8")),                # a small span right at the edge
    (5150e6, 2e6, (5151e6, 2e6, "5.2")),                # lower edges of other bands
    (850e6, 2e6, (851e6, 2e6, "868")),
    (1000e6, 5e6, (925.5e6, 5e6, "900")),
    (3000e6, 20e6, (2473.5e6, 20e6, "2.4")),
    (2442e6, 1, (2442e6, bands.SPAN_MIN_HZ, "2.4")),
])
def test_fit_window_keeps_views_inside_one_band(center, span, expected):
    c, s, key = bands.fit_window(center, span)
    assert (c, s, key) == (pytest.approx(expected[0]), pytest.approx(expected[1]), expected[2])


@pytest.mark.parametrize("center, span", [(math.nan, 5e6), (math.inf, 5e6), ("x", 5e6), (None, None)])
def test_fit_window_falls_back_to_the_default_band(center, span):
    c, s, key = bands.fit_window(center, span)
    assert key == "2.4" and 2400e6 <= c - s / 2 and c + s / 2 <= 2483.5e6


def test_drawer_fit_matches_the_backend():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the drawer's band fitting")
    cases = [[2442e6, 60e6], [2482e6, 60e6], [5800e6, 1e12], [866e6, 60e6], [101.1e6, 2e6],
             [1000e6, 5e6], [3000e6, 20e6], [2442e6, 1], [1400e6, 100e6], [5645e6, 3e6], [0, 0],
             [5200e6, 20e6], [5640e6, 60e6], [855e6, 30e6], [5645e6, 60e6], [5644.999e6, 60e6],
             [5645e6, 2e6], [2483.5e6, 10e6], [870e6, 4e6]]
    src = re.search(r"const DRONE_BANDS=.*?\nfunction specVideoBand", JS, re.S).group(0).rsplit("\nfunction", 1)[0]
    js = src + "\nprocess.stdout.write(JSON.stringify(" + json.dumps(cases) + ".map(([c,s])=>specFit(c,s))));"
    got = json.loads(subprocess.check_output([node, "-e", js], text=True))
    for (c, s), g in zip(cases, got):
        ec, es, ek = bands.fit_window(c, s)
        assert (g["center"], g["span"], g["band"]) == (pytest.approx(ec), pytest.approx(es), ek)
