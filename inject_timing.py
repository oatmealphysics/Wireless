#!/usr/bin/env python3
"""
inject_timing.py — is the ~50% frame loss a FRACTIONAL TIMING (STO) defect?

`inject_sweep.py` showed the recovery rate plateaus at ~5-6 of 12 injected
frames and does NOT improve as SNR rises from 8 dB to 45 dB. An SNR-independent
loss is deterministic, so it is a defect in the chain, not noise.

Leading suspect: there is no fractional-sample timing recovery anywhere in the
path. `find_bursts` quantises each burst start to its FFT hop (0.4 ms!), and
`aligned_payload_sets` then recovers only an INTEGER-symbol boundary (the
preamble run) plus an integer bin de-rotation. The residual sub-sample offset
smears each dechirp window across two FFT bins, producing the +-1 bin symbol
errors seen in `inject_test` stage 4 (34 -> 35). With the Hamming parity left
zero there is no error correction, so one bad symbol kills the whole frame.

This test injects the SAME frame at a swept sub-sample delay and measures
symbol accuracy as a function of that delay alone. SNR is held high (40 dB) so
noise cannot confound it.

  * accuracy high at delay~0 and collapsing toward delay~0.5 sample
        => CONFIRMED: fractional timing recovery is the missing piece.
  * accuracy flat across all delays
        => REFUTED: the loss is elsewhere (look at the SFD/preamble boundary).

Run:  python3 inject_timing.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import inject_test as T                                        # noqa: E402
from elrs_sdr import positivedet as _pd                        # noqa: E402
from elrs_sdr.core import lora_ref as _ref                     # noqa: E402
from elrs_sdr.core import lora_payload as _lp                  # noqa: E402
from elrs_sdr.core import ota as _ota                          # noqa: E402
from elrs_sdr.core.lora import LoRaParams, demodulate_symbols  # noqa: E402

SF, BW, N = T.SF, T.BW, T.N
OPTS = _ref.RefOpts(cr=1, ldro=False, msb_first=True, whiten=True)


def frac_delay(x: np.ndarray, d: float) -> np.ndarray:
    """Delay `x` by `d` samples (d in [0,1)) with an exact FFT phase ramp."""
    n = len(x)
    f = np.fft.fftfreq(n)
    return np.fft.ifft(np.fft.fft(x) * np.exp(-2j * np.pi * f * d))


def run_one(delay: float, want: list[int], frame: np.ndarray) -> tuple[float, bool]:
    """Apply a sub-sample delay at the 812 kHz chip rate, then demod + decode.

    Deliberately skips `find_bursts`: we hand the aligner a PERFECT integer
    symbol boundary, so the only error source left is the fractional delay.
    """
    x = frac_delay(frame, delay)
    pre_syms = T.PREAMBLE_SYMBOLS + 2          # preamble + 2 sync-word symbols
    start = int(round((pre_syms + T.SFD_SYMBOLS) * N))
    p = LoRaParams(sf=SF, bw_hz=BW, sample_rate=float(BW), invert_iq=False)
    got = [int(v) for v in demodulate_symbols(x, p, start, len(want))]
    k = min(len(got), len(want))
    acc = sum(1 for j in range(k) if got[j] == want[j]) / max(1, k)
    data = _ref.decode_payload(got, SF, OPTS, max_bytes=13)
    crc_ok = any(h.uid4 == T.UID[4] and h.uid5 == T.UID[5]
                 for h in _lp._try_sync(data, True))
    return acc, crc_ok


def main() -> int:
    raw = _ota.build_sync(is_full=True, fhss_index=42, nonce=7, rf_rate_enum=3,
                          uid4=T.UID[4], uid5=T.UID[5],
                          crc_init=_ota.ota_uid_crc_init(T.UID, 3), ota_nonce=0)
    want = _ref.encode_payload(raw, SF, OPTS)
    frame = T.build_frame_iq(want)
    print(f"one known ELRS SYNC frame, {len(want)} payload symbols, "
          f"noise-free, perfect integer symbol boundary.\n")
    print(f"{'delay (chips)':>14} {'symbol acc':>11} {'CRC':>6}")
    print("-" * 34)
    rows = []
    for d in np.arange(0.0, 1.0, 0.0625):
        acc, ok = run_one(float(d), want, frame)
        rows.append((float(d), acc, ok))
        print(f"{d:>14.4f} {acc*100:>10.0f}% {'PASS' if ok else 'fail':>6}")

    accs = [a for _, a, _ in rows]
    print(f"\naccuracy at delay 0.0000: {accs[0]*100:.0f}%")
    print(f"worst accuracy over the sweep: {min(accs)*100:.0f}% "
          f"(at delay {rows[int(np.argmin(accs))][0]:.4f})")
    n_crc = sum(1 for _, _, ok in rows if ok)
    print(f"CRC passes: {n_crc}/{len(rows)} delay offsets")

    if accs[0] > 0.95 and min(accs) < 0.9:
        print("\nCONFIRMED: symbol accuracy depends on the sub-chip delay "
              "alone, with no noise present.")
        print("=> The missing piece is FRACTIONAL TIMING RECOVERY. The path "
              "resolves the symbol boundary only to the nearest whole chip,")
        print("   so roughly half of all frames land near the worst-case "
              "offset and lose symbols to inter-bin smear.")
        print("   Two independent fixes, both of which raise real-air yield:")
        print("     1. estimate the residual STO from the preamble's "
              "dechirped phase slope and correct it before the payload demod;")
        print("     2. use the Hamming parity bits for real single-bit "
              "correction instead of discarding them.")
    elif min(accs) >= 0.9:
        print("\nREFUTED: accuracy is flat across sub-chip delays, so timing "
              "is not the ~50% loss. Look at the SFD/preamble boundary next.")
    else:
        print("\nINCONCLUSIVE: accuracy is poor even at delay 0 — the "
              "noise-free baseline itself is broken; fix that first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
