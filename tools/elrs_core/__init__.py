"""Vendored ELRS protocol core — CRC, OTA packet model, FHSS sequence.

Exact Python ports of the ExpressLRS C source, validated with encode/decode
round-trips against the pinned firmware/src/expresslrs-4.1.0 source. See
PROVENANCE.md for commit pin and source paths.

Phase 1 (protocol layer only, pure Python, no numpy):
  crc.py  — Crc2Byte (CRC-14/16) + Crc8 (CRC-8/DVB-S2 for CRSF framing)
  ota.py  — OTA packet model (sync/rc/linkstats/data, pack/unpack, CRC)
  fhss.py — FHSS LCG PRNG + block-shuffle sequence builder + domain table
"""
