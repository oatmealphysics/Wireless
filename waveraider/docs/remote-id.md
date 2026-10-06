# Remote ID support

[Back to README](../README.md)

Remote ID broadcasts report information about a UAS and its operator. Wave Raider
currently decodes supported messages from recorded packet captures. Values are
as broadcast, not independently authenticated.

## Coverage

| Input | Current status |
| --- | --- |
| Recorded Wi-Fi beacon Remote ID | Supported; independent reference captures and edge cases tested |
| Recorded Bluetooth legacy Remote ID advertisements | Supported; synthetic wire-roundtrip and malformed-input tests |
| Live Remote ID reception | Unsupported; next milestone requires a positive transmitter and validated receive path |
| Wi-Fi NAN | Unsupported, including NAN frames present alongside beacons in reference captures |
| Bluetooth 5 Long Range / Nordic sniffer link type 272 | Unsupported |
| Monitor and Settings | Bundled sample only; no live Remote ID feed |

An SDR spectrum sweep is not a decoded packet stream. SDR discovery or tuning does
not establish Remote ID receive support.

## Decoding and validity

- Protocol versions 0, 1, and 2 are supported. Other versions are diagnosed.
- Wi-Fi/Bluetooth framing includes the message counter. Complete single messages
  and supported message packs are handled; truncated envelopes and malformed
  packs cannot create or refresh a report.
- Basic ID, Location, System, and Operator ID are decoded within the implemented
  field subset. Full Authentication/Self-ID contents, speed, and direction are
  outside current support. Authentication/Self-ID alone cannot create or refresh
  a report; supported messages in mixed packs remain usable.
- Both-zero coordinates and encoded unknown altitude/timestamp values display as
  not reported. Invalid coordinates/timestamps produce diagnostics while other
  valid fields remain usable. New unknown/invalid values clear older values.
- Text serial-number and registration IDs can identify a report. Supported ASCII
  identifiers retain exact text, including spaces. None-type, binary, and badly
  padded identifiers retain raw bytes but are not promoted to UAS watchlist keys.
- Operator locations carry their declared type: takeoff, live GNSS, or fixed.
  They do not necessarily show where a pilot is currently standing.

A valid partial report can have identity or position “not reported.” Recognizing
an OUI, UUID, or unsupported message type alone is insufficient for a report.

## Grouping and time

Rows represent transmitters grouped by transport and address, not unique aircraft.
The same address over Wi-Fi and Bluetooth stays separate. Multiple addresses or
transports may correspond to one aircraft; automatic aircraft fusion is absent.

First/last receive times come from capture observations. Claimed Location message
time is distinct; System timestamp decoding is deferred. Capture provenance is
retained through the survey path. Full session history and operational stale-track
handling remain roadmap work; the 30-second helper is a provisional default.

## Validation boundary

Independent Wi-Fi captures are cross-checked against pinned opendroneid-core-c
output, with [fixture provenance](../tests/fixtures/opendroneid/PROVENANCE.md).
The reference capture's None-type Basic ID is not a valid identifier just because
its bytes look printable. Reference agreement does not authenticate a broadcast
or prove every field conforms to the standard.

No positive live Remote ID reception or field detection-rate claim is supported
by this release. See the [next milestone](roadmap.md#m1b-prove-one-live-remote-id-receive-path--next-blocked-on-a-transmitter).
