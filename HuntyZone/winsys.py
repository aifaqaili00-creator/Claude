"""Windows integration: "Start with Windows", battery state, internet check, quiet hours.

Everything degrades gracefully on other systems (tests run on Linux).
"""
import glob
import logging
import os
import socket
import sys

log = logging.getLogger('pc')
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'ProductChecker'


def login_command(folder):
    """The command Windows runs at sign-in: start.bat in background mode."""
    return '"%s" background' % os.path.join(str(folder), 'start.bat')


def set_run_at_login(enabled, command=None):
    """Add or remove the HKCU Run entry. Returns True when the registry now matches the wish."""
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        log.exception('could not change the Run key')
        return False


def run_at_login():
    if sys.platform != 'win32':
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_NAME)[0]
    except OSError:
        return None


def on_battery():
    """True when a laptop runs on battery. False when plugged in or unknown."""
    if sys.platform == 'win32':
        try:
            import ctypes
            from ctypes import wintypes

            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [('ACLineStatus', wintypes.BYTE), ('BatteryFlag', wintypes.BYTE),
                            ('BatteryLifePercent', wintypes.BYTE), ('SystemStatusFlag', wintypes.BYTE),
                            ('BatteryLifeTime', wintypes.DWORD), ('BatteryFullLifeTime', wintypes.DWORD)]
            st = SYSTEM_POWER_STATUS()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
                return False
            return st.ACLineStatus == 0
        except Exception:                                   # noqa: BLE001
            return False
    try:
        mains = [p for p in glob.glob('/sys/class/power_supply/*/online')]
        if not mains:
            return False
        return all(open(p).read().strip() == '0' for p in mains)
    except OSError:
        return False


def online(hosts=(('www.amazon.com', 443), ('1.1.1.1', 443)), timeout=3):
    """True when any of the hosts accepts a connection."""
    for host, port in hosts:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def in_quiet_hours(hour, start, end):
    """Quiet from `start` to `end` o'clock, wrapping past midnight (22 to 7). Equal start and end = never."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):     # 0x1: never show a prompt
        raise OSError('Windows could not %s the secret (error %d)' % ('protect' if protect else 'read',
                                                                    ctypes.get_last_error()))
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(data):
    """Encrypt bytes so only this Windows user on this PC can read them (DPAPI)."""
    if sys.platform != 'win32':
        raise OSError('Saving secrets needs Windows')
    return _dpapi(data, True)


def unprotect(blob):
    if sys.platform != 'win32':
        raise OSError('Reading secrets needs Windows')
    return _dpapi(blob, False)
