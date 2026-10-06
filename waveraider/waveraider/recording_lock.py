"""Nonblocking cross-process lock on a recording output directory."""
from contextlib import contextmanager
import os


@contextmanager
def directory_lock(folder):
    with (folder / '.recording.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise OSError('another recorder is using this output folder') from None
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise OSError('another recorder is using this output folder') from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
