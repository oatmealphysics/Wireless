# TinyHawk paired capture: Python setup and offline PHY check (2026-10-16)

## Scope and provenance

This is **offline, receive-only** analysis of the October 16 B205mini IQ file
recorded while the owned E8/TinyHawk pair was in normal operation and the
owner moved the controller's right stick. No device was reopened, configured,
transmitted to, probed, or flashed for this analysis. The raw IQ, USB channel
log, and symbol-candidate JSONL are ignored private files under `work/`.

The Windows host previously could not run the Microsoft Store Python binary;
the existing project venv referred to that unavailable base interpreter.
An official [Python 3.12.10 Windows x64 installer](https://www.python.org/downloads/release/python-31210/)
was downloaded from `https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe`
to ignored `work/runtime-bootstrap/`. Its SHA-256 was
`67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB`;
Windows Authenticode reported a valid Python Software Foundation signature.
Only after those checks was it installed per-user under
`C:\Users\Board\Documents\Codex\Python312`, without altering system PATH.
An independent ignored venv at `work/python312-venv/` contains Python 3.12.10,
NumPy 2.5.4, SciPy 1.18.1, and pyserial 3.5. The old broken venv was preserved.

## Validation

- The six synthetic tests for `tools/tinyhawk-msp-rc-observe.py` passed.
- The complete `tools/tests/` offline suite passed: **93 tests** in 5.592 s.
- `tools/elrs-capture-intake.py` confirmed the new IQ file is sample-aligned
  sc16le: **240,040,000 bytes / 60,010,000 complex samples / 15.0025 s** at
  4 MS/s. Its SHA-256 is
  `B5898512F0D07C370B1249DB870899400C309C7449E36DEBECF8843C513B96B5`.
  The 2441.3788 MHz observed channel is inside its 2439–2443 MHz digital span.
- `tools/elrs-phy-scan.py` scanned the full 15-second recording on that
  channel. It saved **28 private PHY candidates** in
  `work/tinyhawk-rc-retry-full-phy-2026-10-16.jsonl` (SHA-256
  `79002749AB07AD6BBD84456532EF7AADE5AD37DECE1FF9C80779D30DC5862267`).
  These form seven visits with four frames each. Visit starts are approximately
  1.629, 3.762, 5.895, 8.029, 10.162, 12.295, and 14.428 seconds after the
  IQ file start; mean spacing is **2.133147 s**, consistent with the previously
  observed Betaflight 4.4.2 v3 150 Hz/channel-41 schedule.
- All **28/28** candidates pass the existing 12/12 preamble and two-sync-chirp
  gates and produce 21 candidate payload-symbol positions. Across the 588
  positions, 141 have a top-two FFT-bin margin below 3 dB. That is a power
  margin, not a calibrated error probability.
- The two four-packet visits at about 12.295 and 14.428 seconds fall within
  the approximate 3.6-second USB/IQ overlap. The USB channel-0 log shows
  right-stick movement in that interval. Filesystem creation time is only an
  approximate IQ start anchor, and changing symbol vectors can also reflect
  packet counters, CRC, and other fields; this is **not** a byte-for-byte or
  frame-to-channel association.

## Limit

`tools/elrs-phy-scan.py` verifies waveform structure; it does not invert the
SX1280 `0x07` 4/8 long interleaver, scrambling, and FEC. Therefore **zero
packet bytes or CRCs have been validated** from this new recording. The next
offline stage is a separately tested inverse with independent known-answer
evidence and held-out real-frame CRC checks. Synthetic forward/inverse
round-trips alone are not sufficient. The owner declined opening the drone;
no internal SPI tap is part of the current path. Neither the E8 nor TinyHawk
may be flashed.
