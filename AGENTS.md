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
| **EMAX E8 controller** | TX / attack TX | ESP32 | SX1280 | ExpressLRS standalone |
| **FPV headset** | Monitor FPV feed | — | — | Analog |
| **Ettus B205mini** | SDR transceiver | — | — | GPS spoofing, custom RF |
| **Ettus B210** | SDR transceiver | — | — | Full-duplex, higher BW |
| **Multiple ALFA cards** | WiFi injection | — | — | Monitor mode, deauth, rogue AP |
| **Ubuntu lab machine** | Attack platform | — | — | Runs all tooling |

Serial/USB access is available for lab development and debugging. All attacks must work wirelessly on game day.

## Key Technical Context

### ELRS Binding Phrase Security Model

The binding phrase is the sole shared secret between TX and RX. It is not encryption — it is anti-collision. The attack chain is fully documented in `firmware/README.md`:

1. Binding phrase → MD5 hash → first 6 bytes = UID
2. UID[3:5] are sent **in plaintext** in every SYNC packet
3. UID seeds the FHSS hopping sequence (deterministic, 256-entry, 80 channels for 2.4 GHz)
4. UID seeds the CRC initializer (CRC14 or CRC16)
5. No encryption on any OTA packet

Know the binding phrase → know every hop → forge valid packets → override the legitimate TX.

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

### Done
- Vulnerability reference document covering WiFi, BLE, ELRS, MAVLink, GPS, chipset exploitation (~1555 lines)
- 7-phase attack research plan with lab exercises
- Firmware corpus: Betaflight hex/bin, ELRS source, unified target configs
- ELRS binding phrase rainbow table generator (193 phrases, reverse SYNC lookup)
- CRSF serial probe tool for USB-connected ELRS devices

### In Progress
- Serial testing of EMAX E8 over USB (needs battery power)
- Layer 2 / chipset-level attack research program design

### Not Started
- Attack scripts listed in the research plan (`tools/` — 15 scripts outlined)
- Firmware binary analysis in Ghidra
- Lab exercises from the plan (WiFi deauth, PMKID, Tello takeover, MAVLink injection, etc.)
- Additional hardware acquisition (Tello, Pixhawk, ESP32 dev board, etc.)
- Game-day playbook automation

### Open Questions
- What drones/protocols will the CTF use? (Unknown target platforms)
- Is RF jamming allowed? Is GPS spoofing allowed?
- Scoring model — is commandeering worth more than DoS?
- Physical layout — how close can attackers get to targets?
- Is there a flag format or submission API?
