#!/usr/bin/env python3
#
# droneid_json_rx.py — SDR → DJI Drone-ID receiver (NDSS'23, RUB-SysSec/
# DroneSecurity) that prints one JSON line per decoded frame to stdout.
#
# This is the elrs-sdr webui integration seam for the DJI/OcuSync drone-ID
# layer, mirroring the Wi-Fi json_sink pattern. EVERYTHING is captured by the
# SDR (no onboard NIC / BLE / sensors) — the B205mini is the only RF source.
#
# It mirrors DroneSecurity's own live receiver: it hops the known Drone-ID
# carrier frequencies (2.4 GHz + 5.8 GHz), captures 50 Msps of IQ on RX2, and
# runs the proven SpectrumCapture -> Packet -> Decoder -> DroneIDPacket chain.
#
# Usage:
#   python3 droneid_json_rx.py --device type=b200 --gain 40 [--legacy]
#
# Each decoded frame prints one line like:
#   {"ts":..., "carrier_mhz":2444.5, "type":0, "lat":..., "lon":...,
#    "alt":..., "speed":..., "drone_id":"...", "operator":"..."}
#
# The webui reads stdout, parses each line, and populates the airborne drone
# row (position / operator / ID) in the unified table.
#
import argparse
import json
import os
import sys
import time

import numpy as np
# uhd is only needed for the live capture path (the laptop has it; the
# enclave / offline decode tests don't). Imported lazily in capture().
try:
    import uhd  # noqa: F401
except ImportError:
    uhd = None

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "DroneSecurity", "src")
SRC = os.path.abspath(SRC)
sys.path.insert(0, SRC)


def _have_real_scipy():
    try:
        import scipy.signal  # noqa: F401
        return True
    except Exception:
        return False


# allow the numpy-only scipy shim to satisfy `import scipy.signal` when the
# laptop doesn't have the exact scipy the project pins (harmless if it does).
SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "DroneSecurity", "_shim")
SHIM = os.path.abspath(SHIM)
if os.path.isdir(SHIM) and not _have_real_scipy():
    sys.path.insert(0, SHIM)

# --- DroneSecurity decode chain (the proven NDSS'23 receiver) ---
from SpectrumCapture import SpectrumCapture          # noqa: E402
from Packet import Packet                            # noqa: E402
from qpsk import Decoder                             # noqa: E402
from droneid_packet import DroneIDPacket             # noqa: E402

# Known Drone-ID carrier frequencies (MHz) — from DroneSecurity's live RX.
CARRIERS_MHZ = [2414.5, 2429.502441, 2434.5, 2444.5, 2459.5, 2474.5,
                5721.5, 5731.5, 5741.5, 5756.5, 5761.5, 5771.5,
                5786.5, 5801.5, 5816.5, 5831.5]


def capture(usrp, freq_hz, srate, gain, duration_s, antenna="RX2"):
    """Capture `duration_s` of IQ from the B205 at freq_hz / srate."""
    usrp.set_rx_antenna(antenna, 0)
    if gain is not None:
        usrp.set_rx_gain(gain, 0)
    else:
        try:
            usrp.set_rx_agc(True, 0)
        except Exception:
            pass
    usrp.set_rx_rate(srate, 0)
    usrp.set_rx_freq(uhd.libpyuhd.types.tune_request(freq_hz), 0)
    time.sleep(0.2)  # settle after retune
    st_args = uhd.usrp.StreamArgs("fc32", "sc16")
    streamer = usrp.get_rx_stream(st_args)
    meta = uhd.types.RXMetadata()
    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_continuous)
    streamer.issue_stream_cmd(cmd)
    n = int(srate * duration_s)
    buf = np.zeros((1, min(n, 1 << 20)), dtype=np.complex64)
    out = np.empty(n, dtype=np.complex64)
    got = 0
    while got < n:
        k = streamer.recv(buf, meta)
        if k:
            take = min(k, n - got)
            out[got:got + take] = buf[0][:take]
            got += take
    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_continuous)
    streamer.issue_stream_cmd(cmd)
    return out


def decode_chunk(samples, Fs, legacy=False):
    """Run the DroneSecurity decode chain on one capture. Yields a dict per
    decoded Drone-ID frame."""
    chunk = int(0.5 * Fs)  # 500 ms chunks (as in DroneSecurity)
    results = []
    for i in range(0, len(samples), chunk):
        seg = samples[i:i + chunk]
        if len(seg) < chunk // 2:
            break
        try:
            cap = SpectrumCapture(raw_data=seg, Fs=Fs, legacy=legacy)
        except Exception as e:
            sys.stderr.write(f"[droneid] detection error: {e!r}\n")
            continue
        for pn in range(len(cap.packets)):
            try:
                pkt = cap.get_packet_samples(pktnum=pn)
                packet = Packet(pkt)
                symbols = packet.get_symbol_data(skip_zc=True)
                decoder = Decoder(symbols)
                payload = None
                for phase in range(4):
                    decoder.raw_data_to_symbol_bits(phase)
                    dum = decoder.magic()
                    try:
                        p = DroneIDPacket(dum)
                        payload = p
                        break
                    except Exception:
                        continue
                if payload is None:
                    continue
                results.append(_frame_to_dict(payload, cap))
            except Exception as e:
                sys.stderr.write(f"[droneid] decode frame {pn}: {e!r}\n")
    return results


def _frame_to_dict(payload, cap):
    """Best-effort: map a DroneIDPacket to a JSON-able dict. Field names are
    read defensively so a schema difference in the reference doesn't crash
    the feed — unknown fields are omitted rather than fabricated."""
    d = {"ts": time.time()}
    # DroneIDPacket exposes attributes like .lat, .lon, .alt, .speed,
    # .type, .timestamp, and the drone/app IDs (names vary by protocol type).
    for k in ("lat", "lon", "alt", "altitude", "speed", "velocity",
              "type", "subtype", "timestamp"):
        v = getattr(payload, k, None)
        if v is not None and not callable(v):
            try:
                d[k] = v.item() if hasattr(v, "item") else v
            except Exception:
                pass
    # IDs: try the common attribute names; include whichever exist.
    for k in ("drone_id", "app_id", "operator_id", "serial", "uid",
              "drone_sn", "operator_sn", "position_source"):
        v = getattr(payload, k, None)
        if v is not None and not callable(v):
            d[k] = str(v)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="type=b200")
    ap.add_argument("--gain", type=float, default=40.0)
    ap.add_argument("--srate", type=float, default=50e6)
    ap.add_argument("--dwell", type=float, default=1.3,
                    help="seconds to dwell on each carrier (DroneSecurity uses 1.3)")
    ap.add_argument("--legacy", action="store_true",
                    help="enable legacy (Mavic Pro/2) support")
    ap.add_argument("--carriers", default="",
                    help="comma list of MHz to override the default set")
    args = ap.parse_args()

    carriers = CARRIERS_MHZ
    if args.carriers:
        carriers = [float(x) for x in args.carriers.split(",") if x.strip()]

    sys.stderr.write(f"[droneid_json_rx] {len(carriers)} carriers, "
                     f"{args.srate/1e6:.0f} Msps, gain={args.gain}, "
                     f"dwell={args.dwell}s, SDR-only capture\n")

    usrp = uhd.usrp.MultiUSRP(args.device)

    while True:
        for mhz in carriers:
            try:
                samples = capture(usrp, mhz * 1e6, args.srate,
                                  args.gain, args.dwell)
            except Exception as e:
                sys.stderr.write(f"[droneid] capture {mhz} MHz: {e!r}\n")
                continue
            for frame in decode_chunk(samples, args.srate, args.legacy):
                frame["carrier_mhz"] = mhz
                # keep stdout as the clean JSON channel
                print(json.dumps(frame), flush=True)


if __name__ == "__main__":
    main()
