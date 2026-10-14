# Drone RF Identification Project — Full Conversation Summary

A start-to-finish account of the work, from the first reference-codebase study
through the WaveRaider integration, the air-recovery measurement work, and into
the EMAX E8 controller protocol study. All work is on the operator's own
hardware, on an isolated bench, receive-only for the RF side.

---

## Phase 0 — The starting point (reference-codebase study)

The project began with a request to **summarize the ELRS (ExpressLRS) + LoRa
reference codebases** for a drone-protocol-identification SDR project —
specifically, what's **reusable** to build positive protocol detectors and
extract concrete protocol parameters.

The reference corpus (`drone_refs/_ext/`) held the upstream source:
- **ExpressLRS** (the control-link firmware — TX + RX), the OTA packet format
  (`expresslrs_packet_s`, the OTA4/OTA8 layouts), the CRC scheme, the FHSS
  LCG, the sync-word derivation.
- **Betaflight** (the drone flight-controller side — CRSF, not the radio link).
- The Ghost-RX variants, CR-HLA, and other ELRS forks.

The reusable core that came out of this study became `elrs_sdr/core/` — the
validated numeric heart of the project (exact ports of the ELRS source):
- `crc.py` — the CRC16 the ELRS frames use (with the test vectors).
- `ota.py` — the OTA packet model (version-parameterized: v2–v6).
- `fhss.py` — the FHSS hop-sequence LCG (the channel-hopping pattern, seeded
  by the link UID).
- `lora.py` — the LoRa PHY demod (the Semtech-proprietary bit-mapping, BPSK
  differencing, interleaving — the one layer that can't be proven on a desk).
- `gfsk.py` — the GFSK/FLRC front-end.

The key design insight from this phase (and the standing goal that drove
everything after): **the identifier must work against ALL drones, ALL ELRS
versions (v2–v6), ALL UIDs, ALL rates/modes** — not just the one drone being
tested. The program must **measure + classify from the RF and discover the air
state**, never assume the target's rate/PHY/settings. The Tinyhawk was always
just the *test target*, never the scope ceiling.

---

## Phase 1 — The first UI + the B205 capture decode

The first delivery was a **web UI** that surfaced what the SDR was seeing,
driven by the operator's **Ettus B205mini** (a USRP, accessed via UHD/SoapySDR).
The UI showed a spectrum sweep + a table of detected signals, so the operator
could *see* what was in the air rather than reading a log.

In parallel, the **first real capture** was taken: the operator's B205 recorded
the 2.4 GHz band where their controller + drone link were active. The capture
work established the practical constraints that shaped the rest of the project:
- **The measured sample rate is not the requested rate.** The B205 over USB
  delivers slightly (or, on a bad link, much) under the configured rate — e.g.
  ~4.88 MS/s for a requested 5 MS/s. Every analyzer had to read the *measured*
  `sample_rate_hz` from the capture's `.meta.txt`, not assume the configured
  value (the wrong rate stretches the time/frequency axes and breaks every
  downstream measurement).
- **The burst frequency offset is real.** ELRS bursts are off-center from the
  channel center (measured +0.32 MHz on ch31, +1.65 MHz on ch40, due to channel
  overlap). The correlator/demod had to **measure + frequency-shift to the
  burst's actual offset** before matching the sync — correlating against the
  assumed center was the bug that made the early sync "not lock."

The USB-capture reliability was a real early problem (the `D`/`DDOD` drop
indicators) — resolved by a deeper recv ring + per-channel `_stop_stream()` in
`elrs_sdr/radio.py`. Two clean 80/80-channel sweeps (~4.88 MS/s, 12.7 s, zero
drops) confirmed it.

The **identity goal** was clarified in this phase: the end-state identity is a
**hardware-unique ID per drone** (MAC/IMEI-style), found by SDR-only 2.4 GHz RF.
The ELRS 6-byte **bind UID is NOT this** — it's a *pairing secret*, not a
hardware identity, and the ELRS air protocol carries no hardware ID. The
identifier that *can* be recovered passively is the **sync word / FHSS sequence**
(both UID-seeded). For a link bound the traditional 3-click way (no binding
phrase — the operator's Tinyhawk's case), the UID is a random per-module value
exchanged at bind time: **not computable, not in Betaflight, not readable from
the config**. So the general method had to be **recover-from-the-air** —
demodulate a burst, read the sync word out of the packet, and use *that* as the
per-link discriminator. A detector that *requires* the UID up front can't work
against an arbitrary drone.

---

## Phase 2 — The "moving to WaveRaider" fusion

The project's biggest architectural pivot: **WaveRaider became the base.**
WaveRaider is a 148-file, MIT-licensed SDR web application (spectrum,
demod, DJI DroneID, Remote-ID, a signature knowledge base, an evidence UI).
The decision was to **tack on our SDR stuff only as necessary** to catch
signals WaveRaider doesn't already cover — no re-architecture, no touching
what works.

The detailed integration plan (`docs/WAVERAIDER_FUSION_PLAN.md`) identified
two gaps:
1. **A UHD/SoapySDR capture backend** — WaveRaider detects radios by `lsusb`
   model-substring and has rtl/hackrf/bladerf/signalhound/soapy backends, but
   **not the B205mini (Ettus USRP/UHD)**. The plan added a UHD backend behind
   WaveRaider's existing `sweep(start, stop, bins)` contract (power sweep for
   the spectrum view + IQ capture for decode), reusing our `elrs_sdr/radio.py`
   UHD-binding shim (it handles the 4 pybind variants + the stream-start fix).
2. **An ELRS decode + PHY detector plugin** — WaveRaider listed ELRS as
   "tier-1 decodable" in its knowledge base but had **no decoder**. The plan
   ported our validated `elrs_sdr/core` (LoRa/FHSS/CRC/OTA) in as a WaveRaider
   **protocol plugin** + a **sweep PHY detector** (a cheap presence hint, not a
   detection — matching WaveRaider's `rf_candidates` contract), so an ELRS
   control link shows as a decoded entity, not a power spike.

The plan was deliberately **generic-by-design**: the ELRS work was the first
instance of a repeatable per-protocol pattern
(`protocols/<name>/{decoder,detector,classifier,__init__}.py` + one line in
`_PLUGINS`), so "go after every drone and protocol" became a checklist
(ELRS → Crossfire → DSMX → FrSky ACCST → Futaba → DJI), not a rewrite.

**The delivery surface** was fixed in this phase: the product is the
**WaveRaider web-UI** (Monitor → ELRS Find, the spectrum/overlay). A detection
that can't be shown there isn't finished.

**Merge discipline** (established here, still in force): Claude owns
`core/lora_payload.py` + `fhss*.py`; andeds owns `detect.py` + the
`ANDEDS_NOTES*.md` handoff log. Shared files require coordination.

---

## Phase 3 — Decoding more: the air-recovery measurement work

The core hard problem: **reading the sync word out of a demodulated burst** to
get the per-link identifier without the UID up front (the "air-recovery" path).
This was where the two-agent collaboration (andeds on the enclave, Claude on
the laptop) did its heaviest lifting, against real wideband captures.

The **air-state classifier** (`elrs_sdr/core/identify.py`) was built +
verified: it measures the burst physics (bandwidth, amp-CV, duty, freq-offset),
frequency-shifts to baseband, iterates all OTA versions (v2–v6) × UID for GFSK
+ all 6 LoRa rates, gates on CRC-validity, and returns a full `AirState`.

A pivotal exchange: Claude ran `identify.py` on a real 28 MS/s wideband capture
(the operator's `2026-10-08_13-06-21_2450mhz_pred.cdif`) and found **3 bugs in
the measurement layer** — all in andeds's code:
1. **Positive-half-only spectrum** — used `fft(x)[:nfft//2]` for a *complex
   baseband* burst whose energy spans `-srate/2..+srate/2`, halving the
   bandwidth + forcing the offset always-positive. Fix: `_centered_spectrum()`
   (fftshift, full band).
2. **`freq_offset_hz` used argmax (peak bin)** — for a 2-tone FSK burst the
   peak sits *on a tone*, so the offset was wrong by ~the deviation (~365 kHz).
   Fix: power-weighted **centroid** of the -10 dB occupied band (lands between
   the tones = true carrier).
3. **`burst_core` returned a fixed 1 ms window** — ELRS bursts are often <1 ms,
   so the fixed window padded the burst with noise floor → amp-CV → ~2.0 →
   tripped the `amp_cv < 0.35` gate → **rejected the strongest, cleanest
   bursts** (a 53.7 dB burst rejected with ampCV=1.99). Fix: **adaptive**
   (grow outward from the envelope peak while within -6 dB, capped at burst_ms).

Andeds **verified all 3 fixes in the actual code** (not just the commit
message — the standing rule "verify Claude's claimed fixes in the code, not the
notes") + ran the selftest (ALL PASS). Measured effect: the 53.7 dB burst went
from ampCV 1.993 → 0.092 (now `is_signal=True`); offsets correctly signed;
bandwidth no longer halved.

**Honest status at this point (the standing honesty contract):** the
*measurement layer* was now correct on real wideband IQ, but **identification
was still blocked** — every burst was `fsk_unidentified`, `crc_valid=False`,
zero gfsk_hits (the sync is UID-seeded, no UID obtainable), zero lora_hits.
The measurement was fixed; the **sync-word air-recovery** (actually reading the
sync out of the demod) was the remaining work.

The **false-positive problem** was a recurring theme — passive ELRS detection
over a busy 2.4 GHz band (Wi-Fi everywhere) has three known false-positive
classes (non-UID-specific correlation, the LoRa CRC false-accept, the
dense-Wi-Fi preamble false-positive). A "hit" is not a real link until it
survives all three; the decisive discriminator is a **CRC-valid decode on the
recovered (or supplied) sync reference**. Every result is labeled with its
evidence class (VALIDATED / SELF-CONSISTENT / SYNTHETIC) — a "detected" that
isn't labeled is an overclaim.

The **per-link identity** was validated on a real capture: **net_id
`00111858`** (recovered from the air, cross-checked against the link). This was
the "it actually works on a real signal" milestone for the ELRS side.

---

## Phase 4 — The Live Detections table + "Drones nearby"

The operator wanted the **real-time table of items being seen** — prominent,
always visible, not buried behind a tab. This became the **Live Detections
table** (commit `f5a0f58`) in the WaveRaider UI: a prominent, always-on view of
the currently-detected links, each row showing the protocol, the per-link
identifier, the channel/rate, and the evidence class. A **"Drones nearby"**
filter was added to focus the table on aircraft-relevant links. This was the
delivery surface the whole project was building toward — the place where a
detection becomes "finished."

**Git hygiene** milestone in this phase: 503 capture files (8.9 GB) were
untracked from the git index; `captures/` is synced via a pre-push hook
(`scripts/git/pre-push`, installed per-clone by `scripts/git/install-hooks.sh`)
that auto-commits the captures on every `git push` (so a capture you took is
pushed without manual `git add`). No history rewrite (no force-push per
AGENTS.md). **Current state (2026-10-10, operator request):** `captures/` is
now blanket-ignored in `.gitignore`, so the hook is a no-op for new captures
by default — a specific capture is shared with an explicit
`git add -f captures/<file>`.

---

## Phase 5 — The gear-shift: the EMAX E8 controller protocol study

The operator shifted to a **new project**: the **EMAX E8 ELRS controller** (the
TX side — the handheld, not the drone). The goal: understand the E8's serial
protocol well enough to build **our own native tooling** against it (no vendor
tool, no Wine). The E8 is an EMAX fork of ExpressLRS on a different MCU family
— it's **not** in the upstream ELRS target list, so its protocol had to be
recovered from the vendor's host tool + live captures.

**Standing conventions for this phase** (user-mandated):
- **Framing:** all notes/commits/docs use "firmware analysis" / "protocol study"
  / "input handling assessment" language. (The underlying objective is
  understanding the controller's input handling; the notes describe the
  technical steps, not a named offensive goal.)
- **Notes discipline:** every commit/push to `elrs-sdr` MUST include the
  detailed notes entry in `docs/ANDEDS_NOTES_FOR_CLAUDE.md` in the **same
  commit** (code + notes together, no follow-up notes commits).
- **Data hygiene:** no vendor/system names in notes; no emojis; delete
  binaries after analysis.
- **Read-only:** no flash / no config-write / no device-modifying command until
  the parser is understood + a human approves a specific write.

### 5a. Getting the artifacts
- The **23.8 MB "actual download"** (`EMAX E8 ELRS Configurator.exe`) —
  **Enigma Virtual Box packed** (a *file-storage* packer that bundles the exe +
  Qt DLLs for distribution, **not** code encryption). Unpacked by `evbunpack`
  to the inner exe + Qt5 runtime DLLs.
- The **113 KB inner exe** — the real GUI tool. **`.text` is plaintext**
  (entropy 5.799). All 7 button handlers + the Qt runtime are readable
  directly. A near-identical `serial_bootloader.exe` (same build, different
  resources; the "Serial Update Tool 2.1" window title lives here).

### 5b. The long detour: the "Enigma block" (and why it was wrong)
Early on, andeds measured the `.enigma1` section of the *23.8 MB download*
(entropy 7.6–7.95) and concluded the handler code was **encrypted**, requiring
a Wine memory-dump to read. Two unpack paths were written up (winegdb + a
`/proc/<PID>/mem` dumper). **This was a mistake** — andeds had measured the
Virtual Box *data section* (a file bundle), not the inner exe's `.text`.
Claude's §44 caught it: the inner exe is plaintext, no decryption needed.
Anded's §41 owned the error + flagged §40 as corrected. (Lesson: measure the
right artifact before concluding "encrypted.")

### 5c. The protocol, decoded (static + live)
With the inner exe confirmed plaintext, the protocol came from **static decode
of `.text`/`.rdata`/`.idata`** + **live strace captures**:
- **Command surface** (Qt moc stringdata table): 7 slots (Connect/Disconnect,
  openFile, Update, refresh, Read, Write, language) + 4 timeout signals + 2
  status/progress signals.
- **Serial config:** `115200 8N1, no flow control` — read from the
  `setBaudRate`/`setDataBits`/`setStopBits`/`setParity`/`setFlowControl`
  immediates in `.text`.
- **Frame sizes:** TX commands are **fixed 64 bytes**; the params-read RX
  response is **17 bytes**.
- **CRC:** **CRC-16/XMODEM** (poly `0x1021`, init `0x0000`, no reflection),
  computed over the 60-byte frame body (bytes [2..61], excluding the 2-byte
  header). Independently verified: the standard test vector `123456789`→`0x31C3`
  passes; 60 zero bytes→`0x0000`.
- **The `32 31 00...` puzzle** (resolved): the first live strace capture showed
  a frame starting `32 31` (ASCII "21") + zeros, which didn't match the static
  decode. **It was a parser bug** — the strace parser didn't handle strace's
  octal escapes (`\2\1` = bytes `0x02 0x01`), so they fell through to literal
  ASCII `32`/`31`. The fixed parser gave the correct bytes.

### 5d. The confirmed protocol (validated on live hardware)
| Item | Value |
|---|---|
| Transport | USB CDC-ACM `/dev/ttyACM0`, 115200 8N1, no flow |
| **Read TX** | 64 bytes: `02 01` + 62 zero bytes (CRC of the zero body = `00 00`) |
| **Read RX** | 17 bytes: `02 c5 <rate:1> <cfg1:1> <cfg2:1> <UID:6> <padding:6>` |
| Handshake | **None** — `open(port) → write(READ_CMD) → read(17)` is enough |
| CRC | CRC-16/XMODEM (poly `0x1021`, init `0x0000`), over the 60-byte body |
| Rate | byte `0x02` = 150 Hz (`.rdata` order `500/250/150/50`) |
| bind UID | RX bytes [5..10] (the 6-byte pairing secret) |

**The Read command is constant** (no dynamic fields on the request side) —
which is why the native reader is a 20-line script. The vendor tool + Wine are
no longer needed for a Read.

The **handler-identity map** was resolved (by wire-byte match + the matching
store sequence):
| Addr | Size | Handler |
|---|---|---|
| `0x404620` | 909 B | `on_readConfigButton_clicked` (Read) |
| `0x405940` | 2079 B | `on_writeConfigButton_clicked` (Write) |
| `0x402a60` | 2646 B | `on_sendFilepushButton_clicked` (Update) |
| `0x4018b0` | 1806 B | `on_openSerialpushButton_clicked` (Connect) |
| `0x406160` | 1605 B | QSerialPort init / ctor |
| `0x4067b0` | 1186 B | `on_refreshButton_clicked` |
| `0x4078b0` | 1485 B | `on_openFilepushButton_clicked` |
| `0x406c60` | 3140 B | Read response **processor** (not a slot) |

The **Update (flash) framing** was statically decoded (3 frame types: a
`0x18×5` sync sentinel, `0x04`/`0x05` command headers, dynamic chunk frames —
all using the same CRC loop) but **not yet captured** (it's a firmware write;
we don't trigger an Update autonomously).

### 5e. The native reader
`tools/e8_read.py` — a small, **read-only** client that sends the 64-byte Read
command, receives the 17-byte config response, and prints the parsed fields
(UID + Rate + the two config bytes). It validates the response (checks length=17
+ header `02 c5`), honestly marks the Power/tlm index→label mapping as TBD
(printing raw hex alongside the best-guess labels), and takes no dynamic input
beyond the port. This is the "we can talk to the E-8 from our own tooling, no
vendor tool" milestone.

### 5f. What's outstanding (the E8)
1. **writeConfig TX frame-body** — the `02 02 00` header is known; the dynamic
   payload bytes (what byte = Power/tlm/Rate/UID) needs a Write-click strace
   capture. A *write* op (needs explicit go-ahead).
2. **Update chunk framing** — needs a Choose-File + Update-click strace OR a
   decompiler pass. A firmware write (higher risk; held off unless native flash
   tooling is specifically wanted).
3. **Power/tlm index→label mapping** — the frame has cfg1=`0x01` cfg2=`0x03`,
   but the GUI showed 100mW/OFF, which doesn't match the naive dropdown order.
   Settled by any Write-cycle capture that varies one field.

---

## Phase 6 — Project governance (the standing files)

Two governance files were added + refined to keep the multi-agent work
consistent:

**AGENTS.md** — the canonical design goals, ground rules, and conventions.
Key sections:
- **§0 Authorization & scope** (added late): states the actual posture plainly —
  authorized reverse-engineering + interoperability work on hardware the
  operator owns, on an isolated bench, for a defensive receive-only purpose.
  Ties the authorization to the hard boundary (stay inside the boundary and
  you're inside the scope; crossing a line needs human sign-off). Plus an
  **incidental third-party RF** rule (a wideband capture that picks up a
  neighbor's link doesn't retain the foreign UID/MAC/serial in anything that
  leaves the bench).
- **§1 The mission** — the general passive detector, the WaveRaider delivery
  surface, the #1 "GENERAL not single-target" goal, the core philosophy
  ("measure + classify, never assume"), the identity goal.
- **§2 The hard boundary** — passive detect/decode/identify only; bench/owned
  aircraft only; the TX gate is a wall (load-bearing, requires explicit human
  sign-off to touch); defensive/situational-awareness intent.
- **§3 The honesty contract** — every finding labeled with its evidence class
  (VALIDATED / SELF-CONSISTENT / SYNTHETIC); the three false-positive classes;
  the decisive CRC-valid discriminator.
- **§4 Ground rules** — one branch (`main`); captures sync via git (pre-push
  hook); the enclave has no SDR (andeds does offline analysis of uploaded
  captures; Claude runs the real RF); commit hygiene.
- **§5 Code conventions** — `elrs_sdr/core/` is the single source of truth;
  version-parameterized; measure don't assume; always analyze at the measured
  sample rate; the burst frequency offset is real.

**SECURITY.md** — agent guardrails (adapted from the "SECURITY.md for AI
agents" pattern to this project). The agent rules:
1. **No secrets in code** — the bind UID is a pairing secret, never a constant
   in code/fixtures/logs/tests (a carve-out allows referencing it in the
   analysis *prose* for cross-checking, but never inlining it into generated
   code).
2. **Untrusted input** — captures + serial frames are validated before parse,
   length fields are never trusted, fail closed (typed exception or
   Result-dataclass, not bare `None`), no eval/pickle on parsed data.
3. **The TX gate is a security boundary** — covers the *pattern* (`*_tx*`,
   `tx_*`/`transmit_*` functions, `transmitter`/`jammer`/`injector` modules,
   subprocess TX binaries), not just the named files; renaming doesn't bypass
   it; enabling needs human sign-off.
4. **Bench/owned-only + read-only serial** — no live/third-party target;
   **state-modifying** commands (flash/config-write/bind-UID change) are gated
   until a human approves; **query-style writes** (the request half of a
   query/response, e.g. `tools/e8_read.py`) are allowed — the distinction is
   "writes that modify the device's stored state" (gated) vs. "writes that are
   the request half of a query/response" (allowed).
5. **Input validation at every boundary** — CLI/config/frame-field values are
   type- + range-checked; no permissive defaults.
6. **Function/module hardening** — small single-purpose parsers; pure parse
   functions; restricted surface.

Plus a **pre-merge checklist** (secret scan in every encoding an agent might
generate — hex pair, decimal tuple, bytes-list, `UID=`/`BIND=` assignments,
`.ts`/`.json`/fixtures; bounds-check the parsers; TX gate intact; no
live/third-party target; serial work read-only; input validation; fail closed;
selftest green; no `__pycache__`; **no debug/scratch artifacts** — backtrace
files, wine logs, strace logs, memory dumps stay out of the tree).

---

## The collaboration model (how it actually worked)

Two agents, two machines, one repo:
- **andeds** (on the enclave — a closed-network Linux box, no internet, no SDR,
  no USB to the devices): writes code, does **offline analysis of the captures
  the laptop uploads**, verifies claims against the actual files, keeps the
  notes + governance.
- **Claude** (on the laptop `borealis` — has internet, the B205, the E8, the
  EMAX tool, Wine): runs the real RF + the real device, does the live
  captures, pushes findings to the shared notes.

The **rolling handoff** is two files: `docs/ANDEDS_NOTES_FOR_CLAUDE.md`
(andeds → Claude) + `docs/CLAUDE_NOTES_FOR_ANDEDS.md` (Claude → andeds). The
numbered sections (§1, §2, …) are the running log; AGENTS.md is the durable
"what this is for." The standing rules that kept it honest: **verify the other
agent's claimed fixes in the actual code, not just the notes**; **label every
finding with its evidence class, never overclaim**; **one logical change per
commit, code + notes together**; **the TX gate is a wall on both sides**.

---

## Where things stand (end of conversation)

**ELRS SDR side (the main mission):**
- The general passive air-state detector is built + verified
  (`elrs_sdr/core/identify.py`), with the measurement layer fixed on real
  wideband IQ (the 3 burst-physics bug fixes).
- The per-link identity (net_id `00111858`) is validated on a real capture.
- The WaveRaider integration (UHD backend + ELRS plugin) is the delivery
  surface; the **Live Detections table** + **"Drones nearby"** filter are
  deployed in the UI.
- The remaining ELRS work is the **sync-word air-recovery** (reading the sync
  out of a demodulated burst to get the per-link ID without the UID up front) —
  the measurement is ready for it; the demod+sync-extraction is the next step.

**EMAX E8 side (the protocol study):**
- The **Read-params protocol is confirmed end-to-end on live hardware** — the
  64-byte Read TX command + the 17-byte RX response, no handshake,
  CRC-16/XMODEM, all validated. The vendor tool + Wine are no longer needed for
  a Read.
- The **native reader** (`tools/e8_read.py`) works — a 20-line read-only
  script that reads the E8's config (UID + rate + power/tlm bytes).
- The **handler map is resolved** + the **Update/flash framing is statically
  decoded** (3 frame types) but not yet captured (it's a firmware write).
- **Outstanding:** the writeConfig TX frame-body, the Update chunk framing, the
  Power/tlm index→label mapping — all need a Write/Update-click capture (write
  ops that need explicit go-ahead).

**Governance:**
- AGENTS.md (the mission, the hard boundary, the honesty contract, the
  authorization & scope) + SECURITY.md (the agent guardrails) are in place +
  refined. The notes-discipline (code + notes in the same commit) is holding.
