"""Out of stock: the booming and brand-new products of Product ideas, watched in the USA, Australia and the UAE.

Every day (and when you press Check now) the app takes the Booming and brand-new products from the Movers & Shakers and
New Releases lists it reads anyway (engine.stockwatch.candidates), and reads each one's listing in the USA, Australia and
the UAE: listed there or not yet, in stock or not, how many sellers (gapfinder.read_listing: the SP-API when its keys
are saved, else the product page, read again after 20 hours). A product that ran out recently with fewer than 5 sellers
shows on the Out of stock tab and raises a Home alert. A read that was blocked or paused is kept as not read.
"""
import asyncio
import logging
import time

import amazon_check as ac
import gapfinder
import storage
import throttle
from engine import stockwatch as sw

log = logging.getLogger('pc')
MARKETS = ('US', 'AU', 'AE')
USER_READS = 25                 # listings a Check now reads per market (press it again for more)
AUTO_READS = 20                 # ... and the automatic refresh, per market and day
SHOWN = 150


def max_sellers(settings):
    return int(settings.get('oos_max_sellers') or sw.THRESH['max_sellers'])


def _ideas(conn, now):
    rows = []
    for m in MARKETS:
        rows += [dict(r, market=m) for r in storage.list_history(conn, m, now - sw.THRESH['idea_days'] * 86400,
                                                              ('movers', 'new', 'best'))]
    return rows


def watched(db, now=None):
    """(candidates, readings) for the tab and the next run."""
    now = now or time.time()

    def build(conn):
        return sw.candidates(_ideas(conn, now), now), storage.stock_checks(conn, now - 90 * 86400)
    return db.read(build)


def table(db, settings, now=None):
    """The Out of stock tab: every watched product with its state in each market, the ones that fit first."""
    now = now or time.time()
    cands, checks = watched(db, now)
    rs = sw.rows(cands, checks, now, MARKETS, max_sellers(settings))
    last = max((r[0]['ts'] for r in checks.values() if r), default=None)
    auto = db.one("SELECT MAX(last_run_at) AS t FROM task WHERE kind='stock'") or {}
    return {'rows': rs[:SHOWN], 'watched': len(cands), 'fits': sum(1 for r in rs if r['fits']),
            'booming': sum(1 for c in cands if c['kind'] == 'booming'), 'new': sum(1 for c in cands if c['kind'] == 'new'),
            'read': sum(1 for r in rs if any(s['state'] != 'unread' for s in r['markets'].values())),
            'last': last, 'auto_last': auto.get('t'), 'max_sellers': max_sellers(settings),
            'auto': bool(settings.get('oos_auto', True) and settings.get('auto_refresh', True))}


def _alert(row, mk, now):
    s = row['markets'][mk]
    title = (row['title'] or row['asin'])[:90]
    return {'rule': 'oos_few', 'severity': 3, 'market': mk, 'target': row['asin'],
            'title': 'Out of stock with %s seller%s: %s' % (s['sellers'] if s['sellers'] is not None else 'few',
                                                           '' if s['sellers'] == 1 else 's', title),
            'body': '%s on Amazon %s. %s.' % (row['idea']['why'], sw.MARKET_NAMES[mk], s['why']),
            'data': {'asin': row['asin'], 'sellers': s['sellers'], 'idea': row['idea']['why']},
            'dedupe_key': 'oos|%s|%s|%s' % (mk, row['asin'], int(s['since'] or 0) // 86400), 'ts': now}


async def run(S, markets=MARKETS, n=USER_READS, progress=None):
    """Read the next watched listings (at most n per market, the markets side by side). Returns {'status', 'note'}."""
    say = progress or (lambda m: None)
    now = time.time()
    cands, checks = await asyncio.to_thread(watched, S.db, now)
    if not cands:
        return {'status': 'ok', 'note': 'No booming or brand-new products yet: Product ideas reads them (Movers & Shakers, '
                                        'New Releases).'}
    per = {m: [a for _, a in sw.plan(cands, checks, now, n, (m,))] for m in markets}     # each market its own share
    client = getattr(S, 'spapi', None)
    stops, counts = {}, {m: 0 for m in markets}
    if any(per.values()):
        await S.checker.start()

    async def one_market(m):
        ctx = {}
        for i, asin in enumerate(per[m], 1):
            if ctx.get('stop'):
                break
            say('Amazon %s: stock %d of %d · %s' % (sw.MARKET_NAMES[m], i, len(per[m]), asin))
            try:
                got = await gapfinder.read_listing(S, m, asin, client, ctx, fresh=20 * 3600)
            except Exception as e:                                   # noqa: BLE001 - one bad listing never ends the run
                log.warning('stock %s %s failed: %s', m, asin, e)
                continue
            if got['status'] in ('ok', 'missing'):
                counts[m] += 1
            await S.db.awrite(storage.save_stock_check, m, asin, sw.reading(got))
        if ctx.get('stop'):
            stops[m] = ctx['stop']
    await asyncio.gather(*(one_market(m) for m in markets if per[m]))
    t = await asyncio.to_thread(table, S.db, S.settings)
    alerts = [_alert(r, m, now) for r in t['rows'] if r['fits'] for m in r['out']]
    fresh = S.db.write(gapfinder.add_new_alerts, alerts) if alerts else []
    if fresh:
        import analysis
        S.say('%d product%s out of stock with few sellers: see Market Gaps, Out of stock.' % (len(fresh), '' if len(fresh) == 1 else 's'))
        await asyncio.to_thread(analysis.notify_new, S, fresh)
    note = '%d listing%s read (%s); %d product%s out of stock with %d sellers or fewer' % (
        sum(counts.values()), '' if sum(counts.values()) == 1 else 's',
        ', '.join('%s %d' % (sw.MARKET_NAMES[m], counts[m]) for m in markets if per[m]) or 'all read today',
        t['fits'], '' if t['fits'] == 1 else 's', t['max_sellers'])
    if stops:
        note += '; stopped: %s' % '; '.join('%s: %s' % (sw.MARKET_NAMES[m], gapfinder._first_line(e)) for m, e in stops.items())
        blocked = [e for e in stops.values() if isinstance(e, ac.BlockedError)]
        if blocked and ac.BACKGROUND.get():
            raise blocked[0]
    return {'status': 'partial' if stops else 'ok', 'note': note}


def reads_for(task_market):
    return [task_market] if task_market in MARKETS else list(MARKETS)


async def run_task(S, task):
    """The automatic refresh: one market's watched listings, within its daily budget."""
    try:
        return await run(S, reads_for(task['market']), AUTO_READS)
    except throttle.QuotaExceeded as e:
        return {'status': 'partial', 'note': str(e)}
