# Connected E8 collaboration handoff — 2026-10-14

The owner asked Codex and Claude to work together after confirming concurrent
controller probing. This file is a shared handoff, **not confirmation that Claude
has received or acknowledged it**. Codex has no direct messaging connection to
the external Claude app and has asked the owner to relay this note.

## Safety and access

- **Never flash the connected EMAX controller with anything, under any
  circumstances.** No firmware upload, erase, restore, update, or bootloader
  loader execution. The owner's prohibition also appears in `../AGENTS.md`.
- This session is identification and normal configuration reads only: no
  settings writes, reset/bootloader-entry sequences, live fuzzing, or RF tests.
  Opening a serial port can itself affect control lines; do not describe it as
  zero-impact.
- **Codex is not opening COM4.** Claude may retain live access while Codex works
  offline. A free port is not an agreed handoff. Before Codex resumes live reads,
  obtain an explicit release from Claude/the owner, then recheck the endpoint.
- Keep captures, binding data and other private configuration under ignored
  `work/`; publish only reviewed non-sensitive observations.

## Evidence available to Claude

- Windows COM4 was `8888:2001`, CDC ACM, bus description `USB Serial`. COM3 is a
  different Prolific adapter and was not identified as the controller.
- Owner reports a screenless E8-like handset, R4+ plus power startup with a
  flashing green signal/mode LED, and a previous successful read using EMAX's
  custom configurator on Ubuntu under Wine. The earlier data/build is unavailable.
- Owner additionally recalls **COM33** in that Wine session and Linux device
  **`/dev/ttyACM0`**. The exact Wine mapping has not been inspected; COM33 was
  likely its Windows-style alias for the Linux serial endpoint. These historical
  names do not require COM33 on this Windows PC or establish a firmware version.
  Linux's `ttyACM` naming is consistent with the CDC ACM interface observed here;
  see the [kernel ACM documentation](https://docs.kernel.org/usb/acm.html).
- The manufacturer-linked configurator inspected offline has SHA-256
  `7ec41368c1d7fe9cbf7e25ba053b05519f7e1aa8542c698e70764162be99362d`.
  Codex has **not executed** this unsigned executable or its bundled loader.
- The attributed normal Read Configuration request is **64 bytes**:
  `02 01` followed by **62 zero bytes**, at **115200, 8-N-1, no flow control**.
  The host callback requests a **17-byte** reply beginning with `02`.
  No dedicated firmware-version query was identified.
- The immutable helper is `../tools/e8-read-config.py`; it defaults to dry-run.
  Do not start it while another process owns the port. Its current live path
  explicitly requests DTR/RTS false, but this does not prove absence of transients
  or equivalence to Qt/Wine behavior.
- Both Codex vendor-read attempts received zero bytes in a three-second window,
  including one after the reported serial-mode startup. A later longer-settle
  attempt failed to open the port and **sent nothing**. Empty reads do not prove
  charging-only mode, an incorrect protocol, or absence of vulnerabilities.
- The app's serial-open success branch showed UI state changes but no extra
  application protocol handshake in the reviewed code. Library/driver behavior
  and Wine/Windows differences remain unresolved.

Full provenance, code addresses and caveats are in
[E8 USB characterization](E8-USB-CHARACTERIZATION.md).

## Useful next handoff

If Claude obtains a normal successful read, record the exact configurator build,
OS, mode/LED observation, serial settings and byte count. Preserve raw request and
response privately; report whether the UI actually labels a firmware version.
Do not publish binding bytes or confuse `Serial Update Tool 2.1`, `REV_0100`, or
the Windows driver version with installed controller firmware.

If the port still returns no data, stop broad protocol/baud/reset guessing.
Compare the known-working Wine setup with the Windows connection offline or
through a normal read-only configurator session. Any new live operation must
respect the no-flashing rule and single-owner access above. A wired read does
not establish that its handler is reachable over RF.
