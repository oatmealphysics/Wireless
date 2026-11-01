#!/usr/bin/env python3
"""
Tests for wifi-pcap-decoder.py

Tests the pure-function helpers (OUI lookup, SSID classification,
frame type naming, ESP-NOW detection constants) without fabricating
synthetic pcap captures. Full integration testing requires a real
pcap — run manually with:

    python3 tools/wifi-pcap-decoder.py <real-capture.pcap> --json

See standing directive: all tests run against real data or validate
logic that doesn't depend on capture content.
"""

import importlib.util
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent

# scapy is an optional dependency (used only for pcap I/O in the decoder).
# Skip the whole module cleanly when it is absent so unittest discovery does
# not crash on the import — matching the project's optional-dep skip pattern.
try:
    import scapy  # noqa: F401
    _HAS_SCAPY = True
except ImportError:
    _HAS_SCAPY = False

spec = importlib.util.spec_from_file_location("wifi_pcap_decoder", TOOLS_DIR / "wifi-pcap-decoder.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["wifi_pcap_decoder"] = mod
if _HAS_SCAPY:
    spec.loader.exec_module(mod)


class TestOUILookup:
    def test_espressif_ouis_recognized(self):
        espressif_prefixes = [
            "18:fe:34:aa:bb:cc",
            "24:0a:c4:11:22:33",
            "30:ae:a4:de:ad:be",
            "a4:cf:12:00:00:01",
            "cc:50:e3:ff:ff:ff",
        ]
        for mac in espressif_prefixes:
            assert mod.lookup_oui(mac) == "Espressif Inc.", f"Failed for {mac}"
            assert mod.is_espressif_mac(mac), f"is_espressif_mac failed for {mac}"

    def test_non_espressif_returns_unknown(self):
        assert mod.lookup_oui("00:11:22:33:44:55") == "Unknown"
        assert not mod.is_espressif_mac("00:11:22:33:44:55")

    def test_dji_oui(self):
        assert mod.lookup_oui("60:60:1f:00:00:00") == "DJI Technology"

    def test_parrot_oui(self):
        assert mod.lookup_oui("90:03:b7:ab:cd:ef") == "Parrot SA"

    def test_mac_oui_extracts_first_three_octets(self):
        assert mod.mac_oui("AA:BB:CC:DD:EE:FF") == "aa:bb:cc"
        assert mod.mac_oui("18:FE:34:12:34:56") == "18:fe:34"


class TestSSIDClassification:
    def test_elrs_tx_ssid(self):
        assert mod.classify_ssid("ExpressLRS TX") == "ELRS WiFi Backpack"

    def test_elrs_rx_ssid(self):
        assert mod.classify_ssid("ExpressLRS RX") == "ELRS WiFi Backpack"

    def test_elrs_underscore_ssid(self):
        assert mod.classify_ssid("ELRS_backpack") == "ELRS WiFi Backpack"

    def test_tello_ssid(self):
        assert mod.classify_ssid("TELLO-A1B2C3") == "Drone WiFi"

    def test_dji_ssid(self):
        assert mod.classify_ssid("DJI-Mini3") == "Drone WiFi"

    def test_ardupilot_ssid(self):
        assert mod.classify_ssid("ArduPilot-NetworkManager") == "Drone WiFi"

    def test_normal_ssid_not_classified(self):
        assert mod.classify_ssid("MyHomeNetwork") is None
        assert mod.classify_ssid("Starbucks WiFi") is None

    def test_empty_ssid(self):
        assert mod.classify_ssid("") is None

    def test_fpv_ssid(self):
        assert mod.classify_ssid("FPV-Goggles") == "Drone WiFi"


class TestFrameTypeNames:
    def test_beacon(self):
        assert mod.get_frame_type_name(0, 8) == "Management/Beacon"

    def test_probe_request(self):
        assert mod.get_frame_type_name(0, 4) == "Management/Probe Request"

    def test_probe_response(self):
        assert mod.get_frame_type_name(0, 5) == "Management/Probe Response"

    def test_deauth(self):
        assert mod.get_frame_type_name(0, 12) == "Management/Deauthentication"

    def test_action(self):
        assert mod.get_frame_type_name(0, 13) == "Management/Action"

    def test_ack(self):
        assert mod.get_frame_type_name(1, 13) == "Control/ACK"

    def test_data(self):
        assert mod.get_frame_type_name(2, 0) == "Data/Data"

    def test_qos_data(self):
        assert mod.get_frame_type_name(2, 8) == "Data/QoS Data"

    def test_unknown_subtype(self):
        name = mod.get_frame_type_name(0, 15)
        assert "Subtype15" in name

    def test_unknown_type(self):
        name = mod.get_frame_type_name(3, 0)
        assert "Type3" in name


class TestESPNowConstants:
    def test_espnow_element_id(self):
        assert mod.ESPNOW_ELEMENT_ID == 221

    def test_espressif_oui_bytes(self):
        assert b"\x18\xfe\x34" in mod.ESPRESSIF_OUI_BYTES
        assert b"\x24\x0a\xc4" in mod.ESPRESSIF_OUI_BYTES

    def test_filter_map_keys(self):
        expected = {"esp-now", "beacon", "probe", "esp", "drone", "mgmt", "data", "deauth", "action"}
        assert set(mod.FILTER_MAP.keys()) == expected


class TestDecodedFrameDataclass:
    def test_default_values(self):
        f = mod.DecodedFrame(
            timestamp=1000.0,
            frame_type=0,
            frame_subtype=8,
            type_name="Management/Beacon",
        )
        assert f.src is None
        assert f.is_espnow is False
        assert f.raw_len == 0

    def test_frame_to_dict(self):
        f = mod.DecodedFrame(
            timestamp=1000.5,
            frame_type=0,
            frame_subtype=13,
            type_name="Management/Action",
            src="18:fe:34:aa:bb:cc",
            dst="ff:ff:ff:ff:ff:ff",
            is_espnow=True,
            espnow_payload_hex="deadbeef",
            is_esp_mac=True,
            raw_len=128,
        )
        d = mod.frame_to_dict(f)
        assert d["type"] == "Management/Action"
        assert d["esp_now"] is True
        assert d["espnow_payload"] == "deadbeef"
        assert d["esp_mac"] is True
        assert d["raw_len"] == 128


class TestBeaconInfo:
    def test_defaults(self):
        b = mod.BeaconInfo(ssid="test", bssid="aa:bb:cc:dd:ee:ff")
        assert b.count == 0
        assert b.crypto == []
        assert b.signal_dbm == []
