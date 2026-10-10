"""One copy of the app at a time, and a clean browser profile after a crash.

Also decides what happens when the window closes (watchdog_decision): stop, or keep refreshing in the background.
"""
import os
import sys
from pathlib import Path

ERROR_ALREADY_EXISTS = 183


class Instance:
    """A named mutex on Windows, an exclusive lock file elsewhere."""

    def __init__(self, name='ProductCheckerV6', lock_dir=None):
        self.name = name
        self.lock_dir = Path(lock_dir) if lock_dir else None
        self._handle = None
        self._file = None

    def acquire(self):
        if sys.platform == 'win32' and self.lock_dir is None:
            try:
                import ctypes
                from ctypes import wintypes
                k32 = ctypes.WinDLL('kernel32', use_last_error=True)
                k32.CreateMutexW.restype = wintypes.HANDLE
                k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
                h = k32.CreateMutexW(None, False, 'Local\\' + self.name)
                if not h:
                    return True                      # cannot tell: do not block the app
                if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                    k32.CloseHandle(h)
                    return False
                self._handle = h
                return True
            except Exception:                        # noqa: BLE001
                return True
        folder = self.lock_dir or Path(os.environ.get('TEMP') or '/tmp')
        folder.mkdir(parents=True, exist_ok=True)
        f = open(folder / (self.name + '.lock'), 'a+')
        try:
            if sys.platform == 'win32':
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._file = f
        return True

    def release(self):
        if self._handle:
            try:
                import ctypes
                ctypes.WinDLL('kernel32').CloseHandle(self._handle)
            except Exception:                        # noqa: BLE001
                pass
            self._handle = None
        if self._file:
            try:
                if sys.platform == 'win32':
                    import msvcrt
                    self._file.seek(0)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._file.close()
            self._file = None


LOCK_NAMES = ('SingletonLock', 'SingletonCookie', 'SingletonSocket')


def profile_locked(profile_dir):
    """Is a Chrome profile in use (or left locked by a crash)?"""
    p = Path(profile_dir)
    if any(os.path.lexists(p / n) for n in LOCK_NAMES):
        return True
    lf = p / 'lockfile'
    if lf.exists():
        try:
            os.remove(lf)                            # Windows keeps it open (undeletable) while Chrome runs
            return False
        except OSError:
            return True
    return False


def clear_stale_profile_lock(profile_dir):
    """Remove lock files from our private profile. Only call while this app holds the Instance lock."""
    p = Path(profile_dir)
    removed = False
    for n in LOCK_NAMES + ('lockfile',):
        f = p / n
        if os.path.lexists(f):
            try:
                os.remove(f)
                removed = True
            except OSError:
                pass
    return removed


def watchdog_decision(now, window_exited_at, started_at, last_beat, keep_running, mode):
    """'continue', 'shutdown' or 'background' after the app window closes or stops checking in."""
    if mode == 'background':
        return 'continue'
    gone = (window_exited_at is not None and window_exited_at - started_at > 8) or (now - last_beat > 180)
    if gone:
        return 'background' if keep_running else 'shutdown'
    return 'continue'
