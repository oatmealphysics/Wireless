#!/usr/bin/env python3
"""drone_scan.py -- overarching supervisor for the drone detection tools.

One command starts everything and gives you a single Web UI on one port:

  * Open Drone ID capture + decode (odr_detect.py)  -> drones panel
  * ExpressLRS 2.4 GHz LoRa decode (elrs_live.py)   -> ELRS links panel
  * the unified Web UI (odr_web.py) on --port        -> http://host:port

The ELRS decoder needs an SDR (UHD) + numpy and, for a bound link, the bind
UID; ODID needs only BlueZ (btmon) for BLE. Either can be turned off. If a
subsystem's dependency is missing it is skipped with a clear note, not a crash.

Usage:
    # ODID only (BLE via btmon) -- the common laptop case
    sudo python3 drone_scan.py

    # ODID + ELRS: starts elrs_live in bind mode (no UID needed) into a pkts
    # file and tails it into the same UI
    sudo python3 drone_scan.py --elrs --elrs-mode bind

    # ELRS on a known-bound link (strongest decode)
    python3 drone_scan.py --elrs --elrs-mode live --uid A1B2C3D4E5F6

    # no radio at all: verify the whole UI with synthetic traffic
    python3 drone_scan.py --odid-source replay

    # ODID from a saved Wi-Fi pcap + ELRS replay of a saved pkts file
    python3 drone_scan.py --odid-source pcap -f wifi.pcap \
        --elrs --elrs-pkts-replay saved_pkts.jsonl

Everything is supervised: Ctrl-C stops all children cleanly, a crashed ELRS
child is logged (and optionally restarted), and the UI stays up either way.
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def have_pkg(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def have_exe(name):
    return shutil.which(name) is not None


def build_elrs_cmd(args, pkts_path):
    """Return the elrs_live.py argv for the requested mode, or None to skip."""
    if not have_pkg("uhd") or not have_pkg("numpy"):
        print("[elrs] SKIP: python 'uhd' and/or 'numpy' not importable "
              "(SDR path needs both). Install UHD>=4.2 + numpy to enable.")
        return None
    cmd = [PY, os.path.join(HERE, "elrs_live.py")]
    if args.device:
        cmd += ["--device", args.device]
    cmd += ["--freq", str(args.freq), "--srate", str(args.srate),
            "--gain", str(args.gain)]
    if args.elrs_mode == "bind":
        cmd += ["--bind"]
    elif args.elrs_mode == "live":
        cmd += ["--live"]
        if args.uid:
            cmd += ["--uid", args.uid]
        elif args.phrase:
            cmd += ["--phrase", args.phrase]
    cmd += ["--pkts", pkts_path]
    return cmd


def build_elrs_replay(args, pkts_path):
    """Synthetic ELRS packet writer (no SDR) so the links panel can be
    exercised. Emits plausible CRC-plausible packet dicts on an interval."""
    import json, threading

    def loop():
        i = 0
        # a fixed init/uid so it aggregates into one link
        init = "0x1234"
        uid = "a1b2c3d4e5f6"
        while True:
            i += 1
            pkt = {"t": time.time(), "sf": 5 + (i % 4), "inv": i % 2,
                   "size": 13, "type": i % 8, "init": init, "ov": 7,
                   "uid": uid, "hex": ("%02x" * 13) % tuple(
                       (i * 13 + k) % 256 for k in range(13))}
            with open(pkts_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(pkt) + "\n")
            time.sleep(args.elrs_replay_interval)

    return threading.Thread(target=loop, daemon=True)


def main():
    ap = argparse.ArgumentParser(
        description="Overarching drone detector: ODID + ELRS in one Web UI.")
    ap.add_argument("--port", type=int, default=10000)
    ap.add_argument("--host", default="0.0.0.0")
    # ODID
    ap.add_argument("--odid-source",
                    choices=["btmon-live", "btmon", "pcap", "stdin", "replay"],
                    default="btmon-live")
    ap.add_argument("-f", "--file", help="input file for ODID (pcap / btmon log)")
    # ELRS
    ap.add_argument("--elrs", action="store_true",
                    help="enable the ELRS decoder (needs uhd+numpy + SDR)")
    ap.add_argument("--elrs-mode", choices=["bind", "live"], default="bind",
                    help="bind (no UID) or live (needs --uid/--phrase)")
    ap.add_argument("--elrs-pkts-replay",
                    help="instead of a real SDR, replay synthetic ELRS packets "
                         "from this interval (UI test)")
    ap.add_argument("--elrs-replay-interval", type=float, default=0.5)
    ap.add_argument("--device", default=None, help="UHD device arg (type=b200)")
    ap.add_argument("--freq", type=float, default=2.44e9)
    ap.add_argument("--srate", type=float, default=6.0e6)
    ap.add_argument("--gain", type=float, default=30.0)
    ap.add_argument("--uid", default=None)
    ap.add_argument("--phrase", default=None)
    ap.add_argument("--no-restart-elrs", action="store_true",
                    help="do not auto-restart a crashed ELRS child")
    args = ap.parse_args()

    if args.odid_source in ("btmon", "pcap") and not args.file:
        ap.error("--odid-source %s needs -f <file>" % args.odid_source)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    pkts_path = os.path.join(HERE, "elrs_pkts_%s.jsonl" % stamp)
    odr_jsonl = os.path.join(HERE, "odr_scan_%s.jsonl" % stamp)
    log_dir = HERE

    # --- ELRS child (real SDR) or replay thread ---------------------------
    elrs_proc = None
    elrs_replay_thread = None
    elrs_cmd = None
    if args.elrs:
        if args.elrs_pkts_replay:
            pkts_path = args.elrs_pkts_replay
            elrs_replay_thread = build_elrs_replay(args, pkts_path)
            elrs_replay_thread.start()
            print("[elrs] replaying synthetic packets -> %s" % pkts_path)
        else:
            elrs_cmd = build_elrs_cmd(args, pkts_path)
            if elrs_cmd:
                print("[elrs] starting: %s" % " ".join(elrs_cmd))

    # --- unified web UI (odr_web.py) -- parent of the whole thing ---------
    web_cmd = [PY, os.path.join(HERE, "odr_web.py"),
               "--port", str(args.port), "--host", args.host,
               "--source", args.odid_source, "--jsonl", odr_jsonl]
    if args.file:
        web_cmd += ["-f", args.file]
    if args.elrs:
        web_cmd += ["--elrs-pkts", pkts_path]
    print("[ui]   starting: %s" % " ".join(web_cmd))
    print("[ui]   open http://%s:%d" % (args.host if args.host != "0.0.0.0" else "localhost", args.port))

    web = subprocess.Popen(web_cmd, cwd=log_dir)

    def shutdown(signum, _frame):
        print("\n[scan] stopping…")
        if web.poll() is None:
            web.terminate()
        if elrs_proc and elrs_proc.poll() is None:
            elrs_proc.terminate()
        try:
            time.sleep(1)
        finally:
            if web.poll() is None:
                web.kill()
            if elrs_proc and elrs_proc.poll() is None:
                elrs_proc.kill()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # --- supervise: keep ELRS alive unless disabled -----------------------
    restarts = 0
    try:
        while True:
            if web.poll() is not None:
                print("[scan] web UI exited (%d); stopping." % web.returncode)
                break
            if elrs_proc is not None and elrs_proc.poll() is not None:
                if args.no_restart_elrs:
                    print("[elrs] child exited (%d); not restarting."
                          % elrs_proc.returncode)
                    elrs_proc = None
                elif restarts < 5:
                    restarts += 1
                    print("[elrs] child exited (%d); restarting (%d/5)…"
                          % (elrs_proc.returncode, restarts))
                    time.sleep(2)
                    elrs_proc = subprocess.Popen(elrs_cmd, cwd=log_dir)
                else:
                    print("[elrs] gave up restarting after 5 crashes.")
                    elrs_proc = None
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(0, None)
        print("[scan] done. jsonl: %s   elrs pkts: %s" % (odr_jsonl, pkts_path))


if __name__ == "__main__":
    main()
