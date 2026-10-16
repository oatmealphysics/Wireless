# AGENTS.md — Wireless Drone CTF Research Project

## Project Overview

This is an offensive security research project preparing for a wireless drone CTF competition. The attack team's job is to deny, disrupt, or commandeer target drones and capture flags from ground control stations — all delivered wirelessly on game day.

A teammate handles defense. This repo is exclusively offense.

## Multi-Agent Workflow

This repo is co-edited by **Claude Code** and **Codex**, back and forth. Both agents must follow these rules:

1. **Always `git pull` before editing.** The other agent may have pushed changes since your last session.
2. **Read files fresh** — never rely on cached state from a prior session or context summary.
3. **Commit and push after making changes** so the other agent picks up your work.
4. **Watch for new sections, restructured content, or corrections** the other agent may have introduced.
5. **Never force-push or rewrite history.** Both agents work on `main`.
6. **Serialize access to the connected E8.** See `research/E8-COLLABORATION.md`.
   Codex has stopped opening COM4 while Claude may be testing. Do not infer a
   hardware-access handoff from a free port; confirm it with the other operator.
7. **Use `SESSION-NOTES.md` as the single shared running log.** Read its latest
   entries before starting work and append a dated, agent-attributed entry before
   handoff. Record the starting revision, objective, actions, evidence, what
   worked, what failed or remained inconclusive, safety boundaries, changed
   files, validation, commit/push status, and concrete next steps. Preserve
   earlier entries; correct them with a new cross-referenced entry rather than
   rewriting history. Keep secrets and raw private device data under ignored
   `work/` and put only sanitized conclusions in the log.

## Owned E8 and TinyHawk: Absolute No-Flashing Constraint

The owner explicitly instructed: **Do not flash the EMAX E8 controller or the
TinyHawk III Plus drone with anything under any circumstances.** This overrides
generic tool flags, separate-approval language, and research plans below.
For either device, do not upload, erase, replace, restore, or update firmware;
do not run bootloader loaders or firmware update tools against it. An earlier
probe or research document is not authorization to repeat those operations.

Current controller work is identification and normal configuration reads only.
Do not write settings, issue reset/bootloader-entry sequences, fuzz the live
device, or transmit RF as part of this characterization. Preserve private
configuration/captures under ignored `work/`. Entering the documented serial
mode does not authorize any update operation.

For the TinyHawk, continue only passive capture, offline analysis, and
separately authorized read-only identification within the active ELRS
benchmark. No firmware update, recovery flash, or reflash is an available
validation or repair step. Other state-changing work still has its own gate
under `SECURITY.md`; the no-flashing rule cannot be bypassed by that gate.

## Repository Structure

```
Harness/
├── drone-wireless-vuln-reference.md   # ~1500-line vulnerability reference (CVEs, chipsets, attack techniques)
├── attack-research-plan.md            # 7-phase attack research plan with lab exercises
├── firmware/                          # Firmware corpus for offline analysis
│   ├── README.md                      # Hardware→chipset mapping and attack surface analysis
│   ├── betaflight/                    # Betaflight firmware binaries and unified target configs
│   │   ├── betaflight_CRAZYBEEF4SX1280.bin    # Raw binary for Ghidra (STM32F411, ARM Cortex-M4, base 0x08000000)
│   │   ├── betaflight_2026.6.2_STM32F411_CRAZYBEEF4SX1280.hex
│   │   ├── EMAX_TINYHAWKF4SX1280.config       # Pin mapping, SPI buses, SX1280 GPIO
│   │   └── ...
│   ├── src/                           # Source archives (extract tarballs for analysis)
│   │   ├── expresslrs-4.1.0.tar.gz    # ExpressLRS 4.1.0 source
│   │   ├── betaflight-2026.6.2.tar.gz # Betaflight source (SPI ELRS RX driver)
│   │   └── .gitignore                 # Extracted trees excluded from git
│   └── reference/                     # ELRS target registry, Lua scripts
│       └── targets.json               # EMAX target definitions (ESP32 + SX1280)
├── tools/                             # Attack scripts and utilities
│   ├── elrs-uid-rainbow.py            # Binding phrase → UID rainbow table generator
│   ├── elrs-rainbow-table.csv         # Pre-computed table (193 phrases)
│   ├── elrs-rainbow-table.json        # Same, JSON format
│   └── elrs-serial-probe.py           # CRSF serial interface probe for USB-connected ELRS devices
└── firmware_repo/                     # (reserved for future firmware builds)
```

## Hardware in the Lab

| Device | Role | MCU | RF Chip | Protocol |
|---|---|---|---|---|
| **EMAX TinyHawk III** | Target drone | STM32F411CEU6 | SX1280 (SPI) | ELRS baked into Betaflight |
| **EMAX E8 controller** | TX / attack TX | Unconfirmed (ESP32 is a related-module hypothesis) | Unconfirmed for this unit | ELRS variant reported; installed version unconfirmed |
| **FPV headset** | Monitor FPV feed | — | — | Analog |
| **Ettus B205mini** | SDR transceiver | — | — | GPS spoofing, custom RF |
| **Ettus B210** | SDR transceiver | — | — | Full-duplex, higher BW |
| **Multiple ALFA cards** | WiFi injection | — | — | Monitor mode, deauth, rogue AP |
| **Ubuntu lab machine** | Attack platform | — | — | Runs all tooling |

Serial/USB access is available for lab development and debugging. All attacks must work wirelessly on game day.

## Key Technical Context

### ELRS Binding Phrase Security Model

The binding phrase derives the pairing UID; it is not encryption. For the live
Betaflight 4.4.2 v3 receiver, the source-derived model is:

1. Binding phrase → MD5 hash → first 6 bytes = UID
2. UID[4] and UID[5] are visible in every SYNC packet; this is only 16 bits of
   the six-byte UID, not the full pairing value
3. UID seeds the FHSS hopping sequence (240 entries for the 80-channel 2.4 GHz domain)
4. UID seeds the CRC initializer (CRC14 or CRC16)
5. No encryption on any OTA packet

Knowing the complete UID permits a source-derived packet candidate. Whether
the live receiver accepts it remains a separate bench-validation question.

### Betaflight Target for TinyHawk III

- Unified target: `EMAX_TINYHAWKF4SX1280` on `CRAZYBEEF4SX1280` base firmware
- SPI ELRS receiver — no separate ESP chip; ELRS stack compiled directly into Betaflight
- Gyro: MPU6000 on SPI bus 1
- SX1280: SPI bus 3, CS=PA15, EXTI=PC14, RESET=PA8, BUSY=PA13

### ExpressLRS TX Targets for E8

- No E8-specific entry in the official ELRS target registry
- Closest targets: `EMAX_2400_TX` (OLED) and `EMAX_NANO_2400_TX` — both ESP32 + SX1280
- Firmware base: `Unified_ESP32_2400_TX`
- Upload methods: UART, WiFi (WiFi mode is an attack surface)

These target/MCU details describe related EMAX modules, not an identified E8
firmware image. See `HARDWARE-IDENTITY.md` before selecting a device target.

## Guidelines for Agents

### Writing Research Documents

- **Accuracy over volume.** Flag unverified CVEs. Distinguish "advisory says RCE" from "public PoC demonstrates RCE." Record firmware build, patch level, and transport for every claim.
- **Keep documents navigable.** These are lookup references under time pressure on game day. Use tables, short entries, and cross-references.
- **Pin versions.** When citing a tool, firmware, or CVE, include the version, commit, or date. Things change.
- **Separate fact from hypothesis.** If a technique should work based on source analysis but hasn't been lab-tested, say so.

### Writing Tools and Scripts

- **All scripts go in `tools/`.** Name them descriptively: `elrs-uid-rainbow.py`, not `script1.py`.
- **Include usage docs** in the script's docstring or `--help`. A teammate should be able to run it without reading the source.
- **Target Python 3.10+** on Ubuntu. Prefer stdlib; if a dependency is needed, note it at the top of the script.
- **No destructive defaults.** Scripts that transmit, inject, or modify firmware must require explicit flags (`--arm`, `--execute`, `--flash`). Default mode should be read-only or dry-run.
- **Log what you do.** Print actions to stderr so the operator knows what's happening under time pressure.

### Working with Firmware

- **Provenance matters.** Record source URL, version, commit hash, and SHA-256 for every firmware file. Update `firmware/README.md` and `firmware/SHA256SUMS`.
- **Don't commit extracted source trees** — they're too large. Commit the tarballs and add extracted directories to `.gitignore`. Re-extract with `tar xzf`.
- **Ghidra load settings for Betaflight binaries:** Processor = ARM Cortex (little endian), base address = `0x08000000` (STM32 flash start).
- **Use REA** (`reverse-engineer-anything` skill) for firmware binary analysis when available. It's the right tool for decompiling and inspecting the `.bin` files.

### Security and Scope

- This is authorized CTF preparation against our own equipment. All testing targets hardware we own.
- Never commit credentials, tokens, or secrets. The GitHub PAT leak earlier in this project is a reminder.
- Scripts and techniques documented here are for competition use. Frame everything as CTF research.

### What NOT to Do

- Don't reorganize the document structure without discussion. Both agents and the user have mental models of where things are.
- Don't delete content the other agent wrote — update or annotate it instead.
- Don't add speculative features or abstractions. If a script does one thing well, that's enough.
- Don't commit binary blobs without documenting what they are and where they came from.

## Current State and Open Work

> **The way ahead is in [`SESSION-NOTES.md`](SESSION-NOTES.md).** Read §1–§7
> for the orchestrator's code-level review and P1 defect list, §16–§17 for the
> 4.4.2 rewire and vendored-tool fixes, §19 for the v3 correction and RF-TX
> safety gate, and **§20 for the current owner-directed ELRS pair benchmark**.
> §18 and [`research/layer2-wireless-attack-research.md`](research/layer2-wireless-attack-research.md)
> are earlier exploratory work, not the active path.

### Owner direction (2026-10-15): benchmark the owned ELRS pair first

The owner redirected the first benchmark to the **EMAX E8 controller and
TinyHawk III ELRS drone as a pair**, without a WiFi entry step. The previous
WiFi-first Chain 1 plan is paused. Establish measured, version-matched ELRS
behavior before making any claim about forged-packet acceptance. MAVLink/WiFi,
DJI/BLE, and RF disruption are deferred from this benchmark. The month-scale
target remains a planning horizon, not evidence that any stage is operational.

### Done
- Vulnerability reference document covering WiFi, BLE, ELRS, MAVLink, GPS, chipset exploitation (~1555 lines)
- 7-phase attack research plan with lab exercises
- Firmware corpus: Betaflight 4.4.2 + 2026.6.2, ELRS 4.1.0, ArduPilot 4.7.2, PX4 1.18.0-rc1, Tello SDK (24 artifacts, SHA256-verified)
- ELRS binding phrase rainbow table generator (723 phrases, reverse SYNC lookup)
- CRSF serial probe tool for USB-connected ELRS devices
- 5 exploit chains (ELRS / MAVLink / Wi-Fi-Tello / multi-recon) + supporting tools
- Differential review + orchestrator re-verification (SESSION-NOTES §1–§3)
- Live TinyHawk III firmware identification: Betaflight 4.4.2 (not 2026.6.2)
- Complete ELRS 4.4.2 vs 2026.6.2 source diff (attack parameters confirmed for v3)
- ELRS consumer-tool OTA v3 corrections (§19); the vendored core remains v4-oriented
- Layer 2 wireless attack surface analysis and target landscape mapping

### In Progress (E8–TinyHawk ELRS pair benchmark)
- Preserve the known read-only device configuration and source-matched
  Betaflight 4.4.2 facts; keep raw UID and captures private under `work/`.
- Obtain or locate a passive capture of this exact pair during normal operation.
  Re-check older FLRC labels against the source-derived LoRa configuration.
- Decode real frames and compare PHY, FHSS, CRC, and nonzero-nonce behavior
  against the version-matched source; do not treat synthetic round-trips as proof.
- Resolve v3 behavior in the companion ELRS core and re-vendor with provenance.
- Keep ELRS RF transmission disabled until a real LoRa PHY and a separately
  authorized, safe bench-validation plan exist.

### Paused or deferred
- E8 WiFi-mode exploitation, RF disruption/jamming, and WiFi config extraction.
- MAVLink/WiFi and DJI/BLE work while the first ELRS pair benchmark is active.

### Other device evidence
- A native, read-only E8 configuration read has been reported and documented
  in `research/e8-protocol-reconstruction.md`; it does not identify the
  installed firmware version or authorize additional serial operations.

### Not Yet Started
- Chain 3: DJI BLE exploitation (after Chain 1 proven + DJI hw acquired)
- Phase D: game-day playbook re-grounded in live RF output
- Firmware binary analysis in Ghidra (4.4.2 binary ready)
- ESP32 BLE exploitation (BrakTooth, if E8 BT is active)

### Open Questions (partially resolved — see SESSION-NOTES §4 and §18)
- CTF target platform(s) → **Q1** (ELRS path chosen regardless; still informs Chain 2/3 priority)
- RF rule details → **Q4** (needed before any later active RF bench stage;
  no jamming is part of the first benchmark)
- Scoring model — commandeering vs DoS? → **Q5**
- Physical layout / proximity? → **Q7**
- E8 installed firmware version and radio details remain unconfirmed; no
  WiFi capability is assumed for this benchmark
- (plus Q2, Q3, Q6, Q8, Q9 in SESSION-NOTES §4)
