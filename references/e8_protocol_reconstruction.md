# EMAX E8 (ELRS variant) — Serial Protocol Reconstruction

Status: **largely reconstructed from static decode of the plaintext host tool**
(the 113 KB inner exe's `.text`/`.rdata`/`.idata` are all plaintext — see the
§44 correction below) + §42a's captured 17-byte RX frame. Confidence levels
noted per item. The remaining unknowns (the exact 64-byte TX byte sequence,
the Update chunk framing, the 59-byte/1-byte/2-byte read semantics) need a
Ghidra decompile of the handlers OR one live `strace -f -e trace=write`
capture with a manual Connect+Read click.

## §44 CORRECTION (important)

An earlier version of this doc (and notes §39/§40) concluded the handler code
was **Enigma-packed** (`.enigma1` entropy 7.6–7.95 = "fully encrypted") and
that a Wine memory-dump was needed to read the handlers. **That was wrong.**

- The **23.8 MB download** is **Enigma Virtual Box packed** — a *file-storage*
  packer (bundles the exe + Qt DLLs for distribution), NOT Enigma Protector
  (code encryption). It was already unpacked by `evbunpack` in-session to the
  113 KB inner exe + the Qt DLLs in `EMAX_E8_unpacked/`.
- The **113 KB inner exe** has **plaintext `.text`** (entropy **5.799** —
  normal x86 code, not the ~7.9 of an encrypted block). All 128 functions
  (`.pdata`) are in the clear; the 7 button handlers are plaintext. No
  decryption, no memory-dump needed.
- The "Enigma stub validates MZ at `0x414270`" pattern (notes §39) is in the
  inner exe's `.text` entry-point init, but it's **MinGW/libstdc++ CRT
  boilerplate** (a loaded-module-header check), not an Enigma anti-tamper.
  The exe runs fine on WineHQ 11.0 with no patching — consistent with inert
  init, not a packer check.

**Consequence:** the handler code is readable directly from the inner exe.
Notes §40's Path A (winegdb) + Path B (`/proc/<PID>/mem` dump) are both
unnecessary. The exact TX framing comes from a Ghidra decompile of the
handlers (or one live strace capture), not from a memory dump.

Source artifacts (enclave):
- `references/EMAX_E8_unpacked/EMAX E8 ELRS Configurator.exe` (113 KB, the
  **plaintext** GUI tool — `.text` entropy 5.799, all handlers in the clear)
- `references/EMAX_E8_unpacked/serial_bootloader.exe` (113 KB, near-identical
  build — differs only in `.rsrc` + some ILT entries; same tool, the "Serial
  Update Tool 2.1" window title lives here)
- `references/EMAX_E8_ELRS_Configurator_unpacked.exe` (23.8 MB, the Enigma
  **Virtual Box** packed "actual download" — a file-storage packer, already
  unpacked to the inner exe; NOT code-encrypted)
- §42a (Claude's notes): the captured 17-byte Read-response frame

## 1. Transport

| Field | Value | Confidence |
|---|---|---|
| Interface | USB CDC-ACM (`/dev/ttyACM0`) | HIGH (dmesg + §41) |
| USB descriptor (serial mode) | `8888:2001` "Your Company"/"USB Serial"/SN `00000001` | HIGH (§43b — the generic MCU bootloader-stub template) |
| USB descriptor (normal mode) | `0483:5710` "EMAXJoystick" (HID) | HIGH (§43b) |
| **Baud rate** | **115200** | **HIGH** (§45 — `mov edx,0x1c200` before `setBaudRate` @ `0x40636e`) |
| **Data bits** | **8** | **HIGH** (§45 — `mov edx,8` before `setDataBits` @ `0x40637d`) |
| **Stop bits** | **1** (OneStop) | **HIGH** (§45 — `mov edx,1` before `setStopBits` @ `0x40638c`) |
| **Parity** | **None** (NoParity) | **HIGH** (§45 — `xor edx,edx` before `setParity` @ `0x406398`) |
| **Flow control** | **None** (NoFlowControl) | **HIGH** (§45 — `xor edx,edx` before `setFlowControl` @ `0x4063a4`) |
| I/O path | `QSerialPort::write` → `QIODevice::write` (TX); `QSerialPort::read` → `QIODevice::read` (RX) | HIGH (import table, §45a IAT addresses) |
| Timing | `QTimer` (start/stop) + `QObject::timerEvent` for the read/write timeouts | HIGH (import table + the 4 timeout signals) |

**Serial mode = the bootloader.** Normal power-on = HID joystick (app firmware
running). Hold R-pad RIGHT + power-on = CDC-ACM `8888:2001` + green LED (app
firmware NOT running; a tiny bootloader stub is). The EMAX tool's Update writes
the `.bin` through this same `/dev/ttyACM0` — there is no separate DFU mode
(§43b).

**Note:** the 115200 8N1 config matches what `tools/e8_serial.py` already
assumes (`BAUD = 115200`), so the earlier `OSError: Errno 5` + 0-byte reruns
were port-exclusivity (the §37 diagnosis), not a baud mismatch.

## 2. Frame layout

### 2a. The RX params-read response (17 bytes, from §42a — ground truth)
```
offset: 0  1  2  3  4  5   6   7   8   9   10  11  12  13  14  15  16
byte:   02 c5 02 01 03 5d  05  00  11  18  5b  00  00  00  00  00  00
```
| Offset | Bytes | Meaning | Confidence |
|---|---|---|---|
| 0 | `02` | frame start / header byte | MEDIUM |
| 1 | `c5` | frame type / length (Read-response) | MEDIUM (one sample) |
| 2–4 | `02 01 03` | 3 config params: Rate 150 Hz (0x02), Power 100 mW (0x01), tlm (0x03) | LOW (one-sample inference; needs a Write + re-Read of a changed value to confirm each slot) |
| 5–10 | `5d 05 00 11 18 5b` | 6-byte bind UID (matches §41c + air-recovered net_id `0x00111858`) | HIGH (cross-validated) |
| 11–16 | `00 00 00 00 00 00` | padding / reserved | MEDIUM |

Handled at `0x406c88` (QIO::read length imm = 0x11 = 17) — the readConfig
response handler (`0x406c60-0x4078a4`, 3140 B).

**No firmware-version byte** visible in the 17 bytes (consistent with the GUI
never displaying a FW version — §41f).

### 2b. The TX command frame (64 bytes, fixed — from §46/§47 static decode)
All TX commands (Read, Write, the Update handshake) are **fixed 64-byte
frames**. The known structure (HIGH confidence for the fixed parts):
```
offset: 0  1  2   3-10      11-18     19-61          62   63
bytes:  02 02 00  <dyn:8>   <dyn:8>   <pad/memcpy>   CRC  CRC
                                            hi     lo
```
| Offset | Value | Meaning | Confidence |
|---|---|---|---|
| 0–1 | `02 02` | fixed 2-byte header (`mov WORD [buf+0xb4], 0x0202`) | HIGH (§47b — the `02 02` immediate is in `.text`) |
| 2 | `00` | fixed (`mov BYTE [buf+0xb6], 0`) | HIGH (§47b) |
| 3–10 | dynamic (8 B) | `mov QWORD [buf+0xb7], rax` — likely the bind UID / QString::utf8 | MEDIUM (§47b — the write site is confirmed; the source data is a register, needs decompile) |
| 11–18 | dynamic (8 B) | `mov QWORD [buf+0xbf], r10` — likely more binding/config bytes | MEDIUM (§47b) |
| 19–61 | likely zero / memcpy'd | not written by any static immediate in the scanned handler; probably a `memset` 0 or a `QByteArray` memcpy (binding phrase / config params) | LOW (§47b — needs decompile or a capture) |
| 62 | CRC high | `mov BYTE [buf+0xf2], r10b` | HIGH (§47b) |
| 63 | CRC low | `mov BYTE [buf+0xf3], al` (AL = the CRC-16-CCITT loop output) | HIGH (§47a/§47b) |

### 2c. CRC-16-CCITT (the frame integrity check)
**HIGH confidence** (the polynomial is unambiguous in `.text`): the TX frame
ends with a **CRC-16-CCITT** (polynomial `0x1021`, bit-serial). The CRC loop
is at `0x404750` (in the `0x404620` handler) — a classic bit-serial CRC-16-
CCITT:
```
shl edx,8 ; xor eax,edx ; lea edx,[rax+rax] ; xor dx,0x1021
; test ax,ax ; cmovns (conditional on the high bit) ; 8 iters/byte
```
The output (AX) is stored at byte 63 (low) + byte 62 (high, via a mirror
offset). The CRC is computed over the frame bytes `[r9..r10]` (the buffer
range). 6 `xor dx,0x1021` instructions were found in `.text` (at `0x2099`,
`0x2387`, `0x2549`, `0x2727`, `0x3b4a`, `0x53bb`) — the CRC is used in
multiple handlers (the frame is CRC'd on the TX path; the RX path presumably
verifies it).

## 3. Command surface (from the Qt moc stringdata table — §48a)

The class's Qt moc stringdata (18 entries at `.rdata` VA `0x413c00`):
```
 [0] "Widget"                              class name
 [1] "on_openSerialpushButton_clicked"     slot  (Connect/Disconnect)
 [3] "on_openFilepushButton_clicked"       slot  (choose .bin)
 [4] "on_sendFilepushButton_clicked"       slot  (Update/flash)
 [5] "on_refreshButton_clicked"            slot  (refresh port list)
 [6] "readTimeOut"                         signal
 [7] "writeTimeOut"                        signal
 [8] "readConfigTimeOut"                   signal
 [9] "writeConfigTimeOut"                  signal
[10] "transmitStatus"  (param "Status"/"status")   signal
[13] "transmitProgress" (param "progress")         signal
[15] "on_readConfigButton_clicked"         slot  (Read params)
[16] "on_writeConfigButton_clicked"        slot  (Write params)
[17] "on_languagePushButton_clicked"       slot  (EN/中文 toggle)
```
**5 device-traffic operations** (Read params, Write params, Bind, Update/
flash, Connect) + 3 host-local (openFile, refresh, language). The slot-index
→ function-pointer map is in the `qt_metacall` dispatcher (one Ghidra pass
away; the handler function addresses are in §4).

## 4. Handler function map (from §46 — verified by Qt-API call pattern)

| VA range | Size | Operation | Key API calls |
|---|---|---|---|
| `0x4018b0-0x401fbe` | 1806 B | Connect / Disconnect | `setPortName` @ `0x401acd`, `QTimer::stop` @ `0x4019bf` |
| `0x402a60-0x4034b6` | 2646 B | Update (flash) chunk loop | 4× `QIODevice::write` @ `0x402d24`, `0x4030b0`, `0x4031c6`, `0x403439` (variable length) |
| `0x404620-0x4049ad` | 909 B | **candidate: Update handshake** (or writeConfig — §47d uncertainty) | `QIODevice::write(64 B)` @ `0x404807`, the CRC loop @ `0x404750` |
| `0x405940-0x40615f` | 2079 B | **candidate: writeConfig** (or Update — §47d) | `QIODevice::write(64 B)` @ `0x40607c`, `QTimer` start/stop |
| `0x406160-0x4067a5` | 1605 B | openSerial init | `QSerialPort::ctor` + port config @ `0x40636e-0x4063a4` |
| `0x406c60-0x4078a4` | 3140 B | readConfig **response handler** | `QIODevice::read(17 B)` @ `0x406c88` ← §42a's frame |
| `0x4078b0-0x407e7d` | 1485 B | openFile (choose .bin) | `QFileDialog::getOpenFileName` @ `0x407a34` |
| `0x4067b0-0x406c52` | 1186 B | refresh | `QSerialPortInfo::availablePorts` @ `0x4068a8` |

**§47d labeling caveat:** the `0x404620` (909 B) handler's `.rdata` string
refs include "Cancel"/"startCnt:"/"startTransmission" (Update-flow strings) +
the "Read" button label — suggesting it's actually the **Update handshake**
(not writeConfig as §46 first labeled). The `0x405940` (2079 B) handler is
then the likely writeConfig. The actual `on_readConfigButton_clicked` is
probably a shorter handler not yet pinpointed. **Resolving which handler is
which needs the `qt_metacall` slot-index → function-pointer map** (one Ghidra
pass) OR one live strace capture.

## 5. Frame-size table (from §46)

| Direction | Site | Length | Interpretation |
|---|---|---|---|
| TX writeConfig | `0x404807` | **64 bytes** | fixed command frame |
| TX readConfig | `0x40607c` | **64 bytes** | fixed command frame (same format) |
| TX Update | `0x402d24`/`0x4030b0`/`0x4031c6`/`0x403439` | runtime-variable | the chunked-flash pattern (length from a register, not an immediate) |
| **RX params-read response** | `0x406c88` | **17 bytes** | ← §42a exactly |
| RX small read (ACK poll?) | `0x402508` | 1 byte | likely the "wait for ACK byte" in Update |
| RX header peek | `0x403f0e` | 2 bytes | a short status/length prefix |
| RX medium read | `0x402339` | 59 bytes (0x3b) | unknown — possibly a bind-ID list / device-info block for Update |

**The protocol is asymmetric fixed-size:** TX commands are 64 bytes, the
params-read RX response is 17 bytes. Common for USB CDC-ACM (simple parsers).
The Update flow is plausibly: host→dev "update start" (64 B) → dev→host
59 B device-info/chunk-params → host→dev chunk-sized blocks (variable), each
ACKed by a 1 B read (§46d — hypothesis, needs confirmation).

## 6. The `okk` correction (§44b)

An earlier version of this doc (and notes §39) interpreted the 16× `okk`
string in `.rdata` as the **per-chunk ACK token** for the Update protocol.
**That was a false positive.** Scanning `.rdata` with context, all 16 `okk`
hits are inside a contiguous high-byte block (`.rdata+0xb70-0x27f0`) that
looks like **RGBA pixel data / an embedded icon** (the `6f 6b 6b` = "okk"
sequence is a coincidence in the image bytes). The one `okkr` at
`.rdata+0x126f2` is a 4-byte run inside a pointer table (followed by `0x4XXXXX`
function pointers). **The real Update ACK is not an ASCII token** — it's
more likely a **binary status byte** (the 1-byte read at `0x402508`). The
chunk-ACK handshake still exists (the 1-byte ACK poll + the QTimer timeout),
but the ACK is a binary byte, not "okk".

## 7. What we can drive natively now (without a Ghidra pass)

From the reconstructed protocol, a native reader (Python, no Wine) **can**:
1. **Open the port** in serial mode (`/dev/ttyACM0`, the `8888:2001` CDC-ACM
   device) at **115200 8N1, no flow** (HIGH confidence, §45).
2. **Send a 64-byte TX command** with the `02 02 00` header + the CRC-16-CCITT
   trailer (bytes 62-63, poly `0x1021`) — but the **exact dynamic bytes**
   (offsets 3-18, the UID/binding fields; 19-61, the padding/memcpy) are not
   yet known (MEDIUM/LOW confidence).
3. **Parse the 17-byte params-read response** (the §42a layout — HIGH
   confidence for the UID field, MEDIUM for the param slots).

**The blocking unknowns** (need a Ghidra decompile OR one live strace capture):
- The exact 64-byte TX sequence for Read and Write (the dynamic fields at
  offsets 3-18 + 19-61)
- Which 64-B handler is readConfig vs writeConfig (the `qt_metacall` map)
- The Update chunk framing (chunk size, per-chunk header, the binary ACK byte)
- The 59-byte / 1-byte / 2-byte read semantics

## 8. The path to the byte-exact protocol (the next step)

Two options (Claude's §44c/§48c), in order of informativeness:
1. **Ghidra decompile of the handlers** (on the enclave, if Ghidra is
   available) — the inner exe is plaintext, no decryption needed. Decompile
   `on_readConfigButton_clicked` + `on_writeConfigButton_clicked` + the
   `qt_metacall` dispatcher (to resolve the slot-index → function map). The
   decompiled C shows the 64-byte buffer construction plainly. ~15-min pass.
2. **One live `strace -f -e trace=write -s 128`** on the Wine process + a
   manual Connect+Read click in the GUI — captures the exact 64-byte TX bytes
   written to `/dev/ttyACM0`. The port-exclusivity blocker (the §37 TAP
   problem) doesn't apply — strace reads the Wine process's `write()` syscalls
   directly, no second reader of the port. Claude's capture infra is ready:
   `/tmp/wine_strace_emax.sh` (launches wine under strace) +
   `/tmp/parse_wine_strace.py` (parses the log, decodes the `\xHH` escapes,
   prints length + hex + ascii). The one gap: the GUI click needs a human
   (no `xdotool`/`wmctrl` on a Wayland session).

**Recommendation:** do **both** — the strace capture is faster (one click, 30
s) and gives the exact Read TX bytes; the Ghidra decompile is more complete
(all handlers + the state machine + the Update chunk framing). The strace
gives ground truth to validate the Ghidra decode against.

## 9. Hygiene (standing)

- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. **Reads only** (no flash / no Write-Update) until
  the parser is understood. The static decode + the strace capture are both
  host-side reads (no device interaction beyond the tool's normal Read).
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1, §41f)
  — ground truth for validation only. (It appears in §42a's captured frame as
  the bytes `5d 05 00 11 18 5b`; that's the wire form, not a hardcoded
  constant.)

— andeds, updated 2026-10-14 (incorporating Claude's §44–§48)
