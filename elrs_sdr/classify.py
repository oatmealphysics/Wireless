"""
Single-window protocol classifier — the shared brain for a live RF overlay.

WaveRaider's Inspect RF already probes each strong carrier with a short IQ
window and scores it for ELRS/LoRa (`waveraider/protocols/elrs.elrs_present`,
which calls `elrs_sdr.positivedet.lora_chirp`). This function generalises that:
run every per-window positive detector in `positivedet` on one IQ capture and
return the best *positive* claim, so a caller can annotate a carrier with a
protocol hint beyond ELRS — OFDM/Wi-Fi, DJI OcuSync-shape, analog FPV video.

Design matches the rest of the project:
  * positively identify, never default-bin — a protocol is claimed only if its
    own detector clears a threshold; otherwise the carrier is "unidentified".
  * honesty about validation — only the LoRa chirp detector is capture-validated
    (`confirmed=True`); the others are synthetic-calibrated (`confirmed=False`,
    surfaced as hints), and every claim carries its raw metric string.

This lives in elrs_sdr (not WaveRaider) on purpose: it's the validated DSP
layer WaveRaider already reuses, so a one-line call from a WaveRaider protocol
plugin picks it up without duplicating any detector.

FHSS is intentionally excluded here: `positivedet.fhss_hop` needs a time series
of peak frequencies across several dwells, not a single IQ window, so it belongs
to the scanner loop (`dronescan`), not this per-window entry point.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import positivedet

# Per-detector claim thresholds (mirror dronescan.DroneScanner). A detector must
# clear its threshold to make a positive claim; LoRa is the only confirmed one.
_LORA_CLAIM = 0.5
_OFDM_CLAIM = 0.6
_VIDEO_CLAIM = 0.6
_OCUSYNC_CLAIM = 0.6


@dataclass
class Classification:
    """One window's best positive protocol claim (or 'unidentified')."""
    protocol: str                       # human label
    signature: str                      # lora | ofdm | ocusync | video | unknown
    score: float                        # winning detector score 0..1
    confirmed: bool                     # True only for capture-validated (LoRa)
    metric: str                         # winning detector's raw metric string
    scores: dict = field(default_factory=dict)   # every detector's (score, metric)

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "signature": self.signature,
            "score": round(self.score, 3),
            "confirmed": self.confirmed,
            "metric": self.metric,
            "scores": {k: [round(v[0], 3), v[1]] for k, v in self.scores.items()},
        }


def classify_iq(iq, srate: float, bands: str | None = None,
                sf: int = 7, gfsk_uid: bytes | None = None) -> Classification:
    """Classify one complex IQ window by running every per-window detector.

    `bands` optionally restricts which detectors run to the ones that make sense
    for the carrier's band ("2.4", "5.8", "5ghz", "1.2", "sub1g", "433"); None
    runs them all. Returns the highest-scoring positive claim, or an
    'unidentified' Classification when nothing clears its threshold.

    If `gfsk_uid` is given, also run the FLRC/GFSK UID-sync detector
    (`elrs_sdr.core.gfsk`) against that link UID — this is what lets a live
    overlay tag an ELRS/FLRC (GFSK) link, which the LoRa-chirp detector alone
    cannot see (the GFSK sync word is UID-derived, so a UID is required). A gated
    sync match is a strong hint, reported `confirmed=False`: the GFSK demod is
    synthetic until a real capture validates it.
    """
    scores: dict = {}

    lora_s, lora_m = positivedet.lora_chirp(iq, srate, sf=sf)
    scores["lora"] = (lora_s, lora_m)

    # OFDM / DJI OcuSync are 2.4 / 5 GHz phenomena; video is 1.2 / 5.8 GHz.
    on_ofdm_band = bands in (None, "2.4", "5ghz", "5.8")
    on_video_band = bands in (None, "5.8", "1.2")

    if on_ofdm_band:
        ofdm_s, ofdm_m = positivedet.ofdm_preamble(iq, srate)
        scores["ofdm"] = (ofdm_s, ofdm_m)
        ocus_s, ocus_m = positivedet.ocusync_phy(iq, srate)
        scores["ocusync"] = (ocus_s, ocus_m)
    if on_video_band:
        vid_s, vid_m = positivedet.analog_video(iq, srate)
        scores["video"] = (vid_s, vid_m)

    # GFSK / FLRC (ELRS) — UID-keyed sync detection. The FLRC sync word is
    # UID-derived, so this only runs when a candidate link UID is supplied. A
    # gated sync hit (gfsk.detect_sync_word applies an absolute peak gate) is the
    # positive tell for an ELRS FLRC link that the LoRa chirp detector can't see.
    if gfsk_uid is not None:
        try:
            from .core import gfsk as _gfsk
            params = _gfsk.FlrcParams(float(srate))
            starts = _gfsk.detect_sync_word(iq, _gfsk.flrc_sync_word(gfsk_uid), params)
            if starts:
                scores["gfsk"] = (0.9, f"{len(starts)} UID-sync hit(s) @ {gfsk_uid.hex()}")
        except Exception:
            pass

    # Candidate positive claims: (score, signature, protocol label, confirmed).
    candidates = []
    if lora_s >= _LORA_CLAIM:
        candidates.append((lora_s, "lora",
                           "LoRa (ELRS / Meshtastic / sub-1G telemetry)", True))
    if scores.get("ofdm", (0,))[0] >= _OFDM_CLAIM:
        candidates.append((scores["ofdm"][0], "ofdm",
                           "OFDM / Wi-Fi-class (DJI / consumer / FPV-WiFi)", False))
    if scores.get("ocusync", (0,))[0] >= _OCUSYNC_CLAIM:
        candidates.append((scores["ocusync"][0], "ocusync",
                           "DJI OcuSync / Lightbridge (proprietary OFDM)", False))
    if scores.get("video", (0,))[0] >= _VIDEO_CLAIM:
        candidates.append((scores["video"][0], "video",
                           "Analog FPV video", False))
    if scores.get("gfsk", (0,))[0] >= 0.5:
        candidates.append((scores["gfsk"][0], "gfsk",
                           "ELRS / FLRC (GFSK, UID-sync match)", False))

    if not candidates:
        metric = " ".join(f"{k}={v[0]:.2f}" for k, v in scores.items())
        return Classification("Unidentified RF (no positive match)", "unknown",
                              0.0, False, metric, scores)

    # Best claimant; tie-break favours the confirmed (LoRa) detector.
    best = max(candidates, key=lambda c: (c[0], 1 if c[3] else 0))
    score, signature, protocol, confirmed = best
    metric = scores[signature][1]
    return Classification(protocol, signature, score, confirmed, metric, scores)
