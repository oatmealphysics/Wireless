# ELRS v3 Hybrid-RC Replay Canary

**Date:** 2026-10-17

**Status:** Offline selector and validators complete; no RF transmission used

**Scope:** Betaflight 4.4.2 hybrid RC decode, fail-closed replay selection,
live AUX-configuration evidence, CRC14, and long-interleaver verification

## Summary

`tools/elrs-v3-rc-replay-canary.py` selects one previously captured,
CRC-valid hybrid RC frame as an observable replay canary. It does not invent
control values. A candidate must prove all of the following:

- the packet is an RC packet with a valid v3 UID-derived CRC;
- AUX1 is low and the live TinyHawk configuration proves AUX1-low is disarm;
- throttle is at most 1050, with only stricter command-line limits allowed;
- pitch and yaw are within 50 microseconds of center;
- roll deviation is between 50 and 300 microseconds;
- the rotating switch value is exactly represented; and
- its destination AUX channel controls no configured mode or adjustment.

The plan remains private, records `transmit_enabled: false`, and cannot be
consumed directly by the existing three-fragment MSP scheduler. Scheduling
integration is a separate change.

## Independent implementation reuse

The tool imports the maintained implementations rather than carrying private
copies:

- CRC14 and UID initialization from `elrs-v3-frame-check.py`;
- SX1280 SF7 4/8 long-interleaver encode/decode from
  `elrs-li-forward.py`.

The selected bytes must round-trip through that encoder/decoder. The saved
plan validator also recomputes the packet CRC, RC safety state, AUX evidence,
and expected symbols. Serialized `safety` booleans are never trusted.

## Betaflight 4.4.2 hybrid RC layout

The decode follows `unpackAnalogChannelData()` and
`unpackChannelDataHybridSwitch8()` in the pinned Betaflight source.

- Bytes 1-5 contain roll, pitch, yaw, and throttle as four little-endian
  10-bit values. Betaflight adds 988, producing 988-2011.
- Bit 7 of byte 6 is the low-latency AUX1 value: zero maps to 1000 and one
  maps to 2000.
- Bits 5-3 select the rotating AUX field.
- Selectors 0-5 update Betaflight AUX indices 1-6 with a 3-bit value.
- Selectors 6-7 update AUX index 7 with the combined 4-bit value.
- Bit 6 is the telemetry/stubborn acknowledgement bit.

For 3-bit fields, raw values 0, 6, and 7 are exact low/default encodings.
For the 4-bit field, only endpoints 0 and 15 are admitted. This encoding
check is not by itself a safety decision; the live configuration evidence is
also mandatory.

## Live AUX safety evidence

`tools/tinyhawk-msp-rc-observe.py --private-aux-safety-out ...` can create a
private `betaflight-aux-safety-v1` snapshot after its existing read-only
identity gate confirms Betaflight 4.4.2, board `S411`, and craft
`TH III Plus`. It adds only these read commands:

- `MSP_MODE_RANGES` (34);
- `MSP_ADJUSTMENT_RANGES` (52); and
- `MSP_MODE_RANGES_EXTRA` (238).

The snapshot contains every returned slot plus explicit slot counts. The
canary tool refuses wrong identity, malformed or truncated arrays, an ARM
range that does not prove AUX1-low disarms, and any candidate whose rotating
AUX destination appears in a usable mode or adjustment range. The snapshot
and resulting plan must remain beneath ignored `work/`.

## Selection policy

There is no unsafe fallback. A frame outside any mandatory boundary is
discarded. Among the remaining candidates, the largest permitted roll
deviation is preferred, followed by the earliest input line for deterministic
selection. The roll signal is intended for a tightly coordinated read-only
`MSP_RC` observer; a single 150 Hz frame may otherwise be too brief to see.

## Fresh-SYNC contract

`validate_sync_for_rc_replay()` requires:

- `switch_mode == "hybrid"`; and
- `new_tlm_ratio == 0`.

The eventual scheduling layer must additionally obtain that state from a
newly decoded SYNC accepted by `elrs-v3-fresh-sync-clock.py`. A historical
SYNC or the capture-relative timestamp stored in the replay plan is not a
live transmit clock.

## Private output schema

The `elrs-v3-rc-replay-canary-v1` document contains:

- one entry in `frames` with packet bytes and 21 symbols;
- recomputable RC-channel and safety values;
- `aux_safety_evidence` identifying the gated board/craft and unused rotating
  AUX channel;
- the fixed SF7/BW812500/implicit-eight-byte PHY contract;
- required hybrid/no-telemetry SYNC state; and
- explicit transport limitations and `transmit_enabled: false`.

The plan intentionally omits the UID. Validation receives the private UID and
AUX snapshot separately and fails if CRC, symbols, decoded controls, or
serialized evidence disagree.

## Safety boundaries

This work does not provide a transmitter, arm command, motor command,
configuration write, bootloader operation, binding operation, or firmware
operation. The no-opening/no-flashing rule remains absolute for both devices.
Any later RF acceptance test still requires propellers removed, the drone
visibly disarmed, exclusive USB ownership, a fresh live SYNC, and an
independently validated SDR path.

## Tests

Synthetic tests under `tools/tests/` cover Windows and Linux on Python 3.10
and 3.12 through the normal CI discovery path. They exercise packet decode,
mandatory control bounds, strict throttle-limit behavior, AUX snapshot
identity/completeness/ARM proof, configured mode and adjustment rejection,
fresh-SYNC requirements, CRC and symbol recomputation, serialized-evidence
tampering, private paths, and end-to-end plan creation.
