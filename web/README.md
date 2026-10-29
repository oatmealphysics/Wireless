# Drone Scanner Web UI

Local Flask dashboard for the project's synthetic demo targets and optional
WiFi, BLE, SDR, MAVLink, and telemetry scanner backends. Live scan results are
heuristic candidates: a matching name, an energy peak, or an unsigned MAVLink
frame is not by itself proof of device identity, vulnerability, or control.

Run all commands below from the repository root. Examples use `python3` on
Linux/macOS; use `python` inside an activated Windows virtual environment.

## Quick start: simulation

```bash
python3 -m pip install -r web/requirements.txt
python3 web/app.py --simulate
```

Open <http://127.0.0.1:5000>. Simulation uses a fixed synthetic target catalog
(timestamps may vary) and deterministic hardware status, does not call the live
scanner or hardware detector, and keeps actions in dry-run mode. It does not
require project hardware.

## Runtime modes

| Option | Default | Behavior |
|---|---:|---|
| `--host` | `127.0.0.1` | Address for Flask's development server. The default is loopback-only. |
| `--port` | `5000` | Development-server TCP port. |
| `--simulate` | off | Use the fixed synthetic target catalog and probe-free deterministic `not_checked` hardware status. |
| `--live-actions` | off | Permit implemented live action paths. It does not enable or configure scanners. |
| `--debug` | off | Enable Flask debug/reload behavior; the reloader can repeat CLI startup probes. |
| `--wifi-iface` | `wlan0` | Requested WiFi interface recorded with scans. The current `nmcli` backend does not force interface selection. |
| `--ble-iface` | `hci0` | Requested BLE controller recorded with scans. The current `bluetoothctl` backend uses the system default controller. |
| `--sdr-args` | `driver=uhd` | SoapySDR device arguments used by SDR backends. |

The combinations that matter are:

- `--simulate`: synthetic scans and dry-run actions. Adding `--live-actions`
  does not turn a simulated scan into a live one.
- no mode flag: live scanner and hardware-probe paths, but actions remain
  dry-run.
- `--live-actions` without `--simulate`: live scanner paths and implemented
  live actions. This is the only mode that can issue action-side network or
  device operations.

The CLI performs one hardware probe before starting the live development
server. In live mode, page initialization and each **Refresh** request perform
another uncached probe. Importing the module or constructing an app through
`create_app()` does not run the CLI startup probe.

## Live backend behavior and dependencies

One background worker owns a scan. It runs the selected backends sequentially,
emits per-backend status events, and publishes the collected target list after
the selected backends finish. `scan_time` is passed to each selected adapter;
adapters currently interpret, cap, or ignore it differently, so it is not a
total wall-clock limit.

| Backend | Current behavior | Dependency / local effect |
|---|---|---|
| WiFi | Name-pattern candidate discovery | `nmcli`; requests an active WiFi rescan |
| BLE | Name-pattern candidate discovery | `bluetoothctl`; runs active BLE discovery |
| SDR | Energy-based candidate discovery plus MAVLink UDP listeners | SoapySDR, NumPy, and a device driver for receive-only RF; also binds/listens on local UDP ports |
| MAVLink | Parses received UDP frames | Python standard library; binds and listens on local UDP |
| Telemetry | SDR energy sweep labeled `ELRS-like FHSS` after repeated hits | SoapySDR, NumPy, and a device driver; receive-only RF, no frame decode or identity proof |

Hardware reporting is informational. It does not select protocols or suppress
an unavailable backend. WiFi reporting uses `nmcli`/`ip`; BLE reporting uses
`hciconfig` or a Bleak fallback; SDR reporting enumerates SoapySDR devices; and
MAVLink reporting only checks whether local UDP listener ports can bind. A
successful UDP check does not prove that a peer or hardware device is present.
Bleak is also used by the live BLE GATT action, not by the primary BLE scanner.

Example live launch:

```bash
python3 web/app.py --wifi-iface wlan0 --ble-iface hci0 --sdr-args "driver=uhd"
```

## Dashboard behavior

### Static assets and browser policy

The HTML shell loads the versioned same-origin assets
`/static/dashboard.v1.css` and `/static/dashboard.v1.js`. They are cacheable for
one year, so a content change must also bump the filename and the two template
references. The template and generated UI fragments do not depend on inline
scripts, style blocks, `style=` attributes, or inline event handlers.

Every app response carries a strict Content Security Policy. Its default is
`'none'`; scripts, styles, fonts, images, fetches, and the EventSource connection
are limited to the same origin, while inline script/style attributes, framing,
plugins, and base-URL changes are denied. Companion headers disable MIME
sniffing and framing, keep referrers private, and isolate cross-origin resources.
The policy is a browser hardening boundary, not an authorization mechanism for
live backends or actions.

### Scans and targets

Only one scan may run per app instance. Starting another scan, or clearing
targets while a scan is active, returns HTTP 409. The UI shows backend states
(`idle`, `scanning`, `done`, or `error`) and renders dynamic values through
escaped text/attribute helpers and event listeners; scanner-provided values are
not inserted into inline event handlers.

Displayed scores, threat labels, and vulnerability text are classification
hints. Synthetic targets contain fixture values; live targets derive them from
scanner metadata and heuristics.

### Actions

The default action mode is dry-run. Implemented behavior differs by action:

| Family | Current behavior |
|---|---|
| Tello | Live mode can send SDK commands. **Capture Video** sends `streamon`; it does not record a file. |
| Parrot | Live mode locally sends UDP AT commands for land/watchdog actions or opens Telnet for the control-process action. |
| MAVLink | **Sniff Traffic** opens a local UDP listener. Each implemented live command sends five UDP packet copies. **Replay Packets** only returns CLI guidance; the web route does not replay packets. |
| ELRS | **Preview FHSS** and **Forge Disarm** construct offline candidates. **Deep ELRS Scan** is receive-only SDR. No web ELRS action has an RF transmit path. |
| BLE | Service enumeration opens an active BLE connection and reads GATT metadata; it is not passive observation. |

Dry-run is a safety property, not an authorization mechanism. Review a live
action's implementation and target before enabling it.

### Chain 2 API

Chain 2 currently has API routes but no dedicated dashboard panel:

- `POST /api/chain2/simulate` builds MAVLink command packets and records a
  simulated result history. It does not simulate a complete WiFi transition.
- `POST /api/chain2/inject` is dry-run while live actions are disabled. With
  live actions enabled and a valid `host:port` endpoint, it sends five UDP
  packet copies; without an endpoint the result remains `pending`.
- `GET /api/chain2/commands` lists supported command definitions.
- `GET /api/chain2/status` returns the bounded Chain 2 history and latest state.

## API reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard HTML |
| `/api/status` | GET | Current mode, scan state, targets, and cached hardware state |
| `/api/scan` | POST | Start one background scan |
| `/api/targets/<id>` | GET | Target detail |
| `/api/targets/<id>/action` | POST | Execute or preview `{"action": "name"}` |
| `/api/actions` | GET | Action definitions |
| `/api/log` | GET | Latest 50 retained action-log entries |
| `/api/clear` | POST | Clear targets while no scan is active |
| `/api/hardware` | GET | Probe-free deterministic status in simulation; uncached local detection in live mode |
| `/api/events` | GET | Server-Sent Events stream |
| `/api/chain2/status` | GET | Chain 2 state and history |
| `/api/chain2/simulate` | POST | Build simulated Chain 2 command results |
| `/api/chain2/inject` | POST | Preview a command or send five UDP copies under live-action gating |
| `/api/chain2/commands` | GET | Chain 2 command definitions |

If supplied, `POST /api/scan` requires `protocols` to be a nonempty subset of
`wifi`, `ble`, `sdr`, `mavlink`, and `telemetry`; omitting it selects the
default set. `scan_time` must be numeric, greater than zero, and no more than
300 seconds. The request may override `wifi_iface`, `ble_iface`, and `sdr_args`
for that scan.

`/api/events` accepts the standard `Last-Event-ID` header or `?last_id=N`.
Domain event types are `scan_start`, `scanner_start`, `scanner_done`,
`scanner_error`, `scan_failed`, `target_found`, `scan_complete`,
`action_executed`, and `targets_cleared`. The UI does not display every event
type directly.

## State and lifecycle

`create_app()` creates an isolated `DashboardState` unless one is supplied.
One state may belong to exactly one Flask app, one scan lifecycle may run per
app, and a runtime reset requires a quiescent worker. The exported module-level
`app` remains for WSGI and compatibility use; tests and integrations should
create fresh state/app pairs.

Event, action, and Chain 2 histories retain at most 500, 200, and 200 entries.
`/api/log` returns only the latest 50 action entries. A new SSE connection
starts at the current event tail; a reconnect cursor resumes with newer events
that are still retained.

## Validation

Install the browser-test requirements before the web suites:

```bash
python3 -m pip install -r web/requirements-e2e.txt
```

Run the maintained validator and no-browser dashboard suites from the
repository root:

```bash
python3 -B tools/validate-project.py
python3 -B -m unittest discover -s tools/tests -p "test_web_dashboard.py" -v
python3 -B -m unittest discover -s web/tests -p "test_api_routes.py" -v
python3 -B -m unittest discover -s web/tests -p "test_app_factory.py" -v
python3 -B -m unittest discover -s web/tests -p "test_app_lifecycle.py" -v
python3 -B -m unittest discover -s web/tests -p "test_state_histories.py" -v
```

Google Chrome must already be installed for the browser suite; installing the
Playwright Python package does not install the Chrome channel.

Linux/macOS:

```bash
DASHBOARD_BROWSER_CHANNEL=chrome \
  python3 -B -m unittest discover -s web/tests -p "test_dashboard_browser.py" -v
```

PowerShell:

```powershell
$env:DASHBOARD_BROWSER_CHANNEL = "chrome"
python -B -m unittest discover -s web/tests -p "test_dashboard_browser.py" -v
```

The browser suite forces simulation and dry-run modes, fails if a live scan,
live action, or hardware probe is called, serves the app on an ephemeral
loopback port, and aborts and reports dashboard-page HTTP(S) requests to
non-loopback hosts. That is an application-flow guarantee, not a claim that the
browser process or operating system has no other network activity. It also
asserts the exact Content Security Policy, observes browser CSP violations,
confirms both versioned assets loaded, and retains the 390 px viewport gate.

GitHub Actions runs the validator/tool suite and every dashboard suite on
Ubuntu and Windows with Python 3.10 and 3.12. See
[`validate.yml`](../.github/workflows/validate.yml).
