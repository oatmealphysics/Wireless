# DISC-02: ExpressLRS Backpack — ESP-NOW MSPv2 Stack Buffer Overflow

**Advisory type:** Sanitized (no operational payload)
**Date:** 2026-10-26
**Status:** Draft — pending maintainer contact

## Summary

The ExpressLRS Backpack firmware contains a stack buffer overflow in its MSP
message parser (`lib/MSP/msp.cpp`). The MSPv2 protocol uses a 16-bit payload
length field, but the parser stores incoming bytes into a fixed 64-byte buffer
without checking whether the declared length exceeds the buffer capacity. When
the Backpack receives an MSPv2 frame with a payload length greater than 64
bytes, the parser writes past the end of the stack-allocated buffer.

The vulnerable code path is reached from the ESP-NOW receive callback, which
processes incoming wireless frames before any MAC address or CRC validation.

## Affected versions

- ExpressLRS Backpack release **1.5.9** (commit `437a4f43e02a448ab66ec6520ec55cee4fcb10a6`)
- Current master branch (`d15ed3057efc60f5f580b3ed9b1084f4f35a49dd`) as of 2026-10-19
- Likely all versions that use the same `lib/MSP/msp.cpp` parser with a
  fixed-size payload buffer and unchecked MSPv2 length

## Impact

- **Stack buffer overflow:** Attacker-controlled length drives writes beyond
  the 64-byte payload buffer on the stack.
- **Denial of service:** Probable (crash via stack corruption).
- **Code execution:** Not demonstrated on an embedded ESP8266/ESP32/ESP32-C3
  target. Stack layout, NX, and ASLR properties vary by platform and SDK.

## Attack prerequisites

1. The attacker must be within ESP-NOW range of the Backpack device
   (typically Wi-Fi range, ~100 m line of sight).
2. ESP-NOW frames are unencrypted by default. No pairing, binding phrase,
   or prior link access is required.
3. The attacker sends a crafted ESP-NOW frame containing an MSPv2 message
   with a payload length field > 64.

## Affected source locations

- `lib/MSP/msp.h` — declares 64-byte input/payload capacity
- `lib/MSP/msp.cpp` — accepts MSPv2 16-bit payload length, stores each
  byte at incrementing offset without bounds check
- `src/Vrx_main.cpp`, `src/Tx_main.cpp` — ESP-NOW receive callbacks that
  create a local `MSP` object and parse all received bytes

## Evidence boundary

| Claim | Status |
|---|---|
| Stack overwrite via oversized MSPv2 length | **Reproduced offline** (host ASan fixture) |
| ESP-NOW RF delivery (source-derived) | **Source-confirmed** (callback parses before MAC/CRC check) |
| OTA reproduction on embedded target | **Not demonstrated** |
| Controlled program counter on ESP target | **Not demonstrated** |
| Remote code execution | **Not demonstrated** |

## Remediation

**Recommended fix:** In `msp.cpp`, reject any MSPv2 frame whose declared
payload length exceeds `MSP_PORT_INBUF_SIZE` (64) at parse time, before
writing into the buffer. Discard the remainder of the frame and reset the
parser state.

**Defense in depth:**
- Validate ESP-NOW source MAC against a known peer list before parsing.
- Consider enabling ESP-NOW encryption where the protocol supports it.

## Coordination

This advisory will be sent to the ExpressLRS project maintainers through
their GitHub Security Advisories process or direct maintainer contact.
The reproduction fixture and exact trigger payload will be shared only with
the maintainers. Public disclosure follows the 90-day coordinated timeline
or the maintainer's fix release.

## References

- ExpressLRS Backpack: https://github.com/ExpressLRS/Backpack
- Internal: `research/common-drone-wireless-subsystem-rce-survey-2026-10-19.md` Finding A
- Private packet: `work/disclosure/disc-02/`
