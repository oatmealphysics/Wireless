"""Protocol registry. Remote ID (`uas`) is the only supported protocol. The general
Wi-Fi, Bluetooth, DMR, TETRA, analog and cellular analyzers were retired after v0.5.1;
their names are still recognized so the error can say so."""
from __future__ import annotations

from .base import ProtocolPlugin
from .uas import UasPlugin

DEFAULT_PROTOCOL = "uas"
_PLUGINS: dict[str, ProtocolPlugin] = {p.name: p for p in (UasPlugin(),)}
RETIRED = ('wifi', 'ble', 'bt', 'bt_traffic', 'dmr', 'tetra', 'analog', 'gsm', 'umts', 'cdma', 'lte', 'nr')


class RetiredProtocol(ValueError):
    """A protocol name that v0.5.1 accepted but this version no longer ships."""


def get_plugin(name: str | None = DEFAULT_PROTOCOL) -> ProtocolPlugin:
    key = (name or DEFAULT_PROTOCOL).lower()
    if key in _PLUGINS:
        return _PLUGINS[key]
    if key in RETIRED:
        raise RetiredProtocol(f"protocol {name!r} was retired; Remote ID ('uas') is the supported "
                              "protocol. v0.5.1 still includes the older analyzers.")
    raise ValueError(f"unknown protocol {name!r}; available: {', '.join(available())}")


def available() -> list[str]:
    return list(_PLUGINS)
