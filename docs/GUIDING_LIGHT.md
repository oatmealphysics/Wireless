# GUIDING LIGHT — the project's end-to-end roadmap

**Status:** living doc. Newest guidance at the top. This is *the* strategic
spine for the project — the "guiding light" the operator set on 2026-10-14.
It is **growing**: each agent appends/updates the phase they are working, and
the operator + andeds re-prioritize at each phase gate. The three rolling
handoff logs (`ANDEDS_NOTES_FOR_BOTH.md`, `CLAUDE_NOTES_FOR_BOTH.md`,
`CODEX_NOTES_FOR_BOTH.md`) track *day-to-day progress*; **this file is the
durable "where we are going and what 'done' looks like."**

`AGENTS.md` is the canonical *rules* (scope, honesty contract, conventions).
`SECURITY.md` is the code-level guardrails. This file is the **plan**.

---

## The goal (operator, 2026-10-14 — verbatim)

> Detect, identify, then be able to execute a code/command on a drone or
> controller. All wirelessly, using whatever protocol the drone/GCS
> communicate with.

Three verbs, one pipeline, one surface:

```
     RF in the air
          │
          ▼
   ┌────────────┐     ┌────────────┐     ┌─────────────────────┐
   │ 1. DETECT  │────▶│ 2. IDENTIFY│────▶│ 3. EXECUTE (TX)      │
   │ something  │     │ which link,│     │ forge a valid frame  │
   │ is here, on│     │ what it is,│     │ → command / code on  │
   │ what PHY   │     │ unique ID  │     │ the owned bench link │
   └────────────┘     └────────────┘     └─────────────────────┘
          └────────────┬──────────────┬────────────────┬─────────┘
                       ▼              ▼                ▼
              WaveRaider UI    identity table     TX phase (gated,
              (presence +      (per-aircraft ID)   bench-only, human
               protocol +      surfaced in UI)     sign-off)
```

- **Protocol-agnostic is the #1 invariant** (AGENTS.md §1 "general, not
  single-target"). ELRS is the *first instance*, not the ceiling. The
  architecture must generalize to "whatever protocol the drone/GCS speak"
  (DJI OcuSync/Drone-ID, Crossfire, DSMx, SiK, Wi-Fi, Open Drone ID …).
- **MEASURE + CLASSIFY from the RF; DISCOVER the air state.** Never assume
  rate/PHY/settings. Build discriminators, not assumption-dependent decoders
  (AGENTS.md §1 core philosophy).
- **Identity = hardware-unique per drone** (MAC/IMEI-style), not the ELRS
  bind UID (which is a pairing secret). See AGENTS.md §1 "Identity goal."
- **One delivery surface:** WaveRaider web-UI. A capability that can't be
  shown there isn't finished (AGENTS.md §1 delivery surface).

---

## The three phases at a glance

| # | Phase | One-line goal | Primary deliverable | Owner (primary / QC) | Gate to next |
|---|-------|---------------|---------------------|----------------------|--------------|
| 1 | **DETECT** | See *that* a link is present, on what PHY/protocol, at what rate/freq | a live air-state detector that reports `NONE→PRESENCE→FAMILY→SPECIFIC` for any protocol, surfaced in WaveRaider | Codex / Claude | each of the 16 catalog protocols reaches ≥ `FAMILY` on a real capture; ELRS reaches `SPECIFIC` |
| 2 | **IDENTIFY** | Know *which* link/aircraft it is — a hardware-unique ID + full air state | identity table in WaveRaider: per-aircraft HW-ID + (version, mode, rate, channel, hop, UID/sync) | Codex / Claude | ELRS sync word / UID + version + rate recovered **from the air** (no operator-supplied UID), CRC-validated, shown in UI |
| 3 | **EXECUTE** | Send a valid command/code wirelessly to the **owned bench** link | a gated TX path that forges a CRC-valid ELRS frame (and, later, other protocols) and is accepted by the owned receiver, bench-only, human-signed | Codex / Claude | the bench receiver accepts a forged frame with the right command; positive-command only; **explicit human sign-off + bench-only** before any TX |

Phases overlap (you keep improving DETECT/IDENTIFY while building EXECUTE),
but the *entry condition* for each gate is what defines "done."

---

## Phase 1 — DETECT

**End state:** WaveRaider shows, in real time, "a signal is present at
<freq>, PHY=<LoRa/GFSK/FHSS/802.11/Drone-ID>, protocol=<specific name> or
<family>, with confidence" — for whatever is in the air, without being told
the answer in advance.

### What already exists (ground truth, 2026-10-14)
- `elrs_sdr/detect.py` — the software port of the reference C-UAS SDR's
  5-stage pipeline (channelizer → hysteresis energy detect → sync
  correlation → per-protocol demod → Net-ID extraction). Has a synthetic
  selftest + runs on a real capture.
- `elrs_sdr/dronescan.py` — multi-band presence + protocol scanner.
- `elrs_sdr/positivedet.py` — PHY detectors (LoRa chirp, GFSK/FLRC); LoRa
  chirp is at **FAMILY** level.
- `elrs_sdr/protocol_catalog.py` — the **16-protocol target list** + honest
  `DetectionLevel` (`NONE→PRESENCE→FAMILY→SPECIFIC→IDENTIFIED`) per
  protocol. Run `python3 -m elrs_sdr.protocol_catalog` for the live gap
  report.
- `elrs_sdr/core/identify.py` — ELRS air-state classifier (measures burst
  physics, iterates all OTA v2–v6 × all rates, gates on CRC).
- `apps/droneid_json_rx.py` — DJI Drone-ID via the proven DroneSecurity
  receiver (runs in enclave on a sample). `apps/wifi_json_rx.py` — 802.11
  BSSID via gr-ieee802-11 (needs laptop build).
- `waveraider/` (Monitor → ELRS Find + spectrum overlay) — **the surface**.

### Phase 1 work items (Codex implements, Claude QCs)
- **P1-1 · Specific-protocol fingerprinting.** Close the `FAMILY→SPECIFIC`
  gap: distinguish ELRS vs Crossfire vs generic LoRa (and DSMx vs SiK) from
  payload/timing fingerprint (packet cadence, sync/CRC layout, symbol rate)
  — *not* just chirp presence. `positivedet.lora_chirp` is the placeholder.
  Evidence bar: VALIDATED on a real capture, labeled per §3 honesty.
- **P1-2 · Protocol-agnostic scanner loop.** Make the scanner walk the band
  and, for each burst, hand off to the right per-protocol demod, all fed
  into the same `Detection`/`NetID` dataclasses. No ELRS-only assumptions in
  the shared scanner/overlay path (leave room for DJI/ODI/etc.).
- **P1-3 · WaveRaider surface for all protocols.** The UI must render a row
  per tracked signal with protocol + confidence, honestly marking
  `unconfirmed` where no decoder exists. ELRS Find is today's surface;
  generalize the row model so a DJI/ODI/Wi-Fi detection renders the same
  way. A detector improvement not shown in the UI is not finished.
- **P1-4 · Proven-receiver bridges (Wi-Fi + DJI).** Build/run
  `wifi_json_rx.py` (laptop) + `droneid_json_rx.py` (enclave sample →
  laptop RF) as the webui subprocess bridges from
  `docs/DRONE_RF_ARCHITECTURE.md`. Each prints one JSON line per frame;
  webui merges into the airborne-ID table.
- **P1-5 · False-positive hardening.** Keep the three known FP classes
  (non-UID-specific correlation, LoRa CRC false-accept, dense-Wi-Fi preamble
  flood) gated in `identify.py`/`detect.py`. Every "hit" must survive them
  before it's shown as a real link (AGENTS.md §3).

**Gate P1→P2:** ≥ the 16 named protocols each reach ≥ `FAMILY` on a real
capture (or are honestly marked "no decoder → presence only"); ELRS reaches
`SPECIFIC`; the WaveRaider table shows all of it live.

---

## Phase 2 — IDENTIFY

**End state:** For each detected link, WaveRaider shows a **per-aircraft
hardware-unique ID** plus the full air state (version, mode, rate, channel,
hop behavior, recovered sync/UID) — **recovered from the RF, never
operator-supplied**, and CRC-validated.

### The identity model (from AGENTS.md §1 + docs/DRONE_RF_ARCHITECTURE.md)
- **Per-protocol ID:** ELRS → air-recovered sync word / UID; Wi-Fi →
  BSSID/MAC; DJI → serial/Drone-ID; ODI → serial/operator. A *link
  discriminator* (the UID-seeded sync) is **not** the same as a *hardware
  ID* — the doc is explicit about that, and the UI must show both and say
  which is which.
- **ELRS has no hardware ID on the air.** The recoverable passive identity
  is the UID-seeded sync/FHSS. Binding-phrase links: `UID = MD5(phrase)[:6]`
  (offline). 3-click (no-phrase) links (the operator's Tinyhawk): UID is
  random per-module, **recover-from-the-air only**.
- **The HW-unique ID (the operator's true target)** is MAC/IMEI-style —
  found by SDR-only 2.4 GHz RF (Wi-Fi BSSID, or the link's identity where it
  exists). This is the end-state identity the fusion layer
  (`elrs_sdr/fusion.py` → `TrackTable`) tracks across sources.

### Phase 2 work items (Codex implements, Claude QCs)
- **P2-1 · Air-recovery: read the sync word out of a real burst.** The next
  build step in `identify.py`: demod a clean burst (ch31/ch40 in the 120433
  capture) and **extract the sync word from the packet** — giving both
  modulation confirmation (FLRC vs Bluetooth) and the per-link discriminator
  **without the UID up front.** This is the keystone of "identify everything."
- **P2-2 · UID recovery hardening.** `core/fhss_seed.py` (NET_ID / seed
  recovery, already VALIDATED on multiple captures) + `lora_sync.py` —
  confirm the recovered sync yields a **CRC-valid frame at a consistent
  rate/channel** (the decisive real-hit discriminator, AGENTS.md §3).
- **P2-3 · Cross-source identity fusion.** `fusion.py`/`TrackTable`:
  identity-key a track from *all* sources (Remote ID + SDR scan + DJI
  Drone-ID) so the HW-unique ID persists across a link's hops and across
  protocols. The `webui.py` Dashboard is the merged view.
- **P2-4 · Full air-state report.** Surface the complete `AirState`
  (version, mode, rate, channel, freq-offset, hop) per link in the UI, with
  the evidence class (VALIDATED / SELF-CONSISTENT / SYNTHETIC) shown next to
  it — never a bare "found it."
- **P2-5 · Per-protocol Net-ID extraction** for the non-ELRS bridges
  (Wi-Fi MAC from P1-4, DJI serial from P1-4) — plug into the same table.

**Gate P2→P3:** for the owned ELRS link, the sync word/UID + version + rate
are **recovered from the air** (no operator-supplied UID), pass the CRC on
the recovered reference, and are shown in the WaveRaider identity table. At
least one non-ELRS ID (Wi-Fi BSSID or DJI serial) is shown live too.

---

## Phase 3 — EXECUTE  *(the staged, gated goal — current phase is still passive)*

**End state:** We can **forge a protocol-valid frame** and the **owned
bench receiver accepts it and acts on the command** — wirelessly,
bench-only, with **explicit human sign-off** for each TX. Positive commands
first (e.g. throttle up). Generalize the "forge a valid frame" step to
"whatever protocol the target speaks."

> **Scope reminder (AGENTS.md §0/§2, SECURITY.md):** TX is a *stated project
> goal*, deliberately **gated and bench-only for the current phase**. Any
> transmit, flash, or persistent device change needs **explicit human
> sign-off in that specific change**, on **andeds's own hardware, on the
> bench, radio present — never a live/in-the-air/third-party link.** The
> passive detector/analysis/web-UI paths must **never** wire in the TX side
> (a detection or UI flow reaching `tx_*` is a regression).

### What already exists
- `elrs_sdr/radio.py:880 tx_tune`, `:898 tx_stream` — **fully implemented
  UHD TX path** (direction-symmetric with RX). Not a stub.
- `elrs_sdr/cli.py:737 cmd_tx` — the (future, gated) TX command.
- The exact protocol ports (CRC, OTA model, UID-derived CRC init, channel
  pack, FHSS) are **VALIDATED** — the forge side reuses them.
- **Known weak point (QC-001):** the only gate is a **self-asserted CLI
  flag** (`--i-am-authorized-to-transmit`). That is *not* the sign-off
  mechanism the project wants — it is to be **replaced** (not removed) by a
  proper human sign-off when the TX phase begins.

### Phase 3 work items (Codex implements, Claude QCs — each needs sign-off)
- **P3-0 · Replace the TX gate with a real sign-off.** Before any TX:
  replace the self-asserted flag with a proper mechanism (e.g. a signed
  token / two-person rule / out-of-band human confirmation) + hard bench-only
  checks (radio-present + bench-SSID/owner assertion). QC-001 closes only
  when this lands. **No TX fires before this.**
- **P3-1 · Frame forge for ELRS.** Reuse the validated ports: build a
  CRC-valid RC frame with the **recovered UID + correct OTA version + rate +
  nonce** (the operator's Tinyhawk: UID 00:00:00:11:18:5B, 3-click, no
  phrase). Positive command first (throttle = 1000). Evidence bar:
  SELF-CONSISTENT (encode→decode round-trip) before any bench transmit.
- **P3-2 · Bench transmit + acceptance proof.** With human sign-off, on the
  owned bench: transmit the forged frame to the **owned Tinyhawk receiver**
  and prove it **accepts** (link-state/CRSF/arming feedback), not just that
  bytes went out. This is the moment the "execute" verb is real.
- **P3-3 · Generalize "forge a valid frame."** Abstract the forge step so
  other protocols (DJI, Crossfire, Wi-Fi injection where in scope) can slot
  in — same as DETECT/IDENTIFY, ELRS is the first instance. (Keep in mind
  the §2 boundary: each new protocol's TX needs its own sign-off +
  bench-only justification.)
- **P3-4 · "Code" rung (staged, later).** The operator's goal says
  "execute a code/command." Rung 1 = RC command injection (throttle/arm)
  via the ELRS link. Rung 2 = deeper: a malformed-frame / overflow surface
  in the receiver or flight-controller firmware → actual code execution.
  Rung 2 is a **separate, later** sub-project (owned hardware, bench-only,
  its own sign-off); it builds on Rung 1's forge capability. Do not conflate
  them in the UI or in scope claims.

**Gate P3:** the owned bench receiver accepts a forged, CRC-valid,
recovered-UID ELRS frame carrying a positive command, with human sign-off,
bench-only, and the result (accepted + acted) is demonstrated and logged.

---

## Standing ground rules (all phases, all agents)
- **One branch: `main`.** Pull before work, push after. Code + the notes
  entry for that change go in the **same commit** (andeds's rule).
- **Honesty contract** (AGENTS.md §3): label every finding VALIDATED /
  SELF-CONSISTENT / SYNTHETIC. A "detected"/"forged"/"accepted" without its
  evidence class is an overclaim.
- **Measure, don't assume** (AGENTS.md §1/§5): analyze at the *measured*
  sample rate (`.meta.txt`), recover the sync/UID from the air (never
  operator-supplied), keep it general (not single-target).
- **Stay passive for the current phase** (AGENTS.md §2): no TX/flash/
  persistent change without human sign-off; bench/owned only; the passive
  paths never reach `tx_*`.
- **Surface it in WaveRaider** (AGENTS.md §1): no capability is "done"
  until it's shown in the UI, honestly.
- **No secrets in code** (SECURITY.md): the bind UID never hardcoded in
  `.py`/fixtures/tests — runtime input or air-recovered only.

---

## Phase gate log (append here — the "growing" record)
<!-- Newest gate decision at the top. Format: date — gate — who — verdict -->
- **2026-10-14 — Phase 1 → (in progress).** Detector built + ELRS burst
  classified to `fsk_unidentified` (detected + measured, not yet
  air-recovered). LoRa demod still first-order (needs a real capture to
  validate). Air-recovery (P2-1) is the keystone next step. **Current phase
  is passive; TX remains gated (QC-001 open).**
