# Drone Project — Session Summary
**Date:** 2026-10-05 · **Repo:** `andeds-agent/elrs-sdr-transfer` · **Goal:** a webui showing every drone around me, its protocol, and its identifiers.

---

## TL;DR

Five things got done today, all committed/pushed to `main`:

| # | Work | Result |
|---|------|--------|
| 1 | Built a **2.4 GHz power scanner** (`--scan` mode) + B200 RX-chain arm fixes | Find *where* the confirmed 2.4 GHz signal is, before decoding |
| 2 | **Restructured the repo** into a clean `lib / tools / docs` layout; deduped the ELRS decode | 1213 lines removed; 5-way CRC/FHSS/OTA duplication collapsed into `elrs_sdr/core/` |
| 3 | **Unpacked + deduped the reference zips** (35 archives → 28 clean repos) | Full reference set local; 1 source (ardupilot) found corrupt |
| 4 | **Merged the intern's `dronewatch`** into `elrs_sdr` as one package | Unified sensor-fusion dashboard (Remote ID + SDR scan + DJI), identity-keyed |
| 5 | **Fixed a real LoRa bug** the intern found (verified independently) | `detect_sync_word` matched-filter conjugation was broken |

Final state: **`elrs_sdr` selftest 133/133 PASS · C++ decode 18/18 PASS · dashboard serves live.**

---

## 1. The 2.4 GHz scanner (`--scan`)

**Context:** The Tinyhawk III Plus (analog RTF, possibly ELRS-upgraded) is *confirmed* broadcasting on 2.4 GHz, but the last live capture showed **all-zero IQ** (`pk=0.0000 mean=0.0000` across all channels). So the open question was: *is the signal even reaching our capture, and on which channel?*

**What was built** (in `cpp/elrs_intercept.cpp`):
- A `--scan` mode: sweeps all 80 ISM2G4 channels (2400.4–2479.4 MHz), measures per-channel power (mean + peak magnitude) over a configurable dwell, and prints a ranked table with dB-over-floor. No demod — just "where's the energy."
- **B200 RX-chain arm fixes** (the classic "all-zero" cures): `set_rx_antenna("RX2")`, `set_dc_offset_mode(ENABLE)`, `set_iq_balance_mode(ENABLE)` — these are the three things that most often leave a B205/B210 producing flat-zero IQ.

**Status:** Built + syntax-checked, pushed. **Not yet run on the laptop** — this is the immediate next test (see "Next up").

```
./elrs_intercept --scan type=b200 40 200     # gain=40, dwell=200 ms/channel
```

---

## 2. Repo restructure + dedup

**Context:** The repo had grown into overlapping layers — three parallel ELRS implementations (C++ `cpp/`, Python `elrs_sdr/` package, and `tools/` scripts), with the CRC-14/FHSS/OTA logic in **5 separate copies**.

**What was done** (commit `c7bad5b`, 23 files, +92/−1213):
- `elrs_sdr/crc.py, ota.py, fhss.py, lora.py` → moved into **`elrs_sdr/core/`** as the single source of truth.
- **Deleted** the 3 superseded `tools/elrs_*.py` scripts, the 34 MB research tarball + 107 KB tarball, a stray launcher script, and an unreferenced transcript.
- **Kept** `tools/lora_ref.py` (the C++ cross-validation depends on it), the ODID/BLE/Wi-Fi tools, `apps/`, and all vendored references (`DroneSecurity/`, `gr-ieee802-11/`, `drone_refs/`).
- Added `.gitignore` for `*.iq / *.jsonl / *.tgz` + build artifacts.
- Added a "Repository layout" section to the README.

Resulting layout:
```
elrs_sdr/            the Python package
  core/              ELRS decode (single source of truth)
  radio.py dronscan.py wifi.py positivedet.py webui.py cli.py selftest.py ...
cpp/                 live UHD tool (C++)
tools/               standalone scripts (lora_ref, odr_*, drone_scan, ...)
docs/  apps/         docs + DJI adapter
DroneSecurity/ gr-ieee802-11/ drone_refs/   vendored refs (untouched)
```
All moves were `git mv` (history preserved). Verified: selftest + C++ 18/18 + CLI all green.

---

## 3. Reference zips — unpacked + deduped

**Context:** The upload store (`/opt/data/Drone_Project/references`, reached via the dashboard files API) held 35 archive files with misleading extensions.

**What was found:** the extensions lied. They're:
- 4 raw `.zip`
- 17 `.zip.tgz` = **plain tar** → one `.zip`
- 14 `.zip.tgz.tar.xz` = **plain tar** → plain tar → one `.zip` (two tar layers, *no* actual xz/gzip)

**What was done:** wrote a recursive-unwrap pipeline (auto-detects each tar layer, peels to the inner zip, unpacks), fetched all 35 via the files API, unpacked, and deduped by base repo name (kept the rawest source, dropped the 6 redundant xz-wrapped copies).

**Result:** **28 distinct repos, 428 MB**, at `refwork/deduped/`, with a `manifest.json`.

**⚠️ One source is corrupt:** `ardupilot.zip.tgz` (76 MB) is **truncated on the server** — its inner zip is a `BadZipFile`. Confirmed the download is byte-complete (matches server size exactly), so it was broken at the original upload. **Needs a fresh re-upload.** Two repos from the intended 30-set were never uploaded: **`inav`** and **`MissionPlanner`**.

---

## 4. Merged the intern's `dronewatch` (the big one)

**Context:** The intern built a standalone `dronewatch` package — a rewrite of `elrs-sdr-transfer` around the single goal ("every drone around me, its protocol, its identifiers"), carrying the validated decode cores plus a **new sensor-fusion layer + unified dashboard** and two bug fixes. It arrived via a tarball in the Files tab (git push was blocked by laptop permissions).

**Decision (operator):** *Merge* into `elrs_sdr` as **one package** — no duplicate decode, re-point at `elrs_sdr/core`, back-port the real fixes.

**What was done** (commit `64e3e94`, 9 files, +1253/−747):

New modules:
- **`opendroneid.py`** — Remote ID (ASTM F3411) decoder + synthesizer. The **identity jackpot**: serial, operator ID, operator location, position, speed. Round-trip tested.
- **`fusion.py`** — sensor-fusion. Each source emits normalised events; one `TrackTable` merges them into per-drone **tracks keyed by identity** (sticky-`confirmed`, signature fallback so unidentified signals accumulate as one row instead of churning). Three sources: `ReplaySource` (synthetic), `SdrScanSource` (presence+protocol), `DjiDroneIdSource` (reads a DJI receiver's JSON lines).
- **`log.py`** — centralised logging.

Changed:
- **`webui.py`** → the unified **DroneWatch** dashboard: one identity-keyed table (status, kind, protocol, serial, operator, position, altitude, speed, freq, RSSI), fed by the fusion `TrackTable`. Replaced the old waterfall UI.
- **`cli.py`** → `webui` now wires the fusion sources (`--replay / --sdr / --dji / --window`); added global `-v/-q` + `log.setup()`. All 13 prior subcommands preserved.
- **`radio.py`** → stderr spew → `log.debug/warning` (verified the *only* diff was logging).
- **`core/lora.py`** → the conjugation bug fix (see #5).
- **`selftest.py`** → added ODID round-trip + fusion-merge tests; re-pointed dashboard UI checks at the new UI.

**Verified:** `elrs_sdr.selftest` **133/133 PASS** · C++ **18/18** · `webui --replay` live-serves `/api/state` with confirmed identity tracks · `CLI --help` shows all subcommands + `-v/-q`.

---

## 5. The LoRa conjugation bug (found by the intern, verified by me)

**The claim:** `detect_sync_word` passed `np.conj(sync_word)` to `np.correlate`, which *double-conjugated* the matched-filter kernel — for a chirp that correlates an up-chirp against a down-chirp and never dechirps, so no peak forms.

**My independent verification** (built my own signal from scratch, not the intern's test):
| Correlation | Peak location | Peak-to-noise |
|---|---|---|
| old `correlate(sig, conj(sw))` | −1 off, **2.0×** | buried in noise — **no detection** |
| fixed `correlate(sig, sw)` | exact, **44.4×** | clean peak at the true chirp start |

**Verdict:** the fix is correct. Back-ported into `elrs_sdr/core/lora.py` with a comment. This is a real improvement to the decoder, not just the intern's copy.

---

## The architecture now (what the goal looks like)

No single receiver sees every drone, so **three sources → one fusion layer → one dashboard**:

| Source | Receives | Gives identity? |
|---|---|---|
| **Remote ID** (`opendroneid`) | ASTM F3411 over BLE + Wi-Fi beacon/NAN | **Yes** — serial, operator, position |
| **SDR scan** (`dronescan`+`positivedet`) | band sweep on the USRP | Mostly no — presence + **protocol** |
| **DJI DroneID** (`fusion.DjiDroneIdSource`) | DJI OcuSync, SDR-only | **Yes** — DJI serial + operator/drone position |

`python -m elrs_sdr webui` renders the merged, identity-keyed table.

---

## Honest status / what's still open

1. **Zero-IQ capture is still the gate** for live `--sdr` / `--dji` data. The `--scan` mode + B200 arm fixes are built and pushed but **not yet run on the laptop**. This is the blocker for any real (non-synthetic) row in the dashboard.
2. **Identity paths, SDR-only reality:** DJI DroneID is the best SDR-native identity win. Standards Remote ID over the SDR alone is a large DSP build (Wi-Fi OFDM unvalidated; no BLE receiver in the repo) — the decoder is ready but needs a monitor-mode NIC + BLE adapter to feed it live bytes.
3. **Reference set is 27/30:** ardupilot is corrupt (re-upload needed); inav + MissionPlanner were never uploaded.
4. **Git push from the laptop is blocked** (your user lacks maintainer rights on the repo; the intern's work came via the Files API instead). To fix: add your user as a **Maintainer** on `andeds-agent/elrs-sdr-transfer`.

## Next up (recommended order)

1. **Run the scanner** on the B205 to locate the confirmed 2.4 GHz signal and confirm the RF path is alive:
   `./elrs_intercept --scan type=b200 40 200`
2. **Wire the DJI DroneID receiver** as the first *real* identity source:
   `python -m elrs_sdr webui --dji "python3 apps/droneid_json_rx.py --device type=b200"`
3. **Re-upload ardupilot** (+ optionally inav/MissionPlanner) to complete the reference set.
4. **Add your user as Maintainer** so git works from the laptop going forward.

---

## Commits pushed today
- `c7bad5b` — Restructure: clean lib/tools/docs layout, dedup the ELRS decode
- `64e3e94` — Merge dronewatch: unified sensor-fusion dashboard (intern's rewrite)
- (earlier in the window: `0f29cd2` add `--scan` mode + B200 RX-chain arm fixes)
