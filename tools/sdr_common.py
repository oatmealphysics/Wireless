#!/usr/bin/env python3
"""
SDR Common Abstraction Layer

Provides a generic SDR interface via SoapySDR so all RF tools in this
repo work with any supported hardware: Ettus USRP (B205mini, B210, etc.),
HackRF, RTL-SDR (RX only), LimeSDR, BladeRF, and others.

Usage from other tools:

    from sdr_common import SDRDevice, add_sdr_args

    # In argparse setup:
    add_sdr_args(parser)

    # After parsing:
    sdr = SDRDevice.from_args(args)
    sdr.setup_rx(freq=2.4e9, rate=2e6, gain=40)
    samples = sdr.receive(num_samples=1024)
    sdr.close()

Requires: SoapySDR python bindings
    pip install SoapySDR   (or system package: python3-soapysdr)

SoapySDR driver modules must be installed for your hardware:
    USRP:    soapysdr-module-uhd    (or SoapyUHD)
    HackRF:  soapysdr-module-hackrf (or SoapyHackRF)
    RTL-SDR: soapysdr-module-rtlsdr (or SoapyRTLSDR)
    LimeSDR: soapysdr-module-lms7   (or SoapyLMS7)
    BladeRF: soapysdr-module-bladerf (or SoapyBladeRF)
"""

import argparse
import sys
import time

try:
    import SoapySDR
    from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX, SOAPY_SDR_TX
    HAS_SOAPY = True
except ImportError:
    HAS_SOAPY = False

import numpy as np


def check_soapy_available():
    """Exit with a helpful message if SoapySDR is not installed."""
    if not HAS_SOAPY:
        print("[!] SoapySDR Python bindings not found.", file=sys.stderr)
        print("    Install with: pip install SoapySDR", file=sys.stderr)
        print("    Or system package: apt install python3-soapysdr", file=sys.stderr)
        print("", file=sys.stderr)
        print("    Also install the driver module for your hardware:", file=sys.stderr)
        print("      USRP:    apt install soapysdr-module-uhd", file=sys.stderr)
        print("      HackRF:  apt install soapysdr-module-hackrf", file=sys.stderr)
        print("      RTL-SDR: apt install soapysdr-module-rtlsdr", file=sys.stderr)
        print("      LimeSDR: apt install soapysdr-module-lms7", file=sys.stderr)
        print("      BladeRF: apt install soapysdr-module-bladerf", file=sys.stderr)
        sys.exit(1)


def list_devices() -> list[dict]:
    """Enumerate all SDR devices visible to SoapySDR."""
    check_soapy_available()
    results = SoapySDR.Device.enumerate()
    devices = []
    for r in results:
        devices.append(dict(r))
    return devices


def print_devices():
    """Print all detected SDR devices to stderr."""
    devices = list_devices()
    if not devices:
        print("[!] No SDR devices found. Check connections and drivers.", file=sys.stderr)
        return
    print(f"[*] Found {len(devices)} SDR device(s):", file=sys.stderr)
    for i, dev in enumerate(devices):
        label = dev.get("label", dev.get("driver", "unknown"))
        print(f"    [{i}] {label}", file=sys.stderr)
        for k, v in dev.items():
            if k != "label":
                print(f"        {k} = {v}", file=sys.stderr)


def add_sdr_args(parser: argparse.ArgumentParser):
    """Add standard SDR CLI arguments to an argparse parser."""
    sdr_group = parser.add_argument_group("SDR options")
    sdr_group.add_argument(
        "--sdr-device", default="",
        help='SoapySDR device string (e.g. "driver=uhd", "driver=hackrf", '
             '"driver=rtlsdr"). Empty string auto-detects the first available device.',
    )
    sdr_group.add_argument(
        "--sdr-rate", type=float, default=None,
        help="Sample rate in Hz (e.g. 2e6 for 2 Msps). "
             "Default depends on the tool.",
    )
    sdr_group.add_argument(
        "--sdr-gain", type=float, default=None,
        help="RF gain in dB. Default depends on the tool.",
    )
    sdr_group.add_argument(
        "--sdr-antenna", default=None,
        help="Antenna port name (e.g. 'TX/RX', 'RX2'). Default uses device default.",
    )
    sdr_group.add_argument(
        "--sdr-bandwidth", type=float, default=None,
        help="Analog filter bandwidth in Hz. Default matches sample rate.",
    )
    sdr_group.add_argument(
        "--sdr-list", action="store_true",
        help="List detected SDR devices and exit.",
    )
    sdr_group.add_argument(
        "--sdr-channel", type=int, default=0,
        help="SDR channel index (default: 0).",
    )


class SDRDevice:
    """Generic SDR device wrapper around SoapySDR."""

    def __init__(self, device_args: str = "", channel: int = 0):
        check_soapy_available()
        self.channel = channel
        self._rx_stream = None
        self._tx_stream = None

        if not device_args:
            devices = SoapySDR.Device.enumerate()
            if not devices:
                print("[!] No SDR devices found. Check connections and drivers.", file=sys.stderr)
                sys.exit(1)
            device_args = devices[0]
            label = device_args.get("label", device_args.get("driver", "unknown"))
            print(f"[*] Auto-detected SDR: {label}", file=sys.stderr)

        self.dev = SoapySDR.Device(device_args)
        hw_info = self.dev.getHardwareInfo()
        driver = hw_info.get("driver", "unknown")
        print(f"[*] Opened SDR: driver={driver}", file=sys.stderr)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "SDRDevice":
        """Create an SDRDevice from parsed CLI arguments."""
        if getattr(args, "sdr_list", False):
            print_devices()
            sys.exit(0)
        return cls(
            device_args=args.sdr_device,
            channel=getattr(args, "sdr_channel", 0),
        )

    def setup_rx(self, freq: float, rate: float, gain: float = 40.0,
                 antenna: str | None = None, bandwidth: float | None = None):
        """Configure the device for receiving."""
        ch = self.channel
        self.dev.setSampleRate(SOAPY_SDR_RX, ch, rate)
        self.dev.setFrequency(SOAPY_SDR_RX, ch, freq)
        self.dev.setGain(SOAPY_SDR_RX, ch, gain)
        if antenna:
            self.dev.setAntenna(SOAPY_SDR_RX, ch, antenna)
        if bandwidth:
            self.dev.setBandwidth(SOAPY_SDR_RX, ch, bandwidth)

        actual_rate = self.dev.getSampleRate(SOAPY_SDR_RX, ch)
        actual_freq = self.dev.getFrequency(SOAPY_SDR_RX, ch)
        actual_gain = self.dev.getGain(SOAPY_SDR_RX, ch)
        actual_ant = self.dev.getAntenna(SOAPY_SDR_RX, ch)
        print(f"[*] RX configured: freq={actual_freq/1e6:.3f} MHz, "
              f"rate={actual_rate/1e6:.3f} Msps, gain={actual_gain:.1f} dB, "
              f"antenna={actual_ant}", file=sys.stderr)

    def setup_tx(self, freq: float, rate: float, gain: float = 40.0,
                 antenna: str | None = None, bandwidth: float | None = None):
        """Configure the device for transmitting."""
        ch = self.channel
        self.dev.setSampleRate(SOAPY_SDR_TX, ch, rate)
        self.dev.setFrequency(SOAPY_SDR_TX, ch, freq)
        self.dev.setGain(SOAPY_SDR_TX, ch, gain)
        if antenna:
            self.dev.setAntenna(SOAPY_SDR_TX, ch, antenna)
        if bandwidth:
            self.dev.setBandwidth(SOAPY_SDR_TX, ch, bandwidth)

        actual_rate = self.dev.getSampleRate(SOAPY_SDR_TX, ch)
        actual_freq = self.dev.getFrequency(SOAPY_SDR_TX, ch)
        actual_gain = self.dev.getGain(SOAPY_SDR_TX, ch)
        actual_ant = self.dev.getAntenna(SOAPY_SDR_TX, ch)
        print(f"[*] TX configured: freq={actual_freq/1e6:.3f} MHz, "
              f"rate={actual_rate/1e6:.3f} Msps, gain={actual_gain:.1f} dB, "
              f"antenna={actual_ant}", file=sys.stderr)

    def start_rx(self):
        """Activate the RX stream."""
        self._rx_stream = self.dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [self.channel])
        self.dev.activateStream(self._rx_stream)

    def stop_rx(self):
        """Deactivate the RX stream."""
        if self._rx_stream is not None:
            self.dev.deactivateStream(self._rx_stream)
            self.dev.closeStream(self._rx_stream)
            self._rx_stream = None

    def start_tx(self):
        """Activate the TX stream."""
        self._tx_stream = self.dev.setupStream(SOAPY_SDR_TX, SOAPY_SDR_CF32, [self.channel])
        self.dev.activateStream(self._tx_stream)

    def stop_tx(self):
        """Deactivate the TX stream."""
        if self._tx_stream is not None:
            self.dev.deactivateStream(self._tx_stream)
            self.dev.closeStream(self._tx_stream)
            self._tx_stream = None

    def receive(self, num_samples: int = 65536) -> np.ndarray:
        """Receive a block of complex samples. Returns np.complex64 array."""
        if self._rx_stream is None:
            self.start_rx()
        buf = np.zeros(num_samples, dtype=np.complex64)
        total = 0
        while total < num_samples:
            chunk_size = min(num_samples - total, 16384)
            sr = self.dev.readStream(self._rx_stream, [buf[total:total+chunk_size]], chunk_size)
            if sr.ret > 0:
                total += sr.ret
            elif sr.ret == -1:
                time.sleep(0.001)
            else:
                print(f"[!] RX stream error: {sr.ret}", file=sys.stderr)
                break
        return buf[:total]

    def transmit(self, samples: np.ndarray):
        """Transmit a block of complex samples (np.complex64)."""
        if self._tx_stream is None:
            self.start_tx()
        samples = samples.astype(np.complex64)
        offset = 0
        while offset < len(samples):
            chunk_size = min(len(samples) - offset, 16384)
            sr = self.dev.writeStream(
                self._tx_stream,
                [samples[offset:offset+chunk_size]],
                chunk_size,
            )
            if sr.ret > 0:
                offset += sr.ret
            elif sr.ret == -1:
                time.sleep(0.001)
            else:
                print(f"[!] TX stream error: {sr.ret}", file=sys.stderr)
                break

    def get_rx_freq_range(self) -> tuple[float, float]:
        """Return (min_freq, max_freq) for RX in Hz."""
        ranges = self.dev.getFrequencyRange(SOAPY_SDR_RX, self.channel)
        if ranges:
            return ranges[0].minimum(), ranges[-1].maximum()
        return (0, 0)

    def get_tx_freq_range(self) -> tuple[float, float]:
        """Return (min_freq, max_freq) for TX in Hz."""
        ranges = self.dev.getFrequencyRange(SOAPY_SDR_TX, self.channel)
        if ranges:
            return ranges[0].minimum(), ranges[-1].maximum()
        return (0, 0)

    def get_sample_rate_range(self, direction=None) -> tuple[float, float]:
        """Return (min_rate, max_rate) for the given direction."""
        if direction is None:
            direction = SOAPY_SDR_RX
        ranges = self.dev.getSampleRateRange(direction, self.channel)
        if ranges:
            return ranges[0].minimum(), ranges[-1].maximum()
        return (0, 0)

    def has_tx(self) -> bool:
        """Check if this device supports transmit."""
        try:
            info = self.dev.getHardwareInfo()
            driver = info.get("driver", "").lower()
            if "rtlsdr" in driver:
                return False
            num_tx = self.dev.getNumChannels(SOAPY_SDR_TX)
            return num_tx > 0
        except Exception:
            return False

    def close(self):
        """Clean up all streams and close the device."""
        self.stop_rx()
        self.stop_tx()
        self.dev = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
