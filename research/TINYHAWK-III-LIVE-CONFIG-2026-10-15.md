# TinyHawk III Plus — Live Betaflight Identification and Configuration Summary

**Evidence class:** LIVE OBSERVATION (read-only Betaflight CLI transcript)

**Observed:** 2026-10-15 from the owner’s connected flight controller. This is
the current lab test article, not an assertion about an eventual CTF target.
The raw CLI transcript is intentionally not committed: it contains the ELRS UID,
which is pairing-sensitive configuration. Preserve any raw capture only in an
ignored private `work/` directory.

## Firmware identity

| Field | Observed value |
|---|---|
| Firmware | Betaflight 4.4.2 |
| Build timestamp | 2023-11-30 01:55:56 |
| Revision | `23d066d08` |
| MCU / target family | STM32F411 (`S411`) |
| MSP API | 1.45 |
| Board name | `EMAX_TINYHAWKF4SX1280` |
| Manufacturer | EMAX |
| Craft name | TH III Plus |

This is a material correction to the existing firmware corpus: the committed
2026.6.2 `CRAZYBEEF4SX1280` image remains a **related sample**, not the image
installed on this controller. Any finding derived from that corpus must retain
its version/build qualifier until checked against Betaflight 4.4.2.

## Observed hardware and receiver configuration

| Area | Observation | Interpretation |
|---|---|---|
| Receiver | `RX_SPI` enabled; `rx_spi_protocol = EXPRESSLRS` | The receiver is integrated SPI ExpressLRS, not an external serial RX. |
| Radio bus | `rx_spi_bus = 3` | Consistent with the target’s SX1280 bus mapping. |
| SX1280 signals | CS `PA15`, EXTI `PC14`, reset `PA8`, busy `PA13` | Matches the board-level resource map reported by the live controller. |
| ELRS domain | `ISM2400` | 2.4 GHz ELRS domain. |
| ELRS rate | `expresslrs_rate_index = 2` | Measure the actual PHY/version from a recorded signal; do not infer it from this field alone. |
| ELRS switch mode | `HYBRID` | Runtime configuration observed. |
| ELRS model ID | `255` | Runtime configuration observed. |
| ELRS UID | Present in the transcript | **Redacted; do not commit or log.** |
| Telemetry feature | `feature -TELEMETRY` | Telemetry is disabled in this live runtime configuration. This does not by itself prove whether a compile-time ELRS/MSP telemetry path exists in the installed binary. |
| Failsafe | `failsafe_procedure = DROP` | Bench testing requires a non-flight-safe setup; unexpected link loss is not benign. |

Other live resource observations include the MPU6000-class SPI gyro on bus 1,
OSD SPI on bus 2, and serial RX/TX resources consistent with the board target.

## CLI behavior observed

The CLI entered normally and produced a large configuration batch after `dump`.
It also printed these messages:

```text
###WARNING: NO CUSTOM DEFAULTS FOUND###
###ERROR IN dump: NO CONFIG FOUND###
###ERROR IN version: NO CONFIG FOUND###
```

These messages are recorded as observations, not as evidence that configuration
is absent: the same session returned board identity, resources, features, and
runtime settings. Do not attempt a reset, reflash, or configuration write to
resolve them. If they matter later, reproduce them read-only and compare the
Configurator/CLI version and connection mode.

## Implications for the research corpus

1. **Prioritize a 4.4.2 evidence lane.** Acquire the official Betaflight 4.4.2
   STM32F411 release artifact, matching 4.4.2 source, and the matching EMAX
   unified target configuration. Record provenance and SHA-256 before analysis.
2. **Do not call the official release image the exact installed binary.** The
   normal Betaflight configurator/CLI identifies the installed build but does
   not read out its raw flash image. An official artifact can be a version- and
   target-matched analysis sample; its hardware match remains qualified unless
   byte identity is independently established.
3. **Re-qualify earlier 2026.6.2 conclusions.** The target name and resource
   layout are corroborated, but build-dependent protocol code, compile flags,
   and vulnerability claims require comparison with 4.4.2 before being treated
   as applicable to this flight controller.
4. **Separate compile-time and runtime facts.** The committed target config
   enables `USE_RX_EXPRESSLRS_TELEMETRY`, while the observed live configuration
   disables the `TELEMETRY` feature. Neither fact alone establishes reachability
   of an MSP-over-air path in the live 4.4.2 binary.

## Next safe actions

1. Save the raw CLI transcript privately, with the UID redacted from any shared
   excerpt.
2. Obtain the matching 4.4.2 release/source/configuration artifacts without
   changing the flight controller; add their hashes and provenance to the
   firmware register.
3. Diff the 4.4.2 target configuration and source against the existing 2026.6.2
   corpus before promoting any existing finding to the live test article.
4. Keep the controller in read-only configuration mode. No firmware flash,
   reset, settings write, or RF test is authorized by this observation.
