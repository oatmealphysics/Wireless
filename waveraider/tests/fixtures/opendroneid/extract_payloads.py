"""Print each Wi-Fi beacon's Remote ID payload for reference_harness.c.

Output lines: <frame index> <transmitter> <message counter> <payload hex>, where
the payload is everything after vendor OUI FA:0B:BC, app code 0x0D and the
1-byte message counter.

Usage: python extract_payloads.py <capture.pcap>
"""
import sys

from scapy.all import PcapReader  # type: ignore
from scapy.layers.dot11 import Dot11, Dot11Elt  # type: ignore


def main(path: str) -> None:
    with PcapReader(path) as rd:
        for i, pkt in enumerate(rd):
            if not pkt.haslayer(Dot11):
                continue
            e = pkt.getlayer(Dot11Elt)
            while isinstance(e, Dot11Elt):
                raw = bytes(e)
                body = raw[2:2 + raw[1]] if len(raw) > 1 else b""
                if raw[:1] == b"\xdd" and body[:4] == b"\xfa\x0b\xbc\x0d" and len(body) > 5:
                    print(i, pkt[Dot11].addr2, body[4], body[5:].hex())
                e = e.payload.getlayer(Dot11Elt)


if __name__ == "__main__":
    main(sys.argv[1])
