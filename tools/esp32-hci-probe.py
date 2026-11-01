#!/usr/bin/env python3
"""
ESP32 Hidden HCI Command Probe — CVE-2025-27840 Detection Tool

Probes an ESP32 Bluetooth controller for the presence of undocumented
vendor-specific HCI commands (OGF 0x3F) disclosed in CVE-2025-27840.

This tool is READ-ONLY: it issues only diagnostic/read commands and
never writes to memory, flash, or configuration. It can detect whether
the hidden commands are present (unpatched) or rejected (patched).

Transport options:
  - UART HCI (direct serial to ESP32 controller, typically 115200 or 921600)
  - USB HCI (ESP32 in USB Bluetooth mode)

Requires: pyserial (pip install pyserial)

Usage:
    python3 esp32-hci-probe.py --port /dev/ttyUSB0          # UART probe
    python3 esp32-hci-probe.py --port /dev/ttyUSB0 --baud 921600
    python3 esp32-hci-probe.py --port COM4 --detect-only     # just check, no read
    python3 esp32-hci-probe.py --simulate                     # demo mode, no hardware

For authorized CTF research and security testing only.
"""

import argparse
import struct
import sys
import time
from dataclasses import dataclass
from typing import Optional

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


# --- HCI Constants ---
HCI_COMMAND_PKT = 0x01
HCI_EVENT_PKT = 0x04
HCI_COMMAND_COMPLETE_EVT = 0x0E

# Vendor OGF
VENDOR_OGF = 0x3F

# Hidden command OCFs (CVE-2025-27840)
OCF_READ_MEMORY = 0x01
OCF_WRITE_MEMORY = 0x02
OCF_DELETE_NVDS = 0x03
OCF_GET_FLASH_ID = 0x05
OCF_ERASE_FLASH = 0x06
OCF_WRITE_FLASH = 0x07
OCF_READ_FLASH = 0x08
OCF_READ_NVDS = 0x09
OCF_WRITE_NVDS = 0x0A
OCF_COEX_ENABLE = 0x0B
OCF_SEND_LMP = 0x0E
OCF_READ_KERNEL_STATS = 0x10
OCF_PLATFORM_RESET = 0x11
OCF_READ_MEMORY_INFO = 0x12
OCF_REGISTER_READ = 0x30
OCF_REGISTER_WRITE = 0x31
OCF_SET_MAC_ADDR = 0x32
OCF_SET_CRC_INIT = 0x35
OCF_LLCP_DISCARD = 0x36
OCF_RESET_RX_COUNT = 0x37
OCF_RESET_TX_COUNT = 0x38
OCF_RF_REG_READ = 0x39
OCF_RF_REG_WRITE = 0x3A
OCF_SET_TX_PASSWORD = 0x3B
OCF_SET_LE_PARAMS = 0x40
OCF_WRITE_LE_DEFAULTS = 0x41
OCF_LLCP_PASSTHROUGH = 0x42
OCF_SEND_LLCP = 0x43
OCF_LMP_DISCARD = 0x44

COMMAND_NAMES = {
    OCF_READ_MEMORY: "Read Memory",
    OCF_WRITE_MEMORY: "Write Memory",
    OCF_DELETE_NVDS: "Delete NVDS Parameter",
    OCF_GET_FLASH_ID: "Get Flash ID",
    OCF_ERASE_FLASH: "Erase Flash",
    OCF_WRITE_FLASH: "Write Flash",
    OCF_READ_FLASH: "Read Flash",
    OCF_READ_NVDS: "Read NVDS Parameter",
    OCF_WRITE_NVDS: "Write NVDS Parameter",
    OCF_COEX_ENABLE: "Enable/Disable Coexistence",
    OCF_SEND_LMP: "Send LMP Packet",
    OCF_READ_KERNEL_STATS: "Read Kernel Stats",
    OCF_PLATFORM_RESET: "Platform Reset",
    OCF_READ_MEMORY_INFO: "Read Memory Info",
    OCF_REGISTER_READ: "Register Read",
    OCF_REGISTER_WRITE: "Register Write",
    OCF_SET_MAC_ADDR: "Set MAC Address",
    OCF_SET_CRC_INIT: "Set CRC Initial Value",
    OCF_LLCP_DISCARD: "LLCP Messages Discard",
    OCF_RESET_RX_COUNT: "Reset RX Count",
    OCF_RESET_TX_COUNT: "Reset TX Count",
    OCF_RF_REG_READ: "RF Register Read",
    OCF_RF_REG_WRITE: "RF Register Write",
    OCF_SET_TX_PASSWORD: "Set TX Password",
    OCF_SET_LE_PARAMS: "Set LE Parameters",
    OCF_WRITE_LE_DEFAULTS: "Write LE Default Values",
    OCF_LLCP_PASSTHROUGH: "LLCP Pass Through Enable",
    OCF_SEND_LLCP: "Send LLCP Packet",
    OCF_LMP_DISCARD: "LMP Messages Discard",
}

# Safe read-only probes (never write/erase/reset)
SAFE_PROBE_OCFS = [
    OCF_READ_MEMORY_INFO,
    OCF_GET_FLASH_ID,
    OCF_READ_KERNEL_STATS,
]

# HCI error codes
HCI_SUCCESS = 0x00
HCI_UNKNOWN_COMMAND = 0x01
HCI_INVALID_PARAMS = 0x12


def make_opcode(ogf: int, ocf: int) -> int:
    return (ogf << 10) | ocf


def build_hci_command(ocf: int, params: bytes = b"") -> bytes:
    opcode = make_opcode(VENDOR_OGF, ocf)
    return struct.pack("<BHB", HCI_COMMAND_PKT, opcode, len(params)) + params


def build_read_memory_cmd(address: int, access_size: int = 8, count: int = 1) -> bytes:
    params = struct.pack("<IBB", address, access_size, count)
    return build_hci_command(OCF_READ_MEMORY, params)


def build_read_flash_cmd(address: int, length: int) -> bytes:
    params = struct.pack("<II", address, length)
    return build_hci_command(OCF_READ_FLASH, params)


@dataclass
class HciEvent:
    event_code: int
    length: int
    data: bytes

    @property
    def is_command_complete(self) -> bool:
        return self.event_code == HCI_COMMAND_COMPLETE_EVT

    @property
    def status(self) -> Optional[int]:
        if self.is_command_complete and len(self.data) >= 4:
            return self.data[3]
        return None

    @property
    def return_opcode(self) -> Optional[int]:
        if self.is_command_complete and len(self.data) >= 3:
            return struct.unpack_from("<H", self.data, 1)[0]
        return None

    @property
    def return_data(self) -> bytes:
        if self.is_command_complete and len(self.data) > 4:
            return self.data[4:]
        return b""


class HciTransport:
    """UART-based HCI transport for ESP32."""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 2.0):
        if not HAS_SERIAL:
            raise RuntimeError("pyserial not installed (pip install pyserial)")
        self.ser = serial.Serial(port, baud, timeout=timeout)
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def send(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def recv_event(self, timeout: float = 2.0) -> Optional[HciEvent]:
        self.ser.timeout = timeout
        header = self.ser.read(1)
        if not header or header[0] != HCI_EVENT_PKT:
            return None
        evt_hdr = self.ser.read(2)
        if len(evt_hdr) < 2:
            return None
        event_code = evt_hdr[0]
        param_len = evt_hdr[1]
        params = self.ser.read(param_len) if param_len > 0 else b""
        return HciEvent(event_code, param_len, params)

    def close(self) -> None:
        self.ser.close()


class SimulatedTransport:
    """Simulated HCI transport for demo/testing without hardware."""

    def __init__(self, vulnerable: bool = True):
        self.vulnerable = vulnerable
        self._last_opcode = 0

    def send(self, data: bytes) -> None:
        if len(data) >= 4 and data[0] == HCI_COMMAND_PKT:
            self._last_opcode = struct.unpack_from("<H", data, 1)[0]

    def recv_event(self, timeout: float = 2.0) -> Optional[HciEvent]:
        if self.vulnerable:
            status = HCI_SUCCESS
            extra = b"\x00" * 8
        else:
            status = HCI_UNKNOWN_COMMAND
            extra = b""

        data = struct.pack("<BHB", 1, self._last_opcode, status) + extra
        return HciEvent(HCI_COMMAND_COMPLETE_EVT, len(data), data)

    def close(self) -> None:
        pass


def probe_vendor_commands(transport, detect_only: bool = False, verbose: bool = False) -> dict:
    """Probe for CVE-2025-27840 hidden HCI commands. Read-only."""
    results = {
        "vulnerable": False,
        "commands_found": [],
        "commands_rejected": [],
        "errors": [],
        "memory_info": None,
        "flash_id": None,
    }

    print("\n[*] Probing ESP32 for hidden vendor HCI commands (CVE-2025-27840)")
    print("[*] Using SAFE read-only probes only\n")

    for ocf in SAFE_PROBE_OCFS:
        name = COMMAND_NAMES.get(ocf, f"Unknown (0x{ocf:02X})")
        opcode = make_opcode(VENDOR_OGF, ocf)
        cmd = build_hci_command(ocf)

        if verbose:
            print(f"  TX: {cmd.hex()}")

        transport.send(cmd)
        evt = transport.recv_event(timeout=2.0)

        if evt is None:
            print(f"  [?] 0xFC{ocf:02X} {name}: No response (timeout)")
            results["errors"].append({"ocf": ocf, "name": name, "error": "timeout"})
            continue

        if not evt.is_command_complete:
            print(f"  [?] 0xFC{ocf:02X} {name}: Unexpected event 0x{evt.event_code:02X}")
            results["errors"].append({"ocf": ocf, "name": name, "error": f"unexpected event 0x{evt.event_code:02X}"})
            continue

        status = evt.status
        if status == HCI_SUCCESS:
            print(f"  [!] 0xFC{ocf:02X} {name}: SUPPORTED (vulnerable)")
            results["commands_found"].append({"ocf": ocf, "name": name, "data": evt.return_data.hex()})
            results["vulnerable"] = True

            if ocf == OCF_READ_MEMORY_INFO:
                results["memory_info"] = evt.return_data.hex()
            elif ocf == OCF_GET_FLASH_ID:
                results["flash_id"] = evt.return_data.hex()
        elif status == HCI_UNKNOWN_COMMAND:
            print(f"  [+] 0xFC{ocf:02X} {name}: REJECTED (patched or not ESP32)")
            results["commands_rejected"].append({"ocf": ocf, "name": name})
        else:
            print(f"  [?] 0xFC{ocf:02X} {name}: Status 0x{status:02X}")
            results["errors"].append({"ocf": ocf, "name": name, "error": f"status 0x{status:02X}"})

    if results["vulnerable"] and not detect_only:
        print("\n[*] Hidden commands confirmed. Reading memory info...")
        if results["memory_info"]:
            print(f"    Memory info: {results['memory_info']}")
        if results["flash_id"]:
            print(f"    Flash ID: {results['flash_id']}")

    print("\n" + "=" * 60)
    if results["vulnerable"]:
        print("[!!] CVE-2025-27840: ESP32 IS VULNERABLE")
        print(f"     {len(results['commands_found'])} hidden commands accepted")
        print("     Arbitrary memory/flash read is possible via HCI")
    elif results["commands_rejected"]:
        print("[OK] ESP32 commands rejected — likely patched or not original ESP32")
    else:
        print("[??] Inconclusive — no valid responses received")
        print("     Device may not be ESP32, or HCI transport is not connected")
    print("=" * 60)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="ESP32 Hidden HCI Command Probe (CVE-2025-27840)",
        epilog="For authorized CTF research and security testing only.",
    )
    parser.add_argument("--port", help="Serial port for HCI UART (e.g., /dev/ttyUSB0, COM4)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--detect-only", action="store_true", help="Only detect presence, don't read data")
    parser.add_argument("--simulate", action="store_true", help="Run in simulation mode (no hardware)")
    parser.add_argument("--simulate-patched", action="store_true", help="Simulate a patched device")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show raw HCI packets")
    parser.add_argument("-o", "--output", help="Save results to JSON file")
    args = parser.parse_args()

    if not args.simulate and not args.port:
        parser.error("--port is required unless --simulate is used")

    if args.simulate:
        transport = SimulatedTransport(vulnerable=not args.simulate_patched)
        print("[*] Running in SIMULATION mode")
    else:
        if not HAS_SERIAL:
            print("ERROR: pyserial not installed. Run: pip install pyserial", file=sys.stderr)
            sys.exit(1)
        try:
            transport = HciTransport(args.port, args.baud)
            print(f"[*] Connected to {args.port} at {args.baud} baud")
        except serial.SerialException as e:
            print(f"ERROR: Cannot open {args.port}: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        results = probe_vendor_commands(transport, args.detect_only, args.verbose)
    finally:
        transport.close()

    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n[*] Results saved to {args.output}")

    sys.exit(0 if not results["vulnerable"] else 2)


if __name__ == "__main__":
    main()
