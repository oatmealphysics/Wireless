"""Drone RF signature knowledge base — band matching + tiering."""
from waveraider import uas_rf


def _names(freq_mhz):
    return [c["name"] for c in uas_rf.candidates_at(freq_mhz)]


def test_900_control_band():
    names = _names(915)
    assert "ExpressLRS 900" in names and "TBS Crossfire" in names and "FrSky R9 900" in names
    # most-exploitable first: ELRS (tier 1) leads the proprietary tier-2 links
    assert uas_rf.candidates_at(915)[0]["tier"] == 1


def test_58_video_raceband():
    names = _names(5806)                      # raceband R5
    assert "Analog FPV 5.8" in names and "DJI OcuSync / DroneID" in names
    assert "HDZero" in names and "Walksnail Avatar HD" in names


def test_24_reference_includes_multiple_shared_band_protocols():
    names = _names(2442)
    assert {"ExpressLRS 2.4", "DJI OcuSync / DroneID", "Spektrum DSMX/DSM2"} <= set(names)
    assert uas_rf.candidates_at(2442)[0]["tier"] == 1     # decodable listed first


def test_13ghz_video():
    assert _names(1280) == ["Analog FPV 1.2/1.3"]


def test_non_drone_frequency_is_empty():
    assert uas_rf.candidates_at(101.1) == []              # FM broadcast
    assert uas_rf.in_drone_band(101.1) is False
    assert uas_rf.in_drone_band(915) is True


def test_classify_narrows_by_bandwidth():
    # Bandwidth ranks reference compatibility, not identity or drone likelihood.
    narrow = uas_rf.classify(2442, bw_khz=500)       # ~500 kHz -> control link
    assert narrow[0].get("bw_fit") and narrow[0]["name"] in (
        "ExpressLRS 2.4", "FrSky ACCST/ACCESS 2.4", "Flysky AFHDS2A")
    wide = uas_rf.classify(2442, bw_khz=20000)        # ordinary Wi-Fi can also match
    assert wide[0]["name"] == "DJI OcuSync / DroneID" and wide[0].get("bw_fit")


def test_catalog_has_tiers():
    cat = uas_rf.catalog()
    assert len(cat) == len(uas_rf.SIGNATURES)
    assert all("tier_label" in c and c["bands_mhz"] for c in cat)
    # DroneID entry flags operator-position decode
    dji = next(c for c in cat if c["name"].startswith("DJI"))
    assert "OPERATOR" in dji["note"]
