"""Remote ID decoding against independent Open Drone ID reference data, plus edge
cases built from the ASTM F3411 message layout.

Reference inputs are captures published by opendroneid/wireshark-dissector; their
expected values come from the opendroneid-core-c reference decoder, not from
WaveRaider (see fixtures/opendroneid/PROVENANCE.md). Built packets are round-tripped
through their wire bytes, so Scapy dissection is exercised as it is for captures."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from scapy.all import PcapReader  # type: ignore
from scapy.layers.bluetooth import EIR_Hdr  # type: ignore
from scapy.layers.bluetooth4LE import BTLE, BTLE_ADV, BTLE_ADV_NONCONN_IND  # type: ignore
from scapy.layers.dot11 import Dot11, Dot11Beacon, Dot11Elt, RadioTap  # type: ignore
from scapy.packet import Raw  # type: ignore

from waveraider import targets
from waveraider.uas_classify import UasClassifier, _wifi_odid, is_stale, parse_message, split_payload, survey

FIX = Path(__file__).parent / "fixtures" / "opendroneid"
CAPTURES = ("odid_wifi_bcn_sample", "odid_wifi_sample")
REF_MAC = "84:cc:a8:60:43:24"
REF_KEY = f"wifi_beacon:{REF_MAC}"
REF_TYPES = {"basic_id_0": "basic_id", "location": "location", "self_id": "self_id",
             "system": "system", "operator_id": "operator_id"}
DECODED_REF_KEYS = ("basic_id_0", "location", "system", "operator_id")


def _expected(name: str) -> dict:
    lines = (FIX / f"{name}.expected.jsonl").read_text().splitlines()
    return {r["frame"]: r for r in map(json.loads, lines)}


# ---- independent reference captures -------------------------------------

@pytest.mark.parametrize("name", CAPTURES)
def test_each_beacon_matches_reference_decoder(name):
    expected = _expected(name)
    seen = set()
    with PcapReader(str(FIX / f"{name}.pcap")) as rd:
        for i, pkt in enumerate(rd):
            payload, problem = _wifi_odid(pkt) if pkt.haslayer(Dot11) else (None, None)
            assert problem is None
            if payload is None:
                assert i not in expected
                continue
            seen.add(i)
            ref = expected[i]
            messages, problem = split_payload(payload)
            assert problem is None
            infos = [parse_message(m) for m in messages]
            assert all(infos)
            got = {info["type"]: info for info in infos}
            assert set(got) == {REF_TYPES[k] for k in ref if k in REF_TYPES}

            if "basic_id_0" in ref:
                b, rb = got["basic_id"], ref["basic_id_0"]
                assert (b["id_type"], b["ua_type"]) == (rb["id_type"], rb["ua_type"])
                assert bytes.fromhex(b["uas_id_raw"]).rstrip(b"\x00").decode() == rb["uas_id"]
            if "location" in ref:
                loc, rl = got["location"], ref["location"]
                assert loc["position"] == pytest.approx((rl["lat"], rl["lon"]), abs=1e-7)
                assert loc["alt"] == (None if rl["alt_geo"] == -1000 else rl["alt_geo"])
                assert loc["claimed_time_s"] == (None if rl["timestamp"] == 65535 else rl["timestamp"])
                assert loc["invalid"] == []
            if "system" in ref:
                sysm, rs = got["system"], ref["system"]
                assert sysm["op_location_type"] == rs["op_location_type"]
                assert sysm["op_position"] == pytest.approx((rs["op_lat"], rs["op_lon"]), abs=1e-7)
            if "operator_id" in ref:
                assert got["operator_id"]["operator_id"] == ref["operator_id"]["id"]
    assert seen == set(expected)


def test_reference_capture_is_one_transmitter_without_a_valid_identity():
    s = survey(FIX / "odid_wifi_bcn_sample.pcap")
    assert list(s.entities) == [REF_KEY] and not s.diagnostics
    e = s.entities[REF_KEY]
    expected = _expected("odid_wifi_bcn_sample")
    last = expected[max(expected)]["location"]

    assert "uas_id" not in e.attrs and not e.label          # Basic ID type 0 = None
    assert bytes.fromhex(e.attrs["basic_ids"][0]["raw"]).rstrip(b"\x00") == b"MFG1A0123456789"
    assert (e.attrs["lat"], e.attrs["lon"]) == pytest.approx((last["lat"], last["lon"]), abs=1e-7)
    assert e.attrs["alt"] == 237.0
    assert e.attrs["op_location_type"] == 0
    assert e.attrs["operator_id"] == "GBR-OP-123ABCD"
    assert (e.attrs["addr"], e.attrs["transport"], e.observations) == (REF_MAC, "wifi_beacon", 21)
    assert e.attrs["first_seen"] <= e.attrs["last_seen"]

    summaries = [f.summary for f in s.findings]
    assert any("identity not reported" in t for t in summaries)
    assert any("(takeoff)" in t for t in summaries)
    assert not targets.match(e, watch=[{"type": "uas_id", "value": "MFG1A0123456789"}])


def test_single_message_capture_counts_only_decoded_messages_and_skips_nan():
    expected = _expected("odid_wifi_sample")
    decoded = [r for r in expected.values() if any(k in r for k in DECODED_REF_KEYS)]
    self_id_only = len(expected) - len(decoded)
    assert self_id_only > 0                                   # the capture rotates a Self-ID message

    s = survey(FIX / "odid_wifi_sample.pcap")
    assert s.total_frames == 63 and list(s.entities) == [REF_KEY]   # NAN action frames not decoded
    assert s.entities[REF_KEY].observations == len(decoded)
    assert s.diagnostics == {"authentication/self-ID not decoded": self_id_only}


# ---- edge cases built from the message layout -----------------------------

MAC = "60:60:1f:00:00:09"
WIFI, BLE = f"wifi_beacon:{MAC}", f"ble:{MAC}"
LAT, LON = 368529000, -759780000                              # 36.8529, -75.9780


def _msg(mtype: int, body: bytes = b"", version: int = 2) -> bytes:
    m = bytearray(25)
    m[0] = (mtype << 4) | version
    m[1:1 + len(body)] = body
    return bytes(m)


def basic_id(text=b"SN-0001", id_type=1, ua_type=2, version=2):
    return _msg(0, bytes([(id_type << 4) | ua_type]) + text.ljust(20, b"\x00")[:20], version)


def location(lat_raw, lon_raw, alt_raw=2474, ts=0xFFFF, version=2):
    body = bytearray(24)                                      # message bytes 1..24
    body[4:12] = struct.pack("<ii", lat_raw, lon_raw)
    body[14:16] = struct.pack("<H", alt_raw)                  # 2474 -> 237.0 m
    body[20:22] = struct.pack("<H", ts)
    return _msg(1, bytes(body), version)


def system(lat_raw, lon_raw, location_type=1):
    return _msg(4, bytes([location_type]) + struct.pack("<ii", lat_raw, lon_raw))


def operator_id(text=b"OP-1"):
    return _msg(5, b"\x00" + text.ljust(20, b"\x00")[:20])


def self_id(text=b"Recreational"):
    return _msg(3, b"\x00" + text.ljust(23, b"\x00"))


def pack(*messages, version=2, size=25, count=None):
    n = len(messages) if count is None else count
    return bytes([0xF0 | version, size, n]) + b"".join(messages)


def beacon_ie(body, length=None, mac=MAC, t=1000.0):
    p = (RadioTap() / Dot11(type=0, subtype=8, addr1="ff:ff:ff:ff:ff:ff", addr2=mac, addr3=mac)
         / Dot11Beacon() / Dot11Elt(ID="SSID", info="") / Dot11Elt(ID=221, len=length, info=body))
    p.time = t
    return p


def beacon(payload, counter=7, mac=MAC, t=1000.0):
    return beacon_ie(b"\xfa\x0b\xbc\x0d" + bytes([counter]) + payload, mac=mac, t=t)


def ble_ad(value, length=None, mac=MAC, t=1000.0):
    ad = EIR_Hdr(len=length, type=0x16) / Raw(load=value)
    p = BTLE() / BTLE_ADV() / BTLE_ADV_NONCONN_IND(AdvA=mac, data=[ad])
    p.time = t
    return p


def ble(payload, counter=7, mac=MAC, t=1000.0):
    return ble_ad(b"\xfa\xff\x0d" + bytes([counter]) + payload, mac=mac, t=t)


def run(*packets):
    clf = UasClassifier()
    for p in packets:
        wire = p.__class__(bytes(p))                          # dissect like a capture
        wire.time = p.time
        clf.update(wire)
    clf.rebuild_findings()
    return clf.survey


@pytest.mark.parametrize("counter", [0x00, 0x21, 0x7F, 0xF0, 0xF3, 0xFF])
def test_message_counter_never_shifts_the_payload(counter):
    s = run(beacon(pack(basic_id(), location(LAT, LON)), counter=counter))
    a = s.entities[WIFI].attrs
    assert a["uas_id"] == "SN-0001"
    assert (a["lat"], a["lon"]) == pytest.approx((36.8529, -75.978))
    assert not s.diagnostics


def test_counterless_legacy_framing_is_not_a_detection():
    s = run(beacon_ie(b"\xfa\x0b\xbc\x0d" + pack(basic_id(), location(LAT, LON))))
    assert not s.entities and not s.findings
    assert sum(s.diagnostics.values()) == 1


def test_bluetooth_legacy_single_messages():
    s = run(ble(basic_id(b"BLE-1")), ble(location(LAT, LON), counter=8))
    e = s.entities[BLE]
    assert e.attrs["uas_id"] == "BLE-1" and e.attrs["transport"] == "ble" and "lat" in e.attrs
    assert e.observations == 2


@pytest.mark.parametrize("version", [0, 1, 2])
def test_supported_protocol_versions_decode(version):
    s = run(ble(basic_id(version=version)))
    assert s.entities[BLE].attrs["protocol_version"] == version


@pytest.mark.parametrize("version", [3, 15])
def test_unknown_protocol_versions_are_diagnostics_not_detections(version):
    s = run(ble(basic_id(version=version)),
            beacon(pack(basic_id(), version=version), mac="60:60:1f:00:00:0a"))
    assert not s.entities
    assert sum(s.diagnostics.values()) == 2


@pytest.mark.parametrize("payload, problem", [
    (b"", "empty payload"),
    (basic_id()[:24], "truncated message"),
    (pack(basic_id(), count=0), "malformed message pack"),
    (pack(basic_id(), basic_id(), location(LAT, LON), count=10), "malformed message pack"),
    (pack(basic_id(), size=24), "malformed message pack"),
    (pack(basic_id(), location(LAT, LON))[:40], "truncated message pack"),
    (pack(location(LAT, LON), location(LAT, LON)), "invalid message pack content"),
    (pack(basic_id(), pack(basic_id())[:25]), "invalid message pack content"),
    (pack(_msg(6)), "invalid message pack content"),
])
def test_malformed_payloads_are_rejected(payload, problem):
    assert split_payload(payload) == ([], problem)
    s = run(beacon(payload))
    assert not s.entities and s.diagnostics == {problem: 1}


# V1: recognized but undecoded message types never create or refresh a report.

@pytest.mark.parametrize("payload", [
    bytes([0x22, 0x00, 0xFF]) + bytes(22),                    # Authentication, page 0, LastPageIndex 255
    self_id(),
])
def test_undecoded_message_types_alone_are_not_reports(payload):
    s = run(ble(payload))
    assert not s.entities and not s.findings
    assert s.diagnostics == {"authentication/self-ID not decoded": 1}


def test_mixed_pack_keeps_its_decoded_messages():
    s = run(beacon(pack(basic_id(), self_id(), location(LAT, LON))))
    assert s.entities[WIFI].attrs["uas_id"] == "SN-0001" and not s.diagnostics


def test_undecoded_message_does_not_refresh_a_report():
    s = run(ble(basic_id(), t=1.0), ble(self_id(), t=5.0))
    e = s.entities[BLE]
    assert (e.observations, e.attrs["last_seen"]) == (1, 1.0)


# V2: the outer Wi-Fi element / Bluetooth AD must be complete.

@pytest.mark.parametrize("packet", [
    beacon_ie(b"\xfa\x0b\xbc\x0d\x07" + pack(basic_id()), length=100),   # declares 100, carries 33
    beacon_ie(b"\xfa\x0b\xbc\x0d"),                                      # no message counter
    beacon_ie(b"\xfa\x0b\xbc"),                                          # no app code
    ble_ad(b"\xfa\xff\x0d\x07" + basic_id(), length=60),                 # declares 60, carries 30
    ble_ad(b"\xfa\xff\x0d"),                                             # no message counter
])
def test_truncated_envelopes_are_diagnostics(packet):
    s = run(packet)
    assert not s.entities
    assert s.diagnostics == {"truncated Remote ID envelope": 1}


def test_truncated_envelope_does_not_refresh_a_report():
    s = run(beacon(pack(basic_id()), t=1.0),
            beacon_ie(b"\xfa\x0b\xbc\x0d\x07" + pack(basic_id()), length=100, t=5.0))
    e = s.entities[WIFI]
    assert (e.observations, e.attrs["last_seen"]) == (1, 1.0)


def test_other_vendor_elements_are_ignored():
    s = run(beacon_ie(b"\xfa\x0b\xbc\x0e" + bytes(30)), beacon_ie(b"\x00\x50\xf2\x04" + bytes(10)))
    assert not s.entities and not s.diagnostics


# V3: identifiers are used exactly as sent, or not at all.

def test_identifier_whitespace_is_preserved_not_normalized():
    s = run(ble(basic_id(b" SN-0001 ")), ble(operator_id(b"OP-1 "), mac="60:60:1f:00:00:0b"))
    drone, operator = s.entities[BLE], s.entities["ble:60:60:1f:00:00:0b"]
    assert drone.attrs["uas_id"] == " SN-0001 " and drone.label == " SN-0001 "
    assert operator.attrs["operator_id"] == "OP-1 "
    assert not targets.match(drone, watch=[{"type": "uas_id", "value": "SN-0001"}])
    assert not targets.match(operator, watch=[{"type": "operator_id", "value": "OP-1"}])


@pytest.mark.parametrize("raw", [b"SN\x070001", b"SN-0001\x00junk", b"\xffSN-0001", b"\x00SN-0001"])
def test_binary_or_badly_padded_identifiers_are_unusable(raw):
    s = run(ble(basic_id(raw)), ble(operator_id(raw), mac="60:60:1f:00:00:0b"))
    assert "uas_id" not in s.entities[BLE].attrs and not s.entities[BLE].label
    assert s.entities[BLE].attrs["basic_ids"][1]["raw"] == raw.ljust(20, b"\x00").hex()
    assert "operator_id" not in s.entities["ble:60:60:1f:00:00:0b"].attrs


def test_untyped_or_binary_id_types_are_not_used_as_uas_id():
    s = run(ble(basic_id(b"MFG1A0123456789", id_type=0)),
            ble(basic_id(b"0123456789abcdef", id_type=3), mac="60:60:1f:00:00:0c"))
    watch = [{"type": "uas_id", "value": "MFG1A0123456789"}]
    for e in s.entities.values():
        assert "uas_id" not in e.attrs and not e.label
        assert not targets.match(e, watch=watch)


def test_serial_preferred_and_newer_basic_id_of_same_type_replaces_it():
    s = run(ble(basic_id(b"CAA-REG-9", id_type=2)), ble(basic_id(b"SN-0001", id_type=1)))
    assert s.entities[BLE].attrs["uas_id"] == "SN-0001"
    s = run(ble(basic_id(b"SN-0001")), ble(basic_id(b"\xff\xfe", id_type=1)))
    assert "uas_id" not in s.entities[BLE].attrs


# V4: the same address on different transports stays separate evidence.

def test_same_address_on_wifi_and_bluetooth_stays_separate():
    s = run(beacon(pack(basic_id(b"WIFI-ID")), t=1.0), ble(basic_id(b"BLE-ID"), t=2.0))
    wifi, bt = s.entities[WIFI], s.entities[BLE]
    assert (wifi.attrs["uas_id"], wifi.attrs["transport"], wifi.attrs["first_seen"]) == ("WIFI-ID", "wifi_beacon", 1.0)
    assert (bt.attrs["uas_id"], bt.attrs["transport"], bt.attrs["first_seen"]) == ("BLE-ID", "ble", 2.0)
    assert wifi.attrs["addr"] == bt.attrs["addr"] == MAC
    watch = [{"type": "mac", "value": MAC.upper()}]
    assert targets.match(wifi, watch=watch) and targets.match(bt, watch=watch)


# Unknown vs invalid field values.

def test_unknown_values_are_not_reported_and_not_diagnosed():
    s = run(beacon(pack(location(0, 0, alt_raw=0, ts=0xFFFF), system(0, 0))))
    a = s.entities[WIFI].attrs
    assert not {"lat", "lon", "alt", "claimed_time_s", "op_lat", "op_lon"} & set(a)
    assert [f.kind for f in s.findings] == ["uas_remote_id"]
    assert not s.diagnostics


def test_zero_latitude_alone_is_a_real_position():
    a = run(beacon(pack(location(0, LON)))).entities[WIFI].attrs
    assert (a["lat"], a["lon"]) == pytest.approx((0.0, -75.978))


def test_out_of_range_coordinates_are_dropped_with_a_diagnostic():
    s = run(beacon(pack(location(910000000, LON), system(LAT, 1810000000))))
    a = s.entities[WIFI].attrs
    assert not {"lat", "lon", "op_lat", "op_lon"} & set(a) and a["alt"] == 237.0
    assert s.diagnostics == {"invalid drone position": 1, "invalid operator position": 1}


@pytest.mark.parametrize("raw, claimed, diagnostics", [
    (0, 0.0, {}), (36000, 3600.0, {}),
    (36001, None, {"invalid location timestamp": 1}), (0xFFFF, None, {}),
])
def test_claimed_time_limits(raw, claimed, diagnostics):
    s = run(beacon(pack(location(LAT, LON, ts=raw)), t=5000.0))
    a = s.entities[WIFI].attrs
    assert a.get("claimed_time_s") == claimed and s.diagnostics == diagnostics
    assert a["first_seen"] == a["last_seen"] == 5000.0          # received time kept apart


def test_newer_unknown_position_replaces_older_valid_one():
    s = run(beacon(pack(location(LAT, LON)), t=1.0), beacon(pack(location(0, 0, alt_raw=0)), t=2.0))
    a = s.entities[WIFI].attrs
    assert "lat" not in a and "alt" not in a
    assert (a["first_seen"], a["last_seen"]) == (1.0, 2.0)


@pytest.mark.parametrize("location_type, label", [(0, "takeoff"), (1, "live GNSS"), (2, "fixed")])
def test_operator_location_is_labelled_with_its_declared_type(location_type, label):
    s = run(beacon(pack(system(LAT, LON, location_type))))
    ops = [f for f in s.findings if f.kind == "operator_location"]
    assert len(ops) == 1 and f"({label})" in ops[0].summary


def test_partial_report_without_identity_is_still_a_labelled_report():
    s = run(beacon(pack(operator_id())))
    assert s.entities[WIFI].attrs["operator_id"] == "OP-1"
    assert "identity not reported" in s.findings[0].summary


def test_staleness_uses_elapsed_time_since_last_reception():
    e = run(beacon(pack(basic_id()), t=100.0)).entities[WIFI]
    assert not is_stale(e, now=120.0)
    assert is_stale(e, now=131.0)
