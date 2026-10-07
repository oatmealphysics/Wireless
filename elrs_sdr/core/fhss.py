"""
ELRS FHSS (frequency-hopping) domain + sequence model.

Ports of:
  src/lib/FHSS/random.cpp   -> lcg / rng / rng_n
  src/lib/FHSS/FHSS.cpp     -> domains table, build_sequence, frequency math

The hop *sequence* is a pseudo-random permutation generated from a 32-bit seed
via a linear congruential generator:
    seed = (214013 * seed + 2531011) % 2147483648      (31-bit modulus)
    rng  = seed >> 16                                  (0..0x7FFF, per random.cpp)

The seed for a bound link is derived from the UID (see ota.uid_seed).

Regulatory domains are a flat table; for the 2.4 GHz band (SX1280 / LR1121 /
LR2021) every domain is the same 80-channel 2400.4-2479.4 MHz map, so for a
B205mini/B210 the relevant table is ISM2G4 / CE_LBT.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FHSS_SEQUENCE_LEN = 256

# ----------------------------------------------------------------------------
# LCG (src/lib/FHSS/random.cpp)
# ----------------------------------------------------------------------------
_LCG_M = 2147483648          # 2^31
_LCG_A = 214013
_LCG_C = 2531011


class Lcg:
    """Deterministic 31-bit LCG identical to ELRS's `seed`/`rng`."""

    __slots__ = ("seed",)

    def __init__(self, seed: int = 0):
        self.seed = seed & 0x7FFFFFFF

    def rng(self) -> int:
        # ELRS src/lib/FHSS/random.cpp rng(): seed=(a*seed+c)%2^31; return seed>>16
        # (yields 0..0x7FFF). This MUST be >>16 to match the firmware hop sequence;
        # an earlier >>15 here was non-conformant and produced the wrong permutation
        # (cross-checked against random.cpp and the GNU_Radio_ExpressLRS reference,
        # both of which use >>16).
        self.seed = (_LCG_A * self.seed + _LCG_C) % _LCG_M
        return self.seed >> 16

    def rng_n(self, upper: int) -> int:
        # 0 <= x < upper, upper < 256
        return self.rng() % upper


# ----------------------------------------------------------------------------
# Domain table (src/lib/FHSS/FHSS.cpp, FREQ_HZ_TO_REG_VAL already expanded)
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class FhssDomain:
    name: str
    freq_start: int     # Hz
    freq_stop: int      # Hz
    freq_count: int     # number of channels
    freq_center: int    # Hz (informational)

    @property
    def freq_spread(self) -> float:
        # (stop - start) / (count - 1)
        if self.freq_count <= 1:
            return 0.0
        return (self.freq_stop - self.freq_start) / (self.freq_count - 1)

    def channel_freq(self, index: int) -> int:
        """Hz for channel index (0-based). ELRS uses integer register math;
        for SDR capture we work in Hz directly."""
        return int(round(self.freq_start + self.freq_spread * index))

    @property
    def sync_channel(self) -> int:
        return self.freq_count // 2


DOMAINS: dict[str, FhssDomain] = {
    "AU915":  FhssDomain("AU915",  915_500_000, 926_900_000, 20, 921_000_000),
    "FCC915": FhssDomain("FCC915", 903_500_000, 926_900_000, 40, 915_000_000),
    "EU868":  FhssDomain("EU868",  863_275_000, 869_575_000, 13, 868_000_000),
    "IN866":  FhssDomain("IN866",  865_375_000, 866_950_000,  4, 866_000_000),
    "AU433":  FhssDomain("AU433",  433_420_000, 434_420_000,  3, 434_000_000),
    "EU433":  FhssDomain("EU433",  433_100_000, 434_450_000,  3, 434_000_000),
    "US433":  FhssDomain("US433",  433_250_000, 438_000_000,  8, 434_000_000),
    "US433W": FhssDomain("US433W", 423_500_000, 438_000_000, 20, 434_000_000),
    "TH920":  FhssDomain("TH920",  920_500_000, 924_700_000,  8, 922_600_000),
    # 2.4 GHz band — identical map for all radios that support it
    "ISM2G4": FhssDomain("ISM2G4", 2400_400_000, 2479_400_000, 80, 2440_000_000),
    "CE_LBT": FhssDomain("CE_LBT", 2400_400_000, 2479_400_000, 80, 2440_000_000),
}

# Default domain for a B205mini/B210 scanning 2.4 GHz ELRS
DEFAULT_2G4_DOMAIN = "ISM2G4"


# ----------------------------------------------------------------------------
# Sequence builder (src/lib/FHSS/FHSS.cpp::FHSSrandomiseFHSSsequenceBuild)
# ----------------------------------------------------------------------------
def build_sequence(seed: int, freq_count: int, sync_channel: int,
                   out_len: int = FHSS_SEQUENCE_LEN) -> list[int]:
    """
    Build the hop sequence: a permutation of the freq_count channels, repeated
    in blocks, where within each block (except the entry that lands on the
    sync channel at block-start) one random swap is performed.

    Mirrors FHSSrandomiseFHSSsequenceBuild exactly:
      1. sequenceCount = round up out_len to a whole multiple of freqCount
      2. base[i] = i % freqCount   (identity mapping)
      3. for each i not at a block boundary (i % freqCount != 0):
           offset = (i // freqCount) * freqCount
           rand   = rng_n(freqCount - 1) + 1     # 1..freqCount-1
           swap seq[i] <-> seq[offset + rand]

    Returns a list of length sequenceCount (>= out_len).
    """
    # Round up to whole multiple of freqCount
    sequence_count = ((out_len + freq_count - 1) // freq_count) * freq_count

    seq = []
    for i in range(sequence_count):
        if i % freq_count == 0:
            seq.append(sync_channel)
        elif i % freq_count == sync_channel:
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


def frequency_at(domain: FhssDomain, sequence: list[int], ptr: int) -> int:
    """Center frequency (Hz) for hop pointer `ptr` into `sequence`."""
    return domain.channel_freq(sequence[ptr % len(sequence)])


# ----------------------------------------------------------------------------
# Seed derivation — the *same* seed drives both the FHSS sequence and (via
# OtaGetUidSeed) the CRC initializer on the bound link.
# ----------------------------------------------------------------------------
def uid_seed(uid: bytes) -> int:
    """Port of OtaGetUidSeed: (UID[2]<<24)|(UID[3]<<16)|(UID[4]<<8)|(UID[5]^OTA_VERSION_ID).
    NOTE: ELRS uses this 32-bit value as the *seed* for the FHSS LCG and the
    link's random streams. OTA_VERSION_ID must match the link's firmware.
    """
    from .ota import OTA_VERSION_ID
    return ((uid[2] << 24) + (uid[3] << 16) +
            (uid[4] << 8) + (uid[5] ^ OTA_VERSION_ID))
