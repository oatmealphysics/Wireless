# Betaflight 4.4.2 SPI ELRS: MSP motor-output abuse candidate

Status: **source-level attack hypothesis, not a live exploit**

Reviewed: 2026-10-16
Target: owned TinyHawk III Plus, live-reported Betaflight 4.4.2 / `EMAX_TINYHAWKF4SX1280`

## Result

The previously reported non-RC/failsafe path remains plausible, but the single-channel passive recording does not establish a sufficiently frequent accepted non-RC stream to sustain the 100 ms RX signal deadline. A separate and more direct source-level candidate is ELRS-carried MSP command dispatch. In the pinned Betaflight 4.4.2 source, a connected, model-matched receiver can assemble an MSP write request from CRC-valid uplink packets and pass it to the normal flight-controller MSP dispatcher. That dispatcher accepts `MSP_SET_MOTOR` (command 214) without an arming-state check and writes `motor_disarmed[]`; the mixer uses that array for disarmed motor outputs. Thus an accepted command could cause physical motor output **even while the FC is disarmed**. This is an inference from the source call chain, not a measured result on this drone.

This is not arbitrary code execution or a memory-corruption finding. MSP motor control is an intentional Betaflight function, normally used under direct operator control. The security question is whether the radio link's UID/CRC and connection/model-match checks are sufficient to prevent an unauthorized radio sender from reaching that function.

## Evidence and boundaries

- The version-matched source archive is already present at `firmware/src/betaflight-4.4.2.tar.gz`; SHA-256 rechecked as `195b0c6eb66741f6be64f30c63e6a41cc0cf2e990505508f1f7cc270de477c29`. Its reported release revision is `23d066d08`. The installed binary has **not** been read back or shown byte-identical to this archive.
- The live CLI configuration in `research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md` reports BF 4.4.2, SPI ExpressLRS, `failsafe_procedure = DROP`, and runtime `feature -TELEMETRY`. The same note documents that the runtime telemetry flag does not itself disable the compiled MSP-over-ELRS receive path. The compiled path is inferred from the target source/config, not confirmed by an accepted live MSP command.
- In `src/main/rx/expresslrs.c`, `processRFPacket()` first requires the packet CRC, then routes `ELRS_MSP_DATA_PACKET` to `processRFMspPacket()`. The latter requires `ELRS_CONNECTED`; after reassembly, ordinary MSP dispatch requires `connectionHasModelMatch`. Binding/config handling has separate logic and is not evidence that ordinary MSP bypasses model match.
- `src/main/rx/expresslrs_telemetry.c` `receiveMspData()` copies bounded chunks into the MSP buffer; `processMspPacket()` passes MSP request/write frames to `handleCrsfMspFrameBuffer()`. `src/main/telemetry/crsf.c` buffers bounded CRSF MSP fragments; `src/main/telemetry/msp_shared.c` reassembles a request and calls `mspFcProcessCommand()`. The latter uses the normal MSP command handlers in `src/main/msp/msp.c`.
- `src/main/msp/msp_protocol.h` defines `MSP_SET_MOTOR` as 214. In `src/main/msp/msp.c`, its handler assigns each `motor_disarmed[i]` from the supplied motor value and has no adjacent `ARMING_FLAG(ARMED)` rejection or source-descriptor restriction. In `src/main/flight/mixer.c`, disarmed mixing copies `motor_disarmed[i]` into `motor[i]`. Actual output still depends on scheduler, motor protocol, configuration, and ESC state.
- Some MSP write commands have explicit arming restrictions: `MSP_EEPROM_WRITE` rejects `ARMED`. This contrast supports that the absence of such a check in `MSP_SET_MOTOR` is meaningful, but does not prove a radio sender can deliver the command.
- Existing Betaflight `src/test/unit/telemetry_crsf_msp_unittest.cc` covers MSP-over-CRSF reassembly, but does not establish this radio-to-motor effect or the installed build's behavior.

## What would be required to weaponize it

1. Obtain the correct UID/CRC seed and establish a valid connected, model-matched ELRS context. A missing user-supplied binding phrase does not eliminate the UID/CRC requirement.
2. Decode the actual pair's OTA packets with a validated SX1280 long-interleaver/FEC path, then verify CRC and nonce/FHSS behavior against the pinned v3 source. The October 15 passive IQ has chirps and channel timing, **not decoded packet bytes**.
3. Demonstrate command acceptance in a separately approved, physically safe bench test. No RF transmission, crafted-input test, motor-output test, or flash operation was performed here. The current project gate does not authorize live injection or motor actuation; both devices have an absolute no-flashing rule.

Until all three conditions are met, classify this as a **potential command-abuse vector**, not a working exploit. Do not use the existing packet-forger or SDR transmitter against a live device on the strength of this note.

## Relationship to the stale-RC finding

`research/betaflight-442-elrs-nonrc-failsafe-finding.md` traces a separate path by which recognized CRC-valid non-RC packets can appear as fresh RC frames. The October 15 passive analysis shows roughly 2.133 seconds between **observed channel-41 visits**, far longer than the 100 ms generic RX deadline. This single-channel view does not measure the full hopping link's SYNC rate; it cannot establish either sustained suppression or its impossibility. A sufficiently frequent accepted non-RC stream would be needed, plus a controlled observation of failsafe state. The MSP command-abuse path may be more direct if radio delivery becomes possible, but also carries a much higher physical hazard.

## Safe next actions

Update, 2026-10-16: the offline forward LI encoder and a read-only
`MSP_API_VERSION` three-fragment canary now pass independent TX16S off-air
symbol vectors, the Betaflight CRSF/MSP unit fixture, v3 CRC checks, and an
ideal waveform recovery test. See
`elrs-msp-api-canary-forward-path-2026-10-16.md`. This closes packet/symbol
construction, not live RF acceptance. A later bench check transmitted only
one off-hop synthetic-UID fragment at minimum configured gain; it could not
form a command and was not recovered on the B205mini receive path. No live-UID
packet was transmitted.

- Continue receive-only decoding of the existing private IQ, using a known-good SX1280 PHY reference or independent packet-byte oracle. Do not publish raw UID/captures.
- Build a host-only test using the pinned source to assert MSP command dispatch and motor-array behavior with mocked outputs. Include a regression case for insufficient/malformed MSP chunks. This would strengthen the source result without touching hardware.
- If a later owner-approved live plan is considered, require removed propellers, isolated/verified outputs, a designated serial/RF operator, explicit target and count/rate/power limits, stop conditions, and an approved recovery path. **Never flash either device.**
