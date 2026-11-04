# Web UI controls isolated Capability Packs

The web UI is the human control plane over repository-owned, independently testable Capability Packs, not the implementation boundary for attack logic. Live execution belongs in a scoped, isolated executor with structured requests, outcomes, cancellation, time bounds, and evidence, so UI tests can use a fake executor while hardware acceptance remains a separate gated evidence seam.
