# E8 ELRS controller: console and filesystem investigation

## Question and current answer

Can the operator access the owned controller's operating system and run commands
such as `ls` or `mkdir`?

**No shell or filesystem interface has been established.** The confirmed USB
serial interface handles binary configuration/update messages. The existing
native reader is a protocol client, not a terminal. An internal debug console
may exist, but neither its presence nor its pinout is known. This investigation
does not establish that the controller has no OS or filesystem.

## Evidence checked on 2026-10-14

| Finding | Evidence and limit |
|---|---|
| EMAX documents a custom serial configurator with Read/Write settings operations | VALIDATED against the vendor support article linked below. The article does not describe a shell. |
| The project has a confirmed binary Read protocol: 64-byte request and 17-byte response at 115200 8N1 | VALIDATED in prior project hardware tests, recorded in `references/e8_protocol_reconstruction.md`. Codex read the client and notes; Codex has not repeated the hardware test in this investigation. |
| Prior project tests observed HID joystick `0483:5710` in normal mode and CDC-ACM `8888:2001` in serial/update mode | Historical hardware evidence in Claude's sections 41 and 43. These descriptors do not identify the MCU, OS, filesystem, or internal console. |
| The local unpacked vendor host executable exposes configuration and update handlers | VALIDATED by static printable-string inspection of the 115,712-byte host tool. Matches include Read, Write, Update, Connect, and serial baud configuration. No matching console/filesystem feature was found by that limited inspection. This is not exhaustive analysis of the controller firmware. |
| `SHELL32.dll` appears in the host executable | A Windows host dependency, not evidence of a shell on the controller. |
| The controller was absent from the current USB inventory | VALIDATED by `lsusb`; neither previously observed controller ID appeared. `/dev/serial/by-id` also did not exist. No serial port was opened. |
| No controller firmware image was located in the searched project and Downloads folders | The search found packed/unpacked Windows configurators and existing protocol tools, not the device firmware. This does not establish that no firmware image exists elsewhere. |

### Architecture conclusions that remain unproven

- MCU model, board revision, and application/bootloader memory layout.
- Bare-metal firmware versus an RTOS or a general-purpose OS.
- Presence of a filesystem, external storage, or a command interpreter.
- UART console pads, electrical levels, baud rate, and console enablement.
- SWD/JTAG availability or readout-protection state.
- Whether serial mode executes a separate bootloader image or an update branch
  within another firmware image. The interface's update role is established;
  USB personality changes alone do not prove its internal implementation.

Earlier notes speculate about Air32/AT32/STM32-family hardware. Treat that as
unconfirmed for this exact ELRS unit until a matching board marking, schematic,
or device-firmware analysis establishes it. FrSky D8 and ELRS E8 variants must
not be conflated: EMAX identifies the D8 variant by micro-USB and ELRS by USB-C.
Likewise, the Windows configurator is not the controller's firmware.

## What interaction is currently supported

The known serial operation reads configuration fields. It does not enumerate
directories, create files, execute programs, or provide a firmware dump.
`mkdir` would also be a state-changing operation if a filesystem were later
established; it is not part of this read-only investigation.

A host-side terminal or CLI can be built around the confirmed protocol, but
its commands would be local aliases for known device operations. Calling such
a tool a controller OS shell would misrepresent what it does.

## Next observations needed

1. Connect the owned controller in serial mode, with the vendor configurator
   closed. Match its descriptors and interface classes to the historical USB
   evidence. Run only the confirmed Read query if appropriate, redact the bind
   UID, and record whether unsolicited text is present. A binary response or
   silence does not exclude a separate internal console.
2. Obtain the exact device firmware or matching board documentation through
   an established owner/vendor path. Analyze it offline for MCU architecture,
   RTOS markers, command tables, filesystem code, and UART initialization.
   Do not infer the device OS from Windows host imports.
3. If board access is desired, first identify the board revision and MCU
   markings. Determine any UART/debug pinout and voltage from matching evidence
   before connecting equipment. Observe a candidate UART receive-only first;
   a boot log does not by itself establish an interactive shell.

No undocumented serial probes, shell strings, erase/unlock commands, flash
writes, persistent configuration changes, or RF transmission were performed.
Further hardware observation depends on the controller being connected.

## Follow-up: live serial check on 2026-10-14

The operator reconnected the controller and requested another check. **VALIDATED
on the owned device by Codex:** USB `8888:2001` appeared as `/dev/ttyACM0`, with
manufacturer `Your Company` and product `USB Serial`. No other process was
reported by the port-ownership check. An exclusive 115200 8N1 connection sent
only the confirmed 64-byte Read request; its response was exactly 17 bytes with
the expected `02 c5` header. Rate index 2 maps to 150 Hz. Configuration bytes
were 1 and 3; their Power/telemetry label mappings remain unconfirmed. Reserved
bytes were zero. The bind UID was redacted from tool output and is not recorded.

No unsolicited bytes arrived during the short initial observation. This
confirms a working binary protocol connection, **not a shell**; silence during
a brief observation does not disprove another console. No ASCII commands or
undocumented probes were sent. The serial connection was closed afterward.

## Primary sources

- [EMAX USA: ExpressLRS E8 custom configurator](https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264-expresslrs-e8-transmitter-custom-configurator)
- [EMAX USA: E8 variants and USB connectors](https://shop.emax-usa.com/collections/new-products/products/e8-transmitter)
- Project evidence: `references/e8_protocol_reconstruction.md`,
  `tools/e8_read.py`, and Claude's handoff sections 41, 43, 49, and 50
  (currently in `docs/CLAUDE_NOTES_FOR_ANDEDS.md`).
