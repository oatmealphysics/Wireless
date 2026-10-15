# E8–TinyHawk passive IQ analysis — 2026-10-15

## Scope and provenance

This is **receive-only analysis** of a 20-second capture made while the owner
reported the EMAX E8 and TinyHawk III Plus ELRS pair powered on and connected.
No device was configured or transmitted to for this analysis. The raw IQ,
spectrogram, and capture metadata remain in ignored `work/captures/`; do not
commit them or the private pairing UID.

| Item | Value |
|---|---|
| Capture | `work/captures/e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq` (local only) |
| SHA-256 | `5FAF27CB6B8C7261950EBF38A67034BEE9223C290C49DC6F47C4F5E93F0FEE2F` |
| SDR | Ettus B205mini, UHD 4.11.0.0-release, RX2, 35 dB gain |
| Samples | 80,000,000 complex interleaved int16 I/Q; 4.000 MS/s; 20 seconds |
| Tuning | 2441.000 MHz center, 2.000 MHz analog RX bandwidth |
| Source comparator | Installed TinyHawk Betaflight 4.4.2, `src/main/rx/expresslrs_common.c` in `firmware/src/betaflight-4.4.2.tar.gz` |

The fixed-frequency recording covers only a small part of the 80-channel
FHSS band. The physical antenna connection was inferred by comparing two
short pilots: RX2 showed much stronger bursts than TX/RX. The pair was not
switched off for a control recording, so exact transmitter attribution is
strongly supported by protocol agreement but not independently proven.

## Measured observations

- The spectrogram shows upward LoRa chirps about 0.8 MHz wide. In the clean
  preamble, chirp resets are spaced 156–160 µs at 4 µs analysis resolution,
  matching SF7 at the SX1280's nominal 812.5 kHz bandwidth (157.54 µs per
  symbol). This **contradicts the old FLRC label** for the live pair.
- Dechirping one packet after frequency shifting and resampling the IQ to
  812.5 kS/s recovered **12 repeated preamble symbols**, followed by sync
  symbol bins **8 and 16**. Candidate payload *symbols* were recovered, but
  these are not decoded bytes and were not CRC-validated.
- A clean 2441.38 MHz channel visit begins at approximately 1.187, 3.321,
  5.454, 7.587, 9.720, 11.853, 13.986, 16.119, and 18.253 seconds. The
  mean measured center is 2441.3788 MHz. Successive visits are 2.133–2.134
  seconds apart. At 150 Hz with four packets per channel, 80 channel slots
  take `80 × 4 / 150 ≈ 2.1333` seconds. This is consistent with the v3
  source's fixed **channel 41** at the start of every 80-slot FHSS block.
- One channel-41 visit contains packet starts at 1.18793, 1.19460, 1.20127,
  and 1.20792 seconds: 6.65–6.67 ms spacing, with each packet on-air for
  about 5.9 ms. The observed cadence matches LoRa 150 Hz and the
  source-defined `fhssHopInterval = 4`.
- Bursts near 2440.39 MHz also recur, but at **nonuniform** intervals (for
  example 0.094, 2.014, 3.961, 6.494 seconds). They fit an ordinary hopping
  channel, not the fixed sync schedule. Thus the older observation at
  2440.4 MHz does **not** imply the live link uses the v4 sync channel.
- The local observed hop pattern repeats about every 6.4 seconds, matching
  `240 slots × 4 packets / 150 Hz` for the 4.4.2 source's 240-entry sequence.
  Other visible centers near 2439.49 and 2442.33 MHz are at the edge of the
  analog passband and their precise estimates are filter-biased.

Frequency centers are SDR measurements, not absolute-calibration claims;
the approximately 20 kHz offset from the nominal 2441.4 MHz may include
SDR/transmitter reference error and measurement bias.

## Method and limits

Detection used 1 ms mean IQ power against a median-noise baseline, followed
by short-time spectra and 10 µs power windows for packet timing. For symbol
inspection, one channel-41 packet was mixed toward baseband, resampled by
13/64 from 4 MS/s to 812.5 kS/s, and dechirped with a 128-sample SF7
reference before FFT peak selection. The preamble/sync observations are from
the recording, not a synthetic packet round-trip.

The existing `tools/elrs-analyzer.py` accepts **already decoded packet
bytes**; despite its introductory wording it has no raw-IQ LoRa demodulator.
The project also lacks an SX1280 long-interleaving 4/7 decoder. We therefore
do **not** have validated SYNC or RC payload bytes, UID fragments, FHSS index,
or CRC matches from this capture. Source-to-capture agreement on PHY and
timing substantially advances Gate 1 of `SESSION-NOTES.md` §20, while Gate 2
remains open. It establishes no forged-packet acceptance or exploitability.

The next offline step is to implement or adapt an SX1280-compatible
deinterleaver/FEC/whitening decoder and require several independent frames
to pass the Betaflight 4.4.2 v3 CRC before reporting packet fields. An
owner-assisted pair-off control capture would separately strengthen RF
attribution, without touching the E8 serial interface.
