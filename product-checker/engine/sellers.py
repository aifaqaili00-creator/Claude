"""Demand vs sellers on one Amazon search page: how many sales a month each seller gets.

Sales come from the "N+ bought in past month" badges (engine/badge.py: bucket means with their ranges). Listings
without a badge still count as sellers but their sales are unknown, so per-seller numbers are an undercount; they
are labelled as estimates. Pure functions only.
"""
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
