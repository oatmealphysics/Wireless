"""Drone / UAS RF signature knowledge base.

A reference of the control, telemetry, and video links drones use, so a signal
found in a spectrum survey can be matched to the protocols that live on that
frequency — and the operator knows which are decodable, which are only
detectable by signature, and which are opaque.

Detection tiers:
  1 = detectable AND passively exploitable (decode content or position metadata)
  2 = detectable by band/bandwidth/hop signature, presence-only (no passive decode)
  3 = effectively opaque in a spectrum survey (needs higher-layer, e.g. 802.11)

RF parameters are from public/reverse-engineering sources; several are community
reverse-engineering, not vendor specs (noted per entry). Channel plans are FCC
region. This informs recognition — it is not a guarantee of identification.
"""
from __future__ import annotations

# (name, kind, [ (lo_mhz, hi_mhz), ... ], modulation, occupied_bw, hop, tier, note)
SIGNATURES = [
    # ---- control / telemetry links ----
    ("ExpressLRS 2.4", "control", [(2400.4, 2479.4)],
     "LoRa CSS / FLRC, FHSS (80 ch)", "203 kHz–1.6 MHz",
     "FHSS up to per-packet; 50–1000 Hz", 1,
     "Open-source, plaintext payload; hop sequence derivable from binding phrase/UID."),
    ("ExpressLRS 900", "control", [(902.0, 928.0), (863.0, 870.0)],
     "LoRa CSS / FSK, FHSS (40 ch @915)", "125–500 kHz",
     "FHSS; 25–1000 Hz", 1,
     "Open-source, plaintext; FCC915 = 40 ch over ~23.4 MHz centered 915."),
    ("TBS Crossfire", "control", [(850.0, 870.0), (902.0, 928.0)],
     "LoRa CSS (SX1272) [reverse-eng], FHSS", "125–500 kHz [unverified]",
     "FHSS, adaptive 150→50 Hz", 2,
     "Proprietary, optional telemetry encryption; presence-only."),
    ("TBS Tracer", "control", [(2400.0, 2483.5)],
     "proprietary FHSS [unverified]", "[unverified]",
     "FHSS, fixed 250 Hz", 2, "Proprietary; presence-only."),
    ("FrSky ACCST/ACCESS 2.4", "control", [(2400.0, 2483.5)],
     "GFSK on CC2500 (narrowband, NOT LoRa)", "~500 kHz",
     "FHSS ~47 ch [reverse-eng], ~111 Hz", 2,
     "Narrowband GFSK — a discriminator vs ELRS/Tracer; decode feasible, no turnkey tool."),
    ("FrSky R9 900", "control", [(902.0, 928.0), (863.0, 870.0)],
     "LoRa CSS (SX1276), FHSS", "125–500 kHz [unverified]",
     "self-healing FHSS", 2, "Proprietary; presence-only."),
    ("Spektrum DSMX/DSM2", "control", [(2400.0, 2483.5)],
     "GFSK 1 Mbit/s in DSSS (wideband)", "~1.5 MHz [unverified]",
     "DSMX = FHSS 23/74; DSM2 = 2 fixed ch; 45/91 Hz", 1,
     "Well reverse-engineered; GNU Radio receiver demods + follows hop sequence."),
    ("Flysky AFHDS2A", "control", [(2405.5, 2475.0)],
     "GFSK 500 kbit/s on A7105 (narrowband)", "~500 kHz",
     "FHSS 16-ch list, ~260 Hz", 2,
     "Decodable/hop-followable (DIY-Multiprotocol); bind ch 0x0D/0x8C."),
    ("Futaba FASST/S-FHSS", "control", [(2400.0, 2483.5)],
     "S-FHSS = 2-FSK on CC2500; FASST = FH/DSSS [opaque]", "~1.5 MHz ch [S-FHSS]",
     "S-FHSS 30 ch, ~147 hops/s; FASST unknown", 2,
     "S-FHSS decodable (28 hop codes); FASST/FASSTest not reverse-engineered."),
    # ---- video / FPV links ----
    ("Analog FPV 5.8", "video", [(5645.0, 5945.0)],
     "wideband analog FM of composite video", "~20–30 MHz occupied",
     "FIXED channel (no hop)", 1,
     "Trivially demodulable to live video; 40-ch raster (raceband R1..R8 5658–5917)."),
    ("Analog FPV 1.2/1.3", "video", [(1080.0, 1360.0)],
     "wideband analog FM", "~20 MHz occupied",
     "FIXED channel", 1,
     "Demodulable to video; near GPS L1 / DME — legality region-specific."),
    ("DJI OcuSync / DroneID", "video", [(2400.0, 2483.5), (5725.0, 5850.0)],
     "OFDM, adaptive; video encrypted", "10 / 20 / 40 MHz",
     "hops many times/sec (DFS)", 1,
     "Video opaque, BUT DroneID broadcast (9-sym OFDM burst @15.36 MHz, ~600 ms) "
     "decodes drone + OPERATOR GPS on O2/O3; encrypted on O4+."),
    ("Walksnail Avatar HD", "video", [(5660.0, 5914.0)],
     "OFDM [inferred], encrypted/scrambled", "~20 / 40 MHz",
     "FIXED channel", 2,
     "Presence/classify only; no passive content decode."),
    ("HDZero", "video", [(5658.0, 5917.0)],
     "custom OFDM + JSCC", "~27 MHz",
     "FIXED channel (raceband grid)", 2,
     "~27 MHz digital block on a raceband center is distinctive; no passive decode."),
    # ---- consumer / multi-band ----
    ("Wi-Fi / toy drone", "consumer", [(2400.0, 2483.5), (5150.0, 5895.0)],
     "802.11 (the drone IS a Wi-Fi AP)", "20/40 MHz Wi-Fi",
     "Wi-Fi channels", 3,
     "Invisible as a 'drone' at RF — pull out via 802.11 SSID/OUI (use the Wi-Fi plugin)."),
    ("Autel SkyLink", "consumer", [(902.0, 928.0), (2400.0, 2483.5),
                                   (5150.0, 5250.0), (5725.0, 5850.0)],
     "proprietary, multi-band adaptive FHSS [unverified]", "[unverified]",
     "hops + switches bands", 2,
     "These bands are shared with other emitters, including Wi-Fi at 5.2 GHz; "
     "band activity alone cannot identify SkyLink. No passive decode here."),
]

_TIER_LABEL = {1: "decodable", 2: "signature-only", 3: "opaque (802.11)"}

# Occupied-bandwidth range (kHz) per link, for the classifier. Ranges are the
# research figures (several reverse-engineered/estimated) — used to rank, not prove.
_BW_KHZ = {
    "ExpressLRS 2.4": (203, 1625), "ExpressLRS 900": (125, 500),
    "TBS Crossfire": (125, 500), "TBS Tracer": (200, 1625),
    "FrSky ACCST/ACCESS 2.4": (300, 700), "FrSky R9 900": (125, 500),
    "Spektrum DSMX/DSM2": (1000, 1800), "Flysky AFHDS2A": (300, 700),
    "Futaba FASST/S-FHSS": (300, 1800),
    "Analog FPV 5.8": (18000, 30000), "Analog FPV 1.2/1.3": (15000, 25000),
    "DJI OcuSync / DroneID": (8000, 42000), "Walksnail Avatar HD": (18000, 42000),
    "HDZero": (24000, 30000), "Wi-Fi / toy drone": (18000, 42000),
    "Autel SkyLink": (1000, 42000),
}


def classify(freq_mhz: float, bw_khz: float | None = None) -> list[dict]:
    """Rank reference profiles by band/width compatibility, NOT drone likelihood.

    Ordinary Wi-Fi, Bluetooth and other emitters can match the same profiles.
    A tier describes a protocol's capabilities, not observed detection evidence.
    No identity or presence claim may be made from this list alone.
    """
    cands = candidates_at(freq_mhz)
    if bw_khz:
        for c in cands:
            rng = _BW_KHZ.get(c["name"])
            c["bw_fit"] = bool(rng and rng[0] * 0.5 <= bw_khz <= rng[1] * 1.7)
        cands.sort(key=lambda c: (not c.get("bw_fit"), c["tier"]))
    return cands


def _fields(sig):
    keys = ("name", "kind", "bands_mhz", "modulation", "bandwidth", "hop", "tier", "note")
    return dict(zip(keys, sig))


def catalog() -> list[dict]:
    """The whole knowledge base, as dicts (for a reference view)."""
    out = []
    for s in SIGNATURES:
        d = _fields(s)
        d["bands_mhz"] = [[lo, hi] for lo, hi in d["bands_mhz"]]
        d["tier_label"] = _TIER_LABEL[d["tier"]]
        out.append(d)
    return out


def candidates_at(freq_mhz: float) -> list[dict]:
    """Drone protocols whose band covers `freq_mhz`, most-exploitable first."""
    hits = []
    for s in SIGNATURES:
        d = _fields(s)
        if any(lo <= freq_mhz <= hi for lo, hi in d["bands_mhz"]):
            hits.append({"name": d["name"], "kind": d["kind"], "tier": d["tier"],
                         "tier_label": _TIER_LABEL[d["tier"]], "modulation": d["modulation"],
                         "note": d["note"]})
    hits.sort(key=lambda h: h["tier"])
    return hits


def in_drone_band(freq_mhz: float) -> bool:
    return any(candidates_at(freq_mhz))
