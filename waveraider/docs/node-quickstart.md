# Node reporting and IQ recording — experimental

This first implementation adds **CLI node heartbeats, optional GPSD snapshots and
explicit IQ recordings**. Your bladeRF micro supplies RF samples. Node reporting
works over any IP network that provides a route from each sensor computer to the
reporting computer, with TCP port **8943** allowed. Use Ethernet, Wi-Fi, a routed
network, VPN, or another IP transport of your choice. WaveRaider does not configure
the network equipment; configure routing, name resolution and firewall access separately.
The reporting computer's web GUI remains on port 8899 and still shows sample replays;
node telemetry is inspected with the commands below or the optional Sensor nodes panel.
It is kept separate from Monitor’s sample observations.

No TDOA solver, inter-radio clock synchronization, automatic detection reporting or
remote recording commands are included. A successful network test does not validate
RF timing. Use a native Linux install for initial physical-radio testing; Docker
requires explicit USB passthrough and separate mounted configuration/data folders.

## 1. Check the tools

After installing WR's current source or runtime image:

```sh
wr node --help
wr record --help
```

For a downloaded Docker bundle, run commands inside its image. From the extracted
folder, this verifies the CLI is present (use `sudo` before `docker` on Linux):

```sh
docker compose run --rm --entrypoint wr waveraider node --help
```

The physical-node walkthrough below uses native Linux. Running services or captures
in containers additionally needs explicit config/data mounts, port 8943 publishing
and USB access; the default GUI container does not enable those automatically.

The runtime image includes OpenSSL for enrollment and libbladeRF. With a native
Ubuntu install, add these before enrollment (skip if already installed):

```sh
sudo apt update && sudo apt install -y openssl libbladerf2 bladerf
```

The commands below assume `wr` is on PATH. For the source virtual environment,
activate it first: `. .venv/bin/activate`. Do not use an old v0.6.3 release ZIP;
these commands are in the current tested build.

## 2. Set up the reporting computer

Find the reporting computer's address on your chosen IP network. Run this command and
enter that address when prompted (for a same-computer demonstration, enter `localhost`):

```sh
printf 'Reporting computer IP or hostname: '
read -r WR_COORDINATOR
wr node init --directory "$HOME/WR-network" --host "$WR_COORDINATOR" --nodes sensor-a sensor-b sensor-c
wr node serve --config "$HOME/WR-network/coordinator/coordinator.json"
```

Leave the last command running. Enrollment creates a server certificate valid for
one year and a different credential for each sensor. Copy **only** the `sensor-a`
folder to sensor A, `sensor-b` to B, etc., using your trusted file-transfer method.
Keep the **coordinator** folder on the reporting computer. Never commit these files
or send every node's credentials to each sensor. Enrollment will not overwrite an
existing folder. For a different coordinator address, create a new enrollment.

## 3. Start each sensor's reports

On sensor A, put its transferred folder at `~/WR-network/sensor-a`, then run:

```sh
wr node report --config "$HOME/WR-network/sensor-a/node.json"
```

For B/C use their corresponding folder names. To test the same-computer demo,
open a second terminal and use the command above without moving any folders.
Heartbeats report receiver status as **not probed**; they do not open the bladeRF.

Optional GPS: install/configure GPSD locally for your USB puck, identify its device
path, and add `--gps-device /dev/ttyACM0` (replace with the actual path). WR reads
only local GPSD at 127.0.0.1:2947, selects that device, and accepts a complete recent
TPV fix. Missing, partial, invalid, old and disconnected fixes stay explicitly
unavailable. Altitude is included only with a 3D fix. A generic USB GPS provides
position information; it does not establish SDR sample timing. This adapter does
not yet retain/interpolate position history for captures from moving operators.

## 4. Check incoming reports

In another terminal on the reporting computer:

```sh
wr node status --directory "$HOME/WR-network/coordinator"
wr node status --directory "$HOME/WR-network/coordinator" --recordings
```

The first command shows the most recent report from each node, receipt age and
sequence gaps. **Recent** means a report arrived within 15 seconds, not that its
capture time is synchronized or the radio is working. The second shows retained
recording summaries; it does not transfer their raw IQ files. Nothing shows a drone
location or bearing, and `localization_eligible` stays false.

## 5. Try a recording without hardware

On sensor A:

```sh
wr record --simulate --node-id sensor-a --output "$HOME/WR-recordings" --frequency 2444500000 --rate 160000 --seconds 1 --label "SIMULATION: workflow check" --node-config "$HOME/WR-network/sensor-a/node.json"
```

This creates clearly labeled synthetic noise and sends a summary. It does not
transmit, receive real RF or imitate a confirmed drone detection.

## 6. Record from your bladeRF micro

Stop Inspect RF and other programs using this receiver. Configure the correct
FPGA image and USB permissions first. `bladeRF-cli -p` lists connected serials.
The recorder opens only the exact serial you enter, on **RX0**:

```sh
printf 'bladeRF micro serial (32 characters): '
read -r WR_SERIAL
printf 'Center frequency in Hz: '
read -r WR_FREQUENCY
printf 'Describe drone/controller, operating mode, distance and background: '
read -r WR_LABEL
wr record --serial "$WR_SERIAL" --node-id sensor-a --output "$HOME/WR-recordings" --frequency "$WR_FREQUENCY" --rate 15360000 --seconds 1 --label "$WR_LABEL" --node-config "$HOME/WR-network/sensor-a/node.json"
```

Use `--gain` to set manual receive gain (default 30 dB). This command records one
second at one frequency, with no automatic retuning or indefinite recording. It
uses no transmit functions and never flashes firmware. **The physical capture path
still needs validation on your bladeRF micro**; tests so far use fake hardware and
simulation, with actual native-library loading checks.

Each successful capture gets its own directory containing `iq.sigmf-data` and
`iq.sigmf-meta`. The samples are signed interleaved little-endian 16-bit I/Q with
Q11 scale 1/2048. Metadata includes frequency, rate, receiver settings, operator
label, hashes, device counters and read-time brackets for every block. Host times
are not RF arrival timestamps. Device counters are free-running, without a common
UTC mapping. GPS capture-position interpolation is not implemented.

**Limits:** 30 seconds and 512 MiB per recording, and 2 GiB total in the selected
output folder by default. A 1-second 15.36 MSPS recording is about 61 MB. Adjust the
folder limit with `--storage-mib`; nothing automatically deletes recordings. A
block-count/time budget also aborts stalled or repeatedly truncated captures. The
recorder checks free space and quota, serializes writers to the same folder, and
retains failed/interrupted recordings in `.partial` folders. Do not use partial data
as complete captures. Overruns and counter discontinuities remain in metadata;
never treat discontinuous recordings as a continuous timing reference.

## Link outages, retention and trust

- Every report uses certificate-verified HTTPS plus a per-node bearer credential.
  Unenrolled nodes, spoofed node IDs, oversized messages and TDOA-ready claims are rejected.
- Each node keeps at most **128 unsent reports** in `outbox.sqlite3`. At capacity,
  the oldest report is discarded and a cumulative drop count is reported. Keep the
  outbox across restarts; do not share a node's enrollment between multiple computers.
- Heartbeats retry queued reports. After a one-off recording with failed delivery,
  run `wr node report` to resume sending. Raw IQ remains on the sensor even if the
  summary is dropped from the queue. Copy captures separately for analysis.
- The coordinator persists sequence high-water marks and at most 128 recent reports
  per node. Duplicates and older sequences cannot overwrite newer state. Sequence
  gaps count missing sequence numbers, not RF sample loss. Enrollment is capped at
  32 nodes, messages at 16 KiB, and simultaneous request workers at 16.
- Preserve coordinator/outbox databases for duplicate protection. After losing one,
  create a new enrollment with new node IDs rather than silently resetting sequence
  numbers. Remove a node from coordinator.json and restart to revoke it.
- Report metadata and GPS positions are stored locally. Enrollment keys and captures
  stay out of Git. Operator labels are unverified notes, not automatic training truth.
  There is no automatic retraining, persistent IQ capture in normal monitoring,
  remote radio-control endpoint, or IQ streaming over the network.

## Before field collection

First validate one micro recording against a known signal and check counters,
overruns, file hashes, gain/clipping and disk throughput. Collect background-only
and controller-only runs alongside known drone runs, with flight logs and antenna
positions. For TDOA, a separate two-radio timing experiment must establish offset,
drift and sample-clock calibration before a moving multi-node trial.

## Receiver health and the Monitor panel

To query an explicitly selected bladeRF micro alongside heartbeats, add
`--serial YOUR_32_CHARACTER_SERIAL` to `wr node report --config /path/to/node.json`.
This bounded read-only query checks whether the device responds and its FPGA is
configured; it does not tune, stream, transmit, or establish RF reception. A busy,
disconnected or inaccessible receiver is reported as unavailable. Omit `--serial`
to leave the receiver explicitly unprobed. Stop other receiver users during the
initial capture trial.

On the reporting computer, opt in to showing local telemetry:

```sh
WAVERAIDER_COORDINATOR=/absolute/path/to/nodes/coordinator wr serve --port 8899
```

Open **Monitor → Sensor nodes**. The panel shows last receipt, sender-clock heartbeat
age, receiver query state, GPS fix age, queue loss, sequence gaps, and the eight most
recent retained recording summaries. Simulation is labeled and never added to sample
observation counts. Old or future-dated heartbeats cannot keep a receiver healthy;
GPS fixes older than five seconds are labeled stale. Cross-host age estimates depend
on host clocks and do not establish sample synchronization.

The web process reads only `reports.sqlite3`, never enrollment keys. This opt-in view
shares node metadata with everyone able to reach the existing web GUI. Use the trusted
local network boundary described in the installation guide. In Docker, mount only the
coordinator database directory read-only and set `WAVERAIDER_COORDINATOR` to that mount;
the default bundle does not expose coordinator data.

## Verify a completed capture

```sh
wr verify-recording /absolute/path/to/capture-uuid
```

The verifier checks both stored hashes, file size, sample count and counter/discontinuity
metadata. It reports clipping fraction, RMS/peak sample components, overruns and the
average bytes written per elapsed capture time. It rejects `.partial` recordings.
A verified file is not proof of a known transmitter, adequate sustained disk throughput,
or field readiness. Preserve the JSON output with the field trial notes.

For the first physical trial, record the exact hardware/firmware, frequency, gain,
antennas and known transmitter mode. Verify the capture, inspect the known signal,
then repeat with background-only and controller-only controls. Test a node-link outage,
resume reporting and confirm queued summaries arrive without duplicate entries. Repeat
receiver disconnect/reconnect and longer captures before claiming field readiness.
