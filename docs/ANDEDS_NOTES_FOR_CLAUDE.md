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
