"""DroneID / OFDM burst detection, verified against synthetic OFDM (no radio)."""
import numpy as np
import pytest

from waveraider import droneid


def _ofdm(nsym, fft_size=droneid.FFT_SIZE, cp_len=droneid.CP_LEN, seed=0):
    """Synthetic OFDM: QPSK subcarriers -> IFFT -> prepend a cyclic prefix."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(nsym):
        sc = (rng.integers(0, 2, fft_size) * 2 - 1) + 1j * (rng.integers(0, 2, fft_size) * 2 - 1)
        t = np.fft.ifft(sc)
        out.append(np.concatenate([t[-cp_len:], t]))       # CP = copy of the tail
    return np.concatenate(out).astype(np.complex64)


def _noise(n, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)


def test_ofdm_metric_high_for_ofdm_low_for_noise():
    ofdm = _ofdm(9)                                        # a 9-symbol burst
    assert droneid.ofdm_metric(ofdm) > 0.7                 # CP autocorrelation fires
    assert droneid.ofdm_metric(_noise(ofdm.size)) < 0.4    # noise stays low


def test_detect_flags_ofdm_burst():
    r = droneid.detect(_ofdm(9), droneid.SAMPLE_RATE)
    assert r["detected"] is True and r["metric"] >= droneid.DETECT_THRESHOLD
    assert "DroneID" in r["note"]


def test_detect_rejects_noise():
    r = droneid.detect(_noise(20_000), droneid.SAMPLE_RATE)
    assert r["detected"] is False and r["bursts"] == []


def test_scan_finds_a_burst_embedded_in_noise():
    # a DroneID-like burst sitting inside a longer noise buffer
    burst = _ofdm(9)
    buf = np.concatenate([_noise(8000, seed=1), burst, _noise(8000, seed=2)]).astype(np.complex64)
    bursts = droneid.scan_for_bursts(buf, droneid.SAMPLE_RATE)
    assert len(bursts) >= 1
    assert bursts[0]["peak_metric"] > droneid.DETECT_THRESHOLD


def test_too_short_is_graceful():
    assert droneid.ofdm_metric(_noise(100)) == 0.0
    assert droneid.detect(_noise(100))["detected"] is False


def _droneid_burst(seed=0):
    """Synthetic DroneID-like burst: 9 OFDM symbols, ZC pilots at symbols 4 & 6."""
    syms = []
    for k in range(9):
        if k == 3:
            syms.append(droneid.zc_symbol_time(droneid.ZC_ROOTS[0]))
        elif k == 5:
            syms.append(droneid.zc_symbol_time(droneid.ZC_ROOTS[1]))
        else:
            syms.append(_ofdm(1, seed=seed + k))
    return np.concatenate(syms).astype(np.complex64)


def test_droneid_zc_pilots_form_candidate():
    r = droneid.detect(_droneid_burst(), droneid.SAMPLE_RATE)
    assert r["detected"] and r["droneid"] and r["zc_peak"] >= droneid.ZC_THRESHOLD
    assert "DroneID candidate" in r["note"]


def test_generic_ofdm_is_not_flagged_as_droneid():
    r = droneid.detect(_ofdm(9), droneid.SAMPLE_RATE)   # OFDM but no ZC pilots
    assert r["detected"] and not r["droneid"] and r["zc_peak"] < droneid.ZC_THRESHOLD


def _reference_pilot(root):
    """Independent frequency-grid fixture: 600 occupied bins, nulled DC."""
    n = np.arange(601)
    sequence = np.exp(-1j * np.pi * root * n * (n + 1) / 601)
    bins = np.zeros(1024, dtype=complex)
    bins[-300:] = sequence[:300]
    bins[1:301] = sequence[301:]
    symbol = np.fft.ifft(bins)
    return np.concatenate((symbol[-72:], symbol))


def test_reference_mapping_has_null_dc():
    actual = droneid.zc_symbol_time(600)
    np.testing.assert_allclose(actual, _reference_pilot(600), atol=1e-12)
    assert abs(np.fft.fft(actual[72:])[0]) < 1e-12


def test_independent_pair_with_noise_phase_and_gain():
    burst = np.concatenate((_ofdm(3), _reference_pilot(600), _ofdm(1),
                            _reference_pilot(147), _ofdm(3)))
    burst = 0.7 * np.exp(0.8j) * burst + 0.001 * _noise(len(burst), seed=42)
    result = droneid.detect(np.concatenate((0.001 * _noise(1500), burst)))
    assert result['classification'] == 'droneid_candidate'
    assert result['payload_validated'] is False
    assert abs(result['pilot_pair']['spacing_samples'] - 2192) <= 4


def test_single_pilot_does_not_identify_droneid():
    for root in (600, 147):
        result = droneid.detect(np.concatenate((_ofdm(3), _reference_pilot(root), _ofdm(3))))
        assert result['zc_peak'] > 0.9
        assert not result['droneid']
        assert result['pilot_pair'] is None


def test_reversed_or_wrongly_spaced_pilots_are_rejected():
    for first, second, middle in [(147, 600, 1), (600, 147, 0), (600, 147, 3)]:
        pieces = [_ofdm(3), _reference_pilot(first)]
        if middle:
            pieces.append(_ofdm(middle))
        pieces.extend([_reference_pilot(second), _ofdm(3)])
        assert not droneid.detect(np.concatenate(pieces))['droneid']


def test_constant_carrier_is_not_droneid():
    result = droneid.detect(np.ones(20000, dtype=complex))
    assert not result['droneid']


def test_rate_and_invalid_samples_are_rejected():
    import pytest
    for rate in (0, float('nan'), 20_000_000):
        with pytest.raises(ValueError, match='samples/second'):
            droneid.detect(_ofdm(9), rate)
    for samples in (np.zeros((2, 5000)), [complex(float('nan'), 0)], [float('inf')]):
        with pytest.raises(ValueError, match='one-dimensional'):
            droneid.detect(samples)


def _shift(iq, hz, fs=droneid.SAMPLE_RATE):
    """Apply a carrier frequency offset, as a drone/receiver crystal mismatch does."""
    n = np.arange(len(iq))
    return (np.asarray(iq) * np.exp(2j * np.pi * hz * n / fs)).astype(np.complex64)


def _burst_in_noise(snr_db, offset_hz=0.0, seed=7):
    """Unit-power DroneID-like burst inside complex noise at the given burst SNR."""
    burst = _droneid_burst(seed=seed)
    burst = burst / np.sqrt(np.mean(np.abs(burst) ** 2))
    burst = np.concatenate((np.zeros(20_000), burst, np.zeros(20_000)))
    sigma = 10 ** (-snr_db / 20)
    noise = sigma * _noise(len(burst), seed=seed + 100) / np.sqrt(2)
    return _shift(burst + noise, offset_hz)


def test_zc_metric_matches_direct_normalized_correlation():
    # Pins the metric values so a faster implementation cannot change them.
    x = _burst_in_noise(3.0)[:30_000]
    ref = droneid.zc_symbol_time(600)
    corr = np.abs(np.correlate(x, ref, mode="valid"))
    energy = np.array([np.sum(np.abs(x[i:i + ref.size]) ** 2) for i in range(0, x.size - ref.size + 1)])
    expected = np.clip(corr / (np.sqrt(energy * np.sum(np.abs(ref) ** 2)) + 1e-12), 0, 1)
    np.testing.assert_allclose(droneid._zc_metric(x, 600), expected, atol=2e-4)


@pytest.mark.parametrize("offset_hz", [12_000.0, -21_000.0])
def test_candidate_survives_carrier_frequency_offset(offset_hz):
    result = droneid.detect(_burst_in_noise(12.0, offset_hz))
    assert result["classification"] == "droneid_candidate"


def test_reports_the_frequency_offset_it_corrected():
    result = droneid.detect(_burst_in_noise(12.0, 12_000.0))
    assert abs(result["cfo_hz"] - 12_000.0) <= 3_750


def test_weak_burst_below_the_ofdm_gate_is_still_a_candidate():
    result = droneid.detect(_burst_in_noise(-6.0))
    assert result["metric"] < droneid.DETECT_THRESHOLD      # the CP gate alone would miss it
    assert result["classification"] == "droneid_candidate"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_long_noise_never_forms_a_candidate(seed):
    assert not droneid.detect(_noise(1_000_000, seed=seed))["droneid"]


@pytest.mark.parametrize("offset_hz", [0.0, 7_500.0, -15_000.0, 22_500.0])
def test_offset_generic_ofdm_is_never_droneid(offset_hz):
    burst = np.concatenate((_noise(5000, seed=9) * 0.01, _ofdm(9, seed=4), _noise(5000, seed=10) * 0.01))
    assert not droneid.detect(_shift(burst, offset_hz))["droneid"]


@pytest.mark.parametrize('offset_hz', [-22500, -18750, 18750, 22500])
def test_offset_grid_edges_and_midpoints(offset_hz):
    result = droneid.detect(_burst_in_noise(12, offset_hz))
    assert result['droneid']
    assert abs(result['cfo_hz'] - offset_hz) <= 3750


@pytest.mark.parametrize('delta', [-1200, -1, 0, 1, 1200])
def test_pair_survives_processing_boundary(delta):
    burst = _droneid_burst(seed=4)
    start = droneid._BLOCK - 4 * (droneid.FFT_SIZE + droneid.CP_LEN) + delta
    x = np.zeros(droneid._BLOCK + 20000, dtype=np.complex64)
    x[start:start + len(burst)] = burst
    x = _shift(x, -15000)
    score, pair, offset, peak = droneid._pilot_search(x, droneid.SAMPLE_RATE)
    reference = droneid._pilot_search_block(x, droneid.SAMPLE_RATE)
    assert score == pytest.approx(reference[0], abs=2e-5)
    assert pair == reference[1]
    assert offset == reference[2]
    assert peak == pytest.approx(reference[3], abs=2e-5)
    assert score >= droneid.ZC_THRESHOLD


def test_blockwise_cp_matches_direct_metric_across_boundary():
    x = _noise(droneid._BLOCK + 3000, seed=58).astype(np.complex64)
    n, cp = droneid.FFT_SIZE, droneid.CP_LEN
    prod = x[:-n] * np.conj(x[n:])
    energy = np.abs(x[:-n]) ** 2 + np.abs(x[n:]) ** 2
    expected = np.abs(np.convolve(prod, np.ones(cp), 'valid')) / (0.5 * np.convolve(energy, np.ones(cp), 'valid') + 1e-12)
    np.testing.assert_allclose(droneid.cp_metric_array(x), expected, atol=1e-7)


def test_full_capture_noise_has_no_pilot_pair():
    from waveraider.spectrum import DRONEID_WINDOW_SAMPLES
    x = _noise(DRONEID_WINDOW_SAMPLES, seed=20260925).astype(np.complex64)
    result = droneid.detect(x)
    assert not result['droneid']
    assert result['pilot_pair'] is None
    assert result['cfo_hz'] is None
