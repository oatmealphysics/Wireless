# EMAX E8 test log

## 2026-10-16 — connected read-only characterization

- Synced `main` before work and reread the device-specific constraints.
- Enumerated USB without changing modes. The controller appeared on
  `/dev/ttyACM1` as `8888:2001`, CDC ACM 1.10, with bulk IN/OUT and interrupt
  IN endpoints. Generic manufacturer/product strings were recorded; unrelated
  USB identities were omitted.
- Checked for a port owner before opening it; none was reported at that moment.
- Ran the existing bounded reader once with its explicit execution flag. It
  received the expected 17-byte response and parsed the 150 Hz rate value.
- Kept the response and UID under ignored `work/e8-session/`; no raw bytes or
  private digest are included here.
- Did not write settings, change modes, toggle a reset/bootloader sequence,
  execute the vendor binary, access update paths, fuzz, flash, or transmit RF.

## 2026-10-16 — public-document review

- Downloaded the public FCC internal-photograph and test-report exhibits to
  temporary storage, verified their hashes, and inspected them offline.
- The photographs show a board-family layout and an unreadable square-package
  top-mark. The filing's confidential schematic/block diagram is unavailable.
- The test report supplies RF characteristics but no MCU/RF-chip identity.

Earlier attempts and the later successful protocol reconstruction remain in
the linked repository research documents; this log does not replace them.
