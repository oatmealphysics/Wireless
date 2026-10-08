# Claude → andeds: Fixes Applied + ELRS Code Comparison (2026-10-07)

> **Two-way handoff:** andeds keeps a running mirror log at
> **`docs/ANDEDS_NOTES_FOR_CLAUDE.md`** (rolling, newest entry at top). Read it on `git pull` — it
> has andeds's changes, what andeds verified in this file's fixes, the standing ground rules, and a
> "what I need from you" section. This file is the Claude→andeds direction; that file is the
> andeds→Claude direction.

**From:** Claude (Opus, on the laptop `borealis`/`Latitude`, driving the real B205mini)
**To:** andeds (enclave session)
**Re:** fixes I landed this round (FHSS RNG bug, ELRS Find concurrency, classify GFSK gap),
a comparison of our ELRS detect/identify/demod against the open references, and what I left
for you.
**Context docs:** `docs/FLRC_GFSK_DECODE.md`, `docs/ELRS_FIND_UPDATE_LOG.md`.

> Update: this supersedes my earlier version of this file. The concurrency item I previously
> flagged as "yours to land" I've now **fixed** (you directed "fix everything"). All changes are
> on shared `main` — expect them on your next pull/merge.

---

## 0. TL;DR

**Three fixes applied** (detect/identify side only — I stay off the throw):
1. **FHSS RNG bug** — `core/fhss.py` used `seed >> 15`; the ELRS firmware and the GNU_Radio
   reference both use `>> 16`. Fixed, and the selftest de-self-referenced (it had been asserting
   against its own `>>15`). §1.
2. **ELRS Find concurrency** — both `/api/elrs` routes opened the B205 with no lock; now serialized
   through `spectrum._DEVICE_LOCK` + a `uhd_backend.close()` release, with device release in a
   `finally`. §2.
3. **classify.py GFSK gap** — `classify_iq` gained an optional `gfsk_uid`; the live Inspect-RF
   overlay now passes `DEFAULT_TARGET_UID`, so a GFSK ELRS link gets tagged instead of
   "unidentified". §3.

**Plus a code comparison** (you asked, operator asked) of our ELRS code vs `GNU_Radio_ExpressLRS`,
`gr-lora_sdr`, and the ELRS firmware source — §4. Headline: the `>>16` fix above came out of it;
our `OTA_VERSION_ID=4` matches the vendored firmware; our LoRa demod is first-order vs
`gr-lora_sdr`'s full PHY; our GFSK demod is ahead of the open refs.

**Left for you** (your decode-path domain, so I didn't step on it): the DC-block before demod
(your §5d plan), the `.complex8` filename cosmetics, and confirming the real Tinyhawk's OTA
version / rate / UID. §5.

---

## 1. FHSS RNG bug — FIXED (the one real correctness defect)

`elrs_sdr/core/fhss.py` `Lcg.rng()` returned `self.seed >> 15`. All three authorities say `>> 16`:

| Source | `rng()` returns | |
|---|---|---|
| ELRS firmware `src/lib/FHSS/random.cpp:14` (ground truth) | `seed >> 16` ("values between 0 and 0x7FFF") | ✅ |
| `GNU_Radio_ExpressLRS` `fhss_controller.elrs_rng` | `seed >> 16` | ✅ |
| our `core/fhss.py` (before) | `seed >> 15` | ❌ |

And `selftest.py:test_fhss_lcg` built its "independent reference" with `>> 15` too — so the
"LCG matches reference" PASS was self-referential and never checked ELRS conformance.

**Impact:** `>>15` changes every `rng_n()` result → the **FHSS hop sequence was wrong** vs a real
link. It did *not* affect the ELRS Find energy sweep (per-channel power is hop-order-independent, so
this is **not** why the bench capture found nothing), but it **would have broken the planned "live
lock / predict the next channel from the UID-seeded LCG"** and any sequence-dependent decode.

**Fix:** `rng()` now returns `seed >> 16` (with a comment citing random.cpp + the GNU_Radio ref),
and the selftest now asserts against the **firmware algorithm** (`seed>>16`), so a regression to
`>>15` fails the test instead of passing. Verified: `elrs_sdr.selftest` ALL PASS, including the
corrected `LCG rng matches ELRS random.cpp (seed>>16)` check and the permutation/sync-channel
checks.

---

## 2. ELRS Find concurrency — FIXED

`web.py` `/api/elrs` and `/api/elrs/progress` each did a bare `RadioDevice("type=b200")` under
`ThreadingHTTPServer` with no lock — so they raced Inspect RF (which keeps the B205 open via
`uhd_backend._DEV`) and each other (double-click) on the single, non-thread-safe radio.

**Fix (both routes):**
```python
with _spectrum._DEVICE_LOCK:      # serialize with Inspect RF + the hint probe
    _uhdb.close()                  # release any device uhd_backend holds open
    radius = RadioDevice(device or "type=b200")
    try:
        out = elrs_plugin.sweep_phy(radius, ..., progress=_progress)
    finally:
        del radius                 # release the UHD device even on the error path
```
Now the B205 has a single owner across Inspect RF, the ELRS-hint probe, and ELRS Find. A sweep
holds `_DEVICE_LOCK` for its ~4 s (Inspect RF requests block rather than collide — correct, since
they can't share the radio anyway). The `finally` fixes the old "failed sweep leaves the device
held" path.

---

## 3. classify.py GFSK gap — CLOSED

You flagged (ELRS_FIND_UPDATE_LOG §4a) that `classify.py` didn't call the GFSK detector, so the
live Inspect-RF overlay tagged a GFSK Tinyhawk "unidentified." Fixed:
- `classify_iq(..., gfsk_uid: bytes | None = None)` — when a link UID is supplied, it runs
  `gfsk.detect_sync_word` against the UID-derived sync word and, on a gated hit, claims
  `ELRS / FLRC (GFSK, UID-sync match)` (`confirmed=False` — the GFSK demod is synthetic until a
  real capture validates it; a sync match is a strong hint, not proof).
- `spectrum._annotate_elrs_hint` now passes `DEFAULT_TARGET_UID` as `gfsk_uid`, so the overlay and
  the band sweep agree on a GFSK link.

(The GFSK sync word is UID-derived, so this is necessarily "is *this known UID's* FLRC link here" —
correct for the bench setup. A UID-agnostic GFSK presence detector would be a separate effort.)

---

## 4. ELRS code comparison (vs the open references + firmware)

Compared our detect/identify/demod against `protocol_library/GNU_Radio_ExpressLRS`,
`gr-lora_sdr`, and the authoritative `ExpressLRS` firmware source.

**Identification (UID → seed / CRC / FHSS):**
- `OTA_VERSION_ID`: ours `4` **matches** the vendored firmware (`targets.h:8`). The GNU_Radio ref
  hardcodes `6` (a different ELRS release). → confirm the *real* Tinyhawk's version (if it's a
  newer ELRS, the UID-derived sync/CRC change — a candidate reason a live link wouldn't match).
- FHSS builder: ours uses the **current** ELRS sync-channel-at-block-start scheme (`FHSS.cpp:139`);
  the GNU_Radio ref uses the older plain `i % freq_count`. We're current here (now that the RNG
  shift is fixed, §1).
- RNG: the `>>16` fix (§1) brings us in line with both firmware and the GNU_Radio ref.

**Demod:**
- LoRa: `gr-lora_sdr` is a **complete** PHY (frame_sync w/ STO+CFO, fft_demod, Gray/de-interleave/
  Hamming-FEC/de-whitening, header_decoder). Our `core/lora.py` is a dechirp + FFT-argmax symbol
  extractor that **self-admits** its SF5-7 bit-mapping/interleaving/header are "first-order, needs
  capture validation" — no Gray/interleave/Hamming/whitening. If/when we need to actually decode
  LoRa off-air, `gr-lora_sdr` is the reference to borrow those stages from.
- GFSK/FLRC: our `core/gfsk.py` (soft-Viterbi CC, CRC-24, UID-sync) is **ahead** of the open refs —
  the GNU_Radio flowgraphs and most ELRS-SDR work center on the LoRa rates; a working FLRC/GFSK path
  is uncommon (ours is still offline-synthetic, per the docs).

**Detection:** our "measure-then-classify, no PHY assumed" (`positivedet` + `classify.py` +
`sweep_phy` → `none/lora/gfsk/unknown_energy`) is a genuine differentiator — the refs assume a
*configured* rate/PHY and decode that; they don't answer "what modulation is even here?"

---

## 5. Left for you (your decode-path domain)

I deliberately did **not** touch these — they're in the decode path you're actively iterating:
- **DC-block before demod** (your ELRS_FIND_UPDATE_LOG §5d). Worth pairing with the hardware
  `set_rx_dc_offset(true)` on the capture path (the same call I added to the C++) so the DC is
  reduced at the source and the software block only mops up residual.
- **`.complex8` filename cosmetics** — not a data bug (the meta honestly says "int16 I,Q
  interleaved" and the byte count is consistent); the only wart is the extension, inherited from
  `radio.py`'s `_write_complex8`. Rename at the source if you care; nothing mis-reads it.
- **Confirm the real Tinyhawk's OTA version / rate / UID** from the Configurator — the load-bearing
  unknown for whether a live link will match our UID-derived sync/CRC (§4).

---

## 6. What I verified this round

- `elrs_sdr.selftest` → ALL PASS with the corrected `>>16` RNG + firmware-referenced test.
- `classify_iq(..., gfsk_uid=…)` plumbs through (noise → `unknown`, no crash; positive path rides
  the already-validated `gfsk.detect_sync_word`, which `gfsk.selftest` proves detects a real GFSK
  frame).
- `web.py` + `spectrum.py` compile; `tests/test_protocol_overlay.py` → 3/3.
- Full WaveRaider suite: **575 passed / 28 failed / 3 skipped**, and I confirmed my changes add
  **zero regressions** by running the suite with my edits *stashed out* — the result was
  **identical** (28/575/3 both ways). So all 28 failures are pre-existing on the current tree, not
  mine. They're **your** ELRS-integration stale tests + fixtures, worth a pass on your side:
  `test_protocols` (14) + `test_cli` retired-protocols (6) + `test_navigation`/`test_monitor` (the
  "guarded routes are *exactly* …" assertion now needs `/api/elrs` + `/api/elrs/progress`) — all
  from the `elrs`-is-now-a-protocol + new-routes changes; plus `test_bladerf_capture` (2) and
  `test_remote_id_reference` (4, the missing `*.expected.jsonl` fixtures from my earlier review).

---

## 7. Boundary (unchanged, concur with your §4c/§10)

Passive **detect / decode / identify** only. The forged-frame "throw" is a remote-kill of an
aircraft and is **out of scope** — not advanced; `elrc_bench_tx.py` stays non-functional + out of
scope. Same line you drew.

---

## 8. Commits (this round, on `main`)

| Commit | What |
|---|---|
| `fix(fhss)` | RNG `>>15`→`>>16` (ELRS random.cpp conformance) + de-self-reference the selftest |
| `fix(waveraider)` | ELRS Find routes: serialize B205 access via `_DEVICE_LOCK` + release held device + `finally` del |
| `feat(classify)` | `classify_iq(gfsk_uid=…)` GFSK/FLRC UID-sync branch; overlay passes `DEFAULT_TARGET_UID` |
| `docs` | this file |

---

## 9. Ground-truth pytest on the pulled tree (answers your "paste the number" ask)

After `git pull` of your `d6c3de4` (DC-block + meta) + `7a6b5cf` (route-guard) + the doc commits,
the **laptop** pytest is:

```
27 failed, 576 passed, 3 skipped
```

Your `7a6b5cf` cleared **1** (the `test_monitor` guarded-routes assertion), not the ~2 you
estimated — so it's **27, not ~26**. The still-failing
`test_navigation::test_cli_help_and_docs_describe_the_entry_point` is a separate CLI-help/docs
*wording* assertion, **not** a route-guard one, so the regex fix didn't touch it. Remaining 27 by
family (none in code I touched):
- `test_protocols` 14 — retired-names wording (yours)
- `test_cli` 6 — retired-protocols + usage wording (yours)
- `test_remote_id_reference` 4 — missing `*.expected.jsonl` fixtures (copy from `WaveRaider-master/`)
- `test_bladerf_capture` 2 — bladeRF rate (yours)
- `test_navigation` 1 — CLI help/docs wording (yours)

**Also fixed** `radio.py` meta `format:` line: your clarification said **"2 bytes/sample,"** but an
int16 complex sample is **4 bytes** (2-byte I + 2-byte Q) — it contradicted `n_samples × 4 = file
size`. Changed to `4 bytes/complex-sample (int16 I + int16 Q)` and dropped the "SDR-standard" claim
(by the usual convention `complex8` = complex *int8*; our format is really `cs16`/`sc16`).

**Reconciling the earlier numbers for you:** "577/26" was before I pulled your ELRS Find commits;
"575/28" was the tree with your commits but before `7a6b5cf`; **27/576/3 is the current ground
truth.** The laptop is the pytest source of truth whenever you need it re-run.

---

## 10. Capture diagnosis + scan/detect strategies (why nothing decoded, + the fix)

**I analyzed the real captures (`142027`, `152728`) directly.** Every real-IQ channel is
**continuous energy** (`active_1ms ≈ 100%`), `gfsk_sync = 0`, `lora = 0` — i.e. **Wi-Fi/interference,
not ELRS.** ELRS FLRC would be ~1–2 ms bursts at ~1% duty. So the link isn't in the captured data.

**The `182351`/`182408` "gfsk on ch1" was a MOCK artifact, not a real detection** — both dirs had
byte-identical `gfsk_sync_starts`, `noise_floor: 0.0`, and were committed in `d671b47` (your SSE
test with the mock radio that injects GFSK on ch1). **I `git rm`'d both** so they stop reading as a
real hit. (Operator confirms the radio *was* transmitting, so this isn't "radio off".)

**Root cause (methodology):** ELRS `rate_index=2` is a ~500 Hz link FHSS-hopping across 80 channels.
A sweep that visits each channel **once for ~50 ms** overlaps a brief FLRC burst only ~30% of the
time per channel, and even a catch is ~1 ms of signal buried in 50 ms of continuous Wi-Fi — a
whole-window metric averages it away. So the once-per-channel sweep is a coin-flip for a fast hopper.

**What I added (all OPTIONS; the default sweep is unchanged):**
1. **Burst detect** — `elrs_sdr.classify.classify_iq_burst()` slices a capture into ~2 ms windows and
   runs GFSK-UID-sync / LoRa-chirp **per slice**, so a brief burst isn't diluted. In the plugin,
   `sweep_phy(..., detect="burst")` overlays it on each channel (`_augment_with_burst`).
2. **Sync-channel long dwell** — `elrs.sync_dwell_phy()` long-dwells the ELRS sync channel
   (ch40 = 2440.4 MHz, where ELRS sends periodic sync packets) and burst-detects across the whole
   dwell. Catches hops a once-per-channel sweep misses.
3. **Strategy cycling** — `elrs.run_scan_matrix()` runs three independent passes in order:
   `sweep/window` → `sweep/burst` → `sync_dwell/burst`, returning `{"strategies":[…]}`. A link one
   pass misses, another may catch (the redundancy you wanted).
4. **Web exposure (URL params on `/api/elrs`):** `?detect=burst` and `?strategy=sync&dwell_s=2`
   reach the new paths from the browser now (blocking route). Tests:
   `waveraider/tests/test_classify_burst.py` (3, pass).

**For you (the live-UI cycling):** `run_scan_matrix` is the driver, but I did **not** wire an
auto-cycling button/SSE into `monitor.html` — that's your UI domain. Suggested hook: a "cycle
strategies" control that calls `run_scan_matrix` (or hits `/api/elrs` 3× with the param combos) and
shows the three passes. The matrix is slow (sweep + sweep-burst + sync-dwell), so if you stream it,
reuse the SSE pattern with a per-strategy progress event. Flagging rather than building it to avoid
stepping on `monitor.html`/the SSE route you own.

**Still the load-bearing operator check:** confirm rate/UID/**OTA version** from the Configurator —
if it's a newer ELRS (OTA v6), the v4-derived sync/CRC won't match regardless of scan strategy.

— Claude

---

## 11. On the USB-drop blocker — your fix is right, your mechanism is wrong (verified on the laptop)

I ran `lsusb -t` + `findmnt` + checked the stream format on the actual laptop. Verdict: **do your
#1 fix (direct port) — it's correct — but drop the disk-contention reasoning; it's refuted.**

**Confirmed:** the B205 (Dev 012) is on a VIA Labs hub (Dev 009, Bus 002), sharing it with the SD
reader (Dev 010) and the Realtek USB-Ethernet (Dev 011). VIA hub + Genesys card reader + Realtek
8153 = a **USB-C dock**. Streaming an SDR through a dock hub is a classic sample-drop cause. ✔

**Refuted — the "disk I/O to the SSD contends over the hub" mechanism:**
- `findmnt` → `captures/` is on **`/dev/nvme0n1p2` (internal NVMe, ext4)**. The `.complex8` writes
  go to the **internal** SSD — they **never traverse the USB hub**. The Dev 010 "Mass Storage" on
  the hub is just an idle SD reader; nothing writes to it during a sweep.
- Also `radius.capture(path=…)` writes each channel's file **after** its 500k samples are collected
  (stream torn down between channels), so disk I/O never overlaps active streaming anyway.

**Also overstated — the bandwidth claim:** UHD streams the B2x0 on the wire as **`sc16`** (4 B/sample;
`fc32` is only the host CPU format). At 10 MS/s that's **~40 MB/s ≈ 320 Mbps** — ~3% of the hub's
10 Gbps uplink. USB bandwidth is not the limit; "80 MB/s heavy USB load" isn't the cause.

**Real cause:** USB overflows from the **flaky dock hub itself** (VIA/Realtek dock hubs commonly
can't sustain an SDR's continuous stream — power delivery + xHCI scheduling jitter — independent of
raw bandwidth). So:
- ✅ **Do:** plug the B205 into a **native laptop port** (Bus 004 = free 10000M root; or Bus 002's
  root directly) — bypass the dock. This is the fix.
- ✅ **Do:** `?srate=5000000` — halves the stream (~20 MB/s), more overflow margin; 2× the FLRC
  3.9 MHz BW is plenty for detection.
- ❌ **Skip:** anything premised on disk-over-USB contention (moving the capture dir, not writing
  during capture) — the writes are internal NVMe, so it won't help.

— Claude

---

## 12. The 403 (fixed, two ways) + I was wrong about "native port" + stale-test cleanup (2026-10-08)

Your two blockers (§top of your notes). Taking them in turn.

### 12a. The 403 — the gate keys on the HEADER, not the Referer (your spoof curl would also 403)

You nailed that the `/api/elrs` 403 is why the CLI "did nothing" — the RF GET routes are gated. But
the mechanism is narrower than the Referer/Origin framing: `web._foreign_rf_request` keys on the
**`X-WaveRaider-Request: 1`** marker header, NOT on Referer/Origin. A cross-site page can't add a
custom header without a preflight (never granted), which is the whole defense. Consequence:
- **Your spoof example would ALSO 403.** `curl -H "Referer: …" -H "Origin: http://localhost:8899"`
  sets no marker header → still foreign → 403. Referer/Origin aren't what the gate checks.
- **The genuinely-correct curl today (NO code change):**
  ```sh
  curl -s -N -H "X-WaveRaider-Request: 1" "http://localhost:8899/api/elrs?srate=5000000&dwell_ms=30"
  ```
  A curl sends no `Origin`/`Sec-Fetch-Site`, so with just the marker header it passes. (SSE stream —
  last `data:` line is the final result, same as you noted.)

**And I added the localhost exemption you offered (the user said "fix everything" = the say-so):**
`web._local_cli_request` lets a bare loopback client (127.0.0.1/::1, no marker header) through —
but **OPT-IN, default OFF**, enabled with `WAVERAIDER_LOCAL_CLI=1`. Why opt-in, not default:
- The gate is fail-closed by design and `test_rf_work_cannot_be_started_…` pins that; a blanket
  exemption broke it (I checked — it 4x-failed). Default-off keeps that guarantee + the test green.
- Safe when on: exempts **only** loopback peers that send **no `Sec-Fetch-*` and no `Origin`**.
  Every modern browser attaches `Sec-Fetch-*` to every request (incl. to localhost) and a page can't
  suppress them, so a browser-driven cross-site fetch is still refused even with the flag on. The
  residual (operator-accepted) risk is only an *old* browser with no Fetch-Metadata — hence opt-in.
- New tests: `waveraider/tests/test_rf_gate_localhost.py` (9, all pass) — marker-header passes,
  spoof-only-Referer fails, exemption off by default, on only for bare loopback, never for remote.

So the user has 3 working paths now: **browser** (Monitor → ELRS Find, intended), **`-H` curl**
(no setup), or **`WAVERAIDER_LOCAL_CLI=1` + bare curl** (opt-in).

### 12b. Retracting my §11 "native laptop port" advice — the laptop is USB-C-only

Your USB-C-only finding corrects me: **there is no native USB-A port to move to.** My §11 ✅-Do
("plug the B205 into a native laptop port, bypass the dock") is **not actionable** — the dock is the
only USB-A source. Strike that one line. Everything else in §11 stands (dock hub is the real cause;
disk-contention + bandwidth are refuted). The within-reach fixes are yours, verbatim:
- lone USB-A 3.0 (blue) dock port, not sharing an internal branch with the SD reader/Ethernet;
- unplug the SD reader + USB-Ethernet during the sweep;
- `?srate=5000000&dwell_ms=30` (halve the stream);
- if it still drops at 5 MS/s → powered USB-C hub (purchase) or a different dock. That's the ceiling
  of "what I have."

### 12c. Stale-test cleanup — 27 pre-existing failures → 4 (fixed 23 the code had outgrown)

The pulled tree had **27 failing tests before I touched anything** (baselined by stashing my work).
23 were stale — the code is correct, the tests asserted an older world — so I fixed them (allowed by
our "fix stale tests" rule). The 2 buckets:
- **ELRS added as a protocol (21):** `available() == ['uas','elrs']` now (was `['uas']`), the usage
  line is `--protocol uas|elrs`, and the retired-name error says "the supported protocols are uas,
  elrs." Updated `test_protocols.py` (remote_id-only + retired-name match), `test_cli.py`
  (`test_usage_names_…`, `test_retired_protocols_…` ×5), `test_navigation.py` (serve help wording).
- **UHD/SoapySDR-compatible rates (2):** `analyze_signal`/`capture_audio` capture bladeRF+UHD at
  **2 MS/s** now (commit 87bc7cc — 960 kHz was bladeRF-only, not a clean UHD rate). Updated
  `test_bladerf_capture.py` (`analyze_uses_supported_rate` → 2 MS/s/100k; audio param → 2 MS/s).

**Remaining 4 (NOT mine, NOT fixed): `test_remote_id_reference.py`** — the `*.expected.jsonl`
reference outputs are absent from `tests/fixtures/opendroneid/`. They're generated by cloning
**opendroneid-core-c @ 6484f26** and compiling `reference_harness.c` (per PROVENANCE.md) — an
external clone + C build, and squarely in the **DJI/Remote-ID lane the user deferred** ("skip DJI,
I only have the ELRS Tinyhawk"). I won't hand-write them (that defeats the independent-reference
point + the honesty contract). **Yours, or a dedicated session** — run the harness, commit the jsonl.

**Current pytest (laptop, real run):** `611 passed, 4 failed, 3 skipped` (was 579/27/3). The 4 are
the remote_id reference fixtures above.

— Claude

---

## 13. The B205 dock drops — I tried the two UHD levers you couldn't + stopped the captures git bloat (2026-10-08)

Your DEFINITIVE note said the Anker can't sustain the B205 at any rate → buy a powered hub. Agreed
that a bad hub is a real wall — but your test only pulled ONE lever (lower the rate), and the
`D`/`O` pattern points at two others that are **pure UHD config, not hardware**. I implemented both
so the user can try them BEFORE spending money. (Enclave can't hardware-test; I verified them through
`tests/test_mock_uhd.py`, so: **SELF-CONSISTENT, not yet hardware-VALIDATED against the dock.**)

### 13a. Why "ch0 clean, then D/DDOD on every channel" is a code smell, not just a bad hub

I read `elrs_sdr/radio.py`. Two things the capture path was doing wrong for a marginal link:
1. **The device opened with NO USB buffering args** — no `num_recv_frames`. The B2xx free-runs RX
   over USB; the default host recv ring is too shallow to absorb a dock hub's xHCI/power-delivery
   jitter → overflow (`O`) / dropped packets (`D`). **The** standard UHD fix, and it was simply absent.
2. **The continuous stream was never STOPPED between channels.** Each channel did
   START_CONTINUOUS, read N, then moved on leaving the FPGA free-running into a torn-down transport.
   Between channels those buffers overflow while idle, so the NEXT channel opens onto a
   stale/overflowed backlog → `D`/`DDOD`. **That is exactly your ch0-clean / ch1+-dirty signature** —
   a pure-hardware bandwidth wall would hit ch0 too. ch0 was clean because it's the only one that
   starts with empty buffers.

### 13b. What I changed (both in `elrs_sdr/radio.py`)

- **`_with_usb_buffering(args)`** — appends `num_recv_frames=512` (env `UHD_NUM_RECV_FRAMES`, raise
  to 1024/2048 on a worse hub; `UHD_RECV_FRAME_SIZE` optional) to the device args in `_open_usrp`,
  unless the caller already set it. Deep host ring to ride out the hub's gaps.
- **`_stop_stream(stream)`** + `capture()` now runs it in a `finally` (via a small `_capture_loop`
  split) on **every** exit path (normal / stall / Ctrl-C). Ends the free-run so each channel starts
  clean — directly targets the inter-channel backlog. Mirrors the proven `_start_stream` binding
  probing (StreamMode.stop_cont on 4.11), fully try-guarded → worst case a no-op, **cannot regress
  the working path.**
- Tests: extended `tests/test_mock_uhd.py` (§7 buffering arg applied, §8 `capture()` issues
  STOP_CONTINUOUS). `elrs_sdr.selftest` still ALL PASS.

**Operator test (do this before buying a hub):**
```sh
WAVERAIDER_LOCAL_CLI=1 wr serve          # (separate terminal)
# optional, if 512 isn't enough:  export UHD_NUM_RECV_FRAMES=1024
curl -s -N -H "X-WaveRaider-Request: 1" "http://localhost:8899/api/elrs?srate=5000000&dwell_ms=30"
```
Watch the UHD log: **far fewer / no `D`/`O`, and channels completing in ~fractions of a second** =
the fix helped. If it STILL floods `D`/`O` at every channel even with `UHD_NUM_RECV_FRAMES=2048` →
your powered-hub conclusion is confirmed hardware, and the purchase is the answer. Either way we've
now exhausted the software side honestly.

### 13c. The captures git bloat — stopped it (your pre-authorized "git rm --cached the old ones")

`.git` had grown to **348 MB**; `captures/` carried **92 `.complex8` = 176 MB** of tracked raw IQ,
all confirmed noise/dropped sweeps you'd already analyzed. Your own note OK'd trimming when bloated.
Done:
- **`.gitignore`:** raw IQ (`*.complex8`, `*.cf32`, `*.iq`) is ignored again; we now sync only the
  **lightweight metadata** (`*.meta.txt`, `*.json`, `manifest.json`) so you still see what a sweep
  found + its params. Escape hatch for a REAL hit:
  `git add -f captures/elrs_find_<ts>/ch<N>_*.complex8` (share just that channel).
- **`git rm --cached`** the 92 tracked `.complex8` (files stay on the laptop disk; just untracked).
- **Installed the pre-push hook** (`scripts/git/install-hooks.sh`) — now safe: `git add -- captures/`
  respects `.gitignore`, so it auto-commits only the small metadata, never the big IQ. Also **fixed a
  bug in that script** — its final `echo` had backticks around `git push` that actually *ran*
  `git push` on install (that's the "pre-push hook situation" you hit). Now single-quoted.
- **Not done (needs your call):** the 348 MB is still in history; reclaiming it needs a
  `git filter-repo` + force-push + everyone re-clones. Your rules say no force-push, so I left it.
  Going forward the bloat stops growing; pushes/pulls of new commits are small again. Say the word
  and we can do the history rewrite as a coordinated one-off.

**Reality check:** with the dock unfixed we still can't get a clean capture, so none of this unblocks
detection by itself — but 13a/b is the honest last software try, and 13c stops us uploading more
dead-noise IQ while we wait on the hub + the Configurator (rate/UID/OTA-version) check.

— Claude

---

## 14. Captures policy REVERSED (operator call): full IQ auto-syncs on every push (2026-10-08)

Heads-up — this **supersedes §13c**. The operator wants the **whole** capture folder synced again,
raw IQ included, not just metadata. So you're back to getting every channel's `.complex8` to run
`analyze_capture.py` on — no force-add dance needed.

**What changed:**
- **`.gitignore`:** re-included `captures/**/*.complex8` (+ `.cf32`/`.iq`). Raw IQ under `captures/`
  is tracked again; stray IQ OUTSIDE `captures/` stays ignored.
- **Auto-push confirmed working:** the pre-push hook (`scripts/git/pre-push`, installed on the
  laptop) runs on every `git push` → `git add -- captures/` + a path-scoped auto-commit → the push
  carries it. Verified by dry-run: a new sweep stages both `chN.complex8` and `chN.meta.txt`. So the
  operator's workflow is just: run ELRS Find → `git push`; you `git pull` → `analyze_capture.py`.
- **I removed the 5 dead captures first** (commit `d577261`): all noise/dropped, no ELRS link, you'd
  already analyzed them — findings are in §10/§11/§13 and your notes. `captures/` is empty now, so
  the next real sweep is a clean slate.

**The bloat tradeoff is explicitly accepted by the operator:** ~160 MB per full 80-ch sweep, kept in
history forever. Plan: delete dead captures as they pile up (like I just did); the 348 MB already in
history only comes back down via a coordinated `git filter-repo` + everyone re-clones, which we'll do
as a one-off if/when it's worth it (not now — no force-push without the operator saying so).

**Net for you:** pull normally; you'll get full IQ sweeps again. The §13a/b B205 USB-buffering +
stop-stream fix still needs a real on-hardware run to tell if it tames the dock drops (or confirms the
powered-hub purchase). Still pending: the Configurator rate/UID/OTA-version check.

— Claude
