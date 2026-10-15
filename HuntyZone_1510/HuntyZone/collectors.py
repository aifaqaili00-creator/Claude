"""What each scheduled task does. The scheduler (scheduler.py) decides when; these decide what.

Every handler takes a task row ({'kind', 'market', 'target', ...}) and returns {'status', 'note', 'data'}.
Blocks raise amazon_check.BlockedError, so the scheduler can cool that marketplace down.
"""
import asyncio
import logging
import time

import amazon_check as ac
import config
import file_rank as fr
import gapfinder
import ideas
import product
import reports
import scheduler as sc
import storage
import throttle

log = logging.getLogger('pc')
LIST_CATEGORIES = 8                     # categories per market when you have not chosen any (automatic refresh)
USER_CATEGORIES = 16                    # ... in a scan you start yourself
CATEGORY_TTL = 24 * 3600                # Amazon's category menu is read again once a day
QUALIFY_TOP = 10                        # radar products the automatic refresh qualifies per market and day
QUALIFY_CHECKS = 4                      # ... of which this many are searched on the markets they could sell in next
QUALIFY_PAGES = 6                       # ... and this many product pages read
VARIANTS = 4                            # longer phrases 'Find the underserved version' searches
QUALIFY_ABROAD = 4                      # top phrases whose related searches and trends in the next markets are read
QUALIFY_WORDS = {'suggestions': ('Amazon suggestion list', 'Amazon suggestion lists'),
                 'trends': ('Google trend', 'Google trends'),
                 'abroad': ('Google trend in another market', 'Google trends in other markets'),
                 'readiness': ('suggestion list in another market', 'suggestion lists in other markets'),
                 'searches': ('search in another market', 'searches in other markets'),
                 'pages': ('product page', 'product pages')}


def _dur(s):
    return '%d s' % s if s < 90 else '%d min' % round(s / 60)


def task_specs(settings, watches):
    """The work to schedule, from the watchlist and settings."""
    specs = []
    if not settings.get('auto_refresh', True):
        return specs
    for m in config.MARKETS:
        specs.append({'kind': 'lists', 'market': m, 'target': 'all', 'every_s': sc.EVERY['lists:' + m], 'priority': 3})
        if settings.get('auto_qualify', True):
            specs.append({'kind': 'qualify', 'market': m, 'target': 'radar', 'every_s': sc.EVERY['qualify'], 'priority': 4})
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
    if settings.get('gap_auto', True):
        for m in gapfinder.PROOF:
            specs.append({'kind': 'proof', 'market': m, 'target': 'best', 'every_s': sc.EVERY['proof'], 'priority': 6})
        for m in config.TARGET_MARKETS:
            specs.append({'kind': 'gaps', 'market': m, 'target': 'proven', 'every_s': sc.EVERY['gaps'], 'priority': 5})
    if settings.get('mg_auto', True):
        for m in config.TARGET_MARKETS:
            specs.append({'kind': 'mgaps', 'market': m, 'target': 'niches', 'every_s': sc.EVERY['mgaps'], 'priority': 6})
    for m in sorted({w['market'] for w in watches if w['kind'] == 'lone' and w.get('enabled', 1)}):
        specs.append({'kind': 'lone', 'market': m, 'target': 'watched', 'every_s': sc.EVERY['lone'], 'priority': 3})
    if settings.get('board_live', True):
        specs.append({'kind': 'board_live', 'market': '', 'target': 'spapi', 'every_s': sc.EVERY['board_live'], 'priority': 4})
    specs.append({'kind': 'import_scan', 'market': '', 'target': 'downloads', 'every_s': sc.EVERY['import_scan'], 'priority': 6})
    specs.append({'kind': 'maintenance', 'market': '', 'target': 'db', 'every_s': sc.EVERY['maintenance'], 'priority': 9})
    return specs


class Collectors:
    def __init__(self, state):
        self.S = state
        self.cats = {}                                  # market -> categories read from Amazon

    def handlers(self):
        return {'products': self.products, 'lists': self.lists, 'watch_kw': self.watch_kw, 'trends': self.trends, 'suggest': self.suggest,
                'import_scan': self.import_scan, 'maintenance': self.maintenance, 'qualify': self.qualify,
                'proof': self.proof, 'gaps': self.gaps, 'lone': self.lone, 'board_live': self.board_live,
                'mgaps': self.mgaps}

    async def mgaps(self, task):
        """Market Gaps: a few Best Sellers categories and baseline niches of Australia or the UAE read again."""
        import marketgaps
        return await marketgaps.refresh_market(self.S, task['market'], marketgaps.AUTO_TOP, marketgaps.AUTO_CATS)

    async def board_live(self, task):
        """Product board: units left and daily sales from the SP-API (nothing to do without keys)."""
        import board
        client = getattr(self.S, 'spapi', None)
        if not client:
            return {'status': 'ok', 'note': 'no SP-API keys: units left are typed by you'}
        out = await asyncio.to_thread(board.sync_live, self.S.db, client, self.S.settings)
        if out['alerts'] and self.S.settings.get('board_notify', True):
            import analysis
            await asyncio.to_thread(analysis.notify_new, self.S, out['alerts'])
        note = '%d card%s: %d new stock count%s, %d days of sales' % (
            out['cards'], '' if out['cards'] == 1 else 's', out['counts'], '' if out['counts'] == 1 else 's', out['sales_days'])
        if out['errors']:
            note += '; ' + '; '.join(out['errors'])[:200]
        return {'status': 'partial' if out['errors'] else 'ok', 'note': note}

    async def proof(self, task):
        """Gap Finder: Best Sellers of a proof market (USA, UK, Germany)."""
        return await gapfinder.read_proof_lists(self.S, task['market'])

    async def gaps(self, task):
        """Gap Finder: the next proven products checked in Australia or the UAE."""
        return await gapfinder.check_market(self.S, task['market'], gapfinder.CHECK_TOP)

    async def lone(self, task):
        """Gap Finder: the lone exporters you watch, read again."""
        return await gapfinder.recheck_lone(self.S, task['market'])

    async def _categories(self, market, limit=LIST_CATEGORIES):
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
        return safe if any(c.get('default') for c in safe) else safe[:limit]

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
        cats = await self._categories(market, USER_CATEGORIES if progress else LIST_CATEGORIES)
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

    async def underserved(self, market, phrase, boom=None, progress=None, n=VARIANTS):
        """Find the underserved version of a phrase: the longer phrases Amazon suggests for it, searched in the markets
        it could sell in next (searches of the last few hours are reused) and ranked by their gap score there
        (reports.variant_gaps). Your own job: a captcha shows as a failed search of that phrase."""
        from engine import discovery
        say = progress or (lambda m: None)
        kw = config.norm_kw(phrase)
        say('Amazon %s suggestions for "%s"…' % (ac.MARKETS[market]['name'], kw))
        got = await ideas.suggest(self.S.checker, market, kw)
        await self.S.db.awrite(storage.save_suggest, market, kw, got['keywords'], got.get('n_calls', 0),
                               got.get('n_failed', 0), 'ok' if got['keywords'] else 'empty')
        variants = discovery.pick_variants(kw, [k['keyword'] for k in got['keywords']], n)
        targets = reports.TARGETS[market]
        if variants:
            say('Searching %d phrase%s on Amazon %s…' % (len(variants), '' if len(variants) == 1 else 's',
                                                       ' and '.join(ac.MARKETS[t]['name'] for t in targets)))
            await self.S.check_batch([{'key': v + '|' + t, 'market': t, 'search': v} for v in variants for t in targets], say)
        return await asyncio.to_thread(reports.variant_gaps, self.S.db, market, kw, variants, boom or {}, self.S.settings,
                                       {k['keyword']: k['hits'] for k in got['keywords']})

    async def qualify(self, task):
        """The automatic refresh's qualifying: the market's best radar products that still miss something, within the
        day's 'qualify' budgets."""
        market = task['market']
        rows = await asyncio.to_thread(reports.qualify_candidates, self.S.db, market, self.S.settings, None,
                                       QUALIFY_TOP, QUALIFY_CHECKS)
        if not rows:
            return {'status': 'ok', 'note': 'nothing to qualify'}
        return await self.qualify_products(market, [r['asin'] for r in rows], checks=QUALIFY_CHECKS, pages=QUALIFY_PAGES)

    async def qualify_products(self, market, asins, progress=None, checks=0, pages=12, phrases=None):
        """Read what the opportunity score misses for these products, one step at a time for all of them: the phrase
        shoppers type (Amazon's suggestions for the title's first words; `phrases` {asin: words} you typed win), its
        Google trend, whether the suggestions on the markets it could sell in next know the phrase, a search there (the
        first `checks` products only) and the product page (at most `pages`). Anything read recently is not read again.
        A pausing source or a used-up budget ends that step only. A captcha or block on a product page ends the
        automatic refresh's run. Returns {'status', 'note', 'counts'}."""
        from engine import discovery
        S, db = self.S, self.S.db
        say = progress or (lambda m: None)
        name = ac.MARKETS[market]['name']
        fresh = reports.QUALIFY_FRESH
        now = time.time()
        items = []
        for a in asins:
            title = (db.read(storage.asin_info, market, a) or {}).get('title') or ''
            words = fr.search_words(title)
            if words:
                items.append({'asin': a, 'title': title, 'words': words, 'prefixes': discovery.phrase_prefixes(words)})
        done = {'suggestions': 0, 'trends': 0, 'abroad': 0, 'readiness': 0, 'searches': 0, 'pages': 0}
        stopped = []
        mine = db.read(storage.prefs, market, asins)

        async def ask(code, prefix):
            """Keep Amazon's suggestions for a prefix; False when the call failed (a pause or budget raises)."""
            try:
                got = await ideas.complete(S.checker, code, prefix)
            except throttle.ThrottleError:
                raise
            except Exception as e:                                   # noqa: BLE001 - one failed call skips one prefix
                log.warning('suggestions %s "%s" failed: %s', code, prefix, e)
                return False
            kws = [{'keyword': k, 'hits': 1, 'best': n} for n, k in enumerate(got, 1) if k]
            await db.awrite(storage.save_suggest, code, prefix, kws, 1, 0, 'ok' if kws else 'empty')
            return True

        try:                                                         # 1. the phrase shoppers type
            for n, it in enumerate(items, 1):
                have = db.read(storage.suggestions, market, it['prefixes'], now - fresh['phrase'] * 86400)
                for p in it['prefixes']:
                    if p not in have:
                        say('%s: Amazon suggestions for "%s" (%d of %d)' % (name, p, n, len(items)))
                        done['suggestions'] += await ask(market, p)
        except throttle.ThrottleError as e:
            stopped.append('suggestions: %s' % e)
        for it in items:
            known = db.read(storage.suggestions, market, it['prefixes'])
            it['phrase'] = (phrases or {}).get(it['asin']) or (mine.get(it['asin']) or {}).get('phrase') or discovery.pick_phrase(
                it['title'], [k for p in it['prefixes'] for k in known.get(p, {}).get('keywords', [])])
            it['kw'] = config.norm_kw(it['phrase'] or it['words'])

        if S.settings.get('trends_enabled', True):                   # 2. its Google trend, here and abroad
            terms = list(dict.fromkeys(it['phrase'] or discovery.suggest_term(it['words']) for it in items))
            reads = [(term, market, n <= QUALIFY_ABROAD) for n, term in enumerate(terms, 1)]
            reads += [(term, t, False) for t in reports.TARGETS[market] for term in terms[:QUALIFY_ABROAD]]
            try:
                for n, (term, geo, related) in enumerate(reads, 1):
                    if S.trends.breaker.is_open():
                        stopped.append('Google Trends is paused (%s)' % (S.trends.breaker.reason or 'asked to slow down'))
                        break
                    say('%s: Google Trends for "%s" in %s (%d of %d)' % (name, term, ac.MARKETS[geo]['name'], n, len(reads)))
                    got = await S.refresh_trends(term, geo, related=related) is not None
                    done['trends' if geo == market else 'abroad'] += got
            except throttle.ThrottleError as e:
                stopped.append('Google Trends: %s' % e)

        kws = list(dict.fromkeys(it['kw'] for it in items))
        for t in reports.TARGETS[market]:                            # 3. do the next markets' suggestions know it
            try:
                have = db.read(storage.suggestions, t, kws, now - fresh['phrase'] * 86400)
                for n, kw in enumerate(kws, 1):
                    h = have.get(kw)
                    if h and (discovery.autocomplete_knows(kw, h['keywords']) or h['ts'] >= now - fresh['arrival'] * 86400):
                        continue                                     # not known there yet: asked weekly to catch it arriving
                    say('%s: does Amazon %s suggest "%s"? (%d of %d)' % (name, ac.MARKETS[t]['name'], kw, n, len(kws)))
                    done['readiness'] += await ask(t, kw)
            except throttle.ThrottleError as e:
                stopped.append('%s suggestions: %s' % (ac.MARKETS[t]['name'], e))

        if checks:                                                   # 4. a search there (automatic refresh only)
            fast, local, _ = S.days()
            for t in reports.TARGETS[market]:
                for kw in list(dict.fromkeys(it['kw'] for it in items[:checks])):
                    if db.read(storage.latest_serp, t, kw, None, None, fresh['check'] * 86400):
                        continue
                    say('%s: searching "%s" on Amazon %s' % (name, kw, ac.MARKETS[t]['name']))
                    try:
                        s = await S.checker.check(kw, t, fast, 1, None, local)
                    except ac.BlockedError as e:                     # that market said stop, not this one
                        await S.save_check({'market': t, 'keyword': kw, 'status': e.status, 'error': str(e)[:200]},
                                           None, 1, 'qualify')
                        S.throttle.report('amazon:' + t, e.status, str(e)[:120])
                        stopped.append('Amazon %s: %s' % (ac.MARKETS[t]['name'], str(e).splitlines()[0][:120]))
                        break
                    except throttle.ThrottleError as e:
                        stopped.append('Amazon %s: %s' % (ac.MARKETS[t]['name'], e))
                        break
                    await S.save_check(s, None, 1, 'qualify')
                    done['searches'] += 1

        block = None                                                 # 5. the product page, last: the slowest
        try:
            for it in items:
                if done['pages'] >= pages:
                    break
                last = db.one("SELECT MAX(ts) AS ts FROM product_snapshot WHERE market=? AND asin=? AND "
                              "status IN ('ok','partial')", (market, it['asin']))
                if last and (last['ts'] or 0) >= now - fresh['page'] * 86400:
                    continue
                say('%s: product page %s' % (name, it['asin']))
                p = await product.read_product(S.checker, it['asin'], market)
                await db.awrite(storage.save_product, market, it['asin'], p)
                done['pages'] += 1
        except throttle.ThrottleError as e:
            stopped.append('product pages: %s' % e)
        except ac.BlockedError as e:
            if ac.BACKGROUND.get():
                block = e
            stopped.append('product pages: %s' % str(e).splitlines()[0][:120])
        if done['pages']:
            await S.refit_curve(market)
        if block:
            raise block
        what = ', '.join('%d %s' % (v, QUALIFY_WORDS[k][v != 1]) for k, v in done.items() if v)
        note = '%d product%s: %s' % (len(items), '' if len(items) == 1 else 's', what or 'nothing new to read')
        if stopped:
            note += '; stopped: ' + '; '.join(stopped)
        return {'status': 'partial' if stopped else 'ok', 'note': note, 'counts': done}

    async def maintenance(self, task):
        out = await self.S.db.awrite(storage.retention)
        for m in config.MARKETS:
            await self.S.refit_curve(m)
        await asyncio.to_thread(self.S.db.routine_backups)
        ok = await asyncio.to_thread(self.S.db.quick_check)
        if not ok:
            self.S.say('The history database failed its integrity check. A backup is kept in the backup folder.')
        return {'status': 'ok' if ok else 'error', 'note': ', '.join('%s %d' % kv for kv in out.items() if kv[1])}
