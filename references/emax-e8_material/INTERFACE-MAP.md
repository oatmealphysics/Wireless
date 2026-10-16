# EMAX E8 interface map

| State/personality | Interface | Direction | Evidence | Capability established | Limit |
|---|---|---|---|---|---|
| Normal controller mode | USB HID `0483:5710`, `EMAXJoystick` | Host ↔ controller | Historical live enumeration | Joystick personality exists | Current session did not transition into or query it |
| Documented serial/configuration mode | USB CDC ACM `8888:2001`, 115200 8-N-1 | Host ↔ controller | Current descriptors, host-tool static analysis, live read | Fixed read request and 17-byte response | Generic USB strings do not identify silicon or firmware |
| Configuration service | Vendor binary protocol over CDC | Host ↔ controller | Static analysis plus live read | Rate/config indexes and private six-byte UID can be read | No firmware-version field found; write/update paths prohibited |
| Vendor desktop tool | Windows x64 Qt/MinGW executable | Host ↔ CDC | Static inspection only | Opens serial endpoint and supplies read/write/update UI | Host title/version is not device identity; downloaded binary was not executed by Codex |
| RF control/output | 2.4 GHz control link | Controller → receiver | Product use and separate passive bench observations | Owned E8–TinyHawk link operates | No RF TX was initiated as part of this characterization; RF IC remains unknown |
| PCB/debug | Unconfirmed headers/pads | Unknown | FCC family photograph only | No exact-unit capability established | Enclosure opening/probing is separately gated |

The CDC personality may also carry an update protocol, based on host-tool
analysis. That fact neither identifies the running component nor authorizes any
update, bootloader, or flash operation.
