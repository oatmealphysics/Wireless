# elrs-sdr

Capture, decode — and (future) transmit — ExpressLRS drone signals with an
Ettus B210 / B205mini.

Built from a first-hand read of the [ExpressLRS](https://github.com/ExpressLRS/ExpressLRS)
source (`main`), so the protocol layers are exact ports, not guesses.

## What this is / isn't

| Layer | Status | Why |
|---|---|---|
| CRC (Crc2Byte 14/16-bit, CRC8 0xD5) | ✅ exact port, self-tested | byte-for-byte from `src/lib/CRC` |
| OTA packet model (8-byte std / 13-byte full) | ✅ exact port, self-tested | from `src/lib/OTA` |
| UID-derived CRC init + binding-mode init | ✅ exact port, self-tested | `OtaUpdateCrcInitFromUid` / `EnterBindingMode` |
| Channel pack/unpack (4×10-bit in 5 bytes) | ✅ exact port, self-tested | `Pack/UnpackUInt11ToChannels4x10` |
| FHSS LCG + regulatory domains + hop sequence | ✅ exact port, self-tested | `src/lib/FHSS` |
| LoRa PHY (SF5-8 @ 800 kHz, chirp mod/demod) | ⚠️ first-order | Semtech-proprietary bit mapping; **needs a real capture to validate** |
| UHD transport (RX now, TX built but gated) | ✅ API-complete | direction-symmetric by design |

The LoRa demod is the single layer that can't be proven on a desk: the
SF5/SF6 bit-per-symbol convention, BPSK differencing, and interleaving are
Semtech implementation details. The code is structured so that the **first real
capture** becomes a unit test for `lora.py` — everything above it is already
validated against the exact CRC.

## Repository layout

```
elrs_sdr/            the Python package (the `elrs-sdr` command + webui)
  core/              ELRS decode — single source of truth (exact reference ports)
    crc.py             Crc2Byte (CRC-14 0x2E57 / CRC-16 0x3D65), OTA CRC init
    ota.py             OTA packet model (sync/rc/linkstats, pack/unpack, validate)
    fhss.py            FHSS sequence (LCG PRNG + block-shuffle), ISM2G4 map
    lora.py            software LoRa demod (preamble correlate + symbol demod)
  radio.py           UHD device wrapper (RX+TX symmetric) + .complex8 I/O
  dronscan.py        multi-band presence + protocol scanner
  wifi.py  positivedet.py   band/channel tables + PHY detectors
  opendroneid.py     Remote ID (ASTM F3411) decoder + synthesiser  [identity]
  fusion.py          sensor-fusion: sources -> events -> identity-keyed TrackTable
  webui.py           unified DroneWatch dashboard (all sources fused)  [identity]
  log.py  cli.py  selftest.py  uhd_bootstrap.py
```

The **goal** is one dashboard showing every drone around you, its protocol, and
its identifiers. No single receiver sees all drones, so three independent
sources feed one fusion layer (`fusion.py` → `TrackTable`), and `webui.py`
renders the merged, identity-keyed table:

| Source | Receives | Gives identity? |
|---|---|---|
| **Remote ID** (`opendroneid`) | ASTM F3411 over BLE + Wi-Fi beacon/NAN | **Yes** — serial, operator, position |
| **SDR scan** (`dronescan`+`positivedet`) | band sweep on the USRP | Mostly no — presence + **protocol** |
| **DJI DroneID** (`fusion.DjiDroneIdSource`) | DJI OcuSync, SDR-only | **Yes** — DJI serial + operator/drone position |

The Python package (`elrs_sdr/`) and the C++ tool (`cpp/`) are **two
implementations of the same reference ports** — C++ for low-latency live
capture on the laptop, Python for the webui, offline decoding, and testing.
`tools/` holds standalone scripts that don't belong in the package; `lora_ref.py`
is the proven numpy LoRa generator the C++ demod cross-validates against.

## The decoding barrier (read this first)

ELRS validates each frame with a CRC whose **init is derived from the link's
6-byte UID**:

```
OtaCrcInitializer = (UID[4]<<8 | UID[5]) ^ (OTA_VERSION_ID << 8)   # OTA_VERSION_ID = 4
```

Without the UID you can demodulate the LoRa frame but you **cannot** validate
its CRC, so you can't tell real packets from noise. Two on-ramps:

1. **Bind-mode capture (easiest).** Put the TX in bind mode. In bind mode the
   CRC init collapses to `OTA_VERSION_ID` (no UID) and the nonce is forced 0,
   so frames validate *without knowing the UID*. The sync packet then carries
   `UID4`/`UID5` directly. This is what `bind-capture` does.
2. **Known UID.** If you already have the link UID (e.g. from a bound RX),
   pass `--uid <hex>` and frames validate normally.

`OtaGetUidSeed` (the FHSS-seed derivation) also depends on the UID, so
reconstructing the *hop sequence* for a moving link needs the full UID — bind
mode gives you the two low bytes; the other four arrive over MSP during a real
bind.

## Install (on the machine with the radio)

```bash
# UHD with python bindings (Debian/Ubuntu)
sudo apt install uhd-host
# ensure `import uhd` works in your python:
python3 -c "import uhd; print(uhd.get_version_string())"

# USB access for a non-root user (one-time; then re-plug the radio)
sudo cp /usr/lib/uhd/utils/uhd-usrp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# project deps
cd elrs-sdr
python3 -m venv .venv
.venv/bin/pip install numpy
.venv/bin/pip install -e .        # optional: installs the `elrs-sdr` command
```

> **PEP 668 / "externally-managed-environment":** on Ubuntu 24.04 a *system*
> `pip install` is refused. Either use the venv above (recommended), or
> `pip install --break-system-packages numpy` (fine on a personal laptop).
> UHD itself comes from `apt` (`uhd-host` / `python3-uhd`), **not** pip.

Verify the stack **without hardware** (two levels):

```bash
# (1) protocol layers — CRC / UID-init / packet / FHSS / LoRa-chirp (no uhd needed)
.venv/bin/python -m elrs_sdr.selftest            # 29 checks should pass

# (2) the UHD transport path — proves constructor + RX-chain + recv work
#     against a mock 4.11 binding (no radio, no uhd needed)
.venv/bin/python tests/test_mock_uhd.py          # ALL MOCK-UHD 4.11 CHECKS PASSED
```

Both are hardware-independent. `selftest` validates the signal/protocol math;
`test_mock_uhd.py` validates the UHD wrapper (the part that is environment-
specific) against a simulated UHD 4.11 API, so it catches binding regressions
before you ever touch the radio.

## Usage

Run from the repo dir as `python -m elrs_sdr …` or, if you ran `pip install -e .`,
just `elrs-sdr …`.

```bash
# 1. Confirm the radio is visible
python -m elrs_sdr info

# 2. Find where the drone's hops are (2.4 GHz sweep)
python -m elrs_sdr scan --sweep 80 --dwell-ms 25

# 3. Capture IQ at a frequency of interest (opens in GQRtx / SDR# too)
python -m elrs_sdr capture --freq 2440e6 --srate 4e6 --gain 40 \
       --samples 4194304 --out cap.complex8

# 4. Decode a capture offline (binding-mode init by default)
python -m elrs_sdr decode cap.complex8 --rate 250Hz

#    …or with a known UID:
python -m elrs_sdr decode cap.complex8 --rate 250Hz --uid aabbccddeeff

# 5. Live decode loop
python -m elrs_sdr monitor --freq 2440e6 --rate 250Hz

# 6. Bind-mode capture to recover the link UID
python -m elrs_sdr bind-capture --samples 4194304 --out bind.complex8
python -m elrs_sdr decode bind.complex8 --rate 50Hz

# 7. TX — GATED, future
python -m elrs_sdr tx --rate 250Hz                 # refuses without the flag
python -m elrs_sdr tx --rate 250Hz \
       --i-am-authorized-to-transmit               # only if you own the link
```

### Drone presence + protocol scanner (multi-band)

A first step up the stack: *is there a drone nearby, and what protocol is it
using?* Sweeps the drone-relevant RF bands on the B205mini (all within its
100 kHz–6 GHz range) and classifies each band's energy by RF signature.

| Band | Range | Why |
|---|---|---|
| `2.4` | 2.40–2.48 GHz | ELRS, FrSky, Wi-Fi, most control links |
| `5.8` | 5.725–5.875 GHz | analog FPV video, 5 GHz edge |
| `sub1g` | 700–900 MHz | LoRa telemetry, 868/915 ELRS long-range, sub-1G RC |
| `433` | 425–440 MHz | older FHSS, some FrSky, telemetry |

**Tiered classifier** (honest about confidence):

| Signature | Detector | Protocols | Confidence |
|---|---|---|---|
| LoRa chirp | FFT matched-filter vs SF7 base chirp (both IQ polarities) | ELRS / CRSF-LoRa / 915-LR | **high** |
| OFDM / Wi-Fi | wide + multibin + known channel centers | DJI / consumer / FPV-WiFi | high/med |
| Analog video | 5.8 GHz wide constant band | analog FPV video | high |
| FHSS wideband | fast multi-bin hopping, non-OFDM, no chirp | FrSky-family / generic | med (family, not vendor) |
| unknown | presence only | — | low |

```bash
# One-shot scan (all bands), prints a presence + protocol report
python -m elrs_sdr dronescan

# Only certain bands, more observation cycles
python -m elrs_sdr dronescan --bands 2.4,5.8 --cycles 12 --dwell-s 0.2 --gain 35
```

### Unified drone dashboard (DroneWatch)

One page, fed by the **fusion layer**, showing every drone around you merged
across all sources (Remote ID / SDR protocol scan / DJI DroneID) in a single
**identity-keyed** table: status, kind, protocol, serial/identity, operator,
position, altitude, speed, frequency, RSSI. Pure stdlib — no build step.

```bash
# No hardware needed — synthetic Remote ID drones, to see the pipeline work:
python -m elrs_sdr webui --replay            # http://127.0.0.1:8090

# Live SDR band/protocol-scan source (needs the radio):
python -m elrs_sdr webui --sdr

# Add a DJI DroneID receiver (any cmd emitting one JSON line per frame):
python -m elrs_sdr webui --dji "python3 apps/droneid_json_rx.py --device type=b200"

# Fuse all three at once:
python -m elrs_sdr webui --sdr --dji "..." --replay

-v / --verbose  turns on debug logging (UHD binding-probe trail); -q = errors only.
```

**Honest limits.** No single receiver sees every drone — the SDR scan is the
presence+protocol layer; *identity* comes from Remote ID and DJI DroneID.
For an SDR-only operator the identity wins are **DJI DroneID** (SDR-native) and
— with a ~$15 monitor-mode Wi-Fi dongle + BLE adapter — **standards Remote ID**.
The ODID decoder is ready for whichever transport feeds it bytes. 5.8 GHz sits
at the edge of the B205's range (works, noisier); prefer a B210 on USB3 for
wideband DJI/Wi-Fi work.

### Air rates (2.4 GHz, SX1280)

| `--rate` | SF | BW | CR | OTA | interval |
|---|---|---|---|---|---|
| `500Hz` | 5 | 800k | 4/6 | 8-byte | 2 ms |
| `333Hz_8ch` | 5 | 800k | 4/8 | **13-byte** | 3 ms |
| `250Hz` | 6 | 800k | 4/8 | 8-byte | 4 ms |
| `150Hz` | 7 | 800k | 4/8 | 8-byte | 6.7 ms |
| `100Hz_8ch` | 7 | 800k | 4/8 | **13-byte** | 10 ms |
| `50Hz` | 8 | 800k | 4/8 | 8-byte | 20 ms |

800 kHz LoRa needs ≥ ~4 MS/s to be captured comfortably — the default `--srate 4e6`
is set for that. (The 1000Hz/500Hz 2.4 GHz rates are **FLRC**, not LoRa, and are
out of scope for the LoRa path.)

## Architecture (RX/TX symmetric by design)

```
elrs_sdr/
  cli.py        subcommands: info/scan/capture/decode/monitor/bind-capture/tx
    │
  radio.py      RadioDevice — ONE UHD wrapper, symmetric RX+TX
    │           rx_stream()/capture()  ↔  tx_stream()/tx_tune()
    │           .complex8 file I/O (SDR# / GQRtx compatible)
    │
  core/         the ELRS decode (single source of truth, exact reference ports)
    │           lora.py    LoRa PHY — chirp mod/demod, IQ inversion, preamble detect
    │                     modulate_frame()  ↔  demodulate_frame()  [needs capture]
    │           ota.py     OTA packet model — build_*/decode_*/validate_*/generate_*
    │                     the SAME functions serve RX (validate+unpack) and TX (pack)
    │           crc.py     Crc2Byte + Crc8 — exact ports
    │           fhss.py    LCG + domains + hop-sequence builder
```

Adding TX later is *wiring, not re-implementation*: `ota.build_*` and
`lora.modulate_frame` already produce the frames and waveforms; `radio.tx_stream`
already sends them. The only gates are the authorization flag and validating the
TX-side LoRa bit mapping against the same real capture used to validate RX.

## Legal / safety

This tool can demodulate and (eventually) transmit on the 2.4 GHz ISM band that
FPV drones use. **Capture is passive.** Transmission is disabled by default and
requires an explicit `--i-am-authorized-to-transmit` flag. Only transmit on
links you own, in an isolated/regulated environment, and within your local
regulatory limits.
