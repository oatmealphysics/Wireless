# Setup and current usage

[Back to README](../README.md)

These instructions describe v0.6.3, the latest release. v0.5.1 and earlier open
on the protocol dashboard and include the general-purpose analyzers retired in
v0.6.0. The browser opens on Monitor and Settings, which use a
bundled sample. Live Remote ID reception is roadmap work. Use the CLI to analyze
your own supported recorded captures.

## Experimental node reporting and recording

The current development build provides `wr node` and `wr record`. Follow the
[node quickstart](node-quickstart.md) for enrollment, IP-network transport, GPSD position
snapshots and opt-in bladeRF micro recording. These commands do not add live detections
or TDOA to Monitor. Node and recording tests do not validate physical radio performance.

## Docker with sample data

For a ready-to-run download, follow the README's three-step
[Install — copy & paste](../README.md#install--copy--paste).
To build from source, see [installation reference](install-advanced.md#build-from-source).
With Git authentication and Docker set up, start Docker and run:

```sh
git clone https://github.com/skippydinglechalk420/WaveRaider.git
cd WaveRaider
docker compose up
```

Without the Compose plugin, build and run the same image with plain Docker:

```sh
docker build -t waveraider .
docker run --rm -p 8899:8899 -v waveraider-data:/root/.waveraider waveraider
```

Open http://localhost:8899 and select **Start sample session** on Monitor. Stop with `Ctrl+C`.
Either way, a named volume keeps the watchlist and monitoring settings across
restarts. After `git pull`, run `docker compose up --build` (or repeat the
`docker build`) so Docker runs the updated version.

Docker provides a convenient sample environment; it does not configure
USB passthrough, host drivers, or live Remote ID reception. Host hardware support
must be assessed separately.

## Python alternative

Use Python 3.9 or newer. After cloning and entering the repository:

**macOS / Linux:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m waveraider.cli serve
```

**Windows PowerShell:**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install .
.venv\Scripts\python -m waveraider.cli serve
```

Open http://localhost:8899. Stop with `Ctrl+C`. Running the app does not establish
that a particular OS/receiver can capture supported packets.

The examples below use `wr` from the installed environment. If it is not on your
PATH, use `.venv/bin/python -m waveraider.cli` (macOS/Linux) or
`.venv\Scripts\python -m waveraider.cli` (Windows) in its place.

## Recorded Remote ID captures

Run these from the repository root:

```sh
wr data/uas_sample.pcap
wr --tui replay:data/uas_sample.pcap@20
wr --explain data/uas_sample.pcap
wr --html report.html data/uas_sample.pcap
```

Replace `data/uas_sample.pcap` with your own supported capture path. The bundled
file is synthetic. Replay streams recorded packets at the requested rate; it is
not live reception. HTML exports a recorded survey, not an operational session
history. Remote ID (`--protocol uas`) is the default and only protocol; a protocol
name from v0.5.1 such as `wifi` exits with an error saying it was retired.

| Source syntax | Current meaning |
| --- | --- |
| `path.pcap` or `file:path.pcap` | Read a supported recording |
| `replay:path.pcap@20` | Replay recorded packets at 20 frames per second |
| `live:<iface>` | Existing incomplete interface; unsupported for live Remote ID |

The default explanation backend uses local rules. Optional `--backend ollama`
uses a separately configured model service to summarize supplied facts; model
text is not new evidence. `cloud` is a placeholder, not an implemented backend.

## Web UI and watchlist

- **Monitor** (`/`): start a sample session to replay a bundled Remote ID capture.
  The session controls stay in the header, with observations and their selected
  evidence below. Open **Receiver status & coverage** for receive-path details and
  coverage limits; its summary always says live Remote ID is unavailable and reports
  receiver-check failures. Select a row (or focus it and press Enter/Space) to inspect
  it. On a narrow screen selection brings the details into view. Everything remains
  labeled sample data. **How to read this evidence** expands the longer provenance
  notes and stays open through refreshes. Unauthenticated-value warnings remain
  visible above the fields. Diagnostics and per-observation export are still available.
  All available observations appear together in one list, with evidence labels on each row.
  RF and DroneID are not monitoring inputs yet. **Session history & exports** links to Settings.
- **Inspect RF** (a drawer on Monitor): open it from an observation or from a radio
  under Settings › Receivers for live/simulated RF inspection; see
  [controls and limits](spectrum.md). Closing it stops live capture and releases the radio.
- **Settings** (`/settings`): Receivers (including radio control: whether Inspect RF
  currently holds the radio lease, under **Radio control & technical details**), Monitoring (live listening stays off until live
  capture exists, under **Receive paths · not available yet**; the stale threshold is provisional), Watchlist, Data & export
  (session JSON/CSV and the session history), and About & activity.

**Observation lifecycle.** By how long ago it was last updated, an observation is *fresh*,
then *stale* (dimmed; after 30 s by default), then listed under *Older observations* (after
300 s by default; machine value `lapsed`). Nothing is ever removed from a session, its exports
or its saved record. When an update arrives after a pause longer than the stale threshold,
Monitor records a gap: it shows the length of the last gap, how many gaps have been recorded,
and the latest recorded sequence of updates, which begins only when an update follows such a
gap. **These ages and gaps describe sample replay updates on this computer. Replay pauses can
create gaps. They do not show live reception or establish that an aircraft left or returned,
and are not an alert or detection policy.** At the default threshold, the bundled replay
normally stays fresh. Shorter thresholds can count replay-loop pauses as gaps. Each gap is
compared with the stale threshold in effect when the next sample update is processed; changing
it does not recalculate earlier counts. Both thresholds are under Settings › Monitoring and are
provisional. The effective age threshold is the greater of its saved value and the stale
threshold; lowering stale can restore the saved value. Ages and gaps are measured on elapsed
time, so setting the computer's clock backwards or forwards doesn't create or hide a gap; the
first- and last-heard times that are shown and exported are still wall-clock readings. Saved
lifecycle values are a snapshot at stop time; retained rows on Monitor continue to age. No
updates are being received while stopped. JSON and CSV exports and saved sessions carry
`lifecycle`, `update_gaps` and `last_gap_s`.

**Session history.** Every start of Monitor is a new session with its own id. When you
stop a session, replace it with a new start, or its replay fails, WaveRaider attempts to
save one record in `~/.waveraider/sessions/`. The session reports whether that save is
pending, succeeded or failed. There is one JSON file per session; newly written files use
mode 0600 and a newly created history folder uses mode 0700 on systems that enforce these
permissions; `WAVERAIDER_SESSIONS` moves the folder. **These permissions restrict filesystem
access, not access through the web server. The server currently listens on all interfaces
without authentication; anyone who can reach it can view, export or delete saved sessions.
Use it only on a trusted, access-restricted network.** The records include reported
identities, transmitter addresses, locations, operator fields and watchlist matches; today
these are sample data.

A record says where the data came from (today always the bundled sample: its name, file
and SHA-256), when the session ran, the stale threshold in force, and its observations and
diagnostics **as they stood at the stop**. The sample SHA-256 is of the file as read when
the session started and assumes the bundled sample doesn't change while the session runs;
if the file can't be read, the record says so instead. Up to 30 readable records with the
latest recorded session start times are kept; clock changes can affect which records are
retained, and the record just saved is never the one removed. Settings › Data & export
lists them, exports each as JSON or CSV, and deletes one or all. Delete all removes the
records present at that time; a session still running or still being saved can create a
record afterward. Like every POST request (the watchlist, the monitoring profile, starting
or stopping a session, the Inspect RF radio lease), the delete requests must be JSON, and a
browser request from another site is refused. The GET requests that start RF work or touch
the radio lease (`/api/spectrum`, `/api/analyze`, `/api/audio`, `/api/droneid`) must carry
the app's `X-WaveRaider-Request: 1` header, which another site's page cannot add. These are
cross-site-request measures for browsers, not authentication — a client that sends the
JSON or the header itself is accepted, and behind a reverse proxy the Host header must be
preserved. A record larger than 8 MiB is not saved, and the
session reports that. A file that isn't a consistent sample-session record of this
version is listed as unreadable rather than shown as history. A session still running
when the server exits is not saved. Saved sample sessions stay labeled as sample data;
history is a local record, not evidence of live reception.

Old `/classic` bookmarks redirect to Monitor. A discovered SDR does not imply a
working Remote ID packet receiver.

The watchlist is stored in `~/.waveraider/targets.json`. Matching uses entity
identity fields, not arbitrary attributes such as channel numbers. Settings adds
only UAS IDs, operator IDs, and transmitter addresses. Entries of older types from
v0.5.1 (BD_ADDR, SSID, device name, radio/cell IDs, IMSI/IMEI/TMSI, and others) stay
listed and removable, but match only identifier fields Remote ID observations carry.

## Retired after v0.5.1

The general-purpose protocol tools were removed in v0.6.0: the Wi-Fi (`wifi`),
Bluetooth LE (`ble`), Bluetooth Classic (`bt`), DMR, TETRA, GSM, and analog
(`rtl_power`) analyzers, their bundled samples, the unimplemented Bluetooth traffic,
UMTS, CDMA, LTE, and 5G NR placeholders, and the protocol dashboard with its views
and APIs. Use v0.5.1 if you still need them.

| Retired | Now |
| --- | --- |
| Dashboard Survey › UAS | Monitor's sample session, and `wr` for your own Remote ID recordings |
| Other Survey protocols | No replacement |
| Spectrum tab | The Inspect RF drawer, limited to the drone bands (868 and 900 MHz, 1.3, 2.4, 5.2 and 5.8 GHz). General band presets and labels (FM, airband, VHF, 433 MHz ISM) and audio listening were dropped |
| Targets dashboard and drawer | Settings › Watchlist and Monitor match badges. There is no cross-protocol sample sweep |
| Interfaces | Settings › Receivers and `wr interfaces`. The manually configured interface list was retired; `~/.waveraider/interfaces.json` is left untouched |
| Status and Logs | Settings › About & activity; radio control is under Settings › Receivers |
| Readout analysis text | `wr --explain` on a recording. Monitor has no narrative |

## Offline installation

The README documents tested Intel/ARM runtime bundles with launchers. Install Docker
on the destination first, then copy the entire extracted bundle; it starts offline.
The commands below are for building your own artifacts on a connected computer.
The default local analysis needs no cloud service.

**Docker**, built for the destination's CPU type (`linux/amd64` for Intel/AMD such as
most NUCs, `linux/arm64` for ARM). Docker Desktop builds either type; on Linux, set up
emulation first ([multi-platform builds](https://docs.docker.com/build/building/multi-platform/)):

```sh
docker buildx build --platform linux/amd64 -t waveraider:dev --load .
docker save waveraider:dev -o waveraider.tar
# On the destination:
docker load -i waveraider.tar
docker run -p 8899:8899 -v waveraider-data:/root/.waveraider waveraider:dev
```

The named volume keeps the watchlist and monitoring settings when the container is
recreated. Check the type with `docker image inspect waveraider:dev --format
'{{.Architecture}}'`. Without buildx, `docker build --platform …` works too, but it
relabels the cached `python:3.11-slim-bookworm` base image; run `docker pull python:3.11-slim-bookworm`
before building for your own computer again.

This packages the current checkout, so it's tagged `dev`. To package a release
instead, run `git checkout v0.6.3` (or an earlier tag) first and tag the image to
match, for example `waveraider:0.6.3`.

**Python.** Build the wheelhouse with the destination's Python version (major and
minor, for example 3.11): pip resolves dependencies for the building Python, and some
require a newer Python than Wave Raider does. NumPy is a compiled core dependency, so the
OS family and CPU architecture must also match the destination. Test the wheelhouse with no network before taking it offline:

```sh
python3 -m pip wheel . -w wheelhouse
python3 -m venv /tmp/wr-check
/tmp/wr-check/bin/python -m pip install --no-index --find-links wheelhouse waveraider
# Move wheelhouse to the destination, then install into a virtual environment there:
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse waveraider
.venv/bin/python -m waveraider.cli serve
```

On Windows, use `python` and `.venv\Scripts\python`.

Native SDR libraries/tools need separate preparation. Offline installation does
not add live Remote ID support.

## Display theme

Use **Theme** in the Monitor or Settings header to choose **System**, **Dark**, or
**Light**. System follows your device appearance. Your selection is stored only in
this browser and applies to both pages; it does not change receiver or monitoring
settings. If browser storage is unavailable, the choice lasts for the current page.
Dark uses charcoal/navy surfaces with soft white text; Light uses pale surfaces with
dark text. Both retain labeled warnings, evidence limits and keyboard focus rings.

## Sensor node health

Monitor includes a separate **Sensor nodes** disclosure. Configure the reporting
computer with `WAVERAIDER_COORDINATOR` to enable its read-only local coordinator view.
It shows connection/report ages, optional receiver queries, GPS status, queue losses
and retained recording summaries. Simulated recordings stay labeled and do not enter
the sample observation list. Device access does not establish reception; node positions
are not aircraft positions. See [node setup](node-quickstart.md#receiver-health-and-the-monitor-panel).
