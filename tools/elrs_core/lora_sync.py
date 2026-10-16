"""
lora_sync.py — sub-chip timing alignment for the LoRa payload demod.

THE DEFECT THIS FIXES
---------------------
`inject_timing.py` proved, on a noise-free synthetic frame with a PERFECT
integer symbol boundary, that the receive path loses the payload as soon as the
sub-chip timing offset exceeds ~0.35 chips:

    delay 0.00-0.31 chips -> 100% symbol accuracy, CRC PASS
    delay 0.375          ->  70%
    delay 0.50           ->  35%
    delay >=0.5625       ->   0%          (only 6 of 16 offsets decoded)

That matches the SNR-INDEPENDENT ~5/12 plateau in `inject_sweep.py` (unchanged
from 8 dB to 45 dB), so it is a deterministic chain defect, not noise. Cause:
`find_bursts` quantises each burst start to its 0.4 ms FFT hop, and
`aligned_payload_sets` then recovers only an INTEGER symbol boundary plus an
INTEGER bin de-rotation. Nothing ever removed the sub-chip remainder.

WHAT WAS TRIED, AND WHAT THE MEASUREMENTS SAID
----------------------------------------------
1. SUB-BIN FREQUENCY CORRECTION (refuted). The first hypothesis was that the
   sub-chip delay shows up purely as a fractional-bin frequency offset, since a
   chirp trades time for frequency. Estimating the preamble's fractional bin and
   de-rotating by it made things WORSE -- symbol-exact offsets went 8/16 -> 6/16.
   The measured fractional offsets were small (<=0.25 bins) while accuracy fell
   to 0%, which a 0.25-bin shift cannot explain. Hypothesis dropped.

2. TWO-SIDED STO/CFO SEPARATION (unreliable here). Using the opposite signs of
   an up-chirp preamble and a down-chirp SFD, `estimate_sto_cfo()` recovers CFO
   accurately but its timing term wraps above ~0.5 chip, and the empirical
   preamble-bin-vs-delay slope did not match the textbook derivation. Kept
   below as a diagnostic only; the payload path does not depend on it.

3. FRACTIONAL TIME CORRECTION (confirmed). A direct grid search over both axes
   showed a time correction restores 100% symbol accuracy at EVERY injected
   delay, while the best frequency correction stalls at 67% for tau = 0.5:

       tau     best time corr        best freq corr
       0.2500  100% at d=+0.1875     100% at b=+0.2500
       0.5000  100% at d=-0.0625      67% at b=+0.4375
       0.6250  100% at d=-0.1875     100% at b=-0.8125
       0.7500  100% at d=-0.3125     100% at b=-0.6875
       0.9375  100% at d=-0.5000     100% at b=-0.5625

So the correction axis is TIME. This module therefore does not try to *estimate*
the offset from a model it cannot derive; it hands the caller a small grid of
candidate sub-chip delays and lets the ELRS CRC pick the winner -- the same
pattern the path already uses for the 3 SFD candidates and 2 IQ polarities.
`inject_timing.py` measured the capture region as ~0.6 chips wide, so a grid at
0.25-chip spacing always lands within 0.125 chips of optimum, comfortably
inside it. `selftest()` below validates exactly that claim against ground truth.
"""
from __future__ import annotations

import numpy as np

from .lora import _base_chirp

ZP = 16          # zero-padding factor for the diagnostic bin interpolation

#: Candidate sub-chip delays. 0.25 spacing => worst-case 0.125 chips from
#: optimum, against a measured ~0.6-chip-wide capture region.
FRAC_DELAYS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75)


def frac_delay(x: np.ndarray, d: float) -> np.ndarray:
    """Delay `x` by `d` samples via an FFT phase ramp (circular).

    Circular wrap is harmless when correcting a long captured window, which is
    how the receive path uses it. It is NOT suitable for injecting a delay into
    a short isolated frame -- the chirp discontinuity then smears the spectrum.
    `selftest()` injects delays into a zero-padded frame for that reason.
    """
    if d == 0.0:
        return x
    f = np.fft.fftfreq(len(x))
    return np.fft.ifft(np.fft.fft(x) * np.exp(-2j * np.pi * f * d)
                       ).astype(np.complex64)


def _frac_peak_bin(seg: np.ndarray, n: int, zp: int = ZP) -> float:
    """Interpolated peak bin of `seg`, as a SIGNED offset in [-n/2, n/2) bins."""
    spec = np.abs(np.fft.fft(seg, n * zp))
    k = int(np.argmax(spec))
    b = k / zp
    return b - n if b > n / 2 else b


def estimate_fine_offset(rs: np.ndarray, sf: int, pre_start: int, pre_end: int,
                         max_symbols: int = 4) -> float:
    """Residual SUB-BIN frequency offset of the preamble tone, in bins.

    This is a SEPARATE impairment from the sub-chip timing that `FRAC_DELAYS`
    covers, and it has a different cause: `aligned_scan.find_bursts` estimates
    each burst's centre frequency as the energy-weighted centroid of a 2048-bin
    FFT, whose bins are ~4.9 kHz wide at 10 MS/s. The leftover fraction of a
    LoRa bin shifts every payload symbol by the same amount, producing the +-1
    bin symbol errors seen in `inject_test.py` stage 4 (34 -> 35), and with the
    Hamming parity left zero there is no error correction to absorb them. It is
    directly visible in the probe of the synthetic capture: the locked preamble
    bin drifts 89 -> 79 across frames that are byte-identical by construction.

    The caller's `(v - pre) % 2^sf` de-rotation removes the INTEGER part, so
    only the fractional remainder is returned, in (-0.5, +0.5].

    *** REFUTED AS A CORRECTION -- DIAGNOSTIC USE ONLY. ***

    Applying this was measured twice and was worse both times:
      * in isolation on synthetic frames, symbol-exact offsets 8/16 -> 6/16;
      * in the full `aligned_scan` pipeline, frames recovered 6/12 -> 2/12 and
        raw hits 8 -> 3, across the whole 8-45 dB sweep.
    The reason is that a sub-chip DELAY on a chirp also appears as a fractional
    bin shift, so this quantity is a mixture of the two impairments rather than
    the frequency one alone. Removing it as if it were pure frequency destroys
    the timing relationship that the `FRAC_DELAYS` grid relies on. The two are
    entangled, not complementary. Report it, do not correct with it.
    """
    n = 1 << sf
    a = pre_start * n
    npre = min(pre_end - pre_start + 1, max_symbols)
    if a < 0 or npre < 1 or a + npre * n > len(rs):
        return 0.0
    up = _base_chirp(sf)
    bins = [_frac_peak_bin(rs[a + i * n: a + (i + 1) * n] * np.conj(up), n)
            for i in range(npre)]
    b = float(np.median(bins))
    return float(b - np.round(b))


def apply_fine_offset(rs: np.ndarray, sf: int, frac_bins: float) -> np.ndarray:
    """De-rotate `rs` by `frac_bins` so the dechirped tones land on integer bins."""
    if not frac_bins:
        return rs
    n = 1 << sf
    k = np.arange(len(rs))
    return (rs * np.exp(-2j * np.pi * frac_bins * k / n)).astype(np.complex64)


def estimate_sto_cfo(rs: np.ndarray, sf: int, pre_start: int, pre_end: int,
                     sync_symbols: int = 2) -> tuple[float, float]:
    """DIAGNOSTIC ONLY -- see note 2 in the module docstring.

    Separates timing from carrier via the opposite signs an up-chirp preamble
    and a down-chirp SFD impose:
        sto = bin_up - bin_down        cfo_bins = (bin_up + bin_down) / 2
    The CFO term is accurate; the timing term wraps above ~0.5 chip. The payload
    path uses the `FRAC_DELAYS` grid instead.
    """
    n = 1 << sf
    up = _base_chirp(sf)
    a = pre_start * n
    npre = min(pre_end - pre_start + 1, 4)
    if a < 0 or npre < 1 or a + npre * n > len(rs):
        return 0.0, 0.0
    bin_up = float(np.median(
        [_frac_peak_bin(rs[a + i * n: a + (i + 1) * n] * np.conj(up), n)
         for i in range(npre)]))
    b = (pre_end + 1 + sync_symbols) * n
    if b + 2 * n > len(rs):
        return 0.0, 0.0
    bin_dn = float(np.median(
        [_frac_peak_bin(rs[b + i * n: b + (i + 1) * n] * up, n)
         for i in range(2)]))
    return bin_up - bin_dn, (bin_up + bin_dn) / 2.0


# --------------------------------------------------------------------------- #
# selftest — validate the FRAC_DELAYS grid against known injected offsets
# --------------------------------------------------------------------------- #
def _build_frame(sf: int, payload_syms, pad: int = 0) -> np.ndarray:
    """inject_test.py's frame layout: 12 preamble + 2 sync + 2.25 SFD + payload."""
    n = 1 << sf
    up = _base_chirp(sf)
    down = np.conj(up)
    k = np.arange(n)
    parts = [up] * 12
    parts += [up * np.exp(2j * np.pi * s * k / n) for s in (8, 16)]
    parts += [down, down, down[: n // 4]]
    parts += [up * np.exp(2j * np.pi * (s % n) * k / n) for s in payload_syms]
    x = np.concatenate(parts)
    if pad:
        z = np.zeros(pad, dtype=complex)
        x = np.concatenate([z, x, z])
    return x


def _inject_delay(x: np.ndarray, tau: float, pad: int) -> np.ndarray:
    """Band-limited sub-sample delay of a zero-padded frame, then unpad, so the
    circular wrap lands in the padding instead of in the signal."""
    f = np.fft.fftfreq(len(x))
    y = np.fft.ifft(np.fft.fft(x) * np.exp(-2j * np.pi * f * tau))
    return y[pad:len(x) - pad] if pad else y


def selftest(sf: int = 7, verbose: bool = True) -> None:
    """Sweep a known sub-chip delay; SOME grid delay must recover every symbol."""
    n = 1 << sf
    want = [5, 42, 99, 7, 120, 63]
    pad = 8 * n
    start = int(round((12 + 2 + 2.25) * n))
    up = _base_chirp(sf)

    def demod(x, at, count):
        out = []
        for i in range(count):
            seg = x[at + i * n: at + (i + 1) * n]
            if len(seg) < n:
                break
            out.append(int(np.argmax(np.abs(np.fft.fft(seg * np.conj(up))))))
        return out

    def acc(got):
        return sum(1 for a, b in zip(got, want) if a == b) / len(want)

    uncorrected = recovered = 0
    for tau in np.arange(0.0, 1.0, 0.0625):
        x = _inject_delay(_build_frame(sf, want, pad), float(tau), pad)
        a0 = acc(demod(x, start, len(want)))
        best = max((acc(demod(frac_delay(x, -d), start, len(want))), d)
                   for d in FRAC_DELAYS)
        uncorrected += a0 == 1.0
        recovered += best[0] == 1.0
        if verbose:
            print(f"  tau={tau:+.4f}  no-correction {a0*100:>3.0f}%  "
                  f"grid-best {best[0]*100:>3.0f}% (d={best[1]:.2f})"
                  f"{'' if best[0] == 1.0 else '   <-- MISSED'}")
    if verbose:
        print(f"\n  symbol-exact without correction: {uncorrected}/16 offsets")
        print(f"  symbol-exact with the delay grid: {recovered}/16 offsets")
    assert recovered == 16, (
        f"the {len(FRAC_DELAYS)}-point grid missed {16 - recovered}/16 offsets; "
        f"tighten FRAC_DELAYS")
    if verbose:
        print(f"  [PASS] the {len(FRAC_DELAYS)}-point delay grid recovers every "
              f"symbol at every sub-chip offset "
              f"({uncorrected}/16 -> {recovered}/16)")


if __name__ == "__main__":
    selftest()
