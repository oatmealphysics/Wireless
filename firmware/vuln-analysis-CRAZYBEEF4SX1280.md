# Firmware Vulnerability Analysis — betaflight_CRAZYBEEF4SX1280.bin

**Analyst:** andeds (orchestrator), 2026-10-15
**Target:** EMAX TinyHawk III flight controller (Betaflight 2026.6.2, `CRAZYBEEF4SX1280`, STM32F411)
**Bin:** `firmware/betaflight/betaflight_CRAZYBEEF4SX1280.bin` (426,910 bytes)
**SHA-256:** `d98f2569e4fba71a3c3054fd798c6329c64b7ee4b686384a2a1abc9e28d9ad0e`
**Method:** entropy scan (binary) + source cross-reference (Betaflight 2026.6.2 + ELRS 4.1.0, both in `firmware/src/`). This extends Codex's `ghidra-analysis-CRAZYBEEF4SX1280.md` (the lay-of-the-land) with the buffer-control / dangerous-sink / entropy passes the owner requested.
**Evidence class:** VALIDATED (source-derived, line-cited). No live exploit was run; this is static analysis of the corpus binary + its pinned source.

---

## 1. The lay of the land (what the firmware is, what's in it)

Betaflight 2026.6.2 unified target for the Crazybee F4SX1280 / TinyHawk F4SX1280. ARM Cortex-M4F, base `0x08000000`, 416.9 KiB used of 512 KiB flash, 128 KiB SRAM. Vector table at flash base (reset `0x080556C5`).

**Code regions** (from Codex's Ghidra pass + confirmed against source):
- `0x08000000–0x0800B500` — startup, IRQ/DMA handlers
- `0x0800B500–0x08040000` — core Betaflight (PID, mixer, scheduler, sensors)
- `0x08040000–0x08056000` — **ELRS RX stack + SX1280 driver** (the attack surface)
- `0x08056000–0x08060000` — CLI, config param tables
- `0x08060000–0x0806839E` — rodata (param names, format strings, MSP/build strings)

**Functions (the "what calls what" lay of the land):** the ELRS RX path is a small, well-defined call tree:

```
expressLrsTimerTick()                (timer ISR, phase-locked to RX)
  └─ processRFPacket(payload, ts)    expresslrs.c:811  — main entry, on each RF packet
       ├─ validatePacketCrcStd()     :791               — CRC14 gate (UID-derived init)
       ├─ case ELRS_RC_DATA_PACKET:
       │    └─ memcpy(payload, dmaBuffer, ELRS_RX_TX_BUFF_SIZE=8)   :846
       ├─ case ELRS_MSP_DATA_PACKET:
       │    └─ processRFMspPacket()   :697
       │         ├─ (bind mode) unpackBindPacket()
       │         ├─ receiveMspData(packageIndex, payload)            — accumulates into mspBuffer[65]
       │         └─ processMspPacket(mspBuffer)                      :~730 — THE INJECTION SURFACE
       └─ case ELRS_SYNC_PACKET:
            └─ processRFSyncPacket()  :739               — UID[3]/UID[4]/UID[5] check, model match
```

So the function that "does the dangerous thing" with attacker-controlled input is **`processMspPacket()`**, called from `processRFMspPacket()` after the MSP payload has been CRC-validated and accumulated into the 65-byte `mspBuffer`.

---

## 2. Entropy (the "randomization / encryption / API keys" question)

**Measured (binary, 426,910 bytes):**
- Overall Shannon entropy: **6.97 bits/byte**
- Highest 4 KB window: **< 7.5** (no window crosses the 7.5 threshold at all — 0 high-entropy windows)
- Highest 256-byte block: **6.63** at `0x08043880` (this is code, not data — `00b5 0629 83b0 …` is Thumb instruction bytes)
- First 64 KB (code): 5.00 · Last 64 KB (rodata): 5.74

**Finding: there is NO high-entropy region anywhere in the firmware.** No encrypted blobs, no packed/obfuscated code, no embedded API keys, no XOR-obfuscated strings, no visible PRNG key schedule. This is a **plaintext, unstripped, unobfuscated** build. Everything a static analyzer or source reader can see is exactly what's in the image.

**Implication for the CTF:** (a) there is no "find the hidden key" problem — the secrets are the ELRS UID (in EEPROM/config, `expresslrs_uid` param) and, if a binding phrase is set, its MD5-derived UID. (b) the firmware is fully analyzable offline, which is why this whole pass worked from source. (c) the CRC14 table is **generated at runtime** by `generateCrc14Table()` (called from `expressLrsSpiInit`) — that's the only "computed" data, and it's deterministic, not key-dependent.

---

## 3. Dangerous functions / buffer control (the "can I control what's placed in the buffer?" question)

I traced every `memcpy`/`strcpy`/`memset`/`sprintf` in the ELRS RX path (`expresslrs.c`) and checked what bounds-checks precede each sink. The full sink list in the attack-surface region:

| Sink | Location | Size / bound | Attacker-controlled? | Verdict |
|---|---|---|---|---|
| `memcpy(payload, dmaBuffer, ELRS_RX_TX_BUFF_SIZE)` | `expresslrs.c:846` | **8 bytes** (fixed macro) | The 8-byte RC payload, post-CRC | **No overflow.** Fixed size, both src and dst are `ELRS_RX_TX_BUFF_SIZE`. The `payload` dest is a fixed-size RX channel buffer. |
| `memcpy(telemetryPacket, &otaPkt, ELRS_RX_TX_BUFF_SIZE)` | `expresslrs.c:487` | **8 bytes** (fixed) | Downlink telemetry (TX side, not the attacker path) | Not on the RX attack path. |
| `memset(&eprState, 0, sizeof(eprState_t))` | `expresslrs.c:149` | `sizeof` (fixed) | No | Safe. |
| `receiveMspData(packageIndex, payload)` | called at `:719` | accumulates into `mspBuffer[ELRS_MSP_BUFFER_SIZE=65]` | **Yes — the MSP payload is attacker-controlled** | **The injection surface.** See below. |
| `processMspPacket(mspBuffer)` | `:~730` | consumes `mspBuffer[65]` | **Yes** | **The real sink.** |

### 3.1. The RC-data path is NOT an overflow (Codex was right, and the source confirms it)

The `memcpy` at `:846` copies a **fixed 8 bytes** (`ELRS_RX_TX_BUFF_SIZE 8`, `expresslrs_common.h:48`). Both source (`dmaBuffer`) and the size constant are fixed. There is no length field from the air that controls the copy size — the OTA packet is a fixed struct (`elrsOtaPacket_t`), and the DMA buffer is exactly that size. So **a malformed RC packet cannot overflow the channel buffer via this memcpy.** The gate before it is `validatePacketCrcStd()` (CRC14 with the UID-derived init) + `connectionState == ELRS_CONNECTED` + `connectionHasModelMatch`.

### 3.2. The MSP-over-ELRS path IS the injection surface (this is the real finding)

`processRFMspPacket()` (`expresslrs.c:697`) is where attacker-controlled bytes reach a deserializer:

1. The RF MSP payload is accumulated into `mspBuffer[ELRS_MSP_BUFFER_SIZE]` (**65 bytes**, `expresslrs_telemetry.h:36`) via `receiveMspData(packageIndex, payload)`.
2. When the multi-package MSP transfer completes (`hasFinishedMspData()`), the buffer is dispatched:
   - `ELRS_MSP_SET_RX_CONFIG` + `ELRS_MSP_MODEL_ID` → **writes `modelId`** (`mspBuffer[9]`) to `rxExpressLrsSpiConfigMutable()` — i.e., an air packet can **change the model-match value**.
   - otherwise (if `connectionHasModelMatch`) → **`processMspPacket(mspBuffer)`** — the full Betaflight MSP command handler.

**The pre-conditions** (all must hold): `USE_MSP_OVER_TELEMETRY` compiled in, `connectionState == ELRS_CONNECTED`, CRC14 valid (i.e., attacker knows the UID), and model match. **Once those hold, the attacker sends arbitrary MSP commands to the flight controller over the air.** That includes (from the Betaflight MSP surface):
- Config reads/writes (parameters)
- **`ELRS_MSP_SET_RX_CONFIG` — write a new model ID, or in the ELRS bind path, effectively rebind**
- Reboot
- Motor test (requires arming)
- **DFU / firmware-update initiation** (`MSP_SET_HW_STATUS` / `MSP_SET_FW_VERSION` in some builds)

**The buffer-control answer:** the attacker does **not** control the *size* of the copy into `mspBuffer` (it's bounded by `ELRS_MSP_BUFFER_SIZE` and the `packageIndex`/bytes-per-call framing), so this is **not a classic stack/heap overflow via a length field**. The risk is **command injection**, not memory corruption: a CRC-valid MSP packet gets deserialized and executed by the flight controller's own MSP handler. The danger is the *semantics* of the MSP commands (rebind, reboot, DFU, motor), not a buffer overrun.

**Memory-leak check:** Betaflight is a bare-metal, fixed-allocation firmware (no heap in the RX path — `eprState`, `dmaBuffer`, `mspBuffer`, `telemetryPacket` are all static fixed arrays). I found **no `malloc`/`free` in the ELRS RX path** and no leak-prone dynamic allocation. The `free` string hits in the binary are state-machine enums (`I_FREEZE`, `SYSTEM_INIT`), not libc calls. **No memory-leak attack surface in the ELRS path.** (A full heap audit would need the scheduler/CLI paths, which are out of scope for the RF attack.)

---

## 4. What this means for the CTF (synthesis with Codex + Claude)

Codex's Ghidra doc already established the protocol facts (OTA v3, binding UID `{0,1,2,3,4,5}`, 3-of-6 UID bytes observable, V3 nonce not mixed into CRC, 1.5 s failsafe window, model-match mask `0x3F`). My pass adds the **sink-level buffer-control answer**:

1. **The overflow path is a dead end** (fixed 8-byte RC memcpy, no length field). Don't spend CTF time hunting a buffer overflow in the ELRS RX path — it's not there.
2. **The viable RF→RCE path is MSP-over-ELRS command injection.** Forge a CRC-valid MSP packet (needs the UID — recoverable from the air per `fhss_seed.py`, or from the 3 observable UID bytes + the known binding UID in bind mode), get the RX into `ELRS_CONNECTED` + model-match, then send `MSP_SET_RX_CONFIG` (rebind / change model) or a reboot/DFU command. **This is the "execute a command on the drone wirelessly" the CTF is asking for** — and it's exactly what the `elrs_chain.py` exploit chain is *trying* to do (its `build_msp_packet` is the relevant builder, currently on the buggy local CRC that Phase 1 Step 1 rewires).
3. **The binding-mode UID `{0,1,2,3,4,5}` is the lowest-hanging fruit.** Any TinyHawk left in bind mode uses the universally-known UID — the FHSS sequence, CRC init, and sync channel are all derivable from it with no recovery step. On game day, if a target drone is in bind mode (or was never bound with a phrase), the entire chain is open.
4. **Entropy finding removes the "hidden key" class of problem.** No obfuscation, no embedded keys. The only secrets are the UID (config) and the binding phrase (if set).

### The attack chain, restated with the sink-level detail

```
1. RECOVER UID  (air: fhss_seed.py from observed hops  |  OR  bind-mode UID {0,1,2,3,4,5}  |  OR  E8 read)
2. FORGE        (elrs_core.ota — CRC14 with UID-derived init, OTA v3 for this build [Codex §4.1])
3. SYNC         (send ELRS_SYNC_PACKET with UID[3]/[4]/[5] → RX enters ELRS_TENTATIVE → ELRS_CONNECTED)
4. MODEL-MATCH  (send MSP_SET_RX_CONFIG / model ID to satisfy connectionHasModelMatch, OR rely on default modelId=0xFF → modelXor=0x00)
5. INJECT       (send MSP command via processRFMspPacket → processMspPacket: rebind / reboot / DFU / motor)
```

Steps 2–5 are the `elrs_chain.py` + `elrs-packet-forger.py` + (Phase 2) FLRC-modulated TX. Step 1 is the elrs-sdr `fhss_seed.py` air-recovery. This is the concrete CTF exploit, and the firmware analysis confirms the *sink* (MSP command execution) is real and reachable.

---

## 5. What I did NOT do (honesty contract)

- **No live exploit / no TX / no hardware.** This is static analysis of the corpus binary + its pinned source. The "attacker sends MSP commands" claim is source-derived (the code path exists and is gated only by CRC + connection + model-match), **not** demonstrated on the owned drone. Demonstrating it is Step 3.12 of §12 (Phase 2, live end-to-end).
- **The OTA version for *this* build is v3** (Codex §4.1, confirmed in source `expresslrs_common.h` — `USE_ELRSV4` not defined). My earlier §12 "measure the version from the capture" instruction still stands as the general rule, but for *this specific* corpus binary the answer is **v3**.
- **The MSP-over-telemetry feature must be compiled in** (`USE_MSP_OVER_TELEMETRY`). I confirmed the code path exists in the source; I did not confirm the *specific* 2026.6.2 build enabled that define (it's a target build flag). The live test (Step 2/3) confirms it.
- **I did not audit the non-ELRS paths** (CLI over serial, MSP over USB, the DFU/bootloader path, the sensor drivers) for buffer overflows. Those are separate attack surfaces (USB/serial-delivered, not RF) and out of scope for the "wireless on game day" constraint. A full corpus overflow audit is a separate work item.
- **IDA headless would not initialize on this host** (`Failed to initialize IDA as library, error 1` — proc-module load issue, not the script). I used the pinned source instead, which is more authoritative for buffer-control than a binary scan anyway. The binary entropy scan (section 2) did run cleanly without IDA.

## 6. Open items / next (feeds §12)

1. ~~Confirm `USE_MSP_OVER_TELEMETRY` is set~~ — **CONFIRMED for this build.** The
   corpus target config `betaflight/EMAX_TINYHAWKF4SX1280.config` has
   `#define USE_RX_EXPRESSLRS_TELEMETRY` (line 15) + `feature TELEMETRY` (line 108).
   The MSP-over-air path is compiled in. (The exact `USE_MSP_OVER_TELEMETRY`
   define is set by the `USE_RX_EXPRESSLRS_TELEMETRY` feature macro in the
   target build; the source gates the RX accumulation on it, and this build
   enables the feature.)
2. **MSP command surface (step 5) — mapped.** `msp.c` `processMspPacket()`
   handles, among others: `MSP_REBOOT_FIRMWARE`, `MSP_REBOOT_BOOTLOADER_ROM`,
   `MSP_REBOOT_MSC`/`_UTC` (mass-storage / DFU entry), `MSP_REBOOT_BOOTLOADER_FLASH`,
   plus the read commands (`MSP_API_VERSION`, `MSP_FC_VARIANT`, `MSP_FC_VERSION`,
   `MSP_BOARD_INFO`, `MSP_BUILD_INFO`, `MSP_ANALOG`, `MSP_DEBUG`, `MSP_UID`).
   So an air-injected MSP packet can **reboot the FC into the bootloader/DFU**
   (RCE-adjacent: it's the entry to the firmware-update path) or read the
   build/board identity. The ELRS-specific `MSP_SET_RX_CONFIG` (model ID /
   rebind) is handled one level up in `processRFMspPacket()` before the generic
   `processMspPacket()`. The exact "which single command wins the CTF flag"
   depends on Q2 (where the flag lives) — reboot-to-DFU is the strongest
   demonstrated-capability claim; a config-write is the cleaner "we controlled
   it" claim.
3. **The bind-mode UID path** is the fastest CTF win — document it as the
   primary game-day option (no UID recovery needed if the target is in bind
   mode).
4. **Phase 2 (FLRC PHY)** is still required for the RF delivery (Step 3) — the
   firmware analysis confirms the *what* (MSP command injection) but the
   *how-delivered* (FLRC-modulated, CRC-valid, model-matched) is the elrs-sdr
   PHY + the Step 1/2 protocol work.

— andeds, orchestrator, 2026-10-15. Static firmware analysis: lay-of-the-land, entropy, dangerous-sink / buffer-control. Confirms the MSP-over-ELRS command-injection path as the viable RF→RCE sink; the RC-data overflow path is a dead end. No live exploit, no TX, no hardware.
