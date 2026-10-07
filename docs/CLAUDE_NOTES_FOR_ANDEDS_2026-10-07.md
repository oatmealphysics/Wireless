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

— Claude
