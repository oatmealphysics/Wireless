#!/usr/bin/env python3
"""
Unified Drone Recon Dashboard

Runs the WiFi scanner (recon-wifi-drone.sh), MAVLink sniffer (mavlink-recon.py),
and ELRS analyzer (elrs-analyzer.py) in parallel and presents a combined
terminal view of everything in the air.

Features:
  - Launches each scanner as a subprocess in JSON-line mode
  - Aggregates output into a unified TUI (curses-based)
  - Shows discovered targets with protocol, signal info, and vuln indicators
  - Color-coded by threat level and protocol
  - JSON export for post-game analysis
  - Graceful degradation: if a scanner fails to start, the others keep running

Usage:
    recon-dashboard.py                              # run all scanners
    recon-dashboard.py --wifi-iface wlan0mon         # specify WiFi interface
    recon-dashboard.py --mavlink-port 14550          # MAVLink UDP port
    recon-dashboard.py --elrs-simulate               # ELRS in simulation mode
    recon-dashboard.py --export results.json         # save results on exit
    recon-dashboard.py --simulate                    # demo mode, no hardware needed
    recon-dashboard.py --no-wifi --no-mavlink        # only ELRS
    recon-dashboard.py --headless                    # no TUI, just aggregate JSON lines

For CTF research and authorized security testing only.
"""

import argparse
import curses
import json
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).parent

# How old a target can be before it's considered stale (seconds)
STALE_TIMEOUT = 60
# How old before it's removed from display
REMOVE_TIMEOUT = 300


class Target:
    """A discovered wireless target (drone, GCS, or TX)."""

    def __init__(self, protocol, target_id, target_type="unknown"):
        self.protocol = protocol        # wifi, mavlink, elrs
        self.target_id = target_id      # unique identifier
        self.target_type = target_type   # drone, gcs, tx, ap, client
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.properties = {}
        self.vulns = []
        self.signal = None
        self.events = []

    def update(self, properties):
        self.last_seen = time.time()
        self.properties.update(properties)
        if "rssi" in properties:
            self.signal = properties["rssi"]
        elif "power" in properties:
            self.signal = properties["power"]
        self._check_vulns()

    def _check_vulns(self):
        self.vulns = []
        if self.protocol == "wifi":
            essid = self.properties.get("essid", "")
            if any(p in essid.upper() for p in ["TELLO", "ARDRONE"]):
                self.vulns.append("OPEN_WIFI")
                self.vulns.append("KNOWN_DEFAULT_CREDS")
            if "DJI" in essid.upper():
                self.vulns.append("FPV_INTERCEPT")
        elif self.protocol == "mavlink":
            if not self.properties.get("signed"):
                self.vulns.append("NO_MSG_SIGNING")
            if self.properties.get("armed"):
                self.vulns.append("ARMED")
            state = self.properties.get("state", "")
            if state in ("ACTIVE", "CRITICAL"):
                self.vulns.append("IN_FLIGHT")
        elif self.protocol == "elrs":
            if self.properties.get("rainbow_match"):
                self.vulns.append("BINDING_PHRASE_KNOWN")
                self.vulns.append("FHSS_PREDICTABLE")
                self.vulns.append("TAKEOVER_POSSIBLE")

    @property
    def age(self):
        return time.time() - self.last_seen

    @property
    def is_stale(self):
        return self.age > STALE_TIMEOUT

    @property
    def threat_level(self):
        if "TAKEOVER_POSSIBLE" in self.vulns:
            return "CRITICAL"
        if "BINDING_PHRASE_KNOWN" in self.vulns:
            return "HIGH"
        if "NO_MSG_SIGNING" in self.vulns or "OPEN_WIFI" in self.vulns:
            return "MEDIUM"
        if self.vulns:
            return "LOW"
        return "INFO"

    def to_dict(self):
        return {
            "protocol": self.protocol,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "age_s": round(self.age, 1),
            "signal": self.signal,
            "properties": self.properties,
            "vulns": self.vulns,
            "threat_level": self.threat_level,
        }


class ReconEngine:
    """Manages scanner subprocesses and aggregates targets."""

    def __init__(self, args):
        self.args = args
        self.targets = OrderedDict()
        self.lock = threading.Lock()
        self.processes = {}
        self.scanner_status = {}
        self.event_log = []
        self.running = True
        self.stats = {"wifi": 0, "mavlink": 0, "elrs": 0}

    def start_scanners(self):
        if not self.args.no_wifi:
            self._start_wifi()
        if not self.args.no_mavlink:
            self._start_mavlink()
        if not self.args.no_elrs:
            self._start_elrs()

    def _start_wifi(self):
        script = TOOLS_DIR / "recon-wifi-drone.sh"
        if not script.exists():
            self.scanner_status["wifi"] = "MISSING"
            return

        cmd = ["bash", str(script), "--json"]
        if self.args.wifi_iface:
            cmd.extend(["-i", self.args.wifi_iface])
        if self.args.wifi_setup:
            cmd.extend(["--setup", self.args.wifi_setup])

        if self.args.simulate:
            self.scanner_status["wifi"] = "SIMULATED"
            t = threading.Thread(target=self._simulate_wifi, daemon=True)
            t.start()
            return

        self._launch("wifi", cmd)

    def _start_mavlink(self):
        script = TOOLS_DIR / "mavlink-recon.py"
        if not script.exists():
            self.scanner_status["mavlink"] = "MISSING"
            return

        cmd = [sys.executable, "-I", str(script), "--json"]
        if self.args.mavlink_port:
            cmd.extend(["--udp", f"0.0.0.0:{self.args.mavlink_port}"])
        if self.args.mavlink_serial:
            cmd.extend(["--serial", self.args.mavlink_serial])

        if self.args.simulate:
            self.scanner_status["mavlink"] = "SIMULATED"
            t = threading.Thread(target=self._simulate_mavlink, daemon=True)
            t.start()
            return

        self._launch("mavlink", cmd)

    def _start_elrs(self):
        script = TOOLS_DIR / "elrs-analyzer.py"
        if not script.exists():
            self.scanner_status["elrs"] = "MISSING"
            return

        cmd = [sys.executable, "-I", str(script), "--json"]
        if self.args.elrs_simulate or self.args.simulate:
            cmd.append("--simulate")
        elif self.args.elrs_input:
            cmd.extend(["--input", self.args.elrs_input])
        else:
            # No input specified, and not in simulate mode
            self.scanner_status["elrs"] = "NO_INPUT"
            return

        if self.args.elrs_rainbow:
            cmd.extend(["--rainbow", self.args.elrs_rainbow])

        self._launch("elrs", cmd)

    def _launch(self, name, cmd):
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self.processes[name] = proc
            self.scanner_status[name] = "RUNNING"
            t = threading.Thread(target=self._read_output, args=(name, proc), daemon=True)
            t.start()
            t2 = threading.Thread(target=self._read_stderr, args=(name, proc), daemon=True)
            t2.start()
        except Exception as e:
            self.scanner_status[name] = f"ERROR: {e}"

    def _read_output(self, name, proc):
        try:
            for line in proc.stdout:
                if not self.running:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    self._process_event(name, data)
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        finally:
            if self.running:
                self.scanner_status[name] = "STOPPED"

    def _read_stderr(self, name, proc):
        try:
            for _line in proc.stderr:
                if not self.running:
                    break
        except Exception:
            pass

    def _process_event(self, source, data):
        with self.lock:
            self.stats[source] = self.stats.get(source, 0) + 1
            self.event_log.append({"time": time.time(), "source": source, "data": data})
            if len(self.event_log) > 500:
                self.event_log = self.event_log[-500:]

            msg_type = data.get("type", "")

            if source == "wifi":
                self._handle_wifi(data, msg_type)
            elif source == "mavlink":
                self._handle_mavlink(data, msg_type)
            elif source == "elrs":
                self._handle_elrs(data, msg_type)

            if self.args.headless:
                print(json.dumps(data), flush=True)

    def _handle_wifi(self, data, msg_type):
        if msg_type == "AP":
            bssid = data.get("bssid", "")
            tid = f"wifi:{bssid}"
            if tid not in self.targets:
                self.targets[tid] = Target("wifi", tid, "ap")
            self.targets[tid].update({
                "bssid": bssid,
                "essid": data.get("essid", ""),
                "channel": data.get("channel"),
                "power": data.get("power"),
                "match": data.get("match", ""),
            })
        elif msg_type in ("CLIENT", "PROBE"):
            mac = data.get("mac", "")
            tid = f"wifi:{mac}"
            if tid not in self.targets:
                self.targets[tid] = Target("wifi", tid, "client")
            self.targets[tid].update(data)

    def _handle_mavlink(self, data, msg_type):
        sysid = data.get("sysid", 0)
        compid = data.get("compid", 0)
        tid = f"mav:{sysid}:{compid}"

        if msg_type == "HEARTBEAT":
            if tid not in self.targets:
                mav_type = data.get("mav_type", "")
                ttype = "drone" if mav_type not in ("GCS", "ANTENNA_TRACKER") else "gcs"
                self.targets[tid] = Target("mavlink", tid, ttype)
            self.targets[tid].update({
                "sysid": sysid,
                "compid": compid,
                "mav_type": data.get("mav_type", ""),
                "autopilot": data.get("autopilot", ""),
                "armed": data.get("armed", False),
                "state": data.get("state", ""),
                "mode_flags": data.get("mode_flags", []),
                "version": data.get("version"),
                "source": data.get("source", ""),
                "signed": data.get("signed", False),
            })
        elif msg_type in ("GPS", "POSITION"):
            if tid not in self.targets:
                self.targets[tid] = Target("mavlink", tid, "drone")
            self.targets[tid].update({
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "alt_m": data.get("alt_m"),
            })
        elif msg_type == "STATUSTEXT":
            if tid in self.targets:
                self.targets[tid].properties["last_status"] = data.get("text", "")

    def _handle_elrs(self, data, msg_type):
        if msg_type == "SYNC":
            uid_frag = data.get("uid_fragment", "")
            tid = f"elrs:{uid_frag}"
            if tid not in self.targets:
                self.targets[tid] = Target("elrs", tid, "tx")
            self.targets[tid].update({
                "uid_fragment": uid_frag,
                "freq_mhz": data.get("freq_mhz"),
                "rssi": data.get("rssi"),
                "snr": data.get("snr"),
                "sync_count": data.get("sync_count", 0),
                "fhss_seed": data.get("fhss_seed"),
                "rainbow_match": data.get("rainbow_match", False),
                "binding_phrase": data.get("binding_phrase", ""),
                "full_uid": data.get("full_uid", ""),
            })

    def _simulate_wifi(self):
        """Generate simulated WiFi drone discoveries."""
        sim_aps = [
            {"bssid": "60:60:1F:AA:BB:CC", "essid": "TELLO-AABBCC", "channel": 6, "power": -45},
            {"bssid": "A0:14:3D:11:22:33", "essid": "Parrot-Disco", "channel": 11, "power": -62},
            {"bssid": "48:1C:B9:DE:AD:01", "essid": "DJI-Mavic3-01", "channel": 36, "power": -71},
        ]
        time.sleep(2)
        import random
        while self.running:
            for ap in sim_aps:
                if not self.running:
                    break
                data = {
                    "source": "wifi", "type": "AP",
                    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    "bssid": ap["bssid"], "essid": ap["essid"],
                    "channel": ap["channel"],
                    "power": ap["power"] + random.randint(-5, 5),
                    "match": ap["essid"].split("-")[0].upper(),
                    "drone": True,
                }
                self._process_event("wifi", data)
                time.sleep(1.5 + random.random())
            time.sleep(3)

    def _simulate_mavlink(self):
        """Generate simulated MAVLink heartbeats."""
        import random
        time.sleep(3)
        sim_systems = [
            {"sysid": 1, "compid": 1, "mav_type": "QUADROTOR", "autopilot": "ARDUPILOTMEGA"},
            {"sysid": 255, "compid": 0, "mav_type": "GCS", "autopilot": "INVALID"},
        ]
        while self.running:
            for sys_info in sim_systems:
                if not self.running:
                    break
                data = {
                    "source": "mavlink", "type": "HEARTBEAT",
                    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    "sysid": sys_info["sysid"], "compid": sys_info["compid"],
                    "mav_type": sys_info["mav_type"],
                    "autopilot": sys_info["autopilot"],
                    "armed": random.choice([True, False]),
                    "state": random.choice(["STANDBY", "ACTIVE"]),
                    "mode_flags": ["STABILIZE"],
                    "version": 2,
                    "endpoint": "192.168.1.100:14550",
                    "signed": False,
                    "new_system": False,
                }
                self._process_event("mavlink", data)
                time.sleep(1)
            # Occasionally send GPS
            data = {
                "source": "mavlink", "type": "POSITION",
                "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                "sysid": 1, "compid": 1,
                "lat": 37.7749 + random.uniform(-0.001, 0.001),
                "lon": -122.4194 + random.uniform(-0.001, 0.001),
                "alt_m": 50 + random.uniform(-5, 5),
                "relative_alt_m": 50 + random.uniform(-5, 5),
            }
            self._process_event("mavlink", data)
            time.sleep(2)

    def stop(self):
        self.running = False
        for _name, proc in self.processes.items():
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def export(self, path):
        with self.lock:
            data = {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "stats": self.stats,
                "scanner_status": self.scanner_status,
                "targets": {tid: t.to_dict() for tid, t in self.targets.items()},
                "event_count": len(self.event_log),
                "events": self.event_log[-100:],
            }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)


class DashboardTUI:
    """Curses-based terminal UI for the recon dashboard."""

    # Color pair IDs
    C_HEADER = 1
    C_CRITICAL = 2
    C_HIGH = 3
    C_MEDIUM = 4
    C_LOW = 5
    C_INFO = 6
    C_STALE = 7
    C_WIFI = 8
    C_MAVLINK = 9
    C_ELRS = 10
    C_STATUS = 11

    def __init__(self, engine):
        self.engine = engine
        self.scroll_offset = 0
        self.selected = 0
        self.show_detail = False

    def run(self, stdscr):
        self._init_colors()
        curses.curs_set(0)
        stdscr.timeout(500)
        stdscr.clear()

        while self.engine.running:
            try:
                self._draw(stdscr)
                key = stdscr.getch()
                if key == ord('q') or key == ord('Q'):
                    break
                elif key == curses.KEY_UP or key == ord('k'):
                    self.selected = max(0, self.selected - 1)
                elif key == curses.KEY_DOWN or key == ord('j'):
                    self.selected += 1
                elif key == ord('\n') or key == ord(' '):
                    self.show_detail = not self.show_detail
                elif key == ord('e'):
                    if self.engine.args.export:
                        self.engine.export(self.engine.args.export)
            except KeyboardInterrupt:
                break
            except curses.error:
                pass

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(self.C_HEADER, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(self.C_CRITICAL, curses.COLOR_WHITE, curses.COLOR_RED)
        curses.init_pair(self.C_HIGH, curses.COLOR_RED, -1)
        curses.init_pair(self.C_MEDIUM, curses.COLOR_YELLOW, -1)
        curses.init_pair(self.C_LOW, curses.COLOR_GREEN, -1)
        curses.init_pair(self.C_INFO, curses.COLOR_WHITE, -1)
        curses.init_pair(self.C_STALE, curses.COLOR_BLACK, -1)
        curses.init_pair(self.C_WIFI, curses.COLOR_CYAN, -1)
        curses.init_pair(self.C_MAVLINK, curses.COLOR_MAGENTA, -1)
        curses.init_pair(self.C_ELRS, curses.COLOR_GREEN, -1)
        curses.init_pair(self.C_STATUS, curses.COLOR_BLACK, curses.COLOR_WHITE)

    def _draw(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # Header
        header = " DRONE RECON DASHBOARD "
        ts = datetime.now().strftime('%H:%M:%S')
        header_line = f"{header:^{w - 12}}{ts:>10}  "
        self._safe_addstr(stdscr, 0, 0, header_line[:w], curses.color_pair(self.C_HEADER) | curses.A_BOLD)

        # Scanner status bar
        status_parts = []
        for name in ("wifi", "mavlink", "elrs"):
            st = self.engine.scanner_status.get(name, "OFF")
            count = self.engine.stats.get(name, 0)
            proto_color = {"wifi": self.C_WIFI, "mavlink": self.C_MAVLINK, "elrs": self.C_ELRS}[name]
            if st == "RUNNING":
                status_parts.append((f" {name.upper()}: {st} ({count}) ", proto_color))
            elif st == "SIMULATED":
                status_parts.append((f" {name.upper()}: SIM ({count}) ", proto_color))
            else:
                status_parts.append((f" {name.upper()}: {st} ", self.C_STALE))

        col = 0
        for text, color in status_parts:
            if col + len(text) < w:
                self._safe_addstr(stdscr, 1, col, text, curses.color_pair(color))
                col += len(text) + 1

        # Table header
        table_y = 3
        col_header = f"{'PROTO':<8} {'TYPE':<8} {'ID':<28} {'SIGNAL':>7} {'THREAT':<10} {'VULNS':<30} {'AGE':>5}"
        self._safe_addstr(stdscr, table_y, 0, col_header[:w], curses.A_BOLD | curses.A_UNDERLINE)

        # Targets
        with self.engine.lock:
            targets = list(self.engine.targets.values())

        # Sort by threat level then recency
        threat_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
        targets.sort(key=lambda t: (threat_order.get(t.threat_level, 5), t.age))

        if self.selected >= len(targets):
            self.selected = max(0, len(targets) - 1)

        visible_rows = h - table_y - 3  # leave room for footer
        if self.show_detail:
            visible_rows = max(1, visible_rows // 2)

        # Adjust scroll
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + visible_rows:
            self.scroll_offset = self.selected - visible_rows + 1

        for i, target in enumerate(targets[self.scroll_offset:self.scroll_offset + visible_rows]):
            row = table_y + 1 + i
            if row >= h - 2:
                break

            actual_idx = i + self.scroll_offset
            is_selected = actual_idx == self.selected

            # Format columns
            proto = target.protocol.upper()
            ttype = target.target_type[:7]
            tid = target.target_id.replace(f"{target.protocol}:", "")[:27]
            sig = f"{target.signal}" if target.signal is not None else "—"
            threat = target.threat_level
            vulns = ", ".join(target.vulns[:3]) if target.vulns else "—"
            age = f"{int(target.age)}s"

            line = f"{proto:<8} {ttype:<8} {tid:<28} {sig:>7} {threat:<10} {vulns:<30} {age:>5}"

            # Pick color
            if target.is_stale:
                color = curses.color_pair(self.C_STALE)
            elif threat == "CRITICAL":
                color = curses.color_pair(self.C_CRITICAL) | curses.A_BOLD
            elif threat == "HIGH":
                color = curses.color_pair(self.C_HIGH) | curses.A_BOLD
            elif threat == "MEDIUM":
                color = curses.color_pair(self.C_MEDIUM)
            elif threat == "LOW":
                color = curses.color_pair(self.C_LOW)
            else:
                color = curses.color_pair(self.C_INFO)

            if is_selected:
                color |= curses.A_REVERSE

            self._safe_addstr(stdscr, row, 0, line[:w], color)

        # Detail panel for selected target
        if self.show_detail and targets:
            detail_y = table_y + 1 + visible_rows + 1
            if detail_y < h - 2 and self.selected < len(targets):
                target = targets[self.selected]
                self._safe_addstr(stdscr, detail_y, 0, f" Details: {target.target_id} ", curses.A_BOLD)
                props = target.properties
                detail_row = detail_y + 1
                for key, val in list(props.items())[:h - detail_row - 2]:
                    if detail_row >= h - 2:
                        break
                    line = f"  {key}: {val}"
                    self._safe_addstr(stdscr, detail_row, 0, line[:w])
                    detail_row += 1

        # Footer
        footer_y = h - 1
        n_targets = len(targets)
        n_critical = sum(1 for t in targets if t.threat_level == "CRITICAL")
        n_high = sum(1 for t in targets if t.threat_level == "HIGH")
        footer = f" Targets: {n_targets} | Critical: {n_critical} | High: {n_high} | [q]uit [e]xport [↑↓]select [enter]detail "
        self._safe_addstr(stdscr, footer_y, 0, footer[:w], curses.color_pair(self.C_STATUS))

        stdscr.noutrefresh()
        curses.doupdate()

    def _safe_addstr(self, stdscr, y, x, text, attr=0):
        h, w = stdscr.getmaxyx()
        if y >= h or x >= w:
            return
        try:
            stdscr.addnstr(y, x, text, w - x, attr)
        except curses.error:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Unified drone recon dashboard for CTF research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s --simulate                  Demo mode, no hardware
              %(prog)s --wifi-iface wlan0mon       Live WiFi scanning
              %(prog)s --elrs-simulate             ELRS simulation + live WiFi/MAVLink
              %(prog)s --headless --export out.json Headless with JSON export
        """),
    )

    # WiFi options
    wifi = parser.add_argument_group("WiFi scanner")
    wifi.add_argument('--wifi-iface', metavar='IFACE', help='Monitor-mode interface')
    wifi.add_argument('--wifi-setup', metavar='IFACE', help='Put IFACE into monitor mode')
    wifi.add_argument('--no-wifi', action='store_true', help='Disable WiFi scanner')

    # MAVLink options
    mav = parser.add_argument_group("MAVLink sniffer")
    mav.add_argument('--mavlink-port', type=int, metavar='PORT', help='UDP port (default: 14550)')
    mav.add_argument('--mavlink-serial', metavar='PORT', help='Serial port')
    mav.add_argument('--no-mavlink', action='store_true', help='Disable MAVLink sniffer')

    # ELRS options
    elrs = parser.add_argument_group("ELRS analyzer")
    elrs.add_argument('--elrs-simulate', action='store_true', help='ELRS simulation mode')
    elrs.add_argument('--elrs-input', metavar='FILE', help='ELRS packet log')
    elrs.add_argument('--elrs-rainbow', metavar='FILE', help='Rainbow table JSON')
    elrs.add_argument('--no-elrs', action='store_true', help='Disable ELRS analyzer')

    # General options
    parser.add_argument('--simulate', action='store_true',
                        help='Demo mode: simulate all scanners (no hardware needed)')
    parser.add_argument('--headless', action='store_true',
                        help='No TUI, just aggregate JSON lines to stdout')
    parser.add_argument('--export', metavar='FILE',
                        help='Export results to JSON on exit')

    args = parser.parse_args()

    engine = ReconEngine(args)

    def handle_signal(sig, frame):
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    engine.start_scanners()

    try:
        if args.headless:
            while engine.running:
                time.sleep(1)
        else:
            tui = DashboardTUI(engine)
            curses.wrapper(tui.run)
    finally:
        engine.stop()
        if args.export:
            engine.export(args.export)
            print(f"Results exported to {args.export}", file=sys.stderr)


if __name__ == '__main__':
    main()
