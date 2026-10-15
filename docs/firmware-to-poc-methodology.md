# Firmware → Vulnerability Analysis → PoC Development

> **Methodology.** The full pathway from "a firmware binary is in the repo" to
> "a working proof-of-concept, validated on real gear." Written by andeds
> (orchestrator), 2026-10-15, from the actual TinyHawk III run in this repo.
> It is not a textbook list — each wicket below is annotated with what we
> actually did, what blocked, and what the exit evidence is.
>
> **Scope:** authorized CTF preparation against owned equipment, wireless
> delivery on game day, bench/owned hardware only, no-flash constraint on the
> E8. The defensive framing (situational awareness / counter-UAS) is the
> project's authorization posture; this doc is the offensive analysis pipeline
> inside that boundary.
>
> **The three major wickets** the owner named — *firmware analysis*, *vuln
> analysis*, *PoC development* — are Wickets 3–5 and 8–9 here. The doc covers
> everything between "downloaded blob" and "validated PoC," including the
> reconnaissance, the target-modeling, the attack-surface mapping, the
> exploit-design, and the live-validation stages that make a PoC *real* rather
> than *claimed*.

---

## The shape of the pathway

```
W0  FRAME        target facts + rules + success condition → "I know what matters and what is authorized"
W1  ACQUIRE      blob + provenance + version pin          →  "I know exactly what this is"
W2  MODEL        target architecture + memory map + toolchain → "I can load it correctly"
W3  RECON        symbols/strings/entropy/disasm lay-of-the-land → "I know what's in it"
W4  SURFACE      attack-surface map (inputs, sinks, trust boundaries) → "I know where the input goes"
W5  VULN         candidate vulns, triaged, evidence-classed → "I have a ranked, defensible list"
W6  DESIGN       exploit design + reachability + preconditions → "I know how to win, and what it takes"
W7  BUILD        PoC tooling (forge/deliver/inject) against a reference impl → "the exploit is code, not prose"
W8  VALIDATE-OFFLINE  round-trip + source cross-check + fixtures (no HW) → "the bytes are right"
W9  VALIDATE-LIVE   bench, real target, real RF/transport → "it actually works on the gear"
W10 GAME-DAY     playbook, dry-run, failure modes, handoff → "a teammate can run it under time pressure"
```

Each wicket has a **gate**: a specific piece of evidence that must exist before
you move on. The gates are the honesty contract made concrete — you do not
claim a vuln you haven't evidence-classed, you do not claim a PoC that hasn't
round-tripped, and you do not claim "it works" until it works on real gear.

---

## Gate 0 — FRAME (CTF facts, authorization, and the success condition)

**Goal:** establish what “success” means before selecting an image, an attack
surface, or a PoC. Firmware analysis can be technically correct and still be
irrelevant if it is for the wrong target, an unavailable interface, or a
rule-excluded action.

Record a compact target card before W1:

| Fact | Record | Why it is a gate |
|---|---|---|
| Target identity | Model, board revision, boot/app/radio/GCS versions | Determines whether the downloaded artifact is relevant. |
| Flag condition | Where the flag lives and how it is submitted | A packet being accepted may not score. |
| Exposed interfaces | RF bands/protocols, Wi-Fi, BLE, GCS network, and lab-only ports | Defines what can be reached on game day. |
| Rules and limits | Allowed active traffic, RF power, proximity, recon window, rate limits | Removes invalid paths before implementation. |
| Available equipment | What can be used in the lab versus brought to the CTF | Determines whether the design is deliverable. |
| Lab authority | Exactly which owned targets may be exercised and in what state | Sets the safe validation boundary. |

`SESSION-NOTES.md` §4 is the live owner-question set for this repository. Until
the applicable facts are answered, a target-specific result remains a
*hypothesis*, not a capability. In particular, do not begin active target tool
work merely because a protocol has been recognized.

**Gate 0:** a target card and an authorized lab boundary exist. For this
repository, the E8 exception is absolute: configuration reads only; no flash,
settings write, reset/bootloader entry, RF transmission, or fuzzing.

---

## Wicket 1 — ACQUIRE (the blob + provenance + version pin)

**Goal:** you have a firmware artifact, and you know *exactly* what it is —
source, version, target, hash, collection date, and whether it matches the
hardware you'll test against.

**Why it's a wicket:** every later mistake (wrong base address, wrong
processor, wrong version assumptions) traces back to a provenance gap here.
The TinyHawk corpus had a real one: the target *config* header said "Betaflight
4.3.1 / 2022 build" but the *binaries* were labeled 2026.6.2. Codex flagged it
and we preserved the difference instead of assuming they matched. That
disagreement is exactly why W5 (vuln) had to be version-aware.

**Steps:**
1. **Get the blob from a named source.** Vendor download, a release tag, a
   build you produced, or a capture off the device. Record the URL / commit /
   build command. "Downloaded from the internet, version unknown" is a stop —
   you can't pin what you didn't name.
2. **Hash it.** SHA-256 into `firmware/SHA256SUMS` + the artifact register
   (`firmware/artifact-register.json`). The register records `artifact_kind`
   (downloaded vs. converted), `declared_version`, `declared_target`,
   `source_project`, `hardware_match`, `evidence`. The `hardware_match` field
   is the honest one — for the TinyHawk it's
   `related-flight-controller-sample`, not `confirmed-target`, because the
   corpus binary is a *related* sample, not proven to be the exact image on the
   drone.
3. **Pin the version + target explicitly.** `declared_version` +
   `declared_target` in the register. If the version is a compile-time feature
   (e.g. ELRS OTA v3 vs v4 = `USE_ELRSV4`), pin the *feature selection*, not
   the marketing version. Codex §11.3 in this repo is the model: "do not infer
   the OTA version from the file name, release family, or the lookup script's
   comment."
4. **Note the hardware-match gap.** If the blob is a "related sample" and not
   the confirmed image, say so. The whole analysis is still valid as
   *source-derived* (the code path exists in the version), but the *live*
   validation (W9) is what closes the gap.
5. **Preserve the source alongside the binary.** The TinyHawk corpus commits
   the Betaflight + ELRS *source tarballs* (`firmware/src/`) next to the
   binaries. This is the single highest-leverage acquisition decision — it's
   what let W4/W5 be done from source (authoritative) instead of binary
   (inferential). If you only have the blob and no source, that's a constraint
   you carry into W3/W4/W5 and you say so.

**Gate 1:** a register entry with hash + version + target + source +
`hardware_match`, and the source (or an explicit "no source, binary-only"
note) in the repo.

---

## Wicket 2 — MODEL (target architecture + memory map + toolchain)

**Goal:** you can load the binary into an analyzer *correctly* — right
processor, right endianness, right base address, right memory regions.

**Why it's a wicket:** a wrong base address shifts every xref, every string
offset, every function location by a constant, and the analysis *looks*
plausible while being uniformly wrong. The TinyHawk is a Cortex-M4F at
`0x08000000` (STM32F411 flash start), 512 KiB flash / 128 KiB SRAM. Getting
that right is what made Codex's Ghidra vector-table read (reset at `0x080556C5`)
meaningful.

**Steps:**
1. **Identify the MCU + architecture.** From the target config, the board
   markings, the vendor spec, or the binary's own vector table. The TinyHawk:
   `EMAX_TINYHAWKF4SX1280` → STM32F411CEU6 → ARM Cortex-M4F, little-endian.
2. **Establish the memory map.** Flash base + size, SRAM base + size,
   peripheral regions. The TinyHawk map (Codex's doc §1):
   - Flash `0x08000000–0x0806839E` (416.9 KiB used of 512 KiB)
   - SRAM `0x20000000–0x2001FFF0` (128 KiB)
   - Peripherals `0x40000000–0x50060BFF`
3. **Read the vector table to confirm the base.** The first word is the
   initial SP, the second is the reset vector. The TinyHawk: SP `0x2001FFF0`
   (top of SRAM — consistent), reset `0x080556C5` (Thumb, in flash —
   consistent). If these don't fit your assumed map, your map is wrong.
4. **Pick the toolchain + load settings.** Ghidra: ARM Cortex (LE) v7, base
   `0x08000000`, Raw Binary. IDA: `-p arm:LE`, load at `0x08000000`. Record the
   exact settings (Codex's doc §1 is the template).
5. **Note the tooling constraints.** On this enclave, IDA 9.4 headless would
   not initialize (proc-module load error). The fallback that worked: **the
   pinned source** for buffer-control / data-flow (authoritative), and a
   **Python binary scan** for entropy / strings (no IDA needed). Know your
   tooling limits *before* you need them.

**Gate 2:** a documented load recipe (processor, endianness, base address,
memory map) that the vector table *confirms* (SP + reset fit the map).

---

## Wicket 3 — RECON (symbols, strings, entropy, disassembly lay-of-the-land)

**Goal:** you know what's *in* the binary — the code regions, the function
landscape, the string table, and whether anything is hidden (encrypted /
packed / obfuscated).

**Why it's a wicket:** this is where "high entropy" and "is it obfuscated"
get answered *before* you trust any single-function analysis. A packed binary
will mislead a disassembler; an unobfuscated one (like the TinyHawk) means
what you see is what's there.

**Steps (the four passes, in order):**
1. **Entropy scan.** Slide a window (4 KB) over the binary, compute Shannon
   entropy per window, flag ≥7.5 bits/byte. **Interpretation:**
   - 0 high-entropy windows (the TinyHawk result: overall 6.97, 0 windows
     >7.5, highest 256-byte block 6.63 = Thumb code) → **no encryption, no
     packed blobs, no embedded keys.** Plaintext, analyzable.
   - A high-entropy region in rodata → possible XOR-obfuscated data, an
     embedded key/IV, a compressed/packed section, or a lookup table.
     Investigate *which* before concluding "encrypted."
   - High entropy in the code region → possible VM/obfuscation or a large
     constant table.
2. **String table.** Extract printable strings (min len 4) with offsets.
   **Two uses:** (a) find the protocol / feature names (the TinyHawk: `elrs`,
   `expresslrs`, `msp`, `crsf`, `telemetry`, `failsafe`, `sx1280`, `fhss`,
   `bind` — these *name* the attack surface); (b) find dangerous-function
   names *if* the binary isn't stripped. **Caution:** on a stripped /
   statically-linked ARM build, the "dangerous function" strings you find are
   often state-machine enums (`I_FREEZE`, `SYSTEM_INIT`), not libc calls —
   the TinyHawk scan hit this. Don't read a `free` string as a `free()` call.
3. **Symbol / function inventory.** Count functions, list the ones whose names
   match your protocol keywords (the TinyHawk: the ELRS RX stack in
   `0x08040000–0x08056000`). If the binary is stripped (no symbols), this
   comes from the disassembler's function recovery + the string xrefs.
4. **Disassembly lay-of-the-land.** Read the vector table, the reset handler,
   the main loop, the IRQ handlers. Map the code regions (startup / core
   protocol / CLI / rodata). For the TinyHawk, Codex's Ghidra doc §3 is the
   model — five regions, each with a range and a one-line description.

**Gate 3:** an entropy verdict (obfuscated or not, and where), a string-table
index of the protocol/feature names, and a code-region map. The TinyHawk
verdict: **plaintext, unobfuscated, fully analyzable offline** — which is what
let the rest of the pipeline run from source.

---

## Wicket 4 — SURFACE (attack-surface map: inputs, sinks, trust boundaries)

**Goal:** you know *where untrusted input enters*, *where it goes* (the sinks),
and *what trust boundaries* sit between them. This is the "can I control what's
placed in the buffer?" question, answered structurally before you look for a
specific bug.

**Why it's a wicket:** most firmware bugs are input-to-sink flows that cross a
trust boundary. Mapping the surface first means you look for bugs in the *right
places* instead of reading 400 KB of disassembly randomly.

**Steps:**
1. **Enumerate the inputs.** Every channel that carries data *from outside the
   device's trust boundary*: the RF radio (the TinyHawk's ELRS/SX1280 RX —
   the game-day surface), serial/USB, SPI, I2C, a co-processor, the
   bootloader. For a *wireless* CTF, the RF RX is the primary input; the
   others are lab-only (not game-day-deliverable) and get deprioritized.
2. **Trace each input to its sinks.** For the TinyHawk's ELRS RX path, the
   input is the RF packet in `dmaBuffer`; the sinks are the `memcpy`s and the
   deserializers it reaches:
   ```
   RF packet → dmaBuffer[8]
     ├─ validatePacketCrcStd()          (trust boundary: CRC gate)
     ├─ memcpy(payload, dmaBuffer, 8)   (sink #1: fixed-size, RC data)
     ├─ processRFMspPacket() → mspBuffer[65] → processMspPacket()  (sink #2: the deserializer)
     └─ processRFSyncPacket()           (sink #3: UID/model-match state)
   ```
3. **Identify the trust boundaries.** What *validates* the input before it
   reaches the sink? The TinyHawk: CRC14 (UID-derived init) +
   `connectionState == ELRS_CONNECTED` + `connectionHasModelMatch`. These are
   the gates an attacker must get through — and they define the *preconditions*
   for any exploit (W6).
4. **Classify each sink by its bounds.** For each sink: is the copy size
   fixed or input-controlled? Is there a length field? Is the destination
   heap/stack/fixed-array? The TinyHawk answer that mattered:
   - Sink #1 (RC `memcpy`): **fixed 8 bytes**, no length field → **not an
     overflow** (dead end).
   - Sink #2 (MSP `receiveMspData` → `processMspPacket`): bounded by
     `ELRS_MSP_BUFFER_SIZE=65`, **command injection** (the live finding).
   - Sink #3 (sync): state transition, not a memory sink.

**Gate 4:** a surface map: inputs → trust boundaries → sinks, with each sink
classified (fixed-size / input-sized / deserializer). The TinyHawk map is in
`firmware/vuln-analysis-CRAZYBEEF4SX1280.md` §1 + §3.

---

## Wicket 5 — VULN (candidate vulns, triaged, evidence-classed)

**Goal:** a *ranked, defensible* list of candidate vulnerabilities, each with
an evidence class, each tied to a specific line of code, none overclaimed.

**Why it's a wicket:** this is where "I think there might be a bug" becomes
"I have a triaged candidate with evidence." The evidence classes (from
AGENTS.md §3) are the discipline: **VALIDATED** (demonstrated),
**SELF-CONSISTENT** (internally coherent, not externally confirmed),
**SYNTHETIC** (constructed, not from real data). You do not move a candidate up
a class without the evidence for that class.

**Steps:**
1. **For each sink in W4, ask the right question per sink class:**
   - Fixed-size memory sink → "is the size *really* fixed, or is there a
     hidden length field / a multi-pass accumulation that exceeds it?"
   - Deserializer / command sink → "which commands are reachable, and what do
     they *do*?" (This is where the TinyHawk's real finding came from: the MSP
     deserializer reaches `MSP_REBOOT_*` + `MSP_SET_RX_CONFIG`.)
   - State-transition sink → "can I force the state into a value that
     disables a later check?"
   - Parsing sink → "does the parser trust a length field, an index, or a
     count from the input?"
2. **Cross-check against the source, not just the binary.** The TinyHawk's
   buffer-control verdict was source-derived (`expresslrs.c:846`,
   `expresslrs_common.h:48`) — authoritative, because the source shows the
   actual bounds check. A binary-only verdict is SELF-CONSISTENT at best.
3. **Version-qualify every candidate.** The TinyHawk's candidates are
   version-specific (OTA v3, not v4 — different CRC init, different UID bytes
   in SYNC, different sync channel). A candidate that holds for v3 may not
   hold for v4. Pin the version per candidate.
4. **Triage by game-day relevance.** Rank by (a) is it *wireless-deliverable*
   (the CTF constraint), (b) how many preconditions must hold, (c) how close
   it is to the flag. The TinyHawk ranking:
   1. **MSP-over-ELRS command injection** — wireless, preconditions =
      UID + connect + model-match, reaches reboot/DFU/rebind. **Top candidate.**
   2. **Bind-mode UID takeover** — same sink, but the UID is the known
      `{0,1,2,3,4,5}` (no recovery step). **Fastest win if the target is in
      bind mode.**
   3. RC-data overflow — **dead end** (W4 proved it fixed-size). Removed.
5. **Evidence-class each one.** The TinyHawk's MSP-injection candidate is
   **SELF-CONSISTENT** at the source level (the code path exists and is gated
   only by CRC + connect + model-match) — it is **not** yet VALIDATED (not
   demonstrated on the real drone). That distinction is the whole point of W9.

**Gate 5:** a ranked candidate list, each with: the sink, the code line, the
version, the preconditions, the game-day relevance, and the evidence class.
The TinyHawk list is in `firmware/vuln-analysis-CRAZYBEEF4SX1280.md` §3 + §4.

---

## Wicket 6 — DESIGN (exploit design + reachability + preconditions)

**Goal:** for the top candidate, a concrete exploit design — the exact
sequence of actions, the preconditions that must hold, and the *reachability*
argument (why each precondition can actually be satisfied on the real target).

**Why it's a wicket:** a vuln that can't be reached is a research footnote, not
a PoC. This wicket separates "the bug exists in the code" from "I can actually
trigger it on the target." The TinyHawk design:

```
RECOVER UID   →  air: fhss_seed.py (observe hops, recover the LCG seed)
                 OR  bind-mode UID {0,1,2,3,4,5} (no recovery, if in bind mode)
                 OR  E8 serial read (secondary, not yet proven live)
FORGE         →  elrs_core.ota: CRC14 with the UID-derived init, OTA v3
                 (the version-pinned, validated protocol layer)
SYNC          →  send ELRS_SYNC_PACKET with UID[3]/[4]/[5]
                 → RX enters ELRS_TENTATIVE → ELRS_CONNECTED
MODEL-MATCH   →  send MSP_SET_RX_CONFIG / model ID
                 (OR rely on default modelId=0xFF → modelXor=0x00)
INJECT        →  send the MSP command via processRFMspPacket
                 → processMspPacket: reboot-to-DFU / rebind / config-write
```

**Steps:**
1. **Name each precondition and its satisfier.** UID ← air-recovery /
   bind-mode. Connect ← a valid SYNC packet. Model-match ← the default or a
   SET_RX_CONFIG. Each precondition has a *tool* that satisfies it — if one
   doesn't, the design is incomplete.
2. **Identify the delivery constraint.** The CTF is *wireless*. Every step
   that requires a non-wireless path (USB, serial, physical) is either a
   lab-only setup step or a disqualifier for game day. The TinyHawk: steps
   1–5 are all air-deliverable (once the PHY is in place); the E8 serial read
   is lab-only.
3. **Identify the failure modes.** What makes each step fail on real gear?
   Wrong version (v3 vs v4), wrong PHY (LoRa vs FLRC — the TinyHawk's
   dominant risk, per Claude's finding), model-match not satisfied, the
   receiver already connected to a stronger legitimate TX, the failsafe window
   (1.5 s) expiring mid-takeover. Name them now, not on game day.
4. **Write the design to a doc** (the TinyHawk's is `firmware/
   vuln-analysis-CRAZYBEEF4SX1280.md` §4). It must be runnable by a teammate
   who wasn't in the analysis — that's the test of whether it's a *design* or
   a *vibe*.

**Gate 6:** an exploit design doc with the step sequence, each precondition +
its satisfier, the delivery constraint, and the named failure modes.

---

## Wicket 7 — BUILD (PoC tooling against a reference implementation)

**Goal:** the exploit design becomes *code* — the forge/deliver/inject tools —
built **against a reference implementation**, not a hand-rolled re-derivation.

**Why it's a wicket:** the TinyHawk's P1 bugs (hardcoded OTA v4, wrong MAVLink
wire order, the signing "bypass" that didn't bypass) were all hand-rolled
protocol code that diverged from the reference. The structural fix — and the
lesson of W7 — is **import the validated reference, don't fork it.** The
`elrs_sdr/core` vendored into this repo (W7 in progress) is the reference for
the ELRS protocol layer.

**Steps:**
1. **Pick the reference.** The pinned source (ELRS 4.1.0, Betaflight 2026.6.2)
   for protocol correctness; the validated `elrs_sdr/core` library for the
   CRC/FHSS/OTA/LoRa/FLRC implementations; `pymavlink` for MAVLink. The
   reference is what your PoC is *checked against*.
2. **Build the forge tool.** Emit the exact bytes the reference would. The
   TinyHawk: `elrs-packet-forger.py` (being rewired to `elrs_core` in Step 1)
   emits SYNC/RC/MSP packets with the UID-derived CRC init, version-parameterized.
3. **Build the delivery tool.** The physical-layer encoder + transmitter. The
   TinyHawk: `elrs-tx-emulator.py` (Phase 2 — currently a chirp prototype,
   being rewired to `elrs_core.gfsk`/`lora`). This is where the PHY risk
   (LoRa vs FLRC) lives.
4. **Build the orchestrator.** The tool that chains RECOVER → FORGE → SYNC →
   MODEL-MATCH → INJECT. The TinyHawk: `elrs_chain.py`.
5. **Keep the PoC passive-by-default.** No TX, no hardware access, no state
   change without an explicit `--arm`/`--execute` flag (AGENTS.md's
   "no destructive defaults"). The PoC *can* transmit; it *doesn't* by
   default.

**Gate 7:** the PoC tools exist, are built against the reference, and
pass a no-hardware smoke test (they run, they emit bytes, they don't crash).
The bytes are not yet *proven* correct — that's W8.

---

## Wicket 8 — VALIDATE-OFFLINE (round-trip + source cross-check, no hardware)

**Goal:** prove the PoC emits *correct* bytes, without touching any hardware.
This is the gate between "the exploit is code" (W7) and "the exploit is
*right*" (W8) — and it's the replacement for the synthetic unit-test suites the
owner removed.

**Why it's a wicket:** the TinyHawk's owner directive was "test on live gear,
not synthetic fixtures." W8 honors that by using *real observed data* (a
capture of the actual target link) as the ground truth, not constructed
fixtures. The synthetic round-trip (encode → decode with the same library) is
necessary but **not sufficient** — it only proves self-consistency, not
correctness against the real target.

**Steps:**
1. **Self-consistency round-trip (necessary, not sufficient).** Encode a
   packet with the PoC's forge, decode it with the same reference library,
   assert byte equality. This catches encoding bugs (wrong CRC, wrong field
   order) but *cannot* catch a wrong version or a wrong PHY assumption,
   because the same library is on both sides. **Label this SELF-CONSISTENT.**
2. **Source cross-check (the authoritative offline proof).** Take a *real*
   capture of the target link (the TinyHawk's T1-8 IQ capture, or a fresh one
   on the bench) and compare the PoC's emitted bytes to the bytes actually on
   the air. **Match = the protocol layer is VALIDATED against observed air.**
   This is the strongest offline evidence — it's the real target's bytes, not
   a synthetic one. **A mismatch tells you exactly which field is wrong** (CRC
   init? FHSS seed? version? PHY?) — far more useful than a green self-consistent
   round-trip that never touched real RF.
3. **Version + PHY confirmation from the capture.** The capture tells you the
   actual OTA version (which version's CRC validates) and the actual PHY
   (LoRa vs FLRC — the TinyHawk's dominant risk). Feed *those* measurements
   into the PoC, not assumptions. (This is §12.4a correction #1: do not assume
   v4; measure it.)
4. **Label the evidence class.** A self-consistent round-trip is
   SELF-CONSISTENT. A match against a real capture is VALIDATED (protocol
   layer). The PoC is *not* yet "demonstrated on the target" — that's W9.

**Gate 8:** the PoC's emitted bytes match a real capture of the target (or the
self-consistency round-trip passes *and* the version/PHY are measured from the
capture, not assumed). The evidence class is labeled.

---

## Wicket 9 — VALIDATE-LIVE (bench, real target, real RF/transport)

**Goal:** the PoC works *on the real gear*. This is the wicket that turns a
SELF-CONSISTENT or VALIDATED-protocol PoC into a **demonstrated** one — the
honesty contract's top evidence class.

**Why it's a wicket:** this is where every prior assumption gets tested against
physics. The version was right? The PHY was right? The model-match held? The
receiver accepted the forged frame? The command executed? None of it is known
until this wicket. And it's the one that requires the *owner's real gear* —
the enclave has no SDR, no RF, no target.

**Steps (the TinyHawk sequence, per §12.4):**
1. **Set up the bench.** Target (TinyHawk) powered + bound to the TX (E8, or a
   second ELRS TX). SDR (B205/B210) on the 2.4 GHz band. The owner's gear, per
   AGENTS.md "Hardware in the Lab."
2. **Capture the real link.** Record the target's RF on the SDR. This is the
   ground truth for W8 *and* the input to W9's live test.
3. **Recover the UID (live).** Air-recovery (`fhss_seed.py` from the capture)
   as the primary path; the bind-mode UID as the fast path if the target is in
   bind mode; the E8 read as a secondary confirmation.
4. **Run the PoC against the target.** Forge → (FLRC-modulated) transmit →
   watch the receiver. The acceptance criteria, in order (Codex §11.6's five
   milestones):
   - Milestone 2→3: the forged bytes match the captured air (W8, protocol
     layer).
   - Milestone 4: the receiver state changes (enters TENTATIVE → CONNECTED on
     our SYNC; the LQ/RSSI shows our signal).
   - **Milestone 5: demonstrated receiver behavior** — the target *accepts*
     our frame and *executes* the command (throttle moves, it reboots, it
     rebinds). **This is the PoC.**
5. **Positive-command-only, bench-only.** The standing goal: a *positive*
   command (throttle=1000), on *owned* hardware, *bench-only*. Not a jam, not
   a destructive command, not a third-party target. The no-flash constraint on
   the E8 is absolute.
6. **Record the evidence.** The capture, the PoC output, the receiver's
   response, the exact command, the timestamp. This is the PoC's proof — it
   goes in the repo (redacted: no bind UID, no secrets) as the VALIDATED
   evidence for the vuln candidate from W5.

**Gate 9:** a recorded, repeatable demonstration on the real gear — the target
accepted the forged frame and executed the command. The W5 candidate moves to
**VALIDATED**. The PoC is done.

---

## Wicket 10 — GAME-DAY (playbook, dry-run, failure modes, handoff)

**Goal:** a teammate who wasn't in the analysis can run the PoC under time
pressure, knows what to do when it fails, and knows exactly what the PoC can
and can't do.

**Why it's a wicket:** a PoC that only the analyst can run is not a CTF
capability. The game-day failure mode is not "the exploit doesn't work" — it's
"the exploit works but the operator doesn't know the three commands and the
one failure mode that will actually hit."

**Steps:**
1. **Re-ground the playbook in W9's output.** `docs/game_day_playbook.md`
   describes the *actual* working tool, its exact invocation, its preconditions
   (UID recovery method, version, PHY), and its acceptance criteria. Not the
   hypothetical tool from W6 — the demonstrated tool from W9.
2. **Dry-run under the time limit.** Run the full RECOVER → FORGE → SYNC →
   MODEL-MATCH → INJECT chain in the home lab with a clock. Record where it
   breaks under pressure (the UID recovery is usually the slow step; the
   model-match is the one that silently fails).
3. **Document the failure modes** (from W6 step 4, now *observed* not
   *predicted*): wrong version, wrong PHY, model-match not satisfied,
   legitimate-TX competition, failsafe window. For each: the symptom, the
   diagnosis, the fix.
4. **Document what does NOT work.** The honesty contract: a playbook that
   claims a capability the PoC doesn't have wastes game-day minutes. The
   TinyHawk: the RC-data overflow is a dead end (don't try it); the E8 serial
   read is lab-only (not game-day-deliverable); the MSP injection is the
   demonstrated path.
5. **Handoff.** The playbook + the PoC tools + the W9 evidence, in the repo,
   readable by a teammate. The §12/§13 notes are the context; the playbook is
   the runbook.

**Gate 10:** a playbook grounded in W9, a recorded dry-run, the observed
failure modes, and an honest "what does not work" list. The PoC is a
*game-day capability*, not just a demonstrated proof.

---

## The gates, summarized (the honesty contract as a checklist)

| Wicket | Gate (the evidence that must exist) | Evidence class it produces |
|---|---|---|
| W0 FRAME | target card + rule/authority boundary + defined flag condition | — (scope) |
| W1 ACQUIRE | register entry: hash + version + target + source + `hardware_match` | — (provenance) |
| W2 MODEL | load recipe the vector table *confirms* (SP + reset fit the map) | — (correctness) |
| W3 RECON | entropy verdict + string index + code-region map | — (lay-of-the-land) |
| W4 SURFACE | inputs → trust boundaries → sinks, each sink classified | — (structure) |
| W5 VULN | ranked candidates, each: sink + line + version + preconditions + class | SELF-CONSISTENT (at minimum) |
| W6 DESIGN | step sequence + each precondition's satisfier + delivery constraint + failure modes | — (design) |
| W7 BUILD | PoC tools against the reference, no-hardware smoke test passes | — (code) |
| W8 VALIDATE-OFFLINE | PoC bytes match a real capture (or self-consistent + measured version/PHY) | SELF-CONSISTENT → VALIDATED (protocol) |
| W9 VALIDATE-LIVE | recorded demonstration on real gear: target accepted + executed | **VALIDATED** |
| W10 GAME-DAY | playbook grounded in W9 + dry-run + observed failure modes + "what doesn't work" | — (capability) |

**The two non-negotiables:**
- **You do not move a candidate up an evidence class without the evidence for
  that class.** W5 candidates are SELF-CONSISTENT until W9 demonstrates them.
- **You do not claim "it works" until it works on real gear (W9).** A
  self-consistent round-trip (W8) is not "it works." This is the line the
  TinyHawk project has held throughout — and it's the line that keeps the PoC
  honest.

---

## Cross-cutting PoC contract (applies from W6 onward)

The design is intentionally as important as the bug. A PoC should establish one
bounded primitive on one declared build, not quietly grow into an unreviewable
operational tool. From W6 onward, require each tool to:

- name the accepted model/build/hash and refuse an unknown or mismatched target;
- default to parsing, fixtures, or dry-run behavior; require explicit opt-in
  for authorized live interaction;
- bound its target, packet/input count, duration, retries, and rate;
- include a deterministic `--self-test` and a negative control against an
  independent oracle where one exists;
- log action, tool version, artifact identity, input digest, and result to
  stderr—without committing binding data, credentials, captures, or flags;
- document its stop condition, recovery action, and what an unexpected result
  means.

An encoder and decoder that share the same implementation can agree while both
are wrong. Use the vendor implementation, pinned source, a separate library,
or recorded target traffic as the oracle. This is especially important for
field order, integer widths, CRC coverage, signature coverage, version
selection, PHY/rate assumptions, and state/anti-replay handling.

### Evidence maturity

| Level | Meaning | Permitted claim |
|---|---|---|
| Static lead | Code suggests a condition | Candidate only; not demonstrated. |
| Synthetic | A local fixture or harness exhibits it | Self-consistent; target reachability unknown. |
| Reference-validated | Behavior matches an independent reference or real recorded traffic | Protocol primitive validated. |
| Owned-lab validated | Repeatable result on the matching owned target/build | Lab PoC on the stated interface. |
| CTF-ready | Rules, target match, end-to-end flag condition, and timed dry run are all confirmed | Game-day candidate, not a guarantee. |

### Finding ledger template

Keep one entry per candidate; this preserves useful dead ends as well as wins.

```markdown
### F-YYYY-NNN — concise title

- Status: lead | static | synthetic | reference-validated | owned-lab validated
- Target: model/revision, firmware version, SHA-256, match confidence
- Interface: transport, exposure, required state, and authorization
- Claim: one falsifiable sentence
- Evidence: source file:line and/or binary address; artifact/capture IDs
- Preconditions: build flags, keys, timing, proximity, and CTF rules
- Expected observable: measurable accepted/rejected/state result
- Test plan: fixture -> independent oracle -> owned-lab control + negative control
- Actual result: repetitions, timestamps, and private log/capture locations
- Disproof/limits: what failed and what remains unknown
- CTF relevance: connection to the actual flag condition
- Next action: smallest unanswered question or regression test to add
```

### Definition of done

Before calling a firmware-to-PoC effort complete, the team must be able to
answer: what exact bytes/build/hardware were analyzed; which ingress and trust
boundary are involved; what falsifiable defect exists; what independently
checks the protocol/behavior; what the minimal safe reproducer and negative
control do; what happens on matching owned hardware including recovery; and
how the primitive reaches the CTF's actual flag condition within the rules. A
missing answer means the work is still valuable research, but not a CTF-ready
capability.

---

## Where the TinyHawk is in this pathway (current state, 2026-10-15)

| Wicket | State |
|---|---|
| W1 ACQUIRE | **Done.** Corpus + register + SHA256 + source tarballs. `hardware_match` = related sample. |
| W2 MODEL | **Done.** Cortex-M4F, base `0x08000000`, memory map, vector table confirmed (Codex). |
| W3 RECON | **Done.** Entropy (no obfuscation), string index, code regions, call tree (Codex + andeds §13). |
| W4 SURFACE | **Done.** ELRS RX inputs → CRC boundary → 3 sinks, classified (andeds §13). |
| W5 VULN | **Done.** Ranked: MSP-injection (top), bind-mode UID (fastest), RC-overflow (dead end). SELF-CONSISTENT. |
| W6 DESIGN | **Done.** The 5-step chain (RECOVER→FORGE→SYNC→MODEL-MATCH→INJECT) + preconditions + failure modes (andeds §13 §4). |
| W7 BUILD | **In progress.** `elrs_core` vendored (protocol layer); forger/analyzer/chain **not yet rewired** (Step 1); TX emulator is a chirp prototype (Phase 2). |
| W8 VALIDATE-OFFLINE | **Not started.** Needs the rewired tools (W7) + a real capture to cross-check. |
| W9 VALIDATE-LIVE | **Not started.** Needs W7 + W8 + the owner's bench (TinyHawk + E8 + B205). |
| W10 GAME-DAY | **Not started.** Needs W9. |

**The immediate next wicket is W7 completion (Step 1: rewire the tools to
`elrs_core`), then W8 (offline validation against a real capture), then W9
(live, on the owner's bench).** W1–W6 are done; the analysis phase is
complete. The PoC phase (W7→W10) is what's left.

— andeds, orchestrator, 2026-10-15. The firmware→PoC pathway, ten wickets,
grounded in the TinyHawk run. The gates are the honesty contract made concrete.
