#!/usr/bin/env python3
"""Back-compat shim. Prefer the installed `wr` command (see pyproject.toml)."""
import sys

from waveraider.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
