#!/usr/bin/env python3
"""Small, dependency-free Claude/Codex-inspired powerline status line.

Claude Code sends a JSON status payload on stdin.  With no payload, the
formatter falls back to the current working directory and git state, which
makes the same file useful from a shell prompt or tmux.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


RESET = "\033[0m"
SEP = "\033[90m│\033[0m"
COLORS = {
    "brand": "\033[38;5;215m",
    "path": "\033[38;5;117m",
    "git": "\033[38;5;150m",
    "model": "\033[38;5;183m",
    "context": "\033[38;5;221m",
    "cost": "\033[38;5;114m",
    "muted": "\033[38;5;245m",
}


def read_payload() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def run_git(cwd: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=0.25,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def short_path(cwd: str, payload: dict[str, Any]) -> str:
    workspace = nested(payload, "workspace", "project_dir") or nested(
        payload, "workspace", "current_dir"
    )
    path = Path(str(workspace or cwd)).expanduser()
    try:
        return "~" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def segment(kind: str, text: str, plain: bool) -> str:
    if plain:
        return text
    return f"{COLORS[kind]}{text}{RESET}"


def reset_countdown(resets_at: Any, *, now: float | None = None) -> str | None:
    """Format an epoch reset time as a compact, non-negative countdown."""
    if isinstance(resets_at, bool) or not isinstance(resets_at, (int, float)):
        return None

    seconds_left = max(0.0, float(resets_at) - (time.time() if now is None else now))
    total_minutes = math.ceil(seconds_left / 60)
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)

    if days:
        return f"{days}d{hours}h{minutes}m"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def rate_limit_window(
    payload: dict[str, Any], window: str, label: str, *, now: float | None = None
) -> str | None:
    """Return remaining percentage and reset countdown for one rate-limit window."""
    used = nested(payload, "rate_limits", window, "used_percentage")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None

    remaining = min(100.0, max(0.0, 100.0 - float(used)))
    text = f"{label} {remaining:.0f}% left"
    countdown = reset_countdown(
        nested(payload, "rate_limits", window, "resets_at"), now=now
    )
    return f"{text} {countdown}" if countdown else text


def rate_limits_text(payload: dict[str, Any], *, now: float | None = None) -> str | None:
    """Format the five-hour and weekly rate limits when Claude provides them."""
    windows = [
        rate_limit_window(payload, "five_hour", "5h", now=now),
        rate_limit_window(payload, "seven_day", "weekly", now=now),
    ]
    available = [window for window in windows if window]
    return " - ".join(available) if available else None


def main() -> int:
    payload = read_payload()
    cwd = str(
        nested(payload, "workspace", "current_dir")
        or payload.get("cwd")
        or os.getcwd()
    )
    plain = bool(os.environ.get("NO_COLOR"))
    label = os.environ.get("AGENT_STATUS_LABEL", "Claude")
    model = nested(payload, "model", "display_name") or nested(payload, "model", "id")
    if model:
        model = str(model).replace("claude-", "").replace("gpt-", "gpt-")

    branch = run_git(cwd, "branch", "--show-current") or run_git(
        cwd, "rev-parse", "--short", "HEAD"
    )
    dirty = bool(run_git(cwd, "status", "--porcelain"))
    git_text = f"{branch}{'*' if dirty else ''}" if branch else "nogit"

    used = nested(payload, "context_window", "used_percentage")
    if used is None:
        used = nested(payload, "context", "used_percentage")
    context_text = f"ctx {float(used):.0f}%" if isinstance(used, (int, float)) else "ctx --"

    cost = nested(payload, "cost", "total_cost_usd")
    cost_text = f"${float(cost):.2f}" if isinstance(cost, (int, float)) else None
    limits_text = rate_limits_text(payload)

    parts = [
        segment("brand", f"◆ {label}", plain),
        segment("path", short_path(cwd, payload), plain),
        segment("git", f"⑂ {git_text}", plain),
    ]
    if model:
        parts.append(segment("model", str(model), plain))
    parts.append(segment("context", context_text, plain))
    if limits_text:
        parts.append(segment("context", limits_text, plain))
    if cost_text:
        parts.append(segment("cost", cost_text, plain))
    print(f" {(' ' + SEP + ' ').join(parts)} ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
