# TinyHawk III Plus open questions

| Priority | Question | Why it matters | Smallest safe next step | Current gate |
|---|---|---|---|---|
| P0 | What exact exterior label, PCB revision, and production lot does this unit carry? | Binds vendor/target evidence to the physical article | Photograph exterior, labels, connector, board markings visible without disassembly; redact identifiers | L1 observation |
| P0 | Do the actual MCU and radio top-marks confirm STM32F411CEU6 and SX1280? | Closes strict blueprint hardware identity | Close PCB photographs after an approved physical-inspection/opening plan | Opening gate required |
| P1 | Is the installed image byte-identical to the official 4.4.2 STM32F411 artifact plus target configuration? | Determines how narrowly binary-derived findings apply | Prefer source/build corroboration; only pursue readout if a concrete research question justifies a separately approved firmware-read plan | Firmware-read gate required |
| P1 | Which target customization/calibration regions differ from the public artifact? | Separates code from per-unit state | Compare documented non-sensitive CLI configuration with target defaults; keep UID/calibration private | Read-only plan; no writes |
| P2 | What bootloader/debug configuration is present? | Useful for recovery and low-level analysis, but not needed for present ELRS work | Research official board docs first; photograph pads before considering probes | Offline/opening/probing gates |

For current CTF work, the first two rows are documentation gaps. The 4.4.2
version/target evidence is already sufficient for qualified source analysis;
the exact-image question should not trigger a risky readout merely to make the
record look complete.
