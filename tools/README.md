# Tools

Command-line utilities for offline analysis, local simulation, capture
processing, scanner integration, and protocol experiments across WiFi, BLE,
MAVLink, ELRS, and CRSF. Run commands in this guide from the repository root.
Examples use `python3` on Linux/macOS; use `python` inside an activated Windows
virtual environment.

**Use tools only within the authorization and device constraints in
[SECURITY.md](../SECURITY.md).** A tool's presence in this catalog is not an
instruction to run its hardware, serial, network, or transmit path.

## Execution categories

The repository predates one uniform CLI contract. Do not assume that every
tool uses `--arm`, defaults to dry-run, or emits JSON. Read the named tool's
`--help` and the description below before use.

| Category | Meaning |
|---|---|
| **Offline / file-only** | Reads local fixtures or captures and writes only named local outputs. |
| **Loopback / SITL** | Uses local processes or loopback sockets; it does not require project hardware. |
| **Receive-only hardware** | Opens a radio or capture device for observation without an RF transmit path. |
| **Active read / query** | May transmit a discovery/read request, open a serial port, connect to BLE, or bind/listen on a socket even when it does not intentionally change configuration. |
| **Transmit / state-changing** | Can send commands, RF, or writes. Gates are tool-specific; there is no repository-wide `--arm` guarantee. |

Private UIDs, raw captures, decoded bytes, device configuration, and other
pairing-sensitive material belong under the ignored `work/` directory.
Sanitized aggregate reports may be printed to stdout; output format varies by
tool.

## Reconnaissance

| Tool | Protocol | Description |
|---|---|---|
| `recon-dashboard.py` | All | Curses TUI for simulated or live WiFi, MAVLink, and ELRS scanner backends. `--simulate` is offline; live mode inherits each backend's receive/query behavior. |
| `recon-wifi-drone.sh` | WiFi | Passive WiFi drone scanner using monitor mode (aircrack-ng suite). Identifies drone SSIDs, MACs, and probe requests. Line-oriented output for dashboard piping. |
| `mavlink-recon.py` | MAVLink | Receive-oriented MAVLink parser on UDP or serial. It opens the selected socket/port and extracts system info, GPS, and telemetry. Requires `pymavlink`. |
| `ble-recon-scanner.py` | BLE | Active BLE discovery and optional GATT connection/read for drone-related peripherals. Requires `bleak`; this is not passive RF observation. |
| `passive-telemetry-interceptor.py` | CRSF/ELRS, MAVLink, FrSky | Receive-only RF or file decoder. It has no RF transmit path, but optional `--dashboard` output sends decoded metadata over UDP. |

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
| `elrs-passive-pipeline.py` | Maintained fail-closed wrapper for IQ/ranked-candidate input, SX1280 long-interleaver decode, v3 CRC validation, optional independent byte cross-check, and sanitized reporting. |
| `elrs-sync-capture.py` | Archived prototype. Its CLI fails closed because its PHY/frame model is not valid for the capture-validated path. Use record → `elrs-phy-scan.py` → `elrs-passive-pipeline.py`. |

### Offline Decode & Validation

| Tool | Description |
|---|---|
| `lora_decode.py` | Library decoder for the capture-validated SX1280 SF7 4/8 long-interleaver mapping. Its generic transpose/LFSR helpers are synthetic round-trip models, not the live-link decoder. |
| `elrs-li-offline.py` | Independent SX1280 SF7 4/8 long-interleaver decoder for ranked JSONL. Reports CRC-valid frames. SYNC frames self-check their transmitted suffix; RC validation transfers a consistent suffix learned from separate SYNC frames. Algorithm adapted from WaveShark (GPL-3.0-or-later). |
| `elrs-li-forward.py` | Offline SX1280 encoder and ELRS v3 MSP canary planner. Inverts the long-interleaver mapping; prepares read-only MSP_API_VERSION request packets. |
| `elrs-v3-frame-check.py` | Offline CRC/field checker for pre-decoded Betaflight 4.4.2 ELRS v3 8-byte frames. Validates against a private UID file. |
| `elrs-v3-fhss-check.py` | Cross-checks pre-decoded frames against the source-derived FHSS sequence for a given UID. Aggregate-only output. |
| `elrs-v3-hop-timing-check.py` | Compares passive PHY timing with a source-derived hop plan. Fits packet clock from sync-channel visits and validates other channels. |
| `elrs-capture-forge-validate.py` | Offline CRC/frame recomputation against ranked candidates, with optional phrase, private UID, or rainbow input. A pass establishes software self-consistency and capture agreement only—not full UID recovery, measured FHSS agreement, RF interoperability, receiver acceptance, or operational control. |

### Maintained passive capture path

For the tracked ranked-candidate corpus, the dependency-free path is:

```bash
python3 -B tools/elrs-passive-pipeline.py \
  --ranked-jsonl captures/oct15-ranked-2026-10-16.jsonl \
  captures/tinyhawk-rc-retry-full-phy-2026-10-16.jsonl \
  --cross-check-reference
```

The command always creates a new byte-bearing report below ignored
`work/pipeline/` and prints only a sanitized aggregate to stdout. Ranked mode
is file-only and requires no NumPy or SciPy. Raw-IQ mode delegates candidate
extraction to `elrs-phy-scan.py` and requires NumPy and SciPy; see
[`captures/README.md`](../captures/README.md) for capture provenance and raw-IQ
reproduction prerequisites.

The tracked result is **PASSIVE-CAPTURE-VALIDATED** in the roadmap evidence
vocabulary. The CLI's legacy JSON value `CAPTURE-VALIDATED` maps to that label:
62 ranked candidates yield 54 CRC-valid frames (16 SYNC and 38 RC), while 8
remain unresolved. The independent `elrs-li-offline.py` byte path agrees on
all 54 valid frames with zero mismatches or false positives. This does not
establish a complete UID, transmitter identity, measured multi-channel FHSS
agreement, receiver acceptance, or operational capability.

### Attack Tools

| Tool | Description |
|---|---|
| `elrs-analyzer.py` | OTA packet analyzer for pre-decoded ELRS bytes. Rainbow table matching, simulation mode, dashboard-oriented output. |
| `elrs-packet-forger.py` | Experimental offline packet constructor with optional raw-byte output and no RF backend. It parses `--arm`, but that flag currently enables no transmission path. Version-specific output still requires independent validation. |
| `elrs-tx-emulator.py` | Experimental dry-run/SoapySDR/SPI prototype. `--arm` gates its hardware path, but the Soapy waveform is explicitly not SX1280-compatible and no receiver-acceptance evidence exists. Do not treat preview output as a validated live transmitter. |
| `elrs-uid-rainbow.py` | Generate rainbow tables mapping binding phrases to UIDs, FHSS seeds, and CRC initializers. Supports v3/v4 lookup from captured UID suffix bytes. |
| `elrs-attack-profile.py` | Derive offline attack parameters (UID, CRC, FHSS) from a phrase, UID, WiFi config JSON, or rainbow table match. Output is a JSON profile. |
| `elrs-wifi-extract.py` | Mixed mode: `--uid` is offline; `--scan` requests an active WiFi rescan; `--target` performs HTTP GET; and mDNS performs discovery. `--ssid`/`--auto` delete a matching saved NetworkManager profile and connect to an AP before the GET requests. It does not POST to known ELRS write endpoints, but those connection modes do mutate host network state. |
| `elrs-serial-probe.py` | Defaults to listen-only CRSF serial diagnostics. `--probe` is active serial I/O that sends read requests; it is not intended to write configuration. |

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
| `tello-takeover.py` | Active Tello SDK command sender over UDP. Interactive and single-command modes can send without a repository-standard `--arm` gate; inspect the target and command before use. |
| `tello-video-capture.py` | Capture H.264 video stream from a commandeered Tello as proof-of-control. Requires `opencv-python`. |

## GPS

| Tool | Description |
|---|---|
| `gps-spoof-poc.py` | Offline NMEA/UBX generator by default. `--serial` actively writes generated frames and has no repository-standard `--arm` gate. |

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
| `e8-read-config.py` | Active serial query for E8 configuration reads. It is not an offline parser and remains subject to the owned-device constraints in `AGENTS.md`. |

## Data Files

| File | Description |
|---|---|
| `elrs-rainbow-table.json` | Pre-computed binding phrase → UID lookup table (JSON) |
| `elrs-rainbow-table.csv` | Same rainbow table in CSV format |

## Tests

Tool tests live in `tools/tests/`. The maintained passive decoder/pipeline gates
are dependency-free:

```bash
python3 -B -m unittest discover -s tools/tests -p "test_elrs_passive_pipeline.py" -v
python3 -B -m unittest discover -s tools/tests -p "test_sx1280_capture_decode.py" -v
```

Run the offline project validator and full tool suite with:

```bash
python3 -B tools/validate-project.py
python3 -B -m unittest discover -s tools/tests -p "test_*.py" -v
```

The maintained CI suite does not require project hardware or RF transmission.
Tests for unavailable optional integrations may skip when their dependency is
not installed; CI installs the explicit dashboard/E2E requirements in the web
job. See [the web guide](../web/README.md#validation) for its route, lifecycle,
state-history, and browser commands.
