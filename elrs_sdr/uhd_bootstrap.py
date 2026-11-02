"""
UHD import + path bootstrap — the single place the project talks to the
`uhd` Python module.

Why this module exists (merging the two approaches that got proposed):
  * Gemini's idea: a `load_uhd()` that, if the first `import uhd` fails, walks
    the common system install dirs (source builds drop the module in a
    non-default site-packages, e.g. /usr/local/lib/python3.X/site-packages,
    which Ubuntu does NOT put on sys.path — it uses dist-packages).
  * The fix that actually makes it real: WIRE IT. radio.py and cli.py both
    import UHD through load_uhd() so the bootstrap is used, not dead code.

On an apt UHD install (Ubuntu noble, UHD 4.11) the first import already works
(apt puts it in /usr/lib/python3/dist-packages, on the default path), so
load_uhd() is a fast passthrough there. The candidate-path scan only fires on
source builds where the module landed somewhere off the default path.

load_uhd() returns the uhd module, or raises UHDNotInstalled with a message
that lists WHICH paths were tried and what 'usrp*' symbols exist — so a real
environment mismatch is self-diagnosing, never a bare ModuleNotFoundError.
"""

from __future__ import annotations

import importlib
import site
import sys
from pathlib import Path


class UHDNotInstalled(RuntimeError):
    """Raised when the 'uhd' Python module cannot be imported at all."""


def _candidate_paths() -> list[str]:
    """Directories where a UHD Python build is known to land."""
    pyv = f"python{sys.version_info.major}.{sys.version_info.minor}"
    paths = [
        # apt / Debian / Ubuntu (default dist-packages — usually already on path)
        "/usr/lib/python3/dist-packages",
        "/usr/local/lib/python3/dist-packages",
        # source builds (often OFF the default Ubuntu path -> the real gotcha)
        f"/usr/local/lib/{pyv}/site-packages",
        f"/usr/local/lib/{pyv}/dist-packages",
        "/usr/local/lib/python3/site-packages",
        # whatever this interpreter's site reports
    ]
    try:
        paths.extend(site.getsitepackages())
    except Exception:
        pass
    paths.append(site.getusersitepackages())
    # de-dupe, keep order
    seen = set()
    out = []
    for p in paths:
        if p and p not in seen and Path(p).is_dir():
            seen.add(p)
            out.append(p)
    return out


def load_uhd():
    """Import and return the `uhd` module, bootstrap-ing sys.path if needed.

    Returns the module on success. Raises UHDNotInstalled (with a useful
    message) if it genuinely isn't installed.
    """
    # 1) fast path: already importable (apt UHD, or correct PYTHONPATH)
    try:
        return importlib.import_module("uhd")
    except ImportError:
        pass

    # 2) source-build path: append known candidate dirs and retry
    tried = list(sys.path)
    for path in _candidate_paths():
        if path not in sys.path:
            sys.path.append(path)
            tried.append(path)
        try:
            return importlib.import_module("uhd")
        except ImportError:
            continue

    # 3) not found anywhere: report what we looked at
    raise UHDNotInstalled(
        "Could not import the 'uhd' Python module from any known location.\n"
        f"  Tried {len(tried)} sys.path entries, including:\n"
        f"    " + "\n    ".join(tried[-8:]) + "\n"
        "  Fix: install UHD with Python bindings on the machine where the\n"
        "  radio is attached, e.g. `sudo apt install uhd-host python3-uhd`\n"
        "  or a source build with -DLIBUHD_PYTHON_BINDINGS=ON (then ensure its\n"
        "  site-packages is on sys.path)."
    )
