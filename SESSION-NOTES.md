# Shared Running Session Log

Started 2026-10-15 by andeds/orchestrator. This is the canonical append-only
handoff for Claude Code, Codex, and other project agents.

## Running-log convention

Before work, pull `main`, read `AGENTS.md`, and read the newest relevant log
entries. Before handoff, append a dated, agent-attributed section containing:

1. starting revision and objective;
2. scope, authorization, and device-specific prohibitions;
3. actions performed and evidence locations;
4. what worked and what it establishes;
5. what failed, was blocked, or remained inconclusive;
6. private-data handling and safety state;
7. files changed, validation run, and commit/push status;
8. the next smallest useful steps and work that should not be repeated.

Do not erase or silently rewrite prior entries. When evidence changes an older
conclusion, append a correction and name the superseded section. Never place a
credential, pairing UID, raw private response, identifying capture, or other
secret in this tracked log; point to an ignored `work/` location instead.

> **Current roadmap notice (2026-10-27):** `PROJECT-ROADMAP.md` is the sole
> active priority and status source. This file remains the append-only evidence
> and handoff log. Historical "way ahead" and "next steps" sections below are
> preserved but do not activate or reprioritize work; see §62 for the
> consolidation decision.

## THE WAY AHEAD — read this first

**Current owner decision (2026-10-15): §20 supersedes earlier WiFi-first and
multi-chain priority statements.** The first benchmark is the owned E8–TinyHawk
ELRS pair, without a WiFi entry step. Earlier sections remain as historical
research and must not be treated as active task assignments.

**From the orchestrator (andeds), 2026-10-15.** I have now done a full code-level
review of this repo (all 82 files, all 8 differential-review findings verified in
the actual code, both runnable test suites re-executed). This section is the
authoritative way forward. It supersedes the "Open Questions" at the bottom of
`AGENTS.md` — those are now answered or re-scoped below.

**The one-sentence state:** the research is complete and honest, but the
operational attack tools are **not yet a working CTF capability set** — the three
P1 protocol defects mean the headline tools (ELRS takeover, MAVLink command
injection, signing replay) would **not work against a correctly-implemented
target on game day**. The fix is not to patch all eight findings in parallel. It
is to **pick one attack path and drive it to end-to-end validity**, using the
validated ELRS core from the companion `elrs-sdr` repo instead of the
single-version re-implementations in this repo.

---

## 1. What the orchestrator verified (evidence class: VALIDATED, ran on 2026-10-15)

| Check | Result |
|---|---|
| `python3 -B tools/validate-project.py` | PASS (syntax, JSON, 11 firmware hashes + register, rainbow CSV/JSON, CVE index, local links) |
| `python3 -B -m unittest discover -s tools/tests` | 33 passed |
| Root `tests/` (pytest) | 90 passed (per `Harness_DIFFERENTIAL_REVIEW_2026-10-15.md` after `3737029`; not re-runnable on the enclave — no pytest, no egress) |
| All 8 findings in `Harness_DIFFERENTIAL_REVIEW_2026-10-15.md` | **Re-confirmed in code** — see §3 below for the exact locations I re-verified |

The differential review is accurate. I did not find additional P1s, and I did
not find any of the P1s to be false. The review's own caveat stands: it covered
the focused paths (serial, ELRS analyzer/forger/emulator, MAVLink signing/raw
command, passive interceptor, dashboard, E8 evidence); the **Wi-Fi/Tello chain
and the GPS-spoof / BLE-recon tools were not in that focused set** and have not
yet had an equivalent code-level pass.

---

## 2. The decision the owner must make (blocks everything else)

**Which attack path will the CTF actually test?** This is the single question
that determines the entire work sequence. The three paths are at very different
levels of readiness:

| Path | What's real | What's broken | Effort to make it game-day-real |
|---|---|---|---|
| **ELRS (drone RF takeover)** | Analyzer, forger, TX emulator, UID rainbow (723 phrases), full chain, firmware corpus | P1-2 (hardcoded OTA v4), P2-4/5 (SDR = magnitude-threshold / chirp-prototype), P2-7 (rainbow loader drops all 723 entries) | **HIGH** — needs the real LoRa PHY (demod + encode). This is the same work the `elrs-sdr` repo has been doing. |
| **MAVLink (GCS / autopilot)** | Fuzzer, injector, recon, signing-replay, command builders, full chain | P1-1 (signing bypass doesn't bypass), P1-3 (COMMAND_LONG wire order wrong), P2-6 (replay viability = clock age) | **MEDIUM** — pure protocol conformance, no RF, no hardware, fixable offline against pymavlink. Fastest path to a working tool. |
| **Wi-Fi / Tello** | Tello takeover, video capture, Wi-Fi recon script | **Not yet reviewed** — needs its own code-level pass before we can estimate | **UNKNOWN** |

**Do not start work until the owner names the path.** If the owner does not know
yet, the owner should gather §4 (the CTF facts) from the competition organizers
before spending agent-hours.

---

## 3. The P1 defects, re-verified in code (so you don't re-derive them)

These are the three that make the headline tools non-functional. Exact locations
I re-confirmed on 2026-10-15:

1. **P1-1 — MAVLink signing "bypass" does not bypass.**
   `tools/mavlink-signing-replay.py:202` (`rebuild_with_link_id`) and `:230`
   (`rebuild_with_timestamp`). Both mutate authenticated fields (link_id /
   timestamp) and **retain the old signature** when no `secret_key` is supplied.
   Both CLI mutation paths call them keyless. The MAVLink v2 signature covers
   header + payload + link_id + timestamp — mutating any of those without
   re-signing produces a packet a correctly-validating receiver rejects. The
   module docstring's claim of "cross-link replay (change link_id to bypass
   timestamp tracking)" is **not what the spec allows**. The signing spec's
   anti-replay is per-stream state, not a 60-second clock window (that is P2-6).

2. **P1-2 — ELRS analyzer hardcodes the OTA version.**
   `tools/elrs-analyzer.py:39` sets `OTA_VERSION_ID = 4` as a constant and
   `uid_to_crc_init()` XORs `uid[5] ^ 4`. The version must come **from the
   packet**, not a module constant. A v3 or v5/v6 target decodes with the wrong
   CRC init and silently produces garbage. This is the "measure, don't assume"
   violation the `elrs-sdr` repo's `elrs_sdr/core/ota.py` (v2–v6 parameterized)
   already solved.

3. **P1-3 — COMMAND_LONG builder has the wrong wire field order.**
   `tools/exploit_chains/mavlink_chain.py:391` packs
   `struct.pack("<BBHBfffffff", target_sys, target_comp, command, confirmation, *params)`.
   MAVLink v2 applies **size-based field reordering** — the seven `float` fields
   serialize *before* the small `B/B/H/B` fields. The review's in-memory
   reproduction (command=512 → decodes as command=0, target=0) is correct. The
   same defect is in `tools/mavlink-signing-replay.py:591`. **The advertised
   commands do not deliver the command they claim to.** The `pymavlink`-based
   injector/fuzzer use a different (correct) serialization path and are not
   implicated.

The five P2s (S/IQ demod is magnitude-threshold, TX emulator is a fixed-SF6
chirp with a 3-vs-3.125 samples/chip timing error, replay-viability = clock age,
rainbow-table loader drops all 723 entries on a key-name mismatch, CI omits the
90-test root suite) are real but secondary — fix them *on the chosen path*, not
across all paths.

---

## 4. INFORMATION TO GATHER — the CTF facts (owner, from the organizers)

The "Open Questions" in `AGENTS.md` are the real blocker, not the code. Until
these are answered we are building in the dark. **The owner should get these
from the competition organizers / rules packet before we commit to a path:**

| # | Question | Why it matters |
|---|---|---|
| Q1 | **Exact target platform(s):** drone firmware + version, GCS software + version, autopilot (if any) + firmware, radio link (ELRS? which version? which rate? Crossfire? Wi-Fi? MAVLink over what transport?) | Determines which of the three paths is in scope, and whether our firmware corpus (Betaflight 2026.6.2 / ELRS 4.1.0) matches the real target. A version mismatch is a silent no-op. |
| Q2 | **What is the flag, and where does it live?** (RF-captured telemetry? GCS UI? serial console? a file on the aircraft? a command the aircraft executes?) | Determines the *success criterion* — we build toward "flag captured", not "packet accepted". A packet the receiver accepts is not a flag. |
| Q3 | **What RF / network interfaces are actually exposed on game day?** (2.4 GHz RC link? 5.8 GHz analog FPV? Wi-Fi? BLE? GPS? a ground-station Ethernet/Wi-Fi network?) | Scopes the attack surface. If the GCS is on a Wi-Fi LAN, the Wi-Fi path may be the real target, not the RF link. |
| Q4 | **What is allowed / disallowed?** (jamming? GPS spoofing? active injection? deauth? physical proximity limits? rate limits on TX power?) | Some of our tools (GPS spoof, Wi-Fi deauth, ELRS TX) may be rule-excluded. Don't build what you're not allowed to use. |
| Q5 | **Scoring model:** is commandeering (RCE / command execution) worth more than DoS (denial / disruption)? Is partial credit for "receiver accepted our frame" a thing? | Determines whether we optimize for the full RCE chain or a lighter DoS that's easier to make reliable under time pressure. |
| Q6 | **Do we get a pre-game recon window?** (can we capture the target's RF / network before the timed round? can we read the binding phrase / UID from a pre-game observation?) | If yes, the "air-recovery of UID" problem (the hard part of ELRS identify) may be solvable offline before game day, which de-risks the ELRS path substantially. |
| Q7 | **Physical layout + proximity:** how close can we get to the target? Line-of-sight? What's our position relative to the drone and the GCS? | Affects RF feasibility (a B205 at 50 m is a different problem than at 5 m) and whether a close-range / short-range attack is viable. |
| Q8 | **Hardware we get to use on game day** vs. hardware we only have in the lab. (Can we bring the B210? The ALFA cards? A rogue GCS? A second TX?) | The lab list in `AGENTS.md` is what we *have*, not necessarily what we can *bring*. |
| Q9 | **Is the target's binding phrase / UID known or recoverable before the round?** (Did the organizers publish it? Is the drone freshly bound? Can we observe the bind?) | The ELRS attack chain's first step is UID recovery. If the phrase is published or the bind is observable, the whole chain gets easier. If not, we need the air-recovery path, which is the hardest open problem in the `elrs-sdr` repo. |

**Until Q1–Q5 are answered, do not write attack code.** Gather these facts, then
re-convene to pick the path.

---

## 5. IF ALL THE INFORMATION IS THERE — the steps (the execution plan)

Assume Q1–Q9 are answered and a path is chosen. The sequence, with owners:

### Phase A — Path commitment (owner + andeds, 1 session)
1. Owner answers Q1–Q9 (above).
2. We pick **one** primary path. Record it here and in `AGENTS.md` "Current
   State". The other two paths are parked, not deleted — they stay as
   research, not as parallel workstreams.
3. andeds confirms the chosen path's target version matches our firmware corpus
   (Betaflight / ELRS / GCS versions). If it doesn't, the corpus needs a
   refresh before any tool work.

### Phase B — Protocol conformance (Codex implements, Claude QCs, 1–2 weeks)
4. **Fix the P1s on the chosen path against the reference implementation.**
   - MAVLink path → against `pymavlink` (the generated serializers are the
     reference; the hand-rolled `struct.pack` builders are the defect).
   - ELRS path → against the pinned `firmware/src/expresslrs-4.1.0` source
     **and** the `elrs-sdr` repo's `elrs_sdr/core/` (the validated,
     version-parameterized CRC/FHSS/OTA/LoRa). The poc-research tools should
     **consume `elrs_sdr/core` as a library**, not re-implement the protocol
     layer. This one structural move eliminates P1-2 and most of the ELRS P2s.
5. **Add the missing regression tests.** The review noted "no direct tests of
   these raw builders" for each P1. Every fixed builder gets a test that
   round-trips against the reference (encode with our tool, decode with
   pymavlink / the pinned ELRS struct, assert equality).
6. **Close P2-8:** add the 90-test root suite to `.github/workflows/validate.yml`
   so a green CI badge actually covers the protocol tests.
7. **Fix P2-7** (rainbow-table schema mismatch) if the ELRS path is in scope —
   the loader reads `uid_3_5` but the JSON has `uid_hex`/`uid_bytes`/
   `sync_bytes`; it silently indexes zero of the 723 entries.

### Phase C — RF / hardware validation (laptop + SDR, 2–4 weeks, ELRS path only)
8. Wire the TX/forge side to a **real LoRa PHY** — either the `elrs-sdr`
   validated demod/encode or a build from the pinned ELRS 4.1.0 source. The
   current `elrs-tx-emulator.py` SoapySDR backend is a fixed-SF6 chirp
   prototype with a timing error; it is not SX1280-compatible and must not be
   treated as a working transmitter.
9. **Bench-test against the owned TinyHawk + E8** (read-only first, per the
   no-flash constraint in `AGENTS.md`). Confirm: our forged frame is accepted by
   the owned receiver, the command executes, the positive-command (throttle=1000)
   case works.
10. **End-to-end:** forge → transmit → receiver accepts → command executes →
    (if Q2 says so) flag captured. Document the exact command sequence and its
    output.

### Phase D — Game-day prep
11. Update `docs/game_day_playbook.md` with the **actual working tool**, its
    exact invocation, its limitations, and the failure modes. The current
    playbook describes tools that don't yet work — it must be re-grounded in
    Phase C output.
12. **Dry-run the full chain in the home lab** under the time limit the CTF
    will impose. Record where it breaks under pressure.
13. **Document what does not work** (honesty contract). A playbook that claims
    a capability the tool doesn't have is worse than no playbook — it wastes
    game-day minutes.

---

## 6. Division of labor (who does what)

| Agent | Machine | Owns |
|---|---|---|
| **andeds** (orchestrator) | enclave (no SDR, no egress, no internet) | This notes file, code review, offline analysis of uploaded captures, the `elrs-sdr` companion repo, integration decisions, the "pick one path" gate |
| **Codex** | laptop `borealis` (SDR, E8, internet) | Primary implementation — Phase B protocol conformance, Phase C RF work |
| **Claude** | laptop `borealis` | Adversarial QC + research — verify Codex's fixes in the actual code (not just the notes), independent test fixtures, CVE/evidence audit |

**Standing rules (unchanged):** every commit includes the notes entry in the
same commit (code + notes together); verify the other agent's claimed fixes in
the code, not the notes; label every finding with its evidence class
(VALIDATED / SELF-CONSISTENT / SYNTHETIC); one branch (`main`); no force-push;
the E8 no-flash constraint is absolute.

**The `elrs-sdr` integration:** the companion repo (`gitlab01.an.dd:
andeds-agent/elrs-sdr-transfer`) is the validated ELRS decode/identify stack.
The poc-research repo should **import from it**, not duplicate it. andeds owns
keeping the two consistent. Codex + Claude should not fork `elrs_sdr/core`
into this repo — that's how the single-version re-implementations (the source
of P1-2) happened in the first place.

---

## 7. What is explicitly NOT to be done

- **Do not fix all eight findings in parallel across all three paths.** That
  produces three half-working tools, not one working tool.
- **Do not build attack code before Q1–Q5 are answered.** We are building in
  the dark until the target is known.
- **Do not treat the `elrs-tx-emulator.py` or the passive-interceptor SDR path
  as operational.** They are prototypes. Label them as such in the CLI output,
  not just in a source comment (P2-4/P2-5).
- **Do not flash the E8.** Absolute. No exceptions, no "just to test".
- **Do not commit secrets.** The binding UID is a pairing secret, not a
  constant in code/fixtures/logs. (The AGENTS.md "GitHub PAT leak" note is the
  reminder.)
- **Do not force-push or rewrite history.**

---

*— andeds, orchestrator, 2026-10-15. This section is the current way ahead.
When the owner answers §4, we update §2 (path commitment) and §5 (the active
phase) in place.*

---

## 8. `elrs-sdr` integration assessment (Claude, 2026-10-15)

The owner asked for a review of the companion repo (`~/Drone_Project/elrs-sdr-transfer`,
`git@github-personal:oatmealphysics/research-project.git` on the laptop vs.
`gitlab01.an.dd:andeds-agent/elrs-sdr-transfer` on the enclave) to identify what
should be pulled into this CTF research project. This section is the inventory
and recommendation — **no code was merged, no files were copied**. The owner
decides what to act on.

### 8.1. Repo overview

875 files. A Python package (`elrs_sdr/`) implementing SDR-based ELRS detect →
identify → (gated) execute pipeline, plus a WaveRaider web dashboard, an
OpenDroneID decoder, E8 protocol reconstruction, and a DroneSecurity reference
corpus. ~13,000 lines in the core package. Real IQ captures from the owned
TinyHawk III. Self-test suite (29 checks) validates the protocol layers without
hardware.

### 8.2. Tier 1 — High value, directly applicable to CTF game day

These are the items that solve open problems in this repo or replace
single-version reimplementations with validated exact ports.

| # | Item | `elrs-sdr` location | Lines | What it does | What it fixes in this repo |
|---|---|---|---|---|---|
| **T1-1** | **ELRS core protocol library** | `elrs_sdr/core/{crc,ota,fhss,lora,lora_payload,lora_ref,lora_sync}.py` | ~2,350 | Exact Python ports of CRC-14/16, OTA packet model (build/validate/pack/unpack), FHSS LCG sequence, LoRa PHY. Self-tested against the C source, encode↔decode round-trip proven. | Replaces the hand-rolled crypto in `tools/elrs-packet-forger.py`, `tools/elrs-analyzer.py`, `tools/elrs-uid-rainbow.py`. Eliminates **P1-2** (hardcoded OTA v4) — the core is version-parameterized (v2–v6). This is the structural fix §5 Phase B step 4 calls for. |
| **T1-2** | **GFSK/FLRC demodulator** | `elrs_sdr/core/gfsk.py` | 456 | SX1280 FLRC PHY: soft-Viterbi convolutional decode (K=7, CR 1/2), CRC-24 (poly 0x0186), UID-seeded 32-bit sync word. 100% byte-exact round-trip at ~20 dB on random payloads. | **Critical discovery from the elrs-sdr project:** the TinyHawk III's default air rate (`rate_index=2`) is **FLRC/GFSK, not LoRa**. The poc-research tools assume LoRa modulation. Without this PHY, passive capture and decode of the actual target drone's signal will not work. `elrs-tx-emulator.py`'s fixed-SF6 chirp prototype (P2-5) is building the wrong modulation entirely. |
| **T1-3** | **FHSS seed recovery** | `elrs_sdr/core/fhss_seed.py` | 541 | Recovers the 31-bit FHSS LCG seed from observed hop positions using vectorized numpy brute-force (progressive filtering: first constraint keeps ~1/80 of 2^31 candidates; ~5 observations pin the seed uniquely). Validated against `fhss.build_sequence`. | This is the Fox-IT attack path implemented in code. Capture a few hop positions → recover the full FHSS sequence → follow every hop → full link hijack. Described in this repo's research but not implemented. Combined with T1-2 (decode the FLRC bursts to identify hop channels) this closes the "passive UID recovery" problem. |
| **T1-4** | **ELRS air-state identifier** | `elrs_sdr/core/identify.py` | 581 | Version-agnostic classifier: sweeps OTA versions 2–6 × all 6 air rates × both PHY modes (LoRa + FLRC), gates on CRC validity. Recovers UID from the sync word without operator-supplied UID. Returns structured `AirState` (version, mode, rate, UID, channel, freq_offset, CRC valid, confidence). | The "identify the target's ELRS version, rate, and UID from passive SDR capture" tool. Goes from IQ capture to actionable attack parameters (UID, FHSS seed, CRC init, OTA version) in one step. This is the reconnaissance layer `tools/elrs-analyzer.py` was supposed to be — but version-agnostic and PHY-agnostic. |
| **T1-5** | **UHD radio wrapper** | `elrs_sdr/radio.py` | 1,047 | B210/B205mini UHD device driver: symmetric RX+TX, multi-UHD-version compatibility (4.2/4.9/4.11), `.complex8` file I/O, automatic subdev/antenna selection. Tested on the laptop's B205mini. | The poc-research SDR scripts (`tools/sdr_common.py`, `tools/elrs-tx-emulator.py`) use a SoapySDR abstraction. `radio.py` is UHD-native and already proven on the owned B205mini on this laptop. If the ELRS path is chosen, this is the hardware driver. |
| **T1-6** | **E8 native reader** | `tools/e8_read.py` | 85 | **Confirmed** E8 serial protocol: send 64 bytes (`02 01` + 62 zeros at 115200 8N1), receive 17-byte config response (`02 c5 <rate> <power> <tlm> <UID:6> <padding:6>`). Validated on live hardware with byte-for-byte match. | The poc-research `tools/e8-read-config.py` (Codex) was written without the confirmed protocol. `e8_read.py` is the working, validated reader. **Entry procedure:** power-on holding RIGHT on the right d-pad → center LED flashing green → `/dev/ttyACM0` is live. No Wine, no vendor tool needed. |
| **T1-7** | **E8 protocol reconstruction** | `references/e8_protocol_reconstruction.md` | ~200 | Full reverse-engineering log: confirmed Read command format, CRC-16/XMODEM (poly 0x1021, init 0x0000) over 60-byte body, rate/power/UID byte layout, Write command TX layout (02 02 header), Update multi-stage framing. | Completes this repo's E8 characterization work. The confirmed protocol answers every open question in `E8-SERIAL-CHARACTERIZATION.md` and `research/E8-USB-CHARACTERIZATION.md`. |
| **T1-8** | **IQ capture of the owned drone** | `captures/fhss_20261010_113103/` | 480 MB | `.complex8` IQ capture at 2440.4 MHz, ~10 MS/s, 12 seconds — the owned TinyHawk III transmitting FLRC at rate_index=2. Includes `.meta.txt` with center freq, sample rate, timestamp. | **Real test data from our actual target.** The poc-research repo has no RF captures. This is ground truth for validating any decoder, and it already demonstrates what the real signal looks like (FLRC, not LoRa). |

### 8.3. Tier 2 — Game-day situational awareness

These are useful for recon and target identification during the competition
but not directly part of an attack chain.

| # | Item | `elrs-sdr` location | Lines | What it does |
|---|---|---|---|---|
| **T2-1** | Multi-band drone scanner | `elrs_sdr/dronescan.py` | 989 | Sweeps 2.4 GHz / 5.8 GHz / sub-1G / 433 MHz on the B205/B210, classifies energy by PHY signature (LoRa chirp, OFDM/WiFi, FHSS, analog video). Game-day recon: "what protocols are the CTF drones using?" |
| **T2-2** | Protocol catalog + detection levels | `elrs_sdr/protocol_catalog.py` | 366 | 16-protocol detection matrix with honest confidence levels (NONE → PRESENCE → FAMILY → SPECIFIC → IDENTIFIED). Quick reference for what the SDR can and can't distinguish. |
| **T2-3** | OpenDroneID decoder | `elrs_sdr/opendroneid.py` | 453 | ASTM F3411 Remote ID: serial number, operator ID, position from BLE/WiFi beacons. If CTF drones broadcast Remote ID (regulatory requirement on newer hardware), this reads identity for free — no attack needed. |
| **T2-4** | Sensor fusion layer | `elrs_sdr/fusion.py` | 349 | Merges events from RemoteID + SDR scan + DJI DroneID into one identity-keyed track table. "Every drone around me, protocol, identity" in one view. |
| **T2-5** | WaveRaider web dashboard | `waveraider/` | ~6,300 | Live web UI: drone tracks, spectrum overlay, ELRS Find, protocol classification. Real-time situational awareness during competition. |
| **T2-6** | ELRS burst classifier | `elrs_sdr/core/bursts.py` + `positivedet.py` | ~600 | PHY-level burst detection and LoRa chirp / GFSK / OFDM / analog classification. Feeds the protocol scanner. |

### 8.4. Tier 3 — Reference documentation

| Item | Location | Value |
|---|---|---|
| TinyHawk FLRC discovery | `docs/FLRC_GFSK_DECODE.md` | Documents that rate_index=2 is FLRC, not LoRa — the root cause of "the decoder can't see the drone." Essential context. |
| LoRa sync template spec | `docs/ELRS_LORA_SYNC_SPEC.md` | Exact sync detection parameters extracted from a commercial C-UAS SDR's firmware. |
| FHSS test plan | `docs/WAVERAIDER_FHSS_TEST_PLAN.md` | Methodology for validating FHSS sequence recovery against real captures. |
| Counter-UAS SDR deep dive | `docs/COUNTER_UAS_SDR_FIRMWARE_DEEP_DIVE.md` | Analysis of a production counter-UAS SDR detection pipeline — architecture reference. |
| DroneSecurity IQ samples | `DroneSecurity/samples/` | DJI Mavic Air 2 and Mini 2 IQ captures + QPSK/Gold-code demodulators for DJI DroneID. Useful if CTF includes DJI targets. |
| Drone RF architecture doc | `docs/DRONE_RF_ARCHITECTURE.md` | How the multi-source detection pipeline was designed to generalize across protocols. |

### 8.5. What to skip

| Item | Why |
|---|---|
| Session logs, conversation summaries, agent handoff notes (`docs/CONVERSATION_*.md`, `docs/*NOTES*.md`) | Process artifacts, not deliverables. |
| WaveRaider Docker/CI infrastructure | Different deployment context. |
| DroneSecurity as a whole (except samples) | Third-party reference code, already vendored there. |
| `AGENTS.md`, `SECURITY.md` from that repo | Different scope/rules. This repo has its own. |
| `waveraider/` tests, release notes, changelogs | Only relevant if importing the full WaveRaider package. |

### 8.6. Critical finding — the FLRC problem

The single most important thing from the `elrs-sdr` repo is a **hardware fact**:

> **The TinyHawk III at its default air rate (`rate_index=2`) transmits FLRC/GFSK,
> not LoRa.** The `elrs-sdr` team discovered this empirically — their LoRa-only
> decoder was completely blind to the signal. The FLRC modulation uses a different
> PHY entirely: GFSK with Gaussian BT=1, convolutional coding CR 1/2 (K=7),
> CRC-24 (not CRC-14/16), and a 32-bit UID-seeded sync word (not the LoRa sync).

The poc-research tools (`elrs-analyzer.py`, `elrs-tx-emulator.py`,
`elrs-packet-forger.py`) all assume LoRa modulation. The `elrs-tx-emulator.py`
builds a fixed-SF6 LoRa chirp (P2-5). **If the CTF target runs a FLRC rate
(common default), these tools produce a signal the receiver cannot parse.**

The fix is T1-2 (`gfsk.py`) + T1-4 (`identify.py` — which auto-detects LoRa vs
FLRC). This aligns with the orchestrator's §5 Phase B recommendation to import
`elrs_sdr/core` as a library rather than maintaining separate reimplementations.

### 8.7. Integration recommendation

**If the ELRS path is chosen (per §2):**

1. Add `elrs-sdr-transfer` as a git submodule or copy `elrs_sdr/core/` into
   `tools/elrs_sdr_core/` (submodule preferred — keeps provenance, avoids
   drift, matches the orchestrator's "import, don't duplicate" directive in §6).
2. Rewrite `tools/elrs-analyzer.py` to call `elrs_sdr.core.identify` instead of
   its own single-version decode. Fixes P1-2.
3. Rewrite `tools/elrs-tx-emulator.py` to use `elrs_sdr.core.gfsk` for FLRC
   modulation and `elrs_sdr.radio` for UHD TX. Fixes P2-5.
4. Replace `tools/elrs-packet-forger.py`'s CRC/FHSS/OTA with calls to
   `elrs_sdr.core.{crc,fhss,ota}`. Removes the re-implementation risk.
5. Copy `tools/e8_read.py` into `tools/` — it's the confirmed E8 reader.
6. Copy the IQ capture to a local analysis directory (not in git — 480 MB).
7. Import T2-1 (`dronescan.py`) as the game-day protocol scanner.

**If the MAVLink path is chosen:** T1-1 through T1-5 are not needed immediately.
T2-1/T2-2 are still useful for game-day recon. The E8 reader (T1-6) is useful
regardless of path.

**If both paths are needed:** the above ELRS integration plus the MAVLink P1
fixes (§3 items 1 and 3) from the orchestrator's review.

---

*— Claude, 2026-10-15. Assessment only — no files were merged or copied.
Awaiting owner's path commitment (§2) before acting on any integration.*

---

## 9. Consolidated integration plan (andeds, orchestrator, 2026-10-15)

This section merges **all three** integration assessments into one decision-ready
plan: my §6/§7 review + Phase B/C, Claude's §8 (Tier 1/2/3 inventory + the FLRC
finding), and Codex's §23 in `HOME-LAB-HANDOFF.md` (12 prioritized candidates).
Where the three disagree, I say which way I rule and why. **No code has been
merged** — this is the plan to execute once the owner commits to a path (§2).

### 9.1. Where the three of us agree (the core, non-negotiable)

All three assessments independently land on the same structural conclusion:

> **Stop re-implementing the ELRS protocol layer in this repo. Import the
> validated `elrs_sdr/core` (CRC/FHSS/OTA/LoRa/FLRC) as the single source of
> truth, and make the CTF tools thin adapters over it.**

- **Me (§6/§7):** "import `elrs_sdr/core` as a library, don't fork it — forking
  is how the single-version bugs (P1-2) happened."
- **Claude (§8.7):** "submodule or copy `elrs_sdr/core/` … matches the
  orchestrator's 'import, don't duplicate' directive in §6."
- **Codex (§23.2, priority 1):** "one reusable ELRS packet/CRC/hopping model …
  addresses duplicated protocol assumptions and the wrong type/UID layout."

The concrete fix this buys: **P1-2** (hardcoded `OTA_VERSION_ID=4` →
version-parameterized v2–v6), the three divergent `Crc2Byte`/FHSS/UID copies
(forger / tx-emulator / elrs_chain), and the wrong SYNC/type field positions the
differential review flagged. **Agreement is total on this. No decision needed.**

### 9.2. The one fact that changes everything (Claude, confirmed by me)

> **The TinyHawk III at its default air rate (`rate_index=2`) transmits
> FLRC/GFSK, not LoRa.** The poc-research tools assume LoRa. If the CTF target
> runs a FLRC rate (the common default), `elrs-tx-emulator.py` (a fixed-SF6 LoRa
> chirp, P2-5) and `elrs-analyzer.py` (LoRa-only decode) produce / expect a
> signal the real link does not use.

I accept this as the dominant risk on the ELRS path. It means the **PHY layer
(`gfsk.py` + `identify.py`), not just the protocol layer (CRC/FHSS/OTA), is in
scope for the ELRS path** — Phase 2 of my plan is not optional, it's the actual
blocker. This is the single most valuable thing the elrs-sdr project produced
for the CTF.

### 9.3. Where we disagreed, and my ruling

| Disagreement | Claude said | Codex said | **My ruling** |
|---|---|---|---|
| **How to bring in `elrs_sdr/core`** | Git **submodule** preferred (§8.7.1) | **Copy** the selected files, keep provenance (§23.1) | **Vendored copy with a pinned `PROVENANCE.md`.** Rationale: (a) no egress on the enclave or likely the CTF box — a submodule can't be fetched at build time; (b) the CTF machine may be air-gapped; (c) a submodule adds a fetch step that can silently break. A copy + provenance pin is self-contained and auditable. I update my §6 "import, don't fork" to mean "import the *module*, vendored with a pinned commit" — the anti-fork point (no drift, single source) still holds. |
| **Scope of the first integration** | Rewrite all three tools (analyzer + tx-emulator + forger) at once (§8.7) | Staged: protocol layer first (priority 1), PHY later (priority 8) | **Staged, Codex is right.** Phase 1 = protocol layer only (CRC/FHSS/OTA) across forger + analyzer + elrs_chain + the P2-7 rainbow loader. Phase 2 = PHY (FLRC/LoRa). Doing Claude's full three-tool rewrite before the protocol layer is proven round-trips is too much change at once. |
| **E8 reader** | Copy `tools/e8_read.py` (confirmed, 85 lines) | Bring over "redacted evidence + parser semantics first", keep the CTF reader's dry-run/endpoint checks | **Merge, don't replace.** `e8_read.py` has the confirmed 64-byte/17-byte protocol; the CTF `e8-read-config.py` has the safety plumbing (dry-run default, endpoint validation, private-capture handling). Take the confirmed protocol from `e8_read.py`, keep the CTF tool's safety shell. Don't throw either away. |
| **UHD radio wrapper** | Copy `elrs_sdr/radio.py` (UHD-native, 1047 lines) | "Adapt behind the CTF SDR interface, exclude transmit wiring from observation paths" | **Codex's caution wins for Phase 1.** Bring in the *receive* portions behind the existing `sdr_common.py` interface; do not introduce a competing global radio owner, and keep TX wiring out of any observation path. The full UHD wrapper is a Phase 2 / ELRS-path decision. |
| **What to pull for recon** | Tier 2: dronescan, protocol_catalog, OpenDroneID, fusion, WaveRaider, bursts | Priorities 5,6,7,9,10,11 (bursts, fhss_observe, protocol_catalog, Remote-ID, fusion, WaveRaider plugins) | **Defer all of it.** Recon tooling is valuable but is *not* on the critical path to a working exploit. Pull it only after the chosen attack path is end-to-end valid. Both are right that it's useful; I'm ruling it's later, not now. |
| **PHY (gfsk/lora) confidence** | "100% byte-exact round-trip at ~20 dB" (Claude, §8.2 T1-2) | "Experimental/partial until checked against independent real recordings; synthetic round trips do not close the long-interleaving payload barrier" (Codex, §23.2 p8) | **Codex's caution stands, with a refinement.** The *protocol* round-trip (CRC/OTA) is proven — that's Phase 1 and it's safe. The *PHY* demod (gfsk/lora, especially the long-interleaved payload) is **self-consistent, not yet validated on an independent real capture** — that's Phase 2 and it needs the bench + the T1-8 IQ capture as ground truth. I do not accept "100% byte-exact" as a game-day guarantee until it's run on the `captures/fhss_20261010_113103` capture (real TinyHawk FLRC). Label the PHY work SELF-CONSISTENT until then, per the honesty contract. |

### 9.4. The consolidated execution plan (supersedes §5 Phase B/C detail)

**Gate (unchanged):** owner answers §4 (Q1–Q9) and names the path. If the path
is **not** ELRS, skip to 9.5. The plan below is the ELRS path; the MAVLink path
only needs the P1-1/P1-3/P2-6 fixes (§3) and none of the elrs-sdr import.

**Phase 1 — Protocol layer (Codex implements, Claude QCs, ~1 week, no hardware)**
1. Vendor `elrs_sdr/core/{crc,ota,fhss}.py` → `tools/elrs_core/` with a
   `PROVENANCE.md` pinning the elrs-sdr commit (currently `7936602`). No numpy
   needed for these three (pure Python).
2. Rewrite `tools/elrs-packet-forger.py`: delete local `Crc2Byte`/`ElrsRng`/
   `generate_fhss_sequence`/`uid_to_*`; import from `elrs_core`. **Fixes P1-2**
   (version now a parameter, not a constant).
3. Rewrite `tools/elrs-analyzer.py`: delete `OTA_VERSION_ID=4` constant + local
   decode; call `elrs_core.ota.decode_full` / `decode_std` with the version
   **from the packet**. This is the "measure, don't assume" fix.
4. Rewrite `tools/exploit_chains/elrs_chain.py`: same import swap for
   `phrase_to_uid`/`uid_to_fhss_seed`/`uid_to_crc_init`/`compute_fhss_sequence`
   and the local `crc14`.
5. **Fix P2-7:** the rainbow-table loader in
   `tools/passive-telemetry-interceptor.py:1102` reads `uid_3_5` but the JSON
   has `uid_hex`/`uid_bytes`/`sync_bytes` → it silently indexes 0 of 723 entries.
   Normalize to the actual schema; make a non-empty file → empty index a visible
   error.
6. **Add round-trip tests:** encode a packet with the new `elrs_core`-backed
   forger, decode it back, assert byte equality. Cross-check against the pinned
   `firmware/src/expresslrs-4.1.0` source struct (Codex's "independent fixtures"
   requirement). These are the "no direct tests of these raw builders" gap the
   review flagged.
7. **Close P2-8:** add the 90-test root suite to
   `.github/workflows/validate.yml`.
8. Commit code + notes together. Selftest green. **No hardware, no TX.**

**Phase 2 — PHY layer (Codex implements, Claude QCs, ~2–3 weeks, needs bench)**
9. Vendor `elrs_sdr/core/{gfsk,lora,lora_payload,lora_sync,lora_ref}.py`
   (numpy-dependent) + `identify.py` into `tools/elrs_core/`.
10. Rewrite `tools/elrs-tx-emulator.py` modulation: replace the fixed-SF6
    chirp (`_modulate_lora`) with `elrs_core.gfsk.modulate` (FLRC) /
    `elrs_core.lora.modulate_frame` (LoRa), selected by the target's
    `rate_index`. **Fixes P2-5.**
11. Rewrite `tools/passive-telemetry-interceptor.py` SDR/IQ path: replace
    `envelope_demod()` (magnitude-threshold, P2-4) with
    `elrs_core.identify` (auto-detects LoRa vs FLRC, version-agnostic). This is
    the recon layer the analyzer was supposed to be.
12. **Validate on real ground truth:** run the new PHY against the T1-8 capture
    (`captures/fhss_20261010_113103`, real TinyHawk FLRC, 480 MB — copy locally,
    do **not** commit). Until it decodes that capture, label the PHY
    SELF-CONSISTENT, not VALIDATED (Codex's caution).
13. **Bring in the receive-only UHD radio** (`elrs_sdr/radio.py` RX portions)
    behind `sdr_common.py`, TX wiring excluded from observation paths (Codex).
14. Bench-test against the owned TinyHawk + E8 (read-only first, no-flash):
    forge → (FLRC-modulated) transmit → receiver accepts → command executes.

**Phase 3 — Game-day (unchanged from §5 Phase D)**
15. Re-ground `docs/game_day_playbook.md` in Phase 2 output. Home-lab dry-run
    under the time limit. Document what does not work.

**Defer (recon, both paths, only after the exploit path is valid):**
- `dronescan.py`, `protocol_catalog.py`, OpenDroneID decoder, `fusion.py`,
  WaveRaider dashboard, `bursts.py`, Remote-ID fixtures. Useful, not critical.

### 9.5. If the MAVLink path is chosen instead

None of the elrs-sdr import is needed for the MAVLink attack itself. Do only:
- **P1-1** fix the signing mutation (`mavlink-signing-replay.py:202/230`) — it
  must re-sign with a key, or stop claiming a bypass.
- **P1-3** fix the COMMAND_LONG wire order (`mavlink_chain.py:391`,
  `mavlink-signing-replay.py:591`) — use the pymavlink generated serializer as
  the reference, not hand-rolled `struct.pack`.
- **P2-6** stop reporting "REPLAY VIABLE" from clock age alone.
- **P2-8** close the CI gap.
- The E8 reader merge (9.3) is still useful regardless of path.
- The elrs-sdr recon tools (9.4 defer list) remain available for game-day
  situational awareness.

### 9.6. Standing constraints on any merge (all three of us, unanimous)

- **No TX wiring in any observation/recon path.** The TX gate stays a wall for
  the current phase; it opens only with explicit human sign-off + bench-only.
- **No E8 flash.** Absolute. The E8 reader merge is read-only.
- **Provenance on every vendored file.** Pin the elrs-sdr commit; record the
  source path. No "exact port" label is a substitute for an independent fixture
  check (Codex).
- **Honesty contract on PHY claims.** Self-consistent round-trip ≠ validated on
  real RF. Label accordingly until the T1-8 capture test passes.
- **Code + notes in the same commit.** Every merge commit includes the notes
  entry in the same commit (the standing 3-agent rule).
- **No secrets committed.** The bind UID is a pairing secret, not a constant in
  code/fixtures/logs.

### 9.7. The single question that still blocks Phase 1

**Does the owner commit to the ELRS path, and is the CTF target confirmed to be
ELRS (and which version / rate)?** If yes → Phase 1 starts immediately (no
hardware needed, ~1 week). If the target is MAVLink/GCS → 9.5. If unknown → the
owner gathers §4 (Q1–Q9) from the organizers first. **Do not start Phase 1
tool rewrites before the path is named** — that's the rule I set in §2 and both
agents have held to it so far. Good.

— andeds, orchestrator, 2026-10-15. This consolidates §6–§8 and Codex §23 into
one plan. It is the current way ahead for the ELRS integration.

---

## 10. Path-independent integration (Claude, 2026-10-15)

Enacted the three items from §9 that are **not gated** on path commitment.
No tool rewrites — these are preparation and bug fixes.

### 10.1. Vendored `elrs_sdr/core` protocol layer → `tools/elrs_core/`

Per §9.4 Phase 1 step 1 and §9.3 (vendored copy with pinned provenance).
Copied 3 files from `elrs-sdr-transfer` commit `7936602`:

| File | Lines | What |
|---|---|---|
| `crc.py` | 76 | Crc2Byte (CRC-14/16) + Crc8 (CRC-8/DVB-S2) |
| `ota.py` | 500 | OTA packet model: encode/decode sync/rc/linkstats/data, CRC validate/generate |
| `fhss.py` | 217 | FHSS LCG + sequence builder + ISM2G4 domain table |

Plus `__init__.py` and `PROVENANCE.md` (SHA-256 hashes, source paths, commit pin).

**Verified:** imports clean, build_sync → decode_std round-trip passes, CRC
validates, FHSS sequence is 240 entries with sync channel at block start.
Evidence class: VALIDATED (these are the same files the companion repo's 29
self-tests cover).

**Not done (gated on path commitment):** rewriting the CTF tools to import
from `elrs_core` instead of their local reimplementations. That's §9.4 Phase 1
steps 2-6.

### 10.2. E8 reader merge

Per §9.3: "Take the confirmed protocol from `e8_read.py`, keep the CTF tool's
safety shell." Rewrote `tools/e8-read-config.py` to combine:

- **From `e8_read.py` (confirmed):** the 64-byte Read command (`02 01` + 62
  zeros), 17-byte response parsing (rate/cfg1/cfg2/UID/padding), rate lookup
  table, human-readable output
- **From the original CTF tool (safety):** `--execute` dry-run default,
  VID:PID 0x8888:0x2001 endpoint validation, private capture to `work/e8-session/`,
  JSON summary output, UID redacted from stdout

Also copied the E8 protocol reconstruction doc (`references/e8_protocol_reconstruction.md`)
to `research/e8-protocol-reconstruction.md` for reference.

All 3 existing tests in `tools/tests/test_e8_read_config.py` pass.

### 10.3. P2-7 fix — rainbow table loader schema mismatch

`tools/passive-telemetry-interceptor.py:1102` `load_rainbow_table()` was keying
on `entry.get("uid_3_5", "")` — that key doesn't exist in the JSON schema.
The actual keys are `sync_bytes`, `uid_hex`, `uid_bytes`, etc. (from
`elrs-uid-rainbow.py`'s `generate_table()`).

**Before:** `load_rainbow_table("elrs-rainbow-table.json")` → 0 entries loaded
(silently). Every SYNC packet lookup returned no match.

**After:** keyed on `sync_bytes` (normalized to uppercase). 723 entries load.
Added an explicit error log when a non-empty file produces an empty index
(catches future schema mismatches).

### 10.4. What remains gated on path commitment

Per §9.7, Phase 1 tool rewrites (steps 2-6) are blocked until the owner
answers §4 (Q1-Q9) and names ELRS or MAVLink. The vendored `elrs_core` is
ready for immediate use once the path is named. The E8 reader merge and the
P2-7 fix are done.

*— Claude, 2026-10-15.*

---

## 11. Test suites removed (Claude, 2026-10-15, owner directive)

**Owner directive:** remove all Python test scripts. Validation will be done
against live targets and real data, not synthetic unit tests.

**Removed:**
- `tests/` directory (3 test files + conftest + __init__): `test_elrs_crypto.py`,
  `test_rainbow_table.py`, `test_serial_probe.py`
- `tools/tests/` directory (4 test files): `test_cve_audit.py`,
  `test_e8_read_config.py`, `test_serial_probe.py`, `test_validation.py`
- CI unittest step from `.github/workflows/validate.yml` (the
  `validate-project.py` structural check remains)

**Not removed:** test files inside `firmware/src/` (upstream source tarballs,
not ours).

**For Codex and andeds:** do not recreate `tests/` or `tools/tests/`. The owner
wants live-target validation, not synthetic test suites. The P2-8 item
("close the CI gap by adding the 90-test root suite") is now moot — there is
no test suite to add. `validate-project.py` remains as the CI check.

*— Claude, 2026-10-15.*

---

## 12. Review of Phase 1 landing + next steps (andeds, orchestrator, 2026-10-15)

I pulled and reviewed the two commits that landed after §9 (`2971dad` +
`c7682fd`). This is the current state and the exact next steps. The owner's
directive: **test on live gear, not synthetic fixtures.** That changes the
validation strategy but does not change the protocol-correctness work — see §12.3.

### 12.1. What landed correctly (verified in code, evidence class VALIDATED)

| Item | Status | How I verified |
|---|---|---|
| **Vendored `elrs_core/{crc,ota,fhss}.py`** | **Correct.** Byte-identical to source. | SHA-256 of all three vendored files matches the elrs-sdr source at commit `7936602` exactly. `PROVENANCE.md` pins the commit + source paths + the "do not modify, re-vendor on change" rule. |
| **P2-7 rainbow loader fix** | **Correct.** | `passive-telemetry-interceptor.py:1102` now keys on `sync_bytes` (the actual schema) and logs an **error** when a non-empty file yields an empty index. The old silent `uid_3_5` → 0-of-723 bug is gone. |
| **E8 reader merge** | **Correct.** | `e8-read-config.py` has the confirmed 64-byte TX (`02 01` + 62 zeros) / 17-byte RX (`02 c5 <rate> <cfg1> <cfg2> <UID:6> <pad:6>`) protocol, **dry-run default**, VID:PID `8888:2001` validation, 115200 8N1, UID redacted from stdout. Per §9.3. |
| **E8 protocol reconstruction doc** | **Present.** | `research/e8-protocol-reconstruction.md` (368 lines). |
| **Test removal per owner directive** | **Acknowledged.** | `tests/` + `tools/tests/` removed, CI unittest step dropped, `validate-project.py` retained. §11 records the directive. I will not recreate the synthetic suites. |

### 12.2. The gap — Phase 1 is half done (the tools are NOT rewired)

**The vendored `elrs_core` library is in the repo, but none of the three
consuming tools import it yet.** Verified in code:

| Tool | Still local re-implementation? | The bug it carries |
|---|---|---|
| `tools/elrs-packet-forger.py:160` | **Yes** — local `class Crc2Byte` | Divergent CRC copy |
| `tools/elrs-analyzer.py:39` | **Yes** — `OTA_VERSION_ID = 4` constant | **P1-2** (hardcoded version) |
| `tools/exploit_chains/elrs_chain.py:397` | **Yes** — local `def crc14` | Divergent CRC copy |
| `tools/elrs-tx-emulator.py:220` | **Yes** — local `class Crc2Byte` | Divergent CRC copy (Phase 2) |

**The structural fix for P1-2 is not yet in effect.** The correct library sits
unused in `tools/elrs_core/` while the tools run their own single-version code.
This is the "fork, don't import" failure mode §9.1 was written to prevent.

**Note on the commit message:** `2971dad` says "All 123 tests pass" — true at
that commit (before `c7682fd` removed the suites). The round-trip it refers to
was the vendored library's self-consistency, **not** a test that the consuming
tools now emit correct bytes. The tools still produce bytes from their local
(buggy) code.

### 12.3. Next steps (the path to "live-test ready")

Live validation is the right call for the PHY layer (§9.6). But **protocol
correctness is not optional because we'll test live**: if the forger emits a
v4-only CRC init and the target is v3 or v6, the live test fails for a reason
unrelated to the RF. Fix the protocol layer first, *then* the live test
tests the RF, not the version constant.

**Step 1 — Complete the Phase 1 rewiring (Codex, ~1 day, no hardware, no TX):**
1. `tools/elrs-packet-forger.py`: delete local `Crc2Byte`/`ElrsRng`/
   `generate_fhss_sequence`/`uid_to_*`. Import from `tools/elrs_core.{crc,fhss,ota}`.
   Make OTA version a **CLI parameter** (default 4, overridable) — P1-2 fix
   actually taking effect.
2. `tools/elrs-analyzer.py`: delete `OTA_VERSION_ID = 4` + local decode. Call
   `elrs_core.ota.decode_full`/`decode_std` with the version **from the packet**.
3. `tools/exploit_chains/elrs_chain.py`: same import swap for `phrase_to_uid`/
   `uid_to_fhss_seed`/`uid_to_crc_init`/`compute_fhss_sequence`/local `crc14`.
4. **Live validation replaces the deleted unit tests:** the owner runs the
   rewired forger against the owned TinyHawk and confirms the receiver accepts
   the forged frame. That is the real test (§12.4).
5. Commit code + notes together. `validate-project.py` green.

**Step 2 — Live protocol-layer validation (owner + Codex, on the bench):**
6. With the owned TinyHawk bound to the E8, run
   `python tools/elrs-packet-forger.py --phrase <known-phrase> --ota-version <N> --sync`
   and confirm the emitted bytes match a **real capture** of the same link
   (the T1-8 IQ capture, or a fresh one). **Match = protocol layer VALIDATED
   on real RF** — the strongest evidence class, replacing the synthetic test.
7. If it does **not** match, the diff tells us exactly which field (CRC init?
   FHSS seed? packet layout?) is still wrong — more useful than a green
   synthetic test that never touched real RF.

**Step 3 — Phase 2 PHY (only after Step 2 passes, Codex + Claude, ~2–3 weeks):**
8. Vendor `elrs_sdr/core/{gfsk,lora,lora_payload,lora_sync,lora_ref,identify}.py`
   (numpy) into `tools/elrs_core/`.
9. Rewire `elrs-tx-emulator.py` modulation → `elrs_core.gfsk.modulate` (FLRC,
   the TinyHawk default) / `elrs_core.lora.modulate_frame` (LoRa), by
   `rate_index`. **Fixes P2-5.**
10. Rewire `passive-telemetry-interceptor.py` SDR/IQ path →
    `elrs_core.identify` (auto-detect LoRa vs FLRC). **Fixes P2-4.**
11. **Live validation:** capture the owned TinyHawk on the B205/B210, run the
    new PHY against it, confirm it decodes the real FLRC signal. Until it
    passes on a real capture, the PHY stays SELF-CONSISTENT (§9.6).
12. **Live end-to-end:** forge → FLRC-modulate → transmit on the B205 → owned
    TinyHawk accepts → command executes. **The CTF exploit, proven on real gear.**

**Step 4 — Game-day (after Step 3):**
13. Re-ground `docs/game_day_playbook.md` in Step 3 output. Home-lab dry-run
    under the time limit. Document what does not work.

### 12.4. What "live validation" means concretely (the owner's gear)

Bench (per AGENTS.md): TinyHawk III (target), E8 (TX, no-flash), B205mini +
B210 (SDR), ALFA cards (Wi-Fi path), Ubuntu platform.

**Live-test sequence (Step 2 + Step 3.12):**
1. Power the TinyHawk, bind to the E8. Recover the UID from the air
   (`elrs-sdr` `fhss_seed.py` method) or read it from the E8 via the merged
   `e8-read-config.py`.
2. Capture the link on the B205 (2.4 GHz FHSS band). Ground truth for Step 2.6.
3. Run the rewired forger with the recovered UID. Compare to the capture.
   **Match = protocol layer VALIDATED.**
4. (Phase 2) Modulate the forged frame as FLRC, transmit on the B205, confirm
   the TinyHawk accepts (throttle=1000 positive command). **Accept = exploit
   works on real gear.**

**What I need from the owner to start Step 2:** confirm the TinyHawk + E8 are
on the bench and the B205 is wired up. If so, Codex does Step 1 (~1 day) and
we run Step 2 together on the bench.

### 12.4a. Corrections from the full notes read (andeds, after re-reading Codex §11/§12 + the evening notes)

My initial §12.4 was written before I'd fully re-read Codex's HOME-LAB-HANDOFF
§11/§12 and the evening session notes. Three corrections, all in Codex's favor:

1. **Do NOT assume OTA v4 for the TinyHawk.** Codex §11.3: the OTA version is a
   **compile-time feature** (`USE_ELRSV4` present → v4, absent → v3) in
   Betaflight's `expresslrs_common.h`. The pinned config header says "Betaflight
   4.3.1 / 2022 build" but the corpus binaries are labeled 2026.6.2 — Codex
   explicitly says "do not infer the OTA version from the file name, release
   family, or the lookup script's comment." **Correction to Step 2.6:** the
   version must be **measured from the capture** (the `elrs_core.ota` decode
   reports which version's CRC validates) or read from the E8's rate field —
   then the forger is run with *that* version, not a hardcoded 4. The
   `--ota-version` CLI param (Step 1.1) exists precisely so we can set it from
   the measurement, not assume it.

2. **The SYNC layout discrepancy is unresolved — don't use the rainbow lookup
   as an equivalent observation.** Codex §11.4: ELRS 4.1.0's `OTA_Sync_s`
   exposes `UID4`+`UID5` (two bytes), but the rainbow tool's `sync_bytes` /
   `--lookup` assumes three bytes (UID3-5). Codex: "Do not invent the missing
   byte." **Correction:** Step 2.6 (forger output vs. real capture) is the
   valid check. The rainbow-table `--lookup` path is **not** a valid
   equivalent observation until the two-vs-three-byte discrepancy is resolved
   by release. Label any rainbow match as a *candidate* (Codex §11.5: 16 bits
   of partial identity can collide across phrases).

3. **"Match = protocol layer VALIDATED" is milestone 2→3, not milestone 5.**
   Codex §11.6 requires five separate milestones: (1) source-derived behavior,
   (2) observed packets with defined version/format, (3) candidate identity
   match with documented ambiguity, (4) validated receiver state + timing in a
   controlled lab, (5) demonstrated receiver behavior. My Step 2.6
   (forger bytes match a real capture) is milestone **2→3** — it validates the
   protocol layer against observed air, but it is **not** milestone 5
   (demonstrated takeover). Codex §11.5 is explicit: "deterministic frequency
   hopping and a UID-derived CRC do not by themselves demonstrate accepted
   packet injection or control takeover," and "higher power wins" is not a
   reliable result. **Correction:** Step 2 passing does NOT mean the exploit
   works. It means the forger emits the right bytes for the measured version.
   Milestone 5 (demonstrated receiver behavior) is Step 3.12 — the live
   end-to-end TX test. Do not let Step 2's success read as "exploit works."

4. **The E8 live read is not yet proven.** The evening session notes record
   that Codex's vendor reads **received no bytes**, including after the
   documented serial-mode startup. The confirmed 64B/17B protocol is in the
   merged `e8-read-config.py`, but a **live successful read has not happened in
   this project yet**. **Correction to §12.4 step 1:** the air-recovery path
   (`elrs-sdr` `fhss_seed.py` — recover the FHSS seed from observed hops) is
   the **primary** UID source for Step 2. The E8 read is a **secondary**
   confirmation, not a dependency. Do not block Step 2 on the E8 read
   working.

These corrections do not change the step sequence (1→2→3→4). They change what
each step *claims* and which measurement feeds the forger's version param.
The honest milestone labels matter more than the step numbers.

### 12.5. What is explicitly NOT next

- **Do not start Phase 2 (PHY) before Step 2 (live protocol check) passes.**
- **Do not recreate the synthetic test suites.** Owner directive (§11) stands.
- **Do not flash the E8.** Absolute. The E8 reader is read-only.
- **Do not treat the `elrs-tx-emulator.py` SoapySDR chirp as a working
  transmitter.** It is the P2-5 prototype until Step 3.9 rewires it.
- **Do not commit the 480 MB IQ capture to git.** Copy locally for analysis.

— andeds, orchestrator, 2026-10-15. §12 is the current state + next steps.
Step 1 (rewire the tools to `elrs_core`) is the immediate work item for Codex.
Step 2 (live protocol check on the bench) is the gate to Phase 2.

---

## 13. Firmware vulnerability analysis (andeds, 2026-10-15)

The owner asked for the firmware-analysis phase: lay of the land, function
map, memory leaks, high-entropy regions, dangerous calls / buffer control. I
ran it on the corpus binary (`betaflight_CRAZYBEEF4SX1280.bin`) + the pinned
source (Betaflight 2026.6.2 + ELRS 4.1.0, both in `firmware/src/`). Full write-up:
**`firmware/vuln-analysis-CRAZYBEEF4SX1280.md`**. This extends Codex's
`ghidra-analysis-CRAZYBEEF4SX1280.md` (the lay-of-the-land) with the
entropy + dangerous-sink passes. Evidence class: VALIDATED (source-derived,
line-cited). No live exploit, no TX, no hardware.

**Key findings:**

1. **Entropy: NO high-entropy region anywhere.** Overall 6.97 bits/byte, 0
   windows above 7.5, highest 256-byte block 6.63 (code, not data). No
   encryption, no packed/obfuscated blobs, no embedded API keys, no PRNG key
   schedule. Plaintext, unstripped, unobfuscated build. The only "computed"
   data is the CRC14 table, generated deterministically at runtime. **There is
   no "find the hidden key" problem** — the secrets are the UID (config) and
   the binding phrase (if set).

2. **Buffer control: the RC-data overflow path is a DEAD END.** The
   `memcpy(payload, dmaBuffer, ELRS_RX_TX_BUFF_SIZE)` at `expresslrs.c:846`
   copies a **fixed 8 bytes** (macro `= 8`, `expresslrs_common.h:48`). Both
   src and size are fixed; no length field from the air controls the copy. A
   malformed RC packet **cannot** overflow the channel buffer. Don't spend CTF
   time hunting an overflow in the ELRS RX path — it's not there.

3. **The MSP-over-ELRS path IS the viable RF→RCE sink** (this is the real
   finding). `processRFMspPacket()` (`expresslrs.c:697`) accumulates the
   attacker-controlled MSP payload into `mspBuffer[65]` and, once the link is
   `ELRS_CONNECTED` + CRC-valid + model-matched, dispatches to
   `processMspPacket()` — the FC's own MSP command handler. **This is command
   injection, not memory corruption** (the copy into `mspBuffer` is bounded by
   `ELRS_MSP_BUFFER_SIZE=65`, no length-field overflow). The reachable commands
   include `MSP_REBOOT_FIRMWARE`, `MSP_REBOOT_BOOTLOADER_ROM/FLASH`,
   `MSP_REBOOT_MSC` (DFU entry), plus read commands, and (one level up)
   `MSP_SET_RX_CONFIG` (model ID / rebind). **An air-injected MSP packet can
   reboot the FC into DFU or rebind it.**

4. **`USE_RX_EXPRESSLRS_TELEMETRY` + `feature TELEMETRY` are ON in this build**
   (corpus `.config` lines 15 + 108) — the MSP-over-air path is compiled in.
   Confirmed, not assumed.

5. **No memory leaks in the ELRS RX path.** All buffers are static fixed
   arrays (`eprState`, `dmaBuffer[8]`, `mspBuffer[65]`, `telemetryPacket[8]`);
   no `malloc`/`free` in the RX path. (A full heap audit of the
   scheduler/CLI/USB paths is a separate work item, out of RF scope.)

6. **The attack chain is now concrete** (restated in the doc §4):
   RECOVER UID (air `fhss_seed.py` / bind-mode `{0,1,2,3,4,5}` / E8 read) →
   FORGE (elrs_core.ota, **OTA v3** per Codex §4.1) → SYNC → MODEL-MATCH →
   INJECT (MSP command). Steps 2–5 are the `elrs_chain.py` + `elrs-packet-forger.py`
   + Phase-2 FLRC TX; step 1 is the elrs-sdr air-recovery.

**The bind-mode UID `{0,1,2,3,4,5}` is the lowest-hanging fruit** — any
TinyHawk in bind mode uses the universally-known UID, so the whole chain opens
with no recovery step. Primary game-day option.

**IDA note:** IDA 9.4 headless would not initialize on this host
(`Failed to initialize IDA as library, error 1` — proc-module load issue). I
used the pinned source instead, which is more authoritative for buffer control
than a binary scan. The binary entropy scan ran cleanly without IDA.

**This reframes the CTF goal:** the firmware analysis confirms the *what*
(MSP command injection is the real sink, overflow is a dead end) but the
*how-delivered* (FLRC PHY, Step 1/2 protocol + Step 3 RF) is still the elrs-sdr
work in §12. The two are now consistent: the exploit chain targets the MSP
injection surface, delivered over FLRC.

— andeds, orchestrator, 2026-10-15.

---

## 14. Firmware → PoC methodology (andeds, 2026-10-15)

The owner asked for the detailed pathway from "firmware downloaded" to "PoC
developed," with firmware-analysis / vuln-analysis / PoC-dev as the three
major wickets. Written out as a ten-wicket methodology:
**`docs/firmware-to-poc-methodology.md`** (linked from README).

W1 ACQUIRE → W2 MODEL → W3 RECON → W4 SURFACE → W5 VULN → W6 DESIGN →
W7 BUILD → W8 VALIDATE-OFFLINE → W9 VALIDATE-LIVE → W10 GAME-DAY.

The three major wickets the owner named map to W3–W5 (firmware + vuln
analysis) and W8–W9 (PoC development). Each wicket has a **gate** — a specific
piece of evidence that must exist before you advance — which is the honesty
contract made concrete: no vuln claimed without an evidence class, no PoC
claimed without a round-trip, no "it works" claimed until W9 demonstrates it on
real gear.

Every wicket is annotated with what we *actually* did on the TinyHawk (so it's
a proven path, not a textbook list), including the real blockers (IDA headless
won't init on the enclave → used the pinned source instead; the version
disagreement between the config header and the binaries; the LoRa-vs-FLRC PHY
risk). The doc ends with a "where the TinyHawk is" table: **W1–W6 done, W7 in
progress (the tools not yet rewired), W8–W10 not started.** The immediate next
wicket is W7 completion (Step 1), then W8 (offline validation against a real
capture), then W9 (live, on the owner's bench).

This is the durable "guiding light" for the PoC phase — Codex + Claude execute
against it; andeds holds the gates.

— andeds, orchestrator, 2026-10-15.

---

## 15. Way forward after Claude's live-reading + corpus expansion (andeds, 2026-10-15)

Claude read the live TinyHawk over serial (read-only, 2026-10-15) and pulled
in the ArduPilot 4.7.2 + Tello SDK corpora. This is a material update to the
plan. The live reading resolved several open questions and changed the
assumptions in several places.

### What changed (and what it means)

1. **The live firmware is Betaflight 4.4.2, not 2026.6.2.** The corpus
   `CRAZYBEEF4SX1280` binary (2026.6.2) remains a *related sample* — it is
   NOT the image installed on the lab TinyHawk. All W3–W6 analysis was done on
   2026.6.2 source + binary. The 4.4.2 source diff (Claude,
   `research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md`) confirms the same ELRS
   protocol shape (v3, LoRa-only), but the **MSP handler and buffer sizes must
   be re-verified against 4.4.2 source** before the W5 vuln candidates are
   treated as applicable to the live target.

2. **LoRa only, no FLRC on 4.4.2.** The source diff confirms
   `expresslrs_common.h` in 4.4.2 has no `RATE_FLRC_*` / `RATE_FSK_*` — only
   LoRa rates. **This removes the dominant PHY risk** (the LoRa-vs-FLRC
   question that was the Phase 2 blocker). The `elrs_core.lora` path is the
   one to validate; `gfsk` is not needed for this firmware.

3. **`model_id = 255` (0xFF) → `modelXor = 0x00`.** Model-match is
   effectively always satisfied. **One less precondition** — the MODEL-MATCH
   step is a no-op for this configuration.

4. **`feature -TELEMETRY` (disabled at runtime).** The live config has
   telemetry disabled. The compile-time target config has
   `USE_RX_EXPRESSLRS_TELEMETRY` ON, but the *runtime* feature flag is OFF.
   **This is a real risk for the MSP-injection candidate**: if the MSP-over-air
   path is gated on the runtime `TELEMETRY` feature (not just the compile-time
   define), `processRFMspPacket` may not be reachable in this live config.
   **This must be resolved before the MSP-injection candidate is treated as
   viable.** The RC-data injection path is the fallback if MSP-over-air is
   gated off.

5. **ArduPilot 4.7.2 corpus is now in the repo.** CubeOrange + Pixhawk1 ELF
   + SITL x86_64 + source. **The MAVLink path is no longer blocked at W1.**
   The SITL binary runs on x86_64 Linux — the MAVLink PoC can be validated
   against a real ArduPilot process *without* hardware (W8 offline via SITL).

6. **Tello SDK is in the repo.** `djitellopy` + UDP/54399 JSON protocol.
   **The Wi-Fi path is no longer blocked at W1.** The Tello control protocol
   is documented and testable.

### The revised plan (in order)

**Immediate (no hardware):**

1. **Re-verify W5 against 4.4.2 source (Codex).** Diff the ELRS RX driver
   (`expresslrs.c`, `expresslrs_common.c`) between 4.4.2 and 2026.6.2.
   Specifically: (a) is the MSP handler unchanged? (b) are the buffer sizes
   unchanged? (c) is the `USE_MSP_OVER_TELEMETRY` gate the same? **This
   determines whether the MSP-injection candidate is viable on the live
   target.** If the MSP path is gated off at runtime, RC-data injection
   becomes the primary candidate.

2. **Parameterize `elrs_core` for OTA v3 (Codex).** The vendored library
   defaults to v4; the 4.4.2 target is definitively v3. Make the
   forger/analyzer/chain explicitly use v3. This is the P1-2 fix, now with
   the version *confirmed*.

3. **Fix P1-3 MAVLink wire order + validate against SITL (Codex).** Fix the
   `COMMAND_LONG` pack order, then validate against a running ArduPilot SITL
   x86_64 process (`arducopter_SITL_x86_64_4.7.2` in the corpus). W8 offline
   for MAVLink — no hardware, SITL is the oracle.

4. **Step 1: rewire forger/analyzer/elrs_chain → `elrs_core` (Codex).** Now
   with the version confirmed as v3.

**After the above (bench, owner's gear):**

5. **W8 offline for ELRS (owner + Codex).** Capture the live TinyHawk's RF on
   the B205. Cross-check the forger's bytes. Version (v3), PHY (LoRa), and
   model-match (always satisfied) are now known. The only unknown is the UID
   (air-recovery via `fhss_seed.py` or the bind-mode UID).

6. **W9 live for ELRS (owner + Codex).** Forge → LoRa-modulate → TX on the
   B205 → confirm the TinyHawk accepts. The `feature -TELEMETRY` question is
   resolved by the live test: if the MSP path works, MSP-injection is
   validated; if not, RC-data is the fallback.

7. **W7/W8 for MAVLink (Codex, no hardware).** Run SITL, inject the forged
   MAVLink commands, confirm they execute. Fully offline.

8. **W7 for Wi-Fi/Tello (Codex).** Review the Tello SDK, build the
   deauth/PMKID/rogue-AP toolchain. The JSON protocol is documented.

### The revised critical path

- **ELRS (TinyHawk):** W7 (rewire to `elrs_core`, v3, LoRa) → W8 (capture
  cross-check) → W9 (live bench). The MSP-injection vs. RC-data question is
  resolved by step 1 (the 4.4.2 source diff) and confirmed by step 6.
- **MAVLink (ArduPilot):** W7 (fix P1-3, validate against SITL) → W8 (SITL
  is the oracle, no hardware). **Now the fastest path to a validated PoC.**
- **Wi-Fi (Tello):** W7 (review the SDK, build the Wi-Fi toolchain) → W9
  (live, needs a Tello or equivalent).

**The single most valuable next action is step 1 (the 4.4.2 source diff for
the MSP path), because it determines whether the primary ELRS candidate is
viable on the live target or whether we fall back to RC-data.** Everything
else flows from that answer.

### What I need from the owner

The W0 questions (§4) are still the ultimate gate, but the live reading has
reduced them. The most important remaining W0 facts:

- **ELRS:** Is the live TinyHawk's `TELEMETRY` feature the gate for the
  MSP-over-air path? The 4.4.2 source diff (step 1) answers this without
  hardware.
- **MAVLink:** Is the CTF target an ArduPilot-based autopilot? If yes, the
  SITL validation path is ready.
- **Wi-Fi:** Is the CTF target a Tello (or equivalent)? If yes, the SDK is in
  the corpus.

— andeds, orchestrator, 2026-10-15. §15 is the way forward after Claude's
live reading + corpus expansion. The version (4.4.2) and PHY (LoRa) are now
confirmed; the MSP-path viability is the single most important next question
(step 1: the 4.4.2 source diff).

---

## 17. Review of the 4.4.2 rewire + driver diff + Layer 2 research (andeds, 2026-10-15)

I pulled and reviewed the four commits after §15 (`b8d79a6`, `f0e86e9`,
`f1d467d`, `d79d0e8`). This is a major step — Step 1 (the rewire) is **done
and verified working**, the MSP-path question is **resolved in our favor**, and
the target landscape is **broadened from "drone" to "controller + drone."**

### What landed correctly (verified in code + by running it)

1. **Step 1 (the rewire) is DONE and WORKS.** All three tools
   (`elrs-packet-forger.py`, `elrs-analyzer.py`, `elrs_chain.py`) now import
   from `elrs_core`; the local `Crc2Byte` / `ElrsRng` / `crc14` re-implementations
   are **deleted** (not just bypassed). I ran the forger: it produces valid
   8-byte v3 packets with the correct UID-derived CRC init and sync channel
   41. **P1-2 (hardcoded OTA v4) is structurally fixed** — the version is now
   a parameter, defaulted to v3 for the live target.

2. **The v3 CRC init correction is right.** The forger's `uid_to_crc_init` now
   does `init ^ (ota_version if ota_version == 3 else ota_version << 8)` —
   v3 XORs into the **low** byte, v4 into the high byte. This matches the
   4.4.2 source (`expressLrsSpiInit`). The old code XORed into the high byte
   for all versions, which was wrong for v3.

3. **The sync channel is fixed to v3 (channel 41).** `ISM2G4_SYNC_CHANNEL =
   ISM2G4_FREQ_COUNT // 2 + 1`. The 4.4.2 driver diff confirms
   `(freqCount / 2) + 1` = channel 41 for v3 (v4 uses channel 40). The old
   code used `freqCount // 2` (channel 40), which is the v4 formula — wrong
   for the live target.

4. **The MSP-path question is RESOLVED (in our favor).** The 4.4.2 driver
   diff (`research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md`, §"4.4.2
   MSP-over-ELRS reachability review") is precise: **runtime `feature
   -TELEMETRY` does NOT gate MSP receipt/dispatch in 4.4.2.** The
   `processRFPacket()` dispatches every CRC-valid `ELRS_MSP_DATA_PACKET` to
   `processRFMspPacket()` without checking `FEATURE_TELEMETRY`. The 65-byte
   buffer + 5-byte chunking is unchanged. **MSP-over-ELRS remains the primary
   4.4.2 candidate.** Evidence class: SOURCE-DERIVED / SELF-CONSISTENT,
   pending live validation (W9). RC-data injection remains the fallback.

5. **The full 4.4.2 attack parameters are now confirmed from source** (the
   table at the end of the live-config doc): OTA v3, LoRa only, rate_index 2 =
   LoRa 150Hz SF7 BW800, CRC init `((UID[4]<<8)|UID[5])^3`, no nonce XOR (v3),
   FHSS seed `(UID[2]<<24+UID[3]<<16+UID[4]<<8+UID[5])^3`, sync channel 41,
   UID[4:5] leak in every SYNC, MSP buffer 65B, MSP reachable, model-match
   always satisfied, switch mode HYBRID. **This is the W4/W5 output for the
   live target, now version-confirmed.**

6. **PX4 v1.18.0-rc1 artifacts added** (CubeOrange + FMU-v5 `.px4` firmware).
   Cross-stack MAVLink comparison is now possible (ArduPilot 4.7.2 vs PX4
   1.18.0). The MAVLink path has two reference stacks.

7. **Layer 2 research doc** (`research/layer2-wireless-attack-research.md`,
   462 lines). This is the **broadening** the owner asked for: the target
   landscape now includes the **controllers and ground stations**, not just
   the drones. Three attack chains are defined:
   - **Chain 1: ELRS controller WiFi exploitation** — jam the link → ELRS TX
     auto-starts an open WiFi AP (~60s) → connect → `GET /config` → extract
     the binding phrase + UID → forge. This is a **new** attack surface I
     hadn't tracked: the ELRS *transmitter* (ESP32) has a WiFi config
     interface that leaks the binding phrase.
   - **Chain 2: WiFi network → MAVLink command injection** — join the GCS
     WiFi (PMKID/open/evil-twin) → find MAVLink endpoints (UDP 14550/14551) →
     inject COMMAND_LONG (land/RTL/disarm). Uses the ArduPilot SITL binary
     as the W8 oracle.
   - **Chain 3: DJI BLE exploitation** — CVE-2026-78306 (unauthenticated BLE
     commands) + CVE-2026-77812 (passive WiFi credential sniff). Requires DJI
     hardware (not in lab).

### My assessment

**This is the strongest state the project has been in.** The three things that
were blocking are now resolved:
- **P1-2 (hardcoded v4)** → fixed, verified working.
- **The sync channel (v4 vs v3)** → fixed to v3 (channel 41).
- **The MSP-path viability** → resolved in our favor (MSP is reachable even
  with telemetry disabled).

The ELRS path is now at **W7 (build) done, ready for W8 (offline validation
against a real capture)**. The MAVLink path is at **W7 (build) — needs the
P1-3 fix + SITL validation**. The Layer 2 research **broadened the target
landscape** from "the drone" to "the drone + the controller + the GCS," which
is the "deliberately broad" posture the owner asked for.

**The one thing that's now the highest-value next action: W8 offline
validation for ELRS.** The forger is built and correct (v3, LoRa, channel 41,
MSP reachable). The next gate is to **cross-check the forger's bytes against
a real capture of the live TinyHawk's RF** (the owner's bench, the B205 SDR).
This is W8 — it proves the bytes are right against the real target, not just
self-consistent. The only unknown at W8 is the UID (air-recovery via
`fhss_seed.py` or the bind-mode UID). Everything else (version, PHY, rate,
CRC init, sync channel, model-match) is now confirmed.

### The revised next steps (in order)

1. **W8 offline for ELRS (owner + Codex, bench).** Capture the live
   TinyHawk's RF on the B205. Cross-check the forger's bytes. Recover the UID
   (air or bind-mode). **This is the immediate next gate.** The forger is
   ready; the capture is the missing piece.

2. **W9 live for ELRS (owner + Codex, bench).** Forge → LoRa-modulate → TX on
   the B205 → confirm the TinyHawk accepts. The MSP-injection candidate is
   validated (or RC-data is the fallback).

3. **W7/W8 for MAVLink (Codex, no hardware).** Fix P1-3 (COMMAND_LONG wire
   order) + validate against the ArduPilot SITL x86_64 binary. Fully offline.
   This is the **fastest path to a second validated PoC** (no bench needed).

4. **Layer 2 Chain 1 (ELRS controller WiFi) — the new surface.** The ELRS
   TX auto-starts an open WiFi AP after link loss. The `GET /config` leaks
   the binding phrase. **This is a lab test the owner can do read-only on the
   E8** (the no-flash constraint allows a read-only WiFi config read). If the
   E8's WiFi AP is reachable and the config is readable, the entire UID
   recovery step (W8's only unknown) is eliminated — the binding phrase is
   available directly.

5. **Layer 2 Chain 2 (WiFi → MAVLink).** Build the WiFi join + MAVLink
   injection toolchain. Validate against SITL (W8 offline).

### What I need from the owner

The W0 questions are now **much** more answerable, because the Layer 2
research identified the specific targets:

- **Is the CTF target an ELRS drone + controller (Chain 1)?** If yes, the
  E8's WiFi AP read is the key lab test (read-only, allowed by the
  no-flash constraint). It would eliminate the UID recovery step.
- **Is the CTF target a MAVLink GCS over WiFi (Chain 2)?** If yes, the
  ArduPilot SITL + the WiFi join toolchain is the path.
- **Is the CTF target a DJI drone (Chain 3)?** If yes, DJI hardware is needed
  (not in lab).

**The single most valuable next action is W8 (the ELRS capture cross-check)
— the forger is ready, the capture is the missing piece. And the Layer 2
Chain 1 (E8 WiFi AP read) is the lab test that would eliminate the UID
recovery step entirely.**

— andeds, orchestrator, 2026-10-15. §17 is the review of the 4.4.2 rewire +
driver diff + Layer 2 research. Step 1 is done and verified working; the
MSP-path is resolved in our favor; the target landscape is broadened to
controller + GCS. W8 (the ELRS capture cross-check) is the immediate next
gate.

---

## 16. Betaflight 4.4.2 MSP reachability result (Codex, 2026-10-15)

Completed §15 step 1 by diffing the pinned Betaflight 4.4.2 and 2026.6.2
sources, focusing on `expresslrs.c`, `expresslrs_common.c`, and
`expresslrs_telemetry.{c,h}`, then tracing the compile-time feature closure and
the CRSF/MSP dispatcher.

**Result:** the live runtime setting `feature -TELEMETRY` does **not** gate the
MSP uplink receive/dispatch path in 4.4.2. `initTelemetry()` returns early, but
`expressLrsSpiInit()` still installs the 65-byte MSP receive buffer;
`processRFPacket()` still routes CRC-valid `ELRS_MSP_DATA_PACKET` frames to
`processRFMspPacket()`; and a complete request still reaches
`handleCrsfMspFrameBuffer()` → `mspFcProcessCommand()` when connected and
model-matched. The live `model_id=255` makes model match a no-op.

The buffer remains 65 bytes with five-byte chunks in both versions, and the
copy is capped by remaining capacity. The earlier classification therefore
holds for 4.4.2: **command injection candidate, not memory-corruption
candidate**.

Runtime telemetry-off does skip `initSharedMsp()`, leaving its descriptor at
the static initial value `-1`. The parser still calls the command dispatcher,
and telemetry-origin comparisons use that same value, so source flow remains
reachable. Live validation is still required to promote this from
**SOURCE-DERIVED / SELF-CONSISTENT** to VALIDATED and to confirm acknowledgement
behavior in the installed binary.

Detailed evidence and the revised remaining actions are in
`research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md`.

**Decision:** keep MSP-over-ELRS as the primary TinyHawk 4.4.2 candidate;
RC-data injection remains the fallback.

Completed §15 steps 2/4 in the same change:

- `elrs-packet-forger.py` now defaults to OTA v3 and consumes the vendored CRC
  and FHSS implementations instead of its local copies.
- `elrs_chain.py` now defaults to OTA v3, uses the vendored FHSS builder and
  OTA RC builder, and no longer uses its non-ELRS LCG or incorrect CRC-14
  polynomial.
- `elrs-analyzer.py` now parses 8/13-byte frames with `elrs_core.ota` and
  reports the actual two UID bytes in `OTA_Sync_s`; it no longer invents a
  third byte or derives a full FHSS seed from incomplete identity.

The 4.4.2 source also corrected an earlier notes assumption: v3 computes the
CRC initializer as `((UID[4] << 8) | UID[5]) XOR 3`; the `version << 8` form is
the v4 branch. The consumers implement that version split explicitly without
modifying the pinned vendored core.

Validation: all three scripts compile; forger `info`, analyzer simulation, and
chain dry-run complete; `python3 -B tools/validate-project.py` passes all 102
structural/provenance/link checks. No hardware or TX was used. Live RF capture
comparison remains the W8 gate.

---

## 18. Layer 2 wireless attack program — earlier direction (superseded by §20)

The owner committed to a Layer 2 / chipset-level wireless attack research
program. The full analysis is in
[`research/layer2-wireless-attack-research.md`](research/layer2-wireless-attack-research.md).
Key decisions recorded here for sequencing:

### Owner decisions

1. **Timeline:** ~1 month (target mid-November 2026).
2. **Prove on ELRS first:** Build and validate the full Chain 1 (ELRS WiFi
   mode exploitation → binding phrase extraction → ELRS packet forgery →
   drone control) end-to-end on the TinyHawk III + E8 before acquiring new
   hardware.
3. **DJI sequenced after:** DJI hardware available once Chain 1 is proven.
   Chain 3 (DJI BLE exploitation, CVE-2026-78306) starts then.
4. **Chain 2 (MAVLink/WiFi) in parallel:** No hardware acquisition needed —
   SITL + ALFA cards + laptop are sufficient. Can run alongside Chain 1.

### What this changes about the existing plan

The §2/§5 path commitment question is partially answered: **the owner is
proceeding with ELRS (Chain 1) as the primary path regardless of the Q1–Q9
answers.** The CTF facts still inform priority of Chains 2 and 3, but Chain 1
work is unblocked.

This means Phase B (protocol conformance fixes on the ELRS path — P1-2 OTA
v3 parameterization, which §16/§17 report as already done for the vendored
tools) and Phase C (real LoRa PHY + bench RF validation) are now the active
work. The Layer 2 program adds a new stage *before* the existing ELRS chain:
wireless entry via controller WiFi mode exploitation.

### Execution sequence (Chain 1, ~1 month)

| Week | Milestone | Depends on |
|---|---|---|
| 1 | ELRS WiFi mode recon: confirm E8 enters WiFi mode, document AP name/behavior, build WiFi AP scanner tool | Owner confirms WiFi mode trigger is within no-write constraint |
| 1–2 | ELRS WiFi config extractor: connect to ELRS WiFi AP, extract binding phrase/UID/config via HTTP | WiFi mode recon |
| 2 | RF disruption feasibility: characterize ELRS link disruption methods to force WiFi mode timeout | E8+TinyHawk bench setup |
| 2–3 | End-to-end Chain 1 dry run: force WiFi → extract UID → derive FHSS/CRC → forge packets (simulation, no TX) | Extractor + existing ELRS tools (P1-2 fixed) |
| 3–4 | Chain 1 live validation: RF capture to confirm forged packets match real ELRS traffic | SDR setup (B205/B210) |
| 4 | Game-day playbook: document the Chain 1 procedure as a timed operator checklist | All above |

Chain 2 (MAVLink/WiFi) milestones run in parallel:
- Evil twin / rogue AP automation tool (hostapd + dnsmasq + deauth)
- MAVLink MitM proxy (transparent, selective packet modification)
- MAVLink injection over WiFi (combine network access with existing injector)
- Validated against ArduPilot SITL

---

## 19. ELRS v3 packet correction and RF-TX safety gate (Codex, 2026-10-15)

This supersedes the §17 statement that the reworked forger was already
protocol-correct for the live 4.4.2 target. That check covered a zero-nonce
packet only. Source review found that the vendored `elrs_core/ota.py` implements
v4 nonce mixing and its `ota_version=3` CRC initializer helpers are not v3
compatible. Per `tools/elrs_core/PROVENANCE.md`, the vendored files remain
unchanged; a general core fix must be made in the companion repo and re-vendored.

The project-facing tools now apply the version-specific rules: non-SYNC v3
packets use the static UID-derived CRC initializer, while v4 mixes the nonce;
v3 and v4 use sync channels 41 and 40 respectively. The ELRS chain also uses
the domain frequency table (2400.4 MHz for channel 0), rather than the old
2400.0 MHz offset. This makes v3 sync-channel index 41 approximately
2441.4 MHz; the earlier 2440.4 MHz statement in the live-config notes was
corrected. A prior 2440.4 MHz wideband capture is not by itself evidence
that the link's sync channel was index 40.

The ELRS chain's former `--arm` path sent placeholder IQ samples, not LoRa.
It now fails closed before opening a TX stream; the dry-run packet preview remains
available. A real LoRa PHY and bench validation remain Phase C work. Offline
source-conformance checks do not establish live acceptance by the TinyHawk.

---

## 20. First benchmark: the owned E8–TinyHawk ELRS pair (owner direction, 2026-10-15)

The owner moved the active path away from WiFi. **Benchmark the ELRS control
link between the existing EMAX E8 and TinyHawk III first.** Do not make WiFi
entry, RF link disruption, MAVLink/WiFi, or DJI/BLE prerequisites for this
benchmark. The earlier §18 plan is paused, not silently deleted.

### Evidence gates, in order

| Gate | Deliverable | Pass condition |
|---|---|---|
| 0. Ground truth | Reconcile the private, read-only E8 config response with the observed TinyHawk Betaflight 4.4.2 settings; record provenance without publishing the pairing UID. | Each claim is tagged device-observed, source-derived, or still unknown. E8 installed firmware/MCU are not guessed from related EMAX products. |
| 1. Passive RF | Obtain or locate IQ/packet captures of **this pair** during normal operation; preserve raw files under ignored `work/`. Re-evaluate the older T1-8 capture's FLRC label because the version-matched 4.4.2 source maps the live rate index to LoRa. | The PHY, rate, and hopping observations are measured, not inferred from a filename or newer firmware. |
| 2. OTA conformance | Decode multiple real SYNC and data frames, including nonzero packet counters, and compare v3 frequency mapping, FHSS sequence, packet fields, and CRC against the 4.4.2 source and private E8 UID. | A reproducible offline report explains matching and nonmatching frames. Synthetic round-trips alone do not pass. |
| 3. PHY integration | Coordinate the v3 correction in the companion `elrs-sdr` core, re-vendor with an updated provenance pin, and validate a real LoRa modulation path offline. | A separate review confirms the PHY and version-specific packet bytes; `elrs_chain.py --arm` remains blocked until then. |
| 4. Owned bench | Only after a separately approved bench protocol, check whether the disarmed, propeller-free TinyHawk accepts the generated signal. | Device-observed acceptance or rejection is recorded; no success is claimed from SDR output alone. |

**Assignments:** Codex owns integration, source/capture comparison, and the
evidence ledger. Claude provides independent review of packet/PHY claims and
can coordinate the companion-core change; do not let two processes access the
E8 serial port concurrently. The owner controls physical setup, any capture
handoff, and authorization for later active RF testing. The first two gates
are receive-only or offline; no E8 flash, settings write, reset/bootloader
sequence, live fuzzing, or RF transmission is authorized by this plan.

**Current handoff:** This Windows checkout has no passive IQ capture under
`work/` as checked on 2026-10-15. Locate the existing private capture or make
a new receive-only recording before labeling the packet tools validated on
the pair. Keep raw UID/config bytes and captures out of Git. Existing public
notes that contain a live pairing value require a separate exposure review.

---

## 21. Passive E8–TinyHawk IQ benchmark (Codex, 2026-10-15)

The §20 handoff above is now superseded: a 20-second, 4 MS/s **receive-only**
B205mini IQ capture was made while the owner reported the E8–TinyHawk pair on
and connected. Raw IQ, settings, and a spectrogram are private under ignored
`work/captures/`; only non-secret measurements are recorded in
[`research/e8-tinyhawk-passive-iq-2026-10-15.md`](research/e8-tinyhawk-passive-iq-2026-10-15.md).

The recorded signal has SF7/approximately 800 kHz LoRa chirps, 12 preamble
symbols, four packets per channel at 6.67 ms spacing, a 2441.38 MHz visit
every 2.133 seconds consistent with the **v3 channel-41 sync schedule**, and
a 6.4-second hopping-pattern repeat. The prior 2440.4 MHz observation is
consistent with a normal FHSS channel, not a v4 sync-channel conclusion.
This source-to-capture agreement substantially advances Gate 1. Exact-pair
attribution would benefit from a separate pair-off control capture.

Only candidate LoRa payload **symbols** have been extracted. The project has
no validated SX1280 long-interleaving `0x07` byte decoder, so no real packet bytes, UID
fragment, nonce, FHSS index, or CRC have been validated. Gate 2 remains open;
do not describe the packet forger as live-validated. No E8 access or RF TX
was involved.

---

## 22. Offline capture intake and symbol front end (Codex, 2026-10-15)

Added read-only `tools/elrs-capture-intake.py` for sidecar/filename metadata,
sample alignment, digital channel coverage, and streaming SHA-256 manifest
checks. On the 320 MB capture it reproduced the recorded hash, 80 million
complex samples, and 20-second duration. Added `tools/elrs-iq-symbols.py`, a
bounded sc16le SF7/BW812.5 kHz LoRa synchronizer and candidate symbol
extractor. It found 12/12 consistent preamble bins and both expected sync
offsets in seven independently selected packets across four channel-41
visits; a quiet control window had 0/12 consistent preamble bins. Three
offline unit tests passed. See the updated passive-IQ report for details.

**Do not call this a packet-byte decoder yet.** Betaflight 4.4.2 selects SX1280
coding-mode value `0x07`. Its old header calls this `4/7 LI`, but Semtech's
corrected datasheet calls `0x07` **4/8 long interleaving**. The LI permutation,
scrambling, and FEC path remain unvalidated; no captured packet has passed a
v3 CRC. The next step is to obtain a trustworthy LI reference or known-
plaintext hardware vector, then validate multiple real frames. The 2026-10
GitHub draft release was still being uploaded at last check; its assets were
not available in this local checkout. Keep new raw data and symbol outputs
under ignored `work/`.

---

## 23. Older private wideband capture validated offline (Codex, 2026-10-15)

The October 2026 draft release assets are now available. One 739.7 MB
`.cdif` recording and its SHA-256 manifest were downloaded into ignored
`work/captures/`; the checksum matches. Its BLUE header declares 28 MS/s
`cf32le` IQ at 2450 MHz with 92,461,824 complex samples (3.302208 seconds),
not raw sc16le. The intake tool now parses this container and its real IQ
data boundaries. The bounded symbol tool now accepts BLUE/`cf32le` input
directly and verifies the declared rate. Five offline tests pass.

The 2441.3788 MHz channel shows four 150 Hz bursts near 0.407 seconds and
another four near 2.540 seconds. All eight have 12/12 consistent SF7 preamble
chirps and expected sync offsets; the revisit interval is 2.133 seconds.
This independently reinforces the PHY/timing evidence but does not prove the
older capture came from the same physical transmitter. No packet bytes, UID,
or CRC were recovered; Gate 2 remains open. The precise provenance, timing,
and limits are in
[`research/2026-10-08-blue-capture-offline.md`](research/2026-10-08-blue-capture-offline.md).

---

## 24. New-hardware intake and analysis blueprint (Codex, 2026-10-16)

Added a repository-wide method for taking a newly acquired device from
unpowered intake through evidence-backed identity, artifact acquisition,
offline analysis, read-only validation, separately approved interaction, and
team handoff:

- [`docs/new-hardware-research-blueprint.md`](docs/new-hardware-research-blueprint.md)
  is the detailed process, including interaction levels, evidence labels,
  per-phase gates, record templates, the seven parser questions, firmware/
  host/RF analysis branches, and explicit flash/RF/fuzzing controls.
- [`docs/new-hardware-quick-reference.md`](docs/new-hardware-quick-reference.md)
  is the bench checklist and standing never list.
- [`SECURITY.md`](SECURITY.md) now supplies the repository code, artifact, and
  laboratory guardrails referenced by the method.

The blueprint is explicitly a method rather than authorization. Current owner
direction in `AGENTS.md` and target-specific constraints always control. In
particular, nothing in these documents changes the E8's absolute no-flash,
no-write, no-reset/bootloader, no-fuzz, and no-RF-TX boundary. No hardware was
accessed and no downloaded program was executed while preparing the documents.

---

## 25. E8 and TinyHawk blueprint onboarding (Codex, 2026-10-16)

Applied the new-hardware blueprint to both connected EMAX devices under
`references/`. Each dossier now has a target card, interface map, artifact
manifest, claim-by-claim evidence ledger, test log, and prioritized open
questions. Raw identifiers and captures remain under ignored `work/`.

Read-only USB enumeration found the TinyHawk on `/dev/ttyACM0` as
`0483:5740` / `Betaflight STM32F411` and the E8 serial personality on
`/dev/ttyACM1` as `8888:2001` / `USB Serial`. No port paths should be assumed
after reconnect.

After confirming no process owned either endpoint, the existing bounded E8
reader was run once. It received the expected 17-byte configuration response
and reconfirmed the 150 Hz rate. The raw response and pairing UID remain
private. No settings write, reset/mode transition, downloaded-program
execution, update/flash operation, fuzzing, or RF transmission occurred.

The resulting status is deliberately precise:

- **E8:** configuration interface confirmed; exact PCB revision, MCU, RF IC,
  installed firmware version, and firmware image remain unknown. A public FCC
  filing supplies only a related EMAXE8 board/RF reference because its chip
  marks are unreadable and the connected unit revision has not been matched.
- **TinyHawk III Plus:** live Betaflight 4.4.2/revision/target, STM32F411
  family, integrated SPI ELRS/SX1280 resources, and version-matched official
  artifacts are confirmed. Exact chip top-marks/PCB revision and byte identity
  of installed flash remain unproven.

This corrects the earlier shorthand that the TinyHawk lacked firmware
acquisition: its official 4.4.2 image, derived analysis binary, source archive,
and target configuration are already tracked and hashed. They are
version-matched artifacts, not a dump of installed flash.

---

## 26. Detailed E8/TinyHawk identification handoff (Codex, 2026-10-16)

### Objective and starting state

The owner asked whether the connected EMAX E8 and TinyHawk III Plus had been
fully identified and authorized read-only work needed to close ordinary
identification gaps. Work began from `46ff801` after `git pull --ff-only origin
main` reported that `main` was current. The existing untracked `.claude/`
directory was treated as user-owned and left untouched.

The active boundary came from `AGENTS.md`, `SECURITY.md`, and
`research/E8-COLLABORATION.md`: normal identification/configuration reads were
allowed; the E8 could not be flashed, updated, erased, restored, reset into a
different mode, written, fuzzed, or used for RF transmission. Port availability
alone was not accepted as a collaboration handoff. No enclosure opening,
debug-probe attachment, firmware readout, or TinyHawk state change was assumed
to be authorized.

### Fresh repository review

The following were reread from disk before device work:

- `AGENTS.md`, including the E8 absolute no-flash rule;
- `research/E8-COLLABORATION.md` and
  `research/E8-USB-CHARACTERIZATION.md`;
- `HARDWARE-IDENTITY.md` and `research/e8-protocol-reconstruction.md`;
- `research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md`;
- `tools/e8-read-config.py`, the firmware manifest, and the new-hardware
  blueprint/quick-reference/security documents.

This review corrected the initial shorthand about the TinyHawk. Its exact
installed bytes have not been dumped, but the repository already contains a
live version/build identity and version-matched official artifacts. The E8's
normal configuration protocol was already reconstructed, but its exact
silicon and firmware were still unknown.

### USB enumeration and port ownership

The first sandboxed `lsusb` call failed with `unable to initialize libusb`.
`lsusb -t` still revealed two CDC devices, so descriptor enumeration was
repeated through the approved host path. This worked and produced:

| Device | Session endpoint | Descriptor evidence | Meaning and limit |
|---|---|---|---|
| TinyHawk | `/dev/ttyACM0` | `0483:5740`, `Betaflight STM32F411`, CDC ACM 1.10, Linux `cdc_acm`; stable by-id product path present | Corroborates the application and MCU family; does not give PCB revision, exact package suffix, or flash bytes |
| E8 serial personality | `/dev/ttyACM1` | `8888:2001`, `Your Company` / `USB Serial`, CDC ACM 1.10, Linux `cdc_acm`; stable by-id product path present | Confirms the known generic configuration personality; does not identify MCU, RF IC, firmware, or board revision |

Both exposed two interfaces with 64-byte bulk IN/OUT endpoints and an interrupt
IN endpoint. Device-node numbers are session-local and must be re-enumerated
after reconnect. `fuser -v /dev/ttyACM0 /dev/ttyACM1` reported no current owner
before the one permitted E8 read. A free port was used only after the owner's
explicit read-only handoff in the request; future agents must obtain their own
current handoff.

### E8 live read: what worked

`python3 tools/e8-read-config.py --port /dev/ttyACM1 --execute` was run once.
It sent only the documented fixed 64-byte Read Configuration request at 115200
8-N-1 and received the expected 17-byte response. The parser reconfirmed rate
index `0x02` as 150 Hz, redacted the six-byte pairing UID on stdout, and kept
the raw response under ignored `work/e8-session/`.

This establishes that the current connected E8 serial personality and the
native read path work. It also resolves the older zero-response state in
`research/E8-USB-CHARACTERIZATION.md`; that older chronology remains relevant
for mode, power, and port-contention lessons.

The response does **not** contain a firmware version and does not identify the
MCU or radio. The remaining config indexes were not promoted to named values
without an independent vendor oracle. No second read was needed. No settings
write, control-line experiment, mode transition, reset, vendor-program
execution, update/flash call, fuzzing, or RF action occurred.

### TinyHawk live work: what was deliberately not repeated

The TinyHawk serial port was not opened. The private 2026-10-15 CLI transcript
already reports Betaflight 4.4.2, build time `2023-11-30 01:55:56`, revision
`23d066d08`, MSP API 1.45, `STM32F411`/`S411`, target
`EMAX_TINYHAWKF4SX1280`, EMAX manufacturer ID, and integrated SPI ExpressLRS
resource assignments. The new USB descriptor observation independently
corroborated `Betaflight STM32F411`; repeating the CLI query would have added
little evidence while creating another live interaction.

The firmware corpus was checked rather than reacquired. Hashes matched:

| Artifact | SHA-256 | Classification |
|---|---|---|
| `firmware/betaflight/betaflight_4.4.2_STM32F411.hex` | `bed2ed630acf8301b966ed6ac9fadea53ad0337535856ff8d05e45f852ea9729` | Official version/family-matched release image |
| `firmware/betaflight/betaflight_4.4.2_STM32F411.bin` | `33830bda10262ebc2b3a8c337de611a6f5c9f4b3560bf1b0997f1d1081ffbf3d` | Derived raw analysis binary |
| `firmware/src/betaflight-4.4.2.tar.gz` | `195b0c6eb66741f6be64f30c63e6a41cc0cf2e990505508f1f7cc270de477c29` | Version-matched source archive |
| `firmware/betaflight/EMAX_TINYHAWKF4SX1280.config` | `7b21b072fede3e23fdc43b4d4722d47276f789f6517573e956ec228b1f6bea94` | EMAX target/resource configuration; its older header remains qualified |

These artifacts support qualified 4.4.2 source/binary analysis. They are not a
byte-for-byte dump or attestation of the installed flight-controller flash.

### Public E8 research: what worked and what did not

The public FCC record for `2AL7Y-EMAXE8` was reviewed as a product-family
reference, not as exact-unit evidence:

- internal-photo exhibit 5536597, SHA-256
  `6a0791680a096019dc749bde18cdde7478d5c26e96a4e04965476ea539cbe566`;
- test-report exhibit 5536602, SHA-256
  `c75f307e436f024fc54f4d3acb6c4046a8e63327b1a1bf850e74d56870d34e58`.

The test report usefully records a 2.4 GHz EMAXE8 family transmitter using
GFSK/2-FSK and an internal antenna. It does not supply a processor or RF-chip
part number. The filing lists its block diagram, schematic, and operational
description as metadata-only/confidential; no attempt was made to bypass that
restriction.

Rendering the PDF at high resolution and cropping the main square package did
not recover a legible top-mark. `pdfimages` was then used to extract the
original embedded 1050×787 JPEGs. This proved the limiting factor was the source
photograph, not the renderer: the chip marking and board silkscreen remain too
soft for a defensible part/revision transcription. Upscaling the same pixels
should not be repeated or treated as new evidence.

The FCC material therefore establishes a related EMAXE8 board layout and RF
description only. It does not prove that the connected ELRS variant uses that
revision, and it cannot close the MCU/RF identity gap.

### Documentation produced

Created `references/README.md` as the device completion matrix and two complete
blueprint dossiers:

- `references/emax-e8_material/`
- `references/tinyhawk-iii-plus_material/`

Each contains `TARGET-CARD.md`, `INTERFACE-MAP.md`, `ARTIFACTS.md`,
`EVIDENCE-LEDGER.md`, `TEST-LOG.md`, `OPEN-QUESTIONS.md`, and a summary
`README.md`. The repository README and the older E8/TinyHawk identity notes now
link to these dossiers. Claims are separated into live-observed,
source-derived, related-reference, unproven, and contradicted classes.

The final status recorded there is:

- **E8 — partially identified:** USB personalities and the configuration-read
  protocol are confirmed; exact PCB revision, MCU, RF IC, architecture,
  installed firmware version, and firmware image are not.
- **TinyHawk — functionally identified:** live Betaflight/build/target,
  STM32F411 family, integrated SPI ELRS/SX1280 resources, and version-matched
  artifacts are confirmed; exact PCB/top-marks and installed-flash byte identity
  remain open.

### Privacy, validation, and repository handoff

No UID, raw E8 response, private CLI transcript, capture, or private digest was
committed. The short response's digest was also omitted because hashing a small,
structured private payload is not useful public evidence and may aid guessing.
Only sanitized product descriptors and public-document hashes were tracked.

Validation completed before commit:

- `git diff --check` passed after removing extra blank EOF lines;
- every local Markdown link in the new/updated files resolved;
- all four TinyHawk artifact hashes and both FCC exhibit hashes matched;
- a targeted secret/identifier scan found no newly introduced pairing value;
- `.claude/` remained untracked and untouched.

Commit `cc7dbb0` (`docs: onboard E8 and TinyHawk evidence`) was pushed to both
`origin/main` (`oatmealphysics/research-project`) and `enclave/main`.

### Way forward

1. **Exact-unit exterior inventory:** photograph every side, the USB connector,
   regulatory/model label, packaging, and battery markings for each device.
   Redact serials and private identifiers before committing any image or note.
2. **Physical board identity:** if the owner separately authorizes opening,
   take sharp, square-on photographs of both PCB sides, board revision text,
   MCU top-mark, RF IC top-mark, memory, oscillator, antenna path, and accessible
   headers. Opening remains a separate gate; the current request did not require
   or accomplish it.
3. **E8 firmware provenance:** ask EMAX for the exact board/firmware identity,
   a documented read-only version query, and an attributable firmware package
   for the connected revision. Hash and inspect anything received without
   executing it. Do not substitute OLED/Nano ExpressLRS targets or the Windows
   host tool for E8 device firmware.
4. **TinyHawk strict identity:** use exact-unit photos to confirm the
   STM32F411CEU6/SX1280 top-marks and PCB revision. The existing 4.4.2 artifacts
   are sufficient for qualified source analysis; do not pursue a firmware dump
   merely to make the checklist look complete.
5. **Any firmware readout:** only if a concrete research question justifies it,
   write a separate non-writing readout plan with exact target, access method,
   secrets handling, recovery analysis, and explicit owner approval. This does
   not weaken the E8's permanent no-flash rule.
6. **Resume the active ELRS program independently:** physical identity gaps do
   not block the existing passive/offline Gate 2 work in §§20–23. Continue the
   real-capture long-interleaving/packet-byte validation without claiming live
   packet acceptance and without enabling RF TX.

### Do not repeat or overclaim

- Do not rerun broad baud/protocol/reset guessing against the E8; its documented
  normal read path now works.
- Do not infer E8 silicon from `8888:2001`, `REV_0100`, related EMAX OLED/Nano
  targets, or `Serial Update Tool 2.1`.
- Do not upscale the existing FCC photo again and claim a chip identification.
- Do not call the official TinyHawk 4.4.2 artifact an installed-flash dump.
- Do not open either port concurrently with another agent, and do not infer a
  handoff from an apparently free device node.
- Do not move private material from `work/` into Git.

---

## 27. ELRS profile evidence gate and connected-SDR intake (Codex, 2026-10-16)

### Starting revision and objective

Started from `a32b0ed` after pulling `main` and rereading `AGENTS.md`, the
latest session entries, and `SECURITY.md`. The owner asked to continue and
reported that the E8 and TinyHawk were plugged in, with the controller expected
to transmit and an SDR attached. The immediate objective was to preserve the
Gate 2 evidence boundary while reviewing the newly merged v3 compatibility
layer and attack-profile generator, then assess whether a receive-only SDR
observation was possible from this Windows checkout.

### Actions and evidence

- Checked the pinned Betaflight 4.4.2 `USE_RX_SX1280` rate table in
  `firmware/src/betaflight-4.4.2.tar.gz`,
  `src/main/rx/expresslrs_common.c:61-66`: index 0 = 500 Hz/2000 us,
  1 = 250 Hz/4000 us, 2 = 150 Hz/6666 us, 3 = 50 Hz/20000 us. The
  newly merged `tools/elrs-attack-profile.py` instead used a newer seven-rate
  map for v3 and defaulted to index 4. It also put FHSS channel 41 in the
  synthetic SYNC `fhss_index` field, where a sequence index belongs.
- Found that its `--iq-file` branch invoked the archived
  `tools/elrs-sync-capture.py` prototype. That prototype's entry point is
  deliberately disabled because SX1280 long-interleaved IQ has not yielded
  validated packet bytes. The profile's synthetic CRC round-trip cannot
  substitute for real-frame validation.
- Updated the profile tool to use the 4.4.2 v3 rate indexes (default 2),
  reject unsupported v3 indexes, use FHSS index 0 in its synthetic SYNC,
  fail closed on raw IQ input, label the self-check `synthetic_only`, and
  block downstream forger/TX handoff. Its optional command display now points
  only to the offline v3 frame checker and repeats the RF-TX safety gate.
  Profile schema version is 2. No real UID or captured bytes were used.
- Added six synthetic regression tests in
  `tools/tests/test_elrs_attack_profile.py`. The pre-existing 52 v3-core tests
  also passed. The full offline `tools/tests` suite passed: **86 tests**.
- Windows read-only enumeration saw an Ettus B205mini, USB
  `VID_2500&PID_0022`, status OK. No native `uhd_find_devices` or
  `SoapySDRUtil` command was found. WSL had Python but neither UHD nor Soapy
  utilities, and no usable SDR device path was established in WSL. Therefore no
  new IQ recording was attempted. The existing private captures under
  ignored `work/captures/` were left unchanged.

### Limits, safety, and handoff

This work establishes a corrected **offline parameter representation**, not
decoded real frames, an exact live-binary match, or receiver acceptance.
Gate 2 in §20 remains open. No E8 or TinyHawk serial port was opened; no
settings were written, no firmware was flashed, and no RF was transmitted.
The user's report that the devices are on is not a serial-access handoff.

Changed files: `tools/elrs-attack-profile.py`,
`tools/tests/test_elrs_attack_profile.py`, and this running log. The tool/test
change was committed as `042aac1`; it and the log update were pushed to
`origin/main` at handoff. Validation used only synthetic/offline fixtures.

Next smallest useful step: make a known receive-only UHD/Soapy environment
available to this host or the Ubuntu lab machine, then take a bounded passive
recording with exact sample-rate/center-frequency metadata and a pair-off
control if the owner can arrange one. Independently, continue the SX1280
long-interleaver/FEC reference or known-plaintext fixture work before feeding
any IQ-derived bytes to the v3 CRC checker. Do not use the archived SYNC
prototype or a synthetic profile status as evidence of a live decode.

---

## 28. TinyHawk absolute no-flash direction and B205mini readiness (Codex, 2026-10-16)

Started from `d70a453` after a current `git pull --ff-only`. The owner
explicitly extended the E8's absolute no-flashing rule to the owned TinyHawk
III Plus drone. Neither device may be flashed, updated, erased, restored, or
recovery-flashed under any circumstances. This direction overrides generic
approval gates and any earlier suggestion to use a firmware update for
validation. Other TinyHawk state-changing work still requires its separate
gate. No hardware operation was undertaken for this rule change.

The user also asked what is needed for passive B205mini capture. Read-only
Windows enumeration confirmed USB `VID_2500&PID_0022`, `WINUSB` service,
Ettus Research driver provider, version `4.9.0.0`, and device status OK.
No UHD or Soapy receive utilities were found on this Windows PATH; the
available WSL instance likewise lacked them. This narrows the immediate
missing component to a host receive application/UHD runtime rather than an
unbound Windows USB driver. Official Ettus UHD Windows installation guidance
is at https://files.ettus.com/manual/page_install_binary.html and its
receive-only example inventory is at
https://files.ettus.com/manual/page_gsg_examples.html. Software was not
downloaded, installed, or executed, and no IQ recording was made.

Changed files: `AGENTS.md`, `SECURITY.md`,
`references/tinyhawk-iii-plus_material/README.md`, and this log.
Validation: checked the no-flash wording across these files and confirmed
the worktree diff has no formatting errors. Commit/push status: this update
was committed and pushed to `origin/main` at handoff. No UID, raw capture,
or device response was added.

Next step for receive-only capture: install/locate an official Windows UHD
runtime compatible with the existing B205mini driver, verify device discovery,
then use an RX-only bounded IQ recorder with metadata and private storage.
This is a host-software change, not permission to flash either drone device
or transmit RF. Independently, continue offline Betaflight 4.4.2 failsafe
and MSP-path validation; classify source reachability separately from a
confirmed live exploit.

---

## 29. Correction: UHD was installed and passive recording already existed (Codex, 2026-10-16)

Starting revision: `cf668ca`; `git pull --ff-only` was current. The owner
corrected Codex's assertion in §§27-28 that this Windows session lacked SDR
receive software. Those sections inferred absence from commands not resolving
on `PATH`; that inference was wrong. This entry supersedes only that UHD
availability diagnosis, not the private capture's analysis or the no-flash
rules.

Read-only checks found `C:\Program Files\UHD\bin\uhd_find_devices.exe`,
`C:\Program Files\UHD\bin\uhd_usrp_probe.exe`, and
`C:\Program Files\UHD\lib\uhd\examples\rx_samples_to_file.exe`.
`uhd_config_info.exe --version` reports **UHD 4.11.0.0-release**. Windows
enumeration already showed the B205mini with Ettus WinUSB 4.9.0.0. The
existing local, ignored recording
`work/captures/e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq`
is 320,000,000 bytes, and
`research/e8-tinyhawk-passive-iq-2026-10-15.md` records its 20-second,
4 MS/s, RX2, receive-only acquisition and UHD version. Its private metadata
records that the UHD example made the capture. The source-to-capture PHY
analysis in that research note remains qualified; packet bytes and CRC have
not yet been decoded.

Therefore no installation or driver replacement is needed for another
Windows receive-only capture: use the existing UHD executables by absolute
path (or arrange `PATH`). A fresh recording may improve evidence, but it is
not blocked by missing software, and the current Gate 2 blocker remains the
validated SX1280 long-interleaved packet-byte decoder. No new capture,
device-port access, RF transmission, firmware operation, or private-data
publication occurred during this correction.

Changed file: this append-only log. Validation: compared installed UHD
executables/version with the previously tracked passive-IQ provenance and
confirmed `git diff --check`. Commit/push status: committed and pushed to
`origin/main` at handoff. Next step: use the existing private capture for
offline decoder work; take a new bounded receive-only capture only if it
answers a specific unresolved measurement question.

---

## 30. ELRS source-level attack feasibility and MSP motor path (Codex, 2026-10-16)

Starting revision: `97fbb6b`; `git pull --ff-only` reported current before edits. The owner asked to weaponize the source-level non-RC/failsafe finding or look for other vectors, including firmware research and device probing within reason. Work stayed offline/read-only under `AGENTS.md` and `SECURITY.md`; no device port was opened, no SDR transmitted, no settings were changed, no motors were driven, and neither device was flashed.

Rechecked the existing pinned Betaflight 4.4.2 source SHA-256 (`195b0c6eb66741f6be64f30c63e6a41cc0cf2e990505508f1f7cc270de477c29`). No new firmware acquisition or live readout was needed or authorized. The non-RC finding remains a source-level candidate; the existing single-channel passive SYNC observations do not establish a sustained accepted non-RC stream across the hopping link. The PHY remains undecoded, so no accepted forged frame or packet delivery is established.

Traced a more direct source-level candidate: CRC-valid ELRS MSP packet -> connected/model-matched reassembly -> CRSF/MSP dispatcher -> `MSP_SET_MOTOR` (214) -> `motor_disarmed[]` -> disarmed mixer output. The handler lacks an adjacent arming guard; `MSP_EEPROM_WRITE`, by contrast, rejects when armed. This is potential radio-mediated abuse of an intentional MSP function, **not** confirmed unauthorized access, arbitrary code execution, or a live exploit. The live runtime `feature -TELEMETRY` setting is not itself proof that this receive path is absent; source/config support a compiled path, but installed-binary identity and live command acceptance remain unproven. Detailed evidence and prerequisites: `research/betaflight-442-elrs-msp-motor-path-2026-10-16.md`.

What worked: source-chain and provenance verification. What remained inconclusive: exact installed binary, UID/CRC delivery, real OTA bytes, command acceptance, motor effect, and failsafe behavior. Changed files: the new research note and this append-only log. Validation: source paths, guards, source hash, and `git diff --check`; no hardware tests. Commit/push status: committed and pushed to `origin/main` at handoff. Next step: passive PHY byte recovery and/or host-only mocked MSP/motor test; any live crafted-input or output test requires a separate bounded owner-approved bench plan with props removed, and flashing remains prohibited.

---

## 31. Offline ELRS ranked-symbol recovery, byte decode still open (Codex, 2026-10-16)

Starting revision: `61dec1a`; `git pull --ff-only` reported current before edits. The owner asked to decode the existing passive packet capture and clarified that no binding phrase is presently applied in Betaflight, with phrase-based validation possible later. That setting is not an all-zero UID assumption: the 4.4.2 receiver uses a nonzero paired UID for normal operation and a special UID only in binding mode. The previously wired-read private E8 UID can check eventual decoded bytes but does not itself recover them.

Using only the saved 20-second receive-only IQ, the pinned 4.4.2 source, and the Semtech SX1280 Rev 3.3 datasheet, extended symbol extraction to retain top-three relative FFT-bin hypotheses and top-two power margins. Reprocessed 34 real bursts to a new ignored private file `work/oct15-ranked-2026-10-16.jsonl`: 34/34 satisfy the existing preamble/sync PHY candidate checks, with 714 analyzed payload-symbol positions (21 per frame). Of those, 157 have a top-two margin below 3 dB; this is a power measure, not calibrated probability. A private ±3-sample payload-timing test yielded only 0.0114 mean dominant-bin power-fraction gain and 0.177 dB mean margin gain. These observations do not produce or validate packet bytes. The SX1280 `0x07` long-interleaver, FEC, and scrambling inverse still lacks an independent known-answer vector; do not use the quarantined raw-bin packer or call CRC on invented bytes a decode.

Changed tracked files: `tools/elrs-iq-symbols.py`, `tools/elrs-batch-evidence.py`, their two test modules, `research/elrs-packet-decode-progress-2026-10-16.md`, and this append-only log. The full offline suite passed (87 tests); `git diff --check` passed. Commit/push status: committed and pushed to `origin/main` at handoff. No E8/drone port or SDR device was opened, no firmware was read or flashed, no RF was transmitted, and private UID/capture/symbol data stayed ignored. Next step: obtain an independently labeled SX1280 4/8-LI IQ-plus-FIFO-byte oracle or authoritative inverse, then implement and verify the byte decoder before testing the private capture's CRC/FHSS fields. Any physical tap, pairing change, or live crafted-input experiment needs its own scoped approval; the absolute no-flashing constraint remains.

---

## 32. No-extra-SX1280 route and paired RC/IQ observation (Codex, 2026-10-16)

Starting revision `6c0c39d`; `git pull --ff-only` was attempted before edits
but this host could not connect to GitHub. The worktree was clean. The owner
asked to pursue the offline decoder, TinyHawk USB channel oracle, and passive
SPI tap. The owner explicitly declined opening the drone, so **no SPI tap was
attempted**. A software oscilloscope cannot expose the internal SPI bus through
stock Betaflight USB. Absolute no-flashing for both devices remains in force.

Offline review confirmed the existing 34 PHY candidate frames / 714 symbol
positions remain **undecoded**. A privacy-safe aggregate found 28 distinct
21-symbol vectors, one repeated four times; this is repeatability evidence,
not byte or CRC validation. The Semtech SX1280 Rev 3.3 datasheet confirms
`0x07` is 4/8 long interleaving but does not publish the bit permutation.
`research/elrs-no-extra-sx1280-path-2026-10-16.md` records the evidence bar
for an SDR-only decoder and a separate USB-level check. No proposed inverse
was promoted to a validated decoder merely from a synthetic round-trip.

With the owner reporting the TinyHawk USB port free and propellers removed,
read-only Windows enumeration identified the board at COM5 (`0483:5740`).
MSP read queries verified Betaflight 4.4.2 and craft name `TH III Plus` and
returned stable baseline RC channels. The first paired SDR background launch
failed before writing IQ due missing UHD DLL lookup. A half-second direct UHD
receive-only preflight succeeded after adding the installed UHD bin directory
to that process's PATH. A second background launch failed due a Windows
`Start-Process` duplicate `Path`/`PATH` environment-key error, but its USB
observation logged 159 samples with right-stick movement. COM5 briefly
disappeared and reappeared; no reset, reconfiguration, or alternate-port guess
was used. The owner reported both devices remained connected and powered.

The direct foreground UHD retry succeeded: 15.002507 seconds, 60,010,000
complex samples at 4 MS/s, 2441 MHz center, RX2, receive-only, with 114
private MSP_RC samples. Channel 0 ranged 1311–1710; throttle channel 3 stayed
988. Filesystem timestamp overlap is approximately 3.6 seconds with 30 USB
samples and channel 0 spanning 1313–1633 in that interval. The IQ and channel
logs, hashes, and timing caveats are in the research note; raw files remain
ignored under `work/captures/`. This establishes a live decoded-channel
observation concurrent with RF acquisition, **not** a byte decode, exact
packet association, or exploitability. The E8 port was not opened; no RF was
transmitted; no device setting, firmware, motor, or physical probe action was
taken.

Changed tracked files: a dry-run-default, identity-gated read-only MSP
observer and synthetic tests under `tools/`, the research note, and this log.
The configured Windows Python venv has a missing base interpreter, so the new
tests could not run here; `git diff --check` is the available local validation.
Next: run the synthetic tests on a working Python 3.10+ environment, extract
PHY candidates from the private new IQ, and continue seeking an independently
validated long-interleaver inverse. For a cleaner USB/IQ correlation, begin
USB logging before SDR initialization or extend it through the full receive
window. No SPI access is authorized while the owner's no-opening direction
stands.

Handoff status: research/tool commit `ba0aa03` was pushed to `origin/main`.
This append-only status correction is being committed and pushed separately;
private IQ, USB logs, and UID remain ignored and unpublished.

---

## 33. Python restored; new paired IQ scanned offline (Codex, 2026-10-16)

Starting revision `8d82678`; `git pull --ff-only` reported current and the
worktree was clean. The owner authorized Windows Python setup, pending tests,
and analysis of the new private paired SDR recording. No device port or SDR
was opened during this turn. The no-flash rule for both devices and the
owner's no-opening decision for the TinyHawk remained in force.

Downloaded the official Python.org 3.12.10 x64 installer under ignored
`work/runtime-bootstrap/`. Verified SHA-256
`67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB`
and a valid Windows Authenticode signature from the Python Software Foundation
*before* execution. Installed a per-user interpreter under
`C:\Users\Board\Documents\Codex\Python312` without changing system PATH,
then created an independent ignored `work/python312-venv/`. Installed pinned
NumPy 2.5.4, SciPy 1.18.1, and pyserial 3.5 wheels. The prior broken venv was
not overwritten. The six previously pending synthetic USB-observer tests and
the entire offline suite passed (**93 tests**).

The read-only intake tool rechecked the private October 16 IQ hash, size,
sample alignment, duration, and frequency coverage. The full 15-second
source-matched channel scan found **28 PHY candidates** in seven four-packet
visits, mean spacing 2.133147 seconds; 28/28 passed 12/12 preamble and both
sync-chirp checks and had 21 symbol positions. Two visits fall inside the
approximate USB/IQ overlap. These are still waveform/symbol observations:
**no SX1280 long-interleaved packet bytes, CRCs, or packet-to-channel mapping
were validated**. All private outputs remain under ignored `work/`.
Detailed provenance, hashes, aggregate results, and limitations are in
`research/tinyhawk-paired-phy-analysis-2026-10-16.md`.

Changed tracked files: the new research note and this append-only log.
Validation: Python/package versions, 93 passing tests, capture intake hash,
PHY scan aggregates, and `git diff --check`. Commit/push status is recorded
in the subsequent handoff line. Next step: develop or locate an independently
validated SX1280 `0x07` inverse; use the private candidate set and held-out
real frames only after that gate. No additional hardware, transmission,
flashing, binding, or physical probe is authorized by this offline result.

Handoff status: research/log commit `df227d8` was pushed to `origin/main`.
This status line is being committed and pushed separately; the worktree
contains no tracked capture, private UID, installer, or virtual environment.

---

## 34. Externally measured SX1280 LI map unlocks real v3 bytes (Codex, 2026-10-16)

Starting revision `418c77e`; `git pull --ff-only` was current and the worktree
was clean. Objective: investigate the owner's Gemini proposal and seek a
non-circular solution to the SX1280 4/8 long-interleaver decoding gap. No
device port or SDR was opened, no RF was transmitted, and neither owned
device was modified, opened, or flashed.

Found the original WaveShark SX128x long-interleaver source at pinned commit
`62b513a7b699fd3a6928908102902abf9d9b2650`. Its SF7 4/8-LI mapping
comes from an RP2 one-bit-at-a-time hardware measurement and independent
TX16S off-air confirmation, rather than a self-consistent synthetic encoder.
An attributed **private, ignored** Python experiment at `work/try_li.py`
first reproduced WaveShark's independent 13-byte off-air fixture, then
decoded our two previously captured eight-byte frame sets. WaveShark's
GPL-3.0-or-later code was not vendored into tracked files.

Of 62 existing PHY candidates, 54 distinct frames passed strict FEC and the
source-matched Betaflight v3 CRC: 16 SYNC and 38 RC. They used the published
map with positive bin orientation and relative-bin offset 0 or +1, not a
packet-specific guessed permutation. All SYNC frames agreed on one private
two-byte UID suffix, hybrid switch encoding, and air-rate code 7; the pinned
Betaflight 4.4.2 mapping converts over-air code 7 to local rate index 2,
matching the 150 Hz link. All 38 RC frames passed CRC using the UID suffix
derived from *different* SYNC frames. SYNC self-derived CRC alone is not an
independent identity proof. Eight lower-confidence candidates remain
unresolved. No raw bytes or UID are printed or committed.

Detailed counts, provenance, Gemini-method assessment, and remaining gates are
in `research/elrs-sx1280-li-decode-breakthrough-2026-10-16.md`. Changed
tracked files: that note and this append-only log. Validation: the independent
off-air fixture and 54 real v3 CRC matches; no live acceptance, RF-TX, or
control-channel correlation claimed. Next: package a license-compliant
read-only offline decoder, recover ranked alternatives if useful, and compare
decoded RC changes against the limited paired USB channel observation. The
owner's no-flash and no-opening limits remain in force. Commit/push status is
recorded by the subsequent handoff.

---

## 35. Capture publication and maintained offline LI decoder (Codex, 2026-10-16)

Starting revision `d27faa2`. The owner authorized publishing the captures
and requested completion of the offline decoder and comparison against the
right-stick USB log. The original Desktop checkout became read-only in this
session, so work continued in a new writable local clone at
`C:\Users\Board\Documents\Codex\elrs-li-capture-publication`. It was fetched
against GitHub `main` before editing. The original checkout was not changed.
No controller/drone port, SDR, RF transmitter, firmware updater, or hardware
probe was opened during this work. The owner's absolute no-flash and
no-opening boundaries remain in force.

Published the October 15 and October 16 pair IQ recordings under `captures/`
through Git LFS (320,000,000 and 240,040,000 bytes), plus their ranked-symbol
JSONL files, the October 16 read-only USB/MSP RC log, a privacy-safe decoded
summary, capture format/timing README, and a verified SHA-256 manifest. This
publication makes over-air pairing-related data derivable by others; it is an
explicit owner-directed disclosure. The separate wired E8 configuration
response and full private UID were **not** published. Git LFS uploaded both
objects successfully: **560 MB, 2/2**.

Completed `tools/elrs-li-offline.py`, an attributed GPL-3.0-or-later offline
implementation of the externally measured WaveShark SF7/4-8-LI inverse. Its
tests use two independent TX16S off-air vectors and both published real
ranked-symbol files. It checks the source-matched Betaflight v3 CRC and by
default omits packet bytes/UID from output. The result remains **54 CRC-valid
frames of 62** (16 SYNC, 38 RC, eight unresolved). Added the upstream GPL
license text and a separate fixed-anchor `tools/elrs-rc-overlap.py`.

The original October 16 IQ creation timestamp (not a fitted offset) anchors
the USB comparison. Five CRC-valid RC frames lie within 120 ms of USB samples;
three stationary channel values agree exactly and the moving right-stick
channel has 13.6 mean absolute channel-unit difference. Nearest USB sample
gaps are 10–36 ms, so this supports the channel interpretation without
claiming exact frame-to-stick association. Fresh full rescans of the two
**published IQ copies** reproduced 34/28 PHY candidates and 32/22 CRC-valid
frames. All six capture-manifest SHA-256 hashes matched. The full offline
test suite passed (**99 tests**), and `git diff --cached --check` was clean.

Changed tracked files: `.gitattributes`, `captures/` (two LFS IQ files and
six small evidence/documentation files), `tools/elrs-li-offline.py`,
`tools/elrs-rc-overlap.py`, their tests, GPL license text, and the research
note update. The work/tool/capture commit `510cd11` was pushed to
`origin/main`; this append-only status entry is being committed/pushed as a
separate handoff. Next: compare held-out motion at a tighter synchronized
clock if the owner wants exact per-frame association, and try constrained
ranked-bin recovery for the eight unresolved frames. Neither changes the
separate authorization gate for active RF testing.

---

## 36. Live passive bench review and independent-UID fix (Codex, 2026-10-16)

Starting revision `2e49bf2`; `git pull --ff-only` reported current. The owner
released COM4 to this session and permitted future SDR transmission. Work
remained read-only/non-transmitting because the newly pulled bench plan still
has an open multi-channel FHSS gate. No firmware was flashed or read out, no
setting/reset/binding/motor command was issued, the TinyHawk was not opened,
and no RF was transmitted.

Windows enumerated the E8 on COM4 (`8888:2001`), TinyHawk on COM5
(`0483:5740`), and B205mini serial `33E8308`. The known fixed E8 Read
Configuration request returned the expected 17-byte response, confirmed the
existing 150 Hz setting, and yielded the private UID without changing a
setting. An eight-second B205mini RX2 capture at 2441 MHz, 4 MS/s, 2 MHz
bandwidth, and 35 dB gain contained zero PHY candidates. This is a valid
negative control: the E8 was in its CDC serial/update personality, where the
normal RF application is not running. The private capture and response remain
ignored under `work/`.

Fixed `tools/elrs-li-offline.py` so a supplied full UID must pass both CRC and
the Betaflight 4.4.2 18-bit UID/model-fragment check before a SYNC frame is
accepted. A synthetic test flips only UID byte 3 and verifies rejection. The
fresh private controller UID now independently validates all 16 accepted SYNC
frames from the two published capture sets; totals remain 54/62 CRC-valid (16
SYNC, 38 RC, eight unresolved). Across both sessions, all 14 consecutive SYNC
transitions match the 150 Hz slot count modulo 256 against nonce progression;
maximum nearest-slot timing residual was about 522 microseconds.

Also corrected `--include-bytes` privacy wording and added unit-test discovery
to CI. Detailed evidence and gate status are in
`research/elrs-bench-gate-review-2026-10-16.md`. The full suite passed (100
tests), project validation passed across 177 tracked files, and
`git diff --check` passed. Next: boot the E8 normally and take a short
receive-only wideband capture to close G4; only then restate a separately
bounded RF test plan for owner approval.

---

## 37. Python 3.10 CI timestamp compatibility fix (Codex, 2026-10-16)

Starting revision `7d0a3bb`; `git pull --ff-only` had already reported the
checkout current before diagnosis. GitHub Actions run `38979000441` passed on
Ubuntu and Windows with Python 3.12, but failed on both platforms with Python
3.10. The new full test-discovery step exposed a pre-existing compatibility
problem in `tools/elrs-rc-overlap.py`: the published Windows USB log contains
seven fractional-second digits, while Python 3.10's
`datetime.fromisoformat()` accepts at most the six digits representable as
microseconds.

Updated `parse_utc()` to explicitly truncate sub-microsecond digits before ISO
parsing. The normalization applies to both `Z` and numeric-offset timestamps,
preserves the existing rejection of timezone-naive input, and behaves the
same on every supported Python version. Added regression coverage using the
exact seven-digit timestamp shape from the published capture, plus a
non-UTC-offset equivalent and a naive-timestamp rejection check.

The full local suite passed (**102 tests**) under the available Python 3.12
project environment; project validation passed across 177 tracked files and
`git diff --check` passed. Python 3.10 is not installed on this workstation,
so the cross-version confirmation is delegated to the replacement GitHub
Actions matrix run after push. No hardware, serial port, SDR, RF transmitter,
firmware, or device setting was accessed or changed. Changed tracked files:
`tools/elrs-rc-overlap.py`, `tools/tests/test_elrs_rc_overlap.py`, and this
append-only log. Commit and push to `origin/main` follow this entry; the next
step is to verify all four replacement matrix jobs complete successfully.

---

## 38. Read-only serial viability check (Codex, 2026-10-16)

Starting revision `f59be5a`; `git pull --ff-only` reported current. The owner
asked whether the connected devices could be accessed through their serial
ports and whether that path could be useful. Windows enumerated the expected
E8 endpoint on COM4 (`8888:2001`) and TinyHawk endpoint on COM5
(`0483:5740`). Only the repository's previously reviewed, identity-gated
read helpers were used. No firmware was flashed or read out, no setting was
written, and no reset, binding, bootloader, CLI, arming, motor, RF transmit,
or fuzzing command was issued.

The immutable E8 Read Configuration request returned the expected 17-byte
response and confirmed the existing 150 Hz rate. The two unlabeled config
bytes remained `0x01` and `0x03`; their power/telemetry interpretations are
still tentative. The response also contained the private UID, which remains
redacted and stored only in an ignored `work/e8-session/` capture. This
endpoint still exposes no identified installed-firmware-version query. The
E8 was necessarily in its USB serial/update personality, so the successful
wired read is not evidence that this parser is wirelessly reachable and the
normal RF application is not expected to transmit concurrently.

The TinyHawk read-only MSP helper independently verified Betaflight 4.4.2,
board identifier `S411`, and craft name `TH III Plus`, then obtained six
stable sixteen-channel `MSP_RC` samples over three seconds. The first four
values were `[1500, 1500, 1500, 885]` while the E8 remained in serial mode.
This is a useful receiver-output/failsafe observation, not raw SX1280 FIFO
access and not proof of a serial exploit. The TinyHawk USB endpoint can remain
the ground-truth oracle during a later normally transmitting E8/SDR capture:
it can validate channel mapping, timing, and link-loss behavior without
opening or flashing the drone.

No source file changed. This append-only log is the only tracked change. The
practical next step is a synchronized passive run with the E8 booted normally,
TinyHawk COM5 channel/status observations started first, and the SDR recording
the over-air link. That can test decoded RC-to-channel correspondence and
failsafe transitions. The existing pinned 4.4.2 source review already traces
an ELRS MSP-data packet through `processRFMspPacket()` and the CRSF/MSP
reassembler into the same `mspFcProcessCommand()` dispatcher used by USB.
That makes serial a valid application-layer oracle for an OTA canary, but does
not solve radio delivery. The remaining proof is a correctly encoded,
CRC-valid, synchronized SX1280 long-interleaved transmission accepted by the
live receiver. Ordinary USB MSP access alone remains physical access, not a
game-day wireless vector.

---

## 39. Offline ELRS MSP canary forward encoder (Codex, 2026-10-16)

Starting revision `7524625`; `git pull --ff-only` reported current. Following
the owner's direction to pursue serial-validated commands delivered over the
air, implemented `tools/elrs-li-forward.py`. It performs no device or SDR I/O.
The tool builds a zero-payload, read-only MSPv1 `MSP_API_VERSION` request in
the exact CRSF/MSP buffer form consumed by Betaflight 4.4.2, fragments it into
the source-defined stubborn-uplink package order `1`, `2`, final `0`, wraps
each five-byte fragment in a CRC14-valid standard ELRS v3 MSP packet, and
encodes each eight-byte packet into the live SF7/4-8-LI mode's 21 canonical
payload symbols.

The forward long-interleaver is not supported only by a circular synthetic
round trip: it reproduces all symbols in both independent 13-byte TX16S
off-air vectors published with the empirically measured WaveShark map. Tests
also match Betaflight's independent `crsfPidRequest` fixture, verify all three
canary packets through the maintained v3 CRC checker, recover arbitrary
eight-byte packets with zero FEC corrections, and recover all payload symbols
from an ideal preamble/sync/delimiter/CSS waveform. The ideal waveform test is
integration evidence, not a hardware-radio oracle.

Generated a live-UID-derived canary plan privately under ignored
`work/e8-session/msp-api-version-canary.json`. The default CLI omits packet
bytes, symbols, UID, and derived CRC values; private output is restricted to
the ignored `work/` tree. Also generated three bounded, unscheduled
complex-float32 IQ fixtures under ignored `work/`, each 23,840 samples at
4.0625 MS/s and amplitude 0.25, with SHA-256 values in a private manifest
that explicitly records `transmit_enabled: false`. UHD's file-transmit and
TX/RX-loopback examples are installed locally.

The B205mini cannot be opened by separate RX and TX processes at once, so the
first file-transmit attempt failed before transmission while the receiver
owned the device. A private, ignored one-process UHD helper then made three
bounded bench attempts. Each sent only the first fragment of a synthetic-UID
canary at 2482 MHz, above the pair's highest 2479.4 MHz hop, with TX gain set
to the device minimum. One synthetic fragment cannot complete an MSP request
and cannot pass the live link's UID-derived CRC. Correcting the helper to pull
RX concurrently and increasing only RX gain still produced receiver noise
and short unrelated transients, not a recoverable 5.87 ms frame. The normal
extractor found no 12-symbol preamble or valid PHY candidate. This leaves the
SDR RF path gated on confirmed antenna/port coupling; a UHD sent-sample count
is not treated as waveform validation. No live-UID frame was transmitted.

COM5 was then opened only by the existing bounded read-only MSP observer with
the propellers removed. It reverified `BTFL` 4.4.2, `S411`, and `TH III Plus`
and returned fifteen stable 16-channel samples over three seconds. The
accepted `MSP_API_VERSION` payload was `00 01 2d`, establishing the exact
application-layer response expected from the OTA canary. No setting, motor,
binding, bootloader, reset, or firmware operation was performed.

Implemented `tools/elrs-v3-canary-schedule.py` and synthetic tests. The tool
combines a private canary plan with the latest CRC-valid decoded SYNC frame,
advances nonce and the four-packet FHSS state, and assigns the three fragments
to future frequencies. Output is restricted to ignored `work/`, marked
`transmit_enabled: false`, and explicitly capture-relative/fresh-SYNC-only.
On the two private capture sets, all **14/14** adjacent decoded SYNC pairs
matched the predicted 150 Hz nonce and FHSS transitions; maximum timestamp
residual was 521.8 microseconds. The resulting ignored private plan uses a
16-slot guard and records that this link advertised no telemetry.

No target accepted an RF canary and no target state changed. The absolute
no-opening/no-flashing limits remain in force for both devices.

Detailed evidence and remaining gates are in
`research/elrs-msp-api-canary-forward-path-2026-10-16.md`. The older packet
forger's single-fragment MSP output and the existing SDR emulator's prototype
modulator are not this validated path and must not be used for live testing.
Changed tracked files: the forward encoder and tests, the capture-relative
scheduler and tests, the new research note, the MSP motor-path cross-link, and
this append-only log. Final test and tracked-file counts are recorded after
the validation run. Next: resolve the B205mini RF coupling/port arrangement,
then turn the replay-validated planner into a fresh-SYNC receive clock before
any live-UID canary is considered.

Important observation rule: because decoded SYNC advertises no telemetry,
COM5 cannot show whether an over-air `MSP_API_VERSION` request was accepted;
it only establishes the expected application response. The next observable
canary should therefore be a separately validated one-slot replay of an
already captured low-throttle, disarmed hybrid-RC frame while COM5 watches the
corresponding stick value. Do not substitute a setting or motor write merely
to make the result visible.

Final validation: **115 tests passed**; `tools/validate-project.py` passed
syntax, JSON, corpus hashes/provenance, rainbow data, CVE coverage, and local
links across **182 tracked files**; `git diff --check` passed.

Handoff status: encoder/scheduler/research commit `07343ad` was pushed to
`origin/main`. This status line is being committed and pushed separately;
all UID-derived plans, hardware captures, and local UHD helpers remain ignored.

---

## 40. Stale-safe ELRS fresh-SYNC receive clock (Codex, 2026-10-16)

Starting revision `2bfc7d1`; `git pull --ff-only` reported current. Continued
the receive-clock half of the ELRS canary split while Claude handles the
offline safety validator for an observable RC replay canary. No COM port was
opened. Both devices remained under the absolute no-opening/no-flashing rule,
and no RF signal was transmitted.

Added `tools/elrs-v3-fresh-sync-clock.py`. It reuses the independently tested
v3 SYNC/FHSS parser, fits one packet period while permitting each capture its
own time origin, validates the observed nonce and hop transitions, requires
an explicit anchor source for mixed captures, and rejects future, stale, or
over-horizon schedules. It converts the three previously validated read-only
canary fragments to exact future capture sample indices and optional UHD
device times. The artifact remains private, has no radio backend, and records
`transmit_enabled: false`.

Offline replay against the two existing private decoded-capture sets checked
all **14** SYNC transitions, fit **150.013248 packets/second**, accepted a
50 ms-old anchor, and generated three future slots. UID-derived data and the
result stayed under ignored `work/e8-session/` and were not printed.

Added `tools/uhd-elrs-sync-rx.c`, a bounded native UHD receive helper needed
because the installed generic capture example does not preserve first-sample
hardware time. The helper has no transmit streamer or TX call, fixes the
benchmark to 4.0625 MS/s and the measured 2441.3788 MHz SYNC channel, limits
duration to 0.05--5 seconds, refuses overwrite, and aborts on RX metadata
errors. It writes a receive-only manifest containing actual radio settings,
sample count, and the first sample's device time. The Python clock tool now
independently validates that manifest and can derive its sample rate, capture
cursor, and absolute time origin from it.

The helper compiled cleanly against the installed UHD 4.11 library and ran on
the connected B205mini (`33E8308`) for 0.25 seconds. It returned exactly
**1,015,625** complex-float samples (**8,125,000 bytes**) with a valid UHD
first-sample timestamp. The normal receive-only PHY scanner found zero
candidates on the measured channel. The E8 was still believed to be in its
USB serial/update personality, so the recording is treated as a successful
receive-transport test and negative control, not as a decoder failure. The
capture, manifest, executable, and scan output all remain ignored under
`work/`.

Added synthetic clock/manifest tests and static safety tests that fail if the
native helper acquires a UHD TX API or loses its duration/non-overwrite
guards. Detailed evidence and the remaining real-time limitation are in
`research/elrs-msp-api-canary-forward-path-2026-10-16.md`. Next, boot the E8
normally and take a short timestamped receive-only capture; a CRC-valid SYNC
from that same stream must pass the default 500 ms freshness gate. A live
transport remains disallowed until the independently reviewed observable RC
canary and a real-time decoder/clock path meet at that gate.

Final validation: **123 tests passed**; `tools/validate-project.py` passed
syntax, JSON, corpus hashes/provenance, rainbow data, CVE coverage, and local
links across **186 tracked files**; `git diff --check` passed. Changed tracked
files: the fresh-SYNC clock and tests, the native receive-only UHD helper and
static safety tests, the canary research note, and this append-only log.

Handoff status: implementation/research commit `b2b9634` was pushed to
`origin/main`. All captures, manifests, binaries, UID-derived plans, and scan
outputs remain ignored under `work/`; only source, tests, and redacted notes
were published.

---

## 41. Fail-closed hybrid-RC replay canary review (Codex, 2026-10-17)

Continued PR #36 after Claude reached its usage limit. The original branch
was based on `2bfc7d1`, so current `main` was merged into it before the final
combined validation. No device, serial port, SDR, RF transmitter, or firmware
was accessed during this work.

The RC selector now treats every control bound as mandatory. It rejects roll
outside a 50--300 microsecond deviation window, pitch/yaw beyond 50
microseconds from center, throttle above 1050, and any attempt to loosen the
throttle ceiling. There is no fallback to a more extreme frame. A saved plan
is independently decoded and checked again: UID-derived CRC, encoded symbols,
RC channels, PHY parameters, and every safety field must all match rather
than trusting serialized booleans.

Rotating AUX safety is now tied to live configuration evidence rather than a
default-value assumption. The read-only TinyHawk observer can optionally
capture `MSP_MODE_RANGES`, `MSP_MODE_RANGES_EXTRA`, and
`MSP_ADJUSTMENT_RANGES` after its existing Betaflight 4.4.2 / `S411` /
`TH III Plus` identity gate. The private snapshot must prove that AUX1-low is
disarm and that the selected rotating AUX channel controls no usable mode or
adjustment range. Wrong identity, truncated arrays, invalid ARM mappings, and
configured rotating channels all fail closed. Snapshot and canary output are
restricted to ignored `work/`, refuse overwrite, and remain
`transmit_enabled: false`.

The fresh-SYNC integration contract still requires hybrid switch mode, zero
telemetry ratio, and a newly decoded SYNC. PR #36 deliberately does not add a
transmitter or make its one-frame schema directly consumable by the existing
three-fragment MSP scheduler. Detailed behavior and remaining integration
work are documented in
`research/elrs-v3-hybrid-rc-replay-canary-2026-10-17.md`.

Final combined validation: **188 tests passed**; project validation passed
syntax, JSON, corpus hashes/provenance, rainbow data, CVE coverage, and local
links across **189 tracked files**; `git diff --check` passed.

---

## 42. RC replay/fresh-SYNC integration and capture verdict (Codex, 2026-10-17)

Merged PR #36 into `main` as `97d25fa`, then completed the integration left
for this session. The fresh-SYNC clock now accepts either the original
three-fragment read-only MSP canary or one independently revalidated hybrid-RC
replay frame. RC input requires the private TinyHawk AUX-safety snapshot and a
fresh SYNC proving hybrid switch mode with no telemetry. It produces exactly
one future slot, remains capture-relative, has no radio backend, and records
`transmit_enabled: false`. Existing MSP consumers retain the original v1
`command` field; RC output uses the generalized `canary` descriptor.

With propellers removed and exclusive COM5 access, the bounded read-only MSP
observer reverified Betaflight 4.4.2, board `S411`, and craft `TH III Plus`,
then captured mode and adjustment ranges beneath ignored `work/`. It made no
setting, motor, bind, reset, bootloader, or firmware request. Both devices
remain under the absolute no-opening/no-flashing rule.

The two published ranked-symbol datasets were decoded again with private
packet bytes enabled. The decoder resolved **54 CRC-valid frames**: **38 RC**
and **16 SYNC**, with eight unresolved rows. The replay selector correctly
refused all RC candidates. Its privacy-safe first-failure summary was
**16 armed** and **22 throttle-too-high**; the latter all decoded to throttle
1507. Betaflight 4.4.2 source independently confirms the analog unpacking rule
(`988 + raw 10-bit value`), so the gate was not relaxed and no frame was
invented. The selector now prints these aggregate rejection counts itself.
UID-derived bytes, the live AUX snapshot, and regenerated private JSONL remain
ignored under `work/`.

No RF signal was transmitted and no target accepted a canary. The next safe
bench input is a receive-only capture made while the real controller is
visibly disarmed, throttle is low, pitch/yaw are centered, and roll is held
50--300 microseconds off center. That capture can flow through the completed
selector and fresh-SYNC clock without code changes.

Final validation: **191 tests passed**; `tools/validate-project.py` passed
syntax, JSON, corpus hashes/provenance, rainbow data, CVE coverage, and local
links across **189 tracked files**; `git diff --check` passed.

---

## 43. Capture-derived RC-map correction and fail-closed proof (Codex, 2026-10-18)

Follow-up analysis of the committed Oct 15 and Oct 16 captures established
that this E8/TinyHawk pair maps OTA analog slots `[1,2,3,0]` to MSP
`[roll,pitch,throttle,yaw]`. The initial RC replay canary incorrectly treated
the four OTA slots as direct roll/pitch/yaw/throttle order. That did not alter
the prior no-replay verdict because throttle remains OTA slot 3 at 1507, but
it did make the reported roll and yaw wrong.

Corrected `decode_hybrid_rc()` to apply the verified target mapping and added
a capture-derived regression vector. The Oct 16 public summary now decodes as
roll 1437--1535, pitch 1497, throttle 1507, yaw 988; the Oct 15 summary decodes
as roll 1480, pitch 1497, throttle 1507, yaw 2011. The held-out USB/RF overlap
contains five CRC-valid matches within 120 ms and mean absolute channel error
`[13.6,0,0,0]`, with only the rapidly moving roll channel affected by timing.

Because Betaflight channel order is configurable, the canary no longer trusts
the mapping as a code constant alone. The read-only observer now requests
`MSP_RX_MAP` (64), validates an eight-channel permutation, and records it in
the new private `betaflight-aux-safety-v2` schema. The selector requires the
exact observed `[1,2,3,0,4,5,6,7]` map before it interprets throttle, yaw, or
AUX safety. Missing, malformed, or different mappings fail closed, and the
saved plan validator rechecks the serialized mapping evidence.
The replay-plan schema was therefore advanced from v1 to
`elrs-v3-rc-replay-canary-v2`; stale v1 plans are rejected instead of being
silently reinterpreted under the corrected channel order.

COM5 was opened once by the bounded read-only observer. It reverified
Betaflight 4.4.2 / `S411` / `TH III Plus`, captured the expected map and AUX
ranges to ignored `work/`, and made no setting, motor, bind, reset, bootloader,
or firmware request. Re-running all 38 private RC frames with the v2 evidence
produced the same safe rejection: **16 armed** and **22 throttle-too-high**.
No RF signal was transmitted and no target state was changed. Both devices
remain under the absolute no-opening/no-flashing rule.

Final validation: **194 tests passed**; `tools/validate-project.py` passed
syntax, JSON, corpus hashes/provenance, rainbow data, CVE coverage, and local
links across **189 tracked files**; `git diff --check` passed.

---

## 44. Bounded ELRS v3 injection candidate and corrected target mapping (Codex, 2026-10-18)

Started from `2db437d` with local `main` and `origin/main` at parity. The goal
was to turn the decoded E8/TinyHawk traffic and pinned Betaflight 4.4.2 source
into the smallest defensible wireless exploit candidate, while preserving the
absolute no-opening/no-flashing rule for both devices. No RF transmission,
configuration write, reset, binding, bootloader action, firmware readout, or
motor command occurred during this work.

The strongest supported path is receiver-side hybrid-RC injection, not an
SX1280 memory-corruption exploit. In Betaflight 4.4.2, a connected,
model-matched ELRS v3 receiver accepts a standard RC packet after the static
UID-derived CRC14 check. The hybrid-RC CRC does not incorporate the receiver
nonce or a separate freshness value. The nonce and four-slot hop cadence
therefore govern delivery timing, but do not authenticate a new RC value.
The private UID already known for this owned pair supplies the CRC initializer
and FHSS sequence, while a fresh decoded SYNC supplies the live nonce, FHSS
index, switch mode, telemetry ratio, and air-rate code. One RC packet cannot
establish a disconnected session, so the first proof still has to arrive in a
valid slot while the receiver remains connected and model-matched.

This analysis also corrects section 43 above without rewriting append-only
history. Betaflight's logical `MSP_RC` order is
`[roll,pitch,yaw,throttle]`, and the target's observed `MSP_RX_MAP` is
`[1,2,3,0,4,5,6,7]`. The target's four raw OTA analog slots are therefore
`[throttle,roll,pitch,yaw]`, not the order stated in section 43. The Oct 16
roll-motion capture is raw `[988,1437--1535,1497,1507]`, which means low
throttle, moving roll, centered pitch, and centered yaw. The Oct 15 idle
capture is raw `[2011,1480,1497,1507]`, which means high throttle with the
other three axes near their observed positions. Reprocessing all 38 private
RC frames still produces a safe negative replay result: 16 fail as armed and
22 fail as throttle-high, now for the correct channel reason.

Added `tools/elrs-v3-bounded-rc-injection.py`. It creates exactly one private,
transmit-disabled hybrid-RC candidate with raw OTA values
`[988,1400 or 1600,1500,1500]`: minimum valid throttle, a small left or right
roll observation, centered pitch/yaw, and AUX1 low. It selects only an unused
three-bit rotating AUX and preserves the repeatedly observed live value
exactly. The packet uses the private UID-derived v3 CRC14 and the maintained
SX1280 SF7 4/8 long-interleaver implementation. The symbol round trip is a
consistency check only; it is not claimed as independent SX1280 RF acceptance.
Output is restricted to ignored `work/`, refuses overwrite, explicitly says
`transmit_enabled: false`, and the tool has no serial, SDR, or radio backend.

The read-only COM5 observer now records three timestamped `MSP_RC` samples in
the private `betaflight-aux-safety-v3` schema after the existing Betaflight
4.4.2 / `S411` / `TH III Plus` identity gate. Its live run reverified the
identity and exact RX map, but the receiver was in failsafe and reported
logical throttle 885, below the valid OTA minimum. The bounded builder refused
that snapshot as intended. It made only read-only MSP requests and did not
change device state.

The complete fail-closed gate now requires all of the following:

- the exact board, craft, and eight-channel RX map;
- a live ARM range proving AUX1-low is disarmed;
- three or more equal-width RC samples with AUX1 low, logical throttle
  988--1050, and pitch/yaw within 50 microseconds of center;
- strictly increasing wall-clock timestamps spanning at most one second,
  with the newest sample less than five seconds old and not future-dated;
- a stable, exactly encodable rotating AUX that controls no configured mode
  or adjustment;
- full reconstruction of all eight expected packet bytes, explicit ACK-low,
  CRC14 recomputation, channel decode, and symbol recomputation; and
- a fresh CRC-valid SYNC reporting hybrid mode, telemetry ratio zero, and
  target rate index 7 (LoRa 150 Hz).

`tools/elrs-v3-fresh-sync-clock.py` accepts the new one-frame schema only after
rechecking those invariants and the current safely disarmed live state. It
then emits exactly one future slot and remains a transmit-disabled timing
contract. Regression tests cover stale, future, duplicate/reversed, and
overlong evidence; unsafe current state; wrong mapping; altered construction;
and an ACK-bit mutation with CRC and symbols recomputed. The last case is
rejected because validation reconstructs the complete expected packet instead
of trusting decoded channels alone.

Corrected the legacy ELRS chain's preview order to place minimum throttle in
raw OTA slot zero and added a regression test. This does not validate or
authorize that older chain as a live transport. It only prevents future work
from carrying the former throttle/yaw labeling error forward.

Two private plans synthesized before the channel-order correction were
deleted immediately. They were never transmitted or committed and can only be
regenerated after a new safe live snapshot. UID material, private decoded
packets, live AUX configuration, and generated plans remain ignored beneath
`work/`.

Parallel source review ranked the remaining paths as follows. A CRC-valid v3
type-3 TLM frame may refresh RX activity while Betaflight reuses retained RC
data, creating a plausible stale-control/failsafe-suppression primitive; it
would require a sustained approximately 37.5-packet/second hopping transport
and is riskier than one bounded frame. Replayed or forged SYNC is a plausible
desynchronization/DoS vector, but changing switch mode can request a persistent
configuration save and is excluded. MSP-over-ELRS reaches the ordinary MSP
dispatcher after connection, but only the read-only `MSP_API_VERSION` canary
is suitable as a first probe. Semtech's continuous-RX BUSY-lock erratum is a
low-confidence practical path at this measured PHY/rate, and no credible
RF-to-SPI injection, malformed-length memory corruption, or controller-side
E8 parser exploit was found.

The remaining blocker is RF transport validation: B205 IQ polarity, waveform
acceptance, exact timing, retune behavior, and minimum safe gain are not yet
independently calibrated against this SX1280 receiver. A later one-frame test
still requires a separate sign-off naming the exact target, revision, RF
power, count, duration, stop conditions, and recovery. Propellers must remain
removed; COM5 must record a new normal, disarmed, low-throttle snapshot; the
same receive stream must yield a fresh valid SYNC; and any unexpected channel,
AUX, arm, motor, USB, timing, or calibration state must abort before transmit.

Independent safety review found and closed three fail-open conditions before
publication: stale live evidence, a CRC-correct altered ACK bit, and a SYNC
with the wrong or missing air-rate index. The final audit reported no remaining
actionable finding. Final validation: **211 tests passed**;
`tools/validate-project.py` passed syntax, JSON, corpus hashes/provenance,
rainbow data, CVE coverage, and local links across **193 tracked files**;
`git diff --check` passed.

Handoff status: implementation and research are in commit `66ff149` on
`main`. This append-only record is the second commit prepared for the same
`origin/main` publication. No live-UID RF frame was transmitted.

---

## 45. Wireless-only alternative-path audit and rate-switch latch model (Codex, 2026-10-18)

Started from `edca243` with local `main` and `origin/main` at parity. The owner
requested deep research into wireless delivery paths while restating the
absolute no-flashing rule. This pass was source/offline only: no serial port,
SDR, RF transmission, device reset, configuration write, opening, bootloader,
firmware readout, or flash action occurred on either the E8 or TinyHawk.

The strongest new path is a Betaflight 4.4.2 **forced rate-switch latch**. A
CRC-valid, identity-matching v3 SYNC can advertise another supported 2.4 GHz
air rate. `handleConnectionStateUpdate()` then calls `lostConnection()`, stops
the timer, and retunes to that rate's fixed initial/sync channel. A normal
successful connection previously set `lockRFmode=true`; loss does not clear
it, and rate cycling requires it to be false. The receiver can therefore stay
disconnected on the wrong modulation and common channel until receiver
reinitialization or a correctly modulated sustained stream re-establishes
phase/LQ lock. Preserving the observed hybrid switch mode avoids requesting a
configuration save, so this is a transient runtime state path rather than a
flash/configuration write.

The existing stale-RC finding compounds the latch. At the fixed channel, a
CRC-valid v3 type-3 TLM packet is still reported as new SPI RX data without
copying RC values. More importantly, the outer handler also reports DATA for
a CRC-valid SYNC that fails the later UID3/UID4 comparison. Either can make the
generic RX layer decode the retained RC payload again while ELRS itself remains
disconnected. This supports a source-level failsafe/control-latch candidate,
not arbitrary control, and is hazardous if the retained frame is armed or has
nonzero throttle. It was not tested live.

Added `tools/elrs-v3-rate-switch-latch-model.py`, a packet-free Python state
model with no UID input, file write, serial, SDR, or transmit backend. It checks
the four source rate mappings, forced disconnect and retune, persistence of
`lockRFmode`, the no-config-write invariant when switch mode is preserved,
CRC rejection, post-CRC identity rejection, and type-3 retained-payload reuse.
Its output explicitly records `transmit_enabled: false` and
`live_validated: false`. Thirteen synthetic tests pass. A compiled host C harness
around the pinned Betaflight functions remains the stronger next proof.

The passive bootstrap was tightened. In the 4.4.2 v3 format, SYNC carries UID
bytes 3--5. With the live target's model ID 255, the model XOR is zero, so one
decoded SYNC reveals the CRC material and three of the four FHSS-seed bytes.
Only UID byte 2 remains as a 256-candidate offline search; UID bytes 0 and 1 do
not participate in this implementation's air-link CRC, FHSS seed, or SYNC
identity comparison. This corrects older project wording that described only
two visible UID bytes. No private UID was published.

Other ranked conclusions:

- MSP-over-ELRS is a general Betaflight command plane once connected and
  model-matched. The read-only `MSP_API_VERSION` canary remains the only
  suitable first proof. Model-ID and other write commands are excluded.
- SYNC switch-mode changes can request a persistent configuration save; bind
  takeover and model-match rewrites are also persistent and remain excluded.
- The E8 presently advertises telemetry off. Official v3 transmitter logic
  opens no SX1280 telemetry receive window in that state, so there is no
  demonstrated normal-mode wireless controller parser surface. Enabling it
  would require a configuration write and is not part of this pass.
- The upstream v3.6.3 zero-length CRSF parser fix is an offline E8 firmware
  lead only; exact E8 firmware and reachability remain unknown.
- Same-PHY fixed-length LoRa traffic can reach SX1280 RX_DONE before the
  application CRC check and can starve slots, but this is interference/DoS,
  not control or code execution.
- Betaflight PR #15694's headline deferred-hop race does not apply unchanged
  to 4.4.2. The pinned driver does have a narrower lost-hop path if BUSY stays
  asserted until the periodic Tick observes more than one millisecond of
  processing age, plus an unconfirmed non-atomic double-hop window. Neither
  has a demonstrated RF trigger at the observed SF7/BW812.5 kHz mode.
- No OTA-controlled FIFO length, RF-to-SPI opcode path, or credible memory
  overwrite was found in the fixed eight-byte receiver or bounded MSP
  reassembler.

Detailed evidence, version limits, official-source links, and the revised
ranking are in
`research/elrs-v3-wireless-alternative-paths-2026-10-18.md`. Final validation:
**224 tests passed**; `tools/validate-project.py` passed syntax, JSON, corpus
hashes/provenance, rainbow data, CVE coverage, and local links across **196
tracked files**; `git diff --check` passed.

Changed tracked files are the alternative-path research note, the offline
rate-switch state model, its thirteen-test suite, and this append-only log. The
next safe steps are a C host harness for the exact forced-rate/stale-frame
chain and a private passive UID-byte-2 candidate scorer. Any later live RF
test still requires a new bounded bench plan with props removed, a verified
disarmed/minimum-throttle retained frame, strict packet/power/count limits,
serial observation, stop conditions, and power-cycle recovery. Neither device
may ever be flashed.

---

## 46. Wireless-path findings publication and final audit (Codex, 2026-10-18)

Published the wireless-only alternative-path audit to `origin/main` in commit
`5553146f80f8ca31a559cf7fbeb6f6dcf8676af9`. The detailed evidence remains in
`research/elrs-v3-wireless-alternative-paths-2026-10-18.md`; this entry records
the final reviewed conclusions and corrections.

The leading candidate is a Betaflight 4.4.2 ELRS v3 rate-switch latch. After a
legitimate connection sets `lockRFmode`, an identity-matching, CRC-valid SYNC
that selects another supported rate causes forced loss, stops the ELRS timer,
and retunes the active SX1280 modulation to the common initial/sync channel.
`lostConnection()` does not clear `lockRFmode`, so the normal disconnected
rate-cycling path remains disabled. This supports a targeted wireless denial
hypothesis, but it remains source-derived: no crafted SDR waveform has been
accepted by the live receiver and no live impact has been demonstrated.

The final audit corrected an important state distinction. Betaflight keeps
`receiver.rateIndex`, used as the configured/search-rate fallback, separate
from the active `receiver.modParams->index`. A forced retune changes the active
rate without updating `receiver.rateIndex`; consequently, a later accepted
reserved rate code can fall back to the old search rate and retune the active
PHY again. The offline model now represents both fields and includes a
post-latch reserved-code regression. Its tests are an executable,
regression-checked interpretation of the source, not independent proof of the
firmware's behavior.

Two fixed-channel continuation carriers remain source-plausible. A CRC-valid
v3 type-3 TLM frame, or a CRC-valid SYNC rejected only by the later identity
check, can still be reported as new generic SPI RX data without replacing the
retained RC payload. The generic RX layer may therefore present prior control
values as fresh. This is a possible stale-control/failsafe-suppression chain,
not arbitrary control, and it is unsafe to test live if the retained state is
armed or above minimum throttle.

Passive bootstrap does not require a binding phrase for this model-matched-off
target. A decoded v3 SYNC exposes UID bytes 3--5; those disclose the CRC14
material and three of the four FHSS-seed bytes used by this Betaflight version.
Only UID byte 2 remains as a 256-candidate private offline search. UID bytes 0
and 1 do not participate in the reviewed air-link CRC, FHSS seed, or SYNC
comparison. No private UID or recovered identity material was committed.

Other reviewed paths remain secondary. MSP-over-ELRS reaches the ordinary MSP
dispatcher after connection and model match, but a read-only `MSP_API_VERSION`
request remains the only suitable first canary. Observed SYNCs advertise
telemetry off; assuming the E8 follows upstream v3 scheduling, it opens no
normal telemetry receive/parser window in those epochs. SX1280 same-PHY packet
starvation and narrow BUSY/FHSS timing races remain denial leads without a
demonstrated trigger. No credible OTA-controlled FIFO length, RF-to-SPI opcode
path, or bounded-parser memory overwrite was found.

Final validation for the published changes was 224 passing tests,
`tools/validate-project.py` passing all checks across 196 tracked files, and a
clean `git diff --check`. Two independent final reviews reported no remaining
actionable issues. The entire pass was offline/read-only: no RF transmission,
serial or configuration write,
reset, opening, firmware readout, bootloader action, update, or flashing took
place. The absolute no-flashing rule remains in force for both the E8 and the
TinyHawk.

---

## 47. Common wireless-subsystem RF/RCE survey (Codex, 2026-10-19)

Started from `6f03efc` on `main`; pulled `origin/main` immediately before the
edit and it was current. The owner clarified that “chipset” means the complete
wireless-interface subsystem, including proprietary radio processors such as
DJI OcuSync P1/S1/S2, and that RF delivery is the objective rather than an
excluded route. This pass therefore ranked only chains with a concrete
waveform-to-parser story and treated UART-only findings as secondary unless a
radio bridge is established.

The main new finding is a memory-corruption candidate on a source-derived
direct-RF path in current
ExpressLRS Backpack source (release 1.5.9 at `437a4f4`; reviewed master
`d15ed30`). The ESP-NOW callbacks in the TX and VRX applications parse received
bytes through a stack-local MSP object. The MSPv2 parser accepts a 16-bit
payload length and writes bytes into a 64-byte payload array without an
oversize rejection; checksum and source-MAC validation both occur only after
the payload writes. An ESP-NOW v1 body can carry a 241-byte MSPv2 payload,
yielding 177 controlled writes beyond the array.

An ignored, offline WSL harness compiled the unmodified parser and CRC sources
with GCC 13.3.0 plus ASan/UBSan. It confirmed the first invalid access at
`msp.cpp:102`, overwrite past the containing native object, continued writes
with a bad checksum, and full parsing before rejection of a wrong source MAC.
The host object layout is not an ESP target layout. This confirms offline
memory corruption; ESP-NOW reachability follows from the callback source but
has not been demonstrated OTA. It is **not RCE**: no exact ESP8266, Xtensa
ESP32, or RISC-V ESP32-C3 target has demonstrated control-bearing stack data or
a controlled program counter. No transmitter or ready-to-send RF frame was
added. The main
ExpressLRS repository already fixed the same parser class in commit `1ade76d`,
but the bound is absent from the reviewed Backpack files.

Other high-priority paths were recorded. ExpressLRS PR #3825 fixes unchecked
CRSF parameter chunk/index accesses reachable through a valid ELRS DATA uplink
on standalone receivers; the path plausibly supplies an out-of-bounds stack
write/read after link access. mavlink-router issues #487/#488 document larger
object/stack overwrites reachable when an injectable wireless MAVLink bridge
feeds an enabled logging endpoint. PX4 CVE-2026-32743 is a MAVLink log-handler
stack overflow reachable where MAVLink is exposed over Wi-Fi or telemetry,
with published DoS rather than RCE impact. INAV 9.1 issue #11776 is an
ASan-confirmed four-byte `MSP_DATAFLASH_READ` response overrun potentially
reachable through MSP-over-CRSF. Betaflight PR #15254 fixed an ASan-confirmed
serial-CRSF length over-read relevant to standalone UART receivers but not the
TinyHawk's integrated SPI-ELRS path. A CYRF6936/Spektrum receive-count ordering
concern is retained only as a hardware-model hypothesis pending FIFO behavior
validation.

The DJI review treats P1/S1/S2 as radio/baseband subsystems with their own CPU,
RTOS/Linux environment, and host interface. The NDSS DroneID/DUML work provides
primary evidence for S1 architecture, S1/P1 product mapping, recoverable
artifacts in some generations, and OTA DUML reachability through a legitimate
controlled RC, but
public naming/mapping—especially S2—is inconsistent. No current arbitrary-
outsider OcuSync RF memory-corruption primitive was found. The defined route is
exact update-artifact acquisition, offline extraction, CPU/RTOS and ingress
task mapping, parser/version diffing, and host/emulated fuzzing before solving
the proprietary PHY/authentication delivery layer. DroneID decoding is not
treated as proof of C2 injection.

ESP-Drone/CRTP and OpenIPC/wfb-ng were ranked as open end-to-end Wi-Fi/raw-
802.11 fuzz targets. Walksnail update images are publicly downloadable and can
be deeply extracted, making its proprietary baseband/Linux services another
artifact target; the common Artosyn chip mapping remains teardown evidence.
HDZero's open 8051 VTX parser concerns remain UART-adjacent rather than direct
RF.

Detailed evidence, applicability limits, source links, ROP/JOP proof criteria,
and the four-track work program are in
`research/common-drone-wireless-subsystem-rce-survey-2026-10-19.md`. The
finding does not identify the E8 firmware and does not apply automatically to
the TinyHawk. This pass performed no RF transmission, serial/device access,
configuration write, reset, opening, firmware extraction from a live device,
bootloader action, or flash. The absolute no-flashing rule remains in force for
both owned devices.

Validation: `tools/validate-project.py` passed syntax, JSON, corpus hashes and
provenance, rainbow data, CVE coverage, and local-link checks across 197 files;
`git diff --check` passed. Standard-library discovery ran all 224 tests: 223
passed and the pre-existing, unrelated
`test_waveform_recovers_encoded_symbols` failed because the extractor returned
20 of 21 synthetic symbols. The single test failed identically when rerun and
none of the code or test files involved differs in this documentation-only
change. The WSL environment does not have pytest installed, so the equivalent
unittest runner was used.

---

## 48. Additional aircraft, controller, telemetry, and video target sweep (Codex, 2026-10-19)

Started from `5d4e36b` on `main` and pulled `origin/main` before editing; the
branch was current. The objective was to continue beyond the TinyHawk/E8 pair
and identify source-verifiable wireless parser or control-plane defects in
other aircraft, controllers, telemetry radios, video links, and Remote ID
implementations. This was an offline source/public-artifact review only. It did
not access a device or serial port, transmit RF, join a target network, change
configuration, reset hardware, enter a bootloader, extract live firmware, or
flash anything. The absolute no-flashing rule for the E8 and TinyHawk remains
in force.

The review pinned and inspected ignored working copies of Crazyflie flight
firmware (`9912584c85db92dbbba14334ffed20ebabba463e` and release source
`54f31e243a0b28b67efef5ba20dbb6d9890a5478`), Crazyflie nRF firmware
(`36f284538af8254bc227ea93cd7aaa71c4dfa630`, stable `2026.04` at
`51f900b0140c20196a0ccf647b279ee342d28e15`, and `2026.08`-tagged source at
`884071f9c87978ef75a7c7ec34d46360405662cc`), EdgeTX main/v2.12.4/v2.11.8,
mLRS master `a61aa83f610283b3f329170060adb899eef509e3`, and ESP-Drone master
`db0f6562e4f67cccee3acac4dff39c9aaea4e5fa`. The source trees and temporary
fixtures remain ignored under `work/` and were not added to the repository.

Five principal findings were retained with explicit limits:

1. Current Crazyflie 2.x nRF51 BLE CRTPUP reassembly accepts two 20-byte GATT
   writes whose 19-byte data portions overflow a 32-byte member buffer by six
   bytes. An offline freestanding 32-bit layout fixture placed
   `packet_buffer`, `packet_length`, `packet_index`, and `data_handler` at
   offsets 31, 63, 64, and 68 in a 76-byte object. The overwrite therefore
   reaches parser state, padding, and only the low byte of the callback on that
   reviewed layout. The immediate callback creates a credible control-flow
   research candidate, but no target-build crash or RCE was demonstrated.
2. Crazyflie STM32 CRTP Memory handling passes an attacker-selected one-byte
   read length to the always-present virtual memory tester with only 24 bytes
   remaining in the global packet. A length of 255 writes 231 bytes beyond the
   object with an address-derived incrementing sequence and wraps the response
   size to 5, which passes the later size assertion. Exact overwritten globals
   and control-flow impact require the release ELF/map; arbitrary data and RCE
   are not claimed.
3. Current mLRS MAVLinkX expands compressed data into 300-byte interface
   buffers before MAVLink CRC/signature validation. Run-length and literal
   writes lack effective production capacity checks. This is a source-derived
   RF-fed memory-corruption path; embedded reproduction and code execution are
   unproven.
4. EdgeTX main, v2.12.4, and v2.11.8 retain an unchecked S.Port-to-Bluetooth
   byte-stuffing path. A retained 17-byte record followed by a 19-byte record
   produces a nominal index of 36 against a 33-byte array; the first out-of-
   bounds byte aliases `bufferIndex`, after which behavior is undefined. OTA
   reachability requires a Bluetooth-capable build, connected Bluetooth
   Telemetry mode, and a configured RF/module path that accepts the telemetry.
5. ESP-Drone UDP/2390 has two post-association DoS candidates: an empty
   datagram under-reads `rx_buffer[-1]` and requests a `SIZE_MAX` checksum read,
   while a one-byte zero-checksum datagram queues a zero-length record whose
   downstream subtraction reaches a fatal assertion. Neither is a write
   primitive or RCE.

Additional inventory targets were documented without promotion beyond their
evidence. The optional Crazyflie P2P DTR example can request a 255-byte copy
into a 59-byte local object, a 196-byte destination overrun plus source
over-read, but it is not in the stock application and only the available P2P
prefix is RF-controlled. SJRC F11 has a published physical root command-
injection chain after joining its open Wi-Fi network. OpenDroneID `core-c` v2.0
retains direct-NAN and MAVLink defects fixed only on master. Holy Stone/Fullhan,
HDZero/LIVE555, SiK remote AT, OpenHD wifibroadcast, Parrot, Tello, and toy-drone
families were ranked for exact-version acquisition or parser fuzzing. EdgeTX
Ghost `MENU_DESC` is a definite module-UART overwrite with an unclosed RF
bridge; CRSF `DEVICE_INFO` is an OOB-read candidate rather than a write.

An independent final review checked the overwrite arithmetic, layout claims,
release scope, and RF preconditions. Its corrections were incorporated:
object-relative indexing is not an arbitrary absolute write, the EdgeTX result
becomes undefined after its adjacent index byte is overwritten, ESP-Drone's
two paths are distinct, and no static finding is described as demonstrated
RCE. Operational packet sequences, payload bytes, gadget addresses, and live
RF demonstrations are intentionally omitted pending exact-build reproduction
and maintainer coordination.

The new detailed report is
`research/additional-wireless-target-findings-2026-10-19.md`. Validation after
review passed `tools/validate-project.py` across 198 files. Standard-library
discovery ran 224 tests: 223 passed, and the same pre-existing unrelated
`test_waveform_recovers_encoded_symbols` test failed because the extractor
returned 20 of 21 synthetic symbols. No code or test file is modified by this
documentation-only change. The remaining next steps are private exact-release
ELF/map builds, bounded host regressions, and coordinated disclosure before any
live reproduction.

---

## 49. TBS Crossfire air-link, controller, firmware, and CRSF audit (Codex, 2026-10-20)

Started from `4a25943f6380b26f7c00e5b75b2b5696f39da68d` on `main`;
pulled `origin/main` before editing and the branch was current. This pass
applied the same evidence-first wireless-subsystem review to TBS Crossfire:
separate the proprietary OTA link from CRSF serial routing, inventory public
controller/receiver/Wi-Fi artifacts, reproduce source-level memory-safety
candidates offline, and identify the missing links in a defensible RF-delivery
chain. It was offline and read-only with respect to hardware. No device,
serial port, device network service, or radio was accessed; no RF was
transmitted; no configuration, reset, bootloader action, live firmware
extraction, or flash occurred. Downloaded firmware was not executed. The
absolute no-flashing rule for the owned E8 and TinyHawk remains unchanged.

The review pinned public source for the TBS CRSF specification
(`4e5fe8824211165e091651403e1667e4f32af0d9`), TBS Agent Python
(`93e352ae46ef3019673b3836b6e25a68e1dbf208`), Crossfire reverse
engineering (`3ca28e5f4cdbd3aacf850610ffdfbf9cadcc3a0c`), and the ESP32
receive-side sniffer (`1d9e60ce3ff5c250a766c9fa2a0dcdd920c037ac`). Ignored
artifacts included the official catalog, SHA-256
`FB0811D966709C78FF5D4D1B33465E737585195A34B813B471EAC72FB15CF9DE`;
Nano RX v6.48,
`DD7B88D8C58E19DA718E114C244F2A64CC4F82DDD33CD9E96BA56E96F2CBF750`;
Tango II Crossfire radio v6.48,
`E37F7A9A849A8424E9E6D8DD4992E542655621AF670B7BA62734DA5A851961B5`;
and the TBS Wi-Fi recovery archive,
`6BED2EB77CCFE9FEF59F1A742CD3DE36DBF387819A912DCEB35317670842394F`.
Additional source and artifact details, plus all source trees, binaries,
fixtures, and private coordinates, remain ignored under `work/` and were not
committed.

The strongest new result is a routed-message parser memory-corruption
candidate in an unnamed source-available ecosystem component. A
source-faithful GCC 13.3.0 ASan/UBSan fixture reproduced multiple independent
out-of-bounds reads and writes. This
establishes offline memory corruption, not a target crash, controlled program
counter, ROP/JOP chain, or RCE. The affected component and versions, message
class, source coordinates, boundary arithmetic, fixture modes, and sanitizer
traces are retained only in
`work/disclosure/tbs-crossfire-private-findings-2026-10-20.md` pending
coordinated maintainer disclosure; they are deliberately absent from the
public report and commit.

A direct local ingress into the relevant router is source-confirmed. General
receiver/FC telemetry over a bound Crossfire link is vendor-documented, but
proprietary class/direction filtering into the affected parser is unknown, and
arbitrary unbound RF injection is not established. The result is therefore a
parser candidate with an unclosed RF bridge, not a demonstrated nearby-radio
exploit.

Public air-link evidence remains version-bound. Independent 2021 work
passively followed hops and decoded channels/telemetry on a legacy v6.06
150-Hz FSK link, but explicitly did not demonstrate fake-transmitter acceptance
or takeover. TBS catalog ID `0x0624`, displayed as v6.36, has a beta entry that
says it fixed a critical loss-of-bind defect when telemetry was flooded. That
establishes a remotely relevant availability surface but neither the affected
range's start, an unbound trigger, nor memory corruption. Catalog IDs encode
the decimal minor in the low hexadecimal byte: `0x0613` is v6.19, `0x0624` is
v6.36, and `0x0630` is v6.48. The optional encryption note belongs to v6.19,
not v6.13.

Static artifact work found a Cortex-M vector and plaintext ARM Thumb code in
the Tango II Crossfire-radio v6.48 package, with strings for OTA, binding,
encryption, EEPROM, and CRSF v3. The matching-version Nano RX body remained
opaque/high-entropy; encryption, compression, and other packing were not
distinguished. The v3.20 recovery archive contains ESP8266 WebUI, update,
CRSF/TCP, and MAVLink surfaces, but AP/login defaults and authenticity
enforcement were not inferred from strings alone. The catalog `signature`
field matched package MD5 for the reviewed files; that identifies catalog
checksum semantics only, not a bootloader signature bypass.

Adjacent CRSF consumers reinforce the bridge risk without solving RF session
admission. The public Betaflight issue #15658/PR #15657 is a build-identity-
length-dependent stack overflow/hang on sufficiently long suffixed builds; it
does not apply indiscriminately to every unsuffixed final version. Two further
downstream CRSF consumers have type-specific length-validation candidates, but
their identities, versions, build gates, message types, and trigger analysis
remain private pending coordination; no target fault was shown.
Public INAV issue #11776 establishes an exact four-byte
`MSP_DATAFLASH_READ` response-buffer overwrite in 9.1.0; commit `3ab0e180`
fixes it, and the fix is present in 10.0.0-rc1 and later. An accepted
MSP-over-CRSF peer can reach that handler, while unbound RF and RCE remain
unproven. PX4
CVE-2026-32706 remains an adjacent/raw-serial corruption advisory, and no
convincing current ArduPilot CRSF memory-corruption primitive was found.

The public report is
`research/tbs-crossfire-wireless-security-audit-2026-10-20.md`.
`docs/crsf-protocol-analysis.md` now carries an evidence-status notice because
the older exploratory note overstates encryption defaults, authentication,
Wi-Fi defaults, replay, takeover practicality, and update-signature
conclusions. Changed tracked files are those two documents and this append-only
log; the private disclosure packet and sanitizer fixture remain ignored.

Validation: `tools/validate-project.py` passed syntax, JSON, corpus
hashes/provenance, rainbow data, CVE coverage, and local links across 199
files. Standard-library discovery ran 224 tests: 223 passed, and the
same pre-existing unrelated `test_waveform_recovers_encoded_symbols` test
failed because the extractor returned 20 of 21 synthetic symbols. The offline
sanitizer cases reproduced all documented private memory faults. The final
whitespace check passed. Commit/push status is recorded in the final task
handoff.

The next safe work is coordinated private maintainer disclosure, offline
mapping of CRSF class/direction filters in the plaintext Tango II radio image,
v6.31-to-v6.36 differential analysis of the telemetry-flood fix, and
receive-only validation of the legacy air decoder. Any crafted-input,
bound-link, network, or RF test requires a separate owner-approved plan under
`SECURITY.md`; nothing here authorizes flashing.

---

## 50. Betaflight 4.4.2 OTA v3 SYNC correction and CI confirmation (Codex, 2026-10-24)

Started from `ac25a73` after the repository-required fast-forward pull. The
objective was to resolve contradictory project claims about which UID fields
appear in SYNC and correct the operational documentation. Work after the pull
was offline and documentation/test-only: no serial or device access, RF receive
or transmit, configuration change, firmware operation, or physical interaction
occurred.

The pinned Betaflight 4.4.2 source archive still hashes to
`195b0c6eb66741f6be64f30c63e6a41cc0cf2e990505508f1f7cc270de477c29`.
Its `expresslrs_common.h` defines the six-byte v3 SYNC payload as
`fhssIndex, nonce, flags, UID3, UID4, UID5`. In the eight-byte OTA frame these
identity fields occupy offsets 4--6. `processRFSyncPacket()` compares all of
UID3 and UID4 plus the upper two bits of UID5; the lower six transmitted UID5
bits may be XORed with inverse model ID for model match. CRC14 is checked first
with initializer `((UID[4] << 8) | UID[5]) ^ 3`, which uses the actual full
UID suffix. The pair's existing read-only configuration reports model ID 255,
so its model XOR is zero and its decoded fields equal UID bytes 3--5. OTA v4,
not v3, removes UID3. The existing passive evidence remains consistent with
that source interpretation: 16 decoded SYNC frames agreed on a suffix that
independently validated 38 decoded RC frames. No private UID or packet bytes
were added to tracked files.

Corrected the live-target rules, game-day playbook, research plan, v3/v4
comparison, rainbow-table help, exploit-chain comments, firmware inventory and
analysis notes, failsafe comparison, and related research notes. The corrections
also distinguish raw fields from unmasked UID material, fix the target's v3
sync channel to 41, and use the 240 active entries in the 80-channel FHSS table.
Historical Fox-IT 1.x/2.x phrase recovery is now kept separate from the exact
Betaflight 4.4.2 v3 behavior. A display-only Unicode arrow in the rainbow
tool's argparse title was also replaced with ASCII after `--help` exposed a
Windows CP-1252 encoding failure.

The missing CI unit-test step had already been added on `origin/main` before
these edits; `.github/workflows/validate.yml` now runs both documented offline
commands on Ubuntu and Windows with Python 3.10 and 3.12. Validation here passed:
`tools/validate-project.py` checked 199 files, and standard-library discovery
passed all **224 tests**. The contradiction sweep and path-scoped
`git diff --check` also passed. Commit/push status is recorded in the final
handoff.

---

## 51. GitHub Actions long-interleaver waveform fix (Codex, 2026-10-25)

Started from rebased local revision `1bfad55` on top of `origin/main` at
`f2d9579`. The objective was to reproduce and repair the four failing Offline
project validation matrix jobs reported for Python 3.10/3.12 on Ubuntu and
Windows. This was an offline code/test task only: no hardware, serial port,
radio, network target, configuration, firmware, bootloader, or flashing action
was used.

The full test suite reproduced one deterministic failure in
`test_waveform_recovers_encoded_symbols`. An ideal cyclic LoRa fixture gave
sample offsets 0 and 1 mathematically equivalent preamble scores, but FFT
rounding made the later offset larger by about `9e-16`. Selecting that later
alignment left the final payload symbol one sample short at the fixture edge,
so only 20 of 21 symbols were returned. `find_preamble()` now retains the
earliest candidate when scores differ by no more than `1e-12`; materially
better candidates still replace it.

Validation after the change passed the exact CI commands under Python 3.12.3:
`python -B tools/validate-project.py` checked 212 files, and
`python -B -m unittest discover -s tools/tests -p "test_*.py"` passed all 249
tests. The formerly failing test also passed in isolation, and
`git diff --check` passed. Changed tracked files are
`tools/elrs-iq-symbols.py` and this append-only log. Commit and push were
pending when this entry was written; the final handoff records their result.

---

## 52. Fresh-checkout PHY scan test fix (Codex, 2026-10-25)

Started from `c18125d` after the repository-required pull confirmed local
`main` matched `origin/main`. The objective was to reproduce and repair the
second four-job GitHub Actions failure. This was an offline code/test task
only: no hardware, serial port, radio, network target, configuration, firmware,
bootloader, or flashing action was used.

A clean clone reproduced the failure that the established working checkout
masked. The ignored `work/` directory does not exist after checkout, but
`test_elrs_phy_scan.py` attempted to create a temporary directory inside it
when NumPy and SciPy were available. The neighboring sync-channel test already
created its tool's private work root during class setup. The PHY scan test now
does the same before exercising private-output validation.

Validation was repeated in a fresh Git checkout with Git LFS files left as
checkout pointers and no pre-existing `work/` directory. The exact workflow
commands passed: `python -B tools/validate-project.py` checked 212 files, and
`python -B -m unittest discover -s tools/tests -p "test_*.py"` passed all 249
tests under Python 3.12.3 with the optional scientific dependencies present.
Changed tracked files are `tools/tests/test_elrs_phy_scan.py` and this
append-only log. Commit and push were pending when this entry was written; the
final handoff records their result.

## 53. Documentation correction sweep and October 25 status incorporation (Claude, 2026-10-25)

**Starting revision:** `c18125d` (Fix LoRa waveform alignment test)

**Objective:** Correct stale technical claims across project documentation,
incorporate the standalone `session-notes-2026-10-25.md` status into this
canonical log, and enforce the four-level evidence framework established
in prior sections.

**Safety boundaries:** No hardware access, no serial ports, no RF transmission,
no firmware modifications. The absolute no-flashing constraint for the E8 and
TinyHawk remains in force. Files owned by the concurrent Codex session (ELRS
capture validator, MSP-over-ELRS simulator, OTA-v3 transmitter/packet-forger,
protocol CI) were not touched.

### Stale-claim corrections applied

1. **FHSS sequence length: 256 → 240.** The raw `FHSS_SEQUENCE_LEN` constant
   is 256, but the live sequence is trimmed to `3×NR_FHSS_ENTRIES` = 3×80 = 240
   active entries for the 80-channel 2.4 GHz domain. Corrected in:
   - `E8-SERIAL-CHARACTERIZATION.md` (line ~360)
   - `failsafe-behavior-comparison.md` (line ~301)
   - `tools/elrs-tx-emulator-README.md` (line ~17)

2. **Sync channel: channel 40 → version-qualified.** OTA v3 (Betaflight 4.4.2,
   live target) uses sync channel 41; OTA v4 uses channel 40. The table in
   `research/rf-disruption-feasibility.md` §3.1 previously said "Channel 40"
   without version qualification. The §3.5 narrative already had the correct
   v3/v4 distinction. Corrected the table to reference both versions.

3. **Nonce XOR in CRC init: generic → version-qualified.** The
   `docs/firmware-to-poc-methodology.md` table row 3 previously said "Nonce
   XOR'd into CRC init" as a blanket statement. This is v4-only behavior;
   OTA v3 uses `((UID[4]<<8)|UID[5]) ^ OTA_VERSION_ID` with no nonce XOR
   (see §19 and §34 for the source-derived and capture-validated evidence).
   Corrected to distinguish v3 and v4.

4. **FLRC references for live target.** The 4.4.2 target is LoRa-only
   (confirmed in §15 source analysis). FLRC modes exist only in newer ELRS
   firmware. Added qualification notes in:
   - `research/rf-disruption-feasibility.md` §3.2 modulation table
   - `docs/firmware-to-poc-methodology.md` table row 5

5. **CRC14 table size (no change needed).** `research/betaflight-442-elrs-analysis.md`
   line 143 says "256 entries, precomputed at init" — this refers to the CRC14
   lookup table, not the FHSS sequence. 256 is correct for the table. No edit made.

### October 25 project status (incorporating session-notes-2026-10-25.md)

The standalone file `session-notes-2026-10-25.md` was created as a status
snapshot. Its content is incorporated here with corrections; the standalone
file remains in the repo as a historical artifact.

**PRs #40–#42 are merged** (not "open" as the standalone file stated):
- PR #40 — SX1280 TX hardware guide (commit `f2d9579`)
- PR #41 — Wire real scanners into web UI (commit `ed2d30e`)
- PR #42 — Chain 2 WiFi → MAVLink injection tools (commit `20b2196`)

**Merged PR summary (20+ PRs to main):**

| Category | PRs | Tools / Docs |
|---|---|---|
| ELRS TX emulator | #18 | `elrs-tx-emulator.py` — live TX via SPI to SX1280 |
| MAVLink signing bypass | #19 | `mavlink-signing-bypass.py` — v2 signing weakness PoC |
| CRSF analysis | #20 | `crsf-tool.py` — Crossfire packet parsing and crafting |
| Exploit chains | #21 | End-to-end automated attack sequences |
| WiFi tools | #12 | WiFi drone attack surface reference |
| Betaflight analysis | #34 | MSP-over-ELRS attack surface mapping |
| Replay canary | #36 | Fail-closed ELRS v3 hybrid-RC replay canary |
| Web UI | #38 | Web-based drone scanner dashboard |
| Capture validation | #39 | Chain 1 PHY frame validation against real captures |
| Rainbow tables | #5 | 723 binding phrases, v3/v4 OTA support |
| Recon tools | #9, #13, #16 | BLE scanner, curses dashboard, passive telemetry |
| Protocol research | #6, #7, #8 | Failsafe mapping, GPS spoofing, ELRS v3/v4 diff |
| Testing | #10, #37 | 90+ integration tests, MSP injection simulator |
| SDR compatibility | #14 | SoapySDR abstraction layer for all RF tools |
| SX1280 hardware guide | #40 | Dev board sourcing for Chain 1 live RF ($23–$69) |
| Web UI scanners | #41 | Flask dashboard with real scanner/exploit SSE |
| Chain 2 WiFi tools | #42 | Evil twin AP, MitM proxy, MAVLink injection |

**Evidence levels — what is proven and what is not:**

| Level | Status | Evidence |
|---|---|---|
| Source-derived | ✅ Complete for 4.4.2 v3 | FHSS, CRC14, SYNC layout, nonce behavior all matched to Betaflight 4.4.2 source |
| Synthetic-test validated | ✅ 249 unit tests pass | Round-trip forge→CRC→verify in test harness |
| Passive-capture validated | ✅ 54 PHY frames (16 SYNC, 38 RC) | Real E8→TinyHawk captures decoded with correct CRC and FHSS sequence |
| Receiver-acceptance validated | ❌ Not yet attempted | Requires live RF TX via SX1280 hardware; this is the critical-path blocker |

**Correction:** The standalone file stated "Chain 1 validated." This is an
overclaim. CRC agreement on passive captures proves the source-derived model
matches the over-the-air encoding. It does NOT prove the receiver will accept
forged packets from an independent transmitter. Receiver acceptance is a
separate evidence level that requires live RF testing with SX1280 hardware.

**Forward plan (prioritized):**

*No hardware required:*
1. Chain 2 SITL end-to-end test — evil twin → MitM → MAVLink injection against ArduPilot SITL
2. Web UI integration testing — Flask dashboard with real tool backends
3. Cross-chain orchestrator — unified launcher for Chain 1 + Chain 2
4. MSP-over-ELRS exploitation PoCs (attack surface mapped in PR #34)

*Hardware-dependent (SX1280 dev board, $23–$69, guide in PR #40):*
5. Chain 1 live injection test — packet forge → SPI TX → RF → receiver acceptance
6. Replay canary validation against live RF

**Key risk:** SX1280 hardware is the critical-path blocker for advancing
from passive-capture validation to receiver-acceptance validation.

### Changed files

- `research/rf-disruption-feasibility.md` — sync channel version-qualified; FLRC note added
- `E8-SERIAL-CHARACTERIZATION.md` — FHSS 256→240 correction
- `failsafe-behavior-comparison.md` — FHSS 256→240 correction
- `tools/elrs-tx-emulator-README.md` — FHSS 256→240 correction
- `docs/firmware-to-poc-methodology.md` — nonce XOR version-qualified; FLRC/LoRa note
- `SESSION-NOTES.md` — this entry

### Validation

- `python -B tools/validate-project.py` — PASS (212 files: syntax, JSON, corpus hashes, provenance, rainbow data, CVE coverage, local links)
- `python -B -m unittest discover -s tools/tests -p "test_*.py"` — 241 tests passed, 3 skipped, 0 failures
- `git diff --check` — no whitespace errors

### Next steps

- Codex: continue OTA-v3 transmitter/packet-forger corrections and protocol CI
- Acquire SX1280 dev board for Chain 1 live RF testing
- Run Chain 2 SITL end-to-end test when ArduPilot SITL is available
- Address remaining open questions (CTF target platform, RF rules, scoring model)

## 54. Web dashboard security hardening (Claude, 2026-10-25)

**Starting revision:** c18125d (main, "Fix LoRa waveform alignment test")

**Objective:** Harden the web dashboard (`web/app.py` and
`web/templates/index.html`) against stored-DOM-XSS, insecure defaults,
and UI correctness issues. Scoped by operator to six categories plus
focused tests.

**Actions and evidence:**

1. **Stored-DOM-XSS fix.** The `escHtml()` JS function used
   `textContent`/`innerHTML` which escapes `<>&"` but not single quotes.
   Inline `onclick` handlers used single-quoted attribute strings, so a
   target ID containing `'` could break out of the handler context.
   - Fixed `escHtml()` to also replace `'` with `&#39;`.
   - Removed all inline `onclick=` handlers from the HTML template.
   - Replaced with `data-*` attributes and `addEventListener` event
     delegation attached after DOM insertion.
   - Added `safeCssClass()` to sanitize threat-level values before
     interpolation into CSS class names (strips non-alphanumeric chars).

2. **Bind to localhost by default.** Changed `--host` argparse default
   from `0.0.0.0` to `127.0.0.1`. Operators who need network access can
   pass `--host 0.0.0.0` explicitly.

3. **Dry-run as default.** Added module-level `dry_run_mode = True` and
   `--live-actions` CLI flag. The action execution route now uses
   `execute_action_simulated` (dry-run) whenever `simulate_mode` or
   `dry_run_mode` is true. The `/api/status` endpoint reports `dry_run`
   state. The frontend shows "LIVE MODE (DRY-RUN)" when applicable.

4. **Action categories.** Added `"category"` field to every entry in
   `ACTION_DEFS`: `"simulated"` (computation only, e.g. forge_disarm,
   preview_fhss), `"passive"` (read-only recon, e.g. sniff, streamon,
   ble_scan_services, scan_deeper), or `"active"` (state-changing
   commands). The frontend renders category badges on action cards with
   distinct colors.

5. **Threat labels from score.** `run_scan_simulated()` previously
   copied hardcoded `threat_level` values from `SIMULATED_TARGETS`.
   Now it recalculates `target["threat_level"] = _threat_from_score(target)`
   after computing the score, so labels always derive from the score.

6. **Scanner exception cleanup.** Both `run_scan_simulated()` and
   `run_scan_live()` now wrap their bodies in `try/finally` blocks that
   reset `is_scanning = False` in the `finally` clause, preventing the
   UI from getting stuck in "scanning" state on exceptions.

**Safety boundaries:**
- Did not access hardware, serial ports, RF transmitters, or live networks.
- Did not modify ELRS chain, MSP, OTA, or protocol CI files (Codex scope).
- No live destructive actions added or enabled.
- Preserved the absolute no-flashing rule for E8 and TinyHawk.

**Changed files:**
- `web/app.py` — localhost binding, dry-run default, action categories,
  scanner try/finally, threat label derivation, status API dry_run field.
- `web/templates/index.html` — XSS fixes (escHtml, event delegation,
  safeCssClass), action category badges, dry-run mode display.
- `tools/tests/test_web_dashboard.py` — 28 new focused tests covering
  hostile SSIDs, action validation, scanner failure cleanup, score labels,
  dry-run gating, localhost binding, frontend XSS mitigations, and
  action categories.

**Validation:**
- `python -B tools/validate-project.py`: PASS (213 files)
- `python -B -m unittest discover -s tools/tests -p 'test_*.py'`: 269 tests OK (3 skipped)
- `python -B -m unittest tools/tests/test_web_dashboard.py`: 28/28 OK
- `git diff --check`: clean

**Limitations:**
- Frontend XSS mitigations are structural (event delegation, escaping)
  but do not add Content-Security-Policy headers. CSP would be a
  separate follow-up.
- The `innerHTML` assignments in `updateScannerStatus()`, `renderTargetList()`,
  and `renderDetail()` still use template strings, but all interpolated
  values now pass through `escHtml()` or `safeCssClass()`.
- No end-to-end browser test for XSS payloads; coverage is via
  static analysis of the template and unit tests of the Python layer.

**Next steps:**
- Add CSP headers (`Content-Security-Policy: default-src 'self'`) as a
  defense-in-depth layer.
- Consider migrating innerHTML rendering to DOM API for further XSS
  hardness if the dashboard grows.
- Continue ELRS pair benchmark (Codex scope, §50–§51).

## 55. Chain 2 SITL integration harness and tests (Claude, 2026-10-25)

**Starting revision:** `c18125d` (HEAD of main)

**Objective:** Build an offline Chain 2 MAVLink integration test suite against
ArduPilot SITL. Exercise discovery, interception, MAVLink parsing, signing
policy, command injection, and audit logging end to end. Verify success through
observed SITL state and telemetry, not merely packet transmission. All network
activity localhost-only; no hardware, no RF, no external targets.

**Actions:**

1. Created `tools/sitl_harness.py` — SITL process lifecycle manager.
   - Context-manager (`with SITLHarness() as sitl`) for deterministic setup/teardown.
   - Starts SITL with `--wipe --synthetic-clock` for reproducible EEPROM state.
   - Instance-based port numbering (5760+10*N) avoids collisions when running
     multiple SITL instances in parallel.
   - Work directory `/tmp/sitl_test_{instance}_{pid}`, cleaned on teardown.
   - Helper functions: `wait_for_mode()`, `wait_armed()`, `get_vehicle_state()`.
   - Standalone smoke test (`python3 tools/sitl_harness.py`).

2. Created `tools/tests/test_sitl_chain2.py` — 23 integration tests in 6
   classes, each using a distinct SITL instance:
   - `TestSITLDiscovery` (inst 10): heartbeat, vehicle type, autopilot ID,
     initial mode (STABILIZE), disarmed state, SYS_STATUS presence.
   - `TestSITLSigningPolicy` (inst 11): signing disabled by default, MAVLink v2
     capability confirmed.
   - `TestSITLCommandInjection` (inst 12): mode changes (GUIDED, RTL, LAND)
     verified through telemetry state, COMMAND_LONG ACK, reboot ACK.
   - `TestSITLMavlinkParsing` (inst 13): heartbeat field correctness, ATTITUDE
     stream, GPS position (with init-time retry), raw buffer format, CRC
     validation via pymavlink round-trip.
   - `TestSITLWireOrderP1_3` (inst 14): correct vs wrong COMMAND_LONG wire
     order comparison against pymavlink reference; SITL acceptance of correctly
     ordered payload.
   - `TestSITLAuditLogging` (inst 15): message-type inventory (≥5 types),
     COMMAND_ACK audit trail.
   - All tests skip gracefully when the SITL binary is absent.

**Evidence (SYNTHETIC-TEST only — no live RF or hardware):**

- `python -B tools/validate-project.py` — PASS, 214 files checked.
- `python -B -m unittest discover -s tools/tests -p "test_*.py"` — 264 tests
  passed (3 skipped), including all 23 new SITL tests.
- `git diff --check` — clean.
- pymavlink 2.4.50 installed; ArduCopter SITL 4.7.2 binary used from
  `firmware/ardupilot/arducopter_SITL_x86_64_4.7.2`.

**Key fixes during development:**

- SITL does not stream SYS_STATUS/ATTITUDE/GPS by default; added explicit
  `request_data_stream_send()` calls before waiting for telemetry.
- pymavlink `msg.pack()` defaults to MAVLink v1 (6-byte header, magic 0xFE),
  not v2; detection by magic byte fixes header-length assumptions.
- pymavlink v1 truncates trailing zero bytes from payload; using non-zero field
  values and a zero-padding fallback ensures length comparisons work.
- GPS coordinates require SITL warm-up time; retry loop up to 20 s before
  asserting approximate position match.

**Safety boundaries:**

- All tests run against localhost SITL only. No hardware, serial, RF, or
  external network access.
- No firmware flashing or modification (absolute no-flash constraint preserved).
- No live destructive actions; command injection targets the simulator only.
- Codex-owned files (ELRS capture validator, MSP simulator, OTA-v3 tools,
  protocol CI) were not modified.

**Changed files:**

- `tools/sitl_harness.py` (new)
- `tools/tests/test_sitl_chain2.py` (new)
- `SESSION-NOTES.md` (this entry)

**Dependencies:** `pymavlink>=2.4.50`, ArduPilot SITL binary at
`firmware/ardupilot/arducopter_SITL_x86_64_4.7.2`.

**Next steps:**

- Harden the web dashboard (XSS, localhost binding, dry-run default).
- Consolidate SESSION-NOTES.md and sweep stale documentation claims.
- Once Codex completes the ELRS/MSP/OTA corrections, integrate with the
  Chain 2 SITL tests for end-to-end validation.

## 56. Web dashboard reliability, accessibility, and executable CI coverage (Codex, 2026-10-25)

**Starting revision:** `3cfca9a` (`main`, synchronized with `origin/main`)

**Objective:** Advance the application-engineering side of the project without
hardware, RF, serial, or live-target interaction. Make the Flask dashboard's
scan lifecycle, event stream, request handling, UI state, and test coverage
deterministic enough to trust in offline development.

**Actions and evidence:**

1. **Made scan admission and cleanup deterministic.**
   - Replaced the scan mutex with an `RLock` and made the check/set transition
     atomic, so concurrent requests cannot both reserve the scan worker.
   - Added request-shape, protocol, and duration validation, including malformed
     JSON, non-object bodies, non-finite/oversized values, and unknown protocols.
   - Worker-start and backend-import failures now release the scan slot; active
     scanner states become terminal errors instead of remaining stuck.
   - Clear requests are rejected while a scan is active.

2. **Corrected dashboard/tool integration.**
   - CLI defaults for WiFi, BLE, and SDR interfaces now flow into API-launched
     workers instead of being parsed and discarded.
   - Simulated scans honor the requested protocol subset and report all five
     scanner stages consistently.
   - Live target severity is derived from the computed score, and WiFi targets
     are routed to Tello, Parrot, or unknown action families by detected type.
   - The `--live-actions` flag now updates module state correctly. Both action
     route families use the same dry-run gate.

3. **Fixed long-running event delivery.**
   - SSE event IDs now come from a monotonic counter independent of the bounded
     retention list. IDs no longer repeat once `MAX_EVENTS` is reached.
   - Reconnects honor the standard `Last-Event-ID` header as well as the legacy
     query parameter.

4. **Hardened normal frontend behavior.**
   - Added one checked JSON request helper, stable scan polling with transient
     failure recovery, native EventSource reconnect behavior, and stale-response
     suppression.
   - Scan and clear buttons now track backend state rather than re-enabling
     immediately; selected targets disappearing from results clear the detail
     panel; in-flight action/log responses cannot update a different target.
   - Dynamic scores/signals are finite and bounded before rendering. Missing
     signal values remain unknown rather than being coerced to `0 dBm`.
   - Converted target/action cards to keyboard-focusable buttons, added live
     regions, focus styles, reduced-motion support, responsive modal sizing,
     and valid button content.
   - Attribute values now use quote-safe encoding in addition to existing text
     and CSS-class normalization.

5. **Made the web test suite actually execute in CI.**
   - Added pinned `Flask==3.1.3` runtime requirements (official PyPI release,
     Python 3.9+) and a dedicated Ubuntu/Python 3.12 dashboard test job.
   - Narrowed the optional import gate so only a genuinely absent Flask package
     skips local backend tests; unrelated import regressions now fail.
   - Expanded dashboard coverage from 28 to 46 tests for event rollover, scan
     lifecycle, API validation, CLI/default propagation, protocol filtering,
     target conversion, dry-run consistency, async UI recovery, numeric
     normalization, and accessibility structure.

**What failed or needed correction:**

- The minimal local WSL Python had no Flask or pip, confirming that the previous
  general offline job skipped most web tests. A checksum-verified official pip
  wheel and Flask dependencies were placed only under ignored
  `work/web-test-runtime/` for local execution; no system Python was modified.
- The first cleanup fixture raised after scanner stages had already completed;
  it was corrected to fail during active scanning.
- Independent review caught null-to-zero signal coercion, polling stopping after
  a transient status failure, and an oversized-integer `scan_time` 500 path.
  Each was fixed and covered before the final run.

**Safety boundaries:**

- No E8/TinyHawk access, serial access, firmware changes, RF transmission,
  hardware probing, or external target interaction.
- No live action was executed. Validation used Flask's in-process test client,
  static frontend assertions, and existing offline tests only.
- The only external downloads were official Python packaging artifacts used in
  an ignored local test directory; they are not committed.

**Changed files:**

- `.github/workflows/validate.yml`
- `web/requirements.txt`
- `web/app.py`
- `web/templates/index.html`
- `tools/tests/test_web_dashboard.py`
- `SESSION-NOTES.md`

**Validation:**

- `python -B tools/validate-project.py` — PASS (216 files)
- Flask-enabled dashboard suite — 46/46 PASS
- Flask-enabled full discovery — 318 tests PASS, 23 optional SITL tests skipped
- `git diff --check` — clean
- Independent post-change review — no remaining blockers

**Commit/push status:** Included in the main commit containing this section and
pushed to `origin/main`; no force push or history rewrite.

**Next application-engineering steps:**

- Add a small browser-level test for keyboard selection, scan retry, and stale
  response behavior.
- Move mutable dashboard globals behind an app/state factory for isolated tests
  and cleaner multi-client behavior.
- Bound and lock every action-history path consistently, including auxiliary
  integration histories.
- Extract inline CSS/JavaScript into versioned static assets once the UI grows
  beyond the current single-page prototype.

## 61. Responsible disclosure packaging (Claude, 2026-10-26)

**Starting revision:** `6d04761` (HEAD of main)

**Objective:** Package the private CRSF and parser findings with exact affected
revisions and sanitizer traces. Create a disclosure tracking system and
sanitized advisory drafts. Keep operational payloads private until maintainers
have acknowledged them. Track disclosure state separately from game-day
readiness.

**Scope and authorization:**
- No hardware, serial, RF, or live-network access.
- No Codex-owned files modified (ELRS capture validator, MSP simulator, OTA-v3
  tools, protocol CI).
- Preserved the absolute no-flashing rule for E8 and TinyHawk.

**Actions:**

1. **Surveyed all vulnerability findings across the repository.** Reviewed
   `research/tbs-crossfire-wireless-security-audit-2026-10-20.md`,
   `research/betaflight-442-vulnerability-audit.md`,
   `research/betaflight-442-elrs-nonrc-failsafe-finding.md`,
   `research/common-drone-wireless-subsystem-rce-survey-2026-10-19.md`,
   `docs/crsf-protocol-analysis.md`, and `SECURITY.md` to inventory all
   findings requiring coordinated disclosure.

2. **Created `docs/disclosure-tracking.md`.** Central tracking document for
   all disclosure-eligible findings. Covers:
   - 6 findings requiring maintainer contact (DISC-01 through DISC-06)
   - 9 already-public findings with CVEs/PRs/vendor acks (PUB-01 through PUB-09)
   - 90-day coordinated disclosure policy
   - Operational security rules (payloads stay under ignored `work/disclosure/`)
   - Prioritized contact sequence (DISC-01/02 first, then DISC-04/05)

3. **Created sanitized advisory drafts** in `docs/advisories/`:
   - `DISC-01-crsf-parser-oob.md` — CRSF ecosystem routed-message parser OOB
     read/write. Component identity withheld. Evidence: offline ASan/UBSan
     reproduction. RF delivery unproven.
   - `DISC-02-backpack-espnow-overflow.md` — ExpressLRS Backpack ESP-NOW MSPv2
     stack buffer overflow. Affected: release 1.5.9 and master. Evidence:
     offline ASan reproduction. ESP-NOW delivery source-confirmed.
   - `DISC-03-crsf-consumer-length.md` — Two downstream CRSF consumers with
     type-specific length-validation candidates. No fault demonstrated.
   - `DISC-04-betaflight-failsafe-suppression.md` — Betaflight 4.4.2 SPI ELRS
     non-RC packets refresh signal timer, suppressing failsafe. Source-derived.
   - `DISC-05-elrs-msp-injection-design.md` — ELRS v3 + Betaflight design
     advisory: unauthenticated MSP command injection including force-disarm,
     reboot, GPS overwrite. Source-derived.

4. **Verified operational payloads stay private.** All sanitized advisories
   contain no trigger bytes, reproduction scripts, sanitizer traces, or
   weaponizable payloads. Those materials are referenced as belonging under
   ignored `work/disclosure/` paths. No UIDs, binding phrases, or device
   secrets appear in any tracked file.

**Evidence classification of disclosure materials:**
- DISC-01: Offline-reproduced (ASan/UBSan)
- DISC-02: Offline-reproduced (ASan)
- DISC-03: Source review only
- DISC-04: Source-derived
- DISC-05: Source-derived + design analysis

**Safety boundaries:**
- No RF transmitted, no hardware accessed, no firmware modified.
- No operational payloads committed to the repository.
- Sanitized advisories are safe to share with maintainers.
- CTF game-day use tracked separately from disclosure state.

**Changed files:**
- `docs/disclosure-tracking.md` (new)
- `docs/advisories/DISC-01-crsf-parser-oob.md` (new)
- `docs/advisories/DISC-02-backpack-espnow-overflow.md` (new)
- `docs/advisories/DISC-03-crsf-consumer-length.md` (new)
- `docs/advisories/DISC-04-betaflight-failsafe-suppression.md` (new)
- `docs/advisories/DISC-05-elrs-msp-injection-design.md` (new)
- `SESSION-NOTES.md` (this entry)

**Next steps:**
- Identify security reporting channels for each affected component.
- Prepare private `work/disclosure/` packets with reproduction steps.
- Send DISC-01 and DISC-02 first (highest severity, clearest reproduction).
- Send DISC-04 and DISC-05 to Betaflight (safety-critical, source-derived).
- Track responses and 90-day windows in `docs/disclosure-tracking.md`.

## 60. Safe bench controller (Claude, 2026-10-26)

**Starting revision:** `6d04761` (main, "Add combined attack demo chain and tests")

**Objective:** Build a safe bench controller (roadmap item 9) that creates a
bench plan, verifies prerequisites, and stops before any transmission. Require
explicit arming for RF actions. Include frequency, power, duration, expected
receiver state, abort conditions, and evidence capture parameters. Preserve
the absolute no-flashing rule for the E8 and TinyHawk.

**Scope and authorization:**
- No hardware, serial ports, RF transmitters, or live target access.
- Did not modify ELRS capture validator, MSP-over-ELRS simulator, OTA-v3
  transmitter/packet-forger corrections, or protocol CI (Codex scope).
- The bench controller itself never transmits — it produces a validated plan
  file that a separate TX tool would consume.

**Actions:**

1. **Created `tools/bench-controller.py`** — safe bench test planner.
   - `plan` subcommand: derives all ELRS crypto parameters (UID, CRC init,
     FHSS seed/sequence, sync channel) from a binding phrase, assembles a
     complete bench test plan with RF parameters, safety constraints, abort
     conditions, evidence capture config, and expected receiver state.
   - `verify` subcommand: checks all prerequisites (plan version, no-flash
     enforcement, device list, arm gate, RF power/duration bounds, frequency
     range, test type, abort conditions, UID validity, evidence config).
   - `review` subcommand: displays full plan summary and JSON for inspection.
   - `arm` subcommand: two-step arming gate — requires `--arm` CLI flag AND
     interactive confirmation (type 'ARM'). Blocked by any failing
     prerequisite. Cannot arm an aborted plan.
   - `abort` subcommand: disarms a plan immediately.
   - Safety: EMAX_E8 and TINYHAWK_III in a frozen `BLOCKED_DEVICES` set;
     no-flash enforced at verification; power capped at 10 dBm; duration
     capped at 60s; dry-run is always the default.
   - Version-parameterized: supports OTA v3 (default, live TinyHawk) and v4.
   - Uses vendored `elrs_core` for all crypto (no local reimplementation).

2. **Created `tools/tests/test_bench_controller.py`** — 40 focused tests:
   - `TestPlanCreation` (11 tests): required fields, UID derivation, v3/v4
     parameters, frequency calculation, safety defaults, no-flash devices,
     power/duration bounds, evidence capture, UID privacy.
   - `TestPlanVerification` (14 tests): valid plan passes; version, no-flash,
     device list, arm gate, power, duration, frequency, test type, abort
     conditions, UID, evidence each verified; props warning.
   - `TestPlanArming` (7 tests): confirmation required, interactive ARM,
     blockers prevent arming, already-armed, aborted plan, EOF cancels.
   - `TestPlanAbort` (2 tests): disarms, handles draft.
   - `TestPlanFileIO` (1 test): save/load round-trip.
   - `TestCLIHelp` (3 tests): help, subcommand parsing, arm flag required.
   - `TestNoFlashConstraint` (3 tests): E8 and TinyHawk in frozen set.

**Safety boundaries:**
- No hardware access, serial ports, RF transmission, or live network use.
- No firmware flashing (absolute no-flash constraint preserved in code).
- The controller produces plan files only — transmission is gated behind
  a separate tool that reads the armed plan.
- Props-removed check is a prerequisite warning.
- Codex-owned files were not modified.

**Changed files:**
- `tools/bench-controller.py` (new)
- `tools/tests/test_bench_controller.py` (new)
- `SESSION-NOTES.md` (this entry)

**Validation:**
- `python -B tools/validate-project.py` — PASS (226 files)
- `python -B -m unittest discover -s tools/tests -p "test_*.py"` — 458 tests
  passed (60 skipped), including all 40 new bench controller tests
- `python -B -m unittest tools/tests/test_bench_controller.py` — 40/40 OK
- `git diff --check` — clean

**Evidence class:** SYNTHETIC-TEST (no live RF, no hardware).

**Limitations:**
- The controller is the planning/gating layer only. A TX execution tool
  that consumes the armed plan file is a separate future deliverable.
- No SDR hardware detection (would require UHD/SoapySDR imports).
- Plan files stored under `work/bench/` (gitignored) to keep UID private.

**Next steps:**
- Build the TX execution tool that reads an armed plan and gates on the
  plan's safety constraints before any transmission.
- Add SDR prerequisite detection to the `verify` subcommand when UHD is
  available on the bench machine.
- Integrate with the SITL harness for Chain 2 bench testing.

---

### §59 — Evidence-grade passive ELRS capture pipeline (Claude, 2026-10-26)

**Goal:** Build the full passive IQ→packet decode chain that was missing from
the existing tooling. The prior `elrs-iq-symbols.py` stopped at symbol
extraction and explicitly noted the gap: SX1280 long interleaving, FEC, and
CRC decode were unimplemented.

**What was built:**

1. **`tools/lora_decode.py`** — LoRa PHY decode module (standard library only):
   - Gray encode/decode for all SF values
   - Diagonal de-interleave (standard and SX1280 long interleaving)
   - Hamming(7,4) and extended Hamming(8,4) FEC with error correction
   - SX12xx LFSR whitening/de-whitening (poly 0x171, seed 0xFF)
   - Full `encode_payload()` and `decode_payload()` chains for round-trip testing
   - Evidence class: SELF-CONSISTENT (encode→decode round-trips proven; hardware
     validation awaits real SX1280 captures)

2. **`tools/elrs-passive-pipeline.py`** — End-to-end pipeline CLI (NumPy/SciPy):
   - IQ reading: sc16le, cf32le, cu8 formats with bounded window reads
   - Preamble detection via upchirp correlation with deduplication
   - Payload symbol extraction (dechirp + FFT after preamble+SFD skip)
   - LoRa decode integration (symbols → bytes via lora_decode)
   - ELRS v3/v4 CRC14 validation with the vendored elrs_core
   - SYNC/RCDATA/DATA frame field decoding
   - FHSS hop correlation: brute-force offset search with STRONG/MODERATE/WEAK/NONE
     quality classification
   - Two modes: live IQ capture processing and offline FHSS correlation from JSONL
   - Sanitized reporting: no UID material or packet bytes in stdout; full report
     written only to `work/` (gitignored)

3. **`tools/tests/test_elrs_passive_pipeline.py`** — 57-test suite covering:
   - Gray coding round-trip for all 7-bit values
   - Hamming(7,4) single-error correction for all 16 nibbles × 7 bit positions
   - Hamming(8,4) single-error correction and double-error detection
   - Full encode→decode round-trip for multiple 8-byte payloads (CR 4/7 and 4/8)
   - ELRS CRC14 generate-then-validate and corruption detection
   - FHSS correlation with synthetic perfect, random, and empty frame sets
   - Report sanitization: confirms no UID, raw_hex, or symbols in sanitized output
   - Edge cases: empty symbols, short payloads, zero inputs

**Bugs found and fixed during testing:**

- Hamming(7,4)/(8,4) syndrome-to-position mapping was using raw syndrome value as
  array index instead of mapping through Hamming position table. Fixed with explicit
  `_HAM_POS_TO_IDX` lookup.
- Hamming(8,4) p3-only error case (syndrome=0, overall_parity=1) was unreachable
  because the outer condition required `syndrome != 0`. Added a separate branch.
- Long interleave encode/decode had a tail-handling bug: the last codeword group's
  rows extended past the matrix when `n_cw` wasn't a multiple of `sf`. Replaced the
  diagonal-per-group algorithm with a flat column-major transposition that handles
  any payload length correctly.

**Validation:**

- `python -B tools/validate-project.py` — PASS (227 files)
- `python -B -m unittest discover -s tools/tests -p "test_*.py"` — 483 tests pass, 57 skipped (SITL)
- `git diff --check` — clean

**Security constraints honored:**

- No hardware access, serial ports, RF transmitters, or live targets used.
- No vendored `elrs_core` files modified.
- No Codex-scoped files modified (elrs-capture-forge-validate.py, elrs-iq-symbols.py, etc.).
- UID material never appears in tracked files or sanitized stdout output.
- Packet bytes written only under gitignored `work/`.

**Commit/push status:** Pushed to `claude/project-thread-cao82c` branch.

**Next steps:**

- Validate the LoRa PHY decode against a real SX1280 capture (requires running the
  pipeline on an actual IQ file from the TinyHawk pair).
- Compare decoded packet bytes against `elrs-li-offline.py` output on the same
  captures for cross-validation.
- Once validated against hardware, upgrade evidence class from SELF-CONSISTENT to
  CAPTURE-VALIDATED.

## 58. Receiver acceptance validation plan and test scaffolding (Claude, 2026-10-26)

**Starting revision:** `362ab66` (HEAD of main)

**Objective:** Build the offline receiver acceptance validation plan (roadmap
item 10) and test scaffolding that exercises the entire software pipeline
without hardware, SDR, or RF. The plan defines four stages (A–D) that
progressively establish M2–M5 evidence milestones, from passive baseline
capture through demonstrated receiver behavior.

**Scope, authorization, and device-specific prohibitions:**

- No hardware, serial, RF, SDR, or external network access.
- No firmware flashing (absolute no-flash constraint for E8 and TinyHawk).
- All testing is offline against the vendored `elrs_core` library.
- Codex-owned files (ELRS capture validator, MSP simulator, OTA-v3
  transmitter/packet-forger corrections, protocol CI) were not modified.

**Actions:**

1. Created `research/receiver-acceptance-validation-plan.md` — four-stage
   validation methodology:
   - **Stage A (M2):** Passive baseline capture. Record normal E8→TinyHawk
     link, decode frames, cross-check forger output against real RF.
   - **Stage B (M4):** Read-only MSP probe. Transmit a single
     `MSP_API_VERSION` (cmd 1) request — pure read, no state change. Success
     observable via telemetry response.
   - **Stage C (M4+):** Bounded neutral RC canary. Inject a single RC packet
     with safe values (throttle min, disarmed, centered pitch/yaw, bounded
     roll). Observable via Betaflight CLI channel readout.
   - **Stage D (M5):** Demonstrated receiver behavior via MSP_REBOOT (cmd 68).
     Observable, reversible. Does not claim "commandeering."

   Plan includes: safety constraints (props removed, min power, documented
   stop conditions), prerequisites, abort conditions, evidence grading, and
   dependency mapping.

2. Created `tools/tests/test_receiver_acceptance.py` — 46 offline tests in
   11 test classes:
   - `TestV3CrcInitDerivation` — v3 vs v4 CRC init formula verification
   - `TestV3SyncChannel` — sync channel 41 (v3) vs 40 (v4)
   - `TestV3FhssSequence` — sequence length, sync-at-block-start, range, determinism
   - `TestV3SyncRoundTrip` — encode→decode→compare, CRC validate/corrupt/wrong-UID
   - `TestV3RcDataRoundTrip` — neutral RC packet, armed flag propagation
   - `TestMspCommandSafetyGate` — allowed vs destructive MSP command classification
   - `TestBoundedRcSafetyInvariants` — 8 safe/unsafe RC state checks
   - `TestModelMatchAlwaysSatisfied` — model_id=255 confirmation
   - `TestEvidenceSchema` — JSON schema validation for all 3 evidence reports
   - `TestLiveTargetParameters` — cross-check against §17 confirmed parameters
   - `TestMspFragmentConstruction` — MSPv1 header and stubborn-sender sequence
   - `TestAbortConditionDetection` — arming/mode-change/link-loss detection

**Evidence (SYNTHETIC-TEST only — no live RF or hardware):**

- `python -B tools/validate-project.py` — PASS (232 files)
- `python -B -m unittest discover -s tools/tests -p "test_*.py"` — 516 tests
  passed (60 skipped: SITL binary and Flask optional)
- `git diff --check` — clean

**What worked and what it establishes:**

- The v3 protocol layer (CRC14, FHSS, SYNC, RC DATA) round-trips correctly
  through `elrs_core` + `v3_compat`. All confirmed live parameters (v3, LoRa,
  channel 41, model_id=255) are cross-verified in tests.
- The safety invariant checks confirm that bounded-RC rejects unsafe inputs
  (armed, high throttle, unconstrained roll, full pitch/yaw).
- The MSP command gate correctly excludes destructive commands from all stages.
- Evidence schemas are JSON-serializable and contain all required fields.

**What failed, was blocked, or remained inconclusive:**

- Stages B-D are blocked on LoRa TX modulation (Phase 2 PHY, §9.4 steps 9-11).
  The forger produces correct bytes but cannot transmit without the PHY layer.
- Stage A requires the owner's bench (B205 + TinyHawk + E8).
- No live RF validation — all evidence is SYNTHETIC-TEST grade.

**Private-data handling and safety state:**

- No private UID, binding phrase, or capture data used in tests (test UID
  derived from the public example phrase "iloveflying").
- No hardware accessed, no RF transmitted, no serial ports opened.
- Absolute no-flash constraint preserved.

**Changed files:**

- `research/receiver-acceptance-validation-plan.md` (new)
- `tools/tests/test_receiver_acceptance.py` (new)
- `SESSION-NOTES.md` (this entry)

**Validation:**

- `python -B tools/validate-project.py` — PASS (232 files)
- `python -B -m unittest discover -s tools/tests -p "test_*.py"` — 516/516 PASS
  (60 skipped)
- `git diff --check` — clean

**Next steps:**

- Stage A: Owner captures baseline RF from the live TinyHawk–E8 pair on the
  B205. Cross-check against the forger's SYNC/FHSS/CRC output.
- Phase 2 PHY (Codex): vendor `elrs_core` LoRa modulation, rewire the TX
  emulator. Unblocks Stages B–D.
- Betaflight AUX/mode dump (owner, read-only CLI): required before Stage C
  bounded-RC injection.
- After Stage A passes, Stage B (read-only MSP_API_VERSION probe) is the
  first live transmission test.

## 57. Offline browser validation and multi-client UI state (Codex, 2026-10-26)

**Starting revision:** `7757f42` after the required initial sync. Claude pushed
additional non-overlapping tool work during this session, so the tree was
fast-forwarded again to `362ab66` before validation and handoff.

**Objective:** Continue the application-engineering track without hardware,
RF, serial, or live-target interaction. Exercise the dashboard in a real browser
and fix the UI-state defects exposed by multi-tab, keyboard, and narrow-screen
usage.

**Actions and evidence:**

1. **Added a real offline browser suite.**
   - Added a pinned Playwright test dependency layered on the existing Flask
     requirements.
   - The tests serve the actual Flask app through a threaded Werkzeug server on
     an ephemeral `127.0.0.1` port and drive an installed Chrome-family browser.
   - A browser route guard aborts and records every non-loopback HTTP(S)
     request initiated by the dashboard page. Live scan, live action, and
     hardware-detection functions are patched to fail the test if reached.
     Simulation and dry-run modes are asserted after every case.
   - Six browser cases cover page boot/console errors, a complete simulated
     scan plus dry-run action/log/clear flow, keyboard selection and Arrow-key
     focus movement, out-of-band clear focus recovery, scan-request failure
     recovery, and 390 px horizontal/vertical reachability.

2. **Corrected event-stream cursor semantics.**
   - A new tab now starts at the current SSE tail instead of replaying as many
     as 500 retained events as new notifications.
   - Genuine `Last-Event-ID` reconnects still replay only newer events.
   - A cursor ahead of the server counter, as after a process restart, resets
     to the current tail so future events are not stranded.
   - Added deterministic route-level tests for all three cases.

3. **Improved multi-client and keyboard behavior.**
   - SSE-triggered status requests are coalesced to avoid request bursts.
   - `scan_start` and `targets_cleared` now refresh other open tabs, while
     `action_executed` refreshes the selected target's action log.
   - Target rows preserve focus across DOM re-renders, expose `aria-current`,
     and support Arrow Up/Down plus Home/End navigation. If another client
     clears the focused row, focus moves to the stable Scan control.
   - Action results expose live-region and busy state for assistive technology.

4. **Made the layout resilient at narrow widths and zoom.**
   - Replaced the fixed viewport-height calculation with a dynamic-viewport
     flex shell.
   - Header/status/toolbar rows wrap, detail columns become bounded responsive
     tracks, and the event feed cannot exceed the viewport width.

5. **Expanded executable CI coverage.**
   - The dedicated dashboard job now runs on both Ubuntu and Windows with
     Python 3.12 and installed Chrome, in addition to the existing four-cell
     offline validator matrix.

**What failed or needed correction:**

- Flask's test client waits for the first streaming chunk, so the initial SSE
  test design deadlocked before it could enqueue a new event. The tests were
  corrected to invoke the streaming route inside a request context and drive
  its generator directly.
- Final review found that `min-height: 100dvh` plus hidden body overflow let the
  mobile flex shell grow beyond the viewport and clip content. It now uses a
  definite dynamic viewport height, and the browser test scrolls the detail
  panel to prove the bottom action log is reachable.
- The out-of-band clear test showed that a fresh SSE response did not flush its
  headers until the first domain event. The stream now emits a harmless initial
  comment, so `EventSource` becomes ready immediately without replaying history.
- The same cross-client flow exposed focus falling back to the document body
  when a focused target disappeared. It now moves to the stable Scan control.
- The host had Edge rather than Chrome. The browser channel is configurable via
  `DASHBOARD_BROWSER_CHANNEL`; local validation used `msedge`, while CI
  explicitly uses the `chrome` channel available on hosted runners.

**Safety boundaries:**

- Dashboard-page HTTP(S) requests could contact only `127.0.0.1`/`localhost`;
  no external application request was permitted.
- No E8/TinyHawk access, serial access, firmware changes, RF transmission,
  hardware probing, or live actions occurred.
- Flask and Playwright were installed only into the ignored repository-local
  `work/python312-venv` environment.

**Changed files:**

- `.github/workflows/validate.yml`
- `web/requirements-e2e.txt`
- `web/tests/test_dashboard_browser.py`
- `web/app.py`
- `web/templates/index.html`
- `tools/tests/test_web_dashboard.py`
- `SESSION-NOTES.md`

**Validation:**

- Browser suite on local Edge: 6/6 PASS.
- Flask-enabled dashboard unit suite: 52/52 PASS.
- Full tool test discovery: 484 PASS, 23 optional SITL tests skipped.
- `python -B tools/validate-project.py`: PASS (232 files before this log update).
- `git diff --check`: clean.

**Commit/push status:** Included in the commit containing this section and
pushed to `origin/main`; no force push or history rewrite.

**Next application-engineering steps:**

- Move mutable dashboard globals behind an app/state factory so separate app
  instances and test clients cannot share scan or history state.
- Bound and lock action and auxiliary integration histories consistently.
- Extract inline CSS/JavaScript into versioned static assets and add a strict
  self-only content policy once style attributes have been removed.
- Add persistent scan-session summaries and a non-blocking cancelable progress
  surface for longer scans.
---

### §60 — Claude handoff validation and capture-proven passive pipeline (Codex, 2026-10-27)

**Starting point:** Claude's `ba8abdb` handoff reached `origin/main` through
merge `e176dc4`. After rebasing onto `origin/main` at `3e7e480`, the three next
steps listed in §59 were executed entirely offline against the tracked passive
capture corpus.

**Initial audit result:** The handoff's 57 synthetic encode/decode tests passed,
but the draft was not compatible with the recorded SX1280 signal: it produced
0/54 byte matches against the established decoder and found zero candidates in
a bounded real-IQ window. The draft modeled a 16-symbol generic CR 4/7 path;
the measured link uses 21 payload symbols and SX1280 mode `0x07` (4/8 long
interleaving).

**What changed:**

1. Added a separately implemented, capture-validated SX1280 SF7/4-8-LI inverse
   in `tools/lora_decode.py`, including the measured shortened opening, parity
   layout, nibble order, and whitening sequence. Generic round-trip helpers are
   retained but explicitly labeled synthetic.
2. Reworked `tools/elrs-passive-pipeline.py` into an orchestrator around the
   established `elrs-phy-scan.py` candidate extractor, the new independent byte
   decoder, and `elrs-li-offline.py` as a byte-for-byte reference.
3. Corrected v3 packet labels and RC unpacking, ExpressLRS binding-phrase UID
   derivation, exact measured-frequency handling, full-file SHA-256 hashing,
   and four-packets-per-hop FHSS timing.
4. Made evidence grading fail closed. `CAPTURE-VALIDATED` now requires a passing
   independent byte cross-check plus either an independently supplied UID or at
   least two distinct CRC-valid SYNC observations. A lone self-derived SYNC,
   duplicate inputs, duplicate observations, and single-SYNC-dependent frames
   cannot promote evidence. FHSS correlation requires at least eight distinct
   observations spanning two channels.
5. Hardened all JSON inputs and reports: non-object rows, boolean bins, invalid
   channel metadata, non-finite timestamps, and non-boolean CRC flags are
   rejected; repeated paths/channels are rejected; raw bytes, UID material,
   source paths, and full provenance remain confined to new files below ignored
   `work/`; existing output files are never overwritten.
6. Added exact per-input SHA-256 and per-frame source/line provenance to private
   reports, kept sanitized stdout path-free, and synchronized two stale JSONL
   entries in `captures/SHA256SUMS.txt` with the exact tracked file bytes.

**Capture evidence:**

- Both ranked corpora together: 62 PHY candidates, 54 CRC-valid frames, 16
  SYNC, 38 RC, and 8 unresolved. All 54 accepted packet byte strings match
  `elrs-li-offline.py`; mismatches and false positives are both zero.
- Independent decoder regression: all 62 candidates at both candidate-wide bin
  offsets, 124 comparisons total, exactly match the maintained reference.
- Full October 15 IQ capture: SHA-256
  `5faf27cb6b8c7261950ebf38a67034bee9223c290c49dc6f47c4f5e93f0fee2f`,
  34 candidates, 32 CRC-valid, 10 SYNC, 22 RC, zero byte mismatches.
- Full October 16 IQ capture: SHA-256
  `b5898512f0d07c370b1249db870899400c309c7449e36debecf8843c513b96b5`,
  28 candidates, 22 CRC-valid, 6 SYNC, 16 RC, zero byte mismatches.
- A bounded 0.06-second IQ probe recovered 4/4 CRC-valid frames (1 SYNC, 3 RC)
  and intentionally remained `CRC-VALIDATED`, because one self-derived SYNC is
  not independent identity evidence.

`CAPTURE-VALIDATED` here means that the passive PHY/byte decode is reproduced by
two implementations over multiple tracked off-air observations. It does not
claim live receiver acceptance, transmitter identity authentication, or measured
multi-channel FHSS agreement.

**Changed files:**

- `captures/SHA256SUMS.txt`
- `tools/elrs-passive-pipeline.py`
- `tools/lora_decode.py`
- `tools/tests/test_elrs_passive_pipeline.py`
- `tools/tests/test_sx1280_capture_decode.py`
- `SESSION-NOTES.md`

**Validation:**

- Focused pipeline/decoder suite: 78/78 PASS under dependency-free `python -S -B`.
- Full tool suite: 562 PASS, 23 optional SITL tests skipped.
- `python -B tools/validate-project.py`: PASS (236 files before this log update).
- `git diff --check`: clean.
- Two independent final reviews found no remaining correctness, privacy,
  Windows/Python-3.10, or evidence-class blocker.

**Safety boundary:** No hardware, serial port, RF transmission, firmware change,
network target, or live action was used. Work was limited to tracked passive IQ
and ranked-symbol captures, deterministic local decoding, tests, and reporting.

**Commit/push status:** Corrective implementation is commit `5075c86`; this
canonical log entry is a follow-up documentation commit. Both are intended for
normal push to `origin/main` without force or history rewrite.

**Remaining evidence work:** Strong FHSS claims still require one coherent
multi-channel timeline, a complete independently supplied UID, and at least
eight distinct CRC-valid observations across two or more channels. Until then,
the pipeline reports insufficient evidence rather than promoting the result.

---

## 62. Canonical roadmap and probe-free dashboard simulation (Codex, 2026-10-27)

**Starting point and objective:** Work resumed at local revision `de42c67`
(`origin/main` `3e7e480`) after a fresh successful `git pull --rebase`. The
owner asked for Claude's comprehensive way-forward document to be assessed,
corrected, and used as one guiding document, then asked to proceed immediately.

**Roadmap assessment:** Three independent read-only passes compared Claude's
`codex-tasks.html` inventory with the current code, merged PRs, tests, and this
evidence log. The inventory is useful, but later PRs completed or partially
completed many of its 20 cards; several remaining tasks overlap, have stale
premises, or would cause unnecessary whole-repository churn. The older
`session-notes-2026-10-25.md` way-forward text was already incorporated with
corrections in §53 and is historical.

**What changed:**

1. Added `PROJECT-ROADMAP.md` as the sole current priority/status source. It
   preserves Claude's task IDs, records Done/Partial/Changed/Deferred decisions,
   orders the safe offline backlog, defines acceptance/evidence gates, and
   separates active work from hardware/RF/serial/receiver-acceptance work.
2. Updated `AGENTS.md` and `README.md` to point to the roadmap. Preserved
   `SESSION-NOTES.md` as the canonical append-only evidence/handoff log rather
   than a competing active backlog. Marked `codex-tasks.html` as the historical
   source inventory and linked the maintained roadmap.
3. Used the roadmap to select and complete R0. In simulation,
   `/api/hardware` now returns deterministic `not_checked` data and never calls
   `detect_hardware()`. Page startup and manual refresh therefore do not run
   local subprocess, BLE, SDR, or UDP-bind probes.
4. Changed the hardware widget to show a yellow `Simulation / Not checked`
   state rather than claiming devices are connected or unavailable. Live mode
   still delegates to the detector. MAVLink status now means only UDP-listener
   readiness and explicitly does not claim a connected peer or hardware device.
5. Removed invalid nested-button markup from the widget and added route/browser
   regressions whose hardware detector guard raises if simulation reaches it.

**What this establishes:** R0 is `SYNTHETIC/OFFLINE-TESTED`. The dashboard's
documented no-hardware simulation boundary is now an executable invariant at
both route and browser levels. It does not make any claim about live detector
accuracy or connected hardware.

**Validation:**

- Focused Flask API suite: 38/38 PASS.
- Loopback Playwright/Edge browser suite: 11/11 PASS, including page load,
  manual hardware refresh, mobile reachability, focus recovery, actions, and
  logs.
- Full tool suite: 648 PASS, 23 optional SITL tests skipped.
- `python -B tools/validate-project.py`: PASS (252 files before this entry).
- `git diff --check`: clean.

**Safety and private data:** No hardware, serial port, RF transmission,
firmware change, external target, or live action was used. Browser traffic was
restricted to loopback. No private identifier or capture was read or added.

**Changed files:** `PROJECT-ROADMAP.md`, `AGENTS.md`, `README.md`,
`.github/workflows/validate.yml`, `codex-tasks.html`,
`session-notes-2026-10-25.md`,
`research/layer2-wireless-attack-research.md`, `web/app.py`,
`web/templates/index.html`, `web/tests/test_api_routes.py`,
`web/tests/test_dashboard_browser.py`,
`web/README.md`, and this log.

**Commit/push status:** This section is included with the roadmap and R0
implementation in the same normal commit and is intended for a normal push to
`origin/main`; no force push or history rewrite is used.

**Next task selected by the roadmap:** R1 — move mutable dashboard globals
behind an explicit state/application factory and prove that two app instances
cannot share targets, histories, event cursors, scan state, hardware cache, or
Chain 2 state. R2 then locks and bounds all histories under that ownership
model.

---

## 63. Primary-source research expansion of the canonical roadmap (Codex, 2026-10-27)

**Objective:** The owner supplied Claude's one-screen research summary and
asked for a deeper research pass, assessment, and additions to the singular
guiding document. The linked Claude artifact required sign-in, so the pasted
summary and repository evidence were used as the imported baseline; the
artifact itself was not represented as read.

**Starting revision:** `272d37c`. A fresh `git pull --rebase origin main`
reported that the clean `main` checkout was already up to date before edits.

**Research outcome:** Three independent primary-source passes covered radio and
protocol work, ESP/STM32 hardware and firmware, and adjacent drone stacks. The
result is now integrated directly into `PROJECT-ROADMAP.md`; no competing plan
was created. The roadmap adds a ranked, fully gated software-first queue
RQ1-RQ14 and a separate hardware queue while preserving R1 as the single active
implementation lane. Promotion requires an explicit roadmap status change and
named owners, dependencies, evidence target, and acceptance gate.

**Material corrections:**

1. CVE-2024-42483 is scoped to the higher-level `espressif/esp-now` component
   through 2.5.1, whether package managed or vendored, not every use of the
   base ESP-NOW API. A provenance/SBOM and feature-reachability manifest is now
   a mandatory prerequisite.
2. Espressif states that CVE-2025-27840 is not triggerable over Bluetooth,
   radio, or the Internet, affects only the original ESP32 chip, and is a
   second-stage hosted-HCI/physical-access concern. It was demoted from a radio
   pathway to reachability and patch attestation.
3. CRSF work now begins with differential parser conformance and Betaflight
   SITL, not an injection claim. Physical STM32 fault work begins with errata
   mapping and synthetic mutation tests, and the TinyHawk remains excluded.
4. The highest-value new research candidates are SigMF capture provenance and
   clock normalization, ELRS cross-version known-answer testing, CRSF parser
   conformance, and a generated read-only MSP API 1.45 contract backed by
   Betaflight 4.4.2 SITL.

**Safety boundary:** This pass used public documentation, official source,
standards, and read-only repository inspection. It did not access serial ports,
hardware, RF, private captures, credentials, firmware, external targets, or
state-changing services. Future hardware/serial/RF stages remain explicitly
gated in the roadmap.

**Changed files:** `PROJECT-ROADMAP.md` and this log.

**Validation:** Three independent research passes and one independent final
roadmap review completed. `python -B tools/validate-project.py` passed all 252
tracked-file checks, including syntax, JSON, corpus/provenance data, CVE
coverage, and local links. `git diff --check` passed.

**Commit/push status:** These documentation changes are prepared for a normal
commit and push to `origin/main`; no force push or history rewrite is used.

**Next task:** R1 remains active. RQ1 (capture corpus and timebase
standardization) becomes the first research candidate only after an explicit
owner decision moves R1 out of NOW; RQ2-RQ4 follow it.

---

## 64. Agent ownership split and R1 state-factory handoff (Codex, 2026-10-28)

**Starting revision and objective:** Work began at clean revision `bd1f649`.
`git pull --ff-only origin main` reported that `main` was already up to date.
The owner asked for the roadmap to be divided between Codex and Claude and for
Codex to begin its portion.

**Roadmap coordination:** `PROJECT-ROADMAP.md` now names a primary and
independent reviewer for every maintained and research task. The serialized
maintained order is R1 Codex, R2 Codex, R3 Claude, R4 Claude, R5 Codex, R6
Claude, and R7 Codex. Gated research rows RQ1-RQ14 carry dormant planned
assignments grouped by file/domain family. Those assignments do not activate a
task or relax its hardware, privacy, evidence, RF, or serial gate. R1 remains
the sole NOW lane.

**R1 implementation:** Replaced the dashboard's mutable module globals with a
documented `DashboardState` dataclass and Flask application factory. Each app
stores its own state in `app.extensions`; routes resolve the active app's state,
scan workers receive that exact state as an argument, and the SSE generator
captures it before request context ends. The default `app` object and command
line entry point remain compatible. Hardware detection now returns an uncached
snapshot that only the calling app stores. Simulated target templates are
deep-copied so nested details/lists cannot leak between instances.

**Regression evidence:** Added eight app-factory tests. They prove independent
locks and nested collections; concurrently mutate targets, events, actions,
scan state/defaults, hardware caches, and Chain 2 histories; verify worker
arguments belong to their originating app; exercise separate live-mode scan
defaults; verify SSE cursor isolation; and preserve the default imported app.
Existing API, browser, and dashboard fixtures now reset app-owned state rather
than rebinding detached module aliases. CI runs the factory suite and the full
web job on Ubuntu/Windows with Python 3.10 and 3.12.

**Validation:**

- Factory isolation suite: 8/8 PASS.
- API route suite: 38/38 PASS.
- Maintained dashboard suite: 52/52 PASS.
- Loopback Playwright/Edge browser suite: 11/11 PASS.
- Full tool suite: 648 PASS, 23 optional SITL tests skipped.
- `python -B tools/validate-project.py`: PASS (253 files).
- `git diff --check`: PASS (line-ending notice only).
- Three independent read-only reviews found no remaining R1 architecture,
  state-race, test-evidence, Python-3.10, or CI blocker after corrections.

**Safety boundary:** Work was limited to local code, deterministic simulation,
test clients, and a loopback browser server. No hardware, serial port, RF,
firmware, private capture, credential, external target, or live action was
accessed.

**Changed files:** `.github/workflows/validate.yml`, `PROJECT-ROADMAP.md`,
`web/app.py`, `web/tests/test_app_factory.py`,
`web/tests/test_api_routes.py`, `web/tests/test_dashboard_browser.py`,
`tools/tests/helpers.py`, `tools/tests/test_web_dashboard.py`, and this log.

**Commit/push status:** Prepared for one normal commit and push to
`origin/main`; no force push, rebase, branch switch, or history rewrite is used.

**Handoff:** Claude should independently review R1 against its acceptance
criteria after this commit is pushed. If that review passes, mark R1 DONE and
promote R2 as the single NOW task. Do not begin Claude-owned R3/R4/R6 or any
GATED research item from this entry alone.

---

## 65. Unified dashboard locking and bounded histories (Codex, 2026-10-28)

**Owner decision and roadmap state:** The owner reported that Claude is
temporarily unavailable and directed Codex to continue, with Claude's reviews
to be incorporated later. `PROJECT-ROADMAP.md` records that decision, accepts
R1 as DONE on its repository evidence and three independent read-only reviews,
and makes R2 the sole implementation lane for this work. After R2 validation,
R3 is promoted to NOW with a temporary Codex takeover; Claude's R1/R2/R3 roles
remain deferred retrospective reviews rather than being erased.

**R2 implementation:** `DashboardState.state_lock` is now the single
per-application re-entrant lock for every mutable field and nested object. The
old `scan_lock` and `events_lock` names remain compatibility aliases to that
same lock. Production writers complete state mutations under the lock; route
readers deep-copy the needed snapshot under it and serialize after release;
blocking detector, scanner, action, socket, and SSE-wait work remains outside
the critical section.

Events retain the newest 500 entries. Ordinary actions and Chain 2 injections
retain the newest 200 entries each through centralized helpers. Chain 2 action
and injection histories update atomically, and `last_injection` is derived from
the retained tail. A review found that an empty `commands: []` batch could have
cleared `last_injection` while an older injection remained; the helper and a
route regression now preserve the invariant.

**Regression evidence:** Added nine R2 tests covering concurrent event-ID
allocation and exact rollover, SSE replay after pruning, regular-action backing
retention and the 50-entry API window, oversized and empty Chain 2 batches,
mixed parallel ordinary/Chain 2 writers with concurrent readers, caller-data
detachment, and route snapshots that remain unchanged when the live nested
state mutates before serialization. A thread-ownership-aware lock proxy and
guarded lists deterministically reject append, extend, trim, clear, or reset
outside the application lock; the histories are prefilled to force rollover.
CI explicitly runs this suite in every Ubuntu/Windows and Python 3.10/3.12 web
job.

**Validation:**

- R2 history suite: 9/9 PASS, including 11 consecutive review runs.
- Maintained dashboard/API/factory/history set: 107/107 PASS.
- Loopback Playwright/Edge browser suite: 11/11 PASS.
- Full tool suite: 648 PASS, 23 optional SITL tests skipped.
- Python 3.10 grammar parse: PASS for changed Python files.
- `python -B tools/validate-project.py`: PASS (254 files).
- `git diff --check`: PASS (line-ending notice only).
- Two independent read-only R2 reviews report no remaining locking, deadlock,
  rollover, snapshot, test-evidence, Windows, Python 3.10, or CI blocker after
  the empty-batch and deterministic-test corrections.

**Safety boundary:** Work used local code, deterministic simulation, Flask test
clients, and a loopback-only browser server. It did not access hardware, serial
ports, RF, firmware, private captures, credentials, external targets, or live
actions.

**Changed files:** `.github/workflows/validate.yml`, `PROJECT-ROADMAP.md`,
`web/app.py`, `web/tests/test_state_histories.py`, and this evidence log.

**Commit/push status:** Prepared for one normal commit and push to
`origin/main`; no force push, rebase, branch switch, history rewrite, or broad
staging is used.

**Next task:** R3 is the sole NOW lane. Add lifecycle regressions for partial
startup failure and cleanup, plus any cross-instance leakage case not already
proved by the R1/R2 suites. Claude can retrospectively review the serialized
handoffs when its capacity returns.

---

## 66. Dashboard lifecycle isolation and failure cleanup (Codex, 2026-10-29)

**Owner decision and starting revision:** Work continued from clean revision
`373e65c`. The owner directed Codex to keep advancing ordinary software work
while Claude remains unavailable and reiterated that this lane is application
engineering rather than hardware or live-device work. R3 was therefore
completed under the temporary takeover already recorded in the roadmap, with
three independent read-only sub-agent reviews. R4 is now the sole NOW lane;
Claude's retrospective review remains deferred rather than removed.

**Lifecycle implementation:** `DashboardState` now tracks one scan generation
and worker identity per application. API admission reserves that lifecycle
under the application lock before thread construction, a route-owned wrapper
records bootstrap failures and always finalizes, and stale generations cannot
write into or release a newer scan. Direct simulated/live compatibility calls
claim the same lifecycle. Runtime reset is allowed only while quiescent,
clears every mutable runtime collection/status, preserves mode/default
configuration, and keeps event IDs monotonic so an already-open SSE stream
continues after reset.

**Factory and resource cleanup:** A state object can belong to exactly one
successfully created Flask app, while partial blueprint registration rolls its
claim back for retry. Background web scans opt out of main-thread-only signal
installation. `ChainLogger` creates at most one marked handler per logger even
under concurrent construction. Hardware-probe and MAVLink sockets close after
setup or bind failures, passive SDR streams deactivate and close after read
failure, and the browser fixture closes a partially started loopback server.
Route-test helpers now create fresh state instead of reusing the imported
singleton.

**Regression evidence:** Nineteen lifecycle tests cover factory ownership and
rollback, fresh fixtures, complete instance-local reset, API/direct worker
overlap, stale generations, malformed direct input, constructor/start/bootstrap
failure and retry, real background simulated/live completion, concurrent logger
creation, socket and SDR cleanup, and independent SSE streams. Existing route,
factory, history, dashboard, and browser fixtures were updated to use the same
quiescent cleanup contract. CI explicitly runs the lifecycle file on Ubuntu
and Windows with Python 3.10 and 3.12.

**Validation:**

- Maintained dashboard/API/factory/lifecycle/history set: 127/127 PASS.
- Lifecycle suite: 19/19 PASS locally; the independent test review also ran
  the preceding 18-test version 30 consecutive times without failure before
  the final deterministic malformed-input regression was added.
- Full tool suite: 648 PASS, 23 optional integration tests skipped.
- `python -B tools/validate-project.py`: PASS (255 files).
- Python 3.10 grammar parse: PASS for all nine changed Python files.
- `git diff --check`: PASS (line-ending notices only).
- Three independent final reviews found no remaining lifecycle, stale-worker,
  deadlock, cleanup, test-evidence, Windows, Python 3.10, or CI blocker.
- Browser E2E was not rerun in the disposable WSL environment because no
  Playwright browser is installed; it remains an explicit four-cell CI gate.

**Safety boundary:** This work used local source, deterministic simulation,
Flask test clients, mocked resources, and temporary offline Python packages.
It did not access hardware, serial ports, RF, firmware, private captures,
credentials, external targets, or live actions.

**Changed files:** `.github/workflows/validate.yml`, `PROJECT-ROADMAP.md`,
`tools/exploit_chains/common.py`, `tools/tests/helpers.py`,
`tools/tests/test_web_dashboard.py`, `web/app.py`,
`web/tests/test_api_routes.py`, `web/tests/test_app_factory.py`,
`web/tests/test_app_lifecycle.py`, `web/tests/test_dashboard_browser.py`,
`web/tests/test_state_histories.py`, and this log.

**Commit/push status:** Prepared for one normal commit and push to
`origin/main`; no force push, rebase, branch switch, history rewrite, hardware
action, or broad staging is used.

**Next task:** R4 is the sole NOW lane under the same temporary Codex takeover:
reconcile `web/README.md` and `tools/README.md` with executable commands,
current passive-decoder evidence, and explicit offline/passive/active behavior
boundaries, then obtain an independent documentation review.

---

## 67. TinyHawk MSP characterization and attack-model analysis (Claude, 2026-10-27 + 2026-10-29)

**Starting revision and objective:** Work began from `7820a7a` on the Lenovo
NUC13 desktop. The objective was read-only MSP characterization of the
TinyHawk III Plus on COM5 (USB VCP, VID 0483:5740, STM32 CDC) to complete the
CRSF/MSP pathway mapping and produce an attack-scenario model for the CTF.
This is research work outside the R0–R7 maintained-software backlog; it
contributes to the GATED RQ3/RQ4 evidence base but does not activate those
tasks. The session stalled on 2026-10-27 at a usage limit, then resumed on
2026-10-29 after the limit reset.

**Safety boundary:** All device interaction was read-only MSP queries over the
existing USB VCP connection. No writes, no CLI mode, no motor commands, no
arming, no RF transmission, no firmware changes, no bootloader entry. Props
were removed per standing procedure. The E8 on COM4 was not touched.

**Actions and evidence:**

1. **MSP identity probe** (Oct 27): Queried MSP commands 1–4, 10, 160, 101,
   36 on COM5. Confirmed: API 1.45, BTFL 4.4.2, board S411 (STM32F411),
   unified target EMAX_TINYHAWKF4SX1280, manufacturer EMAX, craft name
   "TH III Plus".

2. **MSP deep dump** (Oct 27): Read 28 MSP commands covering RX config,
   failsafe, serial ports, features, status, RC, motors, attitude, RC tuning.
   Encountered transient USB serial errors after 14 commands; recovered with
   port close/reopen retry logic. Key findings: `serialrx_provider=9` (CRSF),
   failsafe delay 0.5s.

3. **Aux safety snapshot** (Oct 27): Read mode ranges, adjustment ranges, RC
   map, and 3 consecutive RC samples. Decoded 5 active mode ranges and the
   TAER channel mapping.

4. **UID cross-validation** (Oct 27): Extracted UID from E8 vendor-read binary,
   confirmed UID[3:5] = 0x11:18:5b matches SYNC-extracted values from passive
   captures. Validated FHSS seed derivation and CRC init for both OTA v3 and v4.

5. **Attack profile generation** (Oct 27): Generated complete attack parameter
   set from UID: FHSS sequence, CRC init, sync channel, test SYNC packet.

6. **Extended MSP probe** (Oct 29, resumed session): Queried 33 MSP commands
   (32 responded). New findings:
   - **UART2 runs TBS SmartAudio (0x0800), not CRSF.** The CRSF link uses SPI
     bus 3 directly (`rx_spi_protocol=19`). This means the attack pathway is
     OTA → SX1280 SPI → ELRS stack in BF → CRSF → MSP handler, with no UART
     intermediary.
   - **RX config bytes 35–37 contain 0x11:18:5b** — UID[3:5] exposed through a
     third independent MSP command path (after vendor read and SYNC extraction).
   - PID values: ROLL P111/I198/D102, PITCH P116/I208/D116, YAW P111/I198/D0
     (stock EMAX TinyHawk III Plus tune).
   - Arming config: auto_disarm_delay=0, no angle limit. Battery config all
     zeros (USB-only, no battery connected).
   - MSP_MIXER_CONFIG (cmd 66) returned no response — possibly not in the BF
     4.4.2 MSP v1 command set.

7. **Nonce/timing analysis** (Oct 29): Analyzed all 16 SYNC frames from two
   captures. Confirmed 150 Hz packet rate by cross-validating nonce deltas
   against time deltas (e.g., nonce 64→66 in 0.013s = exactly 2 packets at
   150 Hz; nonce 3→64 over 2.113s = 317 packets, 317 mod 256 = 61). FHSS
   cycle is 240 hops at 1.6s, with sync channel (index 41, `freq_count/2+1`
   confirming ELRS ≤3.x formula) visited every 80 hops (0.533s).

8. **Mode range attack modeling** (Oct 29): Mapped all 5 active mode ranges
   to concrete µs values and OTA encoding:
   - **ARM:** AUX1 (ch4) at 1700–2100µs (steps 32–48). Currently disarmed at
     1000µs. Throttle must be <1050µs for initial arm.
   - **ANGLE:** AUX2 (ch5) at 900–1300µs. **HORIZON:** AUX2 at 1300–1700µs.
     Currently in ACRO (ch5=2000µs, above both ranges).
   - **Turtle mode:** AUX3 (ch6) at 1700–2100µs. Currently off at 1000µs.
   - **Anti Gravity:** AUX4 (ch7) at 1700–2100µs. Currently on at 2000µs.
   - In OTA v3 hybrid switch mode, AUX1 updates when `nonce % 8 == 1`
     (every 53ms at 150 Hz).

   Modeled four attack scenarios: force-arm, force-disarm, turtle-mode
   activation, and MSP-over-ELRS command injection.

**What worked:** All read-only MSP commands returned valid data on COM5. The
nonce analysis produced exact timing confirmation. The mode range decoding
matched the expected EMAX TinyHawk III Plus stock configuration.

**What remained inconclusive:** MSP_MIXER_CONFIG (cmd 66) did not respond;
motor values were garbage (USB-only power, no battery). The failsafe response
was only 3 bytes where BF 4.4.2 should return more — possibly a response
format difference or the FC omitting fields without battery.

**Private data (under ignored `work/tinyhawk-identity/`):**
- `msp-identity-2026-10-27.txt` — MSP identity output
- `msp-deep-dump-2026-10-27.json` — 28-command config dump with raw responses
- `aux-safety-2026-10-27.json` — mode ranges, RC map, RC samples
- `uid-derivation-validation-2026-10-27.json` — FHSS/CRC validation summary
- `attack-profile-2026-10-27.json` — complete attack parameter set
- `uid.hex` — UID hex string
- `msp-extended-probe-2026-10-27.json` — 33-command extended probe (saved Oct 29)
- `nonce-timing-analysis-2026-10-29.json` — nonce/FHSS timing analysis
- `mode-range-attack-model-2026-10-29.json` — attack scenario model

**Changed files:** `SESSION-NOTES.md` and private data under `work/`.

**Validation:** All MSP probes ran successfully against COM5. No writes were
issued. Private data is under ignored `work/` per AGENTS.md rule 7.

**Commit/push status:** This log entry is prepared for a normal commit and push
to `origin/main`. No force push, branch switch, or history rewrite.

**Relevance to GATED research pathways:**
- **RQ3 (CRSF conformance):** The serial port topology (SPI CRSF, no UART)
  establishes the internal data path for future CRSF differential testing.
- **RQ4 (MSP read-only contract):** The extended probe results provide ground
  truth for which MSP commands respond on this specific BF 4.4.2 build, which
  informs the allowlist for any future SITL comparison.
- **Attack scenarios:** The mode range model and nonce timing analysis are the
  source-derived + MSP-confirmed basis for any future OTA injection test, which
  remains GATED pending explicit approval and SDR hardware.

**Next steps (all GATED or deferred):**
- Betaflight 4.4.2 source analysis for MSP-over-ELRS packet handling (offline,
  from pinned source tarball — requires RQ4 promotion).
- Bench validation of forged-packet acceptance (GATED: requires SDR, battery,
  explicit approval, props-off plan).
- R4 documentation refresh continues as the active NOW lane (Codex ownership).
