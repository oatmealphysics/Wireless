# E8 USB characterization — 2026-10-14

> **2026-10-16 supplement:** The normal vendor read now succeeds on the
> connected Linux endpoint. `/dev/ttyACM1` enumerated as the same CDC ACM
> `8888:2001` personality, and `tools/e8-read-config.py --execute` received the
> expected 17-byte response at 115200 8-N-1. The private response/UID remains
> under ignored `work/e8-session/`. This validates the read interface but still
> supplies no firmware-version, MCU, radio, or board-revision identity. No
> write, reset/mode transition, update, flash, fuzzing, or RF TX occurred. See
> the [current E8 dossier](../references/emax-e8_material/README.md). The
> unsuccessful 2026-10-14 chronology below remains useful evidence about state
> and port-contention prerequisites.

Starting repository revision: `2da17ce`, after syncing concurrent work. Scope:
USB descriptor discovery, passive reads, standard CRSF discovery, and one fixed
vendor Read Configuration request. No persistent settings writes, firmware
updates, malformed-input tests, controller application execution, or RF injection
were performed. Opening the serial endpoint may change USB/control-line state;
this was not a zero-interaction capture.

## Unit and endpoint evidence

The owner reports an unlabelled, screenless controller matching EMAX's E8 photos,
a red LED, and believed battery power while USB-connected. These observations
support the E8 product-family identification but do not establish its protocol
variant, board/MCU, or installed firmware.

| Field | Observed value | Limit |
|---|---|---|
| Serial endpoint | COM4 | Current session only; confirm again after reconnect |
| USB VID:PID | `8888:2001` | Generic/custom identifiers, not proof of a MCU model |
| Hardware revision field | `REV_0100` | USB device revision; not a confirmed firmware version |
| Bus description | `USB Serial` | No product/firmware identity exposed here |
| Compatible interface | Class 02, subclass 02, protocol 01 | CDC ACM-style serial interface |
| Driver | Microsoft `usbser`, `10.0.26100.9278` | PC driver version, not controller firmware |
| Other serial endpoint | COM3 Prolific USB-to-serial adapter | Not probed; not established as the controller |
| Installed E8 firmware | Not recovered | No successful application response |

The owner subsequently recalled **COM33** in the earlier Ubuntu/Wine configurator
session and **`/dev/ttyACM0`** as the Linux device. This is historical,
owner-reported evidence; the Wine mapping, earlier USB identifiers and successful
reply have not been recovered. COM33 was likely a Wine alias, not a persistent
controller property, and does not imply that Windows COM4 is wrong. The Linux
name is consistent with the observed CDC ACM interface (see the
[kernel ACM documentation](https://docs.kernel.org/usb/acm.html)), but does not
identify the board/MCU, application protocol or installed firmware.

Raw captures stay in ignored `work/e8-session/`. No private binding data was
obtained. The full device instance path/serial and other unrelated USB device
identifiers are deliberately omitted from this shared note.

## Requests actually attempted

| Operation | Setting | Outcome |
|---|---|---|
| Passive serial capture | COM4, 115200, 5 seconds | Zero received bytes |
| Standard CRSF discovery | COM4, 420000; passive + broadcast ping + targeted device ping | Zero received bytes; no device info |
| Second CRSF attempt | COM4, 115200 | Port-open access denied; no requests sent in this attempt |
| Subsequent passive open with DTR/RTS false | COM4, 115200 | Open succeeded, zero bytes; earlier denial was transient/unexplained |
| Vendor Read Configuration | COM4, 115200, 8-N-1, DTR/RTS false; one 64-byte read request | Zero received bytes within three seconds |
| Vendor Read Configuration after owner reports R4+ startup | Same fixed request and serial settings | Zero received bytes within three seconds |
| Proposed longer-settle read | Two-second settle and eight-second receive window planned | Port-open access denied before transmission; no request sent |

CRSF requests were `c8042800ea54` and `c80428eeea97`. The vendor query is the
configurator's fixed read request; no write/update request was sent. An empty
reply can mean wrong power/mode, variant, interface or other unmet prerequisites.
It does not establish a dead device or absence of any serial protocol.

## Manufacturer software acquisition and static findings

Source: [EMAX E8 custom configurator support article](https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264-expresslrs-e8-transmitter-custom-configurator).
The publicly linked Mega file was downloaded/decrypted into ignored work storage:

- Filename: `EMAX E8 ELRS Configurator.exe`
- Size: 23,782,400 bytes
- SHA-256: `7ec41368c1d7fe9cbf7e25ba053b05519f7e1aa8542c698e70764162be99362d`
- Type: x64 PE, Qt5/MinGW, Enigma Virtual Box wrapper
- Windows Authenticode: `NotSigned`
- Program execution: none
- Download limitation: helper checks size/decryption structure and records SHA-256,
  but does not validate Mega's file MAC; the public share and lack of signing do
  not independently authenticate the vendor build.

Static filesystem listing exposed Qt/runtime DLLs, translations and
`serial_bootloader.exe`; no separately named firmware `.bin` appeared in that
listing. This is not a complete search of embedded resources or all vendor
packages. Attempts to restore the PE import directory with unpacker variants
9_70 and 10_70 emitted zero-size import/relocation warnings; neither output is
treated as a faithfully reconstructed runnable program. Analysis used the
original packed file's intact code/name-backed `.idata` entries instead.

Qt method metadata establishes these original-file virtual addresses (image
base `0x400000`):

| Evidence | Address / result |
|---|---|
| Qt static metaobject | `0x40e020`; string metadata `0x413c00`, method metadata `0x413a60` |
| Qt method 10 | `on_readConfigButton_clicked` → dispatcher `0x407fb0` → handler `0x404620` |
| Qt method 11 | `on_writeConfigButton_clicked` → dispatcher `0x407f40` → different handler `0x405940` |
| Baud setup | `0x406369`: argument `0x1c200` (115200); call `QSerialPort::setBaudRate` at `0x40636e` |
| Line format | 8 data bits, one stop bit, no parity or flow control |
| Read request | `0x404708–0x404807`: 64 bytes, prefix `02 01 00`, zero payload and CRC bytes |
| Read response callback | `readConfigTimeOut` → `0x406c60`; asks QIODevice for 17 bytes |
| App title string | `Serial Update Tool 2.1` — host tool version only |

The reviewed UI exposes rate, telemetry ratio, power and six-byte binding data.
No dedicated firmware-version display/query was identified in this bounded
inspection. No version may be inferred from the host title or USB revision.

Additional offline review covered the serial-open handler at `0x4018b0`, its
open call at `0x401b0c` (ReadWrite argument 3), and success branch
`0x401de5–0x401f26`, returning through `0x401a3d`. No additional application
protocol handshake/write was identified on that reviewed success path; it
enables/updates UI controls. This is bounded static evidence, not a trace of Qt,
the USB driver, or the controller. It does not establish identical control-line
behavior under Windows and Wine.

Reproduce static inspection with `tools/inspect-e8-configurator.py` and
`tools/inspect-e8-serial-handlers.py` (dependencies in their docstrings). The fixed
reader `tools/e8-read-config.py --port COM4` defaults to dry-run and requires
`--execute` for that one read operation. Recheck the endpoint first.

## Preconditions to resolve physically

The [EMAX product page](https://emax-usa.com/collections/diy/products/e8-transmitter)
says visually similar E8 variants are distinguished by socket: USB-C for ELRS,
micro-USB for FrSky D8. Confirm this on the actual handset.

The manufacturer's [Nanoscout RTF manual](https://myosuploads3.banggood.com/products/20250204/20250204015303TinyhawkNanoscoutRTFV2.0.pdf)
describes charging red/complete green separately from startup. Its E8 ELRS serial
tool section (PDF page 16, printed page 14) requires the R4+ plus power startup
sequence for serial mode and refers parameter reading to that workflow. This
may explain a live CDC interface that returns no application data. Subsequently
the owner reported holding the right D-pad right-direction button while powering
on and observing a flashing green signal/mode LED. The owner also reports a
previous successful read using EMAX's custom configurator on Ubuntu under Wine;
those readings and the exact software build are not currently available. These
are owner-reported observations, not a captured successful read in this session.
They make the earlier battery/charging-only explanation insufficiently supported.
Entering a mode and flashing are different actions; no firmware file or update
operation is needed for a read. **The owner prohibits flashing under any
circumstances**, including when diagnosing this connection.

COM4 access was intermittently denied, and the owner confirmed another agent was
also probing the controller. Concurrent access is therefore a coordination issue,
but the specific process responsible for each denial was not proven. Codex is
leaving the live port alone and sharing offline evidence through
[the collaboration handoff](E8-COLLABORATION.md). No successful firmware-version
response has been obtained by Codex.

## Research implications

| Candidate work | Evidence available | What must be established next |
|---|---|---|
| Configurator short-response robustness | Host callback tests for nonzero read, then checks byte 0 and copies 16 payload bytes; a full 17-byte-read check was not identified on that path | Reproduce short/fragmented response handling in an offline host-side harness before claiming a defect; no controller/RF vulnerability follows automatically |
| USB configuration boundary | Vendor UI supports parameter reads/writes in its serial mode; a read request is now attributable | Obtain a valid unit response in documented mode; identity/version and validation behavior remain unknown |
| Firmware provenance and update validation | Vendor package has an update UI and serial transport | Obtain an exact E8-matched image/board identity, then inspect offline validation/signature logic; the presence of an updater alone is not a vulnerability |
| Wireless receiver/parser reachability | Only generic upstream ELRS reference sources currently match the broad protocol hypothesis | Confirm protocol variant, installed build, actual incoming RF message types and their parser entry points on this transmitter |
| Wired-to-wireless equivalence | Not established | Show a shared handler or verified bridge. USB/configuration requests do not become valid RF messages merely by transmitting the same bytes |

No controller exploit, memory corruption, denial of service, wireless configuration
access, or installed-version match has been demonstrated. The next useful test
is a successful normal vendor read in the correct device mode, not blind fuzzing
or an assumed wireless delivery port of the USB protocol.
