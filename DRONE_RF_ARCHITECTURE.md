# Drone RF → Hardware Identity — Architecture

**Goal:** identify what's in the air by a *hardware-unique* ID (Wi-Fi BSSID,
DJI Drone-ID serial/operator, ELRS presence) — **captured entirely by the
SDR** (B205mini). No onboard NIC, BLE, or sensors.

## The design principle (why this shape)

Hand-rolling a protocol demod in Python is the wrong fight — it's where the
project got stuck (the Python 802.11 L-SIG path never validated because the
B205mini RX can't cleanly deliver a 20 MHz beacon, and demod-vs-noise is a
calibration rabbit hole with no ground truth). The fix: **let a proven,
citable open-source receiver do the protocol decode; the SDR is the shared
front-end that captures the RF; the webui shows whatever each decoder
produces, and honestly says "unconfirmed" where no decoder exists.**

```
                 B205mini (the ONLY RF source)
                        │  IQ captures
        ┌───────────────┼───────────────────────────┐
        ▼               ▼                           ▼
   RF presence     PROVEN DECODERS (per protocol)   presence only
   + signature     (no hand-rolled demods)          (no decoder exists)
   (FFT / chirp
    math — already
    correct)
        │               │
        ▼               ▼
   webui "airborne ID" table
   one row per tracked signal, identity filled only when a
   proven decoder produced it; otherwise "RF present, unconfirmed"
```

## Protocol → proven-receiver map

| Protocol | What we extract | Proven receiver | Layer | Status |
|---|---|---|---|---|
| **802.11 Wi-Fi** | AP BSSID (hardware MAC) + SSID | **gr-ieee802-11** (C++, GNU Radio) + `json_sink` block | SDR → C++ flowgraph | `apps/wifi_json_rx.py` built; build on laptop |
| **DJI Drone-ID / OcuSync** | drone position, operator ID, speed/alt, serial | **RUB-SysSec/DroneSecurity** (NDSS'23, Python) | SDR → Python ref | `apps/droneid_json_rx.py` built; runs in enclave on sample |
| **ELRS / LoRa** (control link) | LoRa-confirmed presence; CRSF payload | provable chirp math (ours) + sigrok CRSF for payload | SDR → math/decoder | chirp = CONFIRMED; CRSF = byte-level on demod'd bytes |
| **Crossfire / DSMx / SiK** | FHSS presence + payload | sigrok CRSF / GRC DSMx (byte-level) | SDR → decoder | presence via FHSS signature; payload on demod |
| **Open Drone ID** | serial / operator over BLE/Wi-Fi | (BLE — *not* an SDR band) | N/A — out of SDR scope | the SDR can't see BLE; reported as such, not faked |

Key correction from earlier: **OcuSync is NOT "proprietary, no decoder."**
DroneSecurity (NDSS'23, ★1322) is a working OcuSync 2.0 Drone-ID receiver
*tested on the B205-mini* — so the drone layer goes from "signature only" to a
full, citable decode.

## The two SDR-only receiver bridges (webui subprocesses)

Both follow the same pattern: a standalone script that owns the B205,
captures IQ, runs the proven decoder, and prints **one JSON line per decoded
frame to stdout**. The webui spawns the relevant one (per mode), reads
stdout, and merges rows into the airborne-ID table.

### `apps/wifi_json_rx.py` — Wi-Fi BSSID
- GNU Radio flowgraph: `usrp → sync_short → sync_long → fft → frame_equalizer
  → decode_mac → json_sink` (headless, no GUI).
- `json_sink` is a **new C++ block added to gr-ieee802-11** (see
  `gr-ieee802-11/README_JSON_SINK.md`): it taps `decode_mac`'s message output,
  parses the 802.11 header, prints `{"ch","bssid","src","dst","ssid",...}`.
- Tune: `--freq 2437e6 --srate 20e6 --gain 40 --channel 6`.

### `apps/droneid_json_rx.py` — DJI Drone-ID
- Mirrors DroneSecurity's own live receiver: hops the **16 known Drone-ID
  carriers** (2.4 GHz 2414.5–2474.5 MHz; 5.8 GHz 5721.5–5831.5 MHz), 50 Msps,
  RX2, and runs `SpectrumCapture → Packet → Decoder → DroneIDPacket`.
- Prints `{"ts","carrier_mhz","lat","lon","alt","speed","drone_id",...}` per
  decoded frame.
- **All SDR** — no NIC/BLE. If a drone isn't in RF range, it simply doesn't
  appear (honest absence, not a guess).

## Device ownership (one B205, one owner at a time)

The B205mini is a single-owner device. The webui therefore has a **mode
switch**:
- **Presence** — the Python `DroneScanner` (ELRS/LoRa/FHSS/video signatures)
  owns the radio.
- **Wi-Fi ID** — spawns `wifi_json_rx.py`; the Python scanner yields the radio.
- **Drone ID** — spawns `droneid_json_rx.py`; same hand-off.

Only one runs at a time. (A true simultaneous sweep would need a channel
scheduler — deferred until the decoders are proven on hardware.)

## Verified vs. needs-the-laptop

- ✅ **DroneSecurity runs in the enclave** (Python 3.13 + a numpy-only scipy
  shim, `DroneSecurity/_scipy_shim.py`): on the bundled DJI Mini 2 sample it
  **detects all 10 Drone-ID frames, estimates the CFO coherently, and demods
  symbols.** The shim is a compat layer; the laptop (Python 3.10, real scipy
  1.8 as the project pins) runs it **stock, no shim**.
- ✅ **Webui ingestion path** (`droneid_json_rx.decode_chunk`) executes
  end-to-end on a real capture — the seam the webui will use.
- ⚠️ **`wifi_json_rx.py`** is built from the verified block signatures + the
  real `wifi_rx.grc` connection list, but **not run-verified** (no GNU Radio in
  the enclave). The `wifi_loopback.grc` test on the laptop is the branch
  point: if it decodes, the Wi-Fi demod is proven and any ch-6 miss is purely
  B205 RF.
- ⚠️ **`json_sink` C++ block** — standard PMT message block (matched to
  `decode_mac`'s exact `message_port_pub`/`cons(meta,blob)` format); compiles
  in the module build.

## Honest limits (no fabricated IDs)

- The B205mini USB 2.0 RX caps ~8 MS/s even when 20 Msps is requested (the
  50 Msps Drone-ID path is affected too — DroneSecurity is a
  proof-of-concept, "not optimized for bad RF"; that's a known limitation of
  the reference, not of our code).
- Where no proven decoder exists (OcuSync *video feed*, proprietary telemetry
  payloads), the tool reports the **RF signature** and marks the protocol
  **unconfirmed** — it does not invent an ID.
- ELRS/LoRa: the chirp detector is the one *provable* detector (the LoRa PHY
  math); CRSF *payload* decode is a separate, byte-level step on the demod'd
  bytes (sigrok), not an RF signature.
