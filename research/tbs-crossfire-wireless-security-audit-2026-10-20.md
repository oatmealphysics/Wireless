# TBS Crossfire wireless security audit

- **Date:** 2026-10-20
- **Scope:** Authorized CTF research using public source, public vendor
  firmware, and offline synthetic fixtures
- **Focus:** Crossfire air-link evidence, ecosystem parser surfaces, update
  artifacts, and the missing steps in an RF-delivered chain
- **Live activity:** None. No device, serial port, network service, or radio was
  accessed; no RF was transmitted; no configuration or firmware was changed.

This report supersedes the security conclusions in
[`docs/crsf-protocol-analysis.md`](../docs/crsf-protocol-analysis.md). That
older document remains as exploratory history, but it overstates several
claims about authentication, encryption defaults, Wi-Fi credentials, replay,
and practical transmitter impersonation.

## Executive result

The Crossfire path is promising, but the evidence supports a narrower result
than the older project note:

1. **Legacy passive decoding is independently demonstrated.** Public 2021
   research followed the frequency hops and decoded channels and telemetry on
   a Crossfire v6.06, 150-Hz FSK link. It recovered per-hop CRC initial values.
   The researcher explicitly said forged-control takeover was **not done**.
2. **A new ecosystem memory-safety candidate was reproduced offline.**
   A reviewed source-available component contains a routed-message parser defect. A
   source-faithful ASan/UBSan fixture confirmed multiple independent
   out-of-bounds reads and writes. The affected versions, parser class, source
   coordinates, and trigger conditions are withheld pending coordinated
   disclosure.
3. **The private defect is not yet a pure RF exploit.** A direct local ingress
   is source-confirmed. A bound receiver can send telemetry and configuration
   data over the air, but proprietary RF admission and forwarding into the
   affected parser have not been established. Arbitrary nearby RF injection is
   also not established.
4. **TBS disclosed a remotely relevant availability bug.** Catalog ID
   `0x0624`, displayed as Crossfire **v6.36**, says it fixed a critical loss of
   bind when telemetry was flooded. The affected-range start and root cause
   are not public. This is evidence for a stateful RF/telemetry attack surface,
   not evidence of code execution or unbound-attacker reachability.
5. **Current artifacts are available for deeper static work.** The reviewed
   v6.48 Tango II radio image contains plaintext ARM Thumb code. The matching
   Nano RX image is opaque/high-entropy after its package header. The official
   Wi-Fi recovery archive contains analyzable ESP8266 images and a browser
   updater, but its authentication defaults and image-verification semantics
   remain unresolved.

No current Crossfire RF RCE, ROP/JOP chain, forged-control acceptance test,
or OTA signature bypass was found. No ready-to-transmit frame or live exploit
was produced.

## Version-number warning

TBS catalog IDs encode the displayed decimal minor version as a hexadecimal
byte. Read both forms when comparing artifacts:

| Catalog ID | Displayed version | Evidence used here |
|---|---:|---|
| `0x060D` | 6.13 | December 2021 revised/resilient OTA release |
| `0x0613` | 6.19 | December 2022 OTA/encryption public release |
| `0x061F` | 6.31 | January 2024 beta corridor |
| `0x0624` | 6.36 | May 2024 beta telemetry-flood fix entry |
| `0x062A` | 6.42 | November 2024 release corridor |
| `0x0630` | 6.48 | July 2026 current public Crossfire package |

Calling `0x0630` “6.30” or `0x0624` “6.24” is incorrect. The catalog's
displayed version strings and release notes establish the encoding convention.

## Ranked research findings

The priorities are research order, not CVSS ratings.

| Priority | Component | Finding | Wireless status | Claim boundary |
|---|---|---|---|---|
| P0 disclosure | Source-available ecosystem component; identity and versions withheld | Routed-message parser defect produces offline-reproduced OOB reads and writes | Direct local ingress confirmed; RF forwarding into the affected parser unproven | Exact component and trigger private; memory corruption, not RCE |
| P0 | Tango II Crossfire radio v6.48 | Public package exposes plaintext ARM Thumb code and strings for OTA, binding, encryption, EEPROM, and CRSF v3 | This is the proprietary OTA boundary | Static-analysis target, not a vulnerability |
| P0 | Legacy v6.06 150-Hz FSK | Hop order, per-hop CRC state, sticks, and telemetry can be learned passively | Demonstrated on two legacy setups | No public injection/takeover proof; not generalized to current or encrypted links |
| P1 | Unknown pre-v6.36 Crossfire range | Vendor's beta v6.36 entry says it fixed loss of bind when telemetry is flooded | Bound RX/FC telemetry reaches the radio link; unbound trigger unknown | Availability only; affected-range start unknown |
| P1 | Pre-fix Betaflight suffixed/calver cloud, RC, or nightly builds whose formatted identity exceeds the old buffer | Device Ping formats compile-time identity into an undersized stack buffer and can hang the FC | Reproduced through established ELRS and TBS Agent links | Unsuffixed finals may fit; attacker does not choose the overflow bytes |
| P1 | INAV 9.1.0 | `MSP_DATAFLASH_READ` can overrun the 512-byte MSP response buffer by exactly four bytes | An accepted MSP-over-CRSF peer can reach the handler; unauthenticated RF access is not established | Fixed in 10.0.0-rc1; memory corruption, not proven RCE |
| P1 disclosure | Two downstream CRSF consumers; identities and versions withheld | Type-specific length-validation candidates | Direct module/accepted-peer boundaries vary; pure RF reach is unproven | Private source review only; no target fault or write demonstrated |
| P1 adjacent | PX4 `<1.17.0-rc2` | CVE-2026-32706 copies an oversized known CRSF packet into a 64-byte global buffer before CRC completion | Official advisory limits the claim to adjacent/raw serial | Confirmed corruption/DoS, not pure unauthenticated RF |
| P1 | Crossfire Wi-Fi v3.20 | Raw ESP8266 images expose WebUI, `/fw`, CRSF/TCP, MAVLink, and optional AP/Web login settings | Wi-Fi is a separate radio path | Authentication defaults and updater verification are unresolved |

## 1. Crossfire system and trust boundaries

“CRSF” and “Crossfire OTA” must not be conflated.

| Layer | Function | Evidence status |
|---|---|---|
| Crossfire air link | Proprietary 868/915-MHz TX/RX protocol | Partially reverse engineered on legacy 150-Hz FSK only |
| CRSF serial | Up-to-64-byte routed UART frames among handset, TX module, RX, FC, and peripherals | Public TBS specification |
| Handset/controller firmware | Local UI, configuration, routing, and module communication | Public and proprietary implementations vary; private finding identity withheld |
| Crossfire radio firmware | Proprietary TX/RX state machine, OTA, link admission, and RF-to-CRSF bridge | Public packages, no source |
| Wi-Fi coprocessor | Web UI, update, CRSF/TCP and MAVLink bridges | Public ESP8266 recovery images |
| Flight controller | Consumer of CRSF RC, telemetry, MSP, and device-management frames | Betaflight, INAV, PX4, and ArduPilot source varies by version |

The public CRSF specification documents routed serial messages, remote
parameters, direct commands, MSP, MAVLink, factory reset, and factory-firmware
commands. It does **not** document the Crossfire air framing, bind
authentication, encryption construction, or which serial frame classes the
proprietary radio firmware admits in each RF direction.

### Model and silicon map

Current product families include Full/Lite, Micro V2 and Nano transmitters,
Tango II, Nano RX, Nano Diversity RX, Nano RX Pro, and 8-channel Diversity RX.
Exact silicon must be tied to a revision:

- the 2021 teardown identified one older Nano RX as
  PIC32MX170F256D plus SX1272;
- that result must not be projected onto newer Nano, Pro, Diversity, or TX
  revisions;
- the current Tango II Crossfire package has a Cortex-M vector table and ARM
  Thumb code, but this pass did not identify its exact MCU part;
- the v3.20 Wi-Fi recovery image has an ESP8266 boot image and
  `ESP8266_RTOS_SDK` strings.

The evidence-supported model map is:

| Target | Public identifier | Verified silicon or artifact | Important boundary |
|---|---|---|---|
| Older Nano RX studied in 2021 | FCC `QOS-RXNANO`; CRSF receiver `0xEC` | PIC32MX170F256D plus SX1272 on the reviewed board | Do not project this mapping onto rev46+, v3, SE, Pro, or Diversity boards |
| Nano RX rev46+/v3 and Nano RX Pro | CRSF receiver `0xEC` | Current receiver packages exist; MCU and RF IC remain unknown | Vendor notes an MCU change; shared form factor/pinout is not shared-silicon proof |
| Diversity Nano RX | FCC `QOS-RXNANODIV`; CRSF receiver `0xEC` | Vendor confirms chip-based true diversity; exact chips unknown | “True diversity” does not prove dual SX1272 devices |
| Micro TX V2 | product `1710`; vendor-linked FCC family `QOS-TX2W`; CRSF TX `0xEE` | Independent teardown reports SX1272 plus SKY66420-11; MCU unknown | FCC family and secondary teardown do not cover every PCB revision |
| Nano TX, Full TX, and TX Lite | CRSF TX `0xEE` | RF MCU/transceiver unknown; official recovery establishes an ESP-family Wi-Fi processor on supported Wi-Fi models | Nano TX similarity to Micro V2 is not proof of identical silicon; TX Lite lacks Bluetooth |
| Tango II / Pro | FCC `QOS-TANGO2`; radio `0xEE`, handset `0xEA`, Wi-Fi `0x12` | Separate handset, Crossfire-radio, and ESP Wi-Fi artifacts; RF MCU part unknown | Never collapse the three firmware domains into one version or trust boundary |

The 8-channel Diversity RX also remains an acquisition target: its public
product identity and CRSF receiver role are clear, but this pass did not
establish its FCC ID or silicon. Agent catalog IDs and filenames should be
captured and hashed for an exact known module instead of assigning historical
forum filenames to a product by resemblance.

## 2. What the public air-link work actually proves

The strongest public work is g3gg0's 2021 analysis of a Tango II and older
Nano RX on approximately v6.06. On the tested 150-Hz mode it established:

- SX1272/SX1276-compatible FSK at about 85.1 kbaud and 42.48-kHz shift;
- 23-byte uplinks and 13-byte downlinks;
- ten-bit OTA channel values;
- 50 uplink and 50 offset downlink frequencies in the tested regional mode;
- a 150-step schedule that repeats about once per second;
- CRC16 on uplink and CRC8 on downlink, each with hop-dependent initial state;
- passive recovery of hop order and CRC state, followed by live channel and
  telemetry decoding.

The article explicitly labels fake-TX control as “Not done yet.” The published
ESP32 project is a receive-side/WIP sniffer, not a transmitter-impersonation
or replay implementation. Therefore:

- passive confidentiality loss is demonstrated for that legacy mode;
- current forged-packet acceptance is not demonstrated;
- replay must account for timing, hop state, and any version-dependent link
  state;
- 50-Hz, LoRa, current v6.48, newer hardware, and encrypted links remain
  separate validation targets.

### Encryption boundary

The official manual says Crossfire can encrypt telemetry and PPM/control data
when enabled, that changing the setting requires rebinding, and that
encryption disables multi-bind. It does not publish the cipher, mode, key
derivation, integrity construction, anti-replay behavior, default state, or
deployment frequency.

The v6.19 (`0x0613`) release changed OTA behavior and introduced the documented
encryption option after the public v6.06 research. It is therefore incorrect
to describe all Crossfire links as either encrypted or unencrypted without a
version-matched observation.

## 3. Private ecosystem memory-safety candidate

A source-faithful host fixture for a routed-message parser in one reviewed
source-available component was compiled with GCC 13.3.0 and ASan/UBSan. Multiple
independent out-of-bounds reads and writes were reproduced offline. This was
not a target-binary decompilation or a target-device test.

The affected component and versions, message class, source coordinates,
boundary arithmetic, fixture modes, and sanitizer traces are retained in an
ignored private disclosure packet. They are intentionally omitted until
maintainers have been contacted. The public claim is limited to
**offline-reproduced memory corruption in affected source**, not a hardware
crash, controlled program counter, ROP/JOP chain, or RCE.

A direct local ingress into the relevant router is source-confirmed. A bound
receiver can carry telemetry and configuration data to the proprietary radio,
but forwarding from that radio into the affected parser is unproven; arbitrary
unbound RF admission is also unproven. This is a parser candidate with an
unclosed RF bridge, not a demonstrated nearby-radio exploit.

The private packet includes bounded remediation and regression guidance.
Maintainer coordination should precede any live reproduction or publication
of an operational trigger.

## 4. Vendor-confirmed telemetry-flood loss of bind

The official catalog entry for ID `0x0624` (displayed v6.36), dated
2024-05-21, says:

> Fix: Critical bug causing loss of bind when the telemetry gets flooded

The same note appears for Crossfire TX, Micro TX variants, Tango II, Nano RX,
newer NanoRX hardware IDs, and Diversity RX, as well as related Tracer images.

What this establishes:

- TBS considered the defect critical;
- the observed impact included loss of bind;
- telemetry volume/state crossed a security-relevant availability boundary;
- v6.36 is a published fix point.

What it does not establish:

- the first vulnerable version;
- whether the root cause was RF reassembly, module scheduling, UART queues, or
  another state machine;
- whether unbound RF can supply the trigger;
- whether memory corruption was involved;
- code execution.

The manual confirms that a connected FC or receiver can feed telemetry,
arbitrary serial bridge data, and MAVLink across the Crossfire link. A
malicious FC or already-bound receiver is therefore a credible wireless
source for the affected data plane. A nearby outsider still needs a valid RF
session or a separate RF-layer acceptance flaw.

The most useful differential corridor is Tango II radio v6.31 (`0x061F`) to
the v6.36 (`0x0624`) fix, with v6.42 and v6.48 as later comparisons. Locate
changes in RF downlink telemetry reassembly, queue limits, loss-of-bind state,
and transitions among bind, connected, and update modes before attempting any
bench validation.

## 5. Public firmware and update artifacts

All files remained ignored under `work/`; none was executed or committed.

| Artifact | Version/model | Bytes | SHA-256 | Static result |
|---|---|---:|---|---|
| `index.xml` | official catalog retrieved 2026-10-20 | 359,636 | `FB0811D966709C78FF5D4D1B33465E737585195A34B813B471EAC72FB15CF9DE` | version/release-note and package checksum oracle |
| `0x000120xx_0x0630.bin` | Nano RX v6.48 | 227,852 | `DD7B88D8C58E19DA718E114C244F2A64CC4F82DDD33CD9E96BA56E96F2CBF750` | opaque/high-entropy body after small target/version header |
| `0x000140xx_0x0630.bin` | Tango II Crossfire radio v6.48 | 135,036 | `E37F7A9A849A8424E9E6D8DD4992E542655621AF670B7BA62734DA5A851961B5` | Cortex-M vector at package offset `0x0B`; plaintext Thumb code and strings |
| `tbs-cloud-activation.zip` | Wi-Fi recovery corpus through v3.20 | 20,230,422 | `6BED2EB77CCFE9FEF59F1A742CD3DE36DBF387819A912DCEB35317670842394F` | ESP8266 images for Crossfire, Fusion, Mambo, Tango II, and Tracer |

### Package observations

- Tango II radio v6.48 has an initial stack pointer `0x20020000` and reset
  vector `0x080C0021` after the package header. Strings include
  `/CROSSFIRE/EEPROM.bin`, `/CROSSFIRE/OTA/`, `CRSF V3`, binding, and
  encryption labels. It is the best exact-build proprietary-radio target.
- The Nano RX v6.48 body yields no comparable semantic strings. Its entropy
  and structure are consistent with encryption, compression, or another
  proprietary packing format; this pass did not distinguish among them.
- The catalog's 32-hex-character `signature` equals the downloaded package's
  MD5 for the reviewed Nano RX and Tango II radio files. That proves
  catalog checksum semantics only. It does not prove that a bootloader accepts
  unsigned or modified firmware.
- Official support documents OTA receiver updates and a golden/recovery image.
  No malformed-update parser flaw or verification bypass was found.

### Wi-Fi v3.20 static surface

The Crossfire v3.20 recovery image is a raw ESP8266 image with an embedded Web
UI. Strings expose:

- browser firmware upload to `/fw`;
- Wi-Fi scanning and station credential forms;
- AP Lock and Web UI Login settings;
- CRSF routing/TCP tasks;
- MAVLink TCP/UDP tasks;
- an uploaded-file SHA-1 display and a `file failed to verify` path;
- OTA partition and image-format checks.

The manual separately documents AP Lock and Web UI Login as configurable
controls, and MAVLink at `192.168.4.1:8888` when enabled. TBS's public Python
CRSF client says it can connect to a Crossfire Wi-Fi module and defaults to
TCP `192.168.4.1:60950`, annotated as a Fusion port.

These facts establish network bridges and update parsers. They do **not**
establish that AP authentication, Web UI login, or firmware authenticity is
disabled by default. Static strings for SHA-1, TLS, RSA, and ECDSA are not proof
that a particular upload path verifies a digital signature. The next safe step
is control-flow analysis of `/fw`, login enforcement, and CRSF/TCP routing in
the exact device-specific image.

## 6. CRSF consumers and bridge evidence

### Betaflight device-ping stack overflow, fixed

Betaflight issue #15658 reports that a valid device ping from an established
ELRS link or TBS Agent made affected builds hang immediately. The vulnerable
code formatted a long calver/suffixed firmware name and board identifier into
`char buff[30]` using unbounded `tfp_sprintf()`.

This is useful Crossfire-chain evidence because it shows that an ordinary
radio-side configuration action can reach flight-controller CRSF code and
produce a memory fault. The attacker does not control the overflowing string;
the build's own compile-time version does. Treat it as deterministic DoS for
affected builds, not an arbitrary-write primitive. The public reproduction used
a sufficiently long suffixed build identity; the PR's size analysis shows that
ordinary unsuffixed final identities can still fit. Scope therefore follows
the formatted string length rather than all builds sharing one version number.
PR #15657 fixed the construction, and the project's reviewed 2026.6.2 source
contains the fix.

### Private downstream length-validation candidates

Two additional CRSF consumers have source-derived type-specific length
validation concerns. Their identities, versions, feature gates, message types,
source coordinates, and malformed-input conditions remain in the private
disclosure packet. Neither has a demonstrated hardware fault, disclosure
primitive, write, or pure RF attacker path.

### INAV 9.1.0 MSP response overwrite, fixed

INAV issue #11776 identifies an exact four-byte response-buffer overwrite in
`serializeDataflashReadReply()`. The vulnerable code chooses the flash read
length from all space remaining in the 512-byte MSP telemetry response, then
writes a four-byte address into that same buffer before writing the selected
amount of flash data. A maximum-size response therefore requires 516 bytes in
a 512-byte destination.

The issue's source baseline is commit
`c5c593d71d33c8e284bf9cd34381588fda7a98c8`; the vulnerable ordering remains
in stable INAV 9.1.0 at commit
`e519b69b02e27c8bdc03b4a0889f1baaae211a54`. INAV's CRSF receiver reassembles
accepted MSP request/write frame types `0x7A` and `0x7C` into the MSP command
path, so a legitimate or already-accepted radio peer can reach the command in
a CRSF deployment. That does not establish arbitrary unbound Crossfire RF
access.

Commit `3ab0e18022d112d4f62aa24f3fe3db3000f1e8cd` fixes the calculation by
reserving the address first; the correction is present in 10.0.0-rc1 and
later. The production bug and four-byte overwrite are established. A physical
FC crash and reliable execution control are not.

### PX4 advisories

- CVE-2026-32706 / GHSA-mqgj-hh4g-fg5p affects PX4 versions before
  `1.17.0-rc2`. An oversized variable-length known CRSF packet is copied into
  a 64-byte global buffer before CRC validation completes. The published
  integration lab confirms ASan corruption and crash.
- GHSA-qpw7-65ww-wj82 affected `1.14.0-rc2` and was fixed in `1.14.0`.

PX4 explicitly limits the newer advisory to an adjacent/raw-serial attacker
such as a malicious receiver or UART man-in-the-middle and does not claim a
pure RF-only unauthenticated exploit. Crossfire can make a malicious bound
receiver wireless relative to the FC, but that does not solve outsider air-link
admission.

### Negative bridge results

The official CRSF specification assigns `0xAA` to a MAVLink envelope, but no
native `0xAA` reassembly into a MAVLink parser was found in the inspected
open-source CRSF implementations. The Crossfire
manual's separately documented MAVLink serial bridge still exists; this
negative result only means there is no demonstrated generic `0xAA` shortcut
into those open-source parsers.

Current ArduPilot source does have several CRSF handlers that trust
type-specific fields after only generic outer-length and CRC checks. In the
reviewed object layout, short frames appear to consume stale bytes inside a
fixed backing frame rather than escape that object. No convincing ArduPilot
CRSF memory-corruption primitive was established in this pass.

## 7. Correction table for the older Crossfire note

| Older claim | Current evidence-based replacement |
|---|---|
| Default Crossfire has no encryption | Default state was not established. Manual documents optional encryption and rebinding requirements. |
| Crossfire has no authentication | Legacy v6.06 researcher inferred no effective authentication for the tested mode; current bind/session construction is unpublished. |
| Hop/CRC recovery enables practical takeover | Passive recovery and decoding were demonstrated; forged-control acceptance was explicitly not done. |
| Binding uses no shared secret | Public materials do not establish the current bind key-exchange or session-secret construction. |
| Wi-Fi AP is open or weak by default | Unsupported. Manual and v3.20 strings show configurable AP Lock and Web UI Login; defaults were not measured. |
| Firmware update has no signature enforcement | Unsupported. Catalog `signature` is MD5, but bootloader/image authenticity policy remains unknown. |
| SX1272/SX1276 describes all Crossfire hardware | It describes the specific legacy teardown and sniffer. Current model revisions need exact identification. |
| 23/13-byte packets describe Crossfire generally | They are verified for the tested legacy 150-Hz FSK mode only. |
| Replay/injection is a ready attack | No public working Crossfire replay or TX-impersonation tool was found. |
| Payload obfuscation remains the main passive blocker | The public sniffer already parses ten-bit channel values on its validated legacy setup. Current-version differences are now the blocker. |

## 8. RF exploit-chain implications

The most defensible chain hypotheses are:

### Chain A: legacy session recovery -> accepted RF frame -> private parser

1. passively identify a legacy, unencrypted 150-Hz configuration;
2. recover its hop schedule and per-hop CRC state using the public method;
3. independently prove a harmless forged frame is accepted;
4. determine which downlink/telemetry CRSF frame classes the TX radio forwards;
5. only then assess whether the private parser defect is reachable.

Steps 1-2 have public precedent. Steps 3-4 do not. The private parser finding
belongs at step 5 and cannot substitute for those missing proofs.

### Chain B: bound or compromised receiver -> radio bridge -> downstream parser

A malicious FC or already-bound receiver has a stronger starting position: the
manual confirms its telemetry and serial data cross the RF link. The key test
is which routed frame classes and directions the TX radio admits toward each
downstream component. That is an integration and filtering question, not a
PHY-decoding question.

### Chain C: telemetry flooding -> availability

The v6.36 release note is a version-anchored signal that telemetry load could
break bind state. Differential analysis should identify the fixed invariant
before any benign bound-link rate test. The public result supports loss of
availability only.

## 9. Recommended next work

1. **Prepare a private maintainer disclosure packet.** Include the exact source
   coordinates, synthetic sanitizer traces, both defect variants, and a bounded
   fix/regression proposal. Do not begin with live hardware.
2. **Map class/direction filters in the Tango II radio image.** Start at the
   plaintext v6.48 CRSF router and shared-FIFO strings. The binary's code begins
   after the package header/vector region and can be loaded as little-endian
   ARM Thumb with the observed flash addresses.
3. **Acquire the v6.31 and v6.36 Tango II radio artifacts.** Function-match the
   telemetry queues and bind-state logic to isolate the vendor's flood fix.
4. **Build a receive-only legacy Crossfire decoder fixture.** Keep it separate
   from any transmitter. Validate against the public 150-Hz captures/repository
   before applying it to other modes.
5. **Establish an RF-to-CRSF admission matrix.** For each frame type, record
   receiver UART -> RF, RF -> TX module, TX module -> handset, Wi-Fi -> module,
   and USB -> handset behavior. Unknown must remain unknown.
6. **Audit the ESP8266 `/fw` handler offline.** Determine login enforcement,
   accepted image layout, digest versus authenticity checks, downgrade policy,
   and partition bounds without running the downloaded image.
7. **Coordinate the two private downstream length candidates.** Start with
   host-only regressions and do not publish message or trigger details before
   maintainer contact.
8. **Treat INAV's published fix as the minimum supported baseline** for its
   already-public response-buffer issue.

Any live crafted-input, bound-link, network, or RF test requires a separate
owner-approved plan under `SECURITY.md`. Nothing in this report authorizes
flashing. The absolute no-flashing rule for the owned E8 and TinyHawk remains
unchanged.

## Sources and provenance

### Primary and vendor sources

- [TBS Crossfire manual](https://www.team-blacksheep.com/media/files/tbs-crossfire-manual.pdf)
- [TBS firmware catalog](https://firmware.team-blacksheep.com/xml/a7826596de134b62ab8aa6ff0cabbddb)
- [TBS Agent and firmware acquisition page](https://www.team-blacksheep.com/products/prod:agentx)
- [official Nano RX FCC grant](https://www.team-blacksheep.com/TBS_Avionics_Limited_QOS-RXNANO_HK_TUV_FCC_4_Grant.pdf)
- [official Diversity Nano RX FCC grant](https://www.team-blacksheep.com/TBS_Avionics_Limited_QOS_RXNANODIV_HK_TUV_FCC_Grant.pdf)
- [official CRSF specification](https://github.com/tbs-fpv/tbs-crsf-spec/blob/4e5fe8824211165e091651403e1667e4f32af0d9/crsf.md)
- [TBS Agent Python CRSF/TCP client](https://github.com/tbs-fpv/tbs-agent-python/tree/93e352ae46ef3019673b3836b6e25a68e1dbf208)
- [TBS Wi-Fi recovery package and procedure](https://www.team-blacksheep.com/new-support/kb/4000153255/)
- [TBS OTA update support note](https://www.team-blacksheep.com/new-support/kb/4000100109/)

### Independent and downstream evidence

- [g3gg0 legacy Crossfire air-link analysis](https://www.g3gg0.de/fpv-analysis-of-tbs-crossfire/)
- [ESP32 Crossfire sniffer source](https://github.com/g3gg0/ESP32_CRSFSniffer)
- [Crossfire SPI reverse-engineering source](https://github.com/g3gg0/CRSF_RevEng)
- [Betaflight issue #15658](https://github.com/betaflight/betaflight/issues/15658)
- [Betaflight PR #15657](https://github.com/betaflight/betaflight/pull/15657)
- [Betaflight 2026.6.2 source release](https://github.com/betaflight/betaflight/releases/tag/2026.6.2)
- [INAV issue #11776](https://github.com/iNavFlight/inav/issues/11776)
- [INAV fix commit](https://github.com/iNavFlight/inav/commit/3ab0e18022d112d4f62aa24f3fe3db3000f1e8cd)
- [ArduPilot CRSF receiver parser](https://github.com/ArduPilot/ardupilot/blob/09f3dadde08fa0e4b07605537f16e6e4b0500262/libraries/AP_RCProtocol/AP_RCProtocol_CRSF.cpp)
- [PX4 CVE-2026-32706 advisory](https://github.com/PX4/PX4-Autopilot/security/advisories/GHSA-mqgj-hh4g-fg5p)
- [PX4 1.14 CRSF advisory](https://github.com/PX4/PX4-Autopilot/security/advisories/GHSA-qpw7-65ww-wj82)

### Selected pinned local source revisions

| Repository | Revision |
|---|---|
| `tbs-fpv/tbs-crsf-spec` | `4e5fe8824211165e091651403e1667e4f32af0d9` |
| `tbs-fpv/tbs-agent-python` | `93e352ae46ef3019673b3836b6e25a68e1dbf208` |
| `g3gg0/CRSF_RevEng` | `3ca28e5f4cdbd3aacf850610ffdfbf9cadcc3a0c` |
| `g3gg0/ESP32_CRSFSniffer` | `1d9e60ce3ff5c250a766c9fa2a0dcdd920c037ac` |
| `iNavFlight/inav` | `13ebed98e40c8990a6dd849eb8c809967976de40` (issue baseline separately pinned in the report) |
| `ArduPilot/ardupilot` | `09f3dadde08fa0e4b07605537f16e6e4b0500262` |
