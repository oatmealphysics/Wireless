# PROVENANCE — vendored elrs_sdr/core protocol layer

Vendored from the `elrs-sdr-transfer` companion repo per SESSION-NOTES.md §9.4
Phase 1 step 1. `crc.py` is an exact copy (no modifications). `ota.py` and
`fhss.py` carry only ruff lint cleanups applied in commit `e0a8ea2`
(#65): an unused `field` import was removed and import lists were re-sorted.
No protocol logic, constants, or behavior changed; the SHA-256 columns below
are the **current** file hashes (they therefore differ from the source hashes
for the two touched files — see the "Source-hash" column for the original).

## Source

| Field | Value |
|---|---|
| Repository | `gitlab01.an.dd:andeds-agent/elrs-sdr-transfer` (enclave) / `~/Drone_Project/elrs-sdr-transfer` (laptop) |
| Commit | `7936602a73b773c2ed4478212865383ee6358f3e` |
| Commit message | `docs: catalog E8 reference acquisitions with provenance and target limits` |
| Date vendored | 2026-10-15 |

## Files — Phase 1 (protocol layer, pure Python, no numpy)

The "Source-hash" column is the SHA-256 of the file in the pinned
`elrs-sdr-transfer` commit (unchanged). The "Current hash" column is the
SHA-256 of the vendored file as it stands in this repo. They match for
`crc.py` (untouched) and differ for `ota.py`/`fhss.py` (ruff cleanups only).

| Vendored path | Source path | Source-hash (pinned commit) | Current hash | Lines |
|---|---|---|---|---|
| `crc.py` | `elrs_sdr/core/crc.py` | `82415676300288706766f5f0ff4c0eb70cea722a8404382730518ee9d52c2d1e` | `82415676300288706766f5f0ff4c0eb70cea722a8404382730518ee9d52c2d1e` | 75 |
| `ota.py` | `elrs_sdr/core/ota.py` | `ee3fbc7deea416c315c2e2aabdd5048f3ebbcbbef3c61f62eeb75d91e28c141e` | `f44289359493dbfbbd72c519bf4a445fd5d7d713581130b8631ba6203f4bbc17` | 499 |
| `fhss.py` | `elrs_sdr/core/fhss.py` | `48e540e0a50060ec87ba4c25a82c98c7980a253f88fae86dbd62ec09cfa4182d` | `c2200854081dc130e311158dc560076c76a3a0be14e0ece361dcd12eb13bbcc4` | 216 |

## Files — Phase 2 (PHY + measurement layer, numpy)

Vendored 2026-10-16 from the companion repo at commit `ed891b488efd88d378b64327f29ba834564b119c` (`docs: remove Phase 0 from NEW-HARDWARE BLUEPRINT`). These close the "real-capture packet-byte validation" gap: the PHY layer demods a capture down to packet bytes, which the Phase-1 frame layer (`ota.py`/`crc.py`) then CRC-checks. Exact copies — no modifications. They use **numpy** (unlike Phase 1) + the same relative-import convention (`from . import ota`, `from .lora import _base_chirp`, etc.).

| Vendored path | Source path | SHA-256 | Lines |
|---|---|---|---|
| `lora.py` | `elrs_sdr/core/lora.py` | `da558eef3e9976c2b37552041b15bd52e71bcca12b6918666cdf13b43e5721ee` | 343 |
| `gfsk.py` | `elrs_sdr/core/gfsk.py` | `54851091c929f50f94e9680196ac482fffbb4d271956212444a87aa3b1d3c383` | 456 |
| `lora_payload.py` | `elrs_sdr/core/lora_payload.py` | `1ff453e26cdb7fae14184b50f1de0d089f69ffde7f83a72a07417a0e91cba550` | 476 |
| `lora_sync.py` | `elrs_sdr/core/lora_sync.py` | `e7b0aa1503fe9622ed8a436755903eb753fb6ec305ce0fc6f3f0066e78f96400` | 242 |
| `identify.py` | `elrs_sdr/core/identify.py` | `47f2615318844680d9830b28b4dfac78b2641f17b6224fdc7c1e8336378a4faf` | 581 |


## Validation

These modules are self-tested in the companion repo (29 checks pass) and
round-trip against the pinned `firmware/src/expresslrs-4.1.0` C source for
the v4 protocol:
- CRC-14/16 polynomials match `src/lib/CRC/crc.{h,cpp}`
- OTA packet model matches `src/lib/OTA/OTA.{h,cpp}`
- FHSS LCG + sequence builder matches `src/lib/FHSS/{random.cpp,FHSS.cpp}`
- The LCG shift is `>>16` (confirmed against firmware + GNU_Radio_ExpressLRS)
- The FHSS sequence length rounds DOWN (confirmed against real RF captures)

Evidence class: **VALIDATED for v4** (protocol layer). Betaflight 4.4.2 v3
consumer adaptations are source-derived and still need a real-capture
cross-check. The Phase-2 PHY + measurement layer (gfsk.py, lora.py,
lora_payload.py, lora_sync.py, identify.py) is now vendored — it is the
measurement/demod path that feeds the real-capture packet-byte validation
(the "self-consistent decode → CRC-validated real-frame" gap). It is
**SELF-CONSISTENT** (round-trips our own encode/decode) until validated
against a real capture on the bench.

## Rules

Per SESSION-NOTES.md §9.6:
- Do not modify these files directly. Changes go through the companion repo
  and are re-vendored with an updated commit pin.
- The vendored `ota.py` implements v4 CRC behavior. Its `ota_version=3`
  arguments do **not** make `ota_uid_crc_init`, `bind_crc_init`, or
  non-SYNC `generate_crc`/`validate_crc` conform to Betaflight 4.4.2 v3.
  Consumers targeting v3 must apply the source-confirmed v3 initializer and
  omit nonce XOR; a general fix belongs in the companion repo followed by
  re-vendoring with a new commit pin.
