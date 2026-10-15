# E8 hardware identity and serial evidence

Reviewed 2026-10-14 on the Windows home workspace. No device settings were read
or written, no serial port was opened, and no firmware was selected or flashed.

| Observation | Evidence | Conclusion |
|---|---|---|
| Related EMAX OLED/Nano entries use ESP32 + SX1280 | Bundled target registry; home lab handoff section 6 | Useful reference; does not identify the owner's E8 |
| E8 ELRS support article describes a custom serial configurator | Manufacturer article linked in home lab handoff section 7.1 | USB CRSF must be established separately |
| Current Windows device discovery returned a Prolific PL2303GC USB Serial COM Port on COM3 | `Get-PnpDevice -PresentOnly`; VID `067B`, PID `23A3` | Identifies a USB-to-serial adapter only; not evidence of an E8 MCU or protocol |
| No EMAX/STM32/CP210/CH340/ExpressLRS-named endpoint was returned by the inspected port/device filters | Present-device discovery outside the sandbox | Bounded discovery result; a different name/class or disconnected device can be missed |
| Installed firmware, board revision, E8 MCU and radio IC | No unit-specific evidence available | Unconfirmed |

The earlier Linux generic STM32 CDC observation is historical and is not a
current Windows endpoint. Port names can change across reconnects and modes.
The Python runtime available for offline tests is Python 3.12.10; access through
the restricted shell's WindowsApps alias fails, but an approved invocation works.

## Next hardware session

1. Record the label/model variant, board revision if visible, battery/power state,
   and connection mode. Do not infer a MCU from a USB description alone.
2. Compare `python tools/elrs-serial-probe.py --list-ports` before and after
   connecting this controller; record VID/PID, serial number and device path.
3. On the identified endpoint, receive without transmitting:
   `python tools/elrs-serial-probe.py -p <device> --capture work/e8-rx.bin`.
   Opening a port can affect DTR/RTS even in receive-only mode.
4. Decode that capture offline with `--decode work/e8-rx.bin`. Preserve the
   original bytes and command/environment notes. Empty output does not establish
   a firmware failure, wrong baud, or lack of all serial protocols.
5. Use `--probe` only after CRSF is a plausible protocol for the identified
   endpoint. Discovery and parameter reads are the only requests supported.
6. Record reported versions as device-reported evidence; reconcile with product
   and board identification before associating a corpus sample with the unit.

Keep device serial numbers, private configuration and captures under ignored
`work/`; publish only reviewed, non-sensitive observations. Actual E8 identity
remains pending a unit-specific hardware session.
