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

# The validated ELRS core, shared with the standalone elrs_sdr CLI.
try:
    from elrs_sdr.core import lora, ota, fhss, crc  # noqa: F401
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

def decode_iq(samples, srate: float, rate: str = DEFAULT_RATE,
              uid: bytes | None = None):
    """Run the ELRS RX pipeline over an IQ array and return decoded packets.

    Returns a list of dicts, one per CRC-valid frame:
      {"raw": bytes, "sample": int, "invert_iq": bool, "packet": ElrsPacket,
       "freq_hint": None}  (freq is attached by the caller, which knows the tune)

    `uid` is the 6-byte link UID (bytes) if known; else binding-mode init is
    tried (works for bind captures). The nonce is scanned 0..255 for non-sync
    frames (offline decode doesn't track the live counter).
    """
    if not _CORE:
        raise RuntimeError(_CORE_ERR)
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
