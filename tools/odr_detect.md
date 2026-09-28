# Open Drone ID (Remote ID) capture & decode — notes

Companion to `odr_detect.py`. Verified against:

- `opendroneid/opendroneid-core-c` (cloned at `../odr_core`, tag-era master
  `6484f26`) — the reference C implementation. All message decoders in
  `odr_detect.py` are ports of `libopendroneid/opendroneid.c`; the Wi-Fi
  frame layouts are ports of `libopendroneid/wifi.c` + `odid_wifi.h`.
- `opendroneid/specs` (cloned at `../specs`) — ASTM F3411 / ASD-STAN
  prEN 4709-002 spec PDFs (0.64.3).
- `opendroneid/receiver-android` (cloned at `../receiver-android`) —
  reference receiver showing the BLE filter (service-data UUID 0xFFFA,
  AD code 0x0D) and the BT4/BT5 transport distinction.

## 1. How Open Drone ID is transmitted

Remote ID (US ASTM F3411-19 / FAA Remote ID rule; EU ASD-STAN prEN
4709-002) requires the UAS to broadcast identity + kinematic state at
>= 1 Hz to nearby parties. The **message layer is transport-agnostic**:
a fixed 25-byte message format, and a "Message Pack" (type 0xF) that
carries up to 9 messages (227 bytes max) in one transmission.

### The 25-byte message (all fields little-endian)

Byte 0 is `[MessageType:4][ProtoVersion:4]` with the message type in the
**high** nibble (LSB-first bitfield packing: version in bits 0-3, type in
bits 4-7). Protocol versions in the wild: 0 = F3411-19, 1 = prEN
4709-002 P1, 2 = F3411 v1.1 (adds System timestamp, EMERGENCY status,
extended-status desc type).

| type | name | identity/state carried |
|------|------|------------------------|
| 0 | **Basic ID** | UAS identity: ID type (1 = serial number, 2 = CAA registration, 3 = UTM-assigned UUID, 4 = specific session ID) + 20-byte UAS ID + UA type (multicopter, fixed-wing, ...) |
| 1 | **Location** | lat/lon (1e-7 deg, int32), baro + geo altitude (0.5 m, offset -1000 m), height over ground/takeoff, direction (deg, with E/W bit), horizontal speed (0.25/0.75 m/s steps), vertical speed (0.5 m/s signed), status (ground/airborne/emergency/...), accuracy fields, timestamp (0.1 s since the hour) |
| 2 | **Auth** | authentication data, paged (up to 16 pages, 255-byte payload): UAS ID signature, operator ID signature, message-set signature, or network Remote ID attestation |
| 3 | **Self ID** | free-text identifier (23 bytes): e.g. callsign, or an emergency/extended-status text |
| 4 | **System** | operator location (takeoff/live-GNSS/fixed), operator geo altitude, area radius/ceiling/floor, EU classification (category Open/Specific/Certified, class C0-C6), timestamp |
| 5 | **Operator ID** | operator identifier string (20 bytes) |
| 0xF | **Pack** | header `[0xFx][25][N]` + N x 25-byte messages — the unit that actually goes over BLE/Wi-Fi |

So: **drone serial number = Basic ID (type 1/2); operator location =
System; altitude/speed/position = Location; authentication = Auth
(pages).** A compliant transmitter cycles Basic ID, Location, System,
Operator ID (and Auth if it signs) inside successive packs.

### Transport 1: Bluetooth LE advertising (the common one)

- PHY: **BLE** in the 2.4 GHz ISM band (channels 2402-2480 MHz), 1 MHz
  GFSK for LE 1M (the "2.4 GHz PHY"), or LE Coded S2/S8 (BT 5) for range.
  This is a completely different PHY from ELRS (LoRa SF5-8 / FLRC at
  800 kHz BW) — an ELRS SDR capture sees it only as in-band noise.
- Format: legacy (BT 4, 31-byte adv payload) or **extended advertising**
  (BT 5, up to 255-byte payload — required in practice because a full
  227-byte pack + counter does not fit in 31 bytes).
- Identification: AD structure type **0x16 (Service Data - 16-bit UUID)**
  with UUID **0xFFFA** (ASTM International, assigned for Remote ID) and
  AD application code **0x0D** ("Open Drone ID").
- Payload after the AD code: **1 byte message counter**, then the
  **Message Pack** (FRDID framing).
- Rate: >= 1 Hz, typically 1-2 s advertising intervals.
- Note: the BLE path carries **no CRC** beyond the link layer; BT 5
  extended advertising adds no FEC, which is why Auth pages can carry
  optional FEC bytes (see the C header comment on `ODID_Auth_data`).

### Transport 2: Wi-Fi (802.11)

Same 2.4 GHz band, two frame flavors (both carry the same
`[counter][Message Pack]` service info):

- **Wi-Fi NAN** (Neighbor Awareness Networking, ASTM-recommended for the
  "Direct Remote ID" use case): 802.11 management **Action** frame,
  DA = `51-6F-9A-01-00-00` (NAN Network ID), NAN Service Discovery
  (category 0x04, action 0x09, OUI `50-6F-9A` Wi-Fi Alliance, type
  0x13), service descriptor attribute (attr id 0x03, service id
  `88-69-19-9D-92-09`, instance 0x01, control 0x10), then service info.
  NAN cluster ID is fixed to `50-6F-9A-01-00-FF` by the spec.
- **Wi-Fi Beacon**: normal 802.11 Beacon (any SSID, e.g. "DroneID...")
  carrying a **vendor-specific IE** (element id 0xDD, OUI `FA-0B-BC`
  ASD-STAN, type 0x0D) with the service info. This is the easiest Wi-Fi
  flavor to capture (a normal monitor-mode adapter sees it; NAN needs a
  NAN-capable adapter or a pcap from one).

Both transports are optional per regulation (one is mandatory; many
airframes do BLE-only, some both).

## 2. `drone_scan.py` — overarching supervisor (start here)

One command starts the whole detector and gives a single Web UI on one
port. It supervises the ODID capture and (optionally) the ELRS decoder as
child processes and merges both into one tracker/UI. If a subsystem's
dependency is missing it is skipped with a note, not a crash. Ctrl-C stops
everything cleanly; a crashed ELRS child is auto-restarted (≤5×).

```sh
sudo python3 tools/drone_scan.py                       # ODID (BLE/btmon) + UI :10000
sudo python3 tools/drone_scan.py --elrs --elrs-mode bind          # + ELRS bind decode (no UID)
python3  tools/drone_scan.py --elrs --elrs-mode live --uid A1B2C3D4E5F6  # + bound link
python3  tools/drone_scan.py --odid-source replay                 # no radio: synthetic ODID
python3  tools/drone_scan.py --elrs --elrs-pkts-replay t.jsonl    # no SDR: synthetic ELRS links
```

Then open **http://localhost:10000**. The left column lists ODID drones
(identified by serial/registration), the middle column shows the selected
drone's full parameter set **and** an "ELRS links" panel (UID, OTA
versions, LoRa SF, packet types, last packet) below it, and a live event
feed (both protocols) is available. Both streams tee to jsonl
(`odr_scan_<ts>.jsonl`, `elrs_pkts_<ts>.jsonl`).

What it can and can't unify: it supervises the two very different capture
front-ends (BLE/btmon vs. SDR/UHD-LoRa) and merges their *decoded* events
into one UI, but they remain distinct data — drones (ODID regulatory ID)
vs. links (ELRS RF control), shown in separate panels, not merged rows.

## 3. `odr_web.py` — live Web UI (standalone)

Zero-dependency (Python 3.8+ stdlib). Starts a capture and an HTTP server;
open the browser and you see every drone being identified in real time.

```sh
sudo python3 tools/odr_web.py --port 10000                  # BLE via btmon (default)
sudo python3 tools/odr_web.py --port 10000 --source replay  # demo data, no radio
python3 tools/odr_web.py --port 10000 --source pcap -f wifi.pcap
```

Then open **http://localhost:10000** (or `http://<laptop-ip>:10000` from
another device on the LAN — it binds 0.0.0.0).

The UI shows, per drone: identity (serial / CAA registration / UUID + UA
type), live position (lat/lon, geo + baro altitude, height over
ground/takeoff), direction + horizontal/vertical speed, status
(ground/airborne/emergency), operator ID + operator location + area
radius/ceiling/floor, EU class, auth pages, transport, source MAC, RSSI,
and last-seen age. Drones are keyed by Basic ID (serial/registration),
falling back to source MAC, then position hash. A live event feed and a
`/api/state` JSON endpoint (for scripting) are also served. Events are
tee'd to jsonl (default `odr_web_<ts>.jsonl`).

Use `--source replay` to verify the whole pipeline on a machine with no
drone in range: it generates a synthetic moving ODID beacon through the
identical decode path.

## 4. `odr_detect.py` — capture + decode (CLI / library)

```
python3 tools/odr_detect.py --selftest
python3 tools/odr_detect.py --source btmon-live            # live BLE (needs root)
python3 tools/odr_detect.py --source btmon -f btmon.log    # from a btmon text log
python3 tools/odr_detect.py --source pcap -f cap24.pcap    # Wi-Fi (or BLE) pcap
python3 tools/odr_detect.py --source stdin < btmon.log     # generic line input
```

- Stdlib-only (Python 3.8+). For `--source pcap` it uses scapy if
  importable, otherwise a built-in libpcap reader (DLT 113 BT LE link
  layer, DLT 127/1 802.11; radiotap headers are stripped heuristically).
- Each decoded frame: printed in real time **and** tee'd as one JSON
  object per line to `--out` (default `odr_<ts>.jsonl`).
- `--quiet` suppresses console output (jsonl only).
- The self-test builds a BLE adv frame, a NAN action frame, and a beacon
  frame with known content, runs them through the extractors + decoders,
  and asserts the decoded fields.

## 5. Capture method tradeoffs (Linux laptop)

### Option A: Bluetooth adapter + BlueZ `btmon` (recommended for BLE)

What you get: the **exact advertising payload** (AD structures, full
extended-adv length), with timestamps and RSSI. btmon taps the HCI
monitor interface, so it captures **LE Coded / BT 5 extended
advertising** that GUI tools and `hcitool lescan` truncate or drop
(legacy scanners only show the 31-byte truncated payload, and most
phone-class beacons use extended adv).

Setup:

```sh
sudo apt install -y bluez
sudo systemctl enable bluetooth
bluetoothctl
  power on
  scan on      # (optional; scan works without pairing)
  exit
```

Capture (run as root; `-t` text mode, `-w` optional filter):

```sh
sudo btmon -t | tee /tmp/btmon.txt
```

Then decode either live (in another terminal) or offline:

```sh
python3 tools/odr_detect.py --source btmon-live --quiet --out /tmp/odr.jsonl
python3 tools/odr_detect.py --source btmon -f /tmp/btmon.txt
```

Caveats:

- btmon text lines print the **ACL data** variant: HCI packet header (4
  bytes) + event header (3) + LE Advertising Report event (14) + AD
  payload. `odr_detect.py` strips the 21-byte prefix (and retries the
  17-byte offset for event-only lines). If a given BlueZ version prints a
  different prefix, `--source stdin` with your own trimmed hex lines
  always works: each line is raw hex of the AD payload.
- You need a BT **4.2+** adapter for LE, and **BT 5** hardware to see
  extended/coded advertising reliably. USB dongles (RTL8761B/8821CU,
  CSR8510) work; check `lsusb` + `hciconfig` (PHY Coded capability).
- Range: BLE is ~30-100 m in practice (more with Coded S8), vs. ELRS
  kilometers. You must be close to the aircraft or its control link.
- No RF-level data (no power, no channel, no IQ) — but you get clean
  payload bytes and per-advert timestamps, which is all the decode needs.

### Option B: Wi-Fi adapter in monitor mode (for the Wi-Fi transport)

```sh
sudo apt install -y wireshark
ip link set wlan0 down
iw dev wlan0 set type monitor
ip link set wlan0 up
sudo tcpdump -i wlan0 -w /tmp/wifi-24.pcap 'port 11 or port 16450'
```

Beacons on channels 1/6/11 are captured directly; for a full-band sweep,
channel-hop with `iw dev wlan0 set channel 11`. Then:

```sh
python3 tools/odr_detect.py --source pcap -f /tmp/wifi-24.pcap
```

Caveats:

- **NAN frames are not visible to a stock monitor-mode adapter** — most
  NICs drop 802.11 action frames not destined for them, and NAN
  specifically requires NAN firmware support (only a few adapters: some
  IPQ4019/QCA-based devices). If the aircraft broadcasts NAN-only, a
  plain `tcpdump` capture will show nothing. The Beacon flavor IS
  visible. This is the main reason BLE-via-btmon is the more reliable
  laptop path.
- Radiotap/PRISM headers: `odr_detect.py` strips a radiotap header
  heuristically (header-length field). Wireshark-exported pcaps without
  the radiotap header also work (it parses from offset 0).

### Option C: SDR (why NOT, for ODID)

An ELRS-style SDR capture (B210/B205mini + UHD) can **see** ODID as
energy in the 2.4 GHz band, but it is the wrong tool:

- BLE is GFSK 1 MHz + access-code preamble + Whitening + CRC; decoding
  it from IQ means reimplementing a BLE link layer (gatt-ble-uhd,
  bt-sdr, Ubertooth-based captures do this — see `b4tjp/PoC-Bluetooth-
  Low-Energy`, `Ubertooth` for prior art). Wi-Fi is even worse: OFDM
  802.11a/b PHY from IQ is a full radio implementation (gnuradio-combo
  has blocks; it is not a weekend job).
- You already own the SDR for ELRS (LoRa). For ODID the **radio is not
  the interesting part** — the identity/kinematic decoding is, and the
  laptop's BT/Wi-Fi stack gives you the payload bytes for free.
- Where the SDR *does* help: coexistence analysis (how a 800 kHz-BW
  LoRa hop looks against BLE channels 2402-2480), geolocation of a BLE
  beacon via AoA/TDOA with multiple SDRs, or capturing a transmitter you
  are not allowed to put a BT adapter next to. Out of scope here.

**Rule of thumb: btmon for BLE (default), monitor-mode Wi-Fi for beacon
flavor, SDR only for RF-level questions.**

## 6. Reference pointers

- C reference (parse logic): `../odr_core/libopendroneid/opendroneid.c`
  (`decode*Message`, `decodeMessagePack`, `checkPackContent`),
  `opendroneid.h` (structs/endianness), `wifi.c` (NAN/beacon build +
  receive), `odid_wifi.h` (frame structs).
- Android receiver (BLE filter constants):
  `../receiver-android/Android/app/src/main/java/org/opendroneid/android/
  bluetooth/BluetoothScanner.java` (SERVICE_UUID 0xFFFA, AD code 0x0D).
- Specs: `../specs/OpenDroneID_Bluetooth_0.64.3.pdf`,
  `OpenDroneID_Message_0.64.3.pdf`, `OpenDroneID_Wifi_0.64.3.pdf`.
- Known-good external decoders (cross-checks): Doodle Labs
  `OpenDroneID-Decoder` (JS), `sdr-odid`-style btmon greps.
