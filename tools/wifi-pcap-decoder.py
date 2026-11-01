#!/usr/bin/env python3
"""
WiFi pcap decoder — Extract and characterize WiFi frames from pcap/pcapng captures.

Parses 802.11 captures looking for drone-related and ESP-related traffic:
  - Beacon / probe request / probe response (SSIDs, OUIs, channels)
  - ESP-NOW vendor-specific action frames (Espressif OUI 18:fe:34 / 24:0a:c4)
  - ELRS WiFi Backpack SSIDs (ExpressLRS TX/RX, ELRS_*)
  - Association / authentication / deauth frames
  - Data frames with OUI breakdown
  - Vendor identification via IEEE OUI database

Requires: scapy (pip install scapy)

Usage:
    python3 wifi-pcap-decoder.py capture.pcap
    python3 wifi-pcap-decoder.py capture.pcapng --json
    python3 wifi-pcap-decoder.py capture.pcap --json --output decoded.jsonl
    python3 wifi-pcap-decoder.py capture.pcap --verbose
    python3 wifi-pcap-decoder.py capture.pcap --filter esp-now
    python3 wifi-pcap-decoder.py capture.pcap --filter beacon
    python3 wifi-pcap-decoder.py capture.pcap --summary-only

For authorized security research and CTF use only.
"""

import argparse
import json

# Suppress scapy's verbose startup
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logging.getLogger("scapy.runtime").setLevel(logging.ERROR)

from scapy.all import (
    Dot11,
    Dot11Beacon,
    Dot11Elt,
    Dot11ProbeResp,
    PcapNgReader,
    PcapReader,
    RadioTap,
    conf,
    raw,
)

conf.verb = 0

# --- OUI database (Espressif + common drone vendors) ---

KNOWN_OUIS: dict[str, str] = {
    "18:fe:34": "Espressif Inc.",
    "24:0a:c4": "Espressif Inc.",
    "30:ae:a4": "Espressif Inc.",
    "a4:cf:12": "Espressif Inc.",
    "ac:67:b2": "Espressif Inc.",
    "bc:dd:c2": "Espressif Inc.",
    "c4:4f:33": "Espressif Inc.",
    "cc:50:e3": "Espressif Inc.",
    "dc:4f:22": "Espressif Inc.",
    "e0:98:06": "Espressif Inc.",
    "ec:fa:bc": "Espressif Inc.",
    "08:3a:f2": "Espressif Inc.",
    "08:b6:1f": "Espressif Inc.",
    "10:52:1c": "Espressif Inc.",
    "34:85:18": "Espressif Inc.",
    "34:86:5d": "Espressif Inc.",
    "40:f5:20": "Espressif Inc.",
    "48:27:e2": "Espressif Inc.",
    "48:3f:da": "Espressif Inc.",
    "54:32:04": "Espressif Inc.",
    "70:04:1d": "Espressif Inc.",
    "7c:df:a1": "Espressif Inc.",
    "80:65:99": "Espressif Inc.",
    "84:0d:8e": "Espressif Inc.",
    "84:cc:a8": "Espressif Inc.",
    "84:f3:eb": "Espressif Inc.",
    "94:b5:55": "Espressif Inc.",
    "94:b9:7e": "Espressif Inc.",
    "a0:20:a6": "Espressif Inc.",
    "a4:e5:7c": "Espressif Inc.",
    "b4:e6:2d": "Espressif Inc.",
    "c8:c9:a3": "Espressif Inc.",
    "d8:bf:c0": "Espressif Inc.",
    "d8:13:2a": "Espressif Inc.",
    "f0:08:d1": "Espressif Inc.",
    "f4:12:fa": "Espressif Inc.",
    "f4:cf:a2": "Espressif Inc.",
    # DJI
    "60:60:1f": "DJI Technology",
    "34:d2:62": "DJI Technology",
    # Parrot
    "90:03:b7": "Parrot SA",
    "a0:14:3d": "Parrot SA",
    # Yuneec
    "e4:90:7e": "Yuneec",
    # 3DR / ArduPilot common
    "b8:27:eb": "Raspberry Pi (companion computer)",
    # TBS / Betaflight configurator
    "0a:0a:0a": "TBS (local-admin)",
}

# ELRS WiFi Backpack SSID patterns
import re

ELRS_SSID_PATTERNS = [
    re.compile(r"^ExpressLRS\s*(TX|RX)$", re.IGNORECASE),
    re.compile(r"^ELRS[_\s]", re.IGNORECASE),
    re.compile(r"^expresslrs", re.IGNORECASE),
    re.compile(r"^elrs_backpack", re.IGNORECASE),
]

DRONE_SSID_PATTERNS = [
    re.compile(r"^TELLO-[A-F0-9]{6}$", re.IGNORECASE),
    re.compile(r"^Bebop2?-", re.IGNORECASE),
    re.compile(r"^ANAFI-", re.IGNORECASE),
    re.compile(r"^Disco-", re.IGNORECASE),
    re.compile(r"^DJI[_-]", re.IGNORECASE),
    re.compile(r"^Skydio", re.IGNORECASE),
    re.compile(r"^FPV[_-]", re.IGNORECASE),
    re.compile(r"^ArduPilot", re.IGNORECASE),
    re.compile(r"^APM[_-]", re.IGNORECASE),
    re.compile(r"^PX4[_-]", re.IGNORECASE),
    re.compile(r"^MAVLINK", re.IGNORECASE),
    re.compile(r"^Mambo-", re.IGNORECASE),
    re.compile(r"^Swing-", re.IGNORECASE),
    re.compile(r"^3DR[_-]", re.IGNORECASE),
    re.compile(r"^Autel[_-]", re.IGNORECASE),
]

# 802.11 frame type/subtype names
FRAME_TYPES = {
    0: "Management",
    1: "Control",
    2: "Data",
}

MGMT_SUBTYPES = {
    0: "Association Request",
    1: "Association Response",
    2: "Reassociation Request",
    3: "Reassociation Response",
    4: "Probe Request",
    5: "Probe Response",
    8: "Beacon",
    9: "ATIM",
    10: "Disassociation",
    11: "Authentication",
    12: "Deauthentication",
    13: "Action",
    14: "Action No Ack",
}

CTRL_SUBTYPES = {
    8: "Block Ack Request",
    9: "Block Ack",
    10: "PS-Poll",
    11: "RTS",
    12: "CTS",
    13: "ACK",
    14: "CF-End",
    15: "CF-End+CF-Ack",
}

DATA_SUBTYPES = {
    0: "Data",
    1: "Data+CF-Ack",
    2: "Data+CF-Poll",
    4: "Null",
    8: "QoS Data",
    12: "QoS Null",
}

# ESP-NOW uses vendor-specific action frames with category 127 (vendor-specific)
ESPNOW_ELEMENT_ID = 221  # vendor-specific IE
ESPRESSIF_OUI_BYTES = [b"\x18\xfe\x34", b"\x24\x0a\xc4"]


def mac_oui(mac: str) -> str:
    return mac.lower()[:8]


def lookup_oui(mac: str) -> str:
    oui = mac_oui(mac)
    return KNOWN_OUIS.get(oui, "Unknown")


def is_espressif_mac(mac: str) -> bool:
    oui = mac_oui(mac)
    return KNOWN_OUIS.get(oui, "") == "Espressif Inc."


def classify_ssid(ssid: str) -> str | None:
    for pat in ELRS_SSID_PATTERNS:
        if pat.match(ssid):
            return "ELRS WiFi Backpack"
    for pat in DRONE_SSID_PATTERNS:
        if pat.match(ssid):
            return "Drone WiFi"
    return None


def get_frame_type_name(frame_type: int, subtype: int) -> str:
    type_name = FRAME_TYPES.get(frame_type, f"Type{frame_type}")
    if frame_type == 0:
        sub_name = MGMT_SUBTYPES.get(subtype, f"Subtype{subtype}")
    elif frame_type == 1:
        sub_name = CTRL_SUBTYPES.get(subtype, f"Subtype{subtype}")
    elif frame_type == 2:
        sub_name = DATA_SUBTYPES.get(subtype, f"Subtype{subtype}")
    else:
        sub_name = f"Subtype{subtype}"
    return f"{type_name}/{sub_name}"


@dataclass
class BeaconInfo:
    ssid: str
    bssid: str
    channel: int | None = None
    crypto: list[str] = field(default_factory=list)
    oui_vendor: str = ""
    ssid_class: str | None = None
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    signal_dbm: list[int] = field(default_factory=list)


@dataclass
class ESPNowFrame:
    src: str
    dst: str
    timestamp: float
    payload_hex: str
    payload_len: int
    oui_vendor: str = ""
    signal_dbm: int | None = None


@dataclass
class DecodedFrame:
    timestamp: float
    frame_type: int
    frame_subtype: int
    type_name: str
    src: str | None = None
    dst: str | None = None
    bssid: str | None = None
    ssid: str | None = None
    signal_dbm: int | None = None
    channel: int | None = None
    is_espnow: bool = False
    espnow_payload_hex: str | None = None
    is_esp_mac: bool = False
    ssid_class: str | None = None
    crypto: str | None = None
    raw_len: int = 0


def extract_signal_dbm(pkt) -> int | None:
    if pkt.haslayer(RadioTap):
        rt = pkt[RadioTap]
        if hasattr(rt, "dBm_AntSignal"):
            return rt.dBm_AntSignal
    return None


def extract_channel(pkt) -> int | None:
    if pkt.haslayer(RadioTap):
        rt = pkt[RadioTap]
        if hasattr(rt, "ChannelFrequency"):
            freq = rt.ChannelFrequency
            if 2412 <= freq <= 2484:
                if freq == 2484:
                    return 14
                return (freq - 2407) // 5
            elif 5180 <= freq <= 5825:
                return (freq - 5000) // 5
    return None


def extract_ssid(pkt) -> str | None:
    if pkt.haslayer(Dot11Elt):
        elt = pkt[Dot11Elt]
        while elt:
            if elt.ID == 0:  # SSID
                try:
                    ssid = elt.info.decode("utf-8", errors="replace")
                    return ssid if ssid else None
                except (UnicodeDecodeError, AttributeError):
                    return None
            elt = elt.payload if hasattr(elt, "payload") and isinstance(elt.payload, Dot11Elt) else None
    return None


def extract_channel_from_elt(pkt) -> int | None:
    if pkt.haslayer(Dot11Elt):
        elt = pkt[Dot11Elt]
        while elt:
            if elt.ID == 3 and len(elt.info) == 1:  # DS Parameter Set
                return elt.info[0]
            elt = elt.payload if hasattr(elt, "payload") and isinstance(elt.payload, Dot11Elt) else None
    return None


def extract_crypto(pkt) -> list[str]:
    crypto = []
    if pkt.haslayer(Dot11Elt):
        elt = pkt[Dot11Elt]
        while elt:
            if elt.ID == 48:  # RSN (WPA2)
                crypto.append("WPA2")
            elif elt.ID == 221 and elt.info[:4] == b"\x00\x50\xf2\x01":
                crypto.append("WPA")
            elt = elt.payload if hasattr(elt, "payload") and isinstance(elt.payload, Dot11Elt) else None
    if not crypto:
        if pkt.haslayer(Dot11Beacon):
            cap = pkt[Dot11Beacon].cap
            if hasattr(cap, "privacy") and cap.privacy:
                crypto.append("WEP")
            else:
                crypto.append("Open")
        elif pkt.haslayer(Dot11ProbeResp):
            cap = pkt[Dot11ProbeResp].cap
            if hasattr(cap, "privacy") and cap.privacy:
                crypto.append("WEP")
            else:
                crypto.append("Open")
    return crypto


def detect_espnow(pkt) -> bytes | None:
    """Detect ESP-NOW vendor-specific action frames.

    ESP-NOW uses 802.11 Action frames (type 0, subtype 13) with
    category 127 (vendor-specific) and Espressif's OUI.
    """
    if not pkt.haslayer(Dot11):
        return None
    dot11 = pkt[Dot11]
    ftype = dot11.type
    fsubtype = dot11.subtype

    if ftype != 0 or fsubtype != 13:  # not an action frame
        return None

    payload = bytes(dot11.payload)
    if len(payload) < 5:
        return None

    # Action frame: category (1 byte) + OUI (3 bytes) + data
    category = payload[0]
    if category != 127:  # vendor-specific
        return None

    oui = payload[1:4]
    for esp_oui in ESPRESSIF_OUI_BYTES:
        if oui == esp_oui:
            return payload[4:]  # ESP-NOW payload after OUI

    return None


FILTER_MAP = {
    "esp-now": lambda ft, fs, espnow, esp, sc: espnow,
    "beacon": lambda ft, fs, espnow, esp, sc: ft == 0 and fs == 8,
    "probe": lambda ft, fs, espnow, esp, sc: ft == 0 and fs in (4, 5),
    "esp": lambda ft, fs, espnow, esp, sc: esp,
    "drone": lambda ft, fs, espnow, esp, sc: sc or esp or espnow,
    "mgmt": lambda ft, fs, espnow, esp, sc: ft == 0,
    "data": lambda ft, fs, espnow, esp, sc: ft == 2,
    "deauth": lambda ft, fs, espnow, esp, sc: ft == 0 and fs in (10, 12),
    "action": lambda ft, fs, espnow, esp, sc: ft == 0 and fs == 13,
}


def _matches_filter(frame_filter, ftype, fsubtype, is_espnow, is_esp, ssid_class):
    fn = FILTER_MAP.get(frame_filter)
    if fn is None:
        return True
    return fn(ftype, fsubtype, is_espnow, is_esp, ssid_class)


def decode_pcap(
    pcap_path: str,
    frame_filter: str | None = None,
    verbose: bool = False,
) -> tuple[list[DecodedFrame], dict]:
    """Read and decode all frames from a pcap/pcapng file.

    Returns (frames, summary_stats).
    """
    path = Path(pcap_path)
    if not path.exists():
        print(f"Error: {pcap_path} not found", file=sys.stderr)
        sys.exit(1)

    # Auto-detect pcap vs pcapng
    with open(path, "rb") as f:
        magic = f.read(4)

    if magic in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4"):
        reader_cls = PcapReader
    elif magic == b"\x0a\x0d\x0d\x0a":
        reader_cls = PcapNgReader
    else:
        print(f"Error: {pcap_path} is not a valid pcap/pcapng file (magic: {magic.hex()})", file=sys.stderr)
        sys.exit(1)

    frames: list[DecodedFrame] = []
    beacons: dict[str, BeaconInfo] = {}
    espnow_frames: list[ESPNowFrame] = []

    type_counter: Counter = Counter()
    mac_counter: Counter = Counter()
    oui_counter: Counter = Counter()
    ssid_set: set[str] = set()
    esp_macs: set[str] = set()
    drone_ssids: set[str] = set()
    elrs_ssids: set[str] = set()
    channels_seen: set[int] = set()
    total_packets = 0
    first_ts = None
    last_ts = None

    with reader_cls(str(path)) as reader:
        for pkt in reader:
            total_packets += 1
            ts = float(pkt.time)
            if first_ts is None:
                first_ts = ts
            last_ts = ts

            if not pkt.haslayer(Dot11):
                continue

            dot11 = pkt[Dot11]
            ftype = dot11.type
            fsubtype = dot11.subtype
            type_name = get_frame_type_name(ftype, fsubtype)
            type_counter[type_name] += 1

            src = dot11.addr2
            dst = dot11.addr1
            bssid = dot11.addr3 if hasattr(dot11, "addr3") else None

            signal = extract_signal_dbm(pkt)
            channel = extract_channel(pkt) or extract_channel_from_elt(pkt)
            if channel:
                channels_seen.add(channel)

            # Track MACs
            for addr in (src, dst, bssid):
                if addr and addr != "ff:ff:ff:ff:ff:ff":
                    mac_counter[addr] += 1
                    oui_counter[mac_oui(addr)] += 1
                    if is_espressif_mac(addr):
                        esp_macs.add(addr)

            ssid = None
            ssid_class = None
            crypto_str = None

            # Beacon / probe handling
            if ftype == 0 and fsubtype in (4, 5, 8):
                ssid = extract_ssid(pkt)
                if ssid:
                    ssid_set.add(ssid)
                    ssid_class = classify_ssid(ssid)
                    if ssid_class == "ELRS WiFi Backpack":
                        elrs_ssids.add(ssid)
                    elif ssid_class == "Drone WiFi":
                        drone_ssids.add(ssid)

                if fsubtype == 8 and bssid:  # Beacon
                    crypto_list = extract_crypto(pkt)
                    crypto_str = "/".join(crypto_list) if crypto_list else None
                    key = bssid
                    if key not in beacons:
                        beacons[key] = BeaconInfo(
                            ssid=ssid or "(hidden)",
                            bssid=bssid,
                            channel=channel,
                            crypto=crypto_list,
                            oui_vendor=lookup_oui(bssid),
                            ssid_class=ssid_class,
                            first_seen=ts,
                        )
                    b = beacons[key]
                    b.count += 1
                    b.last_seen = ts
                    if signal is not None:
                        b.signal_dbm.append(signal)
                    if ssid and b.ssid == "(hidden)":
                        b.ssid = ssid

            # ESP-NOW detection
            is_espnow = False
            espnow_hex = None
            espnow_payload = detect_espnow(pkt)
            if espnow_payload is not None:
                is_espnow = True
                espnow_hex = espnow_payload.hex()
                espnow_frames.append(ESPNowFrame(
                    src=src or "??",
                    dst=dst or "??",
                    timestamp=ts,
                    payload_hex=espnow_hex,
                    payload_len=len(espnow_payload),
                    oui_vendor=lookup_oui(src) if src else "",
                    signal_dbm=signal,
                ))

            # Deauth tracking
            if ftype == 0 and fsubtype == 12:
                pass  # tracked by type_counter

            is_esp = (src and is_espressif_mac(src)) or (dst and is_espressif_mac(dst))

            frame = DecodedFrame(
                timestamp=ts,
                frame_type=ftype,
                frame_subtype=fsubtype,
                type_name=type_name,
                src=src,
                dst=dst,
                bssid=bssid,
                ssid=ssid,
                signal_dbm=signal,
                channel=channel,
                is_espnow=is_espnow,
                espnow_payload_hex=espnow_hex,
                is_esp_mac=is_esp,
                ssid_class=ssid_class,
                crypto=crypto_str,
                raw_len=len(raw(pkt)),
            )

            # Apply filter
            if frame_filter and not _matches_filter(frame_filter, ftype, fsubtype, is_espnow, is_esp, ssid_class):
                continue

            frames.append(frame)

    duration = (last_ts - first_ts) if first_ts and last_ts else 0
    summary = {
        "file": str(path.name),
        "total_packets": total_packets,
        "dot11_frames": sum(type_counter.values()),
        "duration_seconds": round(duration, 3),
        "time_range": {
            "first": datetime.fromtimestamp(first_ts, tz=timezone.utc).isoformat() if first_ts else None,
            "last": datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat() if last_ts else None,
        },
        "channels": sorted(channels_seen),
        "frame_types": dict(type_counter.most_common()),
        "unique_macs": len(mac_counter),
        "top_macs": [
            {"mac": mac, "count": cnt, "vendor": lookup_oui(mac)}
            for mac, cnt in mac_counter.most_common(20)
        ],
        "oui_breakdown": [
            {"oui": oui, "count": cnt, "vendor": KNOWN_OUIS.get(oui, "Unknown")}
            for oui, cnt in oui_counter.most_common(15)
        ],
        "ssids": sorted(ssid_set),
        "beacons": [
            {
                "bssid": b.bssid,
                "ssid": b.ssid,
                "channel": b.channel,
                "crypto": b.crypto,
                "vendor": b.oui_vendor,
                "ssid_class": b.ssid_class,
                "beacon_count": b.count,
                "avg_signal_dbm": round(sum(b.signal_dbm) / len(b.signal_dbm)) if b.signal_dbm else None,
            }
            for b in sorted(beacons.values(), key=lambda x: -x.count)
        ],
        "esp_analysis": {
            "espressif_macs": sorted(esp_macs),
            "espnow_frame_count": len(espnow_frames),
            "elrs_ssids": sorted(elrs_ssids),
            "drone_ssids": sorted(drone_ssids),
            "espnow_frames": [
                {
                    "src": f.src,
                    "dst": f.dst,
                    "timestamp": f.timestamp,
                    "payload_len": f.payload_len,
                    "payload_hex": f.payload_hex[:64] + ("..." if len(f.payload_hex) > 64 else ""),
                    "vendor": f.oui_vendor,
                    "signal_dbm": f.signal_dbm,
                }
                for f in espnow_frames[:50]  # cap at 50 for readability
            ],
        },
    }

    # Security assessment
    findings = []
    if esp_macs:
        findings.append(f"Espressif MACs detected: {', '.join(sorted(esp_macs))} — potential ESP32/ESP8266 co-processor")
    if elrs_ssids:
        findings.append(f"ELRS WiFi Backpack SSIDs: {', '.join(sorted(elrs_ssids))} — WiFi update mode active or captured")
    if drone_ssids:
        findings.append(f"Drone WiFi SSIDs: {', '.join(sorted(drone_ssids))}")
    if espnow_frames:
        findings.append(f"{len(espnow_frames)} ESP-NOW action frames — confirms ESP-based co-processor with ESP-NOW capability")
    deauth_count = type_counter.get("Management/Deauthentication", 0) + type_counter.get("Management/Disassociation", 0)
    if deauth_count > 10:
        findings.append(f"{deauth_count} deauth/disassoc frames — possible deauth attack or noisy environment")
    if not findings:
        findings.append("No drone-specific or ESP-related traffic identified in this capture")
    summary["security_findings"] = findings

    return frames, summary


def print_summary(summary: dict, file=sys.stdout) -> None:
    print("=" * 72, file=file)
    print(f"  WiFi Pcap Decode: {summary['file']}", file=file)
    print("=" * 72, file=file)
    print(f"  Packets: {summary['total_packets']}  |  802.11 frames: {summary['dot11_frames']}  |  Duration: {summary['duration_seconds']}s", file=file)
    tr = summary["time_range"]
    if tr["first"]:
        print(f"  Time range: {tr['first']} → {tr['last']}", file=file)
    if summary["channels"]:
        print(f"  Channels: {summary['channels']}", file=file)
    print(file=file)

    print("  Frame types:", file=file)
    for name, count in summary["frame_types"].items():
        print(f"    {name:40s} {count:>6d}", file=file)
    print(file=file)

    if summary["beacons"]:
        print("  Access points (beacons):", file=file)
        for b in summary["beacons"]:
            tag = ""
            if b["ssid_class"]:
                tag = f"  *** {b['ssid_class']} ***"
            sig = f"  avg {b['avg_signal_dbm']} dBm" if b["avg_signal_dbm"] is not None else ""
            print(f"    {b['bssid']}  {b['ssid']!r:30s}  ch {b['channel'] or '?':>3}  {'/'.join(b['crypto']):>8s}  {b['vendor']}{sig}{tag}", file=file)
        print(file=file)

    if summary["ssids"]:
        print(f"  All SSIDs seen ({len(summary['ssids'])}):", file=file)
        for ssid in summary["ssids"]:
            c = classify_ssid(ssid)
            tag = f"  ← {c}" if c else ""
            print(f"    {ssid!r}{tag}", file=file)
        print(file=file)

    esp = summary["esp_analysis"]
    if esp["espressif_macs"] or esp["espnow_frame_count"] or esp["elrs_ssids"]:
        print("  *** ESP / Drone analysis ***", file=file)
        if esp["espressif_macs"]:
            print(f"    Espressif MACs: {', '.join(esp['espressif_macs'])}", file=file)
        if esp["elrs_ssids"]:
            print(f"    ELRS Backpack SSIDs: {', '.join(esp['elrs_ssids'])}", file=file)
        if esp["drone_ssids"]:
            print(f"    Drone SSIDs: {', '.join(esp['drone_ssids'])}", file=file)
        if esp["espnow_frame_count"]:
            print(f"    ESP-NOW frames: {esp['espnow_frame_count']}", file=file)
            for f in esp["espnow_frames"][:10]:
                print(f"      {f['src']} → {f['dst']}  len={f['payload_len']}  payload={f['payload_hex']}", file=file)
            if esp["espnow_frame_count"] > 10:
                print(f"      ... and {esp['espnow_frame_count'] - 10} more", file=file)
        print(file=file)

    print("  Top MACs:", file=file)
    for m in summary["top_macs"][:15]:
        esp_tag = " [ESP]" if is_espressif_mac(m["mac"]) else ""
        print(f"    {m['mac']}  {m['count']:>6d}  {m['vendor']}{esp_tag}", file=file)
    print(file=file)

    if summary["oui_breakdown"]:
        print("  OUI breakdown:", file=file)
        for o in summary["oui_breakdown"]:
            print(f"    {o['oui']}  {o['count']:>6d}  {o['vendor']}", file=file)
        print(file=file)

    print("  Security findings:", file=file)
    for finding in summary["security_findings"]:
        print(f"    • {finding}", file=file)
    print("=" * 72, file=file)


def frame_to_dict(f: DecodedFrame) -> dict:
    d = {
        "timestamp": f.timestamp,
        "type": f.type_name,
        "src": f.src,
        "dst": f.dst,
    }
    if f.bssid:
        d["bssid"] = f.bssid
    if f.ssid:
        d["ssid"] = f.ssid
    if f.signal_dbm is not None:
        d["signal_dbm"] = f.signal_dbm
    if f.channel is not None:
        d["channel"] = f.channel
    if f.is_espnow:
        d["esp_now"] = True
        d["espnow_payload"] = f.espnow_payload_hex
    if f.is_esp_mac:
        d["esp_mac"] = True
    if f.ssid_class:
        d["ssid_class"] = f.ssid_class
    if f.crypto:
        d["crypto"] = f.crypto
    d["raw_len"] = f.raw_len
    return d


def main():
    parser = argparse.ArgumentParser(
        description="Decode WiFi pcap captures for drone/ESP traffic analysis",
    )
    parser.add_argument("pcap", help="Path to pcap or pcapng file")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    parser.add_argument("--output", "-o", help="Write per-frame JSONL to file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each decoded frame")
    parser.add_argument("--summary-only", action="store_true", help="Print only the summary, skip frame listing")
    parser.add_argument(
        "--filter",
        choices=["esp-now", "beacon", "probe", "esp", "drone", "mgmt", "data", "deauth", "action"],
        help="Filter to specific frame types",
    )

    args = parser.parse_args()

    frames, summary = decode_pcap(args.pcap, frame_filter=args.filter, verbose=args.verbose)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print_summary(summary)

    if args.verbose and not args.summary_only:
        print(f"\n  Decoded frames: {len(frames)}\n")
        for f in frames:
            ts_str = datetime.fromtimestamp(f.timestamp, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            esp_tag = " [ESP-NOW]" if f.is_espnow else (" [ESP]" if f.is_esp_mac else "")
            ssid_tag = f" ssid={f.ssid!r}" if f.ssid else ""
            sig_tag = f" {f.signal_dbm}dBm" if f.signal_dbm is not None else ""
            print(f"  {ts_str}  {f.type_name:30s}  {f.src or '??':17s} → {f.dst or '??':17s}{ssid_tag}{sig_tag}{esp_tag}")

    if args.output:
        out_path = Path(args.output)
        with open(out_path, "w") as fout:
            fout.write(json.dumps({"_type": "summary", **summary}, default=str) + "\n")
            for f in frames:
                fout.write(json.dumps(frame_to_dict(f), default=str) + "\n")
        print(f"\n  Wrote {len(frames) + 1} lines to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
