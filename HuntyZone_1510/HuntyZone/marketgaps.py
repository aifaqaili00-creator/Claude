"""Market Gaps: the SmartScout baseline, kept fresh by the app's own readings of Amazon Australia and the UAE.

SmartScout was a one-off: its brand and seller exports (smartscout.py) say where the money was in October 2026. After
that the app reads Amazon itself, politely and within the same daily budgets as everything else:

1. sweep(): the Best Sellers lists of your categories in Australia or the UAE (a list read in the last 20 hours is
   reused), the buy box of the top few listings of each (product pages kept two days, or the SP-API), and where a lone
   seller is based. engine.marketgaps.category_table turns each into brand concentration and who holds the buy boxes:
   the app's own successor of SmartScout's brand table.
2. check_niches(): the best baseline niches that are due (never read, or read over a week ago): the phrase shoppers
   type there (Amazon's suggestions), page 1 of its search, the buy boxes of its top 3 listings.
   engine.marketgaps.live_verdict says whether the niche is still open. A gap that opens or starts filling up raises an
   alert (Home, and a Windows notification).
A read that was blocked, paused or out of budget is kept as 'unread' (a gap in the data), never as open.
"""
import asyncio
import logging
import time

import amazon_check as ac
import config
import gapfinder
import ideas
import intel
import reports
import storage
import throttle
from engine import ads as admod
from engine import curve as curvemod
from engine import marketgaps as mg

log = logging.getLogger('pc')
CACHE = {}                      # (market, baseline import ids) -> ranked niches; cleared by a new SmartScout import
SHOWN = 400                     # niches sent to the page per market
USER_TOP = 8                    # niches a Refresh you start checks per market
USER_CATS = 8                   # ... and Best Sellers categories it reads
AUTO_TOP = 4                    # the automatic refresh, per market and day
AUTO_CATS = 3
RECHECK_DAYS = 7                # a niche checked this recently is not checked again by a Refresh
CATEGORY_DAYS = 3               # a category whose buy boxes were read this recently only re-reads its list
BUYBOX_TOP = 5                  # top listings of a Best Sellers list whose buy box is read
NICHE_BUYBOXES = 3              # top organic listings of a niche's page 1 whose buy box is read
SELLER_PAGES = 2                # seller profiles read per niche or category (where a lone seller is based)


def _first_line(e):
    return gapfinder._first_line(e)


# ---------- the baseline ----------
def baseline_ids(conn, market):
    """The newest SmartScout imports a market's ranking rests on: (brands id, US brands id, sellers id)."""
    def newest(kind, mk):
        r = conn.execute('SELECT id FROM import_file WHERE kind=? AND market=? ORDER BY as_of DESC, id DESC LIMIT 1',
                         (kind, mk)).fetchone()
        return r[0] if r else None
    return newest('ss_brands', market), newest('ss_brands', 'US'), newest('ss_sellers', market)


def baseline(db, market):
    """A market's baseline, worked out once per import: {'niches' (engine.marketgaps.rank_niches), 'missing' (big US
    brands absent here), 'sellers' (sellers_summary), 'sellers_as_of'}."""
    ids = db.read(baseline_ids, market)
    key = (str(db.path), market, ids)
    if key not in CACHE:
        rows, _ = db.read(storage.ss_brands, market)
        us, _ = db.read(storage.ss_brands, 'US') if market != 'US' else ([], None)
        srows, s_as_of = db.read(storage.ss_sellers, market)
        if len(CACHE) > 8:
            CACHE.clear()
        CACHE[key] = {'niches': mg.rank_niches(rows, market, us),
                      'missing': mg.missing_brands(us, rows, limit=150) if us and rows else [],
                      'sellers': mg.sellers_summary(srows, market), 'sellers_as_of': s_as_of}
    return CACHE[key]


def ranked(db, market):
    """The scored baseline niches of a market, best first."""
    return baseline(db, market)['niches']


def table(db, settings, market):
    """Everything the Market Gaps page shows for one market."""
    B = baseline(db, market)
    niches = B['niches']
    checks = db.read(storage.mg_checks, market)

    def build(conn):
        brands_n = conn.execute('SELECT n_rows, as_of, file FROM import_file WHERE id=?',
                                (baseline_ids(conn, market)[0],)).fetchone()
        imports = storage.ss_imports(conn)
        return (dict(brands_n) if brands_n else None, imports, storage.mg_categories(conn, market),
                conn.execute("SELECT MAX(last_run_at) FROM task WHERE kind='mgaps' AND market=?", (market,)).fetchone()[0])
    base, imports, cats, auto_last = db.read(build)
    rows = []
    for n in niches[:SHOWN]:
        c = checks.get(n['key'])
        rows.append({**n, 'live': _live(c)})
    shown = {n['key'] for n in rows}
    for key, c in checks.items():                  # a niche checked by hand that is not in the top SHOWN
        if key not in shown:
            n = next((x for x in niches if x['key'] == key), None)
            if n:
                rows.append({**n, 'live': _live(c)})
    gaps_n = [n for n in niches if n['gap']['gap']]
    total = sum(n['revenue'] for n in niches) or 0
    live = [r['live'] for r in rows if r['live']]
    cats.sort(key=lambda c: -(c['data'].get('units_total') or 0))
    return {
        'market': market, 'currency': ac.MARKETS[market]['currency'], 'site': ac.MARKETS[market]['site'],
        'baseline': {'as_of': (base or {}).get('as_of'), 'file': (base or {}).get('file'), 'brands': (base or {}).get('n_rows'),
                     'sellers_as_of': B['sellers_as_of'], 'us': db.read(baseline_ids, market)[1] is not None,
                     'files': [{k: i[k] for k in ('kind', 'market', 'file', 'as_of', 'n_rows')} for i in imports]},
        'kpis': {'niches': len(niches), 'gaps': len(gaps_n),
                 'global_store': round(sum(n['revenue'] * n['shares']['global_store'] for n in niches) / total, 4) if total else None,
                 'resellers': round(sum(n['revenue'] * n['shares']['reseller'] for n in niches) / total, 4) if total else None,
                 'amazon': round(sum(n['revenue'] * n['shares']['amazon'] for n in niches) / total, 4) if total else None,
                 'amazon_gap': sum(1 for n in niches if n['gap']['type'] == 'amazon_then_gap'),
                 'open': sum(1 for x in live if x['verdict'] in ('open', 'amazon_only')),
                 'checked': len(live), 'unread': sum(1 for x in live if x['verdict'] == 'unread')},
        'niches': rows, 'categories': cats,
        'missing': B['missing'], 'sellers': B['sellers'],
        'last_check': db.read(storage.mg_last_ts, market), 'auto_last': auto_last,
        'auto': bool(settings.get('mg_auto', True) and settings.get('auto_refresh', True)),
        'folder': settings.get('mg_dir') or default_folder(),
        'types': [{'type': k, 'label': v, 'means': m} for k, v, m in mg.TYPES],
        'verdicts': [{'verdict': k, 'label': v, 'means': m} for k, v, m in mg.VERDICTS],
    }


def _live(c):
    if not c:
        return None
    d = c['data']
    return {'verdict': c['verdict'], 'label': d.get('label') or mg.VLABEL.get(c['verdict']), 'why': d.get('why'),
            'conf': d.get('conf'), 'gap': d.get('gap'), 'ts': c['ts'], 'phrase': c['phrase'], 'status': c['status'],
            'fast': d.get('fast'), 'total': d.get('total'), 'over_share': d.get('over_share'), 'buyboxes': d.get('buyboxes'),
            'brands': d.get('brands') or [], 'top': d.get('top') or [], 'price_med': d.get('price_med'),
            'bought_total': d.get('bought_total'), 'stopped': d.get('stopped'),
            'history': [{'ts': t, 'verdict': v} for t, v in c.get('history', [])[-8:]]}


def default_folder():
    """The SmartScout folder next to the app (HuntyZone_1510/drive_Data_Scout), when it exists."""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / 'drive_Data_Scout'
    return str(p) if p.is_dir() else ''


def strong_niches(db, market, min_score=70):
    """{niche_key: score} of the baseline niches with an open gap and a high score (the Gap Finder checks products in
    them first)."""
    try:
        return {n['key']: n['score'] for n in ranked(db, market) if n['gap']['gap'] and n['score'] >= min_score}
    except Exception:                                # noqa: BLE001 - no baseline: nothing to prefer
        return {}


# ---------- 1. Best Sellers categories read now ----------
def _latest_list(conn, market, slug, since):
    r = conn.execute("SELECT id FROM list_snapshot WHERE market=? AND kind='best' AND slug=? AND ts>=? AND n>0 "
                     'ORDER BY ts DESC LIMIT 1', (market, slug, since)).fetchone()
    if not r:
        return None
    return [dict(x) for x in conn.execute(
        'SELECT i.asin, i.pos, i.price, i.rating, i.reviews, a.title, a.image FROM list_item i LEFT JOIN asin a '
        'ON a.market=? AND a.asin=i.asin WHERE i.snapshot_id=? ORDER BY i.pos', (market, r[0]))]


def _category_read(conn, market, slug, since):
    r = conn.execute("SELECT MAX(ts) FROM mg_category WHERE market=? AND slug=? AND ts>=? AND status IN ('ok','partial')",
                     (market, slug, since)).fetchone()
    return bool(r and r[0])


def _buybox(listing):
    offers = listing.get('offers') or []
    return next((o for o in offers if o.get('buybox')), offers[0] if offers else None)


async def sweep(S, mk, limit=USER_CATS, progress=None, ctx=None):
    """Best Sellers of up to `limit` categories in one market, each turned into brand and buy-box concentration and kept.
    Categories whose buy boxes were read in the last CATEGORY_DAYS days are skipped. Returns a note."""
    say = progress or (lambda m: None)
    ctx = ctx if ctx is not None else {}
    name = mg.MARKET_NAMES[mk]
    now = time.time()
    cats = [c for c in gapfinder.proof_categories(S.settings, mk)
            if not S.db.read(_category_read, mk, c['slug'], now - CATEGORY_DAYS * 86400)][:limit]
    if not cats:
        return 'Amazon %s: Best Sellers categories read in the last %d days kept' % (name, CATEGORY_DAYS)
    client = getattr(S, 'spapi', None)
    done, missing = 0, 0
    for i, c in enumerate(cats, 1):
        if ctx.get('stop'):
            break
        say('Amazon %s: Best Sellers %d of %d · %s' % (name, i, len(cats), c['name']))
        items = S.db.read(_latest_list, mk, c['slug'], now - gapfinder.LISTS_FRESH)
        if items is None:
            try:
                await S.checker.start()
                got = await ideas.read_list(S.checker, mk, 'best', c['slug'], c['name'])
            except ideas.ListMissing:
                missing += 1
                continue
            except (ac.BlockedError, throttle.ThrottleError) as e:
                ctx['stop'] = e
                break
            except Exception as e:                                 # noqa: BLE001 - one bad list never ends the run
                log.warning('Market Gaps best %s/%s failed: %s', mk, c['slug'], e)
                continue
            status = 'ok' if len(got) >= 30 else 'partial' if got else 'empty_suspect'
            await S.db.awrite(storage.save_list, mk, 'best', c['slug'], c['name'], got, status)
            items = [{**g, 'pos': n} for n, g in enumerate(got, 1)]
        offers = {}
        for it in items[:BUYBOX_TOP]:
            if ctx.get('stop'):
                break
            say('Amazon %s: who sells #%d in %s' % (name, it['pos'], c['name']))
            got = await gapfinder.read_listing(S, mk, it['asin'], client, ctx)
            o = _buybox(got) if got['status'] == 'ok' else None
            if o:
                offers[it['asin']] = o
        if offers:
            await gapfinder.fill_sellers(S, mk, list(offers.values()), ctx, SELLER_PAGES)

        def facts(conn):
            cv = storage.get_curve(conn, mk)
            return cv, intel._brands(conn, mk, [x['asin'] for x in items]), intel._names(conn, mk)
        cv, known, names = S.db.read(facts)
        units = (lambda rank, slug=c['slug']: curvemod.predict(cv, slug, rank)) if cv else None
        data = mg.category_table(items, mk, units, known, names, offers)
        data['top'] = [{'asin': x['asin'], 'title': (x.get('title') or '')[:140], 'price': x.get('price'), 'rank': x['pos'],
                        'reviews': x.get('reviews'), 'image': x.get('image'),
                        'offer': offers.get(x['asin'])} for x in items[:10]]
        status = 'ok' if len(offers) >= min(3, len(items)) else 'partial' if offers or items else 'unread'
        await S.db.awrite(storage.save_mg_category, mk, c['slug'], c['name'], status, data)
        done += 1
    note = 'Amazon %s: %d Best Sellers categor%s read' % (name, done, 'y' if done == 1 else 'ies')
    if missing:
        note += ', %d not on Amazon %s' % (missing, name)
    if ctx.get('stop'):
        note += '; stopped: %s' % _first_line(ctx['stop'])
    return note


# ---------- 2. baseline niches checked now ----------
async def niche_phrase(S, mk, n):
    """(phrase, how): Amazon's suggestion for the niche's name in this market, else the name itself."""
    base = mg.niche_phrase(n['name'])
    if not base:
        return None, 'none'
    have = S.db.read(storage.suggestions, mk, [base], time.time() - reports.QUALIFY_FRESH['phrase'] * 86400)
    if base not in have:
        try:
            got = await ideas.complete(S.checker, mk, base)
            kws = [{'keyword': k, 'hits': 1, 'best': i} for i, k in enumerate(got, 1) if k]
            await S.db.awrite(storage.save_suggest, mk, base, kws, 1, 0, 'ok' if kws else 'empty')
        except throttle.ThrottleError:
            pass
        except Exception as e:                                     # noqa: BLE001 - the name itself still works
            log.warning('suggestions %s "%s" failed: %s', mk, base, e)
        have = S.db.read(storage.suggestions, mk, [base])
    sugg = have.get(base, {}).get('keywords', [])
    p = mg.niche_phrase(n['name'], sugg)
    return p, 'suggestion' if p != base else 'name'


def due(niches, checks, now, n, keys=None):
    """The niches to check next: the ones you picked, else the best scored open baseline niches (not risky) never checked
    or checked over RECHECK_DAYS ago, never-checked first."""
    if keys is not None:
        want = list(dict.fromkeys(keys))
        by = {x['key']: x for x in niches}
        return [by[k] for k in want if k in by][:n]
    todo = [x for x in niches if x['gap']['gap'] and not x['risky']
            and now - (checks.get(x['key']) or {}).get('ts', 0) > RECHECK_DAYS * 86400]
    todo.sort(key=lambda x: (x['key'] in checks, -x['score']))
    return todo[:n]


def _alert(mk, n, prev, verdict, ts):
    """An alert when a checked niche opens or starts filling up (compared with its previous check), else None."""
    name = mg.MARKET_NAMES[mk]
    if not prev or prev == 'unread' or verdict['verdict'] == 'unread':
        return None
    was = prev in ('open', 'amazon_only')
    if verdict['gap'] and not was:
        title, sev = 'A gap opened: %s on Amazon %s' % (n['name'], name), 2
    elif was and verdict['verdict'] in ('closing', 'closed', 'few'):
        title, sev = 'Filling up: %s on Amazon %s' % (n['name'], name), 2
    else:
        return None
    return {'rule': 'mg_' + verdict['verdict'], 'severity': sev, 'market': mk, 'target': n['key'], 'title': title[:120],
            'body': verdict['why'], 'data': {'niche': n['key'], 'name': n['name'], 'verdict': verdict['verdict'], 'before': prev},
            'dedupe_key': 'mg|%s|%s|%s|%s' % (mk, n['key'], prev, verdict['verdict']), 'ts': ts}


async def check_niche(S, mk, n, client=None, ctx=None, say=None):
    """Check one baseline niche on Amazon now and keep it. Returns (data, alert or None)."""
    ctx = ctx if ctx is not None else {}
    say = say or (lambda m: None)
    name = mg.MARKET_NAMES[mk]
    say('Amazon %s: what shoppers type for "%s"' % (name, n['name']))
    phrase, how = await niche_phrase(S, mk, n)
    search = await gapfinder.search_phrase(S, mk, phrase, ctx) if phrase else {'status': 'no phrase', 'rows': []}
    search['phrase'] = phrase
    organic = [r for r in search.get('rows') or [] if not r.get('sponsored')]
    offers = []
    for r in organic[:NICHE_BUYBOXES]:
        if ctx.get('stop'):
            offers.append(None)
            continue
        say('Amazon %s: who sells %s' % (name, (r.get('title') or r.get('asin') or '')[:60]))
        got = await gapfinder.read_listing(S, mk, r['asin'], client, ctx)
        o = _buybox(got) if got['status'] == 'ok' else None
        offers.append(o)
        r['offer'] = o
    if [o for o in offers if o]:
        await gapfinder.fill_sellers(S, mk, [o for o in offers if o], ctx, SELLER_PAGES)
    verdict = mg.live_verdict(search, offers, mk)

    def facts(conn):
        return intel._brands(conn, mk, [r.get('asin') for r in organic]), intel._names(conn, mk)
    known, names = S.db.read(facts)
    data = {**verdict, 'phrase': phrase, 'phrase_how': how, 'niche': n['name'],
            'brands': mg.page_brands(organic, known, names, admod.avoid_words(phrase)),
            'price_med': search.get('price_med'), 'bought_total': search.get('bought_total'),
            'search_status': search.get('status'), 'snapshot_id': search.get('snapshot_id'),
            'top': [{k: r.get(k) for k in ('asin', 'title', 'price', 'origin', 'days', 'bought', 'speed', 'offer')}
                    for r in organic[:6]],
            'baseline': {'score': n['score'], 'gap': n['gap']['type'], 'revenue': n['revenue']},
            'stopped': _first_line(ctx['stop']) if ctx.get('stop') else None}
    status = 'ok' if verdict['verdict'] != 'unread' and all(offers) else 'partial' if verdict['verdict'] != 'unread' \
        else 'unread'
    prev = (S.db.read(storage.mg_checks, mk).get(n['key']) or {}).get('verdict')
    await S.db.awrite(storage.save_mg_check, mk, n['key'], phrase, status, verdict['verdict'], data)
    return data, _alert(mk, n, prev, verdict, time.time())


async def check_niches(S, mk, n=USER_TOP, progress=None, keys=None, ctx=None):
    """Check the next due baseline niches of one market (or the ones you picked). Returns {'note', 'counts', 'alerts'}."""
    say = progress or (lambda m: None)
    ctx = ctx if ctx is not None else {}
    name = mg.MARKET_NAMES[mk]
    niches = await asyncio.to_thread(ranked, S.db, mk)
    if not niches:
        return {'note': 'Amazon %s: no SmartScout baseline yet (import the exports first)' % name, 'counts': {}, 'alerts': []}
    checks = S.db.read(storage.mg_checks, mk)
    todo = due(niches, checks, time.time(), n, keys)
    if not todo:
        return {'note': 'Amazon %s: every open niche was checked in the last %d days' % (name, RECHECK_DAYS), 'counts': {},
                'alerts': []}
    client = getattr(S, 'spapi', None)
    counts, alerts = {}, []
    for i, x in enumerate(todo, 1):
        if ctx.get('stop'):
            break
        say('Amazon %s: niche %d of %d · %s' % (name, i, len(todo), x['name']))
        d, a = await check_niche(S, mk, x, client, ctx, say)
        counts[d['verdict']] = counts.get(d['verdict'], 0) + 1
        if a:
            alerts.append(a)
    open_ = counts.get('open', 0) + counts.get('amazon_only', 0)
    note = 'Amazon %s: %d of %d niche%s checked, %d still open' % (
        name, sum(counts.values()), len(todo), '' if len(todo) == 1 else 's', open_)
    if counts.get('unread'):
        note += ', %d not read' % counts['unread']
    if ctx.get('stop'):
        note += '; stopped: %s' % _first_line(ctx['stop'])
    return {'note': note, 'counts': counts, 'alerts': alerts}


async def _notify(S, alerts):
    fresh = S.db.write(gapfinder.add_new_alerts, alerts) if alerts else []
    if fresh:
        import analysis
        S.say('%d Market Gaps change%s: see Home.' % (len(fresh), '' if len(fresh) == 1 else 's'))
        await asyncio.to_thread(analysis.notify_new, S, fresh)
    return fresh


async def refresh_market(S, mk, n=USER_TOP, cats=USER_CATS, progress=None, keys=None):
    """One market: its Best Sellers categories (unless you picked niches), then the niches due. A block ends the run after
    keeping what was read; in the automatic refresh it is raised, so the scheduler pauses that marketplace."""
    ctx = {}
    notes = []
    if keys is None and cats:
        notes.append(await sweep(S, mk, cats, progress, ctx))
    if not ctx.get('stop'):
        out = await check_niches(S, mk, n, progress, keys, ctx)
        notes.append(out['note'])
        await _notify(S, out['alerts'])
    if ctx.get('stop') and isinstance(ctx['stop'], ac.BlockedError) and ac.BACKGROUND.get():
        raise ctx['stop']
    return {'status': 'partial' if ctx.get('stop') else 'ok', 'note': '; '.join(notes)}


async def refresh(S, progress, markets=None, n=USER_TOP, cats=USER_CATS, keys=None):
    """Your own Refresh: each market's Best Sellers categories and its next niches. Returns {'notes'}."""
    notes = []
    for mk in markets or config.TARGET_MARKETS:
        out = await refresh_market(S, mk, n, cats, progress, keys)
        notes.append(out['note'])
    return {'notes': notes}
