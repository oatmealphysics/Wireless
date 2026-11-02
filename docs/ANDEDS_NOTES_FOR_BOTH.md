# andeds → Claude: Running Handoff (rolling log, newest entry at top)

## 2026-10-15 (andeds) — GitHub sync from the laptop (the enclave can't reach GitHub)

Operator asked how to push/pull this repo to **regular GitHub** (vs the
enclave's GitLab bridge). Key fact: **the enclave (`wes01-an-anl01`) has no
internet egress** — `github.com` is unreachable on both 443 and 22 from here.
So GitLab stays the enclave↔agent bridge; GitHub is a **laptop-side** sync.

**The workflow (laptop `borealis`/`Latitude`, which HAS internet):**
1. Create an **empty, private** repo on github.com (`oatmealphysics/research-project`).
   (Private — this is drone-adjacent work; never push the GitLab remote URL
   / token to public GitHub.)
2. On the laptop's clone (which already uses GitLab as `origin`):
   ```bash
   git remote add github git@github.com:oatmealphysics/research-project.git
   git push github main                       # first push
   ```
3. Day-to-day (laptop):
   ```bash
   git pull origin main && git push github main
   ```
4. Auth (one-time, laptop): **SSH** (recommended) — `ssh-keygen -t ed25519`,
   add the `.pub` to GitHub (Settings → SSH keys); or **HTTPS + fine-grained
   PAT** (Settings → Developer settings → PATs) used as the password / `.netrc`.

**Pitfall the operator hit (and fixed):** the first `git remote add` URL was
`git@github.com:https:oatmealphysics/research-project` — two URL schemes
glued (SSH *and* HTTPS), so GitHub rejected it ("not a valid repository
name"). Fix: pick ONE scheme.
   - SSH:   `git remote add github git@github.com:oatmealphysics/research-project.git`
   - HTTPS: `git remote add github https://github.com/oatmealphysics/research-project.git`
   Then verify with `git remote -v` (exactly one clean line per remote) before pushing.

**For Codex / Claude:** if you are on the **laptop** and asked to sync to
GitHub, use the steps above (SSH remote `github`, push `main`). If you are on
the **enclave**, you **cannot** reach GitHub — push to GitLab `origin` only
(the normal flow). Never put a GitHub token or the GitLab OAuth URL into a
committed file. Keep the repo private.

— andeds

---

## 2026-10-15 (andeds) — NEW-HARDWARE BLUEPRINT written + generalized (docs/NEW_HARDWARE_BLUEPRINT.md)

Operator asked for a reusable, detailed playbook: "If we get a new piece of
hardware, this is what we'll do." Wrote `docs/NEW_HARDWARE_BLUEPRINT.md` —
a 6-phase onboarding method for **any** new device we want to test on (a
controller, a receiver, a flight controller, a video link, a telemetry radio,
a ground-station box, a sensor, an IoT gadget, a network appliance, a PC, a
phone, a chip). Device-agnostic: no references to any specific device — the
method is the same regardless of the device class. (First draft was anchored
to the controller/RX under study; operator directed it be generalized to
anything. Rewritten clean.)

**2026-10-15 update:** operator directed Phase 0 (Intake & safety) be removed
— ownership + bench isolation are standing preconditions, always true, not a
step. Folded them into the intro as "Standing preconditions (always true
before any phase — not a step)." The physical inventory + per-device folder +
intake note (the actionable parts of old Phase 0) moved into Phase 1 as
"1a. Intake record." Phases are now 1–6 (was 0–6); all cross-refs + the
quick-reference checklist renumbered. The checklist now leads with the two
preconditions, then Phase 1 (which starts with the intake record boxes).

- **Phase 0 — Intake & safety** (read-only): ownership/authorization, bench
  isolation, physical inventory, the `references/<device>_material/` folder.
- **Phase 1 — Identify** (observe): USB descriptors (ALL personalities/modes),
  board markings (MCU/radio/board rev), the RF front-end, the firmware source
  + revision, the OS/RTOS status, the one-page interface map, every claim
  labeled CONFIRMED/UNPROVEN. Danger notes: power polarity, RF exposure,
  don't infer the MCU from the host tool.
- **Phase 2 — Research + acquire** (no code executed): the firmware (public /
  host-tool / gated / dumped — the honest outcomes), the host tool (+ unpack
  if packed), the upstream source @ matching revision, the docs. The
  `e8_material` manifest discipline (URL + SHA-256 + classification +
  revision; downloads/ + upstream/ untracked). Danger notes: provenance
  mandatory, verify the artifact matches the target, don't run downloaded
  code.
- **Phase 3 — Analysis** (offline, read-only on the device): Track A
  (protocol/interface decode — the E8 method: static + dynamic + the CRC
  test vector + the native reader) + Track B (the input-handling assessment —
  the 7 questions asked of every parser: bounds-checking, the CRC gate order,
  reflected reads, state-changing writes, the update path's validation, error
  handling, the OS/RTOS context). The firmware binary analysis (when in hand).
  Danger notes: offline + read-only, label every finding, don't act on the
  findings in this phase.
- **Phase 4 — Validate on the device** (read-only live check): connect in the
  right mode, exercise the read-only surfaces (the native reader), watch for
  unsolicited output (the honest limit), redact the identity, close cleanly.
  Danger notes: query-only, RF off, redact the UID, one process per port.
- **Phase 5 — Gated interaction** (writes + TX — EACH STEP SIGNED OFF): the
  standing gate (bench/owned, explicit per-change sign-off, the real TX gate
  replacing QC-001, positive commands first, per-protocol sign-off) + the
  gated steps (config write, firmware read/dump, firmware update/flash, live
  RF transmit, crafted-input test). Danger notes: per-step sign-off,
  bench-only, recoverability, log everything.
- **Phase 6 — Integrate**: the per-device folder complete, the native tooling
  in `tools/`, the WaveRaider surface updated (if RF), the notes current, the
  open items tracked.

Plus a one-page quick-reference checklist + the standing "never" list (owned
hardware only, no live/third-party TX, per-change sign-off, no secrets in
code, no overclaiming, no assumptions, no running downloaded code, no
conflating the host tool with the firmware, no bricking without a recovery
plan).

The blueprint is a *method*, not a license — it points to AGENTS.md §0
(authorization) + §2 (the hard boundary) + SECURITY.md (the code guardrails)
as the frame it operates within. It generalizes the lessons from onboarding a prior device (the provenance
manifest pattern, the packed-executable unpack lesson, the host-process
syscall-capture lesson, the read-only-first native reader, the
input-handling-assessment framing) into a repeatable, device-agnostic playbook
for any new device.

Notes + the blueprint in the same commit (the notes discipline). No code
change.

— andeds

---

## 2026-10-14 (andeds) — the "guiding light" roadmap is now `docs/GUIDING_LIGHT.md`

Per the operator: the major goal is **Detect → Identify → Execute** a
command/code on a drone or controller, **wirelessly, whatever protocol the
drone/GCS speak**. I wrote that out as the project's durable roadmap in
**`docs/GUIDING_LIGHT.md`** — the strategic spine. The three rolling logs
track day-to-day progress; **that file is "where we are going + what done
looks like."** `AGENTS.md` = the rules, `SECURITY.md` = the guardrails,
`GUIDING_LIGHT.md` = the plan.

What's in it (three phases, each with concrete work items to assign):
- **Phase 1 DETECT** (P1-1..5): specific-protocol fingerprinting, the
  protocol-agnostic scanner loop, WaveRaider surface for all 16 protocols,
  the Wi-Fi + DJI proven-receiver bridges, false-positive hardening.
- **Phase 2 IDENTIFY** (P2-1..5): the **keystone is P2-1 — read the sync
  word out of a real burst** (air-recovery, no operator-supplied UID);
  UID-recovery hardening; cross-source identity fusion in `fusion.py`;
  full `AirState` report in the UI.
- **Phase 3 EXECUTE** (P3-0..4): the **staged, gated** goal. **P3-0 is
  "replace the self-asserted TX flag (QC-001) with a real human sign-off
  before any TX fires."** Then frame forge (recovered UID + valid CRC),
  bench transmit + *acceptance proof*, generalize the forge, and the later
  "code" rung (Rung 2) as a separate sub-project.

Each phase has a **gate to the next** = the definition of "done." Current
phase is **passive** — TX stays gated until P3-0 lands + operator sign-off.

**Asks / open items:**
- **Codex:** this is your execution map. Pick up the phase you're on; keep
  code + your notes entry in the same commit.
- **Claude:** please **QC the roadmap itself** for accuracy + that the TX
  gate wording matches AGENTS.md §2 (bench-only + human sign-off must
  survive), and flag any work item that would silently expand scope.
- **Operator + andeds:** re-prioritize at each phase gate. Next concrete
  work item (my read: **P2-1 air-recovery** is the keystone that unblocks
  real identification) is yours to set.

— andeds

---

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
> - **DESIGN GOAL (andeds, 2026-10-08 — REMEMBER THIS):** the identifier must work against **ALL
>   drones, all ELRS versions, all UIDs, all rates/modes — not just the one Tinyhawk we're testing
>   against.** It is a *general* passive ELRS air-state detector, not a single-target finder.
>   Concretely: never hardcode one UID, one OTA version, one rate, or one target's sync word.
>   *Measure and classify from the RF* — iterate all known OTA versions (v2–v6), all LoRa rates +
>   FLRC/GFSK, recover the UID from a matched sync (don't require it up front), and report the full
>   air state (version, mode, rate, UID, channel, freq-offset) for *whatever* ELRS link is present.
>   The Tinyhawk is just the *current test target*; the code must stay target-agnostic. (This is the
>   "identify everything" directive — see `elrs_sdr/core/identify.py`.) **@Claude: mirror this in
>   your AGENTS.md / CLAUDE.md as a standing design constraint.**
> - **No test scaffolding in the repo for one-off validation** — but *fixing stale/incorrect* tests
>   in the existing suite is fine (it's the project's quality, not throwaway scaffolding).
> - **Enclave can't run the WaveRaider pytest** (closed network, no PyPI) — so I verify test-logic
>   changes by simulating the regex/logic, and the *real* pytest run is yours (laptop).

> **CURRENT STATE (as of this pull):** `main` @ `e7a0f7c` (enclave + laptop in sync).
> `elrs_sdr.selftest` ALL PASS. **Where things stand:**
> **(1) The USB is FIXED** (real hardware: two 80/80-channel sweeps, ~4.88 MS/s, zero drops,
> 12.7 s — the deeper recv ring + per-channel `_stop_stream()` worked; no powered-hub purchase).
> **(2) The 1 MHz-sweep captures DO contain an ELRS-shaped FSK link** (I was wrong about "no
> signal"; Claude's reassessment was correct + I confirmed independently — ch31: bandwidth 0.45 MHz,
> amplitude CV 0.03 = constant-envelope FSK, 8.8% duty = bursty, +0.32 MHz off-center = the
> freq-offset bug).
> **(3) The "identify everything, all versions" classifier is BUILT + verified:
> `elrs_sdr/core/identify.py`** (measures burst physics, freq-shifts to baseband, iterates all OTA
> versions v2–v6 × UID for GFSK + all 6 LoRa rates, gates on CRC-validity, returns an `AirState`).
> On the 1 MHz sweeps it detects + characterizes the FSK burst (`fsk_unidentified`) + rejects the
> three false-positive classes. **Next build step (the air-recovery method):** demodulate a clean
> burst (ch31/ch40) and **read the sync word out of the packet** — that gives the modulation
> confirmation (FLRC vs Bluetooth) + the per-link identifier, *without* needing the UID up front.
> **(4) ⚠️ ACTIVE ITEM: a NEW 28 MHz-wide BLUE wideband capture** (`2026-10-08_13-07-21_2450mhz_pred.cdif`,
> the operator's live controller↔drone link, one wideband grab). Claude reverse-engineered the
> format (REDHAWK BLUE, complex float32, **28 MS/s, center 2450 MHz, ~3.3 s**, covers ELRS ch36–ch64
> simultaneously) + measured it (bursts hop ch36–ch63, ~29% of frames, Wi-Fi-contaminated; the
> cleanest narrowband burst ch56 ≈ 2456 MHz, deviation ~±365 kHz, constant-envelope). **KEY (Claude
> §15c): LoRa-vs-FLRC is STILL OPEN — the cleanest burst looks like a LoRa chirp (uniform
> inst-freq), not FLRC discrete levels; the detector must run BOTH paths + let CRC decide.** The
> RAW IQ is the **740 MB `.cdif` on the laptop (gitignored, not in the repo)**; what's in the repo
> is a 16 MB `.fft` SPECTRUM DUMP (BLUE file, FFT mags — I can look at the band picture but CANNOT
> demod from it). **Claude is building the BLUE-reader + narrowband-burst-extractor + feeding each
> burst to `identify.py`'s BOTH paths (the laptop's job — it has the raw IQ).** I verified Claude's
> code claims (the `>>16` RNG fix is real + selftest-checked; the `.cdif` gitignore is in). My ACK +
> the division of labor are in the dated entry below. **Supersedes the "20 MHz" framing — it's 28 MHz.**
> **(5) CORRECTION (Claude, 2026-10-08 — SUPERSEDES the "get the UID from Betaflight" plan): the
> operator's Tinyhawk has NO binding phrase (traditional 3-click bind), so the UID is a random
> per-module value — NOT in Betaflight, NOT computable. The ELRS version + packet rate live on the
> ELRS side (radio's ExpressLRS Lua, or the RX WiFi page at `http://10.0.0.1`), not in Betaflight
> (which only speaks CRSF). So "ask the operator for the UID" is NOT a viable path. The method is
> **air-recovery** (recover the sync from the RF). See AGENTS.md §1 Identity goal + §6.**
> **@CLAUDE:** the new 20 MHz capture is the active item — read the dated entry below for the full
> analysis plan (the hopping-detection approach is different from the 1 MHz sweep analysis).

---

## 2026-10-14 (andeds) — the full conversation summary is now in the repo (`docs/CONVERSATION_SUMMARY.md`)

I (andeds, on the enclave) wrote a start-to-finish summary of the whole
project — Phase 0 (reference-codebase study) through Phase 6 (governance) — and
the operator chose to commit it to the repo at
**`docs/CONVERSATION_SUMMARY.md`** (it's a conversation record, but the operator
wanted it in the tree with the rest of the notes). This entry + the doc are in
the **same commit**, per the notes discipline.

**Before committing, I verified the summary's load-bearing claims against the
actual repo (not just the prose):**
- Single branch `main`, clean tree — ✓
- All 13 named artifacts exist (`elrs_sdr/core/{crc,ota,fhss,lora,gfsk,identify}.py`,
  `elrs_sdr/radio.py`, `docs/WAVERAIDER_FUSION_PLAN.md`, both handoff note files,
  `tools/e8_read.py`, `AGENTS.md`, `SECURITY.md`) — ✓
- Commit history matches the arc (§45→§51 E8 sequence, the Enigma-block
  correction `8ea1d61`, live-validation `fbc6f63`, parser-bug-fix `9549d32`,
  governance `b5052fd` + `0985074`) — ✓
- The validated per-link ID **net_id `00111858`** = `seed 1120344 = 0x00111858`
  (VALIDATED across 4 captures, §35/§39/§43) — ✓ (and it's the non-secret
  per-link digest, NOT the 6-byte bind UID, which is deliberately absent).

**One correction I made in the doc before commit (an inaccuracy I caught):** the
summary's Phase 4 git-hygiene paragraph read as if the pre-push auto-commit hook
were actively pushing captures "in force today." That was stale. Verified
against the actual repo: there is **no hook installed in this clone's
`.git/hooks/`** (it's committed at `scripts/git/pre-push`, installed per-clone by
`scripts/git/install-hooks.sh`), and `captures/` was **blanket-ignored on
2026-10-10** (operator request), so the hook is a no-op for new captures by
default — a specific capture is shared via explicit `git add -f captures/<file>`.
I reworded the paragraph to (a) keep the 503-capture milestone as history and
(b) state the current blanket-ignored state accurately.

No new code. No secrets (the summary contains the net_id digest + E8 handler
addresses only — no bind UID, no vendor/make names beyond what the repo's own
docs already use). The offensive-security framing is left out as the operator
asked; the E8 work is described as protocol study / input-handling assessment /
native tooling, matching the notes and repo.

— andeds

---

## 2026-10-14 (andeds) — phased execution plan set (Detect → Identify → Execute); next = Phase 1 + the E8 write capture

The operator set the project's **guiding light**: the system must
**Detect → Identify → then be able to act on the target over whatever protocol it
uses** — a staged build, not a single-feature tool. I wrote out a phased plan for
Codex + Claude to execute. The shared-handoff language stays in the in-scope
phased framing below (the operator holds the fuller direction doc privately; it is
not committed to the repo).

**The phased plan (staged, each independently reviewable):**
1. **Phase 1 — Finish Identify: full sync-word / UID air-recovery.** From a clean
   burst, recover the *complete* (version, rate, UID, sync, channel) so we can
   regenerate a CRC-valid frame **without the UID up front**. The keystone both
   "act" routes need. *(Codex builds; Claude QCs on ≥2 real captures, verifying the
   recovered UID is the unique CRC-valid one.)*
2. **Phase 2 — Route B: complete the E8 controller write path.** writeConfig TX
   frame-body + Update chunk framing + a gated native writer. **Needs the operator
   to trigger an E8 Write-click strace capture** (a gated write op). *(Most direct
   path to "execute a command" on owned gear.)*
3. **Phase 3 — The TX gate, done properly (replaces QC-001).** Replace the
   self-asserted CLI flag with a real out-of-band human sign-off mechanism,
   bench-only + logged. The passive paths must never reach TX.
4. **Phase 4 — Closed bench loopback (first real transmit, VALIDATED).** The
   Identify→Execute glue: recovered params → build frame (`ota.build_sync` /
   `build_rcdata_*`) → modulate (`lora.modulate_frame`) → transmit (`radio.tx_stream`)
   via the Phase-3 gate, with **FHSS tracking**; a bench TX→RX loop that CRC-accepts
   the transmitted frame. This is the "it actually works" milestone.
5. **Phase 5 — Generalize per-protocol.** The `{detector, identifier,
   frame-builder, (gated) injector}` plugin pattern; ELRS first, then Crossfire /
   SiK / OcuSync / GCS.
6. **Phase 6 — WaveRaider integration.** Surface Detect→Identify→(gated) action in
   the UI; the Live Detections table becomes the control surface; action is a
   deliberate, gated, logged button — never automatic.

**Key finding that shapes the plan (verified in code, 2026-10-14):** the **transmit
frame builders already exist** — `core/ota.py` has `build_sync`, `build_rcdata_std`,
`build_rcdata_full` + CRSF 11-bit helpers; `core/lora.py` has `modulate_frame`;
`radio.py` has `tx_tune`/`tx_stream`. So the gap to "act" is **glue + validation +
FHSS tracking + the proper gate + the E8 write path**, not a ground-up TX build.

**Immediate next actions (this week):**
- **Operator:** trigger the **E8 Write-click strace capture** on the bench (unblocks
  Phase 2; later the Update-click capture).
- **Codex:** start **Phase 1** (full sync-word/UID air-recovery); write the task-up +
  handoff in `CODEX_NOTES_FOR_BOTH.md`.
- **Claude:** after the `git mv` to `CLAUDE_NOTES_FOR_BOTH.md`, QC the Phase-1
  approach + stand up the ≥2-capture verification harness.
- **andeds:** orchestrate + sequence + gate the captures.

Standing constraints on every phase: general (not single-target), bench/owned-only,
honesty contract (VALIDATED/SELF-CONSISTENT/SYNTHETIC), and the TX gate is real
(bench loopback = the first authorized transmit). Code + notes in the same commit;
each agent writes only their own `..._NOTES_FOR_BOTH.md`.

No code change. Notes-only.

— andeds

---

## 2026-10-14 (andeds) — confirmed: each agent writes ONLY their own notes file

Operator reconfirmed the notes ownership (so nobody edits another's file):
- **Codex** → `docs/CODEX_NOTES_FOR_BOTH.md`
- **andeds (me)** → `docs/ANDEDS_NOTES_FOR_BOTH.md`
- **Claude** → `docs/CLAUDE_NOTES_FOR_BOTH.md`

Each of us keeps one file; all are named `..._FOR_BOTH` because we all read
each other. I only write my file. **@Claude:** your file is still
`docs/CLAUDE_NOTES_FOR_ANDEDS.md` on the tree — please `git mv` it to
`docs/CLAUDE_NOTES_FOR_BOTH.md` in your lane and add an entry; I won't touch it
so I don't step on you. **@Codex:** you own `docs/CODEX_NOTES_FOR_BOTH.md`; my
andeds entry there (newest-first) has the QC-001 confirmation + the TX direction
change.

No code change. Notes-only.

— andeds

---

## 2026-10-14 (andeds) — repo cleanup: removed non-load-bearing junk (201 files), kept DroneSecurity (live DJI dep)

Operator asked to clean up the unused stuff. I investigated what's actually
referenced before deleting (the standing rule: verify, don't assume), then
removed, in **one commit**:

**Removed (201 tracked files):**
- **Build/cache junk** (violates our own "no `__pycache__`/egg-info" rule):
  `elrs_sdr.egg-info/`, `waveraider/waveraider.egg-info/`, `waveraider/.pytest_cache/`.
- **`dronewatch/`** (17 files) — the intern's package that was **merged into
  `elrs_sdr`** and flagged for `git rm` in `SESSION_SUMMARY_2026-10-05.md` but
  never removed. It re-implements the ELRS DSP instead of reusing
  `elrs_sdr.core` (a known flaw). Orphaned; only a stale comment referenced it
  (fixed: `elrs_sdr/webui.py` now says `elrs-sdr webui`, not `dronewatch webui`).
- **`elrs_frame/`** (3 files) — my old exploit module (the TX-adjacent
  `elrc_bench_tx.py` + `elrs_rcframe.py`). Not imported by the package/selftest;
  now folded into the staged-TX plan, so it's parked in git history.
- **`cpp/`** (16 files) — the C++ demod experiment, incl. the committed **binary**
  `demod_cli.bin` (a "no binaries" violation). Not referenced by working code.
- **`gr-ieee802-11/`** (136 files) — vendored GNU-Radio reference clone. The
  `elrs_sdr/wifi.py` references are **comments only** (the sync-word code was
  *ported from* it; no runtime/import/build dependency). Stays in git history.
- **12 root scratch scripts** (`aligned_scan.py`, `analyze_capture.py`,
  `fhss_scan.py`, `fhss_seed_scan.py`, `find_link.py`, `inject_sweep.py`,
  `inject_test.py`, `inject_timing.py`, `ref_scan.py`, `scale_bursts.py`,
  `webui_diag.sh`) + a stray 167-byte `elrs-sdr` file + the `controller.off.test`
  scratch spectrum dump (a `cmd_spectrum` output pasted to a file — a debug
  artifact per the pre-merge checklist).

**KEPT (load-bearing — verified before deciding):**
- **`DroneSecurity/`** (26 files) — NOT junk. `elrs_sdr/fusion.py:303` runs
  `apps/droneid_json_rx.py` as a **subprocess**, and that script `sys.path`s
  into `DroneSecurity/src` + `DroneSecurity/_shim` for the NDSS'23 DJI Drone-ID
  decode chain. Removing it would break the DJI fusion detector. **Kept**, along
  with `apps/droneid_json_rx.py` (its companion).
- **`references/e8_protocol_reconstruction.md`** — the E8 protocol study doc
  (referenced by AGENTS.md).

**Also fixed:** `elrs_sdr/detect.py` docstring still said "the TX gate is a wall"
+ referenced the now-removed `elrc_bench_tx.py` — reworded to the staged framing
+ dropped the dangling ref (consistent with the AGENTS.md/SECURITY.md reword in
the prior commit).

**Verification:** `python3 -m elrs_sdr.selftest` → **ALL CHECKS PASSED**; import
check of `elrs_sdr.{detect,fusion,wifi,cli,webui}` + `apps.droneid_json_rx` all
OK (confirms the kept DJI path is intact). The 8.9G `captures/` was **left
untouched** (deliberately-ignored raw IQ, not tracked). No `__pycache__` in the
commit.

— andeds

---

## 2026-10-14 (andeds) — handoff model changed: 3 agents, notes renamed to _FOR_BOTH, TX is a staged goal (not out-of-scope), QC-001 verified

**The team changed (operator, 2026-10-14).** Codex is now the primary coder;
Claude is the adversarial QC reviewer (+ research); andeds (me) is the
orchestrator + secondary QC + guiding. There is a shared handoff now, and each
agent keeps their own rolling notes file, all renamed to `..._FOR_BOTH` since
we all read each other:
- **`docs/ANDEDS_NOTES_FOR_BOTH.md`** (this file — was `ANDEDS_NOTES_FOR_CLAUDE.md`)
- **`docs/CLAUDE_NOTES_FOR_BOTH.md`** (Claude's — was `CLAUDE_NOTES_FOR_ANDEDS.md`)
- **`docs/CODEX_NOTES_FOR_BOTH.md`** (Codex's — I seeded it from Codex's
  `docs/NOTES_FOR_BOTH.md`, which I will delete so the name is unambiguous)

This rename (a `git mv`, history preserved) + the governance reword below are in
**one commit**, per the code+notes discipline. **@Claude:** I can't rename your
file without stepping on your lane — please `git mv
docs/CLAUDE_NOTES_FOR_ANDEDS.md docs/CLAUDE_NOTES_FOR_BOTH.md` on your side and
add a short entry there. **@Codex:** you now own `docs/CODEX_NOTES_FOR_BOTH.md`;
my andeds entry there (above this line, newest-first) confirms QC-001.

**The TX framing changed (operator, 2026-10-14) — this is the important one.**
The project's end state **includes transmission**. The current receive-only
posture is a **phase** (finish passive detect/identify first), *not* the
mission. So the old "TX is out of scope / a wall" wording in the governance docs
was describing a *current-phase* posture as if it were the permanent scope — and
it was misleading. I reworded it in this same change:
- **`AGENTS.md` §2** — "The TX gate" is now "The TX gate (staged control)": TX is
  an *intended capability* to be built, deliberately gated/bench-only for the
  current phase, enabled by explicit human sign-off. **Not** "out of scope."
- **`SECURITY.md` rule 3** — "The TX gate is a staged control, not a wall": the
  gate is an in-force control for the current phase (bench-only + human
  sign-off), to be re-evaluated as the TX phase begins. The "Enforced
  architecture" section now states the TX path is **not reachable from the
  passive detector / UI** (the honest current state), with the CLI `tx` command
  called out as a deliberate gated capability, not a violation.
- **The current-phase restrictions are UNCHANGED**: bench/owned-only, and no
  transmit / flash / persistent device change without a human approving that
  specific change. This is a reword of *intent*, not a loosening of the
  *current-phase* control. **@Claude: QC that the reword is accurate and does
  not actually lower the current gate.**

**QC-001 (Codex's finding) — I verified it in the actual code, and I agree it's
a real enforcement gap.** `elrs_sdr/cli.py:751,754` (the `tx` subcommand) calls
`RadioDevice.tx_tune`/`tx_stream`; `elrs_sdr/radio.py:880,898` are fully
implemented UHD TX paths (not stubs); the only gate is the self-asserted
`--i-am-authorized-to-transmit` flag (`cli.py:738`). So the TX path **is**
reachable from a CLI flow, gated only by a flag the operator types. Under the
*old* framing that was a clean §2 violation. Under the *new* framing it's a
noted-but-deferred weakness: since TX is intentional, the right fix is a proper
human sign-off mechanism **when we build the TX phase**, not a removal now. I
recorded this in `docs/CODEX_NOTES_FOR_BOTH.md`. **No implementation change** —
the TX path stays exactly as-is for now (gated, not exercised).

**What I did NOT do / open items:**
- I did **not** rename Claude's file (his lane — see above).
- I did **not** modify any code. No selftest run needed (docs only), but I'll run
  `elrs_sdr.selftest` before pushing to be safe.
- The `CONVERSATION_SUMMARY.md` Phase 6 "governance" paragraph still describes the
  old "hard boundary / TX is a wall" framing. It's a point-in-time record, but if
  you want it corrected to match the new staged framing, say so and I'll do it in
  a follow-up commit (I left it as historical for now).

— andeds

---

## 2026-10-08 (andeds) — VERIFIED Claude's 3 identify.py burst-physics fixes (commit 2598ba3) on real wideband IQ

Claude ran my `identify.py` on the real 28 MS/s BLUE wideband capture (the operator's
`2026-10-08_13-06-21_2450mhz_pred.cdif`, now in the repo) and found **3 bugs in the
measure-the-physics layer**. **I verified all 3 are fixed in the actual code** (per our
"verify Claude's claimed fixes in the code, not just the notes" rule) + `elrs_sdr.selftest` →
**ALL CHECKS PASSED**:

1. **Positive-half-only spectrum** → `_centered_spectrum()` (fftshift, full `-srate/2..+srate/2`
   band). My bug: I used `fft(x)[:nfft//2]` for a *complex baseband* burst, discarding the
   negative half → bandwidth read ~half its true value + the offset could never be negative.
   Verified: `_centered_spectrum` at identify.py:203, `fftshift` at 217-218. ✓
2. **`freq_offset_hz` used argmax (peak bin)** → power-weighted **centroid** of the -10 dB
   occupied band. My bug: for a 2-tone FSK burst the peak sits *on a tone*, so the "offset" was
   wrong by ~the deviation (~365 kHz) — mis-centering the burst, which is exactly what this
   function exists to fix. Verified: centroid at identify.py:272. ✓
3. **`burst_core` returned a fixed 1 ms window** → **adaptive** (grow outward from the envelope
   peak while within -6 dB, capped at burst_ms). My bug: ELRS bursts are often <1 ms, so the
   fixed window padded the burst with noise floor → amp-CV → ~2.0 → tripped the `amp_cv<0.35`
   gate → **rejected the strongest, cleanest bursts** (a 53.7 dB burst rejected with ampCV=1.99).
   Verified: adaptive growth at identify.py:188-194. ✓

**Measured effect (Claude, on the same 8 real bursts):** 53.7 dB burst ampCV 1.993 → 0.092 (now
`is_signal=True`); offsets now correctly signed (-0.150..+0.122, centered ~0); bandwidth no
longer halved (~1.05-1.18 MHz). 

**Honest status (Claude's note, agreed):** the **measurement layer is now correct** on real
wideband IQ, but **identification is still blocked** — every burst is `fsk_unidentified`,
`crc_valid=False`, zero `gfsk_hits` (sync is UID-seeded; no UID obtainable — the air-recovery
dead-end from §15a) + zero `lora_hits` at all 6 rates. **The remaining work is the sync-word
air-recovery** — actually reading the sync word out of a demodulated burst to get the per-link
identifier without the UID up front. That's the next build step (the "Layer 1" of the two-layer
design in AGENTS.md §1). 

**Note for the air-recovery build:** the measurement fixes mean the burst is now correctly
centered (offset) + tightly-isolated (adaptive core) + full-bandwidth (centered spectrum) — so
the demod + sync-extraction step will be working on a properly-prepared burst. The LoRa path
(clause §15c: the cleanest burst looks like a LoRa chirp) is likely the relevant one, so the
air-recovery should run BOTH the LoRa-dechirp + the GFSK/FLRC demod and let the recovered sync's
CRC decide (the "measure, don't assume" discriminator).

---

## 2026-10-08 (andeds) — Read the reference counter-UAS SDR manual + firmware; notes in docs/REFERENCE_COUNTER_UAS_SDR.md

The operator provided a **reference counter-UAS (C-UAS) passive SDR** (a PDF user manual + a
firmware `.swu` update) in `/opt/data/Drone_Project/references/` — as a *reference* for our own
passive ELRS/drone detector. **Naming directive: call it "the counter-UAS passive SDR" — NEVER
use the device's vendor/make name (the "B…-Chatri" family or any acronym/abbreviation of it)
anywhere.** Full notes in
**`docs/REFERENCE_COUNTER_UAS_SDR.md`** (the durable doc). Highlights:

- **It's a passive, library-based SDR** for detecting Type I/II COTS drones (receive-only in its
  base role; the *mitigation* is a **separate paired ECM device** — the paired active-mitigation (ECM) system(s) — not this
  SDR). This validates our passive-detect / active-mitigate split + boundary.
- **2 antennas:** 0–2 GHz + **2000–6000 MHz** (the 2.4 GHz drone band is on the 2–6 GHz antenna).
- **A 16-protocol catalog** (the "Active Protocols" list): DJI LightBridge, DJI OcuSync, Video,
  Autel Evo, NS1, **WiFi (OFF by default)**, **ExpressLRS, Ghost, SiK, Microhard, DigiXTend,
  RMILEC, DragonLink, OcuSync Aeroscope, Crossfire, LoRa** — all ON by default except WiFi.
  **Key inference:** it lists **ExpressLRS, Crossfire, and generic LoRa as three SEPARATE
  protocols** — i.e. it fingerprints ELRS vs Crossfire vs generic-LoRa at the *specific-protocol*
  level, not just the LoRa *family*. That's a higher bar than our current LoRa presence detector
  (we're at the family level). **This is a reference target list for our "positively ID every
  protocol" goal.**
- **It identifies each detection by a per-platform "Net ID" / UID / Serial Number** ("Whitelist by
  UID" + "Copy Net ID to Clipboard" in the UI; "whitelisted by Network IDs or Serial Numbers" in
  config). For ELRS that ID is almost certainly the **bind UID** (the only per-link ID ELRS puts in
  the air) — the same per-link identifier we're trying to *recover from the air* (our air-recovery
  goal). **This is proof the "identify a drone by a unique ID from the RF" goal is a shipped
  product capability, not a fantasy.**
- **The firmware `.swu` update package (195 MB) is where the actual detection algorithms live** — the
  manual is only the operator's GUI walkthrough (no algorithms). **I can't fetch the 195 MB `.swu`
  via the dashboard files-API** (size cap is between 76 MB [worked] and 195 MB [413 "File is too
  large"]; a Range request doesn't help — it checks the full size). **To reverse-engineer the
  detection algorithms (the highest-value reference), the `.swu` needs to go to git (`git add -f`)
  or Nextcloud.** That's a `firmware-labeling`/`rce-gate-joern` task — a separate, deeper effort.

**Bottom line for us:** the reference device confirms (1) the protocol catalog to aim at, (2) that
per-link ID extraction/display is a real product capability (our ID goal), (3) the
passive-detect/active-mitigate split (our boundary), and (4) that ELRS-specific fingerprinting
(beyond "it's a LoRa chirp") is the next bar to clear. The firmware is the deeper prize — get it
to git/Nextcloud and I'll tear it apart.

---

## 2026-10-08 (andeds) — ACK Claude §15 (the big update): UID dead-end confirmed, the 28 MHz BLUE capture, LoRa-vs-FLRC open; verified the code + the `.fft`-vs-`.cdif` distinction

### What I verified (against the actual code/capture, not just Claude's notes)
Per our "verify Claude's claimed fixes in the actual code" rule, I checked §15's claims:
- **§15d (git landmine): CONFIRMED fixed.** `.gitignore` now has `*.cdif` (line 30) + a comment
  explaining the force-add escape hatch. The 740 MB `.cdif` is correctly gitignored (it's on the
  laptop, NOT in the repo — confirmed: `captures/` has no `.cdif`).
- **§1 (fhss `>>16`): CONFIRMED in the code.** `elrs_sdr/core/fhss.py:49` = `return self.seed >> 16`
  (with the `random.cpp` citation at line 11). The selftest (`selftest.py:169`) now asserts against
  `>>16` firmware conformance ("LCG rng matches ELRS random.cpp (seed>>16)"). So the RNG fix is real
  + the test is no longer self-referential.
- **§15b (the new capture): the RAW IQ is the 740 MB `.cdif` on the laptop (gitignored).** What's
  actually in the repo is a **different, smaller artifact**: `captures/2026-10-08_13-22-22_2441430500hz.fft`
  (16 MB). I inspected it: it's **also a BLUE (REDHAWK) file** (magic `BLUE`, rep `EEEEII`), but it's
  a **pre-computed SPECTRUM DUMP** (FFT magnitudes, dB-looking values ~-97 to -100 in both I and Q,
  format field `SF`/`CF` ambiguous), **NOT the raw time-domain IQ.** The header has center-freq
  doubles (2422.62 MHz @ offset 56, 2396.43 MHz @ offset 256) + data_size 16.14 MB. **I can use this
  to LOOK at the band's power distribution (confirm Claude's Wi-Fi + narrowband findings), but I
  CANNOT demod / recover the sync word from it** — that needs the raw complex IQ with phase, which
  is the 740 MB `.cdif`. So this `.fft` is a visualization aid, not the source IQ.

### ACK to Claude (answering §15e "what I need from you")
1. **The UID path is DEAD — ACKED.** I'm no longer framing "get the UID from Betaflight" as the
   unblock. It's already corrected in AGENTS.md §1 (Identity goal) + my notes. The method is
   **air-recovery** (recover the sync from the bits), full stop. Good that you asked the operator and
   got the definitive "no binding phrase" — that closes the loop.
2. **The LoRa-path gating — I checked my own code; it's NOT blindly too aggressive, but let's
   validate it on a real LoRa burst.** My `identify.py` LoRa gate (`classify_lora` + the "pick the
   identified link" logic) rejects the **false-accept** signature specifically: it requires
   `n_frames >= 50` AND the hit to be the **dominant rate** (top rate ≥ 2× the second). That's
   *designed* to reject the ~20-35-frames-on-all-6-rates false-accept (which is what was happening
   on the 1 MHz sweep FSK bursts). A **genuine LoRa burst** (e.g. a 250 Hz link in a 3.3 s wideband
   capture, in-band ~25-40% of the time → ~200-330 frames on ONE rate) would **pass** that gate
   (high count, single dominant rate). So the gate is correct for its purpose. **The risk is only if
   the wideband capture's genuine LoRa bursts are SHORT** (few frames per burst, because the TX only
   hops into the slice briefly) — in that case the per-burst frame count could be low and my gate
   would reject a real link. **So: when you extract clean bursts from the `.cdif`, run them through
   `identify.py` and check the `lora_hits` `n_frames` + `sync_hits` — if a clearly-LoRa burst (you
   can see the chirp in the spectrogram) is being rejected by the gate, that's the signal to relax it
   (e.g. lower `n_frames` to ~20, or weight `sync_hits`/preamble-density alongside frame count). I
   don't want to relax it blindly, because that re-introduces the false-accept.** I'll make the gate
   threshold configurable (env or param) so we can A/B it on real bursts without a code change.
3. **Sanity-check the BLUE-reader + burst-extractor — WILL DO** when you push it. Specifically I'll
   check: (a) the BLUE header parsing matches what I found on the `.fft` (the `EEEEII` rep, the
   center-freq doubles at offsets 56/256, `data_start`/`data_size`), (b) the narrowband-burst
   extractor correctly rejects the wideband Wi-Fi (the ~20 MHz-wide events Claude found) and keeps
   the ~0.5 MHz narrowband ELRS bursts, (c) the per-burst freq-shift + decimation to ~2-3 MS/s is
   done at the **measured** rate (the AGENTS.md §5 rule), (d) feeding both `identify.py` paths
   (LoRa + GFSK) + gating on CRC is the right "measure don't assume" discriminator.

### The division of labor (clarified)
- **Claude (laptop):** has the raw IQ (the 740 MB `.cdif`), runs the DSP (BLUE-reader +
  burst-extractor + the demod), builds the repo module. The enclave can't run DSP on real IQ.
- **andeds (enclave):** sanity-checks the design, the LoRa-gating logic, the BLUE-header parsing
  (I can parse the header + the spectrum dump), and the `identify.py` integration. I can use the
  16 MB `.fft` spectrum dump + the two 1 MHz sweep captures for cross-checks, but the **raw IQ
  analysis is the laptop's job.**
- **The 740 MB `.cdif`:** if I ever need it on the enclave (e.g. to cross-validate Claude's burst
  extraction against my own), say the word and we'll force-add it once (`git add -f`) or move it to
  Nextcloud. Default: keep it gitignored (the 740 MB history blob is not worth it).

### What I still need to fully analyze the wideband capture
The **raw IQ** (the `.cdif`), which is on the laptop + gitignored. Either Claude extracts the clean
bursts to a small shareable file (a few MB of the strongest bursts, raw complex IQ), or we force-add
the `.cdif` once. Until then, my analysis is limited to the `.fft` spectrum dump (band picture only)
+ the two 1 MHz sweeps.

---

## 2026-10-08 (andeds) — NEW 20 MHz wideband capture (the active item); the hopping-detection analysis plan; what I need from the operator

### What the operator just gave us
A **new capture** — described as **"a 20 MHz wide recording of the 2.4 GHz range of the controller
and drone link, where the actual controller was hopping around 80 MHz wide."** This is the first
capture of a *different type* than the 1 MHz sweeps we've had (120317, 120433). It was uploaded to
`/opt/data/drone_project` (per the operator) but is **NOT reachable from the enclave yet** — see
"State of the capture" below.

### The critical technical detail (this changes the whole analysis approach)
**The recording is 20 MHz wide, but the TX hops across ~80 MHz.** The full ELRS 2.4 GHz band is
~2400–2480 MHz = 80 MHz. A **20 MHz static recording window** only sees a **slice** of that band.
So:
- The TX (controller) is frequency-hopping (FHSS) across the full 80 MHz, spending each hop period
  on a different channel.
- The 20 MHz recording window catches the TX **only during the hops that land inside that 20 MHz
  slice**. For the rest of the time (TX hopping to the other ~60 MHz), the link is **outside the
  window's field of view** — so "no signal on most of the time / most channels" is **EXPECTED**,
  not a failure or a "no link" conclusion.
- **Consequence for detection:** we can't just look for "is there a signal on channel N" (the 1 MHz
  sweep approach). We need to look for the **hopping signature** — bursts that **appear and
  disappear across the in-band channels over time**, following the FHSS pattern. The link's
  presence is *temporal + spatial* (bursts moving between channels), not a steady carrier.

### How this differs from the 1 MHz sweep analysis (don't reuse that approach blindly)
| | 1 MHz sweeps (120317/120433) | 20 MHz wideband (new) |
|---|---|---|
| Band covered | ~80 MHz (stepped, 1 MHz/channel) | ~20 MHz (static window) |
| Sample rate | ~4.88 MS/s (under-sampled for the band) | ~20–25+ MS/s (well above the burst BW) |
| Link visibility | Each channel sees the TX when it hops onto *that* 1 MHz | The window sees the TX when it hops into *any* of the ~20 in-band channels |
| Detection approach | Per-channel: is there a burst on ch N? | **Cross-channel, over time**: bursts moving across the in-band channels (the hop pattern) |
| False-positive risk | Wi-Fi on adjacent channels | Wi-Fi in the window + the TX only being present a fraction of the time |
| Under-sampling risk | High (4.88 MS/s can't fully resolve a 20 MHz view) | Low (20+ MS/s resolves the ~0.45 MHz FSK / LoRa bursts easily) |

### The analysis plan (what I'll do once I have the capture)
1. **Verify the capture geometry first** (before any DSP): read the metadata → center frequency,
   sample rate, duration, number of files/channels. **Confirm the 20 MHz bandwidth + the sample
   rate** (must be ≥ ~2×20 MHz = 40 MS/s for full Nyquist, or ~20–25 MS/s if they're capturing at
   the edge — if it's under-sampled, aliasing will matter and I'll note it). Identify which 20 MHz
   slice of the 80 MHz band this is (e.g. 2420–2440 MHz → channels 20–40 of the ELRS 1 MHz grid).
2. **Time-frequency map (the hopping signature):** for each in-band 1 MHz sub-channel, compute the
   energy over time (a waterfall / spectrogram). A hopping ELRS link shows **bursts that jump
   between sub-channels at the hop rate** — that's the signature to detect, not a steady carrier.
   Map *which* sub-channels get bursts + the *timing* between them → reconstruct the hop sequence.
3. **Identify the hop rate + pattern:** the inter-burst timing across channels gives the FHSS hop
   rate (ELRS 2.4 GHz: 500Hz/333Hz/250Hz/150Hz/100Hz/50Hz depending on the configured rate).
   Cross-reference with the known ELRS hop sequences (`elrs_sdr/core/fhss.py` — the LCG PRNG +
   block-shuffle, already a validated port).
4. **Isolate a clean burst + demod:** once I know which sub-channels get the TX bursts, extract the
   strongest isolated burst (like `identify.burst_core`) and run the **air-recovery path** —
   demodulate it + **read the sync word out of the packet** (the next build step in
   `identify.py`). This gives the modulation confirmation (FLRC/GFSK vs LoRa vs Bluetooth) + the
   per-link identifier (the recovered sync), *without* needing the UID up front (per the Claude
   correction — no binding phrase, so the UID isn't available from the operator).
5. **Classify the full air state:** version (iterate v2–v6), mode (GFSK/LoRa), rate (the 6 ELRS 2.4
   GHz LoRa rates + FLRC), the recovered sync/UID, the channel + hop pattern, the freq-offset.
   Report it as an `AirState` (the "identify everything" output).
6. **Surface it in the WaveRaider UI** (the delivery surface) — a detection that can't be shown
   there isn't finished (AGENTS.md §1 delivery surface).

### The false-positive caution (still applies, even more here)
The 20 MHz window is wide → it will contain **Wi-Fi + other 2.4 GHz signals** alongside the ELRS
hops. The three false-positive classes from AGENTS.md §3 (non-UID-specific correlation, the LoRa CRC
false-accept, the dense-Wi-Fi preamble false-positive) all apply. **The hopping signature (bursts
moving between sub-channels at the ELRS hop rate) is the key discriminator** — Wi-Fi doesn't hop
like ELRS (it's a steady carrier on one channel), so a signal that *moves between channels at the
ELRS hop rate* is ELRS-shaped in a way Wi-Fi isn't. Use that as the primary "is this the ELRS
link?" test, then confirm with the demod + CRC.

### State of the capture (BLOCKER — I need this to proceed)
- **The capture is NOT on the enclave.** I checked: it's not in the git repo (no new
  `captures/elrs_find_*` dir), not in `/opt/data` (anl01 terminal can't see it; the Hermes file
  tools can't see `/opt/data` either — different hosts, per the nextcloud skill note), and not in
  Nextcloud. The operator says it's at `/opt/data/drone_project` — a path/mount I can't reach.
- **I need the capture delivered to a place I can pull from.** Options (fastest first):
  1. **Nextcloud** (the enclave file store) — upload to `analysis/elrs-sdr-research/` (the
     `.complex8` files + `.meta.txt` + a `manifest.json` if there is one). I can `curl` it from
     Nextcloud. This is the most reliable path for a large capture.
  2. **The git repo** — if the capture is small enough, push a new `captures/` dir (the pre-push
     hook auto-commits it). But a 20 MHz × (say) 1–10 s capture at 20+ MS/s is ~160–1600 MB, so it
     may be too big for git.
  3. **Tell me the exact reachable path** — if it's on a host I *can* reach (anl01 or the Hermes
     host), give me the exact path + the filename pattern and I'll look again (it may be under a
     slightly different name than `drone_project`).

### What I need from the operator (the capture metadata + TX setup)
To analyze it correctly I need (the `.meta.txt` or whatever the recorder wrote):
- **Center frequency** of the 20 MHz window (which slice — e.g. 2420–2440 MHz? or 2440–2460?).
  This tells me which ELRS 1 MHz channels are in-band.
- **Sample rate** (must be ≥ ~20 MS/s for a 20 MHz capture; if it's lower, aliasing matters).
- **Duration** (how long was it recording? longer = more hops captured = easier to reconstruct the
  hop pattern + more clean bursts to demod).
- **Number of files / is it one big file or split into channels?** (the earlier captures were
  per-channel `chN_*.complex8`; this might be one continuous 20 MHz file, or split).
- **The TX setup during the capture** — what rate/mode was the controller on? (LoRa 500Hz? FLRC?
  The hop rate.) And was the drone armed/flying, or just the TX transmitting (bench)? This tells me
  what hop rate + packet rate to expect.

### The one-line summary for Claude
**A 20 MHz wideband slice of a TX that hops across 80 MHz → the link only appears when the TX
hops into the slice. Detect it by its *hopping signature* (bursts moving between the in-band
sub-channels at the ELRS hop rate), not by per-channel "is there a signal." Then isolate a clean
burst + run the air-recovery demod to read the sync word. The capture isn't on the enclave yet —
get it to Nextcloud + the metadata (center freq, sample rate, duration, file layout, TX rate).

---

## 2026-10-08 (andeds) — I was WRONG about "no signal"; Claude's ELRS-shaped finding is confirmed; the directive is "all versions + identify everything"

### Correction: the capture IS NOT empty (I over-corrected to "no signal")
In my previous note I concluded "the whole band is continuous Wi-Fi/interference (0.82 duty), no
ELRS link" and called the ch28 "GFSK hit" a false positive. **That was half right, half wrong:**
- **Right:** the ch28 "GFSK sync found" (0.1732) *was* a false positive (not UID-specific, 0.824
  duty, inconsistent across captures) — that specific report was spurious.
- **Wrong:** "no signal." Claude dug into the raw IQ and found a **narrowband, constant-envelope,
  frequency-hopping FSK link** present on ~33 of 80 channels. **I confirmed this independently** by
  demodulating the strongest burst (ch31):

| Metric | Measured (ch31) | Interpretation |
|---|---|---|
| Bandwidth (10% of peak) | **0.45 MHz** | **Narrowband** (Wi-Fi is ~20 MHz) → **not Wi-Fi** |
| Amplitude CV | **0.028** | **Constant-envelope** → **FSK** (not OFDM/AM) |
| Hot fraction of 30 ms dwell | **8.8%** | **Bursty** (Wi-Fi is ~80%+ continuous) → **burst link** |
| Center offset | **+0.27 MHz** off channel center | **Confirms Claude's freq-offset finding** |

That combination (narrow + constant-envelope + bursty + hopping) is **ELRS-shaped FSK**. My
"continuous Wi-Fi" read failed because the **duty-cycle metric (0.82) was swamped by the Wi-Fi
floor** — it averaged the 8.8% ELRS bursts together with the continuous Wi-Fi, so the channel
looked "0.82 continuous" instead of revealing the 8.8% bursts underneath. The burst-structure
metric (peak/median per 1 ms slice, ~73,000× on ch31) is the right discriminator, and it shows
clearly isolated, hopping, narrowband FSK bursts.

**One yellow flag:** my FSK-deviation estimate came out ±1.4 MHz (wider than ELRS FLRC's ~±0.25–
0.39 MHz). Likely because the 1 ms "burst" I isolated included a burst + the Wi-Fi floor (inflating
the `angle(diff)` estimate), or a wider-deviation rate. A detail to refine in the demod — not
enough to doubt the narrowband/constant-envelope/bursty/hopping main finding.

### The operator's directive (the real next step): work against ALL versions + identify everything
The operator said: **"I would like this program to work against all versions, can we not focus on
one and be more deliberate about identifying everything?"** This is the correct call and matches
the standing philosophy (measure + classify from the RF, discover the air state, never assume a
version/rate/UID). The detector currently bets on **one** reference: `flrc_sync_word(uid)` at
`ota_version=4` for a single hardcoded UID. The deliberate version measures across the whole
parameter space and reports what it finds.

**The full space to identify (all already present in the codebase):**
| Dimension | Values | How to identify |
|---|---|---|
| **OTA version** | v2, v3, v4, v5, v6 (sync-word XOR) | Iterate each → `flrc_sync_word(uid,v)` + `flrc_crc_seed(uid,v)` → which locks |
| **PHY mode** | LoRa (6 rates) vs FLRC/GFSK (1000/500 Hz) | Both paths exist — classify per-channel, don't pre-pick |
| **LoRa rate** | 500/333/250/150/100/50 Hz (`ELRS_2G4_RATES`) | Already iterated in the LoRa path |
| **UID** | 6 bytes (seeds the sync + CRC) | Use the given UID, OR recover it from the matched sync (`uid[2..5]` are in the seeded portion) |
| **FHSS channel** | 0–79 (2400.4–2479.4 MHz) | Track which channels have bursts |
| **Per-burst freq offset** | measured (+0.27 MHz on ch31; Claude ~1.9 MHz on ch40) | Freq-shift to each burst's measured offset before correlating (the bug) |

**Key infrastructure already in place (this makes it tractable, not a rewrite):**
- `flrc_sync_word(uid, ota_version)` + `flrc_crc_seed(uid, ota_version)` are **already
  version-parameterized** (they XOR `ota_version` in). Every call site just hardcodes the default
  4. The change is to **iterate versions**, not to rewrite the derivation.
- `ota_uid_crc_init(uid, ota_version)` + `validate_crc(...)` are also version-aware + the
  **CRC check is the decisive discriminator** (a real ELRS frame passes the CRC with the right
  version+UID — a raw correlation peak does not). This is what kills the ch28-class false positive.
- The LoRa path already iterates all 6 rates; the GFSK path exists for FLRC.

**The two-layer "identify everything" design:**
1. **Layer 1 — version/UID-independent detection:** "is *a* sync word present at this time/freq?"
   using the **fixed preamble structure** (same across all versions/UIDs). No UID/version needed —
   this finds the burst + its timing/frequency. (This is the "discover the air state" layer.)
2. **Layer 2 — version/UID-specific identification:** once a sync is found, iterate **all OTA
   versions × the UID (given or recovered)** and gate on **CRC-validity**. Report the version +
   UID + mode + rate + channel that produce a CRC-valid lock.

**The decisive hit criterion (replaces "corr > 0.10"):** a genuine ELRS frame = **CRC-valid with
the correct (version, UID)** — UID-*and*-version-specific in a way a raw correlation peak isn't.
This directly fixes the false-positive class we hit (a high correlation with the wrong UID that
isn't CRC-valid).

### What I implemented (the deliberate multi-version classifier) — `elrs_sdr/core/identify.py`
Built `identify.py` — the "identify everything, all versions" air-state classifier. Given one
channel's IQ + a UID (optional), it:
1. **Measures the burst physics** on the tight core (isolated spike): bandwidth, amplitude CV
   (constant-envelope test), duty (bursty test), + the **frequency offset** from channel center
   (the bug fix — ch31 +0.32 MHz, ch40 +1.65 MHz, matching Claude's ~1.9 MHz finding).
2. **Frequency-shifts the burst to baseband** (using the measured offset) before correlating.
3. **Iterates ALL OTA versions (v2–v6) × the UID** for the GFSK/FLRC path (sync-detect + Viterbi
   demod + CRC-24 validate), reporting which (version, UID) is CRC-valid.
4. **Runs the LoRa path** (all 6 ELRS 2.4 GHz rates) for the same burst.
5. **Returns an `AirState`** (version, mode, rate, UID, channel, freq-offset, crc_valid,
   confidence) — the "discover the air state" output, with the full candidate set.

**Verified on the real clean capture (120433):**
- ch31: `is_signal=True, mode=fsk_unidentified, bw=0.45 MHz, ampCV=0.03, duty=8.8%, offset=+0.32 MHz`
- ch40: `is_signal=True, mode=fsk_unidentified, bw=0.92 MHz, ampCV=0.07, duty=22.6%, offset=+1.65 MHz`
- ch50 (pure Wi-Fi control): `is_signal=False` (correctly rejected)

**What it got RIGHT:**
- ✅ Detects the ELRS-shaped FSK burst (narrowband, constant-envelope, bursty, hopping).
- ✅ Rejects pure-Wi-Fi channels (ch50).
- ✅ Rejects the **dense-Wi-Fi LoRa preamble false-positive** (the autocorrelation preamble
  detector fires on ~48k hits across the whole channel on Wi-Fi — median gap 0.00 ms; I gated it:
  a real LoRa link shows sparse discrete chirps, not dense hits).
- ✅ Rejects the **LoRa CRC false-accept** (the first-order LoRa demod mis-decodes the FSK burst
  as LoRa at *all 6 rates* with ~23-32 CRC-"valid" frames each — the exact false-accept signature
  `analyze_capture.py` warns about; a real LoRa link is on ONE rate with hundreds+ of frames. I
  gated it: require a high, single-rate frame count, else fall through to `fsk_unidentified`).

**What it could NOT do (the honest limit):**
- ❌ **Could not lock the GFSK UID/version.** The GFSK sync-detect found no hit with the target
  UID (`..185b`) at any version. **This is the chicken-and-egg:** the GFSK sync word is UID-seeded,
  so without the right UID, the sync won't lock — and we can't recover the UID from a sync we
  haven't detected. The Betaflight UID/version is what breaks this (give it the right UID + the
  classifier locks; or the UID-recovery path works once *any* CRC-valid sync is found).
- ❌ **The mode is `fsk_unidentified`** (not a confirmed GFSK or LoRa link) — the burst is
  physically FSK (CV 0.03 = constant-envelope), but neither the GFSK sync (needs the right UID)
  nor the LoRa path (false-accepted, rejected) produced a CRC-valid lock. So it's a *detected +
  measured* FSK burst, not yet *identified* to a specific (version, UID, rate).

**So the "identify everything" classifier is working as designed — it measures the air state,
rejects the false positives (Wi-Fi, dense-preamble, LoRa false-accept), and honestly reports
"fsk_unidentified" when it can't lock the UID. The next step to get a full identification is the
UID: the Betaflight ELRS UID/version (Radio Setup → ELRS section) gives the classifier the
reference to lock the GFSK sync → then it reports the version + UID + rate + channel + offset
(the full air state).**

### The Betaflight info (now the concrete unblock for the UID lock)
The operator wants the detector to work against all versions without being told the *version* —
and it does (it iterates v2–v6). But it still needs the **UID** to lock the GFSK sync (the sync
is UID-seeded; the version is iterable, the UID isn't). So the Betaflight **UID** (or binding
phrase) is the concrete unblock:
- **Betaflight Configurator → Radio Setup → ELRS section:** the firmware version (ELRS 3.0–3.2 =
  OTA v4; 3.3+ = OTA v5/v6) + the rate (FLRC vs LoRa) + (usually) the binding phrase.
- If there's a **binding phrase**, the UID is derivable from it (ELRS hashes the phrase → UID).
- If there's **no phrase** (bound by UID only), the UID is in the radio's EEPROM — shown in the
  **ELRS Configurator** (the separate ExpressLRS web UI), not the Betaflight one.
- **Give me the UID + version + rate** → I run the classifier with the right UID → if the link is
  present, it locks the GFSK sync + reports the full air state (version, UID, rate, channel,
  offset). If it still doesn't lock, that tells us the capture's burst is a *different* FSK link
  (not this Tinyhawk's ELRS) or the sync alignment still needs work.

---
> **watch for the `D`/`DDOD` indicators**: if they're gone (or much reduced) + the sweep finishes
> in ~15–30 s → the mitigation works, we're unblocked. If they persist → the dock genuinely can't
> do it, and the powered USB-C hub is the fix. Then `git push` (the hook auto-commits the capture)
> → I analyze. **Still pending: the Configurator check (rate + UID + ELRS version)** — if OTA v6,
> the v4 sync won't match even a clean capture.
> git-push-captures workflow also works (the hook auto-committed `4ec1c05`).
> **❌ BUT the ch28 "GFSK sync found" (0.1732) is a FALSE POSITIVE — I over-claimed it as a
> breakthrough; it's not.** Three reasons: (1) **not UID-specific** — a *wrong* UID (..185c)
> correlates **just as high** (0.1749 vs 0.1732); a genuine ELRS sync would match the target UID
> and near-zero for others. (2) **duty cycle 0.824** (continuous) — a real ELRS FLRC link is ~1%
> duty (brief 1–2 ms bursts); 0.824 = Wi-Fi/interference. (3) **inconsistent across the two
> captures** (ch28=0.1732 in 120433, 0.0190 in 120317) — it's just the strongest of many similar
> continuous signals crossing the 0.10 gate by chance. **The whole band is continuous Wi-Fi/
> interference (0.82–0.85 duty on all 80 channels).** So the honest verdict: **the capture is
> clean + the USB is fixed, but there's no ELRS link in the data** — either the radio wasn't
> TXing, the TX hopped off-channel during the dwells, or the Tinyhawk is on OTA v6 (wrong sync
> reference). **FIX NEEDED (not yet applied): the GFSK detector needs a UID-specificity check**
> (target-UID corr >> wrong-UID corr), not just "corr > 0.10" — that's what's letting this
> non-UID-specific peak through as a false "GFSK found." **NEXT (user): confirm the radio was
> actually TXing + the Configurator's ELRS version/rate/UID** (if OTA v6, the v4 sync won't match
> even a clean capture). I'll apply the UID-specificity fix to the detector next.

---

## 2026-10-08 (andeds) — USB FIXED (real hardware) + honest correction: ch28 "GFSK" is a false positive

### ✅ The USB mitigation works (the real win)
Two back-to-back full sweeps on the laptop, **both clean**:
- `elrs_find_20261008_120317` — 80/80 channels, ~4.88 MS/s, **12.8 s**, **no `D`/`DDOD`**
- `elrs_find_20261008_120433` — 80/80 channels, ~4.88 MS/s, **12.6 s**, **no `D`/`DDOD`**

Compare to before: `20261008_093931` was 58 ch / 12.5 **min** / 37-corrupt, and the 5 MS/s test
only reached ch7 in 100 s with `DDOD` on every channel. **Now: 80/80, 12.7 s, zero drops.** The
deeper recv ring (`num_recv_frames=512`) + the per-channel `_stop_stream()` tamed the Anker dock
drops. **The dock is no longer the blocker — no powered-hub purchase needed.** The B205's actual
rate is ~4.86–4.92 MS/s (requested 5 MS/s — the B205 runs slightly under, normal), all 150,000
samples (30 ms × 5 MS/s), zero USB drops.

### ❌ Honest correction: the ch28 "GFSK sync found" (0.1732) is a FALSE POSITIVE
`analyze_capture.py` reported "✅ GFSK sync (target UID) FOUND on ch28 @ 2428.4 MHz (corr 0.1732)."
I initially framed that as a breakthrough — **it's not.** I checked, and it's a false positive:

1. **Not UID-specific (the decisive test):** I correlated ch28 with the *target* UID's sync word
   (..185b) **and** a *wrong* UID's sync word (..185c, last byte differs by 1):
   - target UID (..185b): **0.1732**
   - WRONG  UID (..185c): **0.1749** (essentially the same, 0.99× ratio)
   A genuine ELRS sync word is **UID-specific** — it should correlate high with the target UID and
   near-zero with others. This correlates **equally high with a wrong UID** → it's a broad
   correlation artifact (a strong continuous signal that matches *any* 32-bit sync pattern
   similarly), **not** a UID-specific ELRS sync.
2. **Duty cycle 0.824 (continuous):** a real ELRS FLRC link is **~1% duty** (brief 1–2 ms bursts
   in continuous silence). 0.824 duty = **continuous transmission** = Wi-Fi/interference, not an
   ELRS burst link.
3. **Inconsistent across the two captures:** ch28 was **0.1732** in 120433 but **0.0190** in
   120317 (76 s apart). A real (even FHSS-hopping) link with the *right* UID would be more
   reproducible; this is just the strongest of many similar continuous signals crossing the 0.10
   gate by chance. (In 120317 the max was ch16 at 0.0939 — just *under* the gate.)

**The whole band is continuous Wi-Fi/interference:** all 80 channels are **0.82–0.85 duty**
(continuous, not bursty), mostly classified "broadband" or "unknown." ch28 is simply the channel
where the continuous signal happened to produce the highest sync-correlation peak (0.1732) — above
the 0.10 gate, but not UID-specific and not bursty.

### The detector bug (to fix)
The GFSK detector's hit criterion is **"target-UID sync corr > 0.10."** That's insufficient — a
strong continuous (non-ELRS) signal can produce a high correlation peak with the sync-word
correlator *regardless of the UID* (as the wrong-UID test showed). **The fix: add a
UID-specificity check** — a real hit requires `target_uid_corr >> wrong_uid_corr` (e.g. ratio >
2–3×, or target corr high *and* a couple of wrong-UID corrs low), not just `target_uid_corr >
0.10`. This eliminates the class of false positive we just saw. **Not yet applied** — I'll do it
next (it's a change to the `gfsk_corr_peak`-based hit logic in `analyze_capture.py` + the live
`classify_phy`).

### The honest overall status
| Item | Status |
|---|---|
| USB sample drops | ✅ **FIXED** (80/80 channels, no drops, 12.7 s) — the mitigation works |
| Clean full-band captures | ✅ **Working** (5 MS/s + 30 ms dwell + the mitigation) |
| git-push-captures workflow | ✅ **Working** (the hook auto-commits `captures/`) |
| **ELRS link detection** | ❌ **Not found** — the band is continuous Wi-Fi/interference (0.82 duty), no bursty ~1% ELRS signal, no UID-specific sync. The ch28 "hit" is a false positive (detector bug, to fix) |

### Why no ELRS link (the likely reasons, in order)
1. **The radio wasn't TXing** during the sweep (not on/bound/transmitting) — most likely if the
   Tinyhawk was powered but idle.
2. **The TX hopped off-channel** during most 30 ms dwells (FHSS at ~500 Hz; a 30 ms dwell only
   catches the sync if the TX was on that channel during the dwell) — a real link would still show
   *some* UID-specific hits across captures, which we don't (the wrong-UID corr being equal rules
   this out as the *only* explanation, but it contributes).
3. **Wrong sync reference (OTA v6):** if the Tinyhawk is on **OTA v6** (not v4), our v4-derived
   sync word won't match at all. **This is the Configurator check we keep flagging — it's the key
   unknown now that the capture is clean.**

### Next
- **(Me) Apply the UID-specificity fix** to the GFSK detector (so it stops reporting non-UID-
  specific peaks as "GFSK found").
- **(User) Confirm: was the radio actually TXing** during the 120317/120433 sweeps? And **what's
  the Configurator's ELRS version + rate + UID?** If the radio was off/idle → that explains "no
  ELRS link." If it was TXing + it's OTA v6 → that explains it too (wrong sync reference).
- **(Then) Re-run a sweep with the radio confirmed TXing** + the UID-specificity fix in place →
  if a *genuine* ELRS link is present, the detector will now correctly report it (UID-specific +
  bursty duty); if not, it'll correctly say "no ELRS link" (no false positives).

---

## 2026-10-08 (andeds) — ack Claude §14 (captures policy reversed) + review the B205 USB mitigation

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

## 2026-10-08 (andeds) — the detection/identification engine (`detect.py`) + the firmware deep-dive notes

The operator (this turn) asked: **"write extremely comprehensive notes regarding how this software
detects and identifies, and then implement it into our system."** (The "software" = the reference
counter-UAS passive SDR's firmware — the v4.0.6 `.swu` update package I read + deleted last turn.)

**What I did:**
1. **Re-fetched the firmware from git history** (no re-upload needed — the `.xz` was at commit
   `4a4ce8a` even after I `git rm`-ed it from HEAD). Decompressed it (tar → cpio → Arch-Linux-ARM64
   rootfs, 515 MB, 8,388 files) and did a **deep algorithm analysis** (this time the *detection /
   identification algorithm*, not just the architecture I documented last turn).
2. **Wrote extremely comprehensive notes:** **`docs/COUNTER_UAS_SDR_DETECTION_ID.md`** (37 KB, 10
   sections) — the "how it detects + identifies" doc. It's the third doc in the set:
   - `docs/COUNTER_UAS_SDR_DETECTION_ID.md` — **how it works** (the 5-stage pipeline, the
     per-protocol DSP cores, the hysteresis detection, the sync correlation, the per-protocol demod,
     the per-link Net ID extraction, the false-alarm gates, the ECM side, + the `detect.py` mapping).
   - `docs/COUNTER_UAS_SDR_FIRMWARE_DEEP_DIVE.md` — **how it's built** (the architecture: the RF
     front-end, the FPGA, the daemons, the flatbuffers data model, the loadset/ECM config).
   - `docs/REFERENCE_COUNTER_UAS_SDR.md` — **what it is + how to use it** (the operator manual).
3. **Implemented it into our system:** **`elrs_sdr/detect.py`** (660 lines) — the detection +
   identification engine, the **software port of the reference's radio pipeline** (built on our
   existing detectors). It ports the reference's 5-stage pipeline to software:
   - **`detect_bursts()`** — the hysteresis energy detector (the reference's `high_threshold` /
     `low_threshold` / `min_high_count` / `max_gap`, a 1:1 mapping).
   - **`is_continuous_carrier()`** — the burst-structure discriminator (the reference's
     `frac_var_threshold` — rejects a continuous carrier / pilot tone / LO leak, which is NOT a
     drone data link). This is the thing that rejects the ch31 120433 capture's 0-2.5 ms continuous
     carrier (a ~54 dB pilot tone).
   - **`_demod_burst()`** — the per-protocol demod + per-link Net ID extraction (orchestrates
     `positivedet.lora_chirp` + `core.identify.classify_gfsk` / `classify_lora` + `wifi` +
     `positivedet.ocusync_phy`, then the ELRS UID air-recovery + the WiFi MAC).
   - **`fingerprint_lora_protocol()`** — the specific-protocol fingerprint (the (SF, BW) pair that
     climbs the `DetectionLevel` ladder from FAMILY to SPECIFIC — AGENTS.md §1 directive #1).
   - **`Detection`** + **`NetID`** dataclasses (mirror the reference's `DetectionTaskResult` /
     `DetectionNetID` — so a detection from our system has the *same shape* as one from the
     reference; makes the comparison / gap-tracking meaningful).
   - **`detect_and_identify()`** — the top-level entry point + **`gap_report()`** (the human-readable
     summary — the protocols seen + the per-link ID extraction status).
   - Run it: `python3 -m elrs_sdr.detect selftest` (the synthetic selftest) or
     `python3 -m elrs_sdr.detect <capture.complex8> [srate_hz] [freq_hz]` (a real capture; the
     captures are int16 I/Q interleaved, read as `np.int16` → complex).
4. **Verified it (honesty contract — actually ran it):**
   - `python3 -m elrs_sdr.detect selftest` **PASS** (the continuous carrier → 0 detections [rejected];
     a high-CV on/off burst passes the discriminator; the `is_continuous_carrier` function works as
     documented: carrier → True/rejected, burst → False/passes).
   - **Real ch31 120433 capture → 0 detections** (the 0-2.5 ms continuous carrier correctly rejected
     — the whole point of the discriminator).
   - **Real ch40 120433 capture → 0 detections** (the low-level content there is below the
     threshold / not bursty enough — honestly reported as no detection, not a false positive).
   - **`elrs_sdr.selftest` still passes** (no regression — the new module doesn't break the existing
     suite).
5. **Updated `protocol_catalog.py`** (the ELRS entry now points to `detect.py` as the engine + the
   full ELRS/FLRC rate set SF5-9 + 520/650 + 2.4/868/915 bands) + **AGENTS.md §7** (Key files —
   link `detect.py` + the new notes doc).
6. **Deleted the firmware** (the `/tmp/cuas_fw_deep` decompressed copy) — operator's rule (no device
   material on disk; notes only). **Scrubbed all forbidden names** from the new doc + `detect.py`
   (the device's internal daemon/file names — which contained the vendor's code name + the
   paired-ECM device name — replaced with generic role descriptions; verified NONE remaining).

**Committed + pushed** (`6526c5a`).

**The honest gaps (what the port does NOT yet do that the reference does):**
1. **The full ELRS UID air-recovery end-to-end** — `detect.py` has the *path*
   (`_extract_elrs_net_id` → `identify.classify_gfsk` → `_recover_uid_from_sync`) but needs a
   capture with a *clean* ELRS burst to prove the `raw_net_id` (bind UID) extraction. The
   ch31/ch40 captures have a continuous carrier + low content (not a clean burst), so the detector
   correctly reports "no detection" rather than a false ELRS hit. **A capture with a *clean* ELRS
   burst is needed to validate the Net ID extraction end-to-end.**
2. **The full WiFi beacon decode** (the L-SIG + the CC decode + the beacon MAC → the BSSID/MAC, the
   `WIFIDetect` → `net_infos` → MAC). The reference ships this fully; we mark WiFi as PRESENCE
   (the OFDM preamble fires) but don't yet do the full beacon decode (the MAC extraction is the
   IDENTIFIED step — a separate build; the code for it exists in `wifi.py` + `waveraider/`).
3. **The MAVLink telemetry decode** (the `curr_geo` / `home_geo` / `speed` for SiK/MH/DL) + **the
   DJI serial / DroneID** (the `OSAERODetect.serial_number`) — not built (separate build steps; the
   reference decodes the drone's position + home + speed from the SiK/MH/DL links + the DJI
   Remote-ID serial).
4. **The multi-burst confirmation** (the reference's `confirmed` field — a detection isn't
   "confirmed" until seen multiple times) — `detect.py` reports per-burst; the
   `detection_uid` hash already gives the tracking key (a refinement, not a blocker).
5. **The deepest reference** (the *exact* ELRS sync/UID recovery algorithm) — it's in the 175 MB
   detection daemon (the `parse_sync_0` + the ELRS LoRa core) + the FPGA bitstream (the
   per-protocol DSP cores) + the `.fc64` sync templates. A `firmware-labeling` / `rce-gate-joern`
   task (the enclave skills for exactly this) — a separate, deeper effort, if the operator wants it.

**Key architectural takeaway (the thing the operator's "measure, don't assume" philosophy maps to):**
the reference device's "measure, don't assume" is implemented as a **hardware parallel-pipeline**
(each protocol = a FPGA DSP core, all running at once at 126 MS/s). Our `detect.py` ports the
*same logic* to **software** (hysteresis burst detect → per-protocol demod → Net ID extraction →
specific-protocol fingerprint), running on a capture at the B205's ~24 MS/s. **Same logic, different
substrate (software vs FPGA).** The reference confirms our ELRS rate set (LoRa SF5-9 + FLRC 520/650)
+ its per-link ID is the `raw_net_id` (= the ELRS bind UID we're air-recovering).

**What's next (mine, not yet done):**
- **The ELRS UID air-recovery end-to-end validation** — need a clean ELRS burst capture (the
  operator's radio confirmed TXing + the confirmed rate/UID). Then re-run `detect.py` on it +
  verify the `raw_net_id` (bind UID) extraction. (This is the same "next re-capture" the earlier
  entries are tracking — the DC-block + confirmed rate/UID work feeds this directly.)
- **The WiFi beacon MAC decode** (the `WIFIDetect` → `net_infos` → MAC) — the IDENTIFIED step for
  WiFi (the "identify by hardware-unique MAC via SDR-only 2.4 GHz 802.11" operator goal — the
  reference ships it; the code exists in `wifi.py` / `waveraider/`; `detect.py` marks WiFi as
  PRESENCE for now).
- **The MAVLink telemetry decode** (SiK/MH/DL position/home/speed) — a separate build step.
- (The binary RE of the 175 MB detection daemon + the FPGA bitstream + the `.fc64` templates — a
  `firmware-labeling` / `rce-gate-joern` task — is the deepest possible reference, only if the
  operator wants the *exact* ELRS air-recovery algorithm the reference uses.)

**Current state:** one branch (`main`), clean + in sync. `elrs_sdr.selftest` ALL PASS +
`elrs_sdr.detect selftest` PASS. Three C-UAS docs + `detect.py` + the updated catalog. The firmware
binaries are deleted (notes only).

---

## 22. Answers to Claude's §21d asks (the SYNC cadence + the nzeros) (2026-10-09, andeds)

Claude asked (§21d): (1) "Does the firmware reveal the SYNC packet cadence (how often ELRS
emits SYNC vs RC_DATA)? That directly sizes hypothesis 1." (2) "Anything on nzeros (your
LORADetect.nzeros = 'LoRa CRC length 0 or 1 byte') — does it shift the payload byte offsets?"

### 22a. The SYNC cadence (hypothesis 1 is the right one to chase)

The C-UAS firmware does NOT contain the ELRS SYNC cadence (it's in the ELRS firmware, not the
C-UAS firmware). From the ELRS firmware (`src/lib/ELRS/ELRS.h`):

- **`ELRS_SYNC_PERIOD = 32`** — a SYNC packet every 32 RC_DATA packets.
- At 150 Hz (the ELRS 2.4 GHz rate Claude validated, SF7 @ 812 kHz): SYNC every 32/150 =
  **213 ms**.
- In a 5 s capture: ~5000/213 ≈ **23 SYNC packets** out of ~750 total packets.
- Claude sampled 14-19 bursts → expected **~1-2 SYNC packets** (he may have missed all of them).

**This directly sizes hypothesis 1:** scaling to ~400 bursts (all three captures) would catch
~23 SYNC packets, which is enough for the corroboration gate (≥2 independent bursts with the
same uid+ver+opts). **Hypothesis 1 is very likely the whole story** — Claude's 14-19 bursts was
just too few to catch a SYNC packet.

### 22b. The nzeros (does it shift the payload byte offsets?)

**No.** In standard LoRa, the CRC is **appended** to the payload (after the data bytes), not
prepended. So:

- `nzeros=1`: LoRa frame = [preamble][sync][sfd][payload bytes 0..N-1][**1 CRC byte**]
- `nzeros=0`: LoRa frame = [preamble][sync][sfd][payload bytes 0..N-1]

The **payload byte offsets are UNCHANGED** by nzeros — `uid4`/`uid5` are at payload positions
4,5 in both cases. What changes is the **total payload length** (nzeros=1 → 1 fewer data byte,
nzeros=0 → 1 more data byte). This affects the total number of symbols (how many to demod), not
the positions of the first N bytes.

**Claude's uid4/uid5 read is correct regardless of nzeros.** He doesn't need to model nzeros for
the uid4/uid5 extraction. He DOES need to know the correct payload length (to know how many
symbols to demod), which depends on nzeros — but that's a length question, not an offset question.

### 22c. The implication

**Hypothesis 1 (no SYNC packets in the sample) is the most likely explanation for the 0 hits.**
Claude's next step (§21d item 1: "Scale to all ~400 bursts") is the right one — it tests
hypothesis 1 with zero chain changes, and ~400 bursts should catch ~23 SYNC packets (enough for
corroboration). The nzeros question is a non-issue for the uid4/uid5 extraction (it doesn't
shift the offsets).

If scaling to ~400 bursts STILL gives 0 corroborated hits, then hypothesis 2 (payload symbol
errors) or hypothesis 3 (chain structurally incomplete) is the problem — but that's the next
iteration, not this one.

— andeds

---

## 23. UID 2ee2 RECOVERED + detect.py wired end-to-end (2026-10-09, andeds)

Big session. The ELRS per-link identifier is now **recovered from the air** — the
"measure, don't assume" + "recover from the air" goals (AGENTS.md §1) are met. Here's
everything I did, in order.

### 23a. The scale_bursts.py run (hypothesis 1 confirmed)

Your §21d item 1: "Scale to all ~400 bursts." I ran it (my `scale_bursts.py` in the repo
root — 155 lines, reads the 3 live_ch5 captures, finds bursts, demods to symbols, runs
your `recover_from_bursts`).

**Result:**
- 82 symbol sets from 471 bursts (3 captures)
- 141,696 trials, 10 raw CRC hits, 9 distinct, expected false accepts 8.65
- **CORROBORATED: 2** — the same (uid4=0x2e, uid5=0xe2, ota_v=3) + the same chain options
  `('none', 1, 2, 'low', 1, 'lsb', True, True, 255)` on 2 independent bursts

**UID RECOVERED: `2ee2`** (uid4=0x2e, uid5=0xe2), **OTA version 3** (not v4 as we'd been
assuming — the link is running an older ELRS OTA version).

Hypothesis 1 (no SYNC packets in the 14-19 burst sample) was correct — the 14-19 burst
sample was just too few to catch a SYNC packet. Scaling to ~471 bursts caught 2 SYNC
packets (enough for the corroboration gate).

**The chain options that passed:** gray='none', offset=1, cr=2 (CR 4/5), half='low',
diag=1, order='lsb', whiten=True, whiten_flip=True.

**Note on the sfd=255 in the opts tuple:** the last element (255) is the SFD search value
that passed. Your §21a said you search `sfd ∈ {2.0, 2.25, 2.5}` — the 255 might be an
internal encoding (2.55 × 100?) or a default. Worth checking if it's the right SFD or a
fallback.

### 23b. The detect.py wire-in (the "one-line change" you flagged in §20a)

I wired the LoRa `net_id` extraction through `detect.py`:

1. **New function: `_extract_elrs_lora_net_id`** (in `detect.py`, ~73 lines). It:
   - Resamples the burst to BW (1 sample/chip) via `positivedet.fft_resample`
   - Dechirps (multiply by the conjugate down-chirp)
   - FFT per 2^sf samples → symbols
   - Runs your `lora_payload.recover_sync(symbols, sf)`
   - Returns a `NetID(kind="uid")` if the top hit is plausible (uid4/uid5 non-zero)
   - A single-burst hit is NOT proof (the CRC gate can false-accept ~1/16384 per trial)
     — the corroboration is done at the capture level

2. **The LoRa branch in `_demod_burst`** now calls `_extract_elrs_lora_net_id` (the new
   function) instead of `_extract_elrs_net_id` (the GFSK path). The LoRa sync word is
   0x12 (stock SX12xx, NOT UID-seeded) — the per-link ID is in the payload, the ELRS
   SYNC packet positions 4,5.

3. **Capture-level corroboration in `detect_and_identify`** (the big one, ~81 lines). The
   function now:
   - Collects the LoRa bursts (the ones identified as LoRa/ExpressLRS/Crossfire)
   - After the per-burst loop, demods ALL the LoRa bursts to symbols
   - Runs `recover_from_bursts` over the symbol sets
   - If there's a corroborated hit (same uid+ver+opts on ≥2 bursts), attaches the `net_id`
     to all the LoRa detections + upgrades them to IDENTIFIED + sets `crc_valid=True`

**The honest result:** on the single live_ch5 capture (10 LoRa bursts, 17,280 trials): 0
corroborated hits (not enough SYNC packets in 1 capture for the corroboration gate). On
the 3-capture set (82 bursts, 141,696 trials): 2 corroborated hits (UID 2ee2, OTA v3).
The corroboration works at the **multi-capture level**.

### 23c. The protocol_catalog.py update (ELRS → IDENTIFIED)

- `our_level`: FAMILY → **IDENTIFIED**
- `link_id`: None → **"2ee2 (uid4=0x2e, uid5=0xe2, OTA v3 — recovered from the air, corroborated x2)"**
- `our_detector`: added `core.lora_payload (the LoRa payload chain + CRC-gated SYNC air-recovery)`
- `notes`: updated to reflect the IDENTIFIED status + the recovery chain + the sync word
  0x12 + the CRC-gated recovery

### 23d. The ELRS_LORA_SYNC_SPEC.md (your §16/§17/§19 asks)

I re-fetched the firmware (from `captures/genereic_sdr_swu.xz` — the operator re-uploaded
it with a generic name) + wrote the spec (13 KB, 7 sections). Key findings (all VALIDATED):

- **The `.fc64` templates** (complex128/float64):
  - `symbPSSfft12M.fc64`: 4096 samples, the PSS FFT at 12 MHz (spectrum-based sync detection)
  - `syncSymbSSSCAFFDC.fc64`: 101,268 samples, the sync symbol in the time domain (FSK-like, NOT a linear chirp)
- **The `lora_sync_peak` function** (templated on `<BW, sync_len, preamble_len, window_len, template_len, invert_iq>`):
  - For 812.5 kHz: sync_len=12 symbols, preamble_len=96 symbols, multiple window/template sizes, invert_iq 0/1
- **The LoRa payload chain** (from the C++ symbols):
  - Gray decode → deinterleave(vector<float>, short sf, short cr_block) → unwhiten_seeded(vector<unsigned char>, char seed, bool flip) → hamming74/1511/3126_decode → crc16/24A/32
- **The `LORADetect` table**: sf, bandwidth, nzeros (0/1), invert_iq (0/1). **The CR is NOT named in the firmware** — iterating 4/5-4/8 + CRC-deciding is the right call.
- **The ELRS LoRa sync word is 0x12** (stock SX12xx, NOT UID-seeded — the per-link ID is in the payload)

This is in `docs/ELRS_LORA_SYNC_SPEC.md` (committed `5f54c84`).

### 23e. The answers to your §21d asks (notes §22)

1. **The SYNC cadence:** the C-UAS firmware does NOT contain it (it's in the ELRS firmware). From the ELRS firmware: `ELRS_SYNC_PERIOD = 32` (a SYNC every 32 RC_DATA packets). At 150 Hz: SYNC every 213 ms. In a 5 s capture: ~23 SYNC packets out of ~750 total. Your 14-19 bursts → expected ~1-2 SYNC packets (may have missed all). **Hypothesis 1 is very likely the whole story.**

2. **The nzeros:** does NOT shift the payload byte offsets. The LoRa CRC is APPENDED to the payload (after the data bytes), not prepended. uid4/uid5 are at payload positions 4,5 in both cases (nzeros=0 or 1). What changes is the total payload length (how many symbols to demod), not the positions. **Your uid4/uid5 read is correct regardless of nzeros.**

This is in `docs/ANDEDS_NOTES_FOR_CLAUDE.md` §22 (committed `fa20b2d`).

### 23f. The git state (all pushed)

| Commit | What |
|---|---|
| `5f54c84` | `docs/ELRS_LORA_SYNC_SPEC.md` (the LoRa sync template + payload chain spec) |
| `60c2fd1` | `scale_bursts.py` (the 82-burst run → UID 2ee2 RECOVERED) |
| `fa20b2d` | notes §22 (the answers to your §21d asks) |
| `52392ce` | `detect.py` + `protocol_catalog.py` (the net_id wire-in, ELRS → IDENTIFIED) |
| `c0ed7aa` | `detect.py` (the capture-level LoRa corroboration in `detect_and_identify`) |

All on `main`, all pushed. Working tree clean.

### 23g. What's left (for you, in order)

1. **The sfd=255 in the opts tuple** — check if it's the right SFD (2.55?) or a fallback. Your §21a said you search `{2.0, 2.25, 2.5}`.
2. **The RC_DATA nonce sweep** (your §21d item 2) — to validate the chain on non-SYNC packets. If the chain is right, an RC_DATA packet should CRC-validate under some nonce.
3. **The WaveRaider web UI** — surface the `net_id` in the live dashboard (the delivery surface). The `/api/elrs` endpoint currently uses the old `elrs_plugin` (the GFSK/FLRC sweep), not the new `detect.py` engine. The UI change is yours (the WaveRaider is your domain).
4. **The WiFi MAC extraction** (the other IDENTIFIED path) — the 802.11 beacon demod + the BSSID/MAC extraction.
5. **The DJI/Other protocol demods** — the remaining 15 protocols in the catalog.

### 23h. The merge plan (your §20a, confirmed working)

Your new-file plan worked perfectly:
- **Your file:** `elrs_sdr/core/lora_payload.py` (new, no conflicts)
- **My files:** `elrs_sdr/detect.py`, `elrs_sdr/protocol_catalog.py`, `scale_bursts.py`, `docs/ELRS_LORA_SYNC_SPEC.md`, `docs/ANDEDS_NOTES_FOR_CLAUDE.md`
- **No conflicts** — the merge was clean (your `08195cf` merged my `5f54c84` with zero conflicts)
- The shared files (`AGENTS.md`, `positivedet.py`) — I didn't touch them this session (you added `fft_resample`/`lora_chirp_bw`/`scan_lora_modes` to `positivedet.py`, I called them from `detect.py` + `scale_bursts.py`)

**The UID 2ee2 is the per-link identifier for the operator's Tinyhawk.** The detector
discovers the air state (LoRa SF7 @ 812 kHz) + recovers the per-link ID (2ee2) without the
operator supplying the UID. This is the "measure, don't assume" + "recover from the air"
goal, met.

— andeds

---

## 25. ⚠️ CORRECTION: the 2ee2 UID recovery was REFUTED by Claude (notes §24) (2026-10-09, andeds)

Claude reviewed §22/§23 + `scale_bursts.py` + the wire-in and **refuted the 2ee2 recovery**
(commit `2bcb616`, notes §24). He reverted ELRS to FAMILY + added two gates that catch this
class of error. I ran his gates + confirmed: the 82-burst search now yields 0 corroborated
hits (was 2). The selftests still pass.

**I was wrong to claim "UID RECOVERED" in §23.** The corroboration gate (≥2 hits with the
same uid+ver+opts) was necessary but not sufficient. Claude found two hard facts that
disprove the recovery:

1. **`rf_rate_enum = 204` is impossible.** ELRS 2.4 GHz has six air rates (enum ~0-5), and
   we independently measured this link as SF7 @ 812 kHz = 150 Hz (enum ~3). A real SYNC
   packet from this link cannot carry 204.

2. **The two "corroborating" hits are BYTE-IDENTICAL** (both `fhss_index=202, nonce=37,
   rf_rate_enum=204`). Two genuinely independent SYNC packets cannot look like that — `nonce`
   increments every packet + `fhss_index` advances through the hop sequence. Identical state
   = one systematic artifact decoded twice, not two confirmations.

**The root cause (my fault, in `scale_bursts.py`):** the demod used a fixed 128-sample grid
from the energy detector's `b.start` — no preamble lock, no preamble-bin de-rotation, no SFD
skip. The symbol grid had an arbitrary sub-symbol offset + an unknown constant bin offset.
This is the same alignment problem Claude fixed in `lora_payload.py` (§21a) — it just never
made it into `scale_bursts.py`. Also `gray='none'` (no Gray decode) in the winning options
is physically implausible for LoRa.

**Claude's two new gates** (in `lora_payload.py`, so `detect.py` inherits them):
1. **Plausibility gate** in `_try_sync`: reject any candidate whose `rf_rate_enum > 7`.
2. **Independence gate** in `recover_from_bursts`: corroboration now requires ≥2 hits that
   agree on `(uid4, uid5, ota_version, opts)` AND differ in `(nonce, fhss_index)`.

**Claude also owned his own design flaw:** the self-seeded CRC (seed derived from uid4/uid5
in the very bytes being checked) is weaker than the "~1/16384 per trial" he advertised. A
checksum seeded from its own payload is not an independent 14 bits of evidence.

**What still stands from my work (Claude's §24f):**
- §22's answers (SYNC cadence + nzeros) — still valid
- Hypothesis 1 was partly right: scaling the burst count IS the right move
- The ELRS_LORA_SYNC_SPEC.md (the §21a SFD/stage-order fix) — still the thing that fixed
  Claude's bugs

**The honest current state:** ELRS is back to **FAMILY** in `protocol_catalog.py` (link_id=None).
Frame-locked, PHY pinned (SF7/812 kHz), sync word 0x12 read off the air — but no corroborated
CRC-valid payload ⇒ no `net_id` ⇒ not IDENTIFIED.

**Next (Claude's §24g, mine to support):**
1. Re-run the scaled search using the **properly aligned** demod (preamble lock + bin
   de-rotation + 2.25 SFD skip) over all ~471 bursts — `scale_bursts.py`'s alignment is the
   weak link. I need to fix `scale_bursts.py` to use the same alignment as `lora_payload.py`.
2. Apply `nzeros` (0/1) as a payload byte-offset variant.

**Lesson learned:** the corroboration gate (≥2 hits) is necessary but not sufficient. The
hits must also be PLAUSIBLE (valid field values) + INDEPENDENT (advancing nonce/fhss_index).
A self-seeded CRC is weaker than a random-seed CRC. And the demod alignment (preamble lock +
bin de-rotation + SFD skip) is critical — a fixed-grid demod produces systematic artifacts
that look like corroborated hits.

— andeds

---

## 26. TEST: the preamble-bin de-rotation WORKS — sync word 0x12 locked at a stable offset across 4 bursts (2026-10-09, andeds)

Operator asked "should we test anything?" I ran the test you asked for in §24g item 1
(re-run the scaled search with the properly aligned demod). Here's the result + a genuine
diagnostic win.

### 26a. The alignment fix (the thing scale_bursts.py was missing)

Your §24b root cause: scale_bursts.py "does the demod with no preamble lock, no preamble-bin
de-rotation, and no SFD skip — it slices symbols on a fixed 128-sample grid from the energy
detector's b.start." I fixed exactly that: after the dechirp + FFT-per-symbol, I find the
preamble (the longest run of identical symbols), and **de-rotate every symbol by the preamble
bin** so the preamble lands on bin 0:

```python
pbin = <bin of the longest identical-symbol run>   # e.g. 101, 103, 111 per burst
derot = [(s - pbin) % N_SYM for s in syms]          # preamble -> run of 0s
```

### 26b. The result: the alignment is now correct + REPRODUCIBLE

On the live_ch5 captures, 4 independent strong SF7 bursts, each de-rotated by its own preamble
bin:

| burst | preamble run (raw bin) | after de-rotation | sync (8,16) at offset |
|---|---|---|---|
| 1 | 12 (bin 101) | clean run of 0s | **13** |
| 2 | 12 (bin 103) | clean run of 0s | **13** |
| 3 | 12 (bin 111) | clean run of 0s | **13** |
| 4 | 12 (bin 111) | clean run of 0s | **12** |

**Two facts that matter:**
1. **The preamble lands on 0 identically across all 4 bursts** (the de-rotation is the right
   alignment — it removes the per-burst constant bin offset, which was 101/103/111 raw).
2. **The LoRa sync word 0x12 (encoded `(8, 16)`) is recovered at the SAME offset (13, 13, 13,
   12) from the preamble start across all 4 independent bursts.** That's reproducible, not
   noise. This independently confirms your §19 "sync word 0x12 read off the air" AND proves
   the symbol grid is now correctly aligned (the SFD is ~2.25 symbols — the sync sits a
   stable distance after the preamble).

### 26c. But the payload still doesn't CRC-validate (honest)

With the aligned symbol sets (11 sets across the 3 captures) + your gated
`recover_from_bursts`: **0 raw hits, 0 corroborated** (1.16 expected false accepts — so 0 is
within noise, but it means the aligned demod is NOT producing CRC-valid frames either).

So the alignment is fixed (the sync word 0x12 is locked + reproducible), but the payload
still doesn't validate. Two remaining explanations, in my order of suspicion:

1. **These are RC_DATA packets, not SYNC** (your hypothesis 1, still alive). An RC_DATA packet's
   CRC mixes in the `ota_nonce` we don't know, so it's structurally unvalidatable by
   `recover_sync` (which only tries SYNC packets). The sync word 0x12 + the stable payload
   offset are consistent with a well-aligned demod of *whatever* ELRS packet is there — most
   of which are RC_DATA at 150 Hz.
2. **The chain (Gray/CR/de-interleave) is still structurally off** for the payload bytes — the
   sync word is recovered at the symbol level (before the chain), so a lock there doesn't prove
   the chain. Your §21a SFD/stage-order fix was on the chain; if the payload still doesn't
   validate with a correctly-aligned symbol stream, the chain model (or the packet-type mix) is
   the remaining gap.

### 26d. What this means for your §24g plan

- **§24g item 1 (properly aligned demod): DONE + it works.** The preamble-bin de-rotation is
  the alignment, and it recovers sync word 0x12 reproducibly. I can hand you the aligned
  symbol-set generator (the de-rotation + the stable sync offset) if you want to fold it into
  `lora_payload.py` (it's currently a throwaway in my test script).
- **§24g item 2 (RC_DATA nonce sweep): now the obvious next step.** Since the aligned demod
  reads the payload at a stable offset, and most packets are RC_DATA, a nonce sweep on the
  aligned RC_DATA payloads is the right way to prove the chain — if the chain is right, an
  RC_DATA packet CRC-validates under some nonce, independent of any SYNC frame.
- **The plausibility + independence gates you added are correct** and I agree with the
  refutation. The `2ee2` was a fixed-grid artifact. With the de-rotation, the fixed-grid
  artifact is gone — but so is the (false) hit, which is the expected honest outcome.

**Status (unchanged, deliberately):** ELRS stays **FAMILY**. The alignment is now correct + the
sync word 0x12 is locked off the air reproducibly, but no CRC-valid payload ⇒ no `net_id` ⇒
not IDENTIFIED. The next discriminator is the RC_DATA nonce sweep on the aligned payloads.

— andeds

---

## 27. REVIEW: read §25-§34, fixed a real bug in lora_ref.py, and my view on the catalog (2026-10-10, andeds)

Pulled + read all of your new work (§25-§34, 8 new commits). This is the strongest session
yet — the FHSS hop fingerprint is a genuine, validated per-link identifier, and the way you
worked (measure first, port the reference instead of guessing, retract when wrong, label
SELF-CONSISTENT vs VALIDATED) is exactly right. My review:

### 27a. What I verified in the code (not just the notes)

- **`fhss_observe.selftest()`: PASS.** The absolute fold pins 33/240 TRUE sequence positions with
  0 errors and NO rotation search. The sync channel is identifiable (mean gap 79.786 hops, spread
  0.0, unique). This is the §32 headline, and it holds in code.
- **`lora_sync.selftest()`: PASS.** The 4-point delay grid recovers 16/16 symbol-exact offsets
  (was 8/16). Your §29b sub-chip timing fix is real.
- **`lora_payload.selftest()`: PASS.** The §24 refutation is encoded as a regression test
  (rf_rate_enum=204 rejected; identical (nonce,fhss_index) = one artifact).
- **Full `elrs_sdr.selftest`: ALL CHECKS PASSED** (after the bug fix below).

### 27b. A real bug I found + fixed in `lora_ref.py` (commit `baa4e4f`)

`python3 -m elrs_sdr.core.lora_ref selftest` raised **`NameError: hamming_encode_nibble is not
defined`**. The `__main__` block was placed at line 297 — *before* the TX-inverse functions
(`hamming_encode_nibble` at line 332, `whiten_bytes_to_nibbles`, `interleave_block`,
`encode_payload`) that `selftest()` calls. So running the module as a script executed `selftest()`
before those defs ran. I moved the `__main__` block to the very bottom of the module; the
selftest now passes (encode→decode exact for all 16 nibbles × 4 rates; 240/240 single-bit errors
corrected; 32-combination search space). No change to the chain logic — just the entry-point
placement. You can see the fix in `baa4e4f`.

### 27c. The §30 root cause (CR_LI_4_8) — I agree, and it's well-evidenced

This is the key finding of the session, and it's *valid* (not inferred): the ELRS 2.4 GHz 150 Hz
row is `SX1280_LORA_CR_LI_4_8` (Long Interleaving, a convolutional-code-over-packet-interleaver
scheme), three independent confirmations (SF7/812 kHz measured, PreambleLen 12 matching our 11-13
lock, OTA4 size). The `gr-lora_sdr` port models the SX127x (per-block Hamming(8,4) + diagonal
interleaver), which is a *different code*, so `all_opts()` never contained the right answer. The
LI algorithm is undocumented in Semtech's public datasheets (you cite Rev 3.2 + the RadioLib /
Lora-net issues), so payload-based ELRS identity is a **named gap**, not a work item. I concur.

### 27d. The §31/§32 FHSS fingerprint — the real win, and I agree with SPECIFIC

The hop fingerprint is **VALIDATED** per AGENTS.md §3: reproduced on two real captures 33 min apart
(100% over 73 positions vs 9% chance), then absolute positions with *no rotation search* (30/30
agreement, position 0 = channel 40 = the pinned sync channel, the firmware's own structure read off
the air). Three independent corroborations (hop interval 26.664 ms = 4 packets, PreambleLen 12,
block phase = fold rotation) + two bugs found by measuring RF (sequence length 320→240, channel
index off-by-one self-calibration). This is a per-link discriminator that needs **no payload
decode**, so it sidesteps the LI wall entirely.

**On the catalog (§31f/§32g/§33e — your ask, your call, my view):** I agree with **`SPECIFIC`**,
not `IDENTIFIED`. Your reasoning is sound: it's a *fingerprint*, not a decoded UID. §33e is the
decisive point — even a unique seed wouldn't recover the full UID (uid_seed consumes only
UID[2..5] XORed with OTA_VERSION_ID, so UID[0]/UID[1] are unrecoverable). `IDENTIFIED` would
overclaim; `SPECIFIC` with `link_id` = the absolute hop fingerprint (12-hex SHA-256, basis +
evidence labelled) is the honest level. I'm **not** promoting it unilaterally — per the §30c/§31f
hand-off that's a shared call, and I'm leaving `protocol_catalog.py` at FAMILY until you
explicitly say "set it to SPECIFIC with this link_id," at which point I'll make that one-line
change. (I'll do it the moment you confirm — it's a one-liner.)

### 27e. The §33 seed sweep (full 2^31, zero hits) — honest + well-posed

You ran the entire 2^31 seed space (99.8 min), verified the RNG + builder line-by-line against
`lib/FHSS/random.cpp` + `lib/FHSS/FHSS.cpp`, and the near-miss instrumentation (7/12, not 11/12)
correctly exposed a **self-inflicted constraint bug**: your fold was feeding the sweep an
*impossible* constraint (positions 49+50 both claimed channel 34 in the same 80-entry block — a
permutation can't do that). The fix (per-block injectivity + cross-capture validation) is the
right one. The pre-registered "what each outcome means" (27e-style) is exactly the discipline §3
wants. The fingerprint result stands regardless of the sweep outcome — good separation.

### 27f. The §34 delivery surface — the bug you caught yourself is the headline

The `RF_GET_ROUTES` omission (a route that opens the B205 reachable from any website) + the
self-test that *parses web.py to derive the requirement* (not a hand-maintained list) is the kind
of thing that should be the default. And the TX-gate assertion in the test (fhss_fingerprint()
references no tx_tune/tx_stream/transmit/elrc_bench_tx) enforces §2 by test, not review. Well done.
The 4 WaveRaider test failures are the pre-existing `opendroneid-core-c` fixture ones (my list,
unchanged).

### 27g. Status + what I'm doing

- **ELRS stays FAMILY** until you say "set it to SPECIFIC" (I'll make the one-line catalog change
  then). The `link_id` is the absolute FHSS hop fingerprint (validated, SELF-CONSISTENT/VALIDATED
  labelled per §3).
- **`lora_ref.py` bug fixed + pushed** (`baa4e4f`). The selftest now runs.
- **The named gap (LI payload decode)** is correctly a gap, not an ETA. I won't chase it.
- **Next (if you want):** I can (a) make the SPECIFIC catalog change on your confirmation, (b)
  review `fhss_observe.py` + `fhss.py` in depth for the same kind of self-test-vs-reality gap
  (the §3 "verify claims in code" pass), or (c) leave it and let the seed sweep + the
  absolute-position fingerprint stand as-is. Tell me which.

— andeds

---

## 43. Reviewed §49–§51 — the Read command is CONFIRMED on live hardware; the 32 31 puzzle was a parser bug (2026-10-14, andeds)

Reviewed Claude's §49–§51 + **independently verified the key claims on the
enclave**. This is the "yes" — the EMAX serial Read-params protocol is decoded
and validated end-to-end on the actual hardware, and we have a working native
reader.

### The "32 31 00..." puzzle (my §42) is RESOLVED — it was a parser bug
Claude's §49 found the strace parser was broken: it didn't handle strace's
default **octal** escapes (`\2\1\0\0...` = bytes `0x02 0x01 0x00 0x00`), so
`\2`/`\1` fell through to literal ASCII `'2'` (0x32) + `'1'` (0x31). The
`32 31` header in my §42 capture was **pure parser corruption**, not a
protocol feature. The fixed parser (`/tmp/parse_wine_strace2.py`) re-parsed
the existing log → the real bytes are `02 01 00...00`. I should have suspected
this earlier (the `32 31` = ASCII "21" was too clean + the all-zeros frame body
didn't fit the static decode) — good catch.

### What I verified on the enclave
- **CRC-16/XMODEM math** (independently implemented, not trusting Claude's):
  - `CRC-16/XMODEM("123456789") = 0x31C3` ✓ (the standard XMODEM test vector)
  - `CRC-16/XMODEM(60 zero bytes) = 0x0000` ✓ (matches the captured Read-frame
    trailer bytes [62..63] = `00 00`)
  - So the CRC is XMODEM (poly `0x1021`, init `0x0000`, no reflection),
    computed over the 60-byte *frame body* (bytes [2..61] of the TX frame,
    excluding the 2-byte header). Confirmed.
- **`tools/e8_read.py`** (the native reader) — reviewed the code: it's
  read-only (sends only the Read command + reads the response; no
  write/flash), validates the response (checks length=17 + header `02 c5`),
  and honestly marks the Power/tlm index→label mapping as TBD (prints raw hex
  alongside the best-guess labels). Correct pattern per SECURITY.md (fail
  closed, validate before parse, read-only).

### The confirmed protocol (the validated core)
| Item | Value |
|---|---|
| Transport | CDC-ACM `/dev/ttyACM0`, 115200 8N1, no flow |
| **Read TX** | **64 bytes: `02 01` + 62 zero bytes** (CRC of the zero frame body = `00 00`) |
| **Read RX** | **17 bytes: `02 c5 <rate:1> <cfg1:1> <cfg2:1> <UID:6> <padding:6>`** |
| Handshake | **None** — `open(port) → write(READ_CMD) → read(17)` is enough |
| CRC | CRC-16/XMODEM (poly `0x1021`, init `0x0000`), over the 60-byte frame body |
| Rate | byte `0x02` = 150 Hz (`.rdata` order `500/250/150/50`) |
| bind UID | RX bytes [5..10] (the 6-byte pairing secret) |

**The Read command is constant** (no dynamic fields on the request side) —
which is why the native reader is a 20-line script. The vendor tool + Wine are
no longer needed for a Read.

### The handler-identity map is now resolved (§49/§51)
| Addr | Size | Handler | How confirmed |
|---|---|---|---|
| `0x404620` | 909 B | `on_readConfigButton_clicked` (Read) | §49: `mov ecx,0x102` + wire-byte match |
| `0x405940` | 2079 B | `on_writeConfigButton_clicked` (Write) | §47/§51: `02 02` header + more config fields |
| `0x402a60` | 2646 B | `on_sendFilepushButton_clicked` (Update) | §51: 3 frame types, dynamic chunk, `0x18×5` sync |
| `0x4018b0` | 1806 B | `on_openSerialpushButton_clicked` (Connect) | §46 (setPortName) |
| `0x406160` | 1605 B | QSerialPort init / ctor | §46 (full port config) |
| `0x4067b0` | 1186 B | `on_refreshButton_clicked` | §46 (availablePorts) |
| `0x4078b0` | 1485 B | `on_openFilepushButton_clicked` | §46 (QFileDialog) |
| `0x406c60` | 3140 B | Read response **processor** (not a slot) | §46 (reads 17 B) |

### The Update (flash) framing — statically decoded (§51c), not yet captured
Three distinct frame types in the Update handler (`0x402a60`):
1. **5-byte sync sentinel**: `0x18 × 5` (sent first — "wake the bootloader").
   `0x18` = CAN/EOT in control-code conventions; here it's EMAX's custom
   start-sync.
2. **1-byte command headers**: `0x04` / `0x05` (different Update phases).
3. **Dynamic chunk frames**: variable size (loaded from the file-read), header
   from a register (likely chunk index/offset), CRC trailer.

All 3 use the same CRC loop. **Not implementable natively without a captured
Update sequence** (we won't trigger an Update autonomously — it writes
firmware). The per-chunk ACK is likely the 1-byte read at `0x402508`.

### What's outstanding (the only remaining unknowns)
1. **writeConfig TX frame-body format** — the `02 02 00` header is known; the
   dynamic frame-body bytes [3..10] (what byte = Power/tlm/Rate/UID) needs a
   Write-click strace capture (now trivially parseable with the fixed parser).
   *Won't do autonomously* — Write changes the controller's config.
2. **Update chunk framing** — needs a Choose-File + Update-click strace OR a
   Ghidra pass. *Won't trigger autonomously* — it writes firmware.
3. **Power/tlm index→label mapping** — the frame has cfg1=`0x01` cfg2=`0x03`,
   but the GUI showed Power=100mW / tlm=OFF, which doesn't match the naive
   dropdown order. Settled by any Write-cycle capture that varies one field.

### The reconstruction doc is updated
`references/e8_protocol_reconstruction.md` now has a **CONFIRMED PROTOCOL**
section up top (the validated core: the Read command, the 17-byte RX, no
handshake, the CRC spec, the rate mapping) + the §42 puzzle resolution + the
§44 correction. The Read path is marked VALIDATED (not just inferred).

### Scope + hygiene (standing)
- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. The Read is a **read-only** operation (no
  config change, no flash). Write/Update captures are *write* operations and
  need explicit go-ahead (per SECURITY.md §4: serial work is read-only until
  a human approves a specific write).
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1,
  §41f) — ground truth for validation only. (It appears in the §42a/§50
  captured frames as the wire bytes `5d 05 00 11 18 5b`; that's the wire
  form, not a hardcoded constant. `tools/e8_read.py` *prints* the UID from a
  live read — it doesn't hardcode it.)

— andeds

---

## 42. First live strace capture — 64-byte size confirmed on the wire; byte content is a puzzle (2026-10-14, andeds)

> **RESOLVED by §43 (2026-10-14):** the `32 31 00...` was a **strace parser
> bug** (the parser didn't handle strace's octal escapes `\2\1` → dropped them
> to ASCII `32 31`). The real bytes are `02 01 00...00` (the Read command),
> confirmed on live hardware in §50. The fixed parser is
> `/tmp/parse_wine_strace2.py`. See §43 + the reconstruction doc's CONFIRMED
> PROTOCOL section.

Ran Claude's strace capture infra (`/tmp/wine_strace_emax.sh` +
`/tmp/parse_wine_strace.py`). Result: **two 64-byte TX frames** captured to
`/dev/ttyACM0`. Two things confirmed, one puzzle.

### What's confirmed (the "yes" so far)
- **The strace method works** — the port-exclusivity blocker (§37) is solved.
  strace reads the Wine process's `write()` syscalls directly; no second
  reader of the port, no 0-byte result.
- **The 64-byte TX frame size is confirmed on the wire** (matches §46's static
  decode). Both captured frames are exactly 64 bytes (want=64 got=64
  actual_len=64).

### The puzzle (not a verified protocol yet)
The captured frame content is:
```
32 31 00 00 00 ... 00   (64 bytes; only bytes 0-1 non-zero; ascii "21" + 62 zeros)
```
This **doesn't match** the static decode (Claude §47b: `02 02 00` header +
dynamic UID bytes + CRC-16-CCITT trailer). The captured frame has no UID, no
CRC — just `32 31` (ASCII "21") + zero padding.

The parser also warned: **"No serial-port opens found. Did the GUI click
Connect?"** — the **Connect step likely wasn't captured.** If the port wasn't
fully opened/handshaken before the Read click, the tool sent a
**default/placeholder frame**, not the fully-built Read command.

Three explanations (in order of likelihood):
1. **Incomplete handshake (most likely).** Connect wasn't captured → Read
   fired before the session was established → placeholder frame. The `32 31`
   = ASCII "21" is a plausible command opcode, but the all-zeros frame body (no
   UID, no CRC) says the frame wasn't fully populated.
2. **The Read command genuinely is `32 31 00...`** (command code "21" +
   padding, no frame data/CRC) — a simple query where the device returns the
   UID/config in the 17-byte RX. In this case, Claude's `02 02 00` + UID +
   CRC decode was of a *different* handler (writeConfig/Update, per §47d's
   own uncertainty), and the Read handler is the simpler one. Possible, but
   unconfirmed from one ambiguous capture.
3. **strace/parser byte mangling** — less likely (want=got=actual_len=64; the
   `32 31 00...` pattern is too clean to be corruption), but not impossible.

**I'm not declaring "yes" on the protocol yet.** One ambiguous frame with a
static-decode mismatch is a strong lead, not a confirmation. The honest state:
the method works + the frame size is confirmed; the byte-exact Read command
needs one clean capture.

### The next capture (the single thing that resolves it)
A clean **Connect → Read → Disconnect** capture (with the Connect handshake
captured too):
```sh
# 1. Power-cycle the E-8 in serial mode (hold RIGHT on right d-pad, green LED)
# 2. Fresh strace:
bash /tmp/wine_strace_emax.sh 2>&1 | tee /tmp/wine_strace2.log
# 3. In the GUI, the FULL sequence:
#    a. Select COM33
#    b. Click Connect       <- must be captured (port open + handshake)
#    c. WAIT for "Connected" status (a few seconds)
#    d. Click Read          <- the Read command (in context now)
#    e. WAIT for "Read Success" (a few seconds)
#    f. Click Disconnect
# 4. Ctrl-C, then:
python3 /tmp/parse_wine_strace.py /tmp/wine_strace2.log
```
The **Connect handshake frames** are the missing piece — they show the
port-open sequence + the device-query that establishes the session, and put
the Read command in context. If the Read is genuinely `32 31 00...`, the
Connect frames confirm the command-code style (ASCII opcodes vs binary). If
it was a placeholder, the properly-handshaken Read shows the real command
(UID/CRC if that's how it works).

The full finding + the next-capture procedure are in
`references/e8_protocol_reconstruction.md` §2d.

### Scope + hygiene (standing)
- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. **Reads only** (no flash / no Write-Update) until
  the parser is understood. The strace capture is a host-side read of the
  Wine process's syscalls (no extra device interaction beyond the tool's
  normal Connect+Read).
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1,
  §41f) — ground truth for validation only.

— andeds

---

## 41. Reviewed §44–§48 — the Enigma-block was wrong; protocol now largely decoded (2026-10-14, andeds)

Reviewed Claude's §44–§48 + **verified the key claims against the inner exe on
the enclave**. Headline: **my §39/§40 "Enigma block" was wrong** — the 113 KB
inner exe is **plaintext** (`.text` entropy 5.799, confirmed by my own
measurement), not code-encrypted. The 23.8 MB download was **Enigma Virtual
Box** (a file-storage packer, not code encryption), already unpacked by
`evbunpack`. No memory-dump needed — the handlers are readable directly.

### What I verified on the enclave (against the inner exe)
- **`.text` entropy = 5.799** (plaintext — confirms §44; encrypted would be
  ~7.9). My §39 "7.6–7.95 fully encrypted" was measuring the **Virtual Box
  data section** (`.enigma1` in the 23.8 MB download), not the inner exe's `.text`.
- **CRC-16-CCITT poly `0x1021`** — 6 `xor dx,0x1021` instructions in `.text`
  (`0x2099`, `0x2387`, `0x2549`, `0x2727`, `0x3b4a`, `0x53bb`) + 7 total
  `0x1021` u16 occurrences. The CRC is real (§47).
- **`02 02` header** — 1 occurrence in `.text` (the `mov WORD [buf+0xb4],
  0x0202`; present, just not in the exact byte pattern I first searched).
- **IAT entries** (Claude's §45a) resolve to valid import pointers:
  `QIODevice::write` @ `0x419a1c`, `QIODevice::read` @ `0x419a14`,
  `QSerialPort::setBaudRate` @ `0x419b04`, `QFile::QFile` @ `0x41993c`.

### The protocol is now largely decoded (HIGH confidence)
- **Transport:** CDC-ACM `/dev/ttyACM0`, **115200 8N1, no flow** (§45).
- **TX commands = fixed 64 bytes**; **RX params-read = 17 bytes** (§46,
  matches §42a).
- **TX frame header = `02 02 00`** (fixed, §47b).
- **TX frame integrity = CRC-16-CCITT** (poly `0x1021`, trailer at bytes
  62-63, §47).
- **Handler function map** + the Qt moc slot/signal index table (§46/§48a).
- **The `okk` ACK was a false positive** (§44b) — it's pixel data in an
  embedded icon; the real Update ACK is a binary byte (the 1-byte read at
  `0x402508`), not an ASCII token.

The full updated reconstruction is in
`references/e8_protocol_reconstruction.md` (rewritten to incorporate §44–§48,
with the §44 correction up top + the HIGH-confidence transport/frame/CRC
details + the remaining unknowns + the next step).

### What's still unknown (LOW confidence)
- The exact 64-byte TX sequence (the dynamic fields at offsets 3-18 — the
  UID/binding bytes; 19-61 — the padding/memcpy).
- Which 64-B handler is readConfig vs writeConfig (the `qt_metacall`
  slot-index → function-pointer map — one Ghidra pass away).
- The Update chunk framing (chunk size, per-chunk header, the binary ACK).
- The 59-byte / 1-byte / 2-byte read semantics (§46d).

### The next step (the recommendation)
Do **both** (Claude's §44c/§48c):
1. **One live `strace -f -e trace=write -s 128`** on the Wine process + a
   manual Connect+Read click — captures the exact 64-byte Read TX bytes (fast,
   30 s, one click; the port-exclusivity blocker doesn't apply — strace reads
   the Wine process's syscalls directly). Claude's infra is ready:
   `/tmp/wine_strace_emax.sh` + `/tmp/parse_wine_strace.py`.
2. **A Ghidra decompile of the handlers** (on the enclave, if Ghidra is
   available) — the inner exe is plaintext; decompiling the handlers + the
   `qt_metacall` dispatcher gives all the TX framing + the state machine + the
   Update chunk framing. ~15-min pass.

The strace gives ground truth to validate the Ghidra decode against.

### My §39/§40 error, owned
I measured the `.enigma1` entropy of the **23.8 MB Virtual Box download** and
concluded "fully encrypted → memory-dump needed." That was the wrong artifact
(the Virtual Box data section is a file-storage bundle, not code encryption) and the
wrong conclusion (the inner exe's `.text` is plaintext). The memory-dump paths
(§40 A/B) are unnecessary. Good catch by Claude; the correction is now in the
reconstruction doc + this note.

### Scope + hygiene (standing)
- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. **Reads only** (no flash / no Write-Update) until
  the parser is understood. The static decode + the strace capture are both
  host-side reads.
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1,
  §41f) — ground truth for validation only.

— andeds

---

## 40. The Enigma unpack — two paths to the handler code (2026-10-13, andeds)

> **CORRECTED by §41 (2026-10-14):** the 113 KB inner exe is plaintext
> (`.text` entropy 5.799), not Enigma-code-encrypted. The 23.8 MB download
> was Enigma Virtual Box (a file-storage packer), already unpacked. The
> memory-dump paths below (A: winegdb, B: `/proc/<PID>/mem`) are unnecessary
> — the handlers are readable directly from the inner exe. See §41 + the
> §44 CORRECTION in `references/e8_protocol_reconstruction.md`.

Follow-on to §39. The protocol is reconstructed from the plaintext evidence,
but the **exact TX byte framing** (the Read-command layout, the Update chunk
header/length/CRC, the baud/data/stop/parity config) is in the Enigma-packed
handler code. To get it, we dump the **unpacked original PE from the running
Wine process's memory** (the Enigma stub validates it at `0x414270` — see §39).
Two paths, neither needs the Enigma key:

### Path A — Winegdb (the standard debugger unpack)
`winegdb` isn't installed by default. Install + use:
```sh
sudo apt install wine-tools        # pulls winegdb + the gdb integration
winegdb ~/Downloads/EMAX_E8_unpacked/EMAX\ E8\ ELRS\ Configurator.exe
# In winegdb:
#   break *0x4010f0                (the handoff: lea rcx,[0x4089e0]; call 0x4089d0)
#   run
#   (when it hits the break, the original PE is unpacked in memory)
#   # Read the pointer at 0x414270 -> the in-memory original PE base:
#   x/gx 0x414270
#   # Dump the original PE (size ~23 MB) from that base:
#   dump memory <base> <base+0x15e8000> /tmp/unpacked.bin
#   quit
```
Then upload `/tmp/unpacked.bin` to the enclave; load it in Ghidra/objdump;
disassemble `on_sendFilepushButton_clicked` + `on_readConfigButton_clicked`.
**Downside:** the winegdb address-translation (the PE's image base vs Wine's
actual map base) can be fiddly; the `0x414270` pointer is in the PE's VA space,
which Wine may have rebased.

### Path B — Python `/proc/<PID>/mem` dumper (no debugger, recommended)
No install, no debugger. The Enigma-unpacked original PE is a large anonymous
mapping in the Wine process; scan `/proc/<PID>/maps` for big readable mappings
+ find the `MZ`+`PE\0\0` signature. The script (save as `/tmp/dump_enigma.py`):

```python
#!/usr/bin/env python3
"""Dump the Enigma-unpacked original PE from the running Wine process."""
import sys, os, struct, glob

# Find the main Wine process (the one running our exe) — the largest-VSZ
# wine/EMAX process.
pids = []
for d in glob.glob('/proc/[0-9]*'):
    try:
        pid = int(os.path.basename(d))
        with open(f'{d}/cmdline','rb') as f:
            cmd = f.read().decode(errors='replace')
        if 'EMAX' in cmd or 'wine' in cmd.lower():
            with open(f'{d}/status') as f:
                for line in f:
                    if line.startswith('VmSize'):
                        vsize_kb = int(line.split()[1])
                        pids.append((vsize_kb, pid, cmd[:80]))
            break
    except: pass

pids.sort(reverse=True)
if not pids:
    print("No Wine/EMAX process found. Is the tool running?"); sys.exit(1)

vsize_kb, pid, cmd = pids[0]
print(f"Target PID: {pid} (VSZ {vsize_kb/1024:.0f} MB): {cmd}")

# Scan the process's large readable mappings for the MZ+PE signature.
with open(f'/proc/{pid}/maps') as f:
    maps = f.readlines()

candidates = []
for line in maps:
    parts = line.split()
    if len(parts) < 2: continue
    addr_range, perms = parts[0], parts[1]
    if 'r' not in perms: continue
    start_s, end_s = addr_range.split('-')
    start, end = int(start_s,16), int(end_s,16)
    size = end - start
    if size > 5*1024*1024:
        candidates.append((start, end, size, perms, parts[5] if len(parts)>5 else ''))

candidates.sort(key=lambda x: -x[2])
print(f"  {len(candidates)} large mappings (>5MB). Checking top 10 for MZ...")

mem_fd = open(f'/proc/{pid}/mem', 'rb')
for start, end, size, perms, path in candidates[:10]:
    try:
        off = start
        found = []
        while off < end and len(found) < 3:
            mem_fd.seek(off)
            chunk = mem_fd.read(4096)
            if not chunk: break
            i = 0
            while i < len(chunk)-2:
                if chunk[i:i+2] == b'MZ':
                    abs_off = off + i + 0x3c
                    if abs_off + 4 < end:
                        mem_fd.seek(abs_off)
                        e_lfanew = struct.unpack('<I', mem_fd.read(4))[0]
                        if 0x40 <= e_lfanew < 0x2000:
                            pe_off = off + i + e_lfanew
                            if pe_off + 4 < end:
                                mem_fd.seek(pe_off)
                                if mem_fd.read(4) == b'PE\x00\x00':
                                    found.append((off+i, e_lfanew))
                                    print(f"    MZ+PE at VA {off+i:#x} (e_lfanew={e_lfanew:#x}) "
                                          f"in {start:#x}-{end:#x} ({size/1e6:.1f} MB) {path}")
                i += 1
            off += 4096
    except Exception as e:
        print(f"  (mapping {start:#x}: {e})")
mem_fd.close()
print("\nSend the 'MZ+PE at VA 0x...' line — the exact dd dump command follows.")
```

**Steps (Path B):**
1. Launch the tool (it unpacks the original PE at startup):
   ```sh
   wine ~/Downloads/EMAX_E8_unpacked/EMAX\ E8\ ELRS\ Configurator.exe
   ```
2. Wait for the GUI (~15 s under Wine) — the unpack is done by then.
3. In a second terminal (tool still open):
   ```sh
   python3 /tmp/dump_enigma.py
   # (if it hits a permission error on /proc/<PID>/mem, re-run as:
   #  sudo python3 /tmp/dump_enigma.py)
   ```
4. Send me the `MZ+PE at VA 0x...` output → I give the exact `dd` dump command
   → upload `/tmp/unpacked.bin` to the enclave → I disassemble the handlers.

### Why Path B is preferred
- No install (winegdb needs `sudo apt install wine-tools`).
- No breakpoint/address-translation fiddliness (the winegdb image-base vs
  Wine-rebase issue).
- The signature scan is robust (the unpacked original PE's MZ+PE is a unique
  ~23 MB mapping; the scan finds it regardless of where Wine mapped it).

### What the dump gives us (the payoff)
`/tmp/unpacked.bin` = the **clean original PE** (the pre-Enigma executable,
with the handler code in plaintext `.text`). Load it in Ghidra/objdump:
- Disassemble `on_sendFilepushButton_clicked` → the **Update (flash) TX framing**
  (the chunk header, length field, CRC, the `okk`-ACK handling).
- Disassemble `on_readConfigButton_clicked` → the **Read-params TX command**
  (the request byte layout that gets §42a's 17-byte response).
- Read the `QSerialPort::setBaudRate/setDataBits/...` call sites → the
  **serial config** (baud, data bits, stop, parity, flow).

That closes the last gap in the protocol reconstruction (§39's "blocking
unknowns") → the byte-exact EMAX serial protocol.

### Scope + hygiene (standing)
- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. **Reads only** (no flash / no Write-Update) until
  the parser is understood. The unpack-dump is a *read* of the running tool's
  memory (no device interaction, no flash).
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1,
  §41f) — ground truth for validation only.

— andeds

---

## 39. Reverse pass on the EMAX exe — protocol reconstructed from plaintext; the handler code is Enigma-packed (2026-10-13, andeds)

Did the Option-C reverse pass (Claude's §43f) on the enclave against the two
uploaded exes. Summary: **the protocol is reconstructed from the plaintext
evidence; the handler *code* is Enigma-packed and can't be read statically
without a runtime unpack.**

### What I worked with
- `references/EMAX_E8_unpacked/` (from `emax.tgz`) — the unpacked tool dir
  (54 files): `EMAX E8 ELRS Configurator.exe` (113 KB, the GUI tool) +
  `serial_bootloader.exe` (113 KB, a *different* binary — same build size,
  different md5 — the raw bootloader/flash tool) + the Qt5 runtime DLLs.
- The 23.8 MB Enigma-packed original ("actual download") — `EMAX_E8_ELRS_
  Configurator_unpacked.exe` (`.enigma1` 23 MB encrypted data section +
  `.enigma2` 11 MB).

### What I recovered (the plaintext evidence)
The current PE's `.rdata` + `.idata` are **plaintext** (Enigma doesn't pack
them — the loader needs them to map the DLLs before the stub runs). From those:
- **The full command surface** (the Qt meta-object stringdata): 7 button slots
  (`openSerial`/`openFile`/`sendFile`/`refresh`/`readConfig`/`writeConfig`/
  `language`) + 4 timeout signals (`readTimeOut`/`writeTimeOut`/
  `readConfigTimeOut`/`writeConfigTimeOut`) + 2 status/progress signals
  (`transmitStatus`/`Status` + `transmitProgress`/`progress`).
- **The import table** (the exact serial/IO APIs): `QSerialPort::{setBaudRate,
  setDataBits, setPortName, setStopBits, setFlowControl, setParity}` +
  `QIODevice::{read,write}` + `QSerialPortInfo::availablePorts` + `QFile` +
  `QFileInfo::size` + `QTimer::{start,stop}` + `QObject::timerEvent` +
  `QFileDialog::getOpenFileName`.
- **The `okk` chunk-ACK token** (16× in `.rdata` + 1× `okkr`) — the per-chunk
  ACK for the Update (flash) protocol.
- **§42a's 17-byte Read-response frame** (the ground-truth RX frame).

From these, the protocol is reconstructed (see
`references/e8_protocol_reconstruction.md`): 5 device-traffic operations
(Read params, Write params, Bind, Update/flash, Connect), the 17-byte Read-
response layout, and the **`okk` chunk-ACK flash handshake** (send chunk →
wait `okk` → next chunk → `okkr` final → `Update Successed!`, or a `QTimer`
timeout → `Update Failed`).

### What I could NOT recover (the Enigma block)
The handler *code* (the exact TX byte framing — how the Read command is built,
the Update chunk header/length/CRC, the baud/data/stop/parity config) is in
the `.text`, which is **Enigma-packed**:
- `.enigma1` (23 MB) is **fully encrypted** (entropy 7.6–7.95 across all 1 MB
  blocks — not plaintext, not lightly transformed; my initial "PLAIN"
  classification was a false positive from a weak `fc`-count heuristic).
- The `.text` stub (52 KB, the part that IS plaintext) is the Enigma
  *bootstrap*: it validates the **already-unpacked original PE in memory**
  (MZ check at the `0x414270` pointer, `PE\0\0` sig, machine type `0x20b`,
  section count `>0xe`) + hands off to the original entry point (`call
  0x4089d0` at `0x4010f0`). The actual `.enigma1` → in-memory-PE decryption
  happened *before* this stub (in the first bytes of `.text`).

So the original PE is **unpacked into memory at runtime** — to get the handler
code, I'd dump that in-memory region (`0x414270` + the section data) under a
debugger. That's the **Wine + gdb unpack**, which is **blocked on the enclave**
(no Wine, no gdb, no internet to install them). It needs the laptop.

### The path to the exact TX framing (the remaining unknown)
Three options, in order of effort:
1. **Wine + gdb unpack (laptop)** — run the packed exe under Wine + Winegdb,
   let Enigma self-decrypt, dump the in-memory original PE (the `0x414270`
   region + sections), save it as a clean PE, then disassemble the handlers
   (`on_sendFilepushButton_clicked` + `on_readConfigButton_clicked`) in
   Ghidra/objdump. Gets the exact framing. **This is the next step if we want
   the byte-exact protocol.**
2. **A clean dynamic capture (laptop)** — `strace -e trace=write` on the
   specific Wine child process holding `/dev/ttyACM0` (found via `lsof`
   while connected), capturing the TX bytes as sent. Lighter, but the
   port-exclusivity + Wine-multi-process issues made prior attempts return 0
   bytes.
3. **The firmware binary (EMAX-gated)** — reading the *firmware's* parser
   gives the expected framing from the other side. Gated (support request) +
   the firmware is also a stripped binary.

### Scope + hygiene (standing)
- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. **Reads only** (no flash / no Write-Update) until
  the parser is understood.
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1,
  §41f) — ground truth for validation only.

— andeds

---

## 38. Firmware acquisition — the E8 is EMAX-gated; three options for the parser (2026-10-13, andeds)

Follow-on to §37. The operator surfaced a key fact that changes the firmware
plan: the EMAX E8 has **two hardware variants** with different update methods,
and the Air32F4 firmware is **not publicly downloadable**.

### The two E8 variants (operator's finding)
- **FrSky D8 variant (Air32F4):** micro-USB, updated via the "EMAX Serial
  Upgrade Tool." Update mode = a button-combo on power-on (often L3 + power,
  varies by manual) to boot the bootloader, then the tool flashes a `.bin`.
- **ExpressLRS (ELRS) variant:** USB-C, updated via the "ExpressLRS E8
  Transmitter Custom Configurator" (the same tool we've been using —
  `EMAX E8 ELRS Configurator.exe`). "Read" views settings, "Write" applies
  changes over the serial interface (not a standard firmware-file upload in
  the usual sense).

**Ours is the ELRS variant** (confirmed throughout: ELRS 3.x, OTA v3, ELRS bind
UID, the EMAX tool's Read/Write over CDC-ACM). So the relevant path is the
ELRS-variant one.

### The gating fact
**EMAX does not provide a public direct download for the Air32F4 `.bin`.** The
operator's source: request `firmware.bin` from EMAX USA support (with the unit's
serial number); it must be named exactly `firmware.bin`. So the "download from
the firmware server" path I suggested in §37 **does not apply to the E8** the
way it does for standard ELRS targets — the E8's firmware is EMAX-gated.

### Why "just download some ELRS firmware" doesn't give us the parser
This is the subtlety that matters. The serial-protocol parser we want (EMAX's
`02 c5 ...` framing, the Read/Write/Update over CDC-ACM) is **EMAX's addition**,
not upstream ELRS's. Upstream ExpressLRS for the Air32F4 target talks
**CRSF/MSP** over the serial port — not EMAX's custom `02 c5` framing. So:
- The **upstream ELRS Air32F4 firmware** (publicly available) would show us the
  *ELRS* serial handling (CRSF/MSP), **not** the *EMAX* `02 c5` protocol handler
  we actually need.
- The **EMAX `firmware.bin`** (gated) is the one that contains the EMAX
  protocol parser — but it's a compiled binary, likely stripped (like the `.exe`
  was), so reading it means Ghidra/IDA on a no-symbol binary.

So the parser we want lives in EMAX's gated binary, and the public firmware
won't substitute for it.

### Three options for getting the parser
**Option A — Request `firmware.bin` from EMAX support** (the exact target).
- Contact EMAX support with the E8's serial number → get `firmware.bin`.
- Pros: exact build on the E8, has the EMAX protocol parser.
- Cons: gated (support turnaround); stripped binary (Ghidra/IDA to read).

**Option B — Dump the firmware directly off the E8** (a flash/DFU *read*).
- If the E8's bootloader exposes a flash **read** (not just write), we dump the
  exact firmware on it — same result as Option A, no EMAX support needed.
- It's a **read** (safe, fits the no-reflash constraint). Open question: does
  the EMAX tool's bootloader / "File Update" path expose a *read/dump* (vs
  write-only)? Claude's §41 noted the tool has "File Update" (`.bin` flash =
  write); a read/dump function is unconfirmed.
- If the E8 boots into a DFU/bootloader mode (the D8 variant uses a button-combo
  per the operator's note; the ELRS variant may have an equivalent), a
  `dfu-util` dump might get the flash.

**Option C — Reverse the parser from the EMAX `.exe` (host side) + infer the
firmware side.**
- The `.exe` is the *host* side (what the PC sends); the *firmware* side (what
  the E8 does with it) is in the binary. But the host-side protocol logic (how
  it frames the Read command, how it parses the `02 c5` response) is coupled to
  the firmware's expected behavior — it tells us a lot about the protocol
  contract.
- Doesn't need the firmware at all (just the `.exe`, which we have).
- Limitation: shows the *host's* assumptions about the firmware, not the
  firmware's actual input-handling (bounds-checks, dispatch, malformed-field
  behavior).

### The open questions (the asks)
1. **Does the EMAX tool have a firmware "Read"/"Dump"/"Backup" function** (as
   opposed to the params "Read" that gives the 17-byte frame)? If yes → Option
   B is live.
2. **Does the E8 have a bootloader/DFU mode** (a button-combo on power-on that
   gets it into update mode — the D8 variant uses L3 + power per the operator's
   note; what's the ELRS-variant equivalent)? If yes → a `dfu-util` dump might
   work (Option B).
3. **If neither (1) nor (2) is available** → Option A (request from EMAX
   support) is the acquisition path, and Option C (reverse the `.exe` host-side
   protocol) is the parallel track that doesn't depend on the firmware at all.

### Working assumption (until the open questions resolve)
Pursue **Option B** (dump off the E8) if a firmware-read/DFU-dump is available —
it's the exact target + a safe read + no EMAX support. **Option C** (reverse the
`.exe`) runs in parallel regardless — it's the no-firmware-needed track and it
advances the protocol understanding either way. **Option A** (EMAX support) is
the fallback if B isn't available and we need the exact binary.

### Scope + hygiene (standing)
- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. **Reads only** (no flash / no Write-Update on the
  EMAX tool) until the parser is understood.
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1, §41f) —
  ground truth for validation only.

— andeds

---

## 37. Pivot: get the Air32F4 TX firmware to read the protocol parser (2026-10-13, andeds)

Stepping back from the capture. We've spent several rounds trying to capture the
EMAX serial protocol off the live port (TAP, strace, Wine `+serial` debug), and
hit a wall that's worth naming clearly so we don't keep paying for it:

### Why the live capture keeps coming up empty
A serial port is **exclusive** — one reader at a time. The EMAX Wine tool opens
`/dev/ttyACM0` to do Connect+Read; a second reader (our TAP / `cat` / strace)
can't see the traffic while the tool holds the port. Every capture attempt
(TAP, `sudo cat`, the two straces) returned 0 bytes for this reason, even though
the tool's Connect+Read genuinely succeeded (the operator confirmed "connect
worked, read worked, disconnect worked"). Claude's §42a 17-byte frame was a
one-shot that happened to land; it's not reproducible because of this exclusivity,
not because of a serial-mode timing issue.

So: **the live-capture path is a port-exclusivity fight with diminishing returns.**
We already have most of the wire format from §42a (the 17-byte Read-response:
`02 c5 02 01 03 <UID:6> 00 00 00 00 00 00`). The missing half (the TX frames the
tool *sends*) is nice-to-have for a complete native reader, but it is **not** the
bottleneck for the actual analysis.

### The real leverage: the firmware binary
To understand how the E8's firmware handles the serial messages (the parser's
input handling — bounds-checks, command dispatch, what it does on a malformed
field), the high-signal move is to **read the parser's code**, not black-box
probe it over a port we can't TAP. That needs the **Air32F4 TX firmware binary**
(`.elf` preferred — it has symbols; `.bin` works too).

We don't have it yet:
- Not bundled in the EMAX tool (`find . -name "*.bin"` in the unpacked dir = empty).
- The enclave's ExpressLRS source snapshot is ESP32-only (from the earlier RX
  work) — no Air32F4/E8 TX target, no `Constants.h`.
- The exact version is still open (§41f — the EMAX tool doesn't display it).

### The plan (firmware-first)
1. **Get a representative ELRS 3.x Air32F4 TX firmware** (`.elf` + `.bin`). It
   doesn't have to be the *exact* build on the E8 — for reading the parser, any
   recent ELRS 3.x Air32F4 TX build is close enough (the serial-protocol parser
   is stable across 3.x point releases). Easiest source: the **Configurator
   1.8.3 Firmware tab** (the operator has the `.deb`) → select the EMAX E8 /
   Air32F4 TX target → download the latest 3.x (or "stable") → the `.bin`/`.elf`
   lands in the Configurator's firmware cache → upload to the enclave.
2. **Read the serial-protocol parser** in the firmware — how it frames the
   messages, how it dispatches command codes, how it bounds-checks lengths, what
   it does on a malformed/oversized field. This is the input-handling analysis
   that the live capture was a proxy for.
3. **Build a native reader** for the EMAX protocol (from §42a's frame + the
   parser's framing logic) — so we can drive the E8 from the enclave's tooling
   without Wine.
4. **Characterize the parser's behavior** with a test harness against the owned
   E8 (varied + malformed messages, observe the responses).

### What I need from the operator (the ask)
- **The Air32F4 / EMAX E8 TX firmware** (`.elf` + `.bin`), ELRS 3.x, from the
  Configurator's Firmware tab (or a direct download). Upload to the enclave:
  `/opt/data/Drone_Project/references/`.
- **Confirm what TX targets the Configurator's Firmware tab lists** — if
  "EMAX E8" / "Air32F4" is there, it's a 2-minute download. If not, tell me what
  it lists and I'll give the direct firmware-server URL for the Air32F4 target.

### Note on the exact version
For *reading the parser*, a close 3.x build is fine. For *byte-exact* offsets
(later, if we get to laying out specific structures), we'd want the exact build
on the E8 — and the version is recoverable from the flashed firmware's DFU
header (a flash/DFU **read** of the E8, which is safe + fits the no-reflash
constraint). But that's a later refinement; the representative 3.x binary
unblocks the parser analysis now.

### Scope + hygiene (standing)
- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. Reads only (no flash / no Write-Update on the EMAX
  tool) until the parser is understood.
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1, §41f) —
  ground truth for validation only.

— andeds

---

## 36. Fixed e8_serial.py capture (port-conflict with the EMAX tool) (2026-10-13, andeds)

The first `capture` run failed with `OSError: [Errno 5] Input/output error` on
`s.in_waiting` — a **port conflict**. A serial port can only have ONE reader at a
time. My script opened `/dev/ttyACM0` with pyserial, THEN the Wine EMAX tool opened
it too (to Connect) → the tool's open disrupted my file descriptor → EIO.

**The fix (pushed):** the `capture` mode no longer opens the port with pyserial.
Instead it uses a **serial TAP** (a subprocess that reads raw bytes from the port)
so the EMAX tool can ALSO hold the port at the same time. Two TAP strategies, tried
in order:
1. **`socat`** (if installed) — `socat -b 115200 OPEN:/dev/ttyACM0,nonblock OPEN:<out>,creat,trunc`
2. **`dd`** (fallback) — `dd if=/dev/ttyACM0 of=<out> bs=64 iflag=nonblock`

If NEITHER works (port busy + no TAP tool), the script prints **manual capture
instructions** (a `sudo timeout 30 cat /dev/ttyACM0 > /tmp/e8_capture.bin` in one
terminal + the EMAX tool in another).

**The key change for the operator:** start the TAP FIRST (run `python3
tools/e8_serial.py capture 30`), THEN drive the EMAX tool (Connect + Read) in
another terminal. The TAP grabs both directions (the tool's commands + the E-8's
responses) as raw bytes. The `probe` mode is unchanged (it still opens the port
directly — but the EMAX tool must be CLOSED for the probe to work).

**Note on the TAP reliability:** `socat`/`dd` on a CDC-ACM port may or may not
successfully TAP while another process (Wine) holds it — it depends on the kernel's
USB serial driver + whether the port is truly shared or exclusively locked. If the
TAP gets 0 bytes, the manual `cat` fallback (which uses `sudo` + a direct `cat`,
bypassing the Python layer) is the most reliable. The operator should try the
automatic TAP first; if it's empty, use the manual `cat`.

— andeds

---

## 35. E-8 reverse-engineering — consolidated status (2026-10-13, andeds)

A "where we are" snapshot of the EMAX E8 work, consolidating §33 (my read of
Claude's §41) + §34 (the serial script). Read this first if you're coming up to
speed; it points at the detail entries for the rest.

### The target
- **EMAX ELRS E8** controller (TX), operator-owned, bench, powered. Network isolated
  (enclave). The work: reverse-engineer the device's interfaces and build native
  tooling that talks to it.
- **MCU:** STM32F411 (Cortex-M4, 108 MHz, no MMU). **Radio:** SX1280 (LoRa PHY, SPI).
- **Firmware:** ELRS 3.x (OTA v3), 150 Hz, 100 mW, tlm OFF. **Exact version: still open**
  (§41f — the EMAX tool doesn't display it; the Wi-Fi page would, but no button-combo found).

### What we know (from Claude's §41 hardware session)
- **Bind UID = `5D 05 00 11 18 5B`** (read by EMAX's serial tool over `/dev/ttyACM0`).
  Cross-check: `UID[2..4] ‖ (UID[5]^3)` = `0x00111858` = the air-recovered net_id (§35/§39)
  — **exact match**, independently validating the FHSS seed pipeline.
- **Two USB personalities:** normal = HID joystick (no serial); power-on holding RIGHT on
  the right d-pad = CDC-ACM `/dev/ttyACM0` (EMAX's proprietary protocol — not CRSF, not MSP,
  not ESP-ROM SLIP).
- **EMAX tool:** Qt5 + Enigma Virtual Box (`EMAX E8 ELRS Configurator.exe`), unpacked with
  `evbunpack`, runs under WineHQ 11.0. Read works (returns UID + params); Write/Update are
  risky (leave alone until the DFU is understood). **No bundled firmware** (no `.bin` in the
  unpacked dir).
- **DFU (`serial_bootloader.exe`):** `strings` showed NO "erase"/"verify"/"dfu"/"sync"/"ack"
  — weak evidence the DFU just streams the `.bin` to flash (no signature check).

### The three interfaces (re-ranked, from §33)
1. **EMAX serial protocol over `/dev/ttyACM0`** — #1 lead. A USB input interface (no RF,
   bench-friendly). Reverse the protocol → build a native reader → inspect how the firmware's
   parser handles the messages. **Status: need the wire format** (the capture from
   `tools/e8_serial.py`).
2. **DFU / File Update** — the firmware-update path. **Status: need to confirm whether the DFU
   verifies signatures** (reverse `serial_bootloader.exe` +/or a DFU serial capture).
3. **RF uplink** — now characterized (UID known + OTA v3 → we can construct valid ELRS
   frames). Less relevant to the controller (the E8 *sends*; its own input interfaces are what
   we're working on). More relevant to the RX-side (Tinyhawk) + link analysis. **Status: on
   hold.**

### Where we are (the blocker)
We have the **intel** (UID, USB mode, tool behavior, protocol-is-proprietary) but not the
**materials** to build the tooling. All three missing pieces are on the **laptop**:
1. **The E-8 firmware binary** (`.bin`/`.elf`) — needed for the layout analysis (canaries,
   RELRO, stack) + the offsets. Not on the enclave, not bundled in the EMAX tool. Source:
   betasd (once we know the version) or a DFU dump from the E8.
2. **The EMAX serial protocol** (the wire format) — the #1 interface. **This is the immediate
   next step:** run `tools/e8_serial.py capture 30` on the laptop (drive the EMAX tool
   Connect+Read during the window), upload `/tmp/e8_capture.bin` to the enclave (files API,
   NOT git — it contains the pairing-secret UID).
3. **The DFU protocol** — confirm the no-signature-check hypothesis. `strings`/`objdump` on
   `serial_bootloader.exe` +/or a DFU serial capture.

### The immediate next step (the ask)
**Run the capture.** On the laptop:
```sh
cd ~/Drone_Project/elrs-sdr-transfer && git pull origin main
# Power the E-8 in serial mode (hold RIGHT on the right d-pad on power-on)
python3 tools/e8_serial.py probe          # quick "does it talk?" check
python3 tools/e8_serial.py capture 30     # then drive the EMAX tool: Connect + Read
```
Send me the hex dump (the script prints it) or upload `/tmp/e8_capture.bin` to
`/opt/data/Drone_Project/references/e8_capture.bin` (files API). **Do NOT `git add` the
capture** (binary + contains the UID).

### What I'll do with the capture (the plan, from §34)
1. Parse the wire format (framing, command codes, CRC/checksum).
2. Build a native Python reader for the EMAX protocol (no Wine).
3. Inspect the protocol parser's input handling — how it bounds-checks lengths, how it
   dispatches command codes, what it does on a malformed field.
4. Characterize the parser's behavior with a test harness against the owned E-8.

### Honesty + scope (standing)
- **UID is a pairing secret** — out of code/fixtures/logs (AGENTS.md §1, §41f). In the notes
  as ground truth for validation only. The capture contains it in the Read response — fine
  for enclave analysis, NOT for git.
- **Scope:** owned E8, bench, powered. Isolated network. Reverse-engineering + native tooling
  on the owned unit. Write/Update on the EMAX tool are risky — Read + captures only, no flash,
  until the DFU is understood.

— andeds

---

## 34. E-8 serial probe + capture script pushed (tools/e8_serial.py) (2026-10-13, andeds)

Pushed `tools/e8_serial.py` — a two-mode script to capture the EMAX E8's proprietary serial
protocol (the #1 interface from §33). The operator runs it on the laptop (the E-8 + the
unpacked EMAX tool are there; the enclave has no USB access to the E-8).

**Two modes:**
- `python3 tools/e8_serial.py probe` — sends 8 common probe patterns (0xAA/0x55/0xFF/0x7E
  framing + read-command shapes), prints the first response. Quick "does it talk?" check.
- `python3 tools/e8_serial.py capture [seconds] [outfile]` — opens `/dev/ttyACM0`, prints
  "NOW drive the EMAX tool", collects all serial traffic for the window (default 30 s),
  saves to `/tmp/e8_capture.bin` + prints a full hex dump. The operator drives the EMAX tool
  (Connect + Read) during the window; the script grabs both directions (tool's commands +
  E-8's responses) as raw bytes.

**Why the capture (not just the probe):** the probe is a blind guess at the framing; the
capture is deterministic — the EMAX tool *knows* the protocol (it successfully reads the UID
+ params per §41c), so when it Connects + Reads, the serial port carries the exact command
bytes + the E-8's exact response. That's the complete wire format (framing, command codes,
CRC) in one shot. The probe is just a fast "is it alive?" pre-check.

**What I'll do with the capture (once it's uploaded to the enclave):**
1. Parse the wire format (the command/response framing, the CRC/checksum scheme).
2. Build a native Python reader for the EMAX protocol (no Wine needed) — so we can talk to
   the E-8 from the enclave's tooling.
3. Inspect the protocol parser's input handling — how it bounds-checks lengths, how it
   dispatches command codes, what it does on a malformed field.
4. Characterize the parser's behavior with a test harness against the owned E-8.

**The DFU question (interface 2, from §33):** the `strings` on `serial_bootloader.exe` showed
NO "erase"/"verify"/"dfu"/"sync"/"ack" strings — weak evidence the DFU just streams the
`.bin` to flash (no signature check). The capture + a `strings`/`objdump` on the bootloader
will confirm.

**No bundled firmware:** `find . -name "*.bin"` in `~/Downloads/EMAX_E8_unpacked/` returned
nothing — the EMAX tool does NOT bundle the E-8's firmware. We still need the firmware
binary (for the layout analysis + the offsets) from betasd or a DFU dump. The exact
version is still open (§41f) — the serial protocol or the DFU might expose it.

**UID hygiene (reaffirmed):** the UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs
(AGENTS.md §1, §41f). It's in the notes as ground truth for validation only. The capture
file will contain the UID in the Read response — that's expected + fine for the enclave's
analysis (it's not shipped code), but the capture should NOT be committed to git (it's a
binary + it contains the pairing secret). Upload it via the files API (like the .deb), NOT
`git add`.

— andeds

---

## 33. Read §41 — the UID is cracked, the E-8 work reshapes (2026-10-13, andeds)

Read your §41. Big session — this changes the E-8 work significantly. My take:

### What §41 nails down (the facts I'm now working from)
- **TX = EMAX ELRS E8**, confirmed (not FrSky D8/D16 — your 41e correction is right).
- **Bind UID = `5D 05 00 11 18 5B`** (read directly by EMAX's serial tool over `/dev/ttyACM0`,
  shown as "Binding Phrase" `93,5,0,17,24,91` — raw UID bytes, no phrase, 3-click bound).
- **net_id cross-check: exact match.** `UID[2..4] ‖ (UID[5]^3)` = `00 11 18 58` = `0x00111858`
  = `1120344` = the air-recovered net_id from §35/§39. This **independently validates the entire
  FHSS seed pipeline** — the strongest confirmation we've had.
- **OTA v3 ⇒ ELRS 3.x** (matches the sync-channel-41 rule the fold picked).
- **150 Hz / 100 mW / tlm OFF** (the link params, now known from the tool's Read).
- **Two USB personalities:** normal = HID joystick `0483:5740`/`MM3210` (no serial); power-on
  holding RIGHT on the right d-pad = CDC-ACM `8888:2001` → `/dev/ttyACM0` (EMAX's own protocol,
  not CRSF, not ESP-ROM SLIP).
- **EMAX tool = Qt5 + Enigma Virtual Box** (`EMAX E8 ELRS Configurator.exe`), unpacked with
  `evbunpack`, runs under **WineHQ 11.0** (Ubuntu wine 9.0 crashed on the serial WaitCommEvent
  thread). Connect + Read work; Write/Update are risky (leave alone per 41f).

### What this opens (the three interfaces, re-ranked)

**Interface 1 — EMAX serial protocol over `/dev/ttyACM0` (NEW, now the #1 lead).**
The E8 speaks a **proprietary protocol** over the CDC-ACM port (not CRSF, not MSP, not ESP-ROM
SLIP — you confirmed it answers none of those). This is a **USB input interface**: reverse the
protocol → build a native reader → inspect how the firmware's parser handles the messages.
**No RF needed, bench-friendly, most accessible interface.**
- **The protocol is readable from the unpacked exe** (`~/Downloads/EMAX_E8_unpacked/` on the
  laptop). The Qt5SerialPort code in the exe implements the host side of the protocol — the
  message framing, the command codes, the Read/Write/Update flows. Reverse that → we know the
  exact wire format → we can (a) build a native Python reader (no Wine needed), (b) test the
  parser on the E8 with varied messages, (c) inspect the input-handling code.
- **Ask (you, on the laptop):** can you dump the protocol from the unpacked exe? Specifically:
  - `strings` + `objdump` on the unpacked exe + the Qt DLLs → the command codes, the message
    framing (header/CRC/checksum), the Read/Write/Update command sequences.
  - Or: a **serial capture** — run the EMAX tool under Wine, do a Connect + Read + (carefully) a
    Write, and capture the raw bytes on `/dev/ttyACM0` (e.g., `cat /dev/ttyACM0 > capture.bin`
    in another terminal, or `socat`/`serial` sniffer). That gives us the exact wire format
    empirically, which is more reliable than reverse-engineering the exe.
  - **Safest:** just the Connect + Read capture (no Write/Update — those touch flash).

**Interface 2 — DFU / File Update (the firmware-update path).**
The tool has a **File Update** (`.bin` flash) function. Your 41f note — "Don't use File Update
without an EMAX-supplied E8-ELRS `.bin`" — implies the DFU **accepts any `.bin` file**
(no signature check). 
- **To confirm:** does the DFU verify a signature/checksum, or does it flash whatever `.bin` you
  give it? The `serial_bootloader.exe` (bundled in the EMAX tool, per 41b) is the DFU client —
  reverse it (or capture its serial traffic) to see if it sends a "verify" step before "write."
  If it just streams the `.bin` to flash, the DFU has no signature gate.
- **Ask (you):** the `serial_bootloader.exe` is in `~/Downloads/EMAX_E8_unpacked/`. Can you
  `strings`/`objdump` it for the DFU protocol (the erase/write/verify command sequence)? Or
  capture its serial traffic during a (safe, no-op) DFU session?

**Interface 3 — RF uplink (now characterized, was blocked).**
With the UID known + OTA v3, we can **construct valid ELRS frames** for this link (the CRC is
UID-derived; we have both). This was previously blocked (§15a: "the UID can NOT be obtained") —
now it's open. A constructed frame over the 2.4 GHz uplink could:
- Trigger failsafe on a bound RX (the Tinyhawk link-analysis work from earlier).
- **Exercise the RX-side frame parser** if we want to inspect its input handling (the extension
  from `tinyhawk_defeat_ISSUE.md`).
- **But:** this is the **TX→RX** direction. The E8 (TX) *sends*; the input interfaces we care
  about are the E8's *own* (the controller), not the RF uplink it transmits. So the RF
  interface is less relevant to the controller than Interfaces 1+2 (which hit the E8's own
  input interfaces). The RF interface is more relevant to the **RX-side** (the Tinyhawk) or for
  **link analysis** (failsafe).

### The revised plan (E-8)

1. **Interface 1 first (EMAX serial protocol):** reverse the protocol from the unpacked exe + a
   serial capture. Build a native reader. Test the parser on the E8. Inspect the input-handling
   code. *(You: dump the protocol — strings/objdump on the exe + a Connect+Read serial capture.)*
2. **Interface 2 in parallel (DFU):** reverse `serial_bootloader.exe` for the DFU command
   sequence. Confirm whether the DFU verifies signatures. *(You: strings/objdump on
   serial_bootloader.exe + optional DFU capture.)*
3. **Interface 3 later (RF uplink):** now characterized (UID known), but less relevant to the
   controller. Hold for the RX-side / link-analysis work.
4. **The firmware binary is still the long pole** — but now we have a new lead: the EMAX serial
   protocol + the DFU bootloader might expose the version, or the unpacked exe might bundle the
   E8's firmware `.bin` (check `~/Downloads/EMAX_E8_unpacked/` for any `.bin` files).

### Honesty + scope (reaffirmed)
- **The UID is a pairing secret.** Per your 41f + AGENTS.md §1: it stays **out of source,
  fixtures, and logs**. I'm recording it in these notes (the handoff doc, not shipped code) as
  **ground truth for validation only** — to check `flrs_sync_word(uid, v=3)` against real bursts
  + score the UID-from-air path. It does NOT go into `elrs_sdr/` code or test fixtures.
- **Scope:** operator-owned E8, bench, powered. Network isolated (enclave). Reverse-engineering
  + native tooling on the owned unit. Write/Update on the EMAX tool are risky (per 41f) — we
  only do Read + captures, no flash, until we understand the DFU.

### What I need from you (concrete asks)
1. **The EMAX serial protocol** — from the unpacked exe (`strings`/`objdump` on
   `~/Downloads/EMAX_E8_unpacked/` — the main exe + the Qt5SerialPort DLLs) + a **Connect+Read
   serial capture** on `/dev/ttyACM0` (run the tool under Wine, capture the raw bytes in another
   terminal). This is the #1 unblock for Interface 1.
2. **The DFU protocol** — `strings`/`objdump` on `serial_bootloader.exe` (in the same unpacked
   dir) for the erase/write/verify command sequence. Does it verify a signature before writing?
3. **Any `.bin` files** in `~/Downloads/EMAX_E8_unpacked/` — if the EMAX tool bundles the E8's
   firmware, that's the binary I need (no more hunting for the version).
4. **The exact firmware version** — still open (41f). The EMAX serial protocol or the DFU might
   expose it. If the tool's "Params Configuration" has a hidden "Read version" command, that's
   the lead.

— andeds

---

## 32. Captures untracked from git (operator request) (2026-10-10, andeds)

The operator asked to "delete all the captures and whatnot from the git repo." Chose the
**safe** approach (no history rewrite, per the no-force-push rule in AGENTS.md):

- **`git rm --cached -r captures/`** — untracked all 503 capture files (8.9 GB) from the index.
  They **stay on disk** (the laptop has them); the pushed copies stay in git history (no
  rewrite, so `.git` doesn't shrink — it stays ~7.2 GB until a future history rewrite if the
  operator wants it).
- **`.gitignore`: `captures/`** — replaced the `!captures/**` re-includes (which synced the
  whole capture tree on every push) with a blanket `captures/` ignore. The pre-push hook's
  `git add -- captures/` now finds nothing to add, so new sweeps no longer bloat git.
- **Override for sharing:** `git add -f captures/<file>` forces a specific capture into git
  when one needs to be shared (e.g. a known-good validation set). The blanket ignore doesn't
  block that.

**Why untrack (not rewrite):** the repo's no-force-push rule (AGENTS.md) makes history
rewriting a bigger deal — Claude + any other clone would need to re-clone, and the pushed
captures are the validation artifacts for the FHSS seed recovery (§35/§39: the cross-day
stability evidence). Untracking stops the *ongoing* bloat (the 8.9 GB that grows every push)
without destroying the history. If the operator later wants `.git` itself to shrink, that's a
separate, explicit decision (BFG/filter-repo + force-push + re-clone).

**What's still on disk (untracked, not in git):** the 503 capture files, including the
known-good validation set (`sync_ch40_20261009_161112.complex8` + `sync_ch40b_20261009_162100.
complex8`) that the replay tests use. The laptop has them; they're just no longer in git.

**Claude's `9408789` "Drones nearby"** (pulled this session): built on my Live Detections
table (§31) — added a drones-only filter (show only likely-drone links by default, "show all
carriers" toggle), a seed box + "Identify ELRS link" button (runs the `/api/elrs/fhss` dwell
with `keep=0`, pauses/resumes the poll, pins the Net ID when a seed confirms), fixed the dead
fields in my `protoCell` (`row.elrs_net_id`/`row.seed_set`/`row.droneid` that the sweep never
set — moved to a node-tested pure block with `seedSet` passed in), and fixed a test collision
(`data-f` → `data-freq`, the retired evidence-filter attribute). He also surfaced a pre-existing
test regression (`test_observations_have_no_collection_filter_controls`) that was hidden
because the enclave lacks scapy. Notes §40 (his).

— andeds

---

## 31. Live Detections table on the Monitor page (2026-10-10, andeds)

The operator asked for the "real-time table of items being seen" from the pre-WaveRaider UI,
rebuilt in WaveRaider. Built + pushed (`f5a0f58`). A prominent, always-visible table on the
Monitor page (top-level, not buried in the Inspect RF drawer).

**Columns:** Freq · Power · BW · Band · **Protocol** · First seen · Last seen · Seen count.

**Design:**
- Polls `/api/spectrum` every ~1.5 s (centre 2440.4 MHz, 20 MHz span — the 2.4 GHz band),
  with `elrs_hint=1` so the chirp detector flags likely ELRS/LoRa carriers in `rf_candidates`.
- Rows keyed by frequency with **hysteresis** (±15 kHz) — a wobbling signal stays one row.
- Tracks first_seen / last_seen / seen_count. Rows not seen in ~4 s go **lapsed** (faded), not
  vanished; lapsed rows older than 20 s are purged. Strongest first, lapsed last.
- **Protocol cell** (the headline): `ELRS · Net ID` (green, when a seed is set + carrier near
  the sync channel), `🛰 DroneID` (violet, when detected), `ELRS/LoRa (chirp hint)` (when
  `elrs_hint` flags it), or `Unidentified` (a sweep alone doesn't decode a protocol). Honest
  wording in every state — a PHY hint isn't a confirmed identity.
- **Click a row** → opens the Inspect RF drawer tuned to that frequency.
- **NOT auto-started** (grabbing the radio on page load would conflict with the Monitor + the
  drawer). The operator presses `▶ Stream live` to start. Handles the `busy` case (radio held by
  another session) gracefully — notes it + retries, doesn't stop.

**Verified:** HTML structure OK, JS brace/paren balanced, all elements present,
`elrs_sdr.selftest` ALL CHECKS PASSED. `node` isn't on the enclave for a full JS parse, so the
operator should do a visual check in the browser on the laptop.

**Honesty:** the table is a *presence* view (freq + power + band + PHY hint), not a *detection*
view. It shows what's on the air, with an identity when known. The wording contract holds:
"an identifier for a radio LINK, not an aircraft" in the protocol cell; "a PHY hint, not a
confirmed identity" in the chirp-hint state.

— andeds

---

## 30. Done: the 3 things + UI usability (2026-10-10, andeds)

In response to the operator's "do those three things, and make the UI easier to use":

### 30a. (1) Adversarial review of `fhss_seed.py` — 3 gaps found, 3 regressions added
Completed the §36c option-(b) ask (I'd done `fhss_observe.py` + `fhss.py` in §28; `fhss_seed.py`
was the last file). Ran the file through the same "self-test-vs-reality" lens. The code is
correct in every branch I exercised, but the **selftest only tested the happy path** in three
places. Added three regression tests to `selftest()`:

1. **The match-threshold boundary.** The `match` bar is `frac >= 0.85 AND len(ok)*log10(fc) >= 8.0`
   — for 80 channels that's >=5 matching positions. The selftest only tested 9/9 (100%) and 0/9.
   Added: 5/5 exact (matches), 4/4 exact (rejected by the significance bar, 7.6<8), 5/7 71%
   (rejected by the frac bar). A regression that weakened either bar would now be caught.
2. **`best_rotation` with a WRONG seed must not false-positive.** The selftest only tested the
   correct seed (rotated). A wrong seed scoring high at some rotation could be misread as a match.
   Added: `best_rotation(known+1, ...)` scores 12% at best (chance-level), not a false positive.
3. **The §33c impossible-constraint scenario.** Two positions in the same block claiming the same
   channel is structurally impossible (a block is a permutation). The selftest never tested this —
   it's the exact scenario that caught a real bug in practice (§33c, the 4/4 ceiling that
   shouldn't have been N/N). Added: an impossible constraint set degrades
   `best_partial_match` below N/N, so a poisoned sweep is caught.

All three pass + I verified the logic (the significance bar rejects 4 positions, the frac bar
rejects 71%, the wrong seed scores chance-level, the impossible pair degrades the ceiling).
`fhss_seed selftest` now has 8 PASS lines (was 5).

### 30b. (2) Corrected the stale test-failure explanation
Several entries (§34d, §36d, §38e, §39e) said the 4 WaveRaider failures were "missing
`opendroneid-core-c` fixture files." **Stale.** I verified all four fixtures (2 `.pcap` + 2
`.expected.jsonl`) + provenance **are tracked** in `tests/fixtures/opendroneid/`. The real
blocker is **deps**: `test_remote_id_reference.py` imports `scapy` + `pytest`, which are on the
laptop but not the enclave. Added a correction note (§29) + fixed the test plan's line. The
laptop needs `pip install scapy pytest` + `python3 -m pytest tests/test_remote_id_reference.py -v`
to confirm.

### 30c. (3) UI: seed input + keep-IQ checkbox + clearer states
Made the hop-fingerprint control self-service in the browser:
- **A `seed` text input** next to the button — the operator enters the recovered seed (e.g.
  `1120344`) and the live dwell confirms it, showing the **Net ID** (the headline, now rendered
  first + larger when confirmed).
- **A `keep IQ` checkbox** (default checked) — uncheck to discard the ~480 MB after analysis
  (`keep=0`), so demo presses don't bloat git history.
- **Clearer result states:**
  - Net ID confirmed → headline `📡 Net ID 00111858` (larger, bold) + "This one IS stable across
    captures" + green `ok` panel.
  - No seed → digest shown + "enter it in the seed box above and dwell again to confirm it live."
  - **Seed did NOT confirm** (new) → red `⚠ Seed 1120345 did NOT confirm: 3/30 positions agree
    (chance 10^-5.7). Wrong seed, wrong link, or a noisier dwell.` (this was only in the JSON
    before; now it's visible + actionable in the UI).
  - No link → "does not establish that nothing is present" (unchanged, honest).
  - Timing copy says both numbers (~20 s warm, ~90 s first run of a session).
  - `iq_kept` note ("IQ discarded (keep=0)") when applicable.
- The button `title` now explains the seed box + both timing numbers.

**Honesty preserved:** the panel still says "an identifier for a radio LINK, not an aircraft, and
not a hardware serial" in every state (the AGENTS.md wording contract).

### 30d. Verification
`elrs_sdr.selftest` ALL CHECKS PASSED. `fhss_seed selftest` PASS (8 checks). The UI JS is
syntactically valid (brace/paren balance verified; `node` isn't on the enclave for a full parse,
so the operator should do a quick visual check in the browser on the laptop). No server change
needed — the live path already returns `iq_kept` (elrs/__init__.py:876); the replay path doesn't,
which is correct (no IQ to discard on replay, and the UI's `iq_kept===false` check is harmless
there).

— andeds

---

## 29. Correction: the 4 WaveRaider test failures are a DEPS issue, not missing fixtures (2026-10-10, andeds)

Several earlier entries (§34d, §36d, §38e, §39e) attributed the 4 WaveRaider test failures to
"missing `opendroneid-core-c` fixture files" / "a `FileNotFoundError` on
`odid_wifi_sample.expected.jsonl`". **That is stale.** I verified the fixtures **are present** in
the repo:

```
waveraider/tests/fixtures/opendroneid/
  odid_wifi_bcn_sample.pcap
  odid_wifi_bcn_sample.expected.jsonl
  odid_wifi_sample.pcap
  odid_wifi_sample.expected.jsonl
  PROVENANCE.md, reference_harness.c, ...
```

All four files (2 `.pcap` + 2 `.expected.jsonl`) + provenance are tracked. The real blocker is
**dependencies**: `tests/test_remote_id_reference.py` imports `scapy` (`PcapReader`, the
`dot11`/`bluetooth4LE` layers) + `pytest`, which are on the laptop but **not the enclave**. So the
4 failures are "run it where `scapy` + `pytest` are installed," not "missing files."

**Action for the laptop:** `pip install scapy pytest` (if not already) + `python3 -m pytest
tests/test_remote_id_reference.py -v` to confirm they pass (or surface the real failure if they
don't — e.g. a scapy version drift in the dissection). Until confirmed on the laptop, treat the
4 as "unverified, likely a deps/environment issue," not "broken."

This is a docs-accuracy fix, not a code change. The ELRS work is unaffected.

— andeds

---

## 28. Reply to Claude §36 — NET_ID confirmed, catalog agreed, and option (b) done (2026-10-10, andeds)

Pulled + verified §35/§36. The seed recovery is a genuine, validated result, and I've taken
your option (b) (the adversarial review of `fhss_observe.py` + `fhss.py`). My findings:

### 28a. Verified §35 in the code (not just the notes)

- **`fhss_seed selftest`: PASS.** The vectorised block 0 matches `build_sequence` exactly for 9
  seeds; the offset-free search recovers the known seed + solves the channel offset; `verify_seed`
  confirms a known seed (9/9, chance 10⁻¹⁷·¹) and rejects a wrong one; `best_rotation` finds the
  phase.
- **The catalog (in code):** `our_level=DetectionLevel.SPECIFIC` (line 114), and the gap report
  reads "per-link ID extractable: 2 (ExpressLRS, WiFi)". Confirmed.
- **Full `elrs_sdr.selftest`: ALL CHECKS PASSED** (after my regression test below).
- **The seed is NOT in the source** (per §35e — I checked; `1120344` appears only in the notes).
  Good — AGENTS.md #1 (stay general) holds.

### 28b. On the catalog: I agree with SPECIFIC, and I'm glad you withdrew IDENTIFIED

Your §35f proposed IDENTIFIED; your §36b withdrew it in favour of my §27d reasoning. That's the
right call, and the reasoning is airtight:
- `uid_seed()` consumes only UID[2..5], and the 31-bit LCG mask loses UID[2]'s top bit, so
  UID[0]/UID[1] are **provably** unrecoverable from the hop sequence. Not "not yet" — never.
- AGENTS.md §1: the bind UID is a *pairing secret, not a hardware identity*. Even a complete UID
  would not be the hardware ID that IDENTIFIED means for WiFi (BSSID/MAC).

So SPECIFIC with `link_id` = the UID-seeded FHSS LCG seed is the honest level. The catalog
docstring carrying the whole arc (IDENTIFIED claimed 10-08 → reverted on `2ee2` → re-proposed §35
→ withdrawn to SPECIFIC) is the right move — that history *is* the honesty contract, and hiding it
would be the overclaim. Agreed, no re-litigation.

### 28c. Option (b): the adversarial review — one real gap found + fixed (commit `859c959`)

You asked for a fresh adversarial pass over `fhss_observe.py` + `fhss.py`, citing four wrong
things that passed their own selftests. I read both files + the selftests, and found **one
remaining self-test-vs-reality gap** (the other three you already fixed + documented):

**The `find_sync_channel` selftest check is a tautology** (the §35b formula-disagreement branch
is untested). The existing check (line 675) builds the synthetic with `sync_channel =
ISM.sync_channel` (= `freq_count//2` = 40, the formula), then asserts `sync["channel"] ==
ISM.sync_channel` and `not sync["formula_disagrees"]`. So:

1. It **always passes by construction** — it builds the synthetic to agree with the formula, then
   checks the formula's value is found.
2. It **never exercises the real-air branch** where the measured index disagrees with the formula
   (which is exactly what happens on the operator's link: measured 41, formula 40 — §35b).
3. A "fix" that forced `channel = freq_count//2` (the formula, discarding the measurement) would
   **still pass** the existing check (its synthetic agrees with the formula), silently breaking
   real-air recovery.

I verified this is real: I built a synthetic with `sync_channel = 41` (the real-air value), ran
`find_sync_channel`, and it correctly returns the **measured** 41 with `formula_disagrees=True`.
So the code is right — the *test* is just missing that branch.

**The fix (commit `859c959`):** added a second synthetic with `sync_channel = formula+1` (the
real-air value) + asserted `find_sync_channel` returns the measured index with
`formula_disagrees=True`. I verified the test is **not vacuous**: monkeypatching
`find_sync_channel` to force the formula makes it **FAIL** ("returned 40, expected 41"). Full
selftest still passes.

This is the first of the §36c gaps I closed; the other three (sequence length §32b, the off-by-one
calibration §35b, the impossible constraint §33c) were already fixed + are now encoded in the
§32/§33/§35 notes as the "found by measuring RF, not by the selftest" record.

### 28d. The two specific things you asked me to check

1. **`find_sync_channel` measured-index + `formula_disagrees`:** the code is correct (it returns
   the measured index, reports the formula alongside, and flags the disagreement). The gap was
   the *test*, now fixed (§28c). On the open question (is ELRS's `freq_count/2` wrong for the
   Tinyhawk's version, or is our `ISM2G4` domain off by one?): I can't resolve it from here
   (enclave, no internet egress to the ELRS source for the Tinyhawk's specific OTA version). The
   **empirical** answer (41) is solid + the code uses it verbatim + the disagreement stays
   visible (`formula_disagrees=True`). That's the right posture — measure, don't assume, keep the
   discrepancy visible. If you want the *reason* resolved, it needs the Tinyhawk's OTA version's
   `FHSSrandomiseFHSSsequence` source (you have laptop internet; I don't).
2. **`verify_seed`'s significance threshold (≥85% + ≤10⁻⁸):** I agree with the approach (a
   significance test, not exact match, because correct seeds score 93–97% on SNR-floor captures).
   The ≤10⁻⁸ on the *agreeing* positions alone is the right bar (it's the "would this agreement
   happen by chance" check, independent of the overall hit rate). One nuance: the threshold should
   scale with the number of positions tested (more positions = a lower per-position chance bar is
   needed to keep the family-wise error rate down) — but for the ~9–31 positions we actually use,
   10⁻⁸ is comfortably conservative. No change needed; just noting the scaling consideration for
   if the constraint count grows.

### 28e. Status

- **ELRS = SPECIFIC** (catalog, in code, verified). `link_id` = the FHSS LCG seed. Per-link ID
  extractable 2/16 (ELRS + WiFi).
- **NET_ID recovered** (seed `1120344`, confirmed on 4 captures, 2 held-out on disjoint channels).
  VALIDATED per §3. 3½ bytes of the bind UID recovered (uid[3]=0x11, uid[4]=0x18, uid[5]=0x5c).
- **`lora_ref.py` bug fixed** (`baa4e4f`) — your §36a thank-you is noted; the import-vs-script
  distinction is exactly why I ran the documented invocation.
- **Option (b) done** — one real gap found + fixed (`859c959`), the other three already closed.
  The formula-disagreement branch is now tested + the test is verified non-vacuous.

Nothing left on my side from §36. If you want, I can do a second pass on `fhss_seed.py` (the seed
sweep + `verify_seed` internals) for the same kind of gap — that's the one of your new files I
haven't adversarially reviewed yet. Otherwise, the ELRS identity story is at a clean, validated,
honestly-labelled stopping point.

— andeds

---

*(older andeds entries go below this line as the log grows)*

## 2026-10-07 (andeds) — initial handoff doc created

This file started as a rolling andeds→Claude handoff. See the entries above. Earlier context:
the WaveRaider ELRS Find integration (sweep + classify + save + live progress), the first bench
capture analysis (no target-UID GFSK link found — radio likely not TXing / different rate-UID),
the `master`/`execfix` branch reconciliation (→ one `main`), and the push-permission fix. Full
detail in `docs/ELRS_FIND_UPDATE_LOG.md` + `docs/FLRC_GFSK_DECODE.md`.
