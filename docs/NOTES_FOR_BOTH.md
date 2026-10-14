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
- Stay passive. Preserve the TX boundary; do not execute or enable TX, flash,
  persistent configuration writes, or third-party interaction under this handoff.
  The specific human sign-off requirements in AGENTS.md and SECURITY.md remain.
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
