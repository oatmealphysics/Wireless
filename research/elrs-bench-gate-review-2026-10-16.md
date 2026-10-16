# E8–TinyHawk passive bench gate review — 2026-10-16

## Scope and safety state

This review continued the owned EMAX E8 / TinyHawk III Plus ELRS benchmark
without flashing, configuration writes, resets, binding changes, motor
commands, crafted device input, or RF transmission. The TinyHawk enclosure was
not opened. The E8 owner explicitly released COM4 to this session; only the
already validated vendor **Read Configuration** request was sent. Raw UID and
per-device responses remain under ignored `work/` paths.

The owner also permitted future SDR transmission in conversation. No RF was
transmitted because the current `docs/bench-validation-plan.md` requires all
passive gates to be complete first. A private one-shot replay candidate was
prepared offline but not executed.

## Connected-device observations

Windows enumerated the following devices:

| Device | Endpoint / identity | Observation |
|---|---|---|
| EMAX E8 | COM4, `8888:2001` | CDC serial/update personality; normal RF application is not running in this mode |
| TinyHawk III Plus | COM5, `0483:5740` | USB CDC; bus-reported description `Betaflight STM32F411` |
| Ettus B205mini | USB serial `33E8308` | UHD 4.11.0.0; device FW 8.0, FPGA 7.0; USB 3 |

No obvious EMAX configurator, Betaflight configurator, UHD recorder, or Python
serial process was running during enumeration.

The fixed 64-byte E8 read request returned the expected 17-byte response. It
confirmed the existing **150 Hz** rate byte and returned the private six-byte
UID; no setting was changed. The two other configuration bytes remain
insufficiently mapped, so this review does not claim a power or telemetry-ratio
value from them. The private response SHA-256 is
`3afd9fd46212af6ae58bdd1775d34277fef40a8f85f18e1d4688c53594fc0bef`.

## New negative control capture

The B205mini made an eight-second receive-only recording with these accepted
settings:

- center: 2441.000 MHz;
- sample rate: 4.000 MS/s;
- analog bandwidth: 2.000 MHz;
- receive gain: 35 dB;
- antenna: RX2;
- samples: 32,000,000 complex `sc16le` samples;
- elapsed receive time: 8.000484 seconds;
- private file SHA-256:
  `5887edc49a44ff66449fd8a6d0ca32102853fc9949c1fa664de9d249b21af2ed`.

The source-matched PHY scanner found **zero** candidates. This is the expected
negative-control result because the E8 was in its `8888:2001` serial/update
personality, not its normal HID/RF application mode. It is not evidence that
the decoder failed or that the TinyHawk lacks ELRS.

## Corrected independent-UID validation

The optional `--uid-file` path in `tools/elrs-li-offline.py` previously checked
a SYNC frame's CRC but did not enforce the `uid_18bit_match` verdict returned by
the source-matched frame checker. A UID with a deliberately wrong byte 3 and
the same CRC suffix could therefore be reported as validating the SYNC frame.

The decoder now requires both conditions for a SYNC frame when an independent
UID is supplied:

1. source-matched v3 CRC passes; and
2. the transmitted UID/model fragment matches the supplied UID under the
   Betaflight 4.4.2 mask.

A synthetic regression test proves that changing only UID byte 3 converts the
same CRC-valid SYNC candidate from `crc_valid` to `unresolved`. The privacy help
also now describes `--include-bytes` as private-analysis output rather than
suggesting it is suitable for sharing.

Applying the corrected implementation to the two published real capture sets
with the freshly read private controller UID produced:

- 62 total PHY candidates;
- 54 CRC-valid frames;
- 16 SYNC and 38 RC frames;
- all accepted SYNC frames matched the independently supplied 18-bit
  UID/model fragment;
- eight unresolved candidates;
- hybrid switch mode.

The UID and raw packet bytes were neither printed nor committed. This closes
the bench plan's independent UID-fragment check for these captures.

## Passive nonce and timing evidence

Across the two independent capture sessions, the 16 accepted SYNC frames form
14 consecutive within-file transitions. For every transition:

`round(delta_time * 150) mod 256 == delta_nonce mod 256`

All 14 comparisons passed, including two-symbol-spaced SYNC pairs, 319/320/321
slot revisit intervals, and a 960-slot interval. The largest residual between
the observed time delta and the nearest 150 Hz slot count was approximately
522 microseconds. Observed FHSS-index changes were 0, 80, or 160 modulo 240 as
expected for the captured sync-channel visits. This is strong passive support
for the 150 Hz nonce/timing model; it is not evidence of live forged-frame
acceptance.

## Active-RF gate status

| Gate | Current status | Evidence / remaining work |
|---|---|---|
| G1 PHY match | Pass | Two independent real sessions, source-matched SF7/BW812.5 kHz candidates |
| G2 frame decode | Pass | 54 real CRC-valid frames, including 16 SYNC |
| G3 independent UID | Pass | 16 SYNC frames pass corrected CRC plus 18-bit UID/model check |
| G4 multi-channel FHSS | **Open** | Existing captures cover only the narrow sync-channel neighborhood; need a receive-only wideband capture during normal E8 RF operation |
| G5 nonce model | Pass for captured hybrid-mode evidence | 14/14 within-file SYNC transitions match 150 Hz slot/nonce progression |
| G6 timing | Pass | Packet/revisit timing agrees with the 150 Hz model within the plan's tolerance |
| G7 control capture | Pass | E8 serial/update mode produced zero PHY candidates with the drone still connected |
| G8 byte decoder | Pass | Externally measured WaveShark LI map plus independent off-air vectors and real CRC checks |
| G9 owner approval | Received in principle, test-specific sign-off still required | Exact target, payload, count, power, physical setup, and stop conditions must be restated after G4 |

No active RF test should run until G4 is closed. The next bench action is to
boot the E8 normally, confirm the pair links, and make a short receive-only
wideband capture with the B205mini. COM4 will disappear in that mode; this is
expected and no serial access is required during the RF recording.

## Validation

- full local suite: **100 tests passed**;
- `tools/validate-project.py`: passed across 176 tracked files;
- `git diff --check`: passed;
- CI now runs unit-test discovery after project validation on both supported
  operating systems and Python versions.
