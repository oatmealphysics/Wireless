"""Every GET route that drives the radio must be gated against cross-site use.

`RF_GET_ROUTES` is what makes `web.py` require the `X-WaveRaider-Request: 1`
header (a cross-site page can make a browser GET anything, but cannot add a
custom header without a preflight this server never grants). A radio-driving
route left out of that set is reachable from any other site.

That is not hypothetical: `/api/elrs/fhss` was added here and the registration
was initially missed, and the pre-existing `RF_GETS` list in `test_monitor.py`
never covered any of the `/api/elrs*` routes either. So rather than add another
hand-maintained list that the next route can also be forgotten from, this test
DERIVES the requirement from `web.py` itself — any route whose handler opens a
`RadioDevice` must be in `RF_GET_ROUTES`.
"""
from __future__ import annotations

import re
from pathlib import Path

from waveraider import web


def _route_blocks() -> dict[str, str]:
    """Map each `if u.path == "<route>":` handler to its body text."""
    src = Path(web.__file__).read_text()
    starts = [(m.start(), m.group(1))
              for m in re.finditer(r'if u\.path == "([^"]+)":', src)]
    out = {}
    for i, (pos, route) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
        out[route] = src[pos:end]
    return out


def test_every_radio_driving_get_route_is_gated():
    blocks = _route_blocks()
    assert blocks, "no `if u.path == ...` handlers found; the parser needs updating"
    ungated = sorted(r for r, body in blocks.items()
                     if "RadioDevice(" in body and r not in web.RF_GET_ROUTES)
    assert not ungated, (
        f"these routes open the radio but are not in RF_GET_ROUTES, so a page "
        f"on another site can start RF work: {ungated}")


def test_elrs_fhss_route_exists_and_is_gated():
    assert "/api/elrs/fhss" in web.RF_GET_ROUTES
    assert "/api/elrs/fhss" in _route_blocks()


def test_rf_get_routes_has_no_stale_entries():
    """A route removed from web.py should not linger in the gate list."""
    blocks = _route_blocks()
    stale = sorted(r for r in web.RF_GET_ROUTES if r not in blocks)
    assert not stale, f"RF_GET_ROUTES lists routes web.py no longer handles: {stale}"


def test_fhss_fingerprint_is_receive_only():
    """The FHSS path must not reach the gated TX side (elrs-sdr AGENTS.md §2).

    The TX gate is load-bearing: detection and UI code paths must never import
    or call the transmit side. This asserts it for the new surface rather than
    trusting review.
    """
    from waveraider.protocols import elrs
    src = Path(elrs.__file__).read_text()
    i = src.index("def fhss_fingerprint(")
    body = src[i:]
    for forbidden in ("tx_tune", "tx_stream", "transmit(", "elrc_bench_tx"):
        assert forbidden not in body, (
            f"fhss_fingerprint() references the TX path ({forbidden!r})")
