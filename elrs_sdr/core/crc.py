"""
Exact Python port of the ELRS CRC engine (src/lib/CRC/crc.{h,cpp}).

ELRS uses a family of *non-reflected* (MSB-first) CRCs where the polynomial
is given in implicit-leading-1 form:
    ELRS_CRC14_POLY = 0x2E57  (14-bit, used by the 8-byte "std" OTA packet)
    ELRS_CRC16_POLY = 0x3D65  (16-bit, used by the 13-byte "full" OTA packet)

The table is built over 256 entries with the high bit set at (bits-1), and the
CRC accumulator is a 16-bit register. calc() returns the value masked to
`bits` bits. See crc.cpp::Crc2Byte::init / ::calc for the reference.

CRC8 (poly 0xD5) is the standard CRSF/Crossfire framing CRC used on the UART,
ported from GENERIC_CRC8.
"""

from __future__ import annotations


class Crc2Byte:
    """Direct port of Crc2Byte (bits in {8..16}, MSB-first, non-reflected)."""

    def __init__(self, bits: int = 16, poly: int = 0x0000):
        self._bits = 0
        self._poly = 0
        self._bitmask = 0
        self._tab: list[int] = [0] * 256
        self.init(bits, poly)

    def init(self, bits: int, poly: int) -> None:
        # crc.cpp short-circuits when unchanged; mirror it for call-site symmetry
        if bits == self._bits and poly == self._poly:
            return
        self._poly = poly
        self._bits = bits
        self._bitmask = (1 << bits) - 1
        highbit = 1 << (bits - 1)
        for i in range(256):
            crc = i << (bits - 8)
            for _ in range(8):
                crc = ((crc << 1) ^ (poly if (crc & highbit) else 0))
            self._tab[i] = crc & 0xFFFF

    def calc(self, data: bytes, crc: int = 0) -> int:
        """Port of Crc2Byte::calc. data may be any bytes-like of len>=1..255."""
        for b in data:
            crc = (crc << 8) ^ self._tab[((crc >> (self._bits - 8)) ^ b) & 0xFF]
        return crc & self._bitmask


class Crc8:
    """Direct port of GENERIC_CRC8 (MSB-first, non-reflected, init 0)."""

    def __init__(self, poly: int = 0xD5):
        self._poly = poly
        self._tab: list[int] = [0] * 256
        for i in range(256):
            crc = i
            for _ in range(8):
                crc = (crc << 1) ^ (poly if (crc & 0x80) else 0)
            self._tab[i] = crc & 0xFF

    def calc(self, data: bytes, crc: int = 0) -> int:
        for b in data:
            crc = self._tab[crc ^ b]
        return crc


# Module-level instances matching ELRS globals
OTA_CRC = Crc2Byte()   # re-init'd per packet-size in ota.py
CRSF_CRC = Crc8(0xD5)

# Polynomials (src/lib/OTA/OTA.h)
ELRS_CRC14_POLY = 0x2E57  # 14-bit, implicit leading 1 (Koopman 0x372b)
ELRS_CRC16_POLY = 0x3D65  # 16-bit, implicit leading 1 (Koopman 0x9eb2)
