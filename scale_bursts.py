#!/usr/bin/env python3
"""
scale_bursts.py — scale the burst count to ~400 (all 3 live_ch5 captures) + run the
CRC-gated ELRS SYNC air-recovery (Claude's §21d item 1: "Scale to all ~400 bursts").

This tests hypothesis 1 (no SYNC packets in the 14-19 burst sample) with zero chain
changes. ~400 bursts should catch ~23 SYNC packets (enough for the corroboration gate).

Run: python3 scale_bursts.py
"""
import numpy as np
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from elrs_sdr.core import identify as _id
from elrs_sdr.core import lora as _lora
from elrs_sdr.core import lora_payload as _lp
from elrs_sdr import positivedet as _pd
from elrs_sdr.detect import detect_bursts, is_continuous_carrier, _power_track, _noise_floor

# The 3 live_ch5 captures (100 MB each, int16 I/Q interleaved, 4.9995 MS/s, 2405.4 MHz)
CAPTURES = [
    "captures/live_ch5_20261008_204039.complex8",
    "captures/live_ch5_20261008_204103.complex8",
    "captures/live_ch5_20261008_204114.complex8",
]
SRATE = 4.9995e6  # the measured srate (from the .meta.txt)
SF = 7            # the validated SF (Claude's §17d)
BW = 812_500.0    # the validated BW (Claude's §17d)

def read_capture(path: str) -> np.ndarray:
    """Read an int16 I/Q interleaved capture -> complex64."""
    raw = np.fromfile(path, dtype=np.int16)
    return raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)

def find_bursts(iq: np.ndarray, srate: float, min_burst_s: float = 0.002):
    """Find all bursts in the capture (hysteresis detect + carrier reject).

    Uses a larger max_gap (5 ms) to merge the fragments that the hysteresis detector
    splits each ELRS burst into (the link hops to a new channel between bursts, so the
    power drops briefly — a 1 ms max_gap was too small, splitting each burst into
    many 0.15 ms fragments).
    """
    win = max(64, int(0.0001 * srate))  # 100 us window
    power = _power_track(iq, win)
    noise = _noise_floor(iq, win)
    # The hysteresis thresholds (10 dB above noise for the high gate)
    high_thresh = noise * 10.0
    low_thresh = noise * 5.0
    min_high_count = max(3, int(0.0005 * srate / win))  # 500 us of high-power
    max_gap = max(5, int(0.005 * srate / win))  # 5 ms gap (was 1 ms — too small)
    bursts = detect_bursts(power, high_thresh, low_thresh, min_high_count, max_gap)
    # Filter: reject continuous carriers + require min duration
    good_bursts = []
    for b in bursts:
        core = iq[b.start:b.stop]
        if len(core) < min_burst_s * srate:
            continue
        if is_continuous_carrier(core, srate):
            continue
        good_bursts.append(b)
    return good_bursts

def demod_burst_to_symbols(burst_iq: np.ndarray, srate: float, sf: int, bw: float) -> list[int] | None:
    """Demod a burst to symbols (resample to BW, dechirp, FFT per 2^sf samples)."""
    # Resample to BW (1 sample/chip)
    if srate > bw * 1.1:
        burst_iq = _pd.fft_resample(burst_iq, int(len(burst_iq) * bw / srate))
    new_srate = bw
    # The base chirp (for the SF)
    base = _lora._base_chirp(sf)
    # The symbol length (2^sf samples)
    n_sym = 1 << sf
    # Dechirp: multiply by the conjugate down-chirp
    # The down-chirp is the conjugate of the up-chirp
    down_chirp = np.conj(base)
    # Tile the down_chirp to match the burst length
    n_tiles = int(np.ceil(len(burst_iq) / n_sym))
    tiled = np.tile(down_chirp, n_tiles)[:len(burst_iq)]
    dechirped = burst_iq * tiled
    # FFT per 2^sf samples
    symbols = []
    for i in range(0, len(dechirped) - n_sym + 1, n_sym):
        seg = dechirped[i:i+n_sym]
        if len(seg) < n_sym:
            break
        fft = np.fft.fft(seg)
        symbols.append(int(np.argmax(np.abs(fft))))
    return symbols if len(symbols) >= 20 else None  # require >= 20 symbols

def main():
    all_symbol_sets = []
    total_bursts = 0
    for cap_path in CAPTURES:
        print(f"Reading {cap_path} ...", flush=True)
        iq = read_capture(cap_path)
        print(f"  {len(iq)} samples", flush=True)
        
        print(f"  Finding bursts ...", flush=True)
        bursts = find_bursts(iq, SRATE)
        print(f"  {len(bursts)} bursts (after carrier reject)", flush=True)
        
        n_demod = 0
        for i, b in enumerate(bursts):
            # Use the full burst (not the capped core) for the demod — the burst_core
            # (burst_ms=1.0) was capping the core at 1.0 ms, which is too short for a
            # preamble + sync + payload (~1.5-2 ms at SF7/812 kHz).
            core = iq[b.start:b.stop]
            if len(core) < 2 * (1 << SF):  # need at least 2 symbols
                continue
            # Measure the freq offset + shift to baseband
            offset = _id.freq_offset_hz(core, SRATE)
            core_bb = _id.freq_shift_to_baseband(core, SRATE, offset)
            # Demod to symbols
            syms = demod_burst_to_symbols(core_bb, SRATE, SF, BW)
            if syms is not None:
                all_symbol_sets.append(syms)
                n_demod += 1
            if (i + 1) % 25 == 0:
                print(f"    {i+1}/{len(bursts)} bursts processed, {n_demod} demod'd", flush=True)
        
        total_bursts += len(bursts)
        print(f"  {n_demod}/{len(bursts)} bursts demod'd to symbols", flush=True)
    
    print(f"\nTotal: {len(all_symbol_sets)} symbol sets from {total_bursts} bursts", flush=True)
    
    if not all_symbol_sets:
        print("No bursts demod'd — exiting.")
        return
    
    # Run the CRC-gated ELRS SYNC air-recovery
    print(f"\nRunning recover_from_bursts({len(all_symbol_sets)} symbol sets, SF={SF}) ...", flush=True)
    t0 = time.time()
    corroborated, report = _lp.recover_from_bursts(all_symbol_sets, SF)
    dt = time.time() - t0
    
    print(f"\n=== RESULTS ({dt:.1f} s) ===")
    print(f"  bursts: {report['bursts']}")
    print(f"  trials: {report['trials']}")
    print(f"  raw_hits: {report['raw_hits']}")
    print(f"  distinct: {report['distinct']}")
    print(f"  expected_false_accepts: {report['expected_false_accepts']:.2f}")
    print(f"  agreement (top 5): {report['agreement']}")
    print(f"  CORROBORATED: {len(corroborated)}")
    if corroborated:
        for h in corroborated[:10]:
            print(f"    uid4={h.uid4:#04x} uid5={h.uid5:#04x} ota_v={h.ota_version} opts={h.opts}")
        print(f"\n  ** UID RECOVERED: {corroborated[0].net_id} **")
    else:
        print(f"\n  No corroborated hits (hypothesis 1 may be wrong, or the chain needs work).")

if __name__ == "__main__":
    main()
