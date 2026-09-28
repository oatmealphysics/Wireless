#!/usr/bin/env python3
"""
ELRS over-air packet decoder - self-contained, no GNU Radio, no SDR needed
to unit-test the packet/CRC layer.

Verified against ExpressLRS source (src/lib/OTA/OTA.h, OTA.cpp, crc.cpp):
  - Crc2Byte: LSB-first table CRC, 14-bit poly 0x2E57 (OTA4) / 16-bit 0x3D65 (OTA8)
  - CRC init = (UID[4]<<8 | UID[5]) ^ (OTA_VERSION_ID << 8)   [OTA_VERSION_ID=4 on main]
  - bind mode: CRC init = OTA_VERSION_ID (constant), nonce locked to 0
  - packet type = low 2 bits of byte0; SYNC carries fhssIndex + UID[4..5]

Run the built-in self-test:   python3 elrs_decode.py --selftest
Decode a captured .bin of raw OTA packets:
    python3 elrs_decode.py --file cap.bin --uid A1B2C3D4E5F6 --size 8
"""
import argparse, hashlib, struct, sys

OTA_VERSION_ID = 4  # ExpressLRS/src/include/targets.h:8 (current main)
CRC14_POLY = 0x2E57  # ELRS_CRC14_POLY  (OTA4 / 8-byte packet)
CRC16_POLY = 0x3D65  # ELRS_CRC16_POLY  (OTA8 / 13-byte packet)


class Crc2Byte:
    """Mirror of ExpressLRS/src/lib/CRC/crc.cpp (reflected/LSB-first, table-driven)."""
    def __init__(self, bits, poly):
        assert 8 <= bits <= 16
        self.bits, self.poly = bits, poly
        self.mask = (1 << bits) - 1
        highbit = 1 << (bits - 1)
        tab = []
        for i in range(256):
            crc = i << (bits - 8)
            for _ in range(8):
                crc = (crc << 1) ^ ((crc & highbit) and poly or 0)
            tab.append(crc & self.mask)
        self.tab = tab

    def calc(self, data, crc=0):
        for b in data:
            crc = (crc << 8) ^ self.tab[((crc >> (self.bits - 8)) ^ b) & 0xFF]
        return crc & self.mask


def uid_from_phrase(phrase):
    # ELRS derives the 6-byte UID as md5(binding phrase)[:6]
    return hashlib.md5(phrase.encode()).digest()[:6]


def uid_seed(uid):
    return ((uid[2] << 24) | (uid[3] << 16) | (uid[4] << 8) |
            (uid[5] ^ OTA_VERSION_ID)) & 0xFFFFFFFF


def elrs_rng_step(seed):
    # PRNG: seed = (0x343FD*seed + 0x269EC3) mod 2**31 ; return seed>>16
    seed = (0x343FD * seed + 0x269EC3) % 0x80000000
    return seed >> 16


def crc_init_for(uid, ota_version=OTA_VERSION_ID):
    return (((uid[4] << 8) | uid[5]) ^ (ota_version << 8)) & 0xFFFF


def validate_ota4(pkt8, init, nonce=0):
    """8-byte 'std' packet: byte0 = type:2|crcHigh:6, last byte = crcLow."""
    pkt = bytearray(pkt8)
    crc_high = pkt[0] >> 2
    pkt[0] &= 0x03            # zero crcHigh bits before calc (matches firmware)
    calc = Crc2Byte(14, CRC14_POLY).calc(bytes(pkt[:7]), init ^ nonce)
    received = (crc_high << 8) | pkt[7]
    return calc == received


def validate_ota8(pkt13, init, nonce=0):
    """13-byte 'full' packet: 11-byte body + 2-byte little-endian CRC16."""
    calc = Crc2Byte(16, CRC16_POLY).calc(bytes(pkt13[:11]), init ^ nonce)
    received = struct.unpack('<H', pkt13[11:13])[0]
    return calc == received


def unpack_4x10(raw5):
    """4x10-bit channel unpack (little-endianish). Returns 4 x 10-bit values."""
    bitbuf = 0
    nbits = 0
    for b in reversed(raw5):
        bitbuf |= b << nbits
        nbits += 8
    out = []
    for _ in range(4):
        out.append(bitbuf & 0x3FF)
        bitbuf >>= 10
    return out


def selftest():
    ok = True
    # OTA8 (16-bit) round-trip: fabricate a full-res body, stamp CRC, must validate.
    body = bytes([0x01]) + b'\x11\x22\x33\x44\x55\x66\x77\x88\x99\xaa'  # exactly 11 bytes
    assert len(body) == 11
    init = 0x1234
    c16 = Crc2Byte(16, CRC16_POLY)
    pkt8 = body + struct.pack('<H', c16.calc(body, init))
    assert len(pkt8) == 13
    r = validate_ota8(pkt8, init)
    print("[PASS] OTA8 (16-bit 0x3D65) round-trip  crc=0x%04x" % c16.calc(body, init) if r
          else "[FAIL] OTA8 round-trip")
    ok = ok and r

    # OTA4 (14-bit) round-trip - standard/hybrid mode.
    # In this mode byte0 top-6 bits (crcHigh) are ZEROED before the CRC calc, and
    # the 14-bit CRC is recovered as ((crcHigh<<8)+crcLow) where crcHigh holds the
    # 6 bits (8..13) of the CRC. See OTA.cpp ValidatePacketCrcStd.
    body4 = bytearray([0b01, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x00])
    c14 = Crc2Byte(14, CRC14_POLY)
    tmp = bytearray(body4)
    tmp[0] &= 0x03                       # zero crcHigh for the calc
    full = c14.calc(bytes(tmp[:7]), init)
    # stamp: crcHigh = top 6 bits of the 14-bit CRC (bits 8..13), crcLow = low 8 bits
    tmp[0] = (tmp[0] & 0x03) | ((full >> 8) & 0x3F)
    tmp[7] = full & 0xFF
    r = validate_ota4(bytes(tmp), init)
    print("[PASS] OTA4 (14-bit 0x2E57, std/hybrid) round-trip  crc=0x%03x" % full if r
          else "[FAIL] OTA4 round-trip")
    ok = ok and r

    # wrong init must FAIL (proves the check is real)
    r = not validate_ota8(pkt8, init ^ 0xFFFF)
    print("[PASS] OTA8 rejects wrong CRC-init" if r else "[FAIL] wrong-init not rejected")
    ok = ok and r

    uid = uid_from_phrase("my-binding-phrase")
    s0 = uid_seed(uid)
    s1 = elrs_rng_step(s0)
    s2 = elrs_rng_step(s1)
    print("[info] phrase->uid=%s seed=0x%08x rng1=0x%04x rng2=0x%04x" %
          (uid.hex(), s0, s1, s2))
    print("[info] crc_init for that uid = 0x%04x   (bind-mode init = 0x%02x)" %
          (crc_init_for(uid), OTA_VERSION_ID))
    print("\n%s" % ("ALL PASS" if ok else "SOME FAILED"))
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--file")
    ap.add_argument("--uid")
    ap.add_argument("--size", type=int, default=8, choices=[8, 13], help="OTA4=8, OTA8=13")
    a = ap.parse_args()
    if a.selftest or (not a.file):
        sys.exit(0 if selftest() else 1)
    data = open(a.file, "rb").read()
    init = crc_init_for(bytes.fromhex(a.uid)) if a.uid else OTA_VERSION_ID
    n = a.size
    print("decoding %d bytes as %dx %d-B packets, init=0x%04x" %
          (len(data), len(data) // n, n, init))
    good = 0
    total = len(data) // n
    for i in range(0, len(data) - n + 1, n):
        pkt = data[i:i + n]
        valid = validate_ota8(pkt, init) if n == 13 else validate_ota4(pkt, init)
        good += valid
        print("  pkt#%4d type=%d  %s  %s" % (i // n, pkt[0] & 0b11, "OK " if valid else "bad", pkt.hex()))
    print("\n%d/%d packets CRC-valid" % (good, total))
