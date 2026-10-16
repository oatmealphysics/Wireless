"""Vendored ELRS protocol core — CRC, OTA, FHSS, PHY, measurement.

Exact Python ports of the ExpressLRS C source, validated with encode/decode
round-trips against the pinned firmware/src/expresslrs-4.1.0 source. See
PROVENANCE.md for the commit pin + source paths + SHA-256s.

Phase 1 (protocol layer, pure Python, no numpy):
  crc.py  — Crc2Byte (CRC-14/16) + Crc8 (CRC-8/DVB-S2 for CRSF framing)
  ota.py  — OTA packet model (sync/rc/linkstats/data, pack/unpack, CRC)
  fhss.py — FHSS LCG PRNG + block-shuffle sequence builder + domain table

Phase 2 (PHY + measurement layer, numpy):
  lora.py        — LoRa demod (Semtech bit-mapping, BPSK diff, interleaving)
  gfsk.py        — GFSK/FLRC front-end + FlrcParams
  lora_payload.py— LoRa payload decode (handoff to ota for the CRC check)
  lora_sync.py   — sync-word extraction (reuses lora._base_chirp)
  identify.py    — air-state classifier (burst physics, OTA-version/rate
                   iterator, CRC gate; the "measure + classify" engine)

The Phase-2 layer is what makes real-capture packet-byte validation possible:
demod a capture down to packet bytes (lora/gfsk), then CRC-check them with the
Phase-1 frame layer (ota/crc).
"""
