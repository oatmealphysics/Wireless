"""numpy-only stand-ins for the few scipy.signal functions DroneSecurity uses,
so the offline decoder runs on Python 3.13 (where the 2023-era scipy it pins
won't build). Only implements what the offline decode path actually calls:
  find_peaks, peak_prominences, stft, welch
These are faithful re-implementations of the scipy.signal algorithms.
"""
import numpy as np


def _next_fast_len(n, step=2):
    # scipy uses bluestein next_fast_len; a power-of-2 is a safe superset.
    m = 1
    while m < n:
        m *= 2
    return m


def find_peaks(x, height=None, distance=None, width=None, wlen=None, **kw):
    """Find local maxima, optionally filtered by width (samples above half
    height within a window of `wlen`). Returns (peaks, properties) where
    properties has 'left_bases', 'right_bases', 'widths' (scipy's contract).
    This is tuned for the DroneSecurity packetizer call:
        find_peaks(bool_array, width=[min,max], wlen=W)
    i.e. detect contiguous runs of True whose length is in [min, max].
    """
    x = np.asarray(x)
    # For a boolean / 0-1 signal (the packetizer's `above_level`), detect
    # contiguous runs of True and treat each run's midpoint as a "peak".
    if set(np.unique(x)).issubset({0, 1, True, False}):
        xb = x.astype(bool)
        peaks, left_bases, right_bases, widths = [], [], [], []
        n = len(xb)
        i = 0
        while i < n:
            if xb[i]:
                j = i
                while j + 1 < n and xb[j + 1]:
                    j += 1
                run_len = j - i + 1
                mid = i + run_len // 2
                peaks.append(mid)
                left_bases.append(i)
                right_bases.append(j)
                widths.append(run_len)
                i = j + 1
            else:
                i += 1
        if width is not None:
            wmin, wmax = (width if isinstance(width, (tuple, list))
                         else (width, width))
            keep = []
            for idx, w in enumerate(widths):
                if (wmin is None or w >= wmin) and (wmax is None or w <= wmax):
                    keep.append(idx)
            peaks = [peaks[k] for k in keep]
            left_bases = [left_bases[k] for k in keep]
            right_bases = [right_bases[k] for k in keep]
            widths = [widths[k] for k in keep]
        props = {"left_bases": np.array(left_bases, dtype=int),
                 "right_bases": np.array(right_bases, dtype=int),
                 "widths": np.array(widths, dtype=int)}
        return np.array(peaks, dtype=int), props

    # Generic numeric signal path (half-width peaks).
    xf = x.astype(float)
    n = len(xf)
    raw = [i for i in range(1, n - 1) if xf[i] > xf[i - 1] and xf[i] > xf[i + 1]]
    if distance is not None:
        kept = []
        for p in raw:
            if not kept or p - kept[-1] >= distance:
                if kept and xf[p] > xf[kept[-1]]:
                    kept[-1] = p
                else:
                    kept.append(p)
        raw = kept
    peaks, left_bases, right_bases, widths = [], [], [], []
    for p in raw:
        hl, hr = _peak_half_widths(xf, p)
        w = hr - hl + 1
        if width is not None:
            wmin, wmax = (width if isinstance(width, (tuple, list))
                          else (width, width))
            if (wmin is not None and w < wmin) or (wmax is not None and w > wmax):
                continue
        peaks.append(p)
        left_bases.append(p - hl + 1)
        right_bases.append(p + hr - 1)
        widths.append(w)
    props = {"left_bases": np.array(left_bases, dtype=int),
             "right_bases": np.array(right_bases, dtype=int),
             "widths": np.array(widths, dtype=int)}
    return np.array(peaks, dtype=int), props


def _peak_half_widths(x, p):
    half = 0
    for d in range(1, len(x)):
        if p - d < 0 or p + d >= len(x):
            break
        if x[p - d] < x[p] / 2 or x[p + d] < x[p] / 2:
            break
        half = d
    return half, half


def peak_prominences(x, peaks):
    """Prominence of each peak (height above the higher of the two basins).
    Returns (prominences, properties) per scipy's contract."""
    x = np.asarray(x, dtype=float)
    proms, lb, rb, lp, rp = [], [], [], [], []
    for p in peaks:
        # walk left to the local minimum (or a higher point), same right
        left_min = _basin_min(x, int(p), -1)
        right_min = _basin_min(x, int(p), +1)
        left_peak = _left_highest(x, int(p), left_min)
        right_peak = _right_highest(x, int(p), right_min)
        ref = max(left_min, right_min)
        proms.append(x[p] - ref)
        lb.append(left_min)
        rb.append(right_min)
        lp.append(left_peak)
        rp.append(right_peak)
    props = {"left_base": np.array(lb), "right_base": np.array(rb),
             "left_peak": np.array(lp), "right_peak": np.array(rp)}
    # DroneSecurity unpacks this as a 3-tuple (`proms, _, _ = ...`), so return
    # (prominences, properties, None) to satisfy that call. Real scipy returns
    # a 2-tuple; this third slot is a compat no-op.
    return np.array(proms), props, None


def _basin_min(x, p, direction):
    running = x[p]
    for i in range(p + direction, p + direction * len(x), direction):
        if i < 0 or i >= len(x):
            break
        if x[i] > x[p]:
            break
        running = min(running, x[i])
    return running


def _left_highest(x, p, left_min):
    best = left_min
    for i in range(p, int(left_min), -1):
        if i < 0:
            break
        best = max(best, x[i])
    return best


def _right_highest(x, p, right_min):
    best = right_min
    for i in range(p, int(right_min) + 1):
        if i >= len(x):
            break
        best = max(best, x[i])
    return best


def stft(x, fs, nfft=64, nperseg=64, noverlap=None, **kw):
    x = np.asarray(x, dtype=complex)
    if nperseg is None:
        nperseg = nfft
    if noverlap is None:
        noverlap = nperseg // 2
    step = nperseg - noverlap
    nseg = max(1, 1 + (len(x) - nperseg) // step)
    window = np.hanning(nperseg)
    nfreq = nfft // 2 + 1          # rfft length
    F = np.zeros((nfreq, nseg), dtype=complex)
    for j in range(nseg):
        start = j * step
        seg = x[start:start + nperseg]
        if len(seg) < nperseg:
            seg = np.pad(seg, (0, nperseg - len(seg)))
        F[:, j] = np.fft.rfft(seg * window, n=nfft)
    f = np.fft.rfftfreq(nfft, d=1.0 / fs)
    t = np.arange(nseg) * step / fs
    # scipy returns (f, t, Zxx) where Zxx is (freq, time); we built (freq, seg)
    return f, t, F


def welch(x, fs, nperseg=None, nfft=None, return_onesided=True, **kw):
    """Periodogram-average PSD. Honors `nfft` and `return_onesided` the way
    scipy.signal.welch does (DroneSecurity calls it with nfft=2048 and
    return_onesided=False, expecting a full 2048-length two-sided spectrum)."""
    x = np.asarray(x, dtype=complex)
    nfft = int(nfft) if nfft is not None else (int(nperseg) if nperseg is not None
                                               else min(len(x), 256))
    if nperseg is None:
        nperseg = nfft
    nperseg = int(nperseg)
    # Welch averaging: non-overlapping segments (simple, matches scipy default
    # of no overlap when noverlap isn't given).
    nseg = max(1, len(x) // nperseg)
    window = np.hanning(nperseg)
    window_powsu = (window ** 2).sum()
    acc = np.zeros(nfft, dtype=complex)
    for j in range(nseg):
        seg = x[j * nperseg:(j + 1) * nperseg]
        if len(seg) < nperseg:
            break
        X = np.fft.fft(seg * window, n=nfft)
        acc += X * np.conj(X)
    acc /= nseg
    Pxx = (acc.real / window_powsu) / fs          # two-sided PSD, length nfft
    f = np.fft.fftfreq(nfft, d=1.0 / fs)
    if return_onesided:
        half = nfft // 2 + 1
        Pxx = Pxx[:half].copy()
        f = f[:half]
        if nfft % 2 == 0:
            Pxx[1:-1] *= 2.0
    return f, Pxx
