"""Out of stock: booming and brand-new products of Product ideas, and whether they ran out with few sellers.

- candidates(): the products worth watching, from the lists the app reads anyway: Booming (sales rank up 300%+ on Movers
  & Shakers, or climbing fast on New Releases or Best Sellers) and brand-new (New Releases top 10), the strongest first.
- reading(): one read of a listing (gapfinder.read_listing) -> the facts kept for it.
- state(): a listing's readings, newest first -> in stock, out of stock (and since when), not on Amazon there yet, or
  not read. Out of stock "recently" = it was in stock at an earlier reading of the last 30 days, or it still holds a
  sales rank or a "bought in past month" badge (it sold until lately).
- rows(): one line per product for the Out of stock tab: where it is out, with how many sellers, and where it is in
  stock or missing. A product fits when it ran out recently and fewer than 5 sellers offered it.
Something not read is 'unread', never out of stock. Pure functions, JSON-safe output.
"""
MARKET_NAMES = {'US': 'USA', 'AU': 'Australia', 'AE': 'UAE'}
THRESH = {
    'booming_pct': 300,            # Movers & Shakers: sales rank up this much (Product ideas' "Booming")
    'new_top': 10,                 # New Releases: this high on the list is brand new
    'climb_top': 20,               # New Releases / Best Sellers: a climb into this top ...
    'climb_min': 15,               # ... by this many places is booming
    'recent_days': 30,             # an earlier in-stock reading this recent makes the stock-out "recent"
    'max_sellers': 4,              # fewer than 5 sellers (Settings: oos_max_sellers)
    'idea_days': 14,               # list readings this recent make a product an idea worth watching
}


def _n(x):
    return '{:,}'.format(int(round(x)))


LIST_NAMES = {'movers': 'Movers & Shakers', 'new': 'New Releases', 'best': 'Best Sellers'}


def candidates(list_rows, now, days=None):
    """list_rows: storage.list_history rows of the US, Australian and UAE lists ({'kind', 'market', 'slug', 'asin', 'pct',
    'pos', 'ts', 'title', 'image', 'category', 'price'}). Returns [{'asin', 'market' (where it was spotted), 'title',
    'image', 'category', 'price', 'kind' ('booming' | 'new'), 'why', 'pct', 'pos', 'last_seen', 'priority'}], strongest
    first. Booming: sales rank up 300%+ on Movers & Shakers, or climbing fast on New Releases or Best Sellers (15+ places
    into the top 20, or new on the list and already in its top 10: Amazon has stopped showing Movers & Shakers at times).
    Brand new: New Releases top 10."""
    since = now - (days or THRESH['idea_days']) * 86400
    first_of_list, track = {}, {}
    for r in list_rows or ():
        if not r.get('asin') or (r.get('ts') or 0) < since:
            continue
        lk = (r['market'], r.get('kind'), r.get('slug'))
        first_of_list[lk] = min(first_of_list.get(lk, r['ts']), r['ts'])
        track.setdefault(lk + (r['asin'],), []).append(r)
    best = {}
    for (mk, kind, slug, asin), rs in track.items():
        rs.sort(key=lambda r: r['ts'])
        first, last = rs[0], rs[-1]
        pos, cat = last.get('pos'), last.get('category') or slug
        found = None
        if kind == 'movers' and (last.get('pct') or 0) >= THRESH['booming_pct']:
            found = ('booming', 'Booming: sales rank up %s%%' % _n(last['pct']), 50 + min(last['pct'], 5000) / 100)
        elif kind in ('new', 'best') and pos and pos <= THRESH['climb_top']:
            climb = (first.get('pos') or pos) - pos
            if climb >= THRESH['climb_min']:
                found = ('booming', 'Climbing fast: #%d to #%d in %s, %s' % (first['pos'], pos, LIST_NAMES[kind], cat),
                         50 + min(climb, 100) / 2)
            elif first['ts'] - first_of_list[(mk, kind, slug)] >= 2 * 86400 and pos <= 10:
                found = ('booming', 'New on %s, %s: already #%d' % (LIST_NAMES[kind], cat, pos), 55 - pos)
        if not found and kind == 'new' and pos and pos <= THRESH['new_top']:
            found = ('new', 'Brand new: #%d in New Releases, %s' % (pos, cat), 30 - 2 * pos)
        if not found:
            continue
        kind_, why, prio = found
        old = best.get((mk, asin))
        item = {'asin': asin, 'market': mk, 'title': last.get('title') or (old or {}).get('title'),
                'image': last.get('image') or (old or {}).get('image'), 'category': cat, 'price': last.get('price'),
                'kind': kind_, 'why': why, 'pct': last.get('pct'), 'pos': pos, 'last_seen': last['ts'], 'priority': prio}
        if not old or prio > old['priority']:
            best[(mk, asin)] = item
    return sorted(best.values(), key=lambda c: (-c['priority'], -(c['last_seen'] or 0)))


def reading(listing):
    """gapfinder.read_listing's answer -> what a stock_check row keeps."""
    lst = listing or {}
    page = lst.get('page') or {}
    cat = lst.get('catalog') or {}
    offers = lst.get('offers') or []
    buybox = next((o for o in offers if o.get('buybox')), offers[0] if offers else None)
    n = lst.get('n_offers')
    if n is None and offers:
        n = len(offers)
    return {'status': lst.get('status') or 'not read', 'available': lst.get('available'), 'n_offers': n,
            'amazon': lst.get('amazon'), 'rank': page.get('root_rank') or cat.get('rank'), 'badge': page.get('badge_low'),
            'price': page.get('price') or lst.get('price'), 'source': lst.get('source'),
            'data': {'seller': (buybox or {}).get('seller'), 'stock': (buybox or {}).get('stock'),
                     'country': (buybox or {}).get('country'), 'days': (buybox or {}).get('days') or page.get('delivery_days'),
                     'title': page.get('title') or cat.get('title'), 'brand': page.get('brand') or cat.get('brand'),
                     'image': page.get('image')}}


VALID = ('ok', 'missing')


def _out_now(r):
    return r['status'] == 'ok' and (r.get('available') is False or r.get('n_offers') == 0)


def state(readings, now, max_sellers=None):
    """A listing's readings in one market, newest first -> {'state': 'out' | 'in' | 'missing' | 'unread', 'recent', 'since',
    'last_in', 'sellers', 'fits', 'why', 'ts', 'price', 'rank', 'badge'}."""
    cap = THRESH['max_sellers'] if max_sellers is None else max_sellers
    good = [r for r in readings or () if r.get('status') in VALID]
    if not good:
        last = (readings or [{}])[0]
        return {'state': 'unread', 'recent': False, 'fits': False, 'since': None, 'last_in': None, 'sellers': None,
                'why': 'Not read yet' + (' (%s)' % last['status'] if last.get('status') else ''), 'ts': last.get('ts'),
                'price': None, 'rank': None, 'badge': None}
    now_r = good[0]
    base = {'ts': now_r['ts'], 'price': now_r.get('price'), 'rank': now_r.get('rank'), 'badge': now_r.get('badge')}
    if now_r['status'] == 'missing':
        return {**base, 'state': 'missing', 'recent': False, 'fits': False, 'since': None, 'last_in': None, 'sellers': 0,
                'why': 'Not on Amazon there yet'}
    window = [r for r in good if r['ts'] >= now - THRESH['recent_days'] * 86400]
    seen = [r['n_offers'] for r in window if r.get('n_offers')]
    sellers = max(seen) if seen else now_r.get('n_offers')
    if not _out_now(now_r):
        return {**base, 'state': 'in', 'recent': False, 'fits': False, 'since': None, 'last_in': now_r['ts'],
                'sellers': sellers, 'why': 'In stock%s' % (', %d seller%s' % (sellers, '' if sellers == 1 else 's')
                                                           if sellers else '')}
    since = now_r['ts']
    last_in = None
    for r in good[1:]:
        if _out_now(r):
            since = r['ts']
        elif r['status'] == 'ok':
            last_in = r['ts']
            break
    was_in = bool(last_in and last_in >= now - THRESH['recent_days'] * 86400)
    sold = bool(now_r.get('rank') or now_r.get('badge'))
    recent = was_in or sold
    few = sellers is None or sellers <= cap
    if was_in:
        why = 'Went out of stock: it was in stock at an earlier reading'
    elif sold:
        why = 'Out of stock, still ranked%s: it sold until lately' % (' #%s' % _n(now_r['rank']) if now_r.get('rank') else '')
    else:
        why = 'Out of stock, no sign of recent sales'
    if sellers is None:
        why += '; sellers not read'
    else:
        why += '; %d seller%s offered it' % (sellers, '' if sellers == 1 else 's')
    return {**base, 'state': 'out', 'recent': recent, 'fits': recent and few, 'since': since, 'last_in': last_in,
            'sellers': sellers, 'why': why}


def rows(cands, checks, now, markets=('US', 'AU', 'AE'), max_sellers=None):
    """One line per product (cands: candidates(); checks: {(market, asin): readings newest first}): {'asin', 'title',
    'image', 'idea' (the candidate), 'markets': {mk: state()}, 'out': [markets where it fits], 'fits', 'score'}, products
    that fit first. A product counts when it ran out recently, in at least one market, with few sellers."""
    out = []
    for c in cands or ():
        st = {m: state(checks.get((m, c['asin'])), now, max_sellers) for m in markets}
        fit = [m for m in markets if st[m]['fits']]
        reached = [m for m in markets if st[m]['state'] in ('in', 'out')]
        title = c.get('title') or next((r['data'].get('title') for m in markets for r in checks.get((m, c['asin'])) or ()
                                        if (r.get('data') or {}).get('title')), None)
        image = c.get('image') or next((r['data'].get('image') for m in markets for r in checks.get((m, c['asin'])) or ()
                                        if (r.get('data') or {}).get('image')), None)
        score = len(fit) * 100 + c['priority'] - min((st[m]['sellers'] or 0) for m in fit) * 5 if fit else c['priority']
        out.append({'asin': c['asin'], 'title': title, 'image': image, 'idea': c, 'markets': st, 'out': fit,
                    'reached': reached, 'fits': bool(fit), 'score': round(score, 1)})
    out.sort(key=lambda r: (not r['fits'], -r['score']))
    return out


def plan(cands, checks, now, n, markets=('US', 'AU', 'AE'), fresh_h=20):
    """What to read next in these markets, at most n reads: (market, asin) not read in the last fresh_h hours, a product out of stock now
    first (did it come back?), then never read, then the strongest ideas; a product spotted in the USA is read in the USA,
    Australia and the UAE, one spotted in Australia or the UAE in its own market and the other one."""
    todo = []
    for c in cands or ():
        where = list(MARKET_NAMES) if c['market'] == 'US' else [c['market']] + [m for m in MARKET_NAMES if m not in ('US', c['market'])]
        for m in (m for m in where if m in markets):
            rs = checks.get((m, c['asin'])) or []
            if rs and rs[0].get('status') in VALID and rs[0]['ts'] >= now - fresh_h * 3600:
                continue                               # read lately (a blocked read is tried again)
            out_now = bool(rs) and rs[0].get('status') == 'ok' and _out_now(rs[0])
            missing = bool(rs) and rs[0].get('status') == 'missing'
            todo.append(((0 if out_now else 1 if not rs else 3 if missing else 2), -c['priority'], m, c['asin']))
    todo.sort()
    return [(m, a) for _, _, m, a in todo[:n]]
