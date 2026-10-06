# Experimental data contracts

These schemas are versioned development interfaces, not a TDOA timing contract.

## Node report schema 1

HTTPS `POST /v1/reports` requires `Content-Type: application/json`, a single
Content-Length of 1–16384 bytes, `X-WR-Node` and the enrolled bearer token.
Browser-origin requests, chunked bodies and redirects are not supported. The body
must contain exactly these top-level fields:

| Field | Meaning |
| --- | --- |
| `schema` | Integer 1 |
| `node_id` | Enrolled 1–48 character node name |
| `stream_id` | UUID fixed by enrollment; binds the persistent sequence stream |
| `boot_id` | UUID for this sender process; not an SDR clock epoch |
| `sequence` | Persistently allocated positive signed-64-bit sequence |
| `created_at` | Sender host wall-clock ISO timestamp with timezone; unvalidated |
| `kind` | `heartbeat` or `recording` |
| `simulated` | Boolean; synthetic recordings must be true |
| `timing` | Both `sample_timing_validated` and `localization_eligible` must be false |
| `position` | GPSD snapshot or explicit unavailable state, with sample timing false |
| `payload` | Bounded metadata object; never raw IQ |

Heartbeat payloads contain `receiver_state: not_probed`, `transport: IP` and
`clock_status: unvalidated`. Recording payloads contain the recorder summary below.
The sender adds `outbox_dropped_total` to both. These reports are authenticated node
claims; their labels and host timestamps are not independently verified ground truth.
The coordinator acknowledges the sequence as `accepted` or `duplicate_or_old`.
A gap is counted when accepting a sequence above the high-water mark plus one.
Older arrivals are ignored rather than used to rewrite history or reduce that gap
count. Repeated acknowledgements do not refresh the node's receipt timestamp.

GPS snapshots contain `state`, `sample_timing_validated: false` and, when known,
`device`. Valid snapshots also have `latitude`, `longitude`, `mode`, `fix_time`,
`age_s` relative to the sender host clock and `receipt_monotonic_s`. Optional GPSD
fields retain their source names: `epx`, `epy`, `epv`, `speed`, `track`, `altHAE`,
`altMSL`. They are not relabeled as 95% confidence values. Monotonic timestamps are
local to the sender boot; they cannot be compared directly across nodes.

## SigMF and the optional `wr` namespace, version 1.0.0

The recorder writes SigMF core 1.2.0 fields using the
[SigMF specification](https://sigmf.org/). It uses `ci16_le` with interleaved I/Q,
plus an optional `wr` metadata extension. No license or verified target label is
invented for operator recordings.

Global extension fields:

| Field | Meaning |
| --- | --- |
| `wr:summary` | Capture UUID, node ID, operator label, simulated flag, frequency/rate/sample/byte counts, SHA-256, host start time, elapsed time, discontinuity/overrun counts, timing/localization flags false |
| `wr:receiver` | Actual micro model/serial/channel/manual gain/bandwidth, or simulation model/seed |
| `wr:q11_scale` | Multiply stored integers by 1/2048 for Q11 normalized values |
| `wr:clock_domain` | `synthetic` or `free_running_device_counter`; never common UTC |
| `wr:timing_note` | Explicit explanation of the missing sample-to-UTC mapping |

Every data block has a core capture segment containing `core:sample_start` and
`core:frequency`, plus:

- `wr:sample_counter`: radio FPGA counter for the first retained sample (synthetic
  index in simulation). Never extrapolate this counter between recording sessions.
- `wr:status_flags`: raw libbladeRF metadata flags; bit 0 denotes overrun.
- `wr:discontinuity`: counter differs from the prior block's counter plus actual count.
- `wr:read_monotonic_start` / `wr:read_monotonic_end`: host read-call brackets.

Segment lengths follow the next segment's sample_start or the dataset length.
`core:sha512` hashes the complete dataset. `core:datetime` and geolocation are
omitted because the implementation does not map sample arrival to UTC/position.
The raw library flags and counter discontinuities matter even when the output file
itself is contiguous. A completed file can contain explicitly flagged dropped RF
samples; completion only means the requested number of returned samples was saved.
