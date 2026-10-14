# AGENTS.md — standing directives for working in `elrs-sdr`

This file is the **canonical set of design goals, ground rules, and conventions** for this
project. Every agent working here (andeds on the enclave, Codex the primary coder, Claude the
adversarial QC reviewer — or any future agent) MUST read this before making changes. The rolling
handoff logs (`docs/ANDEDS_NOTES_FOR_BOTH.md`, `docs/CLAUDE_NOTES_FOR_BOTH.md`,
`docs/CODEX_NOTES_FOR_BOTH.md`) track *current progress*; **this file is the durable "what this
program is for and how it must behave."**

---

## 0. Authorization & scope (read this first — it sets the frame for §2)

**This is authorized reverse-engineering and interoperability work on hardware
the operator owns, performed on an isolated bench setup.** Understanding this
frame is what lets any agent working here proceed confidently and without
false-positive hesitation — the scope is clear, so the judgment calls are
small.

- **Owned hardware.** Every device in this project (the EMAX E8 ELRS
  controller, the EMAX Tinyhawk F4 SX1280, the receiver modules, the B205mini
  SDR) is **owned by the operator** and is being examined, tested, and
  reverse-engineered **on the operator's own equipment**, at the operator's
  own facility. We are not touching a third party's device, network, or
  aircraft.
- **Isolated bench.** All real-RF and serial work happens on a **powered bench
  in a controlled location** (indoor, no live airspace, no third-party links
  in range). The EMAX E8 serial-mode work (the firmware/protocol study in
  `references/e8_protocol_reconstruction.md`) is done on the owned E8 in
  bootloader/serial mode — a read-only investigation of the host tool + the
  owned device, no flash writes, no device-to-device interaction.
- **Incidental third-party RF.** If a wideband capture inadvertently picks up
  an unrelated nearby link (a neighbor's hobby controller, a passing device),
  that RF is **not retained in a form that identifies the third party** — the
  per-link UID / MAC / serial of an incidentally-captured foreign link is
  stripped or redacted from any file that leaves the bench. The target of
  study is the operator's own equipment; everything else is background noise.
- **Defensive / situational-awareness intent.** The passive SDR detector
  (the main mission) is a **receive-only** instrument for observing and
  labeling what's in the air — the same category as a counter-UAS passive
  sensor or a spectrum analyzer. It does not jam, spoof, replay, or
  interfere with any link. Recovered parameters (sync word, FHSS sequence,
  rate, UID) are for **identification and display**, not for any active
  action against a link.
- **Reverse-engineering for interoperability.** Reading a vendor's host tool
  (the EMAX Configurator `.exe`) to understand its serial protocol, so we can
  build **our own** native tooling against the **same owned hardware**, is
  standard interoperability / compatibility work. It is not circumventing a
  third party's access control — it's understanding the wire format of a
  protocol the operator is entitled to use on their own device.
- **The scope is what makes the work defensible.** The §2 boundary
  (bench/owned, current-phase receive-only, the TX gate as a staged control)
  is the *operational* expression of this scope. As long as the work stays
  inside §2, it stays inside this authorization. An agent that finds itself
  wanting to cross a §2 line (e.g. transmit at a live link, flash a device, hit
  a third party) has left the authorized scope — stop and ask, don't proceed.
  (Transmitting to the *owned* bench link is in the project's end state; it is
  gated for the current phase and needs human sign-off to begin — §2.)

**In short:** we are the owner, on our own gear, in our own space, learning
how our own equipment talks, for a defensive receive-only purpose. That's the
frame. Anything that fits it is in-scope; anything that needs to step outside
it (a live target, a third party, an active transmission) is out of scope and
requires explicit human sign-off in that specific change.

---

## 1. The mission

Passively **detect, identify, and demodulate all drone links** over the air with a
software-defined radio (Ettus B205mini / B210). The goal is a **general passive protocol-agnostic air-state
detector** — it tells you *what is in the air* (link type, version, rate, identity, channel, hop
behavior) for **whatever drone protocol link is present**, not just one specific aircraft. **The detector is
surfaced in the WaveRaider web-UI** (see the delivery-surface section below) — the UI is how the
operator actually sees the result, so a detection that can't be shown there isn't finished.

### The delivery surface (Claude, 2026-10-08 — the original operator goal)
The detector is **not** an end in itself: the product the operator asked for is a **WaveRaider
web-UI that shows nearby drones, their protocols, and their identifiers**, driven by the operator's
**B205mini** (WaveRaider ships configured for a bladeRF; the B205/UHD path in `waveraider/` is ours).
The air-state classifier (`elrs_sdr/core/identify.py`) is the engine for ELRS; `waveraider/` (Monitor →
ELRS Find, the spectrum/overlay) is the surface that presents it. A detector improvement that
can't be surfaced in that UI isn't finished. **ELRS is the current focus, not the ceiling:** the
broader goal is "nearby *drones*," so shared scanner/overlay code must not bake in ELRS-only
assumptions — leave room for the other 2.4 GHz drone links WaveRaider also targets (DJI OcuSync
*presence*, Open Drone ID, etc...) rather than making them require a rewrite.

### ⚠️ The #1 design goal — GENERAL, not single-target (andeds, 2026-10-08 — REMEMBER THIS)
The identifier must work against **ALL drones, ALL ELRS versions, All other common protools, ALL UIDs, ALL rates/modes** —
**not just the one Tinyhawk we're testing against.** It is a *general* passive drone protocol air-state
detector, not a single-target finder. Concretely:
- **Never hardcode** one UID, one OTA version, one rate, or one target's sync word.
- **Measure and classify from the RF** — iterate all known OTA versions (v2–v6), all LoRa rates +
  FLRC/GFSK, **recover the sync word / UID from the air** (demod a burst, read the sync out of the
  packet — *never* require the operator to supply the UID; see the Identity goal), and report the
  full air state (version, mode, rate, UID, channel, freq-offset) for *whatever* ELRS link is
  present.
- The Tinyhawk (EMAX TINYHAWK F4 SX1280) is only the **current test target**; the code must stay
  **target-agnostic**. A change that makes the detector work *only* for one drone's UID/version is
  a regression, not an improvement.
- This is the "identify everything, be deliberate" directive — implemented in
  `elrs_sdr/core/identify.py` (the air-recovery path is the next build step there — see §6).

### The core philosophy (emphatic, from andeds)
The program must **MEASURE + CLASSIFY from the RF and DISCOVER the air state** — it must **NEVER
assume** the target's rate, PHY, or settings. "The point is to **find out** and discern the
differences." Build **discriminators** (measurements that tell the cases apart), not
**assumption-dependent decoders**. Every new detector/feature should be testable on a capture
*without* being told the answer in advance.

### Identity goal
The end-state identity is a **hardware-unique ID per drone** (MAC/IMEI-style), found by
**SDR-only 2.4 GHz RF** (802.11 MAC/SSID, or the ELRS link's identity where it exists). The ELRS
6-byte **bind UID is NOT this** — it is a *pairing secret*, not a hardware identity, and the ELRS
air protocol carries no hardware ID. (See `elrs_sdr/posid.py` + `docs/DRONE_RF_ARCHITECTURE.md`
for the ID-architecture analysis.)

**The identifier we can actually recover passively is the sync word / FHSS sequence** (both
UID-seeded). For a link bound with a **binding phrase**, the UID — and therefore the sync word — is
`MD5(phrase)[:6]`, computable offline. For a link bound the **traditional (3-click) way with NO
binding phrase** (confirmed to be the operator's Tinyhawk, 2026-10-08), the UID is a random
per-module value exchanged at bind time: **not computable, and not readable from Betaflight.** So
the general method must be **recover-from-the-air** — demodulate a burst, read the sync word out of
the packet, and use *that* as the per-link discriminator — never "ask the operator for the UID."
A detector that *requires* the UID up front cannot work against an arbitrary drone and violates the
#1 (general, not single-target) goal.

### The target protocol catalog + the specific-protocol bar (from the reference C-UAS SDR)
**Naming directive (operator):** the reference device is **"the counter-UAS passive SDR."** Never
use its vendor/make name, or any acronym/abbreviation of it, in notes, code, filenames or commit
messages.

A shipped, passive, library-based counter-UAS SDR (manual + firmware provided by the operator as a
reference; see `docs/REFERENCE_COUNTER_UAS_SDR.md`) scans for **16 named drone protocols**. That is
our **target list**, encoded as tracked code in **`elrs_sdr/protocol_catalog.py`** (run
`python3 -m elrs_sdr.protocol_catalog` for the live gap report). Two directives follow from it:

1. **Climb to the SPECIFIC-protocol level, not the family level.** The reference device lists
   **ExpressLRS**, **Crossfire** and generic **LoRa** as *three separate protocols*, even though
   ELRS is a fork of Crossfire and both are LoRa-PHY. So specific-protocol fingerprinting is a
   solved problem in a real product. Our `positivedet.lora_chirp` is at the **family** level
   ("LoRa (ELRS/Meshtastic/sub-1G)") — that is a named gap, not a finished feature. Closing it
   needs payload/timing fingerprinting (packet cadence, sync/CRC layout), not just chirp presence.
   The `DetectionLevel` ladder is: `NONE → PRESENCE → FAMILY → SPECIFIC → IDENTIFIED`.
2. **Extract and display a per-link identifier, like the reference does.** It whitelists "by UID"
   and offers "Copy Net ID to Clipboard" — i.e. it pulls a per-platform ID out of the detected
   link. That is proof the operator's "identify a drone by a unique ID from the RF" goal is a
   shipped capability, not a fantasy. Per protocol that means: **ELRS → the air-recovered sync
   word/UID; WiFi → the BSSID/MAC; DJI → the serial/DroneID.** Today only **WiFi** reaches
   `IDENTIFIED` (validated L-SIG decode + beacon MAC); keep that honest in the catalog.

**Honesty rule for the catalog:** `our_level` records what works on a REAL capture, never what a
code path exists for. ELRS stays at `FAMILY` precisely because every real burst still returns
`fsk_unidentified` with no CRC-valid lock. Mark synthetic-only detectors `validated=False`.

---

## 2. The hard boundary (safety — no exceptions)

**Scope framing (updated 2026-10-14):** transmitting is a **stated project goal**
— the end state includes a working TX capability. The current **receive-only**
posture is a **phase** (finish passive detect/identify first), *not* the
mission. So the TX code that exists (`tx_tune`/`tx_stream` in `radio.py`, the
CLI `tx` command) is **working toward the goal, not out of scope.** The
*current-phase* control below is a **staged gate**: it keeps the tool passive
*for now* and must be deliberately lifted (by explicit human sign-off) as the
TX phase begins. Nothing in this section means "TX is out of scope" — it means
"we are not transmitting yet, and the gate is what enforces that."

- **Current phase: passive detect / decode / identify only.** The tool does not
  transmit, flash, or write persistent device state **in this phase.** The
  receive-only behavior is a phase gate, not a permanent scope limit.
- **Bench/owned aircraft only (every phase).** All real-RF and serial testing is
  on andeds's **own** hardware, on the bench, with the radio present — **never**
  a live, in-the-air, or third-party link. This holds for RX and for the future
  TX phase alike.
- **The TX gate (staged control — current phase).** In the *current* phase the
  TX path in `elrs_sdr/radio.py` (`tx_tune`/`tx_stream`) is **gated,
  bench-only, and not exercised.** Enabling a transmit, flashing a device, or
  making a persistent device change requires **explicit human sign-off in that
  specific change** — never an agent's own initiative, and never justified by a
  downstream goal. As the project moves to the TX phase, the gate is re-evaluated
  and re-tightened to a proper sign-off mechanism (the current
  self-asserted-CLI-flag gate is a known weak point, flagged as **QC-001** — to
  be replaced, not removed, when the TX phase starts). The passive detector /
  analysis / web-UI paths must **never** wire in the TX side — a detection or UI
  flow reaching `tx_*` is still a regression.
- **Defensive / situational-awareness intent (current phase).** The current tool
  is a *receive-only* detector: observe and label what's in the air. Recovered
  link parameters (sync word, FHSS sequence, rate) are for
  identification/display, **not** for jamming, spoofing, replay, or interference
  with any link in this phase. (This is a current-phase statement; the intent
  for the TX phase is set when the TX phase starts.)
- **Same line, all sides.** Every agent (andeds, Codex, Claude) holds this
  boundary. It is not negotiable per-task.

---

## 3. The honesty contract (how we label results)
Every finding is labeled with its **evidence class** — never overclaim:
- **VALIDATED** — checked against firmware / datasheet / a real capture.
- **SELF-CONSISTENT** — round-trips our own code (encodes + decodes to the same value) but not
  yet proven against a real source.
- **SYNTHETIC** — offline only; the code produces plausible output but hasn't seen a real signal.

The **LoRa demod is the single layer that can't be proven on a desk** (Semtech-proprietary
bit-mapping, BPSK differencing, interleaving). Everything *above* it (CRC, OTA model, UID init,
channel pack/unpack, FHSS) is **VALIDATED** (exact ports of the ELRS source, self-tested). The
**GFSK/FLRC front-end is SYNTHETIC** until a real capture validates it. A "detected" that isn't
labeled with its evidence class is an overclaim — flag it.

### The false-positive problem (the thing that keeps biting us — be paranoid here)
Passive ELRS detection over a busy 2.4 GHz band (Wi-Fi everywhere) has **three known
false-positive classes**. A "hit" is NOT a real link until it survives all three:
1. **Non-UID-specific correlation** — a high correlation peak that the *wrong* UID matches just as
   well (e.g. ch28 in the 120433 capture: right-UID 0.1732 vs wrong-UID 0.1749). A real ELRS sync
   is **UID-seeded**, so the correct UID should correlate *far* above a wrong one. **In air-recovery
   mode (no UID supplied — the general case) this prior-knowledge test isn't available**, so the
   discriminator shifts to: does the *recovered* sync, used as the reference, produce a **CRC-valid
   frame** at a consistent rate/channel? A peak that doesn't yield a CRC-valid decode on the
   recovered reference is the false-positive, regardless of how tall the correlation was.
2. **The LoRa CRC false-accept** — the first-order LoRa demod mis-decodes a *non-LoRa* signal (an
   FSK burst, noise) as LoRa frames at *every* rate, producing ~20-35 CRC-"valid" frames each. A
   **real** LoRa link is on **ONE rate** with **hundreds+** of frames. ~20-35 on all 6 rates =
   false-accept. (Gated in `identify.py`.)
3. **The dense-Wi-Fi LoRa preamble false-positive** — the autocorrelation-based preamble detector
   fires on ~every sample when Wi-Fi is present (~48k hits, 0.00 ms median gap). A **real** LoRa
   preamble shows **sparse, discrete** chirp hits. (Gated in `identify.py`.)

**The decisive real-hit discriminator is a CRC-valid decode on the recovered (or supplied) sync
reference** — a genuine ELRS frame passes the CRC in a way a raw correlation peak does not. With a
supplied UID that's "CRC with the correct (version, UID)"; in air-recovery mode it's "CRC with the
sync we recovered from the air, at a consistent rate/channel." When in doubt, report the candidate
set + the evidence class, not a confident "found it."

---

## 4. Ground rules (workflow — all agents)
- **One branch: `main`.** Pull before you work, push after. No feature branches, no `master`/
  `execfix` (those are gone; everything is on `main`).
- **Captures sync via git.** `captures/` is tracked (full IQ `.complex8` + `.meta.txt`, re-included
  in `.gitignore`). The **pre-push hook auto-commits `captures/`** on every `git push` (so a
  capture you took is pushed without manual `git add`). Old noise captures are periodically pruned
  to control size.
- **No test scaffolding in the repo for one-off validation** — but *fixing stale/incorrect* tests
  in the existing suite is fine (project quality, not throwaway).
- **Enclave (andeds) can't run the WaveRaider pytest** (closed network, no PyPI). So andeds
  verifies test-logic changes by simulation; the **real pytest run is the laptop's job**.
- **Enclave has NO SDR.** andeds (enclave) writes code + does **offline analysis of captures the
  laptop uploads**. Claude (laptop) runs the real RF (B205, sweeps, ELRS Find). **Never claim the
  enclave "detected a live link"** — run detectors offline on uploaded IQ only.
- **Commit hygiene:** one logical change per commit; message = what + why (not "fix stuff").
  `find . -name __pycache__ -type d -exec rm -rf {} +` before committing (no `__pycache__` in git).

---

## 5. Code conventions
- **`elrs_sdr/core/` is the single source of truth** for the ELRS decode (exact reference ports of
  the ELRS source). Protocol-layer changes go here first; the `waveraider/` integration builds on
  it, never re-derives it.
- **The ELRS protocol is version-parameterized.** `flrc_sync_word(uid, ota_version)`,
  `ota_uid_crc_init(uid, ota_version)`, `bind_crc_init(ota_version)` all take the OTA version —
  the detectors must **iterate the versions** (see `identify.OTA_VERSIONS`), not hardcode `4`.
- **Match the existing style.** The codebase is type-hinted Python (dataclasses, `from __future__
  import annotations`). New modules follow the same pattern (see `identify.py` as the recent
  template: dataclass results + a top-level `identify()` entry point + a full candidate list).
- **Measure, don't assume.** Every detector should take a capture and *derive* the parameters
  (rate, version, offset, UID) from it, rather than taking them as a required input. The **UID /
  sync word is recovered from the air as the primary path** (demod a burst, read the sync out of the
  packet) — a supplied UID is a *convenience cross-check*, never a required input. A detector that
  *requires* the UID up front can't work against an arbitrary drone and violates the #1 goal.
- **Always analyze at the MEASURED sample rate, never the requested one.** The B205 over USB
  delivers slightly (or, on a bad link, much) under the configured rate — e.g. ~4.88 MS/s for a
  requested 5 MS/s. Each capture's `.meta.txt` records the **measured** `sample_rate_hz`; every
  analyzer/detector must read and use that (as `analyze_capture.py` / `identify.py` do), because
  the wrong rate stretches the time/frequency axes and breaks every downstream measurement
  (bandwidth, deviation, symbol rate, offset).
- **The burst frequency offset is real.** ELRS bursts are off-center from the channel center
  (measured +0.32 MHz on ch31, +1.65 MHz on ch40 in the 120433 capture, due to channel overlap at
  ~4.88 MS/s / 1 MHz spacing). Detectors must **measure + frequency-shift to the burst's actual
  offset** before correlating/demodulating — correlating against the assumed center is the bug
  that made the early v4 sync "not lock."

---

## 6. The current state (as of 2026-10-08)
- **USB capture: FIXED.** The B205 drop issue (the `D`/`DDOD` indicators) is resolved by the
  deeper recv ring + per-channel `_stop_stream()` in `elrs_sdr/radio.py`. Two clean 80/80-channel
  sweeps (~4.88 MS/s, 12.7 s, zero drops) confirm it. No powered-hub purchase needed.
- **The "identify everything, all versions" classifier is BUILT + verified:**
  `elrs_sdr/core/identify.py`. It measures the burst physics (bandwidth, amp-CV, duty, freq-
  offset), frequency-shifts to baseband, iterates all OTA versions (v2–v6) × UID for GFSK + all 6
  LoRa rates, gates on CRC-validity, and returns a full `AirState`. **Current status:** it detects
  + measures the ELRS-shaped FSK burst in the clean capture and reports `fsk_unidentified` — it
  *detects and characterizes* the burst but doesn't yet **read the sync word out of it**. The next
  build step (per the air-recovery method in §1/Identity goal) is to **demodulate a clean burst
  (ch31/ch40 in the 120433 capture) and extract the sync word from the packet** — that gives both
  the modulation confirmation (FLRC vs Bluetooth) and the per-link identifier, without needing the
  UID up front.
- **Correction (Claude, 2026-10-08): the UID can NOT just be read from Betaflight.** The operator's
  Tinyhawk has **no binding phrase** (traditional 3-click bind), so the UID is a random per-module
  value — not in the Configurator, not computable. The ELRS **version + packet rate** live on the
  ELRS side too (the radio's ExpressLRS Lua, or the receiver's WiFi page at `http://10.0.0.1`), not
  in Betaflight (which only speaks CRSF). So "get the UID from Betaflight to lock the sync" is not a
  viable path for this link. The real path is **air-recovery**: `identify.py` already measures the
  burst (bandwidth, CV, duty, offset) and iterates versions/rates; the remaining work is to
  **demodulate a clean burst (ch31/ch40 in the 120433 capture) and read the sync word out of the
  packet** — giving both the modulation confirmation (FLRC vs Bluetooth) and the per-link identifier
  without needing the UID up front. The ELRS version is still *useful* (it fixes the packet format)
  but is no longer a blocker — try the iterable versions against the demodulated bits.
- **`elrs_sdr.selftest` ALL PASS.**

---

## 7. Key files
- `elrs_sdr/core/` — the ELRS decode (crc, ota, fhss, lora, gfsk, **identify** = the classifier)
- `elrs_sdr/radio.py` — UHD device wrapper (RX + gated TX) + `.complex8` I/O
- `elrs_sdr/dronescan.py` — multi-band presence + protocol scanner
- `elrs_sdr/protocol_catalog.py` — the **16-protocol target list** from the reference C-UAS SDR +
  our honest `DetectionLevel` per protocol and which per-link ID we can extract
  (`python3 -m elrs_sdr.protocol_catalog` prints the gap report)
- `elrs_sdr/detect.py` — the **detection + identification engine** (the software port of the
  reference C-UAS SDR's radio pipeline; see `docs/COUNTER_UAS_SDR_DETECTION_ID.md` for the
  algorithm + the stage-by-stage mapping). It finds the bursts (hysteresis energy detection +
  the continuous-carrier discriminator), runs the per-protocol demod + per-link Net ID
  extraction (the `Detection`/`NetID` dataclasses mirror the reference's `DetectionTaskResult` /
  `DetectionNetID`), and does the specific-protocol fingerprint (ELRS vs Crossfire vs LoRa).
  `python3 -m elrs_sdr.detect selftest` runs the synthetic selftest;
  `python3 -m elrs_sdr.detect <capture.complex8> [srate_hz] [freq_hz]` runs it on a real capture.
- `docs/COUNTER_UAS_SDR_DETECTION_ID.md` — **how the reference C-UAS SDR detects + identifies**
  (the 5-stage pipeline: channelizer → hysteresis detect → sync correlation → per-protocol demod
  → Net ID extraction; the `Protocol`/`DSPCore` enums; the `ScanConfigFSK` ELRS/FLRC/Crossfire
  rate catalog; the per-protocol `Detect` tables; the `DetectionNetID`; the false-alarm gates;
  the ECM side; + the `elrs_sdr/detect.py` implementation mapping). This is the "how it works"
  doc; `docs/COUNTER_UAS_SDR_FIRMWARE_DEEP_DIVE.md` is the "how it's built" (architecture) doc;
  `docs/REFERENCE_COUNTER_UAS_SDR.md` is the "what it is + how to use it" (operator-manual) doc.
- `docs/REFERENCE_COUNTER_UAS_SDR.md` — the reference counter-UAS passive SDR notes (protocol
  catalog, Net-ID/UID extraction, passive/active split, + the firmware container breakdown)
- `analyze_capture.py` — the offline capture analyzer (channel-by-channel, all modes, UID-specific
  + generic scores; the false-positive warnings live here)
- `waveraider/waveraider/protocols/elrs/` — the live-capture / web-UI integration (`sweep_phy`,
  `sync_dwell_phy`, `_decode_lora`, the GFSK path)
- `waveraider/` (Monitor → ELRS Find, the spectrum/overlay) — **the delivery surface** (the web-UI
  the operator sees; the air-state classifier's output is presented here). A detector improvement
  isn't finished until it's surfaced in this UI.
- `docs/FLRC_GFSK_DECODE.md` — the canonical FLRC/GFSK design + status
- `docs/DRONE_RF_ARCHITECTURE.md` — the RF-identity architecture (the "what ID do we actually get
  from the air" analysis)
- `docs/ANDEDS_NOTES_FOR_BOTH.md` / `docs/CLAUDE_NOTES_FOR_BOTH.md` /
  `docs/CODEX_NOTES_FOR_BOTH.md` — the rolling handoff logs (current progress;
  this file is the durable directives). Each agent keeps their own
  `..._NOTES_FOR_BOTH.md`; the older `ANDEDS_NOTES_FOR_CLAUDE.md` /
  `CLAUDE_NOTES_FOR_ANDEDS.md` are kept as history.

---

**When in doubt:** measure from the RF (at the measured rate), recover the sync/UID from the air
(never ask the operator for it), label the evidence class, keep it general (not single-target),
surface it in the WaveRaider UI, stay passive *for the current phase* (the TX gate is a staged
control — bench-only + human sign-off; see §2), and never overclaim a detection.
