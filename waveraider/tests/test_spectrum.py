"""Spectrum source — the simulated sweep must respond to tuning and stay bounded.
These tests force "no SDR" so they're deterministic whether or not a real radio
is plugged into the machine running them."""
import pytest

from waveraider import bands, spectrum


@pytest.fixture(autouse=True)
def _no_sdr(monkeypatch):
    """Default to no attached radio (simulated source). Tests that need a device
    re-patch interfaces.detect themselves, overriding this."""
    monkeypatch.setattr(spectrum.interfaces, "detect",
                        lambda: {"capture_devices": [], "other_usb_count": 0,
                                 "network_interfaces": []})


def test_source_status_shape():
    st = spectrum.source_status()
    assert st["mode"] in ("simulated", "live")
    assert st["note"]


def test_sweep_shape_and_range():
    d = spectrum.sweep(2_442_000_000, 20_000_000, bins=256)
    assert len(d["points"]) == 256
    assert d["start_hz"] == 2_432_000_000 and d["stop_hz"] == 2_452_000_000 and d["band"] == "2.4"
    assert d["source"] == "simulated"          # no SDR in CI/dev
    assert all(len(p) == 2 for p in d["points"])


def test_tuning_reveals_the_band_you_point_at():
    # The simulated 2.4 GHz band has a strong Wi-Fi-like emitter near 2437 MHz.
    wifi = spectrum.sweep(2_442_000_000, 60_000_000, bins=256)
    assert wifi["peak"]["db"] >= wifi["floor_db"] + 30
    assert abs(wifi["peak"]["freq_hz"] - 2_437_000_000) < 1_000_000
    # The top of the 900 MHz band has no emitter — only noise near the floor.
    quiet = spectrum.sweep(924_000_000, 8_000_000, bins=256)
    assert quiet["peak"]["db"] < quiet["floor_db"] + 15


def test_narrowband_emitter_survives_wide_span():
    # The 868.3 MHz telemetry burst is narrower than a bin across the whole 868 band,
    # but must still appear as a spike.
    d = spectrum.sweep(866_500_000, 7_000_000, bins=256)
    assert d["peak"]["db"] >= d["floor_db"] + 20
    assert abs(d["peak"]["freq_hz"] - 868_300_000) < 30_000


@pytest.mark.parametrize("center, span", [
    (101_100_000, 2_000_000), (10_000_000, 2_000_000), (6_000_000_000, 60_000_000),
    (1_575_420_000, 20_000_000), (2_442_000_000, 10 ** 12), (float("nan"), 5_000_000)])
def test_sweeps_never_leave_the_drone_bands(center, span):
    d = spectrum.sweep(center, span, bins=64)
    lo, hi = next((b[3] * 1e6, b[4] * 1e6) for b in bands.DRONE_BANDS if b[0] == d["band"])
    assert lo <= d["start_hz"] and d["stop_hz"] <= hi
    assert all(s["band"] != bands.OUTSIDE for s in d["signals"])


def test_simulated_emitters_sit_in_the_drone_bands():
    assert all(bands.band_of(f / 1e6) != bands.OUTSIDE for f, _, _, _ in spectrum._EMITTERS)
    assert {bands.band_of(f / 1e6) for f, _, _, _ in spectrum._EMITTERS} == {b[2] for b in bands.DRONE_BANDS}


def test_acquire_records_the_fitted_view():
    try:
        assert spectrum.acquire("fit-check", 101_100_000, 2_000_000)["ok"] is True
        snap = spectrum.lease_snapshot()
        assert (snap["center_hz"], snap["span_hz"]) == (851_000_000, 2_000_000)
    finally:
        spectrum.release("fit-check")


def test_inputs_are_clamped():
    d = spectrum.sweep(2_442_000_000, 10 ** 12, bins=99999)   # absurd span + bins
    assert d["span_hz"] <= spectrum.SPAN_MAX_HZ
    assert len(d["points"]) <= spectrum.BINS_MAX


# ---- capture backends + readiness (SDR prep) ----------------------------

def test_parse_power_csv_rtl_format():
    # rtl_power / hackrf_sweep CSV: date,time,low,high,step,samples,dB...
    csv = "2026-09-14, 10:00:00, 100000000, 100000400, 100.00, 1024, -50.0, -48.0, -12.0, -49.0\n"
    pts = spectrum._parse_power_csv(csv)
    assert pts is not None and len(pts) == 4
    assert pts[0][0] == 100_000_000                       # first bin at Hz_low
    assert max(pts, key=lambda p: p[1])[1] == -12.0       # the strong bin


def _fake_detect(model, usb_id, usb_name):
    return lambda: {"capture_devices": [{"kind": "sdr", "id": usb_id,
                                         "model": model, "usb_name": usb_name}],
                    "other_usb_count": 0, "network_interfaces": []}


def test_readiness_signalhound_without_sdk(monkeypatch):
    monkeypatch.setattr(spectrum.interfaces, "detect",
                        _fake_detect("SignalHound BB60C", "2eb8:0012", "SignalHound BB60C"))
    monkeypatch.setattr(spectrum, "_signalhound_ok", lambda: False)
    r = spectrum.readiness()
    assert r["sdr_detected"] and r["backend"] == "signalhound"
    assert r["live_capable"] is False and r["install_hint"]
    assert spectrum.source_status()["mode"] == "simulated"


def test_readiness_rtl_ready_goes_live(monkeypatch):
    monkeypatch.setattr(spectrum.interfaces, "detect",
                        _fake_detect("RTL-SDR (RTL2832U)", "0bda:2832", "RTL2832U"))
    # rtl_power present on PATH -> the rtl backend's tool check passes
    monkeypatch.setattr(spectrum.shutil, "which",
                        lambda n: "/usr/bin/rtl_power" if n == "rtl_power" else None)
    r = spectrum.readiness()
    assert r["backend"] == "rtl_power" and r["live_capable"] is True
    assert spectrum.source_status()["mode"] == "live"


def test_sweep_reports_signals_and_bands():
    d = spectrum.sweep(2_442_000_000, 60_000_000, 256)   # 2.4 GHz band
    assert any(abs(s["freq_hz"] - 2_437_000_000) < 1_000_000 for s in d["signals"])
    assert all(s["band"] == "2.4 GHz control/video" for s in d["signals"])
    assert all(s["protocol"] is None for s in d["signals"])      # no protocol analyzer to hand off to
    assert [b["name"] for b in d["bands"]] == ["2.4 GHz control/video"]


def test_analyze_signal_needs_a_live_sdr():
    # autouse _no_sdr fixture -> simulated -> analysis is unavailable with a note
    r = spectrum.analyze_signal(101_100_000)
    assert r["available"] is False and "SDR" in r["note"]


def test_analyze_signal_backend_not_wired(monkeypatch):
    # a live but non-capturing backend reports it's not wired, doesn't crash
    monkeypatch.setattr(spectrum, "source_status",
                        lambda selected=None: {"mode": "live", "device": "X",
                                               "backend": "hackrf", "note": "live"})
    r = spectrum.analyze_signal(101_100_000)
    assert r["available"] is False and "hackrf" in r["note"]


def test_droneid_scan_needs_wideband_sdr():
    # no SDR (autouse fixture) -> unavailable with a note
    r = spectrum.scan_droneid()
    assert r["available"] is False and "SDR" in r["note"]


def test_droneid_scan_rejects_narrowband_backend(monkeypatch):
    # a live RTL can't span DroneID's 10 MHz -> refused with an explanation
    monkeypatch.setattr(spectrum, "source_status",
                        lambda selected=None: {"mode": "live", "device": "RTL",
                                               "backend": "rtl_power", "note": "live"})
    r = spectrum.scan_droneid()
    assert r["available"] is False and "rtl_power" in r["note"]


def test_shared_band_hints_are_not_drone_detections():
    # Even outside Wi-Fi, frequency overlap is not protocol evidence.
    d = spectrum.sweep(915_000_000, 20_000_000, 256)
    assert any(s["rf_candidates"] for s in d["signals"])
    assert all(s["drone"] == [] for s in d["signals"])


@pytest.mark.parametrize("center", [2412e6, 2437e6, 2462e6, 5180e6, 5805e6])
@pytest.mark.parametrize("width", [500e3, 20e6, 40e6])
def test_wifi_band_power_peaks_do_not_identify_drones(center, width):
    # A smooth or narrow power lump can overlap drone profiles. Power alone
    # carries no identity, even when a 20 MHz lump ranks DJI first.
    points = [[center + (i - 40) * width / 20,
               -40.0 - min(55.0, abs(i - 40) * 0.6)] for i in range(81)]
    signals = spectrum._find_signals(points, floor=-95.0, span=4 * width)
    assert signals and any(s["rf_candidates"] for s in signals)
    assert all(s["drone"] == [] and s["identification"] == "unidentified"
               for s in signals)
    assert all("no drone-specific evidence" in s["identification_note"] for s in signals)


def test_no_protocol_hand_off_remains(monkeypatch):
    monkeypatch.setattr(spectrum, "source_status", lambda selected=None: {
        "mode": "simulated", "device": None, "backend": None, "note": "simulated"})
    assert not hasattr(spectrum, "protocol_for")
    for center in (101_100_000, 390_000_000, 942_000_000, 2_437_000_000):
        assert all(s["protocol"] is None for s in spectrum.sweep(center, 20_000_000, 256)["signals"])


def test_bands_clip_to_window():
    assert spectrum._bands_in(2_390_000_000, 2_490_000_000) == [
        {"name": "2.4 GHz control/video", "start_hz": 2_400_000_000, "stop_hz": 2_483_500_000}]
    inside = spectrum._bands_in(5_700_000_000, 5_760_000_000)
    assert inside == [{"name": "5.8 GHz video/control", "start_hz": 5_700_000_000, "stop_hz": 5_760_000_000}]
    assert spectrum._bands_in(88_000_000, 108_000_000) == []


def test_find_signals_dedupes_and_tags_band():
    pts = [[100_000_000, -95.0], [101_100_000, -40.0], [101_120_000, -41.0],
           [102_000_000, -95.0], [124_000_000, -60.0]]
    sig = spectrum._find_signals(pts, floor=-95.0, span=25_000_000)
    freqs = [s["freq_hz"] for s in sig]
    assert 101_100_000 in freqs and 124_000_000 in freqs
    assert 101_120_000 not in freqs           # merged into the stronger neighbor


def test_bladerf_uses_native_backend(monkeypatch):
    from waveraider import bladerf_sweep
    monkeypatch.setattr(spectrum.interfaces, "detect",
                        _fake_detect("bladeRF 2.0 micro", "2cf0:5250", "bladeRF"))
    monkeypatch.setattr(bladerf_sweep, "available", lambda: True)
    r = spectrum.readiness()
    assert r["backend"] == "bladerf" and r["live_capable"] is True
    # without libbladeRF it degrades to simulated with a hint
    monkeypatch.setattr(bladerf_sweep, "available", lambda: False)
    r2 = spectrum.readiness()
    assert r2["backend"] == "bladerf" and r2["live_capable"] is False and r2["install_hint"]


def test_two_radios_listed_and_selectable(monkeypatch):
    from waveraider import bladerf_sweep
    # A NooElec RTL dongle + a bladeRF plugged in together.
    monkeypatch.setattr(spectrum.interfaces, "detect", lambda: {
        "capture_devices": [
            {"kind": "sdr", "id": "0bda:2838", "model": "RTL-SDR (RTL2838)", "usb_name": "NooElec NESDR"},
            {"kind": "sdr", "id": "2cf0:5250", "model": "bladeRF 2.0 micro", "usb_name": "bladeRF"},
        ], "other_usb_count": 0, "network_interfaces": []})
    # only rtl_power installed; bladeRF backend unavailable -> RTL is the live one
    monkeypatch.setattr(spectrum.shutil, "which",
                        lambda n: "/usr/bin/rtl_power" if n == "rtl_power" else None)
    monkeypatch.setattr(bladerf_sweep, "available", lambda: False)
    r = spectrum.readiness()
    assert len(r["radios"]) == 2
    # default chosen radio = the first live-capable one (the RTL)
    assert r["usb_id"] == "0bda:2838" and r["live_capable"] is True
    # explicitly selecting the bladeRF switches the chosen radio (native backend, not live here)
    r2 = spectrum.readiness(selected="2cf0:5250")
    assert r2["device"] == "bladeRF 2.0 micro" and r2["backend"] == "bladerf"
    assert r2["live_capable"] is False


def test_readiness_surfaces_unrecognized_usb(monkeypatch):
    monkeypatch.setattr(spectrum.interfaces, "detect",
                        lambda: {"capture_devices": [], "other_usb_count": 1,
                                 "network_interfaces": []})
    monkeypatch.setattr(spectrum.interfaces, "unrecognized_usb",
                        lambda: [{"id": "2eb8:9999", "name": "SignalHound (unknown PID)"}])
    r = spectrum.readiness()
    assert r["sdr_detected"] is False
    assert any("2eb8" in d["id"] for d in r["unrecognized_usb"])
