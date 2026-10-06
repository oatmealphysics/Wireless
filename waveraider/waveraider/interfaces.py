"""Detect connected capture hardware — SDRs, monitor-capable Wi-Fi adapters
(Alfa & friends), BLE sniffers — plus the host's network interfaces.

Identification is by USB VID:PID against a known-device table, which tells you
what is *plugged in*. It does NOT prove the driver is loaded, monitor mode
works, or you have privileges — live capture still depends on OS, driver, and
root. Parsing is split from the shell calls so it's testable without hardware.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
from glob import glob


# vid:pid  ->  (kind, model, note)   [lowercase hex, no 0x]
KNOWN_DEVICES = {
    # ---- Software-defined radios ----
    "0bda:2838": ("sdr", "RTL-SDR (RTL2838)", "~24 MHz–1.75 GHz, 8-bit, RX only"),
    "0bda:2832": ("sdr", "RTL-SDR (RTL2832U)", "8-bit, RX only"),
    "1d50:6089": ("sdr", "HackRF One", "1 MHz–6 GHz, half-duplex, 8-bit"),
    "1d50:604b": ("sdr", "HackRF Jawbreaker", "half-duplex, 8-bit"),
    "2cf0:5246": ("sdr", "bladeRF", "47 MHz–6 GHz, 12-bit"),
    "2cf0:5250": ("sdr", "bladeRF 2.0 micro", "12-bit, 2x2 MIMO"),
    "1d50:6108": ("sdr", "LimeSDR-USB", "100 kHz–3.8 GHz, 12-bit"),
    "1d50:6110": ("sdr", "LimeSDR Mini", "12-bit"),
    "1d50:60a1": ("sdr", "Airspy", "RX only"),
    "2500:0020": ("sdr", "Ettus USRP B200", "70 MHz–6 GHz, 12-bit"),
    "2500:0021": ("sdr", "Ettus USRP B210", "70 MHz–6 GHz, 12-bit, 2x2"),
    "2500:0022": ("sdr", "Ettus USRP B200mini", "12-bit"),
    "fffe:0002": ("sdr", "Ettus USRP (legacy)", ""),
    # ---- SignalHound analyzers (VID 2eb8; PIDs best-effort, confirm on connect) ----
    "2eb8:0012": ("sdr", "SignalHound BB60C", "9 kHz–6 GHz; needs the SignalHound SDK"),
    "2eb8:0013": ("sdr", "SignalHound BB60D", "9 kHz–6 GHz; needs the SignalHound SDK"),
    "2eb8:0020": ("sdr", "SignalHound SM200", "100 kHz–20 GHz; needs the SignalHound SDK"),
    "2eb8:0002": ("sdr", "SignalHound SA44/SA124", "needs the SignalHound SDK"),
    # ---- Monitor-capable Wi-Fi adapters (Alfa & common chipsets) ----
    "0bda:8812": ("wifi", "RTL8812AU (Alfa AWUS036ACH/AC)", "2.4/5 GHz, monitor+inject w/ driver"),
    "0bda:8813": ("wifi", "RTL8813AU", "2.4/5 GHz"),
    "0e8d:7612": ("wifi", "MT7612U (Alfa AWUS036ACM)", "2.4/5 GHz, mainline driver"),
    "0e8d:7961": ("wifi", "MT7921AU (Alfa AWUS036AXML)", "Wi-Fi 6E, passive monitor only"),
    "0cf3:9271": ("wifi", "AR9271 (Alfa AWUS036NHA)", "2.4 GHz, solid monitor+inject"),
    "148f:5370": ("wifi", "Ralink RT5370", "2.4 GHz"),
    "148f:3070": ("wifi", "Ralink RT3070", "2.4 GHz"),
    "148f:7601": ("wifi", "MT7601U", "2.4 GHz"),
    "0bda:8187": ("wifi", "RTL8187 (old Alfa)", "2.4 GHz"),
    # ---- Bluetooth sniffers ----
    "1d50:6002": ("bluetooth", "Ubertooth One", "BT Classic/BLE spectrum sniffer"),
    "1915:520f": ("bluetooth", "Nordic nRF52840 dongle", "BLE sniffer (Sniffle / nRF Sniffer)"),
}


# ---- USB parsing (pure, testable) ---------------------------------------

def parse_lsusb(text: str) -> list:
    """Parse Linux `lsusb` output -> [(vid, pid, name)]."""
    out = []
    for line in text.splitlines():
        m = re.search(r"ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s*(.*)", line)
        if m:
            out.append((m.group(1).lower(), m.group(2).lower(), m.group(3).strip()))
    return out


def parse_system_profiler(text: str) -> list:
    """Parse macOS `system_profiler SPUSBDataType` -> [(vid, pid, name)]."""
    out = []
    name = None
    vid = pid = None
    for line in text.splitlines():
        stripped = line.strip()
        # a device header ends in ':' and isn't a field line
        if stripped.endswith(":") and ":" not in stripped[:-1]:
            name = stripped[:-1]
            vid = pid = None
        mv = re.search(r"Vendor ID:\s*0x([0-9a-fA-F]{1,4})", line)
        mp = re.search(r"Product ID:\s*0x([0-9a-fA-F]{1,4})", line)
        if mv:
            vid = mv.group(1).lower().zfill(4)
        if mp:
            pid = mp.group(1).lower().zfill(4)
        if vid and pid:
            out.append((vid, pid, name or "USB device"))
            vid = pid = None
    return out


def _parse_sysfs_usb() -> list:
    out = []
    for vpath in glob("/sys/bus/usb/devices/*/idVendor"):
        d = os.path.dirname(vpath)
        try:
            vid = open(vpath).read().strip().lower()
            pid = open(os.path.join(d, "idProduct")).read().strip().lower()
        except OSError:
            continue
        name = ""
        try:
            name = open(os.path.join(d, "product")).read().strip()
        except OSError:
            pass
        out.append((vid, pid, name))
    return out


# USB plumbing and input devices that can't be a radio: Linux Foundation root hubs (VID 1d6b)
# and hubs, host controllers, keyboards, pointing devices and digitizers by name. Docker
# Desktop and Colima VMs expose these as virtual devices.
_NOT_A_RADIO_VIDS = {"1d6b"}
_NOT_A_RADIO_NAME = re.compile(
    r"\b(hub|host controller|[xeou]hci|keyboard|mouse|trackpad|touchpad|digitizer|touch ?screen)\b", re.I)


def unrecognized_usb() -> list:
    """USB devices we don't have in KNOWN_DEVICES — so a just-plugged-in radio
    whose exact VID:PID we haven't catalogued is still visible for identification.
    Hubs, host controllers and input devices are left out."""
    return [{"id": f"{vid}:{pid}", "name": name}
            for vid, pid, name in usb_devices()
            if f"{vid}:{pid}" not in KNOWN_DEVICES
            and vid not in _NOT_A_RADIO_VIDS and not _NOT_A_RADIO_NAME.search(name or "")]


def _run(cmd: list) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def usb_devices() -> list:
    if platform.system() == "Darwin":
        return parse_system_profiler(_run(["system_profiler", "SPUSBDataType"]))
    out = _run(["lsusb"])
    return parse_lsusb(out) if out else _parse_sysfs_usb()


# ---- network interfaces (best effort) -----------------------------------

def network_interfaces() -> list:
    system = platform.system()
    if system == "Linux":
        out = []
        for path in sorted(glob("/sys/class/net/*")):
            name = os.path.basename(path)
            if name == "lo":
                continue
            wireless = os.path.exists(os.path.join(path, "wireless")) or os.path.exists(
                os.path.join(path, "phy80211")
            )
            out.append({"name": name, "wireless": wireless})
        return out
    if system == "Darwin":
        names = _run(["ifconfig", "-l"]).split()
        wifi_dev = None
        hp = _run(["networksetup", "-listallhardwareports"])
        m = re.search(r"Hardware Port:\s*Wi-Fi\s*\nDevice:\s*(\w+)", hp)
        if m:
            wifi_dev = m.group(1)
        return [
            {"name": n, "wireless": n == wifi_dev}
            for n in names
            if n != "lo0" and not n.startswith(("gif", "stf", "utun", "llw", "awdl"))
        ]
    return []


# ---- top-level detect ----------------------------------------------------

def detect() -> dict:
    matched, unmatched = [], 0
    for vid, pid, name in usb_devices():
        key = f"{vid}:{pid}"
        if key in KNOWN_DEVICES:
            kind, model, note = KNOWN_DEVICES[key]
            matched.append({"kind": kind, "id": key, "model": model,
                            "note": note, "usb_name": name})
        else:
            unmatched += 1
    return {
        "capture_devices": matched,
        "other_usb_count": unmatched,
        "network_interfaces": network_interfaces(),
    }


def render(inv: dict) -> str:
    lines = []
    devs = inv["capture_devices"]
    lines.append(f"CAPTURE HARDWARE ({len(devs)})")
    if not devs:
        lines.append("  none recognized")
    for d in sorted(devs, key=lambda x: x["kind"]):
        tail = f"  — {d['note']}" if d["note"] else ""
        lines.append(f"  [{d['kind']:<9}] {d['model']}  ({d['id']}){tail}")
    lines.append(f"  (+{inv['other_usb_count']} other USB devices)")

    nets = inv["network_interfaces"]
    lines.append("")
    lines.append(f"NETWORK INTERFACES ({len(nets)})")
    for n in nets:
        w = " (wireless)" if n["wireless"] else ""
        lines.append(f"  {n['name']}{w}")

    lines.append("")
    lines.append("note: USB IDs show what's plugged in — not that a driver, monitor")
    lines.append("mode, or the privileges for live capture are actually available.")
    return "\n".join(lines)
