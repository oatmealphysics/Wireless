"""elrs_sdr — capture, decode, and (later) transmit ExpressLRS drone signals.

Modules:
  crc.py   — exact port of ELRS Crc2Byte / CRC8
  fhss.py  — FHSS LCG, regulatory domains, hop-sequence builder
  ota.py   — OTA packet model: pack/unpack/validate/generate (RX+TX symmetric)
  lora.py  — LoRa PHY: chirp mod/demod (RX+TX symmetric), preamble + sync-word
  radio.py — UHD device wrapper, symmetric RX/TX, IQ capture
  cli.py   — command-line entry points (scan, monitor, capture, decode, tx)

Status: RX path is the primary target and is the first thing to validate on
hardware. The TX path is built to share the exact same packet/PHY primitives
so that adding transmission is a matter of wiring, not re-implementation.
"""

__version__ = "0.1.0"
