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


# --------------------------------------------------------------------------- #
# The Inspect RF control for the hop fingerprint
# --------------------------------------------------------------------------- #
def _inspect_rf_src() -> str:
    from waveraider import monitor
    return Path(monitor.__file__).with_name("inspect_rf.js").read_text()


def test_hop_fingerprint_control_exists_and_is_wired():
    src = _inspect_rf_src()
    assert "function specElrsFhss(" in src
    assert "onclick=\"specElrsFhss()\"" in src, "the button must call the handler"
    assert "/api/elrs/fhss" in src


def test_hop_fingerprint_request_carries_the_marker_header():
    """It must go through specApi, not a raw fetch.

    `specApi` is what adds `X-WaveRaider-Request: 1`, and the server refuses
    radio GETs without it, so a raw `fetch(` here would simply 403 — and would
    also break test_monitor's invariant that every request goes through the one
    helper.
    """
    src = _inspect_rf_src()
    assert src.count("fetch(") == 1, (
        "exactly one fetch( may exist (inside specApi); the fingerprint call "
        "must not add its own")
    body = src[src.index("function specElrsFhss("):]
    body = body[:body.index("\nfunction ")]
    assert "specApi(" in body and "fetch(" not in body


def test_hop_fingerprint_wording_does_not_claim_an_aircraft_identity():
    """WaveRaider's wording discipline, enforced rather than reviewed.

    The README capability table is the boundary: UI text must not claim the tool
    detects, identifies or tracks aircraft. A hop fingerprint identifies a radio
    LINK — it is not an airframe, a serial, or a pilot. It is an easy line to
    cross while editing copy, so it is pinned here.
    """
    src = _inspect_rf_src()
    body = src[src.index("function specElrsFhss("):]
    body = body[:body.index("\nfunction ")]
    low = body.lower()
    for claim in ("drone identified", "aircraft identified", "identifies the drone",
                  "identifies the aircraft", "drone detected", "tracks the drone"):
        assert claim not in low, f"UI text must not claim {claim!r}"
    assert "not an aircraft" in low, (
        "the result must say explicitly that this identifies a link, not an aircraft")
    # a quiet receiver must not be reported as proof of absence
    assert "does not establish" in low, (
        "the no-result branch must not imply that nothing is present")


def test_hop_fingerprint_reports_its_evidence_class():
    """AGENTS.md §3: a detection shown without its evidence class is an overclaim."""
    body = _inspect_rf_src()
    body = body[body.index("function specElrsFhss("):]
    body = body[:body.index("\nfunction ")]
    assert "r.evidence" in body, "the rendered result must surface the evidence label"


# --------------------------------------------------------------------------- #
# A per-capture digest must never be presented as a stable link identifier
# --------------------------------------------------------------------------- #
def _fake_fold(positions, absolute=True, sync_channel=41):
    return {
        "path": "x.complex8", "duration_s": 20.0, "sample_rate_hz": 1e7,
        "center_freq_hz": 2.4404e9, "snr_gate_db": 47.0, "n_bursts": 700,
        "n_offgrid": 0, "n_dwells": 100, "channels_seen": [35, 45],
        "hop_interval_ms": 26.664, "hops_per_s": 37.5, "seq_len": 240,
        "absolute": absolute, "n_positions": len(positions),
        "fingerprint": {str(p): c for p, c in positions.items()},
        "sync": {"channel": sync_channel, "mean_gap_hops": 80.0,
                 "spread_hops": 0.2, "n_sightings": 9, "index_offset": 0,
                 "sync_channel_formula": 40, "formula_disagrees": True,
                 "elrs_generation": "elrs<=3.x", "block_start_slots": [0],
                 "hop_interval_refined_s": 0.0266, "unique": True},
    }


def test_capture_digest_is_not_offered_as_a_stable_link_id(monkeypatch):
    """Regression: two dwells on the SAME link must not yield two identifiers.

    This was a real bug. `link_id` was a hash of the recovered sequence
    positions, and two captures of one link recovered 32 and 31 positions, so
    the hash differed completely (e65504306293 vs 58abf4f227ca) — the UI would
    have shown two IDs for one aircraft. A hash over a variable set cannot be a
    stable ID. The digest is still reported, but only as a per-capture value,
    and `link_id` stays None until something link-stable confirms it.
    """
    from elrs_sdr.core import fhss_observe as fo
    from waveraider.protocols import elrs

    a = {0: 41, 4: 44, 14: 38, 24: 39, 30: 43}
    b = dict(a); b.pop(30)                     # one position fewer, same link

    seen = []
    monkeypatch.setattr(fo, "observe_capture",
                        lambda p, snr_min=None: _fake_fold(seen.pop(0)))

    seen[:] = [a]
    ra = elrs._fhss_analyse("x.complex8")
    seen[:] = [b]
    rb = elrs._fhss_analyse("x.complex8")

    assert ra["fingerprint_digest"] != rb["fingerprint_digest"], (
        "the fixture is wrong: the two folds should digest differently")
    assert ra["link_id"] is None and rb["link_id"] is None, (
        "a per-capture digest must not be reported as link_id")
    assert "not stable" in (ra.get("fingerprint_note") or "").lower()


def test_link_id_appears_only_when_a_seed_confirms_and_is_stable(monkeypatch):
    """The seed is a property of the LINK, so it is the same on both captures."""
    from elrs_sdr.core import fhss_observe as fo
    from elrs_sdr.core import fhss_seed as fs
    from waveraider.protocols import elrs

    monkeypatch.setattr(fo, "observe_capture",
                        lambda p, snr_min=None: _fake_fold({0: 41, 4: 44, 14: 38}))
    monkeypatch.setattr(fs, "best_rotation",
                        lambda *a, **k: {"match": True, "n_match": 3,
                                         "n_checked": 3, "rotation": 80,
                                         "fraction": 1.0})
    r = elrs._fhss_analyse("x.complex8", verify_seed=1120344)
    assert r["net_id"] == f"{1120344:08x}"
    assert r["link_id"] == r["net_id"], "a confirmed seed IS the stable link id"
    assert "seed" in (r.get("link_id_basis") or "").lower()

    monkeypatch.setattr(fs, "best_rotation",
                        lambda *a, **k: {"match": False, "n_match": 1,
                                         "n_checked": 3, "rotation": 7,
                                         "fraction": 0.33})
    r2 = elrs._fhss_analyse("x.complex8", verify_seed=999)
    assert r2.get("net_id") is None and r2["link_id"] is None, (
        "a seed that does not confirm must not produce an identifier")
