"""Offline tests for the bundled Claude Code status-line formatter."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


STATUSLINE_PATH = Path(__file__).parents[1] / "claude-config" / "statusline.py"
SPEC = importlib.util.spec_from_file_location("bundled_statusline", STATUSLINE_PATH)
assert SPEC is not None and SPEC.loader is not None
statusline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(statusline)


class TestRateLimitFormatting(unittest.TestCase):
    def test_requested_combined_format(self) -> None:
        now = 1_700_000_000
        payload = {
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 65,
                    "resets_at": now + (2 * 60 + 33) * 60,
                },
                "seven_day": {
                    "used_percentage": 67,
                    "resets_at": now + (5 * 24 * 60 + 22 * 60 + 19) * 60,
                },
            }
        }

        self.assertEqual(
            statusline.rate_limits_text(payload, now=now),
            "5h 35% left 2h33m - weekly 33% left 5d22h19m",
        )

    def test_missing_window_is_omitted(self) -> None:
        payload = {
            "rate_limits": {
                "five_hour": {"used_percentage": 25, "resets_at": 1_000},
            }
        }

        self.assertEqual(
            statusline.rate_limits_text(payload, now=0),
            "5h 75% left 17m",
        )

    def test_missing_rate_limits_hide_segment(self) -> None:
        self.assertIsNone(statusline.rate_limits_text({}))

    def test_percentage_and_elapsed_reset_are_clamped(self) -> None:
        payload = {
            "rate_limits": {
                "seven_day": {"used_percentage": 120, "resets_at": 99},
            }
        }

        self.assertEqual(
            statusline.rate_limits_text(payload, now=100),
            "weekly 0% left 0m",
        )


if __name__ == "__main__":
    unittest.main()
