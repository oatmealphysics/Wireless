"""Layer 3 — the reasoning layer, behind a pluggable interface.

Turns a protocol's findings into an operator-facing explanation. The knowledge
of what each finding *means* now comes from the active ProtocolPlugin, so this
module is protocol-agnostic — the same backends serve Remote ID and
anything added later. Every explanation is built from the finding's own evidence.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .model import Survey


class Backend(ABC):
    name: str = "base"

    @abstractmethod
    def explain(self, survey: Survey, focus: Optional[str] = None) -> str:
        ...


def _interpret(finding, knowledge: dict) -> str:
    ev = ", ".join(f"{k}={v}" for k, v in finding.evidence.items())
    kb = knowledge.get(finding.kind)
    if kb is None:
        return f"[!] {finding.summary} (evidence: {ev})"
    return (
        f"[!] This capture shows {kb['what']} (evidence: {ev}).\n"
        f"    What it means: {kb['means']}\n"
        f"    Next step: {kb['next']}"
    )


class DeterministicBackend(Backend):
    name = "local-rules"

    def __init__(self, knowledge: Optional[dict] = None, describe=None) -> None:
        self.knowledge = knowledge or {}
        self._describe = describe

    def explain(self, survey: Survey, focus: Optional[str] = None) -> str:
        if focus is not None:
            return self._explain_entity(survey, focus)
        return self._explain_survey(survey)

    def _explain_entity(self, survey: Survey, ent_id: str) -> str:
        e = survey.entities.get(ent_id)
        if e is None:
            return "No entity selected."
        if self._describe is not None:
            head = self._describe(e, survey)
        else:
            head = f"{e.label or e.id} — {e.kind} ({e.observations} observations)."
        lines = [head]
        findings = [f for f in survey.findings if f.evidence.get("entity") == ent_id]
        if not findings:
            lines.append("No anomalies flagged on this entity in the capture.")
        for f in findings:
            lines.append(_interpret(f, self.knowledge))
        return "\n".join(lines)

    def _explain_survey(self, survey: Survey) -> str:
        lines = [
            f"{survey.total_frames} frames  ·  {len(survey.entities)} entities  ·  "
            f"{len(survey.findings)} finding(s)."
        ]
        if not survey.findings:
            lines.append("No anomalies flagged.")
        for f in survey.findings:
            lines.append(_interpret(f, self.knowledge))
        return "\n".join(lines)


class OllamaBackend(Backend):
    """Local-LLM reasoning via an Ollama server. Offline — the model runs on this
    machine. It is handed the deterministic facts, never raw frames, so the rules
    backend stays the source of truth."""

    def __init__(
        self,
        model: str = "llama3.2:3b",
        host: str = "http://localhost:11434",
        knowledge: Optional[dict] = None,
        describe=None,
    ) -> None:
        self.name = f"ollama:{model}"
        self.model = model
        self.host = host.rstrip("/")
        self._rules = DeterministicBackend(knowledge, describe)

    def _prompt(self, facts: str) -> str:
        return (
            "You are an RF analysis assistant for a security operator. Below are "
            "DETERMINISTIC facts extracted from a capture. Explain, in a few tight "
            "sentences, what an operator should take from them and what to check "
            "next. Do not invent devices, counts, or findings not in the facts. If "
            "something is ambiguous (e.g. a shared SSID that could be roaming or an "
            f"evil twin), say so.\n\nFACTS:\n{facts}\n\nANALYSIS:"
        )

    def explain(self, survey: Survey, focus: Optional[str] = None) -> str:
        import json
        import urllib.request

        facts = self._rules.explain(survey, focus)
        body = json.dumps(
            {"model": self.model, "prompt": self._prompt(facts), "stream": False}
        ).encode()
        req = urllib.request.Request(
            f"{self.host}/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                out = json.loads(resp.read()).get("response", "").strip()
        except Exception as e:
            return f"{facts}\n\n(ollama unavailable: {e}; showing rules output)"
        return out or facts


def get_backend(name: str = "none", knowledge: Optional[dict] = None, describe=None) -> Backend:
    """Resolve a reasoning backend. `knowledge` comes from the active protocol
    plugin. Defaults to the offline rules brain."""
    key = (name or "none").lower()
    if key in ("none", "rules", "local-rules", "deterministic"):
        return DeterministicBackend(knowledge, describe)
    if key.startswith("ollama"):
        model = key.split(":", 1)[1] if ":" in key else "llama3.2:3b"
        return OllamaBackend(model=model, knowledge=knowledge, describe=describe)
    if key == "cloud":
        raise NotImplementedError("the cloud backend is not wired yet")
    raise ValueError(f"unknown reasoning backend: {name!r}")
