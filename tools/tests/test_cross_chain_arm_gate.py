"""Regression tests for the cross-chain orchestrator safety gate (RQ / review).

The orchestrator's live connect/probe/inject branches are gated on
``state.dry_run``. These tests lock in the invariant that ``state.dry_run`` is
True unless the operator explicitly passes ``--arm`` (and not ``--dry-run``),
so a bare ``cross_chain_orchestrator.py full`` (no flags) can never touch the
network. This is the fix for the review finding that ``--arm`` was decorative
before the gate was wired in.

SYNTHETIC/OFFLINE-TESTED: builds a ``Namespace`` and checks the dry_run
derivation the orchestrator applies; no network, no hardware.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR / "exploit_chains"))


def _derive_dry_run(args: types.SimpleNamespace) -> bool:
    """Mirror of the orchestrator's ``state.dry_run = args.dry_run or not args.arm``.

    We re-derive here (rather than importing main()) so the test is stable and
    fast; a separate test asserts the orchestrator source actually uses this
    exact expression so the two cannot silently drift apart.
    """
    return args.dry_run or not args.arm


class TestOrchestratorArmGate(unittest.TestCase):
    def test_bare_full_is_preview(self):
        # The dangerous case from the review: no --arm, no --dry-run.
        args = types.SimpleNamespace(dry_run=False, arm=False)
        self.assertTrue(_derive_dry_run(args))

    def test_dry_run_only_is_preview(self):
        args = types.SimpleNamespace(dry_run=True, arm=False)
        self.assertTrue(_derive_dry_run(args))

    def test_arm_without_dryrun_is_live(self):
        # Explicitly armed, not dry-run: live is allowed (the intended use).
        args = types.SimpleNamespace(dry_run=False, arm=True)
        self.assertFalse(_derive_dry_run(args))

    def test_dryrun_wins_over_arm(self):
        args = types.SimpleNamespace(dry_run=True, arm=True)
        self.assertTrue(_derive_dry_run(args))

    def test_orchestrator_source_uses_the_gate(self):
        # Guard against the gate drifting: the orchestrator must set
        # state.dry_run from `args.dry_run or not args.arm`.
        src = (TOOLS_DIR / "exploit_chains" / "cross_chain_orchestrator.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("dry_run=args.dry_run or not args.arm", src)
        # And the arm flag must actually be referenced in the gate (not just
        # parsed/printed).
        self.assertIn("not args.arm", src)


if __name__ == "__main__":
    unittest.main()
