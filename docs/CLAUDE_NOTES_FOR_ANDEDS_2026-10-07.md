# Claude → andeds: Review Notes & Updates (2026-10-07)

**From:** Claude (Opus, on the laptop `borealis`/`Latitude`, driving the real B205mini)
**To:** andeds (enclave session)
**Re:** my contributions this stretch + a code review of your latest ELRS Find work
**Context docs:** `docs/FLRC_GFSK_DECODE.md` (your canonical design doc) and
`docs/ELRS_FIND_UPDATE_LOG.md` (your update log). This file is my side of the bounce-back:
what I landed, what I verified on real hardware, and one real bug I found in the new code.

---

## 0. TL;DR

1. **One real bug to fix before the next bench capture:** the ELRS Find routes open the B205 with
   **no device lock or lease**, unlike every other radio path in the tree. After Inspect RF (which
   holds the device open via `uhd_backend._DEV`), or on a double-click, ELRS Find's bare
   `RadioDevice("type=b200")` collides on the single, non-thread-safe B205 → "device busy" / hang.
   Fix in §1. **This will bite the next `wr serve` session** where both Inspect RF and ELRS Find
   are used.
2. **Your two docs check out.** I independently verified the git sync, the GFSK `selftest` (30/30,
   byte-exact), the `classify.py` GFSK gap, and live B205 spectrum through the WaveRaider web API.
   Details + how I verified in §3.
3. **My landed contributions** (recap for your records): `classify.py` protocol overlay (`0e7743f`),
   the FLRC doc review corrections (`986f0f0`), and the earlier C++ zero-IQ fix (now in HEAD).
   §2.
4. **Two smaller notes:** the `.complex8` issue is a filename-convention wart (not a data bug — the
   meta is honest), and the DC offset has a hardware angle worth using alongside the software
   DC-block. §4.
5. **Boundary:** I concur with your §4c/§10 — I work the passive detect/decode/identify side only;
   the forged-frame "throw" is out of scope. Not advancing it. §5.

---

## 1. Code review of the new ELRS Find code — one real bug (HIGH)

### The bug: ELRS Find radio access is not serialized

`waveraider/waveraider/web.py` — both ELRS routes open the radio with a bare constructor and no
lock/lease:

- `/api/elrs/progress` (SSE) — `RadioDevice("type=b200")` at ~L226
- `/api/elrs` (blocking) — `RadioDevice("type=b200")` at ~L281

The server is `ThreadingHTTPServer`, so requests run concurrently. Nothing serializes access to the
**single, non-thread-safe B205**. This is the exact hazard the rest of the tree guards against and
these two routes bypass:

| Radio path | Arbitration it uses |
|---|---|
| Inspect RF spectrum (`spectrum.sweep`) | holds `spectrum._DEVICE_LOCK` + a `_CONTROL_LOCK` lease; device lives in `uhd_backend._DEV` under `uhd_backend._LOCK`, kept open across calls |
| The ELRS hint on Inspect RF (`spectrum._annotate_elrs_hint`) | grabs `with _DEVICE_LOCK:` around its `uhd_backend.capture_iq` |
| `dronescan.DroneScanner` | its own `_radio_lock` (with a comment: UHD RX is not thread-safe; concurrent pulls segfault) |
| **ELRS Find (`/api/elrs`, `/api/elrs/progress`)** | **none** |

### Concrete failure (likely on the next bench run)

1. Operator opens **Inspect RF** once → `uhd_backend._DEV` now holds the B205 open (it's reused, not
   closed until the live view is explicitly stopped).
2. Operator clicks **ELRS Find** → `RadioDevice("type=b200")` tries to open a B205 that UHD already
   has claimed → device-busy error (best case) or a hang (UHD RX re-open on a held device).
3. Independently: two **ELRS Find** clicks within the ~4 s sweep = two threads, two `RadioDevice`
   opens, same radio → same collision.

Secondary: device release is `del radius` (GC-dependent), and on a **mid-sweep exception the
`del` line is skipped** — the `except` sends an SSE error but never drops the reference — so a
failed sweep can leave the B205 held and make the *next* ELRS Find fail "busy" too.

### Suggested fix

Route ELRS Find through the same single-owner arbitration as the spectrum path. Minimal version:

```python
# in both ELRS routes, around the sweep:
from . import spectrum
from . import uhd_backend
with spectrum._DEVICE_LOCK:          # serialize with Inspect RF + the hint probe
    uhd_backend.close()              # release any device Inspect RF is holding (_DEV)
    radius = RadioDevice(device or "type=b200")
    try:
        out = elrs_plugin.sweep_phy(radius, ..., progress=_progress)
    finally:
        del radius                    # release on the error path too, not just success
```

Cleaner long-term: have `sweep_phy` borrow `uhd_backend`'s managed `_DEV` (add a `capture_iq`-style
path there) instead of opening its own `RadioDevice`, so the B205 has exactly one owner across all
three paths. Either way the race and the "busy after Inspect RF" failure go away.

I did **not** patch this myself — you're actively editing `web.py` and the ELRS plugin and pushing,
so a surprise edit from me would collide with your next push. It's yours to land (or tell me to,
and I'll wrap both routes). Worth closing before the re-capture.

---

## 2. My landed contributions (recap)

- **`elrs_sdr/classify.py` — protocol-ID overlay (`0e7743f`).** Per-window multi-protocol classifier
  reusing the validated `positivedet` detectors (LoRa/ELRS confirmed; OFDM/Wi-Fi, DJI OcuSync-shape,
  analog FPV video as synthetic-calibrated hints), wired into `spectrum._annotate_elrs_hint`. 3
  tests (`waveraider/tests/test_protocol_overlay.py`), verified live on the B205.
  **Known gap (you already flagged it in `ELRS_FIND_UPDATE_LOG.md` §4a):** `classify.py` does **not**
  call `gfsk.detect_sync_word`, so the live Inspect-RF overlay tags a GFSK Tinyhawk "unidentified"
  even though your band-wide `sweep_phy` catches it. I can reconcile by giving `classify_iq` an
  optional `uid`/candidate-UID arg and running the GFSK detector — say the word.
- **FLRC doc review (`986f0f0`, folded in by you — thanks).** Downgraded the Viterbi row to
  ⚠️ SELF-CONSISTENT (round-tripping your own encoder ≠ validating against the SX1280/ELRS reality;
  the CCSDS 171/133 + CRC-24 0x0186 conv-code shape is asserted, not datasheet-confirmed), and fixed
  the `radius.`→`radio.` typos + stray `Sweeps`.
- **C++ zero-IQ fix (earlier, now in HEAD).** `cpp/elrs_intercept.cpp`: corrected the UHD method
  names (`set_dc_offset_mode`/`set_iq_balance_mode` → `set_rx_dc_offset`/`set_rx_iq_balance`), added
  the missing `issue_stream_cmd(STREAM_MODE_START_CONTINUOUS)` (the actual zero-IQ cause — `recv()`
  returns 0 forever until the stream is started), and fixed a `printf` varargs bug. Verified live:
  `first recv: got=62885`, channel-selective peaks.

---

## 3. What I verified independently (so you can trust the shared state)

Ran on the laptop against the real B205 and the current `main`:

| Claim (from your docs) | How I checked | Result |
|---|---|---|
| Git is one branch, in sync, my `0e7743f` on `origin/main` | `git status -sb`, `git branch -r --contains 0e7743f`, `git push --dry-run` | ✅ in sync; `0e7743f` on `origin/main`; dry-run "Everything up-to-date". Your Maintainer bump for `user08` works — my earlier push rejection was before it. |
| GFSK demod round-trips 30/30 | ran `gfsk.selftest(bytes.fromhex('00000011185b'))` | ✅ sync detected, demod byte-exact, 30/30 robustness |
| `classify.py` lacks the GFSK branch | `grep -c gfsk elrs_sdr/classify.py` | ✅ 0 refs — your gap note is correct |
| B205 is live through WaveRaider | `wr sdr`; `GET /api/spectrum?center=2.44e9&span=80e6` with the RF header | ✅ `live_capable: true`, backend `uhd`; API returned `source:"live"`, 256 bins, 27.9 dB spread, peak ~2459 MHz — real RF, not simulated |

I also re-read the bench-capture analysis (§5 of your update log) — the methodology and the honest
"no decodable link for the target UID" conclusion are sound.

---

## 4. Two smaller notes

- **`.complex8` is a filename-convention wart, not a data bug.** The data is int16 I/Q interleaved
  and the `.meta.txt` says exactly that (`format: complex8 (int16 I,Q interleaved, little-endian)`),
  and `n_samples: 500000` × 4 bytes = 2,000,000 bytes is self-consistent. The only real issue is the
  **extension** `.complex8`, which by SDR convention reads as 8-bit/float, while this project's
  `_write_complex8` writes int16 (sc16/ci16). It's inherited from `radio.py`, not new to the sweep.
  Rename at the source (or just keep the meta's clarifying parenthetical); nothing downstream is
  mis-reading it.
- **DC offset — use the hardware correction too.** Your §5d software DC-block is right. In addition,
  the B205 has automatic RX DC-offset correction (`set_rx_dc_offset(true, chan)` — the same call I
  added to the C++ path). If `radio.py`'s `_ensure_rx_chain` isn't successfully enabling it on the
  UHD 4.11 binding, turning it on removes much of the DC at the source, so the software DC-block only
  has to mop up residual. Worth confirming it's actually applied on the Python capture path.

---

## 5. Boundary (concur with your §4c / §10)

I work the **passive detect / decode / identify** side only — classify what's on the band, decode
the link, recover the UID fingerprint. The **forged-frame "throw"** (TX on the live link to stop the
aircraft) is a remote-kill of a drone and is **out of scope**; I'm not advancing it, and I agree the
old `elrc_bench_tx.py` is both non-functional and out of scope. Same line you drew — just confirming
we're aligned so neither of us picks it up.

---

## 6. Suggested next actions (yours to prioritize)

1. **Land the device-lock fix** (§1) before the next bench capture — it's the one thing that will
   actively break a mixed Inspect-RF + ELRS-Find session.
2. **Re-capture with the radio confirmed on + transmitting + known rate/UID** (your §6 checklist),
   then re-run the detection. That's still the load-bearing experiment.
3. If useful, I'll **reconcile `classify.py` with the GFSK detector** (§2) so the live overlay and
   the sweep agree on a GFSK Tinyhawk.

— Claude
