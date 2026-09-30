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

### Live web dashboard (Kismet-style)

A real-time multi-band waterfall with **click-to-lock** band tabs (auto-cycles
2.4 → 5.8 → sub-1G → 433; click a band to lock on it, click again / "Resume"
to release). Pure stdlib — no extra dependencies.

```bash
python -m elrs_sdr webui                 # http://localhost:8090
python -m elrs_sdr webui --port 9000
```

Environment overrides: `ELRS_DEVICE` (default `type=b200`), `ELRS_GAIN`
(default 30), `ELRS_DWELL` (default 0.15 s).

> **Honest limits.** One B205mini can't sample all bands at once — the scanner
> *time-slices* between them (cycling waterfall, not 4 simultaneous scopes).
> Vendor-level ID (e.g. "it's definitely a DJI Mavic 3") is **not** claimed:
> OFDM/Wi-Fi and FHSS are identified at the *protocol-family* level with a
> confidence label, and LoRa/ELRS is the only high-confidence positive.
> 5.8 GHz sits at the edge of the B205's range (works, noisier).

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
cli.py        subcommands: info/scan/capture/decode/monitor/bind-capture/tx
  │
radio.py      RadioDevice — ONE UHD wrapper, symmetric RX+TX
  │           rx_stream()/capture()  ↔  tx_stream()/tx_tune()
  │           .complex8 file I/O (SDR# / GQRtx compatible)
  │
lora.py       LoRa PHY — chirp mod/demod, IQ inversion, preamble+sync detect
  │           modulate_frame()  ↔  demodulate_frame()   [needs capture validation]
  │
ota.py        OTA packet model — build_*/decode_*/validate_*/generate_*
  │           the SAME functions serve RX (validate+unpack) and TX (build+pack)
  │
crc.py        Crc2Byte + Crc8 — exact ports
fhss.py       LCG + domains + hop-sequence builder
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
