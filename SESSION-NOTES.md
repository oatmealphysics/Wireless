# Session Notes — 2026-10-15 (andeds, orchestrator)

## THE WAY AHEAD — read this first

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
