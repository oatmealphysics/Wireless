# Responsible Disclosure Tracking

**Created:** 2026-10-26
**Maintained by:** Project team (Claude Code, Codex, owner)
**Classification:** This document tracks disclosure state only. Operational
payloads, exact trigger inputs, sanitizer traces, and UID material are kept
under ignored `work/disclosure/` and must not be committed.

## Disclosure policy

1. Contact the affected maintainer through their documented security channel
   before publishing any technical detail beyond what is already public.
2. Allow 90 days from initial contact for a fix or public acknowledgement
   before considering independent publication.
3. Share the minimum information needed for the maintainer to reproduce and
   fix the issue. Provide a sanitized advisory (no weaponized payload).
4. Track each finding independently; one maintainer's response does not gate
   another's timeline.
5. Do not publish operational payloads (forged packets, exploit scripts, PoC
   binaries) until the maintainer has acknowledged and either released a fix
   or the 90-day window has elapsed.
6. Disclosure state is tracked separately from CTF game-day readiness.
   A finding used in an authorized competition against owned equipment is not
   the same as public disclosure.

## Finding inventory

### DISC-01: CRSF ecosystem routed-message parser memory corruption

| Field | Value |
|---|---|
| **Component** | Source-available CRSF ecosystem component (identity withheld) |
| **Affected versions** | Withheld pending maintainer contact |
| **Severity estimate** | High (memory corruption: OOB read + write) |
| **Evidence class** | Offline-reproduced (ASan/UBSan on source-faithful fixture) |
| **RF reachability** | Direct local ingress confirmed; RF forwarding into the affected parser is unproven |
| **What is proven** | Multiple independent OOB reads and writes from crafted routed messages |
| **What is NOT proven** | Controlled program counter, ROP/JOP chain, RCE, pure RF delivery |
| **Private materials** | `work/disclosure/disc-01/` — affected versions, parser class, source coordinates, trigger conditions, sanitizer traces, remediation guidance |
| **Sanitized advisory** | [`docs/advisories/DISC-01-crsf-parser-oob.md`](advisories/DISC-01-crsf-parser-oob.md) |
| **Disclosure state** | **NOT YET CONTACTED** |
| **Maintainer channel** | TBD — identify the component's security reporting process |
| **Date contacted** | — |
| **Maintainer response** | — |
| **Fix released** | — |
| **90-day deadline** | — (starts on contact date) |
| **Internal reference** | `research/tbs-crossfire-wireless-security-audit-2026-10-20.md` §3 |

### DISC-02: ExpressLRS Backpack ESP-NOW MSPv2 stack overwrite

| Field | Value |
|---|---|
| **Component** | ExpressLRS Backpack (`lib/MSP/msp.cpp`) |
| **Affected versions** | Release 1.5.9 (commit `437a4f43`) and current master (`d15ed305`) as of 2026-10-19 |
| **Severity estimate** | High (stack buffer overflow from unchecked MSPv2 16-bit length) |
| **Evidence class** | Offline-reproduced (ASan on host fixture) |
| **RF reachability** | Source-derived: unencrypted ESP-NOW receive callback parses attacker-controlled frames without MAC or length validation before write |
| **What is proven** | Stack overwrite of the 64-byte MSP payload buffer via oversized MSPv2 length field |
| **What is NOT proven** | Controlled PC on embedded ESP8266/ESP32 target, RCE over the air |
| **Private materials** | `work/disclosure/disc-02/` — reproduction fixture, trigger payload, sanitizer output |
| **Sanitized advisory** | [`docs/advisories/DISC-02-backpack-espnow-overflow.md`](advisories/DISC-02-backpack-espnow-overflow.md) |
| **Disclosure state** | **NOT YET CONTACTED** |
| **Maintainer channel** | ExpressLRS GitHub Security Advisories or maintainer direct contact |
| **Date contacted** | — |
| **Maintainer response** | — |
| **Fix released** | — |
| **90-day deadline** | — |
| **Internal reference** | `research/common-drone-wireless-subsystem-rce-survey-2026-10-19.md` Finding A |

### DISC-03: Downstream CRSF consumers — type-specific length-validation candidates

| Field | Value |
|---|---|
| **Component** | Two downstream CRSF consumers (identities withheld) |
| **Affected versions** | Withheld pending maintainer contact |
| **Severity estimate** | Medium (length-validation candidates; no target fault or write demonstrated) |
| **Evidence class** | Private source review only |
| **RF reachability** | Direct module/accepted-peer boundaries vary; pure RF reach unproven |
| **What is proven** | Type-specific length-validation gaps identified in source |
| **What is NOT proven** | Target fault, write primitive, RCE, RF delivery |
| **Private materials** | `work/disclosure/disc-03/` — affected components, versions, source coordinates |
| **Sanitized advisory** | [`docs/advisories/DISC-03-crsf-consumer-length.md`](advisories/DISC-03-crsf-consumer-length.md) |
| **Disclosure state** | **NOT YET CONTACTED** |
| **Maintainer channel** | TBD per component |
| **Date contacted** | — |
| **Maintainer response** | — |
| **Fix released** | — |
| **90-day deadline** | — |
| **Internal reference** | `research/tbs-crossfire-wireless-security-audit-2026-10-20.md` ranked findings table |

### DISC-04: Betaflight 4.4.2 failsafe suppression via non-RC ELRS packets

| Field | Value |
|---|---|
| **Component** | Betaflight SPI ELRS receiver driver (`expresslrs.c`) |
| **Affected versions** | 4.4.2 source (confirmed); 2026.6.2 source (same pattern present) |
| **Severity estimate** | Medium–High (safety: failsafe delay/suppression during flight) |
| **Evidence class** | Source-derived (not validated on live hardware) |
| **RF reachability** | Requires UID-derived CRC14 initializer (obtainable from passive SYNC capture) |
| **What is proven** | Source path: CRC-valid non-RC packets refresh signal timer and cause stale RC data to be treated as fresh |
| **What is NOT proven** | Live reproduction, measured suppression duration, exploitation on installed binary |
| **Private materials** | `work/disclosure/disc-04/` — source trace, proposed correction |
| **Sanitized advisory** | [`docs/advisories/DISC-04-betaflight-failsafe-suppression.md`](advisories/DISC-04-betaflight-failsafe-suppression.md) |
| **Disclosure state** | **NOT YET CONTACTED** |
| **Maintainer channel** | Betaflight GitHub Security Advisories or `security@betaflight.com` |
| **Date contacted** | — |
| **Maintainer response** | — |
| **Fix released** | — |
| **90-day deadline** | — |
| **Internal reference** | `research/betaflight-442-elrs-nonrc-failsafe-finding.md` |

### DISC-05: Betaflight 4.4.2 MSP command injection via ELRS OTA (design issue)

| Field | Value |
|---|---|
| **Component** | Betaflight MSP handler + ELRS OTA integration |
| **Affected versions** | 4.4.2 (source-confirmed); likely present in other versions with SPI ELRS and MSP-over-ELRS enabled |
| **Severity estimate** | Critical (design: unauthenticated command execution including force-disarm, reboot, GPS overwrite, PID modification, failsafe reconfiguration) |
| **Evidence class** | Source-derived; MSP-over-ELRS reachability confirmed even with runtime `feature -TELEMETRY` |
| **RF reachability** | Requires ELRS pairing (UID + CRC14 + FHSS sequence) — obtainable from passive capture |
| **What is proven** | Source path from OTA packet to full MSP command table without authentication beyond CRC + connection state + model match |
| **What is NOT proven** | Live exploitation on hardware; this is a design-level finding about the ELRS security model |
| **Note** | This is inherent to the ELRS v3 design (no encryption, no authentication beyond CRC). It may be better characterized as an informational/design advisory rather than a vulnerability report. The ELRS project is aware that v3 has no encryption. The specific MSP command surface and failsafe implications may be less well understood. |
| **Sanitized advisory** | [`docs/advisories/DISC-05-elrs-msp-injection-design.md`](advisories/DISC-05-elrs-msp-injection-design.md) |
| **Disclosure state** | **NOT YET CONTACTED** |
| **Maintainer channel** | Betaflight + ExpressLRS maintainers |
| **Date contacted** | — |
| **Maintainer response** | — |
| **Fix released** | — |
| **90-day deadline** | — |
| **Internal reference** | `research/betaflight-442-vulnerability-audit.md` §2, §3 |

### DISC-06: Betaflight Device Ping stack buffer format overflow

| Field | Value |
|---|---|
| **Component** | Betaflight CRSF Device Ping handler |
| **Affected versions** | Pre-fix suffixed/calver cloud, RC, or nightly builds whose formatted identity exceeds the old buffer |
| **Severity estimate** | Medium (DoS/hang; attacker does not choose the overflow bytes) |
| **Evidence class** | Reproduced through established ELRS and TBS Agent links |
| **RF reachability** | Reachable via ELRS or TBS Agent link to the FC |
| **What is proven** | FC hang when formatted identity exceeds stack buffer |
| **What is NOT proven** | RCE (overflow content is compile-time identity, not attacker-controlled) |
| **Private materials** | `work/disclosure/disc-06/` |
| **Sanitized advisory** | Deferred — low priority given attacker does not control overflow content |
| **Disclosure state** | **NOT YET CONTACTED** |
| **Internal reference** | `research/tbs-crossfire-wireless-security-audit-2026-10-20.md` ranked findings |

## Already-public findings (no disclosure action needed)

These findings already have public CVEs, PRs, or vendor acknowledgements:

| ID | Finding | Public reference | Status |
|---|---|---|---|
| PUB-01 | PX4 CRSF oversized packet buffer overflow | CVE-2026-32706 | Fixed in PX4 1.17.0-rc2 |
| PUB-02 | PX4 MAVLink log-handler stack overflow | CVE-2026-32743 | Public advisory |
| PUB-03 | INAV MSP_DATAFLASH_READ 4-byte overrun | Fixed in INAV 10.0.0-rc1 | Public fix |
| PUB-04 | ExpressLRS CRSF parameter chunk indexing | PR #3825 | Open PR with fix |
| PUB-05 | Betaflight CRSF oversized-length read | PR #15254 | Fixed in newer Betaflight |
| PUB-06 | DJI BLE unauthenticated commands | CVE-2026-78306 | Public advisory |
| PUB-07 | DJI passive WiFi credential sniff | CVE-2026-77812 | Public advisory |
| PUB-08 | TBS Crossfire telemetry-flood loss of bind | Vendor catalog ID 0x0624 (v6.36 fix) | Vendor-acknowledged |
| PUB-09 | mavlink-router ULog reassembly overflows | Issues #487/#488 | Public issues |

## Operational security

- **Payloads stay private.** Forged packets, trigger inputs, reproduction
  scripts, and sanitizer traces remain under ignored `work/disclosure/`.
  They are shared only with the affected maintainer during disclosure.
- **Sanitized advisories are safe to share.** They describe the finding class,
  affected component (where already identified publicly), evidence boundary,
  and remediation direction without providing weaponizable detail.
- **CTF use is separate.** Using a finding against owned equipment in an
  authorized competition does not constitute public disclosure. CTF-specific
  tools and payloads are in the main repo under their respective tool
  directories.
- **No secrets in this file.** UIDs, binding phrases, exact trigger bytes,
  and sanitizer stack traces are never placed in this tracked document.

## Next steps

1. Identify the security reporting channel for each affected component.
2. Prepare the `work/disclosure/` private packets with reproduction steps.
3. Send DISC-01 and DISC-02 first (highest severity, clearest reproduction).
4. Send DISC-04 and DISC-05 to Betaflight (safety-critical, source-derived).
5. Track responses and 90-day windows in this document.
6. DISC-03 and DISC-06 are lower priority; send after the higher-severity
   items are acknowledged.
