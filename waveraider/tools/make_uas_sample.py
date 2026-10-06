"""Generate a synthetic UAS Remote ID (Open Drone ID) capture over Wi-Fi.

Beacons use the spec framing: vendor IE OUI FA:0B:BC, app code 0x0D, a 1-byte
message counter, then an ODID message pack (protocol version 2). Written with
fixed timestamps to data/uas_sample.pcap and the packaged
waveraider/samples/uas.pcap, which must stay identical. Invented IDs and
coordinates; independent reference data lives in tests/fixtures/opendroneid/.

GROUND TRUTH:
  drones .............. 2
    DRONE-SN-001A  60:60:1f:00:00:01  multirotor  drone 36.8529,-75.9780 alt 120 m  operator 36.8600,-75.9700 (live GNSS)
    SKYDIO-9988    60:60:1f:00:00:02  multirotor  drone 36.8400,-75.9900 alt 80 m   (no System msg -> no operator)
  findings ............ 3  (uas_remote_id x2, operator_location x1)
"""
import struct
from pathlib import Path

from scapy.layers.dot11 import Dot11, Dot11Beacon, Dot11Elt, RadioTap
from scapy.utils import wrpcap

BCAST = "ff:ff:ff:ff:ff:ff"
VERSION = 2
T0 = 1_760_000_000.0   # fixed capture time so regenerated files are byte-identical
ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = (ROOT / "data" / "uas_sample.pcap", ROOT / "waveraider" / "samples" / "uas.pcap")


def message(mtype, body):
    m = bytearray(25)
    m[0] = (mtype << 4) | VERSION
    m[1:1 + len(body)] = body
    return bytes(m)


def basic_id(id_type, ua_type, uas_id):
    return message(0, bytes([(id_type << 4) | ua_type]) + uas_id.encode()[:20])


def location(lat, lon, alt):
    body = bytearray(24)                                         # message bytes 1..24
    body[0] = 2 << 4                                             # status: airborne
    body[4:12] = struct.pack("<ii", round(lat * 1e7), round(lon * 1e7))
    body[14:16] = struct.pack("<H", round((alt + 1000) * 2))     # geodetic altitude
    body[20:22] = struct.pack("<H", 0xFFFF)                      # timestamp: unknown
    return message(1, bytes(body))                               # pressure altitude left unknown (0)


def system(op_lat, op_lon, location_type=1):                     # 1 = live GNSS
    return message(4, bytes([location_type]) + struct.pack("<ii", round(op_lat * 1e7), round(op_lon * 1e7)))


def operator_id(oid):
    return message(5, b"\x00" + oid.encode()[:20])


def pack(msgs):
    return bytes([0xF0 | VERSION, 25, len(msgs)]) + b"".join(msgs)


def beacon(mac, counter, odid_pack, t):
    ie = b"\xfa\x0b\xbc\x0d" + bytes([counter & 0xFF]) + odid_pack
    frame = (
        RadioTap()
        / Dot11(type=0, subtype=8, addr1=BCAST, addr2=mac, addr3=mac)
        / Dot11Beacon()
        / Dot11Elt(ID="SSID", info="")
        / Dot11Elt(ID=221, info=ie)
    )
    frame.time = t
    return frame


def build():
    d1 = pack([
        basic_id(1, 2, "DRONE-SN-001A"),
        location(36.8529, -75.9780, 120),
        system(36.8600, -75.9700),
        operator_id("OP-FAA-12345"),
    ])
    d2 = pack([
        basic_id(1, 2, "SKYDIO-9988"),
        location(36.8400, -75.9900, 80),
    ])
    frames = [beacon("60:60:1f:00:00:01", i, d1, T0 + i * 0.1) for i in range(10)]
    frames += [beacon("60:60:1f:00:00:02", i, d2, T0 + 1 + i * 0.1) for i in range(8)]
    return frames


if __name__ == "__main__":
    frames = build()
    for out in OUTPUTS:
        wrpcap(str(out), frames)
        print(f"wrote {len(frames)} UAS frames -> {out}")
