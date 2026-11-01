#!/usr/bin/env python3
"""
Passive Telemetry Interceptor — Drone CTF Recon Tool

Silently receives and decodes drone telemetry from the air without
transmitting. Designed for pre-attack reconnaissance: identify drones,
track positions, read battery levels, and fingerprint protocols before
engaging with active tools.

Supported protocols:
    CRSF/ELRS  — TBS Crossfire / ExpressLRS telemetry frames
    MAVLink    — MAVLink v1/v2 heartbeat, GPS, attitude, battery
    FrSky      — S.Port and F.Port telemetry frames

Radio input via SoapySDR (device-agnostic: Ettus B205mini/B210, RTL-SDR,
HackRF, etc.) or from a raw IQ file for offline analysis.

Output:
    - Live terminal display of detected drones and telemetry
    - JSON Lines log files for post-analysis
    - Optional UDP output for the unified recon dashboard

Usage:
    # Live capture on 2.4 GHz ELRS band
    python3 passive-telemetry-interceptor.py --freq 2.4e9 --protocol elrs

    # Auto-detect protocol on 915 MHz
    python3 passive-telemetry-interceptor.py --freq 915e6

    # MAVLink on 433 MHz with specific SDR
    python3 passive-telemetry-interceptor.py --freq 433e6 --protocol mavlink \\
        --sdr-args "driver=uhd,serial=..."

    # Offline analysis from IQ recording
    python3 passive-telemetry-interceptor.py --iq-file capture.raw --protocol crsf

    # Feed decoded telemetry to the recon dashboard
    python3 passive-telemetry-interceptor.py --freq 2.4e9 --dashboard localhost:4200

Requires: SoapySDR with Python bindings (pip install SoapySDR),
          numpy (pip install numpy)
Optional: pymavlink for extended MAVLink decoding (pip install pymavlink)

For authorized CTF research and security testing only.
"""

import argparse
import datetime
import hashlib
import json
import logging
import signal
import socket
import struct
import sys
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from enum import Enum, auto
from pathlib import Path

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import SoapySDR
    from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX
    HAS_SOAPY = True
except ImportError:
    HAS_SOAPY = False

log = logging.getLogger("telemetry-interceptor")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Protocol(Enum):
    CRSF = auto()
    ELRS = auto()
    MAVLINK_V1 = auto()
    MAVLINK_V2 = auto()
    FRSKY_SPORT = auto()
    FRSKY_FPORT = auto()
    UNKNOWN = auto()


@dataclass
class GPSPosition:
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    groundspeed: float = 0.0
    heading: float = 0.0
    satellites: int = 0
    fix_type: int = 0
    timestamp: float = 0.0


@dataclass
class BatteryState:
    voltage: float = 0.0
    current: float = 0.0
    remaining_pct: int = 0
    consumed_mah: int = 0
    timestamp: float = 0.0


@dataclass
class Attitude:
    pitch: float = 0.0
    roll: float = 0.0
    yaw: float = 0.0
    timestamp: float = 0.0


@dataclass
class LinkStats:
    rssi_db: int = 0
    snr: float = 0.0
    lq: int = 0
    tx_power_mw: int = 0
    timestamp: float = 0.0


@dataclass
class DroneState:
    """Aggregated state for one detected drone."""
    drone_id: str = ""
    protocol: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    packet_count: int = 0
    flight_mode: str = ""
    armed: bool = False
    gps: GPSPosition = field(default_factory=GPSPosition)
    battery: BatteryState = field(default_factory=BatteryState)
    attitude: Attitude = field(default_factory=Attitude)
    link: LinkStats = field(default_factory=LinkStats)
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CRC helpers
# ---------------------------------------------------------------------------

def crsf_crc8(data: bytes) -> int:
    """CRSF CRC-8/DVB-S2 used for frame validation."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0xD5
            else:
                crc = crc << 1
            crc &= 0xFF
    return crc


def mavlink_crc16(data: bytes, extra_crc: int = 0) -> int:
    """MAVLink X.25 CRC (CRC-16/MCRF4XX)."""
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        crc &= 0xFFFF
    if extra_crc:
        tmp = extra_crc ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        crc &= 0xFFFF
    return crc


# MAVLink CRC extras for common message types
MAVLINK_CRC_EXTRA = {
    0: 50,    # HEARTBEAT
    1: 124,   # SYS_STATUS
    24: 24,   # GPS_RAW_INT
    30: 39,   # ATTITUDE
    33: 104,  # GLOBAL_POSITION_INT
    74: 20,   # VFR_HUD
    253: 83,  # STATUSTEXT
}


# ---------------------------------------------------------------------------
# CRSF / ELRS telemetry decoder
# ---------------------------------------------------------------------------

CRSF_SYNC_BYTES = {0xC8, 0xEC, 0xEE}

CRSF_FRAME_TYPES = {
    0x02: "GPS",
    0x07: "VARIO",
    0x08: "BATTERY_SENSOR",
    0x09: "BARO_ALTITUDE",
    0x0A: "HEARTBEAT",
    0x0B: "VIDEO_TRANSMITTER",
    0x0E: "LINK_STATISTICS",
    0x10: "OPENTX_SYNC",
    0x14: "SUBSET_RC_CHANNELS",
    0x16: "RC_CHANNELS_PACKED",
    0x1C: "LINK_RX_ID",
    0x1D: "LINK_TX_ID",
    0x21: "ATTITUDE",
    0x28: "PING_DEVICES",
    0x29: "DEVICE_INFO",
    0x2D: "ELRS_STATUS",
}

# ELRS OTA version for FHSS / CRC derivation
OTA_VERSION_ID = 4


def elrs_binding_phrase_to_uid(phrase: str) -> bytes:
    """Convert ELRS binding phrase to 6-byte UID via MD5."""
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


class CRSFDecoder:
    """Decode CRSF/ELRS telemetry frames from a byte stream."""

    def __init__(self, rainbow_table: dict | None = None):
        self.buf = bytearray()
        self.rainbow = rainbow_table or {}
        self.frame_count = 0

    def feed(self, data: bytes) -> list[dict]:
        """Feed raw bytes, return list of decoded telemetry dicts."""
        self.buf.extend(data)
        results = []
        while len(self.buf) >= 4:
            # scan for sync byte
            sync_idx = -1
            for i, b in enumerate(self.buf):
                if b in CRSF_SYNC_BYTES:
                    sync_idx = i
                    break
            if sync_idx < 0:
                self.buf.clear()
                break
            if sync_idx > 0:
                self.buf = self.buf[sync_idx:]

            if len(self.buf) < 2:
                break
            frame_len = self.buf[1]
            if frame_len < 2 or frame_len > 64:
                self.buf.pop(0)
                continue
            total = frame_len + 2  # sync + len + (type+payload+crc)
            if len(self.buf) < total:
                break

            frame = bytes(self.buf[:total])
            self.buf = self.buf[total:]

            # validate CRC
            expected_crc = crsf_crc8(frame[2:-1])
            if expected_crc != frame[-1]:
                continue

            self.frame_count += 1
            decoded = self._decode_frame(frame)
            if decoded:
                results.append(decoded)
        return results

    def _decode_frame(self, frame: bytes) -> dict | None:
        _sync = frame[0]
        ftype = frame[2]
        payload = frame[3:-1]
        now = time.time()

        type_name = CRSF_FRAME_TYPES.get(ftype, f"0x{ftype:02X}")
        result = {
            "protocol": "CRSF",
            "frame_type": type_name,
            "frame_type_id": ftype,
            "timestamp": now,
            "raw_hex": frame.hex(),
        }

        if ftype == 0x02 and len(payload) >= 15:  # GPS
            lat = struct.unpack(">i", payload[0:4])[0] / 1e7
            lon = struct.unpack(">i", payload[4:8])[0] / 1e7
            groundspeed = struct.unpack(">H", payload[8:10])[0] / 100.0 * 3.6
            heading = struct.unpack(">H", payload[10:12])[0] / 100.0
            alt = struct.unpack(">H", payload[12:14])[0] - 1000
            sats = payload[14]
            result["gps"] = {
                "lat": lat, "lon": lon, "alt": alt,
                "groundspeed_kmh": groundspeed, "heading": heading,
                "satellites": sats,
            }

        elif ftype == 0x08 and len(payload) >= 8:  # BATTERY
            voltage = struct.unpack(">H", payload[0:2])[0] / 10.0
            current = struct.unpack(">H", payload[2:4])[0] / 10.0
            consumed = (payload[4] << 16) | (payload[5] << 8) | payload[6]
            remaining = payload[7]
            result["battery"] = {
                "voltage": voltage, "current_a": current,
                "consumed_mah": consumed, "remaining_pct": remaining,
            }

        elif ftype == 0x21 and len(payload) >= 6:  # ATTITUDE
            pitch = struct.unpack(">h", payload[0:2])[0] / 10000.0
            roll = struct.unpack(">h", payload[2:4])[0] / 10000.0
            yaw = struct.unpack(">h", payload[4:6])[0] / 10000.0
            result["attitude"] = {
                "pitch_rad": pitch, "roll_rad": roll, "yaw_rad": yaw,
            }

        elif ftype == 0x0E and len(payload) >= 10:  # LINK_STATISTICS
            result["link"] = {
                "rssi1_dbm": -1 * payload[0] if payload[0] else 0,
                "rssi2_dbm": -1 * payload[1] if payload[1] else 0,
                "uplink_lq": payload[2],
                "uplink_snr": struct.unpack("b", bytes([payload[3]]))[0],
                "active_antenna": payload[4],
                "rf_mode": payload[5],
                "uplink_tx_power": payload[6],
                "downlink_rssi_dbm": -1 * payload[7] if payload[7] else 0,
                "downlink_lq": payload[8],
                "downlink_snr": struct.unpack("b", bytes([payload[9]]))[0],
            }

        elif ftype == 0x09 and len(payload) >= 4:  # BARO ALTITUDE
            alt = struct.unpack(">H", payload[0:2])[0]
            alt_dm = alt - 10000  # decimeters offset
            result["baro"] = {
                "altitude_m": alt_dm / 10.0,
            }
            if len(payload) >= 6:
                vario = struct.unpack(">h", payload[2:4])[0]
                result["baro"]["vario_cm_s"] = vario

        elif ftype == 0x07 and len(payload) >= 2:  # VARIO
            vario = struct.unpack(">h", payload[0:2])[0]
            result["vario"] = {"vertical_speed_cm_s": vario}

        elif ftype == 0x29 and len(payload) >= 4:  # DEVICE INFO
            # extended frame: [dest][origin][name_null_terminated...]
            try:
                name_end = payload.index(0, 2)
                name = payload[2:name_end].decode("ascii", errors="replace")
                result["device_info"] = {"name": name}
            except (ValueError, IndexError):
                pass

        elif ftype == 0x2D and len(payload) >= 4:  # ELRS_STATUS
            result["elrs_status"] = {"raw": payload.hex()}

        return result


# ---------------------------------------------------------------------------
# MAVLink decoder
# ---------------------------------------------------------------------------

MAVLINK_STX_V1 = 0xFE
MAVLINK_STX_V2 = 0xFD

MAV_TYPE = {
    0: "GENERIC", 1: "FIXED_WING", 2: "QUADROTOR", 3: "COAXIAL",
    4: "HELICOPTER", 6: "GCS", 10: "GROUND_ROVER", 11: "SURFACE_BOAT",
    12: "SUBMARINE", 13: "HEXAROTOR", 14: "OCTOROTOR", 15: "TRICOPTER",
    20: "KITE", 21: "PARACHUTE", 26: "VTOL_TAILSITTER_DUOROTOR",
    29: "VTOL_TILTROTOR",
}

MAV_AUTOPILOT = {
    0: "GENERIC", 3: "ARDUPILOTMEGA", 4: "OPENPILOT", 8: "INVALID",
    12: "PX4", 14: "BETAFLIGHT",
}

MAV_STATE = {
    0: "UNINIT", 1: "BOOT", 2: "CALIBRATING", 3: "STANDBY",
    4: "ACTIVE", 5: "CRITICAL", 6: "EMERGENCY", 7: "POWEROFF",
    8: "FLIGHT_TERMINATION",
}

MAV_MODE_FLAG_ARMED = 0x80


class MAVLinkDecoder:
    """Decode MAVLink v1/v2 telemetry from a byte stream."""

    def __init__(self):
        self.buf = bytearray()
        self.frame_count = 0

    def feed(self, data: bytes) -> list[dict]:
        self.buf.extend(data)
        results = []
        while len(self.buf) >= 8:
            # scan for start byte
            stx_idx = -1
            for i, b in enumerate(self.buf):
                if b in (MAVLINK_STX_V1, MAVLINK_STX_V2):
                    stx_idx = i
                    break
            if stx_idx < 0:
                self.buf.clear()
                break
            if stx_idx > 0:
                self.buf = self.buf[stx_idx:]

            stx = self.buf[0]
            if stx == MAVLINK_STX_V1:
                result = self._try_v1()
            else:
                result = self._try_v2()

            if result is None:
                # not enough data yet or invalid — wait for more
                if len(self.buf) > 280:
                    self.buf.pop(0)
                else:
                    break
            elif result == "skip":
                self.buf.pop(0)
            else:
                results.append(result)
        return results

    def _try_v1(self) -> dict | str | None:
        if len(self.buf) < 8:
            return None
        payload_len = self.buf[1]
        total = 8 + payload_len
        if len(self.buf) < total:
            return None

        frame = bytes(self.buf[:total])
        _seq = frame[2]
        sys_id = frame[3]
        comp_id = frame[4]
        msg_id = frame[5]
        payload = frame[6:6 + payload_len]
        crc_bytes = frame[6 + payload_len:8 + payload_len]

        # validate CRC
        crc_data = frame[1:6 + payload_len]
        extra = MAVLINK_CRC_EXTRA.get(msg_id, 0)
        expected = mavlink_crc16(crc_data, extra)
        received = struct.unpack("<H", crc_bytes)[0]
        if expected != received and extra != 0:
            return "skip"

        self.buf = self.buf[total:]
        self.frame_count += 1
        return self._decode_msg(1, sys_id, comp_id, msg_id, payload)

    def _try_v2(self) -> dict | str | None:
        if len(self.buf) < 12:
            return None
        payload_len = self.buf[1]
        incompat_flags = self.buf[2]
        has_signature = bool(incompat_flags & 0x01)
        total = 12 + payload_len + (13 if has_signature else 0)
        if len(self.buf) < total:
            return None

        frame = bytes(self.buf[:total])
        _seq = frame[4]
        sys_id = frame[5]
        comp_id = frame[6]
        msg_id = frame[7] | (frame[8] << 8) | (frame[9] << 16)
        payload = frame[10:10 + payload_len]
        crc_bytes = frame[10 + payload_len:12 + payload_len]

        # validate CRC
        crc_data = frame[1:10 + payload_len]
        extra = MAVLINK_CRC_EXTRA.get(msg_id, 0)
        expected = mavlink_crc16(crc_data, extra)
        received = struct.unpack("<H", crc_bytes)[0]
        if expected != received and extra != 0:
            return "skip"

        self.buf = self.buf[total:]
        self.frame_count += 1
        return self._decode_msg(2, sys_id, comp_id, msg_id, payload)

    def _decode_msg(self, version: int, sys_id: int, comp_id: int,
                    msg_id: int, payload: bytes) -> dict:
        now = time.time()
        result = {
            "protocol": f"MAVLink_v{version}",
            "sys_id": sys_id,
            "comp_id": comp_id,
            "msg_id": msg_id,
            "timestamp": now,
        }

        if msg_id == 0 and len(payload) >= 9:  # HEARTBEAT
            custom_mode = struct.unpack("<I", payload[0:4])[0]
            mav_type = payload[4]
            autopilot = payload[5]
            base_mode = payload[6]
            system_status = payload[7]
            result["heartbeat"] = {
                "type": MAV_TYPE.get(mav_type, f"UNKNOWN({mav_type})"),
                "autopilot": MAV_AUTOPILOT.get(autopilot, f"UNKNOWN({autopilot})"),
                "base_mode": base_mode,
                "custom_mode": custom_mode,
                "system_status": MAV_STATE.get(system_status, f"UNKNOWN({system_status})"),
                "armed": bool(base_mode & MAV_MODE_FLAG_ARMED),
            }

        elif msg_id == 1 and len(payload) >= 31:  # SYS_STATUS
            voltage = struct.unpack("<H", payload[14:16])[0]
            current = struct.unpack("<h", payload[16:18])[0]
            remaining = struct.unpack("<b", payload[30:31])[0]
            result["sys_status"] = {
                "voltage_mv": voltage,
                "current_ca": current,
                "remaining_pct": remaining,
            }

        elif msg_id == 24 and len(payload) >= 30:  # GPS_RAW_INT
            fix = payload[28] if len(payload) > 28 else 0
            lat = struct.unpack("<i", payload[8:12])[0] / 1e7
            lon = struct.unpack("<i", payload[12:16])[0] / 1e7
            alt = struct.unpack("<i", payload[16:20])[0] / 1000.0
            sats = payload[29] if len(payload) > 29 else 0
            result["gps"] = {
                "fix_type": fix, "lat": lat, "lon": lon,
                "alt": alt, "satellites": sats,
            }

        elif msg_id == 30 and len(payload) >= 28:  # ATTITUDE
            roll = struct.unpack("<f", payload[4:8])[0]
            pitch = struct.unpack("<f", payload[8:12])[0]
            yaw = struct.unpack("<f", payload[12:16])[0]
            result["attitude"] = {
                "roll_rad": round(roll, 4),
                "pitch_rad": round(pitch, 4),
                "yaw_rad": round(yaw, 4),
            }

        elif msg_id == 33 and len(payload) >= 28:  # GLOBAL_POSITION_INT
            lat = struct.unpack("<i", payload[4:8])[0] / 1e7
            lon = struct.unpack("<i", payload[8:12])[0] / 1e7
            alt = struct.unpack("<i", payload[12:16])[0] / 1000.0
            rel_alt = struct.unpack("<i", payload[16:20])[0] / 1000.0
            vx = struct.unpack("<h", payload[20:22])[0] / 100.0
            vy = struct.unpack("<h", payload[22:24])[0] / 100.0
            vz = struct.unpack("<h", payload[24:26])[0] / 100.0
            hdg = struct.unpack("<H", payload[26:28])[0] / 100.0
            result["global_position"] = {
                "lat": lat, "lon": lon, "alt_msl": alt,
                "alt_rel": rel_alt, "vx": vx, "vy": vy, "vz": vz,
                "heading": hdg,
            }

        elif msg_id == 74 and len(payload) >= 20:  # VFR_HUD
            airspeed = struct.unpack("<f", payload[0:4])[0]
            groundspeed = struct.unpack("<f", payload[4:8])[0]
            alt = struct.unpack("<f", payload[8:12])[0]
            climb = struct.unpack("<f", payload[12:16])[0]
            heading = struct.unpack("<h", payload[16:18])[0]
            throttle = struct.unpack("<H", payload[18:20])[0]
            result["vfr_hud"] = {
                "airspeed": round(airspeed, 1),
                "groundspeed": round(groundspeed, 1),
                "alt": round(alt, 1),
                "climb": round(climb, 1),
                "heading": heading,
                "throttle": throttle,
            }

        elif msg_id == 253 and len(payload) >= 2:  # STATUSTEXT
            severity = payload[0]
            text = payload[1:51].split(b"\x00")[0].decode("ascii", errors="replace")
            result["statustext"] = {
                "severity": severity,
                "text": text,
            }

        else:
            result["msg_name"] = f"MSG_{msg_id}"
            result["payload_hex"] = payload.hex()

        return result


# ---------------------------------------------------------------------------
# FrSky decoder
# ---------------------------------------------------------------------------

FRSKY_SPORT_START = 0x7E
FRSKY_FPORT_START = 0x7E

FRSKY_SENSOR_IDS = {
    0x0100: "ALT", 0x0200: "VARIO", 0x0300: "CURR",
    0x0400: "VFAS", 0x0500: "CELLS", 0x0600: "HEADING",
    0x0700: "ACCEL_X", 0x0710: "ACCEL_Y", 0x0720: "ACCEL_Z",
    0x0800: "GPS_LONG_LAT", 0x0820: "GPS_ALT",
    0x0830: "GPS_SPEED", 0x0840: "GPS_COURSE",
    0x0850: "GPS_DATE", 0x0860: "GPS_SATS",
    0x0900: "RPM", 0x0A00: "FUEL", 0x0B00: "TEMP1",
    0x0B10: "TEMP2", 0xF101: "RSSI", 0xF103: "ADC1",
    0xF104: "ADC2", 0xF105: "BATT",
}


class FrSkyDecoder:
    """Decode FrSky S.Port and F.Port telemetry frames."""

    def __init__(self):
        self.buf = bytearray()
        self.frame_count = 0

    def feed(self, data: bytes) -> list[dict]:
        self.buf.extend(data)
        results = []
        while len(self.buf) >= 10:
            # scan for frame start
            start_idx = -1
            for i, b in enumerate(self.buf):
                if b == FRSKY_SPORT_START:
                    start_idx = i
                    break
            if start_idx < 0:
                self.buf.clear()
                break
            if start_idx > 0:
                self.buf = self.buf[start_idx:]

            if len(self.buf) < 10:
                break

            # S.Port frame: 0x7E [phys_id] [prim] [sensor_id_lo] [sensor_id_hi] [data x4]
            frame = bytes(self.buf[1:10])
            self.buf = self.buf[10:]

            # byte-unstuff
            frame = self._unstuff(frame)
            if len(frame) < 8:
                continue

            phys_id = frame[0]
            prim = frame[1]
            if prim not in (0x10, 0x32):  # data frame or F.Port
                continue

            sensor_id = frame[2] | (frame[3] << 8)
            value = struct.unpack("<I", frame[4:8])[0]

            self.frame_count += 1
            decoded = self._decode_sensor(sensor_id, value, phys_id)
            if decoded:
                results.append(decoded)
        return results

    @staticmethod
    def _unstuff(data: bytes) -> bytes:
        out = bytearray()
        i = 0
        while i < len(data):
            if data[i] == 0x7D and i + 1 < len(data):
                out.append(data[i + 1] ^ 0x20)
                i += 2
            else:
                out.append(data[i])
                i += 1
        return bytes(out)

    def _decode_sensor(self, sensor_id: int, value: int, phys_id: int) -> dict | None:
        now = time.time()
        name = FRSKY_SENSOR_IDS.get(sensor_id & 0xFFF0, f"0x{sensor_id:04X}")
        result = {
            "protocol": "FrSky_SPort",
            "sensor": name,
            "sensor_id": f"0x{sensor_id:04X}",
            "phys_id": phys_id,
            "raw_value": value,
            "timestamp": now,
        }

        base = sensor_id & 0xFFF0
        if base == 0x0100:  # ALT
            result["altitude_cm"] = struct.unpack("<i", struct.pack("<I", value))[0]
        elif base == 0x0200:  # VARIO
            result["vario_cm_s"] = struct.unpack("<i", struct.pack("<I", value))[0]
        elif base == 0x0400:  # VFAS (voltage)
            result["voltage"] = value / 100.0
        elif base == 0x0300:  # CURR
            result["current_a"] = value / 10.0
        elif base == 0x0800:  # GPS lat/lon
            deg = (value & 0x3FFFFFFF) / 10000
            minutes = deg % 100
            degrees = int(deg / 100) + minutes / 60.0
            if value & 0x40000000:
                degrees = -degrees
            coord_type = "lon" if value & 0x80000000 else "lat"
            result[coord_type] = round(degrees, 7)
        elif base == 0x0820:  # GPS ALT
            result["gps_alt_cm"] = struct.unpack("<i", struct.pack("<I", value))[0]
        elif base == 0x0830:  # GPS SPEED
            result["gps_speed_knots"] = value / 1000.0
        elif base == 0x0860:  # GPS SATS
            result["satellites"] = value
        elif base == 0x0B00:  # TEMP1 — often flight mode
            result["flight_mode_raw"] = value
        elif sensor_id == 0xF101:
            result["rssi"] = value & 0xFF

        return result


# ---------------------------------------------------------------------------
# Protocol auto-detection
# ---------------------------------------------------------------------------

class ProtocolDetector:
    """Feed raw bytes and vote on which protocol is present."""

    def __init__(self):
        self.votes: dict[str, int] = defaultdict(int)
        self.bytes_seen = 0

    def feed(self, data: bytes):
        self.bytes_seen += len(data)
        for b in data:
            if b == MAVLINK_STX_V1:
                self.votes["mavlink"] += 2
            elif b == MAVLINK_STX_V2:
                self.votes["mavlink"] += 3
            elif b in CRSF_SYNC_BYTES:
                self.votes["crsf"] += 2
            elif b == FRSKY_SPORT_START:
                self.votes["frsky"] += 1

    def best_guess(self) -> str:
        if not self.votes:
            return "unknown"
        return max(self.votes, key=self.votes.get)


# ---------------------------------------------------------------------------
# Drone tracker (aggregates telemetry into per-drone state)
# ---------------------------------------------------------------------------

class DroneTracker:
    """Maintains state for all detected drones."""

    def __init__(self):
        self.drones: dict[str, DroneState] = {}
        self.lock = threading.Lock()

    def update(self, decoded: dict):
        drone_id = self._extract_id(decoded)
        now = time.time()

        with self.lock:
            if drone_id not in self.drones:
                self.drones[drone_id] = DroneState(
                    drone_id=drone_id,
                    protocol=decoded.get("protocol", "?"),
                    first_seen=now,
                )
            drone = self.drones[drone_id]
            drone.last_seen = now
            drone.packet_count += 1
            drone.protocol = decoded.get("protocol", drone.protocol)

            if "gps" in decoded:
                g = decoded["gps"]
                drone.gps.lat = g.get("lat", drone.gps.lat)
                drone.gps.lon = g.get("lon", drone.gps.lon)
                drone.gps.alt = g.get("alt", drone.gps.alt)
                drone.gps.groundspeed = g.get("groundspeed_kmh", drone.gps.groundspeed)
                drone.gps.heading = g.get("heading", drone.gps.heading)
                drone.gps.satellites = g.get("satellites", drone.gps.satellites)
                drone.gps.fix_type = g.get("fix_type", drone.gps.fix_type)
                drone.gps.timestamp = now

            if "global_position" in decoded:
                gp = decoded["global_position"]
                drone.gps.lat = gp.get("lat", drone.gps.lat)
                drone.gps.lon = gp.get("lon", drone.gps.lon)
                drone.gps.alt = gp.get("alt_msl", drone.gps.alt)
                drone.gps.heading = gp.get("heading", drone.gps.heading)
                drone.gps.timestamp = now

            if "battery" in decoded:
                b = decoded["battery"]
                drone.battery.voltage = b.get("voltage", drone.battery.voltage)
                drone.battery.current = b.get("current_a", drone.battery.current)
                drone.battery.remaining_pct = b.get("remaining_pct", drone.battery.remaining_pct)
                drone.battery.consumed_mah = b.get("consumed_mah", drone.battery.consumed_mah)
                drone.battery.timestamp = now

            if "sys_status" in decoded:
                s = decoded["sys_status"]
                drone.battery.voltage = s.get("voltage_mv", 0) / 1000.0
                drone.battery.current = s.get("current_ca", 0) / 100.0
                drone.battery.remaining_pct = s.get("remaining_pct", drone.battery.remaining_pct)
                drone.battery.timestamp = now

            if "attitude" in decoded:
                a = decoded["attitude"]
                drone.attitude.pitch = a.get("pitch_rad", drone.attitude.pitch)
                drone.attitude.roll = a.get("roll_rad", drone.attitude.roll)
                drone.attitude.yaw = a.get("yaw_rad", drone.attitude.yaw)
                drone.attitude.timestamp = now

            if "link" in decoded:
                lk = decoded["link"]
                drone.link.rssi_db = lk.get("rssi1_dbm", drone.link.rssi_db)
                drone.link.snr = lk.get("uplink_snr", drone.link.snr)
                drone.link.lq = lk.get("uplink_lq", drone.link.lq)
                drone.link.timestamp = now

            if "heartbeat" in decoded:
                hb = decoded["heartbeat"]
                drone.flight_mode = hb.get("system_status", drone.flight_mode)
                drone.armed = hb.get("armed", drone.armed)
                drone.extra["mav_type"] = hb.get("type", "")
                drone.extra["autopilot"] = hb.get("autopilot", "")

            if "vfr_hud" in decoded:
                vh = decoded["vfr_hud"]
                drone.extra["airspeed"] = vh.get("airspeed", 0)
                drone.extra["groundspeed"] = vh.get("groundspeed", 0)
                drone.extra["throttle"] = vh.get("throttle", 0)

    def _extract_id(self, decoded: dict) -> str:
        proto = decoded.get("protocol", "")
        if "MAVLink" in proto:
            sys_id = decoded.get("sys_id", 0)
            comp_id = decoded.get("comp_id", 0)
            return f"mav_{sys_id}:{comp_id}"
        elif proto == "CRSF":
            return "crsf_0"
        elif "FrSky" in proto:
            phys_id = decoded.get("phys_id", 0)
            return f"frsky_{phys_id}"
        return "unknown_0"

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [asdict(d) for d in self.drones.values()]


# ---------------------------------------------------------------------------
# JSON Lines logger
# ---------------------------------------------------------------------------

class TelemetryLogger:
    """Write decoded telemetry to JSON Lines files."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.logfile = self.output_dir / f"telemetry_{ts}.jsonl"
        self.fh = open(self.logfile, "a")
        self.count = 0

    def log(self, entry: dict):
        self.fh.write(json.dumps(entry, default=str) + "\n")
        self.count += 1
        if self.count % 100 == 0:
            self.fh.flush()

    def close(self):
        self.fh.flush()
        self.fh.close()


# ---------------------------------------------------------------------------
# Dashboard UDP feed
# ---------------------------------------------------------------------------

class DashboardFeed:
    """Send drone state snapshots to the unified recon dashboard via UDP."""

    def __init__(self, host: str, port: int):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.interval = 1.0
        self.last_send = 0.0

    def maybe_send(self, tracker: DroneTracker):
        now = time.time()
        if now - self.last_send < self.interval:
            return
        self.last_send = now
        snapshot = tracker.snapshot()
        if not snapshot:
            return
        msg = json.dumps({
            "source": "passive-telemetry-interceptor",
            "timestamp": now,
            "drones": snapshot,
        }, default=str).encode()
        try:
            self.sock.sendto(msg, self.addr)
        except OSError:
            pass

    def close(self):
        self.sock.close()


# ---------------------------------------------------------------------------
# SoapySDR radio source
# ---------------------------------------------------------------------------

class SDRSource:
    """Receive samples from an SDR via SoapySDR."""

    def __init__(self, args: str, freq: float, sample_rate: float,
                 bandwidth: float, gain: float):
        if not HAS_SOAPY:
            raise RuntimeError("SoapySDR not installed (pip install SoapySDR)")
        if not HAS_NUMPY:
            raise RuntimeError("numpy not installed (pip install numpy)")

        self.device = SoapySDR.Device(args if args else "")
        self.device.setSampleRate(SOAPY_SDR_RX, 0, sample_rate)
        self.device.setFrequency(SOAPY_SDR_RX, 0, freq)
        if bandwidth > 0:
            self.device.setBandwidth(SOAPY_SDR_RX, 0, bandwidth)
        if gain >= 0:
            self.device.setGain(SOAPY_SDR_RX, 0, gain)

        self.stream = self.device.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        self.sample_rate = sample_rate
        self.buf_size = 16384
        self.running = False

    def start(self):
        self.device.activateStream(self.stream)
        self.running = True

    def read(self) -> np.ndarray | None:
        buf = np.zeros(self.buf_size, dtype=np.complex64)
        sr = self.device.readStream(self.stream, [buf], self.buf_size)
        if sr.ret > 0:
            return buf[:sr.ret]
        return None

    def stop(self):
        self.running = False
        self.device.deactivateStream(self.stream)
        self.device.closeStream(self.stream)


# ---------------------------------------------------------------------------
# Simple AM/FM envelope demodulator (extracts data bits from RF)
# ---------------------------------------------------------------------------

def envelope_demod(samples: np.ndarray) -> bytes:
    """PROTOTYPE — amplitude-only envelope detector, NOT a real demodulator.

    Thresholds sample magnitudes and packs bits. Discards all phase/frequency
    information, so it cannot decode LoRa (ELRS), GFSK, or any modulation
    that encodes data outside the amplitude domain. Output will not contain
    valid protocol frames from those modulations.

    For validated telemetry parsing, supply already-demodulated bytes via
    --stdin-raw instead of using --freq or --iq-file with this path.
    """
    amplitude = np.abs(samples)
    # threshold at mean + 1 std
    threshold = np.mean(amplitude) + np.std(amplitude)
    bits = (amplitude > threshold).astype(np.uint8)

    # pack bits into bytes (MSB first)
    n_bytes = len(bits) // 8
    if n_bytes == 0:
        return b""
    bit_matrix = bits[:n_bytes * 8].reshape(n_bytes, 8)
    powers = np.array([128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8)
    return bytes((bit_matrix * powers).sum(axis=1).astype(np.uint8))


# ---------------------------------------------------------------------------
# Live terminal display
# ---------------------------------------------------------------------------

def render_display(tracker: DroneTracker, stats: dict):
    """Render a live summary to the terminal."""
    lines = []
    lines.append("\033[2J\033[H")  # clear screen
    lines.append("=" * 78)
    lines.append("  PASSIVE TELEMETRY INTERCEPTOR — RECEIVE ONLY")
    lines.append("=" * 78)
    lines.append(f"  Uptime: {stats.get('uptime', '?')}  |  "
                 f"Packets: {stats.get('total_packets', 0)}  |  "
                 f"Drones: {len(tracker.drones)}")
    lines.append("-" * 78)

    with tracker.lock:
        for did, drone in tracker.drones.items():
            age = time.time() - drone.last_seen
            stale = " [STALE]" if age > 10 else ""
            armed_str = "ARMED" if drone.armed else "disarmed"
            lines.append(
                f"  [{drone.protocol}] {did}{stale}  "
                f"mode={drone.flight_mode}  {armed_str}  "
                f"pkts={drone.packet_count}"
            )
            if drone.gps.lat != 0 or drone.gps.lon != 0:
                lines.append(
                    f"    GPS: {drone.gps.lat:.6f}, {drone.gps.lon:.6f}  "
                    f"alt={drone.gps.alt:.0f}m  sats={drone.gps.satellites}  "
                    f"spd={drone.gps.groundspeed:.1f}km/h"
                )
            if drone.battery.voltage > 0:
                lines.append(
                    f"    BAT: {drone.battery.voltage:.1f}V  "
                    f"{drone.battery.current:.1f}A  "
                    f"{drone.battery.remaining_pct}%  "
                    f"{drone.battery.consumed_mah}mAh"
                )
            if drone.attitude.timestamp > 0:
                import math
                lines.append(
                    f"    ATT: pitch={math.degrees(drone.attitude.pitch):.1f}° "
                    f"roll={math.degrees(drone.attitude.roll):.1f}° "
                    f"yaw={math.degrees(drone.attitude.yaw):.1f}°"
                )
            if drone.link.timestamp > 0:
                lines.append(
                    f"    LNK: rssi={drone.link.rssi_db}dBm  "
                    f"snr={drone.link.snr:.1f}dB  lq={drone.link.lq}%"
                )
            lines.append("")

    if not tracker.drones:
        lines.append("  Listening... no drones detected yet.")
        lines.append("")

    lines.append("-" * 78)
    lines.append("  Press Ctrl+C to stop")

    sys.stderr.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# IQ file source (offline analysis)
# ---------------------------------------------------------------------------

class IQFileSource:
    """Read IQ samples from a raw file (interleaved float32 I/Q pairs)."""

    def __init__(self, path: str, sample_rate: float):
        self.path = path
        self.sample_rate = sample_rate
        self.fh = open(path, "rb")
        self.chunk_size = 16384

    def read(self) -> np.ndarray | None:
        raw = self.fh.read(self.chunk_size * 8)  # 2 x float32 per sample
        if not raw:
            return None
        samples = np.frombuffer(raw, dtype=np.float32)
        if len(samples) % 2 != 0:
            samples = samples[:len(samples) - 1]
        return samples[0::2] + 1j * samples[1::2]

    def stop(self):
        self.fh.close()


# ---------------------------------------------------------------------------
# Stdin raw byte source (for piped data, e.g. from gr-osmosdr)
# ---------------------------------------------------------------------------

class StdinSource:
    """Read raw protocol bytes from stdin (no SDR demod needed)."""

    def __init__(self):
        self.chunk_size = 4096

    def read(self) -> bytes | None:
        data = sys.stdin.buffer.read(self.chunk_size)
        if not data:
            return None
        return data


# ---------------------------------------------------------------------------
# Main capture loop
# ---------------------------------------------------------------------------

def load_rainbow_table(path: str) -> dict:
    """Load ELRS rainbow table for binding phrase lookups.

    Keys the table on sync_bytes (UID[3:6] as colon-separated hex), which is
    the field present in the JSON produced by elrs-uid-rainbow.py.
    """
    table = {}
    try:
        entries = json.load(open(path))
        for entry in entries:
            key = entry.get("sync_bytes", "")
            if key:
                table[key.upper()] = entry
        if entries and not table:
            log.error("Rainbow table %s has %d entries but none have 'sync_bytes' — "
                      "wrong schema?", path, len(entries))
        else:
            log.info("Loaded %d rainbow table entries from %s", len(table), path)
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        log.warning("Could not load rainbow table: %s", e)
    return table


def run_capture(args):
    """Main capture loop."""
    # set up decoders
    rainbow = {}
    if args.rainbow_table:
        rainbow = load_rainbow_table(args.rainbow_table)

    crsf_dec = CRSFDecoder(rainbow_table=rainbow)
    mav_dec = MAVLinkDecoder()
    frsky_dec = FrSkyDecoder()
    tracker = DroneTracker()
    detector = ProtocolDetector()

    # set up logger
    logger = None
    if args.log_dir:
        logger = TelemetryLogger(args.log_dir)
        log.info("Logging to %s", logger.logfile)

    # set up dashboard feed
    dashboard = None
    if args.dashboard:
        host, port = args.dashboard.split(":")
        dashboard = DashboardFeed(host, int(port))
        log.info("Dashboard feed → %s:%s", host, port)

    # determine source
    use_sdr = args.freq is not None and not args.iq_file and not args.stdin_raw
    use_iq = args.iq_file is not None
    use_stdin = args.stdin_raw
    source = None

    if use_sdr:
        log.warning("SDR/IQ demodulation is PROTOTYPE ONLY (amplitude envelope). "
                     "It cannot decode LoRa, GFSK, or other phase/frequency modulations. "
                     "Use --stdin-raw with an external demodulator for validated capture.")
        source = SDRSource(
            args=args.sdr_args or "",
            freq=args.freq,
            sample_rate=args.sample_rate,
            bandwidth=args.bandwidth,
            gain=args.gain,
        )
        source.start()
        log.info("SDR started: freq=%.3f MHz, rate=%.3f MHz",
                 args.freq / 1e6, args.sample_rate / 1e6)
    elif use_iq:
        if not HAS_NUMPY:
            print("ERROR: numpy required for IQ file processing", file=sys.stderr)
            sys.exit(1)
        source = IQFileSource(args.iq_file, args.sample_rate)
        log.info("Reading IQ file: %s", args.iq_file)

    # protocol selection
    forced_protocol = args.protocol.lower() if args.protocol else None

    start_time = time.time()
    total_packets = 0
    last_display = 0

    shutdown = threading.Event()

    def handle_signal(sig, frame):
        shutdown.set()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("Capture started — Ctrl+C to stop")

    try:
        while not shutdown.is_set():
            raw_bytes = None

            if use_stdin:
                data = sys.stdin.buffer.read(4096)
                if not data:
                    break
                raw_bytes = data
            elif use_sdr or use_iq:
                samples = source.read()
                if samples is None:
                    if use_iq:
                        break
                    continue
                # PROTOTYPE: envelope_demod is amplitude-only and cannot
                # decode LoRa/GFSK. Use --stdin-raw for validated parsing.
                raw_bytes = envelope_demod(samples)
            else:
                print("ERROR: Specify --freq, --iq-file, or --stdin-raw",
                      file=sys.stderr)
                sys.exit(1)

            if not raw_bytes:
                continue

            # auto-detect if needed
            if not forced_protocol:
                detector.feed(raw_bytes)

            active = forced_protocol or detector.best_guess()
            decoded_list = []

            if active in ("crsf", "elrs"):
                decoded_list = crsf_dec.feed(raw_bytes)
            elif active == "mavlink":
                decoded_list = mav_dec.feed(raw_bytes)
            elif active == "frsky":
                decoded_list = frsky_dec.feed(raw_bytes)
            else:
                # try all decoders
                decoded_list.extend(crsf_dec.feed(raw_bytes))
                decoded_list.extend(mav_dec.feed(raw_bytes))
                decoded_list.extend(frsky_dec.feed(raw_bytes))

            for entry in decoded_list:
                total_packets += 1
                tracker.update(entry)
                if logger:
                    logger.log(entry)

            if dashboard:
                dashboard.maybe_send(tracker)

            # refresh display
            now = time.time()
            if now - last_display >= 0.5:
                last_display = now
                elapsed = now - start_time
                m, s = divmod(int(elapsed), 60)
                h, m = divmod(m, 60)
                stats = {
                    "uptime": f"{h}:{m:02d}:{s:02d}",
                    "total_packets": total_packets,
                }
                if not args.quiet:
                    render_display(tracker, stats)

    finally:
        if source and hasattr(source, "stop"):
            source.stop()
        if logger:
            logger.close()
            log.info("Wrote %d entries to %s", logger.count, logger.logfile)
        if dashboard:
            dashboard.close()

    # final summary
    print(f"\nCapture complete. {total_packets} packets decoded.", file=sys.stderr)
    if tracker.drones:
        print(f"Detected {len(tracker.drones)} drone(s):", file=sys.stderr)
        for did, drone in tracker.drones.items():
            print(f"  {did}: {drone.protocol}, {drone.packet_count} packets, "
                  f"armed={drone.armed}", file=sys.stderr)
    if logger:
        print(f"Log saved to: {logger.logfile}", file=sys.stderr)

    # write final snapshot
    if args.log_dir:
        snapshot_path = Path(args.log_dir) / "drone_snapshot.json"
        with open(snapshot_path, "w") as f:
            json.dump(tracker.snapshot(), f, indent=2, default=str)
        print(f"Drone snapshot: {snapshot_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Passive Telemetry Interceptor — silent drone recon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --freq 2.4e9 --protocol elrs
  %(prog)s --freq 915e6 --sample-rate 2e6
  %(prog)s --freq 433e6 --protocol mavlink --sdr-args "driver=uhd"
  %(prog)s --iq-file capture.raw --protocol crsf
  %(prog)s --stdin-raw --protocol mavlink
  %(prog)s --freq 2.4e9 --dashboard localhost:4200 --log-dir ./logs
""",
    )

    src = parser.add_argument_group("Radio source")
    src.add_argument("--freq", type=float, default=None,
                     help="Center frequency in Hz (e.g. 2.4e9, 915e6, 433e6)")
    src.add_argument("--sample-rate", type=float, default=2e6,
                     help="Sample rate in Hz (default: 2 MHz)")
    src.add_argument("--bandwidth", type=float, default=0,
                     help="RF bandwidth in Hz (default: auto)")
    src.add_argument("--gain", type=float, default=-1,
                     help="RX gain in dB (default: auto)")
    src.add_argument("--sdr-args", type=str, default=None,
                     help='SoapySDR device args (e.g. "driver=uhd,serial=...")')
    src.add_argument("--iq-file", type=str, default=None,
                     help="Read IQ samples from file (float32 interleaved)")
    src.add_argument("--stdin-raw", action="store_true",
                     help="Read raw protocol bytes from stdin (no demod)")

    proto = parser.add_argument_group("Protocol")
    proto.add_argument("--protocol", type=str, default=None,
                       choices=["crsf", "elrs", "mavlink", "frsky"],
                       help="Force protocol (default: auto-detect)")
    proto.add_argument("--rainbow-table", type=str, default=None,
                       help="Path to ELRS rainbow table JSON for binding phrase lookup")

    out = parser.add_argument_group("Output")
    out.add_argument("--log-dir", type=str, default=None,
                     help="Directory for JSON Lines telemetry logs")
    out.add_argument("--dashboard", type=str, default=None,
                     help="UDP target for recon dashboard (host:port)")
    out.add_argument("--quiet", "-q", action="store_true",
                     help="Suppress live terminal display")
    out.add_argument("--verbose", "-v", action="store_true",
                     help="Enable debug logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,
    )

    if not args.freq and not args.iq_file and not args.stdin_raw:
        parser.print_help()
        print("\nERROR: Specify --freq, --iq-file, or --stdin-raw", file=sys.stderr)
        sys.exit(1)

    if (args.freq or args.iq_file) and not HAS_NUMPY:
        print("ERROR: numpy is required for SDR/IQ input (pip install numpy)",
              file=sys.stderr)
        sys.exit(1)

    if args.freq and not args.iq_file and not HAS_SOAPY:
        print("ERROR: SoapySDR is required for live SDR input "
              "(pip install SoapySDR)", file=sys.stderr)
        sys.exit(1)

    run_capture(args)


if __name__ == "__main__":
    main()
