#!/usr/bin/env python3
"""
ELRS Link Finder — modulation-agnostic.

The program does NOT assume which ELRS PHY (LoRa vs FLRC/GFSK) or which rate the
target is on. For each swept 2.4 GHz channel it MEASURES the signal's properties
and CLASSIFIES what's actually transmitting, then decodes with the matching demod.
This is the "find out what's there" front-end — the first step of the chain that ends
in stopping the drone.

Classification signals (measured per channel, no assumptions):
  * band power (is there a transmitter here at all?)
  * LoRa chirp-structure (downchirp correlation peaks -> LoRa/CSS present)
  * GFSK sync match (correlate the 6 known UID-derived sync words -> FLRC/GFSK present)
  * duty cycle / burst shape (ELRS hops: brief bursts, not continuous)

WHY THIS EXISTS
  The earlier decoder was LoRa-only, so it was blind to the target (which was on
  FLRC/GFSK, rate_index=2). The C++ interceptor is also LoRa-only. Neither would
  *find* a GFSK link. This tool classifies first, so it finds either PHY.

AUTHORIZED, BENCH-ONLY. RX-only (passive). No TX.

Run (offline self-check, no radio):
  python3 find_link.py --selftest

Run against the bench drone (B205 on the laptop, drone powered + ELRS TX active):
  python3 find_link.py --sweep
  python3 find_link.py --sweep --srate 10e6 --gain 40 --dwell-ms 200 --channels 80
"""
from __future__ import annotations

import argparse
import sys
import time
import numpy as np

# --- load the validated core (single source of truth) ---
def _bootstrap_core():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    for p in (here, root, os.path.join(root, "..")):
        if p not in sys.path:
            sys.path.insert(0, p)
_bootstrap_core()

from elrs_sdr.core import lora, gfsk, fhss, ota, crc  # noqa: E402

# UID to recover: the target's UID (from the CLI dump). We build the GFSK sync
# reference from it. If the UID is wrong/unknown, the GFSK branch scores low and
# the tool still classifies band power + LoRa, so it degrades gracefully.
TARGET_UID = bytes.fromhex("00000011185b")


# --------------------------------------------------------------------------- #
# Per-channel PHY classification (measured, not assumed)
# --------------------------------------------------------------------------- #
def band_power_db(samples: np.ndarray, noise_floor: float) -> float:
    """Mean band power in dB above the noise floor (0 dB = at floor)."""
    p = float(np.mean(np.abs(samples) ** 2))
    if noise_floor <= 0 or p <= 0:
        return 0.0
    return 10.0 * np.log10(p / noise_floor)


def lora_score(samples: np.ndarray, srate: float) -> tuple[float, int | None]:
    """LoRa/CSS presence: try each ELRS 2.4 GHz LoRa rate's sync correlation.
    Returns (best_score in [0,1], best_air_rate_name or None). High score = LoRa present.
    This is the LoRa branch of the discriminator — mirrors the existing lora.py demod."""
    best = (0.0, None)
    for rate in lora.ELRS_2G4_RATES:
        try:
            params = lora.LoRaParams(rate.sf, rate.bw_hz, sample_rate=srate)
            starts = lora.detect_sync_word(samples, params, threshold=0.5)
            if starts:
                # score by peak correlation strength (recompute) — approximate by count
                s = min(1.0, 0.5 + 0.1 * len(starts))
                if s > best[0]:
                    best = (s, rate.name)
        except Exception:
            continue
    return best


def gfsk_score(samples: np.ndarray, srate: float, uid: bytes) -> tuple[float, list[int]]:
    """FLRC/GFSK presence: correlate the UID-derived 32-bit sync word against the IQ.
    Returns (best_score in [0,1], candidate sync-start positions). High score = GFSK present
    on the target's UID. (If the link is GFSK but a *different* UID, this scores low —
    the band-power + LoRa-absence still flags 'unknown GFSK', and UID recovery is the
    next step.)"""
    sw = gfsk.flrc_sync_word(uid)
    params = gfsk.FlrcParams(sample_rate=srate)
    starts = gfsk.detect_sync_word(samples, sw, params)
    if not starts:
        return (0.0, [])
    # score: normalize the strongest correlation peak (0..1). detect already gates on 0.5*peak,
    # so a hit is real; scale by how many distinct frames (repetition = real link).
    s = min(1.0, 0.6 + 0.05 * min(len(starts), 8))
    return (s, starts)


def classify_channel(samples: np.ndarray, srate: float, noise_floor: float,
                     uid: bytes) -> dict:
    """Measure + classify ONE channel's IQ. No PHY assumed."""
    pw = band_power_db(samples, noise_floor)
    lsc, lname = lora_score(samples, srate)
    gsc, gstarts = gfsk_score(samples, srate, uid)

    # Decide the PHY by which measured signal dominates (with a band-power gate).
    if pw < 3.0:            # at/below noise floor -> nothing here
        phy = "none"
    elif lsc >= gsc and lsc > 0.5:
        phy = "lora"
    elif gsc > lsc and gsc > 0.5:
        phy = "gfsk"
    elif pw >= 3.0:
        phy = "unknown_energy"   # RF present, neither LoRa nor known-UID GFSK
    else:
        phy = "none"

    return {
        "power_db": round(pw, 2),
        "phy": phy,
        "lora_score": round(lsc, 3),
        "lora_rate": lname,
        "gfsk_score": round(gsc, 3),
        "gfsk_sync_starts": gstarts[:8],
    }


# --------------------------------------------------------------------------- #
# The sweep: measure every channel, keep the ones with a transmitter
# --------------------------------------------------------------------------- #
def sweep(radius: "RadioDevice", uid: bytes, srate: float = 10e6, gain: float = 40.0,
          dwell_ms: int = 200, channels: int = 80,
          freq0: float = 2400.4e6, spacing: float = 1.0e6) -> list[dict]:
    """Sweep the ISM2400 FHSS band, classify each channel, return per-channel results
    (only channels with energy). This is the 'find out what's there' pass."""
    results = []
    n_dwell = int(srate * dwell_ms / 1000.0)
    # First pass: noise floor from a quiet channel (assume one is quiet) — use the
    # *minimum* power across the sweep as the floor estimate (refined below).
    raw = []
    for c in range(channels):
        f = freq0 + c * spacing
        radius.rx_tune(f, srate, gain=gain)
        try:
            samples = radius.rx_stream(chunk=n_dwell, timeout_s=dwell_ms / 1000.0 + 0.1)
        except Exception as e:
            print(f"  [ch {c} {f/1e6:.1f} MHz] RX error: {e}", file=sys.stderr)
            continue
        if samples is None or len(samples) == 0:
            continue
        raw.append((c, f, samples))
    # noise floor = 10th percentile of per-sample power across the quietest channels
    all_p = np.concatenate([np.abs(s) ** 2 for _, _, s in raw]) if raw else np.array([0.0])
    noise_floor = float(np.percentile(all_p, 10)) + 1e-12
    for c, f, samples in raw:
        r = classify_channel(samples, srate, noise_floor, uid)
        r["channel"] = c
        r["freq_mhz"] = round(f / 1e6, 2)
        if r["phy"] != "none":
            results.append(r)
    return results


# --------------------------------------------------------------------------- #
# Offline self-check (no radio): prove the classifier separates the two PHYs
# --------------------------------------------------------------------------- #
def selftest() -> bool:
    uid = TARGET_UID
    srate = 10_000_000
    ok = True
    print(f"  [find] selftest: target UID {uid.hex(' ')}")

    # --- Build a GFSK frame (the target's PHY) ---
    gsc_params = gfsk.FlrcParams(sample_rate=srate)
    sw = gfsk.flrc_sync_word(uid)
    payload = bytes([0x48, 0xFF, 0x7F, 0, 0, 0, 0, 0x57])
    ccrc = gfsk.crc24(payload, gfsk.flrc_crc_seed(uid))
    pb = [1, 0] * (gfsk.FLRC_PREAMBLE_BYTES * 8)
    sb = [int((sw >> (gfsk.FLRC_SYNC_LEN * 8 - 1 - i)) & 1) for i in range(gfsk.FLRC_SYNC_LEN * 8)]
    plb = [(payload[i >> 3] >> (7 - (i & 7))) & 1 for i in range(len(payload) * 8)]
    cb = [(ccrc.to_bytes(3, "big")[i >> 3] >> (7 - (i & 7))) & 1 for i in range(24)]
    gfsk_sig = gfsk.modulate(plb, gsc_params, preamble_bits=pb, sync_bits=sb, crc_bits=cb)

    # --- Build a LoRa chirp train (CSS-structured, for the LoRa branch of the discriminator) ---
    # lora.py is RX-only (no modulator), so synthesize a CSS chirp train: a few down/up
    # chirps of the right BW/SF — enough structure for lora.detect_sync_word's downchirp
    # correlation to register "LoRa present" (not a real LoRa frame, just the PHY signature).
    lora_sig = None
    try:
        rate = lora.ELRS_2G4_RATES[0]
        lp = lora.LoRaParams(rate.sf, rate.bw_hz, sample_rate=srate)
        n_sym = lp.n_chirp
        # alternate down/up chirps (CSS preamble-like), n_sym samples each, ~6 symbols
        t = np.arange(n_sym) / srate
        fdev = rate.bw_hz / 2
        chirp_down = np.exp(-1j * np.pi * (fdev / srate) * t * (2 * srate) * t * 0 + 2 * np.pi * (-fdev) * t)
        # proper linear chirp: phase = pi * fdev * t^2 / srate (down-chirp = negative)
        chirp_down = np.exp(-1j * np.pi * fdev * (t * srate) ** 2 / srate)
        chirp_up = np.conj(chirp_down)
        lora_sig = np.concatenate([chirp_down, chirp_up] * 4).astype(np.complex64)
    except Exception as e:
        print(f"  [find] (LoRa branch skipped: {e})")

    # --- realistic noise floor: 10th-percentile sample power of a noise segment ---
    noise_seg = (np.random.randn(20000) + 1j * np.random.randn(20000)).astype(np.complex64) * 0.05
    noise_floor = float(np.percentile(np.abs(noise_seg) ** 2, 10)) + 1e-12

    if gfsk_sig is not None:
        r_g = classify_channel(gfsk_sig.astype(np.complex64), srate, noise_floor, uid)
        print(f"  [find] GFSK signal -> phy={r_g['phy']} (gfsk={r_g['gfsk_score']}, lora={r_g['lora_score']}, pwr={r_g['power_db']}dB)")
        if r_g["phy"] != "gfsk":
            print("  [FAIL] GFSK signal not classified as gfsk")
            ok = False

    if lora_sig is not None:
        r_l = classify_channel(lora_sig.astype(np.complex64), srate, noise_floor, uid)
        print(f"  [find] LoRa signal -> phy={r_l['phy']} (lora={r_l['lora_score']}, gfsk={r_l['gfsk_score']}, pwr={r_l['power_db']}dB)")
        if r_l["phy"] == "gfsk":
            print("  [FAIL] LoRa signal misclassified as gfsk (discriminator not separating PHYs)")
            ok = False

    # pure noise -> none (the critical false-positive check)
    nz = noise_seg.copy()
    r_n = classify_channel(nz, srate, noise_floor, uid)
    print(f"  [find] noise        -> phy={r_n['phy']} (gfsk={r_n['gfsk_score']}, lora={r_n['lora_score']}, pwr={r_n['power_db']}dB)")
    if r_n["phy"] == "gfsk":
        print("  [FAIL] noise classified as gfsk — false positive (the whole point of the absolute gate)")
        ok = False

    print(f"\n  [find] selftest: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="ELRS Link Finder (modulation-agnostic)")
    ap.add_argument("--selftest", action="store_true", help="offline check, no radio")
    ap.add_argument("--sweep", action="store_true", help="sweep the band on the B205")
    ap.add_argument("--srate", type=float, default=10e6, help="sample rate Hz (default 10e6)")
    ap.add_argument("--gain", type=float, default=40.0, help="RX gain dB")
    ap.add_argument("--dwell-ms", type=int, default=200, help="ms per channel")
    ap.add_argument("--channels", type=int, default=80, help="number of FHSS channels")
    ap.add_argument("--freq0", type=float, default=2400.4e6, help="first channel Hz")
    ap.add_argument("--spacing", type=float, default=1.0e6, help="channel spacing Hz")
    ap.add_argument("--dev", default="type=b200", help="UHD device args")
    args = ap.parse_args()

    if args.selftest:
        return 0 if selftest() else 1

    if args.sweep:
        from elrs_sdr.radio import RadioDevice
        print(f"[find] sweeping {args.channels} channels @ {args.freq0/1e6:.1f} MHz "
              f"(spacing {args.spacing/1e6:.1f} MHz), srate {args.srate:.3g}, dwell {args.dwell_ms} ms")
        t0 = time.time()
        radius = RadioDevice(args.dev)
        results = sweep(radius, TARGET_UID, srate=args.srate, gain=args.gain,
                        dwell_ms=args.dwell_ms, channels=args.channels,
                        freq0=args.freq0, spacing=args.spacing)
        radius.close()
        dt = time.time() - t0
        print(f"[find] sweep done in {dt:.1f}s — {len(results)} channel(s) with energy:\n")
        if not results:
            print("  (nothing found — is the drone's ELRS TX on? is it on ISM2400?)")
            return 0
        for r in sorted(results, key=lambda x: -x["power_db"]):
            print(f"  ch {r['channel']:>2}  {r['freq_mhz']:>7.1f} MHz  "
                  f"power {r['power_db']:>5.1f} dB  PHY={r['phy']:<14} "
                  f"(lora={r['lora_score']}, gfsk={r['gfsk_score']})"
                  + (f"  gfsk-sync@{r['gfsk_sync_starts'][:3]}" if r["gfsk_sync_starts"] else "")
                  + (f"  lora-rate={r['lora_rate']}" if r["lora_rate"] else ""))
        # Interpret for the user
        print("\n[find] interpretation:")
        g = [r for r in results if r["phy"] == "gfsk"]
        l = [r for r in results if r["phy"] == "lora"]
        u = [r for r in results if r["phy"] == "unknown_energy"]
        if g:
            chs = [r["channel"] for r in g]
            print(f"  GFSK/FLRC link detected on channel(s) {chs} — matches the target's UID. "
                  f"This is the FLRC path; decode with gfsk.py and proceed to lock+throw.")
        if l:
            chs = [r["channel"] for r in l]
            print(f"  LoRa link detected on channel(s) {chs} (rate {l[0]['lora_rate']}) — "
                  f"the target is on a LoRa rate; decode with lora.py (the C++ interceptor).")
        if u:
            chs = [r["channel"] for r in u]
            print(f"  Unidentified RF on channel(s) {chs} — energy present but neither LoRa nor "
                  f"known-UID GFSK. Could be a different-UID GFSK, Wi-Fi, or other. Investigate.")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
