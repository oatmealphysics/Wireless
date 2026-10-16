# TinyHawk III Plus evidence ledger

| Claim ID | Falsifiable claim | Label | Evidence | Limit / next discriminating test |
|---|---|---|---|---|
| TH3-001 | The connected application reports Betaflight 4.4.2, revision `23d066d08`, MSP 1.45. | `VALIDATED-LIVE` | 2026-10-15 read-only CLI transcript | Repeat only if identity changes; keep transcript private |
| TH3-002 | The connected target reports STM32F411/S411 and board `EMAX_TINYHAWKF4SX1280`. | `VALIDATED-LIVE` | CLI version and board/config output | Exact package suffix and chip top-mark remain physical gaps |
| TH3-003 | The receiver is integrated SPI ExpressLRS with SX1280 resource assignments on bus 3. | `VALIDATED-LIVE` | Live `RX_SPI`, protocol, bus and pin/resource output | Exact radio top-mark still unobserved |
| TH3-004 | The exact MCU is STM32F411CEU6. | `SOURCE-DERIVED` | EMAX/Betaflight target and board mapping | Confirm by exact-unit PCB/top-mark photograph |
| TH3-005 | The installed link code is ELRS OTA v3 and the live rate index maps to 150 Hz LoRa. | `SOURCE-DERIVED` + `OBSERVED` | Matched 4.4.2 source, live rate index, passive RF measurements | Installed-byte equality remains unproven; packet-byte decode remains incomplete |
| TH3-006 | The archived official 4.4.2 image is the exact installed binary. | `UNPROVEN` | Version/build revision matches but no flash readout exists | Exact readout/attestation would need a separate firmware-read gate; usually unnecessary for current source work |
| TH3-007 | The exact PCB revision and physical chip markings are documented. | `UNPROVEN` | No exact-unit photo inventory in the repository | Exterior and PCB photographs under an approved physical-inspection plan |
| TH3-008 | Betaflight uses a general-purpose OS/RTOS on this target. | `CONTRADICTED` | Version-matched Betaflight source uses embedded scheduler/driver architecture | Preserve as source-derived architecture, not a device-reported string |
