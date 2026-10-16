# EMAX E8 evidence dossier

Status as of 2026-10-16: **partially identified**. The connected controller's
USB personalities and normal configuration-read protocol are confirmed. Its
exact PCB revision, MCU, radio IC, installed firmware version, and exact firmware
image are not confirmed.

## What is confirmed

- A current Linux serial personality enumerates as USB CDC ACM `8888:2001`,
  manufacturer `Your Company`, product `USB Serial`, on `/dev/ttyACM1` during
  the 2026-10-16 session. Device paths can change after reconnect.
- A historical normal personality enumerated as HID `0483:5710`, product
  `EMAXJoystick`.
- A documented read-only request at 115200 8-N-1 returns the expected 17-byte
  configuration response. The native reader succeeded on 2026-10-16. The UID
  and raw response remain private under `work/e8-session/`.
- The manufacturer-linked Windows configurator was acquired and inspected
  statically. Its `Serial Update Tool 2.1` string identifies the host program,
  not the controller firmware.
- The public FCC filing for model EMAXE8 is a useful product-family reference.
  It documents 2.4 GHz operation and includes internal PCB photographs, but
  their chip top-marks are not legible enough to identify the processor or
  radio. The filing is not proof that the connected ELRS variant uses the same
  board revision.

## What is not confirmed

- MCU manufacturer/model, RF transceiver model, flash size, PCB revision, or
  debug-port state on this exact unit.
- Whether the CDC implementation is in a bootloader, application branch, or a
  separate configuration image. Sharing an update transport does not answer
  that architecture question.
- Operating system/RTOS status, memory map, boot chain, or installed firmware
  version.
- Any exact E8 firmware binary. Related ExpressLRS targets and host executables
  are not E8 device firmware.
- The meaning of all response configuration indexes. The live read proves the
  bytes and rate mapping already validated by the host tool; it does not justify
  guessing the remaining labels.

## Completion boundary

The least invasive next steps are an exterior/label/connector photo set for the
exact unit and a vendor request for its board/firmware identification and
download. A close photograph of the actual PCB could establish the chip
top-marks, but opening the enclosure requires a separate owner gate under
`SECURITY.md`. Firmware readout is outside the current normal-read scope and
would require its own explicit plan and authorization. Flash, erase, update,
reset/bootloader-entry experiments, settings writes, live fuzzing, and RF TX
remain prohibited for this controller.

## Records

- [Target card](TARGET-CARD.md)
- [Interface map](INTERFACE-MAP.md)
- [Artifact manifest](ARTIFACTS.md)
- [Evidence ledger](EVIDENCE-LEDGER.md)
- [Test log](TEST-LOG.md)
- [Open questions](OPEN-QUESTIONS.md)

Detailed prior work remains in
[`HARDWARE-IDENTITY.md`](../../HARDWARE-IDENTITY.md),
[`research/E8-USB-CHARACTERIZATION.md`](../../research/E8-USB-CHARACTERIZATION.md),
and [`research/e8-protocol-reconstruction.md`](../../research/e8-protocol-reconstruction.md).
