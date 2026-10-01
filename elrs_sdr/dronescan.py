"""
Multi-band drone presence + protocol-identifier engine.

Sweeps the B205mini across the drone-relevant RF bands (time-sliced — one radio
can't sample all bands at once) and classifies each band's energy by RF
signature. Built to run in a background thread and push state (spectrum frames
+ detections) that the web UI reads.

Bands (all within the B205mini 100 kHz - 6 GHz range):
  * sub1g : 700-900 MHz   (LoRa telemetry, 868/915 ELRS long-range, sub-1G RC)
  * 433   : 425-440 MHz   (older FHSS, some FrSky, telemetry)
  * 2.4   : 2.40-2.48 GHz (ELRS, FrSky, Wi-Fi, most control links)
  * 5.8   : 5.725-5.875 GHz (analog FPV video, some FHSS control, 5GHz edge)

Classifier (tiered, per-band, per observation window):
  1. presence   : bins above the noise floor by N dB
  2. signature  : only on bins with energy (keeps it fast)
       - LoRa chirp   : linear-chirp autocorr peak  -> ELRS / CRSF-LoRa / 915-LR   (HIGH)
       - OFDM / Wi-Fi : energy on known Wi-Fi channel centers + wide/structured    (HIGH/MED)
       - FHSS wideband: fast multi-bin hopping, non-OFDM, no chirp                (MED, family not vendor)
       - analog video : 5.8 GHz wideband constant (>~20 MHz occupied)             (HIGH)
       - else         : unknown                                                    (LOW)
  3. hop behavior: bin energy tracked across cycles -> hopping (control) vs static

The classifier functions (classify_band, lora_chirp_score, ofdm_score, ...) are
pure numpy -> unit-testable offline without a radio (see selftest fixtures).
"""

from __future__ import annotations

import math
import threading
import time
import numpy as np
from dataclasses import dataclass, field

from . import wifi
from . import positivedet

# ----------------------------------------------------------------------------
# Band definitions
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Band:
    name: str
    label: str
    lo_hz: float
    hi_hz: float
    srate: float          # IQ sample rate for this band (within B205 31.25kHz-16MHz)
    # known Wi-Fi channel centers within this band (for OFDM classification)
    wifi_channels: tuple = ()

BANDS: list[Band] = [
    Band("2.4",   "2.4 GHz (ELRS/FrSky/Wi-Fi/OcuSync)",
         2.400e9, 2.480e9, 4.0e6,
         wifi_channels=(2412e6, 2417e6, 2422e6, 2427e6, 2432e6,
                        2437e6, 2442e6, 2447e6, 2452e6, 2457e6, 2462e6)),
    Band("5.8",   "5.8 GHz (FPV analog video / 5GHz edge)",
         5.725e9, 5.875e9, 4.0e6,       # 4 Msps = even decimation (no CIC rolloff)
         wifi_channels=()),
    Band("5ghz",  "5 GHz (DJI OcuSync O3/O4 / Lightbridge)",
         5.150e9, 5.350e9, 4.0e6,
         wifi_channels=()),
    Band("sub1g", "700-900 MHz (LoRa/ELRS-LR/RC)",
         700e6, 900e6, 4.0e6,
         wifi_channels=()),
    Band("1.2",   "1.2 GHz (legacy FPV analog video)",
         1.240e9, 1.300e9, 4.0e6,
         wifi_channels=()),
    Band("433",   "433 MHz (old FHSS/telemetry)",
         425e6, 440e6, 2.0e6,
         wifi_channels=()),
]

# Order the auto-cycle sweeps in (2.4 first — highest drone relevance). The
# new bands (5 GHz OcuSync, 1.2 GHz legacy video) are added so the full
# protocol catalog is covered.
CYCLE_ORDER = ["2.4", "5.8", "5ghz", "sub1g", "1.2", "433"]


# ----------------------------------------------------------------------------
# Wi-Fi channel <-> frequency (airodump-style). Lets the user select a channel
# by NUMBER (--channel 6) instead of a raw Hz freq. Standard 2.4 GHz + the
# common 5 GHz channels used by 802.11a/ac/ax.
# ----------------------------------------------------------------------------
# 2.4 GHz: ch 1-13 = 2407 + 5*ch MHz  (ch 1 = 2412, ch 6 = 2437, ch 11 = 2462)
# ch 14 = 2484 (Japan-only)
def wifi24_freq(ch: int) -> float:
    """2.4 GHz Wi-Fi channel center (Hz). ch 1-13 -> 2407+5*ch MHz; ch 14 -> 2484."""
    if 1 <= ch <= 13:
        return (2407.0 + 5.0 * ch) * 1e6
    if ch == 14:
        return 2484.0e6
    raise ValueError(f"invalid 2.4 GHz channel {ch} (1-14)")

# 5 GHz: ch N (36-177) = 5180 + 5*(N-36) MHz  (ch 36 = 5180, ch 100 = 5500,
# ch 149 = 5745)
def wifi5_freq(ch: int) -> float:
    """5 GHz Wi-Fi channel center (Hz). ch 36-177 -> 5180 + 5*(N-36) MHz."""
    if 36 <= ch <= 177:
        return (5180.0 + 5.0 * (ch - 36)) * 1e6
    raise ValueError(f"invalid 5 GHz channel {ch} (36-177)")

def wifi_channel_freq(ch: int) -> float:
    """Map a Wi-Fi channel number to its center frequency (Hz).
    1-14 -> 2.4 GHz band, 36-177 -> 5 GHz band (airodump-style)."""
    if 1 <= ch <= 14:
        return wifi24_freq(ch)
    if 36 <= ch <= 177:
        return wifi5_freq(ch)
    raise ValueError(f"unknown Wi-Fi channel {ch} (use 1-14 or 36-177)")

# The 2.4 GHz channels worth scanning (non-overlapping + the rest). airodump
# defaults to 1,6,11; we scan all 1-13 for completeness (fast at low dwell).
WIFI24_SCAN_CHANNELS = list(range(1, 14))
# Common 5 GHz channels (20 MHz spacing, US regulatory domain).
WIFI5_SCAN_CHANNELS = [36, 40, 44, 48, 52, 56, 60, 64,
                       100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140,
                       149, 153, 157, 161, 165]

def wifi_scan_channels(band: str = "2.4") -> list[int]:
    """The list of channel numbers to sweep for a given band."""
    if band in ("2.4", "2.4ghz"):
        return WIFI24_SCAN_CHANNELS
    if band in ("5.8", "5ghz", "5", "5g"):
        return WIFI5_SCAN_CHANNELS
    raise ValueError(f"no Wi-Fi scan channels for band {band!r}")


def band_by_name(name: str) -> Band:
    for b in BANDS:
        if b.name == name:
            return b
    raise KeyError(name)


# ----------------------------------------------------------------------------
# Spectral helpers (pure numpy — testable)
# ----------------------------------------------------------------------------
def power_spectrum(samples: np.ndarray, srate: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs_hz, power_db) for one block of complex IQ samples.

    power_db is relative to the full-band mean (so a flat band ~ 0 dB,
    a peak >> 0 dB, silence < 0 dB). freqs is the FFT bin grid for the block.
    """
    n = len(samples)
    if n == 0:
        return np.array([]), np.array([])
    # window + full fft (rfft on complex is broken in some numpy 2.x builds —
    # use fft and take the positive-frequency half; works everywhere)
    w = np.hanning(n)
    spec = np.fft.fft((samples * w).astype(np.complex128))
    half = n // 2 + 1
    spec = spec[:half]
    mag = np.abs(spec) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / srate)
    # convert to dB relative to mean power
    mean_p = mag.mean() + 1e-12
    db = 10.0 * np.log10(mag + 1e-12) - 10.0 * np.log10(mean_p + 1e-12)
    return freqs, db


def lora_chirp_score(samples: np.ndarray, srate: float, sf: int = 7,
                     thresh: float = 12.0) -> float:
    """
    Score how much a LoRa chirp is present in `samples` (dB, higher = more).

    A LoRa preamble is a TRAIN of identical up-chirp symbols (each 2^sf samples
    long). We matched-filter the incoming signal against the single-symbol
    base chirp (both polarities for ELRS IQ inversion) using FFT correlation.
    A real LoRa preamble produces a tall, NARROW, REPEATED correlation peak at
    the symbol period; noise does not. The score is the peak-to-noise ratio of
    that correlation (dB). The caller compares against `thresh` (default 12 dB).

    This is a *presence* score — it does not demodulate; exact symbol demod
    lives in lora.py.
    """
    n = len(samples)
    nsym = 1 << sf                      # one LoRa symbol = 2^sf samples
    if n < 2 * nsym:
        return -99.0
    from .lora import _base_chirp
    ref = _base_chirp(sf)               # length nsym, unit up-chirp
    ref = ref.astype(np.complex128)
    # FFT matched filter: correlate samples with ref (and its conjugate for
    # the inverted-IQ case). Use the shorter ref as the kernel.
    best = -99.0
    for kernel in (ref, np.conj(ref)):
        corr = _fft_correlate(samples, kernel)
        if corr.size == 0:
            continue
        # peak-to-median ratio in dB (robust: median ~ noise floor of corr)
        peak = float(np.abs(corr).max())
        noise = float(np.median(np.abs(corr))) + 1e-12
        best = max(best, 20.0 * math.log10(peak / noise + 1e-9))
    return best


def _fft_correlate(signal: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Full linear correlation via FFT.

    NOTE: this build of numpy (2.5.3) rejects rfft on complex input, so use
    the full fft/ifft (complex-safe everywhere).
    """
    n = len(signal) + len(kernel) - 1
    fftlen = 1
    while fftlen < n:
        fftlen <<= 1
    S = np.fft.fft(signal.astype(np.complex128), fftlen)
    K = np.fft.fft(kernel.astype(np.complex128), fftlen)
    corr = np.fft.ifft(S * np.conj(K), fftlen).real
    # 'full' correlation: the kernel can be longer; take the valid middle
    m = len(kernel)
    return corr[m - 1:m - 1 + len(signal)]


def ofdm_score(freqs: np.ndarray, power_db: np.ndarray, band: Band,
               presence_bins: np.ndarray) -> tuple[float, str]:
    """
    Score how much the present energy looks like OFDM/Wi-Fi (0..1, + a label).

    OFDM/Wi-Fi tells:
      * energy sits on known Wi-Fi channel centers (if the band has them)
      * it's relatively wide and structured (a cluster of bins, not a single spike)
      * duty is high (present most of the window)
    Returns (score 0..1, label).
    """
    if not presence_bins.any():
        return 0.0, "none"
    present = freqs[presence_bins]
    if len(present) == 0:
        return 0.0, "none"
    score = 0.0
    reasons = []
    # 1) energy near known Wi-Fi channels
    if band.wifi_channels:
        near = 0
        for fc in band.wifi_channels:
            if (np.abs(present - fc) < 1.5e6).any():
                near += 1
        frac = near / len(band.wifi_channels)
        if frac > 0:
            score += 0.5 * min(1.0, frac * 3)
            reasons.append(f"wifi-ch{near}")
    # 2) wide/structured: spread of present freqs
    # (1 MHz = clearly wideband vs a narrow tone; at 4 Msps the max
    #  representable spread is 2 MHz, so a full-window signal ~2 MHz)
    spread_mhz = (present.max() - present.min()) / 1e6 if len(present) > 1 else 0
    if spread_mhz > 1.0:
        score += 0.3
        reasons.append("wide")
    # 3) how many bins present (OFDM = many; a single carrier = few)
    if presence_bins.sum() > 8:
        score += 0.2
        reasons.append("multibin")
    return min(1.0, score), "/".join(reasons) if reasons else "low"


def analog_video_score(freqs: np.ndarray, power_db: np.ndarray,
                       presence_bins: np.ndarray, band: Band) -> float:
    """5.8 GHz analog video: a wide, constant, high-duty band of energy.

    Real analog FPV video (NTSC/PAL) is ~6-7 MHz carrier + ~6 MHz video bw,
    so a wide flat band that's present (high duty) is the tell. We score on
    spread > 2 MHz (the capture-limited floor) + high duty.
    """
    if band.name != "5.8":
        return 0.0
    if not presence_bins.any():
        return 0.0
    present = freqs[presence_bins]
    spread_mhz = (present.max() - present.min()) / 1e6 if len(present) > 1 else 0
    duty = presence_bins.mean()
    score = 0.0
    if spread_mhz > 1.5:       # wide (analog video is several MHz)
        score += 0.6
    if duty > 0.4:             # constant (not bursty)
        score += 0.4
    return min(1.0, score)


# ----------------------------------------------------------------------------
# Per-band detection state
# ----------------------------------------------------------------------------
@dataclass
class Detection:
    freq_hz: float
    power_db: float          # relative to band mean
    signature: str           # lora | ofdm | fhss | video | unknown
    protocol: str            # human label (ELRS-family, Wi-Fi/DJI, ...)
    confidence: str          # high | medium | low
    note: str = ""
    # --- positive-identification state ---
    confirmed: bool = False  # True only for CAPTURE-VALIDATED detectors (LoRa)
    metric: str = ""         # raw detector metric (chirp dB / OFDM M / line-rate / hop)
    # --- best-effort identity (populated only when we actually have it) ---
    manufacturer: str | None = None    # OUI->vendor (Wi-Fi) or None (else)
    mac: str | None = None             # the 802.11 BSSID/Source MAC (hex) when
                                       #   a beacon is actually decoded; else None
                                       #   (shown as "—" in the MAC column)
    # --- persistence tracking (set by the scanner, not the classifier) ---
    first_seen: float = 0.0            # monotonic time first observed
    last_seen: float = 0.0             # monotonic time last re-observed
    seen_count: int = 0                # how many scans it's been present
    # a stable key so the same physical signal accumulates across cycles
    track_key: str = ""


@dataclass
class BandState:
    name: str
    label: str
    lo_hz: float
    hi_hz: float
    present: bool = False
    presence_db: float = 0.0          # peak power above floor
    floor_db: float = 0.0
    hopping: bool = False
    detections: list = field(default_factory=list)
    # latest spectrum frame for the waterfall (downsampled to fixed width)
    frame: np.ndarray = field(default_factory=lambda: np.zeros(512))
    freqs_frame: np.ndarray = field(default_factory=lambda: np.zeros(512))
    last_update: float = 0.0
    # rolling IQ buffer for the 802.11 L-SIG demod (one block is too short for
    # the full preamble+L-SIG; we accumulate several blocks so demod_lsig has
    # enough samples to find + decode a valid L-SIG). Capped in length.
    iq_buf: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.complex64))
    # whether a valid 802.11 L-SIG has been decoded from the buffer (sticky:
    # once confirmed, stays until the band is retuned / buffer reset).
    lsig_valid: bool = False
    # the last decoded L-SIG rate index + length (for the row's metric).
    lsig_rate: int | None = None
    lsig_len: int | None = None
    # cadence counter: how many blocks since the last L-SIG scan (so we scan
    # on a fixed interval, not literally every block — bounded CPU).
    _lsig_scan_count: int = 0


# ----------------------------------------------------------------------------
# The scanner engine
# ----------------------------------------------------------------------------
class DroneScanner:
    """
    Time-sliced multi-band scanner. Call .step() repeatedly (it's what the
    webui thread calls); it cycles bands (or stays on a locked band), grabs a
    block, computes the spectrum, classifies, and updates per-band state.
    """

    def __init__(self, radio, dwell_s: float = 0.15, lock: str | None = None,
                 min_above_floor_db: float = 8.0, gain: float = 30.0,
                 dwell_per_band: int = 3, dwell_s_per_band: float = 2.0):
        self.radio = radio
        self.dwell_s = dwell_s
        self.min_db = min_above_floor_db
        self.gain = gain
        self.dwell_per_band = max(1, dwell_per_band)
        # wall-clock dwell: how long to stay on each band before cycling to the
        # next (2s default). Takes precedence over the observation-count dwell.
        self.dwell_s_per_band = max(0.1, float(dwell_s_per_band))
        self.locked_band = lock            # None = auto-cycle
        self._cycle_idx = 0
        self._steps_on_band = 0
        self._band_entered_at = time.monotonic()   # when we last (re)tuned
        self.states: dict[str, BandState] = {b.name: BandState(
            b.name, b.label, b.lo_hz, b.hi_hz) for b in BANDS}
        self._buf = np.zeros(4096, dtype=np.complex64)
        self._block_len = 4096
        # history of per-band "peak freq" for hop detection
        self._hop_hist: dict[str, list] = {b.name: [] for b in BANDS}
        # which band we're physically tuned to right now
        self._tuned: str | None = None
        # persistent detection registry: track_key -> {first_seen, last_seen,
        # count, row} so the same physical signal accumulates age across cycles
        # instead of spawning a fresh detection every scan, AND so the table
        # can show a signal within a time window even when it's not present now.
        self._tracked: dict[str, dict] = {}
        # time-window for the table (seconds). 0 = "all time" (no filter).
        self.window_s: float = 0.0
        # UHD's RX stream is NOT thread-safe: the live scan loop AND a
        # webui-initiated raw capture both call radio.recv_block() on the same
        # stream. Concurrent pulls corrupt each other (a blocked recv -> the
        # browser's fetch times out -> "Failed to fetch"). This lock serializes
        # all radio access: step() and capture_raw() each hold it, so only one
        # touches the radio at a time.
        self._radio_lock = threading.Lock()

    def _current_band_name(self) -> str:
        if self.locked_band:
            return self.locked_band
        return CYCLE_ORDER[self._cycle_idx % len(CYCLE_ORDER)]

    def set_lock(self, band: str | None):
        self.locked_band = band
        self._steps_on_band = 0          # reset dwell when (re)locking

    def set_window(self, seconds: float):
        """Set the table's time window (seconds). 0/negative = all time."""
        self.window_s = max(0.0, float(seconds))

    def confirm_80211(self, band_name: str, seconds: float = 3.0) -> dict:
        """Capture `seconds` of IQ on `band_name` and positively confirm 802.11.

        This is the live web-UI confirmation path: record a longer buffer than
        the 4096-sample scan block (the full preamble + L-SIG needs several ms),
        then run the L-SIG scan (brute-force demod_lsig across offsets) plus the
        full positive-detector suite. Returns a structured result:
          {band, srate, n_samples, lsig: {valid, rate, length, detail, hits},
           ofdm: {score, metric}, lora: {score, metric},
           confirmed, protocol}
        'confirmed' is True ONLY if a valid 802.11 L-SIG was decoded (the
        positive ID). Never returns a guess.
        """
        from . import wifi, positivedet
        b = band_by_name(band_name)
        seconds = max(0.2, min(15.0, float(seconds)))   # clamp 0.2..15 s
        n_total = int(seconds * b.srate)
        n_total = max(4096, (n_total // 4096) * 4096)    # multiple of a block
        # capture in blocks (recv_block returns a block at a time)
        buf = np.zeros(4096, dtype=np.complex64)
        chunks = []
        t_end = time.monotonic() + seconds
        self.radio.tune2((b.lo_hz + b.hi_hz) / 2, b.srate, self.gain)
        self.radio.recv_block(buf, 0.1)                  # settle, discard
        while time.monotonic() < t_end and len(chunks) * 4096 < n_total:
            cnt = self.radio.recv_block(buf, 0.5)
            if cnt:
                chunks.append(buf[:cnt].copy())
        if not chunks:
            return {"band": band_name, "srate": b.srate, "n_samples": 0,
                    "lsig": {"valid": False, "detail": "no samples captured"},
                    "ofdm": {"score": 0.0, "metric": ""},
                    "lora": {"score": 0.0, "metric": ""},
                    "confirmed": False, "protocol": "no capture"}
        samples = np.concatenate(chunks)[:n_total]
        n_samples = len(samples)
        # run the full positive-detector suite on the capture
        lora_score, lora_metric = positivedet.lora_chirp(samples, b.srate)
        ofdm_score, ofdm_metric = positivedet.ofdm_preamble(samples, b.srate)
        # L-SIG scan: brute-force demod_lsig across offsets to find a valid
        # 802.11 L-SIG anywhere in the capture (the front-end sync may miss the
        # exact offset, so we scan). This is the POSITIVE 802.11 confirmation.
        lsig = self._lsig_scan(samples, b.srate)
        confirmed = lsig["valid"]
        if confirmed:
            protocol = (f"Wi-Fi / 802.11 (CONFIRMED, rate idx {lsig['rate']}, "
                        f"len {lsig['length']})")
        elif lora_score >= self._LORA_CLAIM:
            protocol = "ELRS-family LoRa (CONFIRMED)"
        elif ofdm_score >= self._OFDM_CLAIM:
            protocol = "Wi-Fi / OFDM (UNCONFIRMED — no valid L-SIG)"
        else:
            protocol = "Unidentified RF (no positive match)"
        return {
            "band": band_name, "srate": b.srate, "n_samples": n_samples,
            "lsig": lsig,
            "ofdm": {"score": ofdm_score, "metric": ofdm_metric},
            "lora": {"score": lora_score, "metric": lora_metric},
            "confirmed": confirmed, "protocol": protocol,
        }

    def capture_raw(self, band_name: str, seconds: float = 3.0,
                    out_dir: str = ".") -> dict:
        """Record `seconds` of raw baseband IQ on `band_name` and save it in
        every useful format (CF32 for GNU Radio/SDR++/IQT, .complex8 for our
        `validate`, + a .meta.txt sidecar). This is the "save the raw signal so
        I can look at the drone protocol data" path.

        Returns {ok, paths:{cf32,complex8,meta}, band, srate, n_samples,
        center_hz, out_dir} or {ok:False, error:...} on failure.
        """
        from . import radio as _radio
        b = band_by_name(band_name)
        seconds = max(0.5, min(30.0, float(seconds)))
        center = (b.lo_hz + b.hi_hz) / 2.0
        buf = np.zeros(4096, dtype=np.complex64)
        chunks = []
        t_end = time.monotonic() + seconds
        # Hold the SAME radio lock the scan loop uses, so the live scanner and
        # this capture never call radio.recv_block() at the same time (UHD's RX
        # stream is not thread-safe; concurrent pulls segfault the server).
        # The scan loop simply waits its turn while we record.
        try:
            with self._radio_lock:
                self.radio.tune2(center, b.srate, self.gain)
                self.radio.recv_block(buf, 0.1)   # settle, discard
                while time.monotonic() < t_end:
                    cnt = self.radio.recv_block(buf, 0.5)
                    if cnt:
                        chunks.append(buf[:cnt].copy())
        except Exception as e:
            return {"ok": False, "error": f"capture failed: {e!r}"}
        if not chunks:
            return {"ok": False, "error": "no samples captured"}
        samples = np.concatenate(chunks)
        # timestamped base path so captures don't collide
        import os
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = os.path.join(out_dir, f"elrs_{band_name}_{stamp}")
        try:
            paths = _radio.write_capture_bundle(
                samples, base, b.srate, band_name, center,
                note="live webui raw capture")
        except Exception as e:
            return {"ok": False, "error": f"write failed: {e!r}"}
        return {"ok": True, "paths": paths, "band": band_name,
                "srate": b.srate, "n_samples": len(samples),
                "center_hz": center, "out_dir": out_dir}

    def wifi_scan(self, band: str = "2.4", dwell_ms: int = 40,
                  srate: float = 4e6,
                  channels: list | None = None,
                  dwell_s: float | None = None,
                  verbose: bool = True) -> list[dict]:
        """Sweep the Wi-Fi channels of a band and report per-channel signal
        strength (airodump-style). Returns a list of
        {channel, freq_hz, peak_db, occupancy, mean_db} sorted by strength.

        This is "scan instead of setting a freq" — find which channel(s) have
        a STEADY signal (a router beacon / drone link) before capturing.

        - dwell_s: how long to dwell on each channel (e.g. 5.0 s for a proper
          survey). If None, uses the legacy quick 40 ms scan.
        - occupancy: the fraction of the dwell window where the channel is
          active (above the noise floor). A steady AP is ~100%; impulsive noise
          or the B205's DC artifact is a few %.
        - peak_db / mean_db: peak and mean power above the noise floor.
        """
        if channels is None:
            channels = wifi_scan_channels(band)
        buf = np.zeros(8192, dtype=np.complex64)
        results = []
        # total dwell per channel (seconds)
        per_ch_s = dwell_s if dwell_s else (dwell_ms * (len(range(4)) + len(range(8))) / 1000.0)
        for ci, ch in enumerate(channels):
            try:
                freq = wifi_channel_freq(ch)
            except ValueError:
                continue
            self.radio.tune2(freq, srate, self.gain)
            self.radio.recv_block(buf, 0.05)   # settle after retune
            # dwell for per_ch_s seconds, measuring occupancy + peak
            t0 = time.time()
            peak_db = 0.0
            mean_db = 0.0
            active_blocks = 0
            total_blocks = 0
            floor_db = 0.0
            while time.time() - t0 < per_ch_s:
                if not self.radio.recv_block(buf, 0.02):
                    continue
                f, db = power_spectrum(buf, srate)
                fl = float(np.percentile(db, 20))
                floor_db = fl if floor_db == 0 else floor_db * 0.9 + fl * 0.1  # smooth
                peak_db = max(peak_db, float(db.max() - fl))
                mean_db += float(np.mean(db - fl))
                active_blocks += int((db > (fl + 6)).mean() > 0.5)
                total_blocks += 1
            occ = (active_blocks / total_blocks) if total_blocks else 0.0
            mean_db = mean_db / total_blocks if total_blocks else 0.0
            res = {
                "channel": ch,
                "freq_hz": freq,
                "freq_mhz": round(freq / 1e6, 1),
                "peak_db": round(peak_db, 1),
                "mean_db": round(mean_db, 1),
                "occupancy": round(occ, 2),
            }
            results.append(res)
            if verbose:
                bar = "#" * int(occ * 30)
                print(f"  ch {ch:>2}  {freq/1e6:7.1f} MHz  peak {peak_db:5.1f} dB  "
                      f"occ {occ*100:4.0f}% {bar}", flush=True)
        results.sort(key=lambda r: (r["occupancy"], r["peak_db"]), reverse=True)
        return results

    def _lsig_scan(self, samples: np.ndarray, srate: float,
                   max_offsets: int = 256) -> dict:
        """Brute-force wifi.demod_lsig across offsets to find a valid 802.11
        L-SIG. Returns {valid, hits, rate, length, detail, best_offset}.

        Tuned for the live scan loop: scans at 1-OFDM-symbol granularity
        (the L-SIG is 2 symbols long, so symbol-step covers all alignments)
        and caps the number of offsets (max_offsets) + early-exits on the
        first valid hit, so it's fast enough to run per-block. The offline
        `validate` CLI uses a finer step + more offsets for thoroughness.
        """
        from . import wifi
        sym = max(64, int(round(srate * 12.8e-6)))      # OFDM symbol period
        # window = 2 L-SIG symbols + preamble margin
        win = 4 * 64 + 2048
        if len(samples) < win:
            return {"valid": False, "hits": 0, "rate": None, "length": None,
                    "detail": "capture too short for L-SIG scan", "best_offset": None}
        step = sym                                    # 1-symbol granularity (fast)
        n_scan = min(max_offsets, (len(samples) - win) // step + 1)
        hits = 0
        best = {"valid": False, "hits": 0, "rate": None, "length": None,
                "detail": "no valid L-SIG found", "best_offset": None}
        for w in range(n_scan):
            off = w * step
            if off + win > len(samples):
                break
            try:
                ok, detail = wifi.demod_lsig(samples[off:off + win], srate)
            except Exception:
                continue
            if ok:
                hits += 1
                if not best["valid"]:
                    best["valid"] = True
                    best["best_offset"] = off
                    best["detail"] = detail
                    import re
                    m = re.search(r"rate=(\d+)", detail)
                    l = re.search(r"len=(\d+)", detail)
                    best["rate"] = int(m.group(1)) if m else None
                    best["length"] = int(l.group(1)) if l else None
                    break          # early-exit: one valid L-SIG is enough (fast)
        best["hits"] = hits
        return best

    def step(self, window_s: float | None = None) -> dict:
        """Advance one observation. Returns the current state dict (for API).

        Radio access (tune2 + recv_block) is held under self._radio_lock so it
        can't collide with a concurrent capture_raw() from the webui (UHD's RX
        stream is not thread-safe; concurrent pulls segfault)."""
        name = self._current_band_name()
        b = band_by_name(name)
        center = (b.lo_hz + b.hi_hz) / 2.0
        # --- radio access (serialized with capture_raw via _radio_lock) ---
        with self._radio_lock:
            if self._tuned != name:
                # retune to the new band + settle (discard the LO-transition block)
                self.radio.tune2(center, b.srate, self.gain)
                self.radio.recv_block(self._buf, 0.1)   # settle, discard
                self._tuned = name
                self._steps_on_band = 0
                self._band_entered_at = time.monotonic()
                # reset the rolling IQ buffer for the 802.11 L-SIG demod (the old
                # band's samples are not contiguous with the new band's)
                self.states[name].iq_buf = np.zeros(0, dtype=np.complex64)
                self.states[name].lsig_valid = False
                self.states[name].lsig_rate = None
                self.states[name].lsig_len = None
                self.states[name]._lsig_scan_count = 0
            # grab a block
            cnt = self.radio.recv_block(self._buf, self.dwell_s)
        # --- non-radio work (classification, cycle) runs WITHOUT the lock so it
        #     doesn't hold up the radio for a concurrent capture ---
        st = self.states[name]
        st.last_update = time.time()
        if cnt and cnt > 0:
            self._process_block(b, st, self._buf[:cnt])
        # advance the cycle once we've dwelled on this band for the configured
        # wall-clock time (default 2s). Time-based (not observation-count) so
        # the dwell is stable regardless of how fast blocks fill.
        if not self.locked_band:
            self._steps_on_band += 1
            if time.monotonic() - self._band_entered_at >= self.dwell_s_per_band:
                self._cycle_idx = (self._cycle_idx + 1) % len(CYCLE_ORDER)
                self._steps_on_band = 0
        return self.state_dict(window_s=window_s)

    def _process_block(self, b: Band, st: BandState, samples: np.ndarray):
        freqs, db = power_spectrum(samples, b.srate)
        if len(freqs) == 0:
            return
        # append to the rolling IQ buffer for the 802.11 L-SIG demod (capped so
        # it doesn't grow unbounded; ~16ms at 4 Msps = 65536 samples = enough
        # for several beacons' worth of preamble+L-SIG)
        cap = 65536
        st.iq_buf = np.concatenate([st.iq_buf, samples])[:cap] if len(samples) else st.iq_buf
        # floor = a robust low percentile (not min — min can be a dead bin)
        floor = float(np.percentile(db, 20))
        thr = floor + self.min_db
        presence = db > thr
        st.floor_db = floor
        # downsample the spectrum to a fixed-width frame for the waterfall
        n_frame = 512
        if len(db) >= n_frame:
            step = len(db) // n_frame
            st.frame = db[::step][:n_frame]
            st.freqs_frame = freqs[::step][:n_frame]
        if presence.any():
            peak_bin = int(np.argmax(db[presence]))
            # map the peak within the present subset back to the full index
            present_idx = np.where(presence)[0]
            peak_global = present_idx[peak_bin]
            st.present = True
            st.presence_db = float(db[peak_global] - floor)
            peak_freq = float(freqs[peak_global])
            # --- POSITIVE-IDENTIFICATION classify ---
            # Each protocol's OWN detector fires independently; nothing is
            # default-binned by elimination. LoRa is the only capture-validated
            # detector (confirmed=True); the rest are synthetic-calibrated and
            # reported as UNCONFIRMED with their raw metric.
            det = self._classify_positive(b, st, samples, freqs, db, presence,
                                          peak_freq)
            # best-effort Wi-Fi identity: attempt a real 802.11 beacon decode
            # for OFDM/802.11 signals. Returns None (-> "—" in the MAC column)
            # until the RF front-end is validated against a real capture; never
            # a guess. We try whenever the signature is OFDM (the L-SIG path)
            # and the band has a Wi-Fi channel range.
            if det.signature == "ofdm" and b.wifi_channels:
                mac = wifi.decode_beacon_mac(samples, b.srate)
                if mac is not None:
                    det.mac = wifi.mac_to_str(mac)      # e.g. "14:7d:53:aa:bb:cc"
                    det.manufacturer = wifi.mac_to_oui_vendor(mac)
            # persistence: key this detection so the same signal accumulates
            # age across cycles. 1 MHz freq bin + band + signature is stable
            # enough to avoid duping a drifting/constant signal. We store the
            # FULL latest row in the registry so the table can show a signal
            # within a time window even if it's not present right now.
            now = time.monotonic()
            det.track_key = f"{b.name}:{round(peak_freq/1e6)}:{det.signature}"
            row = self._row_from_detection(b, st, det, now)
            rec = self._tracked.get(det.track_key)
            if rec is None:
                rec = {"first_seen": now, "last_seen": now, "count": 1, "row": row}
                self._tracked[det.track_key] = rec
            else:
                rec["last_seen"] = now
                rec["count"] += 1
                rec["row"] = row           # keep the latest classification
            det.first_seen = rec["first_seen"]
            det.last_seen = now
            det.seen_count = rec["count"]
            # keep only the most recent few detections
            st.detections = [det] + st.detections
            st.detections = st.detections[:5]
            # (st.hopping is set inside _classify_positive via _track_hop)
        else:
            st.present = False
            st.detections = []

    def _row_from_detection(self, b, st, det, now) -> dict:
        """Build the table row for a detection (used for the persistent registry)."""
        return {
            "track_key": det.track_key,
            "band": b.name,
            "band_label": b.label,
            "freq_mhz": round(det.freq_hz / 1e6, 1),
            "protocol": det.protocol,
            "signature": det.signature,
            "make_model": det.protocol,
            "manufacturer": det.manufacturer,
            "mac": det.mac,             # 802.11 BSSID/Source MAC (hex) or None
            "confidence": det.confidence,
            "confirmed": det.confirmed,
            "metric": det.metric,
            "strength_db": round(det.power_db, 1),
            "hopping": st.hopping,
            "type": "control-link" if (st.hopping and det.signature != "video")
                    else ("video" if det.signature == "video" else "static"),
            "note": det.note,
        }

    def _track_hop(self, name: str, freqs: np.ndarray, presence: np.ndarray,
                   db: np.ndarray) -> bool:
        """Rough hop detector: does the strongest present bin move between cycles?"""
        if not presence.any():
            self._hop_hist[name].append(None)
            return False
        present_idx = np.where(presence)[0]
        strongest = present_idx[int(np.argmax(db[presence]))]
        peak_freq = float(freqs[strongest])
        hist = self._hop_hist[name]
        hist.append(peak_freq)
        hist = hist[-4:]
        self._hop_hist[name] = hist
        if len(hist) < 3:
            return False
        # distinct peak positions across the last few cycles
        # (0.5 MHz bin — fine enough to separate FHSS hops)
        distinct = len({round(f / 5e5) for f in hist if f is not None})
        return distinct >= 2

    # per-protocol claim thresholds (score 0..1). A protocol is CLAIMED only
    # if its own detector clears its threshold. LoRa is the only confirmed one.
    _LORA_CLAIM = 0.5      # chirp score (dB-normalized) >= 0.5  (~9 dB)
    _OFDM_CLAIM = 0.6      # Schmidl-Cox M peak (synthetic-calibrated)
    _VIDEO_CLAIM = 0.6     # line-rate prominence (synthetic-calibrated)
    _FHSS_CLAIM = 0.5      # discrete-freq hopping regularity (synthetic-calib)
    _OCUSYNC_CLAIM = 0.6   # OcuSync PHY (distinct ~2us OFDM symbol period),
                           #   synthetic-calibrated — UNCONFIRMED until a real
                           #   OcuSync capture validates the symbol period.

    def _classify_positive(self, b, st, samples, freqs, db, presence,
                           peak_freq) -> Detection:
        """Positive-identification classification.

        Run each protocol's OWN detector. A protocol is claimed only if its
        detector clears its threshold; the highest-scoring claimant wins. If
        NO detector claims, the signal is honestly 'unidentified' (no default
        bin). Only LoRa is capture-validated (confirmed=True); the others are
        synthetic-calibrated and reported UNCONFIRMED with their raw metric so
        a real capture can later tune the thresholds.
        """
        # track the peak-freq history (feeds FHSS + the hopping display flag)
        hop = self._track_hop(b.name, freqs, presence, db)
        st.hopping = hop
        power_db = float(db.max() - st.floor_db)

        # --- run every positive detector independently ---
        lora_score, lora_metric = positivedet.lora_chirp(samples, b.srate)
        # OFDM gate 1: Schmidl-Cox timing on this block (fast, per-block)
        ofdm_score, ofdm_metric = positivedet.ofdm_preamble(samples, b.srate)
        video_score, video_metric = positivedet.analog_video(samples, b.srate)
        freq_track = [f for f in self._hop_hist.get(b.name, []) if f is not None]
        fhss_score, fhss_metric = positivedet.fhss_hop(freq_track)

        # OFDM gate 2 (the POSITIVE 802.11 confirmation): run the L-SIG scan on
        # the ROLLING IQ buffer (accumulated across blocks — one 4096-sample
        # block is too short for the full preamble+L-SIG). As the signal comes
        # in, each block is appended to the buffer and the L-SIG is checked on
        # a fixed cadence (every ~40 blocks ≈ 40 ms at 4 Msps — a beacon is
        # periodic ~100 ms, so this reliably catches its L-SIG without scanning
        # literally every block). NOT gated on the current block's timing,
        # because the buffer holds the beacon's L-SIG across blocks and the
        # per-block timing is flaky. _lsig_scan is bounded (max_offsets +
        # early-exit on first hit) so it's cheap.
        st._lsig_scan_count += 1
        scan_now = (len(st.iq_buf) >= 8192 and
                    st._lsig_scan_count % 40 == 0)
        if scan_now:
            lsig = self._lsig_scan(st.iq_buf, b.srate)
            if lsig["valid"]:
                st.lsig_valid = True
                st.lsig_rate = lsig["rate"]
                st.lsig_len = lsig["length"]
        else:
            lsig = None
        # the sticky confirmed state (from a prior scan) keeps the row
        # CONFIRMED between scans; it's cleared on band retune / buffer reset.
        if st.lsig_valid:
            lsig = {"valid": True, "detail": "valid (sticky from prior scan)",
                    "hits": 1, "rate": st.lsig_rate, "length": st.lsig_len}
        elif lsig is None:
            lsig = {"valid": False, "detail": "scanning on cadence", "hits": 0,
                    "rate": None, "length": None}

        # candidate claimants: (score, signature, protocol, confirmed, metric)
        candidates = []
        if lora_score >= self._LORA_CLAIM:
            # LoRa is a PHYSICAL-LAYER family: ELRS, Meshtastic, and sub-1G
            # LoRa telemetry are ALL LoRa (the distinction is payload, not RF).
            # We label it as the family, not "ELRS," so we don't mislabel
            # Meshtastic. (Payload-level Meshtastic/MAVLink ID is a separate,
            # capture-gated demod pipeline — not an RF signature.)
            candidates.append((lora_score, "lora",
                               "LoRa (ELRS / Meshtastic / sub-1G telemetry)",
                               True, lora_metric))
        # 802.11 / OFDM: the Schmidl-Cox timing metric ALONE is NOT a reliable
        # discriminator — it saturates to M~1.0 for white noise / any
        # stationary signal (verified against real captures), so timing alone
        # would claim "Wi-Fi/OFDM" on broadband interference. The POSITIVE 802.11
        # confirmation is the decoded + validated L-SIG (Rate/Length/Parity/
        # Tail). So: only claim a Wi-Fi row when the L-SIG validated. The
        # timing score is kept diagnostic (shown in the metric) but never
        # claims on its own. This is the "positive ID only, no default-binning"
        # principle: no L-SIG -> no Wi-Fi claim -> "Unidentified RF".
        if st.lsig_valid:
            proto = ("Wi-Fi / 802.11 (DJI / consumer / FPV-WiFi)  "
                     f"L-SIG rate={st.lsig_rate} len={st.lsig_len}")
            metric = (f"802.11 L-SIG valid (rate={st.lsig_rate}, len={st.lsig_len}) "
                      f"OFDM M={ofdm_metric}")
            candidates.append((1.0, "ofdm", proto, True, metric))
        # DJI OcuSync / Lightbridge: proprietary OFDM with a DISTINCT symbol
        # period (~2 us) vs 802.11 (~4 us). Claimed on the OFDM bands (2.4 /
        # 5 GHz) when the OcuSync PHY detector fires AND the 802.11 L-SIG did
        # NOT validate (OcuSync is OFDM but not 802.11). UNCONFIRMED until a
        # real OcuSync capture validates the symbol period + preamble.
        if b.name in ("2.4", "5ghz") and not st.lsig_valid:
            ocus_score, ocus_metric = positivedet.ocusync_phy(samples, b.srate)
            if ocus_score >= self._OCUSYNC_CLAIM:
                candidates.append((ocus_score, "ocusync",
                                   "DJI OcuSync / Lightbridge (proprietary OFDM)",
                                   False, ocus_metric))
        # Analog FPV video: wide, constant, line-rate-modulated AM. Fires on
        # BOTH the 5.8 GHz and the 1.2 GHz legacy-video bands.
        if b.name in ("5.8", "1.2") and video_score >= self._VIDEO_CLAIM:
            candidates.append((video_score, "video",
                               f"Analog FPV video ({b.label.split()[0]} GHz)",
                               False, video_metric))
        if fhss_score >= self._FHSS_CLAIM and presence.sum() > 4:
            candidates.append((fhss_score, "fhss",
                               "FHSS wideband (FrSky-family / generic)",
                               False, fhss_metric))

        if not candidates:
            # nothing positively claimed — honestly unidentified (no default bin)
            return Detection(peak_freq, power_db, "unknown",
                             "Unidentified RF (no positive match)", "low",
                             confirmed=False,
                             metric=f"lora={lora_score:.2f} ofdm={ofdm_score:.2f} "
                                    f"vid={video_score:.2f} fhss={fhss_score:.2f}")

        # pick the best claimant; tie-break favors LoRa (the confirmed one)
        def rank(c):
            score, sig, proto, conf, metric = c
            return (score, 1 if conf else 0)
        best = max(candidates, key=rank)
        score, sig, proto, conf, metric = best
        # confidence label from score + confirmation
        if conf and score >= 0.8:
            conf_label = "high"
        elif conf:
            conf_label = "medium"
        elif score >= 0.8:
            conf_label = "medium"
        else:
            conf_label = "low"
        suffix = f"{' [UNCONFIRMED]' if not conf else ' [confirmed]'} ({metric})"
        return Detection(peak_freq, power_db, sig, proto, conf_label,
                         note=suffix, confirmed=conf, metric=metric)

    def state_dict(self, window_s: float | None = None) -> dict:
        ws = window_s if window_s is not None else self.window_s
        bands = []
        for name in CYCLE_ORDER:
            st = self.states[name]
            bands.append({
                "name": st.name,
                "label": st.label,
                "lo_hz": st.lo_hz,
                "hi_hz": st.hi_hz,
                "present": st.present,
                "presence_db": round(st.presence_db, 1),
                "floor_db": round(st.floor_db, 1),
                "hopping": st.hopping,
                "locked": (self.locked_band == name),
                "detections": [d.__dict__ for d in st.detections],
                # frame as a plain list for JSON (waterfall)
                "frame": [round(float(x), 1) for x in st.frame.tolist()],
                "freqs_frame": [int(x) for x in st.freqs_frame.tolist()],
                "last_update": st.last_update,
            })
        locked = self.locked_band
        return {
            "cycling": locked is None,
            "locked_band": locked,
            "bands": bands,
            "summary": self._summary(),
            "window_s": ws,
            "detections": self.detections_table(window_s=ws),
        }

    def _summary(self) -> str:
        hits = []
        for name in CYCLE_ORDER:
            st = self.states[name]
            for d in st.detections[:1]:
                if d.confidence in ("high", "medium"):
                    hits.append(f"{d.protocol} @ {d.freq_hz/1e6:.0f} MHz "
                                f"({d.confidence} conf)")
        if not hits:
            return "No drone-class signals detected (quiet or no RF)."
        return f"Likely drone signals: {len(hits)} — " + " | ".join(hits)

    def detections_table(self, window_s: float | None = None) -> list[dict]:
        """Flatten the persistent detection registry into one row per signal,
        across ALL bands (not just the viewed one).

        `window_s` (seconds, default = self.window_s; 0 = all time) keeps only
        signals whose `last_seen` is within the window. Each row carries the
        latest classification + first/last/age/seen-count. Sorted by confidence
        then strength.
        """
        ws = self.window_s if window_s is None else window_s
        now = time.monotonic()
        cutoff = now - ws if ws > 0 else float("-inf")
        rows = []
        for key, rec in self._tracked.items():
            if rec["last_seen"] < cutoff:
                continue
            row = dict(rec["row"])
            row["first_seen"] = rec["first_seen"]
            row["last_seen"] = rec["last_seen"]
            row["age_s"] = round(max(0.0, rec["last_seen"] - rec["first_seen"]), 1)
            row["seen_count"] = rec["count"]
            row["last_seen_s"] = round(max(0.0, now - rec["last_seen"]), 1)
            rows.append(row)
        conf_rank = {"high": 0, "medium": 1, "low": 2}
        rows.sort(key=lambda r: (conf_rank.get(r["confidence"], 3),
                                 -r["strength_db"]))
        return rows
