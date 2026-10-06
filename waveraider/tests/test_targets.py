"""Watchlist matching — an entry matches an entity's identifier fields whatever kind of
entity carries them (older entry types from v0.5.1 included), without false-matching
on measurement attrs."""
import pytest

from waveraider import targets
from waveraider.model import Entity, Survey


@pytest.fixture
def watch(tmp_path, monkeypatch):
    """Point the watchlist at a throwaway file (never the user's real one)."""
    monkeypatch.setenv("WAVERAIDER_TARGETS", str(tmp_path / "targets.json"))
    return targets


def _ent(id, kind, label="", **attrs):
    return Entity(id=id, kind=kind, label=label, attrs=attrs, observations=1)


# ---- matching, protocol by protocol -------------------------------------

def test_mac_matches_wifi_ble_bt_regardless_of_separators(watch):
    watch.add("mac", "AA:BB:CC:11:22:33")
    for kind, eid in [("access_point", "aa-bb-cc-11-22-33"),
                      ("ble_device", "AABB.CC11.2233"),
                      ("bt_device", "aa:bb:cc:11:22:33")]:
        assert watch.match(_ent(eid, kind)) == ["AA:BB:CC:11:22:33"], kind


def test_bd_addr_type_matches_bt_device(watch):
    watch.add("bd_addr", "DE:AD:BE:EF:00:01")
    assert watch.match(_ent("DE:AD:BE:EF:00:01", "bt_device", "JBL Flip 5")) \
        == ["DE:AD:BE:EF:00:01"]


def test_ssid_matches_wifi_by_attr_or_label(watch):
    watch.add("ssid", "CorpNet")
    assert watch.match(_ent("AA:BB:CC:00:00:01", "access_point", "CorpNet", ssid="CorpNet"))
    # even if the label differs, the ssid attr still matches
    assert watch.match(_ent("AA:BB:CC:00:00:02", "access_point", "-", ssid="CorpNet"))


def test_name_matches_bt_device_name(watch):
    watch.add("name", "Grayson's iPhone")
    assert watch.match(_ent("AA:BB:CC:11:22:33", "bt_device", "Grayson's iPhone",
                            name="Grayson's iPhone"))


def test_radio_id_matches_dmr_and_tetra(watch):
    watch.add("radio_id", "2095551")
    assert watch.match(_ent("2095551", "dmr_radio", "2095551"))
    assert watch.match(_ent("2095551", "tetra_ssi", "2095551"))


def test_cid_matches_gsm_cell(watch):
    watch.add("cid", "11750")
    cell = _ent("260-3-3150-11750", "gsm_cell", "Cell 11750",
                cid="11750", arfcn="5", mcc="260", mnc="3", lac="3150", pwr="-28")
    assert watch.match(cell) == ["11750"]


def test_uas_id_matches_drone(watch):
    watch.add("uas_id", "FAA-DRONE-42")
    assert watch.match(_ent("FAA-DRONE-42", "uas", "FAA-DRONE-42"))


# ---- the anti-false-positive guarantee ----------------------------------

def test_no_false_match_on_measurement_attrs(watch):
    """A numeric target must NOT collide with ARFCN/power/etc."""
    watch.add("other", "5")            # someone watches value "5"
    cell = _ent("260-3-3150-11750", "gsm_cell", "Cell 11750",
                cid="11750", arfcn="5", mcc="260", mnc="3", lac="3150", pwr="-28")
    assert watch.match(cell) == []     # arfcn=5 is NOT an identifier attr


def test_empty_watchlist_matches_nothing(watch):
    assert watch.match(_ent("AA:BB:CC:11:22:33", "bt_device")) == []


# ---- findings injection --------------------------------------------------

def test_apply_to_survey_injects_leading_critical_finding(watch):
    watch.add("mac", "AA:BB:CC:11:22:33")
    s = Survey(path="x")
    s.entities["AA:BB:CC:11:22:33"] = _ent("AA:BB:CC:11:22:33", "bt_device", "iPhone")
    s.entities["OTHER"] = _ent("00:00:00:00:00:00", "bt_device", "speaker")
    n = watch.apply_to_survey(s)
    assert n == 1
    assert s.findings[0].kind == "target" and s.findings[0].severity == "critical"
    assert s.findings[0].evidence["entity"] == "AA:BB:CC:11:22:33"


def test_apply_to_survey_noop_without_targets(watch):
    s = Survey(path="x")
    s.entities["a"] = _ent("AA:BB:CC:11:22:33", "bt_device")
    assert watch.apply_to_survey(s) == 0
    assert s.findings == []


def test_end_to_end_through_engine(watch):
    """A watchlist UAS ID surfaces as the leading target finding when the Remote ID sample
    is surveyed through the normal engine path (the same path the CLI and TUI use)."""
    from waveraider.engine import survey_source
    from waveraider.protocols import get_plugin
    from waveraider.sources import FileSource

    watch.add("uas_id", "DRONE-SN-001A")
    s = survey_source(get_plugin("uas"), FileSource("data/uas_sample.pcap"))
    tf = [f for f in s.findings if f.kind == "target"]
    assert len(tf) == 1 and tf[0].evidence["entity"] == "wifi_beacon:60:60:1f:00:00:01"
    assert s.findings[0].kind == "target"       # leads the list
