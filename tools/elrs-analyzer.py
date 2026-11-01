#!/usr/bin/env python3
"""
ELRS OTA Packet Analyzer

Analysis of *pre-decoded* ExpressLRS packet bytes. This tool does not demodulate
raw IQ. Its current packet parser uses the vendored v4-oriented layout; do not
use its SYNC/UID output as a validated decode of the Betaflight 4.4.2 v3 pair.
For receive-only raw-IQ symbol extraction, see elrs-iq-symbols.py.

Input sources:
  - Pre-decoded packet log (hex lines or JSON)
  - Simulated mode for testing without SDR hardware

Output is line-oriented for piping into the dashboard. Each line is:
  ELRS|<timestamp>|<type>|<json-fields>

Usage:
    elrs-analyzer.py --simulate                    # demo mode with fake traffic
    elrs-analyzer.py --input packets.log           # analyze packet log
    elrs-analyzer.py --input packets.log --rainbow ../tools/elrs-rainbow-table.json
    elrs-analyzer.py --json                        # JSON lines output

For CTF research and authorized security testing only.
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from elrs_core.ota import build_rcdata_std, build_sync, decode_full, decode_std
from elrs_core.v3_compat import uid_crc_init, DEFAULT_OTA_VERSION

# ELRS packet types
ELRS_PKT_RC_DATA_4CH = 0x00
ELRS_PKT_MSP = 0x01
ELRS_PKT_SYNC = 0x02
ELRS_PKT_TLM_LINK = 0x03
ELRS_PKT_TLM_BATTERY = 0x04
ELRS_PKT_TLM_GPS = 0x05

ELRS_PKT_NAMES = {
    0x00: "RC_DATA",
    0x01: "MSP",
    0x02: "SYNC",
    0x03: "TLM_LINK",
    0x04: "TLM_BATTERY",
    0x05: "TLM_GPS",
}

# ELRS rate indices
ELRS_RATES = {
    0: "500Hz", 1: "250Hz", 2: "200Hz", 3: "150Hz",
    4: "100Hz", 5: "50Hz", 6: "25Hz", 7: "4Hz",
}

# SX1280 FHSS parameters for 2.4 GHz
FHSS_FREQ_COUNT = 80
FHSS_SEQ_LEN = 256
FHSS_BASE_FREQ = 2400000000  # 2.4 GHz
FHSS_CHANNEL_SPACING = 1000000  # 1 MHz


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def emit(msg_type, fields, json_mode=False):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if json_mode:
        out = {"source": "elrs", "timestamp": ts, "type": msg_type}
        out.update(fields)
        print(json.dumps(out), flush=True)
    else:
        flat = json.dumps(fields)
        print(f"ELRS|{ts}|{msg_type}|{flat}", flush=True)


def phrase_to_uid(phrase):
    """Binding phrase to 6-byte UID (same algorithm as elrs-uid-rainbow.py)."""
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def load_rainbow_table(path):
    """Load rainbow table from JSON file."""
    if not path or not os.path.exists(path):
        return {}

    with open(path) as f:
        data = json.load(f)

    table = {}
    for entry in data:
        uid_key = entry.get("uid_hex") or entry.get("uid", "")
        if not uid_key:
            continue
        uid_bytes = bytes.fromhex(uid_key.replace(":", ""))
        key = uid_bytes[3:6].hex()
        table[key] = entry
    return table


def lookup_uid_fragment(uid3_5_hex, rainbow):
    """Look up a 3-byte UID fragment in the rainbow table."""
    key = uid3_5_hex.lower()
    return rainbow.get(key)


class ElrsPacket:
    """Represents a decoded ELRS OTA packet."""

    def __init__(self, raw_bytes, freq_mhz=0, rssi=0, snr=0):
        self.raw = raw_bytes
        self.freq_mhz = freq_mhz
        self.rssi = rssi
        self.snr = snr
        self.pkt_type = None
        self.fields = {}
        self._decode()

    def _decode(self):
        if len(self.raw) not in (8, 13):
            return
        decoded = decode_std(self.raw) if len(self.raw) == 8 else decode_full(self.raw)
        self.pkt_type = decoded.type

        if self.pkt_type == ELRS_PKT_SYNC:
            self._decode_sync(decoded)
        elif self.pkt_type == ELRS_PKT_RC_DATA_4CH:
            self._decode_rc()
        elif self.pkt_type == ELRS_PKT_TLM_LINK:
            self._decode_tlm_link()

    def _decode_sync(self, decoded):
        if decoded.sync is None:
            return
        # OTA_Sync_s carries UID[4:5]. A third UID byte must not be invented.
        self.fields = {
            "uid4": decoded.sync.uid4,
            "uid5": decoded.sync.uid5,
            "uid_fragment": f"{decoded.sync.uid4:02x}:{decoded.sync.uid5:02x}",
            "nonce_rx": decoded.sync.nonce,
            "fhss_index": decoded.sync.fhss_index,
            "rate_enum": decoded.sync.rf_rate_enum,
            "switch_mode": decoded.sync.switch_enc_mode,
        }

    def _decode_rc(self):
        if len(self.raw) < 5:
            return
        self.fields = {
            "channels_encoded": self.raw[1:5].hex(),
        }

    def _decode_tlm_link(self):
        if len(self.raw) < 4:
            return
        self.fields = {
            "uplink_rssi_1": -(self.raw[1]) if self.raw[1] else 0,
            "uplink_snr": self.raw[2] / 4.0 if len(self.raw) > 2 else 0,
            "active_antenna": self.raw[3] & 0x01 if len(self.raw) > 3 else 0,
        }


class ElrsTracker:
    """Tracks ELRS transmitters by their UID fragment."""

    def __init__(self, rainbow_table=None):
        self.rainbow = rainbow_table or {}
        self.transmitters = {}
        self.sync_count = 0
        self.pkt_count = 0

    def process_packet(self, pkt, json_mode=False):
        self.pkt_count += 1
        pkt_name = ELRS_PKT_NAMES.get(pkt.pkt_type, f"UNKNOWN({pkt.pkt_type})")

        if pkt.pkt_type == ELRS_PKT_SYNC:
            self.sync_count += 1
            uid_frag = pkt.fields.get("uid_fragment", "")
            uid_hex = uid_frag.replace(":", "")

            # Track this transmitter
            is_new = uid_hex not in self.transmitters
            tx_info = self.transmitters.get(uid_hex, {
                "first_seen": time.time(),
                "sync_count": 0,
                "last_freq_mhz": 0,
                "freqs_seen": set(),
            })
            tx_info["last_seen"] = time.time()
            tx_info["sync_count"] += 1
            tx_info["last_freq_mhz"] = pkt.freq_mhz
            tx_info["last_rssi"] = pkt.rssi
            if pkt.freq_mhz:
                tx_info["freqs_seen"].add(pkt.freq_mhz)

            # Rainbow table lookup
            match = lookup_uid_fragment(uid_hex, self.rainbow)
            if match:
                tx_info["binding_phrase"] = match.get("phrase", "")
                tx_info["full_uid"] = match.get("uid_hex") or match.get("uid", "")

            self.transmitters[uid_hex] = tx_info

            fields = {
                "uid_fragment": uid_frag,
                "freq_mhz": pkt.freq_mhz,
                "rssi": pkt.rssi,
                "snr": pkt.snr,
                "new_tx": is_new,
                "sync_count": tx_info["sync_count"],
                "fhss_index": pkt.fields.get("fhss_index", 0),
            }
            if match:
                fields["rainbow_match"] = True
                fields["binding_phrase"] = match.get("phrase", "")
                fields["full_uid"] = match.get("uid_hex") or match.get("uid", "")

            emit("SYNC", fields, json_mode)

        elif pkt.pkt_type == ELRS_PKT_RC_DATA_4CH:
            emit("RC_DATA", {
                "freq_mhz": pkt.freq_mhz,
                "rssi": pkt.rssi,
                **pkt.fields,
            }, json_mode)

        elif pkt.pkt_type == ELRS_PKT_TLM_LINK:
            emit("TLM_LINK", {
                "freq_mhz": pkt.freq_mhz,
                "rssi": pkt.rssi,
                **pkt.fields,
            }, json_mode)

        else:
            emit(pkt_name, {
                "freq_mhz": pkt.freq_mhz,
                "rssi": pkt.rssi,
                "raw": pkt.raw.hex(),
            }, json_mode)

    def get_summary(self):
        summary = {
            "total_packets": self.pkt_count,
            "sync_packets": self.sync_count,
            "unique_transmitters": len(self.transmitters),
            "transmitters": {},
        }
        for uid_hex, info in self.transmitters.items():
            tx_sum = {
                "uid_fragment": ":".join(
                    uid_hex[i:i + 2] for i in range(0, len(uid_hex), 2)
                ),
                "sync_count": info["sync_count"],
                "first_seen": info["first_seen"],
                "last_seen": info["last_seen"],
                "freqs_observed": len(info.get("freqs_seen", set())),
            }
            if "binding_phrase" in info:
                tx_sum["binding_phrase"] = info["binding_phrase"]
                tx_sum["full_uid"] = info["full_uid"]
            summary["transmitters"][uid_hex] = tx_sum
        return summary


def simulate_traffic(tracker, json_mode, duration=0, ota_version=DEFAULT_OTA_VERSION):
    """Generate simulated ELRS traffic for testing."""
    log(f"SIMULATION MODE — generating fake ELRS traffic (OTA v{ota_version})")

    sim_txs = [
        {"phrase": "iloveflying", "rate_hz": 2.0},
        {"phrase": "default", "rate_hz": 1.5},
        {"phrase": "raceday", "rate_hz": 3.0},
    ]

    for tx in sim_txs:
        tx["uid"] = phrase_to_uid(tx["phrase"])

    start = time.time()
    try:
        while True:
            if duration and (time.time() - start) > duration:
                break

            for tx in sim_txs:
                uid = tx["uid"]
                freq = 2400 + random.randint(0, 79)
                rssi = random.randint(-90, -40)
                snr = random.uniform(5.0, 15.0)

                crc_init = uid_crc_init(uid, ota_version)
                sync_raw = build_sync(
                    False,
                    fhss_index=random.randint(0, 239),
                    nonce=random.randint(0, 255),
                    rf_rate_enum=4,
                    uid4=uid[4],
                    uid5=uid[5],
                    crc_init=crc_init,
                )

                pkt = ElrsPacket(bytes(sync_raw), freq_mhz=freq, rssi=rssi, snr=round(snr, 1))
                tracker.process_packet(pkt, json_mode)

                # Occasionally send RC data
                if random.random() < 0.3:
                    rc_raw = build_rcdata_std(
                        [random.randint(172, 1811) for _ in range(4)],
                        switch_index=0,
                        switch_value=0,
                        is_armed=False,
                        crc_init=crc_init,
                        ota_nonce=0,
                    )
                    pkt = ElrsPacket(bytes(rc_raw), freq_mhz=freq, rssi=rssi)
                    tracker.process_packet(pkt, json_mode)

                time.sleep(1.0 / tx["rate_hz"])

    except KeyboardInterrupt:
        pass

    return tracker.get_summary()


def analyze_packet_log(path, tracker, json_mode):
    """Analyze a packet log file (hex lines or JSON lines)."""
    log(f"Analyzing packet log: {path}")
    count = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            try:
                # Try JSON format first
                data = json.loads(line)
                raw = bytes.fromhex(data.get("raw", data.get("payload", "")))
                freq = data.get("freq_mhz", data.get("freq", 0))
                rssi = data.get("rssi", 0)
                snr = data.get("snr", 0)
            except (json.JSONDecodeError, ValueError):
                # Fall back to hex string
                try:
                    parts = line.split()
                    raw = bytes.fromhex(parts[0])
                    freq = float(parts[1]) if len(parts) > 1 else 0
                    rssi = int(parts[2]) if len(parts) > 2 else 0
                    snr = float(parts[3]) if len(parts) > 3 else 0
                except (ValueError, IndexError):
                    continue

            pkt = ElrsPacket(raw, freq_mhz=freq, rssi=rssi, snr=snr)
            tracker.process_packet(pkt, json_mode)
            count += 1

    log(f"Processed {count} packets from log")
    return tracker.get_summary()


def main():
    parser = argparse.ArgumentParser(
        description="ELRS OTA packet analyzer for drone CTF research"
    )
    parser.add_argument('--input', '-i', metavar='FILE',
                        help='Packet log file (hex or JSON lines)')
    parser.add_argument('--rainbow', '-r', metavar='FILE',
                        help='Rainbow table JSON (default: auto-detect in tools/)')
    parser.add_argument('--simulate', '-s', action='store_true',
                        help='Generate simulated traffic for testing')
    parser.add_argument('--sim-duration', type=int, default=0,
                        help='Simulation duration in seconds (0 = infinite)')
    parser.add_argument('--json', action='store_true',
                        help='Output JSON lines')
    parser.add_argument('--ota-version', type=int, default=DEFAULT_OTA_VERSION,
                        choices=[3, 4],
                        help=f'ELRS OTA version for CRC computation '
                             f'(default: {DEFAULT_OTA_VERSION}; '
                             f'3 = Betaflight 4.4.2 / ELRS 3.x, '
                             f'4 = ELRS 4.x)')
    parser.add_argument('--summary', action='store_true',
                        help='Print summary at end')
    args = parser.parse_args()

    # Auto-detect rainbow table
    rainbow_path = args.rainbow
    if not rainbow_path:
        candidates = [
            Path(__file__).parent / "elrs-rainbow-table.json",
            Path("tools/elrs-rainbow-table.json"),
            Path("elrs-rainbow-table.json"),
        ]
        for c in candidates:
            if c.exists():
                rainbow_path = str(c)
                break

    rainbow = load_rainbow_table(rainbow_path)
    if rainbow:
        log(f"Loaded rainbow table: {len(rainbow)} entries from {rainbow_path}")
    else:
        log("No rainbow table loaded (use --rainbow to specify)")

    tracker = ElrsTracker(rainbow)

    if args.simulate:
        summary = simulate_traffic(tracker, args.json, args.sim_duration,
                                   ota_version=args.ota_version)
    elif args.input:
        summary = analyze_packet_log(args.input, tracker, args.json)
    else:
        log("No input specified. Use --simulate for demo mode or --input for a packet log.")
        log("Live SDR capture requires GNU Radio or SoapySDR integration (not yet implemented).")
        parser.print_help()
        sys.exit(1)

    if args.summary:
        print(json.dumps(summary, indent=2, default=str), file=sys.stderr)


if __name__ == '__main__':
    main()
