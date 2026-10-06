"""Opt-in receiver query. No tuning, streaming, transmit or firmware changes."""
from __future__ import annotations

import ctypes as C
import json
import re
import subprocess
import sys


def query(serial):
    from .bladerf_sweep import _libpath
    path = _libpath()
    if not path:
        return {'state': 'unavailable', 'detail': 'libbladeRF is not installed'}
    lib = C.CDLL(path)
    lib.bladerf_open.argtypes = [C.POINTER(C.c_void_p), C.c_char_p]
    lib.bladerf_open.restype = C.c_int
    lib.bladerf_close.argtypes = [C.c_void_p]
    lib.bladerf_close.restype = None
    lib.bladerf_get_board_name.argtypes = [C.c_void_p]
    lib.bladerf_get_board_name.restype = C.c_char_p
    lib.bladerf_is_fpga_configured.argtypes = [C.c_void_p]
    lib.bladerf_is_fpga_configured.restype = C.c_int
    device = C.c_void_p()
    try:
        code = lib.bladerf_open(C.byref(device), ('*:serial=' + serial).encode())
        if code != 0:
            return {'state': 'unavailable', 'detail': f'Receiver open failed ({code}); disconnected, busy or inaccessible'}
        if lib.bladerf_get_board_name(device) != b'bladerf2':
            return {'state': 'unsupported', 'detail': 'Expected a bladeRF micro'}
        configured = lib.bladerf_is_fpga_configured(device)
        return {'state': 'accessible' if configured == 1 else 'not_ready',
                'detail': 'Device responds; FPGA configured. RF reception not tested.' if configured == 1
                else 'Device responds; FPGA is not ready', 'serial': serial}
    finally:
        if device:
            lib.bladerf_close(device)


def receiver_health(serial=None):
    if serial is None:
        return {'state': 'not_probed', 'detail': 'No receiver selected for health checks'}
    if not re.fullmatch(r'[0-9a-fA-F]{32}', serial):
        raise ValueError('receiver serial must contain 32 hexadecimal characters')
    # Isolate native-library hangs/crashes from the heartbeat loop.
    try:
        result = subprocess.run([sys.executable, '-m', 'waveraider.node_health', serial],
                                capture_output=True, text=True, timeout=3, check=True)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'state': 'unavailable', 'detail': 'Receiver query failed or timed out'}


if __name__ == '__main__':
    if len(sys.argv) != 2 or not re.fullmatch(r'[0-9a-fA-F]{32}', sys.argv[1]):
        raise SystemExit(2)
    print(json.dumps(query(sys.argv[1])))
