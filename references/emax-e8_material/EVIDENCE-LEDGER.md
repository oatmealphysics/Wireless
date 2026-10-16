# EMAX E8 evidence ledger

| Claim ID | Falsifiable claim | Label | Evidence | Limit / next discriminating test |
|---|---|---|---|---|
| E8-001 | The connected serial personality is CDC ACM `8888:2001`. | `VALIDATED-LIVE` | 2026-10-16 USB descriptors and Linux driver binding | Re-enumerate after every mode change; identifiers are generic |
| E8-002 | A fixed vendor read at 115200 8-N-1 returns a 17-byte config response. | `VALIDATED-LIVE` | Successful 2026-10-16 native read; prior protocol reconstruction | Keep raw response private; repeat only after an explicit port handoff |
| E8-003 | The response exposes a six-byte pairing UID and rate/config indexes. | `VALIDATED-LIVE` | Parser and cross-checked response structure | Index semantics beyond validated mappings need an independent vendor oracle; do not write values to infer them |
| E8-004 | A normal HID personality identifies itself as `EMAXJoystick` at `0483:5710`. | `OBSERVED` | Earlier live enumeration recorded in protocol research | Not re-observed in the current session; no mode transition performed |
| E8-005 | The connected unit's MCU is ESP32, STM32, AT32, Air32, or another named family. | `UNPROVEN` | Only sibling-target hypotheses and generic USB descriptors | Exact-unit chip top-mark or attributable vendor BOM/schematic |
| E8-006 | The connected unit uses an SX1280. | `UNPROVEN` | Product/protocol relationship and sibling targets | Exact-unit chip mark, attributable schematic/BOM, or independently identified firmware |
| E8-007 | `REV_0100` or `Serial Update Tool 2.1` is the installed firmware version. | `CONTRADICTED` | USB bcdDevice field and host-tool title provenance | Obtain a device-reported version or exact attributable image |
| E8-008 | The exact installed firmware image has been acquired. | `UNPROVEN` | Corpus review finds no E8-specific binary | Vendor-supplied image with exact model/revision provenance; firmware readout remains separately gated |
| E8-009 | FCC filing `2AL7Y-EMAXE8` depicts this exact unit revision. | `UNPROVEN` | Filing is a 2021 EMAXE8 family reference | Compare exact-unit labels, connector, board layout, and PCB revision photographs |
