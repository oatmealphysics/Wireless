"""Wave Raider — a UAS detection platform in development, Remote ID first.

Current capabilities: recorded Remote ID decoding, Monitor and Settings with a
bundled sample session, and Inspect RF for the drone bands. Live Remote ID
reception remains roadmap work. The general protocol analyzers were retired in
0.6.0.

The version reported here (and in Settings › About) is read from the installed
package metadata, so it always matches the release in `pyproject.toml` — no
second place to keep in sync.
"""
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("waveraider")
except PackageNotFoundError:          # running from a source tree with no install
    __version__ = "0.0.0+source"
