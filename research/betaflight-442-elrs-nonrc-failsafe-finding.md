# Betaflight 4.4.2 SPI ELRS: non-RC packets may refresh stale RC data

Status: **source-level finding; not validated on the live drone**

Reviewed: 2026-10-16

Scope: owned TinyHawk III's integrated SPI ExpressLRS receiver, not the EMAX E8 controller

## Executive summary

In the pinned Betaflight 4.4.2 source, a CRC-valid ELRS packet of a recognized non-RC type can be reported to the generic RX layer as `RX_SPI_RECEIVED_DATA` even though no new RC payload was copied. The generic SPI RX layer converts that status into `RX_FRAME_COMPLETE` and unpacks the retained RC payload. Betaflight's signal-loss path treats the resulting frame as fresh data. If a previously valid RC payload is retained and qualifying non-RC packets keep arriving while real RC packets stop, this may keep the last stick positions alive and delay or suppress receiver-loss failsafe.

This is **not** evidence of an accepted forged packet, a wireless exploit, or a confirmed vulnerability in the exact installed binary. No RF transmission, controller probing, firmware flashing, or live failsafe test was performed for this review.

## Evidence and version boundaries

| Item | Evidence | Qualification |
|---|---|---|
| Live receiver | `research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md` records Betaflight 4.4.2, `RX_SPI`, `rx_spi_protocol = EXPRESSLRS`, and `failsafe_procedure = DROP`. | A CLI version/configuration read does not establish byte identity with the archived release binary or source. |
| Primary source | `firmware/src/betaflight-4.4.2.tar.gz`, SHA-256 `195b0c6eb66741f6be64f30c63e6a41cc0cf2e990505508f1f7cc270de477c29`; release commit recorded as `23d066d08` in `firmware/README.md`. | Line references below are to members of this archive. |
| Comparator | `firmware/src/betaflight-2026.6.2.tar.gz`, SHA-256 `4b7d5acb364ff1f28265c5e84d1e63bec20a1a1a39f1e292250d7cae251db85a`. | Not installed on the live drone; persistence in this source does not prove live impact. |
| E8 controller | No E8-specific firmware image has been identified in the corpus. | This finding does not characterize E8 firmware. The no-flashing instruction remains absolute. |

## 4.4.2 source trace

Paths below are relative to `betaflight-4.4.2/` inside the primary archive. Line numbers were checked against that archive on 2026-10-16.

1. `src/main/rx/expresslrs.c:729-787`: `processRFPacket()` rejects a packet if `validatePacketCrcStd()` fails (lines 733-735). For a passing packet, it updates `receiver.lastValidPacketMs` at line 742 **before** examining packet type. The RC case copies the DMA packet into the upstream `payload` only when connected and model-matched (lines 745-759). The MSP case processes MSP but does not copy RC data (760-762); the TLM case is a no-op (763-765); the SYNC case processes synchronization but does not copy RC data (766-768). Every recognized case then reaches `return RX_SPI_RECEIVED_DATA` at line 787. Unknown packet types return `RX_SPI_RECEIVED_NONE` at lines 769-770. A SYNC rejected by `processRFSyncPacket()` can still reach the DATA return, provided the earlier CRC check passed; the rejection only affects `shouldStartTimer`.
2. `src/main/drivers/rx/rx_sx1280.c:770`: the SX1280 receive callback passes the result of `processRFPacket(payload, ...)` into `expressLrsSetRfPacketStatus()`.
3. `src/main/rx/expresslrs.c:1104-1141`: `expressLrsDataReceived()` receives the SPI payload buffer, runs connection/configuration updates, and returns the stored non-NONE RF status to the SPI RX scheduler. `handleConnectionStateUpdate()` uses `lastValidPacketMs` for its connected timeout at lines 980-994; non-RC recognized packets therefore also refresh this connection-level clock.
4. `src/main/rx/rx_spi.c:58,80-91,188-193,206-223`: the SPI payload buffer is retained across calls. The ExpressLRS protocol maps to `expressLrsDataReceived()` and `expressLrsSetRcDataFromPayload()`. `rxSpiFrameStatus()` converts `RX_SPI_RECEIVED_DATA` to `RX_FRAME_COMPLETE` and sets `rxSpiNewPacketAvailable`. On the next raw-channel read, `rxSpiReadRawRC()` unpacks `rxSpiPayload` even if this packet did not replace that buffer. `expressLrsSetRcDataFromPayload()` unpacks its supplied payload in `expresslrs.c:1073-1079`.
5. `src/main/rx/rx.c:503-561,675-750`: an SPI `RX_FRAME_COMPLETE` without FAILSAFE/DROPPED flags counts as `signalReceived` (lines 535-541), extends the signal deadline by 100 ms (lines 548-551), and can drive RX data processing. If the retained channels remain pulse-valid and the failsafe switch is off, `detectAndApplySignalLossBehaviour()` keeps `rxFlightChannelsValid` true and calls `failsafeOnValidDataReceived()` (lines 675-750). Thus the generic failsafe layer has no indication that these channels came from an earlier RC packet.

The resulting path is:

`recognized CRC-valid non-RC OTA packet` → `RX_SPI_RECEIVED_DATA` → `RX_FRAME_COMPLETE` → `unchanged rxSpiPayload unpacked` → `apparently fresh, pulse-valid RC channels` → `valid-data notification to failsafe`.

## Conditions and impact limits

- A prior valid RC packet must have populated the retained buffer with plausible channel values. An initial zero or invalid buffer would not necessarily pass the pulse-validity check.
- Qualifying non-RC packets must arrive frequently enough to sustain the RX signal deadline. The source has a 100 ms generic RX deadline; the precise timing under the installed firmware and actual air rate remains unmeasured.
- A CRC-valid packet is a prerequisite. This review does not establish that an outside transmitter can produce or deliver one to this pair, nor that an ordinary E8 produces a suitable sustained sequence.
- The source's `ELRS_TLM_PACKET` case is recognized but not implemented in this v3 receiver path. It is not evidence that uplink TLM packets are normally emitted. MSP/SYNC availability and frequency under normal operation are likewise unmeasured.
- Other safeguards, arming state, switch changes, explicit failsafe flags, and connection state transitions may alter the observed effect. The source path supports a plausible failsafe delay/suppression, not a measured duration or guaranteed indefinite bypass.
- The live configuration records `failsafe_procedure = DROP`; unexpected behavior around an armed aircraft has safety consequences. Do not validate in flight.

## Comparison with the 2026.6.2 corpus

In `betaflight-2026.6.2/src/main/rx/expresslrs.c:811-875`, the same ordering remains: `lastValidPacketMs` is refreshed before dispatch, only the connected/model-matched RC branch copies the payload, MSP/SYNC branches do not, and recognized packets return `RX_SPI_RECEIVED_DATA`. The v4-capable comparator no longer has the v3 TLM branch. Its generic `src/main/rx/rx_spi.c` likewise maps received DATA to a complete frame and retained-payload decoding. This is a source comparison only; 2026.6.2 is not the installed build.

## Safe validation plan

1. First, build an isolated host-side/unit-test harness around the 4.4.2 receive path or instrument a software simulation. Seed one valid RC payload, follow it with a recognized CRC-valid non-RC packet, then assert whether the SPI RX layer reports `RX_FRAME_COMPLETE`, whether the decoded channels remain unchanged, and whether the generic failsafe callback sees valid data. Also test malformed CRC, unknown type, no prior RC payload, and a genuine RC-loss interval.
2. Confirm that this path is present in the *actual installed build* before applying any real-world conclusion. The currently archived release binary is version-matched but not proven byte-identical to the live firmware. Read-only evidence is preferred; do not flash the E8 or drone to resolve identity.
3. Only after a separate owner-approved, controlled bench plan, consider a props-removed/physically restrained receiver test. Establish a baseline RC-loss/failsafe trace and compare it with normal non-RC traffic. Keep RF transmission disabled under the project's current safety gate; this note is not authorization to generate packets.
4. If reproduced, prepare a minimal upstream report to Betaflight maintainers with source references, conditions, and measured traces. Avoid describing the finding as an exploitable bypass until a controlled reproduction establishes impact.

## Candidate code-level correction to evaluate

Separate **RF packet receipt/link statistics** from **new RC channel data**: return `RX_SPI_RECEIVED_DATA` only when an RC payload is actually copied for a connected, model-matched receiver. Non-RC packet processing may still update radio/link state through a distinct status or path, but should not set `RX_FRAME_COMPLETE` for channel decoding. Whether `lastValidPacketMs` should track all recognized packets or RC-only packets requires an explicit connection-state design decision and regression tests; it should not be changed blindly.

No source patch is proposed here, because the behavior and any necessary compatibility constraints have not yet been tested.
