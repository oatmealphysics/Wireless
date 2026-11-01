#!/usr/bin/env python3
"""Record a fixed ELRS v3 sync-channel *candidate* as private, receive-only IQ.

Betaflight 4.4.2's 2.4 GHz v3 FHSS source selects nominal channel 41
(2441.4 MHz) at each 80-slot block boundary. Tuning near it improves the
chance of observing those visits; this recorder cannot classify ELRS SYNC
packet bytes. It never opens the E8 controller or sets up an SDR TX stream.

Example (matches the earlier B205mini receive setup)::

    python3 tools/elrs-sync-channel-record.py \\
      --sdr-device driver=uhd --antenna RX2 --duration-seconds 20 \\
      --output work/captures/new_channel41_cf32.iq

The output is raw little-endian complex float32 (cf32le). Pass
``--input-format cf32le`` to ``elrs-phy-scan.py`` when analyzing it. A
``.meta.txt`` sidecar records actual receive settings and completeness.
Both files must be new paths under the repository's ignored ``work/``.

Requires Python 3.10+, NumPy, SoapySDR Python bindings, and an RX driver
(e.g. soapysdr-module-uhd). This tool only uses SOAPY_SDR_RX.
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

WORK_ROOT = Path(__file__).resolve().parents[1] / "work"
BLOCK_SAMPLES = 16_384


def output_paths(path: Path) -> tuple[Path, Path]:
    target = path.resolve()
    if (not target.is_relative_to(WORK_ROOT.resolve())
            or target == WORK_ROOT.resolve() or target.suffix.lower() != ".iq"):
        raise ValueError("output must be a new .iq file under ignored work/")
    sidecar = target.with_name(target.name + ".meta.txt")
    if target.exists() or sidecar.exists():
        raise FileExistsError("IQ or metadata output exists; choose a new filename")
    return target, sidecar


def record(output: Path, device_args: str, channel: int, center_hz: float,
           sample_rate: float, bandwidth: float, gain: float,
           antenna: str | None, duration: float, soapy=None) -> dict:
    if not all(math.isfinite(x) for x in
               (center_hz, sample_rate, bandwidth, gain, duration)):
        raise ValueError("receive parameters must be finite")
    if (channel < 0 or center_hz <= 0 or sample_rate <= 0 or bandwidth <= 0
            or duration <= 0 or duration > 300):
        raise ValueError("invalid receive parameters (duration limit: 300 s)")
    target, sidecar = output_paths(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    required_bytes = math.ceil(sample_rate * duration * 8 * 1.1)
    if shutil.disk_usage(target.parent).free < required_bytes:
        raise OSError("insufficient free space for requested cf32le recording")
    if soapy is None:
        import SoapySDR as soapy  # optional hardware dependency, RX path only

    rx = soapy.SOAPY_SDR_RX
    radio = soapy.Device(device_args)
    radio.setSampleRate(rx, channel, sample_rate)
    radio.setFrequency(rx, channel, center_hz)
    radio.setGain(rx, channel, gain)
    radio.setBandwidth(rx, channel, bandwidth)
    if antenna:
        radio.setAntenna(rx, channel, antenna)
    actual_rate = float(radio.getSampleRate(rx, channel))
    actual_center = float(radio.getFrequency(rx, channel))
    actual_bandwidth = float(radio.getBandwidth(rx, channel))
    actual_gain = float(radio.getGain(rx, channel))
    actual_antenna = radio.getAntenna(rx, channel)
    if (not math.isfinite(actual_rate) or actual_rate <= 0
            or not math.isfinite(actual_center) or actual_center <= 0
            or not math.isfinite(actual_bandwidth)
            or not math.isfinite(actual_gain)
            or abs(actual_rate / sample_rate - 1) > 0.01
            or abs(actual_center - center_hz) > sample_rate * 0.01):
        raise ValueError("SDR did not accept the requested sample rate/frequency")

    total = round(duration * actual_rate)
    if total < 1:
        raise ValueError("duration must contain at least one sample")
    received = 0
    stream = None
    activated = False
    started_utc = datetime.now(timezone.utc).isoformat()
    complete = False
    print(f"RX only: {actual_center / 1e6:.6f} MHz, "
          f"{actual_rate / 1e6:.3f} Msps, {duration:.3f} s", file=sys.stderr)
    try:
        with target.open("xb") as sink:
            stream = radio.setupStream(rx, soapy.SOAPY_SDR_CF32, [channel])
            radio.activateStream(stream)
            activated = True
            deadline = time.monotonic() + duration + max(5.0, duration * 0.25)
            buffer = np.empty(BLOCK_SAMPLES, dtype=np.complex64)
            while received < total:
                if time.monotonic() > deadline:
                    raise TimeoutError("RX stalled before requested sample count")
                count = min(BLOCK_SAMPLES, total - received)
                result = radio.readStream(stream, [buffer[:count]], count,
                                          timeoutUs=100_000)
                if result.ret > 0:
                    if result.ret > count:
                        raise OSError("SDR returned more samples than requested")
                    sink.write(buffer[:result.ret].astype("<c8", copy=False).tobytes())
                    received += result.ret
                elif result.ret != -1:  # Soapy timeout is -1
                    raise OSError(f"SDR receive error {result.ret}")
            complete = True
    finally:
        if stream is not None:
            try:
                if activated:
                    radio.deactivateStream(stream)
            finally:
                radio.closeStream(stream)
        # A partial capture is retained for recovery, visibly marked incomplete.
        if target.exists():
            with sidecar.open("x", encoding="utf-8") as meta:
                meta.write(f"format: cf32le\n"
                           f"sample_rate_hz: {actual_rate:.9f}\n"
                           f"center_hz: {actual_center:.3f}\n"
                           f"bandwidth_hz: {actual_bandwidth:.3f}\n"
                           f"gain_db: {actual_gain:.3f}\n"
                           f"antenna: {actual_antenna}\n"
                           f"started_utc: {started_utc}\n"
                           f"samples: {received}\n"
                           f"complete: {str(complete).lower()}\n")
    return {"samples": received, "complete": complete,
            "sample_rate_hz": actual_rate, "center_hz": actual_center}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sdr-device", default="driver=uhd")
    parser.add_argument("--sdr-channel", type=int, default=0)
    parser.add_argument("--center-hz", type=float, default=2_441_000_000)
    parser.add_argument("--sample-rate", type=float, default=4_000_000)
    parser.add_argument("--bandwidth", type=float, default=2_000_000)
    parser.add_argument("--gain", type=float, default=35)
    parser.add_argument("--antenna", default="RX2")
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = record(args.output, args.sdr_device, args.sdr_channel,
                        args.center_hz, args.sample_rate, args.bandwidth,
                        args.gain, args.antenna, args.duration_seconds)
    except (ImportError, OSError, RuntimeError, ValueError, TimeoutError) as exc:
        print(f"Receive-only capture error: {exc}", file=sys.stderr)
        return 2
    print(f"Saved {result['samples']} cf32le samples with private metadata; "
          "no packet bytes decoded", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
