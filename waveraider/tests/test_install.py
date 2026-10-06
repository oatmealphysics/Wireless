"""Operator install path: the README's Docker steps print the address to wait for and
stop promptly on Ctrl+C, and `wr serve` exits cleanly when interrupted."""
from __future__ import annotations

import errno
import re
from pathlib import Path

import pytest

from waveraider import web

ROOT = Path(web.__file__).parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()
README = (ROOT / "README.md").read_text()


def test_docker_output_is_unbuffered_and_stops_on_sigint():
    assert re.search(r"^ENV PYTHONUNBUFFERED=1$", DOCKERFILE, re.M)
    assert re.search(r"^STOPSIGNAL SIGINT$", DOCKERFILE, re.M)
    assert '"8899:8899"' in COMPOSE and ":/root/.waveraider" in COMPOSE


def test_readme_install_guide_covers_each_os_and_the_address_to_open():
    install = README.split("## Install — copy & paste", 1)[1].split("\n## ", 1)[0]
    for heading in ("### 1. Install Docker", "### 2. Download WR", "### 3. Start WR"):
        assert heading in install
    assert 'start.cmd' in install and 'sh start.sh' in install and 'sudo sh start.sh' in install
    assert "http://localhost:8899" in install and "Start sample session" in install
    assert '(docs/install-advanced.md' in install


def test_serve_prints_the_address_and_stops_cleanly_on_interrupt(monkeypatch, capsys):
    events = []

    class FakeServer:
        def __init__(self, address, handler):
            events.append(("bind", address, handler))

        def serve_forever(self):
            events.append("serving")
            raise KeyboardInterrupt

        def server_close(self):
            events.append("closed")

    monkeypatch.setattr(web, "ThreadingHTTPServer", FakeServer)
    try:
        web.serve(port=8899)
    except KeyboardInterrupt:
        pytest.fail("serve let Ctrl+C escape instead of stopping cleanly")
    out = capsys.readouterr().out
    assert "http://localhost:8899/" in out and "Wave Raider stopped." in out
    assert events == [("bind", ("", 8899), web.Handler), "serving", "closed"]


def test_serve_explains_a_busy_port_instead_of_a_traceback(monkeypatch):
    def busy(address, handler):
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(web, "ThreadingHTTPServer", busy)
    with pytest.raises(SystemExit) as exit_info:
        web.serve(port=8899)
    message = str(exit_info.value.code)
    assert "Port 8899 is already in use" in message and "--port" in message


def test_serve_keeps_other_bind_errors(monkeypatch):
    def denied(address, handler):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(web, "ThreadingHTTPServer", denied)
    with pytest.raises(OSError):
        web.serve(port=80)


WORKFLOW = (ROOT / ".github" / "workflows" / "multiarch.yml").read_text()


def test_ci_builds_both_cpu_types_and_never_publishes():
    assert re.search(r"platform: \[linux/amd64, linux/arm64\]", WORKFLOW)
    assert "push: false" in WORKFLOW and "load: true" in WORKFLOW
    assert not re.search(r"push: true|docker/login-action|registry|ghcr\.io|docker push", WORKFLOW)
    assert re.search(r"^permissions:\n  contents: read$", WORKFLOW, re.M)
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses)   # pinned to commits
    for check in ("wr --help", "DRONE-SN-001A", "WaveRaider Monitor", "Wave Raider stopped."):
        assert check in WORKFLOW


def test_offline_guide_covers_both_cpu_types():
    offline = (ROOT / "docs/install-advanced.md").read_text().split("Advanced: installing on a computer with no internet", 1)[1]
    assert "--platform linux/amd64" in offline and "linux/arm64" in offline
    assert "--format '{{.Architecture}}'" in offline and "docker pull python:3.11-slim-bookworm" in offline
    # NumPy wheels require matching OS/architecture as well as Python.
    assert "same\nPython version as the offline computer" in offline and "python3 --version" in offline
    assert "--no-index --find-links wheelhouse waveraider" in offline.split("before going offline", 1)[1]
    usage = (ROOT / "docs" / "usage.md").read_text().split("## Offline installation", 1)[1]
    assert "destination's Python version" in usage and "where you can" not in usage and "where you can" not in offline
