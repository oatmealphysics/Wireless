# Tier 1.1: Spektrum DSM2/DSMX — Attack Surface Analysis

**Date:** 2026-10-31
**Status:** Research complete — PoC requires SDR hardware (HackRF/USRP) + Spektrum TX/RX pair
**Context:** Authorized security research

---

## Executive Summary

Spektrum's DSM2 and DSMX protocols are **fundamentally broken at the protocol level**. The only barrier to mid-flight drone hijack is possessing an SDR and knowing the (fully documented) protocol internals. There is no encryption, no authentication beyond a 4-byte manufacturer ID that is broadcast in cleartext during binding and partially in every data frame, and the frequency-hopping sequence is entirely deterministic from that ID. The "Icarus" attack (PacSec 2016) demonstrated complete takeover; open-source decoders and protocol documentation make reproduction feasible with commodity hardware.

---

## 1. Protocol Architecture

### 1.1 Radio Layer
- **Transceiver:** Cypress CYRF6936 WirelessUSB LP 2.4 GHz Radio SoC
- **Modulation:** GFSK at 1 Mbit/s
- **Spread spectrum:** FHSS (Frequency Hopping Spread Spectrum) + DSSS (Direct Sequence Spread Spectrum)
- **Band:** 2.4 GHz ISM (channels mapped to specific frequencies within 2.404–2.476 GHz range)
- **Available channels:** 74 RF channels (indices 3–76)

### 1.2 Protocol Variants
| Variant | Resolution | Channels | Hop Channels | Year |
|---------|-----------|----------|-------------|------|
| DSM | 10-bit | 6 | Fixed pattern | ~2005 |
| DSM2 | 10-bit (6ch) / 11-bit (8ch) | 6–8 | 2 alternating | ~2006 |
| DSMX | 11-bit | up to 12 (X-Plus: 20) | 23-channel FHSS | ~2011 |

### 1.3 Packet Structure
```
[Header: 2 bytes from MFG ID] [Channel data: 7 × 16-bit words]
```
- **Header bytes:** Derived from the 4-byte CYRF6936 manufacturer ID (MFG ID)
  - DSM2: `0xFF - mfgid[n]` (inverted)
  - DSMX: `mfgid[n]` (direct — ID bytes broadcast in cleartext!)
- **Channel word:** `[1-bit flag][4-bit channel number][11-bit value]`
- **Timing:** Each packet sent twice — on channel 'a', then channel 'b' 4ms later. New data every 22ms.

### 1.4 Spread Spectrum Codes (SOP/DATA)
The CYRF6936 uses Start-of-Packet (SOP, 8 bytes) and DATA (16 bytes) codes for DSSS:
- Selected from a 5×9 lookup table of PN (pseudo-noise) codes
- **Row:** `channel_index mod 5`
- **SOP column:** `(mfgid[0] + mfgid[1] + mfgid[2] + 2) & 0x07`
- **DATA columns:** `7 - sop_col` and `7 - sop_col + 1`
- **CRC seed:** Channel 'a' = `NOT(mfgid_value)`, Channel 'b' = `mfgid_value`

**Critical observation:** ALL spread-spectrum parameters are deterministic functions of the MFG ID. Know the ID → know every SOP code, DATA code, and CRC seed on every channel.

---

## 2. DSMX Frequency Hopping Algorithm (Fully Reversed)

The 23-channel hopping sequence is **entirely deterministic** from the CYRF6936 MFG ID + model number:

```python
def generate_dsmx_channels(mfg_id_bytes, model_num):
    """
    Generate the 23-channel DSMX frequency hopping sequence.
    Algorithm from DeviationTX reverse engineering (credit: Alexandr Alexandrov, Sergey Gimaev).
    
    mfg_id_bytes: 4-byte CYRF6936 manufacturer ID
    model_num: model/fixed ID number
    """
    # Seed derivation
    id_value = int.from_bytes(mfg_id_bytes, 'little') + model_num
    id_tmp = ~id_value & 0xFFFFFFFF  # Negate (two's complement)
    
    channels = []
    count_3_27 = 0   # Band 1: channels 3-27 (max 8)
    count_28_51 = 0  # Band 2: channels 28-51 (max 7)
    count_52_76 = 0  # Band 3: channels 52-76 (max 8)
    
    id_parity = id_tmp & 0x01  # Target parity for all channels
    
    while len(channels) < 23:
        # Linear congruential generator
        id_tmp = (id_tmp * 0x0019660D + 0x3C6EF35F) & 0xFFFFFFFF
        next_ch = ((id_tmp >> 8) % 0x49) + 3  # Range: 3-75
        
        # Parity constraint: channel must match ID parity
        if (next_ch & 0x01) != id_parity:
            continue
            
        # Uniqueness constraint
        if next_ch in channels:
            continue
            
        # Band balance constraints
        if 3 <= next_ch <= 27 and count_3_27 >= 8:
            continue
        if 28 <= next_ch <= 51 and count_28_51 >= 7:
            continue
        if next_ch >= 52 and count_52_76 >= 8:
            continue
            
        channels.append(next_ch)
        if 3 <= next_ch <= 27:
            count_3_27 += 1
        elif 28 <= next_ch <= 51:
            count_28_51 += 1
        else:
            count_52_76 += 1
    
    return channels
```

**This means:** Given the 4-byte MFG ID (which is broadcast in cleartext during binding and partially in every frame), an attacker can compute the exact hopping sequence and predict where the transmitter will be at any future time.

---

## 3. Binding Process — ID Extraction

### 3.1 Bind Packet Structure
```
Byte 0-3:  MFG ID (inverted for DSM2, direct for DSMX)
Byte 4-5:  Checksum (384 + sum of bytes 0-7)
Byte 6:    0x01 (fixed)
Byte 7:    Channel count
Byte 8:    TX capability code (e.g., 0xA2 = DX6i DSMX, 0xB2 = DX8 DSMX)
Byte 9:    Packet count (1 or 2)
Byte 10-11: Checksum (0xEEEE + sum of bytes 8-13)
```

**Binding occurs on any odd channel from 0x01 to 0x4F** with known SOP/DATA codes. Monitoring these channels during a bind event yields the full MFG ID.

### 3.2 In-Flight ID Leakage
Even without capturing a bind event:
- DSMX frames include MFG ID bytes directly in the 2-byte header
- Only 2 of 4 bytes are in each frame, but the other 2 can be derived:
  - The SOP/DATA code selection depends on all 4 bytes
  - The CRC seed depends on the full MFG ID value
  - The hopping sequence depends on the full MFG ID
- **Brute force of the remaining 2 bytes:** Only 65,536 possibilities — trivially searchable

### 3.3 Passive Extraction Timeline
1. **Intercept any DSMX frame** → get 2 of 4 MFG ID bytes + current channel index
2. **Observe hopping pattern** over a few seconds → constrains remaining 2 bytes
3. **Brute-force remaining bits** → 65,536 candidates, testable against observed hopping sequence
4. **Full MFG ID recovered** → compute all SOP/DATA/CRC codes + full 23-channel hop sequence
5. **Estimated time:** Seconds to low minutes with SDR

---

## 4. Attack Vectors

### 4.1 Attack 1: Full Mid-Flight Takeover (Icarus-Class)

**Prerequisite:** MFG ID (obtained via binding capture or passive extraction)

**Method:**
1. Compute the target's 23-channel hopping sequence from MFG ID
2. Compute SOP, DATA, and CRC codes for each channel
3. Synchronize to the target's hopping timing (observe a few hops)
4. Begin transmitting valid packets on the correct channel with correct codes, **slightly before** the legitimate transmitter's packets arrive
5. The receiver accepts the attacker's packets (first valid packet wins in the 11ms window)
6. The legitimate transmitter is effectively locked out — the receiver stays synchronized to the attacker

**Why it works:**
- No authentication: any packet with the correct MFG ID header, SOP code, DATA code, and CRC is accepted
- No sequence numbers or nonces: replay and injection are indistinguishable from legitimate traffic
- First-wins timing: the receiver locks onto whichever valid signal arrives first in each hop window
- 22ms frame rate with 4ms dual-channel redundancy provides predictable timing

**Result:** Complete control of all RC channels (throttle, aileron, elevator, rudder, aux). The legitimate pilot's inputs are ignored.

### 4.2 Attack 2: Selective Channel Override

Instead of full takeover, inject only specific channel values:
- Override throttle to force descent/crash
- Override AUX channels to disarm
- Override rudder for uncontrolled spin
- Leave other channels untouched (harder to detect)

### 4.3 Attack 3: Denial of Service (Simpler)

**No MFG ID needed:**
- Wideband jamming of 2.4 GHz band
- Targeted jamming of DSMX hopping channels (only 23 out of 74 — but without knowing which 23, must jam all 74)
- Force failsafe mode (typically motor cutoff or controlled descent)

### 4.4 Attack 4: Passive Surveillance

**Zero transmission required:**
- Decode all RC channel values in real-time
- Determine pilot intent (throttle position, control inputs)
- Track hopping pattern to fingerprint specific transmitters
- Enumerate all DSMX transmitters in an area

### 4.5 Attack 5: Binding Theft

**During a bind event:**
- Capture the full MFG ID and capability codes
- Bind a malicious receiver to the victim's transmitter
- Or bind a malicious transmitter to the victim's receiver (if you can force a re-bind)

---

## 5. Existing Open-Source Tooling

| Tool | Purpose | Status |
|------|---------|--------|
| **DeviationTX** | Full DSM2/DSMX TX implementation for Walkera Devo radios | Mature, well-documented |
| **DIY-Multiprotocol-TX-Module** | Multi-protocol TX module including DSM2/DSMX | Mature, widely deployed |
| **gr-dsmx-rc** (Morin/Cardoso, GNURadio) | SDR-based DSMX decoder/receiver | Research-grade, GNU Radio |
| **cyrf6936_sigrokdecode** | CYRF6936 SPI protocol decoder for sigrok/PulseView | Logic analyzer tool |
| **SpektrumRC/SRXL2** | Official Spektrum SRXL2 serial protocol library | Official reference |
| **mbcapece3/BindDSMX** | Python binding utility for DSM satellite receivers | Working, ESP8266/Pico |

### Key source code references:
- **DeviationTX `doc/DSM.txt`**: Complete protocol specification including hopping algorithm
- **DeviationTX `src/protocol/dsm2_cyrf6936.c`**: Full C implementation of DSM2/DSMX TX
- **Multiprotocol `Multiprotocol/DSM_cyrf6936.ino`**: Arduino implementation

---

## 6. Hardware Requirements for PoC

### Minimum Viable Attack Setup
| Component | Purpose | Approx. Cost |
|-----------|---------|-------------|
| HackRF One or USRP B200 | SDR transceiver for RX + TX | $300–$500 |
| Spektrum-compatible TX (or Multiprotocol module) | Reference transmitter for validation | $50–$150 |
| Spektrum satellite receiver (e.g., SPM9645) | Test target | $20–$40 |
| Computer + GNU Radio | Signal processing | Existing |

### What's Buildable Without Hardware (Offline)
1. **DSMX hopping sequence generator** — pure computation from MFG ID, fully implementable in Python
2. **SOP/DATA/CRC code calculator** — lookup tables from DeviationTX source
3. **Packet encoder/decoder** — frame construction and parsing
4. **MFG ID brute-forcer** — given 2 known bytes + observed hopping, search remaining 2 bytes
5. **Attack simulation** — model the timing race between legitimate TX and attacker TX
6. **Bind packet generator** — construct valid bind frames for any target MFG ID

### What Requires Hardware
- Actual RF transmission/reception (obviously)
- Timing validation of the 11ms hop window race condition
- Real-world range and reliability testing
- Integration testing with actual Spektrum receivers

---

## 7. Comparison to Current ELRS Target

| Dimension | ELRS/SX1280 | Spektrum DSMX |
|-----------|-------------|---------------|
| **Encryption** | None (CRC only) | None (CRC only) |
| **Authentication** | Binding phrase → UID (MD5) | MFG ID (4 bytes, cleartext) |
| **FHSS** | 240 hops, seed from UID | 23 hops, seed from MFG ID |
| **ID extraction** | UID[3:5] in sync packets | 2/4 bytes in every frame |
| **Brute force** | UID[0:2] = 16M candidates | MFG ID[0:1] = 65K candidates |
| **Published attacks** | NCC Group (unmitigated) | Icarus (full takeover, 2016) |
| **Open-source TX** | ExpressLRS (full source) | DeviationTX, Multiprotocol |
| **Transceiver chip** | Semtech SX1280 | Cypress CYRF6936 |
| **Install base** | Growing (FPV racing) | Large (BNF/RTF, North America) |
| **Prior art depth** | Deep (NCC, academic) | Deep (Icarus, GNURadio, academic) |

**Key advantage of DSMX target:** The brute force space (65K) is ~250x smaller than ELRS (16M), and the hopping sequence (23 channels) is simpler than ELRS (240 hops). DSMX is the easier protocol to fully compromise.

---

## 8. Risk Assessment

| Factor | Rating | Notes |
|--------|--------|-------|
| **Technical feasibility** | **HIGH** | Protocol fully reversed, tooling exists |
| **Hardware accessibility** | **MEDIUM** | SDR ($300+) + target RX needed |
| **Novelty** | **LOW** | Icarus demonstrated in 2016, well-known |
| **Impact** | **HIGH** | Full drone takeover, millions of devices |
| **Remediation** | **NONE** | Spektrum cannot patch deployed hardware |
| **Transferability** | **HIGH** | Same attack class applies to other CYRF6936-based protocols |

---

## 9. Recommended Next Steps

### Offline (no hardware needed):
1. ✅ Implement DSMX hopping sequence generator (Python) — **can do now**
2. ✅ Implement SOP/DATA/CRC code calculator — **can do now**
3. ✅ Implement packet encoder/decoder — **can do now**
4. ✅ Implement MFG ID brute-forcer — **can do now**
5. ✅ Build bind packet generator — **can do now**

### Requires hardware:
6. ⬜ SDR-based DSMX receiver (extend gr-dsmx-rc)
7. ⬜ SDR-based DSMX transmitter/injector
8. ⬜ Timing race validation against real Spektrum RX
9. ⬜ Full Icarus-class takeover reproduction

### Hardware gap:
**To validate any of steps 6-9, we need: HackRF One (or USRP) + a Spektrum DSMX transmitter + a Spektrum satellite receiver.** D does not currently have this hardware connected (see hardware-setup-travel memory). Offline tooling (steps 1-5) is fully buildable now.

---

## Sources

- DeviationTX `doc/DSM.txt` — Complete DSM2/DSMX protocol specification
- DeviationTX source: `src/protocol/dsm2_cyrf6936.c`
- Morin & Cardoso, "Hacking the DSMx Drone RC Protocol" (GNURadio Conference 2021)
- Andersson, "Icarus" (PacSec 2016, Trend Micro DVLabs)
- DSIAC, "Icarus SDR Hijacks Wide-Band Frequency-Agile Drone Control Systems"
- KAIST, "Security Analysis of FHSS-type Drone" (WISA 2015)
- Cypress CYRF6936 Datasheet (Mouser)
- Spektrum SRXL2 Protocol Library (GitHub SpektrumRC/SRXL2)
- DIY-Multiprotocol-TX-Module documentation
