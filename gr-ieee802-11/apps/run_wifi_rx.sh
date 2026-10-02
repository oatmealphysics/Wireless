#!/usr/bin/env bash
# run_wifi_rx.sh — run the gr-ieee802-11 RX flowgraph on the B205 and decode
# the frames it captures into a pcap. Usage:
#   ./run_wifi_rx.sh [freq_mhz] [samp_rate_mhz] [gain_db] [duration_sec]
# Defaults: 2437 (ch 6) 20 40 20
#
# The flowgraph writes decoded 802.11 frames to /tmp/wifi.pcap (file sink,
# byte type). After the run we dump the BSSIDs (AP MACs) from that pcap with
# tshark if available, else just point you at the file for Wireshark.
set -e
FREQ_MHZ="${1:-2437}"
SRATE_MHZ="${2:-20}"
GAIN_DB="${3:-40}"
DURATION="${4:-20}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EX="$DIR/examples"
PY="$EX/wifi_rx.py"

echo "[*] freq=${FREQ_MHZ} MHz  srate=${SRATE_MHZ} MHz  gain=${GAIN_DB} dB  for ${DURATION}s"

# 1) Build the GRC -> python once (skip if already built and newer than the grc)
if [ ! -f "$PY" ] || [ "$EX/wifi_rx.grc" -nt "$PY" ]; then
    echo "[*] building wifi_rx.py from wifi_rx.grc ..."
    ( cd "$EX" && grcc wifi_rx.grc )
fi

# 2) The flowgraph is a GUI (Qt) chooser for freq/srate/gain. Set them via env
#    is not possible, so we drive the defaults: the GRC's variable_qtgui_chooser
#    defaults are freq=2437e6, srate=20e6, gain=40 — matching our defaults.
#    If you need a non-default channel, open wifi_rx.grc in GRC and pick it,
#    or edit the 'value:' of the freq variable below. For now, run headless.
echo "[*] starting RX flowgraph (Ctrl-C to stop early) ..."
# timeout auto-stops after DURATION seconds
timeout -s INT "$DURATION" python3 -u "$PY" || true

# 3) Decode the captured pcap
echo ""
echo "[*] decoding captured frames ..."
PCAP=/tmp/wifi.pcap
if [ -f "$PCAP" ] && [ -s "$PCAP" ]; then
    echo "    captured: $PCAP ($(wc -c < "$PCAP") bytes)"
    if command -v tshark >/dev/null 2>&1; then
        echo ""
        echo "=== decoded 802.11 frames (BSSID = the AP MAC) ==="
        tshark -r "$PCAP" -Y "wlan" -T fields \
          -e wlan.addr -e wlan.sa -e wlan.da \
          -E separator="  |  " 2>/dev/null | sort | uniq -c | sort -rn | head -40 || \
          tshark -r "$PCAP" 2>/dev/null | head -40
    else
        echo "    tshark not installed — open $PCAP in Wireshark, filter: wlan"
    fi
else
    echo "    (no frames captured to $PCAP — the AP may be too weak on the B205"
    echo "     RX, or the channel/srate was off. Try a stronger AP or 10 MHz.)"
fi
