# WaveRaider Fusion Plan — add our SDR (UHD/B205) + ELRS decode, generically

**Goal.** WaveRaider becomes the base. Keep all of its wireless/RF/Remote-ID/DJI
work. Add **only what it's missing**: a way to drive our **UHD/SoapySDR** radio
(the B205mini — WaveRaider has no Ettus path), and a **protocol decoder** for
the link families it can't see into — starting with **ELRS** (the Tinyhawk is
just the test article; the decoder is for the whole ELRS ecosystem and is the
pattern for Crossfire/DSMX/etc. later).

**Principle (from the user):** tack on our SDR stuff *only as necessary* to catch
signals not already covered by WaveRaider. No re-architecture. No touching what
works. Each new protocol is a **plugin + a sweep hook**, exactly the extension
points WaveRaider was designed for.

---

## What WaveRaider already has (DO NOT TOUCH)

| Area | Where | Status |
|---|---|---|
| 6 drone bands (868/900/1.3/2.4/5.2/5.8) | `bands.py` | keep — broader than ours |
| Power-sweep spectrum | `spectrum.py` | keep — the backbone |
| FM / AM / analog-video demod | `demod.py` | keep — covers video/audio links |
| DJI DroneID OFDM detector | `droneid.py` | keep — better than ours (CFO grid) |
| Remote ID decode (scapy) | `uas_classify.py`, `protocols/uas.py` | keep — the identity path |
| Signature knowledge base | `uas_rf.py` | keep — our `uas_rf` equivalent, richer |
| Evidence UI, watchlists, sessions, nodes | `web.py`, `monitor.py`, `node_*` | keep |
| Radio arbitration / lease | `spectrum.py` | keep — we reuse it, not fork it |

**Keep WaveRaider's honesty contract.** Every new piece must say "candidate,
not confirmed," "presence, not identity," and report **unknown** when it can't
tell. No new over-claims.

---

## The gap (only what we add)

1. **UHD / SoapySDR capture backend** — WaveRaider detects radios by `lsusb`
   model-substring and only has rtl/hackrf/bladerf/signalhound/soapy backends.
   The **B205mini (Ettus USRP, UHD) is not recognized** and `analyze_signal` /
   `scan_droneid` explicitly refuse non-bladerf/rtl. → we add a **UHD backend**.
2. **ELRS decode + PHY detector** — WaveRaider has *zero* ELRS (lists it "tier 1
   decodable" in `uas_rf` but has no decoder). → we port our
   `elrs_sdr/core` (LoRa/FHSS/CRC/OTA) in as a **protocol plugin** + a **sweep
   PHY detector**, so an ELRS control link shows as a decoded entity, not a
   power spike. This is the template for every later protocol.

**We do NOT bring in** (redundant — WaveRaider already has it, often better):
its own `radio.py` we drop (use its backends); our DJI path (use theirs); our
band tables (use `bands.py`); our Remote ID (use theirs); our evidence UI (use
theirs).

---

## Design: fit WaveRaider's seams, don't fight them

WaveRaider has three clean extension points we reuse directly:

1. **Sweep backend** — `spectrum._BACKENDS` is a list of
   `(id, label, match(model), tool_ok(), install_hint)`; `_SWEEP_FNS` maps
   `id -> fn(start, stop, bins, device) -> [[freq_hz, db], ...] | None`.
   *Add one entry + one fn for UHD.* The device lock, lease, simulated fallback,
   and `_find_signals` (which already calls `uas_rf.classify`) all work unchanged.
2. **Protocol plugin** — `ProtocolPlugin` (base.py): `name`, `new_classifier()`,
   `columns/row/describe/detail`, `primary_kinds`, `input_kind`, and the declared
   `implemented` / `requires` for planned protocols. Register in
   `protocols/__init__._PLUGINS`. *Add `ElrsPlugin`.*
3. **Classifier** — `ClassifierLike`: `survey`, `update(pkt)`,
   `rebuild_findings()`. Frames come from a `Source.frames()`. *Add
   `ElrsClassifier` fed by decoded ELRS packets.*

### A. The UHD backend (`waveraider/uhd_backend.py`, new)

- **Two capture modes**, both behind the existing `sweep(start, stop, bins)`
  contract so the UI needs no changes:
  - **Power sweep** (for the spectrum view): tune across the window in steps,
    measure per-step power (mean/peak magnitude) → `[[freq_hz, db], ...]`.
    This is the *same thing our `--scan` does*, generalized to any window.
  - **IQ capture** (for ELRS decode + DroneID on our radio): open the USRP,
    `issue_stream_cmd(STREAM_MODE_START_CONTINUOUS)` (the fix we proved), grab a
    window, return `(iq, rate)`. **This is the single most important piece** —
    it's the `capture_iq()` WaveRaider's `scan_droneid`/`analyze_signal` already
    call for bladeRF, but for UHD.
- **Transport:** reuse **our** `elrs_sdr/radio.py` UHD-binding shim (it handles
  the 4 pybind variants + the stream-start + `set_rx_antenna/set_rx_dc_offset/
  set_rx_iq_balance`). We do *not* re-derive UHD handling — we import it.
  → The backend depends on `elrs_sdr.core` + `elrs_sdr.radio` being importable
  (see "Integration mechanics" below for how the two repos share code cleanly).
- **Detection:** add `"ettus"`/`"b205"`/`"b210"`/`"usrp"` to `interfaces` model
  matching and a `_BACKENDS` entry `("uhd", "Ettus USRP via UHD/SoapySDR", ...)`.
  Prefer **SoapySDR** (`soapy_power` / SoapySDR Python) if present (works across
  UHD families), else the native `uhd` Python bindings. `tool_ok()` reports
  which is available; `install_hint` explains the rest.
- **Backend registration:** one line in `spectrum._BACKENDS` + one in
  `_SWEEP_FNS`. Nothing else in `spectrum.py` changes — `_choose`, `readiness`,
  `source_status`, `sweep` all pick it up automatically.

### B. The ELRS decoder port (`waveraider/protocols/elrs/`, new package)

Port our `elrs_sdr/core` **unchanged** (it's the validated numeric core — the
conjugation fix, the CRC vectors, the FHSS LCG all tested):
- `elrs/lora.py`, `elrs/crc.py`, `elrs/ota.py`, `elrs/fhss.py` — the decode.
- `elrs/detector.py` (new, thin) — the **PHY presence detector** for the sweep:
  given an IQ window, run our `lora_chirp_score` / `fhss_hop` and return a
  `(score, "ELRS-family?")` so `_find_signals` can tag a carrier
  `rf_candidates += ["ExpressLRS"]` **as a hint, not a detection** (matches
  WaveRaider's existing `rf_candidates` contract exactly).
- `elrs/classifier.py` — `ElrsClassifier` (mirrors `UasClassifier`):
  `update(pkt)` where `pkt` is a decoded `ElrsPacket` + its carrier freq +
  rssi; keys an `Entity` by **link UID** (the ELRS 6-byte binding UID — the
  closest thing to a hardware id ELRS offers; it is a *link secret*, so the
  knowledge base says so explicitly, not an aircraft serial); accumulates
  channel hops, rate, RSSI, first/last seen; `rebuild_findings()` emits
  `Finding("elrs_link", "info", "ELRS control link observed on N channels, UID
  …, not an aircraft identity")`.
- `elrs/__init__.py` — `ElrsPlugin(ProtocolPlugin)`: `name="elrs"`,
  `primary_kinds=("elrs",)`, `input_kind` (decoded packets), `sample` = a
  bundled decode fixture, `knowledge` (the "what this means / limits" text, in
  WaveRaider's voice), `columns/row/describe/detail` (UID, rate, hops, RSSI,
  band).

### C. The sweep hook (tag ELRS in the power view, no decode)

- In `spectrum._find_signals`, after `uas_rf.classify(mhz, bw)`, **optionally**
  probe the detector for a strong carrier: call `elrs.detector` on a short IQ
  grab at that freq (only when a live radio + `--decode` are available; else
  skip and keep today's behavior). Add `"ExpressLRS (possible)"` to
  `rf_candidates` when the score clears a conservative threshold. **Never**
  promote it to a "detection" — it stays a reference hint, same as the other
  `rf_candidates`. This is gated and default-off so it can't slow the normal
  sweep.

### D. The full decode path (the "catch the Tinyhawk" part)

- A new capture source, `ElrsLiveSource(Source)`: streams IQ from the UHD
  backend over the ISM2G4 80-channel window, runs the LoRa/FHSS/CRC decode
  (our `elrs_sdr` capture/decode logic, now via the UHD backend), and `frames()`
  yields decoded `ElrsPacket`-like objects to `ElrsClassifier`.
- Exposed as a source spec: `wr --protocol elrs sdr:<dev>` (a new source scheme,
  alongside `live:<iface>`), **and** wired into the Monitor so an ELRS entity
  shows up in the same observations table as Remote ID.
- Reuses `spectrum._DEVICE_LOCK` + the lease so ELRS decode and Inspect RF never
  fight over the radio.

---

## Generic-by-design: this is the per-protocol template

The ELRS work is deliberately the **first instance of a repeatable pattern**, so
"go after every drone and protocol" is a checklist, not a rewrite:

```
waveraider/protocols/<name>/
  decoder.py        # the protocol's demod (port / new), validated with tests
  detector.py       # cheap PHY-presence hint for the power sweep (score, family?)
  classifier.py     # <Name>Classifier: update(pkt) -> Entity, rebuild_findings()
  __init__.py       # <Name>Plugin(ProtocolPlugin): name, knowledge, columns, sample
```
+ one line in `protocols/__init__._PLUGINS`
+ (optional) a line in `spectrum._find_signals` for the sweep hint.

**Order of attack (by value × feasibility):** ELRS (now) → **Crossfire** (LoRa
SX1272, same LoRa core + new CRC/sync; high value, our `lora.py` mostly reuses)
→ **Spektrum DSMX** (DSSS GFSK, there's a proven GNU Radio receiver to port) →
**FrSky ACCST/AFHDS** (GFSK, DIY-Multiprotocol reference) → **Futaba S-FHSS** →
**DJI** (already covered by WaveRaider's DroneID for the identity; OcuSync
video stays presence-only). Each is an isolated plugin; none blocks another.

---

## Integration mechanics (how two repos become one cleanly)

WaveRaider is MIT and self-contained; `elrs_sdr` is our repo. To avoid a
duplicated-forever split:

- **WaveRaider becomes the primary repo.** We bring the ELRS core in as
  `waveraider/protocols/elrs/{lora,crc,ota,fhss}.py` (a one-way port of our
  validated core — it's small, ~1.5k lines, and self-contained numpy).
- **UHD transport** (`radio.py`) stays the shared piece: the UHD backend
  imports it. Simplest: port `elrs_sdr/radio.py` + `uhd_bootstrap.py` into
  `waveraider/uhd_radio.py` (it's the one thing that's pure UHD glue, protocol-
  agnostic, and the stream-start fix lives here).
- **`elrs_sdr` continues as the C++/standalone tool** (the `cpp/` path is
  separate and stays). Over time `elrs_sdr` can become a thin wrapper over
  WaveRaider, but **not now** — keep both working during the transition.

---

## Test plan (matches WaveRaider's own discipline)

WaveRaider has ~40 test files, most pure-numpy/scapy, no hardware. We add:
- `tests/test_uhd_backend.py` — with a **mock UHD** (WaveRaider already tests
  SDR paths without hardware; we reuse that pattern): sweep returns the right
  point count/range, IQ capture returns finite complex at the requested rate,
  `tool_ok()` reports correctly, simulated fallback on failure. **No B205
  needed.**
- `tests/test_elrs_decode.py` — port our existing ELRS CRC/FHSS/LoRa vectors
  (they already pass 18/18 C++ + the selftest) so the port is proven identical.
- `tests/test_elrs_classifier.py` — feed decoded ELRS packets → one Entity per
  UID, hops/RSSI accumulate, `rebuild_findings` emits the right Finding,
  "not an aircraft identity" language present.
- `tests/test_elrs_detector.py` — synthetic LoRa chirp → high score; noise →
  low; sweep-hint stays a hint (never a detection).
- **Regression:** run the *whole* existing WaveRaider suite — nothing we add may
  break the 40 existing files.

**Hardware validation (laptop, separate gate — WaveRaider's own rule):** the
UHD backend + ELRS decode against the real B205 + a real ELRS TX is a *live*
integration milestone, tested on the laptop, reported as "validated on-device,"
kept out of the offline test claims.

---

## Phases (each independently shippable + verified)

1. **P1 — UHD backend (power sweep + IQ capture), offline-tested with a mock.**
   *Unblocks using WaveRaider with the B205 immediately* (Inspect RF + its
   FM/AM/DroneID now run on our radio). No ELRS yet.
2. **P2 — ELRS decoder port + classifier + plugin (offline, recorded IQ).**
   *The Tinyhawk's signal can be decoded* from a capture, shown as an ELRS
   entity. No live capture yet.
3. **P3 — ELRS live source + sweep hint, wired into Monitor + `wr` CLI.**
   *`wr --protocol elrs sdr:…` + the dashboard shows live ELRS.*
4. **P4 — laptop live validation** (real B205 + real ELRS TX), then **P5+ — the
   next protocol** (Crossfire) as the second template instance.

Each phase: code → its tests green → **full WaveRaider suite still green** →
commit. Nothing ships a phase that breaks an existing test.

---

## Open questions (need a decision before P1)

1. **Repo home.** Do we make WaveRaider the primary repo (new GitLab project
   `andeds-agent/waveraider`, our `elrs-sdr-transfer` becomes the C++/standalone
   companion)? Or fold into the existing repo under a `waveraider/` subtree?
   *(I recommend a new primary repo — WaveRaider is now the base.)*
2. **UHD via SoapySDR vs native `uhd`.** Does the laptop have SoapySDR +
   `soapy_power` installed, or only the `uhd` Python bindings? (Affects the
   P1 backend's primary path. We can support both, but need to know which to
   lead with.)
3. **Scope of "every protocol" for the first milestone.** ELRS only (P1–P4), or
   also start Crossfire's decoder in parallel? *(I recommend ELRS end-to-end
   first, prove the template, then Crossfire.)*
