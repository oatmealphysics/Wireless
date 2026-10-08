"""The wr CLI: Remote ID by default, and retired protocol names fail before anything opens."""
import inspect
import subprocess
import sys

import pytest

from waveraider import cli, tui
from waveraider.cli import main

SAMPLE = "data/uas_sample.pcap"


def test_default_protocol_renders_the_remote_id_table(capsys):
    assert main(["wr", SAMPLE]) == 0
    out = capsys.readouterr().out
    assert f"capture : {SAMPLE}" in out and "Transmitter  UAS ID" in out
    assert "DRONE-SN-001A" in out and "SKYDIO-9988" in out and "FINDINGS (3)" in out


def test_explain_and_html_report_use_remote_id(capsys, tmp_path):
    assert main(["wr", "--explain", SAMPLE]) == 0
    assert "decoded UAS Remote ID broadcast" in capsys.readouterr().out
    report = tmp_path / "report.html"
    assert main(["wr", "--protocol", "uas", "--html", str(report), SAMPLE]) == 0
    assert "DRONE-SN-001A" in report.read_text()


@pytest.mark.parametrize("args", [
    ["--protocol", "wifi"], ["--protocol", "gsm"], ["--tui", "--protocol", "wifi"],
    ["--html", "never.html", "--protocol", "nr"], ["--explain", "--protocol", "dmr"],
])
def test_retired_protocols_exit_before_opening_a_source_or_the_tui(args, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_source", lambda spec: pytest.fail("source opened"))
    monkeypatch.setattr(tui, "run", lambda *a, **k: pytest.fail("TUI started"))
    assert main(["wr", *args, "missing.pcap"]) == 2
    err = capsys.readouterr().err
    assert "was retired; the supported protocols are uas, elrs" in err
    assert not (tmp_path / "never.html").exists()


def test_unknown_protocol_exits_with_its_own_message(capsys, monkeypatch):
    monkeypatch.setattr(cli, "get_source", lambda spec: pytest.fail("source opened"))
    assert main(["wr", "--protocol", "zigbee", "missing.pcap"]) == 2
    err = capsys.readouterr().err
    assert "unknown protocol 'zigbee'" in err and "retired" not in err


def test_usage_names_remote_id_and_elrs(capsys):
    assert main(["wr"]) == 2
    err = capsys.readouterr().err
    assert "--protocol: uas (Remote ID, the default) or elrs (ExpressLRS control link, SDR-decoded)." in err
    assert "legacy default" not in err and "dmr | tetra" not in err and "/classic" not in err


@pytest.mark.parametrize("args", [["--help"], ["-h"], ["help"], ["serve", "--help"], ["--explain", "-h", SAMPLE]])
def test_help_prints_usage_and_does_nothing_else(args, capsys, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("help must not open a capture or start a server")
    monkeypatch.setattr(cli, "survey_source", forbidden)
    monkeypatch.setattr("waveraider.web.serve", forbidden)
    assert main(["wr", *args]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("usage: wr ") and "wr --help" in out and err == ""


def test_tui_defaults_to_remote_id_and_rejects_retired_names_before_opening():
    assert inspect.signature(tui.run).parameters["protocol"].default == "uas"
    assert inspect.signature(tui.WaveRaiderApp.__init__).parameters["protocol"].default == "uas"
    with pytest.raises(ValueError, match="was retired"):
        tui.WaveRaiderApp("file:missing.pcap", protocol="wifi")


def test_module_entrypoint_runs():
    """`python -m waveraider.cli` must execute main() (needs the __main__ guard)."""
    r = subprocess.run([sys.executable, "-m", "waveraider.cli", SAMPLE], capture_output=True, text=True)
    assert r.returncode == 0
    assert f"capture : {SAMPLE}" in r.stdout and "FINDINGS" in r.stdout
