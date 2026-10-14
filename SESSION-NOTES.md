# Session Notes — 2026-10-14

## What This Session Accomplished

This was a full research session covering firmware acquisition, protocol analysis, tool development, and lab setup. Everything is committed and pushed to `origin/main`.

---

## 1. Repository State

**Repo:** `git@github-personal:oatmealphysics/research-project.git` (private)
**Branch:** `main` — 11 commits, all pushed
**Working tree:** clean (no uncommitted changes)

### Commit History (newest first)
```
93578f3 Add CRSF serial probe tool for USB-connected ELRS devices
7bf484a Add AGENTS.md with project context for multi-agent workflow
bcc565d Add ELRS binding phrase rainbow table generator and pre-computed table
368b0f0 Add Betaflight and ExpressLRS firmware corpus for TinyHawk III / E8
dd5f5a7 Add EMAX E8 ELRS firmware research corpus
d3f7aba Expand advanced wireless chipset exploitation research
207bc9a Add advanced chipset exploitation techniques section
cc88c7a Add Layer 2 / chipset attack research and implementation plan
b856f26 Expand drone wireless vulnerability reference
e9fbd6a Merge branch 'main' of github-personal:oatmealphysics/research-project
2385353 Add drone wireless chipset vulnerability reference
d54892e Initial commit
```

### File Inventory
```
AGENTS.md                          — Multi-agent project rules, repo structure, technical context
attack-research-plan.md            — 7-phase attack plan (~506 lines)
drone-wireless-vuln-reference.md   — CVE/chipset/attack reference (~1555 lines)
firmware/
  README.md                        — Hardware→chipset mapping, ELRS attack surface analysis
  SHA256SUMS                       — Hashes for firmware files
  betaflight/
    betaflight_2026.6.2_STM32F411_CRAZYBEEF4SX1280.hex   — TinyHawk III firmware (Intel HEX)
    betaflight_CRAZYBEEF4SX1280.bin                       — Same, raw binary for Ghidra
    betaflight_2026.6.2_STM32F411.hex                     — Base F411 firmware (Intel HEX)
    betaflight_STM32F411.bin                              — Same, raw binary
    EMAX_TINYHAWKF4SX1280.config                         — Unified target config (pin map)
  reference/
    targets.json                — ELRS target registry (confirms EMAX = ESP32 + SX1280)
    ExpressLRS-master.tar.gz    — ELRS master snapshot
    ExpressLRS-targets-master.tar.gz
    elrs-4.1.0.lua              — Lua config script (not firmware)
  src/
    expresslrs-4.1.0.tar.gz     — ELRS 4.1.0 tagged release source
    betaflight-2026.6.2.tar.gz  — Betaflight source (22 MB)
    .gitignore                  — Excludes extracted trees from git
tools/
  elrs-uid-rainbow.py           — Binding phrase → UID rainbow table generator
  elrs-rainbow-table.csv        — Pre-computed table (193 entries)
  elrs-rainbow-table.json       — Same, JSON
  elrs-serial-probe.py          — CRSF serial probe for USB-connected ELRS devices
```

---

## 2. Extracted Source Trees (LOCAL ONLY — not in git)

These exist on the lab machine but are `.gitignore`d. Re-extract at home with:
```bash
cd ~/Harness/firmware/src
tar xzf expresslrs-4.1.0.tar.gz     # → ExpressLRS-4.1.0/
tar xzf betaflight-2026.6.2.tar.gz  # → betaflight-2026.6.2/
```

### Critical source files for attack research (paths after extraction):

**ELRS binding phrase → UID derivation:**
```
firmware/src/ExpressLRS-4.1.0/src/python/build_flags.py:40
    UID = hashlib.md5('-DMY_BINDING_PHRASE="<phrase>"'.encode()).digest()[0:6]
```

**ELRS OTA packet format, CRC, nonce:**
```
firmware/src/ExpressLRS-4.1.0/src/lib/OTA/OTA.h      — Packet structs, SYNC format
firmware/src/ExpressLRS-4.1.0/src/lib/OTA/OTA.cpp     — CRC init, validation, channel packing
```

**FHSS hopping sequence generation:**
```
firmware/src/ExpressLRS-4.1.0/src/lib/FHSS/FHSS.h    — Sequence structure, freq calcs
firmware/src/ExpressLRS-4.1.0/src/lib/FHSS/FHSS.cpp   — Seed → 256-entry hop table
```

**SX1280 radio driver:**
```
firmware/src/ExpressLRS-4.1.0/src/lib/SX1280Driver/SX1280.cpp      — Radio config, TX/RX
firmware/src/ExpressLRS-4.1.0/src/lib/SX1280Driver/SX1280_Regs.h   — Register map
firmware/src/ExpressLRS-4.1.0/src/lib/SX1280Driver/SX1280_hal.cpp  — SPI HAL
```

**Betaflight SPI ELRS RX driver (runs on TinyHawk III's STM32):**
```
firmware/src/betaflight-2026.6.2/src/main/rx/expresslrs.c           — ELRS protocol handler
firmware/src/betaflight-2026.6.2/src/main/rx/expresslrs_common.c    — Shared ELRS code
firmware/src/betaflight-2026.6.2/src/main/drivers/rx/rx_sx1280.c    — SX1280 SPI driver
firmware/src/betaflight-2026.6.2/src/main/drivers/rx/rx_sx1280.h
```

---

## 3. ELRS Security Model (Summary of Key Findings)

This is the most important research output from the session. Fully documented in `firmware/README.md`.

### The Attack Chain

```
Binding phrase (human-chosen string)
    ↓ MD5('-DMY_BINDING_PHRASE="<phrase>"')[0:6]
6-byte UID
    ↓ UID[2:5] → OtaGetUidSeed()
FHSS seed (deterministic, 256-entry, 80 channels for 2.4 GHz ISM)
    ↓ UID[4:5] → OtaUpdateCrcInitFromUid()
CRC initializer (CRC14 or CRC16)
    ↓
Packet validation (CRC only, NO encryption)
```

### What's exposed over the air

- **SYNC packets broadcast UID[4] and UID[5] in plaintext** (every SYNC, sent periodically)
- **Sync channel is deterministic:** channel 40 of 80, frequency ~2440 MHz — always the same regardless of UID
- **Fox-IT (2022) demonstrated:** capture 1 SYNC packet → get 3 of 4 UID bytes → brute-force the 4th (only 128 unique sequences due to RNG weakness) → full link hijack

### What this means for CTF

1. **Passive capture + rainbow table** = instant UID recovery if they used a common binding phrase
2. **Even without the phrase**, 1 SYNC packet + timing analysis = full hijack (Fox-IT method)
3. **No UID needed at all** for pure DoS (jam the sync channel or wideband)
4. **No UID needed** to exploit firmware bugs in the Betaflight SPI ELRS driver (if any exist)

---

## 4. Hardware Details Confirmed

### TinyHawk III (drone)
- **MCU:** STM32F411CEU6 (ARM Cortex-M4, 100 MHz, 512K flash, 128K RAM)
- **Gyro:** MPU6000 on SPI bus 1
- **ELRS RX:** SX1280 on SPI bus 3 (CS=PA15, EXTI=PC14, RESET=PA8, BUSY=PA13)
- **Betaflight target:** `EMAX_TINYHAWKF4SX1280` (unified target on `CRAZYBEEF4SX1280` base)
- **Key detail:** SPI ELRS — the ELRS stack is compiled INTO Betaflight, not a separate ESP chip. No separate update path for the receiver; you flash the entire FC.

### EMAX E8 Controller (TX)
- **MCU:** ESP32 (main) + likely ESP8285 (WiFi backpack, based on similar EMAX modules)
- **RF:** SX1280 (2.4 GHz ELRS)
- **ELRS targets (closest match):** `EMAX_2400_TX` or `EMAX_NANO_2400_TX` — both `Unified_ESP32_2400_TX`
- **No E8-specific target** in the official ELRS registry
- **Upload methods:** UART, WiFi (WiFi mode = attack surface)
- **USB:** Shows as `/dev/ttyACM0` with VID:PID `8888:2001` ("Your Company USB Serial")
- **Serial status:** Connected but no CRSF response at any baud rate tested (115200, 230400, 400000, 420000, 460800, 500000, 921600). **Likely needs a battery installed and power switch on** — USB probably only charges.

---

## 5. Tools Installed on Lab Machine

### In the repo (portable)
| Tool | Location | Purpose |
|---|---|---|
| `elrs-uid-rainbow.py` | `tools/` | Rainbow table: phrase → UID → FHSS seed → CRC init |
| `elrs-serial-probe.py` | `tools/` | USB serial CRSF probe for E8 controller |

### System-level (lab machine only — reinstall at home)
| Tool | Location | Install Command |
|---|---|---|
| **Ghidra 12.1.4** | `/opt/ghidra/` | Already installed, symlinked to `ghidra` and `analyzeHeadless` |
| **OpenJDK 21** | system | `sudo apt install openjdk-21-jdk` |
| **pyserial 3.5** | pip | `pip3 install pyserial` |
| **objcopy** | system (binutils) | `sudo apt install binutils` |
| **REA** | MCP server | `npx rea-agents@latest setup` (run manually, needs approval) |

### At home you'll need to:
```bash
# 1. Clone the repo
git clone git@github-personal:oatmealphysics/research-project.git Harness

# 2. Extract source trees
cd Harness/firmware/src
tar xzf expresslrs-4.1.0.tar.gz
tar xzf betaflight-2026.6.2.tar.gz

# 3. Install Ghidra (download from ghidra-sre.org, needs JDK 21+)
sudo apt install openjdk-21-jdk
# unzip ghidra, move to /opt/ghidra, symlink ghidraRun

# 4. Install Python deps
pip3 install pyserial

# 5. Install REA (for Claude Code firmware analysis)
npx rea-agents@latest setup
```

---

## 6. Ghidra Quick-Start for Firmware Analysis

### Loading the TinyHawk III firmware
1. Launch: `ghidra`
2. File → New Project → Non-Shared → pick a directory, name it `DroneResearch`
3. File → Import File → select `firmware/betaflight/betaflight_CRAZYBEEF4SX1280.bin`
4. Language: **ARM:LE:32:Cortex** (ARM Cortex little-endian)
5. Options → Base Address: **0x08000000** (STM32 flash start)
6. Click OK, then double-click the file to open CodeBrowser
7. When prompted to analyze, click Yes with defaults

### What to look for
- **ELRS packet handler:** Search for the CRC polynomial constants `0x2E57` (CRC14) or `0x3D65` (CRC16) — these locate the ELRS OTA validation code
- **SX1280 SPI driver:** Search for SX1280 register values from `SX1280_Regs.h` (e.g., `0x0944` for `REG_LR_SYNCWORDBASEADDRESS1`)
- **Buffer handling in RX path:** Look at how received packet buffers are sized and bounded — potential overflow targets
- **Failsafe behavior:** Find what happens when the link is lost — this tells you what DoS achieves (land? hover? RTH?)

### Using REA instead (if installed)
REA can analyze the binary through Claude Code without launching the Ghidra GUI:
```
# In Claude Code, the skill "reverse-engineer-anything" is available
# It can open the .bin, decompile functions, search strings, etc.
```

---

## 7. Serial Testing of the E8 (INCOMPLETE — Next Step)

### What we tried
- Connected E8 via USB → shows as `/dev/ttyACM0` (VID `8888`, PID `2001`)
- Probed at 8 different baud rates (9600 through 921600)
- Sent CRSF pings (type `0x28` broadcast and targeted)
- No response at any baud rate, no passive data captured

### Why it didn't respond
The E8 almost certainly needs a **battery installed and the power switch turned on**. USB provides charging only, not system power to the ESP32. The USB CDC serial interface is registered by the ESP32's USB stack, which enumerates even in low-power/charging mode, but the ELRS application firmware isn't running.

### Next steps for serial testing
1. **Install an 18650 battery** and turn on the E8
2. Re-run the probe: `python3 tools/elrs-serial-probe.py --dump-params`
3. Expected results:
   - Device info response with firmware version, hardware ID
   - Parameter entries including: binding phrase status, TX power, packet rate, WiFi settings
   - Possibly the UID bytes (check if they're exposed via CRSF parameters)
4. **WiFi attack surface test:** Use the probe to read the auto-WiFi-on interval, then trigger WiFi mode and scan for the AP

### What to test once it's powered
```bash
# Basic probe with parameter dump
python3 tools/elrs-serial-probe.py --dump-params

# Listen-only mode to see what the E8 sends unprompted
python3 tools/elrs-serial-probe.py --listen-only --timeout 10

# If WiFi mode activates, scan for the AP:
sudo iwlist wlan0 scan | grep -A5 "ExpressLRS\|elrs\|EMAX"
```

---

## 8. Attack Paths to Explore Next

### A. UID-based attacks (need binding phrase or passive capture)
1. **Rainbow table lookup** — `tools/elrs-uid-rainbow.py --lookup XX:YY:ZZ` after capturing SYNC bytes
2. **Fox-IT method** — capture 1 SYNC, brute-force the 4th UID byte (128 candidates), match via timing
3. **Full link hijack** — with known UID, craft valid packets and follow the FHSS sequence

### B. Chipset-level attacks (NO UID needed)
1. **SX1280 wideband jamming** — Ettus B210 transmits across 2400-2480 MHz, breaks link, drone failsafes
2. **Sync channel targeted DoS** — jam only channel 40 (~2440 MHz) to prevent re-sync after link loss
3. **Betaflight SPI ELRS driver bugs** — audit `expresslrs.c` for buffer overflows in packet parsing; a malformed OTA packet could crash the FC
4. **ESP32 WiFi backdoor on E8** — when WiFi mode activates, connect to the AP, push malicious config or firmware

### C. Scripts from the research plan still to build
```
tools/recon-wifi-drone.sh       — Automated WiFi drone scanner
tools/wifi-dos.py               — Targeted deauth with BSSID detection
tools/tello-takeover.py         — Tello WiFi SDK command injection
tools/mavlink-injector.py       — MAVLink packet injection
tools/px4-shell.py              — PX4 shell exploit (CVE-2026-1579)
tools/sik-analyzer.py           — SiK radio analysis
tools/elrs-analyzer.py          — ELRS passive analysis and SYNC capture
tools/dji-ble-attack.py         — DJI BLE command injection
tools/esp32-ble-fuzz.py         — ESP32 BLE fuzzer
tools/gps-spoof.sh              — GPS spoofing via Ettus
tools/identify-target.sh        — Rapid target identification flowchart
```

---

## 9. Key Reference Documents in the Repo

| Document | Lines | What's in it |
|---|---|---|
| `drone-wireless-vuln-reference.md` | ~1555 | 60+ CVEs, chipset exploitation techniques, protocol attacks, toolchain reference |
| `attack-research-plan.md` | ~506 | 7-phase plan with exercises, scripts to build, open questions |
| `firmware/README.md` | ~150 | Hardware mapping, ELRS attack chain documentation, SHA-256 checksums |
| `AGENTS.md` | ~150 | Multi-agent coordination rules, coding guidelines |

---

## 10. Things That Only Exist on the Lab Machine

| Item | Location | How to recreate |
|---|---|---|
| Extracted ELRS source | `firmware/src/ExpressLRS-4.1.0/` | `tar xzf expresslrs-4.1.0.tar.gz` |
| Extracted Betaflight source | `firmware/src/betaflight-2026.6.2/` | `tar xzf betaflight-2026.6.2.tar.gz` |
| Ghidra 12.1.4 | `/opt/ghidra/` | Download from ghidra-sre.org + JDK 21 |
| REA MCP server | npm/npx | `npx rea-agents@latest setup` |
| Ghidra zip | `~/Downloads/ghidra_12.1.4_PUBLIC_20260921.zip` | Download fresh if needed |
| SSH key | `~/.ssh/Wireless_CTF_Research.ed25519` | Must exist on home machine too |
| Claude Code memory | `~/.claude/projects/-home-borealis-Harness/memory/` | Auto-managed by Claude Code |

---

## 11. SSH / Git Setup Reminder

The repo uses a custom SSH alias. Your `~/.ssh/config` must have:
```
Host github-personal
    HostName github.com
    User git
    IdentityFile ~/.ssh/Wireless_CTF_Research.ed25519
```

And the repo-level git config:
```bash
cd Harness
git config user.name "oatmealphysics"
git config user.email "dboardman13@proton.me"
```

The remote URL is: `git@github-personal:oatmealphysics/research-project.git`

---

## 12. Immediate Next Session Priorities

1. **Power on the E8** (battery + switch) and re-run `elrs-serial-probe.py --dump-params` — extract device info, firmware version, and any exposed UID/binding data
2. **Ghidra analysis** of `betaflight_CRAZYBEEF4SX1280.bin` — find the ELRS packet handler, audit for buffer overflows, map the failsafe logic
3. **Build `elrs-analyzer.py`** — passive SX1280 SYNC capture using the Ettus B210, extract UID[3:5], feed into rainbow table
4. **Test WiFi attack on E8** — trigger WiFi mode, connect to the AP, enumerate what's accessible
5. **Start building the game-day scripts** from the plan (start with `identify-target.sh` for rapid protocol fingerprinting)
