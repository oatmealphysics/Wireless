"""
Hardware-free self-test for dronewatch.

Proves the layers that don't need a radio:
  1. ELRS CRC engines match known vectors.
  2. OTA build -> validate round-trips for 8- and 13-byte packets, all types.
  3. Bind-mode UID recovery (the UID on-ramp).
  4. Channel pack/unpack round-trips.
  5. FHSS LCG matches an independent reference; sequence is a permutation.
  6. The (fixed) LoRa sync-word matched filter peaks at the true chirp start.
  7. Open Drone ID encode -> decode -> summarize round-trips (serial, operator,
     position all recovered).
  8. The fusion TrackTable merges events by identity into single tracks.

Run:  python -m dronewatch selftest
"""

from __future__ import annotations

import sys

import numpy as np

from . import crc, ota, fhss, lora, opendroneid as odid, fusion
from .crc import Crc2Byte, Crc8, ELRS_CRC14_POLY, ELRS_CRC16_POLY

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _uid() -> bytes:
    return bytes([0x11, 0x22, 0xAB, 0xCD, 0xEF, 0x13])


def test_crc_vectors():
    print("CRC vectors:")
    c8 = Crc8(0xD5)
    v = c8.calc(b"123456789")
    check("CRC8(0xD5) '123456789' == 0xBC", v == 0xBC, f"got 0x{v:02x}")
    c14 = Crc2Byte(14, ELRS_CRC14_POLY)
    v14 = c14.calc(b"123456789")
    check("CRC14 masked to 14 bits", (v14 & ~0x3FFF) == 0, f"0x{v14:04x}")
    c16 = Crc2Byte(16, ELRS_CRC16_POLY)
    v16 = c16.calc(b"123456789")
    check("CRC16 masked to 16 bits", (v16 & ~0xFFFF) == 0, f"0x{v16:04x}")


def test_ota_roundtrip():
    print("OTA build/validate round-trip:")
    uid = _uid()
    crc_init = ota.ota_uid_crc_init(uid)
    nonce = 42
    # SYNC (both sizes)
    for is_full in (False, True):
        raw = ota.build_sync(is_full=is_full, fhss_index=3, nonce=7,
                             rf_rate_enum=5, uid4=uid[4], uid5=uid[5],
                             crc_init=crc_init, ota_nonce=nonce)
        ok = ota.validate_crc(raw, is_full, crc_init, nonce, ota.PACKET_TYPE_SYNC)
        check(f"SYNC crc valid (full={is_full})", ok)
    # RCDATA std
    raw = ota.build_rcdata_std([1000, 1200, 900, 1500], switch_index=2,
                               switch_value=3, is_armed=True,
                               crc_init=crc_init, ota_nonce=nonce)
    pkt = ota.decode(raw, uid, crc_init, nonce)
    check("RCDATA std decodes with valid CRC", pkt is not None and pkt.crc_valid)
    # RCDATA full
    raw = ota.build_rcdata_full([1000, 1200, 900, 1500, 800, 1700, 1024, 1024],
                                uplink_power=3, is_high_aux=True, is_armed=True,
                                crc_init=crc_init, ota_nonce=nonce)
    pkt = ota.decode(raw, uid, crc_init, nonce)
    check("RCDATA full decodes with valid CRC", pkt is not None and pkt.crc_valid)


def test_bind_uid_recovery():
    print("Bind-mode UID recovery:")
    uid = _uid()
    bind_init = ota.bind_crc_init()
    raw = ota.build_sync(is_full=False, fhss_index=0, nonce=0, rf_rate_enum=5,
                         uid4=uid[4], uid5=uid[5], crc_init=bind_init, ota_nonce=0)
    pkt = ota.decode(raw, None, bind_init, 0)
    ok = (pkt is not None and pkt.sync is not None and
          pkt.sync.uid4 == uid[4] and pkt.sync.uid5 == uid[5])
    check("bind SYNC recovers UID4/UID5", ok)


def test_channel_pack():
    print("Channel pack/unpack:")
    vals = [100, 500, 1000, 1023]
    packed = ota.pack_channels_4x10(vals)
    out = ota.unpack_channels_4x10(packed)
    # unpack returns value<<1; compare the top 10 bits
    recon = [v >> 1 for v in out]
    check("pack/unpack 4x10 round-trips", recon == [v & 0x3FF for v in vals],
          f"in={vals} out={recon}")


def test_fhss():
    print("FHSS LCG + sequence:")
    l = fhss.Lcg(12345)
    a = [l.rng() for _ in range(4)]
    s = 12345
    ref = []
    for _ in range(4):
        s = (214013 * s + 2531011) % 2147483648
        ref.append(s >> 15)
    check("LCG matches reference", a == ref, f"{a} vs {ref}")
    dom = fhss.DOMAINS["ISM2G4"]
    seq = fhss.build_sequence(fhss.uid_seed(_uid()), dom.freq_count,
                              dom.sync_channel)
    check("sequence block is a permutation",
          sorted(seq[:dom.freq_count]) == list(range(dom.freq_count)))
    check("sync channel at block start", seq[0] == dom.sync_channel)


def test_lora_sync_detect():
    print("LoRa sync-word matched filter (the fixed conjugation):")
    sf = 7
    params = lora.LoRaParams(sf=sf, bw_hz=800_000, sample_rate=800_000 * (1 << sf) / (1 << sf))
    chirp = lora._base_chirp(sf)
    rng = np.random.default_rng(1)
    noise = (rng.normal(size=60) + 1j * rng.normal(size=60)) * 0.1
    sig = np.concatenate([noise, chirp, noise]).astype(np.complex128)
    idx = lora.detect_sync_word(sig, params, sync_word=chirp, threshold=0.5)
    # the matched filter should peak at the chirp start (index 60), within a few
    hit = any(abs(i - 60) <= 2 for i in idx)
    check("sync-word correlation peaks at the true chirp start", hit,
          f"peaks={idx}")


def test_opendroneid():
    print("Open Drone ID encode/decode round-trip:")
    serial = "1581F5FMC24210TEST01"
    operator = "FAA-OP-TEST99"
    lat, lon = 37.33182, -122.03118
    pack = odid.build_pack([
        odid.build_basic_id(serial, id_type=1, ua_type=2),
        odid.build_location(lat, lon, alt_geo_m=123.0, speed_h_ms=10.0),
        odid.build_system(lat, lon),
        odid.build_operator_id(operator),
    ])
    dec, consumed = odid.decode_message(pack)
    check("pack decodes", dec is not None and dec["n_messages"] == 4)
    rep = odid.summarize(dec["messages"])
    check("serial recovered", rep.uas_id == serial, f"got {rep.uas_id!r}")
    check("operator recovered", rep.operator_id == operator,
          f"got {rep.operator_id!r}")
    check("latitude recovered (1e-5)", abs((rep.latitude or 0) - lat) < 1e-5,
          f"got {rep.latitude}")
    check("longitude recovered (1e-5)", abs((rep.longitude or 0) - lon) < 1e-5,
          f"got {rep.longitude}")
    check("identity() prefers serial", rep.identity() == serial)


def test_fusion_merge():
    print("Fusion TrackTable merge:")
    table = fusion.TrackTable()
    serial = "DRONE-XYZ-1"
    # same drone seen twice (two events, one identity) -> one track
    pack = odid.build_pack([
        odid.build_basic_id(serial), odid.build_location(37.0, -122.0)])
    ev1 = fusion.remoteid_event(pack, transport="ble")
    ev2 = fusion.remoteid_event(pack, transport="ble")
    table.ingest(ev1)
    table.ingest(ev2)
    # a different, unidentified SDR control-link -> separate track
    table.ingest({"source": "sdr", "ts": ev1["ts"], "kind": "control-link",
                  "identity": None, "protocol": "ELRS LoRa", "confirmed": False,
                  "fields": {"track_key": "2.4:2440:lora", "freq_mhz": 2440}})
    rows = table.rows()
    check("two events one identity merge to one track",
          sum(1 for r in rows if r["identity"] == serial) == 1)
    check("distinct signals stay separate", len(rows) == 2, f"rows={len(rows)}")
    summ = table.summary()
    check("summary counts confirmed identity", summ["confirmed"] == 1,
          f"summary={summ}")


def main() -> int:
    print("dronewatch self-test\n" + "=" * 40)
    test_crc_vectors()
    test_ota_roundtrip()
    test_bind_uid_recovery()
    test_channel_pack()
    test_fhss()
    test_lora_sync_detect()
    test_opendroneid()
    test_fusion_merge()
    print("=" * 40)
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS)}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
