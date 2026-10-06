# DroneID validation recordings

Attribution: **IQ recording by the WaveShark project,
https://github.com/v0l/waveshark, CC BY 4.0.**

Source manifest and license at upstream commit
`435bb068a348823983b468041a70a3ace0846427`:
- https://github.com/v0l/waveshark/blob/435bb068a348823983b468041a70a3ace0846427/testdata/fixtures.toml
- https://github.com/v0l/waveshark/blob/435bb068a348823983b468041a70a3ace0846427/testdata/offair.toml
- https://github.com/v0l/waveshark/blob/435bb068a348823983b468041a70a3ace0846427/testdata/LICENSE.md

Recordings are not committed. They may contain real-world identifiers; validation
reports contain only filenames, hashes and aggregate candidate counts.

## Positive fixture

`droneid_mini4k_2444.5M_15360k.cs8`: signed interleaved 8-bit IQ, 15.36 MSPS,
2444.5 MHz. Upstream recorded a Mini 4K indoors without a GNSS fix or linked
controller, then mixed, filtered, resampled and cut the recording to its bursts.
This is not a continuous field capture. Upstream reports matching payload checksums
and the physical airframe serial; WaveRaider itself does not decode those payloads.

Compressed SHA-256: `626b82be90c7f1bc56c7f4dd8fba925c54e757a4d9571ab41a8fd023a569c109`.
Decompressed SHA-256: `ab9a98eae3549e104e4f9ddc12332916bd8db817d0a41e152f003b708e9b3cf5`.
`tools/fetch_droneid_fixture.py` verifies both before accepting the download.
The recorded tests require 13 extracted bursts, each detected at the native offset
and after +12 kHz / −18 kHz shifts. Missing or mismatched configured files fail;
unconfigured local runs explicitly skip. CI fetches and runs this fixture.

## Negative corpus

`negatives.json` pins eight upstream recordings, sample formats and original sample
rates. Filenames retain upstream labels; these are selected interference checks,
not proof that an entire protocol family cannot trigger a false candidate.

`tools/validate_droneid_corpus.py` verifies decompressed SHA-256, reads signed IQ,
resamples with SciPy's rational polyphase filter to 15.36 MSPS, and tests the complete
recording in 700 ms windows with four-symbol overlap (including a final partial
window). No decoded identities or coordinates are exported. NumPy and SciPy are
required for this optional tool. Samples, resampling and window policy are fixed;
these recordings must not be treated as an independent held-out benchmark after
being used for development.

The small, selected corpus does not establish operational false-alarm rates,
RF sensitivity, range, multi-model support, or performance under multipath.
