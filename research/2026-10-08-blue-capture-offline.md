# Older wideband ELRS capture: offline BLUE/CDIF intake

This note covers receive-only analysis of one private capture from the October
2026 GitHub release. The raw recording, release manifest, and any pairing
identifiers remain under ignored `work/captures/`. No radio was opened, no
controller was configured, and nothing was flashed or transmitted.

## Provenance and container

| Item | Verified value |
|---|---|
| Local file | `work/captures/2026-10-08_13-07-21_2450mhz_pred.cdif` (not committed) |
| SHA-256 | `9a1766fe9e2162cad3ea2fedb68bebf9db8dc1e9d3ac2c43b6f8732ef48fc423` (matches private release manifest) |
| Container | MIDAS/Platinum BLUE, type 1001, complex-float `CF`, little-endian |
| Header and sample data | 512-byte header; 739,694,592 bytes of `cf32le` IQ; 1,536 trailing metadata bytes |
| Sample count and duration | 92,461,824 complex samples; 28.000 MS/s; 3.302208 seconds |
| RF center and digital span | 2450 MHz; nominal 2436–2464 MHz |
| Header timestamp | 2026-10-08 13:07:21.653430 UTC |

The sample rate, center, datatype, and data boundaries were independently
checked using the SigMF Python [BLUE converter](https://sigmf.readthedocs.io/en/stable/converters.html),
which reads the original container without writing a conversion. The release
manifest validates the *whole* `.cdif` file. `tools/elrs-capture-intake.py` now
reads the BLUE header directly with the standard library and counts only the
declared sample data region; treating all file bytes as raw IQ would add 256
false complex samples and misread the header as signal.

## Observed 2441.3788 MHz bursts

The known channel from the separate October 15 E8–TinyHawk recording lies
within this capture's digital span. A 10 ms FFT-window scan showed two strong
channel visits around 0.41 and 2.55 seconds, about 2.133 seconds apart. A
channel-filtered 0.1 ms power scan resolved four ~6 ms bursts in each visit:

| Visit | Candidate preamble starts (seconds from file data start) |
|---|---|
| First | 0.40729231, 0.41395200, 0.42061908, 0.42727631 |
| Second | 2.54043323, 2.54709538, 2.55376123, 2.56043446 |

All eight bursts showed 12/12 consistent SF7 preamble chirps followed by the
expected sync-symbol bin offsets of 8 and 16 using the existing
`tools/elrs-iq-symbols.py` detector on bounded, frequency-shifted windows.
The first-to-first revisit interval is 2.133141 seconds; within each visit,
packet starts are approximately 6.66–6.67 ms apart. This agrees with the
150 Hz, four-packets-per-hop timing measured in the October 15 recording and
with the Betaflight 4.4.2 source configuration. It does **not** establish that
the two recordings are the same physical transmitter; that would require
validated payload identity or controlled attribution.

The older file's BLUE datatype explains why treating it as raw sc16le IQ is
invalid. The symbol CLI now auto-detects `.cdif`, skips its header/trailer,
and accepts this file directly. For the check, analysis read `cf32le` samples
beginning at byte 512 and resampled only short windows from 28 MS/s to
812.5 kS/s. No full-file conversion or output IQ copy was made. One example:

```text
python tools/elrs-iq-symbols.py work/captures/2026-10-08_13-07-21_2450mhz_pred.cdif \
  --sample-rate 28000000 --center-hz 2450000000 \
  --channel-hz 2441378800 --packet-start 0.40729
```

## Limits and next validation gate

These are physical-layer candidates, not decoded packet bytes. We have not
validated CRC, UID fragments, FHSS indices, control fields, or a binding
phrase from this file. The 2.133-second recurrence and sync pattern are strong
evidence of the same protocol timing, but not of device identity or an
exploit. A validated SX1280 4/8 long-interleaving/FEC path remains necessary
before claiming byte-level decoding. Any cross-capture comparison should use
CRC-validated frames, not just similar burst timing.
