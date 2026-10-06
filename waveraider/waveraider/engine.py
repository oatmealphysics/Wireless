"""Protocol-driven survey runner — glue between a capture source and a protocol
plugin's classifier."""
from __future__ import annotations

from . import targets
from .protocols.base import ProtocolPlugin
from .sources import Source


def survey_source(plugin: ProtocolPlugin, source: Source):
    """Feed every frame from `source` into a fresh classifier for `plugin` and
    return the completed survey."""
    clf = plugin.new_classifier()
    # Label the survey with where it actually came from, so headers/reports show
    # the real capture instead of the classifier's default "<live>".
    origin = getattr(source, "path", None) or (
        f"live:{source.iface}" if getattr(source, "iface", None) else None
    )
    if origin:
        clf.survey.path = origin
    if getattr(plugin, "input_kind", "pcap") == "text":
        path = getattr(source, "path", None)
        if path:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    clf.update(line.rstrip("\n"))
    else:
        for pkt in source.frames():
            clf.update(pkt)
    clf.rebuild_findings()
    # Cross-protocol targeting: flag any entity on the operator's watchlist,
    # regardless of which protocol produced it.
    targets.apply_to_survey(clf.survey)
    return clf.survey
