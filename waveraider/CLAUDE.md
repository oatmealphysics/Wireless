# WaveRaider — guide for coding agents

Read `README.md` and `docs/development.md` first. This file adds what an agent needs to
change the web GUI safely. It contains only repository facts; keep personal notes,
captures and machine configuration out of the repository (`.gitignore` lists what stays out).

## The `gui-refresh` branch: scope

The `gui-refresh` branch exists for one job: **change how the GUI looks and behaves**.
Only these paths may change on it:

- `waveraider/monitor.html`, `waveraider/settings.html`, `waveraider/inspect_rf.js`,
  `waveraider/inspect_rf.css`, `waveraider/spectrum_display.js`, `waveraider/brand/*`
- `docs/usage.md`, `docs/branding.md`, `CHANGELOG.md` (under **Unreleased**)
- the GUI tests: `tests/test_monitor.py`, `tests/test_settings.py`, `tests/test_styles.py`,
  `tests/test_navigation.py`, `tests/test_spectrum_ui.py`, `tests/test_inspect_rf.py`,
  `tests/test_spectrum_display.py`

Everything else is out of scope on this branch: the Python package (`web.py`,
`monitor.py`, `settings.py`, `sessions.py` and the rest), `pyproject.toml`, the Dockerfile,
the workflows, this file, and the other docs. `tools/check_gui_scope.sh` holds the same
list and the **GUI scope check** workflow runs it on every push to `gui-refresh` and on the
pull request into `master`; a red check means the change reaches outside the GUI. If a GUI
change genuinely needs a server-side change, stop and describe it in the pull request
instead of making it; the repository owner decides. Do not edit the tests to make a
constraint go away.

`.claude/settings.json` in this repository backs the same list with Claude Code deny rules:
the file tools and the file commands Claude Code recognises in Bash refuse to change the
Python package, packaging, CI, tooling, the non-GUI tests and docs, and this guide, and a
push to `master` or a force push is refused. A refused edit is the boundary working, not a
bug to route around; if the work truly needs that file, say so in the pull request.

## Branching and merging

- The default branch is **`master`** (there is no `main`). Branch from an up-to-date
  `master`, push the branch, open a pull request, and merge after CI is green.
- CI: `.github/workflows/multiarch.yml` runs on pull requests and on pushes to `master`;
  `.github/workflows/gui-scope.yml` runs the scope check described above.
  The multi-arch build makes the Docker image for amd64 and arm64, then serves Monitor
  and Settings and runs the bundled sample. It never publishes an image.
- Do not force-push, delete remote branches or tags, or edit existing releases or tags.
- The version lives in `pyproject.toml` and `CHANGELOG.md`. A GUI change goes under
  **Unreleased** in the changelog; do not bump the version unless asked.

## Standing requirement: last-known-good download

The user requires the **Download WaveRaider** button to always offer the last
known-good WR version with **all runtime dependencies included**. Preserve this
requirement in every future application, dependency, packaging and documentation change.

- Promote a download only after the full test suite and both Intel/AMD and ARM64
  runtime/install checks pass for the same revision. A newer commit alone is not
  a known-good version; failed or incomplete builds must leave the previous download available.
- Ship complete, tested bundles: Python, direct and transitive runtime dependencies,
  native libraries used by supported bundled features, GUI assets, sample data,
  launchers and integrity metadata. Startup must not fetch missing dependencies.
- Keep host prerequisites (currently Docker and physical receiver setup) explicit;
  never silently turn a bundled dependency into an extra operator install step.
- Preserve a downloadable known-good bundle until its tested replacement is available.
  Expiring CI artifacts alone do not satisfy this ongoing availability requirement.
  See [download maintenance notes](docs/development.md#download-maintenance) for the
  current limitation; do not describe this policy as an implemented permanent release service.

## Network-neutral documentation

Describe node connectivity in terms of reachable IP addresses, routing, ports and
HTTPS. Users choose their own network transport and equipment. Keep an operator’s
personal networking equipment and deployment details out of source, documentation,
PR descriptions and release text.

## Operator communication

Number questions to the user (1, 2, 3…) so they can answer each by number.

## Setup and tests

```sh
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[dev]'
export PATH="/path/to/node/bin:$PATH"      # Node.js: the JavaScript renderer tests skip without it
```

Run the whole suite before every commit, with throwaway store paths so nothing touches
the real `~/.waveraider` on your machine:

```sh
export WAVERAIDER_TARGETS=/tmp/wr-test/targets.json
export WAVERAIDER_MONITORING=/tmp/wr-test/monitoring.json
export WAVERAIDER_SESSIONS=/tmp/wr-test/sessions
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
git diff --check
```

To look at the GUI in a browser, serve on a spare port with the same variables set:

```sh
wr serve --port 8913        # then open http://127.0.0.1:8913/
```

Monitor replays the bundled sample; no receiver is needed. **Do not use Inspect RF's
radio controls against real hardware while developing**, and do not change the SDR
modules (`spectrum.py`, `bladerf_sweep.py`, `demod.py`, `droneid.py`) as part of GUI work.

## Where the GUI lives

The server is `waveraider/web.py` (standard-library HTTP, no framework, no templating).
It reads these files from the package directory and serves them as-is:

| File | Served at | Contents |
| --- | --- | --- |
| `waveraider/monitor.html` | `/` | Monitor page: inline `<style>` and inline page script |
| `waveraider/settings.html` | `/settings` | Settings page (Receivers, Monitoring, Watchlist, Data & export, About & activity): inline style and script |
| `waveraider/inspect_rf.css` | `/inspect-rf.css` | Inspect RF spectrum drawer styles |
| `waveraider/inspect_rf.js` | `/inspect-rf.js` | Inspect RF drawer behaviour |
| `waveraider/spectrum_display.js` | `/spectrum-display.js` | Pure functions for the spectrum canvas; unit-tested under Node |

Data for the pages comes from `waveraider/monitor.py` (session, observations, evidence,
watchlist, exports) and `waveraider/settings.py` (monitoring profile, receivers). Session
saving is `waveraider/sessions.py`. A new static file must be listed in
`[tool.setuptools.package-data]` in `pyproject.toml` (the Dockerfile copies the whole
`waveraider/` directory, so nothing else changes there).

## Conventions the tests enforce

- **Colours come from one shared token set** (`tests/test_styles.py`): both pages define
  the same `--…` custom properties; colour declarations use tokens except the few
  documented exceptions; no rule may override an earlier declaration for the same
  selector; retired dashboard selectors and the old palette must not return.
- **Two tabs only**, Monitor and Settings (`tests/test_navigation.py`); `/classic` and
  the retired `/api/...` routes stay gone.
- **Requests that change something** are `POST` with `Content-Type: application/json`
  and a JSON object body. The GET routes that use the radio (`/api/spectrum` with an
  owner, `/api/analyze`, `/api/audio`, `/api/droneid`) send `X-WaveRaider-Request: 1`.
  The server refuses cross-site requests; keep these headers when adding fetches.
- **Behaviour-critical states stay styled**: lifecycle classes (fresh, stale, older
  observations), watchlist matches, banner tints, the drawer. Check
  `tests/test_monitor.py`, `tests/test_settings.py`, `tests/test_spectrum_ui.py` and
  `tests/test_inspect_rf.py` for the element ids and text the tests look for before
  renaming anything.
- The JavaScript tests run `spectrum_display.js` under Node; keep it free of DOM and
  browser globals.

## Wording discipline

The README's capability table is the boundary. UI text must not claim that WaveRaider
detects, identifies or tracks aircraft, receives live Remote ID, or that a quiet
receiver means no drone is present. Monitor shows a **replayed sample**; Inspect RF shows
spectrum, not identity. Ages and gaps describe replay updates on this computer. When a
change touches user-visible behaviour, update `docs/usage.md` and the changelog in the
same wording style.
