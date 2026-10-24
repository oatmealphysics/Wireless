# ELRS v3 vs v4: Security-Relevant Changes

**Source comparison:** ExpressLRS 3.5.3 vs 4.1.0
**Date:** 2026-10-14
**Purpose:** CTF attack surface analysis — what changed between OTA protocol versions

---

## Executive Summary

ELRS v4 (OTA_VERSION_ID=4) introduces **no encryption** and **no authentication**. The protocol remains fundamentally open: binding phrase → UID → FHSS sequence → CRC seed, all deterministic, no shared secret beyond the binding phrase. However, v4 makes several structural changes to the OTA packet format, CRC validation, SYNC packet, and nonce handling that **break compatibility with v3 tooling** and require updated attack scripts.

Key changes for attackers:
1. **Nonce mixed into CRC** — v4 XORs `OtaNonce` into the CRC initializer for every non-SYNC packet, making per-packet CRC vary with nonce position
2. **UID3 removed from SYNC** — one fewer plaintext UID byte in the air, replaced by `rfRateEnum`
3. **OTA_VERSION_ID shifted in CRC seed** — version now occupies the high byte of CrcInitializer, not XORed into the low bits
4. **FHSS sync channel offset changed** — `freq_count / 2` instead of `freq_count / 2 + 1`
5. **Packet type renaming** — `PACKET_TYPE_MSPDATA` and `PACKET_TYPE_TLM` merged into `PACKET_TYPE_DATA`

None of these changes add real cryptographic protection. The attack chain (know binding phrase → compute UID → derive FHSS sequence → forge packets) remains fully valid, but every step needs v4-specific constants and CRC computation.

---

## 1. OTA Version ID

| | v3 (3.5.3) | v4 (4.1.0) |
|---|---|---|
| **Define** | `OTA_VERSION_ID 3` | `OTA_VERSION_ID 4` |
| **Location** | `src/include/common.h:20` | `src/include/targets.h:8` |

The OTA_VERSION_ID is used in three places: CRC initializer seeding, FHSS/MAC seed computation, and CRSF version reporting. Changing it from 3→4 breaks wire compatibility between versions entirely, which is by design.

---

## 2. CRC Initializer and Nonce Mixing (Critical for Packet Forgery)

### v3: Static CRC initializer
```cpp
// OTA.cpp (v3)
void OtaUpdateCrcInitFromUid()
{
    OtaCrcInitializer = (UID[4] << 8) | UID[5];
    OtaCrcInitializer ^= OTA_VERSION_ID;  // XOR version into low bits
}
```

CRC is computed with a **static** initializer for all packets:
```cpp
// v3 ValidatePacketCrcFull
uint16_t const calculatedCRC =
    ota_crc.calc((uint8_t*)otaPktPtr, OTA8_CRC_CALC_LEN, OtaCrcInitializer);
```

### v4: Nonce mixed into CRC per-packet
```cpp
// OTA.cpp (v4)
void OtaUpdateCrcInitFromUid()
{
    OtaCrcInitializer = (UID[4] << 8) | UID[5];
    // shift OTA_VERSION_ID to the high byte to leave room for
    // xor-ing in the nonce in the GenerateCRC and ValidateCRC function
    OtaCrcInitializer ^= (uint16_t)OTA_VERSION_ID << 8;  // version in HIGH byte now
}
```

CRC now varies per packet based on nonce, **except for SYNC packets**:
```cpp
// v4 ValidatePacketCrcFull
uint16_t nonceValidator = (otaPktPtr->std.type == PACKET_TYPE_SYNC) ? 0 : OtaNonce;
uint16_t const calculatedCRC =
    ota_crc.calc((uint8_t*)otaPktPtr, OTA8_CRC_CALC_LEN, OtaCrcInitializer ^ nonceValidator);
```

### Attack impact

| Aspect | v3 | v4 |
|---|---|---|
| CRC initializer | `(UID[4]<<8 \| UID[5]) ^ 3` | `(UID[4]<<8 \| UID[5]) ^ (4<<8)` |
| Per-packet CRC | Static — same init for all packets | `init ^ OtaNonce` for data packets, `init ^ 0` for SYNC |
| SYNC packet CRC | Same as data | Uses `nonceValidator = 0`, effectively static |
| Forgery complexity | Know UID → forge any packet anytime | Know UID + track nonce → forge any packet |

**Nonce tracking is trivial.** The nonce is an 8-bit counter that increments each packet interval and is sent **in plaintext** in every SYNC packet (`syncPtr->nonce = OtaNonce`). An attacker who can receive SYNC packets (sent on the sync channel at known intervals) can track the nonce perfectly. This adds no real security — it's an anti-collision measure, not a defense.

**SYNC packets remain the easiest forgery target** because their CRC uses `nonceValidator = 0`, making the CRC initializer static and predictable from the UID alone.

---

## 3. SYNC Packet Structure Changes

### v3 OTA_Sync_s
```cpp
typedef struct {
    uint8_t fhssIndex;
    uint8_t nonce;
    uint8_t switchEncMode:1,
            newTlmRatio:3,
            rateIndex:4;       // raw rate table index
    uint8_t UID3;              // UID[3] sent in plaintext
    uint8_t UID4;              // UID[4] sent in plaintext
    uint8_t UID5;              // UID[5], lower 6 bits XORed when ModelMatch is on
} PACKED OTA_Sync_s;
```

### v4 OTA_Sync_s
```cpp
typedef struct {
    uint8_t fhssIndex;
    uint8_t nonce;
    uint8_t rfRateEnum;        // NEW: rate as enum, not table index
    uint8_t switchEncMode:1,
            newTlmRatio:3,
            geminiMode:1,      // NEW: dual-antenna mode flag
            otaProtocol:2,     // NEW: CRSF vs MAVLink
            free:1;
    uint8_t UID4;              // UID[4] still in plaintext
    uint8_t UID5;              // UID[5], lower 6 bits XORed when ModelMatch is on
} PACKED OTA_Sync_s;
```

### Key differences

| Field | v3 | v4 | Security impact |
|---|---|---|---|
| `UID3` | Present (plaintext) | **Removed** | One fewer byte leaked OTA. UID[3] must now be brute-forced (256 values) or inferred from FHSS analysis |
| `rateIndex` | 4-bit raw table index | Replaced by `rfRateEnum` (full byte) | Rate matching uses enum now, not position in rate table |
| `geminiMode` | N/A | 1 bit | New field, not security-relevant |
| `otaProtocol` | N/A | 2 bits | Signals CRSF vs MAVLink mode |
| `UID4`, `UID5` | Present | Present | `UID4` and the upper two bits of `UID5` are direct; the lower six `UID5` bits may carry the model-match XOR |

### UID leakage comparison

| | v3 | v4 |
|---|---|---|
| UID bytes in SYNC | UID[3], UID[4], UID[5] | UID[4], UID[5] |
| Direct UID material | UID[3], UID[4], upper 2 bits of UID[5] | UID[4], upper 2 bits of UID[5] |
| Model-match-dependent field | lower 6 bits of transmitted `UID5` | lower 6 bits of transmitted `UID5` |
| Unknown UID bytes | UID[0:3] (3 bytes) | UID[0:4] (4 bytes) |

Do not equate a transmitted `UID5` field with the full UID byte without knowing
the model-match state. With model match disabled (`modelId == 255`), the XOR is
zero and v3 exposes UID bytes 3--5 directly. This is the measured configuration
of the owned E8/TinyHawk pair. For that v3 case, only UID[2] remains unknown in
the four-byte FHSS seed, giving 256 source-derived candidates; UID[0] and
UID[1] do not participate in this air-link seed. OTA v4 removes `UID3`, adding
another unknown FHSS-seed byte when no other evidence supplies it. The CRC
initializer independently depends on the full UID[4:5] suffix and can constrain
the masked field once packet bytes have been decoded.

In practice, **this doesn't matter much for CTF**: the binding phrase → MD5 → UID pipeline gives you all 6 bytes if you know the phrase, and rainbow table attacks remain fully effective.

---

## 4. FHSS Seed Derivation

### v3: `uidMacSeedGet()` in common.cpp
```cpp
uint32_t uidMacSeedGet()
{
    const uint32_t macSeed = ((uint32_t)UID[2] << 24) + ((uint32_t)UID[3] << 16) +
                             ((uint32_t)UID[4] << 8) + (UID[5]^OTA_VERSION_ID);
    return macSeed;
}
```

### v4: `OtaGetUidSeed()` in OTA.cpp
```cpp
uint32_t OtaGetUidSeed()
{
    return ((uint32_t)UID[2] << 24) + ((uint32_t)UID[3] << 16) +
           ((uint32_t)UID[4] << 8) + (UID[5]^OTA_VERSION_ID);
}
```

**The computation is identical** — only the function name and location changed. The seed is `UID[2:5]` with `UID[5] XOR OTA_VERSION_ID`. Since OTA_VERSION_ID changed from 3→4, the same binding phrase produces a **different FHSS sequence** in v4 vs v3 (the lowest byte of the seed changes).

### Sync channel offset change
```cpp
// v3: sync_channel = (FHSSconfig->freq_count / 2) + 1;
// v4: sync_channel = FHSSconfig->freq_count / 2;
```

For 2.4 GHz (80 channels): sync channel moves from **41** to **40**. This is a one-channel shift that affects where an attacker listens for SYNC packets.

---

## 5. Packet Type Restructuring

### v3 packet types
```cpp
#define PACKET_TYPE_RCDATA  0b00
#define PACKET_TYPE_MSPDATA 0b01
#define PACKET_TYPE_TLM     0b11
#define PACKET_TYPE_SYNC    0b10
```

### v4 packet types
```cpp
#define PACKET_TYPE_DATA        0b01  // replaces MSPDATA + TLM
#define PACKET_TYPE_RCDATA      0b00  // uplink only
#define PACKET_TYPE_SYNC        0b10  // unchanged
#define PACKET_TYPE_LINKSTATS   0b00  // downlink only (same value as RCDATA)
```

**MSP and telemetry merged into a single `PACKET_TYPE_DATA`** (0b01). Direction-specific types now share values: RCDATA (0b00) is uplink-only, LINKSTATS (0b00) is downlink-only. SYNC remains 0b10.

The `tlmFlag` bit in data packets is replaced by `stubbornAck`, which serves the same purpose (acknowledging reliable data delivery) but with a different name and slightly different semantics.

---

## 6. RX SYNC Validation (ProcessRfPacket_SYNC)

### v3 validation
```cpp
// Verify the first two of three bytes of the binding ID
if (otaSync->UID3 != UID[3] || otaSync->UID4 != UID[4])
    return false;
// The third byte XORed with ModelId
if ((otaSync->UID5 & ~MODELMATCH_MASK) != (UID[5] & ~MODELMATCH_MASK))
    return false;
// Rate switch
ExpressLRS_nextAirRateIndex = otaSync->rateIndex;
```

### v4 validation
```cpp
// Verify the first byte of the binding ID (UID3 no longer sent)
if (otaSync->UID4 != UID[4])
    return false;
// The second byte XORed with ModelId
if ((otaSync->UID5 & ~MODELMATCH_MASK) != (UID[5] & ~MODELMATCH_MASK))
    return false;
// Rate switch via enum with hardware validation
expresslrs_RFrates_e proposedRateEnum = (expresslrs_RFrates_e)otaSync->rfRateEnum;
if (!enumRatetoIndex(proposedRateEnum, proposedRateIdx)
    || !isSupportedRFRate(proposedRateIdx))
    return false;
```

**v4 compares only UID[4] and the upper 2 bits of UID[5]** in its SYNC identity
check, versus UID[3] + UID[4] + the upper 2 bits of UID[5] in v3. In both
versions a CRC check runs before `ProcessRfPacket_SYNC` and depends on the full
UID[4:5] suffix. CRC is an integrity/collision check, not cryptographic
authentication, so it should not be described as an independent identity
proof.

**v4 adds rate validation**: the RX now checks that the proposed rate is both a valid enum value AND supported by the hardware. This prevents a crafted SYNC from crashing the RX with an invalid rate index.

---

## 7. New v4 Features (Gemini Mode, OTA Protocol)

### Gemini mode
v4 adds a `geminiMode` bit in the SYNC packet, enabling True Diversity (dual-antenna simultaneous TX). This is operational, not security-relevant, but means the SYNC packet structure has an extra bit that must be correctly set in forged packets.

### OTA protocol field
v4 adds `otaProtocol` (2 bits) to signal CRSF vs MAVLink mode. The RX switches serial protocol based on this field. A forged SYNC could potentially switch the RX between CRSF and MAVLink modes, disrupting the FC communication.

### Bound UID check
v4 adds a helper:
```cpp
bool OtaUidIsBound(const uint8_t uid[UID_LEN])
{
    return (uid[2] != 0 || uid[3] != 0 || uid[4] != 0 || uid[5] != 0);
}
```
Replaces the v3 macro `UID_IS_BOUND`. Functionally identical.

---

## 8. CRC Polynomial (Unchanged)

```cpp
// Both v3 and v4:
#define ELRS_CRC14_POLY 0x2E57
#define ELRS_CRC16_POLY 0x3D65
```

The v3 `ELRS_CRC_POLY 0x07` (CRC8) is removed in v4 — the 8-bit CRC was apparently not used in the OTA path and was cleaned up. The 14-bit and 16-bit polynomials used for actual packet CRC are unchanged.

---

## 9. What Was NOT Added in v4

Despite the major version bump, v4 does **not** add:

- **No encryption** — all OTA data remains plaintext
- **No authentication** — no HMAC, no challenge-response, no session keys
- **No replay protection** — nonce mixing in CRC is sequential and predictable
- **No anti-jamming** — FHSS is the only mitigation and remains deterministic from UID
- **No binding phrase obfuscation** — still MD5 hash, first 6 bytes
- **No secure binding** — binding mode still uses all-zero UID with no encryption

---

## 10. Attack Script Update Checklist

For tools targeting OTA v4 ELRS (not the owned TinyHawk III, whose live
Betaflight 4.4.2 receiver is OTA v3), the following constants and computations
need updating from the v3 implementations:

| Component | v3 Value/Behavior | v4 Value/Behavior | Script Impact |
|---|---|---|---|
| OTA_VERSION_ID | 3 | 4 | FHSS seed, CRC init |
| CRC init formula | `(UID[4]<<8\|UID[5]) ^ 3` | `(UID[4]<<8\|UID[5]) ^ (4<<8)` | Packet CRC computation |
| Per-packet CRC | Static init | `init ^ nonce` (data), `init ^ 0` (SYNC) | Must track nonce for data packet CRC |
| SYNC channel (2.4G) | `freq_count/2 + 1` = 41 | `freq_count/2` = 40 | Listen/inject on channel 40 |
| FHSS seed | `UID[2:5]` with `UID[5]^3` | `UID[2:5]` with `UID[5]^4` | Hop sequence changes |
| SYNC struct | 6 payload bytes: fhss, nonce, mode\|rate, UID3, UID4, UID5 | 6 payload bytes: fhss, nonce, rfRateEnum, mode\|tlm\|gemini\|proto, UID4, UID5 | Forge/parse SYNC packets |
| SYNC UID fields | UID[3], UID[4], UID[5] | UID[4], UID[5] | UID3 no longer sniffable from SYNC |
| Rate field | `rateIndex` (4-bit table index) | `rfRateEnum` (8-bit enum) | Rate spoofing |
| Data packet type | `PACKET_TYPE_MSPDATA` (0b01) / `PACKET_TYPE_TLM` (0b11) | `PACKET_TYPE_DATA` (0b01) for both | Packet construction |
| Telemetry ack bit | `tlmFlag` | `stubbornAck` | Same position, different semantics |

---

## 11. Bottom Line for CTF

**The v3→v4 transition is a refactor, not a security upgrade.** The fundamental ELRS security model — binding phrase as sole shared secret, deterministic FHSS, no encryption — is unchanged. Every attack in our research plan (SYNC sniffing, hop sequence prediction, packet injection, TX override) remains viable against v4 with updated constants.

The nonce-mixed CRC is the only change that adds any defense-in-depth, and it's trivially defeated by tracking the plaintext nonce from SYNC packets. The removal of UID[3] from SYNC adds at most 8 bits of brute-force complexity for passive UID recovery — irrelevant when the binding phrase is known.

**Our rainbow table (`tools/elrs-uid-rainbow.py`) needs no changes** — it computes the full UID from the binding phrase, and that pipeline is identical in v3 and v4. The FHSS sequence derivation tool will need the updated seed formula (`UID[5]^4` instead of `UID[5]^3`) and sync channel offset. Any packet injection tools need the new CRC init formula and nonce-mixed CRC computation.
