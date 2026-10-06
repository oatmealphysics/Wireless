"""SDR arbitration — the Spectrum view owns the radio exclusively while active."""
import time

import pytest

from waveraider import spectrum


@pytest.fixture
def live(monkeypatch):
    """Pretend an RTL-SDR is present so ownership is enforced, and reset state."""
    monkeypatch.setattr(spectrum, "source_status",
                        lambda selected=None: {"mode": "live", "device": "RTL-SDR (test)",
                                               "backend": "rtl_power", "note": "test"})
    # rtl_power isn't really there; make the "live" sweep return deterministic points
    monkeypatch.setattr(spectrum, "_rtl_power_points",
                        lambda a, b, n, device=None: [[int(a), -80.0], [int(b), -80.0]])
    spectrum.release("A"); spectrum.release("B"); spectrum.release("C")
    spectrum._control.update(owner=None, since=0.0, last=0.0)
    yield spectrum
    spectrum._control.update(owner=None, since=0.0, last=0.0)


def test_exclusive_acquire(live):
    assert live.acquire("A", 98e6, 20e6)["ok"] is True
    r = live.acquire("B", 98e6, 20e6)
    assert r["ok"] is False and r["held_by"] == "A"


def test_other_session_sweep_is_refused_while_owned(live):
    live.acquire("A", 98e6, 20e6)
    assert live.sweep(98e6, 20e6, 64, owner="B").get("error") == "busy"
    assert live.sweep(98e6, 20e6, 64, owner="A").get("controlled") is True


def test_release_frees_the_radio(live):
    live.acquire("A", 98e6, 20e6)
    live.release("A")
    assert live.acquire("B", 100e6, 1e6)["ok"] is True


def test_lease_auto_expires(live):
    live.acquire("A", 98e6, 20e6)
    live._control["last"] -= (live.LEASE_TIMEOUT + 1)     # simulate a dead session
    assert live.control_status()["controlled"] is False   # expired -> free
    assert live.acquire("B", 100e6, 1e6)["ok"] is True


def test_simulated_needs_no_control(monkeypatch):
    # With no SDR, a sweep works for anyone (nothing to own).
    monkeypatch.setattr(spectrum, "source_status",
                        lambda selected=None: {"mode": "simulated", "device": None,
                                               "backend": None, "note": "no SDR"})
    d = spectrum.sweep(98e6, 20e6, 64, owner="anyone")
    assert d["source"] == "simulated" and d["controlled"] is False


def test_lease_snapshot_reports_held_and_free_without_changing_the_lease(live):
    assert live.lease_snapshot()["held"] is False
    live.acquire("A", 2442e6, 60e6)
    snap = live.lease_snapshot()
    assert (snap["held"], snap["owner"], snap["center_hz"], snap["span_hz"]) == (True, "A", 2442000000, 60000000)
    assert snap["held_sec"] >= 0 and snap["lease_timeout_s"] == live.LEASE_TIMEOUT
    live._control["last"] -= (live.LEASE_TIMEOUT + 1)     # the view went away
    expired = live.lease_snapshot()
    assert (expired["held"], expired["owner"]) == (False, None)
    assert live._control["owner"] == "A"                  # reading neither clears nor takes the lease
