# Session Handoff

Generate a structured handoff document when context is getting heavy, the current phase of work is complete, or the user requests it. The goal is to enable a fresh session to continue seamlessly.

## When to Suggest a Handoff

- Context window approaching ~150,000-250,000 tokens (~50% capacity)
- Planning phase complete (grill/spec/tickets done), switching to implementation
- Switching between major work phases (recon → exploitation, research → development)
- Multiple complex topics have accumulated in context
- You notice degraded recall or repetition in responses

## Handoff Document Structure

Write the handoff to `handoffs/handoff-YYYY-MM-DD-HHMMSS.md` **inside the current project's root directory** (the git repo root, or the working directory if not in a repo). Create the `handoffs/` folder if it doesn't exist. This keeps handoffs co-located with the project so both Claude and Codex can read them, and they're versioned in git alongside the code.

Examples:
- Working in `~/Desktop/Drone-Research/` → `~/Desktop/Drone-Research/handoffs/handoff-2026-11-02-143000.md`
- Working in `~/Desktop/HTB-Challenges/pwn-challenge/` → `~/Desktop/HTB-Challenges/pwn-challenge/handoffs/handoff-2026-11-02-143000.md`

Print the full absolute path after writing so the user can reference it.

```markdown
# Session Handoff — [Topic/Task]
Generated: [timestamp]

## Current State
[One paragraph: what was being worked on, how far along it is]

## Objective
[What the user is trying to accomplish — the big picture goal]

## Completed Work
- [Bullet list of what's been done, with file paths and key decisions]

## In Progress
- [What was actively being worked on when handoff triggered]
- [Current hypothesis or approach if mid-investigation]

## Key Findings
- [Important discoveries, addresses, offsets, credentials, or technical details]
- [Anything that took significant effort to determine and shouldn't be re-derived]

## Files Modified / Created
- [path] — [what was changed and why]

## Open Questions / Blockers
- [Unresolved issues]
- [Things that need user input]

## Next Steps
1. [Ordered list of what to do next]
2. [Be specific: file names, function names, approaches to try]

## Context for New Session
[Any environment setup, tool configuration, or background the new session needs]
[Reference any relevant playbooks: e.g., "continue using /exploit-dev methodology"]

## Engagement / Scope Details (if applicable)
- Target: [target info]
- Scope: [in-scope details]
- Authorization: [engagement type]
```

## Handoff Procedure

1. Announce: "Context is getting heavy — I'll prepare a handoff document."
2. Write the handoff document
3. If there are modified files, ensure they're saved
4. If in a git repo, suggest committing current work
5. Tell the user: "Start a new session and paste or reference this handoff: [path]"

## For Codex Compatibility

The handoff lives in the project repo, so both agents pick it up naturally:
- Use plain markdown, no Claude-specific syntax
- Include exact file paths and line numbers (relative to project root)
- Be explicit about tool/library versions in use
- A new session from either agent should be told: "Read `handoffs/` for prior context before starting"
- Suggest committing the handoff so it's available after git pull

## Reminders

- A handoff is NOT a summary — it's an operating document for the next session
- Include raw technical details (addresses, offsets, hashes) — they're expensive to re-derive
- Don't over-explain context the new session can get from reading files
- DO include decisions and their reasoning — "we chose approach X because Y didn't work due to Z"
- Keep it under 2000 words — a handoff that's too long defeats the purpose
