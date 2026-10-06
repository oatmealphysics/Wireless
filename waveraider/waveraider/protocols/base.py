"""The protocol-plugin contract.

A plugin bundles everything protocol-specific: how to classify frames into
Entities, how each finding should be read (knowledge), and how to present its
entities (columns / rows / detail). Capture, reasoning, and the TUI stay
protocol-agnostic, so a new protocol is a new plugin, not a rewrite.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from ..model import Entity, Survey

_SEV = {"critical": "[!!]", "warning": "[! ]", "info": "[ i]"}


@runtime_checkable
class ClassifierLike(Protocol):
    survey: object

    def update(self, pkt) -> None: ...
    def rebuild_findings(self) -> None: ...


class ProtocolPlugin(ABC):
    name: str = "base"
    knowledge: dict = {}
    primary_kinds: tuple = ("*",)   # entity kinds to list ("*" = all)
    sample: str | None = None       # bundled demo capture filename
    input_kind: str = "pcap"        # "pcap" (frames) or "text" (decoder output lines)
    implemented: bool = True         # False = planned/stub (needs hardware or data)
    requires: str = ""               # what a planned protocol needs to become real
    blurb: str = ""                  # short description shown on a planned tab
    category: str = ""               # display group, e.g. "UAS"
    label: str = ""                  # display name, e.g. "Remote ID"

    @abstractmethod
    def new_classifier(self) -> ClassifierLike: ...

    # ---- presentation (generic defaults; plugins may override) ----------

    def list_entities(self, survey: Survey) -> list[Entity]:
        es = (
            list(survey.entities.values())
            if "*" in self.primary_kinds
            else survey.of_kind(*self.primary_kinds)
        )
        return sorted(es, key=lambda e: -e.observations)

    def columns(self) -> list[str]:
        return ["ID", "Kind", "Label", "Seen", "Alerts"]

    def row(self, e: Entity, alerts: int) -> list[str]:
        return [e.id, e.kind, e.label or "-", str(e.observations),
                str(alerts) if alerts else "-"]

    def describe(self, e: Entity, survey: Survey) -> str:
        a = ", ".join(f"{k}={v}" for k, v in e.attrs.items())
        base = f"{e.label or e.id} — {e.kind} ({e.observations} observations)"
        return base + (f" [{a}]" if a else "")

    def detail(self, survey: Survey, e: Entity) -> str:
        lines = [f"ID     {e.id}", f"Kind   {e.kind}", f"Label  {e.label or '-'}",
                 f"Seen   {e.observations}"]
        for k, v in e.attrs.items():
            lines.append(f"  {k}: {v}")
        fs = [f for f in survey.findings if f.evidence.get("entity") == e.id]
        lines += ["", f"FINDINGS ({len(fs)})"]
        lines += [f"  {_SEV.get(f.severity, '[ ?]')} {f.summary}" for f in fs] or ["  none"]
        return "\n".join(lines)
