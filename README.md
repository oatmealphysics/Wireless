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

# research-project

Wireless security research toolkit for drone CTF competition. Covers reconnaissance, protocol analysis, exploit development, and defensive detection across WiFi, BLE, MAVLink, ExpressLRS, and CRSF protocols.

**For authorized CTF research and security testing against hardware you own.** See [SECURITY.md](SECURITY.md) for the full authorization boundary and safety guardrails.

Current work is coordinated through the [project roadmap](PROJECT-ROADMAP.md).
It is the sole active priority and status source; `SESSION-NOTES.md` is the
append-only evidence log.

## Prerequisites

- **Python 3.10+** (3.12 recommended)
- **Git**
- **Linux** recommended for full scanner hardware support; Windows and macOS work for offline analysis and SITL testing

Command examples use `python3` on Linux/macOS. Inside an activated Windows
virtual environment, use `python` for the same module or script.

### Optional hardware dependencies (for live scanning)

| Component | Package / Tool | Used by |
|---|---|---|
| WiFi scanning | `aircrack-ng`, `iw`, `nmcli` | `recon-wifi-drone.sh`, WiFi exploit chains |
| BLE scanning | `bleak` (pip), `bluetoothctl` | `ble-recon-scanner.py`, BLE recon |
| SDR (receive/transmit) | `SoapySDR`, `numpy`, hardware driver module | ELRS tools, passive telemetry, SDR recon |
| MAVLink | `pymavlink` (pip) | MAVLink recon, fuzzer, injector, SITL harness |
| Serial ports | `pyserial` (pip) | CRSF serial probe, MSP observer, serial MAVLink |
| Video capture | `opencv-python` (pip) | Tello video capture |
| WiFi deauth | `scapy` (pip) | Evil twin AP (optional fallback) |
| ArduPilot SITL | `firmware/ardupilot/arducopter_SITL_x86_64_4.7.2` | MSP injection sim, SITL integration tests |

## Setup

```bash
# Clone the repository
git clone https://github.com/oatmealphysics/research-project.git
cd research-project

# (Optional) Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install web dashboard dependencies
python3 -m pip install -r web/requirements.txt

# Install SDR dependencies (if using Ettus USRP hardware)
pip install numpy SoapySDR
sudo apt install uhd-host libuhd-dev soapysdr-module-uhd  # Debian/Ubuntu

# Install MAVLink dependencies
pip install pymavlink

# Install BLE dependencies
pip install bleak

# Install serial dependencies
pip install pyserial
```

## Running

### Web dashboard

```bash
# Simulation mode (no hardware)
python3 web/app.py --simulate

# Live mode with hardware (the current nmcli backend uses its default interface)
python3 web/app.py --sdr-args "driver=uhd"
```

See [web/README.md](web/README.md) for the full Web UI guide.

### Terminal dashboard

```bash
python3 tools/recon-dashboard.py --simulate
```

### Individual tools

```bash
# ELRS offline analysis
python3 tools/elrs-packet-forger.py --phrase "iloveflying" info
python3 tools/elrs-uid-rainbow.py generate --wordlist wordlist.txt -o rainbow.json

# MAVLink recon (passive, no transmission)
python3 tools/mavlink-recon.py --udp 0.0.0.0:14550

# WiFi drone scan
sudo tools/recon-wifi-drone.sh -i wlan0mon --json
```

See [tools/README.md](tools/README.md) for the complete tool reference.

## Running Tests

```bash
# Project validation (syntax, manifest, links — no hardware, no network)
python3 -B tools/validate-project.py

# Tool unit tests
python3 -B -m unittest discover -s tools/tests -p "test_*.py" -v

# Web dashboard backend tests
python3 -m pip install -r web/requirements-e2e.txt
python3 -B -m unittest discover -s tools/tests -p "test_web_dashboard.py" -v

# Web dashboard route, factory, lifecycle, and state-history tests
python3 -B -m unittest discover -s web/tests -p "test_api_routes.py" -v
python3 -B -m unittest discover -s web/tests -p "test_app_factory.py" -v
python3 -B -m unittest discover -s web/tests -p "test_app_lifecycle.py" -v
python3 -B -m unittest discover -s web/tests -p "test_state_histories.py" -v

# Web dashboard browser tests (Playwright + Chrome)
python3 -B -m unittest discover -s web/tests -p "test_dashboard_browser.py" -v
```

CI runs the offline validator/tool suite and all dashboard backend, route,
lifecycle, state-history, and browser checks on Ubuntu and Windows with Python
3.10 and 3.12. See
[`.github/workflows/validate.yml`](.github/workflows/validate.yml).

## Project Structure

```
research-project/
├── web/                    # Browser-based dashboard (Flask)
│   ├── app.py              # Main application
│   ├── templates/          # HTML templates
│   ├── tests/              # Route, factory, lifecycle, state, and browser tests
│   └── README.md           # Web UI quick-start guide
├── tools/                  # Security research tools
│   ├── exploit_chains/     # Automated multi-stage attack scripts
│   ├── elrs_core/          # Shared ELRS crypto primitives
│   ├── tests/              # Tool unit tests
│   └── README.md           # Tool reference
├── research/               # Research notes, analysis, findings
├── references/             # Per-device evidence dossiers
├── firmware/               # Firmware corpus and inventory
├── docs/                   # Methodology and process documentation
├── .github/workflows/      # CI configuration
├── SECURITY.md             # Authorization boundary and safety guardrails
└── README.md               # This file
```

## Contributing

### Branch workflow

1. Create a feature branch from `main`:
   ```bash
   git checkout main && git pull
   git checkout -b your-branch-name
   ```
2. Make your changes and commit with clear messages.
3. Push and open a pull request against `main`.

### Code standards

- **Python 3.10+** — use type hints where they aid readability.
- **No third-party dependencies for core tool logic** — the WiFi and MAVLink chains run on stdlib only. SDR, BLE, serial, and video tools declare their dependencies in docstrings.
- **Safe defaults for new work** — new or materially changed transmit/write paths must default to dry-run or read-only and use an explicit, documented execution gate. Existing tools do not share one universal flag; see `tools/README.md`.
- **Machine-readable output where promised** — preserve a tool's documented JSON/line-oriented mode and keep status messages on stderr. Do not assume every legacy tool has structured stdout.
- **Private data isolation** — private UIDs, captures, decoded frames, and device-specific configs belong under ignored `work/`. A raw capture enters the tracked Git-LFS corpus only after an explicit publication decision plus provenance/hash documentation; otherwise do not commit it.

### Adding a new tool

1. Add the tool to `tools/` with a descriptive docstring covering usage, dependencies, execution category, and its explicit state-changing gate when applicable.
2. Default to dry-run/read-only behavior.
3. Add unit tests in `tools/tests/` covering the dry-run path and safety interlocks.
4. Update `tools/README.md` with a one-line description in the appropriate section.
5. Run the full test suite before submitting.

### Safety review checklist (for tools with live/state-changing capability)

Before merging a tool that can transmit, inject, write, or change device state, verify:

- [ ] Safe default and an explicit, documented execution gate
- [ ] Target and version checks
- [ ] Hard bounds on effects (payload count, duration, RF power)
- [ ] Log redaction (no credentials or private identifiers in output)
- [ ] Timeout and partial-failure behavior
- [ ] Tests for the dry-run/preview path
- [ ] Documentation distinguishing capability from authorization

See [SECURITY.md](SECURITY.md) for the full review requirements.

## Key References

- [Current project roadmap](PROJECT-ROADMAP.md)
- [Security and safety guardrails](SECURITY.md)
- [Wireless vulnerability reference](research/drone-wireless-vuln-reference.md)
- [Research plan](research/attack-research-plan.md)
- [Firmware corpus](firmware/README.md)
- [Per-device evidence dossiers](references/README.md)
- [Firmware → Vulnerability → PoC methodology](docs/firmware-to-poc-methodology.md)
- [New hardware research blueprint](docs/new-hardware-research-blueprint.md)
- [2026 defensive hardware/firmware/protocol expansion](research/defensive-attack-surface-expansion-2026-10-29.md)
- [Vulnerability evidence audit](research/CVE-AUDIT.md)
- [Project and collaboration rules](AGENTS.md)
