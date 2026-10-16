# EMAX E8 target card

| Field | Current record |
|---|---|
| Intake ID | `EMAX-E8-LAB-01` |
| Ownership/scope | Owner-supplied EMAX E8 controller; owned CTF research bench |
| Replaceability | Treat as non-expendable and competition-relevant |
| Current allowed level | L0 offline, L1 observation, L2 documented normal configuration reads |
| Explicitly prohibited | Flash/update/erase/restore; settings writes; reset or bootloader-entry sequences; live-device fuzzing/crafted inputs; RF TX |
| Shared-device rule | One process/operator per port; explicit handoff required |
| Private evidence | Ignored `work/e8-session/` only |
| Current Linux endpoint | `/dev/ttyACM1` during 2026-10-16; re-enumerate before every session |
| Safe query | Fixed vendor Read Configuration request through `tools/e8-read-config.py`; dry-run by default |
| Stop conditions | Unexpected mode/LED change, reset, output, heat, disconnect/re-enumeration, malformed/long response, port contention |
| Recovery posture | Disconnect cleanly; do not improvise a reset, write, loader, or update |

No enclosure-opening, hardware-probing, firmware-readout, or output-test gate is
currently recorded. This card documents the boundary; it does not expand it.
