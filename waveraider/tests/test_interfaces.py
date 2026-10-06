"""Interface recognition — tested against synthetic USB output so it's verified
without any hardware plugged in."""
from waveraider.interfaces import KNOWN_DEVICES, parse_lsusb, parse_system_profiler

_LSUSB = """\
Bus 001 Device 003: ID 0bda:8812 Realtek Semiconductor Corp. RTL8812AU
Bus 001 Device 004: ID 0bda:2838 Realtek RTL2838 DVB-T
Bus 001 Device 006: ID 1d50:6089 Great Scott Gadgets HackRF One
Bus 001 Device 009: ID 1234:5678 Generic Hub
"""

_SP = """\
    AWUS036ACH:
      Product ID: 0x8812
      Vendor ID: 0x0bda  (Realtek Semiconductor Corp.)
      Speed: Up to 5 Gb/s
    RTL2838 DVB-T:
      Product ID: 0x2838
      Vendor ID: 0x0bda  (Realtek)
"""


def test_lsusb_recognizes_alfa_sdr_hackrf():
    ids = {f"{v}:{p}" for v, p, _ in parse_lsusb(_LSUSB)}
    assert {"0bda:8812", "0bda:2838", "1d50:6089"} <= ids
    assert KNOWN_DEVICES["0bda:8812"][0] == "wifi"      # Alfa
    assert KNOWN_DEVICES["0bda:2838"][0] == "sdr"       # RTL-SDR
    assert KNOWN_DEVICES["1d50:6089"][0] == "sdr"       # HackRF


def test_system_profiler_parses_vid_pid():
    ids = {f"{v}:{p}" for v, p, _ in parse_system_profiler(_SP)}
    assert "0bda:8812" in ids
    assert "0bda:2838" in ids


def test_known_table_is_well_formed():
    for key, (kind, model, note) in KNOWN_DEVICES.items():
        assert kind in ("sdr", "wifi", "bluetooth")
        assert model and ":" in key


def test_configured_interface_store_is_retired():
    from waveraider import interfaces as I
    for name in ("load_configured", "save_configured", "add_configured", "remove_configured", "_config_file", "CONFIG_FILE"):
        assert not hasattr(I, name)


def test_render_lists_capture_hardware_and_network_interfaces():
    from waveraider.interfaces import render
    text = render({"capture_devices": [{"kind": "sdr", "id": "2cf0:5250", "model": "bladeRF 2.0 micro", "note": "12-bit"}],
                   "other_usb_count": 2,
                   "network_interfaces": [{"name": "en0", "wireless": True}, {"name": "en1", "wireless": False}]})
    assert "CAPTURE HARDWARE (1)" in text and "bladeRF 2.0 micro  (2cf0:5250)" in text
    assert "NETWORK INTERFACES (2)" in text and "en0 (wireless)" in text and "  en1\n" in text


def test_logbuf():
    from waveraider import logbuf
    logbuf.clear()
    logbuf.log("info", "hello")
    assert logbuf.lines()[-1]["msg"] == "hello"
    assert logbuf.lines()[-1]["level"] == "INFO"


def test_unrecognized_usb_leaves_out_hubs_controllers_and_input_devices(monkeypatch):
    from waveraider import interfaces as I
    # What a Colima / Docker Desktop VM exposes, plus a real unknown device and a known SDR.
    monkeypatch.setattr(I, "usb_devices", lambda: [
        ("1d6b", "0002", "xHCI Host Controller"), ("1d6b", "0003", ""),
        ("05ac", "8105", "Virtual USB Keyboard"), ("05ac", "8106", "Virtual USB Digitizer"),
        ("05e3", "0610", "USB2.1 Hub"), ("046d", "c52b", "USB Receiver Mouse"),
        ("05ac", "0340", "Apple Internal Keyboard / Trackpad"),
        ("2eb8", "9999", "SignalHound (unknown PID)"), ("0403", "6015", ""),
        ("2cf0", "5250", "bladeRF 2.0 micro"),
    ])
    assert I.unrecognized_usb() == [{"id": "2eb8:9999", "name": "SignalHound (unknown PID)"},
                                    {"id": "0403:6015", "name": ""}]
