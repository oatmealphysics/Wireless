"""Protocol registry. Each protocol is an independent plugin; adding the next
(Crossfire, DSMX, ...) is one import + one entry in `_build_plugins()`.

Remote ID (`uas`) is the identity path (needs scapy for Wi-Fi/BLE frames).
ELRS (`elrs`) is the SDR-decoded control-link path (needs only the project's
ELRS core). Plugins whose dependencies are missing on this host are skipped, so
an ELRS-only install still works without scapy and a full install still offers
both. The display order is preserved: Remote ID first, then ELRS."""
from __future__ import annotations

from .base import ProtocolPlugin
from .elrs import ElrsPlugin

DEFAULT_PROTOCOL = "uas"
RETIRED = ('wifi', 'ble', 'bt', 'bt_traffic', 'dmr', 'tetra', 'analog', 'gsm', 'umts', 'cdma', 'lte', 'nr')


def _build_plugins() -> dict[str, ProtocolPlugin]:
    plugins = []
    # Remote ID first (the default / identity path). It needs scapy; if scapy is
    # absent on this host, skip it rather than taking down the whole registry.
    try:
        from .uas import UasPlugin
        plugins.append(UasPlugin())
    except ImportError:  # scapy (or a uas dep) missing on this host
        pass
    # ELRS control links (SDR-decoded). Needs only the ELRS core.
    plugins.append(ElrsPlugin())
    return {p.name: p for p in plugins}


_PLUGINS: dict[str, ProtocolPlugin] = _build_plugins()


class RetiredProtocol(ValueError):
    """A protocol name that v0.5.1 accepted but this version no longer ships."""


def get_plugin(name: str | None = DEFAULT_PROTOCOL) -> ProtocolPlugin:
    key = (name or DEFAULT_PROTOCOL).lower()
    if key in _PLUGINS:
        return _PLUGINS[key]
    if key == DEFAULT_PROTOCOL and key not in _PLUGINS:
        raise ImportError(
            f"the default protocol {key!r} (Remote ID) needs scapy, which isn't "
            f"installed on this host. Install it (pip install scapy) or use "
            f"--protocol {', '.join(a for a in _PLUGINS if a != key)}.")
    if key in RETIRED:
        raise RetiredProtocol(f"protocol {name!r} was retired; the supported protocols "
                              f"are {', '.join(available())}. v0.5.1 still includes the older analyzers.")
    raise ValueError(f"unknown protocol {name!r}; available: {', '.join(available())}")


def available() -> list[str]:
    return list(_PLUGINS)
