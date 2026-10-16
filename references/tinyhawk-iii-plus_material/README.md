# EMAX TinyHawk III Plus evidence dossier

Status as of 2026-10-16: **functionally identified, with exact physical and
installed-byte gaps**. Live firmware identity, MCU family, board target, and
receiver/resource configuration are confirmed. Version-matched official
firmware and source are already in the corpus.

## What is confirmed

- The current USB personality enumerates as `0483:5740`, product
  `Betaflight STM32F411`, using Linux `cdc_acm`.
- A read-only Betaflight CLI session reported Betaflight 4.4.2, build
  `2023-11-30 01:55:56`, revision `23d066d08`, MSP API 1.45,
  `STM32F411`/`S411`, board `EMAX_TINYHAWKF4SX1280`, manufacturer EMAX, and
  craft name `TH III Plus`.
- Live resources report integrated SPI ExpressLRS on bus 3 with SX1280 signal
  assignments, plus an MPU6000-class gyro on SPI bus 1.
- The official Betaflight 4.4.2 STM32F411 release, derived raw binary,
  matching 4.4.2 source archive, and EMAX target configuration are acquired,
  hashed, and registered.

## Qualification that still matters

- `STM32F411` is live-reported. The more specific `STM32F411CEU6` package/part
  claim comes from the board/target mapping and has not been verified from the
  top-mark on this exact flight controller.
- `SX1280` is strongly corroborated by the live target name, resource mapping,
  target configuration, and measured 2.4 GHz ELRS behavior. Its chip top-mark
  and the exact PCB revision have not been photographed.
- The archived 4.4.2 image is **version-matched**, not proven byte-identical to
  installed flash. Runtime configuration, target customization, calibration,
  and stored identity can differ.
- Betaflight's bare-metal scheduler/driver architecture is source-derived;
  it is not a separate general-purpose OS identified by the device.

## Completion boundary

For practical protocol and firmware research, this device is identified well
enough to use the 4.4.2 evidence lane with explicit version/target qualifiers.
To satisfy the blueprint's strict physical identity, add exact-unit exterior
and PCB photographs showing board revision and chip markings. Opening the
airframe/enclosure and any firmware readout require separate gates. The owner
has now prohibited **any firmware flash, update, erase, restore, or recovery
flash on this drone under any circumstances** (see `AGENTS.md`). No
configuration write is needed to close the ordinary documentation gaps.

## Records

- [Target card](TARGET-CARD.md)
- [Interface map](INTERFACE-MAP.md)
- [Artifact manifest](ARTIFACTS.md)
- [Evidence ledger](EVIDENCE-LEDGER.md)
- [Test log](TEST-LOG.md)
- [Open questions](OPEN-QUESTIONS.md)

The detailed live transcript summary is
[`research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md`](../../research/TINYHAWK-III-LIVE-CONFIG-2026-10-15.md).
