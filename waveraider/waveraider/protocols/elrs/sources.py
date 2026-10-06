"""ELRS capture sources for WaveRaider.

An ELRS "source" yields *decoded* frame dicts (from protocols.elrs.decode_iq),
not raw bytes — the ElrsClassifier consumes decoded frames directly. Two kinds:

  * ElrsSdrSource  — live: stream IQ from the SDR (UHD/B205 via uhd_backend),
    window it, decode each window, yield frames. This is the "catch the Tinyhawk
    live" path. Honors the SDR device lock + lease so it never fights Inspect RF.
  * ElrsFileSource — recorded: decode a saved .complex8 IQ capture (the format
    the standalone elrs_sdr tool writes) or a text dump of decoded frames.

Both plug into WaveRaider's existing get_source()/survey_source() machinery:
the engine calls source.frames() (input_kind "pcap") and feeds each dict to the
classifier. No CLI or engine changes needed.
"""
from __future__ import annotations

import time

from ...sources import Source
from . import decode_iq, DEFAULT_RATE


# A decode window: how many seconds of IQ to grab before running the LoRa/FHSS/CRC
# pipeline. ELRS at SF7/800kHz is ~1.2 MSPS; a 250 ms window is long enough to
# catch several frames but short enough to stay responsive.
WINDOW_S = 0.25
# Sample rate for the ELRS capture. 800 kHz LoRa needs a few MSPS to demod cleanly.
ELRS_SRATE = 2_400_000
ELRS_GAIN_DB = 30.0


class ElrsSdrSource(Source):
    """Live ELRS decode from the SDR. `device` selects the radio (usb_id or None
    for the first live-capable one). Runs for `secs` (default: until frames stop
    arriving — a quiet period), yielding decoded frame dicts as they're found."""
    name = "elrs_sdr"

    def __init__(self, device: str | None = None, freq_hz: float | None = None,
                 rate: str = DEFAULT_RATE, secs: float = 10.0,
                 uid: bytes | None = None) -> None:
        self.device = device
        self.freq_hz = freq_hz        # None = sweep the ISM2G4 ELRS band (see _freqs)
        self.rate = rate
        self.secs = secs
        self.uid = uid
        self.iface = f"sdr:{device or 'auto'}"
        self.path = None

    def _freqs(self):
        """If a single freq was given, use it. Else a coarse set across ISM2G4 so a
        hopping ELRS link is caught on whatever channel it's on right now."""
        if self.freq_hz:
            return [float(self.freq_hz)]
        # ELRS 2.4 GHz: 80 channels 2400.4–2479.4 MHz. A full per-channel sweep per
        # window is slow; use a coarse grid for live detection and refine on demand.
        return [2400_400_000 + i * 2_000_000 for i in range(40)]  # 40 MHz-spaced

    def frames(self):
        from ... import uhd_backend  # lazy: only imported when a live SDR is used
        if not uhd_backend.available():
            raise RuntimeError(
                "no live SDR available for ELRS decode — connect an Ettus USRP "
                "(or set SoapySDR up). See `sdr` readiness.")
        end = time.monotonic() + self.secs
        freqs = self._freqs()
        while time.monotonic() < end:
            for f in freqs:
                try:
                    iq, rate = uhd_backend.capture_iq(f, ELRS_SRATE, int(ELRS_SRATE * WINDOW_S))
                except Exception:
                    continue
                if iq is None or len(iq) < 1024:
                    continue
                for rec in decode_iq(iq, rate, self.rate, uid=self.uid):
                    rec["freq_hz"] = int(f)
                    yield rec


class ElrsFileSource(Source):
    """Decode a recorded ELRS capture. Accepts:
      * a .complex8 / .iq / .bin IQ capture (int16 or fc32) -> decode_iq
      * a text file of decoded frames (one JSON/dict line each) -> yielded as-is
    """
    name = "elrs_file"

    def __init__(self, path: str, freq_hz: float | None = None,
                 rate: str = DEFAULT_RATE, srate: float = ELRS_SRATE,
                 uid: bytes | None = None) -> None:
        self.path = path
        self.freq_hz = freq_hz
        self.rate = rate
        self.srate = srate
        self.uid = uid

    def _is_text(self) -> bool:
        try:
            with open(self.path, "rb") as f:
                head = f.read(4)
            return bool(head) and head[0] in (0x7b, 0x22, 0x28)  # '{', '"', '('  (not IQ bytes)
        except OSError:
            return False

    def frames(self):
        from . import decode_iq  # noqa: F401  (ensures core loaded)
        from elrs_sdr.core import ota as _ota
        if self._is_text():
            # A dump of already-decoded frames (JSON lines, each with a raw hex or
            # a to_dict()). Reconstruct a real ota.ElrsPacket from the raw bytes so
            # the classifier sees the same object type as the live path.
            import json
            init = _ota.ota_uid_crc_init(self.uid) if self.uid else _ota.bind_crc_init()
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    raw = None
                    if d.get("raw"):            # hex string
                        raw = bytes.fromhex(d["raw"])
                    elif d.get("raw_hex"):       # to_dict() form
                        raw = bytes.fromhex(d["raw_hex"])
                    if raw is None:
                        continue
                    ptype = raw[0] & 3
                    pkt = None
                    for n in ([0] if ptype == _ota.PACKET_TYPE_SYNC else range(256)):
                        pkt = _ota.decode(raw, self.uid, init, n)
                        if pkt is not None:
                            break
                    if pkt is None:
                        continue
                    yield {"packet": pkt, "sample": d.get("sample", 0),
                           "invert_iq": d.get("invert_iq", False),
                           "freq_hz": self.freq_hz}
            return
        # IQ capture: load + decode.
        import numpy as np
        raw = open(self.path, "rb").read()
        # .complex8 (the elrs_sdr capture format) is int16 I/Q pairs, scaled.
        arr = np.frombuffer(raw, dtype=np.int16)
        if arr.size % 2:
            arr = arr[:-1]
        iq = (arr[0::2].astype(np.float32) / 32768.0) + 1j * (arr[1::2].astype(np.float32) / 32768.0)
        for rec in decode_iq(iq, self.srate, self.rate, uid=self.uid):
            rec["freq_hz"] = self.freq_hz
            yield rec
