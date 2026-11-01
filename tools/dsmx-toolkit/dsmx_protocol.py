"""
DSMX Protocol Analysis Toolkit
Offline computational tools for Spektrum DSM2/DSMX protocol analysis.

Based on reverse-engineered protocol documentation from:
- DeviationTX project (doc/DSM.txt, src/protocol/dsm2_cyrf6936.c)
- DIY-Multiprotocol-TX-Module
- Morin & Cardoso, "Hacking the DSMx Drone RC Protocol" (GNURadio Conf 2021)

For authorized security research only.
"""

import struct
from typing import List, Tuple, Optional


# CYRF6936 PN Code Table (5 rows x 9 columns, 8 bytes each, stored LSB-first)
# From DeviationTX reverse engineering
PN_CODES = [
    # Row 0
    [
        bytes([0x83, 0xF7, 0xA8, 0x2D, 0x7A, 0x44, 0x64, 0xD3]),
        bytes([0x3F, 0x2C, 0x4E, 0xAA, 0x71, 0x48, 0x7A, 0xC9]),
        bytes([0x17, 0xFF, 0x9E, 0x21, 0x36, 0x90, 0xC7, 0x82]),
        bytes([0xBC, 0x5D, 0x9A, 0x5B, 0xEE, 0x7F, 0x42, 0xEB]),
        bytes([0x24, 0xF5, 0xDD, 0xF8, 0x7A, 0x77, 0x74, 0xE7]),
        bytes([0x3D, 0x70, 0x7C, 0x94, 0xDC, 0x84, 0xAD, 0x95]),
        bytes([0x1E, 0x6A, 0xF0, 0x37, 0x52, 0x7B, 0x11, 0xD4]),
        bytes([0x62, 0xF5, 0x2B, 0xAA, 0xFC, 0x33, 0xBF, 0xAF]),
        bytes([0x40, 0x56, 0x32, 0xD9, 0x0F, 0xD9, 0x5D, 0x97]),
    ],
    # Row 1
    [
        bytes([0xAB, 0xDC, 0xC1, 0x4D, 0xD6, 0xAD, 0x43, 0x40]),
        bytes([0x1C, 0x25, 0x79, 0xC6, 0xB8, 0xC8, 0x4A, 0x8F]),
        bytes([0x3E, 0x3A, 0xDB, 0x0C, 0xE8, 0xCF, 0xAA, 0xFE]),
        bytes([0x8E, 0x26, 0xC4, 0x4C, 0xD8, 0xB0, 0x76, 0x85]),
        bytes([0xF1, 0x3D, 0xAC, 0x04, 0xE1, 0x27, 0x8E, 0xC8]),
        bytes([0x56, 0x14, 0xE4, 0x51, 0x30, 0x36, 0x6F, 0xFF]),
        bytes([0xC5, 0xD0, 0x61, 0x7C, 0x49, 0xFD, 0x32, 0x22]),
        bytes([0x27, 0xBC, 0x40, 0x1F, 0x32, 0x35, 0x0C, 0x3E]),
        bytes([0x08, 0x2D, 0x7F, 0xA6, 0x5E, 0x7D, 0x1E, 0xDC]),
    ],
    # Row 2
    [
        bytes([0x7F, 0x56, 0x19, 0x7D, 0xE3, 0x47, 0x85, 0xC2]),
        bytes([0xA6, 0x2F, 0xEC, 0x76, 0xB4, 0x00, 0x6E, 0x2F]),
        bytes([0x05, 0xEB, 0x6D, 0x4E, 0x39, 0x65, 0x10, 0xF3]),
        bytes([0xD6, 0x6C, 0xB4, 0x66, 0xA5, 0xF1, 0xCB, 0x8E]),
        bytes([0x1A, 0xF6, 0x44, 0xE2, 0x34, 0x27, 0xF0, 0x70]),
        bytes([0xDE, 0xD7, 0xD3, 0xE6, 0x35, 0x44, 0x57, 0x13]),
        bytes([0x3E, 0x2A, 0xF6, 0x27, 0x95, 0x69, 0xAD, 0x7A]),
        bytes([0xB1, 0xF2, 0xB1, 0x09, 0xB1, 0xBD, 0x40, 0x2C]),
        bytes([0x2A, 0x0E, 0x60, 0xBF, 0x71, 0xC1, 0xB3, 0x33]),
    ],
    # Row 3
    [
        bytes([0x28, 0xBF, 0xCA, 0x56, 0x69, 0x5D, 0x4A, 0xBE]),
        bytes([0xA5, 0xBF, 0x23, 0x6B, 0x4D, 0x2A, 0xF0, 0xCE]),
        bytes([0x00, 0x29, 0x3F, 0x24, 0x73, 0xA3, 0x8E, 0x56]),
        bytes([0x22, 0x2C, 0xD5, 0x72, 0xBE, 0x36, 0x04, 0x6D]),
        bytes([0x34, 0xC0, 0x3A, 0xDE, 0xC4, 0x41, 0xCE, 0xAB]),
        bytes([0xCE, 0x44, 0x09, 0x4B, 0x17, 0x1E, 0xD4, 0x55]),
        bytes([0x5D, 0x11, 0x12, 0xE5, 0xBC, 0xF7, 0x08, 0xCE]),
        bytes([0x8B, 0x24, 0xA6, 0xF2, 0x23, 0x65, 0x75, 0x8A]),
        bytes([0xEB, 0xCE, 0x11, 0x17, 0x4F, 0x4F, 0x86, 0xE3]),
    ],
    # Row 4
    [
        bytes([0xDF, 0x26, 0x5F, 0x7C, 0x5A, 0x5D, 0xA1, 0x67]),
        bytes([0x0B, 0x25, 0x49, 0x10, 0x2A, 0xF6, 0xF9, 0xD1]),
        bytes([0x13, 0xA0, 0xF8, 0x80, 0x1C, 0x2E, 0x08, 0x46]),
        bytes([0xE5, 0x68, 0xD5, 0x15, 0x39, 0x5D, 0x21, 0x48]),
        bytes([0xD2, 0xEA, 0xC2, 0xBD, 0x43, 0x63, 0xFC, 0xF6]),
        bytes([0x94, 0x2D, 0xA1, 0x4B, 0xA5, 0xB5, 0xB8, 0xF0]),
        bytes([0xF5, 0xB0, 0xC2, 0x0C, 0x62, 0xA6, 0x41, 0x73]),
        bytes([0x49, 0xE5, 0x48, 0x54, 0x57, 0xBD, 0x71, 0x57]),
        bytes([0x07, 0xB0, 0x21, 0x27, 0xDB, 0xFC, 0xBF, 0x2B]),
    ],
]

# Bind DATA code (second 16 bytes, fixed)
BIND_DATA_CODE_SUFFIX = bytes([
    0xD7, 0xA1, 0x54, 0xB1, 0x5E, 0x89, 0xAE, 0x86,
    0x0B, 0x57, 0xD6, 0xA8, 0xA8, 0xD3, 0xA3, 0x86,
])

# TX capability codes
TX_CAPABILITIES = {
    0x01: "HP6DSM (DSM2 6ch)",
    0x02: "DM9 (DSM2 9ch)",
    0x12: "DX8 (DSM2 8ch)",
    0xA2: "DX6i (DSMX 6ch)",
    0xB2: "DX8 (DSMX 8ch)",
}

# Channel names
CHANNEL_NAMES = {
    0: "Throttle",
    1: "Aileron",
    2: "Elevator",
    3: "Rudder",
    4: "Gear",
    5: "Aux1",
    6: "Aux2",
    7: "Aux3",
    8: "Aux4",
    9: "Aux5",
    10: "Aux6",
    11: "Aux7",
}


def generate_dsmx_hopping_sequence(mfg_id: bytes, model_num: int = 0) -> List[int]:
    """
    Generate the 23-channel DSMX frequency hopping sequence.

    The sequence is deterministic from the CYRF6936 MFG ID + model number.
    This is the core vulnerability: knowing the MFG ID means knowing
    exactly where the transmitter will hop at every point in time.

    Args:
        mfg_id: 4-byte CYRF6936 manufacturer ID
        model_num: Model/fixed ID number (usually 0 for default)

    Returns:
        List of 23 channel indices (range 3-75)
    """
    assert len(mfg_id) == 4, "MFG ID must be 4 bytes"

    id_value = int.from_bytes(mfg_id, 'little') + model_num
    id_tmp = (~id_value) & 0xFFFFFFFF

    id_parity = id_tmp & 0x01

    channels: List[int] = []
    count_band1 = 0  # channels 3-27, max 8
    count_band2 = 0  # channels 28-51, max 7
    count_band3 = 0  # channels 52-75, max 8

    while len(channels) < 23:
        id_tmp = (id_tmp * 0x0019660D + 0x3C6EF35F) & 0xFFFFFFFF
        next_ch = ((id_tmp >> 8) % 0x49) + 3

        if (next_ch & 0x01) != id_parity:
            continue
        if next_ch in channels:
            continue
        if 3 <= next_ch <= 27 and count_band1 >= 8:
            continue
        if 28 <= next_ch <= 51 and count_band2 >= 7:
            continue
        if next_ch >= 52 and count_band3 >= 8:
            continue

        channels.append(next_ch)
        if 3 <= next_ch <= 27:
            count_band1 += 1
        elif 28 <= next_ch <= 51:
            count_band2 += 1
        else:
            count_band3 += 1

    return channels


def get_sop_data_codes(mfg_id: bytes, channel_idx: int) -> Tuple[bytes, bytes, bytes]:
    """
    Get the SOP code and two DATA codes for a given channel.

    These DSSS codes are required to decode/encode frames on any channel.
    They are entirely determined by the MFG ID and channel index.

    Args:
        mfg_id: 4-byte CYRF6936 manufacturer ID
        channel_idx: Current channel index in the hopping sequence

    Returns:
        Tuple of (sop_code, data_code_0, data_code_1), each 8 bytes
    """
    row = channel_idx % 5
    sop_col = (mfg_id[0] + mfg_id[1] + mfg_id[2] + 2) & 0x07
    data_col0 = 7 - sop_col
    data_col1 = data_col0 + 1

    return (
        PN_CODES[row][sop_col],
        PN_CODES[row][data_col0],
        PN_CODES[row][data_col1],
    )


def get_crc_seed(mfg_id: bytes, is_channel_b: bool = False) -> int:
    """
    Get the CRC seed for a packet.

    Channel 'a' uses NOT(mfg_id_value), channel 'b' uses mfg_id_value.

    Args:
        mfg_id: 4-byte CYRF6936 manufacturer ID
        is_channel_b: True for channel 'b' (second transmission)

    Returns:
        16-bit CRC seed
    """
    mfg_value = (mfg_id[1] << 8) | mfg_id[0]
    if is_channel_b:
        return mfg_value
    return (~mfg_value) & 0xFFFF


def build_packet_header(mfg_id: bytes, is_dsmx: bool = True) -> bytes:
    """
    Build the 2-byte packet header from MFG ID.

    For DSMX: direct MFG ID bytes (cleartext ID in every frame!)
    For DSM2: inverted MFG ID bytes
    """
    if is_dsmx:
        return bytes([mfg_id[2], mfg_id[3]])
    else:
        return bytes([0xFF - mfg_id[2], 0xFF - mfg_id[3]])


def encode_channel_value(channel_num: int, value: int, is_11bit: bool = True,
                         is_second_packet: bool = False) -> int:
    """
    Encode a single RC channel value into a 16-bit word.

    Format: [1-bit flag][4-bit channel][11-bit value] (or 10-bit for DSM2 6ch)
    """
    if is_11bit:
        flag = 1 if (is_second_packet and channel_num == 0) else 0
        return (flag << 15) | (channel_num << 11) | (value & 0x7FF)
    else:
        flag = 1 if (is_second_packet and channel_num == 0) else 0
        return (flag << 15) | (channel_num << 10) | (value & 0x3FF)


def decode_channel_value(word: int, is_11bit: bool = True) -> Tuple[int, int, int]:
    """
    Decode a 16-bit channel word.

    Returns: (flag, channel_number, value)
    """
    if word == 0xFFFF:
        return (-1, -1, -1)  # Unused slot

    flag = (word >> 15) & 0x01
    if is_11bit:
        channel = (word >> 11) & 0x0F
        value = word & 0x7FF
    else:
        channel = (word >> 10) & 0x0F
        value = word & 0x3FF

    return (flag, channel, value)


def build_bind_packet(mfg_id: bytes, channel_count: int = 7,
                      capability: int = 0xA2, is_dsmx: bool = True) -> bytes:
    """
    Build a DSMX/DSM2 bind packet.

    This is what a transmitter broadcasts during binding. Capturing this
    reveals the full MFG ID.
    """
    if is_dsmx:
        b0, b1, b2, b3 = mfg_id[0], mfg_id[1], mfg_id[2], mfg_id[3]
    else:
        b0 = 0xFF - mfg_id[0]
        b1 = 0xFF - mfg_id[1]
        b2 = 0xFF - mfg_id[2]
        b3 = 0xFF - mfg_id[3]

    packet_count = 1 if channel_count <= 7 else 2

    # First 8 bytes
    first_half = bytes([b0, b1, b2, b3, 0x00, 0x00, 0x01, channel_count])

    # Checksum 1: 384 + sum of bytes 0-7
    cksum1 = 384 + sum(first_half)
    first_half = bytearray(first_half)
    first_half[4] = (cksum1 >> 8) & 0xFF
    first_half[5] = cksum1 & 0xFF

    # Second part
    second_half = bytearray([capability, packet_count, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    # Checksum 2: 0xEEEE + sum of bytes 8-13
    cksum2 = 0xEEEE + sum(second_half[:6])
    second_half[2] = (cksum2 >> 8) & 0xFF
    second_half[3] = cksum2 & 0xFF

    return bytes(first_half) + bytes(second_half)


def brute_force_mfg_id(known_bytes_2_3: Tuple[int, int],
                       observed_channels: List[int],
                       model_num: int = 0) -> List[bytes]:
    """
    Brute-force the unknown first 2 bytes of a 4-byte MFG ID.

    Given bytes [2] and [3] (visible in DSMX frame headers) and an observed
    subset of the hopping sequence, try all 65,536 possibilities for
    bytes [0] and [1].

    Args:
        known_bytes_2_3: Tuple of (byte_2, byte_3) from captured frame headers
        observed_channels: List of observed channel indices (partial hop sequence)
        model_num: Model number (usually 0)

    Returns:
        List of candidate 4-byte MFG IDs that produce hopping sequences
        matching the observed channels
    """
    candidates = []
    observed_set = set(observed_channels)

    for b0 in range(256):
        for b1 in range(256):
            mfg_id = bytes([b0, b1, known_bytes_2_3[0], known_bytes_2_3[1]])
            hop_seq = generate_dsmx_hopping_sequence(mfg_id, model_num)
            hop_set = set(hop_seq)

            if observed_set.issubset(hop_set):
                candidates.append(mfg_id)

    return candidates


def channel_to_frequency_mhz(channel_idx: int) -> float:
    """
    Convert a DSMX channel index to approximate RF frequency in MHz.

    CYRF6936 channels are spaced 1 MHz apart starting at ~2.404 GHz.
    Channel index 0 ≈ 2.404 GHz.
    """
    return 2404.0 + channel_idx


def analyze_mfg_id(mfg_id: bytes, model_num: int = 0) -> dict:
    """
    Complete analysis of a target given its MFG ID.

    Returns all protocol parameters needed for interception or injection.
    """
    hop_seq = generate_dsmx_hopping_sequence(mfg_id, model_num)

    hop_freqs = [channel_to_frequency_mhz(ch) for ch in hop_seq]

    codes_per_channel = {}
    for i, ch in enumerate(hop_seq):
        sop, data0, data1 = get_sop_data_codes(mfg_id, ch)
        codes_per_channel[i] = {
            'channel_idx': ch,
            'frequency_mhz': channel_to_frequency_mhz(ch),
            'sop_code': sop.hex(),
            'data_code_0': data0.hex(),
            'data_code_1': data1.hex(),
        }

    crc_a = get_crc_seed(mfg_id, is_channel_b=False)
    crc_b = get_crc_seed(mfg_id, is_channel_b=True)

    header = build_packet_header(mfg_id, is_dsmx=True)

    bind_pkt = build_bind_packet(mfg_id)

    return {
        'mfg_id': mfg_id.hex(),
        'mfg_id_bytes': list(mfg_id),
        'hopping_sequence': hop_seq,
        'hopping_frequencies_mhz': hop_freqs,
        'hop_count': len(hop_seq),
        'crc_seed_channel_a': f"0x{crc_a:04X}",
        'crc_seed_channel_b': f"0x{crc_b:04X}",
        'frame_header': header.hex(),
        'bind_packet': bind_pkt.hex(),
        'codes_per_channel': codes_per_channel,
        'timing': {
            'frame_period_ms': 22,
            'dual_channel_delay_ms': 4,
            'hop_window_ms': 11,
        },
    }


def print_target_analysis(mfg_id: bytes, model_num: int = 0):
    """Print a human-readable analysis of a DSMX target."""
    analysis = analyze_mfg_id(mfg_id, model_num)

    print(f"{'='*70}")
    print(f"DSMX Target Analysis")
    print(f"{'='*70}")
    print(f"MFG ID:           {analysis['mfg_id']} ({analysis['mfg_id_bytes']})")
    print(f"Frame Header:     {analysis['frame_header']}")
    print(f"CRC Seed (ch a):  {analysis['crc_seed_channel_a']}")
    print(f"CRC Seed (ch b):  {analysis['crc_seed_channel_b']}")
    print()
    print(f"Hopping Sequence ({analysis['hop_count']} channels):")
    print(f"  Indices:     {analysis['hopping_sequence']}")
    print(f"  Frequencies: {[f'{f:.0f}' for f in analysis['hopping_frequencies_mhz']]} MHz")
    print()
    print(f"Timing:")
    for k, v in analysis['timing'].items():
        print(f"  {k}: {v}")
    print()
    print(f"Bind Packet: {analysis['bind_packet']}")
    print()
    print(f"Per-Channel Codes (first 5 of 23):")
    for i in range(min(5, len(analysis['codes_per_channel']))):
        info = analysis['codes_per_channel'][i]
        print(f"  Hop {i:2d} → CH {info['channel_idx']:2d} ({info['frequency_mhz']:.0f} MHz)")
        print(f"         SOP:   {info['sop_code']}")
        print(f"         DATA0: {info['data_code_0']}")
        print(f"         DATA1: {info['data_code_1']}")


if __name__ == '__main__':
    print("DSMX Protocol Analysis Toolkit")
    print("=" * 70)
    print()

    # Example: analyze a hypothetical target with MFG ID 0xA1B2C3D4
    example_id = bytes([0xA1, 0xB2, 0xC3, 0xD4])
    print_target_analysis(example_id)

    print()
    print("=" * 70)
    print("MFG ID Brute Force Demo")
    print("=" * 70)
    print()

    # Demonstrate brute force: given bytes [2,3] and a few observed channels
    known_2_3 = (example_id[2], example_id[3])
    full_seq = generate_dsmx_hopping_sequence(example_id)
    observed = full_seq[:5]  # Pretend we observed first 5 channels

    print(f"Known header bytes: {known_2_3}")
    print(f"Observed channels:  {observed}")
    print(f"Searching 65,536 candidates...")

    candidates = brute_force_mfg_id(known_2_3, observed)
    print(f"Found {len(candidates)} candidate(s):")
    for c in candidates[:10]:
        print(f"  {c.hex()} - match: {c == example_id}")
