# Workflow Improvement: Agents & Skills

**Date:** 2026-11-02 · **Author:** andeds (orchestrator) · **Status:** proposal
**Scope:** how to make the 3-agent (Claude / Codex / andeds) workflow less painful,
grounded in the actual friction from recent sessions — not a generic list.

## Current setup
- **Convention-based 3 agents:** Claude Code, Codex, andeds — roles defined in
  `AGENTS.md` prose, **no** agent framework, **no** project skills, **no** A2A config.
- **Topology:** 3 separate machines (laptop `borealis`, Claude's PC, Codex's PC) +
  the enclave (andeds). Two remotes: GitLab `enclave` (canonical, full history) and
  GitHub `origin` (mirror). Laptop reaches both only when it has the right network;
  the enclave reaches GitLab (HTTPS+token) but **not** GitHub (no egress).
- **Coordination artifacts:** `SESSION-NOTES.md` (append-only log),
  `PROJECT-ROADMAP.md` (authoritative backlog), `AGENTS.md`/`SECURITY.md` (rules).

## The 5 real friction points
1. **Git is the choke point.** Most recent session time = git archaeology: history
   rewrites, two-lineage divergence, detached HEAD, protected-branch rejections,
   laptop↔enclave network flapping. Caused by topology, not the agents.
2. **Handoff is manual.** `SESSION-NOTES.md` is prose; "did it land," "next number,"
   "any conflict markers" are all hand-verified. No enforcement.
3. **No enforced ground truth for done/open.** `PROJECT-ROADMAP.md` is authoritative
   but it's a doc re-read each turn; agents drift from it.
4. **Verification is ad hoc.** "Is this POC green right now?" has no one-command
   answer; each agent verifies differently (or not).
5. **Scope/authz gates are prose.** no-flash, `--arm` fail-closed, "offline only"
   live in text an agent must remember. The 2026-11-01 review *found* footguns
   (decorative `--arm`, false `SUCCESS`) — the gate is only as good as the last review.

## Agent types (mapped to friction)

| Agent type | Role | Fixes | Effort |
|---|---|---|---|
| **Repo-sentry / git-ops** | Owns all git: sync, divergence/detached-HEAD/protected-branch recovery. Knows the topology. | #1 | Med |
| **Handoff-scribe** | Owns `SESSION-NOTES.md`: numbering, append-only, format + conflict validation, roadmap cross-ref. | #2 | Low |
| **Roadmap-keeper** | Source of truth for done/open/gated; flags any commit that doesn't update it. | #3 | Low-Med |
| **Verifier / gate-agent** | One-command green check (dry-run pipeline + arm-gate test + offline suite) → PASS/FAIL per POC. | #4 | **Low (wire existing tests)** |
| **Scope-guard / red-team** | Continuous adversarial review for gate bypass / fail-open / false-success — the 2026-11-01 review, but ongoing. | #5 | Med |
| **Bench-runner** | The only agent allowed to touch `--arm`/hardware, under a declared bench plan; reports `ATTEMPTED`/`RECEIVER-ACCEPTED`. | #5 (live) | Med |

## Skills (reusable procedures) worth codifying
- **`repo-sync`** — exact laptop/enclave/GitHub sync sequence + the
  divergence / detached-HEAD / protected-branch recovery playbook.
- **`poc-green-check`** — one-command verifier: `chain1-dry-run` + arm-gate
  unittest + offline suite → PASS/FAIL. Replaces ad-hoc verification.
- **`notes-entry`** — append-to-`SESSION-NOTES.md` procedure (next number, format,
  conflict check, atomic commit+push).
- **`ctf-runbook-exec`** — the detect→identify→exploit sequence from
  `docs/CTF-RUNBOOK.md`, with the `--arm` preconditions enforced.
- **`scope-gate-review`** — adversarial pass checking `--arm`/fail-closed/offline
  invariants on any exploit-chain diff.

## Recommendation (highest ROI first, all low-risk)
1. **Verifier / gate-agent** (`poc-green-check`) — biggest bang, lowest risk;
   "is it green?" becomes one command every agent runs. Buildable now from tests
   that already pass.
2. **`repo-sync` skill** — codify the git topology + recovery playbook so the #1
   friction stops recurring. Pure knowledge, no risk.
3. **Scope-guard agent** — the continuous version of the review that caught the
   footguns; directly protects the CTF work.

**Hold** on the repo-sentry and bench-runner *as separate agents* until the skills
exist — the skills capture ~80% of the value with none of the multi-agent
coordination cost. Separate agents pay off once there are more parallel workstreams.

## Open question (determines the vehicle)
Are Claude and Codex running as **Claude Code / Codex CLI** (so they can load the
same skills andeds can), or bare LLM sessions that only read the repo?
- **If they load skills** → build the skills above (shared, DRY).
- **If they only read the repo** → encode the same content as **repo scripts +
  `AGENTS.md` procedures** (`scripts/poc_green_check.sh`, a `SYNC-PLAYBOOK.md`,
  enforced `AGENTS.md` rules) so they're followable without a skill loader.

Either way the *content* is identical; only the packaging differs.

## Suggested next step
Pick a vehicle (skills vs repo-scripts) and start with **`poc-green-check`** +
**`repo-sync`** — the two that immediately cut the two biggest frictions. Both are
offline, low-risk, and buildable now.
