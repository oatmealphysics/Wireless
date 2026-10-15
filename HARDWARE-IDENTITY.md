# E8 hardware identity and serial evidence

**Subsequent connected-device session:** [USB characterization](research/E8-USB-CHARACTERIZATION.md)
records the new COM4 endpoint, unsuccessful read-only queries, recovered vendor
read-request semantics, and the unresolved variant/power/serial-mode prerequisites.
The observations below describe the earlier discovery session.

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

## Windows session — 2026-10-15

A second USB device appeared on the Windows home workspace as COM4:

| Property | Value |
|---|---|
| Port | COM4 |
| VID | 0x8888 |
| PID | 0x2001 |
| Serial | 00000001 |
| USB class | CDC ACM (02/02/01) |
| Bus description | "USB Serial" |
| Driver | usbser.inf (Microsoft) |
| HW revision | REV_0100 |

This is distinct from the Prolific adapter on COM3 (VID 067B, PID 23A3).

**Probe results:** Zero bytes received across all tests — MSP v1/v2, CRSF,
MAVLink, CLI, passive listen, DTR/RTS toggling, ESP32 boot reset, 1200bps
bootloader touch. Seven baud rates tested (9600 through 921600). The port opens
and closes without error but produces no data.

**Conclusion:** The E8 needs battery power and the power switch engaged. USB CDC
enumerates in charging-only mode but the application firmware is not running.
Full characterization details in `E8-SERIAL-CHARACTERIZATION.md`.

**Causal qualification:** The battery/charging explanation above is a hypothesis,
not established by empty serial reads. The owner reports a red LED and believed
battery power, but startup, variant and serial-mode conditions remain unresolved.
The recovered vendor read command also returned no bytes in the present state.
The manufacturer documents a separate serial mode; see the newer
[USB characterization](research/E8-USB-CHARACTERIZATION.md) for primary sources
and the distinction between USB revision, host-tool version and device firmware.

## Next hardware session

1. Power on the E8 (battery + switch) and confirm COM port assignment.
2. Try CRSF at 460800 baud: `python tools/elrs-serial-probe.py -p COM4 -b 460800 --probe --dump-params`
3. If CRSF fails, try MSP at 115200 with the msp_probe.py script.
4. Extract firmware version, UID, binding phrase status, WiFi config, failsafe behavior.
5. Record reported versions as device-reported evidence; reconcile with product
   and board identification before associating a corpus sample with the unit.

Keep device serial numbers, private configuration and captures under ignored
`work/`; publish only reviewed, non-sensitive observations.
