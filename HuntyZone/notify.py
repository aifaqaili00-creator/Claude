"""Windows notifications: one short summary per refresh, never while you are looking at the app."""
import logging
import os
import subprocess
import sys
from pathlib import Path

import winsys

log = logging.getLogger('pc')
SCRIPT = Path(__file__).resolve().parent / 'toast.ps1'


def toast(title, body, launch_url=None):
    """Show a Windows toast. Returns True if PowerShell ran it. Never raises."""
    if sys.platform != 'win32':
        log.info('toast (not on Windows): %s - %s', title, body)
        return False
    try:
        args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
                '-Title', str(title)[:120], '-Body', str(body)[:400]]
        r = subprocess.run(args, timeout=15, capture_output=True,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        return r.returncode == 0
    except Exception:                                       # noqa: BLE001
        log.exception('toast failed')
        return False


def digest(alerts, settings, now_hour, window_focused):
    """(title, body) for the new alerts worth a toast, or None."""
    if window_focused:
        return None
    if winsys.in_quiet_hours(now_hour, settings.get('quiet_start', 22), settings.get('quiet_end', 7)):
        return None
    keep = [a for a in alerts if (a.get('severity') or 1) >= settings.get('toast_min_severity', 2) and not a.get('toasted')]
    if not keep:
        return None
    keep.sort(key=lambda a: -(a.get('severity') or 1))
    title = '1 new market alert' if len(keep) == 1 else '%d new market alerts' % len(keep)
    lines = [str(a.get('title') or '')[:90] for a in keep[:3]]
    if len(keep) > 3:
        lines.append('+%d more' % (len(keep) - 3))
    return title, '\n'.join(lines)
