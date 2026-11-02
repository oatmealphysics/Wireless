"""
bursts.py — narrowband burst finding over a memmapped .complex8 capture.

Moved here from the root-level `aligned_scan.py` so it is reachable as package
code: AGENTS.md §5 makes `elrs_sdr/core/` the single source of truth, and the
WaveRaider delivery surface needs this path to recover the FHSS hop fingerprint
without importing a top-level script. `aligned_scan.py` now re-exports these
names, so every existing caller is unchanged.

The two non-obvious behaviours here were both found by measurement and are
documented at their definitions: the SNR gate must be derived from the capture
(a fixed dB gate is sample-rate dependent and dropped 570 of 573 real bursts),
and a guard band around DC must be blanked (the B205's LO spike is the global
FFT peak in ~65% of frames).
"""
from __future__ import annotations

import numpy as np

from ..radio import read_capture_meta


DC_GUARD_HZ = 100_000               # blank +-100 kHz around DC (B205 LO spike)


def open_capture(path):
    """Memmap the int16 I/Q file. A 30 s @ 10 MS/s capture is 1.2 GB on disk and
    ~2.4 GB as complex64, so we never materialise the whole thing — bursts are
    found chunk-by-chunk and only short windows around each burst are converted."""
    m = read_capture_meta(path)
    sr = float(m.get("sample_rate_hz", 5e6))
    fc = float(m.get("center_freq_hz", 2.4054e9))
    mm = np.memmap(path, dtype=np.int16, mode="r")
    return mm, sr, fc, len(mm) // 2


def grab(mm, a, n):
    """Complex64 slice [a, a+n) of the memmapped int16 I/Q capture."""
    a = max(0, int(a)); n = int(n)
    seg = np.asarray(mm[2 * a: 2 * (a + n)], dtype=np.float32)
    if len(seg) < 2:
        return np.zeros(0, dtype=np.complex64)
    seg = seg[: (len(seg) // 2) * 2]
    return ((seg[0::2] + 1j * seg[1::2]) / 32767.0).astype(np.complex64)


def _bw10(S, pk, sr, n):
    thr = S[pk] / 10.0
    i = pk
    while i > 0 and S[i] > thr:
        i -= 1
    j = pk
    while j < len(S) - 1 and S[j] > thr:
        j += 1
    return (j - i) * sr / n, i, j


def auto_snr_threshold(mm, sr, n_complex, sample_s=2.0, pct=88.0, floor_db=25.0):
    """Pick the burst SNR gate from the capture itself.

    A FIXED dB gate is rate-dependent and silently drops bursts: with nfft fixed,
    doubling the sample rate doubles the FFT bin width, so the same burst scores
    several dB lower. A 45 dB gate tuned on the 5 MS/s captures (bursts 48-58 dB)
    kept only 3 of 573 real bursts at 10 MS/s, where the same link lands at
    ~40-45 dB — an ~85% loss that made the SYNC-packet yield look hopeless.
    So derive the gate from the frame-SNR distribution instead: bursts are the
    upper tail, the floor is the noise bulk (median ~14 dB in both captures).
    """
    nfft, step = 2048, int(sr * 0.4e-3)
    iq = grab(mm, 0, min(int(sr * sample_s), n_complex))
    mid, guard = nfft // 2, max(1, int(DC_GUARD_HZ / sr * nfft))
    snrs = []
    for k in range(max(0, (len(iq) - nfft) // step)):
        seg = iq[k * step:k * step + nfft]
        S = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(nfft)))) ** 2
        med = np.median(S) + 1e-30
        S[mid - guard: mid + guard + 1] = 0.0          # same DC guard as find_bursts
        snrs.append(10 * np.log10(S.max() / med))
    if not snrs:
        return floor_db
    return float(max(floor_db, np.percentile(snrs, pct)))


def find_bursts(mm, sr, n_complex, snr_min=45.0):
    """Narrowband (ELRS-width) bursts with their energy-weighted centre frequency.
    Streams the memmap in ~2 s chunks so a 30 s capture never loads at once."""
    nfft, step = 2048, int(sr * 0.4e-3)
    out, last = [], -1
    chunk = int(sr * 2.0)
    # Blank a guard band around DC before the peak search. Measured on both 30 s
    # captures: the B205's LO/DC spike is the GLOBAL FFT peak in ~65% of frames
    # (4697-4881 of ~7500), so a bare argmax reports DC rather than the real
    # emitter, and its noise skirts can even sneak past the 0.3-2 MHz bandwidth
    # gate as a fake "ELRS-width" burst. The operator's own controller-off
    # spectrum flagged the same thing (sole peak at +0.00 MHz offset).
    mid, guard = nfft // 2, max(1, int(DC_GUARD_HZ / sr * nfft))
    for base in range(0, n_complex, chunk):
        iq = grab(mm, base, min(chunk + nfft, n_complex - base))
        for k in range(max(0, (len(iq) - nfft) // step)):
            seg = iq[k * step:k * step + nfft]
            S = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(nfft)))) ** 2
            med = np.median(S) + 1e-30
            S[mid - guard: mid + guard + 1] = 0.0      # exclude DC/LO spike
            pk = int(np.argmax(S))
            if 10 * np.log10(S[pk] / med) < snr_min:
                continue
            bw, i0, j0 = _bw10(S, pk, sr, nfft)
            if not (300e3 < bw < 2.0e6):       # reject CW spurs and wideband Wi-Fi
                continue
            a = base + k * step
            if a - last < int(sr * 0.003):
                continue
            fax = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / sr))
            sl = slice(i0, j0 + 1)
            out.append((a, float(np.sum(fax[sl] * S[sl]) / np.sum(S[sl]))))
            last = a
    return out
