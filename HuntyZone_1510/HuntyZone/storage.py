"""The app's memory: one SQLite file (market.db) with every search, list scan, Trends sample and import.

- One writer thread owns the only write connection. Callers hand it a function: db.write(fn, ...) waits for
  the result, db.submit(fn, ...) returns a Future and `await db.awrite(fn, ...)` suits the browser loop.
  Each function runs inside one transaction.
- Reads use a small pool of read-only connections (WAL mode, so reads never wait for the writer).
- The schema is versioned with PRAGMA user_version. The file is backed up before any migration.
- Times: `ts` is UTC epoch seconds. `day` / `month` are in the marketplace's own calendar (config.market_day).
- A blocked or failed fetch is stored with its status and is never counted as zero: charts and alerts read
  only the statuses in VALID.
"""
import asyncio
import contextlib
import json
import logging
import os
import queue
import shutil
import sqlite3
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import config

log = logging.getLogger('pc')

VALID = ('ok', 'empty', 'partial')            # statuses that count as data; anything else is a gap
STATUSES = VALID + ('captcha', 'blocked', 'geo_redirect', 'wrong_location', 'empty_suspect', 'offline', 'timeout',
                    'error', 'missing')
LOW_REVIEWS = 100                              # "easy to compete with" listings have fewer reviews than this
DELIVERY_KEEP_DAYS = 14                        # raw delivery text is dropped after this; the flags stay
SERP_ITEM_KEEP_DAYS = 180                      # then one snapshot per keyword, market and month is kept
LIST_ITEM_KEEP_DAYS = 400
FETCH_LOG_KEEP_DAYS = 30
ALERT_KEEP_DAYS = 180
SUGGEST_KEEP_DAYS = 180                        # older Amazon suggestions go, except the newest per prefix
GAP_KEEP_DAYS = 180                            # older Gap Finder checks and offer readings go, except the newest
BACKUPS = {'daily': 2, 'weekly': 2}

SCHEMA_VERSION = 8
MIGRATIONS = {
    1: """
-- operations
CREATE TABLE run(id INTEGER PRIMARY KEY, kind TEXT, trigger TEXT, started_at INT, finished_at INT, status TEXT,
  n_ok INT DEFAULT 0, n_fail INT DEFAULT 0, n_blocked INT DEFAULT 0, note TEXT);
CREATE TABLE fetch_log(id INTEGER PRIMARY KEY, run_id INT, ts INT, source TEXT, market TEXT, url TEXT,
  status TEXT, http INT, ms INT, error TEXT);
CREATE TABLE source_state(source TEXT PRIMARY KEY, cooldown_until INT DEFAULT 0, fail_streak INT DEFAULT 0,
  budget_factor REAL DEFAULT 1, last_ok INT, last_block INT, reason TEXT);
CREATE TABLE budget(day TEXT, source TEXT, task TEXT, used INT DEFAULT 0, skipped INT DEFAULT 0,
  PRIMARY KEY(day, source, task));
CREATE TABLE task(id INTEGER PRIMARY KEY, kind TEXT, market TEXT, target TEXT, every_s INT, next_run_at INT,
  last_run_at INT, last_status TEXT, fail_streak INT DEFAULT 0, priority INT DEFAULT 5, enabled INT DEFAULT 1,
  UNIQUE(kind, market, target));
CREATE TABLE watch(id INTEGER PRIMARY KEY, kind TEXT, market TEXT, target TEXT, display TEXT,
  stage TEXT DEFAULT 'idea', reject_reason TEXT, cadence_h INT DEFAULT 24, notes TEXT, cogs REAL,
  added_at INT, enabled INT DEFAULT 1, UNIQUE(kind, market, target));
CREATE TABLE alert(id INTEGER PRIMARY KEY, ts INT, rule TEXT, severity INT, market TEXT, target TEXT, title TEXT,
  body TEXT, data_json TEXT, dedupe_key TEXT UNIQUE, seen_at INT, dismissed_at INT, toasted INT DEFAULT 0);
CREATE TABLE ui_state(key TEXT PRIMARY KEY, value TEXT);

-- entities
CREATE TABLE keyword(kw_norm TEXT PRIMARY KEY, display TEXT, created_at INT);
CREATE TABLE asin(market TEXT, asin TEXT, parent_asin TEXT, title TEXT, brand TEXT, image TEXT, root_node TEXT,
  root_name TEXT, sub_json TEXT, dfa_day TEXT, n_variations INT, pkg_l_cm REAL, pkg_w_cm REAL, pkg_h_cm REAL,
  pkg_kg REAL, first_seen INT, last_seen INT, static_fetched_at INT, PRIMARY KEY(market, asin));

-- Amazon search snapshots
CREATE TABLE serp_snapshot(id INTEGER PRIMARY KEY, run_id INT, market TEXT, kw_norm TEXT, ts INT, day TEXT,
  month TEXT, source TEXT, status TEXT, pages INT, location TEXT, location_wanted TEXT, location_ok INT,
  results_total INT, results_over INT, organic INT, sponsored INT, fast INT, local INT, local_other INT,
  overseas INT, overseas_intl INT, origin_unknown INT, badged INT, bought_low_sum REAL, bought_mid_sum REAL,
  bought_high_sum REAL, bought_top INT, price_p25 REAL, price_med REAL, price_p75 REAL, revenue_mid REAL,
  reviews_med_top10 REAL, low_review_n INT, fast_days INT, local_days INT, parse_health REAL, error TEXT);
CREATE TABLE serp_item(snapshot_id INT, pos INT, asin TEXT, sponsored INT, price REAL, rating REAL, reviews INT,
  bought INT, days INT, prime INT, intl INT, first_order INT, delivery TEXT, PRIMARY KEY(snapshot_id, pos));

-- Movers & Shakers / New Releases
CREATE TABLE list_snapshot(id INTEGER PRIMARY KEY, run_id INT, market TEXT, kind TEXT, slug TEXT, category TEXT,
  ts INT, day TEXT, status TEXT, n INT);
CREATE TABLE list_item(snapshot_id INT, pos INT, asin TEXT, pct INT, rank_now INT, rank_before INT, price REAL,
  rating REAL, reviews INT, deal INT, PRIMARY KEY(snapshot_id, pos));

-- Amazon search suggestions
CREATE TABLE suggest_snapshot(id INTEGER PRIMARY KEY, run_id INT, market TEXT, seed_norm TEXT, ts INT, day TEXT,
  status TEXT, n_calls INT, n_failed INT);
CREATE TABLE suggest_item(snapshot_id INT, keyword TEXT, hits INT, best INT);

-- product pages
CREATE TABLE product_snapshot(id INTEGER PRIMARY KEY, run_id INT, market TEXT, asin TEXT, ts INT, day TEXT,
  status TEXT, root_rank INT, root_node TEXT, sub_ranks_json TEXT, badge_low INT, price REAL, offers INT,
  rating REAL, reviews INT, buybox_seller TEXT, ships_from TEXT, sold_by_amazon INT, n_variations INT,
  delivery_days INT, origin TEXT);

-- Google Trends: every sample is kept
CREATE TABLE trends_sample(id INTEGER PRIMARY KEY, term TEXT, geo TEXT, timeframe TEXT, fetched_at INT,
  status TEXT, resolution TEXT, user_type TEXT, points_json TEXT);
CREATE TABLE trends_related(id INTEGER PRIMARY KEY, term TEXT, geo TEXT, timeframe TEXT, fetched_at INT,
  kind TEXT, query TEXT, value INT, breakout INT);

-- Helium 10 imports and calibration
CREATE TABLE import_file(id INTEGER PRIMARY KEY, kind TEXT, market TEXT, kw_norm TEXT, file TEXT, sha1 TEXT UNIQUE,
  as_of INT, month TEXT, imported_at INT, n_rows INT);
CREATE TABLE h10_row(import_id INT, asin TEXT, title TEXT, brand TEXT, price REAL, sales_asin REAL,
  sales_parent REAL, revenue REAL, bsr INT, category TEXT, reviews INT, rating REAL, fba_fees REAL,
  size_tier TEXT, weight_kg REAL, created TEXT, seller_country TEXT, fulfillment TEXT, sponsored INT);
CREATE TABLE kw_volume(market TEXT, kw_norm TEXT, month TEXT, volume INT, source TEXT, import_id INT,
  PRIMARY KEY(market, kw_norm, month, source));
CREATE TABLE calib_point(id INTEGER PRIMARY KEY, market TEXT, node TEXT, ts INT, ln_bsr REAL, low REAL, high REAL,
  kind TEXT, asin TEXT, ref_id INT);

-- derived (can be rebuilt at any time)
CREATE TABLE curve(market TEXT, node TEXT, fitted_at INT, data_json TEXT, PRIMARY KEY(market, node));
CREATE TABLE asin_month(market TEXT, asin TEXT, month TEXT, units_mid REAL, units_lo REAL, units_hi REAL,
  revenue_mid REAL, coverage REAL, method TEXT, PRIMARY KEY(market, asin, month));
CREATE TABLE trends_feature(term TEXT, geo TEXT, computed_at INT, label TEXT, score REAL, data_json TEXT,
  PRIMARY KEY(term, geo));
CREATE TABLE niche_score(market TEXT, kw_norm TEXT, computed_at INT, data_json TEXT, PRIMARY KEY(market, kw_norm));

CREATE INDEX serp_snapshot_kw ON serp_snapshot(market, kw_norm, ts);
CREATE INDEX serp_snapshot_run ON serp_snapshot(run_id);
CREATE INDEX serp_item_asin ON serp_item(asin);
CREATE INDEX list_snapshot_key ON list_snapshot(market, kind, slug, ts);
CREATE INDEX list_item_asin ON list_item(asin);
CREATE INDEX product_snapshot_key ON product_snapshot(market, asin, ts);
CREATE INDEX trends_sample_key ON trends_sample(term, geo, timeframe, fetched_at);
CREATE INDEX trends_related_key ON trends_related(term, geo, fetched_at);
CREATE INDEX h10_row_asin ON h10_row(asin);
CREATE INDEX h10_row_import ON h10_row(import_id);
CREATE INDEX calib_point_key ON calib_point(market, node);
CREATE INDEX alert_ts ON alert(ts);
CREATE INDEX fetch_log_ts ON fetch_log(ts);
CREATE INDEX task_next ON task(next_run_at);
""",
    2: """
-- critical (1-3 star) reviews of listings, and when each listing's reviews were read (product page / reviews page)
CREATE TABLE review(market TEXT, asin TEXT, id TEXT, stars REAL, title TEXT, body TEXT, day TEXT, country TEXT,
  local INT, helpful INT, verified INT, source TEXT, first_seen INT, last_seen INT, PRIMARY KEY(market, asin, id));
CREATE TABLE review_read(market TEXT, asin TEXT, page_at INT, critical_at INT, PRIMARY KEY(market, asin));
""",
    3: """
-- who sells a listing (from its product page) and the sellers themselves (from their Amazon profile page)
ALTER TABLE product_snapshot ADD COLUMN seller_id TEXT;
ALTER TABLE product_snapshot ADD COLUMN fulfillment TEXT;
CREATE TABLE seller(market TEXT, seller_id TEXT, name TEXT, business_name TEXT, country TEXT, rating_pct REAL,
  ratings INT, status TEXT, fetched_at INT, PRIMARY KEY(market, seller_id));
""",
    4: """
-- your choices per product: your supplier price (US$), your search phrase, starred, or Not for me
CREATE TABLE product_pref(market TEXT, asin TEXT, cogs_usd REAL, cogs_at INT, phrase TEXT, phrase_at INT,
  liked_at INT, hidden_at INT, PRIMARY KEY(market, asin));
""",
    5: """
-- the product board: products from sourcing to re-order, their orders and stock, launch hints, notes, deadlines
-- and the reminder emails those send to the people on your notifier list
CREATE TABLE board_card(id INTEGER PRIMARY KEY, title TEXT, market TEXT, asin TEXT, kw TEXT, image TEXT, color TEXT,
  stage TEXT DEFAULT 'sourcing', pos REAL DEFAULT 0, data_json TEXT, created_at INT, updated_at INT, archived_at INT);
CREATE TABLE board_order(id INTEGER PRIMARY KEY, card_id INT, n INT, unit_cost REAL, cost_cur TEXT, ready_at INT,
  created_at INT, UNIQUE(card_id, n));
CREATE TABLE board_line(id INTEGER PRIMARY KEY, order_id INT, market TEXT, units INT, ship_cost REAL, ship_cur TEXT,
  ship_mode TEXT, steps_json TEXT, arrived_units INT, UNIQUE(order_id, market));
CREATE TABLE board_stock(id INTEGER PRIMARY KEY, line_id INT, ts INT, units_left INT);
CREATE TABLE board_hint(id INTEGER PRIMARY KEY, card_id INT, market TEXT, key TEXT, cat TEXT, text TEXT, data_json TEXT,
  status TEXT DEFAULT 'todo', note TEXT, checked_at INT, moved_json TEXT, custom INT DEFAULT 0, recheck_days INT,
  updated_at INT, UNIQUE(card_id, market, key));
CREATE TABLE board_event(id INTEGER PRIMARY KEY, card_id INT, ts INT, kind TEXT, text TEXT, data_json TEXT);
CREATE TABLE board_task(id INTEGER PRIMARY KEY, card_id INT, stage TEXT, start_day TEXT, days INT, due_day TEXT,
  note TEXT, notify_json TEXT, meet_time TEXT, meet_minutes INT, meet_start INT, done_at INT, cancelled_at INT,
  created_at INT);
CREATE TABLE board_notifier(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE COLLATE NOCASE, added_at INT,
  removed_at INT);
CREATE TABLE board_mail(id INTEGER PRIMARY KEY, task_id INT, kind TEXT, send_at INT, status TEXT DEFAULT 'scheduled',
  attempts INT DEFAULT 0, sent_at INT, error TEXT, to_json TEXT, subject TEXT);
CREATE INDEX board_line_order ON board_line(order_id);
CREATE INDEX board_stock_line ON board_stock(line_id, ts);
CREATE INDEX board_hint_card ON board_hint(card_id);
CREATE INDEX board_event_card ON board_event(card_id, ts);
CREATE INDEX board_task_card ON board_task(card_id, stage);
CREATE INDEX board_mail_due ON board_mail(status, send_at);
""",
    6: """
-- the Global Gap Finder (no semicolons in comments: migrations are split on them)
ALTER TABLE product_snapshot ADD COLUMN available INT;
ALTER TABLE product_snapshot ADD COLUMN merchant_id TEXT;
ALTER TABLE product_snapshot ADD COLUMN safety_json TEXT;
CREATE TABLE offer_snapshot(id INTEGER PRIMARY KEY, market TEXT, asin TEXT, ts INT, source TEXT, status TEXT,
  available INT, n_offers INT, amazon INT, n_fba INT, n_fbm INT, offers_json TEXT);
CREATE TABLE gap_check(id INTEGER PRIMARY KEY, ts INT, day TEXT, market TEXT, asin TEXT, cluster TEXT, phrase TEXT,
  status TEXT, gap_type TEXT, data_json TEXT);
CREATE INDEX offer_snapshot_key ON offer_snapshot(market, asin, ts);
CREATE INDEX gap_check_key ON gap_check(cluster, market, ts);
CREATE INDEX gap_check_ts ON gap_check(ts);
""",
    7: """
-- Market intelligence: the ads on a search page, a listing's images and content, and the product-page carousels
ALTER TABLE serp_snapshot ADD COLUMN ads_json TEXT;
ALTER TABLE product_snapshot ADD COLUMN listing_json TEXT;
CREATE TABLE related(market TEXT, asin TEXT, kind TEXT, other TEXT, pos INT, ts INT, PRIMARY KEY(market, asin, kind, other));
CREATE INDEX related_other ON related(market, other);
""",
    8: """
-- the Product board: where a stock count came from (you or the SP-API) and Amazon's daily sales of your products
ALTER TABLE board_stock ADD COLUMN source TEXT;
CREATE TABLE board_sales(card_id INT, market TEXT, day TEXT, units INT, ts INT, PRIMARY KEY(card_id, market, day));
""",
}


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


class DB:
    """market.db: one writer thread, a few read connections."""

    def __init__(self, path=None, readers=4):
        self.path = Path(path or config.APP_DIR / 'market.db')
        self.backup_dir = self.path.parent / 'backup'
        self._q = queue.Queue()
        self._pool = queue.LifoQueue()
        self._max_readers = readers
        self._made = 0
        self._made_lock = threading.Lock()
        self._writer = None
        self._wconn = None

    # ---------- lifecycle ----------
    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            self._migrate(conn)
        except Exception:
            conn.close()
            raise
        self._wconn = conn
        self._writer = threading.Thread(target=self._write_loop, name='db-writer', daemon=True)
        self._writer.start()
        return self

    def close(self):
        if self._writer and self._writer.is_alive():
            done = Future()
            self._q.put((None, (), {}, done))
            done.result(timeout=10)
        while True:
            try:
                self._pool.get_nowait().close()
            except queue.Empty:
                break
        self._writer = None

    def _connect(self, readonly=False):
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=5000')
        if readonly:
            conn.execute('PRAGMA query_only=1')
        else:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def _migrate(self, conn):
        have = conn.execute('PRAGMA user_version').fetchone()[0]
        if have > SCHEMA_VERSION:
            raise RuntimeError('market.db was written by a newer version of Product Checker (schema %d).' % have)
        if 0 < have < SCHEMA_VERSION:
            self.backup(kind='pre-migrate-v%d' % have, conn=conn)
        for v in range(have + 1, SCHEMA_VERSION + 1):
            conn.execute('BEGIN IMMEDIATE')
            try:
                for stmt in MIGRATIONS[v].split(';'):
                    if stmt.strip():
                        conn.execute(stmt)
                conn.execute('PRAGMA user_version=%d' % v)
                conn.execute('COMMIT')
            except Exception:
                conn.execute('ROLLBACK')
                raise
            log.info('market.db schema now version %d', v)

    # ---------- writing ----------
    def _write_loop(self):
        conn = self._wconn
        while True:
            fn, args, kw, fut = self._q.get()
            if fn is None:
                conn.close()
                fut.set_result(True)
                return
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    out = fn(conn, *args, **kw)
                    conn.execute('COMMIT')
                except BaseException:
                    conn.execute('ROLLBACK')
                    raise
                fut.set_result(out)
            except Exception as e:                       # noqa: BLE001 - handed to the caller
                fut.set_exception(e)

    def submit(self, fn, *args, **kw):
        """Run fn(conn, *args, **kw) in one transaction on the writer thread. Returns a Future."""
        fut = Future()
        if not self._writer:
            fut.set_exception(RuntimeError('database is closed'))
            return fut
        self._q.put((fn, args, kw, fut))
        return fut

    def write(self, fn, *args, timeout=60, **kw):
        return self.submit(fn, *args, **kw).result(timeout=timeout)

    async def awrite(self, fn, *args, **kw):
        return await asyncio.wrap_future(self.submit(fn, *args, **kw))

    # ---------- reading ----------
    @contextlib.contextmanager
    def reader(self):
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            with self._made_lock:
                make = self._made < self._max_readers
                if make:
                    self._made += 1
            conn = self._connect(readonly=True) if make else self._pool.get(timeout=30)
        try:
            yield conn
        finally:
            self._pool.put(conn)

    def read(self, fn, *args, **kw):
        """Run fn(conn, *args, **kw) on a read connection."""
        with self.reader() as conn:
            return fn(conn, *args, **kw)

    def all(self, sql, params=()):
        with self.reader() as conn:
            return _rows(conn.execute(sql, params))

    def one(self, sql, params=()):
        with self.reader() as conn:
            r = conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    # ---------- backups and health ----------
    def backup(self, kind='daily', now=None, conn=None):
        """Copy the database into backup/. Keeps BACKUPS[kind] files of each kind. Returns the new file."""
        now = now or time.time()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y-%m-%d' if kind == 'daily' else '%G-W%V' if kind == 'weekly' else '%Y%m%d-%H%M%S',
                              time.gmtime(now))
        target = self.backup_dir / ('%s-%s.db' % (kind, stamp))
        if target.exists() and kind in BACKUPS:
            return target
        tmp = target.with_suffix('.tmp')
        src = conn or sqlite3.connect(str(self.path), timeout=10)
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            if conn is None:
                src.close()
        os.replace(tmp, target)
        keep = BACKUPS.get(kind)
        if keep:
            old = sorted(self.backup_dir.glob('%s-*.db' % kind))
            for f in old[:-keep]:
                try:
                    f.unlink()
                except OSError:
                    pass
        return target

    def routine_backups(self, now=None):
        return [self.backup('daily', now), self.backup('weekly', now)]

    def quick_check(self):
        with self.reader() as conn:
            return conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'

    def size_mb(self):
        total = 0
        for suffix in ('', '-wal', '-shm'):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return round(total / 1e6, 2)


def restore_latest_backup(path):
    """Move a damaged market.db aside and put the newest backup in its place. Returns the backup used, or None."""
    path = Path(path)
    backups = sorted((path.parent / 'backup').glob('*.db'), key=lambda f: f.stat().st_mtime)
    if not backups:
        return None
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for suffix in ('', '-wal', '-shm'):
        p = Path(str(path) + suffix)
        if p.exists():
            os.replace(p, Path(str(path) + '.damaged-' + stamp + suffix))
    shutil.copyfile(backups[-1], path)
    return backups[-1]


# =====================================================================
# Repository functions. Writers take the write connection as their first
# argument (use with db.write / db.awrite); readers take a read connection
# (use with db.read).
# =====================================================================

def _now(ts=None):
    return int(ts if ts is not None else time.time())


def _num(x):
    if x is None or isinstance(x, bool):
        return x if x is None else int(x)
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float('inf'), float('-inf')) else f


# ---------- runs and fetches ----------
def start_run(conn, kind, trigger='user', ts=None, note=None):
    cur = conn.execute('INSERT INTO run(kind, trigger, started_at, status, note) VALUES (?,?,?,?,?)',
                       (kind, trigger, _now(ts), 'running', note))
    return cur.lastrowid


def finish_run(conn, run_id, status='done', n_ok=0, n_fail=0, n_blocked=0, note=None, ts=None):
    conn.execute('UPDATE run SET finished_at=?, status=?, n_ok=?, n_fail=?, n_blocked=?, note=COALESCE(?, note) '
                 'WHERE id=?', (_now(ts), status, n_ok, n_fail, n_blocked, note, run_id))


def interrupt_stale_runs(conn, now=None):
    """Runs still 'running' from a previous start were cut off (crash, sleep, closed laptop)."""
    return conn.execute("UPDATE run SET status='interrupted', finished_at=? WHERE status='running'",
                        (_now(now),)).rowcount


def log_fetch(conn, source, market, url, status, http=None, ms=None, error=None, run_id=None, ts=None):
    conn.execute('INSERT INTO fetch_log(run_id, ts, source, market, url, status, http, ms, error) '
                 'VALUES (?,?,?,?,?,?,?,?,?)', (run_id, _now(ts), source, market, (url or '')[:500], status, http,
                                                ms, (error or '')[:500] or None))


# ---------- keywords and listings ----------
def upsert_keyword(conn, display, ts=None):
    kw = config.norm_kw(display)
    if kw:
        conn.execute('INSERT INTO keyword(kw_norm, display, created_at) VALUES (?,?,?) '
                     'ON CONFLICT(kw_norm) DO NOTHING', (kw, ' '.join(str(display).split())[:120], _now(ts)))
    return kw


def upsert_asins(conn, market, rows, ts=None):
    t = _now(ts)
    for r in rows:
        if not r.get('asin'):
            continue
        conn.execute(
            'INSERT INTO asin(market, asin, title, brand, image, first_seen, last_seen) VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(market, asin) DO UPDATE SET '
            'title=COALESCE(NULLIF(excluded.title, \'\'), title), brand=COALESCE(excluded.brand, brand), '
            'image=COALESCE(NULLIF(excluded.image, \'\'), image), '
            'first_seen=MIN(first_seen, excluded.first_seen), last_seen=MAX(last_seen, excluded.last_seen)',
            (market, r['asin'], (r.get('title') or '')[:300], r.get('brand'), r.get('image') or '', t, t))


# ---------- search snapshots ----------
def serp_status(s):
    """ok / empty / wrong_location, or the failure status the check reported."""
    st = s.get('status')
    if st and st not in VALID:
        return st
    if s.get('error'):
        return 'error'
    if s.get('location_ok') is False:
        return 'wrong_location'
    if st:
        return st
    return 'ok' if s.get('total') else 'empty'


def _quantiles(v):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None, None, None

    def q(p):
        k = (len(v) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(v) - 1)
        return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)
    return q(.25), q(.5), q(.75)


def serp_aggregates(s):
    """Demand and competition numbers for one search, from its listings."""
    from engine import badge
    rows = s.get('rows') or []
    organic = [r for r in rows if not r.get('sponsored')]
    demand = badge.page_demand([r.get('bought') for r in organic], s.get('market', 'US'))
    p25, med, p75 = _quantiles([r.get('price') for r in organic if r.get('price')])
    top10 = [r.get('reviews') or 0 for r in organic[:10]]
    reviews_top10 = _quantiles(top10)[1] if top10 else None
    revenue = 0.0
    for r in organic:
        if r.get('bought') and r.get('price'):
            revenue += badge.badge_estimate(r['bought'], s.get('market', 'US'))['v'] * r['price']
    return {
        'badged': demand['badged'], 'bought_low_sum': demand['low_sum'], 'bought_mid_sum': demand['mid_sum'],
        'bought_high_sum': demand['high_sum'], 'bought_top': demand['top'],
        'price_p25': p25, 'price_med': med, 'price_p75': p75, 'revenue_mid': round(revenue, 2) if revenue else None,
        'reviews_med_top10': reviews_top10,
        'low_review_n': sum(1 for r in organic if (r.get('reviews') or 0) < LOW_REVIEWS),
    }


def save_serp(conn, s, source='user', run_id=None, pages=1, location_wanted=None, ts=None):
    """Store one marketplace's search result (a summary from amazon_check.summarize). Returns the snapshot id."""
    market = s['market']
    t = _now(ts if ts is not None else s.get('checked_at'))
    kw = upsert_keyword(conn, s.get('keyword') or '', t)
    status = serp_status(s)
    rows = s.get('rows') or []
    agg = serp_aggregates(s) if rows else {}
    cols = {
        'run_id': run_id, 'market': market, 'kw_norm': kw, 'ts': t, 'day': config.market_day(t, market),
        'month': config.month_key(t, market), 'source': source, 'status': status, 'pages': pages,
        'location': s.get('location'), 'location_wanted': location_wanted if location_wanted is not None
        else s.get('location_wanted'), 'location_ok': None if s.get('location_ok') is None else int(bool(s['location_ok'])),
        'results_total': s.get('results_total'), 'results_over': _num(s.get('results_over')),
        'organic': s.get('total'), 'sponsored': s.get('sponsored'), 'fast': s.get('fast'), 'local': s.get('local'),
        'local_other': s.get('local_other'), 'overseas': s.get('overseas'), 'overseas_intl': s.get('overseas_intl'),
        'origin_unknown': s.get('origin_unknown'), 'fast_days': s.get('fast_days'), 'local_days': s.get('local_days'),
        'parse_health': _num(s.get('parse_health')), 'error': (s.get('error') or '')[:300] or None, **agg,
    }
    if s.get('ads') is not None:
        cols['ads_json'] = json.dumps(s['ads'])
    names = list(cols)
    cur = conn.execute('INSERT INTO serp_snapshot(%s) VALUES (%s)' % (', '.join(names), ','.join('?' * len(names))),
                       [cols[n] for n in names])
    sid = cur.lastrowid
    for pos, r in enumerate(rows, 1):
        text = r.get('delivery') or ''
        conn.execute(
            'INSERT INTO serp_item(snapshot_id, pos, asin, sponsored, price, rating, reviews, bought, days, prime, '
            'intl, first_order, delivery) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (sid, pos, r.get('asin'), int(bool(r.get('sponsored'))), _num(r.get('price')), _num(r.get('rating')),
             r.get('reviews'), r.get('bought'), r.get('days'), int(bool(r.get('prime'))),
             int(bool(r.get('intl'))), int(bool(r.get('first_order')) or 'first order' in text.lower()),
             text[:240] or None))
    upsert_asins(conn, market, rows, t)
    return sid


def _item_row(market, r):
    site = _site(market)
    return {'market': market, 'asin': r['asin'], 'title': r.get('title') or '', 'image': r.get('image') or '',
            'price': r['price'], 'rating': r['rating'], 'reviews': r['reviews'], 'bought': r['bought'],
            'prime': bool(r['prime']), 'sponsored': bool(r['sponsored']), 'delivery': r['delivery'] or '',
            'days': r['days'], 'intl': bool(r['intl']), 'first_order': bool(r['first_order']),
            'url': '%s/dp/%s' % (site, r['asin']) if site else ''}


def _site(market):
    try:
        import amazon_check
        return amazon_check.MARKETS[market]['site']
    except Exception:                                   # noqa: BLE001
        return ''


def snapshot_rows(conn, snapshot_id, market):
    """The listings of one snapshot, shaped like amazon_check rows, so summarize() can recount them."""
    cur = conn.execute('SELECT i.*, a.title, a.image FROM serp_item i LEFT JOIN asin a ON a.market=? AND a.asin=i.asin '
                       'WHERE i.snapshot_id=? ORDER BY i.pos', (market, snapshot_id))
    return [_item_row(market, r) for r in _rows(cur)]


def snapshot_as_summary(conn, snap):
    """A stored snapshot as the input for amazon_check.summarize (rows, location, keyword, time)."""
    kw = conn.execute('SELECT display FROM keyword WHERE kw_norm=?', (snap['kw_norm'],)).fetchone()
    return {'market': snap['market'], 'keyword': kw[0] if kw else snap['kw_norm'], 'location': snap['location'] or '',
            'checked_at': snap['ts'], 'rows': snapshot_rows(conn, snap['id'], snap['market']),
            'snapshot_id': snap['id'], 'status': snap['status'], 'pages': snap['pages'],
            'ads': json.loads(snap['ads_json']) if snap.get('ads_json') else None}


def latest_serp(conn, market, kw, pages=None, location_wanted=None, max_age_s=None, now=None, statuses=VALID):
    """The newest usable snapshot for this keyword and market (same pages and wanted location), or None."""
    sql = ['SELECT * FROM serp_snapshot WHERE market=? AND kw_norm=? AND status IN (%s)' % ','.join('?' * len(statuses))]
    params = [market, config.norm_kw(kw), *statuses]
    if pages is not None:
        sql.append('AND pages=?')
        params.append(pages)
    if location_wanted is not None:
        sql.append('AND location_wanted=?')
        params.append(location_wanted)
    if max_age_s is not None:
        sql.append('AND ts>=?')
        params.append(_now(now) - max_age_s)
    sql.append('ORDER BY ts DESC LIMIT 1')
    r = conn.execute(' '.join(sql), params).fetchone()
    return snapshot_as_summary(conn, dict(r)) if r else None


def serp_history(conn, market, kw, since_ts=0, valid_only=False):
    """Every snapshot (newest last) for charts. Gaps keep their status so the chart can show them."""
    sql = 'SELECT * FROM serp_snapshot WHERE market=? AND kw_norm=? AND ts>=?'
    if valid_only:
        sql += ' AND status IN (%s)' % ','.join("'%s'" % s for s in VALID)
    return _rows(conn.execute(sql + ' ORDER BY ts', (market, config.norm_kw(kw), since_ts)))


def recent_checks(conn, limit=40):
    """The Library list: recent user checks grouped by run (one entry per press of Check sellers)."""
    runs = _rows(conn.execute(
        "SELECT r.id, r.started_at FROM run r WHERE r.kind='check' AND EXISTS "
        "(SELECT 1 FROM serp_snapshot s WHERE s.run_id=r.id) ORDER BY r.started_at DESC LIMIT ?", (limit,)))
    out = []
    for run in runs:
        snaps = _rows(conn.execute('SELECT s.*, k.display FROM serp_snapshot s LEFT JOIN keyword k ON k.kw_norm=s.kw_norm '
                                   'WHERE s.run_id=? ORDER BY s.market', (run['id'],)))
        out.append({'id': run['id'], 'at': run['started_at'], 'keyword': snaps[0]['display'] or snaps[0]['kw_norm'],
                    'snapshots': snaps})
    return out


def run_snapshots(conn, run_id):
    snaps = _rows(conn.execute('SELECT * FROM serp_snapshot WHERE run_id=? ORDER BY market', (run_id,)))
    return [snapshot_as_summary(conn, s) for s in snaps]


def keywords_overview(conn, limit=500):
    """Every keyword the app has seen, with its markets and last check, for the Library."""
    return _rows(conn.execute(
        'SELECT k.kw_norm, k.display, COUNT(s.id) AS n, MAX(s.ts) AS last_ts, GROUP_CONCAT(DISTINCT s.market) AS markets '
        'FROM keyword k JOIN serp_snapshot s ON s.kw_norm=k.kw_norm GROUP BY k.kw_norm ORDER BY last_ts DESC LIMIT ?',
        (limit,)))


# ---------- history.json from v5 ----------
def import_history_entry(conn, entry):
    """One v5 history entry -> a run plus its snapshots. Skips entries already imported (idempotent)."""
    results = [r for r in entry.get('results') or [] if r.get('market') and r.get('keyword') is not None]
    if not results:
        return 0
    at = _now(entry.get('at') or results[0].get('checked_at'))
    fresh = []
    for r in results:
        t = _now(r.get('checked_at') or at)
        hit = conn.execute('SELECT 1 FROM serp_snapshot WHERE market=? AND kw_norm=? AND ts=? AND source=?',
                           (r['market'], config.norm_kw(r['keyword']), t, 'import')).fetchone()
        if not hit:
            fresh.append((r, t))
    if not fresh:
        return 0
    run_id = start_run(conn, 'check', 'import', ts=at, note='history.json')
    for r, t in fresh:
        save_serp(conn, r, source='import', run_id=run_id, pages=entry.get('pages', 1), ts=t,
                  location_wanted=r.get('location_wanted'))
    finish_run(conn, run_id, 'done', n_ok=len(fresh), ts=at)
    return len(fresh)


def import_history_file(db, path):
    """Import a v5 history.json (one transaction per entry, so a crash can resume), then rename it."""
    path = Path(path)
    if not path.exists():
        return 0
    try:
        entries = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        log.warning('history.json could not be read; left in place')
        return 0
    n = 0
    for entry in reversed(entries if isinstance(entries, list) else []):        # oldest first
        if isinstance(entry, dict):
            try:
                n += db.write(import_history_entry, entry)
            except Exception:                                                   # noqa: BLE001
                log.exception('history entry skipped')
    try:
        os.replace(path, path.with_name('history.v5.bak.json'))
    except OSError:
        log.warning('could not rename history.json')
    log.info('imported %d searches from history.json', n)
    return n


# ---------- list scans (Movers & Shakers, New Releases) ----------
def save_list(conn, market, kind, slug, category, items, status='ok', run_id=None, ts=None):
    t = _now(ts)
    cur = conn.execute('INSERT INTO list_snapshot(run_id, market, kind, slug, category, ts, day, status, n) '
                       'VALUES (?,?,?,?,?,?,?,?,?)', (run_id, market, kind, slug, category, t,
                                                      config.market_day(t, market), status, len(items)))
    sid = cur.lastrowid
    for pos, i in enumerate(items, 1):
        conn.execute('INSERT OR REPLACE INTO list_item(snapshot_id, pos, asin, pct, rank_now, rank_before, price, rating, '
                     'reviews, deal) VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (sid, pos, i.get('asin'), i.get('pct'), i.get('rank_now'), i.get('rank_before'),
                      _num(i.get('price')), _num(i.get('rating')), i.get('reviews'), int(bool(i.get('deal')))))
    upsert_asins(conn, market, items, t)
    return sid


def list_history(conn, market, since_ts=0, kinds=('movers', 'new')):
    """List items since a time, with their snapshot's kind, slug, time and status (for surge scores). Best Sellers
    lists (kind 'best', the Gap Finder's proof) are left out unless asked for."""
    return _rows(conn.execute(
        'SELECT s.kind, s.slug, s.category, s.ts, s.day, s.status, s.n, i.*, a.title, a.image FROM list_snapshot s '
        'JOIN list_item i ON i.snapshot_id=s.id LEFT JOIN asin a ON a.market=s.market AND a.asin=i.asin '
        'WHERE s.market=? AND s.ts>=? AND s.kind IN (%s) ORDER BY s.ts' % ','.join('?' * len(kinds)),
        (market, since_ts, *kinds)))


def list_scans(conn, market, since_ts=0, kinds=('movers', 'new')):
    """List scans grouped as engine.surge expects: [{'ts', 'day', 'market', 'kind', 'slug', 'category', 'n',
    'status', 'items': [...]}], oldest first. 'n_median' is the median n of the same list in the window."""
    scans = {}
    for r in list_history(conn, market, since_ts, kinds):
        sc = scans.setdefault(r['snapshot_id'], {'id': r['snapshot_id'], 'ts': r['ts'], 'day': r['day'], 'market': market,
                                                 'kind': r['kind'], 'slug': r['slug'], 'category': r['category'],
                                                 'n': r['n'], 'status': r['status'], 'items': []})
        sc['items'].append({k: r[k] for k in ('asin', 'pos', 'pct', 'rank_now', 'rank_before', 'price', 'rating',
                                              'reviews', 'title', 'image')} | {'deal': bool(r['deal'])})
    for r in conn.execute('SELECT id, ts, day, kind, slug, category, n, status FROM list_snapshot '
                          'WHERE market=? AND ts>=? AND n=0 AND kind IN (%s)' % ','.join('?' * len(kinds)),
                          (market, since_ts, *kinds)):
        scans.setdefault(r[0], {'id': r[0], 'ts': r[1], 'day': r[2], 'market': market, 'kind': r[3], 'slug': r[4],
                                'category': r[5], 'n': 0, 'status': r[7], 'items': []})
    out = sorted(scans.values(), key=lambda s: s['ts'])
    ns = {}
    for s in out:
        ns.setdefault((s['kind'], s['slug']), []).append(s['n'] or 0)
    for s in out:
        v = sorted(ns[(s['kind'], s['slug'])])
        s['n_median'] = v[len(v) // 2]
    return out


# ---------- product pages ----------
def save_product(conn, market, asin, p, ts=None, run_id=None):
    """One product page reading; also refreshes the listing's fixed facts and adds a calibration point."""
    import product as prod
    t = _now(ts)
    status = p.get('status') or prod.page_status(p)
    cur = conn.execute(
        'INSERT INTO product_snapshot(run_id, market, asin, ts, day, status, root_rank, root_node, sub_ranks_json, badge_low, '
        'price, offers, rating, reviews, buybox_seller, ships_from, sold_by_amazon, n_variations, delivery_days, origin, '
        'seller_id, fulfillment, available, merchant_id, safety_json, listing_json) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (run_id, market, asin, t, config.market_day(t, market), status, p.get('root_rank'), p.get('root_node'),
         json.dumps(p.get('sub_ranks') or []), p.get('badge_low'), _num(p.get('price')), p.get('offers'),
         _num(p.get('rating')), p.get('reviews'), p.get('buybox_seller'), p.get('ships_from'), p.get('sold_by_amazon'),
         p.get('n_variations'), p.get('delivery_days'), p.get('origin'), p.get('seller_id'), p.get('fulfillment'),
         None if p.get('available') is None else int(bool(p['available'])), p.get('merchant_id'),
         json.dumps({'safety': p.get('safety') or [], 'item_form': p.get('item_form'),
                     'bullets': (p.get('bullets') or '')[:600]}) if status in VALID else None,
         json.dumps(p['listing']) if status in VALID and p.get('listing') else None))
    if status in VALID and p.get('related'):
        save_related(conn, market, asin, p['related'], t)
    if status in VALID:
        upsert_asins(conn, market, [{'asin': asin, 'title': p.get('title'), 'image': p.get('image'), 'brand': p.get('brand')}], t)
        dims = p.get('pkg_cm') or [None, None, None]
        conn.execute('UPDATE asin SET root_node=COALESCE(?, root_node), root_name=COALESCE(?, root_name), '
                     'dfa_day=COALESCE(?, dfa_day), n_variations=?, pkg_l_cm=COALESCE(?, pkg_l_cm), pkg_w_cm=COALESCE(?, pkg_w_cm), '
                     'pkg_h_cm=COALESCE(?, pkg_h_cm), pkg_kg=COALESCE(?, pkg_kg), static_fetched_at=? WHERE market=? AND asin=?',
                     (p.get('root_node'), p.get('root_name'), p.get('dfa_day'), p.get('n_variations'), dims[0], dims[1],
                      dims[2], p.get('pkg_kg'), t, market, asin))
        cp = prod.calib_point(market, asin, p, t)
        if cp:
            conn.execute('INSERT INTO calib_point(market, node, ts, ln_bsr, low, high, kind, asin, ref_id) VALUES (?,?,?,?,?,?,?,?,?)',
                         (cp['market'], cp['node'], cp['ts'], cp['ln_bsr'], cp['low'], cp['high'], cp['kind'], asin, cur.lastrowid))
        items = p.get('review_items')
        if items is not None and (items or not p.get('reviews')):     # no reviews seen counts only for a listing without any
            save_reviews(conn, market, asin, items, 'page', t)
    return cur.lastrowid


def product_history(conn, market, asin, since_ts=0):
    rows = _rows(conn.execute('SELECT * FROM product_snapshot WHERE market=? AND asin=? AND ts>=? ORDER BY ts',
                              (market, asin, since_ts)))
    for r in rows:
        r['sub_ranks'] = json.loads(r.pop('sub_ranks_json') or '[]')
    return rows


# ---------- "customers also bought" and the other product-page carousels ----------
def save_related(conn, market, asin, related, ts=None):
    """One page's carousels {kind: [{'asin', 'title'}]}: each kind it showed replaces that kind's links."""
    t = _now(ts)
    for kind, items in related.items():
        conn.execute('DELETE FROM related WHERE market=? AND asin=? AND kind=?', (market, asin, kind))
        for pos, it in enumerate(items, 1):
            conn.execute('INSERT OR REPLACE INTO related(market, asin, kind, other, pos, ts) VALUES (?,?,?,?,?,?)',
                         (market, asin, kind, it['asin'], pos, t))
            conn.execute("INSERT INTO asin(market, asin, title, image, first_seen, last_seen) VALUES (?,?,?,'',?,?) "
                         "ON CONFLICT(market, asin) DO UPDATE SET title=COALESCE(NULLIF(title, ''), excluded.title)",
                         (market, it['asin'], (it.get('title') or '')[:300] or None, t, t))   # a carousel's short title never wins


def related_links(conn, market, asins):
    """Every carousel link from or to these ASINs: [{'asin', 'kind', 'other', 'pos', 'ts'}]."""
    if not asins:
        return []
    q = ','.join('?' * len(asins))
    return _rows(conn.execute('SELECT * FROM related WHERE market=? AND (asin IN (%s) OR other IN (%s)) ORDER BY kind, pos'
                              % (q, q), (market, *asins, *asins)))


def asin_info(conn, market, asin):
    r = conn.execute('SELECT * FROM asin WHERE market=? AND asin=?', (market, asin)).fetchone()
    return dict(r) if r else None


# ---------- reviews ----------
def save_reviews(conn, market, asin, items, source, ts=None):
    """Keep the critical (1-3 star) reviews read from a listing's product page (source 'page') or its reviews page
    ('critical'), and when that page was read. Returns how many critical reviews it had."""
    t = _now(ts)
    crit = [r for r in items or [] if r.get('id') and r.get('stars') and r['stars'] <= 3]
    for r in crit:
        conn.execute(
            'INSERT INTO review(market, asin, id, stars, title, body, day, country, local, helpful, verified, source, first_seen, '
            'last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market, asin, id) DO UPDATE SET stars=excluded.stars, '
            'title=excluded.title, body=excluded.body, day=excluded.day, country=excluded.country, local=excluded.local, '
            'helpful=excluded.helpful, verified=excluded.verified, last_seen=excluded.last_seen',
            (market, asin, r['id'], r['stars'], (r.get('title') or '')[:300], (r.get('body') or '')[:1500], r.get('day'),
             (r.get('country') or '')[:60], 1 if r.get('local') else 0, int(r.get('helpful') or 0), 1 if r.get('verified') else 0,
             source, t, t))
    col = 'critical_at' if source == 'critical' else 'page_at'
    conn.execute('INSERT INTO review_read(market, asin, %s) VALUES (?,?,?) ON CONFLICT(market, asin) DO UPDATE SET %s=excluded.%s'
                 % (col, col, col), (market, asin, t))
    return len(crit)


def reviews_of(conn, market, asins):
    """The stored critical reviews of these listings, and when each listing's reviews were read ({asin: row})."""
    if not asins:
        return [], {}
    marks = ','.join('?' * len(asins))
    rows = _rows(conn.execute('SELECT * FROM review WHERE market=? AND asin IN (%s)' % marks, (market, *asins)))
    reads = _rows(conn.execute('SELECT * FROM review_read WHERE market=? AND asin IN (%s)' % marks, (market, *asins)))
    return rows, {r['asin']: r for r in reads}


# ---------- sellers ----------
def save_seller(conn, market, seller_id, s, ts=None):
    """One reading of a seller's profile page (a reading that found less keeps what was known before)."""
    t = _now(ts)
    old = conn.execute('SELECT * FROM seller WHERE market=? AND seller_id=?', (market, seller_id)).fetchone()
    old = dict(old) if old else {}

    def keep(k):
        return s.get(k) if s.get(k) is not None else old.get(k)
    conn.execute('INSERT OR REPLACE INTO seller(market, seller_id, name, business_name, country, rating_pct, ratings, status, '
                 'fetched_at) VALUES (?,?,?,?,?,?,?,?,?)',
                 (market, seller_id, keep('name'), keep('business_name'), keep('country'), keep('rating_pct'), keep('ratings'),
                  s.get('status') or 'ok', t))


def sellers_of(conn, market, ids):
    """{seller_id: row} for these sellers."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    rows = conn.execute('SELECT * FROM seller WHERE market=? AND seller_id IN (%s)' % ','.join('?' * len(ids)), (market, *ids))
    return {r['seller_id']: dict(r) for r in rows}


def latest_products(conn, market, asins):
    """{asin: its newest good product-page reading, with the listing's brand and launch day}."""
    if not asins:
        return {}
    rows = conn.execute(
        'SELECT p.*, a.brand, a.dfa_day FROM product_snapshot p LEFT JOIN asin a ON a.market=p.market AND a.asin=p.asin '
        "WHERE p.market=? AND p.asin IN (%s) AND p.status IN ('ok','partial') ORDER BY p.ts" % ','.join('?' * len(asins)),
        (market, *asins))
    return {r['asin']: dict(r) for r in rows}                     # ordered by time: the newest wins


def latest_page(conn, market, asin, since_ts=0):
    """The newest product-page reading of one listing since a time, whatever its status (a 'missing' page counts),
    with its safety facts unpacked; None when it was not read."""
    r = conn.execute('SELECT p.*, a.title, a.brand, a.image, a.pkg_l_cm, a.pkg_w_cm, a.pkg_h_cm, a.pkg_kg FROM product_snapshot p '
                     'LEFT JOIN asin a ON a.market=p.market AND a.asin=p.asin WHERE p.market=? AND p.asin=? AND p.ts>=? '
                     'ORDER BY p.ts DESC LIMIT 1', (market, asin, since_ts)).fetchone()
    if not r:
        return None
    d = dict(r)
    d.update(json.loads(d.pop('safety_json') or '{}'))
    return d


# ---------- who offers a listing, and the Gap Finder's checks ----------
def save_offers(conn, market, asin, offers, summary, source, status='ok', ts=None):
    """One reading of who offers a listing (engine.offers shape), from its product page or the SP-API."""
    conn.execute('INSERT INTO offer_snapshot(market, asin, ts, source, status, available, n_offers, amazon, n_fba, n_fbm, '
                 'offers_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                 (market, asin, _now(ts), source, status, None if summary.get('available') is None else int(bool(summary['available'])),
                  summary.get('n_offers'), int(bool(summary.get('amazon'))), summary.get('n_fba'), summary.get('n_fbm'),
                  json.dumps(offers or [])))


def latest_offers(conn, market, asin, before_ts=None):
    """The newest good offers reading of a listing (before a time, when given): {'ts', 'source', 'available', 'n_offers',
    'amazon', 'offers'} or None."""
    sql = "SELECT * FROM offer_snapshot WHERE market=? AND asin=? AND status='ok'"
    params = [market, asin]
    if before_ts is not None:
        sql += ' AND ts<?'
        params.append(before_ts)
    r = conn.execute(sql + ' ORDER BY ts DESC, id DESC LIMIT 1', params).fetchone()
    if not r:
        return None
    d = dict(r)
    d['offers'] = json.loads(d.pop('offers_json') or '[]')
    return d


def save_gap_check(conn, market, asin, cluster, phrase, status, gap_type, data, ts=None):
    t = _now(ts)
    conn.execute('INSERT INTO gap_check(ts, day, market, asin, cluster, phrase, status, gap_type, data_json) '
                 'VALUES (?,?,?,?,?,?,?,?,?)', (t, config.market_day(t, market), market, asin, cluster, phrase, status,
                                                gap_type, json.dumps(data)))


def gap_checks(conn, since_ts=0):
    """The newest check of each product (cluster) in each target market since a time: [{..., 'data'}]."""
    rows = _rows(conn.execute(
        'SELECT g.* FROM gap_check g JOIN (SELECT cluster, market, MAX(id) AS id FROM gap_check WHERE ts>=? '
        'GROUP BY cluster, market) n ON n.id=g.id ORDER BY g.ts DESC', (since_ts,)))
    for r in rows:
        r['data'] = json.loads(r.pop('data_json') or '{}')
    return rows


def calib_obs(conn, market, now=None):
    """Calibration points as engine.curve observations."""
    now = _now(now)
    out = []
    for r in conn.execute('SELECT * FROM calib_point WHERE market=?', (market,)):
        kind = r['kind']
        o = {'market': market, 'node': r['node'] or None, 'ln_bsr': r['ln_bsr'], 'asin': r['asin'],
             'age_days': max(0.0, (now - (r['ts'] or now)) / 86400)}
        if kind in ('h10_parent', 'h10_child'):
            o.update(kind='h10', value=r['low'])
        elif kind == 'own':
            o.update(kind='own', value=r['low'])
        else:
            o.update(kind='badge', low=r['low'], high=r['high'])
        out.append(o)
    return out


def save_curve(conn, market, data, ts=None):
    conn.execute('INSERT OR REPLACE INTO curve(market, node, fitted_at, data_json) VALUES (?,?,?,?)',
                 (market, '_all', _now(ts), json.dumps(data)))


def get_curve(conn, market):
    r = conn.execute("SELECT fitted_at, data_json FROM curve WHERE market=? AND node='_all'", (market,)).fetchone()
    if not r:
        return None
    d = json.loads(r[1])
    d['fitted_at'] = r[0]
    return d


def tracked_asins(conn, market, top_per_keyword=5):
    """ASINs worth reading product pages for: watched ASINs, plus the top organic listings of watched keywords."""
    out = [r[0] for r in conn.execute("SELECT target FROM watch WHERE kind='asin' AND market=? AND enabled=1", (market,))]
    for (kw,) in conn.execute("SELECT target FROM watch WHERE kind='keyword' AND market=? AND enabled=1", (market,)).fetchall():
        snap = conn.execute("SELECT id FROM serp_snapshot WHERE market=? AND kw_norm=? AND status IN ('ok','partial') "
                            "ORDER BY ts DESC LIMIT 1", (market, kw)).fetchone()
        if snap:
            out += [r[0] for r in conn.execute('SELECT asin FROM serp_item WHERE snapshot_id=? AND sponsored=0 ORDER BY pos '
                                               'LIMIT ?', (snap[0], top_per_keyword))]
    seen, uniq = set(), []
    for a in out:
        if a and a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


# ---------- search suggestions ----------
def save_suggest(conn, market, seed, keywords, n_calls=0, n_failed=0, status='ok', run_id=None, ts=None):
    t = _now(ts)
    cur = conn.execute('INSERT INTO suggest_snapshot(run_id, market, seed_norm, ts, day, status, n_calls, n_failed) '
                       'VALUES (?,?,?,?,?,?,?,?)', (run_id, market, config.norm_kw(seed), t,
                                                    config.market_day(t, market), status, n_calls, n_failed))
    for k in keywords:
        conn.execute('INSERT INTO suggest_item(snapshot_id, keyword, hits, best) VALUES (?,?,?,?)',
                     (cur.lastrowid, k.get('keyword'), k.get('hits'), k.get('best')))
    return cur.lastrowid


def suggestions(conn, market, seeds, since_ts=0):
    """{seed_norm: {'ts', 'keywords': [...] best first}} from the newest readable suggestion snapshot of each seed."""
    seeds = list(dict.fromkeys(k for k in (config.norm_kw(s) for s in seeds) if k))
    newest = {}
    for i in range(0, len(seeds), 400):
        part = seeds[i:i + 400]
        for r in conn.execute('SELECT id, seed_norm, ts FROM suggest_snapshot WHERE market=? AND ts>=? AND status IN (%s) '
                              'AND seed_norm IN (%s) ORDER BY ts, id' % (','.join('?' * len(VALID)), ','.join('?' * len(part))),
                              (market, since_ts, *VALID, *part)):
            newest[r['seed_norm']] = (r['id'], r['ts'])
    out = {s: {'ts': ts, 'keywords': []} for s, (_, ts) in newest.items()}
    seed_of = {sid: s for s, (sid, _) in newest.items()}
    ids = list(seed_of)
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        for r in conn.execute('SELECT snapshot_id, keyword FROM suggest_item WHERE snapshot_id IN (%s) ORDER BY rowid'
                              % ','.join('?' * len(part)), part):
            if r['keyword']:
                out[seed_of[r['snapshot_id']]]['keywords'].append(r['keyword'])
    return out


def suggest_history(conn, market, seeds, since_ts=0):
    """{seed_norm: [(ts, [keywords best first]), ...] oldest first} from every readable suggestion snapshot."""
    seeds = list(dict.fromkeys(k for k in (config.norm_kw(s) for s in seeds) if k))
    snaps = {}
    for i in range(0, len(seeds), 400):
        part = seeds[i:i + 400]
        for r in conn.execute('SELECT id, seed_norm, ts FROM suggest_snapshot WHERE market=? AND ts>=? AND status IN (%s) '
                              'AND seed_norm IN (%s) ORDER BY ts, id' % (','.join('?' * len(VALID)), ','.join('?' * len(part))),
                              (market, since_ts, *VALID, *part)):
            snaps[r['id']] = (r['seed_norm'], r['ts'], [])
    ids = list(snaps)
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        for r in conn.execute('SELECT snapshot_id, keyword FROM suggest_item WHERE snapshot_id IN (%s) ORDER BY rowid'
                              % ','.join('?' * len(part)), part):
            if r['keyword']:
                snaps[r['snapshot_id']][2].append(r['keyword'])
    out = {}
    for seed, ts, kws in snaps.values():
        out.setdefault(seed, []).append((ts, kws))
    return out


# ---------- your choices per product ----------
def prefs(conn, market, asins=None):
    """{asin: {'cogs_usd', 'cogs_at', 'phrase', 'phrase_at', 'liked_at', 'hidden_at'}} for a market (all, or these)."""
    if asins is None:
        return {r['asin']: dict(r) for r in conn.execute('SELECT * FROM product_pref WHERE market=?', (market,))}
    out, asins = {}, list(dict.fromkeys(asins))
    for i in range(0, len(asins), 400):
        part = asins[i:i + 400]
        for r in conn.execute('SELECT * FROM product_pref WHERE market=? AND asin IN (%s)' % ','.join('?' * len(part)),
                              (market, *part)):
            out[r['asin']] = dict(r)
    return out


def set_pref(conn, market, asin, ts=None, **fields):
    """Set or clear your choices for one product: cogs_usd (US$ or None), phrase (text or None), liked / hidden
    (True stamps the time, False clears it). Returns the stored row."""
    t = _now(ts)
    cols = {}
    if 'cogs_usd' in fields:
        cols.update(cogs_usd=fields['cogs_usd'], cogs_at=t if fields['cogs_usd'] is not None else None)
    if 'phrase' in fields:
        cols.update(phrase=fields['phrase'] or None, phrase_at=t if fields['phrase'] else None)
    for k in ('liked', 'hidden'):
        if k in fields:
            cols[k + '_at'] = t if fields[k] else None
    conn.execute('INSERT OR IGNORE INTO product_pref(market, asin) VALUES (?,?)', (market, asin))
    if cols:
        conn.execute('UPDATE product_pref SET %s WHERE market=? AND asin=?' % ', '.join('%s=?' % c for c in cols),
                     (*cols.values(), market, asin))
    return dict(conn.execute('SELECT * FROM product_pref WHERE market=? AND asin=?', (market, asin)).fetchone())


def hidden_products(conn, market, limit=100):
    """The products you marked Not for me in a market, newest first, with their title and picture."""
    return _rows(conn.execute('SELECT p.asin, p.hidden_at, a.title, a.image FROM product_pref p LEFT JOIN asin a '
                              'ON a.market=p.market AND a.asin=p.asin WHERE p.market=? AND p.hidden_at IS NOT NULL '
                              'ORDER BY p.hidden_at DESC LIMIT ?', (market, limit)))


# ---------- Google Trends ----------
def save_trends_sample(conn, term, geo, timeframe, status, points=None, resolution=None, user_type=None, ts=None):
    cur = conn.execute('INSERT INTO trends_sample(term, geo, timeframe, fetched_at, status, resolution, user_type, '
                       'points_json) VALUES (?,?,?,?,?,?,?,?)',
                       (config.norm_kw(term), geo, timeframe, _now(ts), status, resolution, user_type,
                        json.dumps(points or [])))
    return cur.lastrowid


def trends_samples(conn, term, geo, timeframe='today 5-y', since_ts=0):
    rows = _rows(conn.execute('SELECT * FROM trends_sample WHERE term=? AND geo=? AND timeframe=? AND fetched_at>=? '
                              'ORDER BY fetched_at', (config.norm_kw(term), geo, timeframe, since_ts)))
    for r in rows:
        r['points'] = json.loads(r.pop('points_json') or '[]')
    return rows


def save_trends_related(conn, term, geo, timeframe, items, ts=None):
    t = _now(ts)
    for i in items:
        conn.execute('INSERT INTO trends_related(term, geo, timeframe, fetched_at, kind, query, value, breakout) '
                     'VALUES (?,?,?,?,?,?,?,?)', (config.norm_kw(term), geo, timeframe, t, i.get('kind'),
                                                  i.get('query'), i.get('value'), int(bool(i.get('breakout')))))


def trends_related(conn, term, geo):
    """The newest related-queries fetch for a term."""
    last = conn.execute('SELECT MAX(fetched_at) FROM trends_related WHERE term=? AND geo=?',
                        (config.norm_kw(term), geo)).fetchone()[0]
    if not last:
        return []
    return _rows(conn.execute('SELECT kind, query, value, breakout, fetched_at FROM trends_related '
                              'WHERE term=? AND geo=? AND fetched_at=? ORDER BY kind, value DESC',
                              (config.norm_kw(term), geo, last)))


def save_trends_feature(conn, term, geo, data, ts=None):
    conn.execute('INSERT OR REPLACE INTO trends_feature(term, geo, computed_at, label, score, data_json) '
                 'VALUES (?,?,?,?,?,?)', (config.norm_kw(term), geo, _now(ts), data.get('label'),
                                          _num(data.get('score')), json.dumps(data)))


def trends_feature(conn, term, geo):
    r = conn.execute('SELECT * FROM trends_feature WHERE term=? AND geo=?', (config.norm_kw(term), geo)).fetchone()
    if not r:
        return None
    out = json.loads(r['data_json'])
    out['computed_at'] = r['computed_at']
    return out


# ---------- Helium 10 imports ----------
def import_known(conn, sha1):
    return conn.execute('SELECT id FROM import_file WHERE sha1=?', (sha1,)).fetchone() is not None


H10_COLS = ('asin', 'title', 'brand', 'price', 'sales_asin', 'sales_parent', 'revenue', 'bsr', 'category', 'reviews',
            'rating', 'fba_fees', 'size_tier', 'weight_kg', 'created', 'seller_country', 'fulfillment', 'sponsored')


def save_import(conn, kind, market, kw, file, sha1, rows, as_of, volumes=None, ts=None):
    """One Helium 10 export. Returns the import id, or None if this exact file was imported before."""
    if import_known(conn, sha1):
        return None
    as_of = _now(as_of)
    cur = conn.execute('INSERT INTO import_file(kind, market, kw_norm, file, sha1, as_of, month, imported_at, n_rows) '
                       'VALUES (?,?,?,?,?,?,?,?,?)', (kind, market, config.norm_kw(kw) or None, str(file)[:300], sha1,
                                                      as_of, config.month_key(as_of, market), _now(ts), len(rows)))
    iid = cur.lastrowid
    for r in rows:
        conn.execute('INSERT INTO h10_row(import_id, %s) VALUES (?%s)' % (', '.join(H10_COLS), ',?' * len(H10_COLS)),
                     [iid] + [_num(r.get(c)) if c in ('price', 'sales_asin', 'sales_parent', 'revenue', 'rating',
                                                      'fba_fees', 'weight_kg') else r.get(c) for c in H10_COLS])
    upsert_asins(conn, market, [r for r in rows if r.get('asin')], as_of)
    for v in volumes or []:
        conn.execute('INSERT OR REPLACE INTO kw_volume(market, kw_norm, month, volume, source, import_id) '
                     'VALUES (?,?,?,?,?,?)', (market, config.norm_kw(v['keyword']), config.month_key(as_of, market),
                                              v.get('volume'), kind, iid))
    return iid


def imports_for(conn, market, kw=None):
    sql = 'SELECT * FROM import_file WHERE market=?'
    params = [market]
    if kw:
        sql += ' AND kw_norm=?'
        params.append(config.norm_kw(kw))
    return _rows(conn.execute(sql + ' ORDER BY as_of', params))


def h10_rows(conn, import_id):
    return _rows(conn.execute('SELECT * FROM h10_row WHERE import_id=?', (import_id,)))


def kw_volume(conn, market, kw):
    return _rows(conn.execute('SELECT * FROM kw_volume WHERE market=? AND kw_norm=? ORDER BY month',
                              (market, config.norm_kw(kw))))


# ---------- watchlist ----------
WATCH_KINDS = ('keyword', 'asin', 'seed', 'category', 'lone')
STAGES = ('idea', 'researching', 'sourcing', 'ordered', 'launched', 'rejected')


def add_watch(conn, kind, market, target, display=None, cadence_h=24, ts=None):
    target = config.norm_kw(target) if kind in ('keyword', 'seed') else str(target).strip()
    conn.execute('INSERT INTO watch(kind, market, target, display, cadence_h, added_at) VALUES (?,?,?,?,?,?) '
                 'ON CONFLICT(kind, market, target) DO UPDATE SET enabled=1',
                 (kind, market, target, display or target, cadence_h, _now(ts)))
    return conn.execute('SELECT id FROM watch WHERE kind=? AND market=? AND target=?', (kind, market, target)).fetchone()[0]


def update_watch(conn, watch_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ('stage', 'reject_reason', 'cadence_h', 'notes', 'cogs', 'enabled',
                                                        'display')}
    if 'stage' in allowed and allowed['stage'] not in STAGES:
        raise ValueError('unknown stage')
    if allowed:
        conn.execute('UPDATE watch SET %s WHERE id=?' % ', '.join('%s=?' % k for k in allowed),
                     [*allowed.values(), watch_id])


def remove_watch(conn, watch_id):
    conn.execute('DELETE FROM watch WHERE id=?', (watch_id,))


def watches(conn, enabled_only=True):
    sql = 'SELECT * FROM watch' + (' WHERE enabled=1' if enabled_only else '') + ' ORDER BY added_at DESC'
    return _rows(conn.execute(sql))


# ---------- alerts ----------
def add_alerts(conn, alerts):
    """Insert alerts; one with a dedupe_key that already exists is skipped. Returns the number added."""
    n = 0
    for a in alerts:
        cur = conn.execute('INSERT OR IGNORE INTO alert(ts, rule, severity, market, target, title, body, data_json, '
                           'dedupe_key) VALUES (?,?,?,?,?,?,?,?,?)',
                           (_now(a.get('ts')), a.get('rule'), a.get('severity', 1), a.get('market'), a.get('target'),
                            a.get('title'), a.get('body'), json.dumps(a.get('data') or {}), a.get('dedupe_key')))
        n += cur.rowcount
    return n


def list_alerts(conn, since_ts=0, include_dismissed=False, limit=200):
    sql = 'SELECT * FROM alert WHERE ts>=?' + ('' if include_dismissed else ' AND dismissed_at IS NULL')
    rows = _rows(conn.execute(sql + ' ORDER BY ts DESC LIMIT ?', (since_ts, limit)))
    for r in rows:
        r['data'] = json.loads(r.pop('data_json') or '{}')
    return rows


def mark_alerts(conn, ids, field='seen_at', ts=None):
    if field not in ('seen_at', 'dismissed_at'):
        raise ValueError(field)
    for i in ids:
        conn.execute('UPDATE alert SET %s=COALESCE(%s, ?) WHERE id=?' % (field, field), (_now(ts), i))


# ---------- small key/value state ----------
def get_state(conn, key, default=None):
    r = conn.execute('SELECT value FROM ui_state WHERE key=?', (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_state(conn, key, value):
    conn.execute('INSERT OR REPLACE INTO ui_state(key, value) VALUES (?,?)', (key, json.dumps(value)))


# ---------- maintenance ----------
def retention(conn, now=None):
    """Drop old raw data. Classification flags stay, so old searches can still be recounted."""
    now = _now(now)
    day = 86400
    out = {}
    out['delivery_cleared'] = conn.execute(
        'UPDATE serp_item SET delivery=NULL WHERE delivery IS NOT NULL AND snapshot_id IN '
        '(SELECT id FROM serp_snapshot WHERE ts<?)', (now - DELIVERY_KEEP_DAYS * day,)).rowcount
    # older than 180 days: keep the items of the first snapshot per keyword, market and month only
    out['serp_items_dropped'] = conn.execute(
        'DELETE FROM serp_item WHERE snapshot_id IN (SELECT id FROM serp_snapshot WHERE ts<? AND id NOT IN '
        '(SELECT MIN(id) FROM serp_snapshot GROUP BY market, kw_norm, month))',
        (now - SERP_ITEM_KEEP_DAYS * day,)).rowcount
    out['list_items_dropped'] = conn.execute(
        'DELETE FROM list_item WHERE snapshot_id IN (SELECT id FROM list_snapshot WHERE ts<?)',
        (now - LIST_ITEM_KEEP_DAYS * day,)).rowcount
    out['fetch_log_dropped'] = conn.execute('DELETE FROM fetch_log WHERE ts<?',
                                            (now - FETCH_LOG_KEEP_DAYS * day,)).rowcount
    out['alerts_dropped'] = conn.execute('DELETE FROM alert WHERE ts<?', (now - ALERT_KEEP_DAYS * day,)).rowcount
    old = 'ts<? AND id NOT IN (SELECT MAX(id) FROM suggest_snapshot WHERE status IN (%s) GROUP BY market, seed_norm)' % (
        ','.join("'%s'" % s for s in VALID))
    cut = (now - SUGGEST_KEEP_DAYS * day,)
    out['suggest_items_dropped'] = conn.execute(
        'DELETE FROM suggest_item WHERE snapshot_id IN (SELECT id FROM suggest_snapshot WHERE %s)' % old, cut).rowcount
    out['suggest_snapshots_dropped'] = conn.execute('DELETE FROM suggest_snapshot WHERE %s' % old, cut).rowcount
    gap_cut = (now - GAP_KEEP_DAYS * day,)
    out['gap_checks_dropped'] = conn.execute(
        'DELETE FROM gap_check WHERE ts<? AND id NOT IN (SELECT MAX(id) FROM gap_check GROUP BY cluster, market)',
        gap_cut).rowcount
    out['offer_readings_dropped'] = conn.execute(
        'DELETE FROM offer_snapshot WHERE ts<? AND id NOT IN (SELECT MAX(id) FROM offer_snapshot GROUP BY market, asin)',
        gap_cut).rowcount
    return out


def counts(conn):
    """Row counts for the Health page."""
    out = {}
    for t in ('serp_snapshot', 'serp_item', 'list_snapshot', 'trends_sample', 'import_file', 'h10_row', 'alert',
              'watch', 'keyword', 'asin'):
        out[t] = conn.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0]
    return out
