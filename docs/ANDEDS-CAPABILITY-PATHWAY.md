# ANDEDS execution brief: capability-program pathway

**Audience:** ANDEDS

**Issued:** 2026-11-04

**Starting revision:** `92f3ae33dab9807c4526e66c0e4a4258a1c009cf`

**Authority:** execution guidance under `AGENTS.md`; it does not replace
`PROJECT-ROADMAP.md` as the active backlog or relax any gate.

Use this brief when selecting or executing work for the capability-program
pathway. Read the settled decisions in
[`handoffs/2026-11-04-roadmap-grilling-handoff.md`](../handoffs/2026-11-04-roadmap-grilling-handoff.md)
only when a step below points to them. Record evidence in `SESSION-NOTES.md`;
do not create another rolling agent log.

## Current state

- The strategic grilling is complete. Its decision frontier is empty.
- Gate 0 is **not complete**. GitLab remains the temporary write authority and
  GitHub remains divergent; read the `NOW` row in `PROJECT-ROADMAP.md` for the
  current tips and migration status.
- A cleaned-history rehearsal and verified offline archive were prepared
  locally by Codex, but later GitLab commits made that candidate stale. The
  remote replacement was interrupted before it changed either remote.
- GitHub authentication was restored locally. This does not give ANDEDS
  Internet access or permission to execute the migration.
- The intended sequence is:

  ```text
  Gate 0 -> truth restoration -> minimal Capability Pack contract
         -> ELRS Pack 1 -> operator-supplied second Stock Target
  ```

- After Gate 0, `PROJECT-ROADMAP.md` plans ANDEDS as primary for the serialized
  TR-A code/regression slice, with Codex as the independent reviewer. That
  assignment activates only when TR is promoted to `NOW`.

## Step 1: synchronize and select work

1. Pull the canonical remote named by the operator before editing.
2. Read `AGENTS.md`, the latest `SESSION-NOTES.md` entries, and the active
   `PROJECT-ROADMAP.md` row.
3. Confirm that exactly one `NOW` outcome exists and that the row names a
   primary writer, an eligible reviewer, acceptance criteria, and an evidence
   target.
4. Work only on the activated outcome and its linked execution specification.

**Complete when:** the local tip equals the canonical remote, the working tree
is clean, and the selected task is explicitly active rather than inferred from
an old handoff or `GATED` row.

If those conditions are absent, stop before editing and report the exact
missing gate in `SESSION-NOTES.md` or to the operator.

## Step 2: respect Gate 0

Gate 0 replaces the active histories with one cleaned `main`, makes GitHub the
write authority, and makes GitLab its deterministic mirror. The original refs
and raw captures remain in a verified offline archive.

ANDEDS has no Internet access and is never an eligible reviewer. Therefore:

- do not run a history filter, force-push, delete remote refs, or configure the
  cross-service mirror;
- do not resume or reproduce Codex's ignored `work/` migration staging;
- wait for the operator to announce Gate 0 completion, then discard the old
  checkout and clone the designated canonical remote fresh;
- verify the announced `main` commit and tree hash before taking a writer task.

**Complete when:** the operator supplies the cleaned tip, fresh GitHub and
GitLab clones have identical intended refs and tree hashes, and ANDEDS is
working from a fresh clone of that history.

## Step 3: bounded truth restoration

After Gate 0, the first implementation outcome is a bounded correction pass,
not general cleanup. When assigned as primary, implement only the activated
slice from this list:

1. Make MAVLink target parsing safe for IPv6 and omitted ports.
2. Import matching Wi-Fi/MAVLink constants from the existing MAVLink chain;
   record the broader six-file duplication as backlog rather than refactoring
   it now.
3. Derive ELRS SYNC and RC version-specific parameters consistently from the
   selected OTA version; keep the single `elrs_core` design.
4. Fix or remove the unread `elrs_chain` `max_power_dbm` field while preserving
   the separate, tested bench-controller field.
5. Replace the simulation tool's real-looking default binding phrase with an
   explicit safe input or placeholder warning.
6. Correct roadmap/evidence claims: no stale active R6 lane, no hosted-CI
   success claim when jobs did not run, no E8 version-match claim, and no
   description of direct USB entry as wireless compromise.
7. Preserve the global no-flashing rule, reviewer eligibility, dynamic task
   assignment, and external-artifact policy.

For the exact defect boundaries, read `SESSION-NOTES.md` entries #73 and #74.

**Complete when:** each assigned defect has a failing regression before the
fix, focused tests pass after it, the project validator passes, the full
maintained offline suites pass, and the roadmap names one truthful `NOW` plus
at most two ordered `NEXT` outcomes. Hosted CI is reported as `NOT RUN` unless
it actually executes.

## Step 4: minimal Capability Pack contract

Start this step only when truth restoration is accepted. Use the canonical
terms in `GLOSSARY.md` and the applicable ADRs under `docs/adr/` once they are
present on the canonical branch.

Build the smallest contract that supports:

```text
registry -> applicability -> scoped authorization -> isolated executor
         -> structured Result State and evidence
```

Keep the web UI as a loopback control plane. Test it against a fake executor;
keep RF and hardware acceptance behind a separate explicit gate. Prefer one
end-to-end contract seam over multiple implementation-level seams.

**Complete when:** one simulated Capability Pack traverses the entire seam;
authorization is per target and action with time bounds; cancellation and
timeouts are observable; Result States never equate transmission with target
acceptance; and no test requires RF, serial, hardware, or external network
access.

## Step 5: ELRS Capability Pack 1

Start this step only after the minimal contract is accepted and the roadmap
activates Pack 1. The E8/TinyHawk pair is a Proving Fixture, not a presumed
competition target. The initial applicability boundary is ELRS 2.4 GHz OTA v3
receiver-side injection.

Default work remains offline. A live step requires its own operator gate for
the exact device, action, bounds, and recovery plan. The E8 and TinyHawk may
never be flashed. The B205mini is the primary portable implementation target;
the B210 is a measured lab reference or fallback.

The leading receiver-acceptance hypothesis is a bounded RC canary, but evidence
may replace it. One `FIELD-READY` useful effect completes Pack 1 v1; additional
effects return to backlog.

**Complete when:** the Capability Pack declares its Applicability Boundary and
Prerequisite Mode, uses a timed and abortable SDR transport, independently
validates candidate IQ, obtains separately authorized Stock Target acceptance,
verifies a useful effect without field-side wiring, records Operational
Reliability under bounded retries, and provides a repeatable operator runbook.

If the end-to-end wireless route is falsified, mark the path `LIMITED` or
`BLOCKED`, preserve reusable components, and return to roadmap selection.

## Review and handoff

- ANDEDS may write but never reviews or marks its own work accepted.
- Claude or Codex must independently review an ANDEDS change before `DONE`.
- Keep one active writer per file family. Background research must have a
  different owner, no active hardware, and no overlapping files.
- Commit a coherent slice, push it to the current canonical remote, and append
  a dated `SESSION-NOTES.md` entry with starting revision, objective, actions,
  evidence, failures, safety boundaries, changed files, validation, commit,
  push status, and the next smallest step.
- Keep private identifiers and raw captures under ignored `work/`. Track only
  sanitized conclusions, hashes, provenance, and retrieval instructions.

**Complete when:** the primary's commit is pushed, the evidence entry is
present, the independent reviewer can reproduce the acceptance commands from a
fresh checkout, and no unresolved gate is described as completed capability.
