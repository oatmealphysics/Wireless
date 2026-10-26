# DISC-01: CRSF Ecosystem Routed-Message Parser — Out-of-Bounds Read/Write

**Advisory type:** Sanitized (no operational payload or exact trigger)
**Date:** 2026-10-26
**Status:** Draft — pending maintainer contact

## Summary

A source-available component in the TBS Crossfire (CRSF) ecosystem contains a
routed-message parser defect that produces out-of-bounds memory reads and writes
when processing crafted messages. The defect was reproduced offline using a
source-faithful host fixture compiled with AddressSanitizer and
UndefinedBehaviorSanitizer (GCC 13.3.0).

## Impact

- **Memory corruption:** Multiple independent OOB reads and writes confirmed.
- **Denial of service:** Probable (crash via invalid memory access).
- **Code execution:** Not demonstrated. No controlled program counter,
  ROP/JOP chain, or RCE was achieved.

## Affected component

The affected component's identity, version range, parser class, and source
coordinates are withheld until the maintainer has been contacted. This
information is available in the private disclosure packet.

## Attack prerequisites

1. An attacker must deliver a crafted routed message to the affected parser.
2. **Direct local ingress** (e.g., serial connection to the component) is
   source-confirmed and sufficient.
3. **RF delivery** via a bound receiver's telemetry/configuration path is
   plausible but unproven — the proprietary radio's admission and forwarding
   behavior into the affected parser has not been established.
4. **Arbitrary nearby RF injection** (unbound attacker, no prior link access)
   is not established.

## Evidence boundary

| Claim | Status |
|---|---|
| OOB reads and writes from crafted input | **Reproduced offline** (ASan/UBSan) |
| Direct local ingress to the parser | **Source-confirmed** |
| RF forwarding into the parser | **Unproven** |
| Unbound RF injection | **Unproven** |
| Controlled program counter | **Not demonstrated** |
| Remote code execution | **Not demonstrated** |

## Remediation direction

The root cause is insufficient bounds checking in the routed-message parser's
length and offset arithmetic for certain message classes. The private
disclosure packet includes:

- Exact affected source files and line numbers
- Boundary arithmetic that permits the overrun
- Minimum-complexity trigger messages
- Sanitizer traces
- A proposed bounds-check correction
- Regression test guidance

## Coordination

This advisory will be sent to the affected component's maintainer through
their documented security reporting channel. Operational details (trigger
bytes, exact versions, source coordinates) will be shared only with the
maintainer. Public disclosure will follow the 90-day coordinated disclosure
timeline or the maintainer's published fix, whichever comes first.

## References

- Internal: `research/tbs-crossfire-wireless-security-audit-2026-10-20.md` §3
- Private packet: `work/disclosure/disc-01/`
