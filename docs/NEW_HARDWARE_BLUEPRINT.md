# NEW-HARDWARE BLUEPRINT — onboarding a new device into the project

**Purpose.** A reusable, step-by-step playbook: **"If we get a new piece of
hardware, this is what we do."** Apply it to *anything* new we want to test on
— a controller, a receiver, a flight controller, a video link, a telemetry
radio, a ground-station box, a sensor, an IoT gadget, a network appliance, a
PC, a phone, a chip. The method is the same regardless of the device class.

**Scope (read first).** This is for **hardware we own, on the bench, in our
own space** (AGENTS.md §0). Every step defaults to **read-only / passive /
offline** until a step explicitly says a *write* or *transmit* (or other
state change) is in play — and any such step requires **explicit operator
sign-off in that specific change** (AGENTS.md §2, SECURITY.md). The blueprint
is a *method*, not a license: it tells you *how to proceed carefully*; it does
not waive the boundary.

**Standing preconditions (always true before any phase — not a step).**
These are the frame the whole blueprint assumes; they aren't done "in a
phase," they're the condition for starting at all:
- **Ownership + authorization** — the device is ours, on the bench, in our
  space (AGENTS.md §0). Without it, don't start.
- **Bench isolation** — no live system above the bench, no third-party link in
  range we'd act on, and the device's *output* side (a radio transmitter, a
  network egress, an actuator) is **off / disconnected / disabled** until an
  output step is explicitly signed off. For a device with a transmission or
  network front-end, keep it passive (no power to the output section, a
  loopback, a shielded box, no egress cable, or no supply to the output
  stage).

**The three standing rules that apply at every step** (from AGENTS.md):
1. **Honesty contract** — label every finding VALIDATED / SELF-CONSISTENT /
   SYNTHETIC. "I think it's X" is not a finding; "X, confirmed by <evidence>"
   is.
2. **Measure, don't assume** — read the device's actual state (descriptors,
   measured values, live bytes), never the nominal/expected value.
3. **Code + notes in the same commit** — each step that produces a tracked
   artifact ships with its notes entry. Each agent writes only their own
   `..._NOTES_FOR_BOTH.md`.

**The output of a completed onboarding:** a per-device folder under
`references/<device>_material/` with a `manifest.json` (provenance + hashes),
a `README.md` (what's confirmed vs unproven), the device's identified
interfaces, and a one-page **interface map** (what talks to what, at what
rate, in what format). Plus any native tooling built against the confirmed
read-only surfaces.

---

---

## Phase 1 — Identify the device (hardware, software, firmware)

**Goal:** establish *what the device actually is* — its processor/board, its
firmware or software, its OS (if any), its interfaces — from evidence, not
from the box label or the marketing page. This phase is all observation +
passive inspection.

### 1a. Intake record (the first thing, before any probing)

- **Physical inventory.** Photograph the device (top, bottom, connectors,
  board if visible). List every physical interface: USB (type + count), UART
  pads, debug/programming headers (SWD/JTAG/UART), network ports
  (RJ45/USB-Ethernet/Bluetooth/Wi-Fi antenna), RF connectors, buttons, LEDs,
  battery contacts, storage (SD/eMMC/USB). Note the power requirements
  (voltage, current, connector polarity).
  **Danger note:** do not power a device whose polarity/supply you haven't
  confirmed — a reversed supply or an over-voltage is the most common way to
  damage an owned device before we've learned anything from it.
- **Create the per-device folder.** `references/<device>_material/` with a
  `README.md` (stub: device name, intake date, owner, a "confirmed vs
  unproven" table to fill in) + a `manifest.json` (empty artifact list) + a
  `.gitignore` that excludes `downloads/` + `upstream/` (binaries/firmware/
  images stay out of git; provenance + notes are tracked).
- **Write the intake entry** in the orchestrator's notes: device intake,
  what's in scope, the first open questions.

### 1b. Hardware identification

- **The bus/descriptor signals (the first, cheapest evidence).** Connect the
  device (with the output section off if separable) and observe what the host
  sees:
  ```sh
  lsusb -v                        # full USB descriptor tree (if a USB device)
  lsusb -t                        # the bus topology
  dmesg | tail -40                # what the kernel/OS logged on connect
  ip link / ifconfig -a           # network interfaces it claims (if a network device)
  # on a network device:
  arp -a                          # its MAC, if it answers
  nmap -sP <ip>                    # presence + open ports (passive-ish, own bench)
  ```
  Record the identifying descriptor(s) for **each** personality/interface the
  device shows.
  **Key technique — enumerate ALL personalities/modes:** many devices change
  their identifier across modes (a USB device that shows one ID in normal mode
  and another in a download/update/serial mode; a network device that exposes
  a setup-AP vs a normal-AP). Power-cycle the device into each documented mode
  (check the manual for the mode-switch procedure — e.g. "hold button X while
  powering on") and record each ID. **A device with multiple identifiers is
  telling you it has multiple software modes** — that's a map of its
  interfaces.
  **Danger/reading note:** a *generic* descriptor (a template vendor string,
  a stock bootloader ID) usually means an **unbranded processor bootloader
  stub** — the vendor hasn't customized it. That's a strong hint about the
  processor family + that the "download mode" is a **bootloader**, not the app.

- **Board markings (the ground truth for the processor).** If the board is
  exposed (or the device has a service opening), photograph the markings: the
  processor part number (e.g. a Cortex-M part, an ESP32, an x86 SoC), the
  radio/wireless chip(s), the flash/memory chips, any board revision string.
  **Do not infer the processor or OS from the vendor's host tool, the
  marketing page, or the packaging** — read the marking. (A Windows host tool
  that ships with the device says nothing about what runs *on* the device.)

- **The output front-end(s).** Identify the transmission/network/actuator
  hardware + its interface (SPI/UART/PCIe/GPIO, or the network stack) from the
  markings or a schematic. Note the band(s)/protocol(s) + the connector(s).
  This tells you what the device *puts out into the world* and through what
  medium — the input to the output-side analysis later. (For an RF device:
  the band + PHY. For a network device: the protocols + the egress path.)

- **Decomposition (only if needed + only with care).** If the board isn't
  visible and the markings are needed, the operator may open the enclosure.
  **Danger note:** note antenna locations (RF exposure), battery type + how to
  isolate it, and any tamper switches. Photograph before removing anything.
  This is a physical-modification step — it's on *owned* hardware, but record
  it.

### 1c. Software / firmware / OS identification

- **The host tool / management software (if the vendor ships one).** If the
  device has a vendor configurator/host/management tool (a `.exe`, a `.app`,
  a web dashboard, a CLI), acquire it (Phase 2) and inspect it for: the
  command surface, the transport (serial/network/USB) + its config, the
  firmware/images it references, and the device modes it drives.
  **Critical distinction:** the *vendor's host/management tool is NOT the
  device's firmware.* A `SHELL32.dll` in a Windows `.exe` is a host dependency,
  not evidence of a shell on the device. Never conflate them.

- **The firmware / software image (the long pole).** Determine whether the
  device's firmware/software is:
  - **Publicly downloadable** (vendor release page, a firmware server, an
    open-source project's release, a package repo) → acquire it (Phase 2).
  - **Update-via-host-tool** (the configurator/manager flashes it) → the image
    is what the tool sends; capture it or find the tool's bundled image.
  - **Not publicly available** (vendor-gated) → it must be **read from the
    device** (a bootloader read / flash dump) or **requested from the vendor**.
    This is often the blocking item; flag it early.
  Record which case applies + the exact revision (version string, build date,
  commit/tag) if known.

- **The OS question (honest scoping).** Determine (or flag as unproven) whether
  the device runs bare-metal firmware, an RTOS (look for the markers in the
  firmware binary), or a general-purpose OS (a filesystem, `/dev`, an init
  system, a shell). **Do not assume.** The honest answer format is "no shell
  or filesystem interface *established*" — not "the device has no OS." Record
  what's evidenced vs what's unproven (see 1f).

### 1d. The interface map (the key deliverable of Phase 1)

Produce a one-page **interface map** for the device — the thing every later
phase builds on:

```
DEVICE: <name> (intake <date>, owner <who>)
Board:   <processor marking> + <wireless/network chip> + <board rev>
Power:   <V, connector, polarity>

Identifiers / personalities:
  - <descriptor> "<product string>"  mode=<normal/download/serial/update>  class=<...>
  - <...>

Control interfaces (how we talk to it):
  - <USB/UART/serial>  config=<baud/...>  format=<binary-protocol/line/JSON/...>
  - <network>  <protocol/port>  auth=<...>
  - command surface: <Read/Write/Update/...> (from host tool or probing)

Output front-end(s) (how it talks to the world):
  - <RF: band(s) + PHY + role + connector>
  - <network: protocols + egress>
  - <actuator: type + interface>

Firmware / software:
  - source=<public/downloadable/host-tool/gated>  revision=<...>
  - OS/RTOS=<bare-metal/RTOS-name/OS-name/unproven>
  - update mechanism=<DFU/serial-flash/network-OTA/none-known>

CONFIRMED:   <list, each with its evidence>
UNPROVEN:    <list, each with "why it's not yet confirmed">
```

### 1e. Danger notes (Phase 1)

- **Power polarity/voltage** — the #1 damage risk (1a).
- **Output exposure** — keep the transmitter/egress/actuator off/disabled
  during hardware ID (the bench-isolation precondition, 1b). Don't have a live output radiating or
  connecting while you're unplugging things.
- **Don't infer the processor/OS from the host tool** (1b, 1c).
- **Don't write anything to "test" the update path** — that's a write step,
  Phase 5, needs sign-off. In Phase 1 you *identify* the update mechanism, you
  don't *use* it.

### 1f. The CONFIRMED / UNPROVEN table (the honesty contract, applied)

For every claim in the interface map, mark it:
- **VALIDATED** — confirmed by direct evidence (a descriptor read, a board
  marking photo, a live byte capture, a firmware string).
- **SELF-CONSISTENT** — round-trips our own reasoning but not yet proven
  against the device.
- **SYNTHETIC / UNPROVEN** — inferred, or a hypothesis flagged as such.
The model is the "unproven" list: the processor model, board revision,
app/bootloader memory layout, bare-metal vs RTOS vs OS, presence of a
filesystem, the debug-pinout, the readout-protection state — all listed as
**unproven** until a matching marking/schematic/firmware establishes them.

**Gate 1→2:** interface map complete (identifiers across all modes, control
interface config, output front-end, firmware source + revision, OS/RTOS
status), every claim labeled CONFIRMED/UNPROVEN. The device is still
unmodified (except possibly a careful, recorded enclosure opening).

---

## Phase 2 — Research + acquire reference material (download copies for analysis)

**Goal:** gather everything we can analyze *offline* — the firmware, the host
tool, the upstream source, the documentation, the board files — with **full
provenance**, so the analysis in Phase 3 is reproducible and auditable.

**The standing acquisition discipline:**
- `downloads/` = vendor docs + host tools + device firmware/images (when their
  origin + target are identified). **Untracked** (gitignored) — binaries don't
  go in git.
- `upstream/` = official source/hardware-definition checkouts for comparison.
  **Untracked.**
- `manifest.json` = **tracked** — for every artifact: source URL, final URL,
  SHA-256, size, classification (vendor_support_page / vendor_linked_manual /
  host_tool_NOT_device_firmware / upstream_source / device_firmware / doc),
  revision identifier (commit/version/build date).
- `README.md` = **tracked** — what's confirmed vs unproven, the acquisition
  results, the honest gaps.
- **Never execute a downloaded executable or build script** during
  acquisition. Acquire + hash + catalog; *analysis* (Phase 3) is a separate,
  deliberate step. (If a binary must be run to understand it, that's a
  sandboxed analysis step in Phase 3, not an acquisition step.)

### 2a. Firmware / software image acquisition (the priority)

Try, in order, recording which one works in the manifest:
1. **Vendor public release** — the vendor's downloads/firmware page. Record
   the exact URL + the image's revision. **Verify it's for THIS device**
   (model + variant — sibling models/variants must not be conflated; a
   "similar" image for a sibling is not the target).
2. **Firmware server / OTA endpoint** — if the device pulls firmware from a
   server, find the exact URL the device requests (from the host tool, or a
   passive capture of the device updating) + the image it returns.
3. **Host-tool-bundled image** — if the configurator/manager flashes the
   device, the image is either bundled in the tool's resources or is what the
   tool downloads on demand. Extract it from the tool (static — don't run the
   tool to get it if you can avoid it) or find the tool's download URL.
4. **Read from the device (a write-adjacent step — sign-off).** If the
   firmware is gated, the only way to get it is a **bootloader read / flash
   dump** off the device. This requires knowing the bootloader's read command
   (which comes from Phase 3's analysis of the host tool's update path) and is
   a *device interaction* — it's on owned hardware but it's not a pure read of
   the *interface*; flag it + get sign-off before doing it.
5. **Vendor request** — as a parallel track (slow turnaround), request the
   image from the vendor's support.

**Record the outcome honestly.** "Not obtained; no verified download found in
the checked vendor pages or upstream targets" is a *valid* outcome, recorded
as such. "We looked here, here, here; here's what we found; here's what's
still missing" is the honest format.

### 2b. Host tool / management software acquisition

Acquire the vendor's configurator/host/management tool. If it's a **packed**
executable (a file-storage packer like Enigma Virtual Box, NSIS, Inno Setup —
which bundle the real binary + resources but are *not* code encryption),
**unpack it first** so Phase 3 analyzes the *inner* plaintext binary, not the
packer wrapper.
**The measurement lesson:** measure the *inner* binary's code-section entropy,
not the packer's data section — measuring the wrong artifact is how a
"plaintext binary" gets misread as "encrypted." Record: the packer type (if
any), the inner binary's size + hash, the unpack tool + version.

### 2c. Upstream source acquisition

If the device's firmware is a fork of an open-source project, clone the
**upstream source at the matching revision** for comparison:
```sh
git clone <upstream-url> upstream/<project>
cd upstream/<project> && git checkout <matching-commit-or-tag>
```
Record the commit in the manifest. **Verify the device's target is actually in
upstream** (search the upstream build config + target files). If the target
isn't in upstream, the firmware is vendor-forked and you need the vendor image
(2a) — record that.

### 2d. Documentation + board files

Acquire: the user manual, the service/repair manual, any schematic or board
files the vendor publishes, the support articles that describe the device's
modes + update procedure. Record each with its URL + hash. The manual is where
the **mode-switch procedures** live (how to get the device into
download/update/serial mode) — that's Phase 1b's input.

### 2e. Danger notes (Phase 2)

- **Provenance is mandatory** — no artifact enters `downloads/` without a
  manifest entry (URL + hash + classification). An un-provenanced binary is a
  liability, not a reference.
- **Verify the artifact matches the target device** (model + variant) before
  treating it as "the firmware." A sibling's image is a *comparison
  reference*, not the target — classify it as such.
- **Don't run downloaded code during acquisition** (2-discipline). Hash +
  catalog now; analyze (and only then run, in a sandbox, if at all) in Phase 3.
- **Packed executable → unpack before analysis** (2b) — don't waste Phase 3 on
  the packer wrapper.

**Gate 2→3:** manifest.json complete (every artifact: URL + hash +
classification + revision), README.md states what's confirmed vs unproven +
the honest gaps, the firmware status is recorded (obtained + revision, or the
exact reason it's not). No downloaded code executed.

---

## Phase 3 — Analysis (what to look for, where the risk is)

**Goal:** analyze the acquired material **offline** to (a) understand the
device's interfaces + protocols, and (b) identify the **input-handling
surfaces** — the places where the device parses untrusted/external input,
because that's where the interesting behavior lives. This phase is read-only
on the *device* (we're analyzing files, not talking to the device) — though it
may *inform* later device-interaction steps.

**The two analysis tracks, run in parallel:**
- **Track A — Protocol/interface analysis** (understand how it talks).
- **Track B — Input-handling assessment** (find where it parses input + how
  robustly).

### 3a. Track A: protocol + interface analysis

- **The control protocol.** If the device has a binary/network control
  protocol, decode it:
  - **Static** (from the host tool's disassembly or source): the command
    surface (the command/op names), the frame/message layout (header, payload,
    checksum), the transport config (baud, ports, framing), the per-command
    buffer construction.
  - **Dynamic** (a live capture): drive the device through its operations and
    capture the actual bytes — a `strace -f -e trace=write/read` on the host
    process (for a host-tool-driven protocol, which reads the syscalls
    directly and avoids port contention), or a TAP/mirror on the control link
    (for a network protocol). The live bytes are ground truth to validate the
    static decode against.
  - **The checksum/CRC scheme** — identify the exact checksum (poly, init,
    reflect, input range, or the hash for a network protocol) + verify it with
    a known test vector.
- **The update/flash protocol.** Decode the device's firmware-update path:
  the sync/entry sequence, the command set, the chunk size + framing, the
  per-chunk ACK, the finalization. This is the highest-value protocol to
  understand (it's the device's most privileged input path).
- **The output protocol (if the device has one).** Identify the protocol the
  device puts out into the world (the RF air protocol, the network protocol) +
  its frame format. This feeds the project's main mission (detect/identify
  what's in the air/network) when the output is a protocol the project tracks.
- **Build the native reader/writer (read-only first).** Once the protocol is
  decoded, build a minimal native client (no vendor tool) that exercises the
  **read-only** surface. **Read-only until the parser is fully understood + a
  human approves a specific write** (SECURITY.md §4). The reader is both a
  validation of the protocol decode and the seed of the device's native
  tooling.

### 3b. Track B: input-handling assessment (the "what to look for")

This is where you find the interesting behavior. For **each interface the
device parses input on** (the control protocol, the update path, the network
server, the debug console, the output receive path, any file/format parser),
assess:

**The input-handling questions (ask these of every parser):**
1. **Bounds-checking.** Does the parser validate the length/size of incoming
   data *before* indexing into it? (A length field trusted without a clamp is
   the classic bug.)
2. **The integrity gate.** Is the checksum/signature verified *before* the
   payload is acted on, or after? (A payload processed before the integrity
   check is a reflected-read / state-change surface.)
3. **Reflected reads.** Does any response echo attacker-controlled bytes back?
   (A reflected read is the first rung of an input-handling finding.)
4. **State-changing writes.** Which inputs change the device's persistent
   state (flash, config, credentials, keys)? Are those gated behind the
   integrity check + an explicit command, or reachable by a malformed message?
5. **The update path's validation.** Does the bootloader verify a
   signature/CRC over the *whole* image before committing it, or does it
   stream chunks as they arrive? (No whole-image check = the update path
   accepts whatever the host sends — the highest-value input-handling
   surface.)
6. **Error handling.** What happens on a malformed/short/bad-checksum message
   — does the parser fail closed (drop + reset) or does it leave the device in
   a partially-updated state?
7. **The execution context.** If the device runs an RTOS, are there shared
   buffers between the parser task and other tasks (a race window)? If
   bare-metal, is there a stack that a deep recursive parse could overflow?
   If it's a network server, is the parser in a process with the privileges to
   write flash/config?

**Point the dangerous stuff (label it, don't act on it in this phase):**
For each input-handling surface, record:
- **The surface** (which parser, which interface).
- **The input that reaches it** (the bytes, the framing).
- **What's validated** (the checks that ARE present).
- **What's NOT validated** (the gaps — the length check, the whole-image
  checksum, the bounds check).
- **The evidence class** (VALIDATED from the disassembly / SELF-CONSISTENT /
  SYNTHETIC hypothesis).
- **The risk, stated technically** (e.g. "the update path commits chunks as
  they arrive; no whole-image signature check is present in the bootloader —
  unconfirmed, needs the firmware binary to verify").

**The honesty rule for this phase:** you are *assessing input handling*, not
*exploiting it*. You document what the parser does + where it's thin. You do
**not** craft a malformed input to trigger a finding in this phase — that's a
later, explicitly-signed step (Phase 5). "The parser doesn't clamp the length
(per the disassembly at 0x...)" is the finding; "here's an input that breaks
it" is Phase 5.

### 3c. The firmware / software binary analysis (when the image is in hand)

If Phase 2 obtained the firmware/software image:
- **Recover symbols** (if the image has them; if not, the strings + the
  disassembly). Identify the processor (from the vector table / the linker
  script / the ELF header), the OS/RTOS (the markers, the heap/stack layout),
  the memory layout (app region, bootloader region, config region, the
  credential/key storage).
- **The mitigation audit** (for the input-handling surfaces): stack
  canaries, RELRO, ASLR (none on a no-MMU processor — flag that), the stack
  size, the MPU/permission configuration (if any), the sandbox/privilege
  separation (if it's an OS).
- **The input-handling surfaces in the binary** (Track B, now on the real
  image): the control parser, the update handler, the network server, the
  output receive handler — disassemble + assess per 3b.
- **The console/filesystem question** (if still open from Phase 1): look for
  a debug-console init, a command table (a shell), a filesystem (the FS
  markers), a remote-shell service (a daemon). The honest answer may be "still
  unproven" — the binary is what would settle it.

### 3d. The deliverables of Phase 3

- The **protocol doc** (the control/update/output protocols, decoded +
  validated, with the evidence class).
- The **input-handling assessment** (every surface: what's validated, what's
  not, the risk, the evidence class).
- The **native reader** (the read-only client, if the protocol is decoded).
- The **binary analysis notes** (the processor, OS/RTOS, memory layout,
  mitigations, the console/filesystem answer — or "still unproven").
- The updated **interface map** (Phase 1d, now with the decoded protocols +
  the input-handling surfaces).

### 3e. Danger notes (Phase 3)

- **This phase is offline + read-only on the device.** We analyze files; we
  don't talk to the device (except the read-only native reader, which is a
  *query*, not a state change). Any step that would *change* the device is
  Phase 5 + sign-off.
- **Label every input-handling finding with its evidence class.** "The
  disassembly shows no length clamp" (VALIDATED) is different from "I think
  the parser might not clamp" (SYNTHETIC). Don't overclaim.
- **Don't act on the findings in this phase.** Document the thin spots; don't
  craft the triggering input. That's the line between "input-handling
  assessment" (this phase) and acting on it (Phase 5, signed).
- **The firmware binary is the long pole** — if Phase 2 didn't get it, a lot
  of Phase 3 is "the host tool tells us X, but the firmware would confirm
  it." Flag every such "host-tool-inferred, firmware-unconfirmed" item.

**Gate 3→4:** protocol doc + input-handling assessment + (if possible) the
native reader + (if the firmware is in hand) the binary analysis notes are
complete, every finding labeled with its evidence class, the interface map
updated. The device is still unmodified.

---

## Phase 4 — Validate on the device (the read-only live check)

**Goal:** confirm the offline analysis against the **live device** — the
read-only surfaces, on the bench, with the device connected. This is where
"SELF-CONSISTENT" becomes "VALIDATED on the owned device." Still read-only /
query-only; no state changes.

4.1. **Connect the device** in the relevant mode (from Phase 1b's mode map),
with the output section off (if the validation is on the control interface,
not the output). Confirm the descriptors/interfaces match Phase 1's records —
a mismatch is a finding (the device isn't in the mode you think).

4.2. **Exercise the read-only surfaces** with the native reader (Phase 3d).
Send the read/query commands; record the live bytes + the evidence class
(VALIDATED on the owned device).

4.3. **Watch for unsolicited output.** During the connection, observe whether
the device emits anything unprompted (a boot banner, a debug log, a console
prompt, a heartbeat). Record what you saw + the honest limit — silence during
a brief observation confirms "no unsolicited output *in this window*," not
"the device never emits anything."

4.4. **Redact the identity.** If the read returns the device's serial/UID/MAC/
key, **redact it from the tool output + the notes** (SECURITY.md §1). The wire
bytes can be in a captured frame for cross-check; the value is not hardcoded
or logged.

4.5. **Close the connection cleanly.** Disconnect, confirm the device returns
to its normal state. Record the session.

**Danger notes (Phase 4):**
- **Query-only.** The live check sends *read* commands (the request half of a
  query/response). It does not send a state-changing command (a config write,
  a flash, a credential change). That's Phase 5.
- **Output off** (unless the validation is specifically the output surface,
  which needs its own sign-off + bench isolation).
- **Redact the identity** (4.4) — the device's serial/key is a secret.
- **One process at a time** on the control link (a second reader on a
  point-to-point link gets nothing; use the host-process syscall capture
  instead, which reads the syscalls directly).

**Gate 4→5:** the read-only surfaces are VALIDATED on the live device (the
offline analysis is confirmed), the identity is redacted, the device is
returned to its normal state. The device's state is unchanged (only reads).

---

## Phase 5 — Gated interaction (writes + output — sign-off required)

**Goal:** if/when the objective requires *changing* the device (a config
write, a firmware update, a live output/transmit, a crafted-input test), do it
**gated, bench-only, with explicit operator sign-off in that specific
change.** This is the only phase that modifies the device or drives its
output. Every step here is individually signed off; there is no blanket
authorization.

**The standing gate (AGENTS.md §2, SECURITY.md):**
- **Bench/owned only.** The device is ours, on the bench, in our space — no
  live/in-the-air/third-party target in range.
- **Explicit human sign-off in that specific change** — never an agent's own
  initiative, never justified by a downstream goal.
- **A real gate mechanism** (not a self-asserted flag): an out-of-band human
  sign-off + hard bench-only checks + a log. No output/state-change fires
  before this lands.
- **Benign, reversible operations first** (e.g. a read-after-write of a config
  value that can be set back, a loopback test, a positive command) — not a
  destructive one.
- **Each new surface/protocol gets its own sign-off + bench-only
  justification** (signing off one interface's write doesn't authorize
  another's).

**The gated steps (each individually signed off):**

5.1. **A config/state write** (set a value the device stores). Signed off,
bench-only. The native writer sends the write message; verify the change with
a subsequent read (read-after-write verification). Log the before/after.

5.2. **A firmware read/dump** (if Phase 2 couldn't get the firmware and the
update/bootloader path is understood). Signed off. Read the firmware off the
device via the bootloader's read command (a read of the flash, but a device
interaction that needs the sign-off + the understood protocol).

5.3. **A firmware update/flash** (if the objective is to put known firmware on
the owned device). Signed off, bench-only. The highest-risk step — it changes
the device's persistent state. Only with a *known-good* image (verified by
hash), a bench loop, and a recovery plan (how to re-flash if it's bricked).

5.4. **A live output/transmit** (drive the device's output front-end at the
bench). Signed off, bench-only, loopback/contained, benign operation first.
The output is aimed at a bench loopback/owned receiver, not at open sky or a
live/third-party target.

5.5. **A crafted-input test** (if the input-handling assessment in Phase 3
found a thin spot and the objective is to *confirm* it on the owned device).
Signed off, bench-only, on the owned device, with the device in a recoverable
state. **This is the line** — it's where "input-handling assessment" (Phase 3,
documented) becomes "confirmed on the owned device" (Phase 5, signed). It is
*not* a step against a live/third-party device, ever.

**Danger notes (Phase 5):**
- **Every step is individually signed off.** No batch, no "while we're at
  it." The sign-off is per-change.
- **Bench-only + output-controlled** for every step. An output step has the
  output aimed at a bench loopback, not at open sky / a live network / a
  third party.
- **Recoverability.** Before a flash (5.3) or a crafted-input test (5.5),
  confirm the device can be recovered (a known-good image, a bootloader that's
  still reachable). Don't brick the only copy of an owned device without a
  recovery path.
- **Log everything.** The before/after state, the sign-off, the result.
- **The identity is still redacted** in any logged output.

**Gate 5→6:** the signed-off step(s) are complete, the result is demonstrated
+ logged, the device is in a known (recoverable/normal) state, the log is
updated.

---

## Phase 6 — Integrate into the project (the deliverable)

**Goal:** fold the new device's understanding + tooling into the project, so
it's part of the standing capability, not a one-off.

6.1. **The per-device folder is complete** — `references/<device>_material/`
with the manifest (provenance + hashes), the README (confirmed vs unproven),
the interface map, the protocol doc, the input-handling assessment, the binary
analysis notes.

6.2. **The native tooling is in the repo** — the read-only reader (Phase 3d/4)
+ any signed-off writer (Phase 5), under `tools/`, following the SECURITY.md
guardrails (no secrets in code, validate before parse, fail closed, the gate
if there's a write/output surface).

6.3. **The project surface (if the device's protocol is one the project
tracks).** If the device speaks a protocol the project detects/identifies, add
it to the project's protocol catalog + the per-protocol plugin. The new
device's protocol is the *next instance* of the generic pattern. A capability
not shown in the project's UI is not finished.

6.4. **The notes are current** — the orchestrator's notes + the relevant
`..._NOTES_FOR_BOTH.md` files record the onboarding (what was found, what's
confirmed vs unproven, the open items, the next steps). Code + notes in the
same commit.

6.5. **The open items are tracked** — the things still unproven (the firmware
if it wasn't obtained, the console/filesystem if it's still open, the
input-handling findings that need the binary to confirm) go on the project's
open-items list with the next step for each.

**Gate 6 (done):** the device is onboarded — identified, researched, analyzed,
validated (read-only), and (if signed off) interacted with; the per-device
folder + native tooling are in the repo; the project surface (if applicable)
is updated; the notes are current; the open items are tracked.

---

## The quick-reference checklist (the one-pager)

```
NEW DEVICE ONBOARDING — <name>, intake <date>, owner <who>

STANDING PRECONDITIONS (always true before any phase — not a step)
  [ ] Ownership + authorization confirmed (AGENTS.md §0)
  [ ] Bench isolated (no live/third-party target; output section off/disabled)

Phase 1 — Identify (read-only / observe)
  [ ] Physical inventory (connectors, power, markings — photographed)
  [ ] references/<device>_material/ created (README + manifest + .gitignore)
  [ ] Intake note written
  [ ] Identifiers — ALL modes/personalities, recorded
  [ ] Board markings (processor + output chip + board rev) — photographed
  [ ] Output front-end(s) identified (band/protocol/role/connector)
  [ ] Firmware/software source + revision (public / host-tool / gated / dumped)
  [ ] OS/RTOS status (bare-metal / RTOS / OS / unproven)
  [ ] Interface map complete (the one-pager)
  [ ] Every claim labeled CONFIRMED / UNPROVEN
  DANGER: power polarity, output exposure, don't infer processor from host tool
  GATE 1→2: interface map done, device unmodified

Phase 2 — Research + acquire (no code executed)
  [ ] Firmware/software image acquired (or the exact reason it's not) — manifest
  [ ] Host/management tool acquired (+ unpacked if packed) — manifest
  [ ] Upstream source cloned @ matching revision (if a fork)
  [ ] Docs / board files acquired
  [ ] manifest.json complete (URL + SHA-256 + classification + revision)
  [ ] README.md states confirmed vs unproven + honest gaps
  [ ] No downloaded executable/build script executed
  DANGER: provenance mandatory, verify artifact matches target, don't run code
  GATE 2→3: manifest complete, firmware status recorded, no code run

Phase 3 — Analysis (offline, read-only on device)
  [ ] Protocol doc (control / update / output — decoded + validated + evidence class)
  [ ] Checksum/CRC scheme identified + verified (test vector)
  [ ] Update/flash protocol decoded (sync, commands, chunk, ACK, finalization)
  [ ] Input-handling assessment (every surface: what's validated, what's not,
      the risk, the evidence class)
  [ ] Binary analysis (if in hand: processor, OS/RTOS, memory layout,
      mitigations, console/filesystem answer)
  [ ] Native reader built (read-only client)
  [ ] Interface map updated
  DANGER: offline + read-only, label every finding, don't act on findings
  GATE 3→4: analysis complete, findings labeled, device unmodified

Phase 4 — Validate on device (read-only live check)
  [ ] Device connected (right mode), descriptors match Phase 1
  [ ] Read-only surfaces exercised (native reader) — live bytes recorded
  [ ] Unsolicited output observed (or "none in N seconds" + the honest limit)
  [ ] Identity (serial/UID/MAC/key) redacted from output + notes
  [ ] Connection closed cleanly, device returned to normal state
  DANGER: query-only, output off, redact identity, one process per link
  GATE 4→5: read-only surfaces VALIDATED on device, identity redacted

Phase 5 — Gated interaction (writes + output — EACH STEP SIGNED OFF)
  [ ] Sign-off mechanism in place (real gate; bench-only + logged)
  [ ] (each step, individually signed off, bench-only, recoverable, logged)
      - config/state write (verify with read-after-write)
      - firmware read/dump (if gated)
      - firmware update/flash (known-good image + recovery plan)
      - live output/transmit (benign op first, bench loopback)
      - crafted-input test (confirm a Phase-3 finding on the owned device)
  DANGER: per-step sign-off, bench-only, recoverability, log everything
  GATE 5→6: signed steps complete + logged, device in known state

Phase 6 — Integrate
  [ ] references/<device>_material/ complete (manifest + README + map + docs)
  [ ] Native tooling in tools/ (reader + any signed-off writer)
  [ ] Project surface updated (if the protocol is one the project tracks)
  [ ] Notes current (orchestrator + the _NOTES_FOR_BOTH files)
  [ ] Open items tracked (firmware, console, unconfirmed findings)
  GATE 6: device onboarded, tooling in repo, surface updated, notes current
```

---

## The standing "never" list (applies across all phases)

- **Never** work on a device that isn't ours, on the bench, in our space.
  (AGENTS.md §0)
- **Never** point an output at a live, in-the-air, or third-party target.
  (AGENTS.md §2)
- **Never** enable a write/flash/output without explicit operator sign-off in
  that specific change. (AGENTS.md §2, SECURITY.md)
- **Never** hardcode a device's serial/UID/key/secret in code, fixtures, or
  logs. (SECURITY.md §1)
- **Never** overclaim a finding — every result carries its evidence class.
  (AGENTS.md §3)
- **Never** assume — read the device's actual state (descriptors, measured
  values, live bytes), not the nominal value. (AGENTS.md §1/§5)
- **Never** run a downloaded executable/build script during acquisition.
  (Phase 2 discipline)
- **Never** conflate the vendor's host/management tool with the device
  firmware.
- **Never** brick the only copy of an owned device without a recovery plan.
  (Phase 5)

---

*This blueprint is the method. It is not authorization — the authorization is
AGENTS.md §0 (owned hardware, bench, the stated purpose), and the boundary is
AGENTS.md §2 (the hard rules). The blueprint tells you how to proceed carefully
within that frame; it does not expand the frame.*
