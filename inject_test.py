#!/usr/bin/env python3
"""
inject_test.py — END-TO-END LOOPBACK TEST of the ELRS SYNC recovery path.

WHY THIS EXISTS
---------------
Two tests already pass and neither one can find the bug we actually have:

  * `lora_ref.selftest()`           — anchors the whitening LFSR to the
    gr-lora_sdr published table. Proves one primitive, in isolation.
  * `lora_ref.selftest_roundtrip()` — encode -> decode -> CRC on *symbol
    numbers held in memory*. It deliberately bypasses the radio chain, so it
    cannot see a timing, alignment, polarity or SFD error.

Everything between "symbols" and "IQ" is therefore untested, and that is
exactly the region where `ref_scan.py` fails on real air data: 2269 payload
sets, 145216 trials, 0 raw hits (notes §28).

So: build a LoRa frame from a *known* ELRS SYNC packet, turn it into IQ, bury
it in noise at a realistic SNR and offset, write it to a .complex8 capture that
is byte-identical in format to a B205 capture, and then run the **unmodified**
receive path over it:

    open_capture -> auto_snr_threshold -> find_bursts
                 -> aligned_payload_sets   (preamble lock, de-rotate, SFD skip)
                 -> lora_ref.decode_payload -> ELRS CRC

PASS  => the receive path is sound; a miss on real air is a PHY mismatch
         (SX1280-vs-SX127x) or there were no SYNC packets in that capture.
FAIL  => the bug is in OUR path, and the stage report below localises it.

SCOPE / TX GUARDRAIL (AGENTS.md)
--------------------------------
This generates IQ **in memory and to a scratch file only**. It never opens the
radio, never calls `radio.transmit`, and imports nothing from `radio` except
the capture-metadata reader. The modulator below is local to this file on
purpose: it is test scaffolding, not a transmitter.

Run:  python3 inject_test.py [--snr-db 30] [--frames 8]
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import aligned_scan as A                                  # noqa: E402
from elrs_sdr.core import lora_payload as _lp             # noqa: E402
from elrs_sdr.core import lora_ref as _ref                # noqa: E402
from elrs_sdr.core import ota as _ota                     # noqa: E402
from elrs_sdr.core.lora import _base_chirp                # noqa: E402

SF, BW = 7, 812_000
N = 1 << SF
SRATE = 9.99986e6            # the measured B205 rate from the real captures
FC = 2.4054e9
UID = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0x2E, 0xE2])
SYNC_WORD_SYMBOLS = (8, 16)  # LoRa sync word 0x12 -> nibbles 1,2 << 3
PREAMBLE_SYMBOLS = 12        # real locks ran 10-13 (notes §19a)
SFD_SYMBOLS = 2.25


# --------------------------------------------------------------------------- #
# Local modulator (test scaffolding only — see SCOPE above)
# --------------------------------------------------------------------------- #
def symbol_iq(sym: int, sf: int = SF) -> np.ndarray:
    """One LoRa symbol: the base up-chirp frequency-shifted by `sym` bins.

    NOTE — `core.lora.modulate()` cannot be used here. It applies a CONSTANT
    phase offset, `base * exp(2j*pi*sym/N)`, which does not move the dechirped
    FFT bin at all, so every symbol it emits demodulates as 0. A LoRa symbol is
    a *cyclic shift* of the chirp, i.e. a `sym`-bin frequency offset:
        x[k] = base[k] * exp(2j*pi*sym*k/N)
    Verified against `demodulate_symbols` by `_assert_modulator()` below.
    """
    n = 1 << sf
    k = np.arange(n)
    return _base_chirp(sf) * np.exp(2j * np.pi * (sym % n) * k / n)


def build_frame_iq(payload_symbols: list[int]) -> np.ndarray:
    """preamble + 2 sync symbols + 2.25-symbol SFD (down-chirps) + payload."""
    parts = [symbol_iq(0) for _ in range(PREAMBLE_SYMBOLS)]
    parts += [symbol_iq(s) for s in SYNC_WORD_SYMBOLS]
    down = np.conj(_base_chirp(SF))
    whole, frac = int(SFD_SYMBOLS), SFD_SYMBOLS - int(SFD_SYMBOLS)
    parts += [down] * whole + [down[: int(round(frac * N))]]
    parts += [symbol_iq(s) for s in payload_symbols]
    return np.concatenate(parts).astype(np.complex64)


def _assert_modulator() -> None:
    """Prove the local modulator is the exact inverse of `demodulate_symbols`."""
    from elrs_sdr.core.lora import LoRaParams, demodulate_symbols
    want = [0, 1, 5, 42, 99, 127]
    iq = np.concatenate([symbol_iq(s) for s in want])
    p = LoRaParams(sf=SF, bw_hz=BW, sample_rate=float(BW), invert_iq=False)
    got = [int(v) for v in demodulate_symbols(iq, p, 0, len(want))]
    assert got == want, f"modulator/demodulator mismatch: {got} != {want}"


# --------------------------------------------------------------------------- #
# Capture synthesis
# --------------------------------------------------------------------------- #
def make_capture(path: str, n_frames: int, snr_db: float,
                 offset_hz: float = 1.2e6, gap_s: float = 0.020) -> list[dict]:
    """Write a .complex8 + .meta.txt holding `n_frames` synthetic ELRS SYNC
    frames in Gaussian noise. Returns the ground truth for each frame."""
    rng = np.random.default_rng(1234)
    total = int(SRATE * (gap_s * (n_frames + 1)))
    # noise floor first, then frames added on top at the requested SNR
    noise_sigma = 0.02
    buf = (rng.normal(0, noise_sigma, total)
           + 1j * rng.normal(0, noise_sigma, total)).astype(np.complex64)

    truth = []
    opts = _ref.RefOpts(cr=1, ldro=False, msb_first=True, whiten=True)
    for i in range(n_frames):
        # vary (fhss_index, nonce) per frame so the independence gate can pass
        raw = _ota.build_sync(is_full=True, fhss_index=(i * 7) % 256,
                              nonce=(i * 13) % 256, rf_rate_enum=3,
                              uid4=UID[4], uid5=UID[5],
                              crc_init=_ota.ota_uid_crc_init(UID, 3),
                              ota_nonce=0)
        syms = _ref.encode_payload(raw, SF, opts)
        frame = build_frame_iq(syms)
        # 812 kHz -> SRATE, then shift off DC (the real link is never at DC,
        # and find_bursts blanks +-100 kHz around it)
        up = _pd_resample(frame, int(round(len(frame) * SRATE / BW)))
        t = np.arange(len(up)) / SRATE
        up = up * np.exp(2j * np.pi * offset_hz * t)
        amp = noise_sigma * np.sqrt(2) * 10 ** (snr_db / 20.0)
        up = (up / (np.sqrt(np.mean(np.abs(up) ** 2)) + 1e-12)) * amp
        a = int(SRATE * gap_s * (i + 0.5))
        buf[a:a + len(up)] += up.astype(np.complex64)
        truth.append({"start": a, "fhss_index": (i * 7) % 256,
                      "nonce": (i * 13) % 256, "n_payload_symbols": len(syms),
                      "payload_symbols": syms, "raw": raw.hex()})

    peak = float(np.max(np.abs(np.concatenate([buf.real, buf.imag]))))
    inter = np.empty(2 * total, dtype=np.int16)
    scale = 32000.0 / max(peak, 1e-9)
    inter[0::2] = np.clip(buf.real * scale, -32767, 32767).astype(np.int16)
    inter[1::2] = np.clip(buf.imag * scale, -32767, 32767).astype(np.int16)
    inter.tofile(path)
    meta = os.path.splitext(path)[0] + ".meta.txt"
    Path(meta).write_text(
        "# elrs-sdr SYNTHETIC loopback capture (inject_test.py) — NOT off-air\n"
        f"center_freq_hz: {FC:.6g}\n"
        f"sample_rate_hz: {SRATE:.6g}\n"
        f"n_samples: {total}\n"
        "format: int16 I,Q interleaved, little-endian\n"
        f"note: {n_frames} synthetic ELRS SYNC frames, uid4=0x{UID[4]:02x} "
        f"uid5=0x{UID[5]:02x}, SNR {snr_db:.0f} dB, offset {offset_hz/1e6:.2f} MHz\n")
    return truth


def _pd_resample(x, n):
    from elrs_sdr import positivedet as _pd
    return _pd.fft_resample(x, n)


# --------------------------------------------------------------------------- #
# Stage-by-stage report over the REAL receive path
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snr-db", type=float, default=30.0)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--keep", action="store_true", help="keep the scratch capture")
    args = ap.parse_args()

    print("=== stage 0: modulator is the inverse of the demodulator ===")
    _assert_modulator()
    print("  [PASS] symbol_iq -> demodulate_symbols is exact for 6 test symbols")

    print("\n=== stage 1: payload algebra (encode -> decode, in memory) ===")
    _ref.selftest()
    _ref.selftest_roundtrip()

    tmpdir = tempfile.mkdtemp(prefix="inject_", dir=os.environ.get(
        "SCRATCH_DIR", tempfile.gettempdir()))
    cap = os.path.join(tmpdir, "inject.complex8")
    print(f"\n=== stage 2: synthesise capture ({args.frames} frames, "
          f"SNR {args.snr_db:.0f} dB) ===")
    truth = make_capture(cap, args.frames, args.snr_db)
    print(f"  wrote {cap} ({os.path.getsize(cap)/1e6:.1f} MB), "
          f"{len(truth)} frames, {truth[0]['n_payload_symbols']} payload symbols each")

    print("\n=== stage 3: find_bursts (real energy detector) ===")
    mm, sr, fc, n = A.open_capture(cap)
    thr = A.auto_snr_threshold(mm, sr, n)
    bursts = A.find_bursts(mm, sr, n, snr_min=thr)
    print(f"  snr_gate={thr:.1f} dB -> {len(bursts)} bursts "
          f"(injected {len(truth)})")
    for (a, cen) in bursts[:args.frames]:
        near = min(truth, key=lambda t: abs(t["start"] - a))
        print(f"    burst @{a} ({a/sr*1e3:7.2f} ms) cen={cen/1e6:+.3f} MHz "
              f"| nearest injected @{near['start']} "
              f"(dt={(a-near['start'])/sr*1e3:+.2f} ms)")
    if not bursts:
        print("  [FAIL] stage 3 — the energy detector did not see the frames.")
        return 1
    print(f"  [{'PASS' if len(bursts) >= len(truth) * 0.5 else 'WEAK'}] "
          f"detector recall {len(bursts)}/{len(truth)}")

    print("\n=== stage 4: aligned_payload_sets (preamble lock, de-rotate, SFD) ===")
    sets = A.aligned_payload_sets(mm, sr, bursts, n)
    print(f"  {len(bursts)} bursts -> {len(sets)} payload symbol sets "
          f"({len(A.SFD_CANDIDATES)} SFD guesses each => "
          f"{len(sets)/max(1,len(A.SFD_CANDIDATES)):.0f} locked bursts)")
    if not sets:
        print("  [FAIL] stage 4 — no preamble lock. The bug is in the "
              "preamble/alignment stage, not the payload chain.")
        return 1

    # how close is any recovered set to the symbols we actually transmitted?
    want = truth[0]["payload_symbols"]
    best = (0.0, -1)
    for i, s in enumerate(sets):
        k = min(len(s), len(want))
        if k < 5:
            continue
        frac = sum(1 for j in range(k) if s[j] == want[j]) / k
        if frac > best[0]:
            best = (frac, i)
    print(f"  best symbol-level agreement with the injected payload: "
          f"{best[0]*100:.0f}%  (set #{best[1]})")
    if best[1] >= 0:
        k = min(12, len(sets[best[1]]), len(want))
        print(f"    injected : {want[:k]}")
        print(f"    recovered: {sets[best[1]][:k]}")
    # Judge this stage on >=50%, not on perfection. Our encoder leaves the
    # Hamming parity bits zero (see `hamming_encode_nibble`) and our decoder
    # ignores parity, so the chain has NO error correction: a single wrong
    # symbol kills that frame's CRC. Partial symbol agreement is therefore
    # expected, and the CRC in stage 5 is the real arbiter.
    print(f"  [{'PASS' if best[0] > 0.5 else 'FAIL'}] symbol recovery "
          f"(this is the stage the §28d round-trip could not test; the CRC in "
          f"stage 5 is the arbiter)")

    print("\n=== stage 5: reference chain + ELRS CRC + §24 gates ===")
    import ref_scan
    hits, corroborated, rep = ref_scan.recover(sets)
    for k_, v in rep.items():
        print(f"  {k_}: {v}")
    got = [h for h in hits if h.uid4 == UID[4] and h.uid5 == UID[5]]
    print(f"  hits matching the INJECTED uid 2ee2: {len(got)}")
    print(f"  corroborated: {len(corroborated)}")
    for h in corroborated[:5]:
        print(f"    net_id={h.net_id} uid4=0x{h.uid4:02x} uid5=0x{h.uid5:02x} "
              f"OTAv{h.ota_version} fhss={h.fhss_index} nonce={h.nonce} "
              f"opts={h.opts}")

    ok = bool(got)
    print(f"\n{'[PASS]' if ok else '[FAIL]'} end-to-end: the known ELRS SYNC "
          f"packet was {'recovered' if ok else 'NOT recovered'} through the "
          f"unmodified receive path.")
    if not args.keep:
        for f in Path(tmpdir).iterdir():
            f.unlink()
        os.rmdir(tmpdir)
    else:
        print(f"  capture kept at {cap}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
