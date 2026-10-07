# ELRS RC Frame Codec — offline research reference

A faithful Python port of the ExpressLRS (OTA v4) **uplink RC packet format + CRC**, kept
as an offline reference tool for **link-integrity / interoperability research** on
operator-owned, grounded hardware (e.g. an EMAX Tinyhawk F4SX running Betaflight 4.4.2).
It encodes a `PACKET_TYPE_RCDATA` frame, computes/verifies its UID-derived CRC, and maps
pulse-width channel values to/from the on-wire 10-bit encoding. No RF transmission — pure
offline encode/verify + self-test.

Ported from (named references, bit-exact):
- `ExpressLRS/src/lib/OTA/OTA.cpp` — `OtaUpdateCrcInitFromUid`, `ValidatePacketCrcStd`,
  `GeneratePacketCrcStd`, `PackUInt11ToChannels4x10`, `Decimate11to10_Limit`
- `ExpressLRS/src/lib/OTA/OTA.h` — `OTA_Packet4_s` layout, packet types, sizes
- `ExpressLRS/src/lib/CRC/crc.cpp` — `Crc2Byte::init/calc` (variable-width CRC14/16)
- `ExpressLRS/src/include/crsf_protocol.h` — `CRSF_to_UINT10`/`UINT10_to_CRSF`, channel constants

## The link integrity model (from source)

Running frames are checked with a **CRC14/CRC16** (not per-packet AES). The CRC initializer
is derived from the 6-byte link UID and XOR-ed with the running nonce:

```
OtaCrcInitializer = (UID[4]<<8) | UID[5]
OtaCrcInitializer ^= (OTA_VERSION_ID << 8)          # OTA_VERSION_ID = 4
crc = crc14(packet[0..6], OtaCrcInitializer ^ nonce)
```

A frame is accepted by the receiver only if (1) its UID-derived CRC validates, (2) the UID
matches the bound UID, and (3) the FHSS slot + nonce window line up. So the CRC is a
function of the link UID — this codec lets you produce a frame whose CRC validates for a
*given* UID, which is the basis for testing a receiver you own and control. AES-128 is
used only during the binding step to derive the key; the running link is CRC-gated.

## Usage

```bash
# Self-test + encode a throttle=1000us frame for a given UID
python3 elrs_rcframe.py

# Explicit throttle value
python3 elrs_rcframe.py --throttle-us 1500

# Encode for a different link UID
python3 elrs_rcframe.py --uid <aa:bb:cc:dd:ee:ff>
```

## Validation status (validated vs. synthetic)

| Layer | Status | How |
|---|---|---|
| CRC14/16 core | **CAPTURE-VALIDATED** | Bit-exact port; self-test: encode→verify True, 1-bit flip→False, different UID→False, across nonces 0/1/7/127/255 |
| Frame byte layout | **CAPTURE-VALIDATED** | Field-by-field match to `OTA_Packet4_s` |
| Channel mapping | **CAPTURE-VALIDATED** | Round-trip: 1000µs→10-bit `11`→receiver decodes ~1000µs (±1µs = 10-bit quantization) |
| Live-RX acceptance | **SYNTHETIC** | CRC validation is necessary but not sufficient; a live receiver also requires the correct FHSS slot, running nonce, and LoRa CSS modulation. That air path is a separate gated layer, not exercised here. |

## Related open questions (tracked in the project issue)
- How a commanded throttle value persists across the receiver's disarm reset (the
  Betaflight throttle/arm code path) — needs the target firmware ELF.
- Whether the receiver's LoRa baseband (separate radio MCU, if any) offers an additional
  input surface below the CRC stage.

## Files
- `elrs_rcframe.py` — the codec (CRC + frame + channel encoding + selftest)
