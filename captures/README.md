# E8 / TinyHawk III Plus passive RF capture set

These are metadata and sanitized derivatives from receive-only recordings of
the owner's paired EMAX E8 controller and TinyHawk III Plus ELRS drone. Neither
controller nor drone was flashed, no RF was transmitted, and neither device
was opened for these recordings. The separate E8 wired configuration response
is **not** included. Raw over-air signals can nevertheless reveal
pairing-related data when decoded; the original samples were deliberately
disclosed for research but are no longer stored in active Git history.

The raw `.iq` files are external artifacts. They are retained in the verified
Gate 0 offline archive and must be copied into this directory before running a
full rescan; they are ignored by Git and must not be recommitted or uploaded to
the active remotes. Each IQ sample is interleaved signed 16-bit little-endian
I/Q (`I0,Q0,I1,Q1`), without a header. Both recordings used an Ettus B205mini,
RX2, 2441.000000 MHz center and 4,000,000 complex samples/s. The observed
2441.3788 MHz channel lies within the capture. The October 15 run was 20.000 s
/ 320 MB; the October 16 run was 15.0025 s / 240.04 MB. File names and SHA-256
values are retained in `SHA256SUMS.txt`; the adjacent SigMF metadata records
the original capture provenance.

| File | Contents |
| --- | --- |
| `e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq` | October 15 passive pair capture, 34 selected PHY candidates |
| `tinyhawk_rc_pair_20261016_retry_2441MHz_4Msps_sc16.iq` | October 16 passive pair capture while the owner moved the right stick left/right, 28 selected PHY candidates |
| `oct15-ranked-2026-10-16.jsonl` | Ranked symbol bins derived from the first IQ file |
| `tinyhawk-rc-retry-full-phy-2026-10-16.jsonl` | Ranked symbol bins derived from the second IQ file |
| `tinyhawk_rc_pair_20261016_retry_msp_rc.jsonl` | Concurrent read-only TinyHawk USB/MSP RC-channel observations (114 samples) |
| `decoded-v3-summary.jsonl` | Read-only decoder output: frame types, CRC status, quality, and analog channel values; raw packet bytes and UID omitted |

The October 16 IQ file's **original local filesystem** creation time was
`2026-10-16T17:45:37.014895Z`, and last-write time was
`2026-10-16T17:45:52.010232Z`. Creation time is only an estimate of RF sample
zero, not an instrument-synchronized timestamp. USB samples span about
`17:45:48.403Z` to `17:46:02.323Z`. Only the two final channel visits overlap
in time with the USB log. Do not infer exact packet-to-stick association from
filesystem times alone.

To reproduce the byte-decoding summary (Python 3.10+, standard library):

```sh
python tools/elrs-li-offline.py \
  captures/oct15-ranked-2026-10-16.jsonl \
  captures/tinyhawk-rc-retry-full-phy-2026-10-16.jsonl \
  --jsonl-out work/decoded-local.jsonl
```

The published [WaveShark SX128x long-interleaver map](https://github.com/v0l/waveshark/blob/62b513a7b699fd3a6928908102902abf9d9b2650/crates/decode/src/lora_li.rs)
is the source for the SF7 4/8-LI inverse. The tool checks the result with the
Betaflight 4.4.2 v3 CRC. On these files it reports **54 CRC-valid frames of
62 candidates**: 16 SYNC, 38 RC, and 8 unresolved. SYNC CRC uses its own
transmitted UID suffix; RC CRC uses the suffix transferred from different
SYNC frames. This validates passive packet-byte recovery, not live acceptance
of any crafted packet. `--include-bytes` deliberately exposes packet bytes;
the committed summary does not use it.

The separate approximate USB/RF comparison uses the **predeclared** original
IQ creation-time anchor, with no clock-offset or channel-permutation search:

```sh
python tools/elrs-rc-overlap.py \
  captures/decoded-v3-summary.jsonl \
  captures/tinyhawk_rc_pair_20261016_retry_msp_rc.jsonl \
  --source tinyhawk-rc-retry-full-phy-2026-10-16.jsonl \
  --iq-start-utc 2026-10-16T17:45:37.014895Z
```

Five CRC-valid RC frames fall within 120 ms of a USB sample. After mapping
OTA analog fields `[1,2,3,0]` to this receiver's observed MSP channel order,
the nearest-sample mean absolute differences are 13.6, 0, 0, and 0 channel
units. The moving channel's discrepancy is expected with 10–36 ms timestamp
gaps and rapid stick motion. This supports, but does not prove, individual
packet-to-stick association. No timestamp shift was fitted to improve it.

The IQ files can be rescanned with `tools/elrs-phy-scan.py` (requires NumPy and
SciPy). Its output safety rule deliberately writes under ignored `work/`;
the selected ranked JSONL files above are committed so others can run the
standard-library decoder without reprocessing hundreds of megabytes first.
As fresh end-to-end checks on the **published** IQ copies, full rescans
produced 34 and 28 PHY candidates and the LI tool recovered the same 32 and
22 CRC-valid frames (October 15: 10 SYNC, 22 RC; October 16: 6 SYNC, 16 RC).
The private rescan files' hashes differ from the committed JSONL because their
embedded capture paths point to the new checkout; the decoded counts agree.
October 16 reproduction:

```sh
python tools/elrs-phy-scan.py \
  --capture captures/tinyhawk_rc_pair_20261016_retry_2441MHz_4Msps_sc16.iq \
  --sample-rate 4000000 --center-hz 2441000000 \
  --channel-hz 2441378800 --start-seconds 0 --duration-seconds 15 \
  --output work/rederived-oct16.jsonl
python tools/elrs-li-offline.py work/rederived-oct16.jsonl
```

The research history and limitations are documented in
`research/elrs-sx1280-li-decode-breakthrough-2026-10-16.md`.
