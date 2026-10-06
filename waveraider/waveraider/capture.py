"""Layer 1 — capture ingest.

Reads 802.11 frames from a pcap. Offline by design: nothing here touches the
network. Uses a streaming reader so large captures don't have to fit in RAM.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from scapy.all import PcapReader  # type: ignore
from scapy.layers.dot11 import Dot11  # type: ignore

# 802.11 frame classes, per the type field in the frame control header.
_TYPE_NAMES = {0: "management", 1: "control", 2: "data"}


@dataclass
class CaptureSummary:
    """A first-pass tally of a capture. Deterministic — no model, no network."""

    path: str
    total: int = 0
    dot11: int = 0
    non_dot11: int = 0
    by_type: Counter = field(default_factory=Counter)

    def render(self) -> str:
        lines = [
            f"capture : {self.path}",
            f"frames  : {self.total}",
            f"802.11  : {self.dot11}  (non-802.11: {self.non_dot11})",
        ]
        for t in ("management", "control", "data", "other"):
            if self.by_type.get(t):
                lines.append(f"  {t:<11}: {self.by_type[t]}")
        return "\n".join(lines)


def iter_frames(path: str | Path) -> Iterator:
    """Yield packets one at a time from a pcap without loading it all at once."""
    with PcapReader(str(path)) as reader:
        for pkt in reader:
            yield pkt


def summarize(path: str | Path) -> CaptureSummary:
    """Tally frame counts by 802.11 type. This is Phase-0 proof-of-parse."""
    s = CaptureSummary(path=str(path))
    for pkt in iter_frames(path):
        s.total += 1
        if pkt.haslayer(Dot11):
            s.dot11 += 1
            ftype = pkt[Dot11].type
            s.by_type[_TYPE_NAMES.get(ftype, "other")] += 1
        else:
            s.non_dot11 += 1
    return s
