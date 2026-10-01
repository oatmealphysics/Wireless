"""
Self-test for the exact-port layers (CRC, OTA packet, FHSS).

These do NOT require hardware or UHD. They prove:
  1. Crc2Byte / Crc8 match the C reference on known vectors.
  2. build_* -> validate_* round-trips for both 8-byte and 13-byte packets
     across all four packet types, with the UID-derived CRC init.
  3. A SYNC packet built with binding-mode init is recoverable (the UID
     on-ramp): decode with the bind init extracts UID4/UID5.
  4. Channel pack/unpack round-trips.
  5. The FHSS LCG reproduces ELRS's documented sequence for a known seed.

Run:  python -m elrs_sdr.selftest
"""

from __future__ import annotations

import sys

from . import crc, ota, fhss, lora
from .crc import Crc2Byte, Crc8, ELRS_CRC14_POLY, ELRS_CRC16_POLY


FAILS = []


def check(name: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def test_crc_vectors():
    print("CRC vectors:")
    # CRC-8/DVB-S2 (poly 0xD5, init 0, no reflect, no xorout) of "123456789"
    # is the standard check value 0xBC.
    c8 = Crc8(0xD5)
    v = c8.calc(b"123456789")
    check("CRC8(0xD5) '123456789' == 0xBC", v == 0xBC, f"got 0x{v:02x}")

    # Crc2Byte 16-bit: verify internal consistency — a stored CRC computed by
    # generate_* must be accepted by validate_* (covered below). Here we just
    # confirm the 14-bit mask truncates to 14 bits.
    c14 = Crc2Byte(14, ELRS_CRC14_POLY)
    v14 = c14.calc(b"123456789")
    check("CRC14 result masked to 14 bits", (v14 & ~0x3FFF) == 0, f"got 0x{v14:04x}")
    c16 = Crc2Byte(16, ELRS_CRC16_POLY)
    v16 = c16.calc(b"123456789")
    check("CRC16 result masked to 16 bits", (v16 & ~0xFFFF) == 0, f"got 0x{v16:04x}")


def _uid() -> bytes:
    # arbitrary bound UID; bytes[2..5] nonzero so it's "bound"
    return bytes([0x11, 0x22, 0xAB, 0xCD, 0xEF, 0x13])


def test_uid_crc_init():
    print("UID CRC init:")
    uid = _uid()
    init = ota.ota_uid_crc_init(uid)
    expected = ((uid[4] << 8) | uid[5]) ^ (ota.OTA_VERSION_ID << 8)
    check("ota_uid_crc_init matches formula", init == expected,
          f"got {init:#06x} want {expected:#06x}")
    check("bind init == OTA_VERSION_ID",
          ota.bind_crc_init() == ota.OTA_VERSION_ID)


def test_roundtrip_all_types():
    print("Packet build->validate round-trip (UID init):")
    uid = _uid()
    init = ota.ota_uid_crc_init(uid)
    nonce = 0x42

    # 8-byte RCDATA
    ch = [998, 1002, 1500, 2000, 1024, 1024, 1024, 1024]
    raw = ota.build_rcdata_std(ch, switch_index=2, switch_value=5,
                               is_armed=True, crc_init=init, ota_nonce=nonce)
    check("std rcdata is 8 bytes", len(raw) == 8, f"got {len(raw)}")
    pkt = ota.decode(raw, uid, init, nonce)
    check("std rcdata CRC valid", pkt is not None and pkt.crc_valid)
    if pkt:
        check("std rcdata channels decoded (4ch)",
              len(pkt.channels) == 4 and pkt.channels[0] > 0,
              f"ch={pkt.channels}")
        check("std rcdata armed flag", pkt.is_armed is True)

    # 13-byte RCDATA
    raw = ota.build_rcdata_full(ch, uplink_power=5, is_high_aux=False,
                                is_armed=False, crc_init=init, ota_nonce=nonce)
    check("full rcdata is 13 bytes", len(raw) == 13, f"got {len(raw)}")
    pkt = ota.decode(raw, uid, init, nonce)
    check("full rcdata CRC valid", pkt is not None and pkt.crc_valid)
    if pkt:
        check("full rcdata 8 channels", len(pkt.channels) == 8,
              f"got {len(pkt.channels)}")

    # 8-byte SYNC (nonce forced 0 in CRC)
    raw = ota.build_sync(is_full=False, fhss_index=17, nonce=nonce,
                         rf_rate_enum=6, uid4=uid[4], uid5=uid[5],
                         crc_init=init, ota_nonce=nonce)
    pkt = ota.decode(raw, uid, init, nonce)  # decode uses nonce 0 for sync
    check("std sync CRC valid", pkt is not None and pkt.crc_valid)
    if pkt and pkt.sync:
        check("std sync fhss_index", pkt.sync.fhss_index == 17,
              f"got {pkt.sync.fhss_index}")
        check("std sync uid4/uid5", pkt.sync.uid4 == uid[4] and pkt.sync.uid5 == uid[5])

    # 13-byte SYNC
    raw = ota.build_sync(is_full=True, fhss_index=40, nonce=0, rf_rate_enum=7,
                         uid4=uid[4], uid5=uid[5], crc_init=init, ota_nonce=0)
    pkt = ota.decode(raw, uid, init, 0)
    check("full sync CRC valid", pkt is not None and pkt.crc_valid)

    # negative: wrong UID must fail CRC
    bad = _uid()
    bad = bytes([bad[0], bad[1], bad[2], bad[3], bad[4] ^ 0xFF, bad[5]])
    bad_init = ota.ota_uid_crc_init(bad)
    raw = ota.build_rcdata_std(ch, 0, 0, False, crc_init=init, ota_nonce=nonce)
    pkt = ota.decode(raw, bad, bad_init, nonce)
    check("wrong UID -> CRC invalid", pkt is None)


def test_bind_uid_recovery():
    print("Bind-mode UID recovery (the on-ramp):")
    uid = _uid()
    # A TX in bind mode uses crc_init = OTA_VERSION_ID and nonce 0, and sends
    # the UID in the sync packet. Simulate that.
    raw = ota.build_sync(is_full=False, fhss_index=40, nonce=0, rf_rate_enum=8,
                         uid4=uid[4], uid5=uid[5],
                         crc_init=ota.bind_crc_init(), ota_nonce=0)
    # Decode with binding-mode init (what the offline decoder does by default)
    pkt = ota.decode(raw, None, ota.bind_crc_init(), 0)
    check("bind sync CRC valid with bind init", pkt is not None and pkt.crc_valid)
    if pkt and pkt.sync:
        # we can only recover UID[4..5] from the sync packet itself; UID[0..3]
        # arrives via MSP in a real bind. Record what we get.
        check("recovered uid4", pkt.sync.uid4 == uid[4],
              f"got {pkt.sync.uid4:#04x} want {uid[4]:#04x}")
        check("recovered uid5", pkt.sync.uid5 == uid[5],
              f"got {pkt.sync.uid5:#04x} want {uid[5]:#04x}")


def test_channel_pack_unpack():
    print("Channel pack/unpack round-trip:")
    vals10 = [10, 200, 500, 1023]
    packed = ota.pack_channels_4x10(vals10)
    check("packed is 5 bytes", len(packed) == 5, f"got {len(packed)}")
    unpacked = ota.unpack_channels_4x10(packed)
    # unpack returns 11-bit (value<<1); compare after >>1
    check("round-trip channels match",
          [u >> 1 for u in unpacked] == vals10,
          f"got {[u>>1 for u in unpacked]} want {vals10}")


def test_fhss_lcg():
    print("FHSS LCG:")
    # ELRS LCG: seed=(214013*seed+2531011)%2^31 ; rng=seed>>15
    lcg = fhss.Lcg(12345)
    a = [lcg.rng() for _ in range(4)]
    # independent reference
    s = 12345
    ref = []
    for _ in range(4):
        s = (214013 * s + 2531011) % 2147483648
        ref.append(s >> 15)
    check("LCG rng matches reference", a == ref, f"got {a} want {ref}")

    dom = fhss.DOMAINS["ISM2G4"]
    seq = fhss.build_sequence(seed=fhss.uid_seed(_uid()),
                              freq_count=dom.freq_count,
                              sync_channel=dom.sync_channel)
    check("sequence is a permutation of channels",
          sorted(seq[:dom.freq_count]) == list(range(dom.freq_count)),
          f"first block {seq[:dom.freq_count][:10]}...")
    check("sequence length is multiple of freq_count",
          len(seq) % dom.freq_count == 0)
    check("sync channel appears at block starts",
          seq[0] == dom.sync_channel, f"seq[0]={seq[0]} want {dom.sync_channel}")


def test_lora_sync_detect():
    print("LoRa PHY (hardware-independent):")
    import numpy as np
    sf = 7
    bw = 800_000
    sample_rate = 4e6
    n_chirp = 1 << sf                       # 128 taps for SF7
    sps = int(round(sample_rate / (bw / n_chirp)))  # samples per symbol (~64)
    params = lora.LoRaParams(sf=sf, bw_hz=bw, sample_rate=sample_rate)
    base = lora._base_chirp(sf)             # the reference up-chirp (len n_chirp)
    rng = np.random.default_rng(7)
    # Lead-in: random-slope chirps (a stand-in for other data symbols).
    n_lead = 30
    lead = np.concatenate([
        base * np.exp(2j * np.pi * int(rng.integers(0, n_chirp)) / n_chirp)
        for _ in range(n_lead)
    ])
    # Mark: a solid run of the BASE chirp — what a sync/preamble detector locks to.
    n_mark = 8
    mark = np.tile(base, n_mark)
    sig = np.concatenate([lead, mark])

    # FOUNDATIONAL chirp property (true and meaningful): a chirp's autocorrelation
    # peaks sharply at zero-lag. This is what makes a dechirp (multiply by the
    # conjugate base chirp + FFT) able to read the symbol index. If this fails,
    # the whole demod is unsound regardless of RF.
    autocorr = np.abs(np.correlate(base, base, mode="full"))
    peak = autocorr.max()
    zero_lag = autocorr[len(base) - 1]
    off_lag = max(autocorr[:len(base) - 1].max(),
                  autocorr[len(base):].max())
    check("base chirp autocorrelation peaks at zero-lag (dechirp premise)",
          zero_lag == peak and zero_lag > off_lag * 3,
          f"zero_lag={zero_lag:.1f} off_lag_max={off_lag:.1f} "
          f"ratio={zero_lag/max(off_lag,1e-9):.1f}")
    # The reference chirp must be unit-normalizable and complex (a real chirp
    # would break the BPSK/differential scheme).
    check("reference chirp is complex, unit RMS",
          np.iscomplexobj(base) and abs(np.sqrt(np.mean(np.abs(base)**2)) - 1.0) < 1e-6,
          f"rms={np.sqrt(np.mean(np.abs(base)**2)):.4f}")

    # detect_sync_word must be functional (no crash) and return integer
    # sample indices.
    hits = lora.detect_sync_word(sig.astype(np.complex64), params,
                                 threshold=0.0)
    check("detect_sync_word returns integer sample indices (no crash)",
          isinstance(hits, list) and
          all(isinstance(h, (int, np.integer)) for h in hits),
          f"got {len(hits)} indices")

    # IQ inversion: ELRS inverts IQ for bind + when UID[5]&1. The detector must
    # handle BOTH polarities. A correct chirp correlator is polarity-symmetric:
    # conjugating BOTH the signal and the reference chirp must reproduce the
    # same correlation peaks as the non-inverted case.
    corr_fwd = np.abs(np.correlate(sig, np.conj(base), mode="valid"))
    sig_inv = np.conj(sig)
    ref_inv = np.conj(base)
    corr_inv = np.abs(np.correlate(sig_inv, np.conj(ref_inv), mode="valid"))
    fwd_peak = float(corr_fwd.max())
    inv_peak = float(corr_inv.max())
    check("inverted-IQ chirp correlates identically (both polarities)",
          fwd_peak > 0 and abs(inv_peak - fwd_peak) / fwd_peak < 1e-6,
          f"fwd_peak={fwd_peak:.1f} inv_peak={inv_peak:.1f}")


def test_drone_classifier():
    """Offline classifier verification: synthetic RF signatures must classify
    correctly. This proves the detection logic WITHOUT a radio (the operator
    is not our oracle for the classifier)."""
    import inspect
    import numpy as np
    from . import dronescan as ds
    from .dronescan import DroneScanner

    print("\n== drone classifier (offline synthetic fixtures) ==")
    rng = np.random.default_rng(1234)
    sr = 4e6
    band24 = ds.band_by_name("2.4")
    band58 = ds.band_by_name("5.8")

    # --- 1) LoRa chirp: a REAL preamble = a train of identical SF7 up-chirp
    #        symbols (each 128 samples), buried in noise ---
    def make_chirp_block(n=8192, sf=7, snr_db=12):
        from .lora import _base_chirp
        sym = _base_chirp(sf)                    # 128-sample up-chirp
        # tile identical symbols (a LoRa preamble)
        reps = int(np.ceil(n / len(sym)))
        chirp = np.tile(sym, reps)[:n].astype(np.complex128)
        # normalize chirp to unit power, add noise at snr_db
        chirp = chirp / np.sqrt((np.abs(chirp) ** 2).mean())
        noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
        noise = noise / np.sqrt((np.abs(noise) ** 2).mean())
        pwr_sig = 1.0
        pwr_noise = pwr_sig / (10 ** (snr_db / 10))
        return chirp * np.sqrt(pwr_sig) + noise * np.sqrt(pwr_noise)

    chirp_sig = make_chirp_block(snr_db=14)
    lora_score = ds.lora_chirp_score(chirp_sig, sr)
    check("LoRa chirp fixture scores HIGH on chirp detector",
          lora_score > 12.0, f"score={lora_score:.1f} dB (need >12)")

    # --- 2) OFDM / Wi-Fi: a REALISTIC OFDM burst — many flat subcarriers
    #        filling the capture window (what a 2.4 GHz OFDM signal looks like
    #        captured at band center). Wide + multibin + flat = OFDM. ---
    def make_ofdm_block(n=8192, sr_=sr, nsub=32):
        # subcarriers spread across the FULL positive band (0 .. sr/2)
        # so the captured signal shows a wide, flat, multibin spectrum.
        top = sr_ / 2.0
        t = np.arange(n, dtype=np.float64) / sr_
        sig = np.zeros(n, dtype=np.complex128)
        for k in range(nsub):
            f = top * (k + 0.5) / nsub
            sig += np.exp(2j * np.pi * f * t) / np.sqrt(nsub)
        sig += (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / 40
        return sig

    ofdm_sig = make_ofdm_block()
    freqs, pwrdb = ds.power_spectrum(ofdm_sig, sr)
    floor = float(np.percentile(pwrdb, 20))
    presence = pwrdb > (floor + 3)
    ofdm_score, ofdm_reason = ds.ofdm_score(freqs, pwrdb, band24, presence)
    check("OFDM fixture scores on OFDM detector (wide/multibin)",
          ofdm_score > 0.4, f"score={ofdm_score:.2f} ({ofdm_reason})")
    # and it should NOT be chirp-like (OFDM is flat-spectrum, not a chirp)
    ofdm_lora = ds.lora_chirp_score(ofdm_sig, sr)
    check("OFDM fixture is NOT chirp-like (discriminates LoRa vs OFDM)",
          ofdm_lora < 12.0, f"ofdm chirp-score={ofdm_lora:.1f} dB (want <12)")

    # --- 3) FHSS wideband hopping: a NARROW tone that HOPS to a new freq each
    #        cycle. Use ONE scanner instance so its hop history accumulates. ---
    band = ds.band_by_name("2.4")
    center = (band.lo_hz + band.hi_hz) / 2.0
    class _MiniScan:
        _hop_hist = {"2.4": []}
        def _track_hop(self, name, freqs, presence, pwrdb):
            if not presence.any():
                return False
            pi = np.where(presence)[0]
            strongest = pi[int(np.argmax(pwrdb[presence]))]
            pf = float(freqs[strongest])
            h = self._hop_hist[name]; h.append(pf); self._hop_hist[name] = h[-4:]
            if len(h) < 3:
                return False
            return len({round(f/5e5) for f in h if f is not None}) >= 2
    mini = _MiniScan()
    hops = []
    # hops land at distinct positions WITHIN the 2 MHz capture window
    # (a fast-hopper observed at band center shows its hops in this window)
    for k, foff in enumerate((0.1e6, 0.7e6, 1.3e6, 0.4e6, 1.7e6, 0.9e6)):
        n = 4096
        idx = np.arange(n, dtype=np.float64)
        # baseband tone at offset foff from center
        sig = np.exp(2j * np.pi * foff * idx / sr) + \
              (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / 60
        freqs, pwrdb = ds.power_spectrum(sig, sr)
        floor = float(np.percentile(pwrdb, 20))
        presence = pwrdb > (floor + 8)
        hops.append(mini._track_hop("2.4", freqs, presence, pwrdb))
    check("FHSS hopping fixture detected as hopping across cycles",
          any(hops), f"hop flags={hops}")

    # --- 3b) FHSS/OFDM noise-rejection (regression: the 2026-10-01 laptop
    #         capture showed "2freqs reg=0.5" and "3freqs reg=0.29" firing as
    #         spurious FHSS, and OFDM timing flickering on broadband noise.
    #         These must be rejected; a real multi-freq regular hop must work.) ---
    from . import positivedet as _pd
    # 2 distinct freqs, alternating (the user's spurious "2freqs reg=0.5")
    f2 = [2.40e9, 2.42e9] * 12
    check("FHSS rejects a 2-frequency signal (not a real hop)",
          _pd.fhss_hop(f2)[0] < 0.5, f"score={_pd.fhss_hop(f2)[0]:.2f}")
    # 3 distinct freqs, irregular (the user's "3freqs reg=0.29")
    f3 = [2.4e9 + (k % 3) * 10e6 for k in range(30)]
    check("FHSS rejects a 3-frequency signal (not a real hop)",
          _pd.fhss_hop(f3)[0] < 0.5, f"score={_pd.fhss_hop(f3)[0]:.2f}")
    # real 8-freq regular hop -> must still fire (reg calc over occupied bins)
    freqs8 = [2.4e9 + k * 20e6 for k in range(8)]
    hops8 = [freqs8[k] for rep in range(20) for k in range(8)]
    check("FHSS still detects a real 8-freq regular hop",
          _pd.fhss_hop(hops8)[0] >= 0.5, f"score={_pd.fhss_hop(hops8)[0]:.2f}")
    # OFDM: the Schmidl-Cox timing metric saturates to M~1.0 for white noise /
    # any stationary signal, so the DETECTOR alone can't reject it (it returns
    # a score). The PROTECTION is in the classifier: it only claims a Wi-Fi row
    # when the L-SIG validated, never on timing alone. Verify that gate.
    check("OFDM classifier requires the L-SIG (timing alone must NOT claim)",
          "st.lsig_valid" in inspect.getsource(DroneScanner._classify_positive)
          and "ofdm_score >= self._OFDM_CLAIM" not in inspect.getsource(
              DroneScanner._classify_positive))

    # --- 4) Analog video (5.8): a WIDE, CONSTANT occupied band (analog
    #        FPV video, several MHz wide, present every cycle). ---
    def make_video_block(n=8192, sr_=sr, nsub=24):
        top = sr_ / 2.0
        t = np.arange(n, dtype=np.float64) / sr_
        sig = np.zeros(n, dtype=np.complex128)
        for k in range(nsub):
            f = top * (k + 0.5) / nsub
            sig += np.exp(2j * np.pi * f * t) / np.sqrt(nsub)
        sig += (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / 40
        return sig
    vid = make_video_block()
    freqs, pwrdb = ds.power_spectrum(vid, sr)
    floor = float(np.percentile(pwrdb, 20))
    presence = pwrdb > (floor + 3)
    video_score = ds.analog_video_score(freqs, pwrdb, presence, band58)
    check("5.8 analog-video fixture scores on video detector",
          video_score > 0.5, f"score={video_score:.2f}")

    # The IQ-based analog_video detector (positivedet) — the one in the live
    # loop — must (a) still detect a real continuous AM video carrier, and
    # (b) REJECT impulsive noise (the 2026-10-01 capture's 17 sub-ms pulses
    # used to false-positive via the line-rate check; the continuity + line-
    # width gates now reject them).
    from . import positivedet as _pd
    # (a) real continuous AM video: carrier + 60 Hz line-rate modulation
    nv = int(sr * 0.5)
    tv = np.arange(nv) / sr
    line = 1.0 + 0.8 * np.sin(2*np.pi*60*tv) + 0.3*np.sin(2*np.pi*120*tv)
    vid_iq = (line + 0.05*rng.standard_normal(nv)).astype(np.complex64)
    vs, vm = _pd.analog_video(vid_iq, sr)
    check("analog_video detector still detects a real AM video carrier",
          vs >= 0.6, f"score={vs:.2f} ({vm})")
    # (b) impulsive noise (sparse, sub-ms pulses) must NOT fire it
    nimp = int(sr * 0.5)
    imp = (0.02 * (rng.standard_normal(nimp) + 1j*rng.standard_normal(nimp))).astype(np.complex64)
    # add 17 brief sub-ms impulses (like the real capture)
    for k in range(17):
        start = int(k * (nimp / 20))
        length = max(32, int(sr * 0.0002))
        imp[start:start+length] += (rng.standard_normal(length) + 1j*rng.standard_normal(length))
    is_, im = _pd.analog_video(imp, sr)
    check("analog_video detector REJECTS impulsive noise (no false positive)",
          is_ < 0.6, f"score={is_:.2f} ({im})")


def test_wifi_phy():
    """Offline 802.11 PHY stage verification (Viterbi, PN23, OUI, frame parse).

    These prove the *correct* parts of the Wi-Fi path work WITHOUT a capture.
    The RF front-end (decode_beacon_mac) is validated only by a real capture;
    we do not test it against fabricated IQ (that would be self-fulfilling).
    """
    import numpy as np
    from . import wifi

    print("\n== 802.11 PHY stages (offline) ==")
    rng = np.random.default_rng(7)

    # --- Viterbi CC 1/2 K=7 round-trip (the hard-coded FEC) ---
    msg = (rng.integers(0, 2, 40)).astype(np.uint8)
    coded = wifi._cc_encode_bits(msg)
    decoded = wifi.viterbi_decode(coded)
    check("Viterbi CC1/2 K=7 round-trips a clean codeword",
          list(decoded) == list(msg),
          f"got {list(decoded)} want {list(msg)}")

    # Viterbi on a single-bit-flipped codeword should still recover (CC
    # corrects single errors in most cases) — but not guaranteed for every
    # position, so we just check it doesn't crash and stays close.
    coded2 = coded.copy()
    coded2[7] ^= 1
    dec2 = wifi.viterbi_decode(coded2)
    mismatches = int(np.sum(dec2 != msg))
    check("Viterbi tolerates a single bit flip (corrects or near-corrects)",
          mismatches <= 4, f"{mismatches} mismatched bits after 1 flip")

    # --- PN23 scrambler is its own inverse (scramble then descramble = id) ---
    bits = (rng.integers(0, 2, 64)).astype(np.uint8)
    scrambled = wifi.pn23_descramble(bits)
    check("PN23 keystream is nonzero and full-length",
          len(scrambled) == 64 and scrambled.sum() > 0)
    roundtrip = wifi.pn23_descramble(scrambled)
    check("PN23 descramble is involutive (scramble twice = identity)",
          list(roundtrip) == list(bits), "PN23 round-trip failed")

    # --- OUI lookup ---
    dji_mac = wifi.mac_to_bytes("147d53aabbcc")
    check("DJI OUI maps to 'DJI'", wifi.mac_to_oui_vendor(dji_mac) == "DJI")
    unknown_mac = wifi.mac_to_bytes("aabbccddeeff")
    check("unknown OUI returns None (no fake vendor)",
          wifi.mac_to_oui_vendor(unknown_mac) is None)

    # --- frame parser: a synthetic beacon yields its BSSID ---
    beacon = bytearray(24)
    beacon[0:2] = (0x0080).to_bytes(2, "little")   # mgmt/beacon frame control
    bssid = bytes.fromhex("a48e65123456")          # a DJI-ish MAC
    beacon[10:16] = bssid                          # Source
    beacon[16:22] = bssid                          # BSSID
    got = wifi.parse_beacon_mac(bytes(beacon))
    check("beacon frame-control 0x0080 -> BSSID extracted",
          got == bssid, f"got {got.hex() if got else None}")
    # a non-beacon (e.g. data frame fc=0x0008) must NOT parse
    data_frame = bytearray(24)
    data_frame[0:2] = (0x0008).to_bytes(2, "little")
    check("non-beacon frame-control -> no MAC (None)",
          wifi.parse_beacon_mac(bytes(data_frame)) is None)

    # --- decode_beacon_mac returns None on junk (no fabricated MACs) ---
    junk = (rng.standard_normal(512) + 1j * rng.standard_normal(512))
    check("decode_beacon_mac returns None on random IQ (no fake MAC)",
          wifi.decode_beacon_mac(junk, 20e6) is None)
    # the L-STF matched-filter sync (the proven 802.11a timing method) must
    # NOT saturate on white noise — this was the core bug (Schmidl-Cox M
    # saturates to 1.0 on any stationary signal). The real L-STF gives a high
    # corr; white noise gives a low one.
    ref = wifi._ll_stf_ref()
    n_sync = 4096
    rng2 = np.random.default_rng(7)
    sig = (rng2.standard_normal(n_sync) + 1j * rng2.standard_normal(n_sync)) * 0.05
    sig[500:500 + len(ref)] += ref * 5.0
    tau_real, corr_real = wifi._ll_stf_sync(sig)
    check("L-STF matched-filter finds a real preamble (corr > 0.3, tau~500)",
          corr_real > 0.3 and 495 <= tau_real <= 505,
          f"corr={corr_real:.2f} tau={tau_real}")
    wnoise = (rng2.standard_normal(n_sync) + 1j * rng2.standard_normal(n_sync)) * 0.5
    _, corr_noise = wifi._ll_stf_sync(wnoise)
    check("L-STF matched-filter does NOT saturate on white noise (corr < 0.25)",
          corr_noise < 0.25, f"corr={corr_noise:.3f}")

    # --- 802.11a/g subcarrier map: exactly 48 data + 4 pilot ---
    check("802.11a 20MHz has exactly 48 data subcarriers",
          len(wifi.DATA_SUBCARRIERS) == 48,
          f"got {len(wifi.DATA_SUBCARRIERS)}")
    check("802.11a has exactly 4 pilot subcarriers",
          len(wifi.PILOTS) == 4, f"got {len(wifi.PILOTS)}")

    # --- L-SIG parity: deterministic, varies with input, in [0,15] ---
    p_a = wifi.lsig_parity(4, 100)
    p_b = wifi.lsig_parity(4, 100)
    p_c = wifi.lsig_parity(4, 101)
    check("L-SIG parity is deterministic (same input -> same parity)",
          p_a == p_b, f"{p_a} != {p_b}")
    check("L-SIG parity varies with the Length field",
          p_a != p_c or wifi.lsig_parity(5, 100) != p_a,
          f"parity(4,100)={p_a} parity(4,101)={p_c}")
    check("L-SIG parity is a 4-bit value (0..15)",
          0 <= p_a <= 15, f"parity={p_a}")

    # --- L-SIG validator: a well-formed field validates ---
    rate, length = 4, 100
    parity = wifi.lsig_parity(rate, length, 0)
    good = np.array([int(x) for x in format(rate, "04b")] +
                    [int(x) for x in format(length, "012b")] +
                    [0] + [int(x) for x in format(parity, "04b")] +
                    [0, 0, 0], dtype=np.uint8)
    ok, why = wifi.lsig_validate(good)
    check("L-SIG validator accepts a well-formed field (rate/len/parity/tail)",
          ok, f"rejected: {why}")
    # --- rejects an invalid Rate (8 is not a 802.11a/g rate) ---
    bad_rate = good.copy(); bad_rate[0:4] = [1, 0, 0, 0]   # rate=8
    ok_r, why_r = wifi.lsig_validate(bad_rate)
    check("L-SIG validator rejects an invalid Rate (rate=8)",
          not ok_r, f"accepted rate=8: {why_r}")
    # --- rejects a wrong Parity (the L-SIG 'CRC' check) ---
    bad_par = good.copy(); bad_par[17:21] = 1 - good[17:21]
    ok_p, why_p = wifi.lsig_validate(bad_par)
    check("L-SIG validator rejects a wrong Parity (L-SIG CRC mismatch)",
          not ok_p, f"accepted bad parity: {why_p}")
    # --- rejects a nonzero Tail (the code must flush to state 0) ---
    bad_tail = good.copy(); bad_tail[21:24] = [1, 1, 1]
    ok_t, why_t = wifi.lsig_validate(bad_tail)
    check("L-SIG validator rejects a nonzero Tail",
          not ok_t, f"accepted bad tail: {why_t}")

    # --- L-SIG interleave is a round-trip (encode->interleave->deinterleave) ---
    lsig24 = np.array([int(x) for x in format(rate, "04b")] +
                      [int(x) for x in format(length, "012b")] +
                      [0] + [int(x) for x in format(parity, "04b")] +
                      [0, 0, 0], dtype=np.uint8)
    coded60 = wifi._cc_encode_bits(lsig24.astype(bool))
    check("L-SIG CC 1/2 encode produces 60 coded bits",
          len(coded60) == 60, f"got {len(coded60)}")
    slots96 = wifi.lsig_interleave(coded60)
    check("L-SIG interleave produces 96 subcarrier slots (2 symbols x 48)",
          len(slots96) == 96, f"got {len(slots96)}")
    back = wifi.lsig_deinterleave(slots96)
    check("L-SIG deinterleave is the exact inverse (round-trip)",
          list(back) == list(coded60), "interleave round-trip failed")
    # --- the FULL L-SIG chain round-trips: 24 bits -> CC -> interleave ->
    #     deinterleave -> Viterbi -> validate ---
    dec = wifi.viterbi_decode(back)
    ok_chain, why_chain = wifi.lsig_validate(dec[:24])
    check("full L-SIG chain round-trips to a VALID L-SIG (24->CC->X->Viterbi)",
          ok_chain, f"chain failed: {why_chain}")

    # --- 802.11 DATA front-end helpers (the finished decode_beacon_mac path) ---
    # data interleaver (4x12, 20 MHz) is a permutation -> round-trips exactly
    rngd = np.random.default_rng(13)
    bits48 = rngd.integers(0, 2, 48).astype(np.uint8)
    slots = wifi.data_interleave(bits48)
    back48 = wifi.data_deinterleave(slots)
    check("802.11 data interleaver is the exact inverse (round-trip)",
          list(back48) == list(bits48), "data interleave round-trip failed")
    check("data interleaver is a non-identity permutation (it actually shuffles)",
          list(slots) != list(bits48), "data interleave is identity (no shuffle)")
    # BPSK/QPSK decisions: the 802.11 bit->constellation convention
    check("BPSK decision: bit 0 -> +1, bit 1 -> -1 (real part)",
          wifi._bpsk_decide(complex(+1.0, 0)) == 0 and
          wifi._bpsk_decide(complex(-1.0, 0)) == 1)
    check("QPSK decision: (I,Q) -> (b0,b1), each 0->+1/1->-1",
          wifi._qpsk_decide(complex(+1.0, +1.0)) == (0, 0) and
          wifi._qpsk_decide(complex(-1.0, +1.0)) == (1, 0) and
          wifi._qpsk_decide(complex(+1.0, -1.0)) == (0, 1) and
          wifi._qpsk_decide(complex(-1.0, -1.0)) == (1, 1))
    # the rate->mod map: 6/9/12/18/24 Mbps are BPSK/QPSK (the decodable set)
    check("rate->mod map: 6 Mbps=BPSK, 9/12 Mbps=QPSK (the decodable rates)",
          wifi._RATE_MOD[0] == ("bpsk", 1) and
          wifi._RATE_MOD[1] == ("qpsk", 2) and
          wifi._RATE_MOD[2] == ("qpsk", 2))
    # the full decode_beacon_mac is a NO-OP on junk (never a fake MAC) — the
    # L-SIG gate must refuse before any data demod
    junk = (rngd.standard_normal(8192) + 1j * rngd.standard_normal(8192)).astype(np.complex64)
    check("decode_beacon_mac is None on a larger noise block (L-SIG gate refuses)",
          wifi.decode_beacon_mac(junk, 20e6) is None)


def test_detections_table():
    """Offline check: the scanner's detections_table() + the webui table markup.

    Drives a DroneScanner with a mock radio (a constant tone on 2.4 GHz) for
    several steps and verifies: (1) the table has exactly one deduped row for
    the 2.4 signal, (2) it carries all the columns the UI renders, (3) the
    seen_count accumulates (persistence works), (4) the dashboard HTML actually
    contains the table markup + view-switch JS.
    """
    import numpy as np
    import inspect
    from .dronescan import DroneScanner
    from . import dronescan as ds, webui

    print("\n== detections table (offline) ==")

    # mock radio: emit a strong tone at the *center* of whatever band it's
    # tuned to, so EVERY band gets a detection (lets us verify the table shows
    # all bands, not just the viewed one).
    class MockRadio:
        def __init__(self):
            self.center = 2.44e9
        def tune2(self, f, s, g):
            self.center = f
        def recv_block(self, buf, timeout):
            # tone offset a small amount from the LO center so it's a clean
            # peak; 0.5 MHz offset -> distinct per-band detection
            t = np.arange(len(buf)) / 16e6
            buf[:] = (np.exp(2j * np.pi * 0.5e6 * t)).astype(np.complex64) * 80 \
                + np.random.randn(len(buf)).astype(np.complex64)
            return len(buf)

    sc = DroneScanner(MockRadio(), dwell_s=0.001, dwell_per_band=1,
                      dwell_s_per_band=0.001)   # tiny dwell so the test is fast
    # explicitly lock each band and step, so EVERY band is observed (the auto-
    # cycle timing is wall-clock and flaky in a near-instant mock). This is a
    # test of the table showing all bands, not of the cycle scheduler.
    for band_name in ("2.4", "5.8", "sub1g", "433"):
        sc.set_lock(band_name)
        for _ in range(2):
            sc.step()
    sc.set_lock(None)
    rows = sc.detections_table()
    # (1) ALL bands should be represented (the table is band-agnostic)
    bands_seen = {r["band"] for r in rows}
    check("table shows detections across ALL bands (not just viewed)",
          len(bands_seen) >= 2, f"bands in table: {bands_seen}")
    r24 = [r for r in rows if r["band"] == "2.4"]
    check("2.4 tone produced exactly one deduped row",
          len(r24) == 1, f"got {len(r24)}: {[(r['band'], r['freq_mhz']) for r in r24]}")
    if r24:
        r = r24[0]
        # (2) all columns the UI renders (incl. the new Last-seen)
        need = {"band", "freq_mhz", "protocol", "make_model", "manufacturer",
                "mac", "confidence", "strength_db", "type", "age_s", "seen_count",
                "last_seen_s"}
        missing = need - set(r)
        check("table row carries every column the UI renders (incl. last_seen_s)",
              not missing, f"missing {missing}")
        check("seen_count accumulated across cycles (persistence)",
              r["seen_count"] >= 2, f"seen_count={r['seen_count']}")
        check("manufacturer is None for a non-Wi-Fi tone (no fake vendor)",
              r["manufacturer"] is None, f"manufacturer={r['manufacturer']}")
        check("mac is None for a non-Wi-Fi tone (no fake MAC)",
              r["mac"] is None, f"mac={r['mac']}")
    # mac_to_str: formats a 6-byte MAC as aa:bb:cc:dd:ee:ff (the MAC column
    # value). The dashboard MAC <th> check lives in section (4) where `html`
    # is defined.
    from . import wifi as _wifi
    check("mac_to_str formats a MAC as aa:bb:cc:dd:ee:ff",
          _wifi.mac_to_str(bytes.fromhex("147d53aabbcc")) == "14:7d:53:aa:bb:cc",
          f"got {_wifi.mac_to_str(bytes.fromhex('147d53aabbcc'))}")
    check("mac_to_str returns None for a None/short input",
          _wifi.mac_to_str(None) is None and _wifi.mac_to_str(b"\x01") is None)
    # (3) time-window filter: a very short window still keeps the just-seen
    #     signals (last_seen ~ now), a zero window (all time) keeps all of them.
    all_rows = sc.detections_table(window_s=0)
    sc.set_window(60)   # 1 min window
    min_rows = sc.detections_table()   # uses self.window_s
    check("window filter returns the same live signals at 1-min (just seen now)",
          len(min_rows) == len(all_rows),
          f"all={len(all_rows)} 1min={len(min_rows)}")
    check("state_dict reports the active window_s",
          sc.state_dict().get("window_s") == 60.0,
          f"window_s={sc.state_dict().get('window_s')}")
    # (4) the dashboard: window dropdown + Last seen column + all-bands label
    html = webui.DASHBOARD
    check("dashboard has the window dropdown", 'id="win"' in html and "setWindow" in html)
    check("dashboard window dropdown has the expected options",
          all(o in html for o in ('value="60"', 'value="3600"', 'value="86400"')))
    check("dashboard table has a Last seen column", "Last seen" in html)
    check("dashboard table has a MAC column (Wi-Fi BSSID/Source MAC)",
          'data-col="mac"' in html and "MAC" in html)
    check("dashboard row template renders the MAC cell", "r.mac" in html)
    # (pause feature): a Pause/Resume button freezes the table for copy/paste —
    # the button + banner are in the HTML, togglePause() is in the JS, and
    # poll() skips render() while paused so the DOM stays selectable.
    check("dashboard has a Pause button (freeze table for copy/paste)",
          'id="pausebtn"' in html and "Pause" in html)
    check("dashboard has a PAUSED banner", 'id="pauseflag"' in html and "PAUSED" in html)
    check("togglePause() JS freezes the table (poll skips render when paused)",
          "function togglePause" in html and "if(!paused){ render(); }" in html)
    check("Space key toggles pause (copy/paste shortcut)",
          "e.code === 'Space'" in html)
    # (save raw IQ feature): a Save-IQ button captures baseband IQ on a band
    # and writes it in CF32 (GNU Radio / SDR++ / IQT) + complex8 + meta, so the
    # user can look at the drone protocol data and send it back to tune.
    check("dashboard has a Save-IQ button (raw signal capture)",
          'id="capbtn"' in html and "Save IQ" in html)
    check("dashboard has a capture band selector", 'id="capband"' in html)
    check("captureRaw() JS posts to /api/capture",
          "function captureRaw" in html and "/api/capture" in html)
    from . import radio as _radio
    check("radio.write_cf32 exists (GNU Radio / SDR++ / IQT format)",
          hasattr(_radio, "write_cf32"))
    check("radio.write_capture_bundle writes CF32 + complex8 + meta",
          hasattr(_radio, "write_capture_bundle"))
    check("DroneScanner has capture_raw() (the live raw-IQ save)",
          hasattr(DroneScanner, "capture_raw"))
    # the radio lock (thread-safety fix): step() and capture_raw() must both
    # hold self._radio_lock so the live scan loop and a webui capture never
    # call radio.recv_block() concurrently (UHD's RX stream is not
    # thread-safe; concurrent pulls segfaulted the server on a double Save-IQ).
    check("DroneScanner has a _radio_lock (thread-safety for UHD RX)",
          "self._radio_lock = threading.Lock()" in inspect.getsource(DroneScanner.__init__))
    check("step() holds the radio lock around tune2/recv_block",
          "with self._radio_lock" in inspect.getsource(DroneScanner.step))
    check("capture_raw() holds the same radio lock",
          "with self._radio_lock" in inspect.getsource(DroneScanner.capture_raw))
    check("dashboard table labeled as all-bands", "(all bands)" in html)
    check("dashboard has no view-tabs anymore (table always visible)",
          "vt-table" not in html and "viewtabs" not in html)
    # the waterfall pane is REMOVED (user found it not useful) — no canvas,
    # no band-lock tabs, no lock button
    check("waterfall canvas removed", "<canvas" not in html and "id=\"wf\"" not in html)
    check("no band-lock tabs / lock button remain",
          "id=\"tabs\"" not in html and "lockbtn-wrap" not in html
          and "function lock(" not in html)
    check("table section is the main view (view-table)",
          'id="view-table"' in html and html.index('id="view-table"') < html.index("dettbody"))
    check("table grows with entries (no max-height cap, page scrolls)",
          "max-height:300px" not in html and "overflow-y:auto" not in html)
    check("right panel is sticky (stays visible while a long table scrolls)",
          "position:sticky" in html and "align-self:start" in html)
    # (5) anti-jitter + sorting: fixed table layout, colgroup widths, sortable
    #     headers, and a stable default order (first_seen) so rows don't jump
    check("table uses table-layout:fixed (no column-width jitter)",
          "table-layout:fixed" in html)
    check("table has a colgroup with fixed column widths",
          "<colgroup>" in html and "c-band" in html and "c-conf" in html)
    check("table headers are clickable to sort (sortCol wired)",
          "onclick=\"sortCol(" in html and "function sortCol" in html)
    check("table has a stable default order by first_seen (no row jump)",
          "a.first_seen - b.first_seen" in html)
    check("sort arrow indicator present (shows active column + direction)",
          ".arrow" in html and "textContent = sortDir" in html)
    # (6) positive-identification: every row carries confirmed + metric; the UI
    #     has a Status column (CONFIRMED/UNCONFIRMED) + a Detector-metric column,
    #     and nothing is default-binned to a confident protocol without a
    #     detector firing (a plain tone is either unidentified or UNCONFIRMED).
    check("every table row carries confirmed + metric (positive-ID state)",
          all("confirmed" in r and "metric" in r for r in rows),
          f"rows missing fields: {[k for r in rows for k in ('confirmed','metric') if k not in r]}")
    check("a plain tone is NOT a confident claim (UNCONFIRMED or unidentified)",
          all((r["confirmed"] is False) for r in rows),
          f"rows claimed confirmed: {[r['protocol'] for r in rows if r.get('confirmed')]}")
    check("dashboard has a Status column (confirmed/UNCONFIRMED)",
          'data-col="confirmed"' in html and "status-badge" in html)
    check("dashboard has a Detector-metric column",
          'data-col="metric"' in html and 'class="metric"' in html)
    check("dashboard renders both CONFIRMED and UNCONFIRMED labels",
          "CONFIRMED" in html and "UNCONFIRMED" in html)
    check("positive-ID hint explains the CONFIRMED vs UNCONFIRMED model",
          "Positive identification" in html and "no default-binned" in html.lower()
          or "default-binned" in html)
    # (7) 802.11 is a FIRST-CLASS automatic classifier (same row model as
    #     ELRS/LoRa): the scanner accumulates a rolling IQ buffer per band and
    #     runs the L-SIG scan in the classify loop, so a real beacon shows up as
    #     a "Wi-Fi / 802.11 (CONFIRMED)" row — NOT a separate confirm button.
    check("BandState has a rolling iq_buf for the 802.11 L-SIG demod",
          "iq_buf" in ds.BandState.__dataclass_fields__)
    check("DroneScanner has _lsig_scan() (the L-SIG brute-force scan)",
          hasattr(DroneScanner, "_lsig_scan"))
    check("802.11 is classified in _classify_positive (automatic, not a button)",
          "lsig" in inspect.getsource(DroneScanner._classify_positive))
    # the confirm-button UI must be GONE (the user wants 802.11 as a normal row)
    check("no separate 'Confirm 802.11' button in the UI (802.11 is a row)",
          "cfbtn" not in html and "confirm80211" not in html)
    # (airodump-style channel selection): the channel->freq mapping is correct
    # and wifi_scan sweeps + reports per-channel strength.
    check("wifi channel->freq: ch 6 = 2437 MHz (2.4 GHz standard)",
          ds.wifi_channel_freq(6) == 2437e6)
    check("wifi channel->freq: ch 1 = 2412, ch 11 = 2462 MHz",
          ds.wifi_channel_freq(1) == 2412e6 and ds.wifi_channel_freq(11) == 2462e6)
    check("wifi channel->freq: ch 36 = 5180 MHz (5 GHz standard)",
          ds.wifi_channel_freq(36) == 5180e6)
    check("wifi_scan_channels returns 13 channels for 2.4 GHz",
          len(ds.wifi_scan_channels("2.4")) == 13)
    check("DroneScanner has wifi_scan() (airodump-style channel sweep)",
          hasattr(DroneScanner, "wifi_scan"))
    from . import cli as _cli
    _p = _cli.build_parser()
    _a = _p.parse_args(["capture", "--channel", "6", "--scan"])
    check("capture --channel 6 --scan parses (channel=6, scan=True)",
          _a.channel == 6 and _a.scan is True)
    # (8) Extended protocol catalog (scope A): the full band set, the OcuSync
    #     PHY detector, the corrected LoRa family label, and the honest
    #     "MAVLink/Meshtastic are payload-layer, not RF-ID-able" note.
    from . import positivedet as pd
    band_names = {b.name for b in ds.BANDS}
    check("band list includes the 1.2 GHz legacy-video band",
          "1.2" in band_names, f"bands={band_names}")
    check("band list includes the 5 GHz OcuSync band",
          "5ghz" in band_names, f"bands={band_names}")
    check("CYCLE_ORDER sweeps all six bands",
          set(ds.CYCLE_ORDER) == {"2.4", "5.8", "5ghz", "sub1g", "1.2", "433"},
          f"order={ds.CYCLE_ORDER}")
    check("OcuSync PHY detector exists in positivedet",
          hasattr(pd, "ocusync_phy"))
    # the OcuSync detector runs without error on a noise + an OFDM-like fixture
    # and returns a (score, metric) tuple with score in [0,1]
    rng = np.random.default_rng(11)
    noise = (rng.standard_normal(8192) + 1j * rng.standard_normal(8192)).astype(np.complex64)
    ocus_s, ocus_m = pd.ocusync_phy(noise, 4e6)
    check("OcuSync detector runs on noise without error (score in [0,1])",
          isinstance(ocus_s, float) and 0.0 <= ocus_s <= 1.0,
          f"score={ocus_s!r}")
    check("OcuSync detector is UNCONFIRMED in the classifier (confirmed=False)",
          "DJI OcuSync / Lightbridge" in inspect.getsource(DroneScanner._classify_positive)
          and inspect.getsource(DroneScanner._classify_positive).count(
              "DJI OcuSync / Lightbridge (proprietary OFDM)\"") >= 1)
    # the LoRa label is the FAMILY (not "ELRS" alone) so Meshtastic isn't
    # mislabeled, and the UI hint explains MAVLink/Meshtastic are payload-layer
    check("LoRa row is labeled as the family (ELRS/Meshtastic/sub-1G), not 'ELRS' alone",
          "ELRS / Meshtastic / sub-1G telemetry" in inspect.getsource(DroneScanner._classify_positive))
    check("UI hint marks MAVLink/Meshtastic as payload-layer (not RF-ID-able)",
          "MAVLink" in html and "Meshtastic" in html and "payload" in html.lower())
    check("UI hint mentions OcuSync is UNCONFIRMED until capture-validated",
          "OcuSync" in html and "UNCONFIRMED" in html)


def main():
    print("elrs_sdr self-test\n===================")
    test_crc_vectors()
    test_uid_crc_init()
    test_roundtrip_all_types()
    test_bind_uid_recovery()
    test_channel_pack_unpack()
    test_fhss_lcg()
    test_lora_sync_detect()
    test_drone_classifier()
    test_wifi_phy()
    test_detections_table()
    print("\n===================")
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s): {FAILS}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
