# Tools

Security research tools for drone CTF competition. Covers reconnaissance, protocol analysis, exploit chain automation, and defensive detection across WiFi, BLE, MAVLink, ELRS, and CRSF protocols.

**All tools are for authorized CTF research against hardware you own.** See [SECURITY.md](../SECURITY.md) for the full authorization boundary and safety guardrails.

## Conventions

- Every tool with transmit/write capability defaults to **dry-run** or **read-only** mode.
- The `--arm` flag (or `--execute`, `--live-actions`) is required to enable any RF transmission, command injection, or state-changing action.
- Output to stdout is structured (JSON lines or line-oriented) for piping. Errors and warnings go to stderr.
- Private data (UIDs, captures, decoded frames) belongs under the ignored `work/` directory.

## Reconnaissance

| Tool | Protocol | Description |
|---|---|---|
| `recon-dashboard.py` | All | Curses-based TUI that runs WiFi, MAVLink, and ELRS scanners in parallel and shows a unified target view. Supports `--simulate` for demo mode and `--export` for JSON output. |
| `recon-wifi-drone.sh` | WiFi | Passive WiFi drone scanner using monitor mode (aircrack-ng suite). Identifies drone SSIDs, MACs, and probe requests. Line-oriented output for dashboard piping. |
| `mavlink-recon.py` | MAVLink | Passive MAVLink sniffer on UDP/serial. Extracts system info, GPS, telemetry from MAVLink v1/v2 traffic. Requires `pymavlink`. |
| `ble-recon-scanner.py` | BLE | Scans for drone-related BLE peripherals and enumerates GATT services/characteristics. Flags unauthenticated write endpoints. Requires `bleak`. |
| `passive-telemetry-interceptor.py` | CRSF/ELRS, MAVLink, FrSky | Receive-only telemetry decoder via SoapySDR or IQ file. Tracks positions, battery, protocol fingerprints without transmitting. |

## ELRS Analysis & Exploitation

### Core Library (`elrs_core/`)

Shared cryptographic primitives vendored from the ExpressLRS and Betaflight sources: CRC computation (`crc.py`), FHSS sequence generation (`fhss.py`), OTA packet framing (`ota.py`), and v3 compatibility layer (`v3_compat.py`). See `elrs_core/PROVENANCE.md` for source pinning.

### IQ Capture & PHY

| Tool | Description |
|---|---|
| `elrs-sync-channel-record.py` | Record receive-only IQ on the ELRS v3 sync channel candidate (2441.4 MHz). Outputs cf32le with a `.meta.txt` sidecar. |
| `elrs-iq-symbols.py` | Extract candidate LoRa chirp symbols from a local IQ file. Front-end demodulator for the SX1280 SF7 benchmark. Reports ranked FFT-bin alternatives, not decoded bytes. |
| `elrs-phy-scan.py` | Locate LoRa PHY candidates in a bounded IQ file interval using energy detection and preamble/sync-word verification. |
| `elrs-batch-evidence.py` | Batch wrapper around `elrs-iq-symbols.py` for known burst start estimates. Outputs ranked symbol hypotheses to private JSONL. |
| `elrs-capture-intake.py` | Inventory and integrity checker for private IQ captures (hash, sample alignment, Nyquist span). Read-only, never opens a radio. |

### Offline Decode & Validation

| Tool | Description |
|---|---|
| `elrs-li-offline.py` | SX1280 SF7 4/8 long-interleaver decoder for ranked symbol JSONL. Reports CRC-verified decoded frames. Algorithm adapted from WaveShark (GPL-3.0-or-later). |
| `elrs-li-forward.py` | Offline SX1280 encoder and ELRS v3 MSP canary planner. Inverts the long-interleaver mapping; prepares read-only MSP_API_VERSION request packets. |
| `elrs-v3-frame-check.py` | Offline CRC/field checker for pre-decoded Betaflight 4.4.2 ELRS v3 8-byte frames. Validates against a private UID file. |
| `elrs-v3-fhss-check.py` | Cross-checks pre-decoded frames against the source-derived FHSS sequence for a given UID. Aggregate-only output. |
| `elrs-v3-hop-timing-check.py` | Compares passive PHY timing with a source-derived hop plan. Fits packet clock from sync-channel visits and validates other channels. |
| `elrs-capture-forge-validate.py` | End-to-end validation: captured frames → UID extraction → rainbow table → forge → CRC cross-check. Validates the complete Chain 1 software path without transmitting. |

### Attack Tools

| Tool | Description |
|---|---|
| `elrs-analyzer.py` | OTA packet analyzer for pre-decoded ELRS bytes. Rainbow table matching, simulation mode, dashboard-oriented output. |
| `elrs-packet-forger.py` | Craft valid ELRS OTA packets (SYNC, RC, MSP) from a binding phrase. Supports OTA v3 and v4. Simulation mode by default; `--arm` for RF injection. |
| `elrs-tx-emulator.py` | Live ELRS TX emulation via SoapySDR or SPI (SX1280). Takeover and inject modes with correct FHSS hopping. Requires `--arm`. |
| `elrs-uid-rainbow.py` | Generate rainbow tables mapping binding phrases to UIDs, FHSS seeds, and CRC initializers. Supports v3/v4 lookup from captured UID suffix bytes. |
| `elrs-attack-profile.py` | Derive offline attack parameters (UID, CRC, FHSS) from a phrase, UID, WiFi config JSON, or rainbow table match. Output is a JSON profile. |
| `elrs-wifi-extract.py` | Read-only extraction of UID and config from an ELRS TX/RX in WiFi update mode. Never POSTs to write endpoints. |
| `elrs-serial-probe.py` | Read-only CRSF serial diagnostics: port listing, live capture, frame decode. `--probe` for discovery reads; no writes. |

### V3 Canary & Injection Planning

| Tool | Description |
|---|---|
| `elrs-v3-canary-schedule.py` | Build a capture-relative FHSS slot plan joining a decoded SYNC observation with a read-only MSP canary. No SDR backend. |
| `elrs-v3-fresh-sync-clock.py` | Convert a fresh decoded SYNC into future SDR slot timestamps with processing lead time. No transmit backend. |
| `elrs-v3-rc-replay-canary.py` | Select and validate a safe captured RC frame for replay (low throttle, disarmed, centered sticks). Fails closed on unsafe frames. |
| `elrs-v3-bounded-rc-injection.py` | Synthesize one fail-closed bounded-RC injection canary keeping AUX1 low and throttle at minimum. No radio backend. |
| `elrs-v3-rate-switch-latch-model.py` | Offline model of a Betaflight 4.4.2 rate-switch latch transition. Host-only hypothesis, no packets built. |
| `elrs-rc-overlap.py` | Compare decoded OTA RC channels with a simultaneous MSP_RC log for timing validation. |

### Anomaly Detection (Defensive)

| Tool | Description |
|---|---|
| `elrs-anomaly-detector.py` | Detect forged, replayed, or anomalous ELRS packets: UID mismatch, unexpected FHSS channel, nonce reuse, CRC failure, rate anomalies, binding-mode probes. |

## MAVLink

| Tool | Description |
|---|---|
| `mavlink-injector.py` | Command injection tool exploiting unsigned MAVLink v1/v2. Sniffs traffic to discover target system ID, then injects chosen commands. Requires `--arm` to transmit. |
| `mavlink-fuzzer.py` | Protocol fuzzer: signing detection, command enumeration, boundary testing, state-change identification. `--passive` (default) or `--active`. Requires `pymavlink`. |
| `mavlink-mitm-proxy.py` | MitM proxy between GCS and drone. Log, inspect, modify, inject, or drop MAVLink packets in transit. Works with ArduPilot SITL. |
| `mavlink-signing-replay.py` | Capture and replay signed MAVLink v2 packets within the anti-replay tolerance window. Supports cross-link replay. |
| `mavlink-signing-enforcer.py` | **Defensive.** Validate MAVLink v2 signing: reject unsigned packets, detect replay/timestamp manipulation, flag dangerous commands from unknown sources. |

## MSP (MultiWii Serial Protocol)

| Tool | Description |
|---|---|
| `msp-vuln-scanner.py` | Enumerate dangerous MSP commands on a Betaflight endpoint (SITL TCP or serial). Classify by arming-state protection and ELRS reachability. |
| `msp-injection-sim.py` | Validate MSP-over-ELRS attack chains against Betaflight SITL. Exercises force-disarm, reboot, motor-override, failsafe-rewrite, GPS-spoof, RX-config-write, RC-override. |
| `msp-elrs-hijack-poc.py` | End-to-end ELRS session hijack simulation: SYNC capture → UID recovery → FHSS prediction → CRC derivation → connection sim → MSP injection. All offline. |
| `msp-elrs-attack-chains.py` | Multi-step MSP attack chain orchestrator: drop, hijack, failsafe-ambush, GPS-redirect, destabilize, full-kill sequences with timing simulation. All offline. |
| `tinyhawk-msp-rc-observe.py` | Bounded, read-only MSP RC-channel observer for the TinyHawk III Plus. Requires explicit flags, exclusive port, and removed propellers for live reads. |

## CRSF Protocol

| Tool | Description |
|---|---|
| `crsf-packet-tool.py` | Decode, encode, and forge CRSF frames (RC channels, command frames, force-disarm, bind). Serial sniffing via `--port`. All TX/inject requires `--arm`. |

## WiFi

| Tool | Description |
|---|---|
| `evil-twin-ap.py` | Create a rogue AP mimicking a drone's WiFi network. Scan, clone, optional deauth, DHCP, DNS spoof, traffic forwarding. Requires `--arm` for active attacks. |
| `evil-twin-detector.py` | **Defensive.** Detect evil twin attacks: duplicate SSIDs, signal anomalies, channel changes, BSSID changes, rapid AP appearance. |
| `tello-takeover.py` | Tello SDK command injection over the unauthenticated UDP interface. Interactive or single-command mode. |
| `tello-video-capture.py` | Capture H.264 video stream from a commandeered Tello as proof-of-control. Requires `opencv-python`. |

## GPS

| Tool | Description |
|---|---|
| `gps-spoof-poc.py` | Generate fake NMEA/UBX GPS frames (static position, drift, RTL-spoof). Prints to stdout by default; `--serial` to send to a flight controller under test. |

## Exploit Chains (`exploit_chains/`)

Automated multi-stage attack scripts. See [`exploit_chains/README.md`](exploit_chains/README.md) for full documentation.

| Script | Protocol | Description |
|---|---|---|
| `elrs_chain.py` | ELRS 2.4 GHz | Scan → rainbow table → offline packet/hop preview (RF TX disabled) |
| `mavlink_chain.py` | MAVLink UDP/serial | Sniff → detect signing → inject or replay |
| `wifi_tello_chain.py` | WiFi (Tello/Parrot) | Scan for drone APs → default creds → send commands |
| `wifi_mavlink_chain.py` | WiFi → MAVLink | Evil twin AP → MitM → MAVLink command injection |
| `multi_recon.py` | All | Parallel WiFi + BLE + SDR scan → rank → auto-launch best chain |
| `combined_attack_demo.py` | All | Combined multi-protocol attack demonstration |
| `chain1-dry-run.py` | ELRS | Chain 1 dry-run validation script |

## Infrastructure

| Tool | Description |
|---|---|
| `sdr_common.py` | Generic SDR abstraction via SoapySDR. Works with USRP, HackRF, RTL-SDR, LimeSDR, BladeRF. |
| `sitl_harness.py` | ArduPilot SITL test harness for offline MAVLink integration testing. Manages SITL lifecycle, localhost-only. |
| `validate-project.py` | Offline repository checks: Python syntax, firmware manifest, JSON/CSV consistency, CVE-index coverage, Markdown link validation. No network, no hardware. |
| `audit-cve-records.py` | Audit CVE record index against research findings. |
| `inspect-e8-configurator.py` | Inspect EMAX E8 configurator protocol. |
| `inspect-e8-serial-handlers.py` | Inspect E8 serial handler behavior. |
| `e8-read-config.py` | Read-only E8 configuration extraction. |

## Data Files

| File | Description |
|---|---|
| `elrs-rainbow-table.json` | Pre-computed binding phrase → UID lookup table (JSON) |
| `elrs-rainbow-table.csv` | Same rainbow table in CSV format |

## Tests

Unit tests live in `tests/`. Run them with:

```bash
python3 -m unittest discover -s tools/tests -p "test_*.py" -v
```

Tests cover dry-run paths, safety interlocks, CRC validation, packet encoding/decoding, and exploit chain logic. No tests require hardware or RF transmission.
