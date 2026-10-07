"""ELRS (ExpressLRS) protocol plugin for WaveRaider.

This is the first instance of the per-protocol extension template: a decoder,
a classifier (frames -> Entities), and presentation, bundled as a
ProtocolPlugin. It is the template Crossfire / DSMX / FrSky will follow.

The numeric decode is NOT re-implemented here. It reuses the project's own
validated ELRS core (elrs_sdr.core: LoRa demod, FHSS LCG, CRC, OTA framing) —
the same code behind the standalone elrs_sdr CLI, tested against the upstream
ExpressLRS reference. This plugin is a thin adapter from that core into
WaveRaider's protocol-agnostic data model (Entity / Finding / Survey).

What it is / isn't (WaveRaider's honesty contract):
  * A decoded ELRS frame is a *control link*, not an aircraft. The link UID is
    a 6-byte *binding secret* between a specific radio and a specific module —
    it is NOT a manufacturer serial, NOT an IMEI, and NOT a hardware identity
    you can look up. Two different Tinyhawks can share a UID pattern; the same
    UID means "this exact radio paired to this exact module," nothing more.
  * `crc_valid=True` means the frame's CRC matched the link's init — strong
    evidence it's a genuine ELRS frame on this link, but the nonce is scanned
    offline (0..255) when no live counter is tracked, so a valid CRC on a
    non-sync frame is "a real ELRS frame," not "a frame from this instant."
  * Channel hops + RF rate are reported as observed, not as a model.
"""
from __future__ import annotations

import dataclasses
import time

from ...model import Entity, Finding, Survey
from ..base import ProtocolPlugin

# The validated ELRS core, shared with the standalone elrs_sdr CLI. elrs_sdr
# lives at the repo root, a sibling of the waveraider/ package. When WaveRaider
# is run from the waveraider/ subtree (python3 -m waveraider.cli), the repo root
# isn't on sys.path, so add it — computed from this file's location — BEFORE the
# import, or _CORE silently becomes False and decode_iq bails with "no module".
try:
    import sys as _sys
    from pathlib import Path as _Path
    # Walk up from this file to the repo root (the dir that holds elrs_sdr/),
    # instead of a hard-coded depth — immune to restructuring.
    for _p in _Path(__file__).resolve().parents:
        if (_p / "elrs_sdr").is_dir():
            if str(_p) not in _sys.path:
                _sys.path.insert(0, str(_p))
            break
    from elrs_sdr.core import lora, ota, fhss, crc, gfsk  # noqa: F401
    _CORE = True
    _CORE_ERR = ""
except Exception as e:  # pragma: no cover - core missing (e.g. partial checkout)
    _CORE = False
    _CORE_ERR = f"elrs_sdr.core unavailable: {e}"

# Default RF rate for an unknown link: SF7/800kHz (100/150 Hz), the most common.
DEFAULT_RATE = "150Hz"
# A link is "active" while its UID is being decoded; beyond this gap between
# decoded frames we mark it stale (provisional, not an operational alert).
STALE_AFTER_S = 10.0

# ELRS RF rate enum -> label (for presentation; mirrors the core's rate table).
_RATE_LABELS = {
    "500Hz": "500 Hz (SF5)", "333Hz_8ch": "333 Hz 8ch (SF5)", "250Hz": "250 Hz (SF6)",
    "150Hz": "150 Hz (SF7)", "100Hz_8ch": "100 Hz 8ch (SF7)", "50Hz": "50 Hz (SF8)",
}


def core_available() -> bool:
    return _CORE


def core_error() -> str:
    return _CORE_ERR


# ---------------------------------------------------------------------------
# IQ -> decoded ELRS packets (the glue the standalone CLI's _decode_samples does)
# ---------------------------------------------------------------------------

def dc_block(iq):
    """Remove the DC offset from an IQ array (subtract the mean of I and Q).

    The B205 capture path leaves a residual DC offset even with the hardware
    set_rx_dc_offset (see radio.py) — the 2026-10-07 bench capture showed a huge
    spike at 0 kHz that corrupts the GFSK/LoRa demod and the correlation
    detectors. Subtracting the per-band mean is the reliable software backstop.
    Applied at the sweep/decode entry so every downstream detector sees clean IQ.
    """
    import numpy as np
    iq = np.asarray(iq)
    if iq.size == 0:
        return iq
    return (iq - iq.mean()).astype(iq.dtype if np.iscomplexobj(iq) else iq.dtype)


def decode_iq(samples, srate: float, rate: str = DEFAULT_RATE,
              uid: bytes | None = None):
    """Run the ELRS RX pipeline over an IQ array and return decoded packets.

    Returns a list of dicts, one per CRC-valid frame:
      {"raw": bytes, "sample": int, "invert_iq": bool, "packet": ElrsPacket,
       "freq_hint": None}  (freq is attached by the caller, which knows the tune)

    `uid` is the 6-byte link UID (bytes) if known; else binding-mode init is
    tried (works for bind captures). The nonce is scanned 0..255 for non-sync
    frames (offline decode doesn't track the live counter).

    PHY: if `rate` is a FLRC/GFSK rate (name starts with "FLRC" or is one of the
    2.4 GHz GFSK indexes), decode with the GFSK branch. Otherwise LoRa. A
    `rate="auto"` sentinel tries BOTH (LoRa then GFSK) — the modulation-agnostic
    path used by the web UI so it doesn't need to assume the PHY.
    """
    if not _CORE:
        raise RuntimeError(_CORE_ERR)

    # --- GFSK branch (the FLRC 2.4 GHz rates the LoRa decoder can't see) ---
    is_gfsk = rate in ("auto",) or (isinstance(rate, str) and rate.upper().startswith("FLRC"))
    if is_gfsk:
        g_pkts = decode_gfsk(samples, srate, uid)
        if rate == "auto":
            # also try LoRa, merge (a link is either PHY, not both — the union is safe)
            try:
                l_pkts = _decode_lora(samples, srate, "150Hz", uid)
            except Exception:
                l_pkts = []
            merged = g_pkts + l_pkts
            merged.sort(key=lambda d: d["sample"])
            return merged
        return g_pkts

    return _decode_lora(samples, srate, rate, uid)


def _decode_lora(samples, srate: float, rate: str, uid: bytes | None = None) -> list[dict]:
    """The original LoRa-only decode (unchanged logic, extracted for the auto path)."""
    if rate not in lora.ELRS_2G4_RATES:
        raise ValueError(f"unknown ELRS rate {rate!r}; "
                         f"valid: {', '.join(lora.ELRS_2G4_RATES)}")
    r = lora.ELRS_2G4_RATES[rate]
    base = lora.LoRaParams(sf=r.sf, bw_hz=r.bw_hz, sample_rate=srate)

    # Detect candidate frame starts on both IQ polarities (real-RF phase unknown).
    starts = []
    for inv in (False, True):
        p = lora.LoRaParams(sf=r.sf, bw_hz=r.bw_hz, sample_rate=srate, invert_iq=inv)
        starts += [(s, inv) for s in lora.detect_sync_word(samples, p)]
    starts.sort()
    deduped = []
    for s, inv in starts:
        if not deduped or s - deduped[-1][0] > base.samples_per_symbol:
            deduped.append((s, inv))

    init = ota.ota_uid_crc_init(uid) if uid else ota.bind_crc_init()
    out = []
    for s, inv in deduped:
        p = lora.LoRaParams(sf=r.sf, bw_hz=r.bw_hz, sample_rate=srate, invert_iq=inv)
        raw = lora.demodulate_frame(samples, p, s, r)
        if raw is None:
            continue
        pkt = _try_crc(raw, uid, init)
        if pkt is None:
            continue
        out.append({"raw": raw, "sample": s, "invert_iq": inv, "packet": pkt})
    return out


def _try_crc(raw: bytes, uid: bytes | None, init: int):
    """Validate a raw frame against the link, scanning the nonce space."""
    ptype = raw[0] & 0x3
    for nonce in ([0] if ptype == ota.PACKET_TYPE_SYNC else range(256)):
        pkt = ota.decode(raw, uid, init, nonce)
        if pkt is not None:
            return pkt
    return None


def elrs_present(iq, srate: float, sf: int = 7) -> float:
    """Cheap PHY-presence hint for the power-sweep view: 0..1 likelihood that an
    IQ window at a strong carrier contains an ELRS/LoRa signal. Uses the
    capture-validated chirp detector (peak + symbol-periodic gates). This is a
    *reference hint*, not a detection — a high score means "ELRS-family activity
    likely," and the sweep tags it as such rather than claiming an identity."""
    if not _CORE:
        return 0.0
    try:
        from elrs_sdr import positivedet
        score, _ = positivedet.lora_chirp(iq, srate, sf=sf)
        return float(score)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# GFSK / FLRC branch + modulation-agnostic PHY classification
# --------------------------------------------------------------------------- #
# The target (Tinyhawk, rate_index=2) is on FLRC/GFSK, not LoRa — decode_iq above is
# LoRa-only and is BLIND to it. These functions add the GFSK branch so the WaveRaider
# UI can SEE a GFSK link, plus a classifier that, per IQ window, MEASURES which ELRS
# PHY (LoRa vs GFSK vs unknown) is actually transmitting — no rate/PHY assumed.
# This is the "find out what's there" front-end, wired into the web UI.

# The target's UID (from the CLI dump). The GFSK sync reference is derived from it.
# If the link is GFSK on a *different* UID, the GFSK branch scores low and the
# classifier still reports "unknown_energy" (the link is found, the UID is the next step).
DEFAULT_TARGET_UID = bytes.fromhex("00000011185b")


def decode_gfsk(iq, srate: float, uid: bytes | None = None) -> list[dict]:
    """Decode FLRC/GFSK ELRS frames (the rate_index 0-3 2.4 GHz rates the LoRa
    decoder can't see). Returns the same dict shape as decode_iq so the classifier
    is uniform. The sync word + CRC seed are derived from `uid` (default: the
    target's UID). The payload handed back is the 8-byte OTA frame; CRC-validity
    is checked by the caller via _try_crc (same as the LoRa branch)."""
    if not _CORE:
        return []
    uid = uid or DEFAULT_TARGET_UID
    params = gfsk.FlrcParams(sample_rate=srate)
    sw = gfsk.flrc_sync_word(uid)
    # DC-block before detection: the B205 residual DC offset corrupts the GFSK
    # correlation + demod (see dc_block docstring; the 2026-10-07 bench capture
    # showed a huge 0 kHz spike). Subtracting the mean is the reliable backstop.
    iq = dc_block(iq)
    starts = gfsk.detect_sync_word(iq, sw, params)
    if not starts:
        return []
    out = []
    for s in starts[:16]:
        payload = gfsk.demodulate_frame(iq, params, s)
        if payload is None or len(payload) < gfsk.FLRC_PAYLOAD_LEN:
            continue
        # validate the OTA CRC (binding check) — reuse the LoRa branch's _try_crc
        pkt = _try_crc(payload, uid, ota.ota_uid_crc_init(uid))
        if pkt is None:
            continue
        out.append({"raw": payload, "sample": s, "invert_iq": gfsk.FLRC_INVERT_IQ,
                    "packet": pkt})
    return out


def classify_phy(iq, srate: float, uid: bytes | None = None,
                 noise_floor: float | None = None) -> dict:
    """Modulation-agnostic classifier for ONE IQ window. MEASURES band power,
    LoRa chirp structure, and GFSK UID-sync match, then CLASSIFIES the PHY —
    no rate/PHY assumed. Returns {power_db, phy, lora_score, gfsk_score, ...}.
    phy in {none, lora, gfsk, unknown_energy}. This is the core of the
    "find out what's there" sweep (exposed to the web UI as /api/elrs)."""
    uid = uid or DEFAULT_TARGET_UID
    if not _CORE:
        return {"power_db": 0.0, "phy": "unknown", "lora_score": 0.0, "gfsk_score": 0.0,
                "error": _CORE_ERR}
    import numpy as np
    iq = np.asarray(iq, dtype=np.complex64)
    # DC-block before measuring: the B205 residual DC offset inflates the band
    # power and corrupts the correlation detectors (see dc_block docstring).
    iq = iq - iq.mean()
    # band power above the noise floor (default: 10th-percentile sample power)
    p = float(np.mean(np.abs(iq) ** 2))
    nf = noise_floor if (noise_floor and noise_floor > 0) else float(np.percentile(np.abs(iq) ** 2, 10)) + 1e-12
    power_db = 10.0 * np.log10(p / nf) if (p > 0 and nf > 0) else 0.0

    # LoRa presence: try the ELRS 2.4 GHz LoRa rates' sync correlation
    lora_score, lora_rate = 0.0, None
    try:
        for rate in lora.ELRS_2G4_RATES:
            lp = lora.LoRaParams(rate.sf, rate.bw_hz, sample_rate=srate)
            if lora.detect_sync_word(iq, lp, threshold=0.5):
                lora_score = min(1.0, lora_score + 0.5)
                lora_rate = lora_rate or rate
    except Exception:
        pass

    # GFSK presence: correlate the UID-derived sync word (absolute-gated in gfsk.py)
    gfsk_score, gfsk_starts = 0.0, []
    try:
        gp = gfsk.FlrcParams(sample_rate=srate)
        gfsk_starts = gfsk.detect_sync_word(iq, gfsk.flrc_sync_word(uid), gp)
        if gfsk_starts:
            gfsk_score = min(1.0, 0.6 + 0.05 * min(len(gfsk_starts), 8))
    except Exception:
        pass

    # Decide by which measured signal dominates. A STRONG PHY match (gfsk or lora score
    # >= 0.6) IS the detection — it overrides the crude band-power gate, because a
    # full-band GFSK signal has mean power ≈ its own noise floor (power_db ~ 0) yet is
    # unmistakably present. The power gate only matters when NEITHER PHY matched (to
    # reject noise as "unknown_energy" vs "none").
    if gfsk_score >= 0.6 and gfsk_score > lora_score:
        phy = "gfsk"
    elif lora_score >= 0.6 and lora_score >= gfsk_score:
        phy = "lora"
    elif power_db < 3.0:
        phy = "none"
    elif power_db >= 3.0:
        phy = "unknown_energy"
    else:
        phy = "none"

    return {"power_db": round(power_db, 2), "phy": phy,
            "lora_score": round(lora_score, 3), "lora_rate": lora_rate,
            "gfsk_score": round(gfsk_score, 3), "gfsk_sync_starts": gfsk_starts[:8]}


def sweep_phy(radius, srate: float = 10e6, gain: float = 40.0, dwell_ms: int = 200,
              channels: int = 80, freq0: float = 2400.4e6, spacing: float = 1.0e6,
              uid: bytes | None = None, output_dir: str | None = None,
              progress=None, detect: str = "window") -> dict:
    """Sweep the ISM2400 FHSS band and classify each channel's PHY (modulation-
    agnostic — no rate/PHY assumed). `radius` is an elrs_sdr.radio.RadioDevice.
    This is the backend for the web UI's /api/elrs route — it answers "what's
    there" from the RF.

    If `output_dir` is given, it ALSO saves the evidence for offline re-analysis:
      * one raw-IQ file per channel with energy:  <output_dir>/ch<N>_<MHz>MHz.complex8
        (SDR#/GQRtx format — the same as radio.capture(path=...), re-openable in any SDR tool)
      * a manifest:  <output_dir>/manifest.json  (params + the per-channel classification
        summary + which channels had energy + the file names)
    This is what you upload to hand the capture back for offline analysis.

    If `progress` is callable, it is invoked after each channel with
    {"done": i, "total": channels, "freq_mhz": f, "phy": <classification>} — used by
    the /api/elrs/progress SSE route to stream a live % to the UI.

    Returns {"channels": [...], "saved_dir": output_dir or None, "saved_files": N,
    "all_channels": [...]}.
    """
    import os, json, time
    import numpy as np
    uid = uid or DEFAULT_TARGET_UID
    # Keep the dwell SHORT: a GFSK/FLRC ELRS frame is ~1-2 ms, so 50 ms (500k samples
    # at 10 MS/s) comfortably captures several frames per channel, and 80 ch x 50 ms
    # = ~4 s total. A 200 ms dwell (2M samples) flooded the terminal with capture
    # progress logs and hit USB bandwidth limits (sustained rate dropped to 5-10 MS/s,
    # which also corrupts the timing the GFSK demod relies on). log_every=1<<30
    # silences the per-chunk "captured X/Y @ Z MS/s" progress print entirely.
    n_dwell = int(srate * dwell_ms / 1000.0)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    # SINGLE interleaved loop: tune -> capture -> classify -> report, per channel.
    # (The previous two-phase structure — capture ALL channels first, then classify
    # — held the UI's progress bar at 0% for the entire multi-second capture phase,
    # then jumped to ~100% in a flash when classification ran. Classifying each
    # window immediately after its capture keeps the live % honest: it tracks
    # channels actually finished out of the total requested, so even a failed
    # capture advances the bar instead of stalling it.)
    raw = []
    results = []
    last_nf = 0.0
    for c in range(channels):
        f = freq0 + c * spacing
        # real RadioDevice API: tune(freq_hz, sample_rate, gain, antenna=None),
        # then capture(n_samples, path=...) -> a single np.complex64 array of that
        # length. Pass a per-channel path when saving so the raw IQ is written to
        # disk as a .complex8 file (SDR#/GQRtx) alongside the in-memory array.
        radius.tune(f, srate, gain)
        ch_path = None
        if output_dir:
            ch_path = os.path.join(output_dir, f"ch{c}_{f/1e6:.1f}MHz.complex8")
        try:
            samples = radius.capture(n_dwell, path=ch_path, log_every=1 << 30)
        except Exception:
            samples = None
        # classify immediately (per-window noise floor when the running floor
        # isn't usable yet — same 10th-percentile rule the two-phase code applied)
        if samples is not None and len(samples) > 0:
            raw.append((c, f, samples, ch_path))
            _p = float(np.mean(np.abs(samples) ** 2))
            _nf = float(np.percentile(np.abs(samples) ** 2, 10)) + 1e-12
            last_nf = _nf
            r = classify_phy(samples, srate, uid=uid,
                             noise_floor=(_nf if _nf > 0 and _p > 0 else None))
            if detect == "burst":
                r = _augment_with_burst(r, samples, srate, uid)
        else:
            r = None
        r = r if r is not None else {"power_db": 0.0, "phy": "none",
                                     "lora_score": 0.0, "lora_rate": None,
                                     "gfsk_score": 0.0, "gfsk_sync_starts": []}
        r["channel"] = c
        r["freq_mhz"] = round(f / 1e6, 2)
        r["n_samples"] = int(len(samples)) if samples is not None else 0
        if ch_path:
            r["iq_file"] = os.path.basename(ch_path)
        results.append(r)   # keep ALL channels in the manifest (energy + none), not just non-none
        if progress:
            try:
                progress({"done": c + 1, "total": channels, "freq_mhz": round(f / 1e6, 2),
                          "phy": r["phy"], "channel": c})
            except Exception:
                pass   # a progress-reporting hiccup must never abort the sweep
    # summary: only the channels with energy (what the UI table shows)
    energy = [r for r in results if r["phy"] != "none"]
    saved_files = 0
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        manifest = {
            "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "params": {"srate": srate, "gain": gain, "dwell_ms": dwell_ms,
                       "channels": channels, "freq0_hz": freq0, "spacing_hz": spacing,
                       "uid": (uid.hex() if uid else None),
                       "noise_floor": round(last_nf, 3)},
            "channels_with_energy": energy,
            "all_channels": results,
        }
        with open(os.path.join(output_dir, "manifest.json"), "w") as fh:
            json.dump(manifest, fh, indent=2)
        saved_files = sum(1 for _, _, _, p in raw if p and os.path.exists(p))
    return {"channels": energy, "saved_dir": (output_dir if output_dir else None),
            "saved_files": saved_files, "all_channels": results}


# ---------------------------------------------------------------------------
# Scan strategies + burst detection (added for redundancy — see the ANDEDS/CLAUDE
# handoff notes). Rationale: ELRS rate_index=2 is a ~500 Hz link that FHSS-hops
# across 80 channels, so a sweep that visits each channel ONCE for ~50 ms rarely
# overlaps a brief (~1-2 ms) FLRC burst, and even a catch is buried in continuous
# Wi-Fi. These give two more angles: (a) a long dwell on the sync channel (where
# ELRS sends periodic sync packets), and (b) burst-aware detection (per-slice, so
# a brief burst isn't averaged away). "detect=burst" and sync_dwell_phy are
# OPTIONS; the default sweep is unchanged.
# ---------------------------------------------------------------------------
FHSS_SYNC_CHANNEL = 40                      # ch40 of the 80-ch ISM2400 map
FHSS_SYNC_FREQ = 2400.4e6 + 40 * 1.0e6      # = 2440.4 MHz (ELRS sync channel)


def _augment_with_burst(r: dict, samples, srate: float, uid: bytes) -> dict:
    """Overlay burst-aware GFSK/LoRa detection onto a whole-window classify_phy
    result: keep its power/energy fields, but if a per-slice burst scan finds a
    GFSK UID-sync or LoRa chirp the whole-window pass missed, promote the phy."""
    try:
        from elrs_sdr import classify as _cls
        bc = _cls.classify_iq_burst(samples, srate, gfsk_uid=uid)
    except Exception:
        return r
    if bc.signature in ("gfsk", "lora"):
        r["phy"] = bc.signature
        key = bc.signature + "_score"
        r[key] = max(float(r.get(key, 0.0)), round(bc.score, 3))
        r["burst_metric"] = bc.metric
    return r


def sync_dwell_phy(radius, srate: float = 10e6, gain: float = 40.0,
                   dwell_s: float = 1.0, freq_hz: float = FHSS_SYNC_FREQ,
                   uid: bytes | None = None, output_dir: str | None = None) -> dict:
    """SECONDARY scan strategy: long-dwell a SINGLE channel (default the ELRS sync
    channel, ch40 = 2440.4 MHz) and burst-detect across the whole dwell. A long
    dwell catches multiple FHSS hops / the periodic sync packets that a
    once-per-channel sweep misses. Returns the same per-channel dict shape as
    sweep_phy (one entry), under {"channels", "strategy", "all_channels", ...}."""
    import os, json, time
    import numpy as np
    uid = uid or DEFAULT_TARGET_UID
    radius.tune(float(freq_hz), srate, gain)
    ch_path = None
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        ch_path = os.path.join(output_dir, f"sync_{freq_hz/1e6:.1f}MHz_dwell{dwell_s:g}s.complex8")
    n = int(srate * dwell_s)
    try:
        samples = radius.capture(n, path=ch_path, log_every=1 << 30)
    except Exception:
        samples = None
    if samples is None or len(samples) == 0:
        r = {"power_db": 0.0, "phy": "none", "lora_score": 0.0, "lora_rate": None,
             "gfsk_score": 0.0, "gfsk_sync_starts": []}
    else:
        _p = float(np.mean(np.abs(samples) ** 2))
        _nf = float(np.percentile(np.abs(samples) ** 2, 10)) + 1e-12
        r = classify_phy(samples, srate, uid=uid,
                         noise_floor=(_nf if _nf > 0 and _p > 0 else None))
        r = _augment_with_burst(r, samples, srate, uid)
    r["channel"] = FHSS_SYNC_CHANNEL
    r["freq_mhz"] = round(freq_hz / 1e6, 2)
    r["n_samples"] = int(len(samples)) if samples is not None else 0
    if ch_path:
        r["iq_file"] = os.path.basename(ch_path)
    energy = [r] if r["phy"] != "none" else []
    if output_dir:
        manifest = {"captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "params": {"strategy": "sync_dwell", "srate": srate, "gain": gain,
                               "dwell_s": dwell_s, "freq_hz": freq_hz,
                               "uid": (uid.hex() if uid else None)},
                    "channels_with_energy": energy, "all_channels": [r]}
        with open(os.path.join(output_dir, "manifest.json"), "w") as fh:
            json.dump(manifest, fh, indent=2)
    return {"strategy": "sync_dwell", "channels": energy, "all_channels": [r],
            "saved_dir": (output_dir if output_dir else None)}


def run_scan_matrix(radius, srate: float = 10e6, gain: float = 40.0,
                    uid: bytes | None = None, output_root: str | None = None,
                    channels: int = 80, sweep_dwell_ms: int = 50,
                    sync_dwell_s: float = 1.0, progress=None) -> dict:
    """Cycle through scan x detect strategies for redundancy / cross-checking.
    Runs, in order, three independent passes (a link one misses, another may
    catch):
      1. wideband sweep + window detect   (the original behaviour)
      2. wideband sweep + burst detect    (per-slice, catches brief FLRC bursts)
      3. sync-channel long-dwell + burst  (catches the periodic sync packets)
    Returns {"strategies": [{name, result}, ...]}. Each pass can save under its
    own subdir of `output_root`."""
    import os
    uid = uid or DEFAULT_TARGET_UID

    def _od(tag):
        return os.path.join(output_root, tag) if output_root else None

    strategies = []
    strategies.append({"name": "sweep/window", "result": sweep_phy(
        radius, srate=srate, gain=gain, dwell_ms=sweep_dwell_ms, channels=channels,
        uid=uid, output_dir=_od("sweep_window"), progress=progress, detect="window")})
    strategies.append({"name": "sweep/burst", "result": sweep_phy(
        radius, srate=srate, gain=gain, dwell_ms=sweep_dwell_ms, channels=channels,
        uid=uid, output_dir=_od("sweep_burst"), progress=progress, detect="burst")})
    strategies.append({"name": "sync_dwell/burst", "result": sync_dwell_phy(
        radius, srate=srate, gain=gain, dwell_s=sync_dwell_s, uid=uid,
        output_dir=_od("sync_dwell"))})
    return {"strategies": strategies}



# ---------------------------------------------------------------------------
# Classifier: decoded frames -> Entities (keyed by link UID)
# ---------------------------------------------------------------------------

class ElrsClassifier:
    """ClassifierLike: update(pkt) per decoded frame; rebuild_findings() at the end.

    A `pkt` here is either:
      * a dict from decode_iq()  -> {"packet": ElrsPacket, "sample":.., ...}, or
      * an ElrsPacket directly (when a source yields packets).
    An optional `freq_hz` kwarg tags the carrier the frame was heard on.
    """

    def __init__(self, path: str = "<live>") -> None:
        self.survey = Survey(path=path)
        self._now = time.time

    @staticmethod
    def _as_packet(p):
        """Normalize a decoded frame to something the classifier can read with
        getattr(). A live frame is a real ota.ElrsPacket; a recorded dump is a
        to_dict() dict. Wrap a dict in a tiny shim exposing the same attributes so
        the rest of update() is uniform."""
        if p is None or not isinstance(p, dict):
            return p
        class _P:
            pass
        o = _P()
        o.crc_valid = bool(p.get("crc_valid", False))
        o.raw = bytes.fromhex(p.get("raw_hex", "")) if p.get("raw_hex") else b""
        o.uid = p.get("uid")
        o.is_armed = bool(p.get("is_armed", False))
        o.channels = p.get("channels", [])
        # sync: to_dict() nests it as a dict with fhss_index / rf_rate
        sync = p.get("sync")
        if isinstance(sync, dict):
            class _S:
                def __init__(self, d):
                    for k, v in d.items():
                        setattr(self, k, v)
            o.sync = _S(sync)
        else:
            o.sync = sync
        return o

    @classmethod
    def _packet_and_ctx(cls, pkt, freq_hz=None):
        if isinstance(pkt, dict):
            p = cls._as_packet(pkt.get("packet"))
            sample = pkt.get("sample")
            inv = pkt.get("invert_iq")
            freq = freq_hz or pkt.get("freq_hz")
        else:
            p, sample, inv, freq = pkt, None, None, freq_hz
        return p, sample, inv, freq

    def update(self, pkt, elapsed: float | None = None, freq_hz: float | None = None) -> None:
        p, sample, inv, freq = self._packet_and_ctx(pkt, freq_hz)
        self.survey.total_frames += 1
        if p is None or not getattr(p, "crc_valid", False):
            self._diagnose("frame not CRC-valid")
            return

        uid = getattr(p, "uid", None)
        if uid:
            key = "elrs:" + bytes(uid).hex()
            label = bytes(uid).hex()
        else:
            # Binding-mode / unknown-UID frame: key by raw so it still aggregates.
            key = "elrs:uid?" + bytes(p.raw).hex()
            label = "UID not resolved"

        now = round(elapsed if elapsed is not None else self._now(), 6)
        e = self.survey.entities.get(key) or Entity(id=key, kind="elrs_link")
        e.observations += 1
        e.label = label
        a = e.attrs
        a.setdefault("first_seen", now)
        a["last_seen"] = now
        if freq:
            a["freq_hz"] = int(freq)
        # Accumulate observed channel hops (from the sync FHSS index when present).
        if getattr(p, "sync", None) is not None:
            try:
                idx = int(dataclasses.asdict(p.sync).get("fhss_index", -1))
            except Exception:
                idx = -1
            if idx >= 0:
                hops = a.setdefault("fhss_hops", [])
                if not hops or hops[-1] != idx:
                    hops.append(idx)
                    if len(hops) > 64:
                        a["fhss_hops"] = hops[-64:]
        # RF rate, if recoverable from the sync word.
        rate_lbl = self._rate_label(p)
        if rate_lbl:
            a["rf_rate"] = rate_lbl
        if getattr(p, "is_armed", False):
            a["armed_seen"] = True
        if getattr(p, "channels", None):
            a["last_channels"] = list(p.channels)
        self.survey.entities[key] = e

    def _diagnose(self, problem: str) -> None:
        d = self.survey.diagnostics
        d[problem] = d.get(problem, 0) + 1

    @staticmethod
    def _rate_label(p) -> str | None:
        try:
            d = dataclasses.asdict(p.sync)
            enum = d.get("rf_rate")
            if enum is None:
                return None
            for name, meta in lora.ELRS_2G4_RATES.items():
                if getattr(meta, "rf_rate", None) == enum:
                    return _RATE_LABELS.get(name, name)
        except Exception:
            pass
        return None

    def rebuild_findings(self) -> None:
        out = []
        for e in self.survey.of_kind("elrs_link"):
            a = e.attrs
            hops = a.get("fhss_hops", [])
            rate = a.get("rf_rate", "rate not resolved")
            freq = a.get("freq_hz")
            fstr = f" at {freq/1e6:.3f} MHz" if freq else ""
            nch = len(hops)
            summary = (f"ELRS control link: UID {e.label}{fstr}, {rate}, "
                       f"{e.observations} frames, {nch} channel hop(s) observed")
            out.append(Finding("elrs_link", "info", summary,
                               {"entity": e.id, "uid": e.label,
                                "observations": e.observations,
                                "note": "ELRS link, not an aircraft identity"}))
            if a.get("armed_seen"):
                out.append(Finding("elrs_armed", "warning",
                                   f"ELRS link {e.label} reported an armed state "
                                   "(radio-side flag, not a takeoff/flight confirmation)",
                                   {"entity": e.id}))
        self.survey.findings = out


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class ElrsPlugin(ProtocolPlugin):
    name = "elrs"
    primary_kinds = ("elrs_link",)
    # "pcap" => engine uses source.frames(). An ELRS source yields decoded frame
    # dicts (live SDR or a recorded capture) — not raw pcap, but frames() is the
    # right seam: the classifier's update() takes each decoded frame.
    input_kind = "pcap"
    implemented = _CORE
    requires = _CORE_ERR or ""
    category = "UAS"
    label = "ExpressLRS"
    sample = None
    blurb = ("ELRS (ExpressLRS) 2.4/868/915 LoRa control links — the protocol most "
             "modern FPV drones (incl. Tinyhawk) use. Decodes the link, not an "
             "aircraft: the UID is a radio<->module binding secret, not a serial.")
    knowledge = {
        "what": ("An ExpressLRS control-link frame (LoRa-modulated, FHSS-hopping "
                 "across the band). Decoded via the project's validated ELRS core."),
        "identity": ("The link UID is a 6-byte binding secret between one radio and one "
                     "module. It is NOT a manufacturer serial, IMEI, or hardware ID. "
                     "It identifies a *pairing*, not an aircraft."),
        "limits": ("CRC-valid means a genuine ELRS frame on this link; the nonce is "
                   "scanned offline when no live counter is tracked. Channel hops and "
                   "RF rate are as observed. No position, speed, or identity is "
                   "inferred."),
        "stale_after_s": STALE_AFTER_S,
    }

    def new_classifier(self) -> ElrsClassifier:
        return ElrsClassifier()

    # ---- presentation ----------------------------------------------------
    def columns(self) -> list[str]:
        return ["UID", "Freq (MHz)", "RF rate", "Hops", "Frames", "Armed", "Seen"]

    def row(self, e: Entity, alerts: int) -> list[str]:
        a = e.attrs
        freq = a.get("freq_hz")
        return [
            e.label or "-",
            f"{freq/1e6:.3f}" if freq else "-",
            a.get("rf_rate", "-"),
            str(len(a.get("fhss_hops", []))),
            str(e.observations),
            "yes" if a.get("armed_seen") else "-",
            str(round(a.get("last_seen", 0), 3)),
        ]

    def describe(self, e: Entity, survey: Survey) -> str:
        a = e.attrs
        freq = a.get("freq_hz")
        parts = [f"ELRS link {e.label}"]
        if freq:
            parts.append(f"@ {freq/1e6:.3f} MHz")
        if a.get("rf_rate"):
            parts.append(a["rf_rate"])
        parts.append(f"{e.observations} frames, {len(a.get('fhss_hops', []))} hops")
        return " ".join(parts)

    def detail(self, survey: Survey, e: Entity) -> str:
        a = e.attrs
        lines = [f"ID     {e.id}", f"Kind   {e.kind}", f"UID    {e.label or '-'}",
                 f"Frames {e.observations}"]
        if a.get("freq_hz"):
            lines.append(f"Freq   {a['freq_hz']/1e6:.3f} MHz")
        if a.get("rf_rate"):
            lines.append(f"Rate   {a['rf_rate']}")
        hops = a.get("fhss_hops", [])
        if hops:
            lines.append(f"Hops   {len(hops)} observed: {hops[:16]}{'…' if len(hops) > 16 else ''}")
        if a.get("armed_seen"):
            lines.append("Armed  yes (radio-side flag)")
        lines += ["", f"FINDINGS ({len([f for f in survey.findings if f.evidence.get('entity') == e.id])})"]
        fs = [f for f in survey.findings if f.evidence.get("entity") == e.id]
        lines += [f"  {f.summary}" for f in fs] or ["  none"]
        return "\n".join(lines)
