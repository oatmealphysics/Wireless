# ELRS v3 Wireless-Only Alternative Attack Paths

**Date:** 2026-10-18

**Target:** Owned EMAX TinyHawk III Plus running Betaflight 4.4.2 with an
integrated SPI SX1280 receiver, paired to the EMAX E8 controller

**Status:** Source-derived and host-modelled; no RF acceptance or live impact
has been demonstrated

**Boundary:** Wireless delivery only. No opening, flashing, bootloader action,
firmware update, configuration write, serial write, reset, or RF transmission
occurred during this work. The absolute no-flashing rule applies to both the
drone and controller.

## Result

The strongest route beyond direct RC injection is a **one-SYNC rate-switch
latch** in the TinyHawk's Betaflight 4.4.2 ELRS receiver.

After the legitimate pair has connected once, Betaflight sets `lockRFmode`.
A CRC-valid, identity-matching SYNC can advertise a different supported air
rate. The main loop then forces a disconnect, stops the ELRS timer, and retunes
the SX1280 to the new rate's fixed initial/sync channel. `lostConnection()`
does not clear `lockRFmode`, while `cycleRfMode()` refuses to cycle rates when
that flag is set. The legitimate 150 Hz transmitter can no longer reacquire a
receiver listening with a different LoRa modulation. Source-wise, the latch
persists until receiver reinitialization or a correctly modulated, sustained
valid packet stream sufficient to re-establish phase/LQ lock; it does not rely
on a persistent configuration change.

This yields two related source-level effects:

1. **Targeted link denial:** stop after the forced rate change. The receiver
   remains disconnected on the selected modulation and common initial channel,
   and ordinary receiver-loss/failsafe behavior should follow.
2. **Fixed-channel retained-RC refresh:** after the rate change, repeat a
   CRC-valid type-3 TLM packet, or a CRC-valid SYNC that deliberately fails the
   later identity comparison. Betaflight reports either as new SPI RX data
   without copying a new RC payload. The generic RX layer then decodes the
   retained prior RC payload and may keep presenting it as fresh. This is a
   possible control/failsafe latch, not arbitrary control.

The second effect is physically hazardous if the retained frame is armed or
has nonzero throttle. It must not be tested on the live drone under the current
boundary.

## Ranked pathways

| Rank | Wireless path | Source confidence | Practical confidence | Present decision |
|---|---|---:|---:|---|
| 1 | Different-rate SYNC leaves the previously connected receiver locked to a new PHY on channel 41 | High | Medium | Best alternative DoS candidate; host-only model added |
| 2 | Fixed-channel rejected-SYNC or type-3 keepalives re-present retained RC data | High | Medium-low | Stronger continuation of the stale-RC finding; live test excluded |
| 3 | Direct hybrid-RC injection at the legitimate 150 Hz rate | High | Medium | Existing primary bounded-control path; transport still unproven |
| 4 | MSP-over-ELRS ordinary command plane | High | Medium-low | Read-only `MSP_API_VERSION` remains the only acceptable first canary |
| 5 | SYNC nonce/FHSS resynchronization denial without a rate change | High | Medium | Simpler transient desynchronization, but weaker than the rate latch |
| 6 | E8 telemetry/downlink parser impersonation | Medium | Low in the current configuration | No receive window while telemetry is off |
| 7 | SX1280 BUSY/lost-hop behavior | Medium | Low | No demonstrated wireless trigger on this exact 4.4.2 driver |
| 8 | SX1280 or MSP memory corruption | Low | Low | No credible length-controlled overwrite found |

## 1. Passive bootstrap does not require the binding phrase

The pinned 4.4.2 OTA SYNC structure contains `UID3`, `UID4`, and `UID5`.
The [official ExpressLRS 3.3.2 transmitter source](https://github.com/ExpressLRS/ExpressLRS/blob/3.3.2/src/src/tx_main.cpp)
fills those fields from UID bytes 3--5 and only alters the low six bits of
UID5 when model match is enabled. The target's read-only live configuration
records model ID 255, so the model XOR is zero.

In Betaflight 4.4.2:

- `src/main/rx/expresslrs.c:715-726,922-933` derives the v3 CRC14 initializer
  only from UID bytes 4 and 5 plus OTA version 3;
- `src/main/rx/expresslrs_common.c:189-219` derives FHSS from UID bytes 2--5;
  and
- `src/main/rx/expresslrs.c:662-705` compares the SYNC identity and model-match
  fields.

Consequently, one decoded SYNC from this model-match-disabled target reveals
the CRC material and three of the four FHSS-seed bytes. Only UID byte 2 remains
unknown: 256 offline candidates. Observed hop positions can select the correct
candidate. UID bytes 0 and 1 do not participate in this 4.4.2 air-link CRC,
FHSS seed, or SYNC comparison.

This corrects older project wording that said v3 SYNC exposes only two UID
bytes. A binding phrase may be useful for a wordlist lookup, but it is not a
cryptographic prerequisite for this target.

## 2. Forced rate-switch latch

The exact 4.4.2 source transition is:

1. `processRFSyncPacket()` accepts the identity, writes
   `receiver.nextRateIndex`, and may synchronize nonce/FHSS state
   (`src/main/rx/expresslrs.c:662-712`).
2. `handleConnectionStateUpdate()` sees that the current rate differs from
   `nextRateIndex` and calls `lostConnection()` (`expresslrs.c:954-970`).
3. `lostConnection()` stops the timer, installs `nextRateIndex` with
   `setRfLinkRate()`, and restarts the radio in receive mode
   (`expresslrs.c:510-535`).
4. `setRfLinkRate()` selects `fhssGetInitialFreq()`
   (`expresslrs.c:320-340`), which is the 2.4 GHz initial/sync channel 41
   (`expresslrs_common.c:137-160,189-205`).
5. A prior successful connection set `lockRFmode = true`
   (`expresslrs.c:553-569`). `lostConnection()` does not clear it, and
   `cycleRfMode()` changes rates only when it is false
   (`expresslrs.c:811-831`).

Therefore the normal, already-connected pair has no automatic rate-cycle
timeout after this transition. For comparison, a receiver that has never
connected would use the ordinary rate-cycle interval, and recognized packets
multiply that interval by ten. The source-derived slow windows are about
7.04 s at 500 Hz, 14.08 s at 250 Hz, 23.46 s at 150 Hz, and 35.2 s at 50 Hz.
`lostConnection()` itself resets the multiplier to one, so those slow windows
apply only after a subsequent recognized, non-rate-changing keepalive sets it
back to ten; otherwise the base windows are 0.704 s, 1.408 s, 2.346 s, and
3.520 s. None of those figures limits the locked post-connection state.

The safe form preserves the observed hybrid switch mode and telemetry ratio.
Changing switch mode in the same SYNC sets `configChanged` and can cause a
persistent configuration save; that variant is explicitly excluded.
A same-rate accepted SYNC preserves the fully connected state only when its
nonce, FHSS index, and model-match state also agree; otherwise the pinned
source enters tentative state. This qualifier does not affect the
different-rate forced-loss path above.

## 3. Fixed-channel retained-RC carriers

The already documented stale-RC flaw becomes easier after the rate latch
because the receiver is no longer hopping.

### Type-3 carrier

`src/main/rx/expresslrs.c:729-787` checks the CRC, updates
`lastValidPacketMs`, treats v3 type 3 as a no-op, and still returns
`RX_SPI_RECEIVED_DATA`. The SX1280 callback publishes that result, the generic
SPI RX layer converts it to `RX_FRAME_COMPLETE`, and the unchanged RC payload
is unpacked again:

- `src/main/drivers/rx/rx_sx1280.c:761-773`;
- `src/main/rx/rx_spi.c:57-91,206-223`; and
- `src/main/rx/rx.c:501-565,671-751`.

The generic signal deadline is 100 ms. A source-level keepalive therefore has
to arrive inside that interval to keep presenting a retained pulse-valid frame.
This does not prove how the installed binary's full failsafe state machine will
behave on hardware.

### Identity-rejected SYNC carrier

The outer packet handler updates `lastValidPacketMs` after the CRC check but
before dispatch. If `processRFSyncPacket()` rejects UID3/UID4, its `false`
return only suppresses timer start; the outer handler still returns
`RX_SPI_RECEIVED_DATA`. A CRC-valid SYNC with a deliberately wrong later
identity field can therefore provide the same generic retained-frame refresh
without changing rate, nonce, FHSS index, switch mode, or telemetry ratio.

This carrier also shows why simply removing the explicit v3 TLM case does not
fully repair the architectural issue. Non-RC packet receipt and new RC-channel
availability need distinct return statuses.

## 4. Offline state model

`tools/elrs-v3-rate-switch-latch-model.py` captures the relevant state
transitions without accepting packet bytes or UID material. It has no serial,
SDR, file-write, or radio backend and always reports `transmit_enabled: false`
and `live_validated: false`.

The model and its tests encode a regression-checked interpretation of the
pinned source in which:

- supported different-rate SYNC values force disconnect and retune to channel
  41;
- the timer stops while `lockRFmode` survives, so automatic rate cycling is
  disabled;
- preserving hybrid mode requests no persistent configuration change;
- CRC failure causes no transition;
- a same-rate SYNC with mismatched nonce, FHSS index, or model-match state
  enters tentative state;
- a CRC-valid identity-rejected SYNC still reports a complete generic frame;
  and
- CRC-valid type-3 traffic while disconnected reports complete frames and
  reuses a retained payload.

Because the Python logic repeats the source interpretation, its passing tests
are not independent validation of Betaflight behavior. The next evidence step
is a host C harness around the pinned functions with spies on the radio, SPI
frame status, and failsafe callback; a live result would still require its own
separately approved bounded plan.

## 5. MSP-over-ELRS remains a broad command surface

`USE_MSP_OVER_TELEMETRY` is enabled in the pinned common target configuration.
Once ELRS is connected and model-matched, five-byte uplink fragments are
reassembled and passed to Betaflight's ordinary MSP dispatcher
(`expresslrs.c:622-659`, `expresslrs_telemetry.c:197-301`). This is not limited
to ELRS configuration messages.

High-impact commands exist, including an explicit arming-disable command and
motor/configuration writes. Those are not suitable first probes. The existing
read-only `MSP_API_VERSION` request remains the bounded positive canary. The
model-ID special case is processed before the ordinary model-match gate and
can change stored model match, but that causes a configuration write and is
excluded here.

The reassembler has no wall-clock timeout and is not reset by
`lostConnection()`, so partial fragments can cross connection epochs. This is
a session-boundary weakness, not a demonstrated memory-corruption primitive.
Copies remain bounded to a 65-byte outer buffer, and the inner MSP parser has
its own length cap.

## 6. Controller-side paths

Captured SYNCs advertised effective `new_tlm_ratio == 0`. In upstream v3 this
encodes `TLM_RATIO_NO_TLM`, whose denominator is one. The
[official ExpressLRS 3.3.2 TX path](https://github.com/ExpressLRS/ExpressLRS/blob/3.3.2/src/src/tx_main.cpp)
does not schedule the telemetry `RXnb()` window when the denominator is one.
Assuming the custom E8 follows that scheduler, no controller downlink parser
was reachable during those observed epochs. This is not a claim that the radio
never receives for any purpose; regional listen-before-talk/channel assessment
may sample RF energy without invoking the telemetry parser. Wireless
interference is still possible, but it is not a demonstrated controller parser
exploit.

If a future owner-approved configuration write enabled telemetry, benign
receiver impersonation could first be tested with one LINK-statistics frame,
then one non-final DATA fragment whose acknowledgement is observed passively
in later uplink RC traffic. That would prove OTA telemetry acceptance and
fragment-reassembly reachability, not application/CRSF parser reachability. It
is not available under the current no-write boundary.

An upstream ExpressLRS v3 zero-length CRSF parser crash was fixed in
[PR #3430](https://github.com/ExpressLRS/ExpressLRS/pull/3430) and released in
[v3.6.3](https://github.com/ExpressLRS/ExpressLRS/releases/tag/3.6.3). It is a
useful offline firmware-review lead, but E8 reachability is unproven: the E8's
exact firmware version is unknown, it is an integrated screenless controller,
and no public source links its telemetry reassembler to the vulnerable parser.
In upstream v3.3.2, completed OTA DATA is forwarded toward the handset rather
than fed into the inbound CRSF parser fixed by PR #3430, so upstream itself
provides no OTA-to-vulnerable-parser chain. Custom E8 code could differ, but
there is presently no evidence that it does.

## 7. SX1280 review and negative results

### Same-PHY packet starvation

Betaflight configures the SX1280 for implicit, fixed eight-byte LoRa packets,
disables the chip's hardware CRC, and routes RX_DONE to DIO1. Consequently,
any waveform the radio accepts under the current PHY enters the fixed FIFO and
status chain; the application CRC is checked only later. An appropriately
phased same-PHY frame that wins receiver acquisition can therefore consume a
receive opportunity without knowing the ELRS CRC or UID. At the observed
SF7/BW812.5 kHz mode, source tables give
5.871 ms airtime inside a 6.666 ms slot, with a hop every four slots.

This is a credible denial/starvation mechanism once real SDR waveform
acceptance and hop timing are proven. It is still interference rather than a
parser exploit, supplies no control, and offers less precision than the
CRC-valid rate-switch path.

Betaflight [PR #15694](https://github.com/betaflight/betaflight/pull/15694)
fixed a later SX1280 FHSS pending-hop race. Its headline RX_DONE/tock
interleaving does **not** apply unchanged to 4.4.2: in 4.4.2,
`pendingDoFHSS` survives the normal packet chain and is retried by
`sx1280EnableIRQs()`.

The pinned driver has a narrower path: it clears `pendingDoFHSS` before waiting
for SX1280 BUSY, and a processing-age threshold greater than one millisecond,
checked only from the periodic Tick ISR, can abandon that callback without
restoring the pending hop. At 150 Hz, Tick and Tock are 180 degrees apart, so a
Tock-started wait normally has to survive about 3.33 ms before the next check.
This can leave logical and physical frequency state different until the next
successfully issued scheduled hop--nominally one four-slot dwell if BUSY
recovers, but longer if it does not. No source-backed or bench-demonstrated
wireless method to hold BUSY across that window has been shown for this pair's
SF7/BW812.5 kHz rate. It remains lower priority than the protocol state bugs.

A second, lower-confidence race exists in the source shape:
`expressLrsIsFhssReq()` tests `alreadyFhss` and then advances `fhssIndex`
without one atomic region. A packet callback that passes the guard immediately
before the higher-priority tock path could let both paths advance the logical
hop. [Betaflight issue #14659](https://github.com/betaflight/betaflight/issues/14659)
flagged the separate non-atomic `fhssIndex` read/modify/write but did not
reproduce this check/set interleaving or a live effect. The candidate has a
narrow instruction-level window. It is an inference that precisely phased
PHY-valid frames might increase its probability; deterministic wireless
reachability is not established.

Semtech also documents a continuous-RX condition in which sufficiently heavy
co-channel traffic can leave BUSY asserted. Betaflight uses continuous receive
and its 4.4.2 timeout abandons software work rather than resetting the radio.
However, Semtech's quantified example is for a different BLE-compatible mode
above about 220 packets/s. This target's 5.871 ms airtime bounds non-overlapping,
completed target-format LoRa receptions near 170/s, but Semtech does not define
the LoRa trigger condition; that estimate neither proves nor disproves
reachability. Treat a persistent BUSY lock as a chipset lead, not as a
demonstrated trigger for this PHY. The
[SX1280 product page](https://www.semtech.com/products/wireless-rf/lora-connect/sx1280)
is the authoritative document index.

Other negatives:

- SX1280 implicit-header reception fixes payload length at eight bytes and the
  driver reads exactly eight FIFO bytes; there is no OTA-controlled read
  length here.
- Application CRC failure occurs before packet-type dispatch inside
  `processRFPacket()`, although the radio-driver chain still performs its FHSS
  check afterward.
- FHSS indices are reduced modulo the sequence length.
- Invalid air-rate codes fall back to `receiver.rateIndex` rather than indexing
  beyond the table. That normally equals the current rate, but after a forced
  retune it can still hold the configured/search rate and cause a retune back.
- The MSP receiver's fragment copies are bounded. Stale tail and cross-session
  semantics exist, but no RF-to-memory-overwrite chain was found.
- There is no demonstrated wireless path into the E8's vendor serial/update
  parser, and updater/bootloader work is out of scope regardless.

## Next safe work

1. Port the Python state assertions into a C host harness around the exact
   4.4.2 functions. Seed only a synthetic, safe retained RC payload.
2. Add a private offline UID-byte-2 enumerator driven by independently measured
   hop positions. Do not publish recovered device identity.
3. Keep the existing read-only MSP canary as the first command-plane proof.
4. Do not perform a live rate-switch or retained-RC test without a new,
   explicit bench plan. Such a plan would require props removed, physical
   restraint, a known disarmed/minimum-throttle retained frame, one named
   target, a strict packet/count/power limit, serial observation, stop
   conditions, and power-cycle recovery. It still must never flash either
   device.

The current best statement is therefore: **the source and host model support a
wireless fixed-channel denial/latch path that may be easier to deliver than
full FHSS takeover, but no SDR waveform has yet been accepted by the live
SX1280 receiver.**
