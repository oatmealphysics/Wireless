# DISC-04: Betaflight SPI ELRS — Failsafe Suppression via Non-RC Packets

**Advisory type:** Sanitized (source-derived, no exploit payload)
**Date:** 2026-10-26
**Status:** Draft — pending maintainer contact

## Summary

In Betaflight 4.4.2's SPI ExpressLRS receiver driver, CRC-valid non-RC OTA
packets (SYNC, MSP, TLM) refresh the signal-received timer and cause stale RC
channel data to be reported as fresh to the failsafe state machine. An attacker
who can transmit CRC-valid ELRS packets on the correct FHSS channel can delay
or suppress the receiver-loss failsafe while no legitimate RC data is arriving.

This is a **safety-critical** finding: failsafe suppression during flight could
prevent the drone from entering its configured landing or drop procedure when
the legitimate transmitter link is lost.

## Affected versions

- **Betaflight 4.4.2** — confirmed in source (`expresslrs.c`, `rx_spi.c`, `rx.c`)
- **Betaflight 2026.6.2** — same pattern present in source (comparator only; not installed on the test target)
- Other versions with the SPI ELRS driver may be affected if the same
  `lastValidPacketMs` refresh and `RX_SPI_RECEIVED_DATA` return pattern exists.

## Mechanism

1. `processRFPacket()` (`expresslrs.c`) updates `receiver.lastValidPacketMs`
   **before** checking the packet type (line 742 in 4.4.2 source).
2. Only the RC branch (connected + model-matched) copies new channel data into
   the upstream payload buffer (lines 745–759).
3. Non-RC branches (MSP, TLM, SYNC) process their respective data but do not
   copy RC channels. All recognized types return `RX_SPI_RECEIVED_DATA`.
4. The SPI RX layer (`rx_spi.c`) converts `RX_SPI_RECEIVED_DATA` to
   `RX_FRAME_COMPLETE` and unpacks the **retained** payload buffer.
5. The generic RX layer (`rx.c`) treats `RX_FRAME_COMPLETE` as a valid signal,
   extends the signal deadline by 100 ms, and calls
   `failsafeOnValidDataReceived()` with the stale-but-pulse-valid channels.

**Result:** The failsafe timer is continuously reset by non-RC traffic, even
though no new stick positions have been received.

## Attack prerequisites

| Prerequisite | Difficulty |
|---|---|
| Know the target's UID (or UID[4:5] from SYNC) | Medium — UID[4:5] broadcast in every SYNC packet |
| Compute CRC14 initializer | Low — `(UID[4] << 8 \| UID[5]) ^ 3` for v3 |
| Know the FHSS sequence | Low — deterministic LCG from UID-derived seed |
| Transmit CRC-valid packets at >10 Hz | Requires SDR or compatible radio hardware |

## Impact

- **Failsafe delay:** The default Stage 1 failsafe delay (0.4 s) can be
  extended indefinitely as long as qualifying packets arrive faster than the
  100 ms signal deadline.
- **Failsafe suppression:** The `RX_LOSS_DETECTED → LANDING` transition can
  be prevented entirely, leaving the drone flying on its last received stick
  positions.
- **Combined with jamming:** An attacker could jam the legitimate RC link
  while sending non-RC ELRS packets to suppress failsafe, then inject MSP
  commands (see DISC-05) or let the drone drift uncontrolled.

## Evidence boundary

| Claim | Status |
|---|---|
| Source path: non-RC packets refresh signal timer | **Confirmed** (4.4.2 and 2026.6.2 source) |
| Stale RC data treated as fresh | **Confirmed** (source trace) |
| Live reproduction on hardware | **Not performed** |
| Measured suppression duration | **Not measured** |
| Exploitation on the installed binary | **Not validated** |

## Remediation direction

Separate RF packet receipt/link statistics from new-RC-channel-data
signaling:

- Return `RX_SPI_RECEIVED_DATA` only when an RC payload is actually copied
  (connected, model-matched, RC packet type).
- Non-RC packet processing may still update radio/link state through a
  distinct status value, but should not trigger `RX_FRAME_COMPLETE` for
  channel decoding.
- Whether `lastValidPacketMs` should track all recognized packets or
  RC-only packets requires an explicit design decision and regression tests.

## Coordination

This advisory will be sent to the Betaflight project through their security
reporting channel. The full source trace with line numbers is available in
the private disclosure packet but is also derivable from the public source
archive.

## References

- Betaflight 4.4.2 source: `expresslrs.c:729-787`, `rx_spi.c:58-223`, `rx.c:503-561`
- Internal: `research/betaflight-442-elrs-nonrc-failsafe-finding.md`
- Internal: `research/betaflight-442-vulnerability-audit.md` §3
- Private packet: `work/disclosure/disc-04/`
