# Common drone wireless-subsystem RCE survey

- **Date:** 2026-10-19
- **Scope:** Authorized CTF research against owned equipment and public firmware
- **Objective:** Find memory-safety defects that are reachable from radio traffic,
  including proprietary radio processors such as DJI OcuSync P1/S1/S2, not just
  discrete RF transceivers.
- **Live-device boundary:** This pass was source research and offline analysis. No
  RF was transmitted and neither the EMAX E8 nor the TinyHawk was written,
  reset, opened, fuzzed live, placed in a bootloader, or flashed. The absolute
  no-flashing rule remains in force for both devices.

## Executive conclusion

The useful unit of analysis is the **wireless subsystem**, not only the chip
that creates or demodulates RF. A practical chain is:

```text
RF waveform
  -> radio/baseband or Wi-Fi MAC
  -> radio driver, shared memory, DMA, or IPC
  -> protocol parser (ESP-NOW, CRSF/MSP, DUML, MAVLink, CRTP, video transport)
  -> memory-safety defect
  -> control-data or program-counter influence
```

Commodity packet radios such as SX1280, SX127x, CC2500, nRF24, A7105, and
CYRF6936 usually do not execute the drone application. Their receive FIFO,
length metadata, interrupt handling, and the host MCU parser are the important
boundary. Integrated systems such as DJI OcuSync S1/P1/S2, OpenIPC video links,
ESP-NOW Backpack devices, and Wi-Fi flight controllers combine more of those
layers in one radio subsystem.

One high-priority memory-corruption candidate was found in current ExpressLRS
Backpack source and reproduced offline under ASan/UBSan. Its direct ESP-NOW RF
reachability is source-derived but has not been demonstrated over the air on an
embedded target. Several other confirmed memory defects are RF reachable only
after obtaining the relevant link access. No reviewed path yet proves a
complete ROP/JOP chain or remote code execution.

## Ranked RF-ingress candidates

| Priority | Wireless subsystem | RF-to-parser path | Finding | Current conclusion |
|---|---|---|---|---|
| P0 | ExpressLRS Backpack, ESP8266/ESP32/ESP32-C3 using ESP-NOW | Unencrypted ESP-NOW receive callback -> local `MSP` parser | MSPv2 length drives writes beyond a 64-byte stack payload before CRC or source-MAC validation | Offline corruption confirmed; OTA reachability is source-derived, and controlled PC/RCE is not proven |
| P1 | Standalone ExpressLRS RX | Valid ELRS data uplink -> CRSF router -> RX parameter endpoint | Open PR #3825 fixes attacker-selected CRSF parameter chunk indexing that can cause out-of-bounds stack access | Direct RF after link access; write/read primitive needs exact-build analysis |
| P1 | mavlink-router on a Linux companion | Injectable telemetry/Wi-Fi link -> valid MAVLink -> logging endpoint | Issues #487/#488 report object-buffer and small stack overflows in ULog reassembly | Conditional direct RF chain; RCE unproven and Linux mitigations apply |
| P1 | PX4 over Wi-Fi/telemetry/ELRS MAVLink | RF data link -> MAVLink/FTP/log handlers | Published MAVLink log-handler stack overflow (CVE-2026-32743) and related parser-lifetime issues | RF-deliverable where MAVLink is exposed; public impact is DoS, not proven RCE |
| P1 | INAV over ELRS MSP/CRSF | Valid ELRS link -> MSP-over-CRSF -> MSP dispatcher | `MSP_DATAFLASH_READ` response has a host-ASan-confirmed four-byte overrun in INAV 9.1 | Direct RF after link access; small overwrite, no RCE proof |
| P1 research | DJI OcuSync S1/P1/S2 class subsystem | Proprietary FHSS/OFDM C2 -> radio RTOS/Linux services -> DUML/control handlers | Firmware artifacts exist for some generations; prior paired-controller research reached DUML, but no current outsider-RF memory-corruption primitive was found | High-value closed target; first recover and map exact radio firmware and ingress task |
| P2 | ESP-Drone / ESP32 Wi-Fi | Wi-Fi UDP -> CRTP | Fully open radio-to-parser path and older Crazyflie-derived parser surface | Strong fuzz target; no specific corruption confirmed in this pass |
| P2 | OpenIPC FPV / wfb-ng | Raw 802.11 frames -> radiotap classification/crypto -> authenticated FEC/aggregation -> Linux video/control services | Open C/C++ data plane; only the driver, radiotap/outer framing, dedup/forwarding, and crypto entry are pre-auth | Strong audit target; no confirmed bug yet |
| P2 | Walksnail/Avatar Artosyn and companion Wi-Fi/BLE | Proprietary 5.8 GHz or standard Wi-Fi/BLE -> baseband/Linux services | Downloadable, deeply extractable update images, but no confirmed memory bug | Strong artifact target; exact silicon and outsider-RF prerequisites remain uncertain |
| P2 experimental | Betaflight Spektrum/CYRF6936 | Spektrum RF -> CYRF RX count/FIFO -> 16-byte host packet buffer | Driver appears to trust radio length before a later length check | Direct-RF hypothesis; hardware FIFO behavior must be modeled before calling it an overflow |
| P2 | Standalone UART CRSF into Betaflight 4.4.2 | RF receiver -> serial CRSF -> Betaflight parser | PR #15254 fixed an ASan-confirmed oversized-length read in newer Betaflight | Likely present in 4.4.2, but not the TinyHawk integrated SPI-ELRS path and not a write/RCE primitive |
| P3 / second stage | HDZero VTX, DM5680 integrated 8051 | FC UART MSP DisplayPort -> VTX parser | Unchecked row/column indexing and bounded-buffer concerns exist in open VTX firmware | UART-adjacent, not presently a direct RF ingress path |
| P3 / precedent | DJI Mini SE Enhanced Wi-Fi | Wi-Fi association/control channel | Published research demonstrated remote hijack of a specific old build | Direct wireless control/authentication failure, not OcuSync RCE |

The ranking intentionally requires an end-to-end RF path. A UART-only defect is
not promoted merely because a radio could hypothetically be attached to the
other end. Conversely, RF is the desired delivery medium, not an excluded one:
offline reproduction is a sequencing step used to avoid confusing a crash,
interference, or parser bug with controlled exploitation.

## Finding A: ExpressLRS Backpack ESP-NOW stack overwrite

### Source state and offline reproduction

The finding is present in Backpack release `1.5.9` at commit
`437a4f43e02a448ab66ec6520ec55cee4fcb10a6` and current master
`d15ed3057efc60f5f580b3ed9b1084f4f35a49dd`, as checked on 2026-10-19:

- [`lib/MSP/msp.h`](https://github.com/ExpressLRS/Backpack/blob/1.5.9/lib/MSP/msp.h)
  declares a 64-byte input/payload capacity.
- [`lib/MSP/msp.cpp`](https://github.com/ExpressLRS/Backpack/blob/1.5.9/lib/MSP/msp.cpp)
  accepts the MSPv2 16-bit payload length and stores each payload byte at the
  next offset without rejecting lengths greater than 64.
- [`src/Vrx_main.cpp`](https://github.com/ExpressLRS/Backpack/blob/1.5.9/src/Vrx_main.cpp)
  and [`src/Tx_main.cpp`](https://github.com/ExpressLRS/Backpack/blob/1.5.9/src/Tx_main.cpp)
  create a local `MSP` object in the ESP-NOW receive callback and parse all
  received bytes. The expected source-MAC comparison occurs only after a
  complete packet has been parsed, so it cannot prevent the earlier writes.
- The reviewed ESP-NOW peer setup sets link-layer encryption to false.

[Espressif documents](https://docs.espressif.com/projects/esp-idf/en/v5.4.3/esp32/api-reference/network/esp_now.html)
a 250-byte maximum ESP-NOW v1.0 body. MSPv2 consumes nine bytes of framing and
checksum around the declared payload, permitting 241 payload bytes in one v1
body. The parser's 64-byte array can therefore be followed by **177
attacker-controlled out-of-bounds writes** at the protocol maximum.

An offline harness compiled the unmodified parser and CRC source directly with
WSL Ubuntu `g++ 13.3.0`, `-O1 -g -fno-omit-frame-pointer`, and
`-fsanitize=address,undefined`. ASan/UBSan reported the first invalid index at
`msp.cpp:102` and a heap-buffer-overflow after writes crossed the 156-byte
native harness object. The x86-64 host layout observed 167 bytes written beyond
the containing object; that number is ABI-specific and must not be projected
onto ESP targets.

Both apparent gates are too late:

- A bad checksum does not prevent the writes because checksum validation
  occurs only after every declared payload byte has been stored.
- A wrong source MAC does not prevent the writes because TX and VRX callbacks
  feed every byte to the parser before comparing the sender address.

The harness, upstream checkout, and binaries remain under ignored `work/`.
This document deliberately does not include a transmitter, over-the-air test
frame, or ready-to-send byte sequence.

### Why it is especially credible

The main ExpressLRS repository already fixed the same parser class in commit
[`1ade76d` ("Reject oversized MSP payloads")](https://github.com/ExpressLRS/ExpressLRS/commit/1ade76dbda38023168fb8b2136a59558cc4cad4d).
That change rejects payload sizes greater than the fixed payload buffer and
adds regression coverage. The Backpack fork has not incorporated the bound in
the reviewed files.

The Backpack target registry includes ESP8285, ESP32, and ESP32-C3 products
from multiple FPV vendors, including transmitter modules and video-receiver
accessories. That does **not** establish that the owned EMAX E8 contains or
runs this code; its exact MCU, RF device, and firmware remain unconfirmed.

### What remains before an RCE claim

1. Compile each exact embedded target and retain its ELF/map file.
2. Determine the local object layout, stack direction, saved return/register
   locations, compiler stack protection, and whether the overwrite reaches a
   control-bearing value before an exception/reset.
3. Establish byte constraints and repeatability on ESP8266, Xtensa ESP32, and
   RISC-V ESP32-C3 separately; they are not interchangeable exploit targets.
4. Add a safe regression showing that rejecting lengths greater than 64 occurs
   before the first payload write.
5. Only after controlled execution is demonstrated offline, design a bounded
   owned-device RF validation with an explicit stop/recovery plan. Do not use
   the E8 or TinyHawk for this validation and do not flash either device.

Until those gates pass, the correct description is **offline-confirmed stack
memory corruption on a source-derived direct-RF path**, not demonstrated OTA
corruption or RCE.

## Finding B: ExpressLRS receiver CRSF parameter indexing

[ExpressLRS PR #3825](https://github.com/ExpressLRS/ExpressLRS/pull/3825), open
and approved when reviewed, adds bounds around CRSF parameter chunk and
parameter indexes. In the pre-fix path, a requested `fieldChunk` selects an
offset into a stack chunk buffer before the response writes its first bytes;
an out-of-range parameter index is also dereferenced too early on one path.

The RF chain exists in the receiver source:

```text
ELRS DATA uplink
  -> data-uplink reassembly
  -> CRSF router
  -> RXEndpoint::handleMessage
  -> parameter request/update handler
  -> unchecked chunk/index access
```

Unlike the Backpack finding, this path requires a frame that the receiver
accepts as part of its ELRS link: the correct hopping state, UID-derived state,
nonce/CRC, and protocol framing are relevant. A malicious paired transmitter
or an independently implemented valid link is a realistic research premise.
The current finding is a potential small stack write plus out-of-bounds read;
the exact affected target/build and control impact still need an offline
receiver-firmware harness.

[PR #3801](https://github.com/ExpressLRS/ExpressLRS/pull/3801) provides a useful
starting point: it host-builds real receiver firmware under ASan/UBSan with a
simulated transmitter/radio. Its current coverage should be extended to data
uplink, CRSF parameter chunks, model match, link-state changes, truncation, and
valid-CRC malformed payloads.

## Flight-controller and companion parsers reached through radio

### mavlink-router

[mavlink-router issue #487](https://github.com/mavlink-router/mavlink-router/issues/487)
and [issue #488](https://github.com/mavlink-router/mavlink-router/issues/488)
report attacker-controlled `LOGGING_DATA` length/offset cases that can overrun
a 2048-byte object buffer and a smaller stack object. The chain is conditional:
the companion must run mavlink-router with the logging endpoint enabled, and an
attacker must inject valid MAVLink through a telemetry radio, Wi-Fi, OpenHD,
mLRS, or ELRS bridge. [PR #492](https://github.com/mavlink-router/mavlink-router/pull/492)
notes that the affected message acceptance is not authentication. Linux NX,
ASLR, PIE, and canaries still matter, and RCE is not established.

### PX4 and MAVLink

PX4's published
[GHSA-97c4-68r9-96p5 / CVE-2026-32743](https://github.com/PX4/PX4-Autopilot/security/advisories/GHSA-97c4-68r9-96p5)
describes a MAVLink log-handler stack overflow through a deeply nested path.
MAVLink can arrive over a telemetry radio, Wi-Fi/UDP, or a bridge such as ELRS
MAVLink, making the parser RF reachable on configurations that expose it. The
advisory establishes denial of service, not controlled execution. PX4 also has
an official
[UDP/ASan MAVLink fuzz harness](https://github.com/PX4/PX4-Autopilot/blob/1c8874d41ac9324124e6b47d5dfb3ebc97ab32cb/platforms/posix/src/px4/common/mavlink_fuzz_tests.cpp)
that should be the starting point for variants and regression checks.

PX4's own
[security scope](https://github.com/PX4/PX4-Autopilot/blob/1c8874d41ac9324124e6b47d5dfb3ebc97ab32cb/SECURITY_SCOPE.md)
largely treats an unsecured MAVLink peer as operator-equivalent. This makes
authentication and transport exposure at least as important as turning a
memory crash into ROP.

### INAV and MSP-over-CRSF

[INAV issue #11776](https://github.com/iNavFlight/inav/issues/11776) reports a
host-ASan-confirmed four-byte overrun in the 9.1 `MSP_DATAFLASH_READ` response
construction. [Issue #11779](https://github.com/iNavFlight/inav/issues/11779)
reports an MSPv1-over-telemetry bounds-state problem. If a valid ELRS link can
carry the corresponding MSP request over CRSF, the first bug is wirelessly
reachable. Its small overwrite has not been shown to control a function
pointer, saved return address, or other execution target.

### Betaflight serial CRSF and CYRF6936

[Betaflight PR #15254](https://github.com/betaflight/betaflight/pull/15254)
fixed a parser inconsistency in which input was truncated to a 64-byte receive
buffer but the original declared length still drove CRC processing, producing
an ASan-confirmed out-of-bounds read. This matters to common standalone ELRS/
Crossfire receivers connected by UART. The owned TinyHawk's integrated SPI
ELRS path does not traverse that serial parser.

The current
[Betaflight Spektrum/CYRF6936 driver](https://github.com/betaflight/betaflight/blob/885f12404d4b8cb7e18d03b7847b325b4dcc725e/src/main/rx/cyrf6936_spektrum.c)
also deserves a dedicated hardware-model test: it obtains an RX count and reads
into a 16-byte packet array before later packet-length validation. The
[CYRF6936 datasheet](https://media.digikey.com/pdf/Data%20Sheets/Cypress%20PDFs/CYRF6936.pdf)
describes a 16-byte FIFO and longer logical packet handling. FIFO overflow and
interrupt behavior may prevent the apparent source-level overflow from being
triggered as assumed. This is a testable direct-RF hypothesis, not a confirmed
defect.

## Proprietary DJI OcuSync radio subsystems

The P1/S1/S2-style names should be treated as **radio/baseband subsystems**, not
as interchangeable RF front ends. They can contain their own CPU, RTOS or Linux
environment, DMA/shared-memory interfaces, proprietary C2 processing, and a
host-facing service layer.

The strongest primary public description found is the 2023 NDSS paper
[*Drone Security and the Mysterious Case of DJI's DroneID*](https://www.ndss-symposium.org/wp-content/uploads/2023-217-paper.pdf):

- DJI RC231 and aircraft use proprietary OcuSync links.
- The paper identifies S1 ("Sparrow") as a proprietary ARM Cortex-M SoC with
  two RTOS environments and associates S1 and P1 ("Pigeon") with different
  aircraft generations.
- OcuSync uses wide OFDM downlink and narrow frequency-hopping uplink
  components; there is no ordinary public compatible C2 receiver.
- Radio firmware can be recovered from some controller updates or aircraft
  filesystems, depending on generation.
- The researchers found multiple flight-controller buffer-overflow crashes and
  DUML command paths that a legitimate, controlled RC could relay over OcuSync.
  DJI fixed the reported defects before publication. This proves an OTA path
  through a paired endpoint, not arbitrary outsider SDR injection.

The exact consumer mapping and the name **S2** are not consistently documented
in primary/vendor material. Treat teardown/community labels as hypotheses until
a board marking, signed update manifest, or extracted firmware identifies the
specific target. Older OcuSync-related firmware has community extraction notes
in [dji-firmware-tools](https://github.com/o-gs/dji-firmware-tools/wiki/Firmware-m0901/2496e48a96b27508604ad3dde5a2b02f031130c0),
including encrypted update modules that unpack to Android/Linux images and DJI
services. This is useful artifact evidence, not proof that a particular P1,
S1, or S2 build is vulnerable.

### Practical research route

1. Identify an exact aircraft/controller pair and preserve its signed update
   packages with version and hashes; no live-device flash or downgrade is
   needed.
2. Extract radio/baseband and host artifacts offline. Classify architecture,
   RTOS/Linux version, symbol remnants, compression, encryption, and IPC.
3. Locate the RF receive task and the first authenticated/unauthenticated
   parsers: framing, reassembly, DUML, telemetry, pairing, video metadata, and
   firmware-update handlers.
4. Build host harnesses or emulator shims around parsers before attempting any
   waveform. Diff adjacent firmware versions for newly added length checks.
5. Determine which malformed input can pass the proprietary PHY, hopping, and
   authentication layers. A bug behind authenticated C2 is still useful after
   link compromise, but it is not a pre-auth radio exploit.
6. Require offline control-flow evidence before designing a bounded RF proof on
   owned expendable hardware.

DroneID decoding is not a shortcut to OcuSync command injection. It is a
separate broadcast and proves that an SDR can receive a DJI waveform, not that
the authenticated C2 uplink accepts attacker-controlled messages.

Research on the DJI Mini SE's legacy
[Enhanced Wi-Fi link](https://arxiv.org/abs/2309.05913) is nevertheless a useful
precedent: a radio subsystem can fail at association/control authentication
without requiring memory corruption. That result should not be generalized to
OcuSync or described as P1/S1/S2 RCE.

## Other radio/video subsystem targets

### ESP-Drone and Crazyflie

[Espressif ESP-Drone](https://github.com/espressif/esp-drone) is a clean,
fully open radio-to-application target: Wi-Fi UDP reaches a Crazyflie-derived
CRTP stack on ESP32-class hardware. It is suitable for coverage-guided parser,
fragmentation, port/channel, and state-machine fuzzing.

[Crazyflie's CRTP documentation](https://github.com/bitcraze/crazyflie-firmware/blob/9912584c85db92dbbba14334ffed20ebabba463e/docs/functional-areas/crtp/index.md)
describes the compact protocol used over its nRF24/BLE-related links. The small
frame size limits single-packet overflow opportunities but does not remove
fragmentation, reassembly, command-state, and cross-port surfaces. No new
memory-corruption primitive was confirmed here.

### OpenIPC and wfb-ng

[wfb-ng](https://github.com/svpcom/wfb-ng) consumes raw 802.11/radiotap data,
authenticates/decrypts accepted data with ChaCha20-Poly1305, then performs its
fragment/ring/FEC work before feeding Linux user-space video and control
services. On OpenIPC FPV systems, the pre-auth audit boundary is limited to the
Wi-Fi driver, radiotap and outer packet classification/length handling,
deduplication/forwarding, and crypto entry points. Fragment indexes, ring state,
and FEC/reassembly are post-auth unless the researcher has link keys or replays
a valid captured packet.

### Walksnail/Avatar

Walksnail publishes downloadable ground/air update images, and
[ar-firmware-tools](https://github.com/fpv-wtf/ar-firmware-tools) can extract
kernel, filesystem, device-tree, and user-application components. Community
teardowns associate some revisions with Artosyn AR9201/AR8211-class processors,
but that mapping is not vendor-verified and may vary. The proprietary 5.8 GHz
baseband is a direct RF parser target; Goggles X adds standard Wi-Fi/BLE service
surfaces. No confirmed memory bug was found, so the next step is image
inventory/version diffing and daemon/parser harnessing, not a claimed exploit.

### HDZero and telemetry radios

[HDZero VTX firmware](https://github.com/hd-zero/hdzero-vtx) exposes code for
the DM5680's integrated 8051 MCU. Review found unchecked-looking DisplayPort
row/column use in the MSP display path, but the path is normally FC UART to VTX,
not an over-the-air receive parser. It becomes relevant as a second stage only
if another RF foothold controls that UART or shared state.

[ArduPilot SiK](https://github.com/ArduPilot/SiK) and
[mLRS](https://github.com/olliw42/mLRS) expose auditable radio/bridge firmware.
They are valuable for stateful RF and nested MAVLink fuzzing, although this pass
found no confirmed direct memory corruption in their radio parsers.

## ROP/JOP feasibility by platform class

| Platform class | Typical exploitation conditions | Required proof before claiming RCE |
|---|---|---|
| Cortex-M flight controller | Fixed flash layout and often no ASLR; execute permissions/MPU vary by target and build | Exact ELF/map, reachable saved LR/function pointer, controllable bytes, stable crash/control transfer |
| ESP8266 / Xtensa ESP32 / RISC-V ESP32-C3 | Architecture- and SDK-specific frames, exception behavior, watchdogs, and optional compiler protections | Reproduce separately per architecture; show overwritten control data and post-return behavior |
| 8051 video/radio MCU | Small address spaces and unusual memory models; often fixed code | Exact linker map and memory-space semantics; prove input reaches code pointer/return state |
| Linux radio/video SoC | Usually ASLR/NX/PIE/canaries and process separation | Information leak or mitigation bypass plus controlled instruction pointer in the affected process |
| Proprietary OcuSync RTOS/baseband | Unknown ABI, memory map, task isolation, secure boot, and input authenticity | Extracted exact firmware, architecture/task map, independently reproduced parser fault, authenticated RF reachability |

A sanitizer crash, a source-level out-of-bounds access, or an RF-triggered
reboot is not a ROP/JOP chain. The minimum credible milestone is a controlled
program counter or control-bearing function pointer in an exact offline target
build, followed by a bounded RF delivery proof.

## Recommended work program

### Track 1: close the direct-RF Backpack finding

1. Build representative Backpack targets with ELF/map retention and inspect
   overwrite reach per ESP architecture.
2. Add a tracked safe regression that exercises the parser only and proves the
   upstream `payloadSize > 64` rejection prevents the first invalid write.
3. Prepare a coordinated maintainer report before publishing a transmitter or
   device-crashing proof. No external contact is authorized by this note.

### Track 2: extend the ExpressLRS receiver fuzzer

Cover data-uplink CRSF parameter requests, chunk indexes, model-match states,
truncation, link loss/reconnect, rate changes, and valid-CRC malformed frames.
Use PR #3825 as both an oracle and a regression target. The result should
classify write, read, disclosure, and reset behavior separately.

### Track 3: exercise open end-to-end radio stacks

- Reproduce mavlink-router #487/#488 and inventory which companion images
  expose the affected logging endpoint.
- Add INAV MSP-over-CRSF regression inputs around the documented four-byte
  overrun.
- Run PX4's official MAVLink harness against FTP/log/shell lifetimes and exact
  released builds.
- Build UDP/CRTP and wfb-ng radiotap/FEC fuzz harnesses.
- Extract and diff Walksnail update images, then inventory Wi-Fi/BLE daemons,
  ports, SDKs, and baseband/user-app IPC.
- Model CYRF6936 FIFO and RX-count behavior before a live Spektrum test.

### Track 4: acquire and map closed radio firmware

For DJI P1/S1/S2-class and comparable video links, prioritize versioned update
artifacts, hashes, extraction reproducibility, CPU/RTOS identification, and the
first RF-facing task. A parser diff or offline crash is a meaningful milestone;
raw SDR capture alone is not.

## Applicability to the owned benchmark

- The TinyHawk uses integrated Betaflight 4.4.2 SPI ELRS with an SX1280. The
  Backpack ESP-NOW path, standalone ELRS RX parameter endpoint, UART CRSF
  over-read, and OcuSync findings do not automatically apply to it.
- The E8's exact MCU, radio, and installed firmware remain unconfirmed. Related
  EMAX module target definitions are not proof that the controller runs
  Backpack firmware or even uses the same processor.
- Existing passive decoding and source-derived ELRS v3 work remains the correct
  way to characterize the owned pair. Neither device is an expendable RCE test
  target, and neither may be flashed under any circumstances.

## Bottom line

The generic “chipset” direction is correct when expanded to the complete
wireless subsystem. The most actionable immediate lead is ExpressLRS Backpack:
public current source exposes a large overwrite on a source-derived direct-RF
path before checksum or source-identity validation, and the overwrite now has
an offline sanitizer reproduction. OTA delivery remains untested. The best
closed-platform target is DJI OcuSync P1/S1/S2-style
firmware extraction and ingress mapping, but there is not yet a defensible
public RCE claim. The next milestone should be controlled offline execution on
an exact build, not merely another RF crash.
