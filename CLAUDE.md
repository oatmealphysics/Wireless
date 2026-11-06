# CLAUDE.md — Session Entry Point

This file is the entry point for Claude Code (and compatible agents) working in
this repository. It does not restate the rules; it points to the authoritative
sources and names the agents and skills this project uses so a session loads
them before doing any work.

## Read these first, every session

1. [`AGENTS.md`](AGENTS.md) — **authoritative** multi-agent workflow rules
   (pull before editing, read files fresh, append to `SESSION-NOTES.md`, never
   force-push or rewrite history, serialize hardware access).
2. [`SECURITY.md`](SECURITY.md) — authorization boundary, default posture, the
   seven required gates for state-changing work, and tool guardrails.
3. [`PROJECT-ROADMAP.md`](PROJECT-ROADMAP.md) — the **only** priority/status
   source. If a plan is not linked from the roadmap, it is a specification, not
   an active assignment.
4. Latest entries of [`SESSION-NOTES.md`](SESSION-NOTES.md) — the single
   append-only evidence log for every workstream. Append a dated,
   agent-attributed entry before handoff.

## Hard invariants (never bypass)

- **No flashing the owned EMAX E8 or TinyHawk III Plus — ever.** No upload,
  erase, replace, restore, update, or bootloader loader against either device,
  under any circumstances. See `AGENTS.md` → "Absolute No-Flashing Constraint".
- **Never force-push or rewrite history.** All agents work on `main`; this repo
  enforces it via `.claude/settings.json`.
- **Offline / dry-run by default.** Tools that transmit, inject, write, erase,
  reset, or flash default to parse/preview and require an explicit action flag
  (`--arm`, `--execute`, `--flash`). Live and destructive actions are gated per
  `SECURITY.md` and each gate needs its own explicit owner sign-off.
- **All scripts live in `tools/`** with usage docs and no destructive defaults.

## Agents

Roles are convention-based (no agent framework is required to work here); they
are defined in prose in [`AGENTS.md`](AGENTS.md) → "Multi-Agent Workflow". The
repo is co-edited by Claude Code and Codex on `main`, with the operator
orchestrating.

A set of **custom CTF agent/skill roles** (verifier/gate-agent, scope-guard,
repo-sentry, bench-runner, etc.) is *proposed but not yet built* in
[`docs/WORKFLOW-AGENTS-SKILLS.md`](docs/WORKFLOW-AGENTS-SKILLS.md). Treat that
file as a design proposal, not as installed tooling.

## Skills

This project uses the external **`mattpocock/skills`** plugin (installed in the
operator's Claude/Codex environments, not committed here). The repo-committed
configuration those skills read lives in `docs/agents/`:

- [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md) — issues and
  specs are tracked in **GitHub Issues** at `oatmealphysics/Wireless` via the
  `gh` CLI. "Publish to the issue tracker" means a GitHub issue in this repo.
- [`docs/agents/triage-labels.md`](docs/agents/triage-labels.md) — the canonical
  five triage labels: `needs-triage`, `needs-info`, `ready-for-agent`,
  `ready-for-human`, `wontfix`.
- [`docs/agents/domain.md`](docs/agents/domain.md) — single-context glossary
  (`GLOSSARY.md`) and system-wide ADR layout (`docs/adr/`); use canonical terms
  and surface ADR conflicts explicitly.

The intended workflow is `grill-with-docs → to-spec → to-tickets`. Its outputs
(a pure `GLOSSARY.md`, sparing ADRs, a published spec, and GitHub tickets) are
**gated**: they are blocked behind the Gate 0 repository migration, GitHub
re-authentication, and explicit operator approval of the `to-spec` test seam.
See `handoffs/2026-11-04-roadmap-grilling-handoff.md` §12–16. Do not publish a
spec or tickets, and do not run the repository migration, without explicit
operator sign-off.

## Directory-scoped settings

`waveraider/.claude/settings.json` applies an additional permission deny-list
when working inside `waveraider/`. The root `.claude/settings.json` applies
repo-wide.
