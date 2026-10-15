# Reproduce the offline checks

Python 3.10+ and Git are required. Run from the repository root:

```sh
python -B tools/validate-project.py
python -B -m unittest discover -s tools/tests -v
```

The first command checks Python syntax without importing or running research
tools, JSON parsing, every firmware hash and its artifact-register entry, all
rows of the stored rainbow CSV/JSON, CVE publication-index coverage, and local
Markdown file links. It does not check external websites or section anchors.
These consistency checks do not establish protocol correctness for Claude's
tools or prove firmware authenticity.

The tests exercise serial framing/CRC, pinned-source constants, fragmentation,
malformed input, passive defaults, capture preservation, device-info validation,
and parameter chunk reads through simulated serial endpoints. They use stdlib
only and never contact hardware. GitHub Actions runs the same commands on
Ubuntu and Windows with Python 3.10 and 3.12. Action dependencies are pinned to
the official tag revisions observed on 2026-10-14.

## Live serial diagnostics

Install `pyserial==3.5` in your chosen environment, then list ports with
`python tools/elrs-serial-probe.py --list-ports`. The tool uses an explicitly
selected endpoint and baud; opening a port is not a baud/protocol test. Receive
only is the default. `--probe` enables CRSF discovery/read requests;
`--dump-params` requires that flag. There are no parameter-write requests.

`--capture work/capture.bin` saves every received byte, including noise, and
refuses to replace an existing file. Capture is RX only; TX requests are logged
to stderr. Keep the command, descriptor listing, stderr and power/mode conditions
with your private capture. `--decode work/capture.bin` works offline without
pyserial. Output is JSON lines on stdout; action/error logs use stderr.

No valid frames or response does not establish a broken controller. It may mean
wrong endpoint, baud, device mode, timing or a non-CRSF protocol. A plausible
incomplete frame is retained awaiting more bytes; final trailing bytes are
reported by offline decoding, rather than discarded as a confirmed packet.
See [hardware identity evidence](HARDWARE-IDENTITY.md).

## CVE source refresh

`python tools/audit-cve-records.py --refresh` explicitly enables requests to the
CVE Program API. It keeps raw records and a candidate index in ignored `work/`.
Review before updating [the checked-in audit](research/CVE-AUDIT.md). CI performs
no network refresh and never executes vulnerability demonstrations.
