# Test Quarantine Gaps

Tests quarantined because they relied on synthetic/fabricated data instead of
real hardware captures. Each entry names the real capture that would let the
test run again.

## Available real captures (for reference)

| File | Contents |
|------|----------|
| `captures/e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq` | Oct 15 passive IQ capture (34 PHY candidates) |
| `captures/tinyhawk_rc_pair_20261016_retry_2441MHz_4Msps_sc16.iq` | Oct 16 IQ capture with stick motion (28 PHY candidates) |
| `captures/oct15-ranked-2026-10-16.jsonl` | Ranked symbol bins from Oct 15 |
| `captures/tinyhawk-rc-retry-full-phy-2026-10-16.jsonl` | Ranked symbol bins from Oct 16 |
| `captures/tinyhawk_rc_pair_20261016_retry_msp_rc.jsonl` | 114 concurrent MSP RC-channel USB samples |
| `captures/decoded-v3-summary.jsonl` | 54 CRC-valid decoded frames (raw bytes/UID omitted) |

## ELRS v3 protocol — needs real timing/injection captures

| File | What it tests | Capture needed |
|------|--------------|----------------|
| `test_elrs_v3_fresh_sync_clock.py` | Fresh-SYNC clock scheduling | Real SYNC timing data with timestamps |
| `test_elrs_v3_canary_schedule.py` | Canary frame scheduling | Real canary frame captures |
| `test_elrs_v3_hop_timing_check.py` | Hop timing validation | Real hop timing observations from IQ |
| `test_elrs_v3_rc_replay_canary.py` | RC decode, safety gates, CRC verification | Raw RC frame bytes with CRC + aux safety evidence |
| `test_elrs_v3_rate_switch_latch_model.py` | Rate-switch state machine | Real rate-switch event observations |
| `test_elrs_v3_bounded_rc_injection.py` | Bounded RC injection packet construction | Raw RC frame bytes for injection validation |

## ELRS decode pipeline — needs real IQ or symbol data

| File | What it tests | Capture needed |
|------|--------------|----------------|
| `test_elrs_offline.py` | IQ intake, file format detection, symbol extraction | Real IQ file with known preamble positions |
| `test_elrs_phy_scan.py` | PHY energy detection, scan_chunk, scan_file | Real IQ with known LoRa frame positions |
| `test_elrs_batch_evidence.py` | Batch symbol analysis, output guards | Real symbol analysis output files |
| `test_elrs_sync_channel_record.py` | SDR channel recording | Real SDR capture session data |

## ELRS passive pipeline — remaining quarantined classes

`test_elrs_passive_pipeline.py` — 2 classes still quarantined, 16 now active:

| Class | Capture needed |
|-------|---------------|
| TestFHSSCorrelation | Real FHSS channel observation captures with known hop sequence |
| TestPipelineResult | Real pipeline output data |

## ELRS attack/detection — needs real attack scenario captures

| File | What it tests | Capture needed |
|------|--------------|----------------|
| `test_elrs_anomaly_detector.py` | Anomaly detection against traffic | Real traffic capture with known anomalies |
| `test_elrs_attack_profile.py` | Attack profile generation | Real attack profile data from live test |
| `test_elrs_chain_target_mapping.py` | Chain takeover channel mapping | Real chain takeover capture data |

## Mixed files — remaining quarantined methods

| File | Quarantined methods | Capture needed |
|------|-------------------|----------------|
| `test_elrs_li_offline.py` | `test_invalid_symbol_rejected`, `test_provided_uid_requires_sync_identity_fragment` | Real symbol vectors, real SYNC frames with full UID |
| `test_elrs_li_forward.py` | `test_msp_request_matches_betaflight_fixture`, `test_api_version_canary_is_crc_valid_and_decodable`, `test_waveform_recovers_encoded_symbols`, `test_rejects_bad_lengths_and_indices` | Real Betaflight MSP capture, real API version frame, real IQ waveform, real malformed frame captures |

## MSP / serial protocol — needs real MSP serial dumps

| File | What it tests | Capture needed |
|------|--------------|----------------|
| `test_msp_vuln_scanner.py` | MSP vulnerability scanning | Real MSP characterization dump from FC |
| `test_msp_injection_sim.py` | MSP/CRSF/ELRS frame injection | Real MSP/CRSF serial captures |
| `test_msp_elrs_hijack_poc.py` | ELRS connection state machine | Real ELRS connection state captures |
| `test_msp_elrs_attack_chains.py` | Attack chain execution | Real attack chain execution data |
| `test_tinyhawk_msp_rc_observe.py` | MSP RC channel observation | Real MSP serial port capture dump |

## Other protocols — needs real protocol captures

| File | What it tests | Capture needed |
|------|--------------|----------------|
| `test_combined_attack_demo.py` | Multi-stage attack pipeline | Real multi-stage attack capture |
| `test_mavlink_signing_enforcer.py` | MAVLink v2 signing enforcement | Real MAVLink v2 signed packet captures |
| `test_receiver_acceptance.py` | Receiver acceptance criteria | Real receiver acceptance test data |
| `test_sitl_chain2.py` | ArduPilot SITL chain2 | Real SITL session capture or live hardware |
| `test_bench_controller.py` | Bench controller safety | Real bench controller session data |
| `test_evil_twin_detector.py` | WiFi evil twin detection | Real WiFi scan data with evil twin scenarios |

## Tests NOT quarantined

### Reworked onto real capture data
- `test_elrs_core_validation.py` — all 9 classes: CRC14, FHSS, nonce, SYNC structure, bind mode (capture-derived UID)
- `test_elrs_v3_frame_check.py` — CRC table semantics (capture-derived UID)
- `test_elrs_v3_fhss_check.py` — FHSS sequence generation, channel matching (capture-derived UID)
- `test_elrs_passive_pipeline.py` — 14 classes reworked: Gray coding, Hamming FEC, whitening, nibble packing, encode/decode round-trip, CRC validation, frame decode, sanitization, edge cases, JSONL loading, UID parsing
- `test_capture_forge_validate.py` — PhraseToUidTests (ELRS spec constant) + ForgeValidationTests (real decoded frames)
- `test_elrs_li_offline.py` — `test_ota_analog_unpacking` (real RC frame bytes + channels)
- `test_elrs_li_forward.py` — `test_real_frame_round_trip` (real SYNC frame bytes)

### Real-data tests (kept as-is)
- `test_sx1280_capture_decode.py` — reads real ranked JSONL captures
- `test_elrs_rc_overlap.py` — reads real decoded-v3-summary.jsonl + MSP RC JSONL
- `test_elrs_li_offline.py` — TX16S WaveShark vectors + real captures (2 methods kept)
- `test_elrs_li_forward.py` — TX16S WaveShark vectors (1 method kept)
- `test_elrs_passive_pipeline.py` — TestPipelineImports + TestCaptureValidatedPipeline
- `test_capture_forge_validate.py` — IntegrationWithCapturesTests + RainbowLookupTests

### Infrastructure tests (no RF/protocol data — kept as-is)
- `test_web_dashboard.py` — Flask dashboard safeguards
- `test_app_lifecycle.py` — Flask app lifecycle
- `test_app_factory.py` — App/state factory isolation
- `test_api_routes.py` — API route smoke tests
- `test_dashboard_browser.py` — Playwright browser tests
- `test_state_histories.py` — Dashboard state history concurrency
- `test_uhd_elrs_sync_rx_source.py` — Source code safety check (no transmit API)
- `test_elrs_sync_capture_quarantine.py` — Legacy code quarantine guard
