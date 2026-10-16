# ELRS v3 MSP API canary: validated offline forward path

Date: 2026-10-16
Target: owned EMAX TinyHawk III Plus, Betaflight 4.4.2, SPI ExpressLRS 150 Hz
Status: **offline packet/symbol/schedule construction validated; no receiver acceptance test**

## Result

`tools/elrs-li-forward.py` closes the byte-to-symbol half of the previously
missing transmit path for the live target's SX1280 SF7, 812.5 kHz,
implicit-header, eight-byte, 4/8-long-interleaving mode. It prepares a
read-only `MSP_API_VERSION` request as the three five-byte stubborn-uplink
fragments expected by Betaflight 4.4.2, wraps each fragment in a CRC14-valid
ELRS v3 MSP packet, and converts every packet to 21 canonical SX1280 payload
symbols. The tool has no SDR/device backend and cannot transmit.

The live UID was used only to generate an ignored private plan at
`work/e8-session/msp-api-version-canary.json`. Default CLI output reports
counts and safety state but omits the UID, packet bytes, symbols, and derived
CRC values. Private plan output is restricted to the ignored `work/` tree.
An additional ignored IQ bundle contains one unscheduled complex-float32
fixture per fragment at 4.0625 MS/s (five samples/chip), amplitude 0.25. Each
file contains 23,840 samples and has a SHA-256 entry in its private manifest;
the manifest explicitly marks the bundle unscheduled and transmission
disabled.

`tools/elrs-v3-canary-schedule.py` now joins that private canary plan to the
latest CRC-valid decoded SYNC observation. It advances the source-defined
nonce and four-packets-per-hop state, selects the UID-derived channel and
frequency for each of the three future fragment slots, and writes only an
ignored, capture-relative plan. It has no device backend, marks transmission
disabled, and explicitly requires a fresh live SYNC clock before use.

## Why serial is relevant

The pinned source establishes that USB MSP and ELRS-carried MSP converge on
the same `mspFcProcessCommand()` dispatcher:

```
ELRS MSP packet
  -> processRFMspPacket()
  -> receiveMspData()
  -> processMspPacket()
  -> handleCrsfMspFrameBuffer()
  -> handleMspFrame()
  -> mspFcProcessCommand()
```

COM5 therefore supplies an application-layer oracle and independent target
identity, while the SDR transport remains a separate proof obligation. COM4
is useful for the private UID/configuration read but cannot be used while the
E8 runs its normal RF application.

The live COM5 oracle was rechecked during this work with propellers removed.
It accepted only the bounded read requests already implemented by
`tinyhawk-msp-rc-observe.py`, verified `BTFL` 4.4.2 / `S411` / `TH III Plus`,
and returned fifteen stable 16-channel `MSP_RC` samples over three seconds.
The accepted `MSP_API_VERSION` payload was `00 01 2d`; the complete validated
MSPv1 reply is `$M>` + length `03` + command `01` + that payload + checksum
`2e`. This establishes the expected application-layer response, not an OTA
delivery result.

## Independent validation gates

1. **Measured LI map:** Encoding both 13-byte WaveShark/TX16S known payloads
   reproduces all 32 independently captured off-air symbols exactly. This is
   stronger than testing only an encoder against its own decoder.
2. **Decoder round trip:** Arbitrary eight-byte packets produce 21 symbols
   that the maintained inverse recovers with zero corrected nibbles.
3. **Betaflight MSP fixture:** The constructed zero-payload MSPv1 request
   matches the first 13 bytes of Betaflight 4.4.2's independent
   `crsfPidRequest` unit fixture (excluding the CRSF CRC that the receiver's
   eight-byte MSP slice does not consume).
4. **Stubborn fragmentation:** The source-defined receiver order is package
   indices `1`, `2`, then final `0`, carrying five bytes each. Reassembly
   yields the expected CRSF/MSP buffer and command byte.
5. **ELRS CRC:** All three synthetic-UID canary packets pass the existing
   independently maintained v3 frame checker as MSP frames. The private live
   plan is generated through the same code path.
6. **Ideal waveform fixture:** The forward CSS fixture's preamble, sync
   chirps, delimiter, and payload recover all 21 encoded symbols through the
   receive-side symbol extractor. This checks code integration but is not an
   independent radio measurement.
7. **Captured clock/state transitions:** The scheduler checked all fourteen
   adjacent CRC-valid SYNC transitions across two private captures. All 14
   independently agreed with the 150 Hz nonce advance and four-slot FHSS
   advance; the largest timestamp-to-slot residual was 521.8 microseconds.
   The resulting private plan assigns all three canary fragments to concrete
   future slots without printing pairing material.

The full offline suite and tracked-file count are recorded in `SESSION-NOTES.md`
after final validation.

## B205mini loopback result

Three bounded hardware attempts transmitted only the first fragment of a
synthetic-UID canary at 2482 MHz, above the pair's highest 2479.4 MHz hop, at
the B205mini's minimum configured TX gain. A single fragment cannot complete
an MSP request and its synthetic UID cannot pass the live link's CRC. The
first receive helper pulled samples only after queueing TX; a corrected helper
then received concurrently. RX sensitivity was also raised while TX gain
remained at minimum.

None of the receive files contained the expected 5.87 ms chirp frame. The
normal extractor found no valid 12-symbol preamble, and a whole-file power
search showed receiver noise plus short unrelated transients rather than the
waveform. The most likely unresolved bench issue is RF coupling/antenna-port
layout between `TX/RX` and `RX2`; a sent-sample count alone is not an RF
waveform validation. No live-UID frame or complete command was transmitted,
and no target state changed.

## What is not yet proven

- The live receiver did not accept a canary. Only an off-hop synthetic-UID,
  single-fragment bench waveform was transmitted.
- The ideal waveform has not been recovered through the B205mini RF path;
  coupling, IQ polarity, amplitude/ramp behavior, frequency error, and
  hardware timing remain empirical gates.
- The new scheduler is capture-relative, not a live receive-clocked backend.
  Three correct packets sent from a stale clock will be ignored.
- `ELRS_CONNECTED` and model match must already hold. The live default model
  ID supports model match, but a sender still needs a correctly synchronized
  session.
- A returned MSP response may be unavailable because runtime telemetry is
  disabled. The v3 MSP-confirm bit offers another observation path, but it
  also requires a downlink capture and correct slot association.

The older `elrs-packet-forger.py` MSP builder emits a single, differently
laid-out fragment and is **not** the validated canary path. The SDR backend in
`elrs-tx-emulator.py` explicitly lacks SX1280 whitening/interleaving and must
not be used for this test.

## Next controlled gates

1. Resolve the B205mini coupling/port arrangement with an attenuated cable,
   second receiver, or confirmed antennas. Require the normal receiver
   pipeline to recover all 21 symbols and the original CRC-valid packet bytes.
2. Turn the now replay-validated slot planner into a receive-clocked backend
   that consumes a newly observed SYNC and proves its scheduled timestamps
   without transmitting a live-UID frame.
3. The observed SYNC ratio is no-telemetry, and COM5 `MSP_RC` cannot reveal
   acceptance of an `MSP_API_VERSION` query. Keep that query as the safest
   transport fixture, but do not call it a live acceptance test without a
   downlink. The observable alternative is one replayed, previously captured
   hybrid-RC frame with low throttle and disarm held, while COM5 watches for
   its brief stick value; that needs its own offline safety validator first.
4. Only after those gates pass, consider one bounded, low-power observable
   canary with the drone disarmed and propellers removed. No setting write,
   arming, motor, reset, bootloader, bind, or firmware command belongs in that
   first acceptance test. The absolute no-flashing rule for both devices
   remains unchanged.
