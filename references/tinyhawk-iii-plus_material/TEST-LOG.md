# TinyHawk III Plus test log

## 2026-10-15 — live read-only identity/configuration

- Read Betaflight CLI version, board identity, resource map, receiver settings,
  feature state, and failsafe configuration.
- Stored the raw transcript under ignored `work/` because it contains the ELRS
  UID and other unit-specific configuration.
- Performed no reset, save, defaults, configuration write, bootloader action,
  flash, motor actuation, or RF transmission.
- Acquired and registered the matching official 4.4.2 release artifact, raw
  analysis conversion, source tag archive, and target configuration.

## 2026-10-16 — USB descriptor corroboration

- Enumerated the already connected device without opening its port.
- Observed `/dev/ttyACM0`, USB `0483:5740`, product `Betaflight STM32F411`,
  CDC ACM 1.10 with bulk IN/OUT and interrupt IN endpoints, bound to `cdc_acm`.
- Did not repeat the CLI query because the prior transcript already answers the
  identity questions and another live interaction would add little evidence.

Separate passive RF captures and offline protocol analysis are indexed from
`SESSION-NOTES.md`; they support link behavior, not physical chip top-marks or
byte identity of installed flash.
