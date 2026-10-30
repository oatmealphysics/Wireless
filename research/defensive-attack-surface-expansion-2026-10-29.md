# Defensive Drone Attack-Surface Expansion — 2026-10-29

**Status:** source-backed research and roadmap input only
**Execution boundary:** offline artifacts, synthetic fixtures, loopback services,
and SITL only
**Roadmap relationship:** expands and refines `PROJECT-ROADMAP.md` RQ6–RQ14 and
proposes RQ15–RQ22; every implementation row remains `GATED`

## Purpose

This pass identifies drone hardware, firmware, and protocol trust boundaries
that are useful to test without interacting with an aircraft. Here,
"targetable" means a boundary with a concrete applicability question, an
offline test oracle, and a defensible assurance outcome. It does not mean that
the boundary is reachable on the owned E8 or TinyHawk, that a vulnerability is
present, or that an operational exploit has been demonstrated.

The pass intentionally excludes attack recipes, RF transmission, live bus
stimulus, serial or CAN writes, firmware changes, boot-mode transitions,
debug attachment, eFuse or option-byte access, and physical fault injection.
The E8 and TinyHawk remain no-flash/no-opening devices.

## Executive decision

The best near-term additions are configuration and provenance checks, not new
live-device probes:

1. **Betaflight configuration/build attestation** is directly relevant to the
   known 4.4.2 target. It can bind a cloud-build receipt, target, selected
   features, preset include graph, and sanitized configuration to one semantic
   record before any restore or update is considered.
2. **Verified-boot and update-policy conformance** should model authorization,
   target binding, signed-byte coverage, downgrade policy, interrupted-update
   recovery, and peripheral firmware separately. "Signed" is not a complete
   boot-chain finding.
3. **DShot/ESC command and telemetry conformance** is a valuable intra-aircraft
   contract once the actual motor protocol and ESC firmware family are proven.
   It is not a remote-reachability claim.
4. **Hardware/BOM and silicon provenance** should feed the existing errata and
   advisory lanes. Self-reported IDs, MCU unique IDs, and board IDs are
   inventory evidence, not cryptographic attestation.
5. **Diagnostic-artifact privacy** should become a reusable release gate for
   Blackbox, ULog, DataFlash, configuration, telemetry, and core-dump material.

For ecosystems not present on the current Betaflight aircraft, the strongest
software-first paths are MAVLink transaction state machines, PX4 uXRCE-DDS and
offboard leases, GNSS/correction freshness and authentication state, and
DroneCAN/Cyphal peripheral update trust. They remain conditional until a
specific platform, version, and fixture need exists.

## Non-findings and evidence rules

The following are explicit non-findings:

- The E8 MCU, framework, boot chain, and ESP applicability remain unknown.
- A firmware package containing a signature does not prove that the installed
  bootloader enforces it, that all executable regions are covered, or that a
  downgrade is rejected.
- A CRC, APDescriptor checksum, DShot checksum, transfer ID, board ID, node ID,
  MCU UID, or `GetInfo` unique ID is error-detection or identity metadata, not
  source authentication.
- A secure element on a BOM does not prove that firmware uses it, that private
  keys are non-exportable, or that example keys were replaced.
- Galileo OSNMA authenticates Galileo navigation-message data. It does not by
  itself authenticate signal ranging, make a receiver safety-certified, or
  establish that a position solution is plausible.
- A sanitizer crash or state-machine disagreement proves a software defect in
  the tested build and fixture. It does not prove radio reachability, receiver
  acceptance, or an aircraft effect.

Every promoted lane must pin exact source revisions, hash all fixtures, record
the target and build configuration, use monotonic arrival time for freshness,
and report `confirmed`, `absent`, and `unknown` separately. Unknown is not
equivalent to not applicable.

## Overlap control

This document does not duplicate the existing roadmap:

- RQ1/RQ5 already own capture provenance and cross-log timebase work.
- RQ3/RQ4 already own CRSF and read-only MSP contracts.
- RQ6 remains the common firmware provenance/SBOM gate.
- RQ7/RQ8 remain the errata and synthetic fault-model lanes.
- RQ9/RQ10 own OpenDroneID and DRIP trust-state work.
- RQ11 remains the generated MAVLink wire-contract lane.
- RQ12 remains capture-quality work, not device fingerprinting.
- RQ13 remains the only CVE patch-differential lane and still depends on exact
  ESP applicability.
- RQ14 remains the adjacent bus/schema/topology lane, but needs an explicit
  DroneCAN-v0 versus Cyphal-v1 split.

The detailed additions below either extend one of those gates or define a new
stateful assurance problem that the existing rows do not cover.

## Ranked pathway map

| Rank | Pathway | Current-platform relevance | Offline result |
|---:|---|---|---|
| 1 | Betaflight preset/config/build attestation | High: known Betaflight 4.4.2 target | Deterministic semantic configuration and build receipt |
| 2 | Verified boot, update enforcement, target binding, rollback | Medium now; high for future artifacts | Boot/update trust graph and negative fixture corpus |
| 3 | DShot/ESC command-state and telemetry conformance | Conditional on exact FC/ESC evidence | Pure frame/timing/state oracle |
| 4 | Hardware/BOM/silicon provenance and revision-aware errata | Medium using existing evidence | Redacted manifest and applicability diff |
| 5 | Diagnostic-artifact privacy and release sanitizer | High for every retained/shared artifact | Redacted derivative plus provenance manifest |
| 6 | MAVLink parameter, FTP, routing, and transaction oracles | Low for TinyHawk; high for MAVLink stacks | Generated state-machine corpus |
| 7 | PX4 uXRCE-DDS exposure and offboard-control lease | Future PX4/SITL only | Inbound surface manifest and loss/reconnect oracle |
| 8 | GNSS receiver, RTCM, and OSNMA integrity states | Future GNSS/SITL only | Freshness, provenance, and authentication-state oracle |
| 9 | DroneCAN/Cyphal topology and peripheral update trust | Future CAN platform only | Versioned schema/update trust graph |
| 10 | ArduPilot Lua/ROMFS/default-policy integrity | Future ArduPilot artifacts only | Extension inventory and checksum/policy oracle |
| 11 | Rebuild equivalence and dependency-lock attestation | Useful whenever exact sources exist | Variance report with no unexplained normalization |
| 12 | Conditional SRXL2, SmartAudio, and cooperative-traffic state | Not applicable to current link without evidence | Decode-only/state-only adapters |

## 1. Betaflight preset, configuration, and build attestation

### Applicability gate

- Exact Betaflight release and target.
- Firmware artifact plus cloud-build key/log/JSON when available.
- Commit-pinned preset index and recursively referenced preset files.
- Sanitized starting configuration from the same firmware line.

### Offline deliverable

Build a read-only attestor that:

- binds target, release, build key, selected feature defines, target
  configuration, build log, artifact hash, and CLI-reported board/version;
- verifies the preset index hash, resolves recursive `INCLUDE` files, expands
  selected options, and rejects cycles, missing files, unknown commands, or
  incompatible firmware ranges;
- interprets `diff all`/`dump` under the exact version schema and produces a
  deterministic before/after semantic diff;
- flags settings that require reboot, changed resources/ports, receiver or
  motor protocol changes, and references to parameters absent from that build;
- never emits a command stream and has no serial transport.

### Assurance value and limitations

This catches wrong-target builds, missing compile-time features, hidden preset
includes, cross-version restores, and configuration drift. A build key or
repository hash is provenance metadata, not a signature. A semantic diff does
not establish flight safety, and command order plus initial state still matter.

Primary sources:

- [Betaflight Cloud Build API](https://betaflight.com/docs/development/API/Cloud-Build-API)
- [Betaflight firmware presets and format](https://github.com/betaflight/firmware-presets)
- [Betaflight CLI backup/restore guidance](https://betaflight.com/docs/wiki/guides/current/Cli)
- [Betaflight build feature defines](https://betaflight.com/docs/development/Defines)

## 2. Verified boot, update enforcement, and rollback policy

### Applicability gate

For each platform, identify the exact MCU/board revision, boot media, ROM or
application bootloader, firmware envelope, trusted key identity, signed byte
ranges, target/board identifiers, version policy, recovery path, and debug
state. RQ6 provenance is a prerequisite.

PX4, ArduPilot, ESP-IDF, STM32 ROM DFU, and peripheral updates are separate
profiles; evidence from one must not be projected onto another.

### Offline deliverable

- Parse `.px4`, `.apj`, `.hex`, `.bin`, partition tables, and available
  manifest/build metadata without contacting a device.
- Generate a boot-chain graph from immutable ROM through bootloader,
  application, external storage, recovery, and peripheral images.
- Verify declared target, address/size bounds, hashes, signature metadata,
  public-key identity, signed-byte coverage, and version/rollback policy.
- Exercise tampered, wrong-target, old-version, test-key, truncated,
  incompletely covered, and interrupted-update fixtures in a local state
  machine.
- For ESP fixtures only, model `otadata` transitions and `secure_version`
  monotonicity; build configuration alone does not prove production eFuses.

### Assurance value and limitations

PX4 documents Ed25519 authorization and notes that multipart packages may have
only the standalone TOC checked during upload, with remaining images checked at
boot. PX4 and ArduPilot signing documentation does not by itself establish an
anti-rollback policy. ESP secure-boot, flash-encryption, anti-rollback, debug,
and UART-download posture must be reported separately. No option-byte, eFuse,
boot-status, DFU, flash, or debug interaction is authorized by this lane.

Primary sources:

- [PX4 bootloader secure boot](https://docs.px4.io/main/en/advanced_config/bootloader_secure_boot)
- [ArduPilot secure firmware](https://ardupilot.org/dev/docs/secure-firmware.html)
- [ESP-IDF OTA, rollback, and anti-rollback](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/ota.html)
- [ESP-IDF Secure Boot v2](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/security/secure-boot-v2.html)
- [USB DFU 1.1](https://www.usb.org/sites/default/files/DFU_1.1.pdf)
- [STM32 system-memory boot modes](https://www.st.com/resource/en/application_note/an2606-stm32microcontroller-system-memory-boot-mode-stmicroelectronics.pdf)
- [NIST SP 800-193 platform firmware resiliency](https://csrc.nist.gov/pubs/sp/800/193/final)

## 3. Peripheral firmware and update trust

Flight-controller verified boot does not establish trust in CAN nodes, IO
MCUs, GNSS receivers, ESCs, radio/VTX modules, or companion processors.

The offline model should record, per component:

- updater, update transport, staging location, image envelope, board/target
  descriptor, signature/authentication mechanism, version policy, and recovery;
- authorized file server or source, request/response state, transfer identity,
  target matching, interrupted-transfer behavior, and post-update identity;
- whether a property is authenticated, merely checksum-protected, or unknown.

PX4 uses an `APDescriptor` in DroneCAN peripheral images for board matching,
versioning, and checksums. That is useful integrity and applicability metadata,
but is not a cryptographic signature without separate evidence. No CAN, serial,
SD staging, or firmware update belongs in the offline lane.

Primary sources:

- [PX4 DroneCAN firmware update](https://docs.px4.io/main/en/dronecan/)
- [DroneCAN file and firmware-update services](https://dronecan.github.io/Specification/6._Application_level_functions/)

## 4. DShot/ESC command-state and telemetry conformance

### Applicability gate

Prove the FC configuration uses DShot and identify the exact ESC firmware
family/version. The airframe product name is not enough to infer Bluejay,
BLHeli, AM32, or a protocol revision.

### Offline deliverable

Pin Betaflight 4.4.2 and the identified ESC source, then build pure models for:

- frame checksum, pulse timing tolerance, truncation, noise, and resync;
- command repetition, stopped-motor preconditions, cooldowns, and save rules;
- bidirectional GCR telemetry and extended DShot telemetry types;
- telemetry checksum/error rates, plausible units, and contradictory
  eRPM/voltage/current/temperature states;
- loss/stale-data behavior and the boundary between telemetry health and motor
  command authority.

Only synthesized frames and local property/differential tests are in scope. No
GPIO, motor, serial, or RF output is permitted. The result establishes an
intra-aircraft protocol contract, not remote reachability.

Primary sources:

- [Betaflight DShot protocol documentation](https://betaflight.com/docs/development/API/Dshot)
- [Betaflight 4.4.2 DShot implementation](https://github.com/betaflight/betaflight/blob/4.4.2/src/main/drivers/dshot.c)

## 5. Hardware, BOM, silicon, and memory-boundary provenance

### Applicability gate and deliverable

Construct a redacted expected/observed/unknown hardware manifest from existing
artifacts first: board revision, package/part, MCU `DEV_ID`/`REV_ID`, firmware
target/board ID, sensors, peripherals, and their firmware/hardware identifiers.
Classify every identifier as self-reported, immutable, or cryptographically
authenticated. Salt/hash serial and unique-ID values before tracking them.

Use the exact silicon revision to drive RQ7 errata mapping. Do not apply F411
findings to F7/H7 devices. For F7/H7 platforms, add a source-only DMA/cache/MPU
manifest covering buffer regions, alignment, ownership, clean/invalidate
operations, memory attributes, and parser bounds. For RQ8, add reset cause,
BOR, watchdog coverage, and actuator-safe-state invariants to the synthetic
fault model.

### Limitations

The STM32 96-bit UID and firmware-reported component IDs are inventory
evidence, not attestation. Existing artifacts/logs are the first phase. No
enclosure opening, SWD/JTAG, fresh bus stimulus, option-byte access, or induced
brownout/glitch belongs in this pathway.

### Conditional secure-element/key-custody sublane

Some higher-end flight-controller BOMs include a secure element such as an
SE051, but physical presence is not evidence that the flight stack uses it.
Promotion requires the exact board/BOM and part variant, firmware driver and
build configuration, middleware/applet versions, call graph, object policy,
management-channel policy, and provisioning/recovery design. A safe offline
deliverable is a handle-only abstract object policy plus a deterministic fake
backend proving that private-key export, key logging, plaintext fallback, and
example-key acceptance fail closed. No I2C operation, object enumeration,
provisioning, or cryptographic command belongs in the offline lane.

Primary sources for that conditional sublane are the
[NXP SE051 data sheet](https://www.nxp.com/docs/en/data-sheet/SE051.pdf) and the
[PX4 SE05x integration issue](https://github.com/PX4/PX4-Autopilot/issues/26261).
The open-source integration status must be rechecked at promotion; a component
on a board is not a maintained PX4 integration claim.

Primary sources:

- [STM32F411 RM0383](https://www.st.com/resource/en/reference_manual/dm00119316-stm32f411xce-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)
- [STM32F411 ES0287 errata](https://www.st.com/resource/en/errata_sheet/es0287-stm32f411xcxe-device-errata-stmicroelectronics.pdf)
- [ST AN4839 cache/DMA coherency](https://www.st.com/resource/en/application_note/an4839-level-1-cache-on-stm32f7-series-and-stm32h7-series-stmicroelectronics.pdf)
- [ST AN4838 MPU guidance](https://www.st.com/content/st_com/en/technical-documents/an4838-introduction-to-memory-protection-unit-management-on-stm32-mcus-stmicroelectronics_asset/chunk-003-topic-an4838-mpu-setting-example-with-stm32cube-hal.html)

## 6. MAVLink stateful trust boundaries

RQ11 should grow beyond generated IDs/lengths/`CRC_EXTRA` while retaining one
generated source of truth.

### Parameter and cache oracle

Test byte-wise versus C-cast encoding, 16-byte IDs, target component handling,
full-list completion, missing indices, invariant-table assumptions, dropped
`PARAM_VALUE` updates, cache invalidation, PX4 hash behavior, and documented
ArduPilot differences. A write-capable fixture is not required; synthetic
messages can model acknowledgements and cache transitions.

### FTP and packed-parameter oracle

Test sessions, sequence/retry idempotence, path canonicalization, bounds,
single-session behavior, EOF/NAK completion, and the `@PARAM/param.pck` virtual
file. The packed-parameter protocol has live-value and block-padding semantics,
fixed read-size requirements, and no internal integrity check beyond its
header/count; those are concrete parser/state tests.

### Command, routing, and metadata oracle

Test ACK correlation, retries, `IN_PROGRESS`, cancellation, target mismatch,
reboot-driven route invalidation, sysid/compid collisions, broadcast forwarding,
and the requirement that routers not mutate signed frames. Treat component
metadata JSON/XZ and URIs as untrusted input: constrain schemes, paths, JSON
schema versions, CRC expectations, and decompression resources. External fetch
remains disabled by default.

Primary sources:

- [MAVLink Parameter Protocol](https://mavlink.io/en/services/parameter.html)
- [Parameters over MAVLink FTP](https://mavlink.io/en/services/parameter_ftp.html)
- [MAVLink File Transfer Protocol](https://mavlink.io/en/services/ftp.html)
- [MAVLink Command Protocol](https://mavlink.io/en/services/command.html)
- [MAVLink routing](https://mavlink.io/en/guide/routing.html)
- [MAVLink Component Metadata Protocol](https://mavlink.io/en/services/component_metadata.html)

## 7. PX4 uXRCE-DDS and offboard-control lease

### Applicability gate

Pin an exact PX4 release/board with
`CONFIG_MODULES_UXRCE_DDS_CLIENT=y`, the Agent, DDS/ROS distribution,
`dds_topics.yaml`, and matching `px4_msgs`. Keep the test agent on loopback/SITL.

### Offline/SITL deliverable

- Generate an inbound/outbound topic and service manifest.
- Compare uORB/ROS types and hashes, QoS, namespaces, and instance routing.
- Exercise client/session-key uniqueness, create/delete/write status,
  reliable/best-effort sequence state, fragmentation, reconnect cleanup, and
  version mismatch.
- For offboard control, model proof-of-life separately from setpoint freshness;
  test startup qualification, loss timeout, mode-flag precedence, unsupported
  or NaN fields, stale/reordered/duplicate samples, writer collisions, and the
  configured fallback state.

PX4 explicitly documents that its DDS transport is unauthenticated and reaches
uORB directly, requiring network isolation. An XRCE client key is not a
cryptographic identity. No result from SITL should be promoted to a physical
control-path claim.

Primary sources:

- [PX4 uXRCE-DDS guide](https://docs.px4.io/main/en/middleware/uxrce_dds)
- [DDS-XRCE 1.0](https://www.omg.org/spec/DDS-XRCE/1.0/PDF)
- [PX4 offboard-control documentation](https://github.com/PX4/PX4-Autopilot/blob/main/docs/en/flight_modes/offboard.md)

## 8. GNSS receiver, correction-chain, and authentication states

### Applicability gate

Prove the receiver model, firmware, and UBX/proprietary protocol generation;
pin its exact vendor interface specification. Select the PX4/ArduPilot version
and correction transport. Do not treat one receiver generation's configuration
messages as universal.

### Offline/SITL deliverable

- Generate class/ID/length/version contracts, checksum and resynchronization
  tests, configuration ACK/NAK state, and capability negotiation.
- Reassemble MAVLink `GPS_RTCM_DATA`; test fragment sequence, duplicate/loss,
  mixed-source rejection, timeout, available CRCs, correction age, and source
  provenance.
- Exercise simulated GNSS `off`, `stuck`, and `wrong` conditions and compare
  navigation innovations/fault states instead of trusting receiver status
  alone.
- Add Galileo OSNMA test vectors and report navigation-data authentication,
  key/time state, unavailable authentication, and navigation plausibility as
  separate dimensions.

No live GNSS, correction service, RF input, or coordinate-bearing tracked
fixture is required. Coordinates and receiver identifiers must be synthetic or
redacted.

Galileo declared OSNMA Initial Service operational on 24 July 2025. Its current
service definition explicitly limits it to non-safety-critical use, so an
OSNMA-valid message must not be translated into a blanket "position trusted"
verdict.

Primary sources:

- [PX4 GPS drivers](https://github.com/PX4/PX4-GPSDrivers)
- [PX4 RTK integration](https://docs.px4.io/main/en/advanced/rtk_gps)
- [MAVLink `GPS_RTCM_DATA`](https://mavlink.io/en/messages/common.html#GPS_RTCM_DATA)
- [Galileo OSNMA in-force reference documents](https://www.gsc-europa.eu/electronic-library/programme-reference-documents/galileo-in-force/osnma)
- [Galileo OSNMA Service Definition Document v1.0](https://www.gsc-europa.eu/sites/default/files/Galileo-OSNMA-SDD_v1.0.pdf)

## 9. DroneCAN-v0 and Cyphal-v1 topology/update trust

RQ14 must not treat these as one interchangeable protocol family.

For DroneCAN/UAVCAN v0, test DSDL signatures, transfer-ID continuity,
multi-frame reassembly, `NodeStatus` cadence/offline state, uptime regression,
duplicate node IDs, dynamic allocation identity continuity, `GetNodeInfo`, file
service, and `BeginFirmwareUpdate` state. Match service responses by data type,
client/server IDs, and transfer ID.

For Cyphal v1, pin the finalized specification and regulated DSDL set, then
test type version/extent compatibility, subject/service port manifests,
ordering/deduplication, redundant interfaces, Heartbeat, PnP allocation,
`GetInfo`, registers, `ExecuteCommand`, and file/update policy.

Both specifications provide transport integrity and identity fields but do not
turn CRC-valid transfers, node IDs, or unique IDs into authentication. Node
unique IDs are private inventory data. Live CAN transmission remains excluded.

Primary sources:

- [DroneCAN application-level functions](https://dronecan.github.io/Specification/6._Application_level_functions/)
- [Cyphal v1 specification](https://specification.opencyphal.org/Cyphal_Specification.pdf)
- [Cyphal regulated DSDL types](https://github.com/OpenCyphal/public_regulated_data_types)

## 10. ArduPilot Lua, ROMFS, defaults, and parameter policy

### Applicability gate

Require a pinned ArduPilot build with scripting enabled, embedded ROMFS scripts,
or a private captured SD-card inventory.

### Offline deliverable

- Hash and classify ROMFS versus SD-card scripts.
- Compare expected loaded/running sets with `SCR_LD_CHECKSUM` and
  `SCR_RUN_CHECKSUM` semantics.
- Parse embedded defaults, read-only declarations, and parameter-lockdown
  allowlists.
- Verify that release artifacts and configuration declare every extension and
  fallback path; use a fake runtime for deterministic load/error cases.

Checksums detect change, not author trust. ArduPilot's parameter-lockdown
mechanism is not an iron-clad security boundary, and static analysis cannot
establish script timing or flight safety.

Primary sources:

- [ArduPilot Lua scripting](https://ardupilot.org/dev/docs/common-lua-scripts.html)
- [ArduPilot OEM customizations](https://ardupilot.org/dev/docs/common-oem-customizations.html)
- [ArduPilot parameter lockdown](https://ardupilot.org/dev/docs/common-parameter-lockdown.html)

## 11. Diagnostic-artifact privacy and release sanitizer

### Applicability gate and deliverable

Use only existing exported artifacts or synthetic fixtures. Scan Blackbox,
ULog, DataFlash, configuration, `.tlog`, component metadata, and ESP core dumps
for coordinates, missions, vehicle/node IDs, device serials, RC data,
configuration, signing setup material, and memory contents. Produce a redacted
derivative with:

- original and derivative hashes;
- source format/version and transformations;
- per-class removed/retained counts without echoing secrets;
- an explicit list of fields that cannot be proven secret-free;
- encryption state recorded separately from redaction state.

Require PX4 encrypted ULog handling to preserve provenance, and require an
encrypted ESP coredump partition whenever flash encryption is enabled. A
generic RAM scanner cannot prove that a core dump contains no secret, and an
encrypted file is not automatically safe to share with its key or rich
identifiers intact. Originals remain private under `work/`.

Primary sources:

- [Betaflight Blackbox](https://betaflight.com/docs/wiki/guides/current/Black-Box-logging-and-usage)
- [PX4 ULog format](https://docs.px4.io/main/en/dev_log/ulog_file_format)
- [PX4 log encryption](https://docs.px4.io/main/en/dev_log/log_encryption)
- [MAVLink signing and log handling](https://mavlink.io/en/guide/message_signing.html)
- [ESP-IDF core dumps](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/core_dump.html)

## 12. Rebuild equivalence and dependency-lock attestation

Extend RQ6 with concrete per-stack adapters:

- Betaflight: cloud-build receipt, target/config, compile-time features, build
  log/rebuild command, and artifact hash.
- ArduPilot: manifest entry, APJ metadata, board/vehicle/channel, Git identity,
  signed-firmware marker, features, defaults, ROMFS, and image hash.
- ESP-IDF: target, manifests, `dependencies.lock`, `project_description.json`,
  `sdkconfig`, component hashes/sources/overrides, linked-versus-unused
  components, and version-pinned SPDX/CycloneDX/VEX.
- PX4: target, submodules, board config, build container/toolchain, metadata,
  and package contents.

When complete inputs exist, perform two clean builds in digest-pinned
environments and attribute every byte/section difference. Normalize only
fields documented as nondeterministic. Equality does not prove trustworthy
source; inequality does not prove compromise; absence of a reproducibility
claim stays `unknown`.

Primary sources:

- [ESP-IDF dependency lock format](https://docs.espressif.com/projects/idf-component-manager/en/latest/reference/dependencies_lock.html)
- [Espressif SBOM tool](https://github.com/espressif/esp-idf-sbom)
- [PX4 build containers](https://docs.px4.io/main/en/test_and_ci/docker)
- [ArduPilot firmware manifest generation](https://github.com/ArduPilot/ardupilot/blob/master/Tools/scripts/generate_manifest.py)

## 13. Lower-priority conditional adapters

These should not delay the higher-value work and are not applicable without
exact feature evidence:

- **SRXL2:** differential length/CRC, startup listening, handshake, device-ID
  collision, baud negotiation, telemetry scheduling, failsafe freshness, and
  resynchronization. Bind/VTX/forward-programming classes remain decode-only.
- **SmartAudio:** revision negotiation, frame/length/CRC, response correlation,
  retry/timeout, channel/frequency/power/pit-mode state, and a
  deployment-supplied VTX-table allowlist. No UART or RF output.
- **Cooperative traffic/DAA:** synthetic `ADSB_VEHICLE` age, identifier
  precedence/collision, self-filtering, stale expiry, queue eviction, duplicate
  handling, conflict state, and warn-only action policy in SITL.

Primary sources:

- [Spektrum SRXL2 reference implementation/specification](https://github.com/SpektrumRC/SRXL2)
- [TBS SmartAudio protocol](https://www.team-blacksheep.com/media/files/tbs_smartaudio_rev08.pdf)
- [PX4 Detect and Avoid](https://docs.px4.io/main/en/advanced_features/detect_and_avoid)

## Proposed roadmap integration

Promote none of these automatically. Record the following as dormant `GATED`
rows:

- **RQ15:** Betaflight preset/config/build attestation.
- **RQ16:** verified boot, update target binding, and rollback-policy oracle.
- **RQ17:** DShot/ESC command-state and telemetry conformance.
- **RQ18:** PX4 uXRCE-DDS exposure and offboard-control lease oracle.
- **RQ19:** GNSS receiver/correction/authentication-state contract.
- **RQ20:** diagnostic-artifact privacy and release sanitizer.
- **RQ21:** ArduPilot Lua/ROMFS/default-policy integrity.
- **RQ22:** conditional peripheral serial and cooperative-traffic adapters.

Also refine existing rows:

- RQ6 gains build receipts, dependency locks, artifact envelopes, and rebuild
  variance attribution.
- RQ7 gains revision-aware hardware/BOM evidence and F7/H7 DMA/cache/MPU
  checks without diluting the current F411 first target.
- RQ8 gains BOR/reset/watchdog/output-safe-state invariants.
- RQ11 gains parameter-cache, FTP, command, routing, and metadata state oracles.
- RQ14 explicitly separates DroneCAN/UAVCAN v0 from Cyphal v1 and adds
  peripheral update/file-service trust without treating checksums as
  authentication.

Recommended order after the active application backlog is deliberately
promoted into research work:

1. RQ15 Betaflight configuration/build attestation.
2. RQ6 concrete artifact/SBOM and rebuild adapters.
3. RQ20 diagnostic-artifact privacy release gate.
4. RQ17 DShot/ESC conformance if ESC identity is proven.
5. RQ16 update/rollback corpus for a selected platform.
6. Ecosystem-specific RQ11/RQ14/RQ18/RQ19/RQ21/RQ22 work only when an owned
   platform or fixture requirement exists.

This order produces reusable evidence and prevents speculative hardware or CVE
claims from outrunning platform provenance.
