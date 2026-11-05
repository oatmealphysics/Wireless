# Issue tracker: GitHub

Issues and specifications for this repository live in GitHub Issues at
`oatmealphysics/Wireless`. Use the authenticated `gh` CLI for all
operations and verify the target with `gh repo view --json nameWithOwner`
before the first write of a session.

## Conventions

- Create issues with `gh issue create` and read them with `gh issue view`.
- Apply or remove labels with `gh issue edit --add-label` and
  `gh issue edit --remove-label`.
- Use native GitHub sub-issues and blocking dependencies when available.
  Otherwise, record `Part of #<parent>` and `Blocked by: #<issue>` in the body.
- Comment with the evidence or reason before closing an issue.
- A skill instruction to publish to the issue tracker means creating a GitHub
  issue in this repository.
- File issues only in this repository, never in the upstream skill repository.

## Pull requests as a triage surface

**PRs as a request surface: no.**
