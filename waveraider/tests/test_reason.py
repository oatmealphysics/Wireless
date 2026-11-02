"""Reasoning stays grounded in the survey's facts: explanations quote each finding's evidence."""
import pytest

from waveraider.protocols import get_plugin
from waveraider.reason import get_backend
from waveraider.uas_classify import survey

P = get_plugin("uas")
SAMPLE = "data/uas_sample.pcap"
DRONE = "wifi_beacon:60:60:1f:00:00:01"


def _b():
    return get_backend("none", knowledge=P.knowledge, describe=P.describe)


def test_default_backend_is_offline_rules():
    assert get_backend("none").name == "local-rules"


def test_explain_focus_cites_the_reported_identity_and_operator_location():
    text = _b().explain(survey(SAMPLE), focus=DRONE)
    assert "uas_id=DRONE-SN-001A" in text
    assert "op_location_type=live GNSS" in text and "not authenticated" in text
    assert "SKYDIO-9988" not in text                    # focus stays on the selected transmitter


def test_explain_survey_covers_every_finding():
    text = _b().explain(survey(SAMPLE))
    assert "3 finding(s)" in text and "DRONE-SN-001A" in text and "SKYDIO-9988" in text


def test_ollama_backend_resolves():
    assert get_backend("ollama").name.startswith("ollama")


def test_cloud_backend_still_unwired():
    with pytest.raises(NotImplementedError):
        get_backend("cloud")
