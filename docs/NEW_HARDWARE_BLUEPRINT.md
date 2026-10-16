# NEW-HARDWARE BLUEPRINT — onboarding a new owned device into the project

**Purpose.** A reusable, step-by-step playbook: **"If we get a new piece of
hardware, this is what we do."** Apply it to any owned RF/IoT/drone/GCS
device the operator brings in — a new controller, a receiver, a flight
controller, a video link, a telemetry radio, a ground-station box, etc.

**Scope (read first).** This is for **owned hardware, on the bench, in the
operator's own space** (AGENTS.md §0). Every step defaults to **read-only /
passive / offline** until a step explicitly says a *write* or *transmit* is in
play — and any such step requires **explicit operator sign-off in that
specific change** (AGENTS.md §2, SECURITY.md). The blueprint is a *method*,
not a license: it tells you *how to proceed carefully*, it does not waive the
boundary.

**The three standing rules that apply at every step** (from AGENTS.md):
1. **Honesty contract** — label every finding VALIDATED / SELF-CONSISTENT /
   SYNTHETIC. "I think it's X" is not a finding; "X, confirmed by <evidence>"
   is.
2. **Measure, don't assume** — read the device's actual state (descriptors,
   measured sample rate, live bytes), never the nominal/expected value.
3. **Code + notes in the same commit** — each step that produces a tracked
   artifact ships with its notes entry. Each agent writes only their own
   `..._NOTES_FOR_BOTH.md`.

**The output of a completed onboarding:** a per-device folder under
`references/<device>_material/` (the `e8_material` pattern) with a
`manifest.json` (provenance + hashes), a `README.md` (what's confirmed vs
unproven), the device's identified interfaces, and a one-page
**interface map** (what talks to what, at what rate, in what format). Plus
any native tooling built against the confirmed read-only surfaces.

---

## Phase 0 — Intake & safety setup (do this before touching anything)

**Goal:** establish the safety frame + a record of what the device is, *before*
any probing. Nothing in Phase 0 modifies the device.

0.1. **Confirm ownership + authorization.** Verify the device is the
operator's own, on the bench, in their space. Record: model, serial number,
where it was obtained, the date. (This is the AGENTS.md §0 authorization —
without it, stop.)

0.2. **Bench isolation.** Confirm the bench is isolated: no live airspace
above it, no third-party links in range that we'd act on, the device's RF
transmitter (if any) is **off / removed / disabled** until a TX step is
explicitly signed off. For a device with an RF front-end, note how to keep it
passive (power to the logic but not the PA, a loopback antenna, a shielded
box, or simply no supply to the RF section).

0.3. **Power + physical inventory.** Photograph the device (top, bottom,
connectors, board if visible). List every physical interface: USB (type +
count), UART pads, SWD/JTAG headers, RF connectors, buttons, LEDs, battery
contacts. Note the power requirements (voltage, current, connector polarity).
**Danger note:** do not power a device whose polarity/supply you haven't
confirmed — a reversed supply or an over-voltage is the most common way to
brink an owned device before you've learned anything from it.

0.4. **Create the per-device folder.** `references/<device>_material/` with a
`README.md` (stub: device name, intake date, owner, "confirmed vs unproven"
table to fill in) + a `manifest.json` (empty artifact list) + a `.gitignore`
that excludes `downloads/` + `upstream/` (binaries/firmware stay out of git;
provenance + notes are tracked — the `e8_material` pattern).

0.5. **Write the intake entry** in the orchestrator's notes (andeds): device
intake, what's in scope, the safety frame, the first open questions.

**Gate 0→1:** ownership confirmed, bench isolated, physical interface list
done, device folder created. No device modification.

---

## Phase 1 — Identify the device (hardware, software, firmware)

**Goal:** establish *what the device actually is* — its MCU/board, its
firmware, its OS (if any), its interfaces — from evidence, not from the box
label or the marketing page. This phase is all observation + passive
inspection.

### 1a. Hardware identification

- **USB descriptors (the first, cheapest signal).** Connect the device (with
  the RF section off if separable) and run:
  ```sh
  lsusb -v                        # full descriptor tree
  lsusb -t                        # the bus topology
  cat /sys/bus/usb/devices/*/idVendor /sys/bus/usb/devices/*/idProduct
  dmesg | tail -40                # what the kernel logged on plug-in
  ```
  Record the `idVendor:idProduct` for *each* USB personality the device shows.
  **Key technique — enumerate ALL personalities:** many MCU devices change
  their USB ID across modes (e.g. the E8 shows `0483:5710` "EMAXJoystick" HID
  in normal mode and `8888:2001` "Your Company / USB Serial" CDC-ACM in
  serial/update mode). Power-cycle the device into each documented mode (check
  the manual for the mode-switch procedure — e.g. "hold button X while
  powering on") and record each ID. **A device with multiple USB IDs is
  telling you it has multiple software modes** — that's a map of its
  interfaces.
  **Danger note:** a *generic* descriptor (`"Your Company"`, `"USB Serial"`,
  a `8888:xxxx` or `0483:xxxx` template ID) usually means an **unbranded MCU
  bootloader stub** — the vendor hasn't customized it. That's a strong hint
  about the MCU family (STM32/AT32/CH32/etc. all ship these templates) and
  that the "serial mode" is a **bootloader**, not the app.

- **Board markings (the ground truth for the MCU).** If the board is exposed
  (or the device has a service opening), photograph the markings: the MCU
  part number (e.g. `STM32F411CCU6`, `AT32F435`, `ESP32-WROOM`), the radio
  chip (e.g. `SX1280`, `SX1276`, `nRF52840`), the flash chip, any board
  revision string. **Do not infer the MCU from the Windows host tool or the
  marketing page** — read the marking. (The E8 investigation explicitly
  flagged that "Air32/AT32/STM32-family" was *unconfirmed* until a board
  marking established it.)

- **The radio front-end.** Identify the RF chip + its interface (SPI/UART/
  GPIO) from the markings or a schematic. Note the band(s) + the connector.
  This tells you what the device *transmits/receives* and on what PHY — the
  input to the RF-side analysis later.

- **Decomposition (only if needed + only with care).** If the board isn't
  visible and the markings are needed, the operator may open the enclosure.
  **Danger note:** note antenna locations (RF exposure), battery type +
  how to isolate it, and any tamper switches. Photograph before removing
  anything. This is a physical-modification step — it's on *owned* hardware,
  but record it.

### 1b. Software / firmware identification

- **The host tool (if the vendor ships one).** If the device has a vendor
  configurator/host tool (a `.exe`, a `.app`, a Python tool), acquire it
  (Phase 2) and inspect it for: the serial command surface, the baud/config,
  the firmware it references, and the device modes it drives. **Critical
  distinction (the E8 lesson):** the *Windows host tool is NOT the device
  firmware.* `SHELL32.dll` in a `.exe` is a Windows host dependency, not
  evidence of a shell on the device. Never conflate them.

- **The firmware image (the long pole).** Determine whether the device's
  firmware is:
  - **Publicly downloadable** (vendor release page, a firmware server, an
    open-source project's release) → acquire it (Phase 2).
  - **Update-via-host-tool** (the configurator flashes it) → the image is
    what the tool sends; capture it or find the tool's bundled image.
  - **Not publicly available** (the E8 case — EMAX-gated) → it must be
    **read from the device** (a bootloader read / flash dump) or **requested
    from the vendor**. This is often the blocking item; flag it early.
  Record which case applies + the exact revision (version string, build date,
  commit) if known.

- **The OS / RTOS question (honest scoping).** Determine (or flag as
  unproven) whether the device runs bare-metal firmware, an RTOS
  (FreeRTOS/Zephyr/ThreadX — look for the markers in the firmware binary), or
  a general-purpose OS (Linux — look for a filesystem, a `/dev`, an init).
  **Do not assume.** The E8 investigation's honest answer was "no shell or
  filesystem interface *established*" — not "the device has no OS." Record
  what's evidenced vs what's unproven (see 1e).

### 1c. The interface map (the key deliverable of Phase 1)

Produce a one-page **interface map** for the device — the thing every later
phase builds on:

```
DEVICE: <name> (intake <date>, owner <who>)
Board:   <MCU marking> + <radio chip> + <board rev>
Power:   <V, connector, polarity>

USB personalities:
  - <idVendor:idProduct> "<product string>"  mode=<normal/serial/update>  class=<HID/CDC-ACM/DFU/...>
  - <...>

Serial/UART:
  - <interface>  baud=<measured>  config=<8N1/...>  format=<binary-protocol/line/...>
  - command surface: <Read/Write/Update/...> (from host tool or probing)

RF:
  - <band(s)>  PHY=<LoRa/GFSK/802.11/...>  role=<TX/RX/TX+RX>  connector=<...>

Firmware:
  - source=<public/downloadable/host-tool/gated>  revision=<...>
  - OS/RTOS=<bare-metal/RTOS-name/Linux/unproven>
  - update mechanism=<DFU/serial-flash/none-known>

CONFIRMED:   <list, each with its evidence>
UNPROVEN:    <list, each with "why it's not yet confirmed">
```

### 1d. Danger notes (Phase 1)

- **Power polarity/voltage** — the #1 brick risk (0.3).
- **RF exposure** — keep the transmitter off/disabled during hardware ID
  (0.2, 1a). Don't have a live PA radiating while you're unplugging things.
- **Don't infer the MCU/OS from the host tool** (1a, 1b) — the E8 lesson.
- **Don't flash anything to "test" the update path** — that's a write step,
  Phase 4/5, needs sign-off. In Phase 1 you *identify* the update mechanism,
  you don't *use* it.

### 1e. The CONFIRMED / UNPROVEN table (the honesty contract, applied)

For every claim in the interface map, mark it:
- **VALIDATED** — confirmed by direct evidence (a descriptor read, a board
  marking photo, a live byte capture, a firmware string).
- **SELF-CONSISTENT** — round-trips our own reasoning but not yet proven
  against the device.
- **SYNTHETIC / UNPROVEN** — inferred, or a hypothesis flagged as such.
The E8 console investigation is the model: it listed "MCU model, board
revision, app/bootloader memory layout, bare-metal vs RTOS vs OS, presence of
a filesystem, UART console pads, SWD/JTAG state" all as **unproven** until a
matching marking/schematic/firmware establishes them.

**Gate 1→2:** interface map complete (USB personalities, serial config, RF
front-end, firmware source + revision, OS/RTOS status), every claim labeled
CONFIRMED/UNPROVEN. The device is still unmodified (except possibly a
careful, recorded enclosure opening).

---

## Phase 2 — Research + acquire reference material (download copies for analysis)

**Goal:** gather everything we can analyze *offline* — the firmware, the
host tool, the upstream source, the documentation, the board files — with
**full provenance**, so the analysis in Phase 3 is reproducible and auditable.

**The standing acquisition discipline** (the `e8_material` pattern):
- `downloads/` = vendor docs + host tools + device firmware (when its origin +
  target are identified). **Untracked** (gitignored) — binaries don't go in git.
- `upstream/` = official source/hardware-definition checkouts for comparison.
  **Untracked.**
- `manifest.json` = **tracked** — for every artifact: source URL, final URL,
  SHA-256, size, classification (vendor_support_page / vendor_linked_manual /
  Windows_host_tool_NOT_device_firmware / upstream_source / device_firmware),
  revision identifier (commit/version/build date).
- `README.md` = **tracked** — what's confirmed vs unproven, the acquisition
  results, the honest gaps.
- **Never execute a downloaded executable or build script** during
  acquisition. Acquire + hash + catalog; *analysis* (Phase 3) is a separate,
  deliberate step. (The E8 acquisition note: "No downloaded executable or
  build script was executed.")

### 2a. Firmware acquisition (the priority)

Try, in order, recording which one works in the manifest:
1. **Vendor public release** — the vendor's downloads/firmware page. Record
   the exact URL + the image's revision. **Verify it's for THIS device**
   (model + variant — the E8 has FrSky-D8 vs ELRS variants that must not be
   conflated; a "similar" firmware for a sibling model is not the target).
2. **Firmware server / OTA endpoint** — if the device pulls firmware from a
   server (an ELRS `firmware.betasd.com`-style URL, a vendor OTA), find the
   exact URL the device requests (from the host tool or a passive network
   capture of the device updating) + the image it returns.
3. **Host-tool-bundled image** — if the configurator flashes the device, the
   image is either bundled in the tool's resources or is what the tool
   downloads on demand. Extract it from the tool (static — don't run the
   tool to get it if you can avoid it) or find the tool's download URL.
4. **Read from the device (a write-adjacent step — sign-off).** If the
   firmware is gated (the E8 case), the only way to get it is a **bootloader
   read / flash dump** off the device. This requires knowing the bootloader's
   read command (which comes from Phase 3's analysis of the host tool's
   update path) and is a *device interaction* — it's on owned hardware but
   it's not a pure read of the *interface*; flag it + get sign-off before
   doing it.
5. **Vendor request** — as a parallel track (slow turnaround), request the
   `firmware.bin`/`.elf` from the vendor's support.

**Record the outcome honestly.** The E8 result was `exact_e8_elrs_device_firmware:
not obtained; no verified download found in checked vendor pages or upstream
targets` — that's a *valid* outcome, and it's recorded as such. "We looked
here, here, here; here's what we found; here's what's still missing" is the
honest format.

### 2b. Host tool acquisition

Acquire the vendor's configurator/host tool (the `.exe`/`.app`/Python). If
it's a packed Windows exe (the E8 was Enigma **Virtual Box** — a file-storage
packer, not code encryption), **unpack it first** (`evbunpack` or equivalent)
so Phase 3 analyzes the *inner* plaintext binary, not the packer wrapper.
**The E8 lesson:** measure the *inner* binary's `.text` entropy, not the
packer's data section — that's what tripped the "it's encrypted" false
conclusion. Record: the packer type (if any), the inner exe's size + hash,
the unpack tool + version.

### 2c. Upstream source acquisition

If the device's firmware is a fork of an open-source project (ELRS, Betaflight,
a FreeRTOS app, etc.), clone the **upstream source at the matching revision**
for comparison:
```sh
git clone <upstream-url> upstream/<project>
cd upstream/<project> && git checkout <matching-commit-or-tag>
```
Record the commit in the manifest. **Verify the device's target is actually in
upstream** (the E8 lesson: a search of upstream `platformio.ini` + target files
found EMAX OLED/Nano ESP32 targets but **no E8 entry** — so the E8 is an EMAX
fork *not* in upstream; that's a finding, not a dead end). If the target isn't
in upstream, the firmware is vendor-forked and you need the vendor image
(2a) — record that.

### 2d. Documentation + board files

Acquire: the user manual, the service/repair manual, any schematic or board
files the vendor publishes, the support articles that describe the device's
modes + update procedure. Record each with its URL + hash. The manual is
where the **mode-switch procedures** live (how to get the device into
serial/update/bootloader mode) — that's Phase 1a's input.

### 2e. Danger notes (Phase 2)

- **Provenance is mandatory** — no artifact enters `downloads/` without a
  manifest entry (URL + hash + classification). An un-provenanced binary is a
  liability, not a reference.
- **Verify the artifact matches the target device** (model + variant) before
  treating it as "the firmware." A sibling model's image is a *comparison
  reference*, not the target — classify it as such.
- **Don't run downloaded code during acquisition** (2-discipline). Hash +
  catalog now; analyze (and only then run, in a sandbox, if at all) in Phase 3.
- **Packed exe → unpack before analysis** (2b) — don't waste Phase 3 on the
  packer wrapper.

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
on the *device* (we're analyzing files, not talking to the device) — though
it may *inform* later device-interaction steps.

**The two analysis tracks, run in parallel:**
- **Track A — Protocol/interface analysis** (understand how it talks).
- **Track B — Input-handling assessment** (find where it parses input + how
  robustly).

### 3a. Track A: protocol + interface analysis

- **The serial/binary protocol.** If the device has a binary serial protocol
  (the E8's 64-byte-command / 17-byte-response scheme), decode it:
  - **Static** (from the host tool's disassembly): the command surface (the
    button/slot names), the frame layout (header, payload, CRC), the baud/
    config (the `setBaudRate`-style immediates), the per-command buffer
    construction. (The E8 method: static decode of the plaintext inner exe's
    `.text`/`.rdata`/`.idata` — the CRC-16/XMODEM, the `02 01`/`02 02`
    headers, the handler map all came from here.)
  - **Dynamic** (a live capture): drive the device through its operations and
    capture the actual bytes (the `strace -f -e trace=write` method for a
    host-tool-driven protocol, or a serial TAP for a direct protocol). The
    live bytes are ground truth to validate the static decode against. (The
    E8 method: the `32 31` "mystery" was a strace parser bug — the live
    capture + a correct parser is what confirmed the real `02 01` command.)
  - **The checksum/CRC scheme** — identify the exact CRC (poly, init,
    reflect, input range) + verify it with a known test vector (the E8:
    CRC-16/XMODEM, poly `0x1021`, init `0x0000`; verified by `123456789`→
    `0x31C3` + 60 zeros→`0x0000`).
- **The update/flash protocol.** Decode the device's firmware-update path:
  the sync/entry sequence, the command set (the E8: `0x18×5` sync sentinel,
  `0x04`/`0x05` phase commands, dynamic chunk frames), the chunk size +
  framing, the per-chunk ACK, the finalization. This is the highest-value
  protocol to understand (it's the device's most privileged input path).
- **The RF protocol (if the device has one).** Identify the air protocol
  (ELRS/Crossfire/DSMx/proprietary) + its PHY + frame format. This feeds the
  project's main DETECT/IDENTIFY mission (GUIDING_LIGHT Phase 1/2). For an
  ELRS-family device, the sync/UID/CRC are the reusable core.
- **Build the native reader/writer (read-only first).** Once the protocol is
  decoded, build a minimal native client (no vendor tool) that exercises the
  **read-only** surface (the E8: `tools/e8_read.py` — a 20-line read-only
  client). **Read-only until the parser is fully understood + a human
  approves a specific write** (SECURITY.md §4). The reader is both a
  validation of the protocol decode and the seed of the device's native
  tooling.

### 3b. Track B: input-handling assessment (the "what to look for")

This is where you find the interesting behavior. For **each interface the
device parses input on** (the serial protocol, the update path, the USB HID,
the RF receive, any debug console), assess:

**The input-handling questions (ask these of every parser):**
1. **Bounds-checking.** Does the parser validate the length/size of incoming
   data *before* indexing into it? (A length field that's trusted without a
   clamp is the classic bug.)
2. **The CRC/checksum gate.** Is the integrity check done *before* the payload
   is acted on, or after? (A payload that's processed before the CRC is
   verified is a reflected-read / state-change surface.)
3. **Reflected reads.** Does any response echo attacker-controlled bytes back?
   (A reflected read is the first rung of an input-handling finding.)
4. **State-changing writes.** Which inputs change the device's persistent
   state (flash, config, bind key)? Are those gated behind the integrity
   check + an explicit command, or reachable by a malformed frame?
5. **The update path's validation.** Does the bootloader verify a
   signature/CRC over the *whole* image before committing it to flash, or
   does it stream chunks to flash as they arrive? (No whole-image check =
   the update path accepts whatever the host sends — the highest-value
   input-handling surface.)
6. **Error handling.** What happens on a malformed/short/bad-CRC frame — does
   the parser fail closed (drop + reset) or does it leave the device in a
   partially-updated state?
7. **The OS/RTOS context.** If the device runs an RTOS, are there shared
   buffers between the parser task and other tasks (a race window)? If
   bare-metal, is there a stack that a deep recursive parse could overflow?

**Point the dangerous stuff (label it, don't act on it in this phase):**
For each input-handling surface, record:
- **The surface** (which parser, which interface).
- **The input that reaches it** (the bytes, the framing).
- **What's validated** (the checks that ARE present).
- **What's NOT validated** (the gaps — the length check, the whole-image
  CRC, the bounds check).
- **The evidence class** (VALIDATED from the disassembly / SELF-CONSISTENT /
  SYNTHETIC hypothesis).
- **The risk, stated technically** (e.g. "the update path commits chunks to
  flash as they arrive; no whole-image signature check is present in the
  bootloader's string table — unconfirmed, needs the firmware binary to
  verify").

**The honesty rule for this phase:** you are *assessing input handling*, not
*exploiting it*. You document what the parser does + where it's thin. You do
**not** craft a malformed input to trigger a finding in this phase — that's a
later, explicitly-signed step (Phase 5). "The parser doesn't clamp the chunk
size (per the disassembly at 0x...)" is the finding; "here's a chunk that
crashes it" is Phase 5.

### 3c. The firmware binary analysis (when the firmware is in hand)

If Phase 2 obtained the firmware image (`.bin`/`.elf`):
- **Recover symbols** (if the `.elf` has them; if not, the strings + the
  disassembly). Identify the MCU (from the vector table / the linker
  script), the OS/RTOS (FreeRTOS/Zephyr markers, the heap/stack layout), the
  flash layout (app region, bootloader region, config region, the bind-key
  storage).
- **The mitigation audit** (for the input-handling surfaces): canaries (the
  `__stack_chk_guard` on ARM), RELRO (the GOT/PLT layout), ASLR (none on a
  no-MMU MCU like the STM32/AT32 — flag that), the stack size, the MPU
  configuration (if any).
- **The input-handling surfaces in the firmware** (Track B, now on the real
  binary): the serial parser, the update handler, the RF receive handler —
  disassemble + assess per 3b.
- **The console/filesystem question** (if still open from Phase 1): look for
  a UART init (the baud + the console task), a command table (a shell), a
  filesystem (the FAT/LittleFS/RTOS-FS markers). The E8 investigation left
  this **unproven** — the firmware binary is what would settle it.

### 3d. The deliverables of Phase 3

- The **protocol doc** (the serial/update/RF protocols, decoded + validated,
  with the evidence class) — the `e8_protocol_reconstruction.md` pattern.
- The **input-handling assessment** (every surface: what's validated, what's
  not, the risk, the evidence class).
- The **native reader** (the read-only client, if the protocol is decoded).
- The **firmware analysis notes** (the MCU, OS/RTOS, flash layout,
  mitigations, the console/filesystem answer — or "still unproven").
- The updated **interface map** (Phase 1c, now with the decoded protocols +
  the input-handling surfaces).

### 3e. Danger notes (Phase 3)

- **This phase is offline + read-only on the device.** We analyze files; we
  don't talk to the device (except the read-only native reader, which is a
  *query*, not a state change). Any step that would *change* the device is
  Phase 5 + sign-off.
- **Label every input-handling finding with its evidence class.** "The
  disassembly shows no length clamp" (VALIDATED) is different from "I think
  the parser might not clamp" (SYNTHETIC). Don't overclaim.
- **Don't act on the findings in this phase.** Document the thin spots;
  don't craft the triggering input. That's the line between "input-handling
  assessment" (this phase) and "exploitation" (Phase 5, signed).
- **The firmware binary is the long pole** — if Phase 2 didn't get it, a lot
  of Phase 3 is "the host tool tells us X, but the firmware would confirm
  it." Flag every such "host-tool-inferred, firmware-unconfirmed" item.

**Gate 3→4:** protocol doc + input-handling assessment + (if possible) the
native reader + (if the firmware is in hand) the firmware analysis notes are
complete, every finding labeled with its evidence class, the interface map
updated. The device is still unmodified.

---

## Phase 4 — Validate on the device (the read-only live check)

**Goal:** confirm the offline analysis against the **live device** — the
read-only surfaces, on the bench, with the device connected. This is where
"SELF-CONSISTENT" becomes "VALIDATED on the owned device." Still read-only /
query-only; no state changes.

4.1. **Connect the device** in the relevant mode (from Phase 1a's mode map),
with the RF section off (if the validation is serial/USB, not RF). Confirm
the USB/serial descriptors match Phase 1's records (a mismatch is a finding —
the device isn't in the mode you think).

4.2. **Exercise the read-only surfaces** with the native reader (Phase 3d).
The E8 example: an exclusive 115200 8N1 connection sent only the confirmed
64-byte Read request; the response was exactly 17 bytes with the expected
`02 c5` header. Record the live bytes + the evidence class (VALIDATED on the
owned device).

4.3. **Watch for unsolicited output.** During the connection, observe whether
the device emits anything unprompted (a boot banner, a debug log, a console
prompt). The E8 check: "no unsolicited bytes arrived during the short initial
observation" — that *confirms a working binary connection, not a shell*;
silence during a brief observation doesn't disprove another console. Record
what you saw + the honest limit.

4.4. **Redact the identity.** If the read returns the device's bind UID /
serial / MAC, **redact it from the tool output + the notes** (SECURITY.md §1,
the E8 pattern: "the bind UID was redacted from tool output and is not
recorded"). The wire bytes can be in a captured frame for cross-check; the
value is not hardcoded or logged.

4.5. **Close the connection cleanly.** Disconnect, confirm the device returns
to its normal state (the LED, the USB personality). Record the session.

**Danger notes (Phase 4):**
- **Query-only.** The live check sends *read* commands (the request half of a
  query/response). It does not send a state-changing command (a config write,
  a flash, a bind change). That's Phase 5.
- **RF off** (unless the validation is specifically the RF surface, which
  needs its own sign-off + bench isolation).
- **Redact the identity** (4.4) — the device's UID/serial is a secret.
- **One process at a time** on the serial port (the E8 port-exclusivity
  lesson — a second reader TAPing the port gets nothing; use `strace -f -e
  trace=write` on the host process instead, which reads the syscalls
  directly).

**Gate 4→5:** the read-only surfaces are VALIDATED on the live device (the
offline analysis is confirmed), the identity is redacted, the device is
returned to its normal state. The device's state is unchanged (only reads).

---

## Phase 5 — Gated interaction (writes + transmit — sign-off required)

**Goal:** if/when the objective requires *changing* the device (a config
write, a firmware update, a live RF transmit, a crafted-input test), do it
**gated, bench-only, with explicit operator sign-off in that specific
change.** This is the only phase that modifies the device or transmits. Every
step here is individually signed off; there is no blanket authorization.

**The standing gate (AGENTS.md §2, SECURITY.md, GUIDING_LIGHT Phase 3):**
- **Bench/owned only.** The device is the operator's own, on the bench, radio
  present, no live/in-the-air/third-party link in range.
- **Explicit human sign-off in that specific change** — never an agent's own
  initiative, never justified by a downstream goal.
- **The TX gate is real** (replaces the self-asserted flag, QC-001): a proper
  out-of-band human sign-off mechanism + hard bench-only checks (radio-present
  + owner assertion) + logged. No TX fires before this lands.
- **Positive commands first** (for an RF "act" step): a benign, reversible
  command (throttle up on the owned bench receiver), not a destructive one.
- **Each new surface/protocol gets its own sign-off + bench-only
  justification** (the ELRS link is signed off; a different protocol's TX is a
  separate sign-off).

**The gated steps (each individually signed off):**

5.1. **A config write** (e.g. the E8 writeConfig — set a rate/power/tlm value).
Signed off, bench-only. The native writer (Phase 3d, extended) sends the
write frame; verify the change with a subsequent read (the read-after-write
verification). Log the before/after.

5.2. **A firmware read/dump** (if Phase 2 couldn't get the firmware and the
update path is understood). Signed off. Read the firmware off the device
via the bootloader's read command (not a write — it's a read of the flash,
but it's a device interaction that needs the sign-off + the understood
bootloader protocol).

5.3. **A firmware update/flash** (if the objective is to put known firmware
on the owned device). Signed off, bench-only. The highest-risk step — it
changes the device's persistent state. Only with a *known-good* firmware
image (the vendor's, verified by hash), a bench loop, and a recovery plan
(how to re-flash if it bricked).

5.4. **A live RF transmit** (the "act" phase — forge a valid frame + the
owned bench receiver accepts it). Signed off, bench-only, positive command
first, the bench TX→RX loopback (GUIDING_LIGHT P3-2 / the phased plan Phase 4).
The "it actually works" milestone — the owned receiver accepts the forged,
CRC-valid, recovered-UID frame carrying a positive command, and the result
(accepted + acted) is demonstrated + logged.

5.5. **A crafted-input test** (if the input-handling assessment in Phase 3
found a thin spot and the objective is to *confirm* it on the owned device).
Signed off, bench-only, on the owned device, with the device in a recoverable
state. **This is the line** — it's where "input-handling assessment" (Phase 3,
documented) becomes "confirmed on the owned device" (Phase 5, signed). It is
*not* a step against a live/third-party device, ever.

**Danger notes (Phase 5):**
- **Every step is individually signed off.** No batch, no "while we're at
  it." The sign-off is per-change.
- **Bench-only + RF-controlled** for every step. A transmit step has the PA
  aimed at the bench loopback, not at open sky.
- **Recoverability.** Before a flash (5.3) or a crafted-input test (5.5),
  confirm the device can be recovered (a known-good firmware, a bootloader
  that's still reachable). Don't brick the only copy of an owned device
  without a recovery path.
- **Log everything.** The before/after state, the sign-off, the result. The
  phase-gate log (GUIDING_LIGHT) records the gate decision.
- **The identity is still redacted** in any logged output.

**Gate 5→6:** the signed-off step(s) are complete, the result is demonstrated
+ logged, the device is in a known (recoverable/normal) state, the
phase-gate log is updated.

---

## Phase 6 — Integrate into the project (the deliverable)

**Goal:** fold the new device's understanding + tooling into the project, so
it's part of the standing capability, not a one-off.

6.1. **The per-device folder is complete** — `references/<device>_material/`
with the manifest (provenance + hashes), the README (confirmed vs unproven),
the interface map, the protocol doc, the input-handling assessment, the
firmware analysis notes.

6.2. **The native tooling is in the repo** — the read-only reader (Phase 3d/4)
+ any signed-off writer (Phase 5), under `tools/`, following the SECURITY.md
guardrails (no secrets in code, validate before parse, fail closed, the TX
gate if there's a TX surface).

6.3. **The WaveRaider surface (if it's an RF device).** If the device's RF
protocol is one the project DETECTs/IDENTIFies, add it to the protocol catalog
(`elrs_sdr/protocol_catalog.py`) + the per-protocol plugin
(`waveraider/protocols/<name>/`). The new device's protocol is the *next
instance* of the generic pattern (ELRS → Crossfire → DSMx → …). A detector
improvement not shown in the UI is not finished (AGENTS.md §1).

6.4. **The notes are current** — the orchestrator's notes (andeds) + the
relevant `..._NOTES_FOR_BOTH.md` files record the onboarding (what was found,
what's confirmed vs unproven, the open items, the next steps). Code + notes in
the same commit.

6.5. **The open items are tracked** — the things that are still unproven (the
firmware if it wasn't obtained, the console/filesystem if it's still open, the
input-handling findings that need the firmware to confirm) go on the project's
open-items list with the next step for each.

**Gate 6 (done):** the device is onboarded — identified, researched, analyzed,
validated (read-only), and (if signed off) interacted with; the per-device
folder + native tooling are in the repo; the WaveRaider surface (if RF) is
updated; the notes are current; the open items are tracked.

---

## The quick-reference checklist (the one-pager)

```
NEW DEVICE ONBOARDING — <name>, intake <date>, owner <who>

Phase 0 — Intake & safety (read-only)
  [ ] Ownership + authorization confirmed (AGENTS.md §0)
  [ ] Bench isolated (no live airspace, RF TX off/disabled)
  [ ] Physical inventory (connectors, power, markings — photographed)
  [ ] references/<device>_material/ created (README + manifest + .gitignore)
  [ ] Intake note written
  GATE 0→1: safety frame set, device unmodified

Phase 1 — Identify (read-only / observe)
  [ ] USB descriptors — ALL personalities (each mode), recorded
  [ ] Board markings (MCU + radio + board rev) — photographed
  [ ] RF front-end identified (band, PHY, role, connector)
  [ ] Firmware source + revision (public / host-tool / gated / dumped)
  [ ] OS/RTOS status (bare-metal / RTOS / Linux / unproven)
  [ ] Interface map complete (the one-pager)
  [ ] Every claim labeled CONFIRMED / UNPROVEN
  DANGER: power polarity, RF exposure, don't infer MCU from host tool
  GATE 1→2: interface map done, device unmodified

Phase 2 — Research + acquire (no code executed)
  [ ] Firmware acquired (or the exact reason it's not) — manifest entry
  [ ] Host tool acquired (+ unpacked if packed) — manifest entry
  [ ] Upstream source cloned @ matching revision (if a fork)
  [ ] Docs / board files acquired
  [ ] manifest.json complete (URL + SHA-256 + classification + revision)
  [ ] README.md states confirmed vs unproven + honest gaps
  [ ] No downloaded executable/build script executed
  DANGER: provenance mandatory, verify artifact matches target, don't run code
  GATE 2→3: manifest complete, firmware status recorded, no code run

Phase 3 — Analysis (offline, read-only on device)
  [ ] Protocol doc (serial / update / RF — decoded + validated + evidence class)
  [ ] CRC/checksum scheme identified + verified (test vector)
  [ ] Update/flash protocol decoded (sync, commands, chunk, ACK, finalization)
  [ ] Input-handling assessment (every surface: what's validated, what's not,
      the risk, the evidence class)
  [ ] Firmware binary analysis (if in hand: MCU, OS/RTOS, flash layout,
      mitigations, console/filesystem answer)
  [ ] Native reader built (read-only client)
  [ ] Interface map updated
  DANGER: offline + read-only, label every finding, don't act on findings
  GATE 3→4: analysis complete, findings labeled, device unmodified

Phase 4 — Validate on device (read-only live check)
  [ ] Device connected (right mode), descriptors match Phase 1
  [ ] Read-only surfaces exercised (native reader) — live bytes recorded
  [ ] Unsolicited output observed (or "none in N seconds" + the honest limit)
  [ ] Identity (UID/serial/MAC) redacted from output + notes
  [ ] Connection closed cleanly, device returned to normal state
  DANGER: query-only, RF off, redact identity, one process per port
  GATE 4→5: read-only surfaces VALIDATED on device, identity redacted

Phase 5 — Gated interaction (writes + TX — EACH STEP SIGNED OFF)
  [ ] Sign-off mechanism in place (replaces QC-001; bench-only + logged)
  [ ] (each step, individually signed off, bench-only, recoverable, logged)
      - config write (verify with read-after-write)
      - firmware read/dump (if gated)
      - firmware update/flash (known-good image + recovery plan)
      - live RF transmit (positive command first, bench loopback)
      - crafted-input test (confirm a Phase-3 finding on the owned device)
  DANGER: per-step sign-off, bench-only, recoverability, log everything
  GATE 5→6: signed steps complete + logged, device in known state

Phase 6 — Integrate
  [ ] references/<device>_material/ complete (manifest + README + map + docs)
  [ ] Native tooling in tools/ (reader + any signed-off writer)
  [ ] WaveRaider surface updated (if RF: protocol catalog + plugin)
  [ ] Notes current (orchestrator + the _NOTES_FOR_BOTH files)
  [ ] Open items tracked (firmware, console, unconfirmed findings)
  GATE 6: device onboarded, tooling in repo, surface updated, notes current
```

---

## The standing "never" list (applies across all phases)

- **Never** work on a device that isn't the operator's own, on the bench, in
  their space. (AGENTS.md §0)
- **Never** point a transmit at a live, in-the-air, or third-party link.
  (AGENTS.md §2)
- **Never** enable a write/flash/transmit without explicit operator sign-off
  in that specific change. (AGENTS.md §2, SECURITY.md)
- **Never** hardcode a device's bind UID / serial / secret in code, fixtures,
  or logs. (SECURITY.md §1)
- **Never** overclaim a finding — every result carries its evidence class.
  (AGENTS.md §3)
- **Never** assume — read the device's actual state (descriptors, measured
  rate, live bytes), not the nominal value. (AGENTS.md §1/§5)
- **Never** run a downloaded executable/build script during acquisition.
  (Phase 2 discipline)
- **Never** conflate the vendor's host tool with the device firmware. (The E8
  lesson)
- **Never** brick the only copy of an owned device without a recovery plan.
  (Phase 5)

---

*This blueprint is the method. It is not authorization — the authorization is
AGENTS.md §0 (owned hardware, bench, defensive/reverse-engineering purpose),
and the boundary is AGENTS.md §2 (the hard rules). The blueprint tells you how
to proceed carefully within that frame; it does not expand the frame.*
