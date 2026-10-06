# Product roadmap

[Back to README](../README.md)

Wave Raider's direction is a focused UAS detection platform that runs offline on a
small field computer. It detects drones from Remote ID broadcasts and, planned, from
the RF of their control and video links, learned from raw IQ recordings. The operator
should be able to answer: Are my receivers working? What am I observing? What evidence
supports it? How recently was it observed?

This roadmap describes intended work, not available features or release dates.
The first supported live receiver/platform combination and final screen layout
remain open decisions.

## Intended field deployment

The accepted architecture is one operator-carried reporting node plus two or three
operator-carried sensor nodes, with GPSD and a compatible GNSS receiver on every
receiving node. Each processing host is an Intel NUC or Jetson-class field computer
with attached receivers. See [distributed nodes and GPSD](distributed-nodes.md) for
the staged implementation and validation gates. A Samsung S20 5G accesses its GUI over local Wi-Fi. The phone is the
operator display, not the IQ-processing host. Exact host model, SDR, Wi-Fi topology
and resource budgets still need field validation. The web GUI is the current client;
a native Android client is not required to close the experimental DroneID branch.
The current HTTP server has no login; access control for an operational network is
still a deployment requirement, not an implemented application capability.

## M1a: recorded Remote ID decoder correctness — shipped in v0.5.1

The corrective release fixes transport framing and validates supported decoded
fields against independent Open Drone ID captures and pinned reference-decoder
output. It adds malformed-input diagnostics, honest unknown-value handling,
transport-qualified transmitter grouping, and first/last receive times.

This establishes a recorded-data decoder foundation. It does not establish live
reception, authentication, complete protocol conformance, or field performance.
See [current support](remote-id.md) and [validation](development.md).

## M1b: prove one live Remote ID receive path — next, blocked on a transmitter

A suitable positive test transmitter is required before this milestone can pass.
Choose the narrowest feasible receiver/transport/platform combination and state
its coverage explicitly. Candidate paths to investigate include macOS Bluetooth
legacy reception, a dedicated Bluetooth sniffer, or a Wi-Fi monitor-mode adapter.
None is an established supported live configuration in Wave Raider today.
Hardware and dependency choices remain open; this roadmap assumes no purchase.

The existing Scapy live source needs packet delivery through a bounded callback
queue instead of polling stored results. A platform Bluetooth adapter needs its
own validated input bridge; repairing Scapy alone does not provide one.

Exit criteria:

- Repeated live reception of identifiable expected messages from a known positive
  transmitter, plus a negative/control session.
- Visible input errors and drop counts, bounded buffering, orderly start/stop,
  and adapter disconnect/reconnect checks.
- Receiver/source provenance and receive times retained; platform device UUIDs
  distinguished from MAC addresses.
- Unsupported transports and monitoring gaps explicitly reported.

One received packet or a fake-sniffer test alone does not satisfy this milestone.
A failed feasibility probe should document its result without claiming support.

## Then: Monitor + Settings

Status: Monitor, Settings, and the Inspect RF drawer shipped in v0.6.0 and are
the default navigation. Monitor's Remote ID observations come only from a replayed
bundled sample; Inspect RF uses a supported SDR or a simulated spectrum. The
protocol dashboard was retired, and `/classic` redirects to Monitor. The exit
criteria below still require a live Remote ID source.

Replace protocol-first navigation with one primary Monitor view and Settings.
Monitor should combine receiver health, active coverage, recent observations,
and an evidence detail panel. Spectrum belongs in advanced inspection, limited to
the drone bands. Final interactions require design review.

Build session provenance, history/export, and an explicit stale-observation
lifecycle as part of the monitoring foundation. A first step shipped in v0.6.3:
WaveRaider attempts to save each stopped session locally with its source provenance; saved
sessions can be exported or deleted later (normally up to 30 kept). That is a local record of sample replays,
not a session database: there is no query across sessions, no live source, and no
retention policy beyond the count. After v0.6.3, observations also carry an explicit
lifecycle by age (fresh, stale, older) and a record of gaps between updates. They describe
sample replay updates on one computer, behind two provisional thresholds untested against
live reception: that is still not an operational stale-observation or alert policy, and a
gap says nothing about an aircraft leaving or returning.
A map is useful only for valid reported locations; power alone provides no
position, distance, or bearing.

Keep demo/replay data visibly separate from live data and out of operational
alerts/counts. Receiver failure must remain distinguishable from no observations.
Alerts should follow explicit evidence rules, deduplicate repeats, and handle
stale/reappearing observations. Watchlist matches do not establish a threat.

Exit: an operator can start monitoring, recognize receiver failure, explain an
observation, and export its evidence without navigating protocol catalogs.

## Retire unrelated protocol analysis — done in v0.6.0

The general Wi-Fi and Bluetooth network/device analyzers, GSM, DMR, TETRA, and the
analog survey, their samples, the unimplemented placeholders, and the protocol
dashboard with its APIs were removed after v0.5.1, which still contains them.

Kept: Wi-Fi beacon and Bluetooth legacy advertising parsing for Remote ID; shared
capture, source, model, reporting, and reasoning; receiver discovery; radio control;
and RF inspection. The band table moved to `bands.py` before the analog survey was
removed. Replacement coverage and intentional losses are listed in
[usage](usage.md#retired-after-v051).

Retirement doesn't add live reception: M1b is still the gate for live Remote ID.

## RF detection from raw IQ — planned

Remote ID only finds drones that broadcast it. RF detection looks for the drone's own
control, telemetry and video links in the drone bands that Inspect RF already covers,
using a classifier trained on raw IQ. Nothing here is implemented yet.

- **Data:** a curated library of recorded IQ with labels and provenance (SigMF), kept
  separate from the app, plus a capture campaign with known transmitters. Only classes
  with defensible labels and compatible data rights are used; a recorded license alone
  doesn't make data eligible. The current library has little protocol-labeled data and
  no ExpressLRS, Crossfire, HDZero or Walksnail recordings. A September 2026 search of
  public sources found no raw IQ with both usable license terms and protocol evidence
  for those links, nor for the DJI O3/O4 air units, AFHDS 3, OpenIPC-class links or
  Wi-Fi Remote ID. The search was not exhaustive: some dataset indexes could not be
  queried, and publicly posted recordings without a stated license were not counted.
  These classes therefore need new captures from known transmitters with logged modes.
  No such recording has been made yet. An experimental receive-only bladeRF micro
  recorder is now included; see the [node quickstart](node-quickstart.md). It still
  needs physical-hardware validation. Recordings of non-drone signals
  (Wi-Fi, Bluetooth, other ISM devices) and of background at the deployment site are
  first-class, because false alarms in shared bands are the main risk.
- **Training stays outside the app.** Training runs on separate, more powerful hardware.
  Wave Raider would ship only a small inference module and versioned model files,
  and work offline. Model updates would be prepared offline; field observations would
  not be used for automatic learning or retraining. Connected recordings (originals and their excerpts or derived
  bursts, reused emitters, mixtures, shared sessions) stay in the same group before any
  fitting or tuning, and evaluation uses held-out groups, so results aren't flattered
  by near-duplicates. Source licenses and label status are recorded for every run.
- **Pipeline:** timed dwells across the drone bands, burst detection, snippets, the
  classifier with an explicit "unknown" outcome and a score whose calibration must be
  validated, then tracks over time. The deterministic DroneID detector runs alongside it.
- **Models:** start with an interpretable baseline, then one shared neural model, adding
  per-family specialist checks only where the shared model confuses links (for example
  DJI OcuSync against Wi-Fi).
- **Order:** recorded IQ in Wave Raider first, clearly labeled; live IQ from an SDR
  later.

Before recorded-IQ integration: measured detection per link type at a false-event
budget per hour of background recordings, performance across signal strength,
rejection of link types it wasn't trained on, validated score calibration on stated
data, checks that it isn't learning recording-hardware quirks, and a runtime budget on
the field computer. The numerical budgets are agreed in a data and evaluation contract
before any training, which this roadmap does not authorize.

Live use needs its own authorization and gate: independent field sessions with known
transmitters **and** long, representative sessions without them; false events measured
for the whole pipeline through Monitor, with exposure time and uncertainty; scheduler
coverage and blind time per band; and sustained event, error and resource budgets on
the target hardware, all agreed before the evaluation. Detecting a known transmitter
alone does not end experimental status. If labels, data or exposure are insufficient,
the affected feature stays experimental or unavailable.

## Field deployment — planned

The current deployment target is a native Ubuntu installation on a small field
computer such as a NUC, with one or two SDR receivers; the exact hardware is not yet
chosen. Repeated sequential scans have blind intervals and do not provide continuous
coverage of every band. Alert delivery and log retention remain design decisions.
Both CPU types are supported for Docker builds: the Docker image is build-checked for
linux/amd64 and linux/arm64 (v0.6.1), and the Python install includes NumPy for CPU processing. A native
install is likely simpler for SDR access than Docker, which needs USB device
passthrough on Linux. Processing live IQ must keep up on the chosen hardware, measured
on that hardware.

## Emitter location — exploratory, after detection works

Wave Raider's planned field role is detection and alerting; emitter location would be a
separate later capability. Direction finding is a possible later direction, and the
preferred technique is **time difference of arrival (TDOA) across networked nodes**:
several receivers timestamp the same burst, and the small differences in arrival time
constrain where the emitter is. This is the accepted development direction, but
localization remains unimplemented and depends on the detection and timing gates
in the [distributed-node design](distributed-nodes.md).

What it would require:

- **Three or more suitably placed networked nodes** as a minimum for a constrained
  two-dimensional solution, with geometry and ambiguity checks. Two nodes constrain a
  position to a curve, not a point, and node geometry dominates the result.
- **A validated inter-node timing and sample-clock error budget**, with calibrated
  receiver delays. Timing hardware remains undecided. A 1 ns timing error corresponds to
  about 0.3 m of path-length difference, before geometry and other error sources.
- **Overlapping reception of the same burst in the same band** at every node, which the
  scan schedule would have to guarantee. Frequency-hopping control links make that
  scheduling problem harder, not easier.
- **A network link between nodes** carrying short signal snippets and a shared event
  identity, plus node positions at capture time with measured uncertainty and a solver
  that reports error bounds. Moving nodes use GNSS/GPSD position history; a GPS fix
  does not establish synchronized SDR sample timing.
- **A retention decision.** The intended policy is that Wave Raider retains no raw IQ
  persistently during operation by default. The accepted design allows bounded volatile
  event snippets for correlation; buffer and transfer limits must be defined before
  implementation. Persistent capture still requires an explicit retention decision.

Other candidates include calibrated received-signal-strength location estimates and
coherent multi-antenna bearing estimates. Their cost, accuracy and ambiguity would need
evaluation: a bearing alone is not a position, a single antenna baseline carries mirror
and phase-wrap ambiguity, and both approaches need calibration and stated assumptions.
None is scheduled.

## Later: broader coverage and measured performance

Evaluate Wi-Fi NAN and Bluetooth 5 Long Range support, receiver scheduling and
dwell time, and real DroneID recordings. Preserve exclusive radio access.
Full DroneID payload/CRC decoding is a separate scope decision.

Measure detection rate against known positive events, false alerts per hour,
detection delay, missed samples, queue drops, duplicate alerts, incorrect merges,
receiver recovery, and sustained memory/storage use. Separate methods and test
conditions; establish numerical targets after representative measurements.
Synthetic fixtures protect logic but cannot measure field sensitivity.

## Evidence contract throughout

- No decoded Remote ID report without a valid supported Remote ID message.
  Malformed or unsupported data belongs in diagnostics.
- DroneID candidates may exist without decoded payloads; they are not verified
  aircraft identities. RF profile matches are not detections.
- A future RF classifier result is a separate evidence type, not a ranking: a model
  result consistent with a supported link family, with its score type, model version
  and validated calibration domain, subject to measured error and coverage. It can say
  "unknown", is not a probability that a drone is present (these links have non-drone
  uses), and is never an identity, aircraft count or location. A quiet band doesn't
  prove absence.
- Any future location estimate is an estimate with stated error bounds, method and node
  geometry, never a point without uncertainty, and never an identity or aircraft count.
- Preserve raw identifiers and use supported text identifiers without silently
  changing them into watchlist matches.
- Keep receive time separate from claimed message time. Do not infer aircraft
  fusion from matching addresses, identifiers, frequencies, or timing alone.
