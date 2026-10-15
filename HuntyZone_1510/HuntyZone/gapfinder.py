"""Global Gap Finder: what reads Amazon (or the SP-API) for engine.gaps, and keeps what it found in market.db.

1. read_proof_lists(): Best Sellers of your categories in the USA, the UK and Germany, and the UK's and Germany's
   Movers & Shakers, once a day each (the USA's Movers & Shakers come from the radar's own list scans).
2. proven(): products selling well in at least two of them (Best Sellers top 100, Movers & Shakers products now in their
   Best Sellers top 100, "bought in past month" badges, the US sales curve, Helium 10 exports), the same product grouped
   across markets.
3. check_product(): one proven product in Australia or the UAE: the same ASIN there (SP-API when its keys are saved,
   else its product page), the phrase shoppers type there and page 1 of its search, other listings of the same
   product, and the seller profiles that say where a lone seller is based. engine.gaps.classify names the gap.
4. recheck_lone(): the lone exporters you watch, read again each day; a second seller raises an alert.
A read that was blocked or paused is kept as 'unread' (a gap in the data), never as "nobody sells it".
"""
import asyncio
import logging
import time

import amazon_check as ac
import config
import file_rank as fr
import ideas
import product
import reports
import spapi
import storage
import throttle
from engine import discovery, gaps
from engine import offers as offs

log = logging.getLogger('pc')
PROOF = ('US',) + config.PROOF_MARKETS
PROOF_DAYS = 10                 # Best Sellers and Movers & Shakers readings this recent count as proof
EVIDENCE_DAYS = 45              # ... and badges or Helium 10 exports this recent
LISTS_FRESH = 20 * 3600         # a market's Best Sellers read this recently are not read again by your own run
BEST_LISTS = 8                  # Best Sellers lists per proof market when you chose no categories
CHECK_TOP = 8                   # proven products the automatic refresh checks per market and day
USER_TOP = 12                   # ... and a check you start yourself
RECHECK_DAYS = 7                # a product's finished check in a market is repeated after a week
PAGE_FRESH = 2 * 86400          # a product page read this recently is not read again
SELLER_PAGES = 3                # seller profiles read per product check
SAME_PAGES = 2                  # pages of other listings of the same product read per check
SAME_SHOWN = 5                  # ... and kept with the check
FEW_SELLERS = 3                 # where sellers are based is read only when this few sell it


def mins(settings):
    """Units a month that count as selling well in each proof market (Settings)."""
    return {'US': settings.get('proof_us') or 300, 'GB': settings.get('proof_gb') or 100,
            'DE': settings.get('proof_de') or 100}


def _why(e):
    """The status a stopped read is kept with."""
    if isinstance(e, ac.BlockedError):
        return getattr(e, 'status', None) or 'blocked'
    return 'budget' if isinstance(e, throttle.QuotaExceeded) else 'paused'


def _first_line(e):
    return (str(e).strip() or e.__class__.__name__).splitlines()[0][:120]


# ---------- 1. proof lists ----------
def proof_categories(settings, market):
    """The categories whose Best Sellers are read: the ones you chose for this market, else the built-in ones."""
    chosen = (settings.get('categories') or {}).get(market) or []
    if chosen:
        return [{'slug': s, 'name': s.replace('-', ' ').title()} for s in chosen]
    out, names = [], set()
    for slug, name in ideas.DEFAULTS.get(market, ideas.DEFAULT_US):
        if name not in names:                            # e.g. two slugs for Garden: the first one is enough
            names.add(name)
            out.append({'slug': slug, 'name': name})
    return out[:BEST_LISTS]


def lists_read_since(conn, market, since_ts):
    return conn.execute("SELECT COUNT(*) FROM list_snapshot WHERE market=? AND kind='best' AND ts>=? AND n>0",
                        (market, since_ts)).fetchone()[0]


def fresh_lists(conn, market, since_ts):
    """{(kind, slug)} of the lists of a market read with products since a time."""
    return {(r[0], r[1]) for r in conn.execute('SELECT kind, slug FROM list_snapshot WHERE market=? AND ts>=? AND n>0',
                                                (market, since_ts))}


MOVERS_MARKETS = config.PROOF_MARKETS          # the US gets its Movers & Shakers from the radar's own list scans
LIST_NAMES = {'best': 'Best Sellers', 'movers': 'Movers & Shakers'}


async def read_proof_lists(S, market, progress=None):
    """Best Sellers of one proof market's categories, then (UK, Germany) their Movers & Shakers, each saved as soon as
    it is read; a list read in the last 20 hours is not read again. A captcha, block, pause or used-up budget ends the
    run (raised when nothing was read); any other problem skips one list."""
    say = progress or (lambda m: None)
    name = gaps.MARKET_NAMES[market]
    cats = proof_categories(S.settings, market)
    kinds = ('best', 'movers') if market in MOVERS_MARKETS else ('best',)
    fresh = S.db.read(fresh_lists, market, time.time() - LISTS_FRESH)
    todo = [(k, c) for k in kinds for c in cats if (k, c['slug']) not in fresh]
    if todo:
        await S.checker.start()
    n = {'best': 0, 'movers': 0, 'short': 0, 'failed': 0}
    missing, stop = set(), None
    for i, (kind, c) in enumerate(todo, 1):
        if c['slug'] in missing:
            continue
        say('%s: %s %d of %d · %s' % (name, LIST_NAMES[kind], i, len(todo), c['name']))
        try:
            items = await ideas.read_list(S.checker, market, kind, c['slug'], c['name'])
        except ideas.ListMissing:
            missing.add(c['slug'])
            continue
        except (ac.BlockedError, throttle.ThrottleError) as e:
            stop = e
            break
        except Exception as e:                                       # noqa: BLE001 - one bad list never ends the run
            log.warning('%s %s/%s failed: %s', kind, market, c['slug'], e)
            n['failed'] += 1
            continue
        status = 'ok' if len(items) >= 30 else 'partial' if items else 'empty_suspect'
        await S.db.awrite(storage.save_list, market, kind, c['slug'], c['name'], items, status)
        n[kind] += bool(items)
        n['short'] += 0 < len(items) < 30
    if stop and not (n['best'] or n['movers']):
        raise stop
    note = '%d Best Sellers list%s' % (n['best'], '' if n['best'] == 1 else 's')
    if 'movers' in kinds:
        note += ' and %d Movers & Shakers list%s' % (n['movers'], '' if n['movers'] == 1 else 's')
    note += ' read on Amazon %s' % name
    if n['short']:
        note += ' (%d with fewer than 30 products)' % n['short']
    kept = len(kinds) * len(cats) - len(todo)
    if kept:
        note += ', %d read in the last 20 hours kept' % kept
    if missing:
        note += ', %d categor%s not on Amazon %s' % (len(missing), 'y' if len(missing) == 1 else 'ies', name)
    if n['failed']:
        note += ', %d failed' % n['failed']
    if stop:
        note += '; stopped early: %s' % _first_line(stop)
    read = n['best'] + n['movers']
    return {'status': 'ok' if (read or kept or not todo) and not stop else 'partial' if read or kept else 'empty',
            'note': note}


# ---------- 2. proven products ----------
def evidence(conn, now):
    """Products of the proof markets with what says they sell: {(market, asin): {'market', 'asin', 'title', 'brand',
    'image', 'price', 'category', 'ev': {key: evidence}}} (engine.gaps.selling_well shapes)."""
    from engine import curve as curvemod
    items = {}
    q = ','.join('?' * len(PROOF))

    def item(mk, asin, **kw):
        it = items.setdefault((mk, asin), {'market': mk, 'asin': asin, 'title': None, 'brand': None, 'image': None,
                                           'price': None, 'category': None, 'ev': {}})
        for k, v in kw.items():
            if v is not None:
                it[k] = v                                                    # rows come oldest first: the newest wins
        return it
    for r in conn.execute(
            'SELECT s.market, s.kind, s.slug, s.category, i.asin, i.pos, i.pct, i.rank_now, i.rank_before, i.price, a.title, '
            'a.brand, a.image FROM list_snapshot s JOIN list_item i ON i.snapshot_id=s.id LEFT JOIN asin a ON a.market=s.market '
            "AND a.asin=i.asin WHERE s.market IN (%s) AND s.kind IN ('best','movers') AND s.ts>=? ORDER BY s.ts" % q,
            (*PROOF, now - PROOF_DAYS * 86400)):
        it = item(r['market'], r['asin'], title=r['title'], brand=r['brand'], image=r['image'], price=r['price'],
                  category=r['category'])
        it['ev'][(r['kind'], r['slug'])] = {'kind': r['kind'], 'rank': r['pos'], 'pct': r['pct'], 'rank_now': r['rank_now'],
                                            'rank_before': r['rank_before'], 'category': r['category'], 'slug': r['slug']}
    since = now - EVIDENCE_DAYS * 86400
    for r in conn.execute(
            'SELECT p.market, p.asin, MAX(p.badge_low) AS low, a.title, a.brand, a.image FROM product_snapshot p '
            'LEFT JOIN asin a ON a.market=p.market AND a.asin=p.asin WHERE p.market IN (%s) AND p.ts>=? '
            'AND p.badge_low IS NOT NULL GROUP BY p.market, p.asin' % q, (*PROOF, since)):
        item(r['market'], r['asin'], title=r['title'], brand=r['brand'], image=r['image'])['ev'][('badge',)] = \
            {'kind': 'badge', 'low': r['low']}
    for r in conn.execute(
            'SELECT f.market, r.asin, MAX(r.sales_asin) AS units, MAX(r.title) AS title, MAX(r.brand) AS brand, '
            'MAX(r.price) AS price, MAX(r.category) AS category FROM h10_row r JOIN import_file f ON f.id=r.import_id '
            'WHERE f.market IN (%s) AND f.as_of>=? AND r.asin IS NOT NULL AND r.sales_asin>0 GROUP BY f.market, r.asin' % q,
            (*PROOF, since)):
        item(r['market'], r['asin'], title=r['title'], brand=r['brand'], price=r['price'],
             category=r['category'])['ev'][('h10',)] = {'kind': 'h10', 'units': r['units']}
    cv = storage.get_curve(conn, 'US')
    if cv:
        for (mk, _), it in items.items():
            ranks = [(e['rank'], e['slug']) for e in it['ev'].values() if mk == 'US' and e['kind'] == 'best' and e.get('rank')]
            ranks += [(e['rank_now'], e['slug']) for e in it['ev'].values()
                      if mk == 'US' and e['kind'] == 'movers' and e.get('rank_now')]
            if ranks:
                rank, slug = min(ranks)
                u = curvemod.predict(cv, slug, rank)
                if u and u.get('v') is not None:
                    it['ev'][('curve',)] = {'kind': 'curve', 'units': u}
    return items


def proven(conn, settings, now=None):
    """Products selling well in at least two proof markets, strongest first: [{'cluster', 'asins', 'title', 'brand',
    'image', 'category', 'best', 'items', 'proof'}]."""
    now = now or time.time()
    items = evidence(conn, now)
    groups = gaps.group([{**it, 'ev': list(it['ev'].values())} for it in items.values() if it.get('title')])
    m = mins(settings)
    out = []
    for g in groups:
        by = {}
        for it in g['items']:
            by.setdefault(it['market'], []).extend(it['ev'])
        pr = gaps.proof(by, m)
        if not pr['proven']:
            continue
        best = [e['rank'] for it in g['items'] for e in it['ev'] if e['kind'] == 'best' and e.get('rank')]
        out.append({'cluster': g['key'], 'asins': list(dict.fromkeys(it['asin'] for it in g['items'])),
                    'title': g['items'][0]['title'], 'brand': next((it['brand'] for it in g['items'] if it.get('brand')), None),
                    'image': next((it['image'] for it in g['items'] if it.get('image')), None),
                    'category': next((it['category'] for it in g['items'] if it.get('category')), None),
                    'best': min(best) if best else None, 'proof': pr,
                    'items': [{'market': it['market'], 'asin': it['asin'], 'title': it['title'], 'price': it['price']}
                              for it in g['items']]})
    out.sort(key=lambda c: (-c['proof']['n'], c['best'] or 999))
    return out


def due(conn, cands, market, now, n, strong=None):
    """The proven products to check next in a market: never finished there first, then the oldest finished checks
    (after RECHECK_DAYS). A check that was blocked or only half read is tried again on the next run. Within each,
    products in a strong Market Gaps niche of that market (strong: {niche_key: score}) come first."""
    from engine import marketgaps as mgx
    last = {r[0]: r[1] for r in conn.execute(
        "SELECT cluster, MAX(ts) FROM gap_check WHERE market=? AND status='ok' GROUP BY cluster", (market,))}
    todo = [c for c in cands if now - last.get(c['cluster'], 0) > RECHECK_DAYS * 86400]
    boost = {c['cluster']: mgx.niche_boost(c.get('title'), strong) for c in todo} if strong else {}
    todo.sort(key=lambda c: (c['cluster'] in last, last.get(c['cluster'], 0) if c['cluster'] in last else 0,
                             -boost.get(c['cluster'], 0)))           # stable: strongest first within
    return todo[:n]


# ---------- 3. one product in one target market ----------
def amazon_ids(S, market):
    """Amazon's own seller ids in a market, learned from product pages sold by Amazon (never hard-coded)."""
    return (S.db.read(storage.get_state, 'amazon_ids', {}) or {}).get(market, [])


def learn_amazon(S, market, p):
    mid = p.get('merchant_id')
    if p.get('fulfillment') != 'amazon' or not mid:
        return

    def fn(conn):
        ids = storage.get_state(conn, 'amazon_ids', {}) or {}
        if mid not in ids.get(market, []):
            ids[market] = (ids.get(market, []) + [mid])[-5:]
            storage.set_state(conn, 'amazon_ids', ids)
    S.db.write(fn)


async def read_listing(S, mk, asin, client=None, ctx=None, fresh=PAGE_FRESH):
    """Who offers one ASIN in a market: {'status': 'ok' | 'missing' | a blocked status | 'paused' | 'budget',
    'source': 'spapi' | 'page', 'available', 'n_offers', 'amazon', 'offers', 'page' (the product page used),
    'catalog' (SP-API catalog facts)}. The SP-API answers when its keys cover the market; else the product page
    (one read in the last `fresh` seconds is reused). A block or pause is kept in ctx['stop']."""
    ctx = ctx if ctx is not None else {}
    if client is not None and client.has(mk):
        try:
            summ = spapi.catalog_summary(await asyncio.to_thread(client.catalog_item, mk, asin), mk)
            if not summ['listed']:
                return {'status': 'missing', 'source': 'spapi', 'offers': [], 'n_offers': 0, 'available': False,
                        'catalog': summ}
            payload = await asyncio.to_thread(client.item_offers, mk, asin)
            offers, summary = offs.from_spapi(payload, mk, amazon_ids(S, mk))
            await S.db.awrite(storage.save_offers, mk, asin, offers, summary, 'spapi')
            return {'status': 'ok', 'source': 'spapi', 'offers': offers, 'catalog': summ, **summary}
        except spapi.SpApiError as e:
            log.warning('SP-API %s %s: %s', mk, asin, e)                # the product page answers instead
    if ctx.get('stop'):
        return {'status': _why(ctx['stop']), 'source': 'page', 'offers': []}
    p = S.db.read(storage.latest_page, mk, asin, time.time() - fresh) if fresh else None
    read_now = not p or p['status'] not in ('ok', 'partial', 'missing')
    if read_now:
        try:
            p = await product.read_product(S.checker, asin, mk)
        except (ac.BlockedError, throttle.ThrottleError) as e:
            ctx['stop'] = e
            return {'status': _why(e), 'source': 'page', 'offers': []}
        await S.db.awrite(storage.save_product, mk, asin, p)
    if p['status'] == 'missing':
        return {'status': 'missing', 'source': 'page', 'offers': [], 'n_offers': 0, 'available': False, 'page': p}
    if p['status'] not in ('ok', 'partial'):
        return {'status': p['status'], 'source': 'page', 'offers': [], 'page': p}
    learn_amazon(S, mk, p)
    sid = p.get('seller_id') or p.get('merchant_id')
    o = offs.from_page(p, mk, S.db.read(storage.sellers_of, mk, [sid]).get(sid) if sid else None)
    offers = [o] if o else []
    summary = {'n_offers': max(p.get('offers') or 0, len(offers)), 'amazon': bool(o and o['amazon']), 'n_fba': None,
               'n_fbm': None, 'available': p.get('available') if p.get('available') is not None else bool(offers)}
    if read_now:
        await S.db.awrite(storage.save_offers, mk, asin, offers, summary, 'page')
    return {'status': 'ok', 'source': 'page', 'offers': offers, 'page': p, **summary}


async def fill_sellers(S, mk, offers, ctx, limit=SELLER_PAGES):
    """Where third-party sellers are based: known profiles (a month old at most), then up to `limit` profile pages."""
    ids = list(dict.fromkeys(o['seller_id'] for o in offers if o.get('seller_id') and not o.get('amazon')
                             and not o.get('country')))
    known = S.db.read(storage.sellers_of, mk, ids) if ids else {}
    read = 0
    for sid in ids:
        row = known.get(sid)
        if not (row and row.get('country') and time.time() - (row.get('fetched_at') or 0) < reports.SELLER_MAX_AGE):
            if read >= limit or ctx.get('stop'):
                continue
            try:
                s = await product.read_seller(S.checker, sid, mk)
            except (ac.BlockedError, throttle.ThrottleError) as e:
                ctx['stop'] = e
                continue
            except Exception as e:                                   # noqa: BLE001 - one profile never ends a check
                log.warning('seller %s %s failed: %s', mk, sid, e)
                continue
            read += 1
            await S.db.awrite(storage.save_seller, mk, sid, s)
            row = {**(row or {}), **{k: v for k, v in s.items() if v is not None}}
        for o in offers:
            if o.get('seller_id') == sid:
                o['country'] = o.get('country') or (row or {}).get('country')
                o['seller'] = o.get('seller') or (row or {}).get('name')
    return read


async def proof_page(S, cand):
    """The product page in the market that proved it (brand and bullet points for the phrase and the warnings): a
    reading of the last two weeks, else one read now. None when it cannot be read (that never stops the check)."""
    it = cand['items'][0]
    p = S.db.read(storage.latest_page, it['market'], it['asin'], time.time() - reports.LISTING_MAX_AGE)
    if p and p['status'] in ('ok', 'partial'):
        return p
    try:
        p = await product.read_product(S.checker, it['asin'], it['market'])
    except (ac.BlockedError, throttle.ThrottleError) as e:
        log.info('proof page %s %s not read: %s', it['market'], it['asin'], _first_line(e))
        return None
    await S.db.awrite(storage.save_product, it['market'], it['asin'], p)
    return p if p['status'] in ('ok', 'partial') else None


async def find_phrase(S, mk, title, brand):
    """(phrase, how): what shoppers type for it in this market (Amazon's suggestions there, never with the brand)."""
    words = fr.search_words(title or '', brand or '')
    prefixes = discovery.phrase_prefixes(words) if words else []
    have = S.db.read(storage.suggestions, mk, prefixes, time.time() - reports.QUALIFY_FRESH['phrase'] * 86400)
    for p in prefixes:
        if p in have:
            continue
        try:
            got = await ideas.complete(S.checker, mk, p)
        except throttle.ThrottleError:
            break
        except Exception as e:                                       # noqa: BLE001 - one failed call skips one prefix
            log.warning('suggestions %s "%s" failed: %s', mk, p, e)
            continue
        kws = [{'keyword': k, 'hits': 1, 'best': n} for n, k in enumerate(got, 1) if k]
        await S.db.awrite(storage.save_suggest, mk, p, kws, 1, 0, 'ok' if kws else 'empty')
    known = S.db.read(storage.suggestions, mk, prefixes)
    return gaps.generic_phrase(title, brand, [k for p in prefixes for k in known.get(p, {}).get('keywords', [])])


async def search_phrase(S, mk, phrase, ctx):
    """Page 1 of a phrase's search in a market (a search of the last few hours is reused):
    {'status', 'rows', 'total', 'local', 'overseas', 'bought_total', 'price_med', 'location_ok', 'snapshot_id'}."""
    s = S.cached(mk, phrase, 1)
    if not s:
        if ctx.get('stop'):
            return {'status': _why(ctx['stop']), 'rows': []}
        fast, local, _ = S.days()
        try:
            s = await S.checker.check(phrase, mk, fast, 1, None, local)
        except ac.BlockedError as e:
            ctx['stop'] = e
            await S.save_check({'market': mk, 'keyword': phrase, 'status': e.status, 'error': str(e)[:200]}, None, 1, 'gaps')
            return {'status': _why(e), 'rows': []}
        except throttle.ThrottleError as e:
            ctx['stop'] = e
            return {'status': _why(e), 'rows': []}
        await S.save_check(s, None, 1, 'gaps')
    return {'status': storage.serp_status(s), 'rows': s.get('rows') or [],
            **{k: s.get(k) for k in ('total', 'local', 'overseas', 'bought_total', 'price_med', 'location_ok', 'snapshot_id')}}


def _page_facts(p):
    p = p or {}
    dims = p.get('pkg_cm') or [p.get('pkg_l_cm'), p.get('pkg_w_cm'), p.get('pkg_h_cm')]
    return {'price': p.get('price'), 'rating': p.get('rating'), 'reviews': p.get('reviews'),
            'days': p.get('delivery_days'), 'dims_cm': dims if all(dims) else None, 'kg': p.get('pkg_kg'),
            'category': p.get('root_name')}


async def check_product(S, cand, mk, client=None, ctx=None, say=None):
    """Check one proven product in Australia or the UAE and keep the check. Returns its data (see reports.gaps_table)."""
    ctx = ctx if ctx is not None else {}
    say = say or (lambda m: None)
    name = gaps.MARKET_NAMES[mk]
    listing, asin = {'status': 'not read', 'offers': []}, cand['asins'][0]
    for a in cand['asins'][:2]:                                  # 1. the same ASIN there (a second ASIN of it if missing)
        say('Amazon %s: is %s sold there?' % (name, a))
        listing, asin = await read_listing(S, mk, a, client, ctx), a
        if listing['status'] != 'missing':
            break
    page = listing.get('page') if (listing.get('page') or {}).get('status') in ('ok', 'partial') else None
    info = page or await proof_page(S, cand)                      # 2. brand, bullet points, safety lines
    brand = (listing.get('catalog') or {}).get('brand') or (info or {}).get('brand') or cand.get('brand')
    title = cand.get('title') or (info or {}).get('title') or ''
    say('Amazon %s: what shoppers type for "%s"' % (name, title[:50]))
    phrase, how = await find_phrase(S, mk, title, brand)          # 3. the phrase there, and page 1 of its search
    search = await search_phrase(S, mk, phrase, ctx) if phrase else {'status': 'no phrase', 'rows': []}
    search['phrase'] = phrase
    rows = [r for r in search['rows'] if not r.get('sponsored')]
    own = set(cand['asins'])
    same = [r for r in rows if r.get('asin') not in own and gaps.same_product(phrase, r.get('title'))]
    lonely = listing['status'] in ('ok', 'missing') and len(listing.get('offers') or []) <= 1 \
        and (listing.get('n_offers') or 0) <= 1
    if lonely:                                                    # 4. who sells the other listings of it
        for r in same[:SAME_PAGES]:
            got = await read_listing(S, mk, r['asin'], client, ctx)
            if got['status'] == 'ok' and got['offers']:
                r['offer'] = next((o for o in got['offers'] if o.get('buybox')), got['offers'][0])
    everyone = list(listing.get('offers') or []) + [r['offer'] for r in same if r.get('offer')]
    if len(everyone) <= FEW_SELLERS:                              # 5. where those sellers are based
        await fill_sellers(S, mk, everyone, ctx)
    gap = gaps.classify(mk, listing, search, cand['asins'])
    flags = gaps.flags(title, (info or {}).get('bullets') or '', (info or {}).get('item_form'),
                       (info or {}).get('safety') or (), (listing.get('catalog') or {}).get('dg'))
    lst_known = listing['status'] in ('ok', 'missing')
    srch_known = search['status'] in ('ok', 'empty', 'partial')
    data = {'title': title, 'brand': brand, 'image': cand.get('image'), 'category': cand.get('category'),
            'asins': cand['asins'], 'asin': asin, 'items': cand['items'], 'proof': cand['proof'], 'phrase': phrase,
            'phrase_how': how, 'gap': gap, 'flags': flags,
            'listing': {'status': listing['status'], 'source': listing.get('source'), 'available': listing.get('available'),
                        'n_offers': listing.get('n_offers'), 'offers': listing.get('offers') or [],
                        **_page_facts(page), 'price': (page or {}).get('price') or listing.get('price')},
            'search': {k: search.get(k) for k in ('status', 'total', 'local', 'overseas', 'bought_total', 'price_med',
                                                  'location_ok', 'snapshot_id')},
            'same': [{k: r.get(k) for k in ('asin', 'title', 'origin', 'days', 'price', 'offer')} for r in same[:SAME_SHOWN]],
            'stopped': _first_line(ctx['stop']) if ctx.get('stop') else None}
    status = 'ok' if lst_known and srch_known else 'partial' if lst_known or srch_known else 'unread'
    await S.db.awrite(storage.save_gap_check, mk, asin, cand['cluster'], phrase, status, gap['type'], data)
    return data


async def check_market(S, mk, n=CHECK_TOP, progress=None, clusters=None):
    """Check the next proven products in one target market (or the clusters you picked). A block ends the run after
    keeping what was read; in the automatic refresh it is raised, so the scheduler pauses that marketplace."""
    say = progress or (lambda m: None)
    name = gaps.MARKET_NAMES[mk]
    now = time.time()
    cands = await asyncio.to_thread(S.db.read, proven, S.settings, now)
    if clusters is not None:
        todo = [c for c in cands if c['cluster'] in clusters][:n]
    else:
        import marketgaps
        strong = await asyncio.to_thread(marketgaps.strong_niches, S.db, mk)
        todo = await asyncio.to_thread(S.db.read, due, cands, mk, now, n, strong)
    if not todo:
        return {'status': 'ok', 'counts': {}, 'note': 'Amazon %s: nothing new to check (%d proven product%s)' % (
            name, len(cands), '' if len(cands) == 1 else 's')}
    client = getattr(S, 'spapi', None)
    ctx, counts = {}, {}
    for i, c in enumerate(todo, 1):
        if ctx.get('stop'):
            break
        say('Amazon %s: product %d of %d · %s' % (name, i, len(todo), (c['title'] or c['cluster'])[:60]))
        d = await check_product(S, c, mk, client, ctx, say)
        counts[d['gap']['type']] = counts.get(d['gap']['type'], 0) + 1
    found = sum(v for k, v in counts.items() if k in gaps.GAPS)
    note = 'Amazon %s: %d of %d proven product%s checked, %d gap%s' % (
        name, sum(counts.values()), len(todo), '' if len(todo) == 1 else 's', found, '' if found == 1 else 's')
    if ctx.get('stop'):
        note += '; stopped: %s' % _first_line(ctx['stop'])
        if isinstance(ctx['stop'], ac.BlockedError) and ac.BACKGROUND.get():
            raise ctx['stop']
    return {'status': 'partial' if ctx.get('stop') else 'ok', 'note': note, 'counts': counts}


async def run(S, progress, markets=None, proof=True, n=USER_TOP, force=False):
    """Your own Gap Finder run: the proof markets' Best Sellers (unless read in the last 20 hours), then the next
    proven products in each target market. Returns {'notes'}."""
    notes = []
    if proof:
        for m in PROOF:
            if not force and S.db.read(lists_read_since, m, time.time() - LISTS_FRESH):
                continue
            try:
                notes.append((await read_proof_lists(S, m, progress))['note'])
            except (ac.BlockedError, throttle.ThrottleError) as e:
                notes.append('Amazon %s: %s' % (gaps.MARKET_NAMES[m], _first_line(e)))
    for mk in markets or config.TARGET_MARKETS:
        notes.append((await check_market(S, mk, n, progress))['note'])
    return {'notes': notes}


# ---------- 4. the lone exporters you watch ----------
def add_new_alerts(conn, alerts):
    """Keep the alerts not seen before; returns them."""
    keys = [a['dedupe_key'] for a in alerts]
    have = {r[0] for r in conn.execute('SELECT dedupe_key FROM alert WHERE dedupe_key IN (%s)' % ','.join('?' * len(keys)),
                                       keys)} if keys else set()
    fresh = [a for a in alerts if a['dedupe_key'] not in have]
    storage.add_alerts(conn, fresh)
    return fresh


async def recheck_lone(S, mk, progress=None):
    """Read the watched lone exporters' listings in one market again; a second seller raises an alert (Home, and a
    Windows notification when the app is not in front)."""
    import analysis
    say = progress or (lambda m: None)
    ws = [w for w in S.db.read(storage.watches) if w['kind'] == 'lone' and w['market'] == mk]
    client = getattr(S, 'spapi', None)
    ctx, alerts, read = {}, [], 0
    for w in ws:
        if ctx.get('stop'):
            break
        say('Amazon %s: who sells %s now?' % (gaps.MARKET_NAMES[mk], w['target']))
        before = S.db.read(storage.latest_offers, w['market'], w['target'])
        now = await read_listing(S, mk, w['target'], client, ctx, fresh=0)
        if now['status'] != 'ok':
            continue
        read += 1
        a = gaps.second_seller(mk, w['target'], w.get('display'), before, now, time.time())
        if a:
            alerts.append(a)
    fresh = S.db.write(add_new_alerts, alerts) if alerts else []
    if fresh:
        S.say('%d lone exporter%s got company: see Home.' % (len(fresh), '' if len(fresh) == 1 else 's'))
        await asyncio.to_thread(analysis.notify_new, S, fresh)
    note = '%d of %d watched listing%s read, %d with a new seller' % (read, len(ws), '' if len(ws) == 1 else 's', len(fresh))
    if ctx.get('stop'):
        note += '; stopped: %s' % _first_line(ctx['stop'])
        if isinstance(ctx['stop'], ac.BlockedError) and ac.BACKGROUND.get():
            raise ctx['stop']
    return {'status': 'partial' if ctx.get('stop') else 'ok', 'note': note}
