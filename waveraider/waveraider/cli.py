"""Wave Raider command-line entry point (console script: `wr`)."""
from __future__ import annotations

import sys
from pathlib import Path

from .engine import survey_source
from .protocols import DEFAULT_PROTOCOL, get_plugin
from .sources import get_source

_SEV_MARK = {"critical": "[!!]", "warning": "[! ]", "info": "[ i]"}

_USAGE = (
    "usage: wr [--tui | --explain | --html FILE] [--protocol uas] [--backend NAME] <source>\n"
    "       wr serve [--port N] | wr interfaces | wr sdr | wr --help\n"
    "       wr node --help | wr record --help | wr verify-recording DIRECTORY\n"
    "  <source>: a Remote ID capture (Wi-Fi beacon or Bluetooth legacy advertising pcap),\n"
    "            or replay:<path>[@rate] | live:<iface>\n"
    "  --protocol: uas (Remote ID), the default and only supported protocol.\n"
    "  live:<iface> is incomplete; live Remote ID, NAN and BT5 Long Range are unsupported.\n"
    "  serve: web UI on Monitor + Settings (sample data).\n"
    "  The general Wi-Fi/Bluetooth, DMR, TETRA, analog and cellular analyzers were retired after v0.5.1."
)


def render(plugin, s) -> str:
    out = [f"capture : {s.path}", f"frames  : {s.total_frames}", ""]
    out.append("  ".join(plugin.columns()))
    for e in plugin.list_entities(s):
        alerts = sum(1 for f in s.findings if f.evidence.get("entity") == e.id)
        out.append("  ".join(plugin.row(e, alerts)))
    out += ["", f"FINDINGS ({len(s.findings)})"]
    for f in s.findings:
        out.append(f"  {_SEV_MARK.get(f.severity, '[ ?]')} {f.summary}")
    if not s.findings:
        out.append("  none")
    return "\n".join(out)


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if argv[1:2] == ["node"]:
        from .node_cli import main as node_main
        return node_main(argv[2:])
    if argv[1:2] == ["verify-recording"]:
        from .recording_check import main as verify_main
        return verify_main(argv[2:])
    if argv[1:2] == ["record"]:
        from .node_cli import record_main
        return record_main(argv[2:])
    if "-h" in argv[1:] or "--help" in argv[1:] or argv[1:2] == ["help"]:
        print(_USAGE)
        return 0

    def opt(flag, default=None):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                return argv[i + 1]
        return default

    if len(argv) > 1 and argv[1] == "serve":
        from .web import serve
        serve(port=int(opt("--port", "8899")))
        return 0

    if len(argv) > 1 and argv[1] == "interfaces":
        from .interfaces import detect, render as render_interfaces
        print(render_interfaces(detect()))
        return 0

    if len(argv) > 1 and argv[1] == "sdr":
        from .spectrum import readiness
        r = readiness()
        radios = r.get("radios", [])
        print(f"SDR readiness — {len(radios)} radio(s) detected")
        for rd in radios:
            mark = "LIVE-CAPABLE" if rd["live_capable"] else "simulated only"
            star = " *" if rd["usb_id"] == r.get("usb_id") else "  "
            print(f"{star}{rd['device']}  [{rd['usb_id']}]  — {mark}")
            print(f"    backend: {rd['backend_label'] or '(none for this device)'}"
                  f" · tool: {'installed' if rd['tool_installed'] else 'NOT installed'}")
            if rd["install_hint"]:
                print(f"    to enable: {rd['install_hint']}")
        if radios:
            print("  (* = the radio Inspect RF drives by default)")
        if not r["sdr_detected"]:
            print("  no SDR recognized.")
            for d in r.get("unrecognized_usb", []):
                print(f"    unrecognized USB: {d['id']}  {d['name']}")
        return 0

    backend_name = opt("--backend", "none")
    protocol = opt("--protocol", DEFAULT_PROTOCOL)
    html_path = opt("--html")

    consumed = {"--tui", "--explain", "--backend", backend_name, "--protocol", protocol}
    if html_path:
        consumed |= {"--html", html_path}
    args = [a for a in argv[1:] if a not in consumed]
    if len(args) != 1:
        print(_USAGE, file=sys.stderr)
        return 2
    source_spec = args[0]

    try:                                   # reject a retired or unknown protocol before opening anything
        plugin = get_plugin(protocol)
    except ValueError as e:
        print(f"wr: {e}", file=sys.stderr)
        return 2

    if "--tui" in argv:
        from .tui import run as run_tui
        run_tui(source_spec, protocol=plugin.name)
        return 0

    try:
        s = survey_source(plugin, get_source(source_spec))
    except FileNotFoundError:
        print(f"no such capture: {source_spec}", file=sys.stderr)
        return 1

    if html_path:
        from .report import render_html
        Path(html_path).write_text(render_html(plugin, s), encoding="utf-8")
        print(f"wrote {html_path}")
        return 0

    if "--explain" in argv:
        from .reason import get_backend
        backend = get_backend(backend_name, knowledge=plugin.knowledge, describe=plugin.describe)
        print(f"ANALYSIS  ({backend.name})\n")
        print(backend.explain(s))
    else:
        print(render(plugin, s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
