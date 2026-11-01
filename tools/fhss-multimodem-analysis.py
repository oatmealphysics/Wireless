#!/usr/bin/env python3
"""
Multi-Modem FHSS Drone Takeover — Feasibility Analysis

Analyzes the multi-modem approach to FHSS drone takeover described by
Kang et al. (ETRI Journal 2025). Instead of predicting or brute-forcing
the FHSS hopping sequence, multiple synchronized SDR receivers each
monitor a subset of channels simultaneously, reconstructing the hop
pattern in real time through signal correlation.

Reference:
  Kang et al., "Multi-modem-based FHSS-drone takeover with precision
  spoofing," ETRI Journal, 2025.
  DOI: 10.4218/etrij.2024-0369

ELRS FHSS parameters (from existing project research):
  - Band: 2400-2480 MHz (ISM 2.4 GHz)
  - Channels: 80 (1 MHz spacing)
  - Packet rate: 150 Hz (6.67ms per packet)
  - FHSS period: 240 hops / 1.6s
  - Sync channel: every 0.533s (every 80th hop)
  - Modulation: SX1280 LoRa SF6/SF7 or FLRC
  - Bandwidth: 800 kHz or 1.6 MHz (mode-dependent)

Multi-modem approach applied to ELRS:
  Each modem covers N channels (bandwidth-limited snapshot). With M modems
  covering all 80 channels, the system observes every hop and reconstructs
  the sequence without any prior key knowledge.

Hardware requirements for full 80-channel coverage:
  - Each SDR: ~20 MHz bandwidth → covers ~20 channels
  - Minimum modems: 80/20 = 4 SDRs for full coverage
  - With overlap for reliability: 5-6 SDRs recommended
  - Synchronization: GPS-disciplined clock or shared reference
  - Processing: Real-time correlation across all modems

This tool analyzes feasibility and provides timing/bandwidth calculations.
It does NOT perform any RF operations.

Usage:
  python3 fhss-multimodem-analysis.py --analyze
  python3 fhss-multimodem-analysis.py --calculate-modems
  python3 fhss-multimodem-analysis.py --timing-analysis
  python3 fhss-multimodem-analysis.py --chain

For authorized security research and CTF use only.
"""

import argparse
import json
import math
import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# ELRS FHSS parameters
# ---------------------------------------------------------------------------

ELRS_PARAMS = {
    "band_start_mhz": 2400,
    "band_end_mhz": 2480,
    "num_channels": 80,
    "channel_spacing_mhz": 1.0,
    "packet_rate_hz": 150,
    "packet_interval_ms": 6.667,
    "fhss_hops_per_cycle": 240,
    "fhss_cycle_ms": 1600,
    "sync_interval_hops": 80,
    "sync_interval_ms": 533,
    "modulation": "LoRa SF6 BW800 (150Hz mode)",
    "bandwidth_khz": 812,
    "preamble_symbols": 12,
    "payload_bytes": 8,
    "crc_bits": 16,
}

# SDR hardware options
SDR_OPTIONS = [
    {
        "name": "RTL-SDR v4",
        "bandwidth_mhz": 2.4,
        "sample_rate_msps": 2.4,
        "frequency_range": "24-1766 MHz",
        "price_usd": 30,
        "channels_covered": 2,
        "notes": "Too narrow for practical multi-modem ELRS coverage",
    },
    {
        "name": "HackRF One",
        "bandwidth_mhz": 20,
        "sample_rate_msps": 20,
        "frequency_range": "1-6000 MHz",
        "price_usd": 300,
        "channels_covered": 20,
        "notes": "Most practical per-modem bandwidth for ELRS",
    },
    {
        "name": "USRP B210",
        "bandwidth_mhz": 56,
        "sample_rate_msps": 61.44,
        "frequency_range": "70-6000 MHz",
        "price_usd": 2200,
        "channels_covered": 56,
        "notes": "2 channels; one B210 covers 70% of ELRS band",
    },
    {
        "name": "LimeSDR Mini 2.0",
        "bandwidth_mhz": 30,
        "sample_rate_msps": 30.72,
        "frequency_range": "10-3500 MHz",
        "price_usd": 300,
        "channels_covered": 30,
        "notes": "Good balance of bandwidth and cost",
    },
    {
        "name": "BladeRF 2.0 micro xA9",
        "bandwidth_mhz": 56,
        "sample_rate_msps": 61.44,
        "frequency_range": "47-6000 MHz",
        "price_usd": 750,
        "channels_covered": 56,
        "notes": "2 RX channels; covers most of ELRS band",
    },
]


# ---------------------------------------------------------------------------
# Modem coverage calculations
# ---------------------------------------------------------------------------

def calculate_modem_requirements(sdr_bw_mhz, overlap_pct=10):
    """Calculate how many modems are needed for full ELRS band coverage."""
    band_width = ELRS_PARAMS["band_end_mhz"] - ELRS_PARAMS["band_start_mhz"]
    effective_bw = sdr_bw_mhz * (1 - overlap_pct / 100)
    num_modems = math.ceil(band_width / effective_bw)
    total_coverage = num_modems * sdr_bw_mhz
    overlap_total = total_coverage - band_width
    return {
        "band_width_mhz": band_width,
        "sdr_bandwidth_mhz": sdr_bw_mhz,
        "overlap_pct": overlap_pct,
        "effective_bw_per_modem": effective_bw,
        "num_modems": num_modems,
        "total_coverage_mhz": total_coverage,
        "overlap_total_mhz": overlap_total,
        "channels_per_modem": int(sdr_bw_mhz / ELRS_PARAMS["channel_spacing_mhz"]),
    }


def calculate_timing_requirements():
    """Calculate timing precision needed for FHSS tracking."""
    packet_interval_us = ELRS_PARAMS["packet_interval_ms"] * 1000
    # LoRa symbol duration at SF6 BW800kHz
    # T_symbol = 2^SF / BW = 2^6 / 800000 = 80us
    sf = 6
    bw = 800000
    t_symbol_us = (2 ** sf / bw) * 1e6
    # Preamble duration
    preamble_us = ELRS_PARAMS["preamble_symbols"] * t_symbol_us
    # Total packet on-air time (approximate)
    # Payload + header + CRC overhead depends on CR
    cr = 4  # coding rate 4/8
    payload_symbols = math.ceil(
        (8 * ELRS_PARAMS["payload_bytes"] + ELRS_PARAMS["crc_bits"] + 28) /
        (4 * (sf - 2))
    ) * (cr + 4)
    packet_on_air_us = (ELRS_PARAMS["preamble_symbols"] + 4.25 + payload_symbols) * t_symbol_us
    # Guard time (interval - on-air)
    guard_us = packet_interval_us - packet_on_air_us

    return {
        "packet_interval_us": packet_interval_us,
        "symbol_duration_us": t_symbol_us,
        "preamble_duration_us": preamble_us,
        "packet_on_air_us": packet_on_air_us,
        "guard_time_us": guard_us,
        "timing_precision_required_us": guard_us / 4,
        "sync_clock_accuracy_ppm": 1e6 * (guard_us / 4) / packet_interval_us,
    }


def calculate_data_rate():
    """Calculate per-modem data rate requirements."""
    results = {}
    for sdr in SDR_OPTIONS:
        sample_rate = sdr["sample_rate_msps"] * 1e6
        # Complex samples: I + Q, each typically 8 or 16 bits
        bytes_per_second_8bit = sample_rate * 2  # I + Q, 8-bit each
        bytes_per_second_16bit = sample_rate * 4  # I + Q, 16-bit each
        mb_per_sec_16bit = bytes_per_second_16bit / (1024 * 1024)

        results[sdr["name"]] = {
            "sample_rate_msps": sdr["sample_rate_msps"],
            "data_rate_8bit_mbps": bytes_per_second_8bit / (1024 * 1024),
            "data_rate_16bit_mbps": mb_per_sec_16bit,
        }
    return results


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_approach():
    """Analyze the multi-modem FHSS takeover approach."""
    print("\n" + "=" * 70)
    print("Multi-Modem FHSS Takeover — Approach Analysis")
    print("=" * 70)

    print("\n  REFERENCE:")
    print('    Kang et al., "Multi-modem-based FHSS-drone takeover with')
    print('    precision spoofing," ETRI Journal, 2025')
    print("    DOI: 10.4218/etrij.2024-0369")

    print("\n  CONCEPT:")
    print("    Traditional FHSS attacks require knowing the hop sequence")
    print("    (derived from a shared secret like the ELRS binding phrase).")
    print("    The multi-modem approach observes ALL channels simultaneously")
    print("    using multiple wideband receivers, then correlates received")
    print("    signals across modems to reconstruct the hop pattern in real")
    print("    time — no prior key knowledge needed.")

    print("\n  ELRS FHSS PARAMETERS:")
    for k, v in ELRS_PARAMS.items():
        print(f"    {k}: {v}")

    print("\n  MULTI-MODEM ADVANTAGES OVER KEY RECOVERY:")
    print("    1. No binding phrase / UID knowledge required")
    print("    2. Works against any FHSS scheme (algorithm-agnostic)")
    print("    3. Real-time: no precomputation or brute-force needed")
    print("    4. Adapts to frequency agility changes during flight")
    print("    5. Can track multiple drones simultaneously")

    print("\n  CHALLENGES FOR ELRS SPECIFICALLY:")
    print(f"    1. High packet rate ({ELRS_PARAMS['packet_rate_hz']} Hz)")
    print(f"       — {ELRS_PARAMS['packet_interval_ms']:.3f} ms between hops")
    print(f"    2. 80 channels across {ELRS_PARAMS['band_end_mhz'] - ELRS_PARAMS['band_start_mhz']} MHz")
    print(f"    3. LoRa modulation requires specialized demodulation")
    print(f"    4. Low signal power (typical ELRS TX: 25-1000 mW)")
    print(f"    5. Sync channel appears every {ELRS_PARAMS['sync_interval_ms']} ms")
    print(f"       — existing project research already tracks this")

    print("\n  COMPARISON WITH EXISTING PROJECT APPROACH:")
    print("    Current: Single-channel sync capture → UID recovery → FHSS prediction")
    print("      + Lower hardware cost (one SDR)")
    print("      + Proven (capture-validated in this project)")
    print("      - Requires binding phrase or partial UID")
    print("      - Prediction-based: any error propagates")
    print("    Multi-modem: All channels simultaneously → direct observation")
    print("      + No key material needed")
    print("      + Observation-based: adapts to any sequence")
    print("      - Higher hardware cost (4-6 SDRs)")
    print("      - Complex real-time processing")
    print("      - Synchronization requirements between modems")


def calculate_modems():
    """Calculate modem requirements for each SDR option."""
    print("\n" + "=" * 70)
    print("Modem Coverage Calculations")
    print("=" * 70)

    band_width = ELRS_PARAMS["band_end_mhz"] - ELRS_PARAMS["band_start_mhz"]
    print(f"\n  ELRS band: {ELRS_PARAMS['band_start_mhz']}-{ELRS_PARAMS['band_end_mhz']} MHz "
          f"({band_width} MHz, {ELRS_PARAMS['num_channels']} channels)")

    for sdr in SDR_OPTIONS:
        req = calculate_modem_requirements(sdr["bandwidth_mhz"])
        total_cost = req["num_modems"] * sdr["price_usd"]
        print(f"\n  {sdr['name']} (~${sdr['price_usd']}):")
        print(f"    Bandwidth: {sdr['bandwidth_mhz']} MHz")
        print(f"    Channels per modem: ~{req['channels_per_modem']}")
        print(f"    Modems needed: {req['num_modems']}")
        print(f"    Total cost: ~${total_cost}")
        print(f"    Total coverage: {req['total_coverage_mhz']:.1f} MHz "
              f"(overlap: {req['overlap_total_mhz']:.1f} MHz)")
        print(f"    {sdr['notes']}")

    print("\n  RECOMMENDED CONFIGURATIONS:")
    print("    Budget: 4x HackRF One = $1,200 (full 80-channel coverage)")
    print("    Optimal: 3x LimeSDR Mini = $900 (90 MHz coverage, margin)")
    print("    Premium: 2x USRP B210 = $4,400 (112 MHz, best performance)")
    print("    Minimum: 2x BladeRF 2.0 = $1,500 (4 RX channels total)")


def timing_analysis():
    """Analyze timing precision requirements."""
    print("\n" + "=" * 70)
    print("Timing & Synchronization Analysis")
    print("=" * 70)

    timing = calculate_timing_requirements()

    print(f"\n  ELRS PACKET TIMING:")
    print(f"    Packet interval: {timing['packet_interval_us']:.0f} µs "
          f"({ELRS_PARAMS['packet_interval_ms']:.3f} ms)")
    print(f"    LoRa symbol duration: {timing['symbol_duration_us']:.1f} µs "
          f"(SF{6} BW{800}kHz)")
    print(f"    Preamble: {timing['preamble_duration_us']:.0f} µs "
          f"({ELRS_PARAMS['preamble_symbols']} symbols)")
    print(f"    Packet on-air time: ~{timing['packet_on_air_us']:.0f} µs")
    print(f"    Guard time: ~{timing['guard_time_us']:.0f} µs")

    print(f"\n  SYNCHRONIZATION REQUIREMENTS:")
    print(f"    Timing precision needed: <{timing['timing_precision_required_us']:.0f} µs")
    print(f"    Clock accuracy: <{timing['sync_clock_accuracy_ppm']:.0f} ppm")
    print(f"    Methods:")
    print(f"      1. GPS-disciplined oscillator (GPSDO): <1 ppb — excellent")
    print(f"      2. Shared 10 MHz reference: <1 ppm — adequate")
    print(f"      3. Software PPS sync: ~1-10 µs — marginal")
    print(f"      4. NTP only: ~1-10 ms — INSUFFICIENT")

    print(f"\n  PROCESSING PIPELINE:")
    print(f"    Per modem:")
    data_rates = calculate_data_rate()
    for name, dr in data_rates.items():
        print(f"      {name}: {dr['data_rate_16bit_mbps']:.1f} MB/s (16-bit I/Q)")

    print(f"\n    Total pipeline (4x HackRF):")
    total_mbps = 4 * data_rates["HackRF One"]["data_rate_16bit_mbps"]
    print(f"      Data rate: {total_mbps:.0f} MB/s ({total_mbps * 8 / 1000:.1f} Gbps)")
    print(f"      Processing: LoRa demodulation on each channel")
    print(f"      Correlation: Cross-modem signal matching")
    print(f"      Latency budget: <{timing['packet_interval_us']:.0f} µs per hop")

    print(f"\n  INJECTION TIMING (after sequence reconstruction):")
    print(f"    Once the hop sequence is observed for ~{ELRS_PARAMS['sync_interval_ms']} ms,")
    print(f"    the next hop can be predicted from the observed pattern.")
    print(f"    Injection must arrive within the guard window:")
    print(f"      Window: {timing['guard_time_us']:.0f} µs")
    print(f"      Requires: TX modem pre-tuned to next frequency")
    print(f"      Processing: <{timing['packet_interval_us'] / 2:.0f} µs to decide and tune")


def analyze_chains():
    """Analyze attack chains using multi-modem approach."""
    print("\n" + "=" * 70)
    print("Multi-Modem Attack Chain Analysis")
    print("=" * 70)

    chains = [
        {
            "id": "MM-KC-01",
            "name": "Blind FHSS Takeover (No Key Knowledge)",
            "severity": "CRITICAL",
            "stages": [
                "RECON: Deploy multi-modem array, begin recording all 80 channels",
                "WEAPONIZE: Real-time FHSS pattern extraction via cross-correlation",
                "DELIVER: Predict next hop, pre-tune TX modem to target frequency",
                "EXPLOIT: Inject forged packet at correct time/frequency/modulation",
                "C2: Maintain hop tracking, inject commands on every other hop",
                "ACTIONS: RC override, MSP injection, failsafe manipulation",
            ],
            "hardware": "4-6 SDRs + 1 TX SDR + GPS reference + processing PC",
            "cost": "$1,500-5,000",
            "detection": "Very difficult — injection looks like legitimate ELRS",
            "feasibility": "THEORETICAL — not demonstrated against ELRS specifically",
        },
        {
            "id": "MM-KC-02",
            "name": "Passive FHSS Sequence Fingerprinting",
            "severity": "HIGH",
            "stages": [
                "RECON: Record all 80 channels during target operation",
                "WEAPONIZE: Extract complete hop sequence from multi-channel data",
                "EXPLOIT: Match sequence against precomputed FHSS tables",
                "ACTIONS: Recover binding phrase / UID from sequence match",
            ],
            "hardware": "4-6 SDRs + processing PC (receive only)",
            "cost": "$1,200-4,400",
            "detection": "Undetectable — purely passive observation",
            "feasibility": "PLAUSIBLE — ELRS FHSS uses MINSTD LCG, sequence is deterministic",
        },
    ]

    for c in chains:
        print(f"\n  CHAIN {c['id']}: {c['name']}")
        print(f"    Severity: {c['severity']}")
        print(f"    Feasibility: {c['feasibility']}")
        for stage in c["stages"]:
            print(f"      {stage}")
        print(f"    Hardware: {c['hardware']}")
        print(f"    Cost: {c['cost']}")
        print(f"    Detection: {c['detection']}")

    print("\n  HARDWARE GATE:")
    print("    This attack surface is HARDWARE-GATED. Building the PoC requires:")
    print("    1. Multiple synchronized SDRs (minimum 4 for ELRS)")
    print("    2. GPS-disciplined clock or shared reference oscillator")
    print("    3. Real-time LoRa demodulation capability on each modem")
    print("    4. Cross-modem correlation processing (GPU recommended)")
    print("    5. TX-capable SDR for injection phase")
    print("    None of these are available in the current lab setup.")
    print("    The Lenovo desktop has no SDR hardware connected.")

    print("\n  SOFTWARE-FEASIBLE PREPARATORY WORK (done in this tool):")
    print("    - Coverage calculations for different SDR configurations")
    print("    - Timing analysis for injection window requirements")
    print("    - Processing pipeline data rate estimates")
    print("    - Feasibility assessment against ELRS-specific parameters")
    print("    - Attack chain documentation")

    print("\n  BENCH STEPS TO UNBLOCK:")
    print("    1. Acquire 4x HackRF One or 2x LimeSDR Mini (~$600-1,200)")
    print("    2. Obtain GPS-disciplined reference clock (~$100-300)")
    print("    3. Build multi-modem recording harness (GNU Radio + Python)")
    print("    4. Validate LoRa demodulation on each modem independently")
    print("    5. Implement cross-modem correlation algorithm")
    print("    6. Test against known ELRS FHSS sequence (from existing captures)")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Modem FHSS Drone Takeover Feasibility Analysis"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze the multi-modem approach",
    )
    parser.add_argument(
        "--calculate-modems", action="store_true",
        help="Calculate modem requirements for ELRS band coverage",
    )
    parser.add_argument(
        "--timing-analysis", action="store_true",
        help="Analyze timing and synchronization requirements",
    )
    parser.add_argument(
        "--chain", action="store_true",
        help="Analyze attack chains",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file",
    )
    args = parser.parse_args()

    print("Multi-Modem FHSS Drone Takeover — Feasibility Analysis")
    print(f"Reference: Kang et al., ETRI Journal, 2025")
    print(f"Target: ELRS 2.4 GHz FHSS ({ELRS_PARAMS['num_channels']} channels, "
          f"{ELRS_PARAMS['packet_rate_hz']} Hz)")
    print(f"Approach: Wideband multi-receiver observation")
    print(f"Status: HARDWARE-GATED (no SDR array available)")

    results = {
        "target_protocol": "ELRS 2.4 GHz FHSS",
        "channels": ELRS_PARAMS["num_channels"],
        "packet_rate_hz": ELRS_PARAMS["packet_rate_hz"],
        "min_modems_hackrf": 4,
        "min_cost_usd": 1200,
        "feasibility": "THEORETICAL",
        "hardware_gated": True,
    }

    if args.analyze:
        analyze_approach()

    if args.calculate_modems:
        calculate_modems()

    if args.timing_analysis:
        timing_analysis()

    if args.chain:
        analyze_chains()

    if not any([args.analyze, args.calculate_modems, args.timing_analysis, args.chain]):
        analyze_approach()
        calculate_modems()
        timing_analysis()
        analyze_chains()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
