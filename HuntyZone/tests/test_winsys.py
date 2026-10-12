"""Windows integration helpers, notifications, the single-instance lock and the watchdog decision."""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify  # noqa: E402
import singleton  # noqa: E402
import winsys  # noqa: E402

SETTINGS = {'quiet_start': 22, 'quiet_end': 7, 'toast_min_severity': 2}


def test_quiet_hours():
    assert winsys.in_quiet_hours(23, 22, 7) and winsys.in_quiet_hours(0, 22, 7) and winsys.in_quiet_hours(6, 22, 7)
    assert not winsys.in_quiet_hours(7, 22, 7) and not winsys.in_quiet_hours(21, 22, 7)
    assert winsys.in_quiet_hours(13, 12, 14) and not winsys.in_quiet_hours(14, 12, 14)
    assert not any(winsys.in_quiet_hours(h, 5, 5) for h in range(24))


def test_online(monkeypatch):
    def fail(*a, **k):
        raise OSError('no route')
    monkeypatch.setattr(socket, 'create_connection', fail)
    assert winsys.online() is False

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    calls = []

    def second(addr, timeout=None):
        calls.append(addr)
        if len(calls) == 1:
            raise OSError
        return Conn()
    monkeypatch.setattr(socket, 'create_connection', second)
    assert winsys.online() is True and len(calls) == 2


def test_platform_helpers_are_safe():
    assert isinstance(winsys.on_battery(), bool)
    if sys.platform != 'win32':
        assert winsys.set_run_at_login(True, 'x') is False and winsys.run_at_login() is None
        assert notify.toast('t', 'b') is False
    assert winsys.login_command('C:\\Apps\\Product Checker') == '"C:\\Apps\\Product Checker' + ('\\' if sys.platform == 'win32' else '/') + 'start.bat" background'


def test_digest_rules():
    a = [{'title': 'A', 'severity': 3}, {'title': 'B', 'severity': 1}, {'title': 'C', 'severity': 2},
         {'title': 'D', 'severity': 2}, {'title': 'E', 'severity': 2}, {'title': 'F', 'severity': 3, 'toasted': 1}]
    title, body = notify.digest(a, SETTINGS, 12, False)
    assert title == '4 new market alerts' and body.split('\n')[0] == 'A' and body.endswith('+1 more') and 'B' not in body
    assert notify.digest(a, SETTINGS, 12, True) is None
    assert notify.digest(a, SETTINGS, 23, False) is None
    assert notify.digest([{'title': 'x', 'severity': 1}], SETTINGS, 12, False) is None
    assert notify.digest(a[:1], SETTINGS, 12, False)[0] == '1 new market alert'


def test_instance_lock(tmp_path):
    a, b = singleton.Instance('t', tmp_path), singleton.Instance('t', tmp_path)
    assert a.acquire()
    assert not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_profile_locks(tmp_path):
    assert not singleton.profile_locked(tmp_path)
    (tmp_path / 'SingletonLock').symlink_to('host-123')            # dangling, as Chrome leaves it
    assert singleton.profile_locked(tmp_path)
    assert singleton.clear_stale_profile_lock(tmp_path)
    assert not singleton.profile_locked(tmp_path) and not singleton.clear_stale_profile_lock(tmp_path)
    (tmp_path / 'lockfile').write_text('')
    assert not singleton.profile_locked(tmp_path)                   # removable = not held


def test_watchdog_decision():
    d = singleton.watchdog_decision
    assert d(1000, None, 0, 990, False, 'window') == 'continue'
    assert d(1000, 500, 0, 990, False, 'window') == 'shutdown'
    assert d(1000, 500, 0, 990, True, 'window') == 'background'
    assert d(1000, 5, 0, 990, False, 'window') == 'continue'        # quick hand-off to an open window
    assert d(1000, None, 0, 700, False, 'window') == 'shutdown'
    assert d(1000, None, 0, 700, True, 'window') == 'background'
    assert d(1000, 500, 0, 0, False, 'background') == 'continue'
