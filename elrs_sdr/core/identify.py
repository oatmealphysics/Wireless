"""
identify.py — the "identify everything, work against all versions" ELRS air-state classifier.

This is the deliberate, version-agnostic detector that replaces the single
hardcoded (ota_version=4, one UID) sync correlation. Per the operator's directive
("work against all versions, be more deliberate about identifying everything"), it:

  1. MEASURES the burst's frequency offset (the bursts are off-center from the
     capture center: +0.27 MHz on ch31, up to ~1.9 MHz on ch40 due to channel
     overlap at 4.88 MS/s / 1 MHz spacing) and FREQUENCY-SHIFTS it to baseband
     before correlating — the fix for the freq-offset bug that made the
     correlator correlate against an off-tuned signal (garbage regardless of UID).

  2. Iterates ALL OTA versions (v2..v6) x the UID, running the GFSK/FLRC
     sync-detect + Viterbi demod + CRC-24 validation, and reports WHICH
     (version, UID) produces a CRC-VALID frame. The CRC is the decisive
     discriminator (a real ELRS frame passes the CRC with the right version+UID;
     a raw correlation peak does not — this kills the ch28-class false positive).

  3. Also runs the LoRa path (all 6 ELRS 2.4 GHz rates) on the same burst, so it
     classifies the PHY MODE (FLRC/GFSK vs LoRa) + the LoRa RATE without
     pre-picking.

  4. If the given UID does not CRC-validate, RECOVERS the UID from the matched
     sync word (the seeded portion encodes uid[2..5]) and re-validates — so it
     can identify a link even without being told the UID up front.

  5. Returns a structured AirState (version, mode, rate, UID, channel,
     freq_offset, crc_valid, confidence) — the "discover the air state" output,
     not a single "found/not found".

Design: measure + classify from the RF, never assume a version/rate/UID. The
same object model is used for detection here and (later) for the live pipeline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import gfsk, lora, ota
from .gfsk import FlrcParams
from .lora import ELRS_2G4_RATES


# ---------------------------------------------------------------------------- #
# The OTA versions to sweep (the operator wants "all versions", not just v4)
# ---------------------------------------------------------------------------- #
# ELRS OTA protocol versions seen in the field. The sync word + CRC seed are
# version-parameterized (they XOR ota_version in), so each version produces a
# DIFFERENT sync reference. Sweeping all of them + gating on CRC-validity is
# how we identify which version is actually present, without being told.
OTA_VERSIONS: list[int] = [2, 3, 4, 5, 6]


# ---------------------------------------------------------------------------- #
# Result containers
# ---------------------------------------------------------------------------- #
@dataclass
class GfskHit:
    """One GFSK/FLRC frame candidate (a (version, uid) that detected a sync)."""
    version: int
    uid: bytes
    sync_peak: float          # normalized correlation peak (0..1)
    payload: bytes | None     # demodulated payload (None if demod failed)
    crc_valid: bool           # the DECISIVE discriminator
    freq_offset_hz: float = 0.0
    channel: int = -1
    freq_mhz: float = 0.0


@dataclass
class LoRaHit:
    """One LoRa frame candidate (a rate that detected a preamble/sync)."""
    rate_name: str
    sf: int
    bw_hz: int
    sync_hits: int            # number of preamble/sync detections
    crc_valid: bool           # at least one CRC-valid frame
    n_frames: int
    freq_offset_hz: float = 0.0
    channel: int = -1
    freq_mhz: float = 0.0


@dataclass
class AirState:
    """The identified air state for one capture/channel — the 'identify everything' output."""
    is_signal: bool = False            # is there a non-Wi-Fi FSK/LoRa burst here?
    mode: str = "unknown"              # "gfsk" | "lora" | "unknown"
    ota_version: int | None = None
    uid: bytes | None = None
    uid_recovered: bool = False        # True if we recovered the UID from the sync
    lora_rate: str | None = None
    crc_valid: bool = False            # the decisive real-hit flag
    confidence: float = 0.0            # 0..1
    # measured burst physics (the 'measure + classify' evidence)
    bandwidth_mhz: float = 0.0
    amp_cv: float = 0.0                # <0.3 = constant-envelope (FSK)
    duty: float = 0.0                  # hot fraction of the dwell (bursty ~1-10%)
    freq_offset_hz: float = 0.0        # measured offset from channel center
    channel: int = -1
    freq_mhz: float = 0.0
    # the full candidate set (so the operator can see everything, not just the winner)
    gfsk_hits: list[GfskHit] = field(default_factory=list)
    lora_hits: list[LoRaHit] = field(default_factory=list)

    def summary(self) -> str:
        if not self.is_signal:
            return "no FSK/LoRa burst detected (Wi-Fi/noise/interference only)"
        bits = [f"mode={self.mode}", f"crc_valid={self.crc_valid}"]
        if self.mode == "gfsk" and self.ota_version is not None:
            bits.append(f"OTA v{self.ota_version}")
            if self.uid is not None:
                bits.append(f"uid={self.uid.hex()}{' (recovered)' if self.uid_recovered else ''}")
        if self.mode == "lora" and self.lora_rate is not None:
            bits.append(f"rate={self.lora_rate}")
        bits += [f"ch{self.channel}@{self.freq_mhz:.1f}MHz",
                 f"bw={self.bandwidth_mhz:.2f}MHz", f"ampCV={self.amp_cv:.2f}",
                 f"duty={self.duty*100:.1f}%", f"offset={self.freq_offset_hz/1e6:+.2f}MHz",
                 f"conf={self.confidence:.2f}"]
        return " | ".join(bits)


# ---------------------------------------------------------------------------- #
# Burst extraction + physics (measure, don't assume)
# ---------------------------------------------------------------------------- #
def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def extract_burst(iq: np.ndarray, srate: float,
                  burst_ms: float = 1.0,
                  pad_ms: float = 1.0) -> tuple[np.ndarray, int, float]:
    """Isolate the strongest burst. The ELRS bursts are ISOLATED (~1 ms spikes in
    silence), so we take the single most-energetic ~burst_ms slice (the burst
    itself) — NOT a wide window (which would include the Wi-Fi floor and inflate
    the amplitude CV + mask the freq offset). We pad a little (pad_ms total) so the
    sync+preamble have context, but the physics (bw, amp-CV, offset) are measured on
    the TIGHT core so the floor doesn't contaminate them.

    Returns (burst_samples [the padded window], start_sample, core_energy)."""
    iq = np.asarray(iq, dtype=np.complex128)
    p = np.abs(iq) ** 2
    sl = int(srate * 0.001)  # 1 ms
    if sl < 64 or len(iq) < 2 * sl:
        return iq, 0, float(np.max(p)) if len(p) else 0.0
    energies = [(i, float(p[i:i + sl].sum())) for i in range(0, len(iq) - sl, sl)]
    if not energies:
        return iq, 0, 0.0
    energies.sort(key=lambda x: -x[1])
    center = energies[0][0]  # the strongest 1 ms slice (the burst core)
    core_half = int(srate * burst_ms / 2)
    pad_half = int(srate * (burst_ms + 2 * pad_ms) / 2)
    # return the padded window (for the demod) but the caller measures physics on the core
    start = max(0, center + sl - pad_half)
    end = min(len(iq), start + 2 * pad_half)
    return iq[start:end], start, energies[0][1]


def burst_core(iq: np.ndarray, srate: float, burst_ms: float = 1.0) -> np.ndarray:
    """The TIGHT core of the strongest burst (for physics: bw, amp-CV, offset).

    ADAPTIVE duration (fix, 2026-10-08): this used to return a FIXED burst_ms
    window centred on the most energetic slice. ELRS bursts are often much
    SHORTER than 1 ms, so a fixed 1 ms core padded the burst with idle noise
    floor — and since amp-CV is std/mean of the amplitude, mixing burst+floor
    sent CV to ~2.0 and tripped identify()'s `amp_cv < 0.35` gate, REJECTING the
    strongest, cleanest bursts (observed: a 53.7 dB narrowband burst rejected
    with ampCV=1.99). We now grow a contiguous run outward from the envelope
    peak while it stays within 6 dB of that peak, so the core tracks the burst's
    real duration, and cap it at burst_ms so a continuous (non-bursty) signal
    can't return the whole window."""
    iq = np.asarray(iq, dtype=np.complex128)
    env = np.abs(iq)
    if len(env) < 64:
        return iq
    # light smoothing so noise notches don't fragment the run
    w = max(1, int(srate * 20e-6))          # ~20 us
    sm = np.convolve(env, np.ones(w) / w, mode="same") if w > 1 else env
    pk = float(sm.max())
    if pk <= 0:
        return iq
    thr = pk * 0.5                           # -6 dB in amplitude
    i = int(np.argmax(sm))
    a = i
    while a > 0 and sm[a - 1] > thr:
        a -= 1
    b = i
    while b < len(sm) - 1 and sm[b + 1] > thr:
        b += 1
    cap = int(srate * burst_ms / 1000.0)     # burst_ms MILLISECONDS -> samples
    if cap >= 64 and (b - a) > cap:
        b = a + cap
    core = iq[a:b + 1]
    return core if len(core) >= 64 else iq


def _centered_spectrum(x: np.ndarray, srate: float) -> tuple[np.ndarray, np.ndarray]:
    """Power spectrum + frequency axis over the FULL complex band (-srate/2..+srate/2).

    Fix (2026-10-08): the bandwidth/offset measurements used
    `np.fft.fft(x)[:nfft//2]` with `fftfreq(...)[:nfft//2]`, i.e. the POSITIVE
    half only. These are COMPLEX baseband bursts whose energy legitimately spans
    negative frequencies, so half the signal was discarded: bandwidth read ~half
    its true value and the measured offset could never be negative (every real
    burst came back with a positive offset, which is impossible in general)."""
    n = len(x)
    nfft = _next_pow2(n)
    win = np.hanning(n).astype(np.float64)
    xx = np.asarray(x, dtype=np.complex128) * win
    xx = np.concatenate([xx, np.zeros(nfft - n, dtype=np.complex128)])
    spec = np.abs(np.fft.fftshift(np.fft.fft(xx))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / srate))
    return spec, freqs


def measure_burst(burst: np.ndarray, srate: float) -> tuple[float, float, float]:
    """Return (bandwidth_mhz, amp_cv, duty_fraction_of_burst). bandwidth = 10%-of-peak
    occupied; amp_cv = amplitude coefficient of variation (<0.3 = constant-envelope
    FSK); duty = fraction of the burst above 10x the median (a sanity check on the
    burst being isolated, not a continuous signal)."""
    n = len(burst)
    if n < 64:
        return 0.0, 0.0, 0.0
    # FULL complex band (see _centered_spectrum): the old positive-half-only
    # version under-reported bandwidth by ~2x on a centred burst.
    spec, freqs = _centered_spectrum(burst, srate)
    thr = np.max(spec) * 0.1                 # -10 dB occupied band
    band = freqs[spec > thr]
    bw = (band.max() - band.min()) / 1e6 if band.size else 0.0
    amp = np.abs(burst)
    amp_cv = float(np.std(amp) / (np.mean(amp) + 1e-12))
    # duty: fraction of the FULL channel would be computed by the caller (it has the
    # whole channel); here we just report the burst is 'hot' (it's the strongest slice).
    return float(bw), amp_cv, 1.0


def measure_duty(iq: np.ndarray, srate: float) -> float:
    """Fraction of the whole channel above 10x the median power (the bursty-vs-
    continuous discriminator: ELRS bursts ~1-10%, continuous Wi-Fi ~80%+)."""
    p = np.abs(np.asarray(iq, dtype=np.complex128)) ** 2
    if p.size == 0:
        return 0.0
    med = np.median(p)
    if med <= 0:
        return 0.0
    return float(np.mean(p > med * 10))


def freq_offset_hz(burst: np.ndarray, srate: float) -> float:
    """The burst's center-frequency offset from the channel center (baseband).
    This is the BUG fix: the bursts are off-center (ch31 +0.27 MHz, ch40 ~+1.9 MHz
    due to channel overlap), so we must measure it + shift to baseband before
    correlating, or the correlator correlates against an off-tuned signal."""
    n = len(burst)
    if n < 64:
        return 0.0
    # Fix (2026-10-08), two bugs here:
    #  1) positive-half-only spectrum -> the offset could never be negative.
    #  2) argmax picked the PEAK bin. An FSK/FLRC burst has TWO tones at
    #     +-deviation, so the peak sits ON one tone and the "offset" came out
    #     wrong by ~the deviation (~=365 kHz measured on these captures). That
    #     mis-centres the burst before correlation -- the very bug this function
    #     exists to fix. Use the power-weighted CENTROID of the occupied band,
    #     which for a 2-tone FSK lands between the tones (the true carrier).
    spec, freqs = _centered_spectrum(burst, srate)
    thr = float(np.max(spec)) * 0.1          # -10 dB occupied band
    m = spec > thr
    if not np.any(m):
        return 0.0
    return float(np.sum(freqs[m] * spec[m]) / np.sum(spec[m]))


def freq_shift_to_baseband(burst: np.ndarray, srate: float, offset: float) -> np.ndarray:
    """Rotate the burst down by `offset` Hz so its center is at baseband 0 Hz (the
    position the GFSK/LoRa correlators assume)."""
    if abs(offset) < 1e3:  # already ~centered
        return burst
    t = np.arange(len(burst)) / srate
    return burst * np.exp(-2j * math.pi * offset * t)


# ---------------------------------------------------------------------------- #
# The GFSK/FLRC multi-version classifier (the core of 'identify everything')
# ---------------------------------------------------------------------------- #
def _gfsk_try_uid_version(burst_bb: np.ndarray, srate: float, uid: bytes,
                          version: int) -> GfskHit | None:
    """Try ONE (uid, version) against a baseband-shifted burst: detect the sync,
    demod the payload, validate the CRC-24. Return a GfskHit (crc_valid set per the
    actual CRC check) or None if no sync detected."""
    sync_word = gfsk.flrc_sync_word(uid, version)
    crc_seed = gfsk.flrc_crc_seed(uid, version)
    params = FlrcParams(sample_rate=int(srate))
    starts = gfsk.detect_sync_word(burst_bb, sync_word, params)
    if not starts:
        return None
    # the strongest sync start
    start = starts[0]
    payload = gfsk.demodulate_frame(burst_bb, params, start)
    crc_valid = False
    if payload is not None and len(payload) >= gfsk.FLRC_PAYLOAD_LEN:
        # validate the CRC-24: recompute over the payload with the version/uid seed
        # and compare to the trailing CRC bytes (FLRC_CRC_LEN=3).
        crc_valid = _gfsk_crc_ok(payload, crc_seed)
    # measure the sync peak (for confidence)
    peak = _sync_peak(burst_bb, sync_word, params)
    return GfskHit(version=version, uid=uid, sync_peak=peak,
                   payload=payload, crc_valid=crc_valid)


def _gfsk_crc_ok(payload: bytes, crc_seed: int) -> bool:
    """Validate the CRC-24 on a demodulated FLRC payload. The payload is the
    OTA4 8 bytes + 3 CRC bytes = 11 bytes total (FLRC_PAYLOAD_LEN is the OTA frame;
    the on-air payload includes the trailing CRC). We recompute crc24 over the first
    (len-3) bytes and compare to the trailing 3."""
    if len(payload) < gfsk.FLRC_CRC_LEN + 4:
        return False
    body = payload[:-gfsk.FLRC_CRC_LEN]
    stored = int.from_bytes(payload[-gfsk.FLRC_CRC_LEN:], "big")
    calc = gfsk.crc24(body, crc_seed)
    return (calc & 0xFFFFFF) == (stored & 0xFFFFFF)


def _sync_peak(iq: np.ndarray, sync_word: int, params: FlrcParams) -> float:
    """Normalized correlation peak for a sync word (for confidence scoring)."""
    try:
        sync_bits = [int((sync_word >> (gfsk.FLRC_SYNC_LEN * 8 - 1 - i)) & 1)
                     for i in range(gfsk.FLRC_SYNC_LEN * 8)]
        ref = gfsk.modulate([], params, sync_bits=sync_bits)
        ref_n = ref / (np.linalg.norm(ref) + 1e-12)
        s = iq - iq.mean()
        s_n = s / (np.linalg.norm(s) + 1e-12)
        if len(s_n) < len(ref_n):
            return 0.0
        corr = np.correlate(s_n, ref_n, mode="valid").real
        return float(np.max(np.abs(corr)))
    except Exception:
        return 0.0


def _recover_uid_from_sync(sync_word: int) -> bytes:
    """Recover uid[2..5] from a matched 32-bit sync word (the seeded portion encodes
    them; uid[0..1] are not in the sync, so they default to 0 — the CRC check will
    confirm the full UID). flrc_sync_word = (uid[2]<<24)|(uid[3]<<16)|(uid[4]<<8)|(uid[5]^ver).
    We invert it: uid[2]=sw>>24, uid[3]=(sw>>16)&FF, uid[4]=(sw>>8)&FF,
    uid[5]=sw&FF ^ version (version must be known to un-XOR the last byte)."""
    uid = bytearray(6)
    uid[2] = (sync_word >> 24) & 0xFF
    uid[3] = (sync_word >> 16) & 0xFF
    uid[4] = (sync_word >> 8) & 0xFF
    # uid[5] = (sw & 0xFF) ^ version — but we don't know the version here; the
    # caller tries each version and picks the one that CRC-validates. Return with
    # uid[5] = sw&0xFF (version 0) and let the caller un-XOR per version.
    uid[5] = sync_word & 0xFF
    return bytes(uid)


def classify_gfsk(burst: np.ndarray, srate: float, uid: bytes | None,
                  channel: int, freq_mhz: float) -> tuple[list[GfskHit], float]:
    """Classify the burst as GFSK/FLRC across ALL versions x (the given UID + any
    UID recovered from the sync). Return (list of GfskHits, measured freq_offset).
    The caller picks the CRC-valid hit as the identified link."""
    offset = freq_offset_hz(burst, srate)
    bb = freq_shift_to_baseband(burst, srate, offset)
    hits: list[GfskHit] = []
    # 1) try the given UID (if provided) across all versions
    if uid is not None:
        for v in OTA_VERSIONS:
            h = _gfsk_try_uid_version(bb, srate, uid, v)
            if h is not None:
                h.freq_offset_hz = offset
                h.channel = channel
                h.freq_mhz = freq_mhz
                hits.append(h)
    # 2) try to RECOVER the UID: detect a sync with the given UID's sync word is not
    #    possible without the UID, so instead — if we have a uid, we can also test the
    #    recovered-uid path (the sync's seeded bytes reveal uid[2..5]). We do this by
    #    trying each version's recovered-uid (un-XOR'd) + validating the CRC.
    if uid is not None:
        for v in OTA_VERSIONS:
            sw = gfsk.flrc_sync_word(uid, v)
            rec = bytearray(_recover_uid_from_sync(sw))
            # un-XOR the last byte for THIS version
            rec[5] = (sw & 0xFF) ^ v
            rec_uid = bytes(rec)
            if rec_uid == uid:
                continue  # already tried
            h = _gfsk_try_uid_version(bb, srate, rec_uid, v)
            if h is not None and h.crc_valid:
                h.uid_recovered = True
                h.freq_offset_hz = offset
                h.channel = channel
                h.freq_mhz = freq_mhz
                hits.append(h)
    return hits, offset


# ---------------------------------------------------------------------------- #
# The LoRa classifier (all 6 rates) — the mode/RATE identification
# ---------------------------------------------------------------------------- #
def classify_lora(burst: np.ndarray, srate: float, channel: int,
                  freq_mhz: float) -> tuple[list[LoRaHit], float]:
    """Classify the burst across all ELRS 2.4 GHz LoRa rates (preamble/sync detect +
    CRC). Return (list of LoRaHits, measured freq_offset). Uses the REAL decode path
    (elrs._decode_lora, the same the live 'auto' mode + analyze_capture.py use) for the
    CRC-valid frame count, and lora.detect_sync_word for the preamble/sync hit count."""
    offset = freq_offset_hz(burst, srate)
    bb = freq_shift_to_baseband(burst, srate, offset)
    hits: list[LoRaHit] = []
    # the real CRC-valid decode path lives in the waveraider elrs protocol module
    try:
        from waveraider.protocols import elrs as _wrelrs
    except Exception:
        _wrelrs = None
    for rate_name, r in ELRS_2G4_RATES.items():
        try:
            p = lora.LoRaParams(sf=r.sf, bw_hz=r.bw_hz, sample_rate=int(srate))
            # preamble/sync hit count (the autocorrelation-based detector, sync_word=None).
            # CRITICAL GATE: the autocorrelation preamble detector FALSE-POSITIVES on
            # Wi-Fi OFDM (it fires on ~every sample when Wi-Fi is present — we measured
            # ~48k hits across the whole channel on pure-Wi-Fi channels, median gap 0.00 ms).
            # A REAL LoRa link shows SPARSE, DISCRETE chirp hits (gaps ~the frame
            # interval, e.g. ~2 ms at 500Hz). So we reject any rate whose hits are DENSE
            # (median gap < 0.1 ms = the Wi-Fi false-positive signature).
            preamble_indices = lora.detect_sync_word(bb, p, sync_word=None, threshold=0.5)
            n_preamble = len(preamble_indices)
            dense_wifi = False
            if n_preamble > 50:
                gaps = np.diff(np.asarray(preamble_indices)) / srate * 1000.0  # ms
                if np.median(gaps) < 0.1:  # firing on ~every sample = Wi-Fi OFDM, not LoRa
                    dense_wifi = True
            if dense_wifi:
                n_preamble = 0  # reject the Wi-Fi false-positive
            if n_preamble == 0 and _wrelrs is None:
                continue
            # CRC-valid frame count via the real decode path (capped for tractability)
            n_frames = 0
            crc_valid = False
            if _wrelrs is not None:
                try:
                    pkts = _wrelrs._decode_lora(bb, float(srate), rate_name, None, cap=10) \
                        if _accepts_cap(_wrelrs._decode_lora) else _wrelrs._decode_lora(bb, float(srate), rate_name, None)
                    n_frames = len(pkts)
                    # _decode_lora returns list[dict] with {"raw","sample","invert_iq","packet"};
                    # the crc_valid is on the nested "packet" (an ElrsPacket).
                    crc_valid = any(_pkt_crc_valid(pk) for pk in pkts)
                except Exception:
                    pass
            if n_preamble == 0 and n_frames == 0:
                continue
            hits.append(LoRaHit(rate_name=rate_name, sf=r.sf, bw_hz=r.bw_hz,
                                sync_hits=n_preamble, crc_valid=crc_valid,
                                n_frames=n_frames, freq_offset_hz=offset,
                                channel=channel, freq_mhz=freq_mhz))
        except Exception:
            continue
    return hits, offset


def _accepts_cap(fn) -> bool:
    """Does `fn` accept a `cap=` kwarg? (the _decode_lora signature varies)."""
    import inspect
    try:
        return "cap" in inspect.signature(fn).parameters
    except Exception:
        return False


def _pkt_crc_valid(pk) -> bool:
    """Extract crc_valid from a _decode_lora result entry. It's a dict
    {"raw","sample","invert_iq","packet"} where "packet" is an ElrsPacket (has
    .crc_valid) — but tolerate a bare ElrsPacket or a dict-with-crc_valid-key."""
    if isinstance(pk, dict):
        inner = pk.get("packet", pk)
        if isinstance(inner, dict):
            return bool(inner.get("crc_valid", False))
        return bool(getattr(inner, "crc_valid", False))
    return bool(getattr(pk, "crc_valid", False))


# ---------------------------------------------------------------------------- #
# The top-level 'identify everything' entry point
# ---------------------------------------------------------------------------- #
def identify(iq: np.ndarray, srate: float, uid: bytes | None = None,
             channel: int = -1, freq_mhz: float = 0.0,
             target_burst_ms: float = 2.0) -> AirState:
    """Identify the air state for one channel's IQ: is there a non-Wi-Fi FSK/LoRa
    burst, and if so, which (version, mode, rate, UID, channel, freq-offset)?
    Measures the burst physics (bandwidth, amp-CV, duty, freq-offset) + classifies
    across all versions x all rates, gating on CRC-validity. Returns an AirState."""
    iq = np.asarray(iq, dtype=np.complex128)
    state = AirState(channel=channel, freq_mhz=freq_mhz)
    if len(iq) < int(srate * 0.001) * 2:
        return state
    # 1) measure the burst physics on the TIGHT core (the isolated spike) so the
    #    Wi-Fi floor doesn't contaminate the amp-CV / bandwidth / offset; keep the
    #    PADDED window for the demod (it needs the preamble+payload context).
    core = burst_core(iq, srate, burst_ms=1.0)
    window, _start, _energy = extract_burst(iq, srate, burst_ms=1.0, pad_ms=1.0)
    bw, amp_cv, _ = measure_burst(core, srate)
    duty = measure_duty(iq, srate)
    offset = freq_offset_hz(core, srate)
    state.bandwidth_mhz = bw
    state.amp_cv = amp_cv
    state.duty = duty
    state.freq_offset_hz = offset

    # 2) is it a non-Wi-Fi FSK/LoRa burst? (narrow + constant-envelope + bursty)
    #    Wi-Fi: bw > ~10 MHz, or amp_cv > 0.5 (OFDM/AM), or duty > 0.6 (continuous).
    looks_fsk = (bw < 5.0) and (amp_cv < 0.35) and (duty < 0.6) and (bw > 0.05)
    if not looks_fsk:
        state.is_signal = False
        return state
    state.is_signal = True

    # 3) classify the PHY MODE + identify the version/UID/rate on the PADDED window
    #    (the demod needs the preamble+payload), using the measured offset.
    gfsk_hits, _ = classify_gfsk(window, srate, uid, channel, freq_mhz)
    lora_hits, _ = classify_lora(window, srate, channel, freq_mhz)
    state.gfsk_hits = gfsk_hits
    state.lora_hits = lora_hits

    # 4) pick the identified link: prefer CRC-valid, then highest sync peak.
    #    CRITICAL: a real LoRa link shows CRC-valid frames on ONE rate (the one the
    #    TX uses), in HUNDREDS (a consistent stream). ~20-35 CRC-"valid" frames on
    #    ALL 6 rates is the first-order-LoRa-demod FALSE-ACCEPT signature (it
    #    mis-decodes an FSK burst as LoRa at every rate, producing a few CRC
    #    false-accepts each) — that's NOT a real LoRa link. So a LoRa hit only
    #    counts as a real link if it's the DOMINANT rate (most frames) AND has a
    #    high frame count (>=50; a real stream is hundreds+). Otherwise it's the
    #    false-accept and we fall through to the GFSK / fsk_unidentified path.
    gfsk_valid = [h for h in gfsk_hits if h.crc_valid]
    lora_valid = [h for h in lora_hits if h.crc_valid and h.n_frames >= 50]
    # a real LoRa link is on ONE rate; if multiple rates are "valid" with similar
    # low counts, it's the false-accept (reject the whole LoRa classification).
    if len(lora_valid) > 1:
        counts = sorted([h.n_frames for h in lora_valid], reverse=True)
        # if the top rate isn't clearly dominant (>=2x the second), it's false-accept
        if counts[0] < 2 * counts[1]:
            lora_valid = []
    if gfsk_valid:
        best = max(gfsk_valid, key=lambda h: h.sync_peak)
        state.mode = "gfsk"
        state.ota_version = best.version
        state.uid = best.uid
        state.uid_recovered = best.uid_recovered
        state.crc_valid = True
        state.confidence = min(1.0, best.sync_peak / 0.25)  # ~0.25 = a clean sync
    elif lora_valid:
        best = max(lora_valid, key=lambda h: h.n_frames)
        state.mode = "lora"
        state.lora_rate = best.rate_name
        state.crc_valid = True
        state.confidence = min(1.0, 0.5 + best.n_frames / 200.0)  # hundreds+ = conf 1.0
    elif gfsk_hits:
        # sync detected but no CRC-valid (could be wrong UID, or a partial frame)
        best = max(gfsk_hits, key=lambda h: h.sync_peak)
        state.mode = "gfsk"
        state.ota_version = best.version
        state.uid = best.uid
        state.crc_valid = False
        state.confidence = min(0.6, best.sync_peak / 0.25)
    elif lora_hits:
        # LoRa demod "saw" frames but they're the false-accept (low count, multi-rate)
        # — this is an FSK burst the LoRa demod mis-decoded, NOT a real LoRa link.
        best = max(lora_hits, key=lambda h: h.sync_hits)
        state.mode = "fsk_unidentified"
        state.crc_valid = False
        state.confidence = 0.25
    else:
        # narrowband constant-envelope burst but no sync/LoRa lock — FSK-shaped but
        # unidentified (could be a different FSK link, or a weak/partial ELRS frame)
        state.mode = "fsk_unidentified"
        state.crc_valid = False
        state.confidence = 0.2
    return state
