# Identity tracks and offline target binding

Wireless has one shared contract for turning passive detector output into
stable identity tracks and checking whether an offline proof of concept matches
the observed target. Both paths are deterministic and Python 3.10+ compatible.

## Stable identity tracks

`elrs_sdr.identity` accepts JSON arrays, an object with an `observations` array,
or JSON Lines. A detection carries a protocol, measured frequency, evidence
maturity, source, and either a typed identity or an explicitly identity-bearing
unique frequency.

```bash
python3 -m elrs_sdr.cli identify-tracks observations.jsonl \
  --output tracks.json --expect-tracks 2 --expect-spectrum 1
```

The output uses `wireless-detection-track-set/v1`. Canonical entity IDs are
SHA-256 hashes over the versioned protocol and typed identity. Ordinary
measured frequency is retained in `frequencies_hz` but is not part of that key,
so a hopping link remains one track. If frequency is truly identity-bearing,
the producer must say so with `unique_frequency_hz`.

Evidence maturity is `presence`, `family`, `specific`, or `identified`.
Optional compatibility fields are `protocol_version`, `phy`, `model`, and
`firmware`. Conflicting values are preserved under `compatibility_conflicts`
and the corresponding exact field becomes unknown.

Spectrum records use `wireless-spectrum-evidence/v1`. They remain separate
context and cannot create or promote an identity.

Exit `0` means the track set was produced and all requested expectations
matched. Exit `3` means the track set was produced but an `--expect-*` check
failed. Exit `2` means invalid input or an output error.

Existing detector results can cross the same seam directly:

```python
observation = detection.to_observation(
    timestamp="2026-11-06T18:00:00Z",
    source="recorded-capture",
    compatibility={"protocol_version": "ota-v3", "phy": "lora-sx1280"},
)
```

`elrs_sdr.fusion.detection_event()` adapts the same result into the live
`TrackTable`; its canonical entity ID does not change as measured frequency
changes.

## Offline PoC target binding

`elrs_sdr.target_binding` compares exactly one track with a
`wireless-poc-compatibility/v1` manifest:

```json
{
  "schema": "wireless-poc-compatibility/v1",
  "poc": {
    "name": "example-offline-poc",
    "version": "1.0.0",
    "mode": "offline-non-transmitting"
  },
  "constraints": {
    "protocol": "elrs",
    "protocol_version": "ota-v3",
    "phy": "lora-sx1280",
    "identity_type": "uid"
  },
  "minimum_evidence_maturity": "identified"
}
```

```bash
python3 -m elrs_sdr.cli bind-target manifest.json tracks.json \
  --entity-id did:v1:... --output receipt.json
```

Exit `0` means every declared constraint matched and the receipt contains a
deterministic `binding_id`. Exit `3` means rejected and no binding ID is
emitted. Exit `2` means invalid or ambiguous input. Missing, unknown,
conflicting, mismatched, or insufficient evidence always rejects.

The binder evaluates compatibility only. It never imports or invokes the named
PoC and never performs transmission or device I/O.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_identity.py' -v
python3 -m unittest discover -s tests -p 'test_target_binding.py' -v
python3 -m unittest discover -s tests -p 'test_detect_integration.py' -v
python3 -m elrs_sdr.detect selftest
```
