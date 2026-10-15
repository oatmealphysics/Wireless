# Firmware Inventory & Development Chain

> **From a known firmware need → a developed, validated exploit chain.**
> andeds (orchestrator), 2026-10-15.
>
> This is the **operational companion** to
> [`firmware-to-poc-methodology.md`](firmware-to-poc-methodology.md).
> That doc is the *methodology* (the ten wickets, the gates). This doc is the
> **inventory** (every firmware we know we need, what we have, what we need to
> download) + the **chain to run** against each one, so a teammate can pick up
> any row and start.
>
> **Scope note (per the owner):** the TinyHawk is a **test article** — the one
> platform we have a corpus + bench for. The CTF target is whatever W0
> identifies, and the attack surface is deliberately **broad across every
> candidate protocol family**, not TinyHawk-only.
>
> **Egress constraint:** this enclave has **no internet** (VLAN 716). No
> firmware can be downloaded from here. Everything in the "NEED TO DOWNLOAD"
> column comes in via the laptop → gitlab01 push (or Nextcloud for files), per
> the `enclave-ingest` flow. The download *instructions* are here so the laptop
> side knows exactly what to pull.
>
> **Layer 2 target landscape:** this inventory covers the *firmware* we develop
> on. The broader target landscape — the **controllers and ground stations**,
> not just the drones — is in
> [`research/layer2-wireless-attack-research.md`](../research/layer2-wireless-attack-research.md)
> (three chains: ELRS controller WiFi, WiFi→MAVLink, DJI BLE). The E8 WiFi AP
> / `GET /config` surface (Chain 1) is tracked here as row **11b** / §A6,
> because it's the lab test that would eliminate the UID-recovery step for the
> ELRS path. **§17 (SESSION-NOTES) is the orchestrator's review of the 4.4.2
> rewire + driver diff + Layer 2 research; the assessments in that review are
> mirrored into this inventory (the E8/Chain 1 row, the W-states, the
> "highest-value lab test" call-out).**

---

## Part A — The firmware inventory

Every firmware image we know we need to develop on, with its **have / need**
status, its **wicket state** (from the methodology), and the **chain to run**.

Legend — **HAVE** = in the corpus, hashed, registered. **NEED** = must be
downloaded (laptop → gitlab01). **W-state** = where it sits in the ten-wicket
pipeline (W0 FRAME … W10 GAME-DAY).

### A1. ELRS / Betaflight (the test article — TinyHawk / Crazybee F4SX1280)

| Firmware | HAVE / NEED | Version / target | W-state | Notes |
|---|---|---|---|---|
| `betaflight_CRAZYBEEF4SX1280.bin` | **HAVE** | 2026.6.2 / Crazybee F4SX1280 | W1–W6 done, W7 in progress | The worked example. SHA256-verified, registered. Related-sample (not confirmed game-day image). |
| `betaflight_STM32F411.bin` (+ `.hex`) | **HAVE** | 2026.6.2 / STM32F411 | W1 done (acquire) | Generic F411 build. Same source, different target config. Not yet individually analyzed. |
| `EMAX_TINYHAWKF4SX1280.config` | **HAVE** | target config | W1 (evidence) | Proves `USE_RX_EXPRESSLRS_TELEMETRY` + `feature TELEMETRY` are ON (the MSP-over-air path). |
| ELRS 4.1.0 **source** (`expresslrs-4.1.0.tar.gz`) | **HAVE** | 4.1.0 | W7 (reference) | The pinned reference for the OTA/FHSS/CRC/LoRa/FLRC protocol layer. |
| ExpressLRS **master** source + targets.json | **HAVE** | master snapshot | W7 (reference) | Newer-than-4.1.0 reference; for version-drift checks. |
| ELRS 4.1.0 Lua (handset UI) | **HAVE** | 4.1.0 | reference | The Configurator side. |
| **The exact game-day TinyHawk image** | **NEED** | (owner confirms) | W1 (close the `hardware_match` gap) | The corpus binary is a *related sample*. If the CTF target is this exact drone, pull the exact image from the drone/Configurator to flip `hardware_match` to `confirmed-target`. |
| **A second ELRS drone (multi-target)** | **NEED** | (CTF-dependent) | W1 (acquire) | If the CTF has >1 ELRS target or a different ELRS build (e.g. a different Betaflight version, or a standalone-ESP ELRS with the WiFi-update surface), pull it. |

**Chain to run (ELRS / TinyHawk) — the furthest along, the worked example:**

```
W0  FRAME      [owner] Confirm the CTF target is this drone (or name the real
                       one). Fill the §4 target card. Until then: test-article.
W1  ACQUIRE    [done]  Corpus + register + SHA256 + source. If game-day image
                       is needed: laptop → Configurator export → gitlab01 push.
W2  MODEL      [done]  Cortex-M4F, base 0x08000000, memory map, vector table OK.
W3  RECON      [done]  Entropy (no obfuscation), strings, code regions, call tree.
W4  SURFACE    [done]  ELRS RX inputs → CRC boundary → 3 sinks (RC memcpy = dead
                       end; MSP deserializer = the sink; sync = state).
W5  VULN       [done]  Ranked: MSP-injection (top), bind-mode UID (fastest),
                       RC-overflow (dead end). SELF-CONSISTENT.
W6  DESIGN     [done]  RECOVER→FORGE→SYNC→MODEL-MATCH→INJECT + preconditions +
                       failure modes.
W7  BUILD      [NEXT]  (1) Rewire forger/analyzer/elrs_chain to elrs_core
                       (Step 1, ~1 day, no HW). (2) Vendor gfsk/lora/identify,
                       rewire the TX emulator + passive interceptor (Phase 2).
W8  VALIDATE   [next]  (1) Self-consistency round-trip. (2) Cross-check the
     OFFLINE             forger's bytes against a REAL capture of the target link
                       (the T1-8 capture or a fresh bench one). Measure the OTA
                       version + PHY from the capture — do NOT assume v4.
W9  VALIDATE   [next]  Bench: TinyHawk + E8 + B205. Recover UID (air fhss_seed,
     LIVE                or bind-mode {0,1,2,3,4,5}), forge, FLRC-modulate, TX,
                       confirm the receiver accepts + executes (milestone 5).
                       Positive command (throttle=1000), owned, bench-only.
W10 GAME-DAY   [last]  Re-ground the playbook in W9. Dry-run under time. Failure
                       modes + "what doesn't work." Handoff.
```

---

### A2. MAVLink (autopilot / GCS — the "command injection over MAVLink" path)

| Firmware | HAVE / NEED | Version / target | W-state | Notes |
|---|---|---|---|---|
| **ArduPilot 4.7.2 (CubeOrange ELF)** | **HAVE** | 4.7.2 / CubeOrange | W1 done (acquire) | In the corpus. ELF binary, ARM. |
| **ArduPilot 4.7.2 (Pixhawk1 ELF)** | **HAVE** | 4.7.2 / Pixhawk1 | W1 done (acquire) | In the corpus. ELF binary, ARM. |
| **ArduPilot 4.7.2 (SITL x86_64)** | **HAVE** | 4.7.2 / SITL | W1 done (acquire) | **Runs on x86_64 Linux** — the W8 oracle for MAVLink, no hardware needed. |
| **ArduPilot 4.7.2 source** | **HAVE** | 4.7.2 | W7 (reference) | The pinned reference for the MAVLink handler. |
| **The CTF GCS / companion computer image** | **NEED** | (CTF-dependent) | W1 (acquire) | If the CTF exposes a GCS over network/RF, pull its image + confirm the signing key exposure. |
| MAVLink v2 **spec** (pymavlink) | **HAVE** (reference) | v2 | W7 (reference) | The pinned reference the injector must be built against. |

**Chain to run (MAVLink) — the fastest-to-working path if it's the CTF target:**

```
W0  FRAME      [owner] Is the CTF target a MAVLink autopilot / GCS? Where's the
                       flag (GCS UI? a MAVLog? a command ack)? Signing present?
W1  ACQUIRE    [NEXT]  Pull the exact autopilot firmware + GCS image (laptop →
                       gitlab01). No target firmware yet — this is the blocker.
W2  MODEL      [next]  (Depends on the MCU/SoC: often ARM + Linux companion.)
W3  RECON      [next]  Entropy + strings + the MAVLink handler location.
W4  SURFACE    [done]  Protocol spec known: every MAVLink v2 message, the
                       signing header (link_id + timestamp + signature), the
                       COMMAND_LONG wire order (the P1-3 bug site).
W5  VULN       [done]  P1-1 (signing "bypass" doesn't bypass — the tool mutates
                       authenticated fields, keeps the stale signature), P1-3
                       (COMMAND_LONG packs small fields before floats — wrong
                       wire order), P2-6 (replay "viability" = clock age, not
                       anti-replay).
W6  DESIGN     [done]  Forged COMMAND_LONG / MAV_CMD injection, no-key or
                       leaked-key signing. Preconditions: no signing OR leaked
                       key OR the signing "bypass" (which doesn't work — P1-1).
W7  BUILD      [NEXT]  Fix P1-3 (pack floats first, per the v2 reordering) +
                       P1-1 (either implement real signing with the key, or drop
                       the fake-bypass claim) against pymavlink. Add the
                       regression tests the review said were missing.
W8  VALIDATE   [next]  Round-trip every forged message through pymavlink's
                       parser (the independent oracle). Field order, CRC,
                       signature must all parse.
W9  VALIDATE   [next]  Bench: the autopilot + a MAVLink RX. Confirm the forged
     LIVE                COMMAND executes. (No SDR needed — MAVLink is
                       serial/UDP/telemetry, not necessarily RF-LoRa.)
W10 GAME-DAY   [last]  Playbook + dry-run.
```

> **MAVLink is the lowest-effort path if it's the target** — pure protocol
> work, no RF PHY, no SDR. The P1-3 fix is a one-line pack-order change; the
> P1-1 fix is "use the real key or drop the claim." That's why the critical
> path table lists it alongside ELRS.

---

### A3. Wi-Fi (Tello / AP-mode drones — the "network takeover" path)

| Firmware | HAVE / NEED | Version / target | W-state | Notes |
|---|---|---|---|---|
| **Tello DJI SDK (djitellopy + UDP/54399 protocol)** | **HAVE** | master snapshot | W1 done (acquire) | The Tello control protocol (UDP/TCP JSON) is documented + testable. `tello_test.py` included. |
| **The CTF Wi-Fi drone firmware** | **NEED** | (CTF-dependent) | W1 (acquire) | If the CTF target is a *different* Wi-Fi drone (not a Tello), pull its firmware. If it IS a Tello, the SDK is the corpus. |

**Chain to run (Wi-Fi) — the "is it even a firmware problem" path:**

```
W0  FRAME      [owner] Is the CTF target a Wi-Fi drone? Is the flag on the
                       drone, the GCS, or a network service? Is the AP open or
                       WPA?
W1  ACQUIRE    [NEXT]  Pull the exact Wi-Fi drone firmware (laptop → gitlab01)
                       OR confirm the attack is purely at the Wi-Fi layer (no
                       firmware RE needed — deauth/PMKID/rogue-AP).
W2  MODEL      [cond]  Only if firmware RE is needed (a custom control daemon).
W3  RECON      [cond]  Only if firmware RE is needed.
W4  SURFACE    [done]  Wi-Fi layer: deauth, PMKID capture, rogue AP, WPS.
                       Control protocol: UDP/TCP JSON command surface.
W5  VULN       [done]  Standard Wi-Fi attacks (deauth = DoS; PMKID = key
                       recovery; rogue AP = MITM/command injection). No novel
                       vuln expected — this is a technique, not a 0-day.
W6  DESIGN     [done]  Deauth → capture → forge control commands. OR rogue AP →
                       MITM the JSON protocol → inject.
W7  BUILD      [next]  Review the existing scripts (unreviewed). Build a
                       deauth + PMKID + rogue-AP toolchain (hostapd + aircrack
                       on the laptop's Wi-Fi adapter, or a monitor-mode adapter
                       for the CTF).
W8  VALIDATE   [next]  Offline: the forged JSON commands parse against the
     OFFLINE             protocol. The deauth frames are well-formed.
W9  VALIDATE   [next]  Bench: the Wi-Fi drone + a monitor adapter. Confirm
     LIVE                deauth works, PMKID captures, the forged command
                       executes.
W10 GAME-DAY   [last]  Playbook + dry-run. (Note: Wi-Fi CTFs often need a
                       physical monitor-mode adapter the enclave doesn't have —
                       confirm game-day equipment at W0.)
```

> **Wi-Fi is usually the *least* firmware-RE-heavy path** — it's a network
> technique, not a binary exploit. The "firmware" question is whether the drone
> has a custom control daemon worth RE'ing, or whether the standard Wi-Fi
> attacks are enough. W0 decides.

---

### A4. BLE (the "proximity takeover" path)

| Firmware | HAVE / NEED | Version / target | W-state | Notes |
|---|---|---|---|---|
| **A BLE-capable drone / controller firmware** | **NEED** | (CTF-dependent) | W1 (acquire) — NOT STARTED | No BLE firmware in the corpus. The attack surface is documented (concepts) but no target. BLE CTFs need a BLE adapter (Ubertooth/nRF52) the enclave doesn't have — confirm at W0. |

**Chain to run (BLE) — the lowest-maturity path, not started:**

```
W0  FRAME      [owner] Is the CTF target BLE? What's the GATT service / command
                       surface? Is a BLE adapter available for game day?
W1  ACQUIRE    [NEXT]  Pull the BLE target firmware (laptop → gitlab01).
W2  MODEL      [next]  (Depends on the BLE SoC: nRF52, ESP32-BLE, etc.)
W3  RECON      [next]  Entropy + the GATT service table + the command handlers.
W4  SURFACE    [part]  BLE attack surface documented (pairing/bonding bypass,
                       GATT command injection, MITM). No specific target yet.
W5  VULN       [next]  BLE-specific: weak pairing (Just Works), GATT write
                       without auth, LTK recovery.
W6  DESIGN     [next]  Forge/forge-GATT-writes the control commands.
W7  BUILD      [next]  BLE PoC (Ubertooth/bleak/nRF SDK).
W8  VALIDATE   [next]  Round-trip the GATT writes against the service spec.
W9  VALIDATE   [next]  Bench: BLE adapter + the target. Confirm the command.
W10 GAME-DAY   [last]  Playbook. (BLE CTFs are proximity-limited — range is a
                       W0 fact.)
```

---

### A5. GPS (the "spoofing" path)

| Firmware | HAVE / NEED | Version / target | W-state | Notes |
|---|---|---|---|---|
| **The GPS target (a drone with a GPS receiver)** | **NEED** | (CTF-dependent) | W1 (acquire) — NOT STARTED | No GPS target firmware. The attack is **GPS spoofing** (SDR-based, L1 C/A). The `gps-spoof-poc` exists but is unreviewed. GPS CTFs need an SDR + a GNSS SDR (HackRF/Pluto) — confirm at W0. |

**Chain to run (GPS) — the SDR-path, not started:**

```
W0  FRAME      [owner] Is the CTF target GPS-dependent (failsafe-on-loss,
                       geofencing, a GPS-locked flag)? Is a GNSS SDR available
                       for game day?
W1  ACQUIRE    [NEXT]  Identify the GPS target. (No "firmware" to download in
                       the usual sense — the attack is at the RF/GNSS layer.)
W2  MODEL      [N/A]   SDR-based; no MCU RE.
W3  RECON      [N/A]   The GNSS signal structure (L1 C/A) is well-known.
W4  SURFACE    [done]  GPS spoofing concept documented. The receiver's
                       trust model (does it accept a spoofed fix? failsafe on
                       loss?).
W5  VULN       [done]  GPS spoofing is a standard technique (no 0-day). The
                       "vuln" is the receiver's trust + the failsafe behavior.
W6  DESIGN     [part]  Spoof a fix → move the drone / trigger failsafe / defeat
                       geofence.
W7  BUILD      [part]  Review `gps-spoof-poc`. Build a GNSS SDR spoofing
                       toolchain (yato/softGNSS + HackRF/Pluto).
W8  VALIDATE   [next]  Offline: the spoofed signals are well-formed C/A.
W9  VALIDATE   [next]  Bench: the GNSS SDR + the target. Confirm the spoofed
     LIVE                fix is accepted.
W10 GAME-DAY   [last]  Playbook. (GPS spoofing needs the SDR + range — a W0
                       fact.)
```

---

### A6. E8 (the ELRS TX / handset — delivery device *and* a Layer 2 target)

> **§17 assessment mirrored here (andeds):** the Layer 2 research
> (`research/layer2-wireless-attack-research.md`, Chain 1) reframes the E8. It
> is not *only* the delivery device — it is also a **target in its own right**,
> because the ESP32 TX **auto-starts an open WiFi AP** after link loss, and
> `GET /config` on that AP **leaks the binding phrase + UID**. A read-only WiFi
> config read is allowed under the no-flash constraint. **If the E8's WiFi AP
> is reachable and the config is readable, the UID-recovery step (W8's only
> unknown for ELRS) is eliminated entirely** — the binding phrase is available
> directly, no air-recovery needed. This is the highest-value lab test in the
> project right now.

| Firmware / surface | HAVE / NEED | Version / target | W-state | Notes |
|---|---|---|---|---|
| **E8 ELRS TX firmware** | **NEED** (ref) | 4.x (ELRS) | reference (not a flash target) | The E8 is the *transmitter* we use to deliver the ELRS exploit. No-flash constraint is absolute. |
| **E8 WiFi AP / `GET /config` (Chain 1)** | **NEED** (lab test) | ESP32 ELRS TX | W7 (Chain 1) | **The Layer 2 surface.** Auto-open WiFi AP after link loss; `GET /config` leaks the binding phrase + UID. Read-only lab test on the owned E8 (allowed by the no-flash constraint). If it works, it eliminates the UID-recovery step for the ELRS path. |
| **E8 serial protocol** | **HAVE** (merged reader) | — | W7 (delivery tool) | 64-byte TX / 17-byte RX config protocol confirmed in the `vendor-e8-config-reader`. A live read got no bytes (evening notes) — air-recovery is the primary UID source *until* the WiFi AP read is tested. |

**Chain to run (E8) — the delivery-device chain *plus* the Layer 1 Chain 1
target chain:**

```
Delivery-device role (the means, not the target):
W0  [owner] Can the E8 TX our forged frames (reprogrammed via its ELRS
            firmware), or is the B205 SDR the delivery path?
W7  [done]  The E8 config reader is merged (dry-run default, no flash).
W9  [next]  Bench: does the E8 (or the B205) deliver our forged LoRa frames
            to the TinyHawk? (Shared with the ELRS chain, W9.)

Layer 2 Chain 1 role (the E8 AS A TARGET — the new surface):
W0  [owner] Is the CTF target an ELRS drone + controller? (Chain 1 applies.)
W4  [done]  Surface known: the ESP32 TX auto-starts an open WiFi AP after
            link loss (~60s); GET /config exposes the binding phrase + UID.
W5  [done]  The "vuln" is the unauthenticated open-AP config read (no 0-day —
            it's the vendor's default recovery behavior).
W6  [done]  Design: jam/disrupt the link -> wait for the AP -> connect
            (ALFA card, lab) -> GET /config -> extract phrase -> derive UID.
W7  [next]  Build the AP scanner + HTTP config extractor (lab tool, no flash).
W8/W9 [next]  Lab test on the OWNED E8 (read-only): confirm the AP comes up on
            link loss, confirm GET /config returns the phrase. THIS is the
            highest-value lab test — it removes the UID-recovery step for the
            entire ELRS path.
```

> **The E8 is the *means* (delivery) and now also a *target* (Chain 1 WiFi
> config read). The no-flash constraint is absolute — the Chain 1 test is
> read-only (scan + GET), never a settings write or flash. If the E8's WiFi AP
> can't be reached, the B205 SDR is the delivery path and air-recovery
> (`fhss_seed.py`) remains the UID source.

---

## Part B — The master status board

One table: every firmware, have/need, wicket state, the single next action.

| # | Firmware / platform | HAVE / NEED | W-state (furthest done) | The single next action | Owner / tool |
|---|---|---|---|---|---|
| 1 | TinyHawk / Crazybee F4SX1280 (ELRS/BF 2026.6.2, related sample) | **HAVE** | W6 done, **W7 in progress** | Rewire forger/analyzer/chain → `elrs_core`, **v3** (Step 1) | Codex |
| 1b | TinyHawk III live firmware (ELRS/BF **4.4.2**, the actual target) | **HAVE** | W1 done (acquire, 4.4.2 bin+src), W2–W6 need re-verification | **Diff the 4.4.2 ELRS RX driver vs 2026.6.2** (MSP handler, buffer sizes, TELEMETRY gate) | Codex |
| 2 | STM32F411 generic BF build (2026.6.2) | **HAVE** | W1 (acquire only) | Individually analyze — low priority | Codex |
| 3 | The exact game-day TinyHawk image | **NEED** | W1 (close the gap) | If the CTF target is this drone: pull the exact image | Owner (laptop) |
| 4 | ELRS 4.1.0 source (reference) | **HAVE** | W7 (reference) | — (done) | — |
| 5 | ExpressLRS master source (reference) | **HAVE** | W7 (reference) | — (done) | — |
| 6 | **ArduPilot 4.7.2 (CubeOrange + Pixhawk1 ELF + SITL x86_64)** | **HAVE** | W1 done (acquire), W4–W5 done (vulns P1-1/P1-3) | **Fix P1-3 + validate against SITL x86_64** (W8 offline, no HW) | Codex |
| 7 | MAVLink v2 spec (pymavlink, reference) | **HAVE** | W7 (reference) | — (done) | — |
| 7b | The CTF GCS / companion image | **NEED** | W1 (acquire) | If the CTF exposes a GCS: pull its image | Owner (laptop) |
| 8 | **Tello DJI SDK (djitellopy + UDP/54399 protocol)** | **HAVE** | W1 done (acquire), W4 done (surface) | **Review the SDK + build the Wi-Fi toolchain** (deauth/PMKID/rogue-AP) | Codex |
| 8b | The CTF Wi-Fi drone firmware (if not a Tello) | **NEED** | W1 (acquire) | If the CTF target is a different Wi-Fi drone: pull it | Owner (laptop) |
| 9 | BLE target firmware | **NEED** | W4 partial (surface), **W1 (acquire) = blocker** | Confirm at W0: is the CTF BLE? Pull the firmware + confirm a BLE adapter | Owner (laptop) |
| 10 | GPS target (GNSS SDR spoofing) | **NEED** | W4 done (concept), **W1 (acquire) = blocker** | Confirm at W0: is the CTF GPS-dependent? Confirm a GNSS SDR | Owner (laptop) |
| 11 | E8 ELRS TX firmware (delivery device) | **NEED** (ref) | W7 (delivery tool) | Confirm at W0: can the E8 TX our frames, or is the B205 SDR the path? | Owner |
| 11b | **E8 WiFi AP / `GET /config` (Layer 2 Chain 1 — E8 as a TARGET)** | **NEED** (lab test) | W4–W6 done (surface/design), **W7/W8 next** | **Read-only lab test on the owned E8** (allowed by no-flash). If the AP + config read work, the UID-recovery step for ELRS is eliminated. **Highest-value lab test in the project.** | Owner (lab) |

**The "NEED TO DOWNLOAD" items (down from five to three after Claude's
corpus expansion):**
- **#7b — the CTF GCS / companion image** (if the CTF exposes a GCS over
  network/RF; the ArduPilot firmware itself is now HAVE).
- **#8b — the CTF Wi-Fi drone firmware** (if the CTF target is a *different*
  Wi-Fi drone than a Tello; the Tello SDK is now HAVE).
- **#9/#10 — the BLE + GPS target firmware** (if the CTF is BLE or
  GPS-dependent; these have no corpus yet).

**And the one that closes the test-article gap:**
- **#3 — the exact game-day TinyHawk image** (if the CTF target is this
  drone; the 4.4.2 corpus binary is a version-matched sample, not the
  confirmed installed image).

All of these come in via **laptop → gitlab01 push** (no egress on the
enclave). The download instructions for each are in the chain above.

**The corpus is now substantially more complete than it was a day ago.**
MAVLink (ArduPilot 4.7.2, including the SITL x86_64 oracle) and Wi-Fi (Tello
SDK) are no longer blocked at W1 — they have firmware in the repo and can
move to W7 (build) immediately. The only paths still blocked at W1 are BLE
and GPS (no corpus, and both need specific game-day hardware the enclave
doesn't have).

---

## Part C — What "done" means (the gates, restated for the inventory)

A firmware row is **done** when:
1. **W1–W6 are cleared** (acquire → model → recon → surface → vuln → design) —
   the *analysis* is complete. For the TinyHawk: **done.**
2. **W7 is cleared** (the PoC tools are built against the reference) — the
   exploit is *code.* For the TinyHawk: **in progress** (Step 1).
3. **W8 is cleared** (offline validation against a real capture) — the bytes
   are *proven right.* For the TinyHawk: **not started.**
4. **W9 is cleared** (live validation on the owned gear) — the exploit is
   *demonstrated.* For the TinyHawk: **not started.**
5. **W10 is cleared** (the playbook is grounded in W9) — it's a *game-day
   capability.* For the TinyHawk: **not started.**

**The TinyHawk is the only firmware at W7+**. Every other row is blocked at
**W1 (acquire)** — it needs the firmware downloaded, which needs the owner's
laptop (no egress here) *and* the W0 answer (which platform is the CTF
target). The methodology is broad and complete; the inventory is the concrete
"what's actually in the repo vs. what we still need to pull."

— andeds, orchestrator, 2026-10-15. The firmware inventory + the chain to run
against every firmware we know we need to develop on. The TinyHawk is the test
article (the only one past W6); the rest are blocked at W1 (download) pending
the owner's W0 (the CTF target) + the laptop → gitlab01 push.
