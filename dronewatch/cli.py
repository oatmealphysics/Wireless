"""
dronewatch command line.

    dronewatch webui [--replay] [--sdr] [--dji CMD] [--port 8090] [--verbose]
    dronewatch selftest
    dronewatch info

The webui command wires up the capture sources you select into the shared
fusion TrackTable and serves the unified dashboard. With no radio hardware
(SDR-only operator without BLE/Wi-Fi monitors), `--replay` lets you see the
whole pipeline working with synthetic Remote ID drones.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import log as _log
from .log import get_logger

log = get_logger(__name__)


def _build_sdr_source(args):
    """Open the radio + scanner; return an SdrScanSource or None on failure."""
    from .fusion import SdrScanSource
    try:
        from .radio import RadioDevice
        from .dronescan import DroneScanner
    except Exception as e:
        log.warning("SDR source unavailable (import failed): %r", e)
        return None
    try:
        radio = RadioDevice(args.device)
    except Exception as e:
        log.warning("could not open radio %r: %r — skipping SDR source",
                    args.device, e)
        return None
    scanner = DroneScanner(radio, gain=args.gain)
    return SdrScanSource(scanner)


def cmd_webui(args):
    from . import fusion
    from . import webui

    table = fusion.TrackTable()
    sources: list = []

    if args.sdr:
        src = _build_sdr_source(args)
        if src is not None:
            sources.append(src)
    if args.dji:
        sources.append(fusion.DjiDroneIdSource(args.dji.split()))
    if args.replay or not sources:
        if not sources and not args.replay:
            log.warning("no live source available — falling back to --replay "
                        "(synthetic Remote ID). Use --sdr with a radio, or "
                        "--dji CMD, for live data.")
        sources.append(fusion.ReplaySource())

    fusion.start_sources(sources, table)
    httpd = webui.serve(table, host=args.host, port=args.port,
                        window_s=args.window)
    print(f"dronewatch dashboard: http://{args.host}:{args.port}  "
          f"(sources: {', '.join(s.name for s in sources)})", flush=True)
    print("Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nshutting down.", flush=True)
        httpd.shutdown()


def cmd_selftest(args):
    from . import selftest
    return selftest.main()


def cmd_info(args):
    try:
        from .radio import RadioDevice
        r = RadioDevice(args.device)
    except Exception as e:
        print(f"radio not available: {e}", file=sys.stderr)
        return 2
    print(f"opened {args.device}")
    try:
        print("gain range:", r.get_gain_range())
        print("rx antennas:", r.get_antennas("rx"))
    except Exception as e:
        print(f"probe error: {e}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dronewatch",
                                description="Unified drone detection + identity")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="debug logging (incl. UHD binding-probe trail)")
    p.add_argument("--quiet", "-q", action="store_true", help="errors only")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("webui", help="serve the unified dashboard")
    w.add_argument("--replay", action="store_true",
                   help="include the synthetic Remote ID source (demo/no-hardware)")
    w.add_argument("--sdr", action="store_true",
                   help="include the SDR band/protocol scanner (needs a radio)")
    w.add_argument("--dji", metavar="CMD", default=None,
                   help="include DJI DroneID; CMD is the receiver command whose "
                        "stdout is one JSON line per frame")
    w.add_argument("--device", default="type=b200",
                   help="UHD device args (e.g. type=b200)")
    w.add_argument("--gain", type=float, default=30.0, help="RX gain (dB)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8090)
    w.add_argument("--window", type=float, default=120.0,
                   help="default table time window in seconds (0 = all time)")
    w.set_defaults(fn=cmd_webui)

    s = sub.add_parser("selftest", help="run the hardware-free self-test")
    s.set_defaults(fn=cmd_selftest)

    i = sub.add_parser("info", help="probe the radio")
    i.add_argument("--device", default="type=b200")
    i.set_defaults(fn=cmd_info)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _log.setup(verbose=args.verbose, quiet=args.quiet)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
