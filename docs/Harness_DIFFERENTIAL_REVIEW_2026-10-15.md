# Update review — 2026-10-15

## Executive summary

Reviewed range: `4f0d9e4..3737029`. This includes 36 commits affecting 66 files, with 31,996 additions and 2,205 deletions. The review began at `2b3618a`; Claude's concurrent test-repair commit `3737029` was subsequently inspected and retested before this report was finalized.

**Recommendation: conditional acceptance of the research update; the new operational tools are not yet validated as a working CTF capability set.** Three high-priority protocol defects and five medium-priority correctness/validation defects were identified. These are project-tool/research findings, not newly discovered vulnerabilities in the E8 or an autopilot.

| Priority | Count | Meaning in this review |
|---|---:|---|
| P1 / high | 3 | Core packet processing or generation gives materially incorrect results |
| P2 / medium | 5 | Unsupported capability claims, false viability conclusions, broken integration, or missing regression coverage |
| Critical | 0 | No critical finding established in the reviewed scope |

Offline validation passes, including all eleven registered firmware hashes and the 33 tests selected by CI. The separate root pytest suite initially returned **80 passed, 10 failed**; after Claude's concurrent repair it returns **90 passed**. No controller, serial port, RF transmitter/receiver, or live network target was accessed for this review. No research-tool implementation was modified by this reviewer.

The update improves corpus provenance, passive serial defaults, malformed-frame handling, CVE evidence qualifications, and explicit E8 access constraints. Its remaining operational defects are mostly in newly added tools that are outside the CI-selected test suite.

## 1. [P1] Modified MAVLink signature fields do not implement a signing bypass

**Locations:** `tools/mavlink-signing-replay.py:202–250`, used at `:536–543`. Introduced by `9cf1546`.

`rebuild_with_link_id()` and `rebuild_with_timestamp()` alter authenticated fields but retain the old signature when no key is supplied. Both CLI mutation paths call these methods without a key. Their output therefore does not authenticate under the original signing key. The optional key-taking branches also clear the signing bit in the hashed header while retaining it in the returned packet, so those signatures do not cover the actual returned header.

The [official signing format](https://mavlink.io/en/guide/message_signing.html#signature) authenticates the complete packet together with its link ID and timestamp. Moving or changing these fields is not sufficient to bypass authentication on a correctly validating receiver.

**Offline evidence:** a CRC-correct, locally constructed signed HEARTBEAT fixture authenticated under a made-up 32-byte key. After passing it through the current methods:

```text
original:          signature matches = True
link mutation:     signature matches = False
timestamp mutation:signature matches = False
known key branch:  signature matches = False
```

No packets were sent. This demonstrates a mismatch between the tool's output and its signing-bypass claim; it does not test a permissive or faulty receiver implementation.

**Impact:** operators can transmit invalidly authenticated packets and mistake rejection for a timing or receiver issue. The document's claimed cross-link bypass is unsupported by this implementation.

**Recommendation:** withdraw the automatic bypass claim, distinguish authenticated mutations from unchanged captured-packet analysis, and verify signing behavior against independent reference fixtures before treating this mode as valid. A receiver configured to accept invalid signatures is a separate condition requiring separate evidence.

**Blast radius:** two CLI method call sites in one replay tool; the accompanying signing research and playbook rely on the claim. No tests covering either method were found in `tests/` or `tools/tests/`.

## 2. [P1] The ELRS analyzer reads packet type and SYNC identity from the wrong bits/bytes

**Location:** `tools/elrs-analyzer.py:156–181`. Introduced by `c1fa976`.

The analyzer calculates packet type as `(header >> 2) & 0x07`. The pinned ExpressLRS 4.1.0 `src/lib/OTA/OTA.h` defines the type in the low two bits for both standard and full-size packets. For a standard packet, the analyzer's selected bits are part of the CRC-high field.

The SYNC decoder then interprets bytes 1, 2 and 3 as three UID bytes. In the checked 4.1.0 structure these positions carry hopping index, nonce and RF-rate enum. The explicit UID fields occupy later bytes. The analyzer thus produces plausible-looking identity fragments from timing/rate state.

**Offline evidence:** a standard SYNC fixture built with the repository's source-compatible SYNC builder, using the made-up phrase `review-fixture`, produced:

```text
Low-two-bit packet type: 2 (SYNC)
Analyzer packet type:    3 (reported as telemetry)

For another nonce that entered the analyzer's SYNC branch:
Actual explicit UID bytes: 6f56
Analyzer UID fragment:     28:00:04
```

The simulator generates packets in the same incorrect layout expected by this decoder. A successful demonstration using those simulator packets is therefore not an independent protocol check. The analyzer also contains a different hopping algorithm from the pinned source; its reconstruction output needs independent review rather than reuse as a known-good reference.

**Impact:** real decoded ELRS packets can be misclassified, transmitters can be grouped by the wrong identifier, and phrase matches can be missed or falsely attributed. This affects file analysis even without an SDR.

**Recommendation:** make parsing explicitly version/packet-size aware, establish layout and CRC handling from pinned structures, and test against independent source-derived fixtures. Preserve partial-identity ambiguity rather than presenting timing fields as identity.

**Blast radius:** three `ElrsPacket(...)` construction sites: two simulator paths and the input-log path. The dashboard consumes the analyzer output. No direct analyzer tests were found in the two test directories.

## 3. [P1] Two COMMAND_LONG builders serialize application-order fields rather than wire-order fields

**Locations:** `tools/exploit_chains/mavlink_chain.py:391–395` and `tools/mavlink-signing-replay.py:591–596`. Introduced by `000e2fc` and `9cf1546`, respectively.

Both implementations serialize the target IDs, command and confirmation before the seven floats. That order resembles the application API, but it is not the transmitted payload layout. MAVLink applies [size-based field ordering](https://mavlink.io/en/guide/serialization.html#field-reordering); a COMMAND_LONG payload places its seven float parameters ahead of the smaller fields.

**Offline evidence:** the chain builder was called with target system 42, target component 1, command 512, and seven zero parameters. Decoding its bytes using wire field order returned:

```text
Requested: command=512 target_system=42 target_component=1
Decoded:   command=0   target_system=0  target_component=0 confirmation=0
```

This was an in-memory serialization check only. An outer packet CRC does not repair a semantically misordered payload.

**Impact:** advertised command tests do not deliver the command and target selected by the operator. Depending on parameter bits, the decoded command/target can differ unpredictably from the requested values; send counters do not establish execution.

**Recommendation:** use a reference/generated serializer, or independently validate the manually generated bytes against one. Keep this a protocol-conformance correction and verify it offline before any device test.

**Blast radius:** four calls to the chain's builder, covering dry-run and UDP/serial paths, plus the separate unsigned-command CLI path. The `pymavlink`-based injector/fuzzer use a different serialization path and are not implicated by this particular finding. No direct tests of these raw builders were found.

## 4. [P2] The passive interceptor's SDR/IQ path does not implement the advertised RF demodulation

**Locations:** `tools/passive-telemetry-interceptor.py:970–989` and `:1191–1198`. Introduced by `52196a7`.

All SDR/IQ input reaches `envelope_demod()`, which thresholds sample magnitudes and packs one threshold result per sample into bytes. It has no modulation-specific synchronization, symbol recovery, packet framing or LoRa/FSK decoding before passing the result into telemetry parsers.

Magnitude-only processing discards phase/frequency information: two sample sequences with the same magnitudes but different phases produce identical bytes. That makes it insufficient for the advertised ELRS/LoRa and frequency-modulated telemetry receive paths. Unencrypted payloads do not remove the physical-layer decoding requirement. CRSF serial framing also must not be treated as the ELRS over-the-air layout.

The function's internal comment acknowledges that real decoding is missing, but the CLI examples still present `--freq ... --protocol elrs` and raw-IQ input as operational telemetry capture. An empty result through this path is not evidence that the target sent no telemetry.

**Recommendation:** label these paths as unsupported/prototype and require already decoded bytes for validated parser use until a suitable decoder and independent recorded-signal fixtures exist. Expose the limitation in CLI output, not only in a source comment.

**Blast radius:** one call site in `run_capture()`, shared by SDR and IQ-file modes. The `--stdin-raw` byte-input path bypasses this specific stage. No independent RF/IQ decoder tests were found. No live RF test was attempted.

## 5. [P2] The ELRS SDR transmitter's waveform is a fixed chirp prototype

**Location:** `tools/elrs-tx-emulator.py:467–518`, called at `:463`. Introduced by `81eab5d`.

The SoapySDR backend fixes SF6 and bandwidth 812,500 Hz, groups raw packet bits directly into cyclic-shift symbols, and does not obtain a complete PHY configuration from the selected ELRS air-rate setting. This is a chirp-generation experiment, not a demonstrated SX1280-compatible ELRS encoder.

There is also a concrete timing mismatch from integer sample-rate conversion. At the default sample rate of 2.5 Msps, `int(rate / bandwidth)` gives three samples per chip, making a 64-chip symbol 76.8 microseconds. A 64-chip symbol at the stated bandwidth would last approximately 78.77 microseconds. No resampling reconciles the difference.

The reviewed encoder does not implement the complete radio packet coding/framing chain. Its custom two-downchirp “sync word” and direct payload grouping should not be taken as validation against a real SX1280 receiver. Independent SPI-backend correctness was not established by this observation.

**Impact:** a successful SoapySDR write can be counted as a sent ELRS packet even though receiver-decodable ELRS framing has not been established. Different CLI air rates do not supply the necessary PHY differences to this encoder.

**Recommendation:** explicitly mark this backend experimental/unsupported for interoperable packet transmission. Validate waveform generation offline against known-good radio fixtures before making any real-link claim. This review does not authorize RF testing of the connected E8.

**Blast radius:** one backend method call site shared by the emulator's SDR transmission modes. No waveform-conformance tests were found.

## 6. [P2] The signing analysis mistakes a clock tolerance for a general replay window

**Locations:** `tools/mavlink-signing-replay.py:451–459` and `docs/mavlink-signing-bypass.md:84–116`. Introduced by `9cf1546`.

The analyzer announces `REPLAY VIABLE` solely because a packet's timestamp is less than 60 seconds old. The document likewise claims acceptance of already received messages within 60 seconds and unrestricted acceptance on a new link ID.

The [signing specification](https://mavlink.io/en/guide/message_signing.html#accepting-signed-packets) separately requires valid authentication and stream timestamp checks; its one-minute tolerance does not eliminate per-stream anti-replay state. Changing link ID also invokes finding 1. Capture age alone cannot establish receiver acceptance, especially when the receiver has already accepted the original or a newer message.

**Impact:** the analysis can report a viable bypass for ordinary duplicate packets that a correctly validating receiver rejects. This is a separate defect from generating invalid signatures: even unchanged authentic packets cannot be judged replayable from wall-clock age alone.

**Recommendation:** report timestamp age as an observation and acceptance as unknown unless receiver state and policy are actually established. Rework the affected research claims around specific tested implementations rather than a universal bypass.

**Blast radius:** one analysis CLI path and its accompanying research claims. No receiver-state anti-replay fixture tests were found.

## 7. [P2] The passive interceptor silently drops every entry in the supplied lookup table

**Location:** `tools/passive-telemetry-interceptor.py:1102–1111`. Introduced by `52196a7`.

Its loader expects a `uid_3_5` field. The tracked JSON instead contains `uid_hex`, `uid_bytes` and `sync_bytes`. Because missing keys default to an empty string, the loader skips every row without raising an error.

**Offline reproduction:** importing the module and calling its loader on `tools/elrs-rainbow-table.json` returned zero entries; the JSON contains 723 entries. This is an integration failure even for a byte-decoded input stream, independent of the missing SDR demodulator.

**Recommendation:** consume a documented shared schema, normalize its key format against the lookup consumer, preserve collisions, and treat a nonempty file producing an empty index as a visible error. Add a fixture using the actual checked-in table.

**Blast radius:** one loader call site in `run_capture()`, used when `--rainbow-table` is selected. No test of this loader was found.

## 8. [P2] CI still excludes the root test suite repaired during review

**Locations:** `.github/workflows/validate.yml:23–24`, `tests/test_elrs_crypto.py:75`, `tests/test_rainbow_table.py:145`, and assertions in `tests/test_serial_probe.py`. CI introduced by `7b2a089`; root tests by `b10693d`.

CI runs only the stdlib tests in `tools/tests/`. The merged pytest tests under root `tests/` are neither installed nor run by that workflow. Running them locally at `2b3618a` returned **10 failures and 80 passes**, despite the CI-selected tests passing.

The failures were stale expectations: the removed `OTA_VERSION_ID` name, rejection of supported two-byte lookup, old CRSF addresses/frame identifiers, and acceptance of truncated device information. Claude repaired those tests in `3737029`, preserving the implementation corrections. The repair was reviewed and all **90 root tests now pass**. This failure is therefore resolved; CI omission remains open.

**Impact:** a green CI result still does not exercise the 90-test root suite and would not catch a recurrence of the inconsistency observed today. Neither suite currently covers the protocol failures identified above.

**Recommendation:** include the repaired root suite in an offline CI invocation. Clearly distinguish repository consistency checks, protocol conformance checks, and actual hardware validation.

**Blast radius:** 90 root tests are omitted from the four CI matrix combinations. The 33 stdlib tests are passing; their useful coverage should be retained.

## Validation performed

| Check | Result | Interpretation |
|---|---|---|
| `git pull --ff-only` | Already up to date | Review began on current local `2b3618a` |
| `python3 -B tools/validate-project.py` | PASS | Syntax, JSON, eleven artifact hashes/register entries, table consistency, CVE-index coverage and local links |
| `python3 -B -m unittest discover -s tools/tests -v` | 33 passed | Offline diagnostics/validation tests passed |
| `python3 -B -m pytest -q -p no:cacheprovider tests` | 80 passed, 10 failed | Root-suite expectations need reconciliation |
| Same root pytest command after `3737029` | 90 passed | Concurrent test repair verified; failures resolved |
| `bash -n tools/recon-wifi-drone.sh` | PASS | Shell syntax only; no scanner execution |
| Local signed HEARTBEAT fixture | Original authenticates; all three modified outputs fail | Finding 1 reproduced without a device |
| Local SYNC fixtures | Misclassification and wrong identity fields | Finding 2 reproduced against pinned struct layout |
| In-memory COMMAND_LONG decode | Command/target differ from requested values | Finding 3 reproduced without a socket |
| Actual-table passive loader check | 723 rows in, zero indexed | Finding 7 reproduced |

The small reproduction checks used made-up phrases/keys and imported modules without invoking their live entrypoints. No permanent test code or implementation fixes were added as part of this review.

## Historical context and improvements retained

- The original serial probe at `4f0d9e4` had incorrect CRSF identifiers, active-by-default querying and incomplete capture handling. The update repairs these with passive defaults, independent source checks, fragmented-frame tests, complete parameter chunk reads and private raw capture preservation. Root tests initially expected older values; `3737029` repaired those expectations without reverting the protocol fixes.
- Firmware artifact metadata now records explicit unknown provenance fields rather than inventing download/build identity. Eleven artifacts match their saved hashes.
- The CVE publication index has 97 entries and the audit distinguishes record publication from substantive applicability review. This is an evidence improvement, not a verification of every exploit claim.
- The connected-E8 prohibition and single-operator handoff rule are explicit in `AGENTS.md` and `research/E8-COLLABORATION.md`. The immutable vendor configuration reader defaults to dry-run and has three passing offline tests. It was not run live here.
- The latest characterization already qualifies Windows `usbser` incompatibility as a hypothesis and identifies the recorded `02 02` variant as overlapping a settings-write opcode. These qualifications are present at `E8-SERIAL-CHARACTERIZATION.md:19–30` and in the evening notes. They are **known qualified claims**, not newly established root causes or safe operations. Lower sections still use categorical wording; consolidating that wording would reduce confusion.

No removal of an established security fix was demonstrated in the focused paths. The principal regression pattern is that corrected shared assumptions were not consistently applied to newly added standalone tools. The observed stale-test problem was resolved during review.

## Coverage, risk and limits

**Strategy:** focused review of a medium-size changed-file set, with prioritization of authentication, packet formats, input parsing, transport boundaries and passive/live behavior. All 66 changed-file names and history were inventoried. The review did not audit every line of the roughly 32,000-line addition.

Detailed analysis covered the serial diagnostics/vendor-read helper, validation/CI, root and stdlib test interactions, ELRS analyzer/forger/emulator, MAVLink signing and raw-command paths, passive interceptor, dashboard integration points, and E8 evidence qualifications. Other chain/scanner/fuzzer tools and long research documents received selective inspection. The large generated phrase table was checked by validator/schema/count, not individually evaluated as a source of real-world default phrases.

Git blame was inspected for the principal signing, ELRS parsing, raw MAVLink command, passive table-loader, waveform and CI call sites. Source-only call counts above describe direct invocation locations; they are not measured runtime coverage or a complete transitive call graph. The affected standalone paths have low direct caller counts, but their output feeds operator decisions and the game-day playbook.

No coverage percentage is claimed. No external firmware authenticity verification, hardware fault reproduction, flight behavior check, USB trace comparison, RF conformance test or full CVE applicability audit was performed.

**Confidence:** high for the directly reproduced serialization/schema/signature/test findings; high for identifying absent modulation-specific processing in the inspected SDR paths; no claim about exploitability of the connected controller.

## Suggested next changes

1. Include the repaired root tests in CI, preserving the independently checked serial fixes.
2. Correct or withdraw the false signing-bypass/replay conclusions and the misserialized raw command paths.
3. Correct ELRS decoding against pinned source fixtures and reuse an explicit version/schema contract across tools.
4. Mark unsupported SDR PHY paths visibly as prototypes until offline reference-signal validation exists.
5. Retain E8 read-only/no-flashing/single-operator constraints; consolidate categorical driver/hardware statements with their existing qualifications.

This report is a review artifact only. It does not authorize device operations, and it does not claim the code changes above have already been made.
