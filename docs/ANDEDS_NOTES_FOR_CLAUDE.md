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

## 2026-10-08 (andeds) — Read the reference counter-UAS SDR manual + firmware (the "BC.pdf" / ".swu"); notes in docs/REFERENCE_COUNTER_UAS_SDR.md

The operator provided a **reference counter-UAS (C-UAS) passive SDR** (a PDF user manual + a
firmware `.swu` update) in `/opt/data/Drone_Project/references/` — as a *reference* for our own
passive ELRS/drone detector. **Naming directive: call it "the counter-UAS passive SDR" — NEVER
"BC," "Bal-Chatri," "BC2," or any vendor/make name.** Full notes in
**`docs/REFERENCE_COUNTER_UAS_SDR.md`** (the durable doc). Highlights:

- **It's a passive, library-based SDR** for detecting Type I/II COTS drones (receive-only in its
  base role; the *mitigation* is a **separate paired ECM device** — Egon II / Modi — not this
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
- **The firmware (`bc2-4.0.6.swu`, 195 MB) is where the actual detection algorithms live** — the
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

*(older andeds entries go below this line as the log grows)*

## 2026-10-07 (andeds) — initial handoff doc created

This file started as a rolling andeds→Claude handoff. See the entries above. Earlier context:
the WaveRaider ELRS Find integration (sweep + classify + save + live progress), the first bench
capture analysis (no target-UID GFSK link found — radio likely not TXing / different rate-UID),
the `master`/`execfix` branch reconciliation (→ one `main`), and the push-permission fix. Full
detail in `docs/ELRS_FIND_UPDATE_LOG.md` + `docs/FLRC_GFSK_DECODE.md`.
