"""UAS Remote ID (Open Drone ID) — ground-truth tests over Wi-Fi, plus a BLE
transport decode. Answer key in tools/make_uas_sample.py; independent reference
checks and edge cases are in test_remote_id_reference.py."""
from pathlib import Path

from waveraider.uas_classify import UasClassifier, survey


def test_ordinary_wifi_capture_is_not_remote_id():
    s = survey("tests/fixtures/wifi_beacons_no_remote_id.pcap")
    assert s.total_frames > 0
    assert not s.of_kind("uas")
    assert not s.findings
    assert not s.diagnostics


def test_bundled_sample_copies_match():
    assert Path("data/uas_sample.pcap").read_bytes() == Path("waveraider/samples/uas.pcap").read_bytes()


def test_wifi_sample_two_drones():
    s = survey("data/uas_sample.pcap")
    assert s.total_frames == 18
    drones = s.of_kind("uas")
    assert len(drones) == 2
    assert {d.attrs.get("uas_id") for d in drones} == {"DRONE-SN-001A", "SKYDIO-9988"}
    assert not s.diagnostics


def test_location_and_operator_decoded():
    s = survey("data/uas_sample.pcap")
    d = s.entities["wifi_beacon:60:60:1f:00:00:01"]
    assert d.attrs["addr"] == "60:60:1f:00:00:01"
    assert abs(d.attrs["lat"] - 36.8529) < 1e-4
    assert abs(d.attrs["lon"] - (-75.9780)) < 1e-4
    assert d.attrs["alt"] == 120.0
    assert abs(d.attrs["op_lat"] - 36.8600) < 1e-4
    assert d.attrs["op_location_type"] == 1
    assert d.attrs["operator_id"] == "OP-FAA-12345"
    assert d.attrs["transport"] == "wifi_beacon"


def test_findings():
    s = survey("data/uas_sample.pcap")
    assert len([f for f in s.findings if f.kind == "uas_remote_id"]) == 2
    ops = [f for f in s.findings if f.kind == "operator_location"]
    assert len(ops) == 1 and "(live GNSS)" in ops[0].summary


def test_ble_transport_decodes():
    from scapy.layers.bluetooth import EIR_Hdr
    from scapy.layers.bluetooth4LE import BTLE, BTLE_ADV, BTLE_ADV_IND
    from scapy.packet import Raw

    m = bytearray(25)
    m[0] = 0x02                       # Basic ID, protocol version 2
    m[1] = (1 << 4) | 2               # serial id, multirotor
    name = b"BLE-DRONE-1"
    m[2:2 + len(name)] = name
    # service data 0xFFFA, app code 0x0D, message counter, message
    ad = EIR_Hdr(type=0x16) / Raw(load=b"\xfa\xff\x0d\x07" + bytes(m))
    pkt = BTLE() / BTLE_ADV(TxAdd=1) / BTLE_ADV_IND(AdvA="c0:00:00:00:00:aa", data=[ad])

    clf = UasClassifier()
    clf.update(pkt)
    clf.rebuild_findings()
    drones = clf.survey.of_kind("uas")
    assert len(drones) == 1
    assert drones[0].attrs.get("uas_id") == "BLE-DRONE-1"
    assert drones[0].attrs["transport"] == "ble"
