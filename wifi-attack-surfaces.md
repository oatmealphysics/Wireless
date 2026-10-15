# WiFi Attack Surfaces in Drone Platforms

> **Purpose:** Focused reference for WiFi-based attacks against drone platforms in CTF competition.
> Complements `drone-wireless-vuln-reference.md` with deeper operational detail per platform.
> **Last updated:** 2026-10-14

---

## Table of Contents

- [Quick-Reference: WiFi Attack Matrix](#quick-reference-wifi-attack-matrix)
- [DJI WiFi Attack Surface](#dji-wifi-attack-surface)
  - [Enhanced WiFi (Mini SE, Mini 2 SE, Tello)](#enhanced-wifi-mini-se-mini-2-se-tello)
  - [WiFi Video Downlink Interception](#wifi-video-downlink-interception)
  - [DJI App Control via WiFi](#dji-app-control-via-wifi)
  - [WiFi Credential Recovery (BLE + WPA2)](#wifi-credential-recovery-ble--wpa2)
  - [OcuSync Models — WiFi Is Not the Control Link](#ocusync-models--wifi-is-not-the-control-link)
- [Tello and WiFi-Controlled Drones](#tello-and-wifi-controlled-drones)
  - [Ryze Tello — Full Takeover](#ryze-tello--full-takeover)
  - [Tello Attack Chain (Step by Step)](#tello-attack-chain-step-by-step)
  - [Tello Video Stream Interception](#tello-video-stream-interception)
  - [Other WiFi-Controlled Toy Drones](#other-wifi-controlled-toy-drones)
- [Parrot WiFi Attack Surface](#parrot-wifi-attack-surface)
  - [AR.Drone — Zero-Auth Root Shell](#ardrone--zero-auth-root-shell)
  - [Bebop / Bebop 2 — Weak WPA2 + No PMF](#bebop--bebop-2--weak-wpa2--no-pmf)
  - [Anafi — Deauth Susceptibility](#anafi--deauth-susceptibility)
- [WiFi Deauthentication Across Drone Platforms](#wifi-deauthentication-across-drone-platforms)
  - [Which Drones Are Vulnerable](#which-drones-are-vulnerable)
  - [Deauth Attack Execution](#deauth-attack-execution)
  - [Bypassing PMF (802.11w)](#bypassing-pmf-80211w)
- [Default AP Credentials by Platform](#default-ap-credentials-by-platform)
- [Unencrypted Telemetry over WiFi](#unencrypted-telemetry-over-wifi)
  - [MAVLink over WiFi (UDP 14550/14555)](#mavlink-over-wifi-udp-1455014555)
  - [Video Feeds](#video-feeds)
  - [Companion Computer Web Interfaces](#companion-computer-web-interfaces)
- [WiFi-Specific CVEs for Drone Interfaces](#wifi-specific-cves-for-drone-interfaces)
- [WiFi Recon Playbook](#wifi-recon-playbook)
- [Tools Reference](#tools-reference)
- [Sources](#sources)

---

## Quick-Reference: WiFi Attack Matrix

| Platform | WiFi Role | Default Auth | Deauth Vuln | Video Exposed | Takeover Path | Difficulty |
|---|---|---|---|---|---|---|
| **Tello** | Control + video | None (open AP) | Yes | Yes (UDP H.264) | Connect → send SDK commands | Trivial |
| **Parrot AR.Drone** | Control + video | None (open AP) | Yes | Yes (raw stream) | Connect → telnet root | Trivial |
| **Parrot Bebop 2** | Control + video | Weak WPA2 | Yes (no PMF) | Yes | Crack PSK → API/FTP | Easy |
| **Parrot Anafi** | Control + video | WPA2 (settable) | Yes (CVE-2019-3944) | Yes | Crack PSK → SDK API | Easy–Medium |
| **DJI Mini SE** | Control + video | WPA2 | Yes (no PMF confirmed) | Yes (Enhanced WiFi) | Crack/sniff PSK → MitM | Medium |
| **DJI Mini 2 SE** | Control + video | WPA2 | Likely | Yes (Enhanced WiFi) | Crack/sniff PSK → MitM | Medium |
| **DJI (OcuSync)** | Companion only | WPA2 | N/A (not control link) | No (AES-256) | Not via WiFi | Hard |
| **Syma / Hubsan** | Control + video | None (open AP) | Yes | Yes | Connect → app protocol | Trivial |
| **MAVLink (WiFi)** | Telemetry/cmd | Varies (often open) | N/A | N/A | Connect → MAVLink inject | Easy |

---

## DJI WiFi Attack Surface

DJI uses WiFi in two distinct architectures. Understanding which one the target uses determines whether WiFi is worth attacking.

### Enhanced WiFi (Mini SE, Mini 2 SE, Tello)

**What it is:** WiFi IS the control and video link. The drone acts as a WiFi access point; the controller (phone or dedicated RC) connects as a client. All command and video traffic flows over this WiFi connection.

**Why it matters:** Everything rides on one WiFi link — break it and you own control, video, and telemetry.

**Architecture:**
```
Phone/RC ←── WiFi (WPA2) ──→ Drone AP
              ↑
        All traffic here:
        - Flight commands
        - Video downlink
        - Telemetry
```

**Attack vectors:**
1. **PMKID capture + offline crack:** Grab the PMKID from the AP's first EAPOL frame. No connected client needed. Crack offline with hashcat.
2. **WPA2 handshake capture:** Deauth an active client, capture the 4-way handshake, crack offline.
3. **CVE-2023-6951 (Weak credentials):** QuickTransfer Mode uses derivable WPA2 keys. If the drone enters this mode, the PSK can be computed.
4. **CVE-2026-77812 (BLE WiFi credential leak):** Sniff BLE traffic near the drone during WiFi operations — the SSID and password are sent in cleartext over BLE.
5. **Man-in-the-middle:** Once you have the PSK, set up a rogue AP with the same SSID. Deauth the legitimate controller, and both drone and controller may reconnect through your AP.

**Post-access:**
- Intercept and inject DJI app protocol commands
- Capture live video feed
- Inject modified GPS coordinates into the app display
- Block legitimate controller by holding the WiFi connection

**Confirmed vulnerable models (Enhanced WiFi):**
- DJI Mini SE
- DJI Mini 2 SE
- DJI Tello (Ryze, DJI-powered)
- DJI Spark (legacy)

### WiFi Video Downlink Interception

On Enhanced WiFi models, the video downlink is accessible to anyone on the WiFi network:

- **Protocol:** Typically RTP/UDP or proprietary UDP stream
- **Encryption:** None on Enhanced WiFi models (the WPA2 layer is the only protection)
- **Tools:** `ffplay`, `ffmpeg`, VLC, or Wireshark with RTP dissector
- **Capture method:** Once on the network (via PSK crack or open AP), capture UDP traffic and decode the H.264/H.265 stream

**Note:** OcuSync models encrypt the video downlink with AES-256. WiFi-based video interception does not work against OcuSync.

### DJI App Control via WiFi

DJI's mobile apps (DJI Fly, DJI Go 4) communicate with Enhanced WiFi drones using a proprietary protocol over WiFi:

- **Transport:** UDP and TCP connections between app and drone
- **Authentication:** Relies entirely on WPA2 network membership — once on the WiFi network, the drone accepts commands from any client running the DJI SDK
- **DJI Mobile SDK:** Publicly documented; allows programmatic control of camera, gimbal, flight path, and telemetry
- **Attack tool:** Write a script using the DJI Mobile SDK (or reverse the protocol) to send commands once you're on the WiFi network

**Key insight:** DJI's Enhanced WiFi drones do not authenticate which client on the network is sending commands. Join the network → send commands → control the drone.

### WiFi Credential Recovery (BLE + WPA2)

Two paths to recovering the WiFi PSK without brute force:

**Path 1: BLE sniffing (CVE-2026-77812)**
- DJI drones leak WiFi credentials over BLE during pairing/provisioning
- Passive attack: sniff BLE traffic with `btlejack` or `ubertooth` during drone power-on or app connection
- Yields SSID and WPA2 password in cleartext

**Path 2: CVE-2023-6951 (QuickTransfer)**
- When QuickTransfer Mode is active, the WPA2 key is derivable from observable parameters
- Requires user to activate the mode (some user interaction)

**Path 3: PMKID offline attack (universal)**
- Works against any WPA2 AP, including DJI drones
- Send a single association request; the AP responds with PMKID in the first EAPOL message
- Crack offline: `hcxdumptool` to capture, `hashcat -m 22000` to crack
- Speed depends on password complexity; DJI defaults are typically 8-character alphanumeric

### OcuSync Models — WiFi Is Not the Control Link

**Models:** Mavic Air 2, Mini 3 Pro, Mini 4 Pro, Air 3, Mavic 3 series, Avata series

- OcuSync uses a proprietary RF link with AES-256 encryption for control and video
- WiFi on these models is used only for firmware updates, file transfer, and companion features
- Breaking the WiFi does NOT give you control of the drone
- **CTF implication:** If the target is an OcuSync drone, skip WiFi and focus on BLE (CVE-2026-78306) or physical/autopilot attacks

---

## Tello and WiFi-Controlled Drones

### Ryze Tello — Full Takeover

The Tello is the most commonly seen drone in CTF competitions and security labs. Its WiFi security is essentially nonexistent.

**Security posture:**
- WiFi AP with **no password by default**
- Setup process does not prompt user to set a password
- Even with a password set, the control protocol has no authentication layer
- Video stream is unencrypted UDP
- SDK command interface accepts commands from any client on the network

**Network layout:**
```
Tello AP (192.168.10.1)
  ├── Control: UDP port 8889 (SDK text commands)
  ├── State:   UDP port 8890 (telemetry broadcast)
  └── Video:   UDP port 11111 (H.264 stream)
```

### Tello Attack Chain (Step by Step)

**Scenario:** Tello is powered on with default config. Operator's phone is connected.

1. **Scan:** `airodump-ng wlan0mon` — look for SSID matching `TELLO-*` pattern
2. **Connect:** Join the open WiFi network (or deauth operator + join)
3. **Discover:** Drone is at `192.168.10.1`
4. **Send SDK commands:**
   ```bash
   # Enter SDK mode
   echo "command" | nc -u -w1 192.168.10.1 8889
   # Take off
   echo "takeoff" | nc -u -w1 192.168.10.1 8889
   # Land (or don't)
   echo "land" | nc -u -w1 192.168.10.1 8889
   # Emergency stop (motors off immediately — drone falls)
   echo "emergency" | nc -u -w1 192.168.10.1 8889
   ```
5. **Capture video:**
   ```bash
   # Start video stream
   echo "streamon" | nc -u -w1 192.168.10.1 8889
   # View with ffplay
   ffplay -i udp://0.0.0.0:11111 -fflags nobuffer
   ```
6. **Read telemetry:** Listen on UDP 8890 for state data (battery, height, temperature, barometer, flight time)

**Denial of service:**
- Deauth the operator's phone from the Tello network
- Connect yourself
- The Tello only accepts one controller at a time — the legitimate operator is locked out
- Issue `land` or `emergency` command

**Flag capture potential:**
- Read flight logs via SDK
- Capture camera snapshots
- Access stored data if the Tello has a file transfer interface

### Tello Video Stream Interception

- **Format:** H.264 encoded, transmitted over UDP port 11111
- **No encryption** — anyone on the WiFi network can capture it
- **Tools:** `ffplay`, `ffmpeg`, `vlc`, or raw capture with `tcpdump`
- **Passive mode:** If you're on the network, you can capture the stream without sending any commands
- **Recording:** `ffmpeg -i udp://0.0.0.0:11111 -c copy output.mp4`

### Other WiFi-Controlled Toy Drones

Many sub-$100 drones use the same architecture as the Tello:

| Drone | WiFi | Control Port | Video Port | Default Password |
|---|---|---|---|---|
| Ryze Tello | 2.4 GHz, open | UDP 8889 | UDP 11111 | None |
| Syma X5SW | 2.4 GHz, open | Proprietary | Proprietary | None |
| Hubsan X4 H501S | 2.4 GHz, open | Proprietary | Proprietary | None |
| Holy Stone HS110D | 2.4 GHz, open | Proprietary | Proprietary | None |
| JJRC H68 | 2.4 GHz, open | Proprietary | Proprietary | None |

**Common patterns:**
- Open WiFi AP (SSID contains model name)
- Drone is always at a fixed IP (often `192.168.x.1`)
- Control protocol is app-specific but usually UDP-based
- Video is unencrypted UDP (often MJPEG or H.264)
- Some use the same chipset (Hi3518 or similar) and share protocol structures

**Generic attack:** Connect to the AP → reverse the app's UDP protocol (Wireshark the official app) → replay commands.

---

## Parrot WiFi Attack Surface

### AR.Drone — Zero-Auth Root Shell

The original Parrot AR.Drone is the canonical insecure drone. Zero wireless security.

**Attack chain:**
1. Connect to open WiFi AP (no password)
2. `telnet 192.168.1.1` — root shell, no password
3. Full filesystem access, process control, motor control
4. `ftp 192.168.1.1` — access stored photos/videos

**Services exposed on WiFi:**
| Port | Service | Auth |
|---|---|---|
| 23 | Telnet (root) | None |
| 21 | FTP | None |
| 5555 | AT command interface | None |
| 5556 | Navigation data (UDP) | None |
| 5557 | Video stream (UDP) | None |

**CTF relevance:** Still appears in CTFs as an easy target. If you see one, it's free points.

### Bebop / Bebop 2 — Weak WPA2 + No PMF

**Key weaknesses:**
- WPA2 with weak/predictable default passwords
- **No 802.11w (PMF)** — deauth attacks work
- ARP cache poisoning demonstrated
- Buffer overflow vulnerabilities reported
- FTP server exposes media files

**Attack chain:**
1. `airodump-ng` to identify the Bebop AP
2. Deauth + capture WPA2 handshake
3. Crack with `hashcat` or `aircrack-ng` (weak defaults = fast crack)
4. Connect to network
5. Access FTP for media, or send commands via Parrot SDK API

**Research findings (academic):**
- Replay attacks demonstrated against control protocol
- ARP spoofing enables MitM between controller and drone
- Video stream can be intercepted and modified in transit

### Anafi — Deauth Susceptibility

**CVE-2019-3944:** The Anafi drops its controller connection when hit with deauthentication frames.
- **Tested by:** Tenable (TRA-2019-22)
- **Fixed in:** Firmware 1.5.0 (partially — added reconnection logic, but deauth still causes temporary disconnection)
- **KTH research:** 7 out of 10 DoS attempts succeeded against Anafi in controlled testing

**CVE-2019-3945:** Malformed date field in SDK command crashes the drone's web server.
- Web server runs on the drone for SDK interaction
- Send a crafted HTTP request → crash the server → loss of control interface

**WiFi configuration:**
- WPA2 with user-settable password (but may ship with weak defaults)
- No PMF support confirmed in current firmware
- SDK API is accessible to any client on the WiFi network

---

## WiFi Deauthentication Across Drone Platforms

Deauthentication is the simplest and most reliable WiFi-based DoS against drones. It works because most drone WiFi implementations do not support 802.11w Protected Management Frames (PMF).

### Which Drones Are Vulnerable

| Platform | PMF Support | Deauth Vulnerable | Notes |
|---|---|---|---|
| Tello | No | **Yes** | Open network; deauth disconnects operator |
| AR.Drone | No | **Yes** | Open network |
| Bebop 2 | No | **Yes** | WPA2 but no PMF |
| Anafi | No | **Yes** (CVE-2019-3944) | Firmware 1.5.0 adds reconnect, doesn't prevent deauth |
| DJI Enhanced WiFi | Unknown | **Likely** | No PMF documentation found |
| DJI OcuSync | N/A | **N/A** | WiFi not used for control |
| Syma / Hubsan | No | **Yes** | Open network |

### Deauth Attack Execution

**Requirements:** WiFi adapter capable of monitor mode + packet injection (ALFA cards in the lab).

```bash
# Put adapter in monitor mode
sudo airmon-ng start wlan0

# Identify target drone AP
sudo airodump-ng wlan0mon

# Continuous deauth against drone AP (broadcast)
sudo aireplay-ng --deauth 0 -a <BSSID> wlan0mon

# Targeted deauth (specific client)
sudo aireplay-ng --deauth 0 -a <BSSID> -c <CLIENT_MAC> wlan0mon
```

**Using `mdk4` for more aggressive deauth:**
```bash
# Deauth all clients on a specific AP
sudo mdk4 wlan0mon d -B <BSSID>

# Deauth with randomized source addresses (harder to filter)
sudo mdk4 wlan0mon d -B <BSSID> -c 1
```

**Behavior on deauth:**
- **Tello:** Enters hover/land failsafe after losing controller connection
- **Parrot:** Varies by model — some hover, some land, AR.Drone may drift
- **DJI Enhanced WiFi:** Likely enters RTH (Return to Home) or land failsafe
- **Continuous deauth prevents reconnection** — the drone stays in failsafe

### Bypassing PMF (802.11w)

If a drone does implement PMF (rare in current models):

1. **Channel-switching attack (CSA):** Send spoofed Channel Switch Announcement frames. The client switches to the announced channel, breaking the connection. Works even with PMF because CSA frames are unauthenticated in most implementations.
2. **Rogue AP with higher signal:** Clone the SSID, broadcast at higher power on a different channel. Some clients will roam to the stronger signal.
3. **Association flooding:** Overwhelm the drone's AP with fake association requests, exhausting its client table.

**Note:** PMF bypass is theoretical for drone targets — no consumer drone has been confirmed to implement PMF robustly.

---

## Default AP Credentials by Platform

These are known factory defaults. Many operators never change them.

| Platform | Default SSID Pattern | Default Password | WiFi Security | Source |
|---|---|---|---|---|
| **Ryze Tello** | `TELLO-XXXXXX` | None (open) | None | Which? Lab 2021, manufacturer docs |
| **Parrot AR.Drone** | `ardrone_XXXXXX` | None (open) | None | Community testing |
| **Parrot AR.Drone 2** | `ardrone2_XXXXXX` | None (open) | None | Community testing |
| **Parrot Bebop** | `BebopDrone-XXXXXX` | Weak/predictable | WPA2 | Academic research |
| **Parrot Bebop 2** | `Bebop2-XXXXXX` | Weak/predictable | WPA2 | KTH thesis |
| **Parrot Anafi** | `Anafi-XXXXXX` | User-settable (weak default reported) | WPA2 | Tenable TRA-2019-22 |
| **DJI Spark** | `Spark-XXXXXX` | 8-char alphanumeric | WPA2 | DJI community |
| **DJI Mini SE** | Model-specific | 8-char alphanumeric | WPA2 | CVE-2023-6951 |
| **Syma X5SW** | `FPV_XXXXXX` | None (open) | None | Which? Lab 2021 |
| **Hubsan X4** | `Hubsan_XXXXXX` | None (open) | None | Community testing |
| **Holy Stone** | `HS_XXXXXX` | None (open) | None | Community testing |

**Recon shortcut:** Any SSID matching `TELLO-*`, `ardrone*`, `Bebop*`, `Spark-*`, `FPV_*`, `Hubsan_*`, or `HS_*` is almost certainly a drone AP.

---

## Unencrypted Telemetry over WiFi

### MAVLink over WiFi (UDP 14550/14555)

Open-source drones (ArduPilot, PX4) commonly use MAVLink over WiFi for ground station communication:

- **Transport:** UDP, typically port 14550 (GCS) or 14555 (companion)
- **Encryption:** None by default (MAVLink v1 has no encryption; v2 has optional signing but no encryption)
- **Content exposed:** GPS coordinates, altitude, battery state, RC channel values, sensor data, flight mode, system status
- **Command injection:** Send MAVLink commands from any client on the same network

**WiFi-connected MAVLink setups:**
- Companion computers (Raspberry Pi, Jetson) running `mavproxy` with WiFi AP
- WiFi telemetry modules (ESP8266/ESP32-based, e.g., mRo SiK WiFi, ESP-Link)
- Ground station laptops connecting to drone AP over WiFi

**Capture example:**
```bash
# Capture MAVLink on the default port
tcpdump -i wlan0 -w mavlink.pcap udp port 14550
# Analyze in Wireshark with MAVLink dissector
```

### Video Feeds

| Platform | Video Protocol | Encryption | Capture Tool |
|---|---|---|---|
| Tello | H.264 over UDP 11111 | None | `ffplay`, `ffmpeg` |
| AR.Drone | Raw video UDP 5555 | None | `ffplay`, custom |
| Bebop 2 | H.264 over RTP | None (relies on WPA2) | `ffplay`, Wireshark |
| Anafi | H.264/H.265 over RTP | None (relies on WPA2) | `ffplay`, Wireshark |
| DJI Enhanced WiFi | Proprietary UDP | None (relies on WPA2) | Reverse engineering needed |
| DJI OcuSync | Proprietary | **AES-256** | Not feasible |
| MAVLink drones | GStreamer/RTSP | Typically none | `ffplay`, VLC |

### Companion Computer Web Interfaces

Some drone companion computers expose web interfaces on the WiFi network:

- **MAVProxy web UI:** HTTP on port 5000+ (no auth by default)
- **QGroundControl server:** May expose a web interface
- **Custom dashboards:** Flask/Node.js apps for telemetry display (often unprotected)
- **SSH:** If the companion computer runs Linux with default credentials

**Risk:** Once on the drone's WiFi network, port scan the drone IP range. Exposed services are common.

---

## WiFi-Specific CVEs for Drone Interfaces

| CVE | Platform | Description | Impact | PoC |
|---|---|---|---|---|
| **CVE-2019-3944** | Parrot Anafi | Deauth frame causes controller disconnect | DoS | Tenable PoC |
| **CVE-2019-3945** | Parrot Anafi | SDK date field crashes web server | DoS | Tenable PoC |
| **CVE-2023-6951** | DJI (QuickTransfer) | WPA2 key derivable from observable params | Network access | Unknown |
| **CVE-2026-77812** | DJI (multiple) | WiFi creds leaked over BLE in cleartext | Credential theft | Unknown |
| **CVE-2026-78306** | DJI (16 models) | BT commands change WiFi SSID/password | DoS / lockout | PoC available |
| N/A | Ryze Tello | No default WiFi password, no control auth | Full takeover | Trivial |
| N/A | Parrot AR.Drone | Open WiFi, telnet root, FTP | Full takeover | Trivial |
| N/A | Parrot Bebop | Weak WPA2, no PMF, ARP spoof | MitM / takeover | Academic |

---

## WiFi Recon Playbook

Quick-reference procedure for the first 5 minutes at a CTF when facing unknown drone targets.

### Step 1: Passive Scan (30 seconds)
```bash
# Monitor mode
sudo airmon-ng start wlan0

# Scan all channels, log SSIDs
sudo airodump-ng wlan0mon --write ctf-recon --output-format csv,pcap
```

**Look for:** `TELLO-*`, `ardrone*`, `Bebop*`, `Anafi-*`, `Spark-*`, `DJI-*`, `FPV_*`, `Hubsan_*`, `HS_*`

### Step 2: Identify Target Type (30 seconds)
- **Open AP (no encryption):** Tello, AR.Drone, toy drones → connect directly
- **WPA2 without PMF:** Bebop, Anafi, DJI Enhanced WiFi → PMKID or handshake crack
- **WPA2 with PMF:** Rare; try CSA attack
- **No drone-like SSID visible:** Drones may use dedicated RC link (ELRS, OcuSync, Crossfire) → switch to RF analysis

### Step 3: Execute (Based on Target Type)

**Open AP:**
```bash
# Connect
nmcli device wifi connect "TELLO-XXXXXX"
# Immediately test control
echo "command" | nc -u -w1 192.168.10.1 8889
```

**WPA2 (PMKID capture — fastest path, no client needed):**
```bash
# Capture PMKID
sudo hcxdumptool -i wlan0mon --enable_status=1 -o pmkid.pcapng \
  --filterlist_ap=<BSSID> --filtermode=2
# Convert
hcxpcapngtool pmkid.pcapng -o hash.22000
# Crack
hashcat -m 22000 hash.22000 -a 3 '?l?l?l?l?l?l?l?l' # 8-char lowercase
```

**WPA2 (Handshake capture — requires connected client):**
```bash
# Deauth to force handshake
sudo aireplay-ng --deauth 5 -a <BSSID> wlan0mon
# Capture handshake (airodump-ng should show "WPA handshake" in header)
# Crack
aircrack-ng -w /usr/share/wordlists/rockyou.txt ctf-recon-01.cap
```

### Step 4: Post-Access Actions (Per Platform)

| Platform | First Command | Goal |
|---|---|---|
| Tello | `echo "command" \| nc -u 192.168.10.1 8889` | Enter SDK mode |
| AR.Drone | `telnet 192.168.1.1` | Root shell |
| Bebop | `curl http://192.168.42.1/` | Check SDK/API |
| Anafi | `curl http://192.168.42.1/api/v1/info` | Drone info via SDK |
| MAVLink | `mavproxy.py --master=udp:DRONE_IP:14550` | GCS connection |

---

## Tools Reference

| Tool | Purpose | Install |
|---|---|---|
| `aircrack-ng` suite | WiFi capture, injection, cracking | `apt install aircrack-ng` |
| `hcxdumptool` | PMKID capture | `apt install hcxdumptool` |
| `hcxpcapngtool` | Convert pcapng to hashcat format | `apt install hcxtools` |
| `hashcat` | GPU-accelerated password cracking | `apt install hashcat` |
| `mdk4` | Advanced WiFi DoS (deauth, beacon flood) | `apt install mdk4` |
| `Wireshark` | Packet analysis, MAVLink dissector | `apt install wireshark` |
| `ffmpeg` / `ffplay` | Video stream capture and playback | `apt install ffmpeg` |
| `mavproxy` | MAVLink ground station, command injection | `pip install mavproxy` |
| `pymavlink` | MAVLink packet crafting (Python) | `pip install pymavlink` |
| `nmap` | Port scan drone network for exposed services | `apt install nmap` |
| `bettercap` | WiFi + BLE MitM framework | `apt install bettercap` |

---

## Sources

- Tenable TRA-2019-22 — Parrot Anafi WiFi DoS (CVE-2019-3944, CVE-2019-3945)
- KTH Thesis: Parrot Anafi Penetration Testing (diva2:1484619)
- KTH: DoS Attacks Against Parrot Anafi (diva2:1601435)
- Which? Consumer Report 2021 — Drone WiFi Security Testing
- DJI Security Advisory: CVE-2023-6951
- DJI BLE Research: CVE-2026-78306 (Yousef & Samhi)
- DJI BLE WiFi Credential Leak: CVE-2026-77812
- NDSS 2023: DJI DroneID — Schiller et al.
- Ryze Tello SDK Documentation
- Tello community security research
- Parrot AR.Drone security assessment — multiple community sources
- ExpressLRS binding phrase vulnerability disclosure (2022)
- hashcat documentation — WPA/WPA2 cracking modes
- aircrack-ng wiki — deauthentication and handshake capture
