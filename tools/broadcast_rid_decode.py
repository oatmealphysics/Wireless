#!/usr/bin/env python3
"""Broadcast Remote ID (RID) Beacon Decoder — SDR IQ to Drone Identity

Decodes FAA Broadcast Remote ID beacons transmitted over Bluetooth 5 Long
Range (BLE Extended Advertising) and WiFi NAN (Neighbor Awareness Networking)
as specified by ASTM F3411-22a and ASD-STAN prEN 4709-002.

Remote ID overview:
    Drones over 250g in the US (and similar rules in EU/UK) must broadcast
    identification and location data. Two transport mechanisms:
    1. Bluetooth 5 Legacy Advertising (ADV_NONCONN_IND, 37/38/39 channels)
    2. WiFi NAN (Neighbor Awareness Networking, channel 6 or 149)

    Both carry the same ASTM F3411 Open Drone ID message set:
    - Message Type 0: Basic ID (drone serial number, UA type)
    - Message Type 1: Location/Vector (lat, lon, alt, speed, heading)
    - Message Type 2: Authentication
    - Message Type 3: Self-ID (operator description)
    - Message Type 4: System (operator location, area count)
    - Message Type 5: Operator ID
    - Message Type 0xF: Message Pack (multiple messages in one frame)

    BLE transport uses the company-specific AD type (0xFF) with the Open
    Drone ID company ID (0xFFFA assigned by Bluetooth SIG for ASTM ODID).

This decoder handles the BLE advertising channel transport. WiFi NAN
decoding is left to the sibling WiFi decoder thread.

SDR requirements:
    - Center frequency: 2.402 GHz (channel 37), 2.426 GHz (ch 38), 2.480 GHz (ch 39)
    - Sample rate: >= 2 Msps (BLE uses 1 Mbps GFSK)
    - HackRF or any 2.4 GHz-capable SDR

NOTE: BLE physical-layer decoding overlaps with the BLE decoder being built
in a sibling thread. This module focuses on the Remote ID message parsing
layer. If a BLE PHY decoder becomes available, this module's parse_odid_*
functions can be called directly with the decoded advertising payload.

Capture command (HackRF, BLE advertising channel 37):
    hackrf_transfer -r rid_ble_ch37.raw -f 2402000000 -s 4000000 \\
        -g 40 -l 32 -a 1 -n 40000000

No real Broadcast RID IQ capture exists in this project. The capture
command above documents how to obtain one. The ODID message parsers are
validated against the ASTM F3411-22a specification byte layouts.

For authorized security research and CTF purposes only.
Requires: numpy
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, asdict
from enum import IntEnum
from pathlib import Path

import numpy as np


# --- ASTM F3411-22a Open Drone ID message types ---

class ODIDMessageType(IntEnum):
    BASIC_ID = 0x0
    LOCATION = 0x1
    AUTH = 0x2
    SELF_ID = 0x3
    SYSTEM = 0x4
    OPERATOR_ID = 0x5
    MESSAGE_PACK = 0xF


class UAType(IntEnum):
    NONE = 0
    AEROPLANE = 1
    HELICOPTER_OR_MULTIROTOR = 2
    GYROPLANE = 3
    HYBRID_LIFT = 4
    ORNITHOPTER = 5
    GLIDER = 6
    KITE = 7
    FREE_BALLOON = 8
    CAPTIVE_BALLOON = 9
    AIRSHIP = 10
    PARACHUTE = 11
    ROCKET = 12
    TETHERED = 13
    GROUND_OBSTACLE = 14
    OTHER = 15


class IDType(IntEnum):
    NONE = 0
    SERIAL_NUMBER = 1  # ANSI/CTA-2063-A format
    CAA_REGISTRATION = 2
    UTM_ASSIGNED = 3
    SPECIFIC_SESSION = 4


class SpeedMultiplier(IntEnum):
    X025 = 0  # 0.25 m/s resolution
    X075 = 1  # 0.75 m/s resolution


ODID_COMPANY_ID = 0xFFFA  # Bluetooth SIG assigned for ASTM ODID
ODID_MSG_SIZE = 25  # Each ODID message is 25 bytes


@dataclass
class BasicIDMessage:
    msg_type: int = 0
    id_type: int = 0
    ua_type: int = 0
    uas_id: str = ""

    def describe(self) -> str:
        id_name = IDType(self.id_type).name if self.id_type < 5 else f"UNKNOWN({self.id_type})"
        ua_name = UAType(self.ua_type).name if self.ua_type < 16 else f"UNKNOWN({self.ua_type})"
        return f"BasicID: {id_name} '{self.uas_id}' UA={ua_name}"


@dataclass
class LocationMessage:
    msg_type: int = 1
    status: int = 0
    direction: float = 0.0  # degrees
    speed_horizontal: float = 0.0  # m/s
    speed_vertical: float = 0.0  # m/s
    latitude: float = 0.0  # degrees
    longitude: float = 0.0  # degrees
    altitude_baro: float = 0.0  # meters
    altitude_geo: float = 0.0  # meters
    height_above_takeoff: float = 0.0  # meters
    horizontal_accuracy: int = 0
    vertical_accuracy: int = 0
    speed_accuracy: int = 0
    timestamp: float = 0.0  # seconds since hour

    def describe(self) -> str:
        return (
            f"Location: ({self.latitude:.6f}, {self.longitude:.6f}) "
            f"alt={self.altitude_geo:.1f}m speed={self.speed_horizontal:.1f}m/s "
            f"heading={self.direction:.0f}°"
        )


@dataclass
class SystemMessage:
    msg_type: int = 4
    operator_location_type: int = 0
    classification_type: int = 0
    operator_latitude: float = 0.0
    operator_longitude: float = 0.0
    area_count: int = 0
    area_radius: float = 0.0
    area_ceiling: float = 0.0
    area_floor: float = 0.0
    ua_classification: int = 0
    operator_altitude_geo: float = 0.0
    timestamp: int = 0  # unix time (minutes since 2019-01-01)

    def describe(self) -> str:
        return (
            f"System: operator=({self.operator_latitude:.6f}, "
            f"{self.operator_longitude:.6f}) area_count={self.area_count}"
        )


@dataclass
class SelfIDMessage:
    msg_type: int = 3
    description_type: int = 0
    description: str = ""

    def describe(self) -> str:
        return f"SelfID: '{self.description}'"


@dataclass
class OperatorIDMessage:
    msg_type: int = 5
    operator_id_type: int = 0
    operator_id: str = ""

    def describe(self) -> str:
        return f"OperatorID: '{self.operator_id}'"


@dataclass
class AuthMessage:
    msg_type: int = 2
    auth_type: int = 0
    page_number: int = 0
    page_count: int = 0
    length: int = 0
    timestamp: int = 0
    auth_data: bytes = b""

    def describe(self) -> str:
        return f"Auth: type={self.auth_type} page={self.page_number}/{self.page_count}"


def parse_basic_id(data: bytes) -> BasicIDMessage:
    """Parse a Basic ID message (type 0) from 25-byte ODID payload."""
    if len(data) < ODID_MSG_SIZE:
        raise ValueError(f"Expected {ODID_MSG_SIZE} bytes, got {len(data)}")

    byte0 = data[0]
    msg_type = (byte0 >> 4) & 0x0F
    proto_version = byte0 & 0x0F

    byte1 = data[1]
    id_type = (byte1 >> 4) & 0x0F
    ua_type = byte1 & 0x0F

    uas_id_raw = data[2:22]
    uas_id = uas_id_raw.split(b"\x00")[0].decode("ascii", errors="replace")

    return BasicIDMessage(
        msg_type=msg_type,
        id_type=id_type,
        ua_type=ua_type,
        uas_id=uas_id,
    )


def parse_location(data: bytes) -> LocationMessage:
    """Parse a Location/Vector message (type 1) from 25-byte ODID payload."""
    if len(data) < ODID_MSG_SIZE:
        raise ValueError(f"Expected {ODID_MSG_SIZE} bytes, got {len(data)}")

    byte0 = data[0]
    byte1 = data[1]
    status = (byte1 >> 4) & 0x0F
    height_type = (byte1 >> 2) & 0x01
    ew_direction = (byte1 >> 1) & 0x01
    speed_mult = byte1 & 0x01

    direction_raw = data[2]
    direction = direction_raw * (180.0 / 179.0 if ew_direction == 0 else 1.0)
    if ew_direction:
        direction += 180.0

    speed_h_raw = data[3]
    mult = 0.25 if speed_mult == 0 else 0.75
    speed_h = speed_h_raw * mult + (speed_mult * 255 * 0.25 if speed_mult else 0)

    speed_v_raw = struct.unpack_from("<b", data, 4)[0]
    speed_v = speed_v_raw * 0.5

    lat = struct.unpack_from("<i", data, 5)[0] * 1e-7
    lon = struct.unpack_from("<i", data, 9)[0] * 1e-7

    alt_baro_raw = struct.unpack_from("<H", data, 13)[0]
    alt_baro = alt_baro_raw * 0.5 - 1000.0

    alt_geo_raw = struct.unpack_from("<H", data, 15)[0]
    alt_geo = alt_geo_raw * 0.5 - 1000.0

    height_raw = struct.unpack_from("<H", data, 17)[0]
    height = height_raw * 0.5 - 1000.0

    byte19 = data[19]
    h_acc = (byte19 >> 4) & 0x0F
    v_acc = byte19 & 0x0F

    byte20 = data[20]
    baro_acc = (byte20 >> 4) & 0x0F
    speed_acc = byte20 & 0x0F

    ts_raw = struct.unpack_from("<H", data, 21)[0]
    timestamp = ts_raw * 0.1

    return LocationMessage(
        msg_type=1,
        status=status,
        direction=direction,
        speed_horizontal=speed_h,
        speed_vertical=speed_v,
        latitude=lat,
        longitude=lon,
        altitude_baro=alt_baro,
        altitude_geo=alt_geo,
        height_above_takeoff=height,
        horizontal_accuracy=h_acc,
        vertical_accuracy=v_acc,
        speed_accuracy=speed_acc,
        timestamp=timestamp,
    )


def parse_system(data: bytes) -> SystemMessage:
    """Parse a System message (type 4) from 25-byte ODID payload."""
    if len(data) < ODID_MSG_SIZE:
        raise ValueError(f"Expected {ODID_MSG_SIZE} bytes, got {len(data)}")

    byte1 = data[1]
    op_loc_type = (byte1 >> 6) & 0x03
    class_type = (byte1 >> 4) & 0x03
    ua_class = byte1 & 0x0F

    op_lat = struct.unpack_from("<i", data, 2)[0] * 1e-7
    op_lon = struct.unpack_from("<i", data, 6)[0] * 1e-7

    area_count = struct.unpack_from("<H", data, 10)[0]
    area_radius_raw = data[12]
    area_radius = area_radius_raw * 10.0  # meters

    area_ceiling_raw = struct.unpack_from("<H", data, 13)[0]
    area_ceiling = area_ceiling_raw * 0.5 - 1000.0

    area_floor_raw = struct.unpack_from("<H", data, 15)[0]
    area_floor = area_floor_raw * 0.5 - 1000.0

    op_alt_raw = struct.unpack_from("<H", data, 17)[0]
    op_alt = op_alt_raw * 0.5 - 1000.0

    timestamp = struct.unpack_from("<I", data, 19)[0]

    return SystemMessage(
        msg_type=4,
        operator_location_type=op_loc_type,
        classification_type=class_type,
        operator_latitude=op_lat,
        operator_longitude=op_lon,
        area_count=area_count,
        area_radius=area_radius,
        area_ceiling=area_ceiling,
        area_floor=area_floor,
        ua_classification=ua_class,
        operator_altitude_geo=op_alt,
        timestamp=timestamp,
    )


def parse_self_id(data: bytes) -> SelfIDMessage:
    """Parse a Self-ID message (type 3) from 25-byte ODID payload."""
    if len(data) < ODID_MSG_SIZE:
        raise ValueError(f"Expected {ODID_MSG_SIZE} bytes, got {len(data)}")

    desc_type = data[1]
    desc_raw = data[2:25]
    desc = desc_raw.split(b"\x00")[0].decode("ascii", errors="replace")

    return SelfIDMessage(msg_type=3, description_type=desc_type, description=desc)


def parse_operator_id(data: bytes) -> OperatorIDMessage:
    """Parse an Operator ID message (type 5) from 25-byte ODID payload."""
    if len(data) < ODID_MSG_SIZE:
        raise ValueError(f"Expected {ODID_MSG_SIZE} bytes, got {len(data)}")

    op_id_type = data[1]
    op_id_raw = data[2:22]
    op_id = op_id_raw.split(b"\x00")[0].decode("ascii", errors="replace")

    return OperatorIDMessage(
        msg_type=5, operator_id_type=op_id_type, operator_id=op_id,
    )


def parse_auth(data: bytes) -> AuthMessage:
    """Parse an Authentication message (type 2) from 25-byte ODID payload."""
    if len(data) < ODID_MSG_SIZE:
        raise ValueError(f"Expected {ODID_MSG_SIZE} bytes, got {len(data)}")

    byte1 = data[1]
    auth_type = (byte1 >> 4) & 0x0F
    page_number = byte1 & 0x0F

    if page_number == 0:
        page_count = data[2]
        length = data[3]
        timestamp = struct.unpack_from("<I", data, 4)[0]
        auth_data = data[8:25]
    else:
        page_count = 0
        length = 0
        timestamp = 0
        auth_data = data[2:25]

    return AuthMessage(
        msg_type=2,
        auth_type=auth_type,
        page_number=page_number,
        page_count=page_count,
        length=length,
        timestamp=timestamp,
        auth_data=auth_data,
    )


def parse_odid_message(data: bytes) -> dict:
    """Parse any ODID message from its 25-byte payload.

    Returns a dict representation of the parsed message.
    """
    if len(data) < 1:
        return {"error": "empty payload"}

    msg_type = (data[0] >> 4) & 0x0F

    parsers = {
        ODIDMessageType.BASIC_ID: parse_basic_id,
        ODIDMessageType.LOCATION: parse_location,
        ODIDMessageType.AUTH: parse_auth,
        ODIDMessageType.SELF_ID: parse_self_id,
        ODIDMessageType.SYSTEM: parse_system,
        ODIDMessageType.OPERATOR_ID: parse_operator_id,
    }

    if msg_type == ODIDMessageType.MESSAGE_PACK:
        return _parse_message_pack(data)

    parser = parsers.get(msg_type)
    if parser is None:
        return {"msg_type": msg_type, "raw": data.hex(), "error": "unknown type"}

    msg = parser(data)
    result = asdict(msg) if hasattr(msg, "__dataclass_fields__") else vars(msg)
    if isinstance(result.get("auth_data"), bytes):
        result["auth_data"] = result["auth_data"].hex()
    result["_description"] = msg.describe()
    return result


def _parse_message_pack(data: bytes) -> dict:
    """Parse a Message Pack (type 0xF) containing multiple ODID messages."""
    if len(data) < 2:
        return {"msg_type": 0xF, "error": "too short"}

    byte1 = data[1]
    msg_count = byte1 & 0x0F

    messages = []
    offset = 2
    for i in range(msg_count):
        if offset + ODID_MSG_SIZE > len(data):
            break
        sub_msg = parse_odid_message(data[offset : offset + ODID_MSG_SIZE])
        messages.append(sub_msg)
        offset += ODID_MSG_SIZE

    return {
        "msg_type": 0xF,
        "msg_type_name": "MESSAGE_PACK",
        "message_count": msg_count,
        "messages": messages,
    }


def extract_odid_from_ble_adv(adv_payload: bytes) -> list[dict]:
    """Extract ODID messages from a BLE advertising payload.

    Scans the AD structures for the ODID company-specific type (0xFF with
    company ID 0xFFFA). Returns parsed messages found.
    """
    messages = []
    offset = 0

    while offset < len(adv_payload):
        if offset + 1 > len(adv_payload):
            break
        length = adv_payload[offset]
        if length == 0:
            break
        if offset + 1 + length > len(adv_payload):
            break

        ad_type = adv_payload[offset + 1]
        ad_data = adv_payload[offset + 2 : offset + 1 + length]

        # 0xFF = Manufacturer Specific Data
        if ad_type == 0xFF and len(ad_data) >= 2:
            company_id = struct.unpack_from("<H", ad_data, 0)[0]
            if company_id == ODID_COMPANY_ID:
                # ODID message follows the 2-byte company ID + 1 byte counter
                odid_offset = 3  # company_id (2) + msg_counter (1)
                if len(ad_data) >= odid_offset + ODID_MSG_SIZE:
                    odid_payload = ad_data[odid_offset : odid_offset + ODID_MSG_SIZE]
                    msg = parse_odid_message(odid_payload)
                    messages.append(msg)

        offset += 1 + length

    return messages


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Broadcast Remote ID decoder — parses ASTM F3411 Open Drone ID "
            "messages from BLE advertising payloads"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
    hex     — Parse ODID message(s) from hex-encoded bytes (for testing)
    ble-adv — Parse ODID from a BLE advertising payload (hex-encoded)

The BLE PHY-layer decoding (IQ -> BLE packets) is handled by the BLE
decoder in the sibling thread. This tool focuses on the ODID message
format parsing layer above the BLE transport.

Capture command (HackRF, BLE channel 37):
    hackrf_transfer -r rid_ble_ch37.raw -f 2402000000 -s 4000000 \\
        -g 40 -l 32 -a 1 -n 40000000

NOTE: No real Broadcast RID capture exists in this project.
""",
    )

    sub = parser.add_subparsers(dest="mode")

    hex_parser = sub.add_parser("hex", help="Parse ODID message from hex string")
    hex_parser.add_argument("hexdata", help="25-byte ODID message as hex string")

    adv_parser = sub.add_parser("ble-adv", help="Parse ODID from BLE advertising payload")
    adv_parser.add_argument("hexdata", help="BLE advertising payload as hex string")

    pack_parser = sub.add_parser("pack", help="Parse an ODID Message Pack")
    pack_parser.add_argument("hexdata", help="Message pack payload as hex string")

    args = parser.parse_args()

    if args.mode == "hex":
        data = bytes.fromhex(args.hexdata)
        if len(data) < ODID_MSG_SIZE:
            print(f"[!] Need at least {ODID_MSG_SIZE} bytes, got {len(data)}", file=sys.stderr)
            sys.exit(1)
        msg = parse_odid_message(data[:ODID_MSG_SIZE])
        print(json.dumps(msg, indent=2, default=str))

    elif args.mode == "ble-adv":
        adv_data = bytes.fromhex(args.hexdata)
        messages = extract_odid_from_ble_adv(adv_data)
        if not messages:
            print("[!] No ODID messages found in advertising payload", file=sys.stderr)
            sys.exit(1)
        for msg in messages:
            print(json.dumps(msg, indent=2, default=str))

    elif args.mode == "pack":
        data = bytes.fromhex(args.hexdata)
        msg = _parse_message_pack(data)
        print(json.dumps(msg, indent=2, default=str))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
