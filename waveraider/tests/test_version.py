"""The UI version must track the packaged release — no hardcoded drift."""
from importlib.metadata import version as pkg_version

import waveraider
from waveraider import monitor, settings


def test_version_is_derived_from_package_metadata():
    # __version__ comes from the installed package, so it equals pyproject's.
    assert waveraider.__version__ == pkg_version("waveraider")


def test_settings_about_reports_the_current_version():
    d = settings.about(session=monitor.MonitorSession())
    assert d["version"] == waveraider.__version__
    assert d["version"] not in ("", "0.0.0+source")
