"""Protocol-neutral data model.

An Entity is any observed thing — today a Remote ID transmitter on one transport —
with a kind, a label, and a bag of protocol-specific attributes. A Finding is
something worth attention, tied to an entity by id. The model knows nothing about a
particular protocol, so the capture, TUI, report and reasoning layers stay shared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Entity:
    id: str                              # unique key (MAC / BSSID / address / TG id)
    kind: str                            # "access_point", "client", "ble_device", ...
    label: str = ""                      # human name (SSID, device name)
    attrs: dict = field(default_factory=dict)   # protocol-specific facts
    observations: int = 0                # frames/packets seen for this entity


@dataclass
class Finding:
    """Something worth the operator's attention. `evidence` carries the counts
    and ids it rests on and SHOULD include an "entity" key linking it to an
    Entity — no claim without its receipts."""

    kind: str
    severity: str                        # info | warning | critical
    summary: str
    evidence: dict = field(default_factory=dict)


@dataclass
class Survey:
    path: str
    total_frames: int = 0
    entities: dict = field(default_factory=dict)     # id -> Entity
    findings: list = field(default_factory=list)     # list[Finding]
    diagnostics: dict = field(default_factory=dict)  # problem -> count; input that isn't a detection

    def of_kind(self, *kinds: str) -> list:
        return [e for e in self.entities.values() if e.kind in kinds]
