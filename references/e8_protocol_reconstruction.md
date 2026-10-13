# EMAX E8 (ELRS variant) — Serial Protocol Reconstruction

Status: **reconstructed from plaintext evidence** (the host tool's `.rdata`/`.idata`
+ §42a's captured 17-byte RX frame). The handler *code* is Enigma-packed
(`.enigma1` entropy 7.6–7.95 = fully encrypted), so the exact TX byte framing
is inferred from the command surface + import table + the one captured frame,
not read from the handler directly. Confidence levels noted per item.

Source artifacts (enclave):
- `references/EMAX_E8_unpacked/EMAX E8 ELRS Configurator.exe` (113 KB, unpacked
  GUI tool — the `.rdata`/`.idata` are plaintext; the `.text` handler code is
  Enigma-packed)
- `references/EMAX_E8_unpacked/serial_bootloader.exe` (113 KB, same build,
  different resources — the raw bootloader/flash tool)
- `references/EMAX_E8_ELRS_Configurator_unpacked.exe` (23.8 MB, the Enigma-
  packed original "actual download" — `.enigma1` 23 MB encrypted data section)
- §42a (Claude's notes): the captured 17-byte Read-response frame

## 1. Transport

| Field | Value | Confidence |
|---|---|---|
| Interface | USB CDC-ACM (`/dev/ttyACM0`) | HIGH (dmesg + §41) |
| USB descriptor (serial mode) | `8888:2001` "Your Company"/"USB Serial"/SN `00000001` | HIGH (§43b — the generic MCU bootloader-stub template) |
| USB descriptor (normal mode) | `0483:5710` "EMAXJoystick" (HID) | HIGH (§43b) |
| Baud rate | unknown (a `mov` immediate in the packed code; QSerialPort::setBaudRate is imported) | LOW — needs the unpack or a capture |
| Data bits / stop / parity / flow | unknown (QSerialPort::setDataBits/setStopBits/setParity/setFlowControl imported) | LOW — likely 8N1, no flow (the ELRS/CRSF default), but unconfirmed |
| I/O path | `QSerialPort::write` → `QIODevice::write` (TX); `QSerialPort::read` → `QIODevice::read` (RX) | HIGH (import table) |
| Timing | `QTimer` (start/stop) + `QObject::timerEvent` for the read/write timeouts | HIGH (import table + the 4 timeout signals) |

**Serial mode = the bootloader.** Normal power-on = HID joystick (app firmware
running). Hold R-pad RIGHT + power-on = CDC-ACM `8888:2001` + green LED (app
firmware NOT running; a tiny bootloader stub is). The EMAX tool's Update writes
the `.bin` through this same `/dev/ttyACM0` — there is no separate DFU mode
(§43b).

## 2. Frame layout (from §42a's captured Read-response)

The one frame we have on the wire (the params **Read-response**, 17 bytes):
```
offset: 0  1  2  3  4  5   6   7   8   9   10  11  12  13  14  15  16
byte:   02 c5 02 01 03 5d  05  00  11  18  5b  00  00  00  00  00  00
```
Claude's §42a decode (best read):
| Offset | Bytes | Meaning | Confidence |
|---|---|---|---|
| 0 | `02` | frame start / header byte | MEDIUM (consistent with ELRS framing) |
| 1 | `c5` | frame type / length (Read-response) | MEDIUM (one sample) |
| 2–4 | `02 01 03` | 3 config params: Rate 150 Hz (0x02), Power 100 mW (0x01), tlm OFF (0x03) | LOW (one-sample inference; needs a Write + re-Read of a changed value to confirm each slot) |
| 5–10 | `5d 05 00 11 18 5b` | 6-byte bind UID (matches §41c + air-recovered net_id `0x00111858`) | HIGH (cross-validated) |
| 11–16 | `00 00 00 00 00 00` | padding / reserved | MEDIUM |

**No firmware-version byte** is visible in the 17 bytes (consistent with the
GUI never displaying a FW version — §41f).

## 3. Command surface (from the meta-object + button handlers)

The 7 button slots + the 4 timeout signals + the 2 status/progress signals map
to these protocol operations:

| Operation | Slot | Signal(s) | Direction | Notes |
|---|---|---|---|---|
| Connect / Disconnect | `on_openSerialpushButton_clicked` | — | host→dev (open) | Opens the COM port (QSerialPort config: baud/data/stop/parity/flow) |
| Refresh port list | `on_refreshButton_clicked` | — | host-local | `QSerialPortInfo::availablePorts` (no device traffic) |
| **Read params** | `on_readConfigButton_clicked` | `readConfigTimeOut` | host→dev (cmd) + dev→host (17-byte resp) | The §42a frame is the response. TX command framing = unknown (packed) |
| **Write params** | `on_writeConfigButton_clicked` | `writeConfigTimeOut` | host→dev (cmd + params) | TX framing unknown; likely mirrors the Read layout (02 <type> <rate> <power> <tlm> ...) |
| **Bind** | (via `bind_label`/`bindingEdit`/`Binding Phrase`) | — | host→dev | Sets the 6-byte UID. TX framing unknown |
| **Update (flash)** | `on_sendFilepushButton_clicked` | `transmitStatus`, `transmitProgress`, `writeTimeOut` | host→dev (chunked `.bin`) | The chunked flash-write with the `okk` per-chunk ACK (see §4) |
| Choose `.bin` file | `on_openFilepushButton_clicked` | — | host-local | `QFileDialog::getOpenFileName` (no device traffic) |
| Language toggle | `on_languagePushButton_clicked` | — | host-local | EN/中文 (no device traffic) |

**The device-traffic operations are 5:** Read params, Write params, Bind,
Update (flash), + the Connect handshake. The other 3 slots are host-local.

## 4. The Update (flash) protocol — the `okk` chunk-ACK handshake

The strongest protocol evidence is the **`okk` token** (appears 16× in `.rdata`
+ 1× `okkr` — the repetition matches a per-chunk ACK counter). Combined with
the imports (`QFile` + `QFileInfo::size` + `QIODevice::write` + `QTimer`), the
Update flow is:

1. Host opens the `.bin` (`QFile::setFileName` + `QFileInfo::size` for the total).
2. Host sends the `.bin` in **chunks** (the chunk size is a `mov` immediate in
   the packed code — unknown; typical MCU flash chunk sizes are 256/512/1024 B).
3. After each chunk, the bootloader replies **`okk`** (the per-chunk ACK).
   The host counts the `okk` replies (`startCnt:` / `transmitProgress` /
   `progress` signals update the UI progress bar).
4. On all chunks ACKed → `Update Successed!` + `transmitStatus` = success.
   On a timeout (no `okk` within the `QTimer` window) → `Update Failed` +
   `writeTimeOut`.

The `okkr` variant (1×) is likely the **final** ACK (after the last chunk) or
a retransmit marker — the trailing `r` distinguishing it from the per-chunk
`okk`.

**This is the classic serial flash-programming handshake** (send chunk → wait
ACK → next chunk), the same pattern as DFU/ST-bootloader/ESP32-ROM serial
flashing. The exact chunk framing (header bytes, length field, CRC) is in the
packed code and needs the unpack or a capture to confirm.

## 5. What we can drive natively now (without the unpack)

From the reconstructed protocol, a native reader (Python, no Wine) can:
1. **Open the port** in serial mode (`/dev/ttyACM0`, the `8888:2001` CDC-ACM
   device) — but the baud/config is unknown (LOW confidence). The ELRS/CRSF
   default (115200 8N1) is the best guess.
2. **Send the Read-params command** + parse the 17-byte response (the §42a
   layout) — but the TX command framing is unknown (packed). We have the
   *response* layout, not the *request* framing.
3. **Send the Update (flash) chunks** + watch for `okk` ACKs — the chunk
   framing is unknown (packed), but the `okk`-ACK protocol is confirmed.

**The blocking unknowns** (all in the packed handler code):
- The baud rate + data/stop/parity/flow config
- The TX command framing for Read/Write/Bind (the request byte layout)
- The Update chunk framing (chunk size, header, length field, CRC)

**To resolve these**, one of:
- **The Enigma unpack** (run the exe under Wine + a debugger, let it
  self-decrypt, dump the unpacked `.text`, disassemble the handlers) — gets
  the exact framing. Heaviest option.
- **A clean dynamic capture** (the actual TX bytes as sent) — the
  `WINEDEBUG=+serial` attempt didn't produce serial content; a `strace -e
  trace=write` on the right Wine child process (the one holding `/dev/ttyACM0`,
  found via `lsof`) might work. Lighter, but the port-exclusivity +
  Wine-multi-process issues made prior attempts return 0 bytes.
- **The firmware binary** (EMAX-gated `firmware.bin` via support, or a
  bootloader flash-read if one exists) — reading the *firmware's* parser gives
  the expected framing from the other side. Gated + the firmware is also a
  stripped binary.

## 6. Hygiene (standing)

- Owned E8, bench, powered. Isolated network. Reverse-engineering + native
  tooling on the owned unit. **Reads only** (no flash / no Write-Update) until
  the parser is understood.
- UID `5D 05 00 11 18 5B` stays out of code/fixtures/logs (AGENTS.md §1, §41f)
  — ground truth for validation only. (It appears in §42a's captured frame as
  the bytes `5d 05 00 11 18 5b`; that's the wire form, not a hardcoded
  constant.)

— andeds, 2026-10-13
