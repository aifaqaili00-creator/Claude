"""Demand vs sellers on one Amazon search page: how many sales a month each seller gets.

Sales come from the "N+ bought in past month" badges (engine/badge.py: bucket means with their ranges). Listings
without a badge still count as sellers but their sales are unknown, so per-seller numbers are an undercount; they
are labelled as estimates. Pure functions only.
"""
import datetime as dt

import countries as cty
from engine import badge


def _r(x, d=1):
    return None if x is None else round(x, d)


def per_seller(rows, market='US'):
    """rows: one search's listings (with 'sponsored', 'bought', 'origin', ...). Returns the demand-vs-sellers picture:

    listings        organic listings, best sellers first: asin, title, price, origin, speed, days, units (est or None)
    demand          {'v', 'lo', 'hi', 'badged'} buyers a month on page 1 (None values when no listing has a badge)
    sellers         organic listings; local / overseas / unknown: by where they ship from
    per_seller      demand / sellers {'v', 'lo', 'hi'}
    per_local_seller demand / local sellers, None when there are none yet
    top3_share      share of badge sales taken by the 3 best sellers (None with fewer than 3 badged)
    local_share_of_sales / overseas_share_of_sales  of the badge sales whose origin is known
    no_badge        listings with no badge (sales unknown)
    """
    organic = [r for r in rows or [] if not r.get('sponsored')]
    listings = []
    for r in organic:
        u = badge.badge_estimate(r['bought'], market) if r.get('bought') else None
        listings.append({k: r.get(k) for k in ('asin', 'title', 'price', 'origin', 'speed', 'days', 'url')} | {'units': u})
    listings.sort(key=lambda x: -(x['units']['v'] if x['units'] else -1))
    d = badge.page_demand([r.get('bought') for r in organic], market)
    n = len(organic)
    local = sum(1 for r in organic if r.get('origin') == 'local')
    overseas = sum(1 for r in organic if r.get('origin') == 'overseas')
    mid, lo, hi = d['mid_sum'], d['low_sum'], d['high_sum']
    out = {'listings': listings, 'demand': {'v': mid, 'lo': lo, 'hi': hi, 'badged': d['badged']},
           'sellers': n, 'local': local, 'overseas': overseas, 'unknown': n - local - overseas,
           'no_badge': n - d['badged'], 'per_seller': None, 'per_local_seller': None, 'top3_share': None,
           'local_share_of_sales': None, 'overseas_share_of_sales': None}
    if mid is None or not n:
        return out
    out['per_seller'] = {'v': _r(mid / n), 'lo': _r(lo / n), 'hi': _r(hi / n)}
    if local:
        out['per_local_seller'] = {'v': _r(mid / local), 'lo': _r(lo / local), 'hi': _r(hi / local)}
    units = [x['units']['v'] for x in listings if x['units']]
    total = sum(units)
    if len(units) >= 3 and total:
        out['top3_share'] = round(sum(units[:3]) / total, 3)
    by = {'local': 0.0, 'overseas': 0.0}
    for x in listings:
        if x['units'] and x['origin'] in by:
            by[x['origin']] += x['units']['v']
    known = by['local'] + by['overseas']
    if known:
        out['local_share_of_sales'] = round(by['local'] / known, 3)
        out['overseas_share_of_sales'] = round(by['overseas'] / known, 3)
    return out


def _pct(x):
    return int(round(x * 100))


def who_sells(listings, market, today=None):
    """Who sells the best page-1 listings. listings: best first, each with 'seller', 'seller_id', 'country' (code),
    'ships' ('amazon' / 'fba' / 'fbm' / None), 'brand', 'launched' ('YYYY-MM-DD') and 'units' ({'v', ...} or None).

    sellers    one row per seller (two listings of one seller count once): name, id, country, ships, listings,
               units, share; shares are of the known sales, or of the listings when no listing shows sales
    countries  the sellers' countries: code, listings, share; ships: amazon / fba / fbm / unknown listings
    brand_top  the brand with the biggest share; new: listings launched in the last year (of those with a launch day)
    dominant   the country of most sellers {'code', 'n', 'of', 'ships'} when one has half of them, else None
    reasons    [{'level': good|warn|bad, 'text'}]; takeaway: one line about who sells
    """
    today = today or dt.date.today()
    n = len(listings)
    basis = 'sales' if any((x.get('units') or {}).get('v') for x in listings) else 'listings'
    weight = (lambda x: (x.get('units') or {}).get('v') or 0) if basis == 'sales' else (lambda x: 1)
    total = sum(weight(x) for x in listings) or 1
    groups = {}
    for x in listings:
        key = 'amazon' if x.get('ships') == 'amazon' else x.get('seller_id') or (x.get('seller') or '').strip().lower()
        if not key:
            continue
        g = groups.setdefault(key, {'name': 'Amazon' if key == 'amazon' else x.get('seller'), 'id': x.get('seller_id'),
                                    'country': x.get('country'), 'ships': [], 'listings': 0, 'units': None, 'w': 0})
        g['name'] = g['name'] or x.get('seller')
        g['country'] = g['country'] or x.get('country')
        if x.get('ships') and x['ships'] not in g['ships']:
            g['ships'].append(x['ships'])
        g['listings'] += 1
        if (x.get('units') or {}).get('v'):
            g['units'] = (g['units'] or 0) + x['units']['v']
        g['w'] += weight(x)
    sellers = sorted(groups.values(), key=lambda g: (-g['w'], -g['listings']))
    for g in sellers:
        g['share'] = round(g.pop('w') / total, 3)
    known_seller = sum(1 for x in listings if x.get('ships') == 'amazon' or x.get('seller_id') or x.get('seller'))
    by_c = {}
    for x in listings:
        if x.get('country') and x.get('ships') != 'amazon':          # Amazon itself is counted on its own
            c = by_c.setdefault(x['country'], {'code': x['country'], 'listings': 0, 'w': 0})
            c['listings'] += 1
            c['w'] += weight(x)
    known_country = sum(c['listings'] for c in by_c.values())
    ctrs = sorted(by_c.values(), key=lambda c: (-c['listings'], -c['w']))
    for c in ctrs:
        c['share'] = round(c.pop('w') / total, 3)
    ships = {k: sum(1 for x in listings if x.get('ships') == k) for k in ('amazon', 'fba', 'fbm')}
    ships['unknown'] = n - sum(ships.values())
    brands = {}
    for x in listings:
        if x.get('brand'):
            b = brands.setdefault(x['brand'].strip().lower(), {'name': x['brand'].strip(), 'listings': 0, 'w': 0})
            b['listings'] += 1
            b['w'] += weight(x)
    top_b = max(brands.values(), key=lambda b: (b['w'], b['listings']), default=None)
    brand_top = {'name': top_b['name'], 'listings': top_b['listings'], 'share': round(top_b['w'] / total, 3)} if top_b else None
    days = []
    for x in listings:
        try:
            days.append((today - dt.date.fromisoformat(x['launched'])).days)
        except (TypeError, ValueError, KeyError):
            continue
    new = {'n': sum(1 for d in days if d <= 365), 'known': len(days)}
    seller_ships = [k for k in ('fba', 'fbm') if ships['fba'] + ships['fbm'] and ships[k] / (ships['fba'] + ships['fbm']) >= .6]
    dominant = None
    if ctrs and known_country >= 3 and ctrs[0]['listings'] / known_country >= .5:
        dominant = {'code': ctrs[0]['code'], 'n': ctrs[0]['listings'], 'of': n, 'ships': seller_ships[0] if seller_ships else None}

    reasons = []

    def say(level, text):
        reasons.append({'level': level, 'text': text})
    of_what = (lambda s: '%d%% of the sales' % _pct(s['share'])) if basis == 'sales' else \
        (lambda s: '%d of the top %d listings' % (s['listings'], n))
    if known_seller >= max(3, n // 2):
        a = ships['amazon']
        if a >= 3:
            say('bad', 'Amazon itself sells %d of the top %d: hard to beat on price and delivery.' % (a, n))
        elif a:
            say('warn', 'Amazon itself sells %d of the top %d.' % (a, n))
        else:
            say('good', 'Amazon itself does not sell in the top %d.' % n)
        if sellers and sellers[0]['share'] >= .5 and len(sellers) > 1:
            say('bad', 'One seller (%s) has %s.' % (sellers[0]['name'], of_what(sellers[0])))
        elif len(sellers) >= 3 and sum(s['share'] for s in sellers[:3]) >= .75:
            say('warn', 'The 3 biggest sellers have %d%% of the %s.' % (_pct(sum(s['share'] for s in sellers[:3])),
                                                                   'sales' if basis == 'sales' else 'top listings'))
        elif len(sellers) >= 3:
            say('good', 'Spread out: %d different sellers, the biggest has %s.' % (len(sellers), of_what(sellers[0])))
    if dominant:
        c, adj = dominant['code'], cty.adjective(dominant['code'])
        if c == 'CN':
            say('warn', 'Sellers based in China have %d of the top %d listings: expect price competition.' % (dominant['n'], n))
        elif c == market:
            say('warn', 'Most top listings are sold by %s businesses: established local competition.' % (adj or cty.name(c)))
        else:
            say('warn', 'Most top listings are sold by sellers based in %s.' % cty.name(c))
    elif len(ctrs) >= 2 and known_country >= 3:
        say('good', 'The sellers come from %d countries: no country dominates.' % len(ctrs))
    if ships['fbm'] >= 2:
        say('good', '%d of the top %d are shipped by the seller (FBM): stock in Amazon\'s warehouse would deliver faster.'
            % (ships['fbm'], n))
    if brand_top and brand_top['share'] >= .5 and len(brands) > 1:
        say('bad', 'One brand (%s) has %s.' % (brand_top['name'], of_what(brand_top)))
    if new['known'] >= 5:
        if new['n'] >= 3:
            say('good', '%d of the top listings launched in the last year: newcomers can win here.' % new['n'])
        elif not new['n']:
            say('warn', 'None of the top listings is under a year old: the leaders are established.')

    takeaway = None
    if known_seller:
        ship_txt = {'fba': ' using FBA', 'fbm': ' shipping orders themselves (FBM)'}.get(seller_ships[0] if seller_ships else '', '')
        if dominant:
            adj = cty.adjective(dominant['code'])
            takeaway = ('Mostly %s sellers' % adj if adj else 'Mostly sellers based in %s' % cty.name(dominant['code'])) + ship_txt + \
                ': %d of the top %d' % (dominant['n'], n)
            share = sum(c['share'] for c in ctrs if c['code'] == dominant['code'])
            takeaway += (', %d%% of the sales.' % _pct(share)) if basis == 'sales' else '.'
        else:
            takeaway = ('Sellers from %d countries' % len(ctrs) if len(ctrs) >= 2 else '%d different sellers' % len(sellers)) + \
                (ship_txt.replace(' using', ', most using').replace(' shipping', ', most shipping') if ship_txt else '') + '.'
        if ships['amazon']:
            takeaway += ' Amazon itself sells %d.' % ships['amazon']
        elif known_seller >= max(3, n // 2):
            takeaway += ' Amazon itself does not sell here.'
    return {'n': n, 'basis': basis, 'known': {'seller': known_seller, 'country': known_country}, 'sellers': sellers,
            'countries': ctrs, 'ships': ships, 'brand_top': brand_top, 'new': new, 'dominant': dominant,
            'amazon': ships['amazon'], 'reasons': reasons, 'takeaway': takeaway}
