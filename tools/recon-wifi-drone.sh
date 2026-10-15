#!/usr/bin/env bash
#
# recon-wifi-drone.sh — Automated WiFi drone AP scanner
#
# Puts a wireless interface into monitor mode, captures beacons briefly,
# and pattern-matches SSIDs against known drone naming conventions.
# Passive/receive-only — no frames transmitted.
#
# Usage:
#   ./recon-wifi-drone.sh                          # auto-detect interface
#   ./recon-wifi-drone.sh -i wlan1                 # specify interface
#   ./recon-wifi-drone.sh -i wlan1 -t 15           # scan for 15 seconds
#   ./recon-wifi-drone.sh -i wlan1 --channel 6     # lock to channel 6
#   ./recon-wifi-drone.sh --no-monitor             # interface already in monitor mode
#
# Requires: aircrack-ng suite (airodump-ng, airmon-ng), iw
# Tested on: Ubuntu 22.04+ with ALFA AWUS036ACH
#
# For CTF research and authorized security testing only.

set -euo pipefail

# --- Defaults ---
IFACE=""
SCAN_TIME=10
CHANNEL=""
SKIP_MONITOR=false
TMPDIR=""

# Known drone SSID patterns (case-insensitive grep -iE)
# Each entry: "regex|manufacturer|notes"
DRONE_PATTERNS=(
    "^TELLO-|Ryze/DJI Tello|Open AP, UDP SDK on 8889"
    "^RMTT-|Robomaster TT (Tello variant)|Open AP, SDK on 8889"
    "^Bebop2-|Parrot Bebop 2|WPA2-PSK, Telnet root shell"
    "^BebopDrone-|Parrot Bebop 1|Open AP, Telnet root"
    "^Mambo-|Parrot Mambo|BLE primary, WiFi secondary"
    "^Disco-|Parrot Disco|WPA2-PSK"
    "^Anafi-|Parrot Anafi|WPA2-PSK, USB-C Ethernet"
    "^SkyController|Parrot SkyController|WPA2-PSK, acts as AP for Anafi"
    "^ardrone2_|Parrot AR.Drone 2.0|Open AP, Telnet root, FTP"
    "^DJI-|DJI (various)|Check BLE for CVE-2026-78306"
    "^Spark-|DJI Spark|WiFi direct control"
    "^Mavic-|DJI Mavic (WiFi mode)|WiFi direct or RC relay"
    "^PHANTOM|DJI Phantom (WiFi mode)|Older models expose WiFi"
    "^WiFi-UAV|Generic WiFi drone|Likely open AP, probe further"
    "^FPV-|Generic FPV WiFi|May be video feed only"
    "^UAV-|Generic UAV|Probe for control protocol"
    "^Hubsan|Hubsan WiFi drone|App-controlled, HTTP API"
    "^Holy ?Stone|Holy Stone drone|App-controlled WiFi"
    "^SNAPTAIN|Snaptain drone|App-controlled WiFi"
    "^SYMA|Syma WiFi drone|App-controlled, may also use 2.4G RC"
    "^Potensic|Potensic drone|App-controlled WiFi"
    "^Eachine|Eachine drone|Budget FPV, may be WiFi or analog"
)

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") [OPTIONS]

Passive WiFi drone AP scanner. Identifies drone access points by SSID pattern.

Options:
  -i, --interface IFACE   Wireless interface (default: auto-detect)
  -t, --time SECONDS      Scan duration (default: $SCAN_TIME)
  -c, --channel CHAN       Lock to specific channel (default: hop all)
  --no-monitor             Skip monitor mode setup (interface already in monitor)
  -h, --help               Show this help

Output: table of detected drone APs with manufacturer, encryption, channel.
Requires root for monitor mode.
EOF
    exit 1
}

cleanup() {
    if [[ -n "$TMPDIR" && -d "$TMPDIR" ]]; then
        rm -rf "$TMPDIR"
    fi
    # Restore managed mode unless we were told to skip
    if [[ "$SKIP_MONITOR" == false && -n "$IFACE" ]]; then
        echo "[*] Restoring $IFACE to managed mode..." >&2
        airmon-ng stop "${IFACE}mon" &>/dev/null || true
    fi
}
trap cleanup EXIT

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--interface) IFACE="$2"; shift 2 ;;
        -t|--time)      SCAN_TIME="$2"; shift 2 ;;
        -c|--channel)   CHANNEL="$2"; shift 2 ;;
        --no-monitor)   SKIP_MONITOR=true; shift ;;
        -h|--help)      usage ;;
        *)              echo "Unknown option: $1" >&2; usage ;;
    esac
done

# --- Check prerequisites ---
for cmd in airodump-ng iw; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "[!] Required tool not found: $cmd" >&2
        echo "    Install with: sudo apt install aircrack-ng wireless-tools" >&2
        exit 1
    fi
done

if [[ $EUID -ne 0 ]]; then
    echo "[!] This script requires root for monitor mode. Use sudo." >&2
    exit 1
fi

# --- Auto-detect interface ---
if [[ -z "$IFACE" ]]; then
    IFACE=$(iw dev | awk '/Interface/{print $2}' | head -1)
    if [[ -z "$IFACE" ]]; then
        echo "[!] No wireless interface found. Specify with -i." >&2
        exit 1
    fi
    echo "[*] Auto-detected interface: $IFACE" >&2
fi

# --- Monitor mode ---
MON_IFACE="$IFACE"
if [[ "$SKIP_MONITOR" == false ]]; then
    echo "[*] Enabling monitor mode on $IFACE..." >&2
    airmon-ng check kill &>/dev/null || true
    airmon-ng start "$IFACE" &>/dev/null
    # airmon-ng may rename to wlan0mon or similar
    if iw dev "${IFACE}mon" info &>/dev/null 2>&1; then
        MON_IFACE="${IFACE}mon"
    fi
    echo "[*] Monitor interface: $MON_IFACE" >&2
fi

# --- Scan ---
TMPDIR=$(mktemp -d /tmp/recon-wifi-drone.XXXXXX)
OUTPREFIX="$TMPDIR/scan"

echo "[*] Scanning for $SCAN_TIME seconds..." >&2

AIRODUMP_ARGS=(-w "$OUTPREFIX" --output-format csv --write-interval 1)
if [[ -n "$CHANNEL" ]]; then
    AIRODUMP_ARGS+=(--channel "$CHANNEL")
fi

# Run airodump-ng in background, kill after timeout
airodump-ng "${AIRODUMP_ARGS[@]}" "$MON_IFACE" &>/dev/null &
AIRO_PID=$!
sleep "$SCAN_TIME"
kill "$AIRO_PID" 2>/dev/null || true
wait "$AIRO_PID" 2>/dev/null || true

# --- Parse results ---
CSV_FILE="$OUTPREFIX-01.csv"
if [[ ! -f "$CSV_FILE" ]]; then
    echo "[!] No scan output produced. Check interface and permissions." >&2
    exit 1
fi

echo "" >&2
echo "===================================================================="
echo "  WiFi Drone AP Scan Results"
echo "  Interface: $MON_IFACE | Duration: ${SCAN_TIME}s | $(date -Iseconds)"
echo "===================================================================="
echo ""

FOUND=0

# Parse airodump CSV: BSSID, First time seen, Last time seen, channel, Speed,
#                     Privacy, Cipher, Auth, Power, beacons, IV, LAN IP, ID-length, ESSID, Key
# Section 1 is APs (before the blank line), section 2 is clients
while IFS=',' read -r bssid _first _last channel speed privacy cipher auth power _beacons _iv _lanip _idlen essid _key; do
    # Skip header and empty lines
    [[ "$bssid" =~ ^[[:space:]]*BSSID ]] && continue
    [[ "$bssid" =~ ^[[:space:]]*Station ]] && break  # hit client section
    [[ -z "$essid" ]] && continue

    # Trim whitespace
    essid=$(echo "$essid" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    bssid=$(echo "$bssid" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    channel=$(echo "$channel" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    privacy=$(echo "$privacy" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    power=$(echo "$power" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    [[ -z "$essid" ]] && continue

    # Check against drone patterns
    for entry in "${DRONE_PATTERNS[@]}"; do
        IFS='|' read -r pattern mfg notes <<< "$entry"
        if echo "$essid" | grep -iqE "$pattern"; then
            FOUND=$((FOUND + 1))
            printf "  [DRONE #%d]\n" "$FOUND"
            printf "    SSID:         %s\n" "$essid"
            printf "    BSSID:        %s\n" "$bssid"
            printf "    Channel:      %s\n" "$channel"
            printf "    Encryption:   %s\n" "$privacy"
            printf "    Signal (dBm): %s\n" "$power"
            printf "    Manufacturer: %s\n" "$mfg"
            printf "    Notes:        %s\n" "$notes"
            echo ""
            break
        fi
    done
done < "$CSV_FILE"

if [[ "$FOUND" -eq 0 ]]; then
    echo "  No drone APs detected in ${SCAN_TIME}s scan."
    echo "  Try increasing scan time (-t 30) or checking antenna connection."
else
    echo "--------------------------------------------------------------------"
    printf "  Total drone APs found: %d\n" "$FOUND"
fi

echo ""
echo "  All captured APs saved to: $CSV_FILE"
echo "===================================================================="
