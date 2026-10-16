# New Hardware One-Page Gate Card

> Memory aid for the detailed
> [blueprint](new-hardware-research-blueprint.md), not authorization. Current
> `AGENTS.md`, `SECURITY.md`, target card, and owner direction control.

```text
ID/unit/revision: ___________________  Owner/operator: ___________________
Allowed: L0 offline  L1 passive  L2 read  L3 state  L4 active  L5 persistent
Prohibited: _________________________  Goal: _____________________________
Private evidence: work/_________________________________________________
```

## 0 — Intake and safety

- [ ] Record ownership, scope, allowed level, prohibitions, custodian, and
      stops; inventory/photograph device, ports, labels, accessories, power.
- [ ] Verify voltage, polarity, ground, logic level, current limit, ESD,
      battery/heat/motion/optical hazards before power.
- [ ] Remove/restrain loads; make power-off reachable; keep output off,
      receive-only, shielded, terminated, or looped back; one operator/port.

**Gate 0:** authority, power path, hazards, baseline, and stop plan are known.

## 1 — Identify

- [ ] Record direct processor, output-chip/front-end, memory, board, and exact
      model/revision evidence; keep sibling-device claims hypothetical.
- [ ] Separate boot ROM, bootloader, app/OS, radio, config, host/mobile tool,
      cloud; enumerate every permitted bus/USB/setup-vs-normal-network/BLE/RF
      personality and transition.
- [ ] Use descriptors and authorized-lab inventory (`ip link`, `ip neigh`,
      `arp -an`, bounded `nmap -sn`; `-sP` is legacy). Label active discovery.
- [ ] Map path, state, direction, identity, access, read/write/update/output,
      persistence, reachability, evidence, permission. Label every claim.

**Gate 1:** exact-unit identity, state/personality and output-front-end tables,
and generic interface map exist; no transition exceeded authorization.

## 2 — Research and acquire

- [ ] Index vendor artifacts/docs, pinned source/tag/build flags, silicon docs,
      filings, advisories, and secondary work; map component version ancestry.
- [ ] Preserve originals. Record origin/UTC/operator/SHA-256/format/identity/
      signature/classification and exact/version/related/unknown target match.
- [ ] Record every transform. Identify and statically unpack host packaging
      (Enigma Virtual Box, NSIS, Inno Setup, SFX, executable packer) without
      running downloads; keep private/licensed/large material under `work/`.

**Gate 2:** every artifact is attributable and target-classified; unavailable,
encrypted, gated, dumped, and prohibited outcomes are stated honestly.

## 3 — Analyze offline

- [ ] Reconstruct control, update/flash, and output protocols: transport,
      framing/fields/units, checksum/integrity, state, replay/auth, side effects.
- [ ] Combine static work with separately approved isolated host tracing
      (`strace`/API trace) or TAP/mirror capture; build checksum vectors and a
      native read-only reader checked against an independent oracle.
- [ ] Ask every parser: bounds? validation order? state/auth? read/reflection?
      write/update? errors/concurrency? execution context and privilege to write
      flash/config or drive output?
- [ ] Map binary/container/architecture/memory/boot chain and inputs→checks→
      sinks; analyze host privilege/update/secrets and RF/network PHY/auth/
      failsafe/reachability.
- [ ] Tie each finding to target/hash/interface/preconditions, counter-evidence,
      evidence label, safe reproducer, negative control, and next test.

**Gate 3:** another analyst can reproduce each claim offline. Static/offline is
never described as exact-target live capability.

## 4 — Validate read-only

- [ ] Re-read boundaries; confirm handoff, one port, wiring/current, tool hash,
      exact query, cap/timeout/stop, baseline, private log, and redaction.
- [ ] Put outputs in verified off/safe state except the exact isolated control
      path; observe, then send only the smallest documented read.
- [ ] Preserve raw evidence privately; redact serial/UID/MAC/key/credential/
      flag; disconnect cleanly and compare after-state.

**Gate 4:** only intended reads occurred. `VALIDATED-LIVE` names exact unit,
state, build, observable, and limits.

## 5 — Gated interaction

- [ ] For each separate action, sign exact target/build/interface/bytes/hash,
      question/effect/persistence, limits, containment, backup/recovery,
      controls/aborts, data handling, operator, approver, timestamp.
- [ ] Gate separately: config/state write; firmware read/dump; update/flash;
      live output/TX; crafted-input/fuzz test. Approval never carries forward.
- [ ] Send approved output only to bench loopback/simulator/dummy load/shielded
      or conducted path/restrained owned target—never open sky, production, or
      a live/third-party target. Start known-good; stop on first surprise.

**Gate 5:** one approved action stayed within limits and left a known state.

## 6 — Integrate

- [ ] Complete device folder: target card, interface map, artifacts, evidence
      ledger, log, questions, private pointers, sanitized fixtures.
- [ ] Put native tools in `tools/`; safe default, target/version check, bounds,
      redaction, self-test, stop/recovery docs.
- [ ] Update tracked project surface, inventory, methodology, README, notes;
      validate links/hashes/code/fixtures/secrets/binaries; commit and push.

**Gate 6:** a teammate can reproduce offline work, see every prohibition, run
safe defaults, find evidence, and name the next authorized step.

## Never

- [ ] No unauthorized/non-owned lab target or live/third-party effect.
- [ ] No read treated as write/TX/flash/reset/fuzz approval; no device rule
      overridden by this method or a tool flag.
- [ ] No guessed power, identity, image, version, output containment, recovery,
      or port handoff; no hazardous motion.
- [ ] No downloaded-code execution during acquisition or public upload of
      private material; no secrets/UIDs/MACs/keys/serials/flags/captures in Git.
- [ ] No artifact without provenance/hash; no host-tool/firmware conflation;
      no static/offline result called live; no only-unit brick risk without
      explicit approval and proven recovery.

```text
Evidence/build: _____________________  Hard boundary: ___________________
Unknown / smallest safe next test: _____________________________________
Next owner / commit / evidence: ________________________________________
```
