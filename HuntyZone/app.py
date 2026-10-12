"""Product Checker: Amazon AU / UAE / US research desk.

Starts a small local server and opens the app in its own window. Run with start.bat or:  python app.py
"""
import asyncio
import json
import logging
import logging.handlers
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape as html_escape
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import amazon_check as ac
import analysis
import board
import chrome_profiles as cp
import collectors
import config
import file_rank as fr
import fxrates
import ideas
import importer
import mailer
import product
import reports
import scheduler
import singleton
import storage
import throttle
import trends
import winsys
import xray
from jobs import JobManager

HERE = Path(__file__).resolve().parent
UI_DIR = HERE / 'ui'
APP_DIR = config.APP_DIR
SETTINGS_FILE = APP_DIR / 'settings.json'
HISTORY_FILE = APP_DIR / 'history.json'                       # v5 only: imported into market.db on first start
DB_FILE = APP_DIR / 'market.db'
PORT_FILE = APP_DIR / 'port.txt'
MAIL_SECRET = APP_DIR / 'mail.secret'                        # the email app password, encrypted for this Windows user
BOARD_WAKE = threading.Event()                               # wakes the reminder sender early ("Send reminder now")
HELIUM10 = 'https://members.helium10.com/'
VERSION = config.VERSION
QUALIFY_MAX = 12                                             # products per Qualify press on the radar / ideas
ASIN_RX = re.compile(r'[A-Z0-9]{10}')
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


PDF_PARTS = ('summary', 'sellers', 'money', 'demand', 'competition', 'reviews', 'markets', 'data')
A4_CONTENT_PX = 718                                          # A4 width less 10 mm margins, in CSS pixels


def pdf_name(kw, market, day=None):
    """'dog bed - Amazon Australia - 2026-10-12.pdf', without characters Windows does not allow in file names."""
    safe = ' '.join(re.sub(r'[<>:"/\\|?*\x00-\x1f]+', ' ', kw).split()).strip(' .')[:80] or 'report'
    return '%s - Amazon %s - %s.pdf' % (safe, ac.MARKETS[market]['name'], day or time.strftime('%Y-%m-%d'))


def downloads_dir():
    d = Path.home() / 'Downloads'
    return d if d.is_dir() else APP_DIR / 'reports'


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
        self.review_wall = {}                                     # market -> when its reviews page asked to sign in
        self.pdfs = {}                                            # id -> a PDF this app saved (only these can be opened)
        self.messages = []                                        # recent log lines for the UI
        self.last_beat = time.time()
        self.window = None
        self.token = secrets.token_urlsafe(24)                    # every API call must carry it
        self.port = None
        self.started = time.time()
        self.loop = asyncio.new_event_loop()
        if start_loop:                                            # one event loop thread owns the browser
            threading.Thread(target=self.loop.run_forever, daemon=True, name='browser').start()
        self.mode = 'window'                                      # or 'background' (no window, keeps refreshing)
        self.focused_at = 0
        self.gate = throttle.Gate(self.loop)                      # your own jobs always go first
        self.throttle = throttle.Throttle(throttle.Store(self.db), self.gate)
        self.checker = ac.Checker(log=self.say)
        self.checker.throttle = self.throttle
        self.trends = trends.BrowserTrends(self.checker, self.say)
        self.trends.throttle = self.throttle
        self.trends.breaker.load(self.db.read(storage.get_state, 'trends_breaker'))
        self.jobs = JobManager(self.run, self.say, self.gate.user_start, self.gate.user_end)
        self.collectors = collectors.Collectors(self)
        self.scheduler = scheduler.Scheduler(self.db, self.collectors.handlers(), self.throttle,
                                             conditions=self.pause_reason, on_run_done=self.after_run, say=self.say)
        self.sync_tasks()

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
        jobs = getattr(self, 'jobs', None)
        if jobs is not None and not ac.BACKGROUND.get():
            jobs.broadcast(msg)                 # e.g. "solve the captcha in the browser window" shows in the progress line

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

    # ---------- automatic refresh ----------
    def pause_reason(self):
        """Why background work should not run now (None = go ahead)."""
        if not self.settings.get('auto_refresh', True):
            return 'auto refresh is off'
        if self.settings.get('pause_on_battery', True) and winsys.on_battery():
            return 'on battery'
        return None

    def sync_tasks(self):
        self.scheduler.sync_tasks(collectors.task_specs(self.settings, self.db.read(storage.watches)))

    def window_focused(self):
        return time.time() - self.focused_at < 30

    async def after_run(self, run_id, results):
        fresh = await asyncio.to_thread(analysis.run, self, results)
        if fresh:
            self.say('%d new alert%s on Home.' % (len(fresh), '' if len(fresh) == 1 else 's'))
            await asyncio.to_thread(analysis.notify_new, self, fresh)
        try:
            await asyncio.to_thread(board.refresh_for, self.db, self.settings, results)
        except Exception:                                           # noqa: BLE001 - the board catches up next time
            log.exception('board hints refresh')

    async def refit_curve(self, market):
        """Fit the market's BSR -> units curve in a separate process, so the browser loop never stalls."""
        from engine import curve
        obs = await asyncio.to_thread(self.db.read, storage.calib_obs, market)
        if not obs:
            return None
        try:
            fit = await self.loop.run_in_executor(self.procs(), curve.fit_market, obs, market)
        except Exception:                                               # noqa: BLE001 - a broken pool: fit here instead
            log.exception('curve fit in a separate process failed')
            fit = await asyncio.to_thread(curve.fit_market, obs, market)
        await self.db.awrite(storage.save_curve, market, fit)
        return fit

    def procs(self):
        if not getattr(self, '_procs', None):
            from concurrent.futures import ProcessPoolExecutor
            self._procs = ProcessPoolExecutor(max_workers=1)
        return self._procs

    def apply_startup_setting(self):
        want = bool(self.settings.get('start_with_windows'))
        if want or winsys.run_at_login():
            winsys.set_run_at_login(want, winsys.login_command(HERE))

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

    async def refresh_trends(self, kw, geo, progress=None, force=False, related=True):
        """Google Trends for one term and country. Google refusing pauses Trends; that is reported, not raised."""
        try:
            if progress:
                progress('Google Trends: %s in %s...' % (kw, ac.MARKETS[geo]['name']))
            return await trends.refresh(self.db, self.trends, kw, geo, force=force, with_related=related)
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

    async def read_top(self, market, kw, progress=None, force=False):
        """Read what a keyword report needs about its best page-1 listings: their product pages (seller, brand, how
        it ships, size, reviews), the critical reviews of the top 5 while Amazon shows them to you, and the profile
        pages of sellers not read in a month. With `force` everything is read again. Stops at a block or captcha and
        keeps what it has; raises when nothing could be read. Returns how many pages were read."""
        name = ac.MARKETS[market]['name']
        w = await asyncio.to_thread(reports.who_sells, self.db, market, kw)
        top = [x['asin'] for x in w['listings']]
        read, err = 0, None

        async def attempt(what, coro):
            nonlocal read, err
            try:
                await coro
                read += 1
            except (ac.BlockedError, throttle.ThrottleError):
                raise
            except Exception as e:                          # noqa: BLE001 - one page failing is not fatal
                err = e
                log.info('%s not read: %s', what, e)
        try:
            pages = top if force else w['todo']['pages']
            for i, asin in enumerate(pages, 1):
                if progress:
                    progress('Reading listing %d of %d on Amazon %s...' % (i, len(pages), name))
                await attempt('%s %s' % (market, asin), self._read_page(market, asin))
            if time.time() - self.review_wall.get(market, 0) > 12 * 3600:
                reads = (await asyncio.to_thread(self.db.read, storage.reviews_of, market, top[:5]))[1]
                old = time.time() - reports.LISTING_MAX_AGE
                stale = [a for a in top[:5] if force or ((reads.get(a) or {}).get('critical_at') or 0) < old]
                for i, asin in enumerate(stale, 1):
                    if progress:
                        progress('Reading the negative reviews of listing %d of %d...' % (i, len(stale)))
                    try:
                        items = await product.read_critical_reviews(self.checker, asin, market)
                    except product.SignInNeeded:                # the product pages above gave their reviews
                        self.review_wall[market] = time.time()
                        break
                    except (ac.BlockedError, throttle.ThrottleError):
                        raise
                    except Exception as e:                      # noqa: BLE001
                        err = e
                        continue
                    if items:
                        await self.db.awrite(storage.save_reviews, market, asin, items, 'critical')
                        read += 1
            w = await asyncio.to_thread(reports.who_sells, self.db, market, kw)
            ids = list(dict.fromkeys(x['seller_id'] for x in w['listings'] if x['seller_id'])) if force else w['todo']['sellers']
            for i, sid in enumerate(ids, 1):
                if progress:
                    progress('Reading seller %d of %d on Amazon %s...' % (i, len(ids), name))
                await attempt('seller %s %s' % (market, sid), self._read_seller(market, sid))
        except (ac.BlockedError, throttle.ThrottleError) as e:          # stop, keep what we have
            err = e
            if progress:
                progress(str(e).splitlines()[0][:150])
        if err and not read:
            raise err
        return read

    async def _read_page(self, market, asin):
        p = await product.read_product(self.checker, asin, market, reviews=True)
        await self.db.awrite(storage.save_product, market, asin, p)
        if p.get('signed_in'):
            self.review_wall.pop(market, None)

    async def _read_seller(self, market, seller_id):
        await self.db.awrite(storage.save_seller, market, seller_id, await product.read_seller(self.checker, seller_id, market))

    def explain(self, keyword, results):
        """Add the one-line verdict and who sells it (from what is stored, nothing is fetched) to check results."""
        for r in results:
            if not r.get('error'):
                r['sentence'] = reports.sentence(self.db, r['market'], keyword, self.settings)
                w = reports.who_sells(self.db, r['market'], keyword)
                r['who'] = {'takeaway': w['takeaway'], 'known': w['known']['seller'], 'n': w['n']}
        return results

    async def make_pdf(self, market, kw, parts, progress=None):
        """Save the keyword report as a PDF in Downloads: the cover with the one-line summary and the chosen parts,
        drawn by a hidden Chrome or Edge from this app's own page. Returns {'id', 'name', 'folder'}."""
        if not self.checker.pw:
            from playwright.async_api import async_playwright
            self.checker.pw = await async_playwright().start()
        if progress:
            progress('Starting a hidden browser...')
        browser, first = None, None
        exe = os.environ.get('PRODUCT_CHECKER_BROWSER')
        for extra in [dict(executable_path=exe)] if exe else [dict(channel='chrome'), dict(channel='msedge'), {}]:
            try:
                browser = await self.checker.pw.chromium.launch(headless=True, **extra)
                break
            except Exception as e:                          # noqa: BLE001 - try the next browser
                first = first or e
        if not browser:
            raise RuntimeError('Could not start Chrome or Edge to draw the PDF (%s)' % str(first).splitlines()[0][:200])
        title = html_escape('Product Checker · “%s” · Amazon %s' % (kw, ac.MARKETS[market]['name']))
        try:
            page = await browser.new_page(viewport={'width': A4_CONTENT_PX, 'height': 1000})
            if progress:
                progress('Drawing the report...')
            await page.goto('http://127.0.0.1:%d/?print=%s#/k/%s/%s' % (self.port, ','.join(parts), market, quote(kw, safe='')))
            await page.wait_for_function("document.documentElement.dataset.ready === '1'", timeout=60000)
            await page.emulate_media(media='print')
            pdf = await page.pdf(
                format='A4', print_background=True, margin={'top': '12mm', 'bottom': '14mm', 'left': '10mm', 'right': '10mm'},
                display_header_footer=True, header_template='<span></span>',
                footer_template='<div style="font-size:8px;color:#667085;width:100%%;padding:0 10mm;display:flex;'
                                'justify-content:space-between"><span>%s</span><span><span class="pageNumber"></span> / '
                                '<span class="totalPages"></span></span></div>' % title)
        finally:
            await browser.close()
        folder = downloads_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path, n = folder / pdf_name(kw, market), 2
        while path.exists():
            path, n = folder / pdf_name(kw, market).replace('.pdf', ' (%d).pdf' % n), n + 1
        path.write_bytes(pdf)
        pid = uuid.uuid4().hex[:10]
        self.pdfs[pid] = path
        return {'id': pid, 'name': path.name, 'folder': str(folder)}

    async def read_reviews(self, market, asins, progress=None):
        """Read the critical reviews of these listings: from the listing's reviews page (the 10 most helpful 1-3 star
        reviews) while Amazon shows it, else from the reviews on its product page (which also gives its size).
        Amazon shows reviews pages only to signed-in shoppers; that is retried after 12 hours or once a product
        page shows you are signed in. Raises when no listing could be read. Returns how many were read."""
        read, err = 0, None
        for i, asin in enumerate(asins, 1):
            if progress:
                progress('Reading the negative reviews of listing %d of %d on Amazon %s...' % (i, len(asins), ac.MARKETS[market]['name']))
            try:
                if time.time() - self.review_wall.get(market, 0) > 12 * 3600:
                    try:
                        items = await product.read_critical_reviews(self.checker, asin, market)
                    except product.SignInNeeded:
                        self.review_wall[market] = time.time()
                        items = None
                    if items:                               # none at all: check the product page instead
                        await self.db.awrite(storage.save_reviews, market, asin, items, 'critical')
                        read += 1
                        continue
                p = await product.read_product(self.checker, asin, market, reviews=True)
                await self.db.awrite(storage.save_product, market, asin, p)
                if p.get('signed_in'):
                    self.review_wall.pop(market, None)
                read += 1
            except (ac.BlockedError, throttle.ThrottleError) as e:     # a block, captcha or pause: stop, keep what we have
                err = e
                if progress:
                    progress(str(e).splitlines()[0][:150])
                break
            except Exception as e:                          # noqa: BLE001 - one page failing is not fatal
                err = e
                log.info('reviews of %s %s not read: %s', market, asin, e)
        if err and not read:
            raise err
        return read

    async def check_batch(self, items, progress, refresh=False):
        """Check several products (a list you typed, a ranked file or ideas), 3 at a time."""
        fast, local, pages = self.days()
        sem = asyncio.Semaphore(3)
        out = {}
        run_id = await self.db.awrite(storage.start_run, 'batch', 'user')

        async def one(it):
            async with sem:
                kw, code = it['search'], it['market']
                hit = None if refresh else self.cached(code, kw, pages)
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
                    'market', 'fast_price_min', 'fast_price_max', 'price_med', 'status')}
                ps = hit.get('per_seller') or {}
                out[it['key']]['demand'] = ps.get('demand')                 # for the demand vs sellers bars on cards
                out[it['key']]['per_local_seller'] = ps.get('per_local_seller')
                out[it['key']]['overseas_share_of_sales'] = ps.get('overseas_share_of_sales')
                progress('"%s": %d fast, %d local, %d overseas of %d' % (kw, hit['fast'], hit['local'], hit['overseas'], hit['total']))
        await asyncio.gather(*(one(i) for i in items))
        n_fail = sum(1 for v in out.values() if 'error' in v)
        await self.db.awrite(storage.finish_run, run_id, 'done', len(out) - n_fail, n_fail)
        return out

    async def find_ideas(self, code, slugs, kinds, hide, refresh, progress, phrases=None):
        menu = self.idea_cats.get(code)
        if refresh or not menu or time.time() - menu['at'] > 24 * 3600:
            progress('Reading the %s category list...' % ac.MARKETS[code]['name'])
            got = await ideas.categories(self.checker, code)            # a captcha or block raises its own message
            if got:                                                      # an empty answer is never kept
                menu = self.idea_cats[code] = {'at': time.time(), 'cats': got}
        cats = menu['cats'] if menu else []
        safe = [c for c in cats if not c['risky']]
        chosen = [c for c in cats if c['slug'] in slugs] if slugs else (
            safe if any(c.get('default') for c in safe) else safe[:collectors.USER_CATEGORIES])
        if not chosen:
            raise RuntimeError('No categories found on Amazon %s. Try again in a minute.' % ac.MARKETS[code]['name'])
        if any(c.get('default') for c in chosen):
            progress("Amazon's category menu could not be read, so the built-in %s categories are used"
                     % ac.MARKETS[code]['name'])
        key = (code, tuple(sorted(c['slug'] for c in chosen)), tuple(sorted(kinds)))
        hit = self.idea_cache.get(key)
        if hit and not refresh and time.time() - hit['at'] < self.settings['cache_hours'] * 3600:
            progress('Using the scan from %d minutes ago' % ((time.time() - hit['at']) // 60))
            raw = hit
        else:
            raw = await ideas.scan(self.checker, code, chosen, kinds, None, progress)
            if not raw['items']:                                         # nothing read: say why, keep nothing
                raise RuntimeError('Could not read any Movers & Shakers or New Releases list on Amazon %s%s. '
                                   'Try again later, or pick other categories.' % (
                                       ac.MARKETS[code]['name'],
                                       " (its category menu could not be read either; a copy of the page is in the "
                                       "app folder's debug folder)" if any(c.get('default') for c in chosen) else ''))
            raw['at'] = time.time()
            found = {i['slug'] for i in raw['items']}                    # built-in slugs Amazon lacks: drop them
            if menu and any(c.get('default') and c['slug'] not in found for c in chosen):
                menu['cats'] = [c for c in menu['cats'] if not c.get('default') or c['slug'] in found] or menu['cats']
            self.idea_cache[key] = raw
            await self.keep_lists(code, raw['items'], raw['at'])
        # "hide risky" is applied when reading, so switching it never re-scrapes
        prange = fxrates.ranges(self.settings)[code] if self.settings.get('price_filter', True) else None
        stats = {}
        scored = await asyncio.to_thread(ideas.score_ideas, raw['items'], code, hide, prange,
                                         self.settings.get('exclude_tech', True), stats)
        disc = await asyncio.to_thread(reports.ideas_discovery, self.db, code, scored, self.settings, None, phrases)
        chosen_slugs = {c['slug'] for c in chosen}
        return {**{k: v for k, v in raw.items() if k != 'items'}, 'filtered': stats,
                'price_range': prange, 'all_categories': [{**c, 'on': c['slug'] in chosen_slugs} for c in cats],
                'kinds': kinds, **disc}

    async def keep_lists(self, market, items, ts):
        """Keep a list scan you ran yourself in the list history, one snapshot per list, so the radar and the scores
        see it too. A database problem is logged, never shown as a failed scan."""
        lists = {}
        for it in items:
            lists.setdefault((it['kind'], it['slug']), (it.get('category'), []))[1].append(it)

        def fn(conn):
            for (kind, slug), (category, got) in lists.items():
                storage.save_list(conn, market, kind, slug, category, got, 'ok' if len(got) >= 30 else 'partial', None, ts)
        try:
            await self.db.awrite(fn)
        except Exception:                                           # noqa: BLE001
            log.exception('could not keep the list scan')


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
        """The request body, read once (a second read would wait on the socket until it times out)."""
        if getattr(self, '_body_cache', None) is None:
            n = int(self.headers.get('Content-Length') or 0)
            self._body_cache = self.rfile.read(n) if n else b''
        return self._body_cache

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
        self._body_cache = None                         # one handler can serve several requests on a kept-alive socket
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
            mk = q.get('market') or 'auto'
            a = xray.analyse(path, mk, (q.get('keyword') or '').strip(),
                             registered=bool(S.settings.get('registered', {}).get(mk)))
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
        mine = S.settings.get('price_currency', 'USD')

        def conv(v, market):
            code = fxrates.CURRENCY.get(market)
            return round(fxrates.convert(v, code, mine), 2) if isinstance(v, (int, float)) and code else None
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
                fr.excel_safe(pd.DataFrame([{**{k: r.get(k) for k in (
                    'name', 'location', 'total', 'fast', 'slow', 'unknown', 'local', 'overseas', 'overseas_intl', 'overseas_slow',
                    'origin_unknown', 'sponsored', 'fast_reviews_median', 'bought_top', 'verdict', 'currency')},
                    'typical_price': r.get('price_med'), 'typical_price_' + mine: conv(r.get('price_med'), r.get('market'))}
                    for r in res if 'error' not in r])).to_excel(xw, sheet_name='Summary', index=False)
                rows = [{**x, 'currency': r.get('currency'), 'price_' + mine: conv(x.get('price'), r.get('market'))}
                        for r in res for x in r.get('rows', [])]
                fr.excel_safe(pd.DataFrame(rows).drop(columns=['image'], errors='ignore')).to_excel(xw, sheet_name='Listings', index=False)
            name = 'Delivery_check_%s.xlsx' % '_'.join(job['result'].get('keyword', 'x').split())
        elif q.get('kind') == 'batch':
            out = []
            for r in (q.get('rows') if isinstance(q.get('rows'), list) else [])[:200]:
                if not isinstance(r, dict) or r.get('market') not in ac.MARKETS:
                    continue
                n = lambda k: r[k] if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool) else None  # noqa: E731
                share = n('overseas_share')
                out.append({'Product': str(r.get('product') or '')[:120], 'Market': ac.MARKETS[r['market']]['name'],
                            'Local sellers': n('local'), 'Overseas sellers': n('overseas'), 'Fast (local stock)': n('fast'),
                            'Listings': n('total'), 'Buyers / month': n('demand'), 'Buyers per local seller': n('per_local_seller'),
                            'Overseas share of sales %': round(share * 100) if share is not None else None,
                            'Typical price': n('price_med'), 'Currency': ac.MARKETS[r['market']]['currency'],
                            'Typical price (%s)' % fxrates.SYMBOL[mine].strip(): conv(n('price_med'), r['market']),
                            'Verdict': str(r.get('verdict') or r.get('error') or '')[:300]})
            if not out:
                return self._send(400, {'error': 'Nothing to save yet. Check some products first.'})
            fr.excel_safe(pd.DataFrame(out)).to_excel(tmp, index=False, sheet_name='Products')
            name = 'Products_check.xlsx'
        elif q.get('kind') == 'xray' and q.get('id') in S.xrays:
            a = S.xrays[q['id']]
            xray.export_excel(a, tmp)
            name = 'Xray_analysis_%s_%s.xlsx' % (a['market'], '_'.join(a['keyword'].split()) or 'market')
        else:
            return self._send(404, {'error': 'nothing to export'})
        data = Path(tmp).read_bytes()
        os.remove(tmp)
        return self._send(200, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                          {'Content-Disposition': attachment(name)})


def attachment(name):
    """Content-Disposition for a download. Headers are Latin-1, so a keyword in Arabic (or with quotes) goes in the
    RFC 5987 form, next to a plain fallback name."""
    plain = re.sub(r'[^A-Za-z0-9.-]+', '_', name).strip('_') or 'export.xlsx'
    return 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (plain, quote(name))


# ---------------- GET routes ----------------
@route('GET', '/api/state')
def api_state(h, q):
    S.last_beat = time.time()
    fxrates.refresh_soon()                              # at most once a day, in the background
    since = float(q.get('since') or 0)
    return h._send(200, {
        'version': VERSION, 'settings': S.settings, 'profiles': S.profiles,
        'browser': {'state': S.checker.state, 'error': S.checker.error, 'visible': S.checker.visible},
        'locations': S.checker.locations,
        'markets': {c: {'name': m['name'], 'currency': m['currency'], 'location': m['location'], 'site': m['site']}
                    for c, m in ac.MARKETS.items()},
        'targets': fr.TARGETS,
        'jobs_running': S.jobs.running(),
        'mode': S.mode,
        'scheduler': S.scheduler.status(),
        'alerts_unseen': (S.db.one('SELECT COUNT(*) AS n FROM alert WHERE seen_at IS NULL AND dismissed_at IS NULL') or {}).get('n', 0),
        'messages': [m for m in S.messages if m['t'] > since],
        'fx': fxrates.summary(S.settings),
        'board_rev': board.rev(),
    })


@route('POST', '/api/fx/refresh')
def api_fx_refresh(h, q):
    """Fetch today's exchange rates now (they are also refreshed by themselves once a day)."""
    fxrates.refresh(force=True)
    return h._send(200, fxrates.summary(S.settings))


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
    return h._send(200, {'keyword': snaps[0]['keyword'], 'results': S.explain(snaps[0]['keyword'], [S.resummarize(r) for r in snaps])})


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


@route('GET', '/api/report/sellers')
def api_report_sellers(h, q):
    """Who sells a keyword's best page-1 listings: seller, country, brand, how it ships, share of the sales."""
    market = q.get('market') if q.get('market') in ac.MARKETS else 'AU'
    kw = ' '.join(str(q.get('kw') or '').split())[:120]
    if not config.norm_kw(kw):
        return h._send(400, {'error': 'Which keyword?'})
    return h._send(200, reports.who_sells(S.db, market, kw))


@route('POST', '/api/report/top')
def api_report_top(h, q):
    """Read the keyword's best listings on Amazon (product pages, negative reviews, sellers); returns who sells them."""
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else 'AU'
    kw = ' '.join(str(d.get('kw', '')).split())[:120]
    if not config.norm_kw(kw):
        return h._send(400, {'error': 'Which keyword?'})

    async def work(progress):
        await S.read_top(market, kw, progress, force=bool(d.get('force')))
        return await asyncio.to_thread(reports.who_sells, S.db, market, kw)
    return h._send(200, {'job': S.new_job('listings', work)})


@route('POST', '/api/report/pdf')
def api_report_pdf(h, q):
    """Save the keyword report as a PDF in Downloads, with the parts you chose."""
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else 'AU'
    kw = ' '.join(str(d.get('kw', '')).split())[:120]
    chosen = d.get('sections') if isinstance(d.get('sections'), list) else []
    parts = [p for p in PDF_PARTS if p in chosen]
    if not config.norm_kw(kw):
        return h._send(400, {'error': 'Which keyword?'})
    if not parts:
        return h._send(400, {'error': 'Pick at least one part of the report.'})
    return h._send(200, {'job': S.new_job('pdf', lambda p: S.make_pdf(market, kw, parts, p))})


@route('POST', '/api/pdf/open')
def api_pdf_open(h, q):
    """Open a PDF this app saved, or show it in its folder."""
    d = h._json()
    path = S.pdfs.get(str(d.get('id') or ''))
    if not path or not path.exists():
        return h._send(404, {'error': 'That PDF is not there any more. Make it again.'})
    if d.get('folder'):
        subprocess.Popen(['explorer', '/select,', str(path)])
    else:
        os.startfile(str(path))                                     # noqa: S606 - a file this app wrote
    return h._send(200, {'ok': True})


def _review_target(d):
    """Market, keyword and ASIN of a reviews request: a keyword's best listings, or one listing (asin)."""
    market = d.get('market') if d.get('market') in ac.MARKETS else 'AU'
    asin = str(d.get('asin') or '').strip().upper()[:12]
    kw = None if asin else ' '.join(str(d.get('kw') or '').split())[:120]
    return market, kw, asin if asin.isalnum() else None


@route('GET', '/api/report/reviews')
def api_report_reviews(h, q):
    """The top negative reviews of a keyword's best listings, or of one listing."""
    market, kw, asin = _review_target(q)
    if not asin and not config.norm_kw(kw or ''):
        return h._send(400, {'error': 'Which keyword or product?'})
    return h._send(200, reports.reviews_for(S.db, market, kw, asin))


@route('POST', '/api/report/reviews')
def api_report_reviews_read(h, q):
    """Read the negative reviews on Amazon: of the listings not read in two weeks, or of all of them (force)."""
    d = h._json()
    market, kw, asin = _review_target(d)
    if not asin and not config.norm_kw(kw or ''):
        return h._send(400, {'error': 'Which keyword or product?'})

    async def work(progress):
        r = await asyncio.to_thread(reports.reviews_for, S.db, market, kw, asin)
        todo = r['asins'] if d.get('force') else r['todo']
        if todo:
            await S.read_reviews(market, todo, progress)
        return await asyncio.to_thread(reports.reviews_for, S.db, market, kw, asin)
    return h._send(200, {'job': S.new_job('reviews', work)})


@route('POST', '/api/parse_size')
def api_parse_size(h, q):
    """Size and weight from text pasted from a supplier page, e.g. 'Single package size: 30X20X10 cm'."""
    dims, kg = product.parse_size_text(str(h._json().get('text') or '')[:3000])
    return h._send(200, {'dims_cm': dims, 'kg': kg})


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

    async def work(progress):
        out = await S.check_many(kw, markets, bool(d.get('refresh')), progress)
        await asyncio.to_thread(S.explain, kw, out['results'])
        return out
    return h._send(200, {'job': S.new_job('check', work)})


@route('POST', '/api/check_batch')
def api_check_batch(h, q):
    d = h._json()
    items = []
    for i in d.get('items', []) if isinstance(d.get('items'), list) else []:
        if isinstance(i, dict) and i.get('market') in ac.MARKETS and config.norm_kw(i.get('search')):
            items.append({'key': str(i.get('key', ''))[:200], 'market': i['market'],
                          'search': ' '.join(str(i['search']).split())[:120]})
    jid = S.new_job('batch', lambda p: S.check_batch(items[:60], p, bool(d.get('refresh'))))
    return h._send(200, {'job': jid})


@route('POST', '/api/ideas')
def api_ideas(h, q):
    d = h._json()
    code = d.get('market') if d.get('market') in ac.MARKETS else 'US'
    kinds = [k for k in d.get('kinds', ['movers', 'new']) if k in ideas.LISTS] or ['movers', 'new']
    slugs = [str(s) for s in d.get('slugs') or []][:20]
    phrases = typed_phrases(d)
    jid = S.new_job('ideas', lambda p: S.find_ideas(code, slugs, kinds, d.get('hide_risky', True) is not False,
                                                    bool(d.get('refresh')), p, phrases))
    return h._send(200, {'job': jid})


def typed_phrases(d):
    """The search words you typed for products: {asin: words} from the request's 'phrases'."""
    out = {}
    for a, words in list((d.get('phrases') if isinstance(d.get('phrases'), dict) else {}).items())[:500]:
        k = config.norm_kw(words)[:120] if isinstance(words, str) else ''
        if k and ASIN_RX.fullmatch(str(a)):
            out[a] = k
    return out


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
    S.sync_tasks()
    if 'start_with_windows' in d:
        S.apply_startup_setting()
    if d.get('auto_refresh'):
        S.scheduler.poke()
    return h._send(200, {'ok': True, 'settings': S.settings, 'fx': fxrates.summary(S.settings)})


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
            for c in ac.MARKETS:                                # one at a time: Amazon's popups need the focused tab
                await S.checker.ensure_location(c, manual=True)
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
    if h._json().get('focused'):
        S.focused_at = time.time()
    return h._send(200, {'ok': True})


# ---------------- automatic refresh, watchlist, alerts ----------------
@route('GET', '/api/home')
def api_home(h, q):
    out = reports.home(S.db, S)
    out['opportunities'] = reports.opportunities(S.db, S.settings)
    return h._send(200, out)


@route('GET', '/api/radar')
def api_radar(h, q):
    market = q.get('market') if q.get('market') in ac.MARKETS else 'US'
    return h._send(200, {**reports.radar(S.db, market, settings=S.settings, **reports.radar_filters(S.settings, market)),
                         'trends': reports.search_trends(S.db), 'rising': reports.google_rising(S.db, market, S.settings)})


@route('GET', '/api/opportunity_map')
def api_opportunity_map(h, q):
    market = q.get('market') if q.get('market') in ac.MARKETS else 'AU'
    return h._send(200, reports.opportunity_map(S.db, market))


@route('GET', '/api/watchlist')
def api_watchlist(h, q):
    return h._send(200, reports.watchlist(S.db, S.settings))


@route('GET', '/api/health')
def api_health(h, q):
    return h._send(200, reports.health(S.db, S))


@route('GET', '/api/alerts')
def api_alerts(h, q):
    return h._send(200, S.db.read(storage.list_alerts, time.time() - 30 * 86400, False, 200))


@route('POST', '/api/watch')
def api_watch(h, q):
    d = h._json()
    kind = d.get('kind') if d.get('kind') in storage.WATCH_KINDS else 'keyword'
    target = ' '.join(str(d.get('target') or '').split())[:120]
    markets = [m for m in (d.get('markets') or [d.get('market')]) if m in ac.MARKETS]
    if not target or not markets:
        return h._send(400, {'error': 'What should be watched, and where?'})
    ids = []
    for m in markets:
        if kind == 'keyword':
            S.db.write(storage.upsert_keyword, target)
        ids.append(S.db.write(storage.add_watch, kind, m, target, target))
    S.sync_tasks()
    S.scheduler.poke()
    return h._send(200, {'ok': True, 'ids': ids})


@route('POST', '/api/watch/update')
def api_watch_update(h, q):
    d = h._json()
    try:
        S.db.write(storage.update_watch, int(d.get('id') or 0), **{k: v for k, v in d.items() if k != 'id'})
    except ValueError as e:
        return h._send(400, {'error': str(e)})
    S.sync_tasks()
    return h._send(200, {'ok': True})


@route('POST', '/api/watch/remove')
def api_watch_remove(h, q):
    S.db.write(storage.remove_watch, int(h._json().get('id') or 0))
    S.sync_tasks()
    return h._send(200, {'ok': True})


@route('POST', '/api/alerts/mark')
def api_alerts_mark(h, q):
    d = h._json()
    ids = [int(i) for i in d.get('ids') or [] if str(i).isdigit()][:500]
    S.db.write(storage.mark_alerts, ids, 'dismissed_at' if d.get('dismiss') else 'seen_at')
    return h._send(200, {'ok': True})


@route('POST', '/api/scheduler')
def api_scheduler(h, q):
    d = h._json()
    a = d.get('action')
    if a == 'run_now':
        S.scheduler.run_all_now()
    elif a == 'pause':
        S.scheduler.pause(max(0.25, min(72.0, float(d.get('hours') or 1))) * 3600)
    elif a == 'resume':
        S.scheduler.resume()
    else:
        return h._send(400, {'error': 'unknown action'})
    return h._send(200, S.scheduler.status())


@route('POST', '/api/source/clear')
def api_source_clear(h, q):
    src = str(h._json().get('source') or '')
    if src == 'google':
        S.trends.breaker.ok()
    if src in throttle.SOURCES:
        S.throttle.clear(src)
    S.scheduler.poke()
    return h._send(200, {'ok': True})


@route('GET', '/api/report/product')
def api_report_product(h, q):
    market = q.get('market') if q.get('market') in ac.MARKETS else 'US'
    asin = str(q.get('asin') or '').strip().upper()[:12]
    if not asin.isalnum():
        return h._send(400, {'error': 'Which product (ASIN)?'})
    return h._send(200, reports.product_report(S.db, market, asin, S.settings))


@route('POST', '/api/report/product/refresh')
def api_report_product_refresh(h, q):
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else 'US'
    asin = str(d.get('asin') or '').strip().upper()[:12]
    if not asin.isalnum():
        return h._send(400, {'error': 'Which product (ASIN)?'})

    async def work(progress):
        progress('Reading the product page on Amazon %s...' % ac.MARKETS[market]['name'])
        p = await product.read_product(S.checker, asin, market, reviews=True)
        await S.db.awrite(storage.save_product, market, asin, p)
        progress('Updating the sales curve...')
        await S.refit_curve(market)
        return await asyncio.to_thread(reports.product_report, S.db, market, asin, S.settings)
    return h._send(200, {'job': S.new_job('product', work)})


@route('GET', '/api/report/money')
def api_report_money(h, q):
    market = q.get('market') if q.get('market') in ac.MARKETS else 'AU'
    kw = ' '.join(str(q.get('kw') or '').split())[:120]
    if not config.norm_kw(kw):
        return h._send(400, {'error': 'Which keyword?'})
    return h._send(200, reports.money(S.db, market, kw, S.settings))


@route('POST', '/api/profit')
def api_profit(h, q):
    """The profit calculator: unit economics, the price grid with fee steps, and the highest product cost for 20%."""
    from engine import profit as pf
    fxrates.rates()                                     # the fee tables convert with today's rates too
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else 'US'

    def num(k, lo=0.0, hi=1e6):
        try:
            return max(lo, min(hi, float(d[k]))) if d.get(k) not in (None, '') else None
        except (TypeError, ValueError):
            return None
    dims = [num('l'), num('w'), num('h')]
    cost_cur = d.get('cogs_currency') if d.get('cogs_currency') in fxrates.CURRENCIES else 'USD'
    cost = num('cogs')
    cogs_usd = fxrates.convert(cost, cost_cur, 'USD') if cost is not None else (num('cogs_usd') or 0.0)
    inp = {'market': market, 'price': num('price', 0.01), 'category': str(d.get('category') or '')[:80],
           'dims_cm': dims if all(dims) else None, 'kg': num('kg', 0.001, 500), 'cogs_usd': cogs_usd,
           'freight_mode': 'air' if d.get('freight_mode') == 'air' else 'sea', 'duty_pct': num('duty_pct', 0, 1),
           'tacos_pct': num('tacos_pct', 0, 1), 'registered': bool(d.get('registered')),
           'fba_fee_override': num('fba_fee'), 'date': config.market_today(market).isoformat()}
    inp = {k: v for k, v in inp.items() if v is not None}
    e = pf.unit_economics(inp)
    if not e:
        return h._send(400, {'error': 'Enter a selling price.'})
    if d.get('kw') and d.get('save_cogs'):
        kw = config.norm_kw(d['kw'])
        S.db.write(lambda c: c.execute("UPDATE watch SET cogs=? WHERE kind='keyword' AND market=? AND target=?",
                                       (inp.get('cogs_usd'), market, kw)))
    max_usd = pf.max_cogs(inp, 0.20)
    return h._send(200, {'economics': e, 'grid': pf.price_grid(inp), 'max_cogs_20': max_usd, 'cost_currency': cost_cur,
                         'max_cogs_20_cost': round(fxrates.convert(max_usd, 'USD', cost_cur), 2) if max_usd is not None else None})


@route('GET', '/api/calibration')
def api_calibration(h, q):
    return h._send(200, reports.calibration(S.db))


@route('POST', '/api/radar/scan')
def api_radar_scan(h, q):
    """Read the market's Movers & Shakers and New Releases now (your own job, not background)."""
    market = h._json().get('market')
    market = market if market in ac.MARKETS else 'US'

    async def work(progress):
        progress('Reading Movers & Shakers and New Releases on Amazon %s...' % ac.MARKETS[market]['name'])
        out = await S.collectors.lists({'kind': 'lists', 'market': market, 'target': 'all'}, progress)
        progress((out.get('note') or '') + ' · grouping similar products…')
        await asyncio.to_thread(analysis.run, S, [])
        return await asyncio.to_thread(lambda: {**reports.radar(S.db, market, settings=S.settings,
                                                                **reports.radar_filters(S.settings, market)),
                                                'trends': reports.search_trends(S.db),
                                                'rising': reports.google_rising(S.db, market, S.settings),
                                                'scan_note': out.get('note') or ''})
    return h._send(200, {'job': S.new_job('radar', work)})


@route('POST', '/api/discovery/qualify')
def api_discovery_qualify(h, q):
    """Read what the opportunity score misses for a few radar or idea products (at most QUALIFY_MAX): the phrase
    shoppers type, its Google trend, whether the markets it could sell in next know the phrase, and the product page.
    Your own job: no daily budget, but a source that is pausing is skipped. Searching the other markets is the Check
    button's job."""
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else 'US'
    asins = []
    for a in d.get('asins') if isinstance(d.get('asins'), list) else []:
        a = str(a).strip().upper()
        if ASIN_RX.fullmatch(a) and a not in asins:
            asins.append(a)
    if not asins:
        return h._send(400, {'error': 'Which products?'})
    todo = asins[:QUALIFY_MAX]
    phrases = typed_phrases(d)

    async def work(progress):
        out = await S.collectors.qualify_products(market, todo, progress, phrases=phrases)
        return {**out, 'market': market, 'asins': todo, 'skipped': len(asins) - len(todo)}
    return h._send(200, {'job': S.new_job('qualify', work)})


@route('POST', '/api/pref')
def api_pref(h, q):
    """Your choices for one product: your supplier price, your search phrase, a star, or Not for me."""
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else None
    asin = str(d.get('asin') or '').strip().upper()
    s = d.get('set') if isinstance(d.get('set'), dict) else {}
    if not market or not ASIN_RX.fullmatch(asin):
        return h._send(400, {'error': 'Which product?'})
    fields = {}
    if 'cost' in s:
        try:
            cost = float(s['cost']) if s['cost'] not in (None, '') else None
        except (TypeError, ValueError):
            cost = -1.0
        if cost is not None and not 0 <= cost <= 100000:
            return h._send(400, {'error': 'Type the supplier price per piece as a number.'})
        cur = s.get('cost_currency') if s.get('cost_currency') in fxrates.CURRENCIES else 'USD'
        fields['cogs_usd'] = round(fxrates.convert(cost, cur, 'USD'), 4) if cost is not None else None
    if 'phrase' in s:
        fields['phrase'] = config.norm_kw(s['phrase'])[:120] if isinstance(s['phrase'], str) else None
    for k in ('liked', 'hidden'):
        if k in s:
            fields[k] = bool(s[k])
    if not fields:
        return h._send(400, {'error': 'Nothing to change.'})
    return h._send(200, {'ok': True, 'pref': S.db.write(storage.set_pref, market, asin, **fields)})


def boom_arg(d):
    """The request's boom {'trend_score', 'cluster_surge'}, each clipped to 0-100 or None."""
    b = d.get('boom') if isinstance(d.get('boom'), dict) else {}
    boom = {}
    for k in ('trend_score', 'cluster_surge'):
        try:
            boom[k] = max(0.0, min(100.0, float(b[k]))) if b.get(k) is not None else None
        except (TypeError, ValueError):
            boom[k] = None
    return boom


@route('POST', '/api/discovery/variants')
def api_discovery_variants(h, q):
    """Find the underserved version of a product's phrase: Amazon's longer suggestions for it, searched in the
    markets it could sell in next and ranked by their gap score (your own job)."""
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else 'US'
    phrase = config.norm_kw(d.get('phrase') if isinstance(d.get('phrase'), str) else '')[:120]
    if not phrase:
        return h._send(400, {'error': 'Which search phrase?'})
    boom = boom_arg(d)
    return h._send(200, {'job': S.new_job('variants', lambda p: S.collectors.underserved(market, phrase, boom, p))})


@route('POST', '/api/launch')
def api_launch(h, q):
    """Where to launch: the same product, supplier price and first order compared across the markets searched."""
    d = h._json()
    market = d.get('market') if d.get('market') in ac.MARKETS else 'US'
    kw = ' '.join(str(d.get('kw') or '').split())[:120]
    if not config.norm_kw(kw):
        return h._send(400, {'error': 'Which keyword?'})

    def num(k, lo, hi):
        try:
            return max(lo, min(hi, float(d[k]))) if d.get(k) not in (None, '') else None
        except (TypeError, ValueError):
            return None
    fxrates.rates()
    cost = num('cogs', 0.0, 100000.0)
    cur = d.get('cogs_currency') if d.get('cogs_currency') in fxrates.CURRENCIES else 'USD'
    dims = [num(k, 0.1, 500.0) for k in ('l', 'w', 'h')]
    return h._send(200, reports.launch(S.db, market, kw, S.settings,
                                       fxrates.convert(cost, cur, 'USD') if cost else None, int(num('units', 1, 100000) or 300),
                                       dims if all(dims) else None, num('kg', 0.001, 500.0),
                                       'air' if d.get('freight_mode') == 'air' else 'sea',
                                       boom_arg(d) if isinstance(d.get('boom'), dict) else None))


# ---------------- product board ----------------
def _int_arg(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def board_change(h, fn, *args, card=None, refresh=False, **kw):
    """Run one board change. Bad input answers 400 with the reason. New stock alerts become a Windows notification;
    refresh rebuilds the card's hints after the change (costs and markets feed them)."""
    try:
        out = S.db.write(fn, *args, **kw)
        if refresh or out.get('watched'):
            more = board.refresh_hints(S.db, out.get('id') or card, S.settings)
            out['trophies'] = (out.get('trophies') or []) + more.get('trophies', [])
    except board.BoardError as e:
        return h._send(400, {'error': str(e)})
    board.bump()
    BOARD_WAKE.set()                                                # the product update's waiting time starts now
    if out.pop('watched', False):
        S.sync_tasks()
    alerts = out.pop('alerts', None)
    if alerts and S.settings.get('board_notify', True):
        threading.Thread(target=analysis.notify_new, args=(S, alerts), daemon=True).start()
    return h._send(200, out)


@route('GET', '/api/board')
def api_board(h, q):
    ok, why = mailer.ready(S.settings, MAIL_SECRET)
    return h._send(200, {**board.state(S.db, S.settings), 'mail': {'ready': ok, 'why': why}})


@route('GET', '/api/board/card')
def api_board_card(h, q):
    try:
        return h._send(200, board.card_detail(S.db, _int_arg(q.get('id')), S.settings))
    except board.BoardError as e:
        return h._send(404, {'error': str(e)})


@route('POST', '/api/board/add')
def api_board_add(h, q):
    """Put a product on the board: {title, market, asin, kw, image}."""
    d = h._json()
    return board_change(h, lambda c: {**board.add_card(c, d, S.settings), 'watched': True})


@route('POST', '/api/board/card')
def api_board_card_set(h, q):
    d = h._json()
    s = d.get('set') if isinstance(d.get('set'), dict) else {}
    return board_change(h, board.update_card, _int_arg(d.get('id')), s, S.settings)


@route('POST', '/api/board/move')
def api_board_move(h, q):
    d = h._json()
    return board_change(h, board.move_card, _int_arg(d.get('id')), d.get('stage'), d.get('pos'))


@route('POST', '/api/board/archive')
def api_board_archive(h, q):
    d = h._json()
    return board_change(h, board.archive_card, _int_arg(d.get('id')), bool(d.get('archived', True)))


@route('POST', '/api/board/delete')
def api_board_delete(h, q):
    """{id}: delete a product and everything on its card for good."""
    d = h._json()
    return board_change(h, board.delete_card, _int_arg(d.get('id')))


@route('POST', '/api/board/note')
def api_board_note(h, q):
    """{id, text} or {remove: note id}."""
    d = h._json()
    if d.get('remove') is not None:
        return board_change(h, board.remove_note, _int_arg(d.get('remove')))
    return board_change(h, board.add_note, _int_arg(d.get('id')), d.get('text'))


@route('POST', '/api/board/order')
def api_board_order(h, q):
    """{id, n, set: {unit_cost, cost_cur}}."""
    d = h._json()
    s = d.get('set') if isinstance(d.get('set'), dict) else {}
    return board_change(h, board.set_order, _int_arg(d.get('id')), _int_arg(d.get('n')) or 1, s, S.settings, refresh=True)


@route('POST', '/api/board/line')
def api_board_line(h, q):
    """{id, n, market, set: {units, ship_cost, ship_cur, ship_mode, steps, arrived_units}} or {..., remove: true}."""
    d = h._json()
    cid, n, mk = _int_arg(d.get('id')), _int_arg(d.get('n')) or 1, d.get('market')
    if d.get('remove'):
        return board_change(h, board.remove_line, cid, n, mk, S.settings, refresh=True)
    s = d.get('set') if isinstance(d.get('set'), dict) else {}
    return board_change(h, board.set_line, cid, n, mk, s, S.settings, refresh=True)


@route('POST', '/api/board/reorder')
def api_board_reorder(h, q):
    d = h._json()
    return board_change(h, board.start_reorder, _int_arg(d.get('id')), d.get('market'), S.settings)


@route('POST', '/api/board/stock')
def api_board_stock(h, q):
    """{line_id, left}: units left of a shipment; or {remove: count id}."""
    d = h._json()
    if d.get('remove') is not None:
        return board_change(h, board.remove_count, _int_arg(d.get('remove')))
    return board_change(h, board.add_count, _int_arg(d.get('line_id')), d.get('left'), S.settings)


@route('POST', '/api/board/hint')
def api_board_hint(h, q):
    """{hint_id, status, note}, {add: {id, market, text}} or {remove: hint_id}."""
    d = h._json()
    if isinstance(d.get('add'), dict):
        a = d['add']
        return board_change(h, board.add_hint, _int_arg(a.get('id')), a.get('market'), a.get('text'))
    if d.get('remove') is not None:
        return board_change(h, board.remove_hint, _int_arg(d.get('remove')))
    return board_change(h, board.set_hint, _int_arg(d.get('hint_id')), d.get('status'), d.get('note'), S.settings)


@route('POST', '/api/board/check')
def api_board_check(h, q):
    """The approval checklist: {id, item, done}, {id, add: text} or {id, remove: item}."""
    d = h._json()
    cid = _int_arg(d.get('id'))
    if d.get('add') is not None:
        return board_change(h, board.add_check, cid, d.get('add'))
    if d.get('remove') is not None:
        return board_change(h, board.remove_check, cid, _int_arg(d.get('remove')))
    return board_change(h, board.set_check, cid, _int_arg(d.get('item')), bool(d.get('done')), S.settings)


@route('POST', '/api/board/hints/refresh')
def api_board_hints_refresh(h, q):
    d = h._json()
    return board_change(h, lambda c: {'id': board._card(c, _int_arg(d.get('id')))['id']}, refresh=True)


@route('POST', '/api/board/task')
def api_board_task(h, q):
    """A stage deadline {id, stage, days | due_day, notify: [ids], note, meet_time, meet_minutes}, {cancel: task id}
    or {send: task id} (send the reminder now)."""
    d = h._json()
    if d.get('cancel') is not None:
        return board_change(h, board.cancel_task, _int_arg(d.get('cancel')))
    if d.get('send') is not None:
        ok, why = mailer.ready(S.settings, MAIL_SECRET)
        if not ok:
            return h._send(400, {'error': why})
        out = board_change(h, board.send_now, _int_arg(d.get('send')))
        BOARD_WAKE.set()
        return out
    if d.get('notify'):
        ok, why = mailer.ready(S.settings, MAIL_SECRET)
        if not ok:
            return h._send(400, {'error': why})
    out = board_change(h, board.set_task, _int_arg(d.get('id')), d.get('stage'), d, S.settings)
    BOARD_WAKE.set()                                                # the start email goes out right away
    return out


@route('GET', '/api/board/preview')
def api_board_preview(h, q):
    return h._send(200, {'mails': S.db.read(board.preview, _int_arg(q.get('task')), S.settings)})


@route('GET', '/api/board/summary')
def api_board_summary(h, q):
    return h._send(200, {'text': board.summary_text(S.db, S.settings)})


def _ids(v):
    """Notifier ids from a list or '1,2'; None when not given (everyone)."""
    if v is None:
        return None
    parts = v if isinstance(v, list) else str(v).split(',')
    return [i for i in (_int_arg(x) for x in parts if str(x).strip()) if i is not None]


@route('GET', '/api/board/inform')
def api_board_inform(h, q):
    """The product update email as it would go now (?to=1,2&message=...)."""
    ok, why = mailer.ready(S.settings, MAIL_SECRET)
    view = board.inform_view(S.db, S.settings, _ids(q.get('to')), q.get('message') or '')
    return h._send(200, {**view, 'mail': {'ready': ok, 'why': why}})


@route('POST', '/api/board/inform')
def api_board_inform_send(h, q):
    """'Inform attendees': {to: [notifier ids], message} emails the product update now."""
    d = h._json()
    ok, why = mailer.ready(S.settings, MAIL_SECRET)
    if not ok:
        return h._send(400, {'error': why})
    try:
        out = board.inform_send(S.db, S.settings, board_send, _ids(d.get('to')), d.get('message') or '')
    except (board.BoardError, mailer.MailError, OSError) as e:
        return h._send(400, {'error': str(e)})
    return h._send(200, out)


@route('POST', '/api/board/notifiers')
def api_board_notifiers(h, q):
    """{add: {name, email}} or {remove: id}: the people reminder emails can go to."""
    d = h._json()
    try:
        if isinstance(d.get('add'), dict):
            out = S.db.write(board.add_notifier, d['add'].get('name'), d['add'].get('email'))
        else:
            out = S.db.write(board.remove_notifier, _int_arg(d.get('remove')))
    except board.BoardError as e:
        return h._send(400, {'error': str(e)})
    board.bump()
    return h._send(200, {'notifiers': out})


@route('GET', '/api/mail')
def api_mail(h, q):
    ok, why = mailer.ready(S.settings, MAIL_SECRET)
    return h._send(200, {'ready': ok, 'why': why, 'has_password': mailer.has_password(MAIL_SECRET),
                         'pending': board.pending_mail(S.db), 'method': mailer.method(S.settings),
                         'where': mailer.where(S.settings, S.profiles)})


@route('POST', '/api/mail')
def api_mail_set(h, q):
    """{password: the app password} saves it encrypted for this Windows user; {forget: true} deletes it."""
    d = h._json()
    try:
        if d.get('forget'):
            mailer.forget_password(MAIL_SECRET)
        else:
            mailer.save_password(MAIL_SECRET, d.get('password'))
    except (mailer.MailError, OSError) as e:
        return h._send(400, {'error': str(e)})
    return api_mail(h, q)


@route('POST', '/api/mail/test')
def api_mail_test(h, q):
    """Send a test email to your own address."""
    me = S.settings.get('mail_address') or (mailer.mail_profile(S.settings, S.profiles).get('email') or '')
    browser = mailer.method(S.settings) == 'browser'
    if not config.EMAIL_RX.match(me):
        return h._send(400, {'error': 'Add your email address in Settings, Email for reminders, so the test can go to you.'})
    try:
        mailer.deliver(S.settings, MAIL_SECRET, [(S.settings.get('mail_from_name') or '', me)],
                       'Product Checker: test email',
                       'This is a test from Product Checker. Board reminders will be sent like this.\n')
    except mailer.MailError as e:
        return h._send(400, {'error': str(e)})
    return h._send(200, {'ok': True, 'to': me, 'opened': browser, 'where': mailer.where(S.settings, S.profiles)})


def board_send(to, subject, text, html, ics):
    mailer.deliver(S.settings, MAIL_SECRET, to, subject, text, html, ics)


def board_loop():
    """Send due reminder emails every 5 minutes (sooner when woken), raise the stock alerts whose day came, and send
    the automatic product update when its waiting time is over."""
    delay = 20                                                       # catch up soon after the app starts
    while True:
        BOARD_WAKE.wait(delay)
        BOARD_WAKE.clear()
        delay = 300
        try:
            out = board.tick(S.db, S.settings, board_send, inform_ok=mailer.ready(S.settings, MAIL_SECRET)[0])
            if out.get('next'):
                delay = max(5, min(300, out['next'] - time.time()))
            if out['alerts'] and S.settings.get('board_notify', True):
                analysis.notify_new(S, out['alerts'])
        except Exception:                                           # noqa: BLE001 - tried again in 5 minutes
            log.exception('board reminders')


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
        S.scheduler.stop()
        if getattr(S, '_procs', None):
            S._procs.shutdown(wait=False, cancel_futures=True)
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
    """When the app window closes: stop, or keep refreshing in the background if "Keep running" is on."""
    exited = None
    while True:
        time.sleep(2)
        win = S.window
        if win is not None and exited is None and win.poll() is not None:
            exited = time.time()
        what = singleton.watchdog_decision(time.time(), exited, S.started, S.last_beat,
                                           S.settings.get('keep_running') or board.pending_mail(S.db) > 0 or
                                           (board.inform_waiting(S.db, S.settings) and
                                            mailer.ready(S.settings, MAIL_SECRET)[0]), S.mode)
        if what == 'shutdown':
            return shutdown()
        if what == 'background':
            S.mode = 'background'
            S.window = None
            S.say('The window is closed. Product Checker keeps refreshing in the background.')
            return


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
    background = '--background' in sys.argv
    no_window = '--no-window' in sys.argv or background
    port = already_running()
    if port:                                                     # bring the open app to the front instead
        if not no_window:
            cp.open_app_window('http://127.0.0.1:%d/' % port, APP_DIR / 'app-window')
        return
    import zlib
    instance = singleton.Instance('ProductCheckerV6-%08x' % zlib.crc32(str(APP_DIR).lower().encode()))   # one per data folder
    if not instance.acquire():
        print('Product Checker is already starting.', flush=True)
        return
    if singleton.clear_stale_profile_lock(ac.PROFILE_DIR):
        log.info('cleared a stale browser profile lock')
    S = State()
    S.instance = instance
    S.mode = 'background' if background else 'window'
    S.apply_startup_setting()
    port = int(os.environ.get('PC_PORT') or free_port())
    S.port = port
    HTTPD = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    HTTPD.daemon_threads = True
    PORT_FILE.write_text(str(port))
    url = 'http://127.0.0.1:%d/' % port
    log.info('started on %s', url)
    print('Product Checker running at', url, flush=True)
    if not background:
        S.run(S.checker.start())                                  # warm up the background browser
    threading.Thread(target=S.scan_imports, daemon=True, name='import-scan').start()
    S.run(S.scheduler.run_forever())                              # automatic refresh
    threading.Thread(target=board_loop, daemon=True, name='board').start()   # board reminder emails
    fxrates.refresh_soon()                                         # today's exchange rates, in the background
    if not no_window:
        S.window = cp.open_app_window(url, APP_DIR / 'app-window')
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        HTTPD.serve_forever()
    except KeyboardInterrupt:
        shutdown()


if __name__ == '__main__':
    main()
