#!/usr/bin/env python3
"""
BLE Recon Scanner — Drone CTF Reconnaissance Tool

Scans for BLE devices and identifies drone-related peripherals, flight
controller configurators, and known vulnerable BLE interfaces. Enumerates
GATT services and characteristics, flagging unauthenticated write endpoints
that may be exploitable.

Requires: bleak (pip install bleak)
Platform: Linux (bluez), macOS, Windows

Usage:
    python3 ble-recon-scanner.py                    # passive scan, 10s
    python3 ble-recon-scanner.py -t 30              # scan for 30 seconds
    python3 ble-recon-scanner.py --enumerate         # connect and enumerate GATT on matches
    python3 ble-recon-scanner.py --enumerate --all   # enumerate ALL discovered devices
    python3 ble-recon-scanner.py -o results.json     # save results to JSON
    python3 ble-recon-scanner.py --known-only        # show only devices matching drone signatures

For authorized CTF research and security testing only.
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError:
    print("ERROR: bleak not installed. Run: pip install bleak", file=sys.stderr)
    sys.exit(1)


# --- Drone-related BLE name patterns ---
# Each entry: (substring_or_prefix, description, threat_notes)
DRONE_NAME_PATTERNS = [
    # DJI
    ("DJI", "DJI drone/controller", "CVE-2026-78306: unauthenticated command injection; CVE-2026-77812: WiFi cred leak over BLE"),
    ("RC-", "DJI remote controller", "May expose BLE command interface"),
    ("Mavic", "DJI Mavic series", "Check for unauthenticated BLE commands"),
    ("Mini", "Possible DJI Mini", "Check for DJI BLE vuln pattern"),
    ("Avata", "DJI Avata", "Check for unauthenticated BLE commands"),
    ("FPV-", "DJI FPV system", "Check for BLE command interface"),
    # Flight controller configurators
    ("SpeedyBee", "SpeedyBee BLE configurator", "BLE bridge to FC serial — may allow unauthenticated MSP/CLI access"),
    ("Betaflight", "Betaflight BLE configurator", "BLE-to-serial bridge — unauthenticated FC access"),
    ("INAV", "iNav BLE configurator", "BLE-to-serial bridge — unauthenticated FC access"),
    ("iNav", "iNav BLE configurator", "BLE-to-serial bridge — unauthenticated FC access"),
    ("BF-", "Possible Betaflight device", "Check for configurator BLE bridge"),
    # ESP32-based devices (common in DIY drones)
    ("ESP32", "ESP32 device", "CVE-2026-45542: protocomm heap overflow; CVE-2021-28139: BrakTooth LMP overflow"),
    ("ESP_", "ESP32/ESP-IDF device", "Check for BLE provisioning vulnerabilities"),
    ("PROV_", "ESP32 provisioning", "BLE provisioning endpoint — CVE-2026-45542 target"),
    # ELRS
    ("ELRS", "ExpressLRS device", "Check for WiFi update mode, binding phrase recovery"),
    ("ExpressLRS", "ExpressLRS TX/RX", "WiFi mode is an attack surface"),
    # Other drone platforms
    ("Tello", "Ryze/DJI Tello", "Known SDK command injection vectors"),
    ("CX-", "Cheerson drone", "Possible unauth BLE control"),
    ("JJRC", "JJRC drone", "Possible unauth BLE control"),
    ("Hubsan", "Hubsan drone", "Check BLE interface"),
    ("Eachine", "Eachine drone", "Check BLE interface"),
    ("EMAX", "EMAX drone/TX", "Check for ELRS WiFi mode"),
    # Companion apps / ground stations
    ("QGroundControl", "QGroundControl GCS", "Ground station — high-value target"),
    ("MissionPlanner", "Mission Planner GCS", "Ground station — high-value target"),
    ("FreePilot", "FreePilot GCS", "Ground station — high-value target"),
    # Generic drone indicators
    ("Drone", "Generic drone device", "Investigate further"),
    ("UAV", "UAV-related device", "Investigate further"),
    ("Flight", "Flight-related device", "Investigate further"),
    ("Copter", "Multicopter device", "Investigate further"),
]

# --- Known drone-related BLE service UUIDs ---
# Standard and vendor-specific UUIDs associated with drones and configurators
DRONE_SERVICE_UUIDS = {
    # Nordic UART Service — used by SpeedyBee, Betaflight BLE, iNav BLE
    "6e400001-b5a3-f393-e0a9-e50e24dcca9e": {
        "name": "Nordic UART Service (NUS)",
        "risk": "HIGH",
        "notes": "Serial bridge to flight controller. SpeedyBee/BF/iNav configurators use this for unauthenticated MSP access.",
    },
    # NUS TX characteristic (notifications from peripheral)
    "6e400003-b5a3-f393-e0a9-e50e24dcca9e": {
        "name": "NUS TX Characteristic",
        "risk": "MEDIUM",
        "notes": "Data FROM the flight controller — may leak telemetry, config, or CLI output.",
    },
    # NUS RX characteristic (write to peripheral)
    "6e400002-b5a3-f393-e0a9-e50e24dcca9e": {
        "name": "NUS RX Characteristic",
        "risk": "HIGH",
        "notes": "Write endpoint TO the flight controller. Can send MSP commands or CLI input.",
    },
    # ESP32 protocomm provisioning
    "021a9004-0382-4aea-bff4-6b3f1c5adfb4": {
        "name": "ESP32 Protocomm Provisioning",
        "risk": "HIGH",
        "notes": "BLE provisioning — CVE-2026-45542 heap overflow target.",
    },
    # DJI BLE service (observed in CVE-2026-78306 research)
    "00001800-0000-1000-8000-00805f9b34fb": {
        "name": "Generic Access",
        "risk": "INFO",
        "notes": "Standard GAP service — check device name for DJI pattern.",
    },
    "00001801-0000-1000-8000-00805f9b34fb": {
        "name": "Generic Attribute",
        "risk": "INFO",
        "notes": "Standard GATT service.",
    },
    # Device Information Service — useful for fingerprinting
    "0000180a-0000-1000-8000-00805f9b34fb": {
        "name": "Device Information Service",
        "risk": "INFO",
        "notes": "May reveal manufacturer, firmware version, hardware revision.",
    },
    # Battery Service
    "0000180f-0000-1000-8000-00805f9b34fb": {
        "name": "Battery Service",
        "risk": "INFO",
        "notes": "Battery level — confirms device is a battery-powered peripheral (drone/TX).",
    },
}

# Standard GATT characteristic UUIDs worth noting
INTERESTING_CHARACTERISTICS = {
    "00002a00-0000-1000-8000-00805f9b34fb": "Device Name",
    "00002a01-0000-1000-8000-00805f9b34fb": "Appearance",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision",
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer Name",
    "00002a19-0000-1000-8000-00805f9b34fb": "Battery Level",
}


@dataclass
class GattCharInfo:
    uuid: str
    handle: int
    properties: list[str]
    known_name: str | None = None
    value: str | None = None
    risk: str | None = None
    notes: str | None = None


@dataclass
class GattServiceInfo:
    uuid: str
    handle: int
    known_name: str | None = None
    risk: str | None = None
    notes: str | None = None
    characteristics: list[GattCharInfo] = field(default_factory=list)


@dataclass
class DeviceResult:
    address: str
    name: str | None
    rssi: int
    matched_pattern: str | None = None
    pattern_description: str | None = None
    threat_notes: str | None = None
    matched_services: list[str] = field(default_factory=list)
    service_uuids: list[str] = field(default_factory=list)
    manufacturer_data: dict = field(default_factory=dict)
    gatt_services: list[GattServiceInfo] = field(default_factory=list)
    enumerated: bool = False
    enum_error: str | None = None


def match_device_name(name: str | None) -> tuple[str, str, str] | None:
    if not name:
        return None
    name_upper = name.upper()
    for pattern, description, threat in DRONE_NAME_PATTERNS:
        if pattern.upper() in name_upper:
            return (pattern, description, threat)
    return None


def match_service_uuids(uuids: list[str]) -> list[dict]:
    matches = []
    for uuid in uuids:
        uuid_lower = uuid.lower()
        if uuid_lower in DRONE_SERVICE_UUIDS:
            matches.append({"uuid": uuid_lower, **DRONE_SERVICE_UUIDS[uuid_lower]})
    return matches


def is_interesting(device, adv_data) -> bool:
    if match_device_name(adv_data.local_name or device.name):
        return True
    if adv_data.service_uuids:
        for uuid in adv_data.service_uuids:
            if uuid.lower() in DRONE_SERVICE_UUIDS:
                return True
    return False


async def scan_devices(scan_time: float, known_only: bool) -> list[DeviceResult]:
    print(f"[*] Scanning for BLE devices ({scan_time}s)...", file=sys.stderr)
    devices = await BleakScanner.discover(timeout=scan_time, return_adv=True)
    results = []

    for device, adv_data in devices.values():
        name = adv_data.local_name or device.name
        if known_only and not is_interesting(device, adv_data):
            continue

        result = DeviceResult(
            address=device.address,
            name=name,
            rssi=adv_data.rssi,
            service_uuids=[str(u) for u in (adv_data.service_uuids or [])],
        )

        if adv_data.manufacturer_data:
            result.manufacturer_data = {
                str(k): v.hex() for k, v in adv_data.manufacturer_data.items()
            }

        name_match = match_device_name(name)
        if name_match:
            result.matched_pattern = name_match[0]
            result.pattern_description = name_match[1]
            result.threat_notes = name_match[2]

        svc_matches = match_service_uuids(adv_data.service_uuids or [])
        if svc_matches:
            result.matched_services = [m["name"] for m in svc_matches]
            if not result.threat_notes:
                high_risk = [m for m in svc_matches if m["risk"] == "HIGH"]
                if high_risk:
                    result.threat_notes = high_risk[0]["notes"]

        results.append(result)

    results.sort(key=lambda r: (
        0 if r.matched_pattern or r.matched_services else 1,
        r.rssi * -1,
    ))

    return results


async def enumerate_gatt(device: DeviceResult, timeout: float = 10.0) -> None:
    print(f"[*] Enumerating GATT on {device.address} ({device.name or 'unknown'})...", file=sys.stderr)
    try:
        async with BleakClient(device.address, timeout=timeout) as client:
            for service in client.services:
                svc_uuid = str(service.uuid).lower()
                svc_info = GattServiceInfo(
                    uuid=svc_uuid,
                    handle=service.handle,
                )

                if svc_uuid in DRONE_SERVICE_UUIDS:
                    db = DRONE_SERVICE_UUIDS[svc_uuid]
                    svc_info.known_name = db["name"]
                    svc_info.risk = db["risk"]
                    svc_info.notes = db["notes"]

                for char in service.characteristics:
                    char_uuid = str(char.uuid).lower()
                    props = char.properties

                    char_info = GattCharInfo(
                        uuid=char_uuid,
                        handle=char.handle,
                        properties=list(props),
                    )

                    if char_uuid in INTERESTING_CHARACTERISTICS:
                        char_info.known_name = INTERESTING_CHARACTERISTICS[char_uuid]

                    if char_uuid in DRONE_SERVICE_UUIDS:
                        db = DRONE_SERVICE_UUIDS[char_uuid]
                        char_info.known_name = db["name"]
                        char_info.risk = db["risk"]
                        char_info.notes = db["notes"]

                    if "write-without-response" in props or "write" in props:
                        if not char_info.risk:
                            char_info.risk = "MEDIUM"
                        if not char_info.notes:
                            char_info.notes = "Writable characteristic — test for unauthenticated access"

                    if "read" in props:
                        try:
                            raw = await client.read_gatt_char(char.uuid)
                            try:
                                char_info.value = raw.decode("utf-8", errors="replace")
                            except Exception:
                                char_info.value = raw.hex()
                        except Exception:
                            pass

                    svc_info.characteristics.append(char_info)

                device.gatt_services.append(svc_info)

        device.enumerated = True

    except BleakError as e:
        device.enum_error = str(e)
        print(f"[!] GATT enum failed for {device.address}: {e}", file=sys.stderr)
    except asyncio.TimeoutError:
        device.enum_error = "Connection timeout"
        print(f"[!] GATT enum timeout for {device.address}", file=sys.stderr)
    except Exception as e:
        device.enum_error = str(e)
        print(f"[!] GATT enum error for {device.address}: {e}", file=sys.stderr)


def print_results(results: list[DeviceResult]) -> None:
    drone_hits = [r for r in results if r.matched_pattern or r.matched_services]
    other = [r for r in results if not r.matched_pattern and not r.matched_services]

    print(f"\n{'='*70}")
    print(f"BLE Scan Results — {len(results)} devices found, {len(drone_hits)} drone-related")
    print(f"{'='*70}")

    if drone_hits:
        print(f"\n{'─'*70}")
        print("DRONE-RELATED DEVICES")
        print(f"{'─'*70}")
        for d in drone_hits:
            print(f"\n  [{d.rssi:>4d} dBm]  {d.address}  {d.name or '(no name)'}")
            if d.matched_pattern:
                print(f"            Pattern:  {d.matched_pattern} → {d.pattern_description}")
            if d.matched_services:
                print(f"            Services: {', '.join(d.matched_services)}")
            if d.threat_notes:
                print(f"            ⚠ THREAT: {d.threat_notes}")
            if d.service_uuids:
                print(f"            UUIDs:    {', '.join(d.service_uuids[:5])}")
            if d.manufacturer_data:
                for mid, data in d.manufacturer_data.items():
                    print(f"            Mfr {mid}: {data[:40]}")

            if d.enumerated and d.gatt_services:
                print(f"            {'─'*50}")
                print(f"            GATT Services ({len(d.gatt_services)}):")
                for svc in d.gatt_services:
                    label = svc.known_name or svc.uuid
                    risk_tag = f" [{svc.risk}]" if svc.risk and svc.risk != "INFO" else ""
                    print(f"              Service: {label}{risk_tag}")
                    if svc.notes:
                        print(f"                       {svc.notes}")
                    for ch in svc.characteristics:
                        props_str = ",".join(ch.properties)
                        ch_label = ch.known_name or ch.uuid
                        risk_tag = f" [{ch.risk}]" if ch.risk and ch.risk != "INFO" else ""
                        print(f"                Char: {ch_label} ({props_str}){risk_tag}")
                        if ch.value:
                            display = ch.value[:60]
                            print(f"                      Value: {display}")
                        if ch.notes:
                            print(f"                      ⚠ {ch.notes}")
            elif d.enum_error:
                print(f"            GATT enum error: {d.enum_error}")

    if other:
        print(f"\n{'─'*70}")
        print(f"OTHER DEVICES ({len(other)})")
        print(f"{'─'*70}")
        for d in other:
            extra = ""
            if d.enumerated and d.gatt_services:
                writable = sum(
                    1 for svc in d.gatt_services
                    for ch in svc.characteristics
                    if "write" in ch.properties or "write-without-response" in ch.properties
                )
                extra = f"  [{len(d.gatt_services)} services, {writable} writable chars]"
            print(f"  [{d.rssi:>4d} dBm]  {d.address}  {d.name or '(no name)'}{extra}")

    print(f"\n{'='*70}")


def save_results(results: list[DeviceResult], path: str) -> None:
    data = {
        "scan_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "device_count": len(results),
        "drone_related_count": sum(1 for r in results if r.matched_pattern or r.matched_services),
        "devices": [asdict(r) for r in results],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[*] Results saved to {path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="BLE Recon Scanner — identify drone-related BLE devices and vulnerable interfaces",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For authorized CTF research and security testing only.",
    )
    parser.add_argument("-t", "--time", type=float, default=10.0,
                        help="Scan duration in seconds (default: 10)")
    parser.add_argument("--enumerate", action="store_true",
                        help="Connect to matched devices and enumerate GATT services")
    parser.add_argument("--all", action="store_true",
                        help="With --enumerate, enumerate ALL devices (not just matches)")
    parser.add_argument("--known-only", action="store_true",
                        help="Show only devices matching drone signatures")
    parser.add_argument("-o", "--output", type=str,
                        help="Save results to JSON file")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    args = parser.parse_args()

    results = asyncio.run(scan_devices(args.time, args.known_only))

    if args.enumerate:
        targets = results if args.all else [
            r for r in results if r.matched_pattern or r.matched_services
        ]
        if targets:
            print(f"[*] Enumerating GATT on {len(targets)} device(s)...", file=sys.stderr)
            for device in targets:
                asyncio.run(enumerate_gatt(device))
        else:
            print("[*] No devices to enumerate.", file=sys.stderr)

    print_results(results)

    if args.output:
        save_results(results, args.output)

    drone_hits = [r for r in results if r.matched_pattern or r.matched_services]
    if drone_hits:
        high_risk = [
            r for r in drone_hits
            if any(
                svc.risk == "HIGH"
                for svc in r.gatt_services
            ) or (r.threat_notes and "CVE" in r.threat_notes)
        ]
        if high_risk:
            print(f"\n[!] {len(high_risk)} HIGH-RISK device(s) found — review threat notes above", file=sys.stderr)


if __name__ == "__main__":
    main()
