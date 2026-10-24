"""Update notice: is a newer Product Checker published? Every copy (the .exe you share, or a source folder) asks GitHub once
a day for the releases of the repository in Settings (update_repo), and finds the newest one whose tag is a version
("v8.0" or "productchecker-v8.0"). When it is newer than the running version, the window shows "Version 8.0 is
available" with what changed and a Download button (the release's ProductChecker.exe, else its page).

To publish one: on github.com, Releases, "Draft a new release", tag v8.0 (the VERSION in config.py), write what changed,
attach dist\\ProductChecker.exe (build_exe.bat), Publish. Nothing else to set up; the repository must be public.
"""
import json
import logging
import re
import threading
import time
import urllib.request

import config

log = logging.getLogger('pc')
CHECK_EVERY = 24 * 3600
TAG = re.compile(r'^(?:productchecker[-_ ]?)?v?(\d+(?:\.\d+){1,2})$', re.I)
ASSET = re.compile(r'productchecker.*\.exe$', re.I)
STATE = {'checked_at': None, 'latest': None, 'error': None}


def vkey(v):
    """'7.10' > '7.9': versions compare as numbers, part by part."""
    return tuple(int(x) for x in re.findall(r'\d+', str(v or '0')))


def newest(releases, current=None):
    """The newest published release that is a version newer than `current`: {'version', 'notes', 'download', 'page',
    'published'} or None. Drafts and pre-releases are left out."""
    best = None
    for r in releases or ():
        if r.get('draft') or r.get('prerelease'):
            continue
        m = TAG.match(str(r.get('tag_name') or '').strip())
        if not m:
            continue
        if best is None or vkey(m.group(1)) > vkey(best['version']):
            exe = next((a.get('browser_download_url') for a in r.get('assets') or () if ASSET.search(a.get('name') or '')), None)
            best = {'version': m.group(1), 'notes': ' '.join(str(r.get('body') or '').split())[:600] or None,
                    'download': exe or r.get('html_url'), 'page': r.get('html_url'), 'published': r.get('published_at'),
                    'exe': bool(exe)}
    if best and vkey(best['version']) > vkey(current or config.VERSION):
        return best
    return None


def fetch(repo, timeout=15):
    req = urllib.request.Request('https://api.github.com/repos/%s/releases?per_page=20' % repo,
                                 headers={'User-Agent': 'ProductChecker/' + config.VERSION, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000).decode('utf-8'))


def check(settings, fetcher=None, now=None):
    """Ask once (when on in Settings). Returns STATE: {'checked_at', 'latest' (newest() or None), 'error'}."""
    repo = (settings.get('update_repo') or '').strip()
    if not settings.get('update_check', True) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        STATE.update(latest=None, error=None)
        return STATE
    try:
        STATE.update(latest=newest((fetcher or fetch)(repo)), error=None)
    except Exception as e:                                          # noqa: BLE001 - offline: try again tomorrow
        STATE['error'] = (str(e).splitlines() or [e.__class__.__name__])[0][:160]
        log.info('update check failed: %s', STATE['error'])
    STATE['checked_at'] = now or time.time()
    return STATE


def start(get_settings):
    """Check a minute after start, then once a day, in the background."""
    def loop():
        time.sleep(60)
        while True:
            check(get_settings())
            time.sleep(CHECK_EVERY)
    threading.Thread(target=loop, daemon=True, name='update-check').start()
