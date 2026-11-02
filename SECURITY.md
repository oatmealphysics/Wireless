> **Merged repo (2026-10-16).** This repository now holds **two workstreams**
> that were previously separate GitLab repos:
>
> - **`tools/`, `web/`, `firmware/`, `research/`, `Harness/`** — wireless drone
>   CTF offense + E8/TinyHawk PoC research (the original `poc-research`).
> - **`elrs_sdr/`, `waveraider/`** — the ELRS SDR passive detector
>   (merged in from `andeds-agent/elrs-sdr-transfer`, which is now retired).
>
> **`SESSION-NOTES.md` is the single shared running log for both workstreams.**
> The per-agent rolling logs (`docs/*_NOTES_FOR_BOTH.md`, `docs/CLAUDE_NOTES_FOR_ANDEDS.md`)
> are **retired** (operator-authorized 2026-10-16) — history only. **`SESSION-NOTES.md`
> is the single active log for every workstream and every agent.** See entry #72.

# Security and Safety Guardrails

This file defines the operating guardrails for research code and laboratory
work in this repository. It does not grant authorization. The controlling
scope is the project owner's direction in [`AGENTS.md`](AGENTS.md), especially
**Security and Scope**, **Owned E8 and TinyHawk: Absolute No-Flashing Constraint**, and
any device-specific handoff such as
[`research/E8-COLLABORATION.md`](research/E8-COLLABORATION.md).

If a document, tool, experiment plan, or older note conflicts with a current
owner constraint, the current constraint wins. A blueprint is a method for
working inside an authorized boundary; it cannot expand that boundary.

## Authorization boundary

- Work only on owned laboratory hardware or a competition target that the
  rules explicitly authorize.
- Record the target, interface, allowed actions, prohibited actions, location,
  and approving owner before live interaction.
- Treat third-party, production, public, and shared RF environments as out of
  scope unless the owner supplies specific written authorization and the
  applicable rules permit the action.
- A successful passive observation does not authorize an active probe. A
  successful read does not authorize a write. A documented update path does
  not authorize flashing it.
- Device-specific restrictions override generic tool capabilities and later
  research ideas. The EMAX E8 and TinyHawk III Plus have an absolute no-flash
  rule in `AGENTS.md`. The E8's additional no-write/no-reset/no-fuzz/no-RF-TX
  characterization boundary also remains in force.

## Default posture

The default is **offline or passive, read-only, and non-transmitting**:

- analyze copies of artifacts rather than the only physical device;
- receive and record rather than transmit;
- query documented read-only interfaces rather than change state;
- use fixtures, emulators, parsers, and independent oracles before hardware;
- keep radios disabled, disconnected, shielded, or terminated unless a
  separately approved test requires transmission;
- keep propellers removed and actuators physically safe whenever powered
  control hardware is on the bench;
- run one process per serial/debug port and coordinate ownership of shared
  hardware.

## Required gates for state-changing work

Each of the following needs its own explicit owner sign-off. Approval for one
does not imply approval for another:

1. configuration or calibration write;
2. reset, bootloader entry, pairing, binding, or mode change that persists;
3. firmware readout or memory dump when access controls or secrets may be
   involved;
4. crafted-input or fuzzing test against live hardware;
5. live output or transmission, including RF/network replay, injection,
   interference, actuator drive, or another physical effect;
6. firmware update, recovery, erase, restore, or flash (this gate is **not
   available** for the owned E8 or TinyHawk under their absolute prohibition);
7. opening the enclosure, attaching debug probes, or modifying hardware.

The sign-off record must name the exact target and revision, action, tool,
expected effect, maximum duration/count/rate/power, recovery path, stop
conditions, operator, date, and private evidence location. Approval must be
re-obtained when any of those materially changes.

## Tool guardrails

Tools that can transmit, drive an output, write, inject, erase, reset, or flash
must:

- default to parse, inspect, preview, or dry-run behavior;
- require an explicit action flag such as `--arm`, `--execute`, or `--flash`;
- name or verify the expected target model, revision, protocol, and version;
- reject an unknown, ambiguous, or mismatched target unless the operator uses
  a separately documented override under an approved plan;
- bound target, payload count, duration, retries, rate, RF power, and physical
  output as applicable;
- display the exact intended action before execution;
- make live and destructive actions visually distinct in `--help` and logs;
- log timestamps, tool revision, target identity, input digest, action, and
  result to stderr without logging credentials or private identifiers;
- stop safely on timeout, partial response, disconnect, ambiguous state, or an
  unexpected target reply;
- never treat a port becoming available as a hardware-access handoff.

For firmware operations, `--flash` is only a software interlock. It is not
authorization. A device-specific prohibition can make that path unavailable
under all circumstances; this applies to both the E8 and TinyHawk.

## Artifact and code handling

- Do not execute newly downloaded binaries, installers, host tools, macros, or
  firmware utilities during acquisition. Hash and inspect them first.
- Prefer static unpacking in an isolated workspace. If dynamic analysis is
  later justified, use a disposable VM or sandbox with no secrets and the
  minimum USB/network access needed.
- Record source URL, retrieval time, SHA-256, declared model/version, signing
  status, packaging, and exact-hardware match for every artifact.
- Preserve originals as read-only evidence. Work from copies and record every
  conversion or unpacking step.
- Never commit credentials, tokens, binding phrases, device UIDs, flags,
  private captures, or identifying configuration. Store them under ignored
  `work/` paths and use redacted or synthetic fixtures in the repository.
- Do not silently replace an artifact. Add a new version and preserve the
  provenance relationship.
- Do not conflate a host application, update package, bootloader, radio image,
  or main application firmware. Classify each component independently.

## Live bench controls

Before powering a device:

- verify connector pinout, voltage, current limit, polarity, and ground;
- photograph and label the setup before moving leads;
- use current-limited power where practical and set a stop threshold;
- remove propellers, disconnect hazardous loads, and secure moving parts;
- place outputs in their verified safe state; isolate any approved live output
  with a bench loopback, simulator, dummy load, shielding, attenuation, or a
  physically restrained owned test article as appropriate;
- ensure only the designated operator owns each serial/debug port;
- establish baseline behavior and a clean disconnect/power-down procedure.

Stop immediately on excess current, heat, odor, swelling, unexpected motion,
unexpected RF, loss of console/control, repeated resets, filesystem errors,
unknown boot state, or any result outside the approved plan. Preserve the
state and logs; do not improvise a recovery write.

## Claims and disclosure

- Label facts as observed, source-derived, binary-derived, reproduced
  offline, validated on the owned device, hypothesis, or contradicted.
- A static code path is not proof that the installed build contains it. A
  self-consistent encoder/decoder is not an independent protocol validation.
- Record negative and inconclusive results. Brief silence does not prove the
  absence of a console, service, or radio.
- Tie vulnerability claims to a specific artifact hash, model/revision,
  interface, preconditions, and observable result.
- Keep reports scoped to the owned lab target and authorized CTF use. Follow
  the vendor or competition disclosure process if a previously unknown issue
  affects others.

## Review before merge

For a tool with live or state-changing capability, review at minimum:

- safe default and explicit arming path;
- target and version checks;
- hard bounds on effects;
- log redaction;
- timeout and partial-failure behavior;
- recovery and stop instructions;
- tests for the dry-run/preview path and safety interlocks;
- documentation that distinguishes capability from authorization.

The repeatable end-to-end process is in
[`docs/new-hardware-research-blueprint.md`](docs/new-hardware-research-blueprint.md).
Its field checklist is
[`docs/new-hardware-quick-reference.md`](docs/new-hardware-quick-reference.md).
