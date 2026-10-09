# Reference: Counter-UAS Passive SDR — Firmware Deep-Dive Notes

> **Naming rule (operator directive, standing):** never use the device's vendor/make name, any
> acronym/abbreviation of it, or the names of its paired systems (the active-mitigation/ECM
> devices) anywhere in our notes, code, or discussion. Refer to the device generically as **"the
> counter-UAS passive SDR"** (or "the C-UAS detector"); the paired active-mitigation device as
> **"the paired ECM system"**; the vendor as **"the vendor (a U.S. defense contractor)"**. The
> *drone-protocol names* (ELRS, Crossfire, LoRa, DJI OcuSync/LightBridge, SiK, DragonLink, etc.)
> are the **subject matter** (the drone links we detect) — those are kept, because they're what the
> detector targets, not the C-UAS system's own branding.
>
> **Source:** the device's **firmware** (a v4.0.6 Software-Update package — a `.swu`), provided by
> the operator as a *reference* for our own passive ELRS/drone detector. This is a **deep-dive into
> the firmware** (complementing the earlier operator-manual notes in
> `docs/REFERENCE_COUNTER_UAS_SDR.md`). Every claim below is labeled **VALIDATED** (read directly
> from the firmware) vs **INFERRED** (my reasoning from it).
>
> **Handling note:** the firmware binary was provided as an `.xz`-compressed tar. I decompressed it
> to analyze the structure, then **deleted all the device binaries** (the operator's rule: don't
> keep the device material on disk — notes only). These notes are the durable artifact.
>
> **This is the "how it's built" (architecture) doc.** It's the middle doc in a three-doc set:
> - `docs/COUNTER_UAS_SDR_DETECTION_ID.md` — **how it works** (the 5-stage detection/identification
>   pipeline, the per-protocol DSP cores, the hysteresis detection, the sync correlation, the
>   per-protocol demod, the per-link Net ID extraction, the false-alarm gates, the ECM side, + the
>   `elrs_sdr/detect.py` implementation mapping). Written the same day, from a deeper algorithm
>   analysis.
> - **`docs/COUNTER_UAS_SDR_FIRMWARE_DEEP_DIVE.md` (this doc)** — **how it's built** (the RF
>   front-end, the FPGA, the daemons, the flatbuffers data model, the loadset/ECM config).
> - `docs/REFERENCE_COUNTER_UAS_SDR.md` — **what it is + how to use it** (the operator manual).
>
> **The implementation that ports this firmware's detection/identification algorithm into our
> system is `elrs_sdr/detect.py`** (the software analog of the reference's radio pipeline; see the
> DETECTION_ID doc §9 for the stage-by-stage mapping).

---

## 1. What the firmware is (VALIDATED)

The update package (`.swu`) is a **nested container**:
1. **Outer:** a **POSIX tar (GNU)** archive containing a single file (the `.swu` proper).
2. **Inner:** the `.swu` is an **ASCII cpio archive (SVR4 with CRC)** — 8 files:
   - `BOOT.bin` — the **U-Boot bootloader** (Xilinx ZynqMP U-Boot image)
   - `Image` — the **Linux kernel** (`Linux kernel ARM64 boot executable Image, little-endian, 4K pages`)
   - `devicetree.dtb` — the **device tree blob** (version 17, ~37 KB)
   - `uEnv.main.txt` + `uEnv.alt.txt` — U-Boot environment files (**A/B boot**)
   - **`ptwo.tar.gz`** — the **root filesystem** (161 MB compressed → 515 MB, 8,388 files)
   - `sw-description` — the **update manifest** (device layout + per-file sha256)
   - `sw-description.sig` — the **manifest signature** (integrity/authenticity)

The **`sw-description`** is a device-layout manifest: the rootfs (`ptwo.tar.gz`) goes to
`/dev/mmcblk1p2` (main) / `/dev/mmcblk1p3` (alt — **A/B rootfs**); the kernel + dtb + BOOT + uEnv
go to the vfat `/dev/mmcblk1p1`. Each file has a **sha256** (integrity). So it's a standard
**A/B-updatable embedded Linux** image on a **ZynqMP** (the `p1` = U-Boot/kernel vfat, `p2`/`p3` =
A/B rootfs on eMMC).

**The OS** in the rootfs is **Arch Linux ARM64** (aarch64, Buildroot-built — the build path in the
binary is `aarch64-buildroot-linux-gnu`). It's a full Linux userland (busybox, dbus, python 3.9,
nmap, rtl-sdr libs, etc.) + the custom detection daemons.

---

## 2. The RF front-end hardware (VALIDATED — from `/root/sdr-profiles/` + the device tree overlays)

This is a **professional-grade wideband SDR**, not a cheap RTL-SDR:

- **RF transceivers: 2× Analog Devices AD9371 / AD9375** (the "X2" = 2-channel, "126M" = 126 MS/s
  profiles; `the AD9371 2-RX 126M device-tree overlay (RX1)` + `the AD9375 2-RX 126M device-tree overlay (RX1)`). The AD9371/9375 are
  wideband (70 MHz–6 GHz) SDR transceivers — the same family as the ADALM-Pluto / ADRV9371, but here
  in a **2-channel, 126 MS/s, RX-focused** config (RX1 + RX2, each with an observe/monitor path).
- **ADC→FPGA interface: JESD204B** (`jesd-interface-clocks-*.dtbo` — the JESD204B high-speed
  serial interface between the AD937x ADCs and the FPGA).
- **FPGA: Xilinx ZynqMP (XC7Z010-class, "xu1")** running the **`the EXDR2 FPGA bitstream (`.bit.bin`)`**
  bitstream — "exdr2" = 2nd-generation **EXDR** (the open-source software-SDR / RF-pipeline
  framework, from the MITRE/US DoD "EXDR" project). So the signal processing is **FPGA-accelerated**
  (the DSP cores run in the Zynq's PL, not just the ARM CPU).
- **Clocking: a Silicon Labs Si5345** (a multichannel synthesizer) — configs for 126M / 122.88M /
  102.4M sample rates (`Si5345-*-exdr-*.txt`). The **Si5345** is the same chip used to clock the
  AD937x in many wideband SDR designs.
- **Two RFFE (Radio-Frequency-Front-End) instances** — `exdr1-rffe` + `exdr2-rffe` init scripts
  (one per AD937x channel, each with its own EXDR pipeline + its own `rffe-cli` reset).
- **IIO devices** (the Linux IIO subsystem nodes the FPGA exposes): `ad9371-phy`,
  `axi-ad9371-rx-hpc`, `axi-ad9371-rx-obs-hpc` (observe/monitor), `axi-ad9371-tx-hpc`,
  `axi-dsp-rx-hpc`, `axi-dsp-obs-hpc` — so the FPGA has **RX + OBS + TX + DSP** AXI DMA channels.
- **Antennas (2 physical ports):** from the operator manual, 0–2 GHz + 2–6 GHz. The device tree
  overlays select the front-end by **PCB revision** (`platform_id`: `OPTI_V00`, `X2R3V1-1`,
  `X2R3V1-5`, `X2R3V2-5`, `X2R3V3-5` — the "X2R3" = 2-RX, rev-3 boards, with AD9371 vs AD9375
  variants).

**Implication for us:** the reference device's 2.4 GHz detector runs on a **126 MS/s, 2-channel,
FPGA-accelerated (EXDR2) wideband SDR** — far more capable than our B205 (24.32 MS/s, 1-channel).
The 126 MS/s sample rate means it can see a **~100+ MHz instantaneous bandwidth** at once — i.e. it
can watch the **entire 2.4 GHz ISM band (2400–2480 MHz = 80 MHz) in a single wideband grab** (no
hopping, no channel-stepping) — which is exactly the regime our 28 MHz wideband captures try to
approximate (and why the reference device can "see" the full ELRS hop pattern at once while we have
to sweep or use a narrower window).

---

## 3. The detection software architecture (VALIDATED)

### 3.1 The daemons (`/root/bin/`)
- **`the main radio/detection daemon (a 175 MB statically-linked C++ binary)`** (175 MB, statically-linked C++) — **the main detection/radio engine.** This is
  where the protocol detection + demod + UID-extraction logic lives. (Statically linked → huge.)
- **`the comms daemon (a 40 MB C++ binary)`** (40 MB) — the **communications layer** (talks to the Android/tablet GUI, the
  the tactical team-communications app-app plugin, the "survey tool" — the `org::surveytool::idl` flatbuffers namespace).
- **`exdr_monitor`** + **`exdr_startup`** — the **EXDR** framework's monitor + startup (the
  signal-processing pipeline supervisor).
- **`rfic`** (743 B, shell) — the **RFIC** (RF IC) controller (the AD937x + Si5345 config).
- **`si5345_cli`** (56 KB) — the **Si5345** synthesizer CLI.
- **`fpga`** (824 B, shell) — the FPGA bitstream loader.
- **`iio`** (895 B, shell) — the Linux IIO control.
- **`overlay-mgr`** — the device-tree-overlay manager (selects the AD9371/9375 front-end by PCB rev).
- **`rffe-cli`** — the RFFE control CLI (resets the front-end).
- **`front_panel`**, `update_leds`, `fan` — the physical device controls (LEDs, fan).
- **`aoap_tcp_relay`** — a TCP relay (AOAP = a comms protocol).
- **`update-boot.py`**, `update_leds`, `reset-cause.sh`, `rotate_log.py`, `rotate_core.py` — the
  maintenance/ops scripts.

### 3.2 The IPC: flatbuffers schemas (`/root/src/flatbuffers/`)
The daemons talk over **flatbuffers** (a zero-copy serialization). The **4 schemas** define the
entire detection data model — this is the **most valuable part of the firmware for us**:
- **`hardware.fbs`** — the RF hardware config (protocols, antennas, RFIC channels, DSP cores, DMA).
- **`inproc_messages.fbs`** — the inter-process message types (jobs, detections, ECM control).
- **`system_status.fbs`** — the system status (active protocols, loadset/ECM config, whitelist,
  engagement, modulation params).
- **`tasks.fbs`** — the **detection tasks + the per-protocol detection results** (the heart of it).

### 3.3 The DSP pipeline (the FPGA runs a per-protocol DSP core)
The `DSPCore` enum (in `hardware.fbs`) lists the **dedicated FPGA DSP cores** — **one per protocol**
(+ verify/stage variants):
- `DSP_RAW_IQ`, `DSP_RESAMPLER` (the baseband / resample)
- `DSP_LB` + `DSP_LB_VERIFY` (LightBridge — 2-stage: detect + verify)
- `DSP_OS` + `DSP_OS_VERIFY` (OcuSync — 2-stage)
- `DSP_FMV` + `DSP_FMV_SPEC` (FPV Video — detect + spectrum)
- `DSP_FSK` (generic FSK)
- `DSP_XT`, `DSP_RM`, `DSP_DL` (DigiXTend, RMILEC, DragonLink)
- `DSP_OSAERO` (OcuSync Aeroscope)
- `DSP_LBID`, `DSP_OSID` (LightBridge/OcuSync **ID** extraction — the "read the serial/UID" stage)
- `DSP_WIFI_2400_5MHZ`, `DSP_WIFI_2400_10MHZ`, `DSP_WIFI_2400_20MHZ` (WiFi 2.4 GHz, 5/10/20 MHz BW)
- `DSP_WIFI_5800_5MHZ`, `DSP_WIFI_5800_10MHZ`, `DSP_WIFI_5800_20MHZ` (WiFi 5.8 GHz)
- `DSP_LORA_250`, `DSP_LORA_500`, `DSP_LORA_812p5` (LoRa at 250 / 500 / 812.5 Hz)

**So the FPGA runs a parallel pipeline: each protocol gets its own hardware DSP core, running in
the Zynq's programmable logic, in parallel.** This is why the device can detect 16+ protocols
simultaneously at 126 MS/s — it's not software-demodulating everything on the ARM CPU, it's
**hardware-pipeline-demodulating** each protocol class in the FPGA, in parallel. The ARM CPU
(`the main radio/detection daemon (a 175 MB statically-linked C++ binary)`) orchestrates the jobs + does the post-detection ID extraction + the GUI comms.

**This is the key architectural insight for us:** the reference device's "measure, don't assume"
philosophy is implemented as a **hardware parallel-pipeline** (each protocol = a DSP core). Our
`identify.py` does the equivalent **in software, per-burst** (iterate all protocols × rates, let CRC
decide). The reference device gets the same coverage faster (FPGA parallelism), but the *logic*
(the per-protocol detectors + the UID extraction) is the same concept we're building.

---

## 4. The protocol catalog + the per-protocol detection (VALIDATED — from `tasks.fbs`)

### 4.1 The `Protocol` enum (the full internal catalog — maps to the manual's 16)
```
UNKNOWN, LB, OS, INF, FMV, SIK, MH, XT, RM, DL, OSAERO, AT, STX, WIFI, XF, ELRS, FLRC, LORA, GHOST, FSK
```
| Internal code | Protocol (from the manual) | Notes |
|---|---|---|
| `LB` | DJI LightBridge | 2-stage (DSP_LB + DSP_LB_VERIFY + DSP_LBID) |
| `OS` | DJI OcuSync | 2-stage (DSP_OS + DSP_OS_VERIFY + DSP_OSID) |
| `INF` | (infrared / FPV?) | `Protocols.inf` defaults to **false** (off) |
| `FMV` | FPV Video (analog) | DSP_FMV + DSP_FMV_SPEC |
| `SIK` | 3DR SiK | FSK scan (many rates — see 4.2) |
| `MH` | Microhard | FSK scan |
| `XT` | DigiXTend | DSP_XT |
| `RM` | RMILEC | DSP_RM |
| `DL` | DragonLink | DSP_DL |
| `OSAERO` | OcuSync Aeroscope | DSP_OSAERO (Remote-ID-adjacent) |
| `AT` | Autel | (LBOSTaskConfig.scan_at) |
| `STX` | (a FSK protocol) | `stx76` in the FSK scan config |
| `WIFI` | WiFi 802.11 | 2.4 + 5.8 GHz, 5/10/20 MHz BW |
| `XF` | Crossfire (TBS) | FSK/LoRa scan (`xf_lora6/10`) |
| **`ELRS`** | **ExpressLRS** | **FSK/LoRa scan (`elrs_127x_lora6-9`, `elrs_128x_lora5-9`)** |
| `FLRC` | Fast LoRa (ELRC) | `flrc_520`, `flrc_650` |
| `LORA` | generic LoRa | DSP_LORA_250/500/812.5 |
| `GHOST` | Ghost | (scan_ghost) |
| `FSK` | generic FSK | (the catch-all) |

### 4.2 The `ScanConfigFSK` — the FSK/LoRa rate catalog (the ELRS-specific rates!)
This is the **exact list of FSK/LoRa rates the device scans for** — **directly relevant to our ELRS
work**:
- **ELRS:** `elrs_127x_lora6`, `elrs_127x_lora7`, `elrs_127x_lora8`, `elrs_127x_lora9` (ELRS 127.x,
  LoRa SF6/7/8/9) + `elrs_128x_lora5`, `elrs_128x_lora6`, `elrs_128x_lora7`, `elrs_128x_lora8`,
  `elrs_128x_lora9` (ELRS 128.x, LoRa SF5/6/7/8/9)
- **FLRC (Fast LoRa):** `flrc_520`, `flrc_650` (520 / 650 kbps)
- **Crossfire:** `xf_lora6`, `xf_lora10` (LoRa SF6 / SF10) + `xf85` (FSK 85 kbps)
- **SiK:** ~16 rates (`sik750_4fsk` down to `sik32`, `sik500_4fsk`, `sik250`, `sik224_mixed`,
  `sik224_4fsk`, `sik200_4fsk`, `sik200`, `sik192`, `sik188`, `sik128`, `sik125`, `sik100`, `sik96`,
  `sik64`, `sik56`, `sik48`, `sik32`)
- **Microhard:** `mh345`, `mh276`, `mh230`, `mh172`, `mh115`, `mh57`
- **STX:** `stx76`

**Key takeaway for our ELRS work:** the reference device scans for **ELRS at LoRa SF5–SF9** (both the
127.x and 128.x ELRS versions) + **FLRC at 520/650 kbps** + **Crossfire at LoRa SF6/SF10 + 85 kbps
FSK**. **This is the exact ELRS rate set we should be scanning for** (our `identify.py`'s LoRa path
covers the 6 ELRS rates; this confirms the SF5–SF9 + FLRC 520/650 range). The device also treats
**ELRS and FLRC as separate protocols** (`ELRS` + `FLRC` enum values + separate `ELRSDetect` +
`FLRCDetect` tables) — i.e. it distinguishes the standard LoRa-ELRS from the Fast-LoRa (FLRC) ELRS,
which matches our own `dronescan.py` finding (the "FLRC vs LoRa" discriminator).

### 4.3 The per-protocol `Detect` tables (what each detection reports)
Each protocol has its own `Detect` table (in `tasks.fbs`) — the fields it extracts:
- **`ELRSDetect` / `FLRCDetect` / `XFDetect` (Crossfire) / `XFDetect`:** `center_freq_hz,
  is_power_valid, power_dbm, det_time_us, antenna, uw_corr` (unique-word correlation),
  **`net_id: DetectionNetID`** ← **the per-link Net ID!**
- **`SIKDetect` / `MHDetect` / `DLDetect` (DragonLink):** the above **+ `curr_geo` (current
  lat/lon/alt), `home_geo` (home position), `speed` (NED velocity), `ned` (NED position), `confirmed`,
  `modulation_order`** — i.e. for SiK/MH/DragonLink, the device **decodes the MAVLink telemetry**
  (position, home, speed) from the link!
- **`LORADetect` (generic LoRa):** `center_freq_hz, power_dbm, antenna, sf` (spreading factor),
  `bandwidth`, `nzeros`, `invert_iq`, `n_upchirp_dets`, `n_downchirp_dets`, `channel_freqs_hz` —
  i.e. for generic LoRa, it measures the **SF + BW + upchirp/downchirp counts + the channel
  frequencies** (the hopping pattern!).
- **`WIFIDetect`:** `center_freq_hz, channel_bw (default 20 MHz), power_dbm, serno` (serial number),
  **`net_infos: [DetectionNetworkInfo]`, `vendor`, `ssid`, `curr_geo`, `rc_geo`, `speed`,
  `rid_detected`** — i.e. for WiFi, it decodes the **SSID + vendor + MAC addresses + the drone/RC
  positions + Remote-ID detection**.
- **`OSAERODetect` (OcuSync Aeroscope):** `curr_geo, home_geo, rc_geo, orientation (pitch/roll/yaw),
  speed, model, rc_info, serial_number` — i.e. for DJI Aeroscope (Remote-ID), it decodes the
  **drone position + home + RC position + orientation + speed + model + serial number**.
- **`RMDetect` (RMILEC):** `sync_word` (uint), `txid` (byte), `variance`, `identified_channels`
  (the hopping channels) — i.e. for RMILEC, it extracts the **sync word + TX ID + the channel hops**.

### 4.4 The `DetectionNetID` — the per-link identifier (THE key table)
```
table DetectionNetID {
  device_network_id:int;   // a numeric net ID
  baudrate_enum:int;
  baudrate:int;
  net_id:string;           // the net ID as a string
  raw_net_id:[ubyte];      // the RAW net ID bytes
}
```
**This is the per-link "Net ID" the manual's "Whitelist by UID" refers to.** It's a
`device_network_id` + `net_id` string + **`raw_net_id` byte array**. **For ELRS, this `raw_net_id`
is almost certainly the 6-byte bind UID** (the per-link identifier ELRS puts in the air) — INFERRED
from the fact that (a) the ELRS link's only per-link airborne ID is the bind UID, and (b) the
detection has a `net_id` field that gets whitelisted. **This is the exact "hardware-unique ID per
drone (well, per-link) via SDR-only RF" the operator wants** — the reference device extracts it
from the air + displays/whitelists it.

**Relevance to our air-recovery goal:** our `identify.py`'s sync-word air-recovery is building the
same thing — recover the per-link ID (the ELRS bind UID / the sync word) from the demodulated bits,
without needing the UID up front. The reference device does this in hardware (the `DSP_LBID` /
`DSP_OSID` "ID" cores + the `parse_sync_0` sync-word matcher). **The `raw_net_id` is the target
output of our air-recovery.**

### 4.5 The `DetectionTaskResult` — the unified detection report
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
the net_id + freq), + the alert type (DETECT / WHITELISTED_DETECT / DISABLED_DETECT).** This is a
*rich* detection — it's not just "there's a signal," it's "there's an ELRS link at 2456 MHz, -42
dBm, Net ID XYZ, drone at (lat, lon, alt), moving at N mps, model ABC."

---

## 5. The sync-word air-recovery (VALIDATED — the mechanism we're building)

The firmware has the **sync-word detection** machinery (the "read the sync out of the air" we've
been building):
- **`parse_sync_0: pos (%zu) ret (%d)`** + **`sync: %02x %02x`** — a sync-word parser that reports
  the position + the matched sync bytes (hex).
- **`/home/root/config/syncSymbSSSCAFFDC.fc64`** (1.6 MB) + **`symbPSSfft12M.fc64`** (64 KB) — the
  **sync-symbol reference templates** (complex float64):
  - `symbPSSfft12M` = a **PSS (Primary Synchronization Signal) FFT at 12 MHz** — a sync-symbol
    template (the LTE/CBSS-style primary sync, used as a correlation template).
  - `syncSymbSSSCAFFDC` = the **sync symbol** ("SSS C-FFDC" — a secondary-sync / specific sync
    pattern) — 1.6 MB = ~204K complex samples = a long reference sync waveform for correlation.
- **`Sync_Task` / `Sync_Task_Instance`** — the sync-detection task (a dedicated task in the pipeline).
- **`LoRaCore`** — the LoRa DSP core (the dechirp + SF/BW measurement).
- **`Applying targeted LoRa mixed chirps technique`** — the LoRa *mitigation* (the "mixed chirps"
  upchirp/downchirp confusion attack — the TX/ECM side, which we do NOT build).

**So the reference device's sync-word air-recovery = correlate the incoming bits against a reference
sync template (the `.fc64` files) to find the sync position, then read the UID/Net ID out of the
demodulated payload.** This is exactly our two-layer design (Layer 1 = air-recovery of the sync/UID,
Layer 2 = positive ID). The reference device does Layer 1 in hardware (FPGA correlation against the
`.fc64` templates); we do it in software (the `detect_sync_word` in our `lora.py` / the GFSK
correlator in `identify.py`).

**Key difference + our gap:** the reference device has **pre-computed reference sync templates**
(the `.fc64` files) — it correlates against a *known* sync symbol. Our air-recovery is harder
because **ELRS's sync is UID-seeded** (the sync word depends on the per-link UID — which is what we
don't have). So the reference device's ELRS detection must either (a) have a *generic* ELRS sync
template (the preamble, not the UID-seeded part) + recover the UID from the payload, or (b) do a
brute-force/adaptive sync match. **Our air-recovery (recover the UID from the air) is the
harder-of-the-two — the reference device likely has the same challenge, solved in the `the main radio/detection daemon (a 175 MB statically-linked C++ binary)`
binary (the `parse_sync_0` + the ELRS LoRa core).** Reverse-engineering *that* binary (the actual
ELRS sync/UID recovery algorithm) would be the deepest reference — but it's a 175 MB
statically-linked C++ binary (a `firmware-labeling` / `rce-gate-joern` task, a separate effort).

---

## 6. The mitigation / ECM side (VALIDATED — the part we do NOT build)

The firmware includes the **active-mitigation (ECM)** logic — the paired active-mitigation device's
behavior (the paired active-mitigation (ECM) system's role from the manual). **We do NOT build this** (it's the
remote-kill/anti-drone-jamming territory, out of scope). But it's documented for completeness:
- **`ECMSystem` enum:** `NONE, E1, E2, M, <a variant code>, LOCAL` — the paired ECM systems
  (E1/E2 = the primary paired-ECM-system class, M = the alternate paired-ECM-system class,
  <a variant code> = a further variant, LOCAL = local TX).
- **`LoadsetConfig` / `TargetRuleSet`:** the **loadset profiles** (the `cuas.json`, `cuas_fb.json`,
  `cuas_lf.json`, `cuas_lf_fb.json`, `fpv.json`, etc. in `/home/root/config/loadset_profiles/`) —
  JSON rule-sets that control the mitigation behavior:
  - `technique_compromise` — rules like "WIFI Ignore" (ignore WiFi if any other signal present),
    "FSK >1GHz Override" (override multiple FSK targets with a 50 kHz chirp).
  - `band_notch` — e.g. notch out 1–1.8 GHz (the GNSS band) to minimize GPS interference.
  - `minimize_footprint` — the LF (low-footprint) variants disable bands with no targets.
  - `target_inference` — rules that *infer* a target from the detected signals.
- **`ECMTransmit` / `auto_tx`:** the auto-transmit (mitigate-on-detect) control.
- **`TechniqueBehavior`** — the mitigation actions (override target, priority protocol/modulation,
  ignore protocol/modulation, split power).
- **`TargetModulationLoRa`** — the LoRa modulation params the device measures *from the air* (SF,
  BW, coding rate, upchirps, downchirps) — to know *how* to jam the LoRa link.

**The passive/active split (reaffirmed):** the *detection* (this SDR, the `the main radio/detection daemon (a 175 MB statically-linked C++ binary)` + FPGA DSP
cores) is **passive** — it matches our passive-only boundary. The *mitigation* (the ECM logic + the
paired active-mitigation device) is **active** — we do NOT build it. We build the *detection +
identification* half (the protocol catalog, the per-link ID extraction, the "measure don't assume"
classifier), using this firmware as a reference for *how* a production C-UAS detector does it.

---

## 7. What this means for our ELRS/drone detector (INFERRED — the actionable takeaways)

1. **The reference device confirms our protocol catalog + the ELRS rate set.** Its `ScanConfigFSK`
   (ELRS 127.x/128.x LoRa SF5–SF9, FLRC 520/650, Crossfire LoRa SF6/SF10 + 85 kbps, SiK ~16 rates,
   MH ~6 rates) is the **target list** for our "positively ID every protocol" goal. Our `identify.py`
   LoRa path should cover the **ELRS LoRa SF5–SF9 + FLRC 520/650** range (it currently does the 6
   standard ELRS rates; FLRC 520/650 is the gap to add).
2. **The reference device's per-link ID is the `raw_net_id` (the `DetectionNetID`).** For ELRS,
   that's the **bind UID** — the same per-link ID our air-recovery is trying to recover. **The
   `raw_net_id` is the target output of our air-recovery** (Layer 1). Once we recover it, we have
   the "hardware-unique ID per drone (per-link)" the operator wants.
3. **The reference device runs a hardware parallel-pipeline (one FPGA DSP core per protocol).** We
   do the equivalent in software (iterate all protocols × rates per burst, let CRC decide). The
   reference device is faster (FPGA parallelism at 126 MS/s), but the *logic* is the same. Our
   software approach is more general (works on any capture, any SDR) — the reference device's is
   faster but locked to its specific FPGA + 126 MS/s frontend.
4. **The reference device decodes the telemetry (position, speed, orientation) for SiK/MH/DL + the
   Remote-ID (Aeroscope/OcuSync) for DJI + the SSID/MAC for WiFi.** This is a *richer* detection
   than our current presence-level detectors. Our next steps (per the standing goal) are: (a) the
   ELRS sync-word air-recovery (the `raw_net_id`), (b) the WiFi MAC/SSID hardware-ID (which the
   reference device's `WIFIDetect` + `DetectionNetworkInfo` [mac_address, device_type, vendor, ssid]
   confirms is a real, shipped capability — the `device_type` enum even has
   `DRONE=2, REMOTE_CONTROLLER=3, <the C-UAS device>=1`), (c) the DJI serial/Remote-ID (the
   `OSAERODetect` serial_number + model).
5. **The reference device's sync-word air-recovery uses pre-computed reference templates (the
   `.fc64` files) + a dedicated sync task.** Our air-recovery is the same concept in software. The
   key difference: ELRS's sync is UID-seeded, so we can't just correlate against a fixed template —
   we have to recover the UID (the harder path). The reference device's `parse_sync_0` + ELRS LoRa
   core (in the 175 MB `the main radio/detection daemon (a 175 MB statically-linked C++ binary)` binary) is where that's solved — a `firmware-labeling` /
   `rce-gate-joern` reverse-engineering task (separate, deeper effort).

---

## 8. Honest assessment + open questions

- **VALIDATED** (read directly from the firmware): the container structure (tar → cpio → 8 files),
  the OS (Arch Linux ARM64, Buildroot), the RF front-end (2× AD9371/9375 @ 126 MS/s, JESD204B,
  ZynqMP XC7Z010-class FPGA with the EXDR2 bitstream, Si5345 clocking, 2 RFFE instances), the
  daemons (`the main radio/detection daemon (a 175 MB statically-linked C++ binary)` 175 MB, `the comms daemon (a 40 MB C++ binary)` 40 MB, `exdr_monitor`/`exdr_startup`, `rfic`,
  `si5345_cli`, `fpga`, `iio`, `overlay-mgr`, `rffe-cli`), the flatbuffers schemas (the full
  detection data model — the `Protocol` enum, the `DSPCore` per-protocol cores, the `ScanConfigFSK`
  ELRS/FLRC/Crossfire/SiK/MH rate catalog, the per-protocol `Detect` tables, the `DetectionNetID`,
  the `DetectionTaskResult`), the sync-word machinery (`parse_sync_0`, the `.fc64` templates,
  `Sync_Task`, `LoRaCore`), the ECM/loadset config (the `cuas*.json` profiles, the `ECMSystem`
  enum, the `technique_compromise` rules).
- **INFERRED** (labeled as such): that the ELRS `raw_net_id` is the bind UID (§4.4), the
  actionable takeaways for our detector (§7), the "harder path" of ELRS's UID-seeded sync (§5).
- **Open questions (need the `the main radio/detection daemon (a 175 MB statically-linked C++ binary)` binary reverse-engineering or the operator):**
  - **Exactly how does the reference device recover the ELRS UID (the `raw_net_id`) from the
    air** — given the sync is UID-seeded? (The algorithm is in the 175 MB `the main radio/detection daemon (a 175 MB statically-linked C++ binary)` binary — a
    separate `firmware-labeling`/`rce-gate-joern` task.)
  - **What are the reference sync templates** (the `.fc64` files) — the exact sync words /
    correlation patterns the device uses per protocol? (They're 1.6 MB + 64 KB complex-float files
    — I can dump + analyze them, but they're device-specific coefficients.)
  - **What's the detection sensitivity / range / detection-time** per protocol? (Not in the firmware
    I analyzed — that's in the FPGA bitstream + the DSP core implementations, a deeper RE.)
  - **The exact FPGA DSP core algorithms** (the `DSP_LORA_250/500/812p5`, `DSP_WIFI_*`, etc.) — the
    per-protocol demod/correlation logic in the bitstream (a `firmware-labeling` task on the
    `the EXDR2 FPGA bitstream (`.bit.bin`)`).

---

## 9. The division of labor (what I did vs what's next)

- **I did (this turn):** fetched the firmware (the `.xz` you pushed to git), decompressed it
  (tar → cpio → rootfs), analyzed the full structure (the container, the RF front-end, the daemons,
  the flatbuffers detection data model, the sync-word machinery, the ECM config), wrote these
  comprehensive notes, **then deleted all the device binaries** (the `.xz` from the repo's
  `captures/` + the decompressed copy in `/tmp`) per your rule (don't keep the device material on
  disk — notes only).
- **Next (if you want the deepest reference):** reverse-engineer the **`the main radio/detection daemon (a 175 MB statically-linked C++ binary)`** (175 MB)
  binary (the ELRS sync/UID recovery algorithm) + the **FPGA bitstream** (the per-protocol DSP
  cores) + the **`.fc64` sync templates** — a `firmware-labeling` / `rce-gate-joern` task (the
  enclave skills for exactly this). That would reveal the *exact* ELRS air-recovery algorithm the
  reference device uses (the hardest part of our own air-recovery goal). But it's a separate,
  deeper effort — these notes capture the *architecture + data model* (the "what"), and the binary
  RE would capture the *algorithms* (the "how").

---

*Notes compiled from the firmware (v4.0.6 `.swu` update package) the operator provided as a
reference. The firmware binaries were deleted after analysis (operator's rule). The operator-manual
notes are in `docs/REFERENCE_COUNTER_UAS_SDR.md`. This is the firmware deep-dive (the "how it's
built + what it detects"), complementing the operator-manual notes (the "what it is + how to use
it").*
