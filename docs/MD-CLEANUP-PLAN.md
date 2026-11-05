# Root `*.md` cleanup plan (post-G0 backlog ticket)

**Status:** PARKED — not authorized, not activated. General cleanup is
excluded from TR (see `PROJECT-ROADMAP.md`, "TR outcome complete when").
Execute only after G0 is accepted and a fresh mirror clone exists; an owner
must activate this as a named slice with an eligible reviewer before edits.

**Prepared by:** ANDEDS (read-only analysis), 2026-11-04, from tip `228c04f`.

**Goal:** reduce 17 root Markdown files to 10 by moving 7 historical/superseded
documents into `docs/` or `research/`. No content is deleted; every move is a
`git mv` plus reference updates, so nothing is lost.

## Why post-G0

- G0 rewrites active history and re-clones both remotes; moves done on the
  current history would be discarded by the migration.
- The TR sprint explicitly excludes "general cleanup"; doing this inside TR
  would violate its acceptance criteria.
- After G0, the new history is the only place this work is durable.

## Root files that STAY (10)

| File | Reason |
|---|---|
| `README.md` | Repository entry point |
| `AGENTS.md` | Standing agent directives; referenced everywhere |
| `SECURITY.md` | Guardrails; referenced by README/AGENTS |
| `PROJECT-ROADMAP.md` | Sole active priority/status source |
| `SESSION-NOTES.md` | Single shared append-only log (root per convention) |
| `VALIDATION.md` | Short repro instructions; referenced by CI docs |
| `E8-SERIAL-CHARACTERIZATION.md` | Live E8 workstream (RQ23 area) |
| `HARDWARE-IDENTITY.md` | Live E8 workstream; cross-linked |
| `HOME-LAB-HANDOFF.md` | 11 inbound refs incl. `firmware/artifact-register.json`; moving is high-churn for little gain |
| `DRONE_RF_ARCHITECTURE.md` | Current architecture of the elrs_sdr workstream |

## Files to MOVE (7)

| File | Destination | Evidence it is historical |
|---|---|---|
| `firmware-to-exploit-pathway.md` (16 lines) | `docs/` | Self-declares retired; points to `docs/firmware-to-poc-methodology.md` |
| `session-notes-2026-10-25.md` | `docs/` | Header: incorporated into SESSION-NOTES #53; "nothing in this file is an active assignment" |
| `Harness_DIFFERENTIAL_REVIEW_2026-10-15.md` | `docs/` | One-off dated code review (36-commit range) |
| `failsafe-behavior-comparison.md` | `research/` | Reference doc, 2026-10-14; zero active inbound refs |
| `wifi-attack-surfaces.md` | `research/` | Reference doc, 2026-10-14 |
| `attack-research-plan.md` | `research/` | Plan, 2026-10-14; roadmap marks it historical context |
| `drone-wireless-vuln-reference.md` | `research/` | CVE reference, 2026-10-14; requires the two tool updates below |

## Mechanical steps (execute in this order)

1. `git mv` each of the 7 files to its destination directory.
2. Update the two hardcoded tool paths:
   - `tools/validate-project.py:90` —
     `(root / "drone-wireless-vuln-reference.md")` →
     `(root / "research" / "drone-wireless-vuln-reference.md")`
   - `tools/audit-cve-records.py:29` — same path update in the CVE scan.
3. Update Markdown links in the tracked referencing files (verified inbound
   ref inventory at `228c04f`):
   - `README.md`, `AGENTS.md`, `PROJECT-ROADMAP.md`, `VALIDATION.md`,
     `HARDWARE-IDENTITY.md`, `E8-SERIAL-CHARACTERIZATION.md`,
     `HOME-LAB-HANDOFF.md`, `docs/CTF-RUNBOOK.md`,
     `docs/game_day_playbook.md`, `firmware/README.md`,
     `references/emax-e8_material/README.md`
   - Known counts: `drone-wireless-vuln-reference.md` 9 inbound files,
     `HARDWARE-IDENTITY.md` 9, `HOME-LAB-HANDOFF.md` 6 (11 inside
     `firmware/artifact-register.json` — check whether those are
     human-readable notes or parsed fields before editing JSON),
     `E8-SERIAL-CHARACTERIZATION.md` 5, `attack-research-plan.md` 4,
     `DRONE_RF_ARCHITECTURE.md` 3, `session-notes-2026-10-25.md` 2 (roadmap
     + SESSION-NOTES), `Harness_DIFFERENTIAL_REVIEW_2026-10-15.md` 2,
     `failsafe-behavior-comparison.md` 1 (SESSION-NOTES only).
4. **Do not edit `SESSION-NOTES.md` links** — it is the append-only
   historical log; old entries keep pointing at old paths (git history
   preserves them).
5. Re-run the validator: `python3 tools/validate-project.py` (its
   `local_link_errors` pass checks every `.md` link, so this is the real
   acceptance gate). Then run `tools/audit-cve-records.py` to confirm the CVE
   scan still reads the moved reference.

## Acceptance criteria

- `ls *.md` in root lists exactly the 10 "STAY" files.
- `python3 tools/validate-project.py` passes (syntax, JSON, corpus hashes,
  rainbow data, CVE coverage, local file links).
- `git status` clean; single coherent commit; no content deleted
  (`git diff --stat` shows renames only, plus the two tool path lines and
  link-text updates).
- Independent reviewer (not the writer) accepts the diff.

## Estimated effort

~30 minutes of execution once activated (moves + refs are mechanical; the
validator catches any missed link).
