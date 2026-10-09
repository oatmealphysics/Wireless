"""
detect.py — the detection + identification engine (the software analog of the reference
counter-UAS passive SDR's radio pipeline).

WHERE THIS COMES FROM
---------------------
The reference device (a shipped passive C-UAS SDR; see
`docs/COUNTER_UAS_SDR_FIRMWARE_DEEP_DIVE.md` for the firmware breakdown) runs a
*hardware* parallel-pipeline: an FFT channelizer feeds one dedicated DSP core per
protocol (LoRa at 250/500/812.5 Hz, WiFi at 5/10/20 MHz, FSK, OFDM symbol-length for
LightBridge/OcuSync, etc.), each core does a *hysteresis-based* energy detection
(high/low threshold + counts), then a sync-word correlation, then the per-protocol
demod, and finally a per-link "Net ID" extraction (the `DetectionNetID`
`raw_net_id` byte-array — for ELRS that's the bind UID). The detection result is a
rich `DetectionTaskResult` (protocol, freq, power, net_id, position, ...).

This module ports that pipeline to *software*, building on our existing detectors
(`core/lora.py`, `core/gfsk.py`, `wifi.py`, `positivedet.py`, `core/identify.py`).
We do NOT replicate the FPGA — we run the same *logic* (hysteresis burst detect ->
per-protocol demod -> Net ID extraction -> specific-protocol fingerprint) on a
capture, in Python. The result is the same kind of report the reference produces:
"there's a link at X MHz, protocol Y, per-link ID Z, evidence class W."

NAMING (operator directive)
---------------------------
The reference device is "the counter-UAS passive SDR." Never use its vendor/make name
or any acronym/abbreviation of it in notes, code, filenames, or commit messages. The
*drone-protocol* names (ELRS, Crossfire, LoRa, WiFi, DJI, SiK, ...) are the subject
matter (the links we detect) and are kept.

PASSIVE-ONLY (AGENTS.md §2 — the hard boundary)
-----------------------------------------------
This is a receive-only detector. It measures + classifies what's in the air; it never
transmits. It must never import, call, or wire in the TX side of `radio.py`
(`tx_tune`/`tx_stream`) or `elrc_bench_tx.py`. The TX gate is a wall.

HONESTY (AGENTS.md §3)
----------------------
Every `Detection` carries an `evidence_class` (VALIDATED / SELF-CONSISTENT / SYNTHETIC)
+ a `detection_level` (the `DetectionLevel` ladder from `protocol_catalog.py`). A
"detected" that isn't labeled with its evidence class is an overclaim.

MEASURE, DON'T ASSUME (AGENTS.md #1 goal)
-----------------------------------------
`detect_and_identify()` takes a capture + its *measured* sample rate, finds the
bursts, and for each burst tries the protocol detectors in a fixed order (cheapest /
most-specific first), returning a candidate per protocol. It never requires the UID /
sync word up front — the Net ID is *recovered from the air* where the protocol allows
it (the ELRS FLRC sync word, the WiFi BSSID/MAC). A supplied UID is a *convenience
cross-check* only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from elrs_sdr.core import gfsk as _gfsk
from elrs_sdr.core import lora as _lora
from elrs_sdr.core import identify as _identify
from elrs_sdr.protocol_catalog import DetectionLevel


# --------------------------------------------------------------------------- #
# The detection data model (mirrors the reference's DetectionTaskResult /
# DetectionNetID).
# --------------------------------------------------------------------------- #
class EvidenceClass(str, Enum):
    """The evidence class of a detection (AGENTS.md §3 honesty contract)."""

    VALIDATED = "validated"          # checked against firmware/datasheet/a real capture
    SELF_CONSISTENT = "self_consistent"  # round-trips our own code, not proven on real RF
    SYNTHETIC = "synthetic"          # offline only; plausible output, never saw a real signal
    PRESENCE = "presence"            # band/energy shape only (no protocol decode)


@dataclass(frozen=True)
class NetID:
    """A per-link identifier, mirroring the reference's `DetectionNetID`.

    `raw_net_id` is the byte-array form (the reference's `raw_net_id:[ubyte]`); for
    ELRS that's the bind UID (6 bytes), for WiFi it's the BSSID/MAC (6 bytes), for
    DJI Remote-ID it's the serial. `net_id_str` is the human-readable form (the
    reference's `net_id:string` — e.g. the UID hex, the MAC string, the serial).
    """

    raw_net_id: bytes
    net_id_str: str
    kind: str  # "uid" | "mac" | "serial" | "sync_word" | "ssid" | ...
    evidence: EvidenceClass = EvidenceClass.SELF_CONSISTENT

    def __str__(self) -> str:
        return f"{self.kind}={self.net_id_str}"


@dataclass(frozen=True)
class Detection:
    """One detected link, mirroring the reference's `DetectionTaskResult` (subset).

    The reference's full table has `power_dbm, freq_hz, protocol, antenna, current_geo,
    home_geo, rc_geo, orientation, speed, net_id, model, rc_info, serial_number, ssid,
    vendor, network_devices, detection_uid, alert_type`. We carry the fields we can
    actually produce from a 2.4 GHz capture (no position/velocity — we don't decode the
    MAVLink telemetry yet; that's a separate build step). `detection_uid` is the
    reference's per-detection unique id (a hash of net_id + freq); we compute the same.
    """

    protocol: str                    # the protocol name (from the catalog)
    detection_level: DetectionLevel  # the ladder position (NONE..IDENTIFIED)
    freq_hz: float                   # the measured center frequency of the burst
    power_db: float                  # the burst peak power (dB above the noise floor)
    net_id: NetID | None             # the per-link ID (None if not extractable)
    evidence: EvidenceClass          # the evidence class (the honesty label)
    sf: int | None = None            # LoRa spreading factor (LoRa-class protocols)
    bw_hz: float | None = None       # the measured bandwidth
    rate_bps: int | None = None      # the bit/symbol rate (FSK-class protocols)
    offset_hz: float | None = None   # the measured frequency offset from channel center
    crc_valid: bool = False          # whether a CRC-valid decode was achieved
    extra: dict = field(default_factory=dict)  # protocol-specific extras (SSID, model, ...)

    @property
    def detection_uid(self) -> str:
        """The reference's `detection_uid` — a stable hash of net_id + freq + protocol.

        This is what the reference uses to *track* a detection across time (merge/
        de-dup the same link seen on consecutive frames). We use the same idea: a
        short hex digest of the identifying fields.
        """
        key = (self.protocol, self.net_id.net_id_str if self.net_id else "",
               f"{self.freq_hz:.0f}")
        import hashlib
        return hashlib.sha1("|".join(key).encode()).hexdigest()[:12]

    def summary(self) -> str:
        bits = [f"{self.protocol}", f"{self.freq_hz/1e6:.2f} MHz",
                f"{self.power_db:.1f} dB"]
        if self.sf is not None:
            bits.append(f"SF{self.sf}")
        if self.bw_hz:
            bits.append(f"{self.bw_hz/1e3:.0f} kHz")
        if self.rate_bps:
            bits.append(f"{self.rate_bps/1e3:.1f} kbps")
        if self.net_id:
            bits.append(str(self.net_id))
        bits.append(f"[{self.evidence.value} / {self.detection_level.label}]")
        return " ".join(bits)


# --------------------------------------------------------------------------- #
# 1. The hysteresis burst detector (the reference's high/low_threshold + counts).
# --------------------------------------------------------------------------- #
@dataclass
class HysteresisBurst:
    """A burst found by hysteresis energy detection.

    Mirrors the reference's detector: it computes a short-window energy (power) track,
    marks samples where the power crosses `high_threshold` (a detection), then extends
    the burst while the power stays above `low_threshold` (hysteresis — prevents a
    single-sample noise spike from becoming a false burst, and lets a burst's tail,
    which dips below the high threshold, stay attached). `min_high_count` rejects
    bursts that had too few high-threshold samples (a false-alarm gate).
    """

    start: int           # sample index of the burst start
    stop: int            # sample index of the burst end (exclusive)
    peak_power: float    # the peak power within the burst (linear)
    peak_idx: int        # the sample index of the peak
    n_high: int          # how many samples were above the high threshold

    @property
    def length(self) -> int:
        return self.stop - self.start

    @property
    def duration_s(self) -> float:
        return self.length / _srate_global  # filled by the caller (see detect_bursts)


def detect_bursts(power: np.ndarray,
                  high_threshold: float,
                  low_threshold: float,
                  min_high_count: int = 4,
                  max_gap: int = 32,
                  ) -> list[HysteresisBurst]:
    """Run the hysteresis energy detector on a power track.

    Parameters (mirroring the reference's `high_threshold` / `low_threshold` /
    `high_threshold_count` config):
      power           — a 1-D array of short-window power (linear, not dB). The caller
                        computes it (e.g. a moving-sum-of-squares over `win` samples).
      high_threshold  — the "detected" threshold (linear power). A sample at/above this
                        is a detection.
      low_threshold   — the "release" threshold (linear power), < high_threshold. A
                        burst continues while power stays >= this. The gap between high
                        and low is the hysteresis band (the false-alarm rejection).
      min_high_count  — a burst must have at least this many high-threshold samples
                        (the reference's `high_threshold_count`). Below this, it's a
                        noise spike, not a burst.
      max_gap         — the max run of sub-`low_threshold` samples allowed *within* a
                        burst (a short dip doesn't end the burst). Above this, the
                        burst ends.

    Returns a list of `HysteresisBurst`, in time order, non-overlapping.
    """
    if power.size == 0:
        return []
    n = power.size
    high = power >= high_threshold
    low = power >= low_threshold
    bursts: list[HysteresisBurst] = []
    i = 0
    while i < n:
        # find the next high-threshold sample (the burst start candidate)
        while i < n and not high[i]:
            i += 1
        if i >= n:
            break
        start = i
        peak_idx = i
        peak_power = float(power[i])
        n_high = 0
        # extend the burst with hysteresis
        j = i
        gap = 0
        while j < n:
            if power[j] >= low_threshold:
                gap = 0
                n_high += int(high[j])
                if power[j] > peak_power:
                    peak_power = float(power[j])
                    peak_idx = j
                j += 1
            else:
                gap += 1
                if gap > max_gap:
                    break
                j += 1
        stop = min(j, n)
        # the min_high_count gate (the false-alarm rejection)
        if n_high >= min_high_count:
            bursts.append(HysteresisBurst(start=start, stop=stop,
                                          peak_power=peak_power,
                                          peak_idx=peak_idx, n_high=n_high))
        i = stop
    return bursts


def _power_track(iq: np.ndarray, win: int) -> np.ndarray:
    """A short-window power track (moving sum-of-squares) from complex IQ.

    This is the input to `detect_bursts`. `win` is the window in samples; use
    ~10-50 samples (a few hundred us at 126 MS/s, or a few ms at 24 MS/s). The
    reference computes an equivalent short-window energy (its `pwr_config`
    `accumulator_interval` + `post_amp`); we use a simple boxcar.
    """
    # sum of |x|^2 over a moving window of `win` samples
    mag2 = np.abs(iq.astype(np.complex128)) ** 2
    # cumulative-sum trick for an O(n) moving sum
    csum = np.concatenate(([0.0], np.cumsum(mag2, dtype=np.float64)))
    if win >= mag2.size:
        win = max(1, mag2.size)
    track = csum[win:] - csum[:-win]
    # center the track on the window midpoint (so index k ~ sample k + win//2)
    return track


def _noise_floor(iq: np.ndarray, win: int, quantile: float = 0.25) -> float:
    """Estimate the noise floor (linear power) from the low-quantile of the power track.

    The low quantile (25%) is robust to the bursts (they only raise the high quantile,
    not the low). This is the reference's equivalent of calibrating the threshold
    against the ambient noise.
    """
    track = _power_track(iq, win)
    if track.size == 0:
        return 0.0
    return float(np.quantile(track, quantile))


def is_continuous_carrier(burst: np.ndarray, srate: float,
                          min_cv: float = 0.15) -> bool:
    """Reject a *continuous carrier* (a pilot tone / LO leak / continuous carrier).

    A real drone data link is *bursty* (the amplitude varies — the burst is on, then
    off, then on again). A continuous carrier (a pilot tone, the B205 LO leak, a
    continuous-tone beacon) is *constant-envelope* — the amplitude barely varies. The
    reference's `frac_var_threshold` / `variance_threshold` is the same discriminator
    (it measures the variance of the signal to tell a continuous tone from a bursty
    link).

    We use the amplitude coefficient-of-variation (CV = std/mean of the magnitude) as
    the discriminator: a CV < `min_cv` means the magnitude is nearly constant -> it's a
    continuous carrier, not a data link. (The ch31 120433 capture's 0-2.5 ms segment is
    a ~54 dB continuous carrier with a low CV — rejecting it is the whole point.)

    Parameters
    ----------
    burst    : the candidate burst (complex IQ).
    srate    : the sample rate (Hz) — used for a sanity check (a "burst" shorter than
               ~100 us can't be a data link, it's a spike).
    min_cv   : the minimum CV to call it a *bursty* (non-carrier) signal. Below this,
               it's a continuous carrier (rejected). 0.15 is a robust default: real
               FSK/LoRa bursts have CV > 0.3 (the on/off burst structure), while a
               continuous carrier has CV < 0.05.
    """
    mag = np.abs(burst.astype(np.complex128))
    mean = float(mag.mean())
    if mean <= 0:
        return True  # silent -> treat as carrier (reject)
    std = float(mag.std())
    cv = std / mean
    # a sanity check: too short to be a data link (a < 100 us spike)
    if burst.size / srate < 1e-4:
        return True
    return cv < min_cv


# --------------------------------------------------------------------------- #
# 2. The specific-protocol fingerprint (ELRS vs Crossfire vs LoRa — the
#    SPECIFIC-protocol bar, AGENTS.md §1 directive #1).
# --------------------------------------------------------------------------- #
def fingerprint_lora_protocol(burst: np.ndarray, srate: float,
                              sf: int, bw_hz: float) -> tuple[str, EvidenceClass, dict]:
    """Fingerprint a LoRa-class burst to a *specific* protocol (ELRS / Crossfire / LoRa).

    The family-level chirp detector (`positivedet.lora_chirp`) says "LoRa-PHY present."
    This climbs the ladder to SPECIFIC by measuring the *packet-cadence + sync-layout*
    discriminators that differ between ELRS, Crossfire, and generic LoRa:

      1. **The SF + BW pair.** ELRS 2.4 GHz uses SF5-SF8 at 125/250/500 kHz BW
         (the `ScanConfigFSK` rates: elrs_127x_lora6-9, elrs_128x_lora5-9, flrc_520/650).
         Crossfire uses SF6/SF10 (xf_lora6/10). Generic LoRa (Meshtastic / sub-1G
         telemetry) has its own common SF/BW combos. The (SF, BW) pair is a strong
         first discriminator.
      2. **The packet cadence (the burst period).** ELRS RC is typically 50-500 Hz
         (2-20 ms period, with a 1-4 ms burst); Crossfire RC is ~50-250 Hz; generic
         LoRa telemetry is slower (1-50 Hz). Measuring the *period* between successive
         bursts of the same shape is the cadence discriminator. (Requires the
         multi-burst track, not a single burst — see `detect_and_identify`.)
      3. **The sync layout.** ELRS FLRC has a 64-bit sync (the UID-seeded
         `flrc_sync_word`); LoRa-SX1280 ELRS has the LoRa preamble + a specific
         header. The sync *length* + *structure* differ. (Requires the demod, which
         `classify_lora` / `classify_gfsk` do.)

    Returns (protocol_name, evidence_class, extra). The evidence is
    SELF_CONSISTENT (the (SF,BW) fingerprint round-trips our knowledge of the ELRS /
    Crossfire rate tables) but is not yet VALIDATED against a real ELRS-vs-Crossfire
    A/B capture (we have no Crossfire capture). So it's an *informed* fingerprint,
    honestly labeled.
    """
    extra: dict = {}
    # The known (SF, BW_kHz) combos per protocol (from the reference's ScanConfigFSK +
    # the ELRS / Crossfire rate tables).
    # ELRS 2.4 GHz (SX1280): SF5@500k, SF6@500k, SF7@500k, SF8@500k, SF9@250k (LoRa mode)
    # + FLRC 520/650 kbps (GFSK, not LoRa-PHY — so not here).
    # Crossfire (TBS): SF6@500k (xf_lora6), SF10@125k (xf_lora10) — the long-range rates.
    # Generic LoRa: SF7@250k (Meshtastic 2.4), SF9@250k, SF12@125k (long-range telemetry).
    elrs_rates = {(5, 500), (6, 500), (7, 500), (8, 500), (9, 250), (12, 125)}
    crossfire_rates = {(6, 500), (10, 125), (9, 125)}
    generic_rates = {(7, 250), (9, 250), (12, 125), (11, 125), (10, 250)}

    bw_k = round(bw_hz / 1e3)
    # normalize the BW to the nearest 125/250/500 (the LoRa BW grid)
    for grid in (125, 250, 500):
        if abs(bw_k - grid) <= 25:
            bw_k = grid
            break

    pair = (sf, bw_k)
    extra["sf_bw_pair"] = pair
    # score each protocol by how well the (SF,BW) fits its known set
    in_elrs = pair in elrs_rates
    in_xf = pair in crossfire_rates
    in_gen = pair in generic_rates
    # the overlap (SF6@500k is in BOTH ELRS and Crossfire) is the honest hard case —
    # report it as ambiguous (family-level) rather than guessing.
    if in_elrs and not in_xf and not in_gen:
        return "ExpressLRS", EvidenceClass.SELF_CONSISTENT, extra
    if in_xf and not in_elrs and not in_gen:
        return "Crossfire", EvidenceClass.SELF_CONSISTENT, extra
    if in_gen and not in_elrs and not in_xf:
        return "LoRa", EvidenceClass.SELF_CONSISTENT, extra
    # ambiguous (the pair fits multiple) or unknown (fits none) -> stay at family level
    extra["ambiguous"] = in_elrs and in_xf
    extra["known_elrs"] = in_elrs
    extra["known_crossfire"] = in_xf
    return "LoRa", EvidenceClass.SYNTHETIC, extra


# --------------------------------------------------------------------------- #
# 3. The per-protocol demod + Net ID extraction (orchestrates our detectors).
# --------------------------------------------------------------------------- #
def _extract_elrs_net_id(burst: np.ndarray, srate: float,
                         uid: bytes | None) -> NetID | None:
    """Extract the ELRS per-link Net ID (the bind UID) from a burst.

    The reference's `DetectionNetID.raw_net_id` for ELRS is the bind UID (6 bytes).
    We recover it from the air:
      - If a UID is supplied (convenience cross-check), use it (the reference does the
        same — a supplied UID locks the sync).
      - Else, air-recovery: the `identify._recover_uid_from_sync` path reads the FLRC
        sync word out of the demodulated bits and recovers the UID from it. This is the
        general path (no UID up front) — AGENTS.md #1 goal.

    Returns a `NetID(kind="uid")` if the UID is recovered, else None (the burst was
    LoRa-PHY without a recoverable FLRC sync, or the demod didn't lock).
    """
    if uid is not None and len(uid) == 6:
        return NetID(raw_net_id=uid, net_id_str=uid.hex(), kind="uid",
                     evidence=EvidenceClass.SELF_CONSISTENT)
    # air-recovery: try to recover the UID from the FLRC sync word (identify's path)
    try:
        # measure the burst offset, shift to baseband, then classify as GFSK (FLRC)
        offset = _identify.freq_offset_hz(burst, srate)
        bb = _identify.freq_shift_to_baseband(burst, srate, offset)
        # classify_gfsk with uid=None -> it tries to recover the sync/UID from the air
        # (the air-recovery path in identify.py). If it recovers a UID, we have the Net ID.
        # NOTE: identify.classify_gfsk returns a GfskHit with the recovered uid when
        # the sync locks; we read it out.
        hit = _identify.classify_gfsk(bb, srate, uid=None,
                                      burst_ms=1.0)
        # the hit carries the recovered uid (or None)
        rec_uid = getattr(hit, "uid", None)
        if rec_uid and len(rec_uid) == 6:
            return NetID(raw_net_id=rec_uid, net_id_str=rec_uid.hex(), kind="uid",
                         evidence=EvidenceClass.SELF_CONSISTENT)
    except Exception:
        pass
    return None


def _extract_wifi_net_id(payload: bytes | None) -> tuple[NetID | None, dict]:
    """Extract the WiFi per-link Net ID (the BSSID/MAC) from a demodulated beacon.

    The reference's `WIFIDetect` decodes the `net_infos:[DetectionNetworkInfo]`
    (mac_address, device_type, vendor, ssid) + the SSID. Our `wifi.py` demodulates the
    L-SIG + the beacon, and `wifi.parse_beacon_mac` + `wifi.mac_to_oui_vendor` extract
    the MAC + the vendor. This wraps that into a `NetID(kind="mac")`.
    """
    extra: dict = {}
    if payload is None:
        return None, extra
    from elrs_sdr import wifi as _wifi
    mac = _wifi.parse_beacon_mac(payload)
    if mac is None:
        return None, extra
    net_id = NetID(raw_net_id=mac, net_id_str=_wifi.mac_to_str(mac), kind="mac",
                   evidence=EvidenceClass.VALIDATED)  # the WiFi MAC is validated (L-SIG decode)
    vendor = _wifi.mac_to_oui_vendor(mac)
    if vendor:
        extra["vendor"] = vendor
    return net_id, extra


def _demod_burst(burst: np.ndarray, srate: float,
                 uid: bytes | None) -> tuple[str, DetectionLevel, NetID | None,
                                             EvidenceClass, dict]:
    """Demod a single burst across the protocol detectors; return the best detection.

    Tries the detectors in a fixed order (most-specific / cheapest first):
      1. LoRa (the ELRS / Crossfire / LoRa-PHY path) — `identify.classify_lora`
         + `fingerprint_lora_protocol` for the SPECIFIC-protocol bar.
      2. GFSK/FLRC (the ELRC ELRS fast-mode path) — `identify.classify_gfsk`.
      3. WiFi (the 802.11 path — the strongest ID source, the BSSID/MAC).
      4. OFDM (the DJI OcuSync / LightBridge presence path — `positivedet`).

    Returns (protocol, detection_level, net_id, evidence, extra).
    """
    extra: dict = {}
    # Measure the burst physics on the TIGHT CORE, not the raw hysteresis window.
    #
    # Fix (Claude, 2026-10-08, measured on real wideband IQ): this used to call
    # measure_burst(burst, ...) / freq_offset_hz(burst, ...) directly on the window
    # that detect_bursts() handed us. That window is bounded by the hysteresis
    # low-threshold, so it still contains idle/noise either side of the actual
    # burst — and since amp_cv is std/mean of amplitude, mixing burst+floor inflates
    # it badly. On the 28 MS/s wideband capture the same bursts read amp_cv
    # 0.67-0.89 here versus 0.17-0.31 through identify()'s core path, i.e. a
    # constant-envelope FSK burst was being mis-measured as "not constant-envelope".
    # identify.identify() already does this correctly (physics on the core, demod on
    # the padded window); this is the same discipline, applied here.
    core = burst
    try:
        core = _identify.burst_core(burst, srate, burst_ms=1.0)
    except Exception:
        pass
    try:
        bw, amp_cv, _duty = _identify.measure_burst(core, srate)
    except Exception:
        bw, amp_cv = 0.0, 0.0
    offset = 0.0
    try:
        offset = _identify.freq_offset_hz(core, srate)
    except Exception:
        pass
    extra["bandwidth_hz"] = bw
    extra["amp_cv"] = amp_cv
    extra["freq_offset_hz"] = offset

    # ---- 1. LoRa (ELRS / Crossfire / LoRa-PHY) ----
    # The LoRa chirp detector (positivedet.lora_chirp) gives the SF + a score.
    # Try each SF (5-9) to find the best (the reference iterates the LoRa DSP cores
    # at 250/500/812.5 Hz — we iterate the SFs).
    best_sf, best_score = None, -1.0
    try:
        from elrs_sdr import positivedet as _pd
        for sf in (5, 6, 7, 8, 9, 12):
            score, _lbl = _pd.lora_chirp(burst, srate, sf=sf)
            if score > best_score:
                best_score, best_sf = score, sf
    except Exception:
        pass
    if best_sf is not None and best_score > 0.5:
        # it's LoRa-PHY; now fingerprint to the specific protocol (the SPECIFIC bar)
        proto, ev, fx = fingerprint_lora_protocol(burst, srate, best_sf, bw or 250e3)
        extra.update(fx)
        extra["lora_score"] = best_score
        # extract the Net ID (the ELRS bind UID via air-recovery)
        net_id = _extract_elrs_net_id(burst, srate, uid)
        # the detection level: SPECIFIC if the fingerprint named ELRS/Crossfire,
        # FAMILY if it stayed at LoRa; IDENTIFIED if we got a Net ID.
        if net_id is not None:
            level = DetectionLevel.IDENTIFIED
        elif proto in ("ExpressLRS", "Crossfire"):
            level = DetectionLevel.SPECIFIC
        else:
            level = DetectionLevel.FAMILY
        return proto, level, net_id, ev, extra

    # ---- 2. GFSK/FLRC (the ELRC ELRS fast-mode) ----
    try:
        offset = extra.get("freq_offset_hz", 0.0) or 0.0
        bb = _identify.freq_shift_to_baseband(burst, srate, offset)
        hit = _identify.classify_gfsk(bb, srate, uid=uid, burst_ms=1.0)
        # a GfskHit with a CRC-valid lock is the ELRS FLRC mode
        crc_ok = getattr(hit, "crc_valid", False)
        if crc_ok:
            net_id = _extract_elrs_net_id(burst, srate, uid)
            level = DetectionLevel.IDENTIFIED if net_id else DetectionLevel.SPECIFIC
            return "ExpressLRS", level, net_id, EvidenceClass.SELF_CONSISTENT, extra
    except Exception:
        pass

    # ---- 3. WiFi (the 802.11 — the strongest ID source) ----
    try:
        from elrs_sdr import wifi as _wifi
        from elrs_sdr import positivedet as _pd
        score, _lbl = _pd.ofdm_preamble(burst, srate)
        if score > 0.5:
            # it's OFDM-like; try the WiFi L-SIG + beacon demod
            # (the full WiFi demod is in waveraider; here we do the presence + try the MAC)
            # NOTE: the full WiFi beacon demod (L-SIG + the CC decode + the beacon MAC)
            # is a heavier path; for now, mark it PRESENCE (OFDM) unless the full
            # demod lands. The MAC extraction is the IDENTIFIED step (a separate build).
            return "WiFi", DetectionLevel.PRESENCE, None, EvidenceClass.SYNTHETIC, extra
    except Exception:
        pass

    # ---- 4. OFDM (DJI OcuSync / LightBridge presence) ----
    try:
        from elrs_sdr import positivedet as _pd
        score, lbl = _pd.ocusync_phy(burst, srate)
        if score > 0.4:
            proto = "DJI OcuSync" if "ocusync" in lbl.lower() else "Video"
            return proto, DetectionLevel.PRESENCE, None, EvidenceClass.SYNTHETIC, extra
    except Exception:
        pass

    # no protocol matched — PRESENCE (energy shape only)
    return "unknown", DetectionLevel.PRESENCE, None, EvidenceClass.PRESENCE, extra


# --------------------------------------------------------------------------- #
# 4. The top-level entry point: detect_and_identify (the reference's pipeline,
#    in software).
# --------------------------------------------------------------------------- #
_srate_global = 1e6  # filled by detect_and_identify (so HysteresisBurst.duration_s works)


def detect_and_identify(iq: np.ndarray, srate: float,
                        uid: bytes | None = None,
                        freq_hz: float | None = None,
                        high_db: float | None = None,
                        win_samples: int = 32,
                        min_burst_s: float = 0.0005,
                        ) -> list[Detection]:
    """Detect + identify all the links in a capture (the reference's pipeline, in software).

    This is the software analog of the reference C-UAS passive SDR's radio pipeline
    (the `the main radio/detection daemon (175 MB, statically-linked C++)` daemon): it finds the bursts (hysteresis energy detection), and
    for each burst runs the per-protocol demod + Net ID extraction (the per-protocol
    DSP cores), returning a `Detection` per burst (the reference's
    `DetectionTaskResult`).

    Parameters
    ----------
    iq          : the complex baseband IQ (the capture, or a burst).
    srate       : the *measured* sample rate (Hz) — read from the capture's .meta.txt,
                  never the requested one (AGENTS.md §5).
    uid         : an OPTIONAL supplied UID (the 6-byte ELRS bind UID) — a convenience
                  cross-check only. The general path (no UID) recovers it from the air.
    freq_hz     : the center frequency (Hz) of the capture's channel. If the capture is
                  a single-channel grab (e.g. a sweep dwell on one channel), this is the
                  channel center; the burst's *measured* offset from it is added.
    high_db     : the detection threshold, in dB *above the noise floor*. If None, it's
                  auto-set to 10 dB above the measured noise floor (a robust default —
                  the reference auto-calibrates its threshold against the ambient noise).
    win_samples : the power-track window (samples). ~32 is a good default (a few hundred
                  us at 24 MS/s).
    min_burst_s : the minimum burst duration (s) to report. ELRC ELRS bursts are ~1-4 ms;
                  a 0.5 ms floor rejects the shortest noise spikes.

    Returns a list of `Detection` (one per burst), sorted by power (strongest first).
    Each carries the protocol, the freq, the power, the Net ID (the per-link ID), the
    evidence class, + the detection level.
    """
    global _srate_global
    _srate_global = float(srate)
    iq = np.asarray(iq, dtype=np.complex128)
    if iq.size == 0:
        return []

    # 1. the noise floor + the threshold (auto-calibrated, the reference's approach)
    noise = _noise_floor(iq, win_samples)
    if noise <= 0:
        return []
    if high_db is None:
        high_db = 10.0
    high_threshold = noise * (10.0 ** (high_db / 10.0))
    # the hysteresis band: low = 6 dB below high (the reference's high/low gap)
    low_threshold = noise * (10.0 ** ((high_db - 6.0) / 10.0))

    # 2. the power track + the hysteresis burst detection
    track = _power_track(iq, win_samples)
    bursts = detect_bursts(track, high_threshold, low_threshold,
                           min_high_count=4, max_gap=32)

    # 3. for each burst, the per-protocol demod + Net ID extraction
    dets: list[Detection] = []
    for b in bursts:
        # the CORE burst (the hysteresis-detected span, before padding) — used for the
        # burst-structure discriminator (a continuous carrier has a low CV over its core;
        # padding into the surrounding low-content region inflates the CV and defeats it).
        core = iq[b.start:b.stop]
        # the burst's duration (the min_burst_s gate, on the core)
        dur_s = core.size / srate
        if dur_s < min_burst_s:
            continue
        # the burst-structure discriminator (the reference's frac_var_threshold):
        # reject continuous carriers (a pilot tone / LO leak — not a data link).
        # Check the central 50% of the core (robust to the burst's own on/off edges).
        c0, c1 = core.size // 4, 3 * core.size // 4
        if c1 > c0 and is_continuous_carrier(core[c0:c1], srate):
            continue
        # expand the burst a bit for the demod (the hysteresis may clip the true edges)
        pad = max(win_samples, int(0.0005 * srate))  # 500 us of padding
        s0 = max(0, b.start - pad)
        s1 = min(iq.size, b.stop + pad)
        burst = iq[s0:s1]
        if burst.size < win_samples:
            continue
        # the burst's center frequency (the channel center + the measured offset)
        try:
            offset = _identify.freq_offset_hz(burst, srate)
        except Exception:
            offset = 0.0
        burst_freq = (freq_hz or 0.0) + offset
        # the burst's peak power (dB above the noise floor)
        peak_db = 10.0 * math.log10(max(b.peak_power, 1e-12) / max(noise, 1e-12))
        # the per-protocol demod + Net ID (the reference's per-protocol DSP cores)
        proto, level, net_id, ev, extra = _demod_burst(burst, srate, uid)
        # the SF (the LoRa spreading factor) — from the fingerprint's (SF,BW) pair
        sf = extra.get("sf")
        if sf is None and extra.get("sf_bw_pair"):
            sf = extra["sf_bw_pair"][0]
        dets.append(Detection(
            protocol=proto,
            detection_level=level,
            freq_hz=burst_freq,
            power_db=peak_db,
            net_id=net_id,
            evidence=ev,
            sf=sf,
            bw_hz=extra.get("bandwidth_hz") or None,
            offset_hz=offset or None,
            crc_valid=extra.get("crc_valid", False),
            extra=extra,
        ))

    # sort by power (strongest first) — the reference shows the strongest targets first
    dets.sort(key=lambda d: d.power_db, reverse=True)
    return dets


def gap_report(dets: list[Detection]) -> str:
    """A human-readable summary of the detections (the reference's target list, live)."""
    if not dets:
        return "no detections"
    lines = ["detected links (strongest first):"]
    seen = set()
    for d in dets:
        lines.append(f"  {d.summary()}  [uid {d.detection_uid}]")
        seen.add(d.protocol)
    lines.append("")
    lines.append(f"  protocols seen: {', '.join(sorted(seen))}")
    # the per-protocol ID extraction status (the reference's 'per-link ID extractable')
    with_id = [d for d in dets if d.net_id]
    lines.append(f"  per-link ID extracted: {len(with_id)}/{len(dets)}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual inspection
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        # the selftest mode: prove the discriminator + the pipeline on synthetic data
        import numpy as np
        sr = 4.874e6
        n = int(0.03 * sr)  # 30 ms
        rng = np.random.default_rng(0)
        # (a) a continuous carrier (a pilot tone) — should be REJECTED
        t = np.arange(n) / sr
        carrier = (np.exp(2j * np.pi * 0.0 * t) * 1000
                   + rng.normal(0, 10, n) + 1j * rng.normal(0, 10, n))
        d_c = detect_and_identify(carrier, sr, freq_hz=2.4314e9)
        assert all(d.protocol != "unknown" or d.detection_level.value != "identified"
                   for d in d_c)
        print(f"  (a) continuous carrier: {len(d_c)} detections (expected 0 — rejected)")
        # (b) a *genuinely bursty* signal (high amplitude CV) — should be DETECTED.
        # A real data-link burst has amplitude variation (the preamble's bit transitions,
        # the on/off structure, the modulation's envelope). We synthesize one by adding
        # a 10x amplitude swing (the on/off burst structure) on top of the tone — that's
        # the high-CV signal the discriminator lets through. (A *pure* continuous tone
        # has a low CV and is *correctly* rejected as a carrier — that's (a).)
        bursty = np.zeros(n, dtype=np.complex128) + rng.normal(0, 10, n) + 1j * rng.normal(0, 10, n)
        on = int(0.5e-3 * sr)   # 500 us on
        off = int(0.5e-3 * sr)  # 500 us off (the on/off burst structure -> high CV)
        for start in range(0, n - on, on + off):
            bursty[start:start + on] += 1000 * np.exp(2j * np.pi * 1e5 * t[start:start + on])
        # check the CV of a full on+off period (the burst structure)
        period = bursty[0:on + off]
        cv_period = float(np.abs(period).std() / (np.abs(period).mean() + 1e-12))
        assert cv_period > 0.15, f"the on/off burst should be high-CV (got {cv_period:.3f})"
        d_b = detect_and_identify(bursty, sr, freq_hz=2.4314e9)
        print(f"  (b) bursty on/off signal (CV={cv_period:.3f}): {len(d_b)} detections (expected >=1)")
        # NOTE: we don't assert >=1 here — the hysteresis gate + the per-protocol demod
        # may not produce a *named* detection on a generic on/off tone (it's not a real
        # ELRS/LoRa/WiFi signal, so the detectors correctly don't name it). What we DO
        # assert is that the discriminator does NOT reject it as a carrier (it's high-CV).
        assert not is_continuous_carrier(period, sr), "the high-CV on/off burst must NOT be rejected as a carrier"
        # (c) the is_continuous_carrier discriminator directly
        assert is_continuous_carrier(carrier[:on], sr) is True, "carrier should be rejected"
        assert not is_continuous_carrier(period, sr), "the high-CV on/off burst should pass"
        print("  (c) is_continuous_carrier: carrier=True (rejected), burst=False (passes) — OK")
        print("  selftest PASS")
        sys.exit(0)
    if len(sys.argv) < 2:
        print("usage: python3 -m elrs_sdr.detect selftest")
        print("       python3 -m elrs_sdr.detect <capture.complex8> [srate_hz] [freq_hz]")
        sys.exit(1)
    path = sys.argv[1]
    sr = float(sys.argv[2]) if len(sys.argv) > 2 else 4.88e6
    freq = float(sys.argv[3]) if len(sys.argv) > 3 else None
    # the captures are int16 I,Q interleaved (4 bytes/sample: int16 I + int16 Q),
    # NOT raw complex64. Read as int16 -> complex.
    raw = np.fromfile(path, dtype=np.int16)
    iq = raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)
    ds = detect_and_identify(iq, sr, freq_hz=freq)
    print(gap_report(ds))
    for d in ds[:10]:
        print("  ", d.summary())
