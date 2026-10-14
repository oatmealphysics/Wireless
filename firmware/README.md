# EMAX E8 ELRS firmware test corpus

Collected 2026-10-14 for offline reference and security testing. Nothing in this folder has been flashed to the transmitter.

## Inventory

- `reference/ExpressLRS-master.tar.gz` — official ExpressLRS source snapshot from the repository's `master` branch at collection time (commit `dd96654519249798707f011b7c79236350f246f8`). This is source, not a ready-to-flash image.
- `reference/ExpressLRS-targets-master.tar.gz` — official supported-target registry snapshot (commit `51a697b3220f069baf3adf2c5353b710df70bbe0`).
- `reference/targets.json` — searchable copy of that target registry. It includes EMAX OLED 2.4 GHz TX (`EMAX_2400_TX`) and EMAX Nano 2.4 GHz TX (`EMAX_NANO_2400_TX`), both configured as ESP32 / SX1280 ExpressLRS transmitter targets. The current registry does not contain an E8 target/name.
- `reference/elrs-4.1.0.lua` — official Lua script attached to ExpressLRS release 4.1.0; it is not transmitter firmware.

## Firmware availability finding

I found no publicly downloadable E8-specific firmware image or version archive in EMAX's public support material or the official ExpressLRS release assets. EMAX documents a custom E8 serial configurator and a serial upgrade process that accepts a separately obtained firmware file. Its configurator download is linked below; it is a settings/updater utility, not itself an identified E8 firmware image. ExpressLRS publishes configurable source and target definitions; the official release page's asset is the Lua script, while target firmware is built for a selected target/configuration.

The EMAX OLED/Nano firmware targets are included as relevant test references, not asserted to be E8 firmware. Even though EMAX E8 product material identifies ELRS 2.4 GHz and the E8 manual describes an SX1280 RF chip, the official registry does not establish that these board layouts, pin assignments, update method, or firmware build match the E8. Keep test corpus provenance and target identity attached to any extracted or generated image.

## Official references

- ExpressLRS source: <https://github.com/ExpressLRS/ExpressLRS>
- ExpressLRS targets: <https://github.com/ExpressLRS/targets>
- ExpressLRS 4.1.0 release: <https://github.com/ExpressLRS/ExpressLRS/releases/tag/4.1.0>
- Official EMAX 2.4 GHz transmitter update guidance: <https://www.expresslrs.org/quick-start/transmitters/emax2400/>
- Official EMAX E8 custom serial configurator article: <https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264-expresslrs-e8-transmitter-custom-configurator>
- EMAX serial configurator download (Mega): <https://mega.nz/file/pkICkJhQ#xO-EF1GunGGqs-GASkFishIIPg1iRWMo_-i_owCs-Ag>
- Official supported-target policy: <https://github.com/ExpressLRS/targets#expresslrs-officially-supported-devices>

## Reproducing / extending this corpus

The source snapshots are pinned to the commits above. For historic release testing, use the official Configurator with each released ExpressLRS version and an explicitly selected target, then preserve the generated binary, build log, target JSON, release tag, and SHA-256 together. As the registry has no E8 target, label builds for `EMAX_2400_TX` and `EMAX_NANO_2400_TX` as related EMAX test samples, not E8-matched firmware.
