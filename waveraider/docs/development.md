# Development and validation

[Back to README](../README.md)

## Setup and checks

From the repository, in a Python 3.9+ virtual environment:

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
git diff --check
```

Put Node.js on `PATH` so the JavaScript renderer tests execute; pytest skips them
if Node is unavailable. Run the complete suite for each change; a prior release
baseline is not a substitute for testing subsequent changes.

## Multi-architecture builds

Wave Raider runs on `linux/amd64` (Intel/AMD, such as NUCs) and `linux/arm64` (ARM,
such as Apple Silicon or a Raspberry Pi 5). NumPy is included in the core install. The
`python:3.11-slim-bookworm` base image and Debian libbladeRF/CLI packages cover both
architectures. Model-specific FPGA/firmware and physical USB access remain deployment setup.

`.github/workflows/multiarch.yml` runs on version tags, pull requests, on demand,
and as part of the validated-release workflow on `master`. For each platform it builds the image on a native Intel/ARM runner, checks the
image's CPU type, runs `wr --help` and the bundled Remote ID sample, serves Monitor and
Settings › About, and checks that `docker stop` finishes promptly with "Wave Raider
stopped." It also runs `tools/smoke_install.py` against the installed package with Docker
networking disabled, tests the offline bundle launcher and persistent volume, then
attaches each bundle as a 30-day Actions artifact. It does not push container images
to an image service. Actions are pinned to commit SHAs.

To assemble a bundle locally after building an image:

```sh
python3 tools/build_bundle.py --image waveraider:amd64 --output dist/waveraider-amd64
```

Use a new output directory. The image content determines its bundle tag.
`packaging/` contains the launchers copied into each download; `SHA256SUMS` detects
corrupt/missing files and `image.json` records image metadata.

To check both types locally with Docker's classic builder (as on this project's Colima
setup, whose VM already had QEMU emulation):

```sh
docker build --platform linux/amd64 -t waveraider:amd64 .
docker build --platform linux/arm64 -t waveraider:arm64 .
docker run --rm --platform linux/amd64 waveraider:amd64 wr --help
docker pull python:3.11-slim-bookworm     # the classic builder relabels the base image; restore it
```

## Download maintenance

**Standing user requirement:** the download button must always provide the last
known-good version of WaveRaider, including all of its runtime dependencies.
The complete policy is in [CLAUDE.md](../CLAUDE.md#standing-requirement-last-known-good-download).

Before replacing the recommended download, verify the same revision passes the
full suite plus the Intel/AMD and ARM64 installed-runtime, offline-startup and
persistence checks. Include newly introduced dependencies in the bundles and test
without network access. Retain the previous working download if any check fails.
Keep the README button and installation instructions aligned with the tested bundles.

**Promotion:** `.github/workflows/release.yml` calls the full test workflow and both
native architecture builds for the same revision. Only after both jobs succeed does
it verify the bundles' internal checksums, required files, source revision and image
architecture, then publish two ZIPs, a manifest and checksums as a new GitHub release.
The download button opens the latest release. Previous releases/assets are retained;
failed tests, builds or uploads leave the previous published download available.
An obsolete queued revision is not promoted. Promotion is restricted to `master`;
PR runs have no publication job or write token. Releases use unique source/run tags
and do not modify existing tags or releases. GitHub CLI stages asset uploads in a draft
before publishing. Repository access is still required for this private project.

Activation requires merging the workflow and a successful first `master` release run.
A local test pass alone does not mean permanent assets have been published. Manual
releases must not mark an unvalidated build as latest and bypass this policy.

## Node and recording validation

`tests/test_node_reporting.py` uses real local TLS sockets to check enrollment,
authentication, hostname/CA verification, bounded queues, deduplication and persistence.
`tests/test_recording.py` checks sample metadata, hashes, quotas, interruptions and
clock discontinuities with simulated/fake sources. `tests/test_node_position.py`
checks GPS fix validity without connecting to a real receiver. The installed-runtime
smoke test exercises enrollment, TLS telemetry and a simulated recording, with
network access disabled except container loopback. It also resolves the native
libbladeRF metadata format without opening hardware.

Before claiming field readiness, follow the [node quickstart](node-quickstart.md)
and distributed-node validation gates with the user's bladeRF micro, actual USB
GPS and chosen IP network. Heartbeat status can optionally query one explicitly selected bladeRF micro
with `--serial`; this establishes device access/FPGA state only, not RF health or
timing lock. `wr verify-recording` checks completed capture integrity and sample
statistics without opening hardware. The optional GUI view reads the local coordinator
database without credentials or write access; delayed heartbeats and old GPS fixes age out. The user requests numbered questions in future coordination.

## Current architecture

Capture source → protocol classifier → survey (entities and findings) → CLI,
terminal UI, browser, or HTML report. Optional reasoning summarizes findings;
it does not establish identities or replace decoder validation.

- `uas_classify.py` and `protocols/uas.py`: Remote ID decoding and presentation.
- `capture.py`, `sources.py`, `engine.py`, `model.py`: shared input and survey model.
- `web.py`: HTTP routes. `monitor.html`/`monitor.py` and `settings.html`/`settings.py`:
  Monitor (`/`) and Settings, fed by a replayed sample. `inspect_rf.js`/`.css`: the
  Inspect RF spectrum view.
- `spectrum.py`, `bladerf_sweep.py`, `demod.py`, `droneid.py`: separate SDR inspection
  and experimental candidate scanning. `bands.py`: the drone band table and view fitting.
- `tools/`: sample generators (`make_uas_sample.py`, `make_negative_control.py`) and
  `tui_smoke.py`. The general-purpose protocol plugins were retired after v0.5.1.

File/replay inputs work for recorded analysis. The `LiveSource` interface exists
but its packet-delivery implementation is incomplete; it is not a working live
Remote ID adapter. See the [roadmap](roadmap.md) for its validation gate.

## Evidence and tests

Bundled demo captures are synthetic. Independent Open Drone ID Wi-Fi fixtures
and expected decoder output have separate [provenance and licensing](../tests/fixtures/opendroneid/PROVENANCE.md).
Keep fixture origin, checksums, pinned reference version, and expected-output
method documented. Avoid circular tests where generator and decoder share the
same mistaken framing.

Tests cover malformed envelopes/packs, unsupported messages/versions, unknown and
invalid values, exact identifier handling, and separate transport namespaces.
Synthetic DroneID tests and negative radio scans do not prove real DroneID
sensitivity. `tests/test_droneid_recorded.py` checks one real recording (WaveShark's
DJI Mini 4K capture, CC BY 4.0, pinned by SHA-256) when `WAVERAIDER_IQ_FIXTURES`
names a directory that holds it; recordings are never committed, and without the
directory those tests skip.

Add real recordings only after reviewing redistribution rights and sensitive
identifiers/locations. Keep personal captures, local review notes, and machine
configuration out of the repository.

## Reproducing DroneID validation

```sh
python tools/fetch_droneid_fixture.py /tmp/wr-iq
WAVERAIDER_IQ_FIXTURES=/tmp/wr-iq python -m pytest tests/test_droneid.py tests/test_droneid_recorded.py tests/test_droneid_scan.py -q
# Optional separate licensed interference corpus; install scipy for this tool:
python tools/validate_droneid_corpus.py /path/to/recorded-iq
```

The Tests workflow installs dev dependencies, verifies Node is available, fetches
the hash-pinned positive fixture, then runs the full suite. Network/download errors
fail the job; they do not silently turn the recorded tests into skips. Local runs
without WAVERAIDER_IQ_FIXTURES still explicitly skip recorded validation. An explicit
but missing or corrupted fixture is an error.

On 2026-09-25, the 13 positive bursts passed at the native offset and with +12/−18 kHz
added. The eight pinned negatives passed nine windows with no candidates (2.151488
seconds total source duration). See the [fixture provenance](../tests/fixtures/droneid/PROVENANCE.md)
for sample rates, hashes, resampling and window policy. These are development checks,
not a held-out field evaluation or evidence of drone absence.

A single-process macOS ARM64 check with Python 3.14, NumPy and a 10,752,000-sample
complex64 zero buffer measured 3.09 seconds / 976 MiB peak RSS before blocking and
3.42 seconds / 216 MiB afterward. This is detector-only, with no radio capture or
web-server concurrency, and is not a NUC/Jetson performance guarantee. Allocator and
NumPy versions affect results. Use a fresh process when comparing peak RSS; zeros
also do not represent the full cost of a populated live capture.

Before field acceptance, measure populated captures, simultaneous UI activity,
end-to-end latency and peak memory on the selected NUC/Jetson and SDR. The capture
backend performs a settling read before its retained read, so 700 ms is retained
IQ duration, not total scan latency. Run known-positive and longer representative
negative sessions, receiver disconnects and repeated scans. No real radio was
exercised by the offline validation above.
