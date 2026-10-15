"""
Tests for the ELRS serial probe's parsing logic: CRC8, frame building,
frame parsing, and device info extraction — no serial hardware needed.
"""

import importlib
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
serial_probe = importlib.import_module("elrs-serial-probe")


class TestCRSFCRC8:
    def test_empty_data(self):
        assert serial_probe.crsf_crc8(b"") == 0

    def test_single_byte(self):
        crc = serial_probe.crsf_crc8(bytes([0x28]))
        assert 0 <= crc <= 255

    def test_deterministic(self):
        data = bytes([0x28, 0x00, 0xEC])
        assert serial_probe.crsf_crc8(data) == serial_probe.crsf_crc8(data)

    def test_different_data_different_crc(self):
        assert serial_probe.crsf_crc8(b"\x01") != serial_probe.crsf_crc8(b"\x02")

    def test_polynomial_0xd5(self):
        # CRSF uses CRC-8/DVB-S2 with polynomial 0xD5
        # Known test vector: CRC8/DVB-S2 of "123456789" = 0xBC
        data = b"123456789"
        assert serial_probe.crsf_crc8(data) == 0xBC

    def test_all_zeros(self):
        assert serial_probe.crsf_crc8(bytes(8)) == serial_probe.crsf_crc8(bytes(8))

    def test_byte_range(self):
        for i in range(256):
            crc = serial_probe.crsf_crc8(bytes([i]))
            assert 0 <= crc <= 255


class TestBuildCRSFFrame:
    def test_frame_structure(self):
        frame = serial_probe.build_crsf_frame(0x28, bytes([0x00, 0xEC]))
        assert frame[0] == serial_probe.CRSF_SYNC_RX  # default sync
        assert frame[2] == 0x28  # frame type
        # length = payload(2) + type(1) + crc(1) = 4
        assert frame[1] == 4

    def test_custom_sync(self):
        frame = serial_probe.build_crsf_frame(0x28, b"\x00", sync=serial_probe.CRSF_SYNC_TX)
        assert frame[0] == serial_probe.CRSF_SYNC_TX

    def test_crc_validates(self):
        frame = serial_probe.build_crsf_frame(0x28, bytes([0x00, 0xEC]))
        # CRC covers type + payload
        crc_data = frame[2:-1]
        expected_crc = serial_probe.crsf_crc8(crc_data)
        assert frame[-1] == expected_crc

    def test_empty_payload(self):
        frame = serial_probe.build_crsf_frame(0x0A, b"")
        assert frame[1] == 2  # type + crc only
        assert len(frame) == 4  # sync + len + type + crc

    def test_roundtrip_with_parse(self):
        frame = serial_probe.build_crsf_frame(0x28, bytes([0x00, 0xEC]))
        parsed = serial_probe.parse_frame(frame)
        assert parsed is not None
        assert parsed["type"] == 0x28
        assert parsed["crc_ok"] is True
        assert parsed["payload"] == bytes([0x00, 0xEC])


class TestBuildPing:
    def test_ping_is_valid_frame(self):
        ping = serial_probe.build_ping()
        parsed = serial_probe.parse_frame(ping)
        assert parsed is not None
        assert parsed["crc_ok"] is True
        assert parsed["type"] == 0x28  # PING_DEVICES

    def test_ping_addresses(self):
        ping = serial_probe.build_ping()
        parsed = serial_probe.parse_frame(ping)
        assert parsed["payload"][0] == 0x00  # broadcast dest
        assert parsed["payload"][1] == 0xEA  # radio transmitter origin


class TestBuildDeviceInfoRequest:
    def test_valid_frame(self):
        req = serial_probe.build_device_info_request()
        parsed = serial_probe.parse_frame(req)
        assert parsed is not None
        assert parsed["crc_ok"] is True
        assert parsed["type"] == 0x28

    def test_addresses(self):
        req = serial_probe.build_device_info_request()
        parsed = serial_probe.parse_frame(req)
        assert parsed["payload"][0] == 0xEE  # CRSF transmitter
        assert parsed["payload"][1] == 0xEA  # radio transmitter


class TestBuildParamRead:
    def test_valid_frame(self):
        req = serial_probe.build_param_read(1)
        parsed = serial_probe.parse_frame(req)
        assert parsed is not None
        assert parsed["crc_ok"] is True
        assert parsed["type"] == 0x2C  # PARAMETER_READ

    def test_param_index_in_payload(self):
        for idx in [1, 5, 20]:
            req = serial_probe.build_param_read(idx)
            parsed = serial_probe.parse_frame(req)
            assert parsed["payload"][2] == idx

    def test_chunk_in_payload(self):
        req = serial_probe.build_param_read(3, chunk=2)
        parsed = serial_probe.parse_frame(req)
        assert parsed["payload"][3] == 2


class TestParseFrame:
    def test_valid_frame(self):
        frame = serial_probe.build_crsf_frame(0x14, bytes([0x01, 0x02, 0x03]))
        parsed = serial_probe.parse_frame(frame)
        assert parsed is not None
        assert parsed["sync"] == serial_probe.CRSF_SYNC_RX
        assert parsed["type"] == 0x14
        assert parsed["type_name"] == "LINK_STATISTICS"
        assert parsed["crc_ok"] is True

    def test_too_short(self):
        assert serial_probe.parse_frame(b"\xC8\x02") is None
        assert serial_probe.parse_frame(b"\xC8") is None
        assert serial_probe.parse_frame(b"") is None

    def test_truncated_data(self):
        # Frame says length=10 but data is shorter
        data = bytes([0xC8, 10, 0x28, 0x00])
        assert serial_probe.parse_frame(data) is None

    def test_bad_crc(self):
        frame = serial_probe.build_crsf_frame(0x28, bytes([0x00, 0xEC]))
        corrupted = bytearray(frame)
        corrupted[-1] ^= 0xFF  # flip CRC
        parsed = serial_probe.parse_frame(bytes(corrupted))
        assert parsed is not None
        assert parsed["crc_ok"] is False

    def test_unknown_type(self):
        frame = serial_probe.build_crsf_frame(0xFE, b"\x00")
        parsed = serial_probe.parse_frame(frame)
        assert parsed is not None
        assert "UNKNOWN" in parsed["type_name"]

    def test_known_types_in_map(self):
        for type_id, name in serial_probe.CRSF_TYPES.items():
            frame = serial_probe.build_crsf_frame(type_id, b"\x00\x00")
            parsed = serial_probe.parse_frame(frame)
            assert parsed["type_name"] == name

    def test_raw_field_contains_frame_bytes(self):
        frame = serial_probe.build_crsf_frame(0x0E, bytes([0x01, 0x02]))
        parsed = serial_probe.parse_frame(frame)
        assert parsed["raw"] == frame


class TestExtractDeviceInfo:
    def _build_device_info_payload(self, name="TestDevice", serial_no=b"\x00\x01\x02\x03",
                                     hw_ver=100, sw_ver=200, param_count=15, protocol_ver=1):
        dest = bytes([0xEC])
        origin = bytes([0xEE])
        name_bytes = name.encode("ascii") + b"\x00"
        rest = serial_no + struct.pack(">I", hw_ver) + struct.pack(">I", sw_ver)
        rest += bytes([param_count, protocol_ver])
        return dest + origin + name_bytes + rest

    def test_basic_extraction(self):
        payload = self._build_device_info_payload()
        info = serial_probe.extract_device_info(payload)
        assert info["name"] == "TestDevice"
        assert info["hw_ver"] == 100
        assert info["sw_ver"] == 200
        assert info["param_count"] == 15
        assert info["protocol_ver"] == 1

    def test_dest_and_origin(self):
        payload = self._build_device_info_payload()
        info = serial_probe.extract_device_info(payload)
        assert info["dest"] == "CRSF_RECEIVER"
        assert info["origin"] == "CRSF_TRANSMITTER"

    def test_serial_number(self):
        payload = self._build_device_info_payload(serial_no=b"\xDE\xAD\xBE\xEF")
        info = serial_probe.extract_device_info(payload)
        assert info["serial_no"] == "deadbeef"

    def test_empty_payload(self):
        info = serial_probe.extract_device_info(b"")
        assert info == {}

    def test_short_payload(self):
        # extract_device_info requires len >= 3 before populating any fields
        info = serial_probe.extract_device_info(b"\xEC\xEE")
        assert info == {}

    def test_three_byte_payload(self):
        # New implementation requires full device info (name + 14 bytes of rest)
        # A 3-byte payload returns empty dict
        info = serial_probe.extract_device_info(b"\xEC\xEE\x00")
        assert info == {}

    def test_no_null_terminator_for_name(self):
        # If no null terminator, name won't be found
        payload = bytes([0xEC, 0xEE]) + b"NoNull"
        info = serial_probe.extract_device_info(payload)
        assert "name" not in info

    def test_long_device_name(self):
        payload = self._build_device_info_payload(name="ExpressLRS TX Module v4.1.0")
        info = serial_probe.extract_device_info(payload)
        assert info["name"] == "ExpressLRS TX Module v4.1.0"

    def test_minimal_rest_data(self):
        # New implementation requires name_end + 15 bytes total
        # Payload with name but insufficient rest data returns empty dict
        payload = bytes([0xEC, 0xEE]) + b"Dev\x00" + bytes(8)
        info = serial_probe.extract_device_info(payload)
        assert info == {}


class TestCRSFConstants:
    def test_sync_bytes_defined(self):
        assert serial_probe.CRSF_SYNC_TX == 0xC8
        assert serial_probe.CRSF_SYNC_RX == 0xC8
        assert serial_probe.CRSF_SYNC_MODULE == 0xEE

    def test_common_frame_types_present(self):
        assert 0x28 in serial_probe.CRSF_TYPES  # PING_DEVICES
        assert 0x29 in serial_probe.CRSF_TYPES  # DEVICE_INFO
        assert 0x0E in serial_probe.CRSF_TYPES  # LINK_STATISTICS
        assert 0x16 in serial_probe.CRSF_TYPES  # RC_CHANNELS_PACKED

    def test_address_map_has_key_entries(self):
        assert 0xEC in serial_probe.CRSF_ADDR  # CRSF_RECEIVER
        assert 0xEE in serial_probe.CRSF_ADDR  # CRSF_TRANSMITTER
        assert 0x00 in serial_probe.CRSF_ADDR  # BROADCAST
