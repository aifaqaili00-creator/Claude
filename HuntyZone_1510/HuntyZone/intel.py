"""Market intelligence: the database reads behind the Market intelligence page (the rules are in engine/intel.py and
engine/ads.py). Everything is built from what HuntyZone already stored; nothing here reads Amazon."""
import json
import time

import config
import reports
import storage
from engine import ads as admod
from engine import curve as curvemod
from engine import intel as en

SINCE_DAYS = 90               # product pages and offers this recent are used
NICHE_DAYS = 60               # keywords searched this recently are niches
MAX_NICHES = 200


def _curve_fn(conn, market):
    cv = storage.get_curve(conn, market)
    return (lambda node, rank: curvemod.predict(cv, node, rank)) if cv else None


def newest_pages(conn, market, since, asins=None):
    """The newest good product-page reading of each listing since a time (of these ASINs, when given)."""
    sql = ("SELECT MAX(id) AS id FROM product_snapshot WHERE market=? AND ts>=? AND status IN ('ok','partial')")
    params = [market, since]
    if asins is not None:
        if not asins:
            return []
        sql += ' AND asin IN (%s)' % ','.join('?' * len(asins))
        params += list(asins)
    rows = storage._rows(conn.execute(
        'SELECT p.asin, p.ts, p.seller_id, p.merchant_id, p.buybox_seller, p.fulfillment, p.badge_low, p.root_rank, '
        'p.root_node, p.available, p.price, p.rating, p.reviews, p.listing_json, a.brand, a.title, a.image '
        'FROM product_snapshot p JOIN (%s GROUP BY asin) n ON n.id=p.id '
        'LEFT JOIN asin a ON a.market=p.market AND a.asin=p.asin' % sql, params))
    for r in rows:
        r['listing'] = json.loads(r.pop('listing_json') or 'null')
    return rows


def _niches(conn, market, now):
    return [r[0] for r in conn.execute(
        "SELECT kw_norm FROM serp_snapshot WHERE market=? AND ts>=? AND status IN ('ok','partial') GROUP BY kw_norm "
        'ORDER BY MAX(ts) DESC LIMIT ?', (market, now - NICHE_DAYS * 86400, MAX_NICHES))]


def _display(conn, kws):
    if not kws:
        return {}
    return {r[0]: r[1] or r[0] for r in conn.execute('SELECT kw_norm, display FROM keyword WHERE kw_norm IN (%s)'
                                                     % ','.join('?' * len(kws)), list(kws))}


def _brands(conn, market, asins):
    """{asin: brand} known from listings (product pages, Helium 10)."""
    asins = [a for a in asins if a]
    if not asins:
        return {}
    q = ','.join('?' * len(asins))
    out = {r[0]: r[1] for r in conn.execute('SELECT r.asin, r.brand FROM h10_row r JOIN import_file f ON f.id=r.import_id '
                                            'WHERE f.market=? AND r.asin IN (%s) AND r.brand IS NOT NULL' % q, (market, *asins))}
    out.update({r[0]: r[1] for r in conn.execute('SELECT asin, brand FROM asin WHERE market=? AND asin IN (%s) AND brand IS NOT NULL'
                                                 % q, (market, *asins))})
    return out


def _names(conn, market):
    """Every brand name known in a market, longest first (titles are matched against them)."""
    return admod.brand_names([r[0] for r in conn.execute(
        'SELECT DISTINCT brand FROM asin WHERE market=? AND brand IS NOT NULL UNION SELECT DISTINCT r.brand FROM h10_row r '
        'JOIN import_file f ON f.id=r.import_id WHERE f.market=? AND r.brand IS NOT NULL', (market, market))])


# ---------- 1. Seller Explorer (and 4. where they are based) ----------
def sellers(db, market, now=None):
    now = now or time.time()

    def build(conn):
        since = now - SINCE_DAYS * 86400
        pages = newest_pages(conn, market, since)
        offers = [{'asin': r[0], 'offers': json.loads(r[1] or '[]')} for r in conn.execute(
            "SELECT o.asin, o.offers_json FROM offer_snapshot o JOIN (SELECT MAX(id) AS id FROM offer_snapshot WHERE market=? "
            "AND ts>=? AND status='ok' GROUP BY asin) n ON n.id=o.id", (market, since))]
        ids = {p['seller_id'] or p['merchant_id'] for p in pages} | {x.get('seller_id') for o in offers for x in o['offers']}
        known = storage.sellers_of(conn, market, [i for i in ids if i])
        first = {r[0]: r[1] for r in conn.execute('SELECT seller_id, MIN(ts) FROM product_snapshot WHERE market=? '
                                                  'AND seller_id IS NOT NULL GROUP BY seller_id', (market,))}
        rows = en.sellers_table(market, pages, known, offers, first, _curve_fn(conn, market))
        return {'market': market, 'sellers': rows, 'origins': en.origins(rows), 'pages': len(pages),
                'profiles': sum(1 for r in rows if r['country'])}
    return db.read(build)


def origins(db, market, kw=None, now=None):
    """Where a niche's sellers are based (its best page-1 listings), or every seller read in the market."""
    if not kw:
        s = sellers(db, market, now)
        return {'market': market, 'kw': None, 'countries': s['origins'], 'sellers': len(s['sellers'])}
    w = reports.who_sells(db, market, kw, now)
    return {'market': market, 'kw': config.norm_kw(kw), 'searched': w.get('searched'), 'countries': w.get('countries') or [],
            'listings': w.get('listings') or [], 'known': w.get('known')}


# ---------- 2. Brand Explorer ----------
def _niche_brands(conn, market, kw, now, names=None):
    snaps = storage.serp_history(conn, market, kw, now - 120 * 86400, valid_only=True)
    if not snaps:
        return {'market': market, 'kw': kw, 'searched': False, 'brands': []}
    last = snaps[-1]
    lo, hi = en.THRESH['growth_days']
    old = [s for s in snaps if last['ts'] - hi * 86400 <= s['ts'] <= last['ts'] - lo * 86400]
    rows = storage.snapshot_rows(conn, last['id'], market)
    before = storage.snapshot_rows(conn, old[-1]['id'], market) if old else None
    asins = list({r['asin'] for r in rows + (before or [])})
    amazon = {p['asin'] for p in newest_pages(conn, market, now - SINCE_DAYS * 86400, asins) if p['fulfillment'] == 'amazon'}
    out = en.brand_shares(rows, market, _brands(conn, market, asins), amazon, before,
                          _names(conn, market) if names is None else names, kw)
    return {**out, 'market': market, 'kw': kw, 'searched': True, 'checked_at': last['ts'],
            'compared_with': old[-1]['ts'] if old else None}


def brands(db, market, kw=None, now=None):
    """One niche's brands (share of page-1 sales, growth, Amazon), or every brand across the niches searched."""
    now = now or time.time()

    def build(conn):
        if kw:
            out = _niche_brands(conn, market, config.norm_kw(kw), now)
            out['display'] = _display(conn, [out['kw']]).get(out['kw'], out['kw'])
            return out
        kws = _niches(conn, market, now)
        names = _names(conn, market)
        agg = {}
        for k in kws:
            for b in _niche_brands(conn, market, k, now, names)['brands']:
                a = agg.setdefault(b['brand'].lower(), {'brand': b['brand'], 'src': b['src'], 'niches': [], 'units': 0.0,
                                                        'listings': 0, 'amazon': False})
                a['src'] = 'listing' if b['src'] == 'listing' else a['src']
                a['niches'].append({'kw': k, 'share': b['share']})
                a['units'] += (b['units'] or {}).get('v') or 0
                a['listings'] += b['listings']
                a['amazon'] = a['amazon'] or b['amazon']
        names = _display(conn, kws)
        rows = sorted(agg.values(), key=lambda a: (-len(a['niches']), -a['units'], a['brand'].lower()))[:300]
        for a in rows:
            a['niches'].sort(key=lambda n: -(n['share'] or 0))
            a['n_niches'] = len(a['niches'])
            a['niches'] = [{**n, 'display': names.get(n['kw'], n['kw'])} for n in a['niches'][:6]]
            a['units'] = round(a['units']) or None
        return {'market': market, 'kw': None, 'brands': rows, 'niches': len(kws),
                'keywords': [{'kw': k, 'display': names.get(k, k)} for k in kws]}
    return db.read(build)


# ---------- 3. Category gap explorer ----------
def categories(db, now=None):
    now = now or time.time()
    return db.read(lambda conn: {'rows': en.category_gaps(storage.gap_checks(conn, now - reports.GAP_SHOW_DAYS * 86400))})


# ---------- 5. Related products ----------
def related(db, market, asin=None):
    """A product's network from the carousels on product pages; without an ASIN, the products whose carousels were
    read (newest first)."""
    def build(conn):
        if not asin:
            rows = storage._rows(conn.execute(
                'SELECT r.asin, COUNT(*) AS links, MAX(r.ts) AS ts, a.title, a.image FROM related r LEFT JOIN asin a '
                'ON a.market=r.market AND a.asin=r.asin WHERE r.market=? GROUP BY r.asin ORDER BY MAX(r.ts) DESC LIMIT 200',
                (market,)))
            return {'market': market, 'asin': None, 'read': rows}
        links = storage.related_links(conn, market, [asin])
        ids = list({l['other'] for l in links} | {l['asin'] for l in links} | {asin})
        info = {r['asin']: r for r in storage._rows(conn.execute(
            'SELECT asin, title, brand, image FROM asin WHERE market=? AND asin IN (%s)' % ','.join('?' * len(ids)), (market, *ids)))}
        me = info.get(asin) or {}
        g = en.related_graph(asin, me.get('title'), links, {a: i['title'] for a, i in info.items()},
                             {**{a: i['brand'] for a, i in info.items() if i['brand']}, **_brands(conn, market, ids)})
        for n in g['nodes']:
            n['image'] = (info.get(n['asin']) or {}).get('image')
        read = conn.execute("SELECT MAX(ts) FROM product_snapshot WHERE market=? AND asin=? AND status IN ('ok','partial')",
                            (market, asin)).fetchone()[0]
        return {'market': market, 'asin': asin, 'title': me.get('title'), 'image': me.get('image'), 'brand': me.get('brand'),
                'page_read_at': read, **g}
    return db.read(build)


# ---------- 6. Ad watch ----------
def ads(db, market, kw=None, now=None):
    """The ad pressure of one keyword (with the brands buying its places), or of every niche searched."""
    now = now or time.time()

    def one(conn, k, names):
        s = storage.latest_serp(conn, market, k)
        if not s:
            return None
        rows = s['rows']
        return {'kw': k, 'checked_at': s['checked_at'],
                **admod.ad_pressure(rows, s.get('ads'), _brands(conn, market, [r['asin'] for r in rows]), names, k)}

    def build(conn):
        names = _names(conn, market)
        if kw:
            k = config.norm_kw(kw)
            out = one(conn, k, names) or {'kw': k, 'score': None, 'label': 'Not read', 'brands': [], 'parts': [],
                                          'why': 'not searched yet'}
            return {**out, 'market': market, 'display': _display(conn, [k]).get(k, k)}
        kws = _niches(conn, market, now)
        names_kw = _display(conn, kws)
        rows = [r for r in (one(conn, k, names) for k in kws) if r]
        for r in rows:
            r['display'] = names_kw.get(r['kw'], r['kw'])
            r['brands'] = r['brands'][:5]
        rows.sort(key=lambda r: -(r['score'] if r['score'] is not None else -1))
        return {'market': market, 'kw': None, 'rows': rows}
    return db.read(build)


# ---------- 8. Listing scorecard ----------
def _facts(p):
    if not p:
        return None
    li = p.get('listing') or {}
    return {'title_len': li.get('title_len') or (len(p['title']) if p.get('title') else None), 'bullets': li.get('bullets'),
            'images': li.get('images'), 'video': li.get('video'), 'aplus': li.get('aplus'), 'rating': p.get('rating'),
            'reviews': p.get('reviews'), 'price': p.get('price')}


def scorecard(db, market, asin, kw=None, now=None):
    """Your listing against the top 10 of its keyword (the keyword you give, else its board card's, else your phrase
    for it). Lists the top-10 pages still to read, so the page can offer to read them."""
    now = now or time.time()

    def build(conn):
        k = config.norm_kw(kw or '')
        if not k:
            r = conn.execute('SELECT kw FROM board_card WHERE market=? AND asin=? AND archived_at IS NULL AND kw IS NOT NULL '
                             'ORDER BY updated_at DESC LIMIT 1', (market, asin)).fetchone()
            k = config.norm_kw(r[0]) if r and r[0] else ''
        if not k:
            r = conn.execute('SELECT phrase FROM product_pref WHERE market=? AND asin=?', (market, asin)).fetchone()
            k = config.norm_kw(r[0]) if r and r[0] else ''
        mine = (newest_pages(conn, market, 0, [asin]) or [None])[0]
        s = storage.latest_serp(conn, market, k) if k else None
        top = []
        for r in (s or {}).get('rows') or []:
            if not r.get('sponsored') and r['asin'] != asin and r['asin'] not in top:
                top.append(r['asin'])
        top = top[:reports.TOP_LISTINGS]
        pages = {p['asin']: p for p in newest_pages(conn, market, now - reports.LISTING_MAX_AGE * 4, top)}
        out = en.scorecard(_facts(mine), [_facts(pages.get(a)) for a in top])
        out.update(market=market, asin=asin, kw=k or None, searched=bool(s), title=(mine or {}).get('title'),
                   image=(mine or {}).get('image'), page_read_at=(mine or {}).get('ts'),
                   todo=[a for a in top if not (pages.get(a) or {}).get('listing')],
                   mine_todo=not (mine or {}).get('listing'))
        return out
    return db.read(build)
