# Drone Scanner Web UI

Browser-based dashboard that wraps the project's recon engine and exploit chain modules into a unified interface. Discover drone targets across WiFi, BLE, SDR, MAVLink, and passive telemetry — then inspect vulnerabilities and execute actions from one place.

## Quick Start

```bash
# Install dependencies
python3 -m pip install -r web/requirements.txt

# Run in simulation mode (no hardware needed)
python3 web/app.py --simulate

# Open http://127.0.0.1:5000 in a browser
```

The dashboard starts on port 5000 by default. Use `--port` to change it:

```bash
python3 web/app.py --simulate --port 8080
```

## Modes

| Flag | Behavior |
|---|---|
| `--simulate` | Demo mode with simulated targets (Tello, Parrot AR.Drone 2, MAVLink quad, ELRS TX, DJI BLE). No hardware required. |
| _(no flag)_ | Live scanning using real scanner backends (WiFi, BLE, SDR, MAVLink UDP, passive telemetry). |
| `--live-actions` | Enable live action execution. Without this flag, all actions run in dry-run mode even during live scans. |
| `--debug` | Flask debug mode with auto-reload. |

## Hardware Configuration

When running in live mode, the dashboard auto-detects available hardware at startup. Override defaults with CLI flags:

```bash
python3 web/app.py --wifi-iface wlan0mon --ble-iface hci0 --sdr-args "driver=uhd"
```

| Flag | Default | Description |
|---|---|---|
| `--wifi-iface` | `wlan0` | WiFi interface for scanning |
| `--ble-iface` | `hci0` | Bluetooth HCI device |
| `--sdr-args` | `driver=uhd` | SoapySDR device arguments |

The `/api/hardware` endpoint returns the detected hardware status as JSON.

## Dashboard Features

### Scanner Panel

Click **Scan** to start a multi-protocol scan. In simulation mode, five synthetic targets appear after a short delay. In live mode, each scanner backend (WiFi, BLE, SDR, MAVLink, telemetry) runs in a background thread and reports results as they arrive.

Scanner status indicators show real-time progress: idle, scanning, done, or error.

### Target Cards

Each discovered target shows:
- **Protocol** — WiFi, BLE, MAVLink, ELRS, or telemetry
- **Threat level** — CRITICAL / HIGH / MEDIUM / LOW, computed from an exploitability score
- **Vulnerabilities** — detected issues (open WiFi, no MAVLink signing, predictable FHSS, exposed BLE, etc.)
- **Available actions** — protocol-specific operations you can trigger

### Actions

Actions are protocol-specific. In dry-run mode (the default), they log what _would_ happen without transmitting:

| Protocol | Actions |
|---|---|
| WiFi (Tello) | Force Land, Emergency Stop, Takeoff, Capture Video, Zero RC |
| WiFi (Parrot) | Force Land, Watchdog Reset, Kill Control Process |
| MAVLink | Sniff Traffic, Disarm Motors, Force Land, Return to Launch, Reboot FC, Replay Packets |
| ELRS | Forge Disarm Packet, Preview FHSS Sequence, Deep ELRS Scan |
| BLE | Scan BLE Services |

### Chain 2: WiFi → MAVLink Injection

The dashboard includes a dedicated Chain 2 panel for WiFi-to-MAVLink attack simulation. Endpoints:

- `POST /api/chain2/simulate` — run a simulated attack sequence
- `POST /api/chain2/inject` — inject a MAVLink command (dry-run unless `--live-actions` and a target endpoint are provided)
- `GET /api/chain2/commands` — list available Chain 2 attack commands
- `GET /api/chain2/status` — current Chain 2 state

### Real-Time Events

The dashboard uses Server-Sent Events (SSE) at `/api/events` for live updates. Event types include `scan_start`, `scanner_done`, `target_found`, `scan_complete`, `action_executed`, and `targets_cleared`.

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard HTML page |
| `/api/status` | GET | Current scan state, targets, hardware, and mode |
| `/api/scan` | POST | Start a scan. Body: `{"protocols": ["wifi","ble","sdr","mavlink","telemetry"], "scan_time": 15}` |
| `/api/targets/<id>` | GET | Detail for a specific target |
| `/api/targets/<id>/action` | POST | Execute an action. Body: `{"action": "land"}` |
| `/api/actions` | GET | All action definitions |
| `/api/log` | GET | Last 50 action log entries |
| `/api/clear` | POST | Clear all discovered targets |
| `/api/hardware` | GET | Detected hardware status |
| `/api/events` | GET | SSE stream for real-time updates |

## Testing

### Unit tests (no browser)

```bash
python3 -m unittest discover -s tools/tests -p "test_web_dashboard.py" -v
```

### Browser tests (Playwright + Chrome)

```bash
python3 -m pip install -r web/requirements-e2e.txt
python3 -m unittest discover -s web/tests -p "test_dashboard_browser.py" -v
```

The browser tests start the app on a loopback port in simulation/dry-run mode, drive it with Playwright, and block all non-loopback network requests.

## Dependencies

- **Runtime:** Flask (`web/requirements.txt`)
- **E2E tests:** Flask + Playwright (`web/requirements-e2e.txt`)
- **Scanner backends (live mode):** SoapySDR, numpy, bleak, scapy, pyserial, nmcli, hciconfig — see the main project README for full installation
