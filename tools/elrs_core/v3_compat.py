"""Betaflight 4.4.2 (ELRS OTA v3) compatibility layer.

The vendored crc.py / ota.py / fhss.py implement ELRS v4 semantics (from
ExpressLRS 4.1.0 standalone). Betaflight 4.4.2 ships a v3 variant whose CRC
initializer, nonce handling, FHSS seed, SYNC structure, and bind-mode CRC
all differ. This module provides v3-correct functions that reuse the v4
CRC engine where possible.

Source: Betaflight 4.4.2
  src/main/rx/expresslrs_common.{h,c}
  src/main/rx/expresslrs.c

Every function here is cross-referenced against the Betaflight source analysis
in betaflight-442-source-analysis.md and the standalone v3 tools
(elrs-v3-frame-check.py, elrs-v3-fhss-check.py). See PROVENANCE.md for
vendoring rules: the v4 files are not modified.
"""

from __future__ import annotations

from dataclasses import dataclass

from .crc import ELRS_CRC14_POLY
from .fhss import Lcg, FHSS_SEQUENCE_LEN, FhssDomain, DOMAINS

V3_OTA_VERSION_ID = 3
V3_SYNC_CHANNEL_80 = 41  # (freq_count / 2) + 1, measured on the live link


# ---------------------------------------------------------------------------
# CRC14 — Betaflight 4.4.2 calcCrc14 (table-less bitwise equivalent)
# ---------------------------------------------------------------------------
def v3_crc14(data: bytes, initial: int) -> int:
    """Bitwise CRC14 matching BF 4.4.2 calcCrc14's 256-entry table."""
    crc = initial & 0x3FFF
    for value in data:
        crc ^= value << 6
        for _ in range(8):
            crc = ((crc << 1) ^ (ELRS_CRC14_POLY if crc & 0x2000 else 0)) & 0x3FFF
    return crc


# ---------------------------------------------------------------------------
# CRC initializer — v3 vs v4
# ---------------------------------------------------------------------------
def v3_uid_crc_init(uid: bytes) -> int:
    """BF 4.4.2: (UID[4]<<8 | UID[5]) ^ ELRS_OTA_VERSION_ID.

    v4 formula is (UID[4]<<8 | UID[5]) ^ (OTA_VERSION_ID << 8) — the shift
    makes them fundamentally different.
    """
    if len(uid) != 6:
        raise ValueError("UID must be 6 bytes")
    return (((uid[4] << 8) | uid[5]) ^ V3_OTA_VERSION_ID) & 0xFFFF


def v3_bind_crc_init() -> int:
    """BF 4.4.2 bind mode: crcInitializer = 0 (explicitly set, not computed)."""
    return 0


# ---------------------------------------------------------------------------
# CRC validation/generation — v3 nonce handling
# ---------------------------------------------------------------------------
def v3_validate_crc(raw: bytes, uid: bytes, *,
                    switch_mode: str = "hybrid",
                    nonce: int | None = None,
                    hop_interval: int = 4) -> bool:
    """Validate an 8-byte v3 standard frame's CRC14.

    In v3, nonce handling differs from v4:
      - hybrid/non-RC: CRC computed with crcHigh bits zeroed, no nonce XOR
      - wide + RC_DATA: crcHigh is set to (nonce % hopInterval) + 1 before CRC
    v4 instead XORs the nonce into the CRC init for all non-SYNC packets.
    """
    if len(raw) != 8:
        raise ValueError("v3 standard frame must be 8 bytes")

    kind = raw[0] & 0x03
    initial = v3_uid_crc_init(uid)

    # Extract stored CRC: 6 bits from byte 0 (crcHigh) + 8 bits from byte 7
    stored_crc = (((raw[0] >> 2) & 0x3F) << 8) | raw[7]

    # Build the 7 bytes for CRC calculation
    if kind == 0 and switch_mode == "wide":
        # RC_DATA + SM_WIDE: crcHigh = (nonce % hopInterval) + 1
        if nonce is None:
            return False  # can't verify without nonce
        crc_high_byte = ((nonce % hop_interval) + 1) << 2
    else:
        # All other types: crcHigh bits zeroed
        crc_high_byte = kind  # type bits only

    calc_data = bytes([crc_high_byte]) + raw[1:7]
    calc = v3_crc14(calc_data, initial)
    return calc == stored_crc


def v3_generate_crc(raw: bytearray, uid: bytes, *,
                    switch_mode: str = "hybrid",
                    nonce: int | None = None,
                    hop_interval: int = 4) -> None:
    """Write CRC14 into an 8-byte v3 frame in place."""
    if len(raw) != 8:
        raise ValueError("v3 standard frame must be 8 bytes")

    kind = raw[0] & 0x03
    initial = v3_uid_crc_init(uid)

    if kind == 0 and switch_mode == "wide":
        if nonce is None:
            raise ValueError("nonce required for wide RC_DATA")
        crc_high_byte = ((nonce % hop_interval) + 1) << 2
    else:
        crc_high_byte = kind

    calc_data = bytes([crc_high_byte]) + raw[1:7]
    crc = v3_crc14(calc_data, initial)

    # Write crcHigh into byte 0 bits [7:2], preserving type bits [1:0]
    raw[0] = (raw[0] & 0x03) | ((crc >> 8) << 2)
    raw[7] = crc & 0xFF


# ---------------------------------------------------------------------------
# FHSS seed — v3 vs v4
# ---------------------------------------------------------------------------
def v3_uid_seed(uid: bytes) -> int:
    """BF 4.4.2: ((UID[2]<<24)+(UID[3]<<16)+(UID[4]<<8)+UID[5]) ^ OTA_VERSION_ID.

    v4 fhss.py does (UID[5] ^ OTA_VERSION_ID) before adding. Numerically
    equivalent (XOR in the low byte, no carry interaction), but uses
    OTA_VERSION_ID=4 whereas v3 uses 3.
    """
    if len(uid) != 6:
        raise ValueError("UID must be 6 bytes")
    return (((uid[2] << 24) + (uid[3] << 16) + (uid[4] << 8) + uid[5])
            ^ V3_OTA_VERSION_ID)


def v3_sync_channel(freq_count: int) -> int:
    """BF 4.4.2: sync_channel = (freq_count / 2) + 1.

    v4 (ELRS master) uses freq_count / 2. The +1 is confirmed by measured RF.
    """
    return (freq_count // 2) + 1


def v3_build_sequence(uid: bytes, freq_count: int = 80,
                      out_len: int = FHSS_SEQUENCE_LEN) -> list[int]:
    """Build the v3 FHSS hop sequence.

    Identical algorithm to fhss.build_sequence but uses:
      - v3 seed (XOR with 3 instead of 4)
      - v3 sync channel ((freq_count/2)+1 instead of freq_count/2)
      - rounds DOWN to fit FHSS_SEQUENCE_LEN (same as current fhss.py)
    """
    seed = v3_uid_seed(uid)
    sync_ch = v3_sync_channel(freq_count)
    sequence_count = (out_len // freq_count) * freq_count

    seq: list[int] = []
    for i in range(sequence_count):
        if i % freq_count == 0:
            seq.append(sync_ch)
        elif i % freq_count == sync_ch:
            seq.append(0)
        else:
            seq.append(i % freq_count)

    lcg = Lcg(seed)
    for i in range(sequence_count):
        if i % freq_count != 0:
            offset = (i // freq_count) * freq_count
            rand = lcg.rng_n(freq_count - 1) + 1
            seq[i], seq[offset + rand] = seq[offset + rand], seq[i]

    return seq


# ---------------------------------------------------------------------------
# SYNC packet decode — v3 structure
# ---------------------------------------------------------------------------
@dataclass
class V3Sync:
    """Decoded v3 SYNC packet payload (bytes 1-6 of the 8-byte frame).

    v3 layout: [fhss_index, nonce, flags, UID3, UID4, UID5]
    where flags = [switchEncMode:1][newTlmRatio:3][rateIndex:4]

    v4 layout: [fhss_index, nonce, rfRateEnum, flags, UID4, UID5]
    where flags = [switchEncMode:1][newTlmRatio:3][geminiMode:1][otaProtocol:2][free:1]

    v3 carries UID3 (24-bit UID fragment); v4 carries only UID4+UID5 (16-bit).
    """
    fhss_index: int
    nonce: int
    switch_enc_mode: int
    new_tlm_ratio: int
    rate_index: int
    uid3: int
    uid4: int
    uid5: int

    @property
    def uid_18bit(self) -> int:
        """The 18-bit UID check used by processRFSyncPacket:
        UID[3] (8 bits) + UID[4] (8 bits) + UID[5] upper 2 bits."""
        return (self.uid3 << 10) | (self.uid4 << 2) | (self.uid5 >> 6)

    def uid_matches(self, uid: bytes) -> bool:
        """Check the 18-bit UID match that processRFSyncPacket performs."""
        return (self.uid3 == uid[3] and
                self.uid4 == uid[4] and
                (self.uid5 & 0xC0) == (uid[5] & 0xC0))


def v3_decode_sync(raw: bytes) -> V3Sync:
    """Decode a v3 SYNC packet (8 bytes). Raises on wrong type or length."""
    if len(raw) != 8:
        raise ValueError("v3 frame must be 8 bytes")
    if (raw[0] & 0x03) != 0x02:
        raise ValueError("not a SYNC packet (type bits != 0x02)")

    flags = raw[3]
    return V3Sync(
        fhss_index=raw[1],
        nonce=raw[2],
        switch_enc_mode=flags & 0x01,
        new_tlm_ratio=(flags >> 1) & 0x07,
        rate_index=(flags >> 4) & 0x0F,
        uid3=raw[4],
        uid4=raw[5],
        uid5=raw[6],
    )


def v3_build_sync(uid: bytes, fhss_index: int, nonce: int,
                  rate_index: int = 0, switch_enc_mode: int = 0,
                  new_tlm_ratio: int = 0) -> bytes:
    """Build a v3 SYNC frame with valid CRC14."""
    flags = (switch_enc_mode & 0x01) | ((new_tlm_ratio & 0x07) << 1) | ((rate_index & 0x0F) << 4)
    raw = bytearray([
        0x02,  # type = SYNC
        fhss_index & 0xFF,
        nonce & 0xFF,
        flags,
        uid[3],
        uid[4],
        uid[5],
        0x00,  # crcLow placeholder
    ])
    v3_generate_crc(raw, uid)
    return bytes(raw)
