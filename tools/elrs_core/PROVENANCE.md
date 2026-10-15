# PROVENANCE — vendored elrs_sdr/core protocol layer

Vendored from the `elrs-sdr-transfer` companion repo per SESSION-NOTES.md §9.4
Phase 1 step 1. These are exact copies — no modifications.

## Source

| Field | Value |
|---|---|
| Repository | `gitlab01.an.dd:andeds-agent/elrs-sdr-transfer` (enclave) / `~/Drone_Project/elrs-sdr-transfer` (laptop) |
| Commit | `7936602a73b773c2ed4478212865383ee6358f3e` |
| Commit message | `docs: catalog E8 reference acquisitions with provenance and target limits` |
| Date vendored | 2026-10-15 |

## Files

| Vendored path | Source path | SHA-256 | Lines |
|---|---|---|---|
| `crc.py` | `elrs_sdr/core/crc.py` | `82415676300288706766f5f0ff4c0eb70cea722a8404382730518ee9d52c2d1e` | 76 |
| `ota.py` | `elrs_sdr/core/ota.py` | `ee3fbc7deea416c315c2e2aabdd5048f3ebbcbbef3c61f62eeb75d91e28c141e` | 500 |
| `fhss.py` | `elrs_sdr/core/fhss.py` | `48e540e0a50060ec87ba4c25a82c98c7980a253f88fae86dbd62ec09cfa4182d` | 217 |

## Validation

These modules are self-tested in the companion repo (29 checks pass) and
round-trip against the pinned `firmware/src/expresslrs-4.1.0` C source:
- CRC-14/16 polynomials match `src/lib/CRC/crc.{h,cpp}`
- OTA packet model matches `src/lib/OTA/OTA.{h,cpp}`
- FHSS LCG + sequence builder matches `src/lib/FHSS/{random.cpp,FHSS.cpp}`
- The LCG shift is `>>16` (confirmed against firmware + GNU_Radio_ExpressLRS)
- The FHSS sequence length rounds DOWN (confirmed against real RF captures)

Evidence class: **VALIDATED** (protocol layer). The PHY layer (gfsk.py,
lora.py, etc.) is Phase 2 and is not vendored here.

## Rules

Per SESSION-NOTES.md §9.6:
- Do not modify these files directly. Changes go through the companion repo
  and are re-vendored with an updated commit pin.
- The OTA version (currently 4) is a parameter in `ota.py`, not a constant
  in the consuming tools. This is the structural fix for P1-2.
