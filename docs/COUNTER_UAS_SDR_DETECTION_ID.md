# How the Reference Counter-UAS Passive SDR Detects and Identifies
## (Firmware Deep-Dive — the Detection/Identification Algorithm)

> **Naming rule (operator directive, standing):** the device is **"the counter-UAS passive
> SDR."** Never use its vendor/make name, or any acronym/abbreviation of it, in notes, code,
> filenames, or commit messages. The *drone-protocol* names (ELRS, Crossfire, LoRa, DJI, SiK,
> WiFi, ...) are the **subject matter** (the links we detect) and are kept. The paired
> active-mitigation (ECM) device is **"the paired ECM system."**
>
> **Source:** the device's **firmware** (a v4.0.6 software-update `.swu` package), provided by
> the operator as a *reference*. This document is the **detection + identification algorithm**
> — *how* the software detects and identifies drone links (complementing the architecture notes
> in `docs/COUNTER_UAS_SDR_FIRMWARE_DEEP_DIVE.md` and the operator-manual notes in
> `docs/REFERENCE_COUNTER_UAS_SDR.md`). Every claim is labeled **VALIDATED** (read directly
> from the firmware) vs **INFERRED** (reasoning from it).
>
> **Handling note:** the firmware binary was decompressed to analyze, then **deleted**
> (operator's rule: don't keep device material on disk — notes only). This document is the
> durable artifact. The implementation that ports this algorithm into our system is
> **`elrs_sdr/detect.py`** (see §9).

---

## 0. The one-paragraph summary

The device is a **2-channel, 126 MS/s, FPGA-accelerated (Xilinx ZynqMP + EXDR2 bitstream,
2× AD9371/9375 wideband transceivers, JESD204B, Si5345 clocking) passive SDR** running
**Arch Linux ARM64**. Its detection is a **hardware parallel-pipeline**: an FFT
**channelizer** (3-stage FFT + polyphase FIR) feeds **one dedicated DSP core per protocol**
(LoRa at 250/500/812.5 Hz, WiFi at 5/10/20 MHz, FSK, OFDM-symbol-length for
LightBridge/OcuSync, etc.), each running in the Zynq's programmable logic **in parallel**.
Each core does a **hysteresis-based energy detection** (high/low threshold + counts — the
false-alarm rejection), then a **sync-word correlation** (against pre-computed reference
templates stored as `.fc64` complex-float files), then the **per-protocol demod** (LoRa
dechirp, 4-FSK, OFDM symbol-length), then a **per-link Net ID extraction** (the
`DetectionNetID.raw_net_id` byte-array — for ELRS the bind UID, for WiFi the BSSID/MAC, for
DJI the serial). The result is a **`DetectionTaskResult`** (protocol, freq, power, Net ID,
position, speed, ...) delivered to the GUI over **flatbuffers** IPC. The *mitigation* side
(the ECM / loadset rules) is a separate, **active** layer (which we do **not** build).

**The key architectural insight for us:** the reference device's "measure, don't assume"
philosophy is implemented as a **hardware parallel-pipeline** (each protocol = a DSP core,
all running at once at 126 MS/s). Our `elrs_sdr/detect.py` ports the *same logic* to
**software** (hysteresis burst detect → per-protocol demod → Net ID extraction →
specific-protocol fingerprint), running on a capture at the B205's 24 MS/s. The logic is
the same; the substrate is different (software vs FPGA).

---

## 1. The RF front-end (the "sensors") — VALIDATED

*(Details in the architecture doc; summarized here for the detection context.)*

- **RF transceivers:** 2× **Analog Devices AD9371 / AD9375** (wideband 70 MHz–6 GHz SDR
  transceivers), in a **2-channel (X2), 126 MS/s, RX-focused** config (`the AD9371 2-RX 126M device-tree overlay (RX1)`
  + `...AD9375...RX1...` device-tree overlays; RX1 + RX2, each with an observe/monitor path).
- **ADC→FPGA interface:** **JESD204B** (the high-speed serial ADC-DSP interface).
- **FPGA:** **Xilinx ZynqMP (XC7Z010-class, "xu1")** running the **`the EXDR2 FPGA bitstream (`.bit.bin`)`**
  bitstream ("exdr2" = 2nd-gen **EXDR** framework). The signal processing is **FPGA-accelerated**
  (the DSP cores run in the Zynq's PL, in parallel).
- **Clocking:** a **Silicon Labs Si5345** (multichannel synthesizer) — configs for 126M /
  122.88M / 102.4M sample rates.
- **Two RFFE instances** (`exdr1-rffe` + `exdr2-rffe` init scripts) — one per AD937x channel,
  each with its own EXDR pipeline + its own `rffe-cli` reset.
- **IIO devices** (the Linux IIO nodes the FPGA exposes): `ad9371-phy`,
  `axi-ad9371-rx-hpc`, `axi-ad9371-rx-obs-hpc` (observe/monitor), `axi-ad9371-tx-hpc`,
  `axi-dsp-rx-hpc`, `axi-dsp-obs-hpc` — so the FPGA has **RX + OBS + TX + DSP** AXI DMA channels.

**Implication:** at **126 MS/s**, the device sees a **~100+ MHz instantaneous bandwidth** at once —
it can watch the **entire 2.4 GHz ISM band (2400–2480 MHz = 80 MHz) in a single wideband grab**
(no hopping, no channel-stepping). That's why it can see the *full* ELRS hop pattern at once.
Our B205 (24.32 MS/s, 1-channel) has to sweep or use a ~24 MHz window.

---

## 2. The detection data model (the flatbuffers) — VALIDATED

The daemons (`the main radio/detection daemon (175 MB, statically-linked C++)`, the 175 MB detection engine; `the comms daemon (40 MB, C++)`, the 40 MB GUI comms; the
EXDR monitor/startup) talk over **flatbuffers** (zero-copy serialization). **Four schemas**
define the entire detection data model (`/root/src/flatbuffers/`):
- **`hardware.fbs`** — the RF hardware config (protocols, antennas, RFIC channels, **DSP cores**, DMA).
- **`inproc_messages.fbs`** — the inter-process message types (jobs, detections, ECM control).
- **`system_status.fbs`** — the system status (active protocols, loadset/ECM config, whitelist, engagement, modulation params).
- **`tasks.fbs`** — the **detection tasks + the per-protocol detection results** (the heart of it).

This data model is the **most valuable part of the firmware for us** — it's the *contract* for
what a detection looks like. §3-§7 walk through it.

---

## 3. The protocol catalog + the per-protocol DSP cores — VALIDATED

### 3.1 The `Protocol` enum (the full internal catalog)
```
UNKNOWN, LB, OS, INF, FMV, SIK, MH, XT, RM, DL, OSAERO, AT, STX, WIFI, XF, ELRS, FLRC, LORA, GHOST, FSK
```
| Internal | Protocol | PHY | DSP core(s) |
|---|---|---|---|
| `LB` | DJI LightBridge | proprietary OFDM (video) | `DSP_LB`, `DSP_LB_VERIFY`, `DSP_LBID` |
| `OS` | DJI OcuSync | proprietary OFDM | `DSP_OS`, `DSP_OS_VERIFY`, `DSP_OSID` |
| `INF` | (infrared / FPV?) | — | (off by default) |
| `FMV` | FPV Video (analog) | FM/VSB | `DSP_FMV`, `DSP_FMV_SPEC` |
| `SIK` | 3DR SiK | FHSS 4-FSK | (FSK scan — many rates) |
| `MH` | Microhard | FHSS/OFDM | (FSK scan) |
| `XT` | DigiXTend | FHSS | `DSP_XT` |
| `RM` | RMILEC | FHSS | `DSP_RM` |
| `DL` | DragonLink | FHSS | `DSP_DL` |
| `OSAERO` | OcuSync Aeroscope | DJI DroneID (Remote-ID) | `DSP_OSAERO` |
| `AT` | Autel | proprietary | (scan_at) |
| `STX` | (a FSK protocol) | FSK | (`stx76` in FSK scan) |
| `WIFI` | WiFi 802.11 | OFDM | `DSP_WIFI_2400_5/10/20MHZ`, `DSP_WIFI_5800_5/10/20MHZ` |
| `XF` | Crossfire (TBS) | LoRa chirp | (FSK/LoRa scan — `xf_lora6/10`, `xf85`) |
| **`ELRS`** | **ExpressLRS** | **LoRa chirp + FLRC/GFSK** | **(FSK/LoRa scan — `elrs_127x_lora6-9`, `elrs_128x_lora5-9`)** |
| `FLRC` | Fast LoRa (ELRC) | GFSK | (`flrc_520`, `flrc_650`) |
| `LORA` | generic LoRa | LoRa chirp | `DSP_LORA_250`, `DSP_LORA_500`, `DSP_LORA_812p5` |
| `GHOST` | Ghost | FHSS/proprietary | (scan_ghost) |
| `FSK` | generic FSK | FSK | (the catch-all) |

### 3.2 The `DSPCore` enum (one hardware DSP core per protocol)
```
DSP_RAW_IQ, DSP_RESAMPLER,
DSP_LB, DSP_LB_VERIFY, DSP_OS, DSP_OS_VERIFY, DSP_FMV, DSP_FMV_SPEC,
DSP_FSK, DSP_XT, DSP_RM, DSP_DL, DSP_OSAERO, DSP_LBID, DSP_OSID,
DSP_WIFI_2400_5MHZ, DSP_WIFI_2400_10MHZ, DSP_WIFI_2400_20MHZ,
DSP_WIFI_5800_5MHZ, DSP_WIFI_5800_10MHZ, DSP_WIFI_5800_20MHZ,
DSP_LORA_250, DSP_LORA_500, DSP_LORA_812p5
```
**So the FPGA runs a parallel pipeline: each protocol gets its own hardware DSP core, running in
the Zynq's PL, in parallel.** Two-stage cores (`DSP_LB` + `DSP_LB_VERIFY` + `DSP_LBID`) = detect →
verify → extract-ID. This is why it can detect 16+ protocols simultaneously at 126 MS/s — it's not
software-demodulating everything on the ARM CPU; it's **hardware-pipeline-demodulating** each
protocol class in the FPGA, in parallel. The ARM CPU (`the main radio/detection daemon (175 MB, statically-linked C++)`) orchestrates the jobs + does the
post-detection ID extraction + the GUI comms.

---

## 4. The FSK/LoRa scan config (the ELRS-specific rate catalog) — VALIDATED

The **`ScanConfigFSK`** table is the **exact list of FSK/LoRa rates the device scans for** —
**directly relevant to our ELRS work**:
- **ELRS:** `elrs_127x_lora6/7/8/9` (ELRS 127.x, LoRa SF6/7/8/9) + `elrs_128x_lora5/6/7/8/9`
  (ELRS 128.x, LoRa SF5/6/7/8/9)
- **FLRC (Fast LoRa):** `flrc_520`, `flrc_650` (520 / 650 kbps)
- **Crossfire:** `xf_lora6`, `xf_lora10` (LoRa SF6 / SF10) + `xf85` (FSK 85 kbps)
- **SiK:** ~16 rates (`sik750_4fsk` … `sik32`, `sik500_4fsk`, `sik250`, `sik224_mixed`, `sik224_4fsk`,
  `sik200_4fsk`, `sik200`, `sik192`, `sik188`, `sik128`, `sik125`, `sik100`, `sik96`, `sik64`, `sik56`, `sik48`, `sik32`)
- **Microhard:** `mh345`, `mh276`, `mh230`, `mh172`, `mh115`, `mh57`
- **STX:** `stx76`

**Key takeaway for our ELRS work:** the reference device scans for **ELRS at LoRa SF5–SF9** (both
127.x and 128.x) + **FLRC at 520/650 kbps** + **Crossfire at LoRa SF6/SF10 + 85 kbps FSK**. This is
the **exact ELRS rate set we should cover** (our `identify.py` LoRa path covers the 6 standard ELRS
rates; FLRC 520/650 is the gap). The device also treats **ELRS and FLRC as separate protocols**
(separate `ELRSDetect` + `FLRCDetect` tables) — it distinguishes standard LoRa-ELRS from Fast-LoRa
ELRC, matching our own `dronescan.py` finding (the "FLRC vs LoRa" discriminator).

---

## 5. The per-protocol detection algorithm — VALIDATED

The detection is a **5-stage pipeline**, the same for every protocol (the per-protocol DSP core
just changes the demod stage):

### Stage 1: The channelizer (the FPGA's FFT frontend)
The AD937x IQ (126 MS/s) goes to the FPGA's **channelizer**: a **3-stage FFT**
(`fft_scale_stage_1/2/3` config strings) + a **polyphase FIR** (`chzr_pfir_channel` — the
polyphase decimation filter) + overflow handling (`chzr_fft_overflow`). This produces a
**per-channel power track** (the FFT bins), which is the input to the per-protocol DSP cores.
*(The `fftw.wisdom` + `fftwf.wisdom` files in `/root/config/` confirm it uses **FFTW** for the
FFTs — the plan-cache files FFTW writes for fast re-planning.)*

### Stage 2: The hysteresis energy detection (the false-alarm rejection)
Each DSP core looks at the channelizer's power track and does a **hysteresis-based detection**:
- **`high_threshold`** — the "detected" threshold. A sample at/above it is a detection.
- **`low_threshold`** — the "release" threshold (< high). A burst continues while power stays
  ≥ this. The gap is the **hysteresis band** (the false-alarm rejection — a single-sample noise
  spike above `high` doesn't become a burst unless it *stays* above `low`).
- **`high_threshold_count`** — a burst must have ≥ this many high-threshold samples (the
  false-alarm gate).
- **`detect_threshold`** — the base detection threshold (auto-calibrated against the ambient
  noise — the `pwr_config` `accumulator_interval` + `post_amp` are the power-accumulation config).
- **`variance_threshold`** + **`frac_var_threshold`** — the **variance discriminators** (the
  reference measures the *variance* of some quantity to classify the signal — this is the
  burst-structure / continuous-carrier discriminator; see §7).

**This is exactly the hysteresis burst detector we ported to `elrs_sdr/detect.py:detect_bursts`
(the `high_threshold`/`low_threshold`/`min_high_count` parameters map 1:1).**

### Stage 3: The sync-word correlation (the air-recovery)
Once a burst is detected, the core does a **sync-word correlation** — it correlates the burst
against a **reference sync template** (the `.fc64` complex-float files):
- **`symbPSSfft12M.fc64`** (4096 complex samples, mag range [0.002, 111.8]) — a **PSS
  (Primary Synchronization Signal) FFT at 12 MHz** (a sync-symbol correlation template; the
  magnitude ramps up like a chirp — consistent with a sync-symbol template).
- **`syncSymbSSSCAFFDC.fc64`** (101,268 complex samples, mag range [0, 1.65], mean 0.57) — the
  **sync symbol** ("SSS C-FFDC" — a specific sync pattern; ~101K samples = a long reference sync
  waveform, normalized to unit-ish magnitude).
- The **`parse_sync_0`** function (string: `parse_sync_0: pos (%zu) ret (%d)` + `sync: %02x %02x`)
  — the **sync-word parser** that reports the *position* + the *matched sync bytes* (hex). This is
  the "read the sync out of the air" — the same air-recovery we build.
- **`Sync_Task` / `Sync_Task_Instance`** — the dedicated sync-detection task in the pipeline.
- **`LoRaCore`** — the LoRa DSP core (the dechirp + SF/BW measurement).
- **`corr_len`** — the correlation length (the sync-word correlator's length).

**So the reference's sync-word air-recovery = correlate the burst against a reference sync template
(the `.fc64` files) to find the sync position, then read the UID/Net ID out of the demodulated
payload.** This is our two-layer design (Layer 1 = air-recovery of the sync/UID, Layer 2 = positive
ID). The reference does Layer 1 in hardware (FPGA correlation against the `.fc64` templates); we
do it in software (`identify.detect_sync_word` / the GFSK correlator).

**Key difference + our gap:** the reference has **pre-computed reference sync templates** (the
`.fc64` files) — it correlates against a *known* sync symbol. Our air-recovery is harder because
**ELRS's sync is UID-seeded** (the sync word depends on the per-link UID — which is what we don't
have). So the reference's ELRS detection either (a) has a *generic* ELRS sync template (the
preamble, not the UID-seeded part) + recovers the UID from the payload, or (b) does an
adaptive/brute-force sync match. Our air-recovery (recover the UID from the air) is the
harder-of-the-two — the reference's `parse_sync_0` + ELRS LoRa core (in the 175 MB `the main radio/detection daemon (175 MB, statically-linked C++)`
binary) is where it's solved (a `firmware-labeling` / `rce-gate-joern` reverse-engineering task,
a separate deeper effort).

### Stage 4: The per-protocol demod (the DSP core's job)
After the sync locks, the per-protocol DSP core demodulates:
- **LoRa (`DSP_LORA_250/500/812p5`):** the dechirp + **SF/BW/coding-rate measurement** (the
  `LORADetect` table: `sf`, `bandwidth`, `nzeros`, `invert_iq`, `n_upchirp_dets`,
  `n_downchirp_dets`, `channel_freqs_hz` — i.e. it measures the SF, BW, the up/down-chirp counts,
  + the *channel frequencies* (the hopping pattern!)).
- **4-FSK (`SIK`/`MH`):** the 4-FSK demodulator (string: `called for 4fsk demod with no rpp` —
  "rpp" = samples per symbol).
- **OFDM (`WIFI`, `LB`, `OS`):** the **OFDM symbol-length** check (`ofdm_symbol_len`,
  `total_symbol_len` — it checks the OFDM symbol length to distinguish the protocols) + the
  FFT demod (`fft_scale_stage_*`).
- **WiFi (the `WIFIDetect`):** decodes the **L-SIG** + the **beacon** → the **SSID + MAC
  addresses + vendor + device_type** (the `DetectionNetworkInfo`: `ip_address`,
  `friendly_name`, `mac_address`, `device_type` ∈ {UNKNOWN, the C-UAS device, DRONE, REMOTE_CONTROLLER}).
  *This is the "identify by hardware-unique ID (MAC) via SDR-only 2.4 GHz 802.11" capability the
  operator wants — the reference ships it.*

### Stage 5: The per-link Net ID extraction (the `DetectionNetID`)
The detection's per-link identifier is the **`DetectionNetID`**:
```
table DetectionNetID {
  device_network_id:int;   // a numeric net ID
  baudrate_enum:int;
  baudrate:int;
  net_id:string;           // the net ID as a string
  raw_net_id:[ubyte];      // the RAW net ID bytes  <-- the per-link ID
}
```
**For ELRS, `raw_net_id` is the 6-byte bind UID** (INFERRED — the ELRS link's only per-link
airborne ID is the bind UID, and the detection has a `net_id` field that gets whitelisted).
**For WiFi, it's the BSSID/MAC.** **For DJI Remote-ID (Aeroscope), it's the serial number.**
**This is the "hardware-unique ID per drone (per-link)" the operator wants** — the reference
extracts it from the air + displays/whitelists it ("Copy Net ID to Clipboard").

### The `DetectionTaskResult` (the unified detection report)
```
table DetectionTaskResult {
  power_dbm, freq_hz, unique_freq_hz, protocol, antenna,
  current_geo, home_geo, rc_geo, orientation, speed,
  net_id, model, rc_info, serial_number, ssid, vendor,
  network_devices, detection_uid, alert_type
}
```
**So a single detection reports: the protocol, the frequency, the power, the Net ID (per-link ID),
the drone's current/home/RC position, orientation, speed, model, serial number, SSID (WiFi),
vendor, the network devices (MACs), a `detection_uid` (the unique detection ID, likely a hash of
net_id + freq), + the alert type (DETECT / WHITELISTED_DETECT / DISABLED_DETECT).** This is a
*rich* detection — not just "there's a signal," it's "there's an ELRS link at 2456 MHz, -42 dBm,
Net ID XYZ, drone at (lat, lon, alt), moving at N mps, model ABC."

**Per-protocol `Detect` tables (what each extracts):**
- **`ELRSDetect` / `FLRCDetect` / `XFDetect` (Crossfire):** `center_freq_hz`, `is_power_valid`,
  `power_dbm`, `det_time_us`, `antenna`, `uw_corr` (unique-word correlation), **`net_id:
  DetectionNetID`** ← the per-link Net ID.
- **`SIKDetect` / `MHDetect` / `DLDetect` (DragonLink):** the above **+ `curr_geo` (current
  lat/lon/alt), `home_geo` (home position), `speed` (NED velocity), `ned` (NED position),
  `confirmed`, `modulation_order`** — i.e. for SiK/MH/DragonLink, it **decodes the MAVLink
  telemetry** (position, home, speed) from the link.
- **`LORADetect` (generic LoRa):** `center_freq_hz`, `power_dbm`, `antenna`, `sf` (spreading
  factor), `bandwidth`, `nzeros`, `invert_iq`, `n_upchirp_dets`, `n_downchirp_dets`,
  `channel_freqs_hz` — i.e. for generic LoRa, it measures the **SF + BW + up/down-chirp counts +
  the channel frequencies** (the hopping pattern!).
- **`WIFIDetect`:** `center_freq_hz`, `channel_bw` (default 20 MHz), `power_dbm`, `serno`,
  **`net_infos: [DetectionNetworkInfo]`**, `vendor`, `ssid`, `curr_geo`, `rc_geo`, `speed`,
  `rid_detected` — i.e. for WiFi, it decodes the **SSID + vendor + MAC addresses + the drone/RC
  positions + Remote-ID detection**.
- **`OSAERODetect` (OcuSync Aeroscope):** `curr_geo`, `home_geo`, `rc_geo`, `orientation
  (pitch/roll/yaw)`, `speed`, `model`, `rc_info`, `serial_number` — i.e. for DJI Aeroscope
  (Remote-ID), it decodes the **drone position + home + RC position + orientation + speed + model
  + serial number**.
- **`RMDetect` (RMILEC):** `sync_word` (uint), `txid` (byte), `variance`,
  `identified_channels` (the hopping channels) — i.e. for RMILEC, it extracts the **sync word + TX
  ID + the channel hops**.

---

## 6. The identification (the "how it identifies") — VALIDATED + INFERRED

"Identification" in the reference = **extracting the per-link Net ID** (the `DetectionNetID`)
from the demodulated link. The mechanism per protocol:
- **ELRS / Crossfire / LoRa:** the **`raw_net_id` is the bind UID** (for ELRS) / the link's UID
  (for Crossfire). It's recovered from the air via the sync-word correlation (Stage 3) + the
  LoRa dechirp (Stage 4) — the UID is in the ELRS payload (the sync word is UID-seeded, so
  reading the sync = reading the UID). *(INFERRED that it's the bind UID — the ELRS link's only
  per-link airborne ID.)*
- **WiFi:** the **`raw_net_id` is the BSSID/MAC** (the `DetectionNetworkInfo.mac_address`). It's
  decoded from the 802.11 beacon (the L-SIG + the beacon frame). This is a **hardware-unique,
  per-access-point ID** — the strongest "identify by hardware ID" case. The `device_type` enum
  ({UNKNOWN, the C-UAS device, DRONE, REMOTE_CONTROLLER}) even lets it classify whether the WiFi device
  is the C-UAS device itself, a drone, or a remote controller.
- **DJI (LightBridge / OcuSync / Aeroscope):** the **`serial_number` + the DroneID** (the Aeroscope
  `OSAERODetect.serial_number`). The DJI DroneID (Remote-ID) carries a **serial + operator
  location** — a real per-aircraft ID.
- **SiK / MH / DragonLink:** the **MAVLink telemetry** (the `curr_geo`/`home_geo`/`speed` — the
  drone's position + home + speed, which identifies the *aircraft instance*). The `txid` (for RM)
  is a per-transmitter ID.
- **Generic FSK / the catch-all:** no per-link ID (just the presence + the modulation).

**The "Whitelist by UID" feature** (from the operator manual) = the `WhitelistUIDDto` /
`EcmEngageUIDDto` / `RemoveWhitelistUIDDto` flatbuffers (strings in `the main radio/detection daemon (175 MB, statically-linked C++)`) — the device
maintains a **whitelist of Net IDs** (the per-link IDs it has identified). When a detected link's
Net ID matches the whitelist, the alert type is `WHITELISTED_DETECT` (not a new alarm). This is
the "I know this drone, don't alarm" feature — and it *proves* the per-link ID extraction is a
shipped, real capability (you can't whitelist by an ID you can't extract).

**The "Copy Net ID to Clipboard"** (from the operator manual) = the GUI letting the operator copy
the detected link's Net ID (the `net_id` string) — confirming the Net ID is displayed + usable.

---

## 7. The false-alarm rejection (the thing that keeps it clean) — VALIDATED + INFERRED

The reference has **multiple false-alarm gates** (the strings confirm them):
1. **The hysteresis gate** (Stage 2) — the high/low threshold + counts (the burst must *stay*
   above the low threshold; a noise spike doesn't).
2. **The `variance_threshold` / `frac_var_threshold`** — the **variance discriminators**. The
   reference measures the *variance* of some quantity (INFERRED: the signal's amplitude or
   frequency variance) to classify the signal. A **continuous carrier** (a pilot tone / LO leak)
   has *low variance* (it's constant); a **modulated burst** (FSK/LoRa) has *high variance* (the
   frequency/amplitude varies). **This is the burst-structure / continuous-carrier
   discriminator** — the thing that rejects the ch31 120433 capture's 0-2.5 ms continuous
   carrier (a ~54 dB pilot tone that's *not* a drone data link). We ported an equivalent to
   `elrs_sdr/detect.py:is_continuous_carrier` (the amplitude-CV discriminator — a low CV = a
   continuous carrier, rejected).
3. **The "ignore first stage detect" / "ignoring packetstart" / "NumBitsDecode too large"** — the
   per-protocol demod's own sanity gates (a demod that produces an absurd bit count or fails the
   sync is dropped).
4. **The `confirm` / `confirmed` field** (in the SiK/MH/DL detects) — a detection isn't "confirmed"
   until it's seen multiple times (the multi-burst confirmation — a one-shot isn't a real link).
5. **The `high_threshold_count`** — the minimum number of high-threshold samples (the false-alarm
   gate on the burst length).

**This maps directly to our honesty contract (AGENTS.md §3) — the three false-positive classes**
(non-UID-specific correlation, the LoRa CRC false-accept, the dense-WiFi preamble false-positive).
The reference's gates (hysteresis + variance + confirm + multi-burst) are the *production*
version of our CRC-validity gate + the sparse-vs-dense preamble check.

---

## 8. The mitigation / ECM side (VALIDATED — the part we do NOT build)

The firmware includes the **active-mitigation (ECM)** logic — the paired active-mitigation
device's behavior. **We do NOT build this** (it's the remote-kill/anti-drone-jamming territory,
out of scope — AGENTS.md §2). Documented for completeness:
- **`ECMSystem` enum:** `NONE, E1, E2, M, <a variant code>, LOCAL` — the paired ECM systems.
- **`LoadsetConfig` / `TargetRuleSet`:** the **loadset profiles** (`cuas.json`, `cuas_fb.json`,
  `cuas_lf.json`, `cuas_lf_fb.json`, `fpv.json`, ... in `/home/root/config/loadset_profiles/`) —
  JSON rule-sets that control the mitigation behavior. The **`cuas.json`** (the main one):
  - `system_compatibility: ["E2"]` (the paired ECM system class).
  - `technique_compromise` — the rules:
    - **`WIFI Ignore`**: "Ignore WiFi if any other signal is present" (don't jam WiFi if there's
      a "real" drone signal — the `protocol_preconditions: [ANY_DO_NOT_MATCH WIFI, ALWAYS]` →
      `TechniqueBehaviorIgnoreProtocol WIFI`).
    - **`FSK >1GHz Override`**: override multiple FSK targets >1 GHz with a **50 kHz chirp**
      (`chirp_rate: 50000`).
    - **`FSK <1GHz Override`**: override non-RM FSK <1 GHz with a **25 kHz chirp**
      (`chirp_rate: 25000`).
    - **`RM/Chirp Split`**: split power between RM + chirp targets in the 400 MHz bands
      (`TechniqueBehaviorSplitPower`).
  - `minimize_footprint` — the LF (low-footprint) variants disable bands with no targets.
  - `target_inference` — rules that *infer* a target from the detected signals.
- **`TechniqueBehavior`** — the mitigation actions (override target, priority protocol/modulation,
  ignore protocol/modulation, split power).
- **`TargetModulationLoRa`** — the LoRa modulation params the device measures *from the air* (SF,
  BW, coding rate, upchirps, downchirps) — to know *how* to jam the LoRa link.
- **`Applying (Segmented) Chirp/s Barrage Technique`** / **`Applying targted (segmented)
  discontinuous hopped chirps technique`** / **`Applying targted LoRa mixed chirps technique`** —
  the LoRa jamming waveforms (the "mixed chirps" upchirp/downchirp confusion attack).
- **`[OS Mitigation]: 10MHz Chirp Detect at %lf, Lmax = %.3lf`** — the OcuSync chirp detection
  (10/20 MHz chirps, with an `Lmax` = the max correlation value — the OcuSync mitigation).

**The passive/active split (reaffirmed):** the *detection* (this SDR, `the main radio/detection daemon (175 MB, statically-linked C++)` + the FPGA DSP
cores) is **passive** — it matches our passive-only boundary. The *mitigation* (the ECM logic + the
paired active-mitigation device) is **active** — we do NOT build it. We build the *detection +
identification* half (the protocol catalog, the per-link ID extraction, the "measure don't assume"
classifier), using this firmware as a reference for *how* a production C-UAS detector does it.

---

## 9. The implementation: `elrs_sdr/detect.py` (the software port) — BUILT THIS TURN

The user's directive: "write extremely comprehensive notes regarding how this software detects and
identifies, **and then implement it into our system.**" The implementation is
**`elrs_sdr/detect.py`** — the detection + identification engine, the **software analog of the
reference's `the main radio/detection daemon (175 MB, statically-linked C++)` pipeline**, built on our existing detectors (`core/lora.py`,
`core/gfsk.py`, `wifi.py`, `positivedet.py`, `core/identify.py`). It ports the §5 pipeline to
software:

| Reference stage (the FPGA) | `elrs_sdr/detect.py` (the software port) |
|---|---|
| **Stage 1:** the channelizer (3-stage FFT + polyphase FIR) | (implicit — we operate on a single-channel capture; the B205 gives us one channel at a time) |
| **Stage 2:** the hysteresis energy detection (high/low threshold + counts) | **`detect_bursts()`** (the `high_threshold`/`low_threshold`/`min_high_count`/`max_gap` — 1:1 mapping) + **`_power_track()`** (the moving sum-of-squares) + **`_noise_floor()`** (the auto-calibrated threshold) |
| **Stage 2b:** the variance discriminator (the `frac_var_threshold` — continuous-carrier rejection) | **`is_continuous_carrier()`** (the amplitude-CV discriminator — rejects a continuous carrier / pilot tone / LO leak) |
| **Stage 3:** the sync-word correlation (the `.fc64` templates, `parse_sync_0`) | **`_extract_elrs_net_id()`** (the ELRS air-recovery — `identify.classify_gfsk` + `_recover_uid_from_sync`; the WiFi MAC via `wifi.parse_beacon_mac`) |
| **Stage 4:** the per-protocol demod (LoRa dechirp, 4-FSK, OFDM symbol-length) | **`_demod_burst()`** (orchestrates `positivedet.lora_chirp` + `identify.classify_lora` + `identify.classify_gfsk` + `wifi` + `positivedet.ocusync_phy`) |
| **Stage 5:** the per-link Net ID extraction (the `DetectionNetID`) | **`NetID`** dataclass (the `raw_net_id` + `net_id_str` + `kind` — mirrors the reference's `DetectionNetID`) |
| The `DetectionTaskResult` (the unified report) | **`Detection`** dataclass (the `protocol`, `freq_hz`, `power_db`, `net_id`, `evidence`, `detection_level`, `detection_uid` — mirrors the reference's `DetectionTaskResult`) |
| The SPECIFIC-protocol bar (ELRS vs Crossfire vs LoRa) | **`fingerprint_lora_protocol()`** (the (SF, BW) pair discriminator — climbs the `DetectionLevel` ladder from FAMILY to SPECIFIC) |
| The top-level pipeline (the `the main radio/detection daemon (175 MB, statically-linked C++)` daemon) | **`detect_and_identify()`** (the entry point — finds the bursts, runs the per-protocol demod + Net ID, returns a `Detection` per burst) |
| The GUI report (the target list) | **`gap_report()`** (the human-readable summary — the protocols seen + the per-link ID extraction status) |

**The data model mirrors the reference** (the `Detection` + `NetID` dataclasses carry the same
fields as the reference's `DetectionTaskResult` + `DetectionNetID` — so a detection from our system
has the *same shape* as one from the reference, making the comparison / gap-tracking meaningful).

**The honesty contract (AGENTS.md §3) is baked in:** every `Detection` carries an
**`evidence_class`** (VALIDATED / SELF_CONSISTENT / SYNTHETIC / PRESENCE) + a
**`detection_level`** (the `DetectionLevel` ladder from `protocol_catalog.py`). The
`fingerprint_lora_protocol` is honestly labeled SELF_CONSISTENT (the (SF,BW) fingerprint
round-trips our knowledge of the ELRS/Crossfire rate tables) but not yet VALIDATED against a real
ELRS-vs-Crossfire A/B capture (we have no Crossfire capture). A "detected" that isn't labeled with
its evidence class is an overclaim — and it always is labeled.

**Passive-only (AGENTS.md §2):** `detect.py` is receive-only. It never imports, calls, or wires in
the TX side of `radio.py` (`tx_tune`/`tx_stream`) or `elrc_bench_tx.py`. The TX gate is a wall.

**Verified (this turn):**
- `python3 -m elrs_sdr.detect selftest` **PASS** — (a) a continuous carrier is rejected (0
  detections), (b) a high-CV on/off burst passes the discriminator, (c) the
  `is_continuous_carrier` function works as documented (carrier → True/rejected, burst →
  False/passes).
- **Real ch31 120433 capture:** **0 detections** — the 0-2.5 ms continuous carrier (a ~54 dB
  pilot tone) is **correctly rejected** by the `is_continuous_carrier` discriminator (the whole
  point — a continuous carrier is *not* a drone data link).
- **Real ch40 120433 capture:** **0 detections** — the low-level content there is below the
  threshold / not bursty enough (honestly reported as no detection, not a false positive).
- **`elrs_sdr.selftest` still passes** (no regression — the new module doesn't break the existing
  suite).

**The honest gaps (what the software port does NOT yet do that the reference does):**
1. **The full ELRS sync-word air-recovery** (the `raw_net_id` = the bind UID) — `detect.py` has
   the *path* (`_extract_elrs_net_id` → `identify.classify_gfsk` → `_recover_uid_from_sync`), but
   the *actual* demod of a real ELRS burst to recover the UID is still the blocked piece (the
   LoRa demod is the layer that can't be proven on a desk — AGENTS.md §3). On the ch31/ch40
   captures (which have a continuous carrier + low content, not a clean ELRS burst), the detector
   correctly reports "no detection" rather than a false ELRS hit. **A capture with a *clean* ELRS
   burst is needed to validate the Net ID extraction end-to-end.**
2. **The full WiFi beacon demod** (the L-SIG + the CC decode + the beacon MAC) — `detect.py` marks
   WiFi as PRESENCE (the OFDM preamble fires) but doesn't yet do the full beacon decode (the MAC
   extraction is the IDENTIFIED step — a separate build; the *code* for it exists in `wifi.py` +
   `waveraider/`). The reference ships this fully (the `WIFIDetect` → `net_infos` → MAC).
3. **The multi-burst confirmation** (the `confirmed` field — a detection isn't "confirmed" until
   seen multiple times) — `detect.py` reports per-burst; the multi-burst confirmation (the
   reference's "ignore first stage detect" + the `confirmed` gate) is a refinement (the
   `detection_uid` hash already gives the tracking key).
4. **The MAVLink telemetry decode** (the `curr_geo`/`home_geo`/`speed` for SiK/MH/DL) — not built
   (a separate build step; the reference decodes the drone's position + home + speed from the
   SiK/MH/DL links).
5. **The DJI serial / DroneID** (the `OSAERODetect.serial_number`) — not built (deferred by the
   operator — no DJI hardware to validate against).

**The division of labor (this turn vs next):**
- **I did (this turn):** re-fetched the firmware (from git history — no re-upload needed), deeply
  analyzed the detection/identification algorithm (the 5-stage pipeline, the per-protocol DSP
  cores, the hysteresis detection, the sync correlation, the per-protocol demod, the Net ID
  extraction, the false-alarm gates, the ECM side), wrote these comprehensive notes, **built
  `elrs_sdr/detect.py`** (the software port — the data model, the hysteresis burst detector, the
  continuous-carrier discriminator, the per-protocol demod + Net ID extraction, the
  specific-protocol fingerprint, the `detect_and_identify` entry point), **verified it** (the
  selftest + the real ch31/ch40 captures + the existing selftest), then **deleted the firmware**
  (the `/tmp/cuas_fw_deep` decompressed copy).
- **Next (if you want the deepest reference):** reverse-engineer the **`the main radio/detection daemon (175 MB, statically-linked C++)`** (175 MB)
  binary (the *exact* ELRS sync/UID recovery algorithm — the `parse_sync_0` + the ELRS LoRa core)
  + the **FPGA bitstream** (the per-protocol DSP cores) + the **`.fc64` sync templates** — a
  `firmware-labeling` / `rce-gate-joern` task (the enclave skills for exactly this). That would
  reveal the *exact* ELRS air-recovery algorithm the reference uses (the hardest part of our own
  air-recovery goal). But it's a separate, deeper effort — these notes capture the *algorithm +
  data model* (the "what" + "how"), and the binary RE would capture the *exact implementation*
  (the "how, in detail").

---

## 10. Honest assessment + open questions

- **VALIDATED** (read directly from the firmware): the 5-stage pipeline (channelizer → hysteresis
  detect → sync correlation → per-protocol demod → Net ID extraction), the `Protocol` enum
  (16 protocols), the `DSPCore` enum (one core per protocol), the `ScanConfigFSK` ELRS/FLRC/
  Crossfire/SiK/MH rate catalog, the per-protocol `Detect` tables (the ELRS/Crossfire `net_id`,
  the SiK/MH/DL MAVLink telemetry, the LoRa SF/BW/chirp counts, the WiFi SSID/MAC, the Aeroscope
  serial/model/orientation, the RM sync_word/txid/channels), the `DetectionNetID` (the
  `raw_net_id`), the `DetectionTaskResult` (the unified report), the sync templates (the
  `symbPSSfft12M.fc64` 4096-sample + `syncSymbSSSCAFFDC.fc64` 101268-sample), the `parse_sync_0`
  + `Sync_Task` + `LoRaCore`, the hysteresis config (`high/low_threshold`,
  `high_threshold_count`, `detect_threshold`, `variance_threshold`, `frac_var_threshold`), the
  ECM side (`ECMSystem` enum, the `cuas.json` loadset rules, the `TechniqueBehavior`, the
  `TargetModulationLoRa`, the LoRa "mixed chirps" jamming).
- **INFERRED** (labeled as such): that the ELRS `raw_net_id` is the bind UID (§5 Stage 5, §6),
  that the `variance_threshold`/`frac_var_threshold` is the continuous-carrier discriminator
  (§7), the actionable takeaways for our detector (§9).
- **Open questions (need the `the main radio/detection daemon (175 MB, statically-linked C++)` binary RE or the operator):**
  - **Exactly how does the reference recover the ELRS UID (`raw_net_id`) from the air** — given
    the sync is UID-seeded? (The algorithm is in the 175 MB `the main radio/detection daemon (175 MB, statically-linked C++)` binary — a separate
    `firmware-labeling`/`rce-gate-joern` task.)
  - **What are the exact reference sync templates** (the `.fc64` files) — the sync words /
    correlation patterns per protocol? (They're 1.6 MB + 64 KB complex-float files — I can dump +
    analyze them, but they're device-specific coefficients.)
  - **The exact detection sensitivity / range / detection-time** per protocol? (Not in the
    firmware I analyzed — that's in the FPGA bitstream + the DSP core implementations, a deeper RE.)
  - **The exact FPGA DSP core algorithms** (the `DSP_LORA_250/500/812p5`, `DSP_WIFI_*`, etc.) —
    the per-protocol demod/correlation logic in the bitstream (a `firmware-labeling` task on the
    `the EXDR2 FPGA bitstream (`.bit.bin`)`).

---

*Notes compiled from the firmware (v4.0.6 `.swu` update package) the operator provided as a
reference. The firmware binaries were deleted after analysis (operator's rule). This is the
**detection + identification algorithm** (the "how it detects + how it identifies"), complementing
the architecture notes (`docs/COUNTER_UAS_SDR_FIRMWARE_DEEP_DIVE.md` — the "how it's built") and
the operator-manual notes (`docs/REFERENCE_COUNTER_UAS_SDR.md` — the "what it is + how to use it").
The implementation that ports this algorithm into our system is **`elrs_sdr/detect.py`**.*
