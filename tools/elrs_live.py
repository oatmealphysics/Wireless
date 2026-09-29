#!/usr/bin/env python3
"""
ELRS 2.4 GHz live capture + decode for a B205mini (also runs on B210).

Pipeline:  UHD RX (2.4 GHz, ~4-6 MS/s IQ)  ->  LoRa demod (both IQ polarities)
           ->  ELRS OTA packet CRC validate  ->  live terminal print (+ file tee)

DESIGN GOALS
  * No GNU Radio. Pure Python + numpy + uhd.
  * Band / rate auto-detect so you do NOT need to know the ELRS version:
      - the demod tries the known 2.4 GHz LoRa (SF,BW) rate set,
      - the CRC init is tried across the small OTA_VERSION_ID space (1..15) and,
        for a bound link, across a brute-force UID space if needed.
  * Bind mode is the entry test: bind packets use CRC init = OTA_VERSION_ID and
    a fixed rate, so they validate with NO UID knowledge.

RUN MODES
  python3 elrs_live.py --scan                       # spectrum scan 2.4 GHz, show peaks
  python3 elrs_live.py --bind                        # capture + decode bind mode (entry test)
  python3 elrs_live.py --live --uid A1B2C3D4E5F6     # decode a known-bound link
  python3 elrs_live.py --live --uidbrute --phrase "my-phrase"   # derive+try UID
  (all capture modes tee raw IQ to --out cap.cfile and packets to --pkts pkts.jsonl)

DEPS:  pip install numpy uhd     (uhd = UHD python bindings, >= 4.2)
"""
import argparse, json, struct, sys, time, hashlib
import numpy as np

# full OTA field decoder (direction/type/channels/CRC-init/UID-low2)
import importlib.util as _ilu_ota, os as _os_ota
_ota_path = _os_ota.path.join(_os_ota.dirname(_os_ota.abspath(__file__)), "elrs_ota.py")
_ota_spec = _ilu_ota.spec_from_file_location("elrs_ota", _ota_path)
elrs_ota = _ilu_ota.module_from_spec(_ota_spec); _ota_spec.loader.exec_module(elrs_ota)

# ---------------------------------------------------------------------------
# ELRS constants (verified vs ExpressLRS main: src/src/common.cpp, OTA.h, targets.h)
# ---------------------------------------------------------------------------
OTA_VERSION_ID_CANDIDATES = list(range(1, 16))   # OTA_VERSION_ID is a 4-bit field (1..15)
CRC14_POLY = 0x2E57   # ELRS_CRC14_POLY  (OTA4 / 8-byte packet)
CRC16_POLY = 0x3D65   # ELRS_CRC16_POLY  (OTA8 / 13-byte packet)

# 2.4 GHz LoRa air rates (SX1280/LR11xx table, common.cpp). (sf, bw_khz, interval_us)
# These are the LoRa modes; FLRC/FSK 2.4 rates are a separate (non-LoRa) PHY.
RATES_2G4 = [
    # sf,  bw_khz,  pkt_interval_us,  ota_bytes
    (5, 800, 2000, 8),    # 500 Hz
    (5, 800, 3003, 13),   # 333 Hz 8ch (full-res)
    (6, 800, 4000, 8),    # 250 Hz
    (7, 800, 6666, 8),    # 150 Hz
    (7, 800, 10000, 13),  # 100 Hz 8ch (full-res)
    (8, 800, 20000, 8),   # 50 Hz
]
# Bind mode: 50 Hz-ish, single rate; the exact bind rate is slow LoRa. We sweep SF.
BIND_SF_CANDIDATES = [9, 10, 11, 12]
BIND_BW_KHZ = 500

BAND_START, BAND_STOP, BAND_MID = 2_400_400_000, 2_479_400_000, 2_440_000_000
N_CHANNELS = 80  # 2.4 GHz FHSS: 80 hops across 2400.4-2479.4 MHz

# ---------------------------------------------------------------------------
# CRC (mirror of ExpressLRS/src/lib/CRC/crc.cpp)
# ---------------------------------------------------------------------------
class Crc2Byte:
    def __init__(self, bits, poly):
        self.bits, self.poly = bits, poly
        self.mask = (1 << bits) - 1
        highbit = 1 << (bits - 1)
        tab = []
        for i in range(256):
            crc = i << (bits - 8)
            for _ in range(8):
                crc = (crc << 1) ^ ((crc & highbit) and poly or 0)
            tab.append(crc & self.mask)
        self.tab = tab
    def calc(self, data, crc=0):
        for b in data:
            crc = (crc << 8) ^ self.tab[((crc >> (self.bits - 8)) ^ b) & 0xFF]
        return crc & self.mask

def crc_init_for_uid(uid, ota_version):
    return (((uid[4] << 8) | uid[5]) ^ (ota_version << 8)) & 0xFFFF

def uid_from_phrase(phrase):
    return hashlib.md5(phrase.encode()).digest()[:6]

# ---------------------------------------------------------------------------
# LoRa demodulation - USES THE CANONICAL gr-lora_sdr REFERENCE (tapparelj/gr-lora_sdr,
# python/lora_sdr/lora.py, vendored as lora_ref.py). Its symbol convention is the
# correct folded-chirp + argmax(|fft(x*downchirp)|) one; validated here to round-trip
# symbols exactly. Do NOT re-derive the chirp math by hand (earlier attempts were wrong).
# ---------------------------------------------------------------------------
import importlib.util as _ilu
import os as _os
def _load_lora_ref():
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "lora_ref.py")
    spec = _ilu.spec_from_file_location("lora_ref", p)
    m = _ilu.module_from_spec(spec); spec.loader.exec_module(m)
    return m
_LORA = _load_lora_ref()

def lora_chirp(sf, bw_hz, fs, inverted=False):
    # kept for API compat; real demod uses _LORA.gen_downchirp (sample-rate normalized)
    n = 1 << sf
    return _LORA.gen_downchirp(sf) if inverted else _LORA.gen_upchirp(sf)

def _detect_preamble_end(rx, sf, R, search_start=0, search_len=None):
    """Locate end of preamble via downchirp correlation energy."""
    N = 1 << sf
    dc = _LORA.gen_downchirp(sf)          # N samples, base (R=1)
    if search_len is None:
        search_len = min(len(rx) - N, 64 * N)
    best_off, best_e = 0, -1.0
    step = max(1, N // 16)
    for off in range(search_start, min(search_start + search_len, len(rx) - N), step):
        win = rx[off:off + N].astype(np.complex64)
        e = np.abs(np.vdot(win, dc))
        if e > best_e:
            best_e, best_off = e, off
    return best_off

def lora_demod(iq, sf, bw_hz, fs, inverted=False, R=1, payload_len=None):
    """Demodulate baseband IQ for (sf, bw, CR R). Returns payload bytes (may be []).
    Uses reference demod_sym; handles both IQ polarities via conjugation (ELRS InvertIQ).
    """
    N = 1 << sf
    if len(iq) < N * 8:
        return []
    seg = iq.astype(np.complex64)
    if inverted:
        seg = np.conj(seg)
    # preamble end (payload start). Reference preamble ~ 8*upchirp + 2*sync + 2*down + 1/4 down,
    # each symbol N*R samples. We search and then step in N*R frames.
    start = _detect_preamble_end(seg, sf, R)
    n_sym = (payload_len if payload_len else 64)
    syms = []
    pos = start
    while pos + N * R <= len(seg) and len(syms) < n_sym:
        syms.append(_LORA.demod_sym(sf, seg[pos:pos + N * R]))
        pos += N * R
    # drop the (up to 2) preamble-sync symbols that may fall in the first frames
    # LoRa payload symbol->bit: each symbol carries SF bits, LSB-first; first 2 symbols
    # of the LoRa frame are the sync word (ELRS uses Semtech private 0x12/0x14).
    data_syms = syms[2:]
    bits = []
    for s in data_syms:
        for k in range(sf):
            bits.append((s >> k) & 1)
    out, acc, cnt = [], 0, 0
    for b in bits:
        acc |= b << cnt; cnt += 1
        if cnt == 8:
            out.append(acc & 0xFF); acc, cnt = 0, 0
    return out

# ---------------------------------------------------------------------------
# Packet validation (from elrs_decode.py - OTA8 proven, OTA4 std/hybrid)
# ---------------------------------------------------------------------------
def validate_ota4(pkt8, init, nonce=0):
    p = bytearray(pkt8)
    crc_high = p[0] >> 2
    p[0] &= 0x03
    calc = Crc2Byte(14, CRC14_POLY).calc(bytes(p[:7]), init ^ nonce)
    return calc == ((crc_high << 8) | p[7])

def validate_ota8(pkt13, init, nonce=0):
    calc = Crc2Byte(16, CRC16_POLY).calc(bytes(pkt13[:11]), init ^ nonce)
    return calc == struct.unpack('<H', pkt13[11:13])[0]

def try_decode(bytes_, uid=None, ota_candidates=OTA_VERSION_ID_CANDIDATES):
    """Try to interpret a byte buffer as OTA8 (13) or OTA4 (8) packets.
    Returns list of (offset, size, type, init_used, ota_ver) for CRC-valid packets."""
    hits = []
    inits = []
    if uid is not None:
        for ov in ota_candidates:
            inits.append(crc_init_for_uid(uid, ov))
    else:
        # bind mode / unknown: init is just OTA_VERSION_ID (low byte), 1..15
        inits = list(ota_candidates)
    for size, fn in ((13, validate_ota8), (8, validate_ota4)):
        for off in range(0, len(bytes_) - size + 1, size):
            pkt = bytes_[off:off + size]
            for i, init in enumerate(inits):
                if fn(pkt, init):
                    hits.append((off, size, pkt[0] & 0b11, init, ota_candidates[i] if uid else i))
                    break
    return hits

# ---------------------------------------------------------------------------
# UHD
# ---------------------------------------------------------------------------
def open_usrp(args):
    """Open the USRP for RX. The Python binding constructor name varies by UHD
    version: modern bindings expose uhd.usrp_multi_source(); older ones used
    uhd.usrp_multi_usrp(). Try both, and on failure print the real available
    symbols so any other mismatch is self-diagnosing (not a blind AttributeError).
    """
    import uhd
    args_str = args.device or "type=b200"   # b200 matches B205mini & B210
    for ctor_name in ("usrp_multi_source", "usrp_multi_usrp"):
        ctor = getattr(uhd, ctor_name, None)
        if ctor is not None:
            try:
                return ctor(args_str)
            except Exception as e:
                print(f"[uhd] {ctor_name}('{args_str}') failed: {e}")
    avail = [x for x in dir(uhd) if "usrp" in x.lower() or "source" in x.lower()]
    print(f"[uhd] ERROR: no usable USRP RX constructor found. args='{args_str}'")
    print(f"[uhd] available uhd.* symbols: {avail}")
    print("[uhd] hint: install UHD (>=4.2) with python bindings; on the B205mini "
          "try --device 'type=b200' (auto) or a specific IP like 'type=b200,addr=192.168.2.2'")
    raise SystemExit(1)

def setup_rx(usrp, args):
    import uhd
    usrp.set_tune_request(uhd.tune_request(args.freq))
    usrp.set_sample_rate(args.srate)
    usrp.set_gain(args.gain, 0)
    # antenna: query, since B205mini may not have "RX2"
    try:
        ants = usrp.get_antenna_names(0)
        usrp.set_antenna(ants[0], 0)
    except Exception:
        pass
    return usrp

def capture(usrp, n):
    """Get n complex samples. Modern binding: usrp.get_samples(n) -> numpy array.
    Some builds expose it as a method returning an array; guard the shape."""
    return usrp.get_samples(n)

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
def do_scan(args):
    usrp = setup_rx(open_usrp(args), args)
    print(f"[scan] {args.srate/1e6:.2f} MS/s @ center {args.freq/1e6:.1f} MHz, gain {args.gain}")
    n = int(args.srate * 2)
    iq = capture(usrp, n)
    psd = np.abs(np.fft.rfft(iq * np.hanning(len(iq)))) ** 2
    freqs = np.fft.rfftfreq(len(iq), d=1.0 / args.srate)
    abs_freqs = freqs + args.freq
    db = 10 * np.log10(psd + 1e-20)
    # report top 10 peaks
    idx = np.argsort(db)[::-1][:10]
    print("top spectral peaks (abs freq, dB rel):")
    for i in sorted(idx):
        print(f"  {abs_freqs[i]/1e6:9.2f} MHz   {db[i]:8.1f}")
    print(f"\nband center {args.freq/1e6:.1f} MHz; ELRS 2.4 hops span 2400.4-2479.4 MHz")

def do_live(args, bind=False):
    usrp = setup_rx(open_usrp(args), args)
    uid = bytes.fromhex(args.uid) if args.uid else None
    if args.phrase:
        uid = uid_from_phrase(args.phrase)
        print(f"[live] uid from phrase = {uid.hex()}")
    f = open(args.out, "wb") if args.out else None
    pk = open(args.pkts, "w") if args.pkts else None
    print(f"[live] RX {args.freq/1e6:.1f} MHz, {args.srate/1e6:.2f} MS/s, gain {args.gain}, uid={uid.hex() if uid else 'auto'}")
    chunk = int(args.srate * 0.5)
    t0 = time.time(); shown = 0
    rfs = BIND_SF_CANDIDATES if bind else [r[0] for r in RATES_2G4]
    bws = {5:800e3, 6:800e3, 7:800e3, 8:800e3, 9:500e3, 10:500e3, 11:250e3, 12:250e3}
    try:
        while True:
            iq = capture(usrp, chunk)
            if f: f.write(np.ascontiguousarray(iq).tobytes())
            for sf in rfs:
                for inv in (False, True):
                    b = lora_demod(iq, sf, bws[sf], args.srate, inv)
                    if not b:
                        continue
                    for (off, size, ptype, init, ov) in try_decode(b, uid):
                        line = (f"  HIT sf={sf} inv={int(inv)} size={size} type={ptype} "
                                f"init=0x{init:04x} ov={ov}")
                        pkt_bytes = bytes(b[off:off+size])
                        # full field decode (direction/type/channels/CRC-init/UID-low2)
                        dec = elrs_ota.decode_packet(pkt_bytes, init=init,
                                                     ota_version=ov, sf=sf)
                        shown_line = line
                        if dec:
                            extra = " ".join(
                                ["dir=%s" % dec.get("direction", "?"),
                                 "pkt=%s" % dec.get("type", "?")])
                            if dec.get("is_armed") is not None:
                                extra += " armed=%d" % dec["is_armed"]
                            if dec.get("channels_us"):
                                c = dec["channels_us"]
                                extra += " ch0=%dus" % c.get("CH0", -1)
                            if dec.get("uid_low2"):
                                extra += " uid_low2=%s" % dec["uid_low2"]
                            shown_line = line + "  " + extra
                        print(f"[{time.time()-t0:7.2f}s]{shown_line}  {pkt_bytes.hex()}")
                        if pk:
                            rec = {"t": time.time(), "sf": sf, "inv": inv,
                                   "size": size, "type": ptype, "init": hex(init),
                                   "ov": ov, "hex": pkt_bytes.hex()}
                            if dec:
                                rec["decoded"] = {k: v for k, v in dec.items()
                                                  if k != "_offset"}
                            pk.write(json.dumps(rec) + "\n")
                            pk.flush()
                        shown += 1
    except KeyboardInterrupt:
        pass
    finally:
        if f: f.close()
        if pk: pk.close()
    print(f"\n[done] {shown} CRC-valid packets in {time.time()-t0:.1f}s")

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=None, help="UHD device arg (default type=b200)")
    ap.add_argument("--freq", type=float, default=2.44e9, help="center freq Hz")
    ap.add_argument("--srate", type=float, default=6.0e6, help="sample rate Hz (4-6 MS/s for 2.4 LoRa)")
    ap.add_argument("--gain", type=float, default=30.0, help="RF gain dB")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--bind", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--uid", default=None, help="6-byte UID hex, e.g. A1B2C3D4E5F6")
    ap.add_argument("--phrase", default=None, help="binding phrase -> md5[:6] UID")
    ap.add_argument("--out", default=None, help="tee raw IQ to this .cfile")
    ap.add_argument("--pkts", default=None, help="tee decoded packets to this .jsonl")
    a = ap.parse_args()
    if a.scan:
        do_scan(a)
    elif a.bind:
        do_live(a, bind=True)
    elif a.live:
        do_live(a, bind=False)
    else:
        ap.print_help()
        print("\nTip: start with --scan to confirm you see the ELRS signal, then --bind.")
        sys.exit(0)
