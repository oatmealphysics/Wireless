# Codex + Claude: Shared Running Handoff

**Direction:** the operator + Andeds set the path forward and priorities.
**Implementation:** Codex is the main coder.
**Quality control:** Claude is the adversarial reviewer of Codex's code.
**Format:** rolling log, newest dated entry first. Read on `git pull` before
starting work; record facts, decisions, evidence, review findings, and open items.

## Standing collaboration rules

- `../AGENTS.md` is the canonical mission, scope, and workflow directive;
  `../SECURITY.md` supplies the security ground rules. This log supplements both.
- The operator + Andeds choose priorities, scope changes, and the next substantive
  work item. Codex implements that direction and resolves routine implementation
  choices within it. Claude challenges the implementation and its evidence;
  review findings do not independently expand project scope.
- Codex records what changed, why, relevant paths, validation performed and its
  limits, and concrete questions for review. Keep code + handoff notes in the
  same commit when recording an implementation change.
- Claude checks the actual code and independently evaluates assumptions,
  correctness, false positives, generality, security boundaries, regressions,
  and WaveRaider presentation. Passing selftests or an author's explanation
  alone is not proof that a result works on real RF.
- Record each review finding with a stable ID (`QC-001`, `QC-002`, ...), file and
  location, evidence or reproduction steps, impact, and recommended correction.
  Track it as **OPEN**, **FIXED — AWAITING QC**, **VERIFIED**, **DISPUTED**, or
  **DEFERRED BY DIRECTION**. Codex documents fixes and checks; Claude records
  independent verification. Preserve the finding and its resolution history.
- If coder and reviewer disagree, record both positions and evidence. The
  operator + Andeds resolve direction or scope questions. Do not mark an
  unresolved finding verified or silently turn it into accepted behavior.
- Findings and capability claims use **VALIDATED**, **SELF-CONSISTENT**, or
  **SYNTHETIC**, as defined in AGENTS.md. Say explicitly when a check was not run,
  a capture is unavailable, or a result is inferred rather than measured.
- Keep the detector general across protocols, versions, UIDs, and rates. Derive
  parameters from RF, use the measured capture sample rate, and surface results
  honestly in WaveRaider. A link discriminator is not an aircraft hardware ID.
- Stay passive **for the current phase**. The TX boundary is a **staged control**
  (see the 2026-10-14 direction change below): do not enable, exercise, or wire
  new TX paths, and no flash / persistent device change, until a human approves
  that specific change. The TX *capability* is in scope for the project — it is
  being deliberately gated for now, not treated as out of scope.
- Do not place credentials, binding phrases, or bind UIDs in this log. Reference
  protected local validation inputs by purpose rather than copying their values.
  Call the reference device only **the counter-UAS passive SDR**.
- Work on `main`; pull before work and push completed commits as directed by
  AGENTS.md. Preserve the older handoff logs as project history. Do not repeat
  historical status as current fact without checking code and later corrections.

## Entry template

```text
## YYYY-MM-DD — Author — Concrete change or review topic

Direction / scope:
What changed or was reviewed:
Relevant files / revision:
Evidence and validation (including class and limitations):
QC findings / responses / status:
Open items for Codex / Claude:
Decisions needed from the operator + Andeds:
```

---

## 2026-10-14 — Codex — E8 console/filesystem investigation; hardware not connected

**Direction:** the operator asked whether the owned controller exposes an OS
console for commands such as `ls` or `mkdir`, and authorized investigation.

**Findings:** no shell or filesystem interface is established. The confirmed
USB serial surface is a binary settings/update protocol. Vendor documentation,
the native reader, historical hardware notes, and static strings in the local
unpacked host tool support that narrower conclusion. Neither MCU identity nor
OS/RTOS/filesystem absence is proven. USB descriptors and Windows host imports
are insufficient to establish those facts.

**Artifact:** `docs/E8_CONSOLE_INVESTIGATION.md` records evidence classes,
limitations, vendor sources, and the next read-only observations. No pairing
secret is copied into the new document or this entry.

**Hardware status:** current `lsusb` shows neither historical E8 USB ID;
`/dev/serial/by-id` is absent. Requested that the operator connect the owned
E8 in serial mode. No serial port was opened, no undocumented command sent,
and no RF transmission, flash, or persistent write performed. Historical
Read validation is explicitly distinguished from this turn's checks.

**QC request for Claude:** check the distinction between host executable and
device firmware, the unconfirmed MCU/OS claims, and the evidence required for
any proposed internal console pinout. Review only; no new hardware probing or
implementation scope is assigned by this note.

**Coordination:** pulled `b09becd`, read Andeds's new `GUIDING_LIGHT.md`, and
will write only this agent's notes file. QC-001 remains deferred by direction;
the current phase remains receive-only. No TX implementation change here.

**Validation:** documentation-only; no implementation tests are claimed.

---

## 2026-10-14 — andeds — QC-001 verified in code; TX is a staged goal, not out-of-scope; note-file naming

### Direction / scope

Confirming your orientation handoff and correcting one framing, per the operator.

### QC-001 — I verified it in the actual code (I agree it is a real gap)

- `elrs_sdr/cli.py:751` and `:754` — the `tx` subcommand calls
  `RadioDevice.tx_tune` and `RadioDevice.tx_stream`.
- `elrs_sdr/radio.py:880` (`tx_tune`) and `:898` (`tx_stream`) are **fully
  implemented UHD TX paths, not stubs**.
- The only gate is a **self-asserted CLI flag**
  (`--i-am-authorized-to-transmit`, checked at `cli.py:738`).

So QC-001 is accurate: the TX path **is reachable from a CLI flow**, gated only
by a flag the operator types — weaker than the "explicit human sign-off" the
docs describe. **However**, see the direction change below: the gap is real
*enforcement*-wise, but it is no longer a "the tool shouldn't transmit at all"
violation, because transmitting is a stated project goal.

### Direction change (operator, 2026-10-14): TX is a staged goal, not out-of-scope

The project's end state **includes transmission**. The current receive-only
posture is a **phase** (passive detect/identify first), not the mission. So:

- The TX code that exists is **working toward the goal, not a violation of it**.
- **Do not delete** the CLI `tx` path or treat it as a bug to remove.
- The honest remediation is: (a) keep it gated/bench-only **for now**, and (b)
  reword AGENTS.md §2 + SECURITY.md rule 3 from "TX out of scope / a wall" to
  "TX is an intended capability, deliberately gated/bench-only for the current
  phase, enabled by explicit human sign-off." I am making that doc rewording in
  this same change; **Claude should QC it** for accuracy + that it doesn't
  actually loosen the current-phase gate (bench-only + human sign-off must stay).
- The self-asserted-flag weakness is **noted but deferred**: since TX is
  intentional, the right gate is a proper human sign-off mechanism when we get
  to the TX phase, not a removal now.

### Note-file naming (operator, 2026-10-14)

Each agent keeps **their own** rolling notes file, all renamed to `..._FOR_BOTH`
since we all read each other now:
- **`docs/CODEX_NOTES_FOR_BOTH.md`** (yours — this file)
- **`docs/ANDEDS_NOTES_FOR_BOTH.md`** (mine)
- **`docs/CLAUDE_NOTES_FOR_BOTH.md`** (Claude's)

I created this file from your `docs/NOTES_FOR_BOTH.md` and will **delete
`docs/NOTES_FOR_BOTH.md`** so the name is unambiguous. **When you pull, please
`git mv docs/CODEX_NOTES_FOR_BOTH.md` if it conflicts, and start writing to
`docs/CODEX_NOTES_FOR_BOTH.md`** (this file). The old `ANDEDS_NOTES_FOR_CLAUDE.md`
/ `CLAUDE_NOTES_FOR_ANDEDS.md` are being renamed to the `_FOR_BOTH` form and kept
as history.

### Open items for Codex
- Confirm you'll write to `docs/CODEX_NOTES_FOR_BOTH.md` going forward.
- No implementation change from you is requested by this entry — it is a
  direction/coordination note. The next substantive work item still comes from
  the operator + andeds.

### Decisions needed from the operator + Andeds
- None beyond the TX framing already confirmed. The next engineering work item
  (staged: finish passive detection, then build TX) is the operator's to set.

— andeds

---

## 2026-10-14 — Codex — Collaboration roles and shared handoff established

### Direction / scope

The operator assigned Codex as the main coder, Claude as the adversarial
quality-control reviewer, and the operator + Andeds as the owners of the path
forward. This file is the shared implementation/review handoff for that model.
No new engineering priority is inferred from the role change.

The operator also authorized Codex to access the owned drone controller as
needed to test the platform. Within the standing bench scope, this includes
read-only inspection, serial query/response operations, and receive-only SDR
validation against the controller's link. Record the device, procedure, measured
parameters, and outcome of each test without copying pairing secrets into the
notes. This standing test authorization does not replace the specific sign-off
requirements for active transmission, flashing, or persistent device changes.

### Current context

- Codex pulled `main` to `6c96427` before this documentation change; that pull
  added `docs/CONVERSATION_SUMMARY.md` and an Andeds handoff entry.
- Codex has read AGENTS.md and SECURITY.md and performed an initial repository
  inventory and targeted source inspection. The full file-by-file reading is
  **not complete**. This is an orientation handoff, not a completed audit.
- The current protocol catalog records ExpressLRS as `SPECIFIC`, with the
  FHSS seed serving as a per-link discriminator. Older status prose records
  `FAMILY`; use the code and dated evidence when assessing present capability.
- The current `.gitignore` ignores `captures/`; later notes record the operator's
  2026-10-10 decision to stop tracking captures by default. This differs from
  older capture-sync wording in AGENTS.md. No capture policy was changed here.

### QC-001 — OPEN — CLI reaches the TX implementation

**Source inspection:** `elrs_sdr/cli.py`, `cmd_tx` (lines 737–755 at the revision
above), checks an authorization flag and then calls `RadioDevice.tx_tune` and
`RadioDevice.tx_stream`. The CLI registers the command. AGENTS.md §2 and
SECURITY.md rule 3 prohibit CLI flows from wiring in the TX side.

**Evidence:** VALIDATED by source inspection for the existence of the call path.
No transmission was attempted; runtime behavior on hardware was not tested.

**Impact / proposed review:** the existing flag-gated CLI path appears to
contradict the standing receive-only architecture. Claude should independently
check the reachability and the applicable directives. The operator + Andeds
should set remediation priority. This entry authorizes no TX operation or gate
change; Codex has made no implementation change to that path.

### Validation and next handoff

This change creates the shared notes only. No radio or serial device was opened,
no code was executed for hardware testing, and no implementation tests are
claimed. Codex will implement the next work item selected by the operator +
Andeds and provide Claude with the exact changes and evidence for review.
