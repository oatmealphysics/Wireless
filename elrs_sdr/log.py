"""
Centralised logging for elrs_sdr.

The predecessor wrote UHD binding-probe diagnostics to stderr with bare
``print(..., file=sys.stderr)`` calls that fired on every run — noisy, and it
buried real errors. Everything now goes through the standard ``logging``
module so verbosity is a single flag.

Usage:
    from .log import get_logger, setup
    log = get_logger(__name__)
    ...
    setup(verbose=True)     # once, from the CLI entry point

By default (no setup() call, or setup(verbose=False)) only INFO and above are
shown, so a clean run is quiet. ``--verbose`` drops the level to DEBUG, which
surfaces the UHD binding-probe trail that used to be unconditional.
"""

from __future__ import annotations

import logging
import sys

_ROOT = "elrs_sdr"
_CONFIGURED = False


def setup(verbose: bool = False, quiet: bool = False) -> None:
    """Configure the package logger. Idempotent; the last call wins."""
    global _CONFIGURED
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.ERROR
    else:
        level = logging.INFO
    logger = logging.getLogger(_ROOT)
    logger.setLevel(level)
    # Replace handlers so repeated setup() calls don't stack duplicates.
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the package root.

    If setup() was never called we still return a usable logger; it inherits
    the root's default (WARNING) so nothing is printed until a real problem.
    """
    short = name.split(".")[-1]
    return logging.getLogger(f"{_ROOT}.{short}")
