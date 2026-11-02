"""The protocol registry: Remote ID (default) + ELRS, with v0.5.1's retired names rejected clearly."""
import importlib
from pathlib import Path

import pytest

from waveraider.protocols import DEFAULT_PROTOCOL, RETIRED, RetiredProtocol, available, get_plugin

V051_RETIRED = {"wifi", "ble", "bt", "bt_traffic", "dmr", "tetra", "analog", "gsm", "umts", "cdma", "lte", "nr"}


def test_remote_id_is_the_default_and_elrs_is_the_other_supported_protocol():
    # ELRS (SDR-decoded control link) was added alongside Remote ID; Remote ID stays the default.
    assert available() == ["uas", "elrs"] and DEFAULT_PROTOCOL == "uas"
    assert get_plugin().name == "uas"
    for name in (None, "", "uas", "UAS"):
        assert get_plugin(name).name == "uas"
    assert get_plugin("elrs").name == "elrs" and get_plugin("ELRS").name == "elrs"


def test_plugin_contract():
    p = get_plugin("uas")
    clf = p.new_classifier()
    assert hasattr(clf, "update") and hasattr(clf, "rebuild_findings") and hasattr(clf, "survey")
    assert p.columns()[0] == "Transmitter"
    assert p.knowledge and all({"what", "means", "next"} <= set(v) for v in p.knowledge.values())


def test_every_v051_protocol_name_is_recognized_as_retired():
    assert set(RETIRED) == V051_RETIRED


@pytest.mark.parametrize("name", sorted(V051_RETIRED) + ["WIFI"])
def test_retired_names_are_rejected_naming_the_supported_protocols(name):
    with pytest.raises(RetiredProtocol, match=r"was retired; the supported protocols are uas, elrs") as e:
        get_plugin(name)
    assert isinstance(e.value, ValueError) and "v0.5.1" in str(e.value)


def test_unknown_names_get_their_own_error():
    with pytest.raises(ValueError, match="unknown protocol 'zigbee'") as e:
        get_plugin("zigbee")
    assert not isinstance(e.value, RetiredProtocol)


RETIRED_FILES = [
    "classify.py", "events.py", "ble_classify.py", "bt_classify.py", "dmr_classify.py", "tetra_classify.py",
    "gsm_classify.py", "analog_classify.py", "protocols/wifi.py", "protocols/ble.py", "protocols/bt.py",
    "protocols/dmr.py", "protocols/tetra.py", "protocols/gsm.py", "protocols/analog.py", "protocols/planned.py",
    "samples/wifi.pcap", "samples/ble.pcap", "samples/bt.txt", "samples/dmr.txt", "samples/gsm.txt",
    "samples/tetra.txt", "samples/analog.txt",
]


def test_retired_modules_and_samples_are_gone():
    # Checked on disk: an editable install elsewhere can still make an old module importable,
    # so the installed-wheel check is what proves the package no longer ships them.
    pkg = Path(importlib.import_module("waveraider.protocols").__file__).resolve().parents[1]
    assert (pkg / "uas_classify.py").exists() and (pkg / "bands.py").exists()
    assert [f for f in RETIRED_FILES if (pkg / f).exists()] == []
    assert sorted(p.name for p in (pkg / "samples").iterdir() if p.suffix in (".pcap", ".txt")) == ["uas.pcap"]
