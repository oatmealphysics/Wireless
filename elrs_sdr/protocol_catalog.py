"""
Target protocol catalog — the reference list for "positively ID every drone protocol".

WHERE THIS COMES FROM
---------------------
Derived from the **reference counter-UAS passive SDR**'s "Active Protocols" list
(see `docs/REFERENCE_COUNTER_UAS_SDR.md` §2): a shipped, passive, library-based
C-UAS detector that scans for 16 named drone RF protocols. That list is a
concrete, real-world answer to "which protocols should a drone detector cover?",
so we record it here as our target list and track OUR level against each one.

**Naming (operator directive):** never name the reference device's vendor/make
(or any acronym/abbreviation of it) anywhere. It is "the counter-UAS passive SDR".

WHY IT'S CODE AND NOT PROSE
---------------------------
The point is the GAP, tracked honestly. Two findings from the reference drive it:

1. **The specific-protocol bar.** The reference device lists **ExpressLRS**,
   **Crossfire**, and generic **LoRa** as three SEPARATE protocols — even though
   ELRS is a fork of Crossfire and both are LoRa-PHY. So it fingerprints at the
   *specific protocol* level. Our `positivedet.lora_chirp` is at the *family*
   level ("LoRa (ELRS/Meshtastic/sub-1G)"). That is a real, named gap, and
   `DetectionLevel` below is the ladder we have to climb to close it.

2. **Per-link ID extraction is a shipped capability.** The reference device
   whitelists "by UID" and offers "Copy Net ID to Clipboard" — i.e. it pulls a
   per-platform identifier out of the detected link. That is exactly the
   operator's "identify a drone by a unique ID from the RF" goal, so each entry
   records which per-link ID *we* can actually extract today (and the honest
   `None` where we can't).

HONESTY CONTRACT (AGENTS.md §3)
-------------------------------
`our_level` is what we can do on a REAL capture, not what a code path exists for.
Specifically: ELRS stays at FAMILY (not SPECIFIC/IDENTIFIED) because on real
wideband IQ every burst still comes back `fsk_unidentified` with no CRC-valid
lock — the sync is UID-seeded and the UID is not obtainable, so identification
waits on sync-word air-recovery. A detector that is synthetic-only is marked
`validated=False`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class DetectionLevel(IntEnum):
    """The ladder from 'nothing' to 'identified', in increasing capability.

    Ordered (IntEnum) so coverage can be compared/sorted meaningfully.
    """

    NONE = 0        # we do not detect it at all
    PRESENCE = 1    # "energy of roughly this shape is here" (band/shape only)
    FAMILY = 2      # the PHY family is identified (e.g. "LoRa"), not the protocol
    SPECIFIC = 3    # the specific protocol is identified (ELRS vs Crossfire vs LoRa)
    IDENTIFIED = 4  # specific protocol AND its per-link identifier is extracted

    @property
    def label(self) -> str:
        return {
            DetectionLevel.NONE: "none",
            DetectionLevel.PRESENCE: "presence",
            DetectionLevel.FAMILY: "family",
            DetectionLevel.SPECIFIC: "specific",
            DetectionLevel.IDENTIFIED: "identified",
        }[self]


@dataclass(frozen=True)
class TargetProtocol:
    """One protocol on the target list, plus our honest status against it."""

    name: str                      # as named by the reference catalog
    phy: str                       # PHY family / modulation class
    bands: tuple[str, ...]         # rough band(s) we'd look in
    reference_default_on: bool     # the reference device's default enable state
    our_level: DetectionLevel
    our_detector: str | None = None   # the code path, if any
    validated: bool = False           # True only if checked against a REAL capture
    link_id: str | None = None        # the per-link identifier we can extract today
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# The catalog. `reference_default_on` mirrors the reference device (15 of 16 ON;
# WiFi is the only one OFF by default there — we deliberately keep WiFi ON
# because the 802.11 MAC/BSSID is our best hardware-unique ID today).
# --------------------------------------------------------------------------- #
CATALOG: tuple[TargetProtocol, ...] = (
    TargetProtocol(
        name="ExpressLRS",
        phy="LoRa chirp (SF5-9, long range) + FLRC/GFSK (520/650, high rate); 2.4/868/915; sync = UID-seeded (FLRC) / 0x12 stock (LoRa)",
        bands=("2.4", "868", "915"),
        reference_default_on=True,
        our_level=DetectionLevel.FAMILY,
        our_detector="detect.py (the engine: hysteresis burst detect -> per-protocol demod -> "
                     "Net ID extraction -> specific-protocol fingerprint); "
                     "positivedet.lora_chirp + core.identify (GFSK/FLRC + LoRa paths); "
                     "core.lora_payload (the LoRa payload chain + CRC-gated SYNC air-recovery)",
        validated=True,     # the LoRa chirp detector is capture-validated
        link_id=None,       # REVERTED: the "2ee2" recovery was refuted — see notes §24
        notes="PRIMARY TARGET. Level is FAMILY, and the earlier IDENTIFIED claim was "
              "REVERTED (2026-10-08). We positively detect + measure the burst physics on "
              "real B205 IQ, and the PHY is pinned: LoRa SF7 @ BW 812 kHz (the ELRS 2.4 GHz "
              "150 Hz rate), validated 17/20 bursts across two independent captures. The "
              "demod is frame-locked: preamble lock (10-13 identical symbols) plus LoRa sync "
              "word 0x12 read off the air across 12 bursts. BUT no per-link ID: the `2ee2` "
              "recovery was a self-seeded-CRC artifact (decoded rf_rate_enum=204, impossible "
              "for a 6-rate radio, and the two corroborating hits were byte-identical, which "
              "real advancing SYNC packets cannot be). Gated out in core.lora_payload. "
              "Reaching IDENTIFIED needs a CRC-valid ELRS SYNC payload whose fields are "
              "plausible AND which advances across bursts. See notes §24.",
        aliases=("ELRS",),
    ),
    TargetProtocol(
        name="Crossfire",
        phy="LoRa chirp (TBS, ELRS's parent)",
        bands=("2.4", "900"),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        our_detector=None,
        link_id=None,
        notes="THE SPECIFIC-PROTOCOL GAP, stated plainly: Crossfire and ELRS share "
              "the LoRa PHY, so our family-level chirp detector cannot tell them "
              "apart. The reference device lists them separately, so distinguishing "
              "them is a solved problem in a shipped product -- it needs payload/"
              "timing fingerprinting (packet cadence, sync/CRC layout), not just "
              "chirp presence.",
        aliases=("TBS Crossfire",),
    ),
    TargetProtocol(
        name="LoRa",
        phy="LoRa chirp (generic / Meshtastic / sub-1G telemetry)",
        bands=("2.4", "900", "433"),
        reference_default_on=True,
        our_level=DetectionLevel.FAMILY,
        our_detector="positivedet.lora_chirp",
        validated=True,
        link_id=None,
        notes="This is the level we are actually at for the whole LoRa class: we "
              "positively identify the chirp family, not which protocol is riding it.",
    ),
    TargetProtocol(
        name="WiFi",
        phy="802.11a/g/n OFDM",
        bands=("2.4", "5ghz"),
        reference_default_on=False,   # OFF by default on the reference device
        our_level=DetectionLevel.IDENTIFIED,
        our_detector="wifi.py (L-SIG demod + 4-check validation) + positivedet.ofdm_preamble",
        validated=True,
        link_id="BSSID / MAC (wifi.parse_beacon_mac, + OUI vendor lookup)",
        notes="Our STRONGEST case and the model for the ID goal: a validated L-SIG "
              "decode confirms the protocol, and the beacon MAC/BSSID is a genuinely "
              "hardware-unique per-link ID. Note we intentionally diverge from the "
              "reference device, which ships WiFi OFF by default -- for us 802.11 is "
              "the main source of a hardware-unique ID.",
    ),
    TargetProtocol(
        name="DJI OcuSync",
        phy="proprietary OFDM",
        bands=("2.4", "5ghz"),
        reference_default_on=True,
        our_level=DetectionLevel.PRESENCE,
        our_detector="positivedet.ocusync_phy",
        validated=False,    # synthetic-calibrated only
        link_id=None,
        notes="Presence/shape only, and SYNTHETIC -- not yet validated against a real "
              "OcuSync capture. No decode, so no serial/Net ID.",
    ),
    TargetProtocol(
        name="DJI LightBridge",
        phy="proprietary OFDM (video link)",
        bands=("2.4", "5.8"),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        our_detector=None,
        link_id=None,
        notes="Not covered. Would likely fall out of a generalized OFDM/OcuSync-class "
              "detector plus a LightBridge-specific signature.",
    ),
    TargetProtocol(
        name="OcuSync Aeroscope",
        phy="DJI DroneID broadcast (Remote-ID-adjacent)",
        bands=("2.4", "5.8"),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        our_detector=None,
        link_id=None,
        notes="DJI DroneID carries a serial + operator location, i.e. a real per-link "
              "ID -- a high-value ID target. Deferred by the operator (no DJI hardware "
              "on hand to validate against).",
    ),
    TargetProtocol(
        name="Video",
        phy="analog FPV video (FM/VSB)",
        bands=("1.2", "5.8"),
        reference_default_on=True,
        our_level=DetectionLevel.PRESENCE,
        our_detector="positivedet.analog_video",
        validated=False,
        link_id=None,
        notes="Analog video carries no digital ID by construction -- PRESENCE is the "
              "ceiling for this one (a video downlink implies an aircraft, nothing more).",
    ),
    TargetProtocol(
        name="Autel Evo",
        phy="proprietary (Autel COTS)",
        bands=("2.4", "5.8"),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        link_id=None,
        notes="Not covered; no reference capture available.",
    ),
    TargetProtocol(
        name="Ghost",
        phy="FHSS / proprietary (long-range RC)",
        bands=("2.4",),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        link_id=None,
        notes="ELRS-adjacent long-range control link; not covered.",
    ),
    TargetProtocol(
        name="SiK - Custom Drone",
        phy="FHSS GFSK (SiK / 3DR telemetry)",
        bands=("433", "900"),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        link_id=None,
        notes="SiK is open (ask for firmware-level detail); a plausible early win "
              "since the PHY is documented, unlike the proprietary entries.",
    ),
    TargetProtocol(
        name="DragonLink",
        phy="FHSS (long-range COTS)",
        bands=("433",),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        link_id=None,
        notes="Not covered. `positivedet.fhss_hop` could give a generic FHSS presence "
              "hint if fed a frequency track from the scanner loop.",
    ),
    TargetProtocol(
        name="Microhard",
        phy="FHSS/OFDM IP radio",
        bands=("900", "2.4"),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        link_id=None,
        notes="Not covered.",
    ),
    TargetProtocol(
        name="DigiXTend - Custom Drone",
        phy="FHSS (Digi XTend long-range)",
        bands=("900",),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        link_id=None,
        notes="Not covered.",
    ),
    TargetProtocol(
        name="RMILEC - Long Range Drone",
        phy="FHSS (long-range RC)",
        bands=("433", "900"),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        link_id=None,
        notes="Not covered.",
    ),
    TargetProtocol(
        name="NS1",
        phy="narrowband (vendor-specific)",
        bands=("2.4", "5.8"),
        reference_default_on=True,
        our_level=DetectionLevel.NONE,
        link_id=None,
        notes="Not covered; the reference manual names it without describing the PHY.",
    ),
)


# --------------------------------------------------------------------------- #
# Queries / reporting
# --------------------------------------------------------------------------- #
def get(name: str) -> TargetProtocol | None:
    """Look a protocol up by name or alias, case-insensitively."""
    key = name.strip().lower()
    for p in CATALOG:
        if p.name.lower() == key or any(a.lower() == key for a in p.aliases):
            return p
    return None


def by_level(level: DetectionLevel) -> tuple[TargetProtocol, ...]:
    return tuple(p for p in CATALOG if p.our_level == level)


def coverage() -> dict[str, int]:
    """Count of target protocols at each detection level."""
    out = {lvl.label: 0 for lvl in DetectionLevel}
    for p in CATALOG:
        out[p.our_level.label] += 1
    return out


def with_link_id() -> tuple[TargetProtocol, ...]:
    """Protocols where we can actually extract a per-link identifier today."""
    return tuple(p for p in CATALOG if p.link_id)


def gap_report() -> str:
    """Human-readable gap analysis against the reference target list."""
    lines = [
        "Target protocol coverage vs the reference counter-UAS passive SDR",
        "(reference catalog: 16 protocols; see docs/REFERENCE_COUNTER_UAS_SDR.md §2)",
        "",
    ]
    cov = coverage()
    lines.append("  level       count  " + "-" * 28)
    for lvl in sorted(DetectionLevel, reverse=True):
        lines.append(f"  {lvl.label:<11} {cov[lvl.label]:>5}")
    lines.append("")
    lines.append(f"  total targets: {len(CATALOG)}")
    ids = with_link_id()
    lines.append(f"  per-link ID extractable: {len(ids)} "
                 f"({', '.join(p.name for p in ids) if ids else 'none'})")
    lines.append("")
    lines.append("  per protocol:")
    for p in sorted(CATALOG, key=lambda x: (-int(x.our_level), x.name)):
        flag = "" if p.validated else ("  [UNVALIDATED]" if p.our_detector else "")
        lines.append(f"    {p.name:<28} {p.our_level.label:<10}{flag}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual inspection
    print(gap_report())
