"""Tiny in-memory log ring buffer for the web UI's Logs view. Not a substitute
for real logging — just enough to see what the app is doing for troubleshooting.
"""
from __future__ import annotations

import time
from collections import deque

_BUF: deque = deque(maxlen=1000)


def log(level: str, msg) -> None:
    _BUF.append({"t": time.strftime("%H:%M:%S"), "level": level.upper(), "msg": str(msg)})


def lines() -> list:
    return list(_BUF)


def clear() -> None:
    _BUF.clear()
