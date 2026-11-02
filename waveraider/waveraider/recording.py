"""Opt-in bounded receive-only IQ recordings with explicit unsynchronized timing."""
from __future__ import annotations

import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import time
import uuid

BLOCK_SAMPLES = 65536
MAX_BYTES = 512 * 1024 * 1024


class Metadata(C.Structure):
    _fields_ = [('timestamp', C.c_uint64), ('flags', C.c_uint32), ('status', C.c_uint32),
                ('actual_count', C.c_uint), ('reserved', C.c_uint8 * 32)]


class Version(C.Structure):
    _fields_ = [('major', C.c_uint16), ('minor', C.c_uint16), ('patch', C.c_uint16),
                ('describe', C.c_char_p)]


def metadata_format(lib):
    # 2.6 added a packed format ahead of META. Do not assume its enum is unchanged.
    if hasattr(lib, 'bladerf_format_to_string'):
        lib.bladerf_format_to_string.argtypes = [C.c_int]
        lib.bladerf_format_to_string.restype = C.c_char_p
        for value in range(8):
            if lib.bladerf_format_to_string(value) == b'BLADERF_FORMAT_SC16_Q11_META':
                return value
        raise OSError('library has no supported SC16 metadata format')
    version = Version()
    lib.bladerf_version.argtypes = [C.POINTER(Version)]
    lib.bladerf_version.restype = None
    lib.bladerf_version(C.byref(version))
    if version.major == 2 and version.minor <= 5:
        return 1
    raise OSError('unsupported libbladeRF metadata ABI')


class BladeRFSource:
    """Standalone bladeRF micro RX0 stream. No transmit or flash operations."""
    def __init__(self, serial, frequency, rate, gain):
        if not re.fullmatch('[0-9a-fA-F]{32}', serial):
            raise ValueError('specify the exact 32-character bladeRF serial number')
        from .bladerf_sweep import _libpath
        path = _libpath()
        if not path:
            raise OSError('libbladeRF is not installed')
        self.lib = C.CDLL(path)
        signatures = {
            'bladerf_open': ([C.POINTER(C.c_void_p), C.c_char_p], C.c_int),
            'bladerf_close': ([C.c_void_p], None),
            'bladerf_is_fpga_configured': ([C.c_void_p], C.c_int),
            'bladerf_get_board_name': ([C.c_void_p], C.c_char_p),
            'bladerf_set_frequency': ([C.c_void_p, C.c_int, C.c_uint64], C.c_int),
            'bladerf_set_sample_rate': ([C.c_void_p, C.c_int, C.c_uint, C.POINTER(C.c_uint)], C.c_int),
            'bladerf_set_bandwidth': ([C.c_void_p, C.c_int, C.c_uint, C.POINTER(C.c_uint)], C.c_int),
            'bladerf_set_gain_mode': ([C.c_void_p, C.c_int, C.c_int], C.c_int),
            'bladerf_set_gain': ([C.c_void_p, C.c_int, C.c_int], C.c_int),
            'bladerf_sync_config': ([C.c_void_p, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_uint, C.c_uint], C.c_int),
            'bladerf_enable_module': ([C.c_void_p, C.c_int, C.c_bool], C.c_int),
            'bladerf_sync_rx': ([C.c_void_p, C.c_void_p, C.c_uint, C.POINTER(Metadata), C.c_uint], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        self.dev = C.c_void_p()
        self.rate = rate
        self.info = {}
        try:
            self.check(self.lib.bladerf_open(C.byref(self.dev), ('*:serial=' + serial).encode()), 'open (stop other radio users first)')
            if self.lib.bladerf_get_board_name(self.dev) != b'bladerf2':
                raise OSError('this recorder currently supports bladeRF micro only')
            if self.lib.bladerf_is_fpga_configured(self.dev) != 1:
                raise OSError('bladeRF FPGA is not configured; install the matching image first')
            actual = C.c_uint()
            self.check(self.lib.bladerf_set_sample_rate(self.dev, 0, rate, C.byref(actual)), 'sample rate')
            self.rate = actual.value
            if self.rate != rate:
                raise OSError('radio did not accept the requested sample rate exactly')
            self.check(self.lib.bladerf_set_bandwidth(self.dev, 0, int(rate * .8), C.byref(actual)), 'bandwidth')
            self.info = {'model': 'bladeRF micro', 'serial': serial, 'channel': 'RX0',
                         'gain_db': gain, 'bandwidth_hz': actual.value}
            self.check(self.lib.bladerf_set_frequency(self.dev, 0, frequency), 'frequency')
            self.check(self.lib.bladerf_set_gain_mode(self.dev, 0, 1), 'manual gain')
            self.check(self.lib.bladerf_set_gain(self.dev, 0, gain), 'gain')
            self.check(self.lib.bladerf_sync_config(self.dev, 0, metadata_format(self.lib), 16, BLOCK_SAMPLES, 8, 5000), 'metadata stream')
            self.check(self.lib.bladerf_enable_module(self.dev, 0, True), 'RX enable')
            self.read(BLOCK_SAMPLES)  # explicitly discard settling samples
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(result, operation):
        if result != 0:
            raise OSError(f'bladeRF {operation} failed ({result})')

    def read(self, count):
        import numpy as np
        data = (C.c_int16 * (2 * count))()
        meta = Metadata()
        meta.flags = 1 << 31  # BLADERF_META_FLAG_RX_NOW
        self.check(self.lib.bladerf_sync_rx(self.dev, data, count, C.byref(meta), 5000), 'RX')
        if not 0 < meta.actual_count <= count:
            raise OSError('radio returned an invalid sample count')
        raw = np.frombuffer(data, dtype=np.int16, count=2 * meta.actual_count).astype('<i2', copy=False).tobytes()
        return raw, int(meta.timestamp), int(meta.status)

    def close(self):
        if self.dev:
            try:
                self.lib.bladerf_enable_module(self.dev, 0, False)
            finally:
                self.lib.bladerf_close(self.dev)
                self.dev = C.c_void_p()


class SimulatedSource:
    def __init__(self, rate):
        import numpy as np
        self.rng = np.random.default_rng(42)
        self.rate, self.counter = rate, 0
        self.info = {'model': 'synthetic noise', 'seed': 42}

    def read(self, count):
        raw = self.rng.integers(-256, 257, count * 2, dtype='<i2').tobytes()
        start = self.counter
        self.counter += count
        return raw, start, 0

    def close(self):
        pass


def record(output, *, node_id, frequency, rate, seconds, label, simulated=False,
           serial=None, gain=30, storage_mib=2048, source_factory=None):
    """Create a unique recording directory; partial failures never become complete data."""
    from .node_reporting import NODE_ID
    if not NODE_ID.fullmatch(node_id) or not isinstance(label, str) or not 1 <= len(label.strip()) <= 240:
        raise ValueError('supply a node ID and a 1–240 character operator label')
    if type(frequency) is not int or not 70_000_000 <= frequency <= 6_000_000_000:
        raise ValueError('frequency must be an integer Hz value from 70 MHz to 6 GHz')
    if type(rate) is not int or not 160_000 <= rate <= 20_000_000:
        raise ValueError('sample rate must be 160000–20000000 Hz')
    if not math.isfinite(seconds) or not 0 < seconds <= 30:
        raise ValueError('duration must be more than zero and at most 30 seconds')
    total = int(rate * seconds)
    if not 0 < total * 4 <= MAX_BYTES:
        raise ValueError('recording must fit the 512 MiB per-capture limit; shorten duration')
    if type(storage_mib) is not int or not 1 <= storage_mib <= 102400:
        raise ValueError('storage quota must be 1–102400 MiB')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Serialize recorders sharing an output folder; never race the quota check.
    from .recording_lock import directory_lock
    with directory_lock(output):
        used = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        required = total * 4 + 4 * 1024 * 1024
        if used + required > storage_mib * 1024 * 1024 or shutil.disk_usage(output).free < required:
            raise OSError('insufficient recording quota or free disk space; move recordings before retrying')
        capture_id = str(uuid.uuid4())
        folder = output / (capture_id + '.partial')
        folder.mkdir(mode=0o700)
        source = None
        captures, written, expected, discontinuities, overruns = [], 0, None, 0, 0
        sha256, sha512 = hashlib.sha256(), hashlib.sha512()
        started = datetime.now(timezone.utc).isoformat()
        monotonic_start = time.monotonic()
        try:
            source = source_factory() if source_factory else (SimulatedSource(rate) if simulated else BladeRFSource(serial or '', frequency, rate, gain))
            if source.rate != rate:
                raise OSError('source sample rate differs from recording metadata')
            with (folder / 'iq.sigmf-data').open('xb') as stream:
                while written < total:
                    if len(captures) >= 8192 or time.monotonic() - monotonic_start > seconds * 3 + 10:
                        raise OSError('capture exceeded its block/time budget; retained as partial')
                    count = min(BLOCK_SAMPLES, total - written)
                    before = time.monotonic()
                    raw, counter, flags = source.read(count)
                    after = time.monotonic()
                    samples = len(raw) // 4
                    if len(raw) % 4 or not 0 < samples <= count or not 0 <= counter < 2**64:
                        raise OSError('invalid IQ block')
                    gap = expected is not None and counter != expected
                    discontinuities += int(gap)
                    overruns += int(bool(flags & 1))
                    captures.append({'core:sample_start': written, 'core:frequency': frequency,
                        'wr:sample_counter': counter, 'wr:status_flags': flags,
                        'wr:discontinuity': gap, 'wr:read_monotonic_start': before,
                        'wr:read_monotonic_end': after})
                    stream.write(raw)
                    sha256.update(raw)
                    sha512.update(raw)
                    written += samples
                    expected = counter + samples
                stream.flush()
                import os
                os.fsync(stream.fileno())
            source.close()
            info = source.info
            source = None
            summary = {'capture_id': capture_id, 'node_id': node_id, 'operator_label': label,
                       'simulated': simulated, 'frequency_hz': frequency, 'sample_rate_hz': rate,
                       'sample_count': written, 'bytes': written * 4, 'sha256': sha256.hexdigest(),
                       'host_started_at': started, 'elapsed_s': time.monotonic() - monotonic_start,
                       'discontinuities': discontinuities, 'overruns': overruns,
                       'sample_timing_validated': False, 'localization_eligible': False}
            metadata = {'global': {'core:datatype': 'ci16_le', 'core:sample_rate': rate,
                'core:version': '1.2.0', 'core:recorder': 'WaveRaider', 'core:sha512': sha512.hexdigest(),
                'core:description': label, 'core:extensions': [{'name': 'wr', 'version': '1.0.0', 'optional': True}],
                'wr:summary': summary, 'wr:receiver': info, 'wr:q11_scale': 1 / 2048,
                'wr:clock_domain': 'synthetic' if simulated else 'free_running_device_counter',
                'wr:timing_note': 'Host times bracket IO; no UTC mapping of RF samples or inter-node synchronization.'},
                'captures': captures, 'annotations': []}
            with (folder / 'iq.sigmf-meta').open('x') as meta_file:
                meta_file.write(json.dumps(metadata, indent=2, allow_nan=False) + '\n')
                meta_file.flush()
                os.fsync(meta_file.fileno())
            final = output / capture_id
            folder.rename(final)
            return final, summary
        finally:
            if source is not None:
                source.close()
            # .partial remains on failure for diagnosis; consumes the same quota.
