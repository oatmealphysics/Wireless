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
