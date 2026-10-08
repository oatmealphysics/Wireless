# andeds → Claude: Running Handoff (rolling log, newest entry at top)

**From:** andeds (enclave session)
**To:** Claude (laptop `borealis`/`Latitude`, driving the real B205mini)
**How this works:** this is my **running update log** for you — I append a new dated entry at the
**top** whenever I make changes Claude should know about (code, analysis, decisions, what I need
from you). You keep your mirror in `docs/CLAUDE_NOTES_FOR_ANDEDS_2026-10-07.md` (or a newer-dated
one). We read each other's docs on `git pull` and act on the "what I need from you / open items"
sections. Keep it terse — facts + decisions + asks, not prose.
**Context docs:** `docs/FLRC_GFSK_DECODE.md` (canonical design/status), `docs/ELRS_FIND_UPDATE_LOG.md`
(the full WaveRaider-integration + first-bench-capture update log).

> **Standing ground rules (both of us, agreed):**
> - **Boundary:** passive **detect / decode / identify** only. The forged-frame "throw" (TX on the
>   live link to stop the drone) is a remote-kill of an aircraft and is **out of scope** — not
>   advanced. `elrc_bench_tx.py` stays non-functional + out of scope. Same line, both sides.
> - **One branch:** `main`. Pull before you work, push after. (You're Maintainer on the project;
>   pushes work.)
> - **Honesty contract:** label **VALIDATED** (checked against firmware/datasheet/real capture)
>   vs **SELF-CONSISTENT** (round-trips our own code) vs **SYNTHETIC** (offline only). Never
>   overclaim. The GFSK front-end is SYNTHETIC until a real capture validates it.
> - **No test scaffolding in the repo for one-off validation** — but *fixing stale/incorrect* tests
>   in the existing suite is fine (it's the project's quality, not throwaway scaffolding).
> - **Enclave can't run the WaveRaider pytest** (closed network, no PyPI) — so I verify test-logic
>   changes by simulating the regex/logic, and the *real* pytest run is yours (laptop).

> **CURRENT STATE (as of this pull):** `main` @ `4dff93e` (enclave + laptop in sync).
> `elrs_sdr.selftest` ALL PASS. **Two things landed since I last looked:**
> **(1) The captures policy was REVERSED (Claude §14, operator call):** full raw IQ syncs on every
> push again (`.gitignore` re-includes `captures/**/*.complex8`). The 5 dead/noise captures were
> removed (`d577261`), so `captures/` is empty — the next real sweep is a clean slate. The
> pre-push hook is confirmed installed on the laptop + working (it auto-committed
> `ca2a29b` = capture 20261008_153552). **So the operator's workflow is now: run ELRS Find →
> `git push`; I `git pull` → `analyze_capture.py`.** The ~160 MB/sweep bloat is explicitly
> accepted; dead captures get deleted as they pile up (a coordinated `git filter-repo` to shrink
> history is a future one-off, not now).
> **(2) A B205 USB-drop mitigation (code, `99e77e6`):** two changes in `elrs_sdr/radio.py` —
> (a) **deeper USB recv ring** (`num_recv_frames=512` default, env-tunable via
> `UHD_NUM_RECV_FRAMES`) so the host rides out the dock hub's scheduling gaps without the FPGA
> overflowing; (b) **`_stop_stream()` at the end of each channel's `capture()`** (via try/finally,
> so it runs on every exit path) — this stops the B2xx's continuous free-run between channels,
> which is what fixes the "ch0 clean, then `D`/`DDOD` on every later channel" symptom (without a
> STOP, the FPGA keeps streaming into a torn-down transport, the buffers overflow while idle, and
> the next channel opens onto a stale backlog → immediate drops). **I verified: syntax OK,
> selftest ALL PASS, the mock-UHD test passes (incl. the new #7 USB-buffering + #8 stream-STOP
> checks), and `_with_usb_buffering` produces the right args (default 512, env override, no
> double-append).** **But it still needs a REAL on-hardware run** to tell if it tames the dock
> drops or confirms the powered-hub purchase — the enclave has no B205.
> **NEXT ACTION (laptop):** `git pull` → restart `wr serve` → run a 5 MS/s / 30 ms dwell sweep
> (B205 on the lone blue port, SD reader/Ethernet unplugged, radio TXing, Tinyhawk armed) →
> **watch for the `D`/`DDOD` indicators**: if they're gone (or much reduced) + the sweep finishes
> in ~15–30 s → the mitigation works, we're unblocked. If they persist → the dock genuinely can't
> do it, and the powered USB-C hub is the fix. Then `git push` (the hook auto-commits the capture)
> → I analyze. **Still pending: the Configurator check (rate + UID + ELRS version)** — if OTA v6,
> the v4 sync won't match even a clean capture.

---

## 2026-10-08 (andeds) — ack Claude §14 (captures policy reversed) + the B205 USB mitigation

**Pulled + reviewed `99e77e6` + `4943ac5` + `4dff93e` (Claude + the operator's commits since
`db24f92`):**

### The captures policy reversal (Claude §14, operator call)
- **`.gitignore`** re-includes `captures/**/*.complex8` (+ `.cf32`/`.iq`) — raw IQ under `captures/`
  is tracked again. Stray IQ *outside* `captures/` stays ignored.
- **The pre-push hook is now installed + working on the laptop** — it auto-committed `ca2a29b`
  (capture `20261008_153552`). So the operator's "just `git push`" workflow **works**: run ELRS
  Find → `git push` (hook auto-commits `captures/`) → I `git pull` → `analyze_capture.py`.
- **The 5 dead captures were removed** (`d577261`) — all noise/dropped, already analyzed. `captures/`
  is empty; the next real sweep is a clean slate.
- **Bloat tradeoff explicitly accepted** by the operator (~160 MB/sweep, kept in history). Plan:
  delete dead captures as they pile up; a coordinated `git filter-repo` to shrink the 348 MB already
  in history is a future one-off (not now, no force-push without the operator saying so).

### The B205 USB-drop mitigation (code, `99e77e6` — I reviewed + verified it)
Two changes in `elrs_sdr/radio.py`, both well-reasoned + defensive:
1. **Deeper USB recv ring** (`_with_usb_buffering`): appends `num_recv_frames=512` (default) to the
   device args (env-tunable via `UHD_NUM_RECV_FRAMES`). A deeper host-side ring lets the host ride
   out the dock hub's scheduling gaps without the FPGA overflowing — UHD's standard fix for `D`/`O`
   drops. Harmless on a good link (a few MB more RAM). Verified: default → `num_recv_frames=512`,
   env override works, no double-append if already set.
2. **`_stop_stream()` at the end of each channel's `capture()`** (via a refactored `_capture_loop`
   + try/finally, so it runs on every exit path): issues a `STOP_CONTINUOUS` stream command so the
   B2xx stops free-running its continuous RX *between* channels. This is the fix for the "ch0
   clean, then `D`/`DDOD` on every later channel" symptom — without a STOP, the FPGA keeps
   streaming into a torn-down transport, the USB buffers overflow while idle, and the next channel
   opens onto a stale/overflowed backlog → immediate drops. The `_stop_stream` impl probes the
   UHD 4.11 `StreamMode`/`StreamCMD` API (multiple binding forms) + is fully try/guarded (a no-op
   where the API differs, so it can't regress the working path).

**My verification (enclave, no B205):** syntax OK, `elrs_sdr.selftest` ALL PASS (no regression),
the mock-UHD test passes including the two new checks (#7 device args carry `num_recv_frames`,
#8 `capture()` issues STOP_CONTINUOUS on exit). `_with_usb_buffering` unit-checked (default 512,
env override, no double-append).

**The open question (needs a real on-hardware run):** does this mitigation actually tame the dock
drops, or does it confirm the powered-hub purchase? The `D`/`DDOD` indicators in the `wr serve`
log are the tell — if a 5 MS/s / 30 ms dwell sweep on the laptop now runs clean (no `D`/`DDOD`,
finishes in ~15–30 s), the mitigation works. If `D`/`DDOD` persist, the dock genuinely can't
sustain the B205 and the powered USB-C hub is the fix. **That's the next test on the laptop.**

---

## 2026-10-08 (andeds) — DEFINITIVE: the Anker dock cannot sustain the B205 (tested at 5 MS/s)

**Test run (laptop, 2026-10-08 ~15:15):** `WAVERAIDER_LOCAL_CLI=1 wr serve` → B205 came up clean
(UHD 4.11, "Operating over USB 3", register loopback passed, clock 40 MHz). Then a **5 MS/s / 30 ms
dwell** sweep (`curl -s -N "http://localhost:8899/api/elrs?srate=5000000&dwell_ms=30"`):

```
wrote .../elrs_find_20261008_111514/ch0_2400.4MHz.complex8
DDOD wrote .../ch1_2401.4MHz.complex8
DDOD wrote .../ch2_2402.4MHz.complex8
DDOD wrote .../ch3_2403.4MHz.complex8
DDOD wrote .../ch4_2404.4MHz.complex8
D    wrote .../ch5_2405.4MHz.complex8
DDOD wrote .../ch6_2406.4MHz.complex8
DDOD wrote .../ch7_2407.4MHz.complex8
DD^C  (user Ctrl-C'd at ch7)
real  1m40.731s   (100 s to reach ch7, vs ~15–30 s expected)
```

**The `D` / `DDOD` characters are UHD's USB overflow/drop indicators** — the B205's USB pipe is
**dropping samples** because the Anker dock's hub can't deliver them fast enough. Every channel
after ch0 shows `D`/`DDOD`. The sweep crawled (100 s for 7 channels) because each channel's 300k
samples (30 ms × 5 MS/s) trickle in over many seconds due to the drops.

**Conclusion: the Anker dock is a HARDWARE blocker, not a tunable one.**
- Lowering the rate to 5 MS/s (which halves the stream) did **NOT** fix it — the drops persist.
- This rules out "just lower the rate" as a fix. The hub's problem is **jitter/scheduling**, not
  raw bandwidth (a 5 MS/s stream is only ~20 MB/s ≈ 160 Mbps, a tiny fraction of USB 3.0's 5 Gbps).
- Claude §11's diagnosis (the VIA Labs dock hub can't sustain a continuous SDR stream — power
  delivery + xHCI scheduling jitter) is **confirmed by this test**.

**The only real fix: a powered USB-C hub (or a different dock).**
- The B205 needs a hub that sustains ~40 MB/s (at 10 MS/s) or ~20 MB/s (at 5 MS/s) with **low
  jitter**. A **powered** USB-C hub (dedicated 5V/1A+ to the B205, no other high-bandwidth devices
  sharing the upstream) is the standard SDR recommendation.
- Cheap docks (Anker, VIA/Realtek-based) routinely fail this — they multiplex the USB-C upstream
  across many ports + a SD reader + Ethernet, and the xHCI scheduling + power delivery can't keep
  up with a continuous high-rate isochronous-ish stream.
- **Until a powered hub is in place, no clean capture is possible on this setup.** Every sweep will
  have `D`/`O` drops + corrupt channels, so the ELRS link can't be detected (a 1% duty FLRC burst
  is buried in a multi-second trickle of Wi-Fi on a dropped channel).

**What this means for the project:** the detection code is ready (GFSK/LoRa/burst/sync-dwell all
verified), the workflow is ready (git-synced captures, the RF-CLI exemption, the speed fix), but
**the bench can't produce a clean capture on the current hardware.** The path forward is:
1. **Get a powered USB-C hub** (the fix), OR
2. **Find a setup without the dock** (if the laptop has a way to get USB-A that isn't through the
   flaky dock — but the user confirmed it's USB-C-only, so this likely means the hub purchase).
3. **In parallel: the Configurator check** (rate + UID + ELRS version) — so that *when* a clean
   capture is possible, we know the Tinyhawk isn't on OTA v6 (which would break the v4 sync).

**Capture `elrs_find_20261008_111514`** (ch0–ch7, all `D`/`DDOD`-dropped) was NOT pushed (the user
Ctrl-C'd + the pre-push hook situation — see the separate note on the git-push-captures issue).
It's on the laptop at `~/Drone_Project/elrs-sdr-transfer/captures/elrs_find_20261008_111514/` if
needed for forensics, but it's all dropped-sample data — not analyzable for the ELRS link.

---

## 2026-10-08 (andeds) — the 403 (curl was never the right way) + the USB-C-only constraint

**What happened:** I handed the user a `curl "http://localhost:8899/api/elrs?..."` to run the
5 MS/s sweep. It returned in **8 ms** with `{"error": "Radio requests can only be made from
WaveRaider's own pages.", "available": false}` (HTTP 403). The user got `KeyError: 'channels'`
because the response was the 403 error JSON, not sweep results. **The sweep was never running —
every curl was bounced by the referrer gate.**

**The referrer gate:** the `/api/elrs` route (and the other radio endpoints) check that the
request comes from WaveRaider's own web pages (a `Referer`/`Origin` check). This is a security
measure (stop random web pages from driving the radio). `curl` from a terminal has no browser
referrer → 403. **This is by design, not a bug.**

**So the correct ways to run a sweep:**
1. **The browser (intended, no setup):** open `http://localhost:8899/` → **Monitor → ELRS Find**
   → set params (or click for the default) → watch the live % stream in → it saves to
   `captures/elrs_find_<ts>/`. **This is the normal workflow.** The CLI was never the path.
2. **CLI with spoofed referrer (quick test):**
   ```bash
   time curl -s -N \
     -H "Referer: http://localhost:8899/monitor" \
     -H "Origin: http://localhost:8899" \
     "http://localhost:8899/api/elrs?srate=5000000&dwell_ms=30" > /tmp/s5.sse
   tail -5 /tmp/s5.sse      # the LAST 'data:' line is the final result (SSE stream)
   ```
   Note: the response is **SSE** (`data: {...}\n\n` lines), not a one-shot JSON — `json.load()`
   on the whole file fails. The final `data:` line has `channels` + `saved_dir`.
3. **Localhost exemption (code change, on the user's say-so):** I can add an exemption so
   `127.0.0.1`/`localhost` requests pass the referrer check without a browser (safe — the check's
   point is to stop *random web pages*, and a localhost CLI isn't that threat). Small change; I'll
   apply it if the user wants CLI to "just work."

**The USB-C-only constraint (why "just move the port" isn't available):** the laptop has **no
USB-A port, only USB-C**. The B205 (USB-A) reaches it **only through the Anker USB-C dock**
(B205 in a USB-A 3.0 blue slot on the dock). So there's **no native USB-A port to move to** —
the dock is the only USB-A source. The dock's internal hub (VIA Labs, per Claude §11) is the
flaky component. **Fixes within "what I have":** (1) move the B205 to a **lone** USB-A 3.0 port on
the dock (not sharing an internal branch with the SD reader / Ethernet), (2) **unplug the SD reader
+ USB-Ethernet** during the sweep (less hub load), (3) **5 MS/s + 30 ms dwell** (halve the stream).
**If the dock still drops at 5 MS/s** → it can't sustain the B205; the real fix is a **powered
USB-C hub** (a purchase) or a different dock.

**Next (user):** run the 5 MS/s + 30 ms dwell sweep **from the browser** (Monitor → ELRS Find,
or with the referrer-spoofed curl above), B205 on the lone blue port, SD reader/Ethernet
unplugged, radio on+TXing, Tinyhawk armed. Push the capture → I analyze the per-channel sample
rates (clean ~5 MS/s vs the <0.3 MS/s collapse) + the clean channels for the ELRS link.

---

## 2026-10-08 (andeds) — analyzed the 20261008 capture: found the USB-drop blocker

**Capture `elrs_find_20261008_093931`** (58 channels, ch0–ch57, **no manifest** — the sweep
stopped early, 13:39:33→13:52:06 = 12.5 min):

- **21 channels OK** (9.9 MS/s, 500k samples): ch 0,3,5,7–12,14–17,19,22,25,33,37,52,53,57
- **37 channels CORRUPT** (measured sample rate collapsed to <0.3 MS/s, mostly <0.1 MS/s). Every
  channel has `n_samples=500000` (the *requested* 50 ms × 10 MS/s), but the *measured* rate shows
  the samples actually arrived over **seconds**, not 50 ms — the B205's USB pipe stalled and the
  data trickled in.
- The 21 clean channels: **no GFSK sync (GFSK ~0.005, gate 0.10), duty ~0.82 (continuous, not an
  ELRS ~1% burst), broadband ~4x spectral peak** → **Wi-Fi/interference, no ELRS link present**
  (same signature as every prior capture).

**Diagnosis — the blocker is USB sample drops (Claude §11 corrected the mechanism):**
1. **Not USB 2.0** — `lsusb -t` shows the B205 on **USB 3.0 @ 5000M** (Bus 002 → hub Dev 009 →
   Port 003 → Dev 012). So the port speed is fine.
2. **The flaky DOCK HUB (the verified cause, per Claude §11):** the B205 is on a **VIA Labs USB-C
   dock hub** (Dev 009) sharing with an SD reader (Dev 010) + Realtek USB-Ethernet (Dev 011). VIA/
   Realtek dock hubs commonly can't sustain a continuous SDR stream (power delivery + xHCI
   scheduling jitter) — independent of raw bandwidth. **My earlier "disk-contention" mechanism was
   WRONG** (Claude refuted it, verified on the laptop): `captures/` is on **internal NVMe**
   (`/dev/nvme0n1p2`), so the `.complex8` writes never traverse the USB hub; and `radius.capture`
   writes each file *after* its samples are collected (stream torn down between channels), so no
   disk I/O overlaps streaming. **My "80 MB/s heavy load" bandwidth claim was also wrong** — UHD
   streams the B2x0 as **`sc16`** (4 B/sample), so 10 MS/s ≈ 40 MB/s ≈ 320 Mbps ≈ 3% of the hub's
   10 Gbps uplink. **So the fix is the same (native port + lower rate) but the reason is the dock
   hub's flakiness, not disk contention or bandwidth.**
3. **Fixes (laptop):** (1) **plug the B205 into a native laptop USB 3.0 port** (Bus 004 = free
   10000M root, or Bus 002's root directly) — bypass the dock. (2) **Re-run at 5 MS/s**
   (`?srate=5000000`) — halves the stream (~20 MB/s), more overflow margin; 2× the FLRC 3.9 MHz BW
   is plenty for detection.
   ```bash
   time curl -s "http://localhost:8899/api/elrs?srate=5000000" > /tmp/s5.json
   python3 -c "import json; d=json.load(open('/tmp/s5.json')); print('ch:', len(d['channels']), d.get('saved_dir'))"
   ```
   **If 5 MS/s on a native port → clean channels + finishes in ~15–30 s** → dock-hub confirmed;
   re-analyze the clean data. **SKIP** any disk-over-USB-contention fix (moving the capture dir,
   buffer-then-write) — it won't help (writes are internal NVMe).

**Open question (the other load-bearing unknown):** even on the 21 *clean* channels there's **no
ELRS link** — only Wi-Fi. So *independently* of the USB issue, confirm from the Configurator:
**rate + UID + ELRS version.** If the Tinyhawk is on **OTA v6**, the v4-derived sync/CRC won't
match *any* channel regardless of capture quality.

---

## 2026-10-07 (andeds) — scan speed: matrix runs in seconds, not minutes (early-exit + fast sync-dwell)

**Your question:** "why does this dwell so long instead of swapping? should be seconds, not minutes."

**Profiled it (not guessed):** the cost is the **CLASSIFY**, not the channel-swapping. The capture
is fast (50 ms × 80 = 4 s RF). The slow parts:
- `sync_dwell_phy` captured `dwell_s × srate` samples (2 s @ 10 MS/s = **20 M**) and burst-scanned it
  as **1000 × 2 ms slices** → ~8.5 s for that pass alone.
- `run_scan_matrix` ran **all 3 passes back-to-back with no early stop** → ~37 s total, even if pass 1
  already found the link.

**Three fixes (`d0f299e`):**
1. **Early-exit** in `run_scan_matrix` (default on): stop as soon as a pass finds a GFSK/LoRa hit.
   "Found on pass 1" → ~one pass. `early_exit=False` (or `?early_exit=0`) still runs all 3 for
   full redundancy.
2. **Faster sync-dwell:** capture the sync channel at **5 MS/s** (`sync_srate` default; FLRC only
   needs ~3.9 MHz BW, so 5 MS/s is plenty + halves the buffer) + cap the burst scan at
   `max_slices=200` (a 1-2 ms FLRC burst is still caught). Sync-dwell ~8.5 s → ~1-2 s.
3. **`_augment_with_burst` threads `max_slices`** (sync-dwell passes 200; per-channel sweep keeps
   the 400 default, so the `sweep/burst` pass is unchanged).

**Web:** `/api/elrs?strategy=matrix` = the 3-pass redundancy with early-exit. `?sync_srate=`,
`?max_slices=`, `?early_exit=0` thread through. The route now normalizes the matrix result shape
(union of per-pass channels + reports which pass hit) so the UI table + "Saved N files" work the
same for matrix as for a single pass. **Backward-compatible:** no params = the original window sweep.

**Verified:** selftest ALL PASS, 3 burst tests pass, matrix early-exits on a hit (only `sweep/window`
ran in the mock), route normalization produces the right combined channels list.

**Expected timing now:** 80-ch `sweep/window` ≈ 4 s RF + ~11 s classify ≈ **~15 s**. Full matrix
worst case (nothing found, all 3 runs) ≈ ~30 s; best case (found on pass 1) ≈ ~15 s.

---

## 2026-10-07 (andeds) — FIXED the sweep hang (stuck at 4% / 2402 MHz)

**Your symptom:** the ELRS Find sweep hung at ~4% (3/80 channels), stuck on ch2 (2402 MHz), and
the manifest was never written (the capture dir only had ch0/ch1/ch2, ch2 corrupt at 0.36 MS/s).

**Root cause:** `RadioDevice.capture()`'s recv loop was
`while got<n_samples: n=_recv(buf, 0.5); if n==0: continue` — if the B205 stream **stalls**
(returns 0 samples repeatedly — a USB hiccup, or the device mid-freq-transition), it `continue`s
**forever**. A single stalled channel hangs the *entire* sweep.

**Two fixes (`61493e0`):**
1. **Stall guard** in `capture()`: bail out after **40 consecutive empty recv** (0.5s × 40 ≈ 20s
   max stall), return the **partial** samples (`out[:got]`), write a
   `PARTIAL capture (stream stalled)` file, so the sweep **advances to the next channel** instead
   of hanging forever. Verified: a fake stalled stream breaks out, returns partial, doesn't hang.
2. **50 ms settle after `tune()`**: the B205 RF front-end needs a moment to lock after a
   freq/rate change; starting the stream immediately (the next `capture()`) can catch the device
   mid-transition → early 0-sample reads → the stall. ~50 ms lets the PLL/RF settle. This is the
   likely **root cause** of the ch2 stall; the stall-guard is the defensive backstop so a sweep
   **never hangs again** regardless of cause.

`elrs_sdr.selftest` ALL PASS. **So: re-run the sweep** (radio on+TXing, Tinyhawk armed) — it should
now complete all 80 channels (a stalled one bails after ~20 s, records the partial, and continues).
Then `git add captures/ && git commit && git push` and I'll pull + analyze.

**Current state:** one branch (`main`), clean + in sync (`61493e0`).

---

## 2026-10-07 (andeds) — dynamic analyze_capture.py (no subfolder typing) + captures-path clarification

**Captures are at `<repo-root>/captures/`** (NOT `waveraider/captures/` — `web.py` writes to
`Path(__file__).parents[2]/captures` = repo root). So on the laptop it's
`~/Drone_Project/elrs-sdr-transfer/captures/elrs_find_<timestamp>/`. (Previously the `.complex8`
IQ was gitignored so I said "upload via the files API" — that's now superseded: see below,
`captures/` is synced as part of the project.)

**`captures/` is now a normal synced part of the project** (`a15020c`) — the `.complex8` IQ +
`.meta.txt` + `manifest.json` under `captures/elrs_find_*/` are **un-ignored**, so they
`git push`/`git pull` like any other file. **No more files-API upload.** (Stray captures outside
`captures/` are still ignored.) So the workflow is just:
```
# laptop:  after ELRS Find, the capture is in captures/elrs_find_<ts>/
git add captures/
git commit -m "bench capture <ts>"
git push
# enclave (me):  git pull  ->  python3 analyze_capture.py --fast   (auto-latest)
```
The `.complex8` IQ files are ~2 MB each × N channels, so a full 80-ch sweep is ~160 MB per
capture in the repo. That's fine for the bench (a handful of captures), but if the repo gets bloated
we can `git rm --cached` the old ones or move them off-branch later. **For now: just commit + push
the capture, I pull + analyze.**

**`analyze_capture.py` is now dynamic** (`1032714`) — you never type the timestamped subfolder:
- `python3 analyze_capture.py` → **auto-picks the most recent** `captures/elrs_find_*/` dir (by
  mtime) and analyzes it. (Run from the repo root.)
- `python3 analyze_capture.py --fast` → **quick read** (~1s/channel): GFSK sync + duty cycle +
  spectrum shape only, skips the slow LoRa CRC-decode. Use this for the "did it find anything?"
  check.
- `python3 analyze_capture.py` (full) → also runs the CRC-valid LoRa frame count, now **capped at
  100** (a real link is a consistent stream; stop once we hit 100) + **skipped on GFSK channels**
  (a channel is either GFSK or LoRa). This fixes the full analysis timing out on a 14-channel
  capture.
- An explicit dir still works: `python3 analyze_capture.py captures/elrs_find_<ts>`.

**Verified:** `--fast` on the 14-channel baseline finishes in seconds (correctly: no GFSK, no LoRa,
duty 0.82 = not an ELRS burst link → "radio not TXing / different rate-UID / interference"). Full
mode on 2 channels ~40s (ch0: 35, ch1: 31 LoRa frames, both <100 = noise false-positives).

**So the workflow for the final capture (laptop):**
```
cd ~/Drone_Project/elrs-sdr-transfer && git pull   # get 1032714
# restart wr serve (plugin changed): Ctrl-C then wr serve
# Monitor -> ELRS Find (radio on+TXing, Tinyhawk powered)
# it saves to: captures/elrs_find_<timestamp>/   (repo root, not waveraider/)
# upload that dir (files API) — the .complex8 won't git-push (gitignored)
```
Then I run `python3 analyze_capture.py --fast` (auto-latest) for the quick read, and full mode if
a GFSK/LoRa hit shows up. `elrs_sdr.selftest` ALL PASS.

**Current state:** one branch (`main`), clean + in sync (`1032714`).

---

## 2026-10-07 (andeds) — final-capture prep: honest per-channel progress + analyze_capture.py

Ready for the last bench capture. Two code changes pushed (`2f81500`):

1. **`sweep_phy` now interleaves capture+classify** (was two-phase). The old structure held the
   live progress bar at 0% for the whole multi-second capture phase, then jumped to ~100% in a
   flash. Now each channel is classified immediately after its capture → the progress callback
   fires per channel as it actually finishes, so the bar tracks real completion (`done=c+1 of
   total=channels`) instead of stalling. A failed capture now emits a `none` result (advances the
   bar) instead of being silently skipped; the manifest keeps ALL channels; noise floor is
   per-window (10th-pct of that channel). **Verified (mock radio):** the validated gfsk.selftest
   signal is detected on ch1 (`phy=gfsk`), 5 progress callbacks fire (done=1..5), return shape
   unchanged. `elrs_sdr.selftest` ALL PASS.
   - (Note: I verified the interleaved loop is **equivalent** to the old two-phase one — for a
     single channel the global-nf vs per-window-nf are identical, and the GFSK branch has its own
     absolute gate that overrides the power floor, so the nf change doesn't affect GFSK detection.)
2. **`analyze_capture.py`** — the offline re-analysis tool I run the moment you upload a capture.
   Per channel: reads the REAL sample rate from the `.meta` (skips USB-corrupt channels <80% of
   10 MS/s), applies `dc_block`, runs the GFSK sync detector (target UID, gate 0.10) + the
   CRC-valid LoRa frame count (low ~20-35 = CRC false-positives on noise, ≥100 = real stream),
   duty cycle + DC-removed spectrum shape, then a per-channel table + verdict.
   `python3 analyze_capture.py <captures_dir> [uid_hex]`. **The enclave has no SDR** — all live RF
   is on your laptop; this just processes the IQ files it saved.

**The 3 things that determine whether this capture works (the last one found no link):**
1. **Radio on + bound + actively transmitting** (watch the TX LED blink during the sweep) — the
   Tinyhawk RX only *receives*; the *radio* transmits the control link.
2. **Tinyhawk armed or in a TXing state** (some only TX when armed).
3. **Rate + UID + ELRS version** from the Configurator (if not `rate_index=2` GFSK / UID
   `00:00:00:11:18:5B` / ELRS v4, the sync reference won't match).

**Your run (laptop):**
```
cd ~/Drone_Project/elrs-sdr-transfer && git pull   # get 2f81500
# restart wr serve (plugin changed): Ctrl-C then wr serve
# Monitor -> ELRS Find (radio on+TXing, Tinyhawk powered) -> it saves to
#   waveraider/captures/elrs_find_<timestamp>/
# upload that dir
```
I'll run `analyze_capture.py` on it and report what's on the air.

**Current state:** one branch (`main`), clean + in sync (`2f81500`). `elrs_sdr.selftest` ALL PASS.

---

## 2026-10-07 (andeds) — ack: your meta correction (accepted) + ground-truth pytest 27/576/3

You pushed two updates (`593c97c`, `07ad23c`); I pulled + verified both.

1. **Your meta `format:` correction — accepted, you were right.** My `d6c3de4` wording said
   "2 bytes/sample," which was wrong/sloppy: an int16 *complex* sample is **4 bytes** (2-byte I +
   2-byte Q), and my text contradicted `n_samples × 4 = file size`. Your fix
   (`4 bytes/complex-sample (int16 I + int16 Q); file size = n_samples*4`) is accurate, and you're
   right that by convention `complex8` = complex *int8* (our format is really `cs16`/`sc16`) — I
   dropped the "SDR-standard" claim. Verified on `main` (`07ad23c`): the line renders correctly
   (real newline, sensible text), `elrs_sdr.selftest` ALL PASS (no regression). **No action needed
   from me — it's merged.**
2. **Ground-truth pytest: `27 failed, 576 passed, 3 skipped`** — thanks, that's the number I was
   missing (no pytest on the enclave). Reconciles everything: "577/26" was pre-ELRS-Find-commits,
   "575/28" was with them but before `7a6b5cf`, **27/576/3 is current.** My `7a6b5cf` cleared
   **1** (the `test_monitor` guarded-routes), not the ~2 I estimated — the `test_navigation`
   CLI-help assertion is a separate *wording* test, not a route-guard one, so the regex fix didn't
   touch it. Remaining 27 by family (none in code you or I touched this round): `test_protocols`
   14 (retired-names wording), `test_cli` 6 (retired-protocols + usage wording),
   `test_remote_id_reference` 4 (missing `*.expected.jsonl` fixtures), `test_bladerf_capture` 2,
   `test_navigation` 1 (CLI help/docs wording).

**Updated "what I need from you" (supersedes the 2 items below it that are now answered):**
- ~~re-run pytest + paste the number~~ → **done: 27/576/3** (see above).
- **Still the load-bearing unknowns for the next re-capture:** (1) confirm the real Tinyhawk's
  **rate + UID + ELRS version** from the Configurator (RX page) — a newer ELRS (e.g. v6) changes the
  UID-derived sync/CRC so a live link won't match our `OTA_VERSION_ID=4` reference; (2) confirm the
  **ELRS radio (TX) is on + bound + actively transmitting** during the sweep (the Tinyhawk RX only
  receives — the radio transmits the control link). The first bench capture found no link, most
  likely because the radio wasn't transmitting or a different rate/UID.

**Open items (mine):**
- The remaining 27 stale tests — I can take the **wording** ones from here (the `test_protocols`
  14 + `test_cli` 6 + `test_navigation` 1 retired-protocols / CLI-help wording = ~21; they're
  stale-string fixes I can do by reading the expected vs actual strings). The `test_remote_id_reference`
  4 need the `*.expected.jsonl` fixtures copied from `WaveRaider-master/` (laptop), and the
  `test_bladerf_capture` 2 are bladeRF-rate-specific (need a bladeRF to truly verify). **Tell me if
  you want me to take the ~21 wording ones now, or hold for the re-capture.** I'd rather not churn
  the test suite right before a bench test unless you want the baseline green.

**Current state:** one branch (`main`), clean + in sync (`07ad23c`). `elrs_sdr.selftest` ALL PASS.

---

## 2026-10-07 (andeds) — DC-block + meta format + 1 stale-test fix; verified your 3 fixes

**What I did (on `main`, pushed):**

1. **DC-block before detect (`d6c3de4`).** Your `CLAUDE_NOTES §5` left me the DC-block. Added
   `elrs.dc_block()` (subtract the per-band mean) and applied it at the top of `decode_gfsk`
   + `classify_phy`, so every downstream detector sees clean IQ. The B205 leaves a residual DC
   offset even with the hardware `set_rx_dc_offset` (the 2026-10-07 bench capture showed a huge
   0 kHz spike).
   - **Verified:** it reduces the 0 kHz spike (ch0 3×→2.7×, ch7 2×→1.2×). **Key finding:** the
     GFSK correlation is *unchanged* (~0.005, still far below the 0.10 gate) — so the bench
     capture **genuinely has no target-UID GFSK link**; DC wasn't hiding one. The DC fix makes the
     *next* capture (radio confirmed transmitting) cleaner/more decodable, but it can't conjure a
     signal that isn't there.
   - Note: your C++ already added `set_rx_dc_offset(true)`; this is the **software backstop** (the
     two pair as you suggested — hardware reduces it at the source, software mops up residual).

2. **Meta format clarity (`d6c3de4`).** The `.complex8` extension is the **SDR standard** for int16
   I/Q interleaved (not a data bug — the byte count is consistent: 2,000,000 = 500k × 4). I left
   the extension and just made the meta `format:` line lead with the unambiguous description
   (`int16 I,Q interleaved, little-endian (.complex8 = SDR-standard int16 I/Q, 2 bytes/sample)`).

3. **Fixed 1 stale test (`7a6b5cf`).** `test_monitor.py::test_the_guarded_get_routes_are_exactly_
   the_ones_that_reach_the_radio_module` extracts GET routes that reach the radio and asserts they
   `== RF_GET_ROUTES`. Its regex only matched `spectrum.*(sweep|analyze_signal|scan_droneid|capture_audio)`,
   so my new `/api/elrs` + `/api/elrs/progress` routes (which reach the radio via
   `elrs_plugin.sweep_phy` + `RadioDevice`, *not* `spectrum.*`) were in `RF_GET_ROUTES` but not
   extracted → the assertion failed (1 of your 28 pre-existing failures). I extended the regex to
   also match `elrs_plugin.sweep_phy` / `RadioDevice(`. **Verified by simulating the regex against
   web.py:** it now finds exactly the 6 `RF_GET_ROUTES` (was 4, missing the 2 ELRS) → passes.
   That clears the 2 route-guard assertions (the `/api/elrs` + `/api/elrs/progress` ones).

**What I verified (your 3 fixes from `CLAUDE_NOTES` — checked the code, not just your note):**
- ✅ FHSS `rng()` = `seed >> 16` (`fhss.py:49`); selftest asserts the firmware `seed>>16`
  (`selftest.py:168-169`). The `>>15`→`>>16` correctness fix is real + the test is de-self-referenced.
- ✅ Concurrency: both `/api/elrs` routes use `with _spectrum._DEVICE_LOCK:` + `_uhdb.close()`
  (`web.py:231,293`).
- ✅ classify GFSK: `classify_iq(gfsk_uid=…)` runs `gfsk.detect_sync_word` on a gated hit
  (`classify.py:100-106`); overlay passes `DEFAULT_TARGET_UID` (`spectrum.py:782-786`).

**Current state:** one branch (`main`), clean + in sync. `elrs_sdr.selftest` ALL PASS (no
regression from my changes).

**What I need from you / the laptop:**
1. **The load-bearing unknown: confirm the real Tinyhawk's rate + UID + ELRS version** from the
   Configurator (RX page). If it's a newer ELRS (e.g. version 6, like the GNU_Radio ref), the
   UID-derived sync/CRC change and a live link won't match our `OTA_VERSION_ID=4` reference. This
   is the single most useful check before the next re-capture.
2. **Confirm the ELRS radio (TX) is on + bound + actively transmitting** during the next sweep
   (the Tinyhawk RX only receives — the *radio* transmits the control link). The first bench
   capture found no link, most likely because the radio wasn't transmitting (or a different
   rate/UID).
3. **Re-run the WaveRaider pytest on the laptop** after `git pull` — the route-guard test should
   now pass (down from 28 → ~26 failures). Paste the tail so we have the real number.
4. **The next re-capture** (radio confirmed TXing + confirmed rate/UID + the DC-block in) — then
   I re-run the full detection (GFSK sync + LoRa preamble + duty cycle + DC-removed spectrum shape)
   on the new `captures/elrs_find_<ts>/` dir and report what it finds.

**Open items (mine, not yet done):**
- Fix the remaining stale WaveRaider tests I *can* do from here: the `test_cli` retired-protocols
  wording (~13; "the supported protocols are uas, elrs" vs the old "Remote ID ('uas')") and the
  `test_version` install issue (~6; needs `pip install -e .`). I can do the wording one now (it's a
  stale-string fix); the version one needs the editable install. The `test_remote_id_reference`
  ones (~6) need the `*.expected.jsonl` fixtures copied from `WaveRaider-master/`. **Tell me if you
  want me to take these, or hold for the re-capture.**
- The `classify.py` GFSK detector is UID-specific (needs the link UID). A UID-*agnostic* GFSK
  presence detector (detect "some FLRC link is here" without knowing the UID) would be a separate,
  bigger effort — probably not worth it for the bench (we know the UID), but it'd matter for the
  general "identify an unknown drone" goal. Parking, not urgent.

**A note on the test numbers:** your notes said "575 passed / 28 failed"; your transcript said
"577 passed / 26 pre-existing." I can't reconcile them (no pytest on the enclave). After my
`7a6b5cf` fix, the *true* count should be ~26 failures (the 2 route-guard ones cleared). The laptop
pytest run is the ground truth — please paste it.

---

*(older andeds entries go below this line as the log grows)*

## 2026-10-07 (andeds) — initial handoff doc created

This file started as a rolling andeds→Claude handoff. See the entries above. Earlier context:
the WaveRaider ELRS Find integration (sweep + classify + save + live progress), the first bench
capture analysis (no target-UID GFSK link found — radio likely not TXing / different rate-UID),
the `master`/`execfix` branch reconciliation (→ one `main`), and the push-permission fix. Full
detail in `docs/ELRS_FIND_UPDATE_LOG.md` + `docs/FLRC_GFSK_DECODE.md`.
