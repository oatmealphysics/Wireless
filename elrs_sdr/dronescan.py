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
import time
import numpy as np
from dataclasses import dataclass, field

from . import wifi

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
    Band("2.4",   "2.4 GHz (ELRS/FrSky/Wi-Fi)",
         2.400e9, 2.480e9, 4.0e6,
         wifi_channels=(2412e6, 2417e6, 2422e6, 2427e6, 2432e6,
                        2437e6, 2442e6, 2447e6, 2452e6, 2457e6, 2462e6)),
    Band("5.8",   "5.8 GHz (FPV video / 5GHz edge)",
         5.725e9, 5.875e9, 4.0e6,       # 4 Msps = even decimation (no CIC rolloff)
         wifi_channels=()),
    Band("sub1g", "700-900 MHz (LoRa/ELRS-LR/RC)",
         700e6, 900e6, 4.0e6,
         wifi_channels=()),
    Band("433",   "433 MHz (old FHSS/telemetry)",
         425e6, 440e6, 2.0e6,
         wifi_channels=()),
]

# Order the auto-cycle sweeps in (2.4 first — highest drone relevance)
CYCLE_ORDER = ["2.4", "5.8", "sub1g", "433"]


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
    # --- best-effort identity (populated only when we actually have it) ---
    manufacturer: str | None = None    # OUI->vendor (Wi-Fi) or None (else)
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
                 dwell_per_band: int = 3):
        self.radio = radio
        self.dwell_s = dwell_s
        self.min_db = min_above_floor_db
        self.gain = gain
        self.dwell_per_band = max(1, dwell_per_band)
        self.locked_band = lock            # None = auto-cycle
        self._cycle_idx = 0
        self._steps_on_band = 0
        self.states: dict[str, BandState] = {b.name: BandState(
            b.name, b.label, b.lo_hz, b.hi_hz) for b in BANDS}
        self._buf = np.zeros(4096, dtype=np.complex64)
        self._block_len = 4096
        # history of per-band "peak freq" for hop detection
        self._hop_hist: dict[str, list] = {b.name: [] for b in BANDS}
        # which band we're physically tuned to right now
        self._tuned: str | None = None
        # persistent detection registry: track_key -> (first_seen, last_seen, count)
        # so the same physical signal accumulates age across cycles instead of
        # spawning a fresh detection every scan.
        self._tracked: dict[str, list] = {}

    def _current_band_name(self) -> str:
        if self.locked_band:
            return self.locked_band
        return CYCLE_ORDER[self._cycle_idx % len(CYCLE_ORDER)]

    def set_lock(self, band: str | None):
        self.locked_band = band
        self._steps_on_band = 0          # reset dwell when (re)locking

    def step(self) -> dict:
        """Advance one observation. Returns the current state dict (for API)."""
        name = self._current_band_name()
        b = band_by_name(name)
        center = (b.lo_hz + b.hi_hz) / 2.0
        if self._tuned != name:
            # retune to the new band + settle (discard the LO-transition block)
            self.radio.tune2(center, b.srate, self.gain)
            self.radio.recv_block(self._buf, 0.1)   # settle, discard
            self._tuned = name
            self._steps_on_band = 0
        # grab a block
        cnt = self.radio.recv_block(self._buf, self.dwell_s)
        st = self.states[name]
        st.last_update = time.time()
        if cnt and cnt > 0:
            self._process_block(b, st, self._buf[:cnt])
        # advance cycle ONLY after dwelling on this band for N observations
        # (not every step — re-tuning every step re-prints UHD's clock block
        #  and never lets hop history / a stable detection accumulate)
        if not self.locked_band:
            self._steps_on_band += 1
            if self._steps_on_band >= self.dwell_per_band:
                self._cycle_idx = (self._cycle_idx + 1) % len(CYCLE_ORDER)
                self._steps_on_band = 0
        return self.state_dict()

    def _process_block(self, b: Band, st: BandState, samples: np.ndarray):
        freqs, db = power_spectrum(samples, b.srate)
        if len(freqs) == 0:
            return
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
            # --- classify ---
            # LoRa chirp (most specific, check first)
            lora_s = lora_chirp_score(samples, b.srate)
            ofdm_s, ofdm_reason = ofdm_score(freqs, db, b, presence)
            video_s = analog_video_score(freqs, db, presence, b)
            # hop behavior: is the energy location moving across cycles?
            hop = self._track_hop(b.name, freqs, presence, db)
            det = self._classify(b, st, freqs, db, presence, peak_freq,
                                 lora_s, ofdm_s, ofdm_reason, video_s, hop)
            # best-effort manufacturer: attempt real 802.11 beacon decode for
            # OFDM signals on a Wi-Fi channel. Returns None (-> "—") until the
            # RF front-end is validated against a real capture; never a guess.
            if det.signature == "ofdm" and b.wifi_channels:
                mac = wifi.decode_beacon_mac(samples, b.srate)
                det.manufacturer = wifi.mac_to_oui_vendor(mac) if mac else None
            # persistence: key this detection so the same signal accumulates
            # age across cycles. 1 MHz freq bin + band + signature is stable
            # enough to avoid duping a drifting/constant signal.
            now = time.monotonic()
            det.track_key = f"{b.name}:{round(peak_freq/1e6)}:{det.signature}"
            rec = self._tracked.get(det.track_key)
            if rec is None:
                rec = [now, 0]
                self._tracked[det.track_key] = rec
            det.first_seen = rec[0]
            det.last_seen = now
            rec[1] += 1
            det.seen_count = rec[1]
            # keep only the most recent few detections
            st.detections = [det] + st.detections
            st.detections = st.detections[:5]
            st.hopping = hop
        else:
            st.present = False
            st.detections = []

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

    def _classify(self, b, st, freqs, db, presence, peak_freq,
                  lora_s, ofdm_s, ofdm_reason, video_s, hop) -> Detection:
        # Tiered decision. LoRa chirp is the most specific -> wins if high.
        if lora_s > 12.0:
            return Detection(peak_freq, float(db.max() - st.floor_db),
                             "lora",
                             "ELRS-family LoRa (2.4/868/915)",
                             "high",
                             f"chirp {lora_s:.0f}dB{' hopping' if hop else ''}")
        if video_s > 0.6:
            return Detection(peak_freq, float(db.max() - st.floor_db),
                             "video", "Analog FPV video (5.8 GHz)",
                             "high",
                             f"wide {ofdm_reason}{' constant' if not hop else ''}")
        if ofdm_s > 0.6:
            return Detection(peak_freq, float(db.max() - st.floor_db),
                             "ofdm", "Wi-Fi / OFDM (DJI / consumer / FPV-WiFi)",
                             "high" if ofdm_s > 0.8 else "medium",
                             f"{ofdm_reason}{' hopping' if hop else ' static'}")
        if hop and presence.sum() > 4:
            return Detection(peak_freq, float(db.max() - st.floor_db),
                             "fhss", "FHSS wideband (FrSky-family / generic)",
                             "medium",
                             f"hopping non-OFDM {ofdm_reason}")
        # low-confidence presence
        return Detection(peak_freq, float(db.max() - st.floor_db),
                         "unknown", "Unidentified RF (best-effort)",
                         "low",
                         f"{ofdm_reason or 'weak'}{' hopping' if hop else ''}")

    def state_dict(self) -> dict:
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
            "detections": self.detections_table(),
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

    def detections_table(self) -> list[dict]:
        """Flatten all band detections into one row-per-detection list for the
        table view. Deduped by track_key (most recent wins), sorted by
        confidence then strength. Includes age (how long it's been present)."""
        now = time.monotonic()
        best: dict[str, dict] = {}
        for name in CYCLE_ORDER:
            st = self.states[name]
            for d in st.detections:
                if not d.track_key:
                    continue
                row = {
                    "band": name,
                    "band_label": st.label,
                    "freq_mhz": round(d.freq_hz / 1e6, 1),
                    "protocol": d.protocol,
                    "signature": d.signature,
                    "make_model": d.protocol,          # protocol-family (honest)
                    "manufacturer": d.manufacturer,    # OUI->vendor or None
                    "confidence": d.confidence,
                    "strength_db": round(d.power_db, 1),
                    "hopping": st.hopping,
                    "type": "control-link" if (st.hopping and d.signature != "video")
                            else ("video" if d.signature == "video" else "static"),
                    "note": d.note,
                    "first_seen": d.first_seen,
                    "last_seen": d.last_seen,
                    "age_s": round(max(0.0, d.last_seen - d.first_seen), 1),
                    "seen_count": d.seen_count,
                }
                # keep the most-recent row per track_key
                prev = best.get(d.track_key)
                if prev is None or d.last_seen > prev["last_seen"]:
                    best[d.track_key] = row
        rows = list(best.values())
        # drop rows whose signal vanished > ~3s ago (stale) but keep for table
        conf_rank = {"high": 0, "medium": 1, "low": 2}
        rows.sort(key=lambda r: (conf_rank.get(r["confidence"], 3),
                                 -r["strength_db"]))
        return rows
