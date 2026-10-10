"""What each scheduled task does. The scheduler (scheduler.py) decides when; these decide what.

Every handler takes a task row ({'kind', 'market', 'target', ...}) and returns {'status', 'note', 'data'}.
Blocks raise amazon_check.BlockedError, so the scheduler can cool that marketplace down.
"""
import asyncio
import logging
import time

import amazon_check as ac
import config
import ideas
import product
import scheduler as sc
import storage
import throttle

log = logging.getLogger('pc')
LIST_CATEGORIES = 8                     # categories per market when you have not chosen any
CATEGORY_TTL = 24 * 3600                # Amazon's category menu is read again once a day


def _dur(s):
    return '%d s' % s if s < 90 else '%d min' % round(s / 60)


def task_specs(settings, watches):
    """The work to schedule, from the watchlist and settings."""
    specs = []
    if not settings.get('auto_refresh', True):
        return specs
    for m in config.MARKETS:
        specs.append({'kind': 'lists', 'market': m, 'target': 'all', 'every_s': sc.EVERY['lists:' + m], 'priority': 3})
    for w in watches:
        if not w.get('enabled', 1):
            continue
        if w['kind'] == 'keyword':
            specs.append({'kind': 'watch_kw', 'market': w['market'], 'target': w['target'],
                          'every_s': max(6, int(w.get('cadence_h') or 24)) * 3600, 'priority': 2})
            if settings.get('trends_enabled', True):
                specs.append({'kind': 'trends', 'market': w['market'], 'target': w['target'],
                              'every_s': sc.EVERY['trends'], 'priority': 4})
        elif w['kind'] == 'seed':
            specs.append({'kind': 'suggest', 'market': w['market'], 'target': w['target'],
                          'every_s': sc.EVERY['suggest'], 'priority': 4})
    if any(w.get('enabled', 1) and w['kind'] in ('keyword', 'asin') for w in watches):
        for m in sorted({w['market'] for w in watches if w['kind'] in ('keyword', 'asin')}):
            specs.append({'kind': 'products', 'market': m, 'target': 'tracked', 'every_s': sc.EVERY['products'], 'priority': 5})
    specs.append({'kind': 'import_scan', 'market': '', 'target': 'downloads', 'every_s': sc.EVERY['import_scan'], 'priority': 6})
    specs.append({'kind': 'maintenance', 'market': '', 'target': 'db', 'every_s': sc.EVERY['maintenance'], 'priority': 9})
    return specs


class Collectors:
    def __init__(self, state):
        self.S = state
        self.cats = {}                                  # market -> categories read from Amazon

    def handlers(self):
        return {'products': self.products, 'lists': self.lists, 'watch_kw': self.watch_kw, 'trends': self.trends, 'suggest': self.suggest,
                'import_scan': self.import_scan, 'maintenance': self.maintenance}

    async def _categories(self, market):
        chosen = self.S.settings.get('categories', {}).get(market) or []
        hit = self.cats.get(market)
        if not hit or not hit['cats'] or time.time() - hit['at'] > CATEGORY_TTL:
            cats = await ideas.categories(self.S.checker, market)        # a block raises; never empty otherwise
            if cats:                                                      # an empty answer is never kept
                hit = self.cats[market] = {'at': time.time(), 'cats': cats}
        cats = hit['cats'] if hit else []
        if chosen:
            return [c for c in cats if c['slug'] in chosen] or [{'slug': s, 'name': s, 'risky': False} for s in chosen]
        safe = [c for c in cats if not c['risky']]
        return safe if any(c.get('default') for c in safe) else safe[:LIST_CATEGORIES]

    def _forget(self, market, slug):
        """A built-in category slug that Amazon does not have: stop asking for it."""
        hit = self.cats.get(market)
        if hit:
            hit['cats'] = [c for c in hit['cats'] if not (c['slug'] == slug and c.get('default'))]

    async def lists(self, task, progress=None):
        """Movers & Shakers and New Releases for the market's categories. Each list is saved as soon as it is read.

        progress is given for a scan you started yourself: two lists are read at a time and each step is reported.
        The automatic refresh reads one at a time. A captcha or block stops the run; any other problem skips one list.
        """
        market = task['market']
        name = ac.MARKETS[market]['name']
        say = progress or (lambda m: None)
        say('%s: opening the browser…' % name)
        await self.S.checker.start()
        say('%s: reading the category list…' % name)
        cats = await self._categories(market)
        if not cats:
            raise RuntimeError('No categories found on Amazon %s. Try again in a few minutes.' % name)
        defaults = any(c.get('default') for c in cats)
        jobs = [(c, k) for c in cats for k in ('movers', 'new')]
        state = {'ok': 0, 'short': 0, 'empty': 0, 'missing': 0, 'failed': 0, 'done': 0, 'items': 0}
        t0 = time.time()
        sem = asyncio.Semaphore(2 if progress else 1)
        stop = []

        async def one(cat, kind):
            async with sem:
                if stop:
                    return
                label = 'Movers & Shakers' if kind == 'movers' else 'New Releases'
                try:
                    items = await ideas.read_list(self.S.checker, market, kind, cat['slug'], cat['name'])
                except ideas.ListMissing:
                    state['missing'] += 1
                    self._forget(market, cat['slug'])
                    items = None
                except (ac.BlockedError, throttle.ThrottleError) as e:  # captcha, block, pause or budget: stop
                    stop.append(e)
                    return
                except Exception as e:                               # noqa: BLE001 - one bad list never ends the scan
                    log.warning('list %s %s/%s failed: %s', market, cat['slug'], kind, e)
                    state['failed'] += 1
                    items = None
                if items is not None:
                    status = 'ok' if len(items) >= 30 else 'partial' if items else 'empty_suspect'
                    await self.S.db.awrite(storage.save_list, market, kind, cat['slug'], cat['name'], items, status)
                    state['ok' if status == 'ok' else 'short' if items else 'empty'] += 1
                    state['items'] += len(items)
                state['done'] += 1
                left = (time.time() - t0) / state['done'] * (len(jobs) - state['done'])
                say('%s: list %d of %d · %s · %s%s' % (name, state['done'], len(jobs), cat['name'], label,
                                                     ' (about %s left)' % _dur(left) if state['done'] < len(jobs) else ''))
        await asyncio.gather(*(one(c, k) for c, k in jobs))
        read = state['ok'] + state['short']
        if stop and not read:
            raise stop[0]
        if not read and progress:                       # your own scan read nothing: say why instead of an empty radar
            raise RuntimeError('Could not read any Movers & Shakers or New Releases list on Amazon %s%s. Try again later.' % (
                name, " (its category menu could not be read either; a copy of the page is in the app folder's debug "
                      "folder)" if defaults else ''))
        note = '%d list%s read' % (read, '' if read == 1 else 's')
        if state['short']:
            note += ' (%d with fewer than 30 products)' % state['short']
        if state['empty']:
            note += ', %d empty' % state['empty']
        if state['failed']:
            note += ', %d failed' % state['failed']
        if defaults:
            note += ' (Amazon\'s category menu could not be read, so built-in %s categories were used)' % name
        if stop:
            note += '; stopped early: %s' % str(stop[0]).splitlines()[0][:120]
        return {'status': 'ok' if state['ok'] else 'partial' if read else 'empty', 'note': note}

    async def products(self, task, max_age_s=20 * 3600):
        """Product pages of tracked listings (BSR, badge, offers), oldest reading first, until today's budget ends."""
        market = task['market']
        asins = self.S.db.read(storage.tracked_asins, market)
        last = {r['asin']: r['ts'] for r in self.S.db.all(
            'SELECT asin, MAX(ts) AS ts FROM product_snapshot WHERE market=? GROUP BY asin', (market,))}
        now = time.time()
        todo = sorted((a for a in asins if now - last.get(a, 0) > max_age_s), key=lambda a: last.get(a, 0))
        n_ok = 0
        for asin in todo:
            p = await product.read_product(self.S.checker, asin, market)        # QuotaExceeded ends the run
            await self.S.db.awrite(storage.save_product, market, asin, p)
            n_ok += p['status'] == 'ok'
        if todo:
            await self.S.refit_curve(market)
        return {'status': 'ok' if n_ok or not todo else 'partial', 'note': '%d of %d product pages read' % (n_ok, len(todo))}

    async def watch_kw(self, task):
        """Search a watched keyword again and keep the snapshot."""
        fast, local, pages = self.S.days()
        kw, market = task['target'], task['market']
        disp = self.S.db.one('SELECT display FROM keyword WHERE kw_norm=?', (kw,))
        try:
            s = await self.S.checker.check(disp['display'] if disp else kw, market, fast, 1, None, local)
        except ac.BlockedError as e:
            await self.S.save_check({'market': market, 'keyword': kw, 'status': e.status, 'error': str(e)[:200]},
                                    None, 1, 'schedule')
            raise
        await self.S.save_check(s, None, 1, 'schedule')
        return {'status': storage.serp_status(s), 'note': '%d fast, %d local, %d overseas' % (s['fast'], s['local'], s['overseas'])}

    async def trends(self, task):
        old = self.S.db.read(storage.trends_feature, task['target'], task['market'])
        new = await self.S.refresh_trends(task['target'], task['market'])
        if new is None:
            return {'status': 'throttled', 'note': self.S.trends.breaker.reason or 'Google Trends paused'}
        return {'status': 'ok' if new.get('status') in ('ok', 'low_volume') else 'empty', 'note': new.get('label') or '',
                'data': {'old': old, 'new': new}}

    async def suggest(self, task):
        out = await ideas.suggest(self.S.checker, task['market'], task['target'])
        status = 'ok' if out['keywords'] else 'empty' if not out.get('n_failed') else 'partial'
        await self.S.db.awrite(storage.save_suggest, task['market'], task['target'], out['keywords'],
                               out.get('n_calls', 0), out.get('n_failed', 0), status)
        return {'status': status, 'note': '%d keywords' % len(out['keywords'])}

    async def import_scan(self, task):
        res = await asyncio.to_thread(self.S.scan_imports)
        n = sum(1 for r in res if r['status'] == 'imported')
        return {'status': 'ok', 'note': '%d new files' % n}

    async def maintenance(self, task):
        out = await self.S.db.awrite(storage.retention)
        for m in config.MARKETS:
            await self.S.refit_curve(m)
        await asyncio.to_thread(self.S.db.routine_backups)
        ok = await asyncio.to_thread(self.S.db.quick_check)
        if not ok:
            self.S.say('The history database failed its integrity check. A backup is kept in the backup folder.')
        return {'status': 'ok' if ok else 'error', 'note': ', '.join('%s %d' % kv for kv in out.items() if kv[1])}
