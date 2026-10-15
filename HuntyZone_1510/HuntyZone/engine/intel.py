"""Market intelligence (SmartScout-style views for Australia and the UAE) from what HuntyZone already reads: product
pages (buy-box seller, brand, badge, rank), seller profiles, search pages, Gap Finder checks and the product-page
carousels. Every estimate keeps its range and source; what was not read stays unknown, never zero.
Pure functions; JSON-safe output.
"""
import statistics

import config
import file_rank as fr

from . import badge, discovery

THRESH = {
    'alike': 0.3,              # title words this alike (Jaccard) make a carousel product a competitor
    'growth_days': (21, 45),   # a niche's growth compares page 1 with a search 21 to 45 days older
    'behind': 0.7,             # the scorecard calls you behind below 70% of the top 10's median
    'ahead': 1.3,              # ... and ahead above 130%
    'rating_step': 0.2,        # stars either side of the median that count as behind / ahead
}
AMAZON = 'AMAZON'              # Amazon itself, as one seller row


def _n(x):
    return '{:,}'.format(int(round(x)))


# ---------- units ----------
def units_of(p, market, curve_fn=None):
    """Monthly units of one listing: its "bought in past month" badge (Amazon's floor, as a range), else its sales
    rank through the market's curve (curve_fn(node, rank) -> est). {'v', 'lo', 'hi', 'src'} or None."""
    p = p or {}
    if p.get('badge_low'):
        e = badge.badge_estimate(p['badge_low'], market)
        if e and e.get('v') is not None:
            return {'v': e['v'], 'lo': e.get('lo'), 'hi': e.get('hi'), 'src': 'badge'}
    if curve_fn and p.get('root_rank'):
        e = curve_fn(p.get('root_node'), p['root_rank'])
        if e and e.get('v') is not None:
            return {'v': e['v'], 'lo': e.get('lo'), 'hi': e.get('hi'), 'src': 'sales-rank curve'}
    return None


def add_units(total, u):
    """Sum two estimates (ranges add up); sources are kept as a list."""
    if not u:
        return total
    lo, hi = u.get('lo') if u.get('lo') is not None else u['v'], u.get('hi') if u.get('hi') is not None else u['v']
    if not total:
        return {'v': u['v'], 'lo': lo, 'hi': hi, 'src': [u['src']]}
    return {'v': total['v'] + u['v'], 'lo': total['lo'] + lo, 'hi': total['hi'] + hi,
            'src': total['src'] + ([u['src']] if u['src'] not in total['src'] else [])}


def _round(u):
    return {k: (round(v) if isinstance(v, (int, float)) else v) for k, v in u.items()} if u else None


# ---------- 1. Seller Explorer ----------
def sellers_table(market, pages, sellers=None, offers=None, first_seen=None, curve_fn=None):
    """Every seller read in a market. pages: the newest product-page reading per ASIN [{'asin', 'seller_id',
    'merchant_id', 'buybox_seller', 'fulfillment', 'brand', 'badge_low', 'root_rank', 'root_node', 'ts'}]; sellers:
    {seller_id: profile row}; offers: the newest offer reading per ASIN [{'asin', 'offers'}] (SP-API: every seller);
    first_seen: {seller_id: ts}. A listing's units go to its buy-box seller. Amazon itself is one row.
    -> [{'seller_id', 'name', 'country', 'amazon', 'rating_pct', 'ratings', 'listings', 'buybox', 'brands', 'ships',
    'units', 'share', 'first_seen', 'last_seen'}], most units first."""
    sellers, first_seen = sellers or {}, first_seen or {}
    by = {}

    def row(sid, name=None):
        s = sellers.get(sid) or {}
        r = by.setdefault(sid, {'seller_id': None if sid == AMAZON else sid, 'name': 'Amazon' if sid == AMAZON else s.get('name'),
                                'country': market if sid == AMAZON else s.get('country'), 'amazon': sid == AMAZON,
                                'rating_pct': s.get('rating_pct'), 'ratings': s.get('ratings'), 'asins': set(), 'buybox': 0,
                                'brands': {}, 'fba': 0, 'fbm': 0, 'units': None, 'first_seen': first_seen.get(sid),
                                'last_seen': None})
        r['name'] = r['name'] or name
        return r
    for p in pages or []:
        amazon = p.get('fulfillment') == 'amazon'
        sid = AMAZON if amazon else p.get('seller_id') or p.get('merchant_id')
        if not sid:
            continue
        r = row(sid, p.get('buybox_seller'))
        r['asins'].add(p['asin'])
        r['buybox'] += 1
        if p.get('brand'):
            r['brands'][p['brand']] = r['brands'].get(p['brand'], 0) + 1
        if p.get('fulfillment') in ('fba', 'amazon'):
            r['fba'] += 1
        elif p.get('fulfillment') == 'fbm':
            r['fbm'] += 1
        r['units'] = add_units(r['units'], units_of(p, market, curve_fn))
        ts = p.get('ts')
        if ts:
            r['last_seen'] = max(r['last_seen'] or 0, ts)
            r['first_seen'] = min(r['first_seen'] or ts, ts)
    for o in offers or []:
        for x in o.get('offers') or []:
            if x.get('amazon') or not x.get('seller_id'):
                continue
            r = row(x['seller_id'], x.get('seller'))
            if o['asin'] not in r['asins']:
                r['asins'].add(o['asin'])
                r['fba' if x.get('fba') else 'fbm'] += 1
    total = sum(r['units']['v'] for r in by.values() if r['units'])
    out = []
    for r in by.values():
        ships = 'FBA' if r['fba'] and not r['fbm'] else 'FBM' if r['fbm'] and not r['fba'] else 'both' if r['fba'] else None
        out.append({**{k: v for k, v in r.items() if k not in ('asins', 'brands', 'fba', 'fbm')},
                    'listings': len(r['asins']), 'ships': ships, 'units': _round(r['units']),
                    'share': round(r['units']['v'] / total, 3) if r['units'] and total else None,
                    'brands': [{'brand': b, 'listings': n} for b, n in sorted(r['brands'].items(), key=lambda x: -x[1])][:5]})
    out.sort(key=lambda r: (-(r['units'] or {}).get('v', -1), -r['listings'], (r['name'] or '').lower()))
    return out


# ---------- 2. Brand Explorer ----------
def brand_shares(rows, market, brands=None, amazon_asins=(), before=None, names=(), kw=''):
    """One niche's brands on page 1. rows: the organic listings [{'asin', 'title', 'bought'}]; brands {asin: brand}
    from listings (else a known brand name the title starts with, else its first word marked 'title', never a word of
    the keyword); amazon_asins: listings Amazon itself sells; before: the rows of a search about a month older.
    -> {'brands': [{'brand', 'src', 'listings', 'units', 'share', 'growth', 'new', 'amazon'}], 'amazon_listings',
    'top_share', 'known_units', 'listings', 'unbranded'}."""
    from .ads import avoid_words, brand_of
    amazon_asins, avoid, lost = set(amazon_asins or ()), avoid_words(kw), [0]

    def tally(rs, count=False):
        out = {}
        for r in rs or []:
            if not r.get('asin') or r.get('sponsored'):
                continue
            b, src = brand_of(r['asin'], r.get('title'), brands, names, avoid)
            if not b:
                lost[0] += count
                continue
            k = b.lower()
            t = out.setdefault(k, {'brand': b, 'src': src, 'listings': 0, 'units': None, 'amazon': False})
            if src == 'listing':
                t['src'] = 'listing'
            t['listings'] += 1
            e = badge.badge_estimate(r['bought'], market) if r.get('bought') else None
            if e and e.get('v') is not None:
                t['units'] = add_units(t['units'], {'v': e['v'], 'lo': e.get('lo'), 'hi': e.get('hi'), 'src': 'badge'})
            t['amazon'] = t['amazon'] or r['asin'] in amazon_asins or k in ('amazon basics', 'amazonbasics', 'amazon')
        return out
    now, old = tally(rows, True), tally(before) if before is not None else None
    total = sum(t['units']['v'] for t in now.values() if t['units'])
    out = []
    for k, t in now.items():
        g, new = None, None
        if old is not None:
            o = old.get(k)
            new = o is None
            if o and o['units'] and t['units'] and o['units']['v']:
                g = round(t['units']['v'] / o['units']['v'] - 1, 3)
        out.append({**t, 'units': _round(t['units']), 'share': round(t['units']['v'] / total, 3) if t['units'] and total else None,
                    'growth': g, 'new': new})
    out.sort(key=lambda b: (-(b['share'] or 0), -b['listings'], b['brand'].lower()))
    organic = [r for r in rows or [] if r.get('asin') and not r.get('sponsored')]
    return {'brands': out, 'amazon_listings': sum(1 for r in organic if r['asin'] in amazon_asins),
            'top_share': out[0]['share'] if out else None, 'known_units': round(total) if total else None,
            'listings': len(organic), 'unbranded': lost[0]}


# ---------- 3. Category gap explorer ----------
OPEN = ('lone_abroad', 'lone_fba', 'amazon_only', 'imported', 'missing')


def category_gaps(checks):
    """The Gap Finder's newest checks [{'market', 'gap_type', 'data': {'category'}}] -> one row per category and
    target market: {'category', 'market', 'checked', 'open', 'imported' (lone exporters abroad + imported only),
    'amazon_only', 'missing', 'local' (lone FBA, few, crowded), 'unread', 'share_open', 'share_imported',
    'share_amazon', 'share_local'}, most open first. Not-read checks are counted apart, never as missing."""
    rows = {}
    for c in checks or []:
        cat = ((c.get('data') or {}).get('category') or 'Other').strip() or 'Other'
        r = rows.setdefault((cat, c['market']), {'category': cat, 'market': c['market'], 'checked': 0, 'open': 0,
                                                'imported': 0, 'amazon_only': 0, 'missing': 0, 'local': 0, 'unread': 0})
        t = c.get('gap_type')
        if t == 'unread':
            r['unread'] += 1
            continue
        r['checked'] += 1
        r['open'] += t in OPEN
        if t in ('lone_abroad', 'imported'):
            r['imported'] += 1
        elif t == 'amazon_only':
            r['amazon_only'] += 1
        elif t == 'missing':
            r['missing'] += 1
        elif t in ('lone_fba', 'few', 'crowded'):
            r['local'] += 1
    out = []
    for r in rows.values():
        n = r['checked']
        out.append({**r, **{'share_' + k: round(r[v] / n, 3) if n else None
                            for k, v in (('open', 'open'), ('imported', 'imported'), ('amazon', 'amazon_only'), ('local', 'local'))}})
    out.sort(key=lambda r: (-(r['share_open'] or 0), -r['checked'], r['category']))
    return out


# ---------- 4. Seller origins ----------
def origins(sellers):
    """Where sellers are based, from Seller Explorer rows: [{'country' (None = not read), 'sellers', 'listings',
    'units', 'share'}], biggest share first."""
    by = {}
    for s in sellers or []:
        c = s.get('country')
        r = by.setdefault(c, {'country': c, 'sellers': 0, 'listings': 0, 'units': 0.0})
        r['sellers'] += 1
        r['listings'] += s.get('listings') or 0
        r['units'] += (s.get('units') or {}).get('v') or 0
    total = sum(r['units'] for r in by.values())
    out = [{**r, 'units': round(r['units']) or None, 'share': round(r['units'] / total, 3) if total else None} for r in by.values()]
    out.sort(key=lambda r: (r['country'] is None, -(r['share'] or 0), -r['sellers']))
    return out


# ---------- 5. Related products ----------
def _words(title):
    return {discovery._fold(w) for w in config.norm_kw(fr.search_words(title or '')).split()}


def alike(a, b):
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def related_graph(center, title, links, titles=None, brands=None):
    """The product's network from the carousels on product pages. links: [{'asin', 'kind', 'other', 'pos'}] from or
    to the centre ('bought' = customers also bought, 'viewed' = also viewed, 'fbt' = frequently bought together,
    'sponsored' = sponsored products on its page). A product with a title like the centre's is a competitor; one
    bought with it but unlike it is an add-on or bundle idea.
    -> {'nodes': [{'asin', 'title', 'brand', 'kinds', 'role', 'pos', 'links_back'}], 'competitors', 'addons',
    'brands': [{'brand', 'n'}], 'read': bool}."""
    titles, brands = titles or {}, brands or {}
    nodes = {}
    for l in links or []:
        if l['asin'] == center and l['other'] != center:
            n = nodes.setdefault(l['other'], {'asin': l['other'], 'kinds': [], 'pos': l['pos'], 'links_back': False})
            if l['kind'] not in n['kinds']:
                n['kinds'].append(l['kind'])
            n['pos'] = min(n['pos'], l['pos'])
        elif l['other'] == center and l['asin'] != center:
            n = nodes.setdefault(l['asin'], {'asin': l['asin'], 'kinds': [], 'pos': 99, 'links_back': False})
            n['links_back'] = True
    out = []
    for n in nodes.values():
        t = titles.get(n['asin']) or ''
        sim = alike(title, t)
        bought = {'bought', 'fbt'} & set(n['kinds'])
        role = 'competitor' if sim >= THRESH['alike'] else 'add-on' if bought else 'related'
        out.append({**n, 'title': t, 'brand': brands.get(n['asin']), 'role': role, 'alike': round(sim, 2)})
    order = {'fbt': 0, 'bought': 1, 'viewed': 2, 'sponsored': 3}
    out.sort(key=lambda n: (min([order.get(k, 9) for k in n['kinds']] or [9]), n['pos']))
    bc = {}
    for n in out:
        if n['brand']:
            bc[n['brand']] = bc.get(n['brand'], 0) + 1
    return {'nodes': out, 'competitors': [n for n in out if n['role'] == 'competitor'],
            'addons': [n for n in out if n['role'] == 'add-on'],
            'brands': [{'brand': b, 'n': c} for b, c in sorted(bc.items(), key=lambda x: -x[1])],
            'read': any(l['asin'] == center for l in links or [])}


# ---------- 7. Amazon out of stock ----------
def amazon_stockout(market, asin, title, before, now, ts):
    """An alert when Amazon itself sold a watched product and now does not: before / now are product readings
    {'fulfillment', 'available', 'buybox_seller', 'ts'}. One alert per outage (keyed on the last reading where
    Amazon sold it). None otherwise."""
    if not before or not now or before.get('fulfillment') != 'amazon' or before.get('available') is False:
        return None
    if now.get('fulfillment') == 'amazon' and now.get('available') is not False:
        return None
    gone = now.get('available') is False
    name = {'AU': 'Australia', 'AE': 'UAE', 'US': 'USA'}.get(market, market)
    who = now.get('buybox_seller') or 'another seller'
    return {'rule': 'amazon_stockout', 'severity': 3 if gone else 2, 'market': market, 'target': asin,
            'title': 'Amazon is out of stock: %s' % (title or asin)[:90] if gone else 'Amazon lost the buy box: %s' % (title or asin)[:90],
            'body': ('Amazon %s shows it as unavailable: Amazon itself ran out. Sellers with stock can win its buyers now.' % name
                     if gone else 'Amazon %s no longer sells it itself; the buy box went to %s. Amazon may be out of stock.'
                     % (name, who)),
            'data': {'asin': asin, 'available': now.get('available'), 'buybox': now.get('buybox_seller')},
            'dedupe_key': 'stockout|%s|%s|%d' % (market, asin, int(before.get('ts') or 0)), 'ts': ts}


# ---------- 8. Listing scorecard ----------
def _median(v):
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


def scorecard(mine, top):
    """Your listing against the top 10 of its keyword. mine / top: {'title_len', 'bullets', 'images', 'video',
    'aplus', 'rating', 'reviews', 'price'} (None = not read). -> {'items': [{'key', 'label', 'mine', 'typical',
    'verdict': 'ahead' | 'level' | 'behind' | 'unknown', 'tip'}], 'score' (0-100, None without data), 'n_top'}."""
    top = [t for t in top or [] if t]
    if not mine:
        return {'items': [], 'score': None, 'n_top': len(top), 'read': False}
    items = []

    def ratio(key, label, unit, tip):
        m, med = mine.get(key), _median([t.get(key) for t in top])
        if m is None or not med:
            items.append({'key': key, 'label': label, 'mine': m, 'typical': med, 'verdict': 'unknown', 'tip': None})
            return
        v = 'behind' if m < THRESH['behind'] * med else 'ahead' if m > THRESH['ahead'] * med else 'level'
        items.append({'key': key, 'label': label, 'mine': m, 'typical': round(med, 1), 'verdict': v,
                      'tip': tip % {'m': _n(m), 'med': _n(med), 'unit': unit} if v == 'behind' else None})

    def share(key, label, tip):
        known = [t.get(key) for t in top if t.get(key) is not None]
        m = mine.get(key)
        if m is None or not known:
            items.append({'key': key, 'label': label, 'mine': m, 'typical': None, 'verdict': 'unknown', 'tip': None})
            return
        n = sum(1 for x in known if x)
        most = n * 2 >= len(known)
        v = 'ahead' if m and not most else 'level' if bool(m) == most or m else 'behind'
        items.append({'key': key, 'label': label, 'mine': bool(m), 'typical': '%d of %d' % (n, len(known)), 'verdict': v,
                      'tip': tip % {'n': n, 'of': len(known)} if v == 'behind' else None})
    ratio('title_len', 'Title length', ' characters', 'Your title has %(m)s%(unit)s; the top 10 use about %(med)s. Add the words shoppers search for.')
    ratio('bullets', 'Bullet points', '', 'You have %(m)s bullet points; the top 10 have about %(med)s.')
    ratio('images', 'Pictures', '', 'You show %(m)s pictures; the top 10 show about %(med)s. Add lifestyle and size pictures.')
    share('video', 'Video', 'Add a video: %(n)s of the top %(of)s have one.')
    share('aplus', 'A+ content', 'Add A+ content: %(n)s of the top %(of)s have it.')
    m, med = mine.get('rating'), _median([t.get('rating') for t in top])
    if m is None or med is None:
        items.append({'key': 'rating', 'label': 'Rating', 'mine': m, 'typical': med, 'verdict': 'unknown', 'tip': None})
    else:
        v = 'behind' if m < med - THRESH['rating_step'] else 'ahead' if m > med + THRESH['rating_step'] else 'level'
        items.append({'key': 'rating', 'label': 'Rating', 'mine': m, 'typical': round(med, 1), 'verdict': v,
                      'tip': 'Your rating is %.1f against %.1f: read your 1-3 star reviews for what to fix.' % (m, med)
                      if v == 'behind' else None})
    ratio('reviews', 'Reviews', '', 'You have %(m)s reviews; the top 10 have about %(med)s. Vine or follow-up emails help.')
    pm, pmed = mine.get('price'), _median([t.get('price') for t in top])
    items.append({'key': 'price', 'label': 'Price', 'mine': pm, 'typical': round(pmed, 2) if pmed else None,
                  'verdict': 'info', 'tip': None if pm is None or not pmed else
                  'You are %d%% %s the top 10\'s median price.' % (abs(round((pm / pmed - 1) * 100)), 'above' if pm > pmed else 'below')})
    scored = [i for i in items if i['verdict'] in ('ahead', 'level', 'behind')]
    score = round(100 * sum({'ahead': 1, 'level': 0.6, 'behind': 0}[i['verdict']] for i in scored) / len(scored)) if scored else None
    return {'items': items, 'score': score, 'n_top': len(top), 'read': True}
