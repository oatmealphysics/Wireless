"""ELRS core decode logic — the single source of truth.

Ports of the upstream ExpressLRS reference (drone_refs/_ext/ExpressLRS):
  crc.py   -> Crc2Byte (CRC-14 poly 0x2E57 / CRC-16 poly 0x3D65), OTA CRC init
  ota.py   -> OTA packet model (sync/rc/linkstats, pack/unpack, CRC validate)
  fhss.py  -> FHSS sequence (LCG PRNG + block-shuffle), ISM2G4 channel map
  lora.py  -> software LoRa demod (preamble correlate + symbol demod)

Everything else in the package (radio, scan, webui, cli) builds on this.
"""
