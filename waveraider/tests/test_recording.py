"""Recorder data integrity, quotas, interruption and timestamp discontinuities."""
import ctypes as C
import hashlib
import json

import pytest

from waveraider.recording import Metadata, SimulatedSource, metadata_format, record
from waveraider.recording_lock import directory_lock


def settings():
    return dict(node_id='sensor-a', frequency=2444500000, rate=160000,
                seconds=.1, label='known background', simulated=True)


def test_recording_hashes_format_and_no_false_timing(tmp_path):
    folder, summary = record(tmp_path, **settings())
    raw = (folder / 'iq.sigmf-data').read_bytes()
    meta = json.loads((folder / 'iq.sigmf-meta').read_text())
    assert len(raw) == 16000 * 4
    assert hashlib.sha256(raw).hexdigest() == summary['sha256']
    assert hashlib.sha512(raw).hexdigest() == meta['global']['core:sha512']
    assert meta['global']['core:datatype'] == 'ci16_le'
    assert meta['global']['wr:clock_domain'] == 'synthetic'
    assert summary['sample_timing_validated'] is False
    assert summary['localization_eligible'] is False
    assert 'core:datetime' not in meta['captures'][0]
    assert not folder.name.endswith('.partial')


def test_discontinuity_preserves_counters_and_flags(tmp_path):
    class Gapped(SimulatedSource):
        def read(self, count):
            raw, timestamp, flags = super().read(count)
            return raw, timestamp + (7 if timestamp else 0), (1 if timestamp else 0)
    opts = {**settings(), 'seconds': 1}
    folder, summary = record(tmp_path, **opts, source_factory=lambda: Gapped(opts['rate']))
    assert summary['discontinuities'] == 1
    assert summary['overruns'] == 2
    meta = json.loads((folder / 'iq.sigmf-meta').read_text())
    assert meta['captures'][1]['wr:sample_counter'] == 65536 + 7
    assert meta['captures'][1]['core:sample_start'] == 65536


def test_failure_keeps_partial_and_closes_source(tmp_path):
    closed = []
    class Failing(SimulatedSource):
        def read(self, count):
            raise OSError('receiver disconnected')
        def close(self):
            closed.append(True)
    with pytest.raises(OSError):
        record(tmp_path, **settings(), source_factory=lambda: Failing(160000))
    assert closed == [True]
    assert len(list(tmp_path.glob('*.partial'))) == 1
    assert not list(tmp_path.glob('*/iq.sigmf-meta'))


def test_quota_refuses_without_opening_radio(tmp_path):
    def forbidden():
        pytest.fail('opened source before checking storage')
    with pytest.raises(OSError, match='quota'):
        record(tmp_path, **settings(), storage_mib=1, source_factory=forbidden)


@pytest.mark.parametrize('changes', [{'seconds': float('nan')}, {'seconds': 31},
    {'rate': 100000000}, {'frequency': 1}, {'node_id': '../evil'}, {'label': ''},
    {'rate': 20000000, 'seconds': 30}])
def test_invalid_requests_do_not_open_hardware(tmp_path, changes):
    with pytest.raises(ValueError):
        record(tmp_path, **{**settings(), **changes})
    assert not list(tmp_path.glob('*.partial'))


def test_no_concurrent_writers_in_one_output_folder(tmp_path):
    with directory_lock(tmp_path):
        with pytest.raises(OSError, match='another recorder'):
            record(tmp_path, **settings())


def test_metadata_abi_layout_and_new_enum_resolution():
    assert C.sizeof(Metadata) == 56
    assert Metadata.timestamp.offset == 0
    assert Metadata.actual_count.offset == 16
    class Fn:
        def __call__(self, value):
            return b'BLADERF_FORMAT_SC16_Q11_META' if value == 2 else b'other'
    class Lib:
        bladerf_format_to_string = Fn()
    assert metadata_format(Lib()) == 2


def test_legacy_metadata_enum_is_explicitly_version_gated():
    from waveraider.recording import Version
    class Fn:
        def __call__(self, ptr):
            version = C.cast(ptr, C.POINTER(Version)).contents
            version.major, version.minor = 2, 4
    class Lib:
        bladerf_version = Fn()
    assert metadata_format(Lib()) == 1


def test_bladerf_micro_uses_metadata_rx_and_closes_on_error(monkeypatch):
    from waveraider.recording import BladeRFSource
    calls = []
    fail = [False]
    class Fn:
        def __init__(self, name):
            self.name = name
        def __call__(self, *args):
            calls.append((self.name, args))
            if self.name == 'bladerf_open':
                C.cast(args[0], C.POINTER(C.c_void_p))[0] = C.c_void_p(123)
            elif self.name == 'bladerf_get_board_name':
                return b'bladerf2'
            elif self.name == 'bladerf_is_fpga_configured':
                return 1
            elif self.name in ('bladerf_set_sample_rate', 'bladerf_set_bandwidth'):
                C.cast(args[3], C.POINTER(C.c_uint))[0] = args[2]
            elif self.name == 'bladerf_format_to_string':
                return b'BLADERF_FORMAT_SC16_Q11_META' if args[0] == 2 else b'other'
            elif self.name == 'bladerf_sync_rx':
                if fail[0]:
                    return -5
                meta = C.cast(args[3], C.POINTER(Metadata)).contents
                meta.timestamp = 123456789
                meta.actual_count = args[2]
                meta.status = 0
            return 0
    class Lib:
        def __getattr__(self, name):
            fn = Fn(name)
            setattr(self, name, fn)
            return fn
    monkeypatch.setattr('waveraider.bladerf_sweep._libpath', lambda: 'fake')
    monkeypatch.setattr('waveraider.recording.C.CDLL', lambda path: Lib())
    source = BladeRFSource('a' * 32, 2444500000, 160000, 30)
    raw, counter, flags = source.read(1024)
    assert len(raw) == 4096 and counter == 123456789
    assert [args[2] for name, args in calls if name == 'bladerf_sync_config'] == [2]
    assert all(args[1] == 0 for name, args in calls if name == 'bladerf_enable_module')
    source.close()
    assert calls[-1][0] == 'bladerf_close'
    fail[0] = True
    with pytest.raises(OSError, match='RX'):
        BladeRFSource('a' * 32, 2444500000, 160000, 30)
    assert calls[-1][0] == 'bladerf_close'
    assert not any('tx' in name.lower() for name, _ in calls)
