"""Layer 1 — pluggable capture sources.

A source yields 802.11 frames. Same contract whether they come from a file all
at once, a file streamed in pseudo-real-time (for development and demos without
hardware), or the incomplete live monitor-mode interface. File/replay inputs support
recorded analysis; live Remote ID reception is not supported yet.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Iterator

from .capture import iter_frames


class Source(ABC):
    name: str = "base"

    @abstractmethod
    def frames(self) -> Iterator:
        """Yield packets. May block between frames (live/replay)."""


class FileSource(Source):
    """Read a whole pcap as fast as possible — the batch path."""

    name = "file"

    def __init__(self, path: str) -> None:
        self.path = path

    def frames(self) -> Iterator:
        yield from iter_frames(self.path)


class ReplaySource(Source):
    """Stream a pcap frame-by-frame at a fixed rate to emulate a live feed.
    Lets anyone watch the monitor update in real time with no capture hardware."""

    name = "replay"

    def __init__(self, path: str, rate_hz: float = 40.0) -> None:
        self.path = path
        self.delay = 1.0 / rate_hz if rate_hz > 0 else 0.0

    def frames(self) -> Iterator:
        for pkt in iter_frames(self.path):
            yield pkt
            if self.delay:
                time.sleep(self.delay)


class LiveSource(Source):
    """Incomplete monitor-mode source; packet delivery needs a callback queue.
    Live Remote ID is unsupported. Requires a card that supports it
    and the privileges to use it (root / sudo); availability is OS-dependent.
    Kept deliberately thin — the heavy lifting is the same classifier."""

    name = "live"

    def __init__(self, iface: str) -> None:
        self.iface = iface

    def frames(self) -> Iterator:
        from scapy.sendrecv import AsyncSniffer  # imported lazily

        sniffer = AsyncSniffer(iface=self.iface, store=False)
        sniffer.start()
        try:
            while True:
                # AsyncSniffer with a callback would be tighter; for v1 we poll
                # its results queue via a prn bridge in a later iteration.
                time.sleep(0.1)
                for pkt in list(sniffer.results or []):
                    yield pkt
                sniffer.results = []
        finally:
            sniffer.stop()


def get_source(spec: str) -> Source:
    """Resolve a source from a spec string:
    'file:<path>' | 'replay:<path>[@rate]' | 'live:<iface>'
    | 'sdr:[freq@rate]' (live ELRS from the SDR) | 'elrs:<path>' (recorded ELRS).
    A bare path is treated as file:<path>."""
    if ":" not in spec:
        return FileSource(spec)
    kind, _, rest = spec.partition(":")
    kind = kind.lower()
    if kind == "file":
        return FileSource(rest)
    if kind == "replay":
        path, _, rate = rest.partition("@")
        return ReplaySource(path, rate_hz=float(rate) if rate else 40.0)
    if kind == "live":
        return LiveSource(rest)
    # ELRS sources (SDR decode) — resolved lazily so the core sources module has
    # no hard dependency on the ELRS plugin.
    if kind in ("sdr", "elrs"):
        from .protocols.elrs import sources as _elrs_src
        if kind == "sdr":
            # 'sdr:' (auto) | 'sdr:<freq_hz>' | 'sdr:<freq_hz>@<rate>'
            freq = rate = None
            if rest:
                rest2, _, r = rest.partition("@")
                rate = r or None
                freq = _to_hz(rest2)
            return _elrs_src.ElrsSdrSource(freq_hz=freq, rate=rate or _elrs_src.DEFAULT_RATE)
        return _elrs_src.ElrsFileSource(rest)
    # Not a known scheme — treat the whole thing as a path.
    return FileSource(spec)


def _to_hz(s: str) -> float | None:
    """'2440' | '2440M' | '2440000000' -> Hz. None if empty/unparseable."""
    s = s.strip()
    if not s:
        return None
    try:
        if s.endswith(("M", "m")):
            return float(s[:-1]) * 1e6
        v = float(s)
        # a bare number < 1000 is MHz-ish (e.g. '2440'); otherwise Hz
        return v * 1e6 if v < 1000 else v
    except ValueError:
        return None
