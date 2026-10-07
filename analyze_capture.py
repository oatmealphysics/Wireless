#!/usr/bin/env python3
"""analyze_capture.py — run the full detection on an ELRS Find captures/ dir.

Offline re-analysis of a real bench capture (the .complex8 int16 I/Q files +
per-channel .meta.txt that sweep_phy saves). For every channel:
  - reads the REAL per-channel sample rate from the .meta (skips the
    USB-bandwidth-corrupt channels that were captured at a wrong rate)
  - applies the DC-block (elrs.dc_block)
  - runs the GFSK sync detector (target UID) + the LoRa preamble detector (all
    6 ELRS rates)
  - measures duty cycle + the DC-removed band-limited spectrum shape
and prints a per-channel table + a verdict. This is the tool I run the moment
the user uploads a fresh captures/ dir.

Usage:
    python3 analyze_capture.py <captures_dir> [uid_hex]
    # e.g. python3 analyze_capture.py waveraider/captures/elrs_find_20261007_182028
    #      python3 analyze_capture.py /path/to/captures 00000011185b
"""
import sys, os, re, json, glob
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'waveraider'))

from elrs_sdr.core import gfsk, lora
from waveraider.protocols import elrs

# channels captured well below 80% of the nominal 10 MS/s are USB-bandwidth
# corrupt (wrong rate -> timing is wrong -> demod is garbage). Skip them.
CORRUPT_FRAC = 0.8


def read_meta(meta_path):
    """Parse the per-channel .meta.txt (sample rate, freq, n_samples)."""
    out = {}
    with open(meta_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('#') or ':' not in line:
                continue
            k, v = line.split(':', 1)
            out[k.strip()] = v.strip()
    return out


def read_iq(c8_path):
    """Read the .complex8 file as int16 I/Q interleaved -> complex64 array."""
    raw = np.fromfile(c8_path, dtype=np.int16)
    iq = raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)
    return iq


def gfsk_corr_peak(iq, sync_word, srate):
    """Normalized GFSK sync-word correlation peak (target-UID). 0..1."""
    params = gfsk.FlrcParams(sample_rate=srate)
    ref = gfsk.modulate([], params, preamble_bits=[],
                        sync_bits=[int((sync_word >> (31 - i)) & 1) for i in range(32)])
    if len(ref) == 0 or len(ref) > len(iq):
        return 0.0
    corr = np.abs(np.correlate(iq, ref.astype(np.complex128), mode='valid'))
    denom = np.sqrt(np.sum(np.abs(iq) ** 2) * np.sum(np.abs(ref) ** 2)) + 1e-12
    return float(corr.max() / denom)


def lora_hits(iq, srate, rate_name):
    """LoRa presence for one ELRS rate, using the SAME detector the live
    classify_phy uses (a boolean 'any preamble at this rate', threshold=0.5).
    Returns the number of rates (out of 6) that show a preamble — 0 means no LoRa.
    (The raw autocorrelation count is too loose on broadband noise, so we count
    *rates* with a preamble hit, like the classifier, not raw positions.)"""
    r = lora.ELRS_2G4_RATES[rate_name]
    p = lora.LoRaParams(sf=r.sf, bw_hz=r.bw_hz, sample_rate=srate)
    try:
        return 1 if lora.detect_sync_word(iq, p, threshold=0.5) else 0
    except Exception:
        return 0


def lora_rate_hits(iq, srate, uid):
    """CRC-VALID LoRa frames across the 6 ELRS rates, via the real _decode_lora
    path (the same decode the live 'auto' mode uses). NOTE: a small number of
    CRC-valid frames on a noise-only channel is EXPECTED (CRC-24 false-accept +
    the first-order LoRa demod) — a REAL LoRa link shows a consistent stream
    (hundreds+ of frames), not 20-35. So treat low counts (< ~100) as
    'possible noise, not a confirmed link'; the GFSK sync (the primary
    identifier for a rate_index=2 target) is the load-bearing signal."""
    total = 0
    for rn in lora.ELRS_2G4_RATES:
        try:
            total += len(elrs._decode_lora(iq, srate, rn, uid))
        except Exception:
            pass
    return total


def duty_cycle(iq):
    """Fraction of samples above 2x the p10 power (ELRS is ~10-30%; >0.6 = continuous)."""
    p = np.abs(iq) ** 2
    thresh = 2 * np.percentile(p, 10)
    return float((p > thresh).mean())


def spectrum_shape(iq, srate, n=2 ** 16):
    """peak/mean of the DC-removed, band-limited (100 kHz - 4.5 MHz) envelope
    spectrum. A narrowband signal -> high ratio; flat noise -> ~3-5x."""
    seg = np.abs(iq[:min(n, len(iq))]).astype(np.float64)
    seg = seg - seg.mean()
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1.0 / srate)
    band = spec[(f > 100e3) & (f < 4.5e6)]
    if band.size == 0:
        return 0.0, 0.0
    return float(band.max() / (band.mean() + 1e-12)), float(f[(f > 100e3) & (f < 4.5e6)][band.argmax()] / 1e6)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cap = sys.argv[1]
    uid_hex = sys.argv[2] if len(sys.argv) > 2 else '00000011185b'
    uid = bytes.fromhex(uid_hex)
    sync = gfsk.flrc_sync_word(uid)
    print(f"ELRS Find capture analysis  |  UID {uid.hex()}  |  sync {sync:#010x}")
    print(f"dir: {cap}")
    print("=" * 100)
    header = (f"{'ch':>3} {'freq_MHz':>9} {'srate':>8} {'status':>9} {'GFSK':>7} "
              f"{'LoRa_fr':>8} {'duty':>6} {'specPk':>7} {'specFreq':>9}")
    print(header)
    print("  (GFSK = target-UID sync corr, gate 0.10 | LoRa_fr = CRC-valid frames, >=100 = real, "
          "~20-35 = noise false-accept)")
    print("-" * 100)

    c8_files = sorted(glob.glob(os.path.join(cap, 'ch*.complex8')),
                      key=lambda p: int(re.search(r'ch(\d+)', p).group(1)))
    gfsk_channels = []
    lora_channels = []
    for c8 in c8_files:
        ch = int(re.search(r'ch(\d+)', c8).group(1))
        meta_path = c8.replace('.complex8', '.meta.txt')
        meta = read_meta(meta_path) if os.path.exists(meta_path) else {}
        srate = float(meta.get('sample_rate_hz', 10e6))
        freq_mhz = float(meta.get('center_freq_hz', 0)) / 1e6
        # USB-bandwidth-corrupt channel (captured at wrong rate)
        if srate < CORRUPT_FRAC * 10e6:
            print(f"{ch:>3} {freq_mhz:>9.1f} {srate/1e6:>7.2f}M {'CORRUPT':>9} "
                  f"{'-':>7} {'-':>5} {'-':>6} {'-':>7} {'-':>9}")
            continue
        iq = read_iq(c8)
        iq = elrs.dc_block(iq)  # the DC-block (matches the live path)
        g = gfsk_corr_peak(iq, sync, srate)
        # LoRa: CRC-valid frames via the real decode path (low = noise, high = real)
        lora_frames = lora_rate_hits(iq, srate, uid)
        duty = duty_cycle(iq)
        spk, sfreq = spectrum_shape(iq, srate)
        # classification: GFSK sync (>=0.10) is the load-bearing signal for a
        # rate_index=2 target. LoRa is only "confirmed" at a high frame count
        # (a real link = hundreds+; ~20-35 = CRC false-positives on noise).
        if g >= 0.10:
            phy = "GFSK ✓"
            gfsk_channels.append((ch, freq_mhz, g))
        elif lora_frames >= 100:
            phy = "LoRa ✓"
            lora_channels.append((ch, freq_mhz, lora_frames))
        elif duty > 0.6 and spk < 20:
            phy = "broadband"
        else:
            phy = "unknown"
        print(f"{ch:>3} {freq_mhz:>9.1f} {srate/1e6:>7.2f}M {phy:>9} {g:>7.4f} "
              f"{lora_frames:>6} {duty:>6.3f} {spk:>7.1f}x {sfreq:>8.2f}M")
    print("-" * 100)
    # manifest (if present)
    mf = os.path.join(cap, 'manifest.json')
    if os.path.exists(mf):
        with open(mf) as f:
            m = json.load(f)
        print(f"\nmanifest: {m.get('srate_hz','?')} Hz, {m.get('channels','?')} ch, "
              f"dwell {m.get('dwell_ms','?')} ms, gain {m.get('gain_db','?')} dB, "
              f"uid {m.get('uid','?')}")
        hits = [c for c in m.get('channels', []) if c.get('phy') not in ('none', None)]
        print(f"manifest non-none channels: {[(c.get('channel'), c.get('freq_mhz'), c.get('phy')) for c in hits] or 'none'}")

    # VERDICT
    print("\n" + "=" * 100)
    print("VERDICT")
    print("=" * 100)
    if gfsk_channels:
        print(f"  ✅ GFSK sync (target UID) FOUND on {len(gfsk_channels)} channel(s):")
        for ch, f, g in gfsk_channels:
            print(f"     ch{ch} @ {f:.1f} MHz  (corr {g:.4f})  -> run gfsk.demodulate_frame for the frame")
    if lora_channels:
        print(f"  ✅ LoRa preamble FOUND on {len(lora_channels)} channel(s):")
        for ch, f, n in lora_channels:
            print(f"     ch{ch} @ {f:.1f} MHz  ({n} hit(s))")
    if not gfsk_channels and not lora_channels:
        print("  ❌ No GFSK (target UID) sync and no LoRa preamble on any clean channel.")
        print("     Likely: (1) radio not transmitting, (2) different rate/UID, or (3) interference.")
        print("     Check the duty cycle + spectrum above: continuous (duty>0.6) = not an ELRS burst link.")


if __name__ == '__main__':
    main()
