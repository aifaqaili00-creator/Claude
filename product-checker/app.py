"""Product Checker: Amazon AU / UAE / US local-seller check + Helium 10 export ranking.

Starts a small local server and opens the app in its own window. Run with start.bat or:  python app.py
"""
import asyncio
import concurrent.futures
import json
import logging
import os
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
import file_rank as fr
import xray

HERE = Path(__file__).resolve().parent
APP_DIR = ac.APP_DIR
APP_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = APP_DIR / 'settings.json'
HISTORY_FILE = APP_DIR / 'history.json'
PORT_FILE = APP_DIR / 'port.txt'
HELIUM10 = 'https://members.helium10.com/'
VERSION = '2.0'

logging.basicConfig(filename=str(APP_DIR / 'app.log'), level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('pc')

DEFAULTS = {'profile': '', 'fast_days': 3, 'pages': 1, 'cache_hours': 6, 'top': 10,
            'locations': {c: m['location'] for c, m in ac.MARKETS.items()}}


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def clean(o):
    """Make data JSON-safe: NaN/inf -> None, numpy numbers -> Python numbers."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float):
        return None if o != o or o in (float('inf'), float('-inf')) else o
    if hasattr(o, 'item'):                                        # numpy scalar
        return clean(o.item())
    return o


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.settings = {**DEFAULTS, **load_json(SETTINGS_FILE, {})}
        self.profiles = cp.list_profiles()
        if not self.settings.get('profile') or self.settings['profile'] not in [p['id'] for p in self.profiles]:
            self.settings['profile'] = cp.pick_default(self.profiles)
        for c, v in (self.settings.get('locations') or {}).items():
            if c in ac.MARKETS and v:
                ac.MARKETS[c]['location'] = v
                ac.MARKETS[c]['expect'] = tuple({str(v).lower(), *ac.MARKETS[c]['expect']})
        self.history = load_json(HISTORY_FILE, [])
        self.cache = {}                                           # (market, keyword, pages, fast) -> summary
        for h in self.history:
            for s in h.get('results', []):
                self.cache[(s['market'], s['keyword'].lower(), h.get('pages', 1), s.get('fast_days', 3))] = s
        self.jobs = {}
        self.ranks = {}                                           # rank id -> DataFrame
        self.xrays = {}                                           # xray analysis id -> result
        self.messages = []                                        # recent log lines for the UI
        self.last_beat = time.time()
        self.window = None

        # one event loop thread owns the browser
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name='browser').start()
        self.checker = ac.Checker(log=self.say)

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
        jid = uuid.uuid4().hex[:10]
        job = {'id': jid, 'kind': kind, 'status': 'running', 'log': [], 'result': None, 'error': '', 'started': time.time()}
        self.jobs[jid] = job

        def progress(m):
            job['log'].append(m)

        def done(fut):
            try:
                job['result'] = fut.result()
                job['status'] = 'done'
            except Exception as e:
                log.exception('job failed')
                job['status'], job['error'] = 'error', str(e).splitlines()[0][:300]
                self.say('Check failed: %s' % job['error'])
        self.run(fn(progress)).add_done_callback(done)
        return jid

    # ---------- checks ----------
    def cached(self, code, kw, pages, fast):
        s = self.cache.get((code, kw.lower(), pages, fast))
        if s and time.time() - s['checked_at'] < self.settings['cache_hours'] * 3600:
            return {**s, 'cached': True}
        return None

    async def check_many(self, keyword, markets, refresh, progress):
        fast, pages = int(self.settings['fast_days']), int(self.settings['pages'])

        async def one(code):
            hit = None if refresh else self.cached(code, keyword, pages, fast)
            if hit:
                progress('%s: from the last %d hours (no new search)' % (ac.MARKETS[code]['name'], self.settings['cache_hours']))
                return hit
            progress('%s: searching "%s"...' % (ac.MARKETS[code]['name'], keyword))
            try:
                s = await self.checker.check(keyword, code, fast, pages, progress)
            except Exception as e:
                progress('%s failed: %s' % (ac.MARKETS[code]['name'], str(e).splitlines()[0][:150]))
                return {'market': code, 'name': ac.MARKETS[code]['name'], 'keyword': keyword, 'error': str(e).splitlines()[0][:200]}
            self.cache[(code, keyword.lower(), pages, fast)] = s
            return s
        results = await asyncio.gather(*(one(c) for c in markets))
        good = [r for r in results if 'error' not in r]
        if good:
            entry = {'id': uuid.uuid4().hex[:8], 'keyword': keyword, 'at': time.time(), 'pages': pages,
                     'results': good}
            with self.lock:
                self.history = [h for h in self.history
                                if not (h['keyword'].lower() == keyword.lower() and
                                        {r['market'] for r in h['results']} == {r['market'] for r in good})]
                self.history.insert(0, entry)
                self.history = self.history[:40]
                save_json(HISTORY_FILE, clean(self.history))
        return {'keyword': keyword, 'results': results}

    async def check_batch(self, items, progress):
        """Check several products (from a ranked file), 3 at a time."""
        fast, pages = int(self.settings['fast_days']), int(self.settings['pages'])
        sem = asyncio.Semaphore(3)
        out = {}

        async def one(it):
            async with sem:
                kw, code = it['search'], it['market']
                hit = self.cached(code, kw, pages, fast)
                if not hit:
                    try:
                        hit = await self.checker.check(kw, code, fast, pages)
                        self.cache[(code, kw.lower(), pages, fast)] = hit
                    except Exception as e:
                        out[it['key']] = {'error': str(e).splitlines()[0][:150]}
                        progress('"%s" failed' % kw)
                        return
                out[it['key']] = {k: hit[k] for k in ('fast', 'slow', 'total', 'level', 'verdict', 'fast_reviews_median',
                                                      'bought_top', 'search_url', 'keyword', 'location_ok')}
                progress('"%s": %d fast of %d' % (kw, hit['fast'], hit['total']))
        await asyncio.gather(*(one(i) for i in items))
        return out


S = None


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
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n else b''

    def _json(self):
        try:
            return json.loads(self._body() or b'{}')
        except ValueError:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ('/', '/index.html'):
                return self._send(200, (HERE / 'ui' / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            if u.path == '/api/state':
                S.last_beat = time.time()
                since = float(q.get('since') or 0)
                return self._send(200, {
                    'version': VERSION, 'settings': S.settings, 'profiles': S.profiles,
                    'browser': {'state': S.checker.state, 'error': S.checker.error, 'visible': S.checker.visible},
                    'locations': S.checker.locations,
                    'markets': {c: {'name': m['name'], 'currency': m['currency'], 'location': m['location'],
                                    'site': m['site']}
                                for c, m in ac.MARKETS.items()},
                    'targets': fr.TARGETS,
                    'messages': [m for m in S.messages if m['t'] > since],
                })
            if u.path == '/api/job':
                job = S.jobs.get(q.get('id', ''))
                return self._send(200 if job else 404, job or {'error': 'no such job'})
            if u.path == '/api/xray/watch':
                p = xray.find_new_export(float(q.get('since') or 0))
                return self._send(200, {'folder': str(xray.downloads_dir()),
                                        'found': {'path': str(p), 'name': p.name, 'mtime': p.stat().st_mtime} if p else None})
            if u.path == '/api/history':
                return self._send(200, [{'id': h['id'], 'keyword': h['keyword'], 'at': h['at'],
                                         'markets': [{'market': r['market'], 'fast': r['fast'], 'total': r['total'],
                                                      'level': r['level']} for r in h['results']]}
                                        for h in S.history])
            if u.path == '/api/history/item':
                h = next((h for h in S.history if h['id'] == q.get('id')), None)
                return self._send(200 if h else 404, {'keyword': h['keyword'], 'results': h['results']} if h else {})
            if u.path in ('/favicon.svg', '/favicon.ico'):
                return self._send(200, ICON, 'image/svg+xml')
            if u.path == '/api/ping':
                return self._send(200, {'ok': True})
            self._send(404, {'error': 'not found'})
        except Exception as e:
            log.exception('GET %s', self.path)
            self._send(500, {'error': str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == '/api/check':
                d = self._json()
                kw = ' '.join(str(d.get('keyword', '')).split())[:120]
                markets = [m for m in d.get('markets', []) if m in ac.MARKETS] or list(ac.MARKETS)
                if not kw:
                    return self._send(400, {'error': 'Type a product first.'})
                jid = S.new_job('check', lambda p: S.check_many(kw, markets, bool(d.get('refresh')), p))
                return self._send(200, {'job': jid})
            if u.path == '/api/check_batch':
                d = self._json()
                items = [i for i in d.get('items', []) if i.get('market') in ac.MARKETS and i.get('search')][:30]
                jid = S.new_job('batch', lambda p: S.check_batch(items, p))
                return self._send(200, {'job': jid})
            if u.path == '/api/rank':
                return self._rank(q)
            if u.path == '/api/xray':
                return self._xray_upload(q)
            if u.path == '/api/xray/load':
                return self._xray_load(self._json())
            if u.path == '/api/export':
                return self._export(self._json())
            if u.path == '/api/settings':
                d = self._json()
                for k in ('profile', 'fast_days', 'pages', 'cache_hours', 'top'):
                    if k in d:
                        S.settings[k] = d[k] if k == 'profile' else max(1, int(d[k]))
                if isinstance(d.get('locations'), dict):
                    for c, v in d['locations'].items():
                        if c in ac.MARKETS and str(v).strip():
                            v = str(v).strip()
                            if v != ac.MARKETS[c]['location']:
                                ac.MARKETS[c]['location'] = v
                                ac.MARKETS[c]['expect'] = (v.lower(),)
                                S.checker.locations[c] = {'ok': None, 'text': ''}
                            S.settings['locations'][c] = v
                S.save_settings()
                return self._send(200, {'ok': True, 'settings': S.settings})
            if u.path == '/api/open':
                d = self._json()
                url = d.get('url') or HELIUM10
                if not url.startswith('https://'):
                    return self._send(400, {'error': 'bad url'})
                used = cp.open_url(url, S.settings.get('profile', ''))
                return self._send(200, {'ok': True, 'profile': used})
            if u.path == '/api/browser':
                action = self._json().get('action')
                if action == 'show':
                    S.run(S.checker.show())
                elif action == 'hide':
                    S.run(S.checker.hide())
                elif action == 'start':
                    S.run(S.checker.start())
                elif action == 'locations':
                    async def all_locations():
                        await S.checker.start()
                        for c in ac.MARKETS:
                            S.checker.locations[c] = {'ok': None, 'text': ''}
                        await asyncio.gather(*(S.checker.ensure_location(c) for c in ac.MARKETS))
                    S.run(all_locations())
                elif action == 'restart':
                    async def restart():
                        await S.checker.close()
                        await S.checker.start()
                    S.run(restart())
                return self._send(200, {'ok': True})
            if u.path == '/api/profiles':
                S.profiles = cp.list_profiles()
                return self._send(200, S.profiles)
            if u.path == '/api/beat':
                S.last_beat = time.time()
                return self._send(200, {'ok': True})
            if u.path == '/api/quit':
                self._send(200, {'ok': True})
                threading.Thread(target=shutdown, daemon=True).start()
                return
            self._send(404, {'error': 'not found'})
        except Exception as e:
            log.exception('POST %s', self.path)
            self._send(500, {'error': str(e).splitlines()[0][:300]})

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
                mk, top, all_, summary = fr.rank(path, q.get('market', 'auto'), int(q.get('top') or 10))
            except Exception as e:
                return self._send(400, {'error': str(e)})
        rid = uuid.uuid4().hex[:8]
        S.ranks[rid] = all_
        counts = all_['verdict'].value_counts().to_dict()
        return self._send(200, {'id': rid, 'file': name, 'market': mk, 'summary': summary, 'counts': counts,
                                'total': len(all_), 'target': fr.TARGETS[mk], 'rows': fr.to_records(all_.head(100))})

    def _xray(self, path, q):
        try:
            a = xray.analyse(path, q.get('market') or 'auto', (q.get('keyword') or '').strip())
        except Exception as e:
            log.exception('xray')
            return self._send(400, {'error': str(e).splitlines()[0][:300]})
        a['id'] = uuid.uuid4().hex[:8]
        S.xrays[a['id']] = a
        S.xrays = dict(list(S.xrays.items())[-20:])
        return self._send(200, a)

    def _xray_upload(self, q):
        name = os.path.basename(q.get('name') or 'xray.csv')
        if os.path.splitext(name)[1].lower() not in ('.csv', '.xlsx', '.xls', '.xlsm'):
            return self._send(400, {'error': 'Please use the .csv (or .xlsx) file that Xray exports.'})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            Path(path).write_bytes(self._body())
            return self._xray(path, q)

    def _xray_load(self, d):
        """Analyse an export the watcher found. Only files inside the Downloads folder are allowed."""
        folder = xray.downloads_dir().resolve()
        path = Path(str(d.get('path') or '')).resolve()
        if folder not in path.parents or not path.is_file():
            return self._send(400, {'error': 'That file is not in your Downloads folder.'})
        return self._xray(str(path), d)

    def _export(self, q):
        import pandas as pd
        tmp = os.path.join(tempfile.gettempdir(), 'pc_export_%s.xlsx' % uuid.uuid4().hex[:6])
        if q.get('kind') == 'rank' and q.get('id') in S.ranks:
            df = S.ranks[q['id']].copy()
            fast = q.get('fast') or {}                               # delivery results from the UI, by ASIN
            extra = []
            if fast:
                df['fast_sellers'] = df['asin'].map(lambda a: (fast.get(a) or {}).get('fast'))
                df['local_verdict'] = df['asin'].map(lambda a: (fast.get(a) or {}).get('verdict'))
                extra = [('fast_sellers', 'Fast sellers'), ('local_verdict', 'Local sellers')]
            fr.export_excel(df, tmp, extra)
            name = 'Top_products.xlsx'
        elif q.get('kind') == 'check' and q.get('job') in S.jobs:
            res = (S.jobs[q['job']].get('result') or {}).get('results', [])
            with pd.ExcelWriter(tmp, engine='openpyxl') as xw:
                pd.DataFrame([{k: r.get(k) for k in ('name', 'location', 'total', 'fast', 'slow', 'unknown',
                                                     'sponsored', 'fast_reviews_median', 'bought_top', 'verdict')}
                              for r in res if 'error' not in r]).to_excel(xw, sheet_name='Summary', index=False)
                rows = [x for r in res for x in r.get('rows', [])]
                pd.DataFrame(rows).drop(columns=['image'], errors='ignore').to_excel(xw, sheet_name='Listings', index=False)
            name = 'Delivery_check_%s.xlsx' % '_'.join((S.jobs[q['job']]['result'] or {}).get('keyword', 'x').split())
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


HTTPD = None
ICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#1f4fd6"/>'
        b'<path d="M9 17l5 5 9-11" fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def shutdown():
    log.info('shutting down')
    try:
        S.run(S.checker.close()).result(timeout=10)
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
            c.sendall(b'GET /api/ping HTTP/1.0\r\n\r\n')
            if b'"ok"' in c.recv(4096):
                return port
    except (OSError, ValueError):
        pass
    return None


def main():
    global S, HTTPD
    no_window = '--no-window' in sys.argv
    port = already_running()
    if port and not no_window:                                   # bring the open app to the front instead
        cp.open_app_window('http://127.0.0.1:%d/' % port, APP_DIR / 'app-window')
        return
    S = State()
    S.started = time.time()
    port = int(os.environ.get('PC_PORT') or free_port())
    HTTPD = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    HTTPD.daemon_threads = True
    PORT_FILE.write_text(str(port))
    url = 'http://127.0.0.1:%d/' % port
    log.info('started on %s', url)
    print('Product Checker running at', url, flush=True)
    S.run(S.checker.start())                                      # warm up the background browser
    if not no_window:
        S.window = cp.open_app_window(url, APP_DIR / 'app-window')
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        HTTPD.serve_forever()
    except KeyboardInterrupt:
        shutdown()


if __name__ == '__main__':
    main()
