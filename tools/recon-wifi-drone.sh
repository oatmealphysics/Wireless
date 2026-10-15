#!/usr/bin/env bash
# recon-wifi-drone.sh — Passive WiFi drone scanner
#
# Scans for drone-associated WiFi networks and clients using monitor mode.
# Identifies known drone SSIDs, MACs, and probe requests.
#
# Requires: airmon-ng, airodump-ng (aircrack-ng suite), iw
#
# Usage:
#   recon-wifi-drone.sh                          # auto-detect monitor interface
#   recon-wifi-drone.sh -i wlan0mon              # specify monitor interface
#   recon-wifi-drone.sh -i wlan0mon --json       # JSON output to stdout
#   recon-wifi-drone.sh -i wlan0mon --setup wlan0  # put wlan0 into monitor mode first
#
# Output is line-oriented for piping into the dashboard. Each line is:
#   WIFI|<timestamp>|<type>|<json-fields>
#
# For CTF research and authorized security testing only.

set -euo pipefail

IFACE=""
JSON_MODE=0
SETUP_IFACE=""
SCAN_DURATION=10
OUTDIR=""

usage() {
    echo "Usage: $0 [-i monitor_iface] [--setup iface] [--json] [--duration N] [--outdir DIR]" >&2
    echo "" >&2
    echo "  -i IFACE       Monitor-mode interface (e.g., wlan0mon)" >&2
    echo "  --setup IFACE  Put IFACE into monitor mode before scanning" >&2
    echo "  --json         Output JSON lines to stdout" >&2
    echo "  --duration N   Seconds per scan cycle (default: 10)" >&2
    echo "  --outdir DIR   Directory for airodump CSV files" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i) IFACE="$2"; shift 2 ;;
        --setup) SETUP_IFACE="$2"; shift 2 ;;
        --json) JSON_MODE=1; shift ;;
        --duration) SCAN_DURATION="$2"; shift 2 ;;
        --outdir) OUTDIR="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

# Known drone SSID patterns (case-insensitive grep)
DRONE_SSID_PATTERNS=(
    "TELLO"
    "ARDRONE"
    "AR.Drone"
    "DJI"
    "Skydio"
    "Parrot"
    "Autel"
    "3DR"
    "YUNEEC"
    "FIMI"
    "EVO"
    "Phantom"
    "Mavic"
    "FPV"
    "HUBSAN"
    "SYMA"
    "Holy.*Stone"
    "Potensic"
    "Ryze"
)

# Known drone OUI prefixes (first 3 bytes of MAC)
DRONE_OUI_PREFIXES=(
    "60:60:1F"   # Tello / Ryze
    "34:D2:62"   # Tello alternate
    "A0:14:3D"   # Parrot
    "90:03:B7"   # Parrot
    "00:26:7E"   # Parrot
    "00:12:1C"   # Parrot legacy
    "60:60:1F"   # DJI
    "48:1C:B9"   # DJI Mavic
    "34:D2:62"   # DJI
    "C8:2C:2B"   # DJI
)

log() {
    echo "[$(date '+%H:%M:%S')] $*" >&2
}

emit() {
    local ts type fields
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    type="$1"
    shift
    if [[ $JSON_MODE -eq 1 ]]; then
        echo "{\"source\":\"wifi\",\"timestamp\":\"$ts\",\"type\":\"$type\",$*}"
    else
        echo "WIFI|$ts|$type|$*"
    fi
}

check_oui() {
    local mac="$1"
    local prefix="${mac:0:8}"
    prefix="$(echo "$prefix" | tr '[:lower:]' '[:upper:]')"
    for oui in "${DRONE_OUI_PREFIXES[@]}"; do
        if [[ "$prefix" == "$oui" ]]; then
            return 0
        fi
    done
    return 1
}

match_ssid() {
    local ssid="$1"
    for pat in "${DRONE_SSID_PATTERNS[@]}"; do
        if echo "$ssid" | grep -qiE "$pat"; then
            echo "$pat"
            return 0
        fi
    done
    return 1
}

# Setup monitor mode if requested
if [[ -n "$SETUP_IFACE" ]]; then
    log "Setting up monitor mode on $SETUP_IFACE..."
    if command -v airmon-ng &>/dev/null; then
        sudo airmon-ng check kill 2>/dev/null || true
        sudo airmon-ng start "$SETUP_IFACE" 2>/dev/null
        IFACE="${SETUP_IFACE}mon"
        log "Monitor interface: $IFACE"
    else
        log "airmon-ng not found, trying iw..."
        sudo ip link set "$SETUP_IFACE" down
        sudo iw dev "$SETUP_IFACE" set type monitor
        sudo ip link set "$SETUP_IFACE" up
        IFACE="$SETUP_IFACE"
        log "Monitor interface: $IFACE"
    fi
fi

# Auto-detect monitor interface
if [[ -z "$IFACE" ]]; then
    IFACE="$(iw dev 2>/dev/null | awk '/Interface/{iface=$2} /type monitor/{print iface; exit}')" || true
    if [[ -z "$IFACE" ]]; then
        log "ERROR: No monitor-mode interface found."
        log "Use --setup <iface> or manually put an interface in monitor mode."
        exit 1
    fi
    log "Auto-detected monitor interface: $IFACE"
fi

# Verify interface exists
if ! ip link show "$IFACE" &>/dev/null; then
    log "ERROR: Interface $IFACE does not exist."
    exit 1
fi

# Create temp directory for scan output
if [[ -z "$OUTDIR" ]]; then
    OUTDIR="$(mktemp -d /tmp/wifi-recon-XXXXXX)"
fi
mkdir -p "$OUTDIR"
log "Scan output dir: $OUTDIR"

cleanup() {
    # Kill background airodump if running
    [[ -n "${DUMP_PID:-}" ]] && kill "$DUMP_PID" 2>/dev/null || true
    wait "$DUMP_PID" 2>/dev/null || true
}
trap cleanup EXIT

SEEN_BSSIDS=()

log "Starting WiFi drone scan on $IFACE (cycle: ${SCAN_DURATION}s)..."
log "Press Ctrl-C to stop."

CYCLE=0
while true; do
    CYCLE=$((CYCLE + 1))
    PREFIX="$OUTDIR/scan-$(printf '%04d' $CYCLE)"

    # Run airodump for one cycle
    if command -v airodump-ng &>/dev/null; then
        timeout "$SCAN_DURATION" airodump-ng \
            --write "$PREFIX" \
            --write-interval 1 \
            --output-format csv \
            --band abg \
            "$IFACE" 2>/dev/null &
        DUMP_PID=$!
        wait "$DUMP_PID" 2>/dev/null || true
        DUMP_PID=""

        # Parse the CSV output
        CSV_FILE="${PREFIX}-01.csv"
        if [[ -f "$CSV_FILE" ]]; then
            IN_AP=0
            IN_CLIENT=0
            while IFS= read -r line; do
                # Detect section headers
                if echo "$line" | grep -q "^BSSID"; then
                    IN_AP=1
                    IN_CLIENT=0
                    continue
                fi
                if echo "$line" | grep -q "^Station MAC"; then
                    IN_AP=0
                    IN_CLIENT=1
                    continue
                fi

                if [[ $IN_AP -eq 1 ]]; then
                    BSSID="$(echo "$line" | cut -d',' -f1 | tr -d ' ')"
                    [[ -z "$BSSID" || "$BSSID" == "BSSID" ]] && continue
                    CHANNEL="$(echo "$line" | cut -d',' -f4 | tr -d ' ')"
                    POWER="$(echo "$line" | cut -d',' -f9 | tr -d ' ')"
                    ESSID="$(echo "$line" | awk -F',' '{print $NF}' | sed 's/^ *//')"

                    # Check if this is a drone
                    DRONE_MATCH=""
                    if DRONE_MATCH="$(match_ssid "$ESSID")"; then
                        true
                    elif check_oui "$BSSID"; then
                        DRONE_MATCH="OUI"
                    else
                        continue
                    fi

                    emit "AP" "\"bssid\":\"$BSSID\",\"essid\":\"$ESSID\",\"channel\":$CHANNEL,\"power\":$POWER,\"match\":\"$DRONE_MATCH\",\"drone\":true"
                fi

                if [[ $IN_CLIENT -eq 1 ]]; then
                    CLIENT_MAC="$(echo "$line" | cut -d',' -f1 | tr -d ' ')"
                    [[ -z "$CLIENT_MAC" ]] && continue
                    ASSOC_BSSID="$(echo "$line" | cut -d',' -f6 | tr -d ' ')"
                    PROBES="$(echo "$line" | awk -F',' '{print $NF}' | sed 's/^ *//')"

                    if check_oui "$CLIENT_MAC"; then
                        emit "CLIENT" "\"mac\":\"$CLIENT_MAC\",\"associated\":\"$ASSOC_BSSID\",\"probes\":\"$PROBES\",\"match\":\"OUI\",\"drone\":true"
                    fi

                    # Check probe requests for drone SSIDs
                    if [[ -n "$PROBES" ]]; then
                        if PROBE_MATCH="$(match_ssid "$PROBES")"; then
                            emit "PROBE" "\"mac\":\"$CLIENT_MAC\",\"probes\":\"$PROBES\",\"match\":\"$PROBE_MATCH\",\"drone\":true"
                        fi
                    fi
                fi
            done < "$CSV_FILE"
            rm -f "${PREFIX}"*.csv "${PREFIX}"*.cap 2>/dev/null || true
        fi
    else
        # Fallback: use iw scan (slower, requires managed mode or scan support)
        log "airodump-ng not available, falling back to iw scan..."
        SCAN_OUT="$(sudo iw dev "$IFACE" scan 2>/dev/null)" || true
        CURRENT_BSSID=""
        CURRENT_SSID=""
        CURRENT_SIGNAL=""
        CURRENT_FREQ=""
        while IFS= read -r line; do
            if [[ "$line" =~ ^BSS\ ([0-9a-f:]+) ]]; then
                # Emit previous if it was a drone
                if [[ -n "$CURRENT_BSSID" && -n "$CURRENT_SSID" ]]; then
                    if match_ssid "$CURRENT_SSID" &>/dev/null || check_oui "$CURRENT_BSSID"; then
                        emit "AP" "\"bssid\":\"$CURRENT_BSSID\",\"essid\":\"$CURRENT_SSID\",\"freq\":\"$CURRENT_FREQ\",\"signal\":\"$CURRENT_SIGNAL\",\"drone\":true"
                    fi
                fi
                CURRENT_BSSID="${BASH_REMATCH[1]}"
                CURRENT_SSID=""
                CURRENT_SIGNAL=""
                CURRENT_FREQ=""
            elif [[ "$line" =~ signal:\ (.+) ]]; then
                CURRENT_SIGNAL="${BASH_REMATCH[1]}"
            elif [[ "$line" =~ freq:\ (.+) ]]; then
                CURRENT_FREQ="${BASH_REMATCH[1]}"
            elif [[ "$line" =~ SSID:\ (.+) ]]; then
                CURRENT_SSID="${BASH_REMATCH[1]}"
            fi
        done <<< "$SCAN_OUT"
        # Emit last entry
        if [[ -n "$CURRENT_BSSID" && -n "$CURRENT_SSID" ]]; then
            if match_ssid "$CURRENT_SSID" &>/dev/null || check_oui "$CURRENT_BSSID"; then
                emit "AP" "\"bssid\":\"$CURRENT_BSSID\",\"essid\":\"$CURRENT_SSID\",\"freq\":\"$CURRENT_FREQ\",\"signal\":\"$CURRENT_SIGNAL\",\"drone\":true"
            fi
        fi
        sleep "$SCAN_DURATION"
    fi
done
