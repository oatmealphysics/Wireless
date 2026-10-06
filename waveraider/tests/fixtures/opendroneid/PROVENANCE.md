# Open Drone ID reference fixtures

Used by `tests/test_remote_id_reference.py` to check WaveRaider's Remote ID decoder
against captures and a decoder that WaveRaider did not produce.

## Captures (inputs)

From [opendroneid/wireshark-dissector](https://github.com/opendroneid/wireshark-dissector)
at commit `d12670f` (2026-06-12), Apache License 2.0 — see `LICENSE` in this directory.

| File | SHA-256 |
| --- | --- |
| `odid_wifi_bcn_sample.pcap` | `86602b63244326adf6e909c140af1aae7d7e729c81ab2e2273302de058765a1f` |
| `odid_wifi_sample.pcap` | `651077b29e9d6916333a2cab627acc02b71d411f9a61cbd1392025bd32001546` |

`odid_bt5_lr_sample.pcapng` (Bluetooth 5 Long Range, Nordic sniffer link type 272,
SHA-256 `586cd7ec…158b46b`) is not vendored: WaveRaider does not decode that capture
format yet. `odid_wifi_sample.pcap` also contains Wi-Fi NAN action frames, which are
not decoded yet either; only its beacons are checked.

## Expected values

`*.expected.jsonl` come from the opendroneid-core-c reference decoder
([opendroneid/opendroneid-core-c](https://github.com/opendroneid/opendroneid-core-c)
commit `6484f26`, Apache-2.0), not from WaveRaider:

1. `extract_payloads.py` lists each beacon's Remote ID payload: the bytes after vendor
   OUI `FA:0B:BC`, app code `0x0D` and the 1-byte message counter.
2. `reference_harness.c`, compiled against that commit's `libopendroneid/opendroneid.c`,
   passes each payload to `decodeOpenDroneID()` and prints the fields the reference
   decoder marks valid.

```sh
clang -std=c99 -I <core-c>/libopendroneid -o harness \
    reference_harness.c <core-c>/libopendroneid/opendroneid.c
python extract_payloads.py odid_wifi_bcn_sample.pcap > bcn.txt
./harness bcn.txt > odid_wifi_bcn_sample.expected.jsonl
```

Limits of this cross-check: the reference decoder neither interprets "unknown"
encodings (altitude −1000 m, both-zero coordinates, timestamp 0xFFFF) nor
authenticates anything, so the tests apply those rules on top of its output. Its
decoded Basic ID in these captures has ID type 0 (None); the tests keep the raw bytes
but do not treat the text as a valid identifier. No Wireshark dissector output was used.
