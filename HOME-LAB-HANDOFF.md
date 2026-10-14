# Wireless Drone CTF Research — Detailed Home Lab Handoff

> Written: 2026-10-14. Repository baseline reviewed: `93578f3ae305f52f170088bfdcc3c57f0e744151`.
> Purpose: preserve the working context, evidence, limitations, and next steps so research can continue from another machine without reconstructing the conversation.
> The firmware collection is for offline test articles. The owner explicitly said they do not plan to flash the EMAX E8.

## Navigation

- [1. Start here at home](#1-start-here-at-home)
- [2. Project objectives and decisions](#2-project-objectives-and-decisions)
- [3. What exists and what remains incomplete](#3-what-exists-and-what-remains-incomplete)
- [4. Repository map and collaboration](#4-repository-map-and-collaboration)
- [5. Session and commit history](#5-session-and-commit-history)
- [6. Hardware identity and confidence](#6-hardware-identity-and-confidence)
- [7. Firmware acquisition findings](#7-firmware-acquisition-findings)
- [8. Complete artifact inventory](#8-complete-artifact-inventory)
- [9. Integrity checks and binary import settings](#9-integrity-checks-and-binary-import-settings)
- [10. Source extraction and offline navigation](#10-source-extraction-and-offline-navigation)
- [11. ExpressLRS observations and version distinctions](#11-expresslrs-observations-and-version-distinctions)
- [12. Existing lookup tool](#12-existing-lookup-tool)
- [13. Existing serial probe](#13-existing-serial-probe)
- [14. USB and serial discovery](#14-usb-and-serial-discovery)
- [15. Codex permissions and troubleshooting history](#15-codex-permissions-and-troubleshooting-history)
- [16. Chipset research program](#16-chipset-research-program)
- [17. Reading the existing vulnerability reference critically](#17-reading-the-existing-vulnerability-reference-critically)
- [18. Suggested work sessions at home](#18-suggested-work-sessions-at-home)
- [19. Evidence templates](#19-evidence-templates)
- [20. Open questions and outstanding work](#20-open-questions-and-outstanding-work)
- [21. Source links](#21-source-links)
- [22. Copy-ready continuation prompt](#22-copy-ready-continuation-prompt)

## 1. Start here at home

The fastest useful continuation is to obtain the current repository, verify its artifacts, extract the two pinned source archives, and investigate the source/version discrepancies documented below. None of those steps requires the E8 to be connected.

Use an existing checkout if one is already present. Before pulling, inspect local changes so work from the other assistant or from another machine is not overwritten:

```bash
cd /path/to/research-project
git status --short
git pull --ff-only
git log -8 --oneline
sha256sum -c firmware/SHA256SUMS
```

For a new checkout, the actual repository is `oatmealphysics/research-project`:

```bash
git clone git@github.com:oatmealphysics/research-project.git
cd research-project
git status --short
sha256sum -c firmware/SHA256SUMS
```

The lab machine uses the SSH host alias `github-personal`. That alias is machine-specific and may not exist at home. The plain `github.com` clone command above requires the home machine's own working GitHub SSH authentication. Do not copy private SSH keys or access tokens into this repository.

Read these files in this order:

1. [AGENTS.md](AGENTS.md): collaboration rules and project context.
2. This handoff: observed state, caveats, and restart instructions.
3. [firmware/README.md](firmware/README.md): corpus overview and source pointers.
4. [attack-research-plan.md](attack-research-plan.md): broader competition research plan.
5. [drone-wireless-vuln-reference.md](drone-wireless-vuln-reference.md): chipset/protocol vulnerability lookup reference.

If the E8 is unavailable at home, begin with source analysis and artifact provenance. If it is available, identify the actual USB device path again before using any serial tool. `/dev/ttyACM0` was an earlier observation, not a portable device identity.

The highest-priority next deliverables are:

- A hardware identity record for the particular E8, separating verified markings and version output from guesses based on other EMAX products.
- A version-aware explanation of ExpressLRS SYNC fields and the existing lookup tool's three-byte assumption.
- A source-level map of input validation and packet-processing paths in the pinned TX and RX sources.
- A documented route to actual E8 firmware test articles, if a public manufacturer image or authorized readback becomes available.

## 2. Project objectives and decisions

### 2.1 Original research request

The project began as an extensive open-source research reference for a wireless drone CTF. The requested emphasis was chipset and Layer 2 vulnerabilities that could produce denial of service or remote code execution. The user wanted a robust Markdown document with indexes, tables, navigable headings, primary references, affected versions, and practical CTF relevance.

Research was subsequently expanded toward heap/buffer overflows, allocator corruption, function-pointer overwrites, return-oriented programming, information leaks, and controller-to-host escalation. A separate research/implementation plan was created for work against the owner's equipment.

The broader plan also discusses protocol weaknesses in WiFi, MAVLink, Bluetooth/BLE, ELRS and other RC links, proprietary drone links, and ground-control-station services. Those are useful surrounding research areas, but a protocol weakness and a chipset memory-corruption vulnerability are different findings and should retain different evidence requirements.

### 2.2 Competition context

The existing project plan describes an offensive CTF role: disrupt or commandeer drones and obtain flags from ground control stations. A teammate handles defense. The competition target models, scoring, permitted RF activities, and flag submission details remain unknown.

Serial/USB/debug access is useful in the lab for observation and diagnosis. The plan's competition constraint is that delivery on game day must be wireless. A problem reached only through USB is therefore an offline research result until a separate wireless input path is established.

### 2.3 Current firmware request

The user requested all available firmware for their EMAX E8 ELRS transmitter, stored in `./firmware`, plus ExpressLRS GitHub material for reference. They later clarified:

> “I do not plan on flashing this thing, I just want the firmwares as test articles.”

This clarification matters for continuing the work. The useful product is an attributable corpus of binaries, source releases, target layouts, and notes. An image can be worth analyzing even when its compatibility with the connected E8 is unproven. Its identity must still be recorded accurately: an EMAX OLED module image must not become an “E8 image” merely because the devices share a brand or RF chip.

The collection is incomplete with respect to actual E8-specific binaries and historical E8 releases. Do not describe it as an exhaustive E8 firmware archive.

### 2.4 Working preferences

- Codex and Claude Code take turns working on the same repository.
- Pull at the beginning, reread current files, commit and push finished work at the end.
- Preserve the other assistant's work and annotate questionable claims with evidence.
- Prefer autonomous progress on authorized research and reversible local work.
- Keep implementation and source claims tied to a release, commit, file, or experiment.
- No firmware flashing was requested for this collection or this handoff.

## 3. What exists and what remains incomplete

| Area | Current state | What this establishes |
|---|---|---|
| Vulnerability reference | Large Markdown reference exists | A research index exists; individual claims still need version/source checks |
| Research plan | Seven-phase Markdown plan exists | A set of planned exercises; not evidence those exercises were completed |
| Initial ELRS collection | Master source archive, target registry archive/JSON, release Lua script | Offline reference snapshots, not an E8 firmware dump |
| Additional corpus | Betaflight HEX/BIN images, target config, tagged ELRS and Betaflight source | Real binary test articles and pinned sources for related TX/RX analysis |
| E8-specific image | None positively identified in the reviewed corpus | Exact E8 image acquisition remains open |
| Installed E8 firmware version | Not established by a device response in this session | Release/source selection cannot yet be matched to the actual transmitter |
| Lookup tool | Python script and 193-entry CSV/JSON tables exist | Offline derivation and candidate lookup; no verified match to this E8 |
| Serial probe | Python script exists | A candidate CRSF probing utility; E8 USB protocol compatibility unconfirmed |
| USB identification | Generic USB CDC device observed earlier | A serial interface was visible; descriptors did not prove MCU or ELRS target |
| Firmware integrity | Hashes recomputed; HEX/BIN correspondence checked during handoff | File integrity and conversion consistency, not device compatibility |
| Chipset exploit | No new working exploit produced in this session | Published techniques are research leads, not reproduced capabilities |
| E8 access configuration | Narrow permission profile saved in user config | Saved configuration exists; successful effective hardware access still needs a fresh-session check |

At the handoff inspection, no `/dev/ttyACM*` device or `/dev/serial/by-id` alias was visible to this execution environment. This is a current observation; it does not establish whether the transmitter was unplugged, powered off, or hidden from the execution environment.

## 4. Repository map and collaboration

### 4.1 Current files

```text
research-project/
├── AGENTS.md
├── HOME-LAB-HANDOFF.md
├── README.md
├── drone-wireless-vuln-reference.md
├── attack-research-plan.md
├── firmware/
│   ├── README.md
│   ├── SHA256SUMS
│   ├── betaflight/
│   │   ├── EMAX_TINYHAWKF4SX1280.config
│   │   ├── betaflight_2026.6.2_STM32F411_CRAZYBEEF4SX1280.hex
│   │   ├── betaflight_CRAZYBEEF4SX1280.bin
│   │   ├── betaflight_2026.6.2_STM32F411.hex
│   │   └── betaflight_STM32F411.bin
│   ├── reference/
│   │   ├── ExpressLRS-master.tar.gz
│   │   ├── ExpressLRS-targets-master.tar.gz
│   │   ├── targets.json
│   │   └── elrs-4.1.0.lua
│   └── src/
│       ├── .gitignore
│       ├── expresslrs-4.1.0.tar.gz
│       └── betaflight-2026.6.2.tar.gz
└── tools/
    ├── elrs-uid-rainbow.py
    ├── elrs-rainbow-table.csv
    ├── elrs-rainbow-table.json
    └── elrs-serial-probe.py
```

Extracted source directories may also be present locally. They are intentionally ignored and will not appear after cloning until extracted. A local `.claude/` directory was untracked when this handoff began; it has not been included in the handoff commit.

### 4.2 Session workflow

Before editing:

```bash
git status --short
git pull --ff-only
git log -6 --oneline
git diff --stat
```

If `git pull --ff-only` reports diverged branches, inspect both sides before deciding how to reconcile them. Do not force-push, reset away work, or automatically stash another assistant's edits. `git log --left-right --graph --oneline HEAD...origin/main` is a useful diagnostic after a successful fetch.

At completion, stage explicit files rather than the whole working tree:

```bash
git add HOME-LAB-HANDOFF.md README.md firmware/README.md firmware/SHA256SUMS
git diff --cached --stat
git diff --cached --check
git commit -m "Document home lab handoff and firmware evidence"
git push origin main
git status --short
```

Use a commit message appropriate to the actual next change. The names above describe the handoff change, not every future session. Report the commit ID and push result so the next assistant knows what is available remotely.

### 4.3 How to keep two assistants from losing context

Each session should record the starting commit, changed files, new evidence, corrections, tests actually run, and the next unresolved question. If a statement changes, explain why and link to the replacement evidence. Avoid relying on a remembered conversation when the files have since changed.

The root `AGENTS.md` is the durable collaboration instruction source. Its hardware summaries are useful orientation but should not supersede direct measurements or pinned source evidence. In particular, its E8 MCU statement currently exceeds what the USB observations establish.

## 5. Session and commit history

The visible recent history is:

| Commit | Subject | Why it matters |
|---|---|---|
| `d3f7aba` | Expand advanced wireless chipset exploitation research | Advanced published case studies were added to the reference |
| `dd5f5a7` | Add EMAX E8 ELRS firmware research corpus | Initial ELRS source/target snapshots and availability notes were pushed |
| `368b0f0` | Add Betaflight and ExpressLRS firmware corpus for TinyHawk III / E8 | Claude added related binary test articles and tagged sources; corpus scope expanded |
| `bcc565d` | Add ELRS binding phrase rainbow table generator and pre-computed table | Offline lookup tool and tables were added |
| `7bf484a` | Add AGENTS.md with project context for multi-agent workflow | Persistent project/collaboration instructions were added |
| `93578f3` | Add CRSF serial probe tool for USB-connected ELRS devices | Candidate USB serial probing script was added |

The initial `dd5f5a7` push completed successfully to `origin/main`. At the start of this handoff, `git pull --ff-only` reported that the checkout was already up to date at `93578f3`.

Earlier in the conversation, workspace writes and Git commands failed under the execution sandbox. The user restarted Codex and reported running apt/AppArmor commands. The exact apt/AppArmor commands and their complete outputs are not preserved in the available conversation, so this handoff does not invent them or claim they fixed the underlying cause.

## 6. Hardware identity and confidence

### 6.1 The actual E8

Known from the user's statement: the test transmitter is an EMAX E8 ELRS variant, and it was connected through serial/USB during the earlier collection work.

Earlier approved USB discovery reported:

```text
Bus 003 Device 031: ID 8888:2001 Your Company USB Serial
USB CDC ACM serial node: /dev/ttyACM0
USB serial string: 00000001
```

The USB bus/device numbers are ephemeral. The VID/PID and strings are generic. They do not establish the MCU family, board revision, running firmware version, or which upstream ExpressLRS board layout applies. An interface that enumerates as CDC ACM might expose settings, an updater, a simulator interface, diagnostic output, or a vendor protocol. Its existence does not prove CRSF over USB.

The saved device-access rule names `/dev/ttyACM0`. If re-enumeration produces `/dev/ttyACM1`, the old rule and old command defaults will no longer target the same path.

### 6.2 Related EMAX ExpressLRS modules

The downloaded official target registry contains these relevant entries:

| Registry entry | Product name | Platform | Firmware environment | Layout |
|---|---|---|---|---|
| `emax.tx_2400.plain` | EMAX OLED 2.4GHz TX | `esp32` | `Unified_ESP32_2400_TX` | `EMAX 2400 OLED.json` |
| `emax.tx_2400.nano` | EMAX Nano 2.4GHz TX | `esp32` | `Unified_ESP32_2400_TX` | `EMAX 2400 Nano.json` |

Both declare `min_version: 3.0.0` and upload methods `uart` and `wifi`. These are properties of the named OLED/Nano modules. They do not prove that the E8 has WiFi, uses the same MCU, or shares their GPIO map. The labels `EMAX_2400_TX` and `EMAX_NANO_2400_TX` in this registry are `prior_target_name` values; the current entries point to unified firmware environments and hardware layouts.

There is no E8-specific target/name in the saved EMAX registry object. Describing OLED/Nano as the “closest” targets means they are related research references, not that a hardware compatibility measurement has been performed.

### 6.3 TinyHawk-side material

The repository also contains an `EMAX_TINYHAWKF4SX1280` target config and Betaflight test images associated with `CRAZYBEEF4SX1280` and STM32F411. These are useful receiver/flight-controller samples. Their filenames and downloaded config do not prove the exact target and firmware installed on the owner's drone.

The config explicitly names the EMAX board and declares SPI ExpressLRS/SX1280 support. Selected pin mappings are:

| Function | Config value |
|---|---|
| SX1280 SPI bus | `rx_spi_bus = 3` |
| SX1280 chip select | `PA15` |
| SX1280 EXTI | `PC14` |
| SX1280 reset | `PA8` |
| SX1280 busy | `PA13` |
| SPI receiver bind input | `PB2` |
| SPI receiver LED | `PB9` |
| Gyro SPI bus | `gyro_1_spibus = 1` |
| OSD SPI bus | `max7456_spi_bus = 2` |
| Configured ELRS domain | `ISM2400` |

The config header mentions Betaflight 4.3.1 and a 2022 build. The corpus binaries/source are labeled 2026.6.2. Preserve that difference. A historic unified config is not proof of the feature selection, layout, or build options used in a modern image.

### 6.4 Identity record to obtain next

Record the product label, USB connector type, board revision, MCU markings if observed, RF chip markings if observed, USB descriptors, actual serial node, running firmware/version response, and the method used to obtain each field. Photograph or save the evidence locally with a date. For fields inferred from a related product, say “inferred” and name that product.

Do not replace “unknown MCU” with “ESP32” solely because an EMAX OLED/Nano target uses ESP32. The corresponding assertion in `AGENTS.md` and `firmware/README.md` remains a research hypothesis for the particular E8 until device-specific evidence confirms it.

## 7. Firmware acquisition findings

### 7.1 Manufacturer material found

The official EMAX article [ExpressLRS E8 Transmitter — Custom Configurator](https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264-expresslrs-e8-transmitter-custom-configurator) states that the E8 ELRS variant uses a custom serial application for settings. Its documented sequence is to connect through USB-C, connect in the application, and use its Read function to view current settings. The article separately describes Write for applying changed settings.

The article links this manufacturer-provided utility:

```text
https://mega.nz/file/pkICkJhQ#xO-EF1GunGGqs-GASkFishIIPg1iRWMo_-i_owCs-Ag
```

During the reviewed collection, the Mega-hosted tool was not downloaded, executed, or statically inspected. No tool archive or executable is currently present in the tracked firmware corpus. Consequently, whether its package contains embedded firmware, example `.bin` files, updater metadata, or additional download URLs is an open investigation.

Public E8/kit manual material also describes a serial upgrade workflow that takes a separately selected firmware file. Finding updater instructions does not establish that the firmware file itself is publicly available.

### 7.2 Upstream ExpressLRS material found

The collection obtained the official ExpressLRS master source snapshot and official target-registry snapshot. A later repository update added the ExpressLRS 4.1.0 tagged source archive.

At collection time, the official GitHub latest-release API returned release `4.1.0`, published `2026-07-17T17:02:11Z`. The asset list observed for that release contained `elrs.lua`. That Lua script was downloaded. It is configuration/UI material for a supporting handset, not an MCU firmware image.

This API check covered that latest release's assets; it was not a full audit of every historical GitHub release, build artifact, fork, or manufacturer mirror. “All available E8 firmware” remains an acquisition goal, not a completed result.

The upstream workflow builds firmware for a selected environment and target configuration. The same source release can produce different binaries for different hardware, feature selections, regulatory domains, and toolchain versions. Source availability alone does not mean an E8-specific prebuilt binary exists.

### 7.3 Master snapshot provenance

The collection recorded these repository commits:

| Repository | Recorded commit | Commit time reported by API |
|---|---|---|
| `ExpressLRS/ExpressLRS` | `dd96654519249798707f011b7c79236350f246f8` | `2026-10-11T01:03:07Z` |
| `ExpressLRS/targets` | `51a697b3220f069baf3adf2c5353b710df70bbe0` | `2026-10-13T21:21:44Z` |

The actual downloads used mutable `master` archive URLs, followed by a separate API query for the commit hashes. The saved bytes are fixed by SHA-256, but that procedure did not independently prove the archives' exact commit identity. If exact revision attribution matters, obtain a commit-addressed archive and compare its contents. Do not silently treat the separate API response as an embedded Git commit in the archive.

### 7.4 Routes to extend the corpus

The following are proposed work, not operations performed during this handoff:

1. Retrieve the manufacturer-linked serial tool package and inspect its file listing and metadata without connecting it to the E8.
2. Look for firmware bundled in that package, release notes, updater URLs, and version strings.
3. Review official EMAX downloads and historical support links for E8-specific files. Keep obsolete and current versions separate.
4. Enumerate upstream tagged releases and determine which target/configuration material exists for related EMAX modules.
5. If useful, generate related EMAX sample builds with a reproducible toolchain. Label these as locally built samples and preserve build settings.
6. Consider a device readback only after identifying the MCU, the supported read interface, and its effect on device state. Acquisition through a bootloader or debug port is a separate task from downloading public files.

Requesting manufacturer information could help, but no message or support ticket has been sent. Sending one would be a separate user-authorized action.

## 8. Complete artifact inventory

These are the tracked test/reference artifacts inspected for this handoff:

| Relative path under `firmware/` | Bytes | Type and role |
|---|---:|---|
| `reference/ExpressLRS-master.tar.gz` | 1,816,291 | Upstream source snapshot archive |
| `reference/ExpressLRS-targets-master.tar.gz` | 54,043 | Hardware target/layout registry archive |
| `reference/targets.json` | 178,221 | Searchable hardware registry JSON |
| `reference/elrs-4.1.0.lua` | 30,517 | Official 4.1.0 release Lua asset |
| `src/expresslrs-4.1.0.tar.gz` | 1,594,346 | Tagged ExpressLRS firmware source |
| `src/betaflight-2026.6.2.tar.gz` | 22,386,576 | Tagged Betaflight flight-controller/RX source |
| `betaflight/EMAX_TINYHAWKF4SX1280.config` | 2,932 | Historic EMAX board configuration |
| `betaflight/betaflight_2026.6.2_STM32F411_CRAZYBEEF4SX1280.hex` | 1,134,310 | Intel HEX binary test article |
| `betaflight/betaflight_CRAZYBEEF4SX1280.bin` | 426,910 | Corresponding raw binary |
| `betaflight/betaflight_2026.6.2_STM32F411.hex` | 1,338,468 | Intel HEX base STM32F411 image |
| `betaflight/betaflight_STM32F411.bin` | 498,418 | Corresponding raw binary |

The binary images are Betaflight test articles, not dumps of the E8 transmitter. ExpressLRS material in this corpus currently consists of source/target/reference files rather than an E8-matched firmware image.

The tagged archives and Betaflight files arrived in Claude's `368b0f0` commit. Their source repository/release attribution is documented there and in `firmware/README.md`; a complete original download log and per-file direct source URL were not present in the reviewed metadata. Retain this provenance limitation when using the files as evidence.

For new material, record the exact URL, download time, HTTP result if available, upstream tag/commit, file size, SHA-256, artifact kind, declared target, and whether the artifact was downloaded, converted, built, or read from hardware.

## 9. Integrity checks and binary import settings

### 9.1 Hash coverage

Before this handoff, `firmware/SHA256SUMS` covered only the four initial reference downloads. The firmware README listed six additional hashes, all of which matched the current files; it did not list a hash for the board config. The handoff update extends the manifest to all eleven tracked corpus artifacts, including the config.

The current config hash is:

```text
7b21b072fede3e23fdc43b4d4722d47276f789f6517573e956ec228b1f6bea94
```

The README value `4b7d5acb364ff1f28265c5e84d1e63bec20a1a1a39f1e292250d7cae251db85a` belongs to the Betaflight source archive, not the board config. Its file association was rechecked while preparing the handoff. A recomputed hash establishes the identity of the present bytes, not their origin.

Run checksum verification from the repository root because manifest paths begin with `firmware/`:

```bash
sha256sum -c firmware/SHA256SUMS
```

A valid local hash check demonstrates agreement with the saved manifest. These are locally computed hashes; they are not vendor signatures or an independent manufacturer authenticity guarantee.

### 9.2 HEX-to-BIN consistency verified

During this handoff, both HEX files were parsed record by record with a local Python verifier. Every record's Intel HEX checksum was checked, extended-address records were honored, and all mapped data bytes were compared with the corresponding `.bin` at base `0x08000000`.

| Pair | Address range, inclusive | Raw binary length | Mapped HEX data bytes |
|---|---|---:|---:|
| `CRAZYBEEF4SX1280` HEX/BIN | `0x08000000`–`0x0806839D` | 426,910 | 403,246 |
| Base `STM32F411` HEX/BIN | `0x08000000`–`0x08079AF1` | 498,418 | 475,826 |

Both raw binaries matched every data byte present in their HEX counterpart, and their total spans matched the HEX minimum/maximum addresses. Differences between mapped-byte counts and raw lengths reflect gaps in the sparse HEX address map. The checks did not establish the provenance or intended semantic contents of padding bytes.

### 9.3 ARM vector evidence

The first two 32-bit little-endian words were inspected:

| Binary | Initial stack pointer | Reset vector as stored |
|---|---|---|
| `betaflight_CRAZYBEEF4SX1280.bin` | `0x2001FFF0` | `0x080556C5` |
| `betaflight_STM32F411.bin` | `0x2001FFF0` | `0x08066201` |

These are consistent with a Cortex-M flash image and a Thumb reset entry. The stored reset-vector low bit indicates Thumb state; the corresponding instruction addresses have that bit cleared. Both reset entries fall within the imported image ranges.

### 9.4 Offline Ghidra import starting point

For these two raw Betaflight images, begin with:

- Processor family: ARM, little-endian, 32-bit, appropriate Cortex-M/Thumb context for STM32F411.
- Image base: `0x08000000`.
- Inspect the vector table and reset entry before trusting automatic disassembly.
- Treat SRAM/peripheral mappings as separate memory blocks; an arbitrary file offset is not an MCU address.
- Save the project and analysis notes under a local analysis directory, not over the immutable test article.

The exact Ghidra language variant depends on the installed version. Check its offered ARM Cortex language definitions rather than copying a language identifier from another installation.

No full Ghidra import, decompilation, emulator execution, or debug session was completed during this handoff. The base-address evidence above is sufficient to start an import; it is not a firmware vulnerability finding.

## 10. Source extraction and offline navigation

### 10.1 Inspect then extract

The repository's `firmware/src/.gitignore` excludes the expected extracted source directory names. Review archive contents first:

```bash
tar -tzf firmware/src/expresslrs-4.1.0.tar.gz | less
tar -tzf firmware/src/betaflight-2026.6.2.tar.gz | less
```

If an extracted directory already exists, inspect it for local analysis edits before extracting on top of it. For a fresh checkout:

```bash
tar -xzf firmware/src/expresslrs-4.1.0.tar.gz -C firmware/src
tar -xzf firmware/src/betaflight-2026.6.2.tar.gz -C firmware/src
```

Expected directory names are `firmware/src/ExpressLRS-4.1.0/` and `firmware/src/betaflight-2026.6.2/`. Keep extracted trees out of commits as instructed by `AGENTS.md`. Preserve intentional analysis patches as a small diff or separately documented change.

### 10.2 Useful initial searches

```bash
rg -n 'MY_BINDING_PHRASE|hashlib.md5' firmware/src/ExpressLRS-4.1.0/src/python
rg -n 'OTA_VERSION_ID|OtaGetUidSeed|OtaUpdateCrcInitFromUid' firmware/src/ExpressLRS-4.1.0/src
rg -n 'OTA_Sync_s|UID4|UID5' firmware/src/ExpressLRS-4.1.0/src/lib/OTA
rg -n 'FHSSrandomise|sync_channel|freq_count' firmware/src/ExpressLRS-4.1.0/src/lib/FHSS
rg -n 'ELRS_OTA_VERSION_ID|USE_ELRSV4' firmware/src/betaflight-2026.6.2/src
rg -n 'memcpy|memmove|length|payload|buffer' firmware/src/betaflight-2026.6.2/src/main/rx/expresslrs*
```

These searches identify candidate files and branches. A matching `memcpy` or length variable is not itself a bug; trace allocation sizes, validation, callers, state, and compile-time conditions.

### 10.3 Initial source map

| Source location within archive | Research use |
|---|---|
| ELRS `src/python/build_flags.py` | Build-time binding phrase/UID derivation |
| ELRS `src/include/targets.h` | OTA version selection in the pinned release |
| ELRS `src/lib/OTA/OTA.h` | Packet structures and packed field layouts |
| ELRS `src/lib/OTA/OTA.cpp` | UID seed, CRC state, OTA encoding/validation |
| ELRS `src/lib/FHSS/FHSS.cpp` | Domain/channel definitions and hopping sequence construction |
| ELRS `src/lib/SX1280Driver/` | RF chip configuration and SPI abstraction |
| Betaflight `src/main/rx/expresslrs.c` | Integrated SPI ELRS receiver path |
| Betaflight `src/main/rx/expresslrs_common.h` | OTA version build condition and common definitions |
| Betaflight `src/main/drivers/rx/rx_sx1280.c` | SX1280 radio-driver path |

## 11. ExpressLRS observations and version distinctions

### 11.1 What was directly checked

The following checks used the local ExpressLRS 4.1.0 source archive, rather than a recollection of older research:

- `src/python/build_flags.py`, lines 39–40 and 71–74, derives six UID bytes from the MD5 digest of the binding-phrase build define.
- `src/include/targets.h`, line 8, defines `OTA_VERSION_ID` as `4` in this pinned source.
- `src/lib/OTA/OTA.cpp`, lines 28–44, derives the CRC initializer and hopping seed from UID bytes and the OTA version.
- `src/lib/OTA/OTA.h`, lines 29–40, contains the current SYNC structure, including `UID4` and `UID5` fields.
- `src/lib/FHSS/FHSS.cpp` contains an ISM2G4 domain with 80 channels, endpoints 2,400.4 and 2,479.4 MHz, and a sync-channel index calculation of `freq_count / 2`.

Line numbers are specific to the pinned archive. Recheck them after changing release versions.

### 11.2 Binding phrase derivation

The existing script reproduces a build-define form:

```text
MD5('-DMY_BINDING_PHRASE="<phrase>"')[0:6]
```

The exact encoded bytes matter: quotes, capitalization, spaces, and character encoding change the digest. The pinned source hashes the define string passed through its build path. Other configuration paths should be checked separately rather than assumed to produce identical bytes.

MD5 here is a deterministic identifier derivation. A small phrase dictionary can produce candidate identifiers quickly. That observation does not establish that the particular E8's phrase is in the dictionary, that every binding mode uses a phrase, or that the first matching partial identifier is unique.

### 11.3 CRC and hopping-seed version dependence

The pinned ELRS source computes a hopping seed from UID bytes 2 through 5 with the OTA version mixed into the final byte. It also mixes the OTA version into the UID-derived CRC initializer. The current lookup tool hard-codes version `4`.

That value is supported for the checked ELRS 4.1.0 source. Its comment describing the same value for all `v3.x/4.x` is not supported by this check. In the pinned Betaflight source, `expresslrs_common.h` selects version `3` when `USE_ELRSV4` is absent and version `4` when it is present.

Therefore, do not infer the OTA version merely from the file name, release family, or the lookup script's comment. Record the compile-time feature selection and actual TX/RX version. The source archive's ability to compile a v4 branch does not prove a particular prebuilt image included it.

### 11.4 Current SYNC fields versus older observations

The inspected ELRS 4.1.0 `OTA_Sync_s` explicitly includes `UID4` and `UID5`, not a `UID3` field. The lookup tool and parts of `AGENTS.md` refer to three UID bytes based on older research. The firmware README's detailed structure instead describes two exposed bytes.

This is a concrete discrepancy to resolve by release and packet format. It may reflect protocol evolution, inference from other observations, or an inaccurate carryover from an older write-up. The handoff has not traced every receive/transmit path or historical layout, so it does not assert that no other field or interaction can reveal additional identity information.

The current tool's `--lookup` requires a three-byte value corresponding to UID bytes 3, 4, and 5. A capture containing only the two explicit current SYNC fields cannot be passed to it as an equivalent observation. Do not invent the missing byte or claim the helper already supports two-byte matching.

### 11.5 Partial matches need independent validation

A two-byte identity observation contains 16 bits. Different phrases can share those bytes. A candidate phrase must be checked against additional independently observed state or a complete lab-controlled identity before labeling it recovered.

Likewise, deterministic frequency hopping and a UID-derived CRC do not by themselves demonstrate accepted packet injection or control takeover. Timing, receiver state, rate selection, format, model matching, transmitter competition, and version checks remain separate conditions.

The statement in the existing README that “higher power wins” is not a reliable general result. Receiving behavior depends on timing, modulation, synchronization, capture effects, collisions, and firmware handling. No such takeover was demonstrated on the owner's E8/TinyHawk equipment in this handoff.

### 11.6 Preserve separate milestones

For continuing ELRS research, use separate records for:

1. Source-derived identifier/configuration behavior.
2. Observed packets with a defined version/format.
3. Candidate identity matching with documented ambiguity.
4. Validated receiver state and timing in a controlled lab.
5. Demonstrated receiver behavior.

A successful earlier milestone should not be described as completion of a later one.

## 12. Existing lookup tool

### 12.1 Files and dependency

`tools/elrs-uid-rainbow.py` is a Python helper using standard-library modules. The tracked CSV and JSON tables contain 193 sample phrases. They are examples, not a record of the owner's secret phrase or a comprehensive phrase space.

The script has options for a single phrase, an external phrase list, appending built-in examples, CSV/JSON output, and reverse lookup against its three-byte `sync_bytes` field.

An offline inspection workflow is:

```bash
python3 tools/elrs-uid-rainbow.py --help
python3 tools/elrs-uid-rainbow.py --phrase home-lab-example
```

The second command uses a made-up sample phrase. It does not contact the device or transmit RF. Avoid storing the owner's actual binding phrase or generated sensitive identity information in public/shared commits.

### 12.2 Review findings to address before relying on output

- OTA version is a fixed constant, currently `4`.
- `sync_bytes` and `--lookup` assume UID bytes 3–5 are available.
- The `collision` flag in table generation tracks repeated full UIDs, not collisions of the partial SYNC lookup key.
- Output labels can suggest current on-air visibility that the checked 4.1.0 structure does not directly support.
- The computed CRC initializer is not a complete encoded packet CRC; nonce/state/packet format still matter.
- A table match is a candidate identifier derivation, not a demonstrated receiver acceptance or control result.

Suggested improvement: make OTA version and observation width explicit inputs, distinguish two-byte and three-byte lookup modes, report every partial-key collision, and add small source-derived known-answer checks. This is proposed work; the tool was not modified in the handoff.

## 13. Existing serial probe

### 13.1 What the script actually does

`tools/elrs-serial-probe.py` imports the third-party `serial` module supplied by pyserial. A home environment needs that dependency before the script can even display help. Record its installed version when setting up a local environment.

Default port is `/dev/ttyACM0`, default baud is `460800`, and default listen timeout is three seconds per phase. The code:

1. Opens the serial port.
2. Resets/discards the existing input buffer.
3. Tries passive CRSF frame parsing for the selected timeout.
4. Unless `--listen-only` is provided, sends a CRSF ping.
5. If device information was not returned, sends a targeted device-info query.
6. With `--dump-params`, sends parameter-read queries and prints responses.

The normal invocation therefore writes bytes to the serial interface. It does not default to a wholly passive capture. This is a mismatch with the repository's preferred read-only/dry-run defaults and needs review before being used against an unconfirmed vendor USB protocol.

The source's “Phase 5” comment mentions WiFi-mode checking, but the reviewed code only prints a summary at that point. It does not implement a verified E8 WiFi-mode detector.

### 13.2 Candidate listen-only use

After re-identifying the device path and verifying the dependency, a candidate application-byte listen command is:

```bash
python3 tools/elrs-serial-probe.py --listen-only --port /dev/ttyACM0 --baud 460800 --timeout 3
```

This is documented for a future controlled lab test, not a command run during the handoff. Replace the port with the actual E8 node. The selected baud is the script's default; it has not been verified as the E8 USB protocol's required value.

Even with `--listen-only`, opening a serial port can change control-line state, and the script clears buffered input. That differs from a zero-interaction USB traffic capture. Investigate DTR/RTS/reset behavior if preserving device state matters.

### 13.3 Decoder and probing limitations

- The script assumes CRSF framing on the USB endpoint. EMAX documents a custom serial application, and USB CRSF support is unconfirmed.
- The frame-type/address display tables should be compared with the exact CRSF definitions in the relevant source before trusting symbolic names.
- The parser filters for CRC-valid CRSF candidates; it does not retain an archival raw-byte stream. No decoded frames is not proof that no bytes arrived.
- Baud fallback happens when opening the serial port raises an exception. It does not retry alternative speeds merely because no valid frames appeared. Successfully opening a TTY does not validate its baud.
- Input-buffer reset can discard useful startup/version bytes.
- Parameter reads do not implement a complete multi-chunk reconstruction of every parameter entry.
- Numeric device-info fields are not automatically a validated semantic firmware-version string.
- Resource cleanup and malformed-response handling need review before long capture sessions.

Suggested next change: add raw-byte logging and a genuinely passive default, validate CRSF definitions, and establish the E8 transport/protocol independently. Do not interpret an empty capture as evidence that the device lacks ELRS.

## 14. USB and serial discovery

### 14.1 Separate discovery from port probing

These commands inspect host/device metadata without writing firmware or application payload bytes:

```bash
lsusb
lsusb -t
ls -l /dev/ttyACM* /dev/ttyUSB*
ls -l /dev/serial/by-id/
id
```

Missing globs or directories can legitimately produce a “No such file” message. A by-id alias is convenient but not guaranteed, particularly with generic descriptors or driver behavior.

For a node that actually exists:

```bash
udevadm info --query=property --name=/dev/ttyACM0
stat /dev/ttyACM0
fuser /dev/ttyACM0
```

The node can change after reconnecting or entering another USB mode. Use the current metadata and connection sequence to identify it rather than assuming the previous bus number.

### 14.2 Power and device mode

`AGENTS.md` says E8 serial testing needs battery power. This session did not validate that power requirement. Record USB-only, battery-powered, and normal/settings/updater modes separately when testing; a change in enumeration is itself useful evidence.

Entering an updater mode, resetting the device, or running a manufacturer application are active interactions. They are not needed for the offline analysis work described in the first home sessions.

### 14.3 Permission troubleshooting

Use the device's actual owner/group/mode and the current process's group list to distinguish Unix permission denial from sandbox denial. If a normal terminal can list the device and the agent cannot, capture both results. If both fail, inspect host enumeration/power/driver state before changing agent settings.

Do not assume a serial failure needs root or broad permission changes. A missing node, busy port, wrong endpoint, different device mode, or unsupported application protocol can produce different failure symptoms.

## 15. Codex permissions and troubleshooting history

### 15.1 Distinct failures observed earlier

| Symptom | What it showed | What it did not establish |
|---|---|---|
| `apply_patch`: Failed to write file | The attempted write did not succeed | That every workspace path was writable or that a particular host security component was responsible |
| Sandbox could not configure loopback | Command startup failed before Git ran | A Git authentication or remote-repository failure |
| `lsusb`: unable to initialize libusb, `-99` | libusb discovery failed inside that execution sandbox | That the USB device was absent from the host |
| `.git/index.lock`: Read-only file system | Protected Git metadata could not be modified in the default execution context | An unwritable source/document directory |
| No `/dev/serial/by-id` entry | No stable alias was available in that observation | That `/dev/ttyACM0` could not exist |

An approved command outside the default sandbox successfully listed the generic USB CDC device. During the initial corpus session, approved Git operations successfully staged, committed, and pushed the files after the default sandbox blocked `.git/index.lock` creation.

### 15.2 Changes made previously

A narrow custom permission profile was saved in `/home/borealis/.codex/config.toml`. The selected fields were reread while writing this handoff:

```toml
approval_policy = "on-request"
approvals_reviewer = "auto_review"
default_permissions = "e8-hardware-lab"

[permissions.e8-hardware-lab]
description = "Workspace access plus read-only USB discovery and the connected EMAX E8 serial port."
extends = ":workspace"

[permissions.e8-hardware-lab.filesystem]
"/dev/ttyACM0" = "write"
"/dev/bus/usb" = "read"
"/sys/bus/usb/devices" = "read"

[permissions.e8-hardware-lab.network]
enabled = false
```

This is a record of the saved local profile, not an assurance that another machine or every launch context will apply it. The file also has model/UI settings that are not necessary to reproduce these permission notes. Do not copy an entire user configuration blindly.

A backup was reported at `/home/borealis/.codex/config.toml.before-e8-hardware-profile`. The earlier edit also resolved duplicate top-level `model` keys and validated TOML parsing. The current config parsed successfully during this handoff.

### 15.3 Effective permissions versus saved permissions

Official OpenAI documentation says permission profiles are selected by `default_permissions`; older `sandbox_mode` settings or `--sandbox` can take precedence. Extending `:workspace` retains protected metadata directories as read-only unless explicitly overridden. These mechanisms explain why source edits, Git metadata, network, and device access can have different outcomes. See [OpenAI permission profiles](https://learn.chatgpt.com/docs/permissions).

The earlier collection still experienced a sandbox libusb failure after the profile was saved. Its effective activation was not demonstrated. In this handoff, no serial device was visible, so there was no new positive hardware-access test.

At home, inspect the active session policy and launch configuration before changing anything. A CLI flag, project config, managed policy, or an already-running session can differ from the user TOML. Restarting a client alone is not evidence that its device permissions changed.

### 15.4 Local CLI evidence

The handoff machine reported `/usr/bin/codex`, version `0.162.1`. Its help exposed `doctor`, `--strict-config`, `--no-daemon`, and configuration overrides. Running help/version in the sandbox also warned that PATH aliases could not be created because of a read-only filesystem; those commands nevertheless returned output.

For home diagnostics, start with version/help rather than assuming the same build or options:

```bash
codex --version
codex --help
codex doctor --help
```

Inspect the installed command's behavior before using additional diagnostic subcommands. This handoff did not run a full Codex doctor diagnosis or change the saved profile.

### 15.5 Automatic-review decisions from earlier work

Two broader permission changes were rejected by automatic approval review:

- Persistently removing sandbox limits and disabling approvals was judged broader than the request to enable USB discovery.
- Granting write access across the USB bus tree together with unrestricted shell network access was also rejected as broader than needed.

The narrower USB-read/serial-node-write/network-disabled profile was accepted and written. These earlier decisions are recorded so a future assistant does not repeatedly propose the same rejected broad change or claim it was applied.

No host-wide AppArmor disablement, persistent unrestricted access, or blanket device permissions should be inferred from the available history. The precise apt/AppArmor commands the user ran were not available to verify.

## 16. Chipset research program

### 16.1 Start with the actual processing boundary

Draw an input path for each test article:

```text
RF frame or host request
    -> radio/controller filtering
    -> firmware or host driver packet handling
    -> length/state validation
    -> buffer/allocation/copy
    -> state update or callback
    -> host notification or application effect
```

For the SPI ELRS Betaflight sample, relevant parsing runs in the flight-controller firmware. For other FullMAC WiFi/Bluetooth products, parsing may run in a separate controller and then cross a host interface. For SoftMAC designs, more processing may occur in a host kernel. Establish the architecture before applying an exploit technique from another product.

SX1280 is the RF chip named in the related sources/config. ESP32 and STM32F411 are processor platforms in the related TX/RX examples. Do not transfer an ESP32 Bluetooth controller exploit to SX1280 or to a Betaflight SPI ELRS receiver just because all three participate in wireless systems.

### 16.2 Research questions for memory corruption

| Candidate class | Questions to answer | Evidence required |
|---|---|---|
| Stack buffer overflow | Does an externally influenced length exceed a stack object? What checks dominate the copy? | Exact allocation/copy path and a reproducible bounds violation |
| Heap overflow | Is allocation smaller than the accepted payload or decoded length? | Size computation, accepted input conditions, sanitizer/debug evidence |
| Integer overflow/truncation | Can arithmetic wrap, sign-convert, or narrow before allocation/indexing? | Concrete types, ranges, and differing pre/post-conversion values |
| Use-after-free | Can a callback/interrupt/event use an object after release or reuse? | Lifetime trace and observable stale access |
| Type confusion | Can a parser/event path treat one object layout as another? | Proven incompatible layouts and reachable dispatch |
| Out-of-bounds read | Are fields consumed before the minimum length is checked? | Read location, bounds, and caller input reachability |
| Function-pointer corruption | Is mutable callback state adjacent to a controllable write? | Object layout and a controllable overwrite; not mere proximity |
| Allocator corruption | Are free-list or block metadata writes influenced by malformed state? | Actual allocator implementation and validated corruption primitive |
| Race/reentrancy | Can ISR/main-loop or task interactions break a validation/lifetime invariant? | Scheduling/state sequence, not only a shared variable |

Avoid inferring an overflow from a large malformed input alone. Determine which validation and RF filtering would allow that input to reach the code.

### 16.3 Exploitability milestones

Keep these milestones separate in results:

1. A candidate suspicious code path.
2. A reproducible memory-safety violation in a faithful harness or target.
3. A reachable violation through the actual protocol state.
4. A controlled corruption or disclosure primitive.
5. A demonstrated control-flow change or code-execution result.
6. A demonstrated boundary crossing, if a separate host exists.

A reboot is a DoS observation until its cause is diagnosed. A host sanitizer crash is not automatically a firmware crash. A controlled overwrite is not automatically a workable ROP chain. An information leak is not automatically a mitigation bypass on a different build.

### 16.4 ROP and related advanced techniques

The vulnerability reference contains published examples involving Broadcom heap corruption, Broadpwn, Qualcomm QualPwn, Bluetooth Frankenstein, Linux BleedingTooth, and other families. Use those as case studies for reasoning about primitives and dependencies.

Before deciding whether a test article needs ROP, determine executable-memory permissions, fixed versus randomized addresses, instruction set, available gadgets, stack layout, interrupt context, and exception behavior. An MCU target may have a very different mitigation model from a Linux host kernel. An absent mitigation in one old firmware does not establish its absence in the E8 or another modern device.

The handoff has not built a gadget inventory, payload, chain, or mitigation bypass. No build-specific ROP addresses or working exploit primitive have been established for these E8-related materials.

### 16.5 Offline harness design

Start with a narrow deterministic parser or state transition from the pinned source. Preserve the same length/type checks and inputs as the firmware path. Model only the minimum required hardware state, and document every stub.

Useful deliverables are a call-path note, a minimal input corpus, a sanitizer-enabled host harness where practical, and a crash log tied to a source revision. Track whether a discovered problem exists in production code or only in an inaccurate harness/stub.

If checksum/state validation is bypassed solely to explore deeper code in a harness, label that change and separately establish whether normal inputs can reach the result. The bypass itself cannot be used as evidence of an on-air vulnerability.

### 16.6 Firmware-to-host claims

Where a separate controller/host architecture exists, identify the actual interface: UART/HCI, SDIO, USB, PCIe, SPI, DMA buffers, shared memory, or vendor events. Controller code execution does not grant arbitrary host-memory access by definition.

For the integrated Betaflight SPI receiver model, there is no separate ESP receiver automatically implied by the source. A fault in the integrated receiver code is within the flight-controller firmware boundary. For the E8, exact processor/controller boundaries still require hardware identification.

## 17. Reading the existing vulnerability reference critically

The reference is broad and useful, but this handoff did not independently reverify every listed CVE, vendor advisory, firmware range, or claimed exploit. Treat it as a research index.

For each item considered for the CTF, confirm:

- The CVE/advisory identifier exists and its primary source matches the claim.
- The affected component is actually wireless firmware, a host driver, an application service, or something else.
- The installed chip/build/patch level matches the affected range.
- The actual transport and device state match the described trigger.
- Advisory impact and public demonstrated impact are distinguished.
- A linked repository contains substantive evidence, rather than only a placeholder or unrelated code.
- The described exploit is applicable to the target architecture and mitigations.

Some entries deserve prompt rechecking because their labels are especially easy to overgeneralize: DJI items such as `CVE-2026-78306` and `CVE-2026-77812`, PX4 items, and “Qualcomm Software Center” entries placed near WLAN research. This handoff makes no new validation claim about those identifiers.

The broader plan also contains assumptions about PMF/deauthentication, PMKID availability, MAVLink acceptance, MAVLink FTP endpoints, GPS/RTH behavior, and ELRS version-specific attacks. Those depend on target configuration and are not universal outcomes. A planned exercise table is not a tested capability matrix.

A good correction preserves the original claim's context, cites the primary replacement evidence, and states the narrower conclusion. Follow `AGENTS.md`'s instruction to update or annotate other-agent content rather than deleting it without explanation.

## 18. Suggested work sessions at home

### Session A — Restore and inventory

Goal: reproduce the available offline materials and know exactly what each file represents.

1. Pull or clone the repo; read `AGENTS.md` and this handoff.
2. Verify all eleven corpus artifacts with `firmware/SHA256SUMS`.
3. Extract the tagged source archives into their ignored directories.
4. Record OS, Python version, available analysis tools, and current Git commit.
5. Create an artifact register using the template below.

Completion evidence: successful hash output, source-directory names, version inventory, and any provenance gaps. No hardware is required.

### Session B — Resolve protocol/tool assumptions

Goal: make the offline lookup helper's labels and assumptions agree with a selected source version.

1. Inspect `OTA_Sync_s`, OTA version definitions, CRC initialization, and UID seed derivation in 4.1.0.
2. Inspect the corresponding Betaflight definitions and `USE_ELRSV4` build condition.
3. Compare these with the lookup script's fixed version and three-byte key.
4. Document the differences before modifying the tool.
5. If updating the helper, add version/observation-width inputs and small known-answer checks derived independently from the chosen source.

Completion evidence: a version comparison table and reproducible offline examples using made-up lab phrases. A radio capture or takeover is not a requirement for this session.

### Session C — Establish E8 identity and USB behavior

Goal: determine what the connected E8 interface actually exposes.

1. Record USB descriptors and the actual device node in a normal terminal.
2. Compare discovery inside the agent's current sandbox.
3. Record battery/power/mode conditions without assuming the prior default node.
4. Establish whether the endpoint uses CRSF or a custom EMAX protocol.
5. Review the serial helper's active defaults and raw-capture limitations before using it.

Completion evidence: an identity/interface note and logs, including a precise explanation of empty/failed results. No firmware update is required.

### Session D — Expand the artifact corpus

Goal: obtain attributable E8-specific material or additional clearly labeled related samples.

1. Inspect the official manufacturer tool package offline if acquired.
2. Record every embedded or linked candidate image with its provenance and stated version.
3. Keep E8, EMAX module, Betaflight RX, and generic source samples distinct.
4. Preserve the raw original file before any extraction/conversion.
5. Extend the manifest and inventory without overwriting older test articles.

Completion evidence: actual new files with source/size/hash/target notes, or a documented unsuccessful acquisition path. “No public image found in these sources” is an acceptable bounded result; “none exists anywhere” is not established.

### Session E — Map one parser and test one hypothesis

Goal: produce a concrete, reviewable memory-safety research result.

1. Choose one exact source path and compile-time configuration.
2. Trace externally influenced fields through validation, copy/allocation, and state changes.
3. Write down the suspected invariant and alternative explanations.
4. Build a faithful offline harness if feasible and record its deviations.
5. Reproduce any result deterministically; distinguish malformed input rejection, ordinary reset, and verified corruption.

Completion evidence: source call path, minimal reproducer for the local harness, logs, and a reachability assessment. Do not describe an untested hardware effect as observed.

### Session F — Correlate source and binary

Goal: determine how much of the pinned source/config can be mapped to the binary test article.

1. Import a Betaflight raw image using the verified base and vectors.
2. Locate version/build strings and identify reset/startup behavior.
3. Correlate source functions by constants, call structure, and data references.
4. Check selected receiver/radio paths and compile-time feature presence.
5. Record confidence for each named function and preserve unresolved regions.

Completion evidence: a Ghidra analysis notebook with addresses, candidate function identities, source references, and confidence. The release label alone is not a source-to-binary proof.

## 19. Evidence templates

### 19.1 Artifact record

```yaml
artifact_id: descriptive-stable-name
file_path: firmware/category/original-file-name
artifact_kind: downloaded-binary | source-archive | target-config | converted-binary | local-build | device-readback
declared_product: unknown-or-named-product
declared_target: unknown-or-exact-target
version_or_tag: unknown-or-release
upstream_commit: unknown-or-full-hash
source_url: exact-download-url-or-unknown
acquired_at: timestamp-with-timezone
acquisition_method: download | conversion | build | readback
size_bytes: integer
sha256: full-digest
parent_artifact: required-for-conversion-or-build
tool_versions: []
build_options: []
hardware_match: confirmed | inferred | unverified | unrelated-reference
evidence_for_match: description-or-link
limitations: []
```

### 19.2 Experiment record

```text
Experiment ID:
Date/time/timezone:
Researcher/assistant:
Repository commit:
Device/board identity:
Firmware artifact + SHA-256:
Source release + relevant build conditions:
Interface/protocol/device state:
Question or hypothesis:
Expected baseline behavior:
Exact command/tool version:
Inputs and their provenance:
Observed output/log path:
Repeated-run results:
Explanation supported by evidence:
Alternative explanations:
Source/harness deviations:
Reachability and impact limitations:
Next discriminating test:
```

### 19.3 Claim/correction ledger

| Claim | Old wording/location | Evidence | Current conclusion | Confidence | Next check |
|---|---|---|---|---|---|
| E8 MCU | ESP32 stated in summaries | Generic USB descriptor; related module registry | E8 MCU unconfirmed | High confidence in the evidence gap | Obtain device-specific MCU evidence |
| Current SYNC fields | Three UID bytes stated in tool/AGENTS | 4.1.0 `OTA_Sync_s` exposes UID4/UID5 | Two explicit fields in checked struct; historical formats require review | Source-verified for this struct | Trace versioned encoding paths |
| OTA constant | Tool comment groups v3/v4 under 4 | ELRS 4.1.0 defines 4; Betaflight selects 3 or 4 | Constant must be tied to source/build | Source-verified | Record binary compile-time branch |
| Hash coverage | Four manifest entries | Eleven tracked corpus artifacts | Manifest extended in handoff | Locally verified | Keep coverage current |
| Serial probing | Utility description begins with passive capture | Default code later calls `ser.write` | Default invocation is active | Code-verified | Passive default/raw log review |
| E8 corpus completeness | “All firmware” requested | No verified E8-specific image present | Acquisition remains incomplete | Corpus-verified | Manufacturer package/history search |

### 19.4 End-of-session handoff

```text
Starting commit:
Ending commit:
Push result:
Files changed:
Newly observed facts:
Corrections and supporting evidence:
Commands/tests actually run:
Commands/tests described but not run:
Remaining uncertainty:
Local untracked or ignored work that another machine will not receive:
Next highest-value task:
```

## 20. Open questions and outstanding work

### E8 acquisition and identity

- [ ] Obtain a device-specific E8 firmware/version identifier.
- [ ] Confirm the particular E8 MCU and board revision.
- [ ] Determine the USB CDC application's protocol.
- [ ] Inspect the manufacturer-linked tool package if downloaded.
- [ ] Determine whether public E8-specific binary versions exist and enumerate them.
- [ ] Document any authorized device-readback option separately from flashing.
- [ ] Independently tie mutable master archives to exact commits if needed.

### Corpus provenance and versions

- [ ] Recover exact acquisition URLs/build metadata for the added Betaflight artifacts.
- [ ] Establish what features the prebuilt Betaflight binaries actually include.
- [ ] Correlate the historic EMAX config with the chosen modern image.
- [ ] Maintain original and derived artifact relationships and checksums.
- [ ] Keep locally generated builds separate from vendor/upstream downloads.

### Tools and protocol notes

- [ ] Resolve current versus historical SYNC identity observations.
- [ ] Make lookup version and observation width explicit.
- [ ] Report partial-key ambiguity rather than only full-UID collisions.
- [ ] Review the serial probe's CRSF labels, address handling, parser behavior, and active defaults.
- [ ] Add archival raw logging before interpreting unsuccessful decode attempts.
- [ ] Confirm dependency/version requirements in a home environment.

### Chipset/Layer 2 research

- [ ] Choose one concrete chip/build and input path for the first memory-safety study.
- [ ] Map validation and allocation/copy invariants before fuzzing.
- [ ] Produce a faithful parser harness and record stubs/changes.
- [ ] Obtain reproducible evidence before assigning DoS/RCE impact.
- [ ] Reverify relevant CVEs against primary advisories and exact target versions.
- [ ] Assess controller-to-host reachability only where the architecture supports that boundary.

### CTF preparation

- [ ] Identify competition target models and installed protocol versions.
- [ ] Obtain the RF activity rules, permitted bands, and relevant test boundaries.
- [ ] Establish scoring, flag format, and submission workflow.
- [ ] Turn tested results into a capability matrix; keep proposed exercises separate.
- [ ] Complete and log lab exercises before treating the playbook as validated.

## 21. Source links

### Project files

- [Collaboration and project context](AGENTS.md).
- [Wireless vulnerability reference](drone-wireless-vuln-reference.md).
- [Research and implementation plan](attack-research-plan.md).
- [Firmware inventory and source notes](firmware/README.md).
- [Corpus integrity manifest](firmware/SHA256SUMS).
- [Lookup helper](tools/elrs-uid-rainbow.py).
- [Serial helper](tools/elrs-serial-probe.py).

### Official upstream/manufacturer material

- [ExpressLRS source repository](https://github.com/ExpressLRS/ExpressLRS).
- [ExpressLRS 4.1.0 release](https://github.com/ExpressLRS/ExpressLRS/releases/tag/4.1.0).
- [ExpressLRS supported target registry](https://github.com/ExpressLRS/targets).
- [EMAX OLED/Nano 2.4 GHz transmitter guidance](https://www.expresslrs.org/quick-start/transmitters/emax2400/).
- [ExpressLRS SPI receiver documentation](https://www.expresslrs.org/hardware/spi-receivers/).
- [EMAX E8 custom serial configurator article](https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264-expresslrs-e8-transmitter-custom-configurator).
- [Betaflight source/releases](https://github.com/betaflight/betaflight).
- [Betaflight unified targets](https://github.com/betaflight/unified-targets).
- [OpenAI permission profile documentation](https://learn.chatgpt.com/docs/permissions).
- [OpenAI configuration basics](https://learn.chatgpt.com/docs/config-file/config-basic).

The local pinned archives are the evidence used for the source checks in this handoff. Upstream links can change; preserve release/commit identity when comparing versions.

## 22. Copy-ready continuation prompt

```text
Continue the wireless drone CTF research project in this checkout.

First run git status and git pull --ff-only, then reread AGENTS.md,
HOME-LAB-HANDOFF.md, firmware/README.md, and the relevant current tool/source.
Claude Code and Codex share this repository; preserve current work and do not
force-push or rely on remembered file contents. Commit and push completed work.

The firmware collection is for offline test articles. I do not plan to flash
the EMAX E8. There is no verified E8-specific binary or installed E8 version
in the current corpus. Related EMAX OLED/Nano modules are documented references;
the particular E8 MCU and USB application protocol still need confirmation.

Begin with the highest-value task that can be completed with the available
equipment. If working offline, verify all corpus hashes and reconcile the
ELRS lookup tool with the pinned 4.1.0 source: the checked SYNC struct exposes
UID4/UID5, while the tool expects three bytes and hard-codes OTA version 4.
Betaflight source can select OTA version 3 or 4 by USE_ELRSV4, and the prebuilt
image's feature selection has not yet been established.

The serial helper sends CRSF queries by default and has not been proven to
match the E8 vendor USB protocol. Establish device identity and protocol before
relying on it. Do not treat lack of decoded CRSF frames as proof of no traffic.

Keep observations, source-derived findings, hypotheses, and planned tests
separate. Record exact versions, source paths, artifact hashes, commands,
results, and remaining uncertainty. End with a precise next-session handoff.
```
