# DISC-03: Downstream CRSF Consumers — Type-Specific Length-Validation Candidates

**Advisory type:** Sanitized (no operational detail)
**Date:** 2026-10-26
**Status:** Draft — pending further analysis and maintainer contact

## Summary

Source review of two downstream consumers of the TBS CRSF serial protocol
identified type-specific length-validation gaps in their CRSF frame parsers.
These candidates have not been promoted to confirmed vulnerabilities: no
target fault, write primitive, or crash has been demonstrated.

## Impact (potential)

- **Parser misbehavior:** Possible if a crafted CRSF frame with an unexpected
  length for its type reaches the affected parser.
- **Memory corruption:** Not demonstrated.
- **Code execution:** Not demonstrated.

## Affected components

The identities and versions of the two affected components are withheld
pending maintainer contact. The private disclosure packet contains the
component names, version ranges, source file coordinates, and the specific
frame types involved.

## Attack prerequisites

1. An attacker must deliver a crafted CRSF serial frame to the consumer.
2. Ingress boundaries vary by component — some accept CRSF from a directly
   connected module, others from an accepted peer over a radio link.
3. Pure RF reach from an unbound attacker is unproven for both components.

## Evidence boundary

| Claim | Status |
|---|---|
| Length-validation gaps identified in source | **Confirmed** (source review) |
| Target fault or crash | **Not demonstrated** |
| Write primitive | **Not demonstrated** |
| RF delivery from unbound attacker | **Unproven** |

## Remediation direction

Each consumer should validate that the payload length of a received CRSF
frame matches the expected length for its declared frame type before
processing the payload. Frames with unexpected lengths should be discarded.

## Coordination

Lower priority than DISC-01 and DISC-02. Will be sent to the respective
maintainers after the higher-severity findings are acknowledged.

## References

- Internal: `research/tbs-crossfire-wireless-security-audit-2026-10-20.md`
- Private packet: `work/disclosure/disc-03/`
