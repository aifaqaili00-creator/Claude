"""Market Gaps: where Amazon Australia and the UAE leave room, from a SmartScout baseline and the app's own readings.

- niches(): SmartScout brand rows -> one row per niche (SmartScout's primary subcategory): its revenue, how concentrated
  it is and who dominates it: the local Amazon, Amazon's Global Store (Amazon US, UK, Germany... shipping it in),
  cross-border resellers, or brands.
- niche_gap() names the gap, score() ranks niches 0-100.
- missing_brands(): big US brands that are absent from a market's baseline. sellers_summary(): who takes the money.
- live_verdict(): page 1 of the niche's phrase and the buy boxes of its top listings, read now: is it still open?
- category_table(): a Best Sellers list read now -> brand and seller concentration, the app's own successor of the
  SmartScout brand table (SmartScout is a one-off baseline: the app keeps the picture fresh by itself).
SmartScout numbers are its estimates in the marketplace's money (A$, AED, US$). Something not read is 'unread', never
open. Pure functions, JSON-safe output.
"""
import math
import re

import config
import file_rank as fr

from . import ads as admod
from . import offers as offmod
from .decide import RULES as DECIDE_RULES
from .discovery import _fold

MARKET_NAMES = {'AU': 'Australia', 'AE': 'UAE', 'US': 'USA'}
LOCAL_AMAZON = {'AU': {'amazon au', 'amazon.com.au', 'amazon australia', 'amazon warehouse'},
                'AE': {'amazon.ae', 'amazon ae', 'amazon uae', 'amazon warehouse'},
                'US': {'amazon.com', 'amazon', 'amazon us', 'amazon warehouse'}}
# shops that buy abroad and ship each order in (seen dominating UAE niches); confirmed by the live check, never trusted alone
RESELLERS = re.compile(r'^(desertcart|jetsouq|ubuy|exborders)', re.I)
GENERIC = {'generic', 'unbranded', 'nobrand', 'unknown', 'na', 'amazon', 'amazonbasics', 'amazonbrand'}   # brand_key() spellings
RISKY = re.compile(r'\b(books?|kindle|ebooks?|software|music|video games?|renewed|refurbished|mobile phones?|smartphones?|'
                   r'cell phones?|laptops?|notebooks? computers?|tablets?|computers?|desktops?|towers?|tvs?|televisions?|'
                   r'monitors?|graphics cards?|gift cards?|prescription|subscriptions?|warrant(?:y|ies)|services?|digital)\b', re.I)
ROLES = ('amazon', 'global_store', 'reseller', 'seller', 'unknown')
ROLE_LABEL = {'amazon': 'Amazon itself', 'global_store': "Amazon's Global Store", 'reseller': 'import resellers',
              'seller': 'brands and their sellers', 'unknown': 'not named'}
THRESH = {
    'cross_min': 0.40,             # Global Store + resellers' share of a niche from which shoppers mostly wait for imports
    'amazon_min': 0.50,            # the local Amazon's share from which a niche is "Amazon's"
    'amazon_next': 0.25,           # ... and the best other brand makes less than this of Amazon's revenue: a gap after it
    'thin_brands': 3,              # brands with a real share (real_share of the niche) at most
    'thin_sellers': 1.5,           # ... each sold by this few sellers on average
    'thin_top3': 0.7,              # ... and the top 3 brands take this much of it
    'real_share': 0.05,
    'us_ratio': 3.0,               # the niche's share of the US market is this many times its share here
    'min_revenue': {'AU': 20000, 'AE': 50000, 'US': 200000},   # a month, in the marketplace's money
    'missing_us': 200000,          # US$ a month from which a US brand missing here is worth showing
    'fast_open': 4,                # live: fast-delivery listings on page 1 that still leave room (amazon_check.level_for)
    'fast_crowded': 10,
    'overseas_min': 0.40,          # live: share of page 1's "bought" badges on listings shipped from abroad
}
TYPES = (
    ('global_store', 'Shipped in from abroad', "Most of it is sold by Amazon's Global Store or resellers that ship it in from "
                                               'abroad: shoppers wait one to three weeks, so local stock would win them.'),
    ('amazon_then_gap', 'Amazon, then a gap', 'Amazon itself sells most of it and the next brand is far behind: nobody local '
                                              'competes with Amazon yet.'),
    ('thin', 'Few brands', 'Only a few brands sell it, each with few sellers.'),
    ('us_proven', 'Bigger in the USA', 'It takes a much bigger share of the US market than of this one: room to grow here.'),
    ('crowded', 'Spread out', 'Many brands and sellers share it: no clear gap.'),
)
LABEL = {k: v for k, v, _ in TYPES}
MEANS = {k: m for k, _, m in TYPES}
GAPS = ('global_store', 'amazon_then_gap', 'thin', 'us_proven')
VERDICTS = (
    ('open', 'Open', 'Few fast local listings, and shoppers buy from abroad or from Amazon only.'),
    ('amazon_only', 'Amazon only', 'Amazon itself holds the buy boxes, with no local seller beside it.'),
    ('few', 'Some local sellers', 'Few fast listings, but local sellers already hold buy boxes.'),
    ('closing', 'Filling up', '5 to 9 listings deliver fast: local sellers are arriving.'),
    ('closed', 'Crowded', '10 or more listings deliver fast.'),
    ('unread', 'Not read', 'Amazon could not be read (blocked, paused or out of budget): a gap in the data, never open.'),
)
VLABEL = {k: v for k, v, _ in VERDICTS}
VMEANS = {k: m for k, _, m in VERDICTS}
STOP = {'and', 'for', 'the', 'of', 'with', 'in', 's', '&'}


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and not math.isinf(v) else None


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _n(x):
    return '{:,}'.format(int(round(x)))


def _pct(x):
    return '%d%%' % round(100 * x)


def niche_key(name):
    """One key per niche name, alike across markets: 'Leg & Foot Supports' -> 'foot leg support'."""
    return ' '.join(sorted({_fold(w) for w in config.norm_kw(name).replace('&', ' ').split() if w not in STOP}))


def brand_key(name):
    return re.sub(r'[^a-z0-9]+', '', str(name or '').lower())


def seller_role(name, market):
    """'amazon' (the local Amazon), 'global_store' (Amazon US... selling here), 'reseller', 'seller' or 'unknown'."""
    n = ' '.join(str(name or '').split())
    if not n or n == '-':
        return 'unknown'
    if n.lower() in LOCAL_AMAZON.get(market, ()):
        return 'amazon'
    if offmod.store_country(n, market):
        return 'global_store'
    if RESELLERS.match(n):
        return 'reseller'
    return 'seller'


def risk_of(name, category=None):
    """Why a niche is risky to start in (phones, laptops, books, renewed...), or None."""
    m = RISKY.search('%s %s' % (name or '', category or ''))
    if m:
        return m.group(0).lower()
    m = fr.TECH[1].search(name or '')
    return m.group(0).lower() if m else None


def _wavg(pairs):
    pairs = [(v, w) for v, w in pairs if v is not None and w]
    tw = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tw if tw else None


# ---------- the baseline: niches from SmartScout brands ----------
def niches(rows, market):
    """SmartScout brand rows (storage.SS_BRAND_COLS) of one market -> [niche], biggest revenue first."""
    groups = {}
    for r in rows or ():
        name = ' '.join(str(r.get('subcategory') or '').split())
        rev = _f(r.get('revenue'))
        if not name or name == '-' or not rev or rev <= 0 or brand_key(r.get('brand')) in GENERIC:
            continue                                  # "Generic" is every unbranded listing, filed under one subcategory
        g = groups.setdefault(niche_key(name), {'names': {}, 'rows': []})
        g['names'][name] = g['names'].get(name, 0) + rev
        g['rows'].append({**r, 'revenue': rev})
    out = []
    for key, g in groups.items():
        rs = sorted(g['rows'], key=lambda r: -r['revenue'])
        total = sum(r['revenue'] for r in rs)
        shares = dict.fromkeys(ROLES, 0.0)
        stores = {}
        cats = {}
        for r in rs:
            role = seller_role(r.get('dominant_seller'), market)
            r['role'] = role
            shares[role] += r['revenue'] / total
            if role == 'global_store':
                c = offmod.store_country(r.get('dominant_seller'), market)
                stores[c] = stores.get(c, 0) + r['revenue'] / total
            cat = str(r.get('category') or '').strip()
            if cat and cat != '-':
                cats[cat] = cats.get(cat, 0) + r['revenue']
        amazon_rev = sum(r['revenue'] for r in rs if r['role'] == 'amazon')
        best_other = max([r['revenue'] for r in rs if r['role'] != 'amazon'] or [0])
        per_listing = [((_f(r.get('reviews')) or 0) / max(1, _f(r.get('products')) or 1), r['revenue']) for r in rs
                       if _f(r.get('reviews')) is not None]
        name = max(g['names'], key=g['names'].get)
        category = max(cats, key=cats.get) if cats else None
        risk = risk_of(name, category)
        out.append({
            'key': key, 'name': name, 'category': category, 'market': market, 'revenue': round(total, 2),
            'brands': len(rs), 'real_brands': sum(1 for r in rs if r['revenue'] >= THRESH['real_share'] * total),
            'top1': rs[0]['revenue'] / total, 'top3': sum(r['revenue'] for r in rs[:3]) / total,
            'shares': {k: round(v, 4) for k, v in shares.items()},
            'stores': {k: round(v, 4) for k, v in sorted(stores.items(), key=lambda kv: -kv[1])},
            'cross': round(shares['global_store'] + shares['reseller'], 4),
            'amazon_next': round(best_other / amazon_rev, 4) if amazon_rev else None,
            'avg_sellers': _wavg([(_f(r.get('avg_sellers')), r['revenue']) for r in rs]),
            'reviews': _wavg(per_listing),
            'price': _wavg([(_f(r.get('avg_price')), r['revenue']) for r in rs]),
            'rating': _wavg([(_f(r.get('rating')), r['revenue']) for r in rs]),
            'growth': _wavg([(_f(r.get('growth_12m')), r['revenue']) for r in rs]),
            'top': [{'brand': str(r.get('brand') or ''), 'revenue': round(r['revenue'], 2), 'share': round(r['revenue'] / total, 4),
                     'seller': r.get('dominant_seller') or None, 'role': r['role'], 'price': _f(r.get('avg_price')),
                     'reviews': _f(r.get('reviews')), 'sellers': _f(r.get('avg_sellers'))} for r in rs[:6]],
            'risky': bool(risk), 'risk_why': risk, 'flags': [label for label, rx in fr.CHECKS if rx.search(name)],
        })
    out.sort(key=lambda n: -n['revenue'])
    return out


def niche_boost(title, strong):
    """The best score among strong niches {niche_key: score} whose every word is in the title, else 0 (the Gap Finder
    checks proven products in strong Market Gaps niches first)."""
    have = {_fold(w) for w in config.norm_kw(title).split()}
    best = 0
    for key, s in (strong or {}).items():
        words = key.split()
        if words and set(words) <= have:
            best = max(best, s)
    return best


def us_index(us_niches):
    """{niche_key: US niche} and the US total revenue, for niche_gap()."""
    return {n['key']: n for n in us_niches or ()}, sum(n['revenue'] for n in us_niches or ())


def us_ratio(n, local_total, us, us_total):
    """How many times bigger the niche's share of the US market is than its share here (None when unknown)."""
    if not us or not local_total or not us_total or not n.get('revenue'):
        return None
    return (us['revenue'] / us_total) / (n['revenue'] / local_total)


def _out(kind, why, conf, **kw):
    return {'type': kind, 'label': LABEL[kind], 'why': why, 'conf': conf, 'gap': kind in GAPS, 'means': MEANS[kind], **kw}


def niche_gap(n, ratio=None):
    """The gap of one baseline niche: {'type', 'label', 'why', 'conf', 'gap', 'means', 'also'} (also: the other gap types
    that fit, weaker). ratio: us_ratio() or None."""
    sh, market = n['shares'], n.get('market')
    conf = 'low' if sh.get('unknown', 0) >= 0.4 else 'medium'          # SmartScout's estimates, a month old at best
    fits = []
    if n['cross'] >= THRESH['cross_min']:
        parts = []
        if sh['global_store']:
            parts.append("Amazon's Global Store %s (%s)" % (_pct(sh['global_store']), ', '.join(n['stores']) or 'abroad'))
        if sh['reseller']:
            parts.append('import resellers %s' % _pct(sh['reseller']))
        fits.append(('global_store', '%s of the niche is shipped in from abroad: %s.' % (_pct(n['cross']), ', '.join(parts))))
    if sh['amazon'] >= THRESH['amazon_min'] and (n['amazon_next'] is None or n['amazon_next'] < THRESH['amazon_next']):
        nxt = 'no other brand' if not n['amazon_next'] else 'the next brand makes %s of what Amazon makes' % _pct(n['amazon_next'])
        fits.append(('amazon_then_gap', 'Amazon itself takes %s, and %s.' % (_pct(sh['amazon']), nxt)))
    if 1 <= n['real_brands'] <= THRESH['thin_brands'] and n['top3'] >= THRESH['thin_top3'] and \
            (n['avg_sellers'] or 0) <= THRESH['thin_sellers']:
        fits.append(('thin', '%d brand%s with a real share, about %.1f seller%s each.' % (
            n['real_brands'], '' if n['real_brands'] == 1 else 's', n['avg_sellers'] or 0,
            '' if round(n['avg_sellers'] or 0, 1) == 1 else 's')))
    if ratio and ratio >= THRESH['us_ratio']:
        fits.append(('us_proven', 'Its share of the US market is %.0f times its share of Amazon %s.' % (
            ratio, MARKET_NAMES.get(market, market))))
    if not fits:
        return _out('crowded', '%d brands share it, the biggest %s, %.1f sellers each on average.' % (
            n['brands'], _pct(n['top1']), n['avg_sellers'] or 0), conf, also=[])
    kind, why = fits[0]
    return _out(kind, why, conf, also=[{'type': k, 'label': LABEL[k], 'why': w} for k, w in fits[1:]])


def price_fit(price, market):
    band = DECIDE_RULES['price_band']['limit'].get(market)
    if price is None or not band:
        return None
    return band[0] <= price <= band[1]


def score(n, gap, ratio=None):
    """0-100 and its parts: demand 30, openness 35, review barrier 15, price 10, momentum 10."""
    market = n.get('market')
    floor = THRESH['min_revenue'].get(market, 20000)
    demand = _clip((math.log10(max(n['revenue'], 1)) - math.log10(floor)) / 2)
    sh = n['shares']
    amazon_gap = sh['amazon'] * (1 - _clip((n['amazon_next'] or 0) / 0.5)) if sh['amazon'] >= 0.3 else 0
    thin = _clip((3.5 - (n['avg_sellers'] or 3.5)) / 2.5) * _clip(n['top3'])
    usp = _clip(math.log(ratio) / math.log(10)) if ratio and ratio > 1 else 0
    openness = max(_clip(n['cross'] / 0.8), amazon_gap, thin, usp)
    if not gap['gap']:
        openness *= 0.6
    barrier = 1 - _clip(math.log10((n['reviews'] or 0) + 1) / math.log10(5000)) if n['reviews'] is not None else 0.5
    pf = price_fit(n['price'], market)
    price = 0.5 if pf is None else 1.0 if pf else 0.3
    momentum = _clip(0.5 + (n['growth'] or 0) / 2) if n['growth'] is not None else 0.5
    parts = {'demand': demand, 'open': openness, 'barrier': barrier, 'price': price, 'momentum': momentum}
    total = 30 * demand + 35 * openness + 15 * barrier + 10 * price + 10 * momentum
    return int(round(total)), {k: round(v, 3) for k, v in parts.items()}


def rank_niches(rows, market, us_rows=None, min_revenue=None):
    """The scored niches of a market: [{niche..., 'gap', 'score', 'parts', 'us_ratio', 'us_revenue'}], best first. Niches
    under the market's minimum revenue a month are left out."""
    floor = THRESH['min_revenue'].get(market, 20000) if min_revenue is None else min_revenue
    ns = niches(rows, market)
    total = sum(n['revenue'] for n in ns)
    idx, us_total = us_index(niches(us_rows, 'US')) if us_rows else ({}, 0)
    out = []
    for n in ns:
        if n['revenue'] < floor:
            continue
        us = idx.get(n['key'])
        ratio = us_ratio(n, total, us, us_total)
        gap = niche_gap(n, ratio)
        s, parts = score(n, gap, ratio)
        out.append({**n, 'gap': gap, 'score': s, 'parts': parts, 'us_ratio': round(ratio, 2) if ratio else None,
                    'us_revenue': round(us['revenue']) if us else None})
    out.sort(key=lambda n: (-n['score'], -n['revenue']))
    return out


def missing_brands(us_rows, local_rows, min_us=None, limit=200):
    """US brands selling at least `min_us` US$ a month that are absent from the local baseline, biggest first."""
    floor = THRESH['missing_us'] if min_us is None else min_us
    have = {brand_key(r.get('brand')) for r in local_rows or ()}
    out = []
    for r in us_rows or ():
        rev, b = _f(r.get('revenue')), brand_key(r.get('brand'))
        if not b or b in have or b in GENERIC or b.startswith('amazon') or not rev or rev < floor:
            continue
        sub = ' '.join(str(r.get('subcategory') or '').split()) or None
        out.append({'brand': str(r.get('brand')), 'us_revenue': round(rev), 'category': r.get('category') or None,
                    'subcategory': sub, 'price': _f(r.get('avg_price')), 'reviews': _f(r.get('reviews')),
                    'rating': _f(r.get('rating')), 'us_seller': r.get('dominant_seller') or None,
                    'amazon_us': seller_role(r.get('dominant_seller'), 'US') == 'amazon',
                    'growth': _f(r.get('growth_12m')), 'risk_why': risk_of(sub, r.get('category'))})
    out.sort(key=lambda x: -x['us_revenue'])
    return out[:limit]


def sellers_summary(rows, market, top=25):
    """Who takes the money in a market (SmartScout sellers): total, the top 10's share, how many sellers take half, the
    shares of Amazon, its Global Store and resellers, and the top sellers."""
    rs = [{**r, 'revenue': _f(r.get('revenue')) or 0} for r in rows or ()]
    rs.sort(key=lambda r: -r['revenue'])
    total = sum(r['revenue'] for r in rs)
    if not total:
        return {'total': 0, 'sellers': 0, 'top10': None, 'half': None, 'shares': {}, 'top': []}
    cum, half = 0, None
    for i, r in enumerate(rs, 1):
        cum += r['revenue']
        if cum >= total / 2:
            half = i
            break
    shares = dict.fromkeys(ROLES, 0.0)
    for r in rs:
        r['role'] = seller_role(r.get('name'), market) if r.get('name') else 'unknown'
        shares[r['role']] += r['revenue'] / total
    return {'total': round(total), 'sellers': len(rs), 'top10': round(sum(r['revenue'] for r in rs[:10]) / total, 4),
            'half': half, 'shares': {k: round(v, 4) for k, v in shares.items()},
            'top': [{'name': r.get('name') or None, 'seller_id': r.get('seller_id'), 'revenue': round(r['revenue']),
                     'share': round(r['revenue'] / total, 4), 'role': r['role'], 'subcategory': r.get('subcategory') or None,
                     'brands': r.get('n_brands'), 'asins': r.get('n_asins'), 'pct_fba': _f(r.get('pct_fba')),
                     'store': offmod.store_country(r.get('name'), market)} for r in rs[:top]]}


def global_store_ids(rows, market):
    """{seller_id: (name, country)} of Amazon's Global Store sellers in a SmartScout sellers export."""
    out = {}
    for r in rows or ():
        c = offmod.store_country(r.get('name'), market)
        if c and r.get('seller_id'):
            out[str(r['seller_id']).upper()] = (r['name'], c)
    return out


# ---------- live: is a niche still open? ----------
def _role_of_offer(o, market):
    if not o:
        return 'unknown'
    if o.get('amazon'):
        return 'store' if o.get('stock') == 'abroad' else 'amazon'
    if o.get('stock') == 'abroad' or (o.get('country') and o['country'] != market and o.get('stock') != 'local'):
        return 'abroad'
    return 'local' if o.get('stock') == 'local' else 'unknown'


def page_brands(rows, known=None, names=(), avoid=()):
    """Brands on page 1 (organic listings) by their share of the "bought" badges, else of the listings."""
    organic = [r for r in rows or () if not r.get('sponsored')]
    tot_b = sum(r.get('bought') or 0 for r in organic)
    acc = {}
    for r in organic:
        b, src = admod.brand_of(r.get('asin'), r.get('title'), known, names, avoid)
        if not b:
            continue
        a = acc.setdefault(b.lower(), {'brand': b, 'from': src, 'listings': 0, 'bought': 0})
        a['listings'] += 1
        a['bought'] += r.get('bought') or 0
    out = list(acc.values())
    for a in out:
        a['share'] = round(a['bought'] / tot_b, 4) if tot_b else round(a['listings'] / max(1, len(organic)), 4)
    out.sort(key=lambda a: (-a['share'], -a['listings']))
    return out[:8]


def live_verdict(search, offers, market):
    """search: page 1 of the niche's phrase {'status', 'phrase', 'rows': classified amazon_check rows}; offers: the buy-box
    offers (engine.offers shape) of its top organic listings, None where a page was not read. Returns {'verdict',
    'label', 'why', 'conf', 'gap', 'fast', 'total', 'overseas', 'over_share', 'buyboxes': {role: n}, 'read'}."""
    s = search or {}
    name = MARKET_NAMES.get(market, market)
    phrase = s.get('phrase') or ''

    def out(kind, why, conf, **kw):
        return {'verdict': kind, 'label': VLABEL[kind], 'why': why, 'conf': conf, 'gap': kind in ('open', 'amazon_only'),
                'means': VMEANS[kind], **kw}
    if s.get('status') not in ('ok', 'empty', 'partial'):
        return out('unread', 'Amazon %s could not be read (%s): a gap in the data, not in the market.' % (
            name, s.get('status') or 'not read'), 'low', fast=None, total=None, overseas=None, over_share=None,
            buyboxes={}, read=0)
    organic = [r for r in s.get('rows') or () if not r.get('sponsored')]
    fast = sum(1 for r in organic if r.get('speed') == 'fast')
    abroad = [r for r in organic if r.get('origin') == 'overseas']
    bought = sum(r.get('bought') or 0 for r in organic)
    over = (sum(r.get('bought') or 0 for r in abroad) / bought) if bought else (len(abroad) / len(organic) if organic else None)
    read = [o for o in offers or () if o]
    roles = dict.fromkeys(('amazon', 'store', 'abroad', 'local', 'unknown'), 0)
    for o in read:
        roles[_role_of_offer(o, market)] += 1
    common = {'fast': fast, 'total': len(organic), 'overseas': len(abroad),
              'over_share': round(over, 4) if over is not None else None, 'buyboxes': roles, 'read': len(read)}
    conf = 'high' if read and len(read) == len(offers or ()) and bought else 'medium' if read or bought else 'low'
    if not organic:
        return out('open', 'Page 1 of "%s" on Amazon %s shows no listings: nobody sells it, or shoppers type it otherwise.' % (
            phrase, name), 'low', **common)
    bb = ', '.join('%d %s' % (v, {'amazon': 'Amazon', 'store': 'Global Store', 'abroad': 'from abroad', 'local': 'local seller' +
                                  ('s' if v > 1 else ''), 'unknown': 'unknown'}[k]) for k, v in roles.items() if v)
    bb = ' Top buy boxes: %s.' % bb if bb else ''
    if fast >= THRESH['fast_crowded']:
        return out('closed', '%d of %d listings deliver fast on Amazon %s.%s' % (fast, len(organic), name, bb), conf, **common)
    if fast > THRESH['fast_open']:
        return out('closing', '%d of %d listings deliver fast on Amazon %s: local sellers are arriving.%s' % (
            fast, len(organic), name, bb), conf, **common)
    if read and roles['amazon'] and not roles['local'] and roles['amazon'] >= len(read) / 2:
        return out('amazon_only', 'Amazon itself holds %d of the %d top buy boxes read, no local seller beside it; %d fast '
                                  'listing%s on page 1.' % (roles['amazon'], len(read), fast, '' if fast == 1 else 's'),
                   conf, **common)
    imported = roles['store'] + roles['abroad']
    if (over is not None and over >= THRESH['overseas_min']) or (read and imported >= len(read) / 2) or \
            (read and not roles['local']):
        why = 'Only %d fast listing%s' % (fast, '' if fast == 1 else 's')
        if over is not None:
            why += ', and %s of page 1%s ships from abroad' % (_pct(over), "'s sales" if bought else '')
        return out('open', '%s on Amazon %s.%s' % (why, name, bb), conf, **common)
    if not read:
        return out('few', 'Only %d fast listing%s on Amazon %s, but shoppers buy mostly listings stocked here; the top buy '
                          'boxes were not read.' % (fast, '' if fast == 1 else 's', name), 'low', **common)
    return out('few', 'Only %d fast listing%s on Amazon %s, but local sellers hold %d of the %d top buy boxes read.%s' % (
        fast, '' if fast == 1 else 's', name, roles['local'], len(read), bb), conf, **common)


def niche_phrase(niche, suggestions=()):
    """What shoppers might type for a niche: Amazon's suggestion that holds every word of its name (shortest first),
    else the name itself in lower case without '&' parts joined ('Leg & Foot Supports' -> 'leg foot supports')."""
    base = config.norm_kw(re.sub(r'\s*&\s*', ' ', niche or ''))
    want = {_fold(w) for w in base.split() if w not in STOP}
    fits = [s for s in suggestions or () if want and want <= {_fold(w) for w in config.norm_kw(s).split()}]
    return min(fits, key=len) if fits else base or None


# ---------- live: a Best Sellers category read now ----------
def category_table(items, market, units_fn=None, known=None, names=(), offers=None):
    """A Best Sellers list [{'asin', 'title', 'rank' or 'pos', 'price', 'reviews'}] with units_fn(rank) -> est dict (the
    market's sales curve) or None, known {asin: brand}, offers {asin: buy-box offer} of the listings whose page was read.
    Returns the brand concentration (by estimated revenue, else by rank weight 1/rank^0.9), who holds the read buy boxes
    and a gap label in niche_gap's shape."""
    rows = []
    for i, it in enumerate(items or (), 1):
        rank = it.get('rank') or it.get('pos') or i
        u = units_fn(rank) if units_fn else None
        v = (u or {}).get('v')
        price = _f(it.get('price'))
        w = v * (price or 1) if v is not None else 1 / rank ** 0.9
        b, src = admod.brand_of(it.get('asin'), it.get('title'), known, names)
        rows.append({'asin': it.get('asin'), 'rank': rank, 'brand': b or '?', 'from': src, 'w': w, 'units': v, 'price': price,
                     'reviews': it.get('reviews')})
    basis = 'curve' if units_fn and rows and all(r['units'] is not None for r in rows) else 'rank'
    total = sum(r['w'] for r in rows) or 1
    acc = {}
    for r in rows:
        a = acc.setdefault(r['brand'].lower(), {'brand': r['brand'], 'from': r['from'], 'listings': 0, 'w': 0.0, 'units': 0})
        a['listings'] += 1
        a['w'] += r['w']
        a['units'] += r['units'] or 0
    brands = sorted(acc.values(), key=lambda a: -a['w'])
    for a in brands:
        a['share'] = round(a.pop('w') / total, 4)
        a['units'] = round(a['units']) if basis == 'curve' else None
    roles = dict.fromkeys(('amazon', 'store', 'abroad', 'local', 'unknown'), 0)
    for o in (offers or {}).values():
        roles[_role_of_offer(o, market)] += 1
    read = sum(roles.values())
    prices = sorted(r['price'] for r in rows if r['price'])
    revs = sorted(r['reviews'] for r in rows if r['reviews'] is not None)
    top1 = brands[0]['share'] if brands else 0
    top3 = sum(a['share'] for a in brands[:3])
    hhi = sum(a['share'] ** 2 for a in brands)
    res = {'n': len(rows), 'basis': basis, 'brands': brands[:12], 'n_brands': len(brands), 'top1': round(top1, 4),
           'top3': round(top3, 4), 'hhi': round(hhi, 4), 'buyboxes': roles, 'read': read,
           'price_med': prices[len(prices) // 2] if prices else None, 'reviews_med': revs[len(revs) // 2] if revs else None,
           'units_total': round(sum(r['units'] or 0 for r in rows)) if basis == 'curve' else None}
    name = MARKET_NAMES.get(market, market)
    if read and (roles['store'] + roles['abroad']) / read >= THRESH['cross_min']:
        kind, why = 'global_store', '%d of the %d top buy boxes read ship from abroad (Global Store or foreign sellers).' % (
            roles['store'] + roles['abroad'], read)
    elif read and roles['amazon'] / read >= THRESH['amazon_min'] and not roles['local']:
        kind, why = 'amazon_then_gap', 'Amazon itself holds %d of the %d top buy boxes read, no local seller.' % (roles['amazon'], read)
    elif top3 >= 0.6:
        kind, why = 'thin', 'The top 3 brands take %s of the Best Sellers on Amazon %s.' % (_pct(top3), name)
    else:
        kind, why = 'crowded', '%d brands share the Best Sellers; the biggest takes %s.' % (len(brands), _pct(top1))
    res['gap'] = _out(kind, why, 'medium' if read >= 3 else 'low', also=[])
    return res
