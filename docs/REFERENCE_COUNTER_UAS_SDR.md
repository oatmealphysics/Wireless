# Reference: Generic Counter-UAS (C-UAS) Passive SDR — Device + Firmware Notes

> **Naming note (operator directive):** do **not** call this device "BC," "Bal-Chatri,"
> "BC2," or any vendor/make name. Refer to it generically as the **"counter-UAS passive SDR"**
> (or "the C-UAS detector"). This file documents a **reference counter-UAS SDR** the operator
> provided (a PDF user manual + a firmware `.swu` update package) as a *reference* for our own
> passive ELRS/drone detector. Per the standing honesty contract, every claim below is labeled
> **VALIDATED** (read directly from the provided document) vs **INFERRED** (my reasoning from it).

**Source artifacts (in `/opt/data/Drone_Project/references/` on the dashboard host):**
- `BC.pdf` (9.5 MB, 122 pages) — a **user/operation manual** for the device (GUI walkthrough,
  kit photos, power/antenna/LED info). **This is the source for all the notes below.**
- `bc2-4.0.6.swu` (195 MB) — the **firmware** (a Software-Update package, version 4.0.6). **NOT
  yet analyzed** — it's over the dashboard files-API size cap (~100–128 MB; the 76 MB ardupilot
  tarball fetched fine, the 195 MB `.swu` returns `{"detail":"File is too large"}` / HTTP 413).
  To analyze the firmware (where the actual detection *algorithms* live) it needs to be pushed to
  the git repo (`git add -f`) or Nextcloud. See "Firmware (.swu) — next step" below.

**Provenance / marking:** the manual is stamped **CONTROLLED UNCLASSIFIED INFORMATION (CUI)**
with a **U.S. Special Operations Command (USSOCOM)** seal; the vendor is **Thales / Harris**
(manual page 3: "Thales/Harris 148, 152 & 163"). It's a U.S. DoD C-UAS product. We're using it as
a *technical reference* for our own passive detector (authorized research on the operator's own
gear); we are **not** reproducing the CUI in any deliverable — these notes are paraphrased/abstract.

---

## 1. What the device is (VALIDATED — from the manual)

- **A completely passive, library-based Software Defined Radio (SDR)** used for **detecting
  Type I and II COTS (commercial-off-the-shelf) Unmanned Aerial Systems** (manual p.2).
  - "Completely passive" = **receive-only detection** in its base role. (This matches our own
    passive-only boundary — the *mitigation/TX* is a **separate, paired device**, not this one.)
- **Physical form:** configurable for **dismounted, mounted, or fixed-site** use; the **GUI runs
  on any Android device** (a tablet) via a plugin in the **ATAK** app (the U.S. military's
  team-communications app) (p.2, p.18–23).
- **Power:** 110 VAC outlet, vehicle power adaptor, or batteries (~4 hr battery life) (p.3).
- **Antennas (2 ports):**
  - **Port 1 (MP-09, HW `BC_ANT_1`): 0 – 2000 MHz** (p.60)
  - **Port 2 (L-COM, HW `BC_ANT_2`): 2000 – 6000 MHz** (p.60)
  - ⇒ **The 2.4 GHz drone band (2400–2480 MHz) is covered by the L-COM / 2000–6000 MHz antenna.**
    (This is the band our ELRS work lives in — so this device's 2.4 GHz detector is directly
    relevant as a reference.)
- **Status LEDs** indicate system state (Idle / Scanning / Transmit / low-power detect /
  high-power detect / RFIC failure / updating) (p.17).
- **Rudimentary spectrum analyzer** as an extended-menu function (not calibrated; a "confidence
  check" for RF-emitting equipment; won't function below 300 MHz) (p.85–87).

**The architecture (VALIDATED):** the passive SDR (this device) **detects**; it can be **paired
with a separate "Modi" or "Egon II" system for RF mitigation** (the TX/ECM side) (p.2, p.30, p.33,
p.59). So: **detection (passive, this SDR) is decoupled from mitigation (active, the paired ECM
device).** This is exactly the passive/active split we maintain — the detector never transmits;
a separate, explicitly-paired system does the mitigation.

---

## 2. The protocol catalog (VALIDATED — the "Active Protocols" list, p.25)

This is the **most directly relevant section** for our work. The device scans for **16 named
drone RF protocols** (15 enabled by default, 1 — WiFi — disabled by default). The operator can
toggle each on/off ("deactivating protocols serves as an example of whitelisting," p.25).

| # | Protocol (as listed) | Default | Our relevance |
|---|---|---|---|
| 1 | **DJI LightBridge** | ON | DJI video link (we have a presence detector, not a full decoder) |
| 2 | **DJI OcuSync** | ON | DJI C2/video (we have an `ocusync_phy` presence detector — UNCONFIRMED) |
| 3 | **Video** | ON | generic analog/digital video |
| 4 | **Autel Evo** | ON | Autel (another COTS vendor) |
| 5 | **NS1** | ON | (narrowband protocol — likely a specific vendor's 2.4/5.8) |
| 6 | **WiFi** | **OFF** | 802.11 (we have a full L-SIG/L-STF 802.11 detector) |
| 7 | **ExpressLRS** | ON | **← our primary target** (ELRS) |
| 8 | **Ghost** | ON | Ghost TX (a known ELRS-adjacent long-range protocol) |
| 9 | **SiK - Custom Drone** | ON | 3DR SiK (an OpenTX/CRSF FHSS protocol) |
| 10 | **Microhard** | ON | (a COTS drone protocol) |
| 11 | **DigiXTend - Custom Drone** | ON | (a long-range custom protocol) |
| 12 | **RMILEC - Long Range Drone** | ON | (long-range protocol) |
| 13 | **DragonLink - Custom Drone** | ON | (a COTS long-range FHSS) |
| 14 | **OcuSync Aeroscope** | ON | DJI Aeroscope (Remote-ID-adjacent) |
| 15 | **Crossfire** | ON | **← the ELRS sibling** (TBS Crossfire, LoRa-based, same PHY family as ELRS) |
| 16 | **LoRa** | ON | **← generic LoRa** (ELRS/Meshtastic/sub-1G — the family label) |

**Key takeaways (INFERRED from the catalog):**
- **The device treats "ExpressLRS," "Crossfire," and "LoRa" as three *separate* protocol entries**
  — even though ELRS and Crossfire are both LoRa-PHY (ELRS is a fork of Crossfire). This tells us
  the vendor **fingerprinting distinguishes ELRS from Crossfire from generic LoRa** (payload-layer
  or timing differences), not just "it's a LoRa chirp." **That's a higher bar than our current
  LoRa presence detector** (which labels the *family* "LoRa (ELRS/Meshtastic/sub-1G)," not the
  specific protocol). Our `dronescan.py` classifier is at the "family" level; this device is at
  the "specific protocol" level for the LoRa class.
- **The catalog is broader than ELRS** — it covers DJI (OcuSync + LightBridge + Aeroscope), Autel,
  SiK, DragonLink, Crossfire, Ghost, generic LoRa, generic Video, WiFi, + several "custom drone"
  long-range protocols (DigiXTend, RMILEC, Microhard, NS1). **This is a reference catalog for
  "which C-UAS should we detect"** — it validates the protocol set we should aim for in our
  "positively ID every drone protocol" goal.
- **WiFi is OFF by default** — interesting: the vendor defaults to *not* scanning WiFi (probably
  because WiFi is ubiquitous + a C-UAS concern is the *drone* links, not the ambient WiFi). We
  scan WiFi (for the MAC/SSID hardware-ID goal).

---

## 3. Identification by UID / Net ID / Serial Number (VALIDATED — p.27, p.63)

- A detection can be **whitelisted "by UID"** (p.27) — and the detection menu has a
  **"Copy Net ID to Clipboard"** action (p.27). The whitelist settings (p.63) say **"UAS platforms
  can be whitelisted by Network IDs or Serial Numbers."**
- ⇒ **The device extracts a per-platform identifier (a "Net ID" / Network ID / Serial Number)
  from the detected link** and uses it to (a) label the detection and (b) whitelist specific
  platforms. **This is exactly the "hardware-unique ID per drone" the operator wants.**
- **How it gets the Net ID (INFERRED):** for the protocols that carry a device ID in the air
  (e.g. DJI's serial/Aeroscope, WiFi's BSSID/MAC, some long-range protocols' Net ID), the device
  demods enough to read the ID field. For ELRS specifically, **the airborne "ID" is the 6-byte bind
  UID** (a pairing secret, not hardware-unique — per our ELRS analysis). So for an ELRS link, this
  device's "UID" is almost certainly the **ELRS bind UID** (the only per-link identifier ELRS puts
  in the air). **This is a strong reference signal that the vendor also keys ELRS detection on the
  bind UID** — the same per-link identifier we're trying to *recover from the air* (our
  air-recovery goal).

**Relevance to our work:** the operator's end goal is a **hardware-unique ID per drone via
SDR-only RF.** This device demonstrates that a commercial C-UAS product does exactly this — it
detects the link, extracts a per-platform ID (Net ID / Serial / UID), and displays/whitelists it.
For ELRS, that ID is the bind UID (which we recover from the air). For WiFi/DJI, it's the MAC/
BSSID/serial. **Our detector should aim to extract + display the same per-link ID the way this
device does** — it's the proof that the "identify by a unique ID from the RF" goal is a real,
shipped product capability, not a fantasy.

---

## 4. The detect → (paired ECM) → mitigate flow (VALIDATED — p.26–38)

- **Detection:** "Strongest Detections" + "All Detections" lists show the detected threats
  (protocol, frequency, Net ID) (p.26–28, p.44–45).
- **Whitelisting:** by UID/Net ID/Serial (p.27–28, p.63–65).
- **Engagement / mitigation:** "Engagement Management" + profile selection + "Save and TX" —
  **only available when connected to an Egon II** (a paired ECM device) (p.30–31, p.33). The
  profiles (CUAS, CUAS_FB, CUAS_LF, CUAS_FPV, etc.) control the *mitigation* behavior:
  - **CUAS_FPV / CUAS_FPV_FB / CUAS_FPV_LF**: **"Prioritizing LoRa-based threats"** (p.37–38) —
    confirms the vendor has a **LoRa/ELRS-specific mitigation mode** (FPV drones are the LoRa/ELRS
    class).
  - `CUAS_*_FB`: "Notch to minimize GNSS interference" (the mitigation notches out the GNSS band
    so it doesn't jam the friendly GPS).
  - `CUAS_*_LF`: "Non-targeted bands default to OFF" (lower-frequency / less-collateral mode).
- **Auto-Transmit:** an option to transmit (mitigate) automatically upon detection without user
  input (p.41–42) — again, only with the paired ECM.

**Relevance (the boundary, reaffirmed):** the *detection* half (this SDR) is passive — it matches
our passive-only boundary exactly. The *mitigation* half (the paired Egon II/Modi) is the active
ECM, which is a **separate device** and squarely in the "out of scope — remote-kill /
anti-drone-jamming" territory we do **not** build. We build the *detection + identification* half
(passive), using this device as a reference for the protocol catalog + the ID extraction. We do
**not** build the mitigation/ECM half.

---

## 5. What this means for our ELRS/drone detector (INFERRED — the actionable takeaways)

1. **The protocol catalog (§2) is a reference target list** for our "positively ID every drone
   protocol" goal. Our `dronescan.py` classifier covers the RF-distinguishable families (LoRa,
   802.11, analog video, FHSS, OcuSync-presence); this device's catalog confirms the *specific*
   protocols to aim at (DJI OcuSync/LightBridge, Autel, SiK, DragonLink, Crossfire, Ghost,
   ELRS, generic LoRa). **Gap to close:** we're at the *family* level for LoRa; this device is at
   the *specific-protocol* level (ELRS vs Crossfire vs generic LoRa). That requires payload/timing
   fingerprinting (a deeper demod), not just the chirp presence detector.
2. **The "identify by Net ID / UID / Serial" capability (§3) is the model for our ID goal.** For
   ELRS, the per-link ID is the bind UID (which we recover from the air — our air-recovery goal).
   The device shows this is a shipped, real capability. **Our detector should extract + display
   the per-link ID the same way** (the recovered sync/UID for ELRS, the MAC/BSSID for WiFi, the
   serial for DJI).
3. **The passive-detect / active-mitigate split (§4) validates our architecture + boundary.** We
   build the passive detector (like this SDR's detection role); we do **not** build the active
   ECM (the paired Egon II/Modi role).
4. **The 2.4 GHz coverage is via the 2000–6000 MHz antenna (§1)** — the same band our ELRS work
   is in. The device's 2.4 GHz detector (for ELRS/Crossfire/LoRa/SiK/DragonLink/etc.) is the
   directly-relevant reference for our ELRS detector.
5. **The firmware (the `.swu`) is where the actual detection algorithms are** — the manual (this
   PDF) is only the operator's GUI walkthrough; it does **not** contain the detection/demod
   algorithms, the per-protocol signature details, the UID-extraction logic, or the LoRa-vs-ELRS-
   vs-Crossfire fingerprinting. **To learn *how* it detects (the algorithms), we'd need to reverse-
   engineer the `.swu` firmware** — which is blocked by the files-API size cap (see below).

---

## 6. Firmware (`.swu`) — what it is + why we can't fetch it yet + the next step

- **`bc2-4.0.6.swu`** (195 MB) is the device's **firmware** — a **Software-Update package**
  (version 4.0.6). The manual (p.75–81) describes pushing an "SWU" from the Android tablet to
  update the device's firmware (requires the device to be on v4.0.0+). So the `.swu` is the
  updatable firmware image for the SDR's DSP/processor.
- **It's over the dashboard files-API size cap.** The cap is somewhere **between 76 MB (worked —
  the ardupilot tarball fetched HTTP 200) and 195 MB (failed — `{"detail":"File is too large"}`,
  HTTP 413).** Likely 100 MB or 128 MB. A `Range` request doesn't help — the endpoint checks the
  *full* file size, not the range.
- **Why it matters:** the firmware is where the **detection algorithms** live (the per-protocol
  signature detectors, the LoRa/ELRS/Crossfire fingerprinting, the UID/Net-ID extraction, the
  hop-tracking). Reverse-engineering it would be the highest-value reference for our own detector
  (exactly the "firmware that uses custom SDR to detect drones" the operator asked about earlier).
- **Next step to get it analyzed (one of):**
  1. **Push it to the git repo** (`git add -f captures/.../bc2-4.0.6.swu` + push) — the operator
     can do this from the laptop/dashboard. 195 MB is large for git but workable (the repo already
     carries ~160 MB sweep captures).
  2. **Upload to Nextcloud** (the enclave file store) — I can then `curl` it from Nextcloud (no
     size cap like the files API).
  3. **Split it** (e.g. `split -b 90m` into 3 chunks under the cap) and fetch the chunks via the
     files API, then reassemble — a workaround if git/Nextcloud aren't preferred.
- **Once we have it:** the plan is to identify the firmware container type (Thales/Harris SWU is
  typically a **signed + compressed** bundle — likely a `tar`/`zip` of an embedded image, possibly
  an ARM/Linux rootfs + the DSP firmware), extract the embedded binaries, and look for the
  detection/protocol logic (strings like the protocol names in §2, the LoRa/ELRS constants, the
  UID-extraction code). This is a **`firmware-labeling` / `rce-gate-joern`** task (the enclave
  skills for exactly this) — a separate, deeper effort than these notes.

---

## 7. Honesty contract + open questions

- **VALIDATED** (read directly from the provided PDF manual): everything in §1 (device type,
  passive, 2-antenna coverage, 0–2 GHz + 2–6 GHz, Android/ATAK GUI, LED states, spectrum
  analyzer), §2 (the 16-protocol catalog + their names + on/off defaults), §3 (the "Whitelist by
  UID" / "Copy Net ID" / "Network IDs or Serial Numbers" text), §4 (the detect→ECG-mitigate flow,
  the CUAS_FPV "prioritizing LoRa-based threats" profiles, the Egon II/Modi pairing, auto-TX).
- **INFERRED** (my reasoning, labeled as such): the per-protocol specificity (ELRS vs Crossfire vs
  generic LoRa as distinct entries = a higher fingerprinting bar than our family-level detector),
  that the ELRS "UID" is the bind UID, the actionable takeaways for our detector (§5), the
  firmware container-type guess (§6).
- **Open questions (need the firmware or the operator to confirm):**
  - **What is the exact per-protocol signature** the device uses to distinguish ELRS from
    Crossfire from generic LoRa? (The manual doesn't say; the firmware would.)
  - **What exactly is the "Net ID" / "UID" for each protocol** (is the ELRS one the bind UID? the
    WiFi one the BSSID? the DJI one the serial?)? (The manual shows the *UI* but not the
    extraction logic.)
  - **What's the detection sensitivity / range / detection-time** for each protocol? (Not in the
    manual — it's an operation manual, not a performance spec.)
  - **What processor / DSP / SDR frontend** does the device use? (Not stated in the manual — the
    firmware hardware IDs would hint at it.)

---

*Notes compiled from the provided `BC.pdf` (122-page user manual) + directory listing of the
`bc2-4.0.6.swu` firmware. The firmware itself is not yet analyzed (files-API size cap). See §6
for the path to get it analyzed.*
