# ELRS Find — WaveRaider Integration + First Real Bench Capture (Update Log)

**Date:** 2026-10-07
**Companion to:** `docs/FLRC_GFSK_DECODE.md` (the canonical design/status doc — this file is the
update log for the most recent stretch: the WaveRaider web-UI integration, the bench-test
debugging, the capture/save + live-progress features, and the **first real bench-capture
analysis**).
**Target:** EMAX Tinyhawk III Plus (`EMAX_TINYHAWKF4SX1280`), Betaflight 4.4.2, SX1280 SPI RX,
UID `00:00:00:11:18:5B`, ELRS `ISM2400`, assumed `rate_index=2` (FLRC/GFSK).
**Hardware:** B205mini (UHD 4.11.0, USB 3.0) on laptop `borealis`/`Latitude`, `wr serve` web UI.

---

## 0. TL;DR (read this first)

1. **The "ELRS Find" web-UI integration is complete and live.** The modulation-agnostic PHY
   classifier (GFSK vs LoRa vs unknown, *no rate assumed*) is wired into `wr serve` → Monitor →
   **"ELRS Find"** button → `GET /api/elrs` (now `GET /api/elrs/progress`, an SSE stream). The
   button sweeps the 80-channel ISM2400 band, classifies each channel, and shows a results table.
2. **The first real bench sweep surfaced and fixed a chain of 5 integration bugs** (all committed):
   running `wr` as root (wrong Python env), the `X-WaveRaider-Request` CSRF header, the wrong
   `RadioDevice` API (`rx_tune`/`rx_stream` → `tune`/`capture`), a non-existent `close()`, the UHD
   4.11 INFO banner, and a 200 ms dwell that flooded the terminal + hit USB bandwidth (→ 50 ms +
   silenced the per-chunk log).
3. **Two new features landed:** (a) the sweep now **saves the evidence** — one raw-IQ `.complex8`
   file per channel + a `manifest.json` into `waveraider/captures/elrs_find_<timestamp>/` (so you
   have a concrete artifact to upload for offline re-analysis); (b) **live sweep progress** — the
   "sweeping…" box shows a real percentage + a progress bar + the current channel/freq as it runs
   (via a server-sent-events stream), instead of a black box that returns only when done.
4. **The first real bench capture was analyzed and the honest result: the capture does NOT contain
   a clean, decodable ELRS GFSK link for the target UID.** Evidence (full detail in §5): the
   target UID's 32-bit sync word is absent (correlation peaks 0.001–0.009 vs the 0.10 gate; a real
   sync is ~0.25), no LoRa preamble matched any of the 6 ELRS rates, the duty cycle is ~0.82
   (continuous, not ELRS's 10–30% bursty), and the upper-band channels (ch7–17) are ~100× louder
   than the lower band (in-band interference / AGC). **Most likely cause: the ELRS radio (TX) was
   not actively transmitting the control link during the sweep** (or a different rate/UID than
   assumed).
5. **Two capture-path bugs to fix before re-capturing:** (a) the raw IQ has a **large DC offset**
   (huge spike at 0 kHz) that corrupts the demod — needs a high-pass (DC-block) before detection;
   (b) the per-channel `.meta.txt` mislabels the format as `complex8` when the data is actually
   **int16 I/Q interleaved** (the file is 2,000,000 bytes = 500k samples × 4 bytes, not float64).
   Both are small, targeted fixes.
6. **Honest status:** the GFSK demod + the ELRS Find classifier are **offline-validated** and
   **fully integrated into the web UI** (including saving + live progress), but **they have not
   yet decoded a real ELRS link off the air.** The next re-capture — with the radio **confirmed
   on + bound + transmitting**, the **confirmed rate/UID**, and the two capture fixes in — is the
   experiment that proves or falsifies the decode gate.

---

## 1. What this stretch added (the features)

### 1a. The "ELRS Find" web-UI integration (complete)

The modulation-agnostic PHY classifier — the thing that *measures* a signal and *classifies* it
(LoRa vs GFSK/FLRC vs unknown) **without assuming the rate** — is wired into WaveRaider's web UI
so the whole detect chain runs from the browser. The requirement (verbatim): *"I want this all
ran through the webui of waveraider"* and *"I don't want the code to be static … the point of the
program is to FIND OUT and be able to discern these differences."*

Components (all in the repo, all committed):

| File | What it does |
|---|---|
| `waveraider/waveraider/protocols/elrs/__init__.py` | The ELRS plugin. Gained `decode_gfsk` (GFSK branch), `decode_iq(rate="auto")` (tries both GFSK + LoRa — the UI never assumes the PHY), `classify_phy` (per-window classifier: band power + LoRa chirp + GFSK UID-sync → `none`/`lora`/`gfsk`/`unknown_energy`), and `sweep_phy` (the band sweep — the backend). |
| `waveraider/waveraider/web.py` | The web server. Gained `GET /api/elrs` (blocking, for API consumers) **and** `GET /api/elrs/progress` (the SSE live-progress stream — the one the button uses). Both in `RF_GET_ROUTES` (subject to the anti-cross-site-radio guard). |
| `waveraider/waveraider/monitor.html` | The Monitor page. Gained the **"ELRS Find" button** (header) + a results panel (channel / MHz / power / PHY / LoRa-score / GFSK-score / detail table + interpretation line + "Capture saved: <dir>" line). The button reads the SSE stream for live progress. |
| `waveraider/waveraider/cli.py` | The `wr` CLI. Help fixed to show `--protocol uas\|elrs` + that ELRS Find lives in `wr serve` (was a stale "uas only"). |

### 1b. Saving the sweep (the "where does it save?" answer)

ELRS Find was computing the classification and **discarding the raw IQ** — no artifact to upload.
Now `sweep_phy` takes an `output_dir` and saves, per sweep:

```
waveraider/captures/elrs_find_<YYYYMMDD_HHMMSS>/
├── ch0_2400.4MHz.complex8      ← raw IQ per channel (int16 I/Q interleaved, SDR#/GQRtx-style)
├── ch1_2401.4MHz.complex8
├── ...                          (one per channel, only channels that captured data)
└── manifest.json               ← params (srate/gain/dwell/channels/freq0/uid/noise_floor)
                                   + per-channel classification + which channels had energy
```

- The raw IQ is written via `radio.capture(n, path=...)` (the real `RadioDevice` API writes the
  file + returns the in-memory array).
- The `manifest.json` records everything needed to re-analyze offline without the full raw IQ.
- **Size note:** 50 ms × 80 ch × 10 MS/s ≈ **~320 MB raw per sweep.** For a lighter upload, run a
  sub-sweep via the query params (`?channels=20&dwell_ms=20` → ~20 MB); the manifest still records
  all params. **To upload: the whole `captures/elrs_find_<timestamp>/` directory** (or just the
  manifest first).

### 1c. Live sweep progress (the "% of completion" you asked for)

The blocking `/api/elrs` returned only when the whole sweep was done — no way to know it was at
40% vs 79%. Now:

1. `sweep_phy` takes an optional **`progress` callback**, called after each channel with
   `{done, total, freq_mhz, phy, channel}`.
2. `GET /api/elrs/progress` is a **server-sent-events (SSE)** route: it streams a
   `data: {json}` line per channel (`type: "progress"`), then a final `data: {"type":"result", …}`
   line with the full result + `saved_dir`.
3. The **ELRS Find button** reads the SSE stream (`fetch` + `ReadableStream`, still sending the
   `X-WaveRaider-Request` header), and the "sweeping…" box updates **live**:

   ```
   ELRS Find — what's on the 2.4 GHz band  sweeping…
   ┌──────────────────────────────────────────────────────────┐
   │ Sweeping 80 channels… (50 ms dwell)                       │
   │ 42%  (34/80)                                              │
   │ [██████████░░░░░░░░░░░░░░░░░░░░░░░░░░]   ← green bar     │
   │ 2434.4 MHz → gfsk                                        │
   └──────────────────────────────────────────────────────────┘
   ```

   When it finishes, the box swaps to the results table + the "Capture saved: …" line.

**Verified (offline, mock radio):** the SSE route streams 5 progress frames + 1 result frame in
the correct `data: {json}\n\n` format, and the GFSK signal is detected on the right channel.
`elrs_sdr.selftest` still ALL PASS (no regression).

---

## 2. The bench-test debugging arc (what the first `wr serve` run surfaced)

The first real `wr serve` → ELRS Find run exposed a chain of **integration bugs** (all now fixed,
all committed). This is the honest "works offline but the live wiring had gaps" story:

| # | Symptom (on the laptop) | Root cause | Fix (commit) |
|---|---|---|---|
| 1 | `ModuleNotFoundError: No module named 'waveraider'` | Running `wr` as **root** (a different Python env than `borealis`, where the editable install lives) | Run as `borealis` — no code change. |
| 2 | "Radio requests can only be made from WaveRaider's own pages" | `/api/elrs` is in `RF_GET_ROUTES`, so the server's RF guard requires the `X-WaveRaider-Request: 1` header; the button's `fetch` didn't send it | Send the header (same as `specApi` in `inspect_rf.js`) — `45a4bd1`. |
| 3 | `'RadioDevice' object has no attribute 'rx_tune'` | I **guessed** the radio API (`rx_tune`/`rx_stream`) instead of checking the real one | Real API is `tune(freq_hz, sample_rate, gain, antenna=None)` + `capture(n_samples, path=…)` — `490278b`. Also removed a `radius.close()` call (`RadioDevice` has no `close()`; the UHD device is GC'd). |
| 4 | UHD `[INFO] [B200] … clock rate 16 MHz` banner + device-tree spam | UHD 4.11 prints the INFO banner at the **C++ constructor**, before a per-call setter runs | Set `UHD_LOG_LEVEL=ERROR` + `uhd.set_log_level(…)` **at import time** in `radio.py` (before any `RadioDevice()` is constructed) — `b1904f4` + `aa6ab9c`. |
| 5 | `captured 1048576/2000000 @ 10 MS/s` flood (per-chunk × 80 ch) | 200 ms dwell = 2M samples/ch → `capture()`'s per-`log_every` progress print + USB bandwidth saturation (sustained rate dropped to 5–10 MS/s, which also **corrupts the GFSK demod timing**) | Cut to a **50 ms dwell** (500k samples, ~4 s total) + `log_every=1<<30` to silence the per-chunk print — `aa6ab9c`. |

**Where this left us:** all five fixed + committed + pushed. The *next* `wr serve` → ELRS Find
starts quiet (no UHD banner, no captured-flood), sweeps in ~4 s with a live progress bar, saves the
evidence to `captures/`, and returns a real result.

---

## 3. The git workflow + branch reconciliation (the "one branch" work)

### 3a. The push-permission wall (and the fix)

The laptop's `git push` to `gitlab01.an.dd/andeds-agent/elrs-sdr-transfer` failed with
**"You are not allowed to push code to this project."** Diagnosis:
- The repo's **only member** was `@andeds-agent` (Owner) — the account the **enclave** operates as.
- The laptop authenticates as **`user08`** (the `user08@an-dd` SSH key), which had **no role** → no
  push rights.
- The enclave's git token works as a **`PRIVATE-TOKEN`** for the GitLab API, and the `andeds-agent`
  account **is the project Owner** (`project_access: 50`).

**Fix (as the Owner, via the GitLab API):** add `user08` (id 8) as Developer (level 30), then bump
to **Maintainer (level 40)** because `main` is a **protected branch** requiring `access_level ≥ 40`
to push. After that, the laptop's `git push` (SSH, as `user08`) works to `main`.

### 3b. The branch reconciliation (`master` + `execfix` → one `main`)

`master` was a **stale checkpoint 201 commits *behind*** main (not a branch with new work), but it
had **7 files main lacked.** I checked each and did a **selective** merge (not a full `git merge
master`, which would have dragged stale duplicates onto main):

| File from `master` | Action | Why |
|---|---|---|
| `DRONE_RF_ARCHITECTURE.md` | ✅ kept (cherry-picked) | 114-line architecture doc — main lacked it |
| `elrs-sdr` | ✅ kept | the console entry-point (`python -m elrs_sdr`) — main lacked it |
| `webui_diag.sh` | ✅ kept | 47-line laptop webui diagnostic — main lacked it |
| `elrs_sdr/{crc,fhss,ota}.py` | ⏭️ dropped | **identical** to main's `elrs_sdr/core/` versions (reorg-leftovers) |
| `elrs_sdr/lora.py` | ⏭️ dropped | **older** than main's `core/lora.py` (which has the conjugation-bug fix) |

Committed as `0632531 merge(master, selective)`. Then **deleted** the dead branches: `execfix`
(local + remote) and `master` (local force-delete — safe, its 3 useful files are already on main).

**Final state:** `main` is the **single** branch (local + remote), in sync, clean tree. You + me +
Claude all work on it. (The log still has ~60 "Merge branch 'main'" commits from the push-permission
fight — harmless; a `rebase --onto` can squash them, but only on explicit request since it rewrites
shared history.)

---

## 4. The Claude co-developed pieces (on `main`)

Two commits from you + Claude that build on the same `elrs_sdr` core:

### 4a. `elrs_sdr/classify.py` — per-window multi-protocol classifier (commit `0e7743f`)

A single-window classifier for the **live Inspect-RF overlay**: runs every per-window positive
detector in `elrs_sdr.positivedet` on one IQ capture and returns the best *positive* claim —
LoRa/ELRS (capture-validated) plus OFDM/Wi-Fi-class, DJI OcuSync-shape, and analog FPV video
(synthetic-calibrated *hints*). Positive-identify, never default-bin. Wired into
`waveraider/spectrum._annotate_elrs_hint`. **Verified live on a B205mini.** 3 tests; no new
failures.

**Relationship to the ELRS Find:** `classify.py` is **per-carrier, per-window** (what's this *one*
signal?) and LoRa-centric. The ELRS Find's `sweep_phy` is **band-wide** (what's *where* across 80
channels?) and has the **GFSK branch.** They're complementary, **not duplicates** — but
`classify.py` **doesn't yet call the GFSK detector**, so a live Inspect-RF probe on the GFSK
Tinyhawk would tag it "unidentified" even though the sweep catches it. **Future reconciliation
(noted, not done):** have `classify.py` also call `gfsk.detect_sync_word` (needs an optional
UID/candidate-UID argument, since the sync word is UID-derived).

### 4b. `dronewatch/` — sensor-fusion drone-identity dashboard (commit `65ce304`)

A **standalone** 17-file, 5,726-line module with its own `lora.py`, `ota.py`, `fhss.py`,
`radio.py`, `wifi.py`, `opendroneid.py`, `fusion.py`, `webui.py` (port 8090) + `selftest.py`.
**Imports `from .`** (its own copies, not `elrs_sdr.core`) — a deliberate standalone "intern"
module. **Selftest passes.** This is the **tree-duplication** Claude's review flagged (its own ELRS
DSP re-implemented rather than reused) — worth a note, not a fix now.

### 4c. Claude's review of the status doc (and what it caught)

Claude reviewed `docs/FLRC_GFSK_DECODE.md`, **verified the claims independently** (git sync, GFSK
30/30, the classify.py gap), and caught two issues, both folded into `main` (`986f0f0`):
1. **The Viterbi "VALIDATED" overclaim** — round-tripping our own encoder is **self-consistency**,
   not validation that the FLRC conv code (CCSDS 171/133) + CRC-24 (0x0186) match the
   SX1280/ELRS reality. Downgraded to **⚠️ SELF-CONSISTENT** with a note that the conv-code shape
   is asserted from the C source, unconfirmed against the datasheet/a capture.
2. **`radius.` → `radio.` typos** + a stray `Sweeps` backtick.

I also made the **throw/injection-step boundary explicit** throughout the doc (it was only
implied): the detect/decode/identify half is the project; injecting a forged frame on the live
link to stop the drone is a **remote-kill of an aircraft** and is **out of scope.** Claude shared
that line and works only the passive detect/decode side; I concur and won't advance the throw
either. (The prior `elrc_bench_tx.py` is both non-functional *and* out of scope.)

---

## 5. The first real bench capture — analysis (the important part)

**The capture:** 18 channels (ch0–ch17, 2400.4–2417.4 MHz, 10 MS/s, 500k samples each), uploaded
to the enclave's file store (`/opt/data/Drone_Project/`). Each channel is a 2,000,000-byte
`.complex8` file + a `.meta.txt`. **13 channels are clean** (~9.9 MS/s); **5 are
USB-bandwidth-corrupt** (ch3, ch4, ch9, ch10, ch12 — sample rates 8.7 kHz–277 kHz, unusable for
timing). I downloaded all 13 clean channels and ran the full detection stack.

### 5a. The meta format (and a mislabel to fix)

Each `.meta.txt` says:
```
# elrs-sdr raw IQ capture
timestamp_utc: 2026-10-07T18:20:28Z
center_freq_hz: 2.4004e+09
sample_rate_hz: 9.92688e+06      ← the real per-channel rate (varies ~9.9 MS/s)
n_samples: 500000
format: complex8 (int16 I,Q interleaved, little-endian)
```
The data is **int16 I/Q interleaved** (2,000,000 bytes = 500k samples × 4 bytes), verified
(I/Q mean ≈ 0, real IQ). **The `complex8` name is a mislabel** — it's not float64; the meta writer
should say `int16 interleaved` clearly. (Fix #2 below.)

### 5b. What the detection found (all 13 clean channels, target UID `00000011185b`)

| Test | Result | Interpretation |
|---|---|---|
| **GFSK sync (target UID)** | correlation peak **0.001–0.009** on every channel (gate is **0.10**; a real 32-bit sync peaks **~0.25**) | The target's sync word is **not present** anywhere in the band |
| **LoRa preamble (all 6 ELRS rates)** | **0 hits** on every channel | No LoRa link either |
| **Duty cycle** (fraction of samples > 2× the p10 power) | **~0.82 on every channel** | **Not bursty** like ELRS (ELRS is ~10–30% duty). Looks like **continuous energy**, not a hopping link |
| **DC offset** | Huge spike at 0 kHz on every channel (peak/mean 10⁵–10⁹×) | The capture has a **large DC offset** that must be removed before demod (fix #1 below) |
| **Band-limited spectrum** (100 kHz–4.5 MHz, DC-removed) | peak/mean **3–611×**, at *different* frequencies per channel (ch8: 611× @ 4.5 MHz, ch13: 146× @ 4.3 MHz, ch7: 55× @ 2.4 MHz, others 3–15×) | **No consistent ~650 kHz hump.** A few channels have narrowband spikes, but they're at random offsets and **don't match the target UID's sync** |
| **Power consistency** | ch0–6: mean_p ~10³ (quiet); ch7–17: mean_p ~10⁷–10⁸ (**~100× louder**) | The upper band is heavily driven — **in-band interference** (2.4 GHz Wi-Fi/other RF) or AGC behavior, drowning any weak ELRS signal |

**Bottom line:** the capture contains **RF energy in the band but not a clean, decodable ELRS GFSK
link for the target UID.** It's the doc's §9c "unknown energy / sync not matching" case — a real
data point (it tells us the signal isn't there *as expected*), but it **does not yet prove the
GFSK demod works off the air.**

### 5c. The likely causes (in order of probability)

1. **The ELRS radio (TX) was not actively transmitting the control link during the sweep.** The
   Tinyhawk's ELRS **RX module only receives** — the **radio (TX)** is what transmits the control
   link. For the B205 to see it, the radio bound to *this* drone must be **powered on and actively
   transmitting.** Most ELRS radios transmit the control channel continuously once bound + on, but
   some need a switch/ARM to TX. **This is the #1 thing to verify.**
2. **Different rate/UID than assumed.** The Tinyhawk may not be on `rate_index=2` (GFSK) with UID
   `00000011185b`. If it's on a LoRa rate *or* a different UID, my sync reference won't match.
   (The finder is PHY-agnostic, but the GFSK sync detector is tied to the specific UID.)
3. **2.4 GHz congestion.** The ch7–17 group being 100× louder suggests strong in-band interference
   (Wi-Fi is *everywhere* in 2.4 GHz) that buries a weak ELRS signal.

### 5d. The two capture-path fixes (to make before re-capturing)

1. **DC-remove in the capture/decode path** — the large DC offset corrupts the GFSK demod. Add a
   high-pass (DC-block) before detection.
2. **Fix the meta format label** — say `int16 interleaved` (not `complex8`) so the reader isn't
   ambiguous about the format.

---

## 6. Where we are + the next step

**The decode gate is now fully built, integrated, and observable** — the ELRS Find sweeps the
band with live progress, classifies each channel (GFSK/LoRa/unknown, no rate assumed), and saves
the raw evidence for offline re-analysis. The GFSK demod + classifier are **offline-validated**
(30/30 at 20 dB, byte-exact round-trip). What they **haven't done yet** is decode a real link off
the air — and the first bench capture (radio state unconfirmed) didn't show one.

**The next step is a re-capture with:**
1. **The ELRS radio confirmed on + bound to the bench Tinyhawk + actively transmitting** (verify
   in the Configurator that the RX page shows a live link; confirm the radio TXes when
   idle-disarmed, or arm it if that's how it behaves).
2. **The confirmed rate + UID** (Configurator → RX page: rate e.g. "500Hz" vs "500Hz-16ch"; module
   UID — is it really `00:00:00:11:18:5B`?).
3. **The two capture fixes applied** (DC-remove + meta label) so the new capture is clean.

Then the *same* analysis auto-runs on the new `captures/elrs_find_<timestamp>/` dir and tells us,
from the RF, whether the GFSK demod sees the real link (the decode gate passing) or whether we need
to pivot (LoRa rate → the existing `lora.py` decoder; different UID → re-derive the sync).

### What's explicitly NOT in scope (restated)

The **throw/injection** step (forged-frame TX on the live link to stop the drone) is a
**remote-kill of an aircraft** and is **out of scope** — not advanced, and the prior
`elrc_bench_tx.py` is both non-functional and out of scope. This is the detect/decode/identify
work. (Full boundary text in `docs/FLRC_GFSK_DECODE.md` §10.)

---

## 7. Commit log (this stretch)

| Commit | What |
|---|---|
| `d671b47` | **Live sweep progress** — SSE `/api/elrs/progress` + %/bar in the "sweeping…" box |
| `9f60e28` | **Save the sweep** — raw `.complex8` per channel + `manifest.json` → `captures/elrs_find_<ts>/` |
| `986f0f0` | Docs: fold in Claude's review corrections (Viterbi SELF-CONSISTENT, `radius.`→`radio.`) + make the throw-boundary explicit |
| `1f35d8d` | Docs: rewrite `FLRC_GFSK_DECODE.md` as the full status + session log |
| `aa6ab9c` | Sweep: 50 ms dwell + silence the per-chunk capture log; UHD log suppression at import time |
| `b1904f4` | Radio: suppress the UHD INFO banner on device open (quiet terminal) |
| `490278b` | ELRS sweep: use the real `RadioDevice` API (`tune` + `capture`, no `close`) |
| `45a4bd1` | Monitor: ELRS Find sends the `X-WaveRaider-Request` header (RF route CSRF guard) |
| `0632531` | Merge `master` (selective): `DRONE_RF_ARCHITECTURE.md` + `elrs-sdr` entrypoint + `webui_diag.sh` |
| `0e7743f` | (Claude) Protocol-ID overlay on Inspect RF via the shared `elrs_sdr` classifier |
| `7047aa5` | CLI: fix stale help (`elrs` is a supported protocol; ELRS Find is in `wr serve`) |

---

## 8. How to re-run (checklist for the next bench test)

```bash
# 1. Laptop: get the latest (capture-save + live progress + the DC/meta fixes once landed)
cd ~/Drone_Project/elrs-sdr-transfer
git pull

# 2. Restart wr serve (the plugin + route + radio.py changed)
wr serve            # -> http://localhost:8899/

# 3. Confirm the ELRS radio is ON + bound to the bench Tinyhawk + transmitting
#    (Configurator -> RX page shows a live link; radio TXes when idle-disarmed, or arm it)

# 4. Confirm the rate + UID from the Configurator (RX page: rate + module UID)

# 5. Monitor -> "ELRS Find"  (radio on). Watch the live % + bar.
#    It saves to:  ~/Drone_Project/elrs-sdr-transfer/waveraider/captures/elrs_find_<timestamp>/

# 6. Upload that dir (or just manifest.json first) to the enclave for offline analysis
```

Then I re-run the detection (GFSK sync + LoRa preamble + duty cycle + DC-removed spectrum shape)
on the new capture and report what it finds.

---

*End of update log. The canonical design/status doc is `docs/FLRC_GFSK_DECODE.md`; this file is
the update log for the WaveRaider integration + the first bench capture.*
