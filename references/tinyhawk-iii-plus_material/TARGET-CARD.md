# TinyHawk III Plus target card

| Field | Current record |
|---|---|
| Intake ID | `EMAX-TH3P-LAB-01` |
| Ownership/scope | Owner-supplied TinyHawk III Plus; owned CTF research bench |
| Replaceability | Treat as non-expendable and competition-relevant |
| Current allowed level | Offline analysis, observation, and documented read-only identity/configuration queries |
| Current Linux endpoint | `/dev/ttyACM0` during 2026-10-16; stable by-id product path exists but should still be rechecked |
| Motion/output controls | Propellers removed before any powered interaction; disarmed; no active RF/output test in this characterization |
| Private evidence | Ignored `work/tinyhawk-live-2026-10-15/` and `work/captures/` |
| Sensitive fields | ELRS UID, USB/device identifiers beyond sanitized product strings, configuration dump, RF captures |
| Stop conditions | Unexpected motor motion, arming, reset/reboot, configuration prompt/write, heat, disconnect, battery distress, or RF output outside the plan |
| Recovery posture | Disconnect cleanly; preserve logs; do not reset defaults or flash to resolve an identity discrepancy |

Configuration writes, firmware readout, flash/update, debug probing, live RF
TX/injection, and crafted-input tests each require their own later gate.
