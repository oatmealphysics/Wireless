# ELRS byte-decoding path without another SX1280 (2026-10-16)

## Result and boundary

The owned TinyHawk III Plus supplies a **read-only channel-level oracle** over
normal Betaflight USB/MSP. It does not expose the SX1280's received FIFO bytes.
The owner declined opening the drone, so a passive internal SPI tap is out of
scope. A software oscilloscope can display SDR/IQ data but cannot recover an
unexposed physical SPI bus through the drone's USB connection. No drone or E8
firmware may be flashed under any circumstances.

The current October 15 private IQ file has 34 source-matched PHY candidates
and 714 symbol positions. None is a validated byte decode. An aggregate
comparison of the 34 private 21-symbol vectors found 28 distinct vectors;
four occurrences of one vector were identical, with other vectors appearing
twice. This repeatability is useful for noise and alignment diagnostics, not
proof of packet type, bytes, or CRC. The raw vectors remain under `work/`.

## Offline path

1. Keep the source-matched SF7, 812.5 kHz, implicit-header, SX1280 modulation
   parameter `0x07` facts separate from the still-unknown byte inverse. The
   [Semtech SX1280 Rev 3.3 datasheet](https://shop.richardsonrfpd.com/docs/rfpd/DS_SX1280-1_V3_3.pdf)
   says `0x07` is 4/8 long interleaving with the same FEC family as legacy
   LoRa but a different interleaving scheme and scrambling. It does not state
   the bit permutation needed by this decoder.
2. Treat a forward simulator and inverse synthetic round-trip as a unit test,
   not an independent oracle. A proposed inverse must then pass multiple
   held-out real frames under a predeclared transform, using the already
   private, independently read UID with `tools/elrs-v3-frame-check.py`. Record
   every transform tried, every failure, and the number of held-out frames;
   do not tune on the held-out set or derive the CRC seed from the candidate.
3. Use simultaneous USB channel and SDR observations as an additional
   high-level check: a deliberate right-stick movement should appear in the
   correct Betaflight `MSP_RC` channel, while the RF burst cadence and symbol
   changes remain consistent. This cannot certify exact bytes, because the
   FC has already parsed and transformed the received RC packet.

The source-matched read-only query surface is MSP v1 `API_VERSION` (1),
`FC_VARIANT` (2), `FC_VERSION` (3), `BOARD_INFO` (4), `NAME` (10), and `RC`
(105). In Betaflight 4.4.2 `src/main/msp/msp.c`, `MSP_RC` serializes
`rcData[i]` as little-endian 16-bit channel values. A 2026-10-16 live,
props-removed COM5 check confirmed Betaflight 4.4.2 / `TH III Plus` and five
stable eight-channel reads. A later read confirmed that `BOARD_INFO` is an
87-byte MSP reply beginning with `S411`; the first ad-hoc reader's 64-byte
limit had caused its timeout. The first paired SDR launch failed before writing
IQ because its UHD DLL directory was absent from the spawned process's PATH;
the USB reader nonetheless obtained 38 samples with no observed channel
variation. A subsequent half-second receive-only B205mini preflight succeeded
after adding `C:\Program Files\UHD\bin` to that launch environment. These
facts do **not** yet establish a simultaneous RC/RF movement correlation.

A second background launch failed before IQ acquisition because Windows
`Start-Process` rejected duplicate `Path`/`PATH` environment keys. Its USB
side did capture 159 private samples: channel 0 ranged 1179–1678 during the
owner's right-stick movement, while channel 3 (throttle) remained 988. A
direct UHD launch resolved the background-process issue. Immediately before
that retry, COM5 briefly vanished from the serial namespace, even though
the owner reported both devices still connected and powered. It reappeared
without a device action, and the retry used only the re-enumerated COM5.

The successful retry recorded **60,010,000 complex samples / 240,040,000
bytes** over 15.002507 seconds at 4 MS/s, 2441 MHz center, 2 MHz analog
bandwidth, 35 dB gain, RX2. The ignored private IQ is
`work/captures/tinyhawk_rc_pair_20261016_retry_2441MHz_4Msps_sc16.iq`
(SHA-256 `B5898512F0D07C370B1249DB870899400C309C7449E36DEBECF8843C513B96B5`).
The private USB log is the neighboring `_msp_rc.jsonl` file (SHA-256
`F636289E5B7DC314F70B60B3481871F01DB09219276407043C0A36DFA8233D80`):
114 valid read-only MSP_RC samples, with channel 0 ranging 1311–1710 and
throttle channel 3 fixed at 988. Neither private file is tracked by Git.

Filesystem timestamps suggest **only about 3.6 seconds of overlap** between
IQ writing (17:45:37.015–17:45:52.010 UTC) and USB sampling
(17:45:48.403–17:46:02.323 UTC). Thirty USB samples fall in that conservative
window, and their channel 0 spans 1313–1633. File creation time is an
approximate SDR start anchor, not a hardware-synchronized timestamp. This
establishes concurrent RF recording and decoded channel movement, but not
packet-level association; PHY extraction and a validated byte inverse are
still needed. A future simultaneous run should start USB sampling *before*
SDR initialization or keep it running longer to maximize overlap.

A bounded 1-ms energy scan of the last four IQ seconds found a median sampled
power of 241.584 (raw ADC-squared units) and 238/4,000 windows above ten
times that median. Several energetic runs were about 26–27 ms long. This
confirms nonzero bursty RF inside the overlap window, but wideband power
alone cannot attribute those bursts to the E8 or associate them with a
specific USB channel sample. No new PHY-symbol or CRC claim follows from it.

## Live limits

- The owner reported the TinyHawk USB port free and confirmed its propellers
  removed. Keep throttle low; use only normal stick movement. No setting,
  binding, reset, bootloader, motor, RF transmission, or flash operation is
  part of this check.
- The separate `tools/tinyhawk-msp-rc-observe.py` defaults to a dry-run plan;
  its live mode is bounded, read-only, and identity-gated. It has synthetic
  tests but this Windows host currently lacks a functioning Python runtime,
  so those tests have not yet been executed here.
- COM5 is the current Windows CDC ACM mapping (`0483:5740`) for this connected
  unit, not a permanent identifier. Recheck endpoint identity before later
  sessions, and never assume a free port is another operator's handoff.
- No SPI tap is attempted. The target config maps SPI3 SCK/MISO/MOSI to
  `PB3/PB4/PB5` and RX chip-select to `PA15`, but the available config is an
  older related 4.3.1 export, not a physical pad map or permission to probe.
