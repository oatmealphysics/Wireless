# New Hardware Research Blueprint

> **Purpose:** a repeatable method for answering, “We received a new piece of
> hardware—what do we do next?” It covers physical intake, identity,
> acquisition, offline analysis, read-only validation, separately approved
> interaction, and handoff.
>
> **This is a method, not a license.** It operates inside the authorization and
> device boundaries in [`../AGENTS.md`](../AGENTS.md) and the code/bench
> guardrails in [`../SECURITY.md`](../SECURITY.md). In this repository,
> `AGENTS.md` **Security and Scope** supplies the authorization frame and
> **Connected E8: Absolute No-Flashing Constraint** supplies a device-specific
> hard boundary. Named headings control if section numbers change. A more
> permissive step below never overrides a narrower current instruction.
>
> **Companion documents:** once a firmware image is identified, continue with
> [`firmware-to-poc-methodology.md`](firmware-to-poc-methodology.md). Use
> [`new-hardware-quick-reference.md`](new-hardware-quick-reference.md) at the
> bench after the plan and target card are complete.

---

## 1. Operating principles

1. **Identity before assumptions.** Product names are families, not hardware
   identities. Board revision, silicon markings, USB descriptors, radio part,
   bootloader, application, and installed build can all differ within one
   retail model.
2. **Observe before changing.** Start offline and passive. Reads, mode changes,
   writes, crafted inputs, RF transmission, and flashing are different levels
   of interaction and require separate treatment.
3. **Copies before experiments.** Preserve original artifacts, captures, and
   configurations. Analyze copies and record transformations.
4. **Provenance before conclusions.** A binary without a source, hash, target,
   and version is an unidentified sample. It may still be useful, but it cannot
   support an exact-target claim.
5. **One claim, one evidence trail.** Every conclusion names its evidence and
   its limits. The absence of an observation is not proof of absence.
6. **One gate, one decision.** Approval to read is not approval to write;
   approval to transmit is not approval to interfere; approval to update one
   device is not approval to update another.
7. **Recoverability before risk.** Do not make the only unit the first test
   article for an irreversible operation. Establish backup, known-good image,
   recovery interface, power integrity, and a stop plan first.
8. **The host tool is part of the system, not the firmware.** Treat desktop or
   mobile software, updater, bootloader, application firmware, radio firmware,
   configuration, cloud service, and protocol as distinct components.

### 1.1 Evidence vocabulary

Use these labels in notes, tables, filenames, and findings:

| Label | Meaning | Permitted claim |
|---|---|---|
| `OBSERVED` | Directly seen on the named physical device without relying on an inference | “This unit exposed VID/PID X in this recorded state.” |
| `DOCUMENTED` | Stated by a vendor or authoritative specification | “The manual declares X”; it may still differ on this revision. |
| `SOURCE-DERIVED` | Established from pinned source code or a reproducible source build | “This revision's source contains X.” |
| `BINARY-DERIVED` | Established from a named binary hash | “Artifact SHA-256 X contains/implements Y.” |
| `REPRODUCED-OFFLINE` | Repeated with a fixture, emulator, parser, harness, or independent oracle | “The behavior reproduces offline under these conditions.” |
| `VALIDATED-LIVE` | Repeated on the exact owned target/build under an approved bench plan | “This unit/build exhibited X.” |
| `HYPOTHESIS` | Plausible but missing decisive evidence | “X may explain Y; test Z would discriminate.” |
| `CONTRADICTED` | Evidence disproves or materially narrows an earlier claim | Preserve the old claim and link the correcting evidence. |
| `INCONCLUSIVE` | The test could not distinguish the alternatives | State why and identify the smallest better test. |

An evidence label is not a confidence score. “High confidence” cannot promote
`SOURCE-DERIVED` into `VALIDATED-LIVE`; only the matching live evidence can.

### 1.2 Interaction levels

| Level | Examples | Default gate |
|---|---|---|
| L0 — Offline | Read documents, hash/unpack copies, analyze source/binaries/captures | Allowed within repository scope |
| L1 — Passive | Photographs, receive-only RF, enumerate already exposed interfaces, observe boot output | Target card + safe bench |
| L2 — Read-only query | Documented “get/version/status/read config” requests | Approved read-only plan; verify no hidden write/reset |
| L3 — Reversible state change | Temporary mode entry, pairing, runtime config, benign crafted request | Explicit per-change sign-off |
| L4 — Active transmission/test | RF TX, replay, injection, live fuzzing, service disruption test | Explicit per-protocol test plan and sign-off |
| L5 — Persistent/destructive | Flash, erase, restore, fuse/debug-lock change, persistent config, hardware modification | Explicit device-specific authorization + recovery proof; prohibited devices remain prohibited |

Do not promote a task merely because the tool calls it “read,” “connect,”
“recovery,” or “safe.” Determine the actual device effect.

---

## 2. Case structure and records

Create a stable slug such as `vendor-model-boardrev` after identity is known.
Before then, use an intake ID such as `intake-2026-10-16-a`. Start the case at:

```text
references/<device>_material/
├── README.md                 # case index, scope, status, links
├── TARGET-CARD.md            # ownership, device identity, authorization, goal
├── INTERFACE-MAP.md          # physical/logical/RF interfaces and states
├── EVIDENCE-LEDGER.md        # claims, labels, sources, contradictions
├── ARTIFACTS.md              # provenance manifest for public/redacted artifacts
├── TEST-LOG.md               # redacted chronological lab log
├── OPEN-QUESTIONS.md         # unknowns and discriminating tests
├── photos/                   # only sanitized photos suitable for Git
├── docs/                     # public manuals, datasheets, notes, citations
├── source/                   # small pinned source references/patches
└── fixtures/                 # synthetic or redacted protocol samples
```

Large, private, licensed, identifying, or raw material belongs under the
ignored workspace, for example:

```text
work/<device>/
├── originals/               # immutable downloads/dumps/captures
├── working/                 # unpacked and transformed copies
├── photos-private/          # serial numbers, labels, bench context
├── captures/                # USB/serial/network/RF captures
├── analysis/                # Ghidra/IDA/REA projects and scratch output
└── logs/                    # full transcripts and approval records
```

Do not create empty ceremony. Every tracked file should answer a future
operator's question. A small device may keep all six text records as sections
of `README.md`; a complex device should split them as shown.

### 2.1 Target card template

```markdown
# Target card — <intake ID / device slug>

- Owner and authorization source:
- Physical custodian / current operator:
- Location and bench:
- Scope start/end date:
- Allowed now: L0 / L1 / L2 / L3 / L4 / L5
- Explicit prohibitions:
- Hardware: vendor, model, board revision, serial (private location only)
- Firmware/software: installed versions and evidence
- Interfaces in scope:
- Research goal / CTF flag relationship:
- Success condition:
- Safety hazards:
- Recovery assets and known-good baseline:
- Sensitive-data handling:
- Last reviewed by/date:
```

### 2.2 Artifact manifest template

Record one row per original and one row per derived file:

| Field | Required content |
|---|---|
| Artifact ID | Stable local identifier, not merely a filename |
| Kind | Firmware, bootloader, updater, host app, driver, source, config, capture, manual |
| Origin | Full URL/repository/tag/support channel/device procedure |
| Retrieved | UTC timestamp and operator |
| SHA-256 | Hash of exact stored bytes |
| Size/format | Byte count, container, compression, file identification |
| Declared identity | Vendor model, target, region, version, build/date |
| Observed identity | Strings, headers, signature, metadata, architecture |
| Hardware match | Exact confirmed / version-matched / related sample / unknown / mismatch |
| Signing/encryption | Scheme, verification result, key/certificate identity if public |
| Handling | Public/tracked, licensed/local-only, private/ignored, secret/restricted |
| Parent/transform | Original ID and exact command/tool/version for a derived file |
| Notes | Ambiguity, contradictions, missing pieces |

### 2.3 Evidence ledger template

| Claim ID | Falsifiable claim | Label | Target/artifact | Evidence pointer | Limits | Next discriminating test |
|---|---|---|---|---|---|---|
| C-001 | `<this unit exposes ... in state ...>` | `OBSERVED` | `<unit/rev>` | `<photo/log/capture>` | `<what this does not prove>` | `<smallest safe test>` |

Never overwrite a contradicted row. Mark it `CONTRADICTED`, link the newer
evidence, and preserve the reasoning trail.

---

## 3. Phase 0 — Intake and safety

**Goal:** establish custody, authority, a safe bench, and an immutable baseline
before power or software interaction.

### 3.1 Authorization and purpose

Record:

- who owns the hardware and who may approve tests;
- whether the item is expendable, replaceable, unique, borrowed, or needed for
  competition;
- the research question and how an answer affects the CTF plan;
- which interfaces and actions are currently allowed;
- explicit prohibitions, including device-specific limits;
- where private identifiers, captures, keys, and credentials may be stored;
- any competition, regulatory, location, RF-power, or timing constraints.

If authority is incomplete, continue with public-document and offline work.
Do not convert uncertainty into implied permission.

### 3.2 Unpowered physical inventory

Without opening the enclosure unless opening is already authorized:

1. Assign an intake ID and attach a removable label to packaging, device, and
   accessories.
2. Photograph all sides, ports, labels, regulatory IDs, seals, antennas,
   cables, power supplies, adapters, and packaging. Store serial numbers and
   private context only under `work/`.
3. Record vendor/model strings exactly, including punctuation, region suffix,
   FCC/CE/IC identifiers, board revision visible through openings, and date or
   lot codes.
4. Inventory every connector. Do not assume that a USB-shaped connector uses
   USB voltage or signaling, or that an unpopulated header has a standard
   pinout.
5. Record power input ratings and the markings on the supplied power source.
   Measure polarity and continuity only with a suitable meter and an
   unpowered target.
6. Note antennas and feed lines, removable storage, batteries, hazardous
   motion, high voltage, hot surfaces, lasers, pressure, and stored energy.
7. Record whether tamper evidence, warranty seals, or borrowed condition makes
   opening inappropriate.

### 3.3 Bench controls

- Use an ESD-safe surface and current-limited supply when appropriate.
- Verify voltage, polarity, and ground before connection. Power polarity is a
  leading brick-and-fire risk; labels and cable color are not proof.
- Remove propellers and hazardous loads, restrain actuators, and keep a clear
  power disconnect within reach.
- Start with RF transmission disabled: antenna disconnected only when the
  transmitter is safe without a load; otherwise use the correct dummy load,
  attenuation, shielding, or receive-only setup.
- Isolate the analysis host from production networks and secrets.
- Designate one operator per shared port/device. Check the collaboration
  handoff rather than inferring ownership from a free port.
- Capture the unpowered condition and the planned wiring before moving leads.

### 3.4 Baseline and stop conditions

Define expected voltage/current, LEDs, sounds, enumeration, normal boot time,
and shutdown behavior before the first power-up. Stop on excess current, heat,
odor, swelling, unexpected motion or RF, reset loops, corrupted storage,
unknown bootloader state, or any result outside the plan.

**Danger points:** reversed polarity, wrong logic voltage, back-powering through
USB/UART/GPIO, antenna-less transmitter damage, exposed LiPo cells, propellers,
shared grounds, and simultaneous power sources.

**Phase 0 output:** target card, unpowered photo inventory, power/interface
worksheet, bench diagram, allowed interaction level, stop conditions.

**Gate 0:** ownership and current authorization are recorded; the planned
power path and hazards are understood. If not, remain offline.

---

## 4. Phase 1 — Identify by observation

**Goal:** build an evidence-backed system identity and interface map without
changing persistent state.

### 4.1 Think in components and states

Model the device as a system rather than one firmware image:

```text
power/charging
    └─ boot ROM / immutable loader
       └─ bootloader / recovery
          └─ main application or OS
             ├─ radio coprocessor / modem firmware
             ├─ configuration and calibration storage
             ├─ removable storage
             └─ companion host/mobile/cloud software
```

List every known state separately: powered off, charge-only, normal boot,
normal USB, documented configuration mode, mass storage, DFU/update,
recovery, factory test, pairing, setup AP, normal/managed network mode, BLE
advertising, output-safe state, and operational RF/actuator output. A single
physical port can expose different interfaces in different states; a network
device may expose a setup AP and web server before joining a network, then a
different address, service set, or egress pattern in normal mode. Enumerate
every personality that can be observed within the current authorization, and
record the transition that exposes it. The E8 lesson is useful here: multiple
USB personalities can reveal architecture, but a mode transition is still an
action and must be allowed. Do not enter a reset, bootloader, factory, pairing,
or update mode merely to complete the table.

### 4.2 External and board identity

Collect the strongest available evidence in this order:

1. Exact external labels and regulatory filings.
2. USB/network/BLE/RF identifiers observed from the unit.
3. Normal read-only “about,” “version,” or status output.
4. Internal board/silicon markings if opening is separately allowed.
5. Vendor schematics, repair manuals, teardowns, and high-resolution photos
   matched by board revision.
6. Host-tool tables or public target registries as supporting evidence only.

Silicon markings and electrical connections are stronger MCU/radio evidence
than a similar product's host-tool dropdown. Record the main processor and
every output processor/chip: radio/baseband, Ethernet or Wi-Fi controller,
motor/servo driver, DAC/audio/video device, FPGA/CPLD, and safety controller.
Map each output front-end: RF band/PHY/antenna chain, network protocols and
egress, display/audio path, actuator/load, or another physical effect. Record
package markings, crystal frequencies, external flash/RAM, secure elements,
RF filters, power amplifiers, LNAs, antenna switches, isolation/level shifting,
and debug/test pads. Do not probe a pad until its voltage and role are
understood.

### 4.3 Bus, USB, and serial observation

On an isolated host, capture each already authorized state. Typical read-only
commands include:

```bash
lsusb
lsusb -v -d <vid:pid>
usb-devices
udevadm info --query=all --name=/dev/ttyACM0
dmesg --follow
stty -F /dev/ttyACM0 -a
```

On Windows, Device Manager, `Get-PnpDevice`, and USB descriptor viewers can
record the same facts. Capture VID/PID, manufacturer/product/serial strings,
device/config/interface descriptors, class/subclass/protocol, endpoints,
driver binding, port path, and enumeration timing.

For serial-looking interfaces, do not guess baud by transmitting probes.
Observe defaults, host-tool behavior, or passive boot output first. Treat DTR,
RTS, BREAK, and port open/close as potential reset or boot-mode controls.

Document visible buses and bridge devices—USB, PCIe, SDIO, Ethernet, CAN,
UART, I2C, SPI, SWD/JTAG, and vendor test headers—and distinguish observation
from electrical probing. Logic-analyzer attachment, active bus scanning, debug
attach, and writes are separately classified actions; do not treat an exposed
header as permission to enumerate it.

### 4.4 Network, Wi-Fi, BLE, and RF observation

Start passive:

- record interfaces, routes, addresses, DHCP/DNS/mDNS/SSDP, listening services,
  and normal connections on an isolated network;
- scan for advertised Wi-Fi SSID/BSSID/channel/security without joining;
- record BLE advertisements, service UUIDs exposed without a connection, and
  timing;
- use a receive-only SDR/spectrum analyzer to record occupied bands,
  bandwidth, duty cycle, burst timing, and correlation with known operator
  actions;
- make a target-off/control recording to distinguish the device from ambient
  traffic.

On the authorized isolated lab segment, useful host-side inventory includes:

```bash
ip link
ip addr
ip route
ip neigh
arp -an
nmap -sn <authorized-lab-subnet>   # -sP is the older alias
```

Record which results are local host state, passive neighbor discovery, or an
active host-discovery probe. `nmap -sn` can transmit ARP/ICMP and is an L2
read-only query rather than passive observation; limit it to the specifically
authorized lab subnet. For each
permitted network personality, capture interface/MAC, link type, addressing,
DHCP role, routes, names, services, and egress separately. Redact MACs and
device identifiers in tracked notes.

Joining, pairing, connecting, service scanning, and active RF probes are not
passive observations. Classify and gate them accordingly.

### 4.5 Software and firmware identity

Record every version surface and its evidence:

- enclosure/label version;
- USB descriptor and normal status output;
- boot banner, application “about” page, web UI, or mobile app;
- host-tool reported hardware, bootloader, radio, and application versions;
- file/version metadata from updater packages;
- source commit/tag and compile-time feature flags;
- firmware build ID, timestamp, target string, partition table, signing key ID,
  or reproducible-build hash;
- OS/kernel/RTOS, architecture, endianness, memory sizes, and filesystems.

Disagreements are findings, not annoyances. Preserve all values and determine
whether they refer to different components or whether one source is stale.

### 4.6 Interface map

For each interface, record:

| Field | Questions |
|---|---|
| Physical path | Connector/pin/bus/radio band/output front-end and electrical levels |
| State | What device state exposes it? How was that state reached? |
| Identity | VID/PID, service UUID, protocol, SSID, frequency, endpoint |
| Direction | Device→host, host→device, bidirectional, receive-only |
| Access | No auth, physical possession, password/key, pairing, signed request |
| Capability | Status read, config read/write, control, debug, update, recovery, output/egress |
| Persistence | Can exercising it change flash/config/fuses or boot state? |
| Game-day reachability | Wireless/remote, physical-only, host-side, unknown |
| Evidence | Photo/log/capture/document and evidence label |
| Current permission | L0–L5 and explicit prohibition if any |

**Danger points:** entering bootloader/recovery while “checking modes,” opening
a port that toggles reset lines, querying undocumented commands, joining a
device AP that stores credentials, and trusting a related model's chipset.

**Phase 1 output:** one-page system identity, component/version table,
state/personality table across bus/USB/network/radio modes, processor and
output-front-end map, generic interface map, architecture sketch, evidence
ledger, and open questions.

**Gate 1:** every material identity claim is labeled `OBSERVED`, `DOCUMENTED`,
or `HYPOTHESIS`; exact-unit facts are separated from family-level research.

---

## 5. Phase 2 — Research and acquire artifacts

**Goal:** collect the firmware, software, source, documentation, and prior work
needed for analysis without executing untrusted code or mislabeling a related
artifact as an exact match.

### 5.1 Research order

Search from authoritative to inferential:

1. Vendor product page, manual, release notes, support downloads, SDK, source
   obligations, security advisories, update instructions, and certificates.
2. Upstream source repositories, exact tags/commits, build manifests, target
   definitions, board files, CI artifacts, and dependency locks.
3. Silicon-vendor datasheets, reference manuals, errata, SDKs, boot-ROM and
   security documentation.
4. Regulatory filings and internal photographs tied to an exact model/region.
5. Package registries, CVE/NVD/vendor advisories, academic work, and conference
   material.
6. Community teardowns, forums, tool definitions, repair notes, and related
   device research—clearly labeled as secondary evidence.

For vulnerabilities, record whether the source is a vendor advisory, patch,
public PoC, reproduction report, or database summary. “Advisory says RCE” and
“public exploit reproduces on this build” are different claims.

### 5.2 Build a version and ancestry map

Create a table linking:

- product and board revisions;
- bootloader, main firmware, radio firmware, FPGA/CPLD bitstream, configuration,
  host app, driver, mobile app, and cloud API versions;
- source project/tag/commit and vendor patch set;
- release date, region/channel, feature flags, and hardware target;
- known predecessor/successor images and update compatibility;
- exact match, version match, related sample, or unknown relationship to the
  owned unit.

This avoids analyzing a current upstream tree when the unit contains a vendor
fork, or treating a host updater's version as the installed firmware version.

### 5.3 Acquisition channels

Use the least invasive channel that can answer the question:

| Channel | Handling |
|---|---|
| Public vendor/release artifact | Download, hash immediately, preserve response/source URL and release metadata |
| Source archive/repository | Pin exact commit/tag, record submodules/LFS, archive reproducibly |
| Host-tool cache/update package | Copy without launching when possible; preserve container and metadata before unpacking |
| Mobile package/driver | Acquire from official source; preserve signature/certificate and package version |
| Gated support portal | Record honest outcome and licensing limits; do not bypass access controls |
| Normal backup/export | Classify whether it changes state or exposes secrets; obtain the appropriate gate |
| Hardware debug/readout | Defer to a separately approved live plan; it may trip locks, contain secrets, or be destructive |
| Network/RF capture | Preserve raw timestamps and setup metadata privately; commit redacted/synthetic fixtures only |

“Unavailable,” “encrypted,” “requires vendor account,” “readout-protected,” and
“not attempted because prohibited” are valid acquisition outcomes. Do not fill
a gap with a falsely exact substitute.

### 5.4 Preserve before unpacking

For every original:

```bash
sha256sum <artifact>
stat <artifact>
file <artifact>
```

Record tool versions and exact commands. Make originals read-only locally and
unpack into `work/<device>/working/`. Prefer format-aware listing before
extraction (`unzip -l`, `tar -tvf`, container metadata). Defend against path
traversal, symlink escape, decompression bombs, scripts, and installers. Static
unpacking does not require executing the vendor tool.

Packed or self-extracting host tools may need staged static analysis. Identify
the packaging first: file-storage virtualization such as Enigma Virtual Box,
an installer such as NSIS or Inno Setup, an archive/SFX wrapper, or a true
executable packer need different extractors and imply different runtime
behavior. Obfuscation or a packer is not itself a vulnerability. Preserve the
packed original, hash every unpacked image, record the extractor/version and
command, and do not launch the package just to unpack it.

### 5.5 Match checks

Before calling an artifact relevant, compare target names, memory layout,
processor, board ID, flash size, partition scheme, radio part, build features,
signing metadata, version output, and release chronology. A valid image for a
sibling board can still brick the owned unit.

**Danger points:** executing downloaded utilities, opening untrusted projects
with active scripts/plugins, leaking device tokens to online analyzers,
overwriting an original, silently following “latest,” and choosing a firmware
target from marketing similarity.

**Phase 2 output:** artifact manifest, hashes, source/version ancestry map,
documentation index, exact-vs-related classification, and an acquisition gap
list.

**Gate 2:** each artifact has provenance, SHA-256, classification, version,
target, and hardware-match status. No downloaded code has been executed merely
to inspect it.

---

## 6. Phase 3 — Offline analysis

**Goal:** model interfaces and trust boundaries, produce falsifiable findings,
and build safe analysis tools before interacting with live hardware.

Run the tracks below in parallel when evidence allows. They converge in the
system attack-surface map.

### 6.1 Track A — Control, update, and output protocol reconstruction

For the control interface, its update/flash subprotocol, and each output
protocol or actuator command path, determine:

1. transport, electrical levels, endpoint roles, and direction;
2. framing, synchronization, escaping, length, type/opcode, sequence, and
   fragmentation;
3. byte order, integer widths, alignment, units, scaling, and encoding;
4. checksum/CRC/MAC/signature algorithm, initialization, coverage, and byte
   order;
5. state machine, timeouts, retry, replay, pairing/session establishment, and
   failure recovery;
6. which messages read, write, execute, update, reset, or enter another mode;
7. what is authenticated, encrypted, freshness-protected, or merely error
   checked.

Build a field table and state diagram. Keep unknown bytes unknown rather than
naming them from a guess. Use multiple captures and controlled differences to
locate fields.

Use complementary static and controlled dynamic evidence:

- statically trace constants, serializers/parsers, dispatch tables, update
  loops, checksum code, and host-tool UI handlers;
- where execution is separately approved, trace the host process in a
  disposable VM with tools such as `strace`/API tracing and restricted USB or
  network access;
- observe an authorized network exchange from a TAP, switch mirror, or host
  capture point rather than inserting an uncontrolled device into production;
- correlate each operator action with exact I/O while keeping the target's
  output section disabled or safely looped back;
- implement a native read-only reader after the request/response and side
  effects are understood, then compare it byte-for-byte with the host tool.

Dynamic host-tool execution is not part of artifact acquisition. Treat it as
its own controlled analysis plan, because the tool may write on connect,
auto-update, toggle control lines, or contact vendor services.

Deliver three independent checks where possible:

- a parser that rejects malformed/truncated input safely;
- a deterministic test vector for framing and CRC/checksum/signature coverage;
- an independent oracle: pinned vendor/upstream implementation, a separate
  library, or a recorded real exchange.

An encoder and decoder sharing the same wrong helper can agree perfectly. That
is `REPRODUCED-OFFLINE`, not independent validation.

### 6.2 Track B — Input-handling assessment

Ask these questions of **every parser and ingress**:

1. **Bounds and shape:** Are lengths, counts, indices, offsets, nesting depth,
   integer conversions, allocation sizes, and termination checked before use?
2. **Validation order:** Does the code parse, allocate, copy, decompress, or
   dispatch before CRC/MAC/signature/authentication? What work happens before
   rejection?
3. **State and authorization:** Which states accept the input? Is a privileged
   or state-changing opcode reachable before authentication or after a
   downgrade/replay?
4. **Read/reflection:** Can requests disclose memory, configuration, identity,
   secrets, logs, filesystem paths, or reflect attacker-controlled data?
5. **Write/update path:** What can persist? How are target identity, signature,
   version, rollback, chunk length/order, completion, and power loss handled?
6. **Errors and concurrency:** Do partial reads, timeouts, duplicate packets,
   races, retries, malformed Unicode, decompression, or resource exhaustion
   leave stale or dangerous state?
7. **Execution context and authority:** Is the parser bare-metal, ISR/task,
   bootloader, kernel/driver, privileged daemon, sandboxed process,
   browser/Electron, or cloud service? For a network server, does its process
   have the privileges, device nodes, capabilities, or IPC path needed to write
   flash/configuration or drive an output? What memory protection, privilege
   separation, and recovery exist?

Trace attacker-controlled bytes from ingress through checks to a sensitive
sink. “Uses `memcpy`” is a lead. “Remote length X reaches `memcpy(dst, src, X)`
before a bound on build hash Y” is a defensible static finding.

Flag dangerous surfaces prominently:

- unauthenticated control, debug, shell, file, memory-read/write, or update
  commands;
- unsigned update, target-check bypass, rollback, recovery downgrade, or
  incomplete signature coverage;
- hard-coded/shared/default credentials, private keys, tokens, or binding
  material;
- memory-unsafe parsing, integer wrap, deserialization, archive extraction,
  command construction, format strings, or unchecked DMA/shared buffers;
- trust in unauthenticated discovery, pairing, time, position, telemetry, or
  safety-state messages;
- secrets or sensitive configuration returned by a nominal status endpoint;
- fail-open behavior, watchdog/reset loops, resource exhaustion, and unsafe
  actuator defaults;
- host tools that run update content, shell commands, plugins, or elevated
  drivers from untrusted inputs.

### 6.3 Track C — Firmware and boot-chain analysis

Follow the detailed workflow in
[`firmware-to-poc-methodology.md`](firmware-to-poc-methodology.md). At minimum:

1. Identify container, compression, encryption, signatures, partitions, and
   embedded filesystems.
2. Establish architecture, endianness, load/base addresses, memory map, vector
   table, entry point, and execution regions using device evidence.
3. Index strings, symbols, imports, protocols, error text, build IDs, endpoints,
   task names, and update/debug references.
4. Map boot ROM assumptions, bootloader, verified-boot root, anti-rollback,
   recovery, application, radio/coprocessor, and configuration/calibration
   partitions.
5. Map every external input to parser, validation, dispatcher, state change,
   and sensitive sink.
6. Diff the closest pinned source/build/config against the artifact. Record
   vendor patches and compile-time feature selection.
7. Decompile only after the load model is justified; wrong architecture/base
   makes polished nonsense. Use REA when available and Ghidra/other tools as
   appropriate, preserving scripts and load recipes.
8. Produce candidates with exact binary addresses or source lines, artifact
   hash, preconditions, counter-evidence, and smallest safe reproduction.

Do not upload private firmware, configs, or captures to public analysis
services. Do not infer that a source path is installed without target/build
evidence.

### 6.4 Track D — Host, mobile, and cloud software

Treat companion software as its own target:

- inspect package signatures, manifests, dependencies, permissions, drivers,
  update endpoints, TLS behavior, local services, IPC, file associations, and
  auto-update logic;
- extract embedded firmware, target tables, protocol constants, resource files,
  web bundles, symbols, logs, and command strings statically;
- identify how USB control lines, network requests, firmware chunks, retries,
  and error handling work;
- search for hard-coded credentials, secrets, insecure storage, update
  verification gaps, unsafe archive extraction, command injection, DLL/search
  path issues, plugin loading, and privileged service boundaries;
- separate application-local risks from paths that can reach the physical
  target or game-day interface.

If dynamic execution later becomes necessary, use a disposable, instrumented
VM with snapshots, no personal secrets, restricted networking, and controlled
USB passthrough. Dynamic execution is a separate plan, not part of acquisition.

### 6.5 Track E — Radio and network surface

From passive captures and source/binaries, model:

- PHY, band/channel plan, modulation, coding, symbol rate, bandwidth, timing,
  hop sequence, synchronization, and direction;
- addressing/device identity, discovery, association/pairing, session setup,
  encryption/authentication, replay/freshness, CRC/FEC, retries, failsafe, and
  coexistence behavior;
- network addressing, trust-on-first-use, service discovery, web/API routes,
  protocol permissions, and credential lifecycle;
- what is reachable wirelessly on game day versus only through a wired bench
  or host updater.

Receive-only observation can characterize presence and timing; it cannot prove
packet acceptance, command authorization, or exploitability.

### 6.6 Findings and triage

Use one falsifiable claim per finding:

```markdown
### F-YYYY-NNN — concise title

- Status/evidence label:
- Exact target: model, revision, version, artifact SHA-256, match confidence
- Interface and required device state:
- Claim:
- Input-to-sink path:
- Security boundary crossed / effect:
- Preconditions and CTF-rule dependencies:
- Evidence and independent oracle:
- Counter-evidence / alternative explanation:
- Safe offline reproducer and negative control:
- Live validation gate, if ever allowed:
- Recovery and stop conditions:
- Result / limits / next test:
```

Prioritize by target match, wireless/game-day reachability, reliability,
required access, safety impact, recoverability, evidence maturity, and
connection to the actual scoring/flag condition—not only severity labels.

**Danger points:** executing the sample, treating strings as reachable code,
trusting an auto-detected architecture, using a related version as proof,
developing live actions before offline controls, or turning a static lead into
an exploitability claim.

**Phase 3 output:** system/attack-surface map, protocol specification and test
vectors, source/binary load recipe, host-app map, ranked findings, offline
harnesses, negative controls, and an explicit unknowns list.

**Gate 3:** important claims have a named artifact/capture and evidence label;
parsers and generators pass offline tests against an independent oracle where
available; live action is not required to call the offline phase complete.

---

## 7. Phase 4 — Read-only live validation

**Goal:** close identity and protocol gaps on the exact owned unit using only
the currently authorized read surfaces.

### 7.1 Preflight

Before connection:

- re-read the target card and current owner constraints;
- obtain the device/port handoff and verify no other process owns it;
- photograph wiring and verify voltage, levels, current limit, antenna/load,
  actuator safety, and RF-off posture; place the entire output section in its
  verified off/safe state, including RF, non-control network egress, motors,
  relays, speakers, lasers, and other physical outputs as applicable; if the
  control interface itself is networked, allow only that exact isolated path;
- record tool commit/hash, command, expected request bytes, expected response,
  timeout, and stop conditions;
- start private logging and identify fields that must be redacted;
- capture the target's before-state and normal baseline.

### 7.2 Validation sequence

1. Connect in the already identified normal/configuration state. Do not use
   reset/bootloader/update sequences unless separately authorized.
2. Observe enumeration and unsolicited output before transmitting a query.
3. Send the smallest documented read-only request using the native reader or a
   byte-for-byte understood equivalent.
4. Bound request count and retries. Stop after ambiguity rather than trying
   nearby opcodes.
5. Record raw response privately, a redacted transcript for Git, timestamps,
   and device/host state.
6. Repeat enough to distinguish a deterministic field from noise; use a
   device-off or known-response negative control when appropriate.
7. Disconnect cleanly, power down according to the baseline procedure, and
   verify that configuration/version/state did not change.

### 7.3 Honest limits

- Silence during a short serial observation does not disprove a console.
- No advertised service does not disprove a service in another state.
- A version string may identify the host protocol rather than every firmware
  component.
- A successful status/config read does not prove the write path or update path.
- A passive RF match establishes likely protocol/timing, not accepted control.
- Redaction must preserve structural evidence without exposing serials, UIDs,
  MAC addresses, keys, binding phrases, credentials, flags, or private
  captures.

**Danger points:** control-line reset, automatic host-tool update, accidental
write-on-connect, retries after partial state changes, multiple port owners,
and committing private identity/configuration.

**Phase 4 output:** exact-unit identity update, redacted transcript, raw private
evidence pointer, before/after comparison, contradictions, and updated
interface map.

**Gate 4:** only intended read-only effects occurred; identity or protocol
claims promoted to `VALIDATED-LIVE` name the exact unit/state/build and test
limits.

---

## 8. Phase 5 — Gated interaction

**Goal:** perform a bounded state-changing or active test only after the
offline case, authorization, recovery, and success criteria are reviewable.

This phase is unavailable for any action a current owner instruction
prohibits. For example, the E8's absolute constraint excludes configuration
writes, reset/bootloader entry, live fuzzing, RF transmission, and all firmware
operations regardless of how complete this phase's paperwork becomes.

### 8.1 Per-step approval record

Use one record per action:

```markdown
# Interaction approval — <ID>

- Exact target/unit/revision/build:
- Requested interaction level and interface:
- Exact action/command/message/file hash:
- Research question and expected observable:
- Why offline/read-only evidence is insufficient:
- Expected state change and persistence:
- Maximum payloads/retries/duration/rate/RF power:
- Bench isolation and safety controls:
- Backup/known-good image and verified recovery path:
- Negative control and abort conditions:
- Sensitive data that may be produced:
- Operator and hardware-access handoff:
- Owner approval, scope, and timestamp:
- Actual result, deviations, recovery, evidence location:
```

Any change to target, payload class, protocol, power, rate, duration, or effect
requires review again.

### 8.2 Escalation ladder

Proceed from the smallest reversible step. Each row is a separate gate:

| Step | Examples | Minimum preparation |
|---|---|---|
| 5A — Benign active query | Documented ping/status on an approved live interface | Exact bytes, bounded count, timeout, baseline, no persistent effect |
| 5B — Crafted nonpersistent input | Boundary/error-handling case, malformed frame, replay in an isolated harness/device | Offline reproduction, independent expected result, watchdog/recovery, one-variable-at-a-time |
| 5C — Configuration/state change | Pairing, binding, calibration, stored setting, mode change | Export/backup, exact rollback, before/after diff, owner sign-off per change |
| 5D — Firmware/memory readout | Normal backup, debug dump, flash read | Legal/ownership check, secrets plan, access-lock behavior, immutable original |
| 5E — Live output/transmit | Bench-looped actuator/network output; RF replay, injection, control, interference, or service-disruption test | Bench loopback or shielded/conducted isolated target, protocol-specific approval, legal/rules check, rate/power/time limit, physical failsafe |
| 5F — Update/flash/hardware change | Bootloader, erase/program/restore, fuse or debug setting, soldering | Explicit device authorization, verified exact image and recovery on spare, stable power, full backup, brick acceptance |

Do not combine ladder steps in one “test.” A firmware readout followed by a
patch and flash is three different decisions.

### 8.3 General execution rules

- Use positive, known-good commands before malformed or boundary cases.
- Change one variable at a time and preserve the exact transmitted bytes.
- Keep the device physically safe: props off, actuators restrained, emergency
  power disconnect ready, battery and RF conditions controlled.
- Bound automation in code and on the bench. No unbounded retries, scans,
  fuzzing, floods, or reconnect loops.
- Verify the selected interface and target immediately before execution.
- Watch current, temperature, resets, logs, RF, motion, and host errors.
- Stop at the first unexpected state. Do not “try recovery” unless the approved
  plan already defines it.
- Compare before/after firmware/configuration/state and record whether rollback
  actually restored the baseline.

### 8.4 Firmware update/flash special gate

Where flashing is explicitly permitted for a device, require all of the
following before the first byte is written:

- exact board/revision/region and target ID confirmed from hardware;
- exact image hash, provenance, signature, memory map, and compatibility;
- known-good original backup if technically and legally available;
- recovery path proven on a spare or equivalent unit—not merely documented;
- stable power, correct cable/probe, verified voltage/levels, and no automatic
  sleep/disconnect;
- understanding of bootloader, dual-bank/rollback, calibration, key/fuse,
  anti-rollback, and readout-protection consequences;
- acceptance that the unit may still become unrecoverable.

Never flash the only unit merely to learn its identity. Never use a related
model's image to “see if it works.” Never interpret an explicit no-flash rule
as a request to design a safer flashing procedure.

### 8.5 Live output and RF special gate

Where live output or transmission is explicitly permitted, aim it at a bench
loopback, simulator, dummy load, RF enclosure, conducted path, or physically
restrained owned test article—not open sky, production infrastructure, or a
live/third-party target. Specify the output protocol and load; for RF, also
specify band, frequency/channel plan, antenna/load, EIRP/power, bandwidth,
rate, duration, physical range, target identifiers, containment, and stop
signal. Ensure only the owned target or loopback can receive the test.

No live/third-party transmission, indiscriminate interference, or expansion
beyond competition rules. A receive-only result never silently authorizes TX.

### 8.6 Crafted-input and fuzzing special gate

- Start with the offline parser/emulator/reference build.
- Minimize to one field and one hypothesis.
- Establish known-good, negative, and malformed controls.
- Cap cases, length, rate, retry, and runtime.
- Monitor reset reason, watchdog, storage integrity, current, and state.
- Prefer a recoverable spare. Do not fuzz the only competition unit.
- Store exact case digests and private bytes so a result is reproducible.

**Danger points:** ambiguous approval, wrong unit/image, irreversible fuses,
write-on-connect tools, automatic retries, RF leakage, hidden persistence,
calibration/key loss, and improvising after a partial failure.

**Phase 5 output:** signed interaction record, exact inputs, logs/captures,
before/after state, recovery result, evidence-label change, and a decision to
stop, repeat, refine offline, or proceed to a separately approved next step.

**Gate 5:** the one approved action completed within its bounds and the device
is in a known state. No approval carries forward implicitly.

---

## 9. Phase 6 — Integrate and hand off

**Goal:** leave a durable, reviewable device package that another operator can
use without reconstructing the session.

### 9.1 Complete the device package

- `README.md`: what the device is, why it matters, current evidence level, and
  where to start.
- `TARGET-CARD.md`: current scope and hard boundaries.
- `INTERFACE-MAP.md`: all physical/logical/RF interfaces and states.
- `ARTIFACTS.md`: every artifact and provenance relationship.
- `EVIDENCE-LEDGER.md`: confirmed, contradicted, inconclusive, and hypothesis
  claims.
- `TEST-LOG.md`: redacted chronology and private evidence locations.
- `OPEN-QUESTIONS.md`: ranked unknowns, why they matter, and the smallest safe
  discriminating test.
- Sanitized fixtures and deterministic test vectors.
- Architecture, boot-chain, protocol/state, and attack-surface diagrams where
  they materially clarify relationships.

### 9.2 Integrate native tooling

Put reusable scripts in `tools/` with descriptive names and Python 3.10+
support where applicable. Each tool must document:

- exact purpose and supported model/protocol/version;
- read-only/default behavior;
- installation and dependencies;
- examples using synthetic identifiers;
- input/output format and log redaction;
- target/version checks;
- explicit action flags and bounds for any live effect;
- self-test, negative control, exit codes, timeout, stop, and recovery behavior;
- what the tool has actually validated versus what remains inferred.

Update the project surface when the device's protocol is one the project
tracks (including the RF/WaveRaider map when applicable). Link the device into
the firmware inventory and the firmware-to-PoC methodology rather than
duplicating detailed binary analysis.

### 9.3 Review and repository hygiene

- Read all changed files fresh and check links/commands.
- Run formatting, syntax, unit, self-test, and fixture checks appropriate to
  the changes.
- Search tracked changes for credentials, UIDs, serial numbers, flags, private
  captures, and large binaries.
- Verify every committed binary has documented provenance and a recorded hash.
- Keep extracted source trees, analyzer projects, raw captures, and private
  logs ignored.
- Update `README.md`, current session notes, and open-work status.
- Commit a cohesive change and push it so the other agent receives the same
  evidence and constraints.

### 9.4 Operator handoff

For a capability that may be used in a competition, create a timed playbook
only after validation supports it. The handoff must state:

- exact supported target/build/interface;
- equipment, cables, antennas, drivers, and versions;
- setup and positive identification steps;
- dry-run/self-test and expected output;
- authorized live command and required flags;
- bounds, stop conditions, and physical failsafe;
- success evidence and flag/scoring connection;
- known failure modes, disproven paths, and recovery;
- private data that must not enter logs or commits.

**Danger points:** leaving an unlabeled “working” script, publishing private
captures, stale safety instructions, claiming a related build as exact, and
writing a playbook from offline-only evidence.

**Phase 6 output:** complete per-device package, reviewed tools, cross-links,
session handoff, open-item owner, and—only when justified—a timed operator
playbook.

**Gate 6 / definition of done:** another teammate can determine the exact
device and artifact identities, reproduce the offline work, understand every
live action and prohibition, find the raw private evidence, run the safe
default tools, and identify the next authorized step without oral context.

---

## 10. Technology-specific branches

Apply these after the common phases; they do not replace them.

### 10.1 Bare-metal MCU / RTOS appliance

- Identify MCU marking, package, flash/RAM, external memory, oscillator,
  voltage, boot pins, SWD/JTAG/UART/test pads, and readout protection.
- Establish vector table, memory map, interrupts, tasks, DMA, watchdog, heap,
  persistent storage, and peripheral ownership.
- Pay special attention to ISR/task races, unchecked DMA lengths, ring buffers,
  parser state across packets, stack size, watchdog recovery, and unsafe
  bootloader/application handoff.
- Treat debug attachment and protection reads as gated; some operations can
  mass-erase or permanently change protection.

### 10.2 Linux/Android/network appliance

- Identify bootloader, kernel, init, root filesystem, partitions, packages,
  services, containers, MAC/sandbox policy, users, capabilities, firewall,
  update agent, web UI, and cloud dependencies.
- Review exposed services, default/derived credentials, command construction,
  upload/extraction, path traversal, deserialization, local privilege
  boundaries, secrets at rest, update signature/rollback, and recovery.
- Preserve filesystem metadata and symlinks when extracting. Never boot an
  unknown image on a trusted network.

### 10.3 FPGA/CPLD/radio coprocessor

- Separate bitstream, soft-core firmware, main MCU firmware, calibration,
  modem tables, and host driver.
- Record device family, configuration mode, bitstream encryption/signing,
  external flash, JTAG chain, clock/reset, and inter-processor protocol.
- Analyze register interfaces, DMA/shared memory, trust boundaries, update
  coverage, and version compatibility.

### 10.4 Wi-Fi/BLE/mobile ecosystem

- Record chipset/firmware, advertised identity, pairing/bonding, Wi-Fi security,
  provisioning, stored networks, service permissions, app package/signature,
  local API, cloud API, and update channel.
- Inspect authentication, session/token lifecycle, certificate validation,
  downgrade/replay, unauthenticated configuration, cross-origin/web UI,
  characteristic permissions, and secrets in logs/backups.
- Passive advertisement observation is L1; association, pairing, GATT reads,
  joining an AP, and API requests may be L2/L3 and must be classified.

### 10.5 RF-control system

- Identify both endpoints, RF IC/front-end, antenna, band, modulation, timing,
  channel/hop plan, pairing identity, packet classes, integrity/authentication,
  telemetry, failsafe, and regulatory limits.
- Keep passive IQ originals private with sample rate, center frequency, gain,
  clock source, antenna, distance, timestamps, target actions, and control
  recordings.
- Prove synchronization/framing/checksum offline before proposing TX. Packet
  synthesis, replay, takeover, interference, and jamming are separate active
  gates.

### 10.6 Host updater or configuration tool

- Preserve installer/package signature and exact version.
- Inventory drivers, helper services, bundled runtimes, elevated components,
  target definitions, firmware resources, update URLs, logs, and USB/network
  behavior.
- Static-unpack first. If packed, preserve the original and document each
  unpacking layer. Dynamic analysis belongs in a disposable isolated VM.
- Treat buttons named Connect, Read, Write, Update, Restore, and Recovery by
  their observed protocol effects, not their labels.

---

## 11. Stop conditions and the standing never list

Stop and return to the last safe phase when identity, target match,
authorization, expected effect, recovery, or physical safety becomes
ambiguous.

Never:

- test non-owned or unauthorized targets;
- transmit into live or third-party environments outside explicit scope;
- use the blueprint to broaden a device-specific boundary;
- flash, erase, reset, write, pair, bind, fuzz, or transmit because a tool has
  the capability or an old note suggested it;
- infer hardware from a product family, host-tool target list, or sibling
  device when direct evidence can decide it;
- power an unknown connector before verifying voltage, polarity, ground, and
  current limit;
- connect hazardous motion hardware with propellers/loads active during basic
  characterization;
- run newly downloaded software merely to inspect it;
- upload private artifacts or captures to public services;
- commit secrets, credentials, tokens, UIDs, binding phrases, serials, flags,
  or private captures;
- conflate host tool, update package, bootloader, application, radio firmware,
  source tree, and installed image;
- overwrite originals or omit provenance and SHA-256;
- call a static lead a confirmed vulnerability or an offline round-trip a live
  capability;
- brick the only copy without explicit authorization, a proven recovery path,
  and acceptance of loss;
- let two processes/operators share a hardware port or infer handoff from port
  availability;
- improvise a recovery write after an unexpected result.

---

## 12. End-to-end deliverables matrix

| Phase | Required deliverable | Exit question |
|---|---|---|
| 0 — Intake | Target card, inventory, bench plan, boundaries | Do we know what we may safely do? |
| 1 — Identify | Identity sheet, component/state table, interface map | Do we know what this exact unit exposes and what remains hypothesis? |
| 2 — Research/acquire | Provenance manifest, hashes, version ancestry, gap list | Do we know exactly what every artifact is and how well it matches? |
| 3 — Analyze | Surface map, protocol model, load recipe, findings, offline controls | Can another analyst reproduce each technical claim without touching the device? |
| 4 — Read-only validate | Redacted transcript, private raw evidence, before/after state | Did the exact unit confirm the intended facts without changing state? |
| 5 — Gated interaction | Per-action approval, bounded execution record, recovery result | Did one explicitly approved action stay inside its limits? |
| 6 — Integrate | Complete device package, reviewed tools, handoff/open items | Can another operator continue safely from the repository alone? |

The process is deliberately asymmetric: phases 0–4 can produce a valuable,
complete characterization even when phase 5 is prohibited. “Not attempted
because prohibited” is a sound result. The strongest research package is the
one that makes both its capabilities and its boundaries unmistakable.
