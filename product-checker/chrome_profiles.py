"""Find Chrome / Edge on Windows, list their profiles (e.g. "Sohaib") and open links in one of them.

Opening a link this way uses your real browser profile with all its logins and extensions.
Automation tools are not allowed to drive that profile (Chrome blocks it since version 136),
so the Amazon checks use their own separate profile instead.
"""
import json
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

LOCAL = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share')

BROWSERS = {
    'chrome': {
        'label': 'Chrome',
        'data': LOCAL / 'Google' / 'Chrome' / 'User Data',
        'exe': [r'%ProgramFiles%\Google\Chrome\Application\chrome.exe',
                r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe',
                r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'],
        'which': ['chrome', 'google-chrome', 'google-chrome-stable'],
        'reg': 'chrome.exe',
    },
    'edge': {
        'label': 'Edge',
        'data': LOCAL / 'Microsoft' / 'Edge' / 'User Data',
        'exe': [r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe',
                r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'],
        'which': ['msedge', 'microsoft-edge'],
        'reg': 'msedge.exe',
    },
}


def find_exe(browser):
    b = BROWSERS[browser]
    for p in b['exe']:
        p = os.path.expandvars(p)
        if '%' not in p and Path(p).is_file():
            return p
    try:                                            # "App Paths" in the Windows registry
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + b['reg']) as k:
                    p = winreg.QueryValue(k, None)
                    if p and Path(p).is_file():
                        return p
            except OSError:
                pass
    except ImportError:
        pass
    for name in b['which']:
        p = shutil.which(name)
        if p:
            return p
    return None


def list_profiles():
    """[{id, browser, dir, name, email, label}] for every Chrome and Edge profile on this PC."""
    out = []
    for browser, b in BROWSERS.items():
        try:
            state = json.loads((b['data'] / 'Local State').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        cache = (state.get('profile') or {}).get('info_cache') or {}
        for d, info in sorted(cache.items(), key=lambda x: (x[0] != 'Default', x[0])):
            name = info.get('name') or d
            email = info.get('user_name') or ''
            out.append({
                'id': '%s:%s' % (browser, d), 'browser': browser, 'dir': d, 'name': name, 'email': email,
                'label': '%s - %s%s' % (b['label'], name, ' (%s)' % email if email else ''),
            })
    return out


def pick_default(profiles, wanted='Sohaib'):
    for p in profiles:
        if wanted and wanted.lower() in (p['name'] + ' ' + p['email']).lower():
            return p['id']
    return profiles[0]['id'] if profiles else ''


def open_url(url, profile_id=''):
    """Open url in the chosen browser profile; falls back to the default browser."""
    if profile_id and ':' in profile_id:
        browser, d = profile_id.split(':', 1)
        exe = find_exe(browser) if browser in BROWSERS else None
        if exe:
            subprocess.Popen([exe, '--profile-directory=%s' % d, url], close_fds=True,
                             creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
            return True
    webbrowser.open(url)
    return False


def open_app_window(url, data_dir, size=(1440, 920)):
    """Open url as a separate app window (no tabs or address bar). Returns the process, or None."""
    for browser in ('chrome', 'edge'):
        exe = find_exe(browser)
        if exe:
            Path(data_dir).mkdir(parents=True, exist_ok=True)
            return subprocess.Popen([exe, '--app=%s' % url, '--user-data-dir=%s' % data_dir,
                                     '--window-size=%d,%d' % size, '--no-first-run', '--no-default-browser-check',
                                     '--disable-features=Translate'], close_fds=True)
    webbrowser.open(url)
    return None
