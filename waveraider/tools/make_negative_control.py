"""Generate the Remote ID negative control: an ordinary 802.11 capture with no Remote ID.

The committed tests/fixtures/wifi_beacons_no_remote_id.pcap came from this script (it
was the retired Wi-Fi analyzer's sample, data/sample.pcap). Synthetic frames and
invented MACs, nothing captured from a real environment. Beacons, deauths, data and
EAPOL frames must produce no Remote ID observations, findings or diagnostics.
Regenerating writes new pcap timestamps, so the bytes differ from the committed file
while the frames are the same.

FRAMES:
  total frames ........ 109
  access points ....... 3    HomeNet    (02:00:00:00:01:00)
                             CoffeeShop (02:00:00:00:02:00, WPS enabled)
                             HomeNet    (02:00:00:00:03:00, evil twin)
  deauth burst ........ 50 frames, AP1 -> client   (attack)
  EAPOL/handshake ..... 4 frames citing AP1         (handshake material)
  evil twin ........... HomeNet on 2 BSSIDs (AP1, AP3)
  WPS ................. CoffeeShop
"""
from pathlib import Path

from scapy.layers.dot11 import (
    Dot11,
    Dot11Beacon,
    Dot11Deauth,
    Dot11Elt,
    RadioTap,
)
from scapy.layers.eap import EAPOL
from scapy.layers.l2 import LLC, SNAP
from scapy.utils import wrpcap

AP3 = "02:00:00:00:03:00"          # evil twin: same SSID as AP1, different BSSID
_WPS_IE = b"\x00\x50\xf2\x04\x10\x4a\x00\x01\x10"  # minimal WPS vendor IE

AP1 = "02:00:00:00:01:00"
AP2 = "02:00:00:00:02:00"
CLIENT = "02:00:00:00:aa:bb"
BCAST = "ff:ff:ff:ff:ff:ff"


def beacon(bssid: str, ssid: str, wps: bool = False):
    frame = (
        RadioTap()
        / Dot11(type=0, subtype=8, addr1=BCAST, addr2=bssid, addr3=bssid)
        / Dot11Beacon(cap="ESS+privacy")
        / Dot11Elt(ID="SSID", info=ssid)
    )
    if wps:
        frame = frame / Dot11Elt(ID=221, info=_WPS_IE)
    return frame


def eapol(client: str, ap: str):
    # WPA 4-way handshake material: an 802.1X/EAPOL frame over 802.11 data.
    return (
        RadioTap()
        / Dot11(type=2, subtype=0, addr1=ap, addr2=client, addr3=ap)
        / LLC(dsap=0xAA, ssap=0xAA, ctrl=3)
        / SNAP(OUI=0x000000, code=0x888E)
        / EAPOL(version=2, type=3)
    )


def deauth(src: str, dst: str):
    return (
        RadioTap()
        / Dot11(type=0, subtype=12, addr1=dst, addr2=src, addr3=src)
        / Dot11Deauth(reason=7)
    )


def data_frame(client: str, ap: str):
    # BSSID (addr3) is the AP; a client->AP data frame is addr1=AP, addr2=client.
    return RadioTap() / Dot11(type=2, subtype=0, addr1=ap, addr2=client, addr3=ap)


def build() -> list:
    frames = []
    frames += [beacon(AP1, "HomeNet") for _ in range(20)]
    frames += [beacon(AP2, "CoffeeShop", wps=True) for _ in range(20)]   # WPS on
    frames += [beacon(AP3, "HomeNet") for _ in range(5)]                 # evil twin
    frames += [deauth(AP1, CLIENT) for _ in range(50)]                   # attack burst
    frames += [data_frame(CLIENT, AP1) for _ in range(10)]
    frames += [eapol(CLIENT, AP1) for _ in range(4)]                     # handshake
    return frames


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "wifi_beacons_no_remote_id.pcap"
    frames = build()
    wrpcap(str(out), frames)
    print(f"wrote {len(frames)} frames -> {out}")
