# RF Disruption Feasibility: Forcing ELRS TX into WiFi Mode

**Date:** 2026-10-15
**Status:** Research and analysis only — no RF transmission
**Source:** ExpressLRS 4.1.0 source analysis, SX1280 datasheet, Ettus B205mini/B210 specs
**Scope:** Can we wirelessly force an ELRS TX to enter WiFi update mode by disrupting its
2.4 GHz link, enabling extraction of the binding phrase?

> **Constraint:** This document is analysis only. No RF transmission, jamming, or active
> interference has been performed or is authorized without a separately approved bench
> plan and confirmation that CTF rules permit it (open question Q4).

---

## 1. Executive Summary

Forcing an ELRS TX into WiFi mode via RF disruption is **not feasible against
integrated controllers like the EMAX E8**. The auto-WiFi trigger depends on the
absence of CRSF serial communication from the handset, not the absence of an RF
link to the receiver. Since integrated controllers detect CRSF on their internal
serial bus at boot, RF disruption of the air link does not trigger auto-WiFi.

For **standalone TX modules** (USB dongles, external bay modules without a host
handset), the attack chain is theoretically viable but faces significant power
budget challenges and legal constraints.

For the **RX side**, the SPI ELRS receiver in the TinyHawk III has no WiFi
hardware, so RX-side WiFi forcing is not applicable to our target drone.

| Target | Auto-WiFi Trigger | RF Disruption Viable? |
|---|---|---|
| E8 controller (integrated TX) | No CRSF on internal UART | **No** — internal bus always active |
| Standalone ELRS TX module | No CRSF from host radio | **Conditional** — only if powered without host |
| TinyHawk III RX (SPI) | N/A — no WiFi hardware | **No** — no ESP32, no WiFi stack |
| Standalone ESP32 ELRS RX | RF link disconnected state | **Yes in theory** — see power budget |

---

## 2. ELRS Connection State Machine (Source-Verified)

### 2.1 RX Connection States

The ELRS receiver cycles through these states (`rx_main.cpp`):

```
disconnected → tentative → connected → (lost) → disconnected
                                    ↘ wifiUpdate (if auto-WiFi enabled)
```

**Transition: connected → disconnected (LostConnection)**

The RX declares connection lost when no valid packet is received for
`DisconnectTimeoutMs` milliseconds. From `rx_main.cpp:2115`:

```c
if ((connectionState == connected) &&
    ((int32_t)ExpressLRS_currAirRate_RFperfParams->DisconnectTimeoutMs
     < (int32_t)(now - localLastValidPacket)))
{
    LostConnection(true);
}
```

### 2.2 Disconnect Timeouts by Rate (SX1280 / 2.4 GHz)

Source: `ExpressLRS_AirRateRFperf[]` in `common.cpp`, SX128X section.

| Rate | Mode | Interval (µs) | Disconnect Timeout (ms) | RX Sensitivity (dBm) |
|---|---|---|---|---|
| FLRC 1000 Hz | FLRC | 1,000 | 2,500 | -104 |
| FLRC 500 Hz | FLRC | 2,000 | 2,500 | -104 |
| LoRa 500 Hz | SF5/BW800 | 2,000 | 2,500 | -105 |
| LoRa 333 Hz 8ch | SF5/BW800 | 3,003 | 2,500 | -105 |
| LoRa 250 Hz | SF6/BW800 | 4,000 | 3,000 | -108 |
| LoRa 150 Hz | SF7/BW800 | 6,666 | 3,500 | -112 |
| LoRa 100 Hz 8ch | SF7/BW800 | 10,000 | 3,500 | -112 |
| LoRa 50 Hz | SF8/BW800 | 20,000 | 4,000 | -115 |

**Key observation:** Even the slowest rate (50 Hz) only needs 4 seconds without a
valid packet to disconnect. But disconnection alone does not trigger WiFi mode — it
triggers RF mode cycling (scanning for the TX on different rates).

### 2.3 RF Mode Cycling (Disconnected State)

When the RX enters `disconnected`, it does not immediately enter WiFi mode. Instead,
it cycles through all supported air rates looking for the TX (`rx_main.cpp:1619-1648`):

```c
static void cycleRfMode(unsigned long now)
{
    if (connectionState == connected || connectionState == wifiUpdate || InBindingMode)
        return;
    if (LockRFmode == false &&
        (now - RFmodeLastCycled) > (cycleInterval * RFmodeCycleMultiplier))
    {
        SetRFLinkRate(scanIndex % RATE_MAX, false);
        scanIndex++;
        Radio.RXnb();
        RFmodeCycleMultiplier = 1;
    }
}
```

The `cycleInterval` is 110% of the time to cycle through all FHSS channels at the
current rate. For a typical configuration:
- 80 channels × FHSShopInterval × packet interval × 1.1
- At 150 Hz (6,666 µs interval, FHSShopInterval=4): 80 × 4 × 6,666 × 1.1 / 1000 ≈ 2,346 ms per rate
- With 10 rates (RATE_MAX for SX128X): full cycle ≈ 23 seconds
- Initial multiplier is `RFmodeCycleMultiplierSlow/2` = 5, so first scan per rate ≈ 12 seconds

**WiFi mode only activates after `wifi_auto_on_interval` expires while the RX stays
in `disconnected` state** — which means surviving the entire rate-cycling scan period
without a single valid packet.

### 2.4 TX Auto-WiFi: The CRSF Gate

The critical finding for the E8 attack scenario. From `devWIFI.cpp:1409-1416`:

```c
#if defined(TARGET_TX)
if (firmwareOptions.wifi_auto_on_interval != -1
    && webserverPreventAutoStart == false
    && connectionState < wifiUpdate
    && !wifiStarted)
{
    DBGLN("No CRSF ever detected, starting WiFi");
    setWifiUpdateMode();
}
```

The `webserverPreventAutoStart` flag is set to `true` the moment **any CRSF data
is detected on the UART** (`tx_main.cpp:724`). This is a wired serial bus check,
not an RF link check.

**For integrated controllers (E8, RadioMaster Zorro, etc.):** The handset's MCU
sends CRSF channel data to the ELRS module over the internal serial bus from the
moment it boots. RF jamming cannot suppress this — it's a wired PCB trace, not
an over-the-air link.

**For standalone modules (USB dongle, bay module without host):** If the module
powers on without any CRSF source, `webserverPreventAutoStart` stays `false`, and
the module enters WiFi mode after `wifi_auto_on_interval` (default: disabled;
when set, typically 20-60 seconds).

---

## 3. FHSS Parameters and Disruption Surface

### 3.1 Frequency Plan (2.4 GHz ISM, SX1280)

Source: `FHSS.cpp`, ISM2G4 domain.

| Parameter | Value |
|---|---|
| Band start | 2,400.4 MHz |
| Band stop | 2,479.4 MHz |
| Channel count | 80 |
| Channel spacing | ~988 kHz (79 MHz / 79 intervals) |
| Sequence length | 240 entries (FHSS_SEQUENCE_LEN=256, trimmed to 240 = 3×80) |
| Sync channel | Channel 41 (v3, live 4.4.2 target) / channel 40 (v4) — see §3.5 |
| Hopping pattern | Pseudo-random, seeded by UID |

### 3.2 Modulation Bandwidth

| Mode | Occupied Bandwidth | Notes |
|---|---|---|
| FLRC (1000/500 Hz) | ~600 kHz | BR 650 kbps, BW 0.6 MHz, CR 1/2 — **not used by Betaflight 4.4.2 target (LoRa only)** |
| LoRa SF5/BW800 | ~812.5 kHz | 500/333 Hz rates |
| LoRa SF6/BW800 | ~812.5 kHz | 250 Hz rate |
| LoRa SF7/BW800 | ~812.5 kHz | 150/100 Hz rates |
| LoRa SF8/BW800 | ~812.5 kHz | 50 Hz rate |

Each individual channel occupies <1 MHz, but the FHSS pattern covers the full
79 MHz span of the 2.4 GHz ISM band.

### 3.3 Time on Air per Packet

| Rate | Time on Air (µs) | Duty Cycle |
|---|---|---|
| FLRC 1000 Hz | 389 | 38.9% |
| FLRC 500 Hz | 389 | 19.5% |
| LoRa 500 Hz | 1,507 | 75.4% |
| LoRa 250 Hz | 3,300 | 82.5% |
| LoRa 150 Hz | 5,871 | 88.1% |
| LoRa 50 Hz | 10,798 | 54.0% |

---

## 4. RF Disruption Methods Analysis

### 4.1 Method A: Wideband Noise Jamming (2.4 GHz Full Band)

**Concept:** Transmit broadband noise across the entire 2400–2480 MHz band,
raising the noise floor above the ELRS signal level on every channel simultaneously.

**Equipment:** Ettus B205mini (56 MHz instantaneous BW) or B210 (56 MHz per channel,
2 channels).

**Power Budget:**

| Parameter | Value | Source |
|---|---|---|
| ELRS TX power (typical) | 100 mW (20 dBm) | Common ELRS setting |
| ELRS TX power (max SX1280) | ~20 mW (13 dBm) chip output | SX1280 register max |
| ELRS TX power (with PA) | Up to 1 W (30 dBm) | External PA modules |
| RX sensitivity (LoRa SF7) | -112 dBm | Source-verified |
| RX sensitivity (FLRC) | -104 dBm | Source-verified |
| Link margin at 10 m (20 dBm TX, -112 RX) | ~80 dB | Free-space path loss at 2.44 GHz, 10 m ≈ 52 dB |
| B205mini max TX power | ~10 dBm (~10 mW) | Ettus spec |
| B210 max TX power | ~10 dBm per channel | Ettus spec |

**Analysis:**

The fundamental problem is power budget. ELRS operates with enormous link margin:

- Free-space path loss at 2.44 GHz, 10 m: ~52 dB
- TX at 20 dBm, RX at -112 dBm → link budget of 132 dB
- At 10 m range, received signal is approximately 20 - 52 = **-32 dBm**
- The jammer must raise the noise floor above -32 dBm at the RX

To jam across the full 79 MHz band with the B205mini:
- B205mini output: ~10 dBm total across 56 MHz instantaneous bandwidth
- Power spectral density: 10 dBm - 10×log10(56e6) ≈ 10 - 77.5 = **-67.5 dBm/Hz**
- ELRS LoRa BW 800 kHz processing gain: 10×log10(800e3/data_rate) varies by SF
- LoRa SF7 at BW800 has ~16 dB processing gain
- Effective jam power in ELRS BW at 10 m: ~-67.5 + 59 - 52 = **-60.5 dBm** (in 800 kHz BW)
- ELRS received signal at 10 m: **-32 dBm**
- **Signal-to-jam ratio: ~28.5 dB** — jamming fails completely

Even at 1 m range (32 dB path loss), the jam density is still well below the ELRS
signal. The B205mini/B210 simply cannot produce enough power to overcome ELRS's
link margin at any practical CTF distance.

**Required jammer power for 10 m range:**
- Need jam PSD to produce -32 dBm in 800 kHz BW at 10 m
- -32 dBm in 800 kHz = -32 + 52 (path loss) = 20 dBm in 800 kHz at jammer
- Spread across 79 MHz: 20 + 10×log10(79e6/800e3) = 20 + 20 = **40 dBm (10 W)**
- With ELRS TX at 100 mW and PA: need **~50 dBm (100 W)** of wideband power

**Verdict: NOT FEASIBLE** with lab SDR equipment. Would require a dedicated
high-power 2.4 GHz jammer (military/law-enforcement grade).

### 4.2 Method B: Targeted FHSS Channel Jamming

**Concept:** If the FHSS hopping sequence is known (derived from the UID), jam only
the specific channel the link is currently using, concentrating all power into
<1 MHz bandwidth.

**Narrowband power advantage:**
- B205mini at 10 dBm into 1 MHz (vs 79 MHz wideband): gain of 10×log10(79) ≈ 19 dB
- Effective jam power at 10 m in 800 kHz: -60.5 + 19 = **-41.5 dBm**
- Still **9.5 dB below** the ELRS signal at 10 m

**Additional challenges:**
1. **UID required first.** The FHSS sequence is seeded by the UID, which is what we're
   trying to extract. This is a chicken-and-egg problem — we need to jam to get the
   UID, but need the UID to jam effectively.
2. **Timing.** FHSS hops happen every few packets (FHSShopInterval = 2-4 packets).
   At 500 Hz, that's a hop every 4-8 ms. The jammer must retune within <1 ms.
3. **Frequency tracking.** Without knowing the sequence, the jammer would need to
   detect which channel is active and follow it — the SX1280 hops faster than most
   SDRs can retune.

**Partial mitigation — sync channel jamming:**
The sync channel is version-dependent: Betaflight 4.4.2 OTA v3 uses channel 41,
while OTA v4 uses channel 40. SYNC packets appear every
`freq_count` hops (every 80 packets). Jamming only the sync channel:
- Forces the RX to miss SYNC packets
- Does NOT cause immediate disconnection — the RX uses its own timing to predict hops
- Eventually degrades link quality (LQ) as the phase-lock drifts

But this still requires overcoming the power budget deficit at the sync frequency.

**Verdict: NOT FEASIBLE** with lab equipment due to power budget. Even with known
FHSS sequence, the SDR lacks the TX power to overcome ELRS link margin at CTF
distances.

### 4.3 Method C: LoRa-Specific Disruption (Chirp Collision)

**Concept:** LoRa's chirp spread spectrum can be disrupted by transmitting
counter-chirps (inverted chirps) that destructively interfere with the signal.

**Theory:**
- LoRa upchirps and downchirps are orthogonal
- ELRS uses standard upchirps for data, inverted IQ for binding
- A well-timed counter-chirp at the correct frequency offset could corrupt
  individual symbols

**Challenges:**
1. **Same power budget problem.** Counter-chirps must arrive at similar power to
   the wanted signal.
2. **SX1280 specifics.** The SX1280 uses a proprietary LoRa implementation. The
   exact modulation details are not fully documented by Semtech for 2.4 GHz.
3. **Timing precision.** Chirps must align within a fraction of a symbol period.
   At SF7/BW800, symbol time is 2^7/800e3 = 160 µs. Timing alignment to <10 µs
   is needed.
4. **No public tooling.** No known open-source LoRa jammer exists for 2.4 GHz SX1280
   modulation. The `gr-lora` GNU Radio blocks target SX127x (sub-GHz LoRa) and
   would need adaptation.

**Verdict: NOT FEASIBLE** — same power budget limitation, plus significant
implementation complexity with no existing tooling.

### 4.4 Method D: WiFi Deauthentication (If WiFi Already Active)

**Concept:** If the E8 is already in WiFi mode and connected to a home network
(STA mode), send WiFi deauth frames to knock it offline, then present a rogue AP.

**This only applies when WiFi mode is already active** — it does not help trigger
WiFi mode entry.

If WiFi mode is active:
- Standard deauth attack (aireplay-ng) against the ELRS AP
- The ELRS device falls back from STA → AP mode after 30 seconds (`devWIFI.cpp:1283`)
- Connect to the AP with password `expresslrs`

**Assessment:** This is a viable secondary technique **if** WiFi mode is already active
through other means. It does not solve the primary problem of triggering WiFi entry.

**Verdict: VIABLE but only as a follow-up** — does not trigger WiFi mode entry.

### 4.5 Method E: FLRC Mode Disruption

**Concept:** FLRC (Fast Long-Range Communication) is a non-LoRa SX1280 mode
used by ELRS at 1000 Hz and 500 Hz rates. It uses GFSK-like modulation at
650 kbps with 600 kHz bandwidth.

FLRC has **no spread-spectrum processing gain** — it's narrowband GFSK with
forward error correction. This makes it more susceptible to narrowband jamming
than LoRa modes.

**Power budget (FLRC at 10 m):**
- RX sensitivity: -104 dBm (8 dB worse than LoRa SF7)
- Received signal at 10 m (20 dBm TX): still -32 dBm
- Jam margin needed: -32 - (-104) = 72 dB above sensitivity
- B205mini at 10 dBm into 600 kHz at 10 m: 10 - 52 = **-42 dBm**
- **Signal-to-jam ratio: 10 dB** — closer but still not enough

At 3 m range (path loss ~42 dB):
- Received ELRS signal: 20 - 42 = -22 dBm
- Jam power at receiver: 10 - 42 = -32 dBm
- **S/J ratio: 10 dB** — ratio stays constant (both subject to same path loss)

The S/J ratio is range-independent when jammer and TX are equidistant from RX.
Only if the jammer is closer to the RX than the TX does the ratio improve.

**Verdict: NOT FEASIBLE** at equal distances. Requires the jammer to be
significantly closer to the RX than the TX is.

---

## 5. Power Budget Summary

The core problem across all methods: ELRS has massive link margin, and lab SDR
equipment has very low TX power.

| Scenario | TX Power | Jam Power | Distance | S/J at RX | Result |
|---|---|---|---|---|---|
| Wideband, B205mini, 10 m | 20 dBm | 10 dBm (79 MHz) | Equal | +28 dB | Fails |
| Narrowband, B205mini, 10 m | 20 dBm | 10 dBm (1 MHz) | Equal | +10 dB | Fails |
| Narrowband, B210 (2ch), 10 m | 20 dBm | 13 dBm (1 MHz) | Equal | +7 dB | Borderline fail |
| Narrowband + PA, 10 m | 20 dBm | 30 dBm (1 MHz) | Equal | -10 dB | **Could work** |
| Narrowband, B205mini, jammer 1 m from RX, TX 10 m | 20 dBm | 10 dBm | Asym. | -10 dB | **Could work** |

**To jam ELRS reliably, you need either:**
1. A high-power amplifier (>1 W into a directional antenna)
2. Proximity advantage (jammer much closer to receiver than the TX is)

Neither is guaranteed at a CTF.

---

## 6. The Real Blocker: Integrated Controllers Don't Auto-WiFi from RF Loss

Even if the power budget were solved, **the E8 will not enter WiFi mode from RF
disruption alone**:

1. The E8 is an integrated controller — the ELRS TX module shares a PCB with the
   handset MCU
2. The handset sends CRSF data to the ELRS module over the internal serial bus
   immediately at boot
3. `webserverPreventAutoStart` is set `true` the moment CRSF is detected
4. RF disruption only affects the air link (TX module → drone RX), not the internal bus
5. The TX will keep transmitting to the void — it doesn't know or care that the RX
   isn't receiving

**The only ways to trigger WiFi mode on an integrated controller are:**
- Physical button press (if mapped to `ACTION_START_WIFI`)
- Lua script command from EdgeTX
- Three-power-cycle sequence (build-dependent, unconfirmed for E8)
- Physical access to the serial bus to suppress CRSF output

None of these are wireless attacks.

---

## 7. Alternative Approaches (Non-RF-Disruption)

Since RF disruption → WiFi mode is not viable for the E8, consider these alternatives
for the same end goal (extract the binding phrase):

### 7.1 Passive SYNC Packet Capture (Current Path — §20 in SESSION-NOTES)

- Capture ELRS packets passively with the SDR
- Betaflight 4.4.2 v3 SYNC carries UID3/UID4/UID5 fields. UID5's lower six
  transmitted bits may be model-match-masked; this model-ID-255 pair has zero XOR
- Cross-reference against the rainbow table (723 phrases)
- **Advantage:** Completely passive, no jamming needed
- **Limitation:** use only unmasked or CRC-resolved UID material for lookup;
  a raw generic UID5 field is not automatically the full UID byte

### 7.2 Side-Channel UID Recovery

- If the E8's internal serial bus can be sniffed (physical access), the full
  UID is transmitted in CRSF packets
- The E8's custom serial protocol response includes UID at bytes [5..10]
  (`e8-protocol-reconstruction.md`)
- **Requires physical access** — not a wireless attack

### 7.3 Brute-Force UID Derivation

- 6-byte UID = 48 bits, but seeded from MD5 of the binding phrase
- Common binding phrases are the realistic search space
- Rainbow table already covers 723 phrases
- On this model-ID-255 pair, recovered UID[3:5] can be compared directly with
  the phrase table. Generic links require model-match or CRC resolution first

### 7.4 ESP32 BLE Exploitation (If E8 Has Active BT)

- ESP32 BLE stack vulnerabilities (BrakTooth CVE-2021-28139, ESP-IDF provisioning
  CVE-2026-45542) could provide code execution on the E8's ESP32
- From there, read the binding phrase from flash/EEPROM
- **Status:** E8's BLE activity unconfirmed. Requires active wireless attack.

### 7.5 E8 WiFi Mode via Physical Button

- Determine the E8's button-to-WiFi-mode mapping
- In a CTF scenario, if brief physical access to the controller is possible,
  a button press could trigger WiFi mode
- **CTF-dependent:** Requires knowing the rules about physical access

---

## 8. Simulation and Testing Without Transmitting

### 8.1 Connection State Machine Simulation

The ELRS connection state machine can be exercised without RF by manipulating
the source code in a test harness:

1. **Extract the state machine** from `rx_main.cpp` into a standalone C++ program
2. **Simulate packet loss patterns:** Feed it a sequence of "received" and "missed"
   packets and observe state transitions
3. **Measure timing:** How quickly does it transition through
   `connected → disconnected → rate cycling → WiFi check`?
4. **Test the WiFi gate:** Confirm that `webserverPreventAutoStart` blocks WiFi
   entry regardless of RF state

### 8.2 FHSS Sequence Generation Verification

The FHSS sequence is deterministic given the UID seed. We can:

1. **Generate the sequence offline** using the `FHSSrandomiseFHSSsequenceBuild()`
   function from `FHSS.cpp`
2. **Verify against captures:** If we have a passive capture, check that the
   observed frequency hopping matches the predicted sequence for a candidate UID
3. **No RF needed:** This is purely computational

### 8.3 Link Budget Calculator

Build a Python tool that takes:
- TX power (dBm)
- Distance (m)
- Frequency (MHz)
- Jammer power (dBm)
- Jammer distance (m)
- Modulation (LoRa SF/BW or FLRC)
- And outputs: received signal, jam power at RX, S/J ratio, predicted disruption

This can model different CTF venue geometries to determine if any placement
scenario makes jamming viable.

---

## 9. Conclusions and Recommendations

### 9.1 RF Disruption → WiFi Mode: Not Viable for E8

The attack chain "jam ELRS → TX enters WiFi → extract binding phrase" fails for
two independent reasons:

1. **Power budget:** Lab SDR equipment (B205mini, B210) cannot produce enough
   power to overcome ELRS link margin at practical distances
2. **Architecture:** Integrated controllers (E8) gate WiFi mode on CRSF serial
   detection, not RF link status. RF jamming cannot suppress the internal bus.

### 9.2 Recommended Path Forward

1. **Continue with passive SYNC capture** (§20 in SESSION-NOTES). This is the
   proven path that needs no jamming and no WiFi mode entry.
2. **Expand the rainbow table** beyond 723 phrases to increase the probability
   of a SYNC-based match.
3. **Investigate E8 button mapping** — if a button triggers WiFi mode, that's
   the simplest path for CTF scenarios allowing brief physical access.
4. **Defer RF disruption research** until: (a) CTF rules on jamming are known
   (Q4), (b) a legitimate need exists that passive capture can't satisfy, and
   (c) appropriate high-power RF equipment is available.

### 9.3 What Would Change This Assessment

| Factor | Impact |
|---|---|
| High-power 2.4 GHz amplifier (>1 W) | Makes targeted jamming viable against LoRa modes |
| CTF uses standalone TX modules (not integrated) | Auto-WiFi via RF disruption becomes viable |
| Proximity advantage (jammer near RX) | Improves S/J ratio for narrowband jamming |
| ELRS firmware with very low TX power | Shrinks link margin, making jamming easier |
| CTF rules explicitly allow jamming | Removes legal constraint (but not power budget) |

---

## 10. References

- ExpressLRS 4.1.0 source: `firmware/src/expresslrs-4.1.0.tar.gz`
  - `src/lib/WIFI/devWIFI.cpp` — WiFi mode entry logic
  - `src/src/rx_main.cpp` — RX connection state machine
  - `src/src/tx_main.cpp` — TX CRSF detection and `webserverPreventAutoStart`
  - `src/lib/FHSS/FHSS.cpp` — FHSS sequence generation
  - `src/src/common.cpp` — Air rate configs, sensitivities, timeouts
  - `src/lib/SX1280Driver/SX1280_Regs.h` — SX1280 power limits (-18 to +13 dBm)
- Semtech SX1280 datasheet (DS_SX1280-1_V3.2): LoRa/FLRC modulation parameters
- Ettus B205mini specs: 56 MHz IBW, ~10 dBm max TX
- Ettus B210 specs: 56 MHz IBW per channel, ~10 dBm max TX per channel
- `elrs-wifi-mode-entry-research.md` (project file) — WiFi mode entry analysis
- `layer2-wireless-attack-research.md` — attack surface analysis
- `SESSION-NOTES.md` §20 — current E8–TinyHawk pair benchmark
