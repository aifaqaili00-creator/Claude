"""Product Checker: Amazon AU / UAE / US research desk.

Starts a small local server and opens the app in its own window. Run with start.bat or:  python app.py
"""
import asyncio
import json
import logging
import logging.handlers
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import amazon_check as ac
import chrome_profiles as cp
import config
import file_rank as fr
import ideas
import importer
import reports
import storage
import trends
import xray
from jobs import JobManager

HERE = Path(__file__).resolve().parent
UI_DIR = HERE / 'ui'
APP_DIR = config.APP_DIR
SETTINGS_FILE = APP_DIR / 'settings.json'
HISTORY_FILE = APP_DIR / 'history.json'                       # v5 only: imported into market.db on first start
DB_FILE = APP_DIR / 'market.db'
PORT_FILE = APP_DIR / 'port.txt'
HELIUM10 = 'https://members.helium10.com/'
VERSION = config.VERSION
DEFAULT_EXPECT = {c: tuple(m['expect']) for c, m in ac.MARKETS.items()}   # e.g. AU: ('2000', 'sydney')
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
        '.svg': 'image/svg+xml', '.png': 'image/png', '.json': 'application/json; charset=utf-8',
        '.woff2': 'font/woff2', '.ico': 'image/x-icon'}   # fixed: the Windows registry can map .js to text/plain

log = logging.getLogger('pc')


def setup_logging():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(str(APP_DIR / 'app.log'), maxBytes=2_000_000, backupCount=5,
                                                   encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    """Write atomically. A Windows file lock (antivirus, indexer) is retried, never fatal."""
    tmp = Path(str(path) + '.tmp')
    for attempt in range(5):
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))
        except OSError:
            log.exception('could not save %s', path)
            return False
    log.warning('could not save %s (file locked)', path)
    return False


def clean(o):
    """Make data JSON-safe: NaN/inf -> None, numpy numbers -> Python numbers."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, float):
        return None if o != o or o in (float('inf'), float('-inf')) else o
    if hasattr(o, 'item') and not isinstance(o, (str, bytes)):          # numpy scalar
        try:
            return clean(o.item())
        except (ValueError, TypeError):
            return str(o)
    return o


class State:
    def __init__(self, start_loop=True):
        self.lock = threading.Lock()
        self.settings = config.merge(load_json(SETTINGS_FILE, {}))
        self.profiles = cp.list_profiles()
        if not self.settings.get('profile') or self.settings['profile'] not in [p['id'] for p in self.profiles]:
            self.settings['profile'] = cp.pick_default(self.profiles)
        self.apply_locations()
        self.db = storage.DB(DB_FILE).open()                      # every search, scan and import is kept here
        self.db.write(storage.interrupt_stale_runs)
        if Path(HISTORY_FILE).exists():
            storage.import_history_file(self.db, HISTORY_FILE)
        self.ranks = {}                                           # rank id -> DataFrame
        self.xrays = {}                                           # xray analysis id -> result
        self.idea_cats = {}                                       # market -> Movers & Shakers categories
        self.idea_cache = {}                                      # (market, slugs, kinds) -> raw scan
        self.messages = []                                        # recent log lines for the UI
        self.last_beat = time.time()
        self.window = None
        self.token = secrets.token_urlsafe(24)                    # every API call must carry it
        self.port = None
        self.started = time.time()
        self.loop = asyncio.new_event_loop()
        if start_loop:                                            # one event loop thread owns the browser
            threading.Thread(target=self.loop.run_forever, daemon=True, name='browser').start()
        self.checker = ac.Checker(log=self.say)
        self.trends = trends.BrowserTrends(self.checker, self.say)
        self.trends.breaker.load(self.db.read(storage.get_state, 'trends_breaker'))
        self.jobs = JobManager(self.run, self.say)

    def apply_locations(self):
        """One rule for which 'Deliver to' text counts as correct, used at start-up and after Settings changes."""
        for c, v in self.settings['locations'].items():
            if c not in ac.MARKETS:
                continue
            ac.MARKETS[c]['location'] = v
            default = config.DEFAULT_LOCATIONS.get(c)
            ac.MARKETS[c]['expect'] = DEFAULT_EXPECT[c] if v == default else (v.lower(),)

    def say(self, msg):
        log.info(msg)
        with self.lock:
            self.messages.append({'t': time.time(), 'msg': msg})
            self.messages = self.messages[-60:]

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def save_settings(self):
        save_json(SETTINGS_FILE, self.settings)

    def new_job(self, kind, fn):
        return self.jobs.start(kind, fn)

    # ---------- checks ----------
    def days(self):
        return int(self.settings['fast_days']), int(self.settings['local_days']), int(self.settings['pages'])

    def resummarize(self, s):
        """Recount a saved result with the current day limits (also upgrades results saved by older versions)."""
        fast, local, _ = self.days()
        out = ac.summarize(s['market'], s['keyword'], s.get('location', ''), s.get('rows', []), fast, local,
                           s.get('checked_at'))
        if s.get('snapshot_id'):
            out['snapshot_id'] = s['snapshot_id']
        return out

    def cached(self, code, kw, pages):
        """A search from the last few hours for the same place and page count, recounted. None if there is none."""
        s = self.db.read(storage.latest_serp, code, kw, pages, self.settings['locations'].get(code),
                         self.settings['cache_hours'] * 3600, statuses=storage.VALID + ('wrong_location',))
        return {**self.resummarize(s), 'cached': True} if s else None

    async def save_check(self, s, run_id, pages, source='user'):
        """Keep a finished search in market.db. A database problem is logged, never shown as a failed check."""
        try:
            s['snapshot_id'] = await self.db.awrite(storage.save_serp, s, source, run_id, pages,
                                                     self.settings['locations'].get(s['market']))
        except Exception:                                           # noqa: BLE001
            log.exception('could not save the search')

    def import_folders(self):
        folders = [xray.downloads_dir()]
        if self.settings.get('import_folder'):
            folders.append(Path(self.settings['import_folder']))
        return folders

    def scan_imports(self, progress=None):
        """Import new Helium 10 exports from Downloads (and the chosen folder). Safe to run any time."""
        out = importer.scan(self.db, self.import_folders(), progress)
        n = sum(1 for r in out if r['status'] == 'imported')
        if n:
            self.say('Imported %d Helium 10 export%s into your history.' % (n, '' if n == 1 else 's'))
        return out

    async def refresh_trends(self, kw, geo, progress=None, force=False):
        """Google Trends for one term and country. Google refusing pauses Trends; that is reported, not raised."""
        try:
            if progress:
                progress('Google Trends: %s in %s...' % (kw, ac.MARKETS[geo]['name']))
            return await trends.refresh(self.db, self.trends, kw, geo, force=force, with_related=True)
        except trends.TrendsError as e:
            if progress:
                progress('Google Trends: %s' % e)
            return None
        finally:
            await self.db.awrite(storage.set_state, 'trends_breaker', self.trends.breaker.state())

    async def check_many(self, keyword, markets, refresh, progress):
        fast, local, pages = self.days()
        run_id = await self.db.awrite(storage.start_run, 'check', 'user')
        fresh = []

        async def one(code):
            hit = None if refresh else self.cached(code, keyword, pages)
            if hit:
                progress('%s: from the last %d hours (no new search)' % (ac.MARKETS[code]['name'], self.settings['cache_hours']))
                return hit
            progress('%s: searching "%s"...' % (ac.MARKETS[code]['name'], keyword))
            try:
                s = await self.checker.check(keyword, code, fast, pages, progress, local)
            except Exception as e:
                msg = (str(e).strip() or e.__class__.__name__).splitlines()[0][:200]
                progress('%s failed: %s' % (ac.MARKETS[code]['name'], msg[:150]))
                gap = {'market': code, 'name': ac.MARKETS[code]['name'], 'keyword': keyword, 'error': msg,
                       'status': getattr(e, 'status', 'error')}
                await self.save_check(dict(gap), run_id, pages)                 # stored as a gap, never as zero
                return gap
            await self.save_check(s, run_id, pages)
            fresh.append(s)
            return s
        results = await asyncio.gather(*(one(c) for c in markets))
        failed = sum(1 for r in results if 'error' in r)
        await self.db.awrite(storage.finish_run, run_id, 'done' if fresh or not failed else 'error', len(fresh), failed)
        return {'keyword': keyword, 'results': results, 'run_id': run_id}

    async def check_batch(self, items, progress):
        """Check several products (from a ranked file or ideas), 3 at a time."""
        fast, local, pages = self.days()
        sem = asyncio.Semaphore(3)
        out = {}
        run_id = await self.db.awrite(storage.start_run, 'batch', 'user')

        async def one(it):
            async with sem:
                kw, code = it['search'], it['market']
                hit = self.cached(code, kw, pages)
                if not hit:
                    try:
                        hit = await self.checker.check(kw, code, fast, pages, None, local)
                        await self.save_check(hit, run_id, pages)
                    except Exception as e:
                        msg = (str(e).strip() or e.__class__.__name__).splitlines()[0][:150]
                        await self.save_check({'market': code, 'keyword': kw, 'error': msg,
                                               'status': getattr(e, 'status', 'error')}, run_id, pages)
                        out[it['key']] = {'error': msg}
                        progress('"%s" failed' % kw)
                        return
                out[it['key']] = {k: hit.get(k) for k in (
                    'fast', 'slow', 'total', 'level', 'verdict', 'fast_reviews_median', 'bought_top', 'bought_total',
                    'bought_listings', 'search_url', 'keyword', 'location_ok', 'local', 'overseas', 'overseas_intl',
                    'market', 'fast_price_min', 'fast_price_max', 'status')}
                progress('"%s": %d fast, %d local, %d overseas of %d' % (kw, hit['fast'], hit['local'], hit['overseas'], hit['total']))
        await asyncio.gather(*(one(i) for i in items))
        n_fail = sum(1 for v in out.values() if 'error' in v)
        await self.db.awrite(storage.finish_run, run_id, 'done', len(out) - n_fail, n_fail)
        return out

    async def find_ideas(self, code, slugs, kinds, hide, refresh, progress):
        if refresh or code not in self.idea_cats:
            progress('Reading the %s category list...' % ac.MARKETS[code]['name'])
            self.idea_cats[code] = await ideas.categories(self.checker, code)
        cats = self.idea_cats[code]
        chosen = [c for c in cats if c['slug'] in slugs] if slugs else [c for c in cats if not c['risky']][:8]
        if not chosen:
            raise RuntimeError('No categories found on Amazon %s. Try again in a minute.' % ac.MARKETS[code]['name'])
        key = (code, tuple(sorted(c['slug'] for c in chosen)), tuple(sorted(kinds)))
        hit = self.idea_cache.get(key)
        if hit and not refresh and time.time() - hit['at'] < self.settings['cache_hours'] * 3600:
            progress('Using the scan from %d minutes ago' % ((time.time() - hit['at']) // 60))
            raw = hit
        else:
            raw = await ideas.scan(self.checker, code, chosen, kinds, None, progress)
            raw['at'] = time.time()
            self.idea_cache[key] = raw
        # "hide risky" is applied when reading, so switching it never re-scrapes
        scored = await asyncio.to_thread(ideas.score_ideas, raw['items'], code, hide)
        chosen_slugs = {c['slug'] for c in chosen}
        return {**{k: v for k, v in raw.items() if k != 'items'}, 'ideas': scored,
                'all_categories': [{**c, 'on': c['slug'] in chosen_slugs} for c in cats], 'kinds': kinds}


S = None
GET, POST = {}, {}


def route(method, path):
    def deco(fn):
        (GET if method == 'GET' else POST)[path] = fn
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    server_version = 'ProductChecker/' + VERSION

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8', headers=None):
        data = body if isinstance(body, bytes) else json.dumps(clean(body), ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n else b''

    def _json(self):
        try:
            d = json.loads(self._body() or b'{}')
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    # ---- security: only this app's own page may talk to the API ----
    def _host_ok(self):
        host = (self.headers.get('Host') or '').lower()
        return host in ('127.0.0.1:%s' % S.port, 'localhost:%s' % S.port)

    def _origin_ok(self):
        origin = self.headers.get('Origin')
        return origin is None or origin in ('http://127.0.0.1:%s' % S.port, 'http://localhost:%s' % S.port)

    def _token_ok(self):
        return secrets.compare_digest(self.headers.get('X-PC-Token') or '', S.token)

    def _dispatch(self, table):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._host_ok():
            return self._send(403, {'error': 'forbidden host'})
        if u.path == '/api/ping':
            return self._send(200, {'ok': True, 'version': VERSION})
        if table is GET and not u.path.startswith('/api/'):
            return self._static(u.path)
        if not self._origin_ok() or not self._token_ok():
            return self._send(403, {'error': 'forbidden'})
        fn = table.get(u.path)
        if not fn:
            return self._send(404, {'error': 'not found'})
        try:
            return fn(self, q)
        except Exception as e:                                  # noqa: BLE001 - shown to the user
            log.exception('%s %s', self.command, self.path)
            return self._send(500, {'error': (str(e).strip() or e.__class__.__name__)[:500]})

    def do_GET(self):
        self._dispatch(GET)

    def do_POST(self):
        self._dispatch(POST)

    def _static(self, path):
        if path in ('/', '/index.html'):
            html = (UI_DIR / 'index.html').read_text(encoding='utf-8').replace('%%PC_TOKEN%%', S.token)
            return self._send(200, html.encode('utf-8'), MIME['.html'])
        if path in ('/favicon.svg', '/favicon.ico'):
            return self._send(200, ICON, 'image/svg+xml')
        if path.startswith('/ui/'):
            target = (UI_DIR / path[4:]).resolve()
            if UI_DIR.resolve() in target.parents and target.is_file() and target.suffix.lower() in MIME:
                return self._send(200, target.read_bytes(), MIME[target.suffix.lower()],
                                  {'Cache-Control': 'no-cache'})
        return self._send(404, {'error': 'not found'})

    # ---- file helpers ----
    def _rank(self, q):
        name = os.path.basename(q.get('name') or 'upload.csv')
        ext = os.path.splitext(name)[1].lower()
        if ext not in ('.csv', '.xlsx', '.xls', '.xlsm'):
            return self._send(400, {'error': 'Please use a .csv or .xlsx file.'})
        data = self._body()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            Path(path).write_bytes(data)
            try:
                mk, top, all_, summary = fr.rank(path, q.get('market', 'auto'), config._coerce('top', q.get('top') or 10))
            except Exception as e:
                return self._send(400, {'error': str(e)})
            imported = importer.import_file(S.db, path, market=mk, name=name)
        rid = uuid.uuid4().hex[:8]
        S.ranks[rid] = all_
        S.ranks = dict(list(S.ranks.items())[-10:])
        counts = all_['verdict'].value_counts().to_dict()
        return self._send(200, {'id': rid, 'file': name, 'market': mk, 'summary': summary, 'counts': counts,
                                'total': len(all_), 'target': fr.TARGETS[mk], 'rows': fr.to_records(all_.head(100)),
                                'imported': imported})

    def _xray(self, path, q, name=None):
        try:
            a = xray.analyse(path, q.get('market') or 'auto', (q.get('keyword') or '').strip())
        except Exception as e:
            log.exception('xray')
            return self._send(400, {'error': str(e).splitlines()[0][:300]})
        a['imported'] = importer.import_file(S.db, path, market=a.get('market'), kind='xray',
                                             keyword=a.get('keyword') or None, name=name or Path(path).name)
        a['id'] = uuid.uuid4().hex[:8]
        S.xrays[a['id']] = a
        S.xrays = dict(list(S.xrays.items())[-20:])
        return self._send(200, a)

    def _export(self, q):
        import pandas as pd
        tmp = os.path.join(tempfile.gettempdir(), 'pc_export_%s.xlsx' % uuid.uuid4().hex[:6])
        if q.get('kind') == 'rank' and q.get('id') in S.ranks:
            df = S.ranks[q['id']].copy()
            fast = q.get('fast') or {}                               # delivery results from the UI, by ASIN
            extra = []
            if fast:
                for col, key in (('fast_sellers', 'fast'), ('local_sellers', 'local'), ('abroad_sellers', 'overseas'),
                                 ('local_verdict', 'verdict')):
                    df[col] = df['asin'].map(lambda a, k=key: (fast.get(a) or {}).get(k))
                extra = [('fast_sellers', 'Fast sellers'), ('local_sellers', 'Local sellers'),
                         ('abroad_sellers', 'Not local'), ('local_verdict', 'Delivery verdict')]
            fr.export_excel(df, tmp, extra)
            name = 'Top_products.xlsx'
        elif q.get('kind') == 'check':
            job = S.jobs.get(q.get('job', ''))
            if not job or not job.get('result'):
                return self._send(410, {'error': 'This check is no longer in memory. Run it again to save it.'})
            res = job['result'].get('results', [])
            with pd.ExcelWriter(tmp, engine='openpyxl') as xw:
                pd.DataFrame([{k: r.get(k) for k in ('name', 'location', 'total', 'fast', 'slow', 'unknown',
                                                     'local', 'overseas', 'overseas_intl', 'overseas_slow',
                                                     'origin_unknown', 'sponsored', 'fast_reviews_median', 'bought_top',
                                                     'verdict')}
                              for r in res if 'error' not in r]).to_excel(xw, sheet_name='Summary', index=False)
                rows = [x for r in res for x in r.get('rows', [])]
                pd.DataFrame(rows).drop(columns=['image'], errors='ignore').to_excel(xw, sheet_name='Listings', index=False)
            name = 'Delivery_check_%s.xlsx' % '_'.join(job['result'].get('keyword', 'x').split())
        elif q.get('kind') == 'xray' and q.get('id') in S.xrays:
            a = S.xrays[q['id']]
            xray.export_excel(a, tmp)
            name = 'Xray_analysis_%s_%s.xlsx' % (a['market'], '_'.join(a['keyword'].split()) or 'market')
        else:
            return self._send(404, {'error': 'nothing to export'})
        data = Path(tmp).read_bytes()
        os.remove(tmp)
        return self._send(200, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                          {'Content-Disposition': 'attachment; filename="%s"' % name})


# ---------------- GET routes ----------------
@route('GET', '/api/state')
def api_state(h, q):
    S.last_beat = time.time()
    since = float(q.get('since') or 0)
    return h._send(200, {
        'version': VERSION, 'settings': S.settings, 'profiles': S.profiles,
        'browser': {'state': S.checker.state, 'error': S.checker.error, 'visible': S.checker.visible},
        'locations': S.checker.locations,
        'markets': {c: {'name': m['name'], 'currency': m['currency'], 'location': m['location'], 'site': m['site']}
                    for c, m in ac.MARKETS.items()},
        'targets': fr.TARGETS,
        'jobs_running': S.jobs.running(),
        'messages': [m for m in S.messages if m['t'] > since],
    })


@route('GET', '/api/job')
def api_job(h, q):
    job = S.jobs.get(q.get('id', ''))
    return h._send(200, job) if job else h._send(410, {'error': 'This job has expired. Please run it again.'})


@route('GET', '/api/xray/watch')
def api_xray_watch(h, q):
    p = xray.find_new_export(float(q.get('since') or 0))
    return h._send(200, {'folder': str(xray.downloads_dir()),
                         'found': {'path': str(p), 'name': p.name, 'mtime': p.stat().st_mtime} if p else None})


@route('GET', '/api/history')
def api_history(h, q):
    out = []
    try:
        limit = max(1, min(200, int(q.get('limit') or 40)))
    except ValueError:
        limit = 40
    for item in S.db.read(storage.recent_checks, limit):
        markets = []
        for snap in item['snapshots']:
            markets.append({'market': snap['market'], 'fast': snap['fast'], 'local': snap['local'],
                            'overseas': snap['overseas'], 'total': snap['organic'], 'status': snap['status'],
                            'level': ac.level_for(snap['fast'], snap['organic'])})
        out.append({'id': item['id'], 'keyword': item['keyword'], 'at': item['at'], 'markets': markets})
    return h._send(200, out)


@route('GET', '/api/history/item')
def api_history_item(h, q):
    try:
        run_id = int(q.get('id') or 0)
    except ValueError:
        run_id = 0
    snaps = S.db.read(storage.run_snapshots, run_id)
    if not snaps:
        return h._send(404, {'error': 'not found'})
    return h._send(200, {'keyword': snaps[0]['keyword'], 'results': [S.resummarize(r) for r in snaps]})


@route('GET', '/api/report/keyword')
def api_report_keyword(h, q):
    market = q.get('market') if q.get('market') in ac.MARKETS else 'AU'
    kw = ' '.join(str(q.get('kw') or '').split())[:120]
    if not config.norm_kw(kw):
        return h._send(400, {'error': 'Which keyword?'})
    return h._send(200, reports.keyword_report(S.db, market, kw, S.settings))


@route('GET', '/api/library')
def api_library(h, q):
    return h._send(200, reports.library(S.db))


@route('GET', '/api/trends/state')
def api_trends_state(h, q):
    return h._send(200, S.trends.breaker.state())


# ---------------- POST routes ----------------
@route('POST', '/api/report/refresh')
def api_report_refresh(h, q):
    """Search Amazon again for this keyword and market, and fetch Google Trends if it is older than a week."""
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else 'AU'
    kw = ' '.join(str(d.get('kw', '')).split())[:120]
    if not config.norm_kw(kw):
        return h._send(400, {'error': 'Which keyword?'})

    async def work(progress):
        if d.get('amazon', True):
            await S.check_many(kw, [market], True, progress)
        if d.get('trends', True) and S.settings.get('trends_enabled', True):
            await S.refresh_trends(kw, market, progress, force=bool(d.get('force_trends')))
        return await asyncio.to_thread(reports.keyword_report, S.db, market, kw, S.settings)
    return h._send(200, {'job': S.new_job('report', work)})


@route('POST', '/api/import/scan')
def api_import_scan(h, q):
    async def work(progress):
        progress('Looking for Helium 10 exports in %s...' % ', '.join(str(f) for f in S.import_folders()))
        return await asyncio.to_thread(S.scan_imports, progress)
    return h._send(200, {'job': S.new_job('import', work)})


@route('POST', '/api/import/upload')
def api_import_upload(h, q):
    name = os.path.basename(q.get('name') or 'export.csv')
    if os.path.splitext(name)[1].lower() not in importer.EXTS:
        return h._send(400, {'error': 'Please use a .csv or .xlsx file exported from Helium 10.'})
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name)
        Path(path).write_bytes(h._body())
        r = importer.import_file(S.db, path, market=q.get('market') if q.get('market') in ac.MARKETS else None,
                                 keyword=(q.get('keyword') or '').strip() or None, name=name)
    return h._send(200 if r['status'] != 'error' else 400, r if r['status'] != 'error' else {'error': r['error'], **r})


@route('POST', '/api/check')
def api_check(h, q):
    d = h._json()
    kw = ' '.join(str(d.get('keyword', '')).split())[:120]
    markets = [m for m in d.get('markets', []) if m in ac.MARKETS] or list(ac.MARKETS)
    if not kw:
        return h._send(400, {'error': 'Type a product first.'})
    jid = S.new_job('check', lambda p: S.check_many(kw, markets, bool(d.get('refresh')), p))
    return h._send(200, {'job': jid})


@route('POST', '/api/check_batch')
def api_check_batch(h, q):
    d = h._json()
    items = []
    for i in d.get('items', []) if isinstance(d.get('items'), list) else []:
        if isinstance(i, dict) and i.get('market') in ac.MARKETS and config.norm_kw(i.get('search')):
            items.append({'key': str(i.get('key', ''))[:200], 'market': i['market'],
                          'search': ' '.join(str(i['search']).split())[:120]})
    jid = S.new_job('batch', lambda p: S.check_batch(items[:60], p))
    return h._send(200, {'job': jid})


@route('POST', '/api/ideas')
def api_ideas(h, q):
    d = h._json()
    code = d.get('market') if d.get('market') in ac.MARKETS else 'US'
    kinds = [k for k in d.get('kinds', ['movers', 'new']) if k in ideas.LISTS] or ['movers', 'new']
    slugs = [str(s) for s in d.get('slugs') or []][:20]
    jid = S.new_job('ideas', lambda p: S.find_ideas(code, slugs, kinds, d.get('hide_risky', True) is not False,
                                                    bool(d.get('refresh')), p))
    return h._send(200, {'job': jid})


@route('POST', '/api/niche')
def api_niche(h, q):
    d = h._json()
    code = d.get('market') if d.get('market') in ac.MARKETS else 'US'
    seed = ' '.join(str(d.get('seed', '')).split())[:60]
    if not seed:
        return h._send(400, {'error': 'Type a niche first, e.g. "tower fan".'})
    jid = S.new_job('niche', lambda p: ideas.suggest(S.checker, code, seed, p))
    return h._send(200, {'job': jid})


@route('POST', '/api/rank')
def api_rank(h, q):
    return h._rank(q)


@route('POST', '/api/xray')
def api_xray_upload(h, q):
    name = os.path.basename(q.get('name') or 'xray.csv')
    if os.path.splitext(name)[1].lower() not in ('.csv', '.xlsx', '.xls', '.xlsm'):
        return h._send(400, {'error': 'Please use the .csv (or .xlsx) file that Xray exports.'})
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name)
        Path(path).write_bytes(h._body())
        return h._xray(path, q, name)


@route('POST', '/api/xray/load')
def api_xray_load(h, q):
    """Analyse an export the watcher found. Only files inside the Downloads folder are allowed."""
    d = h._json()
    folder = xray.downloads_dir().resolve()
    path = Path(str(d.get('path') or '')).resolve()
    if folder not in path.parents or not path.is_file():
        return h._send(400, {'error': 'That file is not in your Downloads folder.'})
    return h._xray(str(path), d)


@route('POST', '/api/export')
def api_export(h, q):
    return h._export(h._json())


@route('POST', '/api/settings')
def api_settings(h, q):
    d = h._json()
    new, errors = config.validate(d, S.settings)
    if errors:
        return h._send(400, {'error': '; '.join(errors)})
    changed = [c for c in ac.MARKETS if new['locations'].get(c) != S.settings['locations'].get(c)]
    S.settings = new
    S.apply_locations()
    for c in changed:
        S.checker.locations[c] = {'ok': None, 'text': ''}
    S.save_settings()
    return h._send(200, {'ok': True, 'settings': S.settings})


@route('POST', '/api/open')
def api_open(h, q):
    d = h._json()
    url = d.get('url') or HELIUM10
    if not isinstance(url, str) or not url.startswith('https://'):
        return h._send(400, {'error': 'bad url'})
    used = cp.open_url(url, S.settings.get('profile', ''))
    return h._send(200, {'ok': True, 'profile': used})


@route('POST', '/api/browser')
def api_browser(h, q):
    action = h._json().get('action')

    def report(fut):
        try:
            fut.result()
        except Exception as e:                                  # noqa: BLE001
            S.say('Browser action "%s" failed: %s' % (action, str(e).splitlines()[0][:150] if str(e) else e))
    if action == 'show':
        fut = S.run(S.checker.show())
    elif action == 'hide':
        fut = S.run(S.checker.hide())
    elif action == 'start':
        fut = S.run(S.checker.start())
    elif action == 'locations':
        async def all_locations():
            await S.checker.start()
            for c in ac.MARKETS:
                S.checker.locations[c] = {'ok': None, 'text': ''}
            await asyncio.gather(*(S.checker.ensure_location(c) for c in ac.MARKETS))
        fut = S.run(all_locations())
    elif action == 'restart':
        async def restart():
            await S.checker.close()
            await S.checker.start()
        fut = S.run(restart())
    else:
        return h._send(400, {'error': 'unknown action'})
    fut.add_done_callback(report)
    return h._send(200, {'ok': True})


@route('POST', '/api/profiles')
def api_profiles(h, q):
    S.profiles = cp.list_profiles()
    return h._send(200, S.profiles)


@route('POST', '/api/beat')
def api_beat(h, q):
    S.last_beat = time.time()
    return h._send(200, {'ok': True})


@route('POST', '/api/quit')
def api_quit(h, q):
    h._send(200, {'ok': True})
    threading.Thread(target=shutdown, daemon=True).start()


HTTPD = None
ICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#1f63d0"/>'
        b'<path d="M8 21l5-6 4 3 7-9" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def shutdown():
    log.info('shutting down')
    try:
        S.run(S.checker.close()).result(timeout=10)
    except Exception:
        pass
    try:
        S.db.close()
    except Exception:
        pass
    try:
        PORT_FILE.unlink()
    except OSError:
        pass
    if HTTPD:
        HTTPD.shutdown()


def watchdog():
    """Stop when the app window is closed (its process ends or it stops checking in)."""
    exited = None
    while True:
        time.sleep(2)
        win = S.window
        if win is not None and exited is None and win.poll() is not None:
            exited = time.time()
            # a quick exit means the window was handed to an app window that was already open: keep running
            if exited - S.started > 8:
                return shutdown()
        if time.time() - S.last_beat > 180:                     # no window has checked in for 3 minutes
            return shutdown()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def already_running():
    try:
        port = int(PORT_FILE.read_text())
        with socket.create_connection(('127.0.0.1', port), timeout=1) as c:
            c.sendall(b'GET /api/ping HTTP/1.0\r\nHost: 127.0.0.1:' + str(port).encode() + b'\r\n\r\n')
            if b'"ok"' in c.recv(4096):
                return port
    except (OSError, ValueError):
        pass
    return None


def main():
    global S, HTTPD
    setup_logging()
    no_window = '--no-window' in sys.argv
    port = already_running()
    if port and not no_window:                                   # bring the open app to the front instead
        cp.open_app_window('http://127.0.0.1:%d/' % port, APP_DIR / 'app-window')
        return
    S = State()
    port = int(os.environ.get('PC_PORT') or free_port())
    S.port = port
    HTTPD = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    HTTPD.daemon_threads = True
    PORT_FILE.write_text(str(port))
    url = 'http://127.0.0.1:%d/' % port
    log.info('started on %s', url)
    print('Product Checker running at', url, flush=True)
    S.run(S.checker.start())                                      # warm up the background browser
    threading.Thread(target=S.scan_imports, daemon=True, name='import-scan').start()
    if not no_window:
        S.window = cp.open_app_window(url, APP_DIR / 'app-window')
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        HTTPD.serve_forever()
    except KeyboardInterrupt:
        shutdown()


if __name__ == '__main__':
    main()
