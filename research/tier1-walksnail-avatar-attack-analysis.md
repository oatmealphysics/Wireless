# Tier 1.3: Walksnail Avatar — Attack Surface Analysis

**Date:** 2026-10-31
**Status:** Research complete — firmware RE toolkit buildable offline, WiFi exploit requires hardware
**Context:** Authorized security research

---

## Executive Summary

Walksnail Avatar is a digital FPV system jointly developed by Caddx and Walksnail, competing with DJI's digital FPV ecosystem. The system runs **Linux on an Artosyn AR9201 SoC** (ARM Cortex-A7 quad-core) with WiFi connectivity for firmware updates and configuration. A **published root exploit** grants persistent SSH access via WiFi with default credentials, and the firmware update format (OTRA containers with RSA signatures) has been partially reverse-engineered. The attack surface is broad: unauthenticated WiFi, Linux userland, U-Boot boot chain, SPI NOR flash with known partition layout, and a video link operating on shared 5.8 GHz spectrum.

---

## 1. Hardware Architecture

### 1.1 System-on-Chip: Artosyn AR9201

| Feature | Detail |
|---------|--------|
| **CPU** | ARM Cortex-A7 quad-core @ 1.5 GHz |
| **DSP** | CEVA XM4/XM6 (video processing) |
| **Baseband** | 2T4R, 2.4 GHz + 5.8 GHz dual-band |
| **Video codec** | H.264 + H.265 hardware encoder/decoder |
| **Secure boot** | Boot from internal ROM; RSA signature chain |
| **RAM** | External DDR4 2400 MHz (256–512 MB typical) |
| **Flash** | External SPI NOR (Winbond W25Q01JV, 128 MiB) |
| **Package** | BGA, custom Artosyn silicon |

The AR9201 is Artosyn's drone-focused SoC, designed for HD digital video transmission. It integrates the RF baseband, video codec, and application processor on a single die. The same silicon family powers multiple digital FPV systems.

### 1.2 Memory Architecture

| Component | Type | Size | Notes |
|-----------|------|------|-------|
| **Boot ROM** | Internal mask ROM | Small | First-stage bootloader, RSA verify |
| **SPI NOR Flash** | Winbond W25Q01JV | 128 MiB | Firmware, config, calibration |
| **DDR4 RAM** | Micron/Samsung | 256–512 MiB | Runtime, video buffers |

### 1.3 Flash Partition Map (Winbond W25Q01JV)

From brick recovery documentation, the 128 MiB SPI NOR flash uses this partition layout:

| Partition | Offset | Size | Description |
|-----------|--------|------|-------------|
| `uboot0` | 0x000000 | 512 KiB | Primary U-Boot |
| `uboot1` | 0x080000 | 512 KiB | Backup U-Boot |
| `uboot-env0` | 0x100000 | 64 KiB | U-Boot environment |
| `uboot-env1` | 0x110000 | 64 KiB | Backup environment |
| `dtb0` | 0x120000 | 128 KiB | Device tree blob |
| `dtb1` | 0x140000 | 128 KiB | Backup DTB |
| `kernel0` | 0x160000 | 4 MiB | Linux kernel |
| `kernel1` | 0x560000 | 4 MiB | Backup kernel |
| `rootfs0` | 0x960000 | 14 MiB | Root filesystem |
| `rootfs1` | 0x1760000 | 14 MiB | Backup rootfs |
| `usr0` | 0x2560000 | 48 MiB | User partition 0 |
| `usr1` | 0x5560000 | 48 MiB | User partition 1 |
| `cal` | 0x8560000 | 1 MiB | RF calibration data |
| `res` | 0x8660000 | Remaining | Reserved |

**Key observation:** Dual-bank A/B update scheme. Each critical partition (U-Boot, kernel, rootfs, usr) has a backup copy, enabling atomic firmware updates with rollback.

### 1.4 Boot Chain

```
Internal ROM → U-Boot (SPI NOR) → Linux kernel → rootfs → application
     ↓              ↓                  ↓
  RSA verify    env vars          Device tree
  (hardware)    (flash)           (flash)
```

1. **Internal ROM**: Hardware RSA verification of U-Boot image
2. **U-Boot**: Selects active bank (0 or 1), loads kernel + DTB
3. **Linux kernel**: Standard ARM Linux boot with device tree
4. **Rootfs**: Embedded Linux userland with Walksnail applications
5. **Application layer**: Video TX/RX, OSD, WiFi services

---

## 2. Communication Links & Attack Entry Points

### 2.1 WiFi Interface (PRIMARY ATTACK SURFACE)

| Property | Detail |
|----------|--------|
| **Mode** | Access point (STA mode also possible) |
| **Default SSID** | Device-specific (e.g., "Avatar-XXXX") |
| **Default password** | `12345678` (8-digit WPA2) |
| **IP address** | `192.168.42.1` (device), DHCP for clients |
| **Services** | HTTP (firmware update), SSH (after root exploit) |
| **Range** | ~50m typical (WiFi range) |

The WiFi interface is the primary configuration and firmware update channel. It is active:
- During firmware updates
- When explicitly enabled by user (varies by firmware version)
- Some firmware versions enable WiFi briefly on boot

### 2.2 Video Link (5.8 GHz)

| Property | Detail |
|----------|--------|
| **Frequency** | 5.8 GHz ISM band |
| **Modulation** | Proprietary OFDM (Artosyn baseband) |
| **Bandwidth** | 20/40 MHz channels |
| **Encryption** | Unknown (likely minimal) |
| **Latency** | ~22–30 ms glass-to-glass |
| **Range** | 3–5 km typical |

The video link protocol is proprietary and not yet publicly documented in detail. The Artosyn baseband handles modulation/demodulation in hardware.

### 2.3 UART / Debug Interfaces

| Interface | Notes |
|-----------|-------|
| **UART console** | Present on PCB test pads; U-Boot + Linux console |
| **SPI flash** | Direct flash read/write via test clips (SOIC-8/16) |
| **JTAG/SWD** | Potentially present but undocumented |

Physical debug interfaces provide full access if the device can be opened.

---

## 3. Published Vulnerabilities & Exploits

### 3.1 WiFi Root Exploit (aj22r/walksnail-root)

**Impact:** Persistent root SSH access via WiFi
**Complexity:** Low (connect to WiFi → run script)
**Requirements:** WiFi connectivity to the device

**Attack chain:**
1. Connect to device WiFi AP (default password `12345678`)
2. Exploit leverages a DRAM scanning technique to locate process memory
3. Injects payload that enables SSH daemon (`dropbear`)
4. SSH access at `192.168.42.1:22` with known/default credentials
5. **Persistent** — survives reboots (modifies filesystem)

**Post-exploitation capabilities:**
- Full root filesystem access (read/write)
- Process inspection and manipulation
- Network configuration changes
- Firmware partition read/dump
- RF calibration data extraction
- Configuration file modification
- Service manipulation (start/stop/modify daemons)

### 3.2 Default WiFi Credentials

The default WiFi password `12345678` is trivially guessable and widely known in the FPV community. Many users never change it, providing easy initial access to any Walksnail device in WiFi range.

### 3.3 Firmware Update Security

| Aspect | Detail |
|--------|--------|
| **Container format** | OTRA (proprietary) |
| **Signature** | RSA (key length unknown, likely 2048-bit) |
| **Encryption** | None observed (payload appears cleartext) |
| **Downgrade protection** | Unknown — version rollback may be possible |
| **Update channel** | WiFi HTTP upload (no TLS) |

The OTRA firmware format uses RSA signatures for integrity verification, but the firmware payload itself does not appear to be encrypted. This means:
- Firmware images can be analyzed/reverse-engineered offline
- Modified firmware cannot be installed without the signing key (unless the verification can be bypassed)
- The signing key is the critical security gate

---

## 4. Attack Vectors

### 4.1 Attack A: WiFi → Root Shell (PROVEN)

**Difficulty:** Trivial | **Hardware:** None (WiFi adapter) | **Range:** ~50m

1. Scan for Walksnail WiFi APs (SSID pattern: "Avatar-*", "Walksnail-*")
2. Connect with default password `12345678`
3. Run published root exploit (DRAM scan + payload injection)
4. SSH to `192.168.42.1` as root
5. Full device control achieved

**Mitigation:** Change WiFi password (but exploit may bypass auth entirely)

### 4.2 Attack B: Firmware Analysis & Modification

**Difficulty:** Medium | **Hardware:** None (offline RE) | **Range:** N/A

1. Download official firmware updates from Walksnail distribution channels
2. Parse OTRA container format (header + RSA signature + payload)
3. Extract filesystem images (kernel, rootfs, usr partitions)
4. Analyze Linux userland for additional vulnerabilities
5. Map running services, open ports, IPC mechanisms
6. Identify hardcoded credentials, debug backdoors, insecure defaults

**Limitation:** Cannot flash modified firmware without RSA signing key bypass.

### 4.3 Attack C: Physical Flash Dump + Boot Chain Analysis

**Difficulty:** Medium | **Hardware:** SPI flash programmer + SOIC clip | **Range:** Physical

1. Open device enclosure (VTX or goggles)
2. Attach SOIC clip to Winbond W25Q01JV flash chip
3. Full 128 MiB flash dump (partition map is known)
4. Extract and analyze each partition:
   - U-Boot: bootloader config, environment variables, boot arguments
   - Kernel: Linux kernel image + device tree → driver/module analysis
   - Rootfs: full Linux filesystem → service audit, credential extraction
   - Cal: RF calibration data → frequency/power characterization
5. Modify U-Boot environment to enable debug output or change boot behavior
6. Write modified flash back (bypasses RSA if U-Boot env is unsigned)

**Key insight:** The U-Boot environment partitions (`uboot-env0`, `uboot-env1`) may not be RSA-signed like the boot images. If environment variables control boot behavior (e.g., `bootargs`, `console=`, `init=`), modifying them could redirect the boot process without defeating RSA signing.

### 4.4 Attack D: Video Link Disruption (RF)

**Difficulty:** Low (jamming) to High (protocol exploitation) | **Hardware:** SDR | **Range:** RF range

1. **Jamming:** 5.8 GHz wideband noise defeats the OFDM video link
   - Legal implications: intentional interference is illegal in most jurisdictions
   - Effective but unsophisticated
2. **Channel analysis:** SDR capture of video link transmissions
   - OFDM demodulation and protocol reverse engineering
   - Artosyn baseband protocol analysis
3. **Targeted disruption:** If protocol is understood, selective frame corruption

### 4.5 Attack E: Supply Chain / Update Interception

**Difficulty:** Medium | **Hardware:** None (network position) | **Range:** Network

1. Firmware updates delivered over HTTP (not HTTPS) via WiFi
2. MITM during firmware update process
3. Deliver modified firmware image
4. **Limitation:** RSA signature verification should catch modified images
5. **But:** If the update client doesn't properly verify, or if there's a TOCTOU race between verification and flashing, modified firmware could be installed

### 4.6 Attack F: Post-Root Persistence & Weaponization

**Difficulty:** Low (after root) | **Prerequisites:** Attack A success

Once root access is achieved via WiFi exploit:
1. **Firmware backdoor:** Modify rootfs to persist SSH/reverse shell across updates
2. **OSD injection:** Manipulate on-screen display to show false information
3. **Video manipulation:** Access to video pipeline allows frame injection/modification
4. **RF parameter tampering:** Modify TX power, frequency, channel settings
5. **Telemetry spoofing:** Inject false telemetry data to goggles/ground station
6. **Bricking:** Corrupt boot partitions to permanently disable device
7. **Lateral movement:** If device is on a network, pivot to other connected systems

---

## 5. Offline Toolkit Architecture

### 5.1 What's Buildable Without Hardware

```
walksnail-toolkit/
├── otra_parser.py           # OTRA firmware container parser
│   - Header parsing (magic, version, size fields)
│   - RSA signature extraction
│   - Payload extraction (kernel, rootfs, usr images)
│   - Partition identification and splitting
│
├── flash_analyzer.py        # SPI NOR flash dump analyzer
│   - Partition map parser (known offsets from recovery docs)
│   - U-Boot environment extractor and decoder
│   - Filesystem image extraction (squashfs/jffs2/ext4)
│   - Cross-reference with OTRA container contents
│
├── wifi_scanner.py          # Walksnail device WiFi discovery
│   - Passive scan for Avatar/Walksnail SSIDs
│   - WPA2 default credential testing (12345678)
│   - Service enumeration post-connect (HTTP, SSH, etc.)
│   - Network topology mapping
│
└── vuln_audit.py            # Post-root vulnerability auditor
    - Service enumeration (open ports, running daemons)
    - Credential search (hardcoded passwords, keys)
    - Configuration audit (insecure defaults)
    - Filesystem permission analysis
```

### 5.2 What Requires Hardware

| Component | Hardware Needed | Purpose |
|-----------|----------------|---------|
| WiFi root exploit | WiFi adapter + Walksnail device | Validate published exploit |
| Flash dump | SPI programmer + SOIC clip + device | Full firmware extraction |
| Video link RE | SDR (5.8 GHz capable) + device | Protocol reverse engineering |
| UART console | USB-UART adapter + device | Debug access, U-Boot interaction |
| OTRA format RE | Official firmware file | Offline analysis (downloadable) |

### 5.3 Firmware Acquisition

Official Walksnail firmware updates can be downloaded from:
- Walksnail/Caddx official website
- Community mirrors (FPV forums)
- Extracted from Walksnail Avatar app (Android/iOS)

These files are the primary input for offline OTRA format analysis.

---

## 6. Comparison to Current ELRS/Betaflight Target

| Dimension | ELRS/Betaflight | Walksnail Avatar |
|-----------|----------------|------------------|
| **Protocol** | ELRS (SX1280 SPI) + CRSF + MSP | Proprietary OFDM + WiFi + Linux |
| **OS** | Bare metal (STM32) | Linux (ARM Cortex-A7) |
| **Authentication** | Binding phrase (MD5-derived) | WiFi WPA2 (default: 12345678) |
| **Encryption** | None (ELRS) | WPA2 (WiFi), unknown (video) |
| **Root access** | Firmware RE needed | Published exploit (trivial) |
| **Attack complexity** | RF protocol knowledge needed | Connect to WiFi → root shell |
| **Command richness** | MSP + CRSF channels | Full Linux userland |
| **Firmware format** | STM32 binary + Betaflight .hex | OTRA container (RSA signed) |
| **Update channel** | USB DFU / ELRS WiFi | WiFi HTTP (no TLS) |
| **Physical debug** | SWD + UART (STM32) | UART + SPI + JTAG |
| **Target population** | Growing (FPV racing) | Growing (digital FPV) |
| **Prior exploits** | NCC Group ELRS findings | WiFi root exploit published |

**Key difference:** Walksnail runs a full Linux OS, making post-exploitation far more powerful than bare-metal targets. A root shell gives access to the entire Linux toolchain — file systems, networking, process control, and the video pipeline. The WiFi attack surface requires zero RF expertise.

---

## 7. Risk Assessment

| Factor | Rating | Notes |
|--------|--------|-------|
| **Technical feasibility** | **VERY HIGH** | Published root exploit, default WiFi creds |
| **Hardware accessibility** | **MEDIUM** | Need Walksnail device for live testing |
| **Novelty** | **HIGH** | Linux-based FPV RE is relatively new territory |
| **Impact** | **CRITICAL** | Full root access → video manipulation, bricking |
| **Remediation** | **PARTIAL** | RSA signing exists but WiFi exploit bypasses it |
| **Target population** | **LARGE** | Growing digital FPV market (Avatar, Walksnail HD) |
| **Transferability** | **HIGH** | Artosyn SoC family used in multiple products |

---

## 8. Priority Next Steps

1. **OTRA firmware format RE** (offline, no hardware) — Download official firmware, reverse the container format, extract filesystem images
2. **Filesystem analysis** (offline after step 1) — Audit extracted rootfs for additional vulns, hardcoded creds, debug interfaces
3. **WiFi root exploit validation** (needs Walksnail device) — Reproduce published exploit, document post-exploitation capabilities
4. **U-Boot environment analysis** (needs flash dump OR firmware extract) — Determine if env vars are signed, assess boot chain bypass potential
5. **Video link protocol RE** (needs SDR + device) — Long-term goal, requires significant RF work

---

## Sources

- aj22r/walksnail-root (GitHub) — WiFi root exploit tool
- Walksnail Avatar brick recovery documentation — Flash partition map, hardware details
- repair.wiki — Artosyn AR9201 SoC identification and specifications
- Walksnail Avatar community reverse engineering (various FPV forums)
- Artosyn product briefs (AR9201 series)
- FPV community firmware analysis threads
