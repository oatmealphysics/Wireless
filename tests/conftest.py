"""Shared fixtures for the test suite."""

import sys
from pathlib import Path

import pytest

# Add tools/ to the import path so tests can import the tool modules
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))


@pytest.fixture
def tmp_wordlist(tmp_path):
    """Create a temporary wordlist file with known phrases."""
    phrases = ["alpha", "bravo", "charlie", "delta"]
    wl = tmp_path / "wordlist.txt"
    wl.write_text("\n".join(phrases) + "\n")
    return wl, phrases
