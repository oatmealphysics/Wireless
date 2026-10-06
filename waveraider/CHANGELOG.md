# Changelog

Wave Raider is moving toward a focused UAS detection platform, Remote ID first.
See the [roadmap](docs/roadmap.md) for planned work; entries below describe shipped
changes unless marked Unreleased. The general-purpose protocol tools were retired
in 0.6.0.

## Unreleased

- Made node setup and CLI help network-neutral: use any reachable IP transport,
  with routing and firewall access configured by the operator.

### Validated downloads and node health

- Added a same-revision release gate across the full suite and both architecture
  builds; complete checksummed bundles are published as permanent release assets.
  Previous releases remain available when a replacement fails validation.
- Added an opt-in read-only Sensor nodes panel with receiver query state, report/GPS
  ages, queue losses and recording summaries, separate from sample observations.
- Added bounded optional bladeRF micro device/FPGA queries to heartbeats. Device
  accessibility does not establish RF reception or timing readiness.
- Added `wr verify-recording` for hashes, counter metadata and sample statistics.
  Physical capture, GPS/network recovery and field performance still require validation.

### Experimental node reporting and recording

- Added a separate authenticated HTTPS reporting service and per-node enrollment,
  bounded durable retry queues, duplicate/out-of-order handling and local status.
- Added explicit, bounded bladeRF micro RX0 recordings with SigMF data/metadata,
  hardware sample counters, discontinuity flags, hashes, quotas and partial-file handling.
  Simulation mode exercises the workflow without opening hardware.
- Added optional local GPSD snapshots. Position validity, sample timing and localization
  readiness stay separate; no TDOA, moving capture-position history, GUI node integration
  or automatic live detection is claimed. Physical receiver/network validation remains pending.
- Included OpenSSL in runtime images for enrollment; installed-runtime checks now exercise
  TLS reporting and simulated recordings on both architectures.

### Installation

- Simplified the README to three beginner steps with Docker download links,
  platform-specific launch commands, and a separate technical installation reference.

- Included NumPy in every install and libbladeRF/CLI in Docker images.
- Added tested offline runtime downloads for Intel/AMD and ARM64, with checksum and
  CPU checks, Windows/macOS/Linux launchers and persistent data. Docker is a host
  prerequisite; model-specific radio setup remains separate.
- Corrected private-repository authentication, Linux prerequisites and offline
  wheelhouse compatibility instructions. Documented phone access for bundles.

### Architecture documentation

- Recorded the accepted reporting-node plus 2–3 sensor-node design, local GPSD
  position inputs, CPU-first processing and staged timing/localization gates.
  This documentation does not add node networking, GPSD integration or TDOA.

### Experimental DroneID scan

- **Carrier offset search.** The pilot pair is now searched over carrier offsets from −22.5 to
  +22.5 kHz in 7.5 kHz steps. Before, an offset of about 7.5 kHz or more between the drone and the
  receiver hid the pilots entirely. Results gain `cfo_hz`, the offset used for a candidate
  (`null` otherwise).
- **The pilot pair is the evidence.** A candidate no longer also needs the cyclic-prefix (OFDM)
  metric to pass; the metric is still reported. Synthetic weak-signal tests cover a
  candidate below that gate. `detected` is true for an OFDM-like signal or a candidate.
- **Longer capture.** A scan now captures 700 ms (10,752,000 samples) instead of about 260 ms, longer
  than the roughly 600 ms between DroneID bursts. The pilot correlation uses FFTs, so the scan
  analyses in a few seconds despite the longer capture and the offset search.
- A real DJI Mini 4K recording (WaveShark, CC BY 4.0) is now an optional test fixture: all 13
  bursts are candidates, including with +12/−18 kHz added offset. Eight pinned interference
  recordings (Wi-Fi, LTE, BLE, ExpressLRS, Remote ID and analog video; 2.15 seconds total)
  produced no candidates in nine windows. This is a regression check, not a field false-alarm
  estimate. Candidates remain unconfirmed: no payload or CRC is decoded.
- **Bounded detector intermediates.** Cyclic-prefix metrics and pilot searches operate in
  blocks, overlapping enough to preserve pilot pairs across boundaries. New tests cover
  boundaries, frequency-grid edges/midpoints and a full 700 ms noise capture.
- **Recorded validation in CI.** The full Python/JavaScript suite now runs on pull requests,
  including the hash-verified Mini 4K fixture. A configured missing fixture fails explicitly.

### Monitor and Settings

- **One observation list.** Removed the collection-type filters; all available
  observations remain visible together, with evidence labels and older observations
  retained in the same table. This does not add RF or DroneID monitoring.

- **Field themes.** Shared charcoal/navy and light palettes with high-contrast text,
  blue actions and labeled status colors. A System/Dark/Light selector follows device
  appearance or remembers a browser preference across Monitor and Settings. Spectrum
  traces and labels follow the theme; capability claims and radio behavior are unchanged.

- **GUI refresh.** A compact session bar, observation-first workspace and consistent
  Settings panels replace the dense stacked layout. Receiver status and coverage
  expand on demand; receiver-check failures remain visible in their summary.
  Sample labels, unauthenticated-field warnings, lifecycle states and save failures
  remain explicit. Longer evidence notes expand without collapsing on each refresh.
  Selecting an observation on a narrow screen brings its evidence into view; wide
  screens keep the list and detail side by side. Receiver coverage and evidence details
  explain the limits of available data.
  RF controls, receive capabilities, APIs and stored data are unchanged.

- **Observation lifecycle.** An observation is now *fresh*, then *stale*, then listed under
  *Older observations* (machine value `lapsed`), by how long ago it was last updated (30 s and
  300 s by default; the second threshold is new under Settings › Monitoring, and its effective
  value is never below the first). Nothing is removed: older observations stay in the session,
  can still be selected and exported, and are saved with it. When an update follows a pause longer
  than the stale threshold, Monitor records a gap and shows the last gap's length, the number of
  gaps recorded and the latest recorded sequence of updates. These ages and gaps describe sample
  replay updates on this computer; replay pauses can create them; they do not show live reception
  or establish that an aircraft left or returned, and are not an alert or detection policy. Ages
  and gaps use elapsed time, so a system clock change doesn't invent or hide one. Session status
  counts observations by lifecycle. JSON exports and saved session records gain `lifecycle`,
  `sequence_started` and `update_gaps`; **CSV exports gain three trailing columns,
  `lifecycle,update_gaps,last_gap_s`** — existing columns keep their names and positions, so read
  columns by header, not by count (records saved by 0.6.3 export with those cells blank). A
  monitoring-settings request that sends both thresholds is validated as a whole and saved once;
  a refused request changes nothing. Recorded-file analysis (`wr <capture>`) is unchanged and
  records no gaps, because a capture's packet times say nothing about when WaveRaider was listening.

## 0.6.3

### Web server

- **Cross-site request checks on the routes that change something or use the radio.** Every POST
  route — the watchlist, monitoring profile, session start/stop, saved-session deletion and the
  Inspect RF radio lease — now requires `application/json` with a JSON object body, and refuses a
  browser request that comes from another site (`Origin` or `Sec-Fetch-Site`). The four GET routes
  that start RF work or touch the radio lease (`/api/spectrum`, which takes or renews the lease
  when it carries an owner, `/api/analyze`, `/api/audio`, `/api/droneid`) now require the app's own
  `X-WaveRaider-Request: 1` header, which a page on another site cannot add, and refuse the same
  foreign origin metadata. Before this, a page on any website open in the same browser could have
  posted a form to a running WaveRaider and changed the watchlist, started or stopped a session, or
  taken or released the radio, and an image or link could have started a sweep. Other GET routes
  only read (status, observations, exports, settings, hardware detection); a page on another site
  can request them but cannot read the answers. These are browser cross-site-request measures, not
  authentication: the server still listens on all interfaces without a login, a client that sends
  the JSON or the header itself (curl, a script) is accepted, and behind a reverse proxy the `Host`
  header must be preserved. This changes API request requirements: POSTs must send
  `Content-Type: application/json` and a JSON object body (use `{}` for operations with no fields),
  and requests to the four radio GET routes must send the header. If Inspect RF stops working after an update, reload the page so the browser picks up the
  new script. Retired and unknown POST paths still return 404.

### Monitor and Settings

- **Session history.** Every start of Monitor is a session with its own id. When a session
  stops (stopped, replaced by a new start, or ended by a replay failure) WaveRaider attempts to
  save one record under `~/.waveraider/sessions/`: the source and its provenance (the bundled
  sample's name and file, and the file's SHA-256 as read when the session started), start and
  stop times, the stale threshold in force, and the observations and diagnostics as of the stop.
  Up to 30 readable records with the latest recorded session start times are kept; clock changes
  can affect which records are retained. Settings › Data & export lists saved sessions, exports
  each as JSON or CSV and deletes one or all. Monitor reports whether saving the stopped session
  is pending, succeeded or failed, including when replay ended with an error. Records are looked
  up by a validated session id. Newly written files use mode 0600 and newly created history
  folders use mode 0700. The directory scan skips entries that are already symlinks and, where
  the platform allows, records are opened without following links; this is not protection
  against a local writer replacing files during a read. These permissions don't restrict access
  through the web server, which listens on all interfaces without authentication: anyone who can
  reach it can view, export or delete saved sessions. The two delete routes require a JSON request
  and refuse a browser request from another site — a cross-site-request measure, not authentication.
  A record over 8 MiB is refused and reported rather than saved. Files that aren't consistent sample-session records of
  this version are listed as unreadable. Session exports now carry the session id. A session
  still running when the server exits is not saved. Sample sessions stay labeled as sample data;
  this is not live reception.

### Documentation

- Roadmap: add an exploratory emitter-location section (multi-node TDOA, its timing
  and geometry requirements, and the alternatives), stated as uncommitted work.
- README and roadmap: record that a September 2026 search of public sources found no
  usable licensed raw IQ for the priority FPV control and video links, so those classes
  depend on the project's own recordings, which have not started.
- README: remove the outdated note that the logo was only on `master`. Development
  notes: drop the outdated v0.5.1 test count.

## 0.6.2

### Inspect RF

- **Stricter analog video check.** The check now reports video only at the NTSC
  (15,734 Hz) or PAL (15,625 Hz) line rate within ±0.2%, and only when a second or
  third harmonic of the line rate is also present. The previous check took the
  strongest peak anywhere between 15.0 and 16.2 kHz: it flagged video in about 6.5%
  of synthetic white-noise test trials and accepted off-standard rates such as
  16.02 kHz. With the change, those synthetic trials report no video, and the recorded
  analog video test captures are still detected. Interference at the same tone and
  harmonic frequencies can still be reported as video. This has not been checked on
  live radio captures.

### Branding

- Adopt the official WaveRaider wave-and-aircraft logo in the README, with its emblem
  in Monitor, Settings and the browser tab. Assets are bundled for offline use.

### Documentation

- Refresh the GitHub landing page with the official logo, current capability limits,
  and the planned offline field-monitoring workflow; distinguish unimplemented IQ
  classification and alerts from the released application.

- Describe the current direction in the README and roadmap: Remote ID plus planned
  RF detection from raw IQ, classifier results as a separate evidence type with gates,
  and field deployment on a small amd64 or arm64 computer. The package description
  and development notes match.

## 0.6.1

### Multi-architecture builds

- A GitHub Actions workflow builds the Docker image for `linux/amd64` (Intel/AMD, such
  as NUCs) and `linux/arm64` (ARM, such as Apple Silicon or a Raspberry Pi 5) and
  smoke-tests each one: CPU type, `wr --help`, the bundled sample, Monitor, and a
  prompt `docker stop`. It never publishes an image, and actions are pinned to commits.
- The offline install guide shows how to build an image for the offline computer's
  CPU type (`docker buildx build --platform …`), check it, and what the classic
  builder's `--platform` does to the cached base image. The Python wheelhouse is pure
  Python, so its OS and CPU type may differ from the offline computer's, but it must
  be built with the same Python version and checked with an offline install first;
  the `sdr` extra also needs a matching OS and CPU.

## 0.6.0

### Monitor, Settings, and Inspect RF (sample data)

- WaveRaider now opens on **Monitor** with two tabs, Monitor and Settings. The
  protocol dashboard was then retired (see Removed); `/classic` redirects to Monitor.
- Monitor replays a bundled Remote ID capture as a clearly labeled sample session:
  receiver status, coverage, observations, evidence detail, diagnostics, a
  watchlist action, and JSON/CSV export. It is not live reception.
- Settings covers receivers, monitoring (live listening stays off; the stale
  threshold is provisional), a watchlist limited to UAS ID, operator ID, and
  transmitter address, session export, and about & activity.
- The Inspect RF drawer on Monitor hosts the spectrum view. Closing it stops live
  capture and releases the radio, including when replies arrive late or can't be read.
- Inspect RF covers the drone bands only (868 and 900 MHz, 1.3, 2.4, 5.2 and 5.8 GHz)
  and opens on 2.4 GHz. The bands cover every range in the RF profile reference. Band
  tabs show the band in view, and tuning, zoom and saved views stay inside one band,
  with a note when a typed view is moved; the server applies the same limits. Signal
  rows and the plot overlay use drone band names, and the simulated spectrum has
  drone-band emitters. The analog video check runs in the 1.3 and 5.8 GHz video bands.
- Settings › Receivers shows radio control: whether Inspect RF currently holds the
  radio lease, with its tuning.
- Fix: Monitor no longer scrolls back to a focused row or button every second.
- Fix: under Docker, the `http://localhost:8899` line now shows as soon as the app is
  ready, and `Ctrl+C` stops it right away instead of after a 10-second wait.
- Fix: in the Python install, if port 8899 is already taken (for example by a Wave
  Raider that is still running), `wr serve` says so and suggests `--port` instead of
  printing a traceback. (Under Docker, Compose reports a busy host port itself.)
- Fix: with no SDR recognized, Inspect RF and `wr sdr` no longer list USB hubs, host
  controllers, keyboards, mice or digitizers as possible radios (Docker's virtual
  machine showed several).
- Fix: `wr --help` (also `-h` and `help`) prints the usage instead of "no such capture".
- The Quiet Precision appearance is the only design; its stylesheets were consolidated
  without changing the look, and the Inspect RF no-signal bar and drone-evidence badge
  now use palette colors.

### Removed

- The general-purpose protocol analyzers and their CLI protocols: `wifi`, `ble`, `bt`,
  `dmr`, `tetra`, `gsm`, and `analog`, plus the unimplemented `bt_traffic`, `umts`,
  `cdma`, `lte`, and `nr` placeholders. Remote ID (`uas`) is now the CLI and TUI
  default; a retired name exits with an error. v0.5.1 still includes them.
- Their bundled samples and generator scripts. The Wi-Fi sample survives only as a
  Remote ID negative-control test fixture.
- The protocol dashboard (Survey, Spectrum tab, Targets, Interfaces, Status, Logs)
  and its APIs: `/api/survey`, `/api/protocols`, `/api/target-sweep`, `/api/targets`,
  `/api/interfaces`, and `/api/status`. `/classic` redirects to Monitor.
- The manually configured interface list (`~/.waveraider/interfaces.json` is left
  untouched on disk).
- Inspect RF's general band presets and band labels (FM, airband, VHF, marine, TV,
  433 MHz ISM), tuning outside the drone bands, and audio listening (Listen and the
  per-signal 🎧 buttons). The backend audio endpoint is no longer used by the UI.
- Spectrum signals no longer suggest a protocol analyzer; `protocol` is always `null`.
- Existing watchlist entries are kept. Older entry types match only identifier
  fields that Remote ID observations carry.

### Documentation and product wording

- Reframe the README, guides, package description, CLI help, and dashboard copy
  around the Remote ID first direction, with current capabilities separated from
  the roadmap. Label sample-backed views and legacy protocol placeholders.
- Document the next sequence: prove one live Remote ID receive path (blocked on
  a positive transmitter), build Monitor + Settings, then retire unrelated protocols.
- Restore the step-by-step install guide for Windows, macOS, and Linux, now opening
  on Monitor, and add an Updating step (`git pull`, then `docker compose up --build`).
  Corrections: Windows continues in a new PowerShell window after Docker Desktop's
  setup, Homebrew uses the `docker-desktop` cask, Ubuntu and Debian get separate Linux
  steps, and the offline Python wheelhouse includes Wave Raider itself and installs
  into a virtual environment.

## 0.5.1

### Remote ID decoding fix

- Skip the 1-byte message counter that precedes Remote ID payloads in Wi-Fi beacons
  and Bluetooth advertisements. Earlier versions misread real broadcasts — identifiers
  came out corrupted and positions were lost — while the bundled synthetic sample,
  built with the same mistake, still passed. The sample is regenerated in spec format.
- Check decoding against independent Open Drone ID reference captures, with expected
  values from the opendroneid-core-c reference decoder (`tests/fixtures/opendroneid/`).
- Decode protocol versions 0–2. Other versions, truncated Wi-Fi elements or Bluetooth
  advertising data, malformed or truncated message packs, and payloads carrying only
  undecoded Authentication/Self-ID messages are counted as diagnostics. They never
  create or refresh a detection.
- Report unknown values as not reported (both-zero coordinates, −1000 m altitude,
  missing timestamps); a newer message with unknown values clears older ones.
  Out-of-range coordinates and timestamps are dropped and counted as invalid.
- Use only text serial-number and registration IDs as the UAS ID, exactly as sent
  (printable ASCII padded with NULs; nothing trimmed). ID type "None", binary and
  badly padded identifiers keep their raw bytes but are not used for watchlist matching.
- Keep the same address heard over Wi-Fi and Bluetooth as separate transmitters.
- Label operator locations with their declared type (takeoff, live GNSS, fixed) and
  record first/last received time and transport per transmitter.

Remote ID decoding is validated on recorded captures only. Live capture, Wi-Fi NAN,
and Bluetooth 5 Long Range are not supported yet.

## 0.5.0

### Spectrum and audio

- Click a spectrum peak to focus the tuner, analyze the signal, and play an audio clip.
- Use a supported 960 kS/s capture rate for bladeRF FM/AM analysis and audio;
  reject failed hardware configuration instead of returning mistimed audio.
- Apply tuning immediately; improve the live sweep loop and switching between SDRs.
- Add FM/AM audio clips, analog signal analysis, drone-band presets, and RF profile hints.
- Add Steady/Slow/Live display pacing, smoothing, peak hold, and display hold/resume.
  Capture continues while the display is held. CSV exports raw data; PNG saves the displayed plot.

### Experimental DroneID scanning

- Require an ordered, correctly spaced Zadoff–Chu pilot pair plus OFDM evidence
  before labeling a result a DroneID candidate.
- Use the Spectrum tuner center and refuse centers outside 2400–2483.5 MHz and
  5725–5850 MHz. Reject missing, nonnumeric, nonfinite, and out-of-band API centers.
- Show the captured range at 15.36 MSPS (center ±7.68 MHz), independent of display span.
- Keep frequency/bandwidth profile hints separate from drone evidence: power-only
  spectrum peaks remain unidentified and do not receive drone badges.

DroneID is experimental: pilot-pair candidates only, with no payload/CRC validation
or GPS extraction. Positive validation on a real recording remains pending. A
roughly 260 ms capture can miss bursts occurring about every 600 ms; negative
results do not establish absence. Full decoding and continuous monitoring are
not included. Remote ID packet parsing is a separate feature.
