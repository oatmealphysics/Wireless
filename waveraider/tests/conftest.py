"""Shared test isolation for the session history."""
import pytest


@pytest.fixture(autouse=True)
def _session_history_in_tmp(tmp_path, monkeypatch):
    """Redirect session-history operations during each test to a temporary folder; collection-time
    work and other stores require separate isolation (the documented recipe still sets every
    WAVERAIDER_* variable for the whole process)."""
    monkeypatch.setenv("WAVERAIDER_SESSIONS", str(tmp_path / "sessions"))
